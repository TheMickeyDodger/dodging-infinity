"""Behavioral tests for Task 6 coordination (Mission Routing + Attention +
Bot Coordination).

Hermetic: stdlib ``unittest``, tmpdir stores, hermetic observation
sources, no network, no subprocess, no live effect of any kind. Nothing
here launches a Mission or Capability, sends a message, dispatches
Herdr work, or performs delivery.

Sections (step 1, the fail-closed spine):
  A  record: id grammar and minter, closed vocabularies and their parity
     with Mission Core (test-side only), context/provenance shapes, the
     authority-none constant, bounded validators
  B  observation: the one-method read-only protocol, validation on
     receipt, freshness classification (FRESH / STALE / INCONSISTENT /
     ABSENT / UNAVAILABLE), conservative handling of a misbehaving source
  C  store: closed document, schema version, store_sequence conflict
     guard, mode refusal, caps, fail-closed load, validate-before-write
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

from coordination import attention  # noqa: E402
from coordination import binding  # noqa: E402
from coordination import handoff  # noqa: E402
from coordination import observation  # noqa: E402
from coordination import record  # noqa: E402
from coordination import routing  # noqa: E402
from coordination import service  # noqa: E402
from coordination import store  # noqa: E402
from mission import record as mission_record  # noqa: E402

HEX_A = "a" * 64
HEX_B = "b" * 64
MISSION_X = "mn-" + "1" * 32
MISSION_Y = "mn-" + "2" * 32
EVIDENCE_1 = "mv-" + "3" * 32
EVIDENCE_2 = "mv-" + "4" * 32
ARTIFACT_1 = "mf-" + "5" * 32
NOW = 1_800_000_000


def context(**overrides):
    fields = {
        "transport": "grok_mcp",
        "principal_kind": record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
        "principal_ref": "credential-0",
        "configured_subject": "operator",
    }
    fields.update(overrides)
    return record.AuthenticatedContext(**fields)


def evidence_ref(**overrides):
    """An ACCEPTED verification record, as the source proves it."""
    fields = {
        "evidence_id": EVIDENCE_1,
        "kind": record.EVIDENCE_KIND_VERIFICATION_RECORD,
        "accepted": True,
        "acceptance_digest_sha256": HEX_B,
        "accepted_at": NOW - 20,
    }
    fields.update(overrides)
    return observation.EvidenceReference(**fields)


def recorded_evidence_ref(**overrides):
    """A merely RECORDED (unaccepted) evidence record."""
    fields = {
        "evidence_id": EVIDENCE_2,
        "kind": record.EVIDENCE_KIND_NARRATIVE_CLAIM,
        "accepted": False,
        "acceptance_digest_sha256": None,
        "accepted_at": None,
    }
    fields.update(overrides)
    return observation.EvidenceReference(**fields)


def receipt(**overrides):
    fields = {
        "available": True,
        "content_digest_sha256": HEX_A,
        "validated_at": NOW - 10,
    }
    fields.update(overrides)
    return fields


def artifact_ref(**overrides):
    fields = {
        "artifact_id": ARTIFACT_1,
        "role": record.ARTIFACT_ROLE_PRODUCED,
        "receipt": receipt(),
    }
    fields.update(overrides)
    return observation.ArtifactReference(**fields)


def condition(**overrides):
    fields = {
        "kind": record.ATTENTION_BLOCKED,
        "key": "reviewer.loop",
        "revision": 2,
        "detail": "reviewer rejected twice",
        "evidence_refs": (evidence_ref(),),
        "artifact_refs": (),
    }
    fields.update(overrides)
    return observation.ObservedCondition(**fields)


def observed(**overrides):
    fields = {
        "mission_id": MISSION_X,
        "current_revision": 2,
        "proposal_digest_sha256": HEX_A,
        "lifecycle_state": record.LIFECYCLE_AUTHORIZED,
        "authorization_digest_sha256": HEX_B,
        "state_cursor": "7",
        "repository_url": "https://github.com/Example/Repo",
        "conditions": (condition(),),
        "evidence_refs": (evidence_ref(), recorded_evidence_ref()),
        "artifact_refs": (artifact_ref(),),
        "observed_at": NOW - 5,
        "source": "hermetic_fake",
    }
    fields.update(overrides)
    return observation.MissionObservation(**fields)


class FakeSource(observation.MissionObservationSource):
    """Hermetic source: a fixed answer per Mission id, read-only."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def observe(self, mission_id):
        self.calls.append(mission_id)
        return self.answers[mission_id]


class RaisingSource(observation.MissionObservationSource):
    def observe(self, mission_id):
        raise RuntimeError("secret detail that must never be copied")


class WrongTypeSource(observation.MissionObservationSource):
    def observe(self, mission_id):
        return {"status": "OBSERVED"}


# =====================================================================
# A. record
# =====================================================================


class IdentityTests(unittest.TestCase):

    def test_mints_only_coordination_prefixes(self):
        for prefix in record.ID_PREFIXES:
            minted = record.mint_id(prefix)
            self.assertIsNone(record.id_problem(minted, prefix))
            self.assertEqual(len(minted), len(prefix) + 1 + record.ID_HEX_CHARS)
        self.assertEqual(
            set(record.ID_PREFIXES), {"cr", "cb", "ca", "ch"})

    def test_never_mints_a_mission_core_identifier(self):
        for foreign in (record.MISSION_ID_PREFIX, record.EVIDENCE_ID_PREFIX,
                        record.ARTIFACT_ID_PREFIX, "mq", "md"):
            with self.assertRaises(record.CoordinationError) as caught:
                record.mint_id(foreign)
            self.assertEqual(caught.exception.problem, record.PROBLEM_ID_GRAMMAR)

    def test_id_grammar_refusals(self):
        self.assertEqual(record.id_problem(42, "cr"), "must be a string")
        self.assertIn("characters", record.id_problem("cr-abc", "cr"))
        self.assertIn("start with", record.id_problem("cb-" + "a" * 32, "cr"))
        self.assertIn("hex", record.id_problem("cr-" + "G" * 32, "cr"))
        self.assertIsNone(record.id_problem(MISSION_X, "mn"))
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_id("cr-" + "A" * 32, "cr", "route_id")
        self.assertEqual(caught.exception.problem, record.PROBLEM_ID_GRAMMAR)

    def test_mint_is_injectable_and_deterministic_for_tests(self):
        minted = record.mint_id("cr", token_hex=lambda n: "0" * (2 * n))
        self.assertEqual(minted, "cr-" + "0" * 32)


class VocabularyParityTests(unittest.TestCase):
    """Task 6 re-declares what it needs from Mission Core and never
    imports it; parity is pinned from the test side only."""

    def test_identity_grammar_matches_mission_core(self):
        self.assertEqual(record.ID_HEX_CHARS, mission_record.ID_HEX_CHARS)
        self.assertEqual(record.MISSION_ID_PREFIX, mission_record.MISSION_ID_PREFIX)
        self.assertEqual(record.EVIDENCE_ID_PREFIX, mission_record.EVIDENCE_ID_PREFIX)
        self.assertEqual(record.ARTIFACT_ID_PREFIX, mission_record.ARTIFACT_ID_PREFIX)
        self.assertFalse(set(record.ID_PREFIXES) & set(mission_record.ID_PREFIXES))

    def test_context_and_lifecycle_vocabularies_match_mission_core(self):
        self.assertEqual(record.CONTEXT_KEYS, mission_record.CONTEXT_KEYS)
        self.assertEqual(record.PRINCIPAL_KINDS, mission_record.PRINCIPAL_KINDS)
        self.assertEqual(record.LIFECYCLE_STATES, mission_record.MISSION_STATES)
        self.assertEqual(record.PROOF_TRANSPORT_CREDENTIAL_ONLY,
                         mission_record.PROOF_TRANSPORT_CREDENTIAL_ONLY)

    def test_closed_vocabularies(self):
        self.assertEqual(record.ROUTE_OUTCOMES, (
            "CLARIFICATION_REQUIRED", "EXISTING_MISSION", "NEW_PROPOSAL"))
        self.assertEqual(record.ATTENTION_KINDS, (
            "AUTHORIZATION_READY", "BLOCKED", "NEEDS_HUMAN", "RESULT_READY"))
        self.assertEqual(record.ATTENTION_PRIORITY, {
            "NEEDS_HUMAN": 10, "AUTHORIZATION_READY": 20, "BLOCKED": 30,
            "RESULT_READY": 40})
        self.assertEqual(record.PRESENTATION_STATES, (
            "ACKNOWLEDGED", "OBSOLETE", "PENDING", "RESOLVED", "SURFACED"))
        self.assertEqual(record.HANDOFF_STATUSES, (
            "ACCEPTED", "ANSWERED", "DECLINED", "OPEN", "WITHDRAWN"))
        self.assertEqual(record.LANES, ("ENGINEERING_LANE",))
        self.assertEqual(record.SUPPORTED_DOMAINS, ("ENGINEERING",))
        self.assertEqual(record.FRESHNESS_STATES, (
            "ABSENT", "FRESH", "INCONSISTENT", "STALE", "UNAVAILABLE"))
        self.assertNotIn("NATURAL_LANGUAGE_TURN", record.ROUTE_TIERS)
        self.assertEqual(len(record.ROUTE_TIERS), 9)

    def test_transition_tables_are_closed_and_terminal_states_have_no_exit(self):
        for state in record.PRESENTATION_STATES:
            self.assertIn(state, record.PRESENTATION_TRANSITIONS)
        for state in record.HANDOFF_STATUSES:
            self.assertIn(state, record.HANDOFF_TRANSITIONS)
        self.assertEqual(record.PRESENTATION_TRANSITIONS["OBSOLETE"], frozenset())
        self.assertEqual(record.PRESENTATION_TRANSITIONS["RESOLVED"], frozenset())
        # A-R1c: acknowledging never immunises a stale presentation, and a
        # FRESH observation that no longer carries the condition resolves it.
        self.assertEqual(record.PRESENTATION_TRANSITIONS["ACKNOWLEDGED"],
                         frozenset(("OBSOLETE", "RESOLVED")))
        self.assertNotIn("SURFACED", record.PRESENTATION_TRANSITIONS["ACKNOWLEDGED"])
        for terminal in ("ANSWERED", "DECLINED", "WITHDRAWN"):
            self.assertEqual(record.HANDOFF_TRANSITIONS[terminal], frozenset())
        self.assertEqual(record.HANDOFF_TRANSITIONS["OPEN"],
                         frozenset(("ACCEPTED", "DECLINED", "WITHDRAWN")))
        self.assertEqual(record.HANDOFF_TRANSITIONS["ACCEPTED"],
                         frozenset(("ANSWERED", "WITHDRAWN")))

    def test_every_problem_code_is_distinct_and_prefixed(self):
        codes = [value for name, value in vars(record).items()
                 if name.startswith("PROBLEM_")]
        self.assertGreater(len(codes), 25)
        self.assertEqual(len(set(codes)), len(codes))
        for code in codes:
            self.assertTrue(code.startswith("coordination_"), code)
        self.assertEqual(record.PROBLEM_CONTEXT_MISMATCH,
                         "coordination_context_mismatch")


class ContextAndProvenanceTests(unittest.TestCase):

    def test_context_validates_and_round_trips(self):
        ctx = context().validate()
        self.assertEqual(record.context_from_dict(ctx.as_dict()), ctx)
        self.assertEqual(tuple(ctx.as_dict()), record.CONTEXT_KEYS)

    def test_context_refusals(self):
        with self.assertRaises(record.CoordinationError) as caught:
            context(transport="Grok MCP").validate()
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        with self.assertRaises(record.CoordinationError) as caught:
            context(principal_kind="human").validate()
        self.assertEqual(caught.exception.problem, record.PROBLEM_PROVENANCE)
        with self.assertRaises(record.CoordinationError) as caught:
            context(principal_ref="x" * (record.MAX_PRINCIPAL_REF_CHARS + 1)).validate()
        self.assertEqual(caught.exception.problem, record.PROBLEM_TOO_LARGE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_context({"transport": "grok_mcp"})
        self.assertEqual(caught.exception.problem, record.PROBLEM_PROVENANCE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.context_from_dict(dict(context().as_dict(), extra=1))
        self.assertEqual(caught.exception.problem, record.PROBLEM_UNKNOWN_KEY)

    def test_provenance_states_only_what_is_known(self):
        block = record.provenance_record(context(), NOW, 2, "7")
        self.assertEqual(tuple(block), record.PROVENANCE_KEYS)
        self.assertIsNone(block["human_identity_proof"])
        self.assertEqual(block["proof"], "transport_credential_only")
        self.assertEqual(block["observed_revision"], 2)
        self.assertEqual(block["observation_cursor"], "7")
        record.validate_provenance(block)
        unobserved = record.provenance_record(context(), NOW, None, None)
        record.validate_provenance(unobserved)
        self.assertEqual(record.provenance_context(block), context())

    def test_provenance_refusals(self):
        block = record.provenance_record(context(), NOW, 2, "7")
        for key, value, problem in (
            ("human_identity_proof", "passport", record.PROBLEM_PROVENANCE),
            ("proof", "verified_human", record.PROBLEM_PROVENANCE),
            ("observed_revision", 0, record.PROBLEM_BAD_VALUE),
            ("observed_revision", True, record.PROBLEM_BAD_TYPE),
            ("observation_cursor", "07", record.PROBLEM_BAD_VALUE),
            ("received_at", -1, record.PROBLEM_BAD_VALUE),
        ):
            broken = dict(block, **{key: value})
            with self.assertRaises(record.CoordinationError) as caught:
                record.validate_provenance(broken)
            self.assertEqual(caught.exception.problem, problem, key)
        # An observed revision without a cursor (or the reverse) is refused:
        # the pair is recorded together or not at all.
        with self.assertRaises(record.CoordinationError) as caught:
            record.validate_provenance(dict(block, observation_cursor=None))
        self.assertEqual(caught.exception.problem, record.PROBLEM_PROVENANCE)


class ValidatorTests(unittest.TestCase):

    def test_authority_none_is_the_only_accepted_value(self):
        self.assertEqual(record.require_authority("none", "r"), "none")
        for claim in ("delivery", "execution", "", None, True, "NONE"):
            with self.assertRaises(record.CoordinationError) as caught:
                record.require_authority(claim, "r")
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_AUTHORITY_CLAIM)

    def test_state_cursor_is_canonical_decimal(self):
        self.assertEqual(record.require_cursor("0", "c"), "0")
        self.assertEqual(record.require_cursor("12345", "c"), "12345")
        self.assertEqual(record.cursor_value("12345"), 12345)
        for bad in ("", "07", "1.0", "-1", "x", 7, None,
                    "1" * (record.MAX_STATE_CURSOR_CHARS + 1)):
            with self.assertRaises(record.CoordinationError):
                record.require_cursor(bad, "c")

    def test_repository_url_must_already_be_canonical(self):
        self.assertEqual(
            record.require_repository_url("https://github.com/Example/Repo", "u"),
            "https://github.com/Example/Repo")
        self.assertIsNone(record.require_optional_repository_url(None, "u"))
        for bad in ("https://github.com/Example/Repo.git",
                    "github.com/Example/Repo", "https://example.com/a/b", 3):
            with self.assertRaises(record.CoordinationError) as caught:
                record.require_repository_url(bad, "u")
            self.assertIn(caught.exception.problem, (
                record.PROBLEM_REPOSITORY_IDENTITY, record.PROBLEM_BAD_TYPE))

    def test_sorted_id_lists(self):
        self.assertEqual(
            record.require_sorted_ids([EVIDENCE_1, EVIDENCE_2], "mv", "refs", 4),
            [EVIDENCE_1, EVIDENCE_2])
        for bad in ([EVIDENCE_2, EVIDENCE_1], [EVIDENCE_1, EVIDENCE_1],
                    [ARTIFACT_1], "mv-x", [EVIDENCE_1, EVIDENCE_2, "mv-" + "9" * 32,
                                          "mv-" + "8" * 32]):
            with self.assertRaises(record.CoordinationError):
                record.require_sorted_ids(bad, "mv", "refs", 3)

    def test_generic_validators_refuse_never_repair(self):
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_int(True, "n")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_TYPE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_str("x" * 5, "s", 4)
        self.assertEqual(caught.exception.problem, record.PROBLEM_TOO_LARGE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_key("Bad Key", "k")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_closed_keys({"a": 1, "b": 2}, ("a",), "d")
        self.assertEqual(caught.exception.problem, record.PROBLEM_UNKNOWN_KEY)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_closed_keys({}, ("a",), "d")
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSING_KEY)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_member("x", ("a", "b"), "m")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        with self.assertRaises(record.CoordinationError) as caught:
            record.require_hex("A" * 64, "h", 64)
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)


# =====================================================================
# B. observation
# =====================================================================


class ObservationShapeTests(unittest.TestCase):

    def test_protocol_is_one_abstract_read_method(self):
        with self.assertRaises(TypeError):
            observation.MissionObservationSource()
        abstract = observation.MissionObservationSource.__abstractmethods__
        self.assertEqual(set(abstract), {"observe"})
        public = [name for name in vars(observation.MissionObservationSource)
                  if not name.startswith("_")]
        self.assertEqual(public, ["observe"])

    def test_valid_observation_round_trips_as_a_closed_dict(self):
        value = observed().validate()
        as_dict = value.as_dict()
        self.assertEqual(tuple(as_dict), observation.OBSERVATION_KEYS)
        self.assertEqual(observation.observation_from_dict(as_dict), value)
        self.assertEqual(tuple(as_dict["conditions"][0]),
                         observation.CONDITION_KEYS)

    def test_condition_digest_is_content_only_and_deterministic(self):
        first = observation.condition_digest(condition())
        self.assertEqual(first, observation.condition_digest(condition()))
        self.assertNotEqual(first, observation.condition_digest(
            condition(detail="reviewer rejected three times")))
        self.assertNotEqual(first, observation.condition_digest(
            condition(revision=3)))
        self.assertEqual(len(first), 64)
        set_digest = observation.condition_set_digest(observed())
        self.assertEqual(set_digest, observation.condition_set_digest(observed()))
        self.assertNotEqual(set_digest, observation.condition_set_digest(
            observed(conditions=())))

    def test_observation_refusals(self):
        cases = (
            (dict(mission_id="mq-" + "1" * 32), record.PROBLEM_ID_GRAMMAR),
            (dict(current_revision=0), record.PROBLEM_BAD_VALUE),
            (dict(current_revision=True), record.PROBLEM_BAD_TYPE),
            (dict(proposal_digest_sha256="zz"), record.PROBLEM_BAD_VALUE),
            (dict(lifecycle_state="RUNNING_FAST"), record.PROBLEM_BAD_VALUE),
            (dict(state_cursor="07"), record.PROBLEM_BAD_VALUE),
            (dict(repository_url="github.com/x/y"),
             record.PROBLEM_REPOSITORY_IDENTITY),
            (dict(evidence_refs=(recorded_evidence_ref(), evidence_ref())),
             record.PROBLEM_BAD_VALUE),
            (dict(evidence_refs=(evidence_ref(), evidence_ref())),
             record.PROBLEM_BAD_VALUE),
            (dict(artifact_refs=(EVIDENCE_1,)), record.PROBLEM_BAD_TYPE),
            (dict(artifact_refs=(evidence_ref(),)), record.PROBLEM_BAD_TYPE),
            (dict(evidence_refs=[evidence_ref()]), record.PROBLEM_BAD_TYPE),
            (dict(observed_at=-1), record.PROBLEM_BAD_VALUE),
            (dict(source=""), record.PROBLEM_BAD_VALUE),
            (dict(conditions=[condition()]), record.PROBLEM_BAD_TYPE),
            (dict(conditions=tuple(condition(key="k%d" % i)
                                   for i in range(
                                       observation.MAX_OBSERVED_CONDITIONS + 1))),
             record.PROBLEM_TOO_LARGE),
            (dict(evidence_refs=tuple(
                recorded_evidence_ref(evidence_id="mv-%032x" % i)
                for i in range(observation.MAX_OBSERVED_REFERENCES + 1))),
             record.PROBLEM_TOO_LARGE),
        )
        for overrides, problem in cases:
            with self.assertRaises(record.CoordinationError) as caught:
                observed(**overrides).validate()
            self.assertEqual(caught.exception.problem, problem, overrides)

    def test_condition_refusals(self):
        for overrides, problem in (
            (dict(kind="REVIEW_FAILED"), record.PROBLEM_BAD_VALUE),
            (dict(key="Bad Key"), record.PROBLEM_BAD_VALUE),
            (dict(revision=0), record.PROBLEM_BAD_VALUE),
            (dict(detail=""), record.PROBLEM_BAD_VALUE),
            (dict(detail="d" * (record.MAX_CONDITION_DETAIL_CHARS + 1)),
             record.PROBLEM_TOO_LARGE),
            (dict(evidence_refs=(ARTIFACT_1,)), record.PROBLEM_BAD_TYPE),
            (dict(evidence_refs=(artifact_ref(),)), record.PROBLEM_BAD_TYPE),
            (dict(evidence_refs=[evidence_ref()]), record.PROBLEM_BAD_TYPE),
        ):
            with self.assertRaises(record.CoordinationError) as caught:
                condition(**overrides).validate()
            self.assertEqual(caught.exception.problem, problem, overrides)

    def test_condition_references_must_be_mission_local(self):
        foreign = evidence_ref(evidence_id="mv-" + "f" * 32)
        with self.assertRaises(record.CoordinationError) as caught:
            observed(conditions=(condition(evidence_refs=(foreign,)),)).validate()
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_INCONSISTENT)

    def test_condition_reference_provenance_must_match_the_observation(self):
        # AMD-3: a condition may not carry EVIDENCE_1 with different
        # validity facts than the observation's own record of EVIDENCE_1.
        equivocal = condition(evidence_refs=(
            evidence_ref(accepted=False, acceptance_digest_sha256=None,
                         accepted_at=None),))
        with self.assertRaises(record.CoordinationError) as caught:
            observed(conditions=(equivocal,)).validate()
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_INCONSISTENT)
        self.assertIn("provenance", str(caught.exception))


