"""The repository-local Git authority gates, each tested against what it
actually stops. None of this is an enforced boundary: these gates are
workflow guardrails, not designed to contain processes running with the
user's own privileges.

- S1, the SEPARATE human merge gate, kinds kept apart: a merge approval
  authorizes exactly one local ``git merge`` of the exact source commit it
  names into the branch and HEAD it was granted for. Each layer checks what
  it can establish where it runs: the pretool check binds the source from
  the command's arguments (and refuses any repository redirection); the
  ``pre-merge-commit`` hook checks ONLY that an approval exists for this
  destination, because it runs before ``MERGE_HEAD`` is written (upstream
  source inference, not a live test); ``pre-commit`` checks ``MERGE_HEAD``
  for a conflicted merge completed by commit; the ``reference-transaction``
  hook requires the actual update to BE the approved merge, retires the
  approval on any mismatch and consumes it when the update is committed.
  ``gh pr create``, ``gh pr merge``, ``git pull`` and (conservative
  over-refusal) ``gh api`` naming pulls or merges have NO approval kind and
  are refused outright, a merge approval included. A commit, push or Mission
  approval never satisfies the merge gate.
- S2, typed confirmation only at the controlling terminal: ``--yes`` is
  gone, a piped answer confirms nothing, no terminal confirms nothing. That
  is ALL it stops, so it is not proof that a human confirmed.
- S3, the approval ledger: TAMPER-EVIDENCE against NON-ADVERSARIAL change,
  never described or tested as a control. Its
  read-and-append, and every mint and retirement, are ONE serialized
  operation (``guards.ledger_lock``), and a human mint recovers a broken
  chain by archiving a copy and atomically publishing a new chain (the
  broken one stays in force until then, so a failed recovery still
  refuses).
- S4, a Mission approval never confers delivery authority.

Non-delivering: every decision is exercised through parsing, refusal paths
and fixture state; nothing commits, pushes, merges or opens a pull request.

Hermetic BY CONSTRUCTION: ``NoProcess`` makes every process entry point
raise (the concurrency tests use THREADS, and a separate open of the lock
file stands in for another process, since ``flock`` locks belong to an open
file description); Git identity (``repo_identity``), the guard's own ``run`` (which
answers only the read-only Git questions the guard asks) and herdctl's
registry and repository resolution are substitutes, so no git process starts
and no user-global registry or configuration is read. Every write is inside
a per-test temporary directory. A SIGALRM watchdog bounds every test
(``Bounded``).
"""

import contextlib
import fcntl
import io
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import unittest  # noqa: E402

import herdctl  # noqa: E402
from herdr import guards  # noqa: E402

from test_grok_bot import Fixture  # noqa: E402
from test_grok_bot_boundaries import Bounded, NoProcess  # noqa: E402

IDENTITY = {"repo_root": None, "git_dir": None, "branch": "feature/x",
            "head": "a" * 40, "remote": "https://github.com/o/r.git",
            "staged_sha256": "b" * 64}
SOURCE = "topic"
JOIN_SECONDS = 10
SOURCE_COMMIT = "c" * 40
OTHER_COMMIT = "d" * 40
# Shell command texts the pretool guard judges. They are DATA here: the
# guard only parses them and nothing ever runs them.
COMMIT_TEXT = "git " + "commit -m x"
PUSH_TEXT = "git " + "push origin feature/x"


def pretool_decision(repo, command):
    """The pretool guard's decision on one Bash command text in ``repo``:
    ``(exit code, stderr)``. The command is parsed, never run."""
    stdin = io.StringIO(json.dumps({"tool_name": "Bash", "cwd": str(repo),
                                    "tool_input": {"command": command}}))
    errors = io.StringIO()
    with mock.patch.object(sys, "stdin", stdin), \
            mock.patch.object(sys, "stderr", errors):
        code = guards.guard_pretool()
    return code, errors.getvalue()


class FakeTerminal(io.StringIO):
    """The controlling terminal, answering ``answer``."""

    def __init__(self, answer):
        super(FakeTerminal, self).__init__()
        self.answer = answer
        self.prompts = []

    def write(self, text):
        self.prompts.append(text)
        return len(text)

    def readline(self):
        return self.answer + "\n"


