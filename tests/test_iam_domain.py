import unittest
from datetime import UTC, datetime

from iam.domain.credentials import ApiCredential
from iam.domain.entities import Device
from iam.domain.enums import DeviceType


class ApiCredentialTestCase(unittest.TestCase):
    def test_encoded_verifier_round_trips_without_disclosing_secret(self):
        credential = ApiCredential.create("device-secret")
        encoded = credential.encode()
        restored = ApiCredential.from_encoded(encoded)

        self.assertTrue(restored.matches("device-secret"))
        self.assertFalse(restored.matches("wrong-secret"))
        self.assertNotIn("device-secret", encoded)
        self.assertFalse(credential.matches(""))

    def test_rejects_empty_credentials_and_malformed_verifiers(self):
        with self.assertRaises(ValueError):
            ApiCredential.create("")
        with self.assertRaises(ValueError):
            ApiCredential.from_encoded("not-a-verifier")


class DeviceAggregateTestCase(unittest.TestCase):
    def setUp(self):
        self.device = Device.register(
            "ENV-001",
            "initial-secret",
            DeviceType.ENVIRONMENTAL_DEVICE,
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_authentication_is_owned_by_active_device(self):
        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(self.device.authenticate("wrong-secret"))

    def test_rotates_credentials_without_mutating_old_aggregate(self):
        rotated = self.device.rotate_api_key("replacement-secret")

        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(rotated.authenticate("initial-secret"))
        self.assertTrue(rotated.authenticate("replacement-secret"))

    def test_revokes_device_and_rejects_further_lifecycle_changes(self):
        revoked = self.device.revoke(datetime(2026, 9, 27, tzinfo=UTC))

        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(revoked.authenticate("initial-secret"))
        with self.assertRaises(ValueError):
            revoked.revoke()
        with self.assertRaises(ValueError):
            revoked.rotate_api_key("replacement-secret")

    def test_rejects_invalid_device_identity(self):
        with self.assertRaises(ValueError):
            Device.register(" ", "initial-secret", DeviceType.CONTAINER_MONITOR)

    def test_rejects_unknown_device_type(self):
        with self.assertRaises(TypeError):
            Device.register("ENV-001", "initial-secret", "ENVIRONMENT_MONITOR")

    def test_rejects_revocation_before_registration(self):
        with self.assertRaises(ValueError):
            self.device.revoke(datetime(2020, 1, 1, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
