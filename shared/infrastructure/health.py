"""Health of the Edge for Docker and the technicians of the station (ADR-0017).

The Edge is healthy while its SQLite database answers: the nodes can sign in,
send their readings and read their configuration. The cloud synchronization
only degrades it, because the Edge keeps the records as ``PENDING`` and sends
them later (ADR-0007). The check reads the database schema and the last cycle
the scheduler keeps in memory, so calling it often costs almost nothing.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from shared.infrastructure.cloud_sync_scheduler import CloudSyncScheduler

# Cycles the synchronization may miss before the Edge counts it as behind.
BEHIND_AFTER_INTERVALS = 3


class HealthStatus(str, Enum):
    """Overall state of the Edge."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CloudSyncHealth:
    """State of the background synchronization with the cloud.

    Attributes:
        enabled: Whether ``CLOUD_SYNC_ENABLED`` is on.
        running: Whether the background thread is alive.
        last_cycle_at: UTC time the last cycle ended, or ``None`` before the first one.
        failed_tasks: Tasks that failed in the last cycle.
        behind: Whether no cycle ended in the last ``BEHIND_AFTER_INTERVALS`` intervals.
    """

    enabled: bool
    running: bool = False
    last_cycle_at: datetime | None = None
    failed_tasks: tuple[str, ...] = field(default_factory=tuple)
    behind: bool = False

    @property
    def degraded(self) -> bool:
        """Whether the synchronization is enabled but not working as expected."""
        return self.enabled and (not self.running or bool(self.failed_tasks) or self.behind)


@dataclass(frozen=True)
class HealthReport:
    """Health of the Edge at one moment."""

    status: HealthStatus
    database_available: bool
    cloud_sync: CloudSyncHealth


class EdgeHealthCheck:
    """Report the health of the database and of the cloud synchronization."""

    def __init__(
        self,
        database_check: Callable[[], bool],
        scheduler: CloudSyncScheduler | None,
        clock: Callable[[], datetime] | None = None,
    ):
        """Initialize the check; without a scheduler the synchronization is disabled."""
        self._database_check = database_check
        self._scheduler = scheduler
        self._clock = clock or (lambda: datetime.now(UTC))

    def report(self) -> HealthReport:
        """Check the database and read the state of the synchronization."""
        database_available = self._database_check()
        cloud_sync = self._cloud_sync()
        if not database_available:
            status = HealthStatus.UNAVAILABLE
        elif cloud_sync.degraded:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.OK
        return HealthReport(status, database_available, cloud_sync)

    def _cloud_sync(self) -> CloudSyncHealth:
        scheduler = self._scheduler
        if scheduler is None:
            return CloudSyncHealth(enabled=False)
        last_cycle = scheduler.last_cycle
        # A cycle is expected at least every interval; it may be slow while the
        # cloud answers slowly, so only several missed cycles count as behind.
        reference = last_cycle.finished_at if last_cycle is not None else scheduler.started_at
        allowance = timedelta(seconds=scheduler.interval_seconds * BEHIND_AFTER_INTERVALS)
        behind = reference is not None and self._clock() - reference > allowance
        return CloudSyncHealth(
            enabled=True,
            running=scheduler.is_running,
            last_cycle_at=None if last_cycle is None else last_cycle.finished_at,
            failed_tasks=() if last_cycle is None else last_cycle.failed_tasks,
            behind=behind,
        )
