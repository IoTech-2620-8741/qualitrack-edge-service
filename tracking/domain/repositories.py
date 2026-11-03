"""Repository abstractions for the Tracking bounded context."""

from dataclasses import dataclass
from typing import Protocol

from tracking.domain.entities import ActuationEvent, MonitoredDevice, TelemetryReading
from tracking.domain.enums import SyncStatus


class MonitoredDeviceRepository(Protocol):
    """Persistence contract for the local copy of the cloud device registry."""

    def find_by_device_id(self, device_id: str) -> MonitoredDevice | None:
        """Load a monitored device by the identifier of its node."""
        ...

    def find_all(self) -> list[MonitoredDevice]:
        """Load every monitored device, ordered by identifier."""
        ...

    def replace_all(self, devices: list[MonitoredDevice]) -> None:
        """Make the local copy equal to the devices the cloud registers."""
        ...


@dataclass(frozen=True)
class TelemetryReadingSaveResult:
    """Persisted readings of one submission and whether it stored a new one."""

    readings: list[TelemetryReading]
    created: bool


@dataclass(frozen=True)
class ActuationEventSaveResult:
    """Persisted action and whether this call stored it."""

    event: ActuationEvent
    created: bool


class TelemetryReadingRepository(Protocol):
    """Persistence contract for the readings the Edge received."""

    def save_idempotently(self, readings: list[TelemetryReading]) -> TelemetryReadingSaveResult:
        """Persist the readings of one submission in one transaction.

        A reading already stored for the same node, metric and time is
        returned instead of stored again.

        Raises:
            TelemetryConflict: A stored reading has the same identity and
                different data; nothing of the submission is stored.
        """
        ...

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored readings of each synchronization status."""
        ...


class ActuationEventRepository(Protocol):
    """Persistence contract for the actions the Edge received."""

    def save_idempotently(self, event: ActuationEvent) -> ActuationEventSaveResult:
        """Persist an action or return the one stored for the same node, action and time.

        Raises:
            TelemetryConflict: The stored action has different data.
        """
        ...

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored actions of each synchronization status."""
        ...