class ReferenceRecordTests(unittest.TestCase):
    """AMD-3: references carry provenance, and presence never upgrades
    status."""

    def test_evidence_vocabularies_match_mission_core(self):
        self.assertEqual(record.EVIDENCE_KINDS, mission_record.EVIDENCE_KINDS)
        self.assertEqual(record.SATISFYING_EVIDENCE_KINDS,
                         mission_record.SATISFYING_EVIDENCE_KINDS)
        self.assertEqual(record.ARTIFACT_ROLES, mission_record.ARTIFACT_ROLES)

    def test_evidence_reference_round_trips_as_a_closed_dict(self):
        value = evidence_ref().validate()
        as_dict = value.as_dict()
        self.assertEqual(tuple(as_dict), observation.EVIDENCE_REFERENCE_KEYS)
        self.assertEqual(observation.evidence_reference_from_dict(as_dict), value)
        unaccepted = recorded_evidence_ref().validate()
        self.assertEqual(
            observation.evidence_reference_from_dict(unaccepted.as_dict()),
            unaccepted)

    def test_evidence_reference_refusals(self):
        for overrides, problem in (
            (dict(evidence_id=ARTIFACT_1), record.PROBLEM_ID_GRAMMAR),
            (dict(kind="HEARSAY"), record.PROBLEM_BAD_VALUE),
            (dict(accepted="yes"), record.PROBLEM_BAD_TYPE),
            # Accepted without an acceptance digest or time, or either
            # without acceptance: the provenance is recorded whole or not
            # at all.
            (dict(acceptance_digest_sha256=None), record.PROBLEM_PROVENANCE),
            (dict(accepted_at=None), record.PROBLEM_PROVENANCE),
            (dict(accepted=False), record.PROBLEM_PROVENANCE),
            (dict(acceptance_digest_sha256="zz"), record.PROBLEM_BAD_VALUE),
            (dict(accepted_at=-1), record.PROBLEM_BAD_VALUE),
            (dict(accepted_at=True), record.PROBLEM_BAD_TYPE),
            # The shape cannot express an accepted narrative claim.
            (dict(kind=record.EVIDENCE_KIND_NARRATIVE_CLAIM),
             record.PROBLEM_OBSERVATION_INCONSISTENT),
            (dict(kind=record.EVIDENCE_KIND_PROCESS_EXIT),
             record.PROBLEM_OBSERVATION_INCONSISTENT),
        ):
            with self.assertRaises(record.CoordinationError) as caught:
                evidence_ref(**overrides).validate()
            self.assertEqual(caught.exception.problem, problem, overrides)

    def test_artifact_reference_round_trips_and_refuses(self):
        value = artifact_ref().validate()
        as_dict = value.as_dict()
        self.assertEqual(tuple(as_dict), observation.ARTIFACT_REFERENCE_KEYS)
        self.assertEqual(tuple(as_dict["receipt"]), observation.ARTIFACT_RECEIPT_KEYS)
        self.assertEqual(observation.artifact_reference_from_dict(as_dict), value)
        bare = artifact_ref(receipt=None).validate()
        self.assertEqual(observation.artifact_reference_from_dict(bare.as_dict()),
                         bare)
        for overrides, problem in (
            (dict(artifact_id=EVIDENCE_1), record.PROBLEM_ID_GRAMMAR),
            (dict(role="SIDE_EFFECT"), record.PROBLEM_BAD_VALUE),
            (dict(receipt={}), record.PROBLEM_MISSING_KEY),
            (dict(receipt=receipt(extra=1)), record.PROBLEM_UNKNOWN_KEY),
            (dict(receipt=receipt(available="yes")), record.PROBLEM_BAD_TYPE),
            (dict(receipt=receipt(content_digest_sha256=None)),
             record.PROBLEM_BAD_TYPE),
            (dict(receipt=receipt(validated_at=-1)), record.PROBLEM_BAD_VALUE),
            (dict(receipt="validated"), record.PROBLEM_NOT_AN_OBJECT),
        ):
            with self.assertRaises(record.CoordinationError) as caught:
                artifact_ref(**overrides).validate()
            self.assertEqual(caught.exception.problem, problem, overrides)

    def test_loader_refuses_a_non_mapping_receipt_outright(self):
        # Finding 1: a JSON list of pairs is NOT repaired into a mapping.
        pairs = [["available", True], ["content_digest_sha256", HEX_A],
                 ["validated_at", NOW - 10]]
        raw = dict(artifact_ref().as_dict(), receipt=pairs)
        with self.assertRaises(record.CoordinationError) as caught:
            observation.artifact_reference_from_dict(raw)
        self.assertEqual(caught.exception.problem, record.PROBLEM_NOT_AN_OBJECT)
        whole = observed().as_dict()
        whole["artifact_refs"][0]["receipt"] = pairs
        with self.assertRaises(record.CoordinationError) as caught:
            observation.observation_from_dict(whole)
        self.assertEqual(caught.exception.problem, record.PROBLEM_NOT_AN_OBJECT)
        for bad in ("validated", 7, [], ()):
            with self.assertRaises(record.CoordinationError) as caught:
                observation.artifact_reference_from_dict(
                    dict(artifact_ref().as_dict(), receipt=bad))
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_NOT_AN_OBJECT, bad)

    def test_receipt_validation_time_never_follows_the_observation(self):
        # Finding 2: validated_at <= observed_at, boundary inclusive.
        at_boundary = observed(
            artifact_refs=(artifact_ref(receipt=receipt(validated_at=NOW - 5)),),
            observed_at=NOW - 5)
        at_boundary.validate()
        future = observed(
            artifact_refs=(artifact_ref(receipt=receipt(validated_at=NOW - 4)),),
            observed_at=NOW - 5)
        with self.assertRaises(record.CoordinationError) as caught:
            future.validate()
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_INCONSISTENT)
        self.assertIn("validated_at", str(caught.exception))
        result = observation.classify(
            observation.ObservationOutcome.observed(future), NOW, MISSION_X)
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIsNone(result.observation)

    def test_acceptance_time_never_follows_the_observation(self):
        # Finding 2 applied to evidence acceptance provenance.
        boundary = evidence_ref(accepted_at=NOW - 5)
        observed(evidence_refs=(boundary, recorded_evidence_ref()),
                 conditions=(condition(evidence_refs=(boundary,)),),
                 observed_at=NOW - 5).validate()
        late = evidence_ref(accepted_at=NOW - 4)
        with self.assertRaises(record.CoordinationError) as caught:
            observed(evidence_refs=(late, recorded_evidence_ref()),
                     conditions=(condition(evidence_refs=(late,)),),
                     observed_at=NOW - 5).validate()
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_INCONSISTENT)
        self.assertIn("accepted_at", str(caught.exception))

    def test_validated_requires_present_available_and_non_future_receipt(self):
        validated = record.REFERENCE_LEVEL_VALIDATED
        recorded = record.REFERENCE_LEVEL_RECORDED
        self.assertTrue(artifact_ref().proves(validated, observed_at=NOW - 10))
        self.assertTrue(artifact_ref().proves(validated, observed_at=NOW))
        self.assertFalse(artifact_ref().proves(validated, observed_at=NOW - 11))
        self.assertFalse(artifact_ref(receipt=None).proves(validated, NOW))
        self.assertFalse(
            artifact_ref(receipt=receipt(available=False)).proves(validated, NOW))
        self.assertTrue(
            artifact_ref(receipt=receipt(available=False)).proves(recorded, NOW))
        # Through the one citation check: an unavailable receipt is
        # RECORDED, never VALIDATED.
        unavailable = observed(
            artifact_refs=(artifact_ref(receipt=receipt(available=False)),),
            conditions=())
        observation.require_reference_level(unavailable, ARTIFACT_1, recorded, "r")
        with self.assertRaises(record.CoordinationError) as caught:
            observation.require_reference_level(unavailable, ARTIFACT_1,
                                                validated, "r")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_REFERENCE_NOT_PROVEN)
        self.assertIn("available", str(caught.exception))

    def test_accepted_requires_acceptance_no_later_than_the_observation(self):
        accepted = record.REFERENCE_LEVEL_ACCEPTED
        self.assertTrue(evidence_ref().proves(accepted, observed_at=NOW - 20))
        self.assertFalse(evidence_ref().proves(accepted, observed_at=NOW - 21))
        self.assertFalse(recorded_evidence_ref().proves(accepted, NOW))
        self.assertTrue(recorded_evidence_ref().proves(
            record.REFERENCE_LEVEL_RECORDED, NOW))

    def test_reference_level_is_exactly_what_the_observation_proves(self):
        value = observed()
        require = observation.require_reference_level
        # Presence proves RECORDED (Mission-locality) and nothing more.
        self.assertEqual(require(value, EVIDENCE_1, record.REFERENCE_LEVEL_RECORDED,
                                 "ref").evidence_id, EVIDENCE_1)
        self.assertEqual(require(value, EVIDENCE_1, record.REFERENCE_LEVEL_ACCEPTED,
                                 "ref").acceptance_digest_sha256, HEX_B)
        require(value, EVIDENCE_2, record.REFERENCE_LEVEL_RECORDED, "ref")
        with self.assertRaises(record.CoordinationError) as caught:
            require(value, EVIDENCE_2, record.REFERENCE_LEVEL_ACCEPTED, "ref")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_REFERENCE_NOT_PROVEN)
        self.assertEqual(record.PROBLEM_REFERENCE_NOT_PROVEN,
                         "coordination_reference_not_proven")
        require(value, ARTIFACT_1, record.REFERENCE_LEVEL_RECORDED, "ref")
        require(value, ARTIFACT_1, record.REFERENCE_LEVEL_VALIDATED, "ref")
        bare = observed(artifact_refs=(artifact_ref(receipt=None),),
                        conditions=())
        with self.assertRaises(record.CoordinationError) as caught:
            require(bare, ARTIFACT_1, record.REFERENCE_LEVEL_VALIDATED, "ref")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_REFERENCE_NOT_PROVEN)
        # Not in the observation at all: not Mission-local.
        with self.assertRaises(record.CoordinationError) as caught:
            require(value, "mv-" + "f" * 32, record.REFERENCE_LEVEL_RECORDED, "ref")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_REFERENCE_NOT_MISSION_LOCAL)
        # A level that does not apply to the reference kind is refused.
        with self.assertRaises(record.CoordinationError) as caught:
            require(value, EVIDENCE_1, record.REFERENCE_LEVEL_VALIDATED, "ref")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        with self.assertRaises(record.CoordinationError) as caught:
            require(value, ARTIFACT_1, record.REFERENCE_LEVEL_ACCEPTED, "ref")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        with self.assertRaises(record.CoordinationError) as caught:
            require(value, "mn-" + "1" * 32, record.REFERENCE_LEVEL_RECORDED, "ref")
        self.assertEqual(caught.exception.problem, record.PROBLEM_ID_GRAMMAR)

    def test_reference_set_digest_covers_identity_and_provenance(self):
        base = observation.reference_set_digest(observed())
        self.assertEqual(base, observation.reference_set_digest(observed()))
        self.assertEqual(len(base), 64)
        # Same ids, different per-reference provenance: a different set.
        changed = evidence_ref(acceptance_digest_sha256=HEX_A)
        self.assertNotEqual(base, observation.reference_set_digest(observed(
            evidence_refs=(changed, recorded_evidence_ref()),
            conditions=(condition(evidence_refs=(changed,)),))))
        self.assertNotEqual(base, observation.reference_set_digest(observed(
            artifact_refs=(artifact_ref(receipt=None),))))
        # A different id set: a different set.
        self.assertNotEqual(base, observation.reference_set_digest(observed(
            evidence_refs=(evidence_ref(),))))

    def test_observation_point_is_the_durable_record_of_a_read(self):
        point = observation.observation_point(observed())
        self.assertEqual(tuple(point), observation.OBSERVATION_POINT_KEYS)
        self.assertEqual(point["revision"], 2)
        self.assertEqual(point["cursor"], "7")
        self.assertEqual(point["proposal_digest_sha256"], HEX_A)
        self.assertEqual(point["authorization_digest_sha256"], HEX_B)
        self.assertEqual(point["lifecycle_state"], record.LIFECYCLE_AUTHORIZED)
        self.assertEqual(point["condition_set_digest_sha256"],
                         observation.condition_set_digest(observed()))
        self.assertEqual(point["reference_set_digest_sha256"],
                         observation.reference_set_digest(observed()))
        observation.validate_observation_point(point)
        for key, value in (("cursor", "07"), ("revision", 0),
                           ("lifecycle_state", "GONE"),
                           ("reference_set_digest_sha256", None),
                           ("authorization_digest_sha256", "zz")):
            with self.assertRaises(record.CoordinationError):
                observation.validate_observation_point(dict(point, **{key: value}))
        with self.assertRaises(record.CoordinationError):
            observation.validate_observation_point(dict(point, extra=1))

    def test_f1_condition_revision_must_equal_the_observed_revision(self):
        # A condition behind or ahead of the Mission's current revision is
        # the source contradicting itself, never a current presentation.
        for stale_revision in (1, 3):
            with self.assertRaises(record.CoordinationError) as caught:
                observed(conditions=(condition(revision=stale_revision),)).validate()
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_OBSERVATION_INCONSISTENT)
            self.assertIn("revision", str(caught.exception))
            result = observation.classify(observation.ObservationOutcome.observed(
                observed(conditions=(condition(revision=stale_revision),))),
                NOW, MISSION_X)
            self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
            self.assertIsNone(result.observation)

    def test_duplicate_condition_identity_is_refused(self):
        with self.assertRaises(record.CoordinationError) as caught:
            observed(conditions=(condition(), condition(detail="other"))).validate()
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_INCONSISTENT)


class FreshnessTests(unittest.TestCase):

    def classify(self, outcome, now=NOW, floor=None, mission_id=MISSION_X):
        return observation.classify(outcome, now, mission_id, floor)

    def test_fresh(self):
        result = self.classify(observation.ObservationOutcome.observed(observed()))
        self.assertEqual(result.freshness, record.FRESHNESS_FRESH)
        self.assertEqual(result.observation, observed())
        self.assertIsNone(result.problem)

    def test_absent_and_unavailable(self):
        absent = self.classify(observation.ObservationOutcome.absent())
        self.assertEqual(absent.freshness, record.FRESHNESS_ABSENT)
        self.assertIsNone(absent.observation)
        unavailable = self.classify(
            observation.ObservationOutcome.unavailable("registry locked"))
        self.assertEqual(unavailable.freshness, record.FRESHNESS_UNAVAILABLE)
        self.assertEqual(unavailable.problem, "registry locked")
        self.assertIsNone(unavailable.observation)

    def test_outcome_shape_refusals(self):
        with self.assertRaises(record.CoordinationError):
            observation.ObservationOutcome(
                status=record.OBSERVATION_OBSERVED, observation=None,
                problem=None).validate()
        with self.assertRaises(record.CoordinationError):
            observation.ObservationOutcome(
                status=record.OBSERVATION_ABSENT, observation=observed(),
                problem=None).validate()
        with self.assertRaises(record.CoordinationError):
            observation.ObservationOutcome(
                status=record.OBSERVATION_UNAVAILABLE, observation=None,
                problem=None).validate()

    def test_stale_by_age(self):
        def read_at(observed_at):
            # Provenance times must not postdate the read (Finding 2).
            accepted = evidence_ref(accepted_at=observed_at - 1)
            return observed(
                observed_at=observed_at,
                evidence_refs=(accepted, recorded_evidence_ref()),
                artifact_refs=(artifact_ref(
                    receipt=receipt(validated_at=observed_at - 1)),),
                conditions=(condition(evidence_refs=(accepted,)),))

        old = read_at(NOW - observation.MAX_OBSERVATION_AGE_SECONDS - 1)
        result = self.classify(observation.ObservationOutcome.observed(old))
        self.assertEqual(result.freshness, record.FRESHNESS_STALE)
        self.assertIsNotNone(result.observation)
        edge = read_at(NOW - observation.MAX_OBSERVATION_AGE_SECONDS)
        self.assertEqual(
            self.classify(observation.ObservationOutcome.observed(edge)).freshness,
            record.FRESHNESS_FRESH)

    def test_future_dated_is_inconsistent(self):
        future = observed(observed_at=NOW + 1)
        result = self.classify(observation.ObservationOutcome.observed(future))
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)

    def test_observation_for_another_mission_is_inconsistent(self):
        result = self.classify(observation.ObservationOutcome.observed(observed()),
                               mission_id=MISSION_Y)
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIn(MISSION_Y, result.problem)

    def test_malformed_observation_is_unavailable_never_trusted(self):
        bad = observed(current_revision=0)
        result = self.classify(observation.ObservationOutcome.observed(bad))
        self.assertEqual(result.freshness, record.FRESHNESS_UNAVAILABLE)
        self.assertIsNone(result.observation)
        self.assertIn(record.PROBLEM_BAD_VALUE, result.problem)

    def test_locality_violation_is_inconsistent(self):
        bad = observed(conditions=(condition(
            evidence_refs=(evidence_ref(evidence_id="mv-" + "f" * 32),)),))
        result = self.classify(observation.ObservationOutcome.observed(bad))
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIsNone(result.observation)

    def test_high_water_mark_regression_is_stale(self):
        floor = observation.HighWaterMark(
            revision=2, cursor="9",
            point=dict(observation.observation_point(observed()), cursor="9"))
        behind = observed(state_cursor="8")
        self.assertEqual(
            self.classify(observation.ObservationOutcome.observed(behind),
                          floor=floor).freshness,
            record.FRESHNESS_STALE)
        older_revision = observed(current_revision=1, state_cursor="99",
                                  conditions=(condition(revision=1),))
        self.assertEqual(
            self.classify(observation.ObservationOutcome.observed(older_revision),
                          floor=floor).freshness,
            record.FRESHNESS_STALE)
        ahead = observed(state_cursor="10")
        self.assertEqual(
            self.classify(observation.ObservationOutcome.observed(ahead),
                          floor=floor).freshness,
            record.FRESHNESS_FRESH)
        # Numeric, not lexical: "10" is ahead of "9".
        self.assertGreater(record.cursor_value("10"), record.cursor_value("9"))

    def same_point_result(self, **overrides):
        same = observed(state_cursor="7")
        floor = observation.HighWaterMark(
            revision=2, cursor="7", point=observation.observation_point(same))
        return self.classify(
            observation.ObservationOutcome.observed(observed(state_cursor="7",
                                                             **overrides)),
            floor=floor)

    def test_same_point_same_content_is_fresh(self):
        self.assertEqual(self.same_point_result().freshness,
                         record.FRESHNESS_FRESH)

    def test_same_point_different_proposal_or_conditions_is_inconsistent(self):
        self.assertEqual(
            self.same_point_result(proposal_digest_sha256=HEX_B).freshness,
            record.FRESHNESS_INCONSISTENT)
        self.assertEqual(self.same_point_result(conditions=()).freshness,
                         record.FRESHNESS_INCONSISTENT)

    def test_same_point_changed_authorization_is_inconsistent(self):
        # AMD-3: null <-> non-null in either direction, and a changed digest.
        self.assertEqual(
            self.same_point_result(authorization_digest_sha256=None).freshness,
            record.FRESHNESS_INCONSISTENT)
        self.assertEqual(
            self.same_point_result(authorization_digest_sha256=HEX_A).freshness,
            record.FRESHNESS_INCONSISTENT)
        without = observed(state_cursor="7", authorization_digest_sha256=None)
        floor = observation.HighWaterMark(
            revision=2, cursor="7", point=observation.observation_point(without))
        gained = observed(state_cursor="7", authorization_digest_sha256=HEX_B)
        result = self.classify(observation.ObservationOutcome.observed(gained),
                               floor=floor)
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIn("authorization", result.problem)

    def test_same_point_changed_lifecycle_is_inconsistent(self):
        result = self.same_point_result(lifecycle_state=record.LIFECYCLE_DENIED)
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIn("lifecycle", result.problem)

    def test_same_point_changed_reference_set_is_inconsistent(self):
        result = self.same_point_result(evidence_refs=(evidence_ref(),))
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIn("reference", result.problem)

    def test_same_point_changed_reference_provenance_is_inconsistent(self):
        # Same ids, one artifact lost its validated receipt.
        result = self.same_point_result(artifact_refs=(artifact_ref(receipt=None),))
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIn("provenance", result.problem)
        self.assertIsNotNone(result.observation)
        # Same ids, an evidence record changed its acceptance digest (the
        # condition cites the same changed record, so the observation is
        # self-consistent and only the same-point rule can fire).
        changed = evidence_ref(acceptance_digest_sha256=HEX_A)
        result = self.same_point_result(
            evidence_refs=(changed, recorded_evidence_ref()),
            conditions=(condition(evidence_refs=(changed,)),))
        self.assertEqual(result.freshness, record.FRESHNESS_INCONSISTENT)
        self.assertIsNotNone(result.observation)

    def test_floor_without_a_point_only_orders(self):
        floor = observation.HighWaterMark(revision=2, cursor="7", point=None)
        self.assertEqual(
            self.classify(observation.ObservationOutcome.observed(
                observed(proposal_digest_sha256=HEX_B,
                         authorization_digest_sha256=None,
                         lifecycle_state=record.LIFECYCLE_DENIED)),
                floor=floor).freshness,
            record.FRESHNESS_FRESH)

    def test_floor_point_must_agree_with_its_own_pair(self):
        point = observation.observation_point(observed())
        with self.assertRaises(record.CoordinationError):
            observation.HighWaterMark(revision=3, cursor="7", point=point).validate()
        with self.assertRaises(record.CoordinationError):
            observation.HighWaterMark(revision=2, cursor="8", point=point).validate()


class SourceBoundaryTests(unittest.TestCase):

    def test_observe_safely_returns_outcome_from_a_well_behaved_source(self):
        source = FakeSource({MISSION_X: observation.ObservationOutcome.observed(
            observed())})
        outcome = observation.observe_safely(source, MISSION_X)
        self.assertEqual(outcome.status, record.OBSERVATION_OBSERVED)
        self.assertEqual(source.calls, [MISSION_X])

    def test_raising_source_is_unavailable_with_class_name_only(self):
        outcome = observation.observe_safely(RaisingSource(), MISSION_X)
        self.assertEqual(outcome.status, record.OBSERVATION_UNAVAILABLE)
        self.assertIn("RuntimeError", outcome.problem)
        self.assertNotIn("secret", outcome.problem)

    def test_wrong_type_from_source_is_unavailable(self):
        outcome = observation.observe_safely(WrongTypeSource(), MISSION_X)
        self.assertEqual(outcome.status, record.OBSERVATION_UNAVAILABLE)
        self.assertIn("dict", outcome.problem)

    def test_source_must_be_the_protocol(self):
        with self.assertRaises(record.CoordinationError) as caught:
            observation.observe_safely(object(), MISSION_X)
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_TYPE)

    def test_mission_id_is_validated_before_the_source_is_asked(self):
        source = FakeSource({})
        with self.assertRaises(record.CoordinationError):
            observation.observe_safely(source, "mn-short")
        self.assertEqual(source.calls, [])


# =====================================================================
# C. store
# =====================================================================


class StoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "protected")
        self.store = store.CoordinationStore(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def write_raw(self, text):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        with open(self.store.path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.store.path, 0o600)

    def test_missing_file_yields_fresh_default(self):
        document = self.store.load()
        self.assertEqual(document, store.default_document())
        self.assertEqual(tuple(sorted(document)), tuple(sorted(store.TOP_LEVEL_KEYS)))
        self.assertEqual(document["coordination_store_schema_version"], 1)
        self.assertEqual(document["store_sequence"], 0)
        self.assertFalse(os.path.exists(self.store.path))

    def test_save_increments_sequence_and_reload_agrees(self):
        document = self.store.load()
        self.store.save(document, expected_sequence=0)
        self.assertEqual(document["store_sequence"], 1)
        self.assertEqual(self.store.load()["store_sequence"], 1)
        self.store.save(self.store.load(), expected_sequence=1)
        self.assertEqual(self.store.load()["store_sequence"], 2)
        mode = stat.S_IMODE(os.stat(self.store.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)

    def test_conflicting_write_fails_closed(self):
        first = self.store.load()
        second = self.store.load()
        self.store.save(first, expected_sequence=0)
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.save(second, expected_sequence=0)
        self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_CONFLICT)
        self.assertEqual(self.store.load()["store_sequence"], 1)
        # The in-memory document the caller holds must state the sequence
        # it was loaded at; a document claiming a different sequence than
        # expected_sequence is refused before anything is written.
        stale = self.store.load()
        stale["store_sequence"] = 5
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.save(stale, expected_sequence=1)
        self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_CONFLICT)

    def test_own_lock_file_never_the_mission_or_workflow_lock(self):
        self.assertEqual(store.COORDINATION_LOCK_FILE_NAME, "coordination.lock")
        self.assertEqual(store.COORDINATION_FILE_NAME, "coordination.json")
        with self.store.lock():
            names = set(os.listdir(self.directory))
        self.assertIn("coordination.lock", names)
        self.assertNotIn("missions.lock", names)
        self.assertNotIn("workflows.lock", names)

    def test_bad_json_is_unreadable_and_never_reinitialized(self):
        self.write_raw("{not json")
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.load()
        self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_UNREADABLE)
        self.assertIn("move the file aside", str(caught.exception))
        with open(self.store.path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{not json")

    def test_top_level_shape_refusals(self):
        good = store.default_document()
        cases = (
            (dict(good, extra={}), "unknown"),
            ({k: v for k, v in good.items() if k != "handoffs"}, "missing"),
            (dict(good, coordination_store_schema_version=2), "version"),
            (dict(good, coordination_store_schema_version=True), "version"),
            (dict(good, coordination_store_schema_version=1.0), "version"),
            (dict(good, store_sequence=-1), "sequence"),
            (dict(good, store_sequence=True), "sequence"),
            (dict(good, bindings=[]), "JSON object"),
            ([], "object"),
        )
        for document, fragment in cases:
            self.write_raw(json.dumps(document))
            with self.assertRaises(store.CoordinationStoreError) as caught:
                self.store.load()
            self.assertEqual(caught.exception.problem,
                             store.PROBLEM_STORE_UNREADABLE)
            self.assertIn(fragment, str(caught.exception).lower()
                          if fragment.islower() else str(caught.exception))

    def test_family_keys_must_fit_their_id_grammar(self):
        for family, prefix in store.FAMILY_PREFIXES.items():
            document = store.default_document()
            document[family]["not-an-id"] = {}
            self.write_raw(json.dumps(document))
            with self.assertRaises(store.CoordinationStoreError) as caught:
                self.store.load()
            self.assertEqual(caught.exception.problem,
                             store.PROBLEM_STORE_UNREADABLE)
            self.assertIn(prefix, str(caught.exception))

    def test_group_or_other_accessible_file_is_refused(self):
        document = self.store.load()
        self.store.save(document, expected_sequence=0)
        os.chmod(self.store.path, 0o640)
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.load()
        self.assertIn("chmod 600", str(caught.exception))
        os.chmod(self.store.path, 0o600)
        self.store.load()

    def test_open_directory_is_refused_before_read_lock_or_write(self):
        os.makedirs(self.directory, mode=0o700)
        os.chmod(self.directory, 0o750)
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.load()
        self.assertIn("chmod 700", str(caught.exception))
        with self.assertRaises(store.CoordinationStoreError):
            with self.store.lock():
                pass
        with self.assertRaises(store.CoordinationStoreError):
            self.store.save(store.default_document(), expected_sequence=0)
        self.assertEqual(os.listdir(self.directory), [])
        os.chmod(self.directory, 0o700)
        self.store.load()

    def test_a_file_where_the_directory_should_be_is_refused(self):
        os.makedirs(self.tmp.name, exist_ok=True)
        with open(self.directory, "w", encoding="utf-8") as handle:
            handle.write("")
        with self.assertRaises(store.CoordinationStoreError) as caught:
            self.store.load()
        self.assertIn("not a directory", str(caught.exception))

    def test_caps_refuse_at_the_bound(self):
        expectations = {
            "bindings": store.MAX_BINDING_RECORDS,
            "route_decisions": store.MAX_ROUTE_DECISION_RECORDS,
            "attention": store.MAX_ATTENTION_RECORDS,
            "handoffs": store.MAX_HANDOFF_RECORDS,
            "participants": store.MAX_PARTICIPANT_ROSTERS,
        }
        self.assertEqual(set(expectations), set(store.FAMILY_PREFIXES))
        for family, cap in expectations.items():
            with self.assertRaises(store.CoordinationStoreError) as caught:
                store.require_capacity(family, cap + 1, "<document>")
            self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_FULL)
            store.require_capacity(family, cap, "<document>")
        self.assertEqual(store.MAX_BINDING_RECORDS, 4096)
        self.assertEqual(store.MAX_ROUTE_DECISION_RECORDS, 16384)
        self.assertEqual(store.MAX_ATTENTION_RECORDS, 16384)
        self.assertEqual(store.MAX_HANDOFF_RECORDS, 4096)
        self.assertEqual(store.MAX_PARTICIPANT_ROSTERS, 1024)

    def test_save_validates_before_touching_the_filesystem(self):
        document = self.store.load()
        self.store.save(document, expected_sequence=0)
        with open(self.store.path, "rb") as handle:
            before = handle.read()
        broken = self.store.load()
        broken["surprise"] = {}
        with self.assertRaises(store.CoordinationStoreError):
            self.store.save(broken, expected_sequence=1)
        with open(self.store.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(
            [n for n in os.listdir(self.directory) if n.startswith(".coordination-")],
            [])

    def test_validate_document_reports_the_path(self):
        with self.assertRaises(store.CoordinationStoreError) as caught:
            store.validate_document({"nope": 1}, "/x/coordination.json")
        self.assertIn("/x/coordination.json", str(caught.exception))

    def test_bindings_and_route_decisions_round_trip_and_fail_closed(self):
        document = self.store.load()
        bound = make_binding(record.BINDING_REPLY_TO_MESSAGE, "msg-1")
        document["bindings"][bound["binding_id"]] = bound
        route = routed(explicit_mission_id=MISSION_X)
        document["route_decisions"][route["route_id"]] = route
        self.store.save(document, expected_sequence=0)
        reloaded = self.store.load()
        self.assertEqual(reloaded["bindings"][bound["binding_id"]], bound)
        self.assertEqual(reloaded["route_decisions"][route["route_id"]], route)
        # A corrupt record of either family refuses the whole document.
        for family, key in (("bindings", bound["binding_id"]),
                            ("route_decisions", route["route_id"])):
            broken = json.loads(json.dumps(reloaded))
            broken[family][key]["authority"] = "delivery"
            self.write_raw(json.dumps(broken))
            with self.assertRaises(store.CoordinationStoreError) as caught:
                self.store.load()
            self.assertIn(record.PROBLEM_AUTHORITY_CLAIM, str(caught.exception))
        # Every family validates its records: an empty object is refused
        # in each of them.
        for family in ("attention", "handoffs", "participants"):
            broken = store.default_document()
            broken[family][
                record.mint_id(store.FAMILY_PREFIXES[family],
                               token_hex=lambda n: "e" * (2 * n))
                if family != "participants" else MISSION_X] = {}
            self.write_raw(json.dumps(broken))
            with self.assertRaises(store.CoordinationStoreError) as caught:
                self.store.load()
            self.assertIn(record.PROBLEM_MISSING_KEY, str(caught.exception))


# =====================================================================
# D. binding
# =====================================================================

CONVERSATION_A = "chat-a"
CONVERSATION_B = "chat-b"
REPO_URL = "https://github.com/Example/Repo"
OTHER_REPO_URL = "https://github.com/Example/Other"


def fresh(mission_id=MISSION_X, **overrides):
    """A FRESH observation of ``mission_id``."""
    return observed(mission_id=mission_id, **overrides)


def make_binding(kind, selector, mission_id=MISSION_X, conversation_ref=None,
                 transport="grok_mcp", bound_at=NOW - 100, expires_at=None,
                 obs=None, binding_id=None, ctx=None):
    if conversation_ref is None and kind in record.CONVERSATION_SCOPED_BINDING_KINDS:
        conversation_ref = CONVERSATION_A
    obs = obs if obs is not None else fresh(mission_id)
    return binding.new_binding(
        binding_id or record.mint_id("cb"), kind, transport, conversation_ref,
        selector, mission_id, obs, bound_at, expires_at, ctx or context())


class BindingRecordTests(unittest.TestCase):

    def test_every_kind_builds_validates_and_round_trips(self):
        cases = {
            record.BINDING_REPLY_TO_MESSAGE: "msg-7",
            record.BINDING_APPROVAL_PRESENTATION: "msg-8",
            record.BINDING_RESULT_PRESENTATION: "msg-9",
            record.BINDING_CONVERSATION: "",
            record.BINDING_REPOSITORY: REPO_URL,
            record.BINDING_ISSUE: REPO_URL + "#42",
            record.BINDING_PROJECT: "example.repo",
            record.BINDING_ALIAS: "the-repo-mission",
        }
        self.assertEqual(set(cases), set(record.BINDING_KINDS))
        for kind, selector in cases.items():
            bound = make_binding(kind, selector)
            self.assertEqual(tuple(bound), binding.BINDING_KEYS)
            self.assertEqual(binding.validate_binding(bound, "b"), bound)
            # AMD-1: every kind records the revision observed at bind time.
            self.assertEqual(bound["bound_revision"], 2)
            self.assertEqual(bound["observation_point"],
                             observation.observation_point(fresh()))
            self.assertEqual(bound["authority"], "none")
            self.assertFalse(bound["revoked"])
            scoped = kind in record.CONVERSATION_SCOPED_BINDING_KINDS
            self.assertEqual(bound["conversation_ref"] is not None, scoped, kind)

    def test_binding_requires_the_observation_of_the_bound_mission(self):
        with self.assertRaises(record.CoordinationError) as caught:
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m", mission_id=MISSION_Y,
                         obs=fresh(MISSION_X))
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_MISMATCH)

    def test_selector_rules_per_kind(self):
        cases = (
            (record.BINDING_CONVERSATION, "not-empty", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_REPLY_TO_MESSAGE, "", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_REPLY_TO_MESSAGE, "m", "conv-missing",
             record.PROBLEM_BAD_TYPE),
            (record.BINDING_REPOSITORY, REPO_URL + ".git", None,
             record.PROBLEM_REPOSITORY_IDENTITY),
            (record.BINDING_REPOSITORY, REPO_URL, CONVERSATION_A,
             record.PROBLEM_BAD_VALUE),
            (record.BINDING_ISSUE, REPO_URL, None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_ISSUE, REPO_URL + "#0", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_ISSUE, REPO_URL + "#07", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_ISSUE, REPO_URL + "#x", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_PROJECT, "Bad Key", None, record.PROBLEM_BAD_VALUE),
            (record.BINDING_ALIAS, "a" * (record.MAX_KEY_CHARS + 1), None,
             record.PROBLEM_TOO_LARGE),
        )
        for kind, selector, conversation_ref, problem in cases:
            with self.assertRaises(record.CoordinationError) as caught:
                if conversation_ref == "conv-missing":
                    bound = make_binding(kind, selector)
                    bound["conversation_ref"] = None
                    binding.validate_binding(bound, "b")
                else:
                    make_binding(kind, selector, conversation_ref=conversation_ref)
            self.assertEqual(caught.exception.problem, problem, (kind, selector))

    def test_expiry_bounds_and_activity(self):
        with self.assertRaises(record.CoordinationError):
            make_binding(record.BINDING_ALIAS, "a", bound_at=NOW, expires_at=NOW)
        with self.assertRaises(record.CoordinationError) as caught:
            make_binding(record.BINDING_ALIAS, "a", bound_at=NOW,
                         expires_at=NOW + binding.MAX_BINDING_VALIDITY_SECONDS + 1)
        self.assertEqual(caught.exception.problem, record.PROBLEM_TOO_LARGE)
        bound = make_binding(record.BINDING_ALIAS, "a", bound_at=NOW,
                             expires_at=NOW + 10)
        self.assertTrue(binding.is_active(bound, NOW + 9))
        self.assertFalse(binding.is_active(bound, NOW + 10))
        self.assertFalse(binding.is_expired(bound, NOW + 9))
        self.assertTrue(binding.is_expired(bound, NOW + 10))
        forever = make_binding(record.BINDING_ALIAS, "b", bound_at=NOW)
        self.assertTrue(binding.is_active(forever, NOW + 10 ** 9))

    def test_revocation_is_explicit_and_final(self):
        bound = make_binding(record.BINDING_ALIAS, "a", bound_at=NOW)
        revoked = binding.revoke(bound, NOW + 5)
        self.assertTrue(revoked["revoked"])
        self.assertEqual(revoked["revoked_at"], NOW + 5)
        self.assertFalse(binding.is_active(revoked, NOW + 6))
        binding.validate_binding(revoked, "b")
        with self.assertRaises(record.CoordinationError) as caught:
            binding.revoke(revoked, NOW + 6)
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)
        with self.assertRaises(record.CoordinationError):
            binding.revoke(bound, NOW - 1)  # never before it was bound
        half = dict(bound, revoked=True)
        with self.assertRaises(record.CoordinationError):
            binding.validate_binding(half, "b")

    def test_record_refusals(self):
        bound = make_binding(record.BINDING_REPLY_TO_MESSAGE, "m")
        for key, value in (("authority", "execution"), ("bound_revision", None),
                           ("bound_revision", 3), ("kind", "MAGIC"),
                           ("mission_id", "mq-" + "1" * 32), ("bound_at", -1),
                           ("bound_by", {"transport": "x"}),
                           ("observation_point", None)):
            with self.assertRaises(record.CoordinationError):
                binding.validate_binding(dict(bound, **{key: value}), "b")
        with self.assertRaises(record.CoordinationError):
            binding.validate_binding(dict(bound, extra=1), "b")


class BindingDocumentTests(unittest.TestCase):

    def document(self, *bindings):
        document = store.default_document()
        for bound in bindings:
            document["bindings"][bound["binding_id"]] = bound
        return document

    def test_active_identity_is_unique_until_revoked(self):
        first = make_binding(record.BINDING_REPLY_TO_MESSAGE, "m")
        second = make_binding(record.BINDING_REPLY_TO_MESSAGE, "m",
                              mission_id=MISSION_Y)
        with self.assertRaises(record.CoordinationError) as caught:
            binding.validate_bindings(self.document(first, second), "<doc>")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BINDING_CONFLICT)
        binding.validate_bindings(
            self.document(binding.revoke(first, NOW), second), "<doc>")
        # Same selector in another conversation, or on another transport,
        # is a different identity.
        binding.validate_bindings(self.document(
            first, make_binding(record.BINDING_REPLY_TO_MESSAGE, "m",
                                mission_id=MISSION_Y,
                                conversation_ref=CONVERSATION_B)), "<doc>")
        binding.validate_bindings(self.document(
            first, make_binding(record.BINDING_REPLY_TO_MESSAGE, "m",
                                mission_id=MISSION_Y, transport="telegram")),
            "<doc>")

    def test_key_must_match_binding_id_and_caps_refuse(self):
        bound = make_binding(record.BINDING_ALIAS, "a")
        document = store.default_document()
        document["bindings"][record.mint_id("cb")] = bound
        with self.assertRaises(record.CoordinationError):
            binding.validate_bindings(document, "<doc>")
        per_mission = [make_binding(record.BINDING_ALIAS, "alias-%d" % i)
                       for i in range(binding.MAX_BINDINGS_PER_MISSION + 1)]
        with self.assertRaises(record.CoordinationError) as caught:
            binding.validate_bindings(self.document(*per_mission), "<doc>")
        self.assertEqual(caught.exception.problem, record.PROBLEM_TOO_LARGE)
        binding.validate_bindings(self.document(*per_mission[:-1]), "<doc>")
        self.assertEqual(binding.MAX_BINDINGS_PER_MISSION, 64)
        self.assertEqual(binding.MAX_BINDINGS_PER_CONVERSATION, 256)
        self.assertEqual(binding.MAX_BINDING_VALIDITY_SECONDS, 315360000)

    def test_lookup_ignores_revoked_and_returns_expired_for_the_caller(self):
        live = make_binding(record.BINDING_REPLY_TO_MESSAGE, "m", bound_at=NOW,
                            expires_at=NOW + 10)
        gone = binding.revoke(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "n"), NOW)
        document = self.document(live, gone)
        found = binding.find(document, record.BINDING_REPLY_TO_MESSAGE, "grok_mcp",
                             CONVERSATION_A, "m")
        self.assertEqual([b["binding_id"] for b in found], [live["binding_id"]])
        self.assertEqual(binding.find(document, record.BINDING_REPLY_TO_MESSAGE,
                                      "grok_mcp", CONVERSATION_A, "n"), [])
        self.assertEqual(binding.find(document, record.BINDING_REPLY_TO_MESSAGE,
                                      "grok_mcp", CONVERSATION_B, "m"), [])


# =====================================================================
# E. routing
# =====================================================================


def inbound(**overrides):
    fields = {
        "transport": "grok_mcp",
        "conversation_ref": CONVERSATION_A,
        "message_ref": "msg-100",
        "explicit_mission_id": None,
        "reply_to_message_ref": None,
        "repository_url": None,
        "issue_ref": None,
        "project_key": None,
        "alias": None,
        "intent": record.INTENT_UNSPECIFIED,
        "domain": None,
        "proposal_digest_sha256": None,
    }
    fields.update(overrides)
    return routing.InboundTurn(**fields)


class Observer(object):
    """Hermetic observation lookup: mission id -> FreshnessResult."""

    def __init__(self, **results):
        self.results = results
        self.calls = []

    def __call__(self, mission_id):
        self.calls.append(mission_id)
        outcome = self.results.get(mission_id)
        if outcome is None:
            outcome = observation.ObservationOutcome.absent()
        return observation.classify(outcome, NOW, mission_id)


def observing(*missions, **overrides):
    return Observer(**dict(
        (mission_id, observation.ObservationOutcome.observed(
            fresh(mission_id, **overrides)))
        for mission_id in missions))


def derive(turn, document=None, observe=None, lanes=(record.LANE_ENGINEERING,)):
    return routing.derive_route(turn, document or store.default_document(),
                                observe or observing(MISSION_X, MISSION_Y), NOW,
                                frozenset(lanes))


def routed(document=None, observe=None, ctx=None, decided_at=NOW, **overrides):
    turn = inbound(**overrides)
    derivation = derive(turn, document, observe)
    return routing.new_route_record(record.mint_id("cr"), turn, derivation,
                                    decided_at, ctx or context())


def with_bindings(*bindings):
    document = store.default_document()
    for bound in bindings:
        document["bindings"][bound["binding_id"]] = bound
    return document


class InboundTurnTests(unittest.TestCase):

    def test_round_trip_and_digest(self):
        turn = inbound(explicit_mission_id=MISSION_X).validate()
        self.assertEqual(tuple(turn.as_dict()), routing.INBOUND_KEYS)
        self.assertEqual(routing.inbound_from_dict(turn.as_dict()), turn)
        self.assertEqual(turn.identity(), ("grok_mcp", CONVERSATION_A, "msg-100"))
        self.assertEqual(turn.digest(), inbound(explicit_mission_id=MISSION_X).digest())
        self.assertNotEqual(turn.digest(), inbound().digest())

    def test_refusals(self):
        for overrides, problem in (
            (dict(explicit_mission_id="mq-" + "1" * 32), record.PROBLEM_ID_GRAMMAR),
            (dict(repository_url=REPO_URL + ".git"),
             record.PROBLEM_REPOSITORY_IDENTITY),
            (dict(issue_ref=REPO_URL), record.PROBLEM_BAD_VALUE),
            (dict(project_key="Bad Key"), record.PROBLEM_BAD_VALUE),
            (dict(intent="MAYBE"), record.PROBLEM_BAD_VALUE),
            (dict(domain=""), record.PROBLEM_BAD_VALUE),
            (dict(proposal_digest_sha256="zz"), record.PROBLEM_BAD_VALUE),
            (dict(message_ref=""), record.PROBLEM_BAD_VALUE),
            (dict(transport="Grok"), record.PROBLEM_BAD_VALUE),
        ):
            with self.assertRaises(record.CoordinationError) as caught:
                inbound(**overrides).validate()
            self.assertEqual(caught.exception.problem, problem, overrides)