class GateFixture(Bounded):

    def setUp(self):
        super(GateFixture, self).setUp()
        self.repo = Path(self.base) / "repo"
        (self.repo / ".herd" / "state").mkdir(parents=True)
        (self.repo / ".herd" / guards.CFG).write_text("{}")
        self.identity = dict(IDENTITY, repo_root=str(self.repo),
                             git_dir=str(self.repo / ".git"))
        for owner, name, value in (
            (guards, "repo_identity", lambda repo: dict(self.identity)),
            (guards, "run", self.fake_run),
            (herdctl, "repo_identity", lambda repo: dict(self.identity)),
            (herdctl, "resolve_repo_ref", lambda ref=None: self.repo),
            (herdctl, "repo_alias", lambda repo: "demo"),
            (herdctl, "registry_load", lambda: {"repos": {}}),
            (herdctl, "cfg", lambda repo: {"project": {"name": "demo"}}),
            (herdctl, "gitout", lambda *a, **k: ""),
        ):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.terminal = None
        patcher = mock.patch.object(guards, "_controlling_terminal",
                                    self.open_terminal)
        patcher.start()
        self.addCleanup(patcher.stop)
        # What revisions resolve to, the merge in progress (MERGE_HEAD), and
        # commits' parents. MERGE_HEAD is ABSENT by default: in upstream Git
        # the pre-merge-commit hook of a fresh automatic merge runs before it
        # is written (the fixture models that order; it is not a live test).
        self.commits = {SOURCE: SOURCE_COMMIT, "other": OTHER_COMMIT}
        self.merge_head = None
        self.parents = {}
        self.ancestors = set()
        # pr_delivery's receipt path would read its store under the user's
        # home; this fixture answers "no receipt" instead.
        patcher = mock.patch.object(guards, "_delivery_receipt_decision",
                                    lambda *a, **k: (False, "no receipt (fixture)"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, argv, *args, **kwargs):
        """The guard's only Git questions, all read-only, ANSWERED here (no
        git process runs): the toplevel, the branch HEAD names, a revision's
        commit, MERGE_HEAD, and a commit's parents."""
        tail = argv[3:] if argv[:2] == ["git", "-C"] else argv[1:]
        answer, code = "", 0
        if tail == ["rev-parse", "--show-toplevel"]:
            answer = str(self.repo)
        elif tail == ["symbolic-ref", "-q", "HEAD"]:
            answer = "refs/heads/" + self.identity["branch"]
        elif tail[:2] == ["merge-base", "--is-ancestor"]:
            code = 0 if (tail[2], tail[3]) in self.ancestors else 1
        elif tail[:4] == ["rev-list", "--parents", "-n", "1"]:
            oid = tail[4]
            answer = " ".join([oid] + self.parents[oid]) if oid in self.parents else ""
            code = 0 if answer else 1
        elif tail[:3] == ["rev-parse", "--verify", "--quiet"]:
            revision = tail[3]
            if revision == "MERGE_HEAD":
                answer = self.merge_head or ""
            else:
                answer = self.commits.get(revision.replace("^{commit}", ""), "")
            code = 0 if answer else 1
        else:
            raise AssertionError("unexpected git question: %r" % (argv,))
        return subprocess.CompletedProcess(argv, code, stdout=answer + "\n",
                                           stderr="")

    def open_terminal(self):
        if self.terminal is None:
            raise OSError("no controlling terminal")
        return self.terminal

    def pretool(self, command):
        return pretool_decision(self.repo, command)

    def approve(self, function, answer="demo", **extra):
        self.terminal = FakeTerminal(answer)
        extra.setdefault("source", SOURCE)
        args = mock.Mock(repo=None, ttl=600, **extra)
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            function(args)
        return out.getvalue()


# ====================================================================
# S1: the separate human merge gate, kinds kept apart
# ====================================================================

LOCAL_MERGES = (
    ("git merge topic", ["topic"]), ("/usr/bin/git merge topic", ["topic"]),
    ("git -C . merge --no-ff -m 'Merge it' topic", ["topic"]),
    ("git -c core.hooksPath=/dev/null merge topic", ["topic"]),
    ("sh -c 'git merge topic'", ["topic"]),
    ("cd repo && git merge topic", ["topic"]),
    ("git merge", []), ("git merge topic other", ["topic", "other"]),
)
NO_APPROVAL_KIND = (
    "gh pr create --fill", "/opt/homebrew/bin/gh pr create",
    'bash -lc "gh pr create --fill"', "gh pr merge 12 --squash",
    "echo ok; gh pr merge 3", "git pull", "git pull --ff-only",
    "git --no-pager pull origin main",
    "gh api repos/o/r/pulls -f title=t", "gh api repos/o/r/pulls",
    "gh api -X PUT repos/o/r/pulls/1/merge",
)
NOT_SHAPED = (
    "git merge-base a b", "git log --merges", "git config pull.rebase true",
    "git status", "echo merge", "gh pr view 1", "gh issue create",
    "git fetch origin", "git branch -a",
)


class MergeGateTests(GateFixture):

    def test_kinds_are_recognised_and_kept_apart(self):
        for command, sources in LOCAL_MERGES:
            with self.subTest(command=command):
                self.assertEqual(guards.delivery_kinds(command),
                                 [(guards.LOCAL_MERGE, sources)])
        for command in NO_APPROVAL_KIND:
            with self.subTest(command=command):
                kinds = [kind for kind, _ in guards.delivery_kinds(command)]
                self.assertTrue(kinds)
                self.assertTrue(set(kinds) <= set(guards.NO_APPROVAL_KIND))
        for command in NOT_SHAPED:
            with self.subTest(command=command):
                self.assertEqual(guards.delivery_kinds(command), [])

    def test_a_local_merge_is_refused_without_a_merge_approval(self):
        for command, _ in LOCAL_MERGES:
            with self.subTest(command=command):
                code, errors = self.pretool(command)
                self.assertEqual(code, 2, errors)
                self.assertIn("Merge blocked", errors)

    def test_a_merge_approval_opens_no_other_kind(self):
        """PR creation, a remote PR merge, git pull and gh api calls naming
        pulls (reads included) are refused for want of an approval kind,
        with a valid merge approval in place."""
        self.approve(herdctl.approve_merge)
        for command in NO_APPROVAL_KIND:
            with self.subTest(command=command):
                code, errors = self.pretool(command)
                self.assertEqual(code, 2, errors)
                self.assertIn("No approval opens it", errors)
        self.assertEqual(self.pretool("git merge topic")[0], 0)

    def test_no_other_approval_satisfies_the_merge_gate(self):
        """A commit approval and a push approval (each valid for its own
        gate) and a merge-path record of another kind never open it."""
        self.approve(herdctl.approve_commit)
        self.assertTrue(guards.approval_valid(self.repo)[0])
        guards.push_approval_path(self.repo).write_text(json.dumps(
            dict(self.identity, kind="push", expires_at=2 ** 31)))
        guards.merge_approval_path(self.repo).write_text(json.dumps(
            dict(self.identity, kind="commit", operation="local_merge",
                 source_commit=SOURCE_COMMIT, expires_at=2 ** 31)))
        code, errors = self.pretool("git merge topic")
        self.assertEqual(code, 2, errors)

    def test_a_merge_approval_binds_the_exact_source(self):
        """A substituted merge never consumes it: another source, a source
        that now resolves to another commit, two sources, or none."""
        for label, command, commits in (
            ("another source", "git merge other", None),
            ("the source moved", "git merge topic", {SOURCE: OTHER_COMMIT}),
            ("two sources", "git merge topic other", None),
            ("no source", "git merge", None),
        ):
            with self.subTest(case=label):
                self.commits = {SOURCE: SOURCE_COMMIT, "other": OTHER_COMMIT}
                self.approve(herdctl.approve_merge)
                if commits:
                    self.commits.update(commits)
                code, errors = self.pretool(command)
                self.assertEqual(code, 2, errors)

    def test_a_merge_approval_is_bound_to_the_destination(self):
        for key, value in (("head", "e" * 40), ("branch", "main")):
            with self.subTest(changed=key):
                self.approve(herdctl.approve_merge)
                self.identity[key] = value
                valid, message = guards.merge_approval_valid(
                    self.repo, source_commit=SOURCE_COMMIT)
                self.assertFalse(valid)
                self.assertIn(key, message)
                self.identity = dict(IDENTITY, repo_root=str(self.repo),
                                     git_dir=str(self.repo / ".git"))

    def reference(self, phase, old, new):
        """The ``reference-transaction`` hook for one branch update."""
        line = "%s %s refs/heads/%s\n" % (old, new, self.identity["branch"])
        errors = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(line)), \
                mock.patch.object(sys, "stderr", errors):
            code = guards.guard_reference_transaction(self.repo, phase)
        return code, errors.getvalue()

    def premerge(self):
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            return guards.guard_premerge(self.repo), errors.getvalue()

    def commit_ref_update(self, new, phase_identity_head=None):
        """Prepared, then (HEAD moved) committed, for ``head`` -> ``new``."""
        prepared = self.reference("prepared", IDENTITY["head"], new)
        if prepared[0] == 0:
            self.identity["head"] = phase_identity_head or new
            self.reference("committed", IDENTITY["head"], new)
        return prepared

    def test_a_fresh_automatic_merge_succeeds_in_the_real_hook_order(self):
        """Pretool (sees the source), then ``pre-merge-commit`` with NO
        MERGE_HEAD yet (upstream Git's order), then the ref update of a commit
        whose parents are exactly (approved HEAD, approved source), then
        committed: allowed throughout, and the approval is consumed once."""
        self.approve(herdctl.approve_merge)
        merge_commit = "f" * 40
        self.parents[merge_commit] = [IDENTITY["head"], SOURCE_COMMIT]
        self.assertEqual(self.pretool("git merge topic")[0], 0)
        self.assertIsNone(self.merge_head)
        self.assertEqual(self.premerge(), (0, ""))
        self.assertEqual(self.commit_ref_update(merge_commit)[0], 0)
        self.assertFalse(guards.merge_approval_path(self.repo).exists())
        self.identity["head"] = IDENTITY["head"]
        self.assertEqual(self.premerge()[0], 1)
        self.assertEqual(self.reference("prepared", IDENTITY["head"],
                                        merge_commit)[0], 1)

    def make_fast_forward(self):
        """The approved source is a descendant of the approved HEAD, two
        commits ahead (so the update is identifiable as a fast-forward)."""
        middle = "9" * 40
        self.parents[SOURCE_COMMIT] = [middle]
        self.parents[middle] = [IDENTITY["head"]]
        self.ancestors.add((IDENTITY["head"], SOURCE_COMMIT))

    def test_an_approved_fast_forward_succeeds_and_is_consumed(self):
        self.make_fast_forward()
        self.approve(herdctl.approve_merge)
        self.assertEqual(self.commit_ref_update(SOURCE_COMMIT)[0], 0)
        self.assertFalse(guards.merge_approval_path(self.repo).exists())

    def test_a_move_to_a_source_that_is_not_a_descendant_is_refused(self):
        """Finding B: ``new == source`` is a fast-forward only when the
        approved HEAD is an ancestor of the source. Otherwise the move would
        discard HEAD's commits, which is not the approved merge. Such a move
        is not identifiable as a merge at all (one new commit, not a
        descendant), so the merge gate never judges it: the commit gate
        refuses it, and the merge approval, never offered for it, is not
        consumed."""
        self.parents[SOURCE_COMMIT] = ["8" * 40]
        self.approve(herdctl.approve_merge)
        code, errors = self.reference("prepared", IDENTITY["head"], SOURCE_COMMIT)
        self.assertEqual(code, 1, errors)
        self.assertTrue(guards.merge_approval_path(self.repo).exists())
        token = json.loads(guards.merge_approval_path(self.repo).read_text())
        self.assertFalse(guards._is_approved_merge_update(
            token, IDENTITY["head"], SOURCE_COMMIT, self.repo))
        self.ancestors.add((IDENTITY["head"], SOURCE_COMMIT))
        self.assertTrue(guards._is_approved_merge_update(
            token, IDENTITY["head"], SOURCE_COMMIT, self.repo))

    def test_a_valid_commit_approval_never_authorizes_a_merge(self):
        """Finding A: with a VALID commit approval and NO merge approval, an
        identifiable merge is still refused at both layers outside the
        pretool: a conflicted merge completed by commit (MERGE_HEAD present),
        a merge-commit ref update, and a multi-commit fast-forward. An
        ordinary commit is still judged by the commit gate alone."""
        self.approve(herdctl.approve_commit)
        self.assertTrue(guards.approval_valid(self.repo)[0])
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            self.merge_head = SOURCE_COMMIT
            self.assertEqual(guards.guard_precommit(self.repo), 1)
        self.assertIn("a commit approval never authorizes a merge",
                      errors.getvalue())
        merge_commit = "f" * 40
        self.parents[merge_commit] = [IDENTITY["head"], SOURCE_COMMIT]
        self.make_fast_forward()
        for label, new in (("merge commit", merge_commit),
                           ("multi-commit fast-forward", SOURCE_COMMIT)):
            with self.subTest(update=label):
                code, message = self.reference("prepared", IDENTITY["head"], new)
                self.assertEqual(code, 1, message)
                self.assertIn("a commit approval never authorizes a merge",
                              message)
        # The commit gate is intact for an ordinary commit.
        self.merge_head = None
        ordinary = "7" * 40
        self.parents[ordinary] = [IDENTITY["head"]]
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(guards.guard_precommit(self.repo), 0)
        self.assertEqual(self.reference("prepared", IDENTITY["head"],
                                        ordinary)[0], 0)

    def test_the_ref_update_refuses_every_substituted_merge(self):
        """Whatever path reached it (no pretool here), the branch update must
        BE the approved merge: another source, a fast-forward to another
        commit, a different first parent, an extra parent, or a move from
        another HEAD is refused. Round 4 (E11): the mismatch RETIRES the
        approval, as an identity mismatch does at every other gate, so even a
        correctly shaped retry of the approved merge then needs a FRESH
        approval."""
        merge_commit, approved = "f" * 40, "1" * 40
        for label, old, new, parents in (
            ("merge of another source", IDENTITY["head"], merge_commit,
             [IDENTITY["head"], OTHER_COMMIT]),
            ("fast-forward to another commit", IDENTITY["head"], OTHER_COMMIT,
             None),
            ("octopus with the source", IDENTITY["head"], merge_commit,
             [IDENTITY["head"], SOURCE_COMMIT, OTHER_COMMIT]),
            ("parents reversed", IDENTITY["head"], merge_commit,
             [SOURCE_COMMIT, IDENTITY["head"]]),
            ("from another HEAD", "e" * 40, merge_commit,
             [IDENTITY["head"], SOURCE_COMMIT]),
        ):
            with self.subTest(case=label):
                # The other commit is two ahead of HEAD, so a move to it is
                # identifiable as a (substituted) fast-forward.
                self.parents = {OTHER_COMMIT: ["9" * 40],
                                "9" * 40: [IDENTITY["head"]],
                                approved: [IDENTITY["head"], SOURCE_COMMIT]}
                self.ancestors = {(IDENTITY["head"], OTHER_COMMIT)}
                if parents:
                    self.parents[merge_commit] = parents
                self.approve(herdctl.approve_merge)
                code, errors = self.reference("prepared", old, new)
                self.assertEqual(code, 1, errors)
                self.assertIn("BLOCKED", errors)
                self.assertFalse(guards.merge_approval_path(self.repo).exists())
                retry, message = self.reference("prepared", IDENTITY["head"],
                                                approved)
                self.assertEqual(retry, 1, message)
                self.assertIn("No merge approval exists", message)
                self.approve(herdctl.approve_merge)
                self.assertEqual(self.reference("prepared", IDENTITY["head"],
                                                approved)[0], 0)
                guards.retire_approval(guards.merge_approval_path(self.repo),
                                       self.repo, guards.KIND_MERGE,
                                       "invalidate")

    def test_without_a_merge_approval_both_hooks_refuse(self):
        merge_commit = "f" * 40
        self.parents[merge_commit] = [IDENTITY["head"], SOURCE_COMMIT]
        self.assertEqual(self.premerge()[0], 1)
        self.assertEqual(self.reference("prepared", IDENTITY["head"],
                                        merge_commit)[0], 1)

    def test_a_conflicted_merge_completed_by_commit_is_checked_at_pre_commit(self):
        """githooks: a conflicted merge completed separately runs
        ``pre-commit``, not ``pre-merge-commit``. MERGE_HEAD exists then, so
        the source is checked there; the ref update still decides."""
        self.approve(herdctl.approve_merge)
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            self.merge_head = OTHER_COMMIT
            self.assertEqual(guards.guard_precommit(self.repo), 1)
            self.approve(herdctl.approve_merge)
            self.merge_head = SOURCE_COMMIT
            self.assertEqual(guards.guard_precommit(self.repo), 0)
        resolved = "f" * 40
        self.parents[resolved] = [IDENTITY["head"], SOURCE_COMMIT]
        self.assertEqual(self.commit_ref_update(resolved)[0], 0)
        self.assertFalse(guards.merge_approval_path(self.repo).exists())

    def guard_command_of(self, hook_name):
        """The guards subcommand and arguments the INSTALLED hook script
        ``hook_name`` invokes. The script is parsed, never run (no process
        starts); its command is then dispatched through ``guards.main``."""
        text = (self.repo / ".git" / "hooks" / hook_name).read_text()
        for line in text.splitlines():
            if " -m herdr.guards " in line:
                return shlex.split(line.split(" -m herdr.guards ", 1)[1])
        self.fail("the %s hook invokes no guard" % hook_name)

    def through_the_hook(self, hook_name, phase=None, stdin=""):
        """Run exactly what the installed hook would run, through the guards
        command line: ``(exit code, stderr)``."""
        argv = [{"$ROOT": str(self.repo), "$1": phase}.get(item, item)
                for item in self.guard_command_of(hook_name)]
        errors = io.StringIO()
        with mock.patch.object(sys, "argv", ["herdr-guards"] + argv), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", errors):
            with self.assertRaises(SystemExit) as caught:
                guards.main()
        return caught.exception.code, errors.getvalue()

    def test_each_installed_hook_performs_the_check_documented_for_its_layer(self):
        """Round 4 (E23): EXECUTABLE wiring, not prose. The hooks are
        installed; each one's guard command is dispatched exactly as the
        hook would dispatch it; and each layer is shown doing the check the
        documentation attributes to it, and not more."""
        def gitout(repo, *args, **kwargs):
            return ".git/hooks/%s" % args[-1].split("/")[-1]
        with mock.patch.object(guards, "gitout", gitout):
            guards.install_git_guard(self.repo)
        self.assertEqual(
            [self.guard_command_of(name)[0] for name in
             ("pre-merge-commit", "pre-commit", "reference-transaction")],
            ["premerge", "precommit", "reference"])
        token = guards.merge_approval_path(self.repo)
        merge_commit, other_merge = "f" * 40, "1" * 40
        self.parents = {merge_commit: [IDENTITY["head"], SOURCE_COMMIT],
                        other_merge: [IDENTITY["head"], OTHER_COMMIT]}
        update = "%s %%s refs/heads/%s\n" % (IDENTITY["head"],
                                             self.identity["branch"])

        # pre-merge-commit: an approval for this destination, nothing more.
        # It does not consume it, and it does not judge the source (it cannot
        # see one for a fresh merge, so even a MERGE_HEAD is not consulted).
        self.assertEqual(self.through_the_hook("pre-merge-commit")[0], 1)
        self.approve(herdctl.approve_merge)
        self.merge_head = OTHER_COMMIT
        self.assertEqual(self.through_the_hook("pre-merge-commit")[0], 0)
        self.assertTrue(token.exists())
        self.merge_head = None
        self.identity["head"] = "e" * 40
        self.assertEqual(self.through_the_hook("pre-merge-commit")[0], 1)
        self.assertFalse(token.exists())
        self.identity["head"] = IDENTITY["head"]

        # pre-commit: MERGE_HEAD is checked against the approved source.
        self.approve(herdctl.approve_merge)
        self.merge_head = OTHER_COMMIT
        code, errors = self.through_the_hook("pre-commit")
        self.assertEqual(code, 1, errors)
        self.assertFalse(token.exists())
        self.approve(herdctl.approve_merge)
        self.merge_head = SOURCE_COMMIT
        self.assertEqual(self.through_the_hook("pre-commit")[0], 0)
        self.assertTrue(token.exists())
        self.merge_head = None

        # reference-transaction: the update must BE the approved merge; a
        # mismatch retires the approval, the approved one is consumed when
        # committed.
        code, errors = self.through_the_hook(
            "reference-transaction", "prepared", update % other_merge)
        self.assertEqual(code, 1, errors)
        self.assertFalse(token.exists())
        self.approve(herdctl.approve_merge)
        self.assertEqual(self.through_the_hook(
            "reference-transaction", "prepared", update % merge_commit)[0], 0)
        self.assertTrue(token.exists())
        self.assertEqual(self.through_the_hook(
            "reference-transaction", "committed", update % merge_commit)[0], 0)
        self.assertFalse(token.exists())

        # The pretool check (a Claude Code hook, not a Git hook) through the
        # same command line: the source is bound from the arguments.
        self.approve(herdctl.approve_merge)
        for command, expected in (("git merge other", 2),
                                  ("git merge topic", 0)):
            payload = json.dumps({"tool_name": "Bash", "cwd": str(self.repo),
                                  "tool_input": {"command": command}})
            errors = io.StringIO()
            with mock.patch.object(sys, "argv", ["herdr-guards", "pretool"]), \
                    mock.patch.object(sys, "stdin", io.StringIO(payload)), \
                    mock.patch.object(sys, "stderr", errors):
                with self.assertRaises(SystemExit) as caught:
                    guards.main()
            self.assertEqual(caught.exception.code, expected, errors.getvalue())
            if expected:
                self.approve(herdctl.approve_merge)

    def test_the_layer_documentation_is_pinned(self):
        """A DOCUMENTATION PIN only: it checks that the prose describing the
        layers says what it should. It proves nothing about behaviour; the
        behavioural evidence is
        ``test_each_installed_hook_performs_the_check_documented_for_its_layer``,
        ``test_a_fresh_automatic_merge_succeeds_in_the_real_hook_order``,
        ``test_a_conflicted_merge_completed_by_commit_is_checked_at_pre_commit``
        and ``test_the_ref_update_refuses_every_substituted_merge``."""
        doc = " ".join(guards.__doc__.split())
        for phrase in ("cannot see the source for a fresh automatic merge",
                       "source-based inference, not a test of the installed Git",
                       "checks only that an approval exists for this destination",
                       "checks ``MERGE_HEAD`` against the approved source",
                       "parents are exactly the approved HEAD and the approved"):
            self.assertIn(phrase, doc)
        premerge = " ".join(guards.guard_premerge.__doc__.split())
        self.assertIn("does NOT consume it", premerge)

    def test_the_approval_display_names_what_merges_into_what(self):
        out = self.approve(herdctl.approve_merge)
        for value in (SOURCE, SOURCE_COMMIT, self.identity["branch"],
                      self.identity["head"], "no pull request"):
            self.assertIn(value, out)
        token = json.loads(guards.merge_approval_path(self.repo).read_text())
        self.assertEqual((token["kind"], token["operation"],
                          token["source_commit"]),
                         ("merge", "local_merge", SOURCE_COMMIT))

    def test_an_unresolvable_source_is_never_approved(self):
        with self.assertRaises(SystemExit):
            self.approve(herdctl.approve_merge, source="nowhere")
        self.assertFalse(guards.merge_approval_path(self.repo).exists())

    def test_the_merge_commit_hook_is_installed(self):
        def gitout(repo, *args, **kwargs):
            return ".git/hooks/%s" % args[-1].split("/")[-1]
        with mock.patch.object(guards, "gitout", gitout):
            guards.install_git_guard(self.repo)
        hook = self.repo / ".git" / "hooks" / "pre-merge-commit"
        self.assertIn("HERD MERGE GUARD", hook.read_text())
        self.assertIn("premerge --repo-path", hook.read_text())


