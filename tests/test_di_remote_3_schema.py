"""DI-REMOTE-3 increment I1: durable schema + deterministic migration.

Scope of THIS file, stated so it is not read as more than it is: the
durable ``result_placeholder`` key, the six additive
``result_delivery`` fields, the load-boundary normalizer, and the
migration dispositions of strategy §4. Nothing here delivers or edits
a result — the transport (I2), the adapter lifecycle (I3), the Broker
gate (I4) and the edit-based delivery engine (I5) are later
increments. The new delivery states are REPRESENTABLE here and are
never PRODUCED here, and the tests say so rather than implying
coverage this increment does not have.

New file, new tests only: no pre-existing test is modified, weakened,
skipped or renamed (lead plan §6.1).

Test ids map to the lead plan §5 matrix and are named in each
docstring: T-M1..T-M10, T-X2, T-X3.

Hermetic: temp directories, injected Telegram API, explicit clocks.
Durable guarantees are re-read from the ON-DISK store through a FRESH
store instance, never from an in-memory document (the recorded
"fail-closed state asserted only in memory" class).
"""

import copy
import json
import os
import unittest

from telegram_operator import adapter as adapter_module
from telegram_operator.adapter import RESULT_MESSAGE_HEADER
from workflow_authority import record as wa_record
from workflow_authority import store as wa_store
from workflow_authority.digest import text_digest

from codex_gateway import role_turn

from test_mission import MissionCase, NOW, cb_update, msg_update
from test_workflow_authority import make_record


# --- Independently authored expectations ------------------------------
#
# These literals are deliberately NOT derived from the constants and
# tables under test. A fixture built from the table it is meant to
# check is self-consistent under a mutant of that table and would stay
# green through a flipped row (the recorded "expiry test deriving its
# clock from the constant under test" class). Each literal below is
# transcribed from lead plan §2.2/§2.3/§3.1 and is pinned equal to the
# module's own value, so drift in EITHER direction fails.

EXPECTED_PLACEHOLDER_KEYS = (
    "state", "chat_id", "message_id", "requested_at", "sent_at",
    "bound_at", "text_digest",
)

EXPECTED_ADDITIVE_DELIVERY_KEYS = (
    "verified_result_digest", "rendered_digest", "edited_message_id",
    "attempted_at", "settled_at", "problem",
)

EXPECTED_LEGACY_DELIVERY_KEYS = (
    "state", "reserved_at", "telegram_message_id",
)

