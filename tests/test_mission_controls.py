"""Task 8, slice S-V: the canonical Mission controls (hold / lift / cancel /
confirmation), the tiered capacity strategy, EDIT supersession, the
retention crash windows, the Runtime → Core reconciliation bridge and
the integrated happy path — all against the REAL wired guards (the
dependency predicate is true for the real service; nothing is patched
to make it so).

Fixtures are the real ones: ``ServiceStateFixture`` (the production
MissionService over a real store) for the core, ``EngagementCase`` (real
store + real gate + gated Broker over the real bridge with engine
doubles + real bootstrap) for the integration, and the Grok MCP fixture
for the tool surface.
"""

import copy
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import authorization as mission_authorization  # noqa: E402
from mission import progress as mission_progress  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import state as ms  # noqa: E402
from mission import state_reconcile as sr  # noqa: E402
from mission import state_service as mission_state_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_control import gate as gate_module  # noqa: E402
from mission_control import integration  # noqa: E402
from mission_control import observation_receipts as receipts_module  # noqa: E402
from mission_control import reconciliation_bridge as bridge  # noqa: E402
from mission_control import status as status_module  # noqa: E402
from pr_delivery import candidate as candidate_module  # noqa: E402
from target_runtime import broker as broker_module  # noqa: E402
from target_runtime import runtime as runtime_module  # noqa: E402
from workflow_authority import record as wa_record  # noqa: E402
from workflow_authority import store as wa_store  # noqa: E402
from workflow_authority.digest import json_digest  # noqa: E402

from test_mission_state import ServiceStateFixture, contract  # noqa: E402
from test_mission_engagement import (  # noqa: E402
    CONTROL_CONTEXT, EngagementCase, HANDOFF_REVISION, WORKSPACE_ID, obs,
)
from test_grok_mcp import MissionFixture, mission_proposal_arguments  # noqa: E402
from _hermetic_git import run_git  # noqa: E402

CONNECTOR = mission_record.AuthenticatedContext(
    transport="grok_mcp",
    principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
    principal_ref="connector-1")


# ====================================================================
# S. The canonical controls through the real service
# ====================================================================


