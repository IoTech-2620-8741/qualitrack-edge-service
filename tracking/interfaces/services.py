"""Interface (REST API) layer for the Tracking bounded context.

Exposes a Flask Blueprint (``tracking_api``) with the endpoints nodes use to
send environmental and container readings, send actuation events and read
their configuration. Every endpoint authenticates the node with its API key.
This layer owns no domain logic: it validates the request, calls the
application services and maps the result to JSON.
"""
from flask import current_app, request, jsonify
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, ConfigDict, Field
from tracking.domain.enums import DeviceType, Metric
from tracking.domain.errors import TelemetryConflict
from tracking.application.errors import ConfigurationNotAvailable, DeviceAuthenticationFailed, DeviceTypeNotAllowed

tracking_api = APIBlueprint("tracking_api", __name__)
TRACKING_TAG = Tag(name="Tracking", description="Telemetry and configurations")
SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]


# ---------- Requests ----------
class EnvironmentReadingRequest(BaseModel):
    """Body of a submission of readings from an environmental device.

    Every metric is optional: the node sends only what it measured.

    Attributes:
        device_id: Identifier of the node.
        profile_version: Version of the profile the node applied, if reported.
        measured_at: ISO 8601 measurement time with offset.
        air_quality: Air quality in ppm.
        luminosity: Luminosity of the room in lux.
        motion: Whether motion was detected.
    """

    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(..., description="Identifier of the node.")
    profile_version: int | None = Field(None, description="Profile version the node applied.")
    measured_at: str = Field(..., description="ISO 8601 measurement time with offset.")
    air_quality: float | None = Field(None, description="Air quality in ppm.")
    luminosity: float | None = Field(None, description="Luminosity of the room in lux.")
    motion: bool | None = Field(None, description="Whether motion was detected.")


class ContainerReadingRequest(BaseModel):
    """Body of a submission of readings from a container monitor.

    Every metric is optional: the node sends only what it measured.

    Attributes:
        device_id: Identifier of the node.
        profile_version: Version of the profile the node applied, if reported.
        measured_at: ISO 8601 measurement time with offset.
        temperature: Temperature in °C.
        humidity: Relative humidity in %.
        luminosity: Luminosity in lux.
        rfid_tag: RFID tag read by the node.
    """

    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(..., description="Identifier of the node.")
    profile_version: int | None = Field(None, description="Profile version the node applied.")
    measured_at: str = Field(..., description="ISO 8601 measurement time with offset.")
    temperature: float | None = Field(None, description="Temperature in °C.")
    humidity: float | None = Field(None, description="Relative humidity in %.")
    luminosity: float | None = Field(None, description="Luminosity in lux.")
    rfid_tag: str | None = Field(None, description="RFID tag read by the node.")


class ActuationEventRequest(BaseModel):
    """Body of the report of an action a node executed.

    Attributes:
        device_id: Identifier of the node.
        action: Action the node executed.
        trigger_metric: Metric that triggered the action, if any.
        trigger_state: State of the metric that triggered the action, if any.
        result: Result of the action; ``"EXECUTED"`` by default.
        occurred_at: ISO 8601 time of the action with offset.
        profile_version: Version of the profile the node applied, if reported.
    """

    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(..., description="Identifier of the node.")
    action: str = Field(..., description="Action executed by the node.")
    trigger_metric: str | None = Field(None, description="Metric that triggered the action.")
    trigger_state: str | None = Field(None, description="State that triggered the action.")
    result: str | None = Field("EXECUTED", description="Result of the action.")
    occurred_at: str = Field(..., description="ISO 8601 time with offset.")
    profile_version: int | None = Field(None, description="Profile version the node applied.")


class DevicePath(BaseModel):
    """Path parameters of the endpoints that address one node.

    Attributes:
        device_id: Identifier of the node.
    """

    device_id: str = Field(..., description="Identifier of the node.")


# ---------- Responses ----------
class ReadingResource(BaseModel):
    """One stored reading of a metric.

    Attributes:
        id: Persistence identity of the reading.
        metric: Variable measured.
        value: Numeric value; ``None`` only for text metrics.
        text_value: Text read (an RFID tag); ``None`` for numeric metrics.
        sync_status: Synchronization state with the cloud.
    """

    id: int | str
    metric: str
    value: float | None = None
    text_value: str | None = None
    sync_status: str


