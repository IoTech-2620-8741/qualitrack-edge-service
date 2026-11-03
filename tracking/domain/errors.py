"""Domain errors for the telemetry the Edge receives."""


class TelemetryConflict(Exception):
    """Raised when a node sends again an observation it already sent with different data."""
