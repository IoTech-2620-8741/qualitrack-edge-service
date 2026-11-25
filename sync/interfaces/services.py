"""Interface (REST API) layer for the Sync bounded context.

This module exposes the REST endpoints of the Sync context. It currently offers
the enrollment of a device from the cached cloud registry, which returns the API
key the node must use in later requests.

Request bodies are validated by ``flask_openapi3`` through the typed ``body``
parameter of the view. Validation failures are translated into the project's
error format by the callback registered in ``app.py``.
"""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field
from tracking.domain.enums import DeviceType
from sync.infrastructure.repositories import MonitoredDeviceRepository

sync_api = APIBlueprint("sync_api", __name__)
SYNC_TAG = Tag(name="Sync", description="Cloud synchronization")


class EnrollDeviceRequest(BaseModel):
    """Body of the device enrollment request.

    Attributes:
        device_id: External identifier of the device registered in the cloud.
        device_type: IoT device type registered in the cloud.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="External identifier registered in the cloud.")
    device_type: str = Field(..., description="IoT device type registered in the cloud.")


class EnrollResponse(BaseModel):
    """Body returned when a device is enrolled.

    Attributes:
        device_id: Identifier of the enrolled device.
        device_type: Type of the enrolled device.
        api_key: API key generated for the device.
    """

    device_id: str
    device_type: str
    api_key: str


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the error.
    """

    error: str


@sync_api.before_request
def require_json_content_type():
    """Reject POST requests whose content type is not JSON.

    It runs before the body validation of ``flask_openapi3``, so a wrong
    ``Content-Type`` is answered with 415 instead of a validation error.

    Returns:
        A 415 error response when the request is not JSON, otherwise ``None``
        so the request continues normally.
    """
    if request.method == "POST" and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415


@sync_api.post(
    "/api/v1/devices/enroll",
    summary="Enroll a device from the cached cloud registry",
    tags=[SYNC_TAG],
    responses={201: EnrollResponse, 400: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def enroll_device(body: EnrollDeviceRequest):
    """Enroll a device that exists in the cached cloud registry.

    Only available in the development environment. Returns 201 with the new
    API key, 400 for an unknown device type, 403 when enrollment is disabled
    or the device is not authorized, and 409 when it is already enrolled.
    """
    # Enrollment without verification is only allowed in development.
    if current_app.config["APP_ENV"] != "development":
        return jsonify({"error": "Unverified device enrollment is disabled"}), 403

    # The device type arrives as free text; it must match a known DeviceType.
    try:
        device_type = DeviceType(body.device_type)
    except ValueError:
        return jsonify({"error": "Invalid device enrollment request"}), 400

    try:
        api_key = current_app.extensions["device_registry_service"].enroll_device(
            body.device_id, device_type,
        )
    except Exception as e:
        msg = str(e).lower()

        # The error message says the device already exists: duplicate (409).
        if any(kw in msg for kw in ["already", "exist", "duplicate", "registered", "conflict"]):
            return jsonify({"error": str(e)}), 409

        # The device is already in the local cache: also a duplicate (409).
        repo = MonitoredDeviceRepository()
        try:
            if repo.find_by_device_id(body.device_id) is not None:
                return jsonify({"error": str(e)}), 409
        except Exception:
            # If the cache check fails, assume the device is not cached.
            pass

        # Any other failure means the device is not authorized (403).
        return jsonify({"error": str(e)}), 403

    return jsonify({
        "device_id": body.device_id,
        "device_type": device_type.value,
        "api_key": api_key,
    }), 201


def init_routes(app):
    """Register the Sync blueprint in the application.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(sync_api)