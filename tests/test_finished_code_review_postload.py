"""I1, PART A, POST-LOAD CHECKS: three checks of the record-side admission entry (artifact_content.historical_execution) that no test of mine exercised.

REVISION 2 OF THIS MODULE (the first revision, with two tests, ran at sha256 a17e7450179fd5440d445d6a4f014815aa78b0f4357f42489ca8ed28f38b97d9; its results belong to those bytes). It exists because the Part A mutant
run (logs/partA-mutants-20261028T084537Z.log) let PA3, PA4 and PA5 SURVIVE: removing the entry's journal-moved check, its one-resolved-root check, or its execution recomputation turned no test red. Each test below is the
SPECIFICATION of one surviving mutant and is meant to go red against it (the mutant table is scratch/i1/mutants_partA.py: PA3, PA4, PA5). The first two build states the product can reach. THE THIRD (PA5) DOES NOT: it exercises
what the consumer is SPECIFIED TO JUDGE, an input the producer does not emit through Mission Core's operations, and it says so in its own docstring.

Same shape as Part A (tests/test_finished_code_review_real.py, whose digest was quoted for review and is not edited): a subclass of the same REAL COMPLETED Mission fixture (Mission Core's normal APIs, an isolated
store, listed by a real owner), reached through a MODULE import of the existing test module so that no pre-existing test class is imported by name, and a guard (``InheritedTestGuard``) that fails the module if
the subclass would run any method beyond OWN_TESTS or if a pre-existing TestCase class appears here. NO git, no worktree, no browser, no network, nothing outside the fixture's temporary root.

Each test asserts, FIRST, that the unchanged state is admitted (so the refusal that follows is caused by the change and by nothing else), then that the entry and the evidence library's own admission refuse the
SAME state with the SAME code. The codes are written as literals so that a changed constant is red; they are the closed vocabulary's values for these refusals.

HOW THE STATE IS MADE (said plainly, because the Lead preferred the fixture's own mechanism):
  - A MOVED JOURNAL HEAD. The fixture's own mechanism (``move_the_head``, which opens a blocker through Mission Core) cannot move the head of a COMPLETED Mission: a terminal Mission takes no such write (the
    state service's terminal guard); the one operation allowed on it, the delivery receipt attestation, needs a stored delivery authorization the fixture does not have. A hand edit of the state record would mean
    forging a valid operation chain. So the HELD ANSWER is copied and ONE listed field is changed: the journal digest the listing carries. The record is untouched. The comparison under test is the record's head
    NOW against the head the listing names; a listing that names a head the record no longer has is the state "the head moved since it was listed", and this is the technique this module's base tests already use for
    listings (a deep copy of the owner's answer, patched in as its corroborating answer).
  - A WORKSPACE THAT IS NO LONGER ONE RESOLVED ROOT. After listing, the fixture's workspace directory is renamed and a link is made at its old path, both inside the fixture's temporary root (the launcher's run
    root), as the audited symlink and hardlink work of tests/test_artifact_content.py does. Nothing is made outside that root.
"""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_artifact_content as ac  # noqa: E402  (a MODULE import: no pre-existing test class is imported by name)
from mission import journal  # noqa: E402
from mission import store as mission_store  # noqa: E402
from target_runtime import artifact_content  # noqa: E402
from target_runtime import mission_state  # noqa: E402

#: The ONLY test methods the real-fixture class may run. An inherited test method, or a renamed one, makes InheritedTestGuard fail the module.
OWN_TESTS = [
    "test_a_journal_head_the_record_no_longer_has_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_workspace_that_is_no_longer_one_resolved_root_is_refused_by_the_entry_and_by_the_evidence_library",
    "test_a_listing_whose_task_is_not_the_one_the_record_names_is_refused_by_the_entry_and_by_the_evidence_library",
]

#: The closed vocabulary's values for the two refusals, as literals (a changed constant is red).
CODE_JOURNAL_MOVED = "mission_moved_since_listing"
CODE_WORKSPACE_UNRESOLVED = "workspace_not_one_resolved_root"
CODE_TASK_OR_WORKSPACE_CHANGED = "mission_task_or_workspace_changed"


