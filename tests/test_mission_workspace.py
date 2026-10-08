"""Task 8 final: automatic, durable, isolated Mission workspaces.

An approved Mission is dispatched WITHOUT a caller-supplied path: once a
local repository and a workspaces root are configured, DI prepares ONE
Git worktree per Mission at a path derived from the Mission id alone,
records that exact binding durably BEFORE the run intent, and dispatches
there. The explicit workspace path stays available as an operator
recovery override under the same checks.

What is REAL here: Git, local only (``init``, a baseline commit through
the hermetic helper, ``worktree add``/``list``, ``rev-parse``, ``status``,
``remote get-url``; nothing is cloned or fetched and nothing contacts a
remote: ``origin`` is a URL nothing ever connects to), the filesystem
under one temporary directory (never the real managed workspaces root),
Mission Core, the local request surface and the run bridge.

Claude workspace trust is REAL too, through the existing managed-workspace
seam (``RuntimeWorker`` over ``workspace_trust``), but only ever against a
TEMPORARY configuration file: ``workspace_trust.default_config_path`` is
patched to it for the point-of-use check, and the bridge's production
config-path helper is a tripwire. Nothing here reads or writes the real
``$HOME`` configuration, and trust proven against a temporary file is not
evidence of a live phone-initiated run.

What is SYNTHETIC, labelled so: the spawn bridge, the read-only
observers, the protected-surface digest and the process-ownership seam
are the recorders of ``tests/test_mission_bridge.py``. Nothing is
spawned, signalled or reaped, and no Herdr runs. ``Crash`` is a
synthetic process death raised at a chosen point.

Termination rule (CONTRIBUTING.md): every test runs under the SIGALRM
watchdog of ``tests/test_mission_bridge.py`` (``Bounded``), and the real
spawn bridge is replaced by a tripwire that fails the test if reached;
every Git child is a bounded local command.
"""

import ast
import contextlib
import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from grok_bot import adapter as adapter_module  # noqa: E402
from grok_bot import index as index_module  # noqa: E402
from grok_bot import mcp as mcp_module  # noqa: E402
from local_request import cli as request_cli  # noqa: E402
from local_request import store as store_module  # noqa: E402
from local_request import surface as surface_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from target_runtime import evidence as evidence_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from target_runtime import mission_workspace as workspace_module  # noqa: E402
from target_runtime import workspace_trust as trust_module  # noqa: E402
from target_runtime.worker import RuntimeWorker  # noqa: E402
from target_runtime.git_transport import (  # noqa: E402
    GitTransport,
    GitTransportError,
)

from _hermetic_git import run_git  # noqa: E402
from test_local_request import NOW, Clock  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    AUTHENTICATED,
    OTHER_URL,
    REPO_URL,
    RESULT_TEXT,
    REVIEW_TEXT,
    SURFACE,
    TASK_ID,
    TESTS_PASS_DIGEST,
    Bounded,
    FakeOwnership,
    Observer,
    SpawnRecorder,
    raw_observation,
    run_request,
    sha256,
)

ACTIVE = bridge_module.PROBLEM_WORKSPACE_ACTIVE


class Crash(BaseException):
    """A synthetic process death at a chosen point. Not an ``Exception``,
    so nothing in the code under test can catch it and carry on."""


def make_repository(parent, name, url=REPO_URL):
    """A local repository with one baseline commit and an ``origin`` URL
    nothing ever connects to. ``.herd`` is ignored, as in a Herdr-managed
    repository, so Herdr state never shows in ``git status``."""
    path = os.path.join(parent, name)
    run_git("init", "-q", path)
    with open(os.path.join(path, ".git", "info", "exclude"), "a") as handle:
        handle.write(".herd/\n")
    with open(os.path.join(path, "README.md"), "w") as handle:
        handle.write("baseline\n")
    run_git("-C", path, "add", "README.md")
    run_git("-C", path, "commit", "-q", "-m", "baseline")
    run_git("-C", path, "remote", "add", "origin", url)
    return os.path.realpath(path)


def add_commit(path, name):
    with open(os.path.join(path, name), "w") as handle:
        handle.write(name + "\n")
    run_git("-C", path, "add", name)
    run_git("-C", path, "commit", "-q", "-m", name)
    return head(path)


def head(path):
    return run_git("-C", path, "rev-parse", "HEAD")


def worktrees(repository):
    """Every LINKED worktree the repository registers, by realpath."""
    text = run_git("-C", repository, "worktree", "list", "--porcelain")
    listed = {}
    for block in [b for b in text.split("\n\n") if b.strip()][1:]:
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value if value else True
        if not isinstance(fields.get("worktree"), str):
            continue  # a registration whose gitdir names no usable path
        listed[os.path.realpath(fields["worktree"])] = fields
    return listed


def snapshot(path):
    """Every file under ``path`` with its bytes (``.git``, a directory in a
    checkout and a file in a linked worktree, excluded)."""
    found = {}
    for directory, names, files in os.walk(path):
        names[:] = [n for n in names if n != ".git"]
        for name in [n for n in files if n != ".git"]:
            full = os.path.join(directory, name)
            with open(full, "rb") as handle:
                found[os.path.relpath(full, path)] = handle.read()
    return found


class RecordingTransport(object):
    """The REAL transport, with one injectable fault before or after the
    worktree is added."""

    def __init__(self, before=None, after=None):
        self.real = GitTransport()
        self.before, self.after = before, after
        self.adds = []

    def __getattr__(self, name):
        return getattr(self.real, name)

    def add_worktree(self, repository, path, commit_sha, lock_reason):
        self.adds.append(path)
        if self.before is not None:
            hook, self.before = self.before, None
            hook()
        self.real.add_worktree(repository, path, commit_sha, lock_reason)
        if self.after is not None:
            hook, self.after = self.after, None
            hook()


class Fixture(Bounded):
    def setUp(self):
        super(Fixture, self).setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.state = os.path.join(self.base, "state")
        self.clock = Clock(NOW)
        self.missions = self.service()
        self.surface = surface_module.LocalRequestSurface(
            self.missions, store_module.LocalRequestStore(self.state), self.clock)
        self.observer = Observer()
        self.spawn = SpawnRecorder(self.observer, self.clock)
        self.ownership = FakeOwnership()
        self.repository = make_repository(self.base, "repository")
        self.root = os.path.join(self.base, "workspaces")
        os.mkdir(self.root, 0o700)
        self.control = os.path.join(self.base, "control")
        os.mkdir(self.control)
        self.surface_digest = {"status": "exact", "digest": SURFACE}
        # Independent bound: the real spawn bridge is a tripwire.
        real_spawn = dispatch_module.production_spawn

        def tripwire(*args, **kwargs):
            raise AssertionError("the real spawn bridge was reached")

        dispatch_module.production_spawn = tripwire
        self.addCleanup(setattr, dispatch_module, "production_spawn", real_spawn)
        # Trust goes to a TEMPORARY configuration only: the point-of-use
        # check reads it as the one the child would consult, and the
        # production config-path helper is a tripwire.
        self.trust_config = os.path.join(self.base, "claude-config.json")
        with open(self.trust_config, "w") as handle:
            json.dump({"numStartups": 3, "projects": {}}, handle)
        self.patch(trust_module, "default_config_path",
                   lambda home=None: self.trust_config)

        def live_config_tripwire():
            raise AssertionError("the live trust configuration was resolved")

        self.patch(bridge_module, "production_trust_config_path",
                   live_config_tripwire)
        self.bridge = self.make_bridge()
        self.context = self.bridge._context

    # -- construction ---------------------------------------------------

    def patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def trusted(self, path):
        return trust_module.is_trusted(self.trust_config, path)

    def service(self):
        return mission_service.MissionService(
            mission_store.MissionStore(self.state), self.clock)

    def records(self, repo):
        listed = [dict(r) for r in self.spawn.records]
        return {"state": "available" if listed else "empty", "truncated": False,
                "listed": listed, "count": len(listed)}

    def make_bridge(self, **changes):
        options = dict(
            spawn_fn=self.spawn, observer_fn=self.observer,
            spawn_records_fn=self.records, transport=GitTransport(),
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            trust_config_path=self.trust_config)
        options.update(changes)
        missions = options.pop("missions", None) or self.missions
        return bridge_module.MissionBridge(missions, self.control, self.clock,
                                           **options)

    # -- flows ----------------------------------------------------------

    def submitted(self, objective):
        return self.surface.submit(run_request(objective=objective))

    def approved(self, objective="automatic workspace"):
        out = self.submitted(objective)
        presented = self.surface.present(out["request_ref"])
        self.surface.attest_approval(
            request_ref=out["request_ref"], mission_id=presented["mission_id"],
            revision=presented["revision"],
            proposal_digest_sha256=presented["proposal_digest_sha256"],
            approved_action_scope=presented["approved_action_scope"],
            approved_delivery_targets=presented["approved_delivery_targets"],
            expires_at=self.clock() + 600, relayed_reply="approved",
            relay_ref="synthetic-relay")
        return out["mission_id"]

    def prepared_only(self, objective="prepared, not dispatched"):
        """A Mission whose worktree is prepared and durably bound, but whose
        dispatch stopped before the intent (the protected-surface digest
        was unreadable, a non-durable refusal after preparation)."""
        mission_id = self.approved(objective)
        self.surface_digest = {"status": "unreadable", "digest": None}
        self.refused(bridge_module.PROBLEM_SURFACE_UNREADABLE,
                     self.bridge.dispatch, mission_id)
        self.surface_digest = {"status": "exact", "digest": SURFACE}
        binding = self.run_block(mission_id)["workspace"]
        self.assertEqual(binding["state"], mission_record.WORKSPACE_STATE_PREPARED)
        self.assertIsNone(self.run_block(mission_id)["intent"])
        alias = dispatch_module.ALIAS_PREFIX + mission_id
        self.assertEqual([c for c in self.spawn.calls if c[1]["alias"] == alias], [])
        return mission_id

    def mission(self, mission_id):
        return self.missions.get(mission_id)["record"]

    def run_block(self, mission_id):
        return self.mission(mission_id).get("run") or {}

    def path(self, mission_id):
        return os.path.join(self.root, mission_id)

    def refused(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(bridge_module.MissionBridgeRefusal) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, caught.exception.reason)
        return caught.exception

    def assert_refused_durably(self, mission_id, problem, error=None):
        run = self.run_block(mission_id)
        self.assertIsNone(run.get("intent"))
        self.assertEqual(run["workspace_refusal"]["problem"], problem)
        self.assertGreaterEqual(run["workspace_refusal"]["refusals"], 1)
        if error is not None:
            self.assertTrue(error.details["refusal_recorded"])
        status = self.bridge.status(mission_id)
        self.assertEqual(status["latest_workspace_refusal"]["problem"], problem)
        self.assertEqual(status["phase"], surface_module.RUN_PHASE_NOT_DISPATCHED)
        alias = dispatch_module.ALIAS_PREFIX + mission_id
        self.assertEqual([c for c in self.spawn.calls if c[1]["alias"] == alias], [])

    def state_op(self, mission_id, method, *args):
        operation = self.missions.mint_state_operation_id(AUTHENTICATED)
        sequence = self.missions.get_state(mission_id)["sequence"]
        return method(mission_id, operation, sequence, *(args + (AUTHENTICATED,)))


