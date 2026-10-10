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

import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

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
    SOURCE_REF,
    DeliveryFixture,
    UpdateFixture,
    _template,
    assert_git_reported_a_prepared_phase_hook_abort,
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



class PrUpdatePushHookTests(unittest.TestCase):
    """Case 15, through the INSTALLED hooks: a ``pr_update`` delivery's
    COMMIT lands through the pre-commit guard on its receipt alone, and
    its PUSH receipt binds the APPROVED pull request head as the expected
    remote old OID: the pre-push hook accepts exactly that and refuses a
    remote at anything else, even a push git itself would accept."""

    def setUp(self):
        self.fx = UpdateFixture(self, hooks=True)
        self.delivery_id = self.fx.authorize_update()
        self.assertFalse(guards.approval_path(self.fx.work).exists())
        self.assertEqual(self.fx.machine.advance_once(self.delivery_id),
                         "advanced")
        record = self.fx.record(self.delivery_id)
        self.assertEqual(record["phase"], auth.PHASE_COMMITTED)
        self.commit_oid = git("rev-parse", "HEAD", cwd=self.fx.work)
        self.assertEqual(git("rev-parse", "HEAD^1", cwd=self.fx.work),
                         self.fx.baseline)

    def _executing_push(self):
        """Persist the PUSH receipt exactly as the machine derives it for
        this kind, in state executing, immediately before the effect."""
        record = self.fx.record(self.delivery_id)
        binding = {
            "repository_realpath": record["repository"]["realpath"],
            "remote_name": "origin",
            "remote_url_exact": record["remote"]["url_exact"],
            "remote_url_push": record["remote"]["url_push"],
            "source_ref": SOURCE_REF,
            "source_commit": self.commit_oid,
            "destination_ref": SOURCE_REF,
            "expected_remote_old_oid": record["original_baseline"][
                "commit_sha"],
            "candidate_identity_digest": record["candidate"][
                "identity_digest_sha256"],
        }
        receipt = receipts.derive(record, PUSH_STEP, binding,
                                  self.fx.clock())
        receipt["state"] = auth.RECEIPT_EXECUTING
        record["steps"][PUSH_STEP]["receipt"] = receipt
        record["steps"][PUSH_STEP]["state"] = auth.STEP_EXECUTING
        with self.fx.store.lock():
            document = self.fx.store.load()
            document["deliveries"][self.delivery_id] = record
            self.fx.store.save(document)
        return receipt

    def _transfer(self):
        return hook_git(["push", "-q", "origin", SOURCE_BRANCH],
                        cwd=self.fx.work)

    def test_the_approved_head_as_remote_old_oid_is_accepted(self):
        self._executing_push()
        completed = self._transfer()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("HERD PUSH AUTHORIZED BY PR delivery receipt",
                      completed.stderr)
        self.assertEqual(self.fx.remote_oid(SOURCE_REF), self.commit_oid)

    def test_a_remote_old_oid_other_than_the_approved_head_is_refused(self):
        self._executing_push()
        # The remote head branch moved BACK to an ancestor of the approved
        # head (a direct ref write in the temporary bare repository): git
        # would accept the fast-forward transfer, so only the hook decides.
        older = _template()["baseline"]
        git("update-ref", SOURCE_REF, older, cwd=self.fx.bare)
        completed = self._transfer()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("HERD PUSH BLOCKED", completed.stderr)
        self.assertIn("expected_remote_old_oid", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(self.fx.remote_oid(SOURCE_REF), older)

    def test_the_machine_completes_through_the_same_hook(self):
        self.assertEqual(self.fx.machine.advance(self.delivery_id),
                         "complete")
        self.assertEqual(self.fx.remote_oid(SOURCE_REF), self.commit_oid)
        record = self.fx.record(self.delivery_id)
        self.assertEqual(record["steps"][PUSH_STEP]["receipt"]["binding"][
            "expected_remote_old_oid"], self.fx.baseline)
        self.assertFalse(guards.push_approval_path(self.fx.work).exists())



class PrUpdateProposedParentsTests(unittest.TestCase):
    """Round 03, the pre-ref-movement enforcement at its two halves:
    ``herdr.guards._delivery_proposed_parents`` reads the COMPLETE parent set
    of the commit object a reference transaction proposes for HEAD's branch,
    BEFORE the ref moves; ``receipts.guard_decision`` refuses a ``pr_update``
    COMMIT receipt unless that set is exactly [the approved head]. A
    ``pull_request`` receipt never consults the fact (legacy unchanged)."""

    def _executing_commit(self, fx, delivery_id, phase):
        record = fx.record(delivery_id)
        record["phase"] = phase
        binding = dict(
            guards._delivery_commit_live(fx.work),
            candidate_identity_digest=record["candidate"][
                "identity_digest_sha256"],
            expected_tree_oid=fx.transport.write_tree(str(fx.work)),
            committer_name="Delivery Human",
            committer_email="human@example.com",
            message_sha256="1" * 64,
        )
        if record["mode"] == auth.MODE_PULL_REQUEST:
            record["steps"][BASE_REFRESH]["state"] = auth.STEP_NOT_NEEDED
        receipt = receipts.derive(record, COMMIT_STEP, binding, fx.clock())
        receipt["state"] = auth.RECEIPT_EXECUTING
        record["steps"][COMMIT_STEP]["receipt"] = receipt
        record["steps"][COMMIT_STEP]["state"] = auth.STEP_EXECUTING
        with fx.store.lock():
            document = fx.store.load()
            document["deliveries"][delivery_id] = record
            fx.store.save(document)

    def _decide(self, fx, **extra):
        live = dict(guards._delivery_commit_live(fx.work), **extra)
        return receipts.guard_decision(fx.work, COMMIT_STEP, live,
                                       fx.clock())

    def test_a_pr_update_commit_needs_exactly_the_approved_head_as_parents(
            self):
        fx = UpdateFixture(self)
        delivery_id = fx.authorize_update()
        self._executing_commit(fx, delivery_id, auth.PHASE_AUTHORIZED)
        approved = fx.baseline
        ok, reason = self._decide(fx, proposed_commit_parents=[approved])
        self.assertTrue(ok, reason)
        for parents in ([approved, "f" * 40], ["f" * 40, approved],
                        ["f" * 40], [], None):
            with self.subTest(parents=parents):
                ok, reason = self._decide(fx,
                                          proposed_commit_parents=parents)
                self.assertFalse(ok)
                self.assertIn(receipts.PROBLEM_PROPOSED_PARENTS, reason)
        # The pre-commit hook has no commit object to read, so it supplies
        # no such fact; its decision on the binding is as before.
        ok, reason = self._decide(fx)
        self.assertTrue(ok, reason)

    def test_a_pull_request_commit_receipt_never_consults_the_fact(self):
        fx = DeliveryFixture(self)
        fx.authorize()
        self._executing_commit(fx, "prd-test", auth.PHASE_BASE_CURRENT)
        for parents in ([fx.baseline, "f" * 40], None, []):
            with self.subTest(parents=parents):
                ok, reason = self._decide(fx,
                                          proposed_commit_parents=parents)
                self.assertTrue(ok, reason)

    def test_the_guard_reads_the_complete_parent_set_or_none(self):
        fx = UpdateFixture(self)
        approved = fx.baseline
        single = run_git("-C", str(fx.work), "commit-tree",
                         approved + "^{tree}", "-p", approved, "-m", "one")
        merged = run_git("-C", str(fx.work), "commit-tree",
                         approved + "^{tree}", "-p", approved, "-p",
                         _template()["baseline"], "-m", "two")
        read = guards._delivery_proposed_parents
        self.assertEqual(
            read(fx.work, [(approved, single, SOURCE_REF)], SOURCE_REF),
            [approved])
        self.assertEqual(
            read(fx.work, [(approved, merged, SOURCE_REF)], SOURCE_REF),
            [approved, _template()["baseline"]])
        # A root commit has an empty parent set: readable, and refused by
        # the receipt check (it is not [approved head]).
        root = run_git("-C", str(fx.work), "commit-tree",
                       approved + "^{tree}", "-m", "root")
        self.assertEqual(
            read(fx.work, [(approved, root, SOURCE_REF)], SOURCE_REF), [])
        # FAIL CLOSED: anything that cannot be read exactly is None (which
        # the receipt check refuses, with the reason in its message), never
        # a pass, and nothing raises out of the hook helper.
        tree = run_git("-C", str(fx.work), "rev-parse",
                       approved + "^{tree}")
        blob = run_git("-C", str(fx.work), "rev-parse",
                       approved + ":keep.txt")
        cases = {
            "no update of HEAD's branch": [],
            "two updates of HEAD's branch": [
                (approved, single, SOURCE_REF),
                (approved, merged, SOURCE_REF)],
            "update of another ref only": [
                (approved, single, "refs/heads/other")],
            "missing object": [(approved, "e" * 40, SOURCE_REF)],
            "tree object, not a commit": [(approved, tree, SOURCE_REF)],
            "blob object, not a commit": [(approved, blob, SOURCE_REF)],
            "option-shaped value": [(approved, "--version", SOURCE_REF)],
            "short oid": [(approved, single[:12], SOURCE_REF)],
            "uppercase oid": [(approved, single.upper(), SOURCE_REF)],
            "non-hex oid": [(approved, "g" * 40, SOURCE_REF)],
        }
        for label, updates in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(read(fx.work, updates, SOURCE_REF))
        # A read whose output is malformed or truncated, or that raises.
        valid = [(approved, single, SOURCE_REF)]
        for label, output in (
            ("short parent id", approved[:39]),
            ("non-hex parent id", "z" * 40),
            ("truncated second id", approved + "\n" + approved[:20]),
            ("trailing garbage", approved + " junk"),
        ):
            with self.subTest(case=label):
                with mock.patch.object(guards, "gitout",
                                       return_value=output):
                    self.assertIsNone(read(fx.work, valid, SOURCE_REF))
        for error in (RuntimeError("fatal: bad object"), OSError("gone")):
            with self.subTest(case=type(error).__name__):
                with mock.patch.object(guards, "gitout",
                                       side_effect=error):
                    self.assertIsNone(read(fx.work, valid, SOURCE_REF))
        # Empty output reads as an empty parent set, which is refused.
        with mock.patch.object(guards, "gitout", return_value=""):
            self.assertEqual(read(fx.work, valid, SOURCE_REF), [])


class _ExecutingUpdateCommitMixin(object):

    def _executing_commit(self, fx, delivery_id):
        record = fx.record(delivery_id)
        binding = dict(
            guards._delivery_commit_live(fx.work),
            candidate_identity_digest=record["candidate"][
                "identity_digest_sha256"],
            expected_tree_oid=fx.transport.write_tree(str(fx.work)),
            committer_name="Delivery Human",
            committer_email="human@example.com",
            message_sha256="1" * 64,
        )
        receipt = receipts.derive(record, COMMIT_STEP, binding, fx.clock())
        receipt["state"] = auth.RECEIPT_EXECUTING
        record["steps"][COMMIT_STEP]["receipt"] = receipt
        record["steps"][COMMIT_STEP]["state"] = auth.STEP_EXECUTING
        with fx.store.lock():
            document = fx.store.load()
            document["deliveries"][delivery_id] = record
            fx.store.save(document)

    def _no_approval_exists(self, fx):
        for path in (guards.approval_path(fx.work),
                     guards.merge_approval_path(fx.work),
                     guards.push_approval_path(fx.work)):
            self.assertFalse(path.exists(), path)


class PrUpdateMergeGateInterleaveTests(_ExecutingUpdateCommitMixin,
                                       unittest.TestCase):
    """The ``prepared`` block of ``guard_reference_transaction`` runs the
    separate merge gate FIRST and the ``pr_update`` COMMIT receipt (with
    the proposed parent set) after it; the ``committed`` phase retires
    approvals through ``_consume_merge_on_commit`` and ``_retire``. Proven
    by execution, in this order: one new commit passes the merge gate and
    reaches the COMMIT receipt with its parent set read; a merge is refused
    by the merge gate with the COMMIT receipt never consulted; and a
    commit landed through the INSTALLED hooks leaves the receipt to the
    machine, retires nothing else, and authorizes nothing further."""

    def _reference(self, fx, phase, old, new):
        """One in-process ``reference-transaction`` decision on the update
        of HEAD's branch from ``old`` to ``new``: ``(exit code, stderr,
        consulted)``, ``consulted`` listing every delivery receipt lookup
        as ``(step, live facts, ok)`` in order."""
        consulted = []
        real = guards._delivery_receipt_decision

        def recording(repo, step, live):
            ok, reason = real(repo, step, live)
            consulted.append((step, dict(live), ok))
            return ok, reason

        stdin = io.StringIO("%s %s %s\n" % (old, new, SOURCE_REF))
        errors = io.StringIO()
        with mock.patch.object(guards, "_delivery_receipt_decision",
                               recording), \
                mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(sys, "stderr", errors):
            code = guards.guard_reference_transaction(fx.work, phase)
        return code, errors.getvalue(), consulted

    def _ledger(self, fx):
        """The approval ledger's exact bytes, or None when there is none:
        a retirement or a mint would append to it."""
        path = guards.ledger_path(fx.work)
        return path.read_bytes() if path.exists() else None

    def _update(self):
        fx = UpdateFixture(self)
        delivery_id = fx.authorize_update()
        self._executing_commit(fx, delivery_id)
        self.assertEqual(git("symbolic-ref", "-q", "HEAD", cwd=fx.work),
                         SOURCE_REF)
        self._no_approval_exists(fx)
        return fx

    def test_one_new_commit_passes_the_merge_gate_to_the_commit_receipt(self):
        fx = self._update()
        approved = fx.baseline
        single = run_git("-C", str(fx.work), "commit-tree",
                         approved + "^{tree}", "-p", approved, "-m", "one")
        self.assertIs(guards._identifiable_merge(
            fx.work, [(approved, single)]), False)
        code, errors, consulted = self._reference(fx, "prepared", approved,
                                                  single)
        self.assertEqual(code, 0, errors)
        self.assertNotIn("HERD MERGE BLOCKED", errors)
        self.assertEqual([(step, ok) for step, _, ok in consulted],
                         [(COMMIT_STEP, True)])
        live = consulted[0][1]
        self.assertEqual(live[receipts.PROPOSED_PARENTS_FACT], [approved])
        self.assertEqual(live["head_before"], approved)
        self._no_approval_exists(fx)

    def test_a_merge_is_refused_by_the_merge_gate_not_the_commit_receipt(
            self):
        fx = self._update()
        approved = fx.baseline
        foreign = _template()["baseline"]
        merge_commit = run_git("-C", str(fx.work), "commit-tree",
                               approved + "^{tree}", "-p", approved, "-p",
                               foreign, "-m", "two")
        first = run_git("-C", str(fx.work), "commit-tree",
                        approved + "^{tree}", "-p", approved, "-m", "a")
        second = run_git("-C", str(fx.work), "commit-tree",
                         approved + "^{tree}", "-p", first, "-m", "b")
        for label, new in (("merge commit", merge_commit),
                           ("multi-commit fast-forward", second)):
            with self.subTest(update=label):
                self.assertIs(guards._identifiable_merge(
                    fx.work, [(approved, new)]), True)
                code, errors, consulted = self._reference(
                    fx, "prepared", approved, new)
                self.assertEqual(code, 1, errors)
                self.assertIn("HERD MERGE BLOCKED", errors)
                self.assertIn("a commit approval never authorizes a merge",
                              errors)
                self.assertNotIn("Traceback", errors)
                # Only the BASE_REFRESH receipt is an alternative authority
                # for a merge; the executing COMMIT receipt never is.
                self.assertEqual([(step, ok) for step, _, ok in consulted],
                                 [(BASE_REFRESH, False)])
                self.assertEqual(git("rev-parse", SOURCE_REF, cwd=fx.work),
                                 approved)
        self._no_approval_exists(fx)

    def test_the_committed_phase_leaves_the_update_receipt_to_the_machine(
            self):
        fx = UpdateFixture(self, hooks=True)
        approved = fx.baseline
        delivery_id = fx.authorize_update()
        self._no_approval_exists(fx)
        ledger = self._ledger(fx)
        # Through the INSTALLED pre-commit and reference-transaction hooks
        # (prepared, then committed), on the receipt alone.
        self.assertEqual(fx.machine.advance_once(delivery_id), "advanced")
        record = fx.record(delivery_id)
        self.assertEqual(record["phase"], auth.PHASE_COMMITTED)
        self.assertEqual(record["steps"][COMMIT_STEP]["receipt"]["state"],
                         auth.RECEIPT_SUCCEEDED)
        head = git("rev-parse", "HEAD", cwd=fx.work)
        self.assertEqual(run_git("-C", str(fx.work), "rev-parse",
                                 head + "^@").split(), [approved])
        self._no_approval_exists(fx)
        self.assertEqual(self._ledger(fx), ledger)
        # The committed phase, replayed for the same update: it consults no
        # receipt, writes nothing to the delivery store and retires nothing.
        before = fx.store.load()
        code, errors, consulted = self._reference(fx, "committed", approved,
                                                  head)
        self.assertEqual(code, 0, errors)
        self.assertEqual(consulted, [])
        self.assertEqual(fx.store.load(), before)
        self._no_approval_exists(fx)
        self.assertEqual(self._ledger(fx), ledger)
        # The spent receipt authorizes no further commit.
        further = run_git("-C", str(fx.work), "commit-tree",
                          head + "^{tree}", "-p", head, "-m", "again")
        code, errors, consulted = self._reference(fx, "prepared", head,
                                                  further)
        self.assertEqual(code, 1, errors)
        self.assertIn("HERD HISTORY UPDATE BLOCKED", errors)
        self.assertEqual(consulted[0][0], COMMIT_STEP)
        self.assertEqual(consulted[0][1][receipts.PROPOSED_PARENTS_FACT],
                         [head])
        self.assertEqual([ok for _, _, ok in consulted],
                         [False] * len(consulted))
        self.assertEqual(git("rev-parse", "HEAD", cwd=fx.work), head)


class PrUpdateReferenceTransactionHookTests(_ExecutingUpdateCommitMixin,
                                            unittest.TestCase):
    """The LATER gate, through the INSTALLED ``reference-transaction`` hook,
    with a ``pr_update`` COMMIT receipt executing: a branch update that is
    not exactly one new commit on the approved head is aborted in the
    ``prepared`` phase BEFORE the ref moves, by the merge gate when the
    update is identifiable as a merge and by the COMMIT receipt's
    proposed-parent check when it is not; the receipt is not spent and no
    approval is consumed or created. Exactly one new commit is admitted
    (the positive control). A real ``git update-ref`` drives each
    transaction: a real commit builds a merge only from a MERGE_HEAD present
    when it starts, which the pre-commit merge gate refuses first
    (tests/test_pr_delivery.py, the racing-merge case), so this is where the
    later gate's defense in depth is exercised through the installed hook."""

    def setUp(self):
        self.fx = UpdateFixture(self, hooks=True)
        self.delivery_id = self.fx.authorize_update()
        self.approved = self.fx.baseline
        self._executing_commit(self.fx, self.delivery_id)
        self._no_approval_exists(self.fx)

    def _move(self, new):
        return hook_git(["update-ref", SOURCE_REF, new, self.approved],
                        cwd=self.fx.work)

    def _object(self, *parents):
        argv = ["-C", str(self.fx.work), "commit-tree",
                self.approved + "^{tree}"]
        for parent in parents:
            argv += ["-p", parent]
        return run_git(*(argv + ["-m", "probe"]))

    def _assert_aborted_before_the_ref_moved(self, completed):
        self.assertNotEqual(completed.returncode, 0, completed.stderr)
        assert_git_reported_a_prepared_phase_hook_abort(self,
                                                        completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(git("rev-parse", SOURCE_REF, cwd=self.fx.work),
                         self.approved)
        receipt = self.fx.record(self.delivery_id)["steps"][COMMIT_STEP][
            "receipt"]
        self.assertEqual(receipt["state"], auth.RECEIPT_EXECUTING)
        self._no_approval_exists(self.fx)

    def test_a_merge_is_aborted_by_the_merge_gate_before_the_ref_moves(self):
        foreign = _template()["baseline"]
        merge_commit = self._object(self.approved, foreign)
        self.assertIs(guards._identifiable_merge(
            self.fx.work, [(self.approved, merge_commit)]), True)
        completed = self._move(merge_commit)
        self._assert_aborted_before_the_ref_moved(completed)
        self.assertIn("HERD MERGE BLOCKED: this branch update is a merge and"
                      " needs the merge approval for exactly it",
                      completed.stderr)
        self.assertNotIn("HERD HISTORY UPDATE BLOCKED", completed.stderr)

    def test_a_commit_not_on_the_approved_head_is_aborted_by_the_receipt(
            self):
        sibling = self._object(_template()["baseline"])
        root = self._object()
        for label, new in (("one commit on another parent", sibling),
                           ("a root commit", root)):
            with self.subTest(update=label):
                self.assertIs(guards._identifiable_merge(
                    self.fx.work, [(self.approved, new)]), False)
                completed = self._move(new)
                self._assert_aborted_before_the_ref_moved(completed)
                self.assertIn("HERD HISTORY UPDATE BLOCKED",
                              completed.stderr)
                self.assertNotIn("HERD MERGE BLOCKED", completed.stderr)

    def test_exactly_one_new_commit_on_the_approved_head_is_admitted(self):
        single = self._object(self.approved)
        completed = self._move(single)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("BLOCKED", completed.stderr)
        self.assertEqual(git("rev-parse", SOURCE_REF, cwd=self.fx.work),
                         single)


if __name__ == "__main__":
    unittest.main(verbosity=1)
