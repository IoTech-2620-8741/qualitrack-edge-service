import re
import unittest
from pathlib import Path


class ContextDependencyTestCase(unittest.TestCase):
    """Checks the dependency rules between the bounded contexts.

    Tracking must not depend on Sync, and Sync may use the model and contracts
    of Tracking but not its infrastructure or interfaces (ADR-0015).
    """

    def test_tracking_does_not_depend_on_sync(self):
        """No module of Tracking imports Sync."""
        tracking = Path(__file__).resolve().parent.parent / "tracking"

        dependent = [
            str(module.relative_to(tracking.parent))
            for module in sorted(tracking.rglob("*.py"))
            if re.search(r"^\s*(from|import)\s+sync\b", module.read_text(), re.MULTILINE)
        ]

        self.assertEqual(dependent, [])

    def test_sync_uses_the_model_of_tracking_but_not_its_infrastructure(self):
        """No module of Sync imports the infrastructure or interfaces of Tracking."""
        sync = Path(__file__).resolve().parent.parent / "sync"

        dependent = [
            str(module.relative_to(sync.parent))
            for module in sorted(sync.rglob("*.py"))
            if re.search(r"^\s*(from|import)\s+tracking\.(infrastructure|interfaces)\b",
                         module.read_text(), re.MULTILINE)
        ]

        self.assertEqual(dependent, [])


if __name__ == "__main__":
    unittest.main()
