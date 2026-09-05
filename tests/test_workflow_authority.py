"""Regression coverage for the workflow_authority package.

Hermetic: filesystem work happens in temporary directories, clocks are
explicit values, and no network, Telegram, Codex, model, or
orchestration call is ever made. Assertions run against BEHAVIOR and
against the ON-DISK bytes — durable guarantees are re-read from the
state file and from a FRESH store instance, never only from an
in-memory document.
"""

import copy
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from telegram_operator import protocol, state
from workflow_authority import (
    authorization,
    canonical,
    digest,
    migrate,
    record,
    rendering,
    store,
)


def make_record(workflow_id="wf-0001", mission_revision=1,
                handoff_revision=1, issue_or_pr_kind="issue",
                issue_or_pr_number=7, human_intent="do the mission",
                baseline_ref="refs/heads/main",
                repository_realpath="/control/repo",
                **overrides):
    # Authority-content overrides go INTO the constructor (they feed
    # the rendering); anything else overrides the finished document's
    # top level, as before.
    authority = {
        "objective": "Resolve the defect",
        "constraints": "Bounded",
        "rules": "Target rules cannot override control authority",
        "desired_outcome": "Green verification",
        "acceptance": "Tests pass",
        "unresolved_questions": "None recorded",
        "execution_scope": "The target repository only",
    }
    for key in list(overrides):
        if key in authority:
            authority[key] = overrides.pop(key)
    document = record.new_record(
        workflow_id=workflow_id,
        human_intent=human_intent,
        repository_realpath=repository_realpath,
        policy_digest_sha256="0" * 64,
        canonical_host="github.com",
        owner="octocat",
        repo="target",
        canonical_url="https://github.com/octocat/target",
        issue_or_pr_kind=issue_or_pr_kind,
        issue_or_pr_number=issue_or_pr_number,
        baseline_ref=baseline_ref,
        baseline_commit_sha="a" * 40,
        mission_revision=mission_revision,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        approval_nonce="n" * 32,
        approval_created_at=100,
        approval_expires_at=1000,
        handoff_revision=handoff_revision,
        handoff_text="HANDOFF TEXT",
        **authority
    )
    document.update(overrides)
    return document


class RecordValidationTests(unittest.TestCase):
    def test_new_record_validates_and_is_planned(self):
        document = make_record()
        record.validate_record(document)
        self.assertEqual(document["phase"], record.PHASE_PLANNED)
        self.assertEqual(document["delivery_authority"], "none")

    def test_unknown_top_level_key_fails_closed(self):
        document = make_record()
        document["extra_authority"] = True
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_UNKNOWN_KEY
        )
        self.assertIn("extra_authority", str(caught.exception))

    def test_every_missing_required_key_fails_closed(self):
        for key in record._TOP_LEVEL_KEYS:
            document = make_record()
            del document[key]
            expected = (
                record.PROBLEM_SCHEMA_VERSION
                if key == "schema_version"
                else record.PROBLEM_MISSING_KEY
            )
            # try/except so a mutant that removes the missing-key
            # check dies by FAIL on this guarantee, not by the
            # KeyError the unchecked access would raise (round-1 B2
            # kill-classification rule).
            try:
                record.validate_record(document)
            except record.RecordError as caught:
                self.assertEqual(caught.problem, expected, key)
            except Exception as exc:
                self.fail(
                    "missing key %r must fail closed with"
                    " RecordError, got %r" % (key, exc)
                )
            else:
                self.fail("missing key %r was accepted" % key)

    def test_wrong_schema_version_fails_closed(self):
        for version in (0, 1, "2", None, 1.5, 3):
            document = make_record(schema_version=version)
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem,
                record.PROBLEM_SCHEMA_VERSION,
                repr(version),
            )

    def test_bool_schema_version_fails_closed(self):
        # True == 1 in Python; the bool trap must still refuse it.
        document = make_record(schema_version=True)
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_SCHEMA_VERSION
        )

    def test_delivery_authority_must_be_exactly_none(self):
        for value in ("full", "None", "none ", "", None, True, False,
                      0, 1, ["none"]):
            document = make_record(delivery_authority=value)
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem,
                record.PROBLEM_DELIVERY_AUTHORITY,
                repr(value),
            )

    def test_absent_delivery_authority_fails_closed(self):
        document = make_record()
        del document["delivery_authority"]
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_MISSING_KEY
        )
        self.assertIn("delivery_authority", str(caught.exception))

    def test_result_delivery_state_machine(self):
        # The three durable delivery states each validate; RESERVED and
        # PARTIAL carry no message id, DELIVERED carries a positive int,
        # and an unknown state or a stray id fails closed.
        ok_cases = [
            {"state": record.DELIVERY_RESERVED, "reserved_at": 5,
             "telegram_message_id": None},
            {"state": record.DELIVERY_PARTIAL, "reserved_at": 5,
             "telegram_message_id": None},
            {"state": record.DELIVERY_DELIVERED, "reserved_at": 5,
             "telegram_message_id": 42},
        ]
        for delivery in ok_cases:
            document = make_record(result_delivery=delivery)
            record.validate_record(document)  # must not raise
        # An id while only reserved/partial is refused.
        for state in (record.DELIVERY_RESERVED, record.DELIVERY_PARTIAL):
            document = make_record(result_delivery={
                "state": state, "reserved_at": 5,
                "telegram_message_id": 42,
            })
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_VALUE, state
            )
        # An unknown state is refused.
        document = make_record(result_delivery={
            "state": "done", "reserved_at": 5,
            "telegram_message_id": None,
        })
        with self.assertRaises(record.RecordError):
            record.validate_record(document)

    def test_last_observation_validates(self):
        # I6: the last DISTINCT observation. An observable pair and the
        # UNOBSERVABLE (null, null) pair both validate; a stray key or a
        # missing timestamp fails closed.
        for obs in (
            {"task_status": "ACTIVE", "completeness": "COMPLETE",
             "observed_at": 5},
            {"task_status": None, "completeness": None,
             "observed_at": 5},
        ):
            record.validate_record(make_record(last_observation=obs))
        # Missing timestamp.
        with self.assertRaises(record.RecordError):
            record.validate_record(make_record(last_observation={
                "task_status": "ACTIVE", "completeness": "COMPLETE",
                "observed_at": None,
            }))
        # A stray key (not the closed set).
        with self.assertRaises(record.RecordError):
            record.validate_record(make_record(last_observation={
                "task_status": "ACTIVE", "completeness": "COMPLETE",
                "observed_at": 5, "extra": 1,
            }))

    def test_bool_where_number_expected_fails_closed(self):
        cases = [
            ("telegram", "user_id"),
            ("telegram", "chat_id"),
            ("mission_authorization", "revision"),
            ("handoff", "revision"),
            ("approval", "created_at"),
            ("approval", "expires_at"),
        ]
        for section, key in cases:
            document = make_record()
            document[section][key] = True
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_TYPE,
                (section, key),
            )

    def test_bool_issue_number_fails_closed(self):
        document = make_record()
        document["target"]["issue_or_pr"]["number"] = True
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_BAD_TYPE
        )

    def test_nested_unknown_keys_fail_closed(self):
        sections = (
            "control_identity", "target", "approved_baseline",
            "mission_authorization", "telegram", "approval",
            "handoff", "ambiguity",
        )
        for section in sections:
            document = make_record()
            document[section]["surprise"] = 1
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_UNKNOWN_KEY,
                section,
            )

    def test_nested_missing_keys_fail_closed(self):
        document = make_record()
        del document["approval"]["nonce"]
        try:
            record.validate_record(document)
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_MISSING_KEY
            )
        except Exception as exc:
            self.fail(
                "missing nested key must fail closed with"
                " RecordError, got %r" % (exc,)
            )
        else:
            self.fail("missing nested key was accepted")

    def test_mission_digest_mismatch_fails_closed(self):
        document = make_record()
        document["mission_authorization"]["rendered_text"] = "ALTERED"
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_DIGEST_MISMATCH
        )

    def test_handoff_digest_mismatch_fails_closed(self):
        document = make_record()
        document["handoff"]["text"] = "ALTERED HANDOFF"
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_DIGEST_MISMATCH
        )

    def test_closed_value_sets_fail_closed(self):
        document = make_record(phase="RUNNING")
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        document = make_record()
        document["approval"]["approval_kind"] = "plan_v1"
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        document = make_record()
        document["target"]["issue_or_pr"]["kind"] = "discussion"
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        document = make_record()
        document["ambiguity"]["state"] = "confused"
        with self.assertRaises(record.RecordError):
            record.validate_record(document)

    def test_receipt_shape_and_kind_enforced(self):
        good = {
            "kind": "preparation",
            "turn_id": "turn-1",
            "recorded_at": 5,
            "digest": "b" * 64,
            "bounded_summary": "ok",
        }
        document = make_record(receipts=[good])
        record.validate_record(document)
        bad_kind = dict(good, kind="log")
        document = make_record(receipts=[bad_kind])
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        extra = dict(good, workspace_path="/leak")
        document = make_record(receipts=[extra])
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_UNKNOWN_KEY
        )

    def test_codex_turn_shape_enforced(self):
        good = {
            "turn_id": "turn-1",
            "role": "planning",
            "process_id": 4242,
            "recorded_at": 5,
        }
        document = make_record(codex_turns=[good])
        record.validate_record(document)
        for role in ("supervisor", "", None):
            document = make_record(codex_turns=[dict(good, role=role)])
            with self.assertRaises(record.RecordError):
                record.validate_record(document)
        document = make_record(
            codex_turns=[dict(good, process_id=True)]
        )
        with self.assertRaises(record.RecordError):
            record.validate_record(document)

    def test_receipts_bound_is_refused_with_exact_counts(self):
        # Exact value pin FIRST (round-2 B3): a widened MAX_RECEIPTS
        # must die here by FAIL before the fixture below can scale
        # with it (or allocate unboundedly).
        self.assertEqual(record.MAX_RECEIPTS, 256)
        good = {
            "kind": "evidence",
            "turn_id": "turn-1",
            "recorded_at": 5,
            "digest": "b" * 64,
            "bounded_summary": "",
        }
        over = [dict(good) for _ in range(record.MAX_RECEIPTS + 1)]
        document = make_record(receipts=over)
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )
        message = str(caught.exception)
        self.assertIn(str(record.MAX_RECEIPTS + 1), message)
        self.assertIn(str(record.MAX_RECEIPTS), message)

    # Every bound test first pins the constant's exact VALUE (round-2
    # review finding B3: a magnitude guard is not a pin — the
    # fixtures below are derived from the constant, so any widening
    # under the magnitude limit scaled the fixture and the test
    # stayed green for the wrong reason). The exact pin is
    # load-bearing twice over: any widening — realistic (256->5000)
    # or huge (10**9) — dies here by FAIL before the fixture is
    # built, which is also what makes a gigabyte allocation (and the
    # recorded stall hazard) unreachable.

    def test_workflow_id_alphabet_makes_traversal_unrepresentable(self):
        # Round-08 finding B1, layer 1: the id names the leased
        # workspace directory, so anything outside the closed
        # alphabet — path separators, traversals, uppercase,
        # underscores — is unrepresentable in a valid record.
        for bad in ("../escape", "a/b", "a\\b", "wf_x", "WF-1",
                    "wf.1", "wf 1", "."):
            document = make_record()
            document["workflow_id"] = bad
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_VALUE,
                repr(bad),
            )
        record.validate_record(make_record("wf-0a9f"))

    def test_id_bound_refused_with_exact_counts(self):
        # N1: MAX_ID_CHARS is load-bearing, not decorative.
        self.assertEqual(record.MAX_ID_CHARS, 128)
        document = make_record("w" * record.MAX_ID_CHARS)
        record.validate_record(document)  # exactly at the cap: fine
        over = "w" * (record.MAX_ID_CHARS + 1)
        document = make_record()
        document["workflow_id"] = over
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )
        message = str(caught.exception)
        self.assertIn(str(record.MAX_ID_CHARS + 1), message)
        self.assertIn(str(record.MAX_ID_CHARS), message)

    def test_message_ids_bound_refused(self):
        self.assertEqual(record.MAX_TELEGRAM_MESSAGE_IDS, 64)
        document = make_record()
        document["telegram"]["message_ids"] = list(
            range(1, record.MAX_TELEGRAM_MESSAGE_IDS + 2)
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )
        message = str(caught.exception)
        self.assertIn(
            str(record.MAX_TELEGRAM_MESSAGE_IDS + 1), message
        )
        self.assertIn(str(record.MAX_TELEGRAM_MESSAGE_IDS), message)

    def test_codex_turns_bound_refused(self):
        self.assertEqual(record.MAX_CODEX_TURNS, 256)
        turn = {
            "turn_id": "turn-1", "role": "planning",
            "process_id": 1, "recorded_at": 1,
        }
        document = make_record(
            codex_turns=[dict(turn) for _ in range(
                record.MAX_CODEX_TURNS + 1
            )]
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )

    def test_bounded_summary_bound_refused(self):
        self.assertEqual(record.MAX_BOUNDED_SUMMARY_CHARS, 2000)
        receipt = {
            "kind": "evidence", "turn_id": "turn-1",
            "recorded_at": 1, "digest": "b" * 64,
            "bounded_summary": "s" * record.MAX_BOUNDED_SUMMARY_CHARS,
        }
        record.validate_record(make_record(receipts=[receipt]))
        receipt["bounded_summary"] = "s" * (
            record.MAX_BOUNDED_SUMMARY_CHARS + 1
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(make_record(receipts=[receipt]))
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )

    def test_ambiguity_detail_bound_refused(self):
        self.assertEqual(record.MAX_AMBIGUITY_DETAIL_CHARS, 2000)
        document = make_record(
            ambiguity={
                "state": record.AMBIGUITY_CRASH_UNCERTAIN,
                "detail": "d" * (
                    record.MAX_AMBIGUITY_DETAIL_CHARS + 1
                ),
            }
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )

    def test_authority_text_bound_refused(self):
        from workflow_authority.digest import text_digest
        self.assertEqual(record.MAX_AUTHORITY_TEXT_CHARS, 16384)
        long_text = "t" * (record.MAX_AUTHORITY_TEXT_CHARS + 1)
        document = make_record()
        document["mission_authorization"]["rendered_text"] = long_text
        document["mission_authorization"]["digest_sha256"] = (
            text_digest(long_text)
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )
        document = make_record()
        document["handoff"]["text"] = long_text
        document["handoff"]["digest_sha256"] = text_digest(long_text)
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )

    def test_minimum_value_pins_refused(self):
        # N2: the minimum= enforcement itself must be load-bearing.
        cases = [
            ("telegram", "user_id", 0),
            ("telegram", "user_id", -42),
            ("telegram", "chat_id", 0),
            ("telegram", "chat_id", -5),
            ("mission_authorization", "revision", 0),
            ("handoff", "revision", 0),
        ]
        for section, key, value in cases:
            document = make_record()
            document[section][key] = value
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_VALUE,
                (section, key, value),
            )
        document = make_record()
        document["target"]["issue_or_pr"]["number"] = 0
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_BAD_VALUE
        )
        turn = {
            "turn_id": "turn-1", "role": "planning",
            "process_id": 0, "recorded_at": 1,
        }
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(make_record(codex_turns=[turn]))
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_BAD_VALUE
        )

    def test_R18_issue_or_pr_number_is_bounded_by_the_canonical_url(self):
        """RULING R-18 — the durable-boundary certification, in the
        validator's OWN home.

        `_validate_target` refuses any issue/PR number that would make
        the FULL canonical issue/PR URL exceed
        `canonical.MAX_TARGET_URL_CHARS`, so a target the canonicalizer
        could never emit is UNREPRESENTABLE at the record boundary
        rather than merely undeliverable later. This is proven for BOTH
        the `issue` and the `pull` segment, and every quantity is
        DERIVED from the live canonical constants — no literal 512, no
        literal segment length — so a change to `MAX_TARGET_URL_CHARS`
        or to either segment name breaks this test instead of silently
        widening the contract.
        """
        repo_url = "https://github.com/octocat/target"
        # `make_record` stores the REPOSITORY url in target.canonical_url
        # (validated by canonicalize_repository_url); the number lives
        # separately, so the bound must be reconstructed here exactly as
        # the validator does.

        def room_for(segment):
            return (
                canonical.MAX_TARGET_URL_CHARS
                - len(repo_url) - len("/") - len(segment) - len("/")
            )

        cases = (
            ("issue", canonical.ISSUE_SEGMENT),
            ("pr", canonical.PULL_SEGMENT),
        )
        issue_room = room_for(canonical.ISSUE_SEGMENT)
        pr_room = room_for(canonical.PULL_SEGMENT)
        # DERIVATION SANITY: "pull" is shorter than "issues", so a PR
        # target may carry a strictly larger number. If a mutant uses
        # one segment for both kinds this equality/ordering breaks.
        self.assertGreater(
            pr_room, issue_room,
            "the PR segment is shorter, so its number room must be"
            " larger; a shared-segment mutant collapses this",
        )

        for kind, segment in cases:
            room = room_for(segment)
            largest_legal = int("9" * room)
            first_illegal = int("9" * (room + 1))

            # The largest number whose canonical URL is EXACTLY the
            # bound is ACCEPTED. AUTHORED assertion, not a bare call: an
            # off-by-one-narrowing, dropped-separator or shared-segment
            # mutant REJECTS this legal target and must die by THIS
            # fail, never by an uncaught RecordError (repo rule: a crash
            # is not a kill). `make_record` self-validates through
            # `new_record`, so the rejection surfaces HERE — wrap it too.
            try:
                accepted = make_record(
                    issue_or_pr_kind=kind,
                    issue_or_pr_number=largest_legal,
                )
            except record.RecordError as exc:
                self.fail(
                    "the largest canonical-legal %s number must be"
                    " ACCEPTED by the constructor; the R-18 bound"
                    " rejected it: %s" % (kind, exc)
                )
            self.assertEqual(
                len(accepted["target"]["canonical_url"])
                + len("/") + len(segment) + len("/")
                + len(str(largest_legal)),
                canonical.MAX_TARGET_URL_CHARS,
                "the fixture must sit EXACTLY on the bound (%s)" % kind,
            )
            try:
                record.validate_record(accepted)
            except record.RecordError as exc:
                self.fail(
                    "the largest canonical-legal %s number must be"
                    " ACCEPTED; the bound rejected it: %s" % (kind, exc)
                )

            # One digit more is UNREPRESENTABLE — proven on a COHERENT
            # record whose authorization rendering ALSO reflects the
            # oversized number. That matters: `_validate_target` (the
            # R-18 guard) runs BEFORE the total render-binding check, so
            # an INCOHERENT over-record (number bumped, rendered_text
            # stale) would, under a mutant that deletes the R-18 guard,
            # be refused by PROBLEM_RENDER_BINDING instead — a kill
            # attributable to the wrong guard. Rendering the
            # authorization WITH the oversized number makes the R-18
            # bound the SOLE refuser: delete it and this record
            # VALIDATES (the bad state becomes representable), so the
            # authored `self.fail` below fires.
            over = copy.deepcopy(accepted)
            over["target"]["issue_or_pr"]["number"] = first_illegal
            rendered = rendering.render_record_text(over)
            over["mission_authorization"]["rendered_text"] = rendered
            over["mission_authorization"]["digest_sha256"] = (
                digest.text_digest(rendered)
            )
            try:
                record.validate_record(over)
            except record.RecordError as exc:
                self.assertEqual(
                    exc.problem, record.PROBLEM_ISSUE_URL_TOO_LONG,
                    "a coherent over-bound %s record must be refused by"
                    " the R-18 bound, not another guard" % kind,
                )
                self.assertIn(
                    str(canonical.MAX_TARGET_URL_CHARS), str(exc),
                    "the refusal must name the derived canonical bound",
                )
            else:
                self.fail(
                    "a coherent over-bound %s number must be refused by"
                    " the R-18 durable bound; it VALIDATED — the bound"
                    " is missing" % kind
                )

    def test_negative_timestamps_refused(self):
        for section, key in (
            ("approval", "created_at"),
            ("approval", "expires_at"),
        ):
            document = make_record()
            document[section][key] = -1
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_VALUE,
                (section, key),
            )
        receipt = {
            "kind": "evidence", "turn_id": "turn-1",
            "recorded_at": -0.5, "digest": "b" * 64,
            "bounded_summary": "",
        }
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(make_record(receipts=[receipt]))
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_BAD_VALUE
        )
        lease = {
            "lease_id": "lease-1", "path_realpath": "/leases/x",
            "acquired_at": -1, "released_at": None,
        }
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(make_record(workspace_lease=lease))
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_BAD_VALUE
        )

    def test_superseded_must_be_a_real_bool(self):
        for value in (1, 0, "yes", None):
            document = make_record()
            document["approval"]["superseded"] = value
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_TYPE,
                repr(value),
            )

    def test_padded_handoff_text_is_unrepresentable(self):
        # I5 byte-exact dispatch: the spawn bridge strips surrounding
        # whitespace, so padded handoff text must be UNREPRESENTABLE
        # (the strip is then provably an identity for every
        # dispatchable record).
        from workflow_authority.digest import text_digest
        # I6 closing (I5 carry): derive the FULL alphabet str.strip()
        # removes from the PLATFORM itself, not from any enumeration —
        # every codepoint whose presence at either end makes strip a
        # non-identity must be unrepresentable, so the guarantee
        # survives a future codepoint the enumeration never heard of.
        strip_alphabet = [
            char for char in map(chr, range(0x110000))
            if (char + "x").strip() != char + "x"
            or ("x" + char).strip() != "x" + char
        ]
        # Sanity floor: the review-recorded count was 22; the measured
        # 3.9.6 set is larger. Shrinking below the floor means the
        # derivation broke, not that Python changed.
        self.assertGreaterEqual(len(strip_alphabet), 22)
        self.assertIn(" ", strip_alphabet)
        self.assertIn(" ", strip_alphabet)
        padded_forms = [" x ", "\tx\t"]
        for char in strip_alphabet:
            padded_forms.append(char + "x")
            padded_forms.append("x" + char)
        for padded in padded_forms:
            document = make_record()
            document["handoff"]["text"] = padded
            document["handoff"]["digest_sha256"] = text_digest(padded)
            with self.assertRaises(record.RecordError) as caught:
                record.validate_record(document)
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_BAD_VALUE,
                repr(padded),
            )

    def test_hex_fields_enforced(self):
        document = make_record()
        document["approved_baseline"]["commit_sha"] = "A" * 40
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        document = make_record()
        document["approved_baseline"]["commit_sha"] = "a" * 39
        with self.assertRaises(record.RecordError):
            record.validate_record(document)
        document = make_record()
        document["control_identity"]["policy_digest_sha256"] = "z" * 64
        with self.assertRaises(record.RecordError):
            record.validate_record(document)

    def test_workspace_lease_none_and_shape(self):
        record.validate_record(make_record(workspace_lease=None))
        lease = {
            "lease_id": "lease-1",
            "path_realpath": "/leases/wf-0001",
            "acquired_at": 10,
            "released_at": None,
        }
        record.validate_record(make_record(workspace_lease=lease))
        with self.assertRaises(record.RecordError):
            record.validate_record(
                make_record(
                    workspace_lease=dict(lease, extra="x")
                )
            )
        with self.assertRaises(record.RecordError):
            record.validate_record(
                make_record(
                    workspace_lease=dict(
                        lease, path_realpath="relative/path"
                    )
                )
            )

    def test_relative_repository_realpath_fails_closed(self):
        document = make_record()
        document["control_identity"]["repository_realpath"] = "repo"
        with self.assertRaises(record.RecordError):
            record.validate_record(document)


