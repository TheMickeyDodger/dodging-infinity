"""Task 8 increment 2 (+2c): authorized Mission -> real Herdr run.

Every effect goes through an INJECTED recorder or fake: the spawn bridge,
the read-only observers, the git transport, the protected-surface digest
and the process-ownership seam. The real ``production_spawn`` path exists
in product code and is pinned here as the production default, but it is
never invoked; no process is started, signalled or reaped, and no
delivery is invoked. The spawn fake models what herd does when it starts a
child: it records the child in the control repository's spawn records and
writes the child's own ``task.json`` description (the handoff) and start
time, which the read-only observer projects. The target's durable
artifacts (its checkpoint and canonical review file) are real files in a
per-Mission temporary workspace, read through the real hardened
primitive. Each test carries an independent SIGALRM termination bound
(CONTRIBUTING.md:74-79) and never relies on the code under test to return.

The approval itself goes through the increment-1 path unchanged: a local
proposal, ``present``, then ``attest_approval`` with the exact reply
"approved" (operator-attested, NOT independently verified).
"""

import ast
import hashlib
import itertools
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import authorization as authorization_module  # noqa: E402
from mission import progress as progress_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from local_request import store as store_module  # noqa: E402
from local_request import surface as surface_module  # noqa: E402
from pr_delivery import authorization as delivery_authorization  # noqa: E402
from pr_delivery import mission_parent  # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from target_runtime import evidence as evidence_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from target_runtime import process_ownership as ownership_module  # noqa: E402
from target_runtime.git_transport import GitTransport, GitTransportError  # noqa: E402
from test_local_request import NOW, Clock, contract, request  # noqa: E402
from test_mission_core import delivery_record, with_receipt  # noqa: E402

WATCHDOG_SECONDS = 60
REPO_URL = "https://github.com/Example/Repo"
OTHER_URL = "https://github.com/Example/Other"
BASELINE = "a" * 40
SURFACE = "b" * 64
TASK_ID = "task-0001"
AUTHENTICATED = mission_record.AuthenticatedContext(
    transport="synthetic_test", principal_kind="local_process_user",
    principal_ref="synthetic-tester")
RESULT_TEXT = "# Task checkpoint\n\nThe readiness probe fix landed with tests.\n"
REVIEW_TEXT = ("Reviewer: `reviewer1` / `session-1`\n\n## Transcript\n\n"
               "Protocol token: APPROVE\n")
TESTS_PASS_DIGEST = hashlib.sha256(b"the focused suite passed").hexdigest()


def run_contract(**extra):
    """An approved contract that declares the run-result requirement."""
    value = contract()
    value["requirements"].append({
        "key": "run_result", "description": "the bound target result",
        "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
        "required_artifact_keys": [], "max_evidence_age_seconds": 3600,
    })
    value.update(extra)
    return value


def run_request(**overrides):
    overrides.setdefault("proof_contract", run_contract())
    return request(**overrides)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeTransport(object):
    def __init__(self, origin=REPO_URL, head=BASELINE, dirty=""):
        self.origin, self.head, self.dirty = origin, head, dirty

    def remote_url(self, path):
        if self.origin is None:
            raise GitTransportError("synthetic: no origin")
        return self.origin + "\n"

    def head_commit(self, path):
        if self.head is None:
            raise GitTransportError("synthetic: no HEAD")
        return self.head + "\n"

    def status_porcelain_readonly(self, path):
        return {"status": "captured", "text": self.dirty,
                "total_bytes": len(self.dirty)}


def raw_observation(task_id=TASK_ID, status="RUNNING", decision="APPROVE",
                    extra_diagnostics=(), **task_fields):
    # herdr.observe-SHAPED: production observes with agents unprobed, so
    # the raw global completeness is PARTIAL with an agents diagnostic.
    task = {"state": "available", "id": task_id, "status": status}
    task.update(task_fields)
    return {
        "completeness": "PARTIAL",
        "diagnostics": [{"source": "agents", "state": "unavailable",
                         "detail": "agents not probed"}] + list(extra_diagnostics),
        "task": task,
        "reviews": {"state": "available", "truncated": False,
                    "listed": [{"round": 1, "decision": decision}]},
        "artifacts": {"state": "available", "listed": []},
    }


class Observer(object):
    """The read-only observer: a status template, plus the child task
    record (description, start time) each workspace actually holds."""

    def __init__(self):
        self.raw = raw_observation()
        self.tasks = {}

    def __call__(self, path):
        raw = json.loads(json.dumps(self.raw))
        task = raw.get("task")
        if isinstance(task, dict):
            child = self.tasks.get(os.path.realpath(path), {})
            task.setdefault("description", child.get("description"))
            task.setdefault("started_at", child.get("started_at"))
        return raw


class SpawnRecorder(object):
    """Records every request; starts nothing. Models herd's own effect:
    a child record in the control spawn records and the child's task.json
    (description = the handoff, truncated by the observer to 200)."""

    def __init__(self, observer, clock, task_id=TASK_ID):
        self.observer, self.clock = observer, clock
        self.calls, self.records = [], []
        self.task_id, self.group, self.raises = task_id, None, None

    def __call__(self, parent_repo, request_):
        self.calls.append((parent_repo, dict(request_)))
        if self.raises == "before_child":
            raise OSError("synthetic: spawn failed before any child existed")
        self.observer.tasks[request_["target_repo"]] = {
            "description": request_["task"][:200], "started_at": self.clock()}
        self.records.append({"repo": request_["target_repo"],
                             "task_id": self.task_id})
        if self.raises == "after_child":
            raise OSError("synthetic: spawn outcome unknown after the child")
        result = {"task": {"id": self.task_id},
                  "child_record": {"task_id": self.task_id},
                  "repo": request_["target_repo"]}
        if self.group is not None:
            result["owned_process_group"] = self.group
        return result


class FakeOwnership(object):
    """The ownership seam's shape; owns only what the test says."""

    def __init__(self, owned=(), verdict=ownership_module.REAPED):
        self.owned, self.verdict, self.reaped = set(owned), verdict, []

    def owned_groups(self, directory=None):
        return set(self.owned)

    def reap_owned(self, pgid, directory=None, **kwargs):
        self.reaped.append((pgid, directory))
        return self.verdict, None


class Bounded(unittest.TestCase):
    def setUp(self):
        def expired(signum, frame):
            raise TimeoutError("watchdog: test exceeded %d s" % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)


