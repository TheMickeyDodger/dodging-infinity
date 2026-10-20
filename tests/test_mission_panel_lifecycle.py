"""Tests for the panel's lifecycle derivation (``mission_panel.lifecycle``).

The derivation turns the DURABLE records (the surface's status, the
replacement lineage, the clock) into a stage, plain-language words and the
only actions a window may offer. It is the one place the honest wording lives,
so it is where the honesty is pinned:

- a withdrawal is called a WITHDRAWAL, never a denial, and a replacement is a
  separate record, never an edit;
- an operator-attested approval is shown as attested and not independently
  verified;
- a recorded start is durable INTENT, never an exactly-once guarantee, and an
  unknown start is a HOLD that is never retried;
- an expired approval is expired, not quietly re-offered.

Stages before a run use the REAL surface over the REAL Mission Core. The run
stages overlay the run section of a real status with the phase under test
(``run_status`` itself is exercised by the bridge tests); that overlay is
labelled SYNTHETIC where it is used.
"""

import copy
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import surface as surface_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission_panel import lifecycle  # noqa: E402
from test_local_request import Fixture, request  # noqa: E402

NO_LINEAGE = {"supersedes": None, "superseded_by": None, "turn_id": None}


class Derive(Fixture):

    def derive(self, status, lineage=None, capability_held=True, run_available=True,
               runnable_problem=None, verification_ready=None, now=None,
               progression=None):
        return lifecycle.derive(
            status, lineage or NO_LINEAGE, self.clock() if now is None else now,
            capability_held=capability_held, run_available=run_available,
            runnable_problem=runnable_problem, verification_ready=verification_ready,
            progression=progression)

    def pending(self, **overrides):
        return self.surface.submit(request(**overrides))

    def approve(self, out, expires_in=600):
        presented = self.surface.present(out["request_ref"])
        return self.surface.attest_approval(
            out["request_ref"], presented["mission_id"], presented["revision"],
            presented["proposal_digest_sha256"], presented["approved_action_scope"],
            presented["approved_delivery_targets"], self.clock() + expires_in,
            "approve", "di-world-panel")

    def overlay_run(self, status, **run):
        """SYNTHETIC: a real approved status with the run section under test."""
        status = copy.deepcopy(status)
        status["run"].update(run)
        return status


