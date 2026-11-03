"""Application errors raised by Tracking use cases."""


class CloudSynchronizationDisabled(Exception):
    """Raised when a use case needs the cloud and the synchronization is disabled."""


class DeviceAuthenticationFailed(Exception):
    """Raised when a node is not authorized to send telemetry."""


class DeviceTypeNotAllowed(Exception):
    """Raised when a node sends telemetry that its type does not report."""
