import unittest

from app import create_app
from iam.infrastructure.models import Device as DeviceModel


class IamApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_signs_in_registered_device_with_its_type(self):
        response = self.client.post(
            "/api/v1/auth/sign-in",
            json={"device_id": "ENV-001"},
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {"device_id": "ENV-001", "device_type": "ENVIRONMENTAL_DEVICE", "authenticated": True},
        )

    def test_rejects_invalid_device_credentials(self):
        for device_id, api_key in (
            ("ENV-001", "invalid-key"),
            ("CNT-404", "invalid-key"),
        ):
            with self.subTest(device_id=device_id):
                response = self.client.post(
                    "/api/v1/auth/sign-in",
                    json={"device_id": device_id},
                    headers={"X-API-Key": api_key},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json, {"error": "Invalid device_id or API key"})

    def test_rejects_missing_api_key(self):
        response = self.client.post(
            "/api/v1/auth/sign-in",
            json={"device_id": "ENV-001"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Missing X-API-Key"})

    def test_rejects_malformed_or_extra_request_fields(self):
        for body in ({}, {"device_id": " "}, {"device_id": "ENV-001", "metric": "AIR_QUALITY"}):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/auth/sign-in",
                    json=body,
                    headers={"X-API-Key": "test-only-key"},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_sign_in_request(self):
        response = self.client.post(
            "/api/v1/auth/sign-in",
            data="{}",
            content_type="text/plain",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 415)

    def test_rejects_malformed_json(self):
        response = self.client.post(
            "/api/v1/auth/sign-in",
            data="{",
            content_type="application/json",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 400)

    def test_previous_authentication_route_is_removed(self):
        response = self.client.post(
            "/api/v1/iam/authentications",
            json={"device_id": "ENV-001"},
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 404)

    def test_seeded_device_stores_a_verifier_instead_of_the_api_key(self):
        with self.app.app_context():
            stored = DeviceModel.get_by_id("ENV-001")

        self.assertNotEqual(stored.credential_verifier, "test-only-key")
        self.assertTrue(stored.credential_verifier.startswith("sha256$"))
        self.assertEqual(stored.device_type, "ENVIRONMENTAL_DEVICE")


if __name__ == "__main__":
    unittest.main()