class Fixture(Bounded):
    def setUp(self):
        super(Fixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.clock = Clock(NOW)
        self.missions = mission_service.MissionService(
            mission_store.MissionStore(self.state), self.clock)
        self.surface = surface_module.LocalRequestSurface(
            self.missions, store_module.LocalRequestStore(self.state), self.clock)
        self.observer = Observer()
        self.spawn = SpawnRecorder(self.observer, self.clock)
        self.transport = FakeTransport()
        self.ownership = FakeOwnership()
        self.surface_digest = {"status": "exact", "digest": SURFACE}
        self.spawn_records = None
        self.workspaces = {}
        self.bridge = self.make_bridge()
        self.context = self.bridge._context

    def records(self, repo):
        if self.spawn_records is not None:
            return json.loads(json.dumps(self.spawn_records))
        listed = [dict(r) for r in self.spawn.records]
        return {"state": "available" if listed else "empty", "truncated": False,
                "listed": listed, "count": len(listed)}

    def make_bridge(self, missions=None):
        return bridge_module.MissionBridge(
            missions or self.missions, "/control-repo", self.clock,
            spawn_fn=self.spawn, observer_fn=self.observer,
            spawn_records_fn=self.records, transport=self.transport,
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope")

    def ws(self, mission_id):
        """A per-Mission clean workspace (the lease rule allows one live run
        per workspace)."""
        if mission_id not in self.workspaces:
            path = os.path.join(self.tmp.name, "ws-%d" % len(self.workspaces))
            os.makedirs(os.path.join(path, ".herd", "state", "reviews"))
            self.workspaces[mission_id] = path
        return self.workspaces[mission_id]

    def real_ws(self, mission_id):
        return os.path.realpath(self.ws(mission_id))

    def dispatch(self, mission_id, **kwargs):
        return self.bridge.dispatch(mission_id, self.ws(mission_id), **kwargs)

    def write_artifacts(self, mission_id, task_id=TASK_ID, result=RESULT_TEXT,
                        review=REVIEW_TEXT):
        state_dir = os.path.join(self.ws(mission_id), ".herd", "state")
        with open(os.path.join(state_dir, "task-checkpoint.md"), "w") as handle:
            handle.write(result)
        name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (task_id, 1)
        with open(os.path.join(state_dir, "reviews", name), "w") as handle:
            handle.write(review)

    def reported(self, **changes):
        value = {"task_id": TASK_ID, "result_digest_sha256": sha256(RESULT_TEXT),
                 "review_digest_sha256": sha256(REVIEW_TEXT)}
        value.update(changes)
        return value

    def approved(self, **overrides):
        """The increment-1 path: propose, present, exact "approved"."""
        out = self.surface.submit(run_request(**overrides))
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

    def authorization(self, mission_id):
        return self.mission(mission_id)["authorization_ids"][-1]

    def mission(self, mission_id):
        return self.missions.get(mission_id)["record"]

    def mission_bytes(self):
        with open(os.path.join(self.state, "missions.json"), "rb") as handle:
            return handle.read()

    def refused(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(bridge_module.MissionBridgeRefusal) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, caught.exception.reason)
        return caught.exception

    def core_refused(self, problem, callable_, *args):
        with self.assertRaises(mission_record.MissionError) as caught:
            callable_(*args)
        self.assertEqual(caught.exception.problem, problem, str(caught.exception))
        return caught.exception

    def intent_args(self, mission_id, **changes):
        args = dict(
            authorization_id=self.authorization(mission_id), revision=1,
            digest=self.mission(mission_id)["revisions"][0]["proposal_digest_sha256"],
            url=REPO_URL, scope=["engineering_change", "repository_read"],
            targets=[], workspace=self.real_ws(mission_id))
        args.update(changes)
        return (mission_id, args["authorization_id"], args["revision"],
                args["digest"], args["url"], args["workspace"], BASELINE,
                "d" * 64, "e" * 64, SURFACE, args["scope"], args["targets"],
                self.context)

    def state_op(self, mission_id, method, *args):
        operation = self.missions.mint_state_operation_id(AUTHENTICATED)
        sequence = self.missions.get_state(mission_id)["sequence"]
        return method(mission_id, operation, sequence, *(args + (AUTHENTICATED,)))

    def satisfy_tests_pass(self, mission_id):
        """The approved contract's OTHER requirement, met by its own
        accepted evidence (the positive path satisfies the WHOLE contract)."""
        submitted = self.state_op(
            mission_id, self.missions.submit_evidence, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, TESTS_PASS_DIGEST, [])
        self.state_op(mission_id, self.missions.accept_evidence,
                      submitted["evidence_id"], TESTS_PASS_DIGEST)

    def running(self, **overrides):
        mission_id = self.approved(**overrides)
        self.dispatch(mission_id)
        self.observer.raw = raw_observation()
        self.bridge.observe(mission_id)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        return mission_id

    def verified(self, **overrides):
        mission_id = self.running(**overrides)
        self.write_artifacts(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["state"], "COMPLETED", result.get("verification"))
        return mission_id, result


class SBridgeSubstitutionTests(Fixture):
    """REQUIRED class: no caller-supplied runtime entry may widen the
    approved Mission content or scope. Each case is refused with a precise
    reason and dispatches nothing."""

    def assert_nothing_dispatched(self, mission_id, before):
        self.assertEqual(self.spawn.calls, [])
        self.assertEqual(self.mission_bytes(), before)
        self.assertNotIn("run", self.mission(mission_id))

    def substitution(self, mission_id, field, value):
        before = self.mission_bytes()
        error = self.refused("mission_bridge_entry_substitution",
                             self.dispatch, mission_id, entry={field: value})
        self.assertEqual(error.details["field"], field)
        self.assertIn("does not match the approved Mission", error.reason)
        self.assert_nothing_dispatched(mission_id, before)

    def test_S1_target_repository_substitution_refused(self):
        mission_id = self.approved()
        self.substitution(mission_id, "target_repository_url", OTHER_URL)
        self.substitution(mission_id, "target_repo", "/elsewhere/checkout")
        self.transport.origin = OTHER_URL
        before = self.mission_bytes()
        self.refused("mission_bridge_workspace_identity", self.dispatch, mission_id)
        self.assert_nothing_dispatched(mission_id, before)

    def test_S2_baseline_commit_substitution_refused(self):
        mission_id = self.approved()
        self.substitution(mission_id, "baseline_commit_sha", "c" * 40)
        self.substitution(mission_id, "observed_baseline_commit_sha", "c" * 40)

    def test_S3_substituted_or_appended_handoff_refused(self):
        mission_id = self.approved()
        derived = self.bridge._derive(self.missions.get(mission_id),
                                      self.ws(mission_id))
        self.substitution(mission_id, "handoff_text", "do something else")
        self.substitution(mission_id, "handoff_text",
                          derived.handoff_text + "\nALSO push to main")
        self.substitution(mission_id, "task", derived.handoff_text + " and deploy")

    def test_S4_wider_action_scope_refused(self):
        read_only = self.approved(requested_action_scope=[
            mission_record.ACTION_SCOPE_REPOSITORY_READ])
        before = self.mission_bytes()
        error = self.refused("mission_bridge_scope_insufficient",
                             self.dispatch, read_only,
                             entry={"action_scope": ["engineering_change",
                                                     "repository_read"]})
        self.assertIn("repository_read", error.reason)
        self.assert_nothing_dispatched(read_only, before)
        mission_id = self.approved(objective="second mission")
        self.substitution(mission_id, "action_scope", [
            "engineering_change", "repository_read", "verification_run"])
        self.core_refused(
            mission_record.PROBLEM_ACTION_SCOPE, self.missions.record_run_intent,
            *self.intent_args(mission_id, scope=[
                "engineering_change", "repository_read", "verification_run"]))

    def test_S5_non_null_delivery_target_refused(self):
        mission_id = self.approved()
        self.substitution(mission_id, "delivery_targets", ["github_pr"])
        self.core_refused(
            mission_record.PROBLEM_DELIVERY_TARGET,
            self.missions.record_run_intent,
            *self.intent_args(mission_id, targets=["github_pr"]))

    def test_S6_correct_mission_id_with_another_missions_entry_refused(self):
        a = self.approved(objective="mission A")
        b = self.approved(objective="mission B")
        claims_b = self.bridge._derive(self.missions.get(b), self.ws(b)).claims()
        before = self.mission_bytes()
        for field in ("mission_id", "proposal_digest_sha256", "authorization_id",
                      "handoff_text", "task", "alias", "target_repo"):
            error = self.refused("mission_bridge_entry_substitution",
                                 self.dispatch, a, entry={field: claims_b[field]})
            self.assertEqual(error.details["field"], field)
        self.refused("mission_bridge_entry_substitution", self.dispatch, a,
                     entry=claims_b)
        self.assert_nothing_dispatched(a, before)
        self.core_refused(
            "mission_authorization_wrong_mission", self.missions.record_run_intent,
            *self.intent_args(a, authorization_id=self.authorization(b)))
        self.assertEqual(self.mission_bytes(), before)

    def test_S7_entry_mutated_after_validation_is_never_reread(self):
        mission_id = self.approved()
        derived = self.bridge._derive(self.missions.get(mission_id),
                                      self.ws(mission_id))
        approved_claims = derived.claims()

        class Shifty(dict):
            """Returns the approved value on the first read, a widened one
            on every later read."""
            reads = {}

            def __getitem__(self, key):
                self.reads[key] = self.reads.get(key, 0) + 1
                value = dict.__getitem__(self, key)
                return value if self.reads[key] == 1 else "/evil/" + str(value)

        entry = Shifty({"target_repo": approved_claims["target_repo"],
                        "task": approved_claims["task"]})
        real_intent = self.missions.record_run_intent

        def mutate_then_record(*args):
            dict.__setitem__(entry, "task", "SUBSTITUTED AFTER VALIDATION")
            dict.__setitem__(entry, "target_repo", "/other/workspace")
            return real_intent(*args)

        self.missions.record_run_intent = mutate_then_record
        self.dispatch(mission_id, entry=entry)
        self.assertEqual(Shifty.reads, {"target_repo": 1, "task": 1})
        self.assertEqual(len(self.spawn.calls), 1)
        sent = self.spawn.calls[0][1]
        self.assertEqual(sent, derived.spawn_request())
        self.assertEqual(sent["task"], derived.handoff_text)
        self.assertEqual(sent["target_repo"], self.real_ws(mission_id))

    def test_S8_unknown_entry_field_refused(self):
        mission_id = self.approved()
        before = self.mission_bytes()
        for field in ("rules", "force", "policy", "test_command"):
            error = self.refused("mission_bridge_entry_unknown_field",
                                 self.dispatch, mission_id, entry={field: True})
            self.assertEqual(error.details["field"], field)
        self.assert_nothing_dispatched(mission_id, before)

    def test_S9_every_spawn_field_is_derived_from_the_approved_record(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        parent, sent = self.spawn.calls[0]
        self.assertEqual(sorted(sent), ["alias", "preset", "target_repo", "task"])
        record_ = self.mission(mission_id)["revisions"][0]
        self.assertIn(record_["proposal"]["objective"], sent["task"])
        self.assertTrue(sent["task"].startswith(bridge_module.handoff_prefix(
            mission_id, 1, record_["proposal_digest_sha256"])))
        self.assertLess(len(bridge_module.handoff_prefix(
            mission_id, 1, record_["proposal_digest_sha256"])), 200)
        self.assertIn("APPROVED ACTION SCOPE\nengineering_change, repository_read",
                      sent["task"])
        self.assertIn("APPROVED DELIVERY TARGETS\n(none)", sent["task"])
        self.assertEqual(sent["task"], sent["task"].strip())
        self.assertEqual(sent["alias"], dispatch_module.ALIAS_PREFIX + mission_id)
        self.assertEqual(sent["preset"], dispatch_module.DI_TARGET_EXECUTION_PRESET)
        intent = self.mission(mission_id)["run"]["intent"]
        self.assertEqual(intent["observed_baseline_commit_sha"], BASELINE)
        self.assertEqual(intent["workspace_realpath"], self.real_ws(mission_id))
        self.assertEqual(intent["target_repository_url"], REPO_URL)


class BObservedBaselineTests(Fixture):
    """The baseline is OBSERVED by DI at dispatch, never approved."""

    def test_B1_the_record_and_status_name_it_observed(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        intent = self.mission(mission_id)["run"]["intent"]
        self.assertIn("observed_baseline_commit_sha", intent)
        self.assertNotIn("baseline_commit_sha", intent)
        self.assertEqual(self.bridge.status(mission_id)["baseline"],
                         "observed by DI at dispatch, not human-approved: the"
                         " human approved a scope, not a commit")

    def test_B2_nothing_calls_it_an_approved_baseline(self):
        for path in [REPO_ROOT / "target_runtime" / "mission_bridge.py"] + sorted(
            (REPO_ROOT / "mission").glob("*.py")
        ):
            text = " ".join(path.read_text().lower().split())
            self.assertNotIn("approved baseline", text, path)

    def test_B3_a_dirty_workspace_cannot_carry_unapproved_content(self):
        mission_id = self.approved()
        self.transport.dirty = " M src/readiness.py\n"
        before = self.mission_bytes()
        error = self.refused("mission_bridge_workspace_not_clean",
                             self.dispatch, mission_id)
        self.assertIn("indistinguishable from the run's own work", error.reason)
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.spawn.calls, [])

    def test_B4_a_mission_without_a_run_result_requirement_is_refused(self):
        mission_id = self.approved(proof_contract=contract())
        self.refused("mission_bridge_no_result_requirement",
                     self.dispatch, mission_id)
        self.assertEqual(self.spawn.calls, [])


class DDispatchTests(Fixture):
    def test_D1_non_authorized_mission_refused(self):
        out = self.surface.submit(run_request())
        self.refused("mission_bridge_not_runnable", self.dispatch, out["mission_id"])
        self.assertEqual(self.spawn.calls, [])

    def test_D2_dispatch_records_intent_then_receipt_and_never_running(self):
        mission_id = self.approved()
        result = self.dispatch(mission_id)
        self.assertEqual(result["phase"], "dispatched_not_yet_observed")
        mission = self.mission(mission_id)
        self.assertEqual(mission["state"], "AUTHORIZED")
        self.assertNotIn("lifecycle", mission)
        run = mission["run"]
        self.assertEqual(run["receipt"]["task_id"], TASK_ID)
        self.assertEqual(run["receipt"]["identity_source"], "start_result")
        self.assertLessEqual(run["intent"]["recorded_at"],
                             run["receipt"]["recorded_at"])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_D3_duplicate_dispatch_records_no_second_dispatch(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        before = self.mission_bytes()
        again = self.make_bridge().dispatch(mission_id, self.ws(mission_id))
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.mission_bytes(), before)
        self.core_refused(mission_record.PROBLEM_RUN_ALREADY_RECORDED,
                          self.missions.record_run_intent,
                          *self.intent_args(mission_id))

    def test_D4_binding_changed_refuses_dispatch(self):
        expired = self.approved(objective="expires")
        self.clock.now += 601
        self.refused("mission_bridge_not_runnable", self.dispatch, expired)
        self.clock.now = NOW
        edited = self.approved(objective="edited")
        self.missions.edit(edited, 1, run_request(objective="edited again"),
                           self.missions.mint_decision_id(AUTHENTICATED),
                           AUTHENTICATED)
        self.refused("mission_bridge_not_runnable", self.dispatch, edited)
        current = self.approved(objective="current")
        for changes, problem in (
            ({"revision": 2}, "mission_authorization_wrong_revision"),
            ({"digest": "f" * 64}, "mission_authorization_wrong_manifest_digest"),
        ):
            with self.subTest(problem=problem):
                self.core_refused(problem, self.missions.record_run_intent,
                                  *self.intent_args(current, **changes))
        out = self.surface.submit(run_request(objective="withdrawn"))
        self.surface.cancel(out["request_ref"], out["control_capability"])
        self.refused("mission_bridge_not_runnable", self.dispatch, out["mission_id"])
        self.assertEqual(self.spawn.calls, [])

    def test_D5_spawn_raising_is_a_hold_never_retried(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        held = self.dispatch(mission_id)
        self.assertTrue(held["hold"])
        self.assertEqual(held["phase"], "hold_intent_outcome_unknown")
        self.assertIsNone(self.mission(mission_id)["run"]["receipt"])
        self.spawn.raises = None
        before = self.mission_bytes()
        for _ in range(3):
            again = self.make_bridge().dispatch(mission_id, self.ws(mission_id))
            self.assertTrue(again["hold"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.mission_bytes(), before)
        self.assertTrue(self.make_bridge().observe(mission_id)["hold"])
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_D6_unresolved_identity_is_a_hold(self):
        mission_id = self.approved()
        self.spawn.task_id = ""
        held = self.dispatch(mission_id)
        self.assertEqual(held["phase"], "hold_target_identity_unknown")
        self.assertTrue(held["hold"])
        self.assertTrue(self.bridge.observe(mission_id)["hold"])
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_D7_receipt_write_failure_after_spawn_is_a_hold(self):
        mission_id = self.approved()
        real = self.missions.record_run_receipt

        def fail(*args):
            raise OSError("synthetic: receipt write failed")

        self.missions.record_run_receipt = fail
        held = self.dispatch(mission_id)
        self.missions.record_run_receipt = real
        self.assertTrue(held["hold"])
        self.make_bridge().dispatch(mission_id, self.ws(mission_id))
        self.assertEqual(len(self.spawn.calls), 1)

    def test_D8_production_defaults_are_the_real_seams_never_invoked(self):
        bridge = bridge_module.MissionBridge(self.missions, "/control", self.clock)
        self.assertIs(bridge._spawn, dispatch_module.production_spawn)
        self.assertIs(bridge._observer,
                      bridge_module.broker_module._production_observer)
        self.assertIsInstance(bridge._transport, GitTransport)
        self.assertIs(bridge._ownership, ownership_module)
        self.assertEqual(bridge._context.principal_kind, "local_process_user")
        self.assertEqual(bridge._context.principal_ref, "uid-%d" % os.getuid())


class RReconcileTests(Fixture):
    def held(self, mode="after_child", **overrides):
        mission_id = self.approved(**overrides)
        self.spawn.raises = mode
        self.dispatch(mission_id)
        self.spawn.raises = None
        return mission_id

    def test_R1_binds_exactly_one_provable_child_then_observes(self):
        mission_id = self.held()
        result = self.bridge.reconcile(mission_id)
        receipt = self.mission(mission_id)["run"]["receipt"]
        self.assertEqual(receipt["task_id"], TASK_ID)
        self.assertEqual(receipt["identity_source"], "reconciliation")
        self.assertEqual(result["phase"], "dispatched_not_yet_observed")
        self.bridge.observe(mission_id)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertEqual(len(self.spawn.calls), 1)

    def assert_blocked(self, mission_id, reason):
        status = self.make_bridge().status(mission_id)
        self.assertEqual(status["state"], "BLOCKED")
        self.assertEqual(status["lifecycle"][-1]["reason"], reason)
        self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                          self.missions.record_run_receipt, mission_id,
                          TASK_ID, "reconciliation", None, self.context)

    def test_R2_no_match_multiple_conflicting_degraded_stop_durably(self):
        cases = (
            ([], "reconcile_no_match"),
            ([{"task_id": TASK_ID}, {"task_id": TASK_ID}],
             "reconcile_multiple_matches"),
            ([{"task_id": "someone-else"}], "reconcile_conflicting_identity"),
        )
        for listed, reason in cases:
            with self.subTest(reason=reason):
                mission_id = self.held(objective=reason)
                listed = [dict(c, repo=self.real_ws(mission_id)) for c in listed]
                self.spawn_records = {"state": "available", "truncated": False,
                                      "listed": listed, "count": len(listed)}
                self.bridge.reconcile(mission_id)
                self.assert_blocked(mission_id, reason)
        mission_id = self.held(objective="degraded")
        self.spawn_records = {"state": "available", "truncated": True,
                              "listed": [], "count": 0}
        self.bridge.reconcile(mission_id)
        self.assert_blocked(mission_id, "reconcile_degraded")
        self.assertEqual(len(self.spawn.calls), 4)


class FAssociationTests(Fixture):
    """Round-14 BLOCKING 2: a child is adopted only with durable proof that
    it belongs to THIS Mission's dispatch intent; a child that merely
    shares the workspace is never adopted."""

    def test_F1_exactly_as_reproduced_b_cannot_adopt_a_live_missions_child(self):
        a = self.approved(objective="mission A")
        shared = self.ws(a)
        self.bridge.dispatch(a, shared)
        self.assertEqual(self.mission(a)["run"]["receipt"]["task_id"], TASK_ID)
        b = self.approved(objective="mission B")
        self.spawn.raises = "before_child"
        before = self.mission_bytes()
        error = self.refused("mission_bridge_mission_core_refused",
                             self.bridge.dispatch, b, shared)
        self.assertEqual(error.details["mission_problem"],
                         mission_record.PROBLEM_RUN_WORKSPACE_BOUND)
        self.assertNotIn("run", self.mission(b))
        self.refused("mission_bridge_wrong_state", self.bridge.reconcile, b)
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(json.loads(self.mission_bytes())["missions"][a],
                         json.loads(before)["missions"][a])

    def test_F2_a_foreign_child_in_a_released_workspace_is_never_adopted(self):
        # Mission A ran and left its child, task record and artifacts in the
        # workspace; A is terminal, so the lease is released.
        a = self.running(objective="mission A")
        shared = self.ws(a)
        self.write_artifacts(a)
        self.bridge.cancel(a)
        self.assertEqual(self.mission(a)["state"], "CANCELLED")
        # Mission B uses the same workspace; its spawn fails before creating
        # any child, so the only child recorded there is A's.
        b = self.approved(objective="mission B")
        self.workspaces[b] = shared
        self.spawn.raises = "before_child"
        held = self.bridge.dispatch(b, shared)
        self.assertTrue(held["hold"])
        self.assertEqual(len(self.spawn.records), 1)
        result = self.bridge.reconcile(b)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["lifecycle"][-1]["reason"],
                         "reconcile_unproven_association")
        self.assertIsNone(self.mission(b)["run"]["receipt"])
        self.refused("mission_bridge_wrong_state", self.bridge.observe, b)
        self.refused("mission_bridge_wrong_state", self.bridge.verify, b,
                     self.reported())
        self.assertFalse(self.bridge.result(b)["verified_result"])
        self.assertEqual(self.mission(a)["state"], "CANCELLED")

    def test_F3_running_needs_the_target_task_record_to_name_this_intent(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        other_digest = "c" * 64
        for fields in (
            {"description": bridge_module.handoff_prefix(
                "mn-" + "0" * 32, 1, other_digest) + " foreign",
             "started_at": NOW},
            {"description": None, "started_at": NOW},
            {"started_at": NOW - 1},
        ):
            with self.subTest(fields=sorted(fields)):
                if "description" not in fields:
                    fields = dict(fields, description=self.observer.tasks[
                        self.real_ws(mission_id)]["description"])
                self.observer.raw = raw_observation(**fields)
                self.assertFalse(self.bridge.observe(mission_id)["observed"])
                self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")
        self.observer.raw = raw_observation()
        self.assertTrue(self.bridge.observe(mission_id)["observed"])

    def test_F4_verification_identity_needs_the_association_too(self):
        mission_id = self.running()
        self.write_artifacts(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.observer.raw = raw_observation(
            status="COMPLETE", started_at=NOW,
            description=bridge_module.handoff_prefix("mn-" + "0" * 32, 1, "c" * 64))
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["verification"]["failed_conjunct"],
                         "verify_target_identity_mismatch")


class OObservationTests(Fixture):
    def test_O1_running_only_from_observation_of_the_bound_target(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        self.observer.raw = raw_observation(task_id="not-ours")
        self.assertFalse(self.bridge.observe(mission_id)["observed"])
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")
        self.core_refused(mission_record.PROBLEM_RUN,
                          self.missions.record_observed_running, mission_id,
                          "not-ours", False, self.context)
        self.observer.raw = raw_observation(
            extra_diagnostics=[{"source": "task", "state": "unavailable",
                                "detail": "synthetic"}])
        self.assertFalse(self.bridge.observe(mission_id)["observed"])
        self.observer.raw = raw_observation()
        self.assertTrue(self.bridge.observe(mission_id)["observed"])
        mission = self.mission(mission_id)
        self.assertEqual(mission["state"], "RUNNING")
        self.assertEqual(mission["lifecycle"][0]["reason"], "observed_running")

    def test_O2_first_seen_already_stopped_has_its_own_reason(self):
        mission_id = self.approved()
        self.dispatch(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.bridge.observe(mission_id)
        self.assertEqual(self.mission(mission_id)["lifecycle"][0]["reason"],
                         "observed_after_stop")


class VVerificationTests(Fixture):
    def test_V1_verified_completion_is_a_di_decided_conjunction(self):
        mission_id, result = self.verified()
        verification = result["verification"]
        self.assertTrue(verification["verified"])
        self.assertTrue(all(c["holds"] for c in verification["conjuncts"]))
        self.assertEqual(verification["raw_global_completeness"], "PARTIAL")
        self.assertTrue(verification["supports_verification"])
        self.assertTrue(result["engineering_completion"])
        self.assertTrue(result["verified_completion"])
        self.assertFalse(result["delivered"])
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["review_evidence"],
                         "target-produced, not independent verification")
        # The positive fixture satisfies its WHOLE contract (round-14 note).
        proof = self.missions.get_state(mission_id)["proof"]
        self.assertTrue(proof is None or proof["satisfied"], proof)

    def test_V2_each_failing_conjunct_stops_with_its_own_code(self):
        cases = (
            ("verify_result_not_reported", {"reported": "a free-text claim"}),
            ("verify_result_not_bound",
             {"reported": self.reported(result_digest_sha256="0" * 64)}),
            ("verify_evidence_incomplete",
             {"raw": raw_observation(status="COMPLETE", extra_diagnostics=[
                 {"source": "artifacts", "state": "malformed", "detail": "x"}])}),
            ("verify_target_identity_mismatch",
             {"raw": raw_observation(task_id="other", status="COMPLETE")}),
            ("verify_target_not_stopped", {"raw": raw_observation()}),
            ("verify_review_not_approve",
             {"raw": raw_observation(status="COMPLETE", decision="REJECT")}),
            ("verify_baseline_moved", {"head": "c" * 40}),
            ("verify_surface_changed", {"surface": "9" * 64}),
        )
        for code, change in cases:
            with self.subTest(code=code):
                mission_id = self.running(objective="verify %s" % code)
                self.write_artifacts(mission_id)
                self.satisfy_tests_pass(mission_id)
                self.observer.raw = change.get(
                    "raw", raw_observation(status="COMPLETE"))
                self.transport.head = change.get("head", BASELINE)
                self.surface_digest = {"status": "exact",
                                       "digest": change.get("surface", SURFACE)}
                result = self.bridge.verify(mission_id,
                                            change.get("reported", self.reported()))
                self.assertEqual(result["state"], "BLOCKED")
                self.assertEqual(result["verification"]["failed_conjunct"], code)
                self.assertFalse(result["verified_completion"])
                self.assertIsNone(result["verification"]["result_evidence_id"])
                self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                                  self.missions.record_verification, mission_id,
                                  {}, None, None, None, None, "COMPLETE",
                                  self.context)
                self.transport.head = BASELINE
                self.surface_digest = {"status": "exact", "digest": SURFACE}

    def test_V3_a_failed_target_with_a_review_artifact_never_verifies(self):
        for status in ("ERROR", "ABORTED"):
            with self.subTest(status=status):
                mission_id = self.running(objective="failed %s" % status)
                self.write_artifacts(mission_id)
                self.satisfy_tests_pass(mission_id)
                self.observer.raw = raw_observation(status=status)
                result = self.bridge.verify(mission_id, self.reported())
                verification = result["verification"]
                holds = dict((c["name"], c["holds"])
                             for c in verification["conjuncts"])
                self.assertTrue(holds["target_stopped"])
                self.assertTrue(holds["review_approve"])
                self.assertTrue(holds["result_bound"])
                self.assertFalse(holds["target_succeeded"])
                self.assertEqual(verification["failed_conjunct"],
                                 "verify_target_not_succeeded")
                self.assertEqual(result["state"], "BLOCKED")
                self.assertFalse(result["engineering_completion"])
                self.assertFalse(result["verified_completion"])

    def test_V4_only_the_full_conjunction_is_verified(self):
        names = [name for name, _ in mission_record.VERIFY_CONJUNCTS]
        self.assertEqual(len(names), 12)
        self.assertEqual(len(set(c for _, c in mission_record.VERIFY_CONJUNCTS)),
                         len(names))
        verified_combinations = 0
        for values in itertools.product((True, False), repeat=len(names)):
            holds = dict(zip(names, values))
            verified, failed = mission_record.verification_outcome(holds)
            if verified:
                verified_combinations += 1
                self.assertTrue(all(values))
                self.assertIsNone(failed)
            else:
                first = names[values.index(False)]
                self.assertEqual(failed, dict(mission_record.VERIFY_CONJUNCTS)[first])
        self.assertEqual(verified_combinations, 1)
        self.assertFalse(mission_record.verification_outcome(
            dict((n, 1) for n in names))[0])

    def test_V5_mission_core_refuses_verified_without_accepted_result_evidence(self):
        mission_id = self.running()
        self.satisfy_tests_pass(mission_id)
        names = [name for name, _ in mission_record.VERIFY_CONJUNCTS]
        before = self.mission_bytes()
        for evidence_id in (None, "mv-" + "0" * 32):
            with self.subTest(evidence_id=evidence_id):
                self.core_refused(
                    mission_record.PROBLEM_RUN, self.missions.record_verification,
                    mission_id, dict((n, True) for n in names), "PARTIAL", True,
                    None, evidence_id, "COMPLETE", self.context)
        self.assertEqual(self.mission_bytes(), before)

    def test_V6_the_result_is_recovered_from_durable_records_after_restart(self):
        mission_id, result = self.verified()
        fresh = bridge_module.MissionBridge(
            mission_service.MissionService(
                mission_store.MissionStore(self.state), self.clock),
            "/unrelated", self.clock)
        recovered = fresh.result(mission_id)
        self.assertTrue(recovered["verified_result"])
        self.assertTrue(recovered["recoverable"])
        self.assertEqual(recovered["result_text"], RESULT_TEXT)
        self.assertEqual(recovered["review_text"], REVIEW_TEXT)
        self.assertEqual(recovered["task_id"], TASK_ID)
        binding = recovered["binding"]
        self.assertEqual(binding["result_digest_sha256"], sha256(RESULT_TEXT))
        self.assertEqual(binding["review_digest_sha256"], sha256(REVIEW_TEXT))
        self.assertEqual(binding["review_locator"],
                         ".herd/state/reviews/task-0001-round-01.md")
        self.assertEqual(recovered["evidence_id"],
                         result["verification"]["result_evidence_id"])
        state = self.missions.get_state(mission_id)["record"]
        evidence = [e for e in state["evidence"]
                    if e["evidence_id"] == recovered["evidence_id"]][0]
        self.assertEqual(evidence["kind"], "VERIFICATION_RECORD")
        self.assertEqual(evidence["requirement_key"], "run_result")
        self.assertIsNotNone(evidence["acceptance"])

    def test_V7_a_result_bound_to_another_target_or_review_is_refused(self):
        for changes in ({"task_id": "other-task"},
                        {"review_digest_sha256": sha256("another review")},
                        {"result_digest_sha256": sha256("another result")}):
            with self.subTest(changes=changes):
                mission_id = self.running(objective="bind %s" % sorted(changes))
                self.write_artifacts(mission_id)
                self.satisfy_tests_pass(mission_id)
                self.observer.raw = raw_observation(status="COMPLETE")
                result = self.bridge.verify(mission_id, self.reported(**changes))
                self.assertEqual(result["verification"]["failed_conjunct"],
                                 "verify_result_not_bound")
                self.assertFalse(self.bridge.result(mission_id)["verified_result"])

    def test_V8_a_digest_alone_is_never_a_verified_result(self):
        mission_id = self.running()
        self.satisfy_tests_pass(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["verification"]["failed_conjunct"],
                         "verify_result_not_bound")
        verified_id, _ = self.verified(objective="later lost")
        os.remove(os.path.join(self.ws(verified_id), ".herd", "state",
                               "task-checkpoint.md"))
        recovered = self.bridge.result(verified_id)
        self.assertFalse(recovered["recoverable"])
        self.assertFalse(recovered["verified_result"])
        self.assertIn("a digest alone is never a verified result",
                      recovered["reason"])

    def test_V9_source_scoped_support_ignores_unconsumed_sources(self):
        mission_id = self.running()
        self.write_artifacts(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE", extra_diagnostics=[
            {"source": "config", "state": "malformed", "detail": "x"},
            {"source": "runtime", "state": "unreadable", "detail": "y"}])
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["verification"]["raw_global_completeness"],
                         "PARTIAL")


class KWholeContractTests(Fixture):
    """Round-14 BLOCKING 1 + round-15 state truth: VERIFIED needs the WHOLE
    approved proof contract, decided by the existing
    ``progress.closure_failures`` inside Mission Core's locked completion
    decision. When every conjunct holds but an obligation is unmet, Mission
    Core DURABLY records a non-terminal, recoverable pending-proof state:
    the stopped target's observation and every blocker code. A fresh
    process reports it truthfully, and the SAME consumed run verifies once
    the obligation is met, with no new dispatch and no renewed authority.
    (Intentional replacement of the round-14 pin, which asserted the
    refusal-only behaviour that lost the outcome.)"""

    def fresh_bridge(self):
        return bridge_module.MissionBridge(
            mission_service.MissionService(
                mission_store.MissionStore(self.state), self.clock),
            "/unrelated", self.clock)

    def assert_pending_proof(self, mission_id, problem, first=None):
        """``problem`` is among the recorded blockers; the first recorded
        blocker is ``first`` (default ``problem``). Durable across restart."""
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        before = self.mission(mission_id)
        result = self.bridge.verify(mission_id, self.reported())
        for status in (result, self.fresh_bridge().status(mission_id)):
            self.assertEqual(status["state"], "RUNNING")
            self.assertEqual(status["phase"], "verification_blocked_pending_proof")
            self.assertFalse(status["hold"])
            self.assertTrue(status["target_stopped_observed"])
            self.assertIsNone(status["verification"])
            self.assertFalse(status["verified_completion"])
            pending = status["pending_proof"]
            codes = [b["code"] for b in pending["blockers"]]
            self.assertEqual(codes[0], first or problem, pending)
            self.assertIn(problem, codes)
            self.assertEqual(pending["observed_task_status"], "COMPLETE")
            self.assertEqual(pending["target_task_id"], TASK_ID)
        after = self.mission(mission_id)
        self.assertEqual(after["lifecycle"], before["lifecycle"])
        self.assertIsNone(after["run"]["verification"])
        return pending

    def test_K1_missing_proof_persists_pending_then_the_same_run_verifies(self):
        mission_id = self.running()
        pending = self.assert_pending_proof(
            mission_id, progress_module.PROBLEM_PROOF_NOT_SATISFIED)
        self.assertIn("tests_pass=MISSING", pending["blockers"][0]["detail"])
        self.assertEqual(pending["attempts"], 1)
        # Meeting the obligation through the existing seams lets the SAME
        # consumed run verify: no new intent, no renewed authority, and the
        # bound result evidence is reused, not duplicated.
        self.assertIsNone(self.missions.get(mission_id)["live_authorization_id"])
        self.satisfy_tests_pass(mission_id)
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(result["phase"], "completed_verified")
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertIsNone(self.missions.get(mission_id)["live_authorization_id"])
        evidence = self.missions.get_state(mission_id)["record"]["evidence"]
        self.assertEqual(
            len([e for e in evidence if e["requirement_key"] == "run_result"]), 1)
        self.assertEqual(self.fresh_bridge().status(mission_id)["phase"],
                         "completed_verified")

    def test_K2_missing_required_artifact_is_pending_proof(self):
        # A required artifact must be named by a requirement (the contract
        # schema refuses one nothing references), so ``tests_pass`` names
        # it; the evaluator then lists the proof failure first and the
        # missing required artifact after it.
        value = run_contract(required_artifacts=[{
            "key": "built_package", "role": mission_record.ARTIFACT_ROLE_PRODUCED,
            "expected_content_digest_sha256": "c" * 64}])
        value["requirements"][0]["required_artifact_keys"] = ["built_package"]
        mission_id = self.running(proof_contract=value)
        self.satisfy_tests_pass(mission_id)
        self.assert_pending_proof(
            mission_id, progress_module.PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE,
            first=progress_module.PROBLEM_PROOF_NOT_SATISFIED)

    def test_K3_unresolved_dependency_is_pending_proof(self):
        mission_id = self.running(proof_contract=run_contract(required_dependencies=[{
            "key": "database", "kind": mission_record.DEPENDENCY_KIND_RESOURCE,
            "target": {"form": mission_record.TARGET_FORM_EXACT_RESOURCE,
                       "resource_key": "primary_db"}}]))
        self.satisfy_tests_pass(mission_id)
        self.assert_pending_proof(
            mission_id, progress_module.PROBLEM_DEPENDENCY_UNRESOLVED)

    def test_K4_unmet_readiness_is_pending_then_met_through_the_route(self):
        mission_id = self.running(proof_contract=run_contract(
            required_resource_readiness=[{"resource_key": "ci_runner",
                                          "max_age_seconds": 60}]))
        self.satisfy_tests_pass(mission_id)
        self.assert_pending_proof(
            mission_id, progress_module.PROBLEM_RESOURCE_NOT_READY)
        self.bridge.prove(mission_id, "observe_resource_readiness", {
            "resource_key": "ci_runner", "status": "READY",
            "observed_at": self.clock()})
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["state"],
                         "COMPLETED")

    def test_K5_mission_core_runs_the_existing_evaluator_inside_its_lock(self):
        source = (REPO_ROOT / "mission" / "service.py").read_text()
        body = source.split("def _contract_failures", 1)[1].split(
            "\n    def ", 1)[0]
        self.assertIn("progress_module.closure_failures(", body)
        self.assertIn("store_module.registry_view(document)", body)
        verification = source.split("def record_verification", 1)[1].split(
            "\n    @staticmethod", 1)[0]
        self.assertIn("failures = self._contract_failures(document, mission, now)",
                      verification)
        self.assertIn("return self._record_pending_proof(", verification)
        self.assertIn("return self._run_write(mission_id, context, mutate)",
                      verification)

    def test_K7_status_reports_the_latest_observation_and_keeps_stopped_truth(self):
        # Round-16 BLOCKING 1, as reproduced: RUNNING observed at NOW, the
        # target observed COMPLETE during verification at NOW+7. Status must
        # name the LATER, stopped observation, and keep the stopped truth
        # after the Mission completes, read after a restart each time.
        mission_id = self.running()
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.clock.now = NOW + 7
        self.bridge.verify(mission_id, self.reported())
        pending = self.fresh_bridge().status(mission_id)
        self.assertEqual(pending["phase"], "verification_blocked_pending_proof")
        self.assertEqual(pending["first_observation"],
                         {"at": NOW, "source": "running_transition",
                          "target_stopped": False, "task_status": None})
        self.assertEqual(pending["latest_observation"],
                         {"at": NOW + 7, "source": "verification_pending_proof",
                          "target_stopped": True, "task_status": "COMPLETE"})
        self.assertTrue(pending["target_stopped_observed"])
        self.clock.now = NOW + 9
        self.satisfy_tests_pass(mission_id)
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["state"],
                         "COMPLETED")
        completed = self.fresh_bridge().status(mission_id)
        self.assertEqual(completed["phase"], "completed_verified")
        self.assertTrue(completed["target_stopped_observed"])
        self.assertEqual(completed["latest_observation"],
                         {"at": NOW + 9, "source": "verification",
                          "target_stopped": True, "task_status": "COMPLETE"})
        self.assertEqual(self.mission(mission_id)["run"]["verification"][
            "observed_task_status"], "COMPLETE")

    def test_K9_a_foreign_targets_stop_is_never_attributed_to_this_mission(self):
        # Round-17 BLOCKING 1, as reproduced: the bound target is task-0001;
        # the verification read sees a FOREIGN task COMPLETE and verification
        # records verify_target_identity_mismatch. A fresh-service status
        # must not attribute that stop to this Mission.
        mission_id = self.running()
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(task_id="foreign-task", status="COMPLETE")
        self.clock.now = NOW + 7
        result = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(result["verification"]["failed_conjunct"],
                         "verify_target_identity_mismatch")
        status = self.fresh_bridge().status(mission_id)
        self.assertEqual(status["target_task_id"], TASK_ID)
        self.assertFalse(status["target_stopped_observed"])
        self.assertEqual(status["latest_observation"],
                         {"at": NOW, "source": "running_transition",
                          "target_stopped": False, "task_status": None})
        unattributed = status["unattributed_observation"]
        self.assertEqual(unattributed["at"], NOW + 7)
        self.assertFalse(unattributed["target_identity_established"])
        self.assertEqual(unattributed["task_status"], "COMPLETE")
        # An unsupported read is not attributed either.
        other = self.running(objective="unsupported read")
        self.write_artifacts(other)
        self.observer.raw = raw_observation(status="COMPLETE", extra_diagnostics=[
            {"source": "task", "state": "unavailable", "detail": "x"}])
        self.bridge.verify(other, self.reported())
        status = self.fresh_bridge().status(other)
        self.assertFalse(status["target_stopped_observed"])
        self.assertFalse(status["unattributed_observation"]["observation_supported"])
        self.assertEqual(status["latest_observation"]["source"], "running_transition")

    def test_K8_a_running_target_is_never_reported_stopped(self):
        mission_id = self.running()
        status = self.fresh_bridge().status(mission_id)
        self.assertFalse(status["target_stopped_observed"])
        self.assertEqual(status["latest_observation"]["source"], "running_transition")
        self.assertFalse(status["latest_observation"]["target_stopped"])
        cancelled = self.running(objective="cancelled while running")
        self.bridge.cancel(cancelled)
        status = self.fresh_bridge().status(cancelled)
        self.assertFalse(status["target_stopped_observed"])
        self.assertIsNone(status["latest_observation"]["target_stopped"])

    def test_K6_blocked_attempts_are_bounded(self):
        mission_id = self.running()
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        for attempt in range(1, mission_record.MAX_VERIFICATION_ATTEMPTS + 1):
            result = self.bridge.verify(mission_id, self.reported())
            self.assertEqual(result["pending_proof"]["attempts"], attempt)
        before = self.mission_bytes()
        error = self.refused("mission_bridge_mission_core_refused",
                             self.bridge.verify, mission_id, self.reported())
        self.assertIn("hard bound", error.reason)
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")


