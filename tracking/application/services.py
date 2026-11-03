"""Application services for the Tracking bounded context.

Application services coordinate Tracking use cases: they read the cloud
through injected gateways and persist the outcome through repositories.
"""
from tracking.application.errors import CloudSynchronizationDisabled
from tracking.application.gateways import DeviceRegistryGateway
from tracking.domain.entities import MonitoredDevice
from tracking.domain.repositories import MonitoredDeviceRepository
from tracking.infrastructure.repositories import (
    MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl,
)


class DeviceRegistryApplicationService:
    """Keep the local copy of the nodes the cloud registers for the laboratory.

    The cloud is the source of truth of the registry. The local copy gives the
    Edge the cloud identifiers it synchronizes telemetry to, and it stays
    available while the cloud is unreachable.
    """

    def __init__(
        self,
        registry_gateway: DeviceRegistryGateway | None,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.registry_gateway = registry_gateway
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )

    def synchronize_registry(self) -> list[MonitoredDevice]:
        """Replace the local copy with the nodes the cloud registers now.

        The local copy changes only when the cloud answers, so a failed
        synchronization keeps the previous one.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                did not answer or rejected the request.
        """
        if self.registry_gateway is None:
            raise CloudSynchronizationDisabled
        devices = self.registry_gateway.fetch_devices()
        self.monitored_device_repository.replace_all(devices)
        return devices

    def monitored_devices(self) -> list[MonitoredDevice]:
        """Return the local copy of the registry, ordered by identifier."""
        return self.monitored_device_repository.find_all()

    def find_monitored_device(self, device_id: str) -> MonitoredDevice | None:
        """Return the cloud registration of one node, if the cloud registers it."""
        return self.monitored_device_repository.find_by_device_id(device_id)
