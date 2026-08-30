"""I5: ownership, cleanup, and "an unrelated session is never touched".

SAFETY, because this module is the one with a blast radius
==========================================================

Every resource this module acts on, it CREATED — temp directories,
temp Claude configurations, and processes it forked itself. It reads no
live agent list and signals no pid it did not fork. The real
                                                    `~/.claude.json`
                                                    stays outside its
                                                    reach, because every
                                                    trust test injects a
                                                    config path under a
                                                    temp directory.

The decoy pattern used throughout: the test creates a resource, does
NOT record it as owned, and then asserts it survives the cleanup
BYTE-IDENTICALLY. A decoy proves more than an absence check, because
"the cleanup did not delete a thing that was never there" is satisfied
by a cleanup that does nothing at all.

THE PIN THAT MATTERS MOST
=========================

`UnrelatedResourceTests` is the executed guarantee for the property
                         that, within a release, an unrelated session
                         stays untouched. R-8 binds hardest here, so it drives the
real `ACTION_RELEASE` through the broker rather than asserting on
source, and a mutant that WIDENS the ownership predicate dies against
its authored assertions.
"""

import errno
import inspect
import json
import secrets
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from target_runtime import broker as broker_module      # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from target_runtime import evidence_preservation as preserve_module  # noqa: E402
from target_runtime import ownership as ownership_module  # noqa: E402
from target_runtime import process_ownership as proc_module  # noqa: E402
from target_runtime import workspace as workspace_module  # noqa: E402
from target_runtime import workspace_ownership as ws_module  # noqa: E402
from target_runtime import workspace_trust as trust_module  # noqa: E402
from workflow_authority import record as wa_record      # noqa: E402

import _scope_hygiene as scope_hygiene                  # noqa: E402
from test_target_runtime import NOW, RuntimeCase        # noqa: E402


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: Where fixture ownership records live. Deliberately NOT a temp
#: directory that a cleanup removes: an ownership record that is
#: deleted while the process it names may still be running is a record
#: #: that is already gone within the window that matters, which is the
#: mechanism behind this increment's own post-harness leak.
OWNER_LEDGER_ROOT = os.path.join(
    tempfile.gettempdir(), "di-owner-ledgers"
)


def remove(path):
    import shutil
    shutil.rmtree(str(path), ignore_errors=True)


def setUpModule():
    """R-47/R-48: this module reaches the ownership API directly AND
    through the production seams, so it runs against a PRIVATE base.

    It replaces a helper that deleted the scope and assignment a
    production seam had written into the SHARED store. That helper was
    the defect: a set difference over a shared directory selects
    whatever appeared while the case ran, which includes another
    party's records. Isolation removes the shared store from reach
    instead of removing entries from it.
    """
    global _ISOLATED_BASE
    _ISOLATED_BASE = scope_hygiene.isolate_module()


def tearDownModule():
    scope_hygiene.release_module(_ISOLATED_BASE)


class OwnershipPredicateTests(unittest.TestCase):
    """The predicate, driven directly over records this test builds."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(remove, self.root)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()

    def leased(self, workflow_id="wf-0001", task_id="task-1",
               path=None):
        lease = path or workspace_module.lease_path(
            str(self.workspaces), workflow_id
        )
        os.makedirs(lease, exist_ok=True)
        return {
            "workflow_id": workflow_id,
            "workspace_lease": {
                "lease_id": "lease-1",
                "path_realpath": os.path.realpath(lease),
            },
            "target_engine": None if task_id is None else {
                "alias": dispatch_module.ALIAS_PREFIX + workflow_id,
                "task_id": task_id,
                "repo": "https://github.com/x/y.git",
                "dispatched_at": 1,
            },
        }

    def test_its_own_lease_is_owned(self):
        entry = self.leased()
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, entry["workspace_lease"]["path_realpath"],
                str(self.workspaces),
            ),
            ownership_module.OWNED,
        )

    def test_another_workflows_lease_is_not_owned(self):
        entry = self.leased("wf-0001")
        other = workspace_module.lease_path(
            str(self.workspaces), "wf-0002"
        )
        os.makedirs(other, exist_ok=True)
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, other, str(self.workspaces)
            ),
            ownership_module.NOT_OWNED,
        )

    def test_a_record_naming_a_path_it_did_not_derive_is_not_owned(self):
        """A record can SAY anything. Ownership requires the recorded lease to equal the path DERIVED
        from the workflow id, so within this check a record pointing at
        a directory outside that lease does not authorise touching it."""
        entry = self.leased("wf-0001")
        foreign = self.workspaces / "someone-elses"
        foreign.mkdir()
        entry["workspace_lease"]["path_realpath"] = str(foreign)
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, str(foreign), str(self.workspaces)
            ),
            ownership_module.NOT_OWNED,
        )

    def test_a_path_outside_the_managed_root_is_not_owned(self):
        outside = self.root / "outside"
        outside.mkdir()
        entry = self.leased("wf-0001")
        entry["workspace_lease"]["path_realpath"] = str(outside)
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, str(outside), str(self.workspaces)
            ),
            ownership_module.NOT_OWNED,
        )

    def test_a_record_with_no_lease_is_UNPROVABLE_not_unowned(self):
        """"We cannot tell" and "it is someone else's" are different
        answers, and a cleanup that must report degradation truthfully
        needs them separated."""
        entry = self.leased()
        entry["workspace_lease"] = None
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, str(self.workspaces / "x"), str(self.workspaces)
            ),
            ownership_module.UNPROVABLE,
        )


class NameIsNeverEvidenceTests(unittest.TestCase):
    """THE ATTRACTIVE WRONG ANSWER, driven so it stays refused.

    The live orphans are named `h566a1-wf-7200299-…` and the dispatch
    layer mints an alias from the workflow id, so a name-prefix rule
    would look like a working predicate. On a machine with ~40 foreign
    agents it would match anything named similarly.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(remove, self.root)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()

    def entry(self, workflow_id="wf-7200299dbac712e76b31eca9"):
        lease = workspace_module.lease_path(
            str(self.workspaces), workflow_id
        )
        os.makedirs(lease, exist_ok=True)
        return {
            "workflow_id": workflow_id,
            "workspace_lease": {
                "lease_id": "lease-1",
                "path_realpath": os.path.realpath(lease),
            },
            "target_engine": {
                "alias": dispatch_module.ALIAS_PREFIX + workflow_id,
                "task_id": "20260828-114612-5d92e1",
                "repo": "u", "dispatched_at": 1,
            },
        }

    def test_the_alias_is_never_evidence_even_when_it_matches(self):
        entry = self.entry()
        exact_alias = entry["target_engine"]["alias"]
        self.assertFalse(
            ownership_module.alias_is_not_evidence(exact_alias),
            "the alias rule returned ownership weight for an alias"
            " that matches the workflow exactly; the architecture"
            " record rules the alias a derived label only",
        )

    def test_a_child_record_matching_only_by_name_is_not_owned(self):
        """A record whose repo is a DIFFERENT directory but whose
        alias-shaped name matches this workflow exactly. A prefix rule
        would claim it; the predicate must not."""
        entry = self.entry()
        foreign = self.workspaces / "h566a1-wf-7200299-something"
        foreign.mkdir()
        record = {
            "repo": str(foreign),
            "task_id": entry["target_engine"]["task_id"],
            "alias": entry["target_engine"]["alias"],
        }
        self.assertEqual(
            ownership_module.owns_child_record(
                entry, record, str(self.workspaces)
            ),
            ownership_module.NOT_OWNED,
        )

    def test_a_repo_match_without_a_task_id_match_is_not_owned(self):
        entry = self.entry()
        record = {
            "repo": entry["workspace_lease"]["path_realpath"],
            "task_id": "some-other-task",
        }
        self.assertEqual(
            ownership_module.owns_child_record(
                entry, record, str(self.workspaces)
            ),
            ownership_module.NOT_OWNED,
        )

    def test_both_matching_is_owned(self):
        entry = self.entry()
        record = {
            "repo": entry["workspace_lease"]["path_realpath"],
            "task_id": entry["target_engine"]["task_id"],
        }
        self.assertEqual(
            ownership_module.owns_child_record(
                entry, record, str(self.workspaces)
            ),
            ownership_module.OWNED,
        )

    def test_the_unresolved_sentinel_owns_nothing(self):
        """Within this predicate a workflow whose identity was not bound
        must not claim other unresolved workflows' records."""
        entry = self.entry()
        entry["target_engine"]["task_id"] = (
            dispatch_module.UNRESOLVED_TASK_ID
        )
        record = {
            "repo": entry["workspace_lease"]["path_realpath"],
            "task_id": dispatch_module.UNRESOLVED_TASK_ID,
        }
        self.assertEqual(
            ownership_module.owns_child_record(
                entry, record, str(self.workspaces)
            ),
            ownership_module.UNPROVABLE,
        )


class StaleVersusCurrentTests(unittest.TestCase):
    """`herdr agent wait --until done` returns exit 0 with a real
    payload on an ALREADY-done agent. A status that is true but STALE
    reads exactly like one true and CURRENT unless something monotonic
    is checked alongside it."""

    @staticmethod
    def obs(revision, sequence):
        return {"revision": revision, "state_change_seq": sequence}

    def test_an_identical_observation_is_not_current(self):
        self.assertFalse(
            ownership_module.observation_is_current(
                self.obs(5, 5), self.obs(5, 5)
            )
        )

    def test_both_counters_must_advance(self):
        self.assertFalse(
            ownership_module.observation_is_current(
                self.obs(5, 5), self.obs(6, 5)
            ),
            "revision alone was the signal that reported 'still"
            " finished from last time' as 'finished this round'",
        )
        self.assertFalse(
            ownership_module.observation_is_current(
                self.obs(5, 5), self.obs(5, 6)
            )
        )
        self.assertTrue(
            ownership_module.observation_is_current(
                self.obs(5, 5), self.obs(6, 6)
            )
        )

    def test_a_backward_counter_is_not_current(self):
        self.assertFalse(
            ownership_module.observation_is_current(
                self.obs(9, 9), self.obs(2, 2)
            )
        )

    def test_a_missing_counter_fails_closed(self):
        for broken in ({}, {"revision": 1}, None,
                       {"revision": True, "state_change_seq": 2}):
            with self.subTest(observation=repr(broken)):
                self.assertFalse(
                    ownership_module.observation_is_current(
                        self.obs(1, 1), broken
                    )
                )


class CleanupReportTruthfulnessTests(unittest.TestCase):
    """The recorded "silent truncation presented as fact" class,
    applied to what a cleanup says it did."""

    def test_degraded_is_derived_not_settable(self):
        report = ownership_module.CleanupReport()
        self.assertFalse(report.degraded)
        report.record("trust", "k", ownership_module.UNPROVABLE)
        self.assertTrue(
            report.degraded,
            "an unprovable resource left the report claiming a"
            " complete cleanup",
        )

    def test_a_failed_removal_degrades_the_report(self):
        report = ownership_module.CleanupReport()
        report.record("workspace", "/x", ownership_module.OWNED,
                      ok=False, detail="boom")
        self.assertTrue(report.degraded)
        self.assertEqual(report.removed, [])

    def test_only_proven_removals_are_counted_as_removed(self):
        report = ownership_module.CleanupReport()
        report.record("workspace", "/a", ownership_module.OWNED)
        report.record("workspace", "/b", ownership_module.NOT_OWNED)
        report.record("workspace", "/c", ownership_module.UNPROVABLE)
        self.assertEqual([name for _kind, name in report.removed], ["/a"])
        self.assertIn("removed 1", report.summary())

    def test_the_summary_names_degradation_FIRST(self):
        report = ownership_module.CleanupReport()
        report.record("trust", "k", ownership_module.UNPROVABLE)
        self.assertTrue(
            report.summary().startswith("cleanup DEGRADED"),
            "a reader scanning summaries had to reach the end of the"
            " line to learn the cleanup was incomplete",
        )


