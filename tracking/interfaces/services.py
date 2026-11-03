"""Interface (REST API) layer for the Tracking bounded context.

Exposes a Flask Blueprint (``tracking_api``) that receives the readings and
actions of the nodes (TS55, TS56, TS57), translates them into calls to the
application service and maps the results back to JSON responses. This layer
owns no domain logic.

Each request carries the ``device_id`` of the node in the body and its API key
in the ``X-API-Key`` header. Responses: ``201`` when something new is stored,
``200`` for an identical retry, ``400`` for invalid data, ``401`` for missing
or rejected credentials, ``403`` when the node type does not send that
telemetry, ``409`` when a retry changes a stored observation and ``415`` for a
non-JSON request.
"""
from flask import Blueprint, current_app, jsonify, request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from tracking.application.errors import DeviceAuthenticationFailed, DeviceTypeNotAllowed
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
)
from tracking.domain.errors import TelemetryConflict

tracking_api = Blueprint("tracking_api", __name__)


class _TelemetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, description="Identifier of the node.")
    profile_version: int | None = Field(None, ge=0, description="Profile version the node applied.")


class EnvironmentReadingRequest(_TelemetryRequest):
    """Metrics the environmental device measured at one moment (TS55)."""

    measured_at: AwareDatetime = Field(..., description="ISO 8601 measurement time with offset.")
    air_quality: float | None = Field(None, description="Air quality in ppm.")
    motion: bool | None = Field(None, description="Whether motion was detected.")

    def values(self) -> dict[Metric, float]:
        values = {}
        if self.air_quality is not None:
            values[Metric.AIR_QUALITY] = self.air_quality
        if self.motion is not None:
            values[Metric.MOTION] = 1.0 if self.motion else 0.0
        return values


class ContainerReadingRequest(_TelemetryRequest):
    """Metrics the container monitor measured at one moment (TS56)."""

    measured_at: AwareDatetime = Field(..., description="ISO 8601 measurement time with offset.")
    temperature: float | None = Field(None, description="Temperature in °C.")
    humidity: float | None = Field(None, description="Relative humidity in %.")
    luminosity: float | None = Field(None, description="Luminosity in lux.")
    rfid_tag: str | None = Field(None, min_length=1, max_length=120, description="RFID tag read.")

    def values(self) -> dict[Metric, float | str]:
        candidates = {
            Metric.TEMPERATURE: self.temperature,
            Metric.HUMIDITY: self.humidity,
            Metric.LUMINOSITY: self.luminosity,
            Metric.RFID_TAG: self.rfid_tag,
        }
        return {metric: value for metric, value in candidates.items() if value is not None}


class ActuationEventRequest(_TelemetryRequest):
    """Action the container monitor executed (TS57)."""

    action: ActuationAction
    trigger_metric: Metric | None = Field(None, description="Metric that caused the action.")
    trigger_state: EnvironmentalState | None = Field(None, description="State that caused the action.")
    result: ActuationResult = ActuationResult.EXECUTED
    occurred_at: AwareDatetime = Field(..., description="ISO 8601 time of the action with offset.")


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _reading_resource(reading) -> dict:
    return {
        "id": reading.id,
        "metric": reading.metric.value,
        "value": reading.value,
        "text_value": reading.text_value,
        "sync_status": reading.sync_status.value,
    }


def _event_resource(event) -> dict:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "action": event.action.value,
        "trigger_metric": None if event.trigger_metric is None else event.trigger_metric.value,
        "trigger_state": None if event.trigger_state is None else event.trigger_state.value,
        "result": event.result.value,
        "occurred_at": _iso(event.occurred_at.value),
        "profile_version": None if event.profile_version is None else event.profile_version.value,
        "sync_status": event.sync_status.value,
    }


def _parse(request_model):
    """Validate the JSON body and the API key; return the request or an error response."""
    if not request.is_json:
        return None, None, (jsonify({"error": "Content-Type must be application/json"}), 415)
    request_data = request.get_json(silent=True)
    if not isinstance(request_data, dict):
        return None, None, (jsonify({"error": "Invalid JSON request body"}), 400)
    if not request_data.get("device_id"):
        return None, None, (jsonify({"error": "Missing device_id"}), 401)
    try:
        parsed = request_model.model_validate(request_data)
    except ValidationError:
        return None, None, (jsonify({"error": "Invalid telemetry request"}), 400)
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return None, None, (jsonify({"error": "Missing X-API-Key"}), 401)
    return parsed, api_key, None


def _record_readings(request_model, device_type: DeviceType):
    parsed, api_key, error = _parse(request_model)
    if error:
        return error
    try:
        result = current_app.extensions["telemetry_service"].record_readings(
            parsed.device_id,
            api_key,
            device_type,
            parsed.measured_at,
            parsed.profile_version,
            parsed.values(),
        )
    except DeviceAuthenticationFailed:
        return jsonify({"error": "Invalid device_id or API key"}), 401
    except DeviceTypeNotAllowed:
        return jsonify({"error": f"Only {device_type.value} nodes send these readings"}), 403
    except TelemetryConflict:
        return jsonify({"error": "These readings were already sent with different values"}), 409
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({
        "device_id": parsed.device_id,
        "measured_at": _iso(result.readings[0].measured_at.value),
        "profile_version": parsed.profile_version,
        "readings": [_reading_resource(reading) for reading in result.readings],
    }), 201 if result.created else 200


@tracking_api.route("/api/v1/monitoring/environment-readings", methods=["POST"])
def create_environment_readings():
    """Receive the air quality and motion the environmental device measured (TS55)."""
    return _record_readings(EnvironmentReadingRequest, DeviceType.ENVIRONMENTAL_DEVICE)


@tracking_api.route("/api/v1/monitoring/container-readings", methods=["POST"])
def create_container_readings():
    """Receive the temperature, humidity, luminosity and RFID tag of a container monitor (TS56)."""
    return _record_readings(ContainerReadingRequest, DeviceType.CONTAINER_MONITOR)


@tracking_api.route("/api/v1/control/actuation-events", methods=["POST"])
def create_actuation_event():
    """Receive an action a container monitor executed (TS57)."""
    parsed, api_key, error = _parse(ActuationEventRequest)
    if error:
        return error
    try:
        result = current_app.extensions["telemetry_service"].record_actuation_event(
            parsed.device_id,
            api_key,
            parsed.action,
            parsed.trigger_metric,
            parsed.trigger_state,
            parsed.result,
            parsed.occurred_at,
            parsed.profile_version,
        )
    except DeviceAuthenticationFailed:
        return jsonify({"error": "Invalid device_id or API key"}), 401
    except DeviceTypeNotAllowed:
        return jsonify({"error": "Only CONTAINER_MONITOR nodes send actuation events"}), 403
    except TelemetryConflict:
        return jsonify({"error": "This action was already sent with different data"}), 409
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(_event_resource(result.event)), 201 if result.created else 200
