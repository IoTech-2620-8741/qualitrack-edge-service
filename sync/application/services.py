"""Application services for the Sync bounded context.

Sync copies from the cloud the profile of each node, sends to the cloud the
readings and actions Tracking keeps pending, and deletes the ones the cloud
answered long ago.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sync.application.gateways import ProfileGateway, TelemetryGateway
from tracking.application.errors import CloudSynchronizationDisabled
from tracking.domain.entities import EnvironmentalProfile, MonitoredDevice
from tracking.domain.enums import SyncStatus
from tracking.domain.repositories import (
    ActuationEventRepository,
    EnvironmentalProfileRepository,
    MonitoredDeviceRepository,
    TelemetryReadingRepository,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
    MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)

LOGGER = logging.getLogger(__name__)

# A purge deletes in short transactions with a pause between them, so the nodes
# that send readings meanwhile wait for the single SQLite writer for a moment only.
PURGE_BATCH_SIZE = 2000
PURGE_PAUSE_SECONDS = 0.05
PURGE_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class SynchronizationSummary:
    """Outcome of one synchronization of a kind of record.

    Attributes:
        synced: Records the cloud accepted.
        rejected: Records the cloud refused for good.
        waiting: Records that stay pending because the cloud registry does not
            place their node in an environment yet.
    """

    synced: int = 0
    rejected: int = 0
    waiting: int = 0


class TelemetrySynchronizationApplicationService:
    """Send the pending readings and actions to the cloud

    Records are sent oldest first, each to the cloud route of its node, which
    the local copy of the cloud registry provides. A record whose node the
    registry does not place in an environment waits for a later
    synchronization. When the cloud cannot answer, the synchronization stops
    and the remaining records stay pending for the next cycle.
    """

    def __init__(
        self,
        telemetry_gateway: TelemetryGateway | None,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
        telemetry_reading_repository: TelemetryReadingRepository | None = None,
        actuation_event_repository: ActuationEventRepository | None = None,
        batch_size: int = 100,
        synced_retention: timedelta = timedelta(days=7),
        rejected_retention: timedelta = timedelta(days=90),
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.telemetry_gateway = telemetry_gateway
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )
        self.telemetry_reading_repository = (
            telemetry_reading_repository or TelemetryReadingRepositoryImpl()
        )
        self.actuation_event_repository = actuation_event_repository or ActuationEventRepositoryImpl()
        self.batch_size = batch_size
        self.synced_retention = synced_retention
        self.rejected_retention = rejected_retention
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep
        # shortcut: the last purge is remembered in memory only, so a restart purges once more; harmless.
        self._last_purge: datetime | None = None

    def synchronize_pending(self) -> dict[str, SynchronizationSummary]:
        """Send every pending reading and action whose node the registry places.

        Returns:
            The summary of the readings and of the actions.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                could not answer; the records sent before keep their answer.
        """
        if self.telemetry_gateway is None:
            raise CloudSynchronizationDisabled
        devices = {device.device_id: device for device in self.monitored_device_repository.find_all()}
        return {
            "readings": self._synchronize(
                self.telemetry_reading_repository, devices, self.telemetry_gateway.send_reading
            ),
            "actuation_events": self._synchronize(
                self.actuation_event_repository, devices, self.telemetry_gateway.send_actuation_event
            ),
        }

    def pending_counts(self) -> dict[str, dict[SyncStatus, int]]:
        """Count the stored readings and actions of each synchronization status."""
        return {
            "readings": self.telemetry_reading_repository.count_by_sync_status(),
            "actuation_events": self.actuation_event_repository.count_by_sync_status(),
        }

    def purge(self) -> int:
        """Delete the readings and actions the cloud answered long ago.

        Synchronized records go after ``synced_retention`` and rejected ones
        after ``rejected_retention``, counted from the moment the Edge received
        them. Pending records are never deleted. It runs at most once an hour.

        Returns:
            How many records were deleted.
        """
        now = self.clock()
        if self._last_purge is not None and now - self._last_purge < PURGE_INTERVAL:
            return 0
        deleted = 0
        for status, retention in (
            (SyncStatus.SYNCED, self.synced_retention),
            (SyncStatus.REJECTED, self.rejected_retention),
        ):
            for repository in (self.telemetry_reading_repository, self.actuation_event_repository):
                deleted += self._delete_in_batches(repository, status, now - retention)
        self._last_purge = now
        if deleted:
            LOGGER.info("Deleted %d old synchronized or rejected records", deleted)
        return deleted

    def _delete_in_batches(self, repository, status: SyncStatus, cutoff: datetime) -> int:
        deleted = 0
        while True:
            count = repository.delete_before(status, cutoff, PURGE_BATCH_SIZE)
            deleted += count
            if count < PURGE_BATCH_SIZE:
                return deleted
            self.sleep(PURGE_PAUSE_SECONDS)

    def _synchronize(self, repository, devices: dict[str, MonitoredDevice], send) -> SynchronizationSummary:
        synced = rejected = waiting = 0
        last_id = 0
        while records := repository.find_pending(last_id, self.batch_size):
            for record in records:
                last_id = record.id
                device = devices.get(record.device_id)
                if device is None or not device.is_in_environment:
                    waiting += 1
                    continue
                answer = send(device, record)
                repository.save_sync_status(record.synchronized(answer))
                if answer is SyncStatus.SYNCED:
                    synced += 1
                else:
                    rejected += 1
        return SynchronizationSummary(synced, rejected, waiting)


class ProfileSynchronizationApplicationService:
    """Keep the local copy of the profile of each node (TS64).

    The profiles are copied from the cloud for every node the registry places
    in an environment; the copy changes only when every profile was read, so
    the nodes keep the last one while the cloud is unreachable.
    """

    def __init__(
        self,
        profile_gateway: ProfileGateway | None,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
        environmental_profile_repository: EnvironmentalProfileRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.profile_gateway = profile_gateway
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )
        self.environmental_profile_repository = (
            environmental_profile_repository or EnvironmentalProfileRepositoryImpl()
        )

    def synchronize_profiles(self) -> list[EnvironmentalProfile]:
        """Copy the profile of every node the registry places in an environment.

        A node without a profile in the cloud loses its local copy, as does a
        node the registry no longer has.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                could not answer; the previous copy is kept.
        """
        if self.profile_gateway is None:
            raise CloudSynchronizationDisabled
        profiles = []
        for device in self.monitored_device_repository.find_all():
            if not device.is_in_environment:
                continue
            profile = self.profile_gateway.fetch_profile(device)
            if profile is not None:
                profiles.append(profile)
        self.environmental_profile_repository.replace_all(profiles)
        return profiles
