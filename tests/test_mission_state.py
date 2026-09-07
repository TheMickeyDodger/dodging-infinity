"""Behavioral tests for Task 5 Mission State (stage 1: schema, pure
evaluators, persistence).

Sections:
  A  record: the additive-optional ``proof_contract`` proposal key, its
     closed schema and bounds, R-5.2 legacy byte/digest preservation,
     the new id prefixes (R-16), R-18/R-19 prerequisite identity
  B  state: Task 5 record shapes, closed validation, bounds, the
     separate progress vocabulary and transition table (R-2)
  C  progress: pure deterministic evaluators (R-8, R-9, R-10, R-11, R-14)
  D  store: the R-3 compatibility rule, cross-reference and cycle
     refusals, fail-closed reload, caps
  E  authorization: the narrow ``state_operation`` reconcile branch (R-6)
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

from mission import record as mission_record  # noqa: E402

# Hard-coded Task 4 digests of the canonical test proposal, captured on
# baseline 212825fd4ebda91360da3df20720b99b02bcddae BEFORE this task
# touched ``mission/record.py``. A Task-4-shaped proposal must still
# normalize and digest to exactly these values (R-5.2).
LEGACY_PROPOSAL_DIGEST = (
    "18b3edb4381ae9f02b0ff274491d0170d55ca541c35e76aa414d43fc2e36b24d"
)
LEGACY_PROPOSAL_NO_REPO_NO_TARGET_DIGEST = (
    "cae5116994d03c3acd2e8a521f43b9aaa34cdb0dba7a621c22ab6148e59701bd"
)
LEGACY_CANONICAL_BYTES = (
    b'{"objective":"Investigate and resolve the flaky readiness probe",'
    b'"repository_url":"https://github.com/Example/Repo",'
    b'"requested_action_scope":["engineering_change","repository_read"],'
    b'"requested_delivery_target":"github_pr",'
    b'"requested_scope":"readiness probe and its tests",'
    b'"target_context":"control repository, readiness subsystem"}'
)

HEX_A = "a" * 64
HEX_B = "b" * 64
MISSION_X = "mn-" + "1" * 32


def proposal(**overrides):
    base = {
        "objective": "Investigate and resolve the flaky readiness probe",
        "target_context": "control repository, readiness subsystem",
        "repository_url": "https://github.com/Example/Repo",
        "requested_scope": "readiness probe and its tests",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": mission_record.DELIVERY_TARGET_GITHUB_PR,
    }
    base.update(overrides)
    return base


def contract(**overrides):
    """A small but complete proof contract (R-5.1, R-18, R-19)."""
    base = {
        "requirements": [
            {
                "key": "tests_pass",
                "description": "the focused suite passes",
                "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
                "required_artifact_keys": ["test_log"],
                "max_evidence_age_seconds": 3600,
            },
        ],
        "required_artifacts": [
            {
                "key": "test_log",
                "role": mission_record.ARTIFACT_ROLE_VERIFICATION,
                "expected_content_digest_sha256": HEX_A,
            },
        ],
        "required_dependencies": [
            {
                "key": "upstream",
                "kind": mission_record.DEPENDENCY_KIND_MISSION,
                "target": {
                    "form": mission_record.TARGET_FORM_EXACT_MISSION,
                    "mission_id": MISSION_X,
                    "revision": 1,
                    "proposal_digest_sha256": HEX_B,
                },
            },
        ],
        "required_resource_readiness": [
            {"resource_key": "build_host", "max_age_seconds": 600},
        ],
        "degradation_policy": {"permitted_blocker_keys": ["flaky_network"]},
        "continuation_budget": {"max_attempts": 3, "max_checkpoints": 8},
    }
    base.update(overrides)
    return base


def refuses(test, problem, callable_, *args, **kwargs):
    with test.assertRaises(mission_record.MissionError) as ctx:
        callable_(*args, **kwargs)
    test.assertEqual(ctx.exception.problem, problem, str(ctx.exception))
    return ctx.exception


# ====================================================================
# A. Record: proof_contract schema and legacy preservation
# ====================================================================


class ALegacyPreservationTests(unittest.TestCase):

    def test_A1_task4_shaped_proposal_normalizes_and_digests_byte_identically(self):
        from workflow_authority.digest import canonical_json_bytes
        clean = mission_record.validate_proposal(proposal())
        self.assertEqual(sorted(clean), sorted(mission_record.PROPOSAL_KEYS))
        self.assertNotIn("proof_contract", clean)
        self.assertEqual(canonical_json_bytes(clean), LEGACY_CANONICAL_BYTES)
        self.assertEqual(mission_record.proposal_digest(proposal()),
                         LEGACY_PROPOSAL_DIGEST)
        self.assertEqual(
            mission_record.proposal_digest(
                proposal(repository_url=None, requested_delivery_target=None)
            ),
            LEGACY_PROPOSAL_NO_REPO_NO_TARGET_DIGEST,
        )

    def test_A2_absent_and_explicit_null_contract_both_normalize_to_absent(self):
        absent = mission_record.validate_proposal(proposal())
        explicit = mission_record.validate_proposal(proposal(proof_contract=None))
        self.assertEqual(absent, explicit)
        self.assertNotIn("proof_contract", explicit)
        self.assertEqual(mission_record.proposal_digest(proposal(proof_contract=None)),
                         LEGACY_PROPOSAL_DIGEST)

    def test_A3_proposal_keys_are_exactly_the_six_required_keys(self):
        self.assertEqual(mission_record.PROPOSAL_KEYS, (
            "objective", "target_context", "repository_url", "requested_scope",
            "requested_action_scope", "requested_delivery_target",
        ))
        self.assertEqual(mission_record.PROPOSAL_OPTIONAL_KEYS, ("proof_contract",))
        # A missing required key still refuses; an unknown key still refuses.
        refuses(self, mission_record.PROBLEM_MISSING_KEY,
                mission_record.validate_proposal,
                {k: v for k, v in proposal().items() if k != "objective"})
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY,
                mission_record.validate_proposal, proposal(extra=1))
        # The optional key is not required.
        mission_record.validate_proposal(proposal())

    def test_A4_present_contract_is_validated_carried_and_changes_the_digest(self):
        clean = mission_record.validate_proposal(proposal(proof_contract=contract()))
        self.assertIn("proof_contract", clean)
        self.assertEqual(set(clean), set(mission_record.PROPOSAL_KEYS)
                         | {"proof_contract"})
        with_contract = mission_record.proposal_digest(proposal(proof_contract=contract()))
        self.assertNotEqual(with_contract, LEGACY_PROPOSAL_DIGEST)
        # Deterministic: key order and list order do not matter.
        shuffled = contract()
        shuffled["requirements"][0]["evidence_kinds"] = list(reversed(
            shuffled["requirements"][0]["evidence_kinds"]))
        reordered = dict(reversed(list(proposal(proof_contract=shuffled).items())))
        self.assertEqual(mission_record.proposal_digest(reordered), with_contract)
        # A one-field change in the contract is a different proposal.
        changed = contract(continuation_budget={"max_attempts": 4, "max_checkpoints": 8})
        self.assertNotEqual(
            mission_record.proposal_digest(proposal(proof_contract=changed)),
            with_contract,
        )
        # The contract digest is the content digest of the normalized contract.
        digest = mission_record.proof_contract_digest(clean["proof_contract"])
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, mission_record.proof_contract_digest(contract()))


class AContractSchemaTests(unittest.TestCase):

    def validate(self, value):
        return mission_record.validate_proof_contract(value)

    def test_A5_contract_is_closed_and_every_key_required(self):
        refuses(self, mission_record.PROBLEM_NOT_AN_OBJECT, self.validate, [])
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate,
                contract(extra=1))
        for key in mission_record.PROOF_CONTRACT_KEYS:
            partial = {k: v for k, v in contract().items() if k != key}
            refuses(self, mission_record.PROBLEM_MISSING_KEY, self.validate, partial)
        self.assertEqual(sorted(mission_record.PROOF_CONTRACT_KEYS), sorted((
            "requirements", "required_artifacts", "required_dependencies",
            "required_resource_readiness", "degradation_policy",
            "continuation_budget",
        )))

    def test_A6_normalization_sorts_by_key_and_refuses_duplicates(self):
        two = contract()
        two["requirements"] = [
            dict(two["requirements"][0], key="zeta"),
            dict(two["requirements"][0], key="alpha", required_artifact_keys=[]),
        ]
        clean = self.validate(two)
        self.assertEqual([r["key"] for r in clean["requirements"]],
                         ["alpha", "zeta"])
        dup = contract()
        dup["requirements"] = [dup["requirements"][0], dict(dup["requirements"][0])]
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate, dup)
        for name in ("required_artifacts", "required_dependencies",
                     "required_resource_readiness"):
            dup = contract()
            dup[name] = [dup[name][0], dict(dup[name][0])]
            refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate, dup)
        dup = contract(degradation_policy={"permitted_blocker_keys": ["a", "a"]})
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate, dup)
        # Inner lists are sorted and duplicate-free too.
        inner = contract()
        inner["requirements"][0]["evidence_kinds"] = [
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
        ]
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate, inner)
        # The normalized form is a fixed point.
        self.assertEqual(self.validate(self.validate(contract())), self.validate(contract()))

    def test_A7_requirements_refuse_bad_shapes_and_bounds(self):
        cases = {
            "empty requirements": (contract(requirements=[]),
                                   mission_record.PROBLEM_PROOF_CONTRACT),
            "too many requirements": (
                contract(requirements=[
                    dict(contract()["requirements"][0], key="k%d" % i)
                    for i in range(mission_record.MAX_PROOF_REQUIREMENTS + 1)
                ]), mission_record.PROBLEM_TOO_LARGE),
            "requirement unknown key": (
                contract(requirements=[dict(contract()["requirements"][0], x=1)]),
                mission_record.PROBLEM_UNKNOWN_KEY),
            "requirement missing key": (
                contract(requirements=[{k: v for k, v in
                                        contract()["requirements"][0].items()
                                        if k != "description"}]),
                mission_record.PROBLEM_MISSING_KEY),
            "bad key grammar": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            key="Has Space")]),
                mission_record.PROBLEM_PROOF_CONTRACT),
            "key too long": (
                contract(requirements=[dict(
                    contract()["requirements"][0],
                    key="k" * (mission_record.MAX_CONTRACT_KEY_CHARS + 1))]),
                mission_record.PROBLEM_TOO_LARGE),
            "description too long": (
                contract(requirements=[dict(
                    contract()["requirements"][0],
                    description="d" * (
                        mission_record.MAX_REQUIREMENT_DESCRIPTION_CHARS + 1))]),
                mission_record.PROBLEM_TOO_LARGE),
            "empty evidence kinds": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            evidence_kinds=[])]),
                mission_record.PROBLEM_PROOF_CONTRACT),
            "unknown evidence kind": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            evidence_kinds=["MAGIC"])]),
                mission_record.PROBLEM_PROOF_CONTRACT),
            "narrative claim can never be declared satisfying": (
                contract(requirements=[dict(
                    contract()["requirements"][0],
                    evidence_kinds=[mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM])]),
                mission_record.PROBLEM_PROOF_CONTRACT),
            "process exit can never be declared satisfying": (
                contract(requirements=[dict(
                    contract()["requirements"][0],
                    evidence_kinds=[mission_record.EVIDENCE_KIND_PROCESS_EXIT])]),
                mission_record.PROBLEM_PROOF_CONTRACT),
            "unknown required artifact key": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            required_artifact_keys=["nope"])]),
                mission_record.PROBLEM_CONTRACT_INCOHERENT),
            "age zero": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            max_evidence_age_seconds=0)]),
                mission_record.PROBLEM_BAD_VALUE),
            "age bool": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            max_evidence_age_seconds=True)]),
                mission_record.PROBLEM_BAD_TYPE),
            "age null": (
                contract(requirements=[dict(contract()["requirements"][0],
                                            max_evidence_age_seconds=None)]),
                mission_record.PROBLEM_BAD_TYPE),
            "age above bound": (
                contract(requirements=[dict(
                    contract()["requirements"][0],
                    max_evidence_age_seconds=(
                        mission_record.MAX_STALENESS_BOUND_SECONDS + 1))]),
                mission_record.PROBLEM_TOO_LARGE),
        }
        for label, (bad, problem) in cases.items():
            with self.subTest(label):
                refuses(self, problem, self.validate, bad)

    def test_A8_required_artifacts_are_content_identified(self):
        # R-19: a required artifact MUST declare its expected digest.
        for digest in (None, "", "z" * 64, "a" * 63, HEX_A.upper()):
            bad = contract(required_artifacts=[dict(
                contract()["required_artifacts"][0],
                expected_content_digest_sha256=digest)])
            with self.subTest(repr(digest)):
                with self.assertRaises(mission_record.MissionError) as ctx:
                    self.validate(bad)
                self.assertIn(ctx.exception.problem, (
                    mission_record.PROBLEM_BAD_TYPE, mission_record.PROBLEM_BAD_VALUE,
                    mission_record.PROBLEM_PROOF_CONTRACT,
                ))
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate,
                contract(required_artifacts=[dict(
                    contract()["required_artifacts"][0], role="ANYTHING")]))
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate,
                contract(required_artifacts=[dict(
                    contract()["required_artifacts"][0], locator="x")]))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                contract(requirements=[dict(contract()["requirements"][0],
                                            required_artifact_keys=[])],
                         required_artifacts=[
                             dict(contract()["required_artifacts"][0], key="a%d" % i)
                             for i in range(mission_record.MAX_REQUIRED_ARTIFACTS + 1)
                         ]))
        # Empty required_artifacts is allowed when no requirement names one.
        self.validate(contract(
            requirements=[dict(contract()["requirements"][0],
                               required_artifact_keys=[])],
            required_artifacts=[],
        ))

    def test_A14_contract_coherence_every_required_artifact_matters(self):
        # R-20.1: a declared required artifact no requirement names is
        # refused, and so is a requirement naming an undeclared one.
        unused = contract(requirements=[dict(contract()["requirements"][0],
                                             required_artifact_keys=[])])
        refuses(self, mission_record.PROBLEM_CONTRACT_INCOHERENT, self.validate, unused)
        extra = contract(required_artifacts=contract()["required_artifacts"] + [
            {"key": "orphan", "role": mission_record.ARTIFACT_ROLE_PRODUCED,
             "expected_content_digest_sha256": HEX_B}])
        refuses(self, mission_record.PROBLEM_CONTRACT_INCOHERENT, self.validate, extra)
        undeclared = contract(requirements=[dict(contract()["requirements"][0],
                                                 required_artifact_keys=["test_log",
                                                                         "ghost"])])
        refuses(self, mission_record.PROBLEM_CONTRACT_INCOHERENT, self.validate,
                undeclared)
        # Shared by two requirements is coherent.
        shared = contract(requirements=[
            contract()["requirements"][0],
            dict(contract()["requirements"][0], key="also_tests"),
        ])
        self.validate(shared)
        self.assertTrue(mission_record.PROBLEM_CONTRACT_INCOHERENT.startswith(
            "mission_state_"))

    def test_A9_required_dependencies_carry_an_exactly_one_of_target(self):
        base = contract()["required_dependencies"][0]
        # A bare {key, kind} slot is forbidden (R-18).
        refuses(self, mission_record.PROBLEM_MISSING_KEY, self.validate,
                contract(required_dependencies=[{"key": "upstream",
                                                 "kind": "MISSION"}]))
        good_targets = [
            {"form": mission_record.TARGET_FORM_EXACT_MISSION,
             "mission_id": MISSION_X, "revision": 2,
             "proposal_digest_sha256": HEX_B},
            {"form": mission_record.TARGET_FORM_ELIGIBILITY,
             "condition": mission_record.ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST,
             "proposal_digest_sha256": HEX_B},
        ]
        for target in good_targets:
            clean = self.validate(contract(required_dependencies=[
                dict(base, target=target)]))
            self.assertEqual(clean["required_dependencies"][0]["target"], target)
        resource = {"form": mission_record.TARGET_FORM_EXACT_RESOURCE,
                    "resource_key": "gpu_pool"}
        clean = self.validate(contract(required_dependencies=[
            dict(base, kind=mission_record.DEPENDENCY_KIND_RESOURCE, target=resource)]))
        self.assertEqual(clean["required_dependencies"][0]["target"], resource)
        bad_targets = {
            "unknown form": {"form": "ANY", "mission_id": MISSION_X},
            "exact mission missing digest": {
                "form": "EXACT_MISSION", "mission_id": MISSION_X, "revision": 1},
            "exact mission extra key": {
                "form": "EXACT_MISSION", "mission_id": MISSION_X, "revision": 1,
                "proposal_digest_sha256": HEX_B, "resource_key": "x"},
            "exact mission bad id": {
                "form": "EXACT_MISSION", "mission_id": "mq-" + "1" * 32,
                "revision": 1, "proposal_digest_sha256": HEX_B},
            "exact mission revision zero": {
                "form": "EXACT_MISSION", "mission_id": MISSION_X, "revision": 0,
                "proposal_digest_sha256": HEX_B},
            "eligibility unknown condition": {
                "form": "ELIGIBILITY", "condition": "ANY_MISSION",
                "proposal_digest_sha256": HEX_B},
            "eligibility free-form predicate": {
                "form": "ELIGIBILITY",
                "condition": mission_record.ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST,
                "proposal_digest_sha256": HEX_B, "predicate": "state == done"},
            "eligibility null digest": {
                "form": "ELIGIBILITY",
                "condition": mission_record.ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST,
                "proposal_digest_sha256": None},
            "resource form with mission kind": resource,
            "not an object": "EXACT_MISSION",
        }
        for label, target in bad_targets.items():
            with self.subTest(label):
                with self.assertRaises(mission_record.MissionError) as ctx:
                    self.validate(contract(required_dependencies=[
                        dict(base, target=target)]))
                self.assertTrue(ctx.exception.problem.startswith("mission_"))
        # Kind/form pairing: RESOURCE kind needs EXACT_RESOURCE.
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate,
                contract(required_dependencies=[dict(
                    base, kind=mission_record.DEPENDENCY_KIND_RESOURCE)]))
        refuses(self, mission_record.PROBLEM_PROOF_CONTRACT, self.validate,
                contract(required_dependencies=[dict(base, kind="SERVICE")]))
        self.assertEqual(mission_record.ELIGIBILITY_CONDITIONS,
                         (mission_record.ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST,))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                contract(required_dependencies=[
                    dict(base, key="d%d" % i)
                    for i in range(mission_record.MAX_REQUIRED_DEPENDENCIES + 1)
                ]))

    def test_A10_readiness_policy_and_budget_shapes(self):
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate,
                contract(required_resource_readiness=[
                    {"resource_key": "h", "max_age_seconds": 1, "status": "READY"}]))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                contract(required_resource_readiness=[
                    {"resource_key": "h", "max_age_seconds": 0}]))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                contract(required_resource_readiness=[
                    {"resource_key": "h%d" % i, "max_age_seconds": 1}
                    for i in range(mission_record.MAX_REQUIRED_RESOURCE_READINESS + 1)
                ]))
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate,
                contract(degradation_policy={"permitted_blocker_keys": [],
                                             "permit_all": True}))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                contract(degradation_policy={"permitted_blocker_keys": [
                    "b%d" % i
                    for i in range(mission_record.MAX_PERMITTED_BLOCKER_KEYS + 1)]}))
        for budget, problem in (
            ({"max_attempts": -1, "max_checkpoints": 1}, mission_record.PROBLEM_BAD_VALUE),
            ({"max_attempts": 1, "max_checkpoints": 0}, mission_record.PROBLEM_BAD_VALUE),
            ({"max_attempts": mission_record.MAX_CONTINUATION_ATTEMPTS + 1,
              "max_checkpoints": 1}, mission_record.PROBLEM_TOO_LARGE),
            ({"max_attempts": 1,
              "max_checkpoints": mission_record.MAX_CONTINUATION_CHECKPOINTS + 1},
             mission_record.PROBLEM_TOO_LARGE),
            ({"max_attempts": 1}, mission_record.PROBLEM_MISSING_KEY),
            ({"max_attempts": 1, "max_checkpoints": 1, "max_retries": 9},
             mission_record.PROBLEM_UNKNOWN_KEY),
            ({"max_attempts": "3", "max_checkpoints": 1}, mission_record.PROBLEM_BAD_TYPE),
        ):
            with self.subTest(repr(budget)):
                refuses(self, problem, self.validate,
                        contract(continuation_budget=budget))
        # Zero attempts is a legitimate approved budget (no continuation).
        self.validate(contract(continuation_budget={"max_attempts": 0,
                                                    "max_checkpoints": 1}))

    def test_A11_evidence_kind_table_declares_non_satisfying_kinds(self):
        kinds = mission_record.EVIDENCE_KINDS
        satisfying = mission_record.SATISFYING_EVIDENCE_KINDS
        self.assertIn(mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM, kinds)
        self.assertIn(mission_record.EVIDENCE_KIND_PROCESS_EXIT, kinds)
        self.assertNotIn(mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM, satisfying)
        self.assertNotIn(mission_record.EVIDENCE_KIND_PROCESS_EXIT, satisfying)
        self.assertTrue(set(satisfying) < set(kinds))
        self.assertTrue(satisfying)
        self.assertEqual(mission_record.ARTIFACT_ROLES, (
            mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT,
            mission_record.ARTIFACT_ROLE_PRODUCED,
            mission_record.ARTIFACT_ROLE_VERIFICATION,
        ))
        self.assertEqual(sorted(mission_record.DEPENDENCY_KINDS),
                         ["MISSION", "RESOURCE"])


class AIdentityTests(unittest.TestCase):

    def test_A12_new_prefixes_are_distinct_two_char_and_registered(self):
        new = {
            "mt": mission_record.PROOF_CONTRACT_ID_PREFIX,
            "mv": mission_record.EVIDENCE_ID_PREFIX,
            "mf": mission_record.ARTIFACT_ID_PREFIX,
            "mb": mission_record.BLOCKER_ID_PREFIX,
            "mc": mission_record.CLAIM_ID_PREFIX,
            "mk": mission_record.CHECKPOINT_ID_PREFIX,
            "mx": mission_record.DEPENDENCY_ID_PREFIX,
            "mo": mission_record.STATE_OPERATION_ID_PREFIX,
        }
        for expected, actual in new.items():
            self.assertEqual(actual, expected)
            self.assertIn(actual, mission_record.ID_PREFIXES)
            minted = mission_record.mint_id(actual)
            self.assertIsNone(mission_record.id_problem(minted, actual))
            self.assertEqual(len(minted), 35)
        prefixes = mission_record.ID_PREFIXES
        self.assertEqual(len(set(prefixes)), len(prefixes))
        self.assertTrue(all(len(p) == 2 for p in prefixes))
        self.assertNotIn("di", prefixes)
        for legacy in ("mn", "mq", "md", "ma", "ml"):
            self.assertIn(legacy, prefixes)
        # No dormant prefix: requirements are addressed by contract key, so
        # there is no proof-requirement prefix at all.
        self.assertNotIn("mp", prefixes)
        self.assertFalse(hasattr(mission_record, "PROOF_REQUIREMENT_ID_PREFIX"))
        self.assertEqual(len(prefixes), 13)

    def test_A13_state_operation_reference_kind_in_provenance(self):
        context = mission_record.AuthenticatedContext(
            transport="local", principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501",
        )
        operation_id = mission_record.mint_id(mission_record.STATE_OPERATION_ID_PREFIX)
        block = mission_record.provenance_record(
            context, 10, mission_record.REFERENCE_KIND_STATE_OPERATION,
            operation_id, MISSION_X, 1,
        )
        self.assertEqual(block["reference_kind"], "state_operation")
        self.assertEqual(mission_record.validate_provenance(block), block)
        # The kind binds the prefix: a decision id under the new kind refuses,
        # and an operation id under the decision kind refuses.
        refuses(self, mission_record.PROBLEM_ID_GRAMMAR,
                mission_record.provenance_record, context, 10,
                mission_record.REFERENCE_KIND_STATE_OPERATION,
                "md-" + "0" * 32, MISSION_X, 1)
        refuses(self, mission_record.PROBLEM_ID_GRAMMAR,
                mission_record.provenance_record, context, 10,
                mission_record.REFERENCE_KIND_DECISION, operation_id, MISSION_X, 1)
        # Task 4 kinds still resolve to their prefixes.
        for kind, prefix in ((mission_record.REFERENCE_KIND_REQUEST, "mq"),
                             (mission_record.REFERENCE_KIND_DECISION, "md")):
            ok = mission_record.provenance_record(
                context, 10, kind, prefix + "-" + "0" * 32, MISSION_X, 1)
            mission_record.validate_provenance(ok)

    def test_A15_state_operation_provenance_cannot_leak_into_task4_records(self):
        from mission import authorization as ma
        from mission import decision as md
        from mission import manifest as mm
        context = mission_record.AuthenticatedContext(
            transport="local", principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501",
        )
        operation_id = hexid("mo", 1)
        leaked = mission_record.provenance_record(
            context, 10, mission_record.REFERENCE_KIND_STATE_OPERATION,
            operation_id, MISSION_X, 1)
        # Revision (manifest) validator.
        mission = mm.new_mission_record(MISSION_X, hexid("mq", 1), proposal(), 10, context)
        mission["revisions"][0]["provenance"] = leaked
        refuses(self, mm.PROBLEM_MALFORMED_STATE, mm.validate_mission_record, mission)
        # Decision validator.
        decision = {
            "decision_id": hexid("md", 1), "mission_id": MISSION_X, "revision": 1,
            "decision": "DENY", "approved_action_scope": None,
            "approved_delivery_targets": None, "expires_at": None,
            "proposal_digest_sha256": None, "provenance": leaked,
            "received_at": 10, "decided_at": 10,
            "decision_digest_sha256": md.decision_digest(
                MISSION_X, 1, "DENY", None, None, None, None),
            "outcome": {"resulting_state": "DENIED", "resulting_revision": 1,
                        "proposal_digest_sha256": LEGACY_PROPOSAL_DIGEST,
                        "authorization_id": None,
                        "invalidated_authorization_ids": []},
        }
        refuses(self, mission_record.PROBLEM_PROVENANCE, md.validate_decision_record,
                decision)
        # Authorization validator (single constructor).
        refuses(self, mission_record.PROBLEM_PROVENANCE, ma.issue_mission_authorization,
                hexid("ma", 1), MISSION_X, 1, LEGACY_PROPOSAL_DIGEST, leaked,
                [mission_record.ACTION_SCOPE_REPOSITORY_READ], [], 10, None)


# ====================================================================
# B. State record shapes and the progress lifecycle
# ====================================================================


def hexid(prefix, n):
    return "%s-%032x" % (prefix, n)


def seal_state(state, max_attempts=3):
    """Test-side: derive every applied operation's outcome from the effect
    records exactly as the service does (closed per-kind schema, R-31.1),
    so hand-built fixtures reconcile. Ops whose effect cannot be found are
    left as they are, so a deletion tamper still refuses."""
    from mission import progress as mp
    from mission import state as ms
    mission_id = state.get("mission_id")
    for name in ("contract_activations", "claims", "artifacts", "evidence", "blockers",
                 "dependencies", "resource_readiness", "checkpoints", "continuations",
                 "applied_operations"):
        if not isinstance(state.get(name), list):
            return state
    had_closure = "closure" in state
    state.setdefault("closure", None)

    def one(name, oid):
        found = [e for e in state[name] if e["operation_id"] == oid]
        return found[0] if len(found) == 1 else None

    def nested(name, event, oid):
        found = [e for e in state[name]
                 if e[event] is not None and e[event]["operation_id"] == oid]
        return found[0] if len(found) == 1 else None

    for entry in state["applied_operations"]:
        kind, oid, seq = entry["kind"], entry["operation_id"], entry["sequence"]
        out = {"mission_id": mission_id, "operation_id": oid, "sequence": seq,
               "progress": mp.progress_at(state, seq)}
        e = None
        if kind == ms.OPERATION_ACTIVATE_CONTRACT:
            e = one("contract_activations", oid)
            if e: out.update((k, e[k]) for k in (
                "activation_id", "revision", "proposal_digest_sha256",
                "contract_digest_sha256", "authorization_id"))
        elif kind == ms.OPERATION_RECORD_CLAIM:
            e = one("claims", oid)
            if e: out.update(claim_id=e["claim_id"], requirement_key=e["requirement_key"])
        elif kind == ms.OPERATION_RECORD_ARTIFACT:
            e = one("artifacts", oid)
            if e: out.update(artifact_id=e["artifact_id"], key=e["key"], role=e["role"])
        elif kind == ms.OPERATION_SUBMIT_EVIDENCE:
            e = one("evidence", oid)
            if e: out.update(evidence_id=e["evidence_id"],
                             requirement_key=e["requirement_key"], kind=e["kind"],
                             accepted=False)
        elif kind == ms.OPERATION_ACCEPT_EVIDENCE:
            e = nested("evidence", "acceptance", oid)
            if e: out.update(evidence_id=e["evidence_id"], accepted=True)
        elif kind == ms.OPERATION_INVALIDATE_EVIDENCE:
            e = nested("evidence", "invalidation", oid)
            if e: out.update(evidence_id=e["evidence_id"], invalidated=True)
        elif kind == ms.OPERATION_OPEN_BLOCKER:
            e = one("blockers", oid)
            if e: out.update(blocker_id=e["blocker_id"], key=e["key"], severity=e["severity"])
        elif kind == ms.OPERATION_RESOLVE_BLOCKER:
            e = nested("blockers", "resolution", oid)
            if e: out.update(blocker_id=e["blocker_id"], resolved=True,
                             evidence_id=e["resolution"]["evidence_id"])
        elif kind == ms.OPERATION_BIND_DEPENDENCY:
            e = one("dependencies", oid)
            if e: out.update(dependency_id=e["dependency_id"], slot_key=e["key"],
                             reference=e["reference"], new_binding=True)
        elif kind == ms.OPERATION_RESOLVE_DEPENDENCY:
            e = nested("dependencies", "resolution", oid)
            if e: out.update(dependency_id=e["dependency_id"], resolved=True,
                             evidence_id=e["resolution"]["evidence_id"])
        elif kind == ms.OPERATION_OBSERVE_RESOURCE_READINESS:
            e = one("resource_readiness", oid)
            if e: out.update(resource_key=e["resource_key"], status=e["status"])
        elif kind == ms.OPERATION_RECORD_CONTINUATION:
            e = one("continuations", oid)
            if e: out.update(attempt=e["attempt"],
                             attempts_remaining=max(0, max_attempts - e["attempt"]))
        elif kind == ms.OPERATION_RECORD_CHECKPOINT:
            e = one("checkpoints", oid)
            if e: out.update((k, e[k]) for k in (
                "checkpoint_id", "next_permitted_step", "refusal", "budget",
                "active_blocker_ids", "outstanding_dependency_ids"))
        else:
            c = state.get("closure")
            e = c if c is not None and c["operation_id"] == oid else None
            if e: out.update(reason=e["reason"], detail=e["detail"])
        if e is not None:
            entry["outcome"] = json.loads(json.dumps(out))
            from mission import state_reconcile as sr
            effect = None if (kind == ms.OPERATION_BIND_DEPENDENCY and not out.get(
                "new_binding", True)) else e
            entry["content_digest_sha256"] = ms.invocation_digest(
                kind, mission_id, seq - 1,
                sr._invocation_arguments(kind, effect, entry["outcome"], state))
    if not had_closure:
        state.pop("closure", None)
    return state


def reg(current_revision, revision_digests, progress, closure_revision=None,
        closure_digest=None):
    """One registry-view entry (see ``mission.store.registry_view``)."""
    return {
        "current_revision": current_revision,
        "revision_digests": dict(revision_digests),
        "progress": progress,
        "closure_activation_id": (None if closure_revision is None
                                  else hexid("mt", 0xC0 + closure_revision)),
        "closure_revision": closure_revision,
        "closure_proposal_digest_sha256": closure_digest,
    }


class StateFixture(unittest.TestCase):
    """Assemble one realistic state record through the constructors:
    activation, claim, artifact, evidence (submitted then accepted),
    blocker (opened then resolved), dependency, readiness observation,
    continuation, checkpoint. Every sub-record is produced by exactly one
    applied operation with a strictly increasing sequence."""

    MISSION = hexid("mn", 0x10)
    AUTH = hexid("ma", 0x20)

    def setUp(self):
        from mission import state as mission_state
        from mission import state_validation as mission_state_validation
        self.ms = mission_state
        self.msv = mission_state_validation
        self.context = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501",
        )
        self.clean_contract = mission_record.validate_proof_contract(contract())
        self.contract_digest = mission_record.proof_contract_digest(contract())
        self.proposal_digest = mission_record.proposal_digest(
            proposal(proof_contract=contract()))
        self.ops = 0

    def op(self, state, kind, applied_at, outcome=None):
        """Append an applied operation and return (operation_id, sequence,
        provenance) for the record it produces."""
        self.ops += 1
        operation_id = hexid("mo", 0x100 + self.ops)
        sequence = state["sequence"] + 1
        provenance = mission_record.provenance_record(
            self.context, applied_at, mission_record.REFERENCE_KIND_STATE_OPERATION,
            operation_id, state["mission_id"], 1,
        )
        self.ms.append_applied_operation(
            state, operation_id, kind, "c" * 64, applied_at, provenance,
            outcome or {"ok": True},
        )
        self.assertEqual(state["sequence"], sequence)
        return operation_id, sequence, provenance

    def build(self):
        ms = self.ms
        state = ms.new_state_record(self.MISSION, 1000)
        self.assertEqual(state["progress"], ms.PROGRESS_NOT_STARTED)
        # 1. activation
        op, seq, prov = self.op(state, ms.OPERATION_ACTIVATE_CONTRACT, 1001)
        self.activation_id = hexid("mt", 1)
        state["contract_activations"].append(ms.new_activation(
            self.activation_id, 1, self.proposal_digest, self.AUTH, "d" * 64,
            self.contract_digest, 1001, prov, op, seq,
        ))
        state["progress"] = ms.PROGRESS_IN_PROGRESS
        # 2. claim (never proof)
        op, seq, prov = self.op(state, ms.OPERATION_RECORD_CLAIM, 1002)
        state["claims"].append(ms.new_claim(
            hexid("mc", 1), self.activation_id, "tests_pass", "I ran them",
            1002, prov, op, seq,
        ))
        # 3. artifact (the required verification artifact, digest matches)
        op, seq, prov = self.op(state, ms.OPERATION_RECORD_ARTIFACT, 1003)
        self.artifact_id = hexid("mf", 1)
        state["artifacts"].append(ms.new_artifact(
            self.artifact_id, "test_log", mission_record.ARTIFACT_ROLE_VERIFICATION,
            ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log-1", HEX_A, True, [],
            1003, prov, op, seq,
        ))
        # 4. evidence submitted
        op, seq, prov = self.op(state, ms.OPERATION_SUBMIT_EVIDENCE, 1004)
        self.evidence_id = hexid("mv", 1)
        state["evidence"].append(ms.new_evidence(
            self.evidence_id, self.activation_id, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
            [self.artifact_id], 1004, prov, op, seq,
        ))
        # 5. evidence accepted (separate event, separate provenance)
        op, seq, prov = self.op(state, ms.OPERATION_ACCEPT_EVIDENCE, 1005)
        state["evidence"][0]["acceptance"] = ms.new_acceptance(
            1005, "e" * 64, self.activation_id, prov, op, seq)
        # 6. blocker opened (HARD: key not permitted by policy)
        op, seq, prov = self.op(state, ms.OPERATION_OPEN_BLOCKER, 1006)
        self.blocker_id = hexid("mb", 1)
        state["blockers"].append(ms.new_blocker(
            self.blocker_id, self.activation_id, "disk_full",
            ms.BLOCKER_SEVERITY_HARD, "no space", 1006, prov, op, seq,
        ))
        state["progress"] = ms.PROGRESS_BLOCKED
        # 7. blocker resolved with the accepted evidence
        op, seq, prov = self.op(state, ms.OPERATION_RESOLVE_BLOCKER, 1007)
        state["blockers"][0]["resolution"] = ms.new_resolution(
            1007, self.evidence_id, prov, op, seq)
        state["progress"] = ms.PROGRESS_IN_PROGRESS
        # 8. dependency bound to the required slot
        op, seq, prov = self.op(state, ms.OPERATION_BIND_DEPENDENCY, 1008)
        self.dependency_id = hexid("mx", 1)
        state["dependencies"].append(ms.new_dependency(
            self.dependency_id, self.activation_id, "upstream",
            mission_record.DEPENDENCY_KIND_MISSION, MISSION_X, 1008, prov, op, seq,
        ))
        # 9. readiness observation
        op, seq, prov = self.op(state, ms.OPERATION_OBSERVE_RESOURCE_READINESS, 1009)
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, 1009, prov, op, seq,
        ))
        # 10. continuation
        op, seq, prov = self.op(state, ms.OPERATION_RECORD_CONTINUATION, 1010)
        state["continuations"].append(ms.new_continuation(
            1, "retry after fix", 1010, prov, op, seq,
        ))
        # 11. checkpoint (derived fields recomputed by section C/D)
        op, seq, prov = self.op(state, ms.OPERATION_RECORD_CHECKPOINT, 1011)
        self.checkpoint_id = hexid("mk", 1)
        state["checkpoints"].append(ms.new_checkpoint(
            self.checkpoint_id, self.activation_id, 1011,
            ["ran tests"], ["resolve upstream"],
            {"revision": 1, "proposal_digest_sha256": self.proposal_digest,
             "contract_digest_sha256": self.contract_digest},
            [], [self.dependency_id],
            {"attempts_consumed": 1, "attempts_remaining": 2,
             "checkpoints_consumed": 1, "checkpoints_remaining": 7},
            "retry when upstream completes", "stop at budget",
            ms.NEXT_STEP_RESOLVE_DEPENDENCIES, None, prov, op, seq,
        ))
        state["updated_at"] = 1011
        return seal_state(state)

    def retire_continuation(self, state, claim_number):
        """Turn the fixture's continuation (operation 10) into a claim so a
        variant whose history is BLOCKED there stays historically possible
        (R-39)."""
        ms = self.ms
        op = state["applied_operations"][9]
        op["kind"] = ms.OPERATION_RECORD_CLAIM
        state["continuations"] = []
        state["claims"].append(ms.new_claim(
            hexid("mc", claim_number), self.activation_id, "tests_pass", "again",
            op["applied_at"], op["provenance"], op["operation_id"], op["sequence"]))
        state["checkpoints"][0]["budget"] = dict(state["checkpoints"][0]["budget"],
                                                 attempts_consumed=0,
                                                 attempts_remaining=3)
        return state

    def validate(self, state):
        seal_state(state, self.clean_contract["continuation_budget"]["max_attempts"])
        return self.msv.validate_state_record(state)

    def tamper(self, state, path, value):
        """Return a deep copy with the value at dotted ``path`` replaced."""
        copy_ = json.loads(json.dumps(state))
        node = copy_
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        last = parts[-1]
        if last.isdigit():
            node[int(last)] = value
        else:
            node[last] = value
        return copy_


class BStateRecordTests(StateFixture):

    def test_B1_progress_vocabulary_and_transition_table(self):
        ms = self.ms
        self.assertEqual(ms.PROGRESS_STATES, (
            "NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED",
            "CLOSED_UNSUCCESSFUL", "ABANDONED",
        ))
        self.assertNotIn("RUNNING", ms.PROGRESS_STATES)
        self.assertEqual(set(ms.PROGRESS_TRANSITIONS), set(ms.PROGRESS_STATES))
        self.assertEqual(ms.PROGRESS_TRANSITIONS["NOT_STARTED"],
                         frozenset(("IN_PROGRESS", "CLOSED_UNSUCCESSFUL", "ABANDONED")))
        self.assertEqual(ms.PROGRESS_TRANSITIONS["IN_PROGRESS"], frozenset((
            "IN_PROGRESS", "BLOCKED", "COMPLETED", "CLOSED_UNSUCCESSFUL",
            "ABANDONED")))
        self.assertEqual(ms.PROGRESS_TRANSITIONS["BLOCKED"],
                         frozenset(("IN_PROGRESS", "CLOSED_UNSUCCESSFUL", "ABANDONED")))
        for terminal in ("COMPLETED", "CLOSED_UNSUCCESSFUL", "ABANDONED"):
            self.assertEqual(ms.PROGRESS_TRANSITIONS[terminal], frozenset())
            self.assertIn(terminal, ms.TERMINAL_PROGRESS_STATES)
            refuses(self, ms.PROBLEM_PROGRESS_TERMINAL,
                    ms.validate_progress_transition, terminal, "IN_PROGRESS")
        # Task 4's mission["state"] table is untouched by this module.
        self.assertEqual(mission_record.ALLOWED_TRANSITIONS["AUTHORIZED"],
                         frozenset(("AWAITING_DECISION",)))
        refuses(self, ms.PROBLEM_PROGRESS_TRANSITION,
                ms.validate_progress_transition, "NOT_STARTED", "COMPLETED")
        refuses(self, ms.PROBLEM_PROGRESS_TRANSITION,
                ms.validate_progress_transition, "BLOCKED", "COMPLETED")
        refuses(self, ms.PROBLEM_PROGRESS_UNKNOWN,
                ms.validate_progress_transition, "RUNNING", "IN_PROGRESS")
        self.assertEqual(ms.validate_progress_transition("IN_PROGRESS", "IN_PROGRESS"),
                         "IN_PROGRESS")
        # The module docstring states the limit explicitly.
        doc = ms.__doc__
        self.assertIn("descriptive progress lifecycle", doc)
        self.assertIn("not execution", doc)
        for unwired in ("RUNNING", "CANCELLED"):
            self.assertIn(unwired, doc)

    def test_B2_fresh_record_and_full_record_validate_and_round_trip(self):
        fresh = self.ms.new_state_record(self.MISSION, 5)
        self.assertEqual(set(fresh), set(self.ms.STATE_RECORD_KEYS))
        self.assertEqual(fresh["sequence"], 0)
        self.assertEqual(fresh["applied_operations"], [])
        self.assertIsNone(fresh["closure"])
        self.validate(fresh)
        state = self.build()
        self.assertIs(self.validate(state), state)
        self.assertEqual(state["sequence"], 11)
        self.assertEqual(len(state["applied_operations"]), 11)
        # JSON round trip is exact.
        self.assertEqual(self.validate(json.loads(json.dumps(state))), state)

    def test_B3_closed_keys_and_identity_grammar_everywhere(self):
        state = self.build()
        ms = self.ms
        refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate,
                dict(state, extra=1))
        refuses(self, mission_record.PROBLEM_MISSING_KEY, self.validate,
                {k: v for k, v in state.items() if k != "closure"})
        for path, problem in (
            ("mission_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("contract_activations.0.activation_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("contract_activations.0.authorization_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("claims.0.claim_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("artifacts.0.artifact_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("evidence.0.evidence_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("blockers.0.blocker_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("dependencies.0.dependency_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("checkpoints.0.checkpoint_id", mission_record.PROBLEM_ID_GRAMMAR),
            ("applied_operations.0.operation_id", mission_record.PROBLEM_ID_GRAMMAR),
        ):
            with self.subTest(path):
                refuses(self, problem, self.validate,
                        self.tamper(state, path, "di-" + "0" * 32))
        for list_name in ("contract_activations", "claims", "artifacts",
                          "evidence", "blockers", "dependencies", "checkpoints",
                          "resource_readiness", "continuations",
                          "applied_operations"):
            with self.subTest(list_name):
                bad = json.loads(json.dumps(state))
                bad[list_name][0]["surprise"] = 1
                refuses(self, mission_record.PROBLEM_UNKNOWN_KEY, self.validate, bad)
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "schema_version", 2))
        refuses(self, ms.PROBLEM_PROGRESS_UNKNOWN, self.validate,
                self.tamper(state, "progress", "RUNNING"))

    def test_B4_sequence_and_operation_bindings_are_exact(self):
        state = self.build()
        ms = self.ms
        # The record sequence is the number of applied operations.
        refuses(self, ms.PROBLEM_SEQUENCE, self.validate,
                self.tamper(state, "sequence", 12))
        # Applied operations are contiguous from 1.
        refuses(self, ms.PROBLEM_SEQUENCE, self.validate,
                self.tamper(state, "applied_operations.3.sequence", 9))
        # Every sub-record names the operation that produced it, at the
        # sequence that operation holds, with the matching kind.
        refuses(self, ms.PROBLEM_OPERATION_BINDING, self.validate,
                self.tamper(state, "evidence.0.sequence", 3))
        refuses(self, ms.PROBLEM_OPERATION_BINDING, self.validate,
                self.tamper(state, "evidence.0.operation_id", hexid("mo", 0x999)))
        refuses(self, ms.PROBLEM_OPERATION_BINDING, self.validate,
                self.tamper(state, "applied_operations.3.kind",
                            ms.OPERATION_RECORD_CLAIM))
        # Nested events (acceptance) bind their own later operation.
        refuses(self, ms.PROBLEM_OPERATION_BINDING, self.validate,
                self.tamper(state, "evidence.0.acceptance.sequence", 4))
        # Duplicate operation ids refuse.
        dup = json.loads(json.dumps(state))
        dup["applied_operations"][1]["operation_id"] = (
            dup["applied_operations"][0]["operation_id"])
        refuses(self, ms.PROBLEM_OPERATION_BINDING, self.validate, dup)
        # Provenance references the operation and this mission.
        refuses(self, mission_record.PROBLEM_PROVENANCE, self.validate,
                self.tamper(state, "applied_operations.0.provenance.reference_id",
                            hexid("mo", 0x998)))
        refuses(self, mission_record.PROBLEM_PROVENANCE, self.validate,
                self.tamper(state, "claims.0.provenance.mission_id", MISSION_X))
        # A foreign reference kind fails the id grammar before the binding.
        refuses(self, mission_record.PROBLEM_ID_GRAMMAR, self.validate,
                self.tamper(state, "claims.0.provenance.reference_kind", "decision"))
        # Applied operation content digests are 64 hex, and (R-34) must be
        # the digest re-derived from the effect payload; validated RAW here
        # because the fixture seal would re-derive it.
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.msv.validate_state_record,
                self.tamper(state, "applied_operations.0.content_digest_sha256", "zz"))
        refuses(self, ms.PROBLEM_INVOCATION_MISMATCH, self.msv.validate_state_record,
                self.tamper(state, "applied_operations.0.content_digest_sha256",
                            "f" * 64))
        refuses(self, ms.PROBLEM_INVOCATION_MISMATCH, self.msv.validate_state_record,
                self.tamper(state, "claims.0.statement", "I did NOT run them"))

    def test_B5_records_bind_the_activation_current_at_their_sequence(self):
        state = self.build()
        ms = self.ms
        for path in ("claims.0.activation_id", "evidence.0.activation_id",
                     "blockers.0.activation_id", "dependencies.0.activation_id",
                     "checkpoints.0.activation_id"):
            with self.subTest(path):
                refuses(self, ms.PROBLEM_ACTIVATION_BINDING, self.validate,
                        self.tamper(state, path, hexid("mt", 7)))
        # Activation bindings: revision, digests, times.
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "contract_activations.0.revision", 0))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "contract_activations.0.contract_digest_sha256",
                            "x" * 64))
        # A second activation must be for a strictly later revision.
        second = json.loads(json.dumps(state))
        op, seq, prov = self.op(second, ms.OPERATION_ACTIVATE_CONTRACT, 1012)
        second["contract_activations"].append(ms.new_activation(
            hexid("mt", 2), 1, self.proposal_digest, self.AUTH, "d" * 64,
            self.contract_digest, 1012, prov, op, seq,
        ))
        refuses(self, ms.PROBLEM_ACTIVATION_ORDER, self.validate, second)
        second["contract_activations"][1]["revision"] = 2
        # R-36: the activation's provenance (and its operation's) carry the
        # revision it activates.
        refuses(self, ms.PROBLEM_PROVENANCE_MISMATCH, self.validate, second)
        second["contract_activations"][1]["provenance"]["revision"] = 2
        second["applied_operations"][-1]["provenance"]["revision"] = 2
        self.validate(second)
        # Checkpoint refs must agree with its activation.
        refuses(self, ms.PROBLEM_CHECKPOINT_REFS, self.validate,
                self.tamper(state, "checkpoints.0.refs.contract_digest_sha256",
                            "f" * 64))
        refuses(self, ms.PROBLEM_CHECKPOINT_REFS, self.validate,
                self.tamper(state, "checkpoints.0.refs.revision", 2))

    def test_B6_evidence_shape_separates_submission_from_acceptance(self):
        state = self.build()
        ms = self.ms
        evidence = state["evidence"][0]
        self.assertEqual(sorted(evidence), sorted(ms.EVIDENCE_KEYS))
        self.assertEqual(sorted(evidence["acceptance"]), sorted(ms.ACCEPTANCE_KEYS))
        self.assertIsNone(evidence["invalidation"])
        self.assertNotEqual(evidence["submitted_at"],
                            evidence["acceptance"]["accepted_at"])
        # Acceptance carries its own digest that must match the submission.
        refuses(self, ms.PROBLEM_EVIDENCE_DIGEST, self.validate,
                self.tamper(state, "evidence.0.acceptance.content_digest_sha256",
                            "f" * 64))
        # A recording time must equal its operation's applied_at (R-25.3).
        refuses(self, ms.PROBLEM_TIME_INCONSISTENT, self.validate,
                self.tamper(state, "evidence.0.acceptance.accepted_at", 3))
        # Non-satisfying kinds may be recorded but never accepted: a stored
        # accepted NARRATIVE_CLAIM or PROCESS_EXIT is malformed.
        for kind in mission_record.NON_SATISFYING_EVIDENCE_KINDS:
            with self.subTest(kind):
                refuses(self, ms.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING,
                        self.validate, self.tamper(state, "evidence.0.kind", kind))
                unaccepted = self.tamper(state, "evidence.0.kind", kind)
                unaccepted["evidence"][0]["acceptance"] = None
                unaccepted["blockers"][0]["resolution"] = None
                unaccepted["progress"] = ms.PROGRESS_BLOCKED
                unaccepted["applied_operations"][4]["kind"] = (
                    ms.OPERATION_RECORD_CLAIM)
                unaccepted["claims"].append(ms.new_claim(
                    hexid("mc", 2), self.activation_id, "tests_pass", "again",
                    1005, unaccepted["applied_operations"][4]["provenance"],
                    unaccepted["applied_operations"][4]["operation_id"], 5,
                ))
                unaccepted["applied_operations"][6]["kind"] = (
                    ms.OPERATION_RECORD_CLAIM)
                unaccepted["claims"].append(ms.new_claim(
                    hexid("mc", 3), self.activation_id, "tests_pass", "again",
                    1007, unaccepted["applied_operations"][6]["provenance"],
                    unaccepted["applied_operations"][6]["operation_id"], 7,
                ))
                unaccepted["checkpoints"][0]["active_blocker_ids"] = [self.blocker_id]
                self.retire_continuation(unaccepted, 40)
                self.validate(unaccepted)
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "evidence.0.kind", "MAGIC"))
        # Referenced artifacts must exist and precede the evidence.
        refuses(self, ms.PROBLEM_UNKNOWN_ARTIFACT, self.validate,
                self.tamper(state, "evidence.0.artifact_ids", [hexid("mf", 9)]))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "evidence.0.artifact_ids",
                            [self.artifact_id, self.artifact_id]))
        # Invalidation is its own recorded event.
        op_state = json.loads(json.dumps(state))
        op, seq, prov = self.op(op_state, ms.OPERATION_INVALIDATE_EVIDENCE, 1012)
        op_state["evidence"][0]["invalidation"] = ms.new_invalidation(
            1012, "superseded by rerun", prov, op, seq)
        self.validate(op_state)
        self.assertEqual(sorted(op_state["evidence"][0]["invalidation"]),
                         sorted(ms.INVALIDATION_KEYS))

    def test_B7_artifacts_are_metadata_with_original_input_linkage(self):
        state = self.build()
        ms = self.ms
        artifact = state["artifacts"][0]
        self.assertEqual(sorted(artifact), sorted(ms.ARTIFACT_KEYS))
        self.assertIn(ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, ms.LOCATOR_KINDS)
        # An original input has no derivation; produced/verification may
        # derive from EARLIER artifacts only, never from themselves.
        refuses(self, ms.PROBLEM_ARTIFACT_DERIVATION, self.validate,
                self.tamper(state, "artifacts.0.derived_from", [self.artifact_id]))
        original = self.tamper(state, "artifacts.0.role",
                               mission_record.ARTIFACT_ROLE_ORIGINAL_INPUT)
        original["artifacts"][0]["derived_from"] = [hexid("mf", 5)]
        refuses(self, ms.PROBLEM_ARTIFACT_DERIVATION, self.validate, original)
        refuses(self, ms.PROBLEM_UNKNOWN_ARTIFACT, self.validate,
                self.tamper(state, "artifacts.0.derived_from", [hexid("mf", 5)]))
        refuses(self, mission_record.PROBLEM_BAD_TYPE, self.validate,
                self.tamper(state, "artifacts.0.available", "yes"))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "artifacts.0.locator_kind", "http_fetch"))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                self.tamper(state, "artifacts.0.locator",
                            "x" * (ms.MAX_LOCATOR_CHARS + 1)))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "artifacts.0.content_digest_sha256", "short"))
        # A null digest and a null key are legal (non-required artifact).
        free = self.tamper(state, "artifacts.0.content_digest_sha256", None)
        free["artifacts"][0]["key"] = None
        self.validate(free)
        # A produced artifact deriving from an earlier original validates.
        chain = json.loads(json.dumps(state))
        op, seq, prov = self.op(chain, ms.OPERATION_RECORD_ARTIFACT, 1012)
        chain["artifacts"].append(ms.new_artifact(
            hexid("mf", 2), None, mission_record.ARTIFACT_ROLE_PRODUCED,
            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, "receipt:abc", None,
            False, [self.artifact_id], 1012, prov, op, seq,
        ))
        self.validate(chain)

    def test_B8_blockers_resolve_only_with_accepted_evidence(self):
        state = self.build()
        ms = self.ms
        blocker = state["blockers"][0]
        self.assertEqual(sorted(blocker), sorted(ms.BLOCKER_KEYS))
        self.assertEqual(sorted(blocker["resolution"]), sorted(ms.RESOLUTION_KEYS))
        self.assertEqual(ms.BLOCKER_SEVERITIES, ("HARD", "DEGRADED"))
        refuses(self, ms.PROBLEM_UNKNOWN_EVIDENCE, self.validate,
                self.tamper(state, "blockers.0.resolution.evidence_id",
                            hexid("mv", 9)))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "blockers.0.severity", "SOFT"))
        refuses(self, ms.PROBLEM_TIME_INCONSISTENT, self.validate,
                self.tamper(state, "blockers.0.resolution.resolved_at", 1))
        # Resolution evidence must be ACCEPTED (before the resolution).
        unaccepted = json.loads(json.dumps(state))
        unaccepted["evidence"][0]["acceptance"] = None
        unaccepted["applied_operations"][4]["kind"] = ms.OPERATION_RECORD_CLAIM
        unaccepted["claims"].append(ms.new_claim(
            hexid("mc", 2), self.activation_id, "tests_pass", "again", 1005,
            unaccepted["applied_operations"][4]["provenance"],
            unaccepted["applied_operations"][4]["operation_id"], 5,
        ))
        refuses(self, ms.PROBLEM_EVIDENCE_NOT_ACCEPTED, self.validate, unaccepted)
        # Progress agrees with blockers: BLOCKED iff an active HARD blocker.
        refuses(self, ms.PROBLEM_PROGRESS_DISAGREES, self.validate,
                self.tamper(state, "progress", ms.PROGRESS_BLOCKED))
        active = json.loads(json.dumps(state))
        active["blockers"][0]["resolution"] = None
        active["applied_operations"][6]["kind"] = ms.OPERATION_RECORD_CLAIM
        active["claims"].append(ms.new_claim(
            hexid("mc", 3), self.activation_id, "tests_pass", "again", 1007,
            active["applied_operations"][6]["provenance"],
            active["applied_operations"][6]["operation_id"], 7,
        ))
        active["checkpoints"][0]["active_blocker_ids"] = [self.blocker_id]
        self.retire_continuation(active, 41)
        refuses(self, ms.PROBLEM_PROGRESS_DISAGREES, self.validate, active)
        active["progress"] = ms.PROGRESS_BLOCKED
        self.validate(active)
        # A DEGRADED active blocker does not make progress BLOCKED.
        active["blockers"][0]["severity"] = ms.BLOCKER_SEVERITY_DEGRADED
        refuses(self, ms.PROBLEM_PROGRESS_DISAGREES, self.validate, active)
        active["progress"] = ms.PROGRESS_IN_PROGRESS
        self.validate(active)

    def test_B9_dependencies_are_mission_safe_and_slots_bind_once(self):
        state = self.build()
        ms = self.ms
        dependency = state["dependencies"][0]
        self.assertEqual(sorted(dependency), sorted(ms.DEPENDENCY_KEYS))
        self.assertIsNone(dependency["resolution"])
        refuses(self, ms.PROBLEM_DEPENDENCY_SELF, self.validate,
                self.tamper(state, "dependencies.0.reference", self.MISSION))
        refuses(self, mission_record.PROBLEM_ID_GRAMMAR, self.validate,
                self.tamper(state, "dependencies.0.reference", "not-a-mission"))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "dependencies.0.kind", "SERVICE"))
        # A bound slot key appears at most once per activation: a second
        # record for the same slot is a rebind and is malformed.
        rebind = json.loads(json.dumps(state))
        op, seq, prov = self.op(rebind, ms.OPERATION_BIND_DEPENDENCY, 1012)
        rebind["dependencies"].append(ms.new_dependency(
            hexid("mx", 2), self.activation_id, "upstream",
            mission_record.DEPENDENCY_KIND_MISSION, hexid("mn", 0x33), 1012,
            prov, op, seq,
        ))
        refuses(self, ms.PROBLEM_DEPENDENCY_REBIND, self.validate, rebind)
        # An extra (unkeyed) resource dependency is an observation only.
        rebind["dependencies"][1]["key"] = None
        rebind["dependencies"][1]["kind"] = mission_record.DEPENDENCY_KIND_RESOURCE
        rebind["dependencies"][1]["reference"] = "shared-cache"
        self.validate(rebind)
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                self.tamper(rebind, "dependencies.1.reference",
                            "r" * (ms.MAX_RESOURCE_REFERENCE_CHARS + 1)))
        # Resolution mirrors blockers: accepted evidence only.
        resolved = json.loads(json.dumps(state))
        op, seq, prov = self.op(resolved, ms.OPERATION_RESOLVE_DEPENDENCY, 1012)
        resolved["dependencies"][0]["resolution"] = ms.new_resolution(
            1012, self.evidence_id, prov, op, seq)
        resolved["checkpoints"][0]["outstanding_dependency_ids"] = [self.dependency_id]
        self.validate(resolved)
        refuses(self, ms.PROBLEM_UNKNOWN_EVIDENCE, self.validate,
                self.tamper(resolved, "dependencies.0.resolution.evidence_id",
                            hexid("mv", 4)))

    def test_B10_readiness_continuation_checkpoint_and_closure_shapes(self):
        state = self.build()
        ms = self.ms
        self.assertEqual(ms.READINESS_STATUSES, ("READY", "NOT_READY", "UNKNOWN"))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "resource_readiness.0.status", "MAYBE"))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                self.tamper(state, "resource_readiness.0.resource_key",
                            "k" * (mission_record.MAX_CONTRACT_KEY_CHARS + 1)))
        # Continuation attempts are numbered contiguously from 1.
        refuses(self, ms.PROBLEM_CONTINUATION_ORDER, self.validate,
                self.tamper(state, "continuations.0.attempt", 2))
        # Checkpoint: exactly one of next step / refusal; closed vocabularies.
        checkpoint = state["checkpoints"][0]
        self.assertEqual(sorted(checkpoint), sorted(ms.CHECKPOINT_KEYS))
        both = self.tamper(state, "checkpoints.0.refusal",
                           {"problem": "mission_state_budget_exhausted",
                            "detail": "x"})
        refuses(self, ms.PROBLEM_CHECKPOINT_STEP, self.validate, both)
        neither = self.tamper(state, "checkpoints.0.next_permitted_step", None)
        refuses(self, ms.PROBLEM_CHECKPOINT_STEP, self.validate, neither)
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "checkpoints.0.next_permitted_step", "LAUNCH"))
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(state, "checkpoints.0.budget.attempts_remaining", -1))
        refuses(self, ms.PROBLEM_UNKNOWN_BLOCKER, self.validate,
                self.tamper(state, "checkpoints.0.active_blocker_ids",
                            [hexid("mb", 9)]))
        refuses(self, ms.PROBLEM_UNKNOWN_DEPENDENCY, self.validate,
                self.tamper(state, "checkpoints.0.outstanding_dependency_ids",
                            [hexid("mx", 9)]))
        refuses(self, mission_record.PROBLEM_TOO_LARGE, self.validate,
                self.tamper(state, "checkpoints.0.completed_work",
                            ["w"] * (ms.MAX_WORK_ITEMS + 1)))
        refusal_only = self.tamper(state, "checkpoints.0.next_permitted_step", None)
        refusal_only["checkpoints"][0]["refusal"] = {
            "problem": ms.PROBLEM_BUDGET_EXHAUSTED, "detail": "3 of 3 attempts used",
        }
        self.validate(refusal_only)
        refuses(self, mission_record.PROBLEM_BAD_VALUE, self.validate,
                self.tamper(refusal_only, "checkpoints.0.refusal.problem",
                            "not_a_mission_code"))
        # Closure: present iff terminal, with a reason from the closed table
        # for that outcome, recorded by the LAST operation.
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(state, "progress", ms.PROGRESS_COMPLETED))
        closed = json.loads(json.dumps(state))
        op, seq, prov = self.op(closed, ms.OPERATION_ABANDON, 1012)
        closed["closure"] = ms.new_closure(
            ms.PROGRESS_ABANDONED, ms.CLOSURE_REASON_CALLER_ABANDONED,
            "operator gave up", 1012, self.activation_id, prov, op, seq)
        closed["progress"] = ms.PROGRESS_ABANDONED
        self.validate(closed)
        self.assertEqual(sorted(closed["closure"]), sorted(ms.CLOSURE_KEYS))
        self.assertIn("activation_id", ms.CLOSURE_KEYS)
        # The closure binds the activation current at its sequence (R-21.2).
        refuses(self, ms.PROBLEM_ACTIVATION_BINDING, self.validate,
                self.tamper(closed, "closure.activation_id", hexid("mt", 7)))
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(closed, "closure.activation_id", None))
        # Abandoning before any activation binds no activation.
        never = ms.new_state_record(self.MISSION, 1)
        self.ops = 500
        op2, seq2, prov2 = self.op(never, ms.OPERATION_ABANDON, 2)
        never["closure"] = ms.new_closure(
            ms.PROGRESS_ABANDONED, ms.CLOSURE_REASON_CALLER_ABANDONED, "never",
            2, None, prov2, op2, seq2)
        never["progress"] = ms.PROGRESS_ABANDONED
        self.validate(never)
        # A COMPLETED closure always names the activation it completed under.
        done = json.loads(json.dumps(state))
        op3, seq3, prov3 = self.op(done, ms.OPERATION_COMPLETE, 1012)
        done["closure"] = ms.new_closure(
            ms.PROGRESS_COMPLETED, ms.CLOSURE_REASON_PROOF_COMPLETE, "all proof",
            1012, self.activation_id, prov3, op3, seq3)
        done["progress"] = ms.PROGRESS_COMPLETED
        self.validate(done)
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(done, "closure.activation_id", None))
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(closed, "closure.reason",
                            ms.CLOSURE_REASON_BUDGET_EXHAUSTED))
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(closed, "progress", ms.PROGRESS_CLOSED_UNSUCCESSFUL))
        refuses(self, ms.PROBLEM_CLOSURE, self.validate,
                self.tamper(closed, "closure.progress", ms.PROGRESS_IN_PROGRESS))
        # COMPLETED has exactly one reason; exhaustion is never one of them.
        self.assertEqual(ms.CLOSURE_REASONS_BY_PROGRESS[ms.PROGRESS_COMPLETED],
                         (ms.CLOSURE_REASON_PROOF_COMPLETE,))
        self.assertIn(ms.CLOSURE_REASON_BUDGET_EXHAUSTED,
                      ms.CLOSURE_REASONS_BY_PROGRESS[ms.PROGRESS_CLOSED_UNSUCCESSFUL])
        self.assertNotIn(ms.CLOSURE_REASON_BUDGET_EXHAUSTED,
                         ms.CLOSURE_REASONS_BY_PROGRESS[ms.PROGRESS_COMPLETED])
        # A closure is not followed by anything: it holds the last sequence.
        late = json.loads(json.dumps(closed))
        self.op(late, ms.OPERATION_RECORD_CLAIM, 1013)
        refuses(self, ms.PROBLEM_CLOSURE, self.validate, late)
        # NOT_STARTED holds no activation; an activation demands progress.
        refuses(self, ms.PROBLEM_PROGRESS_DISAGREES, self.validate,
                self.tamper(state, "progress", ms.PROGRESS_NOT_STARTED))

    def test_B11_caps_are_module_constants_and_refuse_at_the_bound(self):
        ms = self.ms
        for name in ("MAX_CONTRACT_ACTIVATIONS", "MAX_CLAIMS", "MAX_EVIDENCE_RECORDS",
                     "MAX_ARTIFACT_RECORDS", "MAX_BLOCKER_RECORDS",
                     "MAX_CHECKPOINT_RECORDS", "MAX_DEPENDENCY_RECORDS",
                     "MAX_RESOURCE_READINESS_OBSERVATIONS",
                     "MAX_CONTINUATION_RECORDS", "MAX_APPLIED_OPERATIONS",
                     "MAX_CLAIM_STATEMENT_CHARS", "MAX_BLOCKER_DESCRIPTION_CHARS",
                     "MAX_LOCATOR_CHARS", "MAX_ARTIFACT_LINKS", "MAX_WORK_ITEMS",
                     "MAX_WORK_ITEM_CHARS", "MAX_CONDITION_CHARS",
                     "MAX_STATE_REASON_CHARS", "MAX_RESOURCE_REFERENCE_CHARS"):
            self.assertIsInstance(getattr(ms, name), int, name)
            self.assertGreater(getattr(ms, name), 0, name)
        self.assertGreaterEqual(ms.MAX_CONTINUATION_RECORDS,
                                mission_record.MAX_CONTINUATION_ATTEMPTS)
        self.assertGreaterEqual(ms.MAX_CHECKPOINT_RECORDS,
                                mission_record.MAX_CONTINUATION_CHECKPOINTS)
        # Readiness observations: fill to the cap, one more refuses.
        state = self.ms.new_state_record(self.MISSION, 1)
        op, seq, prov = self.op(state, ms.OPERATION_ACTIVATE_CONTRACT, 2)
        state["contract_activations"].append(ms.new_activation(
            hexid("mt", 1), 1, self.proposal_digest, self.AUTH, "d" * 64,
            self.contract_digest, 2, prov, op, seq))
        state["progress"] = ms.PROGRESS_IN_PROGRESS
        for index in range(ms.MAX_RESOURCE_READINESS_OBSERVATIONS):
            op, seq, prov = self.op(state, ms.OPERATION_OBSERVE_RESOURCE_READINESS, 3)
            state["resource_readiness"].append(ms.new_readiness_observation(
                "build_host", ms.READINESS_UNKNOWN, 3, prov, op, seq))
        state["updated_at"] = 3
        self.validate(state)
        op, seq, prov = self.op(state, ms.OPERATION_OBSERVE_RESOURCE_READINESS, 3)
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_UNKNOWN, 3, prov, op, seq))
        refuses(self, ms.PROBLEM_STATE_FULL, self.validate, state)
        self.assertEqual(len(state["resource_readiness"]),
                         ms.MAX_RESOURCE_READINESS_OBSERVATIONS + 1)


# ====================================================================
# C. Pure deterministic evaluators
# ====================================================================


class CProgressTests(StateFixture):

    def setUp(self):
        super(CProgressTests, self).setUp()
        from mission import progress as mission_progress
        self.mp = mission_progress
        self.state = self.build()
        self.now = 1011

    def status(self, state, now=None):
        return self.mp.evaluate_proof(
            self.clean_contract, state, self.activation_id,
            self.now if now is None else now,
        )["requirements"]["tests_pass"]

    def with_claim_instead_of(self, state, index, applied_at):
        """Retarget applied operation ``index`` to a harmless claim so a
        nested event can be dropped while the record stays well formed."""
        state["applied_operations"][index]["kind"] = self.ms.OPERATION_RECORD_CLAIM
        op = state["applied_operations"][index]
        state["claims"].append(self.ms.new_claim(
            hexid("mc", 50 + index), self.activation_id, "tests_pass", "x",
            applied_at, op["provenance"], op["operation_id"], op["sequence"],
        ))
        state["claims"].sort(key=lambda c: c["sequence"])
        return state

    def test_C1_requirement_status_covers_every_vocabulary_member(self):
        mp = self.mp
        self.assertEqual(mp.REQUIREMENT_STATUSES, (
            "SATISFIED", "MISSING", "SUBMITTED_NOT_ACCEPTED", "STALE",
            "MISMATCHED", "INVALIDATED", "CONTRADICTED",
        ))
        self.assertEqual(self.status(self.state), mp.REQUIREMENT_SATISFIED)
        self.assertTrue(mp.evaluate_proof(self.clean_contract, self.state,
                                          self.activation_id, self.now)["satisfied"])
        # MISSING: no evidence under the current activation.
        missing = json.loads(json.dumps(self.state))
        self.assertEqual(mp.evaluate_proof(self.clean_contract, missing,
                                           hexid("mt", 9), self.now)
                         ["requirements"]["tests_pass"], mp.REQUIREMENT_MISSING)
        # SUBMITTED_NOT_ACCEPTED: acceptance is a separate event.
        submitted = json.loads(json.dumps(self.state))
        submitted["evidence"][0]["acceptance"] = None
        self.assertEqual(self.status(submitted), mp.REQUIREMENT_SUBMITTED_NOT_ACCEPTED)
        # STALE: past the requirement's bound (3600 s after acceptance).
        self.assertEqual(self.status(self.state, now=1005 + 3600),
                         mp.REQUIREMENT_SATISFIED)
        self.assertEqual(self.status(self.state, now=1005 + 3601),
                         mp.REQUIREMENT_STALE)
        # A clock before the acceptance is also conservatively stale.
        self.assertEqual(self.status(self.state, now=1004), mp.REQUIREMENT_STALE)
        # INVALIDATED
        invalidated = json.loads(json.dumps(self.state))
        invalidated["evidence"][0]["invalidation"] = {
            "invalidated_at": 1010, "reason": "rerun", "provenance":
            invalidated["evidence"][0]["provenance"], "operation_id":
            hexid("mo", 0x777), "sequence": 12,
        }
        self.assertEqual(self.status(invalidated), mp.REQUIREMENT_INVALIDATED)
        # CONTRADICTED: two accepted, non-invalidated records, different digests.
        contradicted = json.loads(json.dumps(self.state))
        second = json.loads(json.dumps(contradicted["evidence"][0]))
        second["evidence_id"] = hexid("mv", 2)
        second["content_digest_sha256"] = "9" * 64
        second["acceptance"]["content_digest_sha256"] = "9" * 64
        contradicted["evidence"].append(second)
        self.assertEqual(self.status(contradicted), mp.REQUIREMENT_CONTRADICTED)
        # Two accepted records with the SAME digest agree: still satisfied.
        agreeing = json.loads(json.dumps(self.state))
        agreeing["evidence"].append(json.loads(json.dumps(agreeing["evidence"][0])))
        agreeing["evidence"][1]["evidence_id"] = hexid("mv", 2)
        self.assertEqual(self.status(agreeing), mp.REQUIREMENT_SATISFIED)
        # MISMATCHED, each way a required artifact can fail (R-12, R-19).
        for path, value in (
            ("artifacts.0.available", False),
            ("artifacts.0.content_digest_sha256", "0" * 64),
            ("artifacts.0.content_digest_sha256", None),
            ("artifacts.0.role", mission_record.ARTIFACT_ROLE_PRODUCED),
            ("artifacts.0.key", "other_log"),
            ("evidence.0.artifact_ids", []),
            ("evidence.0.kind", mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST),
        ):
            with self.subTest(path):
                self.assertEqual(self.status(self.tamper(self.state, path, value)),
                                 mp.REQUIREMENT_MISMATCHED)
        # An accepted digest that disagrees with the submitted one is
        # MISMATCHED for the evaluator (the store refuses it as malformed).
        self.assertEqual(self.status(self.tamper(
            self.state, "evidence.0.acceptance.content_digest_sha256", "1" * 64
        )), mp.REQUIREMENT_MISMATCHED)
        # Claims never enter the evaluation.
        no_claims = self.tamper(self.state, "claims", [])
        self.assertEqual(self.status(no_claims), mp.REQUIREMENT_SATISFIED)
        only_claims = json.loads(json.dumps(self.state))
        only_claims["evidence"] = []
        self.assertEqual(self.status(only_claims), mp.REQUIREMENT_MISSING)

    def test_C2_narrative_claim_and_process_exit_are_structurally_incapable(self):
        mp = self.mp
        for kind in mission_record.NON_SATISFYING_EVIDENCE_KINDS:
            with self.subTest(kind):
                pending = self.tamper(self.state, "evidence.0.kind", kind)
                pending["evidence"][0]["acceptance"] = None
                self.assertEqual(self.status(pending),
                                 mp.REQUIREMENT_SUBMITTED_NOT_ACCEPTED)
                # Even a forged acceptance handed straight to the pure
                # evaluator cannot satisfy: the kind is outside every
                # requirement's declared kinds by construction.
                forged = self.tamper(self.state, "evidence.0.kind", kind)
                self.assertEqual(self.status(forged), mp.REQUIREMENT_MISMATCHED)
                self.assertFalse(mp.evaluate_proof(
                    self.clean_contract, forged, self.activation_id, self.now
                )["satisfied"])
        self.assertFalse(mp.kind_can_satisfy(mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM))
        self.assertFalse(mp.kind_can_satisfy(mission_record.EVIDENCE_KIND_PROCESS_EXIT))
        self.assertTrue(mp.kind_can_satisfy(mission_record.EVIDENCE_KIND_VERIFICATION_RECORD))

    def test_C3_readiness_is_conservative_stale_aware_and_not_authority(self):
        mp = self.mp
        ready = mp.readiness(self.clean_contract, self.state, self.now)
        self.assertEqual(ready, {"satisfied": True,
                                 "resources": {"build_host": "READY"}})
        # Stale beyond max_age_seconds (600) is not ready.
        self.assertEqual(mp.readiness(self.clean_contract, self.state, 1009 + 600)
                         ["resources"]["build_host"], "READY")
        stale = mp.readiness(self.clean_contract, self.state, 1009 + 601)
        self.assertEqual(stale["resources"]["build_host"], "NOT_READY")
        self.assertFalse(stale["satisfied"])
        # Observed in the future is not ready either.
        self.assertFalse(mp.readiness(self.clean_contract, self.state, 1008)["satisfied"])
        for status in (self.ms.READINESS_UNKNOWN, self.ms.READINESS_NOT_READY):
            self.assertFalse(mp.readiness(
                self.clean_contract,
                self.tamper(self.state, "resource_readiness.0.status", status),
                self.now,
            )["satisfied"])
        # No observation at all is not ready; an observation for another
        # key does not count.
        self.assertFalse(mp.readiness(self.clean_contract,
                                      self.tamper(self.state, "resource_readiness", []),
                                      self.now)["satisfied"])
        self.assertFalse(mp.readiness(
            self.clean_contract,
            self.tamper(self.state, "resource_readiness.0.resource_key", "other"),
            self.now,
        )["satisfied"])
        # The latest observation wins, in accepted order.
        latest = json.loads(json.dumps(self.state))
        later = dict(latest["resource_readiness"][0], status="NOT_READY",
                     observed_at=1010, sequence=12)
        latest["resource_readiness"].append(later)
        self.assertFalse(mp.readiness(self.clean_contract, latest, self.now)["satisfied"])
        # The projection carries no permission-shaped field.
        for key in ready:
            self.assertNotIn("permit", key)
            self.assertNotIn("authoriz", key)
            self.assertNotIn("run", key)

    def test_C4_dependency_slots_and_prerequisite_identity(self):
        mp = self.mp
        slots = mp.dependency_status(self.clean_contract, self.state, self.activation_id)
        self.assertEqual(slots, {"satisfied": False,
                                 "slots": {"upstream": mp.SLOT_BOUND_UNRESOLVED}})
        unbound = self.tamper(self.state, "dependencies", [])
        self.assertEqual(mp.dependency_status(self.clean_contract, unbound,
                                              self.activation_id)["slots"]["upstream"],
                         mp.SLOT_UNBOUND)
        resolved = json.loads(json.dumps(self.state))
        resolved["dependencies"][0]["resolution"] = {
            "resolved_at": 1012, "evidence_id": self.evidence_id,
            "provenance": resolved["dependencies"][0]["provenance"],
            "operation_id": hexid("mo", 0x555), "sequence": 12,
        }
        self.assertEqual(mp.dependency_status(self.clean_contract, resolved,
                                              self.activation_id),
                         {"satisfied": True, "slots": {"upstream": mp.SLOT_RESOLVED}})
        # Prerequisite identity (R-18) is checked against a registry view:
        # exact mission at the declared revision with the declared digest,
        # and the prerequisite's own progress COMPLETED.
        registry = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_COMPLETED, 1, HEX_B)}
        self.assertEqual(mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, registry), [])
        wrong_digest = {MISSION_X: reg(1, {1: HEX_A}, self.ms.PROGRESS_COMPLETED, 1, HEX_A)}
        problems = mp.prerequisite_problems(self.clean_contract, resolved,
                                            self.activation_id, wrong_digest)
        self.assertEqual([p for p, _ in problems], [mp.PROBLEM_DEPENDENCY_TARGET_MISMATCH])
        not_done = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_IN_PROGRESS)}
        problems = mp.prerequisite_problems(self.clean_contract, resolved,
                                            self.activation_id, not_done)
        self.assertEqual([p for p, _ in problems], [mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])
        no_state = {MISSION_X: reg(1, {1: HEX_B}, None)}
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, no_state)],
            [mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])
        unknown = {}
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, unknown)],
            [mp.PROBLEM_DEPENDENCY_TARGET_MISMATCH])
        # R-21.2: a later EDIT of the completed prerequisite drifts it, even
        # though the historical revision-1 digest still matches at bind time.
        drifted = {MISSION_X: reg(2, {1: HEX_B, 2: HEX_A}, self.ms.PROGRESS_COMPLETED,
                                  1, HEX_B)}
        self.assertTrue(mp.target_matches(
            self.clean_contract["required_dependencies"][0]["target"], MISSION_X, drifted))
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, drifted)],
            [mp.PROBLEM_PREREQUISITE_DRIFTED])
        # A completion bound to a different activation/revision never
        # satisfies the declared revision.
        other_completion = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_COMPLETED,
                                           2, HEX_A)}
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, other_completion)],
            [mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])
        unbound_completion = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_COMPLETED)}
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            self.clean_contract, resolved, self.activation_id, unbound_completion)],
            [mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])
        # ELIGIBILITY: current digest must still equal the declared one.
        eligible_contract = dict(self.clean_contract, required_dependencies=[dict(
            self.clean_contract["required_dependencies"][0],
            target={"form": "ELIGIBILITY", "condition": "MISSION_WITH_PROPOSAL_DIGEST",
                    "proposal_digest_sha256": HEX_B})])
        self.assertEqual(mp.prerequisite_problems(
            eligible_contract, resolved, self.activation_id, registry), [])
        self.assertEqual([p for p, _ in mp.prerequisite_problems(
            eligible_contract, resolved, self.activation_id, drifted)],
            [mp.PROBLEM_PREREQUISITE_DRIFTED])
        # An unbound slot raises no prerequisite problem (it is UNBOUND).
        self.assertEqual(mp.prerequisite_problems(
            self.clean_contract, unbound, self.activation_id, drifted), [])
        # Target matching per form.
        exact = self.clean_contract["required_dependencies"][0]["target"]
        self.assertTrue(mp.target_matches(exact, MISSION_X, registry))
        self.assertFalse(mp.target_matches(exact, hexid("mn", 0x44), registry))
        eligibility = {"form": "ELIGIBILITY",
                       "condition": "MISSION_WITH_PROPOSAL_DIGEST",
                       "proposal_digest_sha256": HEX_B}
        self.assertTrue(mp.target_matches(eligibility, MISSION_X, registry))
        edited_away = {MISSION_X: {"current_revision": 2,
                                   "revision_digests": {1: HEX_B, 2: HEX_A},
                                   "progress": self.ms.PROGRESS_COMPLETED}}
        self.assertFalse(mp.target_matches(eligibility, MISSION_X, edited_away))
        self.assertTrue(mp.target_matches(exact, MISSION_X, edited_away))
        resource = {"form": "EXACT_RESOURCE", "resource_key": "gpu_pool"}
        self.assertTrue(mp.target_matches(resource, "gpu_pool", {}))
        self.assertFalse(mp.target_matches(resource, "gpu-pool", {}))

    def test_C5_budget_and_closure_failures_have_distinct_codes(self):
        mp = self.mp
        self.assertEqual(mp.budget(self.clean_contract, self.state), {
            "attempts_consumed": 1, "attempts_remaining": 2,
            "checkpoints_consumed": 1, "checkpoints_remaining": 7,
        })
        registry = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_COMPLETED, 1, HEX_B)}
        failures = mp.closure_failures(self.clean_contract, self.state,
                                       self.activation_id, self.now, registry)
        self.assertEqual([p for p, _ in failures], [mp.PROBLEM_DEPENDENCY_UNRESOLVED])
        eligible = json.loads(json.dumps(self.state))
        eligible["dependencies"][0]["resolution"] = {
            "resolved_at": 1012, "evidence_id": self.evidence_id,
            "provenance": eligible["dependencies"][0]["provenance"],
            "operation_id": hexid("mo", 0x555), "sequence": 12,
        }
        self.assertEqual(mp.closure_failures(self.clean_contract, eligible,
                                             self.activation_id, self.now, registry), [])
        # Every failure has its own code; the order is fixed.
        everything = json.loads(json.dumps(eligible))
        everything["evidence"][0]["acceptance"] = None
        everything["blockers"][0]["resolution"] = None
        everything["dependencies"][0]["resolution"] = None
        everything["resource_readiness"] = []
        codes = [p for p, _ in mp.closure_failures(
            self.clean_contract, everything, self.activation_id, self.now, registry)]
        self.assertEqual(codes, [
            mp.PROBLEM_PROOF_NOT_SATISFIED, mp.PROBLEM_HARD_BLOCKER_ACTIVE,
            mp.PROBLEM_DEPENDENCY_UNRESOLVED, mp.PROBLEM_RESOURCE_NOT_READY,
        ])
        self.assertEqual(len(set(codes)), 4)
        # A DEGRADED active blocker does not block; a stale prerequisite does.
        degraded = json.loads(json.dumps(eligible))
        degraded["blockers"][0]["resolution"] = None
        degraded["blockers"][0]["severity"] = self.ms.BLOCKER_SEVERITY_DEGRADED
        self.assertEqual(mp.closure_failures(self.clean_contract, degraded,
                                             self.activation_id, self.now, registry), [])
        stale_prereq = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_IN_PROGRESS)}
        self.assertEqual([p for p, _ in mp.closure_failures(
            self.clean_contract, eligible, self.activation_id, self.now, stale_prereq)],
            [mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])
        drifted = {MISSION_X: reg(2, {1: HEX_B, 2: HEX_A}, self.ms.PROGRESS_COMPLETED,
                                  1, HEX_B)}
        self.assertEqual([p for p, _ in mp.closure_failures(
            self.clean_contract, eligible, self.activation_id, self.now, drifted)],
            [mp.PROBLEM_PREREQUISITE_DRIFTED])
        # R-20.2: an independent pass over every declared required artifact,
        # regardless of which requirement names it. Absent, unavailable or
        # digest-mismatched each refuse with the same distinct code, and the
        # pass runs even when every requirement would read SATISFIED (an
        # incoherent contract handed straight to the pure evaluator).
        for path, value in (("artifacts", []), ("artifacts.0.available", False),
                            ("artifacts.0.content_digest_sha256", "0" * 64)):
            with self.subTest(path):
                broken = self.tamper(eligible, path, value)
                codes = [p for p, _ in mp.closure_failures(
                    self.clean_contract, broken, self.activation_id, self.now, registry)]
                self.assertIn(mp.PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE, codes)
                self.assertIn(mp.PROBLEM_PROOF_NOT_SATISFIED, codes)
        incoherent = dict(self.clean_contract, requirements=[dict(
            self.clean_contract["requirements"][0], required_artifact_keys=[])])
        broken = self.tamper(eligible, "artifacts.0.available", False)
        self.assertTrue(mp.evaluate_proof(incoherent, broken, self.activation_id,
                                          self.now)["satisfied"])
        self.assertEqual([p for p, _ in mp.closure_failures(
            incoherent, broken, self.activation_id, self.now, registry)],
            [mp.PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE])
        # The latest record for a key is what counts: a later re-record that
        # marks it unavailable degrades conservatively.
        rerecorded = json.loads(json.dumps(eligible))
        rerecorded["artifacts"].append(dict(rerecorded["artifacts"][0],
                                            artifact_id=hexid("mf", 2),
                                            available=False, sequence=13))
        self.assertIn(mp.PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE, [
            p for p, _ in mp.closure_failures(self.clean_contract, rerecorded,
                                              self.activation_id, self.now, registry)])
        self.assertEqual(mp.required_artifact_problems(self.clean_contract, eligible), [])
        # R-24.1: closure_failures is exactly local + prerequisites.
        for state in (everything, eligible, degraded, broken):
            self.assertEqual(
                mp.closure_failures(self.clean_contract, state, self.activation_id,
                                    self.now, drifted),
                mp.local_closure_failures(self.clean_contract, state,
                                          self.activation_id, self.now)
                + mp.prerequisite_problems(self.clean_contract, state,
                                           self.activation_id, drifted))
        self.assertNotIn(mp.PROBLEM_PREREQUISITE_DRIFTED, [
            p for p, _ in mp.local_closure_failures(
                self.clean_contract, eligible, self.activation_id, self.now)])
        # Resolving a blocker changes no requirement status and satisfies
        # no dependency (R-13).
        before = mp.evaluate_proof(self.clean_contract, everything,
                                   self.activation_id, self.now)
        after_state = json.loads(json.dumps(everything))
        after_state["blockers"][0]["resolution"] = eligible["blockers"][0]["resolution"]
        after = mp.evaluate_proof(self.clean_contract, after_state,
                                  self.activation_id, self.now)
        self.assertEqual(before, after)
        self.assertEqual(mp.dependency_status(self.clean_contract, after_state,
                                              self.activation_id)["satisfied"], False)

    def test_C6_next_step_derivation_and_checkpoint_recomputation(self):
        mp = self.mp
        ms = self.ms
        derived = mp.derive_checkpoint_fields(self.clean_contract, self.state,
                                              self.activation_id, self.now)
        checkpoint = self.state["checkpoints"][0]
        for key in ("active_blocker_ids", "outstanding_dependency_ids", "budget",
                    "next_permitted_step", "refusal"):
            self.assertEqual(derived[key], checkpoint[key], key)
        self.assertIsNone(mp.checkpoint_disagreement(self.clean_contract, self.state,
                                                     checkpoint))
        # Tampering any derived field is detected.
        for path, value in (
            ("checkpoints.0.next_permitted_step", ms.NEXT_STEP_CLOSE_COMPLETED),
            ("checkpoints.0.budget.attempts_remaining", 5),
            ("checkpoints.0.outstanding_dependency_ids", []),
            ("checkpoints.0.active_blocker_ids", [self.blocker_id]),
        ):
            with self.subTest(path):
                tampered = self.tamper(self.state, path, value)
                self.assertIsNotNone(mp.checkpoint_disagreement(
                    self.clean_contract, tampered, tampered["checkpoints"][0]))
        # Later state does not disturb the recomputation: it runs on the
        # state AS OF the checkpoint's sequence with recorded_at as clock.
        later = json.loads(json.dumps(self.state))
        later["dependencies"][0]["resolution"] = {
            "resolved_at": 1012, "evidence_id": self.evidence_id,
            "provenance": later["dependencies"][0]["provenance"],
            "operation_id": hexid("mo", 0x555), "sequence": 12,
        }
        later["sequence"] = 12
        self.assertIsNone(mp.checkpoint_disagreement(self.clean_contract, later,
                                                     later["checkpoints"][0]))
        as_of = mp.state_as_of(later, 11)
        self.assertIsNone(as_of["dependencies"][0]["resolution"])
        self.assertEqual(as_of["sequence"], 11)
        early = mp.state_as_of(later, 3)
        self.assertEqual(early["evidence"], [])
        self.assertEqual(len(early["artifacts"]), 1)
        self.assertEqual(early["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(mp.state_as_of(later, 6)["progress"], ms.PROGRESS_BLOCKED)
        self.assertEqual(mp.state_as_of(later, 0)["progress"], ms.PROGRESS_NOT_STARTED)
        # Precedence of the next step.
        def step(state, now=None):
            fields = mp.derive_checkpoint_fields(
                self.clean_contract, state, self.activation_id,
                self.now if now is None else now)
            return fields["next_permitted_step"], fields["refusal"]
        blocked = json.loads(json.dumps(self.state))
        blocked["blockers"][0]["resolution"] = None
        self.assertEqual(step(blocked), (ms.NEXT_STEP_RESOLVE_BLOCKERS, None))
        self.assertEqual(step(self.state), (ms.NEXT_STEP_RESOLVE_DEPENDENCIES, None))
        resolved = json.loads(json.dumps(later))
        # R-22: the contract declares a required MISSION slot, so local state
        # cannot know the prerequisite still holds: the conservative step.
        self.assertEqual(step(resolved), (ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE, None))
        no_mission_slot = dict(self.clean_contract, required_dependencies=[dict(
            self.clean_contract["required_dependencies"][0], kind="RESOURCE",
            target={"form": "EXACT_RESOURCE", "resource_key": "gpu_pool"})])
        resource_bound = json.loads(json.dumps(resolved))
        resource_bound["dependencies"][0]["kind"] = "RESOURCE"
        resource_bound["dependencies"][0]["reference"] = "gpu_pool"
        self.assertEqual(mp.derive_checkpoint_fields(
            no_mission_slot, resource_bound, self.activation_id, self.now
        )["next_permitted_step"], ms.NEXT_STEP_CLOSE_COMPLETED)
        self.assertEqual(step(resolved, now=1009 + 601),
                         (ms.NEXT_STEP_OBSERVE_RESOURCE_READINESS, None))
        # A missing required artifact is a local failure too (R-20.2).
        self.assertEqual(step(self.tamper(resolved, "artifacts.0.available", False)),
                         (ms.NEXT_STEP_SUBMIT_EVIDENCE, None))
        pending = json.loads(json.dumps(resolved))
        pending["evidence"][0]["acceptance"] = None
        self.assertEqual(step(pending), (ms.NEXT_STEP_ACCEPT_EVIDENCE, None))
        pending["evidence"] = []
        self.assertEqual(step(pending), (ms.NEXT_STEP_SUBMIT_EVIDENCE, None))
        # Exhausted attempts with unsatisfied proof: a deterministic refusal,
        # never a retry step. With satisfied proof, completion stays open.
        exhausted = json.loads(json.dumps(pending))
        for attempt in (2, 3):
            op, seq, prov = self.op(exhausted, ms.OPERATION_RECORD_CONTINUATION,
                                    1011 + attempt)
            exhausted["continuations"].append(ms.new_continuation(
                attempt, "again", 1011 + attempt, prov, op, seq))
        next_step, refusal = step(exhausted)
        self.assertIsNone(next_step)
        self.assertEqual(refusal["problem"], ms.PROBLEM_BUDGET_EXHAUSTED)
        # R-23: consuming the final permitted attempt and then holding
        # complete proof is success; completion never reads the budget.
        done_but_exhausted = json.loads(json.dumps(resolved))
        done_but_exhausted["continuations"] = exhausted["continuations"]
        self.assertEqual(step(done_but_exhausted),
                         (ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE, None))
        self.assertEqual(mp.budget(self.clean_contract, exhausted)["attempts_remaining"], 0)
        self.assertTrue(mp.attempts_exhausted(self.clean_contract, exhausted))
        registry = {MISSION_X: reg(1, {1: HEX_B}, ms.PROGRESS_COMPLETED, 1, HEX_B)}
        self.assertEqual(mp.closure_failures(self.clean_contract, done_but_exhausted,
                                             self.activation_id, self.now, registry), [])
        self.assertEqual(mp.closure_eligibility(
            self.clean_contract, done_but_exhausted, self.activation_id, self.now,
            registry), {"eligible": True, "failures": []})
        self.assertFalse(mp.closure_eligibility(
            self.clean_contract, exhausted, self.activation_id, self.now, registry
        )["eligible"])
        # Nothing in the evaluators reads or writes max_attempts: the budget
        # projection is derived from the approved contract and the attempt
        # count only.
        raised = dict(self.clean_contract, continuation_budget={
            "max_attempts": 9, "max_checkpoints": 8})
        self.assertEqual(mp.budget(raised, exhausted)["attempts_remaining"], 6)
        self.assertEqual(mp.budget(self.clean_contract, exhausted)["attempts_remaining"], 0)
        # A stored checkpoint beyond the checkpoint budget disagrees.
        over = json.loads(json.dumps(self.state))
        small = dict(self.clean_contract, continuation_budget={
            "max_attempts": 3, "max_checkpoints": 1})
        over["checkpoints"][0]["budget"]["checkpoints_remaining"] = 0
        self.assertIsNone(mp.checkpoint_disagreement(small, over, over["checkpoints"][0]))
        over["checkpoints"].append(dict(over["checkpoints"][0], sequence=12,
                                        checkpoint_id=hexid("mk", 2)))
        over["checkpoints"][1]["budget"] = dict(over["checkpoints"][1]["budget"],
                                                checkpoints_consumed=2,
                                                checkpoints_remaining=0)
        over["sequence"] = 12
        self.assertIsNotNone(mp.checkpoint_disagreement(small, over, over["checkpoints"][1]))

    def test_C7_dependency_graph_refuses_self_and_cycles(self):
        mp = self.mp
        a, b, c = hexid("mn", 0xa), hexid("mn", 0xb), hexid("mn", 0xc)

        def dep(reference):
            return {"kind": "MISSION", "reference": reference, "resolution": None}
        graph = {a: {"dependencies": [dep(b)]}, b: {"dependencies": [dep(c)]},
                 c: {"dependencies": []}}
        self.assertIsNone(mp.dependency_graph_problem(graph))
        graph[c]["dependencies"].append(dep(a))
        problem = mp.dependency_graph_problem(graph)
        self.assertEqual(problem[0], mp.PROBLEM_DEPENDENCY_CYCLE)
        two = {a: {"dependencies": [dep(b)]}, b: {"dependencies": [dep(a)]}}
        self.assertEqual(mp.dependency_graph_problem(two)[0], mp.PROBLEM_DEPENDENCY_CYCLE)
        own = {a: {"dependencies": [dep(a)]}}
        self.assertEqual(mp.dependency_graph_problem(own)[0], self.ms.PROBLEM_DEPENDENCY_SELF)
        # RESOURCE dependencies are not edges; resolved MISSION ones still are.
        resources = {a: {"dependencies": [{"kind": "RESOURCE", "reference": "x",
                                           "resolution": None}]}}
        self.assertIsNone(mp.dependency_graph_problem(resources))
        resolved_cycle = {a: {"dependencies": [dict(dep(b), resolution={})]},
                          b: {"dependencies": [dep(a)]}}
        self.assertIsNotNone(mp.dependency_graph_problem(resolved_cycle))

    def test_C8_every_evaluator_is_deterministic_and_clockless(self):
        mp = self.mp
        import ast
        source = (REPO_ROOT / "mission" / "progress.py").read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", None) or ""
                for root in [module.split(".")[0]] + [n.split(".")[0] for n in names]:
                    self.assertNotIn(root, ("time", "datetime", "os", "json",
                                            "secrets", "threading"), root)
        self.assertNotIn("open(", source)
        registry = {MISSION_X: reg(1, {1: HEX_B}, self.ms.PROGRESS_COMPLETED, 1, HEX_B)}
        calls = (
            lambda s: mp.evaluate_proof(self.clean_contract, s, self.activation_id, self.now),
            lambda s: mp.readiness(self.clean_contract, s, self.now),
            lambda s: mp.dependency_status(self.clean_contract, s, self.activation_id),
            lambda s: mp.budget(self.clean_contract, s),
            lambda s: mp.closure_failures(self.clean_contract, s, self.activation_id,
                                          self.now, registry),
            lambda s: mp.derive_checkpoint_fields(self.clean_contract, s,
                                                  self.activation_id, self.now),
            lambda s: mp.state_as_of(s, 7),
        )
        reversed_keys = json.loads(json.dumps(self.state))
        reversed_keys = dict(reversed(list(reversed_keys.items())))
        for index, call in enumerate(calls):
            with self.subTest(index):
                first = call(self.state)
                self.assertEqual(first, call(self.state))
                self.assertEqual(first, call(json.loads(json.dumps(self.state))))
                self.assertEqual(first, call(reversed_keys))
        # Evaluators never mutate their input.
        snapshot = json.loads(json.dumps(self.state))
        for call in calls:
            call(self.state)
        self.assertEqual(self.state, snapshot)


# ====================================================================
# D. Store: compatibility, cross-references, cycles, fail-closed reload
# ====================================================================


class Clock(object):
    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


class DocumentFixture(unittest.TestCase):
    """A real store in a temporary protected directory, a real Mission
    created and approved through the real service with a contract-bearing
    proposal, and a Task 5 state record attached by hand (stage 1 has no
    state service surface yet)."""

    def setUp(self):
        from mission import authorization as mission_authorization
        from mission import decision as mission_decision
        from mission import progress as mission_progress
        from mission import service as mission_service
        from mission import state as mission_state
        from mission import store as mission_store
        self.ma = mission_authorization
        self.md = mission_decision
        self.mp = mission_progress
        self.ms = mission_state
        self.mst = mission_store
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "protected")
        self.store = mission_store.MissionStore(self.directory)
        self.clock = Clock()
        self.context = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501",
        )
        self.service = mission_service.MissionService(self.store, self.clock)
        self.ops = 0

    def tearDown(self):
        self.tmp.cleanup()

    def read_bytes(self):
        with open(self.store.path, "rb") as handle:
            return handle.read()

    def write_raw(self, text):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        with open(self.store.path, "w") as handle:
            handle.write(text)
        os.chmod(self.store.path, 0o600)

    def propose(self, **overrides):
        self.clock.advance(1)
        request_id = self.service.mint_request_id(self.context)
        return self.service.propose(request_id, proposal(**overrides), self.context)

    def approve(self, mission_id, revision, expires_at=None):
        self.clock.advance(1)
        decision_id = self.service.mint_decision_id(self.context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        envelope = self.md.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=self.md.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=[current["proposal"]["requested_delivery_target"]],
            expires_at=expires_at,
        )
        return self.service.apply_human_decision(envelope)

    def edit(self, mission_id, revision, **overrides):
        self.clock.advance(1)
        decision_id = self.service.mint_decision_id(self.context)
        return self.service.edit(mission_id, revision, proposal(**overrides),
                                 decision_id, self.context)

    def fill_local(self, document, mission_id, bind=None):
        """Make a state record locally complete: the required artifact, an
        accepted VERIFICATION_RECORD, a READY observation, and (with
        ``bind``) the required slot bound to that Mission and resolved."""
        ms = self.ms
        state = document["mission_state"][mission_id]
        activation_id = self.activation_id(state)
        self.clock.advance(1)
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_ARTIFACT, self.clock())
        artifact_id = hexid("mf", self.ops)
        state["artifacts"].append(ms.new_artifact(
            artifact_id, "test_log", mission_record.ARTIFACT_ROLE_VERIFICATION,
            ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", HEX_A, True, [],
            self.clock(), prov, op, seq))
        self.clock.advance(1)
        op, seq, prov = self.op(document, state, ms.OPERATION_SUBMIT_EVIDENCE, self.clock())
        evidence_id = hexid("mv", self.ops)
        state["evidence"].append(ms.new_evidence(
            evidence_id, activation_id, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
            [artifact_id], self.clock(), prov, op, seq))
        self.clock.advance(5)
        op, seq, prov = self.op(document, state, ms.OPERATION_ACCEPT_EVIDENCE, self.clock())
        state["evidence"][-1]["acceptance"] = ms.new_acceptance(
            self.clock(), "e" * 64, activation_id, prov, op, seq)
        self.clock.advance(1)
        op, seq, prov = self.op(document, state, ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, self.clock(), prov, op, seq))
        if bind is not None:
            self.clock.advance(1)
            op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY,
                                    self.clock())
            state["dependencies"].append(ms.new_dependency(
                hexid("mx", self.ops), activation_id, "upstream",
                mission_record.DEPENDENCY_KIND_MISSION, bind, self.clock(), prov, op, seq))
            self.clock.advance(1)
            op, seq, prov = self.op(document, state, ms.OPERATION_RESOLVE_DEPENDENCY,
                                    self.clock())
            state["dependencies"][-1]["resolution"] = ms.new_resolution(
                self.clock(), evidence_id, prov, op, seq)
        return evidence_id

    def complete_locally(self, document, mission_id):
        ms = self.ms
        state = document["mission_state"][mission_id]
        self.clock.advance(1)
        op, seq, prov = self.op(document, state, ms.OPERATION_COMPLETE, self.clock())
        state["closure"] = ms.new_closure(
            ms.PROGRESS_COMPLETED, ms.CLOSURE_REASON_PROOF_COMPLETE, "proof complete",
            self.clock(), self.activation_id(state), prov, op, seq)
        state["progress"] = ms.PROGRESS_COMPLETED

    def op(self, document, state, kind, applied_at, outcome=None):
        """Reserve a state operation id in the document and apply it."""
        self.ops += 1
        operation_id = hexid("mo", 0x1000 + self.ops)
        document["reservations"][operation_id] = {
            "reserved_at": applied_at, "kind": "state_operation",
            "context": self.context.as_dict(), "consumed_by": operation_id,
        }
        provenance = mission_record.provenance_record(
            self.context, applied_at, mission_record.REFERENCE_KIND_STATE_OPERATION,
            operation_id, state["mission_id"],
            document["missions"][state["mission_id"]]["current_revision"],
        )
        self.ms.append_applied_operation(
            state, operation_id, kind, "c" * 64, applied_at, provenance,
            outcome or {"ok": True},
        )
        return operation_id, state["sequence"], provenance

    def approved_mission(self, **contract_overrides):
        """Create + approve a Mission whose proposal carries a contract."""
        created = self.propose(proof_contract=contract(**contract_overrides))
        approved = self.approve(created["mission_id"], 1)
        return created["mission_id"], approved["authorization_id"]

    def attach_state(self, document, mission_id, authorization_id, activated_at=None):
        """A state record activated against revision 1 of ``mission_id``."""
        ms = self.ms
        mission = document["missions"][mission_id]
        entry = mission["revisions"][0]
        authorization = document["authorizations"][authorization_id]
        state = ms.new_state_record(mission_id, activated_at or self.clock())
        op, seq, prov = self.op(document, state, ms.OPERATION_ACTIVATE_CONTRACT,
                                activated_at or self.clock())
        state["contract_activations"].append(ms.new_activation(
            hexid("mt", self.ops), entry["revision"], entry["proposal_digest_sha256"],
            authorization_id, authorization["authorization_digest_sha256"],
            mission_record.proof_contract_digest(entry["proposal"]["proof_contract"]),
            activated_at or self.clock(), prov, op, seq,
        ))
        state["progress"] = ms.PROGRESS_IN_PROGRESS
        document["mission_state"][mission_id] = state
        return state

    def activation_id(self, state):
        return state["contract_activations"][-1]["activation_id"]

    def seal_document(self, document):
        """Seal every hand-built state record (see ``seal_state``) using
        each Mission's own approved budget."""
        for mission_id, state in document.get("mission_state", {}).items():
            mission = document["missions"].get(mission_id)
            max_attempts = 3
            if mission is not None:
                contract_ = mission["revisions"][-1]["proposal"].get("proof_contract")
                if contract_:
                    max_attempts = contract_["continuation_budget"]["max_attempts"]
            seal_state(state, max_attempts)
        return document

    def save(self, document):
        self.store.save(self.seal_document(document))


