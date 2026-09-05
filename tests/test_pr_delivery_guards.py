"""P1-A6: the guard's SECOND path, proven through the INSTALLED hooks.

Lead M1 and M5. Every case here runs real git in a temporary repository
with ``herdr.guards.install_git_guard`` installed and a local bare
remote, so what is measured is the hook process itself: its exit status
and what it printed. The order the hooks enforce — legacy token first,
delivery receipt second, refusal last — is asserted by execution, and
the delivery store is made absent, unreadable, group-readable, and
corrupt in turn while the legacy flows keep working and the receipt-free
refusal stays a legible sentence rather than a traceback.

No ``gh`` process can start here: the transport's GitHub half is the
same structurally isolated one ``tests/test_pr_delivery.py`` uses.

Standalone: PYTHONPATH=$PWD python3 tests/test_pr_delivery_guards.py
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hermetic_git import run_git, run_git_completed        # noqa: E402

from herdr import guards                                     # noqa: E402
from pr_delivery import authorization as auth                # noqa: E402
from pr_delivery import receipts                             # noqa: E402
from pr_delivery import store as store_module                # noqa: E402

from test_pr_delivery import (                               # noqa: E402
    BASE_BRANCH,
    BASE_REFRESH,
    COMMIT_STEP,
    GITHUB_URL,
    PUSH_STEP,
    SOURCE_BRANCH,
    DeliveryFixture,
    git,
)


def hook_git(argv, cwd):
    """A git invocation whose HOOK output is the evidence; returns the
    CompletedProcess without asserting on it."""
    return run_git_completed(["-C", str(cwd)] + list(argv), check=False)


class _GuardCase(unittest.TestCase):
    def setUp(self):
        self.fx = DeliveryFixture(self, hooks=True)
        self.fx.authorize()
        self.record = self.fx.record()
        self.source_ref = "refs/heads/" + SOURCE_BRANCH

    def _write(self, record):
        with self.fx.store.lock():
            document = self.fx.store.load()
            document["deliveries"][record["delivery_id"]] = record
            self.fx.store.save(document)

    def _executing(self, step, binding, phase):
        """Persist a receipt for ``step`` in state executing, exactly as
        the machine does immediately before the effect."""
        record = self.fx.record()
        record["phase"] = phase
        for earlier in auth.STEPS:
            if earlier == step:
                break
            record["steps"][earlier]["state"] = auth.STEP_NOT_NEEDED
        receipt = receipts.derive(record, step, binding, self.fx.clock())
        receipt["state"] = auth.RECEIPT_EXECUTING
        record["steps"][step]["receipt"] = receipt
        record["steps"][step]["state"] = auth.STEP_EXECUTING
        self._write(record)
        return receipt

    def _commit_binding(self, **overrides):
        live = guards._delivery_commit_live(self.fx.work)
        binding = dict(
            live,
            candidate_identity_digest=self.record["candidate"][
                "identity_digest_sha256"
            ],
            expected_tree_oid=self.fx.transport.write_tree(str(self.fx.work)),
            committer_name="Delivery Human",
            committer_email="human@example.com",
            message_sha256="1" * 64,
        )
        binding.update(overrides)
        return binding

    def _legacy_commit_token(self):
        ident = guards.repo_identity(self.fx.work)
        token = dict(ident, alias="t", approved_at=1, expires_at=2 ** 31)
        path = guards.approval_path(self.fx.work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token))
        return path

    def _legacy_push_token(self):
        ident = guards.push_identity(self.fx.work)
        token = dict(ident, alias="t", approved_at=1, expires_at=2 ** 31)
        path = guards.push_approval_path(self.fx.work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token))
        return path

    def _hook_commit(self):
        return hook_git(
            ["-c", "user.name=Delivery Human",
             "-c", "user.email=human@example.com",
             "-c", "commit.gpgsign=false", "commit", "-qm", "delivered"],
            cwd=self.fx.work,
        )

    def _land_commit_with_receipt(self):
        self._executing(COMMIT_STEP, self._commit_binding(),
                        auth.PHASE_BASE_CURRENT)
        completed = self._hook_commit()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return git("rev-parse", "HEAD", cwd=self.fx.work)

    def _push_binding(self, head, **overrides):
        binding = {
            "repository_realpath": self.record["repository"]["realpath"],
            "remote_name": "origin",
            "remote_url_exact": self.record["remote"]["url_exact"],
            "remote_url_push": self.record["remote"]["url_push"],
            "source_ref": self.source_ref,
            "source_commit": head,
            "destination_ref": self.source_ref,
            "expected_remote_old_oid": auth.ZERO_OID,
            "candidate_identity_digest": self.record["candidate"][
                "identity_digest_sha256"
            ],
        }
        binding.update(overrides)
        return binding


class CommitHookTests(_GuardCase):
    def test_no_receipt_and_no_token_is_refused_legibly(self):
        completed = self._hook_commit()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("HERD COMMIT BLOCKED", completed.stderr)
        self.assertIn("No approval exists", completed.stderr)
        self.assertIn("delivery receipt", completed.stderr)
        self.assertIn("no executing PR delivery receipt", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work),
                         self.fx.baseline)

    def test_valid_receipt_authorizes_exactly_one_commit(self):
        head = self._land_commit_with_receipt()
        self.assertNotEqual(head, self.fx.baseline)
        self.assertEqual(git("rev-parse", "HEAD^1", cwd=self.fx.work),
                         self.fx.baseline)
        # Replay: the same executing receipt no longer binds HEAD.
        (self.fx.work / "again.txt").write_text("x\n")
        git("add", "-A", cwd=self.fx.work)
        completed = self._hook_commit()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("head_before", completed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work), head)

    def test_receipt_bound_to_a_different_staged_diff_refuses(self):
        self._executing(COMMIT_STEP,
                        self._commit_binding(staged_sha256="0" * 64),
                        auth.PHASE_BASE_CURRENT)
        completed = self._hook_commit()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("staged_sha256", completed.stderr)

    def test_receipt_bound_to_a_different_branch_refuses(self):
        self._executing(COMMIT_STEP,
                        self._commit_binding(branch="other",
                                             source_ref="refs/heads/other"),
                        auth.PHASE_BASE_CURRENT)
        completed = self._hook_commit()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("branch", completed.stderr)

    def test_legacy_token_still_authorizes(self):
        token = self._legacy_commit_token()
        completed = self._hook_commit()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("HERD COMMIT PRE-CHECK AUTHORIZED", completed.stderr)
        self.assertNotIn("delivery receipt", completed.stderr)
        self.assertFalse(token.exists(), "the legacy token is consumed")

    def test_ambiguous_receipts_refuse(self):
        self._executing(COMMIT_STEP, self._commit_binding(),
                        auth.PHASE_BASE_CURRENT)
        record = self.fx.record()
        twin = json.loads(json.dumps(record))
        twin["delivery_id"] = "prd-twin"
        twin["authority_digest_sha256"] = auth.authority_digest(twin)
        twin["steps"][COMMIT_STEP]["receipt"]["delivery_id"] = "prd-twin"
        twin["steps"][COMMIT_STEP]["receipt"][
            "parent_authority_digest_sha256"
        ] = twin["authority_digest_sha256"]
        twin["steps"][COMMIT_STEP]["receipt"]["receipt_digest_sha256"] = (
            auth.receipt_digest(twin["steps"][COMMIT_STEP]["receipt"])
        )
        self._write(twin)
        completed = self._hook_commit()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ambiguous", completed.stderr)


class StoreFailureTests(_GuardCase):
    """M1: every store failure is a refusal on the receipt path only."""

    def _break_store(self, how):
        path = self.fx.store.path
        if how == "absent":
            os.unlink(path)
        elif how == "unreadable":
            os.chmod(path, 0)
        elif how == "group_readable":
            os.chmod(path, 0o640)
        elif how == "corrupt":
            with open(path, "w") as handle:
                handle.write("{not json")
        else:
            raise AssertionError(how)

    def test_every_store_failure_keeps_legacy_flows_and_refuses_legibly(self):
        """One fixture per failure mode (B1), three proofs each: the
        receipt-free refusal is a sentence, the legacy commit token still
        authorizes, and the legacy push token still transfers."""
        for how in ("absent", "unreadable", "group_readable", "corrupt"):
            with self.subTest(how=how):
                fx = DeliveryFixture(self, hooks=True)
                fx.authorize()
                self.fx = fx
                self._break_store(how)
                # (a) nothing at all: refused, legibly, no traceback.
                completed = self._hook_commit()
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("HERD COMMIT BLOCKED", completed.stderr)
                self.assertIn("delivery receipt", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                if how != "absent":
                    self.assertIn("unavailable", completed.stderr)
                ok, reason = receipts.guard_decision(
                    fx.work, COMMIT_STEP,
                    guards._delivery_commit_live(fx.work), time.time(),
                )
                self.assertFalse(ok)
                # (b) legacy commit token: authorized, exit 0.
                self._legacy_commit_token()
                completed = self._hook_commit()
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                # (c) legacy push token: transferred, exit 0.
                self._legacy_push_token()
                completed = hook_git(["push", "-q", "origin", SOURCE_BRANCH],
                                     cwd=fx.work)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertEqual(
                    fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                    git("rev-parse", "HEAD", cwd=fx.work),
                )


class PushHookTests(_GuardCase):
    def setUp(self):
        super(PushHookTests, self).setUp()
        self.head = self._land_commit_with_receipt()

    def _push(self, refspec=None):
        argv = ["push", "-q", "origin", refspec or SOURCE_BRANCH]
        return hook_git(argv, cwd=self.fx.work)

    def test_no_receipt_and_no_token_is_refused_legibly(self):
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("HERD PUSH BLOCKED", completed.stderr)
        self.assertIn("No push approval exists", completed.stderr)
        self.assertIn("delivery receipt", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))

    def test_valid_receipt_authorizes_exactly_one_push(self):
        self._executing(PUSH_STEP, self._push_binding(self.head),
                        auth.PHASE_COMMITTED)
        completed = self._push()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("HERD PUSH AUTHORIZED BY PR delivery receipt",
                      completed.stderr)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                         self.head)
        # Replay with a different commit: the receipt binds the exact one.
        (self.fx.work / "again.txt").write_text("x\n")
        git("add", "-A", cwd=self.fx.work)
        self._legacy_commit_token()
        self.assertEqual(self._hook_commit().returncode, 0)
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("source_commit", completed.stderr)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                         self.head)

    def test_receipt_bound_to_a_different_commit_refuses(self):
        self._executing(PUSH_STEP, self._push_binding("a" * 40),
                        auth.PHASE_COMMITTED)
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("source_commit", completed.stderr)
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))

    def test_receipt_bound_to_a_different_destination_refuses(self):
        self._executing(
            PUSH_STEP,
            self._push_binding(self.head, destination_ref="refs/heads/other"),
            auth.PHASE_COMMITTED,
        )
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("destination_ref", completed.stderr)
        completed = self._push("%s:refs/heads/%s" % (SOURCE_BRANCH,
                                                     BASE_BRANCH))
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + BASE_BRANCH),
                         self.fx.baseline)

    def test_post_authorization_push_rewrite_is_refused_by_the_hook(self):
        # Round-01 B2, executed through the installed pre-push hook: a
        # pushInsteadOf added after authorization changes the URL git
        # hands the hook; the receipt binds the authorized push URL.
        self._executing(PUSH_STEP, self._push_binding(self.head),
                        auth.PHASE_COMMITTED)
        evil = Path(self.fx.temp.name) / "evil.git"
        run_git("init", "-q", "--bare", "-b", BASE_BRANCH, str(evil))
        git("config", "url.%s.pushInsteadOf" % evil, GITHUB_URL,
            cwd=self.fx.work)
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("remote_url_push", completed.stderr)
        self.assertEqual(git("ls-remote", str(evil), cwd=self.fx.work), "")
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))
        # Removing the rewrite restores the bound URL: the same receipt
        # authorizes the transfer to the authorized remote.
        git("config", "--unset", "url.%s.pushInsteadOf" % evil,
            cwd=self.fx.work)
        completed = self._push()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH),
                         self.head)

    def test_receipt_bound_to_a_different_remote_refuses(self):
        self._executing(
            PUSH_STEP,
            self._push_binding(
                self.head, remote_url_exact="https://github.com/o/x.git",
            ),
            auth.PHASE_COMMITTED,
        )
        completed = self._push()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("remote_url_exact", completed.stderr)
        # A push straight to a URL is not the named remote either.
        self._executing(PUSH_STEP, self._push_binding(self.head),
                        auth.PHASE_COMMITTED)
        completed = hook_git(["push", "-q", str(self.fx.bare),
                              SOURCE_BRANCH], cwd=self.fx.work)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIsNone(self.fx.remote_oid("refs/heads/" + SOURCE_BRANCH))

    def test_force_push_is_still_refused_by_the_pretool_parser(self):
        ok, reason = guards.simple_git_push("git push --force origin x")
        self.assertFalse(ok)
        self.assertIn("Destructive", reason)

    def test_legacy_token_still_authorizes(self):
        token = self._legacy_push_token()
        completed = self._push()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("HERD PUSH AUTHORIZED:", completed.stderr)
        self.assertNotIn("delivery receipt", completed.stderr)
        self.assertFalse(token.exists())


class BaseRefreshHookTests(_GuardCase):
    def setUp(self):
        super(BaseRefreshHookTests, self).setUp()
        self.new_base = self.fx.advance_base({"README.md": "readme v2\n"})
        git("fetch", "-q", "origin", BASE_BRANCH, cwd=self.fx.work)

    def _refresh_binding(self, **overrides):
        binding = {
            "repository_realpath": self.record["repository"]["realpath"],
            "git_dir_realpath": self.record["repository"]["git_dir_realpath"],
            "remote_name": "origin",
            "remote_url_exact": self.record["remote"]["url_exact"],
            "remote_url_fetch": self.record["remote"]["url_fetch"],
            "source_ref": self.source_ref,
            "base_ref": "refs/heads/" + BASE_BRANCH,
            "old_base_oid": self.fx.baseline,
            "new_base_oid": self.new_base,
            "fast_forward": True,
            "base_changed_paths_digest": "2" * 64,
            "candidate_identity_digest": self.record["candidate"][
                "identity_digest_sha256"
            ],
        }
        binding.update(overrides)
        return binding

    def _move(self, new, old):
        return hook_git(["update-ref", self.source_ref, new, old],
                        cwd=self.fx.work)

    def test_receipt_authorizes_exactly_its_own_update_line(self):
        self._executing(BASE_REFRESH, self._refresh_binding(),
                        auth.PHASE_AUTHORIZED)
        # A different new value is refused...
        other = run_git("-C", str(self.fx.work), "commit-tree",
                        self.new_base + "^{tree}", "-m", "x")
        completed = self._move(other, self.fx.baseline)
        self.assertNotEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("new_base_oid", completed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work),
                         self.fx.baseline)
        # ...and the exact bound transition is allowed, once.
        completed = self._move(self.new_base, self.fx.baseline)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work),
                         self.new_base)
        completed = self._move(self.fx.baseline, self.new_base)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work),
                         self.new_base)

    def test_no_receipt_refuses_the_ref_move(self):
        completed = self._move(self.new_base, self.fx.baseline)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("HERD HISTORY UPDATE BLOCKED", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.fx.work),
                         self.fx.baseline)


class PretoolGuardUnchangedTests(unittest.TestCase):
    def test_pretool_guard_source_does_not_consult_receipts(self):
        import inspect
        source = inspect.getsource(guards.guard_pretool)
        self.assertNotIn("_delivery_receipt_decision", source)
        self.assertIn("approval_valid", source)
        self.assertIn("push_approval_valid", source)


if __name__ == "__main__":
    unittest.main(verbosity=1)
