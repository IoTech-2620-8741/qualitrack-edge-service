"""Application services for the Sync bounded context.

Sync keeps the local copy of the cloud registry of the nodes of this Edge,
copies from the cloud the profile of each node, sends to the cloud the
readings and actions Tracking keeps pending, and deletes the ones the cloud
answered long ago.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sync.application.errors import CloudSynchronizationDisabled
from sync.application.gateways import DeviceRegistryGateway, ProfileGateway, TelemetryGateway
from sync.domain.entities import MonitoredDevice
from sync.domain.repositories import MonitoredDeviceRepository
from sync.infrastructure.repositories import MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl
from sync.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from tracking.domain.entities import EnvironmentalProfile
from tracking.domain.enums import SyncStatus
from tracking.domain.repositories import (
    ActuationEventRepository,
    EnvironmentalProfileRepository,
    TelemetryReadingRepository,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)

LOGGER = logging.getLogger(__name__)

# A purge deletes in short transactions with a pause between them, so the nodes
# that send readings meanwhile wait for the single SQLite writer for a moment only.
PURGE_BATCH_SIZE = 2000
PURGE_PAUSE_SECONDS = 0.05
PURGE_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class RegistrySynchronization:
    """Outcome of one read of the cloud registry.

    Attributes:
        devices: Nodes of this Edge the cloud registers, now in the local copy.
        not_in_cloud: Nodes registered in this Edge that the cloud does not
            register as IoT devices with that ``sensorExternalId``; their
            records wait until the cloud registers them.
    """

    devices: list[MonitoredDevice]
    not_in_cloud: list[str]


class DeviceRegistryApplicationService:
    """Keep the local copy of the nodes of this Edge, as the cloud registers them.

    The cloud is the source of truth of the registry, but the laboratory may
    have nodes in environments other stations serve. The Edge keeps only the
    nodes registered in it with ``flask devices register``, which IAM knows,
    so it reads the profiles of its own nodes only. The local copy gives the
    Edge the cloud identifiers it synchronizes telemetry to, and it stays
    available while the cloud is unreachable.
    """

    def __init__(
        self,
        registry_gateway: DeviceRegistryGateway | None,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.registry_gateway = registry_gateway
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.monitored_device_repository = (
            monitored_device_repository or MonitoredDeviceRepositoryImpl()
        )

    def synchronize_registry(self) -> RegistrySynchronization:
        """Replace the local copy with the nodes of this Edge the cloud registers now.

        The local copy changes only when the cloud answers, so a failed
        synchronization keeps the previous one.

        Raises:
            CloudSynchronizationDisabled: The Edge has no connection with the
                cloud configured.
            shared.infrastructure.cloud_session.CloudRequestError: The cloud
                did not answer or rejected the request.
        """
        if self.registry_gateway is None:
            raise CloudSynchronizationDisabled
        registered = self.iam_anti_corruption_layer.registered_device_ids()
        devices = [device for device in self.registry_gateway.fetch_devices() if device.device_id in registered]
        self.monitored_device_repository.replace_all(devices)
        in_cloud = {device.device_id for device in devices}
        return RegistrySynchronization(devices, sorted(registered - in_cloud))

    def monitored_devices(self) -> list[MonitoredDevice]:
        """Return the local copy of the registry, ordered by identifier."""
        return self.monitored_device_repository.find_all()

    def find_monitored_device(self, device_id: str) -> MonitoredDevice | None:
        """Return the cloud registration of one node, if the cloud registers it."""
        return self.monitored_device_repository.find_by_device_id(device_id)


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