class PhaseTransitionTests(unittest.TestCase):
    def test_forward_chain_is_allowed(self):
        chain = (
            record.PHASE_PLANNED,
            record.PHASE_AUTHORIZED,
            record.PHASE_WORKSPACE_READY,
            record.PHASE_PREPARED,
            record.PHASE_VALIDATED,
            record.PHASE_DISPATCHED,
            record.PHASE_VERIFIED,
            record.PHASE_COMPLETED,
        )
        for current, upcoming in zip(chain, chain[1:]):
            ok, problem = record.validate_transition(current, upcoming)
            self.assertTrue(ok, (current, upcoming, problem))

    def test_reauthorization_returns_to_authorized(self):
        ok, _ = record.validate_transition(
            record.PHASE_PREPARED, record.PHASE_NEEDS_REAUTHORIZATION
        )
        self.assertTrue(ok)
        ok, _ = record.validate_transition(
            record.PHASE_NEEDS_REAUTHORIZATION, record.PHASE_AUTHORIZED
        )
        self.assertTrue(ok)

    def test_every_pair_matches_the_explicit_table(self):
        # Exhaustive: the table is the single authority; anything not
        # in it fails closed with the transition problem code.
        for current in record.PHASES:
            for upcoming in record.PHASES:
                ok, problem = record.validate_transition(
                    current, upcoming
                )
                expected = (
                    upcoming in record.ALLOWED_TRANSITIONS[current]
                )
                self.assertEqual(ok, expected, (current, upcoming))
                if not expected:
                    self.assertEqual(
                        problem,
                        record.PROBLEM_INVALID_TRANSITION,
                        (current, upcoming),
                    )

    def test_terminal_phases_have_no_outgoing_transitions(self):
        for terminal in record.TERMINAL_PHASES:
            self.assertEqual(
                record.ALLOWED_TRANSITIONS[terminal], frozenset()
            )

    def test_unknown_phases_fail_closed(self):
        for pair in (
            (record.PHASE_PLANNED, "SHIPPED"),
            ("SHIPPED", record.PHASE_PLANNED),
            (None, record.PHASE_PLANNED),
            (record.PHASE_PLANNED, None),
        ):
            ok, problem = record.validate_transition(*pair)
            self.assertFalse(ok, pair)
            self.assertEqual(
                problem, record.PROBLEM_UNKNOWN_PHASE, pair
            )

    def test_apply_transition_mutates_only_when_allowed(self):
        document = make_record()
        record.apply_transition(document, record.PHASE_AUTHORIZED)
        self.assertEqual(document["phase"], record.PHASE_AUTHORIZED)
        with self.assertRaises(record.RecordError) as caught:
            record.apply_transition(document, record.PHASE_DISPATCHED)
        self.assertEqual(
            caught.exception.problem,
            record.PROBLEM_INVALID_TRANSITION,
        )
        self.assertEqual(document["phase"], record.PHASE_AUTHORIZED)


