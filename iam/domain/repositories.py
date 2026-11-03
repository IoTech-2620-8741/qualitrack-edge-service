"""Repository abstraction for IAM device aggregates."""

from typing import Protocol

from iam.domain.entities import Device
from iam.domain.enums import DeviceType


class DeviceRepository(Protocol):
    """Persistence contract for registered devices."""

    def find_by_device_id(self, device_id: str) -> Device | None:
        """Load a device aggregate by its identity."""
        ...

    def find_all(self) -> list[Device]:
        """Load every registered device aggregate, ordered by identity."""
        ...

    def save(self, device: Device) -> Device:
        """Persist a device aggregate's current state."""
        ...

    def get_or_create_test_device(
        self, device_id: str, api_key: str, device_type: DeviceType
    ) -> Device:
        """Retrieve or create the configured development device."""
        ...