class ExplicitIdRoutingTests(unittest.TestCase):

    def test_explicit_id_resolves_deterministically(self):
        derivation = derive(inbound(explicit_mission_id=MISSION_X))
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(derivation.mission_id, MISSION_X)
        self.assertEqual(derivation.resolved_tier, record.TIER_EXPLICIT_MISSION_ID)
        self.assertEqual(derivation.reason, record.REASON_RESOLVED)
        self.assertEqual(derivation.candidates, [MISSION_X])
        self.assertEqual(derivation.observed_revision, 2)
        self.assertEqual(derivation.observation_point,
                         observation.observation_point(fresh()))
        self.assertIsNone(derivation.bound_revision)
        again = derive(inbound(explicit_mission_id=MISSION_X))
        self.assertEqual(again, derivation)

    def test_identity_resolution_ignores_lifecycle_and_authority(self):
        # Routing answers "which Mission"; a DENIED, unauthorized Mission
        # is still that Mission. Nothing here reads authority as permission.
        denied = observing(MISSION_X, lifecycle_state=record.LIFECYCLE_DENIED,
                           authorization_digest_sha256=None)
        derivation = derive(inbound(explicit_mission_id=MISSION_X), observe=denied)
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)

    def test_unobservable_explicit_id_clarifies_with_the_truthful_reason(self):
        cases = (
            (observation.ObservationOutcome.absent(), record.REASON_MISSION_NOT_OBSERVED),
            (observation.ObservationOutcome.unavailable("registry locked"),
             record.REASON_OBSERVATION_UNAVAILABLE),
            (observation.ObservationOutcome.observed(
                fresh(observed_at=NOW - observation.MAX_OBSERVATION_AGE_SECONDS - 1,
                      evidence_refs=(evidence_ref(accepted_at=NOW - 2000),
                                     recorded_evidence_ref()),
                      artifact_refs=(artifact_ref(
                          receipt=receipt(validated_at=NOW - 2000)),),
                      conditions=(condition(evidence_refs=(
                          evidence_ref(accepted_at=NOW - 2000),)),))),
             record.REASON_OBSERVATION_STALE),
            (observation.ObservationOutcome.observed(fresh(observed_at=NOW + 1)),
             record.REASON_OBSERVATION_INCONSISTENT),
        )
        for outcome, reason in cases:
            observer = Observer(**{MISSION_X: outcome})
            derivation = derive(inbound(explicit_mission_id=MISSION_X),
                                observe=observer)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED,
                             reason)
            self.assertEqual(derivation.reason, reason)
            self.assertIsNone(derivation.mission_id)
            self.assertEqual(derivation.lane_outcome, record.LANE_OUTCOME_NOT_REQUESTED)
            self.assertTrue(derivation.detail)
            self.assertEqual(derivation.candidates, [MISSION_X])
        unavailable = derive(inbound(explicit_mission_id=MISSION_X),
                             observe=Observer(**{MISSION_X: cases[1][0]}))
        self.assertIn("registry locked", unavailable.detail)

    def test_explicit_id_against_a_binding_naming_another_mission_disagrees(self):
        document = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "msg-1", mission_id=MISSION_Y))
        derivation = derive(inbound(explicit_mission_id=MISSION_X,
                                    reply_to_message_ref="msg-1"), document)
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_CONTEXT_DISAGREES)
        self.assertEqual(derivation.candidates, sorted([MISSION_X, MISSION_Y]))
        self.assertIsNone(derivation.mission_id)
        # Agreement is not disagreement.
        agreeing = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "msg-1"))
        derivation = derive(inbound(explicit_mission_id=MISSION_X,
                                    reply_to_message_ref="msg-1"), agreeing)
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(derivation.resolved_tier, record.TIER_EXPLICIT_MISSION_ID)

    def test_f3_explicit_id_never_bypasses_a_stale_matching_binding(self):
        # AMD-1 through the explicit tier: the binding names the SAME
        # Mission at revision 2, the Mission is at 3. Without the explicit
        # id the tier clarifies BINDING_STALE; naming the Mission must not
        # flip that into a resolution.
        moved = observing(MISSION_X, current_revision=3, state_cursor="12",
                          conditions=(condition(revision=3),))
        for kind, selector, turn in (
            (record.BINDING_APPROVAL_PRESENTATION, "card",
             inbound(reply_to_message_ref="card")),
            (record.BINDING_REPLY_TO_MESSAGE, "m", inbound(reply_to_message_ref="m")),
            (record.BINDING_ALIAS, "the-mission", inbound(alias="the-mission")),
            (record.BINDING_CONVERSATION, "", inbound()),
        ):
            document = with_bindings(make_binding(kind, selector))
            without = derive(turn, document, observe=moved)
            self.assertEqual(without.reason, record.REASON_BINDING_STALE, kind)
            fields = dict(turn.as_dict(), explicit_mission_id=MISSION_X,
                          domain=record.DOMAIN_ENGINEERING)
            with_id = derive(routing.InboundTurn(**fields), document, observe=moved)
            self.assertEqual(with_id.outcome, record.ROUTE_CLARIFICATION_REQUIRED, kind)
            self.assertEqual(with_id.reason, record.REASON_BINDING_STALE, kind)
            self.assertEqual(with_id.bound_revision, 2)
            self.assertEqual(with_id.observed_revision, 3)
            self.assertIsNone(with_id.mission_id)
            self.assertEqual(with_id.lane_outcome, record.LANE_OUTCOME_REFUSED)
            self.assertEqual(with_id.lane_reason, record.LANE_REASON_ROUTE_NOT_RESOLVED)
        # A matching binding at the CURRENT revision resolves through the
        # explicit tier and records the consulted context.
        document = with_bindings(make_binding(record.BINDING_REPLY_TO_MESSAGE, "m"))
        agreeing = derive(inbound(reply_to_message_ref="m",
                                  explicit_mission_id=MISSION_X), document)
        self.assertEqual(agreeing.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(agreeing.resolved_tier, record.TIER_EXPLICIT_MISSION_ID)
        self.assertEqual(agreeing.bound_revision, 2)
        routing.validate_route(routing.new_route_record(
            record.mint_id("cr"), inbound(reply_to_message_ref="m",
                                          explicit_mission_id=MISSION_X),
            agreeing, NOW, context()), "r")
        # An expired matching binding is stale context too: clarify.
        expired = with_bindings(make_binding(record.BINDING_REPLY_TO_MESSAGE, "m",
                                             bound_at=NOW - 100, expires_at=NOW))
        derivation = derive(inbound(reply_to_message_ref="m",
                                    explicit_mission_id=MISSION_X), expired)
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_BINDING_EXPIRED)

    def test_r2_1_unrelated_message_history_never_blocks_a_valid_reply(self):
        # A current revision-3 reply binding next to an unrelated
        # revision-2 message binding in the same conversation: replying to
        # the current message resolves, with and without the explicit id.
        at_three = fresh(current_revision=3, state_cursor="12",
                         conditions=(condition(revision=3),))
        document = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "current", obs=at_three),
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "old"))
        observer = Observer(**{MISSION_X: observation.ObservationOutcome.observed(
            at_three)})
        for turn in (inbound(reply_to_message_ref="current"),
                     inbound(reply_to_message_ref="current",
                             explicit_mission_id=MISSION_X)):
            derivation = derive(turn, document, observe=observer)
            self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION,
                             turn.explicit_mission_id)
            self.assertEqual(derivation.mission_id, MISSION_X)
            self.assertEqual(derivation.bound_revision, 3)
        # The stale binding the turn actually names still clarifies, with
        # and without the explicit id.
        for turn in (inbound(reply_to_message_ref="old"),
                     inbound(reply_to_message_ref="old",
                             explicit_mission_id=MISSION_X)):
            derivation = derive(turn, document, observe=observer)
            self.assertEqual(derivation.reason, record.REASON_BINDING_STALE)
            self.assertEqual(derivation.bound_revision, 2)
        # A genuine CONVERSATION-kind binding at revision 2 IS applicable
        # context for the explicit id and clarifies (AMD-1 stands).
        document["bindings"].update({
            b["binding_id"]: b for b in [make_binding(record.BINDING_CONVERSATION, "")]})
        derivation = derive(inbound(reply_to_message_ref="current",
                                    explicit_mission_id=MISSION_X), document,
                            observe=observer)
        self.assertEqual(derivation.reason, record.REASON_BINDING_STALE)
        self.assertEqual(derivation.bound_revision, 2)

    def test_r2_2_a_stale_decision_records_the_offending_binding(self):
        # A selected revision-3 reply plus a selected revision-2 alias: the
        # alias is what fails, so bound_revision must say 2, not 3.
        at_three = fresh(current_revision=3, state_cursor="12",
                         conditions=(condition(revision=3),))
        document = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m", obs=at_three),
            make_binding(record.BINDING_ALIAS, "the-mission"))
        observer = Observer(**{MISSION_X: observation.ObservationOutcome.observed(
            at_three)})
        for turn in (inbound(reply_to_message_ref="m", alias="the-mission",
                             explicit_mission_id=MISSION_X),):
            derivation = derive(turn, document, observe=observer)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
            self.assertEqual(derivation.reason, record.REASON_BINDING_STALE)
            self.assertEqual(derivation.bound_revision, 2)
            self.assertEqual(derivation.observed_revision, 3)
            self.assertIn("revision 2", derivation.detail)
            route = routing.new_route_record(record.mint_id("cr"), turn, derivation,
                                             NOW, context())
            self.assertEqual(route["bound_revision"], 2)
        # Without the explicit id the reply tier wins on its own and the
        # alias is never consulted: resolution at revision 3.
        derivation = derive(inbound(reply_to_message_ref="m", alias="the-mission"),
                            document, observe=observer)
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(derivation.bound_revision, 3)

    def test_explicit_id_with_new_mission_intent_disagrees(self):
        derivation = derive(inbound(explicit_mission_id=MISSION_X,
                                    intent=record.INTENT_NEW_MISSION,
                                    proposal_digest_sha256=HEX_A))
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_CONTEXT_DISAGREES)
        self.assertIsNone(derivation.proposal_digest_sha256)


class BindingTierRoutingTests(unittest.TestCase):

    TIER_BY_KIND = {
        record.BINDING_REPLY_TO_MESSAGE: record.TIER_REPLY_TO_BINDING,
        record.BINDING_APPROVAL_PRESENTATION: record.TIER_APPROVAL_BINDING,
        record.BINDING_RESULT_PRESENTATION: record.TIER_RESULT_BINDING,
        record.BINDING_REPOSITORY: record.TIER_REPOSITORY_REFERENCE,
        record.BINDING_ISSUE: record.TIER_ISSUE_REFERENCE,
        record.BINDING_PROJECT: record.TIER_PROJECT_BINDING,
        record.BINDING_ALIAS: record.TIER_ALIAS_BINDING,
        record.BINDING_CONVERSATION: record.TIER_UNIQUE_CONVERSATION_MATCH,
    }

    def turn_for(self, kind):
        return {
            record.BINDING_REPLY_TO_MESSAGE: inbound(reply_to_message_ref="m"),
            record.BINDING_APPROVAL_PRESENTATION: inbound(reply_to_message_ref="m"),
            record.BINDING_RESULT_PRESENTATION: inbound(reply_to_message_ref="m"),
            record.BINDING_REPOSITORY: inbound(repository_url=REPO_URL),
            record.BINDING_ISSUE: inbound(issue_ref=REPO_URL + "#42"),
            record.BINDING_PROJECT: inbound(project_key="example.repo"),
            record.BINDING_ALIAS: inbound(alias="the-mission"),
            record.BINDING_CONVERSATION: inbound(),
        }[kind]

    def selector_for(self, kind):
        return {
            record.BINDING_REPLY_TO_MESSAGE: "m",
            record.BINDING_APPROVAL_PRESENTATION: "m",
            record.BINDING_RESULT_PRESENTATION: "m",
            record.BINDING_REPOSITORY: REPO_URL,
            record.BINDING_ISSUE: REPO_URL + "#42",
            record.BINDING_PROJECT: "example.repo",
            record.BINDING_ALIAS: "the-mission",
            record.BINDING_CONVERSATION: "",
        }[kind]

    def test_every_kind_resolves_at_its_bound_revision(self):
        for kind in record.BINDING_KINDS:
            document = with_bindings(make_binding(kind, self.selector_for(kind)))
            derivation = derive(self.turn_for(kind), document)
            self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION, kind)
            self.assertEqual(derivation.mission_id, MISSION_X)
            self.assertEqual(derivation.resolved_tier, self.TIER_BY_KIND[kind])
            self.assertEqual(derivation.bound_revision, 2)
            self.assertEqual(derivation.observed_revision, 2)

    def test_every_kind_clarifies_when_the_revision_moved(self):
        # AMD-1: no kind resolves across a revision change; no escape flag.
        moved = observing(MISSION_X, current_revision=3, state_cursor="12",
                          conditions=(condition(revision=3),))
        for kind in record.BINDING_KINDS:
            document = with_bindings(make_binding(kind, self.selector_for(kind)))
            derivation = derive(self.turn_for(kind), document, observe=moved)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED,
                             kind)
            self.assertEqual(derivation.reason, record.REASON_BINDING_STALE)
            self.assertIsNone(derivation.mission_id)
            self.assertEqual(derivation.bound_revision, 2)
            self.assertEqual(derivation.observed_revision, 3)
            self.assertEqual(derivation.candidates, [MISSION_X])
            self.assertFalse(hasattr(derivation, "revision_advanced"))
            # Routing never repaired the binding.
            stored = list(document["bindings"].values())[0]
            self.assertEqual(stored["bound_revision"], 2)
            self.assertFalse(stored["revoked"])

    def test_repository_binding_clarifies_when_the_observed_repository_differs(self):
        elsewhere = observing(MISSION_X, repository_url=OTHER_REPO_URL)
        for kind, selector in ((record.BINDING_REPOSITORY, REPO_URL),
                               (record.BINDING_ISSUE, REPO_URL + "#42")):
            document = with_bindings(make_binding(kind, selector))
            derivation = derive(self.turn_for(kind), document, observe=elsewhere)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
            self.assertEqual(derivation.reason, record.REASON_BINDING_STALE)

    def test_expired_binding_clarifies_and_revoked_binding_is_invisible(self):
        expired = make_binding(record.BINDING_REPLY_TO_MESSAGE, "m",
                               bound_at=NOW - 100, expires_at=NOW)
        derivation = derive(inbound(reply_to_message_ref="m"), with_bindings(expired))
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_BINDING_EXPIRED)
        self.assertIsNone(derivation.mission_id)
        revoked = binding.revoke(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m"), NOW - 1)
        derivation = derive(inbound(reply_to_message_ref="m"), with_bindings(revoked))
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_NO_CONTEXT)

    def test_unobservable_bound_mission_clarifies_truthfully(self):
        document = with_bindings(make_binding(record.BINDING_ALIAS, "the-mission"))
        for outcome, reason in (
            (observation.ObservationOutcome.absent(), record.REASON_BINDING_STALE),
            (observation.ObservationOutcome.unavailable("down"),
             record.REASON_OBSERVATION_UNAVAILABLE),
        ):
            derivation = derive(inbound(alias="the-mission"), document,
                                observe=Observer(**{MISSION_X: outcome}))
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
            self.assertEqual(derivation.reason, reason)

    def test_tier_order_is_deterministic(self):
        # A reply-to binding outranks a repository binding that names
        # another Mission; the first tier with context wins.
        document = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m"),
            make_binding(record.BINDING_REPOSITORY, REPO_URL, mission_id=MISSION_Y))
        derivation = derive(inbound(reply_to_message_ref="m",
                                    repository_url=REPO_URL), document)
        self.assertEqual(derivation.mission_id, MISSION_X)
        self.assertEqual(derivation.resolved_tier, record.TIER_REPLY_TO_BINDING)
        derivation = derive(inbound(repository_url=REPO_URL), document)
        self.assertEqual(derivation.mission_id, MISSION_Y)
        self.assertEqual(derivation.resolved_tier, record.TIER_REPOSITORY_REFERENCE)

    def test_unique_conversation_match_and_ambiguity(self):
        one = with_bindings(make_binding(record.BINDING_CONVERSATION, ""))
        derivation = derive(inbound(), one)
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(derivation.resolved_tier,
                         record.TIER_UNIQUE_CONVERSATION_MATCH)
        two = with_bindings(
            make_binding(record.BINDING_CONVERSATION, ""),
            make_binding(record.BINDING_CONVERSATION, "", mission_id=MISSION_Y,
                         binding_id=record.mint_id("cb")))
        with self.assertRaises(record.CoordinationError):
            binding.validate_bindings(two, "<doc>")  # same identity twice

    def test_ambiguous_context_clarifies_and_never_guesses(self):
        # The conversation tier sees every active conversation-scoped
        # binding in the conversation. Three reply bindings naming three
        # Missions make a turn that names none of them ambiguous.
        candidates = ["mn-%032x" % i for i in range(1, 4)]
        observer = observing(*candidates)
        document = store.default_document()
        for mission_id in candidates:
            bound = make_binding(record.BINDING_REPLY_TO_MESSAGE,
                                 "reply-" + mission_id[-2:], mission_id=mission_id)
            document["bindings"][bound["binding_id"]] = bound
        members = routing.conversation_missions(document, "grok_mcp", CONVERSATION_A,
                                                NOW)
        self.assertEqual(members, sorted(candidates))
        for turn in (inbound(), inbound(intent=record.INTENT_FOLLOW_UP),
                     inbound(proposal_digest_sha256=HEX_A)):
            derivation = derive(turn, document, observe=observer)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
            self.assertEqual(derivation.reason, record.REASON_AMBIGUOUS_CANDIDATES)
            self.assertEqual(derivation.candidates, sorted(candidates))
            self.assertIsNone(derivation.mission_id)
            self.assertIsNone(derivation.proposal_digest_sha256)
        # Naming one of them by reply reference resolves it.
        derivation = derive(inbound(reply_to_message_ref="reply-" + candidates[1][-2:]),
                            document, observe=observer)
        self.assertEqual(derivation.mission_id, candidates[1])
        # A revoked binding leaves the conversation.
        for bound in list(document["bindings"].values()):
            if bound["mission_id"] != candidates[0]:
                document["bindings"][bound["binding_id"]] = binding.revoke(bound, NOW)
        derivation = derive(inbound(), document, observe=observer)
        self.assertEqual(derivation.outcome, record.ROUTE_EXISTING_MISSION)
        self.assertEqual(derivation.mission_id, candidates[0])
        self.assertEqual(derivation.resolved_tier,
                         record.TIER_UNIQUE_CONVERSATION_MATCH)

    def test_ambiguity_above_the_candidate_bound_clarifies_without_enumerating(self):
        many = ["mn-%032x" % i for i in range(1, routing.MAX_ROUTE_CANDIDATES + 2)]
        document = store.default_document()
        for mission_id in many:
            bound = make_binding(record.BINDING_REPLY_TO_MESSAGE,
                                 "reply-" + mission_id[-3:], mission_id=mission_id)
            document["bindings"][bound["binding_id"]] = bound
        derivation = derive(inbound(intent=record.INTENT_FOLLOW_UP), document,
                            observe=observing(*many))
        self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(derivation.reason, record.REASON_AMBIGUOUS_CANDIDATES)
        self.assertEqual(derivation.candidates, [])
        self.assertIn(str(routing.MAX_ROUTE_CANDIDATES), derivation.detail)
        self.assertEqual(routing.MAX_ROUTE_CANDIDATES, 16)

    def test_two_concurrent_missions_stay_isolated_across_conversations(self):
        document = with_bindings(
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m", mission_id=MISSION_X,
                         conversation_ref=CONVERSATION_A),
            make_binding(record.BINDING_REPLY_TO_MESSAGE, "m", mission_id=MISSION_Y,
                         conversation_ref=CONVERSATION_B),
            make_binding(record.BINDING_CONVERSATION, "", mission_id=MISSION_X,
                         conversation_ref=CONVERSATION_A),
            make_binding(record.BINDING_CONVERSATION, "", mission_id=MISSION_Y,
                         conversation_ref=CONVERSATION_B))
        in_a = derive(inbound(conversation_ref=CONVERSATION_A,
                              reply_to_message_ref="m"), document)
        in_b = derive(inbound(conversation_ref=CONVERSATION_B,
                              reply_to_message_ref="m"), document)
        self.assertEqual((in_a.mission_id, in_b.mission_id), (MISSION_X, MISSION_Y))
        self.assertEqual(derive(inbound(conversation_ref=CONVERSATION_A),
                                document).mission_id, MISSION_X)
        self.assertEqual(derive(inbound(conversation_ref=CONVERSATION_B),
                                document).mission_id, MISSION_Y)
        # A reply reference from A means nothing in an unbound conversation.
        elsewhere = derive(inbound(conversation_ref="chat-c",
                                   reply_to_message_ref="m"), document)
        self.assertEqual(elsewhere.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(elsewhere.reason, record.REASON_NO_CONTEXT)


class NewProposalAndLaneTests(unittest.TestCase):

    def test_new_proposal_when_nothing_resolves_and_a_proposal_is_present(self):
        derivation = derive(inbound(proposal_digest_sha256=HEX_A))
        self.assertEqual(derivation.outcome, record.ROUTE_NEW_PROPOSAL)
        self.assertEqual(derivation.reason, record.REASON_NEW_PROPOSAL_INTENT)
        self.assertEqual(derivation.proposal_digest_sha256, HEX_A)
        self.assertIsNone(derivation.mission_id)
        self.assertEqual(derivation.candidates, [])
        explicit = derive(inbound(intent=record.INTENT_NEW_MISSION,
                                  proposal_digest_sha256=HEX_A))
        self.assertEqual(explicit.outcome, record.ROUTE_NEW_PROPOSAL)

    def test_new_mission_intent_outranks_context_but_not_an_explicit_id(self):
        document = with_bindings(make_binding(record.BINDING_CONVERSATION, ""))
        derivation = derive(inbound(intent=record.INTENT_NEW_MISSION,
                                    proposal_digest_sha256=HEX_A), document)
        self.assertEqual(derivation.outcome, record.ROUTE_NEW_PROPOSAL)

    def test_no_context_and_no_proposal_clarifies(self):
        for turn in (inbound(), inbound(intent=record.INTENT_FOLLOW_UP,
                                        proposal_digest_sha256=HEX_A),
                     inbound(intent=record.INTENT_NEW_MISSION)):
            derivation = derive(turn)
            self.assertEqual(derivation.outcome, record.ROUTE_CLARIFICATION_REQUIRED)
            self.assertEqual(derivation.reason, record.REASON_NO_CONTEXT)
            self.assertIsNone(derivation.proposal_digest_sha256)

    def test_engineering_selects_the_herdr_lane_as_a_value(self):
        derivation = derive(inbound(explicit_mission_id=MISSION_X,
                                    domain=record.DOMAIN_ENGINEERING))
        self.assertEqual(derivation.lane_outcome, record.LANE_OUTCOME_SELECTED)
        self.assertEqual(derivation.lane, record.LANE_ENGINEERING)
        self.assertIsNone(derivation.lane_reason)
        proposal = derive(inbound(proposal_digest_sha256=HEX_A,
                                  domain=record.DOMAIN_ENGINEERING))
        self.assertEqual(proposal.lane, record.LANE_ENGINEERING)

    def test_unsupported_domain_and_unavailable_capability_explain_refusal(self):
        unsupported = derive(inbound(explicit_mission_id=MISSION_X, domain="MARKETING"))
        self.assertEqual(unsupported.lane_outcome, record.LANE_OUTCOME_REFUSED)
        self.assertEqual(unsupported.lane_reason, record.LANE_REASON_DOMAIN_UNSUPPORTED)
        self.assertIsNone(unsupported.lane)
        self.assertEqual(unsupported.outcome, record.ROUTE_EXISTING_MISSION)
        unavailable = derive(inbound(explicit_mission_id=MISSION_X,
                                     domain=record.DOMAIN_ENGINEERING), lanes=())
        self.assertEqual(unavailable.lane_outcome, record.LANE_OUTCOME_REFUSED)
        self.assertEqual(unavailable.lane_reason,
                         record.LANE_REASON_CAPABILITY_UNAVAILABLE)
        unresolved = derive(inbound(domain=record.DOMAIN_ENGINEERING))
        self.assertEqual(unresolved.lane_outcome, record.LANE_OUTCOME_REFUSED)
        self.assertEqual(unresolved.lane_reason, record.LANE_REASON_ROUTE_NOT_RESOLVED)
        none = derive(inbound(explicit_mission_id=MISSION_X))
        self.assertEqual(none.lane_outcome, record.LANE_OUTCOME_NOT_REQUESTED)
        self.assertIsNone(none.lane)

    def test_authority_never_becomes_execution_permission(self):
        # Missing (null), or a Mission the source shows DENIED: the lane is
        # still only a value, and the record carries no permission at all.
        for overrides in (dict(authorization_digest_sha256=None),
                          dict(lifecycle_state=record.LIFECYCLE_DENIED,
                               authorization_digest_sha256=None)):
            route = routed(observe=observing(MISSION_X, **overrides),
                           explicit_mission_id=MISSION_X,
                           domain=record.DOMAIN_ENGINEERING)
            self.assertEqual(route["lane"], record.LANE_ENGINEERING)
            self.assertEqual(route["authority"], "none")
            self.assertEqual(tuple(route), routing.ROUTE_KEYS)
            for key in route:
                self.assertNotIn("permission", key)
                self.assertNotIn("authoriz", key)
        with self.assertRaises(record.CoordinationError) as caught:
            routing.validate_route(dict(routed(explicit_mission_id=MISSION_X),
                                        authority="execution"), "r")
        self.assertEqual(caught.exception.problem, record.PROBLEM_AUTHORITY_CLAIM)


class RouteRecordTests(unittest.TestCase):

    def test_record_is_closed_inspectable_and_round_trips(self):
        route = routed(explicit_mission_id=MISSION_X, domain=record.DOMAIN_ENGINEERING)
        self.assertEqual(tuple(route), routing.ROUTE_KEYS)
        self.assertEqual(tuple(route["inbound"]), routing.INBOUND_KEYS)
        self.assertEqual(route["decided_at"], NOW)
        self.assertEqual(route["provenance"]["observed_revision"], 2)
        self.assertEqual(route["provenance"]["observation_cursor"], "7")
        routing.validate_route(route, "r")
        self.assertEqual(json.loads(json.dumps(route)), route)

    def test_coherence_rules(self):
        existing = routed(explicit_mission_id=MISSION_X)
        clarifying = routed()
        proposal = routed(proposal_digest_sha256=HEX_A)
        cases = (
            (existing, dict(mission_id=None)),
            (existing, dict(resolved_tier=None)),
            (existing, dict(candidates=[])),
            (existing, dict(proposal_digest_sha256=HEX_A)),
            (existing, dict(lane=record.LANE_ENGINEERING)),
            (clarifying, dict(mission_id=MISSION_X)),
            (clarifying, dict(observed_revision=2)),
            (clarifying, dict(reason=record.REASON_RESOLVED)),
            (proposal, dict(proposal_digest_sha256=None)),
            (proposal, dict(candidates=[MISSION_Y, MISSION_X])),
            (existing, dict(lane_outcome=record.LANE_OUTCOME_REFUSED)),
            (existing, dict(inbound_digest_sha256=HEX_B)),
            (existing, dict(decided_at=-1)),
        )
        for base, overrides in cases:
            with self.assertRaises(record.CoordinationError, msg=overrides):
                routing.validate_route(dict(base, **overrides), "r")
        with self.assertRaises(record.CoordinationError):
            routing.validate_route(dict(existing, extra=1), "r")

    def test_document_validation_refuses_duplicate_inbound_identity(self):
        first = routed(explicit_mission_id=MISSION_X)
        second = routed(explicit_mission_id=MISSION_Y)
        document = store.default_document()
        document["route_decisions"][first["route_id"]] = first
        document["route_decisions"][second["route_id"]] = second
        with self.assertRaises(record.CoordinationError) as caught:
            routing.validate_route_decisions(document, "<doc>")
        self.assertEqual(caught.exception.problem, record.PROBLEM_ROUTE_CONFLICT)
        del document["route_decisions"][second["route_id"]]
        other = routed(explicit_mission_id=MISSION_Y, message_ref="msg-101")
        document["route_decisions"][other["route_id"]] = other
        routing.validate_route_decisions(document, "<doc>")
        document["route_decisions"]["cr-" + "0" * 32] = first
        with self.assertRaises(record.CoordinationError):
            routing.validate_route_decisions(document, "<doc>")


class ReplayTests(unittest.TestCase):

    def stored(self, **overrides):
        route = routed(decided_at=NOW - 500, **overrides)
        document = store.default_document()
        document["route_decisions"][route["route_id"]] = route
        return document, route

    def test_same_context_replay_returns_history_without_observing(self):
        document, route = self.stored(proposal_digest_sha256=HEX_A)
        replay = routing.find_replay(document, inbound(proposal_digest_sha256=HEX_A),
                                     context())
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.route, route)
        self.assertEqual(replay.route["decided_at"], NOW - 500)
        self.assertEqual(replay.route["proposal_digest_sha256"], HEX_A)
        self.assertEqual(replay.route["outcome"], record.ROUTE_NEW_PROPOSAL)
        # Still exactly one record; nothing was rewritten.
        self.assertEqual(len(document["route_decisions"]), 1)

    def test_no_record_means_no_replay(self):
        document, _ = self.stored()
        self.assertIsNone(routing.find_replay(document, inbound(message_ref="other"),
                                              context()))

    def test_replay_from_another_context_is_refused_without_content(self):
        document, route = self.stored(explicit_mission_id=MISSION_X)
        for other in (context(principal_ref="credential-1"),
                      context(transport="telegram"),
                      context(principal_kind=record.PRINCIPAL_KIND_LOCAL_PROCESS_USER),
                      context(configured_subject=None)):
            with self.assertRaises(record.CoordinationError) as caught:
                routing.find_replay(document, inbound(explicit_mission_id=MISSION_X),
                                    other)
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_CONTEXT_MISMATCH)
            self.assertNotIn(MISSION_X, str(caught.exception))
            self.assertNotIn(route["route_id"], str(caught.exception))

    def test_same_context_different_content_is_an_idempotency_conflict(self):
        document, _ = self.stored(explicit_mission_id=MISSION_X)
        with self.assertRaises(record.CoordinationError) as caught:
            routing.find_replay(document, inbound(explicit_mission_id=MISSION_Y),
                                context())
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_IDEMPOTENCY_CONFLICT)


