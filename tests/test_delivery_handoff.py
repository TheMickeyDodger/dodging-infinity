"""The read-only delivery HANDOFF facts of a Herdr task (``target_runtime.delivery_handoff``), and the contract block that carries them.

HERMETIC BY CONSTRUCTION. Every fixture is a set of PLAIN FILES in a temporary directory (a ``.git`` directory or file, ``HEAD``, ``commondir``,
``.herd/state/task.json`` and review artifacts). No git process is started and no repository exists; no delivery record is minted; no ceremony is
run; nothing is staged; no branch is changed. Each case supplies its OWN branch state and its OWN COMPLETE and APPROVE (or their absence). This
mission's real state is never read.

What it shows:
- the block CONFERS NOTHING: it has no "ready" field, it always reports the staged candidate as not determined and the workflow id as supplied by
  the human, and an empty ``blockers`` list is still not permission;
- the evidence comes from the EXISTING validation (``delivery_evidence.evaluate``) over bytes read under the catalog's bounds: the unbounded
  ``collect`` is never called, a refusal reason is the collector's own words verbatim, a bound is reported as ``unreadable`` (not ``refused``);
- the per-worktree git directory takes NO ``commondir`` hop, so two worktrees of one repository stay two identities;
- the validator recomputes ``blockers`` from the facts, so a producer cannot send fewer than its own facts imply.

Standalone: ``PYTHONPATH=$PWD python3 tests/test_delivery_handoff.py``
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from herdr import delivery_evidence  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from target_runtime import delivery_handoff as handoff  # noqa: E402
from target_runtime.workspace_origin import workspace_git_dir  # noqa: E402

TASK = "20260904-150441-159120"
OTHER_TASK = "20260101-000000-abcdef"
DETACHED_OID = "ddb73b07e93e351f88df8d4452e57c5ef25c2b15"


def write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    os.chmod(path, mode)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.counter = 0

    def dir(self, name):
        path = os.path.join(self.base, name)
        os.makedirs(path)
        return path

    def worktree(self, name, head="ref: refs/heads/feature/p1\n"):
        """A linked-worktree layout as git writes it, as plain files: ``<name>/.git`` is a FILE naming ``main/.git/worktrees/<name>``, whose
        ``commondir`` points back at ``main/.git``. Returns ``(workspace, per-worktree git dir, common git dir)``."""
        main = os.path.join(self.base, "main")
        common = os.path.join(main, ".git")
        os.makedirs(common, exist_ok=True)
        gitdir = os.path.join(common, "worktrees", name)
        os.makedirs(gitdir)
        write(os.path.join(gitdir, "commondir"), "../..\n")
        if head is not None:
            write(os.path.join(gitdir, "HEAD"), head)
        workspace = self.dir(name)
        write(os.path.join(workspace, ".git"), "gitdir: %s\n" % gitdir)
        return workspace, os.path.realpath(gitdir), os.path.realpath(common)

    def herd(self, workspace, task_id=TASK, status="COMPLETE", decision="APPROVE", token="APPROVE", review_text=None):
        """The task record and review artifacts the collector reads, in the shape of the collector's own tests."""
        reviews = os.path.join(workspace, ".herd", "state", "reviews")
        os.makedirs(reviews, exist_ok=True)
        review = os.path.join(reviews, "%s-round-02.md" % task_id)
        write(review, review_text if review_text is not None else (
            "# Reviewer round 2\n\nProtocol token: `%s`\n\n## Transcript\n\nHERD_DECISION: %s\n" % (token, token)))
        write(os.path.join(reviews, "%s-round-01.md" % task_id), "# Reviewer round 1\n\nProtocol token: `REJECT`\n")
        write(os.path.join(workspace, ".herd", "state", "task.json"), json.dumps({
            "id": task_id, "status": status, "review_rounds": 2,
            "last_review_decision": decision, "last_review_file": review}))
        return review

    def mission(self, workspace, task_id=TASK, model="he" "rdr.task/1"):
        return {"lifecycle": {"model": model, "state": "ACTIVE", "standing": "active"},
                "execution": {"task_id": task_id, "workspace_realpath": workspace}}


