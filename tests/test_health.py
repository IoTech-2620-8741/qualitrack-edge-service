import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from peewee import OperationalError

from app import create_app
from shared.infrastructure.cloud_sync_scheduler import CloudSyncCycle, CloudSyncScheduler
from shared.infrastructure.database import database_available, db
from shared.infrastructure.health import (
    CloudSyncHealth,
    EdgeHealthCheck,
    HealthReport,
    HealthStatus,
)

NOW = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)


class FakeScheduler:
    def __init__(self, running=True, started_at=NOW - timedelta(minutes=10), last_cycle=None, interval_seconds=60):
        self.is_running = running
        self.started_at = started_at
        self.last_cycle = last_cycle
        self.interval_seconds = interval_seconds


class FakeHealthCheck:
    def __init__(self, report):
        self._report = report

    def report(self):
        return self._report


def health_check(database=True, scheduler=None):
    return EdgeHealthCheck(lambda: database, scheduler, clock=lambda: NOW)


class EdgeHealthCheckTestCase(unittest.TestCase):
    """Tests the health check that reports whether the Edge can serve the nodes.

    The status is `ok` while the database answers and the synchronization is
    disabled or recent, `degraded` when a task failed, the thread stopped, or no
    cycle ended in three intervals, and `unavailable` when the database cannot
    be read.
    """

    def test_is_ok_while_the_database_answers_and_the_synchronization_is_disabled(self):
        """The Edge is ok while the database answers and sync is disabled."""
        report = health_check().report()

        self.assertEqual(report.status, HealthStatus.OK)
        self.assertTrue(report.database_available)
        self.assertEqual(report.cloud_sync, CloudSyncHealth(enabled=False))

    def test_is_unavailable_when_the_database_cannot_be_read(self):
        """The Edge is unavailable when the database cannot be read."""
        scheduler = FakeScheduler(last_cycle=CloudSyncCycle(NOW, ()))

        report = health_check(database=False, scheduler=scheduler).report()

        self.assertEqual(report.status, HealthStatus.UNAVAILABLE)
        self.assertFalse(report.database_available)

    def test_is_ok_while_the_last_cycle_ended_recently_without_failures(self):
        """A recent cycle without failures keeps the Edge ok."""
        scheduler = FakeScheduler(last_cycle=CloudSyncCycle(NOW - timedelta(seconds=30), ()))

        report = health_check(scheduler=scheduler).report()

        self.assertEqual(report.status, HealthStatus.OK)
        self.assertEqual(
            report.cloud_sync,
            CloudSyncHealth(enabled=True, running=True, last_cycle_at=NOW - timedelta(seconds=30)),
        )

    def test_is_degraded_but_serving_when_a_synchronization_task_failed(self):
        """A failed task degrades the Edge, but the nodes are still served."""
        cycle = CloudSyncCycle(NOW - timedelta(seconds=30), ("pending telemetry",))

        report = health_check(scheduler=FakeScheduler(last_cycle=cycle)).report()

        self.assertEqual(report.status, HealthStatus.DEGRADED)
        self.assertTrue(report.database_available)
        self.assertEqual(report.cloud_sync.failed_tasks, ("pending telemetry",))

    def test_is_degraded_when_the_synchronization_thread_is_not_running(self):
        """A stopped synchronization thread degrades the Edge."""
        scheduler = FakeScheduler(running=False, last_cycle=CloudSyncCycle(NOW, ()))

        report = health_check(scheduler=scheduler).report()

        self.assertEqual(report.status, HealthStatus.DEGRADED)
        self.assertFalse(report.cloud_sync.running)

    def test_counts_the_synchronization_as_behind_after_three_missed_intervals(self):
        """The synchronization is behind after three missed intervals."""
        for age, behind in ((timedelta(seconds=180), False), (timedelta(seconds=181), True)):
            with self.subTest(age=age):
                scheduler = FakeScheduler(last_cycle=CloudSyncCycle(NOW - age, ()))

                report = health_check(scheduler=scheduler).report()

                self.assertEqual(report.cloud_sync.behind, behind)
                self.assertEqual(report.status, HealthStatus.DEGRADED if behind else HealthStatus.OK)

    def test_waits_for_the_first_cycle_from_the_start_of_the_thread(self):
        """Before the first cycle, the thread start is used to measure the wait."""
        for started_ago, behind in ((timedelta(seconds=20), False), (timedelta(minutes=5), True)):
            with self.subTest(started_ago=started_ago):
                scheduler = FakeScheduler(started_at=NOW - started_ago, last_cycle=None)

                report = health_check(scheduler=scheduler).report()

                self.assertIsNone(report.cloud_sync.last_cycle_at)
                self.assertEqual(report.cloud_sync.behind, behind)

    def test_reads_the_last_cycle_of_the_scheduler(self):
        """The health check reads the last cycle from the real scheduler."""
        scheduler = CloudSyncScheduler(60, [("device registry", lambda: None)], clock=lambda: NOW)
        with self.assertLogs("shared.infrastructure.cloud_sync_scheduler", "INFO"):
            scheduler.run_once()

        report = health_check(scheduler=scheduler).report()

        self.assertEqual(report.cloud_sync, CloudSyncHealth(enabled=True, running=False, last_cycle_at=NOW))
        self.assertEqual(report.status, HealthStatus.DEGRADED)


