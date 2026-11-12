"""Application services for the Tracking bounded context.

Application services coordinate Tracking use cases: they authenticate nodes
through the IAM anti-corruption layer, read the cloud through injected
gateways, invoke domain behavior and persist the outcome through repositories.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


from tracking.application.errors import (
    CloudSynchronizationDisabled,
    ConfigurationNotAvailable,
    DeviceAuthenticationFailed,
    DeviceTypeNotAllowed,
    DeviceAlreadyEnrolled,
    DeviceEnrollmentNotAllowed
)
from tracking.application.gateways import DeviceRegistryGateway, ProfileGateway
from tracking.domain.entities import (
    ActuationEvent,
    EnvironmentalProfile,
    MonitoredDevice,
    ReportingDevice,
    TelemetryReading,
)
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
)

from tracking.domain.enums import DeviceStatus

from tracking.domain.repositories import (
    ActuationEventRepository,
    ActuationEventSaveResult,
    EnvironmentalProfileRepository,
    MonitoredDeviceRepository,
    TelemetryReadingRepository,
    TelemetryReadingSaveResult,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
    MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer

class TelemetryApplicationService:
    """Accept the readings and actions of the nodes and keep them pending.

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

    def _reporting_device(self, device_id: str, api_key: str, device_type: DeviceType) -> ReportingDevice:
        reporting_device = self.iam_anti_corruption_layer.authenticated_reporting_device(device_id, api_key)
        if reporting_device is None:
            raise DeviceAuthenticationFailed
        if reporting_device.device_type is not device_type:
            raise DeviceTypeNotAllowed
        return reporting_device


@dataclass(frozen=True)
class RegistrySynchronization:
    """Outcome of one read of the cloud registry.

    Attributes:
        devices: Nodes of this Edge the cloud registers, now in the local copy.
        not_in_cloud: Nodes registered in this Edge that the cloud does not
            register as IoT devices with that ``sensorExternalId``; their
            records wait until the cloud registers them.
    """

    devices: list[MonitoredDevice]
    not_in_cloud: list[str]


class DeviceRegistryApplicationService:
    """Keep the local copy of the nodes of this Edge, as the cloud registers them.

    The cloud is the source of truth of the registry, but the laboratory may
    have nodes in environments other stations serve. The Edge keeps only the
    nodes registered in it with ``flask devices register``, which IAM knows,
    so it reads the profiles of its own nodes only. The local copy gives the
    Edge the cloud identifiers it synchronizes telemetry to, and it stays
    available while the cloud is unreachable.
    """

    def __init__(
        self,
        registry_gateway: DeviceRegistryGateway | None,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.registry_gateway = registry_gateway
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )

    def synchronize_registry(self) -> RegistrySynchronization:
        """Synchronize all IoT devices assigned to this Edge Gateway.

        The cloud registry is cached independently from local IAM.
        Devices can therefore be discovered before enrollment.

        Returns:
            The synchronized devices and locally registered devices
            no longer found in the cloud registry.
        """
        if self.registry_gateway is None:
            raise CloudSynchronizationDisabled

        devices = self.registry_gateway.fetch_devices()

        # Cache all cloud devices, including those not yet enrolled in IAM.
        self.monitored_device_repository.replace_all(devices)

        registered = self.iam_anti_corruption_layer.registered_device_ids()
        in_cloud = {device.device_id for device in devices}

        return RegistrySynchronization(devices, sorted(registered - in_cloud))

    def monitored_devices(self) -> list[MonitoredDevice]:
        """Return the local copy of the registry, ordered by identifier."""
        return self.monitored_device_repository.find_all()

    def find_monitored_device(self, device_id: str) -> MonitoredDevice | None:
        """Return the cloud registration of one node, if the cloud registers it."""
        return self.monitored_device_repository.find_by_device_id(device_id)

    def enroll_device(
        self,
        device_id: str,
        device_type: DeviceType,
    ) -> str:
        """Enroll an IoT device authorized by the cached cloud registry.

        The device must match the identifier and type registered
        in the cloud, be operational, and have an assigned environment.

        Existing local credentials are never regenerated.

        Args:
            device_id: Identifier presented by the ESP32.
            device_type: Device type presented by the ESP32.

        Returns:
            Newly generated local IAM API key.

        Raises:
            DeviceEnrollmentNotAllowed: If the device is not
                authorized by the cached cloud registry.
            DeviceAlreadyEnrolled: If local IAM already knows
                this device.
        """
        device = self.monitored_device_repository.find_by_device_id(
            device_id
        )

        if (
            device is None
            or device.device_type is not device_type
            or not device.is_in_environment
            or device.status is not DeviceStatus.OPERATIONAL
        ):
            raise DeviceEnrollmentNotAllowed

        registered = self.iam_anti_corruption_layer.registered_device_ids()

        if device_id in registered:
            raise DeviceAlreadyEnrolled

        try:
            return self.iam_anti_corruption_layer.enroll_device(
                device_id,
                device_type,
            )
        except ValueError as error:
            if str(error) == "Device is already registered":
                raise DeviceAlreadyEnrolled from error
            raise


@dataclass(frozen=True)
class NodeConfiguration:
    """The configuration a node reads from the Edge: its type and the profile it applies."""

    device_type: DeviceType
    profile: EnvironmentalProfile


class EnvironmentalProfileApplicationService:
    """Keep the profile of each node and hand it to the node (TS64, TS58, TS59).

    The profiles are copied from the cloud for every node the registry places
    in an environment; the copy changes only when every profile was read, so
    the nodes keep the last one while the cloud is unreachable.
    """

    def __init__(
        self,
        profile_gateway: ProfileGateway | None,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
        environmental_profile_repository: EnvironmentalProfileRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.profile_gateway = profile_gateway
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )
        self.environmental_profile_repository = (
            environmental_profile_repository or EnvironmentalProfileRepositoryImpl()
        )

    def synchronize_profiles(self) -> list[EnvironmentalProfile]:
        """Copy the profile of every node the registry places in an environment.

        A node without a profile in the cloud loses its local copy, as does a
        node the registry no longer has.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                could not answer; the previous copy is kept.
        """
        if self.profile_gateway is None:
            raise CloudSynchronizationDisabled
        profiles = []
        for device in self.monitored_device_repository.find_all():
            if not device.is_in_environment:
                continue
            profile = self.profile_gateway.fetch_profile(device)
            if profile is not None:
                profiles.append(profile)
        self.environmental_profile_repository.replace_all(profiles)
        return profiles

    def configuration_for(self, device_id: str, api_key: str) -> NodeConfiguration:
        """Return the configuration a node applies.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            ConfigurationNotAvailable: The Edge has no profile for the node yet.
        """
        reporting_device = self.iam_anti_corruption_layer.authenticated_reporting_device(device_id, api_key)
        if reporting_device is None:
            raise DeviceAuthenticationFailed
        profile = self.environmental_profile_repository.find_by_device_id(reporting_device.device_id)
        if profile is None:
            raise ConfigurationNotAvailable
        return NodeConfiguration(reporting_device.device_type, profile)

    def profiles(self) -> list[EnvironmentalProfile]:
        """Return the local copy of the profiles, ordered by node."""
        return self.environmental_profile_repository.find_all()