class WorktreeIdentityTests(Fixture):
    """CORRECTION C: the git directory is the PER-WORKTREE one. Following ``commondir`` would resolve every worktree of one repository to the
    same common directory and collapse worktree identity, which is the defect the association fix removes."""

    def test_two_worktrees_of_one_repository_are_two_identities_and_neither_is_the_common_dir(self):
        a, git_a, common = self.worktree("wt-a")
        b, git_b, _ = self.worktree("wt-b")
        self.assertEqual(workspace_git_dir(a), git_a)
        self.assertEqual(workspace_git_dir(b), git_b)
        self.assertNotEqual(git_a, git_b)
        self.assertNotEqual(workspace_git_dir(a), common)
        self.assertNotEqual(workspace_git_dir(b), common)
        self.assertEqual(os.path.basename(workspace_git_dir(a)), "wt-a")
        # the layout really DOES carry a commondir hop: it is what a wrong implementation would have followed
        self.assertTrue(os.path.isfile(os.path.join(git_a, "commondir")))

    def test_the_git_file_of_a_linked_worktree_is_read_the_guarded_way(self):
        # a symlinked `.git` file is not followed, even to a perfectly good one; a directory named .git/ is a plain clone's; neither blocks
        good, git_dir, _ = self.worktree("wt-good")
        self.assertEqual(workspace_git_dir(good), git_dir)
        linked = self.dir("wt-linked")
        os.symlink(os.path.join(good, ".git"), os.path.join(linked, ".git"))
        self.assertIsNone(workspace_git_dir(linked))
        fifo = self.dir("wt-fifo")
        os.mkfifo(os.path.join(fifo, ".git"))
        import signal

        def expired(signum, frame):
            raise AssertionError("workspace_git_dir BLOCKED on a FIFO")
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(5)
        try:
            self.assertIsNone(workspace_git_dir(fifo))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        big = self.dir("wt-big")
        write(os.path.join(big, ".git"), "gitdir: /x\n" + "#" * 9000)
        self.assertIsNone(workspace_git_dir(big))

    def test_a_plain_clone_is_its_own_git_directory_and_unreadable_ones_are_none(self):
        clone = self.dir("clone")
        os.makedirs(os.path.join(clone, ".git"))
        self.assertEqual(workspace_git_dir(clone), os.path.realpath(os.path.join(clone, ".git")))
        self.assertIsNone(workspace_git_dir(self.dir("bare")))
        self.assertIsNone(workspace_git_dir("relative/path"))
        self.assertIsNone(workspace_git_dir(None))
        broken = self.dir("broken")
        write(os.path.join(broken, ".git"), "gitdir: /nowhere/at/all\n")
        self.assertIsNone(workspace_git_dir(broken))


class BranchTests(Fixture):
    def test_named_detached_and_unreadable_heads(self):
        cases = (
            ("ref: refs/heads/feature/p1\n", "named", "refs/heads/feature/p1"),
            (DETACHED_OID + "\n", "detached", None),
            ("e" * 64 + "\n", "detached", None),
            ("ref: refs/tags/v1\n", "unreadable", None),
            ("ref: refs/heads/\n", "unreadable", None),
            ("ref: refs/heads/a\nref: refs/heads/b\n", "unreadable", None),
            ("not a head\n", "unreadable", None),
            ("", "unreadable", None),
            ("ref: refs/heads/" + "x" * 600, "unreadable", None),
        )
        for index, (head, state, ref) in enumerate(cases):
            _, git_dir, _ = self.worktree("wt-%d" % index, head=head)
            self.assertEqual(handoff.branch_of(git_dir), {"state": state, "ref": ref}, repr(head))

    def head_path(self, name):
        _, git_dir, _ = self.worktree(name, head=None)
        return git_dir, os.path.join(git_dir, "HEAD")

    def read_with_deadline(self, git_dir, seconds=5):
        """``branch_of`` under a hard deadline: a regression to an ordinary open of a FIFO would BLOCK, so the deadline turns a hang into a failure."""
        import signal

        def expired(signum, frame):
            raise AssertionError("branch_of BLOCKED (no guarded open)")
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(seconds)
        try:
            return handoff.branch_of(git_dir)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def test_a_FIFO_at_the_head_path_is_unreadable_and_never_blocks(self):
        git_dir, head = self.head_path("wt-fifo")
        os.mkfifo(head)
        self.assertEqual(self.read_with_deadline(git_dir), {"state": "unreadable", "ref": None})

    def test_a_symlinked_head_is_not_followed_even_to_a_perfectly_good_one(self):
        git_dir, head = self.head_path("wt-link")
        good = os.path.join(self.base, "elsewhere-HEAD")
        write(good, "ref: refs/heads/feature/p1\n")
        os.symlink(good, head)
        self.assertEqual(self.read_with_deadline(git_dir), {"state": "unreadable", "ref": None})

    def test_a_directory_at_the_head_path_is_unreadable(self):
        git_dir, head = self.head_path("wt-dir")
        os.mkdir(head)
        self.assertEqual(self.read_with_deadline(git_dir), {"state": "unreadable", "ref": None})

    def test_a_head_that_cannot_be_opened_is_unreadable(self):
        if os.geteuid() == 0:
            self.skipTest("a superuser opens a mode-000 file")
        git_dir, head = self.head_path("wt-denied")
        write(head, "ref: refs/heads/feature/p1\n", mode=0o000)
        self.assertEqual(self.read_with_deadline(git_dir), {"state": "unreadable", "ref": None})

    def test_a_hardlinked_head_is_unreadable(self):
        git_dir, head = self.head_path("wt-hard")
        other = os.path.join(self.base, "outside-HEAD")
        write(other, "ref: refs/heads/feature/p1\n")
        os.link(other, head)
        self.assertEqual(self.read_with_deadline(git_dir), {"state": "unreadable", "ref": None})

    def test_a_normal_head_is_unchanged_by_the_guarded_read(self):
        # the guard must not disturb the ordinary readings
        _, named, _ = self.worktree("wt-ok-named")
        _, detached, _ = self.worktree("wt-ok-detached", head=DETACHED_OID + "\n")
        self.assertEqual(self.read_with_deadline(named), {"state": "named", "ref": "refs/heads/feature/p1"})
        self.assertEqual(self.read_with_deadline(detached), {"state": "detached", "ref": None})

    def test_a_missing_head_and_no_git_dir_are_unreadable(self):
        _, git_dir, _ = self.worktree("wt-nohead", head=None)
        self.assertEqual(handoff.branch_of(git_dir)["state"], "unreadable")
        self.assertEqual(handoff.branch_of(None), {"state": "unreadable", "ref": None})


