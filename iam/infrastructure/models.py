"""Peewee ORM models for the IAM bounded context."""

from peewee import CharField, DateTimeField, Model
from shared.infrastructure.database import db

class Device(Model):
    """ORM mapping of the ``devices`` table."""

    device_id = CharField(primary_key=True)
    api_key = CharField()
    device_type =CharField()
    created_at = DateTimeField()
    revoked_at = DateTimeField(null=True)

    class Meta:
        database = db
        table_name = "devices"
        