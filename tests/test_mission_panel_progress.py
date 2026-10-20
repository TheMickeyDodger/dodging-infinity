"""Tests for ``mission_panel.progress``: the progression pass, the status file it
publishes, and the liveness probe the panel reads.

What the pass is: for the requests THIS panel durably submitted (its journal's
SUBMITTED submissions, and nothing else), and only those that are approved and
not withdrawn, it calls ``MissionBridge.advance`` and reports what came back. It
starts nothing (``advance`` never dispatches), decides nothing itself, and a
Mission it did not enumerate is not read, written or reported.

These run the REAL journal, the REAL local_request surface over the REAL Mission
Core, and the REAL bridge with the bridge suite's scripted observer, spawn
recorder and fake transport. Nothing here is evidence of a live Herdr.
"""

import json
import os
import stat
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import surface as surface_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import progress as progress_module  # noqa: E402
from target_runtime import mission_progress_status as status_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    Fixture, TASK_ID, raw_observation, run_request,
)


class PassFixture(Fixture):
    def setUp(self):
        super(PassFixture, self).setUp()
        self.journal = journal_module.PanelJournal(self.state)
        self.progress = progress_module.ProgressionPass(
            self.journal, self.surface, self.bridge)

    def request_ref(self, mission_id):
        [ref] = [e["request_ref"] for e in self.surface.list_requests()["requests"]
                 if e["mission_id"] == mission_id]
        return ref

    def link(self, request_ref):
        """The panel durably SUBMITTED this request: what its journal records."""
        proposal = mission_record.validate_proposal(run_request())
        turn = self.journal.reserve_turn(None, "ask", "gw", self.clock(), set())
        self.journal.finish_turn(
            turn["turn_id"], journal_module.TURN_COMPLETED, "draft",
            {"status": "completed", "error_code": None, "detail": None,
             "unrecognized_event_lines": 0},
            {"proposal": proposal,
             "proposal_digest_sha256": mission_record.proposal_digest(proposal)},
            None, self.clock())
        self.journal.reserve_submission(
            turn["turn_id"], mission_record.proposal_digest(proposal), None, self.clock())
        self.journal.finish_submission(
            turn["turn_id"], journal_module.SUBMISSION_SUBMITTED, request_ref, None,
            self.clock())

    def linked(self, started=False):
        mission_id = self.approved()
        ref = self.request_ref(mission_id)
        self.link(ref)
        if started:
            self.dispatch(mission_id)
        return mission_id, ref

    def finish_team(self, mission_id):
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")

    def entry(self, report, mission_id):
        [found] = [m for m in report["missions"] if m["mission_id"] == mission_id]
        return found