class EvidenceTests(Fixture):
    def test_the_collector_still_collects_what_it_collected_and_the_bounded_path_agrees(self):
        ws = self.dir("ws")
        review = self.herd(ws)
        document = delivery_evidence.collect(ws, now=5.0)
        self.assertEqual(document["reviewer_approve"]["round"], 2)
        self.assertEqual(document["reviewer_approve"]["review_file_name"], os.path.basename(review))
        self.assertEqual(document["reviewer_approve"]["review_file_sha256"],
                         hashlib.sha256(Path(review).read_bytes()).hexdigest())
        self.assertEqual(sorted(document), ["engineering_complete", "reviewer_approve"])
        self.assertEqual(handoff.evidence_of(ws, TASK), {"state": "collected", "reason": None})

    def test_each_refusal_is_the_collectors_own_words_verbatim_and_never_synthesized(self):
        for kwargs in ({"status": "ACTIVE"}, {"decision": "REJECT"}, {"token": "REJECT"}, {"token": "ACCEPT"}):
            ws = self.dir("ws-%d" % self.counter)
            self.counter += 1
            self.herd(ws, **kwargs)
            with self.assertRaises(delivery_evidence.EvidenceError) as raised:
                delivery_evidence.collect(ws)
            self.assertEqual(handoff.evidence_of(ws, TASK), {"state": "refused", "reason": str(raised.exception)}, kwargs)

    def test_a_route_prepared_for_task_A_and_run_after_task_B_completed_REFUSES_instead_of_substituting(self):
        # the identity hazard: the workspace now holds task B, COMPLETE and APPROVED. Collecting "whatever is current" would seal B's identity into
        # a record the person believes is A's. With the expected id bound in, the collector refuses and produces no document at all.
        ws = self.dir("ws")
        self.herd(ws, task_id=OTHER_TASK)
        self.assertEqual(delivery_evidence.collect(ws, now=5.0)["engineering_complete"]["task_id"], OTHER_TASK)  # unbound: B is happily collected
        with self.assertRaises(delivery_evidence.EvidenceError) as raised:
            delivery_evidence.collect(ws, expect_task_id=TASK)
        self.assertIn("refusing to collect evidence for a different task", str(raised.exception))
        self.assertIn(OTHER_TASK, str(raised.exception))
        self.assertIn(TASK, str(raised.exception))
        # the right task is collected as ever, and the expectation changes nothing else about the document
        ws2 = self.dir("ws2")
        self.herd(ws2, task_id=TASK)
        self.assertEqual(delivery_evidence.collect(ws2, now=5.0, expect_task_id=TASK), delivery_evidence.collect(ws2, now=5.0))

    def test_the_expectation_is_checked_first_so_a_different_task_is_never_described_as_merely_incomplete(self):
        ws = self.dir("ws")
        self.herd(ws, task_id=OTHER_TASK, status="ACTIVE")
        with self.assertRaises(delivery_evidence.EvidenceError) as raised:
            delivery_evidence.collect(ws, expect_task_id=TASK)
        self.assertIn("not the expected", str(raised.exception))
        self.assertNotIn("not COMPLETE", str(raised.exception))

    def test_herdctl_delivery_evidence_passes_the_expectation_through(self):
        # source-level pin: running the CLI resolves its repo with a real git call, which this suite never starts
        source = (REPO_ROOT / "herdctl.py").read_text()
        block = source[source.index('q = sp.add_parser("delivery-evidence")'):][:400]
        self.assertIn('"--expect-task-id"', block)
        self.assertIn("expect_task_id=getattr(args, \"expect_task_id\", None)", source)

    def test_no_record_at_all_is_unreadable_never_collected(self):
        ws = self.dir("empty")
        result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "unreadable")
        self.assertIn("task.json", result["reason"])

    def test_the_task_the_collector_would_seal_must_be_this_missions_own(self):
        ws = self.dir("ws")
        self.herd(ws, task_id=OTHER_TASK)
        result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "refused")
        self.assertIn(OTHER_TASK, result["reason"])
        self.assertIn("would not link here", result["reason"])

    def test_the_unbounded_collector_is_never_called_and_no_unbounded_read_is_made(self):
        ws = self.dir("ws")
        self.herd(ws)
        with mock.patch.object(delivery_evidence, "collect", side_effect=AssertionError("collect must not be called")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("an unbounded read")):
            self.assertEqual(handoff.evidence_of(ws, TASK)["state"], "collected")


