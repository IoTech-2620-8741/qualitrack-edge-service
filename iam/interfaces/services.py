"""HTTP interface for the IAM bounded context.

This module exposes the REST endpoints of the Identity and Access Management
context. It currently offers the sign-in endpoint, which lets a registered
device verify its credentials (``device_id`` and API key).

Request bodies are validated by ``flask_openapi3`` through the typed ``body``
parameter of the view. Validation failures are translated into the project's
error format by the callback registered in ``app.py``.
"""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field

iam_api = APIBlueprint("iam_api", __name__)
IAM_TAG = Tag(name="IAM", description="Identity and Access Management")
SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]


class SignInRequest(BaseModel):
    """Body of the sign-in request.

    Attributes:
        device_id: Identifier of the device that wants to authenticate.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="Identifier of the device.")


class SignInResponse(BaseModel):
    """Body returned when the device is authenticated.

    Attributes:
        device_id: Identifier of the authenticated device.
        device_type: Type of the device, as registered in IAM.
        authenticated: Always ``True`` for a successful sign-in.
    """

    device_id: str
    device_type: str
    authenticated: bool


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the error.
    """

    error: str


def _node_api_key() -> str | None:
    """Extract the API key sent by the node.

    The key is read first from the ``X-API-Key`` header. If it is missing, the
    ``Authorization`` header is checked for a ``Bearer`` token.

    Returns:
        The API key, or ``None`` if the request does not carry a usable one.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


@iam_api.before_request
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


@iam_api.post(
    "/api/v1/auth/sign-in",
    summary="Authenticate a registered device",
    tags=[IAM_TAG],
    security=SECURITY,
    responses={200: SignInResponse, 400: ErrorResponse, 401: ErrorResponse, 415: ErrorResponse},
)
def sign_in(body: SignInRequest):
    """Authenticate a registered device with its API key."""
    # The API key is checked after the body, so an invalid body is always a 400.
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401

    device = current_app.extensions["iam_authentication_service"].authenticated_device(
        body.device_id, api_key
    )
    if device is None:
        return jsonify({"error": "Invalid device_id or API key"}), 401

    return jsonify({
        "device_id": device.device_id,
        "device_type": device.device_type.value,
        "authenticated": True,
    }), 200


def init_routes(app):
    """Register the IAM blueprint in the application.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(iam_api)