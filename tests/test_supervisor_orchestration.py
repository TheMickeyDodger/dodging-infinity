import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr.heartbeat import heartbeat_message
from herdr.instance import HerdrInstance
from herdr.lifecycle import bootstrap_text


class SupervisorOrchestrationContractTests(unittest.TestCase):
    def make_herd(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        herd_root = repo / ".herd"
        roles = herd_root / "roles"

        roles.mkdir(
            parents=True
        )

        for name in [
            "supervisor.md",
            "lead.md",
            "executor.md",
            "reviewer.md",
        ]:
            (
                roles
                / name
            ).write_text(
                f"# {name}\n"
            )

        config = {
            "version": 4,
            "project": {
                "name": repo.name,
                "test_command": "",
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

        (
            herd_root
            / "herd.config.json"
        ).write_text(
            json.dumps(config)
        )

        return (
            temp,
            HerdrInstance(repo),
            config,
        )

    def test_bootstrap_forbids_inference_of_undispatched_task(self):
        temp, herd, config = self.make_herd()
        self.addCleanup(
            temp.cleanup
        )

        config["project"]["test_command"] = (
            "test -f phase8-proof.txt"
        )

        agents = {
            "supervisor": "sup1",
            "lead1": "lead1",
            "executor1": "exec1",
            "reviewer1": "rev1",
        }

        for logical, role_type in (
            ("supervisor", "supervisor"),
            ("lead1", "lead"),
            ("executor1", "executor"),
            ("reviewer1", "reviewer"),
        ):
            text = bootstrap_text(
                herd,
                logical,
                role_type,
                agents,
                config,
            )

            self.assertIn(
                "No engineering task is active during bootstrap.",
                text,
            )
            self.assertIn(
                "Do not infer or begin work from the verification command",
                text,
            )
            self.assertIn(
                "Wait for an explicit task or delegation prompt",
                text,
            )

    def test_supervisor_receives_child_spawn_bridge(self):
        temp, herd, config = self.make_herd()
        self.addCleanup(
            temp.cleanup
        )

        agents = {
            "supervisor": "sup1",
            "lead1": "lead1",
            "executor1": "exec1",
            "reviewer1": "rev1",
        }

        text = bootstrap_text(
            herd,
            "supervisor",
            "supervisor",
            agents,
            config,
        )

        self.assertIn(
            "## Child Herdr orchestration",
            text,
        )

        self.assertIn(
            "-m herdr.orchestrator spawn",
            text,
        )

        self.assertIn(
            ".herd/state/spawn-requests/<name>.json",
            text,
        )

        self.assertNotIn(
            "{spawn_command}",
            text,
        )

        self.assertIn(
            "Spawn success is NOT child completion.",
            text,
        )

        self.assertIn(
            "required dependency",
            text,
        )

    def test_non_supervisor_does_not_receive_spawn_bridge(self):
        temp, herd, config = self.make_herd()
        self.addCleanup(
            temp.cleanup
        )

        agents = {
            "supervisor": "sup1",
            "lead1": "lead1",
            "executor1": "exec1",
            "reviewer1": "rev1",
        }

        text = bootstrap_text(
            herd,
            "lead1",
            "lead",
            agents,
            config,
        )

        self.assertNotIn(
            "## Child Herdr orchestration",
            text,
        )

        self.assertNotIn(
            "herdr.orchestrator",
            text,
        )

    @patch("herdr.heartbeat.agent_info")
    def test_heartbeat_preserves_delegation_boundary(
        self,
        mock_agent_info,
    ):
        temp, herd, _config = self.make_herd()
        self.addCleanup(
            temp.cleanup
        )

        mock_agent_info.return_value = {
            "status": "idle",
            "raw": {},
        }

        text = heartbeat_message(
            herd,
            {
                "agents": {
                    "supervisor": "sup1",
                },
            },
            {
                "id": "task1",
                "status": "ACTIVE",
            },
        )

        self.assertIn(
            "delegated to child Herdrs through the Control Plane",
            text,
        )


if __name__ == "__main__":
    unittest.main()
