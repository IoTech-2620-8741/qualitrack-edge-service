"""Enumerations for the Sync bounded context."""

from enum import Enum


class DeviceStatus(str, Enum):
    """Operational status of a node in the Equipment context of the cloud."""

    OPERATIONAL = "OPERATIONAL"
    MAINTENANCE = "MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    INACTIVE = "INACTIVE"
