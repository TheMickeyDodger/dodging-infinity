"""Tests for ``MissionBridge.advance`` and the cross-process run guard wiring.

``advance`` is the ONE narrow operation the progression runtime drives. It acts
only by the Mission's ACTUAL durable state, one step per call:

- no durable run intent (the person has not pressed Start): nothing, ever. It
  never dispatches, so an approved-but-unstarted Mission is never started by
  progression;
- an intent with no bound target: one bounded, evidence-only reconcile (never a
  respawn), spaced so the durable attempt counter is not burned in seconds;
- a bound target not yet observed: observe;
- RUNNING: ONE fresh set of reads, and a verification is recorded ONLY if every
  conjunct holds in THAT set. It is never "ask readiness, then verify": verify
  re-reads and stops a Mission terminally if the second read differs.

These run the REAL bridge over the REAL Mission Core with the bridge suite's
scripted observer, spawn recorder and fake transport (the repo's way of driving
the bridge without a live Herdr). Nothing here is evidence of a live Herdr.
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from target_runtime import mission_run_guard as guard_module  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    Fixture, TASK_ID, raw_observation, unobserved_raw,
)


class CountingObserver(object):
    """Wraps the fixture's observer and counts calls (one fresh read set = one)."""

    def __init__(self, inner):
        self.inner, self.calls, self.then = inner, 0, None

    def __call__(self, path):
        self.calls += 1
        answer = self.inner(path)
        if self.then is not None and self.calls == 1:
            self.inner.raw = self.then            # the world changes AFTER the first read
        return answer

    @property
    def tasks(self):
        return self.inner.tasks


class AdvanceFixture(Fixture):
    def make_bridge(self, missions=None, run_guard=None):
        return bridge_module.MissionBridge(
            missions or self.missions, "/control-repo", self.clock,
            spawn_fn=self.spawn, observer_fn=self.observer,
            spawn_records_fn=self.records, transport=self.transport,
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            worker=self.trust, run_guard=run_guard)

    def guarded_bridge(self, wait_seconds=0.2):
        guard = guard_module.RunGuard(self.state, wait_seconds=wait_seconds)
        return self.make_bridge(run_guard=guard), guard

    def advance(self, mission_id, bridge=None):
        return (bridge or self.bridge).advance(mission_id)

    def action(self, result):
        return result["advance"]["action"]

    def finish_team(self, mission_id, status="COMPLETE", artifacts=True):
        if artifacts:
            self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status=status)

    def started(self, **overrides):
        """Approved, the person pressed Start (the intent and receipt are
        durable); not yet observed."""
        mission_id = self.approved(**overrides)
        self.dispatch(mission_id)
        return mission_id


