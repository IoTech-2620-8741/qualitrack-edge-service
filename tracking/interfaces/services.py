"""Interface (REST API) layer for the Tracking bounded context.

This module exposes the REST endpoints of the Tracking context: the submission
of environmental and container-monitor readings, the submission of actuation
events and the reading of the configuration of a device.

Request bodies are validated by ``flask_openapi3`` through the typed ``body``
parameter of each view. Validation failures are translated into the project's
error format by the callback registered in ``app.py``.
"""

from flask import current_app, jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

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
    """Base body shared by every request sent by a node.

    Attributes:
        device_id: Identifier of the node that sends the data.
        profile_version: Version of the profile the node had applied, if any.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    device_id: str = Field(..., min_length=1, description="Identifier of the node.")
    profile_version: int | None = Field(None, ge=0, description="Profile version the node applied.")


class EnvironmentReadingRequest(_TelemetryRequest):
    """Body of the environmental readings request.

    Attributes:
        measured_at: Time of the measurement, with timezone offset.
        air_quality: Air quality in ppm.
        luminosity: Luminosity of the room in lux.
        motion: Whether motion was detected.
    """

    measured_at: AwareDatetime = Field(..., description="ISO 8601 measurement time with offset.")
    air_quality: float | None = Field(None, description="Air quality in ppm.")
    luminosity: float | None = Field(None, ge=0, strict=True, description="Luminosity of the room in lux.")
    motion: bool | None = Field(None, description="Whether motion was detected.")

    def values(self) -> dict[Metric, float]:
        """Collect the metrics that were actually sent.

        Returns:
            A mapping from each present metric to its numeric value. Motion is
            converted to ``1.0`` (detected) or ``0.0`` (not detected).
        """
        values = {}
        if self.air_quality is not None: values[Metric.AIR_QUALITY] = self.air_quality
        if self.luminosity is not None: values[Metric.LUMINOSITY] = self.luminosity
        if self.motion is not None: values[Metric.MOTION] = 1.0 if self.motion else 0.0
        return values


class ContainerReadingRequest(_TelemetryRequest):
    """Body of the container-monitor readings request.

    Attributes:
        measured_at: Time of the measurement, with timezone offset.
        temperature: Temperature in °C.
        humidity: Relative humidity in %.
        luminosity: Luminosity in lux.
        rfid_tag: RFID tag that was read.
    """

    measured_at: AwareDatetime = Field(..., description="ISO 8601 measurement time with offset.")
    temperature: float | None = Field(None, description="Temperature in °C.")
    humidity: float | None = Field(None, description="Relative humidity in %.")
    luminosity: float | None = Field(None, description="Luminosity in lux.")
    rfid_tag: str | None = Field(None, min_length=1, max_length=120, description="RFID tag read.")

    def values(self) -> dict[Metric, float | str]:
        """Collect the metrics that were actually sent.

        Returns:
            A mapping from each present metric to its value. Numeric metrics
            keep their number and the RFID tag keeps its text.
        """
        candidates = {
            Metric.TEMPERATURE: self.temperature,
            Metric.HUMIDITY: self.humidity,
            Metric.LUMINOSITY: self.luminosity,
            Metric.RFID_TAG: self.rfid_tag,
        }
        return {metric: value for metric, value in candidates.items() if value is not None}


class ActuationEventRequest(_TelemetryRequest):
    """Body of the actuation event request.

    Attributes:
        action: Action the node performed.
        trigger_metric: Metric that caused the action, if any.
        trigger_state: Environmental state that caused the action, if any.
        result: Outcome of the action; ``EXECUTED`` by default.
        occurred_at: Time of the action, with timezone offset.
    """

    action: ActuationAction
    trigger_metric: Metric | None = Field(None, description="Metric that caused the action.")
    trigger_state: EnvironmentalState | None = Field(None, description="State that caused the action.")
    result: ActuationResult = ActuationResult.EXECUTED
    occurred_at: AwareDatetime = Field(..., description="ISO 8601 time of the action with offset.")


class DevicePath(BaseModel):
    """Path parameters of the configuration endpoint.

    Attributes:
        device_id: Identifier of the node whose configuration is requested.
    """

    device_id: str = Field(..., description="Identifier of the node.")


# ---------- Responses ----------
class ReadingResource(BaseModel):
    """A single stored reading.

    Attributes:
        id: Identifier of the reading.
        metric: Name of the measured metric.
        value: Numeric value, when the metric is numeric.
        text_value: Text value, when the metric is textual (for example RFID).
        sync_status: Synchronization status with the cloud.
    """

    id: int | str
    metric: str
    value: float | None = None
    text_value: str | None = None
    sync_status: str


class ReadingsResponse(BaseModel):
    """Body returned after the readings are recorded.

    Attributes:
        device_id: Identifier of the node that sent the readings.
        measured_at: Time of the measurement in ISO 8601.
        profile_version: Profile version the node reported, if any.
        readings: The readings that were recorded.
    """

    device_id: str
    measured_at: str
    profile_version: int | None = None
    readings: list[ReadingResource]


class ActuationEventResponse(BaseModel):
    """Body returned after an actuation event is recorded.

    Attributes:
        id: Identifier of the event.
        device_id: Identifier of the node that sent the event.
        action: Action that was performed.
        trigger_metric: Metric that caused the action, if any.
        trigger_state: State that caused the action, if any.
        result: Outcome of the action.
        occurred_at: Time of the action in ISO 8601.
        profile_version: Profile version the node reported, if any.
        sync_status: Synchronization status with the cloud.
    """

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
    """Normal and critical limits of a metric.

    Attributes:
        metric: Name of the metric.
        unit: Unit of measurement, if any.
        normal_min: Lower limit of the normal range.
        normal_max: Upper limit of the normal range.
        critical_min: Lower critical limit.
        critical_max: Upper critical limit.
    """

    metric: str
    unit: str | None = None
    normal_min: float | None = None
    normal_max: float | None = None
    critical_min: float | None = None
    critical_max: float | None = None


class ActuationRuleResource(BaseModel):
    """Rule that links an environmental state to an action.

    Attributes:
        metric: Metric the rule watches.
        state: State that triggers the rule.
        action: Action the node must perform.
    """

    metric: str
    state: str
    action: str


class ConfigurationResponse(BaseModel):
    """Configuration of a device, built from its cached profile.

    Attributes:
        device_id: Identifier of the device.
        device_type: Type of the device.
        profile_version: Version of the profile.
        thresholds: Limits of each metric.
        actuation_rules: Rules the node must apply.
        updated_at: Time of the last update in the cloud, if known.
        synced_at: Time the Edge synchronized the profile.
    """

    device_id: str
    device_type: str
    profile_version: int
    thresholds: list[ThresholdResource]
    actuation_rules: list[ActuationRuleResource]
    updated_at: str | None = None
    synced_at: str


class ErrorResponse(BaseModel):
    """Body returned when a request fails.

    Attributes:
        error: Human-readable description of the error.
    """

    error: str


# ---------- Helpers ----------
def _node_api_key() -> str | None:
    """Extract the API key sent by the node.

    The key is read first from the ``X-API-Key`` header. If it is missing, the
    ``Authorization`` header is checked for a ``Bearer`` token.

    Returns:
        The API key, or ``None`` if the request does not carry a usable one.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key: return api_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer": return None
    return token.strip() or None


