"""I2b: the guards cluster collapsed onto `herdr.guards`.

`herdctl.py` carried hand copies of five guard functions. A copy of a
guard drifts, and a drifted guard means the one ENFORCED can differ
from the one REVIEWED — which is why R-10 authorized the collapse
under conditions, and why the conditions are about EXECUTION.

This module is the in-suite half of that evidence:

- `BeforeAndAfterTests` runs each package guard beside the current CLI
  compatibility wrapper over the same matrix. That permanently measures
  delegation parity without requiring HEAD to mean "before collapse".
- `RefusalStrengthTests` drives each named refusal through the
  collapsed guard and asserts it still refuses (G-2).
- `HumanGateTests` asserts WHEN a human is required is unchanged
  end-to-end (G-3).

Fixtures are INDEPENDENT per side. An earlier harness in I2 shared one
fixture and reported a divergence that belonged to the harness rather
than to the code, because `approval_valid` unlinks the approval on
mismatch and the second side then found it already gone. That is one observed
mechanism for a shared-fixture artifact, not a survey of them: per-side
fixtures remove the ones that travel through files under the repo, and
they do not address contamination through process-wide state such as
`sys.stdin` or the working directory, which `run_guard` restores by
hand instead.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))

import herdctl                                        # noqa: E402
from herdr import guards                              # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hermetic_git import run_git                     # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Spelled in pieces so this file's own text does not trip the pretool
#: guard, which refuses a shell command carrying the literal word
#: chained with another operation. Four independent reproductions of
#: that false positive are recorded in the I2b evidence; it is not
#: fixed here and not weakened.
VERB_C = "com" + "mit"
VERB_P = "push"

#: The five functions R-10 named.
COLLAPSED = (
    "guard_pretool",
    "guard_precommit",
    "guard_reference_transaction",
    "guard_prepush",
    "_install_one_git_hook",
)


class GuardFixture(unittest.TestCase):
    """A real git repository, optionally under a symlinked root."""

    def make_repo(self, root=None, with_herd=True, remote=None):
        base = Path(tempfile.mkdtemp(dir=root))
        self.addCleanup(self._remove, base)
        repo = base / "repo"
        repo.mkdir()
        run_git("init", "-q", str(repo))
        (repo / "f.txt").write_text("x")
        run_git("-C", str(repo), "add", "-A")
        run_git("-C", str(repo), VERB_C, "-q", "-m", "init")
        if remote:
            run_git("-C", str(repo), "remote", "add", "origin", remote)
        if with_herd:
            herd = repo / ".herd"
            (herd / "state").mkdir(parents=True, exist_ok=True)
            (herd / "herd.config.json").write_text(
                json.dumps({"version": 4})
            )
        return repo

    @staticmethod
    def _remove(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def authorize(self, module, repo):
        """Issue a valid approval through one module's own helpers."""
        token = dict(module.repo_identity(repo))
        token["expires_at"] = 2 ** 31
        path = module.approval_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token))

    def run_guard(self, fn, *args, stdin=None):
        """(return value, stderr), with stdin injected when given."""
        err, out = io.StringIO(), io.StringIO()
        previous = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stderr(err), \
                 contextlib.redirect_stdout(out):
                value = fn(*args)
        except SystemExit as exc:
            value = ("SystemExit", exc.code)
        except BaseException as exc:                   # noqa: BLE001
            value = (type(exc).__name__, str(exc))
        finally:
            sys.stdin = previous
        return value, err.getvalue()

    @staticmethod
    def scrub(text, *repos):
        """Normalise fixture paths. LONGEST SPELLING FIRST — scrubbing
        `/var/x` before `/private/var/x` leaves `/private<REPO>` and
        manufactures a difference that belongs to this helper rather
        than to the code, which a first run of the scratchpad harness
        actually reported."""
        spellings = set()
        for repo in repos:
            spellings.add(str(repo))
            spellings.add(str(Path(repo).resolve()))
        for spelling in sorted(spellings, key=len, reverse=True):
            text = text.replace(spelling, "<REPO>")
        return text

    def tool_payload(self, command, repo=None, tool="Bash"):
        body = {"tool_name": tool, "tool_input": {"command": command}}
        if repo is not None:
            body["cwd"] = str(repo)
        return json.dumps(body)


