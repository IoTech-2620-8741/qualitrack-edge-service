"""Flask application entry point for the QualiTrack Edge Service.

This module wires together the Flask application, registers the IAM, Tracking and Sync
bounded-context Blueprints, the health Blueprint and the command groups, configures the SQLite
database, and composes the anti-corruption layers of Tracking and Sync with IAM and the
cloud synchronization of the Sync context.

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
from shared.infrastructure.database import configure_database, database_available, db, init_db
from shared.infrastructure.health import EdgeHealthCheck
from shared.interfaces.services import health_api
from sync.application.services import (
    DeviceRegistryApplicationService,
    ProfileSynchronizationApplicationService,
    TelemetrySynchronizationApplicationService,
)
from sync.infrastructure.cloud_profile_gateway import CloudProfileGateway
from sync.infrastructure.cloud_registry_gateway import CloudDeviceRegistryGateway
from sync.infrastructure.cloud_telemetry_gateway import CloudTelemetryGateway
from sync.infrastructure.repositories import MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl
from sync.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer as SyncIamAntiCorruptionLayer
from sync.interfaces.cli import cloud_cli
from sync.interfaces.services import sync_api
from tracking.application.services import (
    EnvironmentalProfileApplicationService,
    TelemetryApplicationService,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from tracking.interfaces.services import tracking_api

def create_app() -> Flask:
    """Create and initialize the Flask application."""
    app = Flask(__name__)
    app.config.from_mapping(load_config(Path(__file__).resolve().parent))
    configure_database(app.config["DATABASE_PATH"])
    init_db()

    app.register_blueprint(iam_api)
    app.register_blueprint(tracking_api)
    app.register_blueprint(sync_api)
    app.register_blueprint(health_api)
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
    # Sync is a conformist of Tracking (ADR-0015): it receives the repositories
    # of Tracking from here instead of building the implementations of another context.
    monitored_device_repository = MonitoredDeviceRepositoryImpl()
    device_registry_service = DeviceRegistryApplicationService(
        registry_gateway, SyncIamAntiCorruptionLayer(iam_authentication_service), monitored_device_repository
    )
    environmental_profile_service = EnvironmentalProfileApplicationService(iam_anti_corruption_layer)
    profile_synchronization_service = ProfileSynchronizationApplicationService(
        profile_gateway, monitored_device_repository, EnvironmentalProfileRepositoryImpl()
    )
    telemetry_synchronization_service = TelemetrySynchronizationApplicationService(
        telemetry_gateway,
        monitored_device_repository,
        TelemetryReadingRepositoryImpl(),
        ActuationEventRepositoryImpl(),
        synced_retention=timedelta(days=app.config["SYNCED_RETENTION_DAYS"]),
        rejected_retention=timedelta(days=app.config["REJECTED_RETENTION_DAYS"]),
    )
    app.extensions["device_registry_service"] = device_registry_service
    app.extensions["environmental_profile_service"] = environmental_profile_service
    app.extensions["profile_synchronization_service"] = profile_synchronization_service
    app.extensions["telemetry_synchronization_service"] = telemetry_synchronization_service
    if registry_gateway is not None:
        # The registry goes first: it gives the cloud route of each node.
        app.extensions["cloud_sync_scheduler"] = CloudSyncScheduler(
            app.config["CLOUD_SYNC_INTERVAL_SECONDS"],
            [
                ("device registry", device_registry_service.synchronize_registry),
                ("environmental profiles", profile_synchronization_service.synchronize_profiles),
                ("pending telemetry", telemetry_synchronization_service.synchronize_pending),
                ("telemetry purge", telemetry_synchronization_service.purge),
            ],
        )
    app.extensions["edge_health_check"] = EdgeHealthCheck(
        database_available, app.extensions.get("cloud_sync_scheduler")
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