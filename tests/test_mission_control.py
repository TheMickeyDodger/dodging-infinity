"""Focused tests for Task 8 slice S-II: the canonical one-load Mission
snapshot and its durable cursor (``MissionService.snapshot``), the
coordination observation adapter (``mission_control.observation_adapter``)
and the pure, lock-free status read (``mission_control.status``).

Every test drives the REAL MissionService over a real temporary store
(the Task 5 fixture), real workflow / delivery / coordination stores on
disk, real child processes for lock contention, and coordination's own
``classify`` and attention projection. Effect counts are read from disk.

Sections: A snapshot, B durable cursor, C adapter (incl. the Supervisor
time-dependent-facts and contract-audit constraints), D status (lock
contention, atomic replacement, absent vs unavailable, no writes),
E static pins on the two new modules.
"""

import ast
import contextlib
import copy
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from coordination import attention as coordination_attention  # noqa: E402
from coordination import observation as coordination_observation  # noqa: E402
from coordination import record as coordination_record  # noqa: E402
from coordination import store as coordination_store  # noqa: E402
from mission import authorization as mission_authorization  # noqa: E402
from mission import observation as mission_observation  # noqa: E402
from mission import progress as mission_progress  # noqa: E402
from mission import reconciliation as mission_reconciliation  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import state as mission_state  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_control import observation_adapter as adapter  # noqa: E402
from mission_control import status as status_module  # noqa: E402
from pr_delivery import store as delivery_store  # noqa: E402
from workflow_authority import atomic as wa_atomic  # noqa: E402
from workflow_authority import store as workflow_store  # noqa: E402

from test_mission_observation import Counting, materialize  # noqa: E402
from test_mission_state import ServiceStateFixture, HEX_A, contract  # noqa: E402

AUTHORITY_WORDS = ("authorization_digest", "decision_id", "consumed_by",
                   "authority_ledger", "expires_at", "revoked", "reserved_at")
STATUS_DEADLINE_SECONDS = 5.0


def inventory(root):
    """Recursive path inventory: relative path -> (kind, mode, size,
    mtime_ns, sha256 of a file's bytes)."""
    found = {}
    if not os.path.exists(root):
        return found
    for directory, names, files in os.walk(root):
        for name in names + files:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            digest = None
            if stat.S_ISREG(info.st_mode):
                with open(path, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
            found[os.path.relpath(path, root)] = (
                stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode),
                info.st_size, info.st_mtime_ns, digest)
    return found


def write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)


def running_as_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


class S2Fixture(ServiceStateFixture):

    def setUp(self):
        super(S2Fixture, self).setUp()
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.workflow_dir = os.path.join(self.base.name, "workflows")
        self.delivery_dir = os.path.join(self.base.name, "delivery")
        self.coordination_dir = os.path.join(self.base.name, "coordination")

    # -- readers ---------------------------------------------------------

    def snapshot(self, mission_id):
        return self.service.snapshot(mission_id)

    def cursor(self, mission_id):
        return int(self.snapshot(mission_id)["durable_cursor"])

    def source(self, service=None):
        return adapter.MissionSnapshotSource(service or self.service)

    def outcome(self, mission_id, service=None):
        return self.source(service).observe(mission_id)

    def observation(self, mission_id, service=None):
        outcome = self.outcome(mission_id, service)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_OBSERVED,
                         outcome.problem)
        return outcome.observation

    def point(self, mission_id):
        return coordination_observation.observation_point(
            self.observation(mission_id))

    def classify(self, observation, floor=None, now=None):
        return coordination_observation.classify(
            coordination_observation.ObservationOutcome.observed(observation),
            self.clock() if now is None else now, observation.mission_id, floor)

    def floor_of(self, observation):
        point = coordination_observation.observation_point(observation)
        return coordination_observation.HighWaterMark(
            revision=point["revision"], cursor=point["cursor"], point=point)

    def status(self, mission_id, **kwargs):
        return status_module.read_status(self.service, mission_id, **kwargs)

    def full_status(self, mission_id, **kwargs):
        return status_module.read_status(
            self.service, mission_id, workflow_directory=self.workflow_dir,
            delivery_directory=self.delivery_dir,
            coordination_directory=self.coordination_dir, **kwargs)

    def assert_strictly_advanced(self, mission_id, before, label):
        after = self.cursor(mission_id)
        self.assertGreater(after, before, label)
        return after


# ====================================================================
# A. The one-load snapshot
# ====================================================================


class ASnapshotTests(S2Fixture):

    def test_A1_snapshot_equals_the_three_reads_on_one_document(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self.call("record_claim", mission_id, "tests_pass", "the suite passes")
        snapshot = self.snapshot(mission_id)
        self.assertEqual(snapshot["record"], self.service.get(mission_id))
        self.assertEqual(snapshot["state"], self.service.get_state(mission_id))
        self.assertEqual(snapshot["observation"], self.service.observe(mission_id))
        self.assertEqual(snapshot["evaluated_at"], self.clock())
        self.assertEqual(snapshot["mission_id"], mission_id)
        coordination_record.require_cursor(snapshot["durable_cursor"], "cursor")
        self.assertEqual(sorted(snapshot), ["durable_cursor", "evaluated_at",
                                            "mission_id", "observation",
                                            "record", "state"])

    def test_A2_snapshot_loads_once_and_neither_locks_saves_nor_mints(self):
        mission_id = self.ready_mission(required_dependencies=[])
        minted = []
        loads = []
        store = mission_store.MissionStore(self.directory)
        real_read = store.read

        def counted_read():
            loads.append(1)
            return real_read()

        store.read = counted_read
        store.load = lambda: (_ for _ in ()).throw(
            AssertionError("snapshot reads through the owner's read(), never load"))
        store.lock = lambda: (_ for _ in ()).throw(
            AssertionError("snapshot must never take the store lock"))
        store.save = lambda document: (_ for _ in ()).throw(
            AssertionError("snapshot must never save"))
        service = mission_service.MissionService(
            store, self.clock,
            lambda prefix: minted.append(prefix) or mission_record.mint_id(prefix))
        before = self.read_bytes()
        listing = sorted(os.listdir(self.directory))
        for _ in range(3):
            snapshot = service.snapshot(mission_id)
        self.assertEqual(len(loads), 3)
        self.assertEqual(minted, [])
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.directory)), listing)
        # The observation part keeps the authority-field exclusions of
        # observe(); the record part carries them as get() always has.
        flat = json.dumps(snapshot["observation"])
        for word in AUTHORITY_WORDS:
            self.assertNotIn(word, flat, word)
        self.assertIn("authorization_digest_sha256",
                      json.dumps(snapshot["record"]))

    def test_A3_unknown_and_malformed_ids_refuse_with_the_core_problem(self):
        for bad in ("mn-" + "9" * 32, "nope"):
            with self.assertRaises(mission_record.MissionError) as caught:
                self.service.snapshot(bad)
            self.assertEqual(caught.exception.problem,
                             mission_authorization.PROBLEM_UNKNOWN_MISSION)


# ====================================================================
# B. The durable cursor
# ====================================================================


class BCursorTests(S2Fixture):

    def test_B1_decisions_and_edits_advance_the_cursor(self):
        approve = self.propose()["mission_id"]
        self.assertEqual(self.cursor(approve), 1)  # revision 1, nothing else
        self.approve(approve, 1)
        self.assertEqual(self.cursor(approve), 3)  # + decision + ISSUED entry
        deny = self.propose()["mission_id"]
        before = self.cursor(deny)
        decision_id = self.service.mint_decision_id(self.context)
        self.service.apply_human_decision(self.md.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id, mission_id=deny,
            revision=1, decision=self.md.DECISION_DENY,
            received_at=self.clock()))
        self.assertEqual(self.cursor(deny), before + 2)  # decision + DENIED
        edit = self.propose()["mission_id"]
        before = self.cursor(edit)
        self.edit(edit, 1, objective="changed once")
        self.assertEqual(self.cursor(edit), before + 2)  # revision + decision
        # EDIT after APPROVE revokes (INVALIDATED_BY_EDIT) as well.
        before = self.cursor(approve)
        self.edit(approve, 1, objective="changed after approval")
        self.assertEqual(self.cursor(approve), before + 3)
        revoked = self.service.get(approve)["authorizations"][0]
        self.assertTrue(revoked["revocation"]["revoked"])

    def test_B2_every_state_operation_family_advances_the_cursor(self):
        upstream = self.completed_prerequisite()
        mission_id = self.dependent_on(upstream)  # activation already applied
        seen = self.cursor(mission_id)
        ms = self.ms
        steps = []

        def step(label, name, *args, **kwargs):
            outcome = self.call(name, mission_id, *args, **kwargs)
            nonlocal_seen[0] = self.assert_strictly_advanced(
                mission_id, nonlocal_seen[0], label)
            steps.append(label)
            return outcome

        nonlocal_seen = [seen]
        step("record_claim", "record_claim", "tests_pass", "the suite passes")
        artifact = step("record_artifact", "record_artifact", "test_log",
                        mission_record.ARTIFACT_ROLE_VERIFICATION,
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A,
                        True, [])
        evidence = step("submit_evidence", "submit_evidence", "tests_pass",
                        mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                        [artifact["artifact_id"]])
        self.clock.advance(1)
        step("accept_evidence", "accept_evidence", evidence["evidence_id"],
             "e" * 64, context=self.other)
        blocker = step("open_blocker", "open_blocker", "flaky_network",
                       "the network flaked")
        step("resolve_blocker", "resolve_blocker", blocker["blocker_id"],
             evidence["evidence_id"])
        bound = step("bind_dependency", "bind_dependency", "upstream", upstream)
        step("resolve_dependency", "resolve_dependency", bound["dependency_id"],
             evidence["evidence_id"])
        step("observe_resource_readiness", "observe_resource_readiness",
             "build_host", ms.READINESS_READY, self.clock())
        step("record_continuation", "record_continuation", "retrying the probe")
        step("record_checkpoint", "record_checkpoint", ["probe fixed"],
             ["verify on the build host"], "retry when the host is ready",
             "stop after three attempts")
        second = step("submit_evidence (second)", "submit_evidence", "tests_pass",
                      mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "f" * 64,
                      [artifact["artifact_id"]])
        step("invalidate_evidence", "invalidate_evidence", second["evidence_id"],
             "superseded by a later run")
        # Reconciliation is a state operation too: a first, changed pass.
        inputs = materialize(
            {"task": Counting({"value": "ACTIVE", "observed_at": self.clock()})},
            mission_id, self.service.get_journal(mission_id)["cursor"])
        result = self.service.reconcile(mission_id, self.oid(), self.seq(mission_id),
                                        inputs, self.context)
        self.assertTrue(result["changed"])
        nonlocal_seen[0] = self.assert_strictly_advanced(mission_id,
                                                         nonlocal_seen[0], "reconcile")
        step("complete_successfully", "complete_successfully", "all proof accepted")
        self.assertEqual(len(steps), 14)
        # The terminal alternatives, each on its own Mission.
        closing = self.ready_mission(required_dependencies=[])
        before = self.cursor(closing)
        self.call("close_unsuccessful", closing, ms.CLOSURE_REASON_CALLER_CLOSED,
                  "closing")
        self.assertGreater(self.cursor(closing), before)
        abandoning = self.ready_mission(required_dependencies=[])
        before = self.cursor(abandoning)
        self.call("abandon", abandoning, "abandoned by the operator")
        self.assertGreater(self.cursor(abandoning), before)

    def test_B3_replay_restart_and_clock_keep_the_cursor_stable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        operation_id = self.oid()
        sequence = self.seq(mission_id)
        self.service.record_claim(mission_id, operation_id, sequence, "tests_pass",
                                  "the suite passes", context=self.context)
        after = self.cursor(mission_id)
        replay = self.service.record_claim(mission_id, operation_id, sequence,
                                           "tests_pass", "the suite passes",
                                           context=self.context)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(self.cursor(mission_id), after)
        # A decision replay too (the core answers idempotently, saving nothing).
        decisions = self.service.get(mission_id)["record"]["decisions"]
        self.assertEqual(len(decisions), 1)
        self.clock.advance(100_000)
        self.assertEqual(self.cursor(mission_id), after)
        restarted = mission_service.MissionService(
            mission_store.MissionStore(self.directory), self.clock)
        self.assertEqual(int(restarted.snapshot(mission_id)["durable_cursor"]),
                         after)
        # And a read that refuses (stale sequence) moves nothing.
        with self.assertRaises(mission_record.MissionError):
            self.service.record_claim(mission_id, self.oid(), 0, "tests_pass",
                                      "stale", context=self.context)
        self.assertEqual(self.cursor(mission_id), after)

    def test_B4_classify_accepts_successive_observations_without_disagreement(self):
        mission_id = self.propose(
            proof_contract=contract(required_dependencies=[]))["mission_id"]
        observations = [self.observation(mission_id)]
        self.approve(mission_id, 1)
        observations.append(self.observation(mission_id))
        self.clock.advance(1)
        self.call("activate_proof_contract", mission_id)
        observations.append(self.observation(mission_id))
        self.call("open_blocker", mission_id, "flaky_network", "flaked")
        observations.append(self.observation(mission_id))
        self.edit(mission_id, 1, objective="changed")
        observations.append(self.observation(mission_id))
        previous = None
        for current in observations:
            result = self.classify(current, previous)
            self.assertEqual(result.freshness, coordination_record.FRESHNESS_FRESH,
                             result.problem)
            # The same read again, against its own recorded point: fresh,
            # never inconsistent.
            again = self.classify(current, self.floor_of(current))
            self.assertEqual(again.freshness, coordination_record.FRESHNESS_FRESH,
                             again.problem)
            previous = self.floor_of(current)
        cursors = [int(o.state_cursor) for o in observations]
        self.assertEqual(cursors, sorted(cursors))
        self.assertEqual(len(set(cursors)), len(cursors))


# ====================================================================
# C. The adapter: durable content only; time in the evaluation point
# ====================================================================


