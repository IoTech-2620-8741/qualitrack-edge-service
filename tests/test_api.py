import unittest

from app import create_app
from iam.infrastructure.models import Device as DeviceModel


class IamApiTestCase(unittest.TestCase):
    """Tests the authentication endpoint and the storage of device credentials.

       Validates that registered devices sign in with their correct type, that
       invalid or missing credentials are rejected with the expected HTTP status
       codes, and that the seeded device stores a SHA-256 verifier instead of the
       raw API key.
       """
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_signs_in_registered_device_with_its_type(self):

        """A registered device authenticates and receives its type."""

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
        """Unknown devices or wrong keys are rejected with 401."""

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
        """The sign-in request without X-API-Key is rejected with 401."""

        response = self.client.post(
            "/api/v1/auth/sign-in",
            json={"device_id": "ENV-001"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "Missing X-API-Key"})

    def test_rejects_malformed_or_extra_request_fields(self):
        """Invalid bodies or unexpected fields are rejected with 400."""

        for body in ({}, {"device_id": " "}, {"device_id": "ENV-001", "metric": "AIR_QUALITY"}):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/v1/auth/sign-in",
                    json=body,
                    headers={"X-API-Key": "test-only-key"},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_sign_in_request(self):
        """A non-JSON request is rejected with 415."""

        response = self.client.post(
            "/api/v1/auth/sign-in",
            data="{}",
            content_type="text/plain",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 415)

    def test_rejects_malformed_json(self):
        """Malformed JSON is rejected with 400."""

        response = self.client.post(
            "/api/v1/auth/sign-in",
            data="{",
            content_type="application/json",
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 400)

    def test_previous_authentication_route_is_removed(self):
        """The old IAM authentication route no longer exists (404)."""

        response = self.client.post(
            "/api/v1/iam/authentications",
            json={"device_id": "ENV-001"},
            headers={"X-API-Key": "test-only-key"},
        )

        self.assertEqual(response.status_code, 404)

    def test_seeded_device_stores_a_verifier_instead_of_the_api_key(self):
        """The seeded device stores a SHA-256 verifier, not the raw key."""

        with self.app.app_context():
            stored = DeviceModel.get_by_id("ENV-001")

        self.assertNotEqual(stored.credential_verifier, "test-only-key")
        self.assertTrue(stored.credential_verifier.startswith("sha256$"))
        self.assertEqual(stored.device_type, "ENVIRONMENTAL_DEVICE")


if __name__ == "__main__":
    unittest.main()
