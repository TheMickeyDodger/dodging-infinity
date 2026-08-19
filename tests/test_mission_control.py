import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from herdr.control_plane import HerdrControlPlane


class MissionControlSnapshotTests(unittest.TestCase):
    @patch(
        "herdr.mission_control.agent_info",
        return_value={
            "status": "working",
            "raw": {},
        },
    )
    def test_snapshot_contract_is_serializable(
        self,
        mock_agent_info,
    ):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        repo = Path(temp.name)
        herd_root = repo / ".herd"
        state = herd_root / "state"
        state.mkdir(parents=True)

        (herd_root / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {
                        "name": "demo",
                    },
                    "policy": {
                        "rules": [
                            "Do not modify migrations",
                        ],
                    },
                }
            )
        )

        (state / "runtime.json").write_text(
            json.dumps(
                {
                    "workspace_id": "ws-demo",
                    "agents": {
                        "supervisor": "sup-demo",
                    },
                    "panes": {
                        "controller": "pane-1",
                    },
                }
            )
        )

        (state / "task.json").write_text(
            json.dumps(
                {
                    "id": "task-1",
                    "status": "ACTIVE",
                    "description": "Investigate the anomaly",
                }
            )
        )

        snapshot = HerdrControlPlane().snapshot(repo)

        self.assertEqual(
            set(snapshot),
            {
                "schema_version",
                "generated_at",
                "repo",
                "runtime",
                "task",
                "children",
                "policy",
            },
        )
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["repo"]["name"], "demo")
        self.assertTrue(snapshot["repo"]["initialized"])
        self.assertEqual(snapshot["runtime"]["status"], "RUNNING")
        self.assertEqual(snapshot["runtime"]["workspace_id"], "ws-demo")
        self.assertEqual(
            snapshot["runtime"]["agents"],
            [
                {
                    "logical_name": "supervisor",
                    "agent": "sup-demo",
                    "status": "working",
                }
            ],
        )
        self.assertEqual(snapshot["task"]["id"], "task-1")
        self.assertEqual(snapshot["children"], [])
        self.assertEqual(
            snapshot["policy"]["rules"],
            ["Do not modify migrations"],
        )

        json.dumps(snapshot)
        mock_agent_info.assert_called_once_with("sup-demo")

    def test_snapshot_resolves_child_repository_status(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        root = Path(temp.name)
        parent = root / "parent"
        child = root / "child"

        parent_state = parent / ".herd" / "state"
        child_state = child / ".herd" / "state"

        parent_state.mkdir(parents=True)
        child_state.mkdir(parents=True)

        (parent / ".herd" / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {
                        "name": "parent",
                    },
                }
            )
        )

        (child / ".herd" / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {
                        "name": "child",
                    },
                }
            )
        )

        (child_state / "task.json").write_text(
            json.dumps(
                {
                    "id": "child-task",
                    "status": "COMPLETE",
                }
            )
        )

        (parent_state / "children.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "children": [
                        {
                            "parent_task_id": "parent-task",
                            "dependency": True,
                            "repo": str(child),
                            "task_id": "child-task",
                            "task_status": "ACTIVE",
                        }
                    ],
                }
            )
        )

        snapshot = HerdrControlPlane().snapshot(parent)

        self.assertEqual(
            snapshot["repo"]["path"],
            str(parent.resolve()),
        )
        self.assertEqual(
            snapshot["runtime"]["status"],
            "STOPPED",
        )
        self.assertEqual(len(snapshot["children"]), 1)

        child_snapshot = snapshot["children"][0]

        self.assertEqual(
            child_snapshot["repo"],
            str(child),
        )
        self.assertEqual(
            child_snapshot["task_id"],
            "child-task",
        )
        self.assertEqual(
            child_snapshot["current_status"],
            "COMPLETE",
        )
        self.assertTrue(
            child_snapshot["dependency"],
        )


if __name__ == "__main__":
    unittest.main()