class YDeliveryParentTests(Fixture):
    """Round-14 BLOCKING 3: a github_pr-scoped run stays ELIGIBLE as a P1-A6
    delivery parent through the separate, exact delivery contract, while
    general engineering authority stays unavailable. No delivery is
    invoked: only the read-only parent check and the evidence-only receipt
    attestation, over in-memory delivery records."""

    def github(self, **overrides):
        return dict(requested_delivery_target=mission_record.DELIVERY_TARGET_GITHUB_PR,
                    **overrides)

    def digest(self, mission_id):
        return self.missions.get(mission_id)["authorizations"][-1][
            "authorization_digest_sha256"]

    def parent(self, mission_id):
        return mission_parent.parent_mission_authority(
            delivery_record(mission_id, self.digest(mission_id), self.clock()),
            self.missions)

    def general_use(self, mission_id, **kwargs):
        return authorization_module.validate_authorization_use(
            self.missions._store.load(), self.authorization(mission_id),
            mission_id, 1, self.clock(), **kwargs)

    def test_Y1_parent_eligibility_holds_before_running_and_after_completion(self):
        mission_id = self.approved(**self.github())
        self.assertTrue(self.parent(mission_id)["valid"])
        self.dispatch(mission_id)
        self.observer.raw = raw_observation()
        self.bridge.observe(mission_id)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertTrue(self.parent(mission_id)["valid"], self.parent(mission_id))
        self.write_artifacts(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["state"],
                         "COMPLETED")
        parent = self.parent(mission_id)
        self.assertTrue(parent["valid"], parent)
        self.assertEqual(parent["authorized_delivery_targets"], ["github_pr"])
        self.assertIn("no authority", parent["detail"])

    def test_Y2_general_engineering_authority_stays_unavailable(self):
        mission_id, _ = self.verified(**self.github())
        for kwargs in ({"required_actions": ("engineering_change",)},
                       {"required_delivery_target": "github_pr"}, {}):
            with self.subTest(kwargs=kwargs):
                check = self.general_use(mission_id, **kwargs)
                self.assertFalse(check.valid)
                self.assertEqual(check.problem, authorization_module.PROBLEM_NOT_AUTHORIZED)
        self.assertIsNone(self.missions.get(mission_id)["live_authorization_id"])
        self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                          self.missions.record_run_intent,
                          *self.intent_args(mission_id))

    def test_Y3_receipt_attestation_still_records_after_completion(self):
        mission_id, _ = self.verified(**self.github())
        step = delivery_authorization.STEPS[0]
        record_ = with_receipt(
            delivery_record(mission_id, self.digest(mission_id), self.clock()),
            step, delivery_authorization.RECEIPT_SUCCEEDED, self.clock())
        operation = self.missions.mint_state_operation_id(AUTHENTICATED)
        result = mission_parent.attest_validated_receipt(
            record_, step, self.missions, operation,
            self.missions.get_state(mission_id)["sequence"], AUTHENTICATED)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["mission_id"], mission_id)
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        # The exception is the receipt attestation alone: proof writes stay shut.
        self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                          self.state_op, mission_id, self.missions.record_claim,
                          "tests_pass", "a late claim")

    def test_Y4_blocked_or_cancelled_runs_are_not_eligible(self):
        cancelled = self.running(**self.github(objective="cancelled"))
        self.bridge.cancel(cancelled)
        blocked = self.running(**self.github(objective="blocked"))
        self.write_artifacts(blocked)
        self.satisfy_tests_pass(blocked)
        self.observer.raw = raw_observation()
        self.assertEqual(self.bridge.verify(blocked, self.reported())["state"],
                         "BLOCKED")
        for mission_id in (cancelled, blocked):
            with self.subTest(state=self.mission(mission_id)["state"]):
                parent = self.parent(mission_id)
                self.assertFalse(parent["valid"])
                self.assertEqual(parent["problem"],
                                 authorization_module.PROBLEM_NOT_AUTHORIZED)

    def test_Y5_engineering_approval_without_github_pr_confers_no_delivery(self):
        running = self.running(objective="no delivery target")
        parent = self.parent(running)
        self.assertFalse(parent["valid"])
        self.assertEqual(parent["problem"],
                         authorization_module.PROBLEM_TARGET_OUTSIDE_SCOPE)
        completed, result = self.verified(objective="no delivery, completed")
        self.assertFalse(self.parent(completed)["valid"])
        self.assertEqual(result["delivery_authority"], "none")
        self.assertFalse(result["delivered"])