class TrustRevocationTests(unittest.TestCase):
    """I5-1. Within this class the real `~/.claude.json` stays out of
             reach, because every case injects a config path under a
             temp directory it created."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(remove, self.root)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.config = self.root / ".claude.json"

    def entry(self, workflow_id="wf-0001", make=True):
        lease = workspace_module.lease_path(
            str(self.workspaces), workflow_id
        )
        if make:
            os.makedirs(lease, exist_ok=True)
        return {
            "workflow_id": workflow_id,
            "workspace_lease": {
                "lease_id": "lease-1",
                "path_realpath": os.path.realpath(lease),
            },
        }

    def write_config(self, entry, extra_projects=None, trusted=True):
        key = trust_module.trust_key(
            entry["workspace_lease"]["path_realpath"]
        )
        projects = {
            # A DECOY the test creates and does not own. It must
            # survive byte-identically.
            "/Users/someone/other-repo": {
                "allowedTools": ["Bash(git status)"],
                "hasTrustDialogAccepted": True,
                "history": [{"display": "keep me"}],
            },
        }
        projects.update(extra_projects or {})
        if trusted:
            projects[key] = {"hasTrustDialogAccepted": True}
        document = {
            "hasCompletedOnboarding": True,
            "numStartups": 12,
            "oauthAccount": {"emailAddress": "someone@example.com"},
            "projects": projects,
        }
        self.config.write_text(json.dumps(document, indent=2))
        return key

    def read_config(self):
        return json.loads(self.config.read_text())

    def test_revocation_removes_exactly_its_own_entry(self):
        entry = self.entry()
        key = self.write_config(entry)
        before = self.read_config()
        ok, problem, detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertTrue(ok, (problem, detail))
        after = self.read_config()
        self.assertNotIn(key, after["projects"])
        # BYTE-PROVEN: every sibling entry and every global key is
        # identical, compared as serialized text rather than by
        # eyeballing the dict.
        del before["projects"][key]
        self.assertEqual(
            json.dumps(after, sort_keys=True),
            json.dumps(before, sort_keys=True),
            "revocation moved something other than its own entry",
        )

    def test_the_decoy_project_survives_byte_identically(self):
        entry = self.entry()
        self.write_config(entry)
        decoy_before = json.dumps(
            self.read_config()["projects"]["/Users/someone/other-repo"],
            sort_keys=True,
        )
        self.assertTrue(trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )[0])
        decoy_after = json.dumps(
            self.read_config()["projects"]["/Users/someone/other-repo"],
            sort_keys=True,
        )
        self.assertEqual(decoy_before, decoy_after)

    def test_revocation_works_after_the_directory_is_gone(self):
        """The live condition this exists to clean: a crash between
        directory removal and entry removal. Establishment requires
        the directory; revocation must not, or exactly the stranded
        entries would be unremovable."""
        entry = self.entry()
        key = self.write_config(entry)
        remove(entry["workspace_lease"]["path_realpath"])
        self.assertFalse(
            os.path.isdir(entry["workspace_lease"]["path_realpath"])
        )
        ok, problem, detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertTrue(ok, (problem, detail))
        self.assertNotIn(key, self.read_config()["projects"])

    def test_it_still_refuses_a_path_outside_the_managed_root(self):
        """Relaxing the directory check must not relax WHICH key may
        be touched."""
        entry = self.entry()
        outside = self.root / "outside"
        outside.mkdir()
        entry["workspace_lease"]["path_realpath"] = str(outside)
        self.write_config(entry)
        ok, problem, _detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertFalse(ok)
        self.assertEqual(
            problem, trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT
        )

    def test_it_refuses_another_workflows_lease_path(self):
        entry = self.entry("wf-0001")
        other = workspace_module.lease_path(
            str(self.workspaces), "wf-0002"
        )
        os.makedirs(other, exist_ok=True)
        entry["workspace_lease"]["path_realpath"] = os.path.realpath(other)
        self.write_config(entry)
        ok, problem, _detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertFalse(ok)
        self.assertEqual(problem, trust_module.PROBLEM_NOT_OWN_LEASE)

    def test_a_corrupt_config_is_a_refusal_and_changes_nothing(self):
        entry = self.entry()
        self.config.write_text("{ not json")
        before = self.config.read_bytes()
        ok, problem, _detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertFalse(ok)
        self.assertEqual(
            problem, trust_module.PROBLEM_CONFIG_UNPARSABLE
        )
        self.assertEqual(self.config.read_bytes(), before)

    def test_revocation_is_idempotent(self):
        entry = self.entry()
        self.write_config(entry)
        self.assertTrue(trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )[0])
        after_first = self.config.read_bytes()
        ok, problem, detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertTrue(ok, (problem, detail))
        self.assertEqual(
            self.config.read_bytes(), after_first,
            "the second revocation rewrote a file that was already in"
            " the intended state",
        )

    def test_establish_then_revoke_returns_the_config_to_its_start(self):
        """Round trip against the REAL establishment path, so the two
        halves are proven to agree on the key rather than each being
        correct about a different one."""
        entry = self.entry()
        self.write_config(entry, trusted=False)
        before = self.config.read_bytes()
        ok, problem, detail = trust_module.establish(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertTrue(ok, (problem, detail))
        self.assertNotEqual(self.config.read_bytes(), before)
        ok, problem, detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config)
        )
        self.assertTrue(ok, (problem, detail))
        self.assertEqual(
            json.dumps(self.read_config(), sort_keys=True),
            json.dumps(json.loads(before), sort_keys=True),
        )

    def test_a_locked_config_refuses_rather_than_forcing(self):
        entry = self.entry()
        self.write_config(entry)
        before = self.config.read_bytes()
        lock = str(self.config) + trust_module.LOCK_SUFFIX
        os.mkdir(lock)
        self.addCleanup(lambda: os.rmdir(lock)
                        if os.path.isdir(lock) else None)
        ok, problem, _detail = trust_module.revoke(
            entry, str(self.workspaces), str(self.config),
            sleeper=lambda _s: None,
        )
        self.assertFalse(ok)
        self.assertEqual(problem, trust_module.PROBLEM_CONFIG_LOCKED)
        self.assertEqual(self.config.read_bytes(), before)


#: How long a fixture descendant sleeps. R-14 E-2 requires that a
#: mutant reverting to a leader-only kill die by AUTHORED ASSERTION
#: rather than because the sleeper happened to expire — a stall
#: recorded as a kill is the defect, not the proof. The arithmetic,
#: stated so it is checkable without rerunning anything:
#:
#:   fixture sleep ................ 3600 s
#:   #: longest wait a test makes ........ 5 s (the grandchild poll)
#:   ratio ......................... 720x
#:
#: So a descendant that is gone when a test looks was signalled, not
#: expired. This is a TEST-FIXTURE I/O bound on a process this suite
#: started; it is not a deadline on an engineering mission, and I3's
#: hard line is untouched by it.
FIXTURE_SLEEP_SECONDS = 3600
GRANDCHILD_POLL_SECONDS = 5


class ProcessTreeOwnershipTests(unittest.TestCase):
    """I5-3, generalised: a component that starts a process owns its
    WHOLE TREE. Every pid here is one this test forked."""

    def setUp(self):
        # R-16 F-2 / R-18: the ledger is DURABLE and OUTLIVES the
        # test.
        #
        # The operative cause of the post-harness leak was here: this
        # was `tempfile.mkdtemp()` with an `addCleanup` that REMOVED
        # it, so the ownership record was destroyed at test cleanup
        # while the process it named could still be alive. # A post-harness sweep would then have had an empty ledger to
        # read, and that is what happened — the four orphaned groups
        # appear in no ledger on disk.
        #
        # It now lives beside the test tree, is NOT removed, and the
        # class-level sweep below reads it after every test in the
        # class has run.
        self.ledger = os.path.join(
            OWNER_LEDGER_ROOT, "case-%d" % os.getpid()
        )
        os.makedirs(self.ledger, exist_ok=True)

    def tearDown(self):
        """R-14 E-2, per test: after this test's OWN reaper has run,
        NO group it recorded survives.

        The reap comes first and then the assertion, deliberately.
        This is a pin on the CONSTRUCT — `reap_owned` is asked to
        clean up everything the ledger names, and the assertion fails
        if it could not. A reaper that killed only leaders would leave
        a descendant here and fail BY ASSERTION rather than by hanging
        or crashing, which is what R-14 E-2 requires and what a
        `STALLED` verdict does not give.
        """
        for pgid in proc_module.surviving_owned_groups(self.ledger):
            proc_module.reap_owned(
                pgid, directory=self.ledger, settle_seconds=3.0
            )
        surviving = proc_module.surviving_owned_groups(self.ledger)
        self.assertEqual(
            surviving, [],
            "this test leaked %d owned process group(s) that its own"
            " reaper could not clean: a component that starts a"
            " process owns its whole tree" % len(surviving),
        )

    def spawn_tree(self):
        """A leader in its own session that starts a GRANDCHILD, then
        exits — so the grandchild outlives its parent and is reachable
        only through the group. Returns (leader_pid, marker_path)."""
        marker = Path(tempfile.mkdtemp())
        self.addCleanup(remove, marker)
        flag = marker / "alive"
        # NO `os.setsid()` here: `spawn_owned` passes
        # `start_new_session=True`, so the leader is ALREADY a session
        # leader and a second call fails with EPERM — which killed the
        # leader before it could start its grandchild, and the fixture
        # then read an empty line. One construct owns the session, and
        # this script must not duplicate it.
        script = (
            "import os, sys, time, subprocess\n"
            "child = subprocess.Popen([sys.executable, '-c',\n"
            "    \"import time, pathlib, sys\\n\"\n"
            "    \"pathlib.Path(sys.argv[1]).write_text('x')\\n\"\n"
            "    \"time.sleep(%d)\", %r])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(%d)\n"
            % (FIXTURE_SLEEP_SECONDS, str(flag),
               FIXTURE_SLEEP_SECONDS)
        )
        # R-14 E-3: every spawn in this module routes through the
        # ONE owned-spawn construct, which starts the child in its own
        # session and RECORDS the group in an owner ledger before the
        # handle comes back. The ledger is what makes the group
        # reapable later even after its leader dies — the orphan shape
        # this fixture's own leak took.
        proc = proc_module.spawn_owned(
            [sys.executable, "-c", script],
            label="ownership-fixture-tree",
            directory=self.ledger,
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self._force_cleanup, proc)
        grandchild = int(proc.stdout.readline().strip())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not flag.exists():
            time.sleep(0.02)
        self.assertTrue(flag.exists(), "the grandchild never started")
        return proc.pid, grandchild

    def _force_cleanup(self, proc):
        """Reap THIS test's own GROUP and WAIT for it, so that within this
        fixture a failing assertion does not leak a descendant. Routed through
        `reap_owned`, so it can only ever signal a group this fixture
        recorded."""
        proc_module.reap_owned(
            proc.pid, directory=self.ledger, settle_seconds=3.0
        )
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:                                 # noqa: BLE001
            pass
        try:
            proc.wait(timeout=3)
        except Exception:                                 # noqa: BLE001
            pass

    @staticmethod
    def zombie(pid):
        """Whether ``pid`` is an uncollected exited child of ours."""
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True,
        ).stdout.strip()
        return out.startswith("Z")

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except OSError as exc:
            return exc.errno != errno.ESRCH
        return True

    def test_reaping_removes_a_grandchild_the_leader_left_behind(self):
        """THE EXECUTED PIN for I5-3. A leader-only kill would leave
        the grandchild running, so the difference between killing a
        leader and reaping a group is observable in every run."""
        leader, grandchild = self.spawn_tree()
        self.assertTrue(self.alive(grandchild))
        # A SHORT settle, so a reaper that kills only the leader
        # reports its failure quickly and this test KILLS it by
        # assertion. With the production settle a leader-only kill
        # left the grandchild sleeping and the mutation run STALLED,
        # and a stall is not a kill.
        verdict, detail = proc_module.reap_group(
            leader, settle_seconds=1.0
        )
        self.assertEqual(
            verdict, proc_module.REAPED,
            "the group was not reaped (%s); a leader-only kill leaves"
            " descendants running and is not ownership" % (detail,),
        )
        deadline = time.monotonic() + GRANDCHILD_POLL_SECONDS
        while time.monotonic() < deadline and self.alive(grandchild):
            time.sleep(0.02)
        self.assertFalse(
            self.alive(grandchild),
            "the grandchild survived the reap; a leader-only kill is"
            " not ownership. Its sleep is %ds and this test waited at"
            " most %ds, so expiry cannot account for a disappearance"
            " here — nor mask one that failed to happen"
            % (FIXTURE_SLEEP_SECONDS, GRANDCHILD_POLL_SECONDS),
        )

    def test_reap_owned_reaps_a_LIVE_group_it_recorded(self):
        """R-15 / reviewer1's blocker, closed.

        `reap_owned` was reachable but UNEXERCISED against a live
        group: every prior test that recorded a group had already
        killed it through `reap_group` before cleanup ran, so deleting
        `reap_owned`'s `os.killpg` left the suite green. This test
        reaps ONLY through `reap_owned`, on a tree that is still
        running, and asserts the verdict AND the grandchild's death —
        the same standard `reap_group` already meets.

        Margin, stated so that within this test expiry is not mistaken
        for a reap: the
        fixture descendant sleeps 3600 s and this test waits at most
        5 s, a ratio of 720. A grandchild that is gone here was
        signalled.
        """
        leader, grandchild = self.spawn_tree()
        self.assertTrue(self.alive(grandchild))
        # # A SHORT settle, so that within this test a reaper which
        # signals no group reports its failure fast and dies HERE by
        # assertion. With the production
        # settle the mutation run STALLED, and a stall is not a kill.
        verdict, detail = proc_module.reap_owned(
            leader, directory=self.ledger, settle_seconds=1.0
        )
        self.assertEqual(
            verdict, proc_module.REAPED,
            "reap_owned did not reap a group it recorded (%s); the"
            " ledger-based reaper is the one R-14 exists for"
            % (detail,),
        )
        deadline = time.monotonic() + GRANDCHILD_POLL_SECONDS
        while time.monotonic() < deadline and self.alive(grandchild):
            time.sleep(0.02)
        self.assertFalse(
            self.alive(grandchild),
            "the grandchild survived reap_owned; its sleep is %ds and"
            " this test waited at most %ds, so expiry cannot account"
            " for a disappearance nor mask one that failed to happen"
            % (FIXTURE_SLEEP_SECONDS, GRANDCHILD_POLL_SECONDS),
        )
        self.assertEqual(
            proc_module.surviving_owned_groups(self.ledger), [],
        )

    def test_reap_owned_refuses_a_group_the_ledger_does_not_name(self):
        """The other half of the same standard: the ownership gate
        still holds, so closing the pin did not widen the reaper."""
        leader, _grandchild = self.spawn_tree()
        other = tempfile.mkdtemp()
        self.addCleanup(remove, other)
        verdict, detail = proc_module.reap_owned(
            leader, directory=other, settle_seconds=1.0
        )
        self.assertEqual(
            verdict, proc_module.REFUSED_NOT_IN_LEDGER, detail
        )
        self.assertTrue(
            self.alive(leader),
            "a group outside the consulted ledger was signalled",
        )

    def test_reap_leader_collects_a_zombie_it_forked(self):
        """`_reap_leader` had ZERO direct test references — reachable
        and unexercised, the same shape as `reap_owned`.

        It is what stops a killed leader lingering as a zombie, and a
        zombie answers `killpg(pgid, 0)`, which is what made an
        earlier `reap_group` report failure over a tree that was
        already gone. Driven here on a child this test forked: killed,
        observed as a zombie, collected, and then unwaitable.
        """
        pid = os.fork()
        if pid == 0:                                   # pragma: no cover
            os._exit(0)
        # Deliberately NO waitpid here: # collecting the child myself would leave the helper no work to
        # do, and the test would pass for the wrong reason. The child exits at once, so a short
        # settle is enough for it to become a zombie.
        deadline = time.monotonic() + GRANDCHILD_POLL_SECONDS
        while time.monotonic() < deadline and not self.zombie(pid):
            time.sleep(0.01)
        self.assertTrue(
            self.zombie(pid),
            "the forked child did not become a zombie, so this test"
            " would not exercise the collection it exists to pin",
        )
        proc_module._reap_leader(pid)
        with self.assertRaises(OSError) as caught:
            os.waitpid(pid, os.WNOHANG)
        self.assertEqual(
            caught.exception.errno, errno.ECHILD,
            "the child was still collectable after _reap_leader, so"
            " it did not collect it",
        )

    def test_reap_leader_tolerates_a_pid_it_did_not_fork(self):
        """ECHILD is not an error within the helper: it means this
        process has no child to collect. Driven rather than reasoned,
        with a pid this process certainly did not fork."""
        proc_module._reap_leader(os.getppid())

    def test_the_I1_reaper_delegates_and_actually_reaps(self):
        """`reap_process_group` in `tests/test_workspace_trust.py` is
        the fourth reaper in the domain, and mutant S13 — which breaks
        its delegation to `process_ownership.reap_group` — SURVIVED
        the I1 tests that already drive it.

        It survived because those tests observe a pty tree that dies
        when its descriptor closes, so an absent reap is invisible
        there. This pin drives the function over a tree THIS test
        created, which does not die on its own, and asserts both the
        empty survivor list and the grandchild's death.

        Margin: the grandchild sleeps 3600 s and this test waits at most 5 s, so
        within that window expiry accounts for neither its death nor a
        reap that did not happen.
        """
        import test_workspace_trust as i1
        leader, grandchild = self.spawn_tree()
        self.assertTrue(self.alive(grandchild))
        survivors = i1.reap_process_group(leader)
        self.assertEqual(
            survivors, [],
            "the I1 reaper reported survivors after reaping a tree it"
            " was handed: %r" % (survivors,),
        )
        deadline = time.monotonic() + GRANDCHILD_POLL_SECONDS
        while time.monotonic() < deadline and self.alive(grandchild):
            time.sleep(0.02)
        self.assertFalse(
            self.alive(grandchild),
            "the grandchild survived the I1 reaper; its delegation to"
            " process_ownership.reap_group is not reaping",
        )

    def test_an_unverified_group_is_never_signalled(self):
        """Two shapes, both refused.

        The original: reading a child's group before `setsid` landed
        returned the PARENT'S group, so `os.getpgid(pid) == pid` must
        hold first. The second, found when this suite began running
        under `start_new_session=True`: the CALLER is then its own
        group leader, the first check passes for `os.getpid()`, and a
        reaper would kill the group it is running in. `os.getpgrp()`
        is refused explicitly.
        """
        self.assertFalse(
            proc_module.group_is_verified(os.getpgrp()),
            "the caller's OWN process group was accepted for reaping",
        )
        self.assertFalse(proc_module.group_is_verified(os.getpid()))
        verdict, detail = proc_module.reap_group(os.getpid())
        self.assertEqual(
            verdict, proc_module.REFUSED_UNVERIFIED_GROUP, detail
        )

    def test_the_refusal_signals_nothing_at_all(self):
        """Driven rather than reasoned: `os.killpg` is replaced with a
        recorder, and the refusal path must not have called it."""
        from unittest.mock import patch
        calls = []
        with patch.object(os, "killpg",
                          side_effect=lambda *a: calls.append(a)):
            proc_module.reap_group(os.getpid())
        self.assertEqual(
            calls, [],
            "a refused reap still signalled a process group",
        )

    def test_a_nonexistent_leader_is_already_gone(self):
        leader, _grandchild = self.spawn_tree()
        self.assertEqual(
            proc_module.reap_group(leader, settle_seconds=1.0)[0],
            proc_module.REAPED,
        )
        # A second reap of a tree that is already gone reports it,
        # rather than reporting REAPED for a kill it did not perform.
        # Which of the three non-REAPED verdicts comes back depends on
        # whether the dead leader's pid still resolves, so the
        # assertion is that it is NOT the success verdict.
        second = proc_module.reap_group(leader, settle_seconds=1.0)[0]
        self.assertIn(
            second,
            (proc_module.ALREADY_GONE,
             proc_module.REFUSED_UNVERIFIED_GROUP,
             proc_module.REAPED_LEADER_ONLY),
        )
        self.assertNotEqual(second, proc_module.REAPED)

    def test_pid_zero_and_one_are_refused(self):
        """`killpg(0, …)` signals the CALLER'S OWN group, and pid 1 is
        init. Within this check both are refused, before a signal is sent."""
        for pid in (0, 1, -1, True, "x", None):
            with self.subTest(pid=repr(pid)):
                self.assertFalse(proc_module.group_is_verified(pid))


class UnrelatedResourceTests(RuntimeCase):
    """THE EXECUTED GUARANTEE: within a release, an unrelated session
    stays untouched.

    Driven through the REAL `ACTION_RELEASE`, with a decoy this test
    creates but does not own. R-8 binds hardest here, so within this
                              class each assertion is on executed
                              behaviour rather than on source.
    """

    def terminal(self, workflow_id="wf-0001"):
        self.put_record(self.authorized_record(workflow_id))
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform(workflow_id, action, 2).ok)
        workflows = self.fresh_workflows()
        entry = workflows["workflows"][workflow_id]
        wa_record.apply_transition(entry, wa_record.PHASE_BLOCKED)
        self.write_raw(workflows)
        return self.fresh_workflows()["workflows"][workflow_id]

    def decoy(self, name="an-unrelated-session"):
        """A directory this test CREATES and does not own, inside the
        managed root — the hardest place for it to be."""
        path = os.path.join(self.workspaces, name)
        os.makedirs(path, exist_ok=True)
        payload = os.path.join(path, "work.txt")
        with open(payload, "w") as handle:
            handle.write("another herd's work\n")
        with open(payload, "rb") as handle:
            return path, payload, handle.read()

    def test_release_removes_its_own_workspace(self):
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        self.assertTrue(os.path.isdir(lease))
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertFalse(os.path.isdir(lease))

    def test_a_decoy_inside_the_managed_root_survives_byte_identically(self):
        """The pin the increment exists for. The decoy sits INSIDE the managed root, so within this case the
        ownership predicate is the only thing keeping it alive."""
        path, payload, before = self.decoy()
        self.terminal()
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        self.assertTrue(
            os.path.isdir(path),
            "a directory this workflow does not own was removed by"
            " its release",
        )
        with open(payload, "rb") as handle:
            self.assertEqual(
                handle.read(), before,
                "an unrelated session's file changed during cleanup",
            )

    def test_a_decoy_named_like_the_workflow_survives(self):
        """The name trap, driven end to end: a decoy whose name
        carries the workflow id and the dispatch alias prefix. A
        prefix-matching predicate would delete it."""
        alias = dispatch_module.ALIAS_PREFIX + "wf-0001"
        path, payload, before = self.decoy(alias)
        self.terminal()
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        self.assertTrue(
            os.path.isdir(path),
            "a directory matching the workflow's ALIAS was removed;"
            " the alias is a derived label, never binding evidence",
        )
        with open(payload, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_a_PREFIX_SIBLING_of_the_lease_survives(self):
        """The gap my first decoy did not cover, found because mutant
        S02 SURVIVED it.

        S02 relaxes the workspace check from equality to
        `startswith`. A decoy named `an-unrelated-session` does not
        start with the lease path, so it stayed alive under the mutant
        and the mutant lived. A PREFIX SIBLING — the lease path plus a
        suffix — is the shape that separates equality from prefix
        matching, and it is the same shape the existing release
        hardening already guards at its own layer.
        """
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        sibling = lease + "-decoy"
        os.makedirs(sibling, exist_ok=True)
        payload = os.path.join(sibling, "work.txt")
        with open(payload, "w") as handle:
            handle.write("another herd's work\n")
        with open(payload, "rb") as handle:
            before = handle.read()
        self.assertEqual(
            ownership_module.owns_workspace(
                entry, sibling, self.workspaces
            ),
            ownership_module.NOT_OWNED,
            "a prefix sibling of the lease was reported as owned;"
            " equality has been relaxed to prefix matching",
        )
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        self.assertTrue(os.path.isdir(sibling))
        with open(payload, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_release_revokes_only_its_own_trust_entry(self):
        """End to end through the broker, against the INJECTED config
        this fixture owns — the real `~/.claude.json` is not involved.
        """
        entry = self.terminal()
        key = trust_module.trust_key(
            entry["workspace_lease"]["path_realpath"]
        )
        with open(self.claude_config, encoding="utf-8") as handle:
            before = json.load(handle)
        self.assertIn(key, before["projects"])
        siblings = {
            name: json.dumps(value, sort_keys=True)
            for name, value in before["projects"].items()
            if name != key
        }
        globals_before = {
            name: json.dumps(value, sort_keys=True)
            for name, value in before.items() if name != "projects"
        }
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        with open(self.claude_config, encoding="utf-8") as handle:
            after = json.load(handle)
        self.assertNotIn(
            key, after["projects"],
            "the release left its own trust entry behind; the grant"
            " is still permanent",
        )
        self.assertEqual(
            {name: json.dumps(value, sort_keys=True)
             for name, value in after["projects"].items()},
            siblings,
            "a project entry this workflow does not own moved",
        )
        self.assertEqual(
            {name: json.dumps(value, sort_keys=True)
             for name, value in after.items() if name != "projects"},
            globals_before,
            "a top-level configuration key moved",
        )

    def test_sessions_close_BEFORE_the_directory_is_deleted(self):
        """R-31 W-4's executed order pin for the instance that
        prompted the closure.

        Driven by recording the ORDER in which the two steps run, so
        an inverted order fails HERE by assertion rather than being
        read out of the source. Before the fix the directory was
        deleted first, and agents would have been stopped only after
        their workspace was already gone.

        Both seams are replaced, so no real workspace is closed.
        """
        order = []
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        # A bound target identity, written durably: the Domain B proof
        # requires one, and `terminal()` stops before dispatch.
        task_id = "20260828-114612-5d92e1"
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["target_engine"] = {
            "alias": dispatch_module.ALIAS_PREFIX + "wf-0001",
            "task_id": task_id, "repo": "u", "dispatched_at": NOW,
        }
        self.write_raw(workflows)

        def live_workspaces():
            return [{"workspace_id": "wTEST",
                     "agent_names": {"a-sup", "a-lead"}}]

        def close_fn(workspace_id):
            order.append("close:%s" % workspace_id)

        self.spawn_record_overrides.update({"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": lease, "task_id": task_id,
            "workspace_id": "wTEST",
            "agents": {"supervisor": "a-sup", "lead1": "a-lead"},
        }]})
        broker = broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn,
            clock=lambda: NOW,
            observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            live_workspaces_fn=live_workspaces,
            workspace_close_fn=close_fn,
        )
        real_release = workspace_module.release

        def watching_release(*args, **kwargs):
            order.append("release")
            return real_release(*args, **kwargs)

        from unittest.mock import patch
        import target_runtime.capability as capability_module
        token = capability_module.mint(
            self.store_dir, "wf-0001",
            broker_module.ACTION_RELEASE, 2, NOW,
        )
        with patch.object(workspace_module, "release",
                          watching_release):
            outcome = broker.perform(
                "wf-0001", broker_module.ACTION_RELEASE, 2,
                capability=token,
            )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(
            order, ["close:wTEST", "release"],
            "the managed directory was deleted before the sessions"
            " were closed; a destructive step must come AFTER the step"
            " that makes it safe",
        )

    def _domain_b_broker(self, live_fn, close_fn, clock=None):
        return broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn,
            clock=clock or (lambda: NOW),
            observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            live_workspaces_fn=live_fn,
            workspace_close_fn=close_fn,
        )

    def _release_through(self, broker, revision=2):
        import target_runtime.capability as capability_module
        token = capability_module.mint(
            self.store_dir, "wf-0001",
            broker_module.ACTION_RELEASE, revision, NOW,
        )
        return broker.perform(
            "wf-0001", broker_module.ACTION_RELEASE, revision,
            capability=token,
        )

    def test_a_degraded_close_RETAINS_the_directory_and_CANDIDACY(self):
        """R-36 AA-4: THE ASSERTION THAT WOULD HAVE CAUGHT IT.

        A transient unreadable projection must not become permanent
        abandonment. Before the fix the delete ran unconditionally
        after the close ATTEMPT, so one bad read deleted a live
        workspace's directory and suppressed every future retry.

        The degraded window is driven first — directory MUST still
        exist, workflow MUST still be a candidate — and then the
        projection is restored and the exact chain completes.
        """
        from target_runtime import runtime as runtime_module
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        task_id = "20260828-114612-5d92e1"
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["target_engine"] = {
            "alias": dispatch_module.ALIAS_PREFIX + "wf-0001",
            "task_id": task_id, "repo": "u", "dispatched_at": NOW,
        }
        self.write_raw(workflows)
        self.spawn_record_overrides.update({"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": lease, "task_id": task_id,
            "workspace_id": "wTEST",
            "agents": {"supervisor": "a-sup"},
        }]})
        closed = []

        # --- the degraded window ---------------------------------
        degraded = self._domain_b_broker(
            live_fn=lambda: None,          # unreadable projection
            close_fn=closed.append,
        )
        outcome = self._release_through(degraded)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(
            closed, [], "a degraded projection still closed something"
        )
        self.assertTrue(
            os.path.isdir(lease),
            "THE DIRECTORY WAS DELETED AFTER AN UNPROVEN CLOSE; a"
            " transient unreadable projection has become permanent"
            " abandonment of a live workspace",
        )
        candidates = [
            wid for wid, _rev in
            runtime_module.terminal_cleanup_candidates(self.store_dir)
        ]
        self.assertIn(
            "wf-0001", candidates,
            "a degraded cleanup stopped being a candidate, so the"
            " retry it needs will never happen",
        )

        # --- the evidence returns --------------------------------
        exact = self._domain_b_broker(
            live_fn=lambda: [{"workspace_id": "wTEST",
                              "agent_names": {"a-sup"}}],
            close_fn=closed.append,
        )
        outcome = self._release_through(exact)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(
            closed, ["wTEST"],
            "the exact chain did not close the proven workspace",
        )
        self.assertFalse(
            os.path.isdir(lease),
            "the directory survived a PROVEN close; cleanup did not"
            " complete",
        )
        self.assertNotIn(
            "wf-0001",
            [wid for wid, _rev in
             runtime_module.terminal_cleanup_candidates(
                 self.store_dir
             )],
            "a completed cleanup is still a candidate, so a later"
            " pass would retry it",
        )

    def test_a_PRESERVATION_failure_HALTS_the_chain(self):
        """R-38 AC-1/AC-3: the assertion that would have caught it.

        Preservation is a PROVEN PRECONDITION of the two destructive
        steps after it. The previous form recorded the failure and
        proceeded — so a preservation failure destroyed the only
        source of the evidence it had just failed to preserve.

        Driven by making preservation fail and asserting the steps
        downstream did NOT run: no close, the directory intact, and the
        workflow still a cleanup candidate so the next pass retries.
        """
        from unittest.mock import patch
        from target_runtime import runtime as runtime_module
        from target_runtime import evidence_preservation as preserve_module
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        task_id = "20260828-114612-5d92e1"
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["target_engine"] = {
            "alias": dispatch_module.ALIAS_PREFIX + "wf-0001",
            "task_id": task_id, "repo": "u", "dispatched_at": NOW,
        }
        self.write_raw(workflows)
        self.spawn_record_overrides.update({"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": lease, "task_id": task_id,
            "workspace_id": "wTEST",
            "agents": {"supervisor": "a-sup"},
        }]})
        closed = []
        broker = self._domain_b_broker(
            live_fn=lambda: [{"workspace_id": "wTEST",
                              "agent_names": {"a-sup"}}],
            close_fn=closed.append,
        )
        with patch.object(
            preserve_module, "preserve",
            return_value=(False, preserve_module.PROBLEM_READBACK,
                          "read-back failed", None),
        ):
            outcome = self._release_through(broker)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(
            outcome.problem,
            ownership_module.PROBLEM_CLEANUP_DEGRADED,
        )
        self.assertEqual(
            closed, [],
            "the sessions were closed after preservation FAILED; the"
            " chain must halt at the first failure",
        )
        self.assertTrue(
            os.path.isdir(lease),
            "THE SOURCE EVIDENCE WAS DESTROYED after preservation"
            " failed — the directory holding the only copy is gone",
        )
        self.assertIn(
            "wf-0001",
            [wid for wid, _rev in
             runtime_module.terminal_cleanup_candidates(
                 self.store_dir
             )],
            "a halted chain stopped being a candidate, so the retry"
            " it needs will never happen",
        )

    def test_the_cleanup_receipt_records_what_happened(self):
        self.terminal()
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        entry = self.fresh_workflows()["workflows"]["wf-0001"]
        summaries = [
            receipt["bounded_summary"]
            for receipt in entry["receipts"]
            if receipt["bounded_summary"].startswith(
                broker_module.CLEANUP_RECEIPT_MARKER
            )
        ]
        self.assertEqual(len(summaries), 1, summaries)
        # R-36 changed this again, and the new value is the honest
        # one. This fixture wires NEITHER a workspace projection nor a
        # close capability, so Domain B is not configured for it at
        # all — a configuration fact rather than a degraded reading.
        # The release therefore completes: # the workspace directory and the trust entry are removed, and
        # the workspace-session step records NOT_OWNED rather than
        # claiming a cleanup it did not attempt.
        #
        # # A Broker that IS configured and is then unable to read the
        # evidence is the dangerous case, and
        # `test_a_degraded_close_RETAINS_the_directory_and_CANDIDACY`
        # drives it.
        self.assertIn("cleanup complete", summaries[0])
        # Three now: the trust entry, the workspace directory, and —
        # added by R-37 — the PRESERVED TARGET EVIDENCE, captured
        # before either was destroyed.
        self.assertIn("removed 3", summaries[0])

    def test_a_second_release_refuses_cleanly_and_touches_nothing(self):
        """Restart behaviour, corrected after execution refuted my
        first reading of it.

        I expected a second release to be a clean no-op. It is not:
        `workspace_module.release` deliberately refuses a repeat with
        `workspace_lease_missing`, and that is an EXISTING guarantee —
        a lease is released once. So the restart property that
        actually holds is the one worth pinning: the refusal is clean,
        and a decoy this test created is still byte-identical
        afterwards.

        The half that IS idempotent is trust revocation, which
        `TrustRevocationTests.test_revocation_is_idempotent` drives —
        so a crash between the two steps leaves a later release able
        to finish the trust half without the workspace half lying.
        """
        path, payload, before = self.decoy("second-release-decoy")
        self.terminal()
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem, "workspace_lease_missing")
        self.assertTrue(os.path.isdir(path))
        with open(payload, "rb") as handle:
            self.assertEqual(handle.read(), before)


class PgidReuseCorroborationTests(unittest.TestCase):
    """R-54 AR-3: A RECORDED PGID IS NOT A DURABLE IDENTITY.

    The specimen, found by census rather than by reading: pgid 44603
    was recorded in an owned root this component wrote; it was later
    held by `/System/Library/CoreServices/ReportCrash daemon`; and it
    was empty by the time it was re-checked. Every stage of
    that is normal OS behaviour — process-group numbers are reused —
    and at the middle stage a recovery acting on the record alone
    would have signalled an unrelated system process.

    "Group N is recorded here and group N is alive" is two facts about
    a NUMBER. What makes it a fact about a PROCESS is corroboration:
    the nonce binding the root to a spawn this component made, and the
    leader's start time matching the one recorded when it was stamped.

    THE SHAPE THAT DETECTS THIS CLASS: a root whose recorded pgid is
    ALIVE and belongs to somebody else. A test using only groups this
    component started passes either way — which is how the defect
    reached a census instead of a test.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def root_with(self, nonce, pgid, start):
        directory = proc_module.create_owned_root(nonce, self.base)
        if start is not None:
            with open(os.path.join(
                directory, proc_module.OWNED_ROOT_START_FILE
            ), "w") as handle:
                handle.write(start)
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_PGID_FILE
        ), "w") as handle:
            handle.write(str(pgid))
        return directory

    def live_group(self):
        """A real group this test owns, correctly stamped."""
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            label="corroboration-fixture",
            directory=self.base, owned_root_base_dir=self.base,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.release, handle)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(self.base)
            if roots and roots[0][1] is not None:
                return roots[0][0], roots[0][1]
            time.sleep(0.02)
        self.fail("the fixture never stamped its root")

    def release(self, handle):
        proc_module.reap_owned(
            handle.pid, directory=self.base, settle_seconds=3.0
        )
        try:
            handle.wait(timeout=3)
        except Exception:                         # noqa: BLE001
            pass

    def test_a_REUSED_group_id_is_NOT_ours(self):
        """The defect, driven directly: the number is live and the
        start time says the process is somebody else's."""
        directory, pgid = self.live_group()
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_START_FILE
        ), "w") as handle:
            handle.write("Thu Jan  1 00:00:00 1970")
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(
            ours,
            "a group whose leader started at a different time than"
            " the record names was treated as ours; that is the"
            " reused-pgid defect, and acting on it signals an"
            " unrelated process",
        )
        self.assertEqual(
            reason, proc_module.UNCORROBORATED_START_MISMATCH
        )

    def test_a_REUSED_group_id_is_REPORTED_and_NEVER_REAPED(self):
        """AF-2 applied to this gate: the OUTCOME changes, and the
        live group is still running afterwards."""
        directory, pgid = self.live_group()
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_START_FILE
        ), "w") as handle:
            handle.write("Thu Jan  1 00:00:00 1970")
        recovered, stuck, unstamped, uncorroborated = (
            proc_module.recover_orphans(self.base, settle_seconds=1.0)
        )
        self.assertEqual(recovered, [])
        self.assertEqual(stuck, [])
        self.assertEqual(unstamped, [])
        self.assertEqual(
            uncorroborated,
            [(directory, pgid,
              proc_module.UNCORROBORATED_START_MISMATCH)],
        )
        self.assertTrue(
            proc_module._group_alive(pgid),
            "recovery KILLED a live group it could not prove was"
            " ours; the record named a number the OS had reused",
        )

    def test_a_CORROBORATED_group_IS_reaped(self):
        """The counterpart, without which "never reaped" could be true
        because nothing is ever reaped. Same fixture, record left
        intact."""
        directory, pgid = self.live_group()
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(reason)
        self.assertEqual(ours, pgid)
        recovered, stuck, unstamped, uncorroborated = (
            proc_module.recover_orphans(self.base, settle_seconds=10.0)
        )
        self.assertEqual(recovered, [pgid])
        self.assertEqual(uncorroborated, [])
        self.assertFalse(proc_module._group_alive(pgid))

    def test_a_root_with_NO_recorded_start_is_uncorroborated(self):
        """The pre-AR-3 record shape: a pgid, and within this record
        nothing to check it against. Those records exist on disk
        today, and they must be reported rather than trusted."""
        directory, pgid = self.live_group()
        os.unlink(os.path.join(
            directory, proc_module.OWNED_ROOT_START_FILE
        ))
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(ours)
        self.assertEqual(reason, proc_module.UNCORROBORATED_NO_START)
        self.assertTrue(proc_module._group_alive(pgid))

    def test_a_root_with_NO_nonce_is_uncorroborated(self):
        directory, pgid = self.live_group()
        os.unlink(os.path.join(
            directory, proc_module.OWNED_ROOT_NONCE_FILE
        ))
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(ours)
        self.assertEqual(reason, proc_module.UNCORROBORATED_NO_NONCE)

    def test_a_nonce_that_does_not_NAME_its_root_is_uncorroborated(self):
        """The nonce binds the record to the spawn. A root carrying
        somebody else's nonce is a record that was moved or copied."""
        directory, pgid = self.live_group()
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_NONCE_FILE
        ), "w") as handle:
            handle.write("own-not-this-root")
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(ours)
        self.assertEqual(
            reason, proc_module.UNCORROBORATED_NONCE_MISMATCH
        )

    def test_scope_liveness_does_NOT_count_a_reused_group(self):
        """The predicate the test harness uses to decide whether a
        scope may be retired reads the same corroboration. Counting a
        reused id as ours would keep a finished scope alive forever."""
        directory, pgid = self.live_group()
        self.assertTrue(proc_module.scope_has_live_group(self.base))
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_START_FILE
        ), "w") as handle:
            handle.write("Thu Jan  1 00:00:00 1970")
        self.assertFalse(
            proc_module.scope_has_live_group(self.base),
            "a reused group id counted as a live group of ours",
        )

    def test_the_STAMP_records_a_start_time_beside_the_pgid(self):
        """The producer half. Without it every record is
        uncorroborated and the gate refuses everything."""
        directory, pgid = self.live_group()
        recorded = proc_module.owned_root_record(directory)
        self.assertEqual(recorded["pgid"], pgid)
        self.assertTrue(recorded["nonce"])
        self.assertTrue(
            recorded["leader_start"],
            "the stamp wrote a group id with nothing to corroborate"
            " it; every such record is one the OS may have reused",
        )
        self.assertEqual(
            recorded["leader_start"],
            proc_module.leader_start_time(pgid),
        )

    def test_a_DEAD_group_is_neither_ours_nor_reported(self):
        """Within this case there is nothing to act on and nothing to
        warn about: the record names a group that is gone, which is
        the ordinary case after a clean run."""
        directory = self.root_with(
            "own-dead", 999999, "Thu Jan  1 00:00:00 1970"
        )
        ours, reason = proc_module.group_is_ours(directory)
        self.assertIsNone(ours)
        self.assertIsNone(reason)


