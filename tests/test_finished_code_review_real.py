"""I1, PART A (a REAL COMPLETED Mission): the record-side admission of a finished Mission to the live code review, on records Mission Core itself produced.

PREPARED FOR THE LEAD'S INSPECTION; NOT RUN. It reaches what the hermetic Layer 1 module (tests/test_finished_code_review.py) cannot: the steps AFTER the Mission store is loaded (the journal head
against the listing, the execution identity re-derived from the record NOW, the execution recomputed from the record), a real COMPLETED record with an established identity, a real owner's held
answer, and the new entry's agreement with the evidence library's own admission on the same records.

NO git, no worktree, no browser, no network beyond the fixture chain's own in-process pieces. It subclasses test_artifact_content.CompletedFixture through a MODULE import
(``import test_artifact_content as ac``); no test class of any pre-existing module is imported by name, and a guard (``InheritedTestGuard``) fails the module if the subclass inherits any test
method beyond the ones listed here.

I1 ALONE DOES NOT MAKE THE CONTROL WORK: no offer can exist for a finished Mission until the workspace witness and the offered flow are integrated.
"""
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_artifact_content as ac  # noqa: E402  (a MODULE import: no pre-existing test class is imported by name)
from mission import journal  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import store as mission_store  # noqa: E402
from target_runtime import artifact_content  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import mission_state  # noqa: E402
from target_runtime import run_association  # noqa: E402

#: The ONLY test methods the real-fixture class may run. An inherited test method, or a renamed one, makes InheritedTestGuard fail the module.
OWN_TESTS = [
    "test_a_lifecycle_chain_change_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_lifecycle_state_change_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_prepared_binding_change_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_real_completed_mission_is_admitted_with_a_proof_that_matches_its_listing",
    "test_a_receipt_change_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_record_that_became_blocked_and_was_listed_again_is_not_established",
    "test_a_source_registration_change_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_the_entry_and_the_evidence_librarys_own_admission_both_admit_an_unchanged_record",
    "test_the_entry_writes_nothing",
    "test_the_provider_adapter_feeds_the_review_branch_a_real_proof",
    "test_two_calls_on_an_unchanged_record_give_equal_proofs",
]


