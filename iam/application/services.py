"""Application services for the IAM bounded context.

Application services orchestrate use-cases by coordinating domain objects and
repositories. They contain no domain logic themselves; all business rules live
in the domain layer.
"""
from iam.domain.entities import Device
from iam.domain.enums import DeviceType
from iam.domain.repositories import DeviceRepository
from iam.infrastructure.repositories import DeviceRepository as PeeweeDeviceRepository


class AuthApplicationService:
    """Application service that orchestrates device-authentication use-cases."""

    def __init__(self, device_repository: DeviceRepository | None = None):
        """Initialize the service with its repository collaborator."""
        self.device_repository = device_repository or PeeweeDeviceRepository()

    def authenticate(self, device_id: str, api_key: str) -> bool:
        """Authenticate a device by its MAC address and API key."""
        device = self.device_repository.find_by_device_id(device_id)
        return device is not None and device.authenticate(api_key)

    def register_device(self, device_id: str, api_key: str,
                        device_type: DeviceType) -> Device:
        """Register a device and persist its aggregate with a protected credential.

        Raises:
            ValueError: A device with this identity is already registered, or
                the domain rejects the identity or credential.
        """
        if self.device_repository.find_by_device_id(device_id) is not None:
            raise ValueError("Device is already registered")
        return self.device_repository.save(
            Device.register(device_id, api_key, device_type)
        )

    def revoke_device(self, device_id: str) -> Device:
        """Revoke and persist an active device.

        Raises:
            ValueError: The device does not exist or its aggregate cannot be
                revoked again.
        """
        device = self.device_repository.find_by_device_id(device_id)
        if device is None:
            raise ValueError("Device not found")
        return self.device_repository.save(device.revoke())

    def rotate_api_key(self, device_id: str, new_api_key: str) -> Device:
        """Replace and persist an active device's API-key verifier.

        Raises:
            ValueError: The device does not exist, is revoked, or the new
                credential is invalid.
        """
        device = self.device_repository.find_by_device_id(device_id)
        if device is None:
            raise ValueError("Device not found")
        return self.device_repository.save(device.rotate_api_key(new_api_key))

    def get_or_create_test_device(
        self,
        device_id: str,
        api_key: str,
        device_type: DeviceType = DeviceType.ENVIRONMENT_MONITOR,
    ) -> Device:
        """Retrieve the default test device, creating it if it does not exist.

        Intended only for development and local testing. The repository
        persists a newly registered aggregate when no matching device exists.

        Returns:
            The matching :class:`~iam.domain.entities.Device` aggregate.
        """
        return self.device_repository.get_or_create_test_device(
            device_id, api_key, device_type
        )