class DStoreCompatibilityTests(DocumentFixture):

    def test_D1_default_document_carries_exactly_one_new_key(self):
        document = self.mst.default_document()
        self.assertEqual(set(document), set(self.mst.TOP_LEVEL_KEYS))
        self.assertEqual(document["mission_state"], {})
        self.assertEqual(self.mst.MISSION_STORE_SCHEMA_VERSION, 1)
        self.assertEqual(
            set(self.mst.TOP_LEVEL_KEYS) - {
                "mission_store_schema_version", "missions", "authorizations",
                "authority_ledger", "reservations"},
            {"mission_state"},
        )
        self.assertEqual(self.store.load(), document)
        self.save(document)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)
        self.assertEqual(sorted(os.listdir(self.directory)), ["missions.json"])

    def test_D2_task4_document_without_mission_state_loads_as_empty_map(self):
        mission_id, authorization_id = self.approved_mission()
        document = json.loads(self.read_bytes())
        self.assertIn("mission_state", document)
        legacy = dict(document)
        del legacy["mission_state"]
        self.write_raw(json.dumps(legacy))
        before = self.read_bytes()
        loaded = self.store.load()
        self.assertEqual(loaded["mission_state"], {})
        self.assertEqual(self.read_bytes(), before)
        # Task 4 reads still work over the legacy bytes, and authority
        # validates exactly as before.
        self.assertTrue(self.service.validate_authorization(
            authorization_id, mission_id, 1).valid)
        self.assertEqual(self.service.get(mission_id)["live_authorization_id"],
                         authorization_id)
        # The next save writes the key; the document is otherwise the same.
        self.edit(mission_id, 1, objective="v2")
        after = json.loads(self.read_bytes())
        self.assertEqual(after["mission_state"], {})
        self.assertEqual(set(after), set(self.mst.TOP_LEVEL_KEYS))

    def test_D3_every_other_unknown_or_missing_key_still_refuses(self):
        mission_id, authorization_id = self.approved_mission()
        good = json.loads(self.read_bytes())
        cases = {}
        for key in self.mst.TOP_LEVEL_KEYS:
            if key == "mission_state":
                continue
            missing = dict(good)
            del missing[key]
            cases["missing " + key] = missing
        cases["unknown key"] = dict(good, mission_states={})
        cases["unknown key beside legacy shape"] = dict(
            (k, v) for k, v in good.items() if k != "mission_state")
        cases["unknown key beside legacy shape"]["extra"] = 1
        cases["mission_state not an object"] = dict(good, mission_state=[])
        cases["mission_state under version 2"] = dict(good,
                                                      mission_store_schema_version=2)
        for label, document in cases.items():
            with self.subTest(label):
                self.write_raw(json.dumps(document))
                before = self.read_bytes()
                with self.assertRaises(self.mst.MissionStoreError) as ctx:
                    self.store.load()
                self.assertEqual(ctx.exception.problem,
                                 self.mst.PROBLEM_STORE_UNREADABLE)
                self.assertEqual(self.read_bytes(), before)
                self.assertFalse(self.service.validate_authorization(
                    authorization_id, mission_id, 1).valid)
        # Save never supplies the key: a five-key document is refused
        # before anything touches the filesystem.
        self.write_raw(json.dumps(good))
        before = self.read_bytes()
        legacy = dict((k, v) for k, v in good.items() if k != "mission_state")
        with self.assertRaises(self.mst.MissionStoreError):
            self.store.save(legacy)
        self.assertEqual(self.read_bytes(), before)

    def test_D4_state_operation_reservations_and_caps(self):
        mst = self.mst
        self.assertIn("state_operation", mst.RESERVATION_KINDS)
        for name in ("MAX_MISSION_STATE_RECORDS", "MAX_RESERVED_STATE_OPERATION_IDS"):
            self.assertIsInstance(getattr(mst, name), int)
            self.assertGreater(getattr(mst, name), 0)
        document = mst.default_document()
        entry = {"reserved_at": 5, "kind": "state_operation",
                 "context": self.context.as_dict(), "consumed_by": None}
        # Key grammar follows the kind.
        bad = dict(document)
        bad["reservations"] = {hexid("md", 1): dict(entry)}
        with self.assertRaises(mst.MissionStoreError):
            self.store.save(bad)
        bad["reservations"] = {hexid("mo", 1): dict(entry, consumed_by=hexid("md", 1))}
        with self.assertRaises(mst.MissionStoreError):
            self.store.save(bad)
        # The cap refuses at the bound and never evicts.
        document["reservations"] = dict(
            (hexid("mo", i), dict(entry))
            for i in range(mst.MAX_RESERVED_STATE_OPERATION_IDS))
        self.save(document)
        document["reservations"][hexid("mo", mst.MAX_RESERVED_STATE_OPERATION_IDS)] = (
            dict(entry))
        with self.assertRaises(mst.MissionStoreError) as ctx:
            self.save(document)
        self.assertEqual(ctx.exception.problem, mst.PROBLEM_STORE_FULL)
        self.assertEqual(len(json.loads(self.read_bytes())["reservations"]),
                         mst.MAX_RESERVED_STATE_OPERATION_IDS)
        # Task 4 caps are untouched.
        self.assertEqual(mst.MAX_RESERVED_DECISION_IDS, 4096)
        self.assertEqual(mst.MAX_RESERVED_REQUEST_IDS, 4096)