class FinishedAdmissionOnARealCompletedMission(ac.CompletedFixture):
    """A REAL COMPLETED Mission (Mission Core's normal APIs, an isolated store), listed by a REAL owner, then the new entry and the evidence library's own admission asked about the SAME held answer."""

    def setUp(self):
        super(FinishedAdmissionOnARealCompletedMission, self).setUp()
        self.complete()
        self.cached_owner = self.owner(version=2)
        self.listed, self.identity = self.listed_completed(self.cached_owner)
        self.cursor = self.identity["cursor_sha256"]
        self.head = self.journal_head()

    # -- the held answer, the entry, the existing admission ------------------------------------------------------------
    def held(self, owner=None):
        return (owner or self.cached_owner)._corroborating_answer()

    def entry(self, owner=None):
        owner = owner or self.cached_owner
        return artifact_content.historical_execution(mission_state, owner._registry, self.held(owner), owner._freshness, owner._now, self.mission_ref)

    def library(self, cursor=None):
        """The evidence library's OWN admission of the same held answer, by reading a declared deliverable."""
        return self.read(self.cached_owner, self.deliverables["result"], self.cursor if cursor is None else cursor)

    def journal_head(self):
        document = mission_store.MissionStore(self.state_dir).load()
        return journal.head_cursor(document["missions"][self.mission_id], document["mission_state"].get(self.mission_id))["journal_digest_sha256"]

    def edit_record(self, change):
        """Edit the stored record directly, then PROVE it still validates natively and its journal cursor did not move."""
        path = os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        change(document["missions"][self.mission_id])
        with open(path, "w") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
        self.assertEqual(self.journal_head(), self.head, "the journal cursor is UNCHANGED by this mutation")

    def edit_registration(self, field, value):
        with open(self.registry.path) as handle:
            document = json.load(handle)
        for source in document["sources"]:
            if source["source_id"] == self.source_id:
                source[field] = value
        with open(self.registry.path, "w") as handle:
            json.dump(document, handle)
        self.registry.load()
        self.assertEqual(self.journal_head(), self.head)

    def assert_both_refuse(self, problem):
        """The entry and the evidence library refuse the SAME held answer with the SAME code. ``problem`` is written by hand from the library's own mutation tests, never read from either side."""
        proof, charged, refusal = self.entry()
        self.assertIsNone(proof, "the entry admitted a changed record")
        self.assertEqual(refusal[0], problem, refusal)
        self.assert_refused(self.library(), problem)

    # -- the tests -----------------------------------------------------------------------------------------------------
    def test_a_real_completed_mission_is_admitted_with_a_proof_that_matches_its_listing(self):
        proof, charged, refusal = self.entry()
        self.assertIsNone(refusal, refusal)
        self.assertEqual(proof["basis"], artifact_content.EXECUTION_HISTORICAL)
        self.assertEqual((proof["source_id"], proof["native_id"]), (self.source_id, self.mission_id))
        self.assertEqual(proof["task_id"], self.listed["execution"]["task_id"])
        self.assertEqual(proof["workspace"], self.workspace)
        self.assertEqual(proof["identity"], self.identity["identity_sha256"])
        self.assertEqual(proof["journal"], self.listed["mission_state"]["report"]["provenance"]["journal_digest_sha256"])
        self.assertEqual(proof["registration"]["source_id"], self.source_id)
        self.assertGreater(charged, 0)
        # ADDED IN I2: the proof also carries the run intent facts the workspace-side witness compares the task record with, read from the REAL record's own run.
        record = mission_store.MissionStore(self.state_dir).load()["missions"][self.mission_id]
        self.assertEqual(proof["mission_id"], record["mission_id"])
        self.assertEqual(proof["intent"], dict((key, record["run"]["intent"][key]) for key in ("revision", "proposal_digest_sha256", "recorded_at")))

    def test_the_entry_and_the_evidence_librarys_own_admission_both_admit_an_unchanged_record(self):
        proof, _charged, refusal = self.entry()
        self.assertIsNone(refusal, refusal)
        answer = self.library()
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED, answer["detail"])
        self.assertEqual(answer["artifact"]["execution_basis"], artifact_content.EXECUTION_HISTORICAL)

    def test_two_calls_on_an_unchanged_record_give_equal_proofs(self):
        first, _a, refusal_a = self.entry()
        second, _b, refusal_b = self.entry()
        self.assertIsNone(refusal_a)
        self.assertIsNone(refusal_b)
        self.assertEqual(first, second, "the release-time comparison needs equal proofs for an unchanged record")

    def test_the_entry_writes_nothing(self):
        store_before = self.store_bytes()
        with open(self.registry.path, "rb") as handle:
            registry_before = handle.read()
        self.entry()
        self.assertEqual(self.store_bytes(), store_before)
        with open(self.registry.path, "rb") as handle:
            self.assertEqual(handle.read(), registry_before)

    def test_a_receipt_change_is_refused_by_the_entry_and_by_the_evidence_library(self):
        def change(mission):
            mission["run"]["receipt"]["identity_source"] = "reconciliation"
        self.edit_record(change)
        self.assert_both_refuse("historical_identity_changed")

    def test_a_prepared_binding_change_is_refused_by_the_entry_and_by_the_evidence_library(self):
        def change(mission):
            mission["run"]["workspace"]["recorded_at"] -= 1
        self.edit_record(change)
        self.assert_both_refuse("historical_identity_changed")

    def test_a_lifecycle_chain_change_is_refused_by_the_entry_and_by_the_evidence_library(self):
        def change(mission):
            mission["lifecycle"][-1]["recorded_at"] += 1
            mission["updated_at"] += 1
        self.edit_record(change)
        self.assert_both_refuse("historical_identity_changed")

    def test_a_lifecycle_state_change_is_refused_by_the_entry_and_by_the_evidence_library(self):
        """COMPLETED becomes a VALID BLOCKED record (a failed verification), the evidence journal untouched; the cached listing still says COMPLETED."""
        name, code = mission_record.VERIFY_CONJUNCTS[0]

        def change(mission):
            verification = mission["run"]["verification"]
            verification["verified"] = False
            verification["failed_conjunct"] = code
            verification["result_evidence_id"] = None
            for conjunct in verification["conjuncts"]:
                if conjunct["name"] == name:
                    conjunct["holds"] = False
            mission["lifecycle"][-1]["to_state"] = mission_record.STATE_BLOCKED
            mission["lifecycle"][-1]["reason"] = code
            mission["state"] = mission_record.STATE_BLOCKED
        self.edit_record(change)
        self.assert_both_refuse("historical_identity_changed")

    def test_a_source_registration_change_is_refused_by_the_entry_and_by_the_evidence_library(self):
        self.edit_registration("producer", "another_producer")
        self.assert_both_refuse("source_registration_changed")

    def test_a_record_that_became_blocked_and_was_listed_again_is_not_established(self):
        """The same BLOCKED record, but now the owner REFRESHES: the new listing says BLOCKED, so the entry refuses it as not established (the late-resolution case ends here too: a BLOCKED record is never admitted)."""
        name, code = mission_record.VERIFY_CONJUNCTS[0]

        def change(mission):
            verification = mission["run"]["verification"]
            verification["verified"] = False
            verification["failed_conjunct"] = code
            verification["result_evidence_id"] = None
            for conjunct in verification["conjuncts"]:
                if conjunct["name"] == name:
                    conjunct["holds"] = False
            mission["lifecycle"][-1]["to_state"] = mission_record.STATE_BLOCKED
            mission["lifecycle"][-1]["reason"] = code
            mission["state"] = mission_record.STATE_BLOCKED
        self.edit_record(change)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS         # a REAL refresh
        listed, _identity = self.listed_completed(self.cached_owner)
        self.assertEqual(listed["lifecycle"]["state"], mission_record.STATE_BLOCKED)
        proof, _charged, refusal = self.entry()
        self.assertIsNone(proof)
        self.assertEqual(refusal[0], "mission_binding_not_bound")

    def test_the_provider_adapter_feeds_the_review_branch_a_real_proof(self):
        """The shape the owner will wire in I3, built here by hand: the review branch consumes a REAL proof and carries it, whole, into its bound.

        CHANGED IN I2: the finished branch now also requires the recorded workspace's OWN task record to name this run, so the fixture's workspace is given one, built from the REAL
        record's own intent (a synthetic file in the fixture's temporary workspace: no live catalog and no real history)."""
        owner = self.cached_owner
        record = mission_store.MissionStore(self.state_dir).load()["missions"][self.mission_id]
        intent = record["run"]["intent"]
        os.makedirs(os.path.join(self.workspace, ".herd", "state"), exist_ok=True)
        with open(os.path.join(self.workspace, ".herd", "state", "task.json"), "w") as handle:
            json.dump({"id": self.listed["execution"]["task_id"], "started_at": intent["recorded_at"],
                       "description": run_association.handoff_prefix(record["mission_id"], intent["revision"], intent["proposal_digest_sha256"]) + " The rest."}, handle)

        def provider(held, mission_ref):
            return artifact_content.historical_execution(mission_state, owner._registry, held, owner._freshness, owner._now, mission_ref)
        accounting = code_review.Accounting()
        bound = code_review._corroborated(self.held(), owner._freshness, owner._now(), self.mission_ref, self.listed["execution"]["task_id"],
                                          finished=provider, accounting=accounting)
        self.assertEqual(bound["workspace"], self.workspace)
        self.assertEqual(bound["finished"]["identity"], self.identity["identity_sha256"])
        self.assertGreater(accounting.registry_bytes, 0)


class InheritedTestGuard(unittest.TestCase):
    """A subclass inherits every test method of its bases, and a module import does not prevent that. This fails if the real-fixture class would run anything but OWN_TESTS."""

    def test_the_real_fixture_class_runs_exactly_its_own_tests_and_inherits_none(self):
        names = unittest.TestLoader().getTestCaseNames(FinishedAdmissionOnARealCompletedMission)
        self.assertEqual(sorted(names), sorted(OWN_TESTS))

    def test_no_pre_existing_test_class_is_imported_into_this_module_by_name(self):
        collected = [name for name, value in sorted(vars(sys.modules[__name__]).items())
                     if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ != __name__]
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
