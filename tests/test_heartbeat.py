import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr.heartbeat import (
    heartbeat_once,
    heartbeat_process_command,
)
from herdr.instance import HerdrInstance


class HerdrHeartbeatTests(unittest.TestCase):
    def make_instance(
        self,
        *,
        task_status="IDLE",
    ):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        state = repo / ".herd" / "state"
        state.mkdir(parents=True)

        (
            repo
            / ".herd"
            / "herd.config.json"
        ).write_text(
            json.dumps(
                {
                    "version": 4,
                    "orchestration": {
                        "heartbeat_seconds": 900,
                    },
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
                        "supervisor": "sup1",
                        "lead1": "lead1",
                    },
                    "panes": {
                        "controller": "pane-controller",
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
                    "id": "task1",
                    "status": task_status,
                    "heartbeat_count": 0,
                }
            )
        )

        return temp, HerdrInstance(repo)

    @patch("herdr.heartbeat.prompt")
    def test_inactive_task_does_not_prompt(
        self,
        mock_prompt,
    ):
        temp, herd = self.make_instance(
            task_status="IDLE"
        )
        self.addCleanup(temp.cleanup)

        result = heartbeat_once(herd)

        self.assertEqual(
            result,
            "skipped",
        )
        mock_prompt.assert_not_called()

    @patch("herdr.heartbeat.agent_info")
    @patch("herdr.heartbeat.prompt")
    def test_active_idle_supervisor_gets_heartbeat(
        self,
        mock_prompt,
        mock_agent_info,
    ):
        temp, herd = self.make_instance(
            task_status="ACTIVE"
        )
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

        result = heartbeat_once(herd)

        self.assertEqual(
            result,
            "ok",
        )

        task = json.loads(
            (
                herd.herd_root
                / "state"
                / "task.json"
            ).read_text()
        )

        self.assertEqual(
            task["heartbeat_count"],
            1,
        )

    def test_process_command_has_no_herdctl_dependency(
        self,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        command = heartbeat_process_command(
            herd.repo
        )

        self.assertNotIn(
            "herdctl.py",
            command,
        )

        self.assertIn(
            "heartbeat.py",
            command,
        )


if __name__ == "__main__":
    unittest.main()
