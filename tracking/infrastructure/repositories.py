"""Repository implementations for the Tracking bounded context.

They map between the Tracking domain entities and the Peewee models, keeping
the domain layer free of infrastructure concerns.
"""

from datetime import UTC, datetime

import peewee

from tracking.domain.entities import ActuationEvent, MonitoredDevice, TelemetryReading
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceStatus,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.errors import TelemetryConflict
from tracking.domain.repositories import ActuationEventSaveResult, TelemetryReadingSaveResult
from tracking.domain.value_objects import ObservationTime, ProfileVersion
from tracking.infrastructure.models import ActuationEvent as ActuationEventModel
from tracking.infrastructure.models import MonitoredDevice as MonitoredDeviceModel
from tracking.infrastructure.models import TelemetryReading as TelemetryReadingModel


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _count_by_sync_status(model) -> dict[SyncStatus, int]:
    counts = {status: 0 for status in SyncStatus}
    query = model.select(model.sync_status, peewee.fn.COUNT(model.id).alias("total")).group_by(model.sync_status)
    for row in query:
        counts[SyncStatus(row.sync_status)] = row.total
    return counts


class TelemetryReadingRepository:
    """Persist the readings the Edge received, once per node, metric and time."""

    def save_idempotently(self, readings: list[TelemetryReading]) -> TelemetryReadingSaveResult:
        """Persist new readings and return the stored ones that a retry repeats.

        The unique index on node, metric and time protects concurrent retries;
        comparing the data ensures a retry cannot change a stored reading.
        """
        database = TelemetryReadingModel._meta.database
        try:
            with database.atomic("IMMEDIATE"):
                return self._save(readings)
        except peewee.IntegrityError:
            with database.atomic("IMMEDIATE"):
                return self._save(readings)

    def find_pending(self, after_id: int, limit: int) -> list[TelemetryReading]:
        """Load up to ``limit`` pending readings stored after ``after_id``, oldest first."""
        query = (
            TelemetryReadingModel.select()
            .where(
                (TelemetryReadingModel.sync_status == SyncStatus.PENDING.value)
                & (TelemetryReadingModel.id > after_id)
            )
            .order_by(TelemetryReadingModel.id)
            .limit(limit)
        )
        return [self._to_entity(row) for row in query]

    def save_sync_status(self, reading: TelemetryReading) -> None:
        """Persist the synchronization status of a stored reading."""
        TelemetryReadingModel.update(sync_status=reading.sync_status.value).where(
            TelemetryReadingModel.id == reading.id
        ).execute()

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored readings of each synchronization status."""
        return _count_by_sync_status(TelemetryReadingModel)

    def _save(self, readings: list[TelemetryReading]) -> TelemetryReadingSaveResult:
        stored, created = [], False
        for reading in readings:
            row = TelemetryReadingModel.get_or_none(
                (TelemetryReadingModel.device_id == reading.device_id)
                & (TelemetryReadingModel.metric == reading.metric.value)
                & (TelemetryReadingModel.measured_at == reading.measured_at.value)
            )
            if row is not None:
                existing = self._to_entity(row)
                if not existing.is_same_observation(reading):
                    raise TelemetryConflict
                stored.append(existing)
                continue
            row = TelemetryReadingModel.create(
                device_id=reading.device_id,
                metric=reading.metric.value,
                value=reading.value,
                text_value=reading.text_value,
                measured_at=reading.measured_at.value,
                profile_version=None if reading.profile_version is None else reading.profile_version.value,
                received_at=reading.received_at,
                sync_status=reading.sync_status.value,
            )
            stored.append(reading.with_persistence_id(row.id))
            created = True
        return TelemetryReadingSaveResult(stored, created)

    @staticmethod
    def _to_entity(row: TelemetryReadingModel) -> TelemetryReading:
        return TelemetryReading(
            row.device_id,
            Metric(row.metric),
            row.value,
            row.text_value,
            ObservationTime(_as_utc(row.measured_at)),
            ProfileVersion.from_optional(row.profile_version),
            _as_utc(row.received_at),
            SyncStatus(row.sync_status),
            row.id,
        )


class ActuationEventRepository:
    """Persist the actions the Edge received, once per node, action and time."""

    def save_idempotently(self, event: ActuationEvent) -> ActuationEventSaveResult:
        """Persist a new action or return the stored one that a retry repeats."""
        database = ActuationEventModel._meta.database
        try:
            with database.atomic("IMMEDIATE"):
                return self._save(event)
        except peewee.IntegrityError:
            with database.atomic("IMMEDIATE"):
                return self._save(event)

    def find_pending(self, after_id: int, limit: int) -> list[ActuationEvent]:
        """Load up to ``limit`` pending actions stored after ``after_id``, oldest first."""
        query = (
            ActuationEventModel.select()
            .where(
                (ActuationEventModel.sync_status == SyncStatus.PENDING.value)
                & (ActuationEventModel.id > after_id)
            )
            .order_by(ActuationEventModel.id)
            .limit(limit)
        )
        return [self._to_entity(row) for row in query]

    def save_sync_status(self, event: ActuationEvent) -> None:
        """Persist the synchronization status of a stored action."""
        ActuationEventModel.update(sync_status=event.sync_status.value).where(
            ActuationEventModel.id == event.id
        ).execute()

    def count_by_sync_status(self) -> dict[SyncStatus, int]:
        """Count the stored actions of each synchronization status."""
        return _count_by_sync_status(ActuationEventModel)

    def _save(self, event: ActuationEvent) -> ActuationEventSaveResult:
        row = ActuationEventModel.get_or_none(
            (ActuationEventModel.device_id == event.device_id)
            & (ActuationEventModel.action == event.action.value)
            & (ActuationEventModel.occurred_at == event.occurred_at.value)
        )
        if row is not None:
            existing = self._to_entity(row)
            if not existing.is_same_observation(event):
                raise TelemetryConflict
            return ActuationEventSaveResult(existing, False)
        row = ActuationEventModel.create(
            device_id=event.device_id,
            action=event.action.value,
            trigger_metric=None if event.trigger_metric is None else event.trigger_metric.value,
            trigger_state=None if event.trigger_state is None else event.trigger_state.value,
            result=event.result.value,
            occurred_at=event.occurred_at.value,
            profile_version=None if event.profile_version is None else event.profile_version.value,
            received_at=event.received_at,
            sync_status=event.sync_status.value,
        )
        return ActuationEventSaveResult(event.with_persistence_id(row.id), True)

    @staticmethod
    def _to_entity(row: ActuationEventModel) -> ActuationEvent:
        return ActuationEvent(
            row.device_id,
            ActuationAction(row.action),
            None if row.trigger_metric is None else Metric(row.trigger_metric),
            None if row.trigger_state is None else EnvironmentalState(row.trigger_state),
            ActuationResult(row.result),
            ObservationTime(_as_utc(row.occurred_at)),
            ProfileVersion.from_optional(row.profile_version),
            _as_utc(row.received_at),
            SyncStatus(row.sync_status),
            row.id,
        )


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
