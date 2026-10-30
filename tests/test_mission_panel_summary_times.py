"""The Mission request panel's OVERVIEW summary carries the two action times its per-request view already holds (R12, the panel-requests half).

``PanelService._summary`` builds one entry of the overview from ``_view``. ``_view`` already computes ``attestation["attested_at"]`` (when an approval was RECORDED) and ``cancellation["cancelled_at"]`` (when a withdrawal was
recorded), but the summary dropped them, so a page that reads only the overview (the Reception) could not tell which requests had a recorded action today. The change is ADDITIVE and over EXISTING facts: two keys, ``attested_at`` and
``cancelled_at``, each None when the view holds none. They record THAT an action was recorded and WHEN; they do not say who performed it, and nothing here adds a handled state, an outcome or an action.

Hermetic: ``_summary`` is exercised on an instance built without its collaborators, with ``_view`` and the surface replaced by stand-ins, so the test pins exactly the mapping from the view to the summary. It does not
exercise the real request surface, a store or any git. (The real adapter's own end-to-end walk is the vitest ``diMissionRequests.test.ts``; its early portion, through the recorded decision, is not run by this module.)
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mission_panel import service as service_module  # noqa: E402

REF = "lr-" + "a" * 32


def view(attestation=None, cancellation=None):
    return {
        "ok": True, "request_ref": REF, "mission_id": "mn-" + "b" * 32, "revision": 2, "created_at": 1_800_000_000,
        "proposal": {"objective": "make the readiness probe stable"}, "proposal_digest_sha256": "c" * 64,
        "lifecycle": {"stage": "approved", "label": "Approved by operator attestation", "terminal": False, "actions": [], "notice": None, "extra": "not copied"},
        "lineage": [], "cancellation": cancellation, "attestation": attestation, "run": None, "start_cause": None, "delivery_authority": "none",
    }


class Surface(object):
    def status(self, request_ref):
        return {}


def summary_of(shown):
    instance = object.__new__(service_module.PanelService)
    instance._surface = Surface()
    instance._view = lambda entry, status=None, with_start_cause=False: shown
    return instance._summary({"request_ref": REF, "proposal": {"objective": "unused"}})


class SummaryTimesTests(unittest.TestCase):

    def test_an_approval_that_was_recorded_carries_its_time(self):
        shown = summary_of(view(attestation={"state": "attested", "attested_at": 1_800_000_500}))
        self.assertEqual((shown["attested_at"], shown["cancelled_at"]), (1_800_000_500, None))

    def test_a_withdrawal_that_was_recorded_carries_its_time(self):
        shown = summary_of(view(cancellation={"cancelled_at": 1_800_000_900, "reason": "withdrawn"}))
        self.assertEqual((shown["attested_at"], shown["cancelled_at"]), (None, 1_800_000_900))

    def test_a_request_with_neither_carries_none_for_both_and_never_a_made_up_time(self):
        shown = summary_of(view())
        self.assertEqual((shown["attested_at"], shown["cancelled_at"]), (None, None))

    def test_an_attestation_without_a_time_or_a_cancellation_without_one_is_none_not_an_error(self):
        shown = summary_of(view(attestation={"state": "attested"}, cancellation={"reason": "withdrawn"}))
        self.assertEqual((shown["attested_at"], shown["cancelled_at"]), (None, None))

    def test_both_times_can_be_present_and_are_kept_apart(self):
        shown = summary_of(view(attestation={"attested_at": 10}, cancellation={"cancelled_at": 20}))
        self.assertEqual((shown["attested_at"], shown["cancelled_at"]), (10, 20))

    def test_the_summary_gains_exactly_these_two_keys_and_invents_no_handled_state(self):
        shown = summary_of(view())
        self.assertEqual(sorted(shown), sorted(["request_ref", "mission_id", "revision", "created_at", "objective", "proposal_digest_sha256", "lifecycle", "attested_at", "cancelled_at"]))
        self.assertEqual(sorted(shown["lifecycle"]), sorted(["stage", "label", "terminal", "actions", "notice"]))
        for invented in ("handled", "handled_at", "outcome", "decided_by", "attested_by", "approved_by"):
            self.assertNotIn(invented, shown)


if __name__ == "__main__":
    unittest.main()
