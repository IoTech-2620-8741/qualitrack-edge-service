"""Interface (REST API) layer of the health of the Edge (ADR-0017).

Exposes a Flask Blueprint (``health_api``) with ``GET /api/v1/health``, which
Docker calls to know whether the container works and a technician can open
from the local network. It needs no API key and reveals no identifiers, keys
or error messages. This layer owns no logic: it maps the report of
:class:`~shared.infrastructure.health.EdgeHealthCheck` to JSON.
"""
from flask import Blueprint, current_app, jsonify

from shared.infrastructure.health import CloudSyncHealth, HealthStatus

health_api = Blueprint("health_api", __name__)


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


@health_api.route("/api/v1/health", methods=["GET"])
def check_health():
    """Report whether the Edge can serve the nodes and how the synchronization goes.

    Responses:
        ``200`` with ``status`` ``ok``, or ``degraded`` while the cloud
        synchronization fails, falls behind or stopped: the Edge keeps
        receiving and storing the telemetry. ``503`` with ``status``
        ``unavailable`` when the database cannot be read.
    """
    report = current_app.extensions["edge_health_check"].report()
    response = jsonify({
        "status": report.status.value,
        "database": "ok" if report.database_available else "unavailable",
        "cloud_sync": _cloud_sync_resource(report.cloud_sync),
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 503 if report.status is HealthStatus.UNAVAILABLE else 200
