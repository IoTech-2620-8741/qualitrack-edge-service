"""IAM aggregatee roots"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from iam.domain import credentials
from iam.domain.credentials import ApiCredential
from iam.domain.enums import DeviceType

@dataclass(frozen=True)
class Device:
    """Registered device aggregate with credential and access lifecycle rules."""

    devices_id: str
    credential: ApiCredential
    device_type: DeviceType
    create_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.devices_id, str) or not self.devices_id.strip():
            raise ValueError("Devices identifier cannot be empty")
        if not isinstance(self.credential, ApiCredential):
            raise ValueError("Devices requires an API credential verifier")
        if not isinstance(self.device_type, DeviceType):
            raise ValueError("Devices required a valid device type")
        if not isinstance(self.create_at,datetime):
            raise ValueError("Device creation time must be a datatime")
        if self.create_at.tzinfo is None or self.create_at.utcoffset() is None:
            raise ValueError("Device creation time must include a timezone")
        object.__setattr__(self, "created_at", self.create_at.astimezone(UTC))
        if self.revoked_at is not None:
            if not isinstance(self.revoked_at, datetime):
                raise TypeError("Revocation time must be a datetime")
            if self.revoked_at.tzinfo is None or self.revoked_at.utcoffset() is None:
                raise ValueError("Revocation time must include a timezone")
            object.__setattr__(self, "revoked_at", self.revoked_at.astimezone(UTC))
            if self.revoked_at < self.create_at:
                raise ValueError("Revocation cannot predate device registration")

    @classmethod
    def register(
            cls,
            device_id: str,
            api_key: str,
            device_type: DeviceType,
            created_at: datetime | None = None,
    ) -> "Device":
        """Register a device and protect its API key with a one-way verifier"""
        return cls(
            device_id,
            ApiCredential.create(api_key),
            device_type,
            created_at or datetime.now(UTC),
        )

    def authenticate(self, api_key: str) -> bool:
        """Accept credentials only while this device is active."""
        return self.revoked_at is None and self.credential.matches(api_key)

    def revoke(self, at: datetime | None = None) -> "Device":
        """Return a revoked device; a revoked device cannot be revoked twice."""
        if self.revoked_at is not None:
            raise ValueError("Device is already revoked")
        revoked_at = at or datetime.now(UTC)
        if revoked_at.tzinfo is None or revoked_at.utcoffset() is None:
            raise ValueError("Revocation time must include a timezone")
        return replace(self, revoked_at=revoked_at)

    def rotate_api_key(self, new_api_key: str) -> "Device":
        """Return an active device with a newly protected API key"""
        if self.revoked_at is not None:
            raise ValueError("Cannot rotate credentials for a revoked device")
        return replace(self, credential=ApiCredential.create(new_api_key))