class AdvanceTests(AdvanceFixture):

    def test_AD1_an_approved_unstarted_mission_is_never_started_by_progression(self):
        mission_id = self.approved()
        before = self.mission_bytes()
        for _ in range(3):
            result = self.advance(mission_id)
            self.assertEqual(self.action(result), "none")
            self.assertIn("Start", result["advance"]["detail"])
        self.assertEqual(self.spawn.calls, [], "progression dispatched a Mission")
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")
        self.assertIsNone(self.mission(mission_id).get("run"))

    def test_AD2_a_started_mission_is_observed_into_running_with_one_spawn_only(self):
        mission_id = self.started()
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "observe")
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertEqual(len(self.spawn.calls), 1)
        self.advance(mission_id)
        self.assertEqual(len(self.spawn.calls), 1, "progression spawned again")

    def test_AD3_a_target_not_yet_named_by_its_workspace_waits_visibly(self):
        mission_id = self.started()
        self.observer.tasks.clear()                 # the workspace does not name this run
        before = self.mission_bytes()
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "wait")
        self.assertEqual(result["advance"]["waiting_for"], "target_not_observed")
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_AD4_a_team_still_working_is_waiting_and_never_ended(self):
        mission_id = self.running()
        before = self.mission_bytes()
        for _ in range(3):
            result = self.advance(mission_id)
            self.assertEqual(self.action(result), "wait")
            self.assertEqual(result["advance"]["waiting_for"],
                             "verify_target_not_stopped")
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertIsNone(self.mission(mission_id)["run"].get("verification"))

    def test_AD5_a_completed_team_with_its_exact_report_is_verified_and_retrievable(self):
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "verify")
        self.assertEqual(result["state"], "COMPLETED", result.get("verification"))
        self.assertTrue(result["verification"]["verified"])
        retrieved = self.bridge.result(mission_id)
        self.assertTrue(retrieved["verified_result"])
        self.assertEqual(len(self.spawn.calls), 1)
        # Terminal: a later pass has nothing to do and changes nothing.
        before = self.mission_bytes()
        self.assertEqual(self.action(self.advance(mission_id)), "none")
        self.assertEqual(self.mission_bytes(), before)

    def test_AD6_unmet_proof_is_recorded_once_and_never_retried_into_exhaustion(self):
        mission_id = self.running()
        self.finish_team(mission_id)                # the contract's other proof is NOT met
        first = self.advance(mission_id)
        self.assertEqual(self.action(first), "verify")
        self.assertEqual(first["state"], "RUNNING", "unmet proof is not terminal")
        self.assertIsNone(first["verification"])
        attempts = self.mission(mission_id)["run"]["pending_proof"]["attempts"]
        self.assertEqual(attempts, 1)
        before = self.mission_bytes()
        for _ in range(mission_record.MAX_VERIFICATION_ATTEMPTS + 2):
            again = self.advance(mission_id)
            self.assertEqual(self.action(again), "wait")
            self.assertEqual(again["advance"]["waiting_for"],
                             "verification_pending_proof")
        self.assertEqual(self.mission_bytes(), before,
                         "progression re-verified a run whose proof is pending")
        # The obligation is met through its existing seam; the SAME run verifies
        # on an explicit verify (progression does not guess).
        self.satisfy_tests_pass(mission_id)
        outcome = self.bridge.verify(mission_id, self.reported())
        self.assertEqual(outcome["state"], "COMPLETED")

    def test_AD7_a_team_that_stopped_in_error_is_a_visible_hold_never_a_verification(self):
        mission_id = self.running()
        self.finish_team(mission_id, status="ERROR")
        before = self.mission_bytes()
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "hold")
        self.assertEqual(result["advance"]["waiting_for"],
                         "verify_target_not_succeeded")
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertFalse(self.bridge.result(mission_id)["verified_result"])

    def test_AD8_stopped_without_its_files_holds_then_verifies_when_they_appear(self):
        mission_id = self.running()
        self.finish_team(mission_id, artifacts=False)
        self.satisfy_tests_pass(mission_id)
        before = self.mission_bytes()
        held = self.advance(mission_id)
        self.assertEqual(self.action(held), "hold")
        self.assertEqual(held["advance"]["waiting_for"], "verify_result_not_reported")
        self.assertEqual(self.mission_bytes(), before, "a not-yet-written file ended the run")
        self.write_artifacts(mission_id)
        self.assertEqual(self.action(self.advance(mission_id)), "verify")
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")

    def test_AD9_a_receipt_that_never_arrived_is_reconciled_without_a_second_spawn(self):
        mission_id = self.approved()
        self.spawn.raises = "after_child"           # the child exists; the receipt is lost
        self.assertTrue(self.dispatch(mission_id)["hold"])
        self.spawn.raises = None
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "reconcile")
        receipt = self.mission(mission_id)["run"]["receipt"]
        self.assertEqual(receipt["task_id"], TASK_ID)
        self.assertEqual(receipt["identity_source"], "reconciliation")
        self.assertEqual(len(self.spawn.calls), 1, "reconciliation respawned")
        self.assertEqual(self.action(self.advance(mission_id)), "observe")
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_AD10_a_child_that_never_appears_is_stopped_durably_not_succeeded(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        self.assertTrue(self.dispatch(mission_id)["hold"])
        self.spawn.raises = None
        self.observer.raw = unobserved_raw()
        first = self.advance(mission_id)
        self.assertEqual(self.action(first), "reconcile")
        self.assertEqual(first["reconcile_attempts"], 1)
        # Passes inside the spacing do not burn the durable counter.
        for _ in range(5):
            soon = self.advance(mission_id)
            self.assertEqual(self.action(soon), "wait")
            self.assertEqual(soon["advance"]["waiting_for"], "reconcile_spacing")
        self.assertEqual(
            self.mission(mission_id)["run"]["reconcile_unobserved"]["attempts"], 1)
        for attempt in range(2, mission_record.MAX_RECONCILE_ATTEMPTS + 1):
            self.clock.now += bridge_module.RECONCILE_SPACING_SECONDS
            result = self.advance(mission_id)
            self.assertEqual(self.action(result), "reconcile")
        mission = self.mission(mission_id)
        self.assertEqual(mission["state"], "BLOCKED")
        self.assertEqual([e["reason"] for e in mission["lifecycle"]],
                         ["reconcile_not_observable"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(self.action(self.advance(mission_id)), "none")
        self.assertEqual(self.mission(mission_id)["run"]["reconcile_unobserved"]
                         ["attempts"], mission_record.MAX_RECONCILE_ATTEMPTS)

    def test_AD11_a_mission_another_process_is_acting_on_is_skipped_not_raced(self):
        guarded, guard = self.guarded_bridge()
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        before = self.mission_bytes()
        with guard.hold(mission_id) as held:
            self.assertTrue(held)
            result = self.advance(mission_id, bridge=guarded)
            self.assertEqual(self.action(result), "busy")
            self.assertEqual(self.mission_bytes(), before)
            # The explicit operations wait their bound, then say so plainly.
            self.refused(bridge_module.PROBLEM_RUN_BUSY, guarded.verify, mission_id,
                         self.reported())
            self.refused(bridge_module.PROBLEM_RUN_BUSY, guarded.observe, mission_id)
            self.refused(bridge_module.PROBLEM_RUN_BUSY, guarded.reconcile, mission_id)
            self.refused(bridge_module.PROBLEM_RUN_BUSY, guarded.dispatch, mission_id)
            self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.action(self.advance(mission_id, bridge=guarded)),
                         "verify")

    def test_AD12_a_restart_continues_from_the_durable_record_without_respawning(self):
        mission_id = self.running()
        restarted = self.make_bridge()               # a fresh process: no memory
        self.assertEqual(self.action(self.advance(mission_id, bridge=restarted)), "wait")
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.assertEqual(self.action(self.advance(mission_id, bridge=restarted)), "verify")
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_AD13_a_paused_run_is_not_progressed(self):
        mission_id = self.running()
        self.bridge.pause(mission_id)
        self.finish_team(mission_id)
        before = self.mission_bytes()
        result = self.advance(mission_id)
        self.assertEqual(self.action(result), "paused")
        self.assertEqual(self.mission_bytes(), before)

    def test_AD14_a_terminal_mission_has_nothing_to_advance(self):
        done, _ = self.verified()
        before = self.mission_bytes()
        self.assertEqual(self.action(self.advance(done)), "none")
        blocked = self.running()
        self.observer.raw = raw_observation(status="ERROR")
        self.write_artifacts(blocked)
        self.bridge.verify(blocked, self.reported())          # the explicit decision
        self.assertEqual(self.mission(blocked)["state"], "BLOCKED")
        mid = self.mission_bytes()
        self.assertEqual(self.action(self.advance(blocked)), "none")
        self.assertEqual(self.mission_bytes(), mid)
        self.assertNotEqual(before, mid)

    def test_AD15_one_fresh_read_set_decides_so_a_changed_world_cannot_end_the_run(self):
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        counting = CountingObserver(self.observer)
        bridge = self.make_bridge()
        bridge._observer = counting
        # The world changes the instant after the first read: the team is
        # "running" again. A decision made from a SECOND read would see it.
        counting.then = raw_observation(status="RUNNING")
        result = self.advance(mission_id, bridge=bridge)
        self.assertEqual(counting.calls, 1, "advance read the target more than once")
        self.assertEqual(self.action(result), "verify")
        self.assertEqual(result["state"], "COMPLETED")

    def test_AD16_readiness_then_verify_would_have_ended_that_run(self):
        # The hazard AD15 closes, shown on the same scenario: two reads.
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        counting = CountingObserver(self.observer)
        bridge = self.make_bridge()
        bridge._observer = counting
        counting.then = raw_observation(status="RUNNING")
        answer = bridge.verification_readiness(mission_id)
        self.assertTrue(answer["ready"])
        outcome = bridge.verify(mission_id, answer["reported_result"])
        self.assertEqual(outcome["state"], "BLOCKED")

    def test_AD17_an_unreadable_world_is_reported_unreadable_and_changes_nothing(self):
        mission_id = self.running()
        before = self.mission_bytes()

        def unreadable(path):
            raise OSError("synthetic: the observer cannot read")
        bridge = self.make_bridge()
        bridge._observer = unreadable
        result = self.advance(mission_id, bridge=bridge)
        # The evidence layer turns a raising observer into an UNREADABLE binding;
        # that is unknown, never "the team has not stopped".
        self.assertEqual(self.action(result), "unreadable")
        self.assertIn("refused_unreadable", result["advance"]["detail"])
        self.assertIsNone(result["advance"]["waiting_for"])
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")

    def test_AD17b_a_raising_read_outside_the_evidence_layer_is_named_by_class_only(self):
        mission_id = self.running()
        before = self.mission_bytes()

        def exploding(self_, workspace):
            raise OSError("synthetic: /private/path/that/must/not/leak")
        bridge = self.make_bridge()
        bridge._fresh_reads = exploding.__get__(bridge)
        result = self.advance(mission_id, bridge=bridge)
        self.assertEqual(self.action(result), "unreadable")
        self.assertIn("OSError", result["advance"]["detail"])
        self.assertNotIn("private", result["advance"]["detail"])
        self.assertEqual(self.mission_bytes(), before)

    def test_AD17c_an_unreadable_bound_target_is_not_reported_as_not_yet_there(self):
        mission_id = self.started()
        before = self.mission_bytes()

        def unreadable(path):
            raise OSError("synthetic")
        bridge = self.make_bridge()
        bridge._observer = unreadable
        result = self.advance(mission_id, bridge=bridge)
        self.assertEqual(self.action(result), "unreadable")
        self.assertIn("refused_unreadable", result["advance"]["detail"])
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_AD21_a_state_that_moved_under_the_step_is_reported_not_raised(self):
        mission_id = self.running()
        bridge = self.make_bridge()

        def moved(self_, mission_id_):
            raise bridge_module.MissionBridgeRefusal(
                bridge_module.PROBLEM_WRONG_STATE, "synthetic: cancelled meanwhile")

        bridge._fresh_reads = moved.__get__(bridge)
        self.assertEqual(self.action(self.advance(mission_id, bridge=bridge)), "none")
        paused = self.make_bridge()

        def paused_(self_, workspace):
            raise bridge_module.MissionBridgeRefusal(
                bridge_module.PROBLEM_PAUSED, "synthetic")
        paused._fresh_reads = paused_.__get__(paused)
        self.assertEqual(self.action(self.advance(mission_id, bridge=paused)), "paused")

    def test_AD18_an_unknown_mission_is_refused_not_invented(self):
        self.refused(bridge_module.PROBLEM_MISSION_CORE, self.bridge.advance,
                     "mn-" + "9" * 32)

    def test_AD19_the_answer_has_exactly_the_documented_shape(self):
        mission_id = self.running()
        result = self.advance(mission_id)
        self.assertEqual(sorted(result["advance"]),
                         ["action", "detail", "waiting_for"])
        self.assertIn(result["advance"]["action"], bridge_module.ADVANCE_ACTIONS)
        self.assertEqual(bridge_module.ADVANCE_ACTIONS,
                         ("none", "paused", "busy", "reconcile", "observe", "wait",
                          "hold", "verify", "unreadable"))

    def test_AD20_advance_never_names_a_delivery_or_widens_authority(self):
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        result = self.advance(mission_id)
        self.assertEqual(result["delivery_authority"], "none")
        self.assertFalse(result["delivered"])


class GuardedOperationTests(AdvanceFixture):
    """The explicit operations take the same per-Mission guard as advance."""

    def test_GD1_an_unguarded_bridge_behaves_as_before(self):
        # The default (no guard) is the existing behavior: nothing waits, and
        # the whole existing flow (approve, dispatch, observe, verify) works.
        mission_id, result = self.verified()
        self.assertEqual(result["state"], "COMPLETED")

    def test_GD2_every_explicit_operation_releases_the_guard_even_when_it_refuses(self):
        guarded, guard = self.guarded_bridge()
        mission_id = self.approved()
        self.refused(bridge_module.PROBLEM_WRONG_STATE, guarded.observe, mission_id)
        self.refused(bridge_module.PROBLEM_WRONG_STATE, guarded.verify, mission_id,
                     self.reported())
        self.refused(bridge_module.PROBLEM_WRONG_STATE, guarded.reconcile, mission_id)
        with guard.hold(mission_id, blocking=False) as held:
            self.assertTrue(held, "an operation left the guard held")


if __name__ == "__main__":
    unittest.main()
