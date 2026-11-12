"""Cloud gateway that reads the environmental profile of each node (TS64).

It is the anti-corruption layer between Sync and the Tracking & Telemetry
context of the cloud: it reads the profile a node applies and translates the
resource of the QualiTrack Cloud REST API into an
:class:`~tracking.domain.entities.EnvironmentalProfile`.
"""
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRejectedError, CloudSession
from sync.domain.entities import MonitoredDevice
from tracking.domain.entities import EnvironmentalProfile
from tracking.domain.enums import ActuationAction, EnvironmentalState, Metric
from tracking.domain.value_objects import ActuationRule, ProfileVersion, Threshold
from urllib.parse import quote

class CloudProfileGateway:
    """Read the profile of the environment or the container monitor of a node."""

    def __init__(self, cloud_session: CloudSession):
        self._cloud_session = cloud_session

    def fetch_profile(self, device: MonitoredDevice) -> EnvironmentalProfile | None:
        """Return the profile the node applies, or ``None`` when the cloud has none yet.

        Raises:
            CloudRejectedError: The cloud answered with an unexpected code or a
                profile the Edge cannot read; the previous copy is kept.
        """
        gateway_id = quote(
            self._cloud_session.device_id,
            safe="",
        )

        path = f"/api/v1/edge-gateways/{gateway_id}"f"/iot-devices/{device.cloud_device_id}""/environmental-profile"

        response = self._cloud_session.request("GET", path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise CloudRejectedError(
                f"The cloud answered {response.status_code} to the profile of {device.device_id}",
                response.status_code,
            )
        try:
            resource = response.json()
            if not isinstance(resource, dict):
                raise ValueError("The profile must be a JSON object")
            return self._to_profile(device.device_id, response.json())
        except (KeyError, TypeError, ValueError) as error:
            raise CloudRejectedError(f"The cloud sent a profile of {device.device_id} the Edge cannot read: {error}") \
                from error

    @staticmethod
    def _to_profile(device_id: str, resource: dict) -> EnvironmentalProfile:
        thresholds = tuple(
            Threshold(
                Metric(item["metric"]),
                item.get("unit") or "",
                item.get("normalMin"),
                item.get("normalMax"),
                item.get("criticalMin"),
                item.get("criticalMax"),
            )
            for item in resource.get("thresholds") or []
        )
        rules = tuple(
            ActuationRule(Metric(item["metric"]), EnvironmentalState(item["state"]), ActuationAction(item["action"]))
            for item in resource.get("actuationRules") or []
        )
        updated_at = resource.get("updatedAt")
        return EnvironmentalProfile(
            device_id,
            ProfileVersion(resource["version"]),
            thresholds,
            rules,
            None if not updated_at else datetime.fromisoformat(updated_at),
            datetime.now(UTC),
        )
