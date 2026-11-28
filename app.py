"""Flask application entry point for the QualiTrack Edge Service.

This module wires together the ``flask_openapi3`` application, registers the IAM, Tracking and
Sync bounded-context blueprints, the health blueprint and the command groups, configures the SQLite
database, and composes the anti-corruption layers of Tracking and Sync with IAM and the
cloud synchronization of the Sync context. The OpenAPI document and its viewers are served
under ``/openapi/``.

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

from flask import jsonify, request
from flask_openapi3 import Info, OpenAPI

from config import load_config
from iam.application.services import AuthApplicationService
from iam.interfaces.cli import devices_cli
from iam.interfaces.services import init_routes as init_iam_routes
from shared.infrastructure.cloud_session import CloudSession
from shared.infrastructure.cloud_sync_scheduler import CloudSyncScheduler
from shared.infrastructure.database import configure_database, database_available, db, init_db
from shared.infrastructure.health import EdgeHealthCheck
from shared.interfaces.services import init_routes as init_health_routes
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
from sync.interfaces.services import init_routes as init_sync_routes
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
from tracking.interfaces.services import init_routes as init_tracking_routes


def _json_error(message: str, status: int):
    """Build a JSON error response with the given status code.

    The status is set on the response itself because ``flask_openapi3`` passes
    the callback result to ``abort``, which does not accept a ``(body, status)``
    tuple.

    Args:
        message: Text to return in the ``error`` field.
        status: HTTP status code of the response.

    Returns:
        A JSON ``Response`` with the status already applied.
    """
    response = jsonify({"error": message})
    response.status_code = status
    return response


def _validation_error_callback(_error):
    """Translate a request validation failure into the project's error format.

    ``flask_openapi3`` answers validation errors with 422 by default. This
    callback keeps the contract of each bounded context instead: telemetry
    routes answer 401 when ``device_id`` is missing and 400 otherwise, device
    enrollment answers 400, and any other route answers a generic 400.

    Args:
        _error: The pydantic ``ValidationError``; unused because the message
            depends on the route, not on the error details.

    Returns:
        A JSON ``Response`` with the matching status code.
    """
    path = request.path
    payload = request.get_json(silent=True)

    # Tracking: a missing device_id is an authentication problem (401),
    # any other invalid field is a malformed request (400).
    if path.startswith(("/api/v1/monitoring/", "/api/v1/control/")):
        if isinstance(payload, dict) and not payload.get("device_id"):
            return _json_error("Missing device_id", 401)
        return _json_error("Invalid telemetry request", 400)

    # Sync: device enrollment.
    if path == "/api/v1/devices/enroll":
        return _json_error("Invalid device enrollment request", 400)

    # IAM and any other route.
    return _json_error("Invalid request body", 400)


def create_app() -> OpenAPI:
    """Build and configure the Edge application.

    Creates the ``OpenAPI`` app with its security schemes, loads the
    configuration, prepares the database, composes the application services
    and the anti-corruption layers of IAM, Tracking and Sync, optionally builds
    the cloud gateways and the synchronization scheduler, seeds the test
    device when configured, registers the routes of every bounded context and
    adds the command groups.

    Returns:
        The configured ``OpenAPI`` application.
    """
    info = Info(title="QualiTrack Edge Service API", version="1.1.1")
    security_schemes = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "BearerAuth": {"type": "http", "scheme": "bearer"},
    }
    app = OpenAPI(
        __name__,
        info=info,
        security_schemes=security_schemes,
        # The callback answers every validation failure with 400 (or 401 for a
        # telemetry request without device_id), never with the default 422.
        validation_error_status=400,
        validation_error_callback=_validation_error_callback,
        doc_ui={
            "swagger": True,
            "redoc": True,
            "rapidoc": True,
            "rapipdf": True,
            "scalar": True,
            "elements": True,
        },
    )
    app.config.from_mapping(load_config(Path(__file__).resolve().parent))
    configure_database(app.config["DATABASE_PATH"])
    init_db()

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

    init_iam_routes(app)
    init_tracking_routes(app)
    init_sync_routes(app)
    init_health_routes(app)

    app.cli.add_command(devices_cli)
    app.cli.add_command(cloud_cli)

    @app.teardown_appcontext
    def close_database(_exception: BaseException | None) -> None:
        """Close the database connection when the application context ends.

        Args:
            _exception: Exception that ended the context, if any; unused.
        """
        if not db.is_closed():
            db.close()

    return app


def start_cloud_sync(app: OpenAPI) -> None:
    """Start the cloud synchronization scheduler, if there is one.

    Does nothing when the synchronization is disabled. In debug mode it starts
    only in the reloader child process, so the scheduler does not run twice.

    Args:
        app: The application that holds the ``cloud_sync_scheduler`` extension.
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