class DatabaseAvailableTestCase(unittest.TestCase):
    """Tests the database availability check used by the health endpoint."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_reads_the_schema_of_the_database(self):
        """The schema of a healthy database can be read."""
        self.assertTrue(database_available())

    def test_answers_false_when_the_database_fails(self):
        """A database error makes the check answer false."""
        with patch.object(db, "execute_sql", side_effect=OperationalError("disk I/O error")):
            self.assertFalse(database_available())


class HealthApiTestCase(unittest.TestCase):
    """Tests the GET /api/v1/health endpoint of the Edge.

    The endpoint needs no API key, answers 200 with `ok` or `degraded`, and 503
    with `unavailable` when the database cannot be read. It never shows
    identifiers, keys or error messages.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.client = self.app.test_client()
        health_check_in_use = self.app.extensions["edge_health_check"]
        self.addCleanup(self.app.extensions.__setitem__, "edge_health_check", health_check_in_use)

    def test_reports_a_healthy_edge_without_an_api_key(self):
        """A healthy Edge answers 200 ok without an API key and no caching."""
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "database": "ok", "cloud_sync": {"enabled": False}})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_answers_503_when_the_database_cannot_be_read(self):
        """An unreadable database makes the endpoint answer 503 unavailable."""
        with patch.object(db, "execute_sql", side_effect=OperationalError("disk I/O error")):
            response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json,
            {"status": "unavailable", "database": "unavailable", "cloud_sync": {"enabled": False}},
        )

    def test_reports_the_state_of_the_synchronization_without_error_details(self):
        """The response shows the sync state and failed tasks, but no error details."""
        cloud_sync = CloudSyncHealth(
            enabled=True,
            running=True,
            last_cycle_at=datetime(2026, 11, 14, 3, 59, 30, 123456, tzinfo=UTC),
            failed_tasks=("pending telemetry",),
        )
        self.app.extensions["edge_health_check"] = FakeHealthCheck(
            HealthReport(HealthStatus.DEGRADED, True, cloud_sync)
        )

        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {
                "status": "degraded",
                "database": "ok",
                "cloud_sync": {
                    "enabled": True,
                    "running": True,
                    "last_cycle_at": "2026-11-14T03:59:30Z",
                    "failed_tasks": ["pending telemetry"],
                    "behind": False,
                },
            },
        )

    def test_only_answers_get(self):
        """The health endpoint rejects POST with 405."""
        self.assertEqual(self.client.post("/api/v1/health").status_code, 405)
