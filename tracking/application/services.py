"""Application services for the Tracking bounded context.

Application services coordinate Tracking use cases: they authenticate nodes
through the IAM anti-corruption layer, read the cloud through injected
gateways, invoke domain behavior and persist the outcome through repositories.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tracking.application.errors import (
    CloudSynchronizationDisabled,
    ConfigurationNotAvailable,
    DeviceAuthenticationFailed,
    DeviceTypeNotAllowed,
)
from tracking.application.gateways import DeviceRegistryGateway, ProfileGateway, TelemetryGateway
from tracking.domain.entities import (
    ActuationEvent,
    EnvironmentalProfile,
    MonitoredDevice,
    ReportingDevice,
    TelemetryReading,
)
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.repositories import (
    ActuationEventRepository,
    ActuationEventSaveResult,
    EnvironmentalProfileRepository,
    MonitoredDeviceRepository,
    TelemetryReadingRepository,
    TelemetryReadingSaveResult,
)
from tracking.infrastructure.repositories import (
    ActuationEventRepository as ActuationEventRepositoryImpl,
    EnvironmentalProfileRepository as EnvironmentalProfileRepositoryImpl,
    MonitoredDeviceRepository as MonitoredDeviceRepositoryImpl,
    TelemetryReadingRepository as TelemetryReadingRepositoryImpl,
)
from tracking.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer

LOGGER = logging.getLogger(__name__)

# A purge deletes in short transactions with a pause between them, so the nodes
# that send readings meanwhile wait for the single SQLite writer for a moment only.
PURGE_BATCH_SIZE = 2000
PURGE_PAUSE_SECONDS = 0.05
PURGE_INTERVAL = timedelta(hours=1)


class TelemetryApplicationService:
    """Accept the readings and actions of the nodes and keep them pending.

    The service asks the Tracking-to-IAM anti-corruption layer to authenticate
    the node, checks that its type sends that telemetry, creates the records
    through domain behavior and persists them idempotently.
    """

    def __init__(
        self,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        telemetry_reading_repository: TelemetryReadingRepository | None = None,
        actuation_event_repository: ActuationEventRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        """Initialize the service with its context integration, repositories and clock."""
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
        self.telemetry_reading_repository = (
            telemetry_reading_repository or TelemetryReadingRepositoryImpl()
        )
        self.actuation_event_repository = actuation_event_repository or ActuationEventRepositoryImpl()
        self.clock = clock or (lambda: datetime.now(UTC))

    def record_readings(
        self,
        device_id: str,
        api_key: str,
        device_type: DeviceType,
        measured_at: datetime,
        profile_version: int | None,
        values: dict[Metric, float | str],
    ) -> TelemetryReadingSaveResult:
        """Accept the metrics a node measured at one moment.

        Args:
            device_id: Identity of the node sending the readings.
            api_key: Credential presented for the node.
            device_type: Type of node the endpoint receives readings from.
            measured_at: Measurement time supplied by the node.
            profile_version: Version of the profile the node applied, if any.
            values: Value of each metric measured; at least one.

        Returns:
            The stored readings and whether any of them is new.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            DeviceTypeNotAllowed: The node is not of ``device_type``.
            ValueError: The domain rejects a value, the time or an empty reading.
            TelemetryConflict: A reading was already sent with different data.
        """
        reporting_device = self._reporting_device(device_id, api_key, device_type)
        if not values:
            raise ValueError("A reading needs at least one metric")
        received_at = self.clock()
        readings = [
            TelemetryReading.record(
                reporting_device, metric, value, measured_at, profile_version, received_at
            )
            for metric, value in values.items()
        ]
        return self.telemetry_reading_repository.save_idempotently(readings)

    def record_actuation_event(
        self,
        device_id: str,
        api_key: str,
        action: ActuationAction,
        trigger_metric: Metric | None,
        trigger_state: EnvironmentalState | None,
        result: ActuationResult,
        occurred_at: datetime,
        profile_version: int | None,
    ) -> ActuationEventSaveResult:
        """Accept an action a container monitor executed.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            DeviceTypeNotAllowed: The node is not a container monitor.
            ValueError: The domain rejects the cause or the time.
            TelemetryConflict: The action was already sent with different data.
        """
        reporting_device = self._reporting_device(device_id, api_key, DeviceType.CONTAINER_MONITOR)
        event = ActuationEvent.record(
            reporting_device,
            action,
            trigger_metric,
            trigger_state,
            result,
            occurred_at,
            profile_version,
            self.clock(),
        )
        return self.actuation_event_repository.save_idempotently(event)

    def pending_counts(self) -> dict[str, dict[SyncStatus, int]]:
        """Count the stored readings and actions of each synchronization status."""
        return {
            "readings": self.telemetry_reading_repository.count_by_sync_status(),
            "actuation_events": self.actuation_event_repository.count_by_sync_status(),
        }

    def _reporting_device(self, device_id: str, api_key: str, device_type: DeviceType) -> ReportingDevice:
        reporting_device = self.iam_anti_corruption_layer.authenticated_reporting_device(device_id, api_key)
        if reporting_device is None:
            raise DeviceAuthenticationFailed
        if reporting_device.device_type is not device_type:
            raise DeviceTypeNotAllowed
        return reporting_device


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


@dataclass(frozen=True)
class NodeConfiguration:
    """The configuration a node reads from the Edge: its type and the profile it applies."""

    device_type: DeviceType
    profile: EnvironmentalProfile


class EnvironmentalProfileApplicationService:
    """Keep the profile of each node and hand it to the node (TS64, TS58, TS59).

    The profiles are copied from the cloud for every node the registry places
    in an environment; the copy changes only when every profile was read, so
    the nodes keep the last one while the cloud is unreachable.
    """

    def __init__(
        self,
        profile_gateway: ProfileGateway | None,
        iam_anti_corruption_layer: IamAntiCorruptionLayer,
        monitored_device_repository: MonitoredDeviceRepository | None = None,
        environmental_profile_repository: EnvironmentalProfileRepository | None = None,
    ):
        """Initialize the service; without a gateway the cloud synchronization is disabled."""
        self.profile_gateway = profile_gateway
        self.iam_anti_corruption_layer = iam_anti_corruption_layer
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

    def configuration_for(self, device_id: str, api_key: str) -> NodeConfiguration:
        """Return the configuration a node applies.

        Raises:
            DeviceAuthenticationFailed: IAM rejects the node's credentials.
            ConfigurationNotAvailable: The Edge has no profile for the node yet.
        """
        reporting_device = self.iam_anti_corruption_layer.authenticated_reporting_device(device_id, api_key)
        if reporting_device is None:
            raise DeviceAuthenticationFailed
        profile = self.environmental_profile_repository.find_by_device_id(reporting_device.device_id)
        if profile is None:
            raise ConfigurationNotAvailable
        return NodeConfiguration(reporting_device.device_type, profile)

    def profiles(self) -> list[EnvironmentalProfile]:
        """Return the local copy of the profiles, ordered by node."""
        return self.environmental_profile_repository.find_all()
