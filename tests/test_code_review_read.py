"""Item 3 (original 20) — ``code_review.read`` over a real workspace.

The overview and every file's diff exactly; the eligibility refusals; MOVEMENT
(the normal case): a workspace that changes under a read, a snapshot that is no
longer current, a registration repointed or removed, an assignment replaced;
the bounds, each with its explicit refusal or flag; the policy exclusions (git
and the file reader are never ASKED for a withheld path); and that nothing is
written. The git-state non-mutation bar is ``test_code_review_git.py``; the
real owner and the route are ``test_code_review_owner.py``.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import git_transport  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import state_artifact  # noqa: E402
import test_code_review_git as gitfx  # noqa: E402
from _hermetic_git import run_git_completed  # noqa: E402
from test_code_review import (  # noqa: E402
    BUSY_PATHS, MISSION_REF, NOW, OTHER_TASK, SOURCE_ID, TASK, Answer,
    ReviewCase)
import test_code_review as review_tests  # noqa: E402
from test_mission_sources import Bounded  # noqa: E402


def fixture_git(repo, *args, check=True):
    """``repo.git(...)`` for the commands that need an identity (commit, stash, merge): through the hermetic helper, with the
    fixture's environment and BYTES output, exactly what ``repo.git`` returned."""
    return run_git_completed(["-C", repo.path] + list(args), check=check,
                             env=gitfx.fixture_git_env(), text=False)