class PreRunStages(Derive):

    def test_L1_a_pending_proposal_waits_for_the_decision_and_offers_it(self):
        out = self.pending()
        view = self.derive(self.surface.status(out["request_ref"]))
        self.assertEqual(view["stage"], "of_record")
        self.assertEqual(view["actions"], ["decide", "withdraw"])
        self.assertFalse(view["terminal"])
        self.assertIn("Nothing has been approved", view["detail"])
        self.assertIn("revision 1", view["detail"])
        self.assertIn(out["proposal_digest_sha256"][:12], view["detail"])

    def test_L2_withdrawal_is_offered_only_while_the_panel_holds_the_key(self):
        out = self.pending()
        view = self.derive(self.surface.status(out["request_ref"]), capability_held=False)
        self.assertEqual(view["actions"], ["decide"])
        self.assertIn("withdraw", view["notice"])

    def test_L3_a_withdrawn_proposal_is_WITHDRAWN_and_closed_not_denied(self):
        out = self.pending()
        self.surface.cancel(out["request_ref"], out["control_capability"])
        view = self.derive(self.surface.status(out["request_ref"]))
        self.assertEqual(view["stage"], "withdrawn")
        self.assertTrue(view["terminal"])
        self.assertEqual(view["actions"], [])
        self.assertIn("withdrawn", view["detail"])
        self.assertIn("permanently closed", view["detail"])
        self.assertIn("not a decision", view["detail"])

    def test_L4_a_replaced_proposal_is_REVISED_by_a_separate_record(self):
        out = self.pending()
        self.surface.cancel(out["request_ref"], out["control_capability"])
        lineage = {"supersedes": None, "superseded_by": "lr-" + "9" * 32, "turn_id": None}
        view = self.derive(self.surface.status(out["request_ref"]), lineage)
        self.assertEqual(view["stage"], "revised")
        self.assertTrue(view["terminal"])
        self.assertIn("lr-" + "9" * 32, view["label"])
        self.assertIn("separate", view["detail"])
        self.assertIn("nothing was edited in place", view["detail"])

    def test_L5_an_interrupted_withdrawal_is_incomplete_and_offers_only_withdraw(self):
        out = self.pending()
        entry = self.surface._store.load()["requests"][out["request_ref"]]
        self.missions.withdraw_proposal(
            out["mission_id"], entry["mission_request_id"], out["control_capability"],
            surface_module.LOCAL_CALLER_CONTEXT)
        view = self.derive(self.surface.status(out["request_ref"]))
        self.assertEqual(view["stage"], "withdrawal_incomplete")
        self.assertEqual(view["actions"], ["withdraw"])
        self.assertFalse(view["terminal"])

    def test_L6_an_approved_proposal_is_attested_not_verified_and_offers_the_start(self):
        out = self.pending()
        self.approve(out, expires_in=600)
        view = self.derive(self.surface.status(out["request_ref"]))
        self.assertEqual(view["stage"], "approved")
        self.assertEqual(view["actions"], ["dispatch"])
        self.assertIn("not independently verified", view["label"])
        self.assertIn("operator attestation", view["detail"])
        self.assertEqual(view["approval_expires_at"], self.clock.now + 600)
        self.assertEqual(view["provenance"], {
            "principal_kind": "operator_attested_relay",
            "proof": "operator_attested_not_independently_verified"})
        self.assertEqual(view["residual_risk"],
                         mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK)

    def test_L7_an_approval_that_lapsed_before_the_start_is_EXPIRED_and_terminal(self):
        out = self.pending()
        self.approve(out, expires_in=600)
        status = self.surface.status(out["request_ref"])
        view = self.derive(status, now=self.clock.now + 601)
        self.assertEqual(view["stage"], "expired")
        self.assertTrue(view["terminal"])
        self.assertEqual(view["actions"], [])
        self.assertIn("Nothing was started", view["detail"])

    def test_L8_the_start_is_not_offered_without_a_run_or_for_an_unrunnable_proposal(self):
        out = self.pending()
        self.approve(out)
        status = self.surface.status(out["request_ref"])
        no_bridge = self.derive(status, run_available=False)
        self.assertEqual(no_bridge["actions"], [])
        self.assertIn("run", no_bridge["notice"])
        unrunnable = self.derive(status, runnable_problem="it has no repository")
        self.assertEqual(unrunnable["actions"], [])
        self.assertIn("no repository", unrunnable["notice"])

    def test_L9_an_unknown_approval_outcome_is_reconciled_never_reapplied(self):
        out = self.pending()
        status = self.surface.status(out["request_ref"])
        status["attested_approval"] = {
            "state": "HOLD", "binding": {"expires_at": self.clock.now + 600},
            "relayed_reply": "approve"}
        view = self.derive(status)
        self.assertEqual(view["stage"], "approval_uncertain")
        self.assertEqual(view["actions"], ["check_approval"])
        self.assertIn("not applied again", view["detail"])

    def test_L9b_an_unknown_approval_outcome_wins_even_when_Mission_Core_already_shows_AUTHORIZED(self):
        # The approval DID land in Mission Core but its answer was lost: the
        # surface holds HOLD while Mission Core reads AUTHORIZED. That is NOT
        # "approved outside this panel": whose decision it was is exactly what
        # is unknown, and the way out is to reconcile, never to re-apply.
        out = self.pending()
        self.approve(out)
        status = self.surface.status(out["request_ref"])
        status["attested_approval"]["state"] = "HOLD"
        view = self.derive(status)
        self.assertEqual(view["stage"], "approval_uncertain")
        self.assertEqual(view["actions"], ["check_approval"])
        self.assertNotIn("outside this panel", view["label"])

    def test_L10_a_mission_closed_elsewhere_is_shown_as_Mission_Cores_record(self):
        out = self.pending()
        self.authenticated_approve(out["mission_id"], 1)
        status = self.surface.status(out["request_ref"])
        view = self.derive(status)
        self.assertEqual(view["stage"], "approved_elsewhere")
        self.assertIn("outside this panel", view["label"])
        self.assertNotIn("attested", view["detail"])

    def test_L11_a_record_that_cannot_be_read_is_never_shown_as_current(self):
        out = self.pending()
        status = self.surface.status(out["request_ref"])
        status["mission_observation"] = None
        status["mission_observation_problem"] = "local_request_mission_unavailable"
        view = self.derive(status)
        self.assertEqual(view["stage"], "unreadable")
        self.assertEqual(view["actions"], [])

    def test_L12_a_refused_approval_attempt_leaves_the_proposal_waiting_with_a_notice(self):
        out = self.pending()
        status = self.surface.status(out["request_ref"])
        status["attested_approval"] = {
            "state": "REFUSED", "binding": {"expires_at": self.clock.now + 600},
            "relayed_reply": "approve",
            "outcome": {"authorization_id": None, "problem": "mission_binding_stale"}}
        view = self.derive(status)
        self.assertEqual(view["stage"], "of_record")
        self.assertIn("mission_binding_stale", view["notice"])
        self.assertIn("nothing was approved", view["notice"])


