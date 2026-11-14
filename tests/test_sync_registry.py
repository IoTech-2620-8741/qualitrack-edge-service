import threading
import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

from app import create_app
from iam.infrastructure.models import Device as DeviceModel
from shared.infrastructure.cloud_session import CloudRejectedError, CloudUnavailableError
from shared.infrastructure.cloud_sync_scheduler import CloudSyncCycle, CloudSyncScheduler
from sync.application.errors import CloudSynchronizationDisabled
from sync.application.services import DeviceRegistryApplicationService
from sync.domain.entities import MonitoredDevice
from sync.domain.enums import DeviceStatus
from sync.infrastructure.cloud_registry_gateway import CloudDeviceRegistryGateway
from sync.infrastructure.repositories import MonitoredDeviceRepository
from sync.integration.iam_anti_corruption_layer import IamAntiCorruptionLayer
from tracking.domain.enums import DeviceType

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def monitored(device_id, device_type=DeviceType.ENVIRONMENTAL_DEVICE, environment_id=2, cloud_device_id=15):
    return MonitoredDevice(
        device_id, device_type, 7, environment_id, cloud_device_id, DeviceStatus.OPERATIONAL, NOW
    )


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body



class FakeCloudSession:
    def __init__(self, response):
        self.response = response
        self.answers = {}
        self.requests = []
        self.device_id = "EDGE-001"

    def request(self, method, path, json=None):
        self.requests.append((method, path))
        return self.answers.get(path, self.response)



def equipment(**overrides):
    return {
        "id": 15,
        "laboratoryId": 7,
        "environmentId": 2,
        "name": "Space monitor",
        "type": "ENVIRONMENTAL_DEVICE",
        "model": "ESP32-WROOM-32",
        "serialNumber": "24:6F:28:AA:10:01",
        "status": "OPERATIONAL",
        "deviceType": "ENVIRONMENTAL_DEVICE",
        "sensorExternalId": "ENV-001",
        "firmwareVersion": "1.0.3",
        **overrides,
    }


