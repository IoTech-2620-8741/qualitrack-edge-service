import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from shared.infrastructure.cloud_session import CloudRejectedError, CloudUnavailableError
from sync.application.errors import CloudSynchronizationDisabled
from sync.application.services import (
    PURGE_PAUSE_SECONDS,
    SynchronizationSummary,
    TelemetrySynchronizationApplicationService,
)
from sync.domain.entities import MonitoredDevice
from sync.domain.enums import DeviceStatus
from sync.infrastructure.cloud_telemetry_gateway import CloudTelemetryGateway
from tracking.domain.entities import ActuationEvent, ReportingDevice, TelemetryReading
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
ENVIRONMENTAL = ReportingDevice("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)
CONTAINER = ReportingDevice("CNT-001", DeviceType.CONTAINER_MONITOR)


def placed(device_id, device_type, environment_id=2, cloud_device_id=15):
    return MonitoredDevice(device_id, device_type, 7, environment_id, cloud_device_id, DeviceStatus.OPERATIONAL, NOW)


def stored_reading(reading_id, device=ENVIRONMENTAL, metric=Metric.AIR_QUALITY, value=412.5, minutes=0):
    reading = TelemetryReading.record(device, metric, value, NOW - timedelta(minutes=minutes), 3, NOW)
    return reading.with_persistence_id(reading_id)


def stored_event(event_id):
    event = ActuationEvent.record(CONTAINER, ActuationAction.VENTILATION_ON, Metric.TEMPERATURE,
                                  EnvironmentalState.WARNING, ActuationResult.EXECUTED, NOW, 3, NOW)
    return event.with_persistence_id(event_id)


def received_reading(reading_id, status, days_ago):
    received_at = NOW - timedelta(days=days_ago)
    reading = TelemetryReading.record(ENVIRONMENTAL, Metric.AIR_QUALITY, 400.0, received_at, None, received_at)
    reading = reading.with_persistence_id(reading_id)
    return reading if status is SyncStatus.PENDING else reading.synchronized(status)


def received_event(event_id, status, days_ago):
    received_at = NOW - timedelta(days=days_ago)
    event = ActuationEvent.record(CONTAINER, ActuationAction.VENTILATION_ON, Metric.TEMPERATURE,
                                  EnvironmentalState.WARNING, ActuationResult.EXECUTED, received_at, None, received_at)
    event = event.with_persistence_id(event_id)
    return event if status is SyncStatus.PENDING else event.synchronized(status)


class SynchronizedTestCase(unittest.TestCase):
    """Tests the transition of a pending record when the cloud answers."""

    def test_only_a_pending_record_takes_the_answer_of_the_cloud(self):
        """Only a pending record can become SYNCED or REJECTED."""
        synced = stored_reading(1).synchronized(SyncStatus.SYNCED)

        self.assertEqual(synced.sync_status, SyncStatus.SYNCED)
        self.assertEqual(stored_event(1).synchronized(SyncStatus.REJECTED).sync_status, SyncStatus.REJECTED)
        with self.assertRaises(ValueError):
            synced.synchronized(SyncStatus.REJECTED)
        with self.assertRaises(ValueError):
            stored_reading(2).synchronized(SyncStatus.PENDING)


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("No JSON body")
        return self._body


class FakeCloudSession:
    def __init__(self, status_code=201, body=None):
        self.status_code = status_code
        self.body = body
        self.requests = []
        self.device_id = "EDGE-001"

    def request(self, method, path, json=None):
        self.requests.append((method, path, json))
        return FakeResponse(self.status_code, self.body)


class CloudTelemetryGatewayTestCase(unittest.TestCase):
    """Tests the translation of an Edge record into a cloud request.

    Validates the route used for environmental and container readings, the
    shape of the request body, and the classification of the cloud answers:
    201/200 → SYNCED, 400/404 → REJECTED, other statuses → CloudRejectedError.
    """

    def test_sends_environmental_readings_to_the_environment_route(self):
        """An environmental reading is sent to the environment route."""
        session = FakeCloudSession(201)

        answer = CloudTelemetryGateway(session).send_reading(
            placed("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE), stored_reading(1)
        )

        self.assertEqual(answer, SyncStatus.SYNCED)
        self.assertEqual(session.requests, [(
            "POST",
            "/api/v1/edge-gateways/EDGE-001/"
            "iot-devices/15/telemetry-measurements",
            {
                "metric": "AIR_QUALITY",
                "value": 412.5,
                "textValue": None,
                "measuredAt": "2026-11-03T15:00:00Z",
                "profileVersion": 3,
            },
        )])

    def test_sends_container_readings_and_actions_to_the_container_monitor_route(self):
        """Container readings and actions are sent to their own routes."""
        session = FakeCloudSession(200)
        gateway = CloudTelemetryGateway(session)
        container = placed("CNT-001", DeviceType.CONTAINER_MONITOR, 3, 16)

        reading_answer = gateway.send_reading(
            container, stored_reading(2, CONTAINER, Metric.RFID_TAG, "E200-3412-0001")
        )
        event_answer = gateway.send_actuation_event(container, stored_event(1))

        self.assertEqual((reading_answer, event_answer), (SyncStatus.SYNCED, SyncStatus.SYNCED))
        reading_request, event_request = session.requests
        self.assertEqual(reading_request[1],
                         "/api/v1/edge-gateways/EDGE-001/iot-devices/16/telemetry-measurements")
        self.assertEqual((reading_request[2]["value"], reading_request[2]["textValue"]), (None, "E200-3412-0001"))
        self.assertEqual(event_request[1], "/api/v1/edge-gateways/EDGE-001/iot-devices/16/actuation-events")
        self.assertEqual(event_request[2], {
            "action": "VENTILATION_ON", "triggerMetric": "TEMPERATURE", "triggerState": "WARNING",
            "result": "EXECUTED", "occurredAt": "2026-11-03T15:00:00Z", "profileVersion": 3,
        })

    def test_a_refused_record_is_rejected_and_other_answers_stop_the_synchronization(self):
        """400/404 reject the record; other statuses raise CloudRejectedError."""
        device = placed("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)
        for status in (400, 404):
            with self.subTest(status=status), self.assertLogs(
                "sync.infrastructure.cloud_telemetry_gateway", "WARNING"
            ) as logs:
                gateway = CloudTelemetryGateway(FakeCloudSession(status, {"message": "Invalid reading"}))
                self.assertEqual(gateway.send_reading(device, stored_reading(1)), SyncStatus.REJECTED)
                self.assertIn("Invalid reading", logs.output[0])
        with self.assertRaises(CloudRejectedError):
            CloudTelemetryGateway(FakeCloudSession(405)).send_reading(device, stored_reading(1))


class InMemoryRecords:
    def __init__(self, *records):
        self.records = {record.id: record for record in records}

    def find_pending(self, after_id, limit, device_ids):
        pending = [
            r for i, r in sorted(self.records.items())
            if i > after_id and r.sync_status is SyncStatus.PENDING and r.device_id in device_ids
        ]
        return pending[:limit]

    def count_pending_outside(self, device_ids):
        return sum(
            1 for r in self.records.values() if r.sync_status is SyncStatus.PENDING and r.device_id not in device_ids
        )

    def save_sync_status(self, record):
        self.records[record.id] = record

    def status_of(self, record_id):
        return self.records[record_id].sync_status

    def delete_before(self, status, cutoff, limit):
        doomed = [i for i, r in sorted(self.records.items()) if r.sync_status is status and r.received_at < cutoff]
        for record_id in doomed[:limit]:
            del self.records[record_id]
        return len(doomed[:limit])


class InMemoryDevices:
    def __init__(self, *devices):
        self.devices = list(devices)

    def find_all(self):
        return self.devices


class FakeTelemetryGateway:
    """Rejects the readings whose value is negative and fails when told to."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.sent = []

    def send_reading(self, device, reading):
        if reading.id == self.fail_on:
            raise CloudUnavailableError("offline")
        self.sent.append((device.device_id, reading.id))
        return SyncStatus.REJECTED if reading.value is not None and reading.value < 0 else SyncStatus.SYNCED

    def send_actuation_event(self, device, event):
        self.sent.append((device.device_id, f"event {event.id}"))
        return SyncStatus.SYNCED


class TelemetrySynchronizationApplicationServiceTestCase(unittest.TestCase):
    """Tests the application service that sends the pending records to the cloud.

    Validates the oldest-first ordering, the batching, the handling of nodes not
    placed in an environment, the stopping on cloud failures, and the retry of
    actions after an outage.
    """

    def setUp(self):
        self.devices = InMemoryDevices(
            placed("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE),
            placed("CNT-001", DeviceType.CONTAINER_MONITOR, environment_id=None, cloud_device_id=16),
        )
        self.readings = InMemoryRecords(
            stored_reading(1, minutes=3),
            stored_reading(2, value=-1.0, minutes=2),
            stored_reading(3, CONTAINER, Metric.TEMPERATURE, 21.5),
            stored_reading(4, ReportingDevice("ENV-404", DeviceType.ENVIRONMENTAL_DEVICE)),
            stored_reading(5, minutes=1),
        )
        self.events = InMemoryRecords(stored_event(1))

    def service(self, gateway):
        return TelemetrySynchronizationApplicationService(
            gateway, self.devices, self.readings, self.events, batch_size=2
        )

    def test_sends_the_records_of_placed_nodes_oldest_first_and_keeps_the_others_pending(self):
        """Only the records of placed nodes are sent, oldest first; the rest wait."""
        gateway = FakeTelemetryGateway()

        summaries = self.service(gateway).synchronize_pending()

        self.assertEqual(summaries["readings"], SynchronizationSummary(synced=2, rejected=1, waiting=2))
        self.assertEqual(summaries["actuation_events"], SynchronizationSummary(waiting=1))
        # A window is sent in parallel, so only the set of records sent is fixed.
        self.assertEqual(sorted(gateway.sent), [("ENV-001", 1), ("ENV-001", 2), ("ENV-001", 5)])
        self.assertEqual(
            [self.readings.status_of(i) for i in range(1, 6)],
            [SyncStatus.SYNCED, SyncStatus.REJECTED, SyncStatus.PENDING, SyncStatus.PENDING, SyncStatus.SYNCED],
        )

    def test_an_unreachable_cloud_stops_and_keeps_the_answers_already_received(self):
        """A cloud failure stops the sync but keeps the answers already received."""
        with self.assertRaises(CloudUnavailableError):
            self.service(FakeTelemetryGateway(fail_on=5)).synchronize_pending()

        self.assertEqual(self.readings.status_of(1), SyncStatus.SYNCED)
        self.assertEqual(self.readings.status_of(5), SyncStatus.PENDING)

    def test_a_failure_keeps_the_other_answers_of_its_window_and_stops_before_the_next_one(self):
        """A failure in a window keeps the other answers and stops before the next window."""
        self.readings = InMemoryRecords(*(stored_reading(i, minutes=10 - i) for i in range(1, 8)))
        gateway = FakeTelemetryGateway(fail_on=2)
        service = TelemetrySynchronizationApplicationService(
            gateway, self.devices, self.readings, self.events, batch_size=100
        )

        with self.assertRaises(CloudUnavailableError):
            service.synchronize_pending()

        self.assertEqual(sorted(record_id for _, record_id in gateway.sent), [1, 3, 4])
        self.assertEqual(
            [self.readings.status_of(i) for i in range(1, 8)],
            [SyncStatus.SYNCED, SyncStatus.PENDING, SyncStatus.SYNCED, SyncStatus.SYNCED,
             SyncStatus.PENDING, SyncStatus.PENDING, SyncStatus.PENDING],
        )

    def test_needs_a_gateway_to_synchronize(self):
        """Without a cloud gateway, synchronization raises CloudSynchronizationDisabled."""
        with self.assertRaises(CloudSynchronizationDisabled):
            TelemetrySynchronizationApplicationService(None, self.devices, self.readings, self.events).synchronize_pending()

    def test_retries_actuation_after_cloud_outage(self):
        """Keep an action pending until Cloud becomes available."""
        self.devices = InMemoryDevices(
            placed("CNT-001",DeviceType.CONTAINER_MONITOR,environment_id=2,cloud_device_id=16)
        )
        self.readings = InMemoryRecords()
        self.events = InMemoryRecords(stored_event(1))

        class OfflineGateway(FakeTelemetryGateway):
            def send_actuation_event(self, device, event):
                raise CloudUnavailableError("offline")
        with self.assertRaises(CloudUnavailableError):
            self.service(OfflineGateway()).synchronize_pending()

        self.assertEqual(self.events.status_of(1),SyncStatus.PENDING)
        result = self.service(FakeTelemetryGateway()).synchronize_pending()
        self.assertEqual(result["actuation_events"].synced,1)
        self.assertEqual(self.events.status_of(1),SyncStatus.SYNCED)



class PurgeTestCase(unittest.TestCase):
    """Tests the deletion of the records the cloud already answered.

    Validates the retention by status (7 days for SYNCED, 90 for REJECTED,
    never PENDING), the batching with pauses, the hourly cap, and the retry
    after a repository failure.
    """

    def setUp(self):
        self.now = NOW
        self.pauses = []
        self.readings = InMemoryRecords(
            received_reading(1, SyncStatus.SYNCED, days_ago=8),
            received_reading(2, SyncStatus.SYNCED, days_ago=6),
            received_reading(3, SyncStatus.REJECTED, days_ago=91),
            received_reading(4, SyncStatus.REJECTED, days_ago=89),
            received_reading(5, SyncStatus.PENDING, days_ago=365),
        )
        self.events = InMemoryRecords(
            received_event(1, SyncStatus.SYNCED, days_ago=8),
            received_event(2, SyncStatus.REJECTED, days_ago=91),
            received_event(3, SyncStatus.PENDING, days_ago=365),
        )

    def service(self):
        return TelemetrySynchronizationApplicationService(
            None, InMemoryDevices(), self.readings, self.events,
            clock=lambda: self.now, sleep=self.pauses.append,
        )

    def test_deletes_the_synchronized_after_7_days_and_the_rejected_after_90_and_never_the_pending(self):
        """Only the old SYNCED and REJECTED records are deleted; PENDING is kept."""
        deleted = self.service().purge()

        self.assertEqual(deleted, 4)
        self.assertEqual(sorted(self.readings.records), [2, 4, 5])
        self.assertEqual(sorted(self.events.records), [3])

    def test_deletes_in_batches_with_a_pause_between_them(self):
        """The purge deletes in batches and pauses between them."""
        self.readings = InMemoryRecords(*(received_reading(i, SyncStatus.SYNCED, days_ago=10) for i in range(1, 6)))

        with patch("sync.application.services.PURGE_BATCH_SIZE", 2):
            deleted = self.service().purge()

        self.assertEqual(deleted, 7)
        self.assertEqual(self.readings.records, {})
        self.assertEqual(self.pauses, [PURGE_PAUSE_SECONDS, PURGE_PAUSE_SECONDS])

    def test_runs_at_most_once_an_hour(self):
        """The purge runs at most once an hour."""
        service = self.service()
        self.assertEqual(service.purge(), 4)
        self.readings.records[6] = received_reading(6, SyncStatus.SYNCED, days_ago=10)

        self.now = NOW + timedelta(minutes=59)
        self.assertEqual(service.purge(), 0)
        self.assertIn(6, self.readings.records)

        self.now = NOW + timedelta(hours=1)
        self.assertEqual(service.purge(), 1)
        self.assertNotIn(6, self.readings.records)

    def test_needs_no_connection_with_the_cloud(self):
        """The purge works without a cloud connection."""
        self.assertEqual(self.service().purge(), 4)


    def test_retries_purge_after_repository_failure(self):
        """Retry cleanup when a previous purge did not finish."""
        service = self.service()

        with patch.object(self.readings, "delete_before", side_effect=RuntimeError("SQLite temporarily unavailable")):
            with self.assertRaises(RuntimeError):
                service.purge()

        deleted = service.purge()

        self.assertEqual(deleted, 4)
        self.assertEqual(sorted(self.readings.records), [2, 4, 5])
        self.assertEqual(sorted(self.events.records), [3])


if __name__ == "__main__":
    unittest.main()
