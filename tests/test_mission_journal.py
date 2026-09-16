"""Behavioral tests for Task 7 Stage 1: the Mission Event Journal and
schema / revision / journal-position-bound snapshots.

The journal IS the Task 5 applied-operation ledger, read as ordered,
stable-identity events with a derived hash chain; the ONE stored
addition is the additive-optional ``snapshot`` key of the state record,
written inside the same atomic ``_apply`` save as the ledger entry it
binds. Everything here drives the REAL store and the REAL service in a
temporary protected directory with an injected clock.

Sections:
  J1  events: order, identity, revision binding, the chain, the cursor
  J2  ordering is enforced: a rewritten history changes the chain and
      the stored snapshot refuses; sequence tamper still refuses
  J3  fail-closed reload of every new shape (snapshot, supported state,
      cursor), each with its own problem code, on load AND on save
  J4  a snapshot whose bindings are not the head is refused for use
      (revision moved by an EDIT; position moved by a later event)
  J5  reload equivalence: snapshot vs replay vs restart, state AND cursor
  J6  duplicate events do not duplicate accepted effects
  J7  stale concurrent writes refuse without mutation
  J8  interrupted persistence: ledger and snapshot commit in one replace
  J9  replay is effect-free, structurally and behaviorally
  J10 caps and bounds are module constants and refuse at the bound
  J11 compatibility: a Task 5 record without ``snapshot`` still loads
  J12 Task 7 Stage 2: the attested receipt form commits in one replace,
      snapshot and replay agree, replay validates nothing
"""

import ast
import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from mission import journal as mj  # noqa: E402
from mission import state as ms  # noqa: E402
from mission import state_service as mss  # noqa: E402
from mission import state_validation as msv  # noqa: E402
from mission import store as mst  # noqa: E402
from test_mission_state import (  # noqa: E402
    HEX_A, ServiceStateFixture, hexid,
)


class JournalFixture(ServiceStateFixture):
    """A ready Mission with a few journaled events."""

    def setUp(self):
        super(JournalFixture, self).setUp()
        self.mission_id = self.ready_mission(required_dependencies=[])
        self.claim = self.call("record_claim", self.mission_id, "tests_pass",
                               "the suite passes")
        self.clock.advance(5)
        self.artifact = self.call("record_artifact", self.mission_id, "test_log",
                                  mission_record.ARTIFACT_ROLE_VERIFICATION,
                                  ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log",
                                  HEX_A, True, [])

    def document(self):
        return json.loads(self.read_bytes())

    def state(self):
        return self.document()["mission_state"][self.mission_id]

    def mission(self):
        return self.document()["missions"][self.mission_id]

    def journal(self, **kwargs):
        return self.service.get_journal(self.mission_id, **kwargs)

    def reload(self):
        return self.service.reload_supported_state(self.mission_id)

    def write_document(self, document):
        self.write_raw(json.dumps(document))

    def contract_at_head(self, document=None):
        document = document or self.store.load()
        mission = document["missions"][self.mission_id]
        state = document["mission_state"][self.mission_id]
        activation = ms.latest_activation(state)
        if activation is None:
            return None
        return mst.activation_contract(document, mission, activation, "activation")


# ====================================================================
# J1. Events: order, identity, bindings, chain, cursor
# ====================================================================


class J1EventTests(JournalFixture):

    def test_J1_events_are_the_ledger_in_order_with_stable_identity(self):
        state = self.state()
        events = mj.events(state)
        self.assertEqual(len(events), state["sequence"])
        self.assertEqual(len(events), 3)
        for index, (event, entry) in enumerate(zip(events, state["applied_operations"])):
            self.assertEqual(set(event), set(mj.EVENT_KEYS))
            self.assertEqual(event["position"], index + 1)
            self.assertEqual(event["position"], entry["sequence"])
            self.assertEqual(event["event_id"], entry["operation_id"])
            self.assertEqual(event["kind"], entry["kind"])
            self.assertEqual(event["mission_id"], self.mission_id)
            self.assertEqual(event["revision"], entry["provenance"]["revision"])
            self.assertEqual(event["recorded_at"], entry["applied_at"])
            self.assertEqual(event["content_digest_sha256"],
                             entry["content_digest_sha256"])
            self.assertEqual(len(event["journal_digest_sha256"]), 64)
        self.assertEqual([e["kind"] for e in events], [
            ms.OPERATION_ACTIVATE_CONTRACT, ms.OPERATION_RECORD_CLAIM,
            ms.OPERATION_RECORD_ARTIFACT])
        self.assertEqual(events[1]["event_id"], self.claim["operation_id"])
        # Every position has its own digest, and the digest at a position
        # is the chain over everything at or before it.
        digests = [e["journal_digest_sha256"] for e in events]
        self.assertEqual(len(set(digests)), 3)
        for position in range(0, 4):
            self.assertEqual(mj.journal_digest_at(state, position),
                             mj.genesis_digest(self.mission_id) if position == 0
                             else digests[position - 1])
        self.assertNotEqual(mj.genesis_digest(self.mission_id),
                            mj.genesis_digest(hexid("mn", 0x77)))

    def test_J1_head_cursor_binds_mission_schema_revision_position_and_chain(self):
        state = self.state()
        cursor = mj.head_cursor(self.mission(), state)
        self.assertEqual(set(cursor), set(mj.CURSOR_KEYS))
        self.assertEqual(cursor, {
            "mission_id": self.mission_id,
            "schema_version": ms.STATE_SCHEMA_VERSION,
            "revision": 1,
            "position": 3,
            "event_id": self.artifact["operation_id"],
            "journal_digest_sha256": mj.journal_digest_at(state, 3),
        })
        self.assertEqual(self.journal()["cursor"], cursor)
        # A historical cursor names the event at that position and the
        # revision in force when it was recorded.
        at_two = mj.cursor_at(state, 2)
        self.assertEqual(at_two["position"], 2)
        self.assertEqual(at_two["event_id"], self.claim["operation_id"])
        self.assertEqual(at_two["revision"], 1)
        self.assertEqual(at_two["journal_digest_sha256"], mj.journal_digest_at(state, 2))
        self.assertIsNone(mj.require_cursor_in_journal(state, at_two))
        self.assertIsNone(mj.require_cursor_in_journal(state, cursor))
        origin = mj.cursor_at(state, 0)
        self.assertEqual(origin["position"], 0)
        self.assertIsNone(origin["event_id"])
        self.assertIsNone(origin["revision"])
        self.assertEqual(origin["journal_digest_sha256"], mj.genesis_digest(self.mission_id))
        self.assertIsNone(mj.require_cursor_in_journal(state, origin))
        # A Mission with no state record yet has a cursor at the origin.
        other, _ = self.approved_mission()
        view = self.service.get_journal(other)
        self.assertEqual(view["cursor"], {
            "mission_id": other, "schema_version": ms.STATE_SCHEMA_VERSION,
            "revision": 1, "position": 0, "event_id": None,
            "journal_digest_sha256": mj.genesis_digest(other)})
        self.assertEqual(view["events"], [])
        self.assertIsNone(view["snapshot"])

    def test_J1_service_view_pages_events_in_order(self):
        view = self.journal()
        self.assertEqual(set(view), {"mission_id", "cursor", "events",
                                     "next_after_position", "snapshot"})
        self.assertEqual([e["position"] for e in view["events"]], [1, 2, 3])
        self.assertIsNone(view["next_after_position"])
        first = self.journal(limit=2)
        self.assertEqual([e["position"] for e in first["events"]], [1, 2])
        self.assertEqual(first["next_after_position"], 2)
        rest = self.journal(after_position=first["next_after_position"], limit=2)
        self.assertEqual([e["position"] for e in rest["events"]], [3])
        self.assertIsNone(rest["next_after_position"])
        self.assertEqual(first["events"] + rest["events"], view["events"])
        self.assertEqual(self.journal(after_position=3)["events"], [])
        self.assertEqual(view["events"], mj.events(self.state()))
        # The stored snapshot's bindings are reported, and they are the head.
        self.assertEqual(view["snapshot"], {
            "schema_version": ms.STATE_SCHEMA_VERSION, "revision": 1, "position": 3,
            "journal_digest_sha256": view["cursor"]["journal_digest_sha256"],
            "current": True})