class RepositoryRedirectionTests(GateFixture):
    """Round 4 (E16). The pretool judges a merge against the repository of
    the payload's working directory (A). A command that runs the merge in
    ANOTHER repository (B) would be judged with A's identity and A's
    approval: a WRONG PRETOOL DECISION. That is what is fixed here. It is not
    a bypass of B's own independently installed hooks, which judge B's own
    update. Every form of redirection is refused conservatively, as
    ``git -C`` already is for commit and push."""

    def setUp(self):
        super(RepositoryRedirectionTests, self).setUp()
        self.repo_b = Path(self.base) / "other-repo"
        (self.repo_b / ".herd" / "state").mkdir(parents=True)
        (self.repo_b / ".herd" / guards.CFG).write_text("{}")
        self.identity_b = dict(IDENTITY, repo_root=str(self.repo_b),
                               git_dir=str(self.repo_b / ".git"),
                               branch="main", head="e" * 40)
        self.commits_b = {SOURCE: OTHER_COMMIT}
        patcher = mock.patch.object(guards, "repo_identity", self.identity_of)
        patcher.start()
        self.addCleanup(patcher.stop)

    def identity_of(self, repo):
        if Path(repo).resolve() == self.repo_b.resolve():
            return dict(self.identity_b)
        return dict(self.identity)

    def fake_run(self, argv, *args, **kwargs):
        """B's answers when a question names B; A's (the fixture's) else."""
        if argv[:3] != ["git", "-C", str(self.repo_b)]:
            return super(RepositoryRedirectionTests, self).fake_run(
                argv, *args, **kwargs)
        tail, answer = argv[3:], ""
        if tail == ["rev-parse", "--show-toplevel"]:
            answer = str(self.repo_b)
        elif tail[:3] == ["rev-parse", "--verify", "--quiet"]:
            answer = self.commits_b.get(tail[3].replace("^{commit}", ""), "")
        else:
            raise AssertionError("unexpected git question: %r" % (argv,))
        return subprocess.CompletedProcess(argv, 0 if answer else 1,
                                           stdout=answer + "\n", stderr="")

    def test_a_redirected_merge_is_refused_not_judged_against_the_wrong_repository(self):
        self.approve(herdctl.approve_merge)
        self.assertEqual(self.pretool("git merge topic")[0], 0)
        # The two repositories differ in exactly what the decision rests on:
        # what ``topic`` is, where it merges into, and whether it is approved.
        self.assertEqual(guards.resolve_commit(self.repo, SOURCE), SOURCE_COMMIT)
        self.assertEqual(guards.resolve_commit(self.repo_b, SOURCE), OTHER_COMMIT)
        self.assertNotEqual(guards.merge_identity(self.repo),
                            guards.merge_identity(self.repo_b))
        self.assertFalse(guards.merge_approval_valid(
            self.repo_b, source_commit=OTHER_COMMIT)[0])
        b = str(self.repo_b)
        for command in (
            "git -C %s merge topic" % b,
            "git --git-dir=%s/.git merge topic" % b,
            "git --git-dir %s/.git --work-tree %s merge topic" % (b, b),
            "git --work-tree=%s merge topic" % b,
            "cd %s && git merge topic" % b,
            "cd %s; git merge topic" % b,
            "(cd %s && git merge topic)" % b,
            "GIT_DIR=%s/.git git merge topic" % b,
            "env GIT_DIR=%s/.git git merge topic" % b,
            "sh -c 'git -C %s merge topic'" % b,
            "bash -lc 'cd %s && git merge topic'" % b,
            "git -c core.hooksPath=/dev/null merge topic",
            "git merge $(echo topic)",
            "git merge `echo topic`",
        ):
            with self.subTest(command=command):
                code, errors = self.pretool(command)
                self.assertEqual(code, 2, errors)
                self.assertIn("Merge blocked", errors)
                # Refused before any approval is evaluated: A's approval is
                # neither used nor retired by a redirection attempt.
                self.assertTrue(guards.merge_approval_path(self.repo).exists())
        self.assertEqual(self.pretool(
            "git merge --no-ff -m 'Merge topic; reviewed' topic")[0], 0)


