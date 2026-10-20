"""Tests for ``MissionBridge.verification_readiness``: the READ-ONLY question
"would verifying this run now be decided, and how?".

Why it exists: ``verify`` is not harmless to call early. When any conjunct
fails it stops the Mission durably and terminally (RUNNING -> BLOCKED), and
"the team is still working" is a failing conjunct. A panel that offered
``verify`` while the team worked would let one click end a healthy run. The
readiness read computes the SAME conjuncts ``verify`` does, from the same
fresh reads, and records nothing, so a window can offer verification only once
its outcome is determined.

These run the REAL bridge over the REAL Mission Core with the bridge test
suite's own scripted observer, spawn recorder and fake transport (the repo's
established way of driving the bridge without a live Herdr). Nothing here is
evidence of a live Herdr.
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
from test_mission_bridge import (  # noqa: E402
    Fixture, TASK_ID, raw_observation, sha256, RESULT_TEXT, REVIEW_TEXT,
)


class ReadinessTests(Fixture):

    def readiness(self, mission_id):
        return self.bridge.verification_readiness(mission_id)

    def finish_team(self, mission_id, status="COMPLETE", artifacts=True):
        if artifacts:
            self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status=status)

    def test_RD1_a_team_still_working_is_waiting_and_the_run_is_untouched(self):
        mission_id = self.running()
        before = self.mission_bytes()
        answer = self.readiness(mission_id)
        self.assertFalse(answer["ready"])
        self.assertFalse(answer["target_stopped"])
        self.assertEqual(answer["waiting_for"], "verify_target_not_stopped")
        self.assertEqual(self.mission_bytes(), before, "readiness wrote something")
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")

    def test_RD2_a_finished_team_is_ready_and_its_report_is_the_one_verify_accepts(self):
        mission_id = self.running()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        before = self.mission_bytes()
        answer = self.readiness(mission_id)
        self.assertTrue(answer["ready"], answer)
        self.assertTrue(answer["target_stopped"])
        self.assertIsNone(answer["waiting_for"])
        self.assertEqual(answer["reported_result"], self.reported())
        self.assertEqual(self.mission_bytes(), before)
        result = self.bridge.verify(mission_id, answer["reported_result"])
        self.assertEqual(result["state"], "COMPLETED", result.get("verification"))

    def test_RD3_a_team_that_stopped_in_error_is_determined_and_would_not_verify(self):
        mission_id = self.running()
        self.finish_team(mission_id, status="ERROR")
        answer = self.readiness(mission_id)
        self.assertFalse(answer["ready"])
        self.assertTrue(answer["target_stopped"])
        self.assertEqual(answer["waiting_for"], "verify_target_not_succeeded")

    def test_RD4_a_finished_team_without_its_files_has_no_report_and_says_so(self):
        mission_id = self.running()
        self.finish_team(mission_id, artifacts=False)
        answer = self.readiness(mission_id)
        self.assertFalse(answer["ready"])
        self.assertIsNone(answer["reported_result"])
        self.assertEqual(answer["waiting_for"], "verify_result_not_reported")

    def test_RD5_readiness_agrees_with_what_verify_then_decides(self):
        # The same conjuncts, the same first failure: never a different answer.
        for status, artifacts in (("COMPLETE", True), ("ERROR", True),
                                  ("COMPLETE", False), ("RUNNING", True)):
            with self.subTest(status=status, artifacts=artifacts):
                mission_id = self.running()
                self.finish_team(mission_id, status=status, artifacts=artifacts)
                self.satisfy_tests_pass(mission_id)
                answer = self.readiness(mission_id)
                if not (answer["ready"] or answer["target_stopped"]):
                    # Waiting: verify is NOT called (it would end the run).
                    self.assertEqual(answer["waiting_for"], "verify_target_not_stopped")
                    continue
                # Verify is handed exactly what readiness used, None included.
                result = self.bridge.verify(mission_id, answer["reported_result"])
                if answer["ready"]:
                    self.assertEqual(result["state"], "COMPLETED")
                else:
                    self.assertEqual(result["state"], "BLOCKED")
                    self.assertEqual(result["verification"]["failed_conjunct"],
                                     answer["waiting_for"])

    def test_RD6_only_a_running_unpaused_run_can_be_asked_about(self):
        approved = self.approved()
        self.refused(bridge_module.PROBLEM_WRONG_STATE, self.readiness, approved)
        mission_id = self.running()
        self.bridge.pause(mission_id)
        self.refused(bridge_module.PROBLEM_PAUSED, self.readiness, mission_id)

    def test_RD7_it_starts_and_records_nothing(self):
        mission_id = self.running()
        spawned = len(self.spawn.calls)
        self.finish_team(mission_id)
        self.readiness(mission_id)
        self.assertEqual(len(self.spawn.calls), spawned)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertIsNone(self.mission(mission_id)["run"].get("verification"))

    def test_RD8_the_answer_has_exactly_the_documented_shape(self):
        mission_id = self.running()
        self.assertEqual(sorted(self.readiness(mission_id)),
                         ["ready", "reported_result", "target_stopped",
                          "waiting_for"])


if __name__ == "__main__":
    unittest.main()