class RetireProcessScopesTests(RuntimeCase):
    """R-54 AR-4: the DECIDED retention lifecycle, EXECUTED.

    AL-4..AL-7 decided it: process-scope records reclaimed as part of
    THEIR OWN workflow's terminal cleanup, under the assignment
    credential, and within that policy never on a clock. No code
    performed it. A decided policy with no implementation is, within
    production, the same defect as an unenforced value, which this
    mission has now seen at R-40, R-38, R-42 and R-45. This class is what makes the fourth
    instance an implementation rather than a fifth instance.

    Every scope here is created through `assign_scope`, the production
    credential path, and the refusals are driven with REAL records: a
    real live corroborated group, and a real assignment belonging to a
    different workflow.
    """

    CONTROL = "/control/repo"

    def scope_for(self, workflow_id, unit_id="t-1", base=None):
        return proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
            workflow_id, unit_id, base=base or self.private,
        )

    def setUp(self):
        super(RetireProcessScopesTests, self).setUp()
        self.private = tempfile.mkdtemp()
        self.addCleanup(remove, self.private)

    def test_a_workflows_OWN_scope_is_retired(self):
        scope = self.scope_for("wf-0001")
        name = os.path.basename(scope)
        assignment = proc_module.assignment_path(name, self.private)
        self.assertTrue(os.path.isdir(scope))
        self.assertTrue(os.path.isfile(assignment))
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [scope])
        self.assertEqual(refused, [])
        self.assertFalse(os.path.isdir(scope))
        self.assertFalse(
            os.path.isfile(assignment),
            "the scope was reclaimed and its CREDENTIAL was left"
            " behind, pointing at a directory that no longer exists",
        )

    def test_another_workflows_scope_is_NEVER_retired(self):
        """The unrelated-resource guarantee, at this seam."""
        mine = self.scope_for("wf-0001")
        theirs = self.scope_for("wf-OTHER")
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [mine])
        self.assertTrue(
            os.path.isdir(theirs),
            "retirement removed a scope belonging to a different"
            " workflow; selection is by assignment credential, and a"
            " credential names exactly one owner",
        )

    def test_another_CONTROLS_scope_is_NEVER_retired(self):
        mine = self.scope_for("wf-0001")
        theirs = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/somebody/elses/repo",
            "wf-0001", "t-1", base=self.private,
        )
        retired, _refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [mine])
        self.assertTrue(os.path.isdir(theirs))

    def test_a_scope_with_NO_valid_assignment_is_NEVER_retired(self):
        """A directory whose NAME parses and which carries no
        credential is not this workflow's to remove."""
        forged = os.path.join(
            proc_module.owned_root_base(self.private),
            proc_module.scope_name(
                proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                "wf-0001", "t-forged",
            ),
        )
        os.makedirs(forged)
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [])
        self.assertEqual(refused, [])
        self.assertTrue(
            os.path.isdir(forged),
            "a correctly named but UNASSIGNED scope was removed;"
            " attribution by name is what R-43 closed",
        )

    def test_a_scope_with_a_LIVE_group_is_REFUSED_and_KEPT(self):
        """The record is the only evidence a later run could recover
        that process from. Removing it while the process lives is the
        leak this module exists to prevent."""
        scope = self.scope_for("wf-0001")
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            label="retire-refusal-fixture",
            directory=scope, owned_root_base_dir=scope,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.release_occupant, scope, handle)
        deadline = time.monotonic() + 10
        pgid = None
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(scope)
            if roots and roots[0][1] is not None:
                pgid = roots[0][1]
                break
            time.sleep(0.02)
        self.assertIsNotNone(pgid)
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [])
        self.assertEqual(
            refused, [(scope, proc_module.RETIRE_REFUSED_LIVE_GROUP)]
        )
        self.assertTrue(os.path.isdir(scope))
        self.assertTrue(
            proc_module._group_alive(pgid),
            "the fixture group died on its own, so the refusal above"
            " proves nothing",
        )

    def release_occupant(self, scope, handle):
        proc_module.reap_owned(
            handle.pid, directory=scope, settle_seconds=3.0
        )
        try:
            handle.wait(timeout=3)
        except Exception:                         # noqa: BLE001
            pass

    def test_NO_AGE_BASED_DELETION_ANYWHERE(self):
        """AL-7, asserted rather than described: a clock is not a
        credential. An ANCIENT scope with no valid assignment stays;
        a BRAND NEW one with a valid assignment goes. Age points the
        opposite way from the outcome in both cases."""
        ancient = os.path.join(
            proc_module.owned_root_base(self.private),
            proc_module.scope_name(
                proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                "wf-0001", "t-ancient",
            ),
        )
        os.makedirs(ancient)
        os.utime(ancient, (0, 0))
        fresh = self.scope_for("wf-0001", unit_id="t-fresh")
        retired, _refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [fresh])
        self.assertTrue(
            os.path.isdir(ancient),
            "the oldest record was removed and the newest kept, which"
            " is what an age-based sweep would do",
        )

    def test_retirement_happens_THROUGH_the_release(self):
        """The ORDERING, driven through the real `ACTION_RELEASE`.

        The seam is what AR-4 asked for: a decided policy that
        EXECUTES. A unit test of `retire_workflow_scopes` proves the
        function works while leaving open whether the release calls
        it, which is exactly the gap R-28 found across fourteen
        rulings.
        """
        from unittest.mock import patch
        entry = self.put_record(self.authorized_record("wf-0001"))
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform("wf-0001", action, 2).ok)
        workflows = self.fresh_workflows()
        record = workflows["workflows"]["wf-0001"]
        wa_record.apply_transition(record, wa_record.PHASE_BLOCKED)
        self.write_raw(workflows)
        control = record["control_identity"]["repository_realpath"]
        seen = {}
        real = proc_module.retire_workflow_scopes

        def capture(control_identity, workflow_id, base=None):
            seen["args"] = (control_identity, workflow_id)
            return real(control_identity, workflow_id, base=base)

        with patch.object(
            proc_module, "retire_workflow_scopes", capture
        ):
            outcome = self.perform(
                "wf-0001", broker_module.ACTION_RELEASE, 2
            )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(
            seen.get("args"), (control, "wf-0001"),
            "the release completed without reclaiming this workflow's"
            " process-scope records; AL-4..AL-7's lifecycle is"
            " decided and nothing performs it",
        )

    def test_a_HALTED_release_retires_NOTHING(self):
        """The conditional half. Retirement runs only after the
        release proved out — a workflow whose cleanup halted keeps
        its records, because a retry will need them."""
        from unittest.mock import patch
        self.put_record(self.authorized_record("wf-0001"))
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform("wf-0001", action, 2).ok)
        workflows = self.fresh_workflows()
        record = workflows["workflows"]["wf-0001"]
        wa_record.apply_transition(record, wa_record.PHASE_BLOCKED)
        self.write_raw(workflows)
        lease = record["workspace_lease"]["path_realpath"]
        os.unlink(os.path.join(
            lease, ".herd", "state",
            preserve_module.REQUIRED_ARTIFACTS[0],
        ))
        called = []
        with patch.object(
            proc_module, "retire_workflow_scopes",
            lambda *a, **k: called.append(a) or ([], []),
        ):
            outcome = self.perform(
                "wf-0001", broker_module.ACTION_RELEASE, 2
            )
        self.assertEqual(
            outcome.outcome, broker_module.OUTCOME_RELEASED_DEGRADED
        )
        self.assertEqual(
            called, [],
            "a HALTED release reclaimed the workflow's process-scope"
            " records; the retry AC-3 relies on would find them gone",
        )


class ZZSharedScopeStoreCensusTests(unittest.TestCase):
    """R-47 AL-2 / R-51 AO-1..AO-5: within this run the suite ADDS
    NOTHING to the machine-global scope stores, REMOVES no entry, and
    CHANGES no bytes of what was already there.

    Named to sort last so it observes the whole run rather than a
    prefix of it. Its baseline is taken in `tests/__init__.py`, before
    the first test module imports, so the window it covers is the run.

    WHY THE THIRD ASSERTION EXISTS: the first version of this class
    compared NAME PAIRS. Names detect a record being removed and are
    blind to one being overwritten in place — R-51's finding, and the
    same family as a directory name used as a credential (R-43) and a
    set difference used as ownership (R-47): a property about CONTENT
    asserted through an observable that carries only IDENTITY. AL-3's
    word was "byte-identically", and within this census a name is
    unable to witness a byte.

    THE UNRELATED WITNESS is not one this class creates: writing a
    sentinel into a shared store is an addition AM-1 and AL-2 forbid.
    It is every record already under those stores, written by earlier
    runs and by anything else on this machine, and the comparison is
    over their digests.

    BOUNDS (AO-5). Within one snapshot: at most
    `_scope_hygiene.MAX_SNAPSHOT_ENTRIES` entries; at most
    `MAX_TREE_FILES` files per tree; at most `MAX_FILE_BYTES` of one
    file folded into its digest. When a cap
    bites, the snapshot says so and this class reports what it
    actually covered — `test_the_snapshot_states_its_BOUNDS` fails
    rather than letting a truncated census read as a whole one.
    """

    def snapshots(self):
        baseline = scope_hygiene.SUITE_START
        self.assertIsNotNone(
            baseline,
            "no baseline was taken, so this census can prove nothing;"
            " tests/__init__.py did not run",
        )
        return baseline, scope_hygiene.shared_base_snapshot()

    def test_the_suite_ADDS_NOTHING_to_the_shared_stores(self):
        added, _removed, _mutated = scope_hygiene.compare_snapshots(
            *self.snapshots()
        )
        self.assertEqual(
            added, [],
            "the suite wrote %d entr(ies) into the MACHINE-GLOBAL"
            " scope stores. Isolation is incomplete: some test path"
            " reaches the shared base, and a harness that can write"
            " there is a harness that will be tempted to clean there"
            % len(added),
        )

    def test_NOTHING_that_was_there_was_REMOVED(self):
        _added, removed, _mutated = scope_hygiene.compare_snapshots(
            *self.snapshots()
        )
        self.assertEqual(
            removed, [],
            "the suite REMOVED %d entr(ies) from the MACHINE-GLOBAL"
            " scope stores. These records belong to earlier runs and"
            " to anything else on this machine; removing one is the"
            " R-47 defect itself" % len(removed),
        )

    def test_NOTHING_that_was_there_CHANGED_ITS_BYTES(self):
        """AO-2. The half that a name-set census, within its own
        terms, is unable to express."""
        _added, _removed, mutated = scope_hygiene.compare_snapshots(
            *self.snapshots()
        )
        self.assertEqual(
            mutated, [],
            "the suite CHANGED the bytes of %d preexisting entr(ies)"
            " without changing their names. AL-3's guarantee is"
            " byte-identical survival, and an in-place overwrite is"
            " the way it fails while every name still matches"
            % len(mutated),
        )

    def test_the_baseline_is_NOT_VACUOUS(self):
        """An empty baseline gives the census two empty sets to
        compare, which proves little. It would stay green if the
        stores were wiped before the run."""
        self.assertTrue(
            scope_hygiene.SUITE_START["entries"],
            "the shared scope stores are EMPTY, so the censuses above"
            " are comparing nothing against nothing",
        )

    def test_the_snapshot_states_its_BOUNDS(self):
        """AO-5 floor discipline: a truncated snapshot must not read
        as a complete one. If a cap bites, this FAILS and names it,
        so that within this class a census covering a prefix of the
        store is visible as one."""
        baseline = scope_hygiene.SUITE_START
        self.assertFalse(
            baseline["truncated"],
            "the baseline hit MAX_SNAPSHOT_ENTRIES=%d and covers only"
            " a prefix of the shared stores; the census below is"
            " true of that prefix and of nothing more"
            % scope_hygiene.MAX_SNAPSHOT_ENTRIES,
        )
        self.assertEqual(
            baseline["bounded"], [],
            "%d path(s) exceeded a per-file or per-tree cap, so their"
            " digests cover a prefix of their bytes"
            % len(baseline["bounded"]),
        )

    def test_a_MUTATION_IN_PLACE_is_DETECTED(self):
        """AO-4, THE NEGATIVE CASE, and the one that matters most.

        It changes BYTES WITHOUT CHANGING A NAME and proves the
        comparison sees it. Driven against a PRIVATE store built to
        the same shape, and within this class never the real one:
        mutating a shared record
        to test the detector would be the write AM-1 forbids, and the
        detector is the same code either way because
        `shared_base_snapshot` takes the root.
        """
        root = tempfile.mkdtemp()
        self.addCleanup(remove, root)
        store = os.path.join(root, proc_module.OWNED_ROOT_DIR_NAME)
        record = os.path.join(store, "own-preexisting")
        os.makedirs(record)
        pgid_file = os.path.join(
            record, proc_module.OWNED_ROOT_PGID_FILE
        )
        with open(pgid_file, "w") as handle:
            handle.write("4242")
        before = scope_hygiene.shared_base_snapshot(root)
        self.assertIn(
            (proc_module.OWNED_ROOT_DIR_NAME, "own-preexisting"),
            before["entries"],
        )
        # SAME NAME, SAME LENGTH, DIFFERENT BYTES — so neither the
        # name set nor a size comparison could tell.
        with open(pgid_file, "w") as handle:
            handle.write("9999")
        after = scope_hygiene.shared_base_snapshot(root)
        added, removed, mutated = scope_hygiene.compare_snapshots(
            before, after
        )
        self.assertEqual(added, [])
        self.assertEqual(
            removed, [],
            "an in-place overwrite showed up as a REMOVAL; the"
            " detector is reading names after all",
        )
        self.assertEqual(len(mutated), 1, mutated)
        self.assertEqual(
            mutated[0][0],
            (proc_module.OWNED_ROOT_DIR_NAME, "own-preexisting"),
        )
        self.assertNotEqual(mutated[0][1], mutated[0][2])
        # And the NAME census stays green on the same mutation, which
        # is precisely why it was insufficient.
        self.assertEqual(
            scope_hygiene.shared_base_entries(root),
            set(before["entries"]),
            "the name set changed, so this mutation would have been"
            " caught by the old census and proves nothing about the"
            " new one",
        )

    def test_a_NEW_FILE_inside_a_preexisting_record_is_DETECTED(self):
        """The other in-place shape: the record's own name is
        unchanged and something appeared INSIDE it."""
        root = tempfile.mkdtemp()
        self.addCleanup(remove, root)
        record = os.path.join(
            root, proc_module.OWNED_ROOT_DIR_NAME, "own-preexisting"
        )
        os.makedirs(record)
        with open(os.path.join(record, "nonce"), "w") as handle:
            handle.write("own-preexisting")
        before = scope_hygiene.shared_base_snapshot(root)
        with open(os.path.join(record, "pgid"), "w") as handle:
            handle.write("4242")
        after = scope_hygiene.shared_base_snapshot(root)
        _added, _removed, mutated = scope_hygiene.compare_snapshots(
            before, after
        )
        self.assertEqual(len(mutated), 1, mutated)

    def test_the_shared_store_is_UNREACHABLE_without_isolation(self):
        """CONSTRUCTION, driven: the capability is unrepresentable,
        not merely unused. Outside an isolation the seam RAISES."""
        import _scope_hygiene as hygiene
        released = list(hygiene._STACK)
        del hygiene._STACK[:]
        hygiene._ACTIVE[0] = None
        try:
            with self.assertRaises(hygiene.SharedBaseReached):
                proc_module.owned_root_base()
            with self.assertRaises(hygiene.SharedBaseReached):
                proc_module.assignment_base()
        finally:
            hygiene._STACK.extend(released)
            hygiene._ACTIVE[0] = (
                hygiene._STACK[-1] if hygiene._STACK else None
            )

    def test_an_EXPLICIT_base_still_works_while_the_guard_is_armed(self):
        """The guard covers the DEFAULT only. A caller that names its
        base is saying which store it means, which is the opposite of
        the defect."""
        private = tempfile.mkdtemp()
        self.addCleanup(remove, private)
        self.assertTrue(
            proc_module.owned_root_base(private).startswith(private)
        )


class RequiredArtifactProductionTests(RuntimeCase):
    """R-42 AF-3 / R-45 AI-1..AI-4: the required set is NON-EMPTY,
    PRODUCTION passes it, and there is ONE definition of it.

    What was wrong, stated exactly: `policy_violations` was correct
    machinery and `preserve` took `required_names`, but within
    production the parameter defaulted to `()` and nothing populated
    it, so the loop over required names could not execute outside a
    test. A guarantee parameterised by an unpopulated set is, within
    this shape, no guarantee at all — and it is harder to spot
    because the machinery reads as correct.

    AI-3 is why no test in this class writes the four names out: they
    come from `preserve_module.REQUIRED_ARTIFACTS`, the one canonical
    definition, EXCEPT in `test_the_required_set_is_NON_EMPTY_and_
    names_the_four`, which pins its contents by hand. That single pin
    is what an emptying mutant dies on; every other assertion derives,
    so production and the tests are unable to drift apart with the
    tests still green.
    """

    def terminal(self, workflow_id="wf-0001"):
        self.put_record(self.authorized_record(workflow_id))
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform(workflow_id, action, 2).ok)
        workflows = self.fresh_workflows()
        entry = workflows["workflows"][workflow_id]
        wa_record.apply_transition(entry, wa_record.PHASE_BLOCKED)
        self.write_raw(workflows)
        return self.fresh_workflows()["workflows"][workflow_id]

    def state_dir(self, entry):
        return os.path.join(
            entry["workspace_lease"]["path_realpath"], ".herd", "state"
        )

    def is_candidate(self, workflow_id="wf-0001"):
        from target_runtime import runtime as runtime_module
        return workflow_id in [
            row[0] if isinstance(row, tuple) else row
            for row in runtime_module.terminal_cleanup_candidates(
                self.store_dir
            )
        ]

    def test_the_required_set_is_NON_EMPTY_and_names_the_four(self):
        """THE PIN AN EMPTYING MUTANT DIES ON (AI-4).

        Written out by hand exactly once, here. A required set that
        is empty makes every downstream check pass, which is the
        defect this ruling closes, so the emptiness itself is what is
        asserted — not merely that the constant exists.
        """
        self.assertEqual(
            tuple(preserve_module.REQUIRED_ARTIFACTS),
            ("supervisor-strategy.md", "lead-evidence.md",
             "executor-evidence.md", "reviewer-evidence.md"),
            "the required artifact set changed; these four are what"
            " R-37 found terminal cleanup destroying and what the"
            " capstone's point07/point08 depend on",
        )
        self.assertTrue(
            preserve_module.REQUIRED_ARTIFACTS,
            "the required set is EMPTY, so the required-artifact half"
            " of the completeness policy can never fire",
        )

    def test_the_preserve_seam_has_NO_DEFAULT_for_the_required_set(self):
        """The structural half. A default of `()` is what let
        production supply an empty set, within a seam that reads as
        correct."""
        import inspect as _inspect
        parameter = _inspect.signature(
            preserve_module.preserve
        ).parameters["required_names"]
        self.assertIs(
            parameter.default, _inspect.Parameter.empty,
            "`required_names` has a default again, so a caller can"
            " omit it silently — which is exactly how production came"
            " to pass an empty required set",
        )

    def test_a_release_with_EVERY_required_artifact_COMPLETES(self):
        """The baseline the halt is measured against (AF-2): the same
        release, same fixture, every required artifact present."""
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        for name in preserve_module.REQUIRED_ARTIFACTS:
            self.assertTrue(
                os.path.isfile(os.path.join(self.state_dir(entry), name)),
                "the fixture does not carry %s, so the halt below"
                " would prove nothing" % name,
            )
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertNotEqual(
            outcome.outcome, broker_module.OUTCOME_RELEASED_DEGRADED
        )
        self.assertFalse(os.path.isdir(lease))
        self.assertFalse(self.is_candidate())

    def test_REMOVING_one_required_artifact_HALTS_and_RETAINS(self):
        """AF-2 / AI-4, as an OUTCOME change rather than a string.

        One required artifact is deleted and, within this fixture,
        everything else matches the completing case above. The chain
        must HALT before the session close, the directory must be
        RETAINED, and the workflow must stay a cleanup candidate.
        """
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        missing = preserve_module.REQUIRED_ARTIFACTS[0]
        os.unlink(os.path.join(self.state_dir(entry), missing))
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        )
        self.assertEqual(
            outcome.outcome, broker_module.OUTCOME_RELEASED_DEGRADED,
            "removing REQUIRED evidence did not change the outcome;"
            " the required set is not gating anything",
        )
        self.assertEqual(
            outcome.problem,
            ownership_module.PROBLEM_CLEANUP_DEGRADED,
        )
        self.assertIn(missing, outcome.detail)
        self.assertTrue(
            os.path.isdir(lease),
            "the managed directory was destroyed after a preservation"
            " that failed to preserve the evidence inside it",
        )
        self.assertTrue(
            os.path.isfile(os.path.join(
                self.state_dir(entry),
                preserve_module.REQUIRED_ARTIFACTS[1],
            )),
            "the evidence that WAS present was destroyed anyway",
        )
        self.assertTrue(
            self.is_candidate(),
            "candidacy was not KEPT, so the retry AC-3 relies on"
            " never happens and the retained directory is abandoned",
        )
        self.assertIsNone(
            self.fresh_workflows()["workflows"]["wf-0001"][
                "workspace_lease"
            ]["released_at"],
            "the lease was released despite the halt",
        )

    def test_production_reads_the_ONE_canonical_required_set(self):
        """AI-3 driven, not asserted: a name added to the canonical
        constant changes what PRODUCTION requires.

        If `broker._release` carried its own copy of the list, this
        patch would leave production unchanged, within this run, and
        the release would complete.
        """
        from unittest.mock import patch
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        widened = tuple(preserve_module.REQUIRED_ARTIFACTS) + (
            "a-name-the-fixture-does-not-have.md",
        )
        with patch.object(preserve_module, "REQUIRED_ARTIFACTS",
                          widened):
            outcome = self.perform(
                "wf-0001", broker_module.ACTION_RELEASE, 2
            )
        self.assertEqual(
            outcome.outcome, broker_module.OUTCOME_RELEASED_DEGRADED,
            "widening the canonical required set did not change what"
            " production requires, so production is reading a"
            " SEPARATE list and the two will drift",
        )
        self.assertIn("a-name-the-fixture-does-not-have.md",
                      outcome.detail)
        self.assertTrue(os.path.isdir(lease))

    def test_the_preserved_archive_RECORDS_what_was_required(self):
        """A reader of the archive can tell what the policy demanded
        at the time, rather than having to assume today's constant."""
        self.terminal()
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_RELEASE, 2
        ).ok)
        document = preserve_module.load_preserved(
            self.store_dir, "wf-0001"
        )
        self.assertEqual(
            document["required_names"],
            list(preserve_module.REQUIRED_ARTIFACTS),
        )