class RunStages(Derive):
    """SYNTHETIC overlays of the run section of a real APPROVED status."""

    def approved_status(self):
        out = self.pending()
        self.approve(out)
        return self.surface.status(out["request_ref"])

    def stage(self, **run):
        return self.derive(self.overlay_run(self.approved_status(), **run))

    def test_R1_a_start_with_no_receipt_is_a_HOLD_that_is_reconciled_not_retried(self):
        for phase in (surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                      surface_module.RUN_PHASE_HOLD_IDENTITY_UNKNOWN):
            with self.subTest(phase=phase):
                view = self.stage(phase=phase, hold=True,
                                  lifecycle_state="AUTHORIZED")
                self.assertEqual(view["stage"], "dispatch_uncertain")
                self.assertEqual(view["actions"], ["reconcile"])
                self.assertNotIn("dispatch", view["actions"])
                self.assertIn("not started again", view["detail"])
                self.assertIn("exactly once", view["detail"])

    def test_R2_a_recorded_start_is_durable_intent_not_an_exactly_once_guarantee(self):
        view = self.stage(phase=surface_module.RUN_PHASE_DISPATCHED,
                          lifecycle_state="AUTHORIZED")
        self.assertEqual(view["stage"], "dispatched")
        self.assertEqual(view["actions"], ["observe"])
        self.assertIn("recorded", view["detail"])
        self.assertIn("not a guarantee", view["detail"])
        self.assertIn("exactly once", view["detail"])

    def test_R3_a_running_team_offers_verification_only_once_its_outcome_is_determined(self):
        base = self.overlay_run(self.approved_status(),
                                phase=surface_module.RUN_PHASE_RUNNING,
                                lifecycle_state="RUNNING")
        waiting = self.derive(base, verification_ready={
            "ready": False, "target_stopped": False, "waiting_for": "target_stopped"})
        self.assertEqual(waiting["stage"], "running")
        self.assertEqual(waiting["actions"], [])
        self.assertIn("target_stopped", waiting["notice"])
        ready = self.derive(base, verification_ready={
            "ready": True, "target_stopped": True, "waiting_for": None})
        self.assertEqual(ready["actions"], ["verify"])
        self.assertIsNone(ready["notice"])
        unknown = self.derive(base, verification_ready=None)
        self.assertEqual(unknown["actions"], [])

    def test_R3b_a_stopped_team_that_would_not_verify_says_so_before_the_decision_is_recorded(self):
        base = self.overlay_run(self.approved_status(),
                                phase=surface_module.RUN_PHASE_RUNNING,
                                lifecycle_state="RUNNING")
        view = self.derive(base, verification_ready={
            "ready": False, "target_stopped": True, "waiting_for": "target_not_succeeded"})
        self.assertEqual(view["actions"], ["verify"])
        self.assertIn("would not verify", view["notice"])
        self.assertIn("target_not_succeeded", view["notice"])
        self.assertIn("stops the run", view["notice"])

    def test_R4_a_verification_blocked_pending_proof_may_be_verified_again_when_ready(self):
        base = self.overlay_run(
            self.approved_status(),
            phase=surface_module.RUN_PHASE_PENDING_PROOF, lifecycle_state="RUNNING",
            pending_proof={"blockers": [{"code": "x", "detail": "y"}], "attempts": 1})
        view = self.derive(base, verification_ready={
            "ready": True, "target_stopped": True, "waiting_for": None})
        self.assertEqual(view["stage"], "pending_proof")
        self.assertEqual(view["actions"], ["verify"])
        self.assertFalse(view["terminal"])

    def test_R5_a_verified_run_offers_the_result_and_is_terminal(self):
        view = self.stage(phase=surface_module.RUN_PHASE_COMPLETED,
                          lifecycle_state="COMPLETED", engineering_verified=True)
        self.assertEqual(view["stage"], "verified")
        self.assertEqual(view["actions"], ["result"])
        self.assertTrue(view["terminal"])
        self.assertIn("not delivered", view["detail"])

    def test_R6_a_stopped_run_names_the_reason_and_is_terminal(self):
        view = self.stage(phase=surface_module.RUN_PHASE_BLOCKED,
                          lifecycle_state="BLOCKED", outcome="target_stopped")
        self.assertEqual(view["stage"], "blocked")
        self.assertTrue(view["terminal"])
        self.assertIn("target_stopped", view["detail"])

    def test_R7_a_cancelled_run_is_terminal(self):
        view = self.stage(phase=surface_module.RUN_PHASE_CANCELLED,
                          lifecycle_state="CANCELLED", outcome="cancelled")
        self.assertEqual(view["stage"], "run_cancelled")
        self.assertTrue(view["terminal"])

    def test_R8_a_paused_run_says_external_work_is_not_suspended(self):
        view = self.stage(phase=surface_module.RUN_PHASE_RUNNING, lifecycle_state="RUNNING",
                          paused=True, pause_statement=surface_module.RUN_PAUSE_STATEMENT)
        self.assertIn("not suspended", view["notice"])