class DStoreCrossReferenceTests(DocumentFixture):

    def setUp(self):
        super(DStoreCrossReferenceTests, self).setUp()
        self.mission_id, self.authorization_id = self.approved_mission()
        self.document = self.store.load()
        self.state = self.attach_state(self.document, self.mission_id,
                                       self.authorization_id)
        self.save(self.document)
        self.good = json.loads(self.read_bytes())

    def refuse(self, document, fragment=None, seal=True):
        if seal:
            document = self.seal_document(json.loads(json.dumps(document)))
        self.write_raw(json.dumps(document))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.load()
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        if fragment is not None:
            self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)
        with self.assertRaises(self.mst.MissionStoreError):
            self.store.save(document)
        self.assertEqual(self.read_bytes(), before)
        return ctx.exception

    def tampered(self):
        return json.loads(json.dumps(self.good))

    def test_D5_state_round_trips_and_binds_the_approved_revision(self):
        self.assertEqual(self.store.load(), self.good)
        state = self.good["mission_state"][self.mission_id]
        activation = state["contract_activations"][0]
        mission = self.good["missions"][self.mission_id]
        self.assertEqual(activation["proposal_digest_sha256"],
                         mission["revisions"][0]["proposal_digest_sha256"])
        self.assertEqual(activation["contract_digest_sha256"],
                         mission_record.proof_contract_digest(
                             mission["revisions"][0]["proposal"]["proof_contract"]))
        # Authority is untouched by attaching state (R-5.5).
        before = json.loads(self.read_bytes())
        self.assertTrue(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1).valid)
        self.assertEqual(before["authorizations"], self.good["authorizations"])
        self.assertEqual(before["authority_ledger"], self.good["authority_ledger"])

    def test_D6_mission_id_agreement_and_activation_resolution(self):
        # Key / mission_id disagreement.
        document = self.tampered()
        document["mission_state"][MISSION_X] = document["mission_state"].pop(self.mission_id)
        self.refuse(document)
        document = self.tampered()
        document["mission_state"][self.mission_id]["mission_id"] = MISSION_X
        self.refuse(document)
        # Unknown mission.
        document = self.tampered()
        stray = json.loads(json.dumps(document["mission_state"][self.mission_id]))
        stray["mission_id"] = MISSION_X
        for entry in stray["applied_operations"]:
            entry["provenance"]["mission_id"] = MISSION_X
        stray["contract_activations"][0]["provenance"]["mission_id"] = MISSION_X
        document["mission_state"][MISSION_X] = stray
        self.refuse(document)
        # Activation revision, proposal digest, contract digest, authorization.
        for path, value in (
            ("revision", 2),
            ("proposal_digest_sha256", "0" * 64),
            ("contract_digest_sha256", "0" * 64),
            ("authorization_id", hexid("ma", 0x77)),
            ("authorization_digest_sha256", "0" * 64),
        ):
            with self.subTest(path):
                document = self.tampered()
                document["mission_state"][self.mission_id]["contract_activations"][0][
                    path] = value
                self.refuse(document)
        # An activation whose revision carries no contract refuses.
        self.write_raw(json.dumps(self.good))
        plain = self.propose()
        approved = self.approve(plain["mission_id"], 1)
        document = self.store.load()
        state = self.ms.new_state_record(plain["mission_id"], self.clock())
        op, seq, prov = self.op(document, state, self.ms.OPERATION_ACTIVATE_CONTRACT,
                                self.clock())
        entry = document["missions"][plain["mission_id"]]["revisions"][0]
        state["contract_activations"].append(self.ms.new_activation(
            hexid("mt", 0x55), 1, entry["proposal_digest_sha256"],
            approved["authorization_id"], approved["authorization_digest_sha256"],
            "0" * 64, self.clock(), prov, op, seq))
        state["progress"] = self.ms.PROGRESS_IN_PROGRESS
        document["mission_state"][plain["mission_id"]] = state
        self.refuse(document, "no proof_contract")

    def test_D7_state_operations_reconcile_with_reservations(self):
        operation_id = self.good["mission_state"][self.mission_id][
            "applied_operations"][0]["operation_id"]
        # Missing reservation.
        document = self.tampered()
        del document["reservations"][operation_id]
        self.refuse(document)
        # Reservation not consumed / consumed by another id / wrong kind /
        # different principal.
        for change in ({"consumed_by": None}, {"consumed_by": hexid("mo", 0x9)},
                       {"kind": "decision"},
                       {"context": dict(self.context.as_dict(), principal_ref="x")}):
            with self.subTest(repr(change)):
                document = self.tampered()
                document["reservations"][operation_id].update(change)
                self.refuse(document)
        # Consumed reservation with no applied operation anywhere.
        document = self.tampered()
        orphan = hexid("mo", 0x8)
        document["reservations"][orphan] = {
            "reserved_at": 1, "kind": "state_operation",
            "context": self.context.as_dict(), "consumed_by": orphan}
        self.refuse(document, "state_operation")
        # An unconsumed reservation is legitimate.
        document = self.tampered()
        document["reservations"][hexid("mo", 0x7)] = {
            "reserved_at": 1, "kind": "state_operation",
            "context": self.context.as_dict(), "consumed_by": None}
        self.write_raw(json.dumps(document))
        self.assertEqual(self.store.load(), document)

    def test_D8_evidence_requirement_artifact_and_blocker_severity_bindings(self):
        ms = self.ms
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        activation_id = self.activation_id(state)
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_ARTIFACT, self.clock())
        state["artifacts"].append(ms.new_artifact(
            hexid("mf", 1), "test_log", mission_record.ARTIFACT_ROLE_VERIFICATION,
            ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:1", HEX_A, True, [],
            self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_SUBMIT_EVIDENCE, self.clock())
        state["evidence"].append(ms.new_evidence(
            hexid("mv", 1), activation_id, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
            [hexid("mf", 1)], self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_ACCEPT_EVIDENCE, self.clock())
        state["evidence"][0]["acceptance"] = ms.new_acceptance(
            self.clock(), "e" * 64, activation_id, prov, op, seq)
        op, seq, prov = self.op(document, state, ms.OPERATION_OPEN_BLOCKER, self.clock())
        state["blockers"].append(ms.new_blocker(
            hexid("mb", 1), activation_id, "flaky_network",
            ms.BLOCKER_SEVERITY_DEGRADED, "network flaked", self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_CLAIM, self.clock())
        state["claims"].append(ms.new_claim(
            hexid("mc", 1), activation_id, "tests_pass", "done", self.clock(),
            prov, op, seq))
        self.save(document)
        good = json.loads(self.read_bytes())
        self.assertEqual(self.store.load(), good)
        # Unknown requirement key on evidence and on claims.
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["evidence"][0]["requirement_key"] = "nope"
        self.refuse(document, "requirement")
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["claims"][0]["requirement_key"] = "nope"
        self.refuse(document, "requirement")
        # An accepted kind the requirement does not declare.
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["evidence"][0]["kind"] = (
            mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION)
        self.refuse(document, "declare")
        # Blocker severity is DERIVED from the approved policy: a stored
        # severity that disagrees refuses either way.
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["blockers"][0]["severity"] = (
            ms.BLOCKER_SEVERITY_HARD)
        document["mission_state"][self.mission_id]["progress"] = ms.PROGRESS_BLOCKED
        self.refuse(document, "severity")
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["blockers"][0]["key"] = "disk_full"
        self.refuse(document, "severity")

    def test_D9_dependency_references_cycles_and_targets(self):
        ms = self.ms
        # A second, third mission to depend on.
        second_id, second_auth = self.approved_mission()
        third_id, third_auth = self.approved_mission()
        document = self.store.load()
        second = self.attach_state(document, second_id, second_auth)
        third = self.attach_state(document, third_id, third_auth)
        first = document["mission_state"][self.mission_id]
        # Extra (unkeyed) MISSION dependencies: first -> second -> third.
        for state, target in ((first, second_id), (second, third_id)):
            op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY,
                                    self.clock())
            state["dependencies"].append(ms.new_dependency(
                hexid("mx", self.ops), self.activation_id(state), None,
                mission_record.DEPENDENCY_KIND_MISSION, target, self.clock(),
                prov, op, seq))
        self.save(document)
        good = json.loads(self.read_bytes())
        # Close the cycle: third -> first.
        document = json.loads(json.dumps(good))
        state = document["mission_state"][third_id]
        op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY, self.clock())
        state["dependencies"].append(ms.new_dependency(
            hexid("mx", 0x99), self.activation_id(state), None,
            mission_record.DEPENDENCY_KIND_MISSION, self.mission_id, self.clock(),
            prov, op, seq))
        exc = self.refuse(document, "cycle")
        self.assertIn(self.mp.PROBLEM_DEPENDENCY_CYCLE, str(exc))
        # Unknown mission reference.
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["dependencies"][0]["reference"] = (
            hexid("mn", 0x4242))
        self.refuse(document, "unknown mission")
        # Self reference is caught by the record validator.
        document = json.loads(json.dumps(good))
        document["mission_state"][self.mission_id]["dependencies"][0]["reference"] = (
            self.mission_id)
        self.refuse(document, "itself")
        # Cross-Mission authority never transfers: depending on an AUTHORIZED
        # Mission leaves live_authorization_id derived only from the
        # dependent Mission's own authorizations, and validation outcomes
        # are unchanged.
        self.write_raw(json.dumps(good))
        self.assertEqual(self.service.get(self.mission_id)["live_authorization_id"],
                         self.authorization_id)
        self.edit(self.mission_id, 1, objective="v2", proof_contract=contract())
        self.assertIsNone(self.service.get(self.mission_id)["live_authorization_id"])
        self.assertTrue(self.service.validate_authorization(second_auth, second_id, 1).valid)
        self.assertFalse(self.service.validate_authorization(second_auth, self.mission_id, 1).valid)
        # Binding a REQUIRED slot: kind and target must match the contract.
        good = json.loads(self.read_bytes())
        contract_slot = good["missions"][second_id]["revisions"][0]["proposal"][
            "proof_contract"]["required_dependencies"][0]
        self.assertEqual(contract_slot["target"]["mission_id"], MISSION_X)
        document = json.loads(json.dumps(good))
        state = document["mission_state"][second_id]
        op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY, self.clock())
        state["dependencies"].append(ms.new_dependency(
            hexid("mx", 0x98), self.activation_id(state), "upstream",
            mission_record.DEPENDENCY_KIND_MISSION, third_id, self.clock(),
            prov, op, seq))
        self.refuse(document, "target")
        document["mission_state"][second_id]["dependencies"][-1]["key"] = "no_such_slot"
        self.refuse(document, "slot")
        document["mission_state"][second_id]["dependencies"][-1]["key"] = "upstream"
        document["mission_state"][second_id]["dependencies"][-1]["kind"] = "RESOURCE"
        document["mission_state"][second_id]["dependencies"][-1]["reference"] = "x"
        self.refuse(document)
        # An EXACT_MISSION slot that names a real mission at its real digest
        # binds; a wrong declared digest refuses even for the right id.
        exact_digest = good["missions"][third_id]["revisions"][0]["proposal_digest_sha256"]
        self.write_raw(json.dumps(good))
        fourth = self.propose(proof_contract=contract(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "EXACT_MISSION", "mission_id": third_id,
                       "revision": 1, "proposal_digest_sha256": exact_digest}}]))
        fourth_auth = self.approve(fourth["mission_id"], 1)["authorization_id"]
        document = self.store.load()
        state = self.attach_state(document, fourth["mission_id"], fourth_auth)
        op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY, self.clock())
        state["dependencies"].append(ms.new_dependency(
            hexid("mx", 0x97), self.activation_id(state), "upstream",
            mission_record.DEPENDENCY_KIND_MISSION, third_id, self.clock(),
            prov, op, seq))
        self.save(document)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))
        wrong = json.loads(self.read_bytes())
        wrong["missions"][fourth["mission_id"]]["revisions"][0]["proposal"][
            "proof_contract"]["required_dependencies"][0]["target"][
            "proposal_digest_sha256"] = "0" * 64
        # (that also breaks the proposal digest, so it fails closed twice over)
        self.refuse(wrong)

    def test_D10_checkpoint_recomputation_and_readiness_are_re_derived_on_load(self):
        ms = self.ms
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        activation_id = self.activation_id(state)
        op, seq, prov = self.op(document, state, ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_CHECKPOINT,
                                self.clock())
        contract_ = document["missions"][self.mission_id]["revisions"][0]["proposal"][
            "proof_contract"]
        activation = state["contract_activations"][0]
        state["checkpoints"].append(ms.new_checkpoint(
            hexid("mk", 1), activation_id, self.clock(), [], ["everything"],
            {"revision": 1, "proposal_digest_sha256": activation["proposal_digest_sha256"],
             "contract_digest_sha256": activation["contract_digest_sha256"]},
            [], [], {}, "retry on failure", "stop at budget", None, None, prov, op, seq))
        derived = self.mp.derive_checkpoint_fields(contract_, state, activation_id,
                                                   self.clock())
        state["checkpoints"][0].update(derived)
        self.save(document)
        good = json.loads(self.read_bytes())
        self.assertEqual(self.store.load(), good)
        self.assertEqual(good["mission_state"][self.mission_id]["checkpoints"][0][
            "next_permitted_step"], ms.NEXT_STEP_RESOLVE_DEPENDENCIES)
        # A stored checkpoint whose recomputation disagrees refuses on load.
        for path, value in (("next_permitted_step", ms.NEXT_STEP_CLOSE_COMPLETED),
                            ("budget", dict(derived["budget"], attempts_remaining=9))):
            with self.subTest(path):
                document = json.loads(json.dumps(good))
                document["mission_state"][self.mission_id]["checkpoints"][0][path] = value
                exc = self.refuse(document)
                self.assertIn(self.mp.PROBLEM_CHECKPOINT_DISAGREES, str(exc))
        # Time passing does NOT invalidate the stored checkpoint (its own
        # recorded_at is the clock), even though readiness is now stale.
        self.write_raw(json.dumps(good))
        self.clock.advance(100_000)
        self.assertEqual(self.store.load(), good)
        self.assertFalse(self.mp.readiness(
            contract_, good["mission_state"][self.mission_id], self.clock()
        )["satisfied"])

    def test_D11_caps_on_state_records_refuse_and_never_evict(self):
        mst = self.mst
        document = self.store.load()
        # Fill the per-record readiness list to the bound through the
        # constructors; the store accepts exactly the bound and refuses one
        # more with mission_store_full, leaving prior bytes intact.
        state = document["mission_state"][self.mission_id]
        for _ in range(self.ms.MAX_RESOURCE_READINESS_OBSERVATIONS):
            op, seq, prov = self.op(document, state,
                                    self.ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                    self.clock())
            state["resource_readiness"].append(self.ms.new_readiness_observation(
                "build_host", self.ms.READINESS_UNKNOWN, self.clock(), prov, op, seq))
        self.save(document)
        before = self.read_bytes()
        op, seq, prov = self.op(document, state,
                                self.ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(self.ms.new_readiness_observation(
            "build_host", self.ms.READINESS_UNKNOWN, self.clock(), prov, op, seq))
        with self.assertRaises(mst.MissionStoreError) as ctx:
            self.save(document)
        self.assertEqual(ctx.exception.problem, mst.PROBLEM_STORE_FULL)
        self.assertEqual(self.read_bytes(), before)
        # The record-count cap is a module constant applied to the map.
        document = json.loads(before)
        for index in range(mst.MAX_MISSION_STATE_RECORDS + 1):
            document["mission_state"][hexid("mn", 0xF000 + index)] = {}
        with self.assertRaises(mst.MissionStoreError) as ctx:
            self.save(document)
        self.assertEqual(ctx.exception.problem, mst.PROBLEM_STORE_FULL)

    def test_D12_registry_view_is_plain_data(self):
        view = self.mst.registry_view(self.good)
        self.assertEqual(sorted(view[self.mission_id]), [
            "closure_activation_id", "closure_proposal_digest_sha256",
            "closure_revision", "current_revision", "progress", "revision_digests"])
        self.assertEqual(view[self.mission_id]["current_revision"], 1)
        self.assertEqual(view[self.mission_id]["revision_digests"][1],
                         self.good["missions"][self.mission_id]["revisions"][0][
                             "proposal_digest_sha256"])
        self.assertEqual(view[self.mission_id]["progress"], self.ms.PROGRESS_IN_PROGRESS)
        self.assertIsNone(view[self.mission_id]["closure_activation_id"])
        plain = self.propose()
        view = self.mst.registry_view(self.store.load())
        self.assertIsNone(view[plain["mission_id"]]["progress"])

    def test_D13_prerequisite_drift_breaks_eligibility_not_history(self):
        ms = self.ms
        # A prerequisite Mission T, genuinely completed under revision 1.
        t_id, t_auth = self.approved_mission(required_dependencies=[])
        document = self.store.load()
        self.attach_state(document, t_id, t_auth)
        self.fill_local(document, t_id)
        self.complete_locally(document, t_id)
        self.save(document)
        t_digest = document["missions"][t_id]["revisions"][0]["proposal_digest_sha256"]
        # A dependent Mission D whose only required slot is EXACT_MISSION T@1.
        d = self.propose(proof_contract=contract(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "EXACT_MISSION", "mission_id": t_id, "revision": 1,
                       "proposal_digest_sha256": t_digest}}]))
        d_id = d["mission_id"]
        d_auth = self.approve(d_id, 1)["authorization_id"]
        document = self.store.load()
        state = self.attach_state(document, d_id, d_auth)
        activation_id = self.activation_id(state)
        contract_ = document["missions"][d_id]["revisions"][0]["proposal"]["proof_contract"]
        # Local state complete: artifact, accepted evidence, readiness,
        # bound and resolved slot.
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_ARTIFACT, self.clock())
        state["artifacts"].append(ms.new_artifact(
            hexid("mf", 0xD1), "test_log", mission_record.ARTIFACT_ROLE_VERIFICATION,
            ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:d", HEX_A, True, [],
            self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_SUBMIT_EVIDENCE, self.clock())
        state["evidence"].append(ms.new_evidence(
            hexid("mv", 0xD1), activation_id, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
            [hexid("mf", 0xD1)], self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_ACCEPT_EVIDENCE, self.clock())
        state["evidence"][0]["acceptance"] = ms.new_acceptance(
            self.clock(), "e" * 64, activation_id, prov, op, seq)
        op, seq, prov = self.op(document, state, ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_BIND_DEPENDENCY, self.clock())
        state["dependencies"].append(ms.new_dependency(
            hexid("mx", 0xD1), activation_id, "upstream",
            mission_record.DEPENDENCY_KIND_MISSION, t_id, self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_RESOLVE_DEPENDENCY, self.clock())
        state["dependencies"][0]["resolution"] = ms.new_resolution(
            self.clock(), hexid("mv", 0xD1), prov, op, seq)
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_CHECKPOINT, self.clock())
        activation = state["contract_activations"][0]
        state["checkpoints"].append(ms.new_checkpoint(
            hexid("mk", 0xD1), activation_id, self.clock(), ["all"], [],
            {"revision": 1, "proposal_digest_sha256": activation["proposal_digest_sha256"],
             "contract_digest_sha256": activation["contract_digest_sha256"]},
            [], [], {}, "none", "at budget", None, None, prov, op, seq))
        state["checkpoints"][0].update(self.mp.derive_checkpoint_fields(
            contract_, state, activation_id, self.clock()))
        self.save(document)
        good = json.loads(self.read_bytes())
        # R-22: the stored step is the conservative one; eligibility is
        # computed at read time from the registry view and is satisfied.
        checkpoint = good["mission_state"][d_id]["checkpoints"][0]
        self.assertEqual(checkpoint["next_permitted_step"],
                         ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE)
        view = self.mst.registry_view(good)
        self.assertEqual(view[t_id]["closure_revision"], 1)
        self.assertEqual(view[t_id]["closure_proposal_digest_sha256"], t_digest)
        self.assertEqual(view[t_id]["closure_activation_id"],
                         good["mission_state"][t_id]["closure"]["activation_id"])
        self.assertEqual(self.mp.closure_failures(
            contract_, good["mission_state"][d_id], activation_id, self.clock(), view), [])
        # A later EDIT of the completed prerequisite drifts it immediately.
        self.edit(t_id, 1, objective="t v2", proof_contract=contract())
        after = json.loads(self.read_bytes())
        view = self.mst.registry_view(after)
        self.assertEqual(view[t_id]["current_revision"], 2)
        self.assertEqual(view[t_id]["progress"], ms.PROGRESS_COMPLETED)
        failures = self.mp.closure_failures(
            contract_, after["mission_state"][d_id], activation_id, self.clock(), view)
        self.assertEqual([p for p, _ in failures], [self.mp.PROBLEM_PREREQUISITE_DRIFTED])
        self.assertFalse(self.mp.closure_eligibility(
            contract_, after["mission_state"][d_id], activation_id, self.clock(), view
        )["eligible"])
        # ...while the historical halves are untouched: D's state record,
        # its binding and its checkpoint are byte-identical, the store
        # reloads without disagreement, and T's completion stays recorded.
        self.assertEqual(after["mission_state"][d_id], good["mission_state"][d_id])
        self.assertEqual(after["mission_state"][t_id], good["mission_state"][t_id])
        self.assertEqual(self.store.load(), after)
        now = self.clock()
        self.clock.advance(50_000)
        self.assertEqual(self.store.load(), after)
        # Authorization EXPIRY on T would not unmake its completion (R-21.3):
        # the registry view carries no expiry input, and T's progress is
        # read from its own record.
        self.assertNotIn("expires_at", json.dumps(view))
        # A completion recorded under a different activation/revision of T
        # never satisfies the declared revision.
        forged = json.loads(json.dumps(after))
        forged_view = self.mst.registry_view(forged)
        forged_view[t_id]["current_revision"] = 1
        forged_view[t_id]["closure_revision"] = 2
        forged_view[t_id]["closure_proposal_digest_sha256"] = "0" * 64
        self.assertEqual([p for p, _ in self.mp.closure_failures(
            contract_, forged["mission_state"][d_id], activation_id, now,
            forged_view)], [self.mp.PROBLEM_PREREQUISITE_NOT_COMPLETE])


class DClosureProvabilityTests(DocumentFixture):
    """R-24 / criterion O: a persisted COMPLETED closure (and an asserted
    unsuccessful reason) is re-proved from the Mission's OWN state on every
    load and save, over the state as of the closure sequence with the
    closure's own closed_at as the clock. Foreign drift and the passage of
    time never make a valid historical record unreadable."""

    def setUp(self):
        super(DClosureProvabilityTests, self).setUp()
        ms = self.ms
        # Prerequisite T, genuinely COMPLETED under revision 1.
        self.t_id, t_auth = self.approved_mission(required_dependencies=[])
        document = self.store.load()
        self.attach_state(document, self.t_id, t_auth)
        self.fill_local(document, self.t_id)
        self.complete_locally(document, self.t_id)
        self.save(document)
        t_digest = document["missions"][self.t_id]["revisions"][0]["proposal_digest_sha256"]
        # Dependent D with EXACT_MISSION T@1; build a genuinely complete
        # local state, then close COMPLETED.
        d = self.propose(proof_contract=contract(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "EXACT_MISSION", "mission_id": self.t_id, "revision": 1,
                       "proposal_digest_sha256": t_digest}}]))
        self.d_id = d["mission_id"]
        d_auth = self.approve(self.d_id, 1)["authorization_id"]
        document = self.store.load()
        self.attach_state(document, self.d_id, d_auth)
        self.fill_local(document, self.d_id, bind=self.t_id)
        self.complete_locally(document, self.d_id)
        self.save(document)
        self.good = json.loads(self.read_bytes())
        self.assertEqual(self.store.load(), self.good)
        state = self.good["mission_state"][self.d_id]
        self.assertEqual(state["progress"], ms.PROGRESS_COMPLETED)
        self.closed_at = state["closure"]["closed_at"]

    def refuse_both(self, document, code=None, seal=True):
        code = code or self.mp.PROBLEM_CLOSURE_NOT_PROVABLE
        if seal:
            document = self.seal_document(json.loads(json.dumps(document)))
        self.write_raw(json.dumps(document))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.load()
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)
        # save() refuses before touching the filesystem: restore the good
        # bytes first so the refusal is provably a pre-write validation.
        self.write_raw(json.dumps(self.good))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.save(document)
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)
        # No temp file survives a refused save (the lock file is the
        # service's cross-process lock, not a write).
        self.assertEqual(
            sorted(n for n in os.listdir(self.directory) if n != "missions.lock"),
            ["missions.json"])

    def d_state(self, document):
        return document["mission_state"][self.d_id]

    def test_O1_setup_is_a_genuine_completed_record(self):
        state = self.d_state(self.good)
        contract_ = self.good["missions"][self.d_id]["revisions"][0]["proposal"][
            "proof_contract"]
        self.assertIsNone(self.mp.closure_proof_problem(contract_, state, state["closure"]))
        self.assertEqual(self.mp.closure_eligibility(
            contract_, state, self.activation_id(state), self.closed_at,
            self.mst.registry_view(self.good)), {"eligible": True, "failures": []})

    def test_O2_every_local_tamper_fails_closed_on_load_and_save(self):
        ms = self.ms
        tampers = {}
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["evidence"][0]["acceptance"] = None
        s["applied_operations"][3]["kind"] = ms.OPERATION_RECORD_CLAIM
        s["claims"].append(ms.new_claim(hexid("mc", 0xE1), self.activation_id(s),
                                        "tests_pass", "x", s["applied_operations"][3]["applied_at"],
                                        s["applied_operations"][3]["provenance"],
                                        s["applied_operations"][3]["operation_id"], 4))
        s["dependencies"][0]["resolution"] = None
        s["applied_operations"][6]["kind"] = ms.OPERATION_RECORD_CLAIM
        s["claims"].append(ms.new_claim(hexid("mc", 0xE2), self.activation_id(s),
                                        "tests_pass", "x", s["applied_operations"][6]["applied_at"],
                                        s["applied_operations"][6]["provenance"],
                                        s["applied_operations"][6]["operation_id"], 7))
        tampers["delete acceptance event"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["evidence"][0]["invalidation"] = dict(
            s["evidence"][0]["acceptance"], reason="x")
        s["evidence"][0]["invalidation"]["invalidated_at"] = (
            s["evidence"][0]["invalidation"].pop("accepted_at"))
        s["evidence"][0]["invalidation"].pop("content_digest_sha256")
        s["evidence"][0]["invalidation"].pop("activation_id")
        s["evidence"][0]["acceptance"] = None
        s["applied_operations"][3]["kind"] = ms.OPERATION_INVALIDATE_EVIDENCE
        s["dependencies"][0]["resolution"] = None
        s["applied_operations"][6]["kind"] = ms.OPERATION_RECORD_CLAIM
        s["claims"].append(ms.new_claim(hexid("mc", 0xE3), self.activation_id(s),
                                        "tests_pass", "x", s["applied_operations"][6]["applied_at"],
                                        s["applied_operations"][6]["provenance"],
                                        s["applied_operations"][6]["operation_id"], 7))
        tampers["flip acceptance to invalidation"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["evidence"][0]["acceptance"]["content_digest_sha256"] = "1" * 64
        tampers["change accepted content digest"] = (d, ms.PROBLEM_EVIDENCE_DIGEST)
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["artifacts"][0]["available"] = False
        tampers["required artifact unavailable"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["artifacts"][0]["content_digest_sha256"] = "2" * 64
        tampers["required artifact digest"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["closure"]["closed_at"] = self.closed_at + 5000
        s["closure"]["provenance"]["received_at"] = self.closed_at + 5000
        s["applied_operations"][-1]["applied_at"] = self.closed_at + 5000
        s["applied_operations"][-1]["provenance"]["received_at"] = self.closed_at + 5000
        tampers["acceptance is stale at a consistently later closed_at"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        blocker_op = s["applied_operations"][4]
        blocker_op["kind"] = ms.OPERATION_OPEN_BLOCKER
        s["resource_readiness"] = []
        s["blockers"].append(ms.new_blocker(
            hexid("mb", 0xE1), self.activation_id(s), "disk_full", ms.BLOCKER_SEVERITY_HARD,
            "no space", blocker_op["applied_at"], blocker_op["provenance"],
            blocker_op["operation_id"], 5))
        tampers["active HARD blocker before closure"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["dependencies"][0]["resolution"] = None
        s["applied_operations"][6]["kind"] = ms.OPERATION_RECORD_CLAIM
        s["claims"].append(ms.new_claim(hexid("mc", 0xE4), self.activation_id(s),
                                        "tests_pass", "x", s["applied_operations"][6]["applied_at"],
                                        s["applied_operations"][6]["provenance"],
                                        s["applied_operations"][6]["operation_id"], 7))
        tampers["delete required dependency resolution"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["resource_readiness"] = []
        s["applied_operations"][4]["kind"] = ms.OPERATION_RECORD_CLAIM
        s["claims"].append(ms.new_claim(hexid("mc", 0xE5), self.activation_id(s),
                                        "tests_pass", "x", s["applied_operations"][4]["applied_at"],
                                        s["applied_operations"][4]["provenance"],
                                        s["applied_operations"][4]["operation_id"], 5))
        tampers["delete required readiness observation"] = d
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["resource_readiness"][0]["observed_at"] = self.closed_at - 100_000
        tampers["stale readiness at closed_at"] = d
        for label, document in tampers.items():
            with self.subTest(label):
                if isinstance(document, tuple):
                    self.refuse_both(document[0], document[1])
                else:
                    self.refuse_both(document)
        # A blocker opened AFTER the closure sequence cannot exist (terminal
        # is terminal); one resolved before it does not disturb the proof.
        d = json.loads(json.dumps(self.good)); s = self.d_state(d)
        s["closure"]["activation_id"] = None
        self.refuse_both(d, ms.PROBLEM_CLOSURE)

    def test_O3_asserted_unsuccessful_reasons_are_re_proved(self):
        ms = self.ms
        # Build an IN_PROGRESS Mission and close it unsuccessfully with a
        # falsified budget_exhausted reason (0 of 3 attempts consumed).
        u_id, u_auth = self.approved_mission()
        document = self.store.load()
        state = self.attach_state(document, u_id, u_auth)
        op, seq, prov = self.op(document, state, ms.OPERATION_CLOSE_UNSUCCESSFUL,
                                self.clock())
        state["closure"] = ms.new_closure(
            ms.PROGRESS_CLOSED_UNSUCCESSFUL, ms.CLOSURE_REASON_BUDGET_EXHAUSTED,
            "out of attempts", self.clock(), self.activation_id(state), prov, op, seq)
        state["progress"] = ms.PROGRESS_CLOSED_UNSUCCESSFUL
        self.good = json.loads(self.read_bytes())
        self.refuse_both(document)
        # The same with hard_blocker_unresolvable and no HARD blocker.
        document["mission_state"][u_id]["closure"]["reason"] = ms.CLOSURE_REASON_HARD_BLOCKER
        self.refuse_both(document)
        # closed_by_caller asserts nothing and is accepted.
        document["mission_state"][u_id]["closure"]["reason"] = ms.CLOSURE_REASON_CALLER_CLOSED
        self.save(document)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))
        # A genuinely exhausted budget (3 of 3) is provable.
        good = json.loads(self.read_bytes())
        v_id, v_auth = self.approved_mission()
        document = self.store.load()
        state = self.attach_state(document, v_id, v_auth)
        for attempt in (1, 2, 3):
            op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_CONTINUATION,
                                    self.clock())
            state["continuations"].append(ms.new_continuation(
                attempt, "again", self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_CLOSE_UNSUCCESSFUL,
                                self.clock())
        state["closure"] = ms.new_closure(
            ms.PROGRESS_CLOSED_UNSUCCESSFUL, ms.CLOSURE_REASON_BUDGET_EXHAUSTED,
            "out of attempts", self.clock(), self.activation_id(state), prov, op, seq)
        state["progress"] = ms.PROGRESS_CLOSED_UNSUCCESSFUL
        self.save(document)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))

    def test_O4_time_and_foreign_edit_never_make_history_unreadable(self):
        contract_ = self.good["missions"][self.d_id]["revisions"][0]["proposal"][
            "proof_contract"]
        state = self.d_state(self.good)
        activation_id = self.activation_id(state)
        # Far past every staleness bound (3600 s evidence, 600 s readiness).
        self.clock.advance(10 * 365 * 24 * 3600)
        self.assertEqual(self.store.load(), self.good)
        # Foreign EDIT of the completed prerequisite.
        self.edit(self.t_id, 1, objective="t v2", proof_contract=contract())
        after = json.loads(self.read_bytes())
        self.assertEqual(after["mission_state"], self.good["mission_state"])
        self.assertEqual(self.store.load(), after)
        # The stored record is still provable at its own closed_at ...
        self.assertIsNone(self.mp.closure_proof_problem(contract_, state, state["closure"]))
        # ... while the LIVE projection reports both the drift and the
        # staleness, and only the live projection does.
        live = self.mp.closure_eligibility(
            contract_, after["mission_state"][self.d_id], activation_id, self.clock(),
            self.mst.registry_view(after))
        self.assertFalse(live["eligible"])
        codes = [f["problem"] for f in live["failures"]]
        self.assertIn(self.mp.PROBLEM_PREREQUISITE_DRIFTED, codes)
        self.assertIn(self.mp.PROBLEM_PROOF_NOT_SATISFIED, codes)
        self.assertIn(self.mp.PROBLEM_RESOURCE_NOT_READY, codes)
        # Task 4 authority: D's own authorization is unaffected by T's edit.
        self.assertEqual(self.service.get(self.d_id)["live_authorization_id"],
                         self.good["mission_state"][self.d_id]["contract_activations"][0][
                             "authorization_id"])


class DHistoricalAuthorityBindingTests(DocumentFixture):
    """Supervisor finding 6 (precision on R-24): an activation or a
    completion recorded before its authorization was issued, or at or
    after an already-effective expiry or revocation, is a forgery and
    refuses; current expiry or a later EDIT preserves valid history."""

    def setUp(self):
        super(DHistoricalAuthorityBindingTests, self).setUp()
        created = self.propose(proof_contract=contract(required_dependencies=[]))
        self.mission_id = created["mission_id"]
        self.expires_at = self.clock() + 1000
        self.authorization_id = self.approve(self.mission_id, 1,
                                             expires_at=self.expires_at)["authorization_id"]
        self.clock.advance(10)
        document = self.store.load()
        self.attach_state(document, self.mission_id, self.authorization_id)
        self.save(document)
        self.good = json.loads(self.read_bytes())
        self.issued_at = self.good["authorizations"][self.authorization_id]["issued_at"]

    def refuse(self, document, code=None, seal=True):
        code = code or self.ms.PROBLEM_AUTHORITY_WINDOW
        if seal:
            document = self.seal_document(json.loads(json.dumps(document)))
        self.write_raw(json.dumps(document))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.load()
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)
        self.write_raw(json.dumps(self.good))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.save(document)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)

    def forge_activation_time(self, document, when):
        """A time-consistent forgery: the record, its operation's applied_at
        and both provenance receipt times all move together."""
        state = document["mission_state"][self.mission_id]
        state["contract_activations"][0]["activated_at"] = when
        state["contract_activations"][0]["provenance"]["received_at"] = when
        state["applied_operations"][0]["applied_at"] = when
        state["applied_operations"][0]["provenance"]["received_at"] = when
        return document

    def loads(self, document):
        document = self.seal_document(json.loads(json.dumps(document)))
        self.write_raw(json.dumps(document))
        self.assertEqual(self.store.load(), document)
        self.store.save(document)
        self.assertEqual(self.store.load(), document)

    def forge_closure_time(self, document, when):
        state = document["mission_state"][self.mission_id]
        state["closure"]["closed_at"] = when
        state["closure"]["provenance"]["received_at"] = when
        state["applied_operations"][-1]["applied_at"] = when
        state["applied_operations"][-1]["provenance"]["received_at"] = when
        return document

    def test_H1_activation_before_issuance_or_after_expiry_refuses(self):
        # R-26.4 boundaries: issued_at - 1 refuses, issued_at loads;
        # expires_at refuses (strict), expires_at - 1 loads.
        self.refuse(self.forge_activation_time(json.loads(json.dumps(self.good)),
                                               self.issued_at - 1))
        self.loads(self.forge_activation_time(json.loads(json.dumps(self.good)),
                                              self.issued_at))
        self.refuse(self.forge_activation_time(json.loads(json.dumps(self.good)),
                                               self.expires_at))
        self.loads(self.forge_activation_time(json.loads(json.dumps(self.good)),
                                              self.expires_at - 1))
        # Current expiry does not unmake a record inside the window
        # (recorded numbers, never the wall clock).
        document = self.forge_activation_time(json.loads(json.dumps(self.good)),
                                              self.issued_at)
        self.write_raw(json.dumps(document))
        self.clock.advance(5000)
        self.assertEqual(self.store.load(), document)
        self.assertFalse(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1).valid)

    def test_H2_activation_after_revocation_refuses_but_edit_preserves_history(self):
        self.edit(self.mission_id, 1, objective="v2", proof_contract=contract())
        after = json.loads(self.read_bytes())
        revoked_at = after["authorizations"][self.authorization_id]["revocation"][
            "revoked_at"]
        self.assertTrue(after["authorizations"][self.authorization_id]["revocation"][
            "revoked"])
        # History preserved: the activation predates the revocation.
        self.assertEqual(self.store.load(), after)
        self.good = after
        # R-26.4: T == revoked_at loads and stays loadable (a live-validated
        # operation in the EDIT's own second is legitimate); revoked_at + 1
        # refuses. The activation is at revision 1, which the EDIT made
        # stale, so time-consistency of the single operation still holds.
        self.loads(self.forge_activation_time(json.loads(json.dumps(after)), revoked_at))
        self.clock.advance(5000)
        self.assertEqual(self.store.load(),
                         self.forge_activation_time(json.loads(json.dumps(after)),
                                                    revoked_at))
        self.refuse(self.forge_activation_time(json.loads(json.dumps(after)),
                                               revoked_at + 1))

    def test_H3_completion_after_expiry_or_revocation_refuses(self):
        ms = self.ms
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        activation_id = self.activation_id(state)
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_ARTIFACT, self.clock())
        state["artifacts"].append(ms.new_artifact(
            hexid("mf", 0x71), "test_log", mission_record.ARTIFACT_ROLE_VERIFICATION,
            ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", HEX_A, True, [],
            self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_SUBMIT_EVIDENCE, self.clock())
        state["evidence"].append(ms.new_evidence(
            hexid("mv", 0x71), activation_id, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
            [hexid("mf", 0x71)], self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_ACCEPT_EVIDENCE, self.clock())
        state["evidence"][0]["acceptance"] = ms.new_acceptance(self.clock(), "e" * 64,
                                                                activation_id, prov, op, seq)
        op, seq, prov = self.op(document, state, ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, self.clock(), prov, op, seq))
        op, seq, prov = self.op(document, state, ms.OPERATION_COMPLETE, self.clock())
        state["closure"] = ms.new_closure(
            ms.PROGRESS_COMPLETED, ms.CLOSURE_REASON_PROOF_COMPLETE, "done",
            self.clock(), activation_id, prov, op, seq)
        state["progress"] = ms.PROGRESS_COMPLETED
        self.save(document)
        good = json.loads(self.read_bytes())
        self.good = good
        self.assertEqual(self.store.load(), good)
        # Closed at or after expiry (time-consistently forged): refused.
        self.refuse(self.forge_closure_time(json.loads(json.dumps(good)), self.expires_at))
        # A later EDIT (before expiry) revokes the authorization; the
        # completion recorded before it stays readable (R-25.2).
        self.edit(self.mission_id, 1, objective="v2", proof_contract=contract())
        after = json.loads(self.read_bytes())
        self.assertTrue(after["authorizations"][self.authorization_id]["revocation"]["revoked"])
        self.assertEqual(after["mission_state"], good["mission_state"])
        self.assertEqual(self.store.load(), after)
        revoked_at = after["authorizations"][self.authorization_id]["revocation"]["revoked_at"]
        self.assertLess(revoked_at, self.expires_at)
        # A completion at the revocation instant loads (R-26); one second
        # after it refuses.
        self.good = after
        for when, expect_load in ((revoked_at, True), (revoked_at + 1, False)):
            document = json.loads(json.dumps(after))
            self.forge_closure_time(document, when)
            if expect_load:
                self.loads(document)
            else:
                self.refuse(document)
        # Completion at expires_at - 1 loads; at expires_at refuses (strict).
        # Readiness observed in the same second keeps the local proof valid.
        self.good = good
        for when, expect_load in ((self.expires_at - 1, True), (self.expires_at, False)):
            document = json.loads(json.dumps(good))
            state = document["mission_state"][self.mission_id]
            self.forge_closure_time(document, when)
            state["resource_readiness"][0]["observed_at"] = when
            state["resource_readiness"][0]["provenance"]["received_at"] = when
            state["applied_operations"][-2]["applied_at"] = when
            state["applied_operations"][-2]["provenance"]["received_at"] = when
            if expect_load:
                self.loads(document)
            else:
                self.refuse(document)
        # Current expiry, long after, preserves the genuine record.
        self.write_raw(json.dumps(after))
        self.clock.advance(50_000)
        self.assertEqual(self.store.load(), after)
        self.assertFalse(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1).valid)

    def test_H4_monotone_time_base(self):
        ms = self.ms
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        self.clock.advance(3)
        op, seq, prov = self.op(document, state, ms.OPERATION_OBSERVE_RESOURCE_READINESS,
                                self.clock())
        state["resource_readiness"].append(ms.new_readiness_observation(
            "build_host", ms.READINESS_READY, self.clock() - 2, prov, op, seq))
        self.clock.advance(3)
        op, seq, prov = self.op(document, state, ms.OPERATION_RECORD_CLAIM, self.clock())
        state["claims"].append(ms.new_claim(hexid("mc", 0x61), self.activation_id(state),
                                            "tests_pass", "x", self.clock(), prov, op, seq))
        self.save(document)
        self.good = json.loads(self.read_bytes())
        # An observation may precede its operation (it did, by 2 s).
        self.assertEqual(self.store.load(), self.good)
        code = ms.PROBLEM_TIME_INCONSISTENT
        # Non-monotone applied operations.
        document = json.loads(json.dumps(self.good))
        ops = document["mission_state"][self.mission_id]["applied_operations"]
        ops[1]["applied_at"] = ops[0]["applied_at"] - 1
        document["mission_state"][self.mission_id]["resource_readiness"][0][
            "observed_at"] = ops[1]["applied_at"]
        self.refuse(document, code)
        # A sub-record timestamp disagreeing with its operation.
        document = json.loads(json.dumps(self.good))
        document["mission_state"][self.mission_id]["claims"][0]["claimed_at"] += 1
        self.refuse(document, code)
        # A future-dated readiness observation is refused outright.
        document = json.loads(json.dumps(self.good))
        good_ops = self.good["mission_state"][self.mission_id]["applied_operations"]
        document["mission_state"][self.mission_id]["resource_readiness"][0][
            "observed_at"] = good_ops[1]["applied_at"] + 1
        self.refuse(document, code)