class MonitoredDeviceTestCase(unittest.TestCase):
    def test_normalizes_the_synchronization_time_to_utc(self):
        device = MonitoredDevice(
            "ENV-001", DeviceType.ENVIRONMENTAL_DEVICE, 7, None, 15, DeviceStatus.OPERATIONAL,
            datetime(2026, 11, 3, 7, 0, tzinfo=timezone(timedelta(hours=-5))),
        )

        self.assertEqual(device.synced_at, NOW)
        self.assertFalse(device.is_in_environment)

    def test_rejects_invalid_cloud_registrations(self):
        for arguments in (
            (" ", DeviceType.ENVIRONMENTAL_DEVICE, 7, 2, 15, DeviceStatus.OPERATIONAL, NOW),
            ("ENV-001", "ENVIRONMENTAL_DEVICE", 7, 2, 15, DeviceStatus.OPERATIONAL, NOW),
            ("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE, 0, 2, 15, DeviceStatus.OPERATIONAL, NOW),
            ("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE, 7, True, 15, DeviceStatus.OPERATIONAL, NOW),
            ("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE, 7, 2, 15, DeviceStatus.OPERATIONAL, datetime(2026, 11, 3)),
        ):
            with self.subTest(arguments=arguments), self.assertRaises((TypeError, ValueError)):
                MonitoredDevice(*arguments)


class CloudDeviceRegistryGatewayTestCase(unittest.TestCase):
    def test_translates_the_iot_devices_of_the_gateway(self):
        session = FakeCloudSession(FakeResponse(200, [
            equipment(),
            equipment(id=16, environmentId=None, deviceType="CONTAINER_MONITOR",
                      sensorExternalId="CNT-001", status="MAINTENANCE"),
            equipment(id=17, deviceType=None, sensorExternalId=None, type="Centrifuge"),
            equipment(id=18, sensorExternalId=None),
            equipment(id=19, sensorExternalId="ENV-009", status="RETIRED"),
        ]))

        with self.assertLogs("sync.infrastructure.cloud_registry_gateway", "WARNING") as logs:
            devices = CloudDeviceRegistryGateway(session).fetch_devices()

        self.assertIn("Skipped the cloud device ENV-009", logs.output[0])

        self.assertEqual(session.requests, [("GET", "/api/v1/edge-gateways/EDGE-001/iot-devices")])
        self.assertEqual([device.device_id for device in devices], ["ENV-001", "CNT-001"])
        container = devices[1]
        self.assertEqual(container.device_type, DeviceType.CONTAINER_MONITOR)
        self.assertEqual(container.cloud_device_id, 16)
        self.assertIsNone(container.environment_id)
        self.assertEqual(container.status, DeviceStatus.MAINTENANCE)

    def test_reads_the_iot_devices_assigned_to_the_gateway(self):
        session = FakeCloudSession(FakeResponse(200, [
            equipment(),
            equipment(
                id=16,
                environmentId=2,
                deviceType="CONTAINER_MONITOR",
                sensorExternalId="CNT-001",
            ),
        ]))

        devices = CloudDeviceRegistryGateway(session).fetch_devices()

        self.assertEqual(session.requests,[("GET", "/api/v1/edge-gateways/EDGE-001/iot-devices")])

        self.assertEqual([(d.device_id, d.environment_id) for d in devices],[("ENV-001", 2), ("CNT-001", 2)])

    def test_gateway_refusal_stops_the_registry(self):
        session = FakeCloudSession(FakeResponse(403))

        with self.assertRaises(CloudRejectedError) as raised:
            CloudDeviceRegistryGateway(session).fetch_devices()

        self.assertEqual(raised.exception.status_code, 403)

    def test_reports_an_unexpected_answer(self):
        with self.assertRaises(CloudRejectedError):
            CloudDeviceRegistryGateway(FakeCloudSession(FakeResponse(404))).fetch_devices()

    def test_rejects_malformed_equipment_lists(self):
        """Reject unexpected equipment list structures."""
        invalid_payloads = [{"equipments": []},[None],["invalid-device"]]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                session = FakeCloudSession(FakeResponse(200, payload))

                with self.assertRaises(CloudRejectedError):
                    CloudDeviceRegistryGateway(session).fetch_devices()


class IamAntiCorruptionLayerTestCase(unittest.TestCase):
    def test_reports_the_nodes_registered_in_this_edge_revoked_ones_included(self):
        class FakeIamDevice:
            def __init__(self, device_id, revoked):
                self.device_id = device_id
                self.revoked_at = NOW if revoked else None

        class FakeIamAuthenticationService:
            def list_devices(self):
                return [FakeIamDevice("ENV-001", False), FakeIamDevice("CNT-009", True)]

        layer = IamAntiCorruptionLayer(FakeIamAuthenticationService())

        self.assertEqual(layer.registered_device_ids(), {"ENV-001", "CNT-009"})


class FakeRegistryGateway:
    def __init__(self, *answers):
        self.answers = list(answers)

    def fetch_devices(self):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeLayer:
    """The nodes registered in this Edge, as the IAM anti-corruption layer reports them."""

    def __init__(self, *device_ids):
        self.device_ids = set(device_ids)

    def registered_device_ids(self):
        return self.device_ids


class DeviceRegistryApplicationServiceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.repository = MonitoredDeviceRepository()
        self.repository.replace_all([])

    def tearDown(self):
        self.repository.replace_all([])
        self.context.pop()

    def test_mirrors_the_cloud_registry_and_keeps_it_when_the_cloud_fails(self):
        service = DeviceRegistryApplicationService(
            FakeRegistryGateway(
                [monitored("ENV-001"), monitored("CNT-001", DeviceType.CONTAINER_MONITOR, None, 16)],
                [monitored("CNT-001", DeviceType.CONTAINER_MONITOR, 3, 16)],
                CloudUnavailableError("offline"),
            ),
            FakeLayer("ENV-001", "CNT-001"),
            self.repository,
        )

        service.synchronize_registry()
        self.assertEqual([d.device_id for d in service.monitored_devices()], ["CNT-001", "ENV-001"])

        service.synchronize_registry()
        (container,) = service.monitored_devices()
        self.assertEqual(container.device_id, "CNT-001")
        self.assertEqual(container.environment_id, 3)
        self.assertEqual(container.synced_at, NOW)

        with self.assertRaises(CloudUnavailableError):
            service.synchronize_registry()
        self.assertEqual([d.device_id for d in service.monitored_devices()], ["CNT-001"])

    def test_keeps_all_cloud_authorized_nodes_even_before_local_enrollment(self):
        service = DeviceRegistryApplicationService(
            FakeRegistryGateway([
                monitored("ENV-001"),
                monitored("CNT-001", DeviceType.CONTAINER_MONITOR, 2, 16),
                monitored("CNT-OTHER", DeviceType.CONTAINER_MONITOR, 9, 30),
            ]),
            FakeLayer("ENV-001", "CNT-001", "CNT-TYPO"),
            self.repository,
        )

        registry = service.synchronize_registry()

        self.assertEqual(
            [d.device_id for d in registry.devices],
            ["ENV-001", "CNT-001", "CNT-OTHER"],
        )

        self.assertEqual(registry.not_in_cloud, ["CNT-TYPO"])

        self.assertEqual([d.device_id for d in service.monitored_devices()],["CNT-001", "CNT-OTHER", "ENV-001"])



    def test_needs_a_gateway_to_synchronize(self):
        with self.assertRaises(CloudSynchronizationDisabled):
            DeviceRegistryApplicationService(None, FakeLayer(), self.repository).synchronize_registry()


    def test_malformed_cloud_registry_preserves_local_devices(self):
        """Keep the previous registry when Cloud returns invalid data."""
        self.repository.replace_all([
            monitored("ENV-001"),
        ])

        session = FakeCloudSession(
            FakeResponse(200, {"equipments": []})
        )

        service = DeviceRegistryApplicationService(
            CloudDeviceRegistryGateway(session),
            FakeLayer("ENV-001"),
            self.repository,
        )

        with self.assertRaises(CloudRejectedError):
            service.synchronize_registry()

        stored_devices = service.monitored_devices()

        self.assertEqual(
            [device.device_id for device in stored_devices],
            ["ENV-001"],
        )


class CloudSyncSchedulerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def test_runs_every_task_and_keeps_going_after_a_failure(self):
        failure = CloudUnavailableError("offline")

        def failing():
            raise failure

        with self.assertLogs("shared.infrastructure.cloud_sync_scheduler", "WARNING"):
            results = CloudSyncScheduler(60, [("first", failing), ("second", lambda: 3)]).run_once()

        self.assertIs(results["first"], failure)
        self.assertEqual(results["second"], 3)

    def test_logs_how_long_the_cycle_took_and_warns_when_it_exceeds_the_interval(self):
        clock = iter([0.0, 0.0, 0.5, 0.5, 2.5, 2.5])
        with patch("shared.infrastructure.cloud_sync_scheduler.time.monotonic", lambda: next(clock)), \
                self.assertLogs("shared.infrastructure.cloud_sync_scheduler", "INFO") as logs:
            CloudSyncScheduler(1, [("registry", lambda: None), ("telemetry", lambda: None)]).run_once()

        self.assertEqual(logs.records[-1].levelname, "WARNING")
        self.assertIn("cycle took 2.5 s (registry 0.5 s, telemetry 2.0 s)", logs.output[-1])

    def test_background_thread_runs_until_stopped(self):
        ran = threading.Event()
        scheduler = CloudSyncScheduler(60, [("task", ran.set)])

        scheduler.start()
        self.assertTrue(ran.wait(5))
        scheduler.stop(5)

    def test_keeps_when_the_last_cycle_ended_and_which_tasks_failed(self):
        finished_at = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)

        def failing():
            raise CloudUnavailableError("offline")

        scheduler = CloudSyncScheduler(
            60, [("registry", lambda: None), ("pending telemetry", failing)], clock=lambda: finished_at
        )
        self.assertIsNone(scheduler.last_cycle)

        with self.assertLogs("shared.infrastructure.cloud_sync_scheduler", "WARNING"):
            scheduler.run_once()

        self.assertEqual(scheduler.last_cycle, CloudSyncCycle(finished_at, ("pending telemetry",)))

    def test_reports_whether_the_background_thread_is_running(self):
        started_at = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)
        scheduler = CloudSyncScheduler(60, [("task", lambda: None)], clock=lambda: started_at)
        self.assertFalse(scheduler.is_running)
        self.assertIsNone(scheduler.started_at)

        scheduler.start()
        self.assertTrue(scheduler.is_running)
        self.assertEqual(scheduler.started_at, started_at)

        scheduler.stop(5)
        self.assertFalse(scheduler.is_running)


class CloudCliTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.runner = self.app.test_cli_runner()

    def test_sync_reports_that_the_synchronization_is_disabled(self):
        result = self.runner.invoke(args=["cloud", "sync"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("CLOUD_SYNC_ENABLED=false", result.output)

    def test_lists_the_local_copy_of_the_registry(self):
        with self.app.app_context():
            MonitoredDeviceRepository().replace_all([monitored("CNT-001", DeviceType.CONTAINER_MONITOR, None, 16)])
        try:
            result = self.runner.invoke(args=["cloud", "devices"])
        finally:
            with self.app.app_context():
                MonitoredDeviceRepository().replace_all([])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.output.split(),
            ["CNT-001", "CONTAINER_MONITOR", "laboratory", "7", "environment", "-", "cloud", "id", "16", "OPERATIONAL"],
        )

    def test_enrolls_a_node_of_the_cached_registry_once(self):
        client = self.app.test_client()
        body = {"device_id": "ENV-ENROLL", "device_type": "ENVIRONMENTAL_DEVICE"}
        with self.app.app_context():
            MonitoredDeviceRepository().replace_all([monitored("ENV-ENROLL")])
        try:
            enrolled = client.post("/api/v1/devices/enroll", json=body)
            again = client.post("/api/v1/devices/enroll", json=body)
            unknown = client.post("/api/v1/devices/enroll", json={**body, "device_id": "ENV-404"})
        finally:
            with self.app.app_context():
                MonitoredDeviceRepository().replace_all([])
                DeviceModel.delete().where(DeviceModel.device_id == "ENV-ENROLL").execute()

        self.assertEqual(enrolled.status_code, 201, enrolled.json)
        self.assertTrue(enrolled.json["api_key"])
        self.assertEqual((again.status_code, unknown.status_code), (409, 403))


if __name__ == "__main__":
    unittest.main()
