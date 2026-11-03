"""Value objects for the telemetry and profile invariants of the Tracking context."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite

from tracking.domain.enums import ActuationAction, EnvironmentalState, Metric

# Clock difference accepted between a node and the Edge, the same as the cloud.
CLOCK_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class ObservationTime:
    """When a node measured a value or executed an action, normalized to UTC.

    The node supplies it, so a retry of the same observation keeps it. It
    cannot be later than the moment the Edge received it plus the clock
    tolerance, because the cloud rejects observations from the future.
    """

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise TypeError("Observation time must be a datetime")
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("Observation time must include a timezone")
        object.__setattr__(self, "value", self.value.astimezone(UTC))

    @classmethod
    def received(cls, value: datetime, received_at: datetime) -> "ObservationTime":
        """Validate an observation time against the moment the Edge received it."""
        observation = cls(value)
        if observation.value > received_at.astimezone(UTC) + CLOCK_TOLERANCE:
            raise ValueError("Observation time cannot be in the future")
        return observation


@dataclass(frozen=True)
class ProfileVersion:
    """Version of the environmental profile a node applied, when it reports it."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError("Profile version must be a non-negative integer")

    @classmethod
    def from_optional(cls, value: int | None) -> "ProfileVersion | None":
        """Return the version, or ``None`` when the node did not report it."""
        return None if value is None else cls(value)


def _optional_limit(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class Threshold:
    """Ranges of a metric in the environmental profile, with the rules of the cloud.

    A value inside the normal range is ``NORMAL``, outside it but inside the
    critical range ``WARNING``, and beyond the critical range ``CRITICAL``.
    Each side is either complete (normal and critical limit) or open.
    """

    metric: Metric
    unit: str
    normal_min: float | None
    normal_max: float | None
    critical_min: float | None
    critical_max: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.metric, Metric) or not self.metric.has_thresholds:
            raise ValueError(f"Thresholds cannot be configured for {self.metric}")
        if not isinstance(self.unit, str):
            raise TypeError("Threshold unit must be a string")
        for name in ("normal_min", "normal_max", "critical_min", "critical_max"):
            object.__setattr__(self, name, _optional_limit(getattr(self, name), name))
        if (self.normal_min is None) != (self.critical_min is None) or (
            (self.normal_max is None) != (self.critical_max is None)
        ):
            raise ValueError(f"Each side of {self.metric.value} needs both its normal and critical limit")
        if self.normal_min is None and self.normal_max is None:
            raise ValueError(f"At least one limit of {self.metric.value} is required")
        if self.normal_min is not None and self.critical_min >= self.normal_min:
            raise ValueError(f"The critical minimum of {self.metric.value} must be lower than the normal minimum")
        if self.normal_max is not None and self.critical_max <= self.normal_max:
            raise ValueError(f"The critical maximum of {self.metric.value} must be higher than the normal maximum")
        if self.normal_min is not None and self.normal_max is not None and self.normal_min >= self.normal_max:
            raise ValueError(f"The normal minimum of {self.metric.value} must be lower than its normal maximum")


@dataclass(frozen=True)
class ActuationRule:
    """Action a container monitor starts when a metric reaches a state."""

    metric: Metric
    state: EnvironmentalState
    action: ActuationAction

    def __post_init__(self) -> None:
        if not isinstance(self.metric, Metric) or not self.metric.has_thresholds:
            raise ValueError(f"Rules cannot be based on {self.metric}")
        if not isinstance(self.state, EnvironmentalState) or self.state is EnvironmentalState.NORMAL:
            raise ValueError("A rule reacts to a WARNING or CRITICAL condition")
        if not isinstance(self.action, ActuationAction) or not self.action.is_activation:
            raise ValueError(f"A rule must start an action; {self.action} does not")
