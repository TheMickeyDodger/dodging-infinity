import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr.instance import HerdrInstance
from herdr.lifecycle import start_herd



def _bound_probe(agent):
    """Herdr's answer for a freshly started agent.

    R-53 AQ-2: bootstrap now BINDS every role from exact live evidence
    before reporting the herd ready, so a bootstrap test has to model
    the dependency answering. It used not to: within these tests Herdr's answer had no reason to
    be stated, because `start_herd` did no probing at all. That is a
    small illustration of the ruling itself — a bootstrap that asks
    about none of its roles is unable to record what they are.
    """
    return {
        "status": "idle",
        "raw": {"result": {"agent": {
            "name": agent,
            "cwd": "/repo",
            "workspace_id": "ws1",
            "pane_id": "pane-" + agent,
            "agent_session": {"value": "sess-" + agent},
        }}},
    }


class HerdrLifecycleTests(unittest.TestCase):
    def make_instance(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        herd_root = repo / ".herd"
        (herd_root / "roles").mkdir(
            parents=True,
        )

        for role in [
            "supervisor",
            "lead",
            "executor",
            "reviewer",
        ]:
            (
                herd_root
                / "roles"
                / f"{role}.md"
            ).write_text(
                f"# {role}\n"
            )

        config = {
            "version": 4,
            "project": {
                "name": "test",
                "test_command": "pytest",
            },
            "orchestration": {
                "leads": 1,
                "pods": 1,
                "agent_start_timeout_ms": 60000,
                "shell_ready_timeout_ms": 30000,
                "agent_task_timeout_ms": 600000,
                "heartbeat_autostart": True,
            },
            "roles": {
                role: {
                    "kind": "claude",
                    "args": [],
                }
                for role in [
                    "supervisor",
                    "lead",
                    "executor",
                    "reviewer",
                ]
            },
            "policy": {},
        }

        (
            herd_root
            / "herd.config.json"
        ).write_text(
            json.dumps(config)
        )

        return temp, HerdrInstance(repo)

    @patch("herdr.lifecycle.agent_info", new=_bound_probe)
    @patch("herdr.lifecycle.run")
    @patch("herdr.lifecycle.prompt")
    @patch("herdr.lifecycle.start_agent")
    @patch("herdr.lifecycle.split")
    @patch("herdr.lifecycle.jrun")
    def test_start_builds_runtime_without_cli_bootstrap(
        self,
        mock_jrun,
        mock_split,
        mock_start_agent,
        mock_prompt,
        mock_run,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        mock_jrun.return_value = {
            "result": {
                "workspace": {
                    "workspace_id": "ws1",
                },
                "root_pane": {
                    "pane_id": "pane-root",
                },
            }
        }

        mock_split.side_effect = [
            "pane-lead",
            "pane-executor",
            "pane-reviewer",
            "pane-controller",
        ]

        mock_prompt.return_value = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        state = start_herd(herd)

        self.assertEqual(
            state["workspace_id"],
            "ws1",
        )

        self.assertIn(
            "supervisor",
            state["agents"],
        )

        self.assertIn(
            "controller",
            state["panes"],
        )

        runtime_path = (
            herd.herd_root
            / "state"
            / "runtime.json"
        )

        self.assertTrue(
            runtime_path.exists()
        )

        self.assertEqual(
            mock_start_agent.call_count,
            4,
        )

    @patch("herdr.lifecycle.agent_info", new=_bound_probe)
    @patch("herdr.lifecycle.run")
    @patch("herdr.lifecycle.prompt")
    @patch("herdr.lifecycle.start_agent")
    @patch("herdr.lifecycle.split")
    @patch("herdr.lifecycle.jrun")
    def test_bootstrap_retries_unobserved_first_delivery(
        self,
        mock_jrun,
        mock_split,
        mock_start_agent,
        mock_prompt,
        mock_run,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        mock_jrun.return_value = {
            "result": {
                "workspace": {
                    "workspace_id": "ws1",
                },
                "root_pane": {
                    "pane_id": "pane-root",
                },
            }
        }

        mock_split.side_effect = [
            "pane-lead",
            "pane-executor",
            "pane-reviewer",
            "pane-controller",
        ]

        success = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        unobserved = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                '{"error":{"code":'
                '"agent_prompt_unobserved"}}'
            ),
        )

        # supervisor succeeds
        # lead succeeds
        # executor first delivery disappears, retry succeeds
        # reviewer succeeds
        mock_prompt.side_effect = [
            success,
            success,
            unobserved,
            success,
            success,
        ]

        mock_run.return_value = success

        state = start_herd(
            herd
        )

        self.assertEqual(
            state["workspace_id"],
            "ws1",
        )

        self.assertEqual(
            mock_prompt.call_count,
            5,
        )

        executor_first = (
            mock_prompt.call_args_list[2]
        )

        executor_retry = (
            mock_prompt.call_args_list[3]
        )

        self.assertEqual(
            executor_first.args[0],
            executor_retry.args[0],
        )

        self.assertEqual(
            executor_first.args[1],
            executor_retry.args[1],
        )

    @patch("herdr.lifecycle.run")
    def test_live_runtime_requires_force(
        self,
        mock_run,
    ):
        temp, herd = self.make_instance()
        self.addCleanup(temp.cleanup)

        state_path = (
            herd.herd_root
            / "state"
            / "runtime.json"
        )
        state_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        state_path.write_text(
            json.dumps(
                {
                    "workspace_id": "ws-old",
                    "agents": {
                        "supervisor": "sup-old",
                    },
                    "panes": {},
                }
            )
        )

        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )

        with self.assertRaises(RuntimeError):
            start_herd(
                herd,
                force=False,
            )


if __name__ == "__main__":
    unittest.main()
