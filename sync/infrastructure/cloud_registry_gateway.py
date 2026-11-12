"""Cloud gateway that reads the device registry of the laboratory.

It is the anti-corruption layer between the Equipment context of the cloud
and Sync: it turns the equipment resources of the QualiTrack Cloud REST API
into :class:`~sync.domain.entities.MonitoredDevice` concepts.
"""
import logging
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRejectedError, CloudSession
from urllib.parse import quote

from sync.domain.entities import MonitoredDevice
from sync.domain.enums import DeviceStatus
from tracking.domain.enums import DeviceType

LOGGER = logging.getLogger(__name__)


class CloudDeviceRegistryGateway:
    """Read the environmental devices and container monitors of the laboratory."""

    def __init__(self, cloud_session: CloudSession):
        self._cloud_session = cloud_session

    def fetch_devices(self) -> list[MonitoredDevice]:
        """Retrieve IoT devices assigned to the authenticated Gateway.

        Requests only the environmental devices and container
        monitors registered in the Gateway's current environment.

        The Edge Gateway identity is obtained from CloudSession.

        Returns:
            Monitored devices authorized for the Gateway.

        Raises:
            CloudRejectedError: If the cloud returns an invalid
                response or an unexpected status.
            CloudUnavailableError: If the cloud cannot be reached.
        """
        device_id = quote(
            self._cloud_session.device_id,
            safe="",
        )

        response = self._cloud_session.request(
            "GET",
            f"/api/v1/edge-gateways/{device_id}/iot-devices",
        )

        if response.status_code != 200:
            raise CloudRejectedError(
                f"The cloud answered {response.status_code} "
                "to the Gateway device registry",
                response.status_code,
            )

        try:
            resources = response.json()
        except ValueError as error:
            raise CloudRejectedError(
                "The cloud returned invalid device registry JSON"
            ) from error

        if not isinstance(resources, list):
            raise CloudRejectedError(
                "The cloud device registry must be a JSON array"
            )

        if any(not isinstance(item, dict) for item in resources):
            raise CloudRejectedError(
                "The cloud device registry contains invalid entries"
            )

        synced_at = datetime.now(UTC)
        devices = []

        for resource in resources:
            device = self._to_monitored_device(
                resource,
                synced_at,
            )

            if device is not None:
                devices.append(device)

        LOGGER.info(
            "Retrieved %d IoT devices for Gateway %s",
            len(devices),
            self._cloud_session.device_id,
        )

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