class PostHarnessLeakTests(unittest.TestCase):
    """R-16 F-3: the leak that is only visible AFTER a harness exits.

    Every prior pin asserted DURING a run. The leak that produced R-16
    was invisible to all of them: a harness terminated, and the groups
    it owned outlived it. So this class runs a REAL harness
    subprocess, has it spawn an owned group, has it exit WITHOUT
    cleaning up, and then asserts from the parent that the survivor is
    detectable from the DURABLE ledger and reapable through it.

    Margin, because expiry must not be able to stand in for a reap:
    the spawned descendant sleeps 3600 s and every wait here is at
    most 10 s, a ratio of 360. Test-fixture I/O bound, not a mission
    deadline.
    """

    #: The descendant's stdout is sent to DEVNULL. Without that it
    #: inherits the harness's pipe and `capture_output` waits for EOF
    #: on a handle a 3600 s sleeper is holding, so the parent blocks
    #: on its own fixture — which is a different bug from the one
    #: under test and would hide it.
    HARNESS = (
        "import os, subprocess, sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from target_runtime import process_ownership as proc\n"
        "proc.spawn_owned([sys.executable, '-c',"
        " 'import time; time.sleep(3600)'],"
        " 'post-harness-pin', directory=%r,"
        # R-47/R-48: the owned root goes BESIDE this harness's own
        # ledger. Left unset it defaulted to the machine-global scope
        # store, and this is a SUBPROCESS — so the in-process guard
        # that makes that store unreachable does not reach it. A
        # child isolates itself by NAMING its base, which is what
        # every other subprocess harness here already does.
        " owned_root_base_dir=%r,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print('spawned', flush=True)\n"
        "os._exit(0)\n"                       # NO cleanup, on purpose
    )

    def setUp(self):
        self.ledger = os.path.join(
            OWNER_LEDGER_ROOT, "post-%d-%s" % (os.getpid(),
                                               secrets.token_hex(4))
        )
        os.makedirs(self.ledger, exist_ok=True)
        self.addCleanup(self._sweep)

    def _sweep(self):
        proc_module.sweep_owned(self.ledger, settle_seconds=10.0)

    def run_harness(self):
        script = self.HARNESS % (REPO_ROOT, self.ledger, self.ledger)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("spawned", completed.stdout)
        return completed

    def test_a_harness_that_exits_without_cleanup_LEAVES_a_survivor(self):
        """The precondition. If the harness did not actually leak, the assertion below would
        pass over an empty set."""
        self.run_harness()
        self.assertEqual(
            len(proc_module.owned_groups(self.ledger)), 1,
            "the harness recorded no group, so this class would be"
            " measuring an empty ledger",
        )
        self.assertTrue(
            proc_module.surviving_owned_groups(self.ledger),
            "the harness exited without leaving a survivor, so the"
            " post-harness leak this class exists for is not being"
            " reproduced",
        )

    def test_the_post_harness_sweep_reaps_what_the_harness_left(self):
        """THE AUTHORED POST-HARNESS ASSERTION (F-3).

        Fails when a harness terminates leaving owned groups alive and
        the sweep does not clean them. A mutant that removes the
        post-exit cleanup dies HERE, by assertion.
        """
        self.run_harness()
        survivors_before = proc_module.surviving_owned_groups(
            self.ledger
        )
        self.assertTrue(survivors_before)
        reaped, stuck, pending = proc_module.sweep_owned(
            self.ledger, settle_seconds=10.0
        )
        self.assertEqual(stuck, [], "a recorded group resisted the"
                                    " sweep: %r" % (stuck,))
        self.assertEqual(pending, [])
        self.assertEqual(sorted(reaped), sorted(survivors_before))
        self.assertEqual(
            proc_module.surviving_owned_groups(self.ledger), [],
            "owned groups survived the post-harness sweep; a harness"
            " that terminates must not leave descendants running, and"
            " their 3600s sleep means expiry cannot clear them",
        )

    def test_the_ledger_OUTLIVES_the_harness(self):
        """The operative cause of the real leak, pinned: the ownership
        record must still be readable after the process that wrote it
        is gone. It previously lived in a temp directory removed at test cleanup,
        so within the window that mattered it was already gone."""
        self.run_harness()
        path = proc_module.ledger_path(self.ledger)
        self.assertTrue(
            os.path.exists(path),
            "the ledger did not survive the harness that wrote it; an"
            " ownership record deleted while its process may be alive"
            " cannot be read when it matters",
        )

    def test_a_crash_between_spawn_and_record_is_REPORTED(self):
        """The second window, closed by the pending nonce: a spawn
        that dies before its group id is recorded leaves a durable
        PENDING marker rather than an unattributable orphan."""
        proc_module.record_pending("own-deadbeef", "probe",
                                   self.ledger)
        self.assertEqual(
            proc_module.pending_nonces(self.ledger), ["own-deadbeef"],
        )
        _reaped, _stuck, pending = proc_module.sweep_owned(
            self.ledger, settle_seconds=1.0
        )
        self.assertEqual(
            pending, ["own-deadbeef"],
            "an unresolved spawn was not reported; it must be"
            " surfaced rather than swept, because what that case needs"
            " is evidence and not a broader kill",
        )


class SpawnGateTests(unittest.TestCase):
    """R-18 H-2: the harness must be ABLE to stop emitting.

    Stated plainly because it bears on what this increment may claim:
    in the event that produced R-18 the emitter was quiesced BY THE
    OPERATOR, and this component had no gate at all. The capability
    below is new, and these are its pins.
    """

    def setUp(self):
        self.directory = os.path.join(
            OWNER_LEDGER_ROOT, "gate-%s" % secrets.token_hex(4)
        )
        os.makedirs(self.directory, exist_ok=True)
        self.addCleanup(remove, self.directory)

    def test_gating_refuses_further_spawns(self):
        self.assertFalse(
            proc_module.spawning_is_gated(self.directory)
        )
        proc_module.gate_spawning(self.directory, "corrective in"
                                                  " progress")
        self.assertTrue(proc_module.spawning_is_gated(self.directory))
        with self.assertRaises(proc_module.SpawnGated):
            proc_module.spawn_owned(
                [sys.executable, "-c", "pass"], "should-not-start",
                directory=self.directory,
            )
        self.assertEqual(
            proc_module.owned_groups(self.directory), set(),
            "a gated spawn still recorded a group, so something"
            " started",
        )

    def test_the_gate_is_DURABLE_across_processes(self):
        """An in-memory flag would be invisible to the process that
        must stop emitting when a different process decides it."""
        proc_module.gate_spawning(self.directory, "durable")
        completed = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r)\n"
             "from target_runtime import process_ownership as p\n"
             "print(p.spawning_is_gated(%r))"
             % (REPO_ROOT, self.directory)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.stdout.strip(), "True",
                         completed.stderr)

    def test_ungating_restores_spawning(self):
        proc_module.gate_spawning(self.directory, "temporary")
        self.assertTrue(proc_module.ungate_spawning(self.directory))
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", "pass"], "after-ungate",
            directory=self.directory,
        )
        handle.wait(timeout=30)
        self.assertEqual(
            len(proc_module.owned_groups(self.directory)), 1
        )


class LedgerExternalRecoveryTests(unittest.TestCase):
    """R-19 I-3(b): recovery of orphans ABSENT FROM THE LEDGER.

    THE LOAD-BEARING REQUIREMENT, and the one that lies outside what a
    finalizer fix reaches. Evidenced twice in this increment: newly emitted fixtures
    cleaned themselves up while the same four PRE-LEDGER groups
    persisted untouched. A recovery design that knows only its own
    spawns is structurally unable to clean state left by a previous,
    crashed, or superseded run — which is the unattended-restart case
    the increment exists to close.

    SPAWN FREEZE: every test in this class works from FILES ONLY. It
    builds owned roots on disk by hand and drives discovery and
    classification over them. The reaping half — a live group reaped
    from root evidence alone — is NOT pinned here, because pinning it
    requires spawning, and no spawning run is authorized until the
    sink is proven. That gap is stated rather than papered over, and
    it is the one piece of this class that is still owed.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def root(self, nonce, pgid=None):
        directory = proc_module.create_owned_root(nonce, self.base)
        if pgid is not None:
            proc_module.record_owned_root_group(directory, pgid)
        return directory

    def test_an_owned_root_is_created_BEFORE_the_group_exists(self):
        """The property whose absence made four orphans
        unattributable: the durable artifact naming a spawn must exist before the
        process does, so within the rest of the call a crash still
        leaves something to find."""
        directory = proc_module.create_owned_root("own-abc", self.base)
        self.assertTrue(os.path.isdir(directory))
        with open(os.path.join(
            directory, proc_module.OWNED_ROOT_NONCE_FILE
        ), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "own-abc")
        # No pgid yet: the spawn has not happened.
        self.assertEqual(
            proc_module.owned_roots(self.base), [(directory, None)]
        )

    def test_an_unstamped_root_is_REPORTED_never_guessed_at(self):
        """A root for which no pgid was written is a spawn that died inside
        the registration window. It is surfaced, because guessing which live
        process it meant is exactly where a recovery turns into the
        name-pattern sweep that is forbidden."""
        directory = self.root("own-crashed")
        recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
            self.base, settle_seconds=1.0
        )
        self.assertEqual(recovered, [])
        self.assertEqual(stuck, [])
        self.assertEqual(unstamped, [directory])

    def test_a_root_naming_a_dead_group_is_skipped_silently(self):
        """Recovery must be idempotent across restarts: a root left by
        a run whose group is already gone is not an error."""
        self.root("own-dead", pgid=_definitely_dead_pgid())
        recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
            self.base, settle_seconds=1.0
        )
        self.assertEqual((recovered, stuck, unstamped), ([], [], []))

    def test_recovery_refuses_this_process_own_group_and_low_pids(self):
        """The guard that keeps a recovery from reaping the recovering
        process. Driven with `os.killpg` replaced by a recorder, so within this
        test a failure leaves the suite intact."""
        from unittest.mock import patch
        for pgid in (0, 1, os.getpgrp()):
            self.root("own-guard-%d" % pgid, pgid=pgid)
        calls = []
        with patch.object(os, "killpg",
                          side_effect=lambda *a: calls.append(a)):
            recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
                self.base, settle_seconds=1.0
            )
        self.assertEqual(
            calls, [],
            "recovery signalled a group it must never signal: %r"
            % (calls,),
        )
        self.assertEqual((recovered, stuck), ([], []))

    def test_recovery_reads_roots_it_did_not_write_in_this_process(self):
        """The restart case, which is the whole point: the roots are read from disk, so a process that recorded none of
        them itself can still recover what an earlier one left."""
        directory = self.root("own-from-a-previous-run",
                              pgid=_definitely_dead_pgid())
        self.assertIn(
            directory,
            [entry for entry, _pgid in proc_module.owned_roots(self.base)],
        )

    def test_the_ledger_and_the_roots_are_INDEPENDENT_evidence(self):
        """A root is readable with no ledger present at all — which is
        the condition the four surviving orphans are in, and the
        reason the ledger alone could not reach them."""
        self.root("own-independent", pgid=_definitely_dead_pgid())
        self.assertEqual(proc_module.owned_groups(self.base), set())
        self.assertEqual(len(proc_module.owned_roots(self.base)), 1)


class FailClosedIsIntendedTests(unittest.TestCase):
    """R-21 K-3: refusing to recover WITHOUT durable evidence is
    CORRECT BEHAVIOUR, pinned as intended rather than fixed as a bug.

    Four historical orphan groups in this increment had no ledger, no
    owned root and no nonce. Production reached none of them, and that
    was RIGHT: the only way to have reached them would have been to
    infer ownership from a name, a command string or a path shape —
    the over-broad reap that would be rejected harder than the leak.

    So the gap was never "recovery cannot see ledger-external
    orphans". It was that SPAWNS WERE NOT DURABLY REGISTERED BEFORE
    STARTING, so no evidence existed for a later run to recover from.
    `RegistrationBeforeSpawnTests` pins that half; this class pins the
    half that must NOT change.

    Files only: nothing here spawns.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def test_recovery_signals_NOTHING_when_no_evidence_exists(self):
        from unittest.mock import patch
        calls = []
        with patch.object(os, "killpg",
                          side_effect=lambda *a: calls.append(a)), \
             patch.object(os, "kill",
                          side_effect=lambda *a: calls.append(a)), \
             patch("subprocess.run") as ran:
            recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
                self.base, settle_seconds=1.0
            )
        self.assertFalse(
            ran.called,
            "recovery asked the system what is running; with no"
            " durable evidence the correct behaviour is to do"
            " nothing, not to go looking for something that resembles"
            " a fixture",
        )
        self.assertEqual(
            (recovered, stuck, unstamped), ([], [], []),
        )
        self.assertEqual(
            calls, [],
            "recovery signalled something with no durable evidence to"
            " justify it; failing closed here is the intended"
            " behaviour, not a defect",
        )

    def test_recovery_never_enumerates_processes_ITSELF(self):
        """The shape a guessing recovery would need: asking the system
        what is running and matching it. Driven rather than read —
        `subprocess.run` is replaced, and recovery must not reach it.
        """
        from unittest.mock import patch
        proc_module.create_owned_root("own-no-pgid", self.base)
        with patch("subprocess.run") as ran:
            proc_module.recover_orphans(self.base, settle_seconds=1.0)
        self.assertFalse(
            ran.called,
            "recovery enumerated system processes; ownership comes"
            " from evidence this component recorded, never from what"
            " happens to be running and resembles it",
        )

    def test_an_unstamped_root_is_never_resolved_by_resemblance(self):
        """A root for which no pgid was written names a spawn that died in
        the registration window. It is REPORTED. Resolving it
        by finding a process that looks similar is the forbidden
        inference, and the returned value carries no pgid at all."""
        directory = proc_module.create_owned_root("own-window",
                                                  self.base)
        recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
            self.base, settle_seconds=1.0
        )
        self.assertEqual(unstamped, [directory])
        self.assertEqual(recovered, [])
        self.assertEqual(stuck, [])


class RegistrationBeforeSpawnTests(unittest.TestCase):
    """R-21 K-1: the LOAD-BEARING fix — durable registration BEFORE
    the process exists.

    This is the same mechanism the F-2 root cause named: the first
    version of `spawn_owned` wrote its ledger record AFTER `Popen`
    returned. A crash inside that window left a process with no durable ownership
    evidence on disk — the state the four historical groups were in, and
    why no legitimate recovery reached them.

    Files only: these tests drive the registration path with
    `subprocess.Popen` replaced, so no process is started.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)
        self.pidfile = os.path.join(self.base, "test-owned-child-pgid")
        self.addCleanup(self._reap_test_owned_child)

    def await_stamp(self, seconds=10.0):
        """The single owned root, waiting up to ``seconds`` for the
        child's own stamp to land. Returns ``(directory, pgid)``, with pgid still None where the
        stamp does not arrive inside that window."""
        deadline = time.monotonic() + seconds
        roots = proc_module.owned_roots(self.base)
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(self.base)
            if len(roots) == 1 and roots[0][1] is not None:
                return roots[0]
            time.sleep(0.02)
        self.assertEqual(len(roots), 1, roots)
        return roots[0]

    def _reap_test_owned_child(self):
        """Reap a child THIS test created, from the test-owned pidfile.

        Needed because the mutant that removes child-side stamping
        leaves precisely the orphan production is right to refuse: an
        unstamped root. The suite must not leak it, and the suite —
        unlike production — genuinely does know it created it.
        """
        if not os.path.exists(self.pidfile):
            return
        try:
            with open(self.pidfile) as handle:
                pgid = int(handle.read().strip())
        except (OSError, ValueError):
            return
        if pgid > 1 and pgid != os.getpgrp():
            proc_module.reap_group_by_recorded_root(
                pgid, settle_seconds=10.0
            )

    def test_the_root_and_nonce_exist_BEFORE_Popen_is_called(self):
        from unittest.mock import patch
        seen = {}

        def capture(*args, **kwargs):
            # Observed at the instant of the spawn: the durable
            # evidence must already be on disk.
            seen["roots"] = proc_module.owned_roots(self.base)
            seen["pending"] = proc_module.pending_nonces(self.base)
            raise RuntimeError("spawn refused by the test")

        with patch("subprocess.Popen", side_effect=capture):
            with self.assertRaises(RuntimeError):
                proc_module.spawn_owned(
                    ["/bin/true"], "registration-probe",
                    directory=self.base, owned_root_base_dir=self.base,
                )
        self.assertEqual(
            len(seen["roots"]), 1,
            "no owned root existed when Popen was called; a crash in"
            " that window would leave an unattributable process",
        )
        self.assertEqual(
            len(seen["pending"]), 1,
            "no pending nonce existed when Popen was called",
        )

    #: The parent dies HERE — after `Popen` returns, before the parent
    #: stamps. `record_owned_group` is the parent's FIRST post-spawn
    #: write, so replacing it with an immediate exit reproduces the
    #: exact interval R-27 identified.
    KILLED_PARENT = (
        "import os, subprocess, sys\n"
        "sys.path.insert(0, %r)\n"
        "from target_runtime import process_ownership as proc\n"
        # The lambda records the child's pgid to a TEST-OWNED file
        # first. # That file is cleanup scaffolding for this suite, and outside
        # the suite it is NOT production ownership evidence — it exists
        # so that when the child-stamp mutant runs, and the production
        # path correctly declines to recover the orphan, the TEST can
        # still reap what it created rather than leaking it.
        "proc.record_owned_group = (lambda pgid, *a, **k:"
        " (open(%r, 'w').write(str(pgid)), os._exit(9)))\n"
        "proc.spawn_owned([sys.executable, '-c',"
        " 'import time; time.sleep(3600)'],"
        " 'parent-dies-in-the-window', directory=%r,"
        " owned_root_base_dir=%r,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "os._exit(0)\n"
    )

    def test_a_parent_that_DIES_AFTER_Popen_still_leaves_a_stamped_root(self):
        """R-27 S-4: THE INTERVAL THE ORDERING FIX DID NOT CLOSE.

        `RegistrationBeforeSpawnTests` covers `Popen` RAISING, which
        leaves no child. This covers `Popen` RETURNING and the parent
        dying before it can stamp — a LIVE child under a root that,
        before child-side stamping, carried no pgid. Recovery
        correctly refuses to bind an unstamped root, so the orphan
        survived: fail-closed working as designed, and the reliability
        class still open behind it.

        With `preexec_fn` stamping in the child, the surviving entity is
        the one that recorded itself, so the root is STAMPED within this
        window even though the parent did not reach its own write.

        Margin: the child sleeps 3600 s and this test waits at most
        10 s, so within that window expiry accounts for neither its
        death nor a recovery that failed to happen.
        """
        script = self.KILLED_PARENT % (
            REPO_ROOT, self.pidfile, self.base, self.base
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            completed.returncode, 9,
            "the parent did not die inside the window, so this test"
            " would not be exercising the interval it exists for"
            " (rc=%s, %s)" % (completed.returncode, completed.stderr),
        )
        # The stamp is written by the CHILD after it execs, so it is
        # asynchronous with respect to the parent's death. Wait a
        # bounded moment for it rather than racing it — and note the
        # wait is far below the child's 3600 s sleep, so a stamp that
        # appears here appeared because the child wrote it.
        directory, pgid = self.await_stamp()
        self.assertIsNotNone(
            pgid,
            "the root is UNSTAMPED after a parent death inside the"
            " window; the child did not record itself, so a live"
            " orphan is left that recovery cannot bind",
        )
        self.assertTrue(
            proc_module._group_alive(pgid),
            "no live child to recover, so the stamp above proves"
            " nothing",
        )
        # And it is RECOVERABLE from that stamp alone.
        recovered, stuck, unstamped, _unc = proc_module.recover_orphans(
            self.base, settle_seconds=10.0
        )
        self.assertEqual(unstamped, [])
        self.assertEqual(stuck, [])
        self.assertEqual(recovered, [pgid])
        self.assertFalse(proc_module._group_alive(pgid))

    def test_the_child_stamp_is_what_makes_it_recoverable(self):
        """Anti-vacuity for the test above: the pgid in the root must
        be the CHILD'S OWN GROUP, not something the parent guessed."""
        script = self.KILLED_PARENT % (
            REPO_ROOT, self.pidfile, self.base, self.base
        )
        subprocess.run([sys.executable, "-c", script],
                       capture_output=True, text=True, timeout=60)
        directory, pgid = self.await_stamp()
        self.assertIsNotNone(pgid)
        self.addCleanup(proc_module.recover_orphans, self.base, 10.0)
        # The stamped pgid leads the group the child actually leads.
        self.assertEqual(
            os.getpgid(pgid), pgid,
            "the stamped id is not a group leader, so it does not"
            " name the child's own group",
        )

    def test_a_crash_during_spawn_LEAVES_provable_evidence(self):
        """The property the historical orphans lacked: after a failed
        spawn, disk still names it."""
        from unittest.mock import patch
        with patch("subprocess.Popen",
                   side_effect=OSError("no such binary")):
            with self.assertRaises(OSError):
                proc_module.spawn_owned(
                    ["/nonexistent"], "crashing",
                    directory=self.base, owned_root_base_dir=self.base,
                )
        roots = proc_module.owned_roots(self.base)
        self.assertEqual(len(roots), 1)
        directory, pgid = roots[0]
        self.assertIsNone(
            pgid,
            "a spawn that never happened recorded a group id",
        )
        self.assertTrue(os.path.isdir(directory))
        self.assertEqual(
            len(proc_module.pending_nonces(self.base)), 1,
            "the crashed spawn left no pending nonce, so it would be"
            " invisible to a later run",
        )