# ====================================================================
# S3 (round 4): the ledger is serialized, and re-authorizing recovers it
# ====================================================================


class LedgerSerializationTests(GateFixture):
    """Two legitimate writers must never chain the same sequence number:
    the ledger's read and append, every mint (record and entry) and every
    retirement (read, removal and entry) are ONE operation under
    ``guards.ledger_lock``, a blocking ``flock`` (the discipline of
    ``RequestIndex.serialized``). Threads stand in for writers; a SEPARATE
    open of the lock file stands in for another process."""

    @contextlib.contextmanager
    def slow_reads(self, delay=0.2):
        """Widen the read-then-append window, so an unserialized writer
        would deterministically chain on a stale read."""
        original = guards._ledger_entries

        def slow(repo):
            entries = original(repo)
            time.sleep(delay)
            return entries
        with mock.patch.object(guards, "_ledger_entries", slow):
            yield

    def run_together(self, *calls):
        failures = []

        def wrapped(call):
            try:
                call()
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)
        threads = [threading.Thread(target=wrapped, args=(call,))
                   for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(JOIN_SECONDS)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def chain(self):
        return guards._ledger_entries(self.repo)

    def test_concurrent_appends_chain_one_after_the_other(self):
        with self.slow_reads():
            self.run_together(*[
                (lambda digest=digest: guards.ledger_append(
                    self.repo, "mint", guards.KIND_COMMIT, digest,
                    expires_at=2 ** 31, create=True))
                for digest in ("1" * 64, "2" * 64, "3" * 64)])
        entries = self.chain()
        self.assertEqual([entry["seq"] for entry in entries], [1, 2, 3])
        self.assertEqual(sorted(entry["token_sha256"] for entry in entries),
                         ["1" * 64, "2" * 64, "3" * 64])

    def test_a_mint_racing_a_retirement_keeps_one_chain(self):
        self.approve(herdctl.approve_commit)
        commit = guards.approval_path(self.repo)
        merge = dict(self.identity, kind=guards.KIND_MERGE,
                     operation=guards.MERGE_OPERATION_LOCAL, source_ref=SOURCE,
                     source_commit=SOURCE_COMMIT,
                     expires_at=int(time.time()) + 600)
        with self.slow_reads():
            self.run_together(
                lambda: herdctl._mint_approval(
                    self.repo, guards.merge_approval_path(self.repo), merge,
                    guards.KIND_MERGE),
                lambda: guards.retire_approval(
                    commit, self.repo, guards.KIND_COMMIT, "consume"))
        events = [(entry["event"], entry["kind"]) for entry in self.chain()]
        self.assertEqual(events[0], ("mint", guards.KIND_COMMIT))
        self.assertEqual(sorted(events[1:]), [("consume", guards.KIND_COMMIT),
                                              ("mint", guards.KIND_MERGE)])
        self.assertTrue(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)[0])

    def lock_depth(self):
        """How deeply THIS process holds the repository's ledger lock now."""
        held = guards._LEDGER_LOCKS.get(
            str(guards.hroot(self.repo) / guards.LEDGER_LOCK))
        return held[1] if held else 0

    def test_a_mint_holds_the_lock_across_its_record_and_its_entry(self):
        """Round 6 follow-up: the mint's OUTER lock is pinned. ``ledger_append``
        locks itself, so chain integrity alone would not notice a mint whose
        record write ran outside the lock. Here:

        - STRUCTURE: when the mint's ``ledger_append`` is entered, the lock is
          ALREADY held and the record is already written; it is still held
          when the append returns; and inside it, the chain READ runs under
          the lock too;
        - BEHAVIOUR: a retirement of the same record, started the moment the
          record is written, must wait for the mint's entry. Otherwise it
          would record a ``consume`` for a digest not yet minted, and the
          later mint entry would leave a false OUTSTANDING approval that is
          missing (tamper evidence on a correct history)."""
        merge = dict(self.identity, kind=guards.KIND_MERGE,
                     operation=guards.MERGE_OPERATION_LOCAL, source_ref=SOURCE,
                     source_commit=SOURCE_COMMIT,
                     expires_at=int(time.time()) + 600)
        record = guards.merge_approval_path(self.repo)
        observed, written = {}, threading.Event()
        original_append, original_read = (guards.ledger_append,
                                          guards._ledger_entries)

        def append(repo, event, *args, **kwargs):
            if event != "mint":
                return original_append(repo, event, *args, **kwargs)
            observed["depth_at_entry"] = self.lock_depth()
            observed["record_at_entry"] = record.exists()
            written.set()
            time.sleep(0.3)
            original_append(repo, event, *args, **kwargs)
            observed["depth_after_append"] = self.lock_depth()

        def read(repo):
            observed.setdefault("depth_during_read", self.lock_depth())
            return original_read(repo)

        def retire():
            written.wait(JOIN_SECONDS)
            guards.retire_approval(record, self.repo, guards.KIND_MERGE,
                                   "invalidate")
        with mock.patch.object(guards, "ledger_append", append), \
                mock.patch.object(guards, "_ledger_entries", read):
            self.run_together(
                lambda: herdctl._mint_approval(self.repo, record, merge,
                                               guards.KIND_MERGE),
                retire)
        self.assertGreaterEqual(observed["depth_at_entry"], 1)
        self.assertTrue(observed["record_at_entry"])
        self.assertGreaterEqual(observed["depth_after_append"], 1)
        self.assertGreaterEqual(observed["depth_during_read"], 1)
        events = [entry["event"] for entry in self.chain()]
        self.assertEqual(events, ["mint", "invalidate"])
        self.assertIsNone(guards.ledger_evidence(self.repo, guards.KIND_MERGE,
                                                 None))

    def test_another_process_holding_the_lock_makes_an_append_wait(self):
        descriptor = os.open(guards.hroot(self.repo) / guards.LEDGER_LOCK,
                             os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        done = threading.Event()

        def append():
            guards.ledger_append(self.repo, "mint", guards.KIND_COMMIT,
                                 "4" * 64, expires_at=2 ** 31, create=True)
            done.set()
        thread = threading.Thread(target=append)
        thread.start()
        self.assertFalse(done.wait(0.5))
        self.assertFalse(guards.ledger_path(self.repo).exists())
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.chain()), 1)

    def break_chain(self):
        ledger = guards.ledger_path(self.repo)
        lines = ledger.read_text().splitlines()
        first = json.loads(lines[0])
        first["at"] += 1
        lines[0] = json.dumps(first, sort_keys=True)
        broken = ("\n".join(lines) + "\n").encode("utf-8")
        ledger.write_bytes(broken)
        return broken

    def test_re_authorizing_recovers_a_broken_chain_and_keeps_the_evidence(self):
        """The repair path the round-4 review found broken: a human mint
        archives the broken ledger BYTE-FOR-BYTE and publishes a new chain.
        An approval minted only in the old chain is not outstanding in the
        new one, so it needs re-authorizing too (fail closed)."""
        self.approve(herdctl.approve_commit)
        self.approve(herdctl.approve_merge)
        broken = self.break_chain()
        valid, message = guards.approval_valid(self.repo)
        self.assertFalse(valid)
        self.assertIn("chain is broken", message)
        self.approve(herdctl.approve_commit)
        ledger = guards.ledger_path(self.repo)
        archived = sorted(ledger.parent.glob("approval-ledger.broken-*.jsonl"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), broken)
        entries = self.chain()
        self.assertEqual([entry["event"] for entry in entries],
                         ["restart", "mint"])
        self.assertEqual(entries[0]["token_sha256"], guards.token_digest(broken))
        self.assertTrue(guards.approval_valid(self.repo)[0])
        self.assertFalse(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)[0])

    def test_a_guard_never_repairs_a_broken_ledger(self):
        self.approve(herdctl.approve_commit)
        self.approve(herdctl.approve_merge)
        broken = self.break_chain()
        with self.assertRaises(ValueError):
            guards.ledger_append(self.repo, "consume", guards.KIND_COMMIT,
                                 "5" * 64)
        guards.retire_approval(guards.approval_path(self.repo), self.repo,
                               guards.KIND_COMMIT, "consume")
        self.assertEqual(guards.ledger_path(self.repo).read_bytes(), broken)
        self.assertEqual(list(guards.ledger_path(self.repo).parent.glob(
            "approval-ledger.broken-*")), [])

    def failing_call(self, name, which):
        """``guards.<name>`` with its ``which``-th call raising OSError (a
        fault injected in-process; nothing else changes)."""
        original, calls = getattr(guards, name), []

        def failing(*args, **kwargs):
            calls.append(args)
            if len(calls) == which:
                raise OSError("injected failure: %s call %d" % (name, which))
            return original(*args, **kwargs)
        return mock.patch.object(guards, name, failing)

    def broken_state(self):
        """Fresh state for one case: a merge approval refused ONLY because
        the chain is broken. Returns the state directory and the broken
        ledger's bytes."""
        state = guards.hroot(self.repo) / "state"
        for item in state.iterdir():
            if item.name != Path(guards.LEDGER_LOCK).name:
                item.unlink()
        # Two entries, so editing the first one breaks the chain.
        self.approve(herdctl.approve_commit)
        self.approve(herdctl.approve_merge)
        broken = self.break_chain()
        self.assertFalse(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)[0])
        return state, broken

    def limited_writes(self, limit, zero_at=None, fail_at=None):
        """``guards._write`` writing at most ``limit`` bytes per call; its
        ``zero_at``-th call makes no progress (returns 0) and its
        ``fail_at``-th call raises OSError. Returns (patch, calls)."""
        original, calls = guards._write, []

        def write(descriptor, data):
            calls.append(len(data))
            if len(calls) == fail_at:
                raise OSError("injected write failure")
            if len(calls) == zero_at:
                return 0
            return original(descriptor, bytes(data[:limit]))
        return mock.patch.object(guards, "_write", write), calls

    def test_short_writes_are_completed_byte_for_byte(self):
        """Round 8: ``os.write`` may write fewer bytes than asked. With every
        write limited to 7 bytes, recovery still archives the broken ledger
        EXACTLY and publishes a COMPLETE replacement that chains."""
        state, broken = self.broken_state()
        patch, calls = self.limited_writes(7)
        with patch:
            self.approve(herdctl.approve_commit)
        self.assertGreater(len(calls), 4)
        archived = sorted(state.glob("approval-ledger.broken-*.jsonl"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), broken)
        entries = self.chain()
        self.assertEqual([entry["event"] for entry in entries],
                         ["restart", "mint"])
        self.assertEqual(entries[0]["token_sha256"], guards.token_digest(broken))
        self.assertTrue(guards.approval_valid(self.repo)[0])

    def test_a_short_write_that_stops_refuses_and_preserves_the_ledger(self):
        """Round 8: a short write that then makes NO progress, or fails, in
        the ARCHIVE or in the REPLACEMENT, refuses. The original ledger is
        preserved byte for byte, refusal still holds, no partial file is
        left looking complete, and nothing is published."""
        commit = dict(self.identity, kind=guards.KIND_COMMIT,
                      expires_at=int(time.time()) + 600)
        for label, target, mode in (
            ("the archive, no progress", "archive", "zero"),
            ("the archive, an error after partial writes", "archive", "fail"),
            ("the replacement, no progress", "replacement", "zero"),
            ("the replacement, an error after partial writes", "replacement",
             "fail"),
        ):
            with self.subTest(case=label):
                state, broken = self.broken_state()
                archive_calls = -(-len(broken) // 7)
                at = 3 if target == "archive" else archive_calls + 2
                patch, calls = self.limited_writes(
                    7, zero_at=at if mode == "zero" else None,
                    fail_at=at if mode == "fail" else None)
                with patch:
                    with self.assertRaises(OSError):
                        herdctl._mint_approval(
                            self.repo, guards.approval_path(self.repo),
                            commit, guards.KIND_COMMIT)
                self.assertGreaterEqual(len(calls), at)
                self.assertEqual(guards.ledger_path(self.repo).read_bytes(),
                                 broken)
                for check in (lambda: guards.merge_approval_valid(
                                  self.repo, source_commit=SOURCE_COMMIT),
                              lambda: guards.approval_valid(self.repo)):
                    valid, message = check()
                    self.assertFalse(valid)
                    self.assertIn("chain is broken", message)
                self.assertEqual(list(state.glob(".approval-ledger.*")), [])
                archived = sorted(state.glob("approval-ledger.broken-*"))
                if target == "archive":
                    self.assertEqual(archived, [])
                else:
                    self.assertEqual(len(archived), 1)
                    self.assertEqual(archived[0].read_bytes(), broken)

    def test_a_failed_recovery_leaves_every_refused_approval_refused(self):
        """Round 6 (a source-traced defect, not an executed exploit): a
        recovery that FAILS part-way must leave refusal in force. Before it,
        a merge approval is refused only because the chain is broken, and a
        commit approval has an EDITED expiry. The recovery (a mint) is made
        to fail at each of its steps: the archive copy, the replacement's
        temporary write, and the atomic publication. After every failure the
        broken ledger is still the active ledger, byte for byte; both
        approvals, and the record the failed mint wrote, are still refused;
        and no temporary file is left."""
        state = guards.hroot(self.repo) / "state"
        push = dict(self.identity, kind=guards.KIND_PUSH,
                    expires_at=int(time.time()) + 600)
        for label, seam, which in (("the archive copy", "_write_synced", 1),
                                   ("the temporary write", "_write_synced", 2),
                                   ("the publication", "_replace_file", 1)):
            with self.subTest(failing=label):
                for item in state.iterdir():
                    if item.name != Path(guards.LEDGER_LOCK).name:
                        item.unlink()
                self.approve(herdctl.approve_merge)
                self.approve(herdctl.approve_commit)
                commit = guards.approval_path(self.repo)
                token = json.loads(commit.read_text())
                token["expires_at"] += 3600
                commit.write_text(json.dumps(token))
                broken = self.break_chain()
                for check in (
                    lambda: guards.merge_approval_valid(
                        self.repo, source_commit=SOURCE_COMMIT),
                    lambda: guards.approval_valid(self.repo),
                ):
                    self.assertFalse(check()[0])
                with self.failing_call(seam, which):
                    with self.assertRaises(OSError):
                        herdctl._mint_approval(
                            self.repo, guards.push_approval_path(self.repo),
                            push, guards.KIND_PUSH)
                self.assertEqual(guards.ledger_path(self.repo).read_bytes(),
                                 broken)
                valid, message = guards.merge_approval_valid(
                    self.repo, source_commit=SOURCE_COMMIT)
                self.assertFalse(valid)
                self.assertIn("chain is broken", message)
                valid, message = guards.approval_valid(self.repo)
                self.assertFalse(valid)
                self.assertIn("chain is broken", message)
                self.assertIsNotNone(guards.ledger_evidence(
                    self.repo, guards.KIND_PUSH,
                    guards.push_approval_path(self.repo).read_bytes()))
                self.assertEqual(list(state.glob(".approval-ledger.*")), [])
                for archived in state.glob("approval-ledger.broken-*"):
                    self.assertEqual(archived.read_bytes(), broken)


# ====================================================================
# S2: typed confirmation, at the controlling terminal only
# ====================================================================


class TypedConfirmationTests(GateFixture):

    def test_yes_is_no_longer_an_option(self):
        parser_errors = io.StringIO()
        for command in ("approve-commit", "approve-push", "approve-merge"):
            with self.subTest(command=command):
                with mock.patch.object(sys, "stderr", parser_errors), \
                        mock.patch.object(sys, "argv",
                                          ["herdctl", command, "--yes"]):
                    with self.assertRaises(SystemExit) as caught:
                        herdctl.main()
                self.assertEqual(caught.exception.code, 2)
        self.assertIn("--yes", parser_errors.getvalue())

    def test_a_piped_answer_with_no_terminal_confirms_nothing(self):
        for function, path in (
            (herdctl.approve_commit, guards.approval_path(self.repo)),
            (herdctl.approve_merge, guards.merge_approval_path(self.repo)),
        ):
            with self.subTest(function=function.__name__):
                with mock.patch.object(sys, "stdin", io.StringIO("demo\n")), \
                        mock.patch.object(sys, "stdout", io.StringIO()):
                    self.terminal = None
                    with self.assertRaises(SystemExit) as caught:
                        function(mock.Mock(repo=None, ttl=600, source=SOURCE))
                self.assertIn("controlling terminal", str(caught.exception))
                self.assertFalse(path.exists())

    def test_a_wrong_answer_at_the_terminal_confirms_nothing(self):
        with self.assertRaises(SystemExit):
            self.approve(herdctl.approve_merge, answer="something else")
        self.assertFalse(guards.merge_approval_path(self.repo).exists())

    def test_the_right_answer_at_the_terminal_mints_one_approval(self):
        self.approve(herdctl.approve_merge)
        self.assertTrue(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)[0])
        self.assertIn("demo", self.terminal.prompts[0])

    def test_the_terminal_claim_is_scoped_in_the_documentation(self):
        """A DOCUMENTATION PIN (round 4, finding 4): the operations guide
        states what the controlling-terminal confirmation stops (``--yes``,
        piped stdin, no terminal) and that it is a workflow guardrail, not
        designed to contain processes running with the user's own
        privileges, as ``typed_confirmation`` does, and makes no claim that
        no agent can operate a gate."""
        text = " ".join((REPO_ROOT / "docs" / "operations.md").read_text(
            encoding="utf-8").split())
        self.assertNotIn("No gate can be operated by an agent", text)
        self.assertNotIn("Three deterministic one-shot human authorization"
                         " gates", text)
        for phrase in ("not designed to contain processes running with the"
                       " user's own privileges",
                       "It is not proof that a human confirmed",
                       "workflow guardrails, not an enforced boundary"):
            self.assertIn(phrase, text)
        code = " ".join(guards.typed_confirmation.__doc__.split())
        self.assertIn("not designed to contain processes running with the"
                      " user's own privileges", code)