class ReadingsResponse(BaseModel):
    """Body returned after readings are recorded.

    Attributes:
        device_id: Identifier of the node that measured the values.
        measured_at: Measurement time in ISO 8601 UTC.
        profile_version: Version of the profile the node applied, if reported.
        readings: The readings that were stored.
    """

    device_id: str
    measured_at: str
    profile_version: int | None = None
    readings: list[ReadingResource]


class ActuationEventResponse(BaseModel):
    """Body returned after an actuation event is recorded.

    Attributes:
        id: Persistence identity of the event.
        device_id: Identifier of the node that executed the action.
        action: Action executed.
        trigger_metric: Metric that triggered the action, or ``None``.
        trigger_state: State that triggered the action, or ``None``.
        result: Result of the action.
        occurred_at: Time of the action in ISO 8601 UTC.
        profile_version: Version of the profile the node applied, or ``None``.
        sync_status: Synchronization state with the cloud.
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
    """Normal and critical range of one metric.

    Attributes:
        metric: Variable the range applies to.
        unit: Unit of measure, if any.
        normal_min: Lower bound of the normal range.
        normal_max: Upper bound of the normal range.
        critical_min: Lower bound below which the value is critical.
        critical_max: Upper bound above which the value is critical.
    """

    metric: str
    unit: str | None = None
    normal_min: float | None = None
    normal_max: float | None = None
    critical_min: float | None = None
    critical_max: float | None = None


class ActuationRuleResource(BaseModel):
    """Rule that tells a node which action to run for a metric state.

    Attributes:
        metric: Variable that is watched.
        state: State of the metric that fires the rule.
        action: Action the node must execute.
    """

    metric: str
    state: str
    action: str


class ConfigurationResponse(BaseModel):
    """Configuration of a node: its profile as cached by the Edge.

    Attributes:
        device_id: Identifier of the node.
        device_type: Type of the node.
        profile_version: Version of the profile.
        thresholds: Normal and critical ranges per metric.
        actuation_rules: Actions to run when a metric reaches a state.
        updated_at: When the cloud last updated the profile, or ``None``.
        synced_at: When the Edge last synchronized the profile.
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
        error: Human-readable description of the failure.
    """

    error: str


# ---------- Helpers ----------
def _node_api_key() -> str | None:
    """Return the API key of the node: the ``X-API-Key`` header, or else an ``Authorization: Bearer`` token.

    When both headers arrive, ``X-API-Key`` wins.

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


def _iso(value) -> str:
    """Format a timezone-aware datetime as ISO 8601 with a ``Z`` suffix for UTC.

    Args:
        value: The datetime to format.

    Returns:
        The formatted string.
    """
    return value.isoformat().replace("+00:00", "Z")


def _reading_resource(reading) -> dict:
    """Map a telemetry reading to its JSON representation.

    Args:
        reading: The ``TelemetryReading`` entity.

    Returns:
        A dict matching :class:`ReadingResource`.
    """
    return {
        "id": reading.id,
        "metric": reading.metric.value,
        "value": reading.value,
        "text_value": reading.text_value,
        "sync_status": reading.sync_status.value,
    }