class AutomaticPreparationTests(Fixture):
    """Criteria 1 and 2: automatic, deterministic, recorded before the
    intent, which names exactly the prepared path."""

    def test_A1_dispatch_without_a_path_prepares_an_isolated_worktree(self):
        mission_id = self.approved()
        result = self.bridge.dispatch(mission_id)
        path = self.path(mission_id)
        self.assertEqual(result["phase"], "dispatched_not_yet_observed")
        listed = worktrees(self.repository)
        self.assertEqual(sorted(listed), [path])
        binding = self.run_block(mission_id)["workspace"]
        self.assertEqual(binding["state"], mission_record.WORKSPACE_STATE_PREPARED)
        self.assertEqual(binding["path_realpath"], path)
        self.assertEqual(binding["repository_realpath"], self.repository)
        self.assertEqual(binding["target_repository_url"], REPO_URL)
        self.assertEqual(binding["baseline_commit_sha"], head(self.repository))
        # The worktree is detached at the baseline and carries the DI lock
        # marker naming this Mission's exact binding.
        self.assertIs(listed[path]["detached"], True)
        self.assertEqual(listed[path]["HEAD"], head(self.repository))
        self.assertEqual(listed[path]["locked"], workspace_module.lock_marker(
            mission_id, binding["binding_digest_sha256"]))
        self.assertEqual(binding["binding_digest_sha256"],
                         mission_record.workspace_binding_digest(
                             mission_id, path, self.repository, REPO_URL, 1,
                             binding["proposal_digest_sha256"],
                             head(self.repository)))
        self.assertEqual(run_git("-C", path, "status", "--porcelain"), "")
        self.assertEqual(snapshot(path), {"README.md": b"baseline\n"})
        # The intent names exactly the prepared path and baseline.
        intent = self.run_block(mission_id)["intent"]
        self.assertEqual(intent["workspace_realpath"], path)
        self.assertEqual(intent["observed_baseline_commit_sha"],
                         binding["baseline_commit_sha"])
        self.assertLessEqual(binding["prepared_at"], intent["recorded_at"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.spawn.calls[0][0], self.control)
        self.assertEqual(self.spawn.calls[0][1]["target_repo"], path)
        self.assertEqual(result["workspace"]["path_realpath"], path)
        self.assertEqual(result["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARED)
        self.assertTrue(self.trusted(path))
        self.assertIsNone(result["latest_workspace_refusal"])

    def test_A2_the_path_is_derived_from_the_mission_identity_alone(self):
        a = self.approved("mission A")
        b = self.approved("mission B")
        for mission_id in (a, b):
            first = workspace_module.workspace_path(self.root, mission_id)
            self.clock.now += 1000
            second = workspace_module.workspace_path(self.root, mission_id)
            self.assertEqual(first, second)
            self.assertEqual(first, os.path.join(self.root, mission_id))
        self.assertNotEqual(self.path(a), self.path(b))
        self.clock.now = NOW
        self.make_bridge().dispatch(a)
        self.make_bridge().dispatch(b)
        self.assertEqual(sorted(worktrees(self.repository)),
                         sorted([self.path(a), self.path(b)]))
        # No randomness and no machine path in the derivation's module.
        source = (REPO_ROOT / "target_runtime" / "mission_workspace.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertFalse(imported & {"random", "secrets", "uuid", "time"},
                         imported)
        for literal in ("/Users/", "/home/", "/private/", "/var/", "/tmp"):
            self.assertNotIn(literal, source)

    def test_A3_preparation_is_durable_before_the_intent_is_recorded(self):
        mission_id = self.approved()
        seen = []
        real = self.missions.record_run_intent

        def observe_then_record(*args):
            binding = self.service().get(mission_id)["record"]["run"]["workspace"]
            seen.append((binding["state"], binding["path_realpath"],
                         sorted(worktrees(self.repository))))
            return real(*args)

        self.missions.record_run_intent = observe_then_record
        self.bridge.dispatch(mission_id)
        self.assertEqual(seen, [(mission_record.WORKSPACE_STATE_PREPARED,
                                 self.path(mission_id), [self.path(mission_id)])])

    def test_A4_intent_recorded_is_still_not_anything_started(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        held = self.bridge.dispatch(mission_id)
        self.assertTrue(held["hold"])
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")
        self.assertIsNone(self.run_block(mission_id)["receipt"])
        self.assertEqual(held["workspace"]["path_realpath"], self.path(mission_id))


class ExactReuseTests(Fixture):
    """Criterion 3: only an exactly matching prepared worktree is reused."""

    def test_R1_a_restarted_retry_reuses_exactly_the_prepared_worktree(self):
        mission_id = self.prepared_only()
        before = worktrees(self.repository)
        self.assertEqual(sorted(before), [self.path(mission_id)])
        restarted = self.make_bridge(missions=self.service())
        result = restarted.dispatch(mission_id)
        self.assertEqual(result["phase"], "dispatched_not_yet_observed")
        self.assertEqual(worktrees(self.repository), before)
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.run_block(mission_id)["intent"]["workspace_realpath"],
                         self.path(mission_id))

    def test_R2_reuse_keeps_the_recorded_baseline_when_the_repository_moved(self):
        mission_id = self.prepared_only()
        baseline = self.run_block(mission_id)["workspace"]["baseline_commit_sha"]
        moved = add_commit(self.repository, "later.txt")
        self.assertNotEqual(moved, baseline)
        self.make_bridge().dispatch(mission_id)
        intent = self.run_block(mission_id)["intent"]
        self.assertEqual(intent["observed_baseline_commit_sha"], baseline)
        self.assertEqual(head(self.path(mission_id)), baseline)
        self.assertEqual(len(worktrees(self.repository)), 1)


class RefusalTests(Fixture):
    """Criterion 4: each refusal is truthful and durable, and leaves no new
    worktree and no run intent."""

    def test_F1_path_collision_with_an_unrelated_directory(self):
        mission_id = self.approved()
        os.makedirs(self.path(mission_id))
        with open(os.path.join(self.path(mission_id), "notes.txt"), "w") as handle:
            handle.write("someone else's\n")
        before = snapshot(self.path(mission_id))
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_COLLISION,
                             self.bridge.dispatch, mission_id)
        self.assertIn("never adopted", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_COLLISION,
                                    error)
        self.assertNotIn("workspace", self.run_block(mission_id))
        self.assertEqual(snapshot(self.path(mission_id)), before)
        self.assertEqual(worktrees(self.repository), {})

    def test_F2_path_collision_with_a_file_or_an_empty_directory(self):
        for shape in ("file", "empty"):
            with self.subTest(shape=shape):
                mission_id = self.approved("collision " + shape)
                if shape == "file":
                    with open(self.path(mission_id), "w") as handle:
                        handle.write("x")
                else:
                    os.mkdir(self.path(mission_id))
                self.refused(bridge_module.PROBLEM_WORKSPACE_COLLISION,
                             self.bridge.dispatch, mission_id)
                self.assert_refused_durably(
                    mission_id, bridge_module.PROBLEM_WORKSPACE_COLLISION)
                self.assertEqual(worktrees(self.repository), {})

    def test_F3_a_dirty_prepared_worktree_is_refused_and_preserved(self):
        mission_id = self.prepared_only()
        scratch = os.path.join(self.path(mission_id), "scratch.txt")
        with open(scratch, "w") as handle:
            handle.write("uncommitted\n")
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_NOT_CLEAN,
                             self.make_bridge().dispatch, mission_id)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_NOT_CLEAN,
                                    error)
        self.assertTrue(os.path.exists(scratch))
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARED)

    def test_F4_a_moved_head_conflicts_with_the_binding(self):
        mission_id = self.prepared_only()
        later = add_commit(self.repository, "later.txt")
        run_git("-C", self.path(mission_id), "checkout", "-q", "--detach", later)
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_CONFLICT,
                             self.make_bridge().dispatch, mission_id)
        self.assertIn("baseline", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_CONFLICT)
        self.assertEqual(head(self.path(mission_id)), later)

    def test_F5_another_configured_repository_conflicts_with_the_binding(self):
        mission_id = self.prepared_only()
        other = make_repository(self.base, "another-clone")
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_CONFLICT,
                             self.make_bridge(workspace_repository=other).dispatch,
                             mission_id)
        self.assertIn("binding", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_CONFLICT)
        self.assertEqual(worktrees(other), {})

    def test_F6_a_worktree_not_prepared_by_di_is_foreign(self):
        for marker in (None, "a human's own lock"):
            with self.subTest(marker=marker):
                mission_id = self.approved("foreign %r" % marker)
                argv = ["-C", self.repository, "worktree", "add", "-q", "--detach"]
                if marker is not None:
                    argv += ["--lock", "--reason", marker]
                run_git(*(argv + [self.path(mission_id), "HEAD"]))
                before = worktrees(self.repository)[self.path(mission_id)]
                error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                                     self.bridge.dispatch, mission_id)
                self.assertIn("not prepared by DI", error.reason)
                self.assert_refused_durably(mission_id,
                                            bridge_module.PROBLEM_WORKSPACE_FOREIGN)
                self.assertNotIn("workspace", self.run_block(mission_id))
                self.assertEqual(worktrees(self.repository)[self.path(mission_id)],
                                 before)

    def test_F7_an_exact_looking_worktree_without_a_durable_binding_is_foreign(self):
        mission_id = self.approved()
        digest = mission_record.workspace_binding_digest(
            mission_id, self.path(mission_id), self.repository,
            REPO_URL, 1, self.mission(mission_id)["revisions"][0][
                "proposal_digest_sha256"], head(self.repository))
        run_git("-C", self.repository, "worktree", "add", "-q", "--detach",
                "--lock", "--reason", workspace_module.lock_marker(mission_id, digest),
                self.path(mission_id), "HEAD")
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                             self.bridge.dispatch, mission_id)
        self.assertIn("ownership", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_FOREIGN)
        # The recovery path names it exactly, and is refused all the same: a
        # path or a marker never makes a binding.
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                             self.bridge.dispatch, mission_id, self.path(mission_id))
        self.assertIn("never makes one", error.reason)
        self.assertNotIn("workspace", self.run_block(mission_id))

    def test_F8_a_symlink_or_another_repositorys_checkout_is_foreign(self):
        elsewhere = make_repository(self.base, "elsewhere")
        for shape in ("symlink", "checkout"):
            with self.subTest(shape=shape):
                mission_id = self.approved("foreign " + shape)
                if shape == "symlink":
                    os.symlink(elsewhere, self.path(mission_id))
                else:
                    run_git("-C", elsewhere, "worktree", "add", "-q", "--detach",
                            self.path(mission_id), "HEAD")
                self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                             self.bridge.dispatch, mission_id)
                self.assert_refused_durably(mission_id,
                                            bridge_module.PROBLEM_WORKSPACE_FOREIGN)
                self.assertEqual(worktrees(self.repository), {})

    def test_F9_a_path_bound_to_another_live_run_is_active(self):
        a = self.approved("mission A")
        b = self.approved("mission B")
        # Mission A's live run is bound to the path B would derive (a record
        # written before automatic workspaces existed, say).
        self.missions.record_workspace_binding(
            a, self.path(b), self.repository, REPO_URL, head(self.repository),
            self.context)
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_ACTIVE,
                             self.bridge.dispatch, b)
        self.assertIn(a, error.reason)
        self.assert_refused_durably(b, bridge_module.PROBLEM_WORKSPACE_ACTIVE, error)
        self.assertNotIn("workspace", self.run_block(b))
        self.assertFalse(os.path.lexists(self.path(b)))
        self.assertEqual(worktrees(self.repository), {})

    def test_F10_another_missions_preserved_worktree_is_never_rebound(self):
        a = self.approved("mission A")
        self.bridge.dispatch(a)
        self.bridge.cancel(a)
        self.assertEqual(self.mission(a)["state"], "CANCELLED")
        b = self.approved("mission B")
        with self.assertRaises(mission_record.MissionError) as caught:
            self.missions.record_workspace_binding(
                b, self.path(a), self.repository, REPO_URL, head(self.repository),
                self.context)
        self.assertEqual(caught.exception.problem,
                         mission_record.PROBLEM_RUN_WORKSPACE_PRESERVED)
        # Through the bridge, a path inside the managed root that is not the
        # Mission's own is foreign, before Mission Core is even asked.
        self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                     self.bridge.dispatch, b, self.path(a))
        self.assert_refused_durably(b, bridge_module.PROBLEM_WORKSPACE_FOREIGN)
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(a)])

    def test_F11_a_prepared_worktree_that_vanished_is_never_recreated(self):
        mission_id = self.prepared_only()
        # Simulates an outside deletion, which this change never performs.
        shutil.rmtree(self.path(mission_id))
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_CONFLICT,
                             self.make_bridge().dispatch, mission_id)
        self.assertIn("never re-created", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_CONFLICT)
        self.assertFalse(os.path.lexists(self.path(mission_id)))