def _iso(value) -> str:
    """Format a datetime as ISO 8601, using ``Z`` for UTC.

    Args:
        value: The datetime to format.

    Returns:
        The ISO 8601 string.
    """
    return value.isoformat().replace("+00:00", "Z")


def _reading_resource(reading) -> dict:
    """Convert a domain reading into its response representation.

    Args:
        reading: The telemetry reading from the domain.

    Returns:
        A dictionary that matches ``ReadingResource``.
    """
    return {
        "id": reading.id, "metric": reading.metric.value, "value": reading.value,
        "text_value": reading.text_value, "sync_status": reading.sync_status.value,
    }


def _event_resource(event) -> dict:
    """Convert a domain actuation event into its response representation.

    Args:
        event: The actuation event from the domain.

    Returns:
        A dictionary that matches ``ActuationEventResponse``.
    """
    return {
        "id": event.id, "device_id": event.device_id, "action": event.action.value,
        "trigger_metric": None if event.trigger_metric is None else event.trigger_metric.value,
        "trigger_state": None if event.trigger_state is None else event.trigger_state.value,
        "result": event.result.value, "occurred_at": _iso(event.occurred_at.value),
        "profile_version": None if event.profile_version is None else event.profile_version.value,
        "sync_status": event.sync_status.value,
    }