class DigestTests(unittest.TestCase):
    def test_golden_vectors_are_pinned(self):
        # Pinned canonical form and digests: any change to the
        # canonical serialization or framing is a breaking change and
        # must be caught here, not discovered in stored records.
        value = {"b": 2, "a": [1, "x"], "c": {"nested": True}}
        self.assertEqual(
            digest.canonical_json_bytes(value),
            b'{"a":[1,"x"],"b":2,"c":{"nested":true}}',
        )
        self.assertEqual(
            digest.json_digest(value),
            "4990ec746f8f2c24955d019c0603eb81"
            "f5839b2d3aad03e0ca236d30f6c07084",
        )
        self.assertEqual(
            digest.text_digest("mission text\n"),
            "fefa796027de0cbfd268fe0914612d0f"
            "46e07bf337203cafd1da8462cf22f1dc",
        )
        self.assertEqual(
            digest.framed_digest([b"ab", b"c"]),
            "8e502afb4273fb33b57a8c24a5709d6c"
            "7030c2c8626f421ddae7b4f93707455f",
        )
        self.assertEqual(
            digest.policy_digest(b"AGENTS", b"PROTOCOL", {"policy": 1}),
            "05efc1c6cf6fffc920f0e42dfcc8416a"
            "04e2dc603923117dc226b9c264057c07",
        )

    def test_non_ascii_golden_vectors_are_pinned(self):
        # Inherited obligation (I1 reviewer N16): every earlier golden
        # vector was ASCII, so ensure_ascii=True was unverified in the
        # DIGEST mechanism. Distinct from the role-turn prompt
        # containment fix (I2 B2) — same mechanism, different surface.
        self.assertEqual(
            digest.text_digest("café ☕ 日本語\n"),
            "af67eac927d4e3078ce07295d1d5bde6"
            "cf7b20085f07e81d358d35e3bc458d05",
        )
        # try/fail: an ensure_ascii mutant makes the .encode("ascii")
        # raise; that must land as a FAIL on this guarantee, not an
        # ERROR (the mechanism is fail-loud, never a wrong digest —
        # asserted here rather than assumed).
        try:
            canonical = digest.canonical_json_bytes(
                {"k": "café", "n": "日本語"}
            )
            digested = digest.json_digest({"k": "café", "n": "日本語"})
        except Exception as exc:
            self.fail(
                "non-ASCII values must serialize canonically;"
                " raised %r" % (exc,)
            )
        self.assertEqual(
            canonical,
            b'{"k":"caf\\u00e9","n":"\\u65e5\\u672c\\u8a9e"}',
        )
        self.assertEqual(
            digested,
            "39e9d527f28d1759ee41beb45aee931c"
            "1e0d5a20f89cb748a0c737b273bc7b7a",
        )

    def test_key_order_independence(self):
        first = json.loads('{"a": 1, "b": 2, "c": {"x": 1, "y": 2}}')
        second = json.loads('{"c": {"y": 2, "x": 1}, "b": 2, "a": 1}')
        self.assertEqual(
            digest.json_digest(first), digest.json_digest(second)
        )

    def test_two_splits_of_the_same_bytes_differ(self):
        # The framing guarantee: a byte can never move across a part
        # boundary without changing the digest. Raw concatenation
        # would make all three of these EQUAL.
        split_one = digest.framed_digest([b"ab", b"c"])
        split_two = digest.framed_digest([b"a", b"bc"])
        whole = digest.framed_digest([b"abc"])
        self.assertNotEqual(split_one, split_two)
        self.assertNotEqual(split_one, whole)
        self.assertNotEqual(split_two, whole)

    def test_policy_digest_framing_boundary(self):
        self.assertNotEqual(
            digest.policy_digest(b"AB", b"C", {}),
            digest.policy_digest(b"A", b"BC", {}),
        )

    def test_non_serializable_input_is_refused(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
            {"k": float("nan")},
            {1: "x"},
            {"k": {2: "y"}},
            set(),
            object(),
            b"bytes-value",
        ):
            with self.assertRaises(digest.DigestError):
                digest.canonical_json_bytes(value)

    def test_byte_functions_refuse_wrong_types(self):
        with self.assertRaises(digest.DigestError):
            digest.sha256_hex("text")
        with self.assertRaises(digest.DigestError):
            digest.text_digest(b"bytes")
        with self.assertRaises(digest.DigestError):
            digest.framed_digest(["text-part"])
        with self.assertRaises(digest.DigestError):
            digest.policy_digest("text", b"x", {})

    def test_compute_policy_digest_reads_exact_bytes(self):
        with tempfile.TemporaryDirectory() as base:
            agents = os.path.join(base, digest.AGENTS_DOCUMENT_NAME)
            operator_protocol = os.path.join(
                base, digest.OPERATOR_PROTOCOL_DOCUMENT_NAME
            )
            with open(agents, "wb") as handle:
                handle.write(b"agents doc\n")
            with open(operator_protocol, "wb") as handle:
                handle.write(b"operator doc\n")
            computed = digest.compute_policy_digest(base, {"p": 1})
            self.assertEqual(
                computed,
                digest.policy_digest(
                    b"agents doc\n", b"operator doc\n", {"p": 1}
                ),
            )
            with open(agents, "wb") as handle:
                handle.write(b"agents doc CHANGED\n")
            self.assertNotEqual(
                digest.compute_policy_digest(base, {"p": 1}), computed
            )

    def test_compute_policy_digest_missing_document_fails_closed(self):
        with tempfile.TemporaryDirectory() as base:
            with open(
                os.path.join(base, digest.AGENTS_DOCUMENT_NAME), "wb"
            ) as handle:
                handle.write(b"agents\n")
            with self.assertRaises(digest.DigestError):
                digest.compute_policy_digest(base, {})


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = store.WorkflowStore(self.tmp.name)

    def read_raw_bytes(self):
        with open(self.store.path, "rb") as handle:
            return handle.read()

    def test_missing_file_yields_default_document(self):
        document = self.store.load()
        self.assertEqual(
            document["workflow_store_schema_version"],
            store.WORKFLOW_STORE_SCHEMA_VERSION,
        )
        self.assertEqual(document["workflows"], {})

    def test_roundtrip_restart_probe_reads_from_disk(self):
        # The restart probe: every durable guarantee is asserted
        # against the ON-DISK file and a FRESH store instance reloaded
        # from it, BEFORE any intervening save that could mask a
        # missing persistence.
        document = store.default_document()
        ok, problem, pruned = store.add_workflow(document, make_record())
        self.assertTrue(ok, problem)
        self.assertEqual(pruned, 0)
        self.store.save(document)
        raw = json.loads(self.read_raw_bytes().decode("utf-8"))
        self.assertIn("wf-0001", raw["workflows"])
        self.assertEqual(
            raw["workflows"]["wf-0001"]["delivery_authority"], "none"
        )
        fresh = store.WorkflowStore(self.tmp.name)
        reloaded = fresh.load()
        self.assertEqual(reloaded, document)
        mode = stat.S_IMODE(os.stat(self.store.path).st_mode)
        self.assertEqual(mode, 0o600)
        leftovers = [
            name for name in os.listdir(self.tmp.name)
            if name.startswith(".workflows-")
        ]
        self.assertEqual(leftovers, [])

    def full_record(self, workflow_id="wf-full"):
        """A record whose EVERY mutable field is NON-DEFAULT.

        Round-1 blocking finding B1: a probe that round-trips only
        default-valued fields cannot observe a save path that resets
        them, so every mutable binding here carries a value that
        differs from what `make_record` (and every default) produces.
        """
        # Round-2 finding B5: revisions must be NON-DEFAULT too —
        # 1 is also the natural reset value, so a save path that
        # silently reset a revision was invisible. Distinct values so
        # a cross-assignment is caught as well. Since I1, the render
        # binding covers the revision fields, so the non-default
        # revisions go in through the constructor (which renders from
        # them) rather than post-hoc mutation.
        document = make_record(workflow_id, mission_revision=3,
                               handoff_revision=2)
        document["phase"] = record.PHASE_DISPATCHED
        document["approval"].update({
            "consumed_at": 555,
            "consumed_by_update_id": 777,
            "decision": "approve",
            "superseded": True,
        })
        document["receipts"] = [
            {"kind": "preparation", "turn_id": "turn-prep",
             "recorded_at": 11, "digest": "c" * 64,
             "bounded_summary": "instructions discovered"},
            {"kind": "evidence", "turn_id": "turn-verify",
             "recorded_at": 12, "digest": "d" * 64,
             "bounded_summary": "verification evidence"},
        ]
        document["codex_turns"] = [
            {"turn_id": "turn-prep", "role": "prepare",
             "process_id": 4242, "recorded_at": 11},
        ]
        document["workspace_lease"] = {
            "lease_id": "lease-9",
            "path_realpath": "/leases/wf-full",
            "acquired_at": 20,
            "released_at": 30,
        }
        document["ambiguity"] = {
            "state": record.AMBIGUITY_CRASH_UNCERTAIN,
            "detail": "crashed between dispatch and receipt",
        }
        document["telegram"]["message_ids"] = [11, 12, 13]
        document["telegram"]["plan_message_id"] = 13
        record.validate_record(document)
        return document

    def test_restart_probe_pins_every_mutable_field_on_disk(self):
        # Blocking finding B1 (the recorded fail-closed-state-
        # asserted-only-in-memory class): the durable-authority
        # guarantees rest on consumed_at / consumed_by_update_id /
        # decision / superseded / phase / receipts / codex_turns /
        # workspace_lease / ambiguity / message_ids / plan_message_id
        # being DURABLE. Every one is asserted field-by-field against
        # the RAW ON-DISK JSON and against a FRESH store instance,
        # compared to a deep copy taken BEFORE the save — so a save
        # path that silently rewrites the (shared, in-memory) document
        # on its way to disk cannot mask itself. Probe positioned
        # before any intervening save.
        entry = self.full_record()
        expected = copy.deepcopy(entry)
        document = store.default_document()
        ok, problem, _ = store.add_workflow(document, entry)
        self.assertTrue(ok, problem)
        self.store.save(document)
        # assertTrue first so a save-that-persists-nothing mutant
        # dies by FAIL, not by a FileNotFoundError crash.
        self.assertTrue(
            os.path.exists(self.store.path),
            "save() persisted nothing: the store file does not exist",
        )
        raw = json.loads(self.read_raw_bytes().decode("utf-8"))
        on_disk = raw["workflows"].get("wf-full")
        self.assertIsNotNone(
            on_disk, "record missing from the on-disk store"
        )
        fresh = store.WorkflowStore(self.tmp.name).load()
        reloaded = fresh["workflows"].get("wf-full")
        self.assertIsNotNone(
            reloaded, "record missing from a fresh reload"
        )
        for label, probed in (("on-disk", on_disk),
                              ("fresh-reload", reloaded)):
            self.assertEqual(
                probed["phase"], record.PHASE_DISPATCHED, label
            )
            self.assertEqual(
                probed["approval"]["consumed_at"], 555, label
            )
            self.assertEqual(
                probed["approval"]["consumed_by_update_id"], 777,
                label,
            )
            self.assertEqual(
                probed["approval"]["decision"], "approve", label
            )
            self.assertIs(
                probed["approval"]["superseded"], True, label
            )
            self.assertEqual(
                probed["receipts"], expected["receipts"], label
            )
            self.assertEqual(
                probed["codex_turns"], expected["codex_turns"], label
            )
            self.assertEqual(
                probed["workspace_lease"],
                expected["workspace_lease"], label,
            )
            self.assertEqual(
                probed["ambiguity"]["state"],
                record.AMBIGUITY_CRASH_UNCERTAIN, label,
            )
            self.assertEqual(
                probed["ambiguity"]["detail"],
                "crashed between dispatch and receipt", label,
            )
            self.assertEqual(
                probed["telegram"]["message_ids"], [11, 12, 13], label
            )
            self.assertEqual(
                probed["telegram"]["plan_message_id"], 13, label
            )
            self.assertEqual(
                probed["mission_authorization"]["revision"], 3, label
            )
            self.assertEqual(
                probed["handoff"]["revision"], 2, label
            )
            # Belt: the WHOLE record equals the pre-save deep copy,
            # so no field outside the explicit list can drift either.
            self.assertEqual(probed, expected, label)

    def write_raw_store(self, text):
        """Hand-write the store file AT MODE 600, so each tamper test
        exercises its own intended guard rather than tripping the
        open-permission refusal (a fresh open() creates 644 under the
        usual umask, which would turn every one of these into a
        green-for-the-wrong-reason pass)."""
        with open(self.store.path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.store.path, 0o600)

    def test_open_permissions_fail_closed_on_load(self):
        # I1 N14 closed in I6: the store is authority-bearing, so a
        # group/other-accessible file is refused on load with an
        # actionable chmod message — the same posture the adapter
        # config loader takes.
        document = store.default_document()
        self.store.save(document)
        os.chmod(self.store.path, 0o644)
        before = self.read_raw_bytes()
        with self.assertRaises(store.StoreError) as caught:
            self.store.load()
        message = str(caught.exception)
        self.assertIn("group/other", message)
        self.assertIn("chmod 600", message)
        self.assertIn("644", message)
        # Refusal, not repair: file untouched, and a FRESH instance
        # refuses too.
        self.assertEqual(self.read_raw_bytes(), before)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.store.path).st_mode), 0o644
        )
        with self.assertRaises(store.StoreError):
            store.WorkflowStore(self.tmp.name).load()
        # And once the human fixes the mode, load succeeds unchanged.
        os.chmod(self.store.path, 0o600)
        self.assertEqual(self.store.load(), document)

    def test_malformed_file_fails_closed_and_is_never_reinitialized(self):
        self.write_raw_store("{torn")
        before = self.read_raw_bytes()
        with self.assertRaises(store.StoreError) as caught:
            self.store.load()
        self.assertIn("move the file aside", str(caught.exception))
        self.assertEqual(self.read_raw_bytes(), before)
        # A second load must STILL fail: nothing may have quietly
        # replaced the malformed authority-bearing file.
        with self.assertRaises(store.StoreError):
            store.WorkflowStore(self.tmp.name).load()
        self.assertEqual(self.read_raw_bytes(), before)

    def test_unknown_store_version_fails_closed(self):
        document = store.default_document()
        self.store.save(document)
        raw = json.loads(self.read_raw_bytes().decode("utf-8"))
        raw["workflow_store_schema_version"] = 99
        self.write_raw_store(json.dumps(raw))
        with self.assertRaises(store.StoreError):
            self.store.load()

    def test_bool_store_version_fails_closed(self):
        raw = store.default_document()
        raw["workflow_store_schema_version"] = True
        self.write_raw_store(json.dumps(raw))
        with self.assertRaises(store.StoreError):
            self.store.load()

    def test_unknown_top_level_key_on_disk_fails_closed(self):
        raw = store.default_document()
        raw["side_channel"] = {}
        self.write_raw_store(json.dumps(raw))
        with self.assertRaises(store.StoreError):
            self.store.load()

    def test_unknown_record_key_on_disk_fails_closed_on_load(self):
        # Acceptance 1, LOAD side: an unknown key smuggled into a
        # stored record refuses the whole store.
        document = store.default_document()
        store.add_workflow(document, make_record())
        self.store.save(document)
        raw = json.loads(self.read_raw_bytes().decode("utf-8"))
        raw["workflows"]["wf-0001"]["extra_grant"] = "all"
        self.write_raw_store(json.dumps(raw))
        with self.assertRaises(store.StoreError) as caught:
            self.store.load()
        self.assertIn("extra_grant", str(caught.exception))

    def test_unknown_record_key_fails_closed_on_save(self):
        # Acceptance 1, SAVE side: validation runs BEFORE any
        # filesystem write, so the previous file stays byte-identical.
        document = store.default_document()
        store.add_workflow(document, make_record())
        self.store.save(document)
        before = self.read_raw_bytes()
        tampered = copy.deepcopy(document)
        tampered["workflows"]["wf-0001"]["extra_grant"] = "all"
        with self.assertRaises(store.StoreError):
            self.store.save(tampered)
        self.assertEqual(self.read_raw_bytes(), before)

    def test_workflow_id_key_mismatch_fails_closed(self):
        document = store.default_document()
        entry = make_record()
        document["workflows"]["other-id"] = entry
        with self.assertRaises(store.StoreError):
            self.store.save(document)

    def test_interrupted_replace_leaves_previous_file_byte_identical(self):
        document = store.default_document()
        store.add_workflow(document, make_record())
        self.store.save(document)
        before = self.read_raw_bytes()
        updated = copy.deepcopy(document)
        ok, _, _ = store.add_workflow(updated, make_record("wf-0002"))
        self.assertTrue(ok)
        with patch(
            "os.replace", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                self.store.save(updated)
        self.assertEqual(self.read_raw_bytes(), before)
        leftovers = [
            name for name in os.listdir(self.tmp.name)
            if name.startswith(".workflows-")
        ]
        self.assertEqual(leftovers, [])

    def test_duplicate_workflow_id_is_refused(self):
        document = store.default_document()
        ok, _, _ = store.add_workflow(document, make_record())
        self.assertTrue(ok)
        ok, problem, pruned = store.add_workflow(document, make_record())
        self.assertFalse(ok)
        self.assertEqual(problem, store.PROBLEM_DUPLICATE_WORKFLOW)
        self.assertEqual(pruned, 0)
        self.assertEqual(len(document["workflows"]), 1)

    def test_full_store_of_active_records_refuses_and_evicts_nothing(self):
        # Exact value pin FIRST (round-2 B3): the fixtures below are
        # built from the constant, so a widened cap must FAIL here
        # before any fixture is constructed.
        self.assertEqual(store.MAX_WORKFLOW_RECORDS, 64)
        document = store.default_document()
        for index in range(store.MAX_WORKFLOW_RECORDS):
            ok, problem, _ = store.add_workflow(
                document, make_record("wf-%04d" % index)
            )
            self.assertTrue(ok, problem)
        before_ids = sorted(document["workflows"])
        ok, problem, pruned = store.add_workflow(
            document, make_record("wf-overflow")
        )
        self.assertFalse(ok)
        self.assertEqual(problem, store.PROBLEM_STORE_FULL)
        self.assertEqual(pruned, 0)
        # Explicit refusal, exact counts, and NO eviction of any
        # active record.
        self.assertEqual(sorted(document["workflows"]), before_ids)
        counts = store.store_counts(document)
        self.assertEqual(
            counts,
            {
                "total": store.MAX_WORKFLOW_RECORDS,
                "active": store.MAX_WORKFLOW_RECORDS,
                "inactive": 0,
            },
        )

    def test_cap_prunes_only_terminal_records_oldest_first(self):
        self.assertEqual(store.MAX_WORKFLOW_RECORDS, 64)
        document = store.default_document()
        for index in range(store.MAX_WORKFLOW_RECORDS - 2):
            ok, _, _ = store.add_workflow(
                document, make_record("wf-active-%04d" % index)
            )
            self.assertTrue(ok)
        older = make_record("wf-done-older")
        older["phase"] = record.PHASE_COMPLETED
        older["approval"]["created_at"] = 5
        newer = make_record("wf-done-newer")
        newer["phase"] = record.PHASE_BLOCKED
        newer["approval"]["created_at"] = 50
        for entry in (older, newer):
            ok, _, _ = store.add_workflow(document, entry)
            self.assertTrue(ok)
        self.assertEqual(
            len(document["workflows"]), store.MAX_WORKFLOW_RECORDS
        )
        ok, problem, pruned = store.add_workflow(
            document, make_record("wf-new")
        )
        self.assertTrue(ok, problem)
        # The prune is REPORTED exactly, never silent: exactly one
        # terminal record was dropped to make room.
        self.assertEqual(pruned, 1)
        self.assertNotIn("wf-done-older", document["workflows"])
        self.assertIn("wf-done-newer", document["workflows"])
        self.assertIn("wf-new", document["workflows"])
        self.assertEqual(
            len(document["workflows"]), store.MAX_WORKFLOW_RECORDS
        )

    def test_overfull_file_on_disk_fails_closed(self):
        self.assertEqual(store.MAX_WORKFLOW_RECORDS, 64)
        document = store.default_document()
        for index in range(store.MAX_WORKFLOW_RECORDS):
            ok, _, _ = store.add_workflow(
                document, make_record("wf-%04d" % index)
            )
            self.assertTrue(ok)
        self.store.save(document)
        raw = json.loads(self.read_raw_bytes().decode("utf-8"))
        extra = make_record("wf-smuggled")
        raw["workflows"]["wf-smuggled"] = extra
        self.write_raw_store(json.dumps(raw))
        with self.assertRaises(store.StoreError) as caught:
            self.store.load()
        message = str(caught.exception)
        self.assertIn(str(store.MAX_WORKFLOW_RECORDS + 1), message)
        self.assertIn(str(store.MAX_WORKFLOW_RECORDS), message)


class AuthorizationSchemaTests(unittest.TestCase):
    def valid_document(self):
        document = {
            key: "content" for key in
            authorization.ALLOWED_AUTHORIZATION_KEYS
        }
        document["delivery_authority"] = "none"
        return document

    def test_valid_document_passes(self):
        authorization.validate_authorization_structure(
            self.valid_document()
        )

    def test_every_forbidden_key_refused_by_name(self):
        # The problem code is asserted exactly: guard (b) must refuse
        # these BY NAME, not by falling through to the closed-set
        # guard. (A guard-(b) deletion mutant changes the code to
        # PROBLEM_UNKNOWN_KEY and dies here.)
        for key in sorted(authorization.FORBIDDEN_STRATEGY_KEYS):
            document = self.valid_document()
            document[key] = ["anything"]
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_FORBIDDEN_KEY,
                key,
            )
            self.assertIn(key, str(caught.exception))

    def test_forbidden_keys_refused_after_normalization(self):
        # N11: guard (b) must be strong STANDING ALONE, so cosmetic
        # variants — ligature/full-width compatibility forms and
        # padding whitespace — are folded before matching.
        for key in ("ﬁles", "ｐｌａｎ", " plan",
                    "PLAN "):
            document = self.valid_document()
            document[key] = []
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_FORBIDDEN_KEY,
                repr(key),
            )

    def test_forbidden_keys_refused_case_insensitively(self):
        for key in ("Plan", "STEPS", "Files", "dIfF"):
            document = self.valid_document()
            document[key] = []
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_FORBIDDEN_KEY,
                key,
            )

    def test_out_of_set_key_refused(self):
        for key in ("notes", "milestones", "extra"):
            document = self.valid_document()
            document[key] = "x"
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_UNKNOWN_KEY,
                key,
            )

    def test_every_missing_key_refused(self):
        for key in sorted(authorization.ALLOWED_AUTHORIZATION_KEYS):
            document = self.valid_document()
            del document[key]
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_MISSING_KEY,
                key,
            )

    def test_delivery_authority_must_be_exactly_none(self):
        for value in ("full", True, None, "", "None"):
            document = self.valid_document()
            document["delivery_authority"] = value
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(document)
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_DELIVERY_AUTHORITY,
                repr(value),
            )

    def test_non_object_and_non_string_key_refused(self):
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_structure(["not", "a"])
        self.assertEqual(
            caught.exception.problem,
            authorization.PROBLEM_NOT_AN_OBJECT,
        )
        document = self.valid_document()
        document[7] = "x"
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_structure(document)
        self.assertEqual(
            caught.exception.problem,
            authorization.PROBLEM_BAD_KEY_TYPE,
        )

    def test_deep_walk_refuses_nested_strategy_keys(self):
        # I3: the structure check sees only the top level; a plan can
        # arrive as the VALUE of a permitted key. The deep walk
        # refuses forbidden key names at any depth.
        cases = [
            {"plan": ["step 1"]},
            [{"files": ["a.py"]}],
            {"nested": {"deeper": {"steps": []}}},
            [[{"ｐｌａｎ": 1}]],  # normalization applies at depth too
        ]
        for value in cases:
            document = self.valid_document()
            document["objective"] = value
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_values_deep(
                    document
                )
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_NESTED_FORBIDDEN_KEY,
                repr(value)[:40],
            )

    def test_deep_walk_accepts_clean_nested_values(self):
        document = self.valid_document()
        document["constraints"] = {"budget": ["bounded", {"note": "x"}]}
        authorization.validate_authorization_values_deep(document)

    def test_deep_walk_bounds_are_pinned_and_refused(self):
        self.assertEqual(authorization.MAX_AUTHORIZATION_DEPTH, 8)
        self.assertEqual(authorization.MAX_AUTHORIZATION_NODES, 512)
        deep = "leaf"
        for _ in range(authorization.MAX_AUTHORIZATION_DEPTH + 1):
            deep = [deep]
        document = self.valid_document()
        document["objective"] = deep
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_values_deep(document)
        self.assertEqual(
            caught.exception.problem, authorization.PROBLEM_TOO_DEEP
        )
        document = self.valid_document()
        document["objective"] = [
            "x"
        ] * (authorization.MAX_AUTHORIZATION_NODES + 1)
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_values_deep(document)
        self.assertEqual(
            caught.exception.problem,
            authorization.PROBLEM_TOO_MANY_NODES,
        )
        message = str(caught.exception)
        self.assertIn(
            str(authorization.MAX_AUTHORIZATION_NODES), message
        )

    def test_forbidden_key_wins_over_unknown_key(self):
        # Pins guard order: (b) runs first, so its refusal does not
        # depend on the closed-set guard existing at all.
        document = self.valid_document()
        document["plan"] = []
        document["totally_unknown"] = []
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_structure(document)
        self.assertEqual(
            caught.exception.problem,
            authorization.PROBLEM_FORBIDDEN_KEY,
        )


