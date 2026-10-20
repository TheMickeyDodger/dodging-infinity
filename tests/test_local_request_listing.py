"""Tests for the surface's two READ-ONLY additions for a reconnecting panel:
``list_requests`` (enumerate the requests a surface holds) and
``binding_status`` (how a DISPLAYED proposal relates to Mission Core's
current one, at decision time).

Both drive the REAL surface over the REAL Mission Core in a temporary
protected directory. ``SYNTHETIC_AUTHENTICATED`` (from test_local_request)
only stands in for a future authenticated decision route acting directly on
Mission Core, to make a Mission edited or authorized without going through
the surface; it is not evidence of any live transport.

The point of both methods is that a window, a reload or a second window can
re-read what is OF RECORD instead of trusting what it last displayed:
neither writes, locks or creates anything, and neither returns a control
capability or any secret.
"""

import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import store as store_module  # noqa: E402
from local_request import surface as surface_module  # noqa: E402
from test_local_request import Fixture, request  # noqa: E402


class ListRequestsTests(Fixture):

    def test_L1_an_empty_surface_lists_nothing_and_creates_nothing(self):
        listed = self.surface.list_requests()
        self.assertEqual(listed["requests"], [])
        self.assertEqual(listed["status"], "listed")
        self.assertEqual(listed["delivery_authority"], "none")
        self.assertEqual(listed["dispatch"], "none")
        self.assertFalse(os.path.exists(self.state), "a read created state")

    def test_L2_it_is_read_only(self):
        self.surface.submit(request(objective="A"))
        before = (self.mission_bytes(), self.surface_bytes())
        self.surface.list_requests()
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)

    def test_L3_each_entry_carries_the_binding_the_panel_must_display(self):
        out = self.surface.submit(request(objective="A"))
        (entry,) = self.surface.list_requests()["requests"]
        self.assertEqual(entry["request_ref"], out["request_ref"])
        self.assertEqual(entry["mission_id"], out["mission_id"])
        self.assertEqual(entry["proposal_digest_sha256"],
                         out["proposal_digest_sha256"])
        self.assertEqual(entry["proposal"], out["proposal"])
        self.assertEqual(entry["surface_state"], store_module.STATE_OPEN)
        self.assertIsNone(entry["cancellation"])
        self.assertIsNone(entry["attested_approval"])
        self.assertEqual(entry["created_at"], self.clock())

    def test_L4_a_withdrawn_request_stays_listed_with_its_history_intact(self):
        out = self.surface.submit(request(objective="A"))
        self.surface.cancel(out["request_ref"], out["control_capability"])
        (entry,) = self.surface.list_requests()["requests"]
        self.assertEqual(entry["surface_state"], store_module.STATE_CANCELLED)
        self.assertEqual(entry["cancellation"], {
            "cancelled_at": self.clock(), "mission_id": out["mission_id"],
            "revision": 1,
            "proposal_digest_sha256": out["proposal_digest_sha256"]})

    def test_L5_a_replacement_is_a_separate_record_not_an_edit(self):
        first = self.surface.submit(request(objective="A"))
        self.surface.cancel(first["request_ref"], first["control_capability"])
        second = self.surface.submit(request(objective="A, revised"))
        listed = {e["request_ref"]: e
                  for e in self.surface.list_requests()["requests"]}
        self.assertEqual(set(listed), {first["request_ref"],
                                       second["request_ref"]})
        self.assertNotEqual(first["mission_id"], second["mission_id"])
        # Measured, not assumed: in this lane a replacement is a NEW Mission
        # at revision 1, never a higher revision of the withdrawn one.
        self.assertEqual(second["revision"], 1)
        self.assertEqual(listed[first["request_ref"]]["surface_state"],
                         store_module.STATE_CANCELLED)
        self.assertEqual(listed[second["request_ref"]]["surface_state"],
                         store_module.STATE_OPEN)

    def test_L6_an_attested_approval_is_listed_with_its_honest_label(self):
        out = self.surface.submit(request(objective="A"))
        presented = self.surface.present(out["request_ref"])
        self.surface.attest_approval(
            out["request_ref"], presented["mission_id"], presented["revision"],
            presented["proposal_digest_sha256"],
            presented["approved_action_scope"],
            presented["approved_delivery_targets"], self.clock() + 600,
            "approved", "synthetic-relay")
        (entry,) = self.surface.list_requests()["requests"]
        self.assertEqual(entry["attested_approval"]["state"], "APPLIED")
        self.assertEqual(entry["attested_approval"]["provenance_label"], {
            "principal_kind": "operator_attested_relay",
            "proof": "operator_attested_not_independently_verified"})

    def test_L7_no_control_capability_or_key_digest_is_ever_listed(self):
        out = self.surface.submit(request(objective="A"))
        text = json.dumps(self.surface.list_requests(), sort_keys=True)
        self.assertNotIn(out["control_capability"], text)
        self.assertNotIn(store_module.token_digest(out["control_capability"]),
                         text)
        self.assertNotIn("control_capabilities", text)
        self.assertNotIn(store_module.TOKEN_PREFIX, text)

    def test_L8_newest_first_and_bounded_by_the_limit(self):
        refs = []
        for index in range(4):
            self.clock.now += 10
            refs.append(self.surface.submit(
                request(objective="request %d" % index))["request_ref"])
        listed = self.surface.list_requests()["requests"]
        self.assertEqual([e["request_ref"] for e in listed], refs[::-1])
        limited = self.surface.list_requests(limit=2)
        self.assertEqual([e["request_ref"] for e in limited["requests"]],
                         refs[::-1][:2])
        self.assertEqual(limited["total"], 4)
        self.assertTrue(limited["truncated"])
        self.assertFalse(self.surface.list_requests()["truncated"])

    def test_L9_a_bad_limit_is_refused_before_any_read(self):
        for bad in (0, -1, True, "3", 1.5,
                    store_module.MAX_REQUEST_RECORDS + 1):
            with self.subTest(limit=bad):
                self.refused("local_request_bad_request",
                             lambda: self.surface.list_requests(limit=bad))

    def test_L10_a_second_process_lists_exactly_the_same_records(self):
        self.surface.submit(request(objective="A"))
        self.assertEqual(self.fresh_surface().list_requests(),
                         self.surface.list_requests())


