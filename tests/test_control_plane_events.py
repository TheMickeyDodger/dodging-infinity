import json
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch, call

from herdr.control_plane import HerdrControlPlane


class ControlPlaneActionEventTests(unittest.TestCase):
    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    @patch("herdr.control_plane.HerdrControlPlane.emit_event")
    @patch("herdr.control_plane.initialize_herd")
    def test_initialize_emits_event(
        self,
        mock_initialize,
        mock_emit,
    ):
        repo = self.make_repo()
        mock_initialize.return_value = {"ok": True}

        result = HerdrControlPlane().initialize(
            repo,
            preset="max-quality",
            alias="demo",
        )

        self.assertEqual(result, {"ok": True})
        mock_emit.assert_called_once_with(
            repo,
            "herd.initialized",
            data={
                "preset": "max-quality",
                "alias": "demo",
            },
        )

    @patch("herdr.control_plane.HerdrControlPlane.emit_event")
    @patch("herdr.control_plane.start_herd")
    def test_start_emits_event(
        self,
        mock_start,
        mock_emit,
    ):
        repo = self.make_repo()
        mock_start.return_value = {
            "workspace_id": "ws-1",
            "agents": {
                "supervisor": "sup-1",
                "executor1": "exec-1",
            },
        }

        result = HerdrControlPlane().start(repo)

        self.assertEqual(result["workspace_id"], "ws-1")
        mock_emit.assert_called_once_with(
            repo,
            "runtime.started",
            data={
                "workspace_id": "ws-1",
                "agents": [
                    "executor1",
                    "supervisor",
                ],
            },
        )

    @patch("herdr.control_plane.HerdrControlPlane.emit_event")
    @patch("herdr.control_plane.dispatch_task")
    def test_dispatch_emits_event(
        self,
        mock_dispatch,
        mock_emit,
    ):
        repo = self.make_repo()
        mock_dispatch.return_value = {
            "id": "task-1",
            "status": "ACTIVE",
            "description": "Investigate anomaly",
        }

        result = HerdrControlPlane().dispatch_task(
            repo,
            "Investigate anomaly",
        )

        self.assertEqual(result["id"], "task-1")
        mock_emit.assert_called_once_with(
            repo,
            "task.dispatched",
            data={
                "task_id": "task-1",
                "status": "ACTIVE",
                "description": "Investigate anomaly",
            },
        )

    @patch("herdr.control_plane.HerdrControlPlane.emit_event")
    @patch("herdr.control_plane.HerdrControlPlane.spawn")
    def test_spawn_child_emits_parent_topology_event(
        self,
        mock_spawn,
        mock_emit,
    ):
        parent = self.make_repo()
        target = parent / "child"

        herd_root = parent / ".herd"
        state = herd_root / "state"
        state.mkdir(parents=True)

        (herd_root / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {
                        "name": "parent",
                    },
                }
            )
        )

        (state / "runtime.json").write_text(
            json.dumps(
                {
                    "workspace_id": "parent-ws",
                    "agents": {
                        "supervisor": "parent-sup",
                    },
                    "panes": {},
                }
            )
        )

        (state / "task.json").write_text(
            json.dumps(
                {
                    "id": "parent-task",
                    "status": "ACTIVE",
                }
            )
        )

        mock_spawn.return_value = {
            "repo": str(target.resolve()),
            "runtime": {
                "workspace_id": "child-ws",
                "agents": {
                    "supervisor": "child-sup",
                    "executor1": "child-exec",
                },
            },
            "task": {
                "id": "child-task",
                "status": "ACTIVE",
            },
        }

        result = HerdrControlPlane().spawn_child(
            parent,
            target,
            task="Investigate child repository",
        )

        self.assertEqual(
            result["child_record"]["parent_task_id"],
            "parent-task",
        )
        self.assertTrue(
            result["child_record"]["dependency"],
        )

        mock_emit.assert_called_once_with(
            parent.resolve(),
            "child.spawned",
            data={
                "child_repo": str(target.resolve()),
                "task_id": "child-task",
                "task_status": "ACTIVE",
                "workspace_id": "child-ws",
                "dependency": True,
                "parent_task_id": "parent-task",
                "agents": [
                    "executor1",
                    "supervisor",
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
