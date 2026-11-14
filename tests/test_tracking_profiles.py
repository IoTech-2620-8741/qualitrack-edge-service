import unittest
from datetime import UTC, datetime

from app import create_app
from iam.domain.enums import DeviceType as IamDeviceType
from tracking.application.errors import ConfigurationNotAvailable, DeviceAuthenticationFailed
from tracking.application.services import EnvironmentalProfileApplicationService
from tracking.domain.entities import EnvironmentalProfile, ReportingDevice
from tracking.domain.enums import ActuationAction, DeviceType, EnvironmentalState, Metric
from tracking.domain.value_objects import ActuationRule, ProfileVersion, Threshold
from tracking.infrastructure.repositories import EnvironmentalProfileRepository

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
TEMPERATURE = Threshold(Metric.TEMPERATURE, "°C", 15, 25, 8, 30)
VENTILATION = ActuationRule(Metric.TEMPERATURE, EnvironmentalState.WARNING, ActuationAction.VENTILATION_ON)


def profile(device_id="CNT-001", version=3, thresholds=(TEMPERATURE,), rules=(VENTILATION,)):
    return EnvironmentalProfile(device_id, ProfileVersion(version), thresholds, rules, NOW, NOW)


class ProfileDomainTestCase(unittest.TestCase):
    """Tests the EnvironmentalProfile, Threshold and ActuationRule rules.

    Validates that open sides are accepted, that invalid thresholds and rules
    are rejected, and that a profile has one threshold per metric and its rules
    refer to one of them.
    """

    def test_accepts_open_sides_and_the_rules_of_the_cloud(self):
        """Open sides are accepted and the cloud rules are preserved."""
        air = Threshold(Metric.AIR_QUALITY, "ppm", None, 800, None, 1200)

        self.assertEqual((air.normal_max, air.critical_max), (800.0, 1200.0))
        self.assertEqual(profile().actuation_rules, (VENTILATION,))

    def test_rejects_thresholds_the_cloud_would_reject(self):
        """Invalid thresholds (metric, sides, order, NaN) are rejected."""
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
        """A rule from NORMAL, an OFF action, or a metric without thresholds is rejected."""
        for arguments in (
            (Metric.TEMPERATURE, EnvironmentalState.NORMAL, ActuationAction.VENTILATION_ON),
            (Metric.TEMPERATURE, EnvironmentalState.WARNING, ActuationAction.VENTILATION_OFF),
            (Metric.MOTION, EnvironmentalState.WARNING, ActuationAction.SERVO_OPEN),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ActuationRule(*arguments)

    def test_a_profile_has_one_threshold_per_metric_and_rules_need_one(self):
        """A profile has one threshold per metric, and its rules refer to one of them."""
        with self.assertRaisesRegex(ValueError, "one threshold per metric"):
            profile(thresholds=(TEMPERATURE, TEMPERATURE))
        with self.assertRaisesRegex(ValueError, "needs a threshold"):
            profile(thresholds=(Threshold(Metric.HUMIDITY, "%RH", 30, 60, 20, 70),))


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
    """Tests the use case that hands each node its own configuration."""

    def test_hands_each_node_its_own_configuration(self):
        """Each node gets its own profile; bad credentials or a missing profile stop."""
        service = EnvironmentalProfileApplicationService(FakeLayer(), InMemoryProfiles(profile("CNT-002", version=1)))

        configuration = service.configuration_for("CNT-002", "secret")

        self.assertEqual(configuration.device_type, DeviceType.CONTAINER_MONITOR)
        self.assertEqual(configuration.profile.version, ProfileVersion(1))
        with self.assertRaises(DeviceAuthenticationFailed):
            service.configuration_for("CNT-002", "invalid")
        with self.assertRaises(ConfigurationNotAvailable):
            service.configuration_for("CNT-001", "secret")


class ConfigurationApiTestCase(unittest.TestCase):
    """Tests the GET /api/v1/configurations/{deviceId} endpoint end to end."""

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
        """The monitor receives its thresholds, rules, versions and times."""
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
        """Missing or wrong keys return 401; a node without profile returns 404."""
        self.assertEqual(self.get("CNT-CONF", None).status_code, 401)
        self.assertEqual(self.get("CNT-CONF", "test-only-key").status_code, 401)
        missing = self.get("ENV-001", "test-only-key")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("no profile", missing.json["error"])

    def test_the_profiles_command_lists_the_local_copy(self):
        """`cloud profiles` lists the local copy of the profiles."""
        result = self.app.test_cli_runner().invoke(args=["cloud", "profiles"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.split(), ["CNT-CONF", "version", "7", "thresholds", "TEMPERATURE", "rules", "1"])


if __name__ == "__main__":
    unittest.main()