class Hooked(object):
    """The real transport, with an ACTION run after the Nth call of a verb:
    what a working agent does to the workspace while a read is in progress."""

    def __init__(self, real, verb, action, nth=1, every=False):
        self.real, self.verb, self.action = real, verb, action
        self.nth, self.every, self.calls = nth, every, {}

    def __getattr__(self, name):
        target = getattr(self.real, name)
        if not callable(target):
            return target

        def call(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            if name == self.verb and (self.every or (
                    self.calls[name] == self.nth)):
                self.action()
            return target(*args, **kwargs)
        return call


class Recording(object):
    """The real transport, recording every call and its arguments."""

    def __init__(self, real):
        self.real, self.log = real, []

    def __getattr__(self, name):
        target = getattr(self.real, name)

        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return target(*args, **kwargs)
        return call

    def asked(self, verb):
        return [entry for entry in self.log if entry[0] == verb]


class CountingOs(object):
    """``os`` with ``read`` counted: the bytes a reader ACTUALLY pulled from
    its descriptors (what the I/O budget is about), not what was charged."""

    def __init__(self):
        self.bytes = 0

    def __getattr__(self, name):
        return getattr(os, name)

    def read(self, descriptor, size):
        data = os.read(descriptor, size)
        self.bytes += len(data)
        return data


def worktree_digest(root):
    """Every non-git file in the workspace, bytes and mtime."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            with open(path, "rb") as handle:
                body = hashlib.sha256(handle.read()).hexdigest()
            entries.append((os.path.relpath(path, root), st.st_size,
                            st.st_mtime_ns, body))
    return hashlib.sha256(repr(entries).encode()).hexdigest()


class OverviewTests(ReviewCase):

    EXPECTED = {
        ".env": ("untracked", "untracked", "withheld", "private_location",
                 None, None, None),
        ".herd/state.json": ("untracked", "untracked", "withheld",
                             "private_location", None, None, None),
        "bin/blob.bin": ("modified", "unstaged", "binary", None, None, None,
                         None),
        "crlf.txt": ("modified", "unstaged", "text", None, 1, 0, None),
        "docs/gone.md": ("deleted", "staged", "text", None, 0, 1, None),
        "empty.txt": ("added", "staged", "empty", None, 0, 0, None),
        "notes/new.txt": ("untracked", "untracked", "text", None, 2, 0, None),
        "src/app.py": ("modified", "both", "text", None, 1, 1, None),
        "src/moved.py": ("renamed", "both", "text", None, 1, 0,
                         "src/mover.py"),
        "src/util.py": ("modified", "unstaged", "text", None, 2, 1, None),
    }

    def test_the_overview_lists_every_changed_path_exactly(self):
        reply = self.overview()
        got = dict((f["path"], (f["change"], f["staging"], f["state"],
                                f["reason"], f["additions"], f["deletions"],
                                f["old_path"]))
                   for f in reply["overview"]["files"])
        self.assertEqual(got, self.EXPECTED)
        self.assertEqual(len(reply["overview"]["files"]), len(self.EXPECTED))
        # every busy path is listed, the rename once (under its new name)
        self.assertEqual(set(got), set(BUSY_PATHS) - {"src/mover.py"})
        for entry in reply["overview"]["files"]:
            self.assertEqual(set(entry), set(code_review.OVERVIEW_FILE_KEYS))
            self.assertRegex(entry["file_id"], r"^rf-[0-9a-f]{32}$")
        self.assertEqual(reply["overview"]["totals"], {
            "files": 10, "additions": 7, "deletions": 3, "withheld": 2,
            "unshown": 4})
        self.assertEqual(set(reply["overview"]), set(code_review.OVERVIEW_KEYS))

    def test_a_file_only_touched_is_not_a_change(self):
        self.assertNotIn("touched.txt", self.files())

    def test_the_target_comparison_and_provenance_are_stated(self):
        reply = self.overview()
        head = self.repo_.git("rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual(reply["target"], {
            "source_id": SOURCE_ID,
            "worktree": os.path.join(os.path.basename(self.root), "ws"),
            "head": head, "branch": "main", "detached": False,
            "attribution": "shared_worktree"})
        self.assertEqual(set(reply["target"]), set(code_review.TARGET_KEYS))
        self.assertEqual(reply["comparison"], {
            "mode": "head_to_worktree",
            "base": {"kind": "head_commit", "oid": head},
            "candidate": {"kind": "working_tree"},
            "covers": ["staged", "unstaged", "untracked",
                       "submodule_pointers"],
            "does_not_cover": ["ignored", "submodule_contents",
                               "binary_content", "files_over_bound",
                               "private_locations"],
            "filters_not_run": []})
        self.assertEqual(set(reply["comparison"]),
                         set(code_review.COMPARISON_KEYS))
        self.assertRegex(reply["snapshot"], r"^[0-9a-f]{64}$")
        self.assertEqual((reply["view"], reply["mission_ref"], reply["task_id"],
                          reply["file_id"]),
                         ("overview", MISSION_REF, TASK, None))
        self.assertIsInstance(reply["observed_at"], int)
        account = reply["accounting"]
        self.assertGreater(account["git_commands"], 0)
        self.assertEqual(account["untracked_bytes"], len("first\nsecond\n"),
                         "only the one untracked text file was read")
        self.assertGreater(account["registry_bytes"], 0)

    def test_a_shared_worktree_is_never_attributed_to_an_agent(self):
        reply = self.overview()
        self.assertEqual(reply["target"]["attribution"], "shared_worktree")
        text = json.dumps(reply)
        for word in ("author", "committer", "blame"):
            self.assertNotIn(word, text)

    def test_a_detached_head_is_reported_as_detached(self):
        self.repo_.git("checkout", "-q", "--detach")
        reply = self.overview()
        self.assertEqual((reply["target"]["branch"], reply["target"]["detached"]),
                         (None, True))

    def test_a_clean_workspace_is_an_empty_listing_not_a_refusal(self):
        clean = self.repo("clean")
        clean.write("a.txt", "a\n")
        clean.stage("a.txt")
        clean.save("base")
        self.answer.mission["execution"]["workspace_realpath"] = clean.path
        reply = self.overview()
        self.assertEqual(reply["overview"]["files"], [])
        self.assertEqual(reply["overview"]["totals"], {
            "files": 0, "additions": 0, "deletions": 0, "withheld": 0,
            "unshown": 0})

    def test_the_overview_is_deterministic_while_nothing_moves(self):
        first, second = self.overview(), self.overview()
        self.assertEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(first["overview"], second["overview"])

    def test_an_index_rewrite_alone_does_not_move_the_snapshot(self):
        # the agent's own `git status` refreshes the index; the comparison
        # (HEAD -> working tree) and what is staged are unchanged
        first = self.overview()["snapshot"]
        self.repo_.git("status", "--porcelain")
        self.assertEqual(self.overview()["snapshot"], first)

    def test_staging_a_change_moves_it(self):
        first = self.overview()["snapshot"]
        self.repo_.stage("src/util.py")
        self.assertNotEqual(self.overview()["snapshot"], first)

    def test_editing_a_listed_file_moves_it(self):
        first = self.overview()["snapshot"]
        self.repo_.write("src/util.py", "A = 1\nB = 21\nC = 3\nD = 4\n")
        self.assertNotEqual(self.overview()["snapshot"], first)

    def test_a_new_head_moves_it(self):
        first = self.overview()["snapshot"]
        self.repo_.git("add", "-A", ":!.env", ":!.herd")
        self.repo_.save("work")
        reply = self.overview()
        self.assertNotEqual(reply["snapshot"], first)
        self.assertEqual(reply["overview"]["totals"]["files"], 2)

    def test_the_ids_belong_to_their_snapshot(self):
        first = self.overview()
        self.repo_.write("src/util.py", "A = 9\n")
        second = self.overview()
        self.assertNotEqual(self.files(first)["src/app.py"]["file_id"],
                            self.files(second)["src/app.py"]["file_id"])

    def test_filters_the_repository_configures_are_reported_not_run(self):
        self.repo_.git("config", "--local", "filter.lfs.clean", "true")
        reply = self.overview()
        self.assertEqual(reply["comparison"]["filters_not_run"], ["lfs"])


class FileTests(ReviewCase):

    def hunks(self, reply):
        return [[(l["kind"], l["old"], l["new"], l["text"])
                 for l in h["lines"]] for h in reply["file"]["hunks"]]

    def test_a_staged_and_unstaged_file_is_one_net_diff(self):
        reply = self.opened("src/app.py")
        self.assertEqual((reply["state"], reply["view"]),
                         (code_review.STATE_OBSERVED, "file"), reply["detail"])
        self.assertEqual(self.hunks(reply), [[
            ("context", 1, 1, "def main():"), ("del", 2, None, "    return 1"),
            ("add", None, 2, "    return 3")]])
        block = reply["file"]
        self.assertEqual(set(block), set(code_review.FILE_KEYS))
        self.assertEqual((block["change"], block["staging"], block["state"]),
                         ("modified", "both", "text"))
        self.assertEqual((block["additions"], block["deletions"],
                          block["lines_total"], block["lines_retained"],
                          block["truncated"]), (1, 1, 3, 3, False))
        raw = self.repo_.git("diff-index", "-p", "--no-color", "--no-ext-diff",
                             "--no-textconv", "HEAD", "--",
                             "src/app.py").stdout
        self.assertEqual((block["bytes_total"], block["digest_sha256"]),
                         (len(raw), hashlib.sha256(raw).hexdigest()),
                         "the digest is the whole diff's, exact")
        for hunk in block["hunks"]:
            self.assertEqual(set(hunk), set(code_review.HUNK_KEYS))
            for line in hunk["lines"]:
                self.assertEqual(set(line), set(code_review.LINE_KEYS))
        self.assertEqual((reply["snapshot"], reply["file_id"]),
                         (self.overview()["snapshot"], block["file_id"]))

    def test_every_listed_file_can_be_opened(self):
        listing = self.overview()
        for path, entry in self.files(listing).items():
            with self.subTest(path=path):
                reply = self.read(snapshot=listing["snapshot"],
                                  file_id=entry["file_id"])
                self.assertEqual(reply["state"], code_review.STATE_OBSERVED,
                                 reply["detail"])
                self.assertEqual(reply["file"]["path"], path)
                self.assertEqual(reply["file"]["state"], entry["state"])
                self.assertEqual((reply["file"]["additions"],
                                  reply["file"]["deletions"]),
                                 (entry["additions"], entry["deletions"]))

    def test_an_untracked_file_is_all_additions(self):
        reply = self.opened("notes/new.txt")
        self.assertEqual(self.hunks(reply), [[
            ("add", None, 1, "first"), ("add", None, 2, "second")]])
        raw = b"first\nsecond\n"
        self.assertEqual((reply["file"]["bytes_total"],
                          reply["file"]["digest_sha256"]),
                         (len(raw), hashlib.sha256(raw).hexdigest()))

    def test_a_deleted_file_shows_what_was_removed(self):
        reply = self.opened("docs/gone.md")
        self.assertEqual(self.hunks(reply), [[
            ("del", 1, None, "to be deleted")]])
        self.assertEqual(reply["file"]["change"], "deleted")

    def test_a_rename_shows_both_names_and_its_similarity(self):
        reply = self.opened("src/moved.py")
        block = reply["file"]
        self.assertEqual((block["change"], block["old_path"], block["path"]),
                         ("renamed", "src/mover.py", "src/moved.py"))
        self.assertGreaterEqual(block["similarity"], 50)
        self.assertEqual(block["additions"], 1)

    def test_states_without_content_have_no_hunks(self):
        for path, state in (("bin/blob.bin", "binary"),
                            ("empty.txt", "empty"), (".env", "withheld"),
                            (".herd/state.json", "withheld")):
            with self.subTest(path=path):
                block = self.opened(path)["file"]
                self.assertEqual((block["state"], block["hunks"]), (state, []))

    def test_a_mode_change_alone_has_no_content_diff(self):
        self.repo_.git("checkout", "--", "src/util.py")
        os.chmod(os.path.join(self.workspace, "src", "util.py"), 0o755)
        reply = self.opened("src/util.py")
        block = reply["file"]
        self.assertEqual((block["state"], block["hunks"], block["mode_change"]),
                         ("no_content_diff", [],
                          {"old": "100644", "new": "100755"}))

    def test_an_untracked_file_over_the_readers_bound_is_oversized(self):
        self.repo_.write("big.txt", "x" * (code_review.MAX_UNTRACKED_FILE_BYTES
                                           + 1))
        listing = self.overview()
        self.assertEqual(self.files(listing)["big.txt"]["state"], "oversized")
        self.assertIsNone(self.files(listing)["big.txt"]["additions"])
        block = self.opened("big.txt", listing)["file"]
        self.assertEqual((block["state"], block["hunks"]), ("oversized", []))

    def test_an_untracked_file_that_is_not_utf8_or_is_binary(self):
        self.repo_.write("latin.txt", b"caf\xe9\n")
        self.repo_.write("nul.dat", b"ab\x00cd\n")
        files = self.files()
        self.assertEqual(files["latin.txt"]["state"], "not_utf8")
        self.assertEqual(files["nul.dat"]["state"], "binary")

    def test_a_tracked_file_that_is_not_utf8_says_so_when_opened(self):
        self.repo_.write("src/util.py", b"caf\xe9\n")
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        self.assertEqual(entry["state"], "text", "known only when read")
        block = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"])["file"]
        self.assertEqual((block["state"], block["hunks"]), ("not_utf8", []))

    def test_a_symlink_that_is_untracked_is_unsupported_not_followed(self):
        outside = os.path.join(self.root, "outside.txt")
        with open(outside, "w") as handle:
            handle.write("OUTSIDE SECRET\n")
        os.symlink(outside, os.path.join(self.workspace, "link.txt"))
        reply = self.opened("link.txt")
        self.assertEqual(reply["file"]["state"], "unsupported")
        self.assertNotIn("OUTSIDE SECRET", json.dumps(reply))

    def test_conflicted_entries_have_no_diff(self):
        self.repo_.git("checkout", "-q", "-b", "other")
        self.repo_.write("src/util.py", "A = 1\nB = 2\nC = 30\n")
        fixture_git(self.repo_, "commit", "-aq", "-m", "other")
        self.repo_.git("checkout", "-q", "main")
        fixture_git(self.repo_, "stash", "-q", "-u")
        self.repo_.write("src/util.py", "A = 1\nB = 2\nC = 31\n")
        fixture_git(self.repo_, "commit", "-aq", "-m", "main change")
        fixture_git(self.repo_, "merge", "other", check=False)
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        self.assertEqual((entry["change"], entry["staging"], entry["state"]),
                         ("conflicted", "conflicted", "conflicted"))

    def test_html_in_a_patch_is_returned_verbatim_as_data(self):
        page = "<script>alert('x')</script>\n<a href=\"javascript:run()\">r</a>\n"
        self.repo_.write("notes/page.html", page)
        reply = self.opened("notes/page.html")
        texts = [l["text"] for l in reply["file"]["hunks"][0]["lines"]]
        self.assertEqual(texts, page.splitlines())

    def test_bidirectional_controls_are_carried_not_acted_on(self):
        self.repo_.write("notes/trojan.txt", "a‮b\n")
        reply = self.opened("notes/trojan.txt")
        self.assertEqual(reply["file"]["hunks"][0]["lines"][0]["text"],
                         "a‮b")


class EligibilityTests(ReviewCase):

    def refused(self, problem, **kwargs):
        self.assert_refused(self.read(**kwargs), problem)

    def test_no_held_answer(self):
        self.answer.held = None
        self.refused(code_review.PROBLEM_NOT_CORROBORATED)
        self.answer.held = {"response": None}
        self.refused(code_review.PROBLEM_NOT_CORROBORATED)

    def test_a_stale_answer_corroborates_nothing(self):
        self.freshness = lambda response, now: (contract.HEALTH_STALE,
                                                "response_stale")
        self.refused(code_review.PROBLEM_NOT_CORROBORATED)

    def test_the_mission_must_be_listed_exactly_once(self):
        self.refused(code_review.PROBLEM_MISSION_NOT_LISTED,
                     mission_ref="mc-1:mn-" + "b" * 32)
        self.answer.response["missions"].append(
            copy.deepcopy(self.answer.mission))
        self.refused(code_review.PROBLEM_MISSION_AMBIGUOUS)

    def test_only_a_mission_core_record_or_herdr_task_names_a_workspace(self):
        self.answer.mission["lifecycle"]["model"] = (
            contract.LIFECYCLE_MODEL_WORKFLOW_STORE)
        self.refused(code_review.PROBLEM_SOURCE_KIND)

    def test_an_active_bound_herdr_task_is_read_from_its_workspace(self):
        ref = "%s:%s@%s" % (SOURCE_ID, TASK, "ab" * 6)
        self.answer.mission["mission_ref"] = ref
        self.answer.mission["lifecycle"]["model"] = code_review.HERDR_TASK_MODEL
        listing = self.overview(mission_ref=ref)
        self.assertEqual(listing["target"]["source_id"], SOURCE_ID)
        self.assertEqual(listing["target"]["attribution"], "shared_worktree")
        self.assertGreater(listing["overview"]["totals"]["files"], 0)

    def test_a_terminal_herdr_task_is_not_a_finished_mission_core_read(self):
        self.answer.mission["lifecycle"].update(
            model=code_review.HERDR_TASK_MODEL, standing=contract.STANDING_TERMINAL)
        self.refused(code_review.PROBLEM_NOT_ACTIVE)

    def test_an_assignment_that_is_not_active_is_not_offered(self):
        for standing in (contract.STANDING_INACTIVE,
                         contract.STANDING_TERMINAL,
                         contract.STANDING_UNKNOWN_FUTURE):
            with self.subTest(standing=standing):
                self.answer.mission["lifecycle"]["standing"] = standing
                self.refused(code_review.PROBLEM_NOT_ACTIVE)

    def test_a_completed_mission_is_not_an_active_assignment(self):
        # its record still proves an execution (the evidence library reads it
        # historically); a LIVE review of its workspace is another question
        self.answer.mission["lifecycle"].update(
            state="COMPLETED", standing=contract.STANDING_TERMINAL)
        self.answer.mission["binding"].update(
            state=contract.BINDING_NOT_APPLICABLE, reason="mission_not_active")
        self.refused(code_review.PROBLEM_NOT_ACTIVE)
        # and even if its standing were wrongly active, the binding refuses
        self.answer.mission["lifecycle"]["standing"] = contract.STANDING_ACTIVE
        self.refused(code_review.PROBLEM_NOT_BOUND)

    def test_every_binding_but_bound_refuses_and_names_itself(self):
        for state in (contract.BINDING_UNBOUND, contract.BINDING_DISAPPEARED,
                      contract.BINDING_AMBIGUOUS, contract.BINDING_UNVERIFIED,
                      contract.BINDING_NOT_APPLICABLE):
            with self.subTest(state=state):
                self.answer.mission["binding"].update(state=state, reason="x")
                reply = self.read()
                self.assert_refused(reply, code_review.PROBLEM_NOT_BOUND)
                self.assertIn(state, reply["detail"])

    def test_an_unresolved_or_inconsistent_execution_refuses(self):
        cases = (("task_id", None), ("workspace_realpath", None),
                 ("consistent", False), ("record_ambiguous", True))
        for key, value in cases:
            with self.subTest(key=key):
                saved = self.answer.mission["execution"][key]
                self.answer.mission["execution"][key] = value
                self.refused(code_review.PROBLEM_UNRESOLVED)
                self.answer.mission["execution"][key] = saved
        self.answer.mission["binding"]["task_id"] = OTHER_TASK
        self.refused(code_review.PROBLEM_UNRESOLVED)

    def test_a_replaced_assignment_is_a_different_assignment(self):
        # the SAME Mission and workspace, another task: the review the client
        # opened was for the task it was shown
        self.answer.mission["execution"]["task_id"] = OTHER_TASK
        self.answer.mission["binding"]["task_id"] = OTHER_TASK
        self.refused(code_review.PROBLEM_TASK_CHANGED)
        self.refused(code_review.PROBLEM_TASK_CHANGED, task_id=TASK)

    def test_the_listing_must_carry_the_missions_source_once(self):
        self.answer.response["sources"] = []
        self.refused(code_review.PROBLEM_NOT_CORROBORATED)

    def test_the_source_must_be_registered_now_as_listed(self):
        self.registry.unregister(SOURCE_ID)
        self.refused(code_review.PROBLEM_SOURCE_NOT_REGISTERED)
        self.registry.register(SOURCE_ID, contract.KIND_MISSION_CORE,
                               os.path.join(self.root, "elsewhere"),
                               "local_request", 60, NOW)
        self.refused(code_review.PROBLEM_REGISTRATION_CHANGED)

    def test_an_unreadable_registry_is_refused_not_assumed(self):
        gone = registry_module.SourceRegistry(os.path.join(self.root, "none"))
        self.assert_refused(self.read(registry=gone),
                            code_review.PROBLEM_REGISTRY)

    def test_the_workspace_must_be_a_real_absolute_directory(self):
        link = os.path.join(self.root, "link")
        os.symlink(self.workspace, link)
        for value in ("relative/path", os.path.join(self.root, "absent"), link,
                      os.path.join(self.workspace, "src", "app.py")):
            with self.subTest(value=value):
                self.answer.mission["execution"]["workspace_realpath"] = value
                self.refused(code_review.PROBLEM_WORKSPACE_UNUSABLE)

    def test_a_subdirectory_of_a_repository_is_not_its_worktree_root(self):
        self.answer.mission["execution"]["workspace_realpath"] = os.path.join(
            self.workspace, "src")
        self.refused(code_review.PROBLEM_NOT_WORKTREE_ROOT)

    def test_a_directory_that_is_not_a_repository_is_refused(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        self.answer.mission["execution"]["workspace_realpath"] = plain
        self.refused(code_review.PROBLEM_GIT_FAILED)

    def test_an_unborn_branch_has_no_base_to_compare(self):
        empty = self.repo("empty")
        self.answer.mission["execution"]["workspace_realpath"] = empty.path
        self.refused(code_review.PROBLEM_NO_BASE)

    def test_a_missing_git_is_a_refusal(self):
        class NoGit(object):
            def __getattr__(self, name):
                def call(*args, **kwargs):
                    raise FileNotFoundError("git")
                return call
        self.refused(code_review.PROBLEM_GIT_FAILED, transport=NoGit())


class ObservationCostTests(ReviewCase):
    """What a read costs in git, pinned: the overview observes the workspace twice to agree with
    itself and once more before release; a FILE read observes it once (equal to the snapshot it was
    asked for, which is itself the stability proof) and once more before release."""

    def inventories(self, **kwargs):
        recording = Recording(self.transport)
        reply = self.read(transport=recording, **kwargs)
        return reply, len(recording.asked("live_inventory")), recording

    def test_the_overview_observes_three_times(self):
        reply, inventories, _recording = self.inventories()
        self.assertEqual(reply["state"], code_review.STATE_OBSERVED)
        self.assertEqual(inventories, 3)

    def test_a_file_read_observes_twice(self):
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        reply, inventories, recording = self.inventories(
            snapshot=listing["snapshot"], file_id=entry["file_id"])
        self.assertEqual(reply["state"], code_review.STATE_OBSERVED,
                         reply["detail"])
        self.assertEqual(inventories, 2)
        self.assertEqual(len(recording.asked("live_file_diff")), 1)
        self.assertEqual(reply["accounting"]["git_commands"],
                         len(recording.log))

    def test_a_file_read_still_refuses_a_workspace_that_moved(self):
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        self.repo_.write("src/other.py", "new\n")
        reply, inventories, recording = self.inventories(
            snapshot=listing["snapshot"], file_id=entry["file_id"])
        self.assert_refused(reply, code_review.PROBLEM_SNAPSHOT_MOVED)
        self.assertEqual(inventories, 1, "refused after ONE observation")
        self.assertEqual(recording.asked("live_file_diff"), [],
                         "no diff is read against a snapshot that is gone")


class MovementTests(ReviewCase):
    """Movement is the normal case: stale is never presented as current."""

    def test_a_file_of_an_old_snapshot_is_refused_when_the_workspace_moved(self):
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        self.repo_.write("src/other.py", "new\n")
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"])
        self.assert_refused(reply, code_review.PROBLEM_SNAPSHOT_MOVED)

    def test_a_file_id_of_no_listed_file_is_refused(self):
        listing = self.overview()
        reply = self.read(snapshot=listing["snapshot"],
                          file_id="rf-" + "0" * 32)
        self.assert_refused(reply, code_review.PROBLEM_FILE_NOT_LISTED)

    def test_a_snapshot_that_was_never_current_is_refused(self):
        entry = self.files()["src/util.py"]
        reply = self.read(snapshot="0" * 64, file_id=entry["file_id"])
        self.assert_refused(reply, code_review.PROBLEM_SNAPSHOT_MOVED)

    def test_a_workspace_that_changes_once_mid_read_is_read_again(self):
        change = lambda: self.repo_.write("src/util.py", "A = 5\n")
        hooked = Hooked(self.transport, "live_inventory", change, nth=1)
        reply = self.overview(transport=hooked)
        self.assertEqual(self.files(reply)["src/util.py"]["additions"], 1)
        self.assertGreater(hooked.calls["live_inventory"], 2,
                           "a second pair of observations was taken")

    def test_a_workspace_that_never_stops_changing_is_refused(self):
        counter = [0]

        def churn():
            counter[0] += 1
            self.repo_.write("src/churn.py", "v%d\n" % counter[0])
        hooked = Hooked(self.transport, "live_inventory", churn, every=True)
        reply = self.read(transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_MOVED_DURING_READ)
        self.assertEqual(hooked.calls["live_inventory"],
                         2 * code_review.MAX_OBSERVATION_ATTEMPTS)

    def test_a_workspace_that_moves_after_a_stable_read_is_discarded(self):
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        hooked = Hooked(self.transport, "live_file_diff",
                        lambda: None)

        # the workspace changes AFTER the diff was read, before release
        real = self.transport.live_file_diff

        def diff_then_move(*args, **kwargs):
            result = real(*args, **kwargs)
            self.repo_.write("src/other.py", "moved\n")
            return result
        hooked.real = mock.Mock(wraps=self.transport)
        hooked.real.live_file_diff = diff_then_move
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_MOVED_DURING_READ)
        self.assertIn("discarded", reply["detail"])

    def test_a_file_that_changes_between_listing_and_read_is_refused(self):
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        change = lambda: self.repo_.write("src/util.py", "A = 1\n")
        hooked = Hooked(self.transport, "live_file_diff", change)
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_MOVED_DURING_READ)

    def test_a_diff_that_does_not_match_its_listing_is_refused_even_if_the_workspace_settles_back(
            self):
        # The release check compares the whole workspace again, so a file that
        # is something else only WHILE git reads it and exactly what it was
        # before anything looks again would pass it. The file's own counts are
        # the second line of defence: its diff must add up to its listing.
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        path = os.path.join(self.workspace, "src", "util.py")
        with open(path, "rb") as handle:
            original = handle.read()
        before = os.stat(path)
        real = self.transport.live_file_diff

        def flipped(*args, **kwargs):
            with open(path, "wb") as handle:     # in place: same inode
                handle.write(b"A = 1\nB = 20\nC = 3\nD = 4\nE = 5\nF = 6\n")
            try:
                return real(*args, **kwargs)
            finally:
                with open(path, "wb") as handle:
                    handle.write(original)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        hooked = Hooked(self.transport, "live_file_diff", lambda: None)
        hooked.real = mock.Mock(wraps=self.transport)
        hooked.real.live_file_diff = flipped
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_MOVED_DURING_READ)
        self.assertIn("counts differ", reply["detail"])

    def test_reads_that_compared_against_different_commits_are_movement(self):
        # the listing, the counts and the modes of ONE observation must come
        # from one commit: git reads them one after another, and an agent that
        # commits between them would otherwise be a listing nobody had
        real_counts = self.transport.live_counts
        other = "b" * 40

        def mixed(*args, **kwargs):
            return dict(real_counts(*args, **kwargs), head=other)
        wrapped = mock.Mock(wraps=self.transport)
        wrapped.live_counts = mixed
        bound = {"source_id": SOURCE_ID, "task_id": TASK}
        with self.assertRaises(code_review._Moved):
            code_review._observe(wrapped, self.workspace, bound,
                                 code_review.Accounting())

    def test_a_file_diff_against_another_commit_than_its_listing_is_movement(
            self):
        listing = self.overview()
        entry = self.files(listing)["src/util.py"]
        real = self.transport.live_file_diff

        def other_commit(*args, **kwargs):
            return dict(real(*args, **kwargs), head="c" * 40)
        hooked = Hooked(self.transport, "live_file_diff", lambda: None)
        hooked.real = mock.Mock(wraps=self.transport)
        hooked.real.live_file_diff = other_commit
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_MOVED_DURING_READ)

    def test_an_untracked_file_that_vanishes_is_movement(self):
        listing = self.overview()
        entry = self.files(listing)["notes/new.txt"]
        path = os.path.join(self.workspace, "notes/new.txt")
        hooked = Hooked(self.transport, "live_filter_drivers",
                        lambda: os.replace(path, path + ".gone"))
        reply = self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_SNAPSHOT_MOVED)

    def test_a_registration_removed_during_the_read_discards_the_answer(self):
        hooked = Hooked(self.transport, "live_counts",
                        lambda: self.registry.unregister(SOURCE_ID))
        reply = self.read(transport=hooked)
        self.assert_refused(reply, code_review.PROBLEM_SOURCE_NOT_REGISTERED)
        self.assertIn("discarded", reply["detail"])

    def test_a_registration_repointed_during_the_read_discards_the_answer(self):
        def repoint():
            self.registry.unregister(SOURCE_ID)
            self.registry.register(
                SOURCE_ID, contract.KIND_MISSION_CORE,
                os.path.join(self.root, "elsewhere"), "local_request", 60, NOW)
        reply = self.read(transport=Hooked(self.transport, "live_counts",
                                           repoint))
        self.assert_refused(reply, code_review.PROBLEM_REGISTRATION_CHANGED)

    def test_the_assignment_replaced_during_the_read_discards_the_answer(self):
        def replace():
            self.answer.mission["execution"]["task_id"] = OTHER_TASK
            self.answer.mission["binding"]["task_id"] = OTHER_TASK
        reply = self.read(transport=Hooked(self.transport, "live_counts",
                                           replace))
        self.assert_refused(reply, code_review.PROBLEM_TASK_CHANGED)

    def test_the_workspace_repointed_during_the_read_discards_the_answer(self):
        other = self.repo("other")
        other.write("a.txt", "a\n")
        other.stage("a.txt")
        other.save("base")

        def repoint():
            self.answer.mission["execution"]["workspace_realpath"] = other.path
        reply = self.read(transport=Hooked(self.transport, "live_counts",
                                           repoint))
        self.assert_refused(reply, code_review.PROBLEM_ASSIGNMENT_CHANGED)

    def test_the_listing_going_stale_during_the_read_discards_the_answer(self):
        calls = [0]

        def freshness(response, now):
            calls[0] += 1
            return ((contract.HEALTH_FRESH, None) if calls[0] == 1
                    else (contract.HEALTH_STALE, "response_stale"))
        self.freshness = freshness
        self.assert_refused(self.read(), code_review.PROBLEM_NOT_CORROBORATED)

    def test_the_owner_withdrawing_its_answer_during_the_read_discards_it(self):
        reply = self.read(transport=Hooked(
            self.transport, "live_counts",
            lambda: setattr(self.answer, "held", None)))
        self.assert_refused(reply, code_review.PROBLEM_NOT_CORROBORATED)


class PrivateBodiesAreNeverReadTests(ReviewCase):
    """ROUND 20 F2. A withheld file's body is not merely never RETURNED; no git
    verb that could hash or diff it is ever pointed at it. The fixture makes a
    read ATTEMPT visible: the private tracked files are unreadable (mode 000),
    so any verb that tries to hash one fails or lists it as changed."""

    #: path: (committed, changed after it). The first changes by one same-size
    #: edit (only a hash can tell); the second is only TOUCHED (stat-dirty,
    #: identical). Both are private by the credential-name rule ("secrets").
    PRIVATE = {"secrets/key.txt": ("BODY-ONE-0123456789\n",
                                   "BODY-TWO-ABCDEFGHIJ\n"),
               "secrets/touched.txt": ("same\n", "same\n")}
    READABLE = False

    def busy(self, name="ws", linked=False):
        repo = self.repo(name)
        gitfx.busy_workspace(repo, extras=self.PRIVATE)
        for relative in self.PRIVATE:
            path = os.path.join(repo.path, *relative.split("/"))
            gitfx.future_mtime(path)            # stat-dirty
            if not self.READABLE:
                os.chmod(path, 0)               # and an open() of it fails
            self.addCleanup(os.chmod, path, 0o600)
        return repo

    def test_unreadable_private_tracked_files_do_not_stop_the_review(self):
        reply = self.overview()
        files = self.files(reply)
        for path in self.PRIVATE:
            with self.subTest(path=path):
                entry = files[path]
                self.assertEqual((entry["change"], entry["staging"],
                                  entry["state"], entry["reason"]),
                                 ("modified", "unstaged", "withheld",
                                  "credential_like_name"))
                self.assertEqual((entry["additions"], entry["deletions"]),
                                 (None, None))
        self.assertEqual(reply["overview"]["totals"]["withheld"], 4)
        text = json.dumps(reply)
        for sentinel in ("BODY-ONE", "BODY-TWO"):
            self.assertNotIn(sentinel, text)
        for path in self.PRIVATE:               # a withheld file opens as nothing
            opened = self.opened(path, reply)["file"]
            self.assertEqual((opened["state"], opened["hunks"]), ("withheld", []))


class PrivateLocationsAreExcludedBeforeGitReadsTests(PrivateBodiesAreNeverReadTests):
    """The same fixture with READABLE bodies: nothing fails, so what is
    asserted is the ARGUMENTS of the verbs that could hash or diff."""

    READABLE = True

    def test_a_file_is_read_through_the_index_its_observation_decided_from(self):
        # ROUND 21 F2: the private locations, the base commit and the file's diff
        # come from ONE private index. The file request's observation opens a view;
        # the diff is made through that SAME view (not a new index carrying the old
        # exclusions), naming the path as a file
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        recording = Recording(self.transport)
        reply = self.read(snapshot=listing["snapshot"], file_id=entry["file_id"],
                          transport=recording)
        self.assertEqual(reply["state"], code_review.STATE_OBSERVED, reply["detail"])
        named = recording.asked("live_names")
        diffs = recording.asked("live_file_diff")
        # two observations: the request's own (whose view stays open for the diff)
        # and the release check's, afterwards, which makes a view of its own
        self.assertEqual((len(named), len(diffs)), (2, 1))
        view = named[0][1][1]
        _name, args, _kwargs = diffs[0]
        self.assertIs(args[3], view, "the diff's index is the observation's own")
        self.assertIsNot(named[1][1][1], view, "the release check re-observes")
        self.assertEqual(args[4], True, "the path is selected as a file")
        self.assertEqual(args[2], ("secrets",), "and its exclusions are that index's")

    def test_every_content_reading_verb_is_told_which_locations_are_private(self):
        recording = Recording(self.transport)
        self.overview(transport=recording)
        views = set()
        for verb in ("live_index_stat", "live_inventory", "live_counts",
                     "live_modes"):
            calls = recording.asked(verb)
            self.assertTrue(calls, verb)
            for _name, args, _kwargs in calls:
                self.assertEqual(args[:2], (self.workspace, ("secrets",)), verb)
                views.add(id(args[2]))
        # ONE index for the names and for every read that could hash a file:
        # what was decided private is what those reads leave out
        for _name, args, _kwargs in recording.asked("live_names"):
            views.add(id(args[1]))
        self.assertEqual(len(views), len(recording.asked("live_names")),
                         "one private view per observation, shared by all")


class PrivateLocationEdgesTests(ReviewCase):
    """What the exclusion means for the listing: names and kinds of change of
    private tracked files come from reads that open no file; the limits it
    has are stated here as tests, so they cannot drift quietly."""

    PRIVATE = {"secrets/key.txt": ("BODY-ONE-0123456789\n",
                                   "BODY-TWO-ABCDEFGHIJ\n"),
               "secrets/touched.txt": ("same\n", "same\n")}

    def busy(self, name="ws", linked=False):
        repo = self.repo(name)
        gitfx.busy_workspace(repo, extras=self.PRIVATE)
        for relative in self.PRIVATE:    # edited later: a stat-only read sees it
            gitfx.future_mtime(os.path.join(repo.path, *relative.split("/")))
        return repo

    def test_a_change_to_a_private_listing_moves_the_snapshot(self):
        # the listing of a withheld file (its kind of change) is part of what
        # a snapshot vouches for: staging one, or moving one, must not leave an
        # old overview looking current
        first = self.overview()["snapshot"]
        self.repo_.stage("secrets/key.txt")              # unstaged -> both
        second = self.overview()["snapshot"]
        self.assertNotEqual(second, first)
        self.repo_.git("mv", "secrets/touched.txt", "src/plain.txt")
        third = self.overview()["snapshot"]
        self.assertNotEqual(third, second)
        self.assertEqual(self.overview()["snapshot"], third,
                         "and it is steady while nothing moves")

    def test_a_staged_private_file_is_listed_by_name_and_kind_only(self):
        self.repo_.write("secrets/new.txt", "BODY-NEW-0123\n")
        self.repo_.stage("secrets/new.txt")
        entry = self.files()["secrets/new.txt"]
        self.assertEqual((entry["change"], entry["staging"], entry["state"],
                          entry["additions"], entry["deletions"]),
                         ("added", "staged", "withheld", None, None))
        self.assertNotIn("BODY-NEW", json.dumps(self.overview()))

    def test_a_private_file_removed_from_the_index_is_listed_as_deleted(self):
        self.repo_.git("rm", "-q", "-f", "--", "secrets/touched.txt")
        entry = self.files()["secrets/touched.txt"]
        self.assertEqual((entry["change"], entry["staging"], entry["state"]),
                         ("deleted", "staged", "withheld"))

    def test_a_private_file_removed_from_the_working_tree_only(self):
        os.unlink(os.path.join(self.workspace, "secrets", "key.txt"))
        entry = self.files()["secrets/key.txt"]
        self.assertEqual((entry["change"], entry["staging"], entry["state"]),
                         ("deleted", "unstaged", "withheld"))

    def test_a_private_file_moved_unchanged_to_a_public_name_stays_withheld(self):
        self.repo_.git("mv", "secrets/touched.txt", "src/plain.txt")
        files = self.files()
        entry = files["src/plain.txt"]
        self.assertEqual((entry["change"], entry["state"], entry["reason"],
                          entry["old_path"], entry["additions"]),
                         ("renamed", "withheld", "credential_like_name",
                          "secrets/touched.txt", None))
        self.assertNotIn("secrets/touched.txt", files,
                         "the pair is ONE entry, as git's own pairs are")

    def test_a_moved_private_file_that_was_also_edited_is_not_paired(self):
        # A LIMIT, stated: git pairs a rename by reading both bodies, and a
        # private body is never read. An exact move is recognised from blob ids
        # (above); a move that also changes the content is two listings: the
        # private name deleted (withheld) and a public file added.
        self.repo_.git("mv", "secrets/touched.txt", "src/plain.txt")
        self.repo_.write("src/plain.txt", "same\nand more\n")
        self.repo_.stage("src/plain.txt")
        files = self.files()
        self.assertEqual((files["src/plain.txt"]["change"],
                          files["src/plain.txt"]["state"]), ("added", "text"))
        self.assertEqual((files["secrets/touched.txt"]["change"],
                          files["secrets/touched.txt"]["state"]),
                         ("deleted", "withheld"))

    def test_an_untracked_file_under_a_tracked_private_location_is_not_listed(self):
        # A LIMIT, stated: git is told to leave the whole location out, so it
        # does not list what is untracked inside it either.
        self.repo_.write("secrets/untracked.txt", "BODY-U\n")
        self.assertNotIn("secrets/untracked.txt", self.files())

    def test_too_many_private_locations_refuse_the_overview(self):
        self.repo_.write("keys/a.pem", "k\n")
        self.repo_.stage("keys/a.pem")        # a second location
        with mock.patch.object(git_transport, "MAX_REVIEW_EXCLUSIONS", 1):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_INVENTORY_OVER_BOUND)
        self.assertIn("private locations", reply["detail"])

    def test_the_private_git_directory_never_outlives_the_observation(self):
        # the view is entered and left by hand: however an observation ends
        # (answered, refused inside it, refused by a bound), nothing is left.
        # An END-STATE guard only: the temporary directory's own finalizer
        # would also remove it once the frames are gone, so the test after
        # this one is what tells the observation's own exit from a collection.
        with mock.patch.object(tempfile, "tempdir", self.root):
            self.overview()
            with mock.patch.object(git_transport, "MAX_REVIEW_EXCLUSIONS", 0):
                self.assert_refused(
                    self.read(), code_review.PROBLEM_INVENTORY_OVER_BOUND)
            with mock.patch.object(git_transport, "MAX_REVIEW_NAMES_BYTES", 5):
                self.assert_refused(
                    self.read(), code_review.PROBLEM_INVENTORY_OVER_BOUND)
        self.assertEqual([n for n in os.listdir(self.root)
                          if n.startswith("di-review-")], [])

    def test_the_private_directory_is_removed_when_the_observation_raises_mid_read(self):
        # the view is left by hand with an empty exception triple, so what
        # removes the scratch directory is its OWN unconditional cleanup, not
        # an exception handler: pinned here for the failure path itself. A read
        # that RAISES (not one that is refused cleanly) while the view is open:
        seen = {}

        class Breaks(object):
            def __init__(self, real):
                self.real = real

            def __getattr__(self, name):
                return getattr(self.real, name)

            def live_inventory(self, workspace, locations, view):
                seen["scratch"] = view.env["GIT_DIR"]
                seen["open_when_it_broke"] = os.path.isdir(seen["scratch"])
                raise RuntimeError("the read broke")
        with mock.patch.object(tempfile, "tempdir", self.root):
            try:
                self.read(transport=Breaks(self.transport))
            except RuntimeError as broke:
                # Looked at HERE, while the exception and so its traceback and
                # every frame's locals are alive: a directory that only a
                # garbage collection (the temporary directory's own finalizer)
                # would remove is still there; it is gone only if the
                # observation left the view itself.
                still_there = os.path.exists(seen["scratch"])
                self.assertIsNotNone(broke.__traceback__)
            else:
                self.fail("the read was expected to raise")
        self.assertTrue(seen["open_when_it_broke"],
                        "it raised while the private directory was in use")
        self.assertFalse(still_there, "removed by the observation's own exit")
        self.assertEqual([n for n in os.listdir(self.root)
                          if n.startswith("di-review-")], [])

    def test_the_index_record_of_the_private_locations_has_a_bound(self):
        with mock.patch.object(git_transport, "MAX_REVIEW_INDEX_RECORD_BYTES", 10):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_INVENTORY_OVER_BOUND)
        self.assertRegex(reply["detail"], r"at least \d+")

    def test_the_list_of_tracked_names_has_a_bound(self):
        with mock.patch.object(git_transport, "MAX_REVIEW_NAMES_BYTES", 5):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_INVENTORY_OVER_BOUND)

    def test_no_git_argument_names_a_private_path_except_to_leave_it_out(self):
        # the whole-system boundary: over an overview AND every file opened,
        # every argument git is given that mentions a private location is a
        # pathspec that leaves it out (or, for the stat-only read, looks at it)
        argvs = []
        real = git_transport.GitTransport._stream

        def spy(transport, argv, *args, **kwargs):
            argvs.append(list(argv))
            return real(transport, argv, *args, **kwargs)
        with mock.patch.object(git_transport.GitTransport, "_stream", spy):
            listing = self.overview()
            for entry in listing["overview"]["files"]:
                self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"])
        verbs = [a[a.index("-C") + 2] for a in argvs if "-C" in a]
        self.assertIn("ls-files", verbs)
        self.assertNotIn("diff-files", verbs, "git is never asked to compare a working file for a private listing")
        self.assertIn("status", verbs)
        for argv in argvs:
            for argument in argv:
                if "secrets" in argument:
                    self.assertTrue(argument.startswith((
                        ":(top,literal,exclude)", ":(top,literal)")), argv)
            if "-p" in argv:                    # a file's diff: public paths
                self.assertFalse([a for a in argv[argv.index("--"):]
                                  if "secrets" in a], argv)
        # every read that can hash carries the exclusion
        for argv in argvs:
            verb = argv[argv.index("-C") + 2] if "-C" in argv else ""
            if verb == "status" or (verb == "diff-index" and "--cached"
                                    not in argv and "-p" not in argv):
                self.assertIn(":(top,literal,exclude)secrets", argv, argv)


class ReadWitnessedTests(ReviewCase):
    """ROUND 21. No claim of "never read" rests on output, on a name missing from a
    listing or on a refusal: each is observed with ``gitfx.ReadWitness`` (a clean
    filter that logs the files git physically reads). The private tracked files are
    stat-dirty, and unreadable where a refusal would say so as well."""

    PRIVATE = {"secrets/key.txt": ("BODY-ONE-0123456789\n",
                                   "BODY-TWO-ABCDEFGHIJ\n"),
               "secrets/touched.txt": ("same\n", "same\n")}

    def busy(self, name="ws", linked=False):
        repo = self.repo(name)
        gitfx.busy_workspace(repo, extras=self.PRIVATE)
        for relative in self.PRIVATE:
            gitfx.future_mtime(os.path.join(repo.path, *relative.split("/")))
        return repo

    def witness(self):
        return gitfx.ReadWitness(self, self.repo_)

    def test_the_witness_sees_the_reads_it_is_meant_to_see(self):
        # the CONTROL: with nothing left out, git reads the private files and the
        # public ones, and the witness says so (so a silent witness means something)
        with self.witness() as witness:
            self.transport.live_inventory(self.workspace, ())
        self.assertLessEqual({"secrets/key.txt", "secrets/touched.txt",
                              "src/app.py"}, witness.reads())

    def test_a_move_destination_that_inherits_privacy_is_never_read(self):
        # F1: secrets/touched.txt moved, unchanged, to a public name. The destination
        # INHERITS the source's privacy, so it is left out of every read that could
        # hash it, not merely withheld later in the answer
        self.repo_.git("mv", "secrets/touched.txt", "src/plain.txt")
        gitfx.future_mtime(os.path.join(self.workspace, "src", "plain.txt"))
        with self.witness() as witness:
            reply = self.overview()
        entry = self.files(reply)["src/plain.txt"]
        self.assertEqual((entry["change"], entry["state"], entry["old_path"]),
                         ("renamed", "withheld", "secrets/touched.txt"))
        reads = witness.reads()
        self.assertIn("src/app.py", reads, "the witness is live")
        self.assertFalse(reads & {"src/plain.txt", "secrets/touched.txt",
                                  "secrets/key.txt"}, reads)

    def test_a_private_blob_is_not_read_from_the_object_store_either(self):
        # The witness sees working-file reads only. A blob read from the object
        # store runs no filter, so here an attempt is made visible by making the
        # private blob unreadable: any read of it FAILS the review. A GUARD, not a
        # RED: it passed on the rejected code too (the private source was already
        # left out as part of its location); it holds the object-store side of the
        # invariant for the move's source and destination, which share one blob.
        self.repo_.git("mv", "secrets/touched.txt", "src/plain.txt")
        digest = gitfx.blob_id(b"same\n").decode()
        loose = os.path.join(self.workspace, ".git", "objects", digest[:2],
                             digest[2:])
        self.assertTrue(os.path.exists(loose), "a loose object, as this needs")
        os.chmod(loose, 0)
        self.addCleanup(os.chmod, loose, 0o444)
        entry = self.files(self.overview())["src/plain.txt"]
        self.assertEqual((entry["change"], entry["state"]),
                         ("renamed", "withheld"))

    def test_a_private_file_recorded_far_in_the_future_is_never_read(self):
        # F4: an entry whose RECORDED time is later than the index's own is "racily
        # clean" to git, which hashes the file to be sure it is unchanged. No chosen
        # future horizon can make that impossible (a later recorded time is racy
        # under any finite index time), so the read that lists a private file's
        # change must not ask git to compare a working file at all.
        repo = self.repo("far")
        recorded = {"secrets/near.txt": time.time() + 72 * 3600,
                    "secrets/far.txt": 4294967295}
        repo.write("src/app.py", "def main():\n    return 1\n")
        for relative, when in recorded.items():
            path = repo.write(relative, "BODY-ONE-0123456789\n")
            os.utime(path, (when, when))
        edited = repo.write("secrets/edited.txt", "BODY-ONE-0123456789\n")
        os.utime(edited, (recorded["secrets/near.txt"],) * 2)
        repo.stage("src", "secrets")
        repo.save("base")
        repo.write("src/app.py", "def main():\n    return 2\n")
        repo.write("secrets/edited.txt", "BODY-TWO-ABCDEFGHIJ\n")   # same size
        os.utime(edited, (recorded["secrets/near.txt"] - 3600,) * 2)
        self.workspace = repo.path
        self.answer.mission["execution"]["workspace_realpath"] = repo.path
        self.repo_ = repo
        # the CONTROL: git's own read of these (unchanged, racily clean) entries
        # DOES open them, however far ahead their recorded time is
        witness = self.witness()
        subprocess.run(
            ["git", "-C", repo.path, "--no-optional-locks", "-c",
             "filter.witness.clean=" + witness.command, "diff-files", "--raw",
             "-z"], capture_output=True, env=gitfx.CLEAN_ENV)
        self.assertEqual(witness.reads() & set(recorded), set(recorded),
                         "git's own diff-files hashes a racily clean entry")
        witness.reset()
        with witness:
            reply = self.overview()
        files = self.files(reply)
        self.assertEqual((files["secrets/edited.txt"]["change"],
                          files["secrets/edited.txt"]["staging"],
                          files["secrets/edited.txt"]["state"]),
                         ("modified", "unstaged", "withheld"))
        for relative in recorded:
            self.assertNotIn(relative, files, "unchanged, so not a change")
        reads = witness.reads()
        self.assertIn("src/app.py", reads, "the witness is live")
        self.assertFalse(reads & (set(recorded) | {"secrets/edited.txt"}), reads)

    def test_a_selected_file_that_becomes_a_directory_is_never_expanded(self):
        # F2: after the overview is observed, the selected file src/app.py is
        # replaced by a DIRECTORY holding a tracked private child (and that is
        # staged). The diff must read neither: its index, its base and its
        # pathspec are the observation's own, and a path that is no longer the file
        # it was listed as is refused
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        child = "src/app.py/secret.txt"

        def replace():
            self.repo_.git("rm", "-q", "-f", "--cached", "--", "src/app.py")
            os.unlink(os.path.join(self.workspace, "src", "app.py"))
            self.repo_.write(child, "BODY-CHILD-0123456789\n")
            self.repo_.stage(child)
            gitfx.future_mtime(os.path.join(self.workspace, *child.split("/")))
        hooked = Hooked(self.transport, "live_file_diff", replace)
        with self.witness() as witness:
            reply = self.read(snapshot=listing["snapshot"],
                              file_id=entry["file_id"], transport=hooked)
        self.assertNotIn(child, witness.reads(), witness.reads())
        self.assertEqual(reply["state"], code_review.STATE_REFUSED)
        self.assertIn(reply["problem"], (
            code_review.PROBLEM_MOVED_DURING_READ,
            code_review.PROBLEM_SNAPSHOT_MOVED))

    def test_a_type_change_is_refused_before_git_is_asked_for_the_file(self):
        # F2: the type of the selected path is checked BEFORE the read, against what
        # the observation recorded (not discovered from what the diff comes back as)
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        recording = Recording(self.transport)
        real_stat = code_review._stat
        calls = []

        def stat(workspace, relative):
            result = real_stat(workspace, relative)
            if relative == "src/app.py":
                calls.append(result)
                if len(calls) > 1 and result is not None:
                    result = [0o040755] + list(result[1:])     # now a directory
            return result
        with mock.patch.object(code_review, "_stat", stat):
            reply = self.read(snapshot=listing["snapshot"],
                              file_id=entry["file_id"], transport=recording)
        self.assertEqual(reply["state"], code_review.STATE_REFUSED)
        self.assertEqual(recording.asked("live_file_diff"), [],
                         "git was never asked for the file")


PIXEL = REPO_ROOT / "integrations" / "pixel-agents"
TSX = PIXEL / "node_modules" / ".bin" / "tsx"
#: The office's own answer validator (server/src/di/codeReview.ts) and, behind it, the dialog's own
#: row and truncation words (webview-ui/src/components/diCodeReviewModel.ts), run as written: stdin is
#: [{answer, ref}], stdout [{ok, view} | {ok: false, detail}] where ``view`` is what the dialog would
#: SHOW of a file answer (the kind of each line row, and the truncation sentence, or null). No copy of
#: any rule lives here: a producer answer goes through the office to the view.
OFFICE_VALIDATOR = """
import { parseCodeReviewAnswer, backendMessage } from './server/src/di/codeReview.ts';
import { MAX_CODE_REVIEW_RESPONSE_BYTES } from './server/src/di/backendClient.ts';
import { unifiedRows, truncationWords } from './webview-ui/src/components/diCodeReviewModel.ts';
// no lone half of a surrogate pair: what the dialog would otherwise draw as a broken character
const wellFormed = (s) => !/[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])|(?<![\\uD800-\\uDBFF])[\\uDC00-\\uDFFF]/.test(s);
let input = '';
process.stdin.on('data', (d) => (input += d)).on('end', () => {
  const cases = JSON.parse(input);
  console.log(JSON.stringify(cases.map((c) => {
    const r = parseCodeReviewAnswer(c.answer, c.ref);
    if (!r.ok) return { ok: false, detail: r.detail };
    let view = null;
    if (r.answer.file !== null) {
      const file = backendMessage(c.ref, r.answer).file;
      const all = file.hunks.flatMap((h) => h.lines);
      view = {
        rows: unifiedRows(file.hunks, new Set()).filter((row) => row.kind === 'line').map((row) => row.line.kind),
        truncation: truncationWords(file),
        // what the OFFICE measures: JavaScript's .length is UTF-16 code units
        lines: all.map((l) => ({ kind: l.kind, units: l.text.length, clipped: l.clipped, wellFormed: wellFormed(l.text) })),
        chars: all.reduce((n, l) => n + l.text.length, 0),
      };
    }
    const target = r.answer.target;
    return {
      ok: true,
      view,
      branchUnits: target && target.branch ? target.branch.length : null,
      branchWellFormed: target && target.branch ? wellFormed(target.branch) : null,
      detailUnits: r.answer.detail === null ? null : r.answer.detail.length,
      detailWellFormed: r.answer.detail === null ? null : wellFormed(r.answer.detail),
      filterUnits: r.answer.comparison ? r.answer.comparison.filtersNotRun.map((f) => f.length) : null,
      filtersWellFormed: r.answer.comparison ? r.answer.comparison.filtersNotRun.every(wellFormed) : null,
      responseBound: MAX_CODE_REVIEW_RESPONSE_BYTES,
    };
  })));
});
"""


def office_verdicts(cases):
    """What the office says about each real backend answer (one process for all)."""
    done = subprocess.run([str(TSX), "-e", OFFICE_VALIDATOR], cwd=str(PIXEL),
                          input=json.dumps(cases), capture_output=True,
                          text=True, timeout=180)
    if done.returncode != 0:
        raise AssertionError("the office validator did not run: "
                             + done.stderr[-400:])
    return json.loads(done.stdout)


@unittest.skipUnless(TSX.exists(), "needs the installed office toolchain (tsx)")
class BackendOfficeBoundaryTests(ReviewCase):
    """ROUND 21 F3. A backend answer can look self-consistent and still be one the
    office REFUSES: the office re-checks every answer against itself (retained lines
    against the totals, truncation against both, the character bound over ALL retained
    text, markers included). So the boundary is crossed here for real: the answer the
    real backend returns over a real repository goes through the office's own
    validator, at exactly the bounds where an end-of-file marker used to be mishandled."""

    @staticmethod
    def numbered(count):
        return "".join("line %d\n" % n for n in range(count))

    def cases(self):
        chars = code_review.MAX_REVIEW_FILE_CHARS
        full = 1200
        whole = (chars - 1) // full                      # whole 1,200-character lines
        rest = chars - whole * full                      # the characters that complete the bound exactly
        at_cap = "".join("x" * full + "\n" for _ in range(whole)) + "y" * rest
        marker = len(" No newline at end of file")
        fills_with_marker = at_cap[:-marker]       # the marker's own characters complete the bound
        return {
            # the line bound exactly, no final newline: the marker is not a line
            "tracked 6000 lines, no final newline": (True, self.numbered(6000)[:-1]),
            "untracked 6000 lines, no final newline": (False, self.numbered(6000)[:-1]),
            "tracked 6000 lines, final newline": (True, self.numbered(6000)),
            "tracked 6001 lines, no final newline": (True, self.numbered(6001)[:-1]),
            "untracked 6001 lines, no final newline": (False, self.numbered(6001)[:-1]),
            # the character bound exactly (the marker's own text counts for the office)
            "tracked at the character bound, no final newline": (True, at_cap),
            "tracked one character over the bound": (True, at_cap + "z"),
            # the bound filled EXACTLY (1,048,576 retained characters) by the lines AND their marker
            "tracked filling the bound with its marker": (True, fills_with_marker),
        }

    def test_the_answers_at_the_bounds_are_ones_the_office_accepts(self):
        written = {}
        for label, (tracked, body) in self.cases().items():
            relative = "edge/case%d.txt" % len(written)
            self.repo_.write(relative, body)
            if tracked:
                self.repo_.stage(relative)
            written[label] = relative
        listing = self.overview()
        ref = {"missionRef": MISSION_REF, "memberRef": "m", "taskId": TASK,
               "listing": "l"}
        sent = [("the overview", {"answer": listing, "ref": dict(ref)})]
        replies = {}
        for label, relative in written.items():
            reply = self.opened(relative, listing)
            replies[label] = reply
            self.assertEqual(reply["state"], code_review.STATE_OBSERVED,
                             (label, reply["detail"]))
            sent.append((label, {"answer": reply, "ref": dict(
                ref, snapshot=listing["snapshot"],
                fileId=self.files(listing)[relative]["file_id"])}))
        verdicts = office_verdicts([case for _label, case in sent])
        refused = [(label, verdict["detail"])
                   for (label, _case), verdict in zip(sent, verdicts)
                   if not verdict["ok"]]
        self.assertEqual(refused, [], "the office refuses answers the backend made")
        # and the shapes at the bounds are what they should be
        for label, reply in replies.items():
            block = reply["file"]
            self.assertEqual(block["truncated"],
                             block["lines_total"] > block["lines_retained"],
                             (label, "truncated if and only if lines were left out"))
        six = replies["tracked 6000 lines, no final newline"]["file"]
        self.assertEqual((six["truncated"], six["lines_total"],
                          six["lines_retained"]), (False, 6000, 6000))
        self.assertEqual(six["hunks"][-1]["lines"][-1]["kind"], "no_newline",
                         "the marker is kept: it is not a line")
        over = replies["tracked 6001 lines, no final newline"]["file"]
        self.assertEqual((over["truncated"], over["lines_total"],
                          over["lines_retained"]), (True, 6001, 6000))
        capped = replies["tracked one character over the bound"]["file"]
        self.assertTrue(capped["truncated"])
        # ROUND 22 F2: past the office, to what the dialog SHOWS. At the character bound the end-of-file
        # marker is either shown or its omission is reported: never silently dropped
        for (label, _case), verdict in zip(sent, verdicts):
            if verdict.get("view") is None:
                continue
            view = verdict["view"]
            if label in ("tracked at the character bound, no final newline",
                         "tracked filling the bound with its marker"):
                self.assertTrue(
                    view["rows"][-1] == "no_newline" or view["truncation"] is not None,
                    (label, "the view shows the marker or says lines are missing", view["rows"][-3:],
                     view["truncation"]))
        filled = [v for (label, _c), v in zip(sent, verdicts)
                  if label == "tracked filling the bound with its marker"][0]["view"]
        self.assertEqual(filled["rows"][-1], "no_newline", "the marker is kept when its line is")
        self.assertIsNone(filled["truncation"], "and nothing is reported missing: nothing is")


#: ONE character, TWO UTF-16 code units: Python's ``len`` says 1, JavaScript's ``.length`` (the office's every bound) says 2.
ASTRAL = "\U0001F600"


@unittest.skipUnless(TSX.exists(), "needs the installed office toolchain (tsx)")
class UnicodeBoundaryTests(ReviewCase):
    """ROUND 23 (the Reviewer's finding). The backend counted characters as code points (Python's
    ``len``) while the office counts UTF-16 code units (JavaScript's ``.length``): 601 emoji are 601
    here and 1,202 there, so a line the backend emitted whole, and a file it kept within the total,
    were REFUSED by the office. Crossed here for real, as the Reviewer asked: the real backend over a
    real repository, through the office's own validator and the dialog's own rows, with every length
    taken from JavaScript, never from a Python copy of the rule."""

    REF = {"missionRef": MISSION_REF, "memberRef": "m", "taskId": TASK, "listing": "l"}

    def through_the_office(self, cases):
        """{label: (tracked, body)} -> {label: (reply, verdict)}, every file read for real and judged by the office."""
        written = {}
        for label, (tracked, body) in cases.items():
            relative = "uni/case%d.txt" % len(written)
            self.repo_.write(relative, body)
            if tracked:
                self.repo_.stage(relative)
            written[label] = relative
        listing = self.overview()
        sent, replies = [], {}
        for label, relative in written.items():
            reply = self.opened(relative, listing)
            self.assertEqual(reply["state"], code_review.STATE_OBSERVED, (label, reply["detail"]))
            replies[label] = reply
            sent.append((label, {"answer": reply, "ref": dict(
                self.REF, snapshot=listing["snapshot"],
                fileId=self.files(listing)[relative]["file_id"])}))
        verdicts = office_verdicts([case for _label, case in sent])
        return dict((label, (replies[label], verdict))
                    for (label, _case), verdict in zip(sent, verdicts))

    def test_a_line_of_astral_characters_is_cut_to_the_units_the_office_counts(self):
        cases = {
            "tracked 601 astral": (True, ASTRAL * 601 + "\n"),
            "untracked 601 astral": (False, ASTRAL * 601 + "\n"),
            "tracked 600 astral (exactly the bound)": (True, ASTRAL * 600 + "\n"),
            "tracked 1199 ASCII then an astral": (True, "a" * 1199 + ASTRAL + "tail\n"),
            "tracked 1200 BMP non-ASCII": (True, "\u00e9" * 1200 + "\n"),
        }
        got = self.through_the_office(cases)
        # (units the OFFICE measures in what is shown, characters left out)
        expected = {
            "tracked 601 astral": (1200, 1),
            "untracked 601 astral": (1200, 1),
            "tracked 600 astral (exactly the bound)": (1200, 0),
            "tracked 1199 ASCII then an astral": (1199, 5),   # the astral is whole or not at all: it does not fit
            "tracked 1200 BMP non-ASCII": (1200, 0),
        }
        seen = {}
        for label, (reply, verdict) in got.items():
            self.assertTrue(verdict["ok"], (label, "the office refuses an answer the backend made",
                                            verdict.get("detail")))
            line = verdict["view"]["lines"][0]
            self.assertTrue(line["wellFormed"], (label, "never half a character"))
            seen[label] = (line["units"], line["clipped"])
        self.assertEqual(seen, expected)
        # the count a reader sees is in CHARACTERS: what is shown plus what is left out is the line
        for label, (tracked, body) in cases.items():
            shown = got[label][0]["file"]["hunks"][0]["lines"][0]
            self.assertEqual(len(shown["text"]) + shown["clipped"], len(body.rstrip("\n")), label)

    def test_the_total_bound_is_the_offices_unit_and_the_answer_stays_inside_the_byte_budget(self):
        exact = (ASTRAL * 600 + "\n") * 873 + ASTRAL * 475     # 873 x 1,200 + 950 + the 26-unit marker = 1,048,576
        cases = {
            "tracked 900 lines of 600 astral": (True, (ASTRAL * 600 + "\n") * 900),
            "tracked filling the bound exactly, with its marker": (True, exact),
            "tracked one astral over the bound": (True, exact + ASTRAL),
        }
        got = self.through_the_office(cases)
        for label, (reply, verdict) in got.items():
            self.assertTrue(verdict["ok"], (label, "the office refuses an answer the backend made",
                                            verdict.get("detail")))
            self.assertLessEqual(verdict["view"]["chars"], code_review.MAX_REVIEW_FILE_CHARS, label)
            wire = len(json.dumps(reply, sort_keys=True).encode("utf-8"))
            self.assertLessEqual(wire, verdict["responseBound"],
                                 (label, "inside the byte budget the office reads"))
        many = got["tracked 900 lines of 600 astral"]
        self.assertEqual((many[0]["file"]["truncated"], many[0]["file"]["lines_total"],
                          many[0]["file"]["lines_retained"], many[1]["view"]["chars"]),
                         (True, 900, 873, 873 * 1200))
        self.assertIsNotNone(many[1]["view"]["truncation"], "the cut is said")
        self.assertEqual(len(many[1]["view"]["rows"]), 873)
        fits = got["tracked filling the bound exactly, with its marker"]
        self.assertEqual((fits[0]["file"]["truncated"], fits[1]["view"]["chars"],
                          fits[1]["view"]["rows"][-1], fits[1]["view"]["truncation"]),
                         (False, code_review.MAX_REVIEW_FILE_CHARS, "no_newline", None))
        over = got["tracked one astral over the bound"]
        self.assertEqual((over[0]["file"]["truncated"], over[0]["file"]["lines_total"],
                          over[0]["file"]["lines_retained"]), (True, 874, 873))
        self.assertIsNotNone(over[1]["view"]["truncation"], "a line that does not fit with its marker is cut, and said")

    def test_a_branch_of_astral_characters_is_cut_to_the_units_the_office_counts(self):
        name = "/".join([ASTRAL * 63] * 3)               # 191 characters, 380 units; every component is a legal 252-byte name
        self.repo_.git("checkout", "-q", "-b", name)
        listing = self.overview()
        verdict = office_verdicts([{"answer": listing, "ref": dict(self.REF)}])[0]
        self.assertTrue(verdict["ok"], ("the office refuses the whole overview", verdict.get("detail")))
        self.assertLessEqual(verdict["branchUnits"], code_review.MAX_BRANCH_CHARS)
        self.assertTrue(verdict["branchWellFormed"])
        self.assertTrue(name.startswith(listing["target"]["branch"]), "what is shown is the start of the real name")

    def test_a_filter_driver_name_is_cut_to_the_units_the_office_counts(self):
        # the repository names content-filter drivers; they are LISTED (never run) and the backend had no bound
        # of its own on a name, while the office accepts at most 64 units each
        names = ["a" * 65, ASTRAL * 33, "b" * 64]
        for name in names:
            self.repo_.git("config", "filter.%s.clean" % name, "cat")
        listing = self.overview()
        verdict = office_verdicts([{"answer": listing, "ref": dict(self.REF)}])[0]
        self.assertTrue(verdict["ok"], ("the office refuses the whole overview", verdict.get("detail")))
        self.assertTrue(verdict["filtersWellFormed"])
        self.assertEqual(sorted(verdict["filterUnits"]), [64, 64, 64],
                         "a name over the bound is shown cut to it; one within it is shown whole")
        shown = listing["comparison"]["filters_not_run"]
        self.assertEqual(sorted(shown), sorted(["a" * 64, ASTRAL * 32, "b" * 64]))

    def test_a_refusal_detail_of_astral_characters_is_cut_to_the_units_the_office_counts(self):
        reply = code_review.refusal(code_review.PROBLEM_GIT_FAILED, ASTRAL * 200, "overview",
                                    MISSION_REF, TASK, None, None)
        verdict = office_verdicts([{"answer": reply, "ref": dict(self.REF)}])[0]
        self.assertTrue(verdict["ok"], ("the office refuses the backend's own refusal", verdict.get("detail")))
        self.assertLessEqual(verdict["detailUnits"], code_review.MAX_DETAIL_CHARS)
        self.assertTrue(verdict["detailWellFormed"])


#: ROUND 24. The child of the watchdog counterfactual: REAL ``ReviewCase`` tests whose real read (or whose fixture) is
#: STUCK, each with a short watchdog, and a report of what happened. It runs in a child process so that a bound that
#: failed to fire could never hang THIS suite: the parent's ``subprocess.run(timeout=)`` is the outer bound, and every
#: stuck fake below terminates itself (the termination rule: a self-terminating fake).
WATCHDOG_CHILD = r"""
import io, json, os, sys, threading, time, unittest
from unittest import mock
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], "tests"))
import test_code_review as base
from target_runtime import code_review, git_transport

