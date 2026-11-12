"""DDD anti-corruption layer for the Sync-to-IAM context relationship."""

from iam.application.services import AuthApplicationService
from iam.domain.enums import DeviceType as IamDeviceType
from tracking.domain.enums import DeviceType


class IamAntiCorruptionLayer:
    """Translate the IAM devices into the nodes Sync keeps the cloud registry of."""

    def __init__(self, iam_authentication_service: AuthApplicationService):
        self._iam_authentication_service = iam_authentication_service

    def registered_device_ids(self) -> set[str]:
        """Return the identifiers of the nodes registered in this Edge.

        Revoked nodes are included: they no longer sign in, but the records
        they sent before can still be synchronized.
        """
        return {device.device_id for device in self._iam_authentication_service.list_devices()}

    def enroll_device(self, device_id: str, device_type: DeviceType) -> str:
        """Enroll a cloud-authorized IoT device in local IAM.

        Reuses the existing IAM registration use case.

        Args:
            device_id: External identifier authorized by Sync.
            device_type: Device type confirmed by the cloud registry.

        Returns:
            The newly generated API key, available only once.
        """
        issued = self._iam_authentication_service.register_device(
            device_id,
            IamDeviceType(device_type.value),
        )

        return issued.api_key