# ====================================================================
# E. Authorization: the narrow state_operation reconcile branch
# ====================================================================


class EReconcileRegistryTests(DocumentFixture):

    def setUp(self):
        super(EReconcileRegistryTests, self).setUp()
        self.mission_id, self.authorization_id = self.approved_mission()
        self.second_id, self.second_auth = self.approved_mission()
        document = self.store.load()
        self.attach_state(document, self.mission_id, self.authorization_id)
        self.attach_state(document, self.second_id, self.second_auth)
        self.save(document)
        self.good = json.loads(self.read_bytes())
        self.assertIsNone(self.ma.reconcile_registry(self.good))

    def test_E1_consumed_state_operation_must_appear_exactly_once(self):
        document = json.loads(json.dumps(self.good))
        orphan = hexid("mo", 0x6)
        document["reservations"][orphan] = {
            "reserved_at": 1, "kind": "state_operation",
            "context": self.context.as_dict(), "consumed_by": orphan}
        problem = self.ma.reconcile_registry(document)
        self.assertEqual(problem[0], self.ma.PROBLEM_HISTORY_INCONSISTENT)
        self.assertIn(orphan, problem[1])
        # The same operation id applied in two missions' records.
        document = json.loads(json.dumps(self.good))
        first_op = document["mission_state"][self.mission_id]["applied_operations"][0]
        second_state = document["mission_state"][self.second_id]
        second_state["applied_operations"][0]["operation_id"] = first_op["operation_id"]
        problem = self.ma.reconcile_registry(document)
        self.assertEqual(problem[0], self.ma.PROBLEM_HISTORY_INCONSISTENT)
        # consumed_by must be the operation id itself.
        document = json.loads(json.dumps(self.good))
        document["reservations"][first_op["operation_id"]]["consumed_by"] = hexid("mo", 0x5)
        self.assertEqual(self.ma.reconcile_registry(document)[0],
                         self.ma.PROBLEM_HISTORY_INCONSISTENT)
        # An unconsumed reservation and a Task-4-era document (no
        # mission_state key at all) both reconcile.
        document = json.loads(json.dumps(self.good))
        document["reservations"][hexid("mo", 0x4)] = {
            "reserved_at": 1, "kind": "state_operation",
            "context": self.context.as_dict(), "consumed_by": None}
        self.assertIsNone(self.ma.reconcile_registry(document))
        legacy = dict((k, v) for k, v in self.good.items() if k != "mission_state")
        legacy["reservations"] = dict(
            (k, v) for k, v in legacy["reservations"].items()
            if v["kind"] != "state_operation")
        self.assertIsNone(self.ma.reconcile_registry(legacy))

    def test_E2_task4_request_and_decision_reconciliation_is_unchanged(self):
        document = json.loads(json.dumps(self.good))
        decision_id = document["missions"][self.mission_id]["decisions"][0]["decision_id"]
        document["reservations"][decision_id]["consumed_by"] = hexid("md", 0x3)
        self.assertEqual(self.ma.reconcile_registry(document)[0],
                         self.ma.PROBLEM_HISTORY_INCONSISTENT)
        document = json.loads(json.dumps(self.good))
        orphan = hexid("md", 0x2)
        document["reservations"][orphan] = {
            "reserved_at": 1, "kind": "decision",
            "context": self.context.as_dict(), "consumed_by": orphan}
        self.assertEqual(self.ma.reconcile_registry(document)[0],
                         self.ma.PROBLEM_HISTORY_INCONSISTENT)
        document = json.loads(json.dumps(self.good))
        request_id = document["missions"][self.mission_id]["request_id"]
        document["reservations"][request_id]["consumed_by"] = self.second_id
        self.assertEqual(self.ma.reconcile_registry(document)[0],
                         self.ma.PROBLEM_HISTORY_INCONSISTENT)
        # The one validation path refuses through the store as before, and
        # a state record never changes what it says about authority.
        self.assertTrue(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1).valid)
        self.assertEqual(self.service.validate_authorization(
            self.authorization_id, self.second_id, 1).problem,
            self.ma.PROBLEM_WRONG_MISSION)
        self.write_raw(json.dumps(document))
        self.assertFalse(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1).valid)

    def test_E3_authorization_module_diff_is_the_one_branch(self):
        import ast
        source = (REPO_ROOT / "mission" / "authorization.py").read_text()
        tree = ast.parse(source)
        names = sorted(n.name for n in tree.body
                       if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
        # The Task 4 surface is intact; nothing new is defined.
        self.assertEqual(names, sorted([
            "AuthorityCheck", "_authorization_agrees", "_history", "_ledger",
            "_mismatch", "_refusal", "_reservation_agrees", "authorization_digest",
            "find_authorization_by_digest", "issue_mission_authorization",
            "new_ledger_entry", "reconcile_mission_history", "reconcile_registry",
            "revoke", "validate_authorization_record", "validate_authorization_use",
            "validate_ledger_entry",
        ]))
        self.assertEqual(source.count("state_operation"), 2)
        self.assertEqual(source.count("mission_state"), 1)


# ====================================================================
# F. Service surface (stage 2)
# ====================================================================


class ServiceStateFixture(DocumentFixture):
    """Drive the REAL MissionService Task 5 surface end to end."""

    def setUp(self):
        super(ServiceStateFixture, self).setUp()
        self.other = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:502",
        )

    def oid(self, context=None):
        return self.service.mint_state_operation_id(context or self.context)

    def seq(self, mission_id):
        return self.service.get_state(mission_id)["sequence"]

    def call(self, name, mission_id, *args, **kwargs):
        """Invoke a mutating operation with a fresh id and the current
        sequence; returns the outcome."""
        context = kwargs.pop("context", self.context)
        method = getattr(self.service, name)
        return method(mission_id, self.oid(context), self.seq(mission_id), *args,
                      context=context, **kwargs)

    def ready_mission(self, **contract_overrides):
        """Create, approve and activate; returns mission_id."""
        mission_id, _ = self.approved_mission(**contract_overrides)
        self.clock.advance(1)
        self.call("activate_proof_contract", mission_id)
        return mission_id

    def make_local_complete(self, mission_id, bind=None):
        ms = self.ms
        artifact = self.call("record_artifact", mission_id, "test_log",
                             mission_record.ARTIFACT_ROLE_VERIFICATION,
                             ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A,
                             True, [])
        evidence = self.call("submit_evidence", mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                             "e" * 64, [artifact["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("observe_resource_readiness", mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        if bind is not None:
            bound = self.call("bind_dependency", mission_id, "upstream", bind)
            self.call("resolve_dependency", mission_id, bound["dependency_id"],
                      evidence["evidence_id"])
        return evidence["evidence_id"]

    def completed_prerequisite(self):
        t_id = self.ready_mission(required_dependencies=[])
        self.make_local_complete(t_id)
        self.call("complete_successfully", t_id, "all proof accepted")
        return t_id

    def dependent_on(self, t_id):
        digest = self.store.load()["missions"][t_id]["revisions"][0][
            "proposal_digest_sha256"]
        return self.ready_mission(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "EXACT_MISSION", "mission_id": t_id, "revision": 1,
                       "proposal_digest_sha256": digest}}])

    def assertRefuses(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(mission_record.MissionError) as ctx:
            callable_(*args, **kwargs)
        self.assertEqual(ctx.exception.problem, problem, str(ctx.exception))
        return ctx.exception

    def authority_bytes(self):
        document = json.loads(self.read_bytes())
        return json.dumps({"a": document["authorizations"],
                           "l": document["authority_ledger"]}, sort_keys=True)

    def refuse_raw(self, document, code):
        good = self.read_bytes()
        self.write_raw(json.dumps(document))
        before = self.read_bytes()
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.load()
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), before)
        with open(self.store.path, "wb") as handle:
            handle.write(good)
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.store.save(document)
        self.assertIn(code, str(ctx.exception))
        self.assertEqual(self.read_bytes(), good)
        return ctx.exception

    def stable(self):
        document = json.loads(self.read_bytes())
        self.assertEqual(self.store.load(), document)
        self.store.save(document)
        self.assertEqual(json.loads(self.read_bytes()), document)
        self.assertIsNone(self.ma.reconcile_registry(document))
        return document

    def state_of(self, document, mission_id):
        return document["mission_state"][mission_id]


