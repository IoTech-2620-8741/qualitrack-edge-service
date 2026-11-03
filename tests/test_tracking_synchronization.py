import unittest
from datetime import UTC, datetime, timedelta

from shared.infrastructure.cloud_session import CloudRejectedError, CloudUnavailableError
from tracking.application.errors import CloudSynchronizationDisabled
from tracking.application.services import SynchronizationSummary, TelemetrySynchronizationApplicationService
from tracking.domain.entities import ActuationEvent, MonitoredDevice, ReportingDevice, TelemetryReading
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceStatus,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.infrastructure.cloud_telemetry_gateway import CloudTelemetryGateway

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


class SynchronizedTestCase(unittest.TestCase):
    def test_only_a_pending_record_takes_the_answer_of_the_cloud(self):
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

    def request(self, method, path, json=None):
        self.requests.append((method, path, json))
        return FakeResponse(self.status_code, self.body)


class CloudTelemetryGatewayTestCase(unittest.TestCase):
    def test_sends_environmental_readings_to_the_environment_route(self):
        session = FakeCloudSession(201)

        answer = CloudTelemetryGateway(session).send_reading(
            placed("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE), stored_reading(1)
        )

        self.assertEqual(answer, SyncStatus.SYNCED)
        self.assertEqual(session.requests, [(
            "POST",
            "/api/v1/laboratories/7/environments/2/telemetry-measurements",
            {"metric": "AIR_QUALITY", "value": 412.5, "textValue": None,
             "measuredAt": "2026-11-03T15:00:00Z", "profileVersion": 3},
        )])

    def test_sends_container_readings_and_actions_to_the_container_monitor_route(self):
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
                         "/api/v1/laboratories/7/environments/3/container-monitors/16/telemetry-measurements")
        self.assertEqual((reading_request[2]["value"], reading_request[2]["textValue"]), (None, "E200-3412-0001"))
        self.assertEqual(event_request[1], "/api/v1/laboratories/7/environments/3/container-monitors/16/actuation-events")
        self.assertEqual(event_request[2], {
            "action": "VENTILATION_ON", "triggerMetric": "TEMPERATURE", "triggerState": "WARNING",
            "result": "EXECUTED", "occurredAt": "2026-11-03T15:00:00Z", "profileVersion": 3,
        })

    def test_a_refused_record_is_rejected_and_other_answers_stop_the_synchronization(self):
        device = placed("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)
        for status in (400, 404):
            with self.subTest(status=status), self.assertLogs(
                "tracking.infrastructure.cloud_telemetry_gateway", "WARNING"
            ) as logs:
                gateway = CloudTelemetryGateway(FakeCloudSession(status, {"message": "Invalid reading"}))
                self.assertEqual(gateway.send_reading(device, stored_reading(1)), SyncStatus.REJECTED)
                self.assertIn("Invalid reading", logs.output[0])
        with self.assertRaises(CloudRejectedError):
            CloudTelemetryGateway(FakeCloudSession(405)).send_reading(device, stored_reading(1))


class InMemoryRecords:
    def __init__(self, *records):
        self.records = {record.id: record for record in records}

    def find_pending(self, after_id, limit):
        pending = [r for i, r in sorted(self.records.items()) if i > after_id and r.sync_status is SyncStatus.PENDING]
        return pending[:limit]

    def save_sync_status(self, record):
        self.records[record.id] = record

    def status_of(self, record_id):
        return self.records[record_id].sync_status


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
        gateway = FakeTelemetryGateway()

        summaries = self.service(gateway).synchronize_pending()

        self.assertEqual(summaries["readings"], SynchronizationSummary(synced=2, rejected=1, waiting=2))
        self.assertEqual(summaries["actuation_events"], SynchronizationSummary(waiting=1))
        self.assertEqual(gateway.sent, [("ENV-001", 1), ("ENV-001", 2), ("ENV-001", 5)])
        self.assertEqual(
            [self.readings.status_of(i) for i in range(1, 6)],
            [SyncStatus.SYNCED, SyncStatus.REJECTED, SyncStatus.PENDING, SyncStatus.PENDING, SyncStatus.SYNCED],
        )

    def test_an_unreachable_cloud_stops_and_keeps_the_answers_already_received(self):
        with self.assertRaises(CloudUnavailableError):
            self.service(FakeTelemetryGateway(fail_on=5)).synchronize_pending()

        self.assertEqual(self.readings.status_of(1), SyncStatus.SYNCED)
        self.assertEqual(self.readings.status_of(5), SyncStatus.PENDING)

    def test_needs_a_gateway_to_synchronize(self):
        with self.assertRaises(CloudSynchronizationDisabled):
            TelemetrySynchronizationApplicationService(None, self.devices, self.readings, self.events).synchronize_pending()


if __name__ == "__main__":
    unittest.main()