class PostLoadChecksOnARealCompletedMission(ac.CompletedFixture):
    """A REAL COMPLETED Mission, listed by a REAL owner; then the entry and the evidence library's own admission asked about the SAME state."""

    def setUp(self):
        super(PostLoadChecksOnARealCompletedMission, self).setUp()
        self.complete()
        self.cached_owner = self.owner(version=2)
        self.listed, self.identity = self.listed_completed(self.cached_owner)
        self.cursor = self.identity["cursor_sha256"]
        self.head = self.journal_head()

    def held(self):
        return self.cached_owner._corroborating_answer()

    def entry(self, answer=None):
        owner = self.cached_owner
        return artifact_content.historical_execution(mission_state, owner._registry, answer if answer is not None else self.held(), owner._freshness, owner._now, self.mission_ref)

    def library(self):
        """The evidence library's OWN admission of the held answer, by reading a declared deliverable."""
        return self.read(self.cached_owner, self.deliverables["result"], self.cursor)

    def journal_head(self):
        document = mission_store.MissionStore(self.state_dir).load()
        return journal.head_cursor(document["missions"][self.mission_id], document["mission_state"].get(self.mission_id))["journal_digest_sha256"]

    def assert_unchanged_state_is_admitted(self):
        """The control inside each test: before the change, the entry admits and the library admits."""
        proof, _charged, refusal = self.entry()
        self.assertIsNone(refusal, refusal)
        self.assertIsNotNone(proof)
        answer = self.library()
        self.assertEqual(answer["state"], artifact_content.STATE_ADMITTED, answer["detail"])

    # -- the tests -----------------------------------------------------------------------------------------------------
    def test_a_journal_head_the_record_no_longer_has_is_refused_by_the_entry_and_by_the_evidence_library(self):
        """Specification of mutant PA3 (the entry skips the journal-moved check).

        THE STATE THIS TEST BUILDS IS PRODUCT-REACHABLE; ONLY THE ROUTE TO IT IS A SHORTCUT. "The record's head is not the head the listing names" is a state the product reaches: the head is derived from
        the Mission state record's applied-operation chain, and the one write a COMPLETED Mission still takes, a delivery receipt attestation, extends that chain after a listing was made. This test cannot
        attest a receipt (the fixture has no delivery authorization), so it edits the LISTING instead and leaves the record alone; the comparison under test is the same one. That is a shortcut to a real
        state, and it is NOT a fabricated state. Contrast the entry's execution recomputation (mutant PA5), tested further down: that test supplies a listing the producer does not emit, as an INPUT THE
        BOUNDARY IS SPECIFIED TO JUDGE, and does not claim the state is reachable through the product."""
        self.assert_unchanged_state_is_admitted()
        listed_head = self.listed["mission_state"]["report"]["provenance"]["journal_digest_sha256"]
        self.assertEqual(listed_head, self.head, "precondition: the listing names the record's real head")
        doctored = copy.deepcopy(self.held())
        row = [m for m in doctored["response"]["missions"] if m["mission_ref"] == self.mission_ref]
        self.assertEqual(len(row), 1)
        provenance = row[0]["mission_state"]["report"]["provenance"]
        other = ("0" if listed_head[0] != "0" else "1") + listed_head[1:]
        provenance["journal_digest_sha256"] = other
        self.assertNotEqual(other, self.journal_head(), "precondition: the listing now names a head the record does not have")
        proof, _charged, refusal = self.entry(doctored)
        self.assertIsNone(proof, "the entry admitted a record whose head is not the listed one")
        self.assertEqual(refusal[0], CODE_JOURNAL_MOVED, refusal)
        with mock.patch.object(self.cached_owner, "_corroborating_answer", lambda: doctored):
            self.assert_refused(self.library(), CODE_JOURNAL_MOVED)

    def test_a_workspace_that_is_no_longer_one_resolved_root_is_refused_by_the_entry_and_by_the_evidence_library(self):
        """Specification of mutant PA4 (the entry skips the one-resolved-root check)."""
        self.assert_unchanged_state_is_admitted()
        self.assertEqual(os.path.realpath(self.workspace), self.workspace, "precondition: the workspace is one resolved root")
        moved = self.workspace + "-moved"
        os.rename(self.workspace, moved)
        os.symlink(moved, self.workspace)
        self.assertNotEqual(os.path.realpath(self.workspace), self.workspace, "the change took effect: the recorded path now resolves elsewhere")
        proof, _charged, refusal = self.entry()
        self.assertIsNone(proof, "the entry admitted a workspace that is not one resolved root")
        self.assertEqual(refusal[0], CODE_WORKSPACE_UNRESOLVED, refusal)
        self.assert_refused(self.library(), CODE_WORKSPACE_UNRESOLVED)

    def test_a_listing_whose_task_is_not_the_one_the_record_names_is_refused_by_the_entry_and_by_the_evidence_library(self):
        """Specification of mutant PA5 (the entry skips the execution recomputation).

        WHAT THIS TEST EXERCISES: WHAT THE CONSUMER IS SPECIFIED TO JUDGE, NOT A STATE THE PRODUCT PRODUCES. The held answer is, by its producer's own words, "the last refreshed answer, as held (judged by the
        caller)" (target_runtime/catalog.py), and the boundary's contract lists "its execution task and workspace are the corroborated ones" as a requirement of the record re-read (module docstring of
        target_runtime/artifact_content.py). So the boundary's input domain includes a listing the producer never emits. This test hands both the entry and the library such a listing, a copy whose listed
        task is not the one the record behind its own identity digest names, and requires both to refuse it. IT DOES NOT CLAIM THAT STATE IS REACHABLE THROUGH THE PRODUCT: under Mission Core's operations the
        fields the listing is built from are written once and before RUNNING, so the producer cannot emit this disagreement. THIS IS THE FIRST TEST OF THE RECOMPUTATION ON THE LIBRARY SIDE.

        WHAT THIS TEST DOES NOT CLOSE: whether the producer ever PAIRS a row with the wrong record (the composer attaches the identity from its own second load of the store) is a different question, and it is
        UNEXAMINED. A green result here says what the consumer judges, not that the producer was checked."""
        self.assert_unchanged_state_is_admitted()
        doctored = copy.deepcopy(self.held())
        row = [m for m in doctored["response"]["missions"] if m["mission_ref"] == self.mission_ref]
        self.assertEqual(len(row), 1)
        listed_task = row[0]["execution"]["task_id"]
        self.assertEqual(listed_task, self.listed["execution"]["task_id"], "precondition: the listing names the record's real task")
        row[0]["execution"]["task_id"] = listed_task + "-listed-but-not-recorded"
        proof, _charged, refusal = self.entry(doctored)
        self.assertIsNone(proof, "the entry admitted a listing whose task is not the one the record names")
        self.assertEqual(refusal[0], CODE_TASK_OR_WORKSPACE_CHANGED, refusal)
        with mock.patch.object(self.cached_owner, "_corroborating_answer", lambda: doctored):
            self.assert_refused(self.library(), CODE_TASK_OR_WORKSPACE_CHANGED)


class InheritedTestGuard(unittest.TestCase):
    """A subclass inherits every test method of its bases, and a module import does not prevent that. This fails if the real-fixture class would run anything but OWN_TESTS."""

    def test_the_real_fixture_class_runs_exactly_its_own_tests_and_inherits_none(self):
        names = unittest.TestLoader().getTestCaseNames(PostLoadChecksOnARealCompletedMission)
        self.assertEqual(sorted(names), sorted(OWN_TESTS))

    def test_no_pre_existing_test_class_is_imported_into_this_module_by_name(self):
        collected = [name for name, value in sorted(vars(sys.modules[__name__]).items())
                     if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ != __name__]
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