class FServiceIdentityTests(ServiceStateFixture):

    def test_F1_state_operation_ids_are_minted_reserved_and_bound(self):
        from mission import state_service as service_module
        operation_id = self.oid()
        self.assertIsNone(mission_record.id_problem(operation_id, "mo"))
        reservation = self.store.load()["reservations"][operation_id]
        self.assertEqual(reservation["kind"], "state_operation")
        self.assertIsNone(reservation["consumed_by"])
        mission_id, _ = self.approved_mission()
        # A caller-chosen id refuses; a foreign principal refuses.
        self.assertRefuses(service_module.PROBLEM_UNKNOWN_STATE_OPERATION_ID,
                           self.service.activate_proof_contract, mission_id,
                           hexid("mo", 0xBAD), 0, self.context)
        self.assertRefuses(service_module.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                           self.service.activate_proof_contract, mission_id,
                           operation_id, 0, self.other)
        # A decision id is not a state operation id.
        decision_id = self.service.mint_decision_id(self.context)
        self.assertRefuses(mission_record.PROBLEM_ID_GRAMMAR,
                           self.service.activate_proof_contract, mission_id,
                           decision_id, 0, self.context)
        # Task 4 minting is unchanged.
        self.assertIsNone(mission_record.id_problem(
            self.service.mint_request_id(self.context), "mq"))


class FContractActivationTests(ServiceStateFixture):

    def test_F2_activation_requires_contract_and_live_authority(self):
        from mission import state_service as sm
        plain = self.propose()["mission_id"]
        self.approve(plain, 1)
        self.assertRefuses(sm.PROBLEM_NO_PROOF_CONTRACT, self.call,
                           "activate_proof_contract", plain)
        pending = self.propose(proof_contract=contract())["mission_id"]
        self.assertRefuses(sm.PROBLEM_STATE_NOT_AUTHORIZED, self.call,
                           "activate_proof_contract", pending)
        self.assertNotIn(pending, self.store.load()["mission_state"])
        self.approve(pending, 1)
        before = self.authority_bytes()
        outcome = self.call("activate_proof_contract", pending)
        self.assertEqual(outcome["revision"], 1)
        self.assertFalse(outcome["idempotent"])
        self.assertEqual(outcome["progress"], self.ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(outcome["sequence"], 1)
        state = self.service.get_state(pending)
        self.assertEqual(state["contract"]["activation_id"], outcome["activation_id"])
        self.assertTrue(state["contract"]["current"])
        self.assertTrue(state["contract"]["authority_live"])
        self.assertEqual(state["contract"]["content"],
                         mission_record.validate_proof_contract(contract()))
        # Same-revision replacement refuses; authority untouched (R-5.5).
        self.assertRefuses(sm.PROBLEM_CONTRACT_ALREADY_ACTIVE, self.call,
                           "activate_proof_contract", pending)
        self.assertEqual(self.authority_bytes(), before)
        # Contract content cannot be supplied: no parameter accepts it.
        import inspect
        params = inspect.signature(self.service.activate_proof_contract).parameters
        self.assertEqual(list(params), ["mission_id", "operation_id",
                                        "expected_sequence", "context"])

    def test_F3_edit_stales_the_contract_and_reauthorization_reactivates(self):
        from mission import state_service as sm
        mission_id = self.ready_mission()
        # EDIT: prior authority revoked by Task 4; every dependent op refuses.
        self.edit(mission_id, 1, objective="v2",
                  proof_contract=contract(continuation_budget={
                      "max_attempts": 5, "max_checkpoints": 8}))
        for name, args in (
            ("record_claim", ("tests_pass", "done")),
            ("record_artifact", ("test_log", "VERIFICATION", "OPAQUE_REFERENCE",
                                 "opaque:1", HEX_A, True, [])),
            ("submit_evidence", ("tests_pass", "VERIFICATION_RECORD", "e" * 64, [])),
            ("open_blocker", ("disk_full", "no space")),
            ("observe_resource_readiness", ("build_host", "READY", self.clock())),
            ("record_continuation", ("retry",)),
            ("record_checkpoint", ([], [], "retry", "stop")),
            ("complete_successfully", ("done",)),
        ):
            with self.subTest(name):
                self.assertRefuses(sm.PROBLEM_CONTRACT_STALE, self.call, name,
                                   mission_id, *args)
        projection = self.service.get_state(mission_id)
        self.assertFalse(projection["contract"]["current"])
        self.assertEqual(projection["contract"]["problem"], sm.PROBLEM_CONTRACT_STALE)
        # Activating again needs a fresh APPROVE of revision 2.
        self.assertRefuses(sm.PROBLEM_STATE_NOT_AUTHORIZED, self.call,
                           "activate_proof_contract", mission_id)
        self.approve(mission_id, 2)
        outcome = self.call("activate_proof_contract", mission_id)
        self.assertEqual(outcome["revision"], 2)
        state = self.service.get_state(mission_id)
        self.assertEqual(len(state["record"]["contract_activations"]), 2)
        self.assertEqual(state["contract"]["content"]["continuation_budget"][
            "max_attempts"], 5)
        # The store reloads the two-activation history cleanly.
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))
        # Abandon and close_unsuccessful(by caller) never need a live contract.
        stale_id = self.ready_mission()
        self.edit(stale_id, 1, objective="v2", proof_contract=contract())
        outcome = self.call("abandon", stale_id, "not worth it")
        self.assertEqual(outcome["progress"], self.ms.PROGRESS_ABANDONED)

    def test_F4_expired_authority_stales_the_contract(self):
        from mission import state_service as sm
        created = self.propose(proof_contract=contract())
        mission_id = created["mission_id"]
        self.approve(mission_id, 1, expires_at=self.clock() + 100)
        self.call("activate_proof_contract", mission_id)
        self.clock.advance(200)
        self.assertRefuses(sm.PROBLEM_CONTRACT_STALE, self.call, "record_claim",
                           mission_id, "tests_pass", "done")
        self.assertRefuses(sm.PROBLEM_STATE_NOT_AUTHORIZED, self.call,
                           "activate_proof_contract", mission_id)


class FIdempotencyAndConcurrencyTests(ServiceStateFixture):

    def test_F5_replay_returns_recorded_outcome_and_consumes_nothing(self):
        from mission import state_service as sm
        mission_id = self.ready_mission()
        operation_id = self.oid()
        sequence = self.seq(mission_id)
        first = self.service.record_continuation(mission_id, operation_id, sequence,
                                                 "retry once", self.context)
        bytes_after = self.read_bytes()
        again = self.service.record_continuation(mission_id, operation_id, sequence,
                                                 "retry once", self.context)
        self.assertEqual(dict(again, idempotent=False), first)
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.read_bytes(), bytes_after)
        self.assertEqual(self.service.get_state(mission_id)["budget"]["attempts_consumed"], 1)
        # Different content under the same id conflicts; nothing changes.
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_continuation, mission_id,
                           operation_id, sequence, "retry twice", self.context)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_claim, mission_id,
                           operation_id, sequence, "tests_pass", "x", self.context)
        other_mission = self.ready_mission()
        bytes_after = self.read_bytes()
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_continuation, other_mission,
                           operation_id, 1, "retry once", self.context)
        # A foreign principal cannot replay it.
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                           self.service.record_continuation, mission_id,
                           operation_id, sequence, "retry once", self.other)
        self.assertEqual(self.read_bytes(), bytes_after)
        # The consumed reservation reconciles (exactly once).
        self.assertEqual(self.store.load()["reservations"][operation_id]["consumed_by"],
                         operation_id)

    def test_F6_stale_sequence_and_concurrent_writers(self):
        from mission import state_service as sm
        import threading
        mission_id = self.ready_mission()
        sequence = self.seq(mission_id)
        self.assertRefuses(sm.PROBLEM_STALE_SEQUENCE, self.service.record_claim,
                           mission_id, self.oid(), sequence + 1, "tests_pass", "x",
                           self.context)
        self.assertRefuses(sm.PROBLEM_STALE_SEQUENCE, self.service.record_claim,
                           mission_id, self.oid(), sequence - 1, "tests_pass", "x",
                           self.context)
        ids = [self.oid() for _ in range(4)]
        results = {}
        gate = threading.Barrier(4)

        def writer(index):
            gate.wait()
            try:
                self.service.record_claim(mission_id, ids[index], sequence,
                                          "tests_pass", "w%d" % index, self.context)
                results[index] = "won"
            except mission_record.MissionError as exc:
                results[index] = exc.problem
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results.values()),
                         [sm.PROBLEM_STALE_SEQUENCE] * 3 + ["won"])
        state = self.service.get_state(mission_id)
        self.assertEqual(state["sequence"], sequence + 1)
        self.assertEqual(len(state["record"]["claims"]), 1)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))