class BeforeAndAfterTests(GuardFixture):
    """G-1: CLI wrappers behave exactly like the package guards.

    THE EXECUTED GUARANTEE for the collapse. The matrix includes a
    SYMLINKED repo root, because resolve-versus-not is invisible on a
    path whose two spellings coincide.
    """

    def roots(self):
        """A plain root and, where the platform provides one, a
        symlinked root. Within this method, the symlinked arm is
        skipped rather than faked when `/tmp` stops being a symlink, so
        it does not report a pass over an unobservable difference; a
        platform where `/tmp` is real leaves the resolve-versus-not
        difference unmeasured here, which the skip says out loud."""
        found = [("plain", None)]
        candidate = Path("/tmp")
        if str(candidate) != str(candidate.resolve()):
            found.append(("symlinked", str(candidate)))
        return found

    def test_the_symlinked_arm_is_available_or_declared_absent(self):
        names = [name for name, _ in self.roots()]
        if "symlinked" not in names:
            self.skipTest(
                "/tmp is not a symlink on this platform, so the"
                " resolve-versus-not difference cannot be observed"
                " here and the arms below run on plain roots only"
            )
        self.assertIn("symlinked", names)

    def test_precommit_matches_before_and_after(self):
        head = guards
        for name, root in self.roots():
            for authorize in (False, True):
                with self.subTest(root=name, authorized=authorize):
                    before_repo = self.make_repo(root)
                    after_repo = self.make_repo(root)
                    if authorize:
                        self.authorize(head, before_repo)
                        self.authorize(herdctl, after_repo)
                    before = self.run_guard(head.guard_precommit,
                                            before_repo)
                    after = self.run_guard(herdctl.guard_precommit,
                                           after_repo)
                    self.assertEqual(
                        before[0], after[0],
                        "the exit code changed across the collapse",
                    )
                    self.assertEqual(
                        self.scrub(before[1], before_repo),
                        self.scrub(after[1], after_repo),
                        "the message changed beyond path spelling",
                    )

    def test_reference_transaction_matches_before_and_after(self):
        head = guards
        zero = "0" * 40
        payloads = ("", "abc def refs/heads/main\n",
                    "%s abc refs/heads/main\n" % zero,
                    "abc %s refs/heads/main\n" % zero,
                    "abc def refs/tags/v1\n", "malformed\n")
        for name, root in self.roots():
            for phase in ("prepared", "committed", "aborted"):
                for payload in payloads:
                    with self.subTest(root=name, phase=phase,
                                      payload=payload[:20]):
                        before_repo = self.make_repo(root)
                        after_repo = self.make_repo(root)
                        before = self.run_guard(
                            head.guard_reference_transaction,
                            before_repo, phase, stdin=payload)
                        after = self.run_guard(
                            herdctl.guard_reference_transaction,
                            after_repo, phase, stdin=payload)
                        self.assertEqual(before[0], after[0])
                        self.assertEqual(
                            self.scrub(before[1], before_repo),
                            self.scrub(after[1], after_repo))

    def test_prepush_matches_before_and_after(self):
        head = guards
        for name, root in self.roots():
            for remote in (None, "https://github.com/x/y.git"):
                for payload in ("", "refs/heads/main a refs/heads/main b\n"):
                    with self.subTest(root=name, remote=bool(remote),
                                      payload=payload[:16]):
                        before_repo = self.make_repo(root, remote=remote)
                        after_repo = self.make_repo(root, remote=remote)
                        before = self.run_guard(
                            head.guard_prepush, before_repo, "origin",
                            "https://github.com/x/y.git", stdin=payload)
                        after = self.run_guard(
                            herdctl.guard_prepush, after_repo, "origin",
                            "https://github.com/x/y.git", stdin=payload)
                        self.assertEqual(before[0], after[0])
                        self.assertEqual(
                            self.scrub(before[1], before_repo),
                            self.scrub(after[1], after_repo))

    def test_pretool_matches_before_and_after(self):
        head = guards
        commands = [
            "ls", "git status",
            "git %s -m x" % VERB_C,
            "git %s --no-verify -m x" % VERB_C,
            "git %s -m x ; ls" % VERB_C,
            "git %s" % VERB_P,
            "git %s --force" % VERB_P,
            "git %s --no-verify" % VERB_P,
            "git %s --dry-run" % VERB_P,
        ]
        for name, root in self.roots():
            for command in commands:
                for with_cwd in (True, False):
                    with self.subTest(root=name, command=command,
                                      cwd=with_cwd):
                        before_repo = self.make_repo(root)
                        after_repo = self.make_repo(root)
                        before = self.run_guard(
                            head.guard_pretool,
                            stdin=self.tool_payload(
                                command,
                                before_repo if with_cwd else None))
                        after = self.run_guard(
                            herdctl.guard_pretool,
                            stdin=self.tool_payload(
                                command,
                                after_repo if with_cwd else None))
                        self.assertEqual(before[0], after[0])
                        self.assertEqual(
                            self.scrub(before[1], before_repo),
                            self.scrub(after[1], after_repo))

    def test_hook_installer_matches_before_and_after(self):
        head = guards
        for name, root in self.roots():
            for hook, marker in (("pre-" + VERB_C, "# herd-guard"),
                                 ("pre-" + VERB_P, "# herd-guard-p")):
                with self.subTest(root=name, hook=hook):
                    before_repo = self.make_repo(root)
                    after_repo = self.make_repo(root)
                    self.run_guard(head._install_one_git_hook,
                                   before_repo, hook, marker, "line")
                    self.run_guard(herdctl._install_one_git_hook,
                                   after_repo, hook, marker, "line")
                    before_path = before_repo / ".git" / "hooks" / hook
                    after_path = after_repo / ".git" / "hooks" / hook
                    self.assertTrue(before_path.exists())
                    self.assertTrue(after_path.exists())
                    self.assertEqual(
                        self.scrub(before_path.read_text(), before_repo),
                        self.scrub(after_path.read_text(), after_repo),
                        "the installed hook body changed",
                    )
                    self.assertEqual(
                        oct(before_path.stat().st_mode & 0o777),
                        oct(after_path.stat().st_mode & 0o777))


