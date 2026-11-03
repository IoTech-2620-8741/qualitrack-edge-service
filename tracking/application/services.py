"""Application services for the Tracking bounded context.

Application services coordinate Tracking use cases: they authenticate nodes
through the IAM anti-corruption layer, read the cloud through injected
gateways, invoke domain behavior and persist the outcome through repositories.
"""
from collections.abc import Callable
from datetime import UTC, datetime

from tracking.application.errors import (
    CloudSynchronizationDisabled,
    DeviceAuthenticationFailed,
    DeviceTypeNotAllowed,
)
from tracking.application.gateways import DeviceRegistryGateway
from tracking.domain.entities import ActuationEvent, MonitoredDevice, ReportingDevice, TelemetryReading
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.repositories import (
    ActuationEventRepository,
    ActuationEventSaveResult,
    MonitoredDeviceRepository,
    TelemetryReadingRepository,
    TelemetryReadingSaveResult,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer


class TelemetryApplicationService:
    """Accept the readings and actions of the nodes and keep them pending (TS55–TS57, TS60).

    The service asks the Tracking-to-IAM anti-corruption layer to authenticate
    the node, checks that its type sends that telemetry, creates the records
    through domain behavior and persists them idempotently.
    """

    def __init__(
        self,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        telemetry_reading_repository: TelemetryReadingRepository | None = None,
        actuation_event_repository: ActuationEventRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        """Initialize the service with its context integration, repositories and clock."""
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.telemetry_reading_repository = (
            telemetry_reading_repository or TelemetryReadingRepositoryImpl()
        )
        self.actuation_event_repository = actuation_event_repository or ActuationEventRepositoryImpl()
        self.clock = clock or (lambda: datetime.now(UTC))

    def record_readings(
        self,
        device_id: str,
        api_key: str,
        device_type: DeviceType,
        measured_at: datetime,
        profile_version: int | None,
        values: dict[Metric, float | str],
    ) -> TelemetryReadingSaveResult:
        """Accept the metrics a node measured at one moment.

        Args:
            device_id: Identity of the node sending the readings.
            api_key: Credential presented for the node.
            device_type: Type of node the endpoint receives readings from.
            measured_at: Measurement time supplied by the node.
            profile_version: Version of the profile the node applied, if any.
            values: Value of each metric measured; at least one.

        Returns:
            The stored readings and whether any of them is new.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            DeviceTypeNotAllowed: The node is not of ``device_type``.
            ValueError: The domain rejects a value, the time or an empty reading.
            TelemetryConflict: A reading was already sent with different data.
        """
        reporting_device = self._reporting_device(device_id, api_key, device_type)
        if not values:
            raise ValueError("A reading needs at least one metric")
        received_at = self.clock()
        readings = [
            TelemetryReading.record(
                reporting_device, metric, value, measured_at, profile_version, received_at
            )
            for metric, value in values.items()
        ]
        return self.telemetry_reading_repository.save_idempotently(readings)

    def record_actuation_event(
        self,
        device_id: str,
        api_key: str,
        action: ActuationAction,
        trigger_metric: Metric | None,
        trigger_state: EnvironmentalState | None,
        result: ActuationResult,
        occurred_at: datetime,
        profile_version: int | None,
    ) -> ActuationEventSaveResult:
        """Accept an action a container monitor executed.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            DeviceTypeNotAllowed: The node is not a container monitor.
            ValueError: The domain rejects the cause or the time.
            TelemetryConflict: The action was already sent with different data.
        """
        reporting_device = self._reporting_device(device_id, api_key, DeviceType.CONTAINER_MONITOR)
        event = ActuationEvent.record(
            reporting_device,
            action,
            trigger_metric,
            trigger_state,
            result,
            occurred_at,
            profile_version,
            self.clock(),
        )
        return self.actuation_event_repository.save_idempotently(event)

    def pending_counts(self) -> dict[str, dict[SyncStatus, int]]:
        """Count the stored readings and actions of each synchronization status."""
        return {
            "readings": self.telemetry_reading_repository.count_by_sync_status(),
            "actuation_events": self.actuation_event_repository.count_by_sync_status(),
        }

    def _reporting_device(self, device_id: str, api_key: str, device_type: DeviceType) -> ReportingDevice:
        reporting_device = self.iam_anti_corruption_layer.authenticated_reporting_device(device_id, api_key)
        if reporting_device is None:
            raise DeviceAuthenticationFailed
        if reporting_device.device_type is not device_type:
            raise DeviceTypeNotAllowed
        return reporting_device


class DeviceRegistryApplicationService:
    """Keep the local copy of the nodes the cloud registers for the laboratory.

    The cloud is the source of truth of the registry. The local copy gives the
    Edge the cloud identifiers it synchronizes telemetry to, and it stays
    available while the cloud is unreachable.
    """

    def __init__(
        self,
        registry_gateway: DeviceRegistryGateway | None,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.registry_gateway = registry_gateway
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )

    def synchronize_registry(self) -> list[MonitoredDevice]:
        """Replace the local copy with the nodes the cloud registers now.

        The local copy changes only when the cloud answers, so a failed
        synchronization keeps the previous one.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                did not answer or rejected the request.
        """
        if self.registry_gateway is None:
            raise CloudSynchronizationDisabled
        devices = self.registry_gateway.fetch_devices()
        self.monitored_device_repository.replace_all(devices)
        return devices

    def monitored_devices(self) -> list[MonitoredDevice]:
        """Return the local copy of the registry, ordered by identifier."""
        return self.monitored_device_repository.find_all()

    def find_monitored_device(self, device_id: str) -> MonitoredDevice | None:
        """Return the cloud registration of one node, if the cloud registers it."""
        return self.monitored_device_repository.find_by_device_id(device_id)