class ProgressionStages(RunStages):
    """With a progression runtime passing, observing, reconciling and
    verifying are ITS job, not the person's: those buttons are not the ordinary
    flow. The explicit Start stays, and so does the explicit decision on a run
    that stopped without succeeding. With no runtime passing, the manual
    fallback is offered AND the screen says nothing is advancing automatically.
    SYNTHETIC overlays, as above."""

    RUNNING = {"state": "running", "detail": "the progression runtime is passing"}

    def not_running(self, state="stopped", detail="no progression runtime has reported here"):
        return {"state": state, "detail": detail}

    def running_base(self):
        return self.overlay_run(self.approved_status(),
                                phase=surface_module.RUN_PHASE_RUNNING,
                                lifecycle_state="RUNNING")

    def test_PG1_an_unknown_start_is_reconciled_by_the_runtime_not_by_a_button(self):
        for phase in (surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                      surface_module.RUN_PHASE_HOLD_IDENTITY_UNKNOWN):
            with self.subTest(phase=phase):
                status = self.overlay_run(self.approved_status(), phase=phase, hold=True,
                                          lifecycle_state="AUTHORIZED")
                view = self.derive(status, progression=self.RUNNING)
                self.assertEqual(view["stage"], "dispatch_uncertain")
                self.assertEqual(view["actions"], [])
                self.assertIn("automatically", view["detail"])
                self.assertIn("never starts the work again", view["detail"])
                self.assertNotIn("Check again", view["detail"])

    def test_PG2_a_requested_start_is_observed_by_the_runtime_not_by_a_button(self):
        status = self.overlay_run(self.approved_status(),
                                  phase=surface_module.RUN_PHASE_DISPATCHED,
                                  lifecycle_state="AUTHORIZED")
        view = self.derive(status, progression=self.RUNNING)
        self.assertEqual(view["actions"], [])
        self.assertIn("automatically", view["detail"])
        self.assertIn("exactly once", view["detail"])

    def test_PG3_a_working_team_is_left_to_work_and_nothing_is_offered(self):
        view = self.derive(self.running_base(), progression=self.RUNNING, verification_ready={
            "ready": False, "target_stopped": False, "waiting_for": "verify_target_not_stopped"})
        self.assertEqual(view["actions"], [])
        self.assertIn("verify_target_not_stopped", view["notice"])

    def test_PG4_a_run_that_is_ready_is_verified_by_the_runtime_not_by_a_button(self):
        view = self.derive(self.running_base(), progression=self.RUNNING, verification_ready={
            "ready": True, "target_stopped": True, "waiting_for": None})
        self.assertEqual(view["actions"], [], "a button raced the runtime to the decision")
        self.assertIn("next pass", view["notice"])

    def test_PG5_a_stopped_team_that_would_not_verify_keeps_the_explicit_decision(self):
        view = self.derive(self.running_base(), progression=self.RUNNING, verification_ready={
            "ready": False, "target_stopped": True, "waiting_for": "verify_target_not_succeeded"})
        self.assertEqual(view["actions"], ["verify"])
        self.assertIn("would not verify", view["notice"])
        self.assertIn("never ends a run on its own", view["notice"])

    def test_PG6_a_run_waiting_on_proof_is_verified_again_only_by_an_explicit_act(self):
        base = self.overlay_run(
            self.approved_status(),
            phase=surface_module.RUN_PHASE_PENDING_PROOF, lifecycle_state="RUNNING",
            pending_proof={"blockers": [{"code": "x", "detail": "y"}], "attempts": 1})
        view = self.derive(base, progression=self.RUNNING, verification_ready={
            "ready": True, "target_stopped": True, "waiting_for": None})
        self.assertEqual(view["actions"], ["verify"])
        self.assertIn("does not retry", view["notice"])

    def test_PG7_with_no_runtime_passing_the_manual_fallback_returns_and_says_why(self):
        for state, detail in (("stopped", "no progression runtime has reported here"),
                              ("stale", "no pass in over 30 seconds"),
                              ("mismatch", "different locations"),
                              ("unreadable", "the status file is not JSON"),
                              ("failing", "its last pass failed (JournalError)")):
            with self.subTest(state=state):
                progression = self.not_running(state, detail)
                hold = self.derive(self.overlay_run(
                    self.approved_status(), phase=surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                    hold=True, lifecycle_state="AUTHORIZED"), progression=progression)
                self.assertEqual(hold["actions"], ["reconcile"])
                self.assertIn("Nothing is advancing this Mission automatically", hold["notice"])
                self.assertIn(detail, hold["notice"])
                dispatched = self.derive(self.overlay_run(
                    self.approved_status(), phase=surface_module.RUN_PHASE_DISPATCHED,
                    lifecycle_state="AUTHORIZED"), progression=progression)
                self.assertEqual(dispatched["actions"], ["observe"])
                ready = self.derive(self.running_base(), progression=progression,
                                    verification_ready={"ready": True, "target_stopped": True,
                                                        "waiting_for": None})
                self.assertEqual(ready["actions"], ["verify"])
                self.assertIn("by hand", ready["notice"])

    def test_PG8_no_probe_at_all_changes_nothing_and_adds_no_claim(self):
        view = self.derive(self.running_base(), progression=None, verification_ready={
            "ready": True, "target_stopped": True, "waiting_for": None})
        self.assertEqual(view["actions"], ["verify"])
        self.assertIsNone(view["notice"])

    def test_PG9_stages_before_a_run_and_terminal_stages_ignore_the_runtime(self):
        out = self.pending()
        before = self.surface.status(out["request_ref"])
        self.assertEqual(self.derive(before, progression=self.RUNNING),
                         self.derive(before, progression=None))
        done = self.overlay_run(self.approved_status(), phase=surface_module.RUN_PHASE_COMPLETED,
                                lifecycle_state="COMPLETED", engineering_verified=True)
        self.assertEqual(self.derive(done, progression=self.RUNNING),
                         self.derive(done, progression=self.not_running()))

    def test_PG10_no_progression_wording_promises_success_or_exactly_once(self):
        views = [
            self.derive(self.running_base(), progression=self.RUNNING, verification_ready={
                "ready": True, "target_stopped": True, "waiting_for": None}),
            self.derive(self.overlay_run(
                self.approved_status(), phase=surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                hold=True, lifecycle_state="AUTHORIZED"), progression=self.RUNNING),
        ]
        for view in views:
            text = " ".join(filter(None, (view["detail"], view["notice"]))).lower()
            # (The honest negation "not a guarantee that it ran exactly once" is
            # required wording and stays; a PROMISE would be "guaranteed".)
            for banned in ("will complete", "will succeed", "guaranteed", "is verified"):
                self.assertNotIn(banned, text)


