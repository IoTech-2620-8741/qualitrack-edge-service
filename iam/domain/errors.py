"""Domain errors of the IAM bounded context."""


class DeviceAlreadyRegistered(ValueError):
    """A device with this identity is already registered."""

    def __init__(self) -> None:
        super().__init__("Device is already registered")