class CAdapterTests(S2Fixture):

    def _expiring_authorized(self, seconds=100):
        mission_id = self.propose()["mission_id"]
        self.approve(mission_id, 1, expires_at=self.clock() + seconds)
        return mission_id

    def test_C1_authorization_expiry_alone_changes_no_durable_fact(self):
        mission_id = self._expiring_authorized(100)
        before = self.observation(mission_id)
        cursor = self.cursor(mission_id)
        live_before = self.status(mission_id)["mission"]
        self.assertEqual(live_before["authorizations"][0]["standing"], "live")
        self.assertIsNotNone(live_before["live_authorization_id"])
        self.clock.advance(200)
        after = self.observation(mission_id)
        self.assertEqual(self.cursor(mission_id), cursor)
        self.assertEqual(coordination_observation.observation_point(before),
                         coordination_observation.observation_point(after))
        self.assertEqual(after.lifecycle_state, coordination_record.LIFECYCLE_AUTHORIZED)
        self.assertIsNotNone(after.authorization_digest_sha256)
        self.assertEqual(after.observed_at, self.clock())
        # Coordination: fresh at the recorded point, no equivocation.
        result = self.classify(after, self.floor_of(before))
        self.assertEqual(result.freshness, coordination_record.FRESHNESS_FRESH,
                         result.problem)
        self.assertFalse(any(c.kind == coordination_record.ATTENTION_AUTHORIZATION_READY
                             for c in after.conditions))
        # Status: expired, as of the evaluation time, issued-only digest,
        # no live authorization.
        mission = self.status(mission_id)["mission"]
        self.assertEqual(mission["durable_cursor"], str(cursor))
        self.assertEqual(mission["evaluated_at"], self.clock())
        self.assertIsNone(mission["live_authorization_id"])
        self.assertEqual(mission["authorizations"][0]["standing"], "expired")
        self.assertEqual(mission["authorizations"][0]["as_of"], self.clock())
        self.assertEqual(mission["authorizations"][0]["digest_is"], "issued-only")
        self.assertEqual(mission["state"], mission_record.STATE_AUTHORIZED)
        self.assertTrue(any("issued-only" in line for line in
                            self.status(mission_id)["limitations"]))

    def test_C2_attention_projection_never_claims_live_authority(self):
        mission_id = self._expiring_authorized(100)
        document = coordination_store.default_document()
        destination = {"transport": "grok_mcp", "conversation_ref": "conv-1"}
        minted = []

        def mint():
            value = coordination_record.mint_id("ca")
            minted.append(value)
            return value

        def project():
            result = self.classify(self.observation(mission_id))
            self.assertEqual(result.freshness, coordination_record.FRESHNESS_FRESH,
                             result.problem)
            coordination_attention.project(document, mission_id, destination,
                                           result, self.clock(), mint)
            return [value["condition_kind"]
                    for value in document["attention"].values()]

        project()                      # live
        self.clock.advance(200)        # expired, no durable change
        kinds = project()
        self.assertNotIn(coordination_record.ATTENTION_AUTHORIZATION_READY, kinds)
        self.assertNotIn("live", json.dumps(document["attention"]).lower())
        mission = self.status(mission_id)["mission"]
        self.assertEqual(mission["authorizations"][0]["standing"], "expired")
        # Revocation by EDIT: the authorization is revoked and superseded;
        # the new revision awaits a decision; still no live-ready claim.
        self.edit(mission_id, 1, objective="changed after expiry")
        kinds = project()
        self.assertNotIn(coordination_record.ATTENTION_AUTHORIZATION_READY, kinds)
        self.assertIn(coordination_record.ATTENTION_NEEDS_HUMAN, kinds)
        mission = self.status(mission_id)["mission"]
        self.assertEqual(mission["authorizations"][0]["standing"], "revoked")
        self.assertTrue(mission["authorizations"][0]["revoked"])
        self.assertIsNone(mission["live_authorization_id"])
        self.assertEqual(mission["state"], mission_record.STATE_AWAITING_DECISION)

    def test_C3_readiness_staleness_alone_changes_no_durable_fact(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self.call("observe_resource_readiness", mission_id, "build_host",
                  self.ms.READINESS_READY, self.clock())
        before = self.observation(mission_id)
        cursor = self.cursor(mission_id)
        self.assertTrue(self.status(mission_id)["mission"]["holds"]["readiness"]
                        ["satisfied"])
        self.clock.advance(601)  # past max_age_seconds 600
        after = self.observation(mission_id)
        self.assertEqual(self.cursor(mission_id), cursor)
        self.assertEqual(coordination_observation.observation_point(before),
                         coordination_observation.observation_point(after))
        self.assertEqual(self.classify(after, self.floor_of(before)).freshness,
                         coordination_record.FRESHNESS_FRESH)
        holds = self.status(mission_id)["mission"]["holds"]
        self.assertFalse(holds["readiness"]["satisfied"])

    def test_C4_external_report_change_is_per_source_freshness_only(self):
        mission_id = self.ready_mission(required_dependencies=[])
        before = self.observation(mission_id)
        cursor = self.cursor(mission_id)
        head = self.service.get_journal(mission_id)["cursor"]
        quiet = self.status(mission_id)["mission"]["sources"]
        reported = self.status(mission_id, inputs=materialize(
            {"task": Counting({"value": "ACTIVE", "observed_at": self.clock()})},
            mission_id, head))["mission"]["sources"]
        # No source answer at all: the fact says so (unavailable), and a
        # reported answer changes only that source's standing/time.
        self.assertEqual(quiet["task"]["standing"],
                         mission_observation.STANDING_UNAVAILABLE)
        self.assertEqual(reported["task"]["standing"],
                         mission_observation.STANDING_REPORTED)
        self.assertEqual(reported["task"]["observed_at"], self.clock())
        after = self.observation(mission_id)
        self.assertEqual(self.cursor(mission_id), cursor)
        self.assertEqual(coordination_observation.observation_point(before),
                         coordination_observation.observation_point(after))

    def test_C5_dependency_mission_change_moves_only_its_own_cursor(self):
        upstream = self.ready_mission(required_dependencies=[])
        dependent = self.dependent_on(upstream)
        self.call("bind_dependency", dependent, "upstream", upstream)
        before = self.observation(dependent)
        cursor = self.cursor(dependent)
        upstream_cursor = self.cursor(upstream)
        self.make_local_complete(upstream)
        self.call("complete_successfully", upstream, "all proof accepted")
        self.assertGreater(self.cursor(upstream), upstream_cursor)
        after = self.observation(dependent)
        self.assertEqual(self.cursor(dependent), cursor)
        self.assertEqual(coordination_observation.observation_point(before),
                         coordination_observation.observation_point(after))
        self.assertEqual(self.classify(after, self.floor_of(before)).freshness,
                         coordination_record.FRESHNESS_FRESH)
        # The binding is a durable condition of the dependent Mission.
        keys = [c.key for c in after.conditions
                if c.kind == coordination_record.ATTENTION_BLOCKED]
        self.assertEqual(keys, ["dependency.upstream"])
        self.assertEqual(self.status(dependent)["mission"]["holds"]
                         ["unresolved_dependencies"], ["upstream"])

    def test_C6_blocker_is_in_both_status_and_observation_until_resolved(self):
        mission_id = self.ready_mission(required_dependencies=[])
        blocker = self.call("open_blocker", mission_id, "flaky_network", "flaked")
        cursor = self.cursor(mission_id)
        observed = self.observation(mission_id)
        conditions = [(c.kind, c.key) for c in observed.conditions]
        self.assertIn((coordination_record.ATTENTION_BLOCKED,
                       "blocker.flaky_network.%s" % blocker["blocker_id"]),
                      conditions)
        holds = self.status(mission_id)["mission"]["holds"]
        self.assertEqual([b["key"] for b in holds["active_blockers"]],
                         ["flaky_network"])
        evidence = self.make_local_complete(mission_id)
        self.call("resolve_blocker", mission_id, blocker["blocker_id"], evidence)
        self.assertGreater(self.cursor(mission_id), cursor)
        self.assertFalse(any(c.kind == coordination_record.ATTENTION_BLOCKED
                             for c in self.observation(mission_id).conditions))
        self.assertEqual(self.status(mission_id)["mission"]["holds"]
                         ["active_blockers"], [])

    def test_C7_two_missions_unknown_absent_and_refusal_unavailable(self):
        first = self.ready_mission(required_dependencies=[])  # activated: running
        second = self.propose()["mission_id"]
        third = self.propose()["mission_id"]
        self.approve(third, 1)  # approved, not activated: authorized
        one, two, three = (self.observation(first), self.observation(second),
                           self.observation(third))
        self.assertEqual(len({one.mission_id, two.mission_id, three.mission_id}), 3)
        self.assertEqual(two.lifecycle_state,
                         coordination_record.LIFECYCLE_AWAITING_DECISION)
        self.assertEqual([(c.kind, c.key) for c in two.conditions],
                         [(coordination_record.ATTENTION_NEEDS_HUMAN, "decision")])
        self.assertEqual(one.lifecycle_state, coordination_record.LIFECYCLE_RUNNING)
        self.assertEqual(three.lifecycle_state,
                         coordination_record.LIFECYCLE_AUTHORIZED)
        self.assertEqual(three.conditions, ())
        absent = self.outcome("mn-" + "9" * 32)
        self.assertEqual(absent.status, coordination_record.OBSERVATION_ABSENT)
        # A store that refuses (or raises anything) is unavailable, never
        # absent: injected, plus the real exposed-directory refusal.
        broken = mission_store.MissionStore(self.directory)
        broken.read = lambda: (_ for _ in ()).throw(OSError("disk gone"))
        broken.load = lambda: (_ for _ in ()).throw(
            AssertionError("the adapter reads through the owner's read(), never load"))
        refused = self.outcome(first, mission_service.MissionService(broken,
                                                                     self.clock))
        self.assertEqual(refused.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertEqual(refused.problem, "OSError")
        if not running_as_root():
            os.chmod(self.directory, 0o755)
            try:
                exposed = self.outcome(first)
            finally:
                os.chmod(self.directory, 0o700)
            self.assertEqual(exposed.status,
                             coordination_record.OBSERVATION_UNAVAILABLE)
            # The store's own typed refusal, surfaced with its reason.
            self.assertTrue(exposed.problem.startswith("MissionStoreError: "),
                            exposed.problem)
            self.assertIn("could not be read", exposed.problem)

    def test_C8_references_are_sorted_by_id_whatever_the_append_order(self):
        # Ids minted in DESCENDING order for each reference kind.
        counters = {}

        def descending_mint(prefix):
            counters[prefix] = counters.get(prefix, 0) + 1
            return "%s-%032x" % (prefix, 0xffff - counters[prefix])

        self.service = mission_service.MissionService(self.store, self.clock,
                                                      descending_mint)
        mission_id = self.ready_mission(required_dependencies=[])
        ms = self.ms
        artifacts = [self.call("record_artifact", mission_id, key,
                               mission_record.ARTIFACT_ROLE_PRODUCED,
                               ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:" + key,
                               HEX_A, True, [])["artifact_id"]
                     for key in ("one", "two", "three")]
        evidence = [self.call("submit_evidence", mission_id, "tests_pass",
                              mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                              "e" * 64, [artifacts[0]])["evidence_id"]
                    for _ in range(3)]
        self.assertEqual(artifacts, sorted(artifacts, reverse=True))
        self.assertEqual(evidence, sorted(evidence, reverse=True))
        observed = self.observation(mission_id)
        self.assertEqual([r.artifact_id for r in observed.artifact_refs],
                         sorted(artifacts))
        self.assertEqual([r.evidence_id for r in observed.evidence_refs],
                         sorted(evidence))

    def test_C9_condition_identities_are_bounded_and_collision_free(self):
        limit = coordination_record.MAX_KEY_CHARS
        long_key = "k" * limit
        upstream = self.ready_mission(required_dependencies=[])
        other = self.ready_mission(required_dependencies=[])

        def target(prerequisite):
            digest = self.store.load()["missions"][prerequisite]["revisions"][0][
                "proposal_digest_sha256"]
            return {"form": mission_record.TARGET_FORM_EXACT_MISSION,
                    "mission_id": prerequisite, "revision": 1,
                    "proposal_digest_sha256": digest}

        mission_id = self.ready_mission(required_dependencies=[
            {"key": "upstream", "kind": mission_record.DEPENDENCY_KIND_MISSION,
             "target": target(upstream)},
            {"key": long_key, "kind": mission_record.DEPENDENCY_KIND_MISSION,
             "target": target(other)},
        ])
        self.call("bind_dependency", mission_id, "upstream", upstream)
        self.call("bind_dependency", mission_id, long_key, other)
        alike = self.call("open_blocker", mission_id, "dependency.upstream",
                          "looks alike")
        long_blocker = self.call("open_blocker", mission_id, long_key,
                                 "long blocker key")
        observed = self.observation(mission_id)
        blocked = sorted(c.key for c in observed.conditions
                         if c.kind == coordination_record.ATTENTION_BLOCKED)
        self.assertEqual(len(blocked), 4)
        self.assertEqual(len(set(blocked)), 4)
        self.assertIn("dependency.upstream", blocked)
        self.assertIn("blocker.dependency.upstream.%s" % alike["blocker_id"],
                      blocked)
        for key in blocked:
            coordination_record.require_key(key, "key")
            self.assertLessEqual(len(key), limit)
        hashed = [key for key in blocked if ".h." in key]
        self.assertEqual(len(hashed), 2)
        self.assertIn("dependency.h." + hashlib.sha256(
            long_key.encode("utf-8")).hexdigest(), hashed)
        self.assertIn("blocker.h." + hashlib.sha256(
            ("%s.%s" % (long_key, long_blocker["blocker_id"])).encode("utf-8")
        ).hexdigest(), hashed)
        # Deterministic: the same document yields the same identities.
        self.assertEqual(blocked, sorted(c.key for c in self.observation(mission_id)
                                         .conditions
                                         if c.kind == coordination_record
                                         .ATTENTION_BLOCKED))
        # A raw key beginning with the hashed mark is hashed, so the two
        # forms never coincide.
        self.assertTrue(adapter.bounded_key("blocker.", "h.abc").startswith(
            "blocker.h."))
        self.assertNotEqual(adapter.bounded_key("blocker.", "h.abc"),
                            "blocker.h.abc")

    def test_C10_more_conditions_than_the_contract_bound_is_explicit_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        bound = coordination_observation.MAX_OBSERVED_CONDITIONS
        for index in range(bound + 1):
            self.call("open_blocker", mission_id, "blocker_%03d" % index, "many")
        outcome = self.outcome(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("%d durable conditions" % (bound + 1), outcome.problem)
        self.assertIn("at most %d" % bound, outcome.problem)
        holds = self.status(mission_id)["mission"]["holds"]
        self.assertEqual(len(holds["active_blockers"]), bound + 1)
        self.assertEqual(self.status(mission_id)["mission"]["availability"],
                         "present")

    def test_C11_construction_and_validation_failures_are_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        with mock.patch.object(coordination_observation.MissionObservation,
                               "validate", side_effect=coordination_record
                               .CoordinationError("forced", "coordination_forced")):
            outcome = self.outcome(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("coordination_forced", outcome.problem)
        with mock.patch.object(adapter, "observation_from_snapshot",
                               side_effect=RuntimeError("boom")):
            outcome = self.outcome(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("RuntimeError", outcome.problem)
        # Never absent, never raised: observe_safely sees the same.
        safe = coordination_observation.observe_safely(self.source(), mission_id)
        self.assertEqual(safe.status, coordination_record.OBSERVATION_OBSERVED)


# ====================================================================
# D. The status read: lock-free, absent vs unavailable, no writes
# ====================================================================


class DStatusTests(S2Fixture):

    def _seed_workflow_store(self):
        store = workflow_store.WorkflowStore(self.workflow_dir)
        store.save(workflow_store.default_document())
        return store

    def test_D1_status_completes_while_a_child_holds_the_real_workflow_lock(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self._seed_workflow_store()
        held = os.path.join(self.base.name, "held")
        release = os.path.join(self.base.name, "release")
        child = subprocess.Popen([sys.executable, "-c", (
            "import os, sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from workflow_authority.store import exclusive_store_lock\n"
            "with exclusive_store_lock(%r):\n"
            "    open(%r, 'w').close()\n"
            "    for _ in range(400):\n"
            "        if os.path.exists(%r):\n"
            "            break\n"
            "        time.sleep(0.05)\n"
        ) % (str(REPO_ROOT), self.workflow_dir, held, release)])
        try:
            for _ in range(400):
                if os.path.exists(held):
                    break
                time.sleep(0.05)
            else:
                self.fail("the child never acquired the workflow lock")
            started = time.monotonic()
            result = self.full_status(mission_id)
            elapsed = time.monotonic() - started
            self.assertIsNone(child.poll())  # still holding the lock
            self.assertLess(elapsed, STATUS_DEADLINE_SECONDS)
            self.assertEqual(result["stores"]["workflow"]["availability"], "present")
            self.assertEqual(result["stores"]["workflow"]["records"], 0)
            self.assertEqual(result["mission"]["availability"], "present")
        finally:
            open(release, "w").close()
            child.wait(timeout=30)

    def test_D2_status_returns_while_the_broker_is_blocked_in_a_verification_turn(self):
        from test_target_runtime import I5LifecycleTests, broker_module  # noqa: E402
        mission_id = self.ready_mission(required_dependencies=[])
        case = I5LifecycleTests("test_verification_turn_refusal_stops_durably")
        case.setUp()
        inside = threading.Event()
        release = threading.Event()
        original = case.broker._role_turn

        def blocking(role, entry, now, **kwargs):
            if role == "verification":
                inside.set()
                release.wait(20)
            return original(role, entry, now, **kwargs)

        case.broker._role_turn = blocking
        outcomes = {}
        try:
            case.dispatched()
            worker = threading.Thread(
                target=lambda: outcomes.setdefault("verify", case.perform(
                    "wf-0001", broker_module.ACTION_VERIFY, 2)), daemon=True)
            worker.start()
            self.assertTrue(inside.wait(20), "the verification turn never started")
            turns_before = len(case.role_turn.calls)
            started = time.monotonic()
            result = status_module.read_status(
                self.service, mission_id, workflow_directory=case.store_dir)
            elapsed = time.monotonic() - started
            self.assertTrue(worker.is_alive())  # still inside the locked action
            self.assertLess(elapsed, STATUS_DEADLINE_SECONDS)
            self.assertEqual(result["stores"]["workflow"]["availability"], "present")
            self.assertEqual(result["stores"]["workflow"]["records"], 1)
            # No model/operator call by status: the turn count is unchanged.
            self.assertEqual(len(case.role_turn.calls), turns_before)
        finally:
            release.set()
            worker.join(30)
            try:
                case.tearDown()
            finally:
                case.doCleanups()
        self.assertIn("verify", outcomes)
        # The blocked verification turn itself completed after release.
        self.assertEqual(len(case.role_turn.calls), turns_before + 1)

    def test_D3_atomic_replacement_during_a_read_yields_whole_old_or_whole_new(self):
        mission_id = self.ready_mission(required_dependencies=[])
        loads = []
        real_read = self.store.read

        def counted_read():
            loads.append(1)
            return real_read()

        self.store.read = counted_read
        operation_id = self.oid()
        sequence = self.seq(mission_id)
        paused = threading.Event()
        proceed = threading.Event()
        real_replace = os.replace

        def gated_replace(source, destination):
            if destination.endswith("missions.json"):
                paused.set()
                proceed.wait(20)
            return real_replace(source, destination)

        def writer():
            with mock.patch("os.replace", gated_replace):
                self.service.record_claim(mission_id, operation_id, sequence,
                                          "tests_pass", "the suite passes",
                                          context=self.context)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            self.assertTrue(paused.wait(20))
            loads[:] = []
            old = self.status(mission_id)
            self.assertEqual(len(loads), 1)  # exactly one Mission owner read
            self.assertEqual(old["mission"]["sequence"], sequence)
            # No mixed projection: every part of the snapshot is at the
            # old sequence.
            snapshot = self.snapshot(mission_id)
            self.assertEqual(snapshot["state"]["sequence"], sequence)
            self.assertEqual(snapshot["observation"]["sequence"], sequence)
            self.assertEqual(snapshot["state"]["record"]["claims"], [])
        finally:
            proceed.set()
            thread.join(30)
        new = self.snapshot(mission_id)
        self.assertEqual(new["state"]["sequence"], sequence + 1)
        self.assertEqual(new["observation"]["sequence"], sequence + 1)
        self.assertEqual(len(new["state"]["record"]["claims"]), 1)
        self.assertEqual(int(new["durable_cursor"]),
                         int(old["mission"]["durable_cursor"]) + 1)

    def test_D3b_replacement_while_a_reader_holds_the_opened_old_descriptor(self):
        # Brief test 3, synchronized: the canonical Mission read OPENS the
        # store file and pauses (descriptor on the OLD inode); the writer
        # performs the REAL temp-fsync-replace; the reader resumes and
        # completes on that descriptor. The one snapshot is entirely OLD,
        # one load, the writer completed, the next status entirely NEW,
        # no thread leaked.
        mission_id = self.ready_mission(required_dependencies=[])
        loads = []
        real_read = self.store.read

        def counted_read():
            loads.append(threading.get_ident())
            return real_read()

        self.store.read = counted_read
        opened = threading.Event()
        proceed = threading.Event()
        holder = {}
        real_open = os.open
        threads_before = threading.active_count()

        def opening(path, *args, **kwargs):
            # The owner's read opens the store FILE relative to its opened
            # directory descriptor (correction 2); the pause is at that
            # descriptor, on the OLD inode.
            handle = real_open(path, *args, **kwargs)
            if (threading.get_ident() == holder.get("reader")
                    and str(path).endswith("missions.json")
                    and kwargs.get("dir_fd") is not None
                    and not opened.is_set()):
                holder["inode"] = os.fstat(handle).st_ino
                opened.set()
                proceed.wait(20)
            return handle

        results = {}

        def reader():
            holder["reader"] = threading.get_ident()
            results["old"] = self.status(mission_id)

        sequence = self.seq(mission_id)
        old_cursor = self.cursor(mission_id)
        loads[:] = []
        with mock.patch.object(wa_atomic.os, "open", opening):
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            try:
                self.assertTrue(opened.wait(20), "the reader never opened the store")
                old_inode = holder["inode"]
                # The REAL atomic replacement, while the descriptor is held.
                self.call("record_claim", mission_id, "tests_pass",
                          "the suite passes")
                self.assertNotEqual(os.stat(self.store.path).st_ino, old_inode)
            finally:
                proceed.set()
                thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(threading.active_count(), threads_before)
        old = results["old"]["mission"]
        # Entirely OLD: no field from the new document.
        self.assertEqual(old["sequence"], sequence)
        self.assertEqual(old["durable_cursor"], str(old_cursor))
        self.assertEqual(old["holds"]["active_blockers"], [])
        self.assertEqual(old["time"]["record_updated_at"],
                         self.store.load()["mission_state"][mission_id]
                         ["applied_operations"][-2]["applied_at"])
        # Exactly one Mission owner read for that status call (the
        # reader's thread); the writer's mint and locked load-modify-save
        # went through ``load`` on the main thread, never ``read``.
        self.assertEqual(loads.count(holder["reader"]), 1)
        self.assertEqual(loads, [holder["reader"]])
        # The writer completed: the new document is on disk, and the next
        # status is entirely NEW.
        self.assertEqual(len(self.store.load()["mission_state"][mission_id]["claims"]),
                         1)
        new = self.status(mission_id)["mission"]
        self.assertEqual(new["sequence"], sequence + 1)
        self.assertEqual(int(new["durable_cursor"]), old_cursor + 1)
        self.assertEqual(new["time"]["record_updated_at"],
                         self.store.load()["mission_state"][mission_id]
                         ["applied_operations"][-1]["applied_at"])

    def test_D4_nonexistent_nested_directories_are_absent_and_stay_absent(self):
        mission_id = self.ready_mission(required_dependencies=[])
        nested = os.path.join(self.base.name, "no", "such", "place")
        before = inventory(self.base.name)
        result = status_module.read_status(
            self.service, mission_id,
            workflow_directory=os.path.join(nested, "workflows"),
            delivery_directory=os.path.join(nested, "delivery"),
            coordination_directory=os.path.join(nested, "coordination"))
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "absent", name)
            self.assertIsNone(result["stores"][name]["problem"])
        self.assertFalse(os.path.exists(os.path.join(self.base.name, "no")))
        self.assertEqual(inventory(self.base.name), before)
        # A missing store FILE in an existing, readable directory: absent.
        os.makedirs(self.workflow_dir, mode=0o700)
        result = self.full_status(mission_id)
        self.assertEqual(result["stores"]["workflow"]["availability"], "absent")
        self.assertEqual(sorted(os.listdir(self.workflow_dir)), [])

    def test_D5_exposed_malformed_invalid_and_inaccessible_are_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        store = self._seed_workflow_store()
        # Exposed file mode -> the store's own refusal, no repair.
        os.chmod(store.path, 0o644)
        result = self.full_status(mission_id)["stores"]["workflow"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertEqual(result["problem"], "StoreError: %s" % (
            wa_atomic.RULE_FILE_EXPOSED % 0o644))
        self.assertEqual(stat.S_IMODE(os.stat(store.path).st_mode), 0o644)
        os.chmod(store.path, 0o600)
        # Malformed JSON -> unavailable.
        write_private(store.path, "{not json")
        result = self.full_status(mission_id)["stores"]["workflow"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertIn("StoreError", result["problem"])
        # Delivery: invalid version, invalid record -> unavailable; valid
        # -> present (through the delivery package's own boundary).
        write_private(os.path.join(self.delivery_dir, "pr_delivery.json"),
                      json.dumps({"pr_delivery_store_schema_version": 999,
                                  "deliveries": {}}))
        result = self.full_status(mission_id)["stores"]["delivery"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertEqual(result["problem"], "StoreError")
        valid = delivery_store.default_document()
        invalid = copy.deepcopy(valid)
        invalid["deliveries"]["dl-bad"] = {"not": "a record"}
        write_private(os.path.join(self.delivery_dir, "pr_delivery.json"),
                      json.dumps(invalid))
        result = self.full_status(mission_id)["stores"]["delivery"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertEqual(result["problem"], "StoreError")
        delivery_store.DeliveryStore(self.delivery_dir).save(valid)
        result = self.full_status(mission_id)["stores"]["delivery"]
        self.assertEqual(result["availability"], "present")
        self.assertEqual(result["records"], 0)
        # Coordination: malformed -> unavailable; valid -> present.
        write_private(os.path.join(self.coordination_dir, "coordination.json"),
                      "[]")
        result = self.full_status(mission_id)["stores"]["coordination"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertIn("CoordinationStoreError", result["problem"])
        os.remove(os.path.join(self.coordination_dir, "coordination.json"))
        coordination_store.CoordinationStore(self.coordination_dir).save(
            coordination_store.default_document(), 0)
        result = self.full_status(mission_id)["stores"]["coordination"]
        self.assertEqual(result["availability"], "present")
        self.assertEqual(result["store_sequence"], 1)
        # Injected PermissionError at the owner's open -> unavailable, not
        # absent.
        with mock.patch.object(wa_atomic.os, "open",
                               refusing_open(STORE_FILE_NAMES["workflow"])):
            result = self.full_status(mission_id)["stores"]["workflow"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertEqual(result["problem"], "PermissionError")
        # The Mission store itself: an unreadable document -> unavailable.
        write_private(self.store.path, "{not json")
        result = self.status(mission_id)["mission"]
        self.assertEqual(result["availability"], "unavailable")
        self.assertIn("MissionStoreError", result["problem"])
        self.assertIn("invalid JSON", result["problem"])
        if not running_as_root():
            # A REAL inaccessible ancestor: unavailable, never absent.
            hidden = os.path.join(self.base.name, "hidden")
            os.makedirs(os.path.join(hidden, "workflows"), mode=0o700)
            os.chmod(hidden, 0)
            try:
                result = status_module.read_status(
                    self.service, mission_id,
                    workflow_directory=os.path.join(hidden, "workflows"))
            finally:
                os.chmod(hidden, 0o700)
            self.assertEqual(result["stores"]["workflow"]["availability"],
                             "unavailable")
            self.assertEqual(result["stores"]["workflow"]["problem"],
                             "PermissionError")

    def test_D6_status_writes_nothing_and_calls_no_writer_lock_or_mint(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self._seed_workflow_store()
        delivery_store.DeliveryStore(self.delivery_dir).save(
            delivery_store.default_document())
        coordination_store.CoordinationStore(self.coordination_dir).save(
            coordination_store.default_document(), 0)
        before = inventory(self.base.name)
        mission_before = inventory(self.directory)
        spies = {}
        patches = []
        for target, name in ((wa_atomic, "atomic_write_json"),
                             (wa_atomic, "exclusive_store_lock"),
                             (os, "makedirs"), (os, "mkdir"), (os, "replace"),
                             (mission_store.MissionStore, "save"),
                             (mission_store.MissionStore, "lock"),
                             (workflow_store.WorkflowStore, "save"),
                             (delivery_store.DeliveryStore, "save"),
                             (delivery_store.DeliveryStore, "lock"),
                             (coordination_store.CoordinationStore, "save"),
                             (coordination_store.CoordinationStore, "lock"),
                             (mission_service.MissionService, "_reserve"),
                             (mission_service.MissionService, "apply_human_decision"),
                             (mission_service.MissionService, "reconcile"),
                             (mission_service.MissionService, "_apply")):
            spy = mock.MagicMock(name="%s.%s" % (getattr(target, "__name__", "os"),
                                                 name))
            spies[(getattr(target, "__name__", "os"), name)] = spy
            patcher = mock.patch.object(target, name, spy)
            patcher.start()
            patches.append(patcher)
        try:
            result = self.full_status(mission_id)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        for key, spy in spies.items():
            self.assertEqual(spy.call_count, 0, key)
        self.assertEqual(result["mission"]["availability"], "present")
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "present")
        self.assertEqual(inventory(self.base.name), before)
        self.assertEqual(inventory(self.directory), mission_before)

    def test_D7_timing_is_per_source_and_claims_no_cross_store_coherence(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self._seed_workflow_store()
        result = self.full_status(mission_id)
        self.assertEqual([result["stores"][n]["timing"]["read_order"]
                          for n in ("workflow", "delivery", "coordination")],
                         [1, 2, 3])
        for name in ("workflow", "delivery", "coordination"):
            timing = result["stores"][name]["timing"]
            self.assertTrue(timing["read_after_mission_snapshot"])
            self.assertEqual(timing["coherence_with_mission_snapshot"], "none")
        text = json.dumps(result).lower()
        for claim in ("never newer", "atomic across", "consistent across",
                      "same instant", "coherent snapshot of all"):
            self.assertNotIn(claim, text, claim)
        self.assertTrue(any("no cross-store atomicity, ordering or coherence"
                            in line for line in result["limitations"]))
        self.assertEqual(result["mission"]["evaluated_at"], self.clock())


# ====================================================================
# F. Correction 1 (round 10): holds never disappear, blocker identity,
#    evidence covers what the loader opens
# ====================================================================


def synthetic_snapshot(**overrides):
    """A minimal snapshot dict for ``derive_holds``: every fact quiet
    unless overridden."""
    def fact(value, standing=mission_observation.STANDING_VERIFIED,
             freshness=None, observed_at=None):
        return {"standing": standing, "freshness": freshness, "value": value,
                "observed_at": observed_at, "source": "record", "detail": None}

    def unavailable_fact():
        return fact(None, mission_observation.STANDING_UNAVAILABLE)

    snapshot = {
        "mission_id": "mn-" + "1" * 32,
        "evaluated_at": 1_000_000,
        "durable_cursor": "7",
        "record": {"record": {"mission_id": "mn-" + "1" * 32, "state": "AUTHORIZED",
                              "current_revision": 1},
                   "authorizations": [], "live_authorization_id": None},
        "state": {"sequence": 3, "proof": None, "dependencies": None,
                  "closure_eligibility": None, "readiness": None,
                  "record": None,
                  "contract": {"active": False, "current": False, "problem": None,
                               "activation_id": None}},
        "observation": {
            "completion": {"holds": []},
            "proof": fact(None),
            "dependencies": fact(None),
            "readiness": fact(None),
            "contract": fact({"active": False, "activation_id": None,
                              "revision": None, "current": False,
                              "authority_live": False, "problem": None}),
            "blockers": fact({"active": [], "hard_active": False}),
            "task": unavailable_fact(), "review": unavailable_fact(),
            "candidate": unavailable_fact(), "delivery": unavailable_fact(),
            "time": {"now": 1_000_000, "record_updated_at": None,
                     "source": "clock"},
        },
    }
    for path, value in overrides.items():
        target = snapshot
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return snapshot


def codes_of(holds):
    return sorted("%s:%s" % (h["kind"], h["code"]) for h in holds)


class FHoldsCompletenessTests(unittest.TestCase):

    def test_F1_vocabulary_covers_every_core_hold_and_problem_constant(self):
        # Every hold/reason constant the core can emit is in the status
        # vocabulary — enumerated from the core modules, not by hand.
        vocabulary = set()
        for kind, codes in status_module.HOLD_VOCABULARY.items():
            vocabulary.update("%s:%s" % (kind, code) for code in codes)
        for hold in mission_observation.HOLDS:
            self.assertIn("completion:" + hold, vocabulary, hold)
        for name in dir(mission_progress):
            value = getattr(mission_progress, name)
            if name.startswith("PROBLEM_"):
                self.assertTrue(any(v.endswith(":" + value) for v in vocabulary),
                                name)
            if name.startswith("REQUIREMENT_") and isinstance(value, str) and (
                value != mission_progress.REQUIREMENT_SATISFIED
            ):
                self.assertIn("proof_requirement:" + value, vocabulary, name)
            if name.startswith("SLOT_") and isinstance(value, str) and (
                value != mission_progress.SLOT_RESOLVED
            ):
                self.assertIn("dependency_slot:" + value, vocabulary, name)
        for name in dir(mission_reconciliation):
            value = getattr(mission_reconciliation, name)
            if name.startswith("TASK_REPORT_") and value not in (
                mission_reconciliation.TASK_REPORT_ACTIVE,
                mission_reconciliation.TASK_REPORT_COMPLETE,
            ):
                self.assertIn("report:task:" + value, vocabulary, name)
            if name.startswith("REVIEW_REPORT_") and (
                value != mission_reconciliation.REVIEW_REPORT_APPROVE
            ):
                self.assertIn("report:review:" + value, vocabulary, name)
            if name.startswith("DELIVERY_REPORT_") and (
                value != mission_reconciliation.DELIVERY_REPORT_VALID
            ):
                self.assertIn("report:delivery:" + value, vocabulary, name)
        self.assertIn("readiness:" + mission_state.READINESS_NOT_READY, vocabulary)
        for severity in mission_state.BLOCKER_SEVERITIES:
            self.assertIn("blocker:" + severity, vocabulary)
        self.assertIn("proof_freshness:" + mission_observation.FRESHNESS_STALE,
                      vocabulary)
        self.assertIn("decision:awaiting_decision", vocabulary)
        self.assertIn("contract:contract_not_current", vocabulary)

    def test_F2_every_vocabulary_code_surfaces_from_the_snapshot_fact_that_carries_it(self):
        # Completeness, behaviourally: for EVERY code of EVERY kind, a
        # snapshot carrying that fact yields that hold, with as_of.
        vocabulary = status_module.HOLD_VOCABULARY
        cases = []
        for code in vocabulary["completion"]:
            cases.append(("completion:" + code, synthetic_snapshot(**{
                "observation.completion": {"holds": [code]}})))
        # Correction 2 (R11-1): every family comes from the OBSERVATION's
        # facts (evaluated regardless of authority), never from the
        # authority-gated state projection.
        def observed(value, freshness=None):
            return {"standing": "verified", "freshness": freshness, "value": value,
                    "observed_at": None, "source": "record", "detail": None}

        live_contract = {"active": True, "activation_id": "ma-x", "revision": 1,
                         "current": True, "authority_live": True, "problem": None}
        for code in vocabulary["proof_requirement"]:
            cases.append(("proof_requirement:" + code, synthetic_snapshot(**{
                "observation.proof": observed(
                    {"satisfied": False, "requirements": {"r": code}})})))
        cases.append(("proof_freshness:stale", synthetic_snapshot(**{
            "observation.proof": {"standing": "verified", "freshness": "stale",
                                  "value": None, "observed_at": None,
                                  "source": "record", "detail": None}})))
        for code in vocabulary["prerequisite"]:
            cases.append(("prerequisite:" + code, synthetic_snapshot(**{
                "observation.dependencies": {
                    "standing": "verified", "freshness": None,
                    "value": {"satisfied": False, "slots": {},
                              "prerequisite_problems": [{"problem": code,
                                                         "detail": "d"}]},
                    "observed_at": None, "source": "registry", "detail": None}})))
        for code in vocabulary["dependency_slot"]:
            cases.append(("dependency_slot:" + code, synthetic_snapshot(**{
                "observation.dependencies": observed(
                    {"satisfied": False, "slots": {"s": code},
                     "prerequisite_problems": []})})))
        for code in vocabulary["closure_eligibility"]:
            if code == status_module.CODE_CLOSURE_NOT_EVALUATED:
                # Evaluated only under live authority: an active contract
                # whose eligibility the core did not evaluate says so.
                cases.append(("closure_eligibility:" + code, synthetic_snapshot(**{
                    "observation.contract": observed(dict(
                        live_contract, authority_live=False,
                        problem="mission_authorization_expired"))})))
                continue
            cases.append(("closure_eligibility:" + code, synthetic_snapshot(**{
                "state.closure_eligibility": {"eligible": False, "failures": [
                    {"problem": code, "detail": "d"}]}})))
        cases.append(("readiness:NOT_READY", synthetic_snapshot(**{
            "observation.readiness": observed(
                {"satisfied": False, "resources": {"host": "NOT_READY"}})})))
        cases.append(("authority:authority_not_live", synthetic_snapshot(**{
            "observation.contract": observed(dict(
                live_contract, authority_live=False,
                problem="mission_authorization_expired"))})))
        for severity in vocabulary["blocker"]:
            cases.append(("blocker:" + severity, synthetic_snapshot(**{
                "observation.blockers": {
                    "standing": "verified", "freshness": None,
                    "value": {"active": [{"blocker_id": "mb-" + "0" * 32,
                                          "key": "k", "severity": severity,
                                          "opened_at": 1}], "hard_active": True},
                    "observed_at": None, "source": "record", "detail": None}})))
        cases.append(("contract:contract_not_current", synthetic_snapshot(**{
            "observation.contract": observed(dict(live_contract, current=False))})))
        cases.append(("contract:mission_state_contract_stale", synthetic_snapshot(**{
            "observation.contract": observed(dict(
                live_contract, authority_live=False,
                problem="mission_state_contract_stale"))})))
        cases.append(("decision:awaiting_decision", synthetic_snapshot(**{
            "record.record": {"mission_id": "mn-" + "1" * 32,
                              "state": "AWAITING_DECISION", "current_revision": 2}})))
        for code in vocabulary["report"]:
            source, value = code.split(":", 1)
            reported = value if source != "delivery" else {"status": value}
            cases.append(("report:" + code, synthetic_snapshot(**{
                "observation." + source: {"standing": "reported", "freshness": "fresh",
                                          "value": reported, "observed_at": 5,
                                          "source": source, "detail": None}})))
        for code in vocabulary["source_standing"]:
            source, standing = code.split(":", 1)
            cases.append(("source_standing:" + code, synthetic_snapshot(**{
                "observation." + source: {"standing": standing, "freshness": None,
                                          "value": None, "observed_at": None,
                                          "source": source, "detail": None}})))
        # Task 8, slice S-V: the control family comes from the canonical
        # control record carried by the state snapshot.
        def controls(hold=None, cancel=None):
            state_record = mission_state.new_state_record("mn-" + "1" * 32, 3)
            state_record["controls"] = {"version": 1,
                                        "holds": [] if hold is None else [hold],
                                        "cancel_request": cancel, "history": []}
            return state_record

        hold_record = {"requested_at": 1, "reason": "pause", "revision": 1,
                       "provenance": None, "operation_id": "mo-" + "1" * 32,
                       "sequence": 1, "lifted_at": None, "lift_operation_id": None,
                       "lift_sequence": None}
        cancel_record = {"requested_at": 1, "reason": "stop", "revision": 1,
                         "provenance": None, "operation_id": "mo-" + "2" * 32,
                         "sequence": 2, "confirmed_at": None, "confirmation": None}
        confirmed_record = dict(cancel_record, confirmed_at=3, confirmation={
            "detail": "all absent", "starts_confirmed": 1, "starts_never_started": 0,
            "provenance": None, "operation_id": "mo-" + "3" * 32, "sequence": 3})
        cases.append(("control:hold_active", synthetic_snapshot(**{
            "state.record": controls(hold=hold_record)})))
        cases.append(("control:cancel_requested", synthetic_snapshot(**{
            "state.record": controls(cancel=cancel_record)})))
        cases.append(("control:cancel_confirmed", synthetic_snapshot(**{
            "state.record": controls(cancel=confirmed_record)})))
        covered = set()
        for expected, snapshot in cases:
            holds = status_module.derive_holds(snapshot)
            self.assertIn(expected, codes_of(holds), expected)
            for hold in holds:
                self.assertEqual(hold["as_of"], snapshot["evaluated_at"])
            covered.add(expected)
        # Every vocabulary entry was exercised (contract problem codes come
        # from the core; one representative is exercised above).
        for kind, codes in vocabulary.items():
            for code in codes:
                self.assertIn("%s:%s" % (kind, code), covered, (kind, code))


class FHoldsRegressionTests(S2Fixture):

    def _codes(self, mission_id, **kwargs):
        return self.status(mission_id, **kwargs)["mission"]["holds"]["codes"]

    def test_F3_resolved_prerequisite_that_drifted_stays_a_hold(self):
        upstream = self.completed_prerequisite()
        dependent = self.dependent_on(upstream)
        evidence = self.make_local_complete(dependent, bind=upstream)
        self.assertNotIn("prerequisite:mission_state_prerequisite_drifted",
                         self._codes(dependent))
        cursor = self.cursor(dependent)
        # The prerequisite drifts (an EDIT on the upstream Mission); the
        # dependent's own document is untouched.
        self.edit(upstream, 1, objective="drifted")
        codes = self._codes(dependent)
        self.assertIn("prerequisite:mission_state_prerequisite_drifted", codes)
        self.assertEqual(self.cursor(dependent), cursor)
        entry = [h for h in self.status(dependent)["mission"]["holds"]["entries"]
                 if h["code"] == "mission_state_prerequisite_drifted"][0]
        self.assertEqual(entry["as_of"], self.clock())
        self.assertIn(upstream, entry["detail"])
        # The coordination observation cannot carry it (same-point rule)
        # and does not equivocate: the point is unchanged.
        before = self.observation(dependent)
        self.assertEqual(self.classify(before, self.floor_of(before)).freshness,
                         coordination_record.FRESHNESS_FRESH)
        self.assertIsNotNone(evidence)

    def test_F4_expired_proof_is_a_hold_without_durable_change(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self.make_local_complete(mission_id)
        self.assertNotIn("proof_requirement:STALE", self._codes(mission_id))
        cursor = self.cursor(mission_id)
        self.clock.advance(3601)  # past max_evidence_age_seconds
        codes = self._codes(mission_id)
        self.assertIn("proof_requirement:STALE", codes)
        self.assertIn("proof_freshness:stale", codes)
        self.assertIn("completion:proof_not_satisfied", codes)
        self.assertEqual(self.cursor(mission_id), cursor)
        observed = self.observation(mission_id)
        self.assertEqual(self.classify(observed, self.floor_of(observed)).freshness,
                         coordination_record.FRESHNESS_FRESH)

    def test_F5_active_to_blocked_external_report_changes_status(self):
        mission_id = self.ready_mission(required_dependencies=[])
        head = self.service.get_journal(mission_id)["cursor"]

        def report(value):
            return materialize(
                {"task": Counting({"value": value, "observed_at": self.clock()})},
                mission_id, head)

        cursor = self.cursor(mission_id)
        active = self.status(mission_id, inputs=report("ACTIVE"))["mission"]
        blocked = self.status(mission_id, inputs=report("BLOCKED"))["mission"]
        self.assertNotEqual(active, blocked)
        self.assertNotIn("report:task:BLOCKED", active["holds"]["codes"])
        self.assertIn("report:task:BLOCKED", blocked["holds"]["codes"])
        self.assertEqual(active["sources"]["task"]["value"], "ACTIVE")
        self.assertEqual(blocked["sources"]["task"]["value"], "BLOCKED")
        self.assertEqual(self.cursor(mission_id), cursor)
        self.assertEqual(coordination_observation.observation_point(
            self.observation(mission_id)), self.point(mission_id))

    def test_F6_two_blockers_sharing_a_key_are_two_conditions(self):
        mission_id = self.ready_mission(required_dependencies=[])
        first = self.call("open_blocker", mission_id, "flaky_network", "first")
        second = self.call("open_blocker", mission_id, "flaky_network", "second")
        observed = self.observation(mission_id)
        blocked = [c for c in observed.conditions
                   if c.kind == coordination_record.ATTENTION_BLOCKED]
        self.assertEqual(len(blocked), 2)
        self.assertEqual(len({c.key for c in blocked}), 2)
        details = sorted(c.detail for c in blocked)
        self.assertTrue(any(first["blocker_id"] in d and d.endswith(": first")
                            for d in details), details)
        self.assertTrue(any(second["blocker_id"] in d and d.endswith(": second")
                            for d in details), details)
        entries = [h for h in self.status(mission_id)["mission"]["holds"]["entries"]
                   if h["kind"] == "blocker"]
        self.assertEqual(len(entries), 2)

    def test_F7_condition_bound_applies_to_the_undeduplicated_count(self):
        mission_id = self.ready_mission(required_dependencies=[])
        bound = coordination_observation.MAX_OBSERVED_CONDITIONS
        # 62 distinct keys + 2 sharing a key = 64 conditions: still observed.
        for index in range(bound - 2):
            self.call("open_blocker", mission_id, "b%03d" % index, "many")
        self.call("open_blocker", mission_id, "shared", "one")
        self.call("open_blocker", mission_id, "shared", "two")
        observed = self.observation(mission_id)
        self.assertEqual(len(observed.conditions), bound)
        # One more (a third with the shared key) exceeds it: unavailable
        # with the reason, and status lists all 65.
        self.call("open_blocker", mission_id, "shared", "three")
        outcome = self.outcome(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("%d durable conditions" % (bound + 1), outcome.problem)
        entries = [h for h in self.status(mission_id)["mission"]["holds"]["entries"]
                   if h["kind"] == "blocker"]
        self.assertEqual(len(entries), bound + 1)


STORE_FILE_NAMES = {
    "workflow": "workflows.json",
    "delivery": "pr_delivery.json",
    "coordination": "coordination.json",
    "mission": "missions.json",
}


def refusing_open(file_name, error=PermissionError):
    """An ``os.open`` that refuses the store FILE ``file_name`` at the
    owner's own open (relative to its opened directory descriptor) and
    is the real ``os.open`` for everything else."""
    real_open = os.open

    def opening(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and path == file_name:
            raise error("denied at the owner's open")
        return real_open(path, flags, *args, **kwargs)

    return opening


class FEvidenceTests(S2Fixture):
    """R10-3 / correction-1 item 3b: the PUBLIC calls — ``read_status(
    service, id, ...)`` and ``MissionSnapshotSource(service).observe(id)``
    with NO directory argument — are correct because every store is read
    ONCE by its owner's ``read()``: absence needs positive evidence and
    every access or read error is unavailable."""

    def _link(self, name, target):
        link = os.path.join(self.base.name, name)
        os.symlink(target, link)
        return link

    def _mission_service_at(self, directory):
        return mission_service.MissionService(
            mission_store.MissionStore(directory), self.clock)

    def _copy_mission_store_to(self, directory):
        os.makedirs(directory, mode=0o700)
        mission_store.MissionStore(directory).save(self.store.load())

    def test_F8_symlink_to_an_inaccessible_target_is_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        if running_as_root():
            self.skipTest("OS permission refusals do not apply to root")
        hidden = os.path.join(self.base.name, "hidden")
        for name in ("workflows", "delivery", "coordination"):
            os.makedirs(os.path.join(hidden, name), mode=0o700)
        workflow_store.WorkflowStore(os.path.join(hidden, "workflows")).save(
            workflow_store.default_document())
        self._copy_mission_store_to(os.path.join(hidden, "missions"))
        links = {name: self._link("link-" + name, os.path.join(hidden, name))
                 for name in ("workflows", "delivery", "coordination", "missions")}
        service = self._mission_service_at(links["missions"])
        os.chmod(hidden, 0)
        try:
            result = status_module.read_status(
                service, mission_id,
                workflow_directory=links["workflows"],
                delivery_directory=links["delivery"],
                coordination_directory=links["coordination"])
            outcome = adapter.MissionSnapshotSource(service).observe(mission_id)
            unknown = adapter.MissionSnapshotSource(service).observe(
                "mn-" + "9" * 32)
        finally:
            os.chmod(hidden, 0o700)
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "unavailable",
                             name)
            self.assertIn("PermissionError", result["stores"][name]["problem"])
        self.assertEqual(result["mission"]["availability"], "unavailable")
        self.assertIn("MissionStoreError", result["mission"]["problem"])
        self.assertIn("PermissionError", result["mission"]["problem"])
        for observed in (outcome, unknown):
            self.assertEqual(observed.status,
                             coordination_record.OBSERVATION_UNAVAILABLE)
            self.assertIn("PermissionError", observed.problem)
        # Access restored: the same public calls are present / absent.
        self.assertEqual(status_module.read_status(service, mission_id)
                         ["mission"]["availability"], "present")
        self.assertEqual(adapter.MissionSnapshotSource(service).observe(
            "mn-" + "9" * 32).status, coordination_record.OBSERVATION_ABSENT)

    def test_F9_dangling_symlink_is_unavailable_never_absent_or_empty(self):
        mission_id = self.ready_mission(required_dependencies=[])
        links = {name: self._link("dangling-" + name,
                                  os.path.join(self.base.name, "gone", name))
                 for name in ("workflows", "delivery", "coordination", "missions")}
        service = self._mission_service_at(links["missions"])
        result = status_module.read_status(
            service, mission_id, workflow_directory=links["workflows"],
            delivery_directory=links["delivery"],
            coordination_directory=links["coordination"])
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "unavailable",
                             name)
            self.assertIn("symlink target", result["stores"][name]["problem"])
        self.assertEqual(result["mission"]["availability"], "unavailable")
        self.assertIn("symlink target", result["mission"]["problem"])
        outcome = adapter.MissionSnapshotSource(service).observe(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("symlink target", outcome.problem)
        # A symlink to a real, readable store is simply present.
        workflow_store.WorkflowStore(self.workflow_dir).save(
            workflow_store.default_document())
        good = self._link("good-workflows", self.workflow_dir)
        result = status_module.read_status(self.service, mission_id,
                                           workflow_directory=good)
        self.assertEqual(result["stores"]["workflow"]["availability"], "present")
        good_missions = self._link("good-missions", self.directory)
        self.assertEqual(status_module.read_status(
            self._mission_service_at(good_missions), mission_id)
            ["mission"]["availability"], "present")

    def test_F10_injected_access_failure_at_the_owner_read_is_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        store = workflow_store.WorkflowStore(self.workflow_dir)
        store.save(workflow_store.default_document())
        delivery_store.DeliveryStore(self.delivery_dir).save(
            delivery_store.default_document())
        coordination_store.CoordinationStore(self.coordination_dir).save(
            coordination_store.default_document(), 0)
        # (a) The owner's open is refused (permission lost at the read
        # itself) -> unavailable for that store, the others unaffected.
        for name in ("workflow", "delivery", "coordination"):
            with mock.patch.object(wa_atomic.os, "open",
                                   refusing_open(STORE_FILE_NAMES[name])):
                result = self.full_status(mission_id)
            self.assertEqual(result["stores"][name]["availability"],
                             "unavailable", name)
            self.assertEqual(result["stores"][name]["problem"], "PermissionError")
            for other in ("workflow", "delivery", "coordination"):
                if other != name:
                    self.assertEqual(result["stores"][other]["availability"],
                                     "present", (name, other))
            self.assertEqual(result["mission"]["availability"], "present")
        # (b) The read can never "default": with every loader forbidden
        # the status is still complete — one owner read, zero loads per
        # store — so no default is ever mistaken for an empty store.
        forbidden = [mock.patch.object(cls, "load", side_effect=AssertionError(
            "status must never call load"))
            for cls in (workflow_store.WorkflowStore, delivery_store.DeliveryStore,
                        coordination_store.CoordinationStore,
                        mission_store.MissionStore)]
        reads = {name: [] for name in STORE_FILE_NAMES}
        spies = []
        for name, cls in (("workflow", workflow_store.WorkflowStore),
                          ("delivery", delivery_store.DeliveryStore),
                          ("coordination", coordination_store.CoordinationStore),
                          ("mission", mission_store.MissionStore)):
            real = cls.read
            spies.append(mock.patch.object(
                cls, "read", autospec=True,
                side_effect=lambda self_, name=name, real=real: (
                    reads[name].append(1) or real(self_))))
        with contextlib.ExitStack() as stack:
            for patcher in forbidden + spies:
                stack.enter_context(patcher)
            result = self.full_status(mission_id)
        self.assertEqual(result["mission"]["availability"], "present")
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "present")
        self.assertEqual({name: len(calls) for name, calls in reads.items()},
                         {"workflow": 1, "delivery": 1, "coordination": 1,
                          "mission": 1})
        # (c) A genuine default on disk IS present with zero records.
        result = self.full_status(mission_id)["stores"]["workflow"]
        self.assertEqual(result["availability"], "present")
        self.assertEqual(result["records"], 0)
        # (d) A genuinely missing file with accessible ancestors: absent.
        os.remove(store.path)
        result = self.full_status(mission_id)["stores"]["workflow"]
        self.assertEqual(result["availability"], "absent")
        self.assertIsNone(result["problem"])

    def test_F11_mission_access_failure_is_unavailable_not_absent(self):
        mission_id = self.ready_mission(required_dependencies=[])
        source = adapter.MissionSnapshotSource(self.service)
        unknown_id = "mn-" + "9" * 32
        # Access fails at the Mission store's own read (its open is
        # refused): unavailable for status AND the adapter, for a known
        # and an unknown id alike — never absent, never a default.
        with mock.patch.object(wa_atomic.os, "open",
                               refusing_open(STORE_FILE_NAMES["mission"])):
            result = status_module.read_status(self.service, mission_id)
            outcome = source.observe(mission_id)
            unknown = source.observe(unknown_id)
        self.assertEqual(result["mission"]["availability"], "unavailable")
        self.assertIn("MissionStoreError", result["mission"]["problem"])
        self.assertIn("PermissionError", result["mission"]["problem"])
        for observed in (outcome, unknown):
            self.assertEqual(observed.status,
                             coordination_record.OBSERVATION_UNAVAILABLE)
            self.assertIn("MissionStoreError", observed.problem)
            self.assertIn("PermissionError", observed.problem)
        # Without the failure the same public calls are present / absent.
        self.assertEqual(status_module.read_status(self.service, mission_id)
                         ["mission"]["availability"], "present")
        self.assertEqual(status_module.read_status(self.service, unknown_id)
                         ["mission"]["availability"], "absent")
        self.assertEqual(source.observe(unknown_id).status,
                         coordination_record.OBSERVATION_ABSENT)
        # The Mission store's canonical validation still refuses a
        # malformed document: unavailable, naming the refusal.
        write_private(self.store.path, json.dumps({"version": 999}))
        result = status_module.read_status(self.service, mission_id)
        self.assertEqual(result["mission"]["availability"], "unavailable")
        self.assertIn("MissionStoreError", result["mission"]["problem"])
        outcome = source.observe(mission_id)
        self.assertEqual(outcome.status, coordination_record.OBSERVATION_UNAVAILABLE)
        self.assertIn("MissionStoreError", outcome.problem)
        # A genuinely absent Mission store (accessible ancestors): absent.
        os.remove(self.store.path)
        self.assertEqual(status_module.read_status(self.service, mission_id)
                         ["mission"]["availability"], "absent")
        self.assertEqual(source.observe(mission_id).status,
                         coordination_record.OBSERVATION_ABSENT)

    def test_F12_each_store_owner_read_result_directly(self):
        # The narrowly scoped owner read at every store, directly: a
        # ``ReadResult`` whose availability is one of the three pinned
        # values; PRESENT carries the same validated document ``load``
        # returns; ABSENT only for a genuinely missing file; UNAVAILABLE
        # (with the reason) for invalid content, a dangling link and an
        # exposed file; and ``load`` keeps its own behaviour (a default
        # on a missing file) untouched.
        self.assertEqual(wa_atomic.READ_AVAILABILITIES,
                         ("absent", "present", "unavailable"))
        owners = (
            ("workflows", workflow_store.WorkflowStore,
             workflow_store.default_document, lambda s, d: s.save(d), "StoreError"),
            ("delivery", delivery_store.DeliveryStore,
             delivery_store.default_document, lambda s, d: s.save(d), "StoreError"),
            ("coordination", coordination_store.CoordinationStore,
             coordination_store.default_document, lambda s, d: s.save(d, 0),
             "CoordinationStoreError"),
            ("missions", mission_store.MissionStore,
             mission_store.default_document, lambda s, d: s.save(d),
             "MissionStoreError"),
        )
        for name, cls, default, save, error_name in owners:
            directory = os.path.join(self.base.name, "owner-" + name)
            store = cls(directory)
            # Absent: the directory and file do not exist; load defaults.
            self.assertEqual(store.read(), wa_atomic.ReadResult(
                wa_atomic.READ_ABSENT, None, None), name)
            self.assertEqual(store.load(), default(), name)
            self.assertFalse(os.path.exists(directory), name)
            # Present: the validated document, equal to load's.
            save(store, default())
            read = store.read()
            self.assertEqual(read.availability, wa_atomic.READ_PRESENT, name)
            self.assertIsNone(read.problem, name)
            self.assertEqual(read.document, store.load(), name)
            # Invalid JSON: unavailable, naming the owner's error and reason.
            write_private(store.path, "{not json")
            read = store.read()
            self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE, name)
            self.assertIsNone(read.document, name)
            self.assertEqual(read.problem, "%s: invalid JSON" % error_name, name)
            # A document the owner's validator refuses: unavailable by class.
            write_private(store.path, json.dumps({"version": 999}))
            read = store.read()
            self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE, name)
            self.assertEqual(read.problem, error_name, name)
            # An exposed file (group/other readable): the owner's refusal,
            # naming the rule, on the OPENED file's mode (root included).
            os.remove(store.path)
            save(store, default())
            os.chmod(store.path, 0o644)
            read = store.read()
            self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE, name)
            self.assertEqual(read.problem, "%s: %s" % (
                error_name, wa_atomic.RULE_FILE_EXPOSED % 0o644), name)
            os.chmod(store.path, 0o600)
            self.assertEqual(store.read().availability,
                             wa_atomic.READ_PRESENT, name)
            # A dangling link as the store directory: unavailable, never
            # absent. Task 8 R24-1: ``load`` REFUSES there too, with this
            # owner's own error, and never initializes a default — the store
            # is present behind an unavailable target.
            dangling = cls(self._link("owner-dangling-" + name,
                                      os.path.join(self.base.name, "gone", name)))
            read = dangling.read()
            self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE, name)
            self.assertEqual(read.problem, "FileNotFoundError (symlink target)",
                             name)
            with self.assertRaises((workflow_store.StoreError, delivery_store.StoreError,
                                    coordination_store.CoordinationStoreError,
                                    mission_store.MissionStoreError)) as raised:
                dangling.load()
            self.assertEqual(type(raised.exception).__name__, error_name, name)
            self.assertIn("symlink target", str(raised.exception), name)
            self.assertFalse(os.path.lexists(os.path.join(self.base.name, "gone", name)),
                             name)
            # Genuinely missing file with an accessible directory: absent.
            os.remove(store.path)
            self.assertEqual(store.read(), wa_atomic.ReadResult(
                wa_atomic.READ_ABSENT, None, None), name)


# ====================================================================
# G. Correction 2 (round 11): holds independent of authority (R11-1),
#    the OPENED target is what is validated (R11-2), traversal semantics
#    (R11-3), decoder failures never escape (R11-4)
# ====================================================================


def write_bytes_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


# (name, class, default document, save, owner error name, file name,
#  directory policy, owner error class)
OWNERS = (
    ("workflow", workflow_store.WorkflowStore, workflow_store.default_document,
     lambda s, d: s.save(d), "StoreError", "workflows.json", False,
     workflow_store.StoreError),
    ("delivery", delivery_store.DeliveryStore, delivery_store.default_document,
     lambda s, d: s.save(d), "StoreError", "pr_delivery.json", False,
     delivery_store.StoreError),
    ("coordination", coordination_store.CoordinationStore,
     coordination_store.default_document, lambda s, d: s.save(d, 0),
     "CoordinationStoreError", "coordination.json", True,
     coordination_store.CoordinationStoreError),
    ("mission", mission_store.MissionStore, mission_store.default_document,
     lambda s, d: s.save(d), "MissionStoreError", "missions.json", True,
     mission_store.MissionStoreError),
)


class GOwnerFixture(S2Fixture):

    def owner_store(self, owner, label=""):
        name, cls, default, save, _, _, _, _ = owner
        directory = os.path.join(self.base.name, "g-%s%s" % (name, label))
        store = cls(directory)
        save(store, default())
        return store

    def status_for(self, mission_id, stores):
        """A status whose configured store directories are ``stores``
        (workflow / delivery / coordination) and whose Mission service
        reads the given Mission store."""
        service = self.service
        if "mission" in stores:
            service = mission_service.MissionService(stores["mission"], self.clock)
        return status_module.read_status(
            service, mission_id,
            workflow_directory=getattr(stores.get("workflow"), "directory", None),
            delivery_directory=getattr(stores.get("delivery"), "directory", None),
            coordination_directory=getattr(stores.get("coordination"), "directory",
                                           None))

    def assert_store_result(self, result, name, availability, problem_part=None):
        view = (result["mission"] if name == "mission" else result["stores"][name])
        self.assertEqual(view["availability"], availability, (name, view["problem"]))
        if problem_part is not None:
            self.assertIn(problem_part, view["problem"], name)

    def load_outcome(self, store):
        """``load``'s own policy outcome: ("refused", <exception>) or
        ("loaded", <document>)."""
        try:
            return "loaded", store.load()
        except Exception as exc:  # noqa: BLE001 - the owner's refusal, whatever it is
            return "refused", exc


class GHoldsAuthorityMatrixTests(S2Fixture):
    """R11-1, as a class: for every authority standing, every hold family
    whose underlying fact exists is present — nothing the core evaluated
    is hidden by an authority that is not live."""

    FAMILIES = ("readiness:NOT_READY", "proof_requirement:STALE",
                "proof_freshness:stale", "completion:proof_not_satisfied",
                "prerequisite:mission_state_prerequisite_drifted",
                "dependency_slot:UNBOUND", "report:task:BLOCKED",
                "source_standing:review:unavailable")

    def _laden(self, mission_id, upstream):
        """Every hold family's underlying fact on one Mission: an accepted
        proof that will go stale, a bound and resolved prerequisite that
        will drift, a second slot left unbound, no readiness observation,
        an open blocker."""
        ms = self.ms
        artifact = self.call("record_artifact", mission_id, "test_log",
                             mission_record.ARTIFACT_ROLE_VERIFICATION,
                             ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A,
                             True, [])
        evidence = self.call("submit_evidence", mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                             "e" * 64, [artifact["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        bound = self.call("bind_dependency", mission_id, "upstream", upstream)
        self.call("resolve_dependency", mission_id, bound["dependency_id"],
                  evidence["evidence_id"])
        blocker = self.call("open_blocker", mission_id, "flaky_network", "flaky")
        return blocker

    def _dependencies(self, upstream):
        digest = self.store.load()["missions"][upstream]["revisions"][0][
            "proposal_digest_sha256"]
        second = copy.deepcopy(contract()["required_dependencies"][0])
        second["key"] = "second"
        return [{"key": "upstream", "kind": "MISSION",
                 "target": {"form": "EXACT_MISSION", "mission_id": upstream,
                            "revision": 1, "proposal_digest_sha256": digest}},
                second]

    def _report(self, mission_id, value):
        head = self.service.get_journal(mission_id)["cursor"]
        return materialize(
            {"task": Counting({"value": value, "observed_at": self.clock()})},
            mission_id, head)

    def _codes(self, mission_id):
        return self.status(mission_id, inputs=self._report(mission_id, "BLOCKED"))[
            "mission"]["holds"]["codes"]

    def _cell(self, standing, expires_in=100):
        """A laden Mission under one authority standing; returns
        (mission_id, blocker, cursor before the standing changed). For
        "expired" the authority expires ``expires_in`` seconds after the
        approval (100: expired by the final 3601 s advance; larger: still
        live after it, so a caller can expire it separately)."""
        upstream = self.completed_prerequisite()
        dependencies = self._dependencies(upstream)
        if standing == "expired":
            created = self.propose(proof_contract=contract(
                required_dependencies=dependencies))
            mission_id = created["mission_id"]
            self.approve(mission_id, 1, expires_at=self.clock() + expires_in)
            self.clock.advance(1)
            self.call("activate_proof_contract", mission_id)
        else:
            mission_id = self.ready_mission(required_dependencies=dependencies)
        blocker = self._laden(mission_id, upstream)
        self.edit(upstream, 1, objective="drifted")  # the prerequisite drifts
        cursor = self.cursor(mission_id)
        if standing == "superseded":
            # An EDIT of this Mission: the activation binds revision 1,
            # the Mission is at revision 2 — the contract is stale and
            # the authority no longer applies.
            self.edit(mission_id, 1, proof_contract=contract(
                required_dependencies=dependencies), objective="edited")
            cursor = self.cursor(mission_id)
        self.clock.advance(3601)  # proof stale; the expiring authority expired
        return mission_id, blocker, cursor

    def test_G1_every_hold_family_survives_every_authority_standing(self):
        for standing in ("live", "expired", "superseded"):
            mission_id, blocker, cursor = self._cell(standing)
            mission = self.status(mission_id, inputs=self._report(
                mission_id, "BLOCKED"))["mission"]
            codes = mission["holds"]["codes"]
            for family in self.FAMILIES:
                self.assertIn(family, codes, (standing, family))
            self.assertIn("blocker:%s" % blocker["severity"], codes, standing)
            # The authority-dependent facts are reported truthfully.
            contract_view = mission["holds"]["contract"]
            if standing == "live":
                self.assertTrue(contract_view["authority_live"])
                self.assertNotIn("authority:authority_not_live", codes)
                self.assertNotIn("closure_eligibility:not_evaluated", codes)
                self.assertIn("closure_eligibility:mission_state_proof_not_satisfied",
                              codes)
            else:
                self.assertFalse(contract_view["authority_live"])
                self.assertIn("authority:authority_not_live", codes, standing)
                self.assertIn("closure_eligibility:not_evaluated", codes, standing)
                entry = [h for h in mission["holds"]["entries"]
                         if h["kind"] == "authority"][0]
                self.assertIn(contract_view["problem"], entry["detail"])
                self.assertEqual(entry["as_of"], self.clock())
            if standing == "expired":
                self.assertEqual(mission["authorizations"][0]["standing"],
                                 "expired")
                self.assertIsNone(mission["live_authorization_id"])
            if standing == "superseded":
                self.assertIn("contract:contract_not_current", codes)
            # Expiry and staleness changed no durable fact.
            self.assertEqual(self.cursor(mission_id), cursor, standing)
            observed = self.observation(mission_id)
            self.assertEqual(self.classify(observed, self.floor_of(observed)).freshness,
                             coordination_record.FRESHNESS_FRESH, standing)

    def test_G1b_never_approved_mission_reports_what_exists_and_nothing_else(self):
        mission_id = self.propose()["mission_id"]
        mission = self.status(mission_id)["mission"]
        codes = mission["holds"]["codes"]
        self.assertIn("decision:awaiting_decision", codes)
        for source in status_module.SOURCES:
            self.assertIn("source_standing:%s:unavailable" % source, codes)
        # No contract, no authority: no proof/readiness/dependency facts
        # exist, and no authority or closure hold is invented for them.
        self.assertFalse(any(c.startswith(("proof_", "readiness:", "dependency_",
                                           "prerequisite:", "authority:",
                                           "closure_eligibility:", "contract:"))
                             for c in codes), codes)
        self.assertIsNone(mission["sources"]["task"]["value"])

    def test_G1c_expiry_alone_reveals_no_new_fact_and_hides_none(self):
        # The SAME laden Mission observed BEFORE its authority expires
        # (still live after the staleness advance) and AFTER it expires:
        # the authority-independent holds are identical; the only
        # additions are the authority hold and the not-evaluated closure
        # hold, the only removals the closure failures the core evaluates
        # under live authority; the cursor is unchanged by the expiry.
        mission_id, _, cursor = self._cell("expired", expires_in=5000)
        before_view = self.status(mission_id, inputs=self._report(
            mission_id, "BLOCKED"))["mission"]
        before = set(before_view["holds"]["codes"])
        self.assertTrue(before_view["holds"]["contract"]["authority_live"])
        self.assertEqual(before_view["authorizations"][0]["standing"], "live")
        self.assertNotIn("authority:authority_not_live", before)
        self.assertNotIn("closure_eligibility:not_evaluated", before)
        self.assertEqual(self.cursor(mission_id), cursor)
        self.clock.advance(2000)  # past expires_at; nothing else changes
        after_view = self.status(mission_id, inputs=self._report(
            mission_id, "BLOCKED"))["mission"]
        after = set(after_view["holds"]["codes"])
        self.assertFalse(after_view["holds"]["contract"]["authority_live"])
        self.assertEqual(after_view["authorizations"][0]["standing"], "expired")
        self.assertEqual(self.cursor(mission_id), cursor)

        # The authority-DEPENDENT holds: the authority hold, the closure
        # evaluation, and the contract kind's core problem code (the
        # core reports an expired authority as its contract problem);
        # ``contract:contract_not_current`` is revision-dependent and
        # stays in the independent set.
        def independent(codes):
            return {c for c in codes
                    if not c.startswith(("authority:", "closure_eligibility:"))
                    and (not c.startswith("contract:")
                         or c == "contract:contract_not_current")}

        self.assertEqual(independent(before), independent(after))
        for family in self.FAMILIES:
            self.assertIn(family, independent(after))
        self.assertEqual(after - before, {"authority:authority_not_live",
                                          "closure_eligibility:not_evaluated",
                                          "contract:mission_state_contract_stale"})
        self.assertTrue(before - after)
        self.assertTrue(all(c.startswith("closure_eligibility:")
                            for c in before - after), before - after)


class GOpenedTargetTests(GOwnerFixture):
    """R11-2, as a class: every owner validates the target it actually
    OPENED — the directory through its descriptor (Mission and
    coordination policy) and the file through its descriptor (every
    owner's file-mode rule) — so nothing swapped in between a check and
    the open is trusted, valid readable links stay supported, and the
    read's policy is exactly ``load``'s."""

    SWAPS = ("exposed_file", "symlink_to_exposed_file", "symlink_to_directory",
             "directory", "fifo")

    def _swap(self, store, kind):
        path = store.path
        elsewhere = os.path.join(self.base.name, "elsewhere-" + kind)
        if kind == "exposed_file":
            exposed = path + ".exposed"
            with open(path, "rb") as source:
                data = source.read()
            write_bytes_private(exposed, data)
            os.chmod(exposed, 0o644)
            os.replace(exposed, path)
        elif kind == "symlink_to_exposed_file":
            with open(path, "rb") as source:
                data = source.read()
            write_bytes_private(elsewhere, data)
            os.chmod(elsewhere, 0o644)
            os.remove(path)
            os.symlink(elsewhere, path)
        elif kind == "symlink_to_directory":
            os.makedirs(elsewhere, mode=0o700, exist_ok=True)
            os.remove(path)
            os.symlink(elsewhere, path)
        elif kind == "directory":
            os.remove(path)
            os.mkdir(path, 0o700)
        elif kind == "fifo":
            os.remove(path)
            os.mkfifo(path, 0o600)
        else:
            raise AssertionError(kind)

    def _expected_rule(self, kind, error_name):
        if kind in ("exposed_file", "symlink_to_exposed_file"):
            return "%s: %s" % (error_name, wa_atomic.RULE_FILE_EXPOSED % 0o644)
        return "%s: %s" % (error_name, wa_atomic.RULE_NOT_REGULAR_FILE)

    def test_G2_swap_between_any_check_and_the_open_is_refused_by_rule(self):
        for owner in OWNERS:
            name, _, _, _, error_name, file_name, _, _ = owner
            for kind in self.SWAPS:
                store = self.owner_store(owner, "-" + kind)
                real_open = os.open
                swapped = []

                def opening(path, flags, *args, **kwargs):
                    # The boundary: the moment the owner opens the store
                    # FILE relative to its opened directory, the file is
                    # swapped underneath it (after any check could have
                    # run and before the open).
                    if (kwargs.get("dir_fd") is not None and path == file_name
                            and not swapped):
                        swapped.append(kind)
                        self._swap(store, kind)
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(wa_atomic.os, "open", opening):
                    read = store.read()
                self.assertEqual(swapped, [kind], (name, kind))
                self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE,
                                 (name, kind, read.problem))
                self.assertEqual(read.problem, self._expected_rule(kind, error_name),
                                 (name, kind))
                # The same swap already in place (no race) refuses the same.
                again = store.read()
                self.assertEqual(again, read, (name, kind))
                # ``load``'s own policy refuses the same target (a FIFO is
                # not compared: ``load`` would block on it).
                if kind != "fifo":
                    self.assertEqual(self.load_outcome(store)[0], "refused",
                                     (name, kind))

    def test_G2b_exposed_directory_follows_each_owner_policy(self):
        for owner in OWNERS:
            name, cls, _, _, error_name, _, directory_policy, _ = owner
            store = self.owner_store(owner, "-dir")
            os.chmod(store.directory, 0o755)
            try:
                read = store.read()
                load = self.load_outcome(store)
                if directory_policy:
                    self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE,
                                     name)
                    self.assertEqual(read.problem, "%s: %s" % (
                        error_name, wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755), name)
                    self.assertEqual(load[0], "refused", name)
                    self.assertIsInstance(load[1], owner[7])
                    if name == "mission":
                        service = mission_service.MissionService(store, self.clock)
                        result = status_module.read_status(service, "mn-" + "9" * 32)
                        self.assertEqual(result["mission"]["availability"],
                                         "unavailable")
                        self.assertIn(wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755,
                                      result["mission"]["problem"])
                        outcome = adapter.MissionSnapshotSource(service).observe(
                            "mn-" + "9" * 32)
                        self.assertEqual(outcome.status,
                                         coordination_record.OBSERVATION_UNAVAILABLE)
                        self.assertIn(wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755,
                                      outcome.problem)
                else:
                    # Workflow and delivery: file-mode rules only, exactly
                    # as their ``load``.
                    self.assertEqual(read.availability, wa_atomic.READ_PRESENT, name)
                    self.assertEqual(load[0], "loaded", name)
                    self.assertEqual(read.document, load[1], name)
                # A directory link to an exposed directory: the OPENED
                # target's mode decides, the same way.
                link = os.path.join(self.base.name, "g-link-dir-" + name)
                os.symlink(store.directory, link)
                linked = cls(link).read()
                self.assertEqual(linked.availability, read.availability, name)
                self.assertEqual(linked.problem, read.problem, name)
            finally:
                os.chmod(store.directory, 0o700)
            self.assertEqual(store.read().availability, wa_atomic.READ_PRESENT, name)

    def test_G2c_read_policy_is_load_policy_on_every_opened_target(self):
        # Parity, outcome by outcome: wherever ``load`` refuses, ``read``
        # is unavailable; wherever ``load`` loads, ``read`` is present
        # with the same document. Readable links stay supported.
        for owner in OWNERS:
            name, cls, default, save, error_name, file_name, _, error_class = owner
            situations = {}
            # A valid store; a link to its directory; a link to its file.
            plain = self.owner_store(owner, "-plain")
            situations["plain"] = plain
            directory_link = os.path.join(self.base.name, "g-dlink-" + name)
            os.symlink(plain.directory, directory_link)
            situations["directory_link"] = cls(directory_link)
            linked = os.path.join(self.base.name, "g-flink-" + name)
            os.makedirs(linked, mode=0o700)
            os.symlink(plain.path, os.path.join(linked, file_name))
            situations["file_link"] = cls(linked)
            # Refusals on the opened target.
            for kind in ("exposed_file", "symlink_to_exposed_file",
                         "symlink_to_directory", "directory"):
                store = self.owner_store(owner, "-parity-" + kind)
                self._swap(store, kind)
                situations[kind] = store
            for label, store in situations.items():
                outcome, payload = self.load_outcome(store)
                read = store.read()
                if outcome == "loaded":
                    self.assertEqual(read.availability, wa_atomic.READ_PRESENT,
                                     (name, label, read.problem))
                    self.assertEqual(read.document, payload, (name, label))
                else:
                    self.assertIsInstance(payload, error_class, (name, label))
                    self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE,
                                     (name, label))
                    self.assertTrue(read.problem.startswith(error_name + ": "),
                                    (name, label, read.problem))
            # The one documented divergence: a missing file, where ``load``
            # supplies a default and ``read`` reports absence.
            os.remove(plain.path)
            self.assertEqual(self.load_outcome(plain), ("loaded", default()))
            self.assertEqual(plain.read(), wa_atomic.ReadResult(
                wa_atomic.READ_ABSENT, None, None), name)

    def test_G2d_public_mission_calls_refuse_a_swapped_mission_store(self):
        mission_id = self.ready_mission(required_dependencies=[])
        for kind in self.SWAPS:
            directory = os.path.join(self.base.name, "g-public-" + kind)
            os.makedirs(directory, mode=0o700)
            store = mission_store.MissionStore(directory)
            store.save(self.store.load())
            self._swap(store, kind)
            service = mission_service.MissionService(store, self.clock)
            rule = self._expected_rule(kind, "MissionStoreError")
            result = status_module.read_status(service, mission_id)
            self.assertEqual(result["mission"]["availability"], "unavailable", kind)
            self.assertIn(rule, result["mission"]["problem"], kind)
            outcome = adapter.MissionSnapshotSource(service).observe(mission_id)
            self.assertEqual(outcome.status,
                             coordination_record.OBSERVATION_UNAVAILABLE, kind)
            self.assertIn(rule, outcome.problem, kind)


class GTraversalTests(GOwnerFixture):
    """R11-3, as a class: the read classifies a path the way the
    filesystem traverses it — nothing is collapsed before the symbolic
    links along it are followed — for every owner and both public
    Mission calls; ``absent`` only with genuine absence."""

    def _layout(self, owner):
        name = owner[0]
        real = self.owner_store(owner, "-real")
        base = self.base.name
        dangling = os.path.join(base, "g-dangling-" + name)
        os.symlink(os.path.join(base, "gone-" + name), dangling)
        file_link = os.path.join(base, "g-filelink-" + name)
        os.symlink(real.path, file_link)
        real_name = os.path.basename(real.directory)
        return real, {
            # (path, expected availability, expected problem fragment)
            "dangling_link_dotdot": (
                os.path.join(dangling, os.pardir, real_name), "unavailable",
                "FileNotFoundError (symlink target)"),
            "file_link_dotdot": (
                os.path.join(file_link, os.pardir, real_name), "unavailable",
                "NotADirectoryError"),
            "real_dotdot_missing": (
                os.path.join(real.directory, os.pardir, "g-missing-" + name),
                "absent", None),
            "real_dotdot_real": (
                os.path.join(real.directory, os.pardir, real_name), "present", None),
            "trailing_separator": (real.directory + os.sep, "present", None),
            "dot_components": (
                os.path.join(real.directory, os.curdir, os.curdir), "present", None),
            "doubled_separator": (
                real.directory.replace(base, base + os.sep, 1), "present", None),
        }

    def test_G3_owner_reads_follow_the_filesystem_traversal(self):
        for owner in OWNERS:
            name, cls = owner[0], owner[1]
            real, cases = self._layout(owner)
            for label, (path, availability, fragment) in cases.items():
                read = cls(path).read()
                self.assertEqual(read.availability, availability,
                                 (name, label, read.problem))
                if fragment is not None:
                    self.assertIn(fragment, read.problem, (name, label))
                if availability == "present":
                    self.assertEqual(read.document, real.load(), (name, label))
                # The old classification (an ``abspath`` that collapses
                # ``..`` first) would have looked at THIS path instead:
                collapsed = os.path.abspath(path)
                if label == "dangling_link_dotdot":
                    self.assertTrue(os.path.isdir(collapsed))
                    self.assertEqual(wa_atomic.path_access(path)[0], "unavailable")
                    self.assertEqual(wa_atomic.path_access(collapsed)[0], "present")

    def test_G3b_relative_paths_from_a_changed_working_directory(self):
        previous = os.getcwd()
        os.chdir(self.base.name)
        self.addCleanup(os.chdir, previous)
        for owner in OWNERS:
            name, cls = owner[0], owner[1]
            real, _ = self._layout(owner)
            real_name = os.path.basename(real.directory)
            relative_cases = (
                (real_name, "present", None),
                (os.path.join(os.curdir, real_name), "present", None),
                (os.path.join("g-dangling-" + name, os.pardir, real_name),
                 "unavailable", "FileNotFoundError (symlink target)"),
                (os.path.join("g-filelink-" + name, os.pardir, real_name),
                 "unavailable", "NotADirectoryError"),
                (os.path.join(real_name, os.pardir, "g-missing-" + name),
                 "absent", None),
                ("g-missing-" + name, "absent", None),
            )
            for path, availability, fragment in relative_cases:
                read = cls(path).read()
                self.assertEqual(read.availability, availability,
                                 (name, path, read.problem))
                if fragment is not None:
                    self.assertIn(fragment, read.problem, (name, path))

    def test_G3c_public_mission_calls_follow_the_traversal(self):
        mission_id = self.ready_mission(required_dependencies=[])
        owner = OWNERS[3]
        real, cases = self._layout(owner)
        real_store = mission_store.MissionStore(real.directory)
        real_store.save(self.store.load())
        for label, (path, availability, fragment) in cases.items():
            service = mission_service.MissionService(
                mission_store.MissionStore(path), self.clock)
            result = status_module.read_status(service, mission_id)["mission"]
            outcome = adapter.MissionSnapshotSource(service).observe(mission_id)
            if availability == "present":
                self.assertEqual(result["availability"], "present", label)
                self.assertEqual(outcome.status,
                                 coordination_record.OBSERVATION_OBSERVED, label)
            elif availability == "absent":
                self.assertEqual(result["availability"], "absent", label)
                self.assertEqual(outcome.status,
                                 coordination_record.OBSERVATION_ABSENT, label)
            else:
                self.assertEqual(result["availability"], "unavailable", label)
                self.assertIn(fragment, result["problem"], label)
                self.assertEqual(outcome.status,
                                 coordination_record.OBSERVATION_UNAVAILABLE, label)
                self.assertIn(fragment, outcome.problem, label)


class GDescriptorOwnershipTests(GOwnerFixture):
    """Item 2b (Supervisor shared-reader audit): single descriptor
    ownership across open/read/close on success and failure, and the
    directory boundary re-validated across file admission for the owners
    with a directory policy."""

    def _spied_read(self, store, file_name, fail_read):
        """Runs ``store.read()`` with: a spy on every ``os.close`` (count
        per descriptor), the store file's descriptor recorded at the
        owner's open, an ``OSError`` injected in the middle of the read
        when ``fail_read``, and a SENTINEL descriptor opened the moment
        the store descriptor is first closed — it reuses that number, so
        a second close would close the sentinel."""
        real_open, real_read, real_close = os.open, os.read, os.close
        sentinel_path = os.path.join(self.base.name, "sentinel")
        write_bytes_private(sentinel_path, b"sentinel")
        state = {"closes": [], "reads": 0}

        def opening(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if kwargs.get("dir_fd") is not None and path == file_name:
                state["fd"] = descriptor
            return descriptor

        def reading(descriptor, size):
            if descriptor == state.get("fd"):
                state["reads"] += 1
                if fail_read:
                    raise OSError(errno.EIO, "mid-read failure")
            return real_read(descriptor, size)

        def closing(descriptor):
            state["closes"].append(descriptor)
            real_close(descriptor)
            if descriptor == state.get("fd") and "sentinel" not in state:
                state["sentinel"] = real_open(sentinel_path, os.O_RDONLY)

        with mock.patch.object(wa_atomic.os, "open", opening), \
                mock.patch.object(wa_atomic.os, "read", reading), \
                mock.patch.object(wa_atomic.os, "close", closing):
            result = store.read()
        self.assertIn("fd", state, "the owner never opened the store file")
        self.assertIn("sentinel", state, "the store descriptor was never closed")
        # The sentinel reused the store descriptor's number and is still
        # valid: nothing closed it a second time.
        self.assertEqual(state["sentinel"], state["fd"])
        os.fstat(state["sentinel"])
        real_close(state["sentinel"])
        # Exactly one close per descriptor (the file's and the directory's).
        self.assertEqual(state["closes"].count(state["fd"]), 1)
        self.assertEqual(len(state["closes"]), len(set(state["closes"])))
        self.assertEqual(len(state["closes"]), 2)
        return result, state

    def test_G5_mid_read_failure_is_unavailable_with_one_close_per_descriptor(self):
        for owner in OWNERS:
            name, _, _, _, _, file_name, _, _ = owner
            store = self.owner_store(owner, "-midread")
            result, state = self._spied_read(store, file_name, fail_read=True)
            self.assertEqual(result, wa_atomic.ReadResult(
                wa_atomic.READ_UNAVAILABLE, None, "OSError"), name)
            self.assertEqual(state["reads"], 1, name)
            # The same discipline on the success path.
            result, state = self._spied_read(store, file_name, fail_read=False)
            self.assertEqual(result.availability, wa_atomic.READ_PRESENT, name)
            self.assertEqual(result.document, store.load(), name)
            # And a failing CLOSE after a successful read is itself named.
            real_close = os.close

            def failing_close(descriptor, fd=state["fd"]):
                real_close(descriptor)
                raise OSError(errno.EIO, "close failure")

            with mock.patch.object(wa_atomic.os, "close", failing_close):
                result = store.read()
            self.assertEqual(result, wa_atomic.ReadResult(
                wa_atomic.READ_UNAVAILABLE, None, "OSError"), name)

    def test_G5b_public_paths_report_a_mid_read_failure_as_unavailable(self):
        mission_id = self.ready_mission(required_dependencies=[])
        stores = {owner[0]: self.owner_store(owner, "-midread-public")
                  for owner in OWNERS}
        stores["mission"].save(self.store.load())
        real_read = os.read
        for owner in OWNERS:
            name, _, _, _, _, file_name, _, _ = owner
            target = stores[name].path
            inode = os.stat(target).st_ino

            def reading(descriptor, size, inode=inode):
                if os.fstat(descriptor).st_ino == inode:
                    raise OSError(errno.EIO, "mid-read failure")
                return real_read(descriptor, size)

            with mock.patch.object(wa_atomic.os, "read", reading):
                result = self.status_for(mission_id, stores)
                if name == "mission":
                    outcome = adapter.MissionSnapshotSource(
                        mission_service.MissionService(stores["mission"],
                                                       self.clock)).observe(mission_id)
            self.assert_store_result(result, name, "unavailable", "OSError")
            for other in OWNERS:
                if other[0] != name:
                    self.assert_store_result(result, other[0], "present")
            if name == "mission":
                self.assertEqual(outcome.status,
                                 coordination_record.OBSERVATION_UNAVAILABLE)
                self.assertIn("OSError", outcome.problem)
        # Without the injection every store is present again (no
        # descriptor was leaked or closed twice along the way).
        result = self.status_for(mission_id, stores)
        for owner in OWNERS:
            self.assert_store_result(result, owner[0], "present")

    def test_G5c_directory_exposure_between_first_check_and_admission(self):
        mission_id = self.ready_mission(required_dependencies=[])
        real_read = os.read
        for owner in OWNERS:
            name, cls, _, _, error_name, file_name, directory_policy, _ = owner
            store = self.owner_store(owner, "-flip")
            if name == "mission":
                store.save(self.store.load())
            flipped = []

            def reading(descriptor, size, store=store, flipped=flipped):
                # The boundary: the directory passed its first check and
                # the file is open; its mode flips before admission.
                if not flipped:
                    flipped.append(True)
                    os.chmod(store.directory, 0o755)
                return real_read(descriptor, size)

            try:
                with mock.patch.object(wa_atomic.os, "read", reading):
                    read = store.read()
                self.assertEqual(flipped, [True], name)
                if directory_policy:
                    self.assertEqual(read, wa_atomic.ReadResult(
                        wa_atomic.READ_UNAVAILABLE, None, "%s: %s" % (
                            error_name, wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755)),
                        name)
                    self.assertEqual(self.load_outcome(store)[0], "refused", name)
                    if name == "mission":
                        os.chmod(store.directory, 0o700)
                        flipped[:] = []
                        service = mission_service.MissionService(store, self.clock)
                        with mock.patch.object(wa_atomic.os, "read", reading):
                            result = status_module.read_status(service, mission_id)
                        self.assertEqual(result["mission"]["availability"],
                                         "unavailable")
                        self.assertIn(wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755,
                                      result["mission"]["problem"])
                else:
                    # File-only policy, exactly as ``load``.
                    self.assertEqual(read.availability, wa_atomic.READ_PRESENT, name)
                    self.assertEqual(self.load_outcome(store), ("loaded", read.document),
                                     name)
            finally:
                os.chmod(store.directory, 0o700)
            self.assertEqual(store.read().availability, wa_atomic.READ_PRESENT, name)


class ReaderProbe(object):
    """Correction 3: one injection point into the production reader.
    Every ``os`` call the reader makes is wrapped; the probe tracks the
    directory and file descriptors it opens, injects ONE failure at the
    requested stage, counts every close per descriptor and records
    whether anything was raised."""

    STAGES = (
        "none", "dir_open_error", "dir_fstat_error", "file_open_error",
        "file_missing_lstat_error", "file_fstat_error", "read_error",
        "file_close_error", "dir_close_error", "parse_other_exception",
        "dir_exposed_after_first_check", "dir_exposed_after_read",
    )

    def __init__(self, store, file_name, stage, error=None):
        self.store = store
        self.file_name = file_name
        self.stage = stage
        self.error = error or OSError(errno.EIO, "injected at " + stage)
        self.directory_fd = None
        self.file_fd = None
        self.closes = []
        self.fired = []
        self.real = {name: getattr(os, name)
                     for name in ("open", "fstat", "lstat", "read", "close")}
        self.real_loads = json.loads

    def _fire(self, stage):
        if self.stage == stage and not self.fired:
            self.fired.append(stage)
            raise self.error

    def _expose(self, stage):
        if self.stage == stage and not self.fired:
            self.fired.append(stage)
            os.chmod(self.store.directory, 0o755)

    def opening(self, path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is None and flags & os.O_DIRECTORY:
            self._fire("dir_open_error")
            self.directory_fd = self.real["open"](path, flags, *args, **kwargs)
            return self.directory_fd
        if kwargs.get("dir_fd") is not None and path == self.file_name:
            self._fire("file_open_error")
            self.file_fd = self.real["open"](path, flags, *args, **kwargs)
            return self.file_fd
        return self.real["open"](path, flags, *args, **kwargs)

    def fstatting(self, descriptor):
        if descriptor == self.directory_fd:
            if self.file_fd is None:
                self._fire("dir_fstat_error")
            return self.real["fstat"](descriptor)
        if descriptor == self.file_fd:
            self._fire("file_fstat_error")
            self._expose("dir_exposed_after_first_check")
            return self.real["fstat"](descriptor)
        return self.real["fstat"](descriptor)

    def lstatting(self, path, *args, **kwargs):
        if kwargs.get("dir_fd") == self.directory_fd and path == self.file_name:
            self._fire("file_missing_lstat_error")
            self._expose("dir_exposed_after_first_check")
        return self.real["lstat"](path, *args, **kwargs)

    def reading(self, descriptor, size):
        if descriptor == self.file_fd:
            self._fire("read_error")
            self._expose("dir_exposed_after_read")
        return self.real["read"](descriptor, size)

    def closing(self, descriptor):
        self.closes.append(descriptor)
        self.real["close"](descriptor)
        if descriptor == self.file_fd:
            self._fire("file_close_error")
        if descriptor == self.directory_fd:
            self._fire("dir_close_error")

    def parsing(self, text, *args, **kwargs):
        self._fire("parse_other_exception")
        return self.real_loads(text, *args, **kwargs)

    def run(self):
        patches = [mock.patch.object(wa_atomic.os, "open", self.opening),
                   mock.patch.object(wa_atomic.os, "fstat", self.fstatting),
                   mock.patch.object(wa_atomic.os, "lstat", self.lstatting),
                   mock.patch.object(wa_atomic.os, "read", self.reading),
                   mock.patch.object(wa_atomic.os, "close", self.closing),
                   mock.patch.object(wa_atomic.json, "loads", self.parsing)]
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            try:
                return self.store.read(), None
            except BaseException as exc:  # noqa: BLE001 - recorded, asserted
                return None, exc


class GReaderPathTableTests(GOwnerFixture):
    """Correction 3: EVERY return path of the shared reader, injected
    through the production reader for all four owners, with
    policy-appropriate expectations; each row asserts the availability,
    that the problem names the failure, exactly one close per opened
    descriptor, and that nothing was raised. The same rows are the
    evidence's path table."""

    # (row, stage, file state, expectation for file-only owners,
    #  expectation for directory-policy owners); an expectation is
    # (availability, problem) where problem is exact text, a callable of
    # the owner's error name, or None for "no problem".
    def _rows(self):
        rule_dir = lambda e: "%s: %s" % (e, wa_atomic.RULE_DIRECTORY_EXPOSED % 0o755)  # noqa: E731
        rule_file = lambda e: "%s: %s" % (e, wa_atomic.RULE_FILE_EXPOSED % 0o644)  # noqa: E731
        rule_reg = lambda e: "%s: %s" % (e, wa_atomic.RULE_NOT_REGULAR_FILE)  # noqa: E731
        rule_json = lambda e: "%s: %s" % (e, wa_atomic.RULE_INVALID_JSON)  # noqa: E731
        rule_type = lambda e: "%s: TypeError" % e  # noqa: E731
        absent = ("absent", None)
        present = ("present", None)
        oserror = ("unavailable", "OSError")
        return (
            # -- the directory open --
            ("P1 directory missing (genuine)", "none", "dir_missing", absent, absent),
            ("P2 directory is a dangling link", "none", "dir_dangling",
             ("unavailable", "FileNotFoundError (symlink target)"),
             ("unavailable", "FileNotFoundError (symlink target)")),
            ("P3 directory open: other OSError", "dir_open_error", "valid",
             oserror, oserror),
            # -- the opened directory: fstat and policy --
            ("P4 directory fstat error", "dir_fstat_error", "valid", oserror,
             oserror),
            ("P5 directory exposed at the first check", "none", "dir_exposed",
             present, ("unavailable", rule_dir)),
            # -- the file open --
            ("P6 file missing (genuine) -> absent", "none", "file_missing",
             absent, absent),
            ("P7 file missing, lstat other error", "file_missing_lstat_error",
             "file_missing", oserror, oserror),
            ("P8 file entry present after the failed open (dangling link or race)",
             "none", "file_dangling",
             ("unavailable", wa_atomic.ENTRY_AFTER_FAILED_OPEN),
             ("unavailable", wa_atomic.ENTRY_AFTER_FAILED_OPEN)),
            ("P9 file open: other OSError", "file_open_error", "valid", oserror,
             oserror),
            # -- the opened file: fstat and rules --
            ("P10 file fstat error", "file_fstat_error", "valid", oserror, oserror),
            ("P11 opened file not regular", "none", "file_directory",
             ("unavailable", rule_reg), ("unavailable", rule_reg)),
            ("P12 opened file exposed", "none", "file_exposed",
             ("unavailable", rule_file), ("unavailable", rule_file)),
            # -- the read and the file close --
            ("P13 read error", "read_error", "valid", oserror, oserror),
            ("P14 file close error", "file_close_error", "valid", oserror,
             oserror),
            # -- the directory recheck --
            ("P15 directory exposed after the first check, present branch",
             "dir_exposed_after_read", "valid", present, ("unavailable", rule_dir)),
            ("P16 directory exposed after the first check, absent branch (R12-2)",
             "dir_exposed_after_first_check", "file_missing", absent,
             ("unavailable", rule_dir)),
            # -- parse outcomes --
            ("P17 invalid JSON", "none", "file_invalid_json",
             ("unavailable", rule_json), ("unavailable", rule_json)),
            ("P18 parse: other exception", "parse_other_exception", "valid",
             ("unavailable", rule_type), ("unavailable", rule_type)),
            ("P19 valid document -> present", "none", "valid", present, present),
            # -- the directory close --
            ("P20 directory close error on PRESENT", "dir_close_error", "valid",
             oserror, oserror),
            ("P21 directory close error on ABSENT (R12-1)", "dir_close_error",
             "file_missing", oserror, oserror),
            ("P22 directory close error on UNAVAILABLE keeps the refusal",
             "dir_close_error", "file_exposed", ("unavailable", rule_file),
             ("unavailable", rule_file)),
        )

    def _prepare(self, owner, row_index, file_state):
        name, cls, default, save, _, file_name, _, _ = owner
        directory = os.path.join(self.base.name, "g-path-%s-%d" % (name, row_index))
        if file_state == "dir_missing":
            return cls(directory)
        if file_state == "dir_dangling":
            os.symlink(os.path.join(self.base.name, "gone-%s-%d" % (name, row_index)),
                       directory)
            return cls(directory)
        store = cls(directory)
        save(store, default())
        if file_state == "dir_exposed":
            os.chmod(directory, 0o755)
        elif file_state == "file_missing":
            os.remove(store.path)
        elif file_state == "file_dangling":
            os.remove(store.path)
            os.symlink(store.path + ".gone", store.path)
        elif file_state == "file_directory":
            os.remove(store.path)
            os.mkdir(store.path, 0o700)
        elif file_state == "file_exposed":
            os.chmod(store.path, 0o644)
        elif file_state == "file_invalid_json":
            write_bytes_private(store.path, b"{not json")
        else:
            self.assertEqual(file_state, "valid")
        return store

    def test_G6_every_reader_path_for_every_owner(self):
        rows = self._rows()
        self.assertEqual(len(rows), 22)
        stages_used = {row[1] for row in rows}
        self.assertEqual(stages_used | {"none"}, set(ReaderProbe.STAGES))
        for owner in OWNERS:
            name, _, _, _, error_name, file_name, directory_policy, _ = owner
            for index, (label, stage, file_state, file_only, with_policy) in (
                    enumerate(rows)):
                store = self._prepare(owner, index, file_state)
                probe = ReaderProbe(
                    store, file_name, stage,
                    error=(TypeError("injected parse failure")
                           if stage == "parse_other_exception" else None))
                try:
                    result, raised = probe.run()
                finally:
                    if file_state == "dir_exposed" or stage.startswith("dir_exposed"):
                        if os.path.isdir(store.directory):
                            os.chmod(store.directory, 0o700)
                self.assertIsNone(raised, (name, label, raised))
                availability, problem = with_policy if directory_policy else file_only
                if callable(problem):
                    problem = problem(error_name)
                self.assertEqual(result.availability, availability,
                                 (name, label, result.problem))
                self.assertEqual(result.problem, problem, (name, label))
                if availability != "present":
                    self.assertIsNone(result.document, (name, label))
                if stage != "none":
                    self.assertEqual(probe.fired, [stage], (name, label))
                # Exactly one close per descriptor the reader opened, and
                # no other close.
                opened = [fd for fd in (probe.directory_fd, probe.file_fd)
                          if fd is not None]
                self.assertEqual(sorted(probe.closes), sorted(opened), (name, label))
                if stage == "dir_open_error":
                    self.assertEqual(opened, [], (name, label))
                elif file_state in ("dir_missing", "dir_dangling"):
                    self.assertEqual(opened, [], (name, label))
                else:
                    self.assertIsNotNone(probe.directory_fd, (name, label))

    def test_G6b_pathlike_inputs_match_load_for_every_owner_and_public_call(self):
        mission_id = self.ready_mission(required_dependencies=[])
        for owner in OWNERS:
            name, cls, default, save, _, file_name, _, _ = owner
            existing = Path(self.owner_store(owner, "-pathlike").directory)
            nonexistent = Path(self.base.name) / ("g-pathlike-missing-" + name)
            nested = Path(self.base.name) / "g-no" / "such" / ("place-" + name)
            for label, directory, availability in (("existing", existing, "present"),
                                                   ("nonexistent", nonexistent,
                                                    "absent"),
                                                   ("nested", nested, "absent")):
                store = cls(directory)
                read = store.read()
                self.assertEqual(read.availability, availability,
                                 (name, label, read.problem))
                # ``load`` accepts the same input (a default when missing).
                loaded = store.load()
                self.assertEqual(loaded, read.document if availability == "present"
                                 else default(), (name, label))
                self.assertEqual(wa_atomic.path_access(directory)[0],
                                 "present" if availability == "present" else "absent",
                                 (name, label))
            self.assertFalse(nonexistent.exists())
            self.assertFalse(nested.parent.exists())
        # ``bytes`` is refused exactly as the owners' ``os.path.join`` refuses it.
        with self.assertRaises(TypeError):
            wa_atomic.path_access(os.fsencode(self.base.name))
        with self.assertRaises(TypeError):
            wa_atomic.read_store_document(os.fsencode(self.base.name), "x.json",
                                          "StoreError", False)
        with self.assertRaises(TypeError):
            workflow_store.WorkflowStore(os.fsencode(self.base.name))
        # Public status with PathLike directories: nothing raises; a
        # nonexistent directory is absent, an existing one present.
        base = Path(self.base.name)
        stores = {owner[0]: self.owner_store(owner, "-pathlike-status")
                  for owner in OWNERS}
        result = status_module.read_status(
            self.service, mission_id,
            workflow_directory=Path(stores["workflow"].directory),
            delivery_directory=Path(stores["delivery"].directory),
            coordination_directory=Path(stores["coordination"].directory))
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "present", name)
        result = status_module.read_status(
            self.service, mission_id,
            workflow_directory=base / "g-pl-missing-w",
            delivery_directory=base / "g-pl-missing-d" / "nested",
            coordination_directory=base / "g-pl-missing-c")
        for name in ("workflow", "delivery", "coordination"):
            self.assertEqual(result["stores"][name]["availability"], "absent", name)
            self.assertIsNone(result["stores"][name]["problem"], name)
        # The Mission store as a PathLike: existing -> present; a valid
        # nonexistent directory -> absent (never unavailable), for both
        # public calls.
        copied = mission_store.MissionStore(Path(stores["mission"].directory))
        copied.save(self.store.load())
        for label, directory, expected in (
                ("existing", Path(stores["mission"].directory), "present"),
                ("nonexistent", base / "g-pl-missing-m", "absent"),
                ("nested", base / "g-pl" / "missing" / "m", "absent")):
            service = mission_service.MissionService(
                mission_store.MissionStore(directory), self.clock)
            result = status_module.read_status(service, mission_id)["mission"]
            self.assertEqual(result["availability"], expected, (label, result))
            outcome = adapter.MissionSnapshotSource(service).observe(mission_id)
            self.assertEqual(outcome.status,
                             coordination_record.OBSERVATION_OBSERVED
                             if expected == "present"
                             else coordination_record.OBSERVATION_ABSENT, label)
            if expected == "absent":
                self.assertIsNone(result["problem"], label)


class GDecoderTests(GOwnerFixture):
    """R11-4, as a class: every decode, parse or validation failure is a
    named UNAVAILABLE result from every owner's ``read()`` and from
    ``read_status``; none escapes as an exception."""

    DEPTH = 20000  # past the interpreter's recursion limit, bounded size

    def _inputs(self):
        return (
            ("deep_arrays", b"[" * self.DEPTH + b"]" * self.DEPTH,
             wa_atomic.RULE_JSON_TOO_DEEP),
            ("deep_objects", b'{"a":' * self.DEPTH + b"1" + b"}" * self.DEPTH,
             wa_atomic.RULE_JSON_TOO_DEEP),
            ("invalid_utf8", b'\xff\xfe{"version": 1}', wa_atomic.RULE_INVALID_UTF8),
            ("truncated", b'{"version": 1, "records": {', wa_atomic.RULE_INVALID_JSON),
            ("empty", b"", wa_atomic.RULE_INVALID_JSON),
            ("nan", b'{"version": NaN}', None),
            ("infinity", b'[Infinity, -Infinity]', None),
            ("huge_integer", b"1" * 5000, None),
            ("non_object_array", b"[]", None),
            ("non_object_string", b'"store"', None),
            ("non_object_null", b"null", None),
            ("non_object_number", b"1", None),
            ("lone_surrogate", b'{"\\ud800": 1}', None),
        )

    def test_G4_every_malformed_input_is_a_named_unavailable_result(self):
        for owner in OWNERS:
            name, _, _, _, error_name, _, _, _ = owner
            store = self.owner_store(owner, "-decode")
            for label, data, rule in self._inputs():
                write_bytes_private(store.path, data)
                try:
                    read = store.read()
                except BaseException as exc:  # noqa: BLE001 - the whole point
                    self.fail("%s/%s: read() raised %r" % (name, label, exc))
                self.assertEqual(read.availability, wa_atomic.READ_UNAVAILABLE,
                                 (name, label, read.problem))
                self.assertIsNone(read.document, (name, label))
                self.assertTrue(read.problem.startswith(error_name),
                                (name, label, read.problem))
                if rule is not None:
                    self.assertEqual(read.problem, "%s: %s" % (error_name, rule),
                                     (name, label))

    def test_G4b_read_status_never_raises_on_a_malformed_store(self):
        mission_id = self.ready_mission(required_dependencies=[])
        stores = {owner[0]: self.owner_store(owner, "-status") for owner in OWNERS}
        stores["mission"].save(self.store.load())
        for label, data, rule in self._inputs():
            for owner in OWNERS:
                name, _, _, save, error_name, _, _, _ = owner
                write_bytes_private(stores[name].path, data)
                try:
                    result = self.status_for(mission_id, stores)
                except BaseException as exc:  # noqa: BLE001
                    self.fail("%s/%s: read_status raised %r" % (name, label, exc))
                self.assert_store_result(result, name, "unavailable", error_name)
                if rule is not None:
                    self.assert_store_result(result, name, "unavailable", rule)
                if name == "mission":
                    outcome = adapter.MissionSnapshotSource(
                        mission_service.MissionService(stores["mission"],
                                                       self.clock)).observe(mission_id)
                    self.assertEqual(outcome.status,
                                     coordination_record.OBSERVATION_UNAVAILABLE,
                                     label)
                    self.assertIn(error_name, outcome.problem, label)
                # Every other configured store is unaffected.
                for other in OWNERS:
                    if other[0] != name:
                        self.assert_store_result(result, other[0], "present")
                # Restore this owner's valid document for the next input.
                os.remove(stores[name].path)
                if name == "mission":
                    stores[name].save(self.store.load())
                else:
                    save(stores[name], owner[2]())


# ====================================================================
# E. Static pins on the two new modules
# ====================================================================


FORBIDDEN_CALLS = ("save", "lock", "exclusive_store_lock", "atomic_write_json",
                   "makedirs", "mkdir", "replace", "unlink", "remove", "chmod",
                   "rename", "write", "mint_decision_id",
                   "mint_request_id", "mint_state_operation_id", "_reserve",
                   "apply_human_decision", "reconcile", "_apply", "project",
                   "surface", "acknowledge", "enqueue", "run", "Popen", "sleep",
                   "Thread", "callable", "getattr", "eval", "exec", "__import__",
                   "import_module", "print")
# Neither module touches the filesystem itself: every read goes through
# a store owner's ``read()``; no ``os`` use and never the builtin open.
READ_ONLY_OS_NAMES = set()
FORBIDDEN_IMPORT_ROOTS = ("subprocess", "threading", "time", "socket", "herdr",
                          "target_runtime", "telegram_operator", "codex_gateway",
                          "grok_mcp", "capability", "worker", "durable_execution",
                          "operator_session", "human_interaction")


class EStaticPinTests(unittest.TestCase):

    def _tree(self, name):
        return ast.parse((REPO_ROOT / "mission_control" / name).read_text())

    def test_E1_adapter_and_status_call_no_writer_lock_mint_or_engine(self):
        import ast as ast_module
        for name in ("observation_adapter.py", "status.py"):
            tree = ast_module.parse((REPO_ROOT / "mission_control" / name).read_text())
            calls = {getattr(n.func, "id", getattr(n.func, "attr", None))
                     for n in ast_module.walk(tree) if isinstance(n, ast_module.Call)}
            for forbidden in FORBIDDEN_CALLS:
                self.assertNotIn(forbidden, calls, (name, forbidden))
            for node in ast_module.walk(tree):
                if not isinstance(node, ast_module.Call):
                    continue
                if isinstance(node.func, ast_module.Name):
                    self.assertNotEqual(node.func.id, "open", (name, node.lineno))
                elif (isinstance(node.func, ast_module.Attribute)
                      and node.func.attr == "open"):
                    receiver = node.func.value
                    self.assertTrue(isinstance(receiver, ast_module.Name)
                                    and receiver.id == "os", (name, node.lineno))
                    flags = node.args[1]
                    self.assertTrue(isinstance(flags, ast_module.Attribute)
                                    and flags.attr == "O_RDONLY", (name, node.lineno))
            for node in ast_module.walk(tree):
                if isinstance(node, (ast_module.Import, ast_module.ImportFrom)):
                    roots = ([a.name.split(".")[0] for a in node.names]
                             if isinstance(node, ast_module.Import)
                             else [(node.module or "").split(".")[0]])
                    for root in roots:
                        self.assertNotIn(root, FORBIDDEN_IMPORT_ROOTS, (name, root))
            source = (REPO_ROOT / "mission_control" / name).read_text()
            for word in ("herdr", ".herd", "subprocess"):
                self.assertNotIn(word, source.lower().replace("herdr-free", ""))

    def test_E2_status_reaches_stores_only_through_the_owner_read(self):
        import ast as ast_module
        tree = ast_module.parse((REPO_ROOT / "mission_control" / "status.py").read_text())
        store_calls = set()
        for node in ast_module.walk(tree):
            if isinstance(node, ast_module.Attribute) and isinstance(
                node.value, ast_module.Name) and node.value.id in (
                    "store", "workflow_store", "delivery_store",
                    "coordination_store", "mission_store"):
                store_calls.add(node.attr)
        self.assertLessEqual(store_calls, {
            "WorkflowStore", "DeliveryStore", "CoordinationStore",
            "MissionStoreError", "path", "read", "READ_PRESENT", "READ_ABSENT",
            "READ_UNAVAILABLE"})
        self.assertNotIn("load", store_calls)
        for name in ("status.py", "observation_adapter.py"):
            module_tree = ast_module.parse(
                (REPO_ROOT / "mission_control" / name).read_text())
            os_calls = {n.attr for n in ast_module.walk(module_tree)
                        if isinstance(n, ast_module.Attribute)
                        and isinstance(n.value, ast_module.Name)
                        and n.value.id == "os"}
            self.assertLessEqual(os_calls, READ_ONLY_OS_NAMES, name)
            # By AST (docstrings may name what the code must not touch):
            # no name or attribute reaches an exists check, a creating
            # helper, a lock or a save.
            mentioned = {n.id for n in ast_module.walk(module_tree)
                         if isinstance(n, ast_module.Name)} | {
                n.attr for n in ast_module.walk(module_tree)
                if isinstance(n, ast_module.Attribute)}
            for word in ("exists", "makedirs", "mkdir", "exclusive_store_lock",
                         "lock", "save", "atomic_write_json"):
                self.assertNotIn(word, mentioned, (name, word))


if __name__ == "__main__":
    unittest.main()
