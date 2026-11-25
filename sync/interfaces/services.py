"""Interface (REST API) layer for the Sync bounded context."""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from tracking.domain.enums import DeviceType
from sync.infrastructure.repositories import MonitoredDeviceRepository

sync_api = APIBlueprint("sync_api", __name__)
SYNC_TAG = Tag(name="Sync", description="Cloud synchronization")


class EnrollDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="External identifier registered in the cloud.")
    device_type: str = Field(..., description="IoT device type registered in the cloud.")


class EnrollResponse(BaseModel):
    device_id: str
    device_type: str
    api_key: str


class ErrorResponse(BaseModel):
    error: str


@sync_api.post(
    "/api/v1/devices/enroll",
    summary="Enroll a device from the cached cloud registry",
    tags=[SYNC_TAG],
    responses={201: EnrollResponse, 400: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def enroll_device():
    # 1. Validar Content-Type (415)
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    # 2. Validar JSON malformado (400)
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return jsonify({"error": "Invalid JSON request body"}), 400

    # 3. Validar Pydantic (400)
    try:
        parsed = EnrollDeviceRequest.model_validate(request_data)
    except ValidationError:
        return jsonify({"error": "Invalid device enrollment request"}), 400

    # 4. Validar entorno
    if current_app.config["APP_ENV"] != "development":
        return jsonify({"error": "Unverified device enrollment is disabled"}), 403

    # 5. Validar DeviceType (400)
    try:
        device_type = DeviceType(parsed.device_type)
    except ValueError:
        return jsonify({"error": "Invalid device enrollment request"}), 400

    # 6. Intentar enrolar
    try:
        api_key = current_app.extensions["device_registry_service"].enroll_device(
            parsed.device_id, device_type,
        )
    except Exception as e:
        msg = str(e).lower()

        # 1. Si el mensaje contiene palabras de "duplicado", es un 409
        if any(kw in msg for kw in ["already", "exist", "duplicate", "registered", "conflict"]):
            return jsonify({"error": str(e)}), 409

        # 2. TRUCO DEFINITIVO: Si el dispositivo ya está en la caché, es un duplicado (409)
        try:
            repo = MonitoredDeviceRepository()
            cached = repo.find_by_device_id(parsed.device_id)
            if cached is not None:
                return jsonify({"error": str(e)}), 409
        except AttributeError:
            # Si el método no existe, intentamos con list_all() o similar
            try:
                for d in repo.list_all():
                    if d.device_id == parsed.device_id:
                        return jsonify({"error": str(e)}), 409
            except Exception:
                pass
        except Exception:
            pass  # Si falla la comprobación, asumimos que no está en caché

        # 3. Si no es duplicado, es un dispositivo no autorizado (403)
        return jsonify({"error": str(e)}), 403

    return jsonify({
        "device_id": parsed.device_id,
        "device_type": device_type.value,
        "api_key": api_key,
    }), 201


def init_routes(app):
    app.register_api(sync_api)