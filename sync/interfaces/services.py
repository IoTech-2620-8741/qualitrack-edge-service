"""Interface (REST API) layer for the Sync bounded context.

Exposes a Flask Blueprint (``sync_api``) with the enrollment of the nodes the
cached cloud registry assigns to this Edge. This layer owns no domain logic.
"""
from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field
from tracking.domain.enums import DeviceType

sync_api = APIBlueprint("sync_api", __name__)
SYNC_TAG = Tag(name="Sync", description="Cloud synchronization")


class EnrollDeviceRequest(BaseModel):
    """Body of the request that enrolls a device in this Edge.

    Unknown fields are rejected and surrounding whitespace is stripped.

    Attributes:
        device_id: External identifier registered in the cloud.
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
        api_key: API key the node must send in later requests.
    """

    device_id: str
    device_type: str
    api_key: str


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the failure.
    """

    error: str


@sync_api.post(
    "/api/v1/devices/enroll",
    summary="Enroll a device from the cached cloud registry",
    tags=[SYNC_TAG],
    responses={201: EnrollResponse, 400: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def enroll_device(body: EnrollDeviceRequest):
    """Enroll a device from the cached cloud registry.

    Unverified enrollment is only allowed in the ``development`` environment.

    Args:
        body: Validated request body with the ``device_id`` and ``device_type``.

    Returns:
        A tuple with the JSON body and the HTTP status: ``201`` with the new
        API key; ``400`` for an unknown device type; ``403`` when enrollment is
        disabled or the device is not authorized; ``409`` for any other
        enrollment failure.
    """
    if current_app.config["APP_ENV"] != "development":
        return jsonify({"error": "Unverified device enrollment is disabled"}), 403

    try:
        device_type = DeviceType(body.device_type)
    except Exception:
        return jsonify({"error": "Invalid device enrollment request"}), 400

    try:
        api_key = current_app.extensions["device_registry_service"].enroll_device(
            body.device_id, device_type,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 403 if "not authorized" in str(e) else 409

    return jsonify({
        "device_id": body.device_id,
        "device_type": device_type.value,
        "api_key": api_key,
    }), 201


def init_routes(app):
    """Register the Sync blueprint so its route appears in the OpenAPI spec.

    Args:
        app: The ``OpenAPI`` application that receives the route.
    """
    app.register_api(sync_api)