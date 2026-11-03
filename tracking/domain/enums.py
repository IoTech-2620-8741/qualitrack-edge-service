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
