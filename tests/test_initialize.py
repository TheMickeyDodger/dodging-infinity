import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr.initialize import initialize_herd
from herdr.instance import HerdrInstance


class HerdrInitializeTests(unittest.TestCase):
    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        subprocess.run(
            [
                "git",
                "init",
                str(repo),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        return temp, repo

    def test_initialize_fresh_repo(self):
        temp, repo = self.make_repo()
        self.addCleanup(
            temp.cleanup
        )

        registry = (
            repo
            / "test-registry.json"
        )

        with patch(
            "herdr.registry.REGISTRY",
            registry,
        ):
            result = initialize_herd(
                repo,
                preset="max-quality",
                test_command="python -m pytest",
                alias="test-repo",
            )

        herd = HerdrInstance(
            repo
        )

        self.assertTrue(
            herd.initialized
        )

        config = herd.load_config()

        self.assertEqual(
            config["preset"],
            "max-quality",
        )

        self.assertEqual(
            config["project"]["test_command"],
            "python -m pytest",
        )

        self.assertEqual(
            result["alias"],
            "test-repo",
        )

        for role in [
            "supervisor.md",
            "lead.md",
            "executor.md",
            "reviewer.md",
        ]:
            self.assertTrue(
                (
                    herd.herd_root
                    / "roles"
                    / role
                ).exists()
            )

    def test_initialize_applies_policy(self):
        temp, repo = self.make_repo()
        self.addCleanup(
            temp.cleanup
        )

        registry = (
            repo
            / "test-registry.json"
        )

        with patch(
            "herdr.registry.REGISTRY",
            registry,
        ):
            initialize_herd(
                repo,
                policy={
                    "rules": [
                        "Keep changes minimal",
                    ],
                    "git": {
                        "push": "forbidden",
                    },
                },
            )

        policy = (
            HerdrInstance(repo)
            .effective_policy()
        )

        self.assertIn(
            "Keep changes minimal",
            policy.rules,
        )

        self.assertEqual(
            policy.get(
                "git",
                "push",
            ),
            "forbidden",
        )

    def test_initialize_installs_package_owned_guards(self):
        temp, repo = self.make_repo()
        self.addCleanup(
            temp.cleanup
        )

        registry = (
            repo
            / "test-registry.json"
        )

        with patch(
            "herdr.registry.REGISTRY",
            registry,
        ):
            initialize_herd(
                repo
            )

        git_dir_raw = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--git-dir",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        git_dir = Path(
            git_dir_raw
        )

        if not git_dir.is_absolute():
            git_dir = (
                repo
                / git_dir
            ).resolve()

        for name in [
            "pre-commit",
            "reference-transaction",
            "pre-push",
        ]:
            hook = (
                git_dir
                / "hooks"
                / name
            ).read_text()

            self.assertIn(
                "herdr.guards",
                hook,
            )

            self.assertNotIn(
                "herdctl _guard",
                hook,
            )


if __name__ == "__main__":
    unittest.main()