def _configuration_resource(configuration) -> dict:
    """Convert a device configuration into its response representation.

    Args:
        configuration: The configuration returned by the profile service.

    Returns:
        A dictionary that matches ``ConfigurationResponse``.
    """
    profile = configuration.profile
    return {
        "device_id": profile.device_id, "device_type": configuration.device_type.value,
        "profile_version": profile.version.value,
        "thresholds": [
            {"metric": t.metric.value, "unit": t.unit, "normal_min": t.normal_min,
             "normal_max": t.normal_max, "critical_min": t.critical_min, "critical_max": t.critical_max}
            for t in profile.thresholds
        ],
        "actuation_rules": [
            {"metric": r.metric.value, "state": r.state.value, "action": r.action.value}
            for r in profile.actuation_rules
        ],
        "updated_at": None if profile.cloud_updated_at is None else _iso(profile.cloud_updated_at),
        "synced_at": _iso(profile.synced_at),
    }


@tracking_api.before_request
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


def _record_readings(body, device_type: DeviceType):
    """Record the readings of a node and build the HTTP response.

    Shared by the environmental and container-monitor endpoints. The body is
    already validated, so only the API key and the business errors are handled.

    Args:
        body: The validated readings request.
        device_type: Type of node that is allowed to send these readings.

    Returns:
        A tuple with the JSON response and the status code: 201 when the
        readings are new, 200 when they were already stored, 401 for missing or
        invalid credentials, 403 for a wrong device type, 409 for conflicting
        values and 400 for domain validation errors.
    """
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        result = current_app.extensions["telemetry_service"].record_readings(
            body.device_id, api_key, device_type, body.measured_at,
            body.profile_version, body.values(),
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
        "device_id": body.device_id, "measured_at": _iso(result.readings[0].measured_at.value),
        "profile_version": body.profile_version, "readings": [_reading_resource(r) for r in result.readings],
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
def create_environment_readings(body: EnvironmentReadingRequest):
    """Record the readings sent by an environmental device."""
    return _record_readings(body, DeviceType.ENVIRONMENTAL_DEVICE)


@tracking_api.post(
    "/api/v1/monitoring/container-readings",
    summary="Submit container-monitor readings",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ReadingsResponse, 200: ReadingsResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def create_container_readings(body: ContainerReadingRequest):
    """Record the readings sent by a container monitor."""
    return _record_readings(body, DeviceType.CONTAINER_MONITOR)


@tracking_api.post(
    "/api/v1/control/actuation-events",
    summary="Submit an actuation event",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ActuationEventResponse, 200: ActuationEventResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse, 415: ErrorResponse},
)
def create_actuation_event(body: ActuationEventRequest):
    """Record an action performed by a container monitor.

    Returns 201 when the event is new and 200 when it was already stored.
    """
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        result = current_app.extensions["telemetry_service"].record_actuation_event(
            body.device_id, api_key, body.action, body.trigger_metric,
            body.trigger_state, body.result, body.occurred_at, body.profile_version,
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
    """Return the thresholds and actuation rules cached for a device.

    Responds 404 when the Edge has not synchronized a profile for it yet.
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
    """Register the Tracking blueprint in the application.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(tracking_api)