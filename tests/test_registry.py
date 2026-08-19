import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import herdr.registry as registry


class RegistryTests(unittest.TestCase):
    def test_new_registry_takes_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            new = root / "dodging-infinity" / "repos.json"
            old = root / "herd-harness" / "repos.json"
            new.parent.mkdir(parents=True)
            old.parent.mkdir(parents=True)
            new.write_text(json.dumps({"version": 1, "repos": {"new": {"path": "/new"}}}))
            old.write_text(json.dumps({"version": 1, "repos": {"old": {"path": "/old"}}}))
            with patch.object(registry, "REGISTRY", new), patch.object(registry, "LEGACY_REGISTRY", old):
                data = registry.registry_load()
            self.assertIn("new", data["repos"])
            self.assertNotIn("old", data["repos"])

    def test_legacy_registry_migrates_when_new_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            new = root / "dodging-infinity" / "repos.json"
            old = root / "herd-harness" / "repos.json"
            old.parent.mkdir(parents=True)
            expected = {"version": 1, "repos": {"legacy": {"path": "/legacy"}}}
            old.write_text(json.dumps(expected))
            with patch.object(registry, "REGISTRY", new), patch.object(registry, "LEGACY_REGISTRY", old):
                data = registry.registry_load()
            self.assertEqual(data, expected)
            self.assertTrue(new.exists())
            self.assertEqual(json.loads(new.read_text()), expected)


if __name__ == "__main__":
    unittest.main()