class TTerminalGuardTests(Fixture):
    def test_T1_no_progress_result_proof_or_decision_after_terminal(self):
        mission_id = self.approved()
        self.bridge.cancel(mission_id)
        self.assertEqual(self.mission(mission_id)["state"], "CANCELLED")
        before = self.mission_bytes()
        authorization_id = self.authorization(mission_id)
        operation = self.missions.mint_state_operation_id(AUTHENTICATED)
        for method, args in (
            (self.missions.activate_proof_contract, (operation, 0, AUTHENTICATED)),
            (self.missions.record_claim, (operation, 0, "tests_pass", "a claim",
                                          AUTHENTICATED)),
            (self.missions.submit_evidence, (
                operation, 0, "run_result", "VERIFICATION_RECORD", "f" * 64, [],
                AUTHENTICATED)),
            (self.missions.record_run_intent, self.intent_args(mission_id)[1:]),
            (self.missions.record_run_receipt, (TASK_ID, "start_result", None,
                                                self.context)),
            (self.missions.record_observed_running, (TASK_ID, False, self.context)),
            (self.missions.record_verification, ({}, None, None, None, None,
                                                 "COMPLETE", self.context)),
            (self.missions.record_run_stop, ("reconcile_no_match", self.context)),
            (self.missions.record_pause, (authorization_id, self.context)),
            (self.missions.record_resume, (authorization_id, self.context)),
            (self.missions.record_cancel, ("cancelled_before_intent",
                                           authorization_id, self.context)),
        ):
            with self.subTest(method=method.__name__):
                self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                                  method, mission_id, *args)
        self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                          self.missions.edit, mission_id, 1,
                          run_request(objective="late edit"),
                          self.missions.mint_decision_id(AUTHENTICATED),
                          AUTHENTICATED)
        after = json.loads(self.mission_bytes())["missions"][mission_id]
        self.assertEqual(after, json.loads(before)["missions"][mission_id])

    def test_T2_late_receipt_after_cancel_refused_in_di_records(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        self.dispatch(mission_id)
        self.bridge.cancel(mission_id)
        self.core_refused(mission_record.PROBLEM_MISSION_TERMINAL,
                          self.missions.record_run_receipt, mission_id, TASK_ID,
                          "start_result", None, self.context)

    def test_T3_decisions_refused_once_a_run_is_recorded(self):
        mission_id = self.approved()
        self.bridge.pause(mission_id)
        self.core_refused(mission_record.PROBLEM_RUN_RECORDED,
                          self.missions.edit, mission_id, 1,
                          run_request(objective="edit under a run"),
                          self.missions.mint_decision_id(AUTHENTICATED),
                          AUTHENTICATED)

    def test_T4_a_stored_run_state_without_its_chain_is_malformed(self):
        mission_id = self.running()
        document = json.loads(self.mission_bytes())
        del document["missions"][mission_id]["lifecycle"]
        with self.assertRaises(mission_store.MissionStoreError):
            mission_store.validate_document(document, "synthetic")


class AAuthorityTests(Fixture):
    """Every core run and control method accepts only an authenticated
    principal kind, and control binds to the Mission's own authorization."""

    def core_methods(self, mission_id):
        authorization_id = self.authorization(mission_id)
        return (
            (self.missions.record_run_intent, self.intent_args(mission_id)[1:-1]),
            (self.missions.record_run_receipt, (TASK_ID, "start_result", None)),
            (self.missions.record_observed_running, (TASK_ID, False)),
            (self.missions.record_run_stop, ("reconcile_no_match",)),
            (self.missions.record_verification, ({}, None, None, None, None,
                                                 "COMPLETE")),
            (self.missions.record_pause, (authorization_id,)),
            (self.missions.record_resume, (authorization_id,)),
            (self.missions.record_cancel, ("cancelled_before_intent",
                                           authorization_id)),
            (self.missions.complete_cancel, ("not_applicable", "nothing_started",
                                             authorization_id)),
        )

    def test_A1_unauthenticated_and_operator_attested_kinds_are_refused(self):
        mission_id = self.approved()
        before = self.mission_bytes()
        for context in (surface_module.LOCAL_CALLER_CONTEXT,
                        surface_module.OPERATOR_ATTESTED_CONTEXT):
            for method, args in self.core_methods(mission_id):
                with self.subTest(kind=context.principal_kind,
                                  method=method.__name__):
                    with self.assertRaises(mission_record.MissionError):
                        method(mission_id, *(args + (context,)))
        self.assertEqual(self.mission_bytes(), before)

    def test_A2_cross_mission_pause_and_cancel_are_refused(self):
        a = self.running(objective="mission A")
        b = self.approved(objective="mission B")
        before = self.mission_bytes()
        b_authorization = self.authorization(b)
        for method, args in (
            (self.missions.record_pause, (b_authorization,)),
            (self.missions.record_cancel, ("cancelled_after_observed_running",
                                           b_authorization)),
        ):
            with self.subTest(method=method.__name__):
                self.core_refused(mission_record.PROBLEM_RUN, method, a,
                                  *(args + (self.context,)))
        for operation in (self.bridge.pause, self.bridge.cancel, self.bridge.resume):
            with self.subTest(operation=operation.__name__):
                self.refused("mission_bridge_authorization_mismatch", operation,
                             a, authorization_id=b_authorization)
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(a)["state"], "RUNNING")
        self.assertEqual(self.mission(b)["state"], "AUTHORIZED")
        self.assertEqual(self.ownership.reaped, [])

    def test_A3_another_missions_withdrawal_capability_controls_nothing(self):
        a_out = self.surface.submit(run_request(objective="pending A"))
        b_out = self.surface.submit(run_request(objective="pending B"))
        before = self.mission_bytes()
        with self.assertRaises(surface_module.LocalRequestRefusal):
            self.surface.cancel(a_out["request_ref"], b_out["control_capability"])
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(a_out["mission_id"])["state"],
                         "AWAITING_DECISION")


