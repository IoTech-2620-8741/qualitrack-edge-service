"""Peewee ORM models for the Sync bounded context.

This module belongs to the infrastructure layer; the domain and application
layers reach it only through the repositories.
"""
from peewee import CharField, DateTimeField, IntegerField, Model

from shared.infrastructure.database import db


class MonitoredDevice(Model):
    """ORM mapping for the ``monitored_devices`` table.

    Each row is a node registered in this Edge, as the cloud registers it for
    the laboratory.

    Attributes:
        device_id (CharField): Natural primary key — the identifier with
            which the cloud registers the node for the Edge (e.g. ``'ENV-001'``).
        device_type (CharField): ``ENVIRONMENTAL_DEVICE`` or ``CONTAINER_MONITOR``.
        laboratory_id (IntegerField): Laboratory of the node in the cloud.
        environment_id (IntegerField): Environment of the node in the cloud,
            or ``None`` while it has none.
        cloud_device_id (IntegerField): Equipment identifier of the node in
            the cloud.
        status (CharField): Operational status of the node in the cloud.
        synced_at (DateTimeField): UTC timestamp of the last read from the cloud.
    """

    device_id = CharField(primary_key=True)
    device_type = CharField()
    laboratory_id = IntegerField()
    environment_id = IntegerField(null=True)
    cloud_device_id = IntegerField()
    status = CharField()
    synced_at = DateTimeField()

    class Meta:
        """Peewee metadata: binds the model to the shared database and names the table."""

        database = db
        table_name = "monitored_devices"
