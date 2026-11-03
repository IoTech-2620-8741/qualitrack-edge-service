import unittest
from datetime import UTC, datetime, timedelta

from app import create_app
from iam.domain.enums import DeviceType as IamDeviceType
from tracking.application.errors import DeviceAuthenticationFailed, DeviceTypeNotAllowed
from tracking.application.services import TelemetryApplicationService
from tracking.domain.entities import ReportingDevice
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.errors import TelemetryConflict
from tracking.infrastructure.models import TelemetryReading as TelemetryReadingModel
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


class IamAntiCorruptionLayerTestCase(unittest.TestCase):
    def test_translates_iam_authentication_to_the_tracking_concept(self):
        class FakeIamDevice:
            device_id = "CNT-001"
            device_type = IamDeviceType.CONTAINER_MONITOR

        class FakeIamAuthenticationService:
            def authenticated_device(self, device_id, api_key):
                return FakeIamDevice() if api_key == "secret" else None

        layer = IamAntiCorruptionLayer(FakeIamAuthenticationService())

        self.assertEqual(
            layer.authenticated_reporting_device("CNT-001", "secret"),
            ReportingDevice("CNT-001", DeviceType.CONTAINER_MONITOR),
        )
        self.assertIsNone(layer.authenticated_reporting_device("CNT-001", "invalid"))


class FakeLayer:
    def __init__(self, device):
        self.device = device

    def authenticated_reporting_device(self, device_id, api_key):
        return self.device if api_key == "secret" else None


class InMemoryRepository:
    def __init__(self):
        self.saved = []

    def save_idempotently(self, records):
        self.saved.append(records)
        return records


class TelemetryApplicationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.readings = InMemoryRepository()
        self.events = InMemoryRepository()
        self.service = TelemetryApplicationService(
            FakeLayer(ReportingDevice("CNT-001", DeviceType.CONTAINER_MONITOR)),
            self.readings,
            self.events,
            clock=lambda: NOW,
        )

    def test_creates_one_pending_reading_per_metric_received_now(self):
        readings = self.service.record_readings(
            "CNT-001", "secret", DeviceType.CONTAINER_MONITOR, NOW, 3,
            {Metric.TEMPERATURE: 21.5, Metric.RFID_TAG: "E200-3412-0001"},
        )

        self.assertEqual([r.metric for r in readings], [Metric.TEMPERATURE, Metric.RFID_TAG])
        self.assertTrue(all(r.received_at == NOW and r.sync_status is SyncStatus.PENDING for r in readings))

    def test_rejects_unknown_nodes_other_types_and_empty_readings(self):
        with self.assertRaises(DeviceAuthenticationFailed):
            self.service.record_readings("CNT-001", "invalid", DeviceType.CONTAINER_MONITOR, NOW, None,
                                         {Metric.TEMPERATURE: 20})
        with self.assertRaises(DeviceTypeNotAllowed):
            self.service.record_readings("CNT-001", "secret", DeviceType.ENVIRONMENTAL_DEVICE, NOW, None,
                                         {Metric.AIR_QUALITY: 400})
        with self.assertRaisesRegex(ValueError, "at least one metric"):
            self.service.record_readings("CNT-001", "secret", DeviceType.CONTAINER_MONITOR, NOW, None, {})
        self.assertEqual(self.readings.saved, [])

    def test_only_container_monitors_send_actions(self):
        service = TelemetryApplicationService(
            FakeLayer(ReportingDevice("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)),
            self.readings, self.events, clock=lambda: NOW,
        )

        with self.assertRaises(DeviceTypeNotAllowed):
            service.record_actuation_event("ENV-001", "secret", ActuationAction.VENTILATION_ON, None, None,
                                           ActuationResult.EXECUTED, NOW, None)


class TelemetryApiTestCase(unittest.TestCase):
    """End-to-end reception through the Flask application and SQLite (TS55, TS56, TS57, TS60)."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True
        with cls.app.app_context():
            cls.container_key = cls.app.extensions["iam_authentication_service"].register_device(
                "CNT-API", IamDeviceType.CONTAINER_MONITOR
            ).api_key

    def setUp(self):
        self.client = self.app.test_client()

    def post(self, path, body, api_key):
        headers = {} if api_key is None else {"X-API-Key": api_key}
        return self.client.post(path, json=body, headers=headers)

    @staticmethod
    def at(minutes):
        return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()

    def test_environment_readings_are_stored_pending_and_retries_are_not_duplicated(self):
        body = {"device_id": "ENV-001", "measured_at": self.at(30), "profile_version": 2,
                "air_quality": 412.5, "motion": True}

        created = self.post("/api/v1/monitoring/environment-readings", body, "test-only-key")
        retried = self.post("/api/v1/monitoring/environment-readings", body, "test-only-key")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            [(r["metric"], r["value"], r["sync_status"]) for r in created.json["readings"]],
            [("AIR_QUALITY", 412.5, "PENDING"), ("MOTION", 1.0, "PENDING")],
        )
        self.assertTrue(created.json["measured_at"].endswith("Z"))
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json["readings"], created.json["readings"])
        with self.app.app_context():
            stored = TelemetryReadingModel.select().where(TelemetryReadingModel.device_id == "ENV-001").count()
        self.assertGreaterEqual(stored, 2)

    def test_container_readings_with_a_changed_value_conflict_and_store_nothing(self):
        measured_at = self.at(40)
        first = self.post("/api/v1/monitoring/container-readings",
                          {"device_id": "CNT-API", "measured_at": measured_at, "humidity": 48},
                          self.container_key)
        changed = self.post("/api/v1/monitoring/container-readings",
                            {"device_id": "CNT-API", "measured_at": measured_at, "temperature": 21.5,
                             "humidity": 50},
                            self.container_key)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(changed.status_code, 409)
        with self.app.app_context():
            stored = [row.metric for row in TelemetryReadingModel.select().where(
                (TelemetryReadingModel.device_id == "CNT-API")
                & (TelemetryReadingModel.measured_at == datetime.fromisoformat(measured_at).astimezone(UTC))
            )]
        self.assertEqual(stored, ["HUMIDITY"])

    def test_container_reading_with_every_metric(self):
        response = self.post("/api/v1/monitoring/container-readings", {
            "device_id": "CNT-API", "measured_at": self.at(50), "profile_version": 3,
            "temperature": 21.5, "humidity": 48, "luminosity": 120, "rfid_tag": "E200-3412-0001",
        }, self.container_key)

        self.assertEqual(response.status_code, 201)
        self.assertEqual([r["metric"] for r in response.json["readings"]],
                         ["TEMPERATURE", "HUMIDITY", "LUMINOSITY", "RFID_TAG"])
        self.assertEqual(response.json["readings"][3]["text_value"], "E200-3412-0001")

    def test_rejects_credentials_and_node_types(self):
        body = {"device_id": "ENV-001", "measured_at": self.at(60), "temperature": 21.5}
        for api_key, expected in ((None, 401), ("invalid-key", 401), ("test-only-key", 403)):
            with self.subTest(api_key=api_key):
                response = self.post("/api/v1/monitoring/container-readings", body, api_key)
                self.assertEqual(response.status_code, expected)
        missing_device = self.post("/api/v1/monitoring/environment-readings",
                                   {"measured_at": self.at(60), "air_quality": 400}, "test-only-key")
        self.assertEqual(missing_device.status_code, 401)

    def test_rejects_invalid_readings(self):
        for body in (
            {"device_id": "ENV-001", "measured_at": self.at(70)},
            {"device_id": "ENV-001", "measured_at": "2026-11-03T10:00:00", "air_quality": 400},
            {"device_id": "ENV-001", "air_quality": 400},
            {"device_id": "ENV-001", "measured_at": self.at(-10), "air_quality": 400},
            {"device_id": "ENV-001", "measured_at": self.at(70), "air_quality": "high"},
            {"device_id": "ENV-001", "measured_at": self.at(70), "temperature": 21.5},
            {"device_id": "ENV-001", "measured_at": self.at(70), "air_quality": 400, "profile_version": -1},
        ):
            with self.subTest(body=body):
                response = self.post("/api/v1/monitoring/environment-readings", body, "test-only-key")
                self.assertEqual(response.status_code, 400)
        non_json = self.client.post("/api/v1/monitoring/environment-readings", data="{}",
                                    content_type="text/plain", headers={"X-API-Key": "test-only-key"})
        self.assertEqual(non_json.status_code, 415)

    def test_actuation_events_are_stored_once(self):
        body = {"device_id": "CNT-API", "action": "VENTILATION_ON", "trigger_metric": "TEMPERATURE",
                "trigger_state": "WARNING", "occurred_at": self.at(80), "profile_version": 3}

        created = self.post("/api/v1/control/actuation-events", body, self.container_key)
        retried = self.post("/api/v1/control/actuation-events", body, self.container_key)
        changed = self.post("/api/v1/control/actuation-events", {**body, "result": "FAILED"}, self.container_key)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["result"], "EXECUTED")
        self.assertEqual(created.json["sync_status"], "PENDING")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json["id"], created.json["id"])
        self.assertEqual(changed.status_code, 409)

    def test_rejects_invalid_actuation_events(self):
        base = {"device_id": "CNT-API", "action": "SERVO_OPEN", "occurred_at": self.at(90)}
        for body, api_key, expected in (
            ({**base, "trigger_metric": "TEMPERATURE"}, self.container_key, 400),
            ({**base, "action": "FAN_SPIN"}, self.container_key, 400),
            ({**base, "trigger_metric": "RFID_TAG", "trigger_state": "WARNING"}, self.container_key, 400),
            ({**base, "device_id": "ENV-001"}, "test-only-key", 403),
        ):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/v1/control/actuation-events", body, api_key).status_code, expected)

    def test_the_pending_command_counts_the_stored_records(self):
        self.post("/api/v1/control/actuation-events",
                  {"device_id": "CNT-API", "action": "SERVO_CLOSE", "occurred_at": self.at(100)},
                  self.container_key)

        result = self.app.test_cli_runner().invoke(args=["cloud", "pending"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.output, r"Readings: PENDING \d+, SYNCED 0, REJECTED 0")
        self.assertRegex(result.output, r"Actuation events: PENDING [1-9]\d*, SYNCED 0, REJECTED 0")


class TelemetryRepositoryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_a_conflicting_submission_stores_none_of_its_readings(self):
        service = TelemetryApplicationService(
            FakeLayer(ReportingDevice("CNT-REPO", DeviceType.CONTAINER_MONITOR)), clock=lambda: NOW
        )
        with self.app.app_context():
            service.record_readings("CNT-REPO", "secret", DeviceType.CONTAINER_MONITOR, NOW, None,
                                    {Metric.TEMPERATURE: 20.0})
            with self.assertRaises(TelemetryConflict):
                service.record_readings("CNT-REPO", "secret", DeviceType.CONTAINER_MONITOR, NOW, None,
                                        {Metric.LUMINOSITY: 90.0, Metric.TEMPERATURE: 25.0})
            stored = [row.metric for row in TelemetryReadingModel.select().where(
                TelemetryReadingModel.device_id == "CNT-REPO")]

        self.assertEqual(stored, ["TEMPERATURE"])

    def test_actions_keep_their_cause_and_result(self):
        service = TelemetryApplicationService(
            FakeLayer(ReportingDevice("CNT-REPO", DeviceType.CONTAINER_MONITOR)), clock=lambda: NOW
        )
        with self.app.app_context():
            first = service.record_actuation_event("CNT-REPO", "secret", ActuationAction.COOLING_ON,
                                                   Metric.HUMIDITY, EnvironmentalState.CRITICAL,
                                                   ActuationResult.FAILED, NOW, 5)
            retry = service.record_actuation_event("CNT-REPO", "secret", ActuationAction.COOLING_ON,
                                                   Metric.HUMIDITY, EnvironmentalState.CRITICAL,
                                                   ActuationResult.FAILED, NOW, 5)

        self.assertTrue(first.created)
        self.assertFalse(retry.created)
        self.assertEqual(retry.event, first.event)


if __name__ == "__main__":
    unittest.main()
