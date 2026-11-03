"""Test environment shared by every test module.

``app`` builds the application when it is imported, so the settings and the
temporary database are set before any test module imports it.
"""
import atexit
import os
import shutil
import tempfile

_test_directory = tempfile.mkdtemp(prefix="qualitrack-edge-test-")
os.environ.update({
    "APP_ENV": "development",
    "DEBUG": "false",
    "DATABASE_PATH": os.path.join(_test_directory, "test.db"),
    "SEED_TEST_DEVICE": "true",
    "TEST_DEVICE_ID": "ENV-001",
    "TEST_DEVICE_API_KEY": "test-only-key",
    "HOST": "127.0.0.1",
    "PORT": "5000",
    "CLOUD_SYNC_ENABLED": "false",
})


@atexit.register
def _remove_test_directory() -> None:
    # Windows cannot delete the SQLite file while a connection keeps it open.
    from shared.infrastructure.database import db

    if not db.is_closed():
        db.close()
    shutil.rmtree(_test_directory, ignore_errors=True)