# ====================================================================
# S3: the approval ledger is tamper-EVIDENCE, not a control
# ====================================================================


class TamperEvidenceTests(GateFixture):

    def test_an_edited_record_is_refused_as_tamper_evidence(self):
        self.approve(herdctl.approve_commit)
        path = guards.approval_path(self.repo)
        token = json.loads(path.read_text())
        token["expires_at"] += 3600
        path.write_text(json.dumps(token))
        valid, message = guards.approval_valid(self.repo)
        self.assertFalse(valid)
        self.assertIn("Tamper evidence", message)

    def test_a_deleted_outstanding_record_is_named_not_treated_as_absent(self):
        self.approve(herdctl.approve_merge)
        guards.merge_approval_path(self.repo).unlink()
        valid, message = guards.merge_approval_valid(self.repo)
        self.assertFalse(valid)
        self.assertIn("is missing", message)

    def test_a_consumed_record_restored_is_refused(self):
        self.approve(herdctl.approve_merge)
        path = guards.merge_approval_path(self.repo)
        data = path.read_bytes()
        self.assertTrue(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT, consume=True)[0])
        path.write_bytes(data)
        valid, message = guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)
        self.assertFalse(valid)
        self.assertIn("not an outstanding herdctl mint", message)

    def test_a_record_written_beside_an_existing_ledger_is_refused(self):
        self.approve(herdctl.approve_commit)
        guards.push_approval_path(self.repo).write_text(json.dumps(
            dict(self.identity, expires_at=2 ** 31)))
        evidence = guards.ledger_evidence(
            self.repo, guards.KIND_PUSH,
            guards.push_approval_path(self.repo).read_bytes())
        self.assertIn("Tamper evidence", evidence)

    def test_a_broken_chain_is_refused(self):
        """An edited entry that has a successor breaks the chain (the
        successor holds its digest). The LAST entry has no successor, so an
        edit to it is not detectable this way: evidence, not a control."""
        self.approve(herdctl.approve_commit)
        self.approve(herdctl.approve_commit)
        ledger = guards.ledger_path(self.repo)
        lines = ledger.read_text().splitlines()
        entry = json.loads(lines[0])
        entry["expires_at"] += 1
        ledger.write_text("\n".join([json.dumps(entry)] + lines[1:]) + "\n")
        valid, message = guards.approval_valid(self.repo)
        self.assertFalse(valid)
        self.assertIn("chain is broken", message)

    def test_without_a_ledger_the_legacy_records_behave_as_before(self):
        """Backward compatible: a repository with no ledger (records written
        before it existed, or by existing fixtures) is judged as before."""
        guards.approval_path(self.repo).write_text(json.dumps(
            dict(self.identity, expires_at=2 ** 31)))
        self.assertFalse(guards.ledger_path(self.repo).exists())
        self.assertEqual(guards.approval_valid(self.repo), (True, "approved"))

    def test_the_ledger_is_never_described_as_a_control(self):
        """A DOCUMENTATION PIN only: the module describes the ledger as
        tamper evidence, not a control, and as not designed to contain
        processes running with the user's own privileges."""
        doc = " ".join(guards.__doc__.split())
        for phrase in ("TAMPER-EVIDENCE against NON-ADVERSARIAL change",
                       "not a control",
                       "not designed to contain processes running with the"
                       " user's own privileges"):
            self.assertIn(phrase, doc)


