import unittest
from datetime import UTC, datetime

from app import create_app
from iam.domain.enums import DeviceType as IamDeviceType
from shared.infrastructure.cloud_session import CloudRejectedError, CloudUnavailableError
from tracking.application.errors import (
    CloudSynchronizationDisabled,
    ConfigurationNotAvailable,
    DeviceAuthenticationFailed,
)
from tracking.application.services import EnvironmentalProfileApplicationService
from tracking.domain.entities import EnvironmentalProfile, MonitoredDevice, ReportingDevice
from tracking.domain.enums import ActuationAction, DeviceStatus, DeviceType, EnvironmentalState, Metric
from tracking.domain.value_objects import ActuationRule, ProfileVersion, Threshold
from tracking.infrastructure.cloud_profile_gateway import CloudProfileGateway
from tracking.infrastructure.repositories import EnvironmentalProfileRepository

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
TEMPERATURE = Threshold(Metric.TEMPERATURE, "°C", 15, 25, 8, 30)
VENTILATION = ActuationRule(Metric.TEMPERATURE, EnvironmentalState.WARNING, ActuationAction.VENTILATION_ON)


def profile(device_id="CNT-001", version=3, thresholds=(TEMPERATURE,), rules=(VENTILATION,)):
    return EnvironmentalProfile(device_id, ProfileVersion(version), thresholds, rules, NOW, NOW)


def placed(device_id, device_type=DeviceType.CONTAINER_MONITOR, environment_id=2, cloud_device_id=16):
    return MonitoredDevice(device_id, device_type, 7, environment_id, cloud_device_id, DeviceStatus.OPERATIONAL, NOW)


class ProfileDomainTestCase(unittest.TestCase):
    def test_accepts_open_sides_and_the_rules_of_the_cloud(self):
        air = Threshold(Metric.AIR_QUALITY, "ppm", None, 800, None, 1200)

        self.assertEqual((air.normal_max, air.critical_max), (800.0, 1200.0))
        self.assertEqual(profile().actuation_rules, (VENTILATION,))

    def test_rejects_thresholds_the_cloud_would_reject(self):
        for arguments in (
            (Metric.RFID_TAG, "tag", 1, 2, 0, 3),
            (Metric.TEMPERATURE, "°C", 15, 25, None, 30),
            (Metric.TEMPERATURE, "°C", None, None, None, None),
            (Metric.TEMPERATURE, "°C", 15, 25, 15, 30),
            (Metric.TEMPERATURE, "°C", 15, 25, 8, 25),
            (Metric.TEMPERATURE, "°C", 25, 15, 8, 30),
            (Metric.TEMPERATURE, "°C", float("nan"), 25, 8, 30),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Threshold(*arguments)

    def test_rejects_rules_that_do_not_start_a_response(self):
        for arguments in (
            (Metric.TEMPERATURE, EnvironmentalState.NORMAL, ActuationAction.VENTILATION_ON),
            (Metric.TEMPERATURE, EnvironmentalState.WARNING, ActuationAction.VENTILATION_OFF),
            (Metric.MOTION, EnvironmentalState.WARNING, ActuationAction.SERVO_OPEN),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ActuationRule(*arguments)

    def test_a_profile_has_one_threshold_per_metric_and_rules_need_one(self):
        with self.assertRaisesRegex(ValueError, "one threshold per metric"):
            profile(thresholds=(TEMPERATURE, TEMPERATURE))
        with self.assertRaisesRegex(ValueError, "needs a threshold"):
            profile(thresholds=(Threshold(Metric.HUMIDITY, "%RH", 30, 60, 20, 70),))


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
                         [("GET", "/api/v1/laboratories/7/environments/2/devices/16/environmental-profile")])
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


class InMemoryProfiles:
    def __init__(self, *profiles):
        self.profiles = {p.device_id: p for p in profiles}

    def find_by_device_id(self, device_id):
        return self.profiles.get(device_id)

    def find_all(self):
        return [self.profiles[device_id] for device_id in sorted(self.profiles)]

    def replace_all(self, profiles):
        self.profiles = {p.device_id: p for p in profiles}


class FakeLayer:
    def authenticated_reporting_device(self, device_id, api_key):
        return ReportingDevice(device_id, DeviceType.CONTAINER_MONITOR) if api_key == "secret" else None


class EnvironmentalProfileApplicationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.devices = InMemoryDevices(
            placed("CNT-001"), placed("CNT-002", cloud_device_id=17), placed("CNT-003", environment_id=None)
        )
        self.repository = InMemoryProfiles(profile("CNT-002", version=1), profile("CNT-OLD"))

    def service(self, gateway):
        return EnvironmentalProfileApplicationService(gateway, FakeLayer(), self.devices, self.repository)

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

    def test_hands_each_node_its_own_configuration(self):
        service = self.service(None)

        configuration = service.configuration_for("CNT-002", "secret")

        self.assertEqual(configuration.device_type, DeviceType.CONTAINER_MONITOR)
        self.assertEqual(configuration.profile.version, ProfileVersion(1))
        with self.assertRaises(DeviceAuthenticationFailed):
            service.configuration_for("CNT-002", "invalid")
        with self.assertRaises(ConfigurationNotAvailable):
            service.configuration_for("CNT-001", "secret")
        with self.assertRaises(CloudSynchronizationDisabled):
            service.synchronize_profiles()


class ConfigurationApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True
        with cls.app.app_context():
            cls.container_key = cls.app.extensions["iam_authentication_service"].register_device(
                "CNT-CONF", IamDeviceType.CONTAINER_MONITOR
            ).api_key

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            EnvironmentalProfileRepository().replace_all([profile("CNT-CONF", version=7)])

    def tearDown(self):
        with self.app.app_context():
            EnvironmentalProfileRepository().replace_all([])

    def get(self, device_id, api_key):
        headers = {} if api_key is None else {"X-API-Key": api_key}
        return self.client.get(f"/api/v1/configurations/{device_id}", headers=headers)

    def test_hands_the_container_monitor_its_thresholds_and_rules(self):
        response = self.get("CNT-CONF", self.container_key)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "device_id": "CNT-CONF",
            "device_type": "CONTAINER_MONITOR",
            "profile_version": 7,
            "thresholds": [{"metric": "TEMPERATURE", "unit": "°C", "normal_min": 15.0, "normal_max": 25.0,
                            "critical_min": 8.0, "critical_max": 30.0}],
            "actuation_rules": [{"metric": "TEMPERATURE", "state": "WARNING", "action": "VENTILATION_ON"}],
            "updated_at": "2026-11-03T15:00:00Z",
            "synced_at": "2026-11-03T15:00:00Z",
        })

    def test_rejects_other_credentials_and_reports_a_missing_profile(self):
        self.assertEqual(self.get("CNT-CONF", None).status_code, 401)
        self.assertEqual(self.get("CNT-CONF", "test-only-key").status_code, 401)
        missing = self.get("ENV-001", "test-only-key")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("no profile", missing.json["error"])

    def test_the_profiles_command_lists_the_local_copy(self):
        result = self.app.test_cli_runner().invoke(args=["cloud", "profiles"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.split(), ["CNT-CONF", "version", "7", "thresholds", "TEMPERATURE", "rules", "1"])


if __name__ == "__main__":
    unittest.main()
