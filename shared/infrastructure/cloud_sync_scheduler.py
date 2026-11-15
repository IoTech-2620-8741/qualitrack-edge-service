"""Background synchronization of the Edge with the cloud.

One daemon thread runs the synchronization tasks of the bounded contexts every
``CLOUD_SYNC_INTERVAL_SECONDS``. A failed task is logged and retried in the
next cycle, so a network outage never stops the Edge from receiving data.
Deployments run a single worker process, as SQLite expects. The scheduler
keeps the outcome of its last cycle, which ``GET /api/v1/health`` reports.
"""
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRequestError
from shared.infrastructure.database import db

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudSyncCycle:
    """Outcome of the last synchronization cycle.

    Attributes:
        finished_at: UTC time the cycle ended.
        failed_tasks: Names of the tasks that raised an error, in their order.
    """

    finished_at: datetime
    failed_tasks: tuple[str, ...]


class CloudSyncScheduler:
    """Run named synchronization tasks once or periodically in one thread."""

    def __init__(
        self,
        interval_seconds: int,
        tasks: list[tuple[str, Callable[[], object]]],
        clock: Callable[[], datetime] | None = None,
    ):
        if interval_seconds < 1:
            raise ValueError("The synchronization interval must be at least one second")
        self._interval_seconds = interval_seconds
        self._tasks = tasks
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        # Replaced as a whole at the end of each cycle, so the request threads
        # that read it never see half of a cycle.
        self._last_cycle: CloudSyncCycle | None = None

    @property
    def interval_seconds(self) -> int:
        """Seconds between the end of a cycle and the start of the next one."""
        return self._interval_seconds

    @property
    def is_running(self) -> bool:
        """Whether the background thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def started_at(self) -> datetime | None:
        """UTC time the background thread was last started, or ``None``."""
        return self._started_at

    @property
    def last_cycle(self) -> CloudSyncCycle | None:
        """Outcome of the last finished cycle, or ``None`` before the first one."""
        return self._last_cycle

    def run_once(self) -> dict[str, object]:
        """Run every task with its own database connection.

        The time of each task and of the whole cycle is logged, so a cycle
        that takes longer than the interval shows that the Edge falls behind.

        Returns:
            The result of each task by name, or the exception it raised.
        """
        results: dict[str, object] = {}
        cycle_started = time.monotonic()
        durations = []
        for name, task in self._tasks:
            task_started = time.monotonic()
            try:
                with db.connection_context():
                    results[name] = task()
            except CloudRequestError as error:
                LOGGER.warning("Cloud synchronization of %s failed: %s", name, error)
                results[name] = error
            except Exception as error:  # noqa: BLE001 - the thread must survive any task failure
                LOGGER.exception("Cloud synchronization of %s failed", name)
                results[name] = error
            durations.append(f"{name} {time.monotonic() - task_started:.1f} s")
        elapsed = time.monotonic() - cycle_started
        log = LOGGER.warning if elapsed > self._interval_seconds else LOGGER.info
        log("Cloud synchronization cycle took %.1f s (%s)", elapsed, ", ".join(durations))
        self._last_cycle = CloudSyncCycle(
            finished_at=self._clock(),
            failed_tasks=tuple(name for name, result in results.items() if isinstance(result, Exception)),
        )
        return results

    def start(self) -> None:
        """Start the background thread; calling it again does nothing."""
        if self.is_running:
            return
        self._stopped.clear()
        self._started_at = self._clock()
        self._thread = threading.Thread(target=self._run, name="cloud-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the background thread to stop and wait for it."""
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stopped.is_set():
            self.run_once()
            self._stopped.wait(self._interval_seconds)
