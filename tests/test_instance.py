import json
import tempfile
import unittest
from pathlib import Path

from herdr.instance import HerdrInstance


class HerdrInstanceTests(unittest.TestCase):
    def make_instance(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)
        herd = repo / ".herd"
        herd.mkdir()

        (herd / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {"name": "test"},
                    "policy": {
                        "rules": [],
                        "git": {
                            "commit": "require-human",
                            "push": "require-human",
                        },
                    },
                }
            )
        )

        return temp, HerdrInstance(repo)

    def test_initialized(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        self.assertTrue(herd.initialized)

    def test_effective_policy(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        policy = herd.effective_policy()

        self.assertEqual(
            policy.get("git", "push"),
            "require-human",
        )

    def test_set_policy(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        herd.set_policy("git.push", "forbidden")

        self.assertEqual(
            herd.effective_policy().get("git", "push"),
            "forbidden",
        )

    def test_add_rule(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        herd.add_rule("Never touch documentation")

        self.assertIn(
            "Never touch documentation",
            herd.effective_policy().rules,
        )

    def test_remove_rule(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        herd.add_rule("Temporary rule")
        herd.remove_rule("Temporary rule")

        self.assertNotIn(
            "Temporary rule",
            herd.effective_policy().rules,
        )

    def test_merge_policy(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        herd.add_rule(
            "Existing rule"
        )

        herd.merge_policy(
            {
                "rules": [
                    "New rule",
                ],
                "git": {
                    "push": "forbidden",
                },
            }
        )

        policy = herd.effective_policy()

        self.assertEqual(
            policy.get("git", "push"),
            "forbidden",
        )

        self.assertEqual(
            policy.rules,
            [
                "Existing rule",
                "New rule",
            ],
        )

    def test_invalid_policy_is_not_written(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        before = herd.config_path.read_text()

        with self.assertRaises(ValueError):
            herd.set_policy("git.push", "YOLO")

        after = herd.config_path.read_text()

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