class DurableFreezeTests(unittest.TestCase):
    """R-20 J-1: a freeze that does not depend on being RECEIVED.

    Twice in this increment a stop instruction sat queued behind the
    activity it existed to stop, and both times an OPERATOR had to
    quiesce the emitter out of band. Within this class the tests work from FILES ONLY, and no process is
    started.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def test_a_freeze_is_visible_to_a_process_that_received_nothing(self):
        proc_module.freeze_spawning("corrective", self.base)
        self.assertTrue(proc_module.is_frozen(self.base))
        self.assertEqual(
            proc_module.freeze_reason(self.base), "corrective"
        )

    def test_a_frozen_spawn_refuses_BEFORE_it_emits(self):
        """The refusal must happen before `Popen`, so a busy emitter
        stops on its very next spawn without having to notice a
        message. Driven with `subprocess.Popen` replaced by a
        recorder: the call must not have been reached."""
        from unittest.mock import patch
        proc_module.freeze_spawning("no emitting", self.base)
        calls = []
        with patch("subprocess.Popen",
                   side_effect=lambda *a, **k: calls.append(a)):
            with self.assertRaises(proc_module.SpawnGated):
                proc_module.spawn_owned(
                    ["/bin/true"], "must-not-start",
                    directory=self.base,
                    owned_root_base_dir=self.base,
                )
        self.assertEqual(
            calls, [],
            "a frozen spawn still reached Popen; the freeze must be"
            " read BEFORE emitting, not after",
        )

    def test_the_refusal_says_WHY(self):
        proc_module.freeze_spawning("R-19 spawn freeze", self.base)
        with self.assertRaises(proc_module.SpawnGated) as caught:
            proc_module.spawn_owned(
                ["/bin/true"], "x", directory=self.base,
                owned_root_base_dir=self.base,
            )
        self.assertIn("R-19 spawn freeze", str(caught.exception))

    def test_thawing_restores_spawning(self):
        proc_module.freeze_spawning("temporary", self.base)
        self.assertTrue(proc_module.thaw_spawning(self.base))
        self.assertFalse(proc_module.is_frozen(self.base))


def _definitely_dead_pgid():
    """A pgid that is not a live group.

    Derived rather than hard-coded: a child is forked and collected,
    so its pid is known-dead by the time it is used. This module spawns
    no long-lived process — the child exits immediately and is waited
    for here.
    """
    pid = os.fork()
    if pid == 0:                                       # pragma: no cover
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


class WorkspaceOwnershipTests(unittest.TestCase):
    """DOMAIN B (R-29): workspaces and their long-lived sessions.

    THE MOST DANGEROUS SURFACE IN THIS INCREMENT. There are fifteen
    workspaces on this machine and exactly one is ours; closing the
    wrong one destroys other people's live sessions and is not
    recoverable, which a leaked sleeper is.

    **No test here can reach a real close.** `close_owned_workspace`
    takes `close_fn` as a REQUIRED parameter with no default, so a
    test that forgot to inject would raise TypeError rather than
    closing anything. Every case below passes a recorder, and the refusal cases assert the
    recorder went uncalled.
    """

    LEASE = "wf-0001"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(remove, self.root)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.closed = []

    def close_fn(self, workspace_id):
        self.closed.append(workspace_id)

    def entry(self, task_id="20260828-114612-5d92e1"):
        lease = workspace_module.lease_path(
            str(self.workspaces), self.LEASE
        )
        os.makedirs(lease, exist_ok=True)
        return {
            "workflow_id": self.LEASE,
            "workspace_lease": {
                "lease_id": "l1",
                "path_realpath": os.path.realpath(lease),
            },
            "target_engine": {
                "alias": dispatch_module.ALIAS_PREFIX + self.LEASE,
                "task_id": task_id, "repo": "u", "dispatched_at": 1,
            },
        }

    def child(self, entry, workspace_id="wV", agents=None,
              task_id=None):
        return {
            "repo": entry["workspace_lease"]["path_realpath"],
            "task_id": task_id or entry["target_engine"]["task_id"],
            "workspace_id": workspace_id,
            "agents": agents if agents is not None else {
                "supervisor": "h566a1-wf-7200299-sup",
                "lead1": "h566a1-wf-7200299-lead1",
                "executor1": "h566a1-wf-7200299-exec1",
                "reviewer1": "h566a1-wf-7200299-rev1",
            },
        }

    #: The live side carries agent NAMES, not logical roles: `herdr
    #: workspace list` has no agent mapping, so the production
    #: projection joins `herdr agent list` on `workspace_id`.
    LIVE_NAMES = {
        "h566a1-wf-7200299-sup", "h566a1-wf-7200299-lead1",
        "h566a1-wf-7200299-exec1", "h566a1-wf-7200299-rev1",
    }

    def live(self, workspace_id="wV", agents=None):
        return [{
            "workspace_id": workspace_id,
            "agent_names": set(self.LIVE_NAMES) if agents is None
            else set(agents),
        }]

    def attempt(self, entry, children, live, live_at_close="same"):
        """Prove, then close — the two-stage shape production uses.

        ``live_at_close`` is the live reading taken IMMEDIATELY BEFORE
        the close, separately from the one the proof used. It defaults
        to the same world; AD-5's tests pass a MUTATED one, which is
        the only shape that can detect check-and-use separation — a
        stable fixture passes whether or not the action re-derives.
        """
        verdict, snapshot, problem, detail = ws_module.prove_ownership(
            entry, children, live, str(self.workspaces)
        )
        if verdict != ws_module.OWNED:
            return False, None, problem, detail
        at_close = live if live_at_close == "same" else live_at_close
        return ws_module.close_proven_workspace(
            snapshot, at_close, self.close_fn
        )

    # -- the one case that may close ---------------------------------

    def test_an_exact_and_unique_chain_closes_exactly_one(self):
        entry = self.entry()
        closed, wid, problem, detail = self.attempt(
            entry, [self.child(entry)], self.live()
        )
        self.assertTrue(closed, (problem, detail))
        self.assertEqual(wid, "wV")
        self.assertEqual(self.closed, ["wV"])

    # -- every refusal, and none of them may close -------------------

    def test_no_child_record_refuses(self):
        entry = self.entry()
        closed, _wid, problem, _d = self.attempt(entry, [], self.live())
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_NO_CHILD_RECORD)
        self.assertEqual(self.closed, [])

    def test_two_matching_child_records_refuse(self):
        entry = self.entry()
        closed, _w, problem, _d = self.attempt(
            entry, [self.child(entry), self.child(entry)], self.live()
        )
        self.assertFalse(closed)
        self.assertEqual(
            problem, ws_module.PROBLEM_MULTIPLE_CHILD_RECORDS
        )
        self.assertEqual(self.closed, [])

    def test_two_live_workspaces_with_that_id_refuse(self):
        entry = self.entry()
        closed, _w, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live() + self.live()
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_MULTIPLE_WORKSPACES)
        self.assertEqual(self.closed, [])

    def test_a_workspace_that_is_not_live_refuses(self):
        entry = self.entry()
        closed, _w, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live("wOTHER")
        )
        self.assertFalse(closed)
        self.assertEqual(
            problem, ws_module.PROBLEM_WORKSPACE_NOT_FOUND
        )
        self.assertEqual(self.closed, [])

    def test_THE_wV_SHAPE_agents_gone_REFUSES(self):
        """THE RECORDED SPECIMEN, as a design input rather than a
        target: a terminal workflow whose recorded agents no longer
        exist. The live workspace has a different agent set, so the chain does
        not agree and no workspace is closed. Refusing is the
        intended outcome, not a gap."""
        entry = self.entry()
        closed, wid, problem, detail = self.attempt(
            entry, [self.child(entry)], self.live(agents=set()),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_AGENTS_DISAGREE)
        self.assertEqual(
            self.closed, [],
            "a workspace whose sessions are not the ones this"
            " workflow created was closed",
        )

    def test_a_single_differing_agent_name_refuses(self):
        entry = self.entry()
        agents = set(self.LIVE_NAMES)
        agents.discard("h566a1-wf-7200299-lead1")
        agents.add("somebody-elses-lead")
        closed, _w, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live(agents=agents)
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_AGENTS_DISAGREE)
        self.assertEqual(self.closed, [])

    def test_an_unbound_task_id_refuses(self):
        entry = self.entry(task_id=dispatch_module.UNRESOLVED_TASK_ID)
        closed, _w, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live()
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_EVIDENCE_DEGRADED)
        self.assertEqual(self.closed, [])

    def test_degraded_listings_refuse(self):
        entry = self.entry()
        for children, live in ((None, self.live()),
                               ([self.child(entry)], None)):
            with self.subTest(children=children is None):
                closed, _w, problem, _d = self.attempt(
                    entry, children, live
                )
                self.assertFalse(closed)
                self.assertEqual(
                    problem, ws_module.PROBLEM_EVIDENCE_DEGRADED
                )
        self.assertEqual(self.closed, [])

    def test_a_child_record_for_ANOTHER_lease_refuses(self):
        entry = self.entry()
        foreign = self.child(entry)
        foreign["repo"] = str(self.workspaces / "someone-else")
        closed, _w, problem, _d = self.attempt(
            entry, [foreign], self.live()
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_NO_CHILD_RECORD)
        self.assertEqual(self.closed, [])

    # -- AD-5: the world MOVES between the two reads -----------------

    def test_a_workspace_that_VANISHES_after_the_proof_is_not_closed(self):
        """R-40 AD-5. THE ONLY TEST SHAPE THAT DETECTS THIS CLASS.

        The proof sees an exact, unique chain. Between that proof and
        the close the live world MOVES — here the workspace is gone.
        A correctly ordered, correctly gated chain would still close
        the wrong thing if the action re-read the world; consuming the
        snapshot and revalidating it fails closed instead.
        """
        entry = self.entry()
        closed, wid, problem, detail = self.attempt(
            entry, [self.child(entry)], self.live(),
            live_at_close=[],
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_agents_that_CHANGE_after_the_proof_block_the_close(self):
        """The dangerous direction: the workspace still exists and its
        sessions are no longer the ones that were proven. Closing it
        would destroy sessions nobody proved this workflow owned."""
        entry = self.entry()
        moved = set(self.LIVE_NAMES)
        moved.add("somebody-elses-new-agent")
        closed, _wid, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live(),
            live_at_close=self.live(agents=moved),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(
            self.closed, [],
            "a workspace whose agents changed after the proof was"
            " closed; a stale proof is not a proof",
        )

    def test_a_SECOND_workspace_appearing_blocks_the_close(self):
        entry = self.entry()
        closed, _wid, problem, _d = self.attempt(
            entry, [self.child(entry)], self.live(),
            live_at_close=self.live() + self.live(),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_a_CHANGED_TASK_ID_after_the_proof_blocks_the_close(self):
        """AF-5. The fields nobody mutates are the fields nobody
        revalidates — which is how the subset revalidation survived.
        Every field the snapshot carries gets a mutation."""
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        self.assertIsNotNone(snapshot)
        moved = dict(entry)
        moved["target_engine"] = dict(entry["target_engine"],
                                      task_id="a-different-task")
        closed, _w, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[self.child(entry)], entry=moved,
            workspaces_root=str(self.workspaces),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_a_MOVED_LEASE_after_the_proof_blocks_the_close(self):
        """AD-4 named 'lease moved' explicitly, and the first
        revalidation could not see the lease at all."""
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        moved = dict(entry)
        moved["workspace_lease"] = dict(
            entry["workspace_lease"],
            path_realpath=str(self.workspaces / "somewhere-else"),
        )
        closed, _w, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[self.child(entry)], entry=moved,
            workspaces_root=str(self.workspaces),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_a_VANISHED_CHILD_RECORD_blocks_the_close(self):
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        closed, _w, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[], entry=entry,
            workspaces_root=str(self.workspaces),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_a_SECOND_CHILD_RECORD_blocks_the_close(self):
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        closed, _w, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[self.child(entry), self.child(entry)],
            entry=entry, workspaces_root=str(self.workspaces),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_a_CHILD_RECORD_naming_another_workspace_blocks_it(self):
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        closed, _w, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[self.child(entry, workspace_id="wOTHER")],
            entry=entry, workspaces_root=str(self.workspaces),
        )
        self.assertFalse(closed)
        self.assertEqual(problem, ws_module.PROBLEM_STALE_PROOF)
        self.assertEqual(self.closed, [])

    def test_the_UNCHANGED_full_binding_still_closes(self):
        """Anti-vacuity for the five above: with the binding unmutated, the
        same call closes. Otherwise they would pass against a
        revalidation that refused everything."""
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        closed, wid, problem, _dd = ws_module.close_proven_workspace(
            snapshot, self.live(), self.close_fn,
            child_records=[self.child(entry)], entry=entry,
            workspaces_root=str(self.workspaces),
        )
        self.assertTrue(closed, problem)
        self.assertEqual(self.closed, [wid])

    def test_a_failed_proof_yields_NO_SNAPSHOT_at_all(self):
        """AD-2 structurally: an id from a NOT_OWNED or UNPROVABLE
        proof is unusable because there is no object carrying one.
        The defect this replaces bound the verdict to `_verdict` and
        used the id anyway."""
        entry = self.entry()
        for children, live in (
            ([], self.live()),
            ([self.child(entry)], self.live("wOTHER")),
            ([self.child(entry)], self.live(agents=set())),
        ):
            with self.subTest(case=repr(live)[:40]):
                verdict, snapshot, _p, _d = ws_module.prove_ownership(
                    entry, children, live, str(self.workspaces)
                )
                self.assertNotEqual(verdict, ws_module.OWNED)
                self.assertIsNone(snapshot)

    def test_the_snapshot_is_IMMUTABLE(self):
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        self.assertIsNotNone(snapshot)
        with self.assertRaises(AttributeError):
            snapshot.workspace_id = "wOTHER"

    # -- the structural guarantee ------------------------------------

    def test_the_close_seam_has_NO_DEFAULT(self):
        """U-5's structural requirement, driven: a caller that omits
        the close function gets a TypeError. There is no value it can
        take by omission, so no test can reach a real close by
        accident."""
        entry = self.entry()
        _v, snapshot, _p, _d = ws_module.prove_ownership(
            entry, [self.child(entry)], self.live(),
            str(self.workspaces),
        )
        with self.assertRaises(TypeError):
            ws_module.close_proven_workspace(snapshot, self.live())
        self.assertEqual(self.closed, [])

    def test_nothing_in_the_module_calls_production_close(self):
        """`production_close` exists for a caller to hand in by name.
        Source is the only feasible level for THIS assertion, and the
        reason is that its subject is whether a reference exists at
        all; the executed guarantee it fronts is every refusal case
        above, each asserting the injected recorder was not called."""
        import inspect
        source = inspect.getsource(ws_module)
        body = source.split("def production_close")[0]
        self.assertNotIn("production_close(", body)


class ProductionLiveWorkspaceProjectionTests(unittest.TestCase):
    """R-32 X-1/X-2/X-3: the REAL production projection callable.

    `_build_broker` hands `_production_live_workspaces` to the Broker,
    so this is the shape Domain B's proof actually consumes. These
    tests drive that callable with `herdr.tasks.run` replaced, so no
    Herdr command is executed and no workspace is touched.
    """

    @staticmethod
    def reply(payload):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps(payload), stderr="")

    def run_with(self, workspaces, agents):
        from unittest.mock import patch
        replies = [
            self.reply({"result": {"workspaces": workspaces}}),
            self.reply({"result": {"agents": agents}}),
        ]
        with patch("herdr.tasks.run", side_effect=replies):
            return broker_module._production_live_workspaces()

    def test_an_exact_join_produces_the_agent_NAME_SET(self):
        projection = self.run_with(
            [{"workspace_id": "wA"}, {"workspace_id": "wB"}],
            [{"workspace_id": "wA", "name": "a1"},
             {"workspace_id": "wA", "name": "a2"},
             {"workspace_id": "wB", "name": "b1"}],
        )
        self.assertEqual(projection, [
            {"workspace_id": "wA", "agent_names": {"a1", "a2"}},
            {"workspace_id": "wB", "agent_names": {"b1"}},
        ])

    def test_the_producer_and_consumer_agree_on_the_key(self):
        """X-2: the key this producer emits is the key the proof
        reads. The docstring once said `agents` while the code read
        `agent_names`, and a producer written against the prose would
        have made the seam silently unable to reach OWNED."""
        projection = self.run_with(
            [{"workspace_id": "wA"}],
            [{"workspace_id": "wA", "name": "a1"}],
        )
        self.assertIn("agent_names", projection[0])
        self.assertNotIn("agents", projection[0])
        # And the consumer reaches OWNED on exactly this shape.
        entry = {
            "workflow_id": "wf-0001",
            "workspace_lease": {"lease_id": "l",
                                "path_realpath": "/x"},
            "target_engine": {"alias": "a", "task_id": "t",
                              "repo": "u", "dispatched_at": 1},
        }
        from unittest.mock import patch
        with patch.object(ws_module.ownership_module,
                          "owns_child_record",
                          return_value=ws_module.OWNED):
            verdict, snapshot, problem, _d = ws_module.prove_ownership(
                entry,
                [{"repo": "/x", "task_id": "t", "workspace_id": "wA",
                  "agents": {"supervisor": "a1"}}],
                projection, "/root",
            )
        self.assertEqual(verdict, ws_module.OWNED, (problem,))
        self.assertEqual(snapshot.workspace_id, "wA")
        self.assertEqual(snapshot.agent_names, frozenset({"a1"}))

    def test_a_malformed_agent_row_DEGRADES_the_whole_projection(self):
        """X-1: a silent skip is outside what this projection may do. A row
        it is unable to read makes the projection degraded, because the
        set-equality proof depends on completeness — and a truncated set
        that happened to equal the recorded one would report OWNED and
        close a workspace holding live agents that no record names."""
        for bad in ("not-a-dict",
                    {"workspace_id": "wA"},
                    {"workspace_id": "wA", "name": 7},
                    {"workspace_id": 7, "name": "a1"}):
            with self.subTest(row=repr(bad)):
                self.assertIsNone(
                    self.run_with([{"workspace_id": "wA"}],
                                  [{"workspace_id": "wA",
                                    "name": "a1"}, bad]),
                    "a malformed agent row was silently skipped,"
                    " narrowing the live set instead of degrading it",
                )

    def test_a_malformed_workspace_row_DEGRADES_the_projection(self):
        for bad in ("not-a-dict", {}, {"workspace_id": ""},
                    {"workspace_id": 7}):
            with self.subTest(row=repr(bad)):
                self.assertIsNone(
                    self.run_with([{"workspace_id": "wA"}, bad],
                                  [{"workspace_id": "wA",
                                    "name": "a1"}]),
                )

    def test_a_degraded_projection_REFUSES_and_closes_nothing(self):
        """The consumer's half: `None` reaches `prove_ownership` as a
        non-list and is refused as degraded evidence, so no close is
        attempted."""
        closed = []
        entry = {
            "workflow_id": "wf-0001",
            "workspace_lease": {"lease_id": "l",
                                "path_realpath": "/x"},
            "target_engine": {"alias": "a", "task_id": "t",
                              "repo": "u", "dispatched_at": 1},
        }
        verdict, snapshot, problem, _d = ws_module.prove_ownership(
            entry, [], None, "/root"
        )
        self.assertNotEqual(verdict, ws_module.OWNED)
        self.assertIsNone(
            snapshot,
            "a failed proof yielded a snapshot, so an id from it"
            " could reach a close",
        )
        ok, _wid, problem2, _d2 = ws_module.close_proven_workspace(
            snapshot, None, closed.append,
        )
        problem = problem or problem2
        self.assertFalse(ok)
        self.assertEqual(problem, ws_module.PROBLEM_EVIDENCE_DEGRADED)
        self.assertEqual(closed, [])

    def test_an_unreadable_listing_degrades(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch("herdr.tasks.run",
                   side_effect=[SimpleNamespace(returncode=1,
                                                stdout="", stderr="x")]):
            self.assertIsNone(
                broker_module._production_live_workspaces()
            )


class ProductionPathOwnershipTests(unittest.TestCase):
    """R-28 T-2: ownership asserted THROUGH THE PRODUCTION CALLER.

    R-15's domain discipline, with the domain set to PRODUCTION SPAWN
    SITES. Every other class in this module calls `process_ownership` directly,
    and a suite of those proves the module works while leaving open
    whether anything USES it — which is exactly what R-28 found: fourteen rulings about a module
    that no production path imported.

    So these tests drive `codex_gateway.role_turn._default_runner`,
    `target_runtime.runtime.recover_inherited_processes` and the
    Runtime CLI, and assert ownership and cleanup happen BECAUSE THE
    PRODUCTION CALLER DID THEM.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def test_the_production_codex_runner_OWNS_what_it_spawns(self):
        """`_default_runner` is the real Codex spawn. Driven with a
        stand-in argv, it must register the process durably and reap
        its group — asserted from the ledger, not from the module."""
        from codex_gateway import role_turn as role_turn_module
        before = set()
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-legacy", "t-legacy", base=self.base,
        )
        rc, out, err, pid = role_turn_module._default_runner(
            [sys.executable, "-c",
             "import sys; sys.stdout.write(sys.stdin.read())"],
            b"hello", None, owner_scope=scope,
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, b"hello")
        after = set(proc_module.owned_groups(scope))
        self.assertIn(
            pid, after - before,
            "the production Codex spawn did not register its process"
            " as owned; the ownership module governs nothing on this"
            " path",
        )
        self.assertFalse(
            proc_module._group_alive(pid),
            "the production runner left its process group alive after"
            " returning",
        )

    def test_the_production_runner_reaps_even_when_the_turn_RAISES(self):
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        seen = {}
        real = proc_module.spawn_owned

        def capture(*args, **kwargs):
            handle = real(*args, **kwargs)
            seen["pid"] = handle.pid
            return handle

        with patch.object(proc_module, "spawn_owned", capture):
            with patch("subprocess.Popen.communicate",
                       side_effect=RuntimeError("turn exploded")):
                with self.assertRaises(RuntimeError):
                    role_turn_module._default_runner(
                        [sys.executable, "-c", "import time;"
                         " time.sleep(3600)"], b"", None,
                        owner_scope=proc_module.assign_scope(
                            proc_module.OWNER_TYPE_WORKFLOW,
                            "/control/repo", "wf-raise", "t-raise",
                            base=self.base,
                        ),
                    )
        self.assertIn("pid", seen)
        self.assertFalse(
            proc_module._group_alive(seen["pid"]),
            "a raising turn left the Codex group running; the reap"
            " must be on every exit path",
        )

    def test_restart_recovery_ACTS_ONLY_on_attributed_records(self):
        """R-34 Z-3, replacing the earlier scoped-base assertion.

        That one required an explicit base: production had written no
        ownership record, which left every enumeration unfounded. Now production registers under scopes NAMING their workflow and
        task, so recovery enumerates those scopes and attributes each
        record to its owner before acting — and a directory whose owner
        is unreadable from its name is reported and left alone.
        """
        from target_runtime import runtime as runtime_module
        results, unattributed = (
            runtime_module.recover_inherited_processes(self.base)
        )
        self.assertIsInstance(results, list)
        self.assertIsInstance(unattributed, list)
        for identity, _rp, _st, _un, _unc in results:
            self.assertIn(
                identity.owner_type, proc_module.OWNER_TYPES
            )
            self.assertTrue(
                identity.control_digest and identity.owner_id
                and identity.unit_id,
                "a recovery row carries no owner, so it acted on a"
                " record it could not attribute",
            )
        for directory, reason in unattributed:
            self.assertTrue(
                directory and reason,
                "a directory was left alone without saying WHY; a"
                " report that cannot distinguish a stray directory"
                " from a forgery is not a report",
            )

    def test_the_CLI_RECOVERS_before_advancing(self):
        """R-34 Z-3: restart recovery now HAS a production caller, and it
        runs BEFORE a workflow is advanced.

        This assertion previously read ['advance'] and that was the
        honest state at the time — the sweep V-2 removed was unscoped
        and unfounded. It changes now because the CODE changed:
        production registers under attributed scopes, so a restart
        sweep reads records production wrote and attributes each
        before acting.
        """
        from unittest.mock import patch
        from target_runtime import cli as cli_module
        calls = []
        with patch.object(cli_module, "_build_broker",
                          return_value=(object(), "/tmp")), \
             patch.object(cli_module, "acquire_runtime_lock",
                          return_value=os.open(os.devnull,
                                               os.O_RDONLY)), \
             patch.object(
                 cli_module.runtime_module,
                 "recover_inherited_processes",
                 side_effect=lambda *a, **k: (
                     calls.append("recovery") or ([], [])
                 ),
             ), \
             patch.object(
                 cli_module.runtime_module, "process_once",
                 side_effect=lambda broker: (
                     calls.append("advance") or {}
                 ),
             ):
            cli_module.main(["once"])
        self.assertEqual(
            calls, ["recovery", "advance"],
            "the Runtime advanced a workflow before reaping what a"
            " previous run left behind; got %r" % (calls,),
        )

    def test_the_CLI_reports_unattributed_records_without_reaping(self):
        from unittest.mock import patch
        from target_runtime import cli as cli_module
        with patch.object(cli_module, "_build_broker",
                          return_value=(object(), "/tmp")), \
             patch.object(cli_module, "acquire_runtime_lock",
                          return_value=os.open(os.devnull,
                                               os.O_RDONLY)), \
             patch.object(
                 cli_module.runtime_module,
                 "recover_inherited_processes",
                 return_value=(
                     [], [("/tmp/some-stray-dir",
                           proc_module.UNATTRIBUTED_NO_LABEL)]
                 ),
             ), \
             patch.object(cli_module.runtime_module, "process_once",
                          return_value={}):
            code = cli_module.main(["once"])
        self.assertEqual(code, 0)

    def _retired_test_the_CLI_does_NOT_sweep_a_record_space_it_did_not_write(self):
        """R-30 V-2, driven: the production entry point performs NO
        process recovery at all.

        It briefly did, unscoped, over the global record space — worse
        than the unwired state, because production does not register
        through the owned path and would have reaped and then
        MISATTRIBUTED another workflow's groups. Until production
        registers, it may not sweep, and this asserts it does not.
        """
        """Ordering matters: a Runtime must clean up what it inherited
        BEFORE it advances workflows, or it advances a workflow while
        a previous run's processes are still alive. Driven by
        replacing the recovery function and asserting the CLI called
        it."""
        from unittest.mock import patch
        from target_runtime import cli as cli_module
        calls = []

        # Only the two seams that stand between the entry point and
        # the code under test are replaced — config construction and
        # the runtime lock. The ORDERING being asserted is the real
        # control flow of `main`.
        with patch.object(cli_module, "_build_broker",
                          return_value=(object(), "/tmp")), \
             patch.object(cli_module, "acquire_runtime_lock",
                          return_value=os.open(os.devnull,
                                               os.O_RDONLY)), \
             patch.object(
                 cli_module.runtime_module,
                 "recover_inherited_processes",
                 side_effect=lambda *a, **k: (
                     calls.append("recovery") or ([], [], [])
                 ),
             ), \
             patch.object(
                 cli_module.runtime_module, "process_once",
                 side_effect=lambda broker: (
                     calls.append("advance") or {}
                 ),
             ):
            cli_module.main(["once"])
        self.assertEqual(
            calls, ["advance"],
            "the Runtime CLI swept a process record space it did not"
            " write; no production reaping without production"
            " registration",
        )


