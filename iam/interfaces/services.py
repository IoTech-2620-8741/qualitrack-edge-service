"""HTTP interface for the IAM bounded context.

Exposes IAM HTTP routes and adapts request data for IAM application use cases.
"""
from flask import current_app, jsonify, request
from flask_openapi import Tag
from pydantic import BaseModel, ConfigDict, Field, ValidationError

IAM_TAG = Tag(name="IAM", description="Identity and Access Management")


def _node_api_key() -> str | None:
    """Return the API key of the node: the ``X-API-Key`` header, or else an ``Authorization: Bearer`` token.

    Nodes built on the Modest IoT Nano Framework send the key with its ``HttpGatewayClient``, which can only
    set the ``Authorization`` header. When both headers arrive, ``X-API-Key`` wins.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


class SignInRequest(BaseModel):
    """Request contract for the sign-in of one registered device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, description="Identifier of the device.")

def sign_in_device():
    """Sign in an environmental device or a container monitor (TS53, TS54).

    The device identity is supplied in the JSON body and its API key in the
    ``X-API-Key`` header or as an ``Authorization: Bearer`` token. This endpoint reports the authentication outcome and
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

    api_key = _node_api_key()
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

def init_routes(app):
    @app.post("/api/v1/auth/sign-in", summary="Authenticate a registered device", tags=[Tag(name="IAM")])
    def api_sign_in():
        return sign_in_device()
