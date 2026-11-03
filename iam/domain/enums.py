from enum import Enum

class DeviceType(str, Enum):
    """Enumeration of QualyTrack IoT devices"""
    ENVIRONMENT_MONITOR = "ENVIRONMENT_MONITOR"
    CONTAINER_MONITOR = "CONTAINER_MONITOR"
