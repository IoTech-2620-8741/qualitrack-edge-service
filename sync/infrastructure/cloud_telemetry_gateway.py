"""Cloud gateway that sends the pending readings and actions (TS61, TS62, TS63).

It is the anti-corruption layer between Sync and the Tracking & Telemetry
context of the cloud: it builds the nested routes of the QualiTrack Cloud REST
API from the cloud identifiers of each node and translates the records into
its request resources.
"""
import logging

import requests

from shared.infrastructure.cloud_session import CloudRejectedError, CloudSession
from sync.domain.entities import MonitoredDevice
from tracking.domain.entities import ActuationEvent, TelemetryReading
from tracking.domain.enums import DeviceType, SyncStatus
from urllib.parse import quote

LOGGER = logging.getLogger(__name__)

# Answers of the cloud that refuse a record for good: invalid data, or a node
# that is not in that environment any more.
_REJECTING_STATUSES = {400, 404, 409, 422}


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _environment_path(device: MonitoredDevice) -> str:
    return f"/api/v1/laboratories/{device.laboratory_id}/environments/{device.environment_id}"


def _container_monitor_path(device: MonitoredDevice) -> str:
    return f"{_environment_path(device)}/container-monitors/{device.cloud_device_id}"

def _reading_payload(reading: TelemetryReading) -> dict:
    """Build the cloud resource for a telemetry reading."""
    return {
        "metric": reading.metric.value,
        "value": reading.value,
        "textValue": reading.text_value,
        "measuredAt": _iso(reading.measured_at.value),
        "profileVersion": (None if reading.profile_version is None else reading.profile_version.value),
    }


def _actuation_payload(event: ActuationEvent) -> dict:
    """Build the cloud resource for an actuation event."""
    return {
        "action": event.action.value,
        "triggerMetric": (None if event.trigger_metric is None else event.trigger_metric.value),
        "triggerState": (None if event.trigger_state is None else event.trigger_state.value),
        "result": event.result.value,
        "occurredAt": _iso(event.occurred_at.value),
        "profileVersion": (None if event.profile_version is None else event.profile_version.value),
    }


class CloudTelemetryGateway:
    """Send readings and actions to the cloud with the session of the Edge."""

    def __init__(self, cloud_session: CloudSession):
        self._cloud_session = cloud_session

    def send_reading(self, device: MonitoredDevice, reading: TelemetryReading) -> SyncStatus:
        """Synchronize an IoT reading through the authenticated Edge Gateway.

        Uses the Gateway external identifier and the cloud Equipment ID
        of the reporting device to construct the telemetry endpoint.

        The cloud validates that the IoT device belongs to the
        authenticated Gateway's laboratory and environment.

        Args:
            device: IoT device registered in the local cloud registry.
            reading: Pending telemetry reading stored in SQLite.

        Returns:
            SYNCED when the cloud accepts the reading or REJECTED
            when it permanently refuses the measurement.
        """
        gateway_id = quote(self._cloud_session.device_id, safe="")

        path = f"/api/v1/edge-gateways/{gateway_id}" f"/iot-devices/{device.cloud_device_id}" "/telemetry-measurements"

        return self._send(path, _reading_payload(reading),f"reading {reading.id}")

    def send_actuation_event(self, device: MonitoredDevice, event: ActuationEvent) -> SyncStatus:
        """Synchronize a container monitor actuation event through its Edge Gateway.

        Uses the Gateway API key to send an actuation event to the cloud
        Equipment identified by device.cloud_device_id.

        Args:
            device: Container monitor registered in the cloud device registry.
            event: Actuation event stored locally and awaiting synchronization.

        Returns:
            SyncStatus.SYNCED if the cloud accepts the event, or
            SyncStatus.REJECTED if it permanently rejects the event.
        """
        gateway_id = quote(self._cloud_session.device_id, safe="")

        path = f"/api/v1/edge-gateways/{gateway_id}"f"/iot-devices/{device.cloud_device_id}""/actuation-events"

        return self._send(path, _actuation_payload(event),f"action {event.id}")

    def _send(self, path: str, body: dict, record: str) -> SyncStatus:
        response = self._cloud_session.request("POST", path, json=body)
        if response.status_code in (200, 201):
            return SyncStatus.SYNCED
        if response.status_code in _REJECTING_STATUSES:
            LOGGER.warning("The cloud rejected the %s (%s): %s", record, response.status_code, _message(response))
            return SyncStatus.REJECTED
        raise CloudRejectedError(
            f"The cloud answered {response.status_code} to the {record}", response.status_code
        )


def _message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "no details"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("details") or body)
    return str(body)
