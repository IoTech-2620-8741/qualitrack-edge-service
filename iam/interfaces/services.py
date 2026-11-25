"""HTTP interface for the IAM bounded context."""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field, ValidationError

iam_api = APIBlueprint("iam_api", __name__)
IAM_TAG = Tag(name="IAM", description="Identity and Access Management")
SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="Identifier of the device.")


class SignInResponse(BaseModel):
    device_id: str
    device_type: str
    authenticated: bool


class ErrorResponse(BaseModel):
    error: str


def _node_api_key() -> str | None:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


@iam_api.post(
    "/api/v1/auth/sign-in",
    summary="Authenticate a registered device",
    tags=[IAM_TAG],
    security=SECURITY,
    responses={200: SignInResponse, 400: ErrorResponse, 401: ErrorResponse, 415: ErrorResponse},
)
def sign_in():
    """Authenticate a registered device with its API key."""
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"error": "Invalid JSON request body"}), 400

    try:
        parsed_body = SignInRequest.model_validate(request_data)
    except ValidationError:
        return jsonify({"error": "Invalid request body"}), 400

    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401

    device = current_app.extensions["iam_authentication_service"].authenticated_device(
        parsed_body.device_id, api_key
    )
    if device is None:
        return jsonify({"error": "Invalid device_id or API key"}), 401

    return jsonify({
        "device_id": device.device_id,
        "device_type": device.device_type.value,
        "authenticated": True,
    }), 200


def init_routes(app):
    app.register_api(iam_api)