# ====================================================================
# J2. Ordering is enforced, not incidental
# ====================================================================


class J2OrderingTests(JournalFixture):

    def test_J2_rewriting_an_earlier_event_changes_the_chain_and_refuses(self):
        good = self.document()
        state = good["mission_state"][self.mission_id]
        before = [mj.journal_digest_at(state, p) for p in range(4)]
        # Shift the claim (position 2) later in time, consistently across
        # the ledger entry, its provenance and its effect record, but
        # still monotone: every Task 5 validator accepts the rewrite.
        rewritten = copy.deepcopy(good)
        rstate = rewritten["mission_state"][self.mission_id]
        entry = rstate["applied_operations"][1]
        shifted = entry["applied_at"] + 3
        self.assertLessEqual(shifted, rstate["applied_operations"][2]["applied_at"])
        entry["applied_at"] = shifted
        entry["provenance"]["received_at"] = shifted
        claim = rstate["claims"][0]
        claim["claimed_at"] = shifted
        claim["provenance"]["received_at"] = shifted
        after = [mj.journal_digest_at(rstate, p) for p in range(4)]
        self.assertEqual(after[:2], before[:2])
        self.assertNotEqual(after[2], before[2])
        self.assertNotEqual(after[3], before[3])
        # The Task 5 record validates on its own ...
        without = copy.deepcopy(rstate)
        without["snapshot"] = None
        msv.validate_state_record(without, "s")
        # ... but the stored snapshot binds the ORIGINAL chain and refuses
        # the rewritten history, on load and on save, bytes untouched.
        exc = self.refuse_raw(rewritten, mj.PROBLEM_SNAPSHOT_BINDING)
        self.assertIn("journal_digest_sha256", str(exc))
        # And the head cursor of the rewritten history is a different cursor.
        self.assertNotEqual(mj.head_cursor(rewritten["missions"][self.mission_id], rstate),
                            mj.head_cursor(good["missions"][self.mission_id], state))

    def test_J2_sequence_and_order_tampers_still_refuse(self):
        good = self.document()
        swapped = copy.deepcopy(good)
        ops = swapped["mission_state"][self.mission_id]["applied_operations"]
        ops[1], ops[2] = ops[2], ops[1]
        self.refuse_raw(swapped, ms.PROBLEM_SEQUENCE)
        dropped = copy.deepcopy(good)
        dropped["mission_state"][self.mission_id]["applied_operations"].pop()
        self.refuse_raw(dropped, ms.PROBLEM_SEQUENCE)

    def test_J2_a_cursor_from_another_history_is_refused(self):
        state = self.state()
        head = mj.head_cursor(self.mission(), state)
        # Beyond the head: a position the journal does not hold.
        beyond = dict(head, position=4, event_id=hexid("mo", 0x9999))
        self.assertRefuses(mj.PROBLEM_JOURNAL_POSITION,
                           mj.require_cursor_in_journal, state, beyond)
        # Right position, wrong chain: a forked history.
        forked = dict(head, journal_digest_sha256="f" * 64)
        self.assertRefuses(mj.PROBLEM_CURSOR_MISMATCH,
                           mj.require_cursor_in_journal, state, forked)
        # Right position and chain, wrong event identity.
        renamed = dict(head, event_id=hexid("mo", 0x9998))
        self.assertRefuses(mj.PROBLEM_CURSOR_MISMATCH,
                           mj.require_cursor_in_journal, state, renamed)
        # Another Mission's cursor.
        other = self.ready_mission(required_dependencies=[])
        foreign = mj.head_cursor(self.store.load()["missions"][other],
                                 self.store.load()["mission_state"][other])
        self.assertRefuses(mj.PROBLEM_CURSOR_MISMATCH,
                           mj.require_cursor_in_journal, state, foreign)
        # Shape is closed and typed before anything is compared.
        self.assertRefuses(mission_record.PROBLEM_UNKNOWN_KEY,
                           mj.require_cursor_in_journal, state, dict(head, extra=1))
        self.assertRefuses(mission_record.PROBLEM_MISSING_KEY,
                           mj.require_cursor_in_journal, state,
                           {k: v for k, v in head.items() if k != "position"})
        self.assertRefuses(mission_record.PROBLEM_BAD_TYPE,
                           mj.require_cursor_in_journal, state, dict(head, position=True))
        self.assertRefuses(mission_record.PROBLEM_BAD_TYPE,
                           mj.require_cursor_in_journal, state, dict(head, position=3.0))
        self.assertRefuses(mj.PROBLEM_JOURNAL_POSITION,
                           mj.require_cursor_in_journal, state, dict(head, position=-1))
        self.assertRefuses(mission_record.PROBLEM_BAD_VALUE,
                           mj.require_cursor_in_journal, state,
                           dict(head, schema_version=2))
        self.assertRefuses(mission_record.PROBLEM_ID_GRAMMAR,
                           mj.require_cursor_in_journal, state,
                           dict(head, event_id="mo-x"))
        self.assertRefuses(mission_record.PROBLEM_NOT_AN_OBJECT,
                           mj.require_cursor_in_journal, state, "cursor")


