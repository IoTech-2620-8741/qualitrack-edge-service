"""Value objects for the telemetry invariants of the Tracking context."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