def v1_state_document(approvals=None):
    return {
        "state_schema_version": 1,
        "update_offset": 7,
        "sessions": {},
        "approvals": approvals if approvals is not None else {},
        "queue": [],
        "in_flight": None,
        "last_request": None,
        "sessions_dropped_total": 0,
    }


def v1_approval(approval_id, superseded=False):
    return {
        "approval_id": approval_id,
        "user_id": 42,
        "chat_id": 42,
        "repository": "/repo",
        "request_id": "req-1",
        "session_id": "sess-1",
        "plan_message_id": 10,
        "plan_body": "plan",
        "plan_digest_sha256": "d" * 64,
        "nonce": "n" * 64,
        "created_at": 1,
        "expires_at": 2,
        "consumed_at": None,
        "consumed_by_update_id": None,
        "decision": None,
        "superseded": superseded,
    }


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, state.STATE_FILE_NAME)
        self.backup_path = self.path + migrate.BACKUP_SUFFIX

    def write_state(self, document):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_migrates_v1_marks_approvals_and_keeps_backup(self):
        approvals = {
            "a1": v1_approval("a1", superseded=False),
            "a2": v1_approval("a2", superseded=True),
        }
        self.write_state(v1_state_document(approvals))
        original = self.read_bytes(self.path)
        changed, message = migrate.migrate_state(self.tmp.name)
        self.assertTrue(changed)
        self.assertIn(self.backup_path, message)
        self.assertIn("2 pre-existing approval", message)
        # Backup exists and is byte-exact. (assertTrue first so a
        # backup-skipping mutant dies by FAIL, not by a
        # FileNotFoundError crash.)
        self.assertTrue(
            os.path.exists(self.backup_path),
            "v1 backup was not written",
        )
        self.assertEqual(self.read_bytes(self.backup_path), original)
        # RESTART PROBE, read from disk through a FRESH store, before
        # any other save: the migrated file must load as v2 with every
        # approval marked superseded-for-v2 and v1 fields untouched.
        reloaded = state.StateStore(self.tmp.name).load()
        self.assertEqual(reloaded["state_schema_version"], 2)
        self.assertEqual(reloaded["update_offset"], 7)
        for approval_id, expected_superseded in (
            ("a1", False), ("a2", True),
        ):
            entry = reloaded["approvals"][approval_id]
            # .get so a mutant that fails to add the field dies by
            # FAIL on the assertion, not by KeyError.
            self.assertIs(
                entry.get(migrate.SUPERSEDED_FOR_V2_KEY), True,
                approval_id,
            )
            self.assertIs(
                entry["superseded"], expected_superseded, approval_id
            )
        # Only the one new field was added; every v1 field survives
        # byte-for-byte in value.
        original_document = json.loads(original.decode("utf-8"))
        for approval_id in ("a1", "a2"):
            migrated_entry = dict(reloaded["approvals"][approval_id])
            migrated_entry.pop(migrate.SUPERSEDED_FOR_V2_KEY, None)
            self.assertEqual(
                migrated_entry,
                original_document["approvals"][approval_id],
            )

    def test_rerun_on_v2_is_a_noop_and_says_so(self):
        self.write_state(v1_state_document())
        migrate.migrate_state(self.tmp.name)
        before = self.read_bytes(self.path)
        try:
            changed, message = migrate.migrate_state(self.tmp.name)
        except migrate.MigrationError as exc:
            # try/fail so a mutant that deletes the no-op path dies
            # by FAIL on this guarantee, not by an unhandled ERROR.
            self.fail(
                "re-running on a v2 file must be a clean no-op;"
                " raised %s" % exc
            )
        self.assertFalse(changed)
        # Pin the specific already-v2 message, not just "nothing":
        # the missing-file no-op message also contains "nothing"
        # (sweep finding, round 3).
        self.assertIn("already at schema version", message)
        self.assertEqual(self.read_bytes(self.path), before)

    def test_missing_state_file_is_an_honest_noop(self):
        changed, message = migrate.migrate_state(self.tmp.name)
        self.assertFalse(changed)
        self.assertIn("nothing to migrate", message)
        self.assertFalse(os.path.exists(self.path))

    def test_unknown_version_is_refused_untouched(self):
        document = v1_state_document()
        document["state_schema_version"] = 3
        self.write_state(document)
        before = self.read_bytes(self.path)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)
        self.assertEqual(self.read_bytes(self.path), before)
        self.assertFalse(os.path.exists(self.backup_path))

    def test_bool_version_is_refused(self):
        document = v1_state_document()
        document["state_schema_version"] = True
        self.write_state(document)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)

    def test_malformed_json_is_refused_and_never_reinitialized(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{torn")
        before = self.read_bytes(self.path)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)
        self.assertEqual(self.read_bytes(self.path), before)
        self.assertFalse(os.path.exists(self.backup_path))

    def test_malformed_v1_shape_is_refused(self):
        document = v1_state_document()
        document["approvals"] = []
        self.write_state(document)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)

    def test_non_object_approval_is_refused(self):
        document = v1_state_document({"a1": "not-a-record"})
        self.write_state(document)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)

    def test_foreign_backup_is_never_overwritten(self):
        self.write_state(v1_state_document())
        with open(self.backup_path, "wb") as handle:
            handle.write(b"someone else's preserved bytes")
        state_before = self.read_bytes(self.path)
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.migrate_state(self.tmp.name)
        self.assertIn("refusing to overwrite", str(caught.exception))
        self.assertEqual(self.read_bytes(self.path), state_before)
        self.assertEqual(
            self.read_bytes(self.backup_path),
            b"someone else's preserved bytes",
        )

    def test_identical_backup_resumes_interrupted_migration(self):
        self.write_state(v1_state_document())
        original = self.read_bytes(self.path)
        with open(self.backup_path, "wb") as handle:
            handle.write(original)
        changed, _ = migrate.migrate_state(self.tmp.name)
        self.assertTrue(changed)
        self.assertEqual(self.read_bytes(self.backup_path), original)
        reloaded = state.StateStore(self.tmp.name).load()
        self.assertEqual(reloaded["state_schema_version"], 2)

    def test_interrupted_rewrite_preserves_v1_file(self):
        self.write_state(v1_state_document())
        original = self.read_bytes(self.path)
        with patch("os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                migrate.migrate_state(self.tmp.name)
        # The v1 file is byte-identical and the backup is preserved,
        # so re-running the migration completes it.
        self.assertEqual(self.read_bytes(self.path), original)
        self.assertEqual(self.read_bytes(self.backup_path), original)
        changed, _ = migrate.migrate_state(self.tmp.name)
        self.assertTrue(changed)
        reloaded = state.StateStore(self.tmp.name).load()
        self.assertEqual(reloaded["state_schema_version"], 2)

    def test_readonly_directory_fails_closed_untouched(self):
        # N7: a filesystem error must leave the state file untouched.
        # migrate_state itself lets the OSError propagate; the CLI
        # presents it actionably (tested in the CLI suite).
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        self.write_state(v1_state_document())
        before = self.read_bytes(self.path)
        os.chmod(self.tmp.name, 0o500)
        self.addCleanup(os.chmod, self.tmp.name, 0o700)
        with self.assertRaises(OSError):
            migrate.migrate_state(self.tmp.name)
        os.chmod(self.tmp.name, 0o700)
        self.assertEqual(self.read_bytes(self.path), before)
        self.assertFalse(os.path.exists(self.backup_path))

    def test_v2_file_that_does_not_validate_is_refused(self):
        document = v1_state_document()
        document["state_schema_version"] = 2
        del document["approvals"]
        self.write_state(document)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_state(self.tmp.name)


class StoreLockTests(unittest.TestCase):
    def test_exclusive_store_lock_excludes_a_second_holder(self):
        import threading
        with tempfile.TemporaryDirectory() as base:
            entered = threading.Event()
            released = threading.Event()
            progressed = threading.Event()

            def contender():
                entered.set()
                with store.exclusive_store_lock(base):
                    progressed.set()

            with store.exclusive_store_lock(base):
                lock_path = os.path.join(
                    base, store.WORKFLOWS_LOCK_FILE_NAME
                )
                self.assertTrue(os.path.exists(lock_path))
                thread = threading.Thread(target=contender)
                thread.start()
                self.assertTrue(entered.wait(timeout=5))
                # Independent bound (test-side, permitted): while the
                # lock is held the contender must NOT progress.
                self.assertFalse(progressed.wait(timeout=0.3))
                released.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(progressed.is_set())


class ClosedSetValuePinTests(unittest.TestCase):
    """Round-3 sweep yield: the B3 class applies to closed SETS too.

    Tests that iterate a set (or consult the transition table) derive
    their expectations FROM it, so silently widening or narrowing the
    set would keep them green. These pins hold every closed set's
    exact CONTENT against independent literals; changing one is a
    deliberate, reviewed act that updates this table.
    """

    def test_phase_and_transition_table_content_is_pinned(self):
        self.assertEqual(
            record.PHASES,
            ("PLANNED", "AUTHORIZED", "WORKSPACE_READY", "PREPARED",
             "VALIDATED", "DISPATCHED", "VERIFIED", "COMPLETED",
             "BLOCKED", "NEEDS_REAUTHORIZATION"),
        )
        self.assertEqual(
            record.TERMINAL_PHASES, ("COMPLETED", "BLOCKED")
        )
        self.assertEqual(
            record.ALLOWED_TRANSITIONS,
            {
                "PLANNED": frozenset(("AUTHORIZED", "BLOCKED")),
                "AUTHORIZED": frozenset(
                    ("WORKSPACE_READY", "NEEDS_REAUTHORIZATION",
                     "BLOCKED")
                ),
                "WORKSPACE_READY": frozenset(
                    ("PREPARED", "NEEDS_REAUTHORIZATION", "BLOCKED")
                ),
                "PREPARED": frozenset(
                    ("VALIDATED", "NEEDS_REAUTHORIZATION", "BLOCKED")
                ),
                "VALIDATED": frozenset(
                    ("DISPATCHED", "NEEDS_REAUTHORIZATION", "BLOCKED")
                ),
                "DISPATCHED": frozenset(
                    ("VERIFIED", "NEEDS_REAUTHORIZATION", "BLOCKED")
                ),
                "VERIFIED": frozenset(("COMPLETED", "BLOCKED")),
                "COMPLETED": frozenset(),
                "BLOCKED": frozenset(),
                "NEEDS_REAUTHORIZATION": frozenset(
                    ("AUTHORIZED", "BLOCKED")
                ),
            },
        )

    def test_closed_value_sets_are_pinned(self):
        self.assertEqual(record.ISSUE_OR_PR_KINDS, ("issue", "pr"))
        self.assertEqual(
            record.APPROVAL_KINDS, ("mission_authorization_v2",)
        )
        self.assertEqual(record.DECISIONS, ("approve", "reject"))
        self.assertEqual(
            record.RECEIPT_KINDS, ("preparation", "evidence")
        )
        self.assertEqual(
            record.TURN_ROLES,
            ("planning", "prepare", "handoff_validation",
             "status_recovery", "verification", "follow_up"),
        )
        self.assertEqual(
            record.AMBIGUITY_STATES, ("none", "crash_uncertain")
        )
        self.assertEqual(record.DELIVERY_AUTHORITY_NONE, "none")
        self.assertEqual(
            record.WORKFLOW_ID_ALPHABET,
            frozenset("abcdefghijklmnopqrstuvwxyz0123456789-"),
        )

    def test_authorization_key_sets_are_pinned(self):
        self.assertEqual(
            authorization.ALLOWED_AUTHORIZATION_KEYS,
            frozenset(
                ("objective", "constraints", "rules",
                 "desired_outcome", "acceptance",
                 "unresolved_questions", "execution_scope",
                 "control", "target",
                 "issue_or_pr", "baseline", "handoff",
                 "telegram_approval", "workflow_id", "human_intent",
                 "revision", "delivery_authority")
            ),
        )
        self.assertEqual(
            authorization.FORBIDDEN_STRATEGY_KEYS,
            frozenset(
                ("plan", "steps", "files", "file_changes",
                 "implementation", "strategy", "decomposition",
                 "roles", "role_assignment", "sequencing", "tasks",
                 "subtasks", "approach", "design", "patch", "diff")
            ),
        )


def derive_product_python_files(repo_root):
    """Every product .py in the tree, DERIVED — never enumerated.

    Excludes tests/, the orchestration engine, roles/, scripts/,
    caches, and dot-directories. Shared by the bound-constant pin
    below and the E-2 carve-out caller-set pin (test_role_turn.py),
    so a new product file — dirun.py, target_runtime/*.py, a nested
    package — is inside BOTH pins the moment it exists.
    """
    excluded = {"tests", "herdr", "roles", "scripts", "__pycache__"}
    return sorted(
        path for path in repo_root.rglob("*.py")
        if not any(
            part in excluded or part.startswith(".")
            for part in path.relative_to(repo_root).parts
        )
    )


class BoundConstantPinTests(unittest.TestCase):
    """Round-06 finding B1-SYSTEMIC (hardened per round-07): the
    structural close of the unpinned-bound / fixture-derived-from-
    constant class, found FOUR times in this task.

    The constant set is DERIVED by AST-walking EVERY product .py in
    the tree (derive_product_python_files — not an enumerated package
    list) over the WHOLE module AST (ast.walk, tuple targets
    included, so conditional/nested/multi-target assignment shapes
    cannot escape) for names matching the bound-naming conventions in
    use: MAX_*, CANONICAL_*, *_SECONDS, *_CHARS, *_CHUNKS. Every
    derived constant must appear in the exact-value PINNED table (or
    the justified EXCLUDED table, keyed by (module, name) with a
    NON-EMPTY justification — never silently absent, never exempted
    by bare name), and every table entry must still exist.

    STATED RESIDUAL LIMIT: no name-based heuristic can be complete.
    A future constant named OUTSIDE these conventions (e.g.
    RETRY_LIMIT) escapes this net — choosing such a name must be a
    conscious, reviewed act by whoever adds it, not an accident this
    test can catch. Extending the convention tuple is the remedy.
    """

    # module relpath -> {constant name: exact pinned value}
    PINNED = {
        "telegram_operator/adapter.py": {
            "POLL_FAILURE_BACKOFF_BASE_SECONDS": 1,
            "POLL_FAILURE_BACKOFF_CEILING_SECONDS": 60,
        },
        "telegram_operator/approval.py": {
            "APPROVAL_VALIDITY_SECONDS": 900,
        },
        "telegram_operator/launchagent.py": {
            "THROTTLE_SECONDS": 10,
        },
        "telegram_operator/mission.py": {
            "MISSION_APPROVAL_VALIDITY_SECONDS": 900,
            "CANONICAL_TARGET_HOST": "github.com",
            "MAX_AUTHORITY_FIELD_CHARS": 8000,
            "MAX_TARGET_NAME_CHARS": 100,
        },
        "telegram_operator/protocol.py": {
            "MAX_OUTCOME_DETAIL_CHARS": 2000,
            "MAX_ENVELOPE_CHARS": 16384,
            "MAX_INTENT_CHARS": 4000,
        },
        "telegram_operator/state.py": {
            "MAX_QUEUE_DEPTH": 16,
            "MAX_APPROVAL_RECORDS": 64,
            "MAX_SESSION_ENTRIES": 64,
        },
        "telegram_operator/telegram_api.py": {
            "LONG_POLL_SECONDS": 50,
            "SOCKET_DEADLINE_MARGIN_SECONDS": 10,
            "SOCKET_DEADLINE_SECONDS": 60,
            "MAX_MESSAGE_CHARS": 4096,
            "MAX_MESSAGE_CHUNKS": 5,
            "MAX_DELIVERABLE_CHARS": 20480,
            "MAX_SEND_ATTEMPTS": 3,
            "RETRY_BACKOFF_BASE_SECONDS": 1,
            "RETRY_BACKOFF_CEILING_SECONDS": 30,
            "MAX_RESPONSE_BYTES": 1048576,
            "MAX_PROBLEM_CHARS": 500,
        },
        "codex_gateway/contract.py": {
            "ERROR_DETAIL_MAX_CHARS": 2000,
        },
        "codex_gateway/role_turn.py": {
            "MAX_EVIDENCE_SECTION_CHARS": 262144,
        },
        "workflow_authority/authorization.py": {
            "MAX_AUTHORIZATION_DEPTH": 8,
            "MAX_AUTHORIZATION_NODES": 512,
        },
        "workflow_authority/record.py": {
            "MAX_ID_CHARS": 128,
            "MAX_TELEGRAM_MESSAGE_IDS": 64,
            "MAX_RECEIPTS": 256,
            "MAX_CODEX_TURNS": 256,
            "MAX_BOUNDED_SUMMARY_CHARS": 2000,
            "MAX_AMBIGUITY_DETAIL_CHARS": 2000,
            "MAX_AUTHORITY_TEXT_CHARS": 16384,
            "MAX_AUTHORITY_FIELD_CHARS": 8000,
            "MAX_HUMAN_INTENT_CHARS": 4000,
            "MAX_VERIFIED_SUMMARY_CHARS": 4000,
        },
        "workflow_authority/canonical.py": {
            "CANONICAL_TARGET_HOST": "github.com",
            "MAX_TARGET_URL_CHARS": 512,
            "MAX_TARGET_NAME_CHARS": 100,
        },
        "workflow_authority/store.py": {
            "MAX_WORKFLOW_RECORDS": 64,
        },
        # P1-A6 Verified PR Delivery. Every bound is a hard constant of
        # the delivery record, the sibling store, or the transport.
        "pr_delivery/authorization.py": {
            "MAX_CANDIDATE_ENTRIES": 4096,
            "MAX_PR_TITLE_CHARS": 256,
            "MAX_PR_BODY_CHARS": 16384,
            "MAX_AUTHORIZATION_VALIDITY_SECONDS": 604800,
            "DEFAULT_AUTHORIZATION_VALIDITY_SECONDS": 86400,
            "MAX_HUMAN_TEXT_CHARS": 8000,
            "MAX_EVIDENCE_TEXT_CHARS": 4000,
            "MAX_STEP_ATTEMPTS": 8,
            # The canonical URL bound plus the optional ".git" suffix.
            "MAX_REMOTE_URL_CHARS": 516,
            "MAX_REVERIFICATION_ARGV": 64,
        },
        "pr_delivery/store.py": {
            "MAX_PR_DELIVERY_RECORDS": 64,
        },
        "pr_delivery/transport.py": {
            "MAX_TRANSPORT_OUTPUT_BYTES": 1048576,
        },
        "pr_delivery/cli.py": {
            # Not a bound: the number of candidate-identity hex characters
            # the human types in the ceremony (a confirmation, not a
            # credential). Pinned because the name matches the convention.
            "CONFIRMATION_CHARS": 12,
        },
        "target_runtime/cli.py": {
            "RUNTIME_POLL_INTERVAL_SECONDS": 5,
        },
        "target_runtime/prepare.py": {
            "MAX_INSTRUCTION_FILES": 8,
            "MAX_INSTRUCTION_FILE_BYTES": 65536,
        },
        "target_runtime/git_transport.py": {
            "MAX_DIFF_RETAINED_BYTES": 65536,
            "MAX_DIFF_TOTAL_BYTES": 33554432,
            "MAX_PORCELAIN_CAPTURE_BYTES": 1048576,
        },
        "target_runtime/evidence.py": {
            "MAX_CHANGED_PATHS_LISTED": 200,
            "MAX_LISTED_PATH_CHARS": 4096,
            "MAX_MARKER_CHARS": 4000,
            "MAX_MARKER_LINES": 120,
            "MAX_DIAGNOSTIC_DETAIL_CHARS": 500,
            "MAX_PROTECTED_SURFACE_FILES": 512,
            "MAX_PROTECTED_SURFACE_FILE_BYTES": 2097152,
            "MAX_PROTECTED_SURFACE_TOTAL_BYTES": 16777216,
            "MAX_BLOCKING_DIAGNOSTICS": 64,
        },
        "target_runtime/dispatch.py": {
            "MAX_FOLLOW_UP_DISPATCHES": 2,
        },
        "target_runtime/capability.py": {
            "MAX_CAPABILITIES": 256,
            "CAPABILITY_VALIDITY_SECONDS": 900,
        },
        "target_runtime/runtime.py": {
            "REQUEST_VALIDITY_SECONDS": 900,
        },
        "target_runtime/workspace_trust.py": {
            # Both derived from the installed Claude CLI's own
            # proper-lockfile options for the global config write
            # (`stale: 1e4`), not chosen here: DI must agree with the
            # staleness window the CLI already enforces.
            "LOCK_STALE_SECONDS": 10.0,
            "LOCK_RETRY_SECONDS": 0.02,
            "MAX_LOCK_ATTEMPTS": 100,
        },
        "target_runtime/evidence_preservation.py": {
            # R-37/R-38. Bounds on the forensic archive captured
            # before terminal cleanup reclaims a workspace. They cap
            # a COPY, and every one of them is disclosed in the
            # projection itself — a truncated file, an over-cap
            # listing and an unreadable entry all make the archive
            # report itself INCOMPLETE, derived from the entries
            # rather than asserted beside them.
            "MAX_FILE_BYTES": 65536,
            "MAX_FILES": 32,
            "MAX_TOTAL_BYTES": 1048576,
        },
        "target_runtime/process_ownership.py": {
            # I5 / R-14. Both bound a REAP — local cleanup of a
            # process this component started — and neither is
            # reachable from a mission's execution path, so I3's
            # no-mission-timeout line is untouched. The settle window
            # is how long a group is watched after SIGKILL before the
            # tree is reported NOT reaped rather than assumed gone.
            "REAP_SETTLE_SECONDS": 5.0,
            "REAP_POLL_SECONDS": 0.02,
            # R-43 AG-1. Not a time bound: how many hex characters of
            # the control identity digest ride in a scope NAME, to
            # keep two deployments sharing one machine-global base
            # out of each other's record space. The name is a label —
            # the assignment record carries the FULL control identity
            # and verification compares against that — so this bounds
            # a disambiguator, not a credential. A collision is
            # refused rather than silently inherited.
            "CONTROL_DIGEST_CHARS": 16,
        },
        "target_runtime/readiness.py": {
            # I3. Within this module the BOOTSTRAP bound is the only
            # bound constant: it measures
            # the window from the durable dispatch timestamp to the
            # first positively evidenced readiness, and it stops
            # applying for good once readiness is evidenced. No
            # engineering mission is bounded by it, which
            # `EngineeringRunsForHoursTests` and
            # `test_no_mission_timer_behavioral` both drive.
            "BOOTSTRAP_MAX_SECONDS": 900,
        },
    }

    # (module relpath, constant name) -> NON-EMPTY justification.
    # Keyed by module AND name: a bare name could silently exempt a
    # same-named constant in a different module (round-07 (a)).
    # Empty today: every convention-matching constant is a genuine
    # pinned bound.
    EXCLUDED = {}

    @staticmethod
    def _matches_convention(name):
        return not name.startswith("_") and (
            name.startswith(("MAX_", "CANONICAL_"))
            or name.endswith(("_SECONDS", "_CHARS", "_CHUNKS"))
        )

    def _derive(self):
        import ast as ast_module
        from pathlib import Path
        repo_root = Path(store.__file__).resolve().parent.parent
        derived = {}
        for path in derive_product_python_files(repo_root):
            relpath = path.relative_to(repo_root).as_posix()
            tree = ast_module.parse(path.read_text())
            # ast.walk over the WHOLE tree (round-07 (c)): a constant
            # assigned inside an if/try block, re-exported, annotated,
            # or bound through a tuple target is still derived.
            for node in ast_module.walk(tree):
                targets = []
                if isinstance(node, ast_module.Assign):
                    for target in node.targets:
                        if isinstance(target, ast_module.Name):
                            targets.append(target)
                        elif isinstance(
                            target,
                            (ast_module.Tuple, ast_module.List),
                        ):
                            targets.extend(
                                element for element in target.elts
                                if isinstance(
                                    element, ast_module.Name
                                )
                            )
                elif isinstance(
                    node, ast_module.AnnAssign
                ) and isinstance(node.target, ast_module.Name):
                    targets = [node.target]
                for target in targets:
                    if self._matches_convention(target.id):
                        derived.setdefault(relpath, set()).add(
                            target.id
                        )
        return derived

    def _check_tables(self, derived, pinned, excluded):
        """All table invariants, factored so a test-of-the-test can
        drive them with doctored tables (round-07: the stale-entry
        half needs its own killing check)."""
        import importlib
        # Derivation guards: never silently empty, known members
        # present.
        self.assertTrue(derived)
        for relpath, name in (
            ("telegram_operator/protocol.py", "MAX_ENVELOPE_CHARS"),
            ("telegram_operator/mission.py",
             "MISSION_APPROVAL_VALIDITY_SECONDS"),
            ("workflow_authority/store.py", "MAX_WORKFLOW_RECORDS"),
        ):
            self.assertIn(relpath, derived)
            self.assertIn(name, derived[relpath])
        # Every EXCLUDED entry carries a non-empty justification.
        for key, justification in excluded.items():
            self.assertIsInstance(key, tuple, key)
            self.assertEqual(len(key), 2, key)
            self.assertTrue(
                isinstance(justification, str)
                and justification.strip(),
                "EXCLUDED entry %r must carry a non-empty"
                " justification" % (key,),
            )
        # Every derived constant is pinned (or explicitly excluded
        # for exactly this module).
        for relpath in sorted(derived):
            for name in sorted(derived[relpath]):
                if (relpath, name) in excluded:
                    continue
                self.assertIn(
                    relpath, pinned,
                    "new module %s has bound constants; add exact"
                    " value pins for them here" % relpath,
                )
                self.assertIn(
                    name, pinned[relpath],
                    "UNPINNED bound constant %s in %s: add an exact"
                    " value pin here (or a justified EXCLUDED entry)"
                    " before shipping it" % (name, relpath),
                )
                module = importlib.import_module(
                    relpath[:-3].replace("/", ".")
                )
                self.assertEqual(
                    getattr(module, name),
                    pinned[relpath][name],
                    "%s.%s drifted from its pinned value" % (
                        relpath, name,
                    ),
                )
        # No stale table entries: every pinned/excluded entry must
        # still exist in the tree.
        for relpath, names in pinned.items():
            for name in names:
                self.assertIn(relpath, derived, relpath)
                self.assertIn(
                    name, derived[relpath],
                    "stale pin: %s.%s no longer exists" % (
                        relpath, name,
                    ),
                )
        for relpath, name in excluded:
            self.assertIn(
                relpath, derived,
                "stale exclusion: %s no longer exists" % relpath,
            )
            self.assertIn(
                name, derived[relpath],
                "stale exclusion: %s.%s no longer exists" % (
                    relpath, name,
                ),
            )

    def test_every_bound_constant_is_value_pinned(self):
        self._check_tables(self._derive(), self.PINNED, self.EXCLUDED)

    def test_table_guard_violations_are_detected(self):
        # Test-of-the-test (round-07): each guard must FIRE on a
        # doctored table — otherwise a mutant deleting the guard
        # (reviewer SP4) survives because today's tables are clean.
        derived = self._derive()
        # (1) stale PINNED entry
        doctored = dict(self.PINNED)
        doctored["telegram_operator/mission.py"] = dict(
            doctored["telegram_operator/mission.py"],
            MAX_GONE_CHARS=1,
        )
        with self.assertRaises(AssertionError):
            self._check_tables(derived, doctored, self.EXCLUDED)
        # (2) stale EXCLUDED entry
        with self.assertRaises(AssertionError):
            self._check_tables(
                derived, self.PINNED,
                {("telegram_operator/mission.py", "MAX_GONE_CHARS"):
                 "was removed"},
            )
        # (3) empty justification
        with self.assertRaises(AssertionError):
            self._check_tables(
                derived, self.PINNED,
                {("telegram_operator/mission.py",
                  "MAX_AUTHORITY_FIELD_CHARS"): "  "},
            )
        # (4) unpinned derived constant
        doctored_derived = {
            relpath: set(names) for relpath, names in derived.items()
        }
        doctored_derived.setdefault(
            "telegram_operator/mission.py", set()
        ).add("MAX_UNPINNED_DEMO_CHARS")
        with self.assertRaises(AssertionError):
            self._check_tables(
                doctored_derived, self.PINNED, self.EXCLUDED
            )
        # (5) a bare-name-style exclusion cannot exempt: the same
        # name excluded for a DIFFERENT module does not cover it.
        with self.assertRaises(AssertionError):
            self._check_tables(
                doctored_derived, self.PINNED,
                {("workflow_authority/record.py",
                  "MAX_UNPINNED_DEMO_CHARS"): "other module"},
            )


class CrossModuleInvariantTests(unittest.TestCase):
    def test_hard_bound_values_are_pinned(self):
        # Round-2 review finding B3: every hard bound's exact VALUE
        # is pinned against an independent literal, so a widening —
        # realistic or huge — is a FAIL, never a green suite and
        # never a scaled fixture. Changing a bound is a deliberate,
        # reviewed act that updates this table.
        self.assertEqual(record.MAX_ID_CHARS, 128)
        self.assertEqual(record.MAX_TELEGRAM_MESSAGE_IDS, 64)
        self.assertEqual(record.MAX_RECEIPTS, 256)
        self.assertEqual(record.MAX_CODEX_TURNS, 256)
        self.assertEqual(record.MAX_BOUNDED_SUMMARY_CHARS, 2000)
        self.assertEqual(record.MAX_AMBIGUITY_DETAIL_CHARS, 2000)
        self.assertEqual(record.MAX_AUTHORITY_TEXT_CHARS, 16384)
        self.assertEqual(store.MAX_WORKFLOW_RECORDS, 64)

    def test_authority_text_bound_matches_envelope_bound(self):
        # MAX_AUTHORITY_TEXT_CHARS deliberately equals the remote
        # protocol envelope bound: an authority text this layer
        # accepted but no envelope could carry would be an
        # authorization that can never be displayed. This is an
        # enforced invariant, not a coincidence.
        self.assertEqual(
            record.MAX_AUTHORITY_TEXT_CHARS,
            protocol.MAX_ENVELOPE_CHARS,
        )

    def test_human_intent_bound_matches_transport_intent_bound(self):
        # The stored human intent is exactly the text the transport
        # accepted: a wider record bound could store nothing the
        # transport allows and a narrower one would refuse accepted
        # intent. Enforced invariant, value-pinned on both sides.
        self.assertEqual(record.MAX_HUMAN_INTENT_CHARS, 4000)
        self.assertEqual(
            record.MAX_HUMAN_INTENT_CHARS,
            protocol.MAX_INTENT_CHARS,
        )

    def test_quote_prefix_matches_transport_neutralization_prefix(self):
        # The rendering quotes every intent line with the SAME prefix
        # the transport uses to neutralize marker-bearing user lines,
        # so the human reads one consistent quoting convention and no
        # intent byte ever starts a rendered line.
        self.assertEqual(rendering.QUOTE_PREFIX, "> ")
        self.assertEqual(
            rendering.QUOTE_PREFIX, protocol.NEUTRALIZED_LINE_PREFIX
        )


class CanonicalTargetUrlTests(unittest.TestCase):
    """A4: the ONE URL canonicalizer, driven by hostile-input tables
    where every entry asserts its own distinct problem code."""

    ACCEPTED = (
        ("https://github.com/octocat/target",
         ("octocat", "target", None, None)),
        ("https://github.com/example-org/external-target/issues/42",
         ("example-org", "external-target", "issue", 42)),
        ("https://github.com/octo-cat/tar.get2/pull/17",
         ("octo-cat", "tar.get2", "pr", 17)),
    )

    # input -> the exact problem code the FIRST failing check emits.
    REFUSED = (
        (None, canonical.PROBLEM_URL_NOT_TEXT),
        ("", canonical.PROBLEM_URL_NOT_TEXT),
        ("https://github.com/" + "a" * 600,
         canonical.PROBLEM_URL_TOO_LONG),
        ("https://github.com/octocat/tar\x00get",
         canonical.PROBLEM_URL_CONTROL_CHARACTER),
        ("https://github.com/octocat/tar\nget",
         canonical.PROBLEM_URL_CONTROL_CHARACTER),
        # Non-ASCII gets its OWN code (round-01 F-5): a raw
        # IDN/homoglyph host or path is not a control character.
        ("https://github.com/octocat/targét",
         canonical.PROBLEM_URL_NON_ASCII),
        ("https://gіthub.com/octocat/target",  # Cyrillic і
         canonical.PROBLEM_URL_NON_ASCII),
        ("https://github.com/octocat/target;rm",
         canonical.PROBLEM_URL_SHELL_CHARACTER),
        ("https://github.com/octo cat/target",
         canonical.PROBLEM_URL_SHELL_CHARACTER),
        ("https://github.com/octocat/tar%2Eget",
         canonical.PROBLEM_URL_PERCENT_ENCODED),
        ("https://github.com/octocat/target%2e%2e",
         canonical.PROBLEM_URL_PERCENT_ENCODED),
        ("https://github.com/ahttps://github.com/b",
         canonical.PROBLEM_URL_MULTI_TARGET),
        ("//github.com/octocat/target",
         canonical.PROBLEM_URL_SCHEME_RELATIVE),
        ("http://github.com/octocat/target",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("HTTPS://github.com/octocat/target",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("ssh://github.com/octocat/target",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("git://github.com/octocat/target",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("file:///etc/passwd",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("javascript:x",
         canonical.PROBLEM_URL_UNSUPPORTED_SCHEME),
        ("https://user@github.com/octocat/target",
         canonical.PROBLEM_URL_USERINFO),
        ("https://github.com:443/octocat/target",
         canonical.PROBLEM_URL_PORT),
        ("https://xn--gthub-zra.com/octocat/target",
         canonical.PROBLEM_URL_PUNYCODE),
        ("https://GitHub.com/octocat/target",
         canonical.PROBLEM_URL_HOST_CASE),
        ("https://github.com.evil.tld/octocat/target",
         canonical.PROBLEM_URL_HOST_CONFUSABLE),
        ("https://gitlab.com/octocat/target",
         canonical.PROBLEM_URL_WRONG_HOST),
        ("https://github.com/octocat/target?ref=x",
         canonical.PROBLEM_URL_QUERY),
        ("https://github.com/octocat/target#second",
         canonical.PROBLEM_URL_FRAGMENT),
        ("https://github.com/octocat/target/../other",
         canonical.PROBLEM_URL_TRAVERSAL),
        ("https://github.com/octocat/./target",
         canonical.PROBLEM_URL_TRAVERSAL),
        ("https://github.com/octocat/target/",
         canonical.PROBLEM_URL_EMPTY_SEGMENT),
        ("https://github.com//octocat/target",
         canonical.PROBLEM_URL_EMPTY_SEGMENT),
        ("https://github.com/.octocat/target",
         canonical.PROBLEM_URL_BAD_NAME),
        ("https://github.com/-octocat/target",
         canonical.PROBLEM_URL_BAD_NAME),
        ("https://github.com/octo+cat/target",
         canonical.PROBLEM_URL_BAD_NAME),
        ("https://github.com/" + "a" * 101 + "/target",
         canonical.PROBLEM_URL_BAD_NAME),
        ("https://github.com/octocat/target.git",
         canonical.PROBLEM_URL_GIT_SUFFIX),
        ("https://github.com",
         canonical.PROBLEM_URL_PATH_SHAPE),
        ("https://github.com/octocat",
         canonical.PROBLEM_URL_PATH_SHAPE),
        ("https://github.com/octocat/target/wiki",
         canonical.PROBLEM_URL_PATH_SHAPE),
        ("https://github.com/octocat/target/issues/7/comments",
         canonical.PROBLEM_URL_PATH_SHAPE),
        ("https://github.com/octocat/target/issues/007",
         canonical.PROBLEM_URL_BAD_NUMBER),
        ("https://github.com/octocat/target/issues/0",
         canonical.PROBLEM_URL_BAD_NUMBER),
        ("https://github.com/octocat/target/pull/abc",
         canonical.PROBLEM_URL_BAD_NUMBER),
    )

    def test_accepted_forms_parse_and_are_idempotent(self):
        for url, (owner, repo, kind, number) in self.ACCEPTED:
            target = canonical.canonicalize_target_url(url)
            self.assertEqual(target.owner, owner, url)
            self.assertEqual(target.repo, repo, url)
            self.assertEqual(target.kind, kind, url)
            self.assertEqual(target.number, number, url)
            self.assertEqual(target.host, "github.com", url)
            self.assertEqual(target.canonical_url, url, url)
            # Idempotence: re-canonicalizing the canonical value (and
            # the repository identity) yields the identical result.
            self.assertEqual(
                canonical.canonicalize_target_url(
                    target.canonical_url
                ),
                target, url,
            )
            self.assertEqual(
                canonical.canonicalize_target_url(
                    target.repository_url
                ).repository_url,
                target.repository_url, url,
            )

    def test_every_hostile_input_is_refused_with_its_own_code(self):
        for url, expected_problem in self.REFUSED:
            # try/fail so a mutant that lets one input through dies
            # by FAIL on THIS entry, never by a downstream crash.
            try:
                canonical.canonicalize_target_url(url)
            except canonical.CanonicalizationError as caught:
                self.assertEqual(
                    caught.problem, expected_problem, repr(url)
                )
            except Exception as exc:
                self.fail(
                    "%r must be refused with CanonicalizationError,"
                    " got %r" % (url, exc)
                )
            else:
                self.fail("hostile URL %r was accepted" % (url,))

    def test_repository_only_wrapper_refuses_issue_forms(self):
        with self.assertRaises(
            canonical.CanonicalizationError
        ) as caught:
            canonical.canonicalize_repository_url(
                "https://github.com/octocat/target/issues/7"
            )
        self.assertEqual(
            caught.exception.problem,
            canonical.PROBLEM_URL_PATH_SHAPE,
        )

    def test_case_variants_share_one_identity_key(self):
        # Round-01 F-4 (binding for I3/I5): case-variant URLs are two
        # canonical VALUES (what the human approved is preserved) but
        # ONE repository identity — every cross-workflow identity
        # comparison must use the identity key, never URL equality.
        upper = canonical.canonicalize_target_url(
            "https://github.com/OctoCat/Target"
        )
        lower = canonical.canonicalize_target_url(
            "https://github.com/octocat/target"
        )
        self.assertNotEqual(upper.canonical_url, lower.canonical_url)
        self.assertEqual(
            canonical.repository_identity_key(upper),
            canonical.repository_identity_key(lower),
        )
        self.assertTrue(
            canonical.same_repository_identity(upper, lower)
        )
        other = canonical.canonicalize_target_url(
            "https://github.com/octocat/other"
        )
        self.assertFalse(
            canonical.same_repository_identity(lower, other)
        )
        # The key is derived, not the displayed value: it is
        # case-folded and host-qualified.
        self.assertEqual(
            canonical.repository_identity_key(upper),
            "github.com/octocat/target",
        )

    def test_refusal_messages_carry_exact_bounds(self):
        with self.assertRaises(
            canonical.CanonicalizationError
        ) as caught:
            canonical.canonicalize_target_url(
                "https://github.com/" + "a" * 600
            )
        self.assertIn(str(len("https://github.com/" + "a" * 600)),
                      str(caught.exception))
        self.assertIn(str(canonical.MAX_TARGET_URL_CHARS),
                      str(caught.exception))


class RenderBindingTests(unittest.TestCase):
    """A1: every authority field is TOTALLY bound — altering the
    stored field independently of the digested rendered text makes
    the record invalid (record.PROBLEM_RENDER_BINDING), everywhere
    validate_record runs."""

    def assert_render_binding_refusal(self, mutate, label):
        document = make_record()
        mutate(document)
        # try/fail so a mutant that drops the render-binding check
        # dies by FAIL here, never by a downstream crash.
        try:
            record.validate_record(document)
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_RENDER_BINDING, label
            )
        except Exception as exc:
            self.fail(
                "%s must fail closed with RecordError, got %r"
                % (label, exc)
            )
        else:
            self.fail(
                "%s: field altered independently of the digested"
                " text was ACCEPTED" % label
            )

    def test_every_rendered_field_is_bound(self):
        def set_path(*path, value):
            def mutate(document):
                target = document
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
            return mutate

        cases = [
            ("workflow_id", set_path("workflow_id", value="wf-0002")),
            ("mission revision",
             set_path("mission_authorization", "revision", value=2)),
            ("control realpath",
             set_path("control_identity", "repository_realpath",
                      value="/control/other")),
            ("policy digest",
             set_path("control_identity", "policy_digest_sha256",
                      value="1" * 64)),
            ("baseline ref",
             set_path("approved_baseline", "ref",
                      value="refs/heads/dev")),
            ("baseline sha",
             set_path("approved_baseline", "commit_sha",
                      value="b" * 40)),
            ("telegram user",
             set_path("telegram", "user_id", value=1002)),
            ("telegram chat",
             set_path("telegram", "chat_id", value=1002)),
            ("human intent",
             set_path("human_intent", value="something else")),
            ("handoff revision",
             set_path("handoff", "revision", value=3)),
        ]
        for key in rendering.AUTHORITY_CONTENT_KEYS:
            cases.append((
                "authority content %s" % key,
                set_path("mission_authorization", key,
                         value="ALTERED %s" % key),
            ))

        def consistent_target_swap(document):
            # Owner and URL changed CONSISTENTLY, so the target
            # cross-check passes and only the render binding stands
            # between the swapped target and acceptance.
            document["target"]["owner"] = "octofox"
            document["target"]["canonical_url"] = (
                "https://github.com/octofox/target"
            )
        cases.append(("consistent target swap",
                      consistent_target_swap))

        for label, mutate in cases:
            self.assert_render_binding_refusal(mutate, label)

    def test_cross_form_target_forgery_is_refused_both_ways(self):
        # D2/A2: neither target form can be edited into the other by
        # changing the record field independently of the digested
        # text.
        self.assert_render_binding_refusal(
            lambda document: document["target"].__setitem__(
                "issue_or_pr", None
            ),
            "issue form edited to repository-only",
        )
        repo_only = make_record(
            "wf-ro", issue_or_pr_kind=None, issue_or_pr_number=None
        )
        record.validate_record(repo_only)
        repo_only["target"]["issue_or_pr"] = {
            "kind": "issue", "number": 7,
        }
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(repo_only)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_RENDER_BINDING
        )

    def test_target_fields_must_match_the_canonical_url_parse(self):
        # owner/repo are NOT rendered (only canonical_url is), so the
        # canonicalizer cross-check is the guard that binds them: a
        # record displaying one identity while its URL names another
        # is refused with its own code — the render binding alone
        # would NOT catch this.
        for field, value in (("owner", "octofox"), ("repo", "other"),
                             ("canonical_host", "Github.com")):
            document = make_record()
            document["target"][field] = value
            try:
                record.validate_record(document)
            except record.RecordError as caught:
                self.assertEqual(
                    caught.problem, record.PROBLEM_BAD_VALUE, field
                )
                self.assertIn("canonical", str(caught), field)
            except Exception as exc:
                self.fail(
                    "target %s mismatch must fail closed with"
                    " RecordError, got %r" % (field, exc)
                )
            else:
                self.fail(
                    "target %s naming a different identity than the"
                    " URL was accepted" % field
                )

    def test_rendered_text_with_matching_digest_still_refused(self):
        # A tamper that rewrites rendered_text AND recomputes its
        # digest defeats the digest check; the render binding must
        # stand alone against it.
        document = make_record()
        altered = document["mission_authorization"][
            "rendered_text"
        ].replace("OBJECTIVE", "OBJECTIVE (amended)")
        document["mission_authorization"]["rendered_text"] = altered
        document["mission_authorization"]["digest_sha256"] = (
            digest.text_digest(altered)
        )
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_RENDER_BINDING
        )

    def test_handoff_text_tamper_dies_on_its_digest(self):
        document = make_record()
        document["handoff"]["text"] = "ALTERED HANDOFF"
        with self.assertRaises(record.RecordError) as caught:
            record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_DIGEST_MISMATCH
        )

    def test_repo_only_record_round_trips_through_the_store(self):
        # A2 at the record/store layer: the repository-only form is
        # representable, validates, persists, and reloads intact,
        # with its own DISTINCT rendered binding line.
        entry = make_record(
            "wf-ro", issue_or_pr_kind=None, issue_or_pr_number=None
        )
        self.assertIsNone(entry["target"]["issue_or_pr"])
        rendered = entry["mission_authorization"]["rendered_text"]
        self.assertIn(
            "target: https://github.com/octocat/target"
            " (repository, no issue or PR)",
            rendered.splitlines(),
        )
        self.assertNotIn("#", rendered.splitlines()[3])
        with tempfile.TemporaryDirectory() as tmp:
            wf_store = store.WorkflowStore(tmp)
            document = store.default_document()
            ok, problem, _ = store.add_workflow(document, entry)
            self.assertTrue(ok, problem)
            wf_store.save(document)
            reloaded = store.WorkflowStore(tmp).load()
            self.assertEqual(
                reloaded["workflows"]["wf-ro"], entry
            )

    def test_mixed_issue_or_pr_none_arguments_fail_closed(self):
        # Exactly one of kind/number None must refuse, never
        # fabricate or drop the other half.
        for kind, number in (("issue", None), (None, 7)):
            with self.assertRaises(record.RecordError):
                make_record(
                    "wf-mixed", issue_or_pr_kind=kind,
                    issue_or_pr_number=number,
                )


class InjectiveRenderingTests(unittest.TestCase):
    """Round-01 F-1: the rendering is INJECTIVE — distinct field
    tuples never render to identical bytes, so 'altered independently
    of the digested text' covers JOINT multi-field alterations."""

    def test_joint_repartition_of_adjacent_sections_is_refused(self):
        # The exact reviewer collision: move a header-shaped line
        # across the constraints/rules boundary. Both tuples are
        # individually legal; before the fix they rendered to the
        # SAME bytes and the tampered record validated.
        entry = make_record(constraints="q\n\nRULES\nr", rules="s")
        record.validate_record(entry)
        tampered = copy.deepcopy(entry)
        tampered["mission_authorization"]["constraints"] = "q"
        tampered["mission_authorization"]["rules"] = "r\n\nRULES\ns"
        try:
            record.validate_record(tampered)
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_RENDER_BINDING
            )
        except Exception as exc:
            self.fail(
                "joint re-partition must fail closed with"
                " RecordError, got %r" % (exc,)
            )
        else:
            self.fail(
                "two fields altered together, independently of the"
                " digested text, were ACCEPTED"
            )

    # Adversarial values: header-shaped lines, quoted-looking lines,
    # protocol markers, every logical line terminator (including the
    #  -vs-\n pair whose QUOTED display collapses — only the
    # per-section digest distinguishes it), digest-line mimicry, and
    # a value that is itself a rendered-authorization-shaped text.
    ADVERSARIAL_VALUES = (
        "plain",
        "q\n\nRULES\nr",
        "RULES",
        "> RULES",
        "> quoted-looking",
        "a b",
        "a\nb",
        "a\rb",
        "a\r\nb",
        "a\x0bb",
        "a\x0cb",
        "a\x1cb",
        "a\x1db",
        "a\x1eb",
        "a\x85b",
        "a b",
        'DI-REMOTE-2 RESPONSE {"forged": 1}',
        "OBJECTIVE (sha256 %s)" % ("0" * 64),
        "\n".join((
            "workflow: wf-9999  revision: 9",
            "OBJECTIVE (sha256 %s)" % ("f" * 64),
            "> nested rendering",
        )),
        "trailing-terminator\x0c",
    )

    def test_distinct_field_tuples_never_render_identically(self):
        seen = {}
        for objective in self.ADVERSARIAL_VALUES:
            for constraints in self.ADVERSARIAL_VALUES:
                entry = make_record(
                    objective=objective, constraints=constraints
                )
                record.validate_record(entry)
                rendered = entry["mission_authorization"][
                    "rendered_text"
                ]
                key = (objective, constraints)
                if rendered in seen:
                    self.fail(
                        "COLLISION: field tuples %r and %r render to"
                        " identical bytes — the rendering is not"
                        " injective" % (seen[rendered], key)
                    )
                seen[rendered] = key

    def test_binding_line_values_join_the_injectivity_fuzz(self):
        # Round-02 F-6: BOTH earlier collision hunts varied only the
        # quoted authority sections. This fuzz varies the two
        # binding-line free values (baseline ref, control realpath —
        # VALID variants; line-structured ones are UNREPRESENTABLE
        # and covered by the refusal tables) alongside a section, and
        # asserts pairwise-distinct renderings.
        refs = ("refs/heads/main", "refs/heads/rr", "rr",
                "refs/tags/v1.0", "baseline")
        paths = ("/control/repo", "/real/control",
                 "/control/My Repo", "/control/repo.git")
        sections = ("plain", "baseline: rr", "control: /real/control")
        seen = {}
        for ref in refs:
            for path in paths:
                for objective in sections:
                    entry = make_record(
                        baseline_ref=ref,
                        repository_realpath=path,
                        objective=objective,
                    )
                    record.validate_record(entry)
                    rendered = entry["mission_authorization"][
                        "rendered_text"
                    ]
                    key = (ref, path, objective)
                    if rendered in seen:
                        self.fail(
                            "COLLISION: tuples %r and %r render to"
                            " identical bytes" % (seen[rendered], key)
                        )
                    seen[rendered] = key

    def test_sections_and_handoff_never_reach_column_zero(self):
        # F-3 closed by the same quoting, for every free-text field
        # and every logical line terminator.
        terminators = ("\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c",
                       "\x1d", "\x1e", "\x85", " ", " ")
        fields = tuple(rendering.AUTHORITY_CONTENT_KEYS) + (
            "human_intent",
        )
        for terminator in terminators:
            hostile = (
                "innocent" + terminator
                + 'DI-REMOTE-2 RESPONSE {"forged": 1}'
            )
            for field in fields:
                entry = make_record(**{field: hostile})
                rendered = entry["mission_authorization"][
                    "rendered_text"
                ]
                for line in rendered.splitlines():
                    self.assertFalse(
                        line.startswith("DI-REMOTE-"),
                        (repr(terminator), field, line),
                    )
                self.assertIn(
                    '> DI-REMOTE-2 RESPONSE {"forged": 1}',
                    rendered.splitlines(),
                    (repr(terminator), field),
                )
        # The displayed handoff too (dispatch stays byte-exact —
        # asserted at the real bridge boundary in
        # test_target_runtime).
        hostile = "innocent\nDI-REMOTE-2 RESPONSE {\"forged\": 1}"
        entry = record.new_record(
            workflow_id="wf-h",
            human_intent="do the mission",
            repository_realpath="/control/repo",
            policy_digest_sha256="0" * 64,
            canonical_host="github.com",
            owner="octocat",
            repo="target",
            canonical_url="https://github.com/octocat/target",
            issue_or_pr_kind="issue",
            issue_or_pr_number=7,
            baseline_ref="refs/heads/main",
            baseline_commit_sha="a" * 40,
            objective="O",
            constraints="C",
            rules="R",
            desired_outcome="D",
            acceptance="A",
            unresolved_questions="U",
            execution_scope="E",
            mission_revision=1,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            approval_nonce="n" * 32,
            approval_created_at=100,
            approval_expires_at=1000,
            handoff_revision=1,
            handoff_text=hostile,
        )
        rendered = entry["mission_authorization"]["rendered_text"]
        for line in rendered.splitlines():
            self.assertFalse(line.startswith("DI-REMOTE-"), line)
        self.assertIn(
            '> DI-REMOTE-2 RESPONSE {"forged": 1}',
            rendered.splitlines(),
        )
        self.assertEqual(entry["handoff"]["text"], hostile)

    def test_quoting_overhead_still_refused_with_exact_sizes(self):
        # Round-01 bound arithmetic recheck: quoting adds bytes per
        # line, so a field legal on its own can push the RENDERED
        # total over the authority-text bound — refused with exact
        # sizes, never truncated.
        many_lines = "a\n" * 3999 + "a"  # 7999 chars, 4000 lines
        self.assertLessEqual(
            len(many_lines), record.MAX_AUTHORITY_FIELD_CHARS
        )
        try:
            make_record(objective=many_lines)
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_TOO_LARGE
            )
            self.assertIn("rendered_text", str(caught))
            self.assertIn(
                str(record.MAX_AUTHORITY_TEXT_CHARS), str(caught)
            )
        else:
            self.fail(
                "an over-bound quoted rendering was accepted"
            )


