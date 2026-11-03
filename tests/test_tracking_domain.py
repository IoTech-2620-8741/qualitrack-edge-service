import unittest
from datetime import UTC, datetime, timedelta, timezone

from tracking.domain.entities import ActuationEvent, ReportingDevice, TelemetryReading
from tracking.domain.enums import (
    ActuationAction,
    ActuationResult,
    DeviceType,
    EnvironmentalState,
    Metric,
    SyncStatus,
)
from tracking.domain.value_objects import ObservationTime, ProfileVersion

RECEIVED_AT = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
ENVIRONMENTAL = ReportingDevice("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)
CONTAINER = ReportingDevice("CNT-001", DeviceType.CONTAINER_MONITOR)


def reading(device=CONTAINER, metric=Metric.TEMPERATURE, value=21.5, measured_at=RECEIVED_AT, version=3):
    return TelemetryReading.record(device, metric, value, measured_at, version, RECEIVED_AT)


def action(trigger_metric=Metric.TEMPERATURE, trigger_state=EnvironmentalState.WARNING, device=CONTAINER):
    return ActuationEvent.record(
        device, ActuationAction.VENTILATION_ON, trigger_metric, trigger_state,
        ActuationResult.EXECUTED, RECEIVED_AT, 3, RECEIVED_AT,
    )


class ObservationTimeTestCase(unittest.TestCase):
    def test_normalizes_to_utc_and_accepts_the_clock_tolerance(self):
        lima = timezone(timedelta(hours=-5))

        observation = ObservationTime.received(datetime(2026, 11, 3, 10, 4, tzinfo=lima), RECEIVED_AT)

        self.assertEqual(observation.value, datetime(2026, 11, 3, 15, 4, tzinfo=UTC))

    def test_rejects_naive_and_future_times(self):
        with self.assertRaises(ValueError):
            ObservationTime(datetime(2026, 11, 3, 15, 0))
        with self.assertRaisesRegex(ValueError, "future"):
            ObservationTime.received(RECEIVED_AT + timedelta(minutes=6), RECEIVED_AT)

    def test_profile_version_is_a_non_negative_integer(self):
        self.assertIsNone(ProfileVersion.from_optional(None))
        for value in (-1, True, 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProfileVersion(value)


class TelemetryReadingTestCase(unittest.TestCase):
    def test_records_a_pending_reading(self):
        recorded = reading()

        self.assertEqual(recorded.sync_status, SyncStatus.PENDING)
        self.assertEqual(recorded.value, 21.5)
        self.assertIsNone(recorded.text_value)
        self.assertEqual(recorded.profile_version, ProfileVersion(3))
        self.assertIsNone(recorded.id)

    def test_keeps_the_rfid_tag_as_text(self):
        recorded = reading(metric=Metric.RFID_TAG, value="E200-3412-0001")

        self.assertEqual(recorded.text_value, "E200-3412-0001")
        self.assertIsNone(recorded.value)

    def test_rejects_a_metric_the_node_type_does_not_report(self):
        with self.assertRaisesRegex(ValueError, "not measured by ENVIRONMENTAL_DEVICE nodes"):
            reading(device=ENVIRONMENTAL, metric=Metric.TEMPERATURE)
        with self.assertRaisesRegex(ValueError, "not measured by CONTAINER_MONITOR nodes"):
            reading(metric=Metric.AIR_QUALITY)

    def test_rejects_values_the_cloud_would_reject(self):
        for metric, value, device in (
            (Metric.TEMPERATURE, float("nan"), CONTAINER),
            (Metric.HUMIDITY, float("inf"), CONTAINER),
            (Metric.LUMINOSITY, True, CONTAINER),
            (Metric.MOTION, 0.5, ENVIRONMENTAL),
            (Metric.RFID_TAG, " ", CONTAINER),
            (Metric.RFID_TAG, "x" * 121, CONTAINER),
        ):
            with self.subTest(metric=metric, value=value), self.assertRaises(ValueError):
                reading(device=device, metric=metric, value=value)

    def test_a_retry_is_the_same_observation_only_with_the_same_data(self):
        original = reading()

        self.assertTrue(original.is_same_observation(reading()))
        self.assertFalse(original.is_same_observation(reading(value=22.0)))
        self.assertFalse(original.is_same_observation(reading(version=4)))


class ActuationEventTestCase(unittest.TestCase):
    def test_records_a_pending_action_with_or_without_cause(self):
        caused = action()
        opened_by_rfid = ActuationEvent.record(
            CONTAINER, ActuationAction.SERVO_OPEN, None, None,
            ActuationResult.EXECUTED, RECEIVED_AT, None, RECEIVED_AT,
        )

        self.assertEqual(caused.sync_status, SyncStatus.PENDING)
        self.assertEqual(caused.trigger_state, EnvironmentalState.WARNING)
        self.assertIsNone(opened_by_rfid.trigger_metric)

    def test_rejects_an_incomplete_or_invalid_cause(self):
        for trigger_metric, trigger_state in (
            (Metric.TEMPERATURE, None),
            (None, EnvironmentalState.CRITICAL),
            (Metric.RFID_TAG, EnvironmentalState.WARNING),
            (Metric.AIR_QUALITY, EnvironmentalState.WARNING),
        ):
            with self.subTest(trigger_metric=trigger_metric), self.assertRaises(ValueError):
                action(trigger_metric, trigger_state)

    def test_only_a_container_monitor_executes_actions(self):
        with self.assertRaisesRegex(ValueError, "container monitor"):
            action(device=ENVIRONMENTAL)


if __name__ == "__main__":
    unittest.main()
