"""Application services for the Tracking bounded context.

Application services coordinate Tracking use cases: they authenticate nodes
through the IAM anti-corruption layer, invoke domain behavior and persist the
outcome through repositories.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tracking.application.errors import (
    ConfigurationNotAvailable,
    DeviceAuthenticationFailed,
    DeviceTypeNotAllowed,
)
from tracking.domain.entities import (
    ActuationEvent,
    EnvironmentalProfile,
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
from tracking.domain.repositories import (
    ActuationEventRepository,
    ActuationEventSaveResult,
    EnvironmentalProfileRepository,
    TelemetryReadingRepository,
    TelemetryReadingSaveResult,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
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
class NodeConfiguration:
    """The configuration a node reads from the Edge: its type and the profile it applies."""

    device_type: DeviceType
    profile: EnvironmentalProfile


class EnvironmentalProfileApplicationService:
    """Hand each node the profile it applies (TS58, TS59).

    The profiles are the local copy the Sync context reads from the cloud, so
    the nodes keep the last one while the cloud is unreachable.
    """

    def __init__(
        self,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        environmental_profile_repository: EnvironmentalProfileRepository | None = None,
    ):
        """Initialize the service with its context integration and repository."""
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.environmental_profile_repository = (
            environmental_profile_repository or EnvironmentalProfileRepositoryImpl()
        )

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
