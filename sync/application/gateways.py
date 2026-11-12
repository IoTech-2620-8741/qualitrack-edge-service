"""Contracts of the cloud gateways used by the Sync application services.

The infrastructure layer implements them, and ``app.py`` injects the
implementations into the services.
"""
from typing import Protocol

from tracking.domain.entities import ActuationEvent, MonitoredDevice, TelemetryReading
from tracking.domain.enums import SyncStatus


class TelemetryGateway(Protocol):
    """Destination of the readings and actions the Edge keeps pending.

    Each method returns ``SYNCED`` when the cloud accepts the record (also when
    it already had it) and ``REJECTED`` when it refuses it for good. It raises
    ``CloudRequestError`` when the cloud cannot answer now, so the record stays
    pending.
    """

    def send_reading(self, device: MonitoredDevice, reading: TelemetryReading) -> SyncStatus:
        """Send one reading to the cloud route of its node."""
        ...

    def send_actuation_event(self, device: MonitoredDevice, event: ActuationEvent) -> SyncStatus:
        """Send one action to the cloud route of its container monitor."""
        ...
