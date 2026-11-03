import unittest

from app import create_app


class DevicesCliTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.runner = self.app.test_cli_runner()
        self.client = self.app.test_client()

    def invoke(self, *arguments):
        return self.runner.invoke(args=["devices", *arguments])

    def sign_in(self, device_id, api_key):
        return self.client.post(
            "/api/v1/auth/sign-in",
            json={"device_id": device_id},
            headers={"X-API-Key": api_key},
        )

    @staticmethod
    def api_key_from(result):
        return result.output.strip().splitlines()[-1].rsplit(": ", 1)[1]

    def test_registered_container_monitor_signs_in_with_the_key_shown_once(self):
        result = self.invoke("register", "CNT-001", "CONTAINER_MONITOR")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Registered CNT-001 (CONTAINER_MONITOR)", result.output)
        response = self.sign_in("CNT-001", self.api_key_from(result))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["device_type"], "CONTAINER_MONITOR")

    def test_rejects_duplicate_registration_and_unknown_type(self):
        self.assertEqual(self.invoke("register", "ENV-002", "ENVIRONMENTAL_DEVICE").exit_code, 0)

        duplicate = self.invoke("register", "ENV-002", "ENVIRONMENTAL_DEVICE")
        self.assertEqual(duplicate.exit_code, 1)
        self.assertIn("Device is already registered", duplicate.output)

        unknown_type = self.invoke("register", "ENV-003", "ENVIRONMENT_MONITOR")
        self.assertEqual(unknown_type.exit_code, 2)

    def test_rotated_key_replaces_the_previous_one(self):
        old_key = self.api_key_from(self.invoke("register", "CNT-002", "CONTAINER_MONITOR"))

        result = self.invoke("rotate-key", "CNT-002")

        self.assertEqual(result.exit_code, 0, result.output)
        new_key = self.api_key_from(result)
        self.assertNotEqual(new_key, old_key)
        self.assertEqual(self.sign_in("CNT-002", old_key).status_code, 401)
        self.assertEqual(self.sign_in("CNT-002", new_key).status_code, 200)

    def test_revoked_device_cannot_sign_in_and_is_listed_as_revoked(self):
        api_key = self.api_key_from(self.invoke("register", "CNT-003", "CONTAINER_MONITOR"))

        result = self.invoke("revoke", "CNT-003")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.sign_in("CNT-003", api_key).status_code, 401)
        listed = [line.split() for line in self.invoke("list").output.splitlines()]
        self.assertIn(["CNT-003", "CONTAINER_MONITOR", "revoked"], listed)
        self.assertIn(["ENV-001", "ENVIRONMENTAL_DEVICE", "active"], listed)

    def test_reports_unknown_device(self):
        for command in ("revoke", "rotate-key"):
            with self.subTest(command=command):
                result = self.invoke(command, "CNT-404")
                self.assertEqual(result.exit_code, 1)
                self.assertIn("Device not found", result.output)


if __name__ == "__main__":
    unittest.main()
