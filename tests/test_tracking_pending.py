import unittest
from datetime import UTC, datetime, timedelta

from app import create_app
from tracking.domain.entities import ReportingDevice, TelemetryReading
from tracking.domain.enums import DeviceType, Metric
from tracking.infrastructure.models import TelemetryReading as TelemetryReadingModel
from tracking.infrastructure.repositories import TelemetryReadingRepository

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


class PendingRecordsRepositoryTestCase(unittest.TestCase):
    """The pending records of the nodes the registry does not place are filtered in SQL."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        TelemetryReadingModel.delete().execute()
        self.repository = TelemetryReadingRepository()
        for minutes, device_id in enumerate(["ENV-001", "ENV-404", "ENV-001", "ENV-404", "ENV-002"]):
            device = ReportingDevice(device_id, DeviceType.ENVIRONMENTAL_DEVICE)
            reading = TelemetryReading.record(device, Metric.AIR_QUALITY, 400.0, NOW - timedelta(minutes=minutes), None, NOW)
            self.repository.save_idempotently([reading])

    def tearDown(self):
        TelemetryReadingModel.delete().execute()
        self.context.pop()

    def test_reads_only_the_pending_records_of_the_given_nodes(self):
        first = self.repository.find_pending(0, 2, ["ENV-001", "ENV-002"])
        rest = self.repository.find_pending(first[-1].id, 2, ["ENV-001", "ENV-002"])

        self.assertEqual([r.device_id for r in first + rest], ["ENV-001", "ENV-001", "ENV-002"])
        self.assertEqual(self.repository.find_pending(0, 10, []), [])

    def test_counts_the_pending_records_of_the_other_nodes(self):
        self.assertEqual(self.repository.count_pending_outside(["ENV-001", "ENV-002"]), 2)
        self.assertEqual(self.repository.count_pending_outside([]), 5)


if __name__ == "__main__":
    unittest.main()
