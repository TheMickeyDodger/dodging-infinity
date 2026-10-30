"""Contained SYNTHETIC parent/child directories for the follow-on dispatch tests.

Nothing here touches a real Herdr, a real repository, a real runtime or the network: it builds two temporary
directories shaped like a parent Herdr and a completed, healthy child Herdr, and hands out their paths and digests.
Every test that uses it fakes the runtime interface (`herdr agent get`, the prompt) with `unittest.mock`; a test that
leaks a call to a real `herdr` binary fails loudly (see `forbid_real_runtime`).
"""

import hashlib
import json
import tempfile
from pathlib import Path
from unittest import mock

PARENT_TASK = "parent-task-1"
FIRST = "20261026-212942-aa2028"
SECOND = "20261027-120000-bb1234"
ROLES = {
    "supervisor": "child-sup",
    "lead1": "child-lead1",
    "executor1": "child-exec1",
    "reviewer1": "child-rev1",
}
SESSIONS = {role: f"sess-{role}" for role in ROLES}
PANES = {role: f"pane-{role}" for role in ROLES}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def read_json(path):
    return json.loads(path.read_text())


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def forbid_real_runtime(testcase):
    """Make any call that would reach a real `herdr` binary (or any subprocess) FAIL THE TEST.

    The modules import `run` into their own namespaces, so each is patched where it is used.
    """
    boom = AssertionError("a real subprocess / herdr call was attempted by a test that must be contained")
    for target in (
        "subprocess.run",
        "subprocess.Popen",
        "herdr.runtime.run",
        "herdr.tasks.run",
        "herdr.lifecycle.run",
    ):
        patcher = mock.patch(target, side_effect=boom)
        patcher.start()
        testcase.addCleanup(patcher.stop)


class Fixture:
    """A parent Herdr that registered ONE completed child (the investigation), and that child, healthy and idle."""

    def __init__(
        self,
        *,
        first_status="COMPLETE",
        archive=True,
        registry=True,
        parent_task_status="ACTIVE",
        parent_running=True,
        child_runtime=True,
        child_repo_in_runtime=True,
        bindings=True,
    ):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.root = root
        self.parent = root / "parent"
        self.child = root / "child"
        self.parent_state = self.parent / ".herd" / "state"
        self.child_state = self.child / ".herd" / "state"
        self.children_json = self.parent_state / "children.json"
        self.child_task_json = self.child_state / "task.json"
        self.child_runtime_json = self.child_state / "runtime.json"
        self.child_config_json = self.child / ".herd" / "herd.config.json"
        self.child_bindings_json = self.child_state / "role-bindings.json"

        write_json(self.parent / ".herd" / "herd.config.json", {"version": 4})
        write_json(self.parent_state / "task.json", {"id": PARENT_TASK, "status": parent_task_status})
        if parent_running:
            write_json(self.parent_state / "runtime.json", {"agents": {"supervisor": "parent-sup"}})

        write_json(
            self.child_config_json,
            {
                "version": 4,
                "orchestration": {"leads": 1, "pods": 1, "agent_task_timeout_ms": 600000},
                "context": {"clear_before_new_task": True},
                "policy": {
                    "rules": ["child rule"],
                    "git": {"commit": "require-human", "push": "require-human"},
                },
            },
        )
        self.first_record = {
            "version": 1,
            "id": FIRST,
            "status": first_status,
            "description": "investigation (completed)",
            "started_at": 1,
            "completed_at": 2,
            "repo": str(self.child),
            "repo_ref": str(self.child),
        }
        write_json(self.child_task_json, self.first_record)
        if archive:
            write_json(self.archive_path(FIRST), self.first_record)
        if child_runtime:
            runtime = {"version": 2, "workspace_id": "w-1", "agents": dict(ROLES), "panes": {}}
            if child_repo_in_runtime:
                runtime["repo"] = str(self.child)
            write_json(self.child_runtime_json, runtime)

        if bindings:
            # the child's saved role bindings, in `herdr.identity`'s own shape (a binding per logical role)
            write_json(
                self.child_bindings_json,
                {
                    "version": 1,
                    "roles": {
                        role: {
                            "logical": role,
                            "agent": name,
                            "session": SESSIONS[role],
                            "stable": {
                                "name": name,
                                "cwd": str(self.child),
                                "workspace_id": "w-1",
                                "pane_id": PANES[role],
                            },
                        }
                        for role, name in ROLES.items()
                    },
                },
            )

        if registry:
            write_json(
                self.children_json,
                {
                    "version": 1,
                    "children": [
                        {
                            "requested_at": 1,
                            "parent_repo": str(self.parent),
                            "parent_task_id": PARENT_TASK,
                            "dependency": True,
                            "repo": str(self.child),
                            "task_id": FIRST,
                            "task_status": "ACTIVE",
                            "workspace_id": "w-1",
                            "agents": dict(ROLES),
                        }
                    ],
                },
            )

    def cleanup(self):
        self.temp.cleanup()

    def live(self, role, status="idle", **changes):
        """What `herdr agent get <role's agent>` answers when the live agent IS the session the child bound.

        The shape is `runtime.agent_info`'s: ``{"status", "raw"}`` with the real envelope ``result.agent``. `changes` overrides or
        adds fields of the agent record (a different session, cwd, pane, `foreground_cwd`, `model` ...); a value of ``None``
        removes the field.
        """
        agent = {
            "name": ROLES[role],
            "cwd": str(self.child),
            "foreground_cwd": str(self.child),
            "workspace_id": "w-1",
            "pane_id": PANES[role],
            "agent_session": {"value": SESSIONS[role]},
            "agent_status": status,
            "revision": 7,
            "state_change_seq": 3,
            "model": "a-configured-model",
        }
        for key, value in changes.items():
            if value is None:
                agent.pop(key, None)
            else:
                agent[key] = value
        return {"status": status, "raw": {"id": "x", "result": {"agent": agent}}}

    def bindings_document(self):
        return read_json(self.child_bindings_json)

    def archive_path(self, task_id):
        return self.child_state / "tasks" / f"{task_id}.json"

    def registry(self):
        return read_json(self.children_json)

    def second_task_started(self, status="ACTIVE", task_id=SECOND):
        """The child's task.json as it is once a second task has displaced the first (the archive of the first stays)."""
        write_json(
            self.child_task_json,
            {
                "version": 1,
                "id": task_id,
                "status": status,
                "description": "follow-on",
                "started_at": 10,
                "repo": str(self.child),
                "repo_ref": str(self.child),
            },
        )

    def register_second(self, task_id=SECOND, **extra):
        registry = self.registry()
        record = {
            "requested_at": 10,
            "parent_repo": str(self.parent),
            "parent_task_id": PARENT_TASK,
            "dependency": True,
            "repo": str(self.child),
            "task_id": task_id,
            "task_status": "ACTIVE",
            "workspace_id": "w-1",
            "agents": dict(ROLES),
        }
        record.update(extra)
        registry["children"].append(record)
        write_json(self.children_json, registry)
        return record

    def digests(self):
        """sha256 of EVERY file of the child's .herd and of the parent's registry/task: the "nothing was touched" witness."""
        found = {}
        for base in (self.child / ".herd", self.parent_state):
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.name.endswith(".lock"):
                    found[str(path.relative_to(self.root))] = sha256_file(path)
        return found