class ControlFixture(ServiceStateFixture):
    """Control conveniences over the REAL operations: a reserved id, the
    exact current sequence, sufficient provenance."""

    def hold(self, mission_id, reason="operator pause", context=None):
        context = context or self.context
        return self.service.request_hold(
            mission_id, self.service.mint_control_operation_id(context),
            self.seq(mission_id), reason, context)

    def lift(self, mission_id, context=None):
        context = context or self.context
        return self.service.lift_hold(
            mission_id, self.service.mint_control_operation_id(context),
            self.seq(mission_id), context)

    def cancel(self, mission_id, reason="operator cancel", context=None):
        context = context or self.context
        return self.service.request_cancel(
            mission_id, self.service.mint_cancel_operation_id(
                mission_id, ms.OPERATION_REQUEST_CANCEL, context),
            self.seq(mission_id), reason, context)

    def confirm(self, mission_id, detail="every start observed absent", context=None):
        context = context or self.context
        return self.service.confirm_cancel(
            mission_id, self.service.mint_cancel_operation_id(
                mission_id, ms.OPERATION_CONFIRM_CANCEL, context),
            self.seq(mission_id), detail, context)

    def controls(self, mission_id):
        return self.service.mission_controls(mission_id)

    def state(self, mission_id):
        return self.service.get_state(mission_id)["record"]

    def refused(self, problem, call, *args, **kwargs):
        with self.assertRaises((mission_record.MissionError,
                                mission_store.MissionStoreError)) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, str(caught.exception))
        return caught.exception

    def engaged(self, mission_id, workflow_id="wf-m-" + "a" * 26, sequence=1,
                point=ms.START_POINT_RUNTIME, owner="owner-1"):
        self.call("observe_resource_readiness", mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        engagement = self.call("reserve_engagement", mission_id, workflow_id, sequence)
        start = self.call("open_engagement_start", mission_id,
                          engagement["engagement_id"], point, owner)
        return engagement, start

    def digest_rederives(self, mission_id, kinds):
        """Every applied control operation's stored digest re-derives from
        its effect through the ONE invocation-argument derivation."""
        state = self.state(mission_id)
        controls = ms.controls_of(state)
        seen = set()
        for op in state["applied_operations"]:
            kind, oid = op["kind"], op["operation_id"]
            if kind not in kinds:
                continue
            if kind == ms.OPERATION_REQUEST_HOLD:
                # ``holds`` is append-only: exactly ONE record names the op.
                [effect] = [h for h in controls["holds"] if h["operation_id"] == oid]
            elif kind == ms.OPERATION_LIFT_HOLD:
                [effect] = [h for h in controls["holds"]
                            if h["lift_operation_id"] == oid]
            elif kind == ms.OPERATION_REQUEST_CANCEL:
                effect = controls["cancel_request"]
                self.assertEqual(effect["operation_id"], oid)
            else:
                effect = controls["cancel_request"]["confirmation"]
                self.assertEqual(effect["operation_id"], oid)
                self.assertEqual(state["closure"]["operation_id"], oid)
            digest = ms.invocation_digest(
                kind, mission_id, op["sequence"] - 1,
                sr._invocation_arguments(kind, effect, op["outcome"], state))
            self.assertEqual(digest, op["content_digest_sha256"], kind)
            seen.add(kind)
        self.assertEqual(seen, set(kinds))


class SControlLifecycleTests(ControlFixture):

    def test_S1_hold_lift_cancel_confirm_through_apply(self):
        mission_id = self.ready_mission()
        before = self.service.get_journal(mission_id)["cursor"]["position"]
        self.assertEqual(self.controls(mission_id), {
            "hold_active": False, "cancel_requested": False,
            "cancel_confirmed": False, "hold": None, "cancel_request": None})
        held = self.hold(mission_id)
        self.assertEqual(held["hold_active"], True)
        self.assertFalse(held["idempotent"])
        view = self.controls(mission_id)
        self.assertTrue(view["hold_active"])
        self.assertEqual(view["hold"]["reason"], "operator pause")
        self.assertEqual(view["hold"]["revision"], 1)
        self.assertIsNone(view["hold"]["lifted_at"])
        # Hold at the gates: the fence and every start refuse reversibly.
        self.refused(ms.PROBLEM_CONTROL_HOLD, self.call, "reserve_engagement",
                     mission_id, "wf-m-" + "b" * 26, 1)
        self.refused(ms.PROBLEM_CONTROL_STATE, self.hold, mission_id)
        # Lift: permits nothing by itself, spends nothing.
        consumed = mission_progress.consumed_attempts(self.state(mission_id))
        lifted = self.lift(mission_id)
        self.assertEqual(lifted["hold_active"], False)
        view = self.controls(mission_id)
        self.assertFalse(view["hold_active"])
        self.assertIsNotNone(view["hold"]["lifted_at"])
        self.assertEqual(view["hold"]["lift_sequence"], self.seq(mission_id))
        self.assertEqual(mission_progress.consumed_attempts(self.state(mission_id)),
                         consumed)
        self.refused(ms.PROBLEM_CONTROL_STATE, self.lift, mission_id)
        # A hold again, then a sticky cancel while a start is open.
        self.hold(mission_id)
        self.lift(mission_id)
        engagement, start = self.engaged(mission_id)
        cancelled = self.cancel(mission_id, "stop everything")
        self.assertEqual(cancelled["cancel_requested"], True)
        self.assertEqual(cancelled["stops_requested"], 1)
        marked = [s for s in ms.engagement_starts_of(self.state(mission_id))
                  if s["start_id"] == start["start_id"]][0]
        self.assertEqual(marked["stop_requested"]["reason"],
                         "cancel requested: stop everything")
        self.assertEqual(marked["stop_requested"]["operation_id"],
                         ms.cancel_operation_id(mission_id, ms.OPERATION_REQUEST_CANCEL))
        # Sticky: nothing undoes it, nothing else is admitted. The same
        # cancel again is the idempotent replay of the ONE derived id; a
        # different reason on that id is a conflict, never a second cancel.
        self.refused(ms.PROBLEM_CONTROL_STATE, self.hold, mission_id)
        self.refused(ms.PROBLEM_CONTROL_STATE, self.lift, mission_id)
        derived = ms.cancel_operation_id(mission_id, ms.OPERATION_REQUEST_CANCEL)
        applied_at = self.seq(mission_id) - 1
        self.assertTrue(self.service.request_cancel(
            mission_id, derived, applied_at, "stop everything", self.context)["idempotent"])
        self.refused(mission_state_service.PROBLEM_STATE_OPERATION_CONFLICT,
                     self.service.request_cancel, mission_id, derived, applied_at,
                     "a different reason", self.context)
        self.refused(mission_state_service.PROBLEM_STATE_OPERATION_CONFLICT,
                     self.cancel, mission_id, "stop everything")
        self.refused(ms.PROBLEM_CONTROL_CANCELLED, self.call, "reserve_engagement",
                     mission_id, "wf-m-" + "c" * 26, 2)
        self.refused(ms.PROBLEM_CONTROL_CANCELLED, self.call, "open_engagement_start",
                     mission_id, engagement["engagement_id"], ms.START_POINT_TASK,
                     "owner-1")
        # Confirmation ONLY by observed absence of every start.
        self.refused(ms.PROBLEM_CANCEL_UNCONFIRMED, self.confirm, mission_id)
        self.call("settle_engagement_start", mission_id, start["start_id"], "owner-1",
                  ms.START_OUTCOME_COMPLETED,
                  {"workspace_id": "w-1", "agent_names": ["a"], "task_id": None}, None)
        self.refused(ms.PROBLEM_CANCEL_UNCONFIRMED, self.confirm, mission_id)
        self.call("observe_engagement_stop", mission_id, start["start_id"], "owner-1",
                  False, "close returned but the workspace is still listed", None)
        self.refused(ms.PROBLEM_CANCEL_UNCONFIRMED, self.confirm, mission_id)
        self.call("observe_engagement_stop", mission_id, start["start_id"], "owner-1",
                  True, "workspace w-1 absent from a fresh listing", None)
        confirmed = self.confirm(mission_id, "one start, absence observed")
        self.assertEqual(confirmed["reason"], ms.CLOSURE_REASON_CALLER_ABANDONED)
        self.assertEqual(confirmed["progress"], ms.PROGRESS_ABANDONED)
        state = self.state(mission_id)
        self.assertEqual(state["progress"], ms.PROGRESS_ABANDONED)
        self.assertEqual(state["closure"]["detail"], "one start, absence observed")
        view = self.controls(mission_id)
        self.assertTrue(view["cancel_confirmed"])
        self.assertEqual(view["cancel_request"]["confirmation"]["starts_confirmed"],
                         [start["start_id"]])
        self.assertIs(view["cancel_request"]["confirmation"]["starts_never_started"], False)
        # Terminal stays terminal; history and receipts are preserved.
        self.refused(ms.PROBLEM_PROGRESS_TERMINAL, self.hold, mission_id)
        self.assertEqual([e["kind"] for e in ms.controls_of(state)["history"]], [
            "hold_requested", "hold_lifted", "hold_requested", "hold_lifted",
            "cancel_requested", "cancel_confirmed"])
        self.assertEqual(len(ms.engagement_starts_of(state)), 1)
        self.assertEqual([o["absent"] for o in ms.engagement_starts_of(state)[0][
            "stop_observations"]], [False, True])
        # The journal advanced by exactly the applied operations.
        after = self.service.get_journal(mission_id)["cursor"]["position"]
        # 4 hold/lift + readiness + reservation + start + cancel + settle
        # + 2 observations + confirmation.
        self.assertEqual(after - before, 12)
        self.digest_rederives(mission_id, ms.CONTROL_OPERATIONS)
        # The store re-validates and reconciles the record on load.
        self.service.get_state(mission_id)
        self.assertEqual(self.service.mission_controls(mission_id)["cancel_confirmed"], True)

    def test_S2_controls_bypass_lapsed_authority_stale_contract_and_budgets(self):
        # (a) Expired authority: ordinary operations refuse, controls run.
        created = self.propose(proof_contract=contract())
        mission_id = created["mission_id"]
        self.approve(mission_id, 1, expires_at=self.clock() + 100)
        self.call("activate_proof_contract", mission_id)
        self.clock.advance(200)
        # The lapsed authorization makes the bound contract not live.
        self.refused(mission_state_service.PROBLEM_CONTRACT_STALE, self.call,
                     "record_claim", mission_id, "tests_pass", "I say so")
        self.assertTrue(self.hold(mission_id)["hold_active"])
        self.assertFalse(self.lift(mission_id)["hold_active"])
        self.assertTrue(self.cancel(mission_id)["cancel_requested"])
        self.assertEqual(self.confirm(mission_id)["progress"], ms.PROGRESS_ABANDONED)
        # (b) Stale contract after an EDIT: ordinary refuses, controls run.
        second = self.ready_mission()
        self.edit(second, 1)
        self.refused(mission_state_service.PROBLEM_CONTRACT_STALE, self.call,
                     "record_claim", second, "tests_pass", "I say so")
        self.assertTrue(self.hold(second)["hold_active"])
        self.assertEqual(self.controls(second)["hold"]["revision"], 2)
        self.assertFalse(self.lift(second)["hold_active"])
        self.assertTrue(self.cancel(second)["cancel_requested"])
        # (c) Exhausted budget: the continuation budget is spent, the
        # controls still record; no budget is consumed by them.
        third = self.ready_mission(continuation_budget={"max_attempts": 1,
                                                        "max_checkpoints": 8})
        self.call("record_continuation", third, "one")
        self.refused(ms.PROBLEM_BUDGET_EXHAUSTED, self.call, "record_continuation",
                     third, "two")
        consumed = mission_progress.consumed_attempts(self.state(third))
        self.hold(third)
        self.lift(third)
        self.cancel(third)
        self.assertEqual(mission_progress.consumed_attempts(self.state(third)), consumed)
        self.assertEqual(self.confirm(third)["progress"], ms.PROGRESS_ABANDONED)

    def test_S3_saturated_ledger_keeps_the_cancel_pair_recordable(self):
        # The Lead's outcome-specific saturation: with the ledger bound
        # shrunk to 40, ordinary operations refuse 16 below it, holds and
        # lifts refuse 2 below it, and the cancel pair fills the last two
        # slots exactly — no run of holds can consume them.
        mission_id = self.ready_mission()
        with mock.patch.object(ms, "MAX_APPLIED_OPERATIONS", 40):
            self.assertEqual(ms.applied_operation_limit(ms.OPERATION_RECORD_CLAIM), 24)
            self.assertEqual(ms.applied_operation_limit(ms.OPERATION_REQUEST_HOLD), 38)
            self.assertEqual(ms.applied_operation_limit(ms.OPERATION_LIFT_HOLD), 38)
            self.assertEqual(ms.applied_operation_limit(ms.OPERATION_REQUEST_CANCEL), 39)
            self.assertEqual(ms.applied_operation_limit(ms.OPERATION_CONFIRM_CANCEL), 40)
            ordinary = 0
            while True:
                try:
                    self.call("record_claim", mission_id, "tests_pass", "claim %d" % ordinary)
                except mission_store.MissionStoreError as exc:
                    # ``_apply`` reports a full ledger as the store's
                    # capacity refusal; the ledger text names the bound.
                    self.assertEqual(exc.problem, mission_store.PROBLEM_STORE_FULL)
                    self.assertIn("hard bound is 40", str(exc))
                    break
                ordinary += 1
            self.assertEqual(len(self.state(mission_id)["applied_operations"]), 24)
            self.assertEqual(ordinary, 23)
            holds = 0
            while True:
                try:
                    (self.lift if holds % 2 else self.hold)(mission_id)
                except mission_store.MissionStoreError as exc:
                    self.assertEqual(exc.problem, mission_store.PROBLEM_STORE_FULL)
                    self.assertIn("hard bound is 40", str(exc))
                    break
                holds += 1
            self.assertEqual(holds, 14)
            self.assertEqual(len(self.state(mission_id)["applied_operations"]), 38)
            self.assertFalse(self.controls(mission_id)["hold_active"])
            self.assertTrue(self.cancel(mission_id)["cancel_requested"])
            self.assertEqual(len(self.state(mission_id)["applied_operations"]), 39)
            self.assertEqual(self.confirm(mission_id)["progress"], ms.PROGRESS_ABANDONED)
            self.assertEqual(len(self.state(mission_id)["applied_operations"]), 40)
        self.assertEqual(ms.CANCEL_OPERATION_RESERVE, 2)
        self.assertEqual(ms.CONTROL_OPERATION_HEADROOM, 16)
        self.assertGreater(ms.CONTROL_OPERATION_HEADROOM, ms.CANCEL_OPERATION_RESERVE)

    def test_S4_saturated_reservations_keep_the_cancel_ids_available(self):
        # The reservation table is shared store-wide and never evicted:
        # ordinary mints stop 16 below the cap, holds may use the
        # headroom, and the cancel pair is reserved under its OWN kind
        # with derived, per-Mission ids — no run of ordinary or hold
        # reservations can exhaust it.
        mission_id = self.ready_mission()
        other = self.ready_mission()
        kind = mission_store.RESERVATION_KIND_STATE_OPERATION
        with mock.patch.dict(mission_store.RESERVATION_CAPS, {kind: 20}):
            minted = 0
            while True:
                try:
                    self.service.mint_state_operation_id(self.context)
                except mission_store.MissionStoreError as exc:
                    self.assertEqual(exc.problem, mission_store.PROBLEM_STORE_FULL)
                    break
                minted += 1
            held = [r for r in self.store.load()["reservations"].values()
                    if r["kind"] == kind]
            self.assertEqual(len(held), 4)
            controls = 0
            while True:
                try:
                    self.service.mint_control_operation_id(self.context)
                except mission_store.MissionStoreError as exc:
                    self.assertEqual(exc.problem, mission_store.PROBLEM_STORE_FULL)
                    break
                controls += 1
            self.assertEqual(controls, 16)
            self.assertEqual(len([r for r in self.store.load()["reservations"].values()
                                  if r["kind"] == kind]), 20)
            # The cancel ids: derived, dedicated, idempotent, consumed once.
            first = self.service.mint_cancel_operation_id(
                mission_id, ms.OPERATION_REQUEST_CANCEL, self.context)
            self.assertEqual(first, ms.cancel_operation_id(mission_id,
                                                           ms.OPERATION_REQUEST_CANCEL))
            self.assertEqual(self.service.mint_cancel_operation_id(
                mission_id, ms.OPERATION_REQUEST_CANCEL, self.context), first)
            self.assertNotEqual(first, ms.cancel_operation_id(
                mission_id, ms.OPERATION_CONFIRM_CANCEL))
            self.assertNotEqual(first, ms.cancel_operation_id(
                other, ms.OPERATION_REQUEST_CANCEL))
            reservations = self.store.load()["reservations"]
            self.assertEqual(reservations[first]["kind"],
                             mission_store.RESERVATION_KIND_CANCEL_OPERATION)
            self.assertEqual(len([r for r in reservations.values()
                                  if r["kind"] == mission_store.RESERVATION_KIND_CANCEL_OPERATION]), 1)
            self.assertTrue(self.cancel(mission_id)["cancel_requested"])
            self.assertEqual(self.store.load()["reservations"][first]["consumed_by"], first)
            # Replaying the consumed id is the idempotent replay.
            again = self.service.request_cancel(mission_id, first, self.seq(mission_id) - 1,
                                                "operator cancel", self.context)
            self.assertTrue(again["idempotent"])
            self.assertEqual(self.confirm(mission_id)["progress"], ms.PROGRESS_ABANDONED)
            self.assertEqual(self.confirm(other)["progress"] if False else
                             self.cancel(other)["cancel_requested"], True)
        self.assertEqual(mission_store.MAX_RESERVED_CANCEL_OPERATION_IDS,
                         2 * mission_store.MAX_MISSION_RECORDS)
        self.assertEqual(mission_store.RESERVATION_CAPS[
            mission_store.RESERVATION_KIND_CANCEL_OPERATION], 2048)
        # An unconsumed cancel id is re-bound to the next sufficient
        # caller; a consumed one is never re-bound.
        pending = self.ready_mission()
        first = self.service.mint_cancel_operation_id(
            pending, ms.OPERATION_REQUEST_CANCEL, self.context)
        self.assertEqual(self.service.mint_cancel_operation_id(
            pending, ms.OPERATION_REQUEST_CANCEL, self.other), first)
        self.assertEqual(self.store.load()["reservations"][first]["context"],
                         self.other.as_dict())
        self.refused(mission_state_service.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                     self.service.request_cancel, pending, first, self.seq(pending),
                     "x", self.context)

    def test_S5_provenance_unknown_mission_and_cross_mission_isolation(self):
        mission_id = self.ready_mission()
        other = self.ready_mission()
        before = self.read_bytes()
        # A connector credential can neither hold, cancel nor mint a cancel id.
        self.refused(ms.PROBLEM_CONTROL_PROVENANCE, self.service.request_hold,
                     mission_id, self.service.mint_state_operation_id(self.context),
                     self.seq(mission_id), "x", CONNECTOR)
        self.refused(ms.PROBLEM_CONTROL_PROVENANCE, self.service.mint_cancel_operation_id,
                     mission_id, ms.OPERATION_REQUEST_CANCEL, CONNECTOR)
        self.assertEqual(self.state(mission_id)["sequence"],
                         json.loads(before)["mission_state"][mission_id]["sequence"])
        # Unknown Mission: refused before any write.
        unknown = "mn-" + "0" * 32
        self.refused(mission_authorization.PROBLEM_UNKNOWN_MISSION,
                     self.service.mission_controls, unknown)
        self.refused(mission_authorization.PROBLEM_UNKNOWN_MISSION,
                     self.service.mint_cancel_operation_id, unknown,
                     ms.OPERATION_REQUEST_CANCEL, self.context)
        # Cross-Mission isolation: a hold and a cancel on one Mission
        # leave the other's controls, gates and ledger untouched.
        self.hold(mission_id)
        self.cancel(other)
        self.assertTrue(self.controls(mission_id)["hold_active"])
        self.assertFalse(self.controls(mission_id)["cancel_requested"])
        self.assertTrue(self.controls(other)["cancel_requested"])
        self.assertFalse(self.controls(other)["hold_active"])
        self.refused(ms.PROBLEM_CONTROL_HOLD, self.call, "reserve_engagement",
                     mission_id, "wf-m-" + "a" * 26, 1)
        self.refused(ms.PROBLEM_CONTROL_CANCELLED, self.call, "reserve_engagement",
                     other, "wf-m-" + "a" * 26, 1)
        third = self.ready_mission()
        self.assertEqual(self.call("reserve_engagement", third, "wf-m-" + "a" * 26, 1)[
            "engagement_sequence"], 1)

    def test_S6_edit_supersedes_starts_and_reports_what_it_left(self):
        mission_id = self.ready_mission()
        engagement, start = self.engaged(mission_id)
        self.call("record_checkpoint", mission_id, ["tests"], ["close"], "retry", "stop")
        state_before = copy.deepcopy(self.state(mission_id))
        edited = self.edit(mission_id, 1)
        self.assertEqual(edited["revision"], 2)
        self.assertEqual(edited["superseded"], {
            "revision": 2,
            "activation_id": state_before["contract_activations"][-1]["activation_id"],
            "checkpoints": 1, "engagements": ["wf-m-" + "a" * 26],
            "starts_stop_requested": 1, "recorded": True})
        state = self.state(mission_id)
        marked = ms.engagement_starts_of(state)[0]
        self.assertEqual(marked["stop_requested"]["revision"], 2)
        self.assertIsNone(marked["stop_requested"]["operation_id"])
        self.assertIn("superseded by EDIT to revision 2", marked["stop_requested"]["reason"])
        self.assertEqual(ms.controls_of(state)["history"][-1]["kind"], "revision_superseded")
        # History preserved: the ledger is byte-identical (an EDIT is a
        # record decision, not a state operation).
        self.assertEqual(state["applied_operations"], state_before["applied_operations"])
        self.assertEqual(state["sequence"], state_before["sequence"])
        # Replay of the same decision reports the same, derived view.
        replay = self.service.apply_human_decision(self.last_envelope)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["superseded"], edited["superseded"])
        # The gate refuses the stale start terminally; the stop requirement
        # is what the settlement records.
        self.assertTrue(ms.start_stop_required(marked))
        # An EDIT of a Mission with nothing running leaves the state
        # record byte-identical (no supersession event, nothing marked).
        quiet = self.ready_mission()
        before = json.loads(self.read_bytes())["mission_state"][quiet]
        outcome = self.edit(quiet, 1)
        self.assertEqual(json.loads(self.read_bytes())["mission_state"][quiet], before)
        self.assertEqual(outcome["superseded"], {
            "revision": 2, "activation_id": before["contract_activations"][-1][
                "activation_id"], "checkpoints": 0, "engagements": [],
            "starts_stop_requested": 0, "recorded": False})

    def test_S9_an_edit_marked_settled_start_records_its_stop_observations(self):
        # A settled start that an EDIT marks with a REVISION-bound stop
        # (no operation, sequence None): its owner's stop observations are
        # required AFTER the EDIT and validate on every save and reload
        # (regression: the validator compared the None sequence).
        mission_id = self.ready_mission()
        _engagement, start = self.engaged(mission_id)
        self.call("settle_engagement_start", mission_id, start["start_id"], "owner-1",
                  ms.START_OUTCOME_COMPLETED,
                  {"workspace_id": "w-1", "agent_names": ["a"], "task_id": None}, None)
        self.edit(mission_id, 1)
        marked = ms.engagement_starts_of(self.state(mission_id))[0]
        self.assertIsNone(marked["stop_requested"]["sequence"])
        self.assertEqual(marked["stop_requested"]["revision"], 2)
        self.call("observe_engagement_stop", mission_id, start["start_id"], "owner-1",
                  False, "close returned but the workspace is still listed", None)
        self.call("observe_engagement_stop", mission_id, start["start_id"], "owner-1",
                  True, "workspace w-1 absent from a fresh listing", None)
        observed = ms.engagement_starts_of(self.state(mission_id))[0]["stop_observations"]
        self.assertEqual([o["absent"] for o in observed], [False, True])
        self.assertEqual([o["provenance"]["revision"] for o in observed], [2, 2])
        mission_store.MissionStore(self.directory).load()  # validates on reload

    def test_S10_a_hold_on_an_open_claim_records_its_sticky_stop(self):
        # R15-1 (the reviewer's exact race, through the real service): an
        # OPEN runtime claim, then HOLD, then RESUME, then the settlement.
        # The hold transaction itself records the open claim's stop
        # requirement; the lift does not clear it; the settlement owes the
        # stop; the next (task) claim is refused.
        mission_id = self.ready_mission()
        engagement, start = self.engaged(mission_id)
        held = self.hold(mission_id)
        self.assertEqual(held["stops_requested"], 1)
        marked = ms.engagement_starts_of(self.state(mission_id))[0]
        hold_op = ms.controls_of(self.state(mission_id))["holds"][-1]["operation_id"]
        self.assertEqual(marked["stop_requested"]["operation_id"], hold_op)
        self.assertIn("hold requested on an open claim", marked["stop_requested"]["reason"])
        self.lift(mission_id)
        self.assertEqual(ms.engagement_starts_of(self.state(mission_id))[0]["stop_requested"],
                         marked["stop_requested"])
        settled = self.call("settle_engagement_start", mission_id, start["start_id"],
                            "owner-1", ms.START_OUTCOME_COMPLETED,
                            {"workspace_id": "w-1", "agent_names": ["a"], "task_id": None},
                            None)
        self.assertTrue(settled["stop_pending"])
        start = ms.engagement_starts_of(self.state(mission_id))[0]
        self.assertTrue(ms.start_stop_required(start))
        self.refused(ms.PROBLEM_ENGAGEMENT_START_ORDER, self.call, "open_engagement_start",
                     mission_id, engagement["engagement_id"], ms.START_POINT_TASK, "owner-1")
        self.digest_rederives(mission_id, (ms.OPERATION_REQUEST_HOLD,
                                           ms.OPERATION_LIFT_HOLD))
        mission_store.MissionStore(self.directory).load()  # validates on reload
        # A hold on a SETTLED start marks nothing (no pretended pause).
        other = self.ready_mission()
        _engagement, other_start = self.engaged(other)
        self.call("settle_engagement_start", other, other_start["start_id"], "owner-1",
                  ms.START_OUTCOME_COMPLETED,
                  {"workspace_id": "w-2", "agent_names": ["a"], "task_id": None}, None)
        self.assertEqual(self.hold(other)["stops_requested"], 0)
        self.assertIsNone(ms.engagement_starts_of(self.state(other))[0]["stop_requested"])

    def edit(self, mission_id, revision, **overrides):
        """The fixture's EDIT, keeping its envelope for the replay."""
        self.clock.advance(1)
        decision_id = self.service.mint_decision_id(self.context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]["proposal"]
        proposal = dict(current)
        proposal.update(overrides or {"objective": current["objective"] + " (revised)"})
        self.last_envelope = self.md.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=self.md.DECISION_EDIT,
            received_at=self.clock(), proposal=proposal)
        return self.service.apply_human_decision(self.last_envelope)

    def test_S7_status_progress_and_validation_views(self):
        mission_id = self.ready_mission()
        base = self.seq(mission_id)
        self.hold(mission_id, "pause for review")
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertTrue(view["controls"]["hold_active"])
        self.assertIn("control:hold_active", view["holds"]["codes"])
        self.lift(mission_id)
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertNotIn("control:hold_active", view["holds"]["codes"])
        self.cancel(mission_id)
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertIn("control:cancel_requested", view["holds"]["codes"])
        self.confirm(mission_id)
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertIn("control:cancel_confirmed", view["holds"]["codes"])
        self.assertNotIn("control:cancel_requested", view["holds"]["codes"])
        # The history projection slices the controls exactly.
        state = self.state(mission_id)
        at_hold = mission_progress.state_as_of(state, base + 1)
        self.assertTrue(ms.hold_active(at_hold))
        self.assertIsNone(at_hold["controls"]["cancel_request"])
        at_lift = mission_progress.state_as_of(state, base + 2)
        self.assertFalse(ms.hold_active(at_lift))
        self.assertIsNotNone(ms.latest_hold(at_lift["controls"])["lifted_at"])
        at_cancel = mission_progress.state_as_of(state, base + 3)
        self.assertTrue(ms.cancel_requested(at_cancel))
        self.assertFalse(ms.cancel_confirmed(at_cancel))
        self.assertEqual(mission_progress.state_as_of(state, base)["controls"]["holds"], [])
        # Tampering: a lift bound to an earlier operation, or a cancel
        # confirmation without its closure, refuses on load.
        document = self.store.load()
        tampered = copy.deepcopy(document)
        hold = tampered["mission_state"][mission_id]["controls"]["holds"][-1]
        hold["lift_sequence"] = hold["sequence"]
        with self.assertRaises(mission_store.MissionStoreError):
            self.store.save(tampered)
        tampered = copy.deepcopy(document)
        tampered["mission_state"][mission_id]["controls"]["cancel_request"][
            "confirmation"] = None
        tampered["mission_state"][mission_id]["controls"]["cancel_request"][
            "confirmed_at"] = None
        with self.assertRaises(mission_store.MissionStoreError):
            self.store.save(tampered)

    def test_S8_submitted_only_or_wrong_evidence_never_closes(self):
        mission_id = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", mission_id, "test_log",
                             mission_record.ARTIFACT_ROLE_VERIFICATION,
                             ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log",
                             "a" * 64, True, [])
        self.call("observe_resource_readiness", mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        evidence = self.call("submit_evidence", mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                             "e" * 64, [artifact["artifact_id"]])
        # Submitted only: not eligible.
        self.refused(mission_progress.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                     "complete_successfully", mission_id, "done")
        # A wrong digest cannot be accepted; a narrative claim cannot
        # satisfy a verification requirement.
        self.refused(ms.PROBLEM_EVIDENCE_DIGEST, self.call, "accept_evidence",
                     mission_id, evidence["evidence_id"], "f" * 64, context=self.other)
        narrative = self.call("submit_evidence", mission_id, "tests_pass",
                              mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM, "9" * 64, [])
        self.clock.advance(1)
        self.refused(ms.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING, self.call, "accept_evidence",
                     mission_id, narrative["evidence_id"], "9" * 64, context=self.other)
        self.refused(mission_progress.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                     "complete_successfully", mission_id, "done")


# ====================================================================
# R. Retention, the reconciliation bridge and the integrated path
# ====================================================================


class RIntegrationTests(EngagementCase):

    def retention(self, workflow_id):
        return self.record(workflow_id)[wa_record.RETENTION_KEY]

    def prepared(self):
        mission_id = self.ready_mission()
        return mission_id, self.validated(mission_id)

    def abandoned_start(self, before_close=None):
        """As in the start-claim tests: a prepared workflow whose runtime
        start blocks until ``proceed`` is set, the wait bound 0.5 s."""
        import threading
        from target_runtime import dispatch as dispatch_module
        mission_id, workflow_id = self.prepared()
        started, proceed = threading.Event(), threading.Event()
        self.engine.block_start = (started, proceed)
        self.addCleanup(proceed.set)
        bound = mock.patch.object(dispatch_module, "START_WAIT_SECONDS", 0.5)
        bound.start()
        self.addCleanup(bound.stop)
        return mission_id, workflow_id, started, proceed, []

    def blocked_by_verification(self, mission_id, workflow_id):
        """A terminal (BLOCKED) record whose Mission is untouched: the
        verification turn fails to complete, the durable stop is
        accepted through the gate."""
        from test_target_runtime import FakeRoleTurnResult
        self.role_turn.verification_result = FakeRoleTurnResult(
            status="role_turn_failed", outcome=None, reason="crashed",
            turn={"turn_id": "turn-v-fail", "role": "verification",
                  "process_id": 4243})
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_VERIFICATION_BLOCKED)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)

    def cleanup_candidates(self, now=None):
        return runtime_module.terminal_cleanup_candidates(
            self.store_dir, self.gate, self.clock() if now is None else now)

    def unresolved(self, workflow_id):
        return wa_record.unresolved_start_receipts(self.record(workflow_id))

    def failing_saves(self, workflow_id, marker):
        """Every workflow save that would persist a receipt carrying
        ``marker`` fails — the crash window right AFTER the canonical
        Mission write and BEFORE the record's own receipt of it."""
        real_save = self.broker.store.save
        state = {"failed": 0}

        def save(document):
            entry = document["workflows"].get(workflow_id)
            if entry is not None and any(
                marker in r["bounded_summary"] for r in entry["receipts"]
            ):
                state["failed"] += 1
                raise wa_store.StoreError("crash: the workflow save failed")
            return real_save(document)
        patcher = mock.patch.object(self.broker.store, "save", save)
        patcher.start()
        self.addCleanup(patcher.stop)
        return state

    def test_R1_retention_is_established_with_the_row_and_protects_it(self):
        mission_id = self.ready_mission()
        workflow_id = self.workflow_row(mission_id)
        entry = self.record(workflow_id)
        retention = entry[wa_record.RETENTION_KEY]
        self.assertEqual(retention, {
            "established_at": entry["approval"]["created_at"],
            "deadline_at": entry["approval"]["created_at"]
            + wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS,
            "reason": wa_record.RETENTION_REASON_DELIVERY_CANDIDATE,
            "released_at": None, "release_reason": None})
        self.assertTrue(wa_store.retention_protects(entry, self.clock()))
        self.assertFalse(wa_store.retention_protects(entry, retention["deadline_at"]))
        # A terminal record within the window: no cleanup candidate, no
        # release, no pruning; the deadline is immutable across passes.
        for action in (broker_module.ACTION_MATERIALIZE, broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF, broker_module.ACTION_DISPATCH):
            self.assertTrue(self.act(workflow_id, action).ok, action)
        self.blocked_by_verification(mission_id, workflow_id)
        self.assertEqual(self.cleanup_candidates(), [])
        released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(released.problem, broker_module.PROBLEM_RETENTION_PROTECTED)
        document = self.broker.store.load()
        with mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", 1):
            self.assertEqual(wa_store._prune_inactive(document, self.clock()), 0)
            from test_workflow_authority import mission_core_record
            another = mission_core_record(self.authorized_record("wf-mission-2"))
            self.assertEqual(wa_store.add_workflow(document, another, self.clock()),
                             (False, wa_store.PROBLEM_STORE_FULL, 0))
            # Past the deadline, while the record still HOLDS its workspace
            # lease, it is pruned by no caller (Task 8 R20-2: the held lease
            # is the record's word that its process scopes were not yet
            # shown absent — ``record.cleanup_evidence_outstanding``).
            later = retention["deadline_at"] + 1
            protected = gate_module.canonically_protected(self.service)
            for predicate in (None, protected):
                self.assertEqual(wa_store.add_workflow(document, another, later, predicate),
                                 (False, wa_store.PROBLEM_STORE_FULL, 0))
                self.assertIn(workflow_id, document["workflows"])
            # Its lease released (on this loaded copy only), the same record is
            # prunable — by a caller that can read its CANONICAL obligations
            # (R15-2: none are outstanding here) — and the insertion succeeds
            # by pruning exactly it; a caller without that read prunes no
            # Mission-origin record at all.
            document["workflows"][workflow_id]["workspace_lease"]["released_at"] = later
            self.assertEqual(wa_store.add_workflow(document, another, later),
                             (False, wa_store.PROBLEM_STORE_FULL, 0))
            self.assertEqual(wa_store.add_workflow(
                document, another, later, protected=protected), (True, None, 1))
            self.assertNotIn(workflow_id, document["workflows"])
        runtime_module.process_once(self.gated_broker())
        self.assertEqual(self.retention(workflow_id)["deadline_at"], retention["deadline_at"])
        self.assertEqual(self.retention(workflow_id)["established_at"],
                         retention["established_at"])

    def test_R2_crash_after_the_canonical_open_keeps_the_record_protected(self):
        # Window (a): the canonical start is written, the admitted
        # receipt's save fails. The claim receipt written BEFORE the
        # canonical open is the record's own unresolved evidence.
        mission_id, workflow_id = self.prepared()
        failures = self.failing_saves(workflow_id, " state=%s" % broker_module.START_STATE_ADMITTED)
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        # The containment boundary records the unsavable record durably
        # (BLOCKED, record growth blocked); nothing after the failed save
        # persisted.
        self.assertEqual(outcome.problem, broker_module.PROBLEM_RECORD_UNSAVABLE)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_RECORD_GROWTH_BLOCKED)
        self.assertGreaterEqual(failures["failed"], 1)
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        claim = broker_module.claim_head(broker_module.dispatch_module.START_POINT_RUNTIME, 1)
        self.assertEqual(self.unresolved(workflow_id),
                         {claim: broker_module.START_STATE_CLAIMING})
        self.assertEqual(self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER), [])
        starts = self.starts(mission_id)
        self.assertEqual(len(starts), 1)
        self.assertIsNone(starts[0]["settlement"])
        self.assertEqual(self.engine.starts, [])
        # Protected regardless of the deadline, release and pruning.
        deadline = entry[wa_record.RETENTION_KEY]["deadline_at"]
        for now in (self.clock(), deadline + 1):
            self.assertTrue(wa_store.retention_protects(entry, now), now)
            document = self.broker.store.load()
            with mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", 1):
                self.assertEqual(wa_store._prune_inactive(document, now), 0)
        # The owner's recovery pass (every Runtime pass recovers a record
        # with unresolved start evidence, whatever its phase) resolves the
        # claim from the canonical facts and settles the start UNCERTAIN
        # (nothing was invoked; no identity; the stop stays pending, never
        # confirmed).
        fresh = self.gated_broker()
        processed = runtime_module.process_once(fresh)
        self.assertIn((runtime_module.RECOVERY_LABEL, True),
                      [(label, o.ok) for label, o in processed[workflow_id]])
        self.assertEqual(self.unresolved(workflow_id)[starts[0]["start_id"]],
                         broker_module.START_STATE_STOP_PENDING)
        self.assertNotIn(claim, self.unresolved(workflow_id))
        settled = self.starts(mission_id)[0]
        self.assertEqual(settled["settlement"]["outcome"], "uncertain")
        self.assertTrue(settled["settlement"]["stop_pending"])
        self.assertFalse(broker_module.mission_gate_module.start_stop_confirmed(settled))
        self.assertEqual(self.engine.starts, [])
        self.assertEqual(self.engine.close_calls, [])
        self.assertTrue(wa_store.retention_protects(self.record(workflow_id), deadline + 1))
        # A crash BEFORE the canonical open leaves a claim with no start:
        # the recovery pass resolves it as unadmitted (nothing to stop).
        other_mission, other_workflow = self.prepared()
        real_open = self.gate.open_start

        def crash_before_open(*args, **kwargs):
            raise wa_store.StoreError("crash before the canonical open")
        with mock.patch.object(self.gate, "open_start", crash_before_open):
            crashed = self.act(other_workflow, broker_module.ACTION_DISPATCH)
        self.assertFalse(crashed.ok)
        self.assertEqual(crashed.problem, broker_module.PROBLEM_SPAWN_FAILED)
        other_claim = broker_module.claim_head(
            broker_module.dispatch_module.START_POINT_RUNTIME, 1)
        self.assertEqual(self.unresolved(other_workflow),
                         {other_claim: broker_module.START_STATE_CLAIMING})
        self.assertEqual(self.starts(other_mission), [])
        self.assertTrue(wa_store.retention_protects(self.record(other_workflow),
                                                    deadline + 10))
        del real_open
        runtime_module.process_once(self.gated_broker())
        self.assertNotIn(other_claim, self.unresolved(other_workflow))
        self.assertIn(" state=%s" % broker_module.START_STATE_CLAIM_UNADMITTED,
                      self.receipts(other_workflow, broker_module.MISSION_CLAIM_RECEIPT_MARKER)[-1])

    def test_R3_crash_between_settlement_and_its_receipt_keeps_protection(self):
        # Window (b): the canonical settlement records a PENDING stop
        # (a cancel landed during the start); the settlement receipt's save
        # fails. The record's latest evidence is ``admitted`` (unresolved),
        # and the settlement receipt itself — when it is written — states
        # ``stop=pending`` rather than resolving the start.
        mission_id, workflow_id = self.prepared()
        self.engine.before_start = lambda: self.service.request_cancel(mission_id)
        failures = self.failing_saves(workflow_id, " state=settled:")
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_RECORD_UNSAVABLE)
        self.assertGreaterEqual(failures["failed"], 1)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertEqual(self.unresolved(workflow_id)[start["start_id"]],
                         broker_module.START_STATE_ADMITTED)
        self.assertEqual(len(self.engine.live), 1)
        entry = self.record(workflow_id)
        deadline = entry[wa_record.RETENTION_KEY]["deadline_at"]
        self.assertTrue(wa_store.retention_protects(entry, deadline + 1))
        self.assertEqual(self.cleanup_candidates(deadline + 1), [])
        # Restart: the owner's pass performs the owned stop, confirms only
        # by observed absence, and the record's evidence follows.
        self.engine.before_start = None
        fresh = self.gated_broker()
        processed = runtime_module.process_once(fresh)
        recovery = [o for label, o in processed[workflow_id]
                    if label == runtime_module.RECOVERY_LABEL]
        self.assertEqual(len(recovery), 1)
        self.assertTrue(recovery[0].ok, (recovery[0].problem, recovery[0].detail))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(self.engine.live, [])
        start = self.starts(mission_id)[0]
        self.assertTrue(broker_module.mission_gate_module.start_stop_confirmed(start))
        self.assertEqual(self.unresolved(workflow_id), {})
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1])
        # Still retained until the deadline; the confirmed cancel releases
        # it on the next pass, and only then is it a cleanup candidate.
        self.assertTrue(wa_store.retention_protects(self.record(workflow_id), self.clock()))
        self.assertEqual(self.cleanup_candidates(), [])
        confirmed = self.service.confirm_cancel(mission_id)
        self.assertEqual(confirmed["progress"], ms.PROGRESS_ABANDONED)
        real_candidates = runtime_module.terminal_cleanup_candidates
        seen = []

        def candidates(*args, **kwargs):
            found = real_candidates(*args, **kwargs)
            seen.append(list(found))
            return found
        with mock.patch.object(runtime_module, "terminal_cleanup_candidates", candidates):
            runtime_module.process_once(fresh)
        retention = self.retention(workflow_id)
        self.assertEqual(retention["release_reason"],
                         wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertIsNotNone(retention["released_at"])
        self.assertEqual(retention["deadline_at"], deadline)
        self.assertFalse(wa_store.retention_protects(self.record(workflow_id), self.clock()))
        # The candidate carries the record's handoff revision (the one a
        # release capability is minted for).
        self.assertIn([(workflow_id, HANDOFF_REVISION)], seen)
        # Task 8 ownership correction (cause 1): the record never bound a task,
        # so the same pass releases it from its canonical start — the runtime
        # the owned stop closed is observed absent (nothing to close, no second
        # close) — instead of retaining it as ``workspace_evidence_degraded``.
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertEqual(self.cleanup_candidates(), [])

    def test_R4_settlement_receipt_states_its_own_stop_requirement(self):
        # A settled start with NO stop resolves by its own receipt; a
        # crafted receipt without the statement stays unresolved.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
        self.assertEqual(len(receipts), 4)
        self.assertTrue(receipts[1].endswith(" stop=none"))
        self.assertTrue(receipts[3].endswith(" stop=none"))
        self.assertEqual(self.unresolved(workflow_id), {})
        claims = self.receipts(workflow_id, broker_module.MISSION_CLAIM_RECEIPT_MARKER)
        self.assertEqual([c.split(" state=")[1].split(" ")[0] for c in claims],
                         ["claiming", "claim:admitted", "claiming", "claim:admitted"])
        entry = copy.deepcopy(self.record(workflow_id))
        stripped = []
        for receipt in entry["receipts"]:
            receipt = dict(receipt)
            receipt["bounded_summary"] = receipt["bounded_summary"].replace(" stop=none", "")
            stripped.append(receipt)
        entry["receipts"] = stripped
        self.assertEqual(sorted(wa_record.unresolved_start_receipts(entry).values()),
                         ["settled:completed", "settled:completed"])
        pending = copy.deepcopy(self.record(workflow_id))
        pending["receipts"][-1]["bounded_summary"] = pending["receipts"][-1][
            "bounded_summary"].replace(" stop=none", " stop=pending")
        self.assertEqual(list(wa_record.unresolved_start_receipts(pending).values()),
                         ["settled:completed"])

    def test_R5_bridge_reports_at_the_head_recollects_and_leaks_nothing(self):
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        entry = self.record(workflow_id)
        reports = bridge.materialize_reports(entry, self.clock())
        self.assertEqual(reports["task"]["value"], "ACTIVE")
        self.assertEqual(reports["review"]["value"], "PENDING")
        self.assertEqual(reports["candidate"]["value"], {
            "baseline_digest_sha256": json_digest(entry["approved_baseline"]),
            "artifact_digests": {"handoff": entry["handoff"]["digest_sha256"]}})
        self.assertEqual(reports["delivery"]["value"]["status"], "ABSENT")
        reservations_before = len(self.mstore.load()["reservations"])
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(sorted(outcomes), [workflow_id])
        self.assertTrue(outcomes[workflow_id].ok, outcomes[workflow_id].detail)
        self.assertEqual(outcomes[workflow_id].outcome,
                         runtime_module.OUTCOME_RECONCILED_CHANGED)
        state = self.service.get_state(mission_id)["record"]
        self.assertEqual(len(state["reconciliations"]), 1)
        recorded = state["reconciliations"][0]
        self.assertEqual(recorded["sources"]["task"]["value"], "ACTIVE")
        self.assertEqual(recorded["sources"]["candidate"]["value"]["artifact_digests"],
                         {"handoff": entry["handoff"]["digest_sha256"]})
        self.assertEqual(recorded["observed_position"], state["sequence"] - 1)
        self.assertEqual(recorded["provenance"]["principal_ref"], "dirun-test")
        self.assertEqual(len(self.mstore.load()["reservations"]), reservations_before + 1)
        # Unchanged: nothing consumed; the ONE reservation the context
        # holds is reused by every later unchanged pass (no state write).
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(outcomes[workflow_id].outcome,
                         runtime_module.OUTCOME_RECONCILED_UNCHANGED)
        self.assertEqual(len(self.mstore.load()["reservations"]), reservations_before + 2)
        bytes_before = self.mission_bytes()
        for _ in range(3):
            outcomes = runtime_module.reconcile_mission_workflows(self.broker)
            self.assertEqual(outcomes[workflow_id].outcome,
                             runtime_module.OUTCOME_RECONCILED_UNCHANGED)
        self.assertEqual(self.mission_bytes(), bytes_before)
        self.assertEqual(len(self.mstore.load()["reservations"]), reservations_before + 2)
        self.assertEqual(len(self.service.get_state(mission_id)["record"]["reconciliations"]), 1)
        unconsumed = [r for r in self.mstore.load()["reservations"].values()
                      if r["kind"] == "state_operation" and r["consumed_by"] is None
                      and r["context"]["principal_ref"] == "dirun-test"]
        self.assertEqual(len(unconsumed), 1)
        # The document moves between the collection and the write: the
        # first attempt refuses ``moved``, the second re-collects and
        # records at the new head — one operation, exact position.
        real_journal = self.service.get_journal
        moved = {"n": 0}

        def moving_journal(mid, *args, **kwargs):
            view = real_journal(mid, *args, **kwargs)
            if moved["n"] == 0:
                moved["n"] += 1
                from mission_control import engineering as engineering_module
                self.op("record_claim", mid, engineering_module.MANDATORY_REQUIREMENT_KEYS[0],
                        "moved under the report")
            return view
        # A genuinely different report: the presented record holds one more
        # observed review round (review standing and a new digest key) — the
        # round receipt AND the collector's listing receipt that proves it
        # (R17-1: a round receipt alone proves no standing).
        record = copy.deepcopy(self.record(workflow_id))
        record["receipts"].append(broker_module.review_round_receipt(
            1, "REJECT", "round-01.md", "9" * 64, self.clock()))
        record["receipts"].append(broker_module.review_listing_receipt(
            [(1, "REJECT")], "9" * 64, True, self.clock()))
        with mock.patch.object(self.service, "get_journal", moving_journal):
            result = bridge.reconcile_workflow(
                self.service, self.gate.context, record, self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(moved["n"], 1)
        state = self.service.get_state(mission_id)["record"]
        self.assertEqual(state["reconciliations"][-1]["observed_position"],
                         state["sequence"] - 1)
        self.assertEqual(state["reconciliations"][-1]["sources"]["review"]["value"],
                         "REJECT")
        self.assertEqual(len(state["reconciliations"]), 2)
        self.assertEqual(len(unconsumed), 1)
        # A concurrent EDIT: the revision moved; the record bound to the
        # superseded revision is NEVER reported as an observation of the
        # new one (R2-11-a): refused, nothing written.
        self.edit(mission_id)
        stale = self.record(workflow_id)
        records_before = len(self.service.get_state(mission_id)["record"]["reconciliations"])
        result = bridge.reconcile_workflow(
            self.service, self.gate.context, stale, self.clock())
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["problem"], bridge.PROBLEM_REVISION_SUPERSEDED)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(len(self.service.get_state(mission_id)["record"]["reconciliations"]),
                         records_before)
        # The pass records the bridge outcome per workflow; a record whose
        # Mission is unknown is skipped, a v2 record never reported.
        processed = runtime_module.process_once(self.broker)
        labels = [label for label, _ in processed[workflow_id]]
        self.assertEqual(labels[-1], runtime_module.RECONCILE_LABEL)
        self.put_record(self.authorized_record("wf-v2"))
        from test_workflow_authority import mission_core_record
        self.put_record(mission_core_record(self.authorized_record("wf-unknown")))
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(sorted(outcomes), [workflow_id])

    def test_R6_integrated_happy_path_with_exact_effects(self):
        # elicited approval → gated dispatch (one row, one fence, one
        # spawn) → hold blocks the next effect, lift revalidates → the
        # completed engagement stays retained → reconciliation reports
        # the record's artifacts → cancel confirmed only by observed
        # absence, then the retention is released.
        mission_id = self.ready_mission()
        result = self.bootstrap(mission_id)
        self.assertTrue(result["ok"], result)
        workflow_id = result["workflow_id"]
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.engagements(mission_id)), 1)
        self.assertTrue(self.bootstrap(mission_id)["idempotent"])
        self.assertEqual(len(self.rows()), 1)
        for action in (broker_module.ACTION_MATERIALIZE, broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.act(workflow_id, action).ok, action)
        self.assertTrue(self.act(workflow_id, broker_module.ACTION_DISPATCH).ok)
        self.assertEqual(len(self.spawn_requests), 1)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [self.record(workflow_id)["handoff"]["text"]])
        starts = self.starts(mission_id)
        self.assertEqual([s["settlement"]["outcome"] for s in starts],
                         ["completed", "completed"])
        self.assertEqual([s["settlement"]["stop_pending"] for s in starts], [False, False])
        # Hold: the next effect (verification) is HELD with one receipt,
        # the phase preserved, no turn accepted; lift admits it again.
        self.service.request_hold(mission_id)
        turns_before = len(self.role_turn.calls)
        held = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(held.problem, gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(held.outcome, broker_module.OUTCOME_MISSION_HELD)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(len(self.receipts(workflow_id,
                                           broker_module.MISSION_HOLD_RECEIPT_MARKER)), 1)
        self.assertEqual(len(self.role_turn.calls), turns_before)
        self.service.release_hold(mission_id)
        self.assertEqual(len(self.receipts(workflow_id,
                                           broker_module.MISSION_HOLD_RECEIPT_MARKER)), 1)
        # Retention protects the record throughout: no cleanup candidate
        # and no pruning even when it is read as inactive (the release
        # refusal of a terminal protected record is R1's).
        self.assertEqual(self.cleanup_candidates(), [])
        self.assertTrue(wa_store.retention_protects(self.record(workflow_id), self.clock()))
        # Reconciliation reports the record's complete facts.
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(outcomes[workflow_id].outcome,
                         runtime_module.OUTCOME_RECONCILED_CHANGED)
        recorded = self.service.get_state(mission_id)["record"]["reconciliations"][-1]
        self.assertEqual(recorded["sources"]["task"]["value"], "ACTIVE")
        self.assertEqual(recorded["sources"]["review"]["value"], "PENDING")
        self.assertEqual(recorded["sources"]["delivery"]["value"]["status"], "ABSENT")
        # Cancel: the stop requirement lands on both settled starts; the
        # owner's pass closes the proven workspace; a close whose fresh
        # listing still shows the workspace NEVER confirms.
        cancelled = self.service.request_cancel(mission_id, "operator cancel")
        self.assertEqual(cancelled["stops_requested"], 2)
        self.engine.close_leaves_visible = True
        blocked = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(blocked.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(blocked.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        # One owned stop per marked start (both settled with the same
        # proven workspace); the listing still shows it after each close.
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        self.assertEqual(len(self.engine.live), 1)
        starts = self.starts(mission_id)
        self.assertFalse(any(broker_module.mission_gate_module.start_stop_confirmed(s)
                             for s in starts))
        self.assertFalse(obs(starts[0])["absent"])
        with self.assertRaises(mission_record.MissionError) as caught:
            self.service.confirm_cancel(mission_id)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_CANCEL_UNCONFIRMED)
        # The sessions leave: the next pass observes absence and confirms
        # every start; then and only then the cancel is confirmed.
        self.engine.close_leaves_visible = False
        self.engine.live = []
        runtime_module.process_once(self.gated_broker())
        starts = self.starts(mission_id)
        self.assertTrue(all(broker_module.mission_gate_module.start_stop_confirmed(s)
                            for s in starts))
        confirmed = self.service.confirm_cancel(mission_id)
        self.assertEqual(confirmed["progress"], ms.PROGRESS_ABANDONED)
        self.assertEqual(sorted(self.service.mission_controls(mission_id)["cancel_request"][
            "confirmation"]["starts_confirmed"]), sorted(s["start_id"] for s in starts))
        self.assertEqual(len(self.spawn_requests), 1)
        self.assertEqual(len(self.engine.starts), 1)
        # Retention released by the confirmed cancel; cleanup admitted.
        runtime_module.process_once(self.gated_broker())
        self.assertEqual(self.retention(workflow_id)["release_reason"],
                         wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertEqual(self.cleanup_candidates(), [(workflow_id, HANDOFF_REVISION)])
        view = status_module.read_status(self.service, mission_id,
                                         workflow_directory=self.store_dir)["mission"]
        self.assertIn("control:cancel_confirmed", view["holds"]["codes"])
        self.assertTrue(all(s["stop_confirmed"] for s in view["engagements"]["starts"]))

    # -- R2-11-a: every held digest, review rounds, drift semantics -----------

    def lease_path(self, workflow_id):
        return self.record(workflow_id)["workspace_lease"]["path_realpath"]

    def write_round(self, workflow_id, round_number, decision):
        from test_target_runtime import TARGET_TASK_ID
        directory = os.path.join(self.lease_path(workflow_id), ".herd", "state", "reviews")
        os.makedirs(directory, exist_ok=True)
        name = "%s-round-%02d.md" % (TARGET_TASK_ID, round_number)
        text = ("# Reviewer round %d\n\nReviewer: `reviewer1` / `sess-target`\n\n"
                "Protocol token: `%s`\n\n## Transcript\n\nHERD_DECISION: %s\n"
                % (round_number, decision, decision))
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        import hashlib
        return name, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def listing_with_rounds(self, rounds):
        """An observer listing EVERY round the target produced."""
        from test_target_runtime import TARGET_TASK_ID, real_shaped_observation

        def observer(repo_path):
            self.observe_calls.append(repo_path)
            raw = real_shaped_observation(status=self.target_task_status,
                                          **self.observation_overrides)
            raw["reviews"]["rounds"] = len(rounds)
            raw["reviews"]["total_files"] = len(rounds)
            raw["reviews"]["listed"] = [
                {"file": "%s-round-%02d.md" % (TARGET_TASK_ID, n), "round": n,
                 "decision": decision, "size": 120, "mtime": 1_000_040 + n}
                for n, decision in rounds]
            return raw
        self.observer = observer
        self.broker._observe = observer

    def findings(self, mission_id):
        state = self.service.get_state(mission_id)["record"]
        return state["reconciliations"][-1]["findings"]

    def kinds(self, mission_id, kind):
        return sorted(f["subject"] or "" for f in self.findings(mission_id)
                      if f["kind"] == kind)

    # -- R2-11-b: the P1-A6 candidate identity, observed read-only -------------

    @staticmethod
    def identity(*entries):
        """The P1-A6 identity of explicit ``(status, mode, blob, path)``
        entries, through ``pr_delivery.candidate.identity_digest`` over
        entries in the delivery contract's path order."""
        return candidate_module.identity_digest(sorted(
            [dict(zip(("status", "mode", "blob", "path"), e)) for e in entries],
            key=lambda e: e["path"].encode("utf-8")))

    @staticmethod
    def blob(content):
        """The git blob id of ``content``, computed independently."""
        data = content.encode("utf-8")
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def observation_receipt(self, base, head, entries, status=None, problem=None):
        """A candidate observation receipt as the Broker writes it (its own
        ``candidate_receipt``), for presenting a record state."""
        status = status or broker_module.CANDIDATE_STATUS_EXACT
        digest = self.identity(*entries) if entries else None
        return broker_module.candidate_receipt(
            {"status": status, "problem": problem, "detail": None,
             "entries": list(entries) or None, "digest": digest,
             "head": head, "base": base}, self.clock())

    def candidate_receipts(self, workflow_id):
        return [r for r in self.record(workflow_id)["receipts"]
                if broker_module.parse_candidate_receipt(r) is not None]

    def git(self, workflow_id, *argv):
        return run_git("-C", self.lease_path(workflow_id), *argv)

    def write(self, workflow_id, path, content, executable=False):
        full = os.path.join(self.lease_path(workflow_id), path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(full, 0o755 if executable else 0o644)

    def stage(self, workflow_id, path, content, executable=False):
        self.write(workflow_id, path, content, executable)
        self.git(workflow_id, "add", "--", path)

    def clean_herd_state(self, workflow_id):
        """Drop the fixture's review-state noise from the lease worktree
        (the verification fixture rewrites tracked review files there):
        what remains is exactly what a test stages."""
        self.git(workflow_id, "checkout", "-q", "--", ".herd")
        self.git(workflow_id, "clean", "-fdq", "--", ".herd")

    def verified(self, mission_id):
        """A VERIFIED Mission-origin record (one APPROVE round), its lease
        then cleaned of review-state noise."""
        workflow_id = self.dispatched(mission_id)
        self.write_round(workflow_id, 1, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE")])
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_VERIFIED)
        self.clean_herd_state(workflow_id)
        return workflow_id

    def one_pass(self, broker=None):
        """The two closing steps of a Runtime pass: the candidate
        observation, then the reconciliation report."""
        broker = broker or self.broker
        return (runtime_module.observe_mission_candidates(broker),
                runtime_module.reconcile_mission_workflows(broker))

    def record_candidate(self, mission_id, digest, locator="opaque:candidate"):
        """The Mission's own recorded candidate identity (what a human-
        reviewed candidate is compared with)."""
        self.op("record_artifact", mission_id, "candidate",
                mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT,
                ms.LOCATOR_KIND_OPAQUE_REFERENCE, locator, digest, True, [])

    def readme(self, workflow_id):
        """``(mode, blob)`` of README.md at the baseline."""
        line = self.git(workflow_id, "ls-tree", self.baseline, "README.md")
        mode, _kind, rest = line.split(" ", 2)
        return mode, rest.split("\t", 1)[0]

    # -- R2-11-b: canonical P1-A6 delivery records bound to the Mission ---------

    def delivery_dir(self):
        path = os.path.join(self.base, "delivery")
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path

    def delivery_broker(self):
        broker = self.gated_broker()
        broker.delivery_store_directory = self.delivery_dir()
        return broker

    def authorization_digest(self, mission_id):
        document = self.mstore.load()
        mission = document["missions"][mission_id]
        return document["authorizations"][mission["authorization_ids"][-1]][
            "authorization_digest_sha256"]

    def delivery_for(self, mission_id, workflow_id, entries, base):
        """A P1-A6 delivery record the UNCHANGED contract accepts (built by
        ``new_authorization``), bound to ``mission_id`` through its Mission
        parent block, delivering the leased workspace with ``entries`` as
        its candidate against ``base``."""
        from test_mission_core import delivery_record
        from pr_delivery import authorization as delivery_authorization
        template = delivery_record(mission_id, self.authorization_digest(mission_id),
                                   self.clock())
        authority = dict((key, copy.deepcopy(template[key]))
                         for key in delivery_authorization.AUTHORITY_KEYS
                         if key not in ("schema_version", "delivery_id"))
        path = self.lease_path(workflow_id)
        authority["repository"].update(realpath=path,
                                       git_dir_realpath=os.path.join(path, ".git"))
        authority["original_baseline"] = {"ref": "refs/heads/main", "commit_sha": base}
        ordered = sorted([dict(zip(("status", "mode", "blob", "path"), e)) for e in entries],
                         key=lambda e: e["path"].encode("utf-8"))
        digest = candidate_module.identity_digest(ordered)
        authority["candidate"] = {"entries": ordered, "entry_count": len(ordered),
                                  "identity_digest_sha256": digest}
        for item in authority["evidence"].values():
            item.update(candidate_identity_digest_sha256=digest, base_oid=base)
        return delivery_authorization.new_authorization(
            "prd-" + mission_id[-12:], authority, self.clock())

    def save_delivery(self, record):
        from pr_delivery import store as delivery_store
        store = delivery_store.DeliveryStore(self.delivery_dir())
        document = store.load()
        document["deliveries"][record["delivery_id"]] = record
        store.save(document)

    def succeeded(self, record, step, binding, observed=None, phase=None,
                  current_base=None):
        """``record`` holding ONE succeeded ``step`` receipt DERIVED by the
        delivery layer's own ``receipts.derive`` (validated), with the
        phase and base state the machine leaves after that step."""
        from test_mission_core import with_receipt
        from pr_delivery import authorization as delivery_authorization
        record = with_receipt(record, step, delivery_authorization.RECEIPT_SUCCEEDED,
                              self.clock(), binding=binding)
        record["steps"][step]["receipt"]["observed"] = observed
        if phase is not None:
            record["phase"] = phase
        if current_base is not None:
            record["base_state"]["current_base_oid"] = current_base
            record["base_state"]["refreshed_at"] = self.clock()
        delivery_authorization.validate_authorization(record)
        return record

    def attest(self, mission_id, record, step):
        """The REAL consumer path: the delivery layer's validating seam
        attests the receipt through the Mission Core's own operation."""
        from pr_delivery import mission_parent
        result = mission_parent.attest_validated_receipt(
            record, step, self.service,
            self.service.mint_state_operation_id(self.context),
            self.service.get_state(mission_id)["sequence"], self.context)
        self.assertTrue(result["valid"], result)
        return result

    def base_advance(self, workflow_id, name="base-advance.txt", content="upstream\n"):
        """A new base commit on top of the baseline changing ONE path
        disjoint from the candidate, built with plumbing only (the index
        and worktree are untouched)."""
        path = self.lease_path(workflow_id)
        index = os.path.join(self.base, "tmp-index-%s" % workflow_id[-6:])
        env = dict(os.environ, GIT_INDEX_FILE=index)
        run_git("-C", path, "read-tree", self.baseline, env=env)
        blob = self._hash(workflow_id, content)
        run_git("-C", path, "update-index", "--add", "--cacheinfo",
                "100644,%s,%s" % (blob, name), env=env)
        tree = run_git("-C", path, "write-tree", env=env)
        return run_git("-C", path, "commit-tree", tree, "-p", self.baseline,
                       "-m", "base advance")

    def _hash(self, workflow_id, content):
        full = os.path.join(self.base, "hash-input-%s" % workflow_id[-6:])
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
        return self.git(workflow_id, "hash-object", "-w", full)

    def refresh_to(self, workflow_id, old, new):
        """The effect P1-A6's BASE_REFRESH performs: a two-way read-tree
        carrying the staged candidate onto the new base, then the source
        ref (the lease's detached HEAD) moved from old to new."""
        self.git(workflow_id, "read-tree", "-m", "-u", old, new)
        self.git(workflow_id, "update-ref", "HEAD", new, old)

    def test_R8_verification_records_every_review_round_and_the_candidate(self):
        # (c) two rounds REJECT then APPROVE: both round digests are held
        # and reported, the standing is APPROVE, nothing contradicts.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        _name1, digest1 = self.write_round(workflow_id, 1, "REJECT")
        name2, digest2 = self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "REJECT"), (2, "APPROVE")])
        self.assertEqual(bridge.review_report(self.record(workflow_id)), "PENDING")
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_VERIFIED)
        rounds = broker_module.observed_review_rounds(entry)
        self.assertEqual([(n, d, digest) for n, d, _f, digest in rounds],
                         [(1, "REJECT", digest1), (2, "APPROVE", digest2)])
        self.assertEqual(rounds[1][2], name2)
        # The verification collection observed the leased workspace ONCE:
        # nothing is staged there, so the P1-A6 identity is UNAVAILABLE with
        # the delivery layer's own code (an empty candidate) — never a HEAD
        # fallback; HEAD is recorded separately and labelled as HEAD.
        [receipt] = self.candidate_receipts(workflow_id)
        candidate = broker_module.observed_candidate(entry)
        self.assertEqual(candidate["status"], broker_module.CANDIDATE_STATUS_UNAVAILABLE)
        self.assertEqual(candidate["problem"], "pr_delivery_candidate_empty")
        self.assertTrue(candidate["consistent"])
        self.assertEqual((candidate["base"], candidate["head"]),
                         (self.baseline, self.baseline))
        self.assertIsNone(candidate["identity"])
        self.assertEqual(receipt["digest"], receipts_module.candidate_binding(
            broker_module.CANDIDATE_STATUS_UNAVAILABLE, self.baseline, self.baseline,
            None, None, "pr_delivery_candidate_empty"))
        held = bridge.held_digests(entry)
        self.assertEqual(sorted(held), ["handoff", "observed_head", "review_round_1",
                                        "review_round_2", "verified_result"])
        self.assertEqual(held["observed_head"],
                         broker_module.head_commit_digest(self.baseline))
        self.assertEqual(held["review_round_1"], digest1)
        self.assertEqual(held["review_round_2"], digest2)
        self.assertEqual(held["verified_result"], entry["verified_result"]["digest"])
        self.assertEqual(bridge.review_report(entry), "APPROVE")
        reports = bridge.materialize_reports(entry, self.clock())
        self.assertEqual(reports["task"]["value"], "COMPLETE")
        self.assertEqual(reports["review"]["value"], "APPROVE")
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(outcomes[workflow_id].outcome,
                         runtime_module.OUTCOME_RECONCILED_CHANGED)
        recorded = self.service.get_state(mission_id)["record"]["reconciliations"][-1]
        self.assertEqual(recorded["sources"]["candidate"]["value"]["artifact_digests"], held)
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
        self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
        self.assertNotIn("review_not_approved", [f["kind"] for f in self.findings(mission_id)])
        # A second verification collection repeats no receipt.
        before = len(entry["receipts"])
        self.broker._record_verification_observations(
            entry, self.broker._collect_evidence(entry))
        self.assertEqual(len(entry["receipts"]), before)
        # APPROVE then REJECT: the latest round decides.
        other = self.ready_mission()
        other_workflow = self.dispatched(other)
        self.write_round(other_workflow, 1, "APPROVE")
        self.write_round(other_workflow, 2, "REJECT")
        self.listing_with_rounds([(1, "APPROVE"), (2, "REJECT")])
        outcome = self.act(other_workflow, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFY_REVIEW_NOT_APPROVE)
        entry = self.record(other_workflow)
        self.assertEqual([d for _n, d, _f, _g in broker_module.observed_review_rounds(entry)],
                         ["APPROVE", "REJECT"])
        self.assertEqual(bridge.review_report(entry), "REJECT")
        self.assertNotIn("verified_result", bridge.held_digests(entry))

    def test_R9_drift_is_scoped_to_the_authorized_revision(self):
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        entry = self.record(workflow_id)
        handoff_digest = entry["handoff"]["digest_sha256"]
        # (a) A Mission artifact the Runtime does not hold yet is exactly
        # that subject unconfirmed; the handoff it holds is confirmed.
        self.op("record_artifact", mission_id, "handoff",
                mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT,
                ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:handoff", handoff_digest,
                True, [])
        self.op("record_artifact", mission_id, "verified_result",
                mission_record.ARTIFACT_ROLE_VERIFICATION,
                ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:verified", "d" * 64, True, [])
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"),
                         ["verified_result"])
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
        self.assertEqual(result["reported_keys"], ["handoff"])
        # (b) A conflicting same-key digest is a contradiction naming the key.
        self.op("record_artifact", mission_id, "handoff",
                mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT,
                ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:handoff-2", "e" * 64, True, [])
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["handoff"])
        self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"),
                         ["verified_result"])
        # Accepted evidence referencing a held key is listed as held; a
        # referenced key the Runtime lacks is listed as not held.
        from mission_control import engineering as engineering_module
        evidence = self.op("submit_evidence", mission_id,
                           engineering_module.MANDATORY_REQUIREMENT_KEYS[0],
                           mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "f" * 64,
                           [self.service.get_state(mission_id)["record"]["artifacts"][1][
                               "artifact_id"]])
        self.clock.advance(1)
        self.service.accept_evidence(
            mission_id, self.service.mint_state_operation_id(CONTROL_CONTEXT),
            self.service.get_state(mission_id)["sequence"], evidence["evidence_id"],
            "f" * 64, CONTROL_CONTEXT)
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertEqual(result["accepted_subjects"], {"verified_result": False})
        # (f) An UNAUTHORIZED baseline move (no EDIT, no attested refresh)
        # is drift; a candidate identity other than the Mission's recorded
        # one contradicts it, naming the key.
        recorded = self.identity(("A", "100644", "1" * 40, "recorded.txt"))
        self.op("record_artifact", mission_id, "candidate",
                mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT,
                ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:candidate", recorded, True, [])
        moved = copy.deepcopy(entry)
        moved["receipts"] = list(entry["receipts"]) + [self.observation_receipt(
            base="c" * 40, head="c" * 40,
            entries=[("A", "100644", "2" * 40, "other.txt")])]
        result = bridge.reconcile_workflow(self.service, self.gate.context, moved,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.kinds(mission_id, "baseline_drift"), [""])
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["candidate", "handoff"])
        self.assertEqual(result["candidate_status"]["status"],
                         broker_module.CANDIDATE_STATUS_EXACT)
        # (d) An EDIT re-bases under a NEW authorized revision: the new
        # workflow's report records a fresh anchor with ZERO drift; the
        # earlier revision's records keep their revision, unrelabelled.
        first_records = list(self.service.get_state(mission_id)["record"]["reconciliations"])
        self.edit(mission_id, baseline={"ref": "refs/heads/main", "commit_sha": "c" * 40})
        self.approve(mission_id)
        self.op("activate_proof_contract", mission_id)
        self.op("observe_resource_readiness", mission_id, "build_host",
                ms.READINESS_READY, self.clock())
        rebased = self.bootstrap(mission_id)
        self.assertTrue(rebased["ok"], rebased)
        new_entry = self.record(rebased["workflow_id"])
        self.assertEqual(new_entry["approved_baseline"]["commit_sha"], "c" * 40)
        result = bridge.reconcile_workflow(self.service, self.gate.context, new_entry,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
        self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"), [])
        self.assertEqual(self.kinds(mission_id, "baseline_unobserved"), [])
        records = self.service.get_state(mission_id)["record"]["reconciliations"]
        self.assertEqual(records[:len(first_records)], first_records)
        self.assertEqual([r["observed_revision"] for r in records],
                         [1] * len(first_records) + [2])
        from mission import reconciliation as rc
        self.assertEqual(rc.baseline_anchor(records, 2),
                         bridge.baseline_identity_digest(new_entry))
        self.assertEqual(rc.baseline_anchor(records, 1),
                         bridge.baseline_identity_digest(entry))
        # WITHIN revision 2 an observation that moved the base and HEAD
        # without any attested receipt drifts again (the authorized
        # BASE_REFRESH / COMMIT cases are R12 / R13).
        seen = copy.deepcopy(new_entry)
        seen["receipts"] = list(new_entry["receipts"]) + [self.observation_receipt(
            base="c" * 40, head="c" * 40, entries=[("A", "100644", "3" * 40, "x.txt")])]
        result = bridge.reconcile_workflow(self.service, self.gate.context, seen,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
        moved = copy.deepcopy(seen)
        moved["receipts"] = list(seen["receipts"]) + [self.observation_receipt(
            base="e" * 40, head="e" * 40, entries=[("A", "100644", "3" * 40, "x.txt")])]
        result = bridge.reconcile_workflow(self.service, self.gate.context, moved,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.kinds(mission_id, "baseline_drift"), [""])
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["observed_head"])
        # The superseded first-revision record is NEVER reported as an
        # observation of revision 2: refused, no record written, nothing
        # relabelled.
        records_before = list(self.service.get_state(mission_id)["record"]["reconciliations"])
        reservations_before = len(self.mstore.load()["reservations"])
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertFalse(result["ok"])
        self.assertEqual(result["problem"], bridge.PROBLEM_REVISION_SUPERSEDED)
        self.assertEqual(self.service.get_state(mission_id)["record"]["reconciliations"],
                         records_before)
        self.assertEqual(len(self.mstore.load()["reservations"]), reservations_before)
        outcomes = runtime_module.reconcile_mission_workflows(self.broker)
        self.assertEqual(outcomes[workflow_id].problem, bridge.PROBLEM_REVISION_SUPERSEDED)
        self.assertTrue(outcomes[rebased["workflow_id"]].ok)

    def test_R10_candidate_is_the_p1a6_staged_identity_observed_read_only(self):
        EXACT = broker_module.CANDIDATE_STATUS_EXACT
        mission_id = self.ready_mission()
        workflow_id = self.verified(mission_id)
        broker = self.gated_broker()  # no delivery store: the leased workspace
        path = self.lease_path(workflow_id)
        # (a) Two DIFFERENT staged candidates on the SAME HEAD: distinct
        # identities, each exactly the P1-A6 digest of its own entries.
        self.stage(workflow_id, "feature.txt", "one\n")
        first = broker_module.capture_candidate(self.transport, path, self.baseline)
        self.git(workflow_id, "rm", "-q", "--cached", "feature.txt")
        self.stage(workflow_id, "feature.txt", "two\n")
        second = broker_module.capture_candidate(self.transport, path, self.baseline)
        one = self.identity(("A", "100644", self.blob("one\n"), "feature.txt"))
        two = self.identity(("A", "100644", self.blob("two\n"), "feature.txt"))
        self.assertNotEqual(one, two)
        self.assertEqual((first["status"], first["digest"], first["head"]),
                         (EXACT, one, self.baseline))
        self.assertEqual((second["status"], second["digest"], second["head"]),
                         (EXACT, two, self.baseline))
        # (b) The pass records the observation ONCE; unchanged passes record
        # no receipt, report ``changed`` false and leak no reservation.
        self.record_candidate(mission_id, two)
        receipts_before = len(self.candidate_receipts(workflow_id))
        observed, reconciled = self.one_pass(broker)
        self.assertEqual(observed[workflow_id].outcome, broker_module.OUTCOME_CANDIDATE_OBSERVED)
        self.assertEqual(reconciled[workflow_id].outcome, runtime_module.OUTCOME_RECONCILED_CHANGED)
        self.assertEqual(len(self.candidate_receipts(workflow_id)), receipts_before + 1)
        held = bridge.held_digests(self.record(workflow_id))
        self.assertEqual(held["candidate"], two)
        self.assertEqual(held["observed_head"], broker_module.head_commit_digest(self.baseline))
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
        self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"), [])
        observed, reconciled = self.one_pass(broker)
        reservations = len(self.mstore.load()["reservations"])
        mission_bytes = self.mission_bytes()
        for _ in range(3):
            observed, reconciled = self.one_pass(broker)
            self.assertEqual(observed, {})
            self.assertEqual(reconciled[workflow_id].outcome,
                             runtime_module.OUTCOME_RECONCILED_UNCHANGED)
        self.assertEqual(len(self.candidate_receipts(workflow_id)), receipts_before + 1)
        self.assertEqual(len(self.mstore.load()["reservations"]), reservations)
        self.assertEqual(self.mission_bytes(), mission_bytes)
        # (c)–(f) Each staged change is a DISTINCT identity and, against the
        # recorded one, a candidate contradiction naming the key.
        mode, readme_blob = self.readme(workflow_id)
        self.assertEqual(mode, "100644")
        feature = ("A", "100644", self.blob("two\n"), "feature.txt")

        def content():
            self.stage(workflow_id, "README.md", "changed readme\n")
            return [feature, ("M", "100644", self.blob("changed readme\n"), "README.md")]

        def mode_change():
            os.chmod(os.path.join(path, "README.md"), 0o755)
            self.git(workflow_id, "add", "--", "README.md")
            return [feature, ("M", "100755", readme_blob, "README.md")]

        def deletion():
            self.git(workflow_id, "rm", "-q", "--", "README.md")
            return [feature, ("D", "100644", readme_blob, "README.md")]

        def new_file():
            self.stage(workflow_id, "new.txt", "new\n")
            return [feature, ("A", "100644", self.blob("new\n"), "new.txt")]

        seen = {two}
        for label, change in (("content", content), ("mode", mode_change),
                              ("deletion", deletion), ("new", new_file)):
            with self.subTest(case=label):
                entries = change()
                expected = self.identity(*entries)
                self.assertNotIn(expected, seen)
                seen.add(expected)
                observed, reconciled = self.one_pass(broker)
                latest = broker_module.observed_candidate(self.record(workflow_id))
                self.assertEqual((latest["status"], latest["identity"]), (EXACT, expected))
                self.assertEqual(bridge.held_digests(self.record(workflow_id))["candidate"],
                                 expected)
                self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["candidate"])
                self.git(workflow_id, "reset", "-q", "--hard")
                self.stage(workflow_id, "feature.txt", "two\n")
        # Back at the recorded candidate: the contradiction resolves.
        self.one_pass(broker)
        self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
        # (g) An untracked file, then an unstaged edit, beside the exact staged
        # candidate: NOT-EXACT is reported — the staged identity alone is
        # never the candidate: the key is omitted, the Core records the
        # recorded candidate UNCONFIRMED, the pass names the problem.
        for label, disturb in (
                ("untracked", lambda: self.write(workflow_id, "untracked.txt", "u\n")),
                ("unstaged", lambda: self.write(workflow_id, "feature.txt", "edited\n"))):
            with self.subTest(case=label):
                disturb()
                observed, _reconciled = self.one_pass(broker)
                latest = broker_module.observed_candidate(self.record(workflow_id))
                self.assertEqual(
                    (latest["status"], latest["problem"], latest["identity"]),
                    (broker_module.CANDIDATE_STATUS_NOT_EXACT,
                     broker_module.PROBLEM_CANDIDATE_NOT_EXACT, two))
                self.assertNotIn("candidate", bridge.held_digests(self.record(workflow_id)))
                result = bridge.reconcile_workflow(self.service, self.gate.context,
                                                   self.record(workflow_id), self.clock())
                self.assertEqual(result["candidate_status"]["status"],
                                 broker_module.CANDIDATE_STATUS_NOT_EXACT)
                self.assertEqual(result["candidate_status"]["problem"],
                                 broker_module.PROBLEM_CANDIDATE_NOT_EXACT)
                self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"),
                                 ["candidate"])
                self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
                self.git(workflow_id, "reset", "-q", "--hard")
                self.git(workflow_id, "clean", "-fdq")
                self.stage(workflow_id, "feature.txt", "two\n")
        # (h) The candidate DISAPPEARS (index reset to HEAD): unavailable
        # with the P1-A6 problem — never the previous identity.
        self.one_pass(broker)
        self.assertEqual(bridge.held_digests(self.record(workflow_id))["candidate"], two)
        self.git(workflow_id, "reset", "-q", "--hard")
        self.one_pass(broker)
        latest = broker_module.observed_candidate(self.record(workflow_id))
        self.assertEqual((latest["status"], latest["problem"]),
                         (broker_module.CANDIDATE_STATUS_UNAVAILABLE,
                          "pr_delivery_candidate_empty"))
        self.assertNotIn("candidate", bridge.held_digests(self.record(workflow_id)))
        self.assertEqual(self.kinds(mission_id, "candidate_artifact_unobserved"), ["candidate"])
        # (i) A TAMPERED recorded observation (its digest, or a field of its
        # summary altered to another validly shaped value) is refused —
        # never reported — and the next pass records the true observation
        # again.
        self.stage(workflow_id, "feature.txt", "two\n")
        self.one_pass(broker)
        store = wa_store.WorkflowStore(self.store_dir)
        for label, tamper in (
                ("digest", lambda r: dict(r, digest="f" * 64)),
                ("identity", lambda r: dict(r, bounded_summary=r["bounded_summary"].replace(
                    "identity=%s" % two, "identity=%s" % ("f" * 64)))),
                ("base", lambda r: dict(r, bounded_summary=r["bounded_summary"].replace(
                    "base=%s" % receipts_module.parse_candidate_receipt(r)["base"],
                    "base=%s" % ("e" * 40))))):
            with self.subTest(case=label):
                document = store.load()
                receipts = document["workflows"][workflow_id]["receipts"]
                self.assertTrue(receipts_module.parse_candidate_receipt(
                    receipts[-1])["consistent"])
                altered = tamper(receipts[-1])
                self.assertNotEqual(altered, receipts[-1])
                receipts[-1] = altered
                store.save(document)
                entry = self.record(workflow_id)
                self.assertFalse(broker_module.observed_candidate(entry)["consistent"])
                self.assertNotIn("candidate", bridge.held_digests(entry))
                result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                                   self.clock())
                self.assertEqual(result["candidate_status"]["problem"],
                                 broker_module.PROBLEM_CANDIDATE_RECEIPT_TAMPERED)
                observed, _ = self.one_pass(broker)
                self.assertEqual(observed[workflow_id].outcome,
                                 broker_module.OUTCOME_CANDIDATE_OBSERVED)
                latest = broker_module.observed_candidate(self.record(workflow_id))
                self.assertTrue(latest["consistent"])
                self.assertEqual(latest["identity"], two)

    def test_R11_the_candidate_capture_leaves_the_index_untouched(self):
        # The Runtime transport's staged-candidate read (real git, the real
        # verb) and its porcelain read change neither the index bytes nor
        # its mtime, and take no index lock.
        from target_runtime.git_transport import GitTransport
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        self.clean_herd_state(workflow_id)
        self.stage(workflow_id, "feature.txt", "two\n")
        # A worktree file newer than the index would make a refreshing
        # command rewrite the index; the read-only capture must not.
        self.write(workflow_id, "feature.txt", "two\n")
        index = os.path.join(self.lease_path(workflow_id), ".git", "index")
        with open(index, "rb") as handle:
            before = handle.read()
        mtime = os.stat(index).st_mtime_ns
        observation = broker_module.capture_candidate(
            GitTransport(), self.lease_path(workflow_id), self.baseline)
        self.assertEqual(observation["status"], broker_module.CANDIDATE_STATUS_EXACT)
        self.assertEqual(observation["digest"],
                         self.identity(("A", "100644", self.blob("two\n"), "feature.txt")))
        with open(index, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(os.stat(index).st_mtime_ns, mtime)
        self.assertFalse(os.path.exists(index + ".lock"))
        # Teeth: a REFRESHING read of the same repository (plain ``git
        # status``, which may take the index lock) does rewrite it here, so
        # the unchanged bytes and mtime above are not vacuous.
        self.git(workflow_id, "status", "--porcelain")
        with open(index, "rb") as handle:
            refreshed = handle.read()
        self.assertTrue(refreshed != before or os.stat(index).st_mtime_ns != mtime)

    def _refresh_binding(self, workflow_id, old, new, identity):
        path = self.lease_path(workflow_id)
        return {"repository_realpath": path,
                "git_dir_realpath": os.path.join(path, ".git"),
                "remote_name": "origin", "remote_url_exact": "https://github.com/octo/repo.git",
                "remote_url_fetch": "/nonexistent/remote.git",
                "source_ref": "refs/heads/feature/p1-a6", "base_ref": "refs/heads/main",
                "old_base_oid": old, "new_base_oid": new, "fast_forward": True,
                "base_changed_paths_digest": "5" * 64,
                "candidate_identity_digest": identity}

    def test_R12_authorized_base_refresh_is_no_drift_the_same_move_without_it_is(self):
        from pr_delivery import authorization as delivery_authorization
        refresh = delivery_authorization.STEP_BASE_REFRESH
        for case in ("attested", "unattested", "artifact_only", "other_move"):
            with self.subTest(case=case):
                mission_id = self.ready_mission()
                workflow_id = self.verified(mission_id)
                broker = self.delivery_broker()
                self.stage(workflow_id, "feature.txt", "two\n")
                entries = [("A", "100644", self.blob("two\n"), "feature.txt")]
                identity = self.identity(*entries)
                self.record_candidate(mission_id, identity)
                record = self.delivery_for(mission_id, workflow_id, entries, self.baseline)
                self.save_delivery(record)
                self.one_pass(broker)
                self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
                self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
                # The P1-A6 BASE_REFRESH effect, within the SAME Mission
                # revision and the SAME delivery authorization.
                new_base = self.base_advance(workflow_id)
                self.refresh_to(workflow_id, self.baseline, new_base)
                refreshed = self.succeeded(
                    record, refresh,
                    self._refresh_binding(workflow_id, self.baseline, new_base, identity),
                    observed={"new_base_oid": new_base},
                    phase=delivery_authorization.PHASE_BASE_CURRENT, current_base=new_base)
                self.save_delivery(refreshed)
                receipt = refreshed["steps"][refresh]["receipt"]
                if case in ("attested", "other_move"):
                    self.attest(mission_id, refreshed, refresh)
                if case == "artifact_only":
                    # A Mission artifact naming the receipt, recorded by a
                    # caller — NOT the attesting operation: authorizes nothing.
                    self.op("record_artifact", mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                            "receipt:" + receipt["receipt_id"],
                            receipt["receipt_digest_sha256"], True, [])
                observed, reconciled = self.one_pass(broker)
                self.assertEqual(observed[workflow_id].outcome,
                                 broker_module.OUTCOME_CANDIDATE_OBSERVED)
                latest = broker_module.observed_candidate(self.record(workflow_id))
                self.assertEqual(
                    (latest["status"], latest["base"], latest["head"], latest["identity"]),
                    (broker_module.CANDIDATE_STATUS_EXACT, new_base, new_base, identity))
                held = bridge.held_digests(self.record(workflow_id), refreshed)
                self.assertEqual(held["baseline_receipt"], receipt["receipt_digest_sha256"])
                self.assertEqual(held["head_receipt"], receipt["receipt_digest_sha256"])
                self.assertEqual(held["candidate"], identity)
                if case in ("attested", "other_move"):
                    self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
                else:
                    self.assertEqual(self.kinds(mission_id, "baseline_drift"), [""])
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["observed_head"])
                if case == "other_move":
                    # The attested receipt authorized baseline→new_base ONLY:
                    # a further move to another base is not that move.
                    other_base = self.base_advance(workflow_id, "other.txt", "other\n")
                    self.refresh_to(workflow_id, new_base, other_base)
                    moved = copy.deepcopy(refreshed)
                    moved["base_state"]["current_base_oid"] = other_base
                    delivery_authorization.validate_authorization(moved)
                    self.save_delivery(moved)
                    self.one_pass(broker)
                    held = bridge.held_digests(self.record(workflow_id), moved)
                    self.assertNotIn("baseline_receipt", held)
                    self.assertNotIn("head_receipt", held)
                    self.assertEqual(self.kinds(mission_id, "baseline_drift"), [""])
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"), ["observed_head"])

    def test_R13_authorized_commit_keeps_the_candidate_an_unauthorized_one_drifts(self):
        from pr_delivery import authorization as delivery_authorization
        commit_step = delivery_authorization.STEP_COMMIT
        for case in ("attested", "unattested", "no_receipt", "artifact_only", "other_tree"):
            with self.subTest(case=case):
                mission_id = self.ready_mission()
                workflow_id = self.verified(mission_id)
                broker = self.delivery_broker()
                self.stage(workflow_id, "feature.txt", "two\n")
                entries = [("A", "100644", self.blob("two\n"), "feature.txt")]
                identity = self.identity(*entries)
                self.record_candidate(mission_id, identity)
                record = self.delivery_for(mission_id, workflow_id, entries, self.baseline)
                self.save_delivery(record)
                self.one_pass(broker)
                self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
                if case == "other_tree":
                    self.stage(workflow_id, "extra.txt", "extra\n")
                # The P1-A6 COMMIT effect: the staged candidate becomes a
                # commit on the base, within the same revision and delivery.
                self.git(workflow_id, "commit", "-q", "-m", "delivery")
                commit = self.git(workflow_id, "rev-parse", "HEAD")
                path = self.lease_path(workflow_id)
                binding = {
                    "repository_realpath": path,
                    "git_dir_realpath": os.path.join(path, ".git"),
                    "branch": "feature/p1-a6", "source_ref": "refs/heads/feature/p1-a6",
                    "head_before": self.baseline, "staged_sha256": "5" * 64,
                    "candidate_identity_digest": identity,
                    "expected_tree_oid": self.git(workflow_id, "rev-parse", "HEAD^{tree}"),
                    "committer_name": "Delivery Human",
                    "committer_email": "human@example.com", "message_sha256": "6" * 64}
                delivered = record
                if case != "no_receipt":
                    delivered = self.succeeded(
                        record, commit_step, binding, observed={"commit_oid": commit},
                        phase=delivery_authorization.PHASE_COMMITTED)
                    self.save_delivery(delivered)
                    receipt = delivered["steps"][commit_step]["receipt"]
                if case in ("attested", "other_tree"):
                    self.attest(mission_id, delivered, commit_step)
                if case == "artifact_only":
                    self.op("record_artifact", mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                            "receipt:" + receipt["receipt_id"],
                            receipt["receipt_digest_sha256"], True, [])
                self.one_pass(broker)
                latest = broker_module.observed_candidate(self.record(workflow_id))
                self.assertEqual((latest["status"], latest["head"]),
                                 (broker_module.CANDIDATE_STATUS_EXACT, commit))
                held = bridge.held_digests(self.record(workflow_id), delivered)
                self.assertEqual(held["observed_head"], broker_module.head_commit_digest(commit))
                self.assertNotIn("baseline_receipt", held)
                if case == "attested":
                    # The committed tree against the base IS the staged
                    # candidate: identity unchanged, HEAD move authorized.
                    self.assertEqual(latest["identity"], identity)
                    self.assertEqual(held["head_receipt"], receipt["receipt_digest_sha256"])
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"), [])
                    self.assertEqual(self.kinds(mission_id, "baseline_drift"), [])
                elif case == "other_tree":
                    # A commit whose tree identity differs: a contradiction of
                    # the recorded candidate, and the receipt (bound to the
                    # reviewed identity) does not relate to this HEAD.
                    self.assertNotEqual(latest["identity"], identity)
                    self.assertNotIn("head_receipt", held)
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"),
                                     ["candidate", "observed_head"])
                else:
                    self.assertEqual(latest["identity"], identity)
                    self.assertEqual(self.kinds(mission_id, "candidate_drift"),
                                     ["observed_head"])
                    self.assertEqual("head_receipt" in held, case != "no_receipt")

    def test_R14_the_owned_stop_is_bounded_and_never_closes_twice(self):
        # Start-claim decision item 5 (bounded cleanup): a cancel lands
        # during the start, so the owned stop runs; the engine's close HANGS.
        # Each owned-stop engine call is bounded: the stop stays PENDING
        # (a timeout is never absence), no second close is issued while the
        # first is in flight, and once it returns the next pass confirms
        # the stop ONLY by observed absence.
        import threading
        import time
        bound = mock.patch.object(broker_module, "OWNED_STOP_WAIT_SECONDS", 0.3)
        bound.start()
        self.addCleanup(bound.stop)
        mission_id, workflow_id = self.prepared()
        release = threading.Event()
        self.addCleanup(release.set)
        issued = []
        real_close = self.engine.close_workspace

        def hanging_close(workspace_id):
            issued.append(workspace_id)
            release.wait(30)
            return real_close(workspace_id)
        self.engine.close_workspace = hanging_close
        broker = self.gated_broker()
        self.engine.before_start = lambda: self.service.request_cancel(mission_id)
        began = time.monotonic()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH, broker=broker)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertIn("owned stop PENDING", outcome.detail)
        self.assertIn("OwnedStopBoundExceeded", outcome.detail)
        self.assertEqual(issued, [WORKSPACE_ID])
        self.assertTrue(broker_module.INFLIGHT_CLOSES.holds(WORKSPACE_ID))
        start = self.starts(mission_id)[0]
        self.assertTrue(start["settlement"]["stop_pending"] or start["stop_requested"])
        self.assertFalse(broker_module.mission_gate_module.start_stop_confirmed(start))
        self.assertEqual(len(self.engine.live), 1)
        # A later pass while the close still hangs: no second close.
        self.engine.before_start = None
        runtime_module.process_once(broker)
        self.assertEqual(issued, [WORKSPACE_ID])
        self.assertFalse(broker_module.mission_gate_module.start_stop_confirmed(
            self.starts(mission_id)[0]))
        # The close returns (late): the in-flight mark is released, and the
        # next pass observes absence and confirms, issuing nothing more.
        release.set()
        deadline = time.monotonic() + 10
        while broker_module.INFLIGHT_CLOSES.holds(WORKSPACE_ID):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(self.engine.live, [])
        runtime_module.process_once(broker)
        self.assertEqual(issued, [WORKSPACE_ID])
        self.assertTrue(broker_module.mission_gate_module.start_stop_confirmed(
            self.starts(mission_id)[0]))
        self.assertEqual(self.unresolved(workflow_id), {})
        # The remaining limit is stated where the Operator and the
        # Supervisor read the Mission, never silent.
        limitations = status_module.read_status(
            self.service, mission_id, workflow_directory=self.store_dir)["limitations"]
        self.assertTrue(any("carry no timeout" in line and "observed absence" in line
                            for line in limitations))
        self.assertTrue(any("does not pause an engineering session" in line
                            for line in limitations))

    def test_R15_a_hanging_listing_leaves_the_stop_pending(self):
        import threading
        import time
        bound = mock.patch.object(broker_module, "OWNED_STOP_WAIT_SECONDS", 0.3)
        bound.start()
        self.addCleanup(bound.stop)
        mission_id, workflow_id = self.prepared()
        release = threading.Event()
        self.addCleanup(release.set)
        hanging = {"on": False}
        real_listing = self.engine.live_listing

        def listing():
            if hanging["on"]:
                release.wait(30)
            return real_listing()
        self.engine.live_listing = listing
        broker = self.gated_broker()

        def cancel_then_hang():
            self.service.request_cancel(mission_id)
            hanging["on"] = True
        self.engine.before_start = cancel_then_hang
        began = time.monotonic()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH, broker=broker)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertIn("live workspace listing unreadable (OwnedStopBoundExceeded)",
                      outcome.detail)
        self.assertEqual(self.engine.close_calls, [])
        start = self.starts(mission_id)[0]
        self.assertFalse(broker_module.mission_gate_module.start_stop_confirmed(start))
        # The listing answers again: the next pass stops and confirms.
        hanging["on"] = False
        release.set()
        self.engine.before_start = None
        runtime_module.process_once(broker)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertTrue(broker_module.mission_gate_module.start_stop_confirmed(
            self.starts(mission_id)[0]))

    def test_R16_a_control_never_drops_a_retained_known_result(self):
        # S4e + S-V together: an abandoned start's LATE result could not be
        # persisted (the source refuses past the hand-over bound), so the
        # known identity is RETAINED in-process for the owner. A hold, a
        # cancel or an EDIT landing then must not drop it; retention keeps
        # the record out of cleanup and pruning even past its deadline;
        # once the source answers, the owner's pass records the identity,
        # performs the ONE owned close and confirms by observed absence.
        import time
        from mission import store as mission_store_module
        for control in ("hold", "cancel", "edit"):
            with self.subTest(control=control):
                self.engine.reset()
                mission_id, workflow_id, started, proceed, _ = self.abandoned_start()
                outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
                self.assertIn("abandoned", (outcome.detail or "") + (outcome.problem or ""))
                start = self.starts(mission_id)[0]
                self.assertEqual(start["settlement"]["outcome"], "uncertain")
                key = (self.gate.owner_ref(self.record(workflow_id), 1), start["start_id"])
                real_observe = self.service.observe_engagement_stop

                def fail_identity(mission, op_id, seq, start_id, owner_ref, absent, detail,
                                  identity, context, real_observe=real_observe):
                    if identity is not None:
                        raise mission_store_module.MissionStoreError("disk gone")
                    return real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                        detail, identity, context=context)
                with mock.patch.object(self.service, "observe_engagement_stop",
                                       fail_identity), \
                        mock.patch.object(broker_module, "MAX_LATE_HANDOVER_ATTEMPTS", 2), \
                        mock.patch.object(broker_module, "LATE_HANDOVER_RETRY_SECONDS", 0.01):
                    proceed.set()
                    deadline = time.monotonic() + 20
                    while broker_module.RETAINED_HANDOVERS.get(key) is None:
                        self.assertLess(time.monotonic(), deadline, control)
                        time.sleep(0.01)
                    # The control lands while the known result is retained.
                    if control == "hold":
                        self.service.request_hold(mission_id)
                    elif control == "cancel":
                        self.service.request_cancel(mission_id)
                    else:
                        self.edit(mission_id)
                    self.assertIsNotNone(broker_module.RETAINED_HANDOVERS.get(key), control)
                    entry = self.record(workflow_id)
                    retention_deadline = entry[wa_record.RETENTION_KEY]["deadline_at"]
                    self.assertTrue(wa_store.retention_protects(entry, retention_deadline + 1))
                    # (Earlier subtests' resolved records may be candidates
                    # by then; THIS record must not be.)
                    self.assertNotIn(workflow_id, [w for w, _ in self.cleanup_candidates(
                        retention_deadline + 1)], control)
                    # The owner's pass while the source still refuses closes
                    # nothing and keeps the retained result.
                    runtime_module.process_once(self.gated_broker())
                    self.assertEqual(self.engine.close_calls, [], control)
                    self.assertEqual(len(self.engine.live), 1, control)
                    self.assertIsNotNone(broker_module.RETAINED_HANDOVERS.get(key), control)
                # The source answers: the owner's pass consumes the retained
                # identity — recorded first, then the single close, then absence.
                runtime_module.process_once(self.gated_broker())
                start = self.starts(mission_id)[0]
                self.assertEqual(ms.start_identity(start)["workspace_id"], WORKSPACE_ID)
                self.assertTrue(ms.start_stop_confirmed(start), control)
                self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], control)
                self.assertEqual(self.engine.live, [], control)
                self.assertEqual(len(self.engine.starts), 1, control)
                self.assertEqual(self.engine.tasks, [], control)
                self.assertIsNone(broker_module.RETAINED_HANDOVERS.get(key), control)
                self.assertEqual(self.unresolved(workflow_id), {}, control)

    def test_R17_a_hold_and_resume_during_the_start_never_drop_its_stop(self):
        # R15-1 through the real gated Broker: while the runtime start's
        # engine call is IN FLIGHT the human holds and resumes. The admitted
        # start completes, but its settlement owes the stop the hold
        # recorded: the owned close runs, the objective is NEVER handed
        # over, and the next pass confirms the stop only by absence.
        mission_id, workflow_id = self.prepared()

        def hold_then_resume():
            self.service.request_hold(mission_id)
            self.service.release_hold(mission_id)
        self.engine.before_start = hold_then_resume
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_STOP_REQUIRED)
        self.assertIn("hold requested on an open claim", outcome.detail)
        [start] = self.starts(mission_id)
        self.assertEqual(start["point"], ms.START_POINT_RUNTIME)
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIn("hold requested on an open claim", start["stop_requested"]["reason"])
        self.assertFalse(ms.hold_active(self.service.get_state(mission_id)["record"]))
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        runtime_module.process_once(self.gated_broker())
        [start] = self.starts(mission_id)
        self.assertTrue(ms.start_stop_confirmed(start))
        self.assertEqual(self.engine.tasks, [])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])

    def test_R18_absence_is_derived_only_from_a_complete_listing(self):
        # R15-5: the owned stop derives absence ONLY from a complete live
        # listing. An unavailable or malformed listing — None, a mapping, a
        # non-object entry, an entry without its id — leaves the stop
        # PENDING and never raises, at the INITIAL branch and after the
        # close alike; a valid listing still confirms.
        from test_mission_engagement import AGENT_NAMES
        mission_id, workflow_id = self.prepared()
        entry = self.record(workflow_id)
        identity = {"workspace_id": WORKSPACE_ID, "agent_names": list(AGENT_NAMES),
                    "task_id": None}
        live_entry = {"workspace_id": WORKSPACE_ID, "agent_names": list(AGENT_NAMES)}
        malformed = {
            "None": None,
            "mapping": {},
            "non-object entry": [None],
            "entry without id": [{"workspace_id": "ws-other", "agent_names": ["x"]},
                                 {"label": "no id"}],
            "empty id": [{"workspace_id": "", "agent_names": ["x"]}],
        }
        for label, listing in malformed.items():
            with self.subTest(branch="initial", listing=label):
                self.engine.close_calls = []
                self.engine.live_listing = lambda listing=listing: listing
                broker = self.gated_broker()
                absent, detail = broker._owned_stop(entry, identity)
                self.assertFalse(absent, detail)
                self.assertIn("unavailable or malformed", detail)
                self.assertIn("PENDING", detail)
                self.assertEqual(self.engine.close_calls, [])
        for label, listing in malformed.items():
            with self.subTest(branch="after close", listing=label):
                self.engine.close_calls = []
                calls = {"n": 0}

                def scripted(listing=listing, calls=calls):
                    calls["n"] += 1
                    # proof, revalidation before the close, then the fresh
                    # post-close listing under test.
                    return [dict(live_entry)] if calls["n"] <= 2 else listing
                self.engine.live_listing = scripted
                broker = self.gated_broker()
                absent, detail = broker._owned_stop(entry, identity)
                self.assertFalse(absent, detail)
                self.assertIn("absence is NOT derived", detail)
                self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        # Valid listings: absent from a complete listing → confirmed without
        # a close; present → the owned close, then absent → confirmed.
        self.engine.close_calls = []
        self.engine.live_listing = lambda: [{"workspace_id": "ws-other",
                                             "agent_names": ["x"]}]
        absent, detail = self.gated_broker()._owned_stop(entry, identity)
        self.assertTrue(absent, detail)
        self.assertEqual(self.engine.close_calls, [])
        calls = {"n": 0}

        def closes(calls=calls):
            calls["n"] += 1
            return [dict(live_entry)] if calls["n"] <= 2 else []
        self.engine.live_listing = closes
        absent, detail = self.gated_broker()._owned_stop(entry, identity)
        self.assertTrue(absent, detail)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])

    def test_R7_cancel_before_task_binding_distinguishes_never_started(self):
        # Never started: a cancel before the fence admits nothing and the
        # confirmation counts no start. Unresolved dispatch: an abandoned
        # start (no identity) is settled UNCERTAIN by the owner and can
        # never be confirmed absent — the cancel stays unconfirmed.
        never = self.ready_mission()
        self.service.request_cancel(never)
        result = self.bootstrap(never)
        self.assertEqual(result["problem"], gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(self.rows(), {})
        confirmed = self.service.confirm_cancel(never, "never started")
        self.assertEqual(confirmed["progress"], ms.PROGRESS_ABANDONED)
        confirmation = self.service.mission_controls(never)["cancel_request"]["confirmation"]
        self.assertIs(confirmation["starts_never_started"], True)
        self.assertEqual(confirmation["starts_confirmed"], [])
        self.assertEqual(self.starts(never), [])
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertIn("abandoned", (outcome.detail or "") + (outcome.problem or ""))
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIsNone(broker_module.mission_gate_module.start_identity(start))
        self.service.request_cancel(mission_id)
        with self.assertRaises(mission_record.MissionError) as caught:
            self.service.confirm_cancel(mission_id)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_CANCEL_UNCONFIRMED)
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertIn("control:cancel_requested", view["holds"]["codes"])
        self.assertFalse(view["engagements"]["starts"][0]["stop_confirmed"])
        self.assertIsNone(view["engagements"]["starts"][0]["identity"])
        proceed.set()

    def test_R19_a_confirmed_cancel_admits_the_owned_cleanup_and_nothing_else(self):
        # R15-3: ownership-safe cleanup has its OWN admission. After a
        # cancel it WAITS while a stop is unconfirmed, then the release
        # handler's retention check still refuses a retained record, and
        # once the cancel is confirmed (retention released) the release
        # REACHES the handler — ownership proven, the owned directory
        # removed, the lease released. No engineering action ever passes
        # through the cleanup door, and every engineering boundary stays
        # refused.
        from target_runtime import ownership as ownership_module
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        lease_path = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        self.assertTrue(os.path.isdir(lease_path))
        spawned = (len(self.spawn_requests), len(self.engine.starts), len(self.engine.tasks))
        cancelled = self.service.request_cancel(mission_id, "operator cancel")
        self.assertEqual(cancelled["stops_requested"], 2)
        # Engineering is refused by the cancel (the owner's recovery runs
        # inside the action first; the close leaves the workspace listed,
        # so nothing is confirmed).
        self.engine.close_leaves_visible = True
        blocked = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(blocked.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        # (a) Stop unconfirmed: the cleanup WAITS — held, reversible, the
        # release handler never runs, nothing is released.
        waited = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertFalse(waited.ok)
        self.assertEqual((waited.outcome, waited.problem),
                         (broker_module.OUTCOME_MISSION_HELD,
                          gate_module.PROBLEM_CLEANUP_AWAITS_STOP))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(os.path.isdir(lease_path))
        # (b) The sessions leave; the release's recovery confirms absence,
        # the cleanup is ADMITTED, and the handler's RETENTION check refuses
        # (the cancel is not confirmed yet, the deadline has not passed).
        self.engine.close_leaves_visible = False
        self.engine.live = []
        retained = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(retained.problem, broker_module.PROBLEM_RETENTION_PROTECTED)
        self.assertTrue(all(ms.start_stop_confirmed(s) for s in self.starts(mission_id)))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(os.path.isdir(lease_path))
        # (c) The cancel is confirmed: the Mission is terminal (ABANDONED).
        # Every ENGINEERING boundary refuses TERMINALLY; the cleanup
        # admission admits.
        confirmed = self.service.confirm_cancel(mission_id)
        self.assertEqual(confirmed["progress"], ms.PROGRESS_ABANDONED)
        entry = self.record(workflow_id)
        for boundary in gate_module.BOUNDARIES:
            admission = self.gate.admit(entry, boundary)
            self.assertFalse(admission.ok, boundary)
            self.assertEqual(admission.classification, gate_module.CLASS_TERMINAL, boundary)
        self.assertNotIn(gate_module.BOUNDARY_CLEANUP, gate_module.BOUNDARIES)
        with self.assertRaises(ValueError):
            self.gate.admit(entry, gate_module.BOUNDARY_CLEANUP)
        self.assertTrue(self.gate.admit_cleanup(entry).ok)
        # No other action passes through the cleanup door: each engineering
        # action is refused, spends nothing, starts nothing, and never
        # consults the cleanup admission.
        cleanup_calls = []
        real_admit_cleanup = self.gate.admit_cleanup

        def spy_cleanup(record):
            # (the workflow admitted, the NAMED call site that asked)
            cleanup_calls.append((record["workflow_id"], sys._getframe(1).f_code.co_name))
            return real_admit_cleanup(record)
        with mock.patch.object(self.gate, "admit_cleanup", spy_cleanup):
            for action in broker_module.BROKER_ACTIONS:
                if action == broker_module.ACTION_RELEASE:
                    continue
                outcome = self.act(workflow_id, action)
                self.assertTrue(outcome.problem is not None, action)
                self.assertEqual(cleanup_calls, [], action)
            self.assertEqual((len(self.spawn_requests), len(self.engine.starts),
                              len(self.engine.tasks)), spawned)
            self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
            # (d) The release: retention released by the confirmed cancel
            # inside the same action, the cleanup admitted, the handler RUN
            # with its ownership proofs — the control-side child record the
            # spawn wrote names this lease and its bound task, the fresh
            # listing no longer shows its workspace (positive evidence that
            # no session remains), so the owned directory is removed and
            # the lease released.
            from test_target_runtime import real_shaped_spawn_result
            from test_mission_engagement import AGENTS
            child = real_shaped_spawn_result(
                lease_path, ownership_module.recorded_task_id(self.record(workflow_id)),
                self.control)["child_record"]
            child.update(workspace_id=WORKSPACE_ID, agents=dict(AGENTS))
            self.spawn_record_overrides = {"records": [child]}
            owns_workspace = mock.patch.object(
                ownership_module, "owns_workspace", wraps=ownership_module.owns_workspace)
            rows = []
            real_row = ownership_module.CleanupReport.record

            def row(report, kind, name, verdict, ok=True, detail=None):
                rows.append((kind, name, verdict, ok, detail))
                return real_row(report, kind, name, verdict, ok=ok, detail=detail)
            closes_before = list(self.engine.close_calls)
            with owns_workspace as proofs, mock.patch.object(
                    ownership_module.CleanupReport, "record", row):
                released = self.act(workflow_id, broker_module.ACTION_RELEASE)
            # "... and nothing else": only THIS workflow is admitted ...
            self.assertEqual(set(wid for wid, _site in cleanup_calls), {workflow_id})
            # ... at exactly the three named sites of the release (a fourth
            # fails loudly): the action boundary; — Task 8 ownership
            # correction — FRESH again at the effect boundary, after the
            # blocking evidence reads, before the session verdict; and — Task 8
            # R21-1 — FRESH once more at the DESTRUCTIVE boundary, after the
            # hold reads, immediately before the workspace relinquish.
            self.assertEqual([site for _wid, site in cleanup_calls],
                             ["_mission_admission", "_cleanup_admission_problem",
                              "_boundary_admission"])
        # The EFFECT, once: the release closes nothing (the owned stop's two
        # attempts above stay the only closes), observes the workspace absent
        # exactly once, and reclaims once.
        self.assertEqual(self.engine.close_calls, closes_before)
        self.assertEqual([r for r in rows if r[0] == "workspace_session"], [
            ("workspace_session", WORKSPACE_ID, ownership_module.OWNED, True,
             "absent from a complete fresh listing; nothing to close")])
        cleanups = self.receipts(workflow_id, broker_module.CLEANUP_RECEIPT_MARKER)
        self.assertEqual(len(cleanups), 1)
        self.assertIn("cleanup complete", cleanups[0])
        self.assertTrue(released.ok, (released.problem, released.detail))
        self.assertGreaterEqual(proofs.call_count, 1)
        entry = self.record(workflow_id)
        self.assertEqual(entry[wa_record.RETENTION_KEY]["release_reason"],
                         wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertIsNotNone(entry["workspace_lease"]["released_at"],
                             (released.outcome, released.detail))
        self.assertFalse(os.path.exists(lease_path))
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual((len(self.spawn_requests), len(self.engine.starts),
                          len(self.engine.tasks)), spawned)
        # Released once: no longer a cleanup candidate; a second release is
        # refused by the handler (the lease is gone), never a second close.
        self.assertEqual(self.cleanup_candidates(), [])
        closes = list(self.engine.close_calls)
        again = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertFalse(again.ok)
        self.assertEqual(self.engine.close_calls, closes)

    def test_R20_a_cancel_after_terminal_completion_is_recovered_and_never_pruned(self):
        # R15-2: a cancel that lands AFTER the workflow's terminal completion
        # marks its SETTLED starts; the record itself still reads
        # ``settled:completed stop=none``. Recovery is scheduled from the
        # CANONICAL obligation, and neither cleanup candidacy nor pruning —
        # past the retention deadline, by any caller — removes the record
        # while that stop is outstanding (or cannot be read).
        mission_id = self.ready_mission()
        workflow_id = self.verified(mission_id)
        completed = self.act(workflow_id, broker_module.ACTION_COMPLETE)
        self.assertTrue(completed.ok, (completed.problem, completed.detail))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.assertEqual(self.unresolved(workflow_id), {})
        self.assertEqual(self.gate.obligations(self.record(workflow_id)), [])
        cancelled = self.service.request_cancel(mission_id, "late cancel")
        self.assertEqual(cancelled["stops_requested"], 2)
        entry = self.record(workflow_id)
        # The record's own evidence is unchanged — only the canonical record
        # knows the stop is owed.
        self.assertEqual(self.unresolved(workflow_id), {})
        starts = self.starts(mission_id)
        self.assertEqual(sorted(self.gate.obligations(entry)),
                         sorted((s["start_id"], "stop not confirmed") for s in starts))
        deadline = entry[wa_record.RETENTION_KEY]["deadline_at"]
        later = deadline + 1
        self.assertFalse(wa_store.retention_protects(entry, later))
        from test_workflow_authority import mission_core_record
        another = mission_core_record(self.authorized_record("wf-mission-r20"))
        protected = gate_module.canonically_protected(self.service)
        with mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", 1):
            for label, predicate in (("no canonical read", None),
                                     ("canonical predicate", protected)):
                document = self.broker.store.load()
                self.assertEqual(wa_store._prune_inactive(document, later, predicate), 0,
                                 label)
                self.assertEqual(wa_store.add_workflow(document, another, later, predicate),
                                 (False, wa_store.PROBLEM_STORE_FULL, 0), label)
                self.assertIn(workflow_id, document["workflows"], label)
            # A source that cannot answer protects too (never read as "none").
            with mock.patch.object(self.service, "get_state",
                                   side_effect=mission_store.MissionStoreError("down")):
                self.assertIsNone(self.gate.obligations(entry))
                self.assertEqual(wa_store._prune_inactive(
                    self.broker.store.load(), later, protected), 0)
                self.assertEqual(self.cleanup_candidates(later), [])
        self.assertEqual(self.cleanup_candidates(later), [])
        # The Runtime pass schedules the owner's recovery from the canonical
        # obligation alone: the owned stop runs (the close leaves the
        # workspace listed: pending, now recorded on the record too).
        self.engine.close_leaves_visible = True
        processed = runtime_module.process_once(self.gated_broker())
        recovery = [o for label, o in processed[workflow_id]
                    if label == runtime_module.RECOVERY_LABEL]
        self.assertEqual(len(recovery), 1)
        self.assertTrue(recovery[0].ok, (recovery[0].problem, recovery[0].detail))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        self.assertEqual(set(self.unresolved(workflow_id).values()),
                         {broker_module.START_STATE_STOP_PENDING})
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        # The sessions leave: the next pass confirms by observed absence;
        # the obligation is discharged, the cancel can be confirmed, the
        # retention is released and only then is the record cleaned up —
        # and prunable by the canonical predicate.
        self.engine.close_leaves_visible = False
        self.engine.live = []
        runtime_module.process_once(self.gated_broker())
        self.assertTrue(all(ms.start_stop_confirmed(s) for s in self.starts(mission_id)))
        self.assertEqual(self.unresolved(workflow_id), {})
        self.assertEqual(self.gate.obligations(self.record(workflow_id)), [])
        self.assertEqual(self.service.confirm_cancel(mission_id)["progress"],
                         ms.PROGRESS_ABANDONED)
        # The control-side child record the spawn wrote (lease + bound task),
        # so the release proves that no session remains.
        from target_runtime import ownership as ownership_module
        from test_target_runtime import real_shaped_spawn_result
        from test_mission_engagement import AGENTS
        lease_path = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        child = real_shaped_spawn_result(
            lease_path, ownership_module.recorded_task_id(self.record(workflow_id)),
            self.control)["child_record"]
        child.update(workspace_id=WORKSPACE_ID, agents=dict(AGENTS))
        self.spawn_record_overrides = {"records": [child]}
        processed = runtime_module.process_once(self.gated_broker())
        entry = self.record(workflow_id)
        self.assertEqual(entry[wa_record.RETENTION_KEY]["release_reason"],
                         wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertIsNotNone(entry["workspace_lease"]["released_at"], processed[workflow_id])
        self.assertEqual(len(self.engine.starts), 1)
        with mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", 1):
            document = self.broker.store.load()
            self.assertEqual(wa_store._prune_inactive(document, later), 0)
            self.assertEqual(wa_store._prune_inactive(document, later, protected), 1)
            self.assertNotIn(workflow_id, document["workflows"])

    def test_R21_the_cleanup_admission_waits_only_on_what_it_names(self):
        # R15-3, at the gate: the cleanup admission's closed cases. It waits
        # (HOLD, reversible) on a hold without a cancel, an unreadable
        # source, an unsettled start and an unconfirmed stop; it refuses a
        # Mission the registry does not hold and a v2 record as ``admit``
        # does; a missing dependency is the dependency refusal; nothing else
        # — a cancel, terminal progress — refuses it.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        entry = self.record(workflow_id)
        admitted = self.gate.admit_cleanup(entry)
        self.assertEqual((admitted.ok, admitted.boundary),
                         (True, gate_module.BOUNDARY_CLEANUP))
        # A hold (no cancel) pauses the cleanup; the lift admits it again.
        self.service.request_hold(mission_id)
        held = self.gate.admit_cleanup(entry)
        self.assertEqual((held.ok, held.problem, held.classification, held.boundary),
                         (False, gate_module.PROBLEM_HOLD_ACTIVE, gate_module.CLASS_HOLD,
                          gate_module.BOUNDARY_CLEANUP))
        self.service.release_hold(mission_id)
        self.assertTrue(self.gate.admit_cleanup(entry).ok)
        # The source cannot answer: a HOLD naming the store's problem.
        with mock.patch.object(self.service, "get_state",
                               side_effect=mission_store.MissionStoreError("disk gone")):
            unavailable = self.gate.admit_cleanup(entry)
        self.assertEqual((unavailable.problem, unavailable.classification),
                         (gate_module.PROBLEM_SOURCE_UNAVAILABLE, gate_module.CLASS_HOLD))
        # Not the gate's record, or a Mission the registry does not hold.
        from test_workflow_authority import mission_core_record
        stranger = mission_core_record(self.authorized_record("wf-mission-r21"))
        unknown = self.gate.admit_cleanup(stranger)
        self.assertEqual((unknown.problem, unknown.classification),
                         (gate_module.PROBLEM_UNKNOWN_MISSION, gate_module.CLASS_TERMINAL))
        v2 = self.gate.admit_cleanup(self.authorized_record("wf-v2-r21"))
        self.assertEqual((v2.problem, v2.classification),
                         (gate_module.PROBLEM_NOT_MISSION_ORIGIN, gate_module.CLASS_TERMINAL))
        # A missing dependency: the dependency refusal (zero effects).
        with mock.patch.object(self.gate, "missing_guards", return_value=("absent_guard",)):
            dependency = self.gate.admit_cleanup(entry)
        self.assertEqual(dependency.classification, gate_module.CLASS_DEPENDENCY)
        # A hold, THEN a cancel: the hold can no longer be lifted, and the
        # cleanup follows the cancel — it waits only on the owed stops.
        self.service.request_hold(mission_id)
        self.service.request_cancel(mission_id)
        with self.assertRaises(mission_record.MissionError):
            self.service.release_hold(mission_id)
        owed = self.gate.admit_cleanup(entry)
        self.assertEqual((owed.problem, owed.classification),
                         (gate_module.PROBLEM_CLEANUP_AWAITS_STOP, gate_module.CLASS_HOLD))
        self.assertEqual(owed.detail.count("(stop not confirmed)"), 2)
        runtime_module.process_once(self.gated_broker())
        self.assertTrue(all(ms.start_stop_confirmed(s) for s in self.starts(mission_id)))
        self.assertTrue(self.service.mission_controls(mission_id)["hold_active"])
        self.assertEqual(self.service.confirm_cancel(mission_id)["progress"],
                         ms.PROGRESS_ABANDONED)
        self.assertTrue(self.gate.admit_cleanup(self.record(workflow_id)).ok)
        # An UNSETTLED start of the workflow: the cleanup waits for its owner.
        other_mission, other_workflow, _started, _proceed, _ = self.abandoned_start()

        def refuse(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start", refuse):
            outcome = self.act(other_workflow, broker_module.ACTION_DISPATCH)
            self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
            self.assertIsNone(self.starts(other_mission)[0]["settlement"])
            unsettled = self.gate.admit_cleanup(self.record(other_workflow))
        self.assertEqual((unsettled.problem, unsettled.classification),
                         (gate_module.PROBLEM_CLEANUP_AWAITS_STOP, gate_module.CLASS_HOLD))
        self.assertIn("(unsettled)", unsettled.detail)

    def test_R22_a_renumbered_review_receipt_on_a_live_record_reports_pending(self):
        # R16-1 end to end: the Broker's own verification collection records
        # rounds 1 REJECT, 2 APPROVE, 3 REJECT; the STORED latest receipt is
        # renumbered 3 -> 1 in place (digest unchanged). The reconciliation
        # reports PENDING — never round 2's APPROVE — names the tampering
        # and omits round 3; the owner's next collection re-records round 3
        # after it and the standing returns to REJECT with all three digests.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        rounds = [(1, "REJECT"), (2, "APPROVE"), (3, "REJECT")]
        digests = {n: self.write_round(workflow_id, n, decision)[1]
                   for n, decision in rounds}
        self.listing_with_rounds(rounds)
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFY_REVIEW_NOT_APPROVE)
        entry = self.record(workflow_id)
        self.assertEqual(bridge.review_report(entry), "REJECT")
        self.assertEqual({n: bridge.held_digests(entry)["review_round_%d" % n]
                          for n in (1, 2, 3)}, digests)
        store = wa_store.WorkflowStore(self.store_dir)
        document = store.load()
        stored = document["workflows"][workflow_id]["receipts"]
        [position] = [i for i, r in enumerate(stored)
                      if r["bounded_summary"].startswith("review round 3:")]
        original = dict(stored[position])
        stored[position] = dict(original, bounded_summary=original["bounded_summary"].replace(
            "review round 3:", "review round 1:", 1))
        store.save(document)
        entry = self.record(workflow_id)
        self.assertEqual(entry["receipts"][position]["digest"], original["digest"])
        self.assertEqual(bridge.review_report(entry), "PENDING")
        held = bridge.held_digests(entry)
        self.assertNotIn("review_round_3", held)
        self.assertEqual((held["review_round_1"], held["review_round_2"]),
                         (digests[1], digests[2]))
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["review_status"]["status"],
                          result["review_status"]["problem"],
                          result["review_status"]["unresolved_positions"]),
                         (bridge.REVIEW_TAMPERED,
                          receipts_module.PROBLEM_REVIEW_RECEIPT_TAMPERED, [position]))
        recorded = self.service.get_state(mission_id)["record"]["reconciliations"][-1]
        self.assertEqual(recorded["sources"]["review"]["value"], "PENDING")
        self.assertNotIn("review_round_3",
                         recorded["sources"]["candidate"]["value"]["artifact_digests"])
        # Honest recovery: the owner's collection re-records ONLY the missing
        # round 3, after the tampered receipt.
        before = len(entry["receipts"])
        self.broker._record_verification_observations(
            entry, self.broker._collect_evidence(entry))
        added = entry["receipts"][before:]
        self.assertEqual([r["bounded_summary"].split(":")[0] for r in added
                          if r["bounded_summary"].startswith("review round")],
                         ["review round 3"])
        self.assertEqual(bridge.review_report(entry), "REJECT")
        self.assertEqual({n: bridge.held_digests(entry)["review_round_%d" % n]
                          for n in (1, 2, 3)}, digests)
        status = bridge.review_status(entry)
        self.assertEqual((status["status"], status["tampered_positions"]),
                         (bridge.REVIEW_PROVEN, [position]))

    def test_R23_the_real_collector_s_delayed_read_is_proven_or_pending(self):
        # R17-1 end to end, the reviewer's exact production path: the real
        # collector records round 1 REJECT, round 3 REJECT and its listing
        # while round 2's read fails, then round 2 APPROVE when it succeeds.
        # Untampered: REJECT. Round 3 renumbered 3 -> 1 IN PLACE (digests
        # unchanged): PENDING, reported to the Mission, the ambiguity
        # unresolved. The owner's next collection re-records round 3: REJECT.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        rounds = [(1, "REJECT"), (2, "APPROVE"), (3, "REJECT")]
        digests = {n: self.write_round(workflow_id, n, decision)[1]
                   for n, decision in rounds}
        self.listing_with_rounds(rounds)
        entry = self.record(workflow_id)
        real_read = broker_module.evidence_module.read_state_artifact
        failing = {"round 2": True}

        def read(lease_path, subdirs, name):
            if failing["round 2"] and name.endswith("-round-02.md"):
                return ("unreadable", 0, None, None)
            return real_read(lease_path, subdirs, name)

        def collect():
            self.broker._record_verification_observations(
                entry, self.broker._collect_evidence(entry))
        with mock.patch.object(broker_module.evidence_module, "read_state_artifact", read):
            collect()
            failing["round 2"] = False
            collect()
        review = [(i, r["bounded_summary"].split(":")[0])
                  for i, r in enumerate(entry["receipts"])
                  if r["bounded_summary"].startswith("review ")]
        self.assertEqual([label for _i, label in review],
                         ["review round 1", "review round 3", "review listing",
                          "review round 2"])
        self.assertEqual(bridge.review_report(entry), "REJECT")
        self.assertEqual({n: bridge.held_digests(entry)["review_round_%d" % n]
                          for n in (1, 2, 3)}, digests)
        position = review[1][0]
        original = dict(entry["receipts"][position])
        entry["receipts"][position] = dict(original, bounded_summary=original[
            "bounded_summary"].replace("review round 3:", "review round 1:", 1))
        self.assertEqual(entry["receipts"][position]["digest"], original["digest"])
        self.assertEqual(bridge.review_report(entry), "PENDING")
        self.assertEqual(receipts_module.unresolved_review_rounds(entry), [position])
        result = bridge.reconcile_workflow(self.service, self.gate.context, entry,
                                           self.clock())
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["review_status"]["status"],
                          result["review_status"]["unresolved_positions"]),
                         (bridge.REVIEW_TAMPERED, [position]))
        recorded = self.service.get_state(mission_id)["record"]["reconciliations"][-1]
        self.assertEqual(recorded["sources"]["review"]["value"], "PENDING")
        # Honest recovery: the owner's collection re-records ONLY round 3.
        before = len(entry["receipts"])
        collect()
        self.assertEqual([r["bounded_summary"].split(":")[0]
                          for r in entry["receipts"][before:]
                          if r["bounded_summary"].startswith("review ")],
                         ["review round 3"])
        self.assertEqual(bridge.review_report(entry), "REJECT")
        self.assertEqual({n: bridge.held_digests(entry)["review_round_%d" % n]
                          for n in (1, 2, 3)}, digests)

    def test_R24_a_malformed_claim_head_never_raises_out_of_the_owner_pass(self):
        # R17 self-audit: a start receipt whose claim head carries a Unicode
        # digit used to raise ValueError out of the owner's recovery (outside
        # the containment's StoreError/RecordError catch). It is now no
        # claim head: the pass returns, nothing is invoked or closed.
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        store = wa_store.WorkflowStore(self.store_dir)
        document = store.load()
        record = document["workflows"][workflow_id]
        record["receipts"] = list(record["receipts"]) + [{
            "kind": wa_record.RECEIPT_KIND_EVIDENCE, "turn_id": "mclaim-tampered",
            "recorded_at": self.clock(),
            "digest": record[wa_record.MISSION_AUTHORITY_KEY]["authorization_digest_sha256"],
            "bounded_summary": "%s claim-runtime_start-²: point=runtime dispatch=1"
                               " state=%s" % (broker_module.MISSION_CLAIM_RECEIPT_MARKER,
                                              broker_module.START_STATE_CLAIMING)}]
        store.save(document)
        self.assertIn("claim-runtime_start-²", self.unresolved(workflow_id))
        starts = self.starts(mission_id)
        calls = (list(self.engine.starts), list(self.engine.tasks),
                 list(self.engine.close_calls))
        outcome = self.gated_broker().maintain(workflow_id, broker_module.MAINTAIN_RECOVERY)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        processed = runtime_module.process_once(self.gated_broker())
        self.assertIn(runtime_module.RECOVERY_LABEL,
                      [label for label, _o in processed[workflow_id]])
        self.assertEqual(self.starts(mission_id), starts)
        self.assertEqual((self.engine.starts, self.engine.tasks, self.engine.close_calls),
                         calls)