class WorktreeIdentityTests(Fixture):
    """Round 2, required fix 1: reuse needs RECIPROCAL worktree identity.
    Exactly one administrative directory of the configured repository must
    point back at the checkout, and the checkout's own ``.git`` pointer must
    resolve to exactly that directory; otherwise the checkout could share
    another worktree's HEAD and index. Each guard is isolated so that
    removing it fails a test, through the automatic and the recovery path,
    before trust and before any intent. Nothing is ever repaired."""

    def crash(self):
        raise Crash()

    def admin_dir(self, path):
        return os.path.realpath(run_git(
            "-C", path, "rev-parse", "--path-format=absolute", "--git-dir"))

    def other_worktree(self, name):
        other = os.path.join(self.base, name)
        run_git("-C", self.repository, "worktree", "add", "-q", "--detach",
                other, head(self.repository))
        return other

    def write(self, path, text):
        with open(path, "w") as handle:
            handle.write(text)

    def read(self, path):
        with open(path) as handle:
            return handle.read()

    def assert_foreign_both_ways(self, mission_id, words):
        for recovery in (None, self.path(mission_id)):
            with self.subTest(recovery_path=recovery):
                error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                                     self.make_bridge().dispatch, mission_id,
                                     recovery)
                self.assertIn(words, error.reason)
                self.assert_refused_durably(
                    mission_id, bridge_module.PROBLEM_WORKSPACE_FOREIGN, error)

    def test_W1_a_checkout_repointed_at_another_worktrees_metadata_is_refused(self):
        mission_id = self.prepared_only()
        path = self.path(mission_id)
        binding = self.run_block(mission_id)["workspace"]
        other = self.other_worktree("same-baseline-elsewhere")
        pointer = os.path.join(path, ".git")
        redirected = "gitdir: %s\n" % self.admin_dir(other)
        self.write(pointer, redirected)
        # Everything the round-1 predicate checked still holds: the
        # registration and its marker, the common directory, HEAD, a clean
        # status. Only the checkout's own metadata is another worktree's.
        listed = worktrees(self.repository)[path]
        self.assertEqual(listed["locked"], workspace_module.lock_marker(
            mission_id, binding["binding_digest_sha256"]))
        self.assertEqual(self.admin_dir(path), self.admin_dir(other))
        self.assertEqual(head(path), binding["baseline_commit_sha"])
        self.assertEqual(run_git("-C", path, "status", "--porcelain"), "")
        self.assert_foreign_both_ways(mission_id, "does not own its registration")
        self.assertEqual(self.read(pointer), redirected)
        self.assertIsNone(self.run_block(mission_id)["intent"])
        self.assertEqual(self.spawn.calls, [])

    def test_W2_the_check_runs_before_trust_on_a_lost_acknowledgement(self):
        mission_id = self.approved()
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                after=self.crash)).dispatch(mission_id)
        path = self.path(mission_id)
        other = self.other_worktree("lost-ack-partner")
        self.write(os.path.join(path, ".git"),
                   "gitdir: %s\n" % self.admin_dir(other))
        self.assert_foreign_both_ways(mission_id, "does not own its registration")
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARING)
        self.assertFalse(self.trusted(path))
        self.assertEqual(self.spawn.calls, [])

    def test_W3_metadata_that_does_not_point_back_is_refused(self):
        mission_id = self.prepared_only()
        path = self.path(mission_id)
        marker = worktrees(self.repository)[path]["locked"]
        other = self.other_worktree("swap-partner")
        own_admin, other_admin = self.admin_dir(path), self.admin_dir(other)
        # The checkout still points at its own administrative directory,
        # but that directory now points at the other checkout, and the
        # registration naming this path (carrying this binding's marker)
        # lives in the OTHER administrative directory.
        self.write(os.path.join(own_admin, "gitdir"),
                   os.path.join(other, ".git") + "\n")
        self.write(os.path.join(other_admin, "gitdir"),
                   os.path.join(path, ".git") + "\n")
        self.write(os.path.join(other_admin, "locked"), marker)
        self.assertEqual(worktrees(self.repository)[path]["locked"], marker)
        self.assertEqual(self.admin_dir(path), own_admin)
        self.assertEqual(run_git("-C", path, "status", "--porcelain"), "")
        self.assert_foreign_both_ways(mission_id, "does not own its registration")
        self.assertEqual(self.spawn.calls, [])

    def test_W4_a_symlinked_git_pointer_is_refused(self):
        mission_id = self.prepared_only()
        path = self.path(mission_id)
        pointer = os.path.join(path, ".git")
        copy = os.path.join(self.base, "pointer-copy")
        self.write(copy, self.read(pointer))
        os.rename(pointer, os.path.join(self.base, "pointer-original"))
        os.symlink(copy, pointer)
        self.assertEqual(run_git("-C", path, "status", "--porcelain"), "")
        self.assert_foreign_both_ways(mission_id, "does not own its registration")
        self.assertTrue(os.path.islink(pointer))

    def test_W5_a_foreign_common_directory_is_refused_on_its_own(self):
        # Isolates the common-directory guard: the pointers stay reciprocal
        # and the marker stays DI's; only the administrative directory's
        # ``commondir`` names another repository. Without that guard the
        # refusal would come later and name another problem.
        mission_id = self.prepared_only()
        path = self.path(mission_id)
        elsewhere = make_repository(self.base, "elsewhere-repository")
        commondir = os.path.join(self.admin_dir(path), "commondir")
        self.write(commondir, os.path.join(elsewhere, ".git") + "\n")
        self.assertEqual(os.path.realpath(run_git(
            "-C", path, "rev-parse", "--path-format=absolute",
            "--git-common-dir")), os.path.realpath(os.path.join(elsewhere, ".git")))
        self.assertEqual(worktrees(self.repository)[path]["locked"],
                         workspace_module.lock_marker(
                             mission_id, self.run_block(mission_id)[
                                 "workspace"]["binding_digest_sha256"]))
        self.assert_foreign_both_ways(mission_id, "belongs to the repository at")
        self.assertEqual(self.spawn.calls, [])

    def test_W6_an_exact_worktree_still_passes_every_identity_check(self):
        mission_id = self.prepared_only()
        self.make_bridge().dispatch(mission_id, self.path(mission_id))
        self.assertEqual(len(self.spawn.calls), 1)


