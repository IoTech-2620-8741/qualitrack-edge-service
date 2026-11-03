"""Enumerations for the Tracking bounded context."""

from enum import Enum


class DeviceType(str, Enum):
    """Types of the nodes whose telemetry Tracking synchronizes, as the cloud names them."""

    ENVIRONMENTAL_DEVICE = "ENVIRONMENTAL_DEVICE"
    CONTAINER_MONITOR = "CONTAINER_MONITOR"


class DeviceStatus(str, Enum):
    """Operational status of a node in the Equipment context of the cloud."""

    OPERATIONAL = "OPERATIONAL"
    MAINTENANCE = "MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    INACTIVE = "INACTIVE"


class Metric(str, Enum):
    """Variables the nodes report, with the same names and rules as the cloud.

    The environmental device reports the air quality in ppm and motion events
    (1 when detected, 0 when the zone is quiet again). The container monitor
    reports temperature in °C, relative humidity in %, luminosity in lux and
    the RFID tags read on the container.
    """

    AIR_QUALITY = "AIR_QUALITY"
    MOTION = "MOTION"
    TEMPERATURE = "TEMPERATURE"
    HUMIDITY = "HUMIDITY"
    LUMINOSITY = "LUMINOSITY"
    RFID_TAG = "RFID_TAG"

    @property
    def device_type(self) -> DeviceType:
        """Type of the node that reports the metric."""
        if self in (Metric.AIR_QUALITY, Metric.MOTION):
            return DeviceType.ENVIRONMENTAL_DEVICE
        return DeviceType.CONTAINER_MONITOR

    @property
    def is_text(self) -> bool:
        """Whether the reading is a text, such as an RFID tag."""
        return self is Metric.RFID_TAG

    @property
    def is_detection(self) -> bool:
        """Whether the reading is 1 when something is detected and 0 otherwise."""
        return self is Metric.MOTION

    @property
    def has_thresholds(self) -> bool:
        """Whether the environmental profile can set thresholds and actions for the metric."""
        return not (self.is_text or self.is_detection)


class EnvironmentalState(str, Enum):
    """State of a metric against the thresholds of the profile."""

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ActuationAction(str, Enum):
    """Actions the container monitor executes locally."""

    VENTILATION_ON = "VENTILATION_ON"
    VENTILATION_OFF = "VENTILATION_OFF"
    COOLING_ON = "COOLING_ON"
    COOLING_OFF = "COOLING_OFF"
    SERVO_OPEN = "SERVO_OPEN"
    SERVO_CLOSE = "SERVO_CLOSE"

    @property
    def is_activation(self) -> bool:
        """Whether the action starts a response, the only kind a rule of the profile requests."""
        return self in (ActuationAction.VENTILATION_ON, ActuationAction.COOLING_ON, ActuationAction.SERVO_OPEN)


class ActuationResult(str, Enum):
    """Outcome of an action of the container monitor."""

    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class SyncStatus(str, Enum):
    """Synchronization state of a record the Edge received (TS60).

    ``PENDING`` records wait in SQLite until the cloud accepts them
    (``SYNCED``); the cloud may also refuse one for good (``REJECTED``).
    """

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    REJECTED = "REJECTED"