# ====================================================================
# G. The Grok MCP EDIT tool reports what it superseded
# ====================================================================


class CandidateRuleTests(unittest.TestCase):
    """R2-11-b, pinned without a fixture: the Broker's exactness rule IS
    the delivery layer's, and a candidate receipt is accepted only in its
    closed, internally consistent shape."""

    LINES = ("A  added.txt", "M  modified.txt", "D  deleted.txt", " M unstaged.txt",
             "MM staged-then-edited.txt", "AM added-then-edited.txt", "AD added-gone.txt",
             " D worktree-deleted.txt", "?? untracked.txt", "R  old.txt -> new.txt",
             "C  a.txt -> b.txt", "T  type.txt", "UU conflict.txt", "!! ignored.txt", "x")

    def test_exactness_is_the_delivery_layers_own_rule(self):
        from pr_delivery import machine
        for line in self.LINES:
            with self.subTest(line=line):
                self.assertEqual(bool(broker_module.porcelain_outside_candidate(line)),
                                 machine._porcelain_unstaged(line) is not None)
        text = "\n".join(self.LINES)
        self.assertEqual(broker_module.porcelain_outside_candidate(text)[0],
                         machine._porcelain_unstaged(text))
        self.assertEqual(broker_module.porcelain_outside_candidate(
            "A  a.txt\nM  b.txt\nD  c.txt\n"), [])

    def observation(self, status, digest=None, problem=None, entries=1):
        return {"status": status, "problem": problem, "detail": None,
                "entries": [None] * entries if entries else None, "digest": digest,
                "head": "b" * 40, "base": "c" * 40}

    def test_a_receipt_is_accepted_only_in_its_closed_consistent_shape(self):
        parse = broker_module.parse_candidate_receipt
        exact = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_EXACT, "a" * 64), 5)
        not_exact = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_NOT_EXACT, "a" * 64,
                             broker_module.PROBLEM_CANDIDATE_NOT_EXACT), 5)
        empty = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_UNAVAILABLE,
                             problem="pr_delivery_candidate_empty", entries=0), 5)
        for receipt in (exact, not_exact, empty):
            self.assertTrue(parse(receipt)["consistent"], receipt)
        self.assertIsNone(parse(empty)["identity"])
        self.assertEqual(parse(exact)["identity"], "a" * 64)
        self.assertIsNone(parse({"bounded_summary": "review round 1: decision=APPROVE"}))

        def summary(receipt, old, new):
            self.assertIn(old, receipt["bounded_summary"])
            return dict(receipt, bounded_summary=receipt["bounded_summary"].replace(old, new))
        tampered = {
            "digest altered": dict(exact, digest="f" * 64),
            "summary identity altered": summary(exact, "a" * 64, "f" * 64),
            "unknown status": summary(exact, "status=exact", "status=bogus"),
            "short base": summary(exact, "c" * 40, "c" * 39),
            "exact with a problem": summary(exact, "problem=None", "problem=x"),
            "not exact without its problem": summary(
                not_exact, "problem=" + broker_module.PROBLEM_CANDIDATE_NOT_EXACT,
                "problem=None"),
            "unavailable under another problem": summary(
                empty, "problem=pr_delivery_candidate_empty",
                "problem=pr_delivery_candidate_too_many"),
            "identity with no entries": summary(exact, "entries=1", "entries=None"),
        }
        for label, receipt in tampered.items():
            with self.subTest(case=label):
                self.assertFalse(parse(receipt)["consistent"])

    def test_R15_4_a_valid_shaped_candidate_field_alteration_is_refused(self):
        # R15-4: every semantic field of a candidate receipt is bound to the
        # receipt digest. Each alteration below leaves EVERY field in its
        # closed, valid shape and the P1-A6 identity unchanged — and the
        # receipt is still refused. The honest receipt is accepted.
        parse = broker_module.parse_candidate_receipt
        exact = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_EXACT, "a" * 64, entries=2), 5)
        honest = parse(exact)
        self.assertTrue(honest["consistent"])
        self.assertEqual((honest["status"], honest["base"], honest["head"],
                          honest["entries"], honest["identity"], honest["problem"]),
                         (broker_module.CANDIDATE_STATUS_EXACT, "c" * 40, "b" * 40, 2,
                          "a" * 64, None))
        self.assertEqual(exact["digest"], receipts_module.candidate_binding(
            broker_module.CANDIDATE_STATUS_EXACT, "c" * 40, "b" * 40, 2, "a" * 64, None))
        # The binding is receipt content, not a second identity: the P1-A6
        # identity is what the parse reports, never the receipt digest.
        self.assertNotEqual(exact["digest"], honest["identity"])

        def summary(old, new):
            self.assertIn(old, exact["bounded_summary"])
            return dict(exact, bounded_summary=exact["bounded_summary"].replace(old, new))
        alterations = {
            "base": summary("base=" + "c" * 40, "base=" + "d" * 40),
            "HEAD": summary("head=" + "b" * 40, "head=" + "e" * 40),
            "HEAD removed": summary("head=" + "b" * 40, "head=None"),
            "entry count": summary("entries=2", "entries=3"),
            "entry count lowered": summary("entries=2", "entries=1"),
            "status to not exact": dict(
                exact, bounded_summary=exact["bounded_summary"]
                .replace("status=exact", "status=not_exact")
                .replace("problem=None", "problem=" + broker_module.PROBLEM_CANDIDATE_NOT_EXACT)),
        }
        for label, receipt in alterations.items():
            with self.subTest(case=label):
                parsed = parse(receipt)
                self.assertNotEqual(receipt["bounded_summary"], exact["bounded_summary"])
                self.assertEqual(parsed["receipt_digest"], exact["digest"])
                self.assertFalse(parsed["consistent"], parsed)
                entry = {"receipts": [exact, receipt]}
                self.assertFalse(broker_module.observed_candidate(entry)["consistent"])
                self.assertFalse(broker_module.same_candidate_observation(
                    broker_module.observed_candidate(entry), exact))
        # The latest tampered receipt is refused, never skipped for the
        # older honest one: nothing is reported as the candidate.
        entry = {"receipts": [exact, alterations["base"]], "phase": "VERIFIED"}
        self.assertNotIn("candidate", bridge.held_digests(entry))
        self.assertNotIn("observed_head", bridge.held_digests(entry))
        self.assertEqual(bridge.candidate_status(entry)["status"], bridge.CANDIDATE_TAMPERED)
        # The honest record reports the P1-A6 identity and the HEAD.
        held = bridge.held_digests({"receipts": [exact]})
        self.assertEqual(held["candidate"], "a" * 64)
        self.assertEqual(held["observed_head"], broker_module.head_commit_digest("b" * 40))

    def test_R15_4_a_valid_shaped_review_round_alteration_is_refused(self):
        # R15-4: a review round receipt binds round, decision, file and the
        # round record's content digest, and the collector's listing receipt
        # binds what it listed. Any valid-shaped alteration of either is
        # refused; the altered round is not reported and — the listed
        # highest round no longer being backed — the standing is PENDING.
        content1, content2 = "1" * 64, "2" * 64
        first = broker_module.review_round_receipt(1, "APPROVE", "t-round-01.md", content1, 5)
        second = broker_module.review_round_receipt(2, "REJECT", "t-round-02.md", content2, 6)
        listing = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (2, "REJECT")], content2, True, 6)
        parse = receipts_module.parse_review_round_receipt
        for receipt, expected in ((first, (1, "APPROVE", "t-round-01.md", content1)),
                                  (second, (2, "REJECT", "t-round-02.md", content2))):
            parsed = parse(receipt)
            self.assertTrue(parsed["consistent"], parsed)
            self.assertEqual((parsed["round"], parsed["decision"], parsed["file"],
                              parsed["content"]), expected)
            self.assertEqual(receipt["digest"],
                             receipts_module.review_round_binding(*expected))
        stated = receipts_module.parse_review_listing_receipt(listing)
        self.assertTrue(stated["consistent"], stated)
        self.assertEqual((stated["complete"], stated["listed"], stated["latest"],
                          stated["decision"], stated["content"]),
                         (True, [(1, "APPROVE"), (2, "REJECT")], 2, "REJECT", content2))
        honest = {"receipts": [first, second, listing], "phase": "DISPATCHED"}
        self.assertEqual(bridge.review_report(honest), "REJECT")
        self.assertEqual(bridge.held_digests(honest)["review_round_2"], content2)
        # Round receipts alone prove nothing (R17-1).
        self.assertEqual(bridge.review_report(
            {"receipts": [first, second], "phase": "DISPATCHED"}), "PENDING")

        def summary(receipt, old, new):
            self.assertIn(old, receipt["bounded_summary"])
            return dict(receipt, bounded_summary=receipt["bounded_summary"].replace(old, new))
        alterations = {
            "decision REJECT to APPROVE": summary(second, "decision=REJECT",
                                                  "decision=APPROVE"),
            "decision removed": summary(second, "decision=REJECT", "decision=None"),
            "content": summary(second, "content=" + content2, "content=" + "3" * 64),
            "file": summary(second, "file=t-round-02.md", "file=t-round-03.md"),
            "round renumbered": summary(second, "review round 2:", "review round 3:"),
            "digest": dict(second, digest="f" * 64),
        }
        for label, receipt in alterations.items():
            with self.subTest(case=label):
                self.assertFalse(parse(receipt)["consistent"])
                # The round-2 receipt edited IN PLACE (position 1).
                entry = {"receipts": [first, receipt, listing], "phase": "DISPATCHED"}
                self.assertEqual(receipts_module.tampered_review_rounds(entry), [1])
                self.assertEqual(receipts_module.unresolved_review_rounds(entry), [1])
                self.assertEqual([r[0] for r in broker_module.observed_review_rounds(entry)],
                                 [1])
                self.assertEqual(bridge.review_report(entry), "PENDING")
                held = bridge.held_digests(entry)
                self.assertNotIn("review_round_2", held)
                self.assertNotIn("review_round_3", held)
                self.assertEqual(held["review_round_1"], content1)
                # The same altered receipt APPENDED beside the honest ones:
                # it displaces nothing (round 2 keeps its own digest) and the
                # proof — the listing backed by the honest round 2 — stands.
                appended = {"receipts": [first, second, listing, receipt],
                            "phase": "DISPATCHED"}
                self.assertEqual(bridge.review_report(appended), "REJECT")
                self.assertEqual(bridge.held_digests(appended)["review_round_2"], content2)
                self.assertEqual(receipts_module.tampered_review_rounds(appended), [3])
                self.assertEqual(receipts_module.unresolved_review_rounds(appended), [])
        # The listing receipt's own fields, altered in valid shape: refused,
        # never a standing.
        listing_alterations = {
            "latest decision REJECT to APPROVE": dict(
                listing, bounded_summary=listing["bounded_summary"]
                .replace("decision=REJECT", "decision=APPROVE")
                .replace("2:REJECT", "2:APPROVE")),
            "incomplete to complete": summary(listing, "complete=True", "complete=False"),
            "content": summary(listing, "content=" + content2, "content=" + "3" * 64),
            "a round dropped": dict(listing, bounded_summary=listing["bounded_summary"]
                                    .replace("latest=2", "latest=1")
                                    .replace("decision=REJECT", "decision=APPROVE")
                                    .replace(",2:REJECT", "")),
            "digest": dict(listing, digest="f" * 64),
        }
        for label, receipt in listing_alterations.items():
            with self.subTest(listing=label):
                self.assertNotEqual(receipt, listing)
                self.assertFalse(receipts_module.parse_review_listing_receipt(
                    receipt)["consistent"])
                entry = {"receipts": [first, second, receipt], "phase": "DISPATCHED"}
                self.assertEqual(receipts_module.tampered_review_rounds(entry), [2])
                self.assertEqual(bridge.review_report(entry), "PENDING")
                self.assertEqual(bridge.review_status(entry)["status"],
                                 bridge.REVIEW_TAMPERED)
        # A listed decision outside the closed tokens is recorded as none:
        # consistent, standing PENDING, as before the binding.
        odd = broker_module.review_round_receipt(1, "MAYBE later", "t-round-01.md",
                                                 content1, 7)
        self.assertTrue(parse(odd)["consistent"])
        self.assertIsNone(parse(odd)["decision"])
        odd_listing = broker_module.review_listing_receipt(
            [(1, "MAYBE later")], content1, True, 7)
        self.assertEqual(bridge.review_report(
            {"receipts": [odd, odd_listing], "phase": "DISPATCHED"}), "PENDING")
        self.assertEqual(bridge.review_status(
            {"receipts": [odd, odd_listing], "phase": "DISPATCHED"})["status"],
            bridge.REVIEW_PROVEN)

    def test_R16_1_a_tampered_round_number_never_restores_an_older_decision(self):
        # R16-1: rounds 1 REJECT, 2 APPROVE, 3 REJECT and the collector's
        # listing of them. Whatever number a tampered receipt STATES —
        # lower, higher, colliding, malformed — it contributes nothing
        # trusted and the standing is PENDING until it is PROVEN again: the
        # listed highest round backed by a trusted receipt (R17-1).
        contents = {1: "1" * 64, 2: "2" * 64, 3: "3" * 64}
        decisions = {1: "REJECT", 2: "APPROVE", 3: "REJECT"}

        def honest(n, at):
            return broker_module.review_round_receipt(
                n, decisions[n], "t-round-%02d.md" % n, contents[n], at)
        listing = broker_module.review_listing_receipt(
            [(n, decisions[n]) for n in (1, 2, 3)], contents[3], True, 7)
        baseline = [honest(1, 5), honest(2, 6), honest(3, 7), listing]

        def entry(receipts):
            return {"receipts": [dict(r) for r in receipts], "phase": "DISPATCHED"}

        def restated(receipt, head):
            summary = receipt["bounded_summary"]
            altered = head + summary[summary.index(":"):]
            self.assertNotEqual(altered, summary)
            return dict(receipt, bounded_summary=altered)
        # (e) The honest baseline: REJECT, all three round digests reported.
        honest_entry = entry(baseline)
        self.assertEqual(bridge.review_report(honest_entry), "REJECT")
        held = bridge.held_digests(honest_entry)
        self.assertEqual({k: held[k] for k in held if k.startswith("review_round_")},
                         {"review_round_%d" % n: contents[n] for n in (1, 2, 3)})
        self.assertEqual(bridge.review_status(honest_entry)["status"], bridge.REVIEW_PROVEN)
        self.assertEqual(receipts_module.tampered_review_rounds(honest_entry), [])
        # (a) downward 3 -> 1, (b) upward 3 -> 4, (c) colliding 3 -> 2, and a
        # malformed number: the LATEST receipt edited in place, its digest
        # unchanged.
        cases = {"downward 3->1": "review round 1", "upward 3->4": "review round 4",
                 "colliding 3->2": "review round 2", "malformed": "review round x",
                 "superscript": "review round ³"}
        for label, head in cases.items():
            with self.subTest(case=label):
                tampered = restated(baseline[2], head)
                self.assertEqual(tampered["digest"], baseline[2]["digest"])
                parsed = receipts_module.parse_review_round_receipt(tampered)
                self.assertIsNotNone(parsed)
                self.assertFalse(parsed["consistent"])
                case = entry([baseline[0], baseline[1], tampered, listing])
                self.assertEqual(bridge.review_report(case), "PENDING")
                # Named, by position (never its stated number) …
                self.assertEqual(receipts_module.tampered_review_rounds(case), [2])
                self.assertEqual(receipts_module.unresolved_review_rounds(case), [2])
                status = bridge.review_status(case)
                self.assertEqual((status["status"], status["problem"],
                                  status["unresolved_positions"]),
                                 (bridge.REVIEW_TAMPERED,
                                  receipts_module.PROBLEM_REVIEW_RECEIPT_TAMPERED, [2]))
                # … round 3 is not reported and the tampered receipt displaces
                # no honest round (1 and 2 keep their own digests).
                held = bridge.held_digests(case)
                self.assertNotIn("review_round_3", held)
                self.assertNotIn("review_round_4", held)
                self.assertEqual(held["review_round_1"], contents[1])
                self.assertEqual(held["review_round_2"], contents[2])
                self.assertEqual([r[0] for r in bridge.review_rounds(case)], [1, 2])
                self.assertEqual(bridge.materialize_reports(case, 9)["review"]["value"],
                                 "PENDING")
                # A later trusted receipt of an OLDER or EQUAL round proves
                # nothing, and neither does a bare HIGHER round recorded
                # later without a listing naming it (R17-1).
                for n in (1, 2):
                    repeat = broker_module.review_round_receipt(
                        n, decisions[n], "t-round-%02d.md" % n, "9" * 64, 8)
                    later = entry([baseline[0], baseline[1], tampered, listing, repeat])
                    self.assertEqual(bridge.review_report(later), "PENDING", n)
                bare = broker_module.review_round_receipt(4, "APPROVE", "t-round-04.md",
                                                          "4" * 64, 8)
                later = entry([baseline[0], baseline[1], tampered, listing, bare])
                self.assertEqual(bridge.review_report(later), "PENDING")
                self.assertEqual(receipts_module.unresolved_review_rounds(later), [2])
                # (d) Honest recovery: the owner re-records round 3 — its
                # receipt backs the listed highest round again; the standing
                # returns to round 3's decision and its digest is reported;
                # the tampering stays on record, resolved by the proof.
                recovered = entry([baseline[0], baseline[1], tampered, listing, honest(3, 9)])
                self.assertEqual(receipts_module.unresolved_review_rounds(recovered), [])
                self.assertEqual(bridge.review_report(recovered), "REJECT")
                self.assertEqual(bridge.held_digests(recovered)["review_round_3"],
                                 contents[3])
                status = bridge.review_status(recovered)
                self.assertEqual((status["status"], status["tampered_positions"]),
                                 (bridge.REVIEW_PROVEN, [2]))
        # An OLDER receipt tampered in place (round 1 restated as 5): the
        # listing names round 3 as the highest and round 3's receipt backs it,
        # so the standing is proven — round 3 decides, round 1 is not
        # reported, the tampering is named and resolved by the proof.
        older = entry([restated(baseline[0], "review round 5")] + baseline[1:])
        self.assertEqual(receipts_module.unresolved_review_rounds(older), [])
        self.assertEqual(receipts_module.tampered_review_rounds(older), [0])
        self.assertEqual(bridge.review_report(older), "REJECT")
        self.assertNotIn("review_round_1", bridge.held_digests(older))
        self.assertNotIn("review_round_5", bridge.held_digests(older))
        # The ONLY review receipt tampered: PENDING whatever the phase.
        alone = {"receipts": [restated(baseline[2], "review round 1")], "phase": "VERIFIED"}
        self.assertEqual(bridge.review_report(alone), "PENDING")

    def test_R17_1_a_delayed_read_never_lets_an_older_round_stand(self):
        # R17-1, the reviewer's exact sequence: round 2's read fails in the
        # first pass, so the collector records round 1 REJECT, round 3
        # REJECT and its listing (1 REJECT, 2 APPROVE, 3 REJECT — round 3
        # read), then round 2 APPROVE in a later pass (the listing unchanged,
        # so not re-recorded). Renumbering round 3 IN PLACE, every digest
        # unchanged, leaves the standing PENDING — never round 2's APPROVE.
        contents = {1: "1" * 64, 2: "2" * 64, 3: "3" * 64}
        decisions = {1: "REJECT", 2: "APPROVE", 3: "REJECT"}

        def honest(n, at):
            return broker_module.review_round_receipt(
                n, decisions[n], "t-round-%02d.md" % n, contents[n], at)
        listing = broker_module.review_listing_receipt(
            [(n, decisions[n]) for n in (1, 2, 3)], contents[3], True, 6)
        sequence = [honest(1, 5), honest(3, 6), listing, honest(2, 9)]
        # Untampered: round 3 decides, all three digests reported.
        untampered = {"receipts": [dict(r) for r in sequence], "phase": "DISPATCHED"}
        self.assertEqual(bridge.review_report(untampered), "REJECT")
        self.assertEqual({n: bridge.held_digests(untampered)["review_round_%d" % n]
                          for n in (1, 2, 3)}, contents)
        for label, head in (("3->1", "review round 1"), ("3->2", "review round 2"),
                            ("3->4", "review round 4")):
            with self.subTest(renumbered=label):
                tampered = [dict(r) for r in sequence]
                summary = tampered[1]["bounded_summary"]
                tampered[1]["bounded_summary"] = head + summary[summary.index(":"):]
                self.assertEqual(tampered[1]["digest"], sequence[1]["digest"])
                case = {"receipts": tampered, "phase": "DISPATCHED"}
                self.assertEqual(bridge.review_report(case), "PENDING")
                self.assertEqual(receipts_module.unresolved_review_rounds(case), [1])
                status = bridge.review_status(case)
                self.assertEqual((status["status"], status["unresolved_positions"]),
                                 (bridge.REVIEW_TAMPERED, [1]))
                self.assertIn("round 3 as listed is not backed", status["gap"])
                self.assertNotIn("review_round_3", bridge.held_digests(case))
                self.assertEqual(bridge.materialize_reports(case, 10)["review"]["value"],
                                 "PENDING")
                # Honest recovery: the owner's next pass re-records round 3.
                recovered = {"receipts": tampered + [honest(3, 11)], "phase": "DISPATCHED"}
                self.assertEqual(bridge.review_report(recovered), "REJECT")
                self.assertEqual(receipts_module.unresolved_review_rounds(recovered), [])
                self.assertEqual(bridge.held_digests(recovered)["review_round_3"],
                                 contents[3])
        # The untampered proof is round 3's own receipt (position 1) backing
        # the listing (position 2) — not round 2's later position.
        reading = receipts_module.review_round_reading(untampered)
        self.assertEqual((reading["proof"]["round"], reading["proof"]["backing_position"],
                          reading["proof"]["listing_position"]), (3, 1, 2))
        # An unreadable highest round never lets an older decision stand
        # either (no tampering at all): round 3 listed but not read.
        unread = broker_module.review_listing_receipt(
            [(n, decisions[n]) for n in (1, 2, 3)], None, True, 6)
        case = {"receipts": [honest(1, 5), honest(2, 6), unread], "phase": "VERIFIED"}
        self.assertEqual(bridge.review_report(case), "PENDING")
        status = bridge.review_status(case)
        self.assertEqual((status["status"], status["problem"]),
                         (bridge.REVIEW_UNPROVEN, receipts_module.PROBLEM_REVIEW_UNPROVEN))
        self.assertIn("round 3 was not read", status["gap"])

    def test_R17_2_a_malformed_number_is_tampering_never_an_exception(self):
        # R17-2: a number that is not a canonical ASCII decimal — Unicode
        # digits, empty, oversized, signed, padded, spaced, fractional — is
        # rejected as tampering, never raised; the bridge reports PENDING.
        honest = broker_module.review_round_receipt(3, "REJECT", "t-round-03.md",
                                                    "3" * 64, 5)
        listing = broker_module.review_listing_receipt([(3, "REJECT")], "3" * 64, True, 5)
        self.assertEqual(bridge.review_report(
            {"receipts": [honest, listing], "phase": "DISPATCHED"}), "REJECT")
        malformed = ("³", "¹", "٣", "３", "", "0", "03", "-3", "+3",
                     " 3", "3 ", "3.0", "1234567", "三", "3​")
        for number in malformed:
            with self.subTest(round=repr(number)):
                summary = honest["bounded_summary"].replace(
                    "review round 3:", "review round %s:" % number)
                receipt = dict(honest, bounded_summary=summary)
                parsed = receipts_module.parse_review_round_receipt(receipt)
                self.assertIsNotNone(parsed)
                self.assertFalse(parsed["consistent"])
                self.assertIsNone(parsed["round"])
                entry = {"receipts": [receipt, listing], "phase": "DISPATCHED"}
                self.assertEqual(bridge.review_report(entry), "PENDING")
                self.assertEqual(bridge.review_status(entry)["status"],
                                 bridge.REVIEW_TAMPERED)
                self.assertEqual(bridge.materialize_reports(entry, 9)["review"]["value"],
                                 "PENDING")
            with self.subTest(listed=repr(number)):
                altered = dict(listing, bounded_summary=listing["bounded_summary"]
                               .replace("listed=3:", "listed=%s:" % number))
                self.assertFalse(receipts_module.parse_review_listing_receipt(
                    altered)["consistent"])
                self.assertEqual(bridge.review_report(
                    {"receipts": [honest, altered], "phase": "DISPATCHED"}), "PENDING")
            with self.subTest(entries=repr(number)):
                exact = broker_module.candidate_receipt(
                    self.observation(broker_module.CANDIDATE_STATUS_EXACT, "a" * 64,
                                     entries=3), 5)
                altered = dict(exact, bounded_summary=exact["bounded_summary"].replace(
                    "entries=3", "entries=%s" % number))
                parsed = broker_module.parse_candidate_receipt(altered)
                self.assertFalse(parsed["consistent"])
                entry = {"receipts": [exact, altered], "phase": "VERIFIED"}
                self.assertNotIn("candidate", bridge.held_digests(entry))
                self.assertEqual(bridge.candidate_status(entry)["status"],
                                 bridge.CANDIDATE_TAMPERED)
        # The same shape in the claim-head parser (the owner's pass reads it
        # from a start receipt): refused, never raised, never re-read as
        # another number.
        self.assertEqual(broker_module.parse_claim_head(
            broker_module.claim_head("runtime_start", 12)), ("runtime_start", 12))
        for number in malformed:
            with self.subTest(claim=repr(number)):
                self.assertIsNone(broker_module.parse_claim_head(
                    "claim-runtime_start-%s" % number))

    def test_R17_audit_every_reading_path_refuses_what_it_cannot_prove(self):
        # R17 self-audit regressions.
        # (a) Canonical form: an alteration that keeps every field's VALUE
        # (padding, a duplicated or extra token, reordering, spacing) is
        # still refused — the summary must be the canonical rendering.
        rnd = broker_module.review_round_receipt(3, "REJECT", "t-round-03.md", "3" * 64, 5)
        lst = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (3, "REJECT")], "3" * 64, True, 5)
        cand = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_EXACT, "a" * 64, entries=2), 5)
        for label, receipt, parse in (
                ("round", rnd, receipts_module.parse_review_round_receipt),
                ("listing", lst, receipts_module.parse_review_listing_receipt),
                ("candidate", cand, broker_module.parse_candidate_receipt)):
            summary = receipt["bounded_summary"]
            self.assertTrue(parse(receipt)["consistent"], label)
            head, _, tail = summary.partition(": ")
            tokens = tail.split(" ")
            variants = {
                "duplicated token": summary + " " + tokens[0],
                "extra token": summary + " note=x",
                "reordered": head + ": " + " ".join(reversed(tokens)),
                "double space": summary.replace(" ", "  ", 1),
                "trailing space": summary + " ",
            }
            for name, altered in variants.items():
                with self.subTest(family=label, variant=name):
                    self.assertNotEqual(altered, summary)
                    self.assertFalse(parse(dict(receipt, bounded_summary=altered))[
                        "consistent"])
        # (b) Recognition does not hang on the marker alone: a receipt with
        # the writer's turn id whose marker was edited is a TAMPERED member
        # of its family — the latest listing / candidate is refused, never
        # skipped in favour of an older one.
        old_listing = broker_module.review_listing_receipt([(1, "APPROVE")], "1" * 64, True, 4)
        one = broker_module.review_round_receipt(1, "APPROVE", "t-round-01.md", "1" * 64, 4)
        new_listing = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (2, "REJECT")], "2" * 64, True, 6)
        two = broker_module.review_round_receipt(2, "REJECT", "t-round-02.md", "2" * 64, 6)
        base = [one, old_listing, two, new_listing]
        self.assertEqual(bridge.review_report({"receipts": base, "phase": "VERIFIED"}),
                         "REJECT")
        hidden = dict(new_listing, bounded_summary=new_listing["bounded_summary"].replace(
            "review listing:", "review listinG:"))
        entry = {"receipts": [one, old_listing, two, hidden], "phase": "VERIFIED"}
        self.assertEqual(receipts_module.review_round_reading(entry)["listing"]["position"], 3)
        self.assertEqual(bridge.review_report(entry), "PENDING")
        self.assertEqual(receipts_module.tampered_review_rounds(entry), [3])
        hidden_round = dict(two, bounded_summary=two["bounded_summary"].replace(
            "review round", "review_round"))
        entry = {"receipts": [one, old_listing, hidden_round, new_listing],
                 "phase": "VERIFIED"}
        self.assertEqual(receipts_module.tampered_review_rounds(entry), [2])
        self.assertEqual(bridge.review_report(entry), "PENDING")
        older = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_EXACT, "d" * 64), 4)
        latest = broker_module.candidate_receipt(
            self.observation(broker_module.CANDIDATE_STATUS_NOT_EXACT, "a" * 64,
                             broker_module.PROBLEM_CANDIDATE_NOT_EXACT), 6)
        masked = dict(latest, bounded_summary=latest["bounded_summary"].replace(
            "candidate identity:", "candidate identitY:"))
        entry = {"receipts": [older, masked], "phase": "VERIFIED"}
        self.assertFalse(broker_module.observed_candidate(entry)["consistent"])
        self.assertNotIn("candidate", bridge.held_digests(entry))
        # (c) A listing that is not complete, or lists no round, proves
        # nothing; with no review observation at all the old default holds.
        incomplete = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (2, "REJECT")], "2" * 64, False, 6)
        entry = {"receipts": [one, two, incomplete], "phase": "VERIFIED"}
        self.assertEqual(bridge.review_report(entry), "PENDING")
        self.assertIn("incomplete", bridge.review_status(entry)["gap"])
        empty = broker_module.review_listing_receipt([], None, True, 6)
        self.assertEqual(bridge.review_report({"receipts": [empty], "phase": "VERIFIED"}),
                         "NONE")
        self.assertEqual(bridge.review_report({"receipts": [empty], "phase": "DISPATCHED"}),
                         "PENDING")
        self.assertEqual(bridge.review_status({"receipts": [], "phase": "VERIFIED"})["status"],
                         bridge.REVIEW_UNOBSERVED)
        # (d) The collector's completeness rule over the observer's shapes.
        statement = receipts_module.listing_statement

        def section(listed, rounds, state="available"):
            return {"state": state, "rounds": rounds, "total_files": len(listed),
                    "truncated": False,
                    "listed": [{"round": n, "decision": d} for n, d in listed]}
        self.assertEqual(statement(section([(1, "APPROVE"), (2, "REJECT")], 2), []),
                         ([(1, "APPROVE"), (2, "REJECT")], True))
        # truncation keeps the most recent rounds: the highest is listed.
        self.assertEqual(statement(dict(section([(40, "APPROVE")], 40), truncated=True),
                                   [])[1], True)
        incomplete_cases = {
            "highest not listed": (section([(1, "APPROVE")], 2), []),
            "scan capped": (section([(1, "APPROVE")], 1),
                            [{"source": "reviews", "state": "unavailable", "detail": "x"}]),
            "duplicate round": (section([(1, "APPROVE"), (1, "REJECT")], 1), []),
            "bool round": ({"state": "available", "rounds": 1, "listed": [
                {"round": True, "decision": "APPROVE"}]}, []),
            "oversized round": (section([(1000000, "APPROVE")], 1000000), []),
            "unreadable section": (section([], None, state="unreadable"), []),
            "listing not a list": ({"state": "available", "rounds": 0, "listed": None}, []),
            "no section": (None, []),
        }
        for label, (reviews, diagnostics) in incomplete_cases.items():
            with self.subTest(listing=label):
                self.assertFalse(statement(reviews, diagnostics)[1])
        self.assertEqual(statement(section([], 0, state="empty"), []), ([], True))
        self.assertEqual(statement(section([], None, state="missing"), []), ([], True))
        # A non-closed decision is stated as none.
        self.assertEqual(statement(section([(1, "LGTM")], 1), [])[0], [(1, None)])
        # (e) The proof never regresses: a newest listing that lost the
        # highest round, or an older listing left latest because the newer
        # one is gone while round 3's receipt remains, proves nothing.
        three = broker_module.review_round_receipt(3, "REJECT", "t-round-03.md", "3" * 64, 7)
        full = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (2, "REJECT"), (3, "REJECT")], "3" * 64, True, 7)
        self.assertEqual(bridge.review_report(
            {"receipts": [one, two, three, full], "phase": "VERIFIED"}), "REJECT")
        shrunk = broker_module.review_listing_receipt(
            [(1, "APPROVE"), (2, "APPROVE")], "2" * 64, True, 9)
        two_approve = broker_module.review_round_receipt(2, "APPROVE", "t-round-02.md",
                                                         "2" * 64, 9)
        entry = {"receipts": [one, two, three, full, two_approve, shrunk], "phase": "VERIFIED"}
        self.assertEqual(bridge.review_report(entry), "PENDING")
        self.assertIn("older than round 3", bridge.review_status(entry)["gap"])
        entry = {"receipts": [one, old_listing, two, new_listing, three], "phase": "VERIFIED"}
        self.assertEqual(bridge.review_report(entry), "PENDING")
        # (f) The collector states at most the most recent rounds, the
        # highest always among them, and its receipt always fits.
        many = section([(n, "APPROVE") for n in range(1, 71)], 70)
        pairs, complete = statement(many, [])
        self.assertEqual((len(pairs), pairs[0][0], pairs[-1][0], complete),
                         (receipts_module.MAX_LISTED_ROUNDS, 7, 70, True))
        wide = broker_module.review_listing_receipt(
            [(n, "APPROVE") for n in range(999936, 1000000)], "9" * 64, True, 9)
        self.assertLessEqual(len(wide["bounded_summary"]),
                             wa_record.MAX_BOUNDED_SUMMARY_CHARS)
        self.assertTrue(receipts_module.parse_review_listing_receipt(wide)["consistent"])
        # (g) A HEAD that is not a 40-hex commit id is recorded as unknown, so
        # the honest receipt is canonical and a repeat is recognised (no new
        # receipt every pass).
        class Transport(object):
            def head_commit(self, path):
                return "f" * 64 + "\n"

            def status_porcelain_readonly(self, path):
                return {"status": "error:Timeout"}
        observation = broker_module.capture_candidate(Transport(), "/nowhere", "c" * 40)
        self.assertIsNone(observation["head"])
        first = broker_module.candidate_receipt(observation, 5)
        self.assertTrue(broker_module.parse_candidate_receipt(first)["consistent"])
        again = broker_module.candidate_receipt(
            broker_module.capture_candidate(Transport(), "/nowhere", "c" * 40), 6)
        self.assertTrue(broker_module.same_candidate_observation(
            broker_module.parse_candidate_receipt(first), again))


