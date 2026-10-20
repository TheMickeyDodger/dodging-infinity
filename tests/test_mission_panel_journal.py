"""Tests for the mission panel's durable journal (``mission_panel.journal``).

The journal is BOOKKEEPING for the DI World request panel and nothing more:
it records that a provider turn was INTENDED before the provider was called,
how that turn ended (or that its end is UNKNOWN), the draft proposal a turn
yielded, and that a draft was submitted. It holds NO authority, NO control
capability and NO secret, and nothing in it is the source of truth for a
proposal, a decision or a run: Mission Core and the local request surface
are. Every test runs the REAL journal over a real temporary protected
directory.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from test_local_request import Bounded, request  # noqa: E402

NOW = 2_000_000


def clean_proposal(**overrides):
    return mission_record.validate_proposal(request(**overrides))


class Fixture(Bounded):

    def setUp(self):
        super(Fixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, "state")
        self.journal = journal_module.PanelJournal(self.directory)

    def path(self):
        return self.journal.path

    def raw(self):
        with open(self.path(), "rb") as handle:
            return handle.read()

    def refused(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(journal_module.JournalError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, str(caught.exception))
        return caught.exception

    def finished_turn(self, text="make the probe stable", **overrides):
        turn = self.journal.reserve_turn(None, text, "gw-1", NOW, set())
        proposal = clean_proposal()
        fields = dict(
            state=journal_module.TURN_COMPLETED, assistant_text="Here is a draft.",
            outcome={"status": "completed", "error_code": None, "detail": None,
                     "unrecognized_event_lines": 0},
            draft={"proposal": proposal,
                   "proposal_digest_sha256": mission_record.proposal_digest(proposal)},
            draft_problem=None)
        fields.update(overrides)
        return self.journal.finish_turn(turn["turn_id"], now=NOW + 5, **fields)


class StorageTests(Fixture):

    def test_J1_reading_an_empty_journal_creates_nothing(self):
        self.assertEqual(self.journal.load()["turns"], {})
        self.assertFalse(os.path.exists(self.directory))

    def test_J2_the_file_is_owner_only_and_round_trips(self):
        turn = self.journal.reserve_turn(None, "hello", "gw-1", NOW, set())
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)
        again = journal_module.PanelJournal(self.directory)
        self.assertEqual(again.turn(turn["turn_id"]), turn)

    def test_J3_an_accessible_directory_is_refused_before_any_read(self):
        os.makedirs(self.directory, mode=0o755)
        os.chmod(self.directory, 0o755)
        self.refused(journal_module.PROBLEM_STORE_UNREADABLE, self.journal.load)

    def test_J4_an_accessible_file_is_refused(self):
        self.journal.reserve_turn(None, "hello", "gw-1", NOW, set())
        os.chmod(self.path(), 0o644)
        self.refused(journal_module.PROBLEM_STORE_UNREADABLE, self.journal.load)

    def test_J5_tampering_fails_closed(self):
        turn = self.finished_turn()
        document = json.loads(self.raw())
        record = document["turns"][turn["turn_id"]]
        tampers = {
            "unknown key": lambda r: r.update(approved=True),
            "bad state": lambda r: r.update(state="APPROVED"),
            "bad id": lambda r: r.update(turn_id="ct-nope"),
            "oversize text": lambda r: r.update(
                user_text="x" * (journal_module.MAX_TURN_TEXT_CHARS + 1)),
            "draft not a proposal": lambda r: r["draft"].update(
                proposal={"objective": "x"}),
            "draft digest forged": lambda r: r["draft"].update(
                proposal_digest_sha256="a" * 64),
            "draft on a refused turn": lambda r: r.update(
                state=journal_module.TURN_REFUSED),
        }
        for name, tamper in tampers.items():
            with self.subTest(tamper=name):
                doc = json.loads(json.dumps(document))
                tamper(doc["turns"][turn["turn_id"]])
                with open(self.path(), "w") as handle:
                    json.dump(doc, handle)
                os.chmod(self.path(), 0o600)
                with self.assertRaises(journal_module.JournalError):
                    self.journal.load()
        del record

    def test_J6_the_journal_has_no_place_for_a_capability_or_a_decision(self):
        keys = set(journal_module.TURN_KEYS) | set(journal_module.SUBMISSION_KEYS)
        for forbidden in ("control_capability", "capability", "token", "approval",
                          "decision", "authorization", "approved"):
            self.assertFalse([k for k in keys if forbidden in k], forbidden)


class TurnTests(Fixture):

    def test_T1_the_intent_is_durable_before_any_provider_call(self):
        turn = self.journal.reserve_turn(None, "make the probe stable", "gw-1", NOW, set())
        self.assertEqual(turn["state"], journal_module.TURN_RESERVED)
        self.assertEqual(turn["sequence"], 1)
        self.assertEqual(turn["user_text"], "make the probe stable")
        self.assertIsNone(turn["assistant_text"])
        self.assertIsNone(turn["draft"])
        self.assertTrue(turn["turn_id"].startswith("ct-"))
        self.assertTrue(turn["conversation_id"].startswith("cv-"))
        self.assertEqual(self.journal.turn(turn["turn_id"]), turn)

    def test_T2_a_conversation_keeps_its_turns_in_order(self):
        first = self.finished_turn("one")
        second = self.journal.reserve_turn(first["conversation_id"], "two", "gw-2",
                                           NOW + 10, set())
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(
            [t["user_text"] for t in self.journal.conversation(first["conversation_id"])],
            ["one", "two"])

    def test_T3_an_unknown_conversation_is_refused(self):
        self.refused(journal_module.PROBLEM_UNKNOWN_CONVERSATION,
                     self.journal.reserve_turn, "cv-" + "0" * 32, "x", "gw", NOW, set())

    def test_T4_a_second_turn_while_one_is_in_flight_is_refused(self):
        first = self.journal.reserve_turn(None, "one", "gw-1", NOW, set())
        self.refused(journal_module.PROBLEM_CONVERSATION_BUSY,
                     self.journal.reserve_turn, first["conversation_id"], "two",
                     "gw-2", NOW + 1, {first["turn_id"]})

    def test_T5_empty_oversize_and_unencodable_words_are_refused_before_a_write(self):
        for bad in ("", "   ", "\n", "x" * (journal_module.MAX_TURN_TEXT_CHARS + 1),
                    "lone \ud800 surrogate", 7, None):
            with self.subTest(text=bad):
                self.refused(journal_module.PROBLEM_BAD_TEXT,
                             self.journal.reserve_turn, None, bad, "gw", NOW, set())
        self.assertFalse(os.path.exists(self.directory))

    def test_T6_a_finished_turn_records_the_outcome_and_the_draft(self):
        turn = self.finished_turn()
        self.assertEqual(turn["state"], journal_module.TURN_COMPLETED)
        self.assertEqual(turn["finished_at"], NOW + 5)
        self.assertEqual(turn["assistant_text"], "Here is a draft.")
        self.assertEqual(turn["draft"]["proposal_digest_sha256"],
                         mission_record.proposal_digest(turn["draft"]["proposal"]))

    def test_T7_a_finished_turn_cannot_be_finished_again(self):
        turn = self.finished_turn()
        self.refused(journal_module.PROBLEM_TURN_NOT_IN_FLIGHT,
                     self.journal.finish_turn, turn["turn_id"],
                     state=journal_module.TURN_FAILED, assistant_text=None,
                     outcome={"status": "codex_failed", "error_code": "x",
                              "detail": None, "unrecognized_event_lines": 0},
                     draft=None, draft_problem=None, now=NOW + 9)

    def test_T8_a_draft_only_rides_a_completed_turn(self):
        turn = self.journal.reserve_turn(None, "x", "gw-1", NOW, set())
        proposal = clean_proposal()
        self.refused(journal_module.PROBLEM_BAD_TURN, self.journal.finish_turn,
                     turn["turn_id"], state=journal_module.TURN_UNCERTAIN,
                     assistant_text=None,
                     outcome={"status": "uncertain", "error_code": "e",
                              "detail": None, "unrecognized_event_lines": 0},
                     draft={"proposal": proposal,
                            "proposal_digest_sha256": mission_record.proposal_digest(proposal)},
                     draft_problem=None, now=NOW + 1)

    def test_T9_a_turn_found_in_flight_by_a_later_process_is_UNCERTAIN(self):
        turn = self.journal.reserve_turn(None, "x", "gw-1", NOW, set())
        # A new process holds no in-flight turn: whatever that provider call
        # did is unknown. It is recorded as UNCERTAIN and NEVER retried.
        changed = journal_module.PanelJournal(self.directory).reconcile_unresolved(
            set(), NOW + 100)
        self.assertEqual(changed, [turn["turn_id"]])
        settled = self.journal.turn(turn["turn_id"])
        self.assertEqual(settled["state"], journal_module.TURN_UNCERTAIN)
        self.assertEqual(settled["finished_at"], NOW + 100)
        self.assertEqual(settled["outcome"]["status"], "uncertain")
        self.assertIsNone(settled["assistant_text"])
        self.assertIsNone(settled["draft"])

    def test_T10_a_live_in_flight_turn_is_left_alone(self):
        turn = self.journal.reserve_turn(None, "x", "gw-1", NOW, set())
        self.assertEqual(self.journal.reconcile_unresolved({turn["turn_id"]}, NOW + 1), [])
        self.assertEqual(self.journal.turn(turn["turn_id"])["state"],
                         journal_module.TURN_RESERVED)

    def test_T11_reconciling_writes_nothing_when_nothing_changed(self):
        self.finished_turn()
        before = self.raw()
        self.assertEqual(self.journal.reconcile_unresolved(set(), NOW + 50), [])
        self.assertEqual(self.raw(), before)

    def test_T12_the_record_count_is_a_hard_bound_and_nothing_is_evicted(self):
        original = journal_module.MAX_TURN_RECORDS
        journal_module.MAX_TURN_RECORDS = 2
        self.addCleanup(setattr, journal_module, "MAX_TURN_RECORDS", original)
        self.journal.reserve_turn(None, "one", "gw-1", NOW, set())
        self.journal.reserve_turn(None, "two", "gw-2", NOW, set())
        self.refused(journal_module.PROBLEM_STORE_FULL, self.journal.reserve_turn,
                     None, "three", "gw-3", NOW, set())
        self.assertEqual(len(self.journal.load()["turns"]), 2)


class SubmittedRequestsTests(Fixture):
    """``submitted_requests``: the requests THIS panel durably submitted. It is
    the one list the progression runtime may enumerate, so a request the panel
    did not submit is never in it."""

    def submit(self, ref, state=None, at=NOW + 20):
        turn = self.finished_turn(text="turn for %s" % ref)
        self.journal.reserve_submission(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"], None, at)
        if state is not None:
            self.journal.finish_submission(
                turn["turn_id"], state,
                ref if state == journal_module.SUBMISSION_SUBMITTED else None,
                None if state != journal_module.SUBMISSION_REFUSED else "refused",
                at + 1)
        return turn

    def ref(self, n):
        return "lr-%032x" % n

    def test_P1_only_submitted_requests_are_listed_newest_first(self):
        self.submit(self.ref(1), journal_module.SUBMISSION_SUBMITTED, at=NOW + 10)
        self.submit(self.ref(2), journal_module.SUBMISSION_SUBMITTED, at=NOW + 30)
        self.submit(self.ref(3), journal_module.SUBMISSION_SUBMITTED, at=NOW + 20)
        self.assertEqual(self.journal.submitted_requests(),
                         [self.ref(2), self.ref(3), self.ref(1)])

    def test_P2_reserved_uncertain_and_refused_submissions_name_no_request(self):
        self.submit(self.ref(1))                                         # RESERVED
        self.submit(self.ref(2), journal_module.SUBMISSION_UNCERTAIN)
        self.submit(self.ref(3), journal_module.SUBMISSION_REFUSED)
        self.assertEqual(self.journal.submitted_requests(), [])

    def test_P3_an_empty_or_absent_journal_lists_nothing_and_creates_nothing(self):
        self.assertEqual(self.journal.submitted_requests(), [])
        self.assertFalse(os.path.exists(self.path()), "a read created the journal")

    def test_P4_listing_writes_nothing(self):
        self.submit(self.ref(1), journal_module.SUBMISSION_SUBMITTED)
        before = self.raw()
        self.journal.submitted_requests()
        self.assertEqual(self.raw(), before)

    def test_P5_a_request_replaced_by_a_revision_is_still_listed_once_each(self):
        first = self.submit(self.ref(1), journal_module.SUBMISSION_SUBMITTED, at=NOW + 10)
        turn = self.finished_turn(text="revised")
        self.journal.reserve_submission(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"], self.ref(1), NOW + 40)
        self.journal.finish_submission(
            turn["turn_id"], journal_module.SUBMISSION_SUBMITTED, self.ref(2), None, NOW + 41)
        self.assertEqual(self.journal.submitted_requests(), [self.ref(2), self.ref(1)])
        self.assertIsNotNone(first)


class SubmissionTests(Fixture):

    def digest(self, turn):
        return turn["draft"]["proposal_digest_sha256"]

    def test_S1_a_submission_is_reserved_before_the_surface_is_asked(self):
        turn = self.finished_turn()
        submission, created = self.journal.reserve_submission(
            turn["turn_id"], self.digest(turn), None, NOW + 20)
        self.assertTrue(created)
        self.assertEqual(submission["state"], journal_module.SUBMISSION_RESERVED)
        self.assertIsNone(submission["request_ref"])
        self.assertEqual(self.journal.turn(turn["turn_id"])["submission"], submission)

    def test_S2_a_duplicate_click_gets_the_existing_reservation(self):
        turn = self.finished_turn()
        first, created = self.journal.reserve_submission(
            turn["turn_id"], self.digest(turn), None, NOW + 20)
        again, created_again = self.journal.reserve_submission(
            turn["turn_id"], self.digest(turn), None, NOW + 21)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(again, first)

    def test_S3_only_the_exact_draft_read_can_be_submitted(self):
        turn = self.finished_turn()
        self.refused(journal_module.PROBLEM_DRAFT_MISMATCH,
                     self.journal.reserve_submission, turn["turn_id"], "b" * 64,
                     None, NOW + 20)
        self.assertIsNone(self.journal.turn(turn["turn_id"])["submission"])

    def test_S4_a_turn_without_a_draft_cannot_be_submitted(self):
        turn = self.finished_turn(draft=None, draft_problem="not a proposal")
        self.refused(journal_module.PROBLEM_NO_DRAFT,
                     self.journal.reserve_submission, turn["turn_id"], "a" * 64,
                     None, NOW + 20)

    def test_S5_a_submitted_draft_records_its_request_and_what_it_replaces(self):
        turn = self.finished_turn()
        self.journal.reserve_submission(turn["turn_id"], self.digest(turn),
                                        "lr-" + "1" * 32, NOW + 20)
        done = self.journal.finish_submission(
            turn["turn_id"], journal_module.SUBMISSION_SUBMITTED,
            "lr-" + "2" * 32, None, NOW + 22)
        self.assertEqual(done["state"], journal_module.SUBMISSION_SUBMITTED)
        self.assertEqual(done["request_ref"], "lr-" + "2" * 32)
        self.assertEqual(done["supersedes"], "lr-" + "1" * 32)
        self.assertEqual(self.journal.superseded_by("lr-" + "1" * 32), "lr-" + "2" * 32)
        self.assertIsNone(self.journal.superseded_by("lr-" + "2" * 32))

    def test_S6_a_refused_submission_may_be_tried_again_an_uncertain_one_may_not(self):
        turn = self.finished_turn()
        self.journal.reserve_submission(turn["turn_id"], self.digest(turn), None, NOW + 20)
        self.journal.finish_submission(turn["turn_id"],
                                       journal_module.SUBMISSION_REFUSED, None,
                                       "local_request_bad_request", NOW + 21)
        again, created = self.journal.reserve_submission(
            turn["turn_id"], self.digest(turn), None, NOW + 30)
        self.assertTrue(created)
        self.journal.finish_submission(turn["turn_id"],
                                       journal_module.SUBMISSION_UNCERTAIN, None,
                                       None, NOW + 31)
        unchanged, created = self.journal.reserve_submission(
            turn["turn_id"], self.digest(turn), None, NOW + 40)
        self.assertFalse(created)
        self.assertEqual(unchanged["state"], journal_module.SUBMISSION_UNCERTAIN)
        del again

    def test_S7_a_reserved_submission_left_by_a_dead_process_becomes_UNCERTAIN(self):
        turn = self.finished_turn()
        self.journal.reserve_submission(turn["turn_id"], self.digest(turn), None, NOW + 20)
        self.assertEqual(
            self.journal.reconcile_unresolved(set(), NOW + 500),
            [turn["turn_id"]])
        self.assertEqual(self.journal.turn(turn["turn_id"])["submission"]["state"],
                         journal_module.SUBMISSION_UNCERTAIN)

    def test_S8_a_submitted_record_must_name_a_request(self):
        turn = self.finished_turn()
        self.journal.reserve_submission(turn["turn_id"], self.digest(turn), None, NOW + 20)
        self.refused(journal_module.PROBLEM_BAD_TURN, self.journal.finish_submission,
                     turn["turn_id"], journal_module.SUBMISSION_SUBMITTED, None,
                     None, NOW + 21)


class OfferTests(Fixture):
    """The frozen, displayed decision offer: bookkeeping, never authority."""

    REF = "lr-" + "1" * 32

    def fields(self, **changes):
        base = dict(
            request_ref=self.REF, mission_id="mn-" + "2" * 32, revision=1,
            proposal_digest_sha256="a" * 64, repository_url="https://github.com/Example/Repo",
            action_scope=["engineering_change", "repository_read"], delivery_targets=[],
            expires_at=NOW + 900)
        base.update(changes)
        return base

    def test_F1_an_issued_offer_is_retained_exactly_with_its_digest(self):
        offer, created = self.journal.freeze_offer(self.fields(), NOW)
        self.assertTrue(created)
        self.assertEqual(offer["expires_at"], NOW + 900)
        self.assertEqual(offer["issued_at"], NOW)
        self.assertEqual(offer["offer_digest_sha256"],
                         journal_module.offer_digest(self.fields()))
        again = journal_module.PanelJournal(self.directory).offer(offer["offer_digest_sha256"])
        self.assertEqual(again, offer)

    def test_F2_while_an_offer_is_live_every_window_gets_the_same_one(self):
        first, _ = self.journal.freeze_offer(self.fields(), NOW)
        later, created = self.journal.freeze_offer(self.fields(expires_at=NOW + 1000), NOW + 300)
        self.assertFalse(created)
        self.assertEqual(later, first, "E1 is frozen: a later window sees the SAME offer")

    def test_F3_a_changed_binding_is_a_different_offer(self):
        base, _ = self.journal.freeze_offer(self.fields(), NOW)
        for change in (dict(revision=2), dict(proposal_digest_sha256="b" * 64),
                       dict(mission_id="mn-" + "3" * 32), dict(repository_url=None),
                       dict(action_scope=["repository_read"]), dict(delivery_targets=["github_pr"]),
                       dict(request_ref="lr-" + "9" * 32)):
            with self.subTest(change=change):
                other, created = self.journal.freeze_offer(self.fields(**change), NOW)
                self.assertTrue(created)
                self.assertNotEqual(other["offer_digest_sha256"], base["offer_digest_sha256"])

    def test_F4_after_the_expiry_a_new_offer_is_issued_with_a_new_expiry(self):
        first, _ = self.journal.freeze_offer(self.fields(), NOW)
        later, created = self.journal.freeze_offer(self.fields(expires_at=NOW + 1900), NOW + 1000)
        self.assertTrue(created)
        self.assertEqual(later["expires_at"], NOW + 1900)
        self.assertNotEqual(later["offer_digest_sha256"], first["offer_digest_sha256"])

    def test_F5_expired_offers_are_pruned_and_a_pruned_offer_is_simply_unknown(self):
        first, _ = self.journal.freeze_offer(self.fields(), NOW)
        self.journal.freeze_offer(self.fields(revision=2, expires_at=NOW + 2000), NOW + 1000)
        self.assertIsNone(self.journal.offer(first["offer_digest_sha256"]))
        self.assertEqual(len(self.journal.load()["offers"]), 1)

    def test_F6_the_live_offers_are_a_hard_bound_and_nothing_is_evicted(self):
        original = journal_module.MAX_OFFER_RECORDS
        journal_module.MAX_OFFER_RECORDS = 2
        self.addCleanup(setattr, journal_module, "MAX_OFFER_RECORDS", original)
        self.journal.freeze_offer(self.fields(revision=1), NOW)
        self.journal.freeze_offer(self.fields(revision=2), NOW)
        self.refused(journal_module.PROBLEM_OFFERS_FULL, self.journal.freeze_offer,
                     self.fields(revision=3), NOW)
        self.assertEqual(len(self.journal.load()["offers"]), 2)

    def test_F7_a_tampered_offer_record_fails_closed(self):
        offer, _ = self.journal.freeze_offer(self.fields(), NOW)
        document = json.loads(self.raw())
        key = offer["offer_digest_sha256"]
        tampers = {
            "expiry moved under the same digest": lambda r: r.update(expires_at=r["expires_at"] + 1),
            "scope moved": lambda r: r.update(action_scope=["repository_read"]),
            "unknown key": lambda r: r.update(approved=True),
            "unsorted scope": lambda r: r.update(action_scope=["repository_read", "engineering_change"]),
            "bad request ref": lambda r: r.update(request_ref="lr-x"),
            "expiry before issue": lambda r: r.update(expires_at=r["issued_at"]),
        }
        for name, tamper in tampers.items():
            with self.subTest(tamper=name):
                doc = json.loads(json.dumps(document))
                tamper(doc["offers"][key])
                with open(self.path(), "w") as handle:
                    json.dump(doc, handle)
                os.chmod(self.path(), 0o600)
                with self.assertRaises(journal_module.JournalError):
                    self.journal.load()

    def test_F8_an_offer_names_no_principal_capability_or_decision(self):
        keys = set(journal_module.OFFER_KEYS)
        for forbidden in ("principal", "capability", "token", "approved", "decision", "authorization"):
            self.assertFalse([k for k in keys if forbidden in k], forbidden)

    def test_F9_a_malformed_issue_is_refused_before_a_write(self):
        for change in (dict(revision=0), dict(expires_at=NOW), dict(expires_at=True),
                       dict(proposal_digest_sha256="A" * 64), dict(request_ref="x"),
                       dict(action_scope="engineering_change"), dict(delivery_targets=[7]),
                       dict(repository_url=7)):
            with self.subTest(change=change):
                self.refused(journal_module.PROBLEM_BAD_TURN, self.journal.freeze_offer,
                             self.fields(**change), NOW)
        self.assertFalse(os.path.exists(self.directory))


if __name__ == "__main__":
    unittest.main()