# =====================================================================
# F. attention
# =====================================================================

DESTINATION_A = {"transport": "grok_mcp", "conversation_ref": CONVERSATION_A}
DESTINATION_B = {"transport": "grok_mcp", "conversation_ref": CONVERSATION_B}


def needs_human(**overrides):
    fields = dict(kind=record.ATTENTION_NEEDS_HUMAN, key="clarify.scope",
                  detail="scope question", evidence_refs=(), artifact_refs=())
    fields.update(overrides)
    return condition(**fields)


def result_of(obs, now=NOW):
    return observation.classify(observation.ObservationOutcome.observed(obs), now,
                                obs.mission_id)


def fresh_result(**overrides):
    return result_of(fresh(**overrides))


class RecordingPresenter(attention.AttentionPresenter):
    """Test-only seam implementation: records what it was asked to
    present and answers with a scripted receipt. Sends nothing."""

    def __init__(self, ok=True, problem=None):
        self.ok = ok
        self.problem = problem
        self.presented = []

    def present(self, destination, presentation):
        self.presented.append((dict(destination), dict(presentation)))
        if self.ok:
            return attention.PresentationReceipt(
                ok=True, message_ref="out-%d" % len(self.presented), problem=None)
        return attention.PresentationReceipt(ok=False, message_ref=None,
                                             problem=self.problem or "declined")


class AttentionProjectionTests(unittest.TestCase):

    def setUp(self):
        self.document = store.default_document()
        self.minted = []

    def mint(self):
        value = record.mint_id("ca")
        self.minted.append(value)
        return value

    def project(self, result=None, destination=DESTINATION_A, mission_id=MISSION_X,
                now=NOW):
        return attention.project(self.document, mission_id, destination,
                                 result or fresh_result(mission_id=mission_id), now,
                                 self.mint)

    def records(self):
        return self.document["attention"]

    def only(self, presentation=None):
        found = [r for r in self.records().values()
                 if presentation is None or r["presentation"] == presentation]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_fresh_condition_creates_one_pending_record(self):
        outcome = self.project()
        self.assertEqual(outcome.freshness, record.FRESHNESS_FRESH)
        self.assertEqual(outcome.created, self.minted)
        self.assertEqual((outcome.suppressed, outcome.obsoleted, outcome.resolved),
                         ([], [], []))
        created = self.only()
        self.assertEqual(tuple(created), attention.ATTENTION_KEYS)
        self.assertEqual(created["presentation"], record.PRESENTATION_PENDING)
        self.assertEqual(created["mission_id"], MISSION_X)
        self.assertEqual(created["revision"], 2)
        self.assertEqual(created["condition_kind"], record.ATTENTION_BLOCKED)
        self.assertEqual(created["condition_key"], "reviewer.loop")
        self.assertEqual(created["condition_digest_sha256"],
                         observation.condition_digest(condition()))
        self.assertEqual(created["authorization_digest_sha256"], HEX_B)
        self.assertEqual(created["destination"], DESTINATION_A)
        self.assertEqual(created["priority"], 30)
        self.assertEqual(created["created_at"], NOW)
        self.assertEqual(created["observation_point"],
                         observation.observation_point(fresh()))
        self.assertEqual(created["authority"], "none")
        for key in ("surfaced_at", "surfaced_message_ref", "surfaced_freshness",
                    "acknowledged_at", "acknowledged_by", "acknowledged_freshness",
                    "acknowledged_observation_cursor", "closed_at", "closed_reason",
                    "superseded_by"):
            self.assertIsNone(created[key], key)
        attention.validate_attention(created, "a")

    def test_repeated_unchanged_blocker_is_suppressed_durably(self):
        first = self.project()
        for _ in range(3):
            again = self.project(now=NOW + 60)
            self.assertEqual(again.created, [])
            self.assertEqual(again.suppressed, first.created)
        self.assertEqual(len(self.records()), 1)
        # A later read at a later cursor with the same condition is still
        # the same presentation: duplicate suppression keys on content.
        later = self.project(result_of(fresh(state_cursor="9", observed_at=NOW + 100),
                                       now=NOW + 100),
                             now=NOW + 100)
        self.assertEqual(later.suppressed, first.created)
        self.assertEqual(len(self.records()), 1)

    def test_changed_condition_obsoletes_and_creates_a_successor(self):
        first = self.project()
        changed = fresh_result(conditions=(condition(detail="rejected three times"),))
        outcome = self.project(changed, now=NOW + 10)
        self.assertEqual(outcome.obsoleted, first.created)
        self.assertEqual(len(outcome.created), 1)
        old = self.records()[first.created[0]]
        new = self.records()[outcome.created[0]]
        self.assertEqual(old["presentation"], record.PRESENTATION_OBSOLETE)
        self.assertEqual(old["closed_reason"], record.CLOSED_CONDITION_CHANGED)
        self.assertEqual(old["closed_at"], NOW + 10)
        self.assertEqual(old["superseded_by"], new["attention_id"])
        self.assertEqual(new["presentation"], record.PRESENTATION_PENDING)
        self.assertEqual(new["condition_digest_sha256"],
                         observation.condition_digest(
                             condition(detail="rejected three times")))
        self.assertEqual(new["created_at"], NOW + 10)
        attention.validate_attention_records(self.document, "<doc>")

    def test_revision_and_authority_changes_have_their_own_reasons(self):
        first = self.project()
        moved = fresh_result(current_revision=3, state_cursor="12",
                             conditions=(condition(revision=3),))
        outcome = self.project(moved, now=NOW + 1)
        self.assertEqual(self.records()[first.created[0]]["closed_reason"],
                         record.CLOSED_REVISION_CHANGED)
        second = outcome.created
        # Authority changed at the same revision: null <-> non-null and
        # digest change all count.
        for digest in (None, HEX_A):
            changed = fresh_result(current_revision=3, state_cursor="13",
                                   authorization_digest_sha256=digest,
                                   conditions=(condition(revision=3),))
            outcome = self.project(changed, now=NOW + 2)
            self.assertEqual(self.records()[second[0]]["closed_reason"],
                             record.CLOSED_AUTHORITY_CHANGED)
            self.assertEqual(self.records()[outcome.created[0]][
                "authorization_digest_sha256"], digest)
            second = outcome.created
        # Revision change outranks authority change in the recorded reason.
        both = fresh_result(current_revision=4, state_cursor="20",
                            authorization_digest_sha256=HEX_B,
                            conditions=(condition(revision=4),))
        outcome = self.project(both, now=NOW + 3)
        self.assertEqual(self.records()[second[0]]["closed_reason"],
                         record.CLOSED_REVISION_CHANGED)
        attention.validate_attention_records(self.document, "<doc>")

    def test_cleared_condition_resolves_without_a_successor(self):
        first = self.project()
        outcome = self.project(fresh_result(conditions=()), now=NOW + 5)
        self.assertEqual(outcome.resolved, first.created)
        self.assertEqual(outcome.created, [])
        closed = self.only()
        self.assertEqual(closed["presentation"], record.PRESENTATION_RESOLVED)
        self.assertEqual(closed["closed_reason"], record.CLOSED_CONDITION_CLEARED)
        self.assertIsNone(closed["superseded_by"])
        # It comes back as a NEW presentation, never by reopening.
        again = self.project(now=NOW + 6)
        self.assertEqual(len(again.created), 1)
        self.assertEqual(len(self.records()), 2)

    def test_non_fresh_observation_changes_nothing_and_reports_freshness(self):
        first = self.project()
        cases = (
            observation.classify(observation.ObservationOutcome.unavailable("down"),
                                 NOW, MISSION_X),
            observation.classify(observation.ObservationOutcome.absent(), NOW,
                                 MISSION_X),
            result_of(fresh(observed_at=NOW + 1)),
            observation.classify(
                observation.ObservationOutcome.observed(fresh(conditions=())),
                NOW, MISSION_X,
                observation.HighWaterMark(revision=2, cursor="8", point=None)),
        )
        for result in cases:
            self.assertNotEqual(result.freshness, record.FRESHNESS_FRESH)
            outcome = self.project(result, now=NOW + 10)
            self.assertEqual(outcome.freshness, result.freshness)
            self.assertEqual((outcome.created, outcome.suppressed, outcome.obsoleted,
                              outcome.resolved), ([], [], [], []))
            self.assertIsNotNone(outcome.problem)
        self.assertEqual(self.records()[first.created[0]]["presentation"],
                         record.PRESENTATION_PENDING)

    def test_projection_is_deterministic_and_per_destination(self):
        two = fresh_result(conditions=(condition(), needs_human()))
        outcome = self.project(two)
        kinds = [self.records()[i]["condition_kind"] for i in outcome.created]
        self.assertEqual(kinds, [record.ATTENTION_BLOCKED, record.ATTENTION_NEEDS_HUMAN])
        other = self.project(two, destination=DESTINATION_B)
        self.assertEqual(len(other.created), 2)
        self.assertEqual(len(self.records()), 4)
        # Clearing at destination A leaves B's presentations untouched.
        self.project(fresh_result(conditions=()), now=NOW + 1)
        states = sorted((r["destination"]["conversation_ref"], r["presentation"])
                        for r in self.records().values())
        self.assertEqual(states, [
            (CONVERSATION_A, record.PRESENTATION_RESOLVED),
            (CONVERSATION_A, record.PRESENTATION_RESOLVED),
            (CONVERSATION_B, record.PRESENTATION_PENDING),
            (CONVERSATION_B, record.PRESENTATION_PENDING)])

    def test_projection_refuses_the_wrong_mission_and_bad_inputs(self):
        with self.assertRaises(record.CoordinationError) as caught:
            self.project(fresh_result(mission_id=MISSION_Y))
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_MISMATCH)
        with self.assertRaises(record.CoordinationError):
            self.project(destination={"transport": "grok_mcp"})
        with self.assertRaises(record.CoordinationError):
            attention.project(self.document, MISSION_X, DESTINATION_A, "fresh", NOW,
                              self.mint)


class AttentionSurfaceTests(unittest.TestCase):

    def setUp(self):
        self.document = store.default_document()
        self.presenter = RecordingPresenter()
        outcome = attention.project(self.document, MISSION_X, DESTINATION_A,
                                    fresh_result(), NOW, lambda: record.mint_id("ca"))
        self.attention_id = outcome.created[0]

    def record_(self):
        return self.document["attention"][self.attention_id]

    def surface(self, result=None, presenter=None, now=NOW + 1):
        return attention.surface(self.document, self.attention_id,
                                 result or fresh_result(), presenter or self.presenter,
                                 now, lambda: record.mint_id("ca"))

    def test_seam_is_abstract_in_product_code(self):
        with self.assertRaises(TypeError):
            attention.AttentionPresenter()
        self.assertEqual(set(attention.AttentionPresenter.__abstractmethods__),
                         {"present"})
        concrete = [name for name, value in vars(attention).items()
                    if isinstance(value, type)
                    and issubclass(value, attention.AttentionPresenter)
                    and value is not attention.AttentionPresenter]
        self.assertEqual(concrete, [])

    def test_surfacing_requires_a_fresh_matching_observation(self):
        outcome = self.surface()
        self.assertTrue(outcome.surfaced)
        self.assertEqual(outcome.freshness, record.FRESHNESS_FRESH)
        self.assertEqual(len(self.presenter.presented), 1)
        destination, presented = self.presenter.presented[0]
        self.assertEqual(destination, DESTINATION_A)
        self.assertEqual(presented["attention_id"], self.attention_id)
        surfaced = self.record_()
        self.assertEqual(surfaced["presentation"], record.PRESENTATION_SURFACED)
        self.assertEqual(surfaced["surfaced_at"], NOW + 1)
        self.assertEqual(surfaced["surfaced_message_ref"], "out-1")
        self.assertEqual(surfaced["surfaced_freshness"], record.FRESHNESS_FRESH)
        attention.validate_attention_records(self.document, "<doc>")
        with self.assertRaises(record.CoordinationError) as caught:
            self.surface()
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)
        self.assertEqual(len(self.presenter.presented), 1)

    def test_non_fresh_observation_performs_no_transition(self):
        for result in (
            observation.classify(observation.ObservationOutcome.unavailable("down"),
                                 NOW, MISSION_X),
            observation.classify(observation.ObservationOutcome.absent(), NOW,
                                 MISSION_X),
            result_of(fresh(observed_at=NOW + 5)),
            observation.classify(
                observation.ObservationOutcome.observed(fresh()), NOW, MISSION_X,
                observation.HighWaterMark(revision=3, cursor="1", point=None)),
        ):
            outcome = self.surface(result)
            self.assertFalse(outcome.surfaced)
            self.assertEqual(outcome.freshness, result.freshness)
            self.assertFalse(outcome.contradicted)
            self.assertEqual(self.record_()["presentation"], record.PRESENTATION_PENDING)
        self.assertEqual(self.presenter.presented, [])

    def test_fresh_contradiction_obsoletes_or_resolves_instead_of_surfacing(self):
        cases = (
            (fresh_result(conditions=(condition(detail="other"),)),
             record.CLOSED_CONDITION_CHANGED, True),
            (fresh_result(current_revision=3, state_cursor="12",
                          conditions=(condition(revision=3),)),
             record.CLOSED_REVISION_CHANGED, True),
            (fresh_result(authorization_digest_sha256=None),
             record.CLOSED_AUTHORITY_CHANGED, True),
            (fresh_result(conditions=()), record.CLOSED_CONDITION_CLEARED, False),
        )
        for result, reason, successor in cases:
            document = store.default_document()
            created = attention.project(document, MISSION_X, DESTINATION_A,
                                        fresh_result(), NOW,
                                        lambda: record.mint_id("ca")).created[0]
            presenter = RecordingPresenter()
            outcome = attention.surface(document, created, result, presenter, NOW + 1,
                                        lambda: record.mint_id("ca"))
            self.assertFalse(outcome.surfaced)
            self.assertTrue(outcome.contradicted)
            self.assertEqual(presenter.presented, [])
            closed = document["attention"][created]
            self.assertEqual(closed["closed_reason"], reason)
            self.assertIsNone(closed["surfaced_at"])
            if successor:
                self.assertEqual(outcome.obsoleted, [created])
                self.assertEqual(len(outcome.created), 1)
                self.assertEqual(closed["superseded_by"], outcome.created[0])
            else:
                self.assertEqual(outcome.resolved, [created])
                self.assertEqual(outcome.created, [])
            attention.validate_attention_records(document, "<doc>")

    def test_a_declined_presentation_leaves_the_record_pending(self):
        declined = RecordingPresenter(ok=False, problem="chunk cap")
        outcome = self.surface(presenter=declined)
        self.assertFalse(outcome.surfaced)
        self.assertFalse(outcome.contradicted)
        self.assertEqual(outcome.problem, "chunk cap")
        self.assertEqual(self.record_()["presentation"], record.PRESENTATION_PENDING)
        self.assertEqual(len(declined.presented), 1)

    def test_presenter_misbehaviour_never_marks_surfaced(self):
        class Raising(attention.AttentionPresenter):
            def present(self, destination, presentation):
                raise RuntimeError("secret provider detail")

        class WrongType(attention.AttentionPresenter):
            def present(self, destination, presentation):
                return {"ok": True}

        for presenter in (Raising(), WrongType()):
            outcome = self.surface(presenter=presenter)
            self.assertFalse(outcome.surfaced)
            self.assertNotIn("secret", outcome.problem or "")
            self.assertEqual(self.record_()["presentation"], record.PRESENTATION_PENDING)
        with self.assertRaises(record.CoordinationError):
            self.surface(presenter=object())

    def test_f6_a_mutating_presenter_cannot_change_the_stored_record(self):
        class Mutating(attention.AttentionPresenter):
            def present(self, destination, presentation):
                destination["conversation_ref"] = "elsewhere"
                presentation["destination"]["conversation_ref"] = "elsewhere"
                presentation["observation_point"]["cursor"] = "99"
                presentation["authority"] = "delivery"
                return attention.PresentationReceipt(ok=True, message_ref="out-m",
                                                     problem=None)

        before = json.loads(json.dumps(self.record_()))
        outcome = self.surface(presenter=Mutating())
        self.assertTrue(outcome.surfaced)
        after = self.record_()
        self.assertEqual(after["destination"], DESTINATION_A)
        self.assertEqual(after["observation_point"], before["observation_point"])
        self.assertEqual(after["authority"], "none")
        self.assertEqual(after["surfaced_message_ref"], "out-m")
        attention.validate_attention_records(self.document, "<doc>")

    def test_surfacing_the_wrong_mission_observation_is_refused(self):
        with self.assertRaises(record.CoordinationError) as caught:
            self.surface(fresh_result(mission_id=MISSION_Y))
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_MISMATCH)
        self.assertEqual(self.presenter.presented, [])


class AttentionAcknowledgeTests(unittest.TestCase):

    def setUp(self):
        self.document = store.default_document()
        self.mint = lambda: record.mint_id("ca")
        self.attention_id = attention.project(
            self.document, MISSION_X, DESTINATION_A, fresh_result(), NOW,
            self.mint).created[0]

    def record_(self):
        return self.document["attention"][self.attention_id]

    def acknowledge(self, result=None, now=NOW + 2, attention_id=None):
        return attention.acknowledge(
            self.document, attention_id or self.attention_id, context(),
            result if result is not None else fresh_result(), now)

    def test_acknowledgment_records_the_human_act_and_its_freshness(self):
        acked = self.acknowledge()
        self.assertEqual(acked["presentation"], record.PRESENTATION_ACKNOWLEDGED)
        self.assertEqual(acked["acknowledged_at"], NOW + 2)
        self.assertEqual(acked["acknowledged_by"], context().as_dict())
        self.assertEqual(acked["acknowledged_freshness"], record.FRESHNESS_FRESH)
        self.assertEqual(acked["acknowledged_observation_cursor"], "7")
        self.assertIsNone(acked["surfaced_at"])
        attention.validate_attention_records(self.document, "<doc>")

    def test_acknowledgment_never_needs_an_observation_but_records_truthfully(self):
        unavailable = observation.classify(
            observation.ObservationOutcome.unavailable("down"), NOW, MISSION_X)
        acked = self.acknowledge(unavailable)
        self.assertEqual(acked["presentation"], record.PRESENTATION_ACKNOWLEDGED)
        self.assertEqual(acked["acknowledged_freshness"], record.FRESHNESS_UNAVAILABLE)
        self.assertIsNone(acked["acknowledged_observation_cursor"])
        attention.validate_attention_records(self.document, "<doc>")

    def test_acknowledgment_from_surfaced_and_only_once(self):
        attention.surface(self.document, self.attention_id, fresh_result(),
                          RecordingPresenter(), NOW + 1, self.mint)
        acked = self.acknowledge()
        self.assertEqual(acked["presentation"], record.PRESENTATION_ACKNOWLEDGED)
        self.assertEqual(acked["surfaced_message_ref"], "out-1")
        with self.assertRaises(record.CoordinationError) as caught:
            self.acknowledge(now=NOW + 3)
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)

    def test_acknowledgment_resolves_nothing_and_authorizes_nothing(self):
        before = dict(self.record_())
        acked = dict(self.acknowledge())
        # Everything about the condition and the Mission binding is
        # unchanged; only the acknowledgment fields were written.
        for key in attention.ATTENTION_KEYS:
            if key in ("presentation", "acknowledged_at", "acknowledged_by",
                       "acknowledged_freshness", "acknowledged_observation_cursor",
                       "acknowledged_observation_point"):
                continue
            self.assertEqual(acked[key], before[key], key)
        self.assertEqual(acked["authority"], "none")
        # The blocker is still the blocker: the next projection of the
        # unchanged condition suppresses, it does not recreate or resolve.
        outcome = attention.project(self.document, MISSION_X, DESTINATION_A,
                                    fresh_result(), NOW + 3, self.mint)
        self.assertEqual(outcome.suppressed, [self.attention_id])
        self.assertEqual(outcome.resolved, [])
        self.assertEqual(self.record_()["presentation"],
                         record.PRESENTATION_ACKNOWLEDGED)

    def test_acknowledged_record_still_goes_obsolete_on_change(self):
        self.acknowledge()
        outcome = attention.project(
            self.document, MISSION_X, DESTINATION_A,
            fresh_result(conditions=(condition(detail="other"),)), NOW + 4, self.mint)
        self.assertEqual(outcome.obsoleted, [self.attention_id])
        self.assertEqual(self.record_()["presentation"], record.PRESENTATION_OBSOLETE)
        self.assertEqual(self.record_()["acknowledged_at"], NOW + 2)

    def test_terminal_records_never_move(self):
        attention.project(self.document, MISSION_X, DESTINATION_A,
                          fresh_result(conditions=()), NOW + 1, self.mint)
        self.assertEqual(self.record_()["presentation"], record.PRESENTATION_RESOLVED)
        with self.assertRaises(record.CoordinationError) as caught:
            self.acknowledge()
        self.assertEqual(caught.exception.problem, record.PROBLEM_ATTENTION_TERMINAL)
        with self.assertRaises(record.CoordinationError) as caught:
            attention.surface(self.document, self.attention_id, fresh_result(),
                              RecordingPresenter(), NOW + 5, self.mint)
        self.assertEqual(caught.exception.problem, record.PROBLEM_ATTENTION_TERMINAL)
        with self.assertRaises(record.CoordinationError) as caught:
            self.acknowledge(attention_id="ca-" + "0" * 32)
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)


