"""HTTP interface for the IAM bounded context.

Exposes IAM HTTP routes and adapts request data for IAM application use cases.
"""
from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field

iam_api = APIBlueprint("iam_api", __name__)
IAM_TAG = Tag(name="IAM", description="Identity and Access Management")
SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]


class SignInRequest(BaseModel):
    """Body of the sign-in request sent by a node.

    Unknown fields are rejected and surrounding whitespace is stripped.

    Attributes:
        device_id: Identifier of the device that wants to authenticate.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="Identifier of the device.")


class SignInResponse(BaseModel):
    """Body returned when a device authenticates successfully.

    Attributes:
        device_id: Identifier of the authenticated device.
        device_type: Type of the device, such as ``ENVIRONMENTAL_DEVICE``.
        authenticated: Always ``True`` in a successful response.
    """

    device_id: str
    device_type: str
    authenticated: bool


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the failure.
    """

    error: str


def _node_api_key() -> str | None:
    """Return the API key of the node: the ``X-API-Key`` header, or else an ``Authorization: Bearer`` token.

    Nodes built on the Modest IoT Nano Framework send the key with its ``HttpGatewayClient``, which can only
    set the ``Authorization`` header. When both headers arrive, ``X-API-Key`` wins.

    Returns:
        The API key, or ``None`` when neither header carries one.
    """
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
def sign_in(body: SignInRequest):
    """Authenticate a registered device with its API key.

    Args:
        body: Validated request body with the ``device_id``.

    Returns:
        A tuple with the JSON body and the HTTP status: ``200`` with the
        authenticated device, or ``401`` when the API key is missing or the
        ``device_id``/API key pair is invalid.
    """
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
    """Register the IAM blueprint so its routes appear in the OpenAPI spec.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(iam_api)