# ====================================================================
# J3. Fail-closed reload of every new shape
# ====================================================================


class J3FailClosedTests(JournalFixture):

    def snapshot_tamper(self, path, value):
        document = self.document()
        target = document["mission_state"][self.mission_id]["snapshot"]
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        return document

    def test_J3_stored_snapshot_is_closed_typed_and_bound(self):
        state = self.state()
        snapshot = state["snapshot"]
        self.assertEqual(set(snapshot), set(mj.SNAPSHOT_KEYS))
        self.assertEqual(set(snapshot["supported_state"]), set(mj.SUPPORTED_STATE_KEYS))
        head = mj.head_cursor(self.mission(), state)
        self.assertEqual(snapshot["schema_version"], ms.STATE_SCHEMA_VERSION)
        self.assertEqual(snapshot["mission_id"], self.mission_id)
        self.assertEqual(snapshot["revision"], head["revision"])
        self.assertEqual(snapshot["position"], head["position"])
        self.assertEqual(snapshot["journal_digest_sha256"], head["journal_digest_sha256"])
        cases = (
            ("schema_version", 2, mj.PROBLEM_SNAPSHOT_BINDING),
            ("schema_version", True, mission_record.PROBLEM_BAD_TYPE),
            ("mission_id", hexid("mn", 0x55), mj.PROBLEM_SNAPSHOT_BINDING),
            ("mission_id", "mn-x", mission_record.PROBLEM_ID_GRAMMAR),
            ("revision", 2, mj.PROBLEM_SNAPSHOT_BINDING),
            ("revision", 1.0, mission_record.PROBLEM_BAD_TYPE),
            ("position", 0, mj.PROBLEM_JOURNAL_POSITION),
            ("position", 4, mj.PROBLEM_JOURNAL_POSITION),
            ("position", True, mission_record.PROBLEM_BAD_TYPE),
            ("journal_digest_sha256", "0" * 64, mj.PROBLEM_SNAPSHOT_BINDING),
            ("journal_digest_sha256", "zz", mission_record.PROBLEM_BAD_VALUE),
            ("supported_state", None, mission_record.PROBLEM_NOT_AN_OBJECT),
            ("supported_state.progress", ms.PROGRESS_BLOCKED, mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.progress", "RUNNING", ms.PROBLEM_PROGRESS_UNKNOWN),
            ("supported_state.closure_reason", "proof_complete", mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.closure_reason", "done", mission_record.PROBLEM_BAD_VALUE),
            ("supported_state.activation_id", hexid("mt", 0x99), mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.activation_id", "mt-x", mission_record.PROBLEM_ID_GRAMMAR),
            ("supported_state.contract", None, mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.contract.revision", 2, mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.contract.revision", True, mission_record.PROBLEM_BAD_TYPE),
            ("supported_state.proof.satisfied", True, mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.proof.satisfied", 1, mission_record.PROBLEM_BAD_TYPE),
            ("supported_state.proof.requirements", {"tests_pass": "SATISFIED"},
             mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.proof.requirements", {"tests_pass": "OK"},
             mission_record.PROBLEM_BAD_VALUE),
            ("supported_state.readiness.resources", {"build_host": "READY"},
             mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.readiness.resources", {"build_host": "MAYBE"},
             mission_record.PROBLEM_BAD_VALUE),
            ("supported_state.dependencies.slots", {"x": "RESOLVED"},
             mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.dependencies.slots", {"x": "DONE"},
             mission_record.PROBLEM_BAD_VALUE),
            ("supported_state.budget.attempts_remaining", 99, mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.budget.attempts_remaining", "3", mission_record.PROBLEM_BAD_TYPE),
            ("supported_state.budget", {"attempts_consumed": 0}, mission_record.PROBLEM_MISSING_KEY),
            ("supported_state.active_blocker_ids", [hexid("mb", 1)], mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.active_blocker_ids", ["mb-x"], mission_record.PROBLEM_ID_GRAMMAR),
            ("supported_state.active_blocker_ids", [hexid("mb", 2), hexid("mb", 1)],
             mission_record.PROBLEM_BAD_VALUE),
            ("supported_state.outstanding_dependency_ids", [hexid("mx", 1)],
             mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.accepted_evidence_ids", [hexid("mv", 1)],
             mj.PROBLEM_SNAPSHOT_DISAGREES),
            ("supported_state.accepted_evidence_ids", "none", mission_record.PROBLEM_BAD_TYPE),
        )
        for path, value, code in cases:
            with self.subTest(path=path, value=value):
                self.refuse_raw(self.snapshot_tamper(path, value), code)
        with self.subTest("unknown snapshot key"):
            document = self.document()
            document["mission_state"][self.mission_id]["snapshot"]["surprise"] = 1
            self.refuse_raw(document, mission_record.PROBLEM_UNKNOWN_KEY)
        with self.subTest("missing snapshot key"):
            document = self.document()
            del document["mission_state"][self.mission_id]["snapshot"]["position"]
            self.refuse_raw(document, mission_record.PROBLEM_MISSING_KEY)
        with self.subTest("unknown supported_state key"):
            document = self.document()
            document["mission_state"][self.mission_id]["snapshot"]["supported_state"][
                "closure_eligibility"] = {"eligible": True}
            self.refuse_raw(document, mission_record.PROBLEM_UNKNOWN_KEY)
        with self.subTest("snapshot is not an object"):
            document = self.document()
            document["mission_state"][self.mission_id]["snapshot"] = []
            self.refuse_raw(document, mission_record.PROBLEM_NOT_AN_OBJECT)
        # A snapshot at an EARLIER position is consistent (historical
        # cache) and loads; the reload path then refuses to USE it (J4).
        document = self.document()
        state = document["mission_state"][self.mission_id]
        state["snapshot"] = mj.new_snapshot(state, self.contract_at_head(document),
                                            position=1)
        self.store.save(document)
        self.assertEqual(self.store.load(), document)
        # Every problem code is distinct and mission_-prefixed.
        codes = (mj.PROBLEM_JOURNAL_POSITION, mj.PROBLEM_CURSOR_MISMATCH,
                 mj.PROBLEM_SNAPSHOT_BINDING, mj.PROBLEM_SNAPSHOT_DISAGREES,
                 mj.PROBLEM_SNAPSHOT_STALE, mj.PROBLEM_JOURNAL_PAGE_BOUND)
        self.assertEqual(len(set(codes)), len(codes))
        for code in codes:
            self.assertTrue(code.startswith("mission_journal_"), code)

    def test_J3_shape_is_the_record_validator_bindings_come_last_in_the_store(self):
        state = self.state()
        msv.validate_state_record(state, "s")
        # The record validator checks the closed, typed shape ...
        bad = copy.deepcopy(state)
        bad["snapshot"]["supported_state"]["progress"] = "RUNNING"
        self.assertRefuses(ms.PROBLEM_PROGRESS_UNKNOWN,
                           msv.validate_state_record, bad, "s")
        bad = copy.deepcopy(state)
        bad["snapshot"]["extra"] = 1
        self.assertRefuses(mission_record.PROBLEM_UNKNOWN_KEY,
                           msv.validate_state_record, bad, "s")
        # ... and leaves the ledger bindings and the recomputation to the
        # store, which runs them AFTER every primary check (above), so a
        # tampered history reports the history's own code, never the
        # snapshot's.
        bad = copy.deepcopy(state)
        bad["snapshot"]["position"] = 2
        msv.validate_state_record(bad, "s")
        self.assertRefuses(mj.PROBLEM_SNAPSHOT_BINDING,
                           mj.require_snapshot_bindings, bad["snapshot"], bad, "s")
        bad = copy.deepcopy(state)
        bad["snapshot"]["supported_state"]["progress"] = ms.PROGRESS_BLOCKED
        msv.validate_state_record(bad, "s")
        self.assertIsNotNone(mj.snapshot_disagreement(bad["snapshot"], bad,
                                                      self.contract_at_head()))
        document = self.document()
        tampered = document["mission_state"][self.mission_id]
        tampered["applied_operations"][1]["provenance"]["revision"] = 99
        tampered["claims"][0]["provenance"]["revision"] = 99
        exc = self.refuse_raw(document, ms.PROBLEM_PROVENANCE_MISMATCH)
        self.assertNotIn(mj.PROBLEM_SNAPSHOT_BINDING, str(exc))


# ====================================================================
# J4. A snapshot whose bindings are not the head is not used
# ====================================================================


class J4SnapshotFreshnessTests(JournalFixture):

    def test_J4_snapshot_at_head_is_used_and_equals_replay(self):
        loaded = self.reload()
        self.assertEqual(set(loaded), {"mission_id", "cursor", "source",
                                       "snapshot_problem", "supported_state"})
        self.assertEqual(loaded["source"], mj.SOURCE_SNAPSHOT)
        self.assertIsNone(loaded["snapshot_problem"])
        state = self.state()
        replayed = mj.supported_state(state, self.contract_at_head(), state["sequence"])
        self.assertEqual(loaded["supported_state"], replayed)
        self.assertEqual(loaded["cursor"], mj.head_cursor(self.mission(), state))
        self.assertEqual(loaded["supported_state"]["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(loaded["supported_state"]["proof"]["requirements"],
                         {"tests_pass": self.mp.REQUIREMENT_MISSING})

    def test_J4_an_edit_moves_the_revision_and_the_snapshot_is_refused_for_use(self):
        before = self.reload()
        self.edit(self.mission_id, 1, objective="v2")
        # The document still loads: a stale snapshot is consistent, and a
        # foreign EDIT never makes a valid record unreadable.
        self.stable()
        after = self.reload()
        self.assertEqual(after["source"], mj.SOURCE_REPLAY)
        self.assertEqual(after["snapshot_problem"], mj.PROBLEM_SNAPSHOT_STALE)
        self.assertEqual(after["cursor"]["revision"], 2)
        self.assertEqual(after["cursor"]["position"], before["cursor"]["position"])
        self.assertEqual(after["cursor"]["journal_digest_sha256"],
                         before["cursor"]["journal_digest_sha256"])
        self.assertEqual(self.state()["snapshot"]["revision"], 1)
        view = self.journal()
        self.assertFalse(view["snapshot"]["current"])
        self.assertEqual(view["snapshot"]["revision"], 1)
        # The replayed projection is the same supported state the stale
        # snapshot held: the EDIT changed authority, not accepted history.
        self.assertEqual(after["supported_state"], before["supported_state"])
        # The stale snapshot is never served: reload equals pure replay.
        state = self.state()
        self.assertEqual(after["supported_state"],
                         mj.supported_state(state, self.contract_at_head(),
                                            state["sequence"]))
        # A later event (abandon needs no contract) re-binds the snapshot
        # to the new head, at revision 2.
        self.call("abandon", self.mission_id, "superseded")
        loaded = self.reload()
        self.assertEqual(loaded["source"], mj.SOURCE_SNAPSHOT)
        self.assertEqual(loaded["cursor"]["revision"], 2)
        self.assertEqual(loaded["cursor"]["position"], 4)
        self.assertEqual(loaded["supported_state"]["progress"], ms.PROGRESS_ABANDONED)
        self.assertEqual(loaded["supported_state"]["closure_reason"],
                         ms.CLOSURE_REASON_CALLER_ABANDONED)
        self.assertEqual(self.state()["snapshot"]["revision"], 2)

    def test_J4_a_later_event_moves_the_position_and_the_snapshot_is_refused_for_use(self):
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        # A record whose head moved past its snapshot (hand-appended
        # history, as a test fixture legitimately does).
        self.clock.advance(1)
        self.op(document, state, ms.OPERATION_RECORD_CLAIM, self.clock())
        state["claims"].append(ms.new_claim(
            hexid("mc", 0x500), self.activation_id(state), "tests_pass", "again",
            self.clock(), state["applied_operations"][-1]["provenance"],
            state["applied_operations"][-1]["operation_id"], state["sequence"]))
        self.save(document)
        loaded = self.reload()
        self.assertEqual(loaded["source"], mj.SOURCE_REPLAY)
        self.assertEqual(loaded["snapshot_problem"], mj.PROBLEM_SNAPSHOT_STALE)
        self.assertEqual(loaded["cursor"]["position"], 4)
        self.assertEqual(self.state()["snapshot"]["position"], 3)
        self.assertFalse(self.journal()["snapshot"]["current"])
        fresh = self.store.load()
        self.assertEqual(loaded["supported_state"], mj.supported_state(
            fresh["mission_state"][self.mission_id], self.contract_at_head(fresh), 4))


# ====================================================================
# J5. Reload equivalence: snapshot, replay, restart
# ====================================================================


class J5ReloadEquivalenceTests(JournalFixture):

    def test_J5_reload_from_record_and_journal_with_or_without_snapshot_is_equal(self):
        ms_ = self.ms
        # Grow a richer history: evidence submitted and accepted, a
        # blocker opened (HARD -> BLOCKED) and resolved, readiness, a
        # continuation and a checkpoint.
        evidence = self.call("submit_evidence", self.mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                             [self.artifact["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        blocker = self.call("open_blocker", self.mission_id, "disk_full", "no space")
        self.assertEqual(self.reload()["supported_state"]["progress"], ms_.PROGRESS_BLOCKED)
        self.call("resolve_blocker", self.mission_id, blocker["blocker_id"],
                  evidence["evidence_id"])
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms_.READINESS_READY, self.clock())
        self.call("record_continuation", self.mission_id, "second pass")
        self.call("record_checkpoint", self.mission_id, ["a"], ["b"], "retry", "stop")
        with_snapshot = self.reload()
        self.assertEqual(with_snapshot["source"], mj.SOURCE_SNAPSHOT)
        supported = with_snapshot["supported_state"]
        self.assertEqual(supported["progress"], ms_.PROGRESS_IN_PROGRESS)
        self.assertEqual(supported["accepted_evidence_ids"], [evidence["evidence_id"]])
        self.assertEqual(supported["active_blocker_ids"], [])
        self.assertEqual(supported["proof"]["satisfied"], True)
        self.assertEqual(supported["readiness"]["satisfied"], True)
        self.assertEqual(supported["budget"]["attempts_consumed"], 1)
        self.assertEqual(supported["budget"]["checkpoints_consumed"], 1)
        # Without the snapshot: replay from record + journal.
        document = self.store.load()
        document["mission_state"][self.mission_id]["snapshot"] = None
        self.store.save(document)
        without_snapshot = self.reload()
        self.assertEqual(without_snapshot["source"], mj.SOURCE_REPLAY)
        self.assertIsNone(without_snapshot["snapshot_problem"])
        self.assertEqual(without_snapshot["supported_state"], with_snapshot["supported_state"])
        self.assertEqual(without_snapshot["cursor"], with_snapshot["cursor"])
        # Across a restart (fresh store, fresh service), still equal.
        restarted = self.service.__class__(self.mst.MissionStore(self.directory),
                                           self.clock)
        after_restart = restarted.reload_supported_state(self.mission_id)
        self.assertEqual(after_restart["supported_state"], with_snapshot["supported_state"])
        self.assertEqual(after_restart["cursor"], with_snapshot["cursor"])
        self.assertEqual(restarted.get_journal(self.mission_id)["events"],
                         self.journal()["events"])
        # And the same instant's read-time projection agrees field by field
        # on everything the snapshot holds.
        projection = restarted.get_state(self.mission_id)
        self.assertEqual(projection["progress"], supported["progress"])
        self.assertEqual(projection["proof"], supported["proof"])
        self.assertEqual(projection["readiness"], supported["readiness"])
        self.assertEqual(projection["dependencies"], supported["dependencies"])
        self.assertEqual(projection["budget"], supported["budget"])
        self.assertEqual(projection["sequence"], with_snapshot["cursor"]["position"])
        # The next event rebuilds the snapshot; the rebuilt snapshot equals
        # the replay at the new head.
        self.call("record_claim", self.mission_id, "tests_pass", "again")
        rebuilt = self.reload()
        self.assertEqual(rebuilt["source"], mj.SOURCE_SNAPSHOT)
        state = self.state()
        self.assertEqual(rebuilt["supported_state"],
                         mj.supported_state(state, self.contract_at_head(), state["sequence"]))
        self.assertEqual(rebuilt["cursor"]["position"], with_snapshot["cursor"]["position"] + 1)

    def test_J5_historical_positions_replay_deterministically(self):
        state = self.state()
        contract = self.contract_at_head()
        at_zero = mj.supported_state(state, contract, 0)
        self.assertEqual(at_zero["progress"], ms.PROGRESS_NOT_STARTED)
        self.assertIsNone(at_zero["activation_id"])
        self.assertIsNone(at_zero["contract"])
        self.assertIsNone(at_zero["proof"])
        self.assertEqual(at_zero["active_blocker_ids"], [])
        at_one = mj.supported_state(state, contract, 1)
        self.assertEqual(at_one["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(at_one["activation_id"], ms.latest_activation(state)["activation_id"])
        self.assertEqual(at_one, mj.supported_state(state, contract, 1))
        self.assertEqual(at_one, json.loads(json.dumps(at_one)))
        self.assertRefuses(mj.PROBLEM_JOURNAL_POSITION, mj.supported_state, state,
                           contract, 4)
        self.assertRefuses(mission_record.PROBLEM_BAD_TYPE, mj.supported_state, state,
                           contract, True)
        # Replay never mutates its input.
        frozen = copy.deepcopy(state)
        mj.supported_state(state, contract, 2)
        mj.events(state)
        mj.head_cursor(self.mission(), state)
        self.assertEqual(state, frozen)


# ====================================================================
# J6 / J7. Duplicate events and stale writes
# ====================================================================


class J6DuplicateAndStaleWriteTests(JournalFixture):

    def test_J6_a_duplicate_event_does_not_duplicate_an_accepted_effect(self):
        operation_id = self.oid()
        sequence = self.seq(self.mission_id)
        first = self.service.record_claim(self.mission_id, operation_id, sequence,
                                          "tests_pass", "once", self.context)
        self.assertFalse(first["idempotent"])
        committed = self.read_bytes()
        cursor = self.reload()["cursor"]
        self.assertEqual(cursor["position"], sequence + 1)
        self.assertEqual(cursor["event_id"], operation_id)
        # Same id, same content: the recorded outcome, nothing appended.
        again = self.service.record_claim(self.mission_id, operation_id, sequence,
                                          "tests_pass", "once", self.context)
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.read_bytes(), committed)
        self.assertEqual(self.reload()["cursor"], cursor)
        self.assertEqual(len([e for e in self.journal()["events"]
                              if e["event_id"] == operation_id]), 1)
        self.assertEqual(len(self.state()["claims"]), 2)
        # Same id, different content: refused, nothing changed.
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_claim, self.mission_id, operation_id,
                           sequence, "tests_pass", "twice", self.context)
        # Same id, another principal: refused, nothing changed.
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                           self.service.record_claim, self.mission_id, operation_id,
                           sequence, "tests_pass", "once", self.other)
        self.assertEqual(self.read_bytes(), committed)
        self.assertEqual(self.reload()["cursor"], cursor)
        # A caller-chosen event id is never a journal event.
        self.assertRefuses(mss.PROBLEM_UNKNOWN_STATE_OPERATION_ID,
                           self.service.record_claim, self.mission_id,
                           hexid("mo", 0x4242), sequence + 1, "tests_pass", "x",
                           self.context)
        self.assertEqual(self.read_bytes(), committed)

    def test_J7_a_stale_concurrent_write_refuses_without_mutation(self):
        sequence = self.seq(self.mission_id)
        committed = self.read_bytes()
        cursor = self.reload()["cursor"]
        # Two writers read the same sequence; the first wins.
        winner, loser = self.oid(), self.oid()
        self.service.record_claim(self.mission_id, winner, sequence, "tests_pass",
                                  "winner", self.context)
        after_winner = self.read_bytes()
        self.assertNotEqual(after_winner, committed)
        self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE,
                           self.service.record_claim, self.mission_id, loser, sequence,
                           "tests_pass", "loser", self.context)
        self.assertEqual(self.read_bytes(), after_winner)
        self.assertEqual(self.reload()["cursor"]["position"], cursor["position"] + 1)
        self.assertEqual(self.reload()["cursor"]["event_id"], winner)
        self.assertIsNone(self.store.load()["reservations"][loser]["consumed_by"])
        # Retried against the current sequence, the loser is accepted once.
        self.service.record_claim(self.mission_id, loser, sequence + 1, "tests_pass",
                                  "loser", self.context)
        self.assertEqual(self.reload()["cursor"]["event_id"], loser)
        self.assertEqual([e["event_id"] for e in self.journal()["events"]][-2:],
                         [winner, loser])


# ====================================================================
# J8. Interrupted persistence
# ====================================================================


class J8InterruptedPersistenceTests(JournalFixture):

    def setUp(self):
        super(J8InterruptedPersistenceTests, self).setUp()
        import unittest.mock as mock
        from workflow_authority import atomic as atomic_module
        self.atomic_module = atomic_module
        self.mock = mock

    def test_J8_ledger_and_snapshot_commit_in_one_replace_or_not_at_all(self):
        operation_id = self.oid()
        sequence = self.seq(self.mission_id)
        before_bytes = self.read_bytes()
        before = self.reload()
        before_view = self.journal()
        with self.mock.patch.object(
            self.atomic_module.os, "replace",
            side_effect=OSError("simulated crash before the atomic replacement"),
        ) as replace:
            with self.assertRaises(OSError):
                self.service.record_claim(self.mission_id, operation_id, sequence,
                                          "tests_pass", "interrupted", self.context)
        self.assertEqual(replace.call_count, 1)
        self.assertFalse(os.path.exists(replace.call_args[0][0]))
        self.assertEqual(self.read_bytes(), before_bytes)
        self.assertFalse(any(n.endswith(".tmp") for n in os.listdir(self.directory)))
        fresh = self.mst.MissionStore(self.directory)
        fresh_service = self.service.__class__(fresh, self.clock)
        self.assertEqual(fresh_service.reload_supported_state(self.mission_id), before)
        self.assertEqual(fresh_service.get_journal(self.mission_id), before_view)
        self.assertEqual(before["source"], mj.SOURCE_SNAPSHOT)
        self.assertIsNone(fresh.load()["reservations"][operation_id]["consumed_by"])
        # Disarmed: exactly one replacement commits the ledger entry AND
        # the re-bound snapshot together.
        with self.mock.patch.object(self.atomic_module.os, "replace",
                                    wraps=os.replace) as replace:
            outcome = fresh_service.record_claim(self.mission_id, operation_id, sequence,
                                                 "tests_pass", "interrupted", self.context)
        self.assertEqual(replace.call_count, 1)
        self.assertFalse(outcome["idempotent"])
        document = json.loads(self.read_bytes())
        state = document["mission_state"][self.mission_id]
        self.assertEqual(state["applied_operations"][-1]["operation_id"], operation_id)
        self.assertEqual(state["snapshot"]["position"], sequence + 1)
        self.assertEqual(state["snapshot"]["journal_digest_sha256"],
                         mj.journal_digest_at(state, sequence + 1))
        after = fresh_service.reload_supported_state(self.mission_id)
        self.assertEqual(after["source"], mj.SOURCE_SNAPSHOT)
        self.assertEqual(after["cursor"]["event_id"], operation_id)
        self.assertEqual(after["supported_state"],
                         mj.supported_state(state, self.contract_at_head(), sequence + 1))
        self.assertEqual(document["reservations"][operation_id]["consumed_by"], operation_id)
        # A document whose snapshot names a head the ledger does not hold
        # (a split) is refused outright, never repaired silently.
        split = json.loads(self.read_bytes())
        split["mission_state"][self.mission_id]["applied_operations"].pop()
        split["mission_state"][self.mission_id]["claims"].pop()
        split["mission_state"][self.mission_id]["sequence"] -= 1
        self.refuse_raw(split, mj.PROBLEM_JOURNAL_POSITION)


# ====================================================================
# J9. Replay is effect-free
# ====================================================================


class J9ReplayEffectFreedomTests(JournalFixture):

    def test_J9_journal_module_is_pure_by_construction(self):
        source = (REPO_ROOT / "mission" / "journal.py").read_text()
        tree = ast.parse(source)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        self.assertEqual(roots, {"copy", "mission", "workflow_authority"})
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "mission":
                    imported.update(a.name for a in node.names)
                elif node.module.startswith("workflow_authority"):
                    imported.add(node.module)
        self.assertEqual(imported, {"progress", "record", "state",
                                    "workflow_authority.digest"})
        names = {getattr(n.func, "id", getattr(n.func, "attr", None))
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for forbidden in ("open", "save", "load", "lock", "apply_human_decision",
                          "issue_mission_authorization", "mint_id", "_apply",
                          "atomic_write_json", "exclusive_store_lock"):
            self.assertNotIn(forbidden, names, forbidden)
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
        for word in ("store", "store_module", "service", "MissionService",
                     "authorization_module", "issue_mission_authorization",
                     "apply_human_decision", "os", "json"):
            self.assertNotIn(word, identifiers, word)
        for word in ("issue_mission_authorization", "apply_human_decision"):
            self.assertNotIn(word, source, word)

    def test_J9_reload_performs_no_write_mint_or_authority_change(self):
        minted = []

        def counting_mint(prefix):
            minted.append(prefix)
            return mission_record.mint_id(prefix)

        service = self.service.__class__(self.mst.MissionStore(self.directory),
                                         self.clock, counting_mint)
        before_bytes = self.read_bytes()
        authority = self.authority_bytes()
        listing = sorted(os.listdir(self.directory))
        for _ in range(3):
            loaded = service.reload_supported_state(self.mission_id)
            view = service.get_journal(self.mission_id)
        self.assertEqual(self.read_bytes(), before_bytes)
        self.assertEqual(self.authority_bytes(), authority)
        self.assertEqual(sorted(os.listdir(self.directory)), listing)
        self.assertEqual(minted, [])
        # The reload result carries supported state and a cursor only:
        # no authorization record, no decision, no reservation.
        flat = json.dumps(loaded) + json.dumps(view)
        for absent in ("authorization_id", "decision_id", "consumed_by",
                       "authority_ledger", "expires_at", "revoked"):
            self.assertNotIn(absent, flat, absent)
        # A pure replay over an in-memory copy changes nothing on disk and
        # nothing in the copy.
        document = self.store.load()
        frozen = copy.deepcopy(document)
        state = document["mission_state"][self.mission_id]
        mission = document["missions"][self.mission_id]
        mj.reload(mission, state, self.contract_at_head(document))
        mj.reload(mission, None, None)
        self.assertEqual(document, frozen)
        self.assertEqual(self.read_bytes(), before_bytes)
        # The Mission's authority is exactly what it was.
        self.assertEqual(service.get(self.mission_id)["record"]["state"],
                         mission_record.STATE_AUTHORIZED)
        self.assertIsNone(self.ma.reconcile_registry(self.store.load()))


# ====================================================================
# J10. Caps and bounds
# ====================================================================


class J10BoundsTests(JournalFixture):

    def test_J10_bounds_are_module_constants_and_refuse_at_the_bound(self):
        self.assertEqual(mj.MAX_JOURNAL_EVENTS, ms.MAX_APPLIED_OPERATIONS)
        self.assertIsInstance(mj.MAX_JOURNAL_PAGE_EVENTS, int)
        self.assertGreater(mj.MAX_JOURNAL_PAGE_EVENTS, 0)
        self.assertLessEqual(mj.MAX_JOURNAL_PAGE_EVENTS, mj.MAX_JOURNAL_EVENTS)
        self.assertEqual(self.journal(limit=mj.MAX_JOURNAL_PAGE_EVENTS)["events"],
                         self.journal()["events"])
        self.assertRefuses(mj.PROBLEM_JOURNAL_PAGE_BOUND, self.service.get_journal,
                           self.mission_id, limit=mj.MAX_JOURNAL_PAGE_EVENTS + 1)
        self.assertRefuses(mission_record.PROBLEM_BAD_VALUE, self.service.get_journal,
                           self.mission_id, limit=0)
        self.assertRefuses(mission_record.PROBLEM_BAD_TYPE, self.service.get_journal,
                           self.mission_id, limit=True)
        self.assertRefuses(mj.PROBLEM_JOURNAL_POSITION, self.service.get_journal,
                           self.mission_id, after_position=4)
        self.assertRefuses(mission_record.PROBLEM_BAD_TYPE, self.service.get_journal,
                           self.mission_id, after_position="0")
        self.assertRefuses(self.ma.PROBLEM_UNKNOWN_MISSION, self.service.get_journal,
                           hexid("mn", 0x7777))
        self.assertRefuses(self.ma.PROBLEM_UNKNOWN_MISSION,
                           self.service.reload_supported_state, hexid("mn", 0x7777))
        # Snapshot id lists are bounded by the same constants as the records.
        document = self.document()
        supported = document["mission_state"][self.mission_id]["snapshot"]["supported_state"]
        supported["active_blocker_ids"] = sorted(
            hexid("mb", i) for i in range(ms.MAX_BLOCKER_RECORDS + 1))
        self.refuse_raw(document, mission_record.PROBLEM_TOO_LARGE)
        document = self.document()
        supported = document["mission_state"][self.mission_id]["snapshot"]["supported_state"]
        supported["accepted_evidence_ids"] = sorted(
            hexid("mv", i) for i in range(ms.MAX_EVIDENCE_RECORDS + 1))
        self.refuse_raw(document, mission_record.PROBLEM_TOO_LARGE)
        document = self.document()
        supported = document["mission_state"][self.mission_id]["snapshot"]["supported_state"]
        supported["outstanding_dependency_ids"] = sorted(
            hexid("mx", i) for i in range(ms.MAX_DEPENDENCY_RECORDS + 1))
        self.refuse_raw(document, mission_record.PROBLEM_TOO_LARGE)
        # The journal shares the ledger's cap: history is never pruned,
        # and a page never exceeds its own bound.
        self.assertEqual(ms.MAX_APPLIED_OPERATIONS, 4096)


# ====================================================================
# J11. Compatibility: a Task 5 record without ``snapshot``
# ====================================================================


class J11CompatibilityTests(JournalFixture):

    def test_J11_snapshot_is_an_additive_optional_state_record_key(self):
        self.assertEqual(ms.STATE_RECORD_OPTIONAL_KEYS, ("snapshot", "reconciliations"))
        self.assertEqual(set(ms.STATE_RECORD_KEYS),
                         set(ms.STATE_RECORD_REQUIRED_KEYS) | {"snapshot", "reconciliations"})
        self.assertIsNone(ms.new_state_record(hexid("mn", 1), 5)["snapshot"])
        # Lead decision 02: the legacy-compatibility property is proven
        # DERIVED over every additive-optional record key, and over the
        # record with ALL of them deleted at once (the genuine "written
        # before any of this existed" record), so a future key is covered
        # the moment it exists. For each case: the record loads unchanged,
        # gains nothing on load, leaves the stored bytes untouched on load,
        # and still lacks the key(s) after a save round-trip.
        good = self.document()
        cases = [(key,) for key in ms.STATE_RECORD_OPTIONAL_KEYS]
        cases.append(tuple(ms.STATE_RECORD_OPTIONAL_KEYS))
        self.assertEqual(len(cases), len(ms.STATE_RECORD_OPTIONAL_KEYS) + 1)
        for deleted in cases:
            with self.subTest(deleted=deleted):
                legacy = copy.deepcopy(good)
                for key in deleted:
                    self.assertIn(key, legacy["mission_state"][self.mission_id])
                    del legacy["mission_state"][self.mission_id][key]
                self.write_document(legacy)
                before = self.read_bytes()
                loaded = self.store.load()
                for key in deleted:
                    self.assertNotIn(key, loaded["mission_state"][self.mission_id])
                self.assertEqual(loaded, legacy)
                self.assertEqual(self.read_bytes(), before)
                self.store.save(loaded)
                saved = json.loads(self.read_bytes())["mission_state"][self.mission_id]
                for key in deleted:
                    self.assertNotIn(key, saved)
                self.assertEqual(json.loads(self.read_bytes()), legacy)
        # The concrete snapshot instance: a record without the key reloads
        # by replay with no snapshot problem, and the next event writes
        # the key. Nothing is supplied on load; ``save`` supplies nothing.
        legacy = copy.deepcopy(good)
        del legacy["mission_state"][self.mission_id]["snapshot"]
        self.write_document(legacy)
        self.assertNotIn("snapshot", self.store.load()["mission_state"][self.mission_id])
        reloaded = self.reload()
        self.assertEqual(reloaded["source"], mj.SOURCE_REPLAY)
        self.assertIsNone(reloaded["snapshot_problem"])
        self.assertEqual(reloaded["cursor"]["position"], 3)
        self.assertIsNone(self.journal()["snapshot"])
        self.assertEqual(self.service.get_state(self.mission_id)["sequence"], 3)
        self.call("record_claim", self.mission_id, "tests_pass", "after legacy")
        state = self.state()
        self.assertIn("snapshot", state)
        self.assertEqual(state["snapshot"]["position"], 4)
        self.assertEqual(self.reload()["source"], mj.SOURCE_SNAPSHOT)
        # The prose states the rule.
        for text in (ms.__doc__, mst.__doc__, mj.__doc__):
            self.assertIn("snapshot", text)
        self.assertIn("additive-optional", ms.__doc__)
        self.assertIn("never a second source of truth", mj.__doc__)


# ====================================================================
# J12. Task 7, Stage 2: the attested receipt form under the journal
# ====================================================================


class J12AttestedReceiptJournalTests(JournalFixture):
    """Condition 6 under the journal: the attesting operation is one
    event of the ledger; its marked artifact, ledger entry, consumed
    reservation and re-bound snapshot commit in ONE replace or not at
    all; snapshot and replay reconstruct identical supported state and
    the marker survives replay untouched; a concurrent stale write and a
    replay of the same reserved id converge without a duplicate effect;
    replay calls no validator."""

    def test_J12_one_event_one_replace_snapshot_and_replay_agree(self):
        import unittest.mock as mock
        from workflow_authority import atomic as atomic_module
        attestation = self.attestation(self.mission_id)
        operation_id = self.oid()
        sequence = self.seq(self.mission_id)
        before_bytes = self.read_bytes()
        before = self.reload()
        # Interrupted before the atomic replacement: nothing commits, the
        # reservation stays unconsumed, no marked artifact exists.
        with mock.patch.object(atomic_module.os, "replace",
                               side_effect=OSError("simulated crash")) as replace:
            with self.assertRaises(OSError):
                self.service.attest_delivery_receipt(self.mission_id, operation_id,
                                                     sequence, attestation, self.context)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(self.read_bytes(), before_bytes)
        self.assertEqual(self.reload(), before)
        self.assertIsNone(self.store.load()["reservations"][operation_id]["consumed_by"])
        self.assertEqual(ms.attested_artifacts(self.state()), [])
        # Disarmed: exactly one replacement commits everything together.
        with mock.patch.object(atomic_module.os, "replace", wraps=os.replace) as replace:
            outcome = self.service.attest_delivery_receipt(
                self.mission_id, operation_id, sequence, attestation, self.context)
        self.assertEqual(replace.call_count, 1)
        state = self.state()
        self.assertEqual(state["applied_operations"][-1]["kind"],
                         ms.OPERATION_ATTEST_DELIVERY_RECEIPT)
        self.assertEqual(state["snapshot"]["position"], sequence + 1)
        self.assertEqual([a["artifact_id"] for a in ms.attested_artifacts(state)],
                         [outcome["artifact_id"]])
        self.assertEqual(self.document()["reservations"][operation_id]["consumed_by"],
                         operation_id)
        # The event is in the journal with the new kind and a moved chain.
        events = self.journal()["events"]
        self.assertEqual(events[-1]["kind"], ms.OPERATION_ATTEST_DELIVERY_RECEIPT)
        self.assertEqual(events[-1]["event_id"], operation_id)
        self.assertNotEqual(events[-1]["journal_digest_sha256"],
                            before["cursor"]["journal_digest_sha256"])
        # Snapshot and replay agree; the marker is untouched by replay.
        with_snapshot = self.reload()
        self.assertEqual(with_snapshot["source"], mj.SOURCE_SNAPSHOT)
        document = self.document()
        document["mission_state"][self.mission_id]["snapshot"] = None
        self.write_document(document)
        replayed = self.reload()
        self.assertEqual(replayed["source"], mj.SOURCE_REPLAY)
        self.assertEqual(replayed["supported_state"], with_snapshot["supported_state"])
        self.assertEqual(replayed["cursor"], with_snapshot["cursor"])
        self.assertEqual(ms.attested_artifacts(self.state()),
                         ms.attested_artifacts(state))
        # Replay of the same reserved id with the same content returns the
        # recorded outcome and writes nothing; a stale sequence refuses.
        bytes_before = self.read_bytes()
        replay = self.service.attest_delivery_receipt(
            self.mission_id, operation_id, sequence, attestation, self.context)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["artifact_id"], outcome["artifact_id"])
        self.assertEqual(self.read_bytes(), bytes_before)
        stale_id = self.oid()
        bytes_before = self.read_bytes()
        self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE,
                           self.service.attest_delivery_receipt, self.mission_id,
                           stale_id, sequence, dict(attestation,
                                                    receipt_id="rcpt-" + "c" * 24),
                           self.context)
        self.assertEqual(self.read_bytes(), bytes_before)
        self.assertEqual(len(ms.attested_artifacts(self.state())), 1)
        # Replay validates nothing: the journal module names no validator,
        # imports nothing beyond the pure modules, and the reload took no
        # lock, wrote nothing and minted nothing (J9 discipline).
        source = (REPO_ROOT / "mission" / "journal.py").read_text()
        for forbidden in ("validate_receipt", "attest", "pr_delivery", "receipt"):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":
    unittest.main()