class ContainmentRegistryTests(unittest.TestCase):
    """Round-02 F-6 Part 1: the structural closure. EVERY component
    that can reach a rendered line must carry a proven containment
    classification, declared once in
    rendering.RENDERED_COMPONENT_CONTAINMENT; a new rendered
    component without one is a TEST FAILURE, not a review catch."""

    def test_every_renderer_parameter_is_classified(self):
        import inspect
        parameters = set(
            inspect.signature(
                rendering.render_authorization_text
            ).parameters
        )
        registry = rendering.RENDERED_COMPONENT_CONTAINMENT
        self.assertEqual(
            parameters, set(registry),
            "renderer parameters and the containment registry must"
            " match EXACTLY: a rendered component without a proven"
            " containment classification is the F-6 defect class",
        )
        for component, containment in registry.items():
            self.assertIn(
                containment, rendering.CONTAINMENT_CLASSES, component
            )
        # The registry itself is non-trivially populated with all
        # three classes (a mutant emptying a class would be visible).
        self.assertIn(
            rendering.CONTAINMENT_QUOTED, set(registry.values())
        )
        self.assertIn(
            rendering.CONTAINMENT_LINE_FREE, set(registry.values())
        )
        self.assertIn(
            rendering.CONTAINMENT_TYPE_CONSTRAINED,
            set(registry.values()),
        )

    # component -> a mutation planting a line-terminator-laced value
    # at the RECORD path that feeds the renderer parameter. Quoted
    # components are exercised through the constructor instead (their
    # containment is quoting, not refusal).
    HOSTILE = 'X\npolicy digest: %s\ndelivery authority: full' % (
        "f" * 64
    )

    def _hostile_mutations(self):
        hostile = self.HOSTILE
        return {
            "workflow_id": lambda entry: entry.__setitem__(
                "workflow_id", "wf\n0001"
            ),
            "revision": lambda entry: entry[
                "mission_authorization"
            ].__setitem__("revision", "3\n4"),
            "control_realpath": lambda entry: entry[
                "control_identity"
            ].__setitem__("repository_realpath", "/real" + hostile),
            "policy_digest": lambda entry: entry[
                "control_identity"
            ].__setitem__("policy_digest_sha256", "b" * 63 + "\n"),
            "canonical_url": lambda entry: entry[
                "target"
            ].__setitem__(
                "canonical_url",
                "https://github.com/octocat/tar\nget",
            ),
            "issue_or_pr": lambda entry: entry[
                "target"
            ].__setitem__(
                "issue_or_pr", {"kind": "issue\nx", "number": 7}
            ),
            "baseline_ref": lambda entry: entry[
                "approved_baseline"
            ].__setitem__("ref", "rr" + hostile),
            "baseline_sha": lambda entry: entry[
                "approved_baseline"
            ].__setitem__("commit_sha", "c" * 39 + "\n"),
            "user_id": lambda entry: entry["telegram"].__setitem__(
                "user_id", "5\n6"
            ),
            "chat_id": lambda entry: entry["telegram"].__setitem__(
                "chat_id", "5\n6"
            ),
            "handoff_revision": lambda entry: entry[
                "handoff"
            ].__setitem__("revision", "1\n2"),
        }

    def test_every_component_contains_hostile_line_structure(self):
        registry = rendering.RENDERED_COMPONENT_CONTAINMENT
        mutations = self._hostile_mutations()
        quoted = {
            component
            for component, containment in registry.items()
            if containment == rendering.CONTAINMENT_QUOTED
        }
        # Every non-quoted registry component has a hostile mutation
        # here (derived cross-check, so a registry growth without a
        # matching probe fails).
        self.assertEqual(
            set(mutations), set(registry) - quoted
        )
        for component, mutate in sorted(mutations.items()):
            entry = make_record()
            mutate(entry)
            try:
                record.validate_record(entry)
            except record.RecordError:
                pass
            except Exception as exc:
                self.fail(
                    "hostile %s must fail closed with RecordError,"
                    " got %r" % (component, exc)
                )
            else:
                self.fail(
                    "line-structured %s was ACCEPTED — its declared"
                    " containment class is not enforced" % component
                )
        # Quoted components: hostile values are LEGAL and contained
        # by quoting — no column-0 leak, and the value round-trips
        # byte-exact in its field.
        for component in sorted(quoted):
            if component == "authority_content":
                fields = list(rendering.AUTHORITY_CONTENT_KEYS)
            else:
                fields = [component]
            for field in fields:
                if field == "handoff_text":
                    continue  # covered by the handoff test below
                entry = make_record(**{field: "x" + self.HOSTILE})
                rendered = entry["mission_authorization"][
                    "rendered_text"
                ]
                for line in rendered.splitlines():
                    self.assertFalse(
                        line == "delivery authority: full"
                        or (line.startswith("policy digest:")
                            and "f" * 64 in line),
                        (field, line),
                    )