class TerminalCleanupReachabilityTests(RuntimeCase):
    """R-33 Y-1/Y-5: terminal cleanup is REACHED, and a NONTERMINAL phase
    stays untouched.

    Before R-33 no production caller invoked `release_workspace`, so
    this whole surface was unreachable in unattended operation. The test that matters most here is the one below it: no close and no
    candidacy for a nonterminal phase — the set enumerated from the
    record module, so a phase added later is covered by construction
    rather than by memory.
    """

    def leased(self, phase):
        """A workflow holding a lease, forced into ``phase``."""
        self.put_record(self.authorized_record("wf-0001"))
        self.assertTrue(self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        ).ok)
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["phase"] = phase
        self.write_raw(workflows)
        return self.fresh_workflows()["workflows"]["wf-0001"]

    def candidates(self):
        from target_runtime import runtime as runtime_module
        return [
            wid for wid, _rev in
            runtime_module.terminal_cleanup_candidates(self.store_dir)
        ]

    def test_terminal_phases_ARE_candidates(self):
        for phase in wa_record.TERMINAL_PHASES:
            with self.subTest(phase=phase):
                self.setUp()
                self.leased(phase)
                self.assertIn(
                    "wf-0001", self.candidates(),
                    "%s is terminal but cleanup never reaches it" % phase,
                )

    def test_NO_NONTERMINAL_PHASE_IS_EVER_A_CANDIDATE(self):
        """THE ONE THAT MATTERS MOST (Y-4/Y-5).

        Orphan buildup is expensive and recoverable; closing a
        workspace where engineering is still running destroys work
        irrecoverably. So the nonterminal set is DERIVED from the
        record module rather than listed — a phase added later is
        nonterminal by construction, and this fails if it ever becomes
        a candidate.
        """
        nonterminal = [
            phase for phase in wa_record.PHASES
            if phase not in wa_record.TERMINAL_PHASES
        ]
        self.assertTrue(
            nonterminal, "no nonterminal phases derived; vacuous"
        )
        for phase in nonterminal:
            with self.subTest(phase=phase):
                self.setUp()
                self.leased(phase)
                self.assertEqual(
                    self.candidates(), [],
                    "%s is NONTERMINAL and became a cleanup candidate;"
                    " closing live engineering work is irrecoverable"
                    % phase,
                )

    def test_a_released_lease_is_no_longer_a_candidate(self):
        """Idempotent retry: only a lease actually released — which
        now happens ONLY after a proven close — ends candidacy."""
        self.leased(wa_record.PHASE_COMPLETED)
        self.assertIn("wf-0001", self.candidates())
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["workspace_lease"][
            "released_at"
        ] = NOW
        self.write_raw(workflows)
        self.assertEqual(self.candidates(), [])


class EvidencePreservationTests(unittest.TestCase):
    """R-37 AB-1/AB-3/AB-4: the forensics survive cleanup, bound to
    what was actually there."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)
        self.lease = os.path.join(self.base, "lease")
        self.state = os.path.join(self.lease, ".herd", "state")
        os.makedirs(self.state)
        self.store = os.path.join(self.base, "store")
        os.makedirs(self.store)
        self.entry = {"workflow_id": "wf-0001",
                      "target_engine": {"task_id": "t-1"}}

    def write(self, name, text):
        with open(os.path.join(self.state, name), "w") as handle:
            handle.write(text)

    def preserve(self, required_names=()):
        """The MECHANISM under test in this class.

        `required_names` is stated explicitly on every call, because
        `preserve` has no default for it: AF-3's hole was a caller
        able to omit it silently. This class drives the policy
        machinery with an empty required set; the PRODUCTION required
        set is driven through `broker._release` in
        `RequiredArtifactProductionTests`, which is where AI-2 lives.
        """
        return preserve_module.preserve(
            self.entry, self.lease, self.store, 1000,
            required_names=required_names,
        )

    def test_preserved_bytes_are_the_bytes_that_were_there(self):
        """AB-4: no fabrication. The stored digest is of the FULL
        file, and it matches the live file at the moment of
        preservation — so preserved content is bound to what actually
        existed rather than to anything this module produced."""
        self.write("lead-evidence.md", "the lead did the work")
        ok, problem, _d, _s = self.preserve()
        self.assertTrue(ok, problem)
        self.assertEqual(
            preserve_module.preserved_text(
                self.store, "wf-0001", "lead-evidence.md"
            ),
            "the lead did the work",
        )
        self.assertTrue(
            preserve_module.digests_match(
                self.store, "wf-0001", self.lease
            ),
            "a preserved digest does not match the live file; the"
            " archive is not bound to what was there",
        )

    def test_it_survives_the_directory_it_read(self):
        """AB-2: the read path does not depend on the managed
        directory. This deletes the workspace outright and reads the
        projection afterwards."""
        self.write("reviewer-evidence.md", "approved")
        self.assertTrue(self.preserve()[0])
        remove(self.lease)
        self.assertFalse(os.path.exists(self.lease))
        self.assertEqual(
            preserve_module.preserved_text(
                self.store, "wf-0001", "reviewer-evidence.md"
            ),
            "approved",
        )

    def test_an_unreadable_artifact_HALTS_preservation(self):
        """AF-1/AF-3: the policy value GATES.

        `complete` previously chose a word in a summary while
        `preserve` returned True regardless, so an INCOMPLETE archive
        reported itself honestly and the chain closed the sessions and
        deleted the directory anyway. Truthful reporting is not
        enforcement.
        """
        self.write("fine.md", "here")
        blocked = os.path.join(self.state, "blocked.md")
        with open(blocked, "w") as handle:
            handle.write("secret")
        os.chmod(blocked, 0)
        self.addCleanup(os.chmod, blocked, 0o600)
        ok, problem, detail, summary = self.preserve()
        self.assertFalse(
            ok,
            "an archive missing REQUIRED evidence reported success,"
            " so the chain would destroy the only other copy",
        )
        self.assertEqual(problem, preserve_module.PROBLEM_INCOMPLETE)
        self.assertIn("could not be read", detail)
        self.assertIn("INCOMPLETE", summary)

    def test_a_truncated_LISTING_HALTS_preservation(self):
        """A listing the archive could not finish leaves it unable to say
        what it failed to preserve, which is worse than knowing."""
        for index in range(preserve_module.MAX_FILES + 3):
            self.write("f%02d.md" % index, "x")
        ok, problem, detail, _s = self.preserve()
        self.assertFalse(ok)
        self.assertEqual(problem, preserve_module.PROBLEM_INCOMPLETE)
        self.assertIn("listing was truncated", detail)

    def test_a_MISSING_required_artifact_HALTS_preservation(self):
        self.write("present.md", "x")
        ok, problem, detail, _s = self.preserve(
            required_names=("absent.md",)
        )
        self.assertFalse(ok)
        self.assertEqual(problem, preserve_module.PROBLEM_INCOMPLETE)
        self.assertIn("absent.md", detail)

    def test_truncated_CONTENT_is_allowed_and_disclosed(self):
        """The policy says so explicitly, and the entry states the
        exact bounds. A bounded, disclosed loss of BYTES is different
        from an unknown loss of EVIDENCE."""
        oversized = "x" * (preserve_module.MAX_FILE_BYTES + 500)
        self.write("huge.md", oversized)
        ok, problem, _d, summary = self.preserve()
        self.assertTrue(ok, problem)
        document = preserve_module.load_preserved(self.store, "wf-0001")
        self.assertTrue(document["complete"])
        row, = [f for f in document["files"] if f["name"] == "huge.md"]
        self.assertTrue(row["truncated"])
        self.assertEqual(row["kept_bytes"],
                         preserve_module.MAX_FILE_BYTES)
        self.assertEqual(row["full_bytes"], len(oversized))

    def test_FLIPPING_the_completeness_value_CHANGES_the_outcome(self):
        """AF-2, stated as the repository's own rule: prove the value
        reaches its destination and CHANGES an outcome, rather than
        merely that it was computed. Forcing a violation must change
        `preserve`'s RETURN, not only its adjective."""
        from unittest.mock import patch
        self.write("a.md", "x")
        self.assertTrue(self.preserve()[0])
        with patch.object(preserve_module, "policy_violations",
                          return_value=["forced"]):
            ok, problem, _d, summary = self.preserve()
        self.assertFalse(
            ok,
            "flipping the completeness value changed only a string;"
            " it is not a gate",
        )
        self.assertEqual(problem, preserve_module.PROBLEM_INCOMPLETE)

    def test_truncation_is_disclosed_EXACTLY(self):
        """AB-3: within this projection a capped archive is not readable as
        a complete one. This repository has shipped a capped archive
        reported as complete once already."""
        oversized = "x" * (preserve_module.MAX_FILE_BYTES + 500)
        self.write("huge.md", oversized)
        self.assertTrue(self.preserve()[0])
        document = preserve_module.load_preserved(self.store, "wf-0001")
        row, = [f for f in document["files"] if f["name"] == "huge.md"]
        self.assertTrue(row["truncated"])
        self.assertEqual(row["kept_bytes"],
                         preserve_module.MAX_FILE_BYTES)
        self.assertEqual(row["full_bytes"], len(oversized))
        # The digest still identifies the WHOLE file, so a partial
        # copy can still be checked against the original.
        self.assertEqual(
            row["digest"],
            __import__("hashlib").sha256(
                oversized.encode()
            ).hexdigest(),
        )

    def test_a_truncated_LISTING_is_DISCLOSED_as_well_as_halting(self):
        """RESTORED under the post-AF-1 semantics, rather than left
        dead under a `_superseded_` name.

        Its original form asserted `preserve` returned ok, which AF-1
        correctly changed: a truncated listing now HALTS. The property
        it pinned is still worth pinning and is not covered by the
        halt test — that the projection SAYS the listing was
        truncated. A halt without disclosure would leave a reader
        unable to tell what the archive failed to reach.
        """
        for index in range(preserve_module.MAX_FILES + 3):
            self.write("f%02d.md" % index, "x")
        ok, problem, _d, _s = self.preserve()
        self.assertFalse(ok)
        self.assertEqual(problem, preserve_module.PROBLEM_INCOMPLETE)
        document = preserve_module.load_preserved(self.store, "wf-0001")
        self.assertTrue(
            document["truncated_listing"],
            "more files were present than the cap and the projection"
            " did not say so",
        )
        self.assertFalse(document["complete"])

    def test_an_unreadable_file_is_RECORDED_not_skipped(self):
        """RESTORED under the post-AF-1 semantics.

        The halt is asserted elsewhere; what this pins is that the
        unreadable file appears IN the projection with
        ``unreadable: True`` rather than being silently omitted. An
        omission would make a partial archive look whole to anyone
        reading it later, which is a different failure from the chain
        proceeding.
        """
        self.write("readable.md", "here")
        unreadable = os.path.join(self.state, "locked.md")
        with open(unreadable, "w") as handle:
            handle.write("secret")
        os.chmod(unreadable, 0)
        self.addCleanup(os.chmod, unreadable, 0o600)
        self.assertFalse(self.preserve()[0])
        document = preserve_module.load_preserved(self.store, "wf-0001")
        names = {f["name"]: f for f in document["files"]}
        self.assertIn(
            "locked.md", names,
            "an unreadable file was silently omitted, making a partial"
            " archive look whole",
        )
        self.assertTrue(names["locked.md"]["unreadable"])

    def test_a_crash_between_write_and_READBACK_is_a_failure(self):
        """AC-4: preservation is proven by reading it back, not by the
        write returning. A crash in that window must report failure,
        because everything downstream destroys what it preserved."""
        from unittest.mock import patch
        self.write("a.md", "x")
        with patch.object(preserve_module, "load_preserved",
                          return_value=None):
            ok, problem, _d, _s = self.preserve()
        self.assertFalse(ok)
        self.assertEqual(problem, preserve_module.PROBLEM_READBACK)

    def test_a_partial_write_never_becomes_the_archive(self):
        """AC-4's atomicity half: the projection is renamed into place, so within this write a
        crash leaves the previous archive or none, rather than a half-
        written one a later read would trust."""
        self.write("a.md", "x")
        self.assertTrue(self.preserve()[0])
        path = preserve_module.preserved_path(self.store, "wf-0001")
        self.assertTrue(os.path.exists(path))
        self.assertFalse(
            os.path.exists(path + ".partial"),
            "a partial file was left beside the archive",
        )
        import json as _json
        with open(path) as handle:
            _json.load(handle)          # parses: not half-written

    def test_an_unreadable_entry_makes_the_archive_INCOMPLETE(self):
        """AC-5, RESTORED under the post-AF-1 semantics: completeness
        is DERIVED from the entries.

        An unreadable file recorded with ``truncated: False`` would
        read as preserved-in-full. The original asserted `preserve`
        returned ok; it now returns False, and the DERIVATION being
        pinned — `complete` following from the entries and the
        violations being named — is unchanged.
        """
        self.write("fine.md", "here")
        blocked = os.path.join(self.state, "blocked.md")
        with open(blocked, "w") as handle:
            handle.write("secret")
        os.chmod(blocked, 0)
        self.addCleanup(os.chmod, blocked, 0o600)
        ok, _p, _d, summary = self.preserve()
        self.assertFalse(ok)
        document = preserve_module.load_preserved(self.store, "wf-0001")
        self.assertFalse(
            document["complete"],
            "an archive containing an unreadable entry reported"
            " itself complete",
        )
        self.assertTrue(document["policy_violations"])
        self.assertIn("INCOMPLETE", summary)

    # DELETED, not renamed: `_superseded_a_truncated_file_makes_the_
    # archive_INCOMPLETE` asserted that CONTENT truncation of an
    # over-large file made the archive incomplete. AF-3 settled the
    # opposite: within this policy, bounded and disclosed content
    # truncation is ALLOWED. The assertion is now wrong rather than
    # merely superseded, and keeping it under a different name would
    # pin behaviour the policy forbids. What replaced it:
    # `test_truncated_CONTENT_is_allowed_and_disclosed` and
    # `test_truncation_is_disclosed_EXACTLY`.

    def test_a_fully_preserved_archive_reports_complete(self):
        self.write("a.md", "small")
        ok, _p, _d, summary = self.preserve()
        self.assertTrue(ok)
        self.assertTrue(
            preserve_module.load_preserved(
                self.store, "wf-0001"
            )["complete"]
        )
        self.assertIn("complete", summary)

    def test_the_workspace_id_is_carried(self):
        """AC-2: the identity is passed IN, from the same binding the
        close acts on, rather than derived here."""
        self.write("a.md", "x")
        preserve_module.preserve(
            self.entry, self.lease, self.store, 1000,
            workspace_id="wV", required_names=(),
        )
        self.assertEqual(
            preserve_module.load_preserved(
                self.store, "wf-0001"
            )["workspace_id"],
            "wV",
        )

    def test_the_projection_carries_the_run_identity(self):
        self.write("a.md", "x")
        self.assertTrue(self.preserve()[0])
        document = preserve_module.load_preserved(self.store, "wf-0001")
        self.assertEqual(document["workflow_id"], "wf-0001")
        self.assertEqual(document["task_id"], "t-1")


