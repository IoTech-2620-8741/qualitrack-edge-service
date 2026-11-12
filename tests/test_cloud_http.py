import json
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import create_app
from shared.infrastructure.cloud_session import CloudSession
from sync.application.services import (
    ProfileSynchronizationApplicationService,
    TelemetrySynchronizationApplicationService,
)
from sync.infrastructure.cloud_profile_gateway import CloudProfileGateway
from sync.infrastructure.cloud_telemetry_gateway import CloudTelemetryGateway
from tracking.application.services import DeviceRegistryApplicationService
from tracking.infrastructure.cloud_registry_gateway import CloudDeviceRegistryGateway
from tracking.infrastructure.models import ActuationEvent as ActuationEventModel
from tracking.infrastructure.models import TelemetryReading as TelemetryReadingModel
from tracking.infrastructure.repositories import EnvironmentalProfileRepository, MonitoredDeviceRepository

_ENVIRONMENT_TELEMETRY = "/api/v1/laboratories/7/environments/2/telemetry-measurements"


class FakeCloudHandler(BaseHTTPRequestHandler):
    """Answers as the QualiTrack Cloud REST API does: sign-in, equipment list and telemetry.

    The telemetry route answers ``201`` for a new reading, ``200`` for one it
    already has and ``400`` for a negative air quality.
    """

    received = []
    stored = set()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append(("POST", self.path, body, self.headers.get("Authorization")))
        if self.path == _ENVIRONMENT_TELEMETRY:
            return self._telemetry(body)
        if self.path != "/api/v1/authentication/sign-in" or body.get("password") != "local-secret":
            return self._answer(401, {"message": "Invalid credentials"})
        self._answer(200, {"id": 3, "username": body["username"], "token": "jwt-token",
                           "roles": ["ROLE_OPERATOR"], "laboratoryId": 7, "passwordChangeRequired": False})

    def _telemetry(self, body):
        if self.headers.get("Authorization") != "Bearer jwt-token":
            return self._answer(401, {"message": "Unauthorized"})
        if body["metric"] == "AIR_QUALITY" and body["value"] < 0:
            return self._answer(400, {"message": "Invalid air quality"})
        key = (body["metric"], body["measuredAt"])
        created = key not in self.stored
        self.stored.add(key)
        self._answer(201 if created else 200, {"id": len(self.stored), **body})

    def do_GET(self):
        self.received.append(("GET", self.path, None, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer jwt-token":
            return self._answer(401, {"message": "Unauthorized"})
        if self.path == "/api/v1/laboratories/7/environments/2/devices/15/environmental-profile":
            return self._answer(200, {
                "id": 4, "scope": "ENVIRONMENT", "laboratoryId": 7, "environmentId": 2, "deviceId": None,
                "version": 5, "updatedAt": "2026-11-03T14:00:00Z", "updatedBy": 3, "actuationRules": [],
                "thresholds": [{"metric": "AIR_QUALITY", "unit": "ppm", "normalMin": None, "normalMax": 800.0,
                                "criticalMin": None, "criticalMax": 1200.0}],
            })
        if self.path != "/api/v1/laboratories/7/equipments":
            return self._answer(404, {"message": "Not found"})
        self._answer(200, [{
            "id": 15, "laboratoryId": 7, "environmentId": 2, "name": "Space monitor",
            "type": "ENVIRONMENTAL_DEVICE", "model": "ESP32-WROOM-32", "serialNumber": "24:6F:28:AA:10:01",
            "status": "OPERATIONAL", "deviceType": "ENVIRONMENTAL_DEVICE", "sensorExternalId": "ENV-001",
            "firmwareVersion": "1.0.3",
        }, {
            # A node of another environment that another station serves.
            "id": 30, "laboratoryId": 7, "environmentId": 9, "name": "Cold room monitor",
            "type": "CONTAINER_MONITOR", "model": "ESP32-WROOM-32", "serialNumber": "24:6F:28:AA:10:09",
            "status": "OPERATIONAL", "deviceType": "CONTAINER_MONITOR", "sensorExternalId": "CNT-OTHER",
            "firmwareVersion": "1.0.3",
        }])

    def _answer(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_arguments):
        pass


class CloudOverHttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloudHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.app = create_app()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeCloudHandler.received.clear()
        FakeCloudHandler.stored.clear()
        self._clear_tables()

    def tearDown(self):
        self._clear_tables()

    def _clear_tables(self):
        with self.app.app_context():
            TelemetryReadingModel.delete().execute()
            ActuationEventModel.delete().execute()
            MonitoredDeviceRepository().replace_all([])
            EnvironmentalProfileRepository().replace_all([])

    def layer(self):
        return self.app.extensions["environmental_profile_service"].iam_anti_corruption_layer

    def service(self, password):
        session = CloudSession(self.base_url, "edge.operator@lab.pe", password)
        return DeviceRegistryApplicationService(
            CloudDeviceRegistryGateway(session), self.layer(), MonitoredDeviceRepository()
        )

    def test_the_sync_command_sends_what_the_nodes_sent_to_the_edge(self):
        client = self.app.test_client()
        measured_at = (datetime.now(UTC) - timedelta(minutes=10)).replace(microsecond=0)
        for minutes, air_quality in ((0, 412.5), (1, -3.0)):
            sent = client.post("/api/v1/monitoring/environment-readings", headers={"X-API-Key": "test-only-key"},
                               json={"device_id": "ENV-001", "air_quality": air_quality, "profile_version": 2,
                                     "measured_at": (measured_at + timedelta(minutes=minutes)).isoformat()})
            self.assertEqual(sent.status_code, 201)
        session = CloudSession(self.base_url, "edge.operator@lab.pe", "local-secret")
        layer = self.layer()
        services = {
            "device_registry_service": DeviceRegistryApplicationService(CloudDeviceRegistryGateway(session), layer),
            "profile_synchronization_service": ProfileSynchronizationApplicationService(
                CloudProfileGateway(session)),
            "telemetry_synchronization_service": TelemetrySynchronizationApplicationService(
                CloudTelemetryGateway(session)),
        }
        previous = {name: self.app.extensions[name] for name in services}
        self.app.extensions.update(services)
        try:
            with self.assertLogs("sync.infrastructure.cloud_telemetry_gateway", "WARNING"):
                first = self.app.test_cli_runner().invoke(args=["cloud", "sync"])
            second = self.app.test_cli_runner().invoke(args=["cloud", "sync"])
        finally:
            self.app.extensions.update(previous)

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("Device registry: 1 device(s) of this Edge from the cloud", first.output)
        self.assertIn("Environmental profiles: 1 profile(s) from the cloud", first.output)
        profile_requests = [path for method, path, _, _ in FakeCloudHandler.received
                            if path.endswith("/environmental-profile")]
        self.assertEqual(set(profile_requests),
                         {"/api/v1/laboratories/7/environments/2/devices/15/environmental-profile"})
        self.assertIn("Readings: 1 synced, 1 rejected, 0 waiting", first.output)
        self.assertIn("Readings: 0 synced, 0 rejected, 0 waiting", second.output)
        telemetry = [(body, authorization) for method, path, body, authorization in FakeCloudHandler.received
                     if path == _ENVIRONMENT_TELEMETRY]
        self.assertEqual(len(telemetry), 2)
        self.assertEqual(telemetry[0], ({
            "metric": "AIR_QUALITY", "value": 412.5, "textValue": None,
            "measuredAt": measured_at.isoformat().replace("+00:00", "Z"), "profileVersion": 2,
        }, "Bearer jwt-token"))
        with self.app.app_context():
            statuses = [row.sync_status for row in TelemetryReadingModel.select().order_by(TelemetryReadingModel.id)]
        self.assertEqual(statuses, ["SYNCED", "REJECTED"])

        configuration = client.get("/api/v1/configurations/ENV-001", headers={"X-API-Key": "test-only-key"})
        self.assertEqual(configuration.status_code, 200)
        self.assertEqual(configuration.json["profile_version"], 5)
        self.assertEqual(configuration.json["device_type"], "ENVIRONMENTAL_DEVICE")
        self.assertEqual(configuration.json["thresholds"], [{
            "metric": "AIR_QUALITY", "unit": "ppm", "normal_min": None, "normal_max": 800.0,
            "critical_min": None, "critical_max": 1200.0,
        }])
        self.assertEqual(configuration.json["updated_at"], "2026-11-03T14:00:00Z")

    def test_reads_the_registry_over_http_with_the_operator_token(self):
        with self.app.app_context():
            try:
                registry = self.service("local-secret").synchronize_registry()
                stored = MonitoredDeviceRepository().find_by_device_id("ENV-001")
                other_station = MonitoredDeviceRepository().find_by_device_id("CNT-OTHER")
            finally:
                MonitoredDeviceRepository().replace_all([])

        self.assertEqual([device.device_id for device in registry.devices], ["ENV-001"])
        self.assertIsNone(other_station)
        self.assertEqual((stored.laboratory_id, stored.environment_id, stored.cloud_device_id), (7, 2, 15))
        self.assertEqual(
            [(method, path, authorization) for method, path, _, authorization in FakeCloudHandler.received],
            [("POST", "/api/v1/authentication/sign-in", None),
             ("GET", "/api/v1/laboratories/7/equipments", "Bearer jwt-token")],
        )
        self.assertEqual(FakeCloudHandler.received[0][2],
                         {"username": "edge.operator@lab.pe", "password": "local-secret"})

    def test_the_sync_command_reports_rejected_credentials(self):
        self.app.extensions["device_registry_service"] = self.service("wrong-secret")
        try:
            result = self.app.test_cli_runner().invoke(args=["cloud", "sync"])
        finally:
            self.app.extensions["device_registry_service"] = DeviceRegistryApplicationService(None, self.layer())

        self.assertEqual(result.exit_code, 1)
        self.assertIn("The cloud rejected the operator credentials", result.output)


if __name__ == "__main__":
    unittest.main()
