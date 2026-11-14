"""Application services for the Sync bounded context.

Sync keeps the local copy of the cloud registry of the nodes of this Edge,
copies from the cloud the profile of each node, sends to the cloud the
readings and actions Tracking keeps pending, and deletes the ones the cloud
answered long ago.
"""
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sync.application.errors import (
    CloudSynchronizationDisabled,
    DeviceEnrollmentNotAllowed,
)
from sync.application.gateways import DeviceRegistryGateway, ProfileGateway, TelemetryGateway
from sync.domain.entities import MonitoredDevice
from sync.domain.enums import DeviceStatus
from sync.domain.repositories import MonitoredDeviceRepository
from sync.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from tracking.domain.entities import EnvironmentalProfile
from tracking.domain.enums import DeviceType, SyncStatus
from tracking.domain.repositories import (
    ActuationEventRepository,
    EnvironmentalProfileRepository,
    TelemetryReadingRepository,
)

LOGGER = logging.getLogger(__name__)

# A purge deletes in short transactions with a pause between them, so the nodes
# that send readings meanwhile wait for the single SQLite writer for a moment only.
PURGE_BATCH_SIZE = 2000
PURGE_PAUSE_SECONDS = 0.05
PURGE_INTERVAL = timedelta(hours=1)

# Records are sent this many at a time: the cloud answers a repeated record
# with 200, so sending in parallel is safe, and an outage costs at most one
# window of timeouts before the synchronization stops.
SEND_WORKERS = 4


@dataclass(frozen=True)
class RegistrySynchronization:
    """Outcome of one read of the cloud registry.

    Attributes:
        devices: IoT devices the cloud assigns to this Edge Gateway, now in the
            local copy, also the ones not yet registered in the Edge.
        not_in_cloud: Nodes registered in this Edge that the cloud does not
            assign to this Edge Gateway; their records wait until it does.
    """

    devices: list[MonitoredDevice]
    not_in_cloud: list[str]


class DeviceRegistryApplicationService:
    """Keep the local copy of the IoT devices the cloud assigns to this Edge Gateway.

    The cloud is the source of truth of the registry and decides which devices
    this Edge Gateway serves: up to 15 of its environment (ADR-0014). The Edge
    caches every one of them, also before they are registered in its IAM, so a
    node can enroll when the cached registry authorizes it. The local copy
    gives the Edge the cloud identifiers it synchronizes telemetry to, and it
    stays available while the cloud is unreachable.
    """

    def __init__(
        self,
        registry_gateway: DeviceRegistryGateway | None,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        monitored_device_repository: MonitoredDeviceRepository,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled.

        ``create_app`` builds the repository (ADR-0015).
        """
        self.registry_gateway = registry_gateway
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.monitored_device_repository = monitored_device_repository

    def synchronize_registry(self) -> RegistrySynchronization:
        """Synchronize all IoT devices assigned to this Edge Gateway.

        The cloud registry is cached independently from local IAM.
        Devices can therefore be discovered before enrollment.

        Returns:
            The synchronized devices and locally registered devices
            no longer found in the cloud registry.
        """
        if self.registry_gateway is None:
            raise CloudSynchronizationDisabled

        devices = self.registry_gateway.fetch_devices()

        # Cache all cloud devices, including those not yet enrolled in IAM.
        self.monitored_device_repository.replace_all(devices)

        registered = self.iam_anti_corruption_layer.registered_device_ids()
        in_cloud = {device.device_id for device in devices}

        return RegistrySynchronization(devices, sorted(registered - in_cloud))

    def monitored_devices(self) -> list[MonitoredDevice]:
        """Return the local copy of the registry, ordered by identifier."""
        return self.monitored_device_repository.find_all()

    def enroll_device(
        self,
        device_id: str,
        device_type: DeviceType,
    ) -> str:
        """Enroll an IoT device authorized by the cached cloud registry.

        The device must match the identifier and type registered
        in the cloud, be operational, and have an assigned environment.

        Existing local credentials are never regenerated.

        Args:
            device_id: Identifier presented by the ESP32.
            device_type: Device type presented by the ESP32.

        Returns:
            Newly generated local IAM API key.

        Raises:
            DeviceEnrollmentNotAllowed: If the device is not
                authorized by the cached cloud registry.
            DeviceAlreadyEnrolled: If local IAM already knows
                this device.
        """
        device = self.monitored_device_repository.find_by_device_id(
            device_id
        )

        if (
            device is None
            or device.device_type is not device_type
            or not device.is_in_environment
            or device.status is not DeviceStatus.OPERATIONAL
        ):
            raise DeviceEnrollmentNotAllowed

        return self.iam_anti_corruption_layer.enroll_device(
            device_id,
            device_type,
        )


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

    Records are sent oldest first, ``SEND_WORKERS`` at a time, each to the
    cloud route of its node, which the local copy of the cloud registry
    provides. A record whose node the registry does not place in an
    environment is not read: it waits for a later synchronization and is only
    counted. When the cloud cannot answer, the synchronization stops after the
    current window and the remaining records stay pending for the next cycle.
    """

    def __init__(
        self,
        telemetry_gateway: TelemetryGateway | None,
        monitored_device_repository: MonitoredDeviceRepository,
        telemetry_reading_repository: TelemetryReadingRepository,
        actuation_event_repository: ActuationEventRepository,
        batch_size: int = 100,
        synced_retention: timedelta = timedelta(days=7),
        rejected_retention: timedelta = timedelta(days=90),
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled.

        Sync is a conformist of Tracking (ADR-0015): it receives the repository
        contracts of Tracking, which ``create_app`` builds, instead of creating
        the implementations of another context.
        """
        self.telemetry_gateway = telemetry_gateway
        self.monitored_device_repository = monitored_device_repository
        self.telemetry_reading_repository = telemetry_reading_repository
        self.actuation_event_repository = actuation_event_repository
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
        placed = sorted(device_id for device_id, device in devices.items() if device.is_in_environment)
        waiting = repository.count_pending_outside(placed)
        synced = rejected = 0
        last_id = 0
        with ThreadPoolExecutor(max_workers=SEND_WORKERS, thread_name_prefix="cloud-send") as pool:
            while records := repository.find_pending(last_id, self.batch_size, placed):
                last_id = records[-1].id
                for start in range(0, len(records), SEND_WORKERS):
                    window = records[start:start + SEND_WORKERS]
                    futures = [pool.submit(send, devices[record.device_id], record) for record in window]
                    failure = None
                    # The answers are saved in this thread, the one with the database connection.
                    for record, future in zip(window, futures):
                        try:
                            answer = future.result()
                        except Exception as error:  # noqa: BLE001 - re-raised once the window is saved
                            failure = failure or error
                            continue
                        repository.save_sync_status(record.synchronized(answer))
                        if answer is SyncStatus.SYNCED:
                            synced += 1
                        else:
                            rejected += 1
                    if failure is not None:
                        raise failure
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
        monitored_device_repository: MonitoredDeviceRepository,
        environmental_profile_repository: EnvironmentalProfileRepository,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled.

        The copy of the profiles belongs to Tracking, which hands it to the
        nodes; ``create_app`` builds its repository (ADR-0015).
        """
        self.profile_gateway = profile_gateway
        self.monitored_device_repository = monitored_device_repository
        self.environmental_profile_repository = environmental_profile_repository

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