class BindingStatusTests(Fixture):

    def displayed(self, out, **changes):
        shown = dict(request_ref=out["request_ref"],
                     mission_id=out["mission_id"], revision=out["revision"],
                     proposal_digest_sha256=out["proposal_digest_sha256"])
        shown.update(changes)
        return shown

    def binding(self, out, **changes):
        return self.surface.binding_status(**self.displayed(out, **changes))

    def test_B1_the_displayed_proposal_matches_the_current_one(self):
        out = self.surface.submit(request(objective="A"))
        result = self.binding(out)
        self.assertEqual(result["binding"], "matches_current_revision")
        self.assertEqual(result["status"], "binding")
        self.assertEqual(result["delivery_authority"], "none")

    def test_B2_it_is_read_only(self):
        out = self.surface.submit(request(objective="A"))
        before = (self.mission_bytes(), self.surface_bytes())
        self.binding(out)
        self.binding(out, revision=2)
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)

    def test_B3_an_edited_mission_makes_the_old_display_stale(self):
        out = self.surface.submit(request(objective="A"))
        self.authenticated_edit(out["mission_id"], 1, objective="A, edited")
        self.assertEqual(self.binding(out)["binding"], "stale_revision")

    def test_B4_a_different_digest_is_a_mismatch_not_a_match(self):
        out = self.surface.submit(request(objective="A"))
        self.assertEqual(
            self.binding(out, proposal_digest_sha256="a" * 64)["binding"],
            "proposal_digest_mismatch")

    def test_B5_a_decided_mission_is_not_pending(self):
        out = self.surface.submit(request(objective="A"))
        self.authenticated_approve(out["mission_id"], 1)
        self.assertEqual(self.binding(out)["binding"], "mission_not_pending")

    def test_B6_an_expired_window_is_reported_expired(self):
        out = self.surface.submit(request(objective="A"))
        result = self.binding(out, expires_at=self.clock() - 1)
        self.assertEqual(result["binding"], "expired")
        self.assertEqual(
            self.binding(out, expires_at=self.clock() + 60)["binding"],
            "matches_current_revision")

    def test_B7_a_withdrawn_request_is_refused_not_classified(self):
        out = self.surface.submit(request(objective="A"))
        self.surface.cancel(out["request_ref"], out["control_capability"])
        self.refused("local_request_cancelled",
                     lambda: self.binding(out))

    def test_B8_a_display_for_another_mission_is_refused(self):
        a = self.surface.submit(request(objective="A"))
        b = self.surface.submit(request(objective="B"))
        self.refused("local_request_misattributed",
                     lambda: self.binding(a, mission_id=b["mission_id"]))

    def test_B9_an_unknown_reference_is_refused(self):
        out = self.surface.submit(request(objective="A"))
        self.refused("local_request_unknown_request",
                     lambda: self.binding(out, request_ref="lr-" + "0" * 32))

    def test_B10_malformed_inputs_are_refused_before_any_read(self):
        out = self.surface.submit(request(objective="A"))
        for changes in (dict(revision=0), dict(revision=True),
                        dict(revision="1"),
                        dict(proposal_digest_sha256="A" * 64),
                        dict(proposal_digest_sha256="a" * 63),
                        dict(expires_at=-1), dict(expires_at=True),
                        dict(mission_id=None), dict(mission_id=7)):
            with self.subTest(changes=changes):
                with self.assertRaises(
                        surface_module.LocalRequestRefusal) as caught:
                    self.binding(out, **changes)
                self.assertIn(caught.exception.problem, (
                    "local_request_bad_request",
                    "local_request_ambiguous_reference"))

    def test_B11_an_incomplete_cancellation_is_not_called_a_match(self):
        out = self.surface.submit(request(objective="A"))
        # Mission Core carries the withdrawal marker, the local save never
        # landed: the request must not read as a match for a decision.
        self.missions.withdraw_proposal(
            out["mission_id"], self.surface._store.load()["requests"][
                out["request_ref"]]["mission_request_id"],
            out["control_capability"], surface_module.LOCAL_CALLER_CONTEXT)
        self.refused("local_request_cancellation_incomplete",
                     lambda: self.binding(out))


if __name__ == "__main__":
    unittest.main()
