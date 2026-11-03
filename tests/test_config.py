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
    },
}


def write_environment_file(root, environment, **overrides):
    values = {**_ENV_FILE_VALUES[environment], **overrides}
    (root / f".env.{environment}").write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"
    )


class LoadConfigTestCase(unittest.TestCase):
    def test_loads_selected_dotenv_and_process_environment_overrides_it(self):
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.development").write_text("APP_ENV=development\n")
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError, "DEBUG must be set"
            ):
                load_config(root)

    def test_rejects_debug_or_test_seeding_in_production(self):
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment_file(root, "development")
            with patch.dict(
                os.environ,
                {"APP_ENV": "development", "SEED_TEST_DEVICE": "true"},
                clear=True,
            ), self.assertRaisesRegex(ValueError, "TEST_DEVICE_API_KEY"):
                load_config(root)

    def test_rejects_invalid_environment_boolean_and_port(self):
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