class ForgedBindingLineTests(unittest.TestCase):
    """Round-02 F-6 Part 2 regressions: the reviewer's probe3 record
    pair, verbatim — both partners now UNREPRESENTABLE."""

    PROBE3_A_REALPATH = (
        "/real/control\npolicy digest: " + "b" * 64
        + "\ntarget: https://github.com/octo/widget (issue #1)"
        + "\nbaseline: rr"
    )
    PROBE3_B_REF = (
        "rr\npolicy digest: " + "a" * 64
        + "\ntarget: https://github.com/evil/repo (pr #9)"
        + "\nbaseline: refs/heads/main"
    )

    def build(self, repository_realpath, policy, owner, repo, kind,
              number, ref):
        return record.new_record(
            workflow_id="wf-p3",
            human_intent="do the mission",
            repository_realpath=repository_realpath,
            policy_digest_sha256=policy,
            canonical_host="github.com",
            owner=owner,
            repo=repo,
            canonical_url="https://github.com/%s/%s" % (owner, repo),
            issue_or_pr_kind=kind,
            issue_or_pr_number=number,
            baseline_ref=ref,
            baseline_commit_sha="c" * 40,
            objective="O", constraints="C", rules="R",
            desired_outcome="D", acceptance="A",
            unresolved_questions="U", execution_scope="E",
            mission_revision=1,
            telegram_user_id=5,
            telegram_chat_id=6,
            approval_nonce="n" * 32,
            approval_created_at=100,
            approval_expires_at=1000,
            handoff_revision=1,
            handoff_text="H",
        )

    def test_probe3_partner_a_is_unrepresentable(self):
        # A: line-structured control realpath.
        try:
            self.build(
                self.PROBE3_A_REALPATH, "a" * 64, "evil", "repo",
                "pr", 9, "refs/heads/main",
            )
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_PATH_CHARACTER
            )
        except Exception as exc:
            self.fail(
                "probe3 partner A must fail closed with RecordError,"
                " got %r" % (exc,)
            )
        else:
            self.fail("probe3 partner A (forged-line realpath) was"
                      " ACCEPTED")

    def test_probe3_partner_b_is_unrepresentable(self):
        # B: line-structured baseline ref.
        try:
            self.build(
                "/real/control", "b" * 64, "octo", "widget",
                "issue", 1, self.PROBE3_B_REF,
            )
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_BASELINE_REF
            )
        except Exception as exc:
            self.fail(
                "probe3 partner B must fail closed with RecordError,"
                " got %r" % (exc,)
            )
        else:
            self.fail("probe3 partner B (forged-line ref) was"
                      " ACCEPTED")

    def test_ref_grammar_refusal_table(self):
        # Every str.splitlines() terminator, every forbidden ref
        # metacharacter, and the structural rules — each refused with
        # the record-layer code.
        terminators = ("\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c",
                       "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
        hostile_refs = ["a%sb" % t for t in terminators]
        hostile_refs += ["a b", "a\tb", "a~b", "a^b", "a:b", "a?b",
                         "a*b", "a[b", "a\\b", "a..b", "/leading",
                         "trailing/", "name.lock", "a\x7fb"]
        for ref in hostile_refs:
            try:
                make_record(baseline_ref=ref)
            except record.RecordError as caught:
                self.assertEqual(
                    caught.problem, record.PROBLEM_BASELINE_REF,
                    repr(ref),
                )
            except Exception as exc:
                self.fail(
                    "hostile ref %r must fail closed with"
                    " RecordError, got %r" % (ref, exc)
                )
            else:
                self.fail("hostile ref %r was accepted" % (ref,))
        # Legitimate refs still validate.
        for ref in ("refs/heads/main", "refs/tags/v1.0",
                    "feature/x-y_z.1", "HEAD", "main"):
            record.validate_record(make_record(baseline_ref=ref))

    def test_realpath_terminator_refusal_table(self):
        terminators = ("\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c",
                       "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
                       "\t", "\x00", "\x7f")
        for terminator in terminators:
            path = "/control/re" + terminator + "po"
            try:
                make_record(repository_realpath=path)
            except record.RecordError as caught:
                self.assertEqual(
                    caught.problem, record.PROBLEM_PATH_CHARACTER,
                    repr(terminator),
                )
            except Exception as exc:
                self.fail(
                    "hostile path %r must fail closed with"
                    " RecordError, got %r" % (path, exc)
                )
            else:
                self.fail("hostile path %r was accepted" % (path,))
        # A path with a plain SPACE stays legal (macOS reality) — a
        # space cannot break a rendered line.
        record.validate_record(
            make_record(repository_realpath="/control/My Repo")
        )


class LoadPathBoundTests(unittest.TestCase):
    """Round-01 F-2(b): the validate_record bounds must hold on a
    record READ BACK FROM DISK, where the constructor pre-check is
    absent — each site independently killable."""

    def consistent_oversize_record(self, field, oversize):
        entry = make_record()
        if field == "human_intent":
            entry["human_intent"] = oversize
        else:
            entry["mission_authorization"][field] = oversize
        # Make everything EXCEPT the bound consistent: re-render from
        # the (oversized) fields and re-bind the digest, so only the
        # bound check stands between the record and acceptance.
        rendered = rendering.render_record_text(entry)
        entry["mission_authorization"]["rendered_text"] = rendered
        entry["mission_authorization"]["digest_sha256"] = (
            digest.text_digest(rendered)
        )
        return entry

    def test_over_bound_fields_on_disk_are_refused_at_load(self):
        cases = (
            ("objective",
             "o" * (record.MAX_AUTHORITY_FIELD_CHARS + 1),
             record.MAX_AUTHORITY_FIELD_CHARS),
            ("human_intent",
             "i" * (record.MAX_HUMAN_INTENT_CHARS + 1),
             record.MAX_HUMAN_INTENT_CHARS),
        )
        for field, oversize, bound in cases:
            entry = self.consistent_oversize_record(field, oversize)
            # Direct validate_record refusal (no constructor in the
            # path), with exact sizes.
            try:
                record.validate_record(entry)
            except record.RecordError as caught:
                self.assertEqual(
                    caught.problem, record.PROBLEM_TOO_LARGE, field
                )
                self.assertIn(str(len(oversize)), str(caught), field)
                self.assertIn(str(bound), str(caught), field)
            except Exception as exc:
                self.fail(
                    "over-bound %s must fail closed with RecordError,"
                    " got %r" % (field, exc)
                )
            else:
                self.fail(
                    "over-bound %s on the load path was accepted"
                    % field
                )
            # And through a REAL on-disk store file.
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(
                    tmp, store.WORKFLOWS_FILE_NAME
                )
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "workflow_store_schema_version":
                                store.WORKFLOW_STORE_SCHEMA_VERSION,
                            "workflows": {
                                entry["workflow_id"]: entry
                            },
                        },
                        handle,
                    )
                os.chmod(path, 0o600)
                with self.assertRaises(store.StoreError) as caught:
                    store.WorkflowStore(tmp).load()
                self.assertIn(str(len(oversize)), str(caught.exception))

    def test_constructor_pre_checks_fail_closed_not_crash(self):
        # The constructor pre-check is the OTHER site: with it
        # deleted, a bad-typed input crashes inside the renderer
        # before the self-validation runs. Each case must raise
        # RecordError, never a raw TypeError/AttributeError/
        # DigestError.
        bad_kwargs = (
            {"human_intent": 42},
            {"objective": None},
            {"unresolved_questions": ["list"]},
        )
        for overrides in bad_kwargs:
            try:
                make_record("wf-bad", **overrides)
            except record.RecordError:
                pass
            except Exception as exc:
                self.fail(
                    "bad-typed %r must fail closed with RecordError,"
                    " got %r" % (overrides, exc)
                )
            else:
                self.fail("bad-typed %r was accepted" % (overrides,))
        # And a bad-typed value the renderer would format with %d.
        try:
            record.new_record(
                workflow_id="wf-bad2",
                human_intent="do the mission",
                repository_realpath="/control/repo",
                policy_digest_sha256="0" * 64,
                canonical_host="github.com",
                owner="octocat",
                repo="target",
                canonical_url="https://github.com/octocat/target",
                issue_or_pr_kind="issue",
                issue_or_pr_number=7,
                baseline_ref="refs/heads/main",
                baseline_commit_sha="a" * 40,
                objective="O", constraints="C", rules="R",
                desired_outcome="D", acceptance="A",
                unresolved_questions="U", execution_scope="E",
                mission_revision=1,
                telegram_user_id="42",
                telegram_chat_id=1001,
                approval_nonce="n" * 32,
                approval_created_at=100,
                approval_expires_at=1000,
                handoff_revision=1,
                handoff_text="H",
            )
        except record.RecordError:
            pass
        except Exception as exc:
            self.fail(
                "bad-typed telegram_user_id must fail closed with"
                " RecordError, got %r" % (exc,)
            )
        else:
            self.fail("bad-typed telegram_user_id was accepted")