class FLifecycleTests(ServiceStateFixture):

    def test_F7_happy_path_to_proof_complete_closure(self):
        ms = self.ms
        t_id = self.completed_prerequisite()
        self.assertEqual(self.service.get_state(t_id)["progress"], ms.PROGRESS_COMPLETED)
        d_id = self.dependent_on(t_id)
        before = self.authority_bytes()
        claim = self.call("record_claim", d_id, "tests_pass", "I believe they pass")
        self.assertIn("claim_id", claim)
        # A claim is not proof.
        self.assertEqual(self.service.get_state(d_id)["proof"]["requirements"]["tests_pass"],
                         self.mp.REQUIREMENT_MISSING)
        self.make_local_complete(d_id, bind=t_id)
        checkpoint = self.call("record_checkpoint", d_id, ["tests", "artifacts"],
                               [], "retry on flake", "stop at budget")
        self.assertEqual(checkpoint["next_permitted_step"],
                         ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE)
        self.assertIsNone(checkpoint["refusal"])
        projection = self.service.get_state(d_id)
        self.assertEqual(projection["closure_eligibility"], {"eligible": True,
                                                              "failures": []})
        self.assertEqual(projection["latest_checkpoint"]["next_permitted_step"],
                         ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE)
        self.assertTrue(projection["latest_checkpoint"]["closure_eligibility"]["eligible"])
        self.assertNotIn("closure_eligibility",
                         projection["record"]["checkpoints"][0])
        # Recording against the unchanged contract touched no authority.
        self.assertEqual(self.authority_bytes(), before)
        done = self.call("complete_successfully", d_id, "all proof accepted")
        self.assertEqual(done["progress"], ms.PROGRESS_COMPLETED)
        self.assertEqual(done["reason"], ms.CLOSURE_REASON_PROOF_COMPLETE)
        # Reload equivalence and terminal-is-terminal.
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call, "record_claim",
                           d_id, "tests_pass", "again")
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call, "abandon", d_id, "x")
        # Task 4's mission state was never touched.
        self.assertEqual(self.service.get(d_id)["record"]["state"], "AUTHORIZED")
        self.assertEqual(self.authority_bytes(), before)

    def test_F8_each_closure_failure_has_its_own_code(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        t_id = self.completed_prerequisite()
        d_id = self.dependent_on(t_id)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        artifact = self.call("record_artifact", d_id, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, False, [])
        evidence = self.call("submit_evidence", d_id, "tests_pass",
                             "VERIFICATION_RECORD", "e" * 64, [artifact["artifact_id"]])
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        self.call("accept_evidence", d_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        # Unavailable required artifact: proof mismatched AND the independent
        # artifact pass; the first code reported is the proof one.
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        good_artifact = self.call("record_artifact", d_id, "test_log", "VERIFICATION",
                                  "OPAQUE_REFERENCE", "opaque:2", HEX_A, True, [])
        evidence = self.call("submit_evidence", d_id, "tests_pass",
                             "VERIFICATION_RECORD", "f" * 64,
                             [good_artifact["artifact_id"]])
        self.call("invalidate_evidence", d_id,
                  self.service.get_state(d_id)["record"]["evidence"][0]["evidence_id"],
                  "superseded")
        self.call("accept_evidence", d_id, evidence["evidence_id"], "f" * 64,
                  context=self.other)
        blocker = self.call("open_blocker", d_id, "disk_full", "no space")
        self.assertEqual(blocker["severity"], ms.BLOCKER_SEVERITY_HARD)
        self.assertEqual(blocker["progress"], ms.PROGRESS_BLOCKED)
        self.assertRefuses(mp.PROBLEM_HARD_BLOCKER_ACTIVE, self.call,
                           "complete_successfully", d_id, "x")
        resolved = self.call("resolve_blocker", d_id, blocker["blocker_id"],
                             evidence["evidence_id"])
        self.assertEqual(resolved["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_UNRESOLVED, self.call,
                           "complete_successfully", d_id, "x")
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_UNRESOLVED, self.call,
                           "complete_successfully", d_id, "x")
        self.call("resolve_dependency", d_id, bound["dependency_id"],
                  evidence["evidence_id"])
        self.assertRefuses(mp.PROBLEM_RESOURCE_NOT_READY, self.call,
                           "complete_successfully", d_id, "x")
        self.call("observe_resource_readiness", d_id, "build_host", "UNKNOWN",
                  self.clock())
        self.assertRefuses(mp.PROBLEM_RESOURCE_NOT_READY, self.call,
                           "complete_successfully", d_id, "x")
        self.call("observe_resource_readiness", d_id, "build_host", "READY",
                  self.clock())
        # Stale readiness / stale evidence at completion time.
        self.clock.advance(601)
        self.assertRefuses(mp.PROBLEM_RESOURCE_NOT_READY, self.call,
                           "complete_successfully", d_id, "x")
        self.call("observe_resource_readiness", d_id, "build_host", "READY",
                  self.clock())
        self.clock.advance(3600)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        self.assertEqual(self.service.get_state(d_id)["proof"]["requirements"]["tests_pass"],
                         mp.REQUIREMENT_STALE)
        # A DEGRADED blocker (policy-permitted key) does not block.
        degraded = self.call("open_blocker", d_id, "flaky_network", "flaked")
        self.assertEqual(degraded["severity"], ms.BLOCKER_SEVERITY_DEGRADED)
        self.assertEqual(degraded["progress"], ms.PROGRESS_IN_PROGRESS)
        codes = [f["problem"] for f in self.service.get_state(d_id)["closure_eligibility"][
            "failures"]]
        self.assertNotIn(mp.PROBLEM_HARD_BLOCKER_ACTIVE, codes)

    def test_F9_evidence_rules_through_the_service(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        mission_id = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, True, [])
        # Narrative and process exit may be recorded, never accepted.
        for kind in mission_record.NON_SATISFYING_EVIDENCE_KINDS:
            with self.subTest(kind):
                submitted = self.call("submit_evidence", mission_id, "tests_pass", kind,
                                      "a" * 64, [artifact["artifact_id"]])
                self.assertRefuses(ms.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING, self.call,
                                   "accept_evidence", mission_id,
                                   submitted["evidence_id"], "a" * 64)
        # A satisfying kind the requirement does not declare.
        other_kind = self.call("submit_evidence", mission_id, "tests_pass",
                               mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION,
                               "b" * 64, [artifact["artifact_id"]])
        self.assertRefuses(sm.PROBLEM_EVIDENCE_KIND_NOT_DECLARED, self.call,
                           "accept_evidence", mission_id, other_kind["evidence_id"],
                           "b" * 64)
        # Unknown requirement, unknown artifact, unknown evidence.
        self.assertRefuses(sm.PROBLEM_UNKNOWN_REQUIREMENT, self.call, "submit_evidence",
                           mission_id, "nope", "VERIFICATION_RECORD", "c" * 64, [])
        self.assertRefuses(ms.PROBLEM_UNKNOWN_ARTIFACT, self.call, "submit_evidence",
                           mission_id, "tests_pass", "VERIFICATION_RECORD", "c" * 64,
                           [hexid("mf", 0x99)])
        self.assertRefuses(ms.PROBLEM_UNKNOWN_EVIDENCE, self.call, "accept_evidence",
                           mission_id, hexid("mv", 0x99), "c" * 64)
        # Acceptance digest must equal the submitted digest; accept twice
        # refuses; accepting invalidated evidence refuses.
        good = self.call("submit_evidence", mission_id, "tests_pass",
                         "VERIFICATION_RECORD", "d" * 64, [artifact["artifact_id"]])
        self.assertRefuses(ms.PROBLEM_EVIDENCE_DIGEST, self.call, "accept_evidence",
                           mission_id, good["evidence_id"], "e" * 64)
        self.assertEqual(self.service.get_state(mission_id)["proof"]["requirements"][
            "tests_pass"], mp.REQUIREMENT_SUBMITTED_NOT_ACCEPTED)
        self.call("accept_evidence", mission_id, good["evidence_id"], "d" * 64,
                  context=self.other)
        self.assertEqual(self.service.get_state(mission_id)["proof"]["requirements"][
            "tests_pass"], mp.REQUIREMENT_SATISFIED)
        # R-27: submitter and acceptor provenance are recorded separately
        # and independently readable (no separation-of-duties rule here).
        stored = [e for e in self.service.get_state(mission_id)["record"]["evidence"]
                  if e["evidence_id"] == good["evidence_id"]][0]
        self.assertEqual(stored["provenance"]["principal_ref"], "uid:501")
        self.assertEqual(stored["acceptance"]["provenance"]["principal_ref"], "uid:502")
        self.assertNotEqual(stored["provenance"]["reference_id"],
                            stored["acceptance"]["provenance"]["reference_id"])
        same_principal = self.call("submit_evidence", mission_id, "tests_pass",
                                   "VERIFICATION_RECORD", "d" * 64,
                                   [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, same_principal["evidence_id"], "d" * 64)
        # R-29: a required key with a contradicting role refuses; a
        # differing digest under the right role is recordable.
        self.assertRefuses(sm.PROBLEM_ARTIFACT_ROLE_MISMATCH, self.call,
                           "record_artifact", mission_id, "test_log", "PRODUCED",
                           "OPAQUE_REFERENCE", "opaque:3", HEX_A, True, [])
        self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                  "OPAQUE_REFERENCE", "opaque:4", HEX_B, True, [])
        self.call("record_artifact", mission_id, "scratch_pad", "PRODUCED",
                  "OPAQUE_REFERENCE", "opaque:5", None, True, [])
        self.assertRefuses(sm.PROBLEM_EVIDENCE_ALREADY_ACCEPTED, self.call,
                           "accept_evidence", mission_id, good["evidence_id"], "d" * 64)
        # A second accepted record with a different digest contradicts.
        second = self.call("submit_evidence", mission_id, "tests_pass",
                           "VERIFICATION_RECORD", "9" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, second["evidence_id"], "9" * 64)
        self.assertEqual(self.service.get_state(mission_id)["proof"]["requirements"][
            "tests_pass"], mp.REQUIREMENT_CONTRADICTED)
        self.call("invalidate_evidence", mission_id, second["evidence_id"], "wrong run")
        self.assertEqual(self.service.get_state(mission_id)["proof"]["requirements"][
            "tests_pass"], mp.REQUIREMENT_SATISFIED)
        self.assertRefuses(sm.PROBLEM_EVIDENCE_INVALIDATED, self.call,
                           "invalidate_evidence", mission_id, second["evidence_id"], "x")
        self.assertRefuses(sm.PROBLEM_EVIDENCE_INVALIDATED, self.call,
                           "accept_evidence", mission_id, second["evidence_id"], "9" * 64)
        # Resolving a blocker with unaccepted evidence refuses and changes
        # no proof status; resolving with accepted evidence satisfies no
        # dependency.
        blocker = self.call("open_blocker", mission_id, "disk_full", "x")
        self.assertRefuses(ms.PROBLEM_EVIDENCE_NOT_ACCEPTED, self.call,
                           "resolve_blocker", mission_id, blocker["blocker_id"],
                           other_kind["evidence_id"])
        self.assertRefuses(ms.PROBLEM_UNKNOWN_BLOCKER, self.call, "resolve_blocker",
                           mission_id, hexid("mb", 0x99), good["evidence_id"])
        self.call("resolve_blocker", mission_id, blocker["blocker_id"], good["evidence_id"])
        self.assertRefuses(sm.PROBLEM_BLOCKER_ALREADY_RESOLVED, self.call,
                           "resolve_blocker", mission_id, blocker["blocker_id"],
                           good["evidence_id"])
        # Evidence bound to a superseded activation cannot be accepted later.
        pending = self.call("submit_evidence", mission_id, "tests_pass",
                            "VERIFICATION_RECORD", "8" * 64, [artifact["artifact_id"]])
        self.edit(mission_id, 1, objective="v2",
                  proof_contract=contract(required_dependencies=[]))
        self.approve(mission_id, 2)
        self.call("activate_proof_contract", mission_id)
        self.assertRefuses(sm.PROBLEM_CONTRACT_STALE, self.call, "accept_evidence",
                           mission_id, pending["evidence_id"], "8" * 64)
        self.assertEqual(self.service.get_state(mission_id)["proof"]["requirements"][
            "tests_pass"], mp.REQUIREMENT_MISSING)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))

    def test_F10_dependency_binding_rules(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        t_id = self.completed_prerequisite()
        stranger = self.ready_mission(required_dependencies=[])
        d_id = self.dependent_on(t_id)
        self.assertRefuses(sm.PROBLEM_UNKNOWN_DEPENDENCY_SLOT, self.call,
                           "bind_dependency", d_id, "no_such_slot", t_id)
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_TARGET_MISMATCH, self.call,
                           "bind_dependency", d_id, "upstream", stranger)
        self.assertRefuses(sm.PROBLEM_DEPENDENCY_UNKNOWN_MISSION, self.call,
                           "bind_dependency", d_id, "upstream", hexid("mn", 0x4242))
        self.assertRefuses(ms.PROBLEM_DEPENDENCY_SELF, self.call,
                           "declare_dependency", d_id, "MISSION", d_id)
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        # R-28: an identical rebind is an accepted operation with an empty
        # effect: it consumes its id, appends one applied operation and
        # advances sequence; it is not a replay.
        sequence = self.seq(d_id)
        applied_before = len(self.service.get_state(d_id)["record"]["applied_operations"])
        same_id = self.oid()
        same = self.service.bind_dependency(d_id, same_id, sequence, "upstream", t_id,
                                            self.context)
        self.assertEqual(same["dependency_id"], bound["dependency_id"])
        self.assertFalse(same["idempotent"])
        self.assertEqual(same["sequence"], sequence + 1)
        state = self.service.get_state(d_id)
        self.assertEqual(state["sequence"], sequence + 1)
        self.assertEqual(len(state["record"]["applied_operations"]), applied_before + 1)
        self.assertEqual(len(state["record"]["dependencies"]), 1)
        self.assertEqual(self.store.load()["reservations"][same_id]["consumed_by"], same_id)
        replay = self.service.bind_dependency(d_id, same_id, sequence, "upstream", t_id,
                                              self.context)
        self.assertEqual(dict(replay, idempotent=False), same)
        self.assertTrue(replay["idempotent"])
        from mission import state_service as sm2
        self.assertRefuses(sm2.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.bind_dependency, d_id, same_id, sequence,
                           "upstream", stranger, self.context)
        self.assertRefuses(ms.PROBLEM_DEPENDENCY_REBIND, self.call,
                           "bind_dependency", d_id, "upstream", stranger)
        # Cycle: stranger declares D, D declares stranger.
        self.call("declare_dependency", stranger, "MISSION", d_id)
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_CYCLE, self.call,
                           "declare_dependency", d_id, "MISSION", stranger)
        extra = self.call("declare_dependency", d_id, "RESOURCE", "shared-cache")
        self.assertIsNone(self.service.get_state(d_id)["record"]["dependencies"][-1]["key"])
        # Cross-Mission authority never transfers.
        self.assertEqual(self.service.get(d_id)["live_authorization_id"],
                         self.service.get_state(d_id)["contract"]["authorization_id"])
        self.assertNotEqual(self.service.get(d_id)["live_authorization_id"],
                            self.service.get(t_id)["live_authorization_id"])
        # Resolving a required MISSION slot needs accepted evidence AND the
        # prerequisite's completion at the declared revision.
        evidence_id = self.make_local_complete(d_id)
        self.call("resolve_dependency", d_id, extra["dependency_id"], evidence_id)
        self.call("resolve_dependency", d_id, bound["dependency_id"], evidence_id)
        self.assertRefuses(sm.PROBLEM_DEPENDENCY_ALREADY_RESOLVED, self.call,
                           "resolve_dependency", d_id, bound["dependency_id"], evidence_id)
        self.assertEqual(self.service.get_state(d_id)["dependencies"]["slots"]["upstream"],
                         mp.SLOT_RESOLVED)
        # A prerequisite that is not COMPLETED cannot be resolved.
        u_id = self.ready_mission(required_dependencies=[])
        digest = self.store.load()["missions"][u_id]["revisions"][0]["proposal_digest_sha256"]
        w_id = self.ready_mission(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "ELIGIBILITY", "condition": "MISSION_WITH_PROPOSAL_DIGEST",
                       "proposal_digest_sha256": digest}}])
        w_bound = self.call("bind_dependency", w_id, "upstream", u_id)
        w_evidence = self.make_local_complete(w_id)
        self.assertRefuses(mp.PROBLEM_PREREQUISITE_NOT_COMPLETE, self.call,
                           "resolve_dependency", w_id, w_bound["dependency_id"],
                           w_evidence)
        self.make_local_complete(u_id)
        self.call("complete_successfully", u_id, "done")
        self.call("resolve_dependency", w_id, w_bound["dependency_id"], w_evidence)
        # Drift: EDIT of the completed prerequisite breaks W's eligibility.
        self.edit(u_id, 1, objective="u v2", proof_contract=contract())
        self.assertRefuses(mp.PROBLEM_PREREQUISITE_DRIFTED, self.call,
                           "complete_successfully", w_id, "done")
        self.assertEqual([f["problem"] for f in self.service.get_state(w_id)[
            "closure_eligibility"]["failures"]], [mp.PROBLEM_PREREQUISITE_DRIFTED])
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))

    def test_F11_budget_continuation_and_unsuccessful_closure(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        mission_id = self.ready_mission(required_dependencies=[])
        self.assertRefuses(mp.PROBLEM_CLOSURE_NOT_PROVABLE, self.call,
                           "close_unsuccessful", mission_id, "hard_blocker_unresolvable", "x")
        self.assertRefuses(mp.PROBLEM_CLOSURE_NOT_PROVABLE, self.call,
                           "close_unsuccessful", mission_id, "budget_exhausted", "x")
        self.assertRefuses(ms.PROBLEM_CLOSURE, self.call, "close_unsuccessful",
                           mission_id, "proof_complete", "x")
        for attempt in (1, 2, 3):
            outcome = self.call("record_continuation", mission_id, "retry %d" % attempt)
            self.assertEqual(outcome["attempt"], attempt)
        self.assertRefuses(ms.PROBLEM_BUDGET_EXHAUSTED, self.call,
                           "record_continuation", mission_id, "retry 4")
        budget = self.service.get_state(mission_id)["budget"]
        self.assertEqual(budget, {"attempts_consumed": 3, "attempts_remaining": 0,
                                  "checkpoints_consumed": 0, "checkpoints_remaining": 8})
        # Exhausted and incomplete: completion refuses on proof, the
        # checkpoint derives a refusal, unsuccessful closure is the one
        # permitted outcome.
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", mission_id, "x")
        checkpoint = self.call("record_checkpoint", mission_id, [], ["all"], "none", "now")
        self.assertIsNone(checkpoint["next_permitted_step"])
        self.assertEqual(checkpoint["refusal"]["problem"], ms.PROBLEM_BUDGET_EXHAUSTED)
        closed = self.call("close_unsuccessful", mission_id, "budget_exhausted",
                           "out of attempts")
        self.assertEqual(closed["progress"], ms.PROGRESS_CLOSED_UNSUCCESSFUL)
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call,
                           "record_continuation", mission_id, "retry 5")
        # Last permitted attempt followed by complete proof is success.
        last = self.ready_mission(required_dependencies=[])
        for attempt in (1, 2, 3):
            self.call("record_continuation", last, "retry")
        self.make_local_complete(last)
        done = self.call("complete_successfully", last, "proof complete")
        self.assertEqual(done["progress"], ms.PROGRESS_COMPLETED)
        # Continuation from BLOCKED is not a permitted transition.
        blocked = self.ready_mission(required_dependencies=[])
        self.call("open_blocker", blocked, "disk_full", "x")
        self.assertRefuses(ms.PROBLEM_PROGRESS_TRANSITION, self.call,
                           "record_continuation", blocked, "retry")
        # hard_blocker_unresolvable is provable there.
        closed = self.call("close_unsuccessful", blocked, "hard_blocker_unresolvable", "x")
        self.assertEqual(closed["reason"], ms.CLOSURE_REASON_HARD_BLOCKER)
        # Checkpoint budget is bounded too.
        tiny = self.ready_mission(required_dependencies=[], continuation_budget={
            "max_attempts": 0, "max_checkpoints": 1})
        self.call("record_checkpoint", tiny, [], [], "r", "s")
        self.assertRefuses(sm.PROBLEM_CHECKPOINT_BUDGET_EXHAUSTED, self.call,
                           "record_checkpoint", tiny, [], [], "r", "s")
        self.assertRefuses(ms.PROBLEM_BUDGET_EXHAUSTED, self.call,
                           "record_continuation", tiny, "retry")
        # Abandon before any activation, and from BLOCKED.
        never = self.propose(proof_contract=contract())["mission_id"]
        self.approve(never, 1)
        outcome = self.call("abandon", never, "changed my mind")
        self.assertEqual(outcome["progress"], ms.PROGRESS_ABANDONED)
        self.assertIsNone(self.service.get_state(never)["record"]["closure"]["activation_id"])
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))

    def test_F12_checkpoints_and_projection_survive_reload(self):
        ms, mp = self.ms, self.mp
        mission_id = self.ready_mission(required_dependencies=[])
        first = self.call("record_checkpoint", mission_id, [], ["everything"],
                          "retry", "stop")
        self.assertEqual(first["next_permitted_step"],
                         ms.NEXT_STEP_OBSERVE_RESOURCE_READINESS)
        self.make_local_complete(mission_id)
        second = self.call("record_checkpoint", mission_id, ["all"], [], "retry", "stop")
        self.assertEqual(second["next_permitted_step"], ms.NEXT_STEP_CLOSE_COMPLETED)
        stored = json.loads(self.read_bytes())
        # A fresh store + service over the same directory sees the same
        # record, recomputes every checkpoint, and the projection agrees.
        from mission import service as service_module
        from mission import store as store_module
        fresh = service_module.MissionService(store_module.MissionStore(self.directory),
                                              self.clock)
        self.assertEqual(fresh.get_state(mission_id)["record"],
                         stored["mission_state"][mission_id])
        self.assertEqual(fresh.get_state(mission_id)["latest_checkpoint"][
            "checkpoint_id"], second["checkpoint_id"])
        # Time passes: stored checkpoints are untouched, the projection moves.
        self.clock.advance(100_000)
        self.assertEqual(fresh.get_state(mission_id)["record"],
                         stored["mission_state"][mission_id])
        self.assertFalse(fresh.get_state(mission_id)["closure_eligibility"]["eligible"])
        self.assertEqual(fresh.get_state(mission_id)["latest_checkpoint"][
            "next_permitted_step"], ms.NEXT_STEP_CLOSE_COMPLETED)
        # get_state on a Mission with no state record is a plain projection.
        plain = self.propose()["mission_id"]
        projection = self.service.get_state(plain)
        self.assertIsNone(projection["record"])
        self.assertEqual(projection["progress"], ms.PROGRESS_NOT_STARTED)
        self.assertEqual(projection["sequence"], 0)
        self.assertFalse(projection["contract"]["active"])


class FNoWeakeningParameterTests(ServiceStateFixture):

    MUTATORS = (
        "activate_proof_contract", "record_claim", "record_artifact",
        "submit_evidence", "accept_evidence", "invalidate_evidence", "open_blocker",
        "resolve_blocker", "bind_dependency", "declare_dependency",
        "resolve_dependency", "observe_resource_readiness", "record_continuation",
        "record_checkpoint", "complete_successfully", "close_unsuccessful", "abandon",
    )
    FORBIDDEN = ("requirements", "budget", "max_", "attempts", "checkpoints",
                 "age", "stale", "degrad", "severity", "required", "permitted",
                 "expires", "contract", "policy", "kinds", "digest_expected",
                 "authorization")

    def test_F13_no_mutator_carries_a_weakening_parameter(self):
        import inspect
        from mission import service as service_module
        for name in self.MUTATORS:
            method = getattr(service_module.MissionService, name)
            params = list(inspect.signature(method).parameters)
            self.assertEqual(params[:4], ["self", "mission_id", "operation_id",
                                          "expected_sequence"], name)
            self.assertEqual(params[-1], "context", name)
            for parameter in params:
                for word in self.FORBIDDEN:
                    self.assertNotIn(word, parameter, (name, parameter))
        # No other public method mutates state: the mutator set is exactly
        # the set of methods that consume a state operation id.
        import ast
        source = (REPO_ROOT / "mission" / "state_service.py").read_text()
        tree = ast.parse(source)
        public = sorted(
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
            and "operation_id" in [a.arg for a in node.args.args]
        )
        self.assertEqual(public, sorted(self.MUTATORS))
        # No word for the excluded surfaces appears in the service source.
        lowered = source.lower()
        for word in ("dispatch", "route", "schedule", "spawn", "subprocess",
                     "fetch", "publish", "deploy", "merge", "release"):
            self.assertNotIn(word, lowered, word)
        # The one production caller of the Task 4 constructor is unchanged.
        service_source = (REPO_ROOT / "mission" / "service.py").read_text()
        self.assertEqual(service_source.count("issue_mission_authorization("), 1)
        self.assertNotIn("issue_mission_authorization", source)
        self.assertNotIn("revoke(", source)
        self.assertNotIn("mission[\"state\"] =", source)


# ====================================================================
# G. Stage 2 gate: R-26 and R-28 proven through the real service
# ====================================================================


class GStageTwoGateTests(ServiceStateFixture):

    def same_second_edit(self, mission_id, revision):
        """A REAL EDIT decision applied without advancing the clock, so
        Task 4 writes revoked_at equal to the current second."""
        decision_id = self.service.mint_decision_id(self.context)
        return self.service.edit(mission_id, revision, proposal(proof_contract=contract()),
                                 decision_id, self.context)

    def assert_stable(self, expected):
        self.assertEqual(json.loads(self.read_bytes()), expected)
        self.assertEqual(self.store.load(), expected)
        self.store.save(self.store.load())
        self.assertEqual(json.loads(self.read_bytes()), expected)

    def without_reservations(self, document):
        """Refused calls still mint (unconsumed) operation ids, so compare
        everything but the reservations map, and check those ids are
        unconsumed."""
        for key, reservation in document["reservations"].items():
            if reservation["kind"] == "state_operation":
                self.assertIn(reservation["consumed_by"], (None, key))
        return dict((k, v) for k, v in document.items() if k != "reservations")

    def test_G1_same_second_activation_and_edit_stays_readable_and_refuses_live(self):
        from mission import state_service as sm
        mission_id, authorization_id = self.approved_mission()
        self.clock.advance(1)
        activated = self.call("activate_proof_contract", mission_id)
        second = self.clock()
        self.same_second_edit(mission_id, 1)
        document = json.loads(self.read_bytes())
        revocation = document["authorizations"][authorization_id]["revocation"]
        self.assertTrue(revocation["revoked"])
        self.assertEqual(revocation["revoked_at"], second)
        self.assertEqual(document["mission_state"][mission_id]["contract_activations"][0][
            "activated_at"], second)
        # Saves and reloads unchanged now and far past every staleness bound.
        self.assert_stable(document)
        self.clock.advance(10 * 365 * 24 * 3600)
        self.assert_stable(document)
        # Post-revocation refusal holds everywhere.
        check = self.service.validate_authorization(authorization_id, mission_id, 1)
        self.assertEqual(check.problem, self.ma.PROBLEM_REVOKED)
        for name, args in (
            ("record_claim", ("tests_pass", "done")),
            ("record_artifact", ("test_log", "VERIFICATION", "OPAQUE_REFERENCE",
                                 "opaque:1", HEX_A, True, [])),
            ("submit_evidence", ("tests_pass", "VERIFICATION_RECORD", "e" * 64, [])),
            ("accept_evidence", (hexid("mv", 1), "e" * 64)),
            ("invalidate_evidence", (hexid("mv", 1), "x")),
            ("open_blocker", ("disk_full", "no space")),
            ("resolve_blocker", (hexid("mb", 1), hexid("mv", 1))),
            ("bind_dependency", ("upstream", MISSION_X)),
            ("declare_dependency", ("RESOURCE", "x")),
            ("resolve_dependency", (hexid("mx", 1), hexid("mv", 1))),
            ("observe_resource_readiness", ("build_host", "READY", self.clock())),
            ("record_continuation", ("retry",)),
            ("record_checkpoint", ([], [], "retry", "stop")),
            ("complete_successfully", ("done",)),
            ("close_unsuccessful", ("budget_exhausted", "x")),
        ):
            with self.subTest(name):
                self.assertRefuses(sm.PROBLEM_CONTRACT_STALE, self.call, name,
                                   mission_id, *args)
        projection = self.service.get_state(mission_id)
        self.assertFalse(projection["contract"]["current"])
        self.assertFalse(projection["contract"]["authority_live"])
        self.assertEqual(projection["contract"]["problem"], sm.PROBLEM_CONTRACT_STALE)
        self.assertEqual(projection["contract"]["activation_id"], activated["activation_id"])
        self.assertEqual(self.without_reservations(json.loads(self.read_bytes())),
                         self.without_reservations(document))
        # The central current check in authorization.py has no time
        # comparison for revocation: it refuses a revoked authorization
        # outright, before and after the clock moves.
        self.assertEqual(self.ma.validate_authorization_use(
            document, authorization_id, mission_id, 1, second).problem,
            self.ma.PROBLEM_REVOKED)

    def test_G2_same_second_completion_and_edit_stays_readable_drift_is_live(self):
        from mission import state_service as sm
        ms, mp = self.ms, self.mp
        t_id = self.ready_mission(required_dependencies=[])
        t_auth = self.service.get_state(t_id)["contract"]["authorization_id"]
        self.make_local_complete(t_id)
        d_id = self.dependent_on(t_id)
        self.clock.advance(1)
        done = self.call("complete_successfully", t_id, "proof complete")
        second = self.clock()
        self.assertEqual(done["progress"], ms.PROGRESS_COMPLETED)
        self.make_local_complete(d_id, bind=t_id)
        eligible_before = self.service.get_state(d_id)["closure_eligibility"]
        self.assertTrue(eligible_before["eligible"])
        # Rewind the shared clock to T's completion second and apply the
        # REAL EDIT there: revoked_at == closed_at.
        self.clock.now = second
        self.same_second_edit(t_id, 1)
        document = json.loads(self.read_bytes())
        self.assertEqual(document["authorizations"][t_auth]["revocation"]["revoked_at"],
                         second)
        self.assertEqual(document["mission_state"][t_id]["closure"]["closed_at"], second)
        self.assert_stable(document)
        self.clock.advance(10 * 365 * 24 * 3600)
        self.assert_stable(document)
        # Live: T's authorization is revoked, T is terminal, D's projection
        # reports the drift and D cannot complete.
        self.assertEqual(self.service.validate_authorization(t_auth, t_id, 1).problem,
                         self.ma.PROBLEM_REVOKED)
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call, "record_claim",
                           t_id, "tests_pass", "again")
        live = self.service.get_state(d_id)["closure_eligibility"]
        self.assertFalse(live["eligible"])
        self.assertIn(mp.PROBLEM_PREREQUISITE_DRIFTED, [f["problem"] for f in live["failures"]])
        self.clock.now = second + 1
        self.assertEqual([f["problem"] for f in self.service.get_state(d_id)[
            "closure_eligibility"]["failures"]], [mp.PROBLEM_PREREQUISITE_DRIFTED])
        self.assertRefuses(mp.PROBLEM_PREREQUISITE_DRIFTED, self.call,
                           "complete_successfully", d_id, "done")
        self.assertEqual(self.without_reservations(json.loads(self.read_bytes())),
                         self.without_reservations(document))
        self.assertIsNone(self.ma.reconcile_registry(self.store.load()))

    def test_G3_identical_rebind_binds_and_consumes_its_operation_id(self):
        from mission import state_service as sm
        t_id = self.completed_prerequisite()
        stranger = self.ready_mission(required_dependencies=[])
        d_id = self.dependent_on(t_id)
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        self.call("record_continuation", d_id, "one attempt")
        before_state = json.loads(json.dumps(self.service.get_state(d_id)))
        before_record = before_state["record"]
        sequence = before_record["sequence"]
        # The no-op call with a fresh id X.
        x = self.oid()
        outcome = self.service.bind_dependency(d_id, x, sequence, "upstream", t_id,
                                               self.context)
        self.assertFalse(outcome["idempotent"])
        self.assertEqual(outcome["dependency_id"], bound["dependency_id"])
        self.assertEqual(outcome["sequence"], sequence + 1)
        after_state = self.service.get_state(d_id)
        after_record = after_state["record"]
        # Bound and consumed: consumed_by X, exactly one ledger entry for X
        # carrying its content digest.
        document = self.store.load()
        self.assertEqual(document["reservations"][x]["consumed_by"], x)
        entries = [e for e in after_record["applied_operations"] if e["operation_id"] == x]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["content_digest_sha256"]), 64)
        self.assertEqual(entries[0]["kind"], self.ms.OPERATION_BIND_DEPENDENCY)
        # Diff across the no-op: only the ledger, the sequence and the
        # updated_at bookkeeping moved.
        changed = sorted(k for k in after_record if after_record[k] != before_record[k])
        self.assertTrue({"applied_operations", "sequence"} <= set(changed))
        self.assertTrue(set(changed) <= {"applied_operations", "sequence", "updated_at"},
                        changed)
        self.assertEqual(len(after_record["applied_operations"]),
                         len(before_record["applied_operations"]) + 1)
        self.assertEqual(after_record["sequence"], sequence + 1)
        for key in ("proof", "readiness", "dependencies", "budget", "closure_eligibility",
                    "progress", "contract"):
            self.assertEqual(after_state[key], before_state[key], key)
        self.assertEqual(after_state["budget"]["attempts_consumed"], 1)
        bytes_bound = self.read_bytes()
        # THE ATTACK: X presented again with different content, as if a
        # first use. Refuses atomically, nothing written.
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.bind_dependency, d_id, x, sequence + 1,
                           "upstream", stranger, self.context)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_continuation, d_id, x, sequence + 1,
                           "sneak an attempt", self.context)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           self.service.record_continuation, stranger, x, 1,
                           "sneak an attempt", self.context)
        self.assertEqual(self.read_bytes(), bytes_bound)
        # Exact replay returns the bound outcome and mutates nothing.
        replay = self.service.bind_dependency(d_id, x, sequence, "upstream", t_id,
                                              self.context)
        self.assertEqual(dict(replay, idempotent=False), outcome)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(self.read_bytes(), bytes_bound)
        self.assertEqual(self.service.get_state(d_id)["budget"]["attempts_consumed"], 1)
        # A genuine rebind to a different reference still refuses.
        self.assertRefuses(self.ms.PROBLEM_DEPENDENCY_REBIND, self.call,
                           "bind_dependency", d_id, "upstream", stranger)
        # Registry-wide identity still reconciles and the store reloads.
        document = self.store.load()
        self.assertIsNone(self.ma.reconcile_registry(document))
        self.assertEqual(document, json.loads(self.read_bytes()))
        # No accepted invocation leaves its id unconsumed: every applied
        # operation is consumed and every consumed state operation is applied.
        applied = set()
        for state in document["mission_state"].values():
            for entry in state["applied_operations"]:
                applied.add(entry["operation_id"])
                self.assertEqual(document["reservations"][entry["operation_id"]][
                    "consumed_by"], entry["operation_id"])
        consumed = set(k for k, v in document["reservations"].items()
                       if v["kind"] == "state_operation" and v["consumed_by"] is not None)
        self.assertEqual(applied, consumed)


# ====================================================================
# H. The hermetic acceptance fixture (criterion H)
# ====================================================================


