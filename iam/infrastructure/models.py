"""Peewee ORM models for the IAM bounded context."""

from peewee import CharField, DateTimeField, Model
from shared.infrastructure.database import db

class Device(Model):
    """ORM mapping for the devices table.

Each row represents a registered smart band device and its associated
one-way API-key verifier, which is used to authenticate inbound requests.
Attributes:
    device_id (CharField): Natural primary key — the human-readable
        device identifier (e.g. ``'QualyTrack-001'``).
    credential_verifier (CharField): Salted, one-way verifier paired
        with the device credential.
    device_type (CharField): Node type, ``ENVIRONMENT_MONITOR`` or
            ``CONTAINER_MONITOR``.
    created_at (DateTimeField): UTC timestamp recording when the device
        was first registered.
    revoked_at (DateTimeField): UTC timestamp at which the device was
        revoked, or ``None`` while it remains active.
"""

    device_id  = CharField(primary_key=True)
    credential_verifier = CharField(column_name="api_key")
    device_type = CharField()
    created_at = DateTimeField()
    revoked_at = DateTimeField(null=True)

    class Meta:
        database = db
        table_name = "devices"