class BoundsTests(Fixture):
    """The catalog's guarantees are kept: a bound or a link is a limit of THIS read (``unreadable``), never the collector's verdict."""

    def test_a_symlinked_task_record_is_not_read(self):
        ws = self.dir("ws")
        self.herd(ws)
        real = os.path.join(self.base, "elsewhere.json")
        Path(os.path.join(ws, ".herd", "state", "task.json")).replace(real)
        os.symlink(real, os.path.join(ws, ".herd", "state", "task.json"))
        result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "unreadable")

    def test_a_symlinked_reviews_directory_is_not_listed(self):
        ws = self.dir("ws")
        self.herd(ws)
        reviews = os.path.join(ws, ".herd", "state", "reviews")
        moved = os.path.join(self.base, "moved-reviews")
        os.rename(reviews, moved)
        os.symlink(moved, reviews)
        result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "unreadable")
        self.assertIn("directory_chain_not_plain", result["reason"])

    def test_a_review_artifact_over_the_bound_is_unreadable_not_refused(self):
        ws = self.dir("ws")
        self.herd(ws, review_text="Protocol token: `APPROVE`\n## Transcript\nHERD_DECISION: APPROVE\n" + "x" * 70000)
        result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "unreadable")
        self.assertIn("without them and may decide differently", result["reason"])

    def test_a_reviews_directory_over_the_entry_bound_is_unreadable(self):
        ws = self.dir("ws")
        self.herd(ws)
        reviews = os.path.join(ws, ".herd", "state", "reviews")
        with mock.patch.object(handoff, "MAX_REVIEW_ENTRIES", 2):
            write(os.path.join(reviews, "%s-round-03.md" % TASK), "x")
            result = handoff.evidence_of(ws, TASK)
        self.assertEqual(result["state"], "unreadable")
        self.assertIn("directory_over_entry_bound", result["reason"])


