"""Application services for the IAM bounded context.

Application services orchestrate use-cases by coordinating domain objects and
repositories. They contain no domain logic themselves; all business rules live
in the domain layer.
"""
from dataclasses import dataclass

from iam.domain.credentials import ApiCredential
from iam.domain.entities import Device
from iam.domain.enums import DeviceType
from iam.domain.repositories import DeviceRepository
from iam.infrastructure.repositories import DeviceRepository as PeeweeDeviceRepository


@dataclass(frozen=True, repr=False)
class IssuedApiKey:
    """A device with the API key it was just given.

    The key is returned only once, so it can be written to the device; the
    Edge keeps only its one-way verifier.
    """

    device: Device
    api_key: str


class AuthApplicationService:
    """Application service that orchestrates device-authentication use-cases."""

    def __init__(self, device_repository: DeviceRepository | None = None):
        """Initialize the service with its repository collaborator."""
        self.device_repository = device_repository or PeeweeDeviceRepository()

    def authenticated_device(self, device_id: str, api_key: str) -> Device | None:
        """Return the identified device when it accepts the presented API key.

        A credential is accepted only when the device exists, is not revoked,
        and its stored verifier matches the presented API key.
        """
        device = self.device_repository.find_by_device_id(device_id)
        if device is None or not device.authenticate(api_key):
            return None
        return device

    def register_device(self, device_id: str, device_type: DeviceType) -> IssuedApiKey:
        """Register a device with a new random API key.

        Raises:
            ValueError: A device with this identity is already registered, or
                the domain rejects the identity.
        """
        if self.device_repository.find_by_device_id(device_id) is not None:
            raise ValueError("Device is already registered")
        api_key = ApiCredential.generate_secret()
        device = self.device_repository.save(
            Device.register(device_id, api_key, device_type)
        )
        return IssuedApiKey(device, api_key)

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

    def rotate_api_key(self, device_id: str) -> IssuedApiKey:
        """Replace an active device's API key with a new random one.

        Raises:
            ValueError: The device does not exist or is revoked.
        """
        device = self.device_repository.find_by_device_id(device_id)
        if device is None:
            raise ValueError("Device not found")
        api_key = ApiCredential.generate_secret()
        device = self.device_repository.save(device.rotate_api_key(api_key))
        return IssuedApiKey(device, api_key)

    def list_devices(self) -> list[Device]:
        """Return every registered device, ordered by identity."""
        return self.device_repository.find_all()

    def get_or_create_test_device(
        self,
        device_id: str,
        api_key: str,
        device_type: DeviceType = DeviceType.ENVIRONMENTAL_DEVICE,
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
