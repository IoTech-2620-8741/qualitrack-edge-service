from enum import Enum

class DeviceType(Enum):
    """Enumeration of QualyTrack IoT devices"""
    ENVIRONMENT_MONITOR = "ENVIRONMENT_MONITOR"
    CONTAINER_MONITOR = "CONTAINER_MONITOR"

class DeviceStatus(Enum):
    """Enumeration of device lifecycle statuses."""

    REGISTERED = "REGISTERED"
    CONFIGURED = "CONFIGURED"
    CALIBRATED = "CALIBRATED"
