"""Application errors raised by Tracking use cases."""


class CloudSynchronizationDisabled(Exception):
    """Raised when a use case needs the cloud and the synchronization is disabled."""


class DeviceAuthenticationFailed(Exception):
    """Raised when a node is not authorized to send telemetry."""


class DeviceTypeNotAllowed(Exception):
    """Raised when a node sends telemetry that its type does not report."""


class ConfigurationNotAvailable(Exception):
    """Raised when the Edge has no profile of the cloud for a node yet."""

class DeviceEnrollmentNotAllowed(Exception):
    """The device is not eligible for initial enrollment."""


class DeviceAlreadyEnrolled(Exception):
    """The device already has local IAM credentials."""