def _event_resource(event) -> dict:
    """Map an actuation event to its JSON representation.

    Args:
        event: The ``ActuationEvent`` entity.

    Returns:
        A dict matching :class:`ActuationEventResponse`.
    """
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
    """Map the configuration of a node to its JSON representation.

    Args:
        configuration: The configuration with the device type and its profile.

    Returns:
        A dict matching :class:`ConfigurationResponse`.
    """
    profile = configuration.profile
    return {
        "device_id": profile.device_id,
        "device_type": configuration.device_type.value,
        "profile_version": profile.version.value,
        "thresholds": [
            {
                "metric": t.metric.value, "unit": t.unit,
                "normal_min": t.normal_min, "normal_max": t.normal_max,
                "critical_min": t.critical_min, "critical_max": t.critical_max,
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


def _record_readings(data, device_type: DeviceType):
    """Authenticate the node and record the readings of a request.

    Collects the metrics the node sent according to its type and delegates to
    the telemetry application service. Domain errors become HTTP statuses.

    Args:
        data: Validated request body, environmental or container.
        device_type: Type of node that is allowed to send these readings.

    Returns:
        A tuple with the JSON body and the HTTP status: ``201`` when the
        readings are new, ``200`` when they were already stored with the same
        values, ``400`` for invalid data, ``401`` for a missing or invalid API
        key, ``403`` when the node type is not allowed and ``409`` when the
        readings were already sent with different values.
    """
    device_id = data.device_id
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        values = {}
        if device_type == DeviceType.ENVIRONMENTAL_DEVICE:
            if data.air_quality is not None: values[Metric.AIR_QUALITY] = data.air_quality
            if data.luminosity is not None: values[Metric.LUMINOSITY] = data.luminosity
            if data.motion is not None: values[Metric.MOTION] = 1.0 if data.motion else 0.0
        elif device_type == DeviceType.CONTAINER_MONITOR:
            if data.temperature is not None: values[Metric.TEMPERATURE] = data.temperature
            if data.humidity is not None: values[Metric.HUMIDITY] = data.humidity
            if data.luminosity is not None: values[Metric.LUMINOSITY] = data.luminosity
            if data.rfid_tag is not None: values[Metric.RFID_TAG] = data.rfid_tag
        result = current_app.extensions["telemetry_service"].record_readings(
            device_id, api_key, device_type, data.measured_at, data.profile_version, values
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
        "device_id": device_id,
        "measured_at": _iso(result.readings[0].measured_at.value),
        "profile_version": data.profile_version,
        "readings": [_reading_resource(r) for r in result.readings],
    }), 201 if result.created else 200


# ---------- Endpoints ----------
@tracking_api.post(
    "/api/v1/monitoring/environment-readings",
    summary="Submit environmental readings",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ReadingsResponse, 200: ReadingsResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse},
)
def env_readings(body: EnvironmentReadingRequest):
    """Submit the readings of an environmental device.

    Args:
        body: Validated request body.

    Returns:
        The result of :func:`_record_readings` for an environmental device.
    """
    return _record_readings(body, DeviceType.ENVIRONMENTAL_DEVICE)


@tracking_api.post(
    "/api/v1/monitoring/container-readings",
    summary="Submit container-monitor readings",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ReadingsResponse, 200: ReadingsResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse},
)
def container_readings(body: ContainerReadingRequest):
    """Submit the readings of a container monitor.

    Args:
        body: Validated request body.

    Returns:
        The result of :func:`_record_readings` for a container monitor.
    """
    return _record_readings(body, DeviceType.CONTAINER_MONITOR)


@tracking_api.post(
    "/api/v1/control/actuation-events",
    summary="Submit an actuation event",
    tags=[TRACKING_TAG],
    security=SECURITY,
    responses={201: ActuationEventResponse, 200: ActuationEventResponse, 400: ErrorResponse,
               401: ErrorResponse, 403: ErrorResponse, 409: ErrorResponse},
)
def actuation_events(body: ActuationEventRequest):
    """Record an action a container monitor executed.

    Args:
        body: Validated request body.

    Returns:
        A tuple with the JSON body and the HTTP status: ``201`` when the event
        is new, ``200`` when it was already stored with the same data, ``400``
        for invalid data, ``401`` for a missing or invalid API key, ``403``
        when the node is not a container monitor and ``409`` when the action
        was already sent with different data.
    """
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        result = current_app.extensions["telemetry_service"].record_actuation_event(
            body.device_id, api_key, body.action, body.trigger_metric, body.trigger_state,
            body.result, body.occurred_at, body.profile_version
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
def configuration(path: DevicePath):
    """Read the configuration the Edge holds for a node.

    Args:
        path: Validated path parameters with the ``device_id``.

    Returns:
        A tuple with the JSON body and the HTTP status: ``200`` with the
        configuration, ``401`` for a missing or invalid API key, or ``404``
        when the Edge has no cloud profile for the node yet.
    """
    api_key = _node_api_key()
    if not api_key:
        return jsonify({"error": "Missing X-API-Key"}), 401
    try:
        configuration_data = current_app.extensions["environmental_profile_service"].configuration_for(
            path.device_id, api_key
        )
    except DeviceAuthenticationFailed:
        return jsonify({"error": "Invalid device_id or API key"}), 401
    except ConfigurationNotAvailable:
        return jsonify({"error": "The Edge has no profile of the cloud for this device yet"}), 404
    return jsonify(_configuration_resource(configuration_data)), 200


def init_routes(app):
    """Register the Tracking blueprint so its routes appear in the OpenAPI spec.

    Args:
        app: The ``OpenAPI`` application that receives the routes.
    """
    app.register_api(tracking_api)