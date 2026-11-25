"""Interface (REST API) layer for the Tracking bounded context.

Exposes a Flask Blueprint (``tracking_api``) that receives the readings and
actions of the nodes (TS55, TS56, TS57) and hands each node its configuration
(TS58, TS59). It translates the requests into calls to the application
services and maps the results back to JSON responses. This layer owns no
domain logic.

Each telemetry request carries the ``device_id`` of the node in the body and
its API key in the ``X-API-Key`` header or as an ``Authorization: Bearer`` token.
Responses: ``201`` when something new is stored, ``200`` for an identical retry,
``400`` for invalid data, ``401`` for missing or rejected credentials, ``403``
when the node type does not send that telemetry, ``409`` when a retry changes a
stored observation and ``415`` for a non-JSON request.
"""
from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from tracking.application.errors import (
    ConfigurationNotAvailable,
    DeviceAuthenticationFailed,
    DeviceTypeNotAllowed,
)
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
)
from tracking.domain.errors import TelemetryConflict

tracking_api = APIBlueprint("tracking_api", __name__)
TRACKING_TAG = Tag(name="Tracking", description="Telemetry and configurations")
SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]


# ---------- Requests ----------
class _TelemetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(..., min_length=1, description="Identifier of the node.")
    profile_version: int | None = Field(None, ge=0, description="Profile version the node applied.")


class EnvironmentReadingRequest(_TelemetryRequest):
    """Metrics the environmental device measured at one moment (TS55)."""

    measured_at: AwareDatetime = Field(..., description="ISO 8601 measurement time with offset.")
    air_quality: float | None = Field(None, description="Air quality in ppm.")
    luminosity: float | None = Field(None, ge=0, strict=True, description="Luminosity of the room in lux.")
    motion: bool | None = Field(None, description="Whether motion was detected.")

    def values(self) -> dict[Metric, float]:
        values = {}
        if self.air_quality is not None:
            values[Metric.AIR_QUALITY] = self.air_quality
        if self.luminosity is not None:
            values[Metric.LUMINOSITY] = self.luminosity
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


class DevicePath(BaseModel):
    """Path parameters of the endpoints that address one node."""

    device_id: str = Field(..., description="Identifier of the node.")


# ---------- Responses  ----------
class ReadingResource(BaseModel):
    id: int | str
    metric: str
    value: float | None = None
    text_value: str | None = None
    sync_status: str


class ReadingsResponse(BaseModel):
    device_id: str
    measured_at: str
    profile_version: int | None = None
    readings: list[ReadingResource]


class ActuationEventResponse(BaseModel):
    id: int | str
    device_id: str
    action: str
    trigger_metric: str | None = None
    trigger_state: str | None = None
    result: str
    occurred_at: str
    profile_version: int | None = None
    sync_status: str


class ThresholdResource(BaseModel):
    metric: str
    unit: str | None = None
    normal_min: float | None = None
    normal_max: float | None = None
    critical_min: float | None = None
    critical_max: float | None = None


class ActuationRuleResource(BaseModel):
    metric: str
    state: str
    action: str


class ConfigurationResponse(BaseModel):
    device_id: str
    device_type: str
    profile_version: int
    thresholds: list[ThresholdResource]
    actuation_rules: list[ActuationRuleResource]
    updated_at: str | None = None
    synced_at: str


class ErrorResponse(BaseModel):
    error: str