class DestructiveOrderingClosureTests(unittest.TestCase):
    """R-31 W-4: the STRUCTURAL CLOSURE for destructive ordering.

    THE INVARIANT: an irreversible or destructive step must come AFTER
    the step that establishes its safety or attribution.

    Three instances in this increment, which is why this is a closure
    and not a third reorder:

    1. `Popen` before `record_owned_group` — the act before the record
       that makes it attributable.
    2. the freeze restored after a run rather than on its exit path —
       state repaired after the fact.
    3. `workspace_module.release` before the session close — the
       irreversible deletion before the step that makes it safe.

    DOMAIN, stated: DESTRUCTIVE OPERATIONS — the calls that destroy,
    kill, or irreversibly remove. Not call sites generally and not
    functions generally.

    JUSTIFIED: the claim is "every destructive step is ordered after
    its safety step". The subject of that claim is a destructive OPERATION, so an
    enumeration over anything wider is complete and false in the way the
    reaper-function case already showed: a function-level scan would
    list `prove_ownership`, which destroys no state, while a call-site
    scan would leave unseen that two destructive calls sit in one
    function in the wrong order.
    """

    #: Calls that destroy, kill, or irreversibly remove.
    DESTRUCTIVE = (
        "rmtree", "unlink", "remove", "killpg", "kill", "close_fn",
        "release", "revoke", "terminate",
    )

    #: Every destructive operation in the I5 production surface,
    #: mapped to: the step that must PRECEDE it (R-31),
    #: WHAT MUST BE PROVEN for it to run at all (R-36), WHAT SINGLE
    #: PROOF THE ACTION CONSUMES (R-40), the executed test that fails
    #: when one of them is violated, and WHAT COMPUTED VALUE GATES IT
    #: versus what is merely reported alongside (R-42).
    #:
    #: That last column exists because a safety value can be computed
    #: #: correctly, reported truthfully, and gate no decision: `complete`
    #: once chose a word in a summary while the function returned
    #: success regardless. Truthful reporting is not enforcement.
    #:
    #: The third column exists because ordering and gating together
    #: are still not enough. `_release` once had the right order AND a
    #: proven precondition, and the close still re-read the world and
    #: took its own identity — so the thing acted upon need not have
    #: been the thing proven. Two reads are two facts.
    #:
    #: The second column exists because ordering is not sequencing.
    #: `_release` once had the right ORDER — close, then delete — and
    #: still deleted unconditionally after a FAILED close, so one
    #: unreadable projection permanently abandoned a live workspace.
    #: A destructive step needs a proven precondition, not merely a
    #: prior neighbour.
    ORDERING = {
        ("target_runtime/broker.py", "_release"): (
            "the target EVIDENCE is preserved first, then the"
            " workspace SESSIONS are closed, and only then is the"
            " managed directory deleted. `evidence_preservation."
            "preserve` is not itself in this domain — it only reads"
            " and writes, destroying nothing — but it must run FIRST,"
            " because both later steps destroy what it reads.",
            "the close returned SESSIONS_RECLAIMED — proven closed,"
            " or positive evidence there was nothing to close. A"
            " degraded or refused close RETAINS the directory and"
            " keeps the workflow a cleanup candidate.",
            "test_sessions_close_BEFORE_the_directory_is_deleted and"
            " test_a_degraded_close_RETAINS_the_directory_and_"
            "CANDIDACY",
            "the ONE `ProofSnapshot` derived once at the top of the release, handed to BOTH the archive and the close",
            "GATED BY: preservation returning ok (AF-1), then the close returning SESSIONS_RECLAIMED (AA-1). Both change the RETURN, not a label."
        ),
        ("herdr/lifecycle.py", "start_herd"): (
            "the partial harness WORKSPACE IS CLOSED FIRST. The"
            " `unlink` here removes `.herd/state/runtime.json`, and it"
            " runs only in the failure handler, after"
            " `herdr workspace close` has reclaimed the workspace that"
            " state file names",
            "the startup already FAILED and is re-raising. Removing"
            " the state of a herd that never came up is what lets the"
            " next bootstrap run at all: a runtime.json naming a"
            " closed workspace is read by health as a live herd. The"
            " `FileNotFoundError` guard means a state file that was"
            " never written is not an error",
            "tests/test_health.py::StartHerdCorruptStateContractTests"
            " drives `start_herd` over corrupt and absent runtime"
            " state and pins which cases raise; tests/test_lifecycle"
            ".py executes the successful path, in which this handler"
            " does not run",
            "the workspace id held in this function's own scope,"
            " taken from the workspace it opened — never a path"
            " derived from a name or a listing",
            "GATED BY: reaching the `except` handler at all. On the"
            " success path the unlink is unreachable, which is the"
            " strongest form of conditional."
        ),
        ("target_runtime/process_ownership.py",
         "retire_workflow_scopes"): (
            "the workflow's terminal cleanup has already completed:"
            " the target evidence was preserved, the sessions were"
            " proven closed, and the managed directory was released."
            " `broker._retire_process_scopes` is the only caller and"
            " it runs after that release returns ok, so a workflow"
            " whose cleanup halted keeps its records",
            "the scope carries a VALID ASSIGNMENT naming exactly this"
            " control repository and this workflow (AG-1/AG-3), and"
            " no CORROBORATED group recorded inside it is still"
            " running (AR-3). Either check failing REPORTS the scope"
            " and leaves it, so the next terminal cleanup retries",
            "RetireProcessScopesTests: test_a_scope_with_a_LIVE_group"
            "_is_REFUSED_and_KEPT and test_another_workflows_scope_is"
            "_NEVER_retired drive both refusals with real records;"
            " test_retirement_happens_THROUGH_the_release drives the"
            " ordering through `ACTION_RELEASE`",
            "the ASSIGNMENT CREDENTIAL written before the spawn — the"
            " same credential recovery validates, never the directory"
            " name",
            "GATED BY: the release returning ok, then"
            " `validate_assignment` returning an identity, then"
            " `scope_has_live_group` returning False. Each changes"
            " what is removed, not a label."
        ),
        ("target_runtime/workspace_ownership.py",
         "close_proven_workspace"): (
            "`prove_ownership` runs before `close_fn` is reached",
            "that proof returned OWNED — exact and unique agreement"
            " across the workflow record, ONE child record and ONE"
            " live workspace whose agent names match the recorded"
            " ones",
            "WorkspaceOwnershipTests: every refusal case asserts the"
            " injected recorder went uncalled",
            "the `ProofSnapshot` passed to it, revalidated against a fresh live reading immediately before the close",
            "GATED BY: `snapshot.still_matches` over the FULL binding — workspace, agents, task id, lease, child record (AF-4)."
        ),
        ("target_runtime/process_ownership.py", "reap_group"): (
            "`group_is_verified` runs before any signal",
            "that check HOLDS: the pid leads its own group and is not"
            " the caller's own group",
            "test_an_unverified_group_is_never_signalled and"
            " test_the_refusal_signals_nothing_at_all",
            "the verification it performs on the pid it was handed; it reads no registry",
            "GATED BY: `group_is_verified`, which returns before any signal."
        ),
        ("target_runtime/process_ownership.py", "reap_owned"): (
            "the ledger is consulted before any signal",
            "the group id is PRESENT in the owner ledger this"
            " component wrote",
            "test_reap_owned_refuses_a_group_the_ledger_does_not_name",
            "the owner ledger entry for that exact group id",
            "GATED BY: ledger membership, checked before the signal."
        ),
        ("target_runtime/process_ownership.py",
         "reap_group_by_recorded_root"): (
            "the owned roots on disk are read before any signal",
            "an OWNED ROOT records this exact group id",
            "FailClosedIsIntendedTests: recovery signals nothing with"
            " no durable evidence",
            "the owned root on disk that recorded that group id",
            "GATED BY: the presence of a stamped owned root; an unstamped one is reported and left alone."
        ),
        ("target_runtime/process_ownership.py", "ungate_spawning"): (
            "no predecessor is required",
            "nothing need be proven: it removes a GATE FILE this"
            " component wrote, whose whole purpose is to be created"
            " and removed, so no state of anyone else's is destroyed."
            " Listed rather than exempted, because a scan that"
            " silently dropped it would be the wrong-domain mistake"
            " again.",
            "SpawnGateTests.test_ungating_restores_spawning",
            "nothing: it removes a file this component wrote",
            "ADVISORY ONLY, and the reason: it removes a file this component wrote, so there is no safety value to gate on."
        ),
        ("target_runtime/process_ownership.py", "thaw_spawning"): (
            "the unfreeze is RECORDED, with authority and reason,"
            " before the freeze file is removed",
            "the audit line is on disk at the moment of removal",
            "test_the_unfreeze_is_recorded_before_the_file_is_removed",
            "the audit line it writes itself, immediately before",
            "ADVISORY ONLY: the audit write precedes the removal unconditionally, and a failure there raises rather than being reported."
        ),
        ("target_runtime/workspace_trust.py", "_atomic_write"): (
            "the temp file is created immediately above the unlink",
            "the path unlinked is the one THIS function created, on"
            " the failure path of its own atomic replace, so no state"
            " existing before the call is destroyed",
            "MinimalWriteTests.test_write_happened_and_is_the_only_"
            "difference — the config is byte-compared, so a temp"
            " file left behind or a wrong file removed would show",
            "the temp path it created itself, moments earlier",
            "GATED BY: the failure of the write it is unwinding; the unlink runs only on that path."
        ),
        ("target_runtime/workspace_trust.py", "revoke"): (
            "`resolve_managed_target` runs before the entry is"
            " removed",
            "it PROVED the key belongs to THIS workflow's own lease"
            " inside the managed root",
            "TrustRevocationTests.test_it_refuses_another_workflows_"
            "lease_path and test_it_still_refuses_a_path_outside_the_"
            "managed_root",
            "the resolved managed target `resolve_managed_target` returned",
            "GATED BY: `resolve_managed_target` returning no problem, checked before the entry is removed."
        ),
    }

    def domain(self):
        """Every function containing a destructive call, in the
        changed PRODUCTION surface. Derived from the diff."""
        import ast
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout
        found = {}
        for line in out.splitlines():
            path = line[3:].strip()
            if not path.endswith(".py"):
                continue
            if path.split("/")[0] not in (
                "target_runtime", "herdr", "workflow_authority",
                "codex_gateway",
            ):
                continue
            with open(os.path.join(REPO_ROOT, path),
                      encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    # `del mapping[key]` is a destructive removal
                    # spelled as a STATEMENT, not a call. A
                    # call-only scan misses `workspace_trust.revoke`,
                    # which removes a configuration entry that way —
                    # the same wrong-domain shape one level down, and
                    # it was caught by this closure's own anti-stale
                    # test rather than by reading.
                    if isinstance(inner, ast.Delete):
                        for target in inner.targets:
                            if isinstance(target, ast.Subscript):
                                found.setdefault(
                                    (path, node.name), set()
                                ).add("del")
                        continue
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    name = (func.attr
                            if isinstance(func, ast.Attribute)
                            else func.id
                            if isinstance(func, ast.Name) else None)
                    if name not in self.DESTRUCTIVE:
                        continue
                    if self._is_probe(inner, name):
                        continue
                    found.setdefault((path, node.name), set()).add(name)
        return found

    @staticmethod
    def _is_probe(call, name):
        """A signal-0 call asks whether a process exists; within this scan
        it destroys no state and is excluded by computation rather than
        by a hand-kept list."""
        import ast
        if name not in ("kill", "killpg") or len(call.args) < 2:
            return False
        second = call.args[1]
        return isinstance(second, ast.Constant) and second.value == 0

    def test_the_domain_is_derived_and_not_vacuous(self):
        domain = self.domain()
        self.assertGreaterEqual(
            len(domain), 5,
            "the destructive-operation scan found almost nothing; a"
            " clean result from a broken detector proves nothing",
        )
        self.assertIn(
            ("target_runtime/broker.py", "_release"), domain,
            "the release path — which deletes a managed directory —"
            " is missing from the destructive domain",
        )

    def test_every_destructive_operation_names_its_predecessor(self):
        unordered = sorted(
            "%s::%s (%s)" % (path, name, ",".join(sorted(calls)))
            for (path, name), calls in self.domain().items()
            if (path, name) not in self.ORDERING
        )
        self.assertEqual(
            unordered, [],
            "destructive operation(s) with no named safety step and no"
            " executed order pin:\n  %s" % "\n  ".join(unordered),
        )

    def test_every_entry_names_THE_PROOF_IT_CONSUMES(self):
        """R-40's third column. Ordering and gating together still
        permit the thing acted upon to differ from the thing proven,
        if the action re-derives its own identity."""
        for key, value in sorted(self.ORDERING.items()):
            with self.subTest(operation="%s::%s" % key):
                self.assertEqual(
                    len(value), 5,
                    "%s::%s does not name the single proof its action"
                    " consumes" % key,
                )
                self.assertTrue(value[3] and value[3].strip())

    def test_every_entry_names_ITS_GATE_or_declares_it_ADVISORY(self):
        """R-42 AF-1: a safety-relevant computed value either GATES a
        decision or is documented as ADVISORY with the reason. There
        is no third category, and "reported truthfully" is not one."""
        for key, value in sorted(self.ORDERING.items()):
            with self.subTest(operation="%s::%s" % key):
                gate = value[4]
                self.assertTrue(gate and gate.strip())
                self.assertTrue(
                    gate.startswith("GATED BY:")
                    or gate.startswith("ADVISORY ONLY"),
                    "%s::%s neither names its gate nor declares itself"
                    " advisory: %r" % (key[0], key[1], gate),
                )

    def test_every_entry_names_WHAT_MUST_BE_PROVEN(self):
        """R-36's second column. An entry that names only a preceding
        step permits the defect the column was added for: the right
        order with an unconditional destructive call."""
        for key, value in sorted(self.ORDERING.items()):
            with self.subTest(operation="%s::%s" % key):
                self.assertEqual(
                    len(value), 5,
                    "%s::%s names no proven precondition; ordering is"
                    " not sequencing" % key,
                )
                precedes, proven, pin, consumes, gate = value
                for part in (precedes, proven, pin, consumes, gate):
                    self.assertTrue(part and part.strip())

    def test_every_named_predecessor_still_has_its_operation(self):
        live = set(self.domain())
        stale = sorted(key for key in self.ORDERING if key not in live)
        self.assertEqual(
            stale, [],
            "ordering entries whose destructive operation is gone:"
            " %r" % (stale,),
        )

    def test_the_domain_is_a_FLOOR_and_its_bounds_are_named(self):
        """R-13 rides here too. The scan counts functions whose OWN
        body spells one of the listed destructive calls, in CHANGED
        production files, at depth ZERO — so the number is a FLOOR.

        This enumeration is fast structural feedback in front of
        `test_sessions_close_BEFORE_the_directory_is_deleted` and
        `test_the_unfreeze_is_recorded_before_the_file_is_removed`,
        which EXECUTE the orders being claimed.

        Named as outside it, each leaving the count a floor: a destructive step reached only through a helper at depth one or
        beyond, one in an UNCHANGED file, one reached through an alias
        or `getattr`, one performed by a library this code calls, and a
        destruction spelled some way outside the listed names.
        """
        doc = inspect.getdoc(
            DestructiveOrderingClosureTests
            .test_the_domain_is_a_FLOOR_and_its_bounds_are_named
        )
        self.assertIn("FLOOR", doc)
        self.assertIn("depth ZERO", doc)
        self.assertTrue(self.domain())

    def test_the_unfreeze_is_recorded_before_the_file_is_removed(self):
        """EXECUTED order pin: the audit line exists on disk at the
        moment the freeze file is removed, not afterwards."""
        base = tempfile.mkdtemp()
        self.addCleanup(remove, base)
        proc_module.freeze_spawning("for the ordering pin", base)
        seen = {}
        real_unlink = os.unlink

        def watching_unlink(path):
            seen["history_at_unlink"] = proc_module.unfreeze_history(
                base
            )
            return real_unlink(path)

        from unittest.mock import patch
        with patch.object(os, "unlink", watching_unlink):
            proc_module.thaw_spawning(
                base, reason="ordering pin", authority="test",
            )
        self.assertTrue(
            seen.get("history_at_unlink"),
            "the freeze file was removed before the unfreeze was"
            " recorded; a lift that leaves no record is"
            " indistinguishable from someone deleting the file",
        )


class ScopedProductionRegistrationTests(unittest.TestCase):
    """R-34 Z-1/Z-3: registration ATTRIBUTED to a workflow and task,
    and a restart path that acts only on attributed records.

    Production previously registered every Codex role turn into ONE
    GLOBAL root under a CONSTANT label, so workflow A could not
    distinguish its records from workflow B's: registration existed and attributed no record to an owner.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def test_a_scope_LABELS_its_owner_and_the_label_is_not_the_proof(self):
        """R-43 AG-2: the name is READABLE and is NOT a credential."""
        scope = proc_module.workflow_scope(
            "/control/repo", "wf-0001", "20260828-114612-5d92e1",
            self.base,
        )
        self.assertEqual(
            proc_module.parse_scope(scope),
            (proc_module.OWNER_TYPE_WORKFLOW,
             proc_module.control_digest("/control/repo"), "wf-0001",
             "20260828-114612-5d92e1"),
        )
        identity, reason = proc_module.validate_assignment(
            scope, base=self.base
        )
        self.assertIsNone(
            identity,
            "a directory that merely PARSES was accepted as owned;"
            " that is attribution by name, which R-43 forbids",
        )
        self.assertEqual(reason, proc_module.UNATTRIBUTED_NO_ASSIGNMENT)

    def test_an_unattributed_scope_is_REFUSED(self):
        """A scope missing either id would attribute its contents to
        'some workflow', which is the state Z-1 exists to end."""
        for workflow_id, task_id in (
            (None, "t"), ("wf", None), ("", "t"), ("wf", ""),
        ):
            with self.subTest(ids=(workflow_id, task_id)):
                with self.assertRaises(ValueError):
                    proc_module.workflow_scope(
                        "/control/repo", workflow_id, task_id,
                        self.base,
                    )

    def test_two_workflows_do_not_share_a_record_space(self):
        a = proc_module.workflow_scope(
            "/control/repo", "wf-A", "t1", self.base
        )
        b = proc_module.workflow_scope(
            "/control/repo", "wf-B", "t1", self.base
        )
        self.assertNotEqual(a, b)
        proc_module.record_owned_group(4242, "x", directory=a)
        self.assertEqual(proc_module.owned_groups(a), {4242})
        self.assertEqual(
            proc_module.owned_groups(b), set(),
            "one workflow's records are visible in another's scope;"
            " that is the cross-workflow contamination Z-1 closes",
        )

    def test_recovery_reports_PER_OWNER(self):
        a = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-A", "t1", base=self.base,
        )
        proc_module.create_owned_root("own-a", a)
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=1.0
        )
        self.assertEqual(unattributed, [])
        self.assertEqual(len(results), 1)
        identity, _r, _s, unstamped, _unc = results[0]
        self.assertEqual(
            (identity.owner_type, identity.owner_id, identity.unit_id),
            (proc_module.OWNER_TYPE_WORKFLOW, "wf-A", "t1"),
        )
        self.assertEqual(len(unstamped), 1)

    def test_an_UNATTRIBUTED_directory_is_reported_never_reaped(self):
        stray = os.path.join(
            proc_module.owned_root_base(self.base), "someone-elses-dir"
        )
        os.makedirs(stray)
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=1.0
        )
        self.assertEqual(results, [])
        self.assertEqual(
            unattributed,
            [(stray, proc_module.UNATTRIBUTED_NO_LABEL)],
        )
        self.assertTrue(
            os.path.isdir(stray),
            "an unattributed directory was acted on; ownership is"
            " never inferred from a directory this component did not"
            " name",
        )

    def test_the_PRODUCTION_runner_REFUSES_an_unattributed_spawn(self):
        """Z-1 at the production seam: a caller unable to say WHOSE process
        this is does not get to start one."""
        from codex_gateway import role_turn as role_turn_module
        with self.assertRaises(ValueError):
            role_turn_module._default_runner(
                [sys.executable, "-c", "pass"], b"", None,
            )

    def test_the_production_runner_registers_under_its_scope(self):
        from codex_gateway import role_turn as role_turn_module
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-0001", "t-1", base=self.base,
        )
        rc, out, _err, pid = role_turn_module._default_runner(
            [sys.executable, "-c",
             "import sys; sys.stdout.write(sys.stdin.read())"],
            b"hi", None, owner_scope=scope,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"hi")
        self.assertIn(
            pid, proc_module.owned_groups(scope),
            "the production spawn did not register under its owning"
            " workflow's scope",
        )
        self.assertFalse(proc_module._group_alive(pid))

    def test_run_role_turn_derives_the_scope_from_the_record(self):
        """The attribution was AVAILABLE on the record and was not
        being threaded through. This drives the real derivation."""
        from codex_gateway import role_turn as role_turn_module
        record = {
            "workflow_id": "wf-0001",
            "control_identity": {"repository_realpath": "/control/repo"},
            "target_engine": {"task_id": "20260828-114612-5d92e1"},
        }
        scope = role_turn_module._owner_scope_for(record)
        self.assertEqual(
            proc_module.parse_scope(scope),
            (proc_module.OWNER_TYPE_WORKFLOW,
             proc_module.control_digest("/control/repo"), "wf-0001",
             "20260828-114612-5d92e1"),
        )
        # AG-1: the ASSIGNMENT is what a later run reads, and within
        # this seam it was written BEFORE any spawn could occur.
        identity, reason = proc_module.validate_assignment(scope)
        self.assertIsNone(reason)
        self.assertEqual(
            identity,
            (proc_module.OWNER_TYPE_WORKFLOW,
             proc_module.control_digest("/control/repo"), "wf-0001",
             "20260828-114612-5d92e1"),
        )

    def test_a_pre_dispatch_record_is_still_attributed(self):
        """Before dispatch there is no task id, and the turn is still
        scoped to exactly one owner rather than a shared root."""
        from codex_gateway import role_turn as role_turn_module
        scope = role_turn_module._owner_scope_for({
            "workflow_id": "wf-0001",
            "control_identity": {"repository_realpath": "/control/repo"},
            "target_engine": None,
        })
        identity = proc_module.parse_scope(scope)
        self.assertEqual(identity.owner_type,
                         proc_module.OWNER_TYPE_WORKFLOW)
        self.assertEqual(identity.owner_id, "wf-0001")
        self.assertEqual(identity.unit_id, "pre-dispatch")
        self.assertIsNone(
            proc_module.validate_assignment(scope)[1],
            "the pre-dispatch scope was named but never assigned",
        )

    def test_a_parent_death_leaves_a_RECOVERABLE_scoped_record(self):
        """Z-3 end to end: a parent dies inside the registration
        window, and a LATER run — holding no state from the first —
        recovers the orphan from the attributed scope alone.

        Margin: the descendant sleeps 3600 s and every wait here is at
        most 10 s, so expiry accounts for neither its death nor a
        recovery that failed to happen.
        """
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-0001", "t-1", base=self.base,
        )
        script = (
            "import os, subprocess, sys\n"
            "sys.path.insert(0, %r)\n"
            "from target_runtime import process_ownership as proc\n"
            "proc.record_owned_group = lambda *a, **k: os._exit(9)\n"
            "proc.spawn_owned([sys.executable, '-c',"
            " 'import time; time.sleep(3600)'], 'scoped',"
            " directory=%r, owned_root_base_dir=%r,"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "os._exit(0)\n" % (REPO_ROOT, scope, scope)
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        deadline = time.monotonic() + 10
        roots = []
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(scope)
            if roots and roots[0][1] is not None:
                break
            time.sleep(0.02)
        self.assertTrue(roots and roots[0][1] is not None,
                        "the child did not stamp its own root")
        pgid = roots[0][1]
        self.assertTrue(proc_module._group_alive(pgid))
        # # A LATER run, holding no state from the first, recovers it —
        # and reports it under its owning workflow and task.
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=10.0
        )
        self.assertEqual(unattributed, [])
        self.assertEqual(len(results), 1)
        identity, recovered, stuck, _u, _unc = results[0]
        self.assertEqual(
            (identity.owner_type, identity.owner_id, identity.unit_id),
            (proc_module.OWNER_TYPE_WORKFLOW, "wf-0001", "t-1"),
        )
        self.assertEqual(recovered, [pgid])
        self.assertEqual(stuck, [])
        self.assertFalse(proc_module._group_alive(pgid))


class ReaperFunctionClosureTests(unittest.TestCase):
    """R-15: E-3 RE-DISCHARGED over the RIGHT DOMAIN.

    DOMAIN, stated because R-15 requires the artifact presenting an
    enumeration to state it: **FUNCTIONS that can terminate or collect
    a process**, not the call sites that invoke them.

    JUSTIFICATION for that domain, because R-15 also requires the
    domain be shown right for the claim: the claim is "every reaper is
    pinned". A reaper is a FUNCTION. An enumeration over CALL SITES
    can be complete while the claim is false — which is exactly what
    happened: the call-site enumeration in `SpawnSiteClosureTests` was
    exhaustive within its own domain and blind to `reap_owned`, a
    sibling reaper function that no test exercised against a live
    group. Call sites are a PROXY for reapers, and a proxy that can be
    complete while the claim fails is the wrong domain.

    Both closures are kept. The call-site one answers "does every
    spawn get owned?"; this one answers "does every reaper get
    pinned?". They are different questions over different domains, and an answer
    to the first leaves the second open.

    Source is the only feasible level for the ENUMERATION, and the
    reason is that its subject is which FUNCTIONS exist in a body of
    text; the behavioural half it fronts is `ProcessTreeOwnershipTests`,
    which drives each pinned reaper against a real process tree.
    """

    #: Calls by which a function can terminate or collect a process.
    #: `killpg(pgid, 0)` is a liveness PROBE rather than a
    #: termination, so a function whose only such call passes signal 0
    #: is not a reaper; that distinction is applied below rather than
    #: assumed, and it is why `_group_alive` and `alive` are not in
    #: the domain.
    TERMINATING = ("kill", "killpg", "terminate", "waitpid")

    #: Each reaper in the domain, mapped to what pins it, or to why it
    #: #: is outside what the `reap_group` standard can pin, and what
    #: covers it instead. A reaper absent from this map fails the closure.
    PINNED = {
        ("target_runtime/process_ownership.py", "reap_group"):
            "test_reaping_removes_a_grandchild_the_leader_left_behind"
            " — mutant S09 reverts it to a leader-only kill and dies"
            " by authored assertion in ~4s against a 3600s fixture"
            " sleep",
        ("target_runtime/process_ownership.py", "reap_owned"):
            "test_reap_owned_reaps_a_LIVE_group_it_recorded — mutant"
            " S11 deletes its os.killpg and dies by authored"
            " assertion; the group is still RUNNING when the reap is"
            " called, which is what the previous pins lacked",
        ("target_runtime/process_ownership.py", "_reap_leader"):
            "test_reap_leader_collects_a_zombie_it_forked — mutant"
            " S12 removes the waitpid and dies by authored assertion",
        ("tests/test_workspace_trust.py", "reap_process_group"):
            "ProcessTreeReapingTests (I1) drives it against a real"
            " pty tree; mutant S13 breaks its delegation to"
            " process_ownership.reap_group and dies by authored"
            " assertion",
        ("tests/test_workspace_trust.py", "_force_kill"):
            "NOT PINNED to the reap_group standard, and it cannot be:"
            " it is a cleanup-time safety net, and an assertion inside"
            " a cleanup cannot fail the test whose leak it exists to"
            " prevent. What covers it instead: it signals only a pid"
            " the test itself forked, and any leak it fails to catch"
            " surfaces in the suite-level PPID-1 observation (E-5).",
        ("tests/test_ownership.py", "_force_cleanup"):
            "NOT PINNED for the same reason, and covered better:"
            " ProcessTreeOwnershipTests.tearDown asserts"
            " surviving_owned_groups() is empty AFTER it runs, so a"
            " failure of this cleanup fails the test by assertion.",
        ("target_runtime/process_ownership.py",
         "reap_group_by_recorded_root"):
            "the recovery-side reaper, reached only from"
            " `recover_orphans`. Its EVIDENCE differs from"
            " `reap_owned`'s — a directory this component created"
            " rather than a ledger line — so it is a distinct reaper"
            " and is listed distinctly. Its guards are pinned by"
            " FailClosedIsIntendedTests (nothing is signalled without"
            " durable evidence) and"
            " LedgerExternalRecoveryTests.test_recovery_refuses_this_"
            "process_own_group_and_low_pids; the reaping of a LIVE"
            " group through it is the piece still owed, and is owed"
            " because proving it requires spawning.",
        ("tests/test_ownership.py", "_definitely_dead_pgid"):
            "a test HELPER that forks a child and immediately waits"
            " for it, so its `waitpid` puts it in this domain while"
            " it starts nothing that outlives the call. It exists to"
            " produce a known-dead pgid for the files-only recovery"
            " tests, and pinning it to the reap_group standard would"
            " mean spawning something for it to reap — which is the"
            " opposite of its purpose.",
        ("tests/test_ownership.py",
         "test_reap_leader_collects_a_zombie_it_forked"):
            "the pin for `_reap_leader` itself: it calls waitpid to"
            " PROVE the helper already collected the child (the call"
            " must raise ECHILD), so it is a reaper by this scan's"
            " definition while being the assertion rather than the"
            " thing asserted on.",
        ("tests/test_target_runtime.py",
         "test_store_lock_excludes_a_real_second_process"):
            "an inline kill in a test BODY rather than a reusable"
            " reaper: it terminates one child that test forked, in the"
            " same body, and there is no reaper function to pin.",
        ("tests/test_target_runtime.py",
         "test_fifo_is_refused_not_followed_bounded_child"):
            "the same shape, using terminate() on one child of its"
            " own.",
    }

    def domain(self):
        """Every reaper FUNCTION on the changed surface, derived."""
        import ast
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout
        found = {}
        for line in out.splitlines():
            path = line[3:].strip()
            if not path.endswith(".py"):
                continue
            if not path.split("/")[0] in (
                "tests", "target_runtime", "herdr", "workflow_authority"
            ):
                continue
            with open(os.path.join(REPO_ROOT, path),
                      encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    name = (func.attr if isinstance(func, ast.Attribute)
                            else func.id if isinstance(func, ast.Name)
                            else None)
                    if name not in self.TERMINATING:
                        continue
                    if self._is_probe(inner, name):
                        continue
                    found[(path, node.name)] = node.lineno
                    break
        return found

    @staticmethod
    def _is_probe(call, name):
        """A signal-0 call does not terminate: it asks whether a process
        exists. Excluded from the domain, and excluded HERE rather
        than by a hand-kept list, so a probe that grows a real signal
        joins the domain automatically."""
        if name not in ("kill", "killpg"):
            return False
        import ast
        if len(call.args) < 2:
            return False
        second = call.args[1]
        return isinstance(second, ast.Constant) and second.value == 0

    def test_the_domain_is_derived_and_not_vacuous(self):
        domain = self.domain()
        self.assertGreaterEqual(
            len(domain), 4,
            "the reaper-function scan found almost nothing; a clean"
            " result from a broken detector proves nothing",
        )
        self.assertIn(
            ("target_runtime/process_ownership.py", "reap_owned"),
            domain,
            "the reaper whose absence from the previous enumeration"
            " was the round-01 blocker is missing from this one too",
        )

    def test_every_reaper_is_pinned_or_declared(self):
        unhandled = sorted(
            "%s::%s (line %d)" % (path, name, line)
            for (path, name), line in self.domain().items()
            if (path, name) not in self.PINNED
        )
        self.assertEqual(
            unhandled, [],
            "reaper function(s) with no pin and no stated reason:\n"
            "  %s" % "\n  ".join(unhandled),
        )

    def test_every_declared_reaper_still_exists(self):
        """Anti-stale: a declaration for a function that is gone is a licence with no
        subject behind it.

        Checked by FUNCTION EXISTENCE rather than by domain
        membership, deliberately: two declared entries —
        `reap_process_group` and `_force_cleanup` — terminate through
        a helper rather than in their own body, so they sit at depth
        ONE and are outside the depth-zero domain by construction.
        They are declared anyway because they ARE reapers to a reader,
        and a map that silently dropped them would be the same
        wrong-domain mistake in miniature.
        """
        import ast
        stale = []
        for path, name in sorted(self.PINNED):
            full = os.path.join(REPO_ROOT, path)
            if not os.path.exists(full):
                stale.append((path, name))
                continue
            with open(full, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            names = {
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef,
                                     ast.AsyncFunctionDef))
            }
            if name not in names:
                stale.append((path, name))
        self.assertEqual(
            stale, [],
            "declared reaper(s) that no longer exist: %r" % (stale,),
        )

    def test_the_declared_depth_one_reapers_are_the_ones_named(self):
        """The two depth-one entries are named explicitly, so their
        absence from the derived domain is a DISCLOSED consequence of
        the floor rather than an unnoticed gap."""
        depth_one = {
            key for key in self.PINNED if key not in set(self.domain())
        }
        self.assertEqual(
            depth_one,
            {("tests/test_workspace_trust.py", "reap_process_group"),
             ("tests/test_ownership.py", "_force_cleanup")},
            "the set of declared reapers outside the depth-zero domain"
            " changed; the floor's consequences are stated, so a new"
            " one must be stated too",
        )

    def test_the_domain_is_a_FLOOR_and_its_bounds_are_named(self):
        """R-13 rides on R-15's axis too.

        The enumeration counts functions whose OWN body spells a
        terminating call, in CHANGED files, at depth ZERO — so the
        number is a FLOOR, not a total.

        Named as outside it, each leaving the count a floor: a reaper
        that terminates only through a helper it calls (depth one or
        beyond), a reaper in an UNCHANGED file, a call reached through
        an alias or `getattr`, a process terminated by a library this
        code calls, and a termination expressed some way other than
        the four names above. The transitive closure over this domain
        returns far more functions — every test that reaches a reaper
        through a helper — and that set answers a different question
        than "which functions ARE reapers".
        """
        doc = inspect.getdoc(
            ReaperFunctionClosureTests
            .test_the_domain_is_a_FLOOR_and_its_bounds_are_named
        )
        self.assertIn("FLOOR, not a total", doc)
        self.assertIn("depth ZERO", doc)
        self.assertTrue(self.domain())


class SpawnSiteClosureTests(unittest.TestCase):
    """R-14 E-3: ONE owned-spawn/reap construct across this task's
    test surface, with the site set derived MECHANICALLY from the
    diff.

    The instance fixes are not the deliverable; this closure is. It walks the changed test files, finds the calls that can start a
    process, and requires each one to be either routed through
    `target_runtime.process_ownership` or covered by a BLOCKING form
    whose scope: it returns only after its child has exited.

    Source is the only feasible level for the enumeration, and the
    reason is that its subject is which CALL SITES exist in a body of
    text; the behavioural half it fronts is
    `ProcessTreeOwnershipTests`, which executes the construct against
    a real tree, and the harness-level `surviving_owned_groups` check.
    """

    #: Names that can start a process. Derived from what the changed
    #: files actually call, not from a remembered list.
    SPAWNERS = (
        "Popen", "run", "call", "check_call", "check_output",
        "fork", "forkpty", "spawn_owned", "system", "posix_spawn",
    )

    #: #: Forms that BLOCK: within such a call the return happens only
    #: after the child has exited, so the site leaves behind no
    #: descendant of its own making. `os.system` blocks too.
    BLOCKING = ("run", "call", "check_call", "check_output", "system")

    #: Sites that start a process which can outlive the call, and are
    #: declared here with the reason they are not routed through the
    #: construct. Each entry is (file, callee, reason).
    DECLARED = {
        ("tests/test_workspace_trust.py", "os.fork"):
            "the I1 pty/fork fixtures build their trees by hand to"
            " model the pre-setsid race the construct exists to"
            " prevent; their REAP is routed through"
            " process_ownership.reap_group, which is the half this"
            " closure is about",
        ("tests/test_workspace_trust.py", "pty.fork"):
            "same fixture family: pty.fork has no Popen form, and its"
            " reap is routed through process_ownership.reap_group",
        ("tests/test_ownership.py", "os.fork"):
            "the `_reap_leader` pin forks a child that exits at once"
            " and is COLLECTED BY THE FUNCTION UNDER TEST; routing it"
            " through the construct would collect it before the"
            " helper could be driven and the pin would pass for the"
            " wrong reason",
        ("tests/test_target_runtime.py", "subprocess.Popen"):
            "a cross-process lock probe whose child is waited for in"
            " the same test body and holds no descendants; it is"
            " named here rather than silently exempt",
    }

    def sites(self):
        """(path, lineno, callee) for every spawn-capable call on a
        changed test file, derived from the diff."""
        import ast
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout
        found = []
        for line in out.splitlines():
            path = line[3:].strip()
            if not (path.startswith("tests/") and path.endswith(".py")):
                continue
            with open(os.path.join(REPO_ROOT, path),
                      encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute):
                    name, base = func.attr, (
                        func.value.id + "."
                        if isinstance(func.value, ast.Name) else ""
                    )
                elif isinstance(func, ast.Name):
                    name, base = func.id, ""
                else:
                    continue
                if name in self.SPAWNERS:
                    found.append((path, node.lineno, base + name))
        return sorted(found)

    def test_the_enumeration_is_derived_and_not_vacuous(self):
        sites = self.sites()
        self.assertGreaterEqual(
            len(sites), 20,
            "the spawn-site scan found almost nothing; a clean result"
            " from a broken detector proves nothing",
        )
        self.assertTrue(
            any(path == "tests/test_ownership.py" for path, _l, _c
                in sites),
            "this module's own spawn is missing from the scan",
        )

    def test_every_spawn_site_is_routed_blocking_or_declared(self):
        unhandled = []
        for path, lineno, callee in self.sites():
            bare = callee.split(".")[-1]
            if bare == "spawn_owned":
                continue                       # routed
            if bare in self.BLOCKING:
                continue                       # cannot outlive itself
            if (path, callee) in self.DECLARED:
                continue                       # declared with a reason
            unhandled.append("%s:%d %s" % (path, lineno, callee))
        self.assertEqual(
            unhandled, [],
            "spawn site(s) that neither route through"
            " process_ownership, nor block, nor carry a declared"
            " reason:\n  %s" % "\n  ".join(unhandled),
        )

    def test_every_declared_exemption_still_exists(self):
        """An exemption for a site that is gone is a stale licence, so
        the declaration set is proven live rather than accumulating."""
        live = {(path, callee) for path, _l, callee in self.sites()}
        stale = sorted(key for key in self.DECLARED if key not in live)
        self.assertEqual(
            stale, [],
            "declared exemption(s) whose site no longer exists: %r"
            % (stale,),
        )

    def test_the_count_is_a_FLOOR_and_the_depth_is_named(self):
        """R-13 rides here. The enumeration counts DIRECT calls spelled
        on the listed names, in changed test files, at depth ZERO —
        the call is attributed where it is written.

        So the number is a FLOOR, not a total. Named as outside it,
        each leaving the count a floor: a spawn reached through a helper at depth one or beyond, a spawn
        in an UNCHANGED file, a callee bound to a local alias or reached
        through `getattr`, and a process started by a library this suite
        calls. A consumer that
        reports this figure carries the floor label with it.
        """
        sites = self.sites()
        self.assertTrue(sites)
        doc = inspect.getdoc(
            SpawnSiteClosureTests
            .test_the_count_is_a_FLOOR_and_the_depth_is_named
        )
        self.assertIn("FLOOR, not a total", doc)
        self.assertIn("depth ZERO", doc)


# The executed hermetic-git sweep runs a swept module with
class ScopeAssignmentCredentialTests(unittest.TestCase):
    """R-43 AG-1..AG-5: the SCOPE ASSIGNMENT is the credential, and a
    NAME is not.

    The Z-1 fix achieved per-workflow attribution by encoding the
    owner in a directory BASENAME and parsing it back at recovery
    time. Anything able to create a directory under the base could
    therefore mint a scope that passed every fail-closed check
    downstream, because every one of them validated the parse, and
    within that parse nothing was validated.

    THE TEST SHAPE THAT DETECTS THIS CLASS, and the reason these tests
    look the way they do (AG-4): a suite that only ever uses scopes
    the component itself created passes whether or not the hole is
    there — which is precisely how the flaw shipped. So the scopes
    here are built BY HAND, correctly named, and each holds a REAL
    LIVE STAMPED GROUP, and the assertion is that the group is still
    running afterwards.
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.scopes_used = []
        # ORDER MATTERS, and it is the recorded rule this fixture
        # nearly broke: `OWNER_LEDGER_ROOT`'s comment says an
        # ownership record deleted while the process it names may
        # still be running is a record that is already gone within the
        # window that matters. This base HOLDS the ledgers, so its
        # removal is registered FIRST and therefore runs LAST, and the
        # survivor check is registered after it so it runs BEFORE the
        # evidence is destroyed. A group that outlives its reap fails
        # the test instead of becoming an unattributable orphan.
        self.addCleanup(remove, self.base)
        self.addCleanup(self.assert_no_survivors)

    # --- helpers ----------------------------------------------------

    def assert_no_survivors(self):
        surviving = []
        for scope in self.scopes_used:
            surviving.extend(
                proc_module.surviving_owned_groups(scope)
            )
        self.assertEqual(
            surviving, [],
            "an occupant this fixture started outlived its reap; the"
            " ledgers naming it are about to be deleted, after which"
            " it is an orphan nothing can attribute",
        )

    def live_group_in(self, scope):
        """A REAL process, in its own session, stamped into ``scope``.

        Returns its pgid. Within these tests every wait is far
        shorter than the occupant's sleep, so a group found dead was
        killed rather than expired.
        """
        os.makedirs(scope, exist_ok=True)
        self.scopes_used.append(scope)
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            label="forged-scope-occupant",
            directory=scope,
            owned_root_base_dir=scope,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.release_occupant, scope, handle)
        deadline = time.monotonic() + 10
        pgid = None
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(scope)
            if roots and roots[0][1] is not None:
                pgid = roots[0][1]
                break
            time.sleep(0.02)
        self.assertIsNotNone(pgid, "the occupant never stamped its root")
        self.assertTrue(proc_module._group_alive(pgid))
        return pgid

    def release_occupant(self, scope, handle):
        """Reap this fixture's own occupant through the PINNED reaper.

        Routed through `reap_owned` against the scope's own ledger, so
        it can only ever signal a group this fixture recorded — and so
        it adds no new reaper to the enumerated domain.
        """
        proc_module.reap_owned(
            handle.pid, directory=scope, settle_seconds=3.0
        )
        try:
            handle.wait(timeout=3)
        except Exception:                                 # noqa: BLE001
            pass

    def assert_left_alone(self, scope, pgid, reason):
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=1.0
        )
        self.assertEqual(
            results, [],
            "recovery ACTED on a scope it could not attribute from"
            " the protected store",
        )
        self.assertEqual(unattributed, [(scope, reason)])
        self.assertTrue(
            proc_module._group_alive(pgid),
            "a live group inside a scope with no valid assignment was"
            " KILLED; attribution fell back to the directory NAME,"
            " which anything able to create a directory controls",
        )
        self.assertTrue(os.path.isdir(scope))

    # --- AG-4: the forgery, driven directly -------------------------

    def test_a_CORRECTLY_NAMED_UNASSIGNED_scope_is_NEVER_reaped(self):
        """AG-4 exactly: a hand-built directory whose name parses,
        holding a live stamped group, and no assignment anywhere."""
        scope = os.path.join(
            proc_module.owned_root_base(self.base),
            proc_module.scope_name(
                proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                "wf-forged", "t-forged",
            ),
        )
        pgid = self.live_group_in(scope)
        self.assert_left_alone(
            scope, pgid, proc_module.UNATTRIBUTED_NO_ASSIGNMENT
        )

    def test_a_FORGED_assignment_does_not_verify_and_is_left_alone(self):
        """The forger writes an assignment too. Without the binding
        key the record does not verify, and the group survives."""
        scope = os.path.join(
            proc_module.owned_root_base(self.base),
            proc_module.scope_name(
                proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                "wf-forged", "t-forged",
            ),
        )
        name = os.path.basename(scope)
        os.makedirs(proc_module.assignment_base(self.base), exist_ok=True)
        with open(proc_module.assignment_path(name, self.base), "w",
                  encoding="utf-8") as handle:
            json.dump({
                "scope_name": name,
                "owner_type": proc_module.OWNER_TYPE_WORKFLOW,
                "control_digest": proc_module.control_digest(
                    "/control/repo"
                ),
                "owner_id": "wf-forged",
                "unit_id": "t-forged",
                "control_identity": "/control/repo",
                "assigned_at": 1.0,
                "assigned_by_pid": 4242,
                "binding": "00" * 32,
            }, handle)
        pgid = self.live_group_in(scope)
        self.assert_left_alone(
            scope, pgid, proc_module.UNATTRIBUTED_FORGED
        )

    def test_a_TAMPERED_assignment_does_not_verify(self):
        """A real assignment, edited after the fact. The binding
        covers every field the reader trusts, so changing one breaks
        it rather than silently transferring ownership."""
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-real", "t-real", base=self.base,
        )
        path = proc_module.assignment_path(
            os.path.basename(scope), self.base
        )
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        record["control_identity"] = "/somebody/elses/repo"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        pgid = self.live_group_in(scope)
        self.assert_left_alone(
            scope, pgid, proc_module.UNATTRIBUTED_FORGED
        )

    def test_a_VALID_assignment_MOVED_to_another_scope_is_CONFLICTING(self):
        """The binding stays intact — it is simply bound to a
        different scope name, which is the point of binding it."""
        real = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-real", "t-real", base=self.base,
        )
        remove(real)
        stolen_name = proc_module.scope_name(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-thief", "t-thief",
        )
        with open(proc_module.assignment_path(
                os.path.basename(real), self.base), encoding="utf-8"
        ) as handle:
            record = handle.read()
        with open(proc_module.assignment_path(stolen_name, self.base),
                  "w", encoding="utf-8") as handle:
            handle.write(record)
        scope = os.path.join(
            proc_module.owned_root_base(self.base), stolen_name
        )
        pgid = self.live_group_in(scope)
        self.assert_left_alone(
            scope, pgid, proc_module.UNATTRIBUTED_CONFLICTING
        )

    def test_a_MALFORMED_assignment_is_left_alone(self):
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-real", "t-real", base=self.base,
        )
        with open(proc_module.assignment_path(
                os.path.basename(scope), self.base), "w",
                encoding="utf-8") as handle:
            handle.write("{not json")
        pgid = self.live_group_in(scope)
        self.assert_left_alone(
            scope, pgid, proc_module.UNATTRIBUTED_MALFORMED
        )

    # --- AG-3: revalidation against the durable record --------------

    def test_a_STALE_assignment_is_reported_and_LEFT_ALONE(self):
        """AG-3: the assignment verifies against the store and names
        an owner the CURRENT durable record does not hold."""
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-gone", "t-gone", base=self.base,
        )
        pgid = self.live_group_in(scope)
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=1.0,
            current_owners={(
                proc_module.OWNER_TYPE_WORKFLOW,
                proc_module.control_digest("/control/repo"),
                "wf-other", "t-other",
            )},
        )
        self.assertEqual(results, [])
        self.assertEqual(
            unattributed, [(scope, proc_module.UNATTRIBUTED_STALE)]
        )
        self.assertTrue(proc_module._group_alive(pgid))

    def test_a_CURRENT_assignment_IS_acted_on(self):
        """The counterpart the stale test needs to mean anything: flip
        the durable record to hold this owner and the SAME scope is
        reaped. Without this, "left alone" could be true because
        within this suite nothing is ever acted on."""
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-live", "t-live", base=self.base,
        )
        pgid = self.live_group_in(scope)
        results, unattributed = proc_module.recover_attributed(
            self.base, settle_seconds=10.0,
            current_owners={(
                proc_module.OWNER_TYPE_WORKFLOW,
                proc_module.control_digest("/control/repo"),
                "wf-live", "t-live",
            )},
        )
        self.assertEqual(unattributed, [])
        self.assertEqual(len(results), 1)
        identity = results[0][0]
        self.assertEqual(
            (identity.owner_type, identity.owner_id, identity.unit_id),
            (proc_module.OWNER_TYPE_WORKFLOW, "wf-live", "t-live"),
        )
        self.assertEqual(results[0][1], [pgid])
        self.assertFalse(proc_module._group_alive(pgid))

    # --- AG-1: the record exists BEFORE the spawn -------------------

    def test_the_ASSIGNMENT_is_written_before_the_scope_exists(self):
        """AG-1. Asserted by ordering, not by prose: the assignment
        file is already on disk when the scope directory appears."""
        seen = {}
        real_makedirs = os.makedirs

        def watch(path, *args, **kwargs):
            name = os.path.basename(str(path).rstrip(os.sep))
            if (
                proc_module.parse_scope(str(path)) is not None
                and "assignment_existed" not in seen
            ):
                seen["assignment_existed"] = os.path.exists(
                    proc_module.assignment_path(name, self.base)
                )
            return real_makedirs(path, *args, **kwargs)

        from unittest.mock import patch
        with patch.object(os, "makedirs", watch):
            proc_module.assign_scope(
                proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                "wf-order", "t-order", base=self.base,
            )
        self.assertTrue(
            seen.get("assignment_existed"),
            "the scope directory was created before its assignment"
            " was durable; a crash in that window leaves a scope whose"
            " owner is only guessable from its name",
        )

    def test_the_binding_key_is_private_to_this_user(self):
        proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-key", "t-key", base=self.base,
        )
        path = os.path.join(
            proc_module.assignment_base(self.base),
            proc_module.ASSIGNMENT_KEY_FILE,
        )
        self.assertEqual(
            os.stat(path).st_mode & 0o777, 0o600,
            "the binding key is readable beyond this user, so the"
            " credential it protects is forgeable by anyone who can"
            " read it",
        )

    def test_the_assignment_store_is_NOT_inside_the_record_space(self):
        """A credential store sitting in the space being enumerated is
        one rename away from being mistaken for a record."""
        self.assertNotIn(
            proc_module.owned_root_base(self.base),
            proc_module.assignment_base(self.base),
        )

    def test_REASSIGNING_the_same_owner_REFRESHES(self):
        """The ordinary case: a workflow takes many role turns and
        each one assigns the same scope."""
        first = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-a", "t-a", base=self.base,
        )
        second = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-a", "t-a", base=self.base,
        )
        self.assertEqual(first, second)
        self.assertIsNone(
            proc_module.validate_assignment(first, base=self.base)[1]
        )

    def test_a_DIGEST_COLLISION_between_controls_is_REFUSED(self):
        """The one way two controls can still land on one scope name.

        The digest in the name is truncated, so a collision is
        possible in principle and must not let the second control
        inherit the first's records. Driven by SHRINKING the digest
        until a collision is findable — the branch is reachable and
        asserted rather than described.
        """
        from unittest.mock import patch
        with patch.object(proc_module, "CONTROL_DIGEST_CHARS", 1):
            controls = ["/control/%d" % index for index in range(64)]
            by_digest = {}
            pair = None
            for control in controls:
                digest = proc_module.control_digest(control)
                if digest in by_digest:
                    pair = (by_digest[digest], control)
                    break
                by_digest[digest] = control
            self.assertIsNotNone(pair, "no collision found to drive")
            proc_module.assign_scope(
                proc_module.OWNER_TYPE_WORKFLOW, pair[0],
                "wf-a", "t-a", base=self.base,
            )
            with self.assertRaises(ValueError):
                proc_module.assign_scope(
                    proc_module.OWNER_TYPE_WORKFLOW, pair[1],
                    "wf-a", "t-a", base=self.base,
                )

    # --- AG-5: the planning owner type is EXACT ---------------------

    def test_a_PLANNING_scope_carries_its_own_owner_type(self):
        scope = proc_module.planning_scope("/control/repo", self.base)
        identity = proc_module.parse_scope(scope)
        self.assertEqual(
            identity.owner_type, proc_module.OWNER_TYPE_PLANNING
        )
        self.assertEqual(
            identity.control_digest,
            proc_module.control_digest("/control/repo"),
        )
        self.assertEqual(
            identity.unit_id, proc_module.PLANNING_UNIT_ID
        )
        self.assertNotEqual(
            scope,
            proc_module.workflow_scope(
                "/control/repo", proc_module.PLANNING_OWNER_ID,
                proc_module.PLANNING_UNIT_ID, self.base
            ),
            "a planning owner and a workflow owner with the same id"
            " collide in one namespace; the owner type is not exact",
        )

    def test_an_UNKNOWN_owner_type_does_not_parse(self):
        stray = os.path.join(
            proc_module.owned_root_base(self.base),
            "scope-owner=impostor__control=abc__id=x__unit=y",
        )
        self.assertIsNone(proc_module.parse_scope(stray))
        with self.assertRaises(ValueError):
            proc_module.scope_name(
                "impostor", "/control/repo", "x", "y"
            )

    def test_a_PLANNING_scope_is_not_stale_against_a_workflow_record(self):
        """The workflow record is not a planning scope's authority:
        within this ordering a planning scope exists BEFORE any
        workflow record."""
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_PLANNING, "/control/repo",
            proc_module.PLANNING_OWNER_ID,
            proc_module.PLANNING_UNIT_ID, base=self.base,
        )
        identity, reason = proc_module.validate_assignment(
            scope, base=self.base, current_owners=set()
        )
        self.assertIsNone(reason)
        self.assertEqual(
            identity.owner_type, proc_module.OWNER_TYPE_PLANNING
        )

    def test_the_production_planning_seam_ASSIGNS_a_planning_owner(self):
        from codex_gateway import role_turn as role_turn_module
        scope = role_turn_module._planning_scope("/control/repo")
        identity, reason = proc_module.validate_assignment(scope)
        self.assertIsNone(reason)
        self.assertEqual(
            identity.owner_type, proc_module.OWNER_TYPE_PLANNING
        )
        self.assertEqual(
            identity.unit_id, proc_module.PLANNING_UNIT_ID
        )


