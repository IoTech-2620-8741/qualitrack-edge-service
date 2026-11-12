"""Application errors raised by Sync use cases."""


class CloudSynchronizationDisabled(Exception):
    """Raised when a use case needs the cloud and the synchronization is disabled."""