class PointerFailureTests(Fixture):
    """Round 3, required fix: a malformed worktree pointer, or one that
    cannot be read or resolved after it was opened, is a TRUTHFUL DURABLE
    refusal (``foreign``), never an escaping exception and never a repair.
    Both pointer directions (the checkout's ``.git``; an administrative
    directory's ``gitdir``), both entry points (automatic dispatch; the
    recovery path), each on a worktree whose preparation acknowledgement
    was lost (PREPARING, never trusted). Read and resolution failures are
    injected through the module's own indirections (``_fstat``, ``_read``,
    ``_realpath``), matched by inode or by path so nothing else is affected.
    Each guard has its own wording, so removing any one fails a test on both
    supported interpreters.

    Round 4: every injected fault lives only inside one ``with`` block in
    its own test, and each case asserts that exactly ITS fault fired on each
    entry point, for the pointer and role it names. A fault left over from
    another case can no longer satisfy a generic assertion."""

    def crash(self):
        raise Crash()

    def lost_acknowledgement(self, objective):
        mission_id = self.approved(objective)
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                after=self.crash)).dispatch(mission_id)
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARING)
        return mission_id

    def admin_dir(self, path):
        return os.path.realpath(run_git(
            "-C", path, "rev-parse", "--path-format=absolute", "--git-dir"))

    def other_worktree(self, name):
        other = os.path.join(self.base, name)
        run_git("-C", self.repository, "worktree", "add", "-q", "--detach",
                other, head(self.repository))
        return other

    def write_bytes(self, path, data):
        with open(path, "wb") as handle:
            handle.write(data)

    def read_bytes(self, name):
        with open(name, "rb") as handle:
            return handle.read()

    def evidence(self, mission_id, files):
        return (json.dumps(self.run_block(mission_id)["workspace"], sort_keys=True),
                sorted(worktrees(self.repository)),
                [self.read_bytes(name) for name in files])

    @contextlib.contextmanager
    def injected_io_fault(self, seam, inode):
        """``seam`` (``_read`` or ``_fstat``) fails for the one file with
        ``inode``, ONLY inside this block; every firing is recorded, and the
        module's seam is restored when the block exits."""
        real = getattr(workspace_module, seam)
        fired = []

        def failing(descriptor, *args):
            if os.fstat(descriptor).st_ino == inode:
                fired.append(seam)
                raise OSError(errno.EIO, "synthetic %s failure" % seam)
            return real(descriptor, *args)

        setattr(workspace_module, seam, failing)
        try:
            yield fired
        finally:
            setattr(workspace_module, seam, real)
        self.assertIs(getattr(workspace_module, seam), getattr(os, seam[1:]))

    @contextlib.contextmanager
    def injected_resolution_fault(self, target):
        """Resolving exactly ``target`` fails, ONLY inside this block."""
        real = workspace_module._realpath
        fired = []

        def failing(value):
            if value == target:
                fired.append(value)
                raise OSError(errno.EIO, "synthetic resolution failure")
            return real(value)

        workspace_module._realpath = failing
        try:
            yield fired
        finally:
            workspace_module._realpath = real
        self.assertIs(workspace_module._realpath, os.path.realpath)

    def assert_pointer_refusal(self, mission_id, words, files, absent=(),
                               fired=None):
        path = self.path(mission_id)
        for recovery in (None, path):
            with self.subTest(recovery_path=recovery):
                before = self.evidence(mission_id, files)
                firings = None if fired is None else len(fired)
                error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                                     self.make_bridge().dispatch, mission_id,
                                     recovery)
                if fired is not None:
                    self.assertGreater(len(fired), firings,
                                       "the intended fault did not fire on"
                                       " this entry point")
                for word in words:
                    self.assertIn(word, error.reason)
                for word in absent:
                    self.assertNotIn(word, error.reason)
                self.assert_refused_durably(
                    mission_id, bridge_module.PROBLEM_WORKSPACE_FOREIGN, error)
                self.assertEqual(self.evidence(mission_id, files), before)
                self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                                 mission_record.WORKSPACE_STATE_PREPARING)
                self.assertTrue(os.path.isdir(path))
                self.assertFalse(self.trusted(path))
                self.assertIsNone(self.run_block(mission_id)["intent"])
        self.assertEqual(self.spawn.calls, [])

    # -- the checkout's ``.git`` pointer --------------------------------

    def test_X1_a_checkout_pointer_with_an_embedded_nul_is_refused(self):
        mission_id = self.lost_acknowledgement("checkout pointer NUL")
        pointer = os.path.join(self.path(mission_id), ".git")
        self.write_bytes(pointer, b"gitdir: \x00bad\n")
        self.assert_pointer_refusal(mission_id, ["is malformed", "NUL"], [pointer])

    def test_X2_a_checkout_pointer_that_is_not_one_gitdir_line_is_refused(self):
        for label, content in (
            ("two lines", b"gitdir: %s\nsecond line\n"),
            ("no gitdir prefix", b"not a gitdir line\n"),
            ("empty target", b"gitdir: \n"),
            ("not UTF-8", b"gitdir: \xff\xfe\n"),
        ):
            with self.subTest(content=label):
                mission_id = self.lost_acknowledgement("checkout pointer " + label)
                pointer = os.path.join(self.path(mission_id), ".git")
                if b"%s" in content:
                    content = content % self.admin_dir(
                        self.path(mission_id)).encode("utf-8")
                self.write_bytes(pointer, content)
                self.assert_pointer_refusal(mission_id, ["is malformed"], [pointer])

    def assert_io_fault_refused(self, mission_id, role, pointer, seam):
        """Exactly ``seam``'s fault, on exactly ``pointer``, through both
        entry points."""
        other = "_fstat" if seam == "_read" else "_read"
        with self.injected_io_fault(seam, os.stat(pointer).st_ino) as fired:
            self.assert_pointer_refusal(
                mission_id, [role + " " + pointer + " could not be read",
                             "synthetic %s failure" % seam],
                [pointer], absent=["synthetic %s failure" % other], fired=fired)
        self.assertEqual(set(fired), {seam})

    def test_X3a_a_checkout_pointer_read_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("checkout pointer _read")
        self.assert_io_fault_refused(
            mission_id, workspace_module.CHECKOUT_POINTER,
            os.path.join(self.path(mission_id), ".git"), "_read")

    def test_X3b_a_checkout_pointer_fstat_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("checkout pointer _fstat")
        self.assert_io_fault_refused(
            mission_id, workspace_module.CHECKOUT_POINTER,
            os.path.join(self.path(mission_id), ".git"), "_fstat")

    def test_X4_a_checkout_pointer_resolution_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("checkout pointer resolution")
        path = self.path(mission_id)
        pointer = os.path.join(path, ".git")
        target = self.read_bytes(pointer).decode("utf-8").strip()[
            len("gitdir: "):]
        with self.injected_resolution_fault(os.path.join(path, target)) as fired:
            self.assert_pointer_refusal(
                mission_id,
                [workspace_module.CHECKOUT_POINTER + " " + pointer +
                 " could not be resolved", "synthetic resolution failure"],
                [pointer], fired=fired)

    # -- an administrative directory's ``gitdir`` pointer ------------------

    def test_Y1_an_administrative_pointer_with_an_embedded_nul_is_refused(self):
        mission_id = self.lost_acknowledgement("administrative pointer NUL")
        other_admin = self.admin_dir(self.other_worktree("nul-pointer-partner"))
        pointer = os.path.join(other_admin, "gitdir")
        self.write_bytes(pointer, b"\x00bad\n")
        # The configured repository still records this checkout.
        self.assertIn(self.path(mission_id), worktrees(self.repository))
        self.assert_pointer_refusal(
            mission_id, ["is malformed", "NUL"],
            [pointer, os.path.join(self.path(mission_id), ".git")])

    def test_Y2a_an_administrative_pointer_read_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("administrative _read")
        self.assert_io_fault_refused(
            mission_id, workspace_module.ADMINISTRATIVE_POINTER,
            os.path.join(self.admin_dir(self.path(mission_id)), "gitdir"),
            "_read")

    def test_Y2b_an_administrative_pointer_fstat_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("administrative _fstat")
        self.assert_io_fault_refused(
            mission_id, workspace_module.ADMINISTRATIVE_POINTER,
            os.path.join(self.admin_dir(self.path(mission_id)), "gitdir"),
            "_fstat")

    def test_Y3_an_administrative_pointer_resolution_failure_is_refused(self):
        mission_id = self.lost_acknowledgement("administrative resolution")
        admin = self.admin_dir(self.path(mission_id))
        pointer = os.path.join(admin, "gitdir")
        target = self.read_bytes(pointer).decode("utf-8").strip()
        with self.injected_resolution_fault(os.path.join(admin, target)) as fired:
            self.assert_pointer_refusal(
                mission_id,
                [workspace_module.ADMINISTRATIVE_POINTER + " " + pointer +
                 " could not be resolved", "synthetic resolution failure"],
                [pointer], fired=fired)


class RootContainmentTests(Fixture):
    """Round 2, required fix 2: the workspaces root lies outside EVERY
    repository. A root inside a third repository, inside another worktree
    of the configured repository, inside a bare repository, or under a
    broken ``.git`` pointer is refused before any binding or creation,
    durably, with no worktree and no spawn."""

    def assert_root_refused(self, root, words):
        mission_id = self.approved("root " + words)
        before = sorted(worktrees(self.repository))
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE,
                             self.make_bridge(workspaces_root=root).dispatch,
                             mission_id)
        self.assertIn("inside the repository at", error.reason)
        self.assertIn(words, error.reason)
        self.assert_refused_durably(
            mission_id, bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE, error)
        self.assertNotIn("workspace", self.run_block(mission_id))
        self.assertEqual(os.listdir(root), [])
        self.assertEqual(sorted(worktrees(self.repository)), before)
        self.assertEqual(self.spawn.calls, [])
        # The recovery path takes the same refusal.
        self.refused(bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE,
                     self.make_bridge(workspaces_root=root).dispatch, mission_id,
                     os.path.join(root, mission_id))

    def test_N1_a_root_inside_a_third_repository(self):
        third = make_repository(self.base, "third-repository", url=OTHER_URL)
        root = os.path.join(third, "nested", "mission-workspaces")
        os.makedirs(root)
        self.assert_root_refused(root, third)

    def test_N2_a_root_inside_another_worktree_of_the_configured_repository(self):
        linked = os.path.join(self.base, "linked-worktree")
        run_git("-C", self.repository, "worktree", "add", "-q", "--detach",
                linked, "HEAD")
        root = os.path.join(linked, "mission-workspaces")
        os.mkdir(root)
        self.assert_root_refused(root, linked)

    def test_N3_a_root_inside_a_bare_repository(self):
        bare = os.path.join(self.base, "bare-repository.git")
        run_git("init", "-q", "--bare", bare)
        root = os.path.join(bare, "mission-workspaces")
        os.mkdir(root)
        self.assert_root_refused(root, bare)

    def test_N4_a_root_under_a_broken_git_pointer_fails_closed(self):
        holder = os.path.join(self.base, "broken-pointer")
        os.mkdir(holder)
        with open(os.path.join(holder, ".git"), "w") as handle:
            handle.write("gitdir: /nonexistent/metadata\n")
        root = os.path.join(holder, "mission-workspaces")
        os.mkdir(root)
        self.assert_root_refused(root, holder)

    def test_N5_a_root_outside_every_repository_is_accepted(self):
        mission_id = self.approved("root outside every repository")
        self.assertIsNone(workspace_module.enclosing_repository(self.root))
        self.bridge.dispatch(mission_id)
        self.assertEqual(len(self.spawn.calls), 1)