# ---------- Helpers ----------
def _node_api_key() -> str | None:
    """Return the API key of the node: the ``X-API-Key`` header, or else an ``Authorization: Bearer`` token."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


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


def _configuration_resource(configuration) -> dict:
    profile = configuration.profile
    return {
        "device_id": profile.device_id,
        "device_type": configuration.device_type.value,
        "profile_version": profile.version.value,
        "thresholds": [
            {
                "metric": t.metric.value,
                "unit": t.unit,
                "normal_min": t.normal_min,
                "normal_max": t.normal_max,
                "critical_min": t.critical_min,
                "critical_max": t.critical_max,
            }
            for t in profile.thresholds
        ],
        "actuation_rules": [
            {"metric": r.metric.value, "state": r.state.value, "action": r.action.value}
            for r in profile.actuation_rules
        ],
        "updated_at": None if profile.cloud_updated_at is None else _iso(profile.cloud_updated_at),
        "synced_at": _iso(profile.synced_at),
    }


def _parse(request_model):
    """Validate the JSON body and the API key; return (parsed, api_key, error_response)."""
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
    api_key = _node_api_key()
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


# ---------- Endpoints ----------
@tracking_api.post(
    "/api/v1/monitoring/environment-readings",
    summary="Submit environmental readings",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ReadingsResponse, 200: ReadingsResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def create_environment_readings():
    """Receive the air quality, luminosity and motion the environmental device measured (TS55).

       Args:
           None: The request body is validated manually via ``_parse()``.

       Returns:
           A tuple with the JSON body and the HTTP status: ``201`` when the
           readings are new, ``200`` when they were already stored with the same
           values, ``400`` for invalid data or invalid JSON, ``401`` for a missing
           or invalid API key, ``403`` when the node is not an environmental
           device, ``409`` when the readings were already sent with different
           values, and ``415`` if the request is not JSON.
       """
    return _record_readings(EnvironmentReadingRequest, DeviceType.ENVIRONMENTAL_DEVICE)


@tracking_api.post(
    "/api/v1/monitoring/container-readings",
    summary="Submit container-monitor readings",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ReadingsResponse, 200: ReadingsResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def create_container_readings():
    """Receive the temperature, humidity, luminosity and RFID tag of a container monitor (TS56).

    Args:
        None: The request body is validated manually via ``_parse()``.

    Returns:
        A tuple with the JSON body and the HTTP status: ``201`` when the
        readings are new, ``200`` when they were already stored with the same
        values, ``400`` for invalid data or invalid JSON, ``401`` for a missing
        or invalid API key, ``403`` when the node is not a container monitor,
        ``409`` when the readings were already sent with different values,
        and ``415`` if the request is not JSON.
    """

    return _record_readings(ContainerReadingRequest, DeviceType.CONTAINER_MONITOR)


@tracking_api.post(
    "/api/v1/control/actuation-events",
    summary="Submit an actuation event",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ActuationEventResponse, 200: ActuationEventResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def create_actuation_event():
    """Record an action a container monitor executed (TS57).

        Args:
            None: The request body is validated manually via ``_parse()``.

        Returns:
            A tuple with the JSON body and the HTTP status: ``201`` when the event
            is new, ``200`` when it was already stored with the same data, ``400``
            for invalid data or invalid JSON, ``401`` for a missing or invalid API
            key, ``403`` when the node is not a container monitor, ``409`` when the
            action was already sent with different data, and ``415`` if the request
            is not JSON.
    """
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


@tracking_api.get(
    "/api/v1/configurations/<device_id>",
    summary="Read the configuration for a device",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={200: ConfigurationResponse, 401: ErrorResponse, 404: ErrorResponse},
)
def get_configuration(path: DevicePath):
    """Read the configuration the Edge holds for a node.

      Hands a node the profile it must apply (TS58 for environmental devices,
      TS59 for container monitors). The node identifies itself with its
      ``device_id`` in the path and its API key in the ``X-API-Key`` header
      (or as an ``Authorization: Bearer`` token), so it can only read its own
      configuration.

      Args:
          path: Validated path parameters with the ``device_id``.

      Returns:
          A tuple with the JSON body and the HTTP status: ``200`` with the
          thresholds, the actuation rules and the profile version; ``401`` for
          missing or rejected credentials; or ``404`` while the Edge has no
          profile of the cloud for the node.
      """
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        configuration = current_app.extensions["environmental_profile_service"].configuration_for(
            path.device_id, api_key
        )
    except DeviceAuthenticationFailed:
        return jsonify({"error": "Invalid device_id or API key"}), 401
    except ConfigurationNotAvailable:
        return jsonify({"error": "The Edge has no profile of the cloud for this device yet"}), 404
    return jsonify(_configuration_resource(configuration)), 200


def init_routes(app):
    """Register the Tracking blueprint so its routes appear in the OpenAPI spec."""
    app.register_api(tracking_api)