"""Can the BACKEND refuse a FILE request, at admission, after an earlier request on the same owner got past it? (per-body reachability for the sibling increment)

The shape that matters to the screen is composite: a successful overview is drawn, the reader selects a file, and THAT request is refused. For the two backend-origin codes ("no_fresh_catalog_answer", "mission_listed_more_than_once")
the question is whether the backend's own admission can refuse the file request. ``code_review._read`` consults the owner's held answer at the START of EVERY request (``_corroborated(corroboration(), freshness, ...)``), the
overview and the file alike, so the answer is yes by construction; this module DEMONSTRATES it rather than arguing it: two ``read()`` calls on one owner, the first of which gets past admission (it goes on to the workspace
observation, which the sentinel transport refuses to perform), the second a FILE request refused at admission by the code, with view ``file`` and no content.

The two OFFICE-origin codes ("member_listed_more_than_once", "backend_discovery_unavailable") are not backend codes and are demonstrated on the office side (server/__tests__/diRefusalCompositeReach.test.ts).

Hermetic: product modules and the Layer 1 module's fixtures only (a MODULE import, no test class imported by name); no git, no network.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_finished_code_review as l1  # noqa: E402  (a MODULE import for its fixtures)
from mission_sources import contract  # noqa: E402
from target_runtime import code_review  # noqa: E402

SNAPSHOT = "b" * 64
FILE_ID = "rf-" + "a" * 32


class FileViewAdmissionTests(unittest.TestCase):

    def two_requests(self, stale_second=False, doubled_second=False):
        """Request 1: the overview. Request 2: a file of the snapshot request 1 would have returned. The owner's held answer is altered ONLY for request 2 (its second consultation)."""
        consultations = {"freshness": 0, "listing": 0}
        observed = {"n": 0}

        def stable(transport, path, bound, accounting):
            observed["n"] += 1
            return {"head": "a" * 40}, []

        def health(response, now):
            consultations["freshness"] += 1
            if stale_second and consultations["freshness"] > 2:  # consultations 1 and 2 are request 1's admission and its release-time re-check
                return contract.HEALTH_STALE, "the last answer is now stale"
            return contract.HEALTH_FRESH, None

        with l1.WitnessedWorkspace() as ws:
            def provider(held, mission_ref):
                return (l1.proof(workspace=ws.path), 1, None)

            def corroboration():
                consultations["listing"] += 1
                one = l1.mission(workspace=ws.path)
                if doubled_second and consultations["listing"] > 2:
                    return l1.answer([one, l1.mission(workspace=ws.path)])
                return l1.answer([one])

            registry = l1.FakeRegistry([l1.registration()])
            real = code_review._observe_stable
            code_review._observe_stable = stable
            try:
                first_reached = None
                try:
                    first_reached = code_review.read(registry, corroboration, health, l1.clock, l1.SentinelTransport(), l1.REF, l1.TASK, finished=provider)
                except l1.Reached:
                    first_reached = "reached the workspace"
                after_first = observed["n"]
                second = code_review.read(registry, corroboration, health, l1.clock, l1.SentinelTransport(), l1.REF, l1.TASK,
                                          snapshot=SNAPSHOT, file_id=FILE_ID, finished=provider)
            finally:
                code_review._observe_stable = real
        return first_reached, after_first, second, observed["n"], consultations

    def assert_file_request_refused_at_admission(self, outcome, problem):
        first, after_first, second, observed_after, consultations = outcome
        # request 1 (the overview) got past admission: the workspace observation RAN once. (How it ends afterwards is not the point: the test's observation is a stub and its transport a sentinel.)
        self.assertEqual(after_first, 1, "request 1 did not get past admission: %r" % (first,))
        if isinstance(first, dict):
            self.assertNotIn(first["problem"], ("no_fresh_catalog_answer", "mission_listed_more_than_once"), "request 1 must not be refused by the code under test")
        self.assertEqual((second["state"], second["problem"], second["view"]), ("refused", problem, "file"), second["detail"])
        for key in ("target", "comparison", "overview", "file"):
            self.assertIsNone(second[key], key)
        self.assertEqual(observed_after, after_first, "request 2 observed nothing: it was refused before the workspace was asked")
        self.assertEqual((consultations["freshness"], consultations["listing"]), (3, 3),
                         "request 1 consulted the owner's held answer at admission and again at release (2), request 2 at admission (the 3rd) and was refused there")

    def test_a_file_request_can_be_refused_no_fresh_catalog_answer_after_an_overview_got_past_admission(self):
        self.assert_file_request_refused_at_admission(self.two_requests(stale_second=True), "no_fresh_catalog_answer")

    def test_a_file_request_can_be_refused_mission_listed_more_than_once_after_an_overview_got_past_admission(self):
        self.assert_file_request_refused_at_admission(self.two_requests(doubled_second=True), "mission_listed_more_than_once")

    def test_the_control_with_nothing_altered_does_not_refuse_the_file_request_at_admission(self):
        """The same two requests with the owner unaltered: request 2 gets past admission too (whatever stops it afterwards, it is not the code under test, and the observation ran for both requests)."""
        try:
            _first, _after_first, second, observed_after, _consultations = self.two_requests()
        except l1.Reached:
            return  # request 2 reached the workspace: it was not refused at admission
        self.assertNotIn(second["problem"], ("no_fresh_catalog_answer", "mission_listed_more_than_once"))
        self.assertEqual(observed_after, 2, "the observation ran for BOTH requests: request 2 was not refused at admission")


class InheritedTestGuard(unittest.TestCase):
    def test_no_pre_existing_test_class_is_imported_into_this_module_by_name(self):
        collected = [name for name, value in sorted(vars(sys.modules[__name__]).items())
                     if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ != __name__]
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
