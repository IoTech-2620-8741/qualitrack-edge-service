"""Repository abstractions for the Tracking bounded context."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tracking.domain.entities import ActuationEvent, EnvironmentalProfile, TelemetryReading
from tracking.domain.enums import SyncStatus


class EnvironmentalProfileRepository(Protocol):
    """Persistence contract for the local copy of the profiles of the nodes."""

    def find_by_device_id(self, device_id: str) -> EnvironmentalProfile | None:
        """Load the profile a node applies."""
        ...

    def find_all(self) -> list[EnvironmentalProfile]:
        """Load every profile, ordered by node."""
        ...

    def replace_all(self, profiles: list[EnvironmentalProfile]) -> None:
        """Make the local copy equal to the profiles the cloud has now."""
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

    def find_pending(self, after_id: int, limit: int, device_ids: Collection[str]) -> list[TelemetryReading]:
        """Load up to ``limit`` pending readings of ``device_ids`` stored after ``after_id``, oldest first."""
        ...

    def count_pending_outside(self, device_ids: Collection[str]) -> int:
        """Count the pending readings of the nodes not in ``device_ids``."""
        ...

    def save_sync_status(self, reading: TelemetryReading) -> None:
        """Persist the synchronization status of a stored reading."""
        ...

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored readings of each synchronization status."""
        ...

    def delete_before(self, status: SyncStatus, cutoff: datetime, limit: int) -> int:
        """Delete up to ``limit`` readings of ``status`` received before ``cutoff``.

        Returns:
            How many were deleted; fewer than ``limit`` when none are left.
        """
        ...


class ActuationEventRepository(Protocol):
    """Persistence contract for the actions the Edge received."""

    def save_idempotently(self, event: ActuationEvent) -> ActuationEventSaveResult:
        """Persist an action or return the one stored for the same node, action and time.

        Raises:
            TelemetryConflict: The stored action has different data.
        """
        ...

    def find_pending(self, after_id: int, limit: int, device_ids: Collection[str]) -> list[ActuationEvent]:
        """Load up to ``limit`` pending actions of ``device_ids`` stored after ``after_id``, oldest first."""
        ...

    def count_pending_outside(self, device_ids: Collection[str]) -> int:
        """Count the pending actions of the nodes not in ``device_ids``."""
        ...

    def save_sync_status(self, event: ActuationEvent) -> None:
        """Persist the synchronization status of a stored action."""
        ...

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored actions of each synchronization status."""
        ...

    def delete_before(self, status: SyncStatus, cutoff: datetime, limit: int) -> int:
        """Delete up to ``limit`` actions of ``status`` received before ``cutoff``.

        Returns:
            How many were deleted; fewer than ``limit`` when none are left.
        """
        ...
