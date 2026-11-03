import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import create_app
from shared.infrastructure.cloud_session import CloudSession
from tracking.application.services import DeviceRegistryApplicationService
from tracking.infrastructure.cloud_registry_gateway import CloudDeviceRegistryGateway
from tracking.infrastructure.repositories import MonitoredDeviceRepository


class FakeCloudHandler(BaseHTTPRequestHandler):
    """Answers the sign-in and the equipment list as the QualiTrack Cloud REST API does."""

    received = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append(("POST", self.path, body, self.headers.get("Authorization")))
        if self.path != "/api/v1/authentication/sign-in" or body.get("password") != "local-secret":
            return self._answer(401, {"message": "Invalid credentials"})
        self._answer(200, {"id": 3, "username": body["username"], "token": "jwt-token",
                           "roles": ["ROLE_OPERATOR"], "laboratoryId": 7, "passwordChangeRequired": False})

    def do_GET(self):
        self.received.append(("GET", self.path, None, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer jwt-token":
            return self._answer(401, {"message": "Unauthorized"})
        if self.path != "/api/v1/laboratories/7/equipments":
            return self._answer(404, {"message": "Not found"})
        self._answer(200, [{
            "id": 15, "laboratoryId": 7, "environmentId": 2, "name": "Space monitor",
            "type": "ENVIRONMENTAL_DEVICE", "model": "ESP32-WROOM-32", "serialNumber": "24:6F:28:AA:10:01",
            "status": "OPERATIONAL", "deviceType": "ENVIRONMENTAL_DEVICE", "sensorExternalId": "ENV-001",
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

    def service(self, password):
        session = CloudSession(self.base_url, "edge.operator@lab.pe", password)
        return DeviceRegistryApplicationService(CloudDeviceRegistryGateway(session), MonitoredDeviceRepository())

    def test_reads_the_registry_over_http_with_the_operator_token(self):
        with self.app.app_context():
            try:
                devices = self.service("local-secret").synchronize_registry()
                stored = MonitoredDeviceRepository().find_by_device_id("ENV-001")
            finally:
                MonitoredDeviceRepository().replace_all([])

        self.assertEqual([device.device_id for device in devices], ["ENV-001"])
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
            self.app.extensions["device_registry_service"] = DeviceRegistryApplicationService(None)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("The cloud rejected the operator credentials", result.output)


if __name__ == "__main__":
    unittest.main()
