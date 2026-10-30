"""WHICH refusal codes can the end-of-read re-check raise? (the sibling increment of round 29)

Round 29 found that four finished-Mission refusal sentences said "Nothing was read" although the same refusals can arise at the END-OF-READ re-check (``code_review._revalidated``), after the workspace was observed. The
Lead's rule for the sibling wording is: the fix follows REACHABILITY, not the pattern. Two older bodies in the webview model say "Nothing was read." and are backend codes:

- ``no_fresh_catalog_answer`` (``PROBLEM_NOT_CORROBORATED``): ``_corroborated`` raises it when there is no answer or the health is not fresh. ``_revalidated`` re-runs ``_corroborated``.
- ``mission_listed_more_than_once`` (``PROBLEM_MISSION_AMBIGUOUS``): ``_corroborated`` raises it when the held answer lists the Mission twice. Re-run the same way.

This module PROVES each is reachable at release, at the public ``read()`` boundary, by letting the first consultation pass and the second (the release-time one) fail. The observation is replaced by a stub with an empty
listing and no git (as in the Layer 1 LateRefusalTests); what is established is the ORDER (the observation ran, then the refusal arose, with no content), not that any content was read.

It also pins the CONTRAST between the two kinds of origin: the two office-side codes (``member_listed_more_than_once``, ``backend_discovery_unavailable``) are decided by the office BEFORE any backend read, so the backend
never produces them (neither is a backend problem code, ``code_review.PROBLEMS``): the PHASE finding (a refusal after the read) applies to the two backend codes only.

That is the phase axis alone. A LATER finding (round 30) is the REFERENT axis: a refusal is printed beside a retained overview, so "Nothing was read." is false of the screen for the office-side codes too, and the
sibling increment changed all four bodies. The per-body demonstration that each code arrives as a FILE refusal beside an observed overview is in tests/test_refusal_file_view_reach.py (the backend codes) and
integrations/pixel-agents/server/__tests__/diRefusalCompositeReach.test.ts (the office codes).

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


class LateRefusalReachabilityTests(unittest.TestCase):

    def read(self, freshness=None, listing=None):
        """A finished-Mission read whose FIRST consultation passes and whose SECOND (the release-time one) is altered by ``freshness`` or ``listing``."""
        observed = {"n": 0}
        calls = {"freshness": 0, "listing": 0}

        def stable(transport, path, bound, accounting):
            observed["n"] += 1
            return {"head": "a" * 40}, []

        def health(response, now):
            calls["freshness"] += 1
            if freshness is not None and calls["freshness"] > 1:
                return freshness
            return contract.HEALTH_FRESH, None

        with l1.WitnessedWorkspace() as ws:
            def provider(held, mission_ref):
                return (l1.proof(workspace=ws.path), 1, None)

            def corroboration():
                calls["listing"] += 1
                one = l1.mission(workspace=ws.path)
                return l1.answer([one, l1.mission(workspace=ws.path)] if listing and calls["listing"] > 1 else [one])
            real = code_review._observe_stable
            code_review._observe_stable = stable
            try:
                result = code_review.read(l1.FakeRegistry([l1.registration()]), corroboration, health, l1.clock, l1.SentinelTransport(), l1.REF, l1.TASK, finished=provider)
            finally:
                code_review._observe_stable = real
        return result, observed["n"], calls

    def assert_late_and_empty(self, result, observed, problem):
        self.assertEqual((result["state"], result["problem"]), ("refused", problem), result["detail"])
        self.assertEqual(observed, 1, "the observation ran BEFORE the refusal: it is a late refusal")
        for key in ("target", "comparison", "overview", "file"):
            self.assertIsNone(result[key], key)

    def test_the_release_check_can_raise_no_fresh_catalog_answer_after_the_observation(self):
        result, observed, calls = self.read(freshness=(contract.HEALTH_STALE, "the last answer is now stale"))
        self.assert_late_and_empty(result, observed, "no_fresh_catalog_answer")
        self.assertEqual(calls["freshness"], 2, "the freshness test ran at admission and again at release")

    def test_the_release_check_can_raise_mission_listed_more_than_once_after_the_observation(self):
        result, observed, calls = self.read(listing=True)
        self.assert_late_and_empty(result, observed, "mission_listed_more_than_once")
        self.assertEqual(calls["listing"], 2, "the held answer was read at admission and again at release")

    def test_an_unaltered_release_check_does_not_raise_either_code(self):
        """The control: the same stubs with nothing altered do NOT end in either code (the read goes on to the workspace observation the sentinel transport refuses to perform)."""
        try:
            result, _observed, _calls = self.read()
        except l1.Reached:
            return
        self.assertNotIn(result["problem"], ("no_fresh_catalog_answer", "mission_listed_more_than_once"))

    def test_the_office_side_bodies_name_codes_the_backend_never_produces(self):
        for office_only in ("member_listed_more_than_once", "backend_discovery_unavailable"):
            self.assertNotIn(office_only, code_review.PROBLEMS, "%s is decided by the office before any backend read" % office_only)
        for both in ("no_fresh_catalog_answer", "mission_listed_more_than_once"):
            self.assertIn(both, code_review.PROBLEMS)


class InheritedTestGuard(unittest.TestCase):
    def test_no_pre_existing_test_class_is_imported_into_this_module_by_name(self):
        collected = [name for name, value in sorted(vars(sys.modules[__name__]).items())
                     if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ != __name__]
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
