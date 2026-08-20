import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mission_control.audit import MissionControlAuditLog
from mission_control.context import HandoffContextAssembler
from mission_control.state import MissionControlStateStore


class FakeControlPlane:
    def __init__(self, snapshot, events=None):
        self._snapshot = snapshot
        self._events = list(events or [])
        self.event_limit = None

    def snapshot(self, repo):
        return self._snapshot

    def events(self, repo, *, limit=100):
        self.event_limit = limit
        return self._events[-limit:]


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, command, cwd):
        command = tuple(command)
        self.calls.append((command, Path(cwd)))

        key = command

        if key not in self.responses:
            raise AssertionError(
                f"Unexpected command: {command}"
            )

        response = self.responses[key]

        if isinstance(response, list):
            if not response:
                raise AssertionError(
                    f"No fake responses left for: {command}"
                )
            response = response.pop(0)

        return subprocess.CompletedProcess(
            command,
            response.get("returncode", 0),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
        )


def result(stdout="", stderr="", returncode=0):
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }


class HandoffContextAssemblerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()

        herd = self.repo / ".herd"
        herd.mkdir(parents=True)

        (herd / "herd.config.json").write_text(
            json.dumps(
                {
                    "project": {
                        "name": "demo",
                        "test_command": "python -m unittest",
                    },
                    "policy": {
                        "rules": ["Keep the boundary"],
                    },
                }
            )
        )

        store = MissionControlStateStore(self.repo)
        store.create("herd-1")
        store.attach_session("terminal-1")

        self.snapshot = {
            "schema_version": 1,
            "repo": {
                "path": str(self.repo),
                "name": "demo",
                "initialized": True,
            },
            "runtime": {
                "status": "RUNNING",
                "workspace_id": "workspace-1",
                "agents": [
                    {
                        "logical_name": "supervisor",
                        "agent": "sup-1",
                        "status": "idle",
                    },
                    {
                        "logical_name": "lead1",
                        "agent": "lead-1",
                        "status": "working",
                    },
                ],
                "panes": {},
            },
            "task": {
                "id": "task-1",
                "status": "ACTIVE",
                "started_at": 1,
                "description": "Ship the thing",
                "policy": {
                    "rules": ["Task rule"],
                },
            },
            "children": [],
            "policy": {
                "rules": ["Keep the boundary"],
            },
        }

        self.git_responses = {
            (
                "git",
                "rev-parse",
                "HEAD",
            ): result("abc123\n"),
            (
                "git",
                "branch",
                "--show-current",
            ): result("main\n"),
            (
                "git",
                "status",
                "--porcelain=v1",
                "--branch",
            ): result("## main\n M demo.py\n"),
            (
                "git",
                "diff",
                "--no-ext-diff",
                "--",
            ): result("unstaged diff\n"),
            (
                "git",
                "diff",
                "--cached",
                "--no-ext-diff",
                "--",
            ): result("staged diff\n"),
            (
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ): result(""),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def _assembler(self, *, responses=None, events=None):
        merged = dict(self.git_responses)

        if responses:
            merged.update(responses)

        return HandoffContextAssembler(
            control_plane=FakeControlPlane(
                self.snapshot,
                events=events,
            ),
            command_runner=FakeRunner(merged),
        )

    def test_assembles_full_structured_context(self):
        herd = self.repo / ".herd"
        state = herd / "state"
        memory = herd / "memory"
        reviews = state / "reviews"

        memory.mkdir(parents=True)
        reviews.mkdir(parents=True)

        (state / "supervisor-status.md").write_text(
            "Supervisor status"
        )
        (state / "task-checkpoint.md").write_text(
            "Checkpoint evidence"
        )
        (memory / "architecture.md").write_text(
            "Architecture memory"
        )
        (self.repo / "AGENTS.md").write_text(
            "Repository agent guidance"
        )
        (
            reviews / "task-1-round-01.md"
        ).write_text("Reviewer round one")

        audit = MissionControlAuditLog(self.repo)
        audit.append(
            "herd-1",
            "execution.started",
            data={
                "execution_id": "exec-1",
                "commands": ["python -m unittest"],
            },
        )
        audit.append(
            "herd-1",
            "execution.completed",
            data={
                "execution_id": "exec-1",
                "exit_code": 0,
                "handoff": "HERDR_HANDOFF:exec-1:0",
                "outcomes": [
                    {
                        "command_index": 0,
                        "command": "python -m unittest",
                        "exit_code": 0,
                    }
                ],
            },
        )

        assembler = self._assembler(
            responses={
                (
                    "herdr",
                    "agent",
                    "read",
                    "sup-1",
                    "--source",
                    "recent-unwrapped",
                    "--lines",
                    "500",
                ): result("Supervisor output"),
                (
                    "herdr",
                    "agent",
                    "read",
                    "lead-1",
                    "--source",
                    "visible",
                ): result("Lead output"),
            },
            events=[
                {
                    "type": "task.dispatched",
                    "data": {"task_id": "task-1"},
                }
            ],
        )

        context = assembler.assemble(
            self.repo,
            herd_id="herd-1",
        )
        data = context.to_dict()

        self.assertEqual(data["herd_id"], "herd-1")
        self.assertEqual(
            data["objective"]["description"],
            "Ship the thing",
        )
        self.assertEqual(
            data["objective"]["rules"]["repository"],
            {"rules": ["Keep the boundary"]},
        )
        self.assertEqual(
            data["objective"]["rules"]["task"],
            {"rules": ["Task rule"]},
        )
        self.assertEqual(
            data["objective"]["verification"]["test_command"],
            "python -m unittest",
        )

        outputs = {
            item["logical_name"]: item
            for item in data["herdr"]["agent_outputs"]
        }

        self.assertEqual(
            outputs["supervisor"]["text"],
            "Supervisor output",
        )
        self.assertEqual(
            outputs["supervisor"]["source"],
            "recent-unwrapped",
        )
        self.assertEqual(
            outputs["lead1"]["text"],
            "Lead output",
        )
        self.assertEqual(
            outputs["lead1"]["source"],
            "visible",
        )

        self.assertEqual(
            data["git"]["head"],
            "abc123",
        )
        self.assertEqual(
            data["git"]["branch"],
            "main",
        )
        self.assertEqual(
            data["git"]["unstaged_diff"],
            "unstaged diff\n",
        )
        self.assertEqual(
            data["git"]["staged_diff"],
            "staged diff\n",
        )

        self.assertEqual(
            data["artifacts"]["supervisor_status"],
            "Supervisor status",
        )
        self.assertEqual(
            data["artifacts"]["task_checkpoint"],
            "Checkpoint evidence",
        )
        self.assertEqual(
            data["artifacts"]["reviews"][0]["text"],
            "Reviewer round one",
        )
        self.assertEqual(
            data["artifacts"]["shared_context"]["architecture"],
            "Architecture memory",
        )
        self.assertEqual(
            data["artifacts"]["shared_context"]["AGENTS.md"],
            "Repository agent guidance",
        )

        self.assertEqual(
            len(data["mission_control"]["audit"]),
            2,
        )
        self.assertEqual(
            data["mission_control"][
                "latest_execution"
            ]["event_type"],
            "execution.completed",
        )
        self.assertEqual(
            data["herdr"]["events"][0]["type"],
            "task.dispatched",
        )

    def test_recent_output_falls_back_to_visible_when_agent_becomes_active(self):
        self.snapshot["runtime"]["agents"] = [
            {
                "logical_name": "supervisor",
                "agent": "sup-1",
                "status": "idle",
            }
        ]

        assembler = self._assembler(
            responses={
                (
                    "herdr",
                    "agent",
                    "read",
                    "sup-1",
                    "--source",
                    "recent-unwrapped",
                    "--lines",
                    "500",
                ): result(
                    stderr="agent_not_idle",
                    returncode=1,
                ),
                (
                    "herdr",
                    "agent",
                    "read",
                    "sup-1",
                    "--source",
                    "visible",
                ): result("Current visible output"),
            }
        )

        context = assembler.assemble(self.repo)
        output = context.herdr["agent_outputs"][0]

        self.assertEqual(output["source"], "visible")
        self.assertEqual(
            output["text"],
            "Current visible output",
        )
        self.assertIsNone(output["error"])

    def test_agent_read_failure_is_explicit_in_context(self):
        self.snapshot["runtime"]["agents"] = [
            {
                "logical_name": "lead1",
                "agent": "lead-1",
                "status": "working",
            }
        ]

        assembler = self._assembler(
            responses={
                (
                    "herdr",
                    "agent",
                    "read",
                    "lead-1",
                    "--source",
                    "visible",
                ): result(
                    stderr="read failed",
                    returncode=1,
                ),
            }
        )

        context = assembler.assemble(self.repo)
        output = context.herdr["agent_outputs"][0]

        self.assertEqual(output["text"], "")
        self.assertEqual(output["error"], "read failed")

    def test_missing_agent_does_not_trigger_live_read(self):
        self.snapshot["runtime"]["agents"] = [
            {
                "logical_name": "reviewer1",
                "agent": "reviewer-1",
                "status": "missing",
            }
        ]

        assembler = self._assembler()
        context = assembler.assemble(self.repo)

        output = context.herdr["agent_outputs"][0]

        self.assertEqual(
            output["error"],
            "agent unavailable",
        )
        self.assertIsNone(output["source"])

    def test_requires_durable_mission_control_state(self):
        other = Path(
            tempfile.mkdtemp(
                dir=self.tempdir.name
            )
        ).resolve()

        assembler = HandoffContextAssembler(
            control_plane=FakeControlPlane(
                self.snapshot
            ),
            command_runner=FakeRunner({}),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "No durable Mission Control Herd state",
        ):
            assembler.assemble(other)

    def test_requested_herd_must_match_durable_state(self):
        assembler = self._assembler()

        with self.assertRaisesRegex(
            RuntimeError,
            "does not match durable",
        ):
            assembler.assemble(
                self.repo,
                herd_id="different-herd",
            )

    def test_git_collection_fails_closed(self):
        self.snapshot["runtime"]["agents"] = []

        responses = dict(self.git_responses)
        responses[
            ("git", "rev-parse", "HEAD")
        ] = result(
            stderr="not a repository",
            returncode=128,
        )

        assembler = HandoffContextAssembler(
            control_plane=FakeControlPlane(
                self.snapshot
            ),
            command_runner=FakeRunner(responses),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Unable to collect Git HEAD",
        ):
            assembler.assemble(self.repo)

    def test_stale_prior_task_status_and_checkpoint_are_excluded(self):
        state = self.repo / ".herd" / "state"
        state.mkdir(parents=True, exist_ok=True)

        status = state / "supervisor-status.md"
        checkpoint = state / "task-checkpoint.md"

        status.write_text("old supervisor status")
        checkpoint.write_text("old checkpoint")

        self.snapshot["task"]["started_at"] = 4_000_000_000
        self.snapshot["runtime"]["agents"] = []

        assembler = self._assembler()
        context = assembler.assemble(self.repo)

        self.assertIsNone(
            context.artifacts["supervisor_status"]
        )
        self.assertIsNone(
            context.artifacts["task_checkpoint"]
        )

    def test_no_current_task_does_not_surface_old_task_artifacts(self):
        state = self.repo / ".herd" / "state"
        state.mkdir(parents=True, exist_ok=True)

        (state / "supervisor-status.md").write_text("old status")
        (state / "task-checkpoint.md").write_text("old checkpoint")

        self.snapshot["task"] = None
        self.snapshot["runtime"]["agents"] = []

        assembler = self._assembler()
        context = assembler.assemble(self.repo)

        self.assertIsNone(
            context.artifacts["supervisor_status"]
        )
        self.assertIsNone(
            context.artifacts["task_checkpoint"]
        )

    def test_latest_execution_tracks_new_unresolved_execution(self):
        self.snapshot["runtime"]["agents"] = []

        audit = MissionControlAuditLog(self.repo)
        audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-1", "commands": ["true"]},
        )
        audit.append(
            "herd-1",
            "execution.completed",
            data={
                "execution_id": "exec-1",
                "exit_code": 0,
                "handoff": "HERDR_HANDOFF:exec-1:0",
                "outcomes": [],
            },
        )
        audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-2", "commands": ["read reply"]},
        )

        assembler = self._assembler()
        context = assembler.assemble(self.repo)

        latest = context.mission_control["latest_execution"]

        self.assertEqual(
            latest["event_type"],
            "execution.started",
        )
        self.assertEqual(
            latest["data"]["execution_id"],
            "exec-2",
        )

    def test_untracked_text_file_contents_are_included(self):
        self.snapshot["runtime"]["agents"] = []
        (self.repo / "new_module.py").write_text(
            "print('new code')\n"
        )

        responses = {
            (
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ): result("new_module.py\0"),
        }

        assembler = self._assembler(responses=responses)
        context = assembler.assemble(self.repo)

        self.assertEqual(
            context.git["untracked_files"],
            [
                {
                    "path": "new_module.py",
                    "kind": "text",
                    "text": "print('new code')\n",
                }
            ],
        )

    def test_only_current_task_review_artifacts_are_included(self):
        review_root = (
            self.repo
            / ".herd"
            / "state"
            / "reviews"
        )
        review_root.mkdir(parents=True)

        (
            review_root
            / "task-1-round-02.md"
        ).write_text("round two")
        (
            review_root
            / "task-1-round-01.md"
        ).write_text("round one")
        (
            review_root
            / "old-task-round-01.md"
        ).write_text("old review")

        self.snapshot["runtime"]["agents"] = []

        assembler = self._assembler()
        context = assembler.assemble(self.repo)

        reviews = context.artifacts["reviews"]

        self.assertEqual(
            [Path(item["path"]).name for item in reviews],
            [
                "task-1-round-01.md",
                "task-1-round-02.md",
            ],
        )
        self.assertEqual(
            [item["text"] for item in reviews],
            ["round one", "round two"],
        )


if __name__ == "__main__":
    unittest.main()