class PPauseTests(Fixture):
    def test_P1_pause_gates_dispatch_until_explicit_resume(self):
        mission_id = self.approved()
        paused = self.bridge.pause(mission_id)
        self.assertTrue(paused["paused"])
        self.assertFalse(paused["external_work_suspended"])
        self.assertIn("NOT suspended", paused["statement"])
        error = self.refused("mission_bridge_paused", self.dispatch, mission_id)
        self.assertIn("External in-flight work is NOT suspended", error.reason)
        self.assertEqual(self.spawn.calls, [])
        self.assertIsNone(self.mission(mission_id)["run"]["intent"])
        self.assertFalse(self.bridge.resume(mission_id)["paused"])
        self.dispatch(mission_id)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_P2_pause_while_running_stops_only_di_progression(self):
        mission_id = self.running()
        self.bridge.pause(mission_id)
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.refused("mission_bridge_paused", self.bridge.verify, mission_id,
                     self.reported())
        self.core_refused(mission_record.PROBLEM_RUN_PAUSED,
                          self.missions.record_verification, mission_id,
                          {}, None, None, None, None, "COMPLETE", self.context)
        self.core_refused(mission_record.PROBLEM_RUN_PAUSED,
                          self.missions.record_pause, mission_id,
                          self.authorization(mission_id), self.context)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertEqual(self.ownership.reaped, [])
        self.assertEqual(len(self.spawn.calls), 1)
        self.bridge.resume(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["state"],
                         "COMPLETED")

    def test_P3_cancel_still_applies_while_paused(self):
        mission_id = self.running()
        self.bridge.pause(mission_id)
        self.assertEqual(self.bridge.cancel(mission_id)["state"], "CANCELLED")

    def test_P4_no_suspend_mechanism_is_invented(self):
        source = (REPO_ROOT / "target_runtime" / "mission_bridge.py").read_text()
        tree = ast.parse(source)
        names = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ("SIGSTOP", "SIGCONT", "SIGTSTP", "kill", "killpg"):
            self.assertNotIn(forbidden, names)


