"""Peewee ORM models for the Tracking bounded context.

This module belongs to the infrastructure layer; the domain and application
layers reach it only through the repositories.
"""
from peewee import AutoField, CharField, DateTimeField, FloatField, IntegerField, Model, TextField

from shared.infrastructure.database import db


class EnvironmentalProfile(Model):
    """ORM mapping for the ``environmental_profiles`` table.

    Each row is the profile a node applies, copied from the cloud (TS64).

    Attributes:
        device_id (CharField): Natural primary key — the node that applies
            the profile.
        version (IntegerField): Version of the profile in the cloud.
        thresholds (TextField): JSON list of the thresholds of the profile.
        actuation_rules (TextField): JSON list of the actuation rules.
        cloud_updated_at (DateTimeField): UTC time of the last change in the
            cloud, or ``None`` when unknown.
        synced_at (DateTimeField): UTC time the Edge copied the profile.
    """

    device_id = CharField(primary_key=True)
    version = IntegerField()
    thresholds = TextField()
    actuation_rules = TextField()
    cloud_updated_at = DateTimeField(null=True)
    synced_at = DateTimeField()

    class Meta:
        """Peewee metadata: binds the model to the shared database and names the table."""

        database = db
        table_name = "environmental_profiles"


class TelemetryReading(Model):
    """ORM mapping for the ``telemetry_readings`` table.

    Each row is one metric a node measured at a moment, kept until the cloud
    accepts it (TS60). A node, metric and measurement time identify a reading.

    Attributes:
        id (AutoField): Auto-incrementing integer primary key.
        device_id (CharField): Node that measured the value. Stored as a plain
            string (not a foreign key) to keep the bounded contexts loosely
            coupled.
        metric (CharField): Variable measured.
        value (FloatField): Numeric value, or ``None`` for text metrics.
        text_value (CharField): Text read (an RFID tag), or ``None``.
        measured_at (DateTimeField): UTC measurement time supplied by the node.
        profile_version (IntegerField): Profile version the node applied, or ``None``.
        received_at (DateTimeField): UTC time the Edge received the reading.
        sync_status (CharField): ``PENDING``, ``SYNCED`` or ``REJECTED``.
    """

    id = AutoField()
    device_id = CharField()
    metric = CharField()
    value = FloatField(null=True)
    text_value = CharField(null=True, max_length=120)
    measured_at = DateTimeField()
    profile_version = IntegerField(null=True)
    received_at = DateTimeField()
    sync_status = CharField(index=True)

    class Meta:
        """Peewee metadata: binds the model to the shared database and names the table."""

        database = db
        table_name = "telemetry_readings"
        indexes = ((("device_id", "metric", "measured_at"), True),)


class ActuationEvent(Model):
    """ORM mapping for the ``actuation_events`` table.

    Each row is an action a container monitor executed, kept until the cloud
    accepts it (TS60). A node, action and time identify an action.

    Attributes:
        id (AutoField): Auto-incrementing integer primary key.
        device_id (CharField): Container monitor that executed the action.
        action (CharField): Action executed.
        trigger_metric (CharField): Metric that caused the action, or ``None``.
        trigger_state (CharField): State that caused the action, or ``None``.
        result (CharField): ``EXECUTED`` or ``FAILED``.
        occurred_at (DateTimeField): UTC time supplied by the node.
        profile_version (IntegerField): Profile version the node applied, or ``None``.
        received_at (DateTimeField): UTC time the Edge received the action.
        sync_status (CharField): ``PENDING``, ``SYNCED`` or ``REJECTED``.
    """

    id = AutoField()
    device_id = CharField()
    action = CharField()
    trigger_metric = CharField(null=True)
    trigger_state = CharField(null=True)
    result = CharField()
    occurred_at = DateTimeField()
    profile_version = IntegerField(null=True)
    received_at = DateTimeField()
    sync_status = CharField(index=True)

    class Meta:
        """Peewee metadata: binds the model to the shared database and names the table."""

        database = db
        table_name = "actuation_events"
        indexes = ((("device_id", "action", "occurred_at"), True),)
