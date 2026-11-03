"""Enumerations for the IAM bounded context."""

from enum import Enum


class DeviceType(str, Enum):
    """Types of the QualiTrack ESP32 nodes, named as the cloud registers them."""

    ENVIRONMENTAL_DEVICE = "ENVIRONMENTAL_DEVICE"
    CONTAINER_MONITOR = "CONTAINER_MONITOR"