SAFETY = 8.0        # every stuck fake below ends by itself after this long: the child cannot hang


class StuckRead(object):
    '''The real transport, except that ONE live read blocks (a fake that waits and releases itself, as test_catalog's
    BlockedQuery does).'''
    def __init__(self, real, seconds):
        self.real = real
        self.seconds = seconds

    def __getattr__(self, name):
        if name != "live_names":
            return getattr(self.real, name)

        def stuck(*args, **kwargs):
            threading.Event().wait(self.seconds)
            raise git_transport.GitTransportError("the stuck read was released")
        return stuck


class Control(base.ReviewCase):
    '''No effective bound (the watchdog is far longer than the stall): the stall is REAL.'''
    watchdog_seconds = 60
    problem = None

    def test_a_stuck_read_blocks(self):
        Control.problem = self.read(transport=StuckRead(self.transport, 3.0))["problem"]


class ReadStall(base.ReviewCase):
    watchdog_seconds = 2
    reached = False

    def test_a_stuck_read_is_bounded(self):
        ReadStall.reached = True
        self.read(transport=StuckRead(self.transport, SAFETY))
        raise AssertionError("the stuck read returned: nothing bounded it")


class GitStall(base.gitfx.FixtureCase):
    '''A test of the git module's own base (the transport's tests): the REAL transport, whose one read blocks.'''
    watchdog_seconds = 2
    reached = False

    def test_a_stuck_transport_read_is_bounded(self):
        repo = self.busy()
        GitStall.reached = True

        def stuck(transport, *args, **kwargs):
            threading.Event().wait(SAFETY)
            raise git_transport.GitTransportError("the stuck read was released")
        with mock.patch.object(git_transport.GitTransport, "_stream", stuck):
            git_transport.GitTransport().live_head(repo.path)
        raise AssertionError("the stuck transport read returned: nothing bounded it")


