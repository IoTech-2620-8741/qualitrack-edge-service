"""Cloud gateway that reads the device registry of the laboratory.

It is the anti-corruption layer between the Equipment context of the cloud
and Sync: it turns the equipment resources of the QualiTrack Cloud REST API
into :class:`~sync.domain.entities.MonitoredDevice` concepts.
"""
import logging
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRejectedError, CloudSession
from sync.domain.entities import MonitoredDevice
from sync.domain.enums import DeviceStatus
from tracking.domain.enums import DeviceType

LOGGER = logging.getLogger(__name__)


class CloudDeviceRegistryGateway:
    """Read the environmental devices and container monitors of the laboratory.

    With ``environment_ids`` it reads only the IoT devices of those environments
    (ADR-0013); without them it reads the equipment of the whole laboratory.
    """

    def __init__(self, cloud_session: CloudSession, environment_ids: tuple[int, ...] = ()):
        self._cloud_session = cloud_session
        self._environment_ids = tuple(environment_ids)

    def fetch_devices(self) -> list[MonitoredDevice]:
        """Return the nodes the cloud registers for the Edge.

        Equipment without an IoT device type or without the identifier the
        Edge recognises (``sensorExternalId``) is not a node, so it is left out.
        """
        laboratory_id = self._cloud_session.laboratory_id()
        if self._environment_ids:
            paths = [
                f"/api/v1/laboratories/{laboratory_id}/environments/{environment_id}/equipments?iot=true"
                for environment_id in self._environment_ids
            ]
        else:
            paths = [f"/api/v1/laboratories/{laboratory_id}/equipments"]
        resources = [resource for path in paths for resource in self._read_equipment_list(path)]

        synced_at = datetime.now(UTC)
        devices = []

        for resource in resources:
            device = self._to_monitored_device(resource, synced_at)
            if device is not None:
                devices.append(device)
        return devices

    def _read_equipment_list(self, path: str) -> list[dict]:
        """Return the equipment resources of one list of the cloud, or reject an unexpected answer."""
        response = self._cloud_session.request("GET", path)
        if response.status_code != 200:
            raise CloudRejectedError(
                f"The cloud answered {response.status_code} to the equipment list",
                response.status_code,
            )

        try:
            resources = response.json()
        except ValueError as error:
            raise CloudRejectedError("The cloud returned invalid equipment JSON") from error
        if not isinstance(resources, list):
            raise CloudRejectedError("The cloud equipment list must be a JSON array")
        if any(not isinstance(resource, dict) for resource in resources):
            raise CloudRejectedError("The cloud equipment list contains invalid entries")
        return resources

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