class CCancelTests(Fixture):
    def test_C1_before_intent(self):
        mission_id = self.approved()
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["achieved"], "cancelled_before_intent")
        self.assertEqual(result["quiescence"], "nothing_started")
        self.assertFalse(result["hold"])
        self.assertEqual(self.spawn.calls, [])

    def test_C2_after_intent_target_unknown_reports_hold(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        self.dispatch(mission_id)
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["achieved"], "cancelled_after_intent_target_unknown")
        self.assertEqual(result["quiescence"], "unproven")
        self.assertEqual(result["control"], "unavailable_no_owned_group")
        self.assertTrue(result["hold"])
        self.assertFalse(result["external_quiescence_claimed"])
        self.assertTrue(self.make_bridge().status(mission_id)["hold"])

    def test_C3_after_observed_running_without_an_owned_group_holds(self):
        mission_id = self.running()
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["achieved"], "cancelled_after_observed_running")
        self.assertEqual(result["control"], "unavailable_no_owned_group")
        self.assertEqual(result["quiescence"], "unproven")
        self.assertTrue(result["hold"])
        self.assertEqual(self.ownership.reaped, [])

    def test_C4_owned_group_is_reaped_through_the_ownership_seam(self):
        self.ownership.owned = {4242}
        self.spawn.group = 4242
        mission_id = self.running()
        self.assertEqual(self.mission(mission_id)["run"]["receipt"][
            "owned_process_group"], 4242)
        result = self.bridge.cancel(mission_id)
        self.assertEqual(self.ownership.reaped, [(4242, "/owner-scope")])
        self.assertEqual(result["control"], "owned_group_reaped")
        self.assertEqual(result["quiescence"], "owned_group_reaped")
        self.assertFalse(result["hold"])

    def test_C5_unverified_ownership_is_reported_not_acted_on(self):
        self.ownership.owned = {4242}
        self.spawn.group = 4242
        mission_id = self.running()
        self.ownership.owned = set()
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["control"], "refused_ownership_unverified")
        self.assertEqual(self.ownership.reaped, [])
        self.assertTrue(result["hold"])
        self.spawn.group = 777
        other = self.running(objective="unowned group")
        self.assertIsNone(self.mission(other)["run"]["receipt"]["owned_process_group"])

    def test_C6_failed_reap_is_unproven(self):
        self.ownership.owned = {4242}
        self.ownership.verdict = ownership_module.REFUSED_NOT_IN_LEDGER
        self.spawn.group = 4242
        mission_id = self.running()
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["control"], "failed_group_still_alive")
        self.assertTrue(result["hold"])

    def test_C7_after_target_terminated(self):
        mission_id = self.running()
        self.observer.raw = raw_observation(status="COMPLETE")
        result = self.bridge.cancel(mission_id)
        self.assertEqual(result["achieved"], "cancelled_after_target_terminated")
        self.assertEqual(result["quiescence"], "task_observed_stopped")
        self.assertFalse(result["hold"])

    def test_C8_interrupted_cancel_completes_without_resignalling(self):
        self.ownership.owned = {4242}
        self.spawn.group = 4242
        mission_id = self.running()
        real = self.missions.complete_cancel

        def crash(*args):
            raise OSError("synthetic: crashed before the cancel completed")

        self.missions.complete_cancel = crash
        with self.assertRaises(OSError):
            self.bridge.cancel(mission_id)
        self.missions.complete_cancel = real
        self.assertEqual(len(self.ownership.reaped), 1)
        self.assertEqual(self.mission(mission_id)["state"], "CANCELLED")
        result = self.make_bridge().cancel(mission_id)
        self.assertEqual(result["control"], "interrupted_not_resignalled")
        self.assertEqual(result["quiescence"], "unproven")
        self.assertTrue(result["hold"])
        self.assertEqual(len(self.ownership.reaped), 1)
        self.refused("mission_bridge_wrong_state", self.bridge.cancel, mission_id)


