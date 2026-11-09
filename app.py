"""Flask application entry point for the QualiTrack Edge Service.

This module wires together the Flask application, registers the IAM and Tracking
bounded-context Blueprints and the command groups, configures the SQLite
database, and composes the Tracking-to-IAM anti-corruption layer and the cloud
synchronization of the Tracking context.

Typical usage::

    python app.py
    # register a node
    flask --app app devices register ENV-001 ENVIRONMENTAL_DEVICE
    # read the device registry of the cloud now
    flask --app app cloud sync
"""

import logging
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask

from config import load_config
from iam.application.services import AuthApplicationService
from iam.interfaces.cli import devices_cli
from iam.interfaces.services import iam_api
from shared.infrastructure.cloud_session import CloudSession
from shared.infrastructure.cloud_sync_scheduler import CloudSyncScheduler
from shared.infrastructure.database import configure_database, db, init_db
from tracking.application.services import (
    DeviceRegistryApplicationService,
    EnvironmentalProfileApplicationService,
    TelemetryApplicationService,
    TelemetrySynchronizationApplicationService,
)
from tracking.infrastructure.cloud_profile_gateway import CloudProfileGateway
from tracking.infrastructure.cloud_registry_gateway import CloudDeviceRegistryGateway
from tracking.infrastructure.cloud_telemetry_gateway import CloudTelemetryGateway
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from tracking.interfaces.cli import cloud_cli
from tracking.interfaces.services import tracking_api

def create_app() -> Flask:
    """Create and initialize the Flask application."""
    app = Flask(__name__)
    app.config.from_mapping(load_config(Path(__file__).resolve().parent))
    configure_database(app.config["DATABASE_PATH"])
    init_db()

    app.register_blueprint(iam_api)
    app.register_blueprint(tracking_api)
    app.cli.add_command(devices_cli)
    app.cli.add_command(cloud_cli)
    iam_authentication_service = AuthApplicationService()
    app.extensions["iam_authentication_service"] = iam_authentication_service
    iam_anti_corruption_layer = IamAntiCorruptionLayer(iam_authentication_service)
    app.extensions["telemetry_service"] = TelemetryApplicationService(
        iam_anti_corruption_layer=iam_anti_corruption_layer
    )

    registry_gateway = profile_gateway = telemetry_gateway = None
    if app.config["CLOUD_SYNC_ENABLED"]:
        cloud_session = CloudSession(
            app.config["CLOUD_API_URL"],
            app.config["CLOUD_DEVICE_ID"],
            app.config["CLOUD_API_KEY"],
        )
        registry_gateway = CloudDeviceRegistryGateway(cloud_session)
        profile_gateway = CloudProfileGateway(cloud_session)
        telemetry_gateway = CloudTelemetryGateway(cloud_session)
    device_registry_service = DeviceRegistryApplicationService(registry_gateway, iam_anti_corruption_layer)
    environmental_profile_service = EnvironmentalProfileApplicationService(
        profile_gateway, iam_anti_corruption_layer
    )
    telemetry_synchronization_service = TelemetrySynchronizationApplicationService(
        telemetry_gateway,
        synced_retention=timedelta(days=app.config["SYNCED_RETENTION_DAYS"]),
        rejected_retention=timedelta(days=app.config["REJECTED_RETENTION_DAYS"]),
    )
    app.extensions["device_registry_service"] = device_registry_service
    app.extensions["environmental_profile_service"] = environmental_profile_service
    app.extensions["telemetry_synchronization_service"] = telemetry_synchronization_service
    if registry_gateway is not None:
        # The registry goes first: it gives the cloud route of each node.
        app.extensions["cloud_sync_scheduler"] = CloudSyncScheduler(
            app.config["CLOUD_SYNC_INTERVAL_SECONDS"],
            [
                ("device registry", device_registry_service.synchronize_registry),
                ("environmental profiles", environmental_profile_service.synchronize_profiles),
                ("pending telemetry", telemetry_synchronization_service.synchronize_pending),
                ("telemetry purge", telemetry_synchronization_service.purge),
            ],
        )

    if app.config["SEED_TEST_DEVICE"]:
        iam_authentication_service.get_or_create_test_device(
            app.config["TEST_DEVICE_ID"],
            app.config["TEST_DEVICE_API_KEY"],
        )

    @app.teardown_appcontext
    def close_database(_exception: BaseException | None) -> None:
        if not db.is_closed():
            db.close()

    return app


def start_cloud_sync(app: Flask) -> None:
    """Start the background cloud synchronization when it is enabled.

    It runs only in the process that serves requests, never in ``flask``
    commands, and only once under the development reloader.
    """
    scheduler = app.extensions.get("cloud_sync_scheduler")
    if scheduler is None:
        return
    if app.config["DEBUG"] and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    scheduler.start()


app = create_app()

if __name__ == "__main__":
    if app.config["APP_ENV"] != "development":
        raise RuntimeError(
            "The built-in Flask server is development-only; use a production WSGI server"
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    start_cloud_sync(app)
    app.run(
        debug=app.config["DEBUG"],
        host=app.config["HOST"],
        port=app.config["PORT"],
    )