class HermeticAcceptanceFixtureTests(ServiceStateFixture):
    """ONE named fixture driving the REAL MissionStore + MissionService in
    a temporary protected directory through the full lifecycle the user
    contract enumerates, in order: create + approve; authorized
    proof-contract establishment; original / produced / verification
    artifact records; blocker and dependency refusal; a partial checkpoint
    with budget; reload-from-disk equivalence; idempotent replay; valid
    prerequisite resolution; each proof failure mode; proof-complete
    successful closure; exhausted budget; abandonment. No Mission or
    Capability is launched and no subprocess is spawned: process creation
    is trapped for the duration and no orchestration or provider module is
    loaded by the run."""

    PROVIDER_ROOTS = ("telegram_operator", "grok_mcp", "operator_session",
                      "codex_gateway", "target_runtime", "capability", "worker",
                      "durable_execution", "herdr")

    def setUp(self):
        super(HermeticAcceptanceFixtureTests, self).setUp()
        import subprocess
        import unittest.mock as mock
        self.spawned = []

        def trap(*args, **kwargs):
            self.spawned.append(args)
            raise AssertionError("the fixture must not spawn a process")
        self.patches = [mock.patch.object(subprocess, "Popen", side_effect=trap),
                        mock.patch("os.system", side_effect=trap),
                        mock.patch("os.fork", side_effect=trap, create=True)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def authority_of(self, mission_id):
        document = json.loads(self.read_bytes())
        return json.dumps({
            "a": dict((k, v) for k, v in document["authorizations"].items()
                      if v["mission_id"] == mission_id),
            "l": [e for e in document["authority_ledger"] if e["mission_id"] == mission_id],
        }, sort_keys=True)

    def fresh_view(self):
        from mission import service as service_module
        from mission import store as store_module
        return service_module.MissionService(
            store_module.MissionStore(self.directory), self.clock)

    def test_H_full_acceptance_lifecycle(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        # 1. create + approve (prerequisite T, dependent D, control E and A).
        t_created = self.propose(proof_contract=contract(required_dependencies=[]))
        t_id = t_created["mission_id"]
        self.assertEqual(t_created["state"], "AWAITING_DECISION")
        self.assertRefuses(sm.PROBLEM_STATE_NOT_AUTHORIZED, self.call,
                           "activate_proof_contract", t_id)
        self.approve(t_id, 1)
        self.assertEqual(self.service.get(t_id)["record"]["state"], "AUTHORIZED")
        t_digest = t_created["proposal_digest_sha256"]
        d_created = self.propose(proof_contract=contract(required_dependencies=[{
            "key": "upstream", "kind": "MISSION",
            "target": {"form": "EXACT_MISSION", "mission_id": t_id, "revision": 1,
                       "proposal_digest_sha256": t_digest}}]))
        d_id = d_created["mission_id"]
        self.approve(d_id, 1)
        # 2. authorized proof-contract establishment.
        self.clock.advance(1)
        activated = self.call("activate_proof_contract", d_id)
        self.call("activate_proof_contract", t_id)
        self.assertEqual(activated["revision"], 1)
        self.assertRefuses(sm.PROBLEM_CONTRACT_ALREADY_ACTIVE, self.call,
                           "activate_proof_contract", d_id)
        contract_view = self.service.get_state(d_id)["contract"]
        self.assertTrue(contract_view["current"] and contract_view["authority_live"])
        self.assertEqual(contract_view["content"]["continuation_budget"]["max_attempts"], 3)
        authority_before = self.authority_of(d_id)
        # 3. original / produced / verification artifact records.
        original = self.call("record_artifact", d_id, None, "ORIGINAL_INPUT",
                             ms.LOCATOR_KIND_REPOSITORY_PATH, "src/probe.py", "1" * 64,
                             True, [])
        produced = self.call("record_artifact", d_id, None, "PRODUCED",
                             ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                             "receipt:candidate-1", "2" * 64, True,
                             [original["artifact_id"]])
        unavailable_log = self.call("record_artifact", d_id, "test_log", "VERIFICATION",
                                    ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log-0",
                                    HEX_A, False, [produced["artifact_id"]])
        stored = self.service.get_state(d_id)["record"]["artifacts"]
        self.assertEqual([a["role"] for a in stored],
                         ["ORIGINAL_INPUT", "PRODUCED", "VERIFICATION"])
        self.assertEqual(stored[1]["derived_from"], [original["artifact_id"]])
        self.assertEqual(stored[2]["derived_from"], [produced["artifact_id"]])
        self.assertRefuses(sm.PROBLEM_ARTIFACT_ROLE_MISMATCH, self.call,
                           "record_artifact", d_id, "test_log", "PRODUCED",
                           ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", HEX_A, True, [])
        # 4. blocker and dependency refusal.
        blocker = self.call("open_blocker", d_id, "disk_full", "no space left")
        self.assertEqual(blocker["severity"], "HARD")
        self.assertEqual(self.service.get_state(d_id)["progress"], ms.PROGRESS_BLOCKED)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        c_id = self.ready_mission(required_dependencies=[])
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_TARGET_MISMATCH, self.call,
                           "bind_dependency", d_id, "upstream", c_id)
        self.assertRefuses(ms.PROBLEM_DEPENDENCY_SELF, self.call,
                           "declare_dependency", d_id, "MISSION", d_id)
        self.call("declare_dependency", c_id, "MISSION", d_id)
        self.assertRefuses(mp.PROBLEM_DEPENDENCY_CYCLE, self.call,
                           "declare_dependency", d_id, "MISSION", c_id)
        # 5. partial checkpoint with budget.
        self.assertRefuses(ms.PROBLEM_PROGRESS_TRANSITION, self.call,
                           "record_continuation", d_id, "retry while blocked")
        checkpoint = self.call("record_checkpoint", d_id, ["artifacts recorded"],
                               ["resolve blocker", "prove tests", "resolve upstream"],
                               "retry after the blocker is resolved",
                               "stop when the budget is exhausted")
        self.assertEqual(checkpoint["next_permitted_step"], ms.NEXT_STEP_RESOLVE_BLOCKERS)
        self.assertEqual(checkpoint["budget"], {
            "attempts_consumed": 0, "attempts_remaining": 3,
            "checkpoints_consumed": 1, "checkpoints_remaining": 7})
        self.assertEqual(checkpoint["active_blocker_ids"], [blocker["blocker_id"]])
        # 6. reload-from-disk equivalence through a fresh store + service.
        on_disk = json.loads(self.read_bytes())
        fresh = self.fresh_view()
        self.assertEqual(fresh.get_state(d_id)["record"], on_disk["mission_state"][d_id])
        self.assertEqual(fresh.get_state(d_id)["latest_checkpoint"]["next_permitted_step"],
                         ms.NEXT_STEP_RESOLVE_BLOCKERS)
        self.assertEqual(self.store.load(), on_disk)
        # 7. idempotent replay (a claim, then the same id again).
        claim_id = self.oid()
        sequence = self.seq(d_id)
        first = self.service.record_claim(d_id, claim_id, sequence, "tests_pass",
                                          "I am confident", self.context)
        bytes_after = self.read_bytes()
        again = fresh.record_claim(d_id, claim_id, sequence, "tests_pass",
                                   "I am confident", self.context)
        self.assertEqual(dict(again, idempotent=False), first)
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.read_bytes(), bytes_after)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT, fresh.record_claim,
                           d_id, claim_id, sequence, "tests_pass", "different",
                           self.context)
        self.assertEqual(self.service.get_state(d_id)["proof"]["requirements"]["tests_pass"],
                         mp.REQUIREMENT_MISSING)
        # 8. valid prerequisite resolution: T completes, D binds and resolves.
        self.make_local_complete(t_id)
        self.call("complete_successfully", t_id, "T proof complete")
        good_log = self.call("record_artifact", d_id, "test_log", "VERIFICATION",
                             ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log-1", HEX_A,
                             True, [produced["artifact_id"]])
        evidence = self.call("submit_evidence", d_id, "tests_pass", "VERIFICATION_RECORD",
                             "e" * 64, [good_log["artifact_id"]])
        self.assertEqual(self.service.get_state(d_id)["proof"]["requirements"]["tests_pass"],
                         mp.REQUIREMENT_SUBMITTED_NOT_ACCEPTED)
        self.clock.advance(1)
        self.call("accept_evidence", d_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        self.assertRefuses(ms.PROBLEM_DEPENDENCY_REBIND, self.call, "bind_dependency",
                           d_id, "upstream", self.ready_mission(required_dependencies=[]))
        self.call("resolve_dependency", d_id, bound["dependency_id"],
                  evidence["evidence_id"])
        self.assertEqual(self.service.get_state(d_id)["dependencies"]["slots"]["upstream"],
                         mp.SLOT_RESOLVED)
        # 9. each proof failure mode, observed through the service.
        def status():
            return self.service.get_state(d_id)["proof"]["requirements"]["tests_pass"]
        self.assertEqual(status(), mp.REQUIREMENT_SATISFIED)
        contradicting = self.call("submit_evidence", d_id, "tests_pass",
                                  "VERIFICATION_RECORD", "f" * 64,
                                  [good_log["artifact_id"]])
        self.call("accept_evidence", d_id, contradicting["evidence_id"], "f" * 64)
        self.assertEqual(status(), mp.REQUIREMENT_CONTRADICTED)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        self.call("invalidate_evidence", d_id, contradicting["evidence_id"], "wrong run")
        self.assertEqual(status(), mp.REQUIREMENT_SATISFIED)
        mismatched = self.call("submit_evidence", d_id, "tests_pass",
                               "VERIFICATION_RECORD", "e" * 64,
                               [unavailable_log["artifact_id"]])
        self.call("accept_evidence", d_id, mismatched["evidence_id"], "e" * 64)
        # Same digest, so not contradicted; but one accepted record
        # references an unavailable required artifact: MISMATCHED.
        self.assertEqual(status(), mp.REQUIREMENT_MISMATCHED)
        self.call("invalidate_evidence", d_id, mismatched["evidence_id"], "bad artifact")
        self.assertEqual(status(), mp.REQUIREMENT_SATISFIED)
        narrative = self.call("submit_evidence", d_id, "tests_pass", "NARRATIVE_CLAIM",
                              "9" * 64, [])
        self.assertRefuses(ms.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING, self.call,
                           "accept_evidence", d_id, narrative["evidence_id"], "9" * 64)
        process_exit = self.call("submit_evidence", d_id, "tests_pass", "PROCESS_EXIT",
                                 "8" * 64, [])
        self.assertRefuses(ms.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING, self.call,
                           "accept_evidence", d_id, process_exit["evidence_id"], "8" * 64)
        self.assertEqual(status(), mp.REQUIREMENT_SATISFIED)
        self.clock.advance(3601)
        self.assertEqual(status(), mp.REQUIREMENT_STALE)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        # Pending (unaccepted) records outrank invalidated ones; retire the
        # narrative and process-exit submissions, then the stale acceptance.
        for pending in (narrative, process_exit):
            self.call("invalidate_evidence", d_id, pending["evidence_id"], "never proof")
        self.call("invalidate_evidence", d_id, evidence["evidence_id"], "stale")
        self.assertEqual(status(), mp.REQUIREMENT_INVALIDATED)
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", d_id, "x")
        renewed = self.call("submit_evidence", d_id, "tests_pass", "VERIFICATION_RECORD",
                            "e" * 64, [good_log["artifact_id"]])
        self.call("accept_evidence", d_id, renewed["evidence_id"], "e" * 64,
                  context=self.other)
        self.assertEqual(status(), mp.REQUIREMENT_SATISFIED)
        # Still blocked, and readiness not yet observed: both distinct.
        self.assertRefuses(mp.PROBLEM_HARD_BLOCKER_ACTIVE, self.call,
                           "complete_successfully", d_id, "x")
        resolved = self.call("resolve_blocker", d_id, blocker["blocker_id"],
                             renewed["evidence_id"])
        self.assertEqual(resolved["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertRefuses(mp.PROBLEM_RESOURCE_NOT_READY, self.call,
                           "complete_successfully", d_id, "x")
        self.call("observe_resource_readiness", d_id, "build_host", "READY", self.clock())
        degraded = self.call("open_blocker", d_id, "flaky_network", "one flake")
        self.assertEqual(degraded["severity"], "DEGRADED")
        # 10. proof-complete successful closure.
        self.assertEqual(self.authority_of(d_id), authority_before)
        final_checkpoint = self.call("record_checkpoint", d_id, ["everything"], [],
                                     "none", "none")
        self.assertEqual(final_checkpoint["next_permitted_step"],
                         ms.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE)
        self.assertTrue(self.service.get_state(d_id)["closure_eligibility"]["eligible"])
        done = self.call("complete_successfully", d_id, "all proof accepted")
        self.assertEqual(done["progress"], ms.PROGRESS_COMPLETED)
        self.assertEqual(self.service.get(d_id)["record"]["state"], "AUTHORIZED")
        self.assertEqual(self.authority_of(d_id), authority_before)
        on_disk = json.loads(self.read_bytes())
        self.assertEqual(self.fresh_view().get_state(d_id)["record"],
                         on_disk["mission_state"][d_id])
        self.assertEqual(self.store.load(), on_disk)
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call, "record_claim",
                           d_id, "tests_pass", "again")
        # 11. exhausted budget.
        e_id = self.ready_mission(required_dependencies=[])
        for attempt in (1, 2, 3):
            self.assertEqual(self.call("record_continuation", e_id, "retry")["attempt"],
                             attempt)
        self.assertRefuses(ms.PROBLEM_BUDGET_EXHAUSTED, self.call,
                           "record_continuation", e_id, "retry 4")
        self.assertRefuses(mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", e_id, "x")
        exhausted = self.call("record_checkpoint", e_id, [], ["all"], "none", "now")
        self.assertEqual(exhausted["refusal"]["problem"], ms.PROBLEM_BUDGET_EXHAUSTED)
        closed = self.call("close_unsuccessful", e_id, "budget_exhausted", "no attempts")
        self.assertEqual(closed["progress"], ms.PROGRESS_CLOSED_UNSUCCESSFUL)
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call,
                           "record_continuation", e_id, "retry 5")
        # 12. abandonment (before any activation, and distinct from the above).
        a_id = self.propose(proof_contract=contract())["mission_id"]
        self.approve(a_id, 1)
        abandoned = self.call("abandon", a_id, "no longer wanted")
        self.assertEqual(abandoned["progress"], ms.PROGRESS_ABANDONED)
        self.assertEqual(abandoned["reason"], ms.CLOSURE_REASON_CALLER_ABANDONED)
        self.assertIsNone(self.service.get_state(a_id)["record"]["closure"]["activation_id"])
        outcomes = {m: self.service.get_state(m)["progress"] for m in (d_id, e_id, a_id)}
        self.assertEqual(outcomes, {d_id: "COMPLETED", e_id: "CLOSED_UNSUCCESSFUL",
                                    a_id: "ABANDONED"})
        # The whole document reconciles and reloads; nothing was launched.
        document = self.store.load()
        self.assertEqual(document, json.loads(self.read_bytes()))
        self.assertIsNone(self.ma.reconcile_registry(document))
        self.assertEqual(self.spawned, [])
        loaded = sorted(name for name in sys.modules
                        if name.split(".")[0] in self.PROVIDER_ROOTS)
        self.assertEqual(loaded, [])
        self.assertEqual(sorted(n for n in os.listdir(self.directory)
                                if n != "missions.lock"), ["missions.json"])


# ====================================================================
# R. Interrupted atomic persistence and duplicate / recovery (R-30)
# ====================================================================


class RInterruptedPersistenceTests(ServiceStateFixture):
    """Fault injected INSIDE the real atomic path: ``os.replace`` raises,
    so ``workflow_authority.atomic.atomic_write_json`` has genuinely
    written and fsynced its temp file and the replacement genuinely does
    not happen. Nothing is stubbed on the store or the service; every
    assertion is against the real file and a real ``MissionStore.load``."""

    def setUp(self):
        super(RInterruptedPersistenceTests, self).setUp()
        import unittest.mock as mock
        from workflow_authority import atomic as atomic_module
        self.atomic_module = atomic_module
        self.mock = mock
        self.mission_id = self.ready_mission(required_dependencies=[])
        ms = self.ms
        artifact = self.call("record_artifact", self.mission_id, "test_log",
                             "VERIFICATION", ms.LOCATOR_KIND_OPAQUE_REFERENCE,
                             "opaque:log", HEX_A, True, [])
        self.evidence_id = self.call("submit_evidence", self.mission_id, "tests_pass",
                                     "VERIFICATION_RECORD", "e" * 64,
                                     [artifact["artifact_id"]])["evidence_id"]
        self.call("observe_resource_readiness", self.mission_id, "build_host", "READY",
                  self.clock())

    def armed(self):
        """The fault: the real ``os.replace`` seen by the atomic primitive
        raises after the temp file was written."""
        return self.mock.patch.object(self.atomic_module.os, "replace",
                                      side_effect=OSError("simulated crash before"
                                                          " the atomic replacement"))

    def listing(self):
        return sorted(os.listdir(self.directory))

    def snapshot(self):
        return {
            "bytes": self.read_bytes(),
            "document": self.store.load(),
            "listing": self.listing(),
            "projection": self.service.get_state(self.mission_id),
        }

    def assert_untouched(self, before, operation_id):
        self.assertEqual(self.read_bytes(), before["bytes"])
        fresh = self.fresh_store()
        self.assertEqual(fresh.load(), before["document"])
        self.assertIsNone(self.ma.reconcile_registry(fresh.load()))
        reservation = fresh.load()["reservations"][operation_id]
        self.assertEqual(reservation["kind"], "state_operation")
        self.assertIsNone(reservation["consumed_by"])
        for state in fresh.load()["mission_state"].values():
            self.assertFalse(any(e["operation_id"] == operation_id
                                 for e in state["applied_operations"]))
        self.assertEqual(self.service.get_state(self.mission_id), before["projection"])
        self.assertEqual(self.listing(), before["listing"])
        self.assertFalse(any(name.endswith(".tmp") for name in self.listing()))

    def fresh_store(self):
        from mission import store as store_module
        return store_module.MissionStore(self.directory)

    def fresh_service(self):
        from mission import service as service_module
        return service_module.MissionService(self.fresh_store(), self.clock)

    def test_R1_failure_before_replacement_leaves_everything_intact(self):
        ms, mp = self.ms, self.mp
        shapes = (
            ("record_continuation", ("first attempt",),
             lambda p: p["budget"]["attempts_consumed"]),
            ("accept_evidence", (self.evidence_id, "e" * 64),
             lambda p: p["proof"]["requirements"]["tests_pass"]),
            ("complete_successfully", ("all proof accepted",),
             lambda p: p["progress"]),
        )
        expected_after = {
            "record_continuation": 1,
            "accept_evidence": mp.REQUIREMENT_SATISFIED,
            "complete_successfully": ms.PROGRESS_COMPLETED,
        }
        for name, args, observe in shapes:
            with self.subTest(name):
                operation_id = self.oid()
                sequence = self.seq(self.mission_id)
                before = self.snapshot()
                observed_before = observe(before["projection"])
                self.assertNotEqual(observed_before, expected_after[name])
                method = getattr(self.service, name)
                with self.armed() as replace:
                    with self.assertRaises(OSError):
                        method(self.mission_id, operation_id, sequence, *args,
                               context=self.context)
                # The primitive really reached the replacement step.
                self.assertEqual(replace.call_count, 1)
                temp_written = replace.call_args[0][0]
                self.assertTrue(os.path.basename(temp_written).startswith(".missions-"))
                self.assertFalse(os.path.exists(temp_written))
                self.assert_untouched(before, operation_id)
                self.assertEqual(self.seq(self.mission_id), sequence)
                self.assertEqual(observe(self.service.get_state(self.mission_id)),
                                 observed_before)
                # Disarmed: the SAME id with the SAME content succeeds once.
                outcome = method(self.mission_id, operation_id, sequence, *args,
                                 context=self.context)
                self.assertFalse(outcome["idempotent"])
                self.assertEqual(outcome["sequence"], sequence + 1)
                document = self.fresh_store().load()
                self.assertEqual(document["reservations"][operation_id]["consumed_by"],
                                 operation_id)
                applied = [e for e in document["mission_state"][self.mission_id][
                    "applied_operations"] if e["operation_id"] == operation_id]
                self.assertEqual(len(applied), 1)
                self.assertEqual(self.seq(self.mission_id), sequence + 1)
                self.assertEqual(observe(self.service.get_state(self.mission_id)),
                                 expected_after[name])
                self.assertIsNone(self.ma.reconcile_registry(document))
                self.assertFalse(any(n.endswith(".tmp") for n in self.listing()))
        # Effects applied exactly once, never twice.
        record = self.service.get_state(self.mission_id)["record"]
        self.assertEqual(len(record["continuations"]), 1)
        self.assertEqual(len([e for e in record["evidence"]
                              if e["acceptance"] is not None]), 1)
        self.assertEqual(record["closure"]["progress"], ms.PROGRESS_COMPLETED)
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))

    def test_R2_committed_write_with_lost_response_recovers_once(self):
        from mission import state_service as sm
        # A genuine continuation whose response is lost in flight.
        operation_id = self.oid()
        sequence = self.seq(self.mission_id)
        self.service.record_continuation(self.mission_id, operation_id, sequence,
                                         "attempt one", self.context)
        # (outcome discarded)  -- restart: fresh store and fresh service.
        service = self.fresh_service()
        document = self.fresh_store().load()
        self.assertEqual(document["reservations"][operation_id]["consumed_by"],
                         operation_id)
        entries = [e for e in document["mission_state"][self.mission_id][
            "applied_operations"] if e["operation_id"] == operation_id]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["content_digest_sha256"]), 64)
        self.assertEqual(service.get_state(self.mission_id)["budget"]["attempts_consumed"], 1)
        bytes_committed = self.read_bytes()
        # Exact replay after restart: bound outcome, no second attempt.
        replay = service.record_continuation(self.mission_id, operation_id, sequence,
                                             "attempt one", self.context)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["operation_id"], operation_id)
        self.assertEqual(replay["attempt"], 1)
        self.assertEqual(replay["sequence"], sequence + 1)
        self.assertEqual(self.read_bytes(), bytes_committed)
        after = service.get_state(self.mission_id)
        self.assertEqual(after["budget"]["attempts_consumed"], 1)
        self.assertEqual(after["sequence"], sequence + 1)
        self.assertEqual(len(after["record"]["continuations"]), 1)
        # Replayed again, still once.
        service.record_continuation(self.mission_id, operation_id, sequence,
                                    "attempt one", self.context)
        self.assertEqual(service.get_state(self.mission_id)["budget"]["attempts_consumed"], 1)
        self.assertEqual(self.read_bytes(), bytes_committed)
        # Conflicting replay: same id, different content.
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           service.record_continuation, self.mission_id, operation_id,
                           sequence, "attempt two", self.context)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT,
                           service.accept_evidence, self.mission_id, operation_id,
                           sequence + 1, self.evidence_id, "e" * 64, self.context)
        self.assertEqual(self.read_bytes(), bytes_committed)
        self.assertIsNone(self.ma.reconcile_registry(self.fresh_store().load()))
        # The same recovery for a proof-changing operation: one acceptance.
        accept_id = self.oid()
        sequence = self.seq(self.mission_id)
        service.accept_evidence(self.mission_id, accept_id, sequence, self.evidence_id,
                                "e" * 64, self.context)
        restarted = self.fresh_service()
        again = restarted.accept_evidence(self.mission_id, accept_id, sequence,
                                          self.evidence_id, "e" * 64, self.context)
        self.assertTrue(again["idempotent"])
        record = restarted.get_state(self.mission_id)["record"]
        self.assertEqual(len([e for e in record["evidence"] if e["acceptance"]]), 1)
        self.assertEqual(restarted.get_state(self.mission_id)["proof"]["requirements"][
            "tests_pass"], self.mp.REQUIREMENT_SATISFIED)
        self.assertIsNone(self.ma.reconcile_registry(self.fresh_store().load()))
        self.assertEqual(self.store.load(), json.loads(self.read_bytes()))


# ====================================================================
# S. Pre-freeze correction round 1 (PF-1..PF-4; R-31, R-32, R-33)
# ====================================================================


class SPreFreezeCorrectionTests(ServiceStateFixture):
    """Every tamper is written RAW (no fixture seal) and must refuse on
    load AND on save with file bytes unchanged; every legitimate
    counterpart loads and re-saves unchanged."""

    def test_S1_deleted_or_multiplied_effects_refuse_and_budget_cannot_be_restored(self):
        ms = self.ms
        code = ms.PROBLEM_EFFECT_INCONSISTENT
        mission_id = self.ready_mission(required_dependencies=[], continuation_budget={
            "max_attempts": 1, "max_checkpoints": 2})
        self.call("record_continuation", mission_id, "the only attempt")
        self.call("record_checkpoint", mission_id, [], ["all"], "none", "now")
        artifact = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, True, [])
        e1 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "1" * 64, [artifact["artifact_id"]])
        e2 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "2" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, e1["evidence_id"], "1" * 64)
        good = self.stable()
        self.assertEqual(self.service.get_state(mission_id)["budget"]["attempts_consumed"], 1)
        self.assertRefuses(ms.PROBLEM_BUDGET_EXHAUSTED, self.call, "record_continuation",
                           mission_id, "second")
        # PF-1 reproduction: delete only the continuation records.
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["continuations"] = []
        self.refuse_raw(d, code)
        # Deleted checkpoint record.
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["checkpoints"] = []
        self.refuse_raw(d, code)
        # Multiply attributed: the continuation record duplicated. Two
        # records carrying one operation's sequence already break the
        # append-only order, so the structural check refuses first; the
        # reused acceptance below reaches the reconciliation itself.
        d = json.loads(json.dumps(good))
        c = self.state_of(d, mission_id)["continuations"]
        c.append(dict(c[0], attempt=2))
        self.refuse_raw(d, ms.PROBLEM_SEQUENCE)
        d = json.loads(json.dumps(good))
        c = self.state_of(d, mission_id)["continuations"]
        c.append(dict(c[0], attempt=2, sequence=c[0]["sequence"] + 1))
        self.refuse_raw(d, ms.PROBLEM_OPERATION_BINDING)
        # Reused acceptance event: E1's acceptance copied onto E2.
        d = json.loads(json.dumps(good))
        evidence = self.state_of(d, mission_id)["evidence"]
        evidence[1]["acceptance"] = json.loads(json.dumps(evidence[0]["acceptance"]))
        evidence[1]["acceptance"]["content_digest_sha256"] = "2" * 64
        self.refuse_raw(d, code)
        # Orphaned effect: a continuation naming an operation the ledger
        # does not hold.
        d = json.loads(json.dumps(good))
        c = self.state_of(d, mission_id)["continuations"]
        c.append(dict(c[0], attempt=2, operation_id=hexid("mo", 0xABC), sequence=99))
        self.refuse_raw(d, ms.PROBLEM_OPERATION_BINDING)
        # Budget is derived from the ledger, never the deletable list: even
        # a pure evaluator handed the tampered state still reports the
        # attempt consumed.
        contract_ = good["missions"][mission_id]["revisions"][0]["proposal"]["proof_contract"]
        tampered = json.loads(json.dumps(self.state_of(good, mission_id)))
        tampered["continuations"] = []
        self.assertEqual(self.mp.budget(contract_, tampered)["attempts_consumed"], 1)
        self.assertTrue(self.mp.attempts_exhausted(contract_, tampered))
        # The genuine document is untouched by all of the above (refused
        # calls mint unconsumed ids, so compare everything but reservations).
        current = self.stable()
        self.assertEqual(current["mission_state"], good["mission_state"])
        self.assertEqual(current["authorizations"], good["authorizations"])
        self.assertRefuses(ms.PROBLEM_BUDGET_EXHAUSTED, self.call, "record_continuation",
                           mission_id, "still exhausted")

    def test_S2_resolution_may_not_name_evidence_invalidated_at_or_before_it(self):
        ms, mp = self.ms, self.mp
        code = ms.PROBLEM_EVIDENCE_INVALIDATED
        mission_id = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, True, [])
        e1 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "1" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, e1["evidence_id"], "1" * 64)
        self.call("invalidate_evidence", mission_id, e1["evidence_id"], "wrong run")
        e2 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "2" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, e2["evidence_id"], "2" * 64)
        blocker = self.call("open_blocker", mission_id, "disk_full", "x")
        extra = self.call("declare_dependency", mission_id, "RESOURCE", "shared-cache")
        # The service refuses invalidated evidence with the R-32 code.
        self.assertRefuses(code, self.call, "resolve_blocker", mission_id,
                           blocker["blocker_id"], e1["evidence_id"])
        self.assertRefuses(code, self.call, "resolve_dependency", mission_id,
                           extra["dependency_id"], e1["evidence_id"])
        self.call("resolve_blocker", mission_id, blocker["blocker_id"], e2["evidence_id"])
        self.call("resolve_dependency", mission_id, extra["dependency_id"],
                  e2["evidence_id"])
        self.call("observe_resource_readiness", mission_id, "build_host", "READY",
                  self.clock())
        self.call("complete_successfully", mission_id, "done")
        good = self.stable()
        # PF-2 reproduction: point the resolutions at E1 (invalidated earlier).
        for name in ("blockers", "dependencies"):
            d = json.loads(json.dumps(good))
            self.state_of(d, mission_id)[name][0]["resolution"]["evidence_id"] = e1["evidence_id"]
            # keep the outcome consistent with the tamper so ONLY R-32 fires
            for op in self.state_of(d, mission_id)["applied_operations"]:
                if op["kind"] in (ms.OPERATION_RESOLVE_BLOCKER, ms.OPERATION_RESOLVE_DEPENDENCY) \
                        and op["outcome"].get("evidence_id") == e2["evidence_id"]:
                    if (name == "blockers") == (op["kind"] == ms.OPERATION_RESOLVE_BLOCKER):
                        op["outcome"]["evidence_id"] = e1["evidence_id"]
            self.refuse_raw(d, code)
        # Legitimate counterpart: evidence invalidated LATER keeps its
        # historical resolution readable (comparison by sequence).
        later = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", later, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, True, [])
        e3 = self.call("submit_evidence", later, "tests_pass", "VERIFICATION_RECORD",
                       "3" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", later, e3["evidence_id"], "3" * 64)
        blocker = self.call("open_blocker", later, "disk_full", "x")
        self.call("resolve_blocker", later, blocker["blocker_id"], e3["evidence_id"])
        self.call("invalidate_evidence", later, e3["evidence_id"], "superseded afterwards")
        self.stable()
        self.assertEqual(self.service.get_state(later)["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(self.service.get_state(later)["proof"]["requirements"]["tests_pass"],
                         mp.REQUIREMENT_INVALIDATED)

    def test_S3_subrecord_provenance_must_equal_its_operation(self):
        ms = self.ms
        code = ms.PROBLEM_PROVENANCE_MISMATCH
        mission_id = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                             "OPAQUE_REFERENCE", "opaque:1", HEX_A, True, [])
        e1 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "1" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, e1["evidence_id"], "1" * 64,
                  context=self.other)
        good = self.stable()
        state = self.state_of(good, mission_id)
        acceptance = state["evidence"][0]["acceptance"]
        self.assertEqual(acceptance["provenance"]["principal_ref"], "uid:502")
        # PF-3 reproduction: another valid principal on the acceptance only.
        for key, value in (("principal_ref", "uid:999"), ("configured_subject", "someone"),
                           ("transport", "other"),
                           ("principal_kind", mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL)):
            with self.subTest(key):
                d = json.loads(json.dumps(good))
                self.state_of(d, mission_id)["evidence"][0]["acceptance"]["provenance"][key] = value
                self.refuse_raw(d, code)
        # The submission's own provenance, and its revision (R-33a).
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["evidence"][0]["provenance"]["principal_ref"] = "uid:502"
        self.refuse_raw(d, code)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["evidence"][0]["provenance"]["revision"] = 2
        self.refuse_raw(d, code)
        # The operation's own provenance revision on a contract-dependent
        # operation must be the bound activation's revision.
        d = json.loads(json.dumps(good))
        ops = self.state_of(d, mission_id)["applied_operations"]
        ops[1]["provenance"]["revision"] = 2
        self.state_of(d, mission_id)["artifacts"][0]["provenance"]["revision"] = 2
        self.refuse_raw(d, code)
        # Legitimate: same-principal acceptance (R-27 stands) loads.
        e2 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "2" * 64, [artifact["artifact_id"]])
        self.call("accept_evidence", mission_id, e2["evidence_id"], "2" * 64)
        self.stable()

    def test_S4_outcomes_are_closed_typed_reconciled_and_replayed_historically(self):
        ms, mp = self.ms, self.mp
        from mission import state_service as sm
        mission_id = self.ready_mission(required_dependencies=[])
        claim_op = self.oid()
        claim_seq = self.seq(mission_id)
        claim = self.service.record_claim(mission_id, claim_op, claim_seq, "tests_pass",
                                          "I say so", self.context)
        continuation = self.call("record_continuation", mission_id, "attempt one")
        checkpoint = self.call("record_checkpoint", mission_id, [], ["all"], "r", "s")
        self.call("open_blocker", mission_id, "disk_full", "later block")
        good = self.stable()
        ops = self.state_of(good, mission_id)["applied_operations"]
        by_kind = dict((op["kind"], op) for op in ops)
        # PF-4 reproduction on the continuation: fabricated success / empty.
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][2]["outcome"]["progress"] = "COMPLETED"
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][2]["outcome"] = {}
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        # R-31.5: an ordinary recording operation (record_claim): fabricated
        # progress, sequence and named identity.
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["progress"] = "BLOCKED"
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["sequence"] += 1
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["claim_id"] = hexid("mc", 0x77)
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["operation_id"] = ops[2]["operation_id"]
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["extra"] = 1
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        # R-31.6: a DERIVED field fabricated on the continuation.
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][2]["outcome"]["attempts_remaining"] = 9
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)
        # R-31.7: bool-for-int and float-for-int inside the checkpoint budget,
        # a bool for attempt, and a float sequence.
        for path, value, code in (
            (("budget", "attempts_consumed"), True, ms.PROBLEM_OUTCOME_MALFORMED),
            (("budget", "checkpoints_remaining"), 7.0, ms.PROBLEM_OUTCOME_MALFORMED),
        ):
            with self.subTest(path):
                d = json.loads(json.dumps(good))
                target = self.state_of(d, mission_id)["applied_operations"][3]["outcome"]
                target[path[0]][path[1]] = value
                self.assertEqual(target[path[0]][path[1]],
                                 good["mission_state"][mission_id]["applied_operations"][3][
                                     "outcome"][path[0]][path[1]])  # equal, yet refused
                self.refuse_raw(d, code)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][2]["outcome"]["attempt"] = True
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][3]["outcome"]["refusal"] = {"problem": 1, "detail": "x"}
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][3]["outcome"]["active_blocker_ids"] = ["mb-x"]
        self.refuse_raw(d, ms.PROBLEM_OUTCOME_MALFORMED)
        d = json.loads(json.dumps(good))
        self.state_of(d, mission_id)["applied_operations"][1]["outcome"]["requirement_key"] = "other"
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)
        # R-31.4: exact replay returns the genuine historical outcome even
        # though the Mission is BLOCKED now.
        self.assertEqual(self.service.get_state(mission_id)["progress"], ms.PROGRESS_BLOCKED)
        replay = self.service.record_claim(mission_id, claim_op, claim_seq, "tests_pass",
                                           "I say so", self.context)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertEqual(dict(replay, idempotent=False), dict(claim, idempotent=False))
        historical = dict((k, v) for k, v in replay.items() if k != "idempotent")
        self.assertEqual(historical, ops[1]["outcome"])
        self.assertEqual(self.stable(), good)
        # Every kind's stored outcome has exactly its closed key set.
        for op in ops:
            self.assertEqual(sorted(op["outcome"]), sorted(
                ms.OUTCOME_COMMON_KEYS + ms.OUTCOME_KEYS_BY_KIND[op["kind"]]))
        self.assertEqual(set(ms.OUTCOME_KEYS_BY_KIND), set(ms.OPERATION_KINDS))

    def test_S5_identical_rebind_noop_is_a_first_class_reconciled_case(self):
        ms = self.ms
        code = ms.PROBLEM_EFFECT_INCONSISTENT
        t_id = self.completed_prerequisite()
        d_id = self.dependent_on(t_id)
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        noop = self.call("bind_dependency", d_id, "upstream", t_id)
        self.assertEqual(noop["dependency_id"], bound["dependency_id"])
        self.assertFalse(noop["new_binding"])
        good = self.stable()
        ops = self.state_of(good, d_id)["applied_operations"]
        self.assertEqual([op["outcome"]["new_binding"] for op in ops
                          if op["kind"] == ms.OPERATION_BIND_DEPENDENCY], [True, False])
        # Flip the no-op to claim a new binding.
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["applied_operations"][-1]["outcome"]["new_binding"] = True
        self.refuse_raw(d, code)
        # Flip the genuine bind to claim a no-op.
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["applied_operations"][-2]["outcome"]["new_binding"] = False
        self.refuse_raw(d, code)
        # Delete the earlier dependency record: both operations lose their
        # effect.
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["dependencies"] = []
        self.refuse_raw(d, code)
        # A no-op claiming a different reference than the earlier binding.
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["applied_operations"][-1]["outcome"]["reference"] = hexid("mn", 0x55)
        self.refuse_raw(d, code)
        # The genuine record still loads, re-saves and replays.
        self.assertEqual(self.stable(), good)
        replay = self.service.bind_dependency(d_id, ops[-1]["operation_id"],
                                              ops[-1]["sequence"] - 1, "upstream", t_id,
                                              self.context)
        self.assertTrue(replay["idempotent"])
        self.assertFalse(replay["new_binding"])