# plan §3.1. True == the field MUST be non-null in that state;
# False == it MUST be null.
EXPECTED_PLACEHOLDER_TABLE = {
    "required": {
        "message_id": False, "sent_at": False,
        "bound_at": False, "text_digest": False,
    },
    "sending": {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    "failed_unsent": {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    "indefinite": {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    "bound": {
        "message_id": True, "sent_at": True,
        "bound_at": True, "text_digest": True,
    },
    "unbindable": {
        "message_id": True, "sent_at": True,
        "bound_at": True, "text_digest": True,
    },
}

# A non-null sample per conditional field, used to build a valid
# placeholder in any state.
_PLACEHOLDER_SAMPLES = {
    "message_id": 77,
    "sent_at": 11,
    "bound_at": 12,
    "text_digest": "b" * 64,
}

# make_record's chat id.
CHAT_ID = 1001


def placeholder(state, **overrides):
    """A placeholder dict that is VALID in ``state`` per the
    independently authored §3.1 table."""
    value = {
        "state": state,
        "chat_id": CHAT_ID,
        "requested_at": 10,
        "message_id": None,
        "sent_at": None,
        "bound_at": None,
        "text_digest": None,
    }
    for field, required in EXPECTED_PLACEHOLDER_TABLE[state].items():
        value[field] = _PLACEHOLDER_SAMPLES[field] if required else None
    value.update(overrides)
    return value


def legacy_delivery(state, message_id=None, reserved_at=5):
    """A result_delivery marker carrying ONLY the three legacy keys —
    the exact shape already written to stores today."""
    return {
        "state": state,
        "reserved_at": reserved_at,
        "telegram_message_id": message_id,
    }


def edit_delivery(state, reserved_at=5, attempted_at=6, settled_at=8,
                  verified_result_digest="c" * 64,
                  rendered_digest="d" * 64,
                  edited_message_id=_PLACEHOLDER_SAMPLES["message_id"],
                  problem="edit degraded: bound object gone"):
    """A state-COHERENT edit-lane result_delivery marker.

    DI-REMOTE-3 I5 (round-01 F1): the six additive fields carry TOTAL
    per-state invariants at the durable boundary, so a marker must carry
    exactly the fields the delivery engine writes for its state and null
    the rest. This builder mirrors that table: the edit lane never uses
    the legacy ``telegram_message_id`` (it records ``edited_message_id``
    instead), always carries both digests and ``attempted_at``, and adds
    ``settled_at``/``edited_message_id``/``problem`` exactly where the
    state requires them.

    Round-02 G1: a ``delivered_by_edit`` receipt is RELATIONALLY bound
    to its placeholder — ``edited_message_id`` must equal the bound
    ``result_placeholder.message_id``. The default here is exactly
    ``placeholder("bound")``'s message id, so a record built with both
    is coherent by construction; a caller pairing this with a different
    bound message id must pass a matching ``edited_message_id``.
    """
    marker = {
        "state": state,
        "reserved_at": reserved_at,
        "telegram_message_id": None,
        "verified_result_digest": verified_result_digest,
        "rendered_digest": rendered_digest,
        "edited_message_id": None,
        "attempted_at": attempted_at,
        "settled_at": None,
        "problem": None,
    }
    if state == wa_record.DELIVERY_EDIT_PENDING:
        return marker  # write-ahead: not yet settled, no id, no problem
    if state == wa_record.DELIVERY_DELIVERED_BY_EDIT:
        marker["edited_message_id"] = edited_message_id
        marker["settled_at"] = settled_at
        return marker
    if state in (wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                 wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
                 wa_record.DELIVERY_EDIT_INDEFINITE):
        marker["settled_at"] = settled_at
        marker["problem"] = problem
        return marker
    raise ValueError("edit_delivery: not an edit-lane state: %r" % state)


def edit_lane_record(marker, placeholder_state="bound", **overrides):
    """A record that could ACTUALLY carry an edit-lane delivery receipt.

    Round-03 H1: an edit-lane receipt is only ever written by the edit
    engine, which requires phase COMPLETED, a verified_result, and a
    BOUND placeholder before it edits. A synthetic edit marker on a
    default (PLANNED, no verified result, no placeholder) record is an
    IMPOSSIBLE combination the validator now refuses, so tests that want
    a VALID edit-lane record build it here. The bound placeholder's
    message id equals ``edit_delivery``'s default ``edited_message_id``,
    so a delivered_by_edit marker is coherent by construction. Callers
    can override the placeholder state or any top-level field to build a
    deliberately-impossible record for a fail-closed assertion.
    """
    summary = "verified outcome"
    kwargs = dict(
        result_placeholder=placeholder(placeholder_state),
        result_delivery=marker,
        phase=wa_record.PHASE_COMPLETED,
        verified_result={
            "summary": summary,
            "digest": text_digest(summary),
            "recorded_at": 1,
        },
    )
    kwargs.update(overrides)  # a caller override wins over the defaults
    return make_record(**kwargs)


def delivery_record(marker):
    """A minimal record carrying ``marker``: an edit-lane marker gets a
    COMPLETE record (H1 prerequisites), a legacy marker gets a bare one.
    """
    if marker.get("state") in wa_record.DELIVERY_EDIT_STATES:
        return edit_lane_record(marker)
    return make_record(result_delivery=marker)


def assert_valid(case, document, note=""):
    """Assert a record VALIDATES, by AUTHORED assertion.

    A bare ``validate_record(...)`` would die with an ERROR under a
    mutant, and a battery of ERRORs is not a kill for the guarantee
    under test (the recorded crash-kill rule).
    """
    try:
        wa_record.validate_record(document)
    except wa_record.RecordError as exc:
        case.fail(
            "%s must VALIDATE; it was refused with %s: %s"
            % (note or "record", exc.problem, exc)
        )


class SchemaShapeTests(unittest.TestCase):
    """The declared shapes match the plan, and each other."""

    def test_declared_key_sets_match_the_plan(self):
        # Anti-vacuity for every fixture below: if the module's key
        # sets drifted from the plan, the fixtures would be testing a
        # different schema than the one specified.
        self.assertEqual(
            tuple(wa_record.RESULT_PLACEHOLDER_KEYS),
            EXPECTED_PLACEHOLDER_KEYS,
        )
        self.assertEqual(
            tuple(wa_record.RESULT_DELIVERY_ADDITIVE_KEYS),
            EXPECTED_ADDITIVE_DELIVERY_KEYS,
        )
        self.assertEqual(
            tuple(wa_record.RESULT_DELIVERY_LEGACY_KEYS),
            EXPECTED_LEGACY_DELIVERY_KEYS,
        )
        self.assertEqual(
            set(wa_record.PLACEHOLDER_STATES),
            set(EXPECTED_PLACEHOLDER_TABLE),
        )
        # The state/field table is TOTAL over the closed state set: a
        # state with no row would otherwise be refused at runtime by a
        # path no test exercises.
        self.assertEqual(
            set(wa_record._PLACEHOLDER_FIELD_TABLE),
            set(EXPECTED_PLACEHOLDER_TABLE),
        )
        self.assertEqual(
            wa_record._PLACEHOLDER_FIELD_TABLE,
            EXPECTED_PLACEHOLDER_TABLE,
        )

    def test_result_placeholder_is_a_top_level_record_key(self):
        # Authored assertion rather than an incidental crash: removing
        # the key from the closed top-level set makes every record
        # construction raise, and a battery of ERRORs is not a kill for
        # this guarantee (recorded crash-kill rule).
        self.assertIn(
            "result_placeholder", wa_record._TOP_LEVEL_KEYS,
            "result_placeholder must be a closed-key-validated"
            " top-level record key",
        )
        self.assertIn("result_delivery", wa_record._TOP_LEVEL_KEYS)

    def test_schema_version_is_not_bumped(self):
        # Plan §2.1 / strategy §4: a bump routes every record already
        # on disk into 'tgop migrate-workflows', whose only v1->v2
        # behaviour is RETIREMENT — that would destroy in-flight
        # COMPLETED records. The additive keys are materialized by the
        # load-boundary normalizer instead.
        self.assertEqual(wa_record.WORKFLOW_SCHEMA_VERSION, 2)
        self.assertEqual(wa_store.WORKFLOW_STORE_SCHEMA_VERSION, 2)

    def test_legacy_delivery_states_are_unchanged(self):
        self.assertEqual(
            tuple(wa_record.DELIVERY_LEGACY_STATES),
            ("reserved", "delivered", "partial"),
        )
        # The new edit states are declared (I5 writes them; nothing in
        # I1 does) and the legacy three still lead the closed set.
        self.assertEqual(
            tuple(wa_record.DELIVERY_EDIT_STATES),
            ("edit_pending", "delivered_by_edit", "degraded_unbindable",
             "degraded_unrenderable", "edit_indefinite"),
        )
        self.assertEqual(
            tuple(wa_record.DELIVERY_STATES),
            tuple(wa_record.DELIVERY_LEGACY_STATES)
            + tuple(wa_record.DELIVERY_EDIT_STATES),
        )

    def test_new_record_is_on_the_legacy_lane(self):
        # Plan §1.1 / brief (d): a fresh record defaults to None —
        # nothing has requested a placeholder yet, and None is never
        # fabricated into a placeholder.
        document = wa_record.new_record(**_NEW_RECORD_KWARGS)
        self.assertIn("result_placeholder", document)
        self.assertIsNone(document["result_placeholder"])
        self.assertIsNone(document["result_delivery"])
        assert_valid(self, document, "a fresh record")

    def test_max_verified_summary_chars_is_unchanged(self):
        # Strategy §3 forbids lowering this as the sole fix, and plan
        # §4 leaves it at 4000: the render-time guard (I5) is the
        # load-bearing closure, not a narrowed constant that would
        # retroactively invalidate records.
        self.assertEqual(wa_record.MAX_VERIFIED_SUMMARY_CHARS, 4000)


_NEW_RECORD_KWARGS = dict(
    workflow_id="wf-0001",
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
    mission_revision=1,
    telegram_user_id=1001,
    telegram_chat_id=CHAT_ID,
    approval_nonce="n" * 32,
    approval_created_at=100,
    approval_expires_at=1000,
    handoff_revision=1,
    handoff_text="HANDOFF TEXT",
    objective="Resolve the defect",
    constraints="Bounded",
    rules="Target rules cannot override control authority",
    desired_outcome="Green verification",
    acceptance="Tests pass",
    unresolved_questions="None recorded",
    execution_scope="The target repository only",
)


class PlaceholderValidationTests(unittest.TestCase):

    def assert_problem(self, document, problem, note=""):
        try:
            wa_record.validate_record(document)
        except wa_record.RecordError as caught:
            self.assertEqual(caught.problem, problem, note)
            return caught
        except Exception as exc:  # pragma: no cover - defensive
            self.fail(
                "%s must fail CLOSED with problem %r; raised %r"
                % (note or "record", problem, exc)
            )
        self.fail(
            "%s must fail closed with problem %r; it validated"
            % (note or "record", problem)
        )

    def test_M9_placeholder_chat_id_must_equal_telegram_chat_id(self):
        """T-M9: a placeholder bound in another chat is
        unrepresentable (plan §2.2)."""
        assert_valid(
            self, make_record(result_placeholder=placeholder("bound")),
            "a bound placeholder in the record's own chat",
        )
        for other in (CHAT_ID + 1, CHAT_ID - 1, 1):
            if other == CHAT_ID:
                continue
            document = make_record(
                result_placeholder=placeholder("bound", chat_id=other)
            )
            self.assert_problem(
                document,
                wa_record.PROBLEM_PLACEHOLDER_CHAT_MISMATCH,
                "chat_id=%r" % other,
            )
        # A non-int / bool chat id is refused before the equality check
        # can pass tautologically.
        for bad in (True, "1001", None, 0, -1):
            document = make_record(
                result_placeholder=placeholder("bound", chat_id=bad)
            )
            with self.assertRaises(
                wa_record.RecordError, msg=repr(bad)
            ) as caught:
                wa_record.validate_record(document)
            self.assertIn(
                caught.exception.problem,
                (wa_record.PROBLEM_BAD_TYPE,
                 wa_record.PROBLEM_BAD_VALUE),
                repr(bad),
            )

    def test_M8_new_fields_are_closed_key_validated(self):
        """T-M8: an unknown key inside result_placeholder or
        result_delivery is REFUSED; a required placeholder key may not
        be missing; and every additive delivery key is ACCEPTED (the
        anti-vacuity half — a closed set that rejected them too would
        pass the rejection assertions alone)."""
        bad = placeholder("bound")
        bad["extra_authority"] = True
        self.assert_problem(
            make_record(result_placeholder=bad),
            wa_record.PROBLEM_UNKNOWN_KEY, "placeholder unknown key",
        )
        for key in EXPECTED_PLACEHOLDER_KEYS:
            value = placeholder("bound")
            del value[key]
            self.assert_problem(
                make_record(result_placeholder=value),
                wa_record.PROBLEM_MISSING_KEY,
                "placeholder missing %r" % key,
            )
        delivery = legacy_delivery("reserved")
        delivery["extra_authority"] = True
        self.assert_problem(
            make_record(result_delivery=delivery),
            wa_record.PROBLEM_UNKNOWN_KEY, "delivery unknown key",
        )
        # Every legacy key is still REQUIRED.
        for key in EXPECTED_LEGACY_DELIVERY_KEYS:
            value = legacy_delivery("reserved")
            del value[key]
            self.assert_problem(
                make_record(result_delivery=value),
                wa_record.PROBLEM_MISSING_KEY,
                "delivery missing legacy %r" % key,
            )
        # Anti-vacuity: each additive key is accepted in a STATE that
        # legitimately carries it. RETARGETED under I5/F1: the six
        # additive fields are now STATE-CONDITIONAL, so "populated" means
        # state-coherent (a legacy reserved marker with additive fields
        # is no longer a valid receipt — that WAS the false-receipt
        # defect F1 closes). The two markers below jointly populate all
        # six additive keys across the states that carry them.
        assert_valid(
            self,
            make_record(result_delivery=legacy_delivery("reserved")),
            "a legacy three-key delivery marker",
        )
        # G1: delivered_by_edit is relationally bound to its placeholder
        # (edited_message_id == bound message id), so it is paired with a
        # matching bound placeholder here.
        assert_valid(
            self,
            edit_lane_record(edit_delivery("delivered_by_edit")),
            "a delivered_by_edit marker: digests, edited id, timestamps",
        )
        assert_valid(
            self,
            edit_lane_record(edit_delivery("degraded_unbindable")),
            "a degraded_unbindable marker: digests, timestamps, problem",
        )
        # ... and each additive key is TYPE-validated when present, on a
        # base state that legitimately carries that field. The
        # delivered_by_edit base is paired with a matching bound
        # placeholder (G1); the field type-checks run BEFORE the
        # relational check, so a bad edited_message_id still surfaces its
        # own type/value code.
        delivered = edit_delivery("delivered_by_edit")
        degraded = edit_delivery("degraded_unbindable")

        for base, key, bad_value, problem in (
            (delivered, "verified_result_digest", "nope",
             wa_record.PROBLEM_BAD_VALUE),
            (delivered, "rendered_digest", "C" * 64,
             wa_record.PROBLEM_BAD_VALUE),
            (delivered, "edited_message_id", 0,
             wa_record.PROBLEM_BAD_VALUE),
            (delivered, "edited_message_id", True,
             wa_record.PROBLEM_BAD_TYPE),
            (delivered, "attempted_at", -1, wa_record.PROBLEM_BAD_VALUE),
            (delivered, "settled_at", "soon", wa_record.PROBLEM_BAD_TYPE),
            (degraded, "problem", "", wa_record.PROBLEM_BAD_VALUE),
            (degraded, "problem",
             "x" * (wa_record.MAX_BOUNDED_SUMMARY_CHARS + 1),
             wa_record.PROBLEM_TOO_LARGE),
        ):
            value = dict(base)
            value[key] = bad_value
            self.assert_problem(
                delivery_record(value), problem,
                "delivery %s=%r" % (key, bad_value),
            )

    def test_M10_placeholder_state_field_table_fails_closed(self):
        """T-M10 (added this increment, beyond the §5 rows the brief
        names): every state validates in its own §3.1 shape, and EVERY
        one-field deviation in EITHER direction fails closed with that
        FIELD's own problem code — no permissive fallthrough."""
        problems = {
            "message_id": wa_record.PROBLEM_PLACEHOLDER_MESSAGE_ID,
            "sent_at": wa_record.PROBLEM_PLACEHOLDER_SENT_AT,
            "bound_at": wa_record.PROBLEM_PLACEHOLDER_BOUND_AT,
            "text_digest": wa_record.PROBLEM_PLACEHOLDER_TEXT_DIGEST,
        }
        for state, row in sorted(EXPECTED_PLACEHOLDER_TABLE.items()):
            assert_valid(
                self, make_record(result_placeholder=placeholder(state)),
                "placeholder state %r in its own table shape" % state,
            )
            for field, required in sorted(row.items()):
                # Deviate exactly one field from the table row.
                deviant = None if required else (
                    _PLACEHOLDER_SAMPLES[field]
                )
                self.assert_problem(
                    make_record(
                        result_placeholder=placeholder(
                            state, **{field: deviant}
                        )
                    ),
                    problems[field],
                    "state=%s field=%s deviant=%r"
                    % (state, field, deviant),
                )
        # An unknown state is refused by the closed state set.
        unknown = placeholder("bound")
        unknown["state"] = "done"
        self.assert_problem(
            make_record(result_placeholder=unknown),
            wa_record.PROBLEM_BAD_VALUE, "unknown placeholder state",
        )

    def test_M11_placeholder_field_types_are_validated(self):
        """T-M11 (round-02, closes reviewer blocker B-1): every
        non-null placeholder field is TYPE-validated, each with its
        exact problem code.

        Round 01 asserted only the null/non-null half of the §3.1
        table, so every `_require_int` / `_require_hex` /
        `_require_timestamp` call in `_validate_result_placeholder`
        could be deleted with all 218 tests still green (reviewer
        mutants N-03, N-04, N-05, N-22). This is the symmetric twin of
        the bad-value table already written for the six additive
        `result_delivery` keys in T-M8 — the asymmetry WAS the defect.

        `text_digest` is the highest-value row: under LEAD RULING L-1
        it is the only handle a human has on an `indefinite`
        placeholder's on-screen object (strategy §1.3), so its format
        guarantee may not rest on an unpinned line.

        Each bad value is placed in a state where the §3.1 table
        REQUIRES that field non-null, so the null/non-null check
        passes and the type check is the thing under test.
        """
        cases = (
            # (state, field, bad value, exact problem code)
            ("bound", "message_id", "77", wa_record.PROBLEM_BAD_TYPE),
            # bool is a subclass of int: True must never masquerade as
            # message id 1 (the recorded bool-twin class).
            ("bound", "message_id", True, wa_record.PROBLEM_BAD_TYPE),
            ("bound", "message_id", 1.0, wa_record.PROBLEM_BAD_TYPE),
            ("bound", "message_id", None, None),  # replaced below
            ("bound", "message_id", 0, wa_record.PROBLEM_BAD_VALUE),
            ("bound", "message_id", -1, wa_record.PROBLEM_BAD_VALUE),
            ("unbindable", "message_id", "77",
             wa_record.PROBLEM_BAD_TYPE),
            ("unbindable", "message_id", 0,
             wa_record.PROBLEM_BAD_VALUE),

            ("bound", "text_digest", 12345,
             wa_record.PROBLEM_BAD_TYPE),
            ("bound", "text_digest", "", wa_record.PROBLEM_BAD_VALUE),
            # UPPERCASE hex is not lowercase hex.
            ("bound", "text_digest", "Z" * 64,
             wa_record.PROBLEM_BAD_VALUE),
            ("bound", "text_digest", "A" * 64,
             wa_record.PROBLEM_BAD_VALUE),
            # 'g' is outside the hex alphabet, right length.
            ("bound", "text_digest", "g" * 64,
             wa_record.PROBLEM_BAD_VALUE),
            # wrong length, both directions.
            ("bound", "text_digest", "a" * 63,
             wa_record.PROBLEM_BAD_VALUE),
            ("bound", "text_digest", "a" * 65,
             wa_record.PROBLEM_TOO_LARGE),
            ("sending", "text_digest", "Z" * 64,
             wa_record.PROBLEM_BAD_VALUE),
            ("indefinite", "text_digest", "a" * 63,
             wa_record.PROBLEM_BAD_VALUE),
            ("failed_unsent", "text_digest", 12345,
             wa_record.PROBLEM_BAD_TYPE),

            ("required", "requested_at", "soon",
             wa_record.PROBLEM_BAD_TYPE),
            ("required", "requested_at", True,
             wa_record.PROBLEM_BAD_TYPE),
            # requested_at is required in EVERY state; absence is a
            # type failure, not a silent pass.
            ("required", "requested_at", None,
             wa_record.PROBLEM_BAD_TYPE),
            ("required", "requested_at", -1,
             wa_record.PROBLEM_BAD_VALUE),
            ("bound", "requested_at", "soon",
             wa_record.PROBLEM_BAD_TYPE),

            ("sending", "sent_at", "soon",
             wa_record.PROBLEM_BAD_TYPE),
            ("sending", "sent_at", True, wa_record.PROBLEM_BAD_TYPE),
            ("sending", "sent_at", -1, wa_record.PROBLEM_BAD_VALUE),
            ("bound", "sent_at", "soon", wa_record.PROBLEM_BAD_TYPE),
            ("indefinite", "sent_at", -1,
             wa_record.PROBLEM_BAD_VALUE),

            ("bound", "bound_at", "soon",
             wa_record.PROBLEM_BAD_TYPE),
            ("bound", "bound_at", True, wa_record.PROBLEM_BAD_TYPE),
            ("bound", "bound_at", -1, wa_record.PROBLEM_BAD_VALUE),
            ("unbindable", "bound_at", "soon",
             wa_record.PROBLEM_BAD_TYPE),
        )
        seen = set()
        for state, field, bad, problem in cases:
            if problem is None:
                # placeholder row for the None case, handled by the
                # §3.1 null/non-null check in T-M10; skip it here so
                # this table stays purely about TYPES.
                continue
            value = placeholder(state)
            value[field] = bad
            self.assert_problem(
                make_record(result_placeholder=value), problem,
                "state=%s %s=%r" % (state, field, bad),
            )
            seen.add(field)
        # Anti-vacuity: every conditional field of the §3.1 table is
        # actually covered by a row above. A field silently dropped
        # from the table (or from this test) fails here.
        self.assertEqual(
            seen, {"message_id", "text_digest", "bound_at",
                   "sent_at", "requested_at"},
        )
        # ... and the well-typed values still validate, so the rows
        # above are not passing because the fixture is broken.
        for state in sorted(EXPECTED_PLACEHOLDER_TABLE):
            assert_valid(
                self, make_record(result_placeholder=placeholder(state)),
                "well-typed placeholder in state %r" % state,
            )

    def test_M11_placeholder_must_be_an_object(self):
        """T-M11 (b): a non-dict placeholder is refused with
        PROBLEM_BAD_TYPE — by AUTHORED assertion.

        Round 01 covered `{}`, `0`, `""`, `[]` only through
        `assertRaises(RecordError)` inside T-M6, so deleting
        `_require_dict` died with a bare TypeError. A crash kill is
        not a kill (reviewer mutant N-23), so the exact code is
        asserted here instead.
        """
        for bad in ([], "", "x", 0, 3, True, 1.5, [1, 2], (1, 2)):
            self.assert_problem(
                make_record(result_placeholder=bad),
                wa_record.PROBLEM_BAD_TYPE, "placeholder=%r" % (bad,),
            )

    def test_placeholder_null_is_the_legacy_lane_and_validates(self):
        # Plan §1.1: None is not "not needed" — it is the legacy lane,
        # and it must remain valid forever.
        assert_valid(
            self, make_record(result_placeholder=None),
            "the legacy lane (result_placeholder is None)",
        )


class NormalizerTests(unittest.TestCase):
    """T-M6/T-M7: the load-boundary normalizer's anti-abuse pins."""

    def test_M6_normalizer_inserts_only_the_new_key_and_never_overwrites(
        self
    ):
        """T-M6: the normalizer inserts ONLY result_placeholder (and,
        nested, only the six additive delivery keys), always as None,
        never derived, and NEVER overwrites a value that is present."""
        # (i) absent -> inserted as None; and the set of keys ADDED at
        # the top level is EXACTLY {"result_placeholder"}. A normalizer
        # broadened to "fill anything missing" fails this by adding
        # more, and a normalizer that filled a DIFFERENT key fails it
        # by adding the wrong one.
        document = make_record()
        del document["result_placeholder"]
        before = set(document)
        wa_store._normalize_additive_keys(document)
        self.assertEqual(
            set(document) - before, {"result_placeholder"}
        )
        self.assertEqual(before - set(document), set())
        self.assertIsNone(document["result_placeholder"])

        # (ii) never DERIVED: the inserted value is None even when the
        # record carries a chat id, a verified result and a delivery
        # marker that a "helpful" normalizer could have synthesized a
        # binding from.
        document = make_record(
            verified_result={
                "summary": "done",
                "digest": text_digest("done"),
                "recorded_at": 5,
            },
            result_delivery=legacy_delivery("delivered", message_id=42),
        )
        del document["result_placeholder"]
        wa_store._normalize_additive_keys(document)
        self.assertIsNone(document["result_placeholder"])

        # (iii) never OVERWRITES a present value.
        present = placeholder("bound")
        document = make_record(result_placeholder=copy.deepcopy(present))
        wa_store._normalize_additive_keys(document)
        self.assertEqual(document["result_placeholder"], present)

        # (iv) never overwrites a present value that is FALSY but not
        # None. This is the observable form of "present is never
        # touched": a normalizer written as "if not
        # document.get(key)" would replace these with None and turn an
        # INVALID record into a valid one — silently repairing a
        # corrupt authorization record instead of failing closed.
        for falsy in ({}, 0, "", []):
            document = make_record(result_placeholder=falsy)
            wa_store._normalize_additive_keys(document)
            self.assertEqual(
                document["result_placeholder"], falsy, repr(falsy)
            )
            with self.assertRaises(
                wa_record.RecordError, msg=repr(falsy)
            ):
                wa_record.validate_record(document)

        # (v) nested: the additive delivery keys are inserted as None
        # ONLY when result_delivery is already a dict. A null marker is
        # never turned into a dict — nothing is created.
        document = make_record(result_delivery=None)
        del document["result_placeholder"]
        wa_store._normalize_additive_keys(document)
        self.assertIsNone(document["result_delivery"])

        document = make_record(
            result_delivery=legacy_delivery("partial")
        )
        del document["result_placeholder"]
        delivery_before = set(document["result_delivery"])
        wa_store._normalize_additive_keys(document)
        self.assertEqual(
            set(document["result_delivery"]) - delivery_before,
            set(EXPECTED_ADDITIVE_DELIVERY_KEYS),
        )
        for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
            self.assertIsNone(document["result_delivery"][key], key)
        # The legacy three are untouched, by value.
        self.assertEqual(
            {k: document["result_delivery"][k]
             for k in EXPECTED_LEGACY_DELIVERY_KEYS},
            legacy_delivery("partial"),
        )

        # (vi) nested: a present additive value — including a falsy
        # one — is never overwritten.
        marker = dict(
            legacy_delivery("delivered", message_id=9),
            problem="",
            edited_message_id=0,
        )
        document = make_record(result_delivery=copy.deepcopy(marker))
        wa_store._normalize_additive_keys(document)
        self.assertEqual(document["result_delivery"]["problem"], "")
        self.assertEqual(
            document["result_delivery"]["edited_message_id"], 0
        )
        with self.assertRaises(wa_record.RecordError):
            wa_record.validate_record(document)

        # (vii) TOTAL: a non-dict is returned untouched rather than
        # crashing the load boundary. Asserted by an AUTHORED failure
        # message, not by letting the exception surface as an ERROR —
        # removing the isinstance guard raises TypeError, and a crash
        # kill is not a kill (reviewer mutant N-24).
        for value in (None, [], "x", 3, 1.5, True, (1, 2)):
            try:
                returned = wa_store._normalize_additive_keys(value)
            except Exception as exc:
                self.fail(
                    "the normalizer must be TOTAL: %r raised %r"
                    % (value, exc)
                )
            self.assertEqual(returned, value, repr(value))

    def test_M7_unknown_and_other_missing_keys_are_still_rejected(self):
        """T-M7 (anti-vacuity for T-M6): strict closed-key validation
        runs UNCHANGED after the normalizer. It fills no other missing
        key, and it repairs nothing."""
        # Every other top-level key stays required after normalization,
        # and the normalizer does not re-insert it.
        for key in wa_record._TOP_LEVEL_KEYS:
            if key == "result_placeholder":
                continue
            document = make_record()
            del document[key]
            wa_store._normalize_additive_keys(document)
            self.assertNotIn(
                key, document,
                "the normalizer must not fill %r" % key,
            )
            expected = (
                wa_record.PROBLEM_SCHEMA_VERSION
                if key == "schema_version"
                else wa_record.PROBLEM_MISSING_KEY
            )
            try:
                wa_record.validate_record(document)
            except wa_record.RecordError as caught:
                self.assertEqual(caught.problem, expected, key)
            except Exception as exc:  # pragma: no cover - defensive
                self.fail(
                    "missing %r must fail closed, not raise %r"
                    % (key, exc)
                )
            else:
                self.fail("missing %r must fail closed" % key)

        # An unknown top-level key is still REFUSED after
        # normalization.
        document = make_record()
        document["extra_authority"] = True
        wa_store._normalize_additive_keys(document)
        with self.assertRaises(wa_record.RecordError) as caught:
            wa_record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, wa_record.PROBLEM_UNKNOWN_KEY
        )

        # A missing LEGACY delivery key is still refused: the nested
        # normalizer fills only the six additive keys.
        document = make_record(
            result_delivery=legacy_delivery("reserved")
        )
        del document["result_delivery"]["reserved_at"]
        wa_store._normalize_additive_keys(document)
        self.assertNotIn("reserved_at", document["result_delivery"])
        with self.assertRaises(wa_record.RecordError) as caught:
            wa_record.validate_record(document)
        self.assertEqual(
            caught.exception.problem, wa_record.PROBLEM_MISSING_KEY
        )


class StoreLoadBoundaryTests(unittest.TestCase):
    """The normalizer is wired at the LOAD boundary, and only there."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name
        self.path = os.path.join(
            self.directory, wa_store.WORKFLOWS_FILE_NAME
        )

    def write_raw(self, *records):
        """Write a store holding one OR MORE raw records.

        Takes *records rather than a single record on purpose: a
        one-record-only fixture cannot distinguish "the load boundary
        normalizes every record" from "it normalizes the first one",
        and the second is what the round-01 fixture actually pinned
        (reviewer blocker B-2 / mutant N-15).
        """
        document = {
            "workflow_store_schema_version": 2,
            "workflows": {
                record["workflow_id"]: record for record in records
            },
        }
        self.assertEqual(
            len(document["workflows"]), len(records),
            "the fixture's records must have distinct workflow ids",
        )
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        os.chmod(self.path, 0o600)

    def legacy_record(self, workflow_id, delivery="unset"):
        """A record in its PRE-increment on-disk shape: no
        result_placeholder key, and no additive delivery keys."""
        record = make_record(workflow_id=workflow_id)
        del record["result_placeholder"]
        if delivery != "unset":
            record["result_delivery"] = delivery
        if isinstance(record.get("result_delivery"), dict):
            for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
                record["result_delivery"].pop(key, None)
        return record

    def test_F1_delivery_receipt_per_state_invariants_fail_closed(self):
        """I5 / round-01 F1: the result_delivery receipt carries TOTAL
        per-state invariants — each state's required fields must be
        present and every other additive field must be null — and a
        violation fails CLOSED at SAVE, at LOAD, and through migration.

        This closes the false-receipt defect: a `delivered_by_edit`
        with no rendered digest, no edited id and no timestamps used to
        VALIDATE, so the adapter treated the workflow as delivered
        without the proof that word implies.
        """
        def assert_refused(document, problem, note):
            try:
                wa_record.validate_record(document)
            except wa_record.RecordError as caught:
                self.assertEqual(caught.problem, problem, note)
                return
            except Exception as exc:  # pragma: no cover - defensive
                self.fail("%s must fail CLOSED; raised %r" % (note, exc))
            self.fail("%s must fail closed; it validated" % note)

        # H1: an edit-lane marker needs a COMPLETE record (COMPLETED,
        # verified_result, bound placeholder); a legacy marker a bare
        # one. `delivery_record` routes each correctly.
        dev_record = delivery_record

        plausible = {
            "verified_result_digest": "a" * 64,
            "rendered_digest": "b" * 64,
            "edited_message_id": 5,
            "attempted_at": 5,
            "settled_at": 5,
            "problem": "an edit problem",
        }
        coherent = {
            "reserved": legacy_delivery("reserved"),
            "delivered": legacy_delivery("delivered", message_id=9),
            "partial": legacy_delivery("partial"),
            "edit_pending": edit_delivery("edit_pending"),
            "delivered_by_edit": edit_delivery("delivered_by_edit"),
            "degraded_unbindable": edit_delivery("degraded_unbindable"),
            "degraded_unrenderable": edit_delivery(
                "degraded_unrenderable"
            ),
            "edit_indefinite": edit_delivery("edit_indefinite"),
        }
        # Every delivery state has a coherent shape here (anti-vacuity),
        # and flipping the null-ness of ANY additive field is refused
        # with the F1 problem code, naming the field.
        for state, marker in coherent.items():
            assert_valid(
                self,
                dev_record(copy.deepcopy(marker)),
                "coherent %s marker" % state,
            )
            for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
                bad = copy.deepcopy(marker)
                bad[key] = None if bad.get(key) is not None \
                    else plausible[key]
                assert_refused(
                    dev_record(bad),
                    wa_record.PROBLEM_DELIVERY_STATE_FIELDS,
                    "%s.%s null-ness flipped" % (state, key),
                )

        # SAVE boundary: a false receipt is refused BEFORE it touches
        # the filesystem (store.save validates the whole document).
        false_edit = edit_delivery("delivered_by_edit")
        false_edit["rendered_digest"] = None
        document = {
            "workflow_store_schema_version": 2,
            "workflows": {"wf-0001": edit_lane_record(false_edit)},
        }
        store = wa_store.WorkflowStore(self.directory)
        with self.assertRaises(wa_store.StoreError) as caught_save:
            store.save(document)
        self.assertIn("rendered_digest", str(caught_save.exception))
        self.assertFalse(
            os.path.exists(self.path),
            "an invalid document must never clobber the store",
        )

        # LOAD boundary: a false receipt written raw to disk fails the
        # WHOLE load closed, naming the record — never silently loaded.
        raw = edit_lane_record(edit_delivery("delivered_by_edit"))
        raw["result_delivery"]["edited_message_id"] = None
        self.write_raw(raw)
        with self.assertRaises(wa_store.StoreError) as caught_load:
            wa_store.WorkflowStore(self.directory).load()
        self.assertIn("edited_message_id", str(caught_load.exception))
        self.assertIn("wf-0001", str(caught_load.exception))

        # MIGRATION truth: a pre-increment legacy record whose delivery
        # marker was hand-populated with additive fields (a shape the
        # engine never wrote) fails the load closed rather than being
        # silently accepted as delivered.
        forged = self.legacy_record(
            "wf-0002",
            delivery=legacy_delivery("delivered", message_id=3),
        )
        forged["result_delivery"]["verified_result_digest"] = "c" * 64
        self.write_raw(forged)
        with self.assertRaises(wa_store.StoreError) as caught_mig:
            wa_store.WorkflowStore(self.directory).load()
        self.assertEqual(
            wa_record.PROBLEM_DELIVERY_STATE_FIELDS
            in str(caught_mig.exception)
            or "verified_result_digest" in str(caught_mig.exception),
            True,
        )

    def test_H1_edit_lane_prerequisites_fail_closed(self):
        """I5 / round-03 H1: EVERY edit-lane receipt requires the edit
        engine's own preconditions — phase COMPLETED, a non-null
        verified_result, and a BOUND placeholder. A receipt in ANY edit
        state without them is IMPOSSIBLE (the engine never wrote it) and
        would strand forever, since those same preconditions fail before
        the engine ever looks at the receipt. It fails closed at SAVE,
        LOAD, and migration. TOTAL over DELIVERY_EDIT_STATES.
        """
        def assert_refused(document, note):
            try:
                wa_record.validate_record(document)
            except wa_record.RecordError as caught:
                self.assertEqual(
                    caught.problem,
                    wa_record.PROBLEM_DELIVERY_BINDING, note)
                return
            except Exception as exc:  # pragma: no cover
                self.fail("%s must fail CLOSED; raised %r" % (note, exc))
            self.fail("%s must fail closed; it validated" % note)

        for state in wa_record.DELIVERY_EDIT_STATES:
            marker = edit_delivery(state)
            # ANTI-VACUITY: the complete, engine-shaped record validates.
            assert_valid(
                self, edit_lane_record(copy.deepcopy(marker)),
                "a complete %s record" % state)
            # (a) phase not COMPLETED.
            assert_refused(
                edit_lane_record(copy.deepcopy(marker),
                                 phase=wa_record.PHASE_PLANNED),
                "%s on a PLANNED record" % state)
            # (b) no verified_result.
            assert_refused(
                edit_lane_record(copy.deepcopy(marker),
                                 verified_result=None),
                "%s with no verified_result" % state)
            # (c) placeholder not bound (the Lead's own probe: an
            # edit_pending receipt against a `required` placeholder).
            assert_refused(
                edit_lane_record(copy.deepcopy(marker),
                                 placeholder_state="required"),
                "%s on a `required` placeholder" % state)

        # SAVE / LOAD / MIGRATION for one representative impossible
        # combo: an edit_pending receipt on a PLANNED record.
        impossible = edit_lane_record(
            edit_delivery("edit_pending"), phase=wa_record.PHASE_PLANNED)
        document = {
            "workflow_store_schema_version": 2,
            "workflows": {"wf-0001": copy.deepcopy(impossible)},
        }
        with self.assertRaises(wa_store.StoreError) as caught_save:
            wa_store.WorkflowStore(self.directory).save(document)
        self.assertIn("not COMPLETED", str(caught_save.exception))
        self.assertFalse(os.path.exists(self.path))

        self.write_raw(copy.deepcopy(impossible))
        with self.assertRaises(wa_store.StoreError) as caught_load:
            wa_store.WorkflowStore(self.directory).load()
        self.assertIn("wf-0001", str(caught_load.exception))
        self.assertIn("edit-lane", str(caught_load.exception))

    def test_G1_delivered_by_edit_binding_fails_closed(self):
        """I5 / round-02 G1: a delivered_by_edit receipt must name the
        object that was actually edited — its edited_message_id MUST
        equal the bound result_placeholder.message_id. A receipt naming
        a different object (edited_message_id=999) is a RELATIONAL lie
        that would suppress delivery forever while /status claims
        success. It fails CLOSED at SAVE, at LOAD, and through migration.
        """
        def forged():
            # A COMPLETE edit-lane record (H1 prerequisites hold) whose
            # bound placeholder names 77 but whose receipt claims 999,
            # so ONLY the edited_message_id relation can refuse it.
            return edit_lane_record(edit_delivery(
                "delivered_by_edit", edited_message_id=999))

        # ANTI-VACUITY: the matching receipt validates.
        assert_valid(
            self,
            edit_lane_record(edit_delivery("delivered_by_edit")),
            "a delivered_by_edit whose edited id matches the binding",
        )

        # SAVE: refused before touching disk, by the relational code.
        document = {
            "workflow_store_schema_version": 2,
            "workflows": {"wf-0001": forged()},
        }
        with self.assertRaises(wa_store.StoreError) as caught_save:
            wa_store.WorkflowStore(self.directory).save(document)
        self.assertIn("edited_message_id", str(caught_save.exception))
        self.assertIn("must equal the bound", str(caught_save.exception))
        self.assertFalse(os.path.exists(self.path))

        # LOAD: the same forged record on disk fails the whole load.
        self.write_raw(forged())
        with self.assertRaises(wa_store.StoreError) as caught_load:
            wa_store.WorkflowStore(self.directory).load()
        self.assertIn("edited_message_id", str(caught_load.exception))
        self.assertIn("wf-0001", str(caught_load.exception))

        # MIGRATION: a record whose placeholder message_id and delivery
        # edited_message_id disagree on disk fails closed, never loaded
        # as delivered.
        drifted = edit_lane_record(
            edit_delivery("delivered_by_edit", edited_message_id=77))
        drifted["result_placeholder"]["message_id"] = 1234
        self.write_raw(drifted)
        with self.assertRaises(wa_store.StoreError) as caught_mig:
            wa_store.WorkflowStore(self.directory).load()
        self.assertIn("must equal the bound", str(caught_mig.exception))

    def test_M6_store_load_materializes_the_additive_keys(self):
        """T-M6 (store level): a record already on disk, written
        before this increment existed, LOADS — no schema bump, no
        migration, no retirement."""
        record = make_record(
            result_delivery=legacy_delivery("delivered", message_id=42)
        )
        del record["result_placeholder"]
        for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
            record["result_delivery"].pop(key, None)
        self.write_raw(record)
        try:
            loaded = wa_store.WorkflowStore(self.directory).load()
        except wa_store.StoreError as exc:
            self.fail(
                "a legacy record must load without a schema bump or a"
                " migration; the store refused it: %s" % exc
            )
        entry = loaded["workflows"]["wf-0001"]
        self.assertIn("result_placeholder", entry)
        self.assertIsNone(entry["result_placeholder"])
        for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
            self.assertIn(key, entry["result_delivery"], key)
            self.assertIsNone(entry["result_delivery"][key], key)
        self.assertEqual(entry["result_delivery"]["state"], "delivered")
        self.assertEqual(
            entry["result_delivery"]["telegram_message_id"], 42
        )

    def test_M6_store_load_normalizes_EVERY_record_not_just_the_first(
        self
    ):
        """T-M6 (round-02, closes reviewer blocker B-2): the load
        boundary normalizes EVERY record in the store.

        A real store holds up to MAX_WORKFLOW_RECORDS. Under a loop
        that stops after the first record, every other legacy record
        fails validation with PROBLEM_MISSING_KEY and the WHOLE store
        refuses to open — the increment's central migration guarantee,
        pinned in round 01 only for a one-record store.

        Three MIXED records, deliberately ordered so the first one is
        already fine under a truncating loop and the failure must come
        from a later one:

          wf-0001  legacy, result_delivery is null
          wf-0002  legacy, a three-key 'delivered' marker (needs the
                   NESTED normalization too, on a non-first record)
          wf-0003  already migrated: a bound placeholder and all nine
                   delivery keys (must be left exactly as it is)
        """
        # RETARGETED under I5/F1: an already-migrated record carries a
        # COHERENT nine-key edit-lane marker (delivered_by_edit: the
        # edit lane records edited_message_id, never the legacy
        # telegram_message_id). All nine keys are present; the ones the
        # state does not use (telegram_message_id, problem) are null.
        already_migrated = edit_lane_record(
            edit_delivery("delivered_by_edit"),
            workflow_id="wf-0003",
        )
        expected_migrated = copy.deepcopy(already_migrated)
        self.write_raw(
            self.legacy_record("wf-0001", delivery=None),
            self.legacy_record(
                "wf-0002",
                delivery=legacy_delivery("delivered", message_id=42),
            ),
            already_migrated,
        )
        try:
            loaded = wa_store.WorkflowStore(self.directory).load()
        except wa_store.StoreError as exc:
            self.fail(
                "a multi-record legacy store must load; the store"
                " refused it: %s" % exc
            )
        workflows = loaded["workflows"]
        self.assertEqual(
            sorted(workflows), ["wf-0001", "wf-0002", "wf-0003"]
        )
        # EVERY record carries the materialized key — asserted one by
        # one so a loop that stops early cannot pass on an aggregate.
        for workflow_id in ("wf-0001", "wf-0002", "wf-0003"):
            self.assertIn(
                "result_placeholder", workflows[workflow_id],
                "record %s was not normalized" % workflow_id,
            )
        self.assertIsNone(workflows["wf-0001"]["result_placeholder"])
        self.assertIsNone(workflows["wf-0002"]["result_placeholder"])
        self.assertIsNone(workflows["wf-0001"]["result_delivery"])
        # The NESTED normalization also ran on a NON-FIRST record.
        second = workflows["wf-0002"]["result_delivery"]
        for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
            self.assertIn(key, second, key)
            self.assertIsNone(second[key], key)
        self.assertEqual(second["state"], "delivered")
        self.assertEqual(second["telegram_message_id"], 42)
        # The already-migrated record is returned byte-for-byte: the
        # normalizer overwrote nothing on it.
        self.assertEqual(workflows["wf-0003"], expected_migrated)

    def test_M7_store_load_still_fails_closed_on_a_corrupt_record(self):
        """T-M7 (store level): the normalizer does not make the store
        forgiving. A record missing any OTHER key, or carrying an
        unknown one, still refuses the whole load."""
        record = make_record()
        del record["result_placeholder"]
        del record["phase"]
        self.write_raw(record)
        with self.assertRaises(wa_store.StoreError):
            wa_store.WorkflowStore(self.directory).load()

        record = make_record()
        del record["result_placeholder"]
        record["extra_authority"] = True
        self.write_raw(record)
        with self.assertRaises(wa_store.StoreError):
            wa_store.WorkflowStore(self.directory).load()

    def test_save_is_not_normalized(self):
        # Deliberately asymmetric: a WRITER must produce a complete
        # record. Normalizing on the way out would hide exactly that
        # defect, so save still refuses an incomplete document.
        record = make_record()
        del record["result_placeholder"]
        document = {
            "workflow_store_schema_version": 2,
            "workflows": {"wf-0001": record},
        }
        with self.assertRaises(wa_store.StoreError):
            wa_store.WorkflowStore(self.directory).save(document)


class DeliveryStatusStringTests(unittest.TestCase):

    def test_X2_legacy_status_strings_are_unchanged(self):
        """T-X2: the four EXISTING _render_delivery_status outputs,
        pinned verbatim. The schema extension must not alter one
        character of what a human already reads in /status."""
        render = adapter_module._render_delivery_status
        self.assertEqual(render(None), "recorded (delivery pending)")
        self.assertEqual(
            render(legacy_delivery("delivered", message_id=42)),
            "delivered",
        )
        self.assertEqual(
            render(legacy_delivery("partial", reserved_at=5)),
            "recorded; delivered INCOMPLETELY (some chunks shown; not"
            " retried automatically, since t=5)",
        )
        self.assertEqual(
            render(legacy_delivery("reserved", reserved_at=5)),
            "recorded; delivery attempted but UNCONFIRMED (not retried"
            " automatically, since t=5)",
        )
        # The SAME four strings after the load-boundary normalizer has
        # added the six additive fields: normalization changes nothing
        # a human sees.
        for marker in (
            legacy_delivery("delivered", message_id=42),
            legacy_delivery("partial", reserved_at=5),
            legacy_delivery("reserved", reserved_at=5),
        ):
            legacy_text = render(copy.deepcopy(marker))
            document = make_record(
                result_delivery=copy.deepcopy(marker)
            )
            wa_store._normalize_additive_keys(document)
            self.assertEqual(
                render(document["result_delivery"]), legacy_text
            )

    def test_new_delivery_states_each_have_their_own_status_branch(
        self
    ):
        """RETARGETED in I5, deliberately and disclosed.

        In I1 this test asserted the opposite property — that the five
        edit-lane states had NO /status branch yet and therefore landed
        in the unknown/unmapped fail-loud fallback. Its I1 docstring
        said so explicitly and named where that would change: "their
        own branches land with the delivery engine in I5 (T-X1)". I5
        has now implemented them, so the old assertion states a rule
        the code no longer follows.

        The recorded rule is that a test whose NAME or comment states
        an inverted rule must be RETARGETED in the SAME change rather
        than left green against a stale contract. This is that
        retarget, and it is strictly STRONGER than what it replaces:
        each state must now have its OWN distinct wording, and the
        fail-loud fallback must STILL be reachable.
        """
        render = adapter_module._render_delivery_status
        rendered = {}
        for state in wa_record.DELIVERY_EDIT_STATES:
            text = render(legacy_delivery(state))
            rendered[state] = text
            # No longer the fallback.
            self.assertNotIn("delivery state unrecognized", text, state)
            # And never dishonest: nothing claims plain "delivered" or
            # "pending" unless it is that.
            self.assertNotIn("delivery pending", text, state)
        self.assertEqual(
            len(set(rendered.values())), len(rendered),
            "every edit-lane state needs its OWN wording: %r" % rendered,
        )
        # The two TERMINAL degraded states say so, and say a human must
        # act — they are never retried.
        for state in (wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                      wa_record.DELIVERY_DEGRADED_UNRENDERABLE):
            self.assertIn("TERMINAL", rendered[state], state)
        self.assertIn(
            "no replacement message is ever sent",
            rendered[wa_record.DELIVERY_DEGRADED_UNBINDABLE],
        )
        self.assertIn(
            "never chunked and never truncated",
            rendered[wa_record.DELIVERY_DEGRADED_UNRENDERABLE],
        )

    def test_the_fail_loud_fallback_is_still_reachable(self):
        """The retarget above must not have removed the fallback: a
        genuinely unknown state still fails loud."""
        render = adapter_module._render_delivery_status
        for state in ("teleported", "", None, 0, True):
            text = render(legacy_delivery(state))
            self.assertIn("delivery state unrecognized", text,
                          repr(state))
            self.assertNotIn("delivered by editing", text, repr(state))


class DeliveryAuthorityTests(unittest.TestCase):

    def test_X3_delivery_authority_stays_none_on_every_new_field(self):
        """T-X3: no new field is an authority field. No placeholder
        state, and no additive delivery field across BOTH coherent
        delivery shapes, changes delivery_authority, makes a non-'none'
        value acceptable, or leaks into the authority-bearing role-turn
        projection.

        RETARGETED under I5/F1 and I5/G1: the additive fields are
        STATE-CONDITIONAL and delivered_by_edit is RELATIONALLY bound to
        its placeholder, so coherent records are used. Round-02 G2: the
        delivery-authority REJECTION MATRIX runs against BOTH coherent
        delivery shapes — critically including the degraded one that
        carries a non-null `problem` — so a mutant that permits a
        non-null authority only when `problem` is populated cannot
        survive (the prior retarget ran the matrix only against
        delivered_by_edit, where problem is null, and lost that
        coverage).
        """
        rejected_authorities = ("full", "partial", "none ", True, None, 1)

        def assert_authority_locked(document, note):
            assert_valid(self, document, note)
            self.assertEqual(
                document["delivery_authority"],
                wa_record.DELIVERY_AUTHORITY_NONE, note,
            )
            for value in rejected_authorities:
                bad = copy.deepcopy(document)
                bad["delivery_authority"] = value
                with self.assertRaises(
                    wa_record.RecordError, msg="%s / %r" % (note, value)
                ) as caught:
                    wa_record.validate_record(bad)
                self.assertEqual(
                    caught.exception.problem,
                    wa_record.PROBLEM_DELIVERY_AUTHORITY,
                    "%s / %r" % (note, value),
                )

        # Part 1: no PLACEHOLDER state confers delivery authority, and
        # the rejection matrix holds with the placeholder populated in
        # every state.
        for state in sorted(EXPECTED_PLACEHOLDER_TABLE):
            assert_authority_locked(
                make_record(result_placeholder=placeholder(state)),
                "placeholder state %r" % state,
            )

        # Part 2: no DELIVERY receipt confers authority, for BOTH
        # coherent shapes. delivered_by_edit is paired with its matching
        # bound placeholder (G1); the degraded shape carries `problem`
        # (G2), so the matrix now runs against a problem-populated
        # record.
        bound_ph = placeholder("bound")
        coherent_deliveries = (
            edit_delivery(
                "delivered_by_edit",
                edited_message_id=bound_ph["message_id"],
            ),
            edit_delivery("degraded_unbindable"),
        )
        # Anti-vacuity: prove the two shapes really differ on `problem`,
        # so the G2 coverage is genuine and not two identical records.
        self.assertIsNone(coherent_deliveries[0]["problem"])
        self.assertIsNotNone(coherent_deliveries[1]["problem"])

        projection = None
        for marker in coherent_deliveries:
            document = edit_lane_record(copy.deepcopy(marker))
            assert_authority_locked(
                document,
                "a bound placeholder with a coherent %s marker"
                " (problem populated=%s)"
                % (marker["state"], marker["problem"] is not None),
            )
            projection = json.dumps(role_turn._bounded_context(document))
            for key in (
                ("result_placeholder",) + EXPECTED_PLACEHOLDER_KEYS[1:]
                + EXPECTED_ADDITIVE_DELIVERY_KEYS
            ):
                self.assertNotIn('"%s"' % key, projection, key)
        # Anti-vacuity: the projection DOES carry delivery_authority.
        self.assertIn('"delivery_authority"', projection)
        self.assertIn('"none"', projection)


class LegacyMigrationTests(MissionCase):
    """T-M1..T-M5: strategy §4 dispositions, driven through the REAL
    adapter against records whose ON-DISK bytes predate this
    increment (no result_placeholder key, no additive delivery
    fields)."""

    def completed_workflow(self, harness, summary="mission verified"):
        harness.offer_mission()
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            for phase in (wa_record.PHASE_WORKSPACE_READY,
                          wa_record.PHASE_PREPARED,
                          wa_record.PHASE_VALIDATED,
                          wa_record.PHASE_DISPATCHED,
                          wa_record.PHASE_VERIFIED,
                          wa_record.PHASE_COMPLETED):
                wa_record.apply_transition(entry, phase)
            entry["verified_result"] = {
                "summary": summary,
                "digest": text_digest(summary),
                "recorded_at": NOW,
            }
            harness.workflow_store.save(workflows)

    def blocked_workflow(self, harness):
        harness.offer_mission()
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            wa_record.apply_transition(entry, wa_record.PHASE_BLOCKED)
            harness.workflow_store.save(workflows)

    def make_on_disk_legacy(self, harness, delivery="unset"):
        """Rewrite the stored record to its PRE-increment bytes: no
        result_placeholder key at all, and a result_delivery marker
        carrying only the three legacy keys. Written as raw JSON on
        purpose — WorkflowStore.save would (correctly) refuse it."""
        path = os.path.join(
            harness.tmpdir, wa_store.WORKFLOWS_FILE_NAME
        )
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        entry = raw["workflows"]["wf-0001"]
        entry.pop("result_placeholder", None)
        if delivery != "unset":
            entry["result_delivery"] = delivery
        if isinstance(entry.get("result_delivery"), dict):
            for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
                entry["result_delivery"].pop(key, None)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        os.chmod(path, 0o600)
        # The bytes on disk really are the legacy shape.
        with open(path, "r", encoding="utf-8") as handle:
            written = json.load(handle)["workflows"]["wf-0001"]
        self.assertNotIn("result_placeholder", written)
        return written

    def result_sends(self, harness):
        return [
            send for send in harness.sends()
            if RESULT_MESSAGE_HEADER in send["text"]
        ]

    def status_text(self, harness, update_id):
        harness.adapter.process_update(
            msg_update(update_id, "/status")
        )
        harness.drain_worker()
        return [
            send["text"] for send in harness.sends()
            if "Adapter state" in send["text"]
        ][-1]

    def assert_legacy_lane(self, harness):
        """Every legacy record reloads on the LEGACY LANE: the
        placeholder is None, and it was never fabricated."""
        try:
            workflows = harness.fresh_workflows()
        except wa_store.StoreError as exc:
            self.fail(
                "a record already on disk, written before this"
                " increment existed, must LOAD; the store refused it:"
                " %s" % exc
            )
        reloaded = workflows["workflows"]["wf-0001"]
        self.assertIn(
            "result_placeholder", reloaded,
            "the load boundary must materialize result_placeholder",
        )
        self.assertIsNone(reloaded["result_placeholder"])
        return reloaded

    def test_M1_legacy_delivered_stays_delivered_and_is_never_resent(
        self
    ):
        """T-M1: a legacy DELIVERED record keeps its message id, is
        never re-delivered or edited, and still renders 'delivered'."""
        harness = self.harness()
        self.completed_workflow(harness)
        self.make_on_disk_legacy(
            harness, legacy_delivery("delivered", message_id=4242)
        )
        before = len(self.result_sends(harness))
        harness.adapter.deliver_pending_results()
        self.assertEqual(len(self.result_sends(harness)), before)
        # Zero edits of any message on the result path.
        self.assertEqual(
            [entry for entry in harness.timeline
             if entry[0] == "editMessageText"], [],
        )
        reloaded = self.assert_legacy_lane(harness)
        self.assertEqual(
            reloaded["result_delivery"]["state"], "delivered"
        )
        self.assertEqual(
            reloaded["result_delivery"]["telegram_message_id"], 4242
        )
        self.assertEqual(
            adapter_module._render_delivery_status(
                reloaded["result_delivery"]
            ),
            "delivered",
        )

    def test_M2_legacy_reserved_is_terminal_truthful_and_not_retried(
        self
    ):
        """T-M2: a legacy RESERVED marker is terminal and degraded —
        never auto-retried, surfaced truthfully."""
        harness = self.harness()
        self.completed_workflow(harness)
        self.make_on_disk_legacy(
            harness, legacy_delivery("reserved", reserved_at=NOW)
        )
        before = len(self.result_sends(harness))
        harness.adapter.deliver_pending_results()
        self.assertEqual(len(self.result_sends(harness)), before)
        reloaded = self.assert_legacy_lane(harness)
        self.assertEqual(
            reloaded["result_delivery"]["state"], "reserved"
        )
        self.assertEqual(
            adapter_module._render_delivery_status(
                reloaded["result_delivery"]
            ),
            "recorded; delivery attempted but UNCONFIRMED (not retried"
            " automatically, since t=%s)" % NOW,
        )
        self.assertIn("wf-0001", self.status_text(harness, 30))

    def test_M3_legacy_partial_is_terminal_truthful_and_not_retried(
        self
    ):
        """T-M3: a legacy PARTIAL marker is terminal and degraded —
        never auto-retried (a retry would re-display shown chunks)."""
        harness = self.harness()
        self.completed_workflow(harness)
        self.make_on_disk_legacy(
            harness, legacy_delivery("partial", reserved_at=NOW)
        )
        before = len(self.result_sends(harness))
        harness.adapter.deliver_pending_results()
        self.assertEqual(len(self.result_sends(harness)), before)
        reloaded = self.assert_legacy_lane(harness)
        self.assertEqual(
            reloaded["result_delivery"]["state"], "partial"
        )
        self.assertEqual(
            adapter_module._render_delivery_status(
                reloaded["result_delivery"]
            ),
            "recorded; delivered INCOMPLETELY (some chunks shown; not"
            " retried automatically, since t=%s)" % NOW,
        )

    def test_M4_historical_blocked_record_is_untouched(self):
        """T-M4: a DI-REMOTE-2 historical BLOCKED workflow has no
        verified result; it loads, it is never delivered, and nothing
        about it is rewritten."""
        harness = self.harness()
        self.blocked_workflow(harness)
        written = self.make_on_disk_legacy(harness)
        self.assertIsNone(written["verified_result"])
        self.assertIsNone(written["result_delivery"])
        before = len(self.result_sends(harness))
        harness.adapter.deliver_pending_results()
        self.assertEqual(len(self.result_sends(harness)), before)
        reloaded = self.assert_legacy_lane(harness)
        self.assertEqual(reloaded["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(reloaded["verified_result"])
        self.assertIsNone(reloaded["result_delivery"])
        # Byte-level: every field the legacy record carried is
        # unchanged; only the additive key was materialized.
        self.assertEqual(
            {k: v for k, v in reloaded.items()
             if k != "result_placeholder"},
            written,
        )

    def test_M5_in_flight_completed_takes_the_legacy_at_most_once_lane(
        self
    ):
        """T-M5: an in-flight COMPLETED record with result_delivery
        None and NO placeholder was never bound, so exactly-once
        cannot be applied to it retroactively. It is delivered by the
        LEGACY at-most-once path, byte-for-byte as today. This
        increment does not claim exactly-once for it."""
        harness = self.harness()
        self.completed_workflow(harness, "external target issue resolved")
        written = self.make_on_disk_legacy(harness)
        self.assertIsNone(written["result_delivery"])
        harness.adapter.deliver_pending_results()
        sends = self.result_sends(harness)
        self.assertEqual(len(sends), 1)
        self.assertIn("external target issue resolved", sends[0]["text"])
        reloaded = self.assert_legacy_lane(harness)
        self.assertEqual(
            reloaded["result_delivery"]["state"], "delivered"
        )
        self.assertIsInstance(
            reloaded["result_delivery"]["telegram_message_id"], int
        )
        # The additive fields exist and are honestly EMPTY: the legacy
        # lane records no digests, because it proved none.
        for key in EXPECTED_ADDITIVE_DELIVERY_KEYS:
            self.assertIn(key, reloaded["result_delivery"], key)
            self.assertIsNone(reloaded["result_delivery"][key], key)
        # A second pass sends nothing more (at-most-once, unchanged).
        harness.adapter.deliver_pending_results()
        self.assertEqual(len(self.result_sends(harness)), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
