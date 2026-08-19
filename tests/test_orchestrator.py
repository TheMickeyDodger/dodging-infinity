import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from herdr.control_plane import HerdrControlPlane
from herdr.orchestrator import (
    execute_spawn_request,
    execute_spawn_request_file,
    main,
)


class HerdrOrchestratorTests(unittest.TestCase):
    def make_parent(self):
        temp = tempfile.TemporaryDirectory()
        parent = Path(temp.name)

        state = (
            parent
            / ".herd"
            / "state"
        )

        state.mkdir(
            parents=True
        )

        (
            parent
            / ".herd"
            / "herd.config.json"
        ).write_text(
            json.dumps(
                {
                    "version": 4,
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

        (
            state
            / "task.json"
        ).write_text(
            json.dumps(
                {
                    "id": "parent-task",
                    "status": "ACTIVE",
                }
            )
        )

        (
            state
            / "runtime.json"
        ).write_text(
            json.dumps(
                {
                    "agents": {
                        "supervisor": "parent-sup",
                    },
                    "panes": {},
                }
            )
        )

        return temp, parent

    def test_spawn_child_rejects_self(self):
        temp, parent = self.make_parent()
        self.addCleanup(
            temp.cleanup
        )

        cp = HerdrControlPlane()

        with self.assertRaises(ValueError):
            cp.spawn_child(
                parent,
                parent,
                task="Recursive spawn",
            )

    def test_spawn_child_merges_rules_and_tracks_child(self):
        temp, parent = self.make_parent()
        self.addCleanup(
            temp.cleanup
        )

        child = (
            parent.parent
            / f"{parent.name}-child"
        )

        cp = HerdrControlPlane()

        cp.spawn = Mock(
            return_value={
                "repo": str(child),
                "runtime": {
                    "workspace_id": "ws-child",
                    "agents": {
                        "supervisor": "child-sup",
                    },
                },
                "task": {
                    "id": "child-task",
                    "status": "ACTIVE",
                },
                "policy": {},
            }
        )

        result = cp.spawn_child(
            parent,
            child,
            task="Child objective",
            rules=[
                "Keep it narrow",
            ],
            policy={
                "git": {
                    "push": "forbidden",
                },
            },
        )

        kwargs = cp.spawn.call_args.kwargs

        self.assertEqual(
            kwargs["policy"]["git"]["push"],
            "forbidden",
        )

        self.assertIn(
            "Keep it narrow",
            kwargs["policy"]["rules"],
        )

        children = json.loads(
            (
                parent
                / ".herd"
                / "state"
                / "children.json"
            ).read_text()
        )

        self.assertEqual(
            children["children"][0]["task_id"],
            "child-task",
        )

        self.assertEqual(
            children["children"][0]["parent_task_id"],
            "parent-task",
        )

        self.assertTrue(
            children["children"][0]["dependency"],
        )

        self.assertEqual(
            result["child_record"]["agents"]["supervisor"],
            "child-sup",
        )

    def test_structured_request_delegates_to_control_plane(self):
        temp, parent = self.make_parent()
        self.addCleanup(
            temp.cleanup
        )

        cp = Mock()

        cp.spawn_child.return_value = {
            "repo": "/tmp/child",
        }

        result = execute_spawn_request(
            parent,
            {
                "target_repo": "/tmp/child",
                "task": "Fix child issue",
                "rules": [
                    "Minimal changes only",
                ],
            },
            control_plane=cp,
        )

        cp.spawn_child.assert_called_once()

        self.assertEqual(
            result["repo"],
            "/tmp/child",
        )

    def test_cli_stdout_is_json_only(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def noisy_spawn(*args, **kwargs):
            print("Bootstrapping child...")
            print("Task dispatched...")
            return {
                "repo": "/tmp/child",
                "task": {
                    "status": "ACTIVE",
                },
            }

        with (
            patch(
                "herdr.orchestrator.execute_spawn_request_file",
                side_effect=noisy_spawn,
            ),
            patch(
                "sys.argv",
                [
                    "herdr-orchestrator",
                    "spawn",
                    "--parent",
                    "/tmp/parent",
                    "--request-file",
                    "/tmp/request.json",
                ],
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            main()

        parsed = json.loads(
            stdout.getvalue()
        )

        self.assertEqual(
            parsed["repo"],
            "/tmp/child",
        )

        self.assertNotIn(
            "Bootstrapping",
            stdout.getvalue(),
        )

        self.assertIn(
            "Bootstrapping child",
            stderr.getvalue(),
        )

    def test_request_file_cannot_escape_parent_state(self):
        temp, parent = self.make_parent()
        self.addCleanup(
            temp.cleanup
        )

        outside = (
            parent
            / "outside.json"
        )

        outside.write_text(
            "{}"
        )

        with self.assertRaises(ValueError):
            execute_spawn_request_file(
                parent,
                outside,
                control_plane=Mock(),
            )


if __name__ == "__main__":
    unittest.main()
