import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import load_config


_ENV_FILE_VALUES = {
    "development": {
        "APP_ENV": "development",
        "DEBUG": "true",
        "DATABASE_PATH": "data/edge.db",
        "SEED_TEST_DEVICE": "false",
        "TEST_DEVICE_ID": "ENV-001",
        "TEST_DEVICE_API_KEY": "",
        "HOST": "0.0.0.0",
        "PORT": "5000",
        "CLOUD_SYNC_ENABLED": "false",
    },
    "production": {
        "APP_ENV": "production",
        "DEBUG": "false",
        "DATABASE_PATH": "data/edge.db",
        "SEED_TEST_DEVICE": "false",
        "TEST_DEVICE_ID": "ENV-001",
        "TEST_DEVICE_API_KEY": "",
        "HOST": "0.0.0.0",
        "PORT": "5000",
        "CLOUD_SYNC_ENABLED": "false",
    },
}


def write_environment_file(root, environment, **overrides):
    values = {**_ENV_FILE_VALUES[environment], **overrides}
    (root / f".env.{environment}").write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"
    )


class LoadConfigTestCase(unittest.TestCase):
    """Tests the loading and validation of the environment settings.

    Validates that the selected .env file is read, that process environment
    variables override it, that production defaults are secure, that missing or
    invalid settings raise ValueError, and that cloud settings are required only
    when synchronization is enabled.
    """

    def test_loads_selected_dotenv_and_process_environment_overrides_it(self):
        """The .env file is read and the process environment overrides it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development", DATABASE_PATH="local.db", PORT="4000")
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "DATABASE_PATH": "override.db"},
                clear=True,
            ):
                config = load_config(root)

        self.assertTrue(config["DEBUG"])
        self.assertEqual(config["PORT"], 4000)
        self.assertEqual(config["DATABASE_PATH"], str(root / "override.db"))

    def test_production_defaults_are_secure(self):
        """Production defaults disable debug and test seeding."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "production")
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                config = load_config(root)

        self.assertFalse(config["DEBUG"])
        self.assertFalse(config["SEED_TEST_DEVICE"])
        self.assertIsNone(config["TEST_DEVICE_API_KEY"])
        self.assertEqual(config["DATABASE_PATH"], str(root / "data/edge.db"))

    def test_requires_settings_to_be_defined_in_file_or_process_environment(self):
        """A missing required setting raises ValueError."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.development").write_text("APP_ENV=development\n")
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError, "DEBUG must be set"
            ):
                load_config(root)

    def test_rejects_debug_or_test_seeding_in_production(self):
        """Debug or test seeding in production raises ValueError."""
        for setting in (
            {"DEBUG": "true"},
            {"SEED_TEST_DEVICE": "true", "TEST_DEVICE_API_KEY": "local-secret"},
        ):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                write_environment_file(Path(directory), "production")
                with patch.dict(
                    os.environ, {"APP_ENV": "production", **setting}, clear=True
                ), self.assertRaises(ValueError):
                    load_config(Path(directory))

    def test_requires_key_for_development_device_seed(self):
        """Seeding a development device requires TEST_DEVICE_API_KEY."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "SEED_TEST_DEVICE": "true"},
                clear=True,
            ), self.assertRaisesRegex(ValueError, "TEST_DEVICE_API_KEY"):
                load_config(root)

    def test_retention_days_default_to_7_and_90_and_can_be_set(self):
        """Retention days default to 7 and 90 and can be overridden."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
                defaults = load_config(root)
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "SYNCED_RETENTION_DAYS": "3", "REJECTED_RETENTION_DAYS": " 30 "},
                clear=True,
            ):
                configured = load_config(root)

        self.assertEqual((defaults["SYNCED_RETENTION_DAYS"], defaults["REJECTED_RETENTION_DAYS"]), (7, 90))
        self.assertEqual((configured["SYNCED_RETENTION_DAYS"], configured["REJECTED_RETENTION_DAYS"]), (3, 30))

    def test_rejects_retention_days_that_are_not_a_positive_integer(self):
        """Retention days must be positive integers; otherwise ValueError."""
        for name in ("SYNCED_RETENTION_DAYS", "REJECTED_RETENTION_DAYS"):
            for value, message in (("0", "at least 1"), ("-5", "at least 1"), ("week", "an integer")):
                with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    write_environment_file(root, "development")
                    with patch.dict(
                        os.environ, {"APP_ENV": "development", name: value}, clear=True
                    ), self.assertRaisesRegex(ValueError, f"{name} must be {message}"):
                        load_config(root)

    def test_cloud_settings_are_optional_while_the_synchronization_is_disabled(self):
        """Cloud settings are optional when synchronization is disabled."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
                config = load_config(root)

        self.assertFalse(config["CLOUD_SYNC_ENABLED"])
        self.assertIsNone(config["CLOUD_API_URL"])
        self.assertIsNone(config["CLOUD_SYNC_INTERVAL_SECONDS"])

    def test_loads_the_cloud_connection_when_the_synchronization_is_enabled(self):
        """Cloud settings are required and loaded when synchronization is enabled."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            write_environment_file(
                root,
                "development",
                CLOUD_SYNC_ENABLED="true",
                CLOUD_API_URL="http://localhost:8080/",
                CLOUD_DEVICE_ID="EDGE-001",
                CLOUD_SYNC_INTERVAL_SECONDS="60",
            )

            with patch.dict(
                    os.environ,
                    {
                        "APP_ENV": "development",
                        "CLOUD_API_KEY": "gateway-secret",
                    },
                    clear=True,
            ):
                config = load_config(root)

        self.assertTrue(config["CLOUD_SYNC_ENABLED"])
        self.assertEqual(config["CLOUD_API_URL"], "http://localhost:8080")
        self.assertEqual(config["CLOUD_DEVICE_ID"], "EDGE-001")
        self.assertEqual(config["CLOUD_API_KEY"], "gateway-secret")
        self.assertEqual(config["CLOUD_SYNC_INTERVAL_SECONDS"], 60)

    def test_rejects_an_incomplete_or_insecure_cloud_connection(self):
        """Incomplete or insecure cloud settings raise ValueError."""
        enabled = {
            "CLOUD_SYNC_ENABLED": "true",
            "CLOUD_API_URL": "https://cloud.example",
            "CLOUD_DEVICE_ID": "EDGE-001",
            "CLOUD_API_KEY": "gateway-secret",
            "CLOUD_SYNC_INTERVAL_SECONDS": "60",
        }
        for environment, overrides, message in (
                ("development", {"CLOUD_API_KEY": ""}, "CLOUD_API_KEY cannot be empty"),
                ("development", {"CLOUD_DEVICE_ID": ""}, "CLOUD_DEVICE_ID cannot be empty"),
                ("development", {"CLOUD_API_URL": "cloud.example"}, "http or https"),
                ("development", {"CLOUD_SYNC_INTERVAL_SECONDS": "0"}, "at least 1"),
                ("development", {"CLOUD_SYNC_INTERVAL_SECONDS": "often"}, "must be an integer"),
        ):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_environment_file(root, environment, **{**enabled, **overrides})
                with patch.dict(os.environ, {"APP_ENV": environment}, clear=True), self.assertRaisesRegex(
                    ValueError, message
                ):
                    load_config(root)

    def test_rejects_invalid_environment_boolean_and_port(self):
        """Invalid environment, boolean, or port values raise ValueError."""
        for environ in (
            {"APP_ENV": "staging"},
            {"APP_ENV": "development", "DEBUG": "sometimes"},
            {"APP_ENV": "development", "PORT": "70000"},
        ):
            with self.subTest(environ=environ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if environ.get("APP_ENV") == "development":
                    write_environment_file(
                        root,
                        "development",
                        **{
                            key: value
                            for key, value in environ.items()
                            if key != "APP_ENV"
                        },
                    )
                with patch.dict(os.environ, environ, clear=True), self.assertRaises(ValueError):
                    load_config(root)


if __name__ == "__main__":
    unittest.main()
