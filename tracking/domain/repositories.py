"""Repository abstractions for the Tracking bounded context."""

from typing import Protocol

from tracking.domain.entities import MonitoredDevice


class MonitoredDeviceRepository(Protocol):
    """Persistence contract for the local copy of the cloud device registry."""

    def find_by_device_id(self, device_id: str) -> MonitoredDevice | None:
        """Load a monitored device by the identifier of its node."""
        ...

    def find_all(self) -> list[MonitoredDevice]:
        """Load every monitored device, ordered by identifier."""
        ...

    def replace_all(self, devices: list[MonitoredDevice]) -> None:
        """Make the local copy equal to the devices the cloud registers."""
        ...
