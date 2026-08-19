import json
import tempfile
import unittest
from pathlib import Path

from herdr.control_plane import HerdrControlPlane


class HerdrControlPlaneTests(unittest.TestCase):
    def make_repo(self):
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

        return temp, repo

    def test_instance(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()
        herd = cp.instance(repo)

        self.assertEqual(herd.repo, repo.resolve())

    def test_policy(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        self.assertEqual(
            cp.policy(repo).get("git", "push"),
            "require-human",
        )

    def test_set_policy(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()
        cp.set_policy(repo, "git.push", "forbidden")

        self.assertEqual(
            cp.policy(repo).get("git", "push"),
            "forbidden",
        )

    def test_spawn_composes_policy_start_and_task(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        from unittest.mock import patch

        with (
            patch.object(
                cp,
                "start",
                return_value={
                    "workspace_id": "ws1",
                },
            ) as mock_start,
            patch.object(
                cp,
                "dispatch_task",
                return_value={
                    "id": "task1",
                    "status": "ACTIVE",
                },
            ) as mock_task,
        ):
            result = cp.spawn(
                repo,
                task="Investigate anomaly",
                policy={
                    "rules": [
                        "Do not touch docs",
                    ],
                    "git": {
                        "push": "forbidden",
                    },
                },
            )

        mock_start.assert_called_once_with(
            repo.resolve(),
            force=False,
        )

        mock_task.assert_called_once_with(
            repo.resolve(),
            "Investigate anomaly",
            rejection_drill=False,
            task_policy=None,
        )

        self.assertEqual(
            result["runtime"]["workspace_id"],
            "ws1",
        )

        self.assertEqual(
            result["task"]["status"],
            "ACTIVE",
        )

        self.assertEqual(
            result["policy"]["git"]["push"],
            "forbidden",
        )

        self.assertIn(
            "Do not touch docs",
            result["policy"]["rules"],
        )

    def test_spawn_auto_initializes_fresh_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        repo = Path(temp.name)
        cp = HerdrControlPlane()

        from unittest.mock import patch

        def initialize_side_effect(
            target,
            **kwargs,
        ):
            herd = Path(target) / ".herd"
            herd.mkdir(parents=True)

            (
                herd
                / "herd.config.json"
            ).write_text(
                json.dumps(
                    {
                        "version": 4,
                        "project": {
                            "name": "test",
                        },
                        "policy": {
                            "rules": [],
                            "git": {
                                "commit": "require-human",
                                "push": "require-human",
                            },
                            "review": {
                                "required": True,
                                "max_rounds": 5,
                            },
                            "scope": {
                                "allowed": [],
                                "blocked": [],
                            },
                        },
                    }
                )
            )

            return {
                "repo": str(target),
                "created": True,
            }

        with (
            patch.object(
                cp,
                "initialize",
                side_effect=initialize_side_effect,
            ) as mock_initialize,
            patch.object(
                cp,
                "start",
                return_value={
                    "workspace_id": "ws1",
                },
            ),
            patch.object(
                cp,
                "dispatch_task",
                return_value={
                    "id": "task1",
                    "status": "ACTIVE",
                },
            ),
        ):
            result = cp.spawn(
                repo,
                task="Do something",
                preset="max-quality",
            )

        mock_initialize.assert_called_once()

        self.assertEqual(
            result["task"]["status"],
            "ACTIVE",
        )

    def test_rule_management(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        cp.add_rule(repo, "Do not touch docs")
        self.assertIn(
            "Do not touch docs",
            cp.policy(repo).rules,
        )

        cp.remove_rule(repo, "Do not touch docs")
        self.assertNotIn(
            "Do not touch docs",
            cp.policy(repo).rules,
        )


if __name__ == "__main__":
    unittest.main()
