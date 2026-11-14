import os
import unittest
from unittest.mock import patch

from app import create_app, start_cloud_sync


class FakeScheduler:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True


class StartCloudSyncTestCase(unittest.TestCase):
    """Tests the start of the background synchronization by the serving process.

    In production, the thread always starts. In development, it starts only in
    the serving process of the Werkzeug reloader. When synchronization is
    disabled, nothing starts.
    """

    def setUp(self):
        self.app = create_app()
        self.scheduler = FakeScheduler()
        self.app.extensions["cloud_sync_scheduler"] = self.scheduler

    def test_the_production_server_starts_the_synchronization(self):
        """The production server starts the background synchronization."""
        self.app.config["DEBUG"] = False

        start_cloud_sync(self.app)

        self.assertTrue(self.scheduler.started)

    def test_the_development_reloader_starts_it_only_in_the_serving_process(self):
        """In development, only the serving process of the reloader starts it."""
        self.app.config["DEBUG"] = True
        with patch.dict(os.environ, {"WERKZEUG_RUN_MAIN": ""}):
            start_cloud_sync(self.app)
        self.assertFalse(self.scheduler.started)

        with patch.dict(os.environ, {"WERKZEUG_RUN_MAIN": "true"}):
            start_cloud_sync(self.app)
        self.assertTrue(self.scheduler.started)

    def test_nothing_starts_while_the_synchronization_is_disabled(self):
        """When synchronization is disabled, no scheduler starts."""
        del self.app.extensions["cloud_sync_scheduler"]

        start_cloud_sync(self.app)


class WsgiModuleTestCase(unittest.TestCase):
    """Tests the WSGI module exposed to Gunicorn."""

    def test_exposes_the_application_for_gunicorn(self):
        """The WSGI module exposes the app and does not start the scheduler."""
        import wsgi

        self.assertIn("tracking_api", wsgi.app.blueprints)
        self.assertNotIn("cloud_sync_scheduler", wsgi.app.extensions)


if __name__ == "__main__":
    unittest.main()
