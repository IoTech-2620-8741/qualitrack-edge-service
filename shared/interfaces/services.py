"""Interface (REST API) layer of the health of the Edge (ADR-0017).

Exposes a Flask Blueprint (``health_api``) with ``GET /api/v1/health``, which
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
    """State of the cloud synchronization inside the health report.

    Only ``enabled`` is present when the synchronization is turned off.

    Attributes:
        enabled: Whether the cloud synchronization is configured.
        running: Whether the scheduler is running, if enabled.
        last_cycle_at: ISO 8601 UTC time of the last sync cycle, or ``None``.
        failed_tasks: Names of the tasks that failed in the last cycle.
        behind: Whether the synchronization is behind schedule.
    """

    enabled: bool
    running: bool | None = None
    last_cycle_at: str | None = Field(None, description="ISO 8601 UTC time of the last sync cycle.")
    failed_tasks: list[str] | None = None
    behind: bool | None = None


class HealthResponse(BaseModel):
    """Body of the health report of the Edge.

    Attributes:
        status: Overall status of the Edge.
        database: ``"ok"`` or ``"unavailable"``.
        cloud_sync: State of the cloud synchronization.
    """

    status: str = Field(..., description="Overall status of the Edge.")
    database: str = Field(..., description="'ok' or 'unavailable'.")
    cloud_sync: CloudSyncResponse


def _iso(value) -> str | None:
    """Format a datetime as ISO 8601 with a ``Z`` suffix for UTC.

    Args:
        value: A timezone-aware datetime, or ``None``.

    Returns:
        The formatted string with second precision, or ``None`` if ``value`` is ``None``.
    """
    return None if value is None else value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _cloud_sync_resource(cloud_sync: CloudSyncHealth) -> dict:
    """Map the cloud synchronization health to its JSON representation.

    Args:
        cloud_sync: Health of the cloud synchronization.

    Returns:
        A dict with only ``enabled`` when the synchronization is off, or the
        full state when it is on.
    """
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
def health_check():
    """Report whether the Edge works.

    Returns:
        A tuple with the JSON health report and the HTTP status: ``503`` when
        the Edge is unavailable, ``200`` otherwise.
    """
    report = current_app.extensions["edge_health_check"].report()
    return jsonify({
        "status": report.status.value,
        "database": "ok" if report.database_available else "unavailable",
        "cloud_sync": _cloud_sync_resource(report.cloud_sync),
    }), 503 if report.status is HealthStatus.UNAVAILABLE else 200


def init_routes(app):
    """Register the health blueprint so its route appears in the OpenAPI spec.

    Args:
        app: The ``OpenAPI`` application that receives the route.
    """
    app.register_api(health_api)