import os
import tempfile
import unittest

_test_directory = tempfile.TemporaryDirectory(prefix="qualitrack-edge-test-")
os.environ.update({
    "APP_ENV": "development",
    "DEBUG": "false",
    "DATABASE_PATH": os.path.join(_test_directory.name, "test.db"),
    "SEED_TEST_DEVICE": "true",
    "TEST_DEVICE_ID": "ENV-001",
    "TEST_DEVICE_API_KEY": "test-only-key",
    "HOST": "127.0.0.1",
    "PORT": "5000",
})

from app import create_app
from iam.infrastructure.models import Device as DeviceModel


class IamApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_authenticates_registered_device(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            json={"device_id": "ENV-001"},
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"device_id": "ENV-001", "authenticated": True})

    def test_rejects_invalid_device_credentials(self):
        for device_id, api_key in (
            ("ENV-001", "invalid-key"),
            ("CNT-404", "invalid-key"),
        ):
            with self.subTest(device_id=device_id):
                response = self.client.post(
                    "/api/v1/iam/authentications",
                    json={"device_id": device_id},
                    headers={"X-API-Key": api_key},
                )
                self.assertEqual(response.status_code, 401)

    def test_rejects_missing_api_key(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            json={"device_id": "ENV-001"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Missing X-API-Key"})

    def test_rejects_malformed_or_extra_request_fields(self):
        for body in ({}, {"device_id": "ENV-001", "metric": "AIR_QUALITY"}):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/iam/authentications",
                    json=body,
                    headers={"X-API-Key": "test-only-key"},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_authentication_request(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            data="{}",
            content_type="text/plain",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 415)

    def test_rejects_malformed_json(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            data="{",
            content_type="application/json",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 400)

    def test_seeded_device_stores_a_verifier_instead_of_the_api_key(self):
        with self.app.app_context():
            stored = DeviceModel.get_by_id("ENV-001")

        self.assertNotEqual(stored.credential_verifier, "test-only-key")
        self.assertTrue(stored.credential_verifier.startswith("pbkdf2_sha256$"))
        self.assertEqual(stored.device_type, "ENVIRONMENTAL_DEVICE")


if __name__ == "__main__":
    unittest.main()
