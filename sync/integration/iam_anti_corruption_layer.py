"""DDD anti-corruption layer for the Sync-to-IAM context relationship."""

from iam.application.services import AuthApplicationService


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