class UnavailableRepositoryTests(Fixture):
    """Criterion 9: an unavailable repository or root fails closed with a
    durable, truthful result, no worker start and no bound worktree."""

    def assert_unavailable(self, bridge, problem, words):
        mission_id = self.approved("unavailable %s" % words)
        before = sorted(os.listdir(self.root)) if os.path.isdir(self.root) else None
        error = self.refused(problem, bridge.dispatch, mission_id)
        self.assertIn(words, error.reason)
        self.assert_refused_durably(mission_id, problem, error)
        self.assertNotIn("workspace", self.run_block(mission_id))
        after = sorted(os.listdir(self.root)) if os.path.isdir(self.root) else None
        self.assertEqual(after, before)
        return mission_id

    def test_U1_nothing_configured(self):
        self.assert_unavailable(
            self.make_bridge(workspace_repository=None, workspaces_root=None),
            bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, "not configured")
        self.assert_unavailable(
            self.make_bridge(workspaces_root=None),
            bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE, "not configured")

    def test_U2_missing_or_not_a_repository(self):
        self.assert_unavailable(
            self.make_bridge(workspace_repository=os.path.join(self.base, "gone")),
            bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, "not a readable")
        plain = os.path.join(self.base, "plain")
        os.mkdir(plain)
        self.assert_unavailable(
            self.make_bridge(workspace_repository=plain),
            bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, "not a readable")
        self.assert_unavailable(
            self.make_bridge(workspace_repository="relative/repository"),
            bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, "absolute")

    def test_U3_a_repository_of_another_project(self):
        other = make_repository(self.base, "other-project", url=OTHER_URL)
        self.assert_unavailable(
            self.make_bridge(workspace_repository=other),
            bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, "not the approved")
        self.assertEqual(worktrees(other), {})

    def test_U4_missing_unusable_or_misplaced_workspaces_root(self):
        missing = os.path.join(self.base, "no-root")
        self.assert_unavailable(
            self.make_bridge(workspaces_root=missing),
            bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE, "not an existing")
        self.assertFalse(os.path.exists(missing))
        inside = os.path.join(self.repository, "workspaces")
        os.mkdir(inside)
        self.assert_unavailable(
            self.make_bridge(workspaces_root=inside),
            bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE, "inside")
        self.assert_unavailable(
            self.make_bridge(workspaces_root=self.control),
            bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE, "inside")
        if os.geteuid() != 0:
            os.chmod(self.root, 0o500)
            self.addCleanup(os.chmod, self.root, 0o700)
            self.assert_unavailable(
                self.make_bridge(), bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE,
                "not writable")

    def test_U5_a_repository_lost_after_preparation_fails_closed(self):
        mission_id = self.prepared_only()
        moved = self.repository + "-moved"
        os.rename(self.repository, moved)
        self.addCleanup(os.rename, moved, self.repository)
        error = self.refused(bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE,
                             self.make_bridge().dispatch, mission_id)
        self.assert_refused_durably(mission_id,
                                    bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE, error)
        self.assertTrue(os.path.isdir(self.path(mission_id)))


class RestartRecoveryTests(Fixture):
    """Criteria 5 and 6 around preparation and dispatch: an uncertain
    preparation reconciles to ONE worktree; an uncertain dispatch is never
    redispatched."""

    def crash(self):
        raise Crash()

    def test_P1_death_after_the_binding_before_the_worktree(self):
        mission_id = self.approved()
        transport = RecordingTransport(before=self.crash)
        with self.assertRaises(Crash):
            self.make_bridge(transport=transport).dispatch(mission_id)
        binding = self.run_block(mission_id)["workspace"]
        self.assertEqual(binding["state"], mission_record.WORKSPACE_STATE_PREPARING)
        self.assertIsNone(self.run_block(mission_id)["intent"])
        self.assertFalse(os.path.lexists(self.path(mission_id)))
        self.make_bridge(missions=self.service()).dispatch(mission_id)
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.run_block(mission_id)["workspace"]["binding_digest_sha256"],
                         binding["binding_digest_sha256"])

    def test_P2_death_after_the_worktree_before_it_is_recorded_prepared(self):
        mission_id = self.approved()
        transport = RecordingTransport(after=self.crash)
        with self.assertRaises(Crash):
            self.make_bridge(transport=transport).dispatch(mission_id)
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARING)
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        retry = RecordingTransport()
        self.make_bridge(missions=self.service(), transport=retry).dispatch(mission_id)
        self.assertEqual(retry.adds, [])
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARED)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_P3_an_uncertain_git_outcome_reconciles_without_a_duplicate(self):
        mission_id = self.approved()

        def failed_after_effect():
            raise GitTransportError("synthetic: the add's outcome was lost")

        transport = RecordingTransport(after=failed_after_effect)
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_PREPARATION_FAILED,
                             self.make_bridge(transport=transport).dispatch,
                             mission_id)
        self.assert_refused_durably(
            mission_id, bridge_module.PROBLEM_WORKSPACE_PREPARATION_FAILED, error)
        self.make_bridge().dispatch(mission_id)
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_P4_a_failed_add_leaves_no_partial_worktree(self):
        mission_id = self.approved()

        def refused_before_effect():
            raise GitTransportError("synthetic: git refused the add")

        transport = RecordingTransport(before=refused_before_effect)
        self.refused(bridge_module.PROBLEM_WORKSPACE_PREPARATION_FAILED,
                     self.make_bridge(transport=transport).dispatch, mission_id)
        self.assertFalse(os.path.lexists(self.path(mission_id)))
        self.assertEqual(worktrees(self.repository), {})
        self.assertIsNone(self.run_block(mission_id)["intent"])
        self.make_bridge().dispatch(mission_id)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_P5_death_after_preparation_before_the_intent(self):
        mission_id = self.approved()
        real = self.missions.record_run_intent

        def die(*args):
            self.missions.record_run_intent = real
            raise Crash()

        self.missions.record_run_intent = die
        with self.assertRaises(Crash):
            self.bridge.dispatch(mission_id)
        self.assertIsNone(self.run_block(mission_id)["intent"])
        self.make_bridge(missions=self.service()).dispatch(mission_id)
        self.assertEqual(len(worktrees(self.repository)), 1)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_D1_an_uncertain_dispatch_is_never_redispatched(self):
        for mode in ("before_child", "after_child", "crash"):
            with self.subTest(mode=mode):
                mission_id = self.approved("uncertain " + mode)
                calls = len(self.spawn.calls)
                if mode == "crash":
                    real = self.spawn

                    def die(parent, request):
                        real(parent, request)
                        raise Crash()

                    with self.assertRaises(Crash):
                        self.make_bridge(spawn_fn=die).dispatch(mission_id)
                else:
                    self.spawn.raises = mode
                    self.assertTrue(self.bridge.dispatch(mission_id)["hold"])
                    self.spawn.raises = None
                self.assertEqual(len(self.spawn.calls), calls + 1)
                # The worktree turning dirty after the intent changes nothing:
                # an existing intent is never dispatched again.
                with open(os.path.join(self.path(mission_id), "late.txt"), "w") as handle:
                    handle.write("written by the child\n")
                for _ in range(3):
                    again = self.make_bridge(missions=self.service()).dispatch(
                        mission_id)
                    self.assertTrue(again["duplicate"])
                    self.assertTrue(again["hold"])
                self.assertEqual(len(self.spawn.calls), calls + 1)
                self.assertTrue(os.path.exists(os.path.join(self.path(mission_id),
                                                            "late.txt")))

    def test_D2_reconcile_binds_the_identity_at_most_once(self):
        mission_id = self.approved()
        self.spawn.raises = "after_child"
        self.bridge.dispatch(mission_id)
        self.spawn.raises = None
        self.observer.raw = raw_observation()
        result = self.bridge.reconcile(mission_id)
        self.assertEqual(result["target_task_id"], TASK_ID)
        self.assertEqual(self.run_block(mission_id)["receipt"]["identity_source"],
                         "reconciliation")
        self.refused(bridge_module.PROBLEM_WRONG_STATE, self.bridge.reconcile,
                     mission_id)
        self.assertEqual(len(self.spawn.calls), 1)


