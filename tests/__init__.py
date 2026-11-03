"""Test environment shared by every test module.

``app`` builds the application when it is imported, so the settings and the
temporary database are set before any test module imports it.
"""
import os
import tempfile

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