class HandoffTests(Fixture):
    def test_a_model_that_is_not_a_herdr_task_has_no_handoff(self):
        ws, _, _ = self.worktree("wt")
        block = handoff.handoff_for_mission(self.mission(ws, model="workflow_authority.workflow_phase/2"))
        self.assertEqual(block, v2.handoff_empty("not_applicable"))

    def test_a_task_with_its_own_complete_approve_and_a_named_branch_has_no_blockers_but_is_still_not_ready(self):
        ws, git_dir, _ = self.worktree("wt")
        self.herd(ws)
        block = handoff.handoff_for_mission(self.mission(ws))
        self.assertEqual(block, {
            "state": "observed", "link_basis": "record_task_worktree_binding",
            "repository": ws, "engineering_task_id": TASK,
            "git_dir_realpath": git_dir,
            "branch": {"state": "named", "ref": "refs/heads/feature/p1"},
            "evidence": {"state": "collected", "reason": None},
            "staged_candidate": "not_determined", "workflow_id": "human_supplied", "blockers": []})
        # it CONFERS NOTHING: no field says ready, may mint, or authorized
        for key in block:
            self.assertNotRegex(key, "ready|mint|authori|permit|allowed|can_")
        self.assertEqual(block["staged_candidate"], "not_determined")

    def test_a_detached_head_with_no_complete_or_approve_is_blocked_for_its_real_reasons(self):
        # the shape of THIS mission's own situation, built entirely from the fixture's own state
        ws, _, _ = self.worktree("wt", head=DETACHED_OID + "\n")
        self.herd(ws, status="ACTIVE", decision="REJECT", token="REJECT")
        block = handoff.handoff_for_mission(self.mission(ws))
        self.assertEqual(block["branch"], {"state": "detached", "ref": None})
        self.assertEqual(block["evidence"]["state"], "refused")
        self.assertIn("not COMPLETE", block["evidence"]["reason"])
        self.assertEqual(block["blockers"], ["head_detached", "evidence_not_collected"])
        self.assertEqual(block["staged_candidate"], "not_determined")

    def test_no_git_directory_no_task_and_an_unreadable_workspace(self):
        ws = self.dir("plain")
        self.herd(ws)
        block = handoff.handoff_for_mission(self.mission(ws))
        self.assertIsNone(block["git_dir_realpath"])
        self.assertEqual(block["blockers"], ["git_dir_not_read", "head_not_read"])
        ws2, _, _ = self.worktree("wt2")
        no_task = handoff.handoff_for_mission(self.mission(ws2, task_id=None))
        self.assertIsNone(no_task["engineering_task_id"])
        self.assertIn("task_id_not_recorded", no_task["blockers"])
        self.assertEqual(handoff.handoff_for_mission(self.mission("relative")), v2.handoff_empty("not_reported"))

    def test_the_block_reads_and_writes_nothing_else(self):
        ws, _, _ = self.worktree("wt")
        self.herd(ws)
        def listing():
            return sorted((root, sorted(names)) for root, _dirs, names in os.walk(self.base))
        before = listing()
        stamps = sorted((p, os.stat(p).st_mtime_ns) for p in (str(q) for q in Path(self.base).rglob("*") if q.is_file()))
        handoff.handoff_for_mission(self.mission(ws))
        self.assertEqual(listing(), before)
        self.assertEqual(sorted((p, os.stat(p).st_mtime_ns) for p in (str(q) for q in Path(self.base).rglob("*") if q.is_file())), stamps)


