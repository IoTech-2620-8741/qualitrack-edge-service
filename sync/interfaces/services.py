"""Interface (REST API) layer for the Sync bounded context.

Exposes an ``APIBlueprint`` (``sync_api``) with the enrollment of the nodes the
cached cloud registry assigns to this Edge (development only, ADR-0014). This
layer owns no domain logic.

Request bodies are validated by ``flask_openapi3`` through the typed ``body``
parameter of the view. Validation failures are translated into the project's
error format by the callback registered in ``app.py``.
"""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field

from sync.application.errors import DeviceAlreadyEnrolled, DeviceEnrollmentNotAllowed
from tracking.domain.enums import DeviceType

sync_api = APIBlueprint("sync_api", __name__)
SYNC_TAG = Tag(name="Sync", description="Cloud synchronization")


class EnrollDeviceRequest(BaseModel):
    """Body of the device enrollment request.

    Attributes:
        device_id: External identifier of the device registered in the cloud.
        device_type: IoT device type registered in the cloud.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, max_length=50, description="External identifier registered in the cloud.")
    device_type: DeviceType = Field(..., description="IoT device type registered in the cloud.")


class EnrollResponse(BaseModel):
    """Body returned when a device is enrolled.

    Attributes:
        device_id: Identifier of the enrolled device.
        device_type: Type of the enrolled device.
        api_key: API key generated for the device.
    """

    device_id: str
    device_type: DeviceType
    api_key: str


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the error.
    """

    error: str


@sync_api.before_request
def reject_unsupported_requests():
    """Reject enrollment outside development and requests that are not JSON.

    It runs before the body validation of ``flask_openapi3``, so outside
    development every request is answered with 403 whatever its body, and a
    wrong ``Content-Type`` is answered with 415 instead of a validation error.

    Returns:
        A 403 or 415 error response, otherwise ``None`` so the request
        continues normally.
    """
    # Enrollment based on public identifiers must not be exposed as an
    # unrestricted production endpoint.
    if current_app.config["APP_ENV"] != "development":
        return jsonify({"error": "Unverified device enrollment is disabled"}), 403
    if request.method == "POST" and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415
    return None


@sync_api.post(
    "/api/v1/devices/enroll",
    summary="Enroll a device from the cached cloud registry",
    tags=[SYNC_TAG],
    responses={201: EnrollResponse, 400: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def enroll_device(body: EnrollDeviceRequest):
    """Enroll an IoT device using the cached cloud registry (development only).

    The ESP32 provides its device identifier and type. Sync verifies that the
    cached cloud registry authorizes the device, then asks IAM to generate its
    API key. Responses: ``201`` with the API key, ``400`` for an invalid
    request, ``403`` when enrollment is disabled or the device is not
    authorized, ``409`` when local IAM already has the device and ``415`` for
    a non-JSON request.
    """
    try:
        api_key = current_app.extensions["device_registry_service"].enroll_device(
            body.device_id,
            body.device_type,
        )
    except DeviceEnrollmentNotAllowed:
        return jsonify({"error": "Device is not authorized for enrollment"}), 403
    except DeviceAlreadyEnrolled:
        return jsonify({"error": "Device is already registered"}), 409

    response = jsonify({
        "device_id": body.device_id,
        "device_type": body.device_type.value,
        "api_key": api_key,
    })
    # The response carries the API key of the node: no cache may keep it.
    response.headers["Cache-Control"] = "no-store"
    return response, 201


def init_routes(app):
    """Register the Sync blueprint in the application.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(sync_api)