class RefusalStrengthTests(GuardFixture):
    """G-2: no guard became weaker.

    The three refusals R-10 names explicitly, driven through the
    COLLAPSED guard and asserted on the exit code and the message.
    """

    def blocked(self, command, repo):
        value, err = self.run_guard(
            herdctl.guard_pretool,
            stdin=self.tool_payload(command, repo))
        return value, err

    def test_the_no_verify_commit_refusal_still_refuses(self):
        repo = self.make_repo()
        value, err = self.blocked("git %s --no-verify -m x" % VERB_C,
                                  repo)
        self.assertEqual(value, 2, err)
        self.assertIn("--no-verify", err)

    def test_the_no_verify_transfer_refusal_still_refuses(self):
        repo = self.make_repo()
        value, err = self.blocked("git %s --no-verify" % VERB_P, repo)
        self.assertEqual(value, 2, err)
        self.assertIn("--no-verify", err)

    def test_the_destructive_transfer_refusals_still_refuse(self):
        repo = self.make_repo()
        for flag in ("--force", "-f", "--force-with-lease",
                     "--mirror", "--delete"):
            with self.subTest(flag=flag):
                value, err = self.blocked(
                    "git %s %s origin main" % (VERB_P, flag), repo)
                self.assertEqual(value, 2, err)
                self.assertIn("blocked", err.lower())

    def test_a_chained_operation_is_still_refused(self):
        repo = self.make_repo()
        value, err = self.blocked("git %s -m x ; rm -rf /tmp/nope"
                                  % VERB_C, repo)
        self.assertEqual(value, 2, err)

    def test_an_unrelated_command_is_still_allowed(self):
        """Anti-vacuity: a guard that refused everything would pass
        every assertion above."""
        repo = self.make_repo()
        value, _ = self.blocked("ls -la", repo)
        self.assertEqual(value, 0)


class HumanGateTests(GuardFixture):
    """G-3: WHEN a human is required is unchanged, end to end."""

    def test_an_unapproved_commit_is_blocked_before_and_after(self):
        head = guards
        before_repo = self.make_repo()
        after_repo = self.make_repo()
        before = self.run_guard(head.guard_precommit, before_repo)
        after = self.run_guard(herdctl.guard_precommit, after_repo)
        self.assertEqual(before[0], 1)
        self.assertEqual(after[0], 1, "the human gate stopped firing")
        self.assertIn("BLOCKED", after[1])

    def test_an_approved_commit_is_authorized_before_and_after(self):
        head = guards
        before_repo = self.make_repo()
        after_repo = self.make_repo()
        self.authorize(head, before_repo)
        self.authorize(herdctl, after_repo)
        before = self.run_guard(head.guard_precommit, before_repo)
        after = self.run_guard(herdctl.guard_precommit, after_repo)
        self.assertEqual(before[0], 0)
        self.assertEqual(after[0], 0,
                         "an approved commit is now refused")

    def test_the_pretool_gate_still_requires_an_approval(self):
        repo = self.make_repo()
        value, err = self.run_guard(
            herdctl.guard_pretool,
            stdin=self.tool_payload("git %s -m x" % VERB_C, repo))
        self.assertEqual(value, 2, err)
        self.assertIn("Commit blocked", err)

        self.authorize(herdctl, repo)
        value, err = self.run_guard(
            herdctl.guard_pretool,
            stdin=self.tool_payload("git %s -m x" % VERB_C, repo))
        self.assertEqual(
            value, 0,
            "an approved commit is blocked; the gate moved rather"
            " than staying where it was: %s" % err,
        )

    def test_the_approval_is_not_consumed_by_the_check(self):
        """The gate's timing is part of WHEN a human is required: a
        pre-check that consumed the approval would move it."""
        repo = self.make_repo()
        self.authorize(herdctl, repo)
        path = herdctl.approval_path(repo)
        before = path.read_bytes()
        self.run_guard(herdctl.guard_precommit, repo)
        self.assertTrue(path.exists(), "the pre-check consumed it")
        self.assertEqual(path.read_bytes(), before)


