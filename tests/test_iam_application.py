import unittest

from iam.application.services import AuthApplicationService
from iam.domain.entities import Device
from iam.domain.enums import DeviceType
from iam.domain.errors import DeviceAlreadyRegistered


class InMemoryDeviceRepository:
    def __init__(self):
        self.devices = {}

    def find_by_device_id(self, device_id):
        return self.devices.get(device_id)

    def find_all(self):
        return [self.devices[device_id] for device_id in sorted(self.devices)]

    def add(self, device: Device):
        if device.device_id in self.devices:
            raise DeviceAlreadyRegistered
        self.devices[device.device_id] = device
        return device

    def save(self, device: Device):
        self.devices[device.device_id] = device
        return device

    def get_or_create_test_device(self, device_id, api_key, device_type):
        return self.devices.setdefault(device_id, Device.register(device_id, api_key, device_type))


class AuthApplicationServiceTestCase(unittest.TestCase):
    """Tests the use cases that manage the identity of the nodes.

    Validates registration with a random key kept only as a verifier, the
    refusal of a duplicate registration, the effect of rotation and revocation
    on the accepted key, and the listing of devices by identity.
    """

    def setUp(self):
        self.repository = InMemoryDeviceRepository()
        self.service = AuthApplicationService(self.repository)

    def test_registers_device_with_a_random_key_kept_only_as_a_verifier(self):
        """Each registration issues a random key, stored only as a verifier."""
        first = self.service.register_device("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)
        second = self.service.register_device("CNT-001", DeviceType.CONTAINER_MONITOR)

        self.assertNotEqual(first.api_key, second.api_key)
        self.assertGreaterEqual(len(first.api_key), 43)
        self.assertNotIn(first.api_key, repr(first))
        self.assertNotIn(first.api_key, self.repository.devices["ENV-001"].credential.encode())
        self.assertEqual(
            self.service.authenticated_device("ENV-001", first.api_key).device_type,
            DeviceType.ENVIRONMENTAL_DEVICE,
        )

    def test_rejects_a_second_registration_of_the_same_device(self):
        """A second registration of the same device raises ValueError."""
        self.service.register_device("ENV-001", DeviceType.ENVIRONMENTAL_DEVICE)

        with self.assertRaisesRegex(ValueError, "already registered"):
            self.service.register_device("ENV-001", DeviceType.CONTAINER_MONITOR)

    def test_rotation_and_revocation_change_which_key_is_accepted(self):
        """Rotation invalidates the old key, and revocation invalidates the new one."""
        issued = self.service.register_device("CNT-001", DeviceType.CONTAINER_MONITOR)
        rotated = self.service.rotate_api_key("CNT-001")

        self.assertIsNone(self.service.authenticated_device("CNT-001", issued.api_key))
        self.assertIsNotNone(self.service.authenticated_device("CNT-001", rotated.api_key))

        self.service.revoke_device("CNT-001")
        self.assertIsNone(self.service.authenticated_device("CNT-001", rotated.api_key))
        with self.assertRaises(ValueError):
            self.service.rotate_api_key("CNT-001")

    def test_unknown_device_is_not_authenticated_nor_managed(self):
        """An unknown device is not authenticated and cannot be managed."""
        self.assertIsNone(self.service.authenticated_device("CNT-404", "any-key"))
        for operation in (self.service.revoke_device, self.service.rotate_api_key):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(
                ValueError, "Device not found"
            ):
                operation("CNT-404")

    def test_lists_devices_by_identity(self):
        """The devices are listed ordered by their identifier."""
        self.service.register_device("ENV-002", DeviceType.ENVIRONMENTAL_DEVICE)
        self.service.register_device("CNT-001", DeviceType.CONTAINER_MONITOR)

        self.assertEqual(
            [device.device_id for device in self.service.list_devices()],
            ["CNT-001", "ENV-002"],
        )


if __name__ == "__main__":
    unittest.main()
