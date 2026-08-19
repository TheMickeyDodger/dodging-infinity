import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr.instance import HerdrInstance
from herdr.tasks import dispatch_task


class HerdrTaskTests(unittest.TestCase):
    def make_instance(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        herd = repo / ".herd"
        state = herd / "state"

        state.mkdir(parents=True)

        (herd / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {
                        "name": "test",
                    },
                    "orchestration": {
                        "agent_task_timeout_ms": 600000,
                    },
                    "context": {
                        "clear_before_new_task": True,
                    },
                    "policy": {
                        "rules": [
                            "Never modify documentation"
                        ],
                        "git": {
                            "commit": "require-human",
                            "push": "require-human",
                        },
                    },
                }
            )
        )

        (state / "runtime.json").write_text(
            json.dumps(
                {
                    "agents": {
                        "supervisor": "sup1",
                        "lead1": "lead1",
                        "executor1": "exec1",
                        "reviewer1": "rev1",
                    },
                    "panes": {},
                }
            )
        )

        return temp, HerdrInstance(repo)

    @patch("herdr.tasks.agent_info")
    @patch("herdr.tasks.prompt")
    def test_dispatch_creates_active_task(
        self,
        mock_prompt,
        mock_agent_info,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        mock_agent_info.return_value = {
            "status": "idle",
            "raw": {},
        }

        mock_prompt.return_value = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        task = dispatch_task(
            herd,
            "Investigate the anomaly",
        )

        self.assertEqual(
            task["status"],
            "ACTIVE",
        )

        saved = json.loads(
            (
                herd.herd_root
                / "state"
                / "task.json"
            ).read_text()
        )

        self.assertEqual(
            saved["description"],
            "Investigate the anomaly",
        )

        prompt_text = mock_prompt.call_args.args[1]

        self.assertIn(
            "Never modify documentation",
            prompt_text,
        )

    @patch("herdr.tasks.agent_info")
    def test_active_task_blocks_second_dispatch(
        self,
        mock_agent_info,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        (
            herd.herd_root
            / "state"
            / "task.json"
        ).write_text(
            json.dumps(
                {
                    "id": "existing",
                    "status": "ACTIVE",
                }
            )
        )

        with self.assertRaises(RuntimeError):
            dispatch_task(
                herd,
                "Second task",
            )

    def test_empty_task_is_rejected(self):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        with self.assertRaises(ValueError):
            dispatch_task(
                herd,
                "   ",
            )


if __name__ == "__main__":
    unittest.main()