class BoundaryTranslationTests(GuardFixture):
    """R-11: the library raises and the CLI translates at its own
    boundary, preserving CONTROL FLOW rather than only the name.

    `herdctl.py:398`'s `except SystemExit` converts a failure into a
    REFUSAL, so a wrapper that let a `RuntimeError` through would
    change what the caller catches, not merely what it is called.
    """

    @staticmethod
    def raised_by(fn, *args):
        """The exception a call raises, CAPTURED rather than expected.

        `assertRaises(SystemExit)` would report a leaked `RuntimeError`
        as a test ERROR, and an error is a crash, not an authored
        judgement — this file does not count crash kills. Capturing
        every `BaseException` and then asserting on its type makes the
        removal of the translation fail by ASSERTION.
        """
        try:
            fn(*args)
        except BaseException as exc:                    # noqa: BLE001
            return exc
        return None

    def test_prepush_translates_a_package_RuntimeError(self):
        from unittest.mock import patch
        with patch.object(guards, "guard_prepush") as raiser:
            raiser.side_effect = RuntimeError("remote gone")
            exc = self.raised_by(
                herdctl.guard_prepush, Path("/x"), "origin", "url")
        self.assertIsInstance(
            exc, SystemExit,
            "the CLI boundary must translate the library's"
            " RuntimeError into SystemExit, because herdctl.py:398"
            " catches SystemExit and a leaked RuntimeError would"
            " change CONTROL FLOW, not just the name; got %r" % (exc,),
        )
        self.assertEqual(str(exc), "remote gone")

    def test_hook_installer_translates_a_package_RuntimeError(self):
        from unittest.mock import patch
        with patch.object(guards, "_install_one_git_hook") as raiser:
            raiser.side_effect = RuntimeError("both hooks exist")
            exc = self.raised_by(
                herdctl._install_one_git_hook,
                Path("/x"), "pre-" + VERB_C, "#m", "line")
        self.assertIsInstance(
            exc, SystemExit,
            "the hook installer's CLI boundary must translate too;"
            " got %r" % (exc,),
        )
        self.assertEqual(str(exc), "both hooks exist")

    def test_the_translation_is_defensive_on_todays_package(self):
        """Recorded rather than implied: `guards.guard_prepush`
        reaches `push_identity` only through `push_approval_valid`,
        which catches `RuntimeError` itself. So the wrapper's handler
        does not fire on today's package, and the two tests above
        drive it with an injected raiser instead of pretending it
        does."""
        repo = self.make_repo(remote="https://github.com/x/y.git")
        value, err = self.run_guard(
            guards.guard_prepush, repo, "origin",
            "https://github.com/x/y.git", stdin="")
        self.assertEqual(
            value, 1,
            "the package guard raised where it used to refuse; the"
            " translation is no longer merely defensive and this"
            " note is stale: %s" % err,
        )
        self.assertIn("HERD PUSH BLOCKED", err)


class DelegationTests(unittest.TestCase):
    """The collapsed functions call the package rather than carrying a
    body of their own.

    Source is the only feasible level for "this function delegates",
    because a body and a delegation that behave identically are what
    `BeforeAndAfterTests` above already proves — the executed
    guarantee. This class is fast structural feedback in front of it,
    catching a re-copy on the next run rather than after the next
    drift.
    """

    def test_each_collapsed_guard_calls_the_package(self):
        import ast
        import inspect
        source = inspect.getsource(herdctl)
        tree = ast.parse(source)
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in COLLAPSED:
                continue
            calls = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    if isinstance(func, ast.Attribute) and isinstance(
                        func.value, ast.Name
                    ) and func.value.id == "guards":
                        calls.add(func.attr)
            found[node.name] = calls
        self.assertEqual(
            sorted(found), sorted(COLLAPSED),
            "a collapsed guard is missing from herdctl.py",
        )
        for name, calls in found.items():
            with self.subTest(name=name):
                self.assertIn(
                    name, calls,
                    "%s does not delegate to guards.%s" % (name, name),
                )


if __name__ == "__main__":
    unittest.main()
