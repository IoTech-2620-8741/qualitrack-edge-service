"""Domain entities for the Tracking bounded context.

``ReportingDevice`` is Tracking's local representation of a node that signed
in. ``TelemetryReading`` and ``ActuationEvent`` are the observations the Edge
keeps until the cloud accepts them (TS60).
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite

from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceStatus,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.value_objects import ObservationTime, ProfileVersion

_MAX_TEXT_LENGTH = 120


def _positive_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_synchronization(current: SyncStatus, answer: SyncStatus) -> None:
    if current is not SyncStatus.PENDING:
        raise ValueError("Only a pending record is synchronized")
    if answer not in (SyncStatus.SYNCED, SyncStatus.REJECTED):
        raise ValueError("The cloud either accepts or rejects a record")


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ReportingDevice:
    """Tracking's local representation of a node allowed to send telemetry."""

    device_id: str
    device_type: DeviceType

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device identifier cannot be empty")
        if not isinstance(self.device_type, DeviceType):
            raise TypeError("Reporting device requires a valid device type")


@dataclass(frozen=True)
class TelemetryReading:
    """One metric a node measured at a moment, kept until the cloud accepts it.

    A node identifies an observation by its metric and its measurement time,
    as the cloud does, so sending it again is not a new reading.

    Attributes:
        device_id: Node that measured the value.
        metric: Variable measured; the node type must report it.
        value: Numeric value; ``None`` only for text metrics.
        text_value: Text read (an RFID tag); ``None`` for numeric metrics.
        measured_at: Measurement time supplied by the node, in UTC.
        profile_version: Version of the profile the node applied, if reported.
        received_at: When the Edge received the reading, in UTC.
        sync_status: Synchronization state with the cloud.
        id: Persistence identity, or ``None`` before the reading is saved.
    """

    device_id: str
    metric: Metric
    value: float | None
    text_value: str | None
    measured_at: ObservationTime
    profile_version: ProfileVersion | None
    received_at: datetime
    sync_status: SyncStatus = SyncStatus.PENDING
    id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device identifier cannot be empty")
        if not isinstance(self.metric, Metric):
            raise TypeError("Telemetry reading requires a valid metric")
        if not isinstance(self.measured_at, ObservationTime):
            raise TypeError("Telemetry reading requires an ObservationTime")
        if self.profile_version is not None and not isinstance(self.profile_version, ProfileVersion):
            raise TypeError("Telemetry reading requires a ProfileVersion or None")
        if not isinstance(self.sync_status, SyncStatus):
            raise TypeError("Telemetry reading requires a valid synchronization status")
        object.__setattr__(self, "received_at", _utc(self.received_at, "Reception time"))
        if self.metric.is_text:
            if (
                not isinstance(self.text_value, str)
                or not self.text_value.strip()
                or len(self.text_value) > _MAX_TEXT_LENGTH
            ):
                raise ValueError(f"{self.metric.value} needs a text value of up to {_MAX_TEXT_LENGTH} characters")
            if self.value is not None:
                raise ValueError(f"{self.metric.value} has no numeric value")
            return
        if self.text_value is not None:
            raise ValueError(f"{self.metric.value} has no text value")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not isfinite(self.value):
            raise ValueError(f"{self.metric.value} needs a finite numeric value")
        object.__setattr__(self, "value", float(self.value))
        if self.metric.is_detection and self.value not in (0.0, 1.0):
            raise ValueError(f"{self.metric.value} is 1 when detected and 0 otherwise")

    @classmethod
    def record(
        cls,
        reporting_device: ReportingDevice,
        metric: Metric,
        reading: float | str,
        measured_at: datetime,
        profile_version: int | None,
        received_at: datetime,
    ) -> "TelemetryReading":
        """Create a pending reading of a metric the node type reports."""
        if not isinstance(reporting_device, ReportingDevice):
            raise TypeError("Telemetry reading requires a ReportingDevice")
        if not isinstance(metric, Metric):
            raise TypeError("Telemetry reading requires a valid metric")
        if metric.device_type is not reporting_device.device_type:
            raise ValueError(f"{metric.value} is not measured by {reporting_device.device_type.value} nodes")
        return cls(
            reporting_device.device_id,
            metric,
            None if metric.is_text else reading,
            reading if metric.is_text else None,
            ObservationTime.received(measured_at, received_at),
            ProfileVersion.from_optional(profile_version),
            received_at,
        )

    def is_same_observation(self, other: "TelemetryReading") -> bool:
        """Whether ``other`` repeats this reading without changing its data."""
        return (
            (self.device_id, self.metric, self.measured_at)
            == (other.device_id, other.metric, other.measured_at)
            and (self.value, self.text_value, self.profile_version)
            == (other.value, other.text_value, other.profile_version)
        )

    def with_persistence_id(self, reading_id: int) -> "TelemetryReading":
        """Return this immutable reading with its persistence identity."""
        return replace(self, id=reading_id)

    def synchronized(self, status: SyncStatus) -> "TelemetryReading":
        """Return this reading with the answer of the cloud (TS61, TS62).

        Raises:
            ValueError: The reading is no longer pending, or ``status`` is not
                an answer (``SYNCED`` or ``REJECTED``).
        """
        _check_synchronization(self.sync_status, status)
        return replace(self, sync_status=status)