# ====================================================================
# T. Pre-freeze correction round 2 (PF-5..PF-8; R-34..R-37)
# ====================================================================


class TPreFreezeRoundTwoTests(ServiceStateFixture):
    """RAW real-service fixtures only: built with real service calls,
    tampered on the raw document, refused on load AND save with bytes
    unchanged; no fixture sealing anywhere on these paths."""

    def rich_mission(self):
        """Every operation kind applied once through the real service."""
        ms = self.ms
        t_id = self.completed_prerequisite()
        d_id = self.dependent_on(t_id)
        self.call("record_claim", d_id, "tests_pass", "I say so")
        original = self.call("record_artifact", d_id, None, "ORIGINAL_INPUT",
                             ms.LOCATOR_KIND_REPOSITORY_PATH, "src/x.py", "1" * 64, True, [])
        log = self.call("record_artifact", d_id, "test_log", "VERIFICATION",
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, True,
                        [original["artifact_id"]])
        evidence = self.call("submit_evidence", d_id, "tests_pass", "VERIFICATION_RECORD",
                             "e" * 64, [log["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", d_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        stale = self.call("submit_evidence", d_id, "tests_pass", "NARRATIVE_CLAIM",
                          "9" * 64, [])
        self.call("invalidate_evidence", d_id, stale["evidence_id"], "narrative")
        blocker = self.call("open_blocker", d_id, "disk_full", "no space")
        self.call("resolve_blocker", d_id, blocker["blocker_id"], evidence["evidence_id"])
        bound = self.call("bind_dependency", d_id, "upstream", t_id)
        self.call("bind_dependency", d_id, "upstream", t_id)  # the no-op
        extra = self.call("declare_dependency", d_id, "RESOURCE", "shared-cache")
        self.call("resolve_dependency", d_id, bound["dependency_id"], evidence["evidence_id"])
        self.call("resolve_dependency", d_id, extra["dependency_id"], evidence["evidence_id"])
        self.call("observe_resource_readiness", d_id, "build_host", "READY", self.clock())
        self.call("record_continuation", d_id, "one more")
        self.call("record_checkpoint", d_id, ["tests"], ["close"], "retry", "stop")
        return t_id, d_id, evidence

    def test_T1_invocation_digest_binds_every_payload_field(self):
        ms = self.ms
        code = ms.PROBLEM_INVOCATION_MISMATCH
        t_id, d_id, evidence = self.rich_mission()
        self.call("complete_successfully", d_id, "all done")
        good = self.stable()
        state = self.state_of(good, d_id)
        kinds = set(op["kind"] for op in state["applied_operations"])
        self.assertEqual(kinds, set(ms.OPERATION_KINDS) - {ms.OPERATION_CLOSE_UNSUCCESSFUL,
                                                           ms.OPERATION_ABANDON})
        # Completeness: every stored digest re-derives from stored state
        # with expected_sequence == sequence - 1, for every kind present,
        # and any other expected_sequence does NOT reproduce it.
        from mission import state_reconcile as sr
        for op in state["applied_operations"]:
            effect = self.effect_of(state, op)
            args = sr._invocation_arguments(op["kind"], effect, op["outcome"], state)
            self.assertEqual(ms.invocation_digest(op["kind"], d_id, op["sequence"] - 1, args),
                             op["content_digest_sha256"], op["kind"])
            self.assertNotEqual(ms.invocation_digest(op["kind"], d_id, op["sequence"], args),
                                op["content_digest_sha256"])
        # PF-5 reproduction and the rest of the payload surface, each a
        # field NO outcome carries.
        tampers = {
            "artifact available": ("artifacts", 1, "available", False),
            "artifact locator": ("artifacts", 1, "locator", "opaque:other"),
            "artifact digest": ("artifacts", 1, "content_digest_sha256", "3" * 64),
            "claim statement": ("claims", 0, "statement", "I did NOT say so"),
            "evidence artifact ids": ("evidence", 0, "artifact_ids", []),
            "blocker description": ("blockers", 0, "description", "other"),
            "readiness observed_at": ("resource_readiness", 0, "observed_at", 5),
            "continuation reason": ("continuations", 0, "reason", "other"),
            "checkpoint completed work": ("checkpoints", 0, "completed_work", ["all"]),
            "checkpoint stop condition": ("checkpoints", 0, "stop_condition", "never"),
        }
        for label, (name, index, field, value) in tampers.items():
            with self.subTest(label):
                d = json.loads(json.dumps(good))
                self.state_of(d, d_id)[name][index][field] = value
                self.refuse_raw(d, code)
        # Nested events and the closure.
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["evidence"][1]["invalidation"]["reason"] = "other"
        self.refuse_raw(d, code)
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["closure"]["detail"] = "not what was said"
        self.refuse_raw(d, ms.PROBLEM_EFFECT_INCONSISTENT)  # outcome disagrees first
        d = json.loads(json.dumps(good))
        self.state_of(d, d_id)["closure"]["detail"] = "not what was said"
        self.state_of(d, d_id)["applied_operations"][-1]["outcome"]["detail"] = "not what was said"
        self.refuse_raw(d, code)
        # PF-5 as the Reviewer described it: unavailable artifact made
        # available cannot open closure on an unreachable Mission.
        u_id = self.ready_mission(required_dependencies=[])
        log = self.call("record_artifact", u_id, "test_log", "VERIFICATION",
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, False, [])
        ev = self.call("submit_evidence", u_id, "tests_pass", "VERIFICATION_RECORD",
                       "e" * 64, [log["artifact_id"]])
        self.call("accept_evidence", u_id, ev["evidence_id"], "e" * 64)
        self.call("observe_resource_readiness", u_id, "build_host", "READY", self.clock())
        self.assertRefuses(self.mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", u_id, "x")
        good = self.stable()
        d = json.loads(json.dumps(good))
        self.state_of(d, u_id)["artifacts"][0]["available"] = True
        self.refuse_raw(d, code)
        # Genuine record still loads, re-saves and replays.
        self.assertEqual(self.stable(), good)
        ops = self.state_of(good, u_id)["applied_operations"]
        replay = self.service.record_artifact(
            u_id, ops[1]["operation_id"], ops[1]["sequence"] - 1, "test_log",
            "VERIFICATION", ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, False,
            [], self.context)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(dict((k, v) for k, v in replay.items() if k != "idempotent"),
                         ops[1]["outcome"])

    def effect_of(self, state, op):
        ms = self.ms
        oid = op["operation_id"]
        kind = op["kind"]
        one = lambda name: [e for e in state[name] if e["operation_id"] == oid]
        nested = lambda name, ev: [e for e in state[name]
                                   if e[ev] is not None and e[ev]["operation_id"] == oid]
        table = {
            ms.OPERATION_ACTIVATE_CONTRACT: one("contract_activations"),
            ms.OPERATION_RECORD_CLAIM: one("claims"),
            ms.OPERATION_RECORD_ARTIFACT: one("artifacts"),
            ms.OPERATION_SUBMIT_EVIDENCE: one("evidence"),
            ms.OPERATION_ACCEPT_EVIDENCE: nested("evidence", "acceptance"),
            ms.OPERATION_INVALIDATE_EVIDENCE: nested("evidence", "invalidation"),
            ms.OPERATION_OPEN_BLOCKER: one("blockers"),
            ms.OPERATION_RESOLVE_BLOCKER: nested("blockers", "resolution"),
            ms.OPERATION_BIND_DEPENDENCY: one("dependencies"),
            ms.OPERATION_RESOLVE_DEPENDENCY: nested("dependencies", "resolution"),
            ms.OPERATION_OBSERVE_RESOURCE_READINESS: one("resource_readiness"),
            ms.OPERATION_RECORD_CONTINUATION: one("continuations"),
            ms.OPERATION_RECORD_CHECKPOINT: one("checkpoints"),
        }
        if kind in table:
            found = table[kind]
            return found[0] if found else None
        return state["closure"]

    def test_T2_acceptance_cannot_describe_a_refused_event(self):
        ms = self.ms
        mission_id = self.ready_mission(required_dependencies=[])
        log = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, True, [])
        e1 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "e" * 64, [log["artifact_id"]])
        self.call("invalidate_evidence", mission_id, e1["evidence_id"], "wrong")
        e2 = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "e" * 64, [log["artifact_id"]])
        self.call("accept_evidence", mission_id, e2["evidence_id"], "e" * 64)
        good = self.stable()
        # PF-6 reproduction: move E2's acceptance onto E1, retarget the
        # outcome, and recompute the request digest consistently so ONLY
        # the acceptance-after-invalidation binding is exercised.
        d = json.loads(json.dumps(good))
        state = self.state_of(d, mission_id)
        state["evidence"][0]["acceptance"] = state["evidence"][1]["acceptance"]
        state["evidence"][1]["acceptance"] = None
        op = state["applied_operations"][-1]
        op["outcome"]["evidence_id"] = e1["evidence_id"]
        op["content_digest_sha256"] = ms.invocation_digest(
            ms.OPERATION_ACCEPT_EVIDENCE, mission_id, op["sequence"] - 1,
            {"evidence_id": e1["evidence_id"], "content_digest_sha256": "e" * 64})
        self.refuse_raw(d, ms.PROBLEM_ACCEPTANCE_AFTER_INVALIDATION)
        # Acceptance under a different activation than the submission.
        stale = self.ready_mission(required_dependencies=[])
        log = self.call("record_artifact", stale, "test_log", "VERIFICATION",
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, True, [])
        old = self.call("submit_evidence", stale, "tests_pass", "VERIFICATION_RECORD",
                        "e" * 64, [log["artifact_id"]])
        self.edit(stale, 1, objective="v2", proof_contract=contract(required_dependencies=[]))
        self.approve(stale, 2)
        self.call("activate_proof_contract", stale)
        new = self.call("submit_evidence", stale, "tests_pass", "VERIFICATION_RECORD",
                        "e" * 64, [log["artifact_id"]])
        self.call("accept_evidence", stale, new["evidence_id"], "e" * 64)
        good = self.stable()
        d = json.loads(json.dumps(good))
        state = self.state_of(d, stale)
        state["evidence"][0]["acceptance"] = state["evidence"][1]["acceptance"]
        state["evidence"][1]["acceptance"] = None
        op = state["applied_operations"][-1]
        op["outcome"]["evidence_id"] = old["evidence_id"]
        op["content_digest_sha256"] = ms.invocation_digest(
            ms.OPERATION_ACCEPT_EVIDENCE, stale, op["sequence"] - 1,
            {"evidence_id": old["evidence_id"], "content_digest_sha256": "e" * 64})
        self.refuse_raw(d, ms.PROBLEM_ACCEPTANCE_ACTIVATION_MISMATCH)
        # An acceptance whose activation id is simply rewritten refuses too.
        d = json.loads(json.dumps(good))
        self.state_of(d, stale)["evidence"][1]["acceptance"]["activation_id"] = (
            self.state_of(d, stale)["contract_activations"][0]["activation_id"])
        self.refuse_raw(d, ms.PROBLEM_ACCEPTANCE_ACTIVATION_MISMATCH)
        # Legitimate counterpart: acceptance THEN later invalidation.
        self.call("invalidate_evidence", stale, new["evidence_id"], "later")
        self.stable()

    def test_T3_asserting_closures_and_activations_bind_their_revision(self):
        ms = self.ms
        code = ms.PROBLEM_PROVENANCE_MISMATCH
        # A genuine revision-1 budget_exhausted closure.
        mission_id = self.ready_mission(required_dependencies=[], continuation_budget={
            "max_attempts": 1, "max_checkpoints": 1})
        self.call("record_continuation", mission_id, "only")
        self.call("close_unsuccessful", mission_id, "budget_exhausted", "spent")
        good = self.stable()
        d = json.loads(json.dumps(good))
        state = self.state_of(d, mission_id)
        state["closure"]["provenance"]["revision"] = 2
        state["applied_operations"][-1]["provenance"]["revision"] = 2
        self.refuse_raw(d, code)
        # hard_blocker_unresolvable likewise.
        blocked = self.ready_mission(required_dependencies=[])
        self.call("open_blocker", blocked, "disk_full", "x")
        self.call("close_unsuccessful", blocked, "hard_blocker_unresolvable", "stuck")
        good = self.stable()
        d = json.loads(json.dumps(good))
        state = self.state_of(d, blocked)
        state["closure"]["provenance"]["revision"] = 2
        state["applied_operations"][-1]["provenance"]["revision"] = 2
        self.refuse_raw(d, code)
        # Activation: stored revision 1 but provenance (both) say 2.
        d = json.loads(json.dumps(good))
        state = self.state_of(d, blocked)
        state["contract_activations"][0]["provenance"]["revision"] = 2
        state["applied_operations"][0]["provenance"]["revision"] = 2
        self.refuse_raw(d, code)
        # Legitimate counterparts: caller closure and abandonment AFTER an
        # EDIT still succeed and their records load and re-save.
        for reason, name in (("closed_by_caller", "close_unsuccessful"), (None, "abandon")):
            m = self.ready_mission(required_dependencies=[])
            self.edit(m, 1, objective="v2", proof_contract=contract())
            if reason is None:
                outcome = self.call("abandon", m, "gave up after the edit")
            else:
                outcome = self.call(name, m, reason, "closed after the edit")
            self.assertEqual(outcome["progress"], ms.PROGRESS_ABANDONED if reason is None
                             else ms.PROGRESS_CLOSED_UNSUCCESSFUL)
            document = self.stable()
            closure = self.state_of(document, m)["closure"]
            self.assertEqual(closure["provenance"]["revision"], 2)
            self.assertEqual(self.state_of(document, m)["contract_activations"][0]["revision"], 1)
        # The asserting reasons still refuse after an EDIT (stale contract).
        from mission import state_service as sm
        m = self.ready_mission(required_dependencies=[])
        self.edit(m, 1, objective="v2", proof_contract=contract())
        self.assertRefuses(sm.PROBLEM_CONTRACT_STALE, self.call, "close_unsuccessful", m,
                           "budget_exhausted", "x")

    def test_T4_sequences_are_integers_before_any_comparison(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self.call("record_claim", mission_id, "tests_pass", "x")
        ev = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "e" * 64, [])
        self.call("accept_evidence", mission_id, ev["evidence_id"], "e" * 64)
        self.call("record_continuation", mission_id, "one")
        good = self.stable()
        cases = {
            "operation sequence 1.0": ("applied_operations", 0, "sequence", 1.0),
            "operation sequence True": ("applied_operations", 0, "sequence", True),
            "activation sequence True": ("contract_activations", 0, "sequence", True),
            "activation sequence 1.0": ("contract_activations", 0, "sequence", 1.0),
            "claim sequence 2.0": ("claims", 0, "sequence", 2.0),
            "outcome sequence 2.0": ("applied_operations", 1, "outcome.sequence", 2.0),
            "state sequence as float": (None, None, "sequence",
                                        float(self.state_of(good, mission_id)["sequence"])),
            "activation revision True": ("contract_activations", 0, "revision", True),
            "continuation attempt 1.0": ("continuations", 0, "attempt", 1.0),
            "acceptance sequence 4.0": ("evidence", 0, "acceptance.sequence", 4.0),
        }
        for label, (name, index, field, value) in cases.items():
            with self.subTest(label):
                d = json.loads(json.dumps(good))
                state = self.state_of(d, mission_id)
                target = state if name is None else state[name][index]
                parts = field.split(".")
                for part in parts[:-1]:
                    target = target[part]
                self.assertEqual(target[parts[-1]], value)  # equal in Python...
                target[parts[-1]] = value
                self.refuse_raw(d, mission_record.PROBLEM_BAD_TYPE)  # ...refused anyway
        self.assertEqual(self.stable(), good)


# ====================================================================
# U. Pre-freeze correction round 3 (PF-9..PF-12; R-38..R-42)
# ====================================================================


class UPreFreezeRoundThreeTests(ServiceStateFixture):
    """RAW real-service fixtures, no sealing: tampers refuse on load AND
    save with bytes unchanged; legitimate counterparts load and re-save."""

    def test_U1_reference_lists_normalize_once_and_replay_as_one_invocation(self):
        from mission import state_service as sm
        ms = self.ms
        mission_id = self.ready_mission(required_dependencies=[])
        a = self.call("record_artifact", mission_id, None, "ORIGINAL_INPUT",
                      ms.LOCATOR_KIND_REPOSITORY_PATH, "a", None, True, [])["artifact_id"]
        b = self.call("record_artifact", mission_id, None, "ORIGINAL_INPUT",
                      ms.LOCATOR_KIND_REPOSITORY_PATH, "b", None, True, [])["artifact_id"]
        c = self.call("record_artifact", mission_id, None, "ORIGINAL_INPUT",
                      ms.LOCATOR_KIND_REPOSITORY_PATH, "c", None, True, [])["artifact_id"]
        # PF-9: duplicates in both methods persist (the regression saved
        # nothing), stored deduplicated and sorted.
        dup_op, seq = self.oid(), self.seq(mission_id)
        produced = self.service.record_artifact(
            mission_id, dup_op, seq, None, "PRODUCED", ms.LOCATOR_KIND_OPAQUE_REFERENCE,
            "p", None, True, [b, a, a], self.context)
        self.assertFalse(produced["idempotent"])
        ev_op, ev_seq = self.oid(), self.seq(mission_id)
        evidence = self.service.submit_evidence(
            mission_id, ev_op, ev_seq, "tests_pass", "VERIFICATION_RECORD", "e" * 64,
            [b, a, b, a], self.context)
        good = self.stable()
        state = self.state_of(good, mission_id)
        self.assertEqual(state["artifacts"][3]["derived_from"], sorted([a, b]))
        self.assertEqual(state["evidence"][0]["artifact_ids"], sorted([a, b]))
        # Replay-equivalence, pinned deliberately (R-38): [A, A], [A] and any
        # reordering are the SAME invocation.
        for replay_list in ([a, b], [b, a], [a, a, b], [b, b, a, a]):
            with self.subTest(str(replay_list)):
                replay = self.service.record_artifact(
                    mission_id, dup_op, seq, None, "PRODUCED",
                    ms.LOCATOR_KIND_OPAQUE_REFERENCE, "p", None, True, replay_list,
                    self.context)
                self.assertTrue(replay["idempotent"])
                self.assertEqual(replay["artifact_id"], produced["artifact_id"])
                replay = self.service.submit_evidence(
                    mission_id, ev_op, ev_seq, "tests_pass", "VERIFICATION_RECORD",
                    "e" * 64, replay_list, self.context)
                self.assertTrue(replay["idempotent"])
                self.assertEqual(replay["evidence_id"], evidence["evidence_id"])
        self.assertEqual(json.loads(self.read_bytes()), good)
        # A genuinely different set still conflicts, atomically.
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT, self.service.record_artifact,
                           mission_id, dup_op, seq, None, "PRODUCED",
                           ms.LOCATOR_KIND_OPAQUE_REFERENCE, "p", None, True, [a, c],
                           self.context)
        self.assertRefuses(sm.PROBLEM_STATE_OPERATION_CONFLICT, self.service.submit_evidence,
                           mission_id, ev_op, ev_seq, "tests_pass", "VERIFICATION_RECORD",
                           "e" * 64, [a], self.context)
        self.assertEqual(json.loads(self.read_bytes()), good)
        # Reload through a fresh store/service and re-save unchanged.
        from mission import service as service_module
        from mission import store as store_module
        fresh = service_module.MissionService(store_module.MissionStore(self.directory),
                                              self.clock)
        self.assertEqual(fresh.get_state(mission_id)["record"], state)
        self.assertEqual(self.stable(), good)

    def test_U2_reconstructible_service_preconditions_hold_in_history(self):
        ms = self.ms
        code = ms.PROBLEM_HISTORY_IMPOSSIBLE
        # PF-10a: a continuation recorded while BLOCKED, coordinated rewrite.
        mission_id = self.ready_mission(required_dependencies=[])
        self.call("open_blocker", mission_id, "disk_full", "x")
        self.call("record_claim", mission_id, "tests_pass", "while blocked")
        good = self.stable()
        d = json.loads(json.dumps(good))
        state = self.state_of(d, mission_id)
        op = state["applied_operations"][-1]
        claim = state["claims"].pop()
        op["kind"] = ms.OPERATION_RECORD_CONTINUATION
        state["continuations"].append(ms.new_continuation(
            1, "sneaked", claim["claimed_at"], claim["provenance"], op["operation_id"],
            op["sequence"]))
        op["outcome"] = {"mission_id": mission_id, "operation_id": op["operation_id"],
                         "sequence": op["sequence"], "progress": ms.PROGRESS_BLOCKED,
                         "attempt": 1, "attempts_remaining": 2}
        op["content_digest_sha256"] = ms.invocation_digest(
            ms.OPERATION_RECORD_CONTINUATION, mission_id, op["sequence"] - 1,
            {"reason": "sneaked"})
        self.refuse_raw(d, code)
        # Legitimate: continuation after the blocker is resolved.
        log = self.call("record_artifact", mission_id, "test_log", "VERIFICATION",
                        ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, True, [])
        ev = self.call("submit_evidence", mission_id, "tests_pass", "VERIFICATION_RECORD",
                       "e" * 64, [log["artifact_id"]])
        self.call("accept_evidence", mission_id, ev["evidence_id"], "e" * 64)
        blocker_id = self.service.get_state(mission_id)["record"]["blockers"][0]["blocker_id"]
        self.call("resolve_blocker", mission_id, blocker_id, ev["evidence_id"])
        self.call("record_continuation", mission_id, "after resolution")
        self.stable()
        # PF-10b: a required key's role rewritten, coordinated with outcome
        # and digest, on an unfinished Mission.
        good = self.stable()
        d = json.loads(json.dumps(good))
        state = self.state_of(d, mission_id)
        artifact = [a for a in state["artifacts"] if a["key"] == "test_log"][0]
        artifact["role"] = "PRODUCED"
        op = [o for o in state["applied_operations"]
              if o["operation_id"] == artifact["operation_id"]][0]
        op["outcome"]["role"] = "PRODUCED"
        op["content_digest_sha256"] = ms.invocation_digest(
            ms.OPERATION_RECORD_ARTIFACT, mission_id, op["sequence"] - 1, {
                "key": "test_log", "role": "PRODUCED",
                "locator_kind": ms.LOCATOR_KIND_OPAQUE_REFERENCE, "locator": "opaque:log",
                "content_digest_sha256": HEX_A, "available": True, "derived_from": []})
        self.refuse_raw(d, code)
        # Legitimate: the valid artifact under its own historical contract,
        # even after an EDIT changes the contract's declared role later.
        self.edit(mission_id, 1, objective="v2", proof_contract=contract(
            required_dependencies=[],
            required_artifacts=[{"key": "test_log", "role": "PRODUCED",
                                 "expected_content_digest_sha256": HEX_A}]))
        self.assertEqual(self.stable(), json.loads(self.read_bytes()))

    def test_U3_cited_revision_is_the_one_the_service_would_have_recorded(self):
        ms = self.ms
        code = ms.PROBLEM_REVISION_IMPOSSIBLE
        # PF-11: a nonexistent revision on a genuine caller closure / abandon.
        for closing in ("close_unsuccessful", "abandon"):
            with self.subTest(closing):
                m = self.ready_mission(required_dependencies=[])
                if closing == "abandon":
                    self.call("abandon", m, "gave up")
                else:
                    self.call("close_unsuccessful", m, "closed_by_caller", "closed")
                good = self.stable()
                for cited in (99, 2, 0):
                    d = json.loads(json.dumps(good))
                    state = self.state_of(d, m)
                    state["closure"]["provenance"]["revision"] = cited
                    state["applied_operations"][-1]["provenance"]["revision"] = cited
                    self.refuse_raw(d, code if cited else mission_record.PROBLEM_BAD_VALUE)
        # R-40a: revision 1 at T1, EDIT to revision 2 at a distinct later T2,
        # caller closure later at T3; rewriting provenance back to revision 1
        # is impossible although revision 1 exists and is not in the future.
        m = self.ready_mission(required_dependencies=[])
        self.clock.advance(10)
        self.edit(m, 1, objective="v2", proof_contract=contract())
        self.clock.advance(10)
        closed = self.call("close_unsuccessful", m, "closed_by_caller", "after the edit")
        good = self.stable()
        state = self.state_of(good, m)
        self.assertEqual(state["closure"]["provenance"]["revision"], 2)
        self.assertEqual(state["contract_activations"][0]["revision"], 1)
        t1 = good["missions"][m]["revisions"][0]["created_at"]
        t2 = good["missions"][m]["revisions"][1]["created_at"]
        t3 = state["closure"]["closed_at"]
        self.assertLess(t1, t2)
        self.assertLess(t2, t3)
        d = json.loads(json.dumps(good))
        self.state_of(d, m)["closure"]["provenance"]["revision"] = 1
        self.state_of(d, m)["applied_operations"][-1]["provenance"]["revision"] = 1
        self.refuse_raw(d, code)
        # Legitimate: the genuine revision-2 closure, and after further EDITs.
        self.assertEqual(self.stable(), good)
        self.edit(m, 2, objective="v3", proof_contract=contract())
        self.edit(m, 3, objective="v4", proof_contract=contract())
        after = self.stable()
        self.assertEqual(after["mission_state"][m], state)
        # Same-second EDIT and operation: BOTH orderings are legitimate.
        for order in ("operation_then_edit", "edit_then_operation"):
            with self.subTest(order):
                n = self.ready_mission(required_dependencies=[])
                self.clock.advance(5)
                if order == "operation_then_edit":
                    self.call("record_claim", n, "tests_pass", "x")  # cites revision 1
                    decision_id = self.service.mint_decision_id(self.context)
                    self.service.edit(n, 1, proposal(proof_contract=contract()),
                                      decision_id, self.context)  # same second
                    expected = 1
                else:
                    decision_id = self.service.mint_decision_id(self.context)
                    self.service.edit(n, 1, proposal(proof_contract=contract()),
                                      decision_id, self.context)
                    outcome = self.call("abandon", n, "same second")  # cites revision 2
                    expected = 2
                document = self.stable()
                state = self.state_of(document, n)
                self.assertEqual(document["missions"][n]["revisions"][1]["created_at"],
                                 state["applied_operations"][-1]["applied_at"])
                self.assertEqual(state["applied_operations"][-1]["provenance"]["revision"],
                                 expected)

    def test_U4_refs_revision_and_schema_version_are_typed_before_comparison(self):
        mission_id = self.ready_mission(required_dependencies=[])
        self.call("record_checkpoint", mission_id, [], ["all"], "r", "s")
        good = self.stable()
        for label, path, value in (
            ("refs.revision True", ("checkpoints", 0, "refs", "revision"), True),
            ("refs.revision 1.0", ("checkpoints", 0, "refs", "revision"), 1.0),
            ("schema_version 1.0", ("schema_version",), 1.0),
            ("schema_version True", ("schema_version",), True),
        ):
            with self.subTest(label):
                d = json.loads(json.dumps(good))
                target = self.state_of(d, mission_id)
                for part in path[:-1]:
                    target = target[part]
                self.assertEqual(target[path[-1]], value)  # equal in Python
                target[path[-1]] = value
                self.refuse_raw(d, mission_record.PROBLEM_BAD_TYPE)
        # The store's own top-level version, same class.
        for value in (1.0, True):
            d = json.loads(json.dumps(good))
            d["mission_store_schema_version"] = value
            self.write_raw(json.dumps(d))
            with self.assertRaises(self.mst.MissionStoreError):
                self.store.load()
            with open(self.store.path, "wb") as handle:
                handle.write(json.dumps(good).encode())
            with self.assertRaises(self.mst.MissionStoreError):
                self.store.save(d)
        for label, path, value in ():
            with self.subTest(label):
                d = json.loads(json.dumps(good))
                target = self.state_of(d, mission_id)
                for part in path[:-1]:
                    target = target[part]
                self.assertEqual(target[path[-1]], value)  # equal in Python
                target[path[-1]] = value
                self.refuse_raw(d, mission_record.PROBLEM_BAD_TYPE)
        self.assertEqual(self.stable(), good)


# ====================================================================
# V. Pre-freeze correction round 4 (PF-13, PF-14; R-43, R-44)
# ====================================================================


class VPreFreezeRoundFourTests(ServiceStateFixture):
    """RAW real-service fixtures, no sealing."""

    def same_second_reauthorize(self, mission_id, revision):
        """EDIT -> APPROVE -> activate, all without advancing the clock."""
        decision_id = self.service.mint_decision_id(self.context)
        self.service.edit(mission_id, revision, proposal(proof_contract=contract(
            required_dependencies=[])), decision_id, self.context)
        decision_id = self.service.mint_decision_id(self.context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        envelope = self.md.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id, mission_id=mission_id,
            revision=revision + 1, decision=self.md.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=[current["proposal"]["requested_delivery_target"]],
        )
        self.service.apply_human_decision(envelope)
        return self.call("activate_proof_contract", mission_id)

    def test_V1_cited_revision_never_moves_backward(self):
        ms = self.ms
        code = ms.PROBLEM_REVISION_REGRESSED
        for closing in ("close_unsuccessful", "abandon"):
            with self.subTest(closing):
                m = self.ready_mission(required_dependencies=[])
                self.clock.advance(7)
                # One second: EDIT to 2, APPROVE, activate 2, then close.
                activated = self.same_second_reauthorize(m, 1)
                self.assertEqual(activated["revision"], 2)
                if closing == "abandon":
                    self.call("abandon", m, "same second")
                else:
                    self.call("close_unsuccessful", m, "closed_by_caller", "same second")
                good = self.stable()
                state = self.state_of(good, m)
                t = state["closure"]["closed_at"]
                self.assertEqual(good["missions"][m]["revisions"][1]["created_at"], t)
                self.assertEqual(state["closure"]["provenance"]["revision"], 2)
                # PF-13: rewrite the closure citation from 2 back to 1. The
                # R-40a timestamp window still passes (revision 2 was created
                # in the same second) and the invocation digest is unchanged;
                # the earlier revision-2 activation proves it impossible.
                d = json.loads(json.dumps(good))
                self.state_of(d, m)["closure"]["provenance"]["revision"] = 1
                self.state_of(d, m)["applied_operations"][-1]["provenance"]["revision"] = 1
                self.refuse_raw(d, code)
                # Readable after later EDITs (Task 4 keeps editing a Mission
                # whose Task 5 state is terminal).
                self.assertEqual(self.stable(), good)
                self.edit(m, 2, objective="v3", proof_contract=contract())
                self.edit(m, 3, objective="v4", proof_contract=contract())
                self.assertEqual(self.stable()["mission_state"][m], state)
        # Legitimate same-second directions: 1 then 1, 1 then 2, 2 then 2.
        n = self.ready_mission(required_dependencies=[])
        self.clock.advance(3)
        self.call("record_claim", n, "tests_pass", "one")          # 1
        self.call("record_claim", n, "tests_pass", "one again")    # 1 then 1
        self.same_second_reauthorize(n, 1)                          # 1 then 2
        self.call("record_claim", n, "tests_pass", "two")          # 2 then 2
        document = self.stable()
        cited = [op["provenance"]["revision"]
                 for op in self.state_of(document, n)["applied_operations"]]
        self.assertEqual(cited[-4:], [1, 1, 2, 2])
        applied = set(op["applied_at"] for op in self.state_of(document, n)[
            "applied_operations"][-4:])
        self.assertEqual(len(applied), 1)
        # A genuine revision-2 caller closure after an EDIT still loads
        # after further EDITs (R-36 exemption untouched).
        self.clock.advance(1)
        self.edit(n, 2, objective="v3", proof_contract=contract())
        self.clock.advance(1)
        self.call("close_unsuccessful", n, "closed_by_caller", "later")
        good = self.stable()
        self.assertEqual(self.state_of(good, n)["closure"]["provenance"]["revision"], 3)
        self.edit(n, 3, objective="v4", proof_contract=contract())
        self.assertEqual(self.stable()["mission_state"][n], self.state_of(good, n))

    def test_V2_revision_identities_are_ints_at_the_consumption_boundary(self):
        from mission import state_service as sm
        code = self.ms.PROBLEM_REVISION_IDENTITY_MALFORMED
        # Activated revision-1 Mission: revisions[0].revision -> 1.0.
        m = self.ready_mission(required_dependencies=[])
        good = self.stable()
        self.assertEqual(good["missions"][m]["revisions"][0]["revision"], 1.0)  # == in Python
        d = json.loads(json.dumps(good))
        d["missions"][m]["revisions"][0]["revision"] = 1.0
        self.refuse_raw(d, code)
        # (bool is already refused by Task 4's own ordinal check.)
        d = json.loads(json.dumps(good))
        d["missions"][m]["revisions"][0]["revision"] = True
        self.refuse_raw(d, "mission_malformed_state")
        # The pre-activation variant that previously loaded and then failed
        # inside activation construction: now refused at load and save.
        p = self.propose(proof_contract=contract(required_dependencies=[]))["mission_id"]
        self.approve(p, 1)
        good = self.stable()
        d = json.loads(json.dumps(good))
        d["missions"][p]["revisions"][0]["revision"] = 1.0
        self.refuse_raw(d, code)
        self.write_raw(json.dumps(d))
        with self.assertRaises(self.mst.MissionStoreError) as ctx:
            self.call("activate_proof_contract", p)
        self.assertEqual(ctx.exception.problem, self.mst.PROBLEM_STORE_UNREADABLE)
        # A float current_revision is caught by Task 4 already; still refused.
        d = json.loads(json.dumps(good))
        d["missions"][p]["current_revision"] = 1.0
        self.write_raw(json.dumps(d))
        with self.assertRaises(self.mst.MissionStoreError):
            self.store.load()
        # Genuine integer ordinals throughout load, re-save and operate.
        self.write_raw(json.dumps(good))
        self.assertEqual(self.stable(), good)
        self.call("activate_proof_contract", p)
        self.assertEqual(self.service.get_state(p)["contract"]["revision"], 1)


if __name__ == "__main__":
    unittest.main()
