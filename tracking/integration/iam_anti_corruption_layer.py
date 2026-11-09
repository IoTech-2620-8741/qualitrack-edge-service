"""DDD anti-corruption layer for the Tracking-to-IAM context relationship."""

from iam.application.services import AuthApplicationService

from tracking.domain.entities import ReportingDevice
from tracking.domain.enums import DeviceType
from iam.domain.enums import DeviceType as IamDeviceType

class IamAntiCorruptionLayer:
    """Translate IAM devices and authentication into Tracking's own concepts."""

    def __init__(self, iam_authentication_service: AuthApplicationService):
        self._iam_authentication_service = iam_authentication_service

    def authenticated_reporting_device(
        self, device_id: str, credential: str
    ) -> ReportingDevice | None:
        """Return Tracking's local device concept when IAM accepts the credentials."""
        device = self._iam_authentication_service.authenticated_device(device_id, credential)
        if device is None:
            return None
        return ReportingDevice(device.device_id, DeviceType(device.device_type.value))

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
            device_id: External identifier authorized by Tracking.
            device_type: Device type confirmed by the cloud registry.

        Returns:
            The newly generated API key, available only once.
        """
        issued = self._iam_authentication_service.register_device(
            device_id,
            IamDeviceType(device_type.value),
        )

        return issued.api_key