class XStatusAndSeparationTests(Fixture):
    def test_X1_status_comes_from_durable_records_alone(self):
        mission_id = self.running()
        status = bridge_module.MissionBridge(
            mission_service.MissionService(
                mission_store.MissionStore(self.state), self.clock),
            "/unrelated", self.clock).status(mission_id)
        self.assertEqual(status["state"], "RUNNING")
        self.assertEqual(status["phase"], "running_observed")
        self.assertEqual(status["run"]["receipt"]["task_id"], TASK_ID)

    def test_X2_delivery_stays_separate_after_a_completed_run(self):
        mission_id, result = self.verified()
        self.assertEqual(result["delivery_authority"], "none")
        self.assertFalse(result["delivered"])
        source = (REPO_ROOT / "target_runtime" / "mission_bridge.py").read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [
                    getattr(node, "module", None) or ""]
                for name in names:
                    self.assertFalse(name.startswith("pr_delivery"), name)
        self.assertIsNone(self.missions.get(mission_id)["live_authorization_id"])

    def test_X3_operator_attested_labels_carry_through(self):
        mission_id = self.running()
        authorization = self.missions.get(mission_id)["authorizations"][0]
        self.assertEqual(authorization["human_principal"]["proof"],
                         "operator_attested_not_independently_verified")


class ZDeliveredDerivedTests(Fixture):
    """B1: delivered is DERIVED from attested P1-A6 receipts, never a
    constant; engineering approval and verification alone stay
    no-delivery. Injected receipt fixtures only; no delivery invoked."""

    github = YDeliveryParentTests.github
    digest = YDeliveryParentTests.digest

    def attest(self, mission_id, step, state):
        record_ = with_receipt(
            delivery_record(mission_id, self.digest(mission_id), self.clock()),
            step, state, self.clock())
        operation = self.missions.mint_state_operation_id(AUTHENTICATED)
        result = mission_parent.attest_validated_receipt(
            record_, step, self.missions, operation,
            self.missions.get_state(mission_id)["sequence"], AUTHENTICATED)
        self.assertTrue(result["valid"], result)

    def test_Z1_absent_receipt_is_not_delivered(self):
        mission_id, result = self.verified(**self.github())
        self.assertFalse(result["delivered"])
        status = self.bridge.status(mission_id)
        self.assertTrue(status["engineering_verified"])
        self.assertFalse(status["delivered"])
        self.assertEqual(status["delivery"]["receipts"], [])
        self.assertEqual(status["delivery_authority"], "none")

    def test_Z2_a_validated_final_receipt_is_reported_delivered(self):
        mission_id, _ = self.verified(**self.github())
        self.attest(mission_id, "COMMIT", delivery_authorization.RECEIPT_SUCCEEDED)
        self.assertFalse(self.bridge.status(mission_id)["delivered"])
        self.attest(mission_id, "PR_CREATE", delivery_authorization.RECEIPT_SUCCEEDED)
        status = self.bridge.status(mission_id)
        self.assertTrue(status["delivered"])
        self.assertEqual(status["delivery_authority"], "none")
        self.assertEqual(self.bridge.result(mission_id)["delivered"], True)
        self.assertEqual(surface_module.DELIVERED_STEP,
                         delivery_authorization.STEPS[-1])

    def test_Z3_a_final_receipt_that_did_not_succeed_is_not_delivered(self):
        mission_id, _ = self.verified(**self.github())
        self.attest(mission_id, "PR_CREATE", delivery_authorization.RECEIPT_EXECUTING)
        status = self.bridge.status(mission_id)
        self.assertFalse(status["delivered"])
        self.assertEqual(status["delivery"]["receipts"][0]["completed_effect"], False)

    def test_Z4_approval_or_verification_alone_never_reports_delivered(self):
        approved = self.approved(**self.github(objective="approved only"))
        self.assertFalse(self.bridge.status(approved)["delivered"])
        verified, _ = self.verified(objective="verified, no delivery target")
        self.assertFalse(self.bridge.status(verified)["delivered"])


class GSpawnCompatibilityTests(Fixture):
    """C: the request DI builds is checked SEMANTICALLY against the
    production entry point's OWN validator and constraint set (herd's
    ``orchestrator._validate_request``, ``_ALLOWED_FIELDS`` and preset
    table), with policy and model preservation. Nothing is spawned and no
    environment is probed."""

    def request(self):
        mission_id = self.approved()
        derived = self.bridge._derive(self.missions.get(mission_id),
                                      self.ws(mission_id))
        return mission_id, derived, derived.spawn_request()

    def test_G1_the_production_validator_accepts_the_request_unchanged(self):
        from herdr import orchestrator
        mission_id, derived, request_ = self.request()
        clean = orchestrator._validate_request(dict(request_))
        self.assertEqual(clean, request_)
        self.assertLessEqual(set(request_), orchestrator._ALLOWED_FIELDS)
        # The child records the task as its task.json description after the
        # same strip, so the association prefix survives verbatim.
        self.assertTrue(clean["task"].strip().startswith(bridge_module.handoff_prefix(
            mission_id, derived.revision, derived.proposal_digest_sha256)))
        # The validator is real: it rejects what the entry point rejects.
        for bad in (dict(request_, unknown=True), dict(request_, task="  "),
                    dict(request_, target_repo="relative/path")):
            with self.assertRaises(ValueError):
                orchestrator._validate_request(bad)

    def test_G2_policy_and_model_configuration_are_preserved(self):
        import copy as copy_module
        from herdr import config as herdr_config
        _, _, request_ = self.request()
        presets_before = copy_module.deepcopy(herdr_config.PRESETS)
        # DI carries no rules, policy, task policy, test command, force or
        # drill: the child Herdr's own policy resolution applies untouched.
        self.assertFalse(set(request_) & {"rules", "policy", "task_policy",
                                          "test_command", "force",
                                          "rejection_drill"})
        self.assertIn(request_["preset"], herdr_config.PRESETS)
        roles = herdr_config.apply_preset_to_config({}, request_["preset"])["roles"]
        self.assertEqual(roles, herdr_config.PRESETS[request_["preset"]]["roles"])
        self.assertEqual(herdr_config.PRESETS, presets_before)

    def test_G3_the_default_is_the_real_entry_point_not_a_recorder(self):
        import inspect
        from herdr import orchestrator
        from local_request import cli as cli_module
        self.assertIs(dispatch_module.execute_spawn_request,
                      orchestrator.execute_spawn_request)
        self.assertIn("return execute_spawn_request(parent_repo, request)",
                      inspect.getsource(dispatch_module.production_spawn))
        built = cli_module.build_bridge(self.missions, "/control-repo", self.clock)
        self.assertIs(built._spawn, dispatch_module.production_spawn)
        self.assertNotIsInstance(built._spawn, SpawnRecorder)


class QProveTests(Fixture):
    """A1: the route satisfies approved obligations through the EXISTING
    seams only, one explicit step at a time; nothing is auto-accepted."""

    def test_Q1_a_submission_alone_never_satisfies_the_contract(self):
        mission_id = self.running()
        self.write_artifacts(mission_id)
        submitted = self.bridge.prove(mission_id, "submit_evidence", {
            "requirement_key": "tests_pass", "kind": "VERIFICATION_RECORD",
            "content_digest_sha256": TESTS_PASS_DIGEST, "artifact_ids": []})
        evidence_id = submitted["proof_operation"]["outcome"]["evidence_id"]
        self.assertFalse(submitted["proof_operation"]["outcome"]["accepted"])
        self.observer.raw = raw_observation(status="COMPLETE")
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["phase"],
                         "verification_blocked_pending_proof")
        # Acceptance needs the submitted digest exactly.
        self.refused("mission_bridge_mission_core_refused", self.bridge.prove,
                     mission_id, "accept_evidence",
                     {"evidence_id": evidence_id, "content_digest_sha256": "0" * 64})
        self.bridge.prove(mission_id, "accept_evidence", {
            "evidence_id": evidence_id, "content_digest_sha256": TESTS_PASS_DIGEST})
        self.assertEqual(self.bridge.verify(mission_id, self.reported())["state"],
                         "COMPLETED")

    def test_Q2_only_existing_seams_with_their_exact_arguments(self):
        mission_id = self.running()
        before = self.mission_bytes()
        for operation, arguments in (
            ("complete_successfully", {}),
            ("accept_all", {}),
            ("submit_evidence", {"requirement_key": "tests_pass"}),
            ("accept_evidence", {"evidence_id": "mv-" + "0" * 32,
                                 "content_digest_sha256": "0" * 64, "auto": True}),
        ):
            with self.subTest(operation=operation):
                self.refused("mission_bridge_proof_operation", self.bridge.prove,
                             mission_id, operation, arguments)
        self.assertEqual(self.mission_bytes(), before)

    def test_Q3_proof_steps_are_refused_while_paused_or_terminal(self):
        mission_id = self.running()
        self.bridge.pause(mission_id)
        self.refused("mission_bridge_paused", self.bridge.prove, mission_id,
                     "record_claim", {"requirement_key": "tests_pass",
                                      "statement": "a claim"})
        self.bridge.resume(mission_id)
        self.bridge.cancel(mission_id)
        self.refused("mission_bridge_mission_core_refused", self.bridge.prove,
                     mission_id, "record_claim",
                     {"requirement_key": "tests_pass", "statement": "late"})


