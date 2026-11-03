"""Repository implementations for the Tracking bounded context."""

from datetime import UTC

from tracking.domain.entities import MonitoredDevice
from tracking.domain.enums import DeviceStatus, DeviceType
from tracking.infrastructure.models import MonitoredDevice as MonitoredDeviceModel


class MonitoredDeviceRepository:
    """Persist and reconstruct the local copy of the cloud device registry."""

    def find_by_device_id(self, device_id: str) -> MonitoredDevice | None:
        """Load a monitored device by the identifier of its node."""
        row = MonitoredDeviceModel.get_or_none(MonitoredDeviceModel.device_id == device_id)
        return None if row is None else self._to_entity(row)

    def find_all(self) -> list[MonitoredDevice]:
        """Load every monitored device, ordered by identifier."""
        return [
            self._to_entity(row)
            for row in MonitoredDeviceModel.select().order_by(MonitoredDeviceModel.device_id)
        ]

    def replace_all(self, devices: list[MonitoredDevice]) -> None:
        """Make the table equal to the given devices in one transaction."""
        rows = [
            {
                "device_id": device.device_id,
                "device_type": device.device_type.value,
                "laboratory_id": device.laboratory_id,
                "environment_id": device.environment_id,
                "cloud_device_id": device.cloud_device_id,
                "status": device.status.value,
                "synced_at": device.synced_at,
            }
            for device in devices
        ]
        with MonitoredDeviceModel._meta.database.atomic():
            MonitoredDeviceModel.delete().where(
                MonitoredDeviceModel.device_id.not_in([row["device_id"] for row in rows])
            ).execute()
            if rows:
                MonitoredDeviceModel.replace_many(rows).execute()

    @staticmethod
    def _to_entity(row: MonitoredDeviceModel) -> MonitoredDevice:
        synced_at = row.synced_at
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=UTC)
        return MonitoredDevice(
            row.device_id,
            DeviceType(row.device_type),
            row.laboratory_id,
            row.environment_id,
            row.cloud_device_id,
            DeviceStatus(row.status),
            synced_at,
        )
