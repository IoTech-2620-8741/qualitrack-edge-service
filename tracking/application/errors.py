"""Application errors raised by Tracking use cases."""


class DeviceAuthenticationFailed(Exception):
    """Raised when a node is not authorized to send telemetry."""


class DeviceTypeNotAllowed(Exception):
    """Raised when a node sends telemetry that its type does not report."""


class ConfigurationNotAvailable(Exception):
    """Raised when the Edge has no profile of the cloud for a node yet."""