class DriftRuleTests(unittest.TestCase):
    """R2-11-a (g): the Task 7 drift behaviour for a caller WITHOUT a
    revision is unchanged by the S-V rule — the first baseline ever
    observed anchors, reported receipt digests authorize nothing and the
    observed head is not compared; with a revision the same reports are
    judged by the attested-transition rule."""

    @staticmethod
    def record(revision, baseline, digests):
        from mission import reconciliation as rc
        return {"observed_revision": revision, "findings": [],
                "sources": {rc.SOURCE_CANDIDATE: {
                    "standing": rc.STANDING_REPORTED,
                    "value": {"baseline_digest_sha256": baseline,
                              "artifact_digests": dict(digests)}}}}

    def test_without_a_revision_receipts_and_heads_change_nothing(self):
        from mission import reconciliation as rc
        as_of = {"artifacts": [], "evidence": []}
        first = self.record(1, "a" * 64, {"observed_head": "1" * 64})
        current = {"baseline_digest_sha256": "b" * 64,
                   "artifact_digests": {"observed_head": "2" * 64,
                                        "baseline_receipt": "9" * 64,
                                        "head_receipt": "9" * 64}}
        task7 = rc.drift_findings(as_of, [first], current, True, revision=None)
        self.assertEqual([(f["kind"], f["subject"]) for f in task7],
                         [(rc.FINDING_BASELINE_DRIFT, None)])
        self.assertIn("a" * 64, task7[0]["detail"])
        # The same reports under a revision: nothing is attested, so the
        # reported receipts authorize nothing — baseline AND head drift.
        scoped = rc.drift_findings(as_of, [first], current, True, revision=1)
        self.assertEqual(sorted((f["kind"], f["subject"] or "") for f in scoped),
                         [(rc.FINDING_BASELINE_DRIFT, ""),
                          (rc.FINDING_CANDIDATE_DRIFT, "observed_head")])
        # A report whose revision differs is not compared at all.
        self.assertEqual(rc.drift_findings(as_of, [first], current, True, revision=2), [])


class GEditToolTests(MissionFixture):

    def test_G1_edit_reports_the_derived_supersession(self):
        client, controller, operator = self.wired()
        self.assertEqual(client.initialize()[0], 200)
        proposed = self.structured(client, "di_mission_propose",
                                   mission_proposal_arguments())
        self.assertTrue(proposed["ok"], proposed)
        mission_id = proposed["mission_id"]
        edited = self.structured(client, "di_mission_edit", dict(
            mission_proposal_arguments(objective="Narrower objective"),
            mission_id=mission_id, expected_revision=1))
        self.assertTrue(edited["ok"], edited)
        self.assertEqual(edited["superseded"], {
            "revision": 2, "activation_id": None, "checkpoints": 0,
            "engagements": [], "starts_stop_requested": 0, "recorded": False})
        self.assertEqual(edited["invalidated_authorization_ids"], [])


if __name__ == "__main__":
    unittest.main()