class ConcurrencyTests(Fixture):
    """Criterion 5: overlapping preparation for one Mission never makes two
    worktrees, and the once-only intent guard admits one dispatch."""

    def test_C1_a_preparation_overlapping_one_in_progress_is_refused_busy(self):
        mission_id = self.approved()
        inner = {}

        def second_dispatch():
            try:
                self.make_bridge(missions=self.service()).dispatch(mission_id)
            except bridge_module.MissionBridgeRefusal as exc:
                inner["problem"] = exc.problem

        transport = RecordingTransport(before=second_dispatch)
        self.make_bridge(transport=transport).dispatch(mission_id)
        self.assertEqual(inner, {"problem": bridge_module.PROBLEM_WORKSPACE_BUSY})
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.run_block(mission_id)["workspace_refusal"]["problem"],
                         bridge_module.PROBLEM_WORKSPACE_BUSY)

    def test_C2_a_second_preparation_after_creation_adopts_and_one_dispatch_wins(self):
        mission_id = self.approved()
        real = self.missions.record_workspace_prepared
        inner = []

        def second_then_record(*args):
            self.missions.record_workspace_prepared = real
            inner.append(self.make_bridge(missions=self.service()).dispatch(
                mission_id))
            return real(*args)

        self.missions.record_workspace_prepared = second_then_record
        error = self.refused(bridge_module.PROBLEM_MISSION_CORE,
                             self.bridge.dispatch, mission_id)
        self.assertEqual(error.details["mission_problem"],
                         mission_record.PROBLEM_RUN_ALREADY_RECORDED)
        self.assertEqual(inner[0]["phase"], "dispatched_not_yet_observed")
        self.assertEqual(sorted(worktrees(self.repository)), [self.path(mission_id)])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_C3_repeated_dispatch_is_a_duplicate(self):
        mission_id = self.approved()
        self.bridge.dispatch(mission_id)
        for _ in range(3):
            self.assertTrue(self.make_bridge().dispatch(mission_id)["duplicate"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(len(worktrees(self.repository)), 1)


class OperatorOverrideTests(Fixture):
    """Criterion 8 (and the Lead's acceptance clarification): the explicit
    path is the operator RECOVERY entry point for the Mission's OWN durably
    bound, DI-prepared worktree. Configuration, isolation, binding and
    ownership checks all still apply; it never creates a workspace and
    never binds another path."""

    def crash(self):
        raise Crash()

    def test_O1_an_unrelated_clean_checkout_is_refused_durably(self):
        checkout = make_repository(self.base, "unrelated-checkout")
        for bridge, problem in (
            (self.make_bridge(), bridge_module.PROBLEM_WORKSPACE_FOREIGN),
            (self.make_bridge(workspace_repository=None, workspaces_root=None),
             bridge_module.PROBLEM_REPOSITORY_UNAVAILABLE),
            (self.make_bridge(workspaces_root=None),
             bridge_module.PROBLEM_WORKSPACE_ROOT_UNAVAILABLE),
        ):
            with self.subTest(problem=problem):
                mission_id = self.approved("unrelated " + problem)
                error = self.refused(problem, bridge.dispatch, mission_id, checkout)
                self.assert_refused_durably(mission_id, problem, error)
                self.assertNotIn("workspace", self.run_block(mission_id))
        self.assertEqual(self.spawn.calls, [])
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(worktrees(self.repository), {})
        self.assertEqual(worktrees(checkout), {})
        # A Mission that DOES hold a binding gains nothing from it either: the
        # unrelated path is refused, never swapped for the bound one.
        bound = self.prepared_only("bound, then an unrelated path")
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN,
                             self.bridge.dispatch, bound, checkout)
        self.assertIn("not mission %s's own workspace" % bound, error.reason)
        self.assertIsNone(self.run_block(bound)["intent"])
        self.assertEqual(self.spawn.calls, [])

    def test_O2_the_recovery_path_takes_every_same_refusal(self):
        a = self.approved("mission A")
        self.bridge.dispatch(a)
        inside = os.path.join(self.root, "not-mine")
        os.mkdir(inside)
        for objective, path_of, problem, words in (
            ("another Mission's worktree", lambda m: self.path(a),
             bridge_module.PROBLEM_WORKSPACE_FOREIGN, "not mission"),
            ("inside the root, not its own", lambda m: inside,
             bridge_module.PROBLEM_WORKSPACE_FOREIGN, "not mission"),
            ("its own path, never bound", self.path,
             bridge_module.PROBLEM_WORKSPACE_FOREIGN, "no durable workspace binding"),
        ):
            with self.subTest(objective=objective):
                mission_id = self.approved(objective)
                error = self.refused(problem, self.bridge.dispatch, mission_id,
                                     path_of(mission_id))
                self.assertIn(words, error.reason)
                self.assert_refused_durably(mission_id, problem, error)
                self.assertNotIn("workspace", self.run_block(mission_id))
        dirty = self.prepared_only("its own worktree, dirty")
        with open(os.path.join(self.path(dirty), "wip.txt"), "w") as handle:
            handle.write("wip\n")
        self.refused(bridge_module.PROBLEM_WORKSPACE_NOT_CLEAN,
                     self.bridge.dispatch, dirty, self.path(dirty))
        self.assert_refused_durably(dirty, bridge_module.PROBLEM_WORKSPACE_NOT_CLEAN)
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(sorted(worktrees(self.repository)),
                         sorted([self.path(a), self.path(dirty)]))

    def test_O3_recovering_the_missions_own_prepared_worktree(self):
        # A lost preparation acknowledgement: the binding was durably
        # recorded and the worktree created, but not recorded PREPARED.
        lost = self.approved("lost acknowledgement")
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                after=self.crash)).dispatch(lost)
        self.assertEqual(self.run_block(lost)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARING)
        retry = RecordingTransport()
        result = self.make_bridge(missions=self.service(), transport=retry).dispatch(
            lost, self.path(lost))
        self.assertEqual(retry.adds, [])
        self.assertEqual(result["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARED)
        self.assertEqual(self.run_block(lost)["intent"]["workspace_realpath"],
                         self.path(lost))
        # A prepared worktree whose dispatch stopped before the intent.
        stopped = self.prepared_only("stopped before the intent")
        self.make_bridge().dispatch(stopped, self.path(stopped))
        self.assertEqual(self.run_block(stopped)["intent"]["workspace_realpath"],
                         self.path(stopped))
        self.assertEqual(len(self.spawn.calls), 2)
        self.assertEqual(sorted(worktrees(self.repository)),
                         sorted([self.path(lost), self.path(stopped)]))

    def test_O4_the_recovery_path_never_creates_a_workspace(self):
        mission_id = self.approved()
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                before=self.crash)).dispatch(mission_id)
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARING)
        retry = RecordingTransport()
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_CONFLICT,
                             self.make_bridge(transport=retry).dispatch,
                             mission_id, self.path(mission_id))
        self.assertIn("never creates", error.reason)
        self.assertEqual(retry.adds, [])
        self.assertFalse(os.path.lexists(self.path(mission_id)))
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_CONFLICT)
        # An ordinary dispatch prepares it.
        self.make_bridge().dispatch(mission_id)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_O5_a_relative_override_is_refused_before_anything_is_recorded(self):
        mission_id = self.approved()
        self.refused(bridge_module.PROBLEM_WORKSPACE_IDENTITY,
                     self.bridge.dispatch, mission_id, "relative/checkout")
        self.assertNotIn("run", self.mission(mission_id))


class HerdrActivityTests(Fixture):
    """The Lead's acceptance clarification 2: a prepared, otherwise exact,
    Git-clean worktree holding Herdr state this store's run intent does not
    represent is refused deterministically BEFORE the spawn seam."""

    def herd_state(self, path, name, content):
        state = os.path.join(path, ".herd", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, name), "w") as handle:
            handle.write(content if isinstance(content, str)
                         else json.dumps(content))
        return os.path.join(state, name)

    def test_H1_an_active_task_in_a_clean_worktree_is_refused_before_the_spawn(self):
        mission_id = self.prepared_only()
        path = self.path(mission_id)
        record = self.herd_state(path, "task.json", {
            "id": "20261008-000000-aaaaaa", "status": "ACTIVE",
            "description": "someone else's task", "started_at": NOW})
        # .herd is ignored: Git calls the worktree clean.
        self.assertEqual(run_git("-C", path, "status", "--porcelain"), "")
        error = self.refused(ACTIVE, self.make_bridge().dispatch, mission_id)
        self.assertIn("20261008-000000-aaaaaa", error.reason)
        self.assertIn("ignored by Git", error.reason)
        self.assert_refused_durably(mission_id, ACTIVE, error)
        self.refused(ACTIVE, self.make_bridge().dispatch, mission_id, path)
        self.assertEqual(self.spawn.calls, [])
        self.assertTrue(os.path.isfile(record))
        self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                         mission_record.WORKSPACE_STATE_PREPARED)

    def test_H2_runtime_unreadable_and_stopped_records(self):
        stopped = {"id": "t-old", "status": "COMPLETE"}
        for objective, files, problem in (
            ("runtime only", {"runtime.json": {"version": 1}}, ACTIVE),
            ("malformed task", {"task.json": "{not json"}, ACTIVE),
            ("a task with no status", {"task.json": {"id": "t-x"}}, ACTIVE),
            ("a stopped task and a runtime",
             {"task.json": stopped, "runtime.json": {"version": 1}}, ACTIVE),
            ("a stopped task alone", {"task.json": stopped},
             bridge_module.PROBLEM_WORKSPACE_CONFLICT),
        ):
            with self.subTest(objective=objective):
                mission_id = self.prepared_only(objective)
                for name, content in files.items():
                    self.herd_state(self.path(mission_id), name, content)
                self.refused(problem, self.make_bridge().dispatch, mission_id)
                self.assert_refused_durably(mission_id, problem)
        linked = self.prepared_only("a linked task record")
        target = os.path.join(self.base, "elsewhere-task.json")
        with open(target, "w") as handle:
            handle.write(json.dumps({"id": "t", "status": "COMPLETE"}))
        os.makedirs(os.path.join(self.path(linked), ".herd", "state"))
        os.symlink(target, os.path.join(self.path(linked), ".herd", "state",
                                        "task.json"))
        self.refused(ACTIVE, self.make_bridge().dispatch, linked)
        self.assertEqual(self.spawn.calls, [])

    def test_H3_state_arriving_after_preparation_is_caught_before_the_intent(self):
        mission_id = self.approved()
        real_prepared = self.missions.record_workspace_prepared
        real_intent = self.missions.record_run_intent
        intents = []

        def herd_starts_after_preparation(*args):
            self.missions.record_workspace_prepared = real_prepared
            outcome = real_prepared(*args)
            self.herd_state(self.path(mission_id), "task.json",
                            {"id": "late-task", "status": "ACTIVE"})
            return outcome

        def intent(*args):
            intents.append(args)
            return real_intent(*args)

        self.missions.record_workspace_prepared = herd_starts_after_preparation
        self.missions.record_run_intent = intent
        error = self.refused(ACTIVE, self.bridge.dispatch, mission_id)
        self.assertIn("late-task", error.reason)
        self.assertEqual(intents, [])
        self.assertEqual(self.spawn.calls, [])
        self.assert_refused_durably(mission_id, ACTIVE, error)

    def test_H4_a_worktree_holding_herdr_state_is_never_acknowledged_prepared(self):
        mission_id = self.approved()
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                after=self.crash)).dispatch(mission_id)
        self.herd_state(self.path(mission_id), "task.json",
                        {"id": "between-crash-and-retry", "status": "ACTIVE"})
        for path in (None, self.path(mission_id)):
            with self.subTest(recovery_path=path):
                self.refused(ACTIVE, self.make_bridge().dispatch, mission_id, path)
                self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                                 mission_record.WORKSPACE_STATE_PREPARING)
        self.assertEqual(self.spawn.calls, [])

    def crash(self):
        raise Crash()

    def test_H5_a_fresh_worktree_has_no_herdr_state(self):
        mission_id = self.approved()
        self.bridge.dispatch(mission_id)
        self.assertFalse(os.path.lexists(os.path.join(self.path(mission_id), ".herd")))
        self.assertEqual(len(self.spawn.calls), 1)


