import unittest
from datetime import UTC, datetime

from shared.infrastructure.cloud_session import CloudRejectedError, CloudUnavailableError
from sync.application.services import ProfileSynchronizationApplicationService
from sync.infrastructure.cloud_profile_gateway import CloudProfileGateway
from tests.test_tracking_profiles import NOW, TEMPERATURE, VENTILATION, InMemoryProfiles, profile
from tracking.application.errors import CloudSynchronizationDisabled
from tracking.domain.entities import MonitoredDevice
from tracking.domain.enums import DeviceStatus, DeviceType
from tracking.domain.value_objects import ProfileVersion


def placed(device_id, device_type=DeviceType.CONTAINER_MONITOR, environment_id=2, cloud_device_id=16):
    return MonitoredDevice(device_id, device_type, 7, environment_id, cloud_device_id, DeviceStatus.OPERATIONAL, NOW)


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeCloudSession:
    def __init__(self, response):
        self.response = response
        self.paths = []
        self.device_id = "EDGE-001"

    def request(self, method, path, json=None):
        self.paths.append((method, path))
        return self.response


def container_profile_resource(**overrides):
    return {
        "id": 9, "scope": "CONTAINER_MONITOR", "laboratoryId": 7, "environmentId": None, "deviceId": 16,
        "version": 3, "updatedAt": "2026-11-03T09:00:00-05:00", "updatedBy": 3,
        "thresholds": [{"metric": "TEMPERATURE", "unit": "°C", "normalMin": 15.0, "normalMax": 25.0,
                        "criticalMin": 8.0, "criticalMax": 30.0}],
        "actuationRules": [{"metric": "TEMPERATURE", "state": "WARNING", "action": "VENTILATION_ON"}],
        **overrides,
    }


class CloudProfileGatewayTestCase(unittest.TestCase):
    def test_translates_the_profile_the_node_applies(self):
        session = FakeCloudSession(FakeResponse(200, container_profile_resource()))

        read = CloudProfileGateway(session).fetch_profile(placed("CNT-001"))

        self.assertEqual(session.paths,
                         [("GET", "/api/v1/edge-gateways/EDGE-001/iot-devices/16/environmental-profile")])
        self.assertEqual((read.device_id, read.version), ("CNT-001", ProfileVersion(3)))
        self.assertEqual(read.thresholds, (TEMPERATURE,))
        self.assertEqual(read.actuation_rules, (VENTILATION,))
        self.assertEqual(read.cloud_updated_at, datetime(2026, 11, 3, 14, 0, tzinfo=UTC))

    def test_a_node_without_profile_has_none_and_other_answers_stop(self):
        self.assertIsNone(CloudProfileGateway(FakeCloudSession(FakeResponse(404))).fetch_profile(placed("CNT-001")))
        for response in (
            FakeResponse(400, {"message": "Bad request"}),
            FakeResponse(200, container_profile_resource(actuationRules=[
                {"metric": "TEMPERATURE", "state": "WARNING", "action": "FAN_SPIN"}])),
        ):
            with self.subTest(status=response.status_code), self.assertRaises(CloudRejectedError):
                CloudProfileGateway(FakeCloudSession(response)).fetch_profile(placed("CNT-001"))


class FakeProfileGateway:
    def __init__(self, profiles, fail=False):
        self.profiles = profiles
        self.fail = fail
        self.read = []

    def fetch_profile(self, device):
        if self.fail:
            raise CloudUnavailableError("offline")
        self.read.append(device.device_id)
        return self.profiles.get(device.device_id)


class InMemoryDevices:
    def __init__(self, *devices):
        self.devices = list(devices)

    def find_all(self):
        return self.devices


class ProfileSynchronizationApplicationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.devices = InMemoryDevices(
            placed("CNT-001"), placed("CNT-002", cloud_device_id=17), placed("CNT-003", environment_id=None)
        )
        self.repository = InMemoryProfiles(profile("CNT-002", version=1), profile("CNT-OLD"))

    def service(self, gateway):
        return ProfileSynchronizationApplicationService(gateway, self.devices, self.repository)

    def test_copies_the_profiles_of_placed_nodes_and_drops_the_rest(self):
        gateway = FakeProfileGateway({"CNT-001": profile("CNT-001", version=4)})

        copied = self.service(gateway).synchronize_profiles()

        self.assertEqual(gateway.read, ["CNT-001", "CNT-002"])
        self.assertEqual([p.device_id for p in copied], ["CNT-001"])
        self.assertEqual([p.device_id for p in self.repository.find_all()], ["CNT-001"])

    def test_keeps_the_previous_copy_when_the_cloud_fails(self):
        with self.assertRaises(CloudUnavailableError):
            self.service(FakeProfileGateway({}, fail=True)).synchronize_profiles()

        self.assertEqual([p.device_id for p in self.repository.find_all()], ["CNT-002", "CNT-OLD"])

    def test_does_not_synchronize_without_a_cloud_connection(self):
        with self.assertRaises(CloudSynchronizationDisabled):
            self.service(None).synchronize_profiles()


if __name__ == "__main__":
    unittest.main()