# ====================================================================
# S4: a Mission approval never confers delivery authority
# ====================================================================


class MissionApprovalConfersNoDeliveryTests(GateFixture):

    def test_no_mission_or_transport_module_writes_git_authority(self):
        """Structural: outside ``herdr/guards.py`` and ``herdctl.py``, no
        product module names an approval record, the ledger writer or the
        mint helper, so no Mission approval path can produce one."""
        names = ("commit-approval.json", "push-approval.json",
                 "merge-approval.json", "approval-ledger.jsonl",
                 "ledger_append", "_mint_approval")
        allowed = {"herdr/guards.py", "herdctl.py"}
        from test_workflow_authority import derive_product_python_files
        for path in derive_product_python_files(REPO_ROOT) + sorted(
            (REPO_ROOT / "herdr").glob("*.py")
        ):
            relpath = path.relative_to(REPO_ROOT).as_posix()
            if relpath in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for name in names:
                self.assertNotIn(name, text, relpath)

    def test_with_no_approval_every_gate_refuses(self):
        """No-approval refusal only. The SEPARATION claim (a Mission approval
        genuinely obtained still confers nothing) is
        ``MissionApprovalSeparationTests`` below."""
        for command in (COMMIT_TEXT, PUSH_TEXT, "git merge topic",
                        "gh pr create --fill"):
            with self.subTest(command=command):
                code, errors = self.pretool(command)
                self.assertEqual(code, 2, errors)
        self.assertFalse(guards.approval_valid(self.repo)[0])
        self.assertFalse(guards.push_approval_valid(self.repo)[0])
        self.assertFalse(guards.merge_approval_valid(
            self.repo, source_commit=SOURCE_COMMIT)[0])


