"""Repository implementation for IAM device aggregates."""

import peewee

from iam.domain.credentials import ApiCredential
from iam.domain.entities import Device
from iam.domain.enums import DeviceType
from iam.infrastructure.models import Device as DeviceModel


class DeviceRepository:
    """Persist and reconstruct IAM device aggregates."""

    def find_by_device_id(self, device_id: str) -> Device | None:
        """Load a device by identity without embedding credential policy in SQL."""
        try:
            row = DeviceModel.get(DeviceModel.device_id == device_id)
        except peewee.DoesNotExist:
            return None
        return self._to_entity(row)

    @staticmethod
    def _to_entity(row: DeviceModel) -> Device:
        return Device(
            row.device_id,
            ApiCredential.from_encoded(row.credential_verifier),
            DeviceType(row.device_type),
            row.created_at,
            row.revoked_at,
        )

    def save(self, device: Device) -> Device:
        """Insert or update the persisted aggregate state."""
        values = {
            "credential_verifier": device.credential.encode(),
            "device_type": device.device_type.value,
            "created_at": device.created_at,
            "revoked_at": device.revoked_at,
        }
        with DeviceModel._meta.database.atomic():
            row = DeviceModel.get_or_none(DeviceModel.device_id == device.device_id)
            if row is None:
                DeviceModel.create(device_id=device.device_id, **values)
            else:
                DeviceModel.update(**values).where(
                    DeviceModel.device_id == device.device_id
                ).execute()
        return device

    def get_or_create_test_device(
        self,
        device_id: str,
        api_key: str,
        device_type: DeviceType = DeviceType.ENVIRONMENTAL_DEVICE,
    ) -> Device:
        """Retrieve the configured development device or register it if absent."""
        row = DeviceModel.get_or_none(DeviceModel.device_id == device_id)
        if row is not None:
            return self._to_entity(row)
        new_device = Device.register(device_id, api_key, device_type)
        row, _ = DeviceModel.get_or_create(
            device_id=device_id,
            defaults={
                "credential_verifier": new_device.credential.encode(),
                "device_type": new_device.device_type.value,
                "created_at": new_device.created_at,
                "revoked_at": None,
            },
        )
        return self._to_entity(row)