@dataclass(frozen=True)
class ActuationEvent:
    """An action a container monitor executed, kept until the cloud accepts it.

    The cause of the action is optional, but when present it names both the
    metric with thresholds and the state that triggered it. An action opened by
    an RFID tag has no metric cause.
    """

    device_id: str
    action: ActuationAction
    trigger_metric: Metric | None
    trigger_state: EnvironmentalState | None
    result: ActuationResult
    occurred_at: ObservationTime
    profile_version: ProfileVersion | None
    received_at: datetime
    sync_status: SyncStatus = SyncStatus.PENDING
    id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device identifier cannot be empty")
        if not isinstance(self.action, ActuationAction):
            raise TypeError("Actuation event requires a valid action")
        if not isinstance(self.result, ActuationResult):
            raise TypeError("Actuation event requires a valid result")
        if not isinstance(self.occurred_at, ObservationTime):
            raise TypeError("Actuation event requires an ObservationTime")
        if self.profile_version is not None and not isinstance(self.profile_version, ProfileVersion):
            raise TypeError("Actuation event requires a ProfileVersion or None")
        if not isinstance(self.sync_status, SyncStatus):
            raise TypeError("Actuation event requires a valid synchronization status")
        object.__setattr__(self, "received_at", _utc(self.received_at, "Reception time"))
        if (self.trigger_metric is None) != (self.trigger_state is None):
            raise ValueError("The cause of an action needs both the metric and the state")
        if self.trigger_metric is not None:
            if not isinstance(self.trigger_metric, Metric) or not isinstance(self.trigger_state, EnvironmentalState):
                raise TypeError("The cause of an action needs a valid metric and state")
            if (
                not self.trigger_metric.has_thresholds
                or self.trigger_metric.device_type is not DeviceType.CONTAINER_MONITOR
            ):
                raise ValueError(f"Actions cannot be caused by {self.trigger_metric.value}")

    @classmethod
    def record(
        cls,
        reporting_device: ReportingDevice,
        action: ActuationAction,
        trigger_metric: Metric | None,
        trigger_state: EnvironmentalState | None,
        result: ActuationResult,
        occurred_at: datetime,
        profile_version: int | None,
        received_at: datetime,
    ) -> "ActuationEvent":
        """Create a pending action of a container monitor."""
        if not isinstance(reporting_device, ReportingDevice):
            raise TypeError("Actuation event requires a ReportingDevice")
        if reporting_device.device_type is not DeviceType.CONTAINER_MONITOR:
            raise ValueError("Only a container monitor executes actions")
        return cls(
            reporting_device.device_id,
            action,
            trigger_metric,
            trigger_state,
            result,
            ObservationTime.received(occurred_at, received_at),
            ProfileVersion.from_optional(profile_version),
            received_at,
        )

    def is_same_observation(self, other: "ActuationEvent") -> bool:
        """Whether ``other`` repeats this action without changing its data."""
        return (
            (self.device_id, self.action, self.occurred_at)
            == (other.device_id, other.action, other.occurred_at)
            and (self.trigger_metric, self.trigger_state, self.result, self.profile_version)
            == (other.trigger_metric, other.trigger_state, other.result, other.profile_version)
        )

    def with_persistence_id(self, event_id: int) -> "ActuationEvent":
        """Return this immutable action with its persistence identity."""
        return replace(self, id=event_id)

    def synchronized(self, status: SyncStatus) -> "ActuationEvent":
        """Return this action with the answer of the cloud (TS63).

        Raises:
            ValueError: The action is no longer pending, or ``status`` is not
                an answer (``SYNCED`` or ``REJECTED``).
        """
        _check_synchronization(self.sync_status, status)
        return replace(self, sync_status=status)


@dataclass(frozen=True)
class MonitoredDevice:
    """A node the cloud registers for the laboratory of the Edge.

    It keeps the cloud identifiers its telemetry is synchronized to: the
    laboratory, the environment where the node is installed and the equipment
    identifier of the node in the cloud.

    Attributes:
        device_id: Identifier with which the cloud registers the node for the
            Edge (``sensorExternalId``, for example ``ENV-001``).
        device_type: Environmental device or container monitor.
        laboratory_id: Laboratory that owns the node in the cloud.
        environment_id: Environment of the node, or ``None`` while the cloud
            has not associated it with one.
        cloud_device_id: Equipment identifier of the node in the cloud.
        status: Operational status of the node in the cloud.
        synced_at: When the Edge last read the node from the cloud, in UTC.
    """

    device_id: str
    device_type: DeviceType
    laboratory_id: int
    environment_id: int | None
    cloud_device_id: int
    status: DeviceStatus
    synced_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device identifier cannot be empty")
        if not isinstance(self.device_type, DeviceType):
            raise TypeError("Monitored device requires a valid device type")
        if not isinstance(self.status, DeviceStatus):
            raise TypeError("Monitored device requires a valid status")
        if not _positive_id(self.laboratory_id) or not _positive_id(self.cloud_device_id):
            raise ValueError("Cloud identifiers must be positive integers")
        if self.environment_id is not None and not _positive_id(self.environment_id):
            raise ValueError("Environment identifier must be a positive integer")
        if not isinstance(self.synced_at, datetime):
            raise TypeError("Synchronization time must be a datetime")
        if self.synced_at.tzinfo is None or self.synced_at.utcoffset() is None:
            raise ValueError("Synchronization time must include a timezone")
        object.__setattr__(self, "synced_at", self.synced_at.astimezone(UTC))

    @property
    def is_in_environment(self) -> bool:
        """Whether the cloud has associated the node with an environment."""
        return self.environment_id is not None
