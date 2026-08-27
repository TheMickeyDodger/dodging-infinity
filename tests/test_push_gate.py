"""Regression tests for the push-approval token lifecycle.

Guards against the dry-run token burn (task 20260823-195003-897312): the
pre-push hook also runs for `git push --dry-run`, so the approval must only
be consumed on evidence of a completed transfer (the approved commit landing
on the approved remote-tracking ref), never inside pre-push itself.

Standalone: PYTHONPATH=$PWD python3 tests/test_push_gate.py
stdlib only (CI runs 3.9/3.13 with no dependencies). All git activity happens
in throwaway temp repos with local bare remotes — no network, and never this
repository's own .herd/state tokens or .git/hooks.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from _hermetic_git import run_git_completed

import herdctl
from herdr import guards


def sh(args, cwd, check=True):
    """Run a git command (argv tail, no leading "git") hermetically:
    the shared helper injects invocation-local identity so commits and
    annotated tags need no ambient Git identity (guarded by
    tests/test_hermetic_git.py)."""
    p = run_git_completed(args, cwd=str(cwd), check=False)
    if check and p.returncode:
        raise AssertionError(
            f"{args} failed rc={p.returncode}\n{p.stdout}\n{p.stderr}"
        )
    return p


class PushGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.bare = base / "remote.git"
        self.work = base / "work"
        sh(["init", "--bare", "-q", str(self.bare)], cwd=base)
        sh(["init", "-q", str(self.work)], cwd=base)
        self.configure(self.work)
        sh(["remote", "add", "origin", str(self.bare)], cwd=self.work)
        (self.work / "a.txt").write_text("one\n")
        sh(["add", "a.txt"], cwd=self.work)
        sh(["commit", "-qm", "one"], cwd=self.work)
        self.br = sh(
            ["branch", "--show-current"], cwd=self.work
        ).stdout.strip()
        sh(["push", "-qu", "origin", self.br], cwd=self.work)
        # The commit that the push approval will authorize, created before the
        # guards are installed so no commit approval is needed here.
        (self.work / "a.txt").write_text("two\n")
        sh(["add", "a.txt"], cwd=self.work)
        sh(["commit", "-qm", "two"], cwd=self.work)
        guards.install_git_guard(self.work)

    def configure(self, repo):
        sh(["config", "user.email", "t@example.com"], cwd=repo)
        sh(["config", "user.name", "T"], cwd=repo)

    def head(self, repo, ref="HEAD"):
        return sh(["rev-parse", ref], cwd=repo).stdout.strip()

    def write_token(self):
        """Create a push approval exactly as `herdctl approve-push` binds it."""
        ident = guards.push_identity(self.work)
        token = dict(ident, alias="t", approved_at=1, expires_at=2**31)
        path = guards.push_approval_path(self.work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token))
        return path

    def clone_and_push_to_bare(self, name, extra_commit=None):
        """Push work's history (optionally plus one commit) to the bare remote
        from an independent checkout, bypassing work's hooks (clones get no
        hooks). Returns the clone path."""
        clone = Path(self.temp.name) / name
        sh(["clone", "-q", str(self.work), str(clone)], cwd=self.temp.name)
        self.configure(clone)
        if extra_commit is not None:
            (clone / "a.txt").write_text(extra_commit)
            sh(["add", "a.txt"], cwd=clone)
            sh(["commit", "-qm", "foreign"], cwd=clone)
        sh(
            ["push", "-q", str(self.bare), f"HEAD:refs/heads/{self.br}"],
            cwd=clone,
        )
        return clone

    def test_dry_run_preserves_token_then_real_push_consumes(self):
        token = self.write_token()
        p = sh(["push", "--dry-run", "origin", self.br], cwd=self.work)
        self.assertIn("HERD PUSH AUTHORIZED", p.stderr)
        self.assertTrue(
            token.exists(),
            "git push --dry-run transfers nothing and must not consume the "
            "push approval",
        )
        p = sh(["push", "origin", self.br], cwd=self.work, check=False)
        self.assertEqual(
            p.returncode,
            0,
            f"authorized real push after a dry-run must succeed:\n{p.stderr}",
        )
        self.assertEqual(self.head(self.bare, self.br), self.head(self.work))
        self.assertFalse(
            token.exists(),
            "the approval must be consumed once the transfer lands",
        )

    def test_real_push_transfers_and_consumes(self):
        token = self.write_token()
        sh(["push", "origin", self.br], cwd=self.work)
        self.assertEqual(self.head(self.bare, self.br), self.head(self.work))
        self.assertFalse(token.exists())
        # With the token consumed, a further push attempt is refused by the
        # guard (nothing new is transferable anyway: the ref is up to date).
        p = sh(["push", "origin", self.br], cwd=self.work, check=False)
        self.assertNotIn("HERD PUSH AUTHORIZED", p.stderr)

    def test_fetch_of_foreign_commit_does_not_consume(self):
        clone = self.clone_and_push_to_bare("clone-foreign", extra_commit="foreign\n")
        token = self.write_token()
        sh(["fetch", "-q", "origin"], cwd=self.work)
        self.assertEqual(
            self.head(self.work, f"refs/remotes/origin/{self.br}"),
            self.head(clone),
        )
        self.assertNotEqual(self.head(clone), self.head(self.work))
        self.assertTrue(
            token.exists(),
            "a fetch that lands a commit other than the approved head must "
            "not burn the authorization",
        )

    def test_fetch_landing_the_approved_head_consumes(self):
        # The approved commit reaches the remote out-of-band (another
        # checkout); observing it on the tracking ref proves it is on the
        # remote, so the single-use approval is spent.
        self.clone_and_push_to_bare("clone-same")
        token = self.write_token()
        sh(["fetch", "-q", "origin"], cwd=self.work)
        self.assertEqual(
            self.head(self.work, f"refs/remotes/origin/{self.br}"),
            self.head(self.work),
        )
        self.assertFalse(
            token.exists(),
            "once the approved commit is observed on the remote-tracking ref "
            "the approval is spent (fail closed)",
        )

    def test_tag_push_identity_binds_exact_tag_object(self):
        tag = "v-test"
        sh(
            ["tag", "-a", tag, "-m", "test release"],
            cwd=self.work,
        )
        tag_oid = self.head(self.work, f"refs/tags/{tag}")

        cli_ident = herdctl.push_identity(
            self.work,
            target_tag=tag,
        )
        guard_ident = guards.push_identity(
            self.work,
            target_tag=tag,
        )

        for ident in (cli_ident, guard_ident):
            self.assertEqual(
                ident["source_ref"],
                f"refs/tags/{tag}",
            )
            self.assertEqual(
                ident["source_oid"],
                tag_oid,
            )
            self.assertEqual(
                ident["target_ref"],
                f"refs/tags/{tag}",
            )


    def test_tag_push_dry_run_then_real_push_succeeds(self):
        tag = "v-test"
        sh(
            ["tag", "-a", tag, "-m", "test release"],
            cwd=self.work,
        )
        ident = herdctl.push_identity(
            self.work,
            target_tag=tag,
        )
        token = dict(
            ident,
            alias="t",
            approved_at=1,
            expires_at=2**31,
        )
        path = guards.push_approval_path(self.work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token))

        refspec = f"refs/tags/{tag}:refs/tags/{tag}"

        p = sh(
            ["push", "--dry-run", "origin", refspec],
            cwd=self.work,
            check=False,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("HERD PUSH AUTHORIZED", p.stderr)
        self.assertTrue(path.exists())

        herdctl.push_tag_cmd(
            SimpleNamespace(
                repo=str(self.work),
                tag=tag,
            )
        )
        self.assertEqual(
            self.head(self.bare, f"refs/tags/{tag}"),
            self.head(self.work, f"refs/tags/{tag}"),
        )
        self.assertFalse(
            path.exists(),
            "tag push approval must be consumed once the approved tag lands",
        )


    def test_push_approval_valid_exception_contract(self):
        # Pins the internally-consistent exception dialects of both duplicated
        # copies: herdctl raises/catches SystemExit, guards RuntimeError.
        token = self.write_token()
        sh(["remote", "remove", "origin"], cwd=self.work)
        with self.assertRaises(SystemExit):
            herdctl.push_identity(self.work)
        ok, msg = herdctl.push_approval_valid(self.work)
        self.assertFalse(ok)
        self.assertIn("origin", msg)
        self.assertFalse(token.exists(), "broken identity invalidates the token")

        sh(["remote", "add", "origin", str(self.bare)], cwd=self.work)
        token = self.write_token()
        sh(["remote", "remove", "origin"], cwd=self.work)
        with self.assertRaises(RuntimeError):
            guards.push_identity(self.work)
        ok, msg = guards.push_approval_valid(self.work)
        self.assertFalse(ok)
        self.assertIn("origin", msg)
        self.assertFalse(token.exists(), "broken identity invalidates the token")


if __name__ == "__main__":
    unittest.main()