class PassTests(PassFixture):

    def test_PR1_a_started_panel_request_runs_to_a_verified_result_with_one_spawn(self):
        mission_id, _ = self.linked(started=True)
        first = self.progress.run_pass()
        self.assertEqual(self.entry(first, mission_id)["action"], "observe")
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        working = self.progress.run_pass()
        self.assertEqual(self.entry(working, mission_id)["action"], "wait")
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        done = self.progress.run_pass()
        self.assertEqual(self.entry(done, mission_id)["action"], "verify")
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        self.assertTrue(self.bridge.result(mission_id)["verified_result"])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_PR2_an_approved_unstarted_panel_request_is_never_started(self):
        mission_id, _ = self.linked(started=False)
        before = self.mission_bytes()
        for _ in range(4):
            report = self.progress.run_pass()
            self.assertEqual(self.entry(report, mission_id)["action"], "none")
        self.assertEqual(self.spawn.calls, [])
        self.assertEqual(self.mission_bytes(), before)

    def test_PR3_a_request_this_panel_did_not_submit_is_never_touched(self):
        # Started and finished, but submitted through another surface: its ref
        # is in the local_request store and Mission Core, and NOT in the journal.
        stranger = self.approved()
        self.dispatch(stranger)
        self.observer.raw = raw_observation()
        self.bridge.observe(stranger)
        self.finish_team(stranger)
        self.satisfy_tests_pass(stranger)
        before = self.mission_bytes()
        mission_id, _ = self.linked(started=True)
        report = self.progress.run_pass()
        self.assertNotIn(stranger, [m["mission_id"] for m in report["missions"]])
        self.assertEqual(self.mission(stranger)["state"], "RUNNING",
                         "an unrelated Mission was advanced")
        self.assertIsNone(self.mission(stranger)["run"].get("verification"))
        self.assertIn(mission_id, [m["mission_id"] for m in report["missions"]])
        self.assertNotEqual(before, self.mission_bytes())     # only the linked one moved

    def test_PR4_a_mission_core_mission_with_no_request_at_all_is_not_enumerated(self):
        # Proposed straight into Mission Core: no local_request record, no journal.
        raw = mission_record.validate_proposal(run_request())
        request_id = self.missions.mint_request_id(self.bridge._context)
        self.missions.propose(request_id, raw, self.bridge._context)
        mission_id, _ = self.linked(started=True)
        report = self.progress.run_pass()
        self.assertEqual([m["mission_id"] for m in report["missions"]], [mission_id])

    def test_PR5_only_approved_unwithdrawn_requests_are_eligible(self):
        approved, _ = self.linked(started=False)
        open_ = self.surface.submit(run_request())               # proposed, never approved
        withdrawn = self.surface.submit(run_request())
        self.surface.cancel(withdrawn["request_ref"], withdrawn["control_capability"])
        self.link(open_["request_ref"])
        self.link(withdrawn["request_ref"])
        report = self.progress.run_pass()
        self.assertEqual(report["eligible"], 1)
        self.assertEqual([m["mission_id"] for m in report["missions"]], [approved])

    def test_PR6_a_linked_ref_the_store_does_not_hold_is_counted_not_hidden(self):
        mission_id, _ = self.linked(started=True)
        self.link("lr-" + "0" * 32)                       # the journal names it; the store has none
        report = self.progress.run_pass()
        self.assertEqual(report["unknown_requests"], 1)
        self.assertEqual([m["mission_id"] for m in report["missions"]], [mission_id])

    def test_PR7_each_pass_is_bounded_and_every_mission_is_reached_in_turn(self):
        original = progress_module.MAX_PASS_MISSIONS
        progress_module.MAX_PASS_MISSIONS = 2
        self.addCleanup(setattr, progress_module, "MAX_PASS_MISSIONS", original)
        ids = [self.linked(started=True)[0] for _ in range(5)]
        seen = []
        for _ in range(3):
            report = self.progress.run_pass()
            self.assertLessEqual(len(report["missions"]), 2)
            self.assertEqual(report["eligible"], 5)
            self.assertTrue(report["truncated"], "a bounded pass hid that it was bounded")
            seen.extend(m["mission_id"] for m in report["missions"])
        self.assertEqual(set(seen), set(ids), "a Mission was starved by the bound")

    def test_PR8_a_finished_mission_is_not_read_again(self):
        mission_id, _ = self.linked(started=True)
        self.progress.run_pass()
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.progress.run_pass()
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        calls = []
        real = self.bridge.advance
        self.bridge.advance = lambda m: (calls.append(m), real(m))[1]
        report = self.progress.run_pass()
        self.progress.run_pass()
        self.assertEqual(calls, [], "a terminal Mission was advanced again")
        self.assertEqual(report["missions"], [])

    def test_PR9_one_failing_mission_does_not_stop_the_others(self):
        broken, _ = self.linked(started=True)
        fine, _ = self.linked(started=True)
        real = self.bridge.advance

        def advance(mission_id):
            if mission_id == broken:
                raise RuntimeError("synthetic: /private/path/that/must/not/leak")
            return real(mission_id)
        self.bridge.advance = advance
        report = self.progress.run_pass()
        failed = self.entry(report, broken)
        self.assertEqual(failed["action"], "unreadable")
        self.assertIn("RuntimeError", failed["detail"])
        self.assertNotIn("private", failed["detail"])
        self.assertEqual(self.entry(report, fine)["action"], "observe")

    def test_PR10_a_refusal_is_reported_as_one(self):
        mission_id, _ = self.linked(started=True)

        def advance(_):
            raise bridge_module.MissionBridgeRefusal(
                bridge_module.PROBLEM_MISSION_CORE, "synthetic refusal")
        self.bridge.advance = advance
        report = self.progress.run_pass()
        entry = self.entry(report, mission_id)
        self.assertEqual(entry["action"], "refused")
        self.assertEqual(entry["waiting_for"], bridge_module.PROBLEM_MISSION_CORE)

    def test_PR11_a_restarted_pass_continues_from_the_durable_records(self):
        mission_id, _ = self.linked(started=True)
        self.progress.run_pass()
        restarted = progress_module.ProgressionPass(
            self.journal, self.surface, self.make_bridge())
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        report = restarted.run_pass()
        self.assertEqual(self.entry(report, mission_id)["action"], "verify")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_PR12_a_mission_another_process_holds_is_reported_busy(self):
        from target_runtime import mission_run_guard as guard_module
        guard = guard_module.RunGuard(self.state, wait_seconds=0.2)
        bridge = self.make_bridge()
        bridge._run_guard = guard
        pass_ = progress_module.ProgressionPass(
            self.journal, self.surface, bridge)
        mission_id, _ = self.linked(started=True)
        with guard.hold(mission_id) as held:
            self.assertTrue(held)
            report = pass_.run_pass()
        self.assertEqual(self.entry(report, mission_id)["action"], "busy")
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_PR13_a_pass_reports_exactly_the_documented_shape(self):
        mission_id, _ = self.linked(started=True)
        report = self.progress.run_pass()
        self.assertEqual(sorted(report),
                         ["eligible", "examined", "missions", "truncated",
                          "unknown_requests"])
        self.assertEqual(sorted(report["missions"][0]),
                         ["action", "detail", "mission_id", "request_ref", "state",
                          "waiting_for"])


