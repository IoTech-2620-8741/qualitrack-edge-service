import base64
import hashlib
import unittest
from datetime import UTC, datetime

from iam.domain.credentials import ApiCredential
from iam.domain.entities import Device
from iam.domain.enums import DeviceType


class ApiCredentialTestCase(unittest.TestCase):
    """Tests the API credential that stores a one-way verifier of the key.

    Validates that new verifiers are a SHA-256 digest, that verifiers stored
    before as PBKDF2 are still accepted, and that empty credentials and
    malformed verifiers are rejected.
    """

    def test_encoded_verifier_round_trips_without_disclosing_secret(self):
        """The verifier matches the original key and never contains it."""
        credential = ApiCredential.create("device-secret")
        encoded = credential.encode()
        restored = ApiCredential.from_encoded(encoded)

        self.assertTrue(restored.matches("device-secret"))
        self.assertFalse(restored.matches("wrong-secret"))
        self.assertNotIn("device-secret", encoded)
        self.assertFalse(credential.matches(""))

    def test_new_verifiers_are_a_sha256_digest_of_the_key(self):
        """New verifiers are the SHA-256 digest of the key, in base64."""
        encoded = ApiCredential.create("device-secret").encode()

        self.assertTrue(encoded.startswith("sha256$"))
        self.assertEqual(
            encoded.removeprefix("sha256$"),
            base64.urlsafe_b64encode(hashlib.sha256(b"device-secret").digest()).decode().rstrip("="),
        )

    def test_still_accepts_the_pbkdf2_verifiers_stored_before(self):
        """A verifier stored before as PBKDF2 is still accepted and preserved."""
        salt = bytes(range(16))
        digest = hashlib.pbkdf2_hmac("sha256", b"device-secret", salt, 310_000)
        encoded = "pbkdf2_sha256$310000${}${}".format(
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        )

        restored = ApiCredential.from_encoded(encoded)

        self.assertTrue(restored.matches("device-secret"))
        self.assertFalse(restored.matches("wrong-secret"))
        self.assertEqual(restored.encode(), encoded)

    def test_rejects_empty_credentials_and_malformed_verifiers(self):
        """Empty keys and malformed verifiers are rejected with ValueError."""
        with self.assertRaises(ValueError):
            ApiCredential.create("")
        with self.assertRaises(ValueError):
            ApiCredential.from_encoded("not-a-verifier")
        for encoded in ("sha256$", "sha256$abc$def", "pbkdf2_sha256$10$AAAAAAAAAAAAAAAAAAAAAA$" + "A" * 43):
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                ApiCredential.from_encoded(encoded)


class DeviceAggregateTestCase(unittest.TestCase):
    """Tests the lifecycle of the Device aggregate.

    Validates authentication, rotation and revocation as immutable operations,
    the rejection of invalid identity or type, and the refusal to revoke before
    registration.
    """

    def setUp(self):
        self.device = Device.register(
            "ENV-001",
            "initial-secret",
            DeviceType.ENVIRONMENTAL_DEVICE,
            datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_authentication_is_owned_by_active_device(self):
        """An active device authenticates with its own key only."""
        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(self.device.authenticate("wrong-secret"))

    def test_rotates_credentials_without_mutating_old_aggregate(self):
        """Rotation returns a new aggregate; the old one keeps its key."""
        rotated = self.device.rotate_api_key("replacement-secret")

        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(rotated.authenticate("initial-secret"))
        self.assertTrue(rotated.authenticate("replacement-secret"))

    def test_revokes_device_and_rejects_further_lifecycle_changes(self):
        """A revoked device cannot authenticate nor change its key."""
        revoked = self.device.revoke(datetime(2026, 9, 27, tzinfo=UTC))

        self.assertTrue(self.device.authenticate("initial-secret"))
        self.assertFalse(revoked.authenticate("initial-secret"))
        with self.assertRaises(ValueError):
            revoked.revoke()
        with self.assertRaises(ValueError):
            revoked.rotate_api_key("replacement-secret")

    def test_rejects_invalid_device_identity(self):
        """A blank device identifier is rejected with ValueError."""
        with self.assertRaises(ValueError):
            Device.register(" ", "initial-secret", DeviceType.CONTAINER_MONITOR)

    def test_rejects_unknown_device_type(self):
        """An unknown device type is rejected with TypeError."""
        with self.assertRaises(TypeError):
            Device.register("ENV-001", "initial-secret", "ENVIRONMENT_MONITOR")

    def test_rejects_revocation_before_registration(self):
        """Revoking before the registration time is rejected with ValueError."""
        with self.assertRaises(ValueError):
            self.device.revoke(datetime(2020, 1, 1, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