class AttentionAggregationTests(unittest.TestCase):

    def test_pending_is_ordered_by_priority_then_time_then_id(self):
        document = store.default_document()
        ids = iter(["ca-" + ch * 32 for ch in "cba"])
        mint = lambda: next(ids)  # noqa: E731
        first = attention.project(
            document, MISSION_X, DESTINATION_A,
            fresh_result(conditions=(condition(), needs_human())), NOW, mint)
        second = attention.project(
            document, MISSION_Y, DESTINATION_A,
            result_of(fresh(MISSION_Y, conditions=(condition(
                kind=record.ATTENTION_RESULT_READY, key="result",
                detail="verified result", evidence_refs=()),))), NOW - 10, mint)
        pending = attention.pending(document, DESTINATION_A)
        self.assertEqual([r["condition_kind"] for r in pending],
                         [record.ATTENTION_NEEDS_HUMAN, record.ATTENTION_BLOCKED,
                          record.ATTENTION_RESULT_READY])
        self.assertEqual([r["priority"] for r in pending], [10, 30, 40])
        self.assertEqual(attention.pending(document, DESTINATION_B), [])
        summary = attention.aggregate(document, DESTINATION_A)
        self.assertEqual(summary["by_kind"], {
            record.ATTENTION_AUTHORIZATION_READY: 0, record.ATTENTION_BLOCKED: 1,
            record.ATTENTION_NEEDS_HUMAN: 1, record.ATTENTION_RESULT_READY: 1})
        self.assertEqual(summary["by_presentation"][record.PRESENTATION_PENDING], 3)
        self.assertEqual(summary["missions"], sorted([MISSION_X, MISSION_Y]))
        self.assertEqual(summary["authority"], "none")
        # Same priority and time: id breaks the tie deterministically.
        tie = store.default_document()
        ids = iter(["ca-" + "f" * 32, "ca-" + "a" * 32])
        attention.project(tie, MISSION_X, DESTINATION_A,
                          fresh_result(conditions=(
                              condition(key="k1"), condition(key="k2"))),
                          NOW, lambda: next(ids))
        self.assertEqual([r["attention_id"] for r in attention.pending(tie, DESTINATION_A)],
                         ["ca-" + "a" * 32, "ca-" + "f" * 32])
        self.assertEqual(first.created + second.created, ["ca-" + "c" * 32,
                                                          "ca-" + "b" * 32,
                                                          "ca-" + "a" * 32])


class AttentionRecordValidationTests(unittest.TestCase):

    def setUp(self):
        self.document = store.default_document()
        self.mint = lambda: record.mint_id("ca")
        self.attention_id = attention.project(
            self.document, MISSION_X, DESTINATION_A, fresh_result(), NOW,
            self.mint).created[0]
        self.base = self.document["attention"][self.attention_id]

    def test_priority_is_re_derived_and_states_are_coherent(self):
        for overrides in (
            dict(priority=10),
            dict(priority=None),
            dict(authority="delivery"),
            dict(condition_kind="REVIEW_FAILED"),
            dict(presentation="SHOWN"),
            dict(presentation=record.PRESENTATION_SURFACED),
            dict(presentation=record.PRESENTATION_SURFACED, surfaced_at=NOW + 1,
                 surfaced_message_ref="m", surfaced_freshness=record.FRESHNESS_STALE),
            dict(presentation=record.PRESENTATION_ACKNOWLEDGED),
            dict(presentation=record.PRESENTATION_ACKNOWLEDGED, acknowledged_at=NOW,
                 acknowledged_by=context().as_dict(),
                 acknowledged_freshness=record.FRESHNESS_UNAVAILABLE,
                 acknowledged_observation_cursor="7"),
            dict(presentation=record.PRESENTATION_OBSOLETE, closed_at=NOW,
                 closed_reason=record.CLOSED_CONDITION_CLEARED,
                 superseded_by="ca-" + "1" * 32),
            dict(presentation=record.PRESENTATION_OBSOLETE, closed_at=NOW,
                 closed_reason=record.CLOSED_CONDITION_CHANGED),
            dict(presentation=record.PRESENTATION_RESOLVED, closed_at=NOW,
                 closed_reason=record.CLOSED_CONDITION_CLEARED,
                 superseded_by="ca-" + "1" * 32),
            dict(surfaced_at=NOW + 1),
            dict(closed_at=NOW - 1, presentation=record.PRESENTATION_RESOLVED,
                 closed_reason=record.CLOSED_CONDITION_CLEARED),
            dict(revision=3),
            dict(authorization_digest_sha256=None),
            dict(destination={"transport": "grok_mcp"}),
            dict(mission_id="mq-" + "2" * 32),
        ):
            with self.assertRaises(record.CoordinationError, msg=overrides):
                attention.validate_attention(dict(self.base, **overrides), "a")
        with self.assertRaises(record.CoordinationError):
            attention.validate_attention(dict(self.base, extra=1), "a")

    def test_document_rules(self):
        duplicate = dict(self.base, attention_id="ca-" + "2" * 32)
        self.document["attention"][duplicate["attention_id"]] = duplicate
        with self.assertRaises(record.CoordinationError) as caught:
            attention.validate_attention_records(self.document, "<doc>")
        self.assertIn("one non-terminal", str(caught.exception))
        del self.document["attention"][duplicate["attention_id"]]
        self.document["attention"]["ca-" + "3" * 32] = self.base
        with self.assertRaises(record.CoordinationError):
            attention.validate_attention_records(self.document, "<doc>")
        del self.document["attention"]["ca-" + "3" * 32]
        # A successor must exist, share the identity, and not predate it.
        outcome = attention.project(
            self.document, MISSION_X, DESTINATION_A,
            fresh_result(conditions=(condition(detail="other"),)), NOW + 1, self.mint)
        attention.validate_attention_records(self.document, "<doc>")
        successor = self.document["attention"][outcome.created[0]]
        for change in (dict(destination=DESTINATION_B), dict(condition_key="else"),
                       dict(created_at=NOW - 1), dict(mission_id=MISSION_Y)):
            self.document["attention"][successor["attention_id"]] = dict(
                successor, **change)
            with self.assertRaises(record.CoordinationError, msg=change):
                attention.validate_attention_records(self.document, "<doc>")
        self.document["attention"][successor["attention_id"]] = successor
        self.document["attention"][self.attention_id]["superseded_by"] = (
            "ca-" + "9" * 32)
        with self.assertRaises(record.CoordinationError):
            attention.validate_attention_records(self.document, "<doc>")


    def test_f7_successor_cycles_are_refused(self):
        # Two obsolete records at the same timestamp, each superseding the
        # other: identity, timestamps and predecessor uniqueness all hold,
        # only cycle detection can refuse it.
        first = json.loads(json.dumps(self.base))
        second = dict(first, attention_id="ca-" + "2" * 32)
        for value, other in ((first, second), (second, first)):
            value.update(presentation=record.PRESENTATION_OBSOLETE, closed_at=NOW,
                         closed_reason=record.CLOSED_CONDITION_CHANGED,
                         superseded_by=other["attention_id"],
                         closed_observation_point=first["observation_point"])
        document = store.default_document()
        document["attention"][first["attention_id"]] = first
        document["attention"][second["attention_id"]] = second
        with self.assertRaises(record.CoordinationError) as caught:
            attention.validate_attention_records(document, "<doc>")
        self.assertIn("cycle", str(caught.exception))
        with self.assertRaises(store.CoordinationStoreError):
            store.validate_document(document, "<doc>")
        # A three-record cycle at the same timestamp is refused too.
        third = dict(first, attention_id="ca-" + "3" * 32,
                     superseded_by=first["attention_id"])
        first["superseded_by"] = second["attention_id"]
        second["superseded_by"] = third["attention_id"]
        document["attention"][third["attention_id"]] = third
        with self.assertRaises(record.CoordinationError):
            attention.validate_attention_records(document, "<doc>")


class AttentionRestartTests(unittest.TestCase):

    def test_reload_preserves_state_and_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            durable = store.CoordinationStore(os.path.join(tmp, "p"))
            document = durable.load()
            mint = lambda: record.mint_id("ca")  # noqa: E731
            created = attention.project(document, MISSION_X, DESTINATION_A,
                                        fresh_result(), NOW, mint).created[0]
            attention.surface(document, created, fresh_result(), RecordingPresenter(),
                              NOW + 1, mint)
            durable.save(document, expected_sequence=0)
            reloaded = durable.load()
            self.assertEqual(reloaded["attention"][created]["presentation"],
                             record.PRESENTATION_SURFACED)
            outcome = attention.project(reloaded, MISSION_X, DESTINATION_A,
                                        fresh_result(), NOW + 2, mint)
            self.assertEqual(outcome.suppressed, [created])
            self.assertEqual(len(reloaded["attention"]), 1)
            acked = attention.acknowledge(reloaded, created, context(), fresh_result(),
                                          NOW + 3)
            durable.save(reloaded, expected_sequence=1)
            self.assertEqual(durable.load()["attention"][created], acked)


# =====================================================================
# G. handoffs and participant rosters
# =====================================================================

COORDINATOR = record.PARTICIPANT_COORDINATOR
ENGINEERING = record.PARTICIPANT_ENGINEERING
RESEARCH = record.PARTICIPANT_RESEARCH
OPERATIONS = record.PARTICIPANT_OPERATIONS
ROSTER = [COORDINATOR, ENGINEERING, RESEARCH]


def cite(reference_id, level):
    return {"reference_id": reference_id, "level": level}


ACCEPTED_CITE = cite(EVIDENCE_1, record.REFERENCE_LEVEL_ACCEPTED)
RECORDED_CITE = cite(EVIDENCE_2, record.REFERENCE_LEVEL_RECORDED)
VALIDATED_CITE = cite(ARTIFACT_1, record.REFERENCE_LEVEL_VALIDATED)


def rostered(document=None, mission_id=MISSION_X, participants=ROSTER, now=NOW - 50):
    document = document if document is not None else store.default_document()
    handoff.set_roster(document, mission_id, list(participants),
                       fresh_result(mission_id=mission_id), now, context())
    return document


class HandoffMaker(object):
    """Builds handoffs against one document with deterministic ids."""

    def __init__(self, document=None):
        self.document = rostered(document)
        self.counter = 0

    def next_id(self):
        self.counter += 1
        return "ch-%032x" % self.counter

    def create(self, handoff_id=None, mission_id=MISSION_X, source=COORDINATOR,
               destination=ENGINEERING, purpose=record.PURPOSE_QUESTION,
               request_text="does this change the implementation?",
               evidence_refs=(ACCEPTED_CITE,), artifact_refs=(VALIDATED_CITE,),
               idempotency_key=None, parent_handoff_id=None, result=None,
               now=NOW, ctx=None):
        handoff_id = handoff_id or self.next_id()
        return handoff.create_handoff(
            self.document, handoff_id, mission_id, source, destination, purpose,
            request_text, list(evidence_refs), list(artifact_refs),
            idempotency_key or "key-" + handoff_id[-4:], parent_handoff_id,
            result if result is not None else fresh_result(mission_id=mission_id),
            now, ctx or context())

    def transition(self, handoff_id, status, actor, result=None, now=NOW + 10,
                   ctx=None):
        return handoff.transition(
            self.document, handoff_id, status, actor,
            result if result is not None else fresh_result(), now, ctx or context())


class RosterTests(unittest.TestCase):

    def test_roster_shape_and_eligibility(self):
        document = rostered()
        roster = document["participants"][MISSION_X]
        self.assertEqual(tuple(roster), handoff.ROSTER_KEYS)
        self.assertEqual(roster["participants"], sorted(ROSTER))
        self.assertEqual(roster["revision"], 2)
        self.assertEqual(roster["observation_point"],
                         observation.observation_point(fresh()))
        self.assertEqual(roster["authority"], "none")
        handoff.validate_rosters(document, "<doc>")
        handoff.require_eligible(document, MISSION_X, ENGINEERING, "destination")
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.require_eligible(document, MISSION_X, OPERATIONS, "destination")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_PARTICIPANT_INELIGIBLE)
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.require_eligible(document, MISSION_Y, ENGINEERING, "destination")
        self.assertEqual(caught.exception.problem, record.PROBLEM_ROSTER_MISSING)
        self.assertEqual(handoff.MAX_PARTICIPANTS_PER_MISSION, 16)

    def test_roster_refusals(self):
        document = store.default_document()
        for participants, problem in (
            ([], record.PROBLEM_BAD_VALUE),
            (["MARKETING"], record.PROBLEM_BAD_VALUE),
            ([RESEARCH, COORDINATOR], record.PROBLEM_BAD_VALUE),
            ([COORDINATOR, COORDINATOR], record.PROBLEM_BAD_VALUE),
        ):
            with self.assertRaises(record.CoordinationError) as caught:
                handoff.set_roster(document, MISSION_X, participants, fresh_result(),
                                   NOW, context())
            self.assertEqual(caught.exception.problem, problem, participants)
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.set_roster(document, MISSION_X, ROSTER,
                               fresh_result(mission_id=MISSION_Y), NOW, context())
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_MISMATCH)
        unavailable = observation.classify(
            observation.ObservationOutcome.unavailable("down"), NOW, MISSION_X)
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.set_roster(document, MISSION_X, ROSTER, unavailable, NOW, context())
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_UNAVAILABLE)
        self.assertEqual(document["participants"], {})
        roster = rostered()["participants"][MISSION_X]
        for overrides in (dict(authority="delivery"), dict(revision=3),
                          dict(mission_id="mq-" + "2" * 32), dict(participants=[]),
                          dict(registered_by={"transport": "x"})):
            with self.assertRaises(record.CoordinationError, msg=overrides):
                handoff.validate_roster(dict(roster, **overrides), "r")
        document = rostered()
        document["participants"][MISSION_Y] = roster
        with self.assertRaises(record.CoordinationError):
            handoff.validate_rosters(document, "<doc>")

    def test_roster_may_grow_but_never_drops_a_referenced_participant(self):
        maker = HandoffMaker()
        maker.create()
        grown = handoff.set_roster(maker.document, MISSION_X,
                                   sorted(ROSTER + [OPERATIONS]), fresh_result(),
                                   NOW + 1, context())
        self.assertIn(OPERATIONS, grown["participants"])
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.set_roster(maker.document, MISSION_X, [COORDINATOR, RESEARCH],
                               fresh_result(), NOW + 2, context())
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_PARTICIPANT_INELIGIBLE)
        self.assertIn(ENGINEERING, maker.document["participants"][MISSION_X][
            "participants"])


class HandoffCreationTests(unittest.TestCase):

    def setUp(self):
        self.maker = HandoffMaker()

    def test_handoff_records_context_and_request_only(self):
        created = self.maker.create()
        self.assertEqual(tuple(created), handoff.HANDOFF_KEYS)
        self.assertEqual(created["handoff_id"], "ch-%032x" % 1)
        self.assertEqual(created["mission_id"], MISSION_X)
        self.assertEqual(created["revision"], 2)
        self.assertEqual(created["proposal_digest_sha256"], HEX_A)
        self.assertEqual((created["source"], created["destination"]),
                         (COORDINATOR, ENGINEERING))
        self.assertEqual(created["purpose"], record.PURPOSE_QUESTION)
        self.assertEqual(created["evidence_refs"], [ACCEPTED_CITE])
        self.assertEqual(created["artifact_refs"], [VALIDATED_CITE])
        self.assertEqual(created["status"], record.HANDOFF_OPEN)
        self.assertEqual(created["forward_depth"], 0)
        self.assertIsNone(created["parent_handoff_id"])
        self.assertIsNone(created["parent_content_digest_sha256"])
        self.assertEqual(created["content_digest_sha256"],
                         handoff.content_digest(created))
        self.assertEqual(len(created["transitions"]), 1)
        self.assertEqual(tuple(created["transitions"][0]), handoff.TRANSITION_KEYS)
        self.assertEqual(created["transitions"][0]["status"], record.HANDOFF_OPEN)
        self.assertEqual(created["transitions"][0]["actor"], COORDINATOR)
        self.assertEqual(created["created_at"], NOW)
        self.assertEqual(created["updated_at"], NOW)
        self.assertEqual(created["observation_point"],
                         observation.observation_point(fresh()))
        self.assertEqual(created["provenance"]["observed_revision"], 2)
        # Transfers context and request only; never ownership, authority
        # or verified-result status. The shape cannot say otherwise.
        self.assertEqual(created["transfers"], "context_and_request_only")
        self.assertEqual(created["authority"], "none")
        for key in created:
            for forbidden in ("owner", "authoriz", "permission", "verified"):
                self.assertNotIn(forbidden, key)
        for value, problem in (("ownership", record.PROBLEM_AUTHORITY_CLAIM),
                               ("verified_result", record.PROBLEM_AUTHORITY_CLAIM),
                               (None, record.PROBLEM_AUTHORITY_CLAIM)):
            with self.assertRaises(record.CoordinationError) as caught:
                handoff.validate_handoff(dict(created, transfers=value), "h")
            self.assertEqual(caught.exception.problem, problem)
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.validate_handoff(dict(created, authority="execution"), "h")
        self.assertEqual(caught.exception.problem, record.PROBLEM_AUTHORITY_CLAIM)
        handoff.validate_handoffs(self.maker.document, "<doc>")

    def test_references_are_cited_only_at_the_proven_level(self):
        # Presence proves RECORDED; ACCEPTED and VALIDATED need provenance.
        self.maker.create(evidence_refs=(RECORDED_CITE,), artifact_refs=())
        self.maker.create(evidence_refs=(cite(EVIDENCE_1, record.REFERENCE_LEVEL_RECORDED),
                                         RECORDED_CITE),
                          artifact_refs=(cite(ARTIFACT_1, record.REFERENCE_LEVEL_RECORDED),))
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(evidence_refs=(cite(EVIDENCE_2,
                                                  record.REFERENCE_LEVEL_ACCEPTED),))
        self.assertEqual(caught.exception.problem, record.PROBLEM_REFERENCE_NOT_PROVEN)
        bare = fresh_result(artifact_refs=(artifact_ref(receipt=None),))
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(result=bare, artifact_refs=(VALIDATED_CITE,))
        self.assertEqual(caught.exception.problem, record.PROBLEM_REFERENCE_NOT_PROVEN)
        unavailable = fresh_result(artifact_refs=(artifact_ref(
            receipt=receipt(available=False)),))
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(result=unavailable, artifact_refs=(VALIDATED_CITE,))
        self.assertEqual(caught.exception.problem, record.PROBLEM_REFERENCE_NOT_PROVEN)
        # Mission-local: a reference the observation does not carry, or one
        # belonging to another Mission, is refused.
        for foreign in (cite("mv-" + "f" * 32, record.REFERENCE_LEVEL_RECORDED),
                        cite("mf-" + "f" * 32, record.REFERENCE_LEVEL_RECORDED)):
            with self.assertRaises(record.CoordinationError) as caught:
                self.maker.create(evidence_refs=(foreign,) if foreign[
                    "reference_id"].startswith("mv") else (),
                    artifact_refs=(foreign,) if foreign[
                        "reference_id"].startswith("mf") else ())
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_REFERENCE_NOT_MISSION_LOCAL)
        other = "mv-" + "9" * 32
        rostered(self.maker.document, MISSION_Y)
        self.maker.create(mission_id=MISSION_Y,
                          result=result_of(fresh(MISSION_Y, evidence_refs=(
                              evidence_ref(evidence_id=other),),
                              conditions=(condition(evidence_refs=(
                                  evidence_ref(evidence_id=other),)),))),
                          evidence_refs=(cite(other, record.REFERENCE_LEVEL_ACCEPTED),),
                          artifact_refs=())
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(evidence_refs=(cite(other,
                                                  record.REFERENCE_LEVEL_RECORDED),),
                              artifact_refs=())
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_REFERENCE_NOT_MISSION_LOCAL)
        # Shape: sorted, unique, level appropriate to the kind.
        for refs, problem in (
            ([RECORDED_CITE, ACCEPTED_CITE], record.PROBLEM_BAD_VALUE),
            ([ACCEPTED_CITE, ACCEPTED_CITE], record.PROBLEM_BAD_VALUE),
            ([cite(EVIDENCE_1, record.REFERENCE_LEVEL_VALIDATED)],
             record.PROBLEM_BAD_VALUE),
            ([cite(ARTIFACT_1, record.REFERENCE_LEVEL_RECORDED)],
             record.PROBLEM_ID_GRAMMAR),
            ([{"reference_id": EVIDENCE_1}], record.PROBLEM_MISSING_KEY),
            ([cite(EVIDENCE_1, record.REFERENCE_LEVEL_ACCEPTED)] * 0 + [
                cite("mv-%032x" % i, record.REFERENCE_LEVEL_RECORDED)
                for i in range(record.MAX_REFERENCE_LIST + 1)],
             record.PROBLEM_TOO_LARGE),
        ):
            with self.assertRaises(record.CoordinationError, msg=refs) as caught:
                self.maker.create(evidence_refs=refs, artifact_refs=())
            self.assertEqual(caught.exception.problem, problem, refs)

    def test_eligibility_and_observation_refusals(self):
        cases = (
            (dict(source=OPERATIONS), record.PROBLEM_PARTICIPANT_INELIGIBLE),
            (dict(destination=OPERATIONS), record.PROBLEM_PARTICIPANT_INELIGIBLE),
            (dict(destination=COORDINATOR), record.PROBLEM_PARTICIPANT_INELIGIBLE),
            (dict(destination="ROBOT"), record.PROBLEM_BAD_VALUE),
            (dict(mission_id=MISSION_Y, result=fresh_result(mission_id=MISSION_Y)),
             record.PROBLEM_ROSTER_MISSING),
            (dict(result=fresh_result(mission_id=MISSION_Y)),
             record.PROBLEM_MISSION_MISMATCH),
            (dict(purpose="GOSSIP"), record.PROBLEM_BAD_VALUE),
            (dict(request_text=""), record.PROBLEM_BAD_VALUE),
            (dict(request_text="x" * (record.MAX_REQUEST_TEXT_CHARS + 1)),
             record.PROBLEM_TOO_LARGE),
            (dict(idempotency_key="Bad Key"), record.PROBLEM_BAD_VALUE),
            (dict(handoff_id="ca-" + "1" * 32), record.PROBLEM_ID_GRAMMAR),
            (dict(result=observation.classify(
                observation.ObservationOutcome.unavailable("down"), NOW, MISSION_X)),
             record.PROBLEM_OBSERVATION_UNAVAILABLE),
            (dict(result=observation.classify(
                observation.ObservationOutcome.absent(), NOW, MISSION_X)),
             record.PROBLEM_MISSION_NOT_OBSERVED),
            (dict(result=result_of(fresh(observed_at=NOW + 1))),
             record.PROBLEM_OBSERVATION_INCONSISTENT),
            (dict(result=observation.classify(
                observation.ObservationOutcome.observed(fresh()), NOW, MISSION_X,
                observation.HighWaterMark(revision=3, cursor="1", point=None))),
             record.PROBLEM_OBSERVATION_STALE),
        )
        for overrides, problem in cases:
            with self.assertRaises(record.CoordinationError, msg=overrides) as caught:
                self.maker.create(**overrides)
            self.assertEqual(caught.exception.problem, problem, overrides)
        self.assertEqual(self.maker.document["handoffs"], {})

    def test_idempotency_and_id_reuse(self):
        first = self.maker.create(idempotency_key="ask-once")
        again = self.maker.create(idempotency_key="ask-once", now=NOW + 5)
        self.assertEqual(again, first)
        self.assertEqual(len(self.maker.document["handoffs"]), 1)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(idempotency_key="ask-once", request_text="different")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_IDEMPOTENCY_CONFLICT)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(handoff_id=first["handoff_id"], idempotency_key="other")
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_IDEMPOTENCY_CONFLICT)
        self.assertEqual(len(self.maker.document["handoffs"]), 1)
        # The same key on another Mission is a different effect.
        rostered(self.maker.document, MISSION_Y)
        other = self.maker.create(mission_id=MISSION_Y, idempotency_key="ask-once",
                                  evidence_refs=(), artifact_refs=())
        self.assertNotEqual(other["handoff_id"], first["handoff_id"])
        handoff.validate_handoffs(self.maker.document, "<doc>")

    def test_a_live_identical_request_is_a_duplicate_effect_whatever_its_key(self):
        # R-4a: a fresh key does not license a second live identical request.
        first = self.maker.create(idempotency_key="first")
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(idempotency_key="second")
        self.assertEqual(caught.exception.problem, record.PROBLEM_DUPLICATE_EFFECT)
        self.assertEqual(record.PROBLEM_DUPLICATE_EFFECT,
                         "coordination_duplicate_effect")
        self.assertEqual(len(self.maker.document["handoffs"]), 1)
        # Different content under a fresh key is a distinct effect.
        distinct = self.maker.create(idempotency_key="second",
                                     request_text="a different question")
        self.assertNotEqual(distinct["handoff_id"], first["handoff_id"])
        # Once the earlier one is terminal, an identical request is a
        # legitimate new effect.
        self.maker.transition(first["handoff_id"], record.HANDOFF_DECLINED,
                              ENGINEERING)
        renewed = self.maker.create(idempotency_key="third", now=NOW + 20)
        self.assertNotEqual(renewed["handoff_id"], first["handoff_id"])
        self.assertEqual(renewed["content_digest_sha256"],
                         first["content_digest_sha256"])
        handoff.validate_handoffs(self.maker.document, "<doc>")
        # The loader refuses two live identical requests too.
        broken = json.loads(json.dumps(self.maker.document))
        broken["handoffs"][first["handoff_id"]]["status"] = record.HANDOFF_OPEN
        broken["handoffs"][first["handoff_id"]]["transitions"] = (
            broken["handoffs"][first["handoff_id"]]["transitions"][:1])
        broken["handoffs"][first["handoff_id"]]["updated_at"] = NOW
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.validate_handoffs(broken, "<doc>")
        self.assertEqual(caught.exception.problem, record.PROBLEM_DUPLICATE_EFFECT)


