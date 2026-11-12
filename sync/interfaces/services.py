"""Interface (REST API) layer for the Sync bounded context.

Exposes a Flask Blueprint (``sync_api``) with the enrollment of the nodes the
cached cloud registry assigns to this Edge. This layer owns no domain logic.
"""
from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sync.application.errors import DeviceAlreadyEnrolled, DeviceEnrollmentNotAllowed
from tracking.domain.enums import DeviceType

sync_api = Blueprint("sync_api", __name__)


class EnrollDeviceRequest(BaseModel):
    """Initial enrollment request submitted by an ESP32."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    device_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="External identifier registered in the cloud.",
    )

    device_type: DeviceType = Field(
        ...,
        description="IoT device type registered in the cloud.",
    )


@sync_api.route("/api/v1/devices/enroll", methods=["POST"])
def enroll_device():
    """Enroll an IoT device using the cached cloud registry.

    Development-only bootstrap flow.

    The ESP32 provides its device identifier and type.
    Sync verifies that the device is authorized by the
    cached cloud registry, then asks IAM to generate its API key.

    Responses:
        201: Device enrolled and API key issued.
        400: Invalid registration request.
        403: Enrollment disabled or device not authorized.
        409: Device already registered in local IAM.
        415: Unsupported request content type.
    """
    # Enrollment based on public identifiers must not be
    # exposed as an unrestricted production endpoint.
    if current_app.config["APP_ENV"] != "development":
        return jsonify({
            "error": "Unverified device enrollment is disabled"
        }), 403

    if not request.is_json:
        return jsonify({
            "error": "Content-Type must be application/json"
        }), 415

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({
            "error": "Invalid JSON request body"
        }), 400

    try:
        enrollment = EnrollDeviceRequest.model_validate(payload)
    except ValidationError:
        return jsonify({
            "error": "Invalid device enrollment request"
        }), 400

    try:
        api_key = current_app.extensions[
            "device_registry_service"
        ].enroll_device(
            enrollment.device_id,
            enrollment.device_type,
        )

    except DeviceEnrollmentNotAllowed:
        return jsonify({
            "error": "Device is not authorized for enrollment"
        }), 403

    except DeviceAlreadyEnrolled:
        return jsonify({
            "error": "Device is already registered"
        }), 409

    response = jsonify({
        "device_id": enrollment.device_id,
        "device_type": enrollment.device_type.value,
        "api_key": api_key,
    })

    response.headers["Cache-Control"] = "no-store"

    return response, 201
