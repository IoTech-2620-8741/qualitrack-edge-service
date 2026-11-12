"""DDD anti-corruption layer for the Tracking-to-IAM context relationship."""

from iam.application.services import AuthApplicationService

from tracking.domain.entities import ReportingDevice
from tracking.domain.enums import DeviceType


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