class StatusTests(PassFixture):

    def setUp(self):
        super(StatusTests, self).setUp()
        os.makedirs(self.state, exist_ok=True)
        self.digest = status_module.composition_digest(
            self.state, "/control", self.repository, self.root)

    def write(self, **changes):
        arguments = dict(
            state_directory=self.state, digest=self.digest, pid=os.getpid(),
            started_at=self.clock() - 10, passes=3, interval_seconds=5,
            report={"examined": 1, "eligible": 1, "truncated": False,
                    "unknown_requests": 0, "missions": []},
            stopping=False, now=self.clock())
        arguments.update(changes)
        status_module.write_status(**arguments)

    def probe(self, **changes):
        arguments = dict(state_directory=self.state, digest=self.digest,
                         now=self.clock())
        arguments.update(changes)
        return status_module.probe(**arguments)

    def status_path(self):
        return os.path.join(self.state, status_module.STATUS_FILE_NAME)

    def test_ST1_a_fresh_status_of_a_live_runtime_is_running(self):
        self.write()
        answer = self.probe()
        self.assertEqual(answer["state"], "running", answer)
        self.assertEqual(answer["passes"], 3)

    def test_ST2_no_status_means_stopped_and_says_so(self):
        answer = self.probe()
        self.assertEqual(answer["state"], "stopped")
        self.assertFalse(os.path.exists(self.status_path()), "a read created the file")

    def test_ST3_a_status_from_a_runtime_that_is_gone_is_stopped(self):
        self.write(pid=2_000_000_000)
        self.assertEqual(self.probe()["state"], "stopped")

    def test_ST4_a_runtime_that_stopped_cleanly_says_so(self):
        self.write(stopping=True)
        self.assertEqual(self.probe()["state"], "stopped")

    def test_ST5_a_runtime_that_stopped_passing_is_stale_not_running(self):
        self.write()
        later = self.clock() + status_module.STATUS_STALE_SECONDS + 1
        answer = self.probe(now=later)
        self.assertEqual(answer["state"], "stale")
        self.assertGreater(answer["last_pass_age_seconds"],
                           status_module.STATUS_STALE_SECONDS)

    def test_ST6_a_runtime_composed_from_other_paths_is_a_mismatch(self):
        other = status_module.composition_digest(
            self.state, "/other-control", self.repository, self.root)
        self.write(digest=other)
        answer = self.probe()
        self.assertEqual(answer["state"], "mismatch")
        self.assertNotIn("/other-control", json.dumps(answer))

    def test_ST7_an_unreadable_status_is_unreadable_never_running(self):
        for payload in (b"not json", b"[]", b"{}", b"x" * (status_module.MAX_STATUS_BYTES + 1),
                        json.dumps({"schema": 99}).encode()):
            with self.subTest(payload=payload[:20]):
                with open(self.status_path(), "wb") as handle:
                    handle.write(payload)
                self.assertEqual(self.probe()["state"], "unreadable")

    def test_ST8_a_symlinked_status_is_not_followed(self):
        target = os.path.join(self.tmp.name, "elsewhere.json")
        self.write()
        os.replace(self.status_path(), target)
        os.symlink(target, self.status_path())
        self.assertEqual(self.probe()["state"], "unreadable")

    def test_ST9b_a_pass_that_failed_as_a_whole_is_failing_not_running(self):
        self.write(problem="JournalError")
        answer = self.probe()
        self.assertEqual(answer["state"], "failing", answer)
        self.assertIn("JournalError", answer["detail"])
        self.assertIn("nothing is being advanced", answer["detail"])
        self.write(problem=None)
        self.assertEqual(self.probe()["state"], "running")

    def test_ST9c_the_problem_field_is_bounded_and_text_only(self):
        self.write(problem="X" * 500)
        with open(self.status_path(), encoding="utf-8") as handle:
            self.assertEqual(len(json.load(handle)["problem"]),
                             status_module.MAX_PROBLEM_CHARS)
        document = json.load(open(self.status_path(), encoding="utf-8"))
        document["problem"] = 7
        with open(self.status_path(), "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        self.assertEqual(self.probe()["state"], "unreadable")

    def test_ST9_the_status_is_private_and_carries_no_path_or_secret(self):
        self.write()
        mode = stat.S_IMODE(os.stat(self.status_path()).st_mode)
        self.assertEqual(mode, 0o600)
        text = open(self.status_path(), encoding="utf-8").read()
        for forbidden in (self.state, self.repository, self.root, "token", "Bearer"):
            self.assertNotIn(forbidden, text)

    def test_ST10_a_write_replaces_the_file_whole_and_leaves_no_temp_file(self):
        self.write(passes=1)
        self.write(passes=2)
        self.assertEqual(self.probe()["passes"], 2)
        leftovers = [n for n in os.listdir(self.state) if "tmp" in n]
        self.assertEqual(leftovers, [])

    def test_ST11_the_digest_changes_with_each_composed_path(self):
        base = ("/state", "/control", "/repo", "/roots")
        seen = {status_module.composition_digest(*base)}
        for index in range(4):
            changed = list(base)
            changed[index] = changed[index] + "-x"
            seen.add(status_module.composition_digest(*changed))
        self.assertEqual(len(seen), 5)
        self.assertEqual(status_module.composition_digest(*base),
                         status_module.composition_digest(*base))

    def test_ST12_the_missions_listed_are_bounded(self):
        many = [{"mission_id": "mn-%032x" % n, "request_ref": "lr-%032x" % n,
                 "state": "RUNNING", "action": "wait", "waiting_for": None,
                 "detail": "x"} for n in range(status_module.MAX_STATUS_MISSIONS + 20)]
        self.write(report={"examined": len(many), "eligible": len(many),
                           "truncated": False, "unknown_requests": 0, "missions": many})
        with open(self.status_path(), encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertLessEqual(len(written["missions"]), status_module.MAX_STATUS_MISSIONS)

    def test_ST13_the_bounds_are_the_documented_ones(self):
        self.assertEqual(progress_module.MAX_PASS_MISSIONS, 32)
        self.assertEqual(status_module.MAX_STATUS_MISSIONS, 32)
        self.assertEqual(status_module.MAX_PROBLEM_CHARS, 80)
        self.assertEqual(status_module.STATUS_STALE_SECONDS, 30)
        self.assertEqual(status_module.MAX_STATUS_BYTES, 65536)
        self.assertEqual(status_module.MAX_REPORT_DETAIL_CHARS, 300)


if __name__ == "__main__":
    unittest.main()
