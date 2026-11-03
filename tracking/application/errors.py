"""Application errors raised by Tracking use cases."""


class CloudSynchronizationDisabled(Exception):
    """Raised when a use case needs the cloud and the synchronization is disabled."""
