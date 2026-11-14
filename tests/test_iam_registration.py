import unittest

from app import create_app
from iam.domain.entities import Device
from iam.domain.enums import DeviceType
from iam.domain.errors import DeviceAlreadyRegistered
from iam.infrastructure.models import Device as DeviceModel
from iam.infrastructure.repositories import DeviceRepository


class DeviceRegistrationRaceTestCase(unittest.TestCase):
    """A second insert of the same node fails instead of replacing the first key.

    The primary key of the devices table is the only guard against two
    concurrent registrations. This test simulates the race at the repository
    level and checks that the first key keeps working.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        DeviceModel.delete().where(DeviceModel.device_id == "ENV-RACE").execute()
        self.context.pop()

    def test_the_second_registration_is_refused_and_the_first_key_keeps_working(self):
        """The second insert raises DeviceAlreadyRegistered and stores nothing."""
        repository = DeviceRepository()
        repository.add(Device.register("ENV-RACE", "first-key", DeviceType.ENVIRONMENTAL_DEVICE))

        with self.assertRaises(DeviceAlreadyRegistered):
            repository.add(Device.register("ENV-RACE", "second-key", DeviceType.ENVIRONMENTAL_DEVICE))

        stored = repository.find_by_device_id("ENV-RACE")
        self.assertTrue(stored.authenticate("first-key"))
        self.assertFalse(stored.authenticate("second-key"))


if __name__ == "__main__":
    unittest.main()