class EvidencePreservationTests(Fixture):
    """Criterion 7: no implicit cleanup anywhere in this change."""

    def write_artifacts(self, path):
        state_dir = os.path.join(path, ".herd", "state")
        os.makedirs(os.path.join(state_dir, "reviews"))
        with open(os.path.join(state_dir, "task-checkpoint.md"), "w") as handle:
            handle.write(RESULT_TEXT)
        name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (TASK_ID, 1)
        with open(os.path.join(state_dir, "reviews", name), "w") as handle:
            handle.write(REVIEW_TEXT)

    def assert_preserved(self, mission_id, binding):
        path = self.path(mission_id)
        listed = worktrees(self.repository)
        self.assertIn(path, listed)
        self.assertEqual(listed[path]["locked"], workspace_module.lock_marker(
            mission_id, binding["binding_digest_sha256"]))
        self.assertTrue(os.path.isfile(os.path.join(path, "README.md")))

    def test_E1_completed_paused_cancelled_and_blocked_worktrees_stay(self):
        outcomes = {}
        completed = self.approved("completed")
        self.bridge.dispatch(completed)
        self.observer.raw = raw_observation()
        self.bridge.observe(completed)
        self.write_artifacts(self.path(completed))
        submitted = self.state_op(
            completed, self.missions.submit_evidence, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, TESTS_PASS_DIGEST, [])
        self.state_op(completed, self.missions.accept_evidence,
                      submitted["evidence_id"], TESTS_PASS_DIGEST)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.bridge.verify(completed, {
            "task_id": TASK_ID, "result_digest_sha256": sha256(RESULT_TEXT),
            "review_digest_sha256": sha256(REVIEW_TEXT)})
        outcomes[completed] = "COMPLETED"
        paused = self.approved("paused")
        self.bridge.dispatch(paused)
        self.bridge.pause(paused)
        outcomes[paused] = "AUTHORIZED"
        cancelled = self.approved("cancelled")
        self.bridge.dispatch(cancelled)
        self.bridge.cancel(cancelled)
        outcomes[cancelled] = "CANCELLED"
        blocked = self.approved("blocked")
        self.spawn.raises = "before_child"
        self.bridge.dispatch(blocked)
        self.spawn.raises = None
        self.observer.raw = {"completeness": "PARTIAL", "diagnostics": [],
                             "task": {"state": "unreadable"}}
        self.bridge.reconcile(blocked)
        outcomes[blocked] = "BLOCKED"
        for mission_id, state in outcomes.items():
            with self.subTest(state=state):
                self.assertEqual(self.mission(mission_id)["state"], state)
                if state == "AUTHORIZED":
                    self.assertTrue(self.bridge.status(mission_id)["paused"])
                self.assert_preserved(mission_id, self.run_block(mission_id)["workspace"])
        self.assertEqual(len(worktrees(self.repository)), 4)

    def test_E2_nothing_in_this_change_can_remove_a_workspace(self):
        removal = {"rmtree", "remove", "removedirs", "rmdir", "unlink", "rename",
                   "replace", "release", "release_workspace", "relinquish_workspace"}
        for relpath in ("target_runtime/mission_workspace.py",
                        "target_runtime/mission_bridge.py"):
            tree = ast.parse((REPO_ROOT / relpath).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", getattr(node.func, "attr", None))
                    self.assertNotIn(name, removal, (relpath, node.lineno))
        source = (REPO_ROOT / "target_runtime" / "git_transport.py").read_text()
        for verb in ('"prune"', '"remove"', '"move"', '"--force"', '"-f"'):
            self.assertNotIn(verb, source)


class MissionCoreBindingTests(Fixture):
    """Mission Core enforces the order and the binding itself."""

    def bind(self, mission_id, path=None, repository=None, baseline=None):
        return self.missions.record_workspace_binding(
            mission_id, path or os.path.join(self.base, "checkout"),
            repository or self.repository, REPO_URL,
            baseline or head(self.repository), self.context)

    def intent(self, mission_id, path, baseline):
        mission = self.mission(mission_id)
        self.bridge._activate_contract(mission_id, 1)
        return self.missions.record_run_intent(
            mission_id, mission["authorization_ids"][-1], 1,
            mission["revisions"][0]["proposal_digest_sha256"], REPO_URL, path,
            baseline, "d" * 64, "e" * 64, SURFACE,
            ["engineering_change", "repository_read"], [], self.context)

    def core_refused(self, problem, callable_, *args):
        with self.assertRaises(mission_record.MissionError) as caught:
            callable_(*args)
        self.assertEqual(caught.exception.problem, problem, str(caught.exception))

    def test_K1_an_intent_needs_a_prepared_binding_at_exactly_its_path(self):
        mission_id = self.approved()
        baseline = head(self.repository)
        path = self.path(mission_id)
        unprepared = mission_record.PROBLEM_RUN_WORKSPACE_UNPREPARED
        self.core_refused(unprepared, self.intent, mission_id, path, baseline)
        binding = self.bind(mission_id, path)
        self.assertEqual(binding["state"], mission_record.WORKSPACE_STATE_PREPARING)
        self.core_refused(unprepared, self.intent, mission_id, path, baseline)
        self.missions.record_workspace_prepared(
            mission_id, binding["binding_digest_sha256"], self.context)
        self.core_refused(unprepared, self.intent, mission_id, path + "-other",
                          baseline)
        self.core_refused(unprepared, self.intent, mission_id, path, "c" * 40)
        self.intent(mission_id, path, baseline)
        self.core_refused(mission_record.PROBLEM_RUN_ALREADY_RECORDED,
                          self.bind, mission_id, path)
        self.core_refused(mission_record.PROBLEM_RUN_ALREADY_RECORDED,
                          self.missions.record_workspace_refusal, mission_id,
                          "late", "after the intent", self.context)

    def test_K2_a_binding_is_exact_and_idempotent(self):
        mission_id = self.approved()
        first = self.bind(mission_id)
        self.assertEqual(first["state"], mission_record.WORKSPACE_STATE_PREPARING)
        self.assertEqual(self.bind(mission_id), first)
        conflict = mission_record.PROBLEM_RUN_WORKSPACE_CONFLICT
        self.core_refused(conflict, self.bind, mission_id,
                          os.path.join(self.base, "elsewhere"))
        self.core_refused(conflict, self.bind, mission_id, None,
                          os.path.join(self.base, "another-repository"))
        self.core_refused(conflict, self.bind, mission_id, None, None, "c" * 40)
        self.core_refused(conflict, self.missions.record_workspace_prepared,
                          mission_id, "f" * 64, self.context)
        self.assertEqual(self.run_block(mission_id)["workspace"], first)
        bad = self.approved("bad binding")
        self.core_refused(mission_record.PROBLEM_BAD_VALUE, self.bind, bad,
                          "relative/path")
        self.core_refused(mission_record.PROBLEM_BAD_VALUE, self.bind, bad, None,
                          "relative/repository")
        self.core_refused(mission_record.PROBLEM_BAD_VALUE, self.bind, bad, None,
                          None, "not-a-commit")
        self.assertNotIn("run", self.mission(bad))

    def test_K3_the_cross_mission_lease(self):
        a = self.approved("mission A")
        shared = os.path.join(self.base, "shared")
        self.bind(a, shared)
        b = self.approved("mission B")
        self.core_refused(mission_record.PROBLEM_RUN_WORKSPACE_BOUND,
                          self.bind, b, shared)
        self.bridge.cancel(a)
        # Once A is terminal its workspace is its evidence: never bound again.
        self.core_refused(mission_record.PROBLEM_RUN_WORKSPACE_PRESERVED,
                          self.bind, b, shared)
        self.assertNotIn("workspace", self.run_block(b))

    def test_K4_refusals_are_counted_and_bounded(self):
        mission_id = self.approved()
        first = self.missions.record_workspace_refusal(
            mission_id, "mission_bridge_workspace_collision", "a reason",
            self.context)
        second = self.missions.record_workspace_refusal(
            mission_id, "mission_bridge_workspace_dirty", "another", self.context)
        self.assertEqual((first["refusals"], second["refusals"]), (1, 2))
        self.assertEqual(self.run_block(mission_id)["workspace_refusal"], second)
        self.core_refused(
            mission_record.PROBLEM_TOO_LARGE, self.missions.record_workspace_refusal,
            mission_id, "p", "x" * (mission_record.MAX_WORKSPACE_DETAIL_CHARS + 1),
            self.context)
        self.assertEqual(mission_record.MAX_WORKSPACE_DETAIL_CHARS, 2000)
        self.assertEqual(mission_record.MAX_WORKSPACE_PROBLEM_CHARS, 128)

    def test_K5_a_tampered_stored_binding_is_refused_on_load(self):
        mission_id = self.approved()
        self.bridge.dispatch(mission_id)
        path = os.path.join(self.state, "missions.json")
        with open(path) as handle:
            pristine = json.load(handle)
        for field, value in (
            ("binding_digest_sha256", "f" * 64),
            ("path_realpath", "/elsewhere"),
            ("baseline_commit_sha", "c" * 40),
            ("state", mission_record.WORKSPACE_STATE_PREPARING),
            ("repository_realpath", "/elsewhere"),
            ("state", "invented"),
        ):
            with self.subTest(field=field):
                document = json.loads(json.dumps(pristine))
                document["missions"][mission_id]["run"]["workspace"][field] = value
                with self.assertRaises(mission_store.MissionStoreError):
                    mission_store.validate_document(document, "synthetic")
        mission_store.validate_document(pristine, "synthetic")

    def test_K6_a_paused_mission_takes_no_binding_and_no_preparation(self):
        mission_id = self.approved()
        self.bridge.pause(mission_id)
        self.core_refused(mission_record.PROBLEM_RUN_PAUSED, self.bind, mission_id)
        self.refused(bridge_module.PROBLEM_PAUSED, self.bridge.dispatch, mission_id)
        self.assertNotIn("workspace", self.run_block(mission_id))
        self.assertNotIn("workspace_refusal", self.run_block(mission_id))
        self.assertEqual(worktrees(self.repository), {})


class WorkspaceTrustTests(Fixture):
    """The Lead's acceptance item 3: a freshly prepared worktree is a new
    directory the Claude CLI has never trusted, and a Herdr started there
    would stop at the trust dialog. Preparation establishes trust through
    the EXISTING managed-workspace seam before the worktree is recorded
    PREPARED, and the point-of-use check runs before the intent; a failure
    is a durable refusal and nothing starts. Temporary configuration only."""

    def config(self):
        with open(self.trust_config) as handle:
            return json.load(handle)

    def write_config(self, document):
        with open(self.trust_config, "w") as handle:
            handle.write(document if isinstance(document, str)
                         else json.dumps(document))

    def test_T1_preparation_trusts_exactly_the_new_worktree_before_prepared(self):
        mission_id = self.approved()
        path = self.path(mission_id)
        seen = []
        real = self.missions.record_workspace_prepared

        def observe_then_record(*args):
            seen.append(self.trusted(path))
            return real(*args)

        self.missions.record_workspace_prepared = observe_then_record
        self.assertFalse(self.trusted(path))
        self.bridge.dispatch(mission_id)
        self.assertEqual(seen, [True])
        document = self.config()
        # One key in one entry; every other key untouched.
        self.assertEqual(document["numStartups"], 3)
        self.assertEqual(sorted(document["projects"]), [trust_module.trust_key(path)])
        self.assertEqual(document["projects"][trust_module.trust_key(path)],
                         {trust_module.TRUST_KEY: True})
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertIsInstance(self.bridge.worker, RuntimeWorker)

    def test_T2_no_trust_configuration_refuses_before_anything_is_prepared(self):
        mission_id = self.approved()
        bridge = self.make_bridge(trust_config_path=None)
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_TRUST,
                             bridge.dispatch, mission_id)
        self.assertIn("trust dialog", error.reason)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_TRUST,
                                    error)
        self.assertNotIn("workspace", self.run_block(mission_id))
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(self.config()["projects"], {})

    def test_T3_a_failed_establishment_is_durable_and_retried_on_the_same_worktree(self):
        for objective, broken in (
            ("projects missing", {"numStartups": 3}),
            ("config unparsable", "{not json"),
        ):
            with self.subTest(objective=objective):
                mission_id = self.approved(objective)
                self.write_config(broken)
                error = self.refused(bridge_module.PROBLEM_WORKSPACE_TRUST,
                                     self.make_bridge().dispatch, mission_id)
                self.assertIn("workspace trust not established", error.reason)
                self.assertTrue(error.details["trust_problem"].startswith(
                    "workspace_trust_"))
                self.assert_refused_durably(
                    mission_id, bridge_module.PROBLEM_WORKSPACE_TRUST, error)
                self.assertEqual(self.run_block(mission_id)["workspace"]["state"],
                                 mission_record.WORKSPACE_STATE_PREPARING)
                with open(self.trust_config) as handle:
                    self.assertEqual(handle.read(), broken if isinstance(
                        broken, str) else json.dumps(broken))
                # Fixed by a human: the retry adopts the same worktree.
                self.write_config({"projects": {}})
                retry = RecordingTransport()
                self.make_bridge(transport=retry).dispatch(mission_id)
                self.assertEqual(retry.adds, [])
                self.assertTrue(self.trusted(self.path(mission_id)))
        self.assertEqual(len(worktrees(self.repository)), 2)
        self.assertEqual(len(self.spawn.calls), 2)

    def test_T4_trust_must_be_consumable_at_the_point_of_use(self):
        mission_id = self.approved("trust removed after preparation")
        real = self.missions.record_workspace_prepared

        def prepared_then_trust_removed(*args):
            self.missions.record_workspace_prepared = real
            outcome = real(*args)
            self.write_config({"numStartups": 3, "projects": {}})
            return outcome

        self.missions.record_workspace_prepared = prepared_then_trust_removed
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_TRUST,
                             self.bridge.dispatch, mission_id)
        self.assertEqual(error.details["trust_problem"],
                         trust_module.PROBLEM_TRUST_NOT_PRESENT)
        self.assert_refused_durably(mission_id, bridge_module.PROBLEM_WORKSPACE_TRUST)
        # The child would read ANOTHER configuration than the one written.
        elsewhere = os.path.join(self.base, "the-childs-config.json")
        with open(elsewhere, "w") as handle:
            json.dump({"projects": {}}, handle)
        other = self.approved("trust written where the child does not read")
        self.patch(trust_module, "default_config_path", lambda home=None: elsewhere)
        error = self.refused(bridge_module.PROBLEM_WORKSPACE_TRUST,
                             self.make_bridge().dispatch, other)
        self.assertEqual(error.details["trust_problem"],
                         trust_module.PROBLEM_CONFIG_NOT_CONSUMED)
        self.assertEqual(self.spawn.calls, [])

    def test_T5_production_wiring_writes_where_the_child_reads_and_tests_never_do(self):
        sentinel = os.path.join(self.base, "production-config.json")
        self.patch(bridge_module, "production_trust_config_path", lambda: sentinel)
        bridge = request_cli.build_bridge(self.missions, self.control, self.clock,
                                          self.repository, self.root)
        self.assertIsInstance(bridge.worker, RuntimeWorker)
        self.assertEqual(bridge.worker.config_path, sentinel)
        self.assertEqual(bridge.worker.workspaces_root, self.root)
        self.assertFalse(os.path.exists(sentinel))
        # Ownership: the bridge's worker is a trust seam only. It observes
        # no live workspace, closes none and probes no readiness, so it is
        # never a second owner of workspaces or processes.
        for made in (bridge, bridge_module.MissionBridge(
                self.missions, self.control, self.clock)):
            self.assertFalse(made.worker.observes_live_workspaces)
            self.assertFalse(made.worker.closes_workspaces)
            self.assertIsNone(made.worker._readiness_probe_fn)
            self.assertIs(made.worker.transport, made._transport)
        # A bridge built without a configuration path never falls back to
        # the live one: it refuses (T2).
        self.assertIsNone(bridge_module.MissionBridge(
            self.missions, self.control, self.clock).worker.config_path)

    def test_T6_trust_never_reaches_another_path(self):
        a = self.approved("mission A")
        self.bridge.dispatch(a)
        b = self.approved("mission B")
        self.refused(bridge_module.PROBLEM_WORKSPACE_FOREIGN, self.bridge.dispatch,
                     b, self.path(a))
        self.assertEqual(sorted(self.config()["projects"]),
                         [trust_module.trust_key(self.path(a))])
        self.assertFalse(self.trusted(self.path(b)))


