"""HTTP interface for the IAM bounded context.

Exposes IAM HTTP routes and adapts request data for IAM application use cases.
"""
from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

iam_api = Blueprint("iam_api", __name__)


class SignInRequest(BaseModel):
    """Request contract for the sign-in of one registered device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, description="Identifier of the device.")


@iam_api.route("/api/v1/auth/sign-in", methods=["POST"])
def sign_in_device():
    """Sign in an environmental device or a container monitor (TS53, TS54).

    The device identity is supplied in the JSON body and its API key in the
    ``X-API-Key`` header. This endpoint reports the authentication outcome and
    the type of the device; device registration, credential rotation and
    revocation are not exposed over HTTP.

    Responses:
        ``200`` when the registered device is active and the credential
        matches; ``400`` for invalid JSON or a malformed payload; ``401`` for
        missing or rejected credentials; ``415`` for a non-JSON request.
    """
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    request_data = request.get_json(silent=True)
    if request_data is None:
        return jsonify({"error": "Invalid JSON request body"}), 400

    try:
        sign_in_request = SignInRequest.model_validate(request_data)
    except ValidationError:
        return jsonify({"error": "Invalid sign-in request"}), 400

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401

    device = current_app.extensions["iam_authentication_service"].authenticated_device(
        sign_in_request.device_id, api_key
    )
    if device is None:
        return jsonify({"error": "Invalid device_id or API key"}), 401

    return jsonify({
        "device_id": device.device_id,
        "device_type": device.device_type.value,
        "authenticated": True,
    }), 200