class LCliRouteTests(Fixture):
    """A + B: the run is drivable through direquest's own CLI (the existing
    ``local_request.cli.main``), for the request's own Mission, with
    recorders injected for every effect, and status is truthful across the
    lifecycle from durable records alone, including after a restart."""

    def cli(self, argv, stdin="", factory=True):
        import io
        from local_request import cli as cli_module
        out = io.StringIO()
        code = cli_module.main(
            ["--state-dir", self.state] + argv, io.StringIO(stdin), out,
            clock=self.clock,
            bridge_factory=(self.factory if factory else None))
        return code, json.loads(out.getvalue())

    def factory(self, missions, control_repo, clock):
        return bridge_module.MissionBridge(
            missions, control_repo, clock, spawn_fn=self.spawn,
            observer_fn=self.observer, spawn_records_fn=self.records,
            transport=self.transport,
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope")

    def approved_via_cli(self, **overrides):
        code, proposed = self.cli(["propose"], json.dumps(run_request(**overrides)))
        self.assertEqual(code, 0, proposed)
        ref = proposed["request_ref"]
        code, status = self.cli(["status", ref], factory=False)
        self.assertEqual(status["run"]["phase"], "not_started")
        _, presented = self.cli(["present", ref])
        argv = ["attest-approval", ref, "--mission-id", presented["mission_id"],
                "--revision", "1", "--proposal-digest",
                presented["proposal_digest_sha256"],
                "--expires-at", str(self.clock() + 600), "--relay-ref", "r"]
        for scope in presented["approved_action_scope"]:
            argv += ["--action-scope", scope]
        for target in presented["approved_delivery_targets"]:
            argv += ["--delivery-target", target]
        code, attested = self.cli(argv, "approved")
        self.assertEqual(code, 0, attested)
        return ref, presented["mission_id"]

    def run_cli(self, ref, command, *extra, stdin=""):
        return self.cli([command, ref, "--control-repo", "/control-repo"]
                        + list(extra), stdin)

    def status(self, ref):
        code, status = self.cli(["status", ref], factory=False)
        self.assertEqual(code, 0, status)
        return status

    def test_L1_dispatch_observe_prove_verify_result_through_the_cli(self):
        ref, mission_id = self.approved_via_cli()
        self.assertEqual(self.status(ref)["run"]["phase"], "authorized_not_dispatched")
        self.assertEqual(self.status(ref)["dispatch"], "none")
        code, out = self.run_cli(ref, "dispatch", "--workspace", self.ws(mission_id))
        self.assertEqual((code, out["phase"]), (0, "dispatched_not_yet_observed"))
        self.assertEqual(self.status(ref)["dispatch"], "recorded")
        self.assertEqual(self.run_cli(ref, "observe")[1]["phase"], "running_observed")
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        reported = json.dumps(self.reported())
        code, out = self.run_cli(ref, "verify", stdin=reported)
        self.assertEqual((code, out["phase"]),
                         (0, "verification_blocked_pending_proof"))
        run = self.status(ref)["run"]
        self.assertEqual(run["phase"], "verification_blocked_pending_proof")
        self.assertEqual(run["pending_proof"]["blockers"][0]["code"],
                         progress_module.PROBLEM_PROOF_NOT_SATISFIED)
        code, out = self.run_cli(ref, "prove", "submit_evidence", stdin=json.dumps({
            "requirement_key": "tests_pass", "kind": "VERIFICATION_RECORD",
            "content_digest_sha256": TESTS_PASS_DIGEST, "artifact_ids": []}))
        self.assertEqual(code, 0, out)
        evidence_id = out["proof_operation"]["outcome"]["evidence_id"]
        # Submitted is not accepted: verify stays pending.
        self.assertEqual(self.run_cli(ref, "verify", stdin=reported)[1]["phase"],
                         "verification_blocked_pending_proof")
        code, out = self.run_cli(ref, "prove", "accept_evidence", stdin=json.dumps({
            "evidence_id": evidence_id, "content_digest_sha256": TESTS_PASS_DIGEST}))
        self.assertEqual(code, 0, out)
        code, out = self.run_cli(ref, "verify", stdin=reported)
        self.assertEqual((code, out["state"]), (0, "COMPLETED"))
        code, out = self.run_cli(ref, "result")
        self.assertTrue(out["recoverable"])
        self.assertEqual(out["result_text"], RESULT_TEXT)
        # A restarted process with no caller context reads the same truth.
        run = self.status(ref)["run"]
        self.assertEqual(run["phase"], "completed_verified")
        self.assertTrue(run["engineering_verified"])
        self.assertEqual(run["mission_state_progress"], "IN_PROGRESS")
        self.assertIn("does not close Mission State progress", run["progress_note"])
        self.assertFalse(run["delivery"]["delivered"])
        self.assertEqual(run["delivery_authority"], "none")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_L2_hold_then_reconcile_through_the_cli(self):
        ref, mission_id = self.approved_via_cli()
        self.spawn.raises = "after_child"
        code, out = self.run_cli(ref, "dispatch", "--workspace", self.ws(mission_id))
        self.assertEqual(code, 0)
        self.assertTrue(out["hold"])
        run = self.status(ref)["run"]
        self.assertEqual((run["phase"], run["hold"]),
                         ("hold_intent_outcome_unknown", True))
        self.assertIn("HOLD", run["hold_statement"])
        self.spawn.raises = None
        self.assertEqual(self.run_cli(ref, "reconcile")[1]["phase"],
                         "dispatched_not_yet_observed")
        # Reconciliation binds the child but leaves the run AUTHORIZED: a
        # verify now is refused, so the documented recovery needs a SECOND
        # observe after reconcile (round-16 non-blocking 3).
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        reported = json.dumps(self.reported())
        code, out = self.run_cli(ref, "verify", stdin=reported)
        self.assertEqual((code, out["problem"]), (3, "mission_bridge_wrong_state"))
        self.assertEqual(self.run_cli(ref, "observe")[1]["phase"], "running_observed")
        code, out = self.run_cli(ref, "prove", "submit_evidence", stdin=json.dumps({
            "requirement_key": "tests_pass", "kind": "VERIFICATION_RECORD",
            "content_digest_sha256": TESTS_PASS_DIGEST, "artifact_ids": []}))
        evidence_id = out["proof_operation"]["outcome"]["evidence_id"]
        self.run_cli(ref, "prove", "accept_evidence", stdin=json.dumps({
            "evidence_id": evidence_id, "content_digest_sha256": TESTS_PASS_DIGEST}))
        code, out = self.run_cli(ref, "verify", stdin=reported)
        self.assertEqual((code, out["state"]), (0, "COMPLETED"))
        self.assertEqual(self.status(ref)["run"]["phase"], "completed_verified")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_L3_paused_cancelled_and_blocked_are_reported_exactly(self):
        ref, mission_id = self.approved_via_cli()
        self.run_cli(ref, "dispatch", "--workspace", self.ws(mission_id))
        self.run_cli(ref, "observe")
        self.run_cli(ref, "pause")
        run = self.status(ref)["run"]
        self.assertTrue(run["paused"])
        self.assertFalse(run["external_work_suspended"])
        self.assertIn("External in-flight work is NOT suspended",
                      run["pause_statement"])
        code, out = self.run_cli(ref, "cancel-run")
        self.assertEqual(code, 0, out)
        run = self.status(ref)["run"]
        self.assertEqual(run["phase"], "cancelled")
        self.assertEqual(run["outcome"], "cancelled_after_observed_running")
        self.assertEqual(run["cancel"]["quiescence"], "unproven")
        self.assertTrue(run["hold"])
        ref2, mission2 = self.approved_via_cli(objective="blocked one")
        self.run_cli(ref2, "dispatch", "--workspace", self.ws(mission2))
        self.run_cli(ref2, "observe")
        self.write_artifacts(mission2)
        self.observer.raw = raw_observation(status="ERROR")
        self.run_cli(ref2, "verify", stdin=json.dumps(self.reported()))
        run = self.status(ref2)["run"]
        self.assertEqual((run["phase"], run["outcome"]),
                         ("blocked", "verify_target_not_succeeded"))
        self.assertFalse(run["engineering_verified"])

    def test_L4_the_route_is_bound_to_the_requests_own_mission(self):
        ref, mission_id = self.approved_via_cli()
        code, out = self.run_cli("lr-" + "0" * 32, "dispatch", "--workspace",
                                 self.ws(mission_id))
        self.assertEqual(code, 3)
        self.assertEqual(out["problem"], "local_request_unknown_request")
        code, out = self.cli(["dispatch", mission_id, "--control-repo",
                              "/control-repo", "--workspace", self.ws(mission_id)])
        self.assertEqual(code, 3)
        self.assertEqual(self.spawn.calls, [])
        code, out = self.cli(["dispatch", ref, "--control-repo", "relative",
                              "--workspace", self.ws(mission_id)])
        self.assertEqual(code, 2)

    def test_L5_the_cli_reaches_the_real_bridge_without_invoking_it(self):
        ref, mission_id = self.approved_via_cli()
        # No injected factory: the CLI builds the REAL bridge (production
        # defaults); an unknown request refuses before any effect.
        code, out = self.cli(["dispatch", "lr-" + "1" * 32, "--control-repo",
                              "/control-repo", "--workspace", self.ws(mission_id)],
                             factory=False)
        self.assertEqual(code, 3)
        self.assertEqual(out["problem"], "local_request_unknown_request")
        self.assertEqual(self.spawn.calls, [])
        refused_bridge = self.cli(["observe", ref, "--control-repo",
                                   "/control-repo"], factory=False)
        self.assertEqual(refused_bridge[0], 3)
        self.assertEqual(refused_bridge[1]["problem"], "mission_bridge_wrong_state")


if __name__ == "__main__":
    unittest.main()
