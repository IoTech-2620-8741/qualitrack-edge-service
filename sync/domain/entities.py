"""Domain entities for the Sync bounded context.

``MonitoredDevice`` is the local copy of what the cloud registers for each node
of this Edge: the cloud route its telemetry is synchronized to.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sync.domain.enums import DeviceStatus
from tracking.domain.enums import DeviceType


def _positive_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class MonitoredDevice:
    """A node of this Edge as the cloud registers it for the laboratory.

    It keeps the cloud identifiers its telemetry is synchronized to: the
    laboratory, the environment where the node is installed and the equipment
    identifier of the node in the cloud.

    Attributes:
        device_id: Identifier with which the cloud registers the node for the
            Edge (``sensorExternalId``, for example ``ENV-001``).
        device_type: Environmental device or container monitor.
        laboratory_id: Laboratory that owns the node in the cloud.
        environment_id: Environment of the node, or ``None`` while the cloud
            has not associated it with one.
        cloud_device_id: Equipment identifier of the node in the cloud.
        status: Operational status of the node in the cloud.
        synced_at: When the Edge last read the node from the cloud, in UTC.
    """

    device_id: str
    device_type: DeviceType
    laboratory_id: int
    environment_id: int | None
    cloud_device_id: int
    status: DeviceStatus
    synced_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("Device identifier cannot be empty")
        if not isinstance(self.device_type, DeviceType):
            raise TypeError("Monitored device requires a valid device type")
        if not isinstance(self.status, DeviceStatus):
            raise TypeError("Monitored device requires a valid status")
        if not _positive_id(self.laboratory_id) or not _positive_id(self.cloud_device_id):
            raise ValueError("Cloud identifiers must be positive integers")
        if self.environment_id is not None and not _positive_id(self.environment_id):
            raise ValueError("Environment identifier must be a positive integer")
        if not isinstance(self.synced_at, datetime):
            raise TypeError("Synchronization time must be a datetime")
        if self.synced_at.tzinfo is None or self.synced_at.utcoffset() is None:
            raise ValueError("Synchronization time must include a timezone")
        object.__setattr__(self, "synced_at", self.synced_at.astimezone(UTC))

    @property
    def is_in_environment(self) -> bool:
        """Whether the cloud has associated the node with an environment."""
        return self.environment_id is not None