class SurfaceTests(Fixture):
    """Criterion 10: Grok Bot dispatch needs no path; the override is
    documented as recovery-only."""

    def adapter(self):
        surface = request_cli.build_surface(
            self.state, self.clock, self.control,
            lambda missions, control, clock: self.make_bridge(missions=missions))
        return adapter_module.GrokBotAdapter(
            surface, None, str(REPO_ROOT), index_module.RequestIndex(self.state),
            self.clock), surface

    def approved_ref(self, objective):
        out = self.submitted(objective)
        presented = self.surface.present(out["request_ref"])
        self.surface.attest_approval(
            request_ref=out["request_ref"], mission_id=presented["mission_id"],
            revision=presented["revision"],
            proposal_digest_sha256=presented["proposal_digest_sha256"],
            approved_action_scope=presented["approved_action_scope"],
            approved_delivery_targets=presented["approved_delivery_targets"],
            expires_at=self.clock() + 600, relayed_reply="approved",
            relay_ref="synthetic-relay")
        return out["request_ref"], out["mission_id"]

    def test_G1_grok_dispatch_takes_no_path(self):
        adapter, _ = self.adapter()
        for arguments in (None, {}):
            with self.subTest(arguments=arguments):
                ref, mission_id = self.approved_ref("grok %r" % (arguments,))
                result = adapter.run(request_ref=ref, command="dispatch",
                                     arguments=arguments)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["workspace"]["path_realpath"],
                                 self.path(mission_id))
                self.assertEqual(result["transport"], "grok_bot")
        self.assertEqual(len(self.spawn.calls), 2)

    def test_G2_the_recovery_path_is_optional_and_checked(self):
        adapter, _ = self.adapter()
        ref, mission_id = self.approved_ref("grok recovery")
        # A lost preparation acknowledgement, recovered through the tool.
        with self.assertRaises(Crash):
            self.make_bridge(transport=RecordingTransport(
                after=self.crash)).dispatch(mission_id)
        result = adapter.run(request_ref=ref, command="dispatch",
                             arguments={"workspace_path": self.path(mission_id)})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["workspace"]["path_realpath"], self.path(mission_id))
        ref, mission_id = self.approved_ref("grok unrelated path")
        checkout = make_repository(self.base, "grok-unrelated")
        refused = adapter.run(request_ref=ref, command="dispatch",
                              arguments={"workspace_path": checkout})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["problem"], bridge_module.PROBLEM_WORKSPACE_FOREIGN)
        self.assertTrue(refused["refusal_recorded"])
        for arguments in ({"workspace_path": 5},
                          {"workspace_path": checkout, "extra": 1}):
            refused = adapter.run(request_ref=ref, command="dispatch",
                                  arguments=arguments)
            self.assertFalse(refused["ok"])
            self.assertEqual(refused["problem"], adapter_module.PROBLEM_BAD_REQUEST)
        self.assertEqual(len(self.spawn.calls), 1)

    def crash(self):
        raise Crash()

    def test_G3_the_tool_contract_requires_no_path_and_names_the_override(self):
        self.assertEqual(adapter_module.RUN_ARGUMENTS["dispatch"], {})
        self.assertEqual(adapter_module.RUN_OPTIONAL_ARGUMENTS,
                         {"dispatch": {"workspace_path": str}})
        description, properties, required, extra = mcp_module.run_tool_definition()
        variant = next(v for v in extra["oneOf"]
                       if v["properties"]["command"]["const"] == "dispatch")
        self.assertNotIn("required", variant)
        arguments = variant["properties"]["arguments"]
        self.assertEqual(arguments["required"], [])
        self.assertEqual(arguments["type"], ["object", "null"])
        note = arguments["properties"]["workspace_path"]["description"]
        self.assertIn("operator recovery only", note)
        self.assertIn("dispatch takes no arguments", description)
        self.assertIn("operator recovery only", description)

    def test_G4_the_command_lines_take_the_configuration_not_a_path(self):
        parser = request_cli._parser()
        args = parser.parse_args([
            "--state-dir", self.state, "dispatch", "rq-x", "--control-repo",
            self.control, "--workspace-repository", self.repository,
            "--workspaces-root", self.root])
        self.assertIsNone(args.workspace)
        self.assertEqual(args.workspace_repository, self.repository)
        self.assertEqual(args.workspaces_root, self.root)


if __name__ == "__main__":
    unittest.main()