class MissionApprovalSeparationTests(Fixture):
    """S4 behaviourally: a Mission approval GENUINELY OBTAINED through the
    Grok Bot ceremony (present, local arming, fire, Mission Core applies it)
    creates no Git or delivery authorization record, reaches no delivery
    machinery, and leaves every Git gate refusing in the repository. Nothing
    is delivered or executed: the gates are judged from fixture state."""

    def setUp(self):
        super(MissionApprovalSeparationTests, self).setUp()
        self.no_process = NoProcess(self)
        self.git_repo = Path(self.repository)
        (self.git_repo / ".herd" / "state").mkdir(parents=True)
        (self.git_repo / ".herd" / guards.CFG).write_text("{}")
        self.identity = dict(IDENTITY, repo_root=str(self.git_repo),
                             git_dir=str(self.git_repo / ".git"))
        self.delivery_reached = []
        from pr_delivery import cli as delivery_cli
        for owner, name, value in (
            (guards, "repo_identity", lambda repo: dict(self.identity)),
            (guards, "run", self.fake_run),
            (guards, "_delivery_receipt_decision",
             lambda *a, **k: (False, "no receipt (fixture)")),
            (delivery_cli, "build_machine",
             lambda *a, **k: self.delivery_reached.append(a)),
        ):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_run(self, argv, *args, **kwargs):
        tail = argv[3:] if argv[:2] == ["git", "-C"] else argv[1:]
        if tail == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(argv, 0, stdout=str(self.git_repo),
                                               stderr="")
        if tail == ["symbolic-ref", "-q", "HEAD"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="refs/heads/" + self.identity["branch"], stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    def authority_records(self):
        names = ("commit-approval.json", "push-approval.json",
                 "merge-approval.json", "approval-ledger.jsonl")
        return sorted(os.path.join(root, name)
                      for root, _, files in os.walk(self.tmp.name)
                      for name in files if name in names)

    def test_a_genuine_mission_approval_confers_no_git_or_delivery_authority(self):
        out, shown, decision = self.approved()
        self.assertEqual(decision["status"], "approved_by_operator_attestation")
        self.assertTrue(decision["arming"]["locally_armed"])
        self.assertEqual(decision["delivery_authority"], "none")
        self.assertEqual(self.mission(out["mission_id"])["state"], "AUTHORIZED")
        self.assertEqual(self.authority_records(), [])
        self.assertEqual(self.delivery_reached, [])
        for command in (COMMIT_TEXT, PUSH_TEXT, "git merge topic",
                        "gh pr create --fill", "gh pr merge 1"):
            with self.subTest(command=command):
                code, errors = pretool_decision(self.git_repo, command)
                self.assertEqual(code, 2, errors)
        self.assertFalse(guards.approval_valid(self.git_repo)[0])
        self.assertFalse(guards.push_approval_valid(self.git_repo)[0])
        self.assertFalse(guards.merge_approval_valid(
            self.git_repo, source_commit=SOURCE_COMMIT)[0])
        line = "%s %s refs/heads/%s\n" % ("a" * 40, "f" * 40,
                                           self.identity["branch"])
        with mock.patch.object(sys, "stdin", io.StringIO(line)), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(guards.guard_reference_transaction(
                self.git_repo, "prepared"), 1)
        self.assertEqual(self.authority_records(), [])


class NoProcessContainmentTests(GateFixture):

    def test_nothing_here_starts_a_process(self):
        """``Bounded`` installed ``NoProcess``: every entry point raises."""
        with self.assertRaises(AssertionError):
            subprocess.run(["git", "status"])
        self.no_process.calls[:] = []


def _hook_demanding_merge_head(repo):
    """The Increment 2 hook: it required MERGE_HEAD, which a fresh
    automatic merge has not written yet when the hook runs."""
    valid, _ = guards.merge_approval_valid(
        repo, source_commit=guards._merge_head(repo), consume=True)
    return 0 if valid else 1


def _cannot_restart(*args):
    raise ValueError("the chain is broken and nothing restarts it")


def _mint_outside_the_lock(r, path, tok, kind):
    """``herdctl._mint_approval`` with its OUTER lock removed: the record
    write and the mint entry are no longer one operation."""
    data = (json.dumps(tok, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    guards.ledger_append(r, "mint", kind, guards.token_digest(data),
                         expires_at=int(tok["expires_at"]), create=True)


def _single_write(target, data):
    """The round-8 defect, reproduced: one write, its count ignored."""
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        guards._write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _moves_the_ledger_away_first(path, event, kind, digest, expires_at):
    """The round-6 defect, reproduced: the active ledger is moved away
    BEFORE its replacement is published."""
    data = path.read_bytes()
    os.replace(path, path.with_name("approval-ledger.broken-moved.jsonl"))
    restart = guards._chained([], "restart", "ledger",
                              guards.token_digest(data), None)
    guards._publish_ledger(path, [restart, guards._chained(
        [restart], event, kind, digest, expires_at)])


MUTANTS = (
    ("the merge-commit hook demands MERGE_HEAD", guards, "guard_premerge",
     _hook_demanding_merge_head,
     ("MergeGateTests.test_a_fresh_automatic_merge_succeeds_in_the_real_hook"
      "_order",)),
    ("any branch update counts as the approved merge", guards,
     "_is_approved_merge_update", lambda token, old, new, repo: True,
     ("MergeGateTests.test_the_ref_update_refuses_every_substituted_merge",)),
    ("no update is identified as a merge (a commit token then suffices)",
     guards, "_identifiable_merge", lambda repo, head_updates: False,
     ("MergeGateTests.test_a_valid_commit_approval_never_authorizes_a_merge",)),
    ("every commit counts as an ancestor (no ancestry check)", guards,
     "_is_ancestor", lambda repo, ancestor, descendant: True,
     ("MergeGateTests.test_a_move_to_a_source_that_is_not_a_descendant_is"
      "_refused",)),
    ("any merge command counts as standalone (redirection unchecked)",
     guards, "_merge_command_refusal", lambda command: None,
     ("RepositoryRedirectionTests.test_a_redirected_merge_is_refused_not"
      "_judged_against_the_wrong_repository",)),
    ("the ledger is not serialized", guards, "ledger_lock",
     lambda repo: contextlib.nullcontext(),
     ("LedgerSerializationTests.test_concurrent_appends_chain_one_after_the"
      "_other",
      "LedgerSerializationTests.test_another_process_holding_the_lock_makes"
      "_an_append_wait")),
    ("a ref-update mismatch keeps the approval", guards, "_retire",
     lambda path, repo, kind, event: None,
     ("MergeGateTests.test_the_ref_update_refuses_every_substituted_merge",)),
    ("a mint cannot restart a broken chain", guards, "_restart_broken_ledger",
     _cannot_restart,
     ("LedgerSerializationTests.test_re_authorizing_recovers_a_broken_chain"
      "_and_keeps_the_evidence",)),
    ("the mint writes its record outside the ledger lock", herdctl,
     "_mint_approval", _mint_outside_the_lock,
     ("LedgerSerializationTests.test_a_mint_holds_the_lock_across_its_record"
      "_and_its_entry",)),
    ("a write's short count is ignored (round 8)", guards, "_write_synced",
     _single_write,
     ("LedgerSerializationTests.test_short_writes_are_completed_byte_for"
      "_byte",
      "LedgerSerializationTests.test_a_short_write_that_stops_refuses_and"
      "_preserves_the_ledger")),
    ("recovery moves the active ledger away before publishing (round 6)",
     guards, "_restart_broken_ledger", _moves_the_ledger_away_first,
     ("LedgerSerializationTests.test_a_failed_recovery_leaves_every_refused"
      "_approval_refused",)),
)


class MutationSelfCheckTests(unittest.TestCase):
    """A plain TestCase: every inner test keeps its own SIGALRM watchdog."""

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_every_gate_mutant_is_caught_and_the_original_passes(self):
        for label, owner, name, mutant, names in MUTANTS:
            with self.subTest(mutant=label):
                with mock.patch.object(owner, name, mutant):
                    broken = self.run_named(names)
                failed = set(getattr(test, "test_case", test).id()
                             for test, _ in broken.failures + broken.errors)
                self.assertEqual(len(failed), len(names), (label, failed))
                restored = self.run_named(names)
                self.assertTrue(restored.wasSuccessful(),
                                (label, restored.failures, restored.errors))


if __name__ == "__main__":
    unittest.main()