class HandoffForwardingTests(unittest.TestCase):

    def setUp(self):
        self.maker = HandoffMaker()
        self.root = self.maker.create(source=COORDINATOR, destination=ENGINEERING)

    def forward(self, parent, source, destination, **overrides):
        return self.maker.create(source=source, destination=destination,
                                 parent_handoff_id=parent["handoff_id"],
                                 request_text="forwarded", **overrides)

    def test_a_forward_is_one_proven_path(self):
        child = self.forward(self.root, ENGINEERING, RESEARCH)
        self.assertEqual(child["parent_handoff_id"], self.root["handoff_id"])
        self.assertEqual(child["parent_content_digest_sha256"],
                         self.root["content_digest_sha256"])
        self.assertEqual(child["forward_depth"], 1)
        self.assertEqual(child["revision"], self.root["revision"])
        self.assertEqual(handoff.chain(self.maker.document, child["handoff_id"]),
                         [self.root["handoff_id"], child["handoff_id"]])
        self.assertEqual(handoff.chain_participants(self.maker.document,
                                                    child["handoff_id"]),
                         [COORDINATOR, ENGINEERING, RESEARCH])
        handoff.validate_handoffs(self.maker.document, "<doc>")

    def test_only_the_addressee_may_forward(self):
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(self.root, COORDINATOR, RESEARCH)
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_FORWARD_CONTINUITY)
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(self.root, RESEARCH, COORDINATOR)
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_FORWARD_CONTINUITY)

    def test_the_root_source_is_in_the_loop_set(self):
        # ENGINEERING forwarding back to COORDINATOR repeats no earlier
        # DESTINATION, but the root source is on the path: a loop.
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(self.root, ENGINEERING, COORDINATOR)
        self.assertEqual(caught.exception.problem, record.PROBLEM_FORWARDING_LOOP)
        child = self.forward(self.root, ENGINEERING, RESEARCH)
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(child, RESEARCH, ENGINEERING)
        self.assertEqual(caught.exception.problem, record.PROBLEM_FORWARDING_LOOP)
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(child, RESEARCH, RESEARCH)
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_PARTICIPANT_INELIGIBLE)

    def test_depth_is_bounded(self):
        names = [COORDINATOR, ENGINEERING, RESEARCH, OPERATIONS,
                 record.PARTICIPANT_RELEASE, record.PARTICIPANT_BROWSER_QA,
                 record.PARTICIPANT_INCIDENT_RECOVERY]
        handoff.set_roster(self.maker.document, MISSION_X, sorted(names),
                           fresh_result(), NOW, context())
        current = self.root
        for depth in range(1, handoff.MAX_HANDOFF_FORWARD_DEPTH + 1):
            current = self.forward(current, names[depth], names[depth + 1])
            self.assertEqual(current["forward_depth"], depth)
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(current, names[handoff.MAX_HANDOFF_FORWARD_DEPTH + 1],
                         names[handoff.MAX_HANDOFF_FORWARD_DEPTH + 2])
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_FORWARD_DEPTH_EXCEEDED)
        self.assertEqual(handoff.MAX_HANDOFF_FORWARD_DEPTH, 4)
        handoff.validate_handoffs(self.maker.document, "<doc>")

    def test_revision_compatibility_is_exact(self):
        moved = fresh_result(current_revision=3, state_cursor="12",
                             conditions=(condition(revision=3),))
        with self.assertRaises(record.CoordinationError) as caught:
            self.forward(self.root, ENGINEERING, RESEARCH, result=moved)
        self.assertEqual(caught.exception.problem, record.PROBLEM_HANDOFF_STALE)
        self.assertEqual(len(self.maker.document["handoffs"]), 1)

    def test_a_declined_or_withdrawn_parent_cannot_be_forwarded(self):
        # Ruling 2: OPEN, ACCEPTED and ANSWERED parents forward (passing an
        # answer along is legitimate); DECLINED and WITHDRAWN do not.
        for status, actor, allowed in (
            (record.HANDOFF_ACCEPTED, ENGINEERING, True),
            (record.HANDOFF_ANSWERED, ENGINEERING, True),
            (record.HANDOFF_DECLINED, ENGINEERING, False),
            (record.HANDOFF_WITHDRAWN, COORDINATOR, False),
        ):
            maker = HandoffMaker()
            root = maker.create()
            if status == record.HANDOFF_ANSWERED:
                maker.transition(root["handoff_id"], record.HANDOFF_ACCEPTED,
                                 ENGINEERING)
            maker.transition(root["handoff_id"], status, actor, now=NOW + 20)
            if allowed:
                child = maker.create(source=ENGINEERING, destination=RESEARCH,
                                     parent_handoff_id=root["handoff_id"],
                                     request_text="forwarded", now=NOW + 30)
                self.assertEqual(child["forward_depth"], 1)
                handoff.validate_handoffs(maker.document, "<doc>")
            else:
                with self.assertRaises(record.CoordinationError, msg=status) as caught:
                    maker.create(source=ENGINEERING, destination=RESEARCH,
                                 parent_handoff_id=root["handoff_id"],
                                 request_text="forwarded", now=NOW + 30)
                self.assertEqual(caught.exception.problem,
                                 record.PROBLEM_INVALID_TRANSITION)
                self.assertIn(status, str(caught.exception))
                self.assertEqual(len(maker.document["handoffs"]), 1)

    def test_parent_must_exist_in_the_same_mission(self):
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(source=ENGINEERING, destination=RESEARCH,
                              parent_handoff_id="ch-" + "e" * 32)
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)
        rostered(self.maker.document, MISSION_Y,
                 participants=[COORDINATOR, ENGINEERING, RESEARCH])
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(mission_id=MISSION_Y, source=ENGINEERING,
                              destination=RESEARCH,
                              parent_handoff_id=self.root["handoff_id"],
                              evidence_refs=(), artifact_refs=(),
                              result=fresh_result(mission_id=MISSION_Y))
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_MISMATCH)

    def test_chain_rules_are_re_validated_on_load(self):
        child = self.forward(self.root, ENGINEERING, RESEARCH)
        document = self.maker.document
        handoffs = document["handoffs"]
        for change in (
            dict(parent_content_digest_sha256=HEX_B),
            dict(source=COORDINATOR),
            dict(destination=COORDINATOR),
            dict(forward_depth=2),
            dict(revision=3),
            dict(parent_handoff_id="ch-" + "e" * 32),
            dict(mission_id=MISSION_Y),
        ):
            document["handoffs"] = json.loads(json.dumps(handoffs))
            document["handoffs"][child["handoff_id"]].update(change)
            digest = handoff.content_digest(document["handoffs"][child["handoff_id"]])
            document["handoffs"][child["handoff_id"]]["content_digest_sha256"] = digest
            with self.assertRaises(record.CoordinationError, msg=change):
                handoff.validate_handoffs(document, "<doc>")
        document["handoffs"] = handoffs
        handoff.validate_handoffs(document, "<doc>")
        # A record whose content digest does not recompute is refused
        # before any chain rule is consulted.
        tampered = json.loads(json.dumps(handoffs))
        tampered[child["handoff_id"]]["request_text"] = "altered"
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.validate_handoff(tampered[child["handoff_id"]], "h")
        self.assertEqual(caught.exception.problem, record.PROBLEM_BAD_VALUE)


class HandoffTransitionTests(unittest.TestCase):

    def setUp(self):
        self.maker = HandoffMaker()
        self.open = self.maker.create()
        self.handoff_id = self.open["handoff_id"]

    def current(self):
        return self.maker.document["handoffs"][self.handoff_id]

    def test_legal_transitions_by_the_right_participant(self):
        accepted = self.maker.transition(self.handoff_id, record.HANDOFF_ACCEPTED,
                                         ENGINEERING)
        self.assertEqual(accepted["status"], record.HANDOFF_ACCEPTED)
        self.assertEqual(accepted["updated_at"], NOW + 10)
        self.assertEqual([t["status"] for t in accepted["transitions"]],
                         [record.HANDOFF_OPEN, record.HANDOFF_ACCEPTED])
        self.assertEqual(accepted["transitions"][-1]["actor"], ENGINEERING)
        self.assertEqual(accepted["transitions"][-1]["observation_point"],
                         observation.observation_point(fresh()))
        answered = self.maker.transition(self.handoff_id, record.HANDOFF_ANSWERED,
                                         ENGINEERING, now=NOW + 20)
        self.assertEqual(answered["status"], record.HANDOFF_ANSWERED)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.transition(self.handoff_id, record.HANDOFF_WITHDRAWN,
                                  COORDINATOR, now=NOW + 30)
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)
        handoff.validate_handoffs(self.maker.document, "<doc>")
        # Context and request only: nothing about the transition confers
        # ownership, authority or a verified result.
        self.assertEqual(answered["transfers"], "context_and_request_only")
        self.assertEqual(answered["authority"], "none")

    def test_each_status_has_its_actor(self):
        for status, wrong in ((record.HANDOFF_ACCEPTED, COORDINATOR),
                              (record.HANDOFF_DECLINED, COORDINATOR),
                              (record.HANDOFF_WITHDRAWN, ENGINEERING),
                              (record.HANDOFF_ACCEPTED, RESEARCH)):
            with self.assertRaises(record.CoordinationError, msg=status) as caught:
                self.maker.transition(self.handoff_id, status, wrong)
            self.assertEqual(caught.exception.problem,
                             record.PROBLEM_PARTICIPANT_INELIGIBLE)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.transition(self.handoff_id, record.HANDOFF_ANSWERED, ENGINEERING)
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)
        with self.assertRaises(record.CoordinationError):
            self.maker.transition(self.handoff_id, "LOST", ENGINEERING)
        self.assertEqual(self.current()["status"], record.HANDOFF_OPEN)
        declined = self.maker.transition(self.handoff_id, record.HANDOFF_DECLINED,
                                         ENGINEERING)
        self.assertEqual(declined["status"], record.HANDOFF_DECLINED)
        withdrawn_id = self.maker.create(idempotency_key="second")["handoff_id"]
        self.assertEqual(self.maker.transition(withdrawn_id, record.HANDOFF_WITHDRAWN,
                                               COORDINATOR)["status"],
                         record.HANDOFF_WITHDRAWN)

    def test_every_transition_re_validates_under_a_fresh_observation(self):
        cases = (
            (observation.classify(observation.ObservationOutcome.unavailable("down"),
                                  NOW, MISSION_X), record.PROBLEM_OBSERVATION_UNAVAILABLE),
            (observation.classify(observation.ObservationOutcome.absent(), NOW,
                                  MISSION_X), record.PROBLEM_MISSION_NOT_OBSERVED),
            (fresh_result(mission_id=MISSION_Y), record.PROBLEM_MISSION_MISMATCH),
            (fresh_result(current_revision=3, state_cursor="12",
                          conditions=(condition(revision=3),)),
             record.PROBLEM_HANDOFF_STALE),
            # The accepted evidence lost its acceptance: the citation is no
            # longer proven, so the transition refuses.
            (result_of(fresh(evidence_refs=(
                evidence_ref(accepted=False, acceptance_digest_sha256=None,
                             accepted_at=None), recorded_evidence_ref()),
                conditions=())), record.PROBLEM_REFERENCE_NOT_PROVEN),
            # The artifact is no longer Mission-local at all.
            (result_of(fresh(artifact_refs=())),
             record.PROBLEM_REFERENCE_NOT_MISSION_LOCAL),
        )
        for result, problem in cases:
            with self.assertRaises(record.CoordinationError, msg=problem) as caught:
                self.maker.transition(self.handoff_id, record.HANDOFF_ACCEPTED,
                                      ENGINEERING, result=result)
            self.assertEqual(caught.exception.problem, problem)
            self.assertEqual(self.current()["status"], record.HANDOFF_OPEN)
            self.assertEqual(len(self.current()["transitions"]), 1)

    def test_f4_a_changed_proposal_at_the_same_revision_is_refused(self):
        # Same revision number, different proposal digest: the complete
        # proposal binding no longer holds. Transitions and forwards refuse.
        changed = fresh_result(state_cursor="8", proposal_digest_sha256=HEX_B)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.transition(self.handoff_id, record.HANDOFF_ACCEPTED, ENGINEERING,
                                  result=changed)
        self.assertEqual(caught.exception.problem, record.PROBLEM_HANDOFF_STALE)
        self.assertIn("proposal", str(caught.exception))
        self.assertEqual(self.current()["status"], record.HANDOFF_OPEN)
        with self.assertRaises(record.CoordinationError) as caught:
            self.maker.create(source=ENGINEERING, destination=RESEARCH,
                              parent_handoff_id=self.handoff_id,
                              request_text="forwarded", result=changed)
        self.assertEqual(caught.exception.problem, record.PROBLEM_HANDOFF_STALE)
        self.assertEqual(len(self.maker.document["handoffs"]), 1)
        # A stored child bound to a different proposal than its parent
        # refuses on load.
        child = self.maker.create(source=ENGINEERING, destination=RESEARCH,
                                  parent_handoff_id=self.handoff_id,
                                  request_text="forwarded")
        broken = json.loads(json.dumps(self.maker.document))
        for key in ("proposal_digest_sha256",):
            broken["handoffs"][child["handoff_id"]][key] = HEX_B
            broken["handoffs"][child["handoff_id"]]["observation_point"][key] = HEX_B
            for entry in broken["handoffs"][child["handoff_id"]]["transitions"]:
                entry["observation_point"][key] = HEX_B
            broken["handoffs"][child["handoff_id"]]["provenance"]
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.validate_handoffs(broken, "<doc>")
        self.assertEqual(caught.exception.problem, record.PROBLEM_HANDOFF_STALE)

    def test_f5_persisted_transitions_are_re_proven_on_load(self):
        self.maker.transition(self.handoff_id, record.HANDOFF_ACCEPTED, ENGINEERING)
        good = json.loads(json.dumps(self.current()))
        handoff.validate_handoff(good, "h")

        def tampered(mutate):
            value = json.loads(json.dumps(good))
            mutate(value)
            return value

        cases = {
            "actor of ACCEPTED is not the destination": tampered(
                lambda v: v["transitions"][1].__setitem__("actor", RESEARCH)),
            "actor of ACCEPTED is the source": tampered(
                lambda v: v["transitions"][1].__setitem__("actor", COORDINATOR)),
            "initial OPEN actor is not the source": tampered(
                lambda v: v["transitions"][0].__setitem__("actor", ENGINEERING)),
            "transition observed at another revision": tampered(
                lambda v: v["transitions"][1]["observation_point"].__setitem__(
                    "revision", 3)),
            "transition observed at another proposal": tampered(
                lambda v: v["transitions"][1]["observation_point"].__setitem__(
                    "proposal_digest_sha256", HEX_B)),
            "transition cursor moved backward": tampered(
                lambda v: v["transitions"][1]["observation_point"].__setitem__(
                    "cursor", "1")),
            "initial point differs from the record's": tampered(
                lambda v: v["transitions"][0]["observation_point"].__setitem__(
                    "cursor", "6")),
        }
        for label, value in cases.items():
            with self.assertRaises(record.CoordinationError, msg=label):
                handoff.validate_handoff(value, "h")
        # A later transition at a later cursor is legitimate.
        later = fresh_result(state_cursor="9")
        answered = self.maker.transition(self.handoff_id, record.HANDOFF_ANSWERED,
                                         ENGINEERING, result=later, now=NOW + 20)
        self.assertEqual(answered["transitions"][-1]["observation_point"]["cursor"], "9")
        handoff.validate_handoffs(self.maker.document, "<doc>")

    def test_transition_history_is_bounded_and_coherent(self):
        self.assertEqual(handoff.MAX_HANDOFF_TRANSITIONS, 8)
        value = json.loads(json.dumps(self.open))
        for overrides in (
            dict(status=record.HANDOFF_ACCEPTED),
            dict(transitions=[]),
            dict(transitions=value["transitions"] * (handoff.MAX_HANDOFF_TRANSITIONS + 1)),
            dict(updated_at=NOW - 1),
            dict(created_at=NOW + 1),
            dict(forward_depth=1),
            dict(parent_content_digest_sha256=HEX_A),
        ):
            with self.assertRaises(record.CoordinationError, msg=overrides):
                handoff.validate_handoff(dict(value, **overrides), "h")
        bad_first = json.loads(json.dumps(value))
        bad_first["transitions"][0]["status"] = record.HANDOFF_ACCEPTED
        bad_first["status"] = record.HANDOFF_ACCEPTED
        with self.assertRaises(record.CoordinationError):
            handoff.validate_handoff(bad_first, "h")
        # A malformed second OPEN entry is a structured refusal, not a
        # raw KeyError (review round 2, non-blocking item).
        second_open = json.loads(json.dumps(value))
        second_open["transitions"].append(dict(second_open["transitions"][0]))
        with self.assertRaises(record.CoordinationError) as caught:
            handoff.validate_handoff(second_open, "h")
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)
        with self.assertRaises(record.CoordinationError):
            handoff.validate_handoff(dict(value, extra=1), "h")


class HandoffReloadTests(unittest.TestCase):

    def test_identity_and_chain_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            durable = store.CoordinationStore(os.path.join(tmp, "p"))
            maker = HandoffMaker(durable.load())
            root = maker.create()
            child = maker.create(source=ENGINEERING, destination=RESEARCH,
                                 parent_handoff_id=root["handoff_id"],
                                 request_text="forwarded")
            maker.transition(root["handoff_id"], record.HANDOFF_ACCEPTED, ENGINEERING)
            durable.save(maker.document, expected_sequence=0)
            reloaded = durable.load()
            self.assertEqual(reloaded["handoffs"][root["handoff_id"]],
                             maker.document["handoffs"][root["handoff_id"]])
            self.assertEqual(reloaded["handoffs"][child["handoff_id"]], child)
            self.assertEqual(reloaded["participants"][MISSION_X],
                             maker.document["participants"][MISSION_X])
            # Same idempotency key after reload: the same record, no effect.
            again = handoff.create_handoff(
                reloaded, "ch-" + "9" * 32, MISSION_X, COORDINATOR, ENGINEERING,
                record.PURPOSE_QUESTION, "does this change the implementation?",
                [ACCEPTED_CITE], [VALIDATED_CITE], root["idempotency_key"], None,
                fresh_result(), NOW + 100, context())
            self.assertEqual(again["handoff_id"], root["handoff_id"])
            self.assertEqual(len(reloaded["handoffs"]), 2)
            # A tampered chain link on disk refuses the whole document.
            broken = json.loads(json.dumps(reloaded))
            broken["handoffs"][child["handoff_id"]]["source"] = COORDINATOR
            # Keep the record self-consistent (its own OPEN actor is its
            # source) so the CHAIN rule is what refuses.
            broken["handoffs"][child["handoff_id"]]["transitions"][0]["actor"] = (
                COORDINATOR)
            digest = handoff.content_digest(broken["handoffs"][child["handoff_id"]])
            broken["handoffs"][child["handoff_id"]]["content_digest_sha256"] = digest
            with open(durable.path, "w", encoding="utf-8") as handle:
                json.dump(broken, handle)
            with self.assertRaises(store.CoordinationStoreError) as caught:
                durable.load()
            self.assertIn(record.PROBLEM_FORWARD_CONTINUITY, str(caught.exception))


# =====================================================================
# H. service: the load-modify-save cycles, restart, conflicts, freshness
# =====================================================================


