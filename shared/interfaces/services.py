"""Interface (REST API) layer of the health of the Edge (ADR-0017).

Exposes an ``APIBlueprint`` (``health_api``) with ``GET /api/v1/health``, which
Docker calls to know whether the container works and a technician can open
from the local network. It needs no API key and reveals no identifiers, keys
or error messages. This layer owns no logic: it maps the report of
:class:`~shared.infrastructure.health.EdgeHealthCheck` to JSON.
"""

from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from shared.infrastructure.health import CloudSyncHealth, HealthStatus

health_api = APIBlueprint("health_api", __name__)
SHARED_TAG = Tag(name="Shared", description="Shared infrastructure")


class CloudSyncResponse(BaseModel):
    """State of the background cloud synchronization.

    Only ``enabled`` is present while the synchronization is disabled.

    Attributes:
        enabled: Whether the synchronization is enabled.
        running: Whether the synchronization thread is running.
        last_cycle_at: ISO 8601 UTC time of the last synchronization cycle.
        failed_tasks: Tasks that failed in the last cycle.
        behind: Whether the synchronization fell behind its interval.
    """

    enabled: bool
    running: bool | None = None
    last_cycle_at: str | None = Field(None, description="ISO 8601 UTC time of the last sync cycle.")
    failed_tasks: list[str] | None = None
    behind: bool | None = None


class HealthResponse(BaseModel):
    """Body of the health report of the Edge.

    Attributes:
        status: ``ok``, ``degraded`` or ``unavailable``.
        database: ``ok`` or ``unavailable``.
        cloud_sync: State of the background cloud synchronization.
    """

    status: str = Field(..., description="'ok', 'degraded' or 'unavailable'.")
    database: str = Field(..., description="'ok' or 'unavailable'.")
    cloud_sync: CloudSyncResponse


def _iso(value) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _cloud_sync_resource(cloud_sync: CloudSyncHealth) -> dict:
    if not cloud_sync.enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "running": cloud_sync.running,
        "last_cycle_at": _iso(cloud_sync.last_cycle_at),
        "failed_tasks": list(cloud_sync.failed_tasks),
        "behind": cloud_sync.behind,
    }


@health_api.get(
    "/api/v1/health",
    summary="Check Edge health",
    tags=[SHARED_TAG],
    responses={200: HealthResponse, 503: HealthResponse},
)
def check_health():
    """Report whether the Edge can serve the nodes and how the synchronization goes.

    Responses: ``200`` with ``status`` ``ok``, or ``degraded`` while the cloud
    synchronization fails, falls behind or stopped (the Edge keeps receiving
    and storing the telemetry); ``503`` with ``status`` ``unavailable`` when
    the database cannot be read.
    """
    report = current_app.extensions["edge_health_check"].report()
    response = jsonify({
        "status": report.status.value,
        "database": "ok" if report.database_available else "unavailable",
        "cloud_sync": _cloud_sync_resource(report.cloud_sync),
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 503 if report.status is HealthStatus.UNAVAILABLE else 200


def init_routes(app):
    """Register the health blueprint in the application.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(health_api)