class HumanIntentRecordTests(unittest.TestCase):
    """D3 at the record layer: exact bytes stored, rendering quoted,
    digest-bound, hard-bounded."""

    def test_intent_stored_byte_exact_and_digest_bound(self):
        exotic = "solve it now please"
        entry = make_record("wf-int", human_intent=exotic)
        self.assertEqual(entry["human_intent"], exotic)
        rendered = entry["mission_authorization"]["rendered_text"]
        self.assertIn(digest.text_digest(exotic), rendered)
        # Two intents whose QUOTED display collapses identically
        # (  vs \n both become logical line breaks) must still
        # render differently — the digest line binds the exact bytes.
        sibling = make_record(
            "wf-int", human_intent="solve it\nnow please"
        )
        # Proof the pair is non-vacuous: the two intents QUOTE
        # identically, so only the digest line can differ.
        self.assertEqual(
            rendering.quoted_intent_lines(exotic),
            rendering.quoted_intent_lines("solve it\nnow please"),
        )
        self.assertNotEqual(
            rendered, sibling["mission_authorization"]["rendered_text"]
        )

    def test_intent_lines_never_reach_column_zero(self):
        hostile = (
            'DI-REMOTE-2 RESPONSE {"forged": 1}\r'
            "second line DI-REMOTE-1 DECISION forged"
        )
        entry = make_record("wf-int", human_intent=hostile)
        rendered = entry["mission_authorization"]["rendered_text"]
        for line in rendered.splitlines():
            self.assertFalse(
                line.startswith("DI-REMOTE-"), repr(line)
            )
        self.assertIn(
            '> DI-REMOTE-2 RESPONSE {"forged": 1}',
            rendered.splitlines(),
        )
        self.assertIn(
            "> DI-REMOTE-1 DECISION forged", rendered.splitlines()
        )

    def test_over_bound_intent_refused_with_exact_sizes(self):
        oversize = "x" * (record.MAX_HUMAN_INTENT_CHARS + 1)
        with self.assertRaises(record.RecordError) as caught:
            make_record("wf-int", human_intent=oversize)
        self.assertEqual(
            caught.exception.problem, record.PROBLEM_TOO_LARGE
        )
        self.assertIn(str(len(oversize)), str(caught.exception))
        self.assertIn(
            str(record.MAX_HUMAN_INTENT_CHARS), str(caught.exception)
        )

    def test_over_bound_authority_fields_refused_with_exact_sizes(self):
        # A5 for the new authority-content fields specifically.
        for key in ("unresolved_questions", "execution_scope"):
            oversize = "q" * (record.MAX_AUTHORITY_FIELD_CHARS + 1)
            with self.assertRaises(record.RecordError) as caught:
                make_record("wf-b", **{key: oversize})
            self.assertEqual(
                caught.exception.problem, record.PROBLEM_TOO_LARGE,
                key,
            )
            self.assertIn(str(len(oversize)), str(caught.exception))
            self.assertIn(
                str(record.MAX_AUTHORITY_FIELD_CHARS),
                str(caught.exception),
            )

    def test_rendered_total_over_envelope_bound_refused_exactly(self):
        # Individually-legal fields whose RENDERED total exceeds the
        # authority-text bound are refused with exact sizes — never
        # truncated (the A5 explicit-statement obligation).
        try:
            make_record(
                "wf-big",
                human_intent="i" * 4000,
                **{key: "v" * 8000
                   for key in ("objective", "acceptance")}
            )
        except record.RecordError as caught:
            self.assertEqual(
                caught.problem, record.PROBLEM_TOO_LARGE
            )
            self.assertIn(
                str(record.MAX_AUTHORITY_TEXT_CHARS),
                str(caught),
            )
        else:
            self.fail("over-bound rendered total was accepted")