class ScriptedSource(observation.MissionObservationSource):
    """Hermetic, read-only: answers per Mission from a mutable table so a
    test can move a Mission between calls. Records every call."""

    def __init__(self, **answers):
        self.answers = dict(answers)
        self.calls = []

    def observe(self, mission_id):
        self.calls.append(mission_id)
        answer = self.answers.get(mission_id)
        if answer is None:
            return observation.ObservationOutcome.absent()
        if isinstance(answer, observation.ObservationOutcome):
            return answer
        return observation.ObservationOutcome.observed(answer)


class Clock(object):

    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class ServiceHarness(object):

    def __init__(self, directory, source=None, clock=None, lanes=(
            record.LANE_ENGINEERING,)):
        self.directory = directory
        self.source = source if source is not None else ScriptedSource(
            **{MISSION_X: fresh(MISSION_X), MISSION_Y: fresh(MISSION_Y)})
        self.clock = clock or Clock()
        self.counter = 0
        self.service = service.CoordinationService(
            store.CoordinationStore(directory), self.clock, self.source,
            frozenset(lanes), mint_id=self.mint)

    def mint(self, prefix):
        self.counter += 1
        return "%s-%032x" % (prefix, self.counter)

    def reopen(self):
        """A fresh service over the same directory: a restart."""
        return ServiceHarness(self.directory, self.source, self.clock)


class ServiceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.harness = ServiceHarness(os.path.join(self.tmp.name, "p"))
        self.svc = self.harness.service
        self.source = self.harness.source
        self.clock = self.harness.clock

    def tearDown(self):
        self.tmp.cleanup()

    def move(self, mission_id=MISSION_X, **overrides):
        """The source now shows ``mission_id`` observed at the clock."""
        overrides.setdefault("observed_at", self.clock.now)
        self.source.answers[mission_id] = fresh(mission_id, **overrides)

    # -- construction and the import boundary --------------------------

    def test_service_validates_its_collaborators(self):
        good = store.CoordinationStore(os.path.join(self.tmp.name, "q"))
        for args in (
            (object(), self.clock, self.source, frozenset()),
            (good, self.clock, object(), frozenset()),
            (good, self.clock, self.source, ["ENGINEERING_LANE"]),
            (good, self.clock, self.source, frozenset(["LANE_X"])),
        ):
            with self.assertRaises(record.CoordinationError):
                service.CoordinationService(*args)
        bad_clock = service.CoordinationService(good, lambda: "now", self.source,
                                                frozenset())
        with self.assertRaises(record.CoordinationError):
            bad_clock.route(inbound(explicit_mission_id=MISSION_X), context())

    def test_service_imports_only_stdlib_workflow_authority_and_itself(self):
        import ast
        allowed_roots = {"abc", "dataclasses", "typing", "json", "os", "stat",
                         "secrets", "workflow_authority", "coordination"}
        for module in (service, store, handoff, attention, routing, binding,
                       observation, record):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(root, allowed_roots, (module.__name__, root))

    # -- routing and bindings -----------------------------------------

    def test_route_records_and_replays_without_re_observing(self):
        outcome = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                         domain=record.DOMAIN_ENGINEERING), context())
        self.assertFalse(outcome.replayed)
        self.assertEqual(outcome.route["outcome"], record.ROUTE_EXISTING_MISSION)
        self.assertEqual(outcome.route["lane"], record.LANE_ENGINEERING)
        self.assertEqual(outcome.route["decided_at"], NOW)
        self.assertEqual(self.source.calls, [MISSION_X])
        self.clock.now = NOW + 50
        replay = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                        domain=record.DOMAIN_ENGINEERING), context())
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.route, outcome.route)
        self.assertEqual(replay.route["decided_at"], NOW)
        self.assertEqual(self.source.calls, [MISSION_X])
        with self.assertRaises(record.CoordinationError) as caught:
            self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                   domain=record.DOMAIN_ENGINEERING),
                           context(principal_ref="someone-else"))
        self.assertEqual(caught.exception.problem, record.PROBLEM_CONTEXT_MISMATCH)
        self.assertEqual(len(self.svc.inspect()["route_decisions"]), 1)

    def test_new_proposal_retries_are_idempotent(self):
        first = self.svc.route(inbound(proposal_digest_sha256=HEX_A), context())
        self.assertEqual(first.route["outcome"], record.ROUTE_NEW_PROPOSAL)
        second = self.svc.route(inbound(proposal_digest_sha256=HEX_A), context())
        self.assertTrue(second.replayed)
        self.assertEqual(second.route["route_id"], first.route["route_id"])
        self.assertEqual(second.route["proposal_digest_sha256"], HEX_A)
        self.assertEqual(len(self.svc.inspect()["route_decisions"]), 1)

    def test_bind_requires_a_fresh_observation_and_routing_never_rebinds(self):
        bound = self.svc.bind(record.BINDING_REPLY_TO_MESSAGE, "grok_mcp",
                              CONVERSATION_A, "msg-1", MISSION_X, context())
        self.assertEqual(bound["bound_revision"], 2)
        resolved = self.svc.route(inbound(reply_to_message_ref="msg-1"), context())
        self.assertEqual(resolved.route["mission_id"], MISSION_X)
        # The Mission moves to revision 3: the binding is stale, routing
        # clarifies and touches nothing.
        self.move(current_revision=3, state_cursor="12",
                  conditions=(condition(revision=3),))
        stale = self.svc.route(inbound(reply_to_message_ref="msg-1",
                                       message_ref="msg-101"), context())
        self.assertEqual(stale.route["outcome"], record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(stale.route["reason"], record.REASON_BINDING_STALE)
        self.assertEqual((stale.route["bound_revision"],
                          stale.route["observed_revision"]), (2, 3))
        document = self.svc.inspect()
        self.assertEqual(len(document["bindings"]), 1)
        self.assertEqual(document["bindings"][bound["binding_id"]], bound)
        # Rebinding is the separate bounded act: the old binding is
        # revoked and a new one is made under a FRESH observation.
        self.clock.now = NOW + 5
        rebound = self.svc.rebind(bound["binding_id"], context())
        self.assertNotEqual(rebound["binding_id"], bound["binding_id"])
        self.assertEqual(rebound["bound_revision"], 3)
        self.assertEqual(binding.identity(rebound), binding.identity(bound))
        document = self.svc.inspect()
        self.assertTrue(document["bindings"][bound["binding_id"]]["revoked"])
        self.assertEqual(document["bindings"][bound["binding_id"]]["revoked_at"],
                         NOW + 5)
        again = self.svc.route(inbound(reply_to_message_ref="msg-1",
                                       message_ref="msg-102"), context())
        self.assertEqual(again.route["mission_id"], MISSION_X)
        self.assertEqual(again.route["bound_revision"], 3)
        with self.assertRaises(record.CoordinationError) as caught:
            self.svc.rebind(bound["binding_id"], context())
        self.assertEqual(caught.exception.problem, record.PROBLEM_INVALID_TRANSITION)

    def test_bind_refuses_truthfully_when_the_mission_is_unobservable(self):
        self.source.answers[MISSION_X] = observation.ObservationOutcome.unavailable(
            "registry locked")
        with self.assertRaises(record.CoordinationError) as caught:
            self.svc.bind(record.BINDING_ALIAS, "grok_mcp", None, "alias", MISSION_X,
                          context())
        self.assertEqual(caught.exception.problem,
                         record.PROBLEM_OBSERVATION_UNAVAILABLE)
        self.assertIn("registry locked", str(caught.exception))
        del self.source.answers[MISSION_X]
        with self.assertRaises(record.CoordinationError) as caught:
            self.svc.bind(record.BINDING_ALIAS, "grok_mcp", None, "alias", MISSION_X,
                          context())
        self.assertEqual(caught.exception.problem, record.PROBLEM_MISSION_NOT_OBSERVED)
        self.assertEqual(self.svc.inspect()["bindings"], {})
        revoked = self.svc.bind(record.BINDING_ALIAS, "grok_mcp", None, "alias",
                                MISSION_Y, context())
        gone = self.svc.revoke_binding(revoked["binding_id"], context())
        self.assertTrue(gone["revoked"])

    def test_two_missions_stay_isolated_through_the_service(self):
        self.svc.bind(record.BINDING_CONVERSATION, "grok_mcp", CONVERSATION_A, "",
                      MISSION_X, context())
        self.svc.bind(record.BINDING_CONVERSATION, "grok_mcp", CONVERSATION_B, "",
                      MISSION_Y, context())
        in_a = self.svc.route(inbound(conversation_ref=CONVERSATION_A), context())
        in_b = self.svc.route(inbound(conversation_ref=CONVERSATION_B), context())
        self.assertEqual((in_a.route["mission_id"], in_b.route["mission_id"]),
                         (MISSION_X, MISSION_Y))

    # -- attention ----------------------------------------------------

    def test_attention_cycle_through_the_service(self):
        projected = self.svc.project_attention(MISSION_X, DESTINATION_A)
        self.assertEqual(len(projected.created), 1)
        attention_id = projected.created[0]
        self.assertEqual(self.svc.project_attention(MISSION_X, DESTINATION_A).suppressed,
                         [attention_id])
        presenter = RecordingPresenter()
        surfaced = self.svc.surface_attention(attention_id, presenter)
        self.assertTrue(surfaced.surfaced)
        self.assertEqual(len(presenter.presented), 1)
        acked = self.svc.acknowledge_attention(attention_id, context())
        self.assertEqual(acked["presentation"], record.PRESENTATION_ACKNOWLEDGED)
        self.assertEqual(acked["acknowledged_observation_cursor"], "7")
        self.assertEqual([r["attention_id"] for r in
                          self.svc.pending_attention(DESTINATION_A)], [attention_id])
        self.assertEqual(self.svc.aggregate_attention(DESTINATION_A)["by_kind"][
            record.ATTENTION_BLOCKED], 1)
        # Unavailable observation: projection reports it and changes nothing;
        # surfacing performs no transition; acknowledgment records it.
        second = self.svc.project_attention(MISSION_X, DESTINATION_B).created[0]
        self.source.answers[MISSION_X] = observation.ObservationOutcome.unavailable(
            "down")
        outcome = self.svc.project_attention(MISSION_X, DESTINATION_B)
        self.assertEqual(outcome.freshness, record.FRESHNESS_UNAVAILABLE)
        self.assertEqual(outcome.problem, "down")
        unsurfaced = self.svc.surface_attention(second, presenter)
        self.assertFalse(unsurfaced.surfaced)
        self.assertEqual(unsurfaced.freshness, record.FRESHNESS_UNAVAILABLE)
        self.assertEqual(len(presenter.presented), 1)
        acked = self.svc.acknowledge_attention(second, context())
        self.assertEqual(acked["acknowledged_freshness"], record.FRESHNESS_UNAVAILABLE)
        self.assertIsNone(acked["acknowledged_observation_cursor"])

    # -- handoffs -----------------------------------------------------

    def test_handoff_cycle_through_the_service(self):
        roster = self.svc.set_roster(MISSION_X, ROSTER, context())
        self.assertEqual(roster["participants"], sorted(ROSTER))
        created = self.svc.create_handoff(
            MISSION_X, COORDINATOR, ENGINEERING, record.PURPOSE_QUESTION, "q?",
            [ACCEPTED_CITE], [VALIDATED_CITE], "ask-1", None, context())
        self.assertEqual(created["status"], record.HANDOFF_OPEN)
        again = self.svc.create_handoff(
            MISSION_X, COORDINATOR, ENGINEERING, record.PURPOSE_QUESTION, "q?",
            [ACCEPTED_CITE], [VALIDATED_CITE], "ask-1", None, context())
        self.assertEqual(again, created)
        accepted = self.svc.transition_handoff(created["handoff_id"],
                                               record.HANDOFF_ACCEPTED, ENGINEERING,
                                               context())
        self.assertEqual(accepted["status"], record.HANDOFF_ACCEPTED)
        # Stale observation: the transition refuses truthfully and writes
        # nothing.
        self.move(current_revision=3, state_cursor="12",
                  conditions=(condition(revision=3),))
        with self.assertRaises(record.CoordinationError) as caught:
            self.svc.transition_handoff(created["handoff_id"], record.HANDOFF_ANSWERED,
                                        ENGINEERING, context())
        self.assertEqual(caught.exception.problem, record.PROBLEM_HANDOFF_STALE)
        self.assertEqual(self.svc.inspect()["handoffs"][created["handoff_id"]],
                         accepted)

    # -- restart, corruption, conflicts -------------------------------

    def test_restart_preserves_everything(self):
        bound = self.svc.bind(record.BINDING_REPLY_TO_MESSAGE, "grok_mcp",
                              CONVERSATION_A, "msg-1", MISSION_X, context())
        route = self.svc.route(inbound(reply_to_message_ref="msg-1"), context()).route
        attention_id = self.svc.project_attention(MISSION_X, DESTINATION_A).created[0]
        self.svc.set_roster(MISSION_X, ROSTER, context())
        created = self.svc.create_handoff(
            MISSION_X, COORDINATOR, ENGINEERING, record.PURPOSE_QUESTION, "q?",
            [ACCEPTED_CITE], [], "ask-1", None, context())
        restarted = self.harness.reopen().service
        document = restarted.inspect()
        self.assertEqual(document["bindings"][bound["binding_id"]], bound)
        self.assertEqual(document["route_decisions"][route["route_id"]], route)
        self.assertEqual(document["attention"][attention_id]["presentation"],
                         record.PRESENTATION_PENDING)
        self.assertEqual(document["handoffs"][created["handoff_id"]], created)
        replay = restarted.route(inbound(reply_to_message_ref="msg-1"), context())
        self.assertTrue(replay.replayed)
        self.assertEqual(restarted.project_attention(MISSION_X, DESTINATION_A).suppressed,
                         [attention_id])
        self.assertEqual(restarted.create_handoff(
            MISSION_X, COORDINATOR, ENGINEERING, record.PURPOSE_QUESTION, "q?",
            [ACCEPTED_CITE], [], "ask-1", None, context())["handoff_id"],
            created["handoff_id"])
        self.assertEqual(restarted.inspect(), document)

    def test_corrupt_state_fails_every_operation_closed(self):
        self.svc.route(inbound(explicit_mission_id=MISSION_X), context())
        path = self.svc.store.path
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{corrupt")
        os.chmod(path, 0o600)
        operations = (
            lambda: self.svc.route(inbound(explicit_mission_id=MISSION_X), context()),
            lambda: self.svc.bind(record.BINDING_ALIAS, "grok_mcp", None, "a",
                                  MISSION_X, context()),
            lambda: self.svc.project_attention(MISSION_X, DESTINATION_A),
            lambda: self.svc.set_roster(MISSION_X, ROSTER, context()),
            lambda: self.svc.pending_attention(DESTINATION_A),
            lambda: self.svc.inspect(),
        )
        for operation in operations:
            with self.assertRaises(store.CoordinationStoreError) as caught:
                operation()
            self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_UNREADABLE)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{corrupt")
        self.assertEqual(self.source.calls, [MISSION_X])

    def test_conflicting_write_fails_closed_and_leaves_the_other_writer_intact(self):
        first = self.svc.route(inbound(explicit_mission_id=MISSION_X), context())

        class Interfered(store.CoordinationStore):
            """Between this writer's load and save, another writer saved."""

            def save(self, document, expected_sequence):
                other = store.CoordinationStore(self.directory)
                foreign = other.load()
                other.save(foreign, expected_sequence=expected_sequence)
                return super(Interfered, self).save(document, expected_sequence)

        racing = service.CoordinationService(
            Interfered(self.harness.directory), self.clock, self.source,
            frozenset([record.LANE_ENGINEERING]), mint_id=self.harness.mint)
        with self.assertRaises(store.CoordinationStoreError) as caught:
            racing.route(inbound(explicit_mission_id=MISSION_Y,
                                 message_ref="msg-200"), context())
        self.assertEqual(caught.exception.problem, store.PROBLEM_STORE_CONFLICT)
        document = self.svc.inspect()
        self.assertEqual(document["store_sequence"], 2)
        self.assertEqual(list(document["route_decisions"]), [first.route["route_id"]])

    # -- the high-water mark (A-R3) -----------------------------------

    def test_high_water_mark_is_derived_from_all_four_families(self):
        self.assertIsNone(service.high_water_mark(store.default_document(), MISSION_X))
        # A route decision alone sets the floor.
        self.svc.route(inbound(explicit_mission_id=MISSION_X), context())
        mark = service.high_water_mark(self.svc.inspect(), MISSION_X)
        self.assertEqual((mark.revision, mark.cursor), (2, "7"))
        self.assertEqual(mark.point, observation.observation_point(fresh()))
        # A handoff observed later raises it.
        self.move(state_cursor="9")
        self.svc.set_roster(MISSION_X, ROSTER, context())
        self.svc.create_handoff(MISSION_X, COORDINATOR, ENGINEERING,
                                record.PURPOSE_SUMMARY, "s", [], [], "k", None,
                                context())
        mark = service.high_water_mark(self.svc.inspect(), MISSION_X)
        self.assertEqual((mark.revision, mark.cursor), (2, "9"))
        # Bindings and attention records count as well.
        self.move(state_cursor="11")
        self.svc.bind(record.BINDING_ALIAS, "grok_mcp", None, "a", MISSION_X, context())
        self.assertEqual(service.high_water_mark(self.svc.inspect(), MISSION_X).cursor,
                         "11")
        self.move(state_cursor="12")
        self.svc.project_attention(MISSION_X, DESTINATION_A)
        self.assertEqual(service.high_water_mark(self.svc.inspect(), MISSION_X).cursor,
                         "12")
        # A source that now answers behind the floor is STALE, truthfully.
        self.move(state_cursor="8")
        stale = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                       message_ref="msg-300"), context())
        self.assertEqual(stale.route["reason"], record.REASON_OBSERVATION_STALE)
        self.assertIn("cursor 12", stale.route["detail"])
        # Another Mission has its own floor.
        self.assertIsNone(service.high_water_mark(self.svc.inspect(), MISSION_Y))

    def test_a_non_fresh_observation_never_raises_the_floor(self):
        self.svc.route(inbound(explicit_mission_id=MISSION_X), context())
        # Old-age STALE at a much later point: clarified, not recorded.
        self.move(current_revision=5, state_cursor="50",
                  observed_at=NOW - observation.MAX_OBSERVATION_AGE_SECONDS - 1,
                  conditions=(condition(revision=5, evidence_refs=(
                      evidence_ref(accepted_at=NOW - 5000),)),),
                  evidence_refs=(evidence_ref(accepted_at=NOW - 5000),
                                 recorded_evidence_ref()),
                  artifact_refs=(artifact_ref(receipt=receipt(
                      validated_at=NOW - 5000)),))
        outcome = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                         message_ref="msg-2"), context())
        self.assertEqual(outcome.route["reason"], record.REASON_OBSERVATION_STALE)
        self.assertIsNone(outcome.route["observation_point"])
        mark = service.high_water_mark(self.svc.inspect(), MISSION_X)
        self.assertEqual((mark.revision, mark.cursor), (2, "7"))
        # The original point is still FRESH against the unchanged floor.
        self.move()
        back = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                      message_ref="msg-3"), context())
        self.assertEqual(back.route["outcome"], record.ROUTE_EXISTING_MISSION)
        # Same point, different content: INCONSISTENT, and the floor holds.
        self.move(proposal_digest_sha256=HEX_B)
        equivocal = self.svc.route(inbound(explicit_mission_id=MISSION_X,
                                           message_ref="msg-4"), context())
        self.assertEqual(equivocal.route["reason"],
                         record.REASON_OBSERVATION_INCONSISTENT)
        self.assertEqual(service.high_water_mark(self.svc.inspect(), MISSION_X).point,
                         mark.point)

    # -- no live effects ----------------------------------------------

    # -- review round 1 regressions ------------------------------------

    def test_f2_clearing_attention_retains_the_freshness_floor(self):
        created = self.svc.project_attention(MISSION_X, DESTINATION_A).created[0]
        self.move(state_cursor="8", conditions=())
        cleared = self.svc.project_attention(MISSION_X, DESTINATION_A)
        self.assertEqual(cleared.resolved, [created])
        mark = service.high_water_mark(self.svc.inspect(), MISSION_X)
        self.assertEqual((mark.revision, mark.cursor), (2, "8"))
        restarted = self.harness.reopen().service
        self.move(state_cursor="7")
        again = restarted.project_attention(MISSION_X, DESTINATION_A)
        self.assertEqual(again.freshness, record.FRESHNESS_STALE)
        self.assertEqual(again.created, [])
        self.assertEqual(len(restarted.inspect()["attention"]), 1)
        # Surfacing and acknowledging under a FRESH read also raise the floor.
        self.move(state_cursor="9")
        second = restarted.project_attention(MISSION_X, DESTINATION_A).created[0]
        self.move(state_cursor="10")
        restarted.surface_attention(second, RecordingPresenter())
        self.assertEqual(service.high_water_mark(restarted.inspect(), MISSION_X).cursor,
                         "10")
        self.move(state_cursor="11")
        acked = restarted.acknowledge_attention(second, context())
        self.assertEqual(acked["acknowledged_observation_point"]["cursor"], "11")
        self.assertEqual(service.high_water_mark(restarted.inspect(), MISSION_X).cursor,
                         "11")

    def test_f3_explicit_id_consults_matching_stale_context_first(self):
        bound = self.svc.bind(record.BINDING_APPROVAL_PRESENTATION, "grok_mcp",
                              CONVERSATION_A, "card-1", MISSION_X, context())
        self.move(current_revision=3, state_cursor="12",
                  conditions=(condition(revision=3),))
        without = self.svc.route(inbound(reply_to_message_ref="card-1",
                                         domain=record.DOMAIN_ENGINEERING), context())
        self.assertEqual(without.route["reason"], record.REASON_BINDING_STALE)
        with_id = self.svc.route(inbound(reply_to_message_ref="card-1",
                                         explicit_mission_id=MISSION_X,
                                         domain=record.DOMAIN_ENGINEERING,
                                         message_ref="msg-101"), context())
        self.assertEqual(with_id.route["outcome"], record.ROUTE_CLARIFICATION_REQUIRED)
        self.assertEqual(with_id.route["reason"], record.REASON_BINDING_STALE)
        self.assertEqual(with_id.route["bound_revision"], bound["bound_revision"])
        self.assertEqual(with_id.route["observed_revision"], 3)
        self.assertEqual(with_id.route["lane_outcome"], record.LANE_OUTCOME_REFUSED)
        self.assertIsNone(with_id.route["lane"])

    def test_fixtures_perform_no_live_effect(self):
        # The only collaborators are the read-only source (calls recorded),
        # the recording presenter (sends nothing), the clock and the store.
        presenter = RecordingPresenter()
        self.svc.route(inbound(explicit_mission_id=MISSION_X,
                               domain=record.DOMAIN_ENGINEERING), context())
        attention_id = self.svc.project_attention(MISSION_X, DESTINATION_A).created[0]
        self.svc.surface_attention(attention_id, presenter)
        self.assertEqual(set(self.source.calls), {MISSION_X})
        self.assertEqual(len(presenter.presented), 1)
        names = set(os.listdir(self.harness.directory))
        self.assertEqual(names, {"coordination.json", "coordination.lock"})
        document = self.svc.inspect()
        for family in store.FAMILY_PREFIXES:
            for value in document[family].values():
                self.assertEqual(value["authority"], "none")


if __name__ == "__main__":
    unittest.main()