class ContractTests(Fixture):
    def lifecycle(self, model="he" "rdr.task/1"):
        return {"model": model, "state": "ACTIVE", "standing": "active"}

    def observed(self):
        ws, _, _ = self.worktree("wt")
        self.herd(ws)
        return handoff.handoff_for_mission(self.mission(ws))

    def test_the_vocabulary_is_the_same_on_both_sides(self):
        self.assertEqual(v2.HANDOFF_KEYS, tuple(handoff.empty("observed")))
        self.assertEqual(v2.HANDOFF_BLOCKERS, handoff.BLOCKERS)
        self.assertEqual(v2.HANDOFF_STATES, (handoff.STATE_OBSERVED, handoff.STATE_NOT_APPLICABLE, handoff.STATE_NOT_REPORTED))
        self.assertEqual(v2.HANDOFF_BRANCH_STATES, (handoff.BRANCH_NAMED, handoff.BRANCH_DETACHED, handoff.BRANCH_UNREADABLE))
        self.assertEqual(v2.HANDOFF_EVIDENCE_STATES, (handoff.EVIDENCE_COLLECTED, handoff.EVIDENCE_REFUSED, handoff.EVIDENCE_UNREADABLE))
        self.assertEqual(v2.HANDOFF_STAGED_NOT_DETERMINED, handoff.STAGED_NOT_DETERMINED)
        self.assertEqual(v2.HANDOFF_WORKFLOW_ID_HUMAN_SUPPLIED, handoff.WORKFLOW_ID_HUMAN_SUPPLIED)
        self.assertEqual(v2.MAX_HANDOFF_REASON_CHARS, handoff.MAX_REASON_CHARS)
        # what is DECLARED is what is LINKED BY: one constant, the contract's own
        self.assertEqual(handoff.LINK_BASIS, v2.MISSION_LINK_BASIS_TASK_WORKTREE)
        self.assertEqual(handoff.LINK_BASIS, "record_task_worktree_binding")
        for blockers in ((), ("git_dir_not_read",), ("head_detached", "evidence_not_collected")):
            for branch in ("named", "detached", "unreadable"):
                for evidence in ("collected", "refused", "unreadable"):
                    for git_dir in ("/g", None):
                        for task_id in ("t", None):
                            self.assertEqual(
                                v2.expected_handoff_blockers(task_id, git_dir, branch, evidence),
                                handoff.expected_blockers(task_id, git_dir, {"state": branch}, {"state": evidence}))
        self.assertIn("handoff", " ".join(v2.MISSION_KEYS_V2) + "handoff")
        self.assertIn("delivery_handoff", v2.MISSION_KEYS_V2)

    def test_the_producers_own_blocks_validate(self):
        block = self.observed()
        v2._validate_delivery_handoff(block, self.lifecycle(), "m.delivery_handoff")
        v2._validate_delivery_handoff(v2.handoff_empty("not_reported"), self.lifecycle(), "m")
        v2._validate_delivery_handoff(v2.handoff_empty("not_applicable"), self.lifecycle("mission_core.mission_state/1"), "m")

    def test_the_validator_refuses_what_breaks_the_rule(self):
        good = self.observed()
        def broken(**changes):
            value = copy.deepcopy(good)
            value.update(changes)
            return value
        wrong_blockers = broken(blockers=["head_detached"])
        detached = copy.deepcopy(good)
        detached["branch"] = {"state": "detached", "ref": None}
        refused = (
            ("an extra key", dict(good, ready=True)),
            ("a missing key", {k: v for k, v in good.items() if k != "blockers"}),
            ("a staged candidate said to be satisfied", broken(staged_candidate="staged")),
            ("a workflow id guessed", broken(workflow_id="wf-0001")),
            ("blockers the facts do not imply", wrong_blockers),
            ("FEWER blockers than a detached head implies", dict(detached, blockers=[])),
            ("a named branch with no ref", broken(branch={"state": "named", "ref": None})),
            ("a ref that is not a branch", broken(branch={"state": "named", "ref": "refs/tags/v1"})),
            ("collected evidence with a reason", broken(evidence={"state": "collected", "reason": "ok"})),
            ("refused evidence with no reason", broken(evidence={"state": "refused", "reason": None})),
            ("an undeclared state", broken(state="ready")),
            ("NO declared link basis (silence is not a declaration)", broken(link_basis=None)),
            ("the older origin-only basis declared", broken(link_basis="record_repository_binding")),
            ("an unrecognised basis declared", broken(link_basis="record_canonical_maybe")),
            ("an observed block that carries no repository", broken(repository=None)),
            ("a not_reported block that carries facts", dict(good, state="not_reported")),
        )
        for name, value in refused:
            with self.assertRaises(contract.ContractError, msg=name):
                v2._validate_delivery_handoff(value, self.lifecycle(), "m.delivery_handoff")
        # not_applicable only for a model without a handoff, and the reverse
        with self.assertRaises(contract.ContractError):
            v2._validate_delivery_handoff(v2.handoff_empty("not_applicable"), self.lifecycle(), "m")
        with self.assertRaises(contract.ContractError):
            v2._validate_delivery_handoff(good, self.lifecycle("mission_core.mission_state/1"), "m")
        with self.assertRaises(contract.ContractError):
            v2._validate_delivery_handoff(v2.handoff_empty("not_reported"), self.lifecycle("mission_core.mission_state/1"), "m")

    def test_a_missing_reader_or_a_failing_one_is_not_reported_never_none(self):
        herdr = self.mission(self.dir("w"))
        self.assertEqual(v2.mission_delivery_handoff(herdr, None), v2.handoff_empty("not_reported"))
        def failing(mission):
            raise RuntimeError("reader failed")
        self.assertEqual(v2.mission_delivery_handoff(herdr, failing), v2.handoff_empty("not_reported"))
        other = self.mission(self.dir("w2"), model="mission_core.mission_state/1")
        self.assertEqual(v2.mission_delivery_handoff(other, failing), v2.handoff_empty("not_applicable"))


if __name__ == "__main__":
    unittest.main()
