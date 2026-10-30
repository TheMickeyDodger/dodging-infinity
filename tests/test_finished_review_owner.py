"""I3, THE OWNER: a REAL COMPLETED Mission asked about through a REAL version 2 owner (``CatalogService.code_review``) with a STUB git boundary.

What it reaches that the hermetic modules cannot: the owner's own wiring of the finished-execution provider (the record-side admission of the evidence library, fed the SAME held answer), the
workspace-side witness on a workspace the fixture really holds, and the closed codes the OWNER's answer carries. The git boundary is a stub that names a DIFFERENT root, so a read that gets past
corroboration ends at the first git read as ``workspace_is_not_a_worktree_root``: no git binary, no worktree, no network. It does NOT show an overview for a finished Mission against a real git
workspace: that is not covered here.

It subclasses test_artifact_content.CompletedFixture through a MODULE import; no pre-existing test class is imported by name, and ``InheritedTestGuard`` fails the module if the subclass inherits
any test method beyond the ones listed here.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_artifact_content as ac  # noqa: E402  (a MODULE import: no pre-existing test class is imported by name)
from mission import record as mission_record  # noqa: E402
from mission import store as mission_store  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import run_association  # noqa: E402

#: The ONLY test methods the real-fixture class may run. An inherited test method, or a renamed one, makes InheritedTestGuard fail the module.
OWN_TESTS = [
    "test_a_blocked_mission_is_refused_through_the_owner_as_not_established",
    "test_a_completed_mission_whose_workspace_names_its_run_reaches_the_git_stage_through_the_owner",
    "test_a_task_record_naming_another_task_is_refused_through_the_owner_and_git_is_never_asked",
    "test_a_version_1_owner_still_refuses_before_any_provider",
    "test_the_review_writes_nothing_to_the_workspace",
    "test_without_the_workspaces_own_task_record_the_owner_refuses_as_not_corroborated_and_git_is_never_asked",
]


class StubGit(object):
    """A git boundary that names ANOTHER root: the first live read refuses the workspace as not a worktree root. Every call is recorded."""

    def __init__(self):
        self.calls = []

    def live_toplevel(self, workspace):
        self.calls.append(workspace)
        return workspace + "-not-the-root"


class FinishedReviewThroughTheOwner(ac.CompletedFixture):
    """A REAL COMPLETED Mission (Mission Core's normal APIs, an isolated store), listed by a REAL version 2 owner, then reviewed through that owner."""

    def setUp(self):
        super(FinishedReviewThroughTheOwner, self).setUp()
        self.complete()
        self.cached_owner = self.owner(version=2)
        self.listed, self.identity = self.listed_completed(self.cached_owner)
        self.task = self.listed["execution"]["task_id"]
        self.git = StubGit()
        self.cached_owner._git_transport = self.git

    def intent(self):
        return mission_store.MissionStore(self.state_dir).load()["missions"][self.mission_id]["run"]["intent"]

    def write_task_record(self, task_id=None, description=None):
        """The recorded workspace's OWN task record (a synthetic file in the fixture's temporary workspace), built from the REAL record's own intent unless a field is overridden."""
        intent = self.intent()
        os.makedirs(os.path.join(self.workspace, ".herd", "state"), exist_ok=True)
        text = description if description is not None else run_association.handoff_prefix(
            self.mission_id, intent["revision"], intent["proposal_digest_sha256"]) + " The rest."
        with open(os.path.join(self.workspace, ".herd", "state", "task.json"), "w") as handle:
            json.dump({"id": self.task if task_id is None else task_id, "started_at": intent["recorded_at"], "description": text}, handle)

    def ask(self, owner=None):
        return (owner or self.cached_owner).code_review(self.mission_ref, self.task)

    def tree(self):
        found = {}
        for directory, _names, files in os.walk(self.workspace):
            for name in files:
                path = os.path.join(directory, name)
                status = os.lstat(path)
                found[path] = (status.st_size, status.st_mtime_ns)
        return found

    def test_a_completed_mission_whose_workspace_names_its_run_reaches_the_git_stage_through_the_owner(self):
        self.write_task_record()
        answer = self.ask()
        self.assertEqual((answer["state"], answer["problem"]), ("refused", code_review.PROBLEM_NOT_WORKTREE_ROOT), answer["detail"])
        self.assertEqual(self.git.calls, [self.workspace], "corroboration passed, so the first git read was made, once")
        self.assertEqual(answer["accounting"]["git_commands"], 1)
        self.assertGreater(answer["accounting"]["registry_bytes"], 0, "the record-side admission's bytes are charged")
        self.assertGreater(answer["accounting"]["untracked_bytes"], 0, "the workspace witness's read is charged")

    def test_without_the_workspaces_own_task_record_the_owner_refuses_as_not_corroborated_and_git_is_never_asked(self):
        answer = self.ask()
        self.assertEqual((answer["state"], answer["problem"]), ("refused", code_review.FINISHED_WORKSPACE_NOT_CORROBORATED), answer["detail"])
        self.assertEqual(self.git.calls, [])

    def test_a_task_record_naming_another_task_is_refused_through_the_owner_and_git_is_never_asked(self):
        self.write_task_record(task_id=self.task + "-other")
        answer = self.ask()
        self.assertEqual((answer["state"], answer["problem"]), ("refused", code_review.FINISHED_WORKSPACE_NOT_CORROBORATED), answer["detail"])
        self.assertEqual(self.git.calls, [])

    def test_a_blocked_mission_is_refused_through_the_owner_as_not_established(self):
        """The record-side refusal crosses the owner as a CLOSED code: the same BLOCKED edit the Part A module makes, the owner REFRESHED, then the review asked."""
        self.write_task_record()
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
        path = os.path.join(self.state_dir, mission_store.MISSIONS_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        change(document["missions"][self.mission_id])
        with open(path, "w") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS         # a REAL refresh
        listed, _identity = self.listed_completed(self.cached_owner)
        self.assertEqual(listed["lifecycle"]["state"], mission_record.STATE_BLOCKED)
        answer = self.ask()
        self.assertEqual((answer["state"], answer["problem"]), ("refused", code_review.FINISHED_NOT_ESTABLISHED), answer["detail"])
        self.assertEqual(self.git.calls, [])

    def test_a_version_1_owner_still_refuses_before_any_provider(self):
        owner = self.owner()
        answer = owner.code_review(self.mission_ref, self.task)
        self.assertEqual((answer["state"], answer["problem"]), ("refused", code_review.PROBLEM_CONTRACT_VERSION))
        self.assertEqual(self.git.calls, [])

    def test_the_review_writes_nothing_to_the_workspace(self):
        self.write_task_record()
        before = self.tree()
        self.ask()
        self.assertEqual(self.tree(), before)


class TypeScriptListParityTests(unittest.TestCase):
    """The office (TypeScript) re-declares the backend's closed problem vocabulary between two markers: the same ordered list, read from its own source, so the two cannot drift. (The pre-existing
    OfficeParityTests make the same check; this module repeats it because the four finished codes enter both lists TOGETHER in I3 and this is the form the contained route runs.)"""

    def test_the_office_list_between_the_markers_is_the_backends_closed_list_in_order(self):
        text = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "src" / "di" / "codeReview.ts").read_text()
        block = text.split("// BEGIN CODE_REVIEW_PROBLEMS", 1)[1].split("// END CODE_REVIEW_PROBLEMS", 1)[0]
        self.assertEqual(re.findall(r"'([a-z0-9_]+)',", block), list(code_review.PROBLEMS))


class InheritedTestGuard(unittest.TestCase):
    """A subclass inherits every test method of its bases, and a module import does not prevent that. This fails if the real-fixture class would run anything but OWN_TESTS."""

    def test_the_real_fixture_class_runs_exactly_its_own_tests_and_inherits_none(self):
        names = unittest.TestLoader().getTestCaseNames(FinishedReviewThroughTheOwner)
        self.assertEqual(sorted(names), sorted(OWN_TESTS))

    def test_no_pre_existing_test_class_is_imported_into_this_module_by_name(self):
        collected = [name for name, value in sorted(vars(sys.modules[__name__]).items())
                     if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ != __name__]
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