class SetupStall(base.ReviewCase):
    watchdog_seconds = 2
    ran = False

    def busy(self, *args, **kwargs):
        threading.Event().wait(SAFETY)
        raise AssertionError("fixture setup returned: nothing bounded it")

    def test_body(self):
        SetupStall.ran = True


report = {}
for key, case in (("control", Control), ("read", ReadStall), ("git", GitStall), ("setup", SetupStall)):
    stream = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case))
    report[key] = {"success": result.wasSuccessful(),
                   "elapsed": time.monotonic() - started,
                   "errors": "".join(text for _test, text in result.errors + result.failures)}
report["control"]["problem"] = Control.problem
report["read"]["reached"] = ReadStall.reached
report["git"]["reached"] = GitStall.reached
report["setup"]["ran"] = SetupStall.ran
print(json.dumps(report))
"""


class WatchdogBoundsTheRealReadTests(Bounded):
    """ROUND 24 (CONTRIBUTING.md, the termination rule). Every ``ReviewCase`` test enters a real fixture, a real backend
    and a real git before anything else could stop it, so the bound that stops a stuck one must be the TEST's and must
    be armed before the fixture is built. ``ReviewCase`` takes the repository's own independent watchdog
    (``test_mission_sources.Bounded``: a SIGALRM that raises ``WatchdogExpired``, a ``BaseException`` that the backend's
    ``except OSError`` cannot swallow, recorded and failed at cleanup). This proves it FIRES, by making the read and
    then the fixture actually stall, in a child process, quickly (a 2-second watchdog, not the real 60)."""

    def test_a_stuck_read_and_a_stuck_fixture_are_stopped_by_the_test_owned_bound(self):
        done = subprocess.run([sys.executable, "-c", WATCHDOG_CHILD, str(REPO_ROOT)],
                              capture_output=True, text=True, timeout=45)
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        report = json.loads(done.stdout)
        control, read, setup, git = report["control"], report["read"], report["setup"], report["git"]
        # the stall is real: with no effective bound the stuck read held the test for the time it was stuck
        self.assertTrue(control["success"], control["errors"])
        self.assertGreaterEqual(control["elapsed"], 2.9)
        self.assertEqual(control["problem"], code_review.PROBLEM_GIT_FAILED)
        # and with the bound it FAILS FAST with the watchdog's error, far inside the 8 s the stuck fake would take
        for label, outcome in (("the real read", read), ("the fixture", setup), ("the transport's own read", git)):
            self.assertFalse(outcome["success"], label)
            self.assertIn("WatchdogExpired", outcome["errors"], label)
            self.assertIn("watchdog: test exceeded 2 s", outcome["errors"], label)
            self.assertLess(outcome["elapsed"], 5.0, label)
        self.assertTrue(read["reached"], "the stall was inside the read, after the fixture was built")
        self.assertIn("code_review.py", read["errors"], "the expiry interrupted the real backend's own code path")
        self.assertFalse(setup["ran"], "a stuck fixture is stopped before the test body")
        self.assertTrue(git["reached"], "the stall was inside the transport's read, after the fixture was built")
        self.assertIn("git_transport.py", git["errors"], "the expiry interrupted the transport's own code path")

    def test_every_test_class_of_the_review_suites_carries_the_bound(self):
        # The base classes carry it, so a class added later is bounded without remembering to be. This names the
        # classes of the three modules that do not (FifoTests keeps its own subprocess bound as well).
        unbounded = []
        for module in (gitfx, review_tests, sys.modules[__name__]):
            for name, cls in vars(module).items():
                if (isinstance(cls, type) and issubclass(cls, unittest.TestCase)
                        and cls.__module__ == module.__name__ and not issubclass(cls, Bounded)):
                    unbounded.append("%s.%s" % (module.__name__, name))
        self.assertEqual(sorted(unbounded), [])


class BoundsTests(ReviewCase):

    def test_more_changed_files_than_the_bound_refuses_the_whole_overview(self):
        with mock.patch.object(code_review, "MAX_REVIEW_FILES", 5):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_TOO_MANY_FILES)
        self.assertIn("5", reply["detail"])

    def test_exactly_the_bound_is_listed(self):
        with mock.patch.object(code_review, "MAX_REVIEW_FILES", 11):
            reply = self.overview()
        self.assertEqual(len(reply["overview"]["files"]), 10)

    def test_an_inventory_past_its_ceiling_refuses_with_a_lower_bound(self):
        with mock.patch.object(git_transport, "MAX_REVIEW_INVENTORY_BYTES", 40):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_INVENTORY_OVER_BOUND)
        self.assertRegex(reply["detail"], r"at least \d+")

    def test_a_git_read_past_its_deadline_refuses(self):
        class Slow(object):
            def __init__(self, real):
                self.real = real

            def __getattr__(self, name):
                if name == "live_inventory":
                    return lambda *a, **k: {
                        "status": git_transport.CAPTURE_REFUSED_DEADLINE,
                        "total_bytes_lower_bound": 0}
                return getattr(self.real, name)
        self.assert_refused(self.read(transport=Slow(self.transport)),
                            code_review.PROBLEM_GIT_DEADLINE)

    def test_a_git_failure_is_refused_not_guessed(self):
        class Fails(object):
            def __getattr__(self, name):
                def call(*args, **kwargs):
                    raise git_transport.GitTransportError("boom")
                return call
        self.assert_refused(self.read(transport=Fails()),
                            code_review.PROBLEM_GIT_FAILED)

    def test_the_untracked_read_budget_is_held_before_the_read(self):
        recording = Recording(self.transport)
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            return real(*args)
        with mock.patch.object(code_review, "MAX_UNTRACKED_TOTAL_BYTES", 1000), \
                mock.patch.object(state_artifact, "read_state_artifact", spy):
            reply = self.read(transport=recording)
        self.assert_refused(reply, code_review.PROBLEM_BUDGET)
        self.assertEqual(reads, [], "no read was made past the budget")

    def test_a_refused_untracked_read_keeps_its_hold(self):
        self.repo_.write("big.txt", "x" * (code_review.MAX_UNTRACKED_FILE_BYTES
                                           + 1))
        reply = self.overview()
        self.assertGreaterEqual(reply["accounting"]["untracked_bytes"],
                                code_review.MAX_UNTRACKED_FILE_BYTES)

    def test_the_untracked_read_cap_bounds_the_bytes_actually_read(self):
        # ROUND 20 F3. A file is known to be over the reader's bound only by
        # reading ONE BYTE PAST it, so each oversized file costs bound + 1
        # bytes of real I/O. Charging only the bound lets the physical total
        # pass the cap (by one byte per refused read, 256 over the whole cap).
        total = code_review.MAX_UNTRACKED_TOTAL_BYTES
        over = code_review.MAX_UNTRACKED_FILE_BYTES + 1
        for number in range(total // code_review.MAX_UNTRACKED_FILE_BYTES + 2):
            # "aa/" sorts before every other path, so these are read first
            self.repo_.write("aa/f%03d.txt" % number, b"x" * over)
        counting = CountingOs()
        with mock.patch.object(prepare_module, "os", counting):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_BUDGET)
        self.assertLessEqual(counting.bytes, total,
                             "the physical reads stay inside the cap")
        self.assertGreater(counting.bytes, total // 2,
                           "the cap, not the files, ended the read")
        self.assertGreaterEqual(reply["accounting"]["untracked_bytes"],
                                counting.bytes,
                                "the charge covers every byte actually read")

    def test_a_file_diff_past_its_ceiling_is_refused_with_a_lower_bound(self):
        listing = self.overview()
        entry = self.files(listing)["src/app.py"]
        with mock.patch.object(git_transport, "MAX_REVIEW_FILE_DIFF_BYTES", 60):
            reply = self.read(snapshot=listing["snapshot"],
                              file_id=entry["file_id"])
        self.assert_refused(reply, code_review.PROBLEM_FILE_DIFF_OVER_BOUND)
        self.assertRegex(reply["detail"], r"at least \d+")
        self.assertNotIn("digest", json.dumps(reply["file"] or {}))

    def test_a_large_diff_is_truncated_with_exact_totals(self):
        self.repo_.write("src/util.py",
                         "".join("line %d\n" % n for n in range(7000)))
        listing = self.overview()
        block = self.read(snapshot=listing["snapshot"],
                          file_id=self.files(listing)["src/util.py"]["file_id"]
                          )["file"]
        self.assertTrue(block["truncated"])
        self.assertEqual(block["lines_retained"],
                         code_review.MAX_REVIEW_FILE_LINES)
        self.assertEqual(block["lines_total"], 7003)
        self.assertEqual((block["additions"], block["deletions"]), (7000, 3))
        shown = sum(1 for h in block["hunks"] for l in h["lines"]
                    if l["kind"] != "no_newline")
        self.assertEqual(shown, code_review.MAX_REVIEW_FILE_LINES)

    def test_a_very_long_line_is_clipped_and_says_by_how_much(self):
        self.repo_.write("src/util.py", "y" * 20000 + "\n")
        listing = self.overview()
        block = self.read(snapshot=listing["snapshot"],
                          file_id=self.files(listing)["src/util.py"]["file_id"]
                          )["file"]
        add = [l for h in block["hunks"] for l in h["lines"]
               if l["kind"] == "add"][0]
        self.assertEqual((len(add["text"]), add["clipped"]),
                         (code_review.MAX_REVIEW_LINE_CHARS,
                          20000 - code_review.MAX_REVIEW_LINE_CHARS))


class SubmoduleReadTests(ReviewCase):
    """A tracked submodule whose pointer moved, uncommitted work inside it,
    and an untracked nested repository."""

    def setUp(self):
        super(SubmoduleReadTests, self).setUp()
        self.repo_ = gitfx.Repo.__new__(gitfx.Repo)
        self.repo_.path = gitfx.submodule_workspace(self.root, "subws").path
        self.workspace = self.repo_.path
        self.answer = Answer(self.workspace, self.registration)

    def test_a_moved_pointer_is_listed_as_a_submodule_not_its_contents(self):
        files = self.files()
        self.assertEqual(sorted(files), ["vendor/clone", "vendor/lib"])
        lib = files["vendor/lib"]
        self.assertEqual((lib["change"], lib["staging"], lib["state"],
                          lib["reason"], lib["additions"], lib["deletions"]),
                         ("modified", "unstaged", "submodule", None, 1, 1))
        self.assertNotIn("vendor/lib/dirty.txt", files)
        self.assertEqual(self.overview()["overview"]["totals"], {
            "files": 2, "additions": 1, "deletions": 1, "withheld": 0,
            "unshown": 1})

    def test_the_pointer_diff_shows_the_old_and_new_commit(self):
        opened = self.opened("vendor/lib")
        self.assertEqual(opened["state"], "observed", opened["detail"])
        block = opened["file"]
        self.assertEqual(block["state"], "submodule")
        lines = [(l["kind"], l["text"]) for h in block["hunks"]
                 for l in h["lines"]]
        self.assertEqual([k for k, _t in lines], ["del", "add"])
        for _kind, text in lines:
            self.assertRegex(text, r"^Subproject commit [0-9a-f]{40}$")
        self.assertNotEqual(lines[0][1], lines[1][1])
        self.assertEqual((block["additions"], block["deletions"]), (1, 1))

    def test_a_nested_repository_is_one_entry_that_says_why_it_is_not_shown(
            self):
        entry = self.files()["vendor/clone"]
        self.assertEqual((entry["change"], entry["staging"], entry["state"],
                          entry["reason"], entry["additions"],
                          entry["deletions"]),
                         ("untracked", "untracked", "unsupported",
                          "nested_repository", None, None))
        opened = self.opened("vendor/clone")
        self.assertEqual(opened["state"], "observed", opened["detail"])
        self.assertEqual(opened["file"]["hunks"], [])
        self.assertEqual(opened["accounting"]["untracked_bytes"], 0,
                         "a directory is never read as a file")

    def test_a_submodule_that_moves_again_moves_the_snapshot(self):
        first = self.overview()["snapshot"]
        sub = gitfx.Repo.__new__(gitfx.Repo)
        sub.path = os.path.join(self.workspace, "vendor", "lib")
        sub.write("c.txt", "c\n")
        sub.stage("c.txt")
        sub.save("moved again")
        self.assertNotEqual(self.overview()["snapshot"], first)

    def test_nothing_in_the_superproject_or_the_submodule_is_written(self):
        sub = os.path.join(self.workspace, "vendor", "lib")
        before = (gitfx.digest(gitfx.git_state(self.workspace)),
                  gitfx.digest(gitfx.git_state(sub)), gitfx.tree_state(sub),
                  worktree_digest(self.workspace))
        listing = self.overview()
        for entry in listing["overview"]["files"]:
            self.read(snapshot=listing["snapshot"], file_id=entry["file_id"])
        self.assertEqual(
            (gitfx.digest(gitfx.git_state(self.workspace)),
             gitfx.digest(gitfx.git_state(sub)), gitfx.tree_state(sub),
             worktree_digest(self.workspace)), before)


class IndexBoundReadTests(ReviewCase):

    def test_an_index_over_the_copy_bound_refuses_with_its_exact_size(self):
        git_dir = self.repo_.git("rev-parse", "--path-format=absolute",
                                 "--absolute-git-dir").stdout.decode().strip()
        size = os.path.getsize(os.path.join(git_dir, "index"))
        with mock.patch.object(git_transport, "MAX_REVIEW_INDEX_BYTES",
                               size - 1):
            reply = self.read()
        self.assert_refused(reply, code_review.PROBLEM_INVENTORY_OVER_BOUND)
        self.assertIn("the index is %d bytes" % size, reply["detail"])
        self.assertIn("%d" % (size - 1), reply["detail"])


def invalid_pem(body):
    """A deliberately INVALID PEM: a BEGIN line and a placeholder body, no key material and no END line. Built at run
    time, so no private-key block sits in this source; the product must refuse to read it, which is what the test proves."""
    return "-----BEGIN " + "PRIVATE KEY-----\n" + body + "\n"


class PolicyReadTests(ReviewCase):

    def test_git_and_the_reader_are_never_asked_for_a_withheld_path(self):
        self.repo_.write("keys/server.pem", invalid_pem("S3CR3T"))
        self.repo_.stage("keys/server.pem")
        self.repo_.save("add key")
        self.repo_.write("keys/server.pem", invalid_pem("N3W"))
        recording = Recording(self.transport)
        reads = []
        real = state_artifact.read_state_artifact

        def spy(*args):
            reads.append(args)
            return real(*args)
        with mock.patch.object(state_artifact, "read_state_artifact", spy):
            listing = self.overview(transport=recording)
            for entry in listing["overview"]["files"]:
                self.read(snapshot=listing["snapshot"],
                          file_id=entry["file_id"], transport=recording)
        entry = self.files(listing)["keys/server.pem"]
        self.assertEqual((entry["state"], entry["reason"], entry["additions"],
                          entry["deletions"]),
                         ("withheld", "key_material_extension", None, None))
        asked = [p for _n, args, _k in recording.asked("live_file_diff")
                 for p in args[1]]
        self.assertNotIn("keys/server.pem", asked)
        self.assertFalse([r for r in reads if r[2] in (".env", "state.json")
                          or r[2] == "server.pem"], reads)
        self.assertNotIn("S3CR3T", json.dumps(listing))
        self.assertNotIn("hunter2", json.dumps(listing))

    def test_a_renamed_file_with_a_withheld_old_name_is_withheld(self):
        self.repo_.write("src/id_rsa_copy.py", "x\n")
        self.repo_.stage("src/id_rsa_copy.py")
        self.repo_.save("add")
        self.repo_.git("mv", "src/id_rsa_copy.py", "src/plain.py")
        entry = self.files()["src/plain.py"]
        self.assertEqual((entry["change"], entry["state"]),
                         ("renamed", "withheld"))


class NothingIsWrittenTests(ReviewCase):

    def test_the_overview_and_every_file_leave_the_workspace_untouched(self):
        git_before = gitfx.digest(gitfx.git_state(self.workspace))
        tree_before = worktree_digest(self.workspace)
        listing = self.overview()
        for entry in listing["overview"]["files"]:
            reply = self.read(snapshot=listing["snapshot"],
                              file_id=entry["file_id"])
            self.assertEqual(reply["state"], code_review.STATE_OBSERVED,
                             reply["detail"])
        self.assertEqual(gitfx.digest(gitfx.git_state(self.workspace)),
                         git_before, "the target's whole git state")
        self.assertEqual(worktree_digest(self.workspace), tree_before,
                         "every file in the workspace")

    def test_the_linked_worktree_a_mission_works_in_is_untouched_too(self):
        linked = self.busy(name="mission", linked=True)
        self.answer.mission["execution"]["workspace_realpath"] = linked.path
        before = gitfx.digest(gitfx.git_state(linked.path))
        tree_before = worktree_digest(linked.path)
        listing = self.overview()
        for entry in listing["overview"]["files"]:
            self.read(snapshot=listing["snapshot"], file_id=entry["file_id"])
        self.assertEqual(gitfx.digest(gitfx.git_state(linked.path)), before)
        self.assertEqual(worktree_digest(linked.path), tree_before)
        self.assertEqual(listing["target"]["branch"], None)
        self.assertTrue(listing["target"]["detached"])

    def test_the_registry_is_not_written(self):
        def registry_bytes():
            with open(self.registry.path, "rb") as handle:
                return handle.read()
        before = registry_bytes()
        self.overview()
        self.assertEqual(registry_bytes(), before)


if __name__ == "__main__":
    unittest.main()
