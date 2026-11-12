"""Contracts of the cloud gateways used by the Tracking application services.

The infrastructure layer implements them, and ``app.py`` injects the
implementations into the services.
"""
from typing import Protocol

from tracking.domain.entities import EnvironmentalProfile, MonitoredDevice


class DeviceRegistryGateway(Protocol):
    """Source of the nodes the cloud registers for the laboratory of the Edge."""

    def fetch_devices(self) -> list[MonitoredDevice]:
        """Read the environmental devices and container monitors of the laboratory."""
        ...


class ProfileGateway(Protocol):
    """Source of the environmental profile each node applies."""

    def fetch_profile(self, device: MonitoredDevice) -> EnvironmentalProfile | None:
        """Read the profile of a node, or ``None`` when the cloud has none yet."""
        ...
