"""Cloud gateway that reads the device registry of the laboratory.

It is the anti-corruption layer between the Equipment context of the cloud
and Tracking: it turns the equipment resources of the QualiTrack Cloud REST API
into :class:`~tracking.domain.entities.MonitoredDevice` concepts.
"""
import logging
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRejectedError, CloudSession
from tracking.domain.entities import MonitoredDevice
from tracking.domain.enums import DeviceStatus, DeviceType

LOGGER = logging.getLogger(__name__)


class CloudDeviceRegistryGateway:
    """Read the environmental devices and container monitors of the laboratory."""

    def __init__(self, cloud_session: CloudSession):
        self._cloud_session = cloud_session

    def fetch_devices(self) -> list[MonitoredDevice]:
        """Return the nodes the cloud registers for the Edge.

        Equipment without an IoT device type or without the identifier the
        Edge recognises (``sensorExternalId``) is not a node, so it is left out.
        """
        laboratory_id = self._cloud_session.laboratory_id()
        response = self._cloud_session.request(
            "GET", f"/api/v1/laboratories/{laboratory_id}/equipments"
        )
        if response.status_code != 200:
            raise CloudRejectedError(
                f"The cloud answered {response.status_code} to the equipment list",
                response.status_code,
            )
        synced_at = datetime.now(UTC)
        devices = []
        for resource in response.json():
            device = self._to_monitored_device(resource, synced_at)
            if device is not None:
                devices.append(device)
        return devices

    @staticmethod
    def _to_monitored_device(resource: dict, synced_at: datetime) -> MonitoredDevice | None:
        device_id = resource.get("sensorExternalId")
        if not device_id or resource.get("deviceType") not in DeviceType._value2member_map_:
            return None
        try:
            return MonitoredDevice(
                device_id=device_id,
                device_type=DeviceType(resource["deviceType"]),
                laboratory_id=resource["laboratoryId"],
                environment_id=resource.get("environmentId"),
                cloud_device_id=resource["id"],
                status=DeviceStatus(resource["status"]),
                synced_at=synced_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.warning("Skipped the cloud device %s: %s", device_id, error)
            return None