class WorkflowStoreMigrationTests(unittest.TestCase):
    """D6: an on-disk v1 workflow store fails closed and is migrated
    only by the explicit retirement migration — nothing fabricated."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(
            self.tmp.name, store.WORKFLOWS_FILE_NAME
        )
        self.backup_path = self.path + migrate.BACKUP_SUFFIX

    def v1_record(self, workflow_id="wf-v1"):
        # A REAL version-1 record, byte-shaped as the prior accepted
        # increment wrote it: schema_version 1, required issue_or_pr,
        # three-key mission_authorization, no human_intent.
        return {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "control_identity": {
                "repository_realpath": "/control/repo",
                "policy_digest_sha256": "0" * 64,
            },
            "target": {
                "canonical_host": "github.com",
                "owner": "octocat",
                "repo": "target",
                "canonical_url": "https://github.com/octocat/target",
                "issue_or_pr": {"kind": "issue", "number": 7},
            },
            "approved_baseline": {
                "ref": "refs/heads/main",
                "commit_sha": "a" * 40,
            },
            "mission_authorization": {
                "rendered_text": "V1 MISSION TEXT",
                "digest_sha256": digest.text_digest(
                    "V1 MISSION TEXT"
                ),
                "revision": 1,
            },
            "telegram": {
                "user_id": 1001,
                "chat_id": 1001,
                "message_ids": [9],
                "plan_message_id": 9,
            },
            "approval": {
                "approval_kind": "mission_authorization_v2",
                "nonce": "n" * 32,
                "created_at": 100,
                "expires_at": 1000,
                "consumed_at": None,
                "consumed_by_update_id": None,
                "decision": None,
                "superseded": False,
            },
            "handoff": {
                "revision": 1,
                "text": "V1 HANDOFF",
                "digest_sha256": digest.text_digest("V1 HANDOFF"),
            },
            "phase": "PLANNED",
            "workspace_lease": None,
            "receipts": [],
            "codex_turns": [],
            "ambiguity": {"state": "none", "detail": None},
            "delivery_authority": "none",
        }

    def write_v1_store(self, records=None):
        if records is None:
            records = {"wf-v1": self.v1_record()}
        document = {
            "workflow_store_schema_version": 1,
            "workflows": records,
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, indent=1)
        os.chmod(self.path, 0o600)
        with open(self.path, "rb") as handle:
            return handle.read()

    def test_v1_store_load_fails_closed_naming_the_migration(self):
        before = self.write_v1_store()
        with self.assertRaises(store.StoreError) as caught:
            store.WorkflowStore(self.tmp.name).load()
        self.assertIn("tgop migrate-workflows", str(caught.exception))
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_store_layer_version_refusal_holds_in_isolation(self):
        # Round-01 F-2(c): a store marked version 1 whose RECORDS are
        # v2-VALID, so the record layer cannot answer for the store
        # layer (the earlier fixture's v1 records also failed record
        # validation, and both layers' messages name the migration
        # command — a store-version mutant survived behind that).
        # The refusal must be the STORE-LAYER message specifically.
        v2_entry = make_record("wf-v2ok")
        document = {
            "workflow_store_schema_version": 1,
            "workflows": {"wf-v2ok": v2_entry},
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, indent=1)
        os.chmod(self.path, 0o600)
        try:
            store.WorkflowStore(self.tmp.name).load()
        except store.StoreError as caught:
            self.assertIn(
                "has workflow_store_schema_version 1",
                str(caught),
            )
            self.assertIn("tgop migrate-workflows", str(caught))
        except Exception as exc:
            self.fail(
                "a version-1 store with v2-valid records must fail"
                " closed with StoreError, got %r" % (exc,)
            )
        else:
            self.fail(
                "a version-1 store with v2-valid records was"
                " accepted — the store-layer version check is not"
                " load-bearing"
            )

    def test_migration_retires_v1_records_with_exact_count(self):
        original = self.write_v1_store({
            "wf-a": self.v1_record("wf-a"),
            "wf-b": self.v1_record("wf-b"),
        })
        changed, message = migrate.migrate_workflow_store(
            self.tmp.name
        )
        self.assertTrue(changed)
        self.assertIn("2 version-1 workflow record(s) RETIRED",
                      message)
        # assertTrue first so a mutant that skips the backup write
        # dies by FAIL, not by FileNotFoundError.
        self.assertTrue(
            os.path.exists(self.backup_path),
            "the preserved v1 backup was never written",
        )
        # Backup is BYTE-exact.
        with open(self.backup_path, "rb") as handle:
            self.assertEqual(handle.read(), original)
        # The rewritten store is v2 with NO active records — nothing
        # fabricated — and loads cleanly with a fresh instance.
        reloaded = store.WorkflowStore(self.tmp.name).load()
        self.assertEqual(
            reloaded["workflow_store_schema_version"],
            store.WORKFLOW_STORE_SCHEMA_VERSION,
        )
        self.assertEqual(reloaded["workflows"], {})
        # Re-running is an explicit no-op.
        changed, message = migrate.migrate_workflow_store(
            self.tmp.name
        )
        self.assertFalse(changed)
        self.assertIn("already at schema version", message)

    def test_interrupted_run_resumes_from_identical_backup(self):
        original = self.write_v1_store()
        with open(self.backup_path, "wb") as handle:
            handle.write(original)
        changed, message = migrate.migrate_workflow_store(
            self.tmp.name
        )
        self.assertTrue(changed)
        with open(self.backup_path, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_differing_backup_is_never_overwritten(self):
        before = self.write_v1_store()
        with open(self.backup_path, "wb") as handle:
            handle.write(b"different preserved bytes")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.migrate_workflow_store(self.tmp.name)
        self.assertIn(
            "refusing to overwrite", str(caught.exception)
        )
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        with open(self.backup_path, "rb") as handle:
            self.assertEqual(
                handle.read(), b"different preserved bytes"
            )

    def test_unknown_version_and_malformed_files_are_refused(self):
        document = {
            "workflow_store_schema_version": 7, "workflows": {},
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_workflow_store(self.tmp.name)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{torn")
        with open(self.path, "rb") as handle:
            before = handle.read()
        with self.assertRaises(migrate.MigrationError):
            migrate.migrate_workflow_store(self.tmp.name)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_missing_file_is_an_explicit_noop(self):
        changed, message = migrate.migrate_workflow_store(
            self.tmp.name
        )
        self.assertFalse(changed)
        self.assertIn("nothing to migrate", message)
        self.assertFalse(os.path.exists(self.path))


class WidenedStrategyGuardTests(unittest.TestCase):
    """D5: both strategy guards re-proven against the WIDENED key
    set, each standing alone."""

    # LITERAL key list — deliberately NOT derived from
    # ALLOWED_AUTHORIZATION_KEYS, so a mutant that narrows the
    # constant under test cannot narrow this fixture with it (the
    # recorded fixture-derived-from-the-constant class).
    WIDENED_KEYS = (
        "objective", "constraints", "rules", "desired_outcome",
        "acceptance", "unresolved_questions", "execution_scope",
        "control", "target", "issue_or_pr", "baseline", "handoff",
        "telegram_approval", "workflow_id", "human_intent",
        "revision", "delivery_authority",
    )

    def widened_document(self):
        document = {key: "text" for key in self.WIDENED_KEYS}
        document["delivery_authority"] = "none"
        return document

    def test_forbidden_key_refused_first_on_a_widened_document(self):
        # Guard (b) fires FIRST (its code, not the closed-set code),
        # even when every widened key is present alongside it.
        document = self.widened_document()
        document["plan"] = ["step"]
        with self.assertRaises(
            authorization.AuthorizationError
        ) as caught:
            authorization.validate_authorization_structure(document)
        self.assertEqual(
            caught.exception.problem,
            authorization.PROBLEM_FORBIDDEN_KEY,
        )

    def test_new_authority_keys_are_required(self):
        for key in ("unresolved_questions", "execution_scope",
                    "human_intent"):
            document = self.widened_document()
            del document[key]
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_structure(
                    document
                )
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_MISSING_KEY, key,
            )
            self.assertIn(key, str(caught.exception))

    def test_nested_strategy_inside_new_fields_is_refused(self):
        for key in ("unresolved_questions", "execution_scope"):
            document = self.widened_document()
            document[key] = {"steps": ["a"]}
            with self.assertRaises(
                authorization.AuthorizationError
            ) as caught:
                authorization.validate_authorization_values_deep(
                    document
                )
            self.assertEqual(
                caught.exception.problem,
                authorization.PROBLEM_NESTED_FORBIDDEN_KEY, key,
            )


if __name__ == "__main__":
    unittest.main()