class WordingTests(Derive):

    def every_view(self):
        out = self.pending()
        views = [self.derive(self.surface.status(out["request_ref"]))]
        self.approve(out)
        approved = self.surface.status(out["request_ref"])
        views.append(self.derive(approved))
        views.append(self.derive(approved, now=self.clock.now + 9999))
        for phase, state in (
                (surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN, "AUTHORIZED"),
                (surface_module.RUN_PHASE_DISPATCHED, "AUTHORIZED"),
                (surface_module.RUN_PHASE_RUNNING, "RUNNING"),
                (surface_module.RUN_PHASE_PENDING_PROOF, "RUNNING"),
                (surface_module.RUN_PHASE_COMPLETED, "COMPLETED"),
                (surface_module.RUN_PHASE_BLOCKED, "BLOCKED"),
                (surface_module.RUN_PHASE_CANCELLED, "CANCELLED")):
            views.append(self.derive(self.overlay_run(
                approved, phase=phase, lifecycle_state=state, outcome="x",
                pending_proof={"blockers": [], "attempts": 1})))
        second = self.pending(objective="B")
        self.surface.cancel(second["request_ref"], second["control_capability"])
        views.append(self.derive(self.surface.status(second["request_ref"])))
        return views

    def test_W1_no_stage_ever_claims_a_denial_or_a_verified_human(self):
        for view in self.every_view():
            text = " ".join(str(view.get(k) or "") for k in ("label", "detail", "notice"))
            self.assertIsNone(re.search(r"\b(deny|denied|denial|rejected)\b", text, re.I), text)
            self.assertIsNone(re.search(r"human.{0,20}verified|verified.{0,12}human", text, re.I), text)

    def test_W2_every_stage_has_words_and_a_closed_action_set(self):
        for view in self.every_view():
            self.assertTrue(view["label"] and view["detail"], view["stage"])
            self.assertLessEqual(set(view["actions"]), set(lifecycle.ACTIONS))
            self.assertIn(view["stage"], lifecycle.STAGES)

    def test_W3_the_stage_vocabulary_is_closed(self):
        self.assertEqual(len(lifecycle.STAGES), len(set(lifecycle.STAGES)))
        for must in ("of_record", "withdrawn", "revised", "expired", "approved",
                     "dispatch_uncertain", "dispatched", "running", "verified"):
            self.assertIn(must, lifecycle.STAGES)


if __name__ == "__main__":
    unittest.main()
