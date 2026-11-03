"""Background synchronization of the Edge with the cloud.

One daemon thread runs the synchronization tasks of the bounded contexts every
``CLOUD_SYNC_INTERVAL_SECONDS``. A failed task is logged and retried in the
next cycle, so a network outage never stops the Edge from receiving data.
Deployments run a single worker process, as SQLite expects.
"""
import logging
import threading
from collections.abc import Callable

from shared.infrastructure.cloud_session import CloudRequestError
from shared.infrastructure.database import db

LOGGER = logging.getLogger(__name__)


class CloudSyncScheduler:
    """Run named synchronization tasks once or periodically in one thread."""

    def __init__(self, interval_seconds: int, tasks: list[tuple[str, Callable[[], object]]]):
        if interval_seconds < 1:
            raise ValueError("The synchronization interval must be at least one second")
        self._interval_seconds = interval_seconds
        self._tasks = tasks
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, object]:
        """Run every task with its own database connection.

        Returns:
            The result of each task by name, or the exception it raised.
        """
        results: dict[str, object] = {}
        for name, task in self._tasks:
            try:
                with db.connection_context():
                    results[name] = task()
            except CloudRequestError as error:
                LOGGER.warning("Cloud synchronization of %s failed: %s", name, error)
                results[name] = error
            except Exception as error:  # noqa: BLE001 - the thread must survive any task failure
                LOGGER.exception("Cloud synchronization of %s failed", name)
                results[name] = error
        return results

    def start(self) -> None:
        """Start the background thread; calling it again does nothing."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
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