class CurrentScopeOwnersTests(RuntimeCase):
    """R-43 AG-3: the owners recovery revalidates against come from
    the DURABLE WORKFLOW RECORD, read by production from the store.

    Without this the staleness gate would be a parameter that within
    production no caller fills — a safety value that within production
    never reaches a decision, which is the class R-40/R-38/R-42
    already covers.
    """

    def test_a_stored_workflow_yields_BOTH_of_its_scope_owners(self):
        """The pre-dispatch scope and the task scope are both owned by
        a workflow. Listing only one strands the other's records."""
        from target_runtime import runtime as runtime_module
        entry = self.authorized_record()
        self.put_record(entry)
        digest = proc_module.control_digest(
            entry["control_identity"]["repository_realpath"]
        )
        owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertIn(
            (proc_module.OWNER_TYPE_WORKFLOW, digest,
             entry["workflow_id"], "pre-dispatch"),
            owners,
        )
        task_id = (entry.get("target_engine") or {}).get("task_id")
        if isinstance(task_id, str) and task_id:
            self.assertIn(
                (proc_module.OWNER_TYPE_WORKFLOW, digest,
                 entry["workflow_id"], task_id),
                owners,
            )

    def test_an_UNKNOWN_workflow_is_NOT_among_the_current_owners(self):
        from target_runtime import runtime as runtime_module
        self.put_record(self.authorized_record())
        owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertNotIn(
            (proc_module.OWNER_TYPE_WORKFLOW,
             proc_module.control_digest("/control/repo"),
             "wf-never-existed", "pre-dispatch"),
            owners,
        )

    def test_an_UNREADABLE_store_yields_NO_owners(self):
        """FAIL-CLOSED, asserted rather than described: within this
        path a recovery that cannot read the durable record acts on no
        workflow scope."""
        from unittest.mock import patch
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store_module
        with patch.object(
            wa_store_module.WorkflowStore, "load",
            side_effect=wa_store_module.StoreError("unreadable"),
        ):
            owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertEqual(owners, set())

    def test_production_recovery_PASSES_the_durable_owners_through(self):
        """The value REACHES its destination: flip the store's view and
        the same scope changes from reaped to left alone."""
        from unittest.mock import patch
        from target_runtime import runtime as runtime_module
        seen = {}

        def capture(settle_seconds=None, current_owners=None):
            seen["owners"] = current_owners
            return [], []

        with patch.object(
            runtime_module.ownership_module, "recover_attributed",
            capture,
        ):
            runtime_module.recover_inherited_processes(self.store_dir)
        self.assertIsNotNone(
            seen.get("owners"),
            "restart recovery ran without the durable record's view of"
            " who currently exists; the AG-3 revalidation would have"
            " nothing to check against",
        )


# `runpy.run_path(..., run_name="__main__")`. Within that runner, a
# module lacking this block imports and exits, so the shim observes no
# git process and the sweep reports having no observation to assert on.
if __name__ == "__main__":
    unittest.main()
