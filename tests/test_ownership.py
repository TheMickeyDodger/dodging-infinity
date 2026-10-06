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
from target_runtime import spawn_stamp as stamp_module      # noqa: E402
from target_runtime import worker as worker_module          # noqa: E402
from target_runtime import workspace as workspace_module  # noqa: E402
from target_runtime import workspace_ownership as ws_module  # noqa: E402
from target_runtime import workspace_trust as trust_module  # noqa: E402
from workflow_authority import record as wa_record      # noqa: E402

import _scope_hygiene as scope_hygiene                  # noqa: E402
import _stamp_faults as stamp_faults                    # noqa: E402
from _di_remote2_surface import (                       # noqa: E402
    DI_REMOTE_2_PRODUCTION_PYTHON, DI_REMOTE_2_TEST_PYTHON,
)
from test_target_runtime import NOW, RuntimeCase        # noqa: E402


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _committed_sources(paths):
    sources = {}
    for path in paths:
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
            sources[path] = handle.read()
    return sources


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
        """Whether ``ps`` reports ``pid`` in the zombie state (``Z``: exited,
        not yet collected by its parent). It does not establish whose child
        ``pid`` is; the caller's own fork does."""
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

        closed = []

        def live_workspaces():
            # Task 8 ownership correction: a real close removes the
            # workspace from the listing, and the release reclaims only on
            # that OBSERVED absence (a close that leaves it listed retains).
            return [] if closed else [{"workspace_id": "wTEST",
                                       "agent_names": {"a-sup", "a-lead"}}]

        def close_fn(workspace_id):
            closed.append(workspace_id)
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
        # Task 8 ownership correction: the listing reflects the close, so
        # the reclaim rests on OBSERVED absence, not the close's return.
        exact = self._domain_b_broker(
            live_fn=lambda: [] if closed else [{"workspace_id": "wTEST",
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

    # -- Task 8 cap correction: the legacy child route, production projection --

    def legacy_history(self, relevant, prefix=40):
        """A bound terminal legacy record whose control repository's REAL
        ``children.json`` holds ``prefix`` unrelated spawn records BEFORE the
        ``relevant`` ones (the real writer's shape), read through the
        PRODUCTION projection. Returns (entry, lease, task_id)."""
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        task_id = "20260828-114612-5d92e1"
        workflows = self.fresh_workflows()
        workflows["workflows"]["wf-0001"]["target_engine"] = {
            "alias": dispatch_module.ALIAS_PREFIX + "wf-0001",
            "task_id": task_id, "repo": "u", "dispatched_at": NOW,
        }
        self.write_raw(workflows)

        def child(repo, task, n, workspace="wTEST", agents=None):
            return {"requested_at": 1000 + n, "parent_repo": self.control,
                    "parent_task_id": None, "dependency": False, "repo": repo,
                    "task_id": task, "task_status": "ACTIVE", "workspace_id": workspace,
                    "agents": agents or {"supervisor": "a-sup"}}
        history = [child(os.path.join(self.workspaces, "wf-old-%03d" % n),
                         "20260801-0000%02d-%06x" % (n % 60, n), n, "w-old-%d" % n,
                         {"supervisor": "old-sup-%d" % n}) for n in range(prefix)]
        history += [child(lease, task, 100 + n) for n, task in enumerate(
            task_id if task is None else task for task in relevant)]
        state = os.path.join(self.control, ".herd", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "children.json"), "w") as handle:
            json.dump({"version": 1, "children": history}, handle)
        # The unscoped (default) projection: truncated in file order, the
        # relevant records past its listing bound — the defect's precondition.
        from herdr.observe import observe_spawn_records
        unscoped = observe_spawn_records(self.control)
        self.assertEqual((unscoped["truncated"], unscoped["count"]),
                         (True, prefix + len(relevant)))
        self.assertNotIn(lease, [r["repo"] for r in unscoped["listed"]])
        return self.fresh_workflows()["workflows"]["wf-0001"], lease, task_id

    def production_broker(self, live_fn, close_fn):
        return broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn,
            clock=lambda: NOW,
            observer_fn=self.observer,
            spawn_records_fn=broker_module._production_spawn_records_observer,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            live_workspaces_fn=live_fn,
            workspace_close_fn=close_fn,
        )

    def test_CAP_legacy_a_relevant_record_beyond_an_unrelated_prefix_releases(self):
        from target_runtime import runtime as runtime_module
        entry, lease, task_id = self.legacy_history([None])
        closed = []
        broker = self.production_broker(
            live_fn=lambda: [] if closed else [{"workspace_id": "wTEST",
                                                "agent_names": {"a-sup"}}],
            close_fn=closed.append)
        outcome = self._release_through(broker)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(closed, ["wTEST"])
        self.assertFalse(os.path.isdir(lease))
        self.assertIsNotNone(self.fresh_workflows()["workflows"]["wf-0001"]
                             ["workspace_lease"]["released_at"])
        self.assertNotIn("wf-0001", [wid for wid, _rev in
                                     runtime_module.terminal_cleanup_candidates(
                                         self.store_dir)])

    def test_CAP_legacy_a_second_match_beyond_the_relevant_bound_is_never_hidden(self):
        """33 records name the lease: the matching one, 31 of other tasks, and
        a SECOND matching one at relevant position 33 — past the listing
        bound. The relevant set is over-bound, so the projection is truncated
        and the route retains; it never reads the first 32 as the whole."""
        entry, lease, task_id = self.legacy_history(
            [None] + ["20260829-0000%02d-dddddd" % n for n in range(31)] + [None])
        closed = []
        broker = self.production_broker(
            live_fn=lambda: [{"workspace_id": "wTEST", "agent_names": {"a-sup"}}],
            close_fn=closed.append)
        outcome = self._release_through(broker)
        self.assertEqual(closed, [])
        self.assertTrue(os.path.isdir(lease))
        self.assertIsNone(self.fresh_workflows()["workflows"]["wf-0001"]
                          ["workspace_lease"]["released_at"], (outcome.outcome,
                                                                outcome.detail))

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
        credential is not this workflow's to remove. Task 8 R20-B
        (Addendum C, C-2c): nor is it silently omitted — the name claims
        this workflow, so it is REFUSED with its reason (a name grants
        retention only), where it used to be dropped from both lists."""
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
        self.assertEqual(refused, [(forged, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED,
            proc_module.UNATTRIBUTED_NO_ASSIGNMENT))])
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

    # -- Task 8 R20-1: retired only once ABSENCE is established ------------

    def root_in(self, scope, nonce, pgid=None, start=None):
        """An owned root under ``scope`` written as the production stamp
        writes one (``create_owned_root``, then the start and the group)."""
        root = proc_module.create_owned_root(nonce, scope)
        for name, value in ((proc_module.OWNED_ROOT_START_FILE, start),
                            (proc_module.OWNED_ROOT_PGID_FILE, pgid)):
            if value is not None:
                with open(os.path.join(root, name), "w") as handle:
                    handle.write(str(value))
        return root

    def kept(self, scope, reason):
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual((retired, refused), ([], [(scope, reason)]))
        self.assertTrue(os.path.isdir(scope))
        self.assertTrue(os.path.isfile(proc_module.assignment_path(
            os.path.basename(scope), self.private)))

    def test_R20_a_scope_whose_recorded_groups_are_all_GONE_is_retired(self):
        scope = self.scope_for("wf-0001")
        self.root_in(scope, "own-dead", pgid=999999, start="Thu Jan  1 00:00:00 1970")
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual((retired, refused), ([scope], []))
        self.assertFalse(os.path.isdir(scope))

    def test_R20_a_NEVER_STAMPED_root_keeps_its_scope(self):
        """Whether its process started cannot be known: ambiguity is not
        absence."""
        scope = self.scope_for("wf-0001")
        self.root_in(scope, "own-unstamped")
        self.kept(scope, proc_module.RETIRE_REFUSED_UNSTAMPED)

    def unreadable_kept(self, damage, repair):
        """An unreadable record is not an absent one: the scope is kept
        while ``damage`` stands (``owned_roots`` would have read it as
        unstamped or as no roots at all), and retired once repaired."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        scope = self.scope_for("wf-0001")
        root = self.root_in(scope, "own-damaged", pgid=999999, start="x")
        record = os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)
        prefix = proc_module.owned_root_base(scope)
        damage(record, prefix)
        self.addCleanup(repair, record, prefix)
        try:
            self.kept(scope, proc_module.RETIRE_REFUSED_UNREADABLE)
        finally:
            repair(record, prefix)
        retired, _refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual(retired, [scope])              # repaired: retired

    @staticmethod
    def readable(record, prefix):
        if os.path.isdir(prefix):
            os.chmod(prefix, 0o755)
        if os.path.exists(record):
            os.chmod(record, 0o644)
            Path(record).write_text("999999")

    def test_R20_an_UNREADABLE_group_record_keeps_its_scope(self):
        self.unreadable_kept(lambda record, prefix: os.chmod(record, 0), self.readable)

    def test_R20_a_group_record_naming_NO_GROUP_keeps_its_scope(self):
        self.unreadable_kept(
            lambda record, prefix: Path(record).write_bytes(b"\xff\xfe"), self.readable)

    def test_R20_a_group_id_LARGER_than_any_group_keeps_its_scope(self):
        self.unreadable_kept(
            lambda record, prefix: Path(record).write_text("9" * 40), self.readable)

    def test_R20_an_UNLISTABLE_owned_root_prefix_keeps_its_scope(self):
        self.unreadable_kept(lambda record, prefix: os.chmod(prefix, 0), self.readable)

    def test_R20_a_LEADERLESS_live_group_keeps_its_scope_and_is_never_signalled(self):
        """The leader ended, its descendant survives in the recorded group:
        its ownership cannot be corroborated, so the record is the only
        evidence of a process that may be ours — kept, and nothing is
        signalled."""
        scope = self.scope_for("wf-0001")
        handle = proc_module.spawn_owned(
            [sys.executable, "-c",
             "import subprocess; subprocess.Popen(['sleep', '3600'])"],
            label="leaderless-fixture",
            directory=scope, owned_root_base_dir=scope,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pgid = handle.pid
        self.addCleanup(self.kill_group, pgid)
        handle.wait(timeout=10)                           # the leader ends
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not proc_module._group_alive(pgid):
            time.sleep(0.02)
        self.assertTrue(proc_module._group_alive(pgid), "no descendant survived")
        self.kept(scope, proc_module.RETIRE_REFUSED_LEADERLESS)
        self.assertTrue(proc_module._group_alive(pgid), "the group was signalled")

    def test_R20_a_REUSED_group_id_is_not_ours_and_its_scope_is_retired(self):
        """A live number whose leader started at a DIFFERENT time is a reused
        id: the recorded group is gone. Retained, it would keep a finished
        scope forever."""
        scope = self.scope_for("wf-0001")
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            label="reused-fixture",
            directory=scope, owned_root_base_dir=scope,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.collect, handle)
        self.addCleanup(self.kill_group, handle.pid)
        deadline = time.monotonic() + 10
        roots = []
        while time.monotonic() < deadline:
            roots = proc_module.owned_roots(scope)
            if roots and roots[0][1] is not None:
                break
            time.sleep(0.02)
        self.assertEqual(roots[0][1], handle.pid)
        with open(os.path.join(roots[0][0], proc_module.OWNED_ROOT_START_FILE), "w") as out:
            out.write("Thu Jan  1 00:00:00 1970")
        retired, refused = proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private
        )
        self.assertEqual((retired, refused), ([scope], []))

    @staticmethod
    def kill_group(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass

    @staticmethod
    def collect(handle):
        try:
            handle.wait(timeout=10)
        except Exception:                         # noqa: BLE001
            pass

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

    # -- Task 8 R20-2: the strict reader the release holds on --------------------

    def refusals(self, workflow_id="wf-0001", base=None, skip_units=()):
        return proc_module.owned_scope_refusals(
            self.CONTROL, workflow_id, base=base or self.private, skip_units=skip_units)

    def claimed_name(self, unit_id, workflow_id="wf-0001"):
        return os.path.join(proc_module.owned_root_base(self.private), proc_module.scope_name(
            proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL, workflow_id, unit_id))

    def test_R20B_owned_scope_refusals_reads_absence_and_only_this_workflow(self):
        """No base: nothing was ever assigned. An assigned, empty scope of
        this workflow: absence established. Another workflow's unstamped
        scope and a skipped unit's are not this reader's to refuse."""
        self.assertEqual(self.refusals(base=os.path.join(self.private, "never")), ([], None))
        self.scope_for("wf-0001")
        self.assertEqual(self.refusals(), ([], None))
        proc_module.create_owned_root("own-0000000000000000", self.scope_for("wf-OTHER"))
        proc_module.create_owned_root("own-0000000000000000", self.scope_for("wf-0001", "verify"))
        self.assertEqual(self.refusals(skip_units=("verify",)), ([], None))
        self.assertEqual(self.refusals(), (
            [(self.claimed_name("verify"), proc_module.RETIRE_REFUSED_UNSTAMPED)], None))

    def test_R20B_owned_scope_refusals_an_unenumerable_base_is_a_problem(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        self.scope_for("wf-0001")
        prefix = proc_module.owned_root_base(self.private)
        os.chmod(prefix, 0)
        self.addCleanup(os.chmod, prefix, 0o755)
        self.assertEqual(self.refusals(), ([], proc_module.RETIRE_REFUSED_UNREADABLE))
        os.chmod(prefix, 0o755)
        other = tempfile.mkdtemp()
        self.addCleanup(remove, other)
        with open(proc_module.owned_root_base(other), "w") as handle:
            handle.write("not a directory")
        self.assertEqual(self.refusals(base=other), ([], proc_module.RETIRE_REFUSED_UNREADABLE))

    def test_R20B_owned_scope_refusals_a_name_without_an_assignment_is_refused(self):
        self.scope_for("wf-0001")
        forged = self.claimed_name("t-2")
        os.makedirs(forged)
        self.assertEqual(self.refusals(), ([(forged, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED, proc_module.UNATTRIBUTED_NO_ASSIGNMENT))],
            None))

    def test_R20B_owned_scope_refusals_a_matching_non_directory_is_refused(self):
        """Addendum A: an entry whose NAME claims exactly this workflow and
        control repository but which is not a directory is never read as
        absent — a symbolic link (to this workflow's real, assigned scope,
        relocated: ``lstat`` does not follow it), a regular file, a FIFO —
        each refused UNREADABLE naming what it is. A base file whose name
        claims nothing is not a scope and is ignored."""
        scope = self.scope_for("wf-0001")
        relocated = os.path.join(self.private, "relocated")
        os.rename(scope, relocated)
        os.symlink(relocated, scope)
        proc_module.create_owned_root("own-0000000000000000", relocated)
        with open(self.claimed_name("t-2"), "w") as handle:
            handle.write("not a scope")
        os.mkfifo(self.claimed_name("t-3"))
        with open(os.path.join(proc_module.owned_root_base(self.private), "README"), "w") as handle:
            handle.write("the base's own file")
        unreadable = proc_module.RETIRE_REFUSED_UNREADABLE
        self.assertEqual(self.refusals(), (sorted([
            (scope, "%s (a symbolic link, not a directory)" % unreadable),
            (self.claimed_name("t-2"), "%s (a regular file, not a directory)" % unreadable),
            (self.claimed_name("t-3"), "%s (a special file, not a directory)" % unreadable),
        ]), None))

    def test_R20B_an_unenumerable_base_is_reported_by_retirement_never_raised(self):
        """A release that already relinquished must not raise out of the
        retirement: scopes that cannot be enumerated are kept and REPORTED."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        scope = self.scope_for("wf-0001")
        prefix = proc_module.owned_root_base(self.private)
        os.chmod(prefix, 0)
        self.addCleanup(os.chmod, prefix, 0o755)
        try:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-0001", base=self.private)
        except OSError as exc:
            self.fail("the retirement raised %r instead of reporting" % (exc,))
        self.assertEqual(result, ([], [(prefix, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        os.chmod(prefix, 0o755)
        self.assertTrue(os.path.isdir(scope))

    # -- Task 8 R20-B Addendum C: the deletion boundary and observed removal ---------

    def retire(self, **kwargs):
        return proc_module.retire_workflow_scopes(
            self.CONTROL, "wf-0001", base=self.private, **kwargs)

    def test_R20B_retirement_stops_at_a_refused_admission_before_a_removal(self):
        """C-1b: the caller's admission is asked immediately before EACH
        removal. Refused before the second: the first stays removed and
        reported, the second is refused with the admission's text, untouched
        and its credential kept; nothing further is asked. (Task 8 R25-1:
        the admission is HELD across the effect, ``admit(effect)``.)"""
        first, second = sorted([self.scope_for("wf-0001", "t-1"),
                                self.scope_for("wf-0001", "t-2")])
        answers = [None, "held"]

        def admit(effect):
            answer = answers.pop(0)
            return (answer, None) if answer is not None else (None, effect())
        self.assertEqual(self.retire(admit=admit),
                         ([first], [(second, "held")]))
        self.assertEqual(answers, [])
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.isdir(second))
        self.assertTrue(os.path.isfile(proc_module.assignment_path(
            os.path.basename(second), self.private)))

    def test_R20B_a_directory_surviving_its_removal_is_refused_never_retired(self):
        """C-2: a removal is OBSERVED. A scope whose owned-root prefix cannot be
        written survives ``rmtree``'s ignored error: refused, naming that its
        credential WAS removed — never retired."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        scope = self.scope_for("wf-0001")
        prefix = proc_module.owned_root_base(scope)
        os.makedirs(prefix)
        with open(os.path.join(prefix, "keep"), "w") as handle:
            handle.write("an entry the prefix cannot lose")
        os.chmod(prefix, 0o500)
        self.addCleanup(os.chmod, prefix, 0o755)
        self.assertEqual(self.retire(), ([], [(scope, "%s (its assignment credential was"
                                                       " removed)"
                                                       % proc_module.RETIRE_REFUSED_UNDELETED)]))
        self.assertTrue(os.path.isdir(scope))
        self.assertFalse(os.path.exists(proc_module.assignment_path(
            os.path.basename(scope), self.private)))

    def test_R20B_a_credential_that_cannot_be_removed_is_never_retired(self):
        """C-2: the directory removed, its credential kept (the credential
        store cannot be written): refused, a truthful partial effect; every
        later retirement re-attempts it, and only once it is observed gone
        is it retired."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        scope = self.scope_for("wf-0001")
        credential = proc_module.assignment_path(os.path.basename(scope), self.private)
        store = proc_module.assignment_base(self.private)
        os.chmod(store, 0o500)
        self.addCleanup(os.chmod, store, 0o700)
        kept = proc_module.RETIRE_REFUSED_CREDENTIAL_KEPT
        self.assertEqual(self.retire(), ([], [(scope, "%s (PermissionError; its directory"
                                                       " was removed)" % kept)]))
        self.assertFalse(os.path.exists(scope))
        self.assertTrue(os.path.isfile(credential))
        self.assertEqual(self.retire(), ([], [(credential, "%s (PermissionError; its"
                                                            " directory is already gone)"
                                                            % kept)]))
        os.chmod(store, 0o700)
        self.assertEqual(self.retire(), ([credential], []))
        self.assertFalse(os.path.exists(credential))

    def test_R20B_a_scope_whose_credential_does_not_read_is_refused_never_omitted(self):
        """C-2c: a scope present on disk whose credential cannot be read is
        refused by its NAME, with the reason — never retired (a name grants
        retention only) and never dropped from both lists."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        scope = self.scope_for("wf-0001")
        credential = proc_module.assignment_path(os.path.basename(scope), self.private)
        os.chmod(credential, 0)
        self.addCleanup(os.chmod, credential, 0o600)
        # Task 8 R22-2: an UNREADABLE credential is UNAVAILABLE, never
        # "malformed" — the value moved; the refusal did not.
        self.assertEqual(self.retire(), ([], [(scope, "%s (%s (the assignment record:"
                                                       " PermissionError))" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED,
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE))]))
        self.assertTrue(os.path.isdir(scope))
        self.assertTrue(os.path.isfile(credential))


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

    A clean machine may have no unrelated shared records, and that is a
    valid baseline.  The suite-wide invariant still compares the real
    stores before and after.  Its anti-vacuity witness is constructed in
    a PRIVATE store below, where deterministic entries prove that the
    same census detects additions, removals, and in-place byte changes
    without writing a sentinel into machine-global state.

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
        """The census is driven over deterministic, private content.

        The machine-global baseline is allowed to be empty in a clean clone;
        treating historical developer state as the witness made this test
        fail for exactly the environment the isolation design must support.
        """
        root = tempfile.mkdtemp()
        self.addCleanup(remove, root)
        record = os.path.join(
            root, proc_module.OWNED_ROOT_DIR_NAME, "own-census-witness"
        )
        os.makedirs(record)
        with open(os.path.join(record, "nonce"), "w") as handle:
            handle.write("deterministic-census-witness")

        baseline = scope_hygiene.shared_base_snapshot(root)
        self.assertTrue(
            baseline["entries"],
            "the test-owned census fixture unexpectedly has no entries",
        )
        self.assertEqual(
            scope_hygiene.compare_snapshots(
                baseline, scope_hygiene.shared_base_snapshot(root)
            ),
            ([], [], []),
            "an unchanged non-empty private baseline did not remain"
            " byte-identical through the census",
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

    `_build_broker` hands `target_runtime.worker`'s
    `_production_live_workspaces` to the Broker,
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
            return worker_module._production_live_workspaces()

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
                worker_module._production_live_workspaces()
            )


class SpawnStampExecutableResolutionTests(unittest.TestCase):
    """The real stamping wrapper preserves Popen's exec contract."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)
        self.bin = os.path.join(self.base, "bin")
        os.makedirs(self.bin)

    def path_python(self, name):
        executable = os.path.join(self.bin, name)
        os.symlink(sys.executable, executable)
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join((
            self.bin, environment.get("PATH", os.defpath),
        ))
        return environment

    def root(self, name):
        root = os.path.join(self.base, name)
        os.makedirs(root)
        return root

    def run_wrapper(self, root, argv, environment=None):
        return subprocess.run(
            [sys.executable, proc_module._STAMP_WRAPPER, root, "--"]
            + list(argv),
            capture_output=True, text=True, timeout=30,
            start_new_session=True, env=environment,
        )

    def release(self, handle):
        if handle.poll() is None:
            proc_module.reap_owned(
                handle.pid, directory=self.base, settle_seconds=3.0,
            )
        try:
            handle.wait(timeout=3)
        except Exception:                                 # noqa: BLE001
            pass

    def test_PATH_only_executable_runs_with_argv_and_environment_unchanged(self):
        name = "path-only-stamp-probe"
        environment = self.path_python(name)
        environment["SPAWN_STAMP_ENV_PROBE"] = "inherited unchanged"
        arguments = ["plain", "space value", "$HOME", ";", ""]
        script = (
            "import json, os, sys; print(json.dumps({"
            "'argv': sys.argv[1:], "
            "'environment': os.environ['SPAWN_STAMP_ENV_PROBE']}))"
        )
        root = self.root("path-root")
        completed = self.run_wrapper(
            root, [name, "-c", script] + arguments, environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {
            "argv": arguments,
            "environment": "inherited unchanged",
        })
        self.assertTrue(os.path.exists(os.path.join(root, "pgid")))

    def test_absolute_executable_still_runs(self):
        root = self.root("absolute-root")
        completed = self.run_wrapper(
            root,
            [sys.executable, "-c",
             "import sys; sys.stdout.write(sys.argv[1])", "absolute-ok"],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "absolute-ok")
        self.assertTrue(os.path.exists(os.path.join(root, "pgid")))

    def test_exec_handoff_is_PATH_aware_and_has_no_shell_fallback(self):
        """Fast structural feedback in front of the executed
        `test_PATH_only_executable_runs_with_argv_and_environment_unchanged`,
        `test_spawn_owned_runs_a_PATH_only_executable_and_keeps_its_stamp`,
        and `test_nonexistent_executable_fails_after_stamping` guarantees.
        """
        source = inspect.getsource(stamp_module.main)
        self.assertIn("os.execvp(rest[0], rest)", source)
        self.assertNotIn("os.execv(rest[0], rest)", source)
        for forbidden in (
            "shell=True", "/bin/sh", "os.system", "subprocess.Popen",
        ):
            self.assertNotIn(forbidden, source)

    def test_nonexistent_executable_fails_after_stamping(self):
        root = self.root("missing-root")
        missing = "definitely-not-an-executable-" + secrets.token_hex(8)
        completed = self.run_wrapper(root, [missing])
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("FileNotFoundError", completed.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(root, "pgid")),
            "the wrapper did not stamp before the failed exec",
        )

    def test_spawn_owned_runs_a_PATH_only_executable_and_keeps_its_stamp(self):
        name = "path-owned-probe"
        environment = self.path_python(name)
        arguments = ["one", "two words", "$(not-a-shell)", ""]
        script = (
            "import json, os, sys; print(json.dumps({"
            "'pid': os.getpid(), 'pgid': os.getpgrp(), "
            "'argv': sys.argv[1:]}))"
        )
        handle = proc_module.spawn_owned(
            [name, "-c", script] + arguments,
            label="path-resolution-integration",
            directory=self.base, owned_root_base_dir=self.base,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment,
        )
        self.addCleanup(self.release, handle)
        stdout, stderr = handle.communicate(timeout=30)
        self.assertEqual(handle.returncode, 0, stderr)
        observed = json.loads(stdout)
        self.assertEqual(observed["argv"], arguments)
        self.assertEqual(observed["pid"], handle.pid)
        self.assertEqual(observed["pgid"], handle.pid)
        roots = proc_module.owned_roots(self.base)
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0][1], handle.pid)
        self.assertIn(handle.pid, proc_module.owned_groups(self.base))

    def test_production_runner_accepts_a_bare_PATH_executable(self):
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        name = "path-role-turn-probe"
        environment = self.path_python(name)
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
            "wf-path", "t-path", base=self.base,
        )
        script = (
            "import json, os, sys; data=sys.stdin.buffer.read(); "
            "print(json.dumps({'pid': os.getpid(), "
            "'pgid': os.getpgrp(), 'argv': sys.argv[1:], "
            "'stdin': data.decode()}))"
        )
        with patch.dict(
            os.environ, {"PATH": environment["PATH"]}, clear=False,
        ):
            rc, stdout, stderr, pid = role_turn_module._default_runner(
                [name, "-c", script, "bare-command"], b"prompt", None,
                owner_scope=scope,
            )
        self.assertEqual(rc, 0, stderr)
        observed = json.loads(stdout)
        self.assertEqual(observed, {
            "pid": pid,
            "pgid": pid,
            "argv": ["bare-command"],
            "stdin": "prompt",
        })
        self.assertIn(pid, proc_module.owned_groups(scope))
        self.assertIn(pid, [pgid for _root, pgid
                            in proc_module.owned_roots(scope)])
        self.assertFalse(proc_module._group_alive(pid))


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

    #: Calls that destroy, kill, or irreversibly remove. The Broker
    #: reaches the directory removal and the trust-entry removal
    #: through its worker seam (`relinquish_workspace`,
    #: `revoke_workspace_trust`), so those two seam names are listed
    #: beside the module operations they delegate to: the release
    #: path stays inside this domain at depth ZERO rather than
    #: dropping out because its destructive call moved behind a seam.
    DESTRUCTIVE = (
        "rmtree", "unlink", "remove", "killpg", "kill", "close_fn",
        "release", "revoke", "terminate",
        "relinquish_workspace", "revoke_workspace_trust",
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
            " keeps the workflow a cleanup candidate. And (Task 8"
            " R20-1) no verification of the workflow is unresolved:"
            " `verification_release_hold` returns None at the top of"
            " the release AND again at the destructive boundary,"
            " immediately before the directory and lease are released;"
            " a hold at either RETAINS the lease, the record and the"
            " ownership records. And (R20-2) every OTHER process scope"
            " of the workflow is shown absent the same way"
            " (`scope_release_hold`, retirement's own rule), so the"
            " lease is released only when every scope it retires can be"
            " retired.",
            "test_sessions_close_BEFORE_the_directory_is_deleted and"
            " test_a_degraded_close_RETAINS_the_directory_and_"
            "CANDIDACY; tests/test_mission_delivery.py::"
            "R20VerificationRetentionTests drives the verification"
            " holds, at the top and at the boundary, and"
            " R20BTaskScopeRetentionTests the task-scope holds",
            "the ONE `ProofSnapshot` derived once at the top of the release, handed to BOTH the archive and the close",
            "GATED BY: `verification_release_hold` and `scope_release_hold` returning None (R20-1, R20-2), then preservation returning ok (AF-1), then the close returning SESSIONS_RECLAIMED (AA-1), then both holds returning None again at the boundary (R20-1, R20-2), then `_sessions_absent_now` returning None — the sessions' absence re-proven, read-only, after those hold reads (R21-C, C-1; R21RemovalRetryTests.test_R21C_a/b) — then a FRESH `_boundary_admission` returning None immediately before the relinquish (R21-1). Each changes the RETURN, not a label; and the relinquish's own result decides release versus retention — only an OBSERVED absence releases the lease (R21-3: `PROBLEM_RELEASE_INCOMPLETE` retains it, `workspace removal pending`)."
        ),
        # Task 8 R21-1 / R21-3 / R21-A: the REMOVAL-ONLY re-entry of a release
        # that reached its destructive boundary without an observed removal.
        # Its one destructive call is the relinquish; it replays no effect
        # (revoke, preservation, close) and re-establishes every precondition.
        ("target_runtime/broker.py", "_retry_workspace_removal"): (
            "the release it re-enters already preserved the evidence and closed"
            " the sessions (a durable `workspace removal pending` receipt is the"
            " outstanding latest, the lease unreleased); before the relinquish"
            " both process-scope holds run, then the sessions' absence NOW is"
            " re-read by the first pass's own proofs (`_sessions_absent_now`,"
            " read-only, closing nothing), then a FRESH cleanup admission",
            "every process scope shown absent (`verification_release_hold`,"
            " `scope_release_hold`), the workflow's sessions ABSENT NOW — every"
            " canonical identity absent from one complete fresh listing and the"
            " same-lease child evidence exactly the canonical history (or, for"
            " any other record, the child-record proof's positive absence) — and"
            " the Mission cleanup admission granted (R21-1, R21-A)",
            "tests/test_mission_delivery.py::R21RemovalRetryTests: test_R21_3a/"
            "3b/3c (the removal not observed: lease kept, the retry removes once"
            " with no replay), test_R21A_a/b/c (a workspace live again, a"
            " contradictory same-lease child, an unavailable listing: zero"
            " removal, the lease kept, then exactly one removal once absence is"
            " restored)",
            "the record's own lease (`workspace.release` matches the recorded"
            " realpath) — no proof snapshot is carried across the passes; the"
            " sessions' absence is re-proven from fresh reads, never from the"
            " earlier pass's receipt",
            "GATED BY: both holds returning None, then `_sessions_absent_now`"
            " returning None, then `_boundary_admission` returning None — each a"
            " RETURN before the relinquish; and the relinquish's own result"
            " (`PROBLEM_RELEASE_INCOMPLETE` keeps the lease and the pending"
            " receipt) decides whether the completed receipt is written."
        ),
        # Task 8 startup correction: a corrective follow-up's start RETIRES
        # this workflow's earlier runtime; its one destructive call of its
        # own is the discard (`os.unlink`) of the persisted runtime state
        # that would otherwise hand the native start a stale id to close.
        # Its closes go through `close_proven_workspace` + `_bounded_close`
        # (their own entries).
        ("target_runtime/broker.py", "_retire_predecessor"): (
            "the runtime state is PRESERVED byte-exact and read back intact"
            " (`evidence_preservation.preserve_runtime_state`), every proven"
            " earlier runtime is CLOSED at its own admitted boundary, and a"
            " fresh complete listing shows EVERY canonical identity ABSENT —"
            " all before the discard, which is its own admitted boundary",
            "the state is this workflow's own (its projected workspace is a"
            " canonical identity and its supervisor an agent of it), it is"
            " byte-identical to the preserved copy (sha256 re-read at the"
            " discard), every earlier runtime is observed absent, and the"
            " fresh spawn-boundary admission holds (`admit_and_mark`)",
            "tests/test_grok_mission_loop.py::StartupRetirementTests::"
            "test_ST1_the_follow_up_retires_its_predecessor_and_the_real_start_proceeds,"
            " test_ST9a/b/c (unproven preservation: nothing closed or"
            " discarded), test_ST5/ST6 (a stale task at the boundary: nothing"
            " closed or discarded), test_ST10/ST11 (absence unobserved: not"
            " discarded)",
            "the `state` read once (`_lease_runtime_state`) — its bytes"
            " preserved, its sha256 compared against the re-read `current`"
            " before the unlink of that one path",
            "GATED BY: `preserve_runtime_state` returning ok, the absence"
            " listing showing no canonical identity, `current['sha256'] =="
            " state['sha256']`, and `admit_and_mark(...).ok` — each a RETURN"
            " that stops the retirement before the unlink."
        ),
        ("target_runtime/evidence_preservation.py", "preserve_runtime_state"): (
            "its own scratch `.partial` file is written, fsynced and LINKED"
            " into place (`os.link`, which never replaces an existing path)"
            " before the unlink, which removes ONLY that scratch name",
            "the unlinked path is this call's own `<archive>.partial` scratch"
            " copy — never the archive (a hard link keeps its bytes) and"
            " never evidence found at the archive path, which is classified"
            " (`inspect_runtime_state`) and kept",
            "tests/test_grok_mission_loop.py::RuntimeStateArchiveTests::"
            "test_missing_is_written_once_and_read_back_intact (no .partial"
            " left, the archive intact) and test_evidence_appearing_after_"
            "the_inspection_is_never_replaced (the appeared evidence kept"
            " byte-exact)",
            "the `temporary` path this call built from the archive path",
            "ADVISORY ONLY: the unlink is scratch cleanup of this call's own"
            " temporary name; its failure is ignored because nothing of the"
            " evidence depends on it."
        ),
        # Task 8 S-IV: the owned stop of a Mission-bound start. The
        # worker's close is handed IN (`close_fn`) and wrapped; the wrapper
        # is the nested `close` below, and `_bounded_close` only builds it.
        ("target_runtime/broker.py", "_bounded_close"): (
            "it BUILDS the bounded close and calls nothing destructive"
            " itself: the wrapper it returns is handed to"
            " `close_proven_workspace`, whose `prove_ownership` runs before"
            " the wrapper is reached; inside the wrapper (the nested"
            " `close` entry) `INFLIGHT_CLOSES.claim` runs before"
            " `close_fn`",
            "the workspace was PROVEN this workflow's own by"
            " `close_proven_workspace`, AND this process holds no"
            " still-running close of the same workspace — one abandoned at"
            " `OWNED_STOP_WAIT_SECONDS` stays claimed until it returns",
            "tests/test_mission_controls.py::RIntegrationTests::test_R14_"
            "the_owned_stop_is_bounded_and_never_closes_twice executes it:"
            " the hanging close is bounded and stays claimed, the later"
            " pass's claim is REFUSED so exactly one close is issued, and"
            " the late return releases the claim before the next pass"
            " confirms by observed absence",
            "the `workspace_id` `close_proven_workspace` hands to the"
            " returned close — the one its `ProofSnapshot` proved and"
            " revalidated against a fresh live reading; the wrapper derives"
            " no identity of its own",
            "GATED BY: `INFLIGHT_CLOSES.claim(workspace_id)` returning True"
            " inside the returned close; False raises"
            " `OwnedStopBoundExceeded` and `close_fn` is never called."
        ),
        ("target_runtime/broker.py", "close"): (
            "the NESTED close built by `_bounded_close` (the Mission start"
            " guard's `close` method calls nothing in this domain at depth"
            " zero): `INFLIGHT_CLOSES.claim` runs before `close_fn`, and"
            " `close_proven_workspace`'s ownership proof runs before this"
            " close is reached. Each `release` here releases the in-flight"
            " claim this call took — after `close_fn` returned or raised, or"
            " from the late return of a close abandoned at the bound — and"
            " destroys nothing",
            "no earlier close of the same workspace is still running in"
            " this process (the claim succeeded), on a workspace"
            " `close_proven_workspace` proved this workflow's own",
            "tests/test_mission_controls.py::RIntegrationTests::test_R14_"
            "the_owned_stop_is_bounded_and_never_closes_twice: the first"
            " claim succeeds and its close hangs past the bound, the later"
            " pass's claim fails (`issued` stays ONE close), and only the"
            " late return releases the claim",
            "the `workspace_id` argument `close_proven_workspace` passes —"
            " the proven, revalidated workspace; the same id is claimed,"
            " closed and released",
            "GATED BY: `INFLIGHT_CLOSES.claim(workspace_id)` returning True;"
            " False raises `OwnedStopBoundExceeded` before `close_fn`."
        ),
        ("target_runtime/broker.py", "_recover_pending_stops"): (
            "the start's `owner_ref` must be THIS owner's, then"
            " `RETAINED_HANDOVERS.lock(key).acquire(False)` must succeed"
            " before `_recover_start` runs; `lock.release()` runs only in the"
            " `finally` of that acquired block. The `release` is a mutex"
            " release matched by name and destroys nothing; the destruction"
            " it orders — the owned close — runs one level down"
            " (`_recover_start` → `_owned_stop` → `close_proven_workspace`)"
            " INSIDE the acquired lock",
            "no late thread is handing the SAME start over right now — the"
            " late thread holds that per-start lock through its owned stop,"
            " so the owner pass skips the start instead of closing a second"
            " time — and the start is this owner's own",
            "tests/test_mission_engagement.py::StartClaimTests::test_D27_"
            "late_thread_and_owner_pass_race_close_once executes it: the"
            " owner pass's non-blocking acquire FAILS while the late thread"
            " is inside its close, it closes nothing, and exactly one close"
            " results; test_D28_wrong_owner_cannot_consume_the_retained_"
            "entry pins the owner check",
            "the one lock object `RETAINED_HANDOVERS.lock(key)` returned for"
            " this iteration's `(owner_ref, start_id)` and acquired by this"
            " iteration; the release consumes exactly that acquisition",
            "GATED BY: `lock.acquire(False)` returning True — False"
            " `continue`s past the start, and the release is unreachable"
            " without the acquire (it is the `finally` of the acquired"
            " `try`)."
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
            " whose cleanup halted keeps its records — or (Task 8 R20-B,"
            " Addendum B) from `_retry_scope_retirement`, the"
            " retirement-only re-entry for a lease that release already"
            " released with a scope refused (an outstanding `process scope"
            " retained` receipt), after both holds return None again;"
            " nothing else is re-run",
            "the scope carries a VALID ASSIGNMENT naming exactly this"
            " control repository and this workflow (AG-1/AG-3), and"
            " ABSENCE is established from its ownership records, read"
            " strictly (R20-1): no CORROBORATED group recorded inside it"
            " is still running (AR-3), no group survives with its"
            " recorded leader gone, every root is stamped, and every"
            " record is readable. Any check failing REPORTS the scope"
            " and leaves it. The release reaches this only after"
            " `scope_release_hold` showed every scope retirable at its"
            " boundary (R20-2); a refusal that still happens writes the"
            " `process scope retained` receipt, which keeps the record —"
            " the scope's only recovery owner — from pruning until a"
            " retry that refuses nothing writes `process scope settled`",
            "RetireProcessScopesTests: test_a_scope_with_a_LIVE_group"
            "_is_REFUSED_and_KEPT and test_another_workflows_scope_is"
            "_NEVER_retired drive both refusals with real records;"
            " test_retirement_happens_THROUGH_the_release drives the"
            " ordering through `ACTION_RELEASE`; the R20-1 retirement"
            " cases drive the leaderless, unstamped and unreadable"
            " refusals",
            "the ASSIGNMENT CREDENTIAL written before the spawn — the"
            " same credential recovery validates, never the directory"
            " name",
            "GATED BY: the release returning ok (or, for the retry, the"
            " released lease, the outstanding receipt, both holds returning"
            " None and a fresh cleanup admission), then `validate_assignment`"
            " returning an identity, then `retirement_refusal` returning None,"
            " both bracketed by unchanged, bound `_ownership_evidence` (Task 8"
            " R25-1), then — for a Mission-origin record — the Broker's"
            " admission taken FRESH for EACH removal and HELD across it"
            " (`admit(effect)`; R20-B Addendum C, C-1b; a refusal stops the"
            " retirement), inside which `remove_scope` re-reads that evidence."
            " Each changes what is removed, not a label. `retired` is decided"
            " by the OBSERVED post-condition (C-2: the directory and its"
            " credential seen absent), never by the attempt."
        ),
        # Task 8 R25-1: the scope's removal, run INSIDE the held admission.
        ("target_runtime/process_ownership.py", "remove_scope"): (
            "`retire_workflow_scopes` reaches it only through the caller's"
            " admission, HELD across it (`admit(effect)`; the Broker's"
            " `MissionEffectGate.admit_cleanup_held`, under the Mission store lock),"
            " after the scope's ownership readers ran bracketed by unchanged,"
            " bound evidence; inside it, the evidence is re-read before the"
            " credential's removal and the `rmtree`",
            "the Mission cleanup admission granted and still held, AND the"
            " scope's ownership evidence (credential, binding key, scope entry,"
            " owned-root prefix, each root and its records) bound by the"
            " bounded, non-waiting reader and EQUAL to what the readers' verdict"
            " rested on",
            "R25HeldRetirementTests drives a scope dangled, an ancestor dangled"
            " and a record swapped DURING the admission (zero credential"
            " removals, zero deletions, each counted); tests/test_mission_"
            "delivery.py::R25HeldAdmissionRouteTests drives a Mission hold and"
            " a source failure arriving during the ownership reads through the"
            " production Broker",
            "the `observed` evidence value bound before the admission, compared"
            " to the one re-read inside it; the `directory` and `credential`"
            " the retirement derived from the validated scope",
            "GATED BY: `_unbound_reason` returning None for the bracketed"
            " evidence before the admission (else it is never reached), then the"
            " held admission running the effect at all (a refusal never calls"
            " it), then the re-read evidence equal to that bound `observed` —"
            " a difference returns a refusal before `_remove_credential` and"
            " `rmtree`."
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
        ("target_runtime/process_ownership.py", "_remove_credential"): (
            "its only caller, `retire_workflow_scopes`, reaches it after the"
            " scope's credential VALIDATED (`workflow_scopes`) and"
            " `retirement_refusal` returned None — or, for a credential whose"
            " directory is already gone, after `_dangling_credentials` READ"
            " that credential's own record — and, for a Mission-origin"
            " record, after the Broker's `admit` returned None for this"
            " removal (Task 8 R20-B, Addendum C)",
            "the credential names exactly this workflow and control"
            " repository by its integrity-bound record (never by a file name"
            " alone), and the scope it attributes is absent or established"
            " retirable",
            "RetireProcessScopesTests: test_R20B_a_credential_that_cannot_be"
            "_removed_is_never_retired drives the kept and the re-attempted"
            " credential; R20BTaskScopeRetentionTests.test_RS4g_a_credential"
            "_that_cannot_be_removed_never_settles_and_never_grows drives it"
            " through the release's retry",
            "the one `assignment_path` the caller derived from the validated"
            " scope name, or the path `_dangling_credentials` read the record"
            " from — the same file is unlinked and then observed",
            "GATED BY: the caller's validation and `retirement_refusal` /"
            " `_dangling_credentials` selection, then the caller's admission"
            " HELD across it with the evidence re-read unchanged inside it"
            " (Task 8 R25-1); its own return is the OBSERVED post-condition"
            " (None only when `lstat` raises FileNotFoundError afterwards AND"
            " the traversal establishes genuine absence, Task 8 R25-2), which"
            " decides retired versus refused."
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
            "the ledger is consulted, then the CURRENT group is proven (Task 8"
            " R28-1), before any signal",
            "the group id is PRESENT in the owner ledger this"
            " component wrote, AND the live group by that number is the one"
            " recorded: this process HOLDS its leader uncollected, or the"
            " recorded leader is alive and corroborated by its owned root",
            "test_reap_owned_refuses_a_group_the_ledger_does_not_name;"
            " R28CurrentGroupOwnershipTests (test_R28_1b, test_R28_1c,"
            " test_R28_1d, test_R28_1e: ZERO signals on an unproven or"
            " unreadable current group)",
            "the owner ledger entry for that exact group id, and the hold or"
            " the owned root's corroboration read fresh at the action boundary,"
            " inside ONE span (the leader's hold lock, then its handle's"
            " collection lock) held from the proof to the signal",
            "GATED BY: ledger membership AND the current-group proof (held, or"
            " corroborated), both checked before the signal; the span's locks"
            " acquired within a bound, or the reap refuses."
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
        # Task 8 R28-1: the BOUNDED membership observation's own cleanup.
        ("target_runtime/process_ownership.py", "_end_observer"): (
            "the membership observer was STARTED by `_live_members_besides` and"
            " has not answered within its bound (or could not be read) before it"
            " is killed",
            "the process signalled is the observer's OWN child, not collected:"
            " `Popen.kill` polls first and signals only a child its handle has not"
            " collected, so its pid cannot have been reused",
            "R28CurrentGroupOwnershipTests.test_R28_1n (a STALLED observer: the"
            " ONE signal is the SIGKILL to the observer's own pid, observed"
            " ended; nothing reaches the held group)",
            "the observer's own `Popen` handle, the one `_live_members_besides`"
            " created",
            "GATED BY: the observation's bound expiring (`TimeoutExpired`) or its"
            " read failing — a listing read in time never reaches it."
        ),
        ("target_runtime/process_ownership.py", "disarm_hold"): (
            "the hold's lock is ACQUIRED within its bound before the decision;"
            " this release (a lock release, not a process or file) follows it",
            "nothing need be proven: it releases a lock this same call acquired,"
            " on every exit from the decision, so the critical section is never"
            " left held and a later observation can progress. Listed rather than"
            " exempted: the scan matches the name `release`",
            "R28CurrentGroupOwnershipTests.test_R28_1p (the lock held elsewhere:"
            " the disarm decides nothing within its bound) and test_R28_1n (a"
            " later valid observation progresses after a stalled one)",
            "nothing: the lock it releases is the one it acquired",
            "ADVISORY ONLY, and the reason: it releases a lock this call holds,"
            " which destroys no state, so there is no safety value to gate on."
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
        # Task 8 R27-1: the stamp writer's ATOMIC replacement discards its OWN
        # replacement when it fails BEFORE the publication; the record path
        # itself is never unlinked, and a published record is never undone.
        ("target_runtime/spawn_stamp.py", "_write_record"): (
            "the replacement is created by THIS call, exclusively"
            " (`O_CREAT | O_EXCL`, `O_NOFOLLOW` where provided), immediately"
            " before the write it unwinds; the record path itself is never"
            " unlinked",
            "the name unlinked is this call's own replacement, on the failure"
            " path BEFORE its publication (its write, its fsync, the last"
            " examination or the rename failed), so no record that existed"
            " before the call is destroyed, and no published record is ever"
            " rolled back (after the rename: `PublicationUnproven`, no"
            " unlink). The LIMIT, stated: the unlink is by NAME, so an object"
            " substituted at this call's hidden, random replacement name would"
            " lose that name",
            "EXECUTED from the writer's own call trace (`stamp_faults`):"
            " R27AtomicStampWriterTests.discarded_own_replacement_only in"
            " test_R27_W1, W2, W4, W9 (last examination) and W11 — every unlink"
            " names this call's own replacement, never the record path, and no"
            " rename published it, the record UNTOUCHED (bytes and inode);"
            " .nothing_undone in test_R27_W7 and W8, and"
            " R26NonWaitingRecordTests.test_R27_1d — after the LAST rename no"
            " unlink, rename, link or write: the published record stays",
            "the replacement path it computed and created itself, moments"
            " earlier",
            "GATED BY: the failure of the write it is unwinding — the unlink"
            " runs only in the handler around the replacement's write, fsync,"
            " last examination and rename, never after the rename returned."
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

    def domain(self, sources=None):
        """Every destructive function in the committed production surface."""
        import ast
        if sources is None:
            paths = [
                path for path in DI_REMOTE_2_PRODUCTION_PYTHON
                if path.split("/")[0] in (
                    "target_runtime", "herdr", "workflow_authority",
                    "codex_gateway",
                )
            ]
            sources = _committed_sources(paths)
        found = {}
        for path, source in sources.items():
            tree = ast.parse(source)
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

    def test_the_detector_bites_without_worktree_or_self_prose(self):
        import ast
        specimen = (
            "import os\n"
            "def synthetic_violation():\n"
            "    os.unlink('owned-only-in-the-specimen')\n"
        )
        key = ("target_runtime/specimen.py", "synthetic_violation")
        domain = self.domain({key[0]: specimen})
        self.assertIn(key, domain)
        self.assertNotIn(key, self.ORDERING)
        prose_only = (
            "def harmless():\n"
            "    '''os.unlink and remove are words, not calls.'''\n"
            "    return True\n"
        )
        self.assertEqual(
            self.domain({"target_runtime/prose.py": prose_only}), {},
        )
        from unittest.mock import patch
        with patch.object(
            subprocess, "run",
            side_effect=AssertionError("dirty state was consulted"),
        ):
            self.assertTrue(self.domain())

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
        body spells one of the listed destructive calls, in the committed
        DI-REMOTE-2 production surface, at depth ZERO — so the number is a
        FLOOR.

        This enumeration is fast structural feedback in front of
        `test_sessions_close_BEFORE_the_directory_is_deleted` and
        `test_the_unfreeze_is_recorded_before_the_file_is_removed`,
        which EXECUTE the orders being claimed.

        Named as outside it, each leaving the count a floor: a destructive step reached only through a helper at depth one or
        beyond, one outside the committed surface, one reached through an alias
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
        ("tests/test_ownership.py", "kill_group"):
            "NOT PINNED, a test-cleanup HELPER (RetireProcessScopesTests,"
            " Task 8 R20-1): it SIGKILLs only the process group the same"
            " test spawned through `spawn_owned` into its own private"
            " scope — the leaderless fixture's surviving descendant, the"
            " reused-id fixture's leader — at cleanup, where an assertion"
            " cannot fail the test whose leak it prevents. Pinning it to"
            " the reap_group standard would mean spawning something for"
            " it to reap; a leak it fails to catch surfaces in the"
            " suite-level PPID-1 observation (E-5).",
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
        # Task 8 R28-1: the HELD leader's collector.
        ("target_runtime/process_ownership.py", "_collect_leader"):
            "R28CurrentGroupOwnershipTests: test_R28_1a (the held leader is"
            " collected only AFTER the reap's signal, through its handle, with"
            " its TRUE status 7, and is no zombie after), test_R28_1d (collected"
            " by the restored reap, status 0) and test_R28_1g (an exited leader"
            " with nothing alive under its number is collected at the disarm;"
            " one with a live descendant is NOT) — each by authored assertion"
            " on a real child of the test.",
        # Task 8 R28-1: the membership observer's own cleanup (its OWN child).
        ("target_runtime/process_ownership.py", "_end_observer"):
            "R28CurrentGroupOwnershipTests.test_R28_1n: a STALLED observer is"
            " killed through its own handle within the observation's bound and"
            " observed ended (return code -SIGKILL), its SIGKILL the ONE signal"
            " recorded — by authored assertion on a real child of the test.",
        ("tests/test_ownership.py", "settle"):
            "NOT PINNED, a test-cleanup HELPER (R28CurrentGroupOwnershipTests,"
            " Task 8 R28-1): it reaps only this case's own groups — through the"
            " production reaper while this process HOLDS the leader, otherwise"
            " a SIGKILL to the group ONLY while every live member is a"
            " descendant the case started, proven by pid AND start time read"
            " then — and it then ASSERTS each group observed ended, so its own"
            " failure fails the case.",
        ("tests/test_ownership.py", "settle_kids"):
            "NOT PINNED, a test-cleanup HELPER nested in test_R28_1i: it"
            " SIGKILLs only a descendant that case's turn started, while its pid"
            " AND start time read then match the booked ones; none should remain"
            " (the case asserts the turn's group gone), and a leak it fails to"
            " catch surfaces in the suite-level PPID-1 observation (E-5).",
        ("tests/test_ownership.py",
         "test_R28_1i_the_ROLE_TURN_runner_reaps_what_its_turn_left_POSITIVELY"):
            "in this domain only through its nested cleanup `settle_kids`"
            " (above); the test body itself signals nothing.",
    }

    def domain(self, sources=None):
        """Every reaper function on the committed DI-REMOTE-2 surface."""
        import ast
        if sources is None:
            paths = [
                path for path in (
                    DI_REMOTE_2_PRODUCTION_PYTHON
                    + DI_REMOTE_2_TEST_PYTHON
                )
                if path.split("/")[0] in (
                    "tests", "target_runtime", "herdr",
                    "workflow_authority",
                )
            ]
            sources = _committed_sources(paths)
        found = {}
        for path, source in sources.items():
            tree = ast.parse(source)
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

    def test_the_detector_bites_without_worktree_or_self_prose(self):
        specimen = (
            "import os\n"
            "def synthetic_reaper(pid):\n"
            "    os.kill(pid, 15)\n"
        )
        key = ("target_runtime/specimen.py", "synthetic_reaper")
        domain = self.domain({key[0]: specimen})
        self.assertIn(key, domain)
        self.assertNotIn(key, self.PINNED)
        prose_only = (
            "def harmless():\n"
            "    '''os.kill and waitpid are words, not calls.'''\n"
            "    return True\n"
        )
        self.assertEqual(
            self.domain({"target_runtime/prose.py": prose_only}), {},
        )
        from unittest.mock import patch
        with patch.object(
            subprocess, "run",
            side_effect=AssertionError("dirty state was consulted"),
        ):
            self.assertTrue(self.domain())

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
        terminating call, in the committed DI-REMOTE-2 surface, at depth ZERO
        — so the number is a FLOOR, not a total.

        Named as outside it, each leaving the count a floor: a reaper
        that terminates only through a helper it calls (depth one or
        beyond), a reaper outside the committed surface, a call reached through
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

    The instance fixes are not the deliverable; this closure is. It walks the committed DI-REMOTE-2 test files, finds the calls that can start a
    process, and requires each one to be either routed through
    `target_runtime.process_ownership` or covered by a BLOCKING form
    whose scope: it returns only after its child has exited.

    Source is the only feasible level for the enumeration, and the
    reason is that its subject is which CALL SITES exist in a body of
    text; the behavioural half it fronts is
    `ProcessTreeOwnershipTests`, which executes the construct against
    a real tree, and the harness-level `surviving_owned_groups` check.
    """

    #: Names that can start a process. Applied to the explicit committed
    #: surface rather than to whatever happens to be dirty today.
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

    def sites(self, sources=None):
        """Spawn-capable calls on the committed DI-REMOTE-2 test surface."""
        import ast
        if sources is None:
            sources = _committed_sources(DI_REMOTE_2_TEST_PYTHON)
        found = []
        for path, source in sources.items():
            tree = ast.parse(source)
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

    def test_the_detector_bites_without_worktree_or_self_prose(self):
        specimen = (
            "import subprocess\n"
            "def synthetic_spawn():\n"
            "    return subprocess.Popen(['child'])\n"
        )
        key = ("tests/specimen.py", "subprocess.Popen")
        sites = self.sites({key[0]: specimen})
        self.assertEqual(len(sites), 1)
        self.assertEqual((sites[0][0], sites[0][2]), key)
        self.assertNotIn(key, self.DECLARED)
        prose_only = (
            "def harmless():\n"
            "    '''subprocess.Popen is prose, not a call.'''\n"
            "    return True\n"
        )
        self.assertEqual(self.sites({"tests/prose.py": prose_only}), [])
        from unittest.mock import patch
        with patch.object(
            subprocess, "run",
            side_effect=AssertionError("dirty state was consulted"),
        ):
            self.assertTrue(self.sites())

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
        on the listed names, in the committed test surface, at depth ZERO —
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
        # The store holds its binding key, as any store a legitimate
        # assignment was ever written to does — installed by the WRITER's
        # accessor. (Task 8 R22-A: a proof read no longer installs one;
        # against a store with NO key the forgery's proof is UNAVAILABLE,
        # never FORGED — FORGED is reserved for a binding that fails against
        # a key that was present and read.)
        proc_module._binding_key(self.base)
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


class R21UnavailableObservationTests(unittest.TestCase):
    """Task 8 R21-2 / R21-B: recovery REPORTS every observation it cannot
    make as UNAVAILABLE, never as absent — an unreadable scope base, scope
    entry, owned-root prefix or owned root; a group record that does not
    decode (BYTE-level, TEXTUAL and out-of-range: separate cases, so the
    failure modes cannot collapse into one); a corroboration record that
    cannot be read or decoded — at ``_scope_directories``, ``owned_roots`` /
    ``owned_roots_observed``, ``recover_attributed`` and the CLI report. It
    acts on nothing such an observation covers: no group is signalled, no
    ownership is inferred, nothing is deleted (the retirement refuses too).
    Once the source reads again, recovery acts on the REAL records exactly
    once, and a further recovery repeats nothing.

    The scope is the production assignment (``assign_scope``) holding a
    REAL live stamped group, as AG-4 builds it (``ScopeAssignmentCredential
    Tests``' fixtures, borrowed without its tests); the damage is permission
    bits or record bytes, restored before the occupant's reap."""

    _AG = ScopeAssignmentCredentialTests
    assert_no_survivors = _AG.assert_no_survivors
    live_group_in = _AG.live_group_in
    release_occupant = _AG.release_occupant

    CONTROL = "/control/repo"
    OWNER = (proc_module.OWNER_TYPE_WORKFLOW, proc_module.control_digest(CONTROL),
             "wf-live", "t-live")
    UNAVAILABLE = proc_module.OBSERVATION_UNAVAILABLE

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        self._AG.setUp(self)
        self.scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL, "wf-live", "t-live",
            base=self.base)
        self.pgid = self.live_group_in(self.scope)
        [(self.root, pgid)] = proc_module.owned_roots(self.scope)
        self.assertEqual(pgid, self.pgid)
        self.credential = proc_module.assignment_path(os.path.basename(self.scope), self.base)

    # -- helpers -------------------------------------------------------------------

    def chmod(self, path, mode):
        """``path`` set to ``mode``; ``restore()`` puts it back (and so does the
        cleanup, which runs BEFORE the occupant's reap and the base's removal)."""
        original = os.stat(path).st_mode & 0o7777
        os.chmod(path, mode)
        self.addCleanup(os.chmod, path, original)
        self.restore = lambda: os.chmod(path, original)

    def rewrite(self, path, data):
        """``path``'s bytes replaced by ``data``; ``restore()`` writes the
        original bytes back."""
        with open(path, "rb") as handle:
            original = handle.read()

        def put(content):
            with open(path, "wb") as handle:
                handle.write(content)
        put(data)
        self.addCleanup(put, original)
        self.restore = lambda: put(original)

    def recovered(self):
        return proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                              current_owners={self.OWNER})

    def cli_lines(self, report):
        import io
        from target_runtime import cli as cli_module
        stream = io.StringIO()
        cli_module.report_inherited_recovery(report, stream=stream)
        return stream.getvalue().splitlines()

    def unavailable_lines(self, unavailable):
        return ["dirun: recovery observations UNAVAILABLE: %d (reported, never read as"
                " absent; nothing they cover was acted on)" % len(unavailable)] + [
            "dirun: UNAVAILABLE (%s): %s" % (reason, path) for path, reason in unavailable]

    def left_alone(self, unavailable, refused, results=None):
        """Recovery with the observation unavailable: EXACTLY ``unavailable``
        reported (and printed), EXACTLY ``results`` (default none: nothing
        attributed by a guess, nothing reaped), the live group still running;
        and the retirement — the deletion route — refuses EXACTLY ``refused``
        and retires nothing."""
        report = self.recovered()
        self.assertEqual(report.unavailable, unavailable)
        self.assertEqual(report[0], [] if results is None else results)
        self.assertEqual(report[1], [])
        self.assertTrue(proc_module._group_alive(self.pgid), "an unavailable observation was acted on")
        lines = self.cli_lines(report)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines(unavailable))
        retired, refusals = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live",
                                                               base=self.base)
        self.assertEqual((retired, refusals), ([], refused))
        self.assertTrue(proc_module._group_alive(self.pgid))
        return lines

    def recovered_once_restored(self):
        """The source restored: nothing was deleted (the scope, its credential
        and the root are all present), recovery acts on the REAL records —
        exactly one reap of the live group, every observation made — and a
        further recovery repeats nothing."""
        self.restore()
        for path in (self.scope, self.credential, self.root):
            self.assertTrue(os.path.exists(path), path)
        report = self.recovered()
        self.assertEqual(report.unavailable, [])
        self.assertEqual(report[1], [])
        self.assertEqual(len(report[0]), 1, report[0])
        identity, reaped, stuck, unstamped, uncorroborated = report[0][0]
        self.assertEqual(tuple(identity), self.OWNER)
        self.assertEqual((reaped, stuck, unstamped, uncorroborated), ([self.pgid], [], [], []))
        self.assertFalse(proc_module._group_alive(self.pgid))
        self.assertEqual(self.unavailable_lines([])[0],
                         [line for line in self.cli_lines(report) if "UNAVAILABLE" in line][0])
        again = self.recovered()
        self.assertEqual((again[0], again[1], again.unavailable), ([], [], []))

    # -- R21-2: unreadable base, entry, nested prefix, root ------------------------

    def test_R21_2a_an_unreadable_scope_base_is_unavailable_never_absent(self):
        prefix = proc_module.owned_root_base(self.base)
        self.chmod(prefix, 0)
        gap = [(prefix, "%s: the scope base cannot be read (PermissionError)" % self.UNAVAILABLE)]
        self.assertEqual(proc_module._scope_directories(self.base), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.classify_scopes(self.base)
        self.assertEqual(raised.exception.unavailable, gap)
        self.left_alone(gap, [(prefix, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    def test_R21_2b_an_unreadable_scope_entry_is_unavailable_never_absent(self):
        prefix = proc_module.owned_root_base(self.base)
        names = sorted(os.listdir(prefix))
        self.assertIn(os.path.basename(self.scope), names)
        self.chmod(prefix, 0o400)                       # listable, entries not examinable
        gap = [(os.path.join(prefix, name),
                "%s: the scope cannot be read (PermissionError)" % self.UNAVAILABLE)
               for name in names]
        self.assertEqual(proc_module._scope_directories(self.base), ([], gap))
        self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    def test_R21_2c_an_unreadable_owned_root_prefix_is_unavailable_never_absent(self):
        nested = proc_module.owned_root_base(self.scope)
        self.chmod(nested, 0)
        gap = [(nested, "%s: the owned-root prefix cannot be read (PermissionError)"
                % self.UNAVAILABLE)]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.owned_roots(self.scope)
        self.assertEqual(raised.exception.unavailable, gap)
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    def test_R21_2d_an_unreadable_owned_root_is_unavailable_never_absent(self):
        nested = proc_module.owned_root_base(self.scope)
        names = sorted(os.listdir(nested))
        self.chmod(nested, 0o400)
        gap = [(os.path.join(nested, name),
                "%s: the owned root cannot be read (PermissionError)" % self.UNAVAILABLE)
               for name in names]
        self.assertIn(self.root, [path for path, _reason in gap])
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable):
            proc_module.owned_roots(self.scope)
        self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    def test_R21_2e_an_unreadable_group_record_is_unavailable_never_unstamped(self):
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        self.chmod(record, 0)
        gap = [(record, "%s: the group record cannot be read (PermissionError)"
                % self.UNAVAILABLE)]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    # -- R21-B: a group record that does not decode, three ways -------------------

    def does_not_decode(self, data):
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        self.rewrite(record, data)
        gap = [(record, "%s: the group record does not decode (not a group id)"
                % self.UNAVAILABLE)]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable):
            proc_module.owned_roots(self.scope)
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.recovered_once_restored()

    def test_R21B_a_group_record_of_invalid_UTF8_BYTES_is_unavailable_never_raised(self):
        """BYTE-level: a text read raised UnicodeDecodeError — a ValueError,
        not an OSError — past both read handlers, out of recovery and the
        CLI."""
        with self.assertRaises(UnicodeDecodeError):
            b"\xff\xfe\x80".decode("utf-8")
        self.does_not_decode(b"\xff\xfe\x80")

    def test_R21B_a_group_record_of_non_numeric_TEXT_is_unavailable(self):
        """TEXTUAL: valid UTF-8 that is not a number."""
        self.does_not_decode(b"abc")

    def test_R21B_a_group_record_naming_no_possible_group_is_unavailable(self):
        """Out of range: ``int()`` accepted it and ``os.killpg`` then raised
        OverflowError (not an OSError) out of ``_group_alive``."""
        self.does_not_decode(str(proc_module.MAX_RECORDED_GROUP_ID + 1).encode("ascii"))

    def test_R21B_a_FORBIDDEN_id_DECODES_and_the_guard_refuses_it(self):
        """A valid but FORBIDDEN group id — 0, 1, this process's own group —
        is not an UNDECODABLE record. Each DECODES (reported as observed,
        never unavailable), and recovery's own guard — which runs AFTER the
        decode — refuses it: zero ``os.killpg`` calls, nothing recovered or
        stuck. Kept distinct from the does-not-decode cases above, so a
        tightening of the decoder cannot bypass the guard by raising first."""
        from unittest.mock import patch
        guard = os.path.join(self.base, "guard")
        roots = []
        for pgid in (0, 1, os.getpgrp()):
            root = proc_module.create_owned_root("own-guard-%d" % pgid, guard)
            with open(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE), "w") as handle:
                handle.write(str(pgid))
            roots.append((root, pgid))
        self.assertEqual(proc_module.owned_roots_observed(guard), (sorted(roots), []))
        self.assertEqual(proc_module.owned_roots(guard), sorted(roots))
        calls, unavailable = [], []
        with patch.object(os, "killpg", side_effect=lambda *a: calls.append(a)):
            recovered, stuck, unstamped, uncorroborated = proc_module.recover_orphans(
                guard, settle_seconds=1.0, unavailable=unavailable)
        self.assertEqual(calls, [], "recovery signalled a group it must never signal")
        self.assertEqual((recovered, stuck, unstamped, uncorroborated, unavailable),
                         ([], [], [], [], []))

    def test_R21B_an_EMPTY_group_record_is_a_root_not_yet_stamped(self):
        """The stamp opens its record, then writes it: an empty record is that
        window (or a crash inside it) — UNSTAMPED, as ``owned_roots`` always
        read it, reported and left alone; not unavailable."""
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        self.rewrite(record, b"")
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([(self.root, None)], []))
        identity = proc_module.validate_assignment(self.scope, base=self.base)[0]
        self.left_alone([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)],
                        results=[(identity, [], [], [self.root], [])])
        self.recovered_once_restored()

    # -- R21-B: a corroboration record that cannot be read or decoded --------------

    def corroboration_unavailable(self, damage, cause):
        nonce = os.path.join(self.root, proc_module.OWNED_ROOT_NONCE_FILE)
        damage(nonce)
        with self.assertRaises((OSError, UnicodeDecodeError)):
            proc_module.group_is_ours(self.root)
        reason = "%s (%s)" % (proc_module.UNCORROBORATED_UNAVAILABLE, cause)
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        identity = proc_module.validate_assignment(self.scope, base=self.base)[0]
        lines = self.left_alone([(self.root, reason)],
                                [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)],
                                results=[(identity, [], [], [], [(self.root, self.pgid, reason)])])
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (self.pgid, self.root, reason), lines)
        self.recovered_once_restored()

    def test_R21B_a_nonce_of_invalid_UTF8_BYTES_is_unavailable_never_raised(self):
        self.corroboration_unavailable(lambda path: self.rewrite(path, b"\xff\xfe\x80"),
                                       "UnicodeDecodeError")

    def test_R21B_an_unreadable_nonce_is_unavailable_never_no_nonce(self):
        self.corroboration_unavailable(lambda path: self.chmod(path, 0), "PermissionError")


class R22TruthfulRecoveryTests(unittest.TestCase):
    """Task 8 R22-1 / R22-2 — R21-2's invariant at three further layers (leader
    corroboration, the workflow store, credential access): a source that
    could NOT BE READ is UNAVAILABLE — never ABSENT, never STALE, never
    "missing" or "malformed", never silently omitted — and nothing it covers
    is acted on: ZERO effects (no reap called, no signal but signal 0, no
    scope or credential deleted, no ownership inferred). Once the source
    reads again, recovery acts on the REAL records exactly once.

    R21UnavailableObservationTests' fixture (a production assignment holding
    a REAL live stamped group), borrowed without its tests."""

    _R21 = R21UnavailableObservationTests
    _AG = ScopeAssignmentCredentialTests
    CONTROL = _R21.CONTROL
    OWNER = _R21.OWNER
    UNAVAILABLE = _R21.UNAVAILABLE
    setUp = _R21.setUp
    assert_no_survivors = _AG.assert_no_survivors
    live_group_in = _AG.live_group_in
    release_occupant = _AG.release_occupant
    chmod = _R21.chmod
    rewrite = _R21.rewrite
    recovered = _R21.recovered
    cli_lines = _R21.cli_lines
    unavailable_lines = _R21.unavailable_lines
    recovered_once_restored = _R21.recovered_once_restored

    # -- helpers -------------------------------------------------------------------

    def no_effects(self):
        """A context in which every effect is COUNTED and must stay zero: the
        recorded-root reaper, and any signal other than signal 0."""
        import contextlib
        from unittest.mock import patch
        reaps, signals = [], []
        real_reap = proc_module.reap_group_by_recorded_root
        real_killpg, real_kill = os.killpg, os.kill

        def reap(*args, **kwargs):
            reaps.append(args)
            return real_reap(*args, **kwargs)

        def killpg(pgid, sig):
            if sig != 0:
                signals.append(("killpg", pgid, sig))
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                signals.append(("kill", pid, sig))
            return real_kill(pid, sig)

        @contextlib.contextmanager
        def counted():
            with patch.object(proc_module, "reap_group_by_recorded_root", reap), \
                    patch.object(os, "killpg", killpg), patch.object(os, "kill", kill):
                yield
            self.assertEqual((reaps, signals), ([], []), "an unavailable source was acted on")
        return counted()

    def leader_query_fails(self):
        """The leader's start-time query FAILS (the `ps` it runs cannot be
        started) while the leader lives — every other subprocess runs."""
        from unittest.mock import patch
        real_run = stamp_module.subprocess.run

        def run(argv, *args, **kwargs):
            if argv and argv[0] == "ps":
                raise OSError("the leader query is refused")
            return real_run(argv, *args, **kwargs)
        return patch.object(stamp_module.subprocess, "run", run)

    def identity(self):
        return proc_module.validate_assignment(self.scope, base=self.base)[0]

    def intact(self):
        for path in (self.scope, self.credential, self.root):
            self.assertTrue(os.path.exists(path), path)
        self.assertTrue(proc_module._group_alive(self.pgid))

    # -- R22-1: a live group whose leader query FAILED -------------------------------

    def test_R22_1a_a_failed_leader_query_is_unresolved_and_never_signalled(self):
        reason = proc_module.UNCORROBORATED_LEADER_UNAVAILABLE
        identity = self.identity()
        with self.leader_query_fails():
            self.assertEqual(proc_module.group_is_ours(self.root), (None, reason))
            self.assertTrue(proc_module.scope_has_live_group(self.scope))
            with self.no_effects():
                report = self.recovered()
                retired, refused = proc_module.retire_workflow_scopes(
                    self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(report[0], [(identity, [], [], [], [(self.root, self.pgid, reason)])])
        self.assertEqual(report[1], [])
        self.assertEqual(report.unavailable, [(self.root, reason)])
        lines = self.cli_lines(report)
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (self.pgid, self.root, reason), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([(self.root, reason)]))
        self.assertEqual((retired, refused),
                         ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.intact()
        self.restore = lambda: None                    # the query answers again
        self.recovered_once_restored()

    def test_R22_1c_an_undeterminable_leader_is_unresolved_never_claimed_alive(self):
        """The leader query FAILS and whether the recorded leader even exists
        cannot be read (the signal-0 probe answers an errno other than ESRCH
        or EPERM): its own reason — existence UNPROVEN, never asserted alive
        or gone — unavailable, reported, never signalled; restored, reaped
        exactly once."""
        import errno as errno_module
        from unittest.mock import patch
        reason = proc_module.UNCORROBORATED_LEADER_UNDETERMINED
        identity = self.identity()
        real_kill = os.kill

        def kill(pid, sig):
            if pid == self.pgid and sig == 0:
                raise OSError(errno_module.EIO, "the OS answer cannot be read")
            return real_kill(pid, sig)
        with self.leader_query_fails(), patch.object(os, "kill", kill):
            self.assertIsNone(proc_module._process_exists(self.pgid))
            self.assertEqual(proc_module.group_is_ours(self.root), (None, reason))
            self.assertTrue(proc_module.scope_has_live_group(self.scope))
            with self.no_effects():
                report = self.recovered()
                retired, refused = proc_module.retire_workflow_scopes(
                    self.CONTROL, "wf-live", base=self.base)
        self.assertNotEqual(reason, proc_module.UNCORROBORATED_LEADER_UNAVAILABLE)
        self.assertEqual(report[0], [(identity, [], [], [], [(self.root, self.pgid, reason)])])
        self.assertEqual((report[1], report.unavailable), ([], [(self.root, reason)]))
        lines = self.cli_lines(report)
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (self.pgid, self.root, reason), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([(self.root, reason)]))
        self.assertEqual((retired, refused),
                         ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.intact()
        self.restore = lambda: None
        self.recovered_once_restored()

    # -- R22-2: the workflow store ---------------------------------------------------

    def empty_store(self):
        from workflow_authority import store as wa_store
        store_dir = tempfile.mkdtemp()
        self.addCleanup(remove, store_dir)
        wa_store.WorkflowStore(store_dir).save(wa_store.default_document())
        return store_dir, os.path.join(store_dir, wa_store.WORKFLOWS_FILE_NAME)

    def owners_unavailable(self, owners, source, cause):
        """Recovery over ``owners`` (an ``UnavailableOwners``): the scope
        UNATTRIBUTED as owners unavailable (never STALE), the store counted
        once, the scope counted, the CLI saying both — zero effects."""
        self.assertIsInstance(owners, proc_module.UnavailableOwners)
        with self.assertRaises(TypeError):
            self.OWNER in owners                       # never readable as "no owners"
        store_reason = "%s: the workflow store cannot be read (%s)" % (self.UNAVAILABLE, cause)
        self.assertEqual((owners.source, owners.reason), (source, store_reason))
        with self.no_effects():
            report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                    current_owners=owners)
        gap = (self.scope, proc_module.UNATTRIBUTED_OWNERS_UNAVAILABLE)
        self.assertEqual(report[0], [])
        self.assertEqual(report[1], [gap])
        self.assertEqual(report.unavailable, [(source, store_reason), gap])
        lines = self.cli_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (gap[1], self.scope), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines(report.unavailable))
        self.intact()

    def test_R22_2a_an_unreadable_workflow_store_is_unavailable_never_stale(self):
        from target_runtime import runtime as runtime_module
        store_dir, path = self.empty_store()
        # GENUINE ABSENCE of the owner: a READABLE record that does not hold it.
        owners = runtime_module.current_scope_owners(store_dir)
        self.assertEqual(owners, set())
        report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                current_owners=owners)
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, proc_module.UNATTRIBUTED_STALE)], []))
        # The record UNREADABLE: unavailable — not the empty set, not STALE.
        self.chmod(path, 0)
        self.owners_unavailable(runtime_module.current_scope_owners(store_dir), path,
                                "StoreError")
        # Restored: the record is READ again and classification resumes on it.
        self.restore()
        self.assertEqual(runtime_module.current_scope_owners(store_dir), set())
        # A record that HOLDS the owner: the live group reaped exactly once.
        self.restore = lambda: None
        self.recovered_once_restored()

    def test_R22_2b_a_workflow_store_whose_lock_cannot_be_taken_is_unavailable(self):
        from target_runtime import runtime as runtime_module
        store_dir, path = self.empty_store()
        self.chmod(store_dir, 0)
        self.owners_unavailable(runtime_module.current_scope_owners(store_dir), path,
                                "PermissionError")
        self.restore()
        self.assertEqual(runtime_module.current_scope_owners(store_dir), set())
        self.restore = lambda: None
        self.recovered_once_restored()

    # -- R22-2: credential ACCESS ----------------------------------------------------

    def credential_unavailable(self, path, what, extra_refused=()):
        """``path`` unreadable: the scope's credential is UNAVAILABLE —
        neither missing nor malformed — at the gate, at classification and in
        recovery and the CLI, with zero effects and the retirement refusing;
        restored, recovery acts on the real record exactly once."""
        reason = "%s (%s: PermissionError)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, what)
        before = self.credential_store_state()
        self.chmod(path, 0)
        self.assertEqual(proc_module.validate_assignment(self.scope, base=self.base),
                         (None, reason))
        self.assertNotEqual(reason, proc_module.UNATTRIBUTED_FORGED)
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.classify_scopes(self.base)
        self.assertEqual(raised.exception.unavailable, [(self.scope, reason)])
        with self.no_effects():
            report = self.recovered()
            retired, refused = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-live", base=self.base)
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, reason)], [(self.scope, reason)]))
        lines = self.cli_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (reason, self.scope), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([(self.scope, reason)]))
        self.assertEqual((retired, refused), ([], [(self.scope, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED, reason))] + list(extra_refused)))
        self.restore()
        self.intact()
        # R22-A: the proof reads WROTE NOTHING — no key installed or replaced,
        # nothing created or removed in the credential store.
        self.assertEqual(self.credential_store_state(), before)
        self.restore = lambda: None
        self.recovered_once_restored()

    def credential_store_state(self):
        """The credential store as the OS shows it: its entries, and the
        binding key's identity and bytes (None when absent)."""
        store = proc_module.assignment_base(self.base)
        key = os.path.join(store, proc_module.ASSIGNMENT_KEY_FILE)
        try:
            with open(key, "rb") as handle:
                key_bytes = handle.read()
            key_state = (os.stat(key).st_ino, key_bytes)
        except FileNotFoundError:
            key_state = None
        return sorted(os.listdir(store)), key_state

    def test_R22_2c_an_unsearchable_credential_store_is_unavailable_never_missing(self):
        store = proc_module.assignment_base(self.base)
        self.credential_unavailable(store, "the assignment record", extra_refused=[
            (store, proc_module.RETIRE_REFUSED_UNREADABLE)])

    def test_R22_2d_an_unreadable_credential_is_unavailable_never_malformed(self):
        self.credential_unavailable(self.credential, "the assignment record")

    def test_R22_2e_an_unreadable_binding_key_is_unavailable_never_malformed(self):
        self.credential_unavailable(
            os.path.join(proc_module.assignment_base(self.base), proc_module.ASSIGNMENT_KEY_FILE),
            "the store's binding key")

    def test_R22A_a_missing_binding_key_is_unavailable_never_forged_never_created(self):
        """R22-A: the store's binding key is MISSING. A proof read must not
        install one — that would MUTATE credential state, misreport every
        existing assignment as FORGED, and suppress its verification until
        the original key were restored: the scope's credential is
        UNAVAILABLE ("missing" key) — never FORGED, never MALFORMED — nothing
        is created, zero effects; once the real key is back, the pre-existing
        assignment verifies normally."""
        store = proc_module.assignment_base(self.base)
        key = os.path.join(store, proc_module.ASSIGNMENT_KEY_FILE)
        aside = key + ".aside"
        os.rename(key, aside)
        self.addCleanup(lambda: os.path.exists(aside) and os.rename(aside, key))
        before = self.credential_store_state()
        self.assertIsNone(before[1])
        reason = "%s (the store's binding key: missing)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE)
        self.assertEqual(proc_module.validate_assignment(self.scope, base=self.base),
                         (None, reason))
        self.assertEqual(proc_module.read_assignment(os.path.basename(self.scope),
                                                     base=self.base), (None, reason))
        with self.no_effects():
            report = self.recovered()
            retired, refused = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(self.credential_store_state(), before)   # NO key, nothing created
        self.assertFalse(os.path.exists(key))
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, reason)], [(self.scope, reason)]))
        self.assertEqual([line for line in self.cli_lines(report) if "UNAVAILABLE" in line],
                         self.unavailable_lines([(self.scope, reason)]))
        self.assertEqual((retired, refused), ([], [(self.scope, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED, reason))]))
        self.intact()
        # Restored: the REAL key returns — the pre-existing assignment verifies.
        os.rename(aside, key)
        self.assertEqual(proc_module.validate_assignment(
            self.scope, base=self.base, current_owners={self.OWNER}), (self.identity(), None))
        self.restore = lambda: None
        self.recovered_once_restored()

    def test_R22_2f_absent_malformed_and_unavailable_stay_distinct(self):
        """One scope, its credential in each state in turn — GENUINELY ABSENT,
        MALFORMED, UNREADABLE — three distinct answers; restored, the scope
        validates again and recovery acts on it exactly once."""
        def reason():
            return proc_module.validate_assignment(self.scope, base=self.base)[1]
        self.chmod(self.credential, 0)
        unavailable = reason()
        self.restore()
        self.rewrite(self.credential, b"{not json")
        malformed = reason()
        self.restore()
        aside = self.credential + ".aside"
        os.rename(self.credential, aside)
        try:
            absent = reason()
        finally:
            os.rename(aside, self.credential)
        self.assertEqual((unavailable, malformed, absent), (
            "%s (the assignment record: PermissionError)"
            % proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE,
            proc_module.UNATTRIBUTED_MALFORMED, proc_module.UNATTRIBUTED_NO_ASSIGNMENT))
        self.assertTrue(proc_module.is_unavailable(unavailable))
        self.assertFalse(proc_module.is_unavailable(malformed))
        self.assertFalse(proc_module.is_unavailable(absent))
        self.assertEqual(proc_module.validate_assignment(
            self.scope, base=self.base, current_owners={self.OWNER}), (self.identity(), None))
        self.restore = lambda: None
        self.recovered_once_restored()


class R22LeaderlessRecoveryTests(unittest.TestCase):
    """Task 8 R22-1 — a live group whose recorded LEADER IS GONE while its
    DESCENDANT survives: UNRESOLVED (its ownership cannot be corroborated),
    reported through recovery and the CLI, never signalled, never read as
    gone (it was silently omitted). Resolution: once the group itself is gone
    (the fixture ends its OWN descendant — a controlled kill of this test's
    own process), recovery has nothing to report."""

    _R21 = R21UnavailableObservationTests
    _AG = ScopeAssignmentCredentialTests
    CONTROL = _R21.CONTROL
    OWNER = _R21.OWNER
    assert_no_survivors = _AG.assert_no_survivors
    recovered = _R21.recovered
    cli_lines = _R21.cli_lines
    unavailable_lines = _R21.unavailable_lines
    no_effects = R22TruthfulRecoveryTests.no_effects

    def setUp(self):
        self._AG.setUp(self)
        self.scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL, "wf-live", "t-live",
            base=self.base)
        self.scopes_used.append(self.scope)
        leader = proc_module.spawn_owned(
            [sys.executable, "-c",
             "import subprocess, sys; subprocess.Popen(['sleep', '120']); sys.exit(0)"],
            label="leaderless-occupant", directory=self.scope,
            owned_root_base_dir=self.scope,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        leader.wait(timeout=10)                        # the leader ends and is collected
        [(self.root, self.pgid)] = proc_module.owned_roots(self.scope)
        self.assertEqual(self.pgid, leader.pid)
        self.addCleanup(self.end_descendants)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not proc_module._group_alive(self.pgid):
            time.sleep(0.02)
        self.assertTrue(proc_module._group_alive(self.pgid), "no descendant survived")

    #: The DECLARED test-cleanup reaper (``ReaperFunctionClosureTests``
    #: lists ``kill_group``): it SIGKILLs only the group this test spawned.
    kill_group = staticmethod(RetireProcessScopesTests.kill_group)

    def end_descendants(self):
        """The fixture's OWN descendant, ended (controlled, through the
        declared ``kill_group``), and waited out."""
        self.kill_group(self.pgid)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and proc_module._group_alive(self.pgid):
            time.sleep(0.02)

    def test_R22_1b_a_surviving_descendant_is_unresolved_and_never_signalled(self):
        reason = proc_module.UNCORROBORATED_LEADERLESS
        identity = proc_module.validate_assignment(self.scope, base=self.base)[0]
        self.assertEqual(proc_module.group_is_ours(self.root), (None, reason))
        with self.no_effects():
            report = self.recovered()
            retired, refused = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(report[0], [(identity, [], [], [], [(self.root, self.pgid, reason)])])
        self.assertEqual((report[1], report.unavailable), ([], []))   # an observation MADE
        lines = self.cli_lines(report)
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (self.pgid, self.root, reason), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([]))
        self.assertEqual((retired, refused),
                         ([], [(self.scope, proc_module.RETIRE_REFUSED_LEADERLESS)]))
        self.assertTrue(proc_module._group_alive(self.pgid))  # never signalled
        # Resolved: the group itself ends — nothing remains to report.
        self.end_descendants()
        self.assertFalse(proc_module._group_alive(self.pgid))
        report = self.recovered()
        self.assertEqual((report[0], report[1], report.unavailable), ([], [], []))


class R23TruthfulRecoveryTests(unittest.TestCase):
    """Task 8 R23-1 / R23-2 — R22-2 was INCOMPLETE: two remaining places where
    what could not be observed, or content that is not a credential, escaped
    the truthful-recovery invariant.

    R23-1: the workflow store's OWN existence gate (``WorkflowStore.load``)
    read every metadata failure as absence — ``os.path.exists`` is False for
    EACCES and EIO — so the store was reinitialized as a default document, its
    owners read as none, the scope as STALE and the CLI said ``UNAVAILABLE: 0``.
    Now only ``FileNotFoundError`` is absence: EACCES and EIO raise StoreError,
    the owners are UNAVAILABLE, nothing is acted on or written, and once the
    metadata reads again the record is read again.

    R23-2: a credential whose binding is not a binding — non-ASCII, an escaped
    surrogate, the wrong length or alphabet — or whose content nests past the
    decoder's or the binding computation's depth RAISED out of
    ``read_assignment`` (``TypeError`` from ``hmac.compare_digest``, a
    ``RecursionError``) and aborted the whole report. Now it is MALFORMED at
    the reader's boundary: its own unattributed row, ZERO effects on it, every
    other scope still reported, the valid and FORGED controls exactly as
    before.

    R21UnavailableObservationTests' fixture (a production assignment holding a
    REAL live stamped group), with R22TruthfulRecoveryTests' helpers, borrowed
    without their tests."""

    _R21 = R21UnavailableObservationTests
    _R22 = R22TruthfulRecoveryTests
    _AG = ScopeAssignmentCredentialTests
    CONTROL = _R21.CONTROL
    OWNER = _R21.OWNER
    UNAVAILABLE = _R21.UNAVAILABLE
    setUp = _R21.setUp
    assert_no_survivors = _AG.assert_no_survivors
    live_group_in = _AG.live_group_in
    release_occupant = _AG.release_occupant
    rewrite = _R21.rewrite
    recovered = _R21.recovered
    cli_lines = _R21.cli_lines
    unavailable_lines = _R21.unavailable_lines
    recovered_once_restored = _R21.recovered_once_restored
    no_effects = _R22.no_effects
    identity = _R22.identity
    intact = _R22.intact
    empty_store = _R22.empty_store
    owners_unavailable = _R22.owners_unavailable
    credential_store_state = _R22.credential_store_state

    # -- helpers -------------------------------------------------------------------

    def never_raised(self, call, *args, **kwargs):
        """``call(*args, **kwargs)``, which must CLASSIFY and never raise on
        content: an escaping ``TypeError`` or ``RecursionError`` is reported as
        the assertion failure it is."""
        try:
            return call(*args, **kwargs)
        except (TypeError, RecursionError) as exc:
            self.fail("%s RAISED %s on content instead of classifying it"
                      % (getattr(call, "__name__", call), type(exc).__name__))

    def stat_refused(self, path, error):
        """``os.stat`` of exactly ``path`` fails with errno ``error`` — the
        metadata cannot be read (the Reviewer's in-memory substitute); every
        other path is observed for real. A context manager."""
        from unittest.mock import patch
        real_stat = os.stat

        def stat(target, *args, **kwargs):
            if not isinstance(target, int) and os.fspath(target) == path:
                raise OSError(error, os.strerror(error), path)
            return real_stat(target, *args, **kwargs)
        return patch.object(os, "stat", stat)

    def credential_bytes(self):
        with open(self.credential, "rb") as handle:
            return handle.read()

    def with_binding(self, binding_json):
        """The scope's credential rewritten with ``binding_json`` — raw JSON
        TEXT, so an escape stays an escape in the file — as its binding, every
        bound field intact; ``restore()`` writes the real credential back."""
        record = json.loads(self.credential_bytes().decode("utf-8"))
        record.pop("binding")
        text = json.dumps(record, sort_keys=True)
        self.rewrite(self.credential,
                     (text[:-1] + ', "binding": ' + binding_json + "}").encode("utf-8"))

    def malformed_contained(self):
        """The scope's credential is MALFORMED at the reader, the gate,
        classification, recovery and the CLI — never raised; ZERO effects on
        it (no reap, no signal but 0, nothing deleted or rewritten, the
        retirement refusing); restored, recovery acts on the real record
        exactly once."""
        reason = proc_module.UNATTRIBUTED_MALFORMED
        damaged, before = self.credential_bytes(), self.credential_store_state()
        self.assertEqual(self.never_raised(proc_module.read_assignment,
                                           os.path.basename(self.scope), base=self.base),
                         (None, reason))
        self.assertEqual(self.never_raised(proc_module.validate_assignment,
                                           self.scope, base=self.base), (None, reason))
        with self.no_effects():
            report = self.never_raised(self.recovered)
            retired, refused = self.never_raised(proc_module.retire_workflow_scopes,
                                                 self.CONTROL, "wf-live", base=self.base)
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, reason)], []))
        lines = self.cli_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (reason, self.scope), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([]))   # malformed is not unavailable
        self.assertEqual((retired, refused), ([], [(self.scope, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED, reason))]))
        self.intact()
        self.assertEqual(self.credential_bytes(), damaged)
        self.assertEqual(self.credential_store_state(), before)
        self.recovered_once_restored()

    # -- R23-1: the workflow store's metadata ---------------------------------------

    def store_metadata_unavailable(self, error):
        """The store's metadata cannot be read (``error``): ``load`` RAISES —
        never a silent default document — the owners are UNAVAILABLE (never the
        empty set), the scope never STALE, the CLI counting both, ZERO effects;
        nothing written or transitioned; restored, the record is read again and
        recovery acts on it exactly once."""
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store
        store_dir, path = self.empty_store()
        with open(path, "rb") as handle:
            stored = handle.read()
        credential, before = self.credential_bytes(), self.credential_store_state()
        with self.stat_refused(path, error):
            with self.assertRaises(wa_store.StoreError) as raised:
                wa_store.WorkflowStore(store_dir).load()
            self.assertIn("UNAVAILABLE, not absent", str(raised.exception))
            self.assertIn(os.strerror(error), str(raised.exception))
            self.owners_unavailable(runtime_module.current_scope_owners(store_dir), path,
                                    "StoreError")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), stored)       # never reinitialized
        self.assertEqual(self.credential_bytes(), credential)
        self.assertEqual(self.credential_store_state(), before)
        # Restored: the record READS again — this store's genuine absence of the owner.
        self.assertEqual(runtime_module.current_scope_owners(store_dir), set())
        self.restore = lambda: None
        self.recovered_once_restored()

    def test_R23_1a_store_metadata_refused_EACCES_is_unavailable_never_absent(self):
        self.store_metadata_unavailable(errno.EACCES)

    def test_R23_1b_store_metadata_failing_EIO_is_unavailable_never_absent(self):
        self.store_metadata_unavailable(errno.EIO)

    def test_R23_1c_a_genuinely_missing_store_is_still_absent(self):
        """The control: NO store file at all is GENUINE ABSENCE — a fresh
        default document (nothing created), the owner set empty, the scope
        STALE and left alone — exactly as before."""
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store
        store_dir = tempfile.mkdtemp()
        self.addCleanup(remove, store_dir)
        path = os.path.join(store_dir, wa_store.WORKFLOWS_FILE_NAME)
        self.assertEqual(wa_store.WorkflowStore(store_dir).load(), wa_store.default_document())
        owners = runtime_module.current_scope_owners(store_dir)
        self.assertEqual(owners, set())
        with self.no_effects():
            report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                    current_owners=owners)
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, proc_module.UNATTRIBUTED_STALE)], []))
        self.assertFalse(os.path.exists(path))
        self.intact()

    # -- R23-2: the binding's representation and malformed content -------------------

    def test_R23_2a_a_non_ASCII_binding_is_malformed_never_raised(self):
        self.with_binding(json.dumps("é" * 64, ensure_ascii=False))
        self.malformed_contained()

    def test_R23_2b_an_escaped_surrogate_binding_is_malformed_never_raised(self):
        self.with_binding('"\\ud800' + "0" * 63 + '"')
        presented = json.loads(self.credential_bytes().decode("utf-8"))["binding"]
        self.assertEqual((len(presented), presented.isascii()), (64, False))
        self.malformed_contained()

    def test_R23_2c_the_binding_representation_is_validated_before_comparison(self):
        """Anything that is not a binding as the writer makes it — the wrong
        alphabet, case or length — is MALFORMED and never compared. The
        controls are exact: a WELL-FORMED binding failing against the present,
        read key is FORGED; the real binding verifies."""
        name = os.path.basename(self.scope)
        malformed = proc_module.UNATTRIBUTED_MALFORMED
        for binding in ("z" * 64, "A" * 64, "0" * 63, "0" * 65, " " + "0" * 63, ""):
            self.with_binding(json.dumps(binding))
            self.assertEqual(self.never_raised(proc_module.read_assignment, name,
                                               base=self.base), (None, malformed), binding)
            self.restore()
        self.with_binding(json.dumps("0" * 64))
        self.assertEqual(proc_module.read_assignment(name, base=self.base),
                         (None, proc_module.UNATTRIBUTED_FORGED))
        self.restore()
        self.assertEqual(proc_module.validate_assignment(
            self.scope, base=self.base, current_owners={self.OWNER}), (self.identity(), None))
        self.with_binding(json.dumps("Z" * 64))
        self.malformed_contained()

    def test_R23_2d_a_credential_nested_past_the_decoder_is_malformed_never_raised(self):
        self.rewrite(self.credential, b"[" * 100000)
        self.malformed_contained()

    def test_R23_2e_a_bound_field_nested_past_the_binding_encoder_is_malformed(self):
        """Content the DECODER accepts but the binding computation cannot
        serialize — a bound field nested to the decoder's deepest accepted
        level, under a WELL-FORMED binding — is MALFORMED, never a
        RecursionError out of the reader. The depth is FOUND, not assumed (it
        depends on the stack the reader runs at): the deepest level the
        decoder accepts, where the binding computation itself must raise."""
        from unittest.mock import patch
        name = os.path.basename(self.scope)
        record = json.loads(self.credential_bytes().decode("utf-8"))
        record.update(binding="0" * 64)
        head = json.dumps({key: value for key, value in record.items() if key != "unit_id"},
                          sort_keys=True)[:-1]
        computed, raised = [], []
        real = proc_module._binding_for

        def binding_for(*args, **kwargs):
            computed.append(True)
            try:
                return real(*args, **kwargs)
            except RecursionError:
                raised.append(True)
                raise

        def nested(depth):
            return (head + ', "unit_id": ' + "[" * depth + "]" * depth + "}").encode("utf-8")

        def attempt(depth):
            """The reader's answer at ``depth`` — never raised — with whether
            the decoder ACCEPTED it (the binding was computed) recorded."""
            self.rewrite(self.credential, nested(depth))
            del computed[:], raised[:]
            with patch.object(proc_module, "_binding_for", binding_for):
                result = self.never_raised(proc_module.read_assignment, name, base=self.base)
            self.restore()
            return result
        forged, malformed = proc_module.UNATTRIBUTED_FORGED, proc_module.UNATTRIBUTED_MALFORMED
        low, high = 1, 100000
        self.assertEqual((attempt(low), computed, raised), ((None, forged), [True], []))
        self.assertEqual((attempt(high), computed), ((None, malformed), []))   # the decoder
        while high - low > 1:                   # the deepest level the decoder accepts
            middle = (low + high) // 2
            result = attempt(middle)
            if computed:
                low = middle
            else:
                self.assertEqual(result, (None, malformed), middle)
                high = middle
        self.assertEqual((attempt(low), computed, raised), ((None, malformed), [True], [True]),
                         "at the decoder's deepest level the binding computation must raise")
        self.rewrite(self.credential, nested(low))
        self.malformed_contained()

    def test_R23_2f_a_malformed_scope_never_prevents_any_other_scope_report(self):
        """Three scopes at once: this one MALFORMED (a non-ASCII binding), a
        valid one whose owner is current, and a FORGED one. The report covers
        all three — the valid scope recovered (its group reaped once), the
        forged and the malformed reported unattributed — and the malformed
        scope suffers ZERO effects."""
        other = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                         "wf-other", "t-other", base=self.base)
        other_pgid = self.live_group_in(other)
        other_owner = (proc_module.OWNER_TYPE_WORKFLOW, proc_module.control_digest(self.CONTROL),
                       "wf-other", "t-other")
        other_identity = proc_module.validate_assignment(other, base=self.base)[0]
        forged_name = proc_module.scope_name(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                             "wf-forged", "t-forged")
        forged = os.path.join(proc_module.owned_root_base(self.base), forged_name)
        os.makedirs(forged)
        with open(proc_module.assignment_path(forged_name, self.base), "w",
                  encoding="utf-8") as handle:
            json.dump({
                "scope_name": forged_name,
                "owner_type": proc_module.OWNER_TYPE_WORKFLOW,
                "control_digest": proc_module.control_digest(self.CONTROL),
                "owner_id": "wf-forged",
                "unit_id": "t-forged",
                "control_identity": self.CONTROL,
                "assigned_at": 1.0,
                "assigned_by_pid": 4242,
                "binding": "0" * 64,
            }, handle)
        self.with_binding(json.dumps("é" * 64, ensure_ascii=False))
        damaged = self.credential_bytes()
        report = self.never_raised(proc_module.recover_attributed, self.base,
                                   settle_seconds=10.0,
                                   current_owners={self.OWNER, other_owner})
        self.assertEqual(report[0], [(other_identity, [other_pgid], [], [], [])])
        self.assertEqual(sorted(report[1]), sorted([
            (self.scope, proc_module.UNATTRIBUTED_MALFORMED),
            (forged, proc_module.UNATTRIBUTED_FORGED)]))
        self.assertEqual(report.unavailable, [])
        lines = self.cli_lines(report)
        for scope, reason in report[1]:
            self.assertIn("dirun: unattributed process record directory REPORTED and left"
                          " alone (%s): %s" % (reason, scope), lines)
        self.assertFalse(proc_module._group_alive(other_pgid))   # the valid scope recovered
        self.intact()                                           # the malformed one untouched
        self.assertEqual(self.credential_bytes(), damaged)
        self.assertTrue(os.path.isdir(forged))
        # Restored: this scope verifies again and is recovered exactly once; the
        # forged one is still reported, never acted on.
        self.restore()
        report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                current_owners={self.OWNER, other_owner})
        self.assertEqual(report[0], [(self.identity(), [self.pgid], [], [], [])])
        self.assertEqual((report[1], report.unavailable),
                         ([(forged, proc_module.UNATTRIBUTED_FORGED)], []))
        self.assertFalse(proc_module._group_alive(self.pgid))


class R24DanglingLinkRecoveryTests(unittest.TestCase):
    """Task 8 R24-2, and R24-1's workflow store on the recovery route: a link
    that EXISTS while its target does not is PRESENT, never absent.

    ``stat`` and ``open`` raise ``FileNotFoundError`` for a missing path AND
    for an existing link whose target is unavailable, at the path or at an
    ancestor. Recovery read that error as absence:
    - a dangling scope base, scope entry, owned-root prefix or owned root
      DISAPPEARED (the CLI said ``UNAVAILABLE: 0``);
    - a dangling group record read as "never stamped";
    - a dangling credential, or credential store, as "no assignment";
    - a dangling workflow-store link as an EMPTY store (owners none, STALE).

    Now the traversal (``classify_missing``) decides. Genuine absence keeps
    today's result exactly: no row and no record appear for it. A dangling
    target is an observation not made. It is REPORTED, with ZERO effects on
    what it covers:
    - no reap and no signal but 0;
    - nothing deleted;
    - nothing written or initialized at the link or its target;
    - the retirement refusing.

    Restored — the TARGET returns, so the link resolves — recovery acts on
    the real records THROUGH the link exactly once, as through any valid link.

    R21UnavailableObservationTests' fixture (a production assignment holding
    a REAL live stamped group), with the R21/R22 helpers."""

    _R21 = R21UnavailableObservationTests
    _R22 = R22TruthfulRecoveryTests
    _AG = ScopeAssignmentCredentialTests
    CONTROL = _R21.CONTROL
    OWNER = _R21.OWNER
    UNAVAILABLE = _R21.UNAVAILABLE
    DANGLING = "FileNotFoundError (symlink target)"
    setUp = _R21.setUp
    assert_no_survivors = _AG.assert_no_survivors
    live_group_in = _AG.live_group_in
    release_occupant = _AG.release_occupant
    recovered = _R21.recovered
    cli_lines = _R21.cli_lines
    unavailable_lines = _R21.unavailable_lines
    left_alone = _R21.left_alone
    recovered_once_restored = _R21.recovered_once_restored
    no_effects = _R22.no_effects
    intact = _R22.intact
    empty_store = _R22.empty_store
    owners_unavailable = _R22.owners_unavailable
    credential_store_state = _R22.credential_store_state

    # -- helpers -------------------------------------------------------------------

    def dangle(self, path):
        """``path`` — a real file or directory — becomes a symbolic link to a
        target that does NOT exist: the link is PRESENT, its target is not.
        The real object waits aside, outside every path under test.

        Returns ``(restore, target, link)``. ``restore`` makes the TARGET
        return (the real object moved to it, so the link resolves); ``link``
        is the link's own ``lstat``. The cleanup puts the real object back at
        ``path``. It runs before the occupant's reap and the base's removal."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        aside = os.path.join(holding, "aside")
        target = os.path.join(holding, "target")
        os.rename(path, aside)
        os.symlink(target, path)

        def put_back():
            os.unlink(path)
            os.rename(target if os.path.lexists(target) else aside, path)
        self.addCleanup(put_back)
        return (lambda: os.rename(aside, target)), target, os.lstat(path)

    def link_unchanged(self, path, target, link):
        """Nothing written at the DENIED object: the same link (same inode,
        same target), and nothing created or initialized at its target."""
        self.assertTrue(os.path.islink(path), path)
        self.assertEqual((os.lstat(path).st_ino, os.readlink(path)), (link.st_ino, target))
        self.assertFalse(os.path.lexists(target), "something was initialized at the target")

    def counted(self, reaps, signals):
        """Every recorded-root reap (its pgid) and every signal but 0
        (``(call, id, signal)``) COUNTED, for per-scope attribution."""
        import contextlib
        from unittest.mock import patch
        real_reap = proc_module.reap_group_by_recorded_root
        real_killpg, real_kill = os.killpg, os.kill

        def reap(pgid, *args, **kwargs):
            reaps.append(pgid)
            return real_reap(pgid, *args, **kwargs)

        def killpg(pgid, sig):
            if sig != 0:
                signals.append(("killpg", pgid, sig))
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                signals.append(("kill", pid, sig))
            return real_kill(pid, sig)

        @contextlib.contextmanager
        def counting():
            with patch.object(proc_module, "reap_group_by_recorded_root", reap), \
                    patch.object(os, "killpg", killpg), patch.object(os, "kill", kill):
                yield
        return counting()

    def gap(self, path, what):
        return (path, "%s: %s cannot be read (%s)" % (self.UNAVAILABLE, what, self.DANGLING))

    # -- R24-2: the five readers, a dangling FILE or DIRECTORY link ------------------

    def test_R24_2a_a_dangling_scope_base_is_unavailable_never_absent(self):
        prefix = proc_module.owned_root_base(self.base)
        restore, target, link = self.dangle(prefix)
        gap = [self.gap(prefix, "the scope base")]
        self.assertEqual(proc_module._scope_directories(self.base), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.classify_scopes(self.base)
        self.assertEqual(raised.exception.unavailable, gap)
        with self.no_effects():
            self.left_alone(gap, [(prefix, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.link_unchanged(prefix, target, link)
        self.restore = restore
        self.recovered_once_restored()

    def test_R24_2b_a_dangling_scope_entry_is_unavailable_never_absent(self):
        prefix = proc_module.owned_root_base(self.base)
        self.assertEqual(sorted(os.listdir(prefix)), [os.path.basename(self.scope)])
        restore, target, link = self.dangle(self.scope)
        gap = [self.gap(self.scope, "the scope")]
        self.assertEqual(proc_module._scope_directories(self.base), ([], gap))
        with self.no_effects():
            self.left_alone(gap, [(self.scope, "%s (a symbolic link, not a directory)"
                                   % proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.link_unchanged(self.scope, target, link)
        self.assertTrue(os.path.isfile(self.credential))   # its credential untouched
        self.restore = restore
        self.recovered_once_restored()

    def test_R24_2c_a_dangling_owned_root_prefix_is_unavailable_never_absent(self):
        nested = proc_module.owned_root_base(self.scope)
        restore, target, link = self.dangle(nested)
        gap = [self.gap(nested, "the owned-root prefix")]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.owned_roots(self.scope)
        self.assertEqual(raised.exception.unavailable, gap)
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        with self.no_effects():
            self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.link_unchanged(nested, target, link)
        self.restore = restore
        self.recovered_once_restored()

    def test_R24_2d_a_dangling_owned_root_is_unavailable_never_absent(self):
        restore, target, link = self.dangle(self.root)
        gap = [self.gap(self.root, "the owned root")]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        with self.assertRaises(proc_module.ObservationUnavailable):
            proc_module.owned_roots(self.scope)
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        with self.no_effects():
            self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.link_unchanged(self.root, target, link)
        self.restore = restore
        self.recovered_once_restored()

    def test_R24_2e_a_dangling_group_record_is_unavailable_never_unstamped(self):
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        restore, target, link = self.dangle(record)
        gap = [self.gap(record, "the group record")]
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([], gap))
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        with self.assertRaises(FileNotFoundError):
            proc_module.owned_root_record(self.root)       # raises: never "no pgid"
        self.assertEqual(proc_module.retirement_refusal(self.scope),
                         proc_module.RETIRE_REFUSED_UNREADABLE)  # never UNSTAMPED
        with self.no_effects():
            self.left_alone(gap, [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)])
        self.link_unchanged(record, target, link)
        self.restore = restore
        self.recovered_once_restored()

    def credential_link_unavailable(self, path, extra_refused=()):
        """``path`` (the credential, or the store holding it) dangles: the
        scope's credential is UNAVAILABLE — never "no assignment" — at the
        reader, the gate, classification, recovery and the CLI. ZERO effects:
        no reap, no signal but 0, nothing written in the credential store,
        the link and its target unchanged, the retirement refusing. Restored,
        recovery acts on the real record through the link exactly once."""
        name = os.path.basename(self.scope)
        reason = "%s (the assignment record: %s)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.DANGLING)
        before = self.credential_store_state()
        restore, target, link = self.dangle(path)
        self.assertEqual(proc_module.read_assignment(name, base=self.base), (None, reason))
        self.assertEqual(proc_module.validate_assignment(self.scope, base=self.base),
                         (None, reason))
        with self.assertRaises(proc_module.ObservationUnavailable) as raised:
            proc_module.classify_scopes(self.base)
        self.assertEqual(raised.exception.unavailable, [(self.scope, reason)])
        with self.no_effects():
            report = self.recovered()
            retired, refused = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-live", base=self.base)
        self.assertEqual((report[0], report[1], report.unavailable),
                         ([], [(self.scope, reason)], [(self.scope, reason)]))
        lines = self.cli_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (reason, self.scope), lines)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines([(self.scope, reason)]))
        self.assertEqual(retired, [])
        self.assertEqual(sorted(refused), sorted([(self.scope, "%s (%s)" % (
            proc_module.RETIRE_REFUSED_UNATTRIBUTED, reason))] + list(extra_refused)))
        self.assertTrue(proc_module._group_alive(self.pgid))
        self.link_unchanged(path, target, link)
        restore()
        self.assertEqual(self.credential_store_state(), before)   # nothing written there
        self.restore = lambda: None
        self.recovered_once_restored()

    def test_R24_2f_a_dangling_credential_is_unavailable_never_no_assignment(self):
        self.credential_link_unavailable(self.credential)

    def test_R24_2g_a_dangling_credential_STORE_ancestor_is_unavailable(self):
        """The dangling ANCESTOR link of the credential: the store directory.
        Its listing for the retirement is unreadable too, and refused."""
        store = proc_module.assignment_base(self.base)
        self.credential_link_unavailable(
            store, extra_refused=[(store, proc_module.RETIRE_REFUSED_UNREADABLE)])

    def test_R24_2h_a_base_behind_a_dangling_ANCESTOR_link_is_unavailable(self):
        """The base reached through a link whose target is missing: recovery
        reports the scope base unavailable (never "no scopes"), and the
        retirement refuses it (never "no scope was ever assigned"). Restored
        — the target returns as the real base — recovery through the alias
        acts exactly once."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        alias, target = os.path.join(holding, "alias"), os.path.join(holding, "target")
        os.symlink(target, alias)
        prefix = proc_module.owned_root_base(alias)
        gap = [self.gap(prefix, "the scope base")]
        self.assertEqual(proc_module._scope_directories(alias), ([], gap))
        with self.no_effects():
            report = proc_module.recover_attributed(alias, settle_seconds=10.0,
                                                    current_owners={self.OWNER})
            retired = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=alias)
        self.assertEqual((report[0], report[1], report.unavailable), ([], [], gap))
        self.assertEqual(retired, ([], [(prefix, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertTrue(proc_module._group_alive(self.pgid))
        self.assertEqual(os.readlink(alias), target)
        self.assertFalse(os.path.lexists(target))
        os.symlink(self.base, target)                    # the target returns
        report = proc_module.recover_attributed(alias, settle_seconds=10.0,
                                                current_owners={self.OWNER})
        self.assertEqual(report.unavailable, [])
        self.assertEqual([(tuple(identity), reaped) for identity, reaped, *_ in report[0]],
                         [(self.OWNER, [self.pgid])])
        self.assertFalse(proc_module._group_alive(self.pgid))
        again = proc_module.recover_attributed(alias, settle_seconds=10.0,
                                               current_owners={self.OWNER})
        self.assertEqual((again[0], again[1], again.unavailable), ([], [], []))

    # -- R24-2: MIXED scopes in ONE observation --------------------------------------

    def test_R24_2i_mixed_scopes_each_truthful_with_per_scope_counts(self):
        """In ONE recovery:
        - A — the fixture's scope, reachable: its live group reaped EXACTLY
          once. That is REQUIRED progress, counted for A alone.
        - B — a live group whose CREDENTIAL dangles: reported unavailable,
          zero effects on B.
        - C — a scope whose ENTRY dangles: reported unavailable.
        - D — never spawned (its owned-root prefix GENUINELY absent): no
          row and no record, exactly as before.

        The retirement refuses B and C and deletes nothing. Restored, B is
        reaped exactly once through its link, C and D need nothing, and a
        further pass repeats nothing."""
        digest = proc_module.control_digest(self.CONTROL)

        def assigned(workflow, unit):
            scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                             workflow, unit, base=self.base)
            return scope, (proc_module.OWNER_TYPE_WORKFLOW, digest, workflow, unit)
        scope_b, owner_b = assigned("wf-b", "t-b")
        pgid_b = self.live_group_in(scope_b)
        credential_b = proc_module.assignment_path(os.path.basename(scope_b), self.base)
        scope_c, owner_c = assigned("wf-c", "t-c")
        scope_d, owner_d = assigned("wf-d", "t-d")
        self.assertFalse(os.path.lexists(proc_module.owned_root_base(scope_d)))
        restore_b, target_b, link_b = self.dangle(credential_b)
        restore_c, target_c, link_c = self.dangle(scope_c)
        owners = {self.OWNER, owner_b, owner_c, owner_d}
        reason_b = "%s (the assignment record: %s)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.DANGLING)
        gap_c = self.gap(scope_c, "the scope")
        reaps, signals = [], []
        with self.counted(reaps, signals):
            report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                    current_owners=owners)
        # A progressed exactly once — and nothing else was acted on.
        self.assertEqual([(tuple(identity), reaped, stuck, unstamped, uncorroborated)
                          for identity, reaped, stuck, unstamped, uncorroborated in report[0]],
                         [(self.OWNER, [self.pgid], [], [], [])])
        self.assertEqual(reaps, [self.pgid])
        self.assertTrue(signals)
        self.assertEqual({target_id for _call, target_id, _sig in signals}, {self.pgid})
        self.assertFalse(proc_module._group_alive(self.pgid))
        # B and C reported; nothing they cover acted on or written.
        self.assertEqual(report[1], [(scope_b, reason_b)])
        self.assertEqual(report.unavailable, [gap_c, (scope_b, reason_b)])
        self.assertTrue(proc_module._group_alive(pgid_b))
        self.link_unchanged(credential_b, target_b, link_b)
        self.link_unchanged(scope_c, target_c, link_c)
        # D: genuinely absent — no row, no record.
        reported = [path for path, _reason in report[1] + report.unavailable]
        self.assertNotIn(scope_d, reported)
        self.assertFalse(os.path.lexists(proc_module.owned_root_base(scope_d)))
        lines = self.cli_lines(report)
        self.assertEqual([line for line in lines if "UNAVAILABLE" in line],
                         self.unavailable_lines(report.unavailable))
        with self.no_effects():
            self.assertEqual(
                proc_module.retire_workflow_scopes(self.CONTROL, "wf-b", base=self.base),
                ([], [(scope_b, "%s (%s)" % (proc_module.RETIRE_REFUSED_UNATTRIBUTED,
                                             reason_b))]))
            self.assertEqual(
                proc_module.retire_workflow_scopes(self.CONTROL, "wf-c", base=self.base),
                ([], [(scope_c, "%s (a symbolic link, not a directory)"
                       % proc_module.RETIRE_REFUSED_UNREADABLE)]))
        for path in (scope_b, scope_d, credential_b, scope_c):
            self.assertTrue(os.path.lexists(path), path)          # nothing deleted
        # Restored: the targets return.
        restore_b()
        restore_c()
        reaps, signals = [], []
        with self.counted(reaps, signals):
            report = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                                    current_owners=owners)
        self.assertEqual([(tuple(identity), reaped) for identity, reaped, *_ in report[0]],
                         [(owner_b, [pgid_b])])
        self.assertEqual((report[1], report.unavailable), ([], []))
        self.assertEqual(reaps, [pgid_b])
        self.assertEqual({target_id for _call, target_id, _sig in signals}, {pgid_b})
        self.assertFalse(proc_module._group_alive(pgid_b))
        again = proc_module.recover_attributed(self.base, settle_seconds=10.0,
                                               current_owners=owners)
        self.assertEqual((again[0], again[1], again.unavailable), ([], [], []))

    # -- R24-2: GENUINE absence, unchanged ---------------------------------------------

    def test_R24_2j_genuine_absence_keeps_todays_results_exactly(self):
        """A path GENUINELY missing — every component before it reachable —
        reads exactly as before. No row, no record and no unavailability
        appear for it, and nothing is created."""
        from workflow_authority import store as wa_store
        fresh = tempfile.mkdtemp()
        self.addCleanup(remove, fresh)
        missing = os.path.join(fresh, "never-created")
        self.assertEqual(proc_module._scope_directories(missing), ([], []))
        self.assertEqual(proc_module.owned_roots_observed(missing), ([], []))
        report = proc_module.recover_attributed(missing, settle_seconds=10.0,
                                                current_owners={self.OWNER})
        self.assertEqual((report[0], report[1], report.unavailable), ([], [], []))
        self.assertEqual(proc_module.retire_workflow_scopes(self.CONTROL, "wf-live",
                                                            base=missing), ([], []))
        self.assertEqual(proc_module.read_assignment("no-such-scope", base=self.base),
                         (None, proc_module.UNATTRIBUTED_NO_ASSIGNMENT))
        self.assertEqual(wa_store.WorkflowStore(os.path.join(fresh, "no-store")).load(),
                         wa_store.default_document())
        self.assertEqual(sorted(os.listdir(fresh)), [])      # nothing created
        # A group record GENUINELY missing: a root not yet stamped, as ever.
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        aside = os.path.join(fresh, "pgid")
        os.rename(record, aside)
        self.addCleanup(lambda: os.path.lexists(aside) and os.rename(aside, record))
        self.assertEqual(proc_module.owned_roots_observed(self.scope), ([(self.root, None)], []))
        self.assertIsNone(proc_module.owned_root_record(self.root)["pgid"])
        self.assertEqual(proc_module.retirement_refusal(self.scope),
                         proc_module.RETIRE_REFUSED_UNSTAMPED)
        os.rename(aside, record)
        self.assertEqual(proc_module.owned_roots_observed(self.scope),
                         ([(self.root, self.pgid)], []))

    # -- R24-2: the retirement's DESTRUCTIVE boundary ----------------------------------

    def test_R24_2k_a_scope_dangling_after_attribution_is_refused_never_deleted(self):
        """The Lead's amended preclear (§4-bis). ``_matching_scopes``
        attributes the REAL scope. Before ``retirement_refusal`` reads its
        NESTED owned-root prefix, the scope entry — the prefix's ANCESTOR —
        becomes a link whose target is missing. ``lstat`` spares only the
        final component, so the prefix read raises FileNotFoundError THROUGH
        that link. Before R24 that read as "no owned roots": no refusal, and
        the release went on through ``admit()`` to ``_remove_credential`` —
        the credential, the sole deletion proof — before ``rmtree``.

        Now it is an ownership observation not made:
        - the scope is REFUSED (UNREADABLE), truthfully;
        - ZERO credential removals, ZERO scope deletions and no
          ``admit()``, each counted by its own assertion;
        - the credential's bytes unchanged, the link unchanged, nothing at
          its target.

        Restored (the target returns), the scope entry is a VALID link: the
        retirement refuses it as it always did ("a symbolic link, not a
        directory"), again with zero removals and deletions, and recovery
        reaps through it exactly once. A scope GENUINELY without owned roots
        (never spawned) is still retired exactly once, through a VALID base
        alias as through the real path."""
        import shutil
        from unittest.mock import patch
        real_matching = proc_module._matching_scopes
        real_remove = proc_module._remove_credential
        real_rmtree = shutil.rmtree
        dangled, removals, deletions, admits = [], [], [], []

        def matching_then_dangle(*args, **kwargs):
            result = real_matching(*args, **kwargs)
            if not dangled:
                dangled.append(self.dangle(self.scope))   # AFTER attribution
            return result

        def remove_credential(path):
            removals.append(path)
            return real_remove(path)

        def rmtree(path, *args, **kwargs):
            deletions.append(path)
            return real_rmtree(path, *args, **kwargs)

        def admit(effect):
            admits.append(True)
            return None, effect()          # Task 8 R25-1: held across the effect

        def counted():
            return (patch.object(proc_module, "_remove_credential", remove_credential),
                    patch.object(shutil, "rmtree", rmtree))
        with open(self.credential, "rb") as handle:
            credential = handle.read()
        removal_patch, rmtree_patch = counted()
        with patch.object(proc_module, "_matching_scopes", matching_then_dangle), \
                removal_patch, rmtree_patch, self.no_effects():
            retired, refused = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-live", base=self.base, admit=admit)
        [(restore, target, link)] = dangled
        self.assertEqual(removals, [])                   # ZERO credential removals
        self.assertEqual(deletions, [])                  # ZERO scope deletions
        self.assertEqual(admits, [])                     # nothing admitted to removal
        self.assertEqual((retired, refused),
                         ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        with open(self.credential, "rb") as handle:
            self.assertEqual(handle.read(), credential)
        self.link_unchanged(self.scope, target, link)
        # Restored: a VALID link, refused as it always was; nothing removed.
        restore()
        removal_patch, rmtree_patch = counted()
        with removal_patch, rmtree_patch, self.no_effects():
            again = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live",
                                                       base=self.base, admit=admit)
        self.assertEqual(again, ([], [(self.scope, "%s (a symbolic link, not a directory)"
                                       % proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual((removals, deletions, admits), ([], [], []))
        self.restore = lambda: None
        self.recovered_once_restored()
        # GENUINE absence: a scope never spawned is retired exactly once, through
        # the real base and through a VALID base alias alike.
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        alias = os.path.join(holding, "alias")
        os.symlink(self.base, alias)
        for workflow, base in (("wf-g1", self.base), ("wf-g2", alias)):
            scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                             workflow, "t-g", base=self.base)
            name = os.path.basename(scope)
            seen = os.path.join(proc_module.owned_root_base(base), name)
            self.assertFalse(os.path.lexists(proc_module.owned_root_base(scope)))
            del removals[:], deletions[:], admits[:]
            removal_patch, rmtree_patch = counted()
            with removal_patch, rmtree_patch:
                result = proc_module.retire_workflow_scopes(self.CONTROL, workflow,
                                                            base=base, admit=admit)
            self.assertEqual(result, ([seen], []), workflow)
            self.assertEqual(removals, [proc_module.assignment_path(name, base=base)])
            self.assertEqual(deletions, [seen])
            self.assertEqual(admits, [True])
            self.assertFalse(os.path.lexists(scope), workflow)

    # -- R24-1: the workflow store on the recovery route --------------------------------

    def test_R24_1a_a_dangling_workflow_store_FILE_link_is_unavailable_never_empty(self):
        """The store file is a link whose target is missing: ``load`` RAISES
        (never an empty store). The owners are UNAVAILABLE (never the empty
        set), the scope is never STALE, and nothing is written at the link or
        its target. Restored, the record reads through the link."""
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store
        store_dir, path = self.empty_store()
        with open(path, "rb") as handle:
            stored = handle.read()
        restore, target, link = self.dangle(path)
        with self.assertRaises(wa_store.StoreError) as raised:
            wa_store.WorkflowStore(store_dir).load()
        self.assertIn("UNAVAILABLE, not absent", str(raised.exception))
        self.assertIn(self.DANGLING, str(raised.exception))
        self.owners_unavailable(runtime_module.current_scope_owners(store_dir), path,
                                "StoreError")
        self.link_unchanged(path, target, link)
        restore()
        self.assertEqual(runtime_module.current_scope_owners(store_dir), set())
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), stored)       # never reinitialized
        self.restore = lambda: None
        self.recovered_once_restored()

    def test_R24_1b_a_dangling_workflow_store_DIRECTORY_ancestor_is_unavailable(self):
        """The store DIRECTORY is a link whose target is missing. ``load``
        RAISES on the gate (a lock-free read). On the recovery route the
        store's lock refuses first, exactly as before R24
        (``FileExistsError``: the link exists, its directory does not). The
        owners are UNAVAILABLE and nothing is created at the target.
        Restored, the record reads through the link."""
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store
        store_dir, path = self.empty_store()
        restore, target, link = self.dangle(store_dir)
        with self.assertRaises(wa_store.StoreError) as raised:
            wa_store.WorkflowStore(store_dir).load()
        self.assertIn(self.DANGLING, str(raised.exception))
        self.owners_unavailable(runtime_module.current_scope_owners(store_dir), path,
                                "FileExistsError")
        self.link_unchanged(store_dir, target, link)
        restore()
        self.assertEqual(runtime_module.current_scope_owners(store_dir), set())
        self.restore = lambda: None
        self.recovered_once_restored()


class R25HeldRetirementTests(RuntimeCase):
    """Task 8 R25-1 / R25-2 at the retirement itself, over scopes assigned
    through the production credential path and REAL owned roots (a stamped
    root whose group is gone).

    R25-1: BOTH proofs hold at the moment of effect. The admission is taken
    FRESH per object and HELD across that object's effect (``admit(effect)``).
    The ownership readers run before it, bracketed by their evidence, and the
    evidence is re-read inside it by a local, non-waiting, BOUNDED reader. A
    transition in either window refuses with ZERO credential removals and
    ZERO scope deletions, each counted at its call. The bytes are kept, and
    once restored the object is retired exactly once.

    R25-2: ``_present`` and ``_remove_credential`` read absence only from the
    traversal: through a dangling ancestor, the obligation is KEPT and
    reported, never "removed"."""

    CONTROL = RetireProcessScopesTests.CONTROL
    scope_for = RetireProcessScopesTests.scope_for
    DANGLING = "FileNotFoundError (symlink target)"
    CHANGED = ("%s (its ownership evidence changed before the removal)"
               % proc_module.RETIRE_REFUSED_UNREADABLE)

    def setUp(self):
        super(R25HeldRetirementTests, self).setUp()
        self.private = tempfile.mkdtemp()
        self.addCleanup(remove, self.private)

    # -- helpers ---------------------------------------------------------------------

    def retire(self, workflow_id="wf-0001", admit=None):
        return proc_module.retire_workflow_scopes(self.CONTROL, workflow_id,
                                                  base=self.private, admit=admit)

    def credential(self, scope):
        return proc_module.assignment_path(os.path.basename(scope), self.private)

    def spent_root(self, scope):
        """A REAL owned root in ``scope``, stamped by its child, whose group
        is gone (the child ran to its end and was collected). Returns the
        root."""
        process = proc_module.spawn_owned(
            [sys.executable, "-c", "pass"], label="r25-spent-root",
            directory=scope, owned_root_base_dir=scope,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait(timeout=30)
        [(root, pgid)] = proc_module.owned_roots(scope)
        self.assertIsNotNone(pgid)
        self.assertFalse(proc_module._group_alive(pgid))
        self.assertIsNone(proc_module.retirement_refusal(scope))
        return root

    def swap_aside(self, path):
        """``path`` becomes a link to a MISSING target; ``put_back()``
        restores the real object as it was (also at cleanup)."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        aside = os.path.join(holding, "aside")
        os.rename(path, aside)
        os.symlink(os.path.join(holding, "missing"), path)

        def put_back():
            if os.path.islink(path):
                os.unlink(path)
                os.rename(aside, path)
        self.addCleanup(put_back)
        return put_back

    @staticmethod
    def tree_bytes(path):
        seen = []
        for root, directories, files in os.walk(path):
            for name in directories:
                seen.append((os.path.relpath(os.path.join(root, name), path), None))
            for name in files:
                full = os.path.join(root, name)
                with open(full, "rb") as handle:
                    seen.append((os.path.relpath(full, path), handle.read()))
        return sorted(seen, key=lambda item: item[0])

    def kept_bytes(self, scope):
        with open(self.credential(scope), "rb") as handle:
            return self.tree_bytes(scope), handle.read()

    def unbound_of(self, scope):
        """WHY ``scope``'s ownership evidence cannot be bound, read now (None
        when it binds). Task 8 R26-1: the retirement's gate refuses with the
        readers' verdict, so the specific cause is asserted here."""
        return proc_module._unbound_reason(proc_module._ownership_evidence(
            scope, self.credential(scope), self.private))

    def effects(self, events=None):
        """``(patches, counts)``: every credential removal INVOKED and every
        ``rmtree`` — the EFFECTS, counted at their calls, apart from any
        report. ``events``, when given, also receives them in order."""
        import shutil
        from unittest.mock import patch
        real_remove, real_rmtree = proc_module._remove_credential, shutil.rmtree
        counts = {"removals": [], "deletions": []}

        def remove_credential(path):
            counts["removals"].append(path)
            if events is not None:
                events.append(("removal", path))
            return real_remove(path)

        def rmtree(path, *args, **kwargs):
            counts["deletions"].append(path)
            if events is not None:
                events.append(("deletion", path))
            return real_rmtree(path, *args, **kwargs)
        return (patch.object(proc_module, "_remove_credential", remove_credential),
                patch.object(shutil, "rmtree", rmtree)), counts

    def run_counted(self, admit=None, workflow_id="wf-0001"):
        (removal, deletion), counts = self.effects()
        with removal, deletion:
            result = self.retire(workflow_id, admit=admit)
        return result, counts

    @staticmethod
    def changing(change, admits):
        """An admission that grants, with ``change()`` landing INSIDE it —
        once, for the first object — before the effect runs."""
        def admit(effect):
            admits.append(True)
            if len(admits) == 1:
                change()
            return None, effect()
        return admit

    def refused_during_the_admission(self, scope, change, restore, others=()):
        """``change()`` lands inside ``scope``'s held admission: the scope is
        refused (CHANGED) with ZERO credential removals and ZERO deletions
        for it, its bytes kept. ``restore()`` puts it back; then the
        retirement retires it exactly once."""
        kept = self.kept_bytes(scope)
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(change, admits))
        self.assertEqual(refused, [(scope, self.CHANGED)])
        self.assertEqual(retired, list(others))
        self.assertNotIn(self.credential(scope), counts["removals"])   # ZERO removals
        self.assertNotIn(scope, counts["deletions"])                   # ZERO deletions
        self.assertEqual(len(counts["removals"]), len(others))
        self.assertEqual(len(admits), 1 + len(others))
        restore()
        self.assertEqual(self.kept_bytes(scope), kept)
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused), ([scope], []))
        self.assertEqual((counts["removals"], counts["deletions"], admits),
                         ([self.credential(scope)], [scope], [True]))
        self.assertFalse(os.path.lexists(scope))
        self.assertFalse(os.path.lexists(self.credential(scope)))

    # -- R25-1: the admission HELD across each effect, fresh per object -----------------

    def test_R25_1a_each_effect_runs_INSIDE_its_own_fresh_held_admission(self):
        """Two scopes: the admission is taken once PER OBJECT, and each
        object's credential removal and deletion run while ITS admission is
        held — never between two admissions, never after one returned."""
        first, second = sorted([self.scope_for("wf-0001", "t-1"),
                                self.scope_for("wf-0001", "t-2")])
        self.spent_root(first)
        events = []

        def admit(effect):
            events.append(("admit", len([e for e in events if e[0] == "admit"])))
            result = effect()
            events.append(("released",))
            return None, result
        (removal, deletion), counts = self.effects(events)
        with removal, deletion:
            self.assertEqual(self.retire(admit=admit), ([first, second], []))
        self.assertEqual(events, [
            ("admit", 0), ("removal", self.credential(first)), ("deletion", first),
            ("released",),
            ("admit", 1), ("removal", self.credential(second)), ("deletion", second),
            ("released",)])

    def test_R25_1b_a_scope_entry_dangled_during_the_admission_is_never_removed(self):
        """The Reviewer's probe: inside the admission the scope entry becomes a
        link to a missing target. The in-section re-read refuses it; the
        second scope, under its own fresh admission, is retired."""
        first, second = sorted([self.scope_for("wf-0001", "t-1"),
                                self.scope_for("wf-0001", "t-2")])
        self.spent_root(first)
        put_back = []
        self.refused_during_the_admission(
            first, lambda: put_back.append(self.swap_aside(first)),
            lambda: put_back[0](), others=[second])

    def test_R25_1c_an_ANCESTOR_dangled_during_the_admission_removes_nothing(self):
        """Inside the admission the owned-root BASE — the scope's parent —
        becomes a link to a missing target, so the scope's ``lstat`` raises
        FileNotFoundError THROUGH it. Never read as absent: refused, zero
        effects; restored, retired once."""
        scope = self.scope_for("wf-0001")
        self.spent_root(scope)
        put_back = []
        self.refused_during_the_admission(
            scope,
            lambda: put_back.append(self.swap_aside(proc_module.owned_root_base(self.private))),
            lambda: put_back[0]())

    def test_R25_1d_a_record_or_root_changed_during_the_admission_removes_nothing(self):
        """Each change a reader's verdict rests on, landing inside the
        admission: a NEW owned root (a spawn's, created before its process),
        the group record rewritten, the group record swapped for a link to an
        identical copy (same bytes, another object), the credential
        rewritten. Each refuses with zero effects; restored, retired once."""
        import shutil
        cases = []

        def new_root(scope, root):
            path = os.path.join(proc_module.owned_root_base(scope), "own-r25-new")
            return (lambda: os.mkdir(path)), (lambda: os.rmdir(path))

        def rewritten(path):
            with open(path, "rb") as handle:
                original = handle.read()

            def write(data):
                def act():
                    with open(path, "wb") as handle:
                        handle.write(data)
                return act
            return write(original + b" "), write(original)

        def linked_copy(path):
            holding = tempfile.mkdtemp()
            self.addCleanup(remove, holding)
            copy = os.path.join(holding, "copy")
            shutil.copy2(path, copy)

            def swap():
                os.rename(path, copy + ".real")
                os.symlink(copy, path)

            def back():
                os.unlink(path)
                os.rename(copy + ".real", path)
            return swap, back
        cases = [
            ("a new owned root", new_root),
            ("the group record rewritten",
             lambda scope, root: rewritten(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE))),
            ("the group record swapped for a link to a copy",
             lambda scope, root: linked_copy(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE))),
            ("the credential rewritten",
             lambda scope, root: rewritten(self.credential(scope))),
        ]
        for number, (label, make) in enumerate(cases):
            with self.subTest(label):
                scope = self.scope_for("wf-0001", "t-%d" % number)
                root = self.spent_root(scope)
                change, restore = make(scope, root)
                self.refused_during_the_admission(scope, change, restore)

    def test_R25_1e_evidence_changing_DURING_the_ownership_reads_refuses_before_admission(self):
        """The ownership readers run BEFORE the admission, bracketed by their
        evidence. A new owned root created while ``retirement_refusal`` reads
        (after its listing) is not in its verdict, but it IS in the bracket:
        refused before any admission is asked, zero effects. Removed again,
        retired once."""
        from unittest.mock import patch
        scope = self.scope_for("wf-0001")
        self.spent_root(scope)
        late = os.path.join(proc_module.owned_root_base(scope), "own-r25-late")
        real_refusal = proc_module.retirement_refusal

        def refusal(directory):
            result = real_refusal(directory)
            if not os.path.exists(late):
                os.mkdir(late)
            return result
        admits = []
        with patch.object(proc_module, "retirement_refusal", refusal):
            (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused), ([], [(scope, (
            "%s (its ownership evidence changed while it was read)"
            % proc_module.RETIRE_REFUSED_UNREADABLE))]))
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        os.rmdir(late)
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused, counts["removals"], counts["deletions"]),
                         ([scope], [], [self.credential(scope)], [scope]))

    def test_R25_1f_a_dangling_credential_changed_during_the_admission_is_kept(self):
        """A credential whose scope directory is already gone, retired by its
        own record. Inside its admission: (a) its scope directory appears
        again; (b) its store becomes a link to a missing target. Each refuses
        with ZERO removals, the credential's bytes kept; restored, it is
        removed exactly once."""
        for label in ("its scope directory present again", "its store dangling"):
            with self.subTest(label):
                workflow_id = "wf-cred-%d" % len(label)
                scope = self.scope_for(workflow_id)
                remove(scope)
                path = self.credential(scope)
                with open(path, "rb") as handle:
                    stored = handle.read()
                if label.startswith("its scope"):
                    change, restore = (lambda: os.mkdir(scope)), (lambda: os.rmdir(scope))
                else:
                    put_back = []
                    change = (lambda: put_back.append(self.swap_aside(
                        proc_module.assignment_base(self.private))))
                    restore = lambda: put_back[0]()
                admits = []
                (retired, refused), counts = self.run_counted(
                    self.changing(change, admits), workflow_id=workflow_id)
                self.assertEqual((retired, refused), ([], [(path, (
                    "%s (its evidence changed before the removal)"
                    % proc_module.RETIRE_REFUSED_UNREADABLE))]))
                self.assertEqual((counts["removals"], counts["deletions"], admits),
                                 ([], [], [True]))
                restore()
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), stored)
                admits = []
                (retired, refused), counts = self.run_counted(
                    self.changing(lambda: None, admits), workflow_id=workflow_id)
                self.assertEqual((retired, refused, counts["removals"], admits),
                                 ([path], [], [path], [True]))
                self.assertFalse(os.path.lexists(path))

    # -- R25-1 (queued context 2): the held section's reader -----------------------------

    def a_record(self, size=8):
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        path = os.path.join(holding, "record")
        with open(path, "wb") as handle:
            handle.write(b"7" * size)
        return path, os.lstat(path)

    def test_R25_1g_the_evidence_reader_never_waits_on_a_FIFO(self):
        """Between the ``lstat`` and the open, the record becomes a FIFO. An
        open for reading would WAIT for a writer; the reader opens with
        ``O_NONBLOCK``, sees through ``fstat`` that what it opened is not
        what it examined, and returns at once, unbound. A record ``lstat``
        already shows as a FIFO is never opened at all: it is unbound as not
        a regular file."""
        import stat
        import threading
        from unittest.mock import patch
        path, info = self.a_record()
        os.unlink(path)
        os.mkfifo(path)
        results = []
        thread = threading.Thread(
            target=lambda: results.append(proc_module._record_digest(None, path, info)))
        thread.daemon = True
        thread.start()
        thread.join(10)

        def unblock():                                   # only if it ever blocked
            if thread.is_alive():
                descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                os.close(descriptor)
                thread.join(10)
        self.addCleanup(unblock)
        self.assertFalse(thread.is_alive(), "the evidence reader WAITED on a FIFO")
        self.assertEqual(results, [proc_module._Unbound(
            "the object opened is not the one examined")])
        real_open, opened = os.open, []

        def counting_open(name, *args, **kwargs):
            opened.append(name)
            return real_open(name, *args, **kwargs)
        with patch.object(os, "open", counting_open):
            item, fifo = proc_module._entry_evidence(None, path, path, True)
        self.assertEqual(opened, [])
        self.assertTrue(stat.S_ISFIFO(fifo.st_mode))
        self.assertEqual(item[-1], proc_module._Unbound("not a regular file"))

    def test_R25_1h_the_evidence_reader_never_follows_a_link_lstat_did_not(self):
        """Between the ``lstat`` and the open, the record becomes a link to an
        IDENTICAL copy. Following it would digest another object under the
        examined object's identity. The reader opens with ``O_NOFOLLOW``, so
        the open itself is refused (``ELOOP``, an ``OSError``): the digest is
        unbound, and the copy is never read. (Were the link followed, the
        ``fstat`` identity check would still refuse it, with its own reason.)"""
        import shutil
        from unittest.mock import patch
        path, info = self.a_record()
        copy = path + ".copy"
        shutil.copy2(path, copy)
        os.unlink(path)
        os.symlink(copy, path)
        real_read, reads = os.read, []

        def counting_read(descriptor, size):
            reads.append(size)
            return real_read(descriptor, size)
        with patch.object(os, "read", counting_read):
            self.assertEqual(proc_module._record_digest(None, path, info),
                             proc_module._Unbound("OSError"))
        self.assertEqual(reads, [])

    def test_R25_1i_the_evidence_reader_is_BOUNDED(self):
        """A record larger than ``EVIDENCE_RECORD_BYTES`` is never read; one
        that GREW after its ``lstat`` is read no further than the bound plus
        one byte, in ``READ_BLOCK_BYTES`` blocks; a listing stops past
        ``EVIDENCE_MAX_ENTRIES``. Each is unbound, never a digest."""
        from unittest.mock import patch
        bound = proc_module.EVIDENCE_RECORD_BYTES
        real_read, reads = os.read, []

        def counting_read(descriptor, size):
            data = real_read(descriptor, size)
            reads.append((size, len(data)))
            return data
        large, info = self.a_record(bound + 1)
        with patch.object(os, "read", counting_read):
            self.assertEqual(proc_module._record_digest(None, large, info),
                             proc_module._Unbound("larger than %d bytes" % bound))
        self.assertEqual(reads, [])                          # never read
        grown, info = self.a_record(8)
        with open(grown, "ab") as handle:                    # the same object, grown
            handle.write(b"8" * (4 * bound))
        with patch.object(os, "read", counting_read):
            self.assertEqual(proc_module._record_digest(None, grown, info),
                             proc_module._Unbound("larger than %d bytes" % bound))
        self.assertLessEqual(sum(got for _size, got in reads), bound + 1)
        self.assertTrue(all(size <= proc_module.READ_BLOCK_BYTES for size, _got in reads))
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        for number in range(proc_module.EVIDENCE_MAX_ENTRIES + 1):
            open(os.path.join(holding, "e%05d" % number), "w").close()
        descriptor = os.open(holding, os.O_RDONLY)
        try:
            self.assertEqual(proc_module._directory_entries(descriptor), proc_module._Unbound(
                "more than %d entries" % proc_module.EVIDENCE_MAX_ENTRIES))
        finally:
            os.close(descriptor)

    def test_R25_1j_evidence_that_cannot_be_bound_refuses_with_zero_effects(self):
        """At the retirement: an owned-root record larger than the bound, and
        an owned-root prefix with more entries than the bound (files, which
        ``retirement_refusal`` itself skips), each REFUSE the scope as
        unbound — no admission asked, zero effects. Brought within bounds,
        each is retired once; a prefix of exactly ``EVIDENCE_MAX_ENTRIES``
        entries is within them.

        (Task 8 R26-1: the first evidence read now GATES the readers, and
        refuses with their own verdict, ``RETIRE_REFUSED_UNREADABLE``. WHY it
        is unbound is asserted on the evidence itself.)"""
        bound = proc_module.EVIDENCE_RECORD_BYTES
        scope = self.scope_for("wf-0001", "t-big")
        root = self.spent_root(scope)
        nonce = os.path.join(root, proc_module.OWNED_ROOT_NONCE_FILE)
        with open(nonce, "rb") as handle:
            original = handle.read()
        with open(nonce, "ab") as handle:
            handle.write(b"0" * bound)
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(self.unbound_of(scope), "%s: larger than %d bytes" % (
            os.path.join(proc_module.owned_root_base(scope), os.path.basename(root),
                         proc_module.OWNED_ROOT_NONCE_FILE), bound))
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        with open(nonce, "wb") as handle:
            handle.write(original)
        (retired, refused), counts = self.run_counted()
        self.assertEqual((retired, refused, counts["deletions"]), ([scope], [], [scope]))
        scope = self.scope_for("wf-0001", "t-many")
        prefix = proc_module.owned_root_base(scope)
        os.makedirs(prefix)
        for number in range(proc_module.EVIDENCE_MAX_ENTRIES + 1):
            open(os.path.join(prefix, "file-%05d" % number), "w").close()
        self.assertIsNone(proc_module.retirement_refusal(scope))
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(self.unbound_of(scope),
                         "more than %d entries" % proc_module.EVIDENCE_MAX_ENTRIES)
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        os.unlink(os.path.join(prefix, "file-00000"))
        (retired, refused), counts = self.run_counted()
        self.assertEqual((retired, refused, counts["deletions"]), ([scope], [], [scope]))

    def test_R25_1k_evidence_covers_what_the_readers_read_through_a_link(self):
        """The readers follow links. ``retirement_refusal`` reads the prefix
        THROUGH the scope entry, and ``open`` reads a record through a link.
        The evidence covers what they read:
        - a scope entry swapped, after attribution, for a VALID link to an
          empty directory (the readers find no owned roots there) is refused
          as unbound — "not a directory", enumeration's own rule — with ZERO
          credential removals and ZERO deletions, the bytes kept. Restored,
          it is retired once.
        - a group record that IS a valid link to a copy of itself is FOLLOWED
          by the evidence as by its reader. A change of the link's TARGET
          inside the admission is seen (zero effects). Unchanged, the scope
          is retired exactly once through the link, and only the link goes.
        - a credential that IS a valid link (R24's restoration) is retired
          exactly once: the link removed, its target never touched."""
        import shutil
        from unittest.mock import patch
        scope = self.scope_for("wf-0001", "t-entry")
        self.spent_root(scope)
        kept = self.kept_bytes(scope)
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        empty, aside = os.path.join(holding, "empty"), os.path.join(holding, "aside")
        os.mkdir(empty)
        real_matching, swapped = proc_module._matching_scopes, []

        def matching_then_swap(*args, **kwargs):
            result = real_matching(*args, **kwargs)
            if not swapped:
                os.rename(scope, aside)
                os.symlink(empty, scope)
                swapped.append(True)
            return result

        def put_back():
            if os.path.islink(scope):
                os.unlink(scope)
                os.rename(aside, scope)
        self.addCleanup(put_back)
        admits = []
        with patch.object(proc_module, "_matching_scopes", matching_then_swap):
            (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertIsNone(proc_module.retirement_refusal(scope))   # the readers: no roots
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(self.unbound_of(scope), "%s: not a directory" % scope)
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        put_back()
        self.assertEqual(self.kept_bytes(scope), kept)
        (retired, refused), counts = self.run_counted()
        self.assertEqual((retired, refused, counts["removals"], counts["deletions"]),
                         ([scope], [], [self.credential(scope)], [scope]))
        scope = self.scope_for("wf-0001", "t-record")
        root = self.spent_root(scope)
        record = os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)
        copy = os.path.join(holding, "pgid-copy")
        shutil.copy2(record, copy)
        os.unlink(record)
        os.symlink(copy, record)
        with open(copy, "rb") as handle:
            original = handle.read()
        self.assertIsNone(proc_module.retirement_refusal(scope))   # read through the link

        def target_rewritten():                     # the reader's verdict is unchanged
            with open(copy, "ab") as handle:
                handle.write(b" ")
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(target_rewritten, admits))
        self.assertEqual((retired, refused), ([], [(scope, self.CHANGED)]))
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], [True]))
        with open(copy, "wb") as handle:
            handle.write(original)
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused, counts["removals"], counts["deletions"], admits),
                         ([scope], [], [self.credential(scope)], [scope], [True]))
        with open(copy, "rb") as handle:
            self.assertEqual(handle.read(), original)          # only the link went
        scope = self.scope_for("wf-0001", "t-credential")
        credential = self.credential(scope)
        real = os.path.join(holding, "credential-real")
        os.rename(credential, real)
        os.symlink(real, credential)
        admits = []
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused, counts["removals"], counts["deletions"], admits),
                         ([scope], [], [credential], [scope], [True]))
        self.assertFalse(os.path.lexists(credential))
        self.assertTrue(os.path.isfile(real))                     # its target never touched

    def test_R25_1l_a_FAILED_metadata_observation_refuses_with_zero_effects(self):
        """The Lead's §4-quater, the OSError path. The group record's METADATA
        read fails (an injected EIO on its ``lstat``, relative to the opened
        root — the evidence reader's own access path) in every snapshot,
        while ``retirement_refusal`` still reads its BYTES by its own path.
        As two equal plain values, the failed observations would be
        compared, admitted, and a change of the record inside the admission
        would go unseen. They are unbound: refused BEFORE any admission,
        with ZERO credential removals and ZERO deletions, the bytes kept.
        With the failure lifted, the scope is retired exactly once. A record
        GENUINELY absent in the opened root (its leader-start) stays bound
        as absent."""
        import errno
        from unittest.mock import patch
        scope = self.scope_for("wf-0001")
        root = self.spent_root(scope)
        record = os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)
        start = os.path.join(root, proc_module.OWNED_ROOT_START_FILE)
        if os.path.lexists(start):
            os.unlink(start)
        kept = self.kept_bytes(scope)
        real_stat = os.stat

        def failing_stat(name, *args, **kwargs):
            if name == proc_module.OWNED_ROOT_PGID_FILE and kwargs.get("dir_fd") is not None:
                raise OSError(errno.EIO, "injected metadata failure")
            return real_stat(name, *args, **kwargs)

        def record_rewritten():
            with open(record, "ab") as handle:
                handle.write(b" ")
        admits = []
        label = os.path.join(proc_module.owned_root_base(scope), os.path.basename(root),
                             proc_module.OWNED_ROOT_PGID_FILE)
        with patch.object(os, "stat", failing_stat):
            self.assertIsNone(proc_module.retirement_refusal(scope))   # its bytes read
            (retired, refused), counts = self.run_counted(
                self.changing(record_rewritten, admits))
            # Task 8 R26-1: the gate refuses with the readers' verdict; WHY,
            # on the evidence itself.
            self.assertEqual(self.unbound_of(scope),
                             "%s: its observation failed (OSError)" % label)
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(counts["removals"], [])                # ZERO credential removals
        self.assertEqual(counts["deletions"], [])               # ZERO scope deletions
        self.assertEqual(admits, [])                            # no admission asked
        self.assertEqual(self.kept_bytes(scope), kept)
        evidence = proc_module._ownership_evidence(scope, self.credential(scope), self.private)
        absent = os.path.join(os.path.dirname(label), proc_module.OWNED_ROOT_START_FILE)
        self.assertIn((absent, "absent", None), evidence)        # genuine absence: bound
        self.assertIsNone(proc_module._unbound_reason(evidence))
        (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual((retired, refused, counts["removals"], counts["deletions"], admits),
                         ([scope], [], [self.credential(scope)], [scope], [True]))

    def test_R25_1m_an_UNAVAILABLE_metadata_observation_refuses_with_zero_effects(self):
        """The Lead's §4-quater, the path-missing path. The credential's
        ``lstat`` by path raises FileNotFoundError while the traversal finds
        every component present (``classify_missing``: unavailable, "during
        the read"), in every snapshot; ``validate_assignment`` still reads
        its bytes. As equal plain values these would be compared and
        admitted, and a rewrite of the credential inside the admission would
        go unseen. They are unbound: refused before any admission, ZERO
        removals and ZERO deletions, the credential's bytes kept. The failure
        lifted, the scope is retired exactly once through a VALID alias of
        the base."""
        import errno
        from unittest.mock import patch
        scope = self.scope_for("wf-0001")
        self.spent_root(scope)
        credential = self.credential(scope)
        kept = self.kept_bytes(scope)
        real_stat = os.stat

        def unavailable_stat(name, *args, **kwargs):
            if (name == credential and kwargs.get("dir_fd") is None
                    and kwargs.get("follow_symlinks") is False):
                raise FileNotFoundError(errno.ENOENT, "injected", name)
            return real_stat(name, *args, **kwargs)

        def credential_rewritten():
            with open(credential, "ab") as handle:
                handle.write(b" ")
        admits = []
        with patch.object(os, "stat", unavailable_stat):
            self.assertIsNotNone(proc_module.validate_assignment(scope, base=self.private)[0])
            (retired, refused), counts = self.run_counted(
                self.changing(credential_rewritten, admits))
            # Task 8 R26-1: the gate refuses with the readers' verdict; WHY,
            # on the evidence itself.
            self.assertEqual(self.unbound_of(scope), (
                "%s: its observation is unavailable (FileNotFoundError (during the read))"
                % credential))
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(counts["removals"], [])                # ZERO credential removals
        self.assertEqual(counts["deletions"], [])               # ZERO scope deletions
        self.assertEqual(admits, [])
        self.assertEqual(self.kept_bytes(scope), kept)
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        alias = os.path.join(holding, "alias")
        os.symlink(self.private, alias)
        seen = os.path.join(proc_module.owned_root_base(alias), os.path.basename(scope))
        (removal, deletion), counts = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(
                self.CONTROL, "wf-0001", base=alias, admit=self.changing(lambda: None, admits))
        self.assertEqual(result, ([seen], []))
        self.assertEqual((counts["removals"], counts["deletions"], admits), (
            [proc_module.assignment_path(os.path.basename(scope), alias)], [seen], [True]))
        self.assertFalse(os.path.lexists(scope))

    def test_R25_1n_a_followed_record_link_is_re_observed_unchanged(self):
        """A record that IS a link is followed ONCE. If the link itself is
        replaced while its target is read — here by another link to the SAME
        target — the evidence is unbound ("its link changed while it was
        read"), never a digest. If it is removed meanwhile, it is unbound
        too ("its link cannot be re-observed"). Unchanged, the same link is
        bound to its target's identity and digest."""
        from unittest.mock import patch
        path, _info = self.a_record()
        target = path + ".target"
        os.rename(path, target)
        os.symlink(target, path)
        link = os.lstat(path)
        bound = proc_module._linked_record_digest(None, path, link)
        self.assertEqual(bound[0], proc_module._identity_of(os.stat(target)))
        real_digest = proc_module._bounded_digest

        def relinked(descriptor, info):
            result = real_digest(descriptor, info)
            os.unlink(path)
            os.symlink(target, path)                           # a NEW link, same target
            return result
        with patch.object(proc_module, "_bounded_digest", relinked):
            self.assertEqual(proc_module._linked_record_digest(None, path, link),
                             proc_module._Unbound("its link changed while it was read"))
        link = os.lstat(path)

        def unlinked(descriptor, info):
            result = real_digest(descriptor, info)
            os.unlink(path)                                    # the link GONE meanwhile
            return result
        with patch.object(proc_module, "_bounded_digest", unlinked):
            self.assertEqual(proc_module._linked_record_digest(None, path, link),
                             proc_module._Unbound("its link cannot be re-observed"))

    # -- R25-2: absence read only from the traversal --------------------------------------

    def test_R25_2a_present_and_remove_credential_never_read_a_dangling_ancestor_as_gone(self):
        """``_present`` is True and ``_remove_credential`` KEEPS the
        credential (reporting why) when its ancestor is a link to a missing
        target — the credential still exists, aside. Genuine absence: not
        present, and nothing to keep. A VALID ancestor link: the credential
        is removed and observed gone."""
        scope = self.scope_for("wf-0001")
        path = self.credential(scope)
        with open(path, "rb") as handle:
            stored = handle.read()
        store = proc_module.assignment_base(self.private)
        put_back = self.swap_aside(store)
        self.assertTrue(proc_module._present(path))
        self.assertEqual(proc_module._remove_credential(path),
                         "its absence cannot be observed (%s)" % self.DANGLING)
        put_back()
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), stored)          # never removed
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        alias = os.path.join(holding, "alias")
        os.symlink(store, alias)
        through = os.path.join(alias, os.path.basename(path))
        self.assertTrue(proc_module._present(through))
        self.assertIsNone(proc_module._remove_credential(through))
        self.assertFalse(os.path.lexists(path))
        self.assertFalse(proc_module._present(path))         # genuinely absent
        self.assertIsNone(proc_module._remove_credential(path))

    def test_R25_2b_a_removal_whose_absence_cannot_be_observed_is_never_retired(self):
        """Inside the effect, after the ``rmtree``, the owned-root base — the
        scope's parent — becomes a link to a missing target: the directory's
        absence cannot be OBSERVED. Refused UNDELETED, naming that, never
        retired. Restored: the directory was in fact removed, so nothing
        remains to retire or refuse."""
        import shutil
        from unittest.mock import patch
        scope = self.scope_for("wf-0001")
        self.spent_root(scope)
        real_rmtree, put_back = shutil.rmtree, []

        def rmtree(path, *args, **kwargs):
            result = real_rmtree(path, *args, **kwargs)
            if path == scope:
                put_back.append(self.swap_aside(proc_module.owned_root_base(self.private)))
            return result
        with patch.object(shutil, "rmtree", rmtree):
            retired, refused = self.retire()
        self.assertEqual((retired, refused), ([], [(scope, (
            "%s (its absence cannot be observed (%s); its assignment credential was removed)"
            % (proc_module.RETIRE_REFUSED_UNDELETED, self.DANGLING)))]))
        put_back[0]()
        self.assertFalse(os.path.lexists(scope))
        self.assertEqual(self.retire(), ([], []))

    def test_R26_1k_evidence_unbound_only_AFTER_the_readers_refuses_in_its_own_words(self):
        """Task 8 R26-1: the gate refuses evidence that is unbound at the FIRST
        read. Evidence that binds there and becomes unbound only WHILE the
        readers run — an owned-root record grown past the bound inside
        ``retirement_refusal`` — is refused by the second check, in its own
        words ("cannot be bound", with the cause), never as a change and
        never retired. No admission is asked; zero effects. Brought back
        within the bound, the scope is retired exactly once."""
        from unittest.mock import patch
        bound = proc_module.EVIDENCE_RECORD_BYTES
        scope = self.scope_for("wf-0001", "t-grown")
        root = self.spent_root(scope)
        nonce = os.path.join(root, proc_module.OWNED_ROOT_NONCE_FILE)
        with open(nonce, "rb") as handle:
            original = handle.read()
        real_refusal, grown = proc_module.retirement_refusal, []

        def refusal(directory):
            result = real_refusal(directory)
            if directory == scope and not grown:
                with open(nonce, "ab") as handle:            # AFTER the reader ran
                    handle.write(b"0" * bound)
                grown.append(nonce)
            return result
        admits = []
        with patch.object(proc_module, "retirement_refusal", refusal):
            (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        cause = "%s: larger than %d bytes" % (
            os.path.join(proc_module.owned_root_base(scope), os.path.basename(root),
                         proc_module.OWNED_ROOT_NONCE_FILE), bound)
        self.assertEqual(grown, [nonce])
        self.assertEqual((retired, refused), ([], [(scope, (
            "%s (its ownership evidence cannot be bound: %s)"
            % (proc_module.RETIRE_REFUSED_UNREADABLE, cause)))]))
        self.assertEqual(self.unbound_of(scope), cause)
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        with open(nonce, "wb") as handle:
            handle.write(original)
        (retired, refused), counts = self.run_counted()
        self.assertEqual((retired, refused, counts["deletions"]), ([scope], [], [scope]))

    def test_R26_1p_a_scope_dangling_after_attribution_is_its_readers_refusal_on_both_paths(
            self):
        """Task 8 R26-1: the seam ``R24_2k`` binds, MOVED by the gate. The scope
        entry — its owned-root prefix's ANCESTOR — becomes a link to a missing
        target right after it is attributed, so ``retirement_refusal`` reads the
        prefix THROUGH that link:
        - on the cleanup HOLD (``owned_scope_refusals``, which has no evidence
          gate) the scope is refused UNREADABLE, never read as "no owned
          roots";
        - in the retirement, AFTER the first evidence read (past the gate), the
          scope is refused UNREADABLE by ``retirement_refusal`` itself — the
          readers' verdict, before any post-reader check — with no admission
          asked, ZERO credential removals and ZERO deletions.
        (``R24_2k`` dangles BEFORE the first evidence read, where the gate now
        refuses first.) Restored, the scope is retired exactly once."""
        from unittest.mock import patch
        scope = self.scope_for("wf-0001", "t-dangle")
        self.spent_root(scope)
        real_matching, real_evidence, put_back = (proc_module._matching_scopes,
                                                  proc_module._ownership_evidence, [])

        def matching_then_dangle(*args, **kwargs):
            result = real_matching(*args, **kwargs)
            if not put_back:
                put_back.append(self.swap_aside(scope))
            return result
        with patch.object(proc_module, "_matching_scopes", matching_then_dangle):
            hold = proc_module.owned_scope_refusals(self.CONTROL, "wf-0001", base=self.private)
        self.assertEqual(len(put_back), 1)
        self.assertEqual(hold, ([(scope, proc_module.RETIRE_REFUSED_UNREADABLE)], None))
        put_back.pop()()

        def evidence_then_dangle(directory, credential, base=None):
            result = real_evidence(directory, credential, base)
            if directory == scope and not put_back:
                put_back.append(self.swap_aside(scope))
            return result
        admits = []
        with patch.object(proc_module, "_ownership_evidence", evidence_then_dangle):
            (retired, refused), counts = self.run_counted(self.changing(lambda: None, admits))
        self.assertEqual(len(put_back), 1)
        self.assertEqual((retired, refused),
                         ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual((counts["removals"], counts["deletions"], admits), ([], [], []))
        put_back.pop()()
        (retired, refused), counts = self.run_counted()
        self.assertEqual((retired, refused, counts["deletions"]), ([scope], [], [scope]))


def stat_size_index():
    """The index of ``st_size`` in an ``os.stat_result`` sequence."""
    import stat
    return stat.ST_SIZE


class R26NonWaitingRecordTests(unittest.TestCase):
    """Task 8 R26-1: an ownership record that is a FIFO (or another special
    file) never makes a route WAIT. Every consumer reads a credential, the
    binding key, or an owned root's nonce or group record through
    ``read_ownership_record`` — one ``O_NONBLOCK`` open, ``fstat`` on what was
    opened, a bounded read — and reports it UNAVAILABLE. The retirement's
    first evidence read GATES its readers. And the SHARED STAMP WRITER
    (``spawn_stamp._write_record``, 1q–1y) never waits either and never
    writes through what it should not: on the production spawn the child
    refuses to exec and the parent reports a STARTED, UNRESOLVED process.

    Each route runs on the PRODUCTION composition: recovery
    (``recover_attributed``), the cleanup hold (``owned_scope_refusals``),
    the retirement (``retire_workflow_scopes``) and the verification reader
    (``verification.prior_ownership``). Each is TIMEOUT-GUARDED (``bounded``):
    a route that waits fails the test, and its own FIFO is then opened for
    writing so that the waiting open returns and the thread ends. NO TEARDOWN
    runs beneath a live route: the inherited ``tearDown`` and every cleanup of
    this case, its fixtures' included, first require each route's thread
    OBSERVED terminated, and are otherwise WITHHELD, each failing the case
    by name (``routes_settled``, ``guarded_cleanup``). Every CHILD this
    fixture starts — the R21 occupant and each writer spawn — is BOOKED at
    its creation (``booking``: fixture bookkeeping, never ownership evidence)
    and settled at cleanup SAFELY (``settle_child``): signalled only while
    freshly proven this process's own uncollected child, then collected
    through its own handle, then judged by signal 0 alone. A child NOT
    observed ended withholds every DESTRUCTIVE cleanup, while the SAFE steps
    (``SAFE_STEPS``) still run.
    Covered: a PRE-EXISTING FIFO, and a TYPE SUBSTITUTED between an
    observation and the open.

    Asserted separately, each by its own assertion:
    - the truthful unavailable classification;
    - the unchanged durable state (the record, the credential's bytes, the
      store, the live group);
    - ZERO reaps, signals, credential removals and scope deletions, counted
      at their calls;
    - exact restored progress (one reap, or one retirement).

    R21UnavailableObservationTests' fixture (a production assignment holding
    a REAL live stamped group), with R22's bindings, borrowed without their
    tests. It then waits until the occupant's OWN stamp is done
    (``await_child_stamp``), so no substituted record has a pending writer."""

    _R21 = R21UnavailableObservationTests
    _R22 = R22TruthfulRecoveryTests
    _AG = ScopeAssignmentCredentialTests
    CONTROL = _R21.CONTROL
    OWNER = _R21.OWNER
    UNAVAILABLE = _R21.UNAVAILABLE
    assert_no_survivors = _AG.assert_no_survivors
    live_group_in = _AG.live_group_in
    recovered = _R21.recovered
    cli_lines = _R21.cli_lines
    unavailable_lines = _R21.unavailable_lines
    recovered_once_restored = _R21.recovered_once_restored
    no_effects = _R22.no_effects
    identity = _R22.identity
    intact = _R22.intact
    effects = R25HeldRetirementTests.effects
    #: The existing test-cleanup signal (no collection): used ONLY on a child
    #: freshly proven this process's own uncollected child (``settle_child``).
    kill_group = staticmethod(RetireProcessScopesTests.kill_group)

    #: The bound a route has to RETURN within; any wait fails the test.
    BOUND = 10.0
    #: How many times a waiting route's own FIFOs are released (one second
    #: of joining each) before its thread is judged NOT observed terminated.
    RELEASE_PASSES = 30
    NOT_REGULAR = "NotARegularRecord"
    #: The SAFE cleanup steps: settling this fixture's own BOOKED children, and
    #: the survivor check, which only reads. They run even while a child is
    #: NOT observed ended; every other cleanup — a record put back, a tree or
    #: store removed — is DESTRUCTIVE, and is then withheld.
    SAFE_STEPS = ("reap_writer_scope", "release_occupant", "assert_no_survivors")

    def setUp(self):
        self.occupants = []
        with self.booking(self.occupants):                   # the R21 occupant, BOOKED
            self._R21.setUp(self)
        self.await_child_stamp()

    # -- helpers -------------------------------------------------------------------

    def await_child_stamp(self):
        """The occupant's root is stamped TWICE: by the parent
        (``spawn_owned``, which R21's fixture waits for) and by the child
        itself, BEFORE it execs (``spawn_stamp``). A record substituted
        while the child's stamp is still pending would be opened FOR
        WRITING by the occupant's own stamp — a writer this fixture does not
        control, which can satisfy a waiting read. So this waits (bounded)
        until the occupant has exec'd its command, i.e. its own stamp is
        done. Observed through ``ps`` only; nothing is signalled."""
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            listed = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(self.pgid)],
                                    capture_output=True, text=True)
            command = listed.stdout.strip()
            if command and "spawn_stamp" not in command:
                return
            time.sleep(0.02)
        self.fail("the occupant never finished its own stamp")

    def make_fifo(self, path):
        """``path`` — a real record — becomes a FIFO with NO writer, so a plain
        ``open`` for reading would wait forever; the real record waits aside.
        ``self.restore()`` puts it back, and so does the cleanup (which runs
        before the occupant's reap and the base's removal). Returns ``path``."""
        import stat
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        aside = os.path.join(holding, "aside")
        os.rename(path, aside)
        os.mkfifo(path, 0o600)

        def put_back():
            if os.path.lexists(path) and stat.S_ISFIFO(os.lstat(path).st_mode):
                os.unlink(path)
                os.rename(aside, path)
        self.addCleanup(put_back)
        self.restore = put_back
        return path

    def bounded(self, call, fifos, *args, **kwargs):
        """``call(*args, **kwargs)`` on a thread, TIMEOUT-GUARDED: it must return
        within ``BOUND``. The thread is REGISTERED before it starts, so no
        cleanup of this case runs beneath it while it lives
        (``guarded_cleanup``). If it waits, this fixture's own ``fifos`` (a
        list, read when needed) are released (``release``) and the test
        FAILS, saying whether the thread was then OBSERVED terminated.
        Returns the call's result, or raises its exception."""
        import threading
        box = {}

        def run():
            try:
                box["result"] = call(*args, **kwargs)
            except BaseException as exc:                 # noqa: BLE001 - re-raised below
                box["error"] = exc
        name = getattr(call, "__name__", repr(call))
        thread = threading.Thread(target=run, name="bounded-route-%s" % name, daemon=True)
        self.__dict__.setdefault("routes", []).append((thread, fifos))
        thread.start()
        thread.join(self.BOUND)
        waited = thread.is_alive()
        self.release(thread, fifos)
        alive = thread.is_alive()
        self.assertFalse(waited or alive, "the route WAITED on an ownership record (%s); its"
                         " thread %s" % (name, "is STILL ALIVE: every cleanup is withheld"
                                         if alive else "was then observed terminated"))
        if "error" in box:
            raise box["error"]
        return box["result"]

    def release(self, thread, fifos):
        """End a route waiting on this fixture's OWN FIFOs: while ``thread``
        lives, each of ``fifos`` is opened for writing and then for reading,
        each without waiting, and closed — a waiting READ open then returns
        and sees end of file; a waiting WRITE open (a stamp) then returns and
        its write finds no reader — and the thread is joined again, at most
        ``RELEASE_PASSES`` times. Nothing is killed. Whether it ended is read
        from ``is_alive`` afterwards, never assumed from a join."""
        for _ in range(self.RELEASE_PASSES):
            if not thread.is_alive():
                return
            for fifo in list(fifos):
                for mode in (os.O_WRONLY, os.O_RDONLY):
                    try:
                        os.close(os.open(fifo, mode | os.O_NONBLOCK))
                    except OSError:
                        pass
            thread.join(1.0)

    def routes_settled(self):
        """True once every route thread ``bounded`` started is OBSERVED
        terminated: each still alive has its own FIFOs released and is
        joined again, and then ``is_alive`` decides. Once False, it stays
        False for the rest of this case."""
        if self.__dict__.get("unsettled"):
            return False
        routes = self.__dict__.get("routes", ())
        for thread, fifos in routes:
            self.release(thread, fifos)
        self.unsettled = ["route thread %s" % thread.name for thread, _fifos in routes
                          if thread.is_alive()]
        return not self.unsettled

    def withhold(self, step, args=()):
        """Record ``step`` as WITHHELD and fail the case naming it, and what
        is retained."""
        self.__dict__.setdefault("withheld", []).append(step)
        routes = list(self.__dict__.get("unsettled") or ())
        raise AssertionError(
            "%s WITHHELD%s: not observed ended — %s; %s (base %s, occupant group %s"
            " retained)" % (
                "cleanup" if args is not None else step,
                " (%s%r)" % (step, args) if args is not None else "",
                ", ".join(routes + list(self.__dict__.get("unsettled_children") or ())),
                "nothing is removed, restored or reaped beneath them" if routes else
                "nothing is removed or restored beneath them; only this fixture's own"
                " booked children are settled, each signalled only while freshly proven"
                " its uncollected child", getattr(self, "base", None),
                getattr(self, "pgid", None)))

    def tearDown(self):
        """The inherited ``tearDown`` runs only once every route is OBSERVED
        terminated — it runs BEFORE any registered cleanup, so it is gated
        here, not there. Otherwise it is WITHHELD and the case fails."""
        if not self.routes_settled():
            self.withhold("tearDown", None)
        super(R26NonWaitingRecordTests, self).tearDown()

    def addCleanup(self, function, *args, **kwargs):
        """EVERY cleanup of this case — its own and its fixtures': a record
        put back, a store removed, the occupant settled — runs through
        ``guarded_cleanup``."""
        super(R26NonWaitingRecordTests, self).addCleanup(self.guarded_cleanup, function,
                                                         args, kwargs)

    def safe_step(self, function):
        """Whether ``function`` is a SAFE step (``SAFE_STEPS``)."""
        return getattr(function, "__name__", None) in self.SAFE_STEPS

    def guarded_cleanup(self, function, args, kwargs):
        """Run ``function`` only once every route is OBSERVED terminated
        (``routes_settled``). Otherwise it is WITHHELD — not run — and the
        case fails naming it; so is every cleanup after it. Nothing is
        removed, restored or reaped beneath a route that may still be
        running, and the retained tree and occupant are named. Once a CHILD
        is NOT observed ended (``unsettled_children``), every DESTRUCTIVE
        cleanup is withheld the same way, while the SAFE steps still run:
        unknown outcomes stay reported, and the evidence stays retained."""
        if not self.routes_settled():
            self.withhold(getattr(function, "__name__", repr(function)), args)
        if self.__dict__.get("unsettled_children") and not self.safe_step(function):
            self.withhold(getattr(function, "__name__", repr(function)), args)
        return function(*args, **kwargs)

    def guarded_patches(self, *patches):
        """Task 8 R27: ``patches`` ENTERED now, and UNDONE by a GUARDED cleanup
        — never beneath a thread ``bounded`` started that is not OBSERVED
        terminated (a ``with`` block would undo them on any exception,
        whatever still runs). The case ``close``s it itself once every such
        thread is observed ended (each ``bounded`` call returned)."""
        import contextlib
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for patch in patches:
            stack.enter_context(patch)
        return stack

    def store_state(self):
        """The credential store as the OS shows it, read WITHOUT opening any
        special file: each entry's type and inode, and a regular file's
        bytes."""
        import stat
        store = proc_module.assignment_base(self.base)
        seen = []
        for name in sorted(os.listdir(store)):
            info = os.lstat(os.path.join(store, name))
            data = None
            if stat.S_ISREG(info.st_mode):
                with open(os.path.join(store, name), "rb") as handle:
                    data = handle.read()
            seen.append((name, stat.S_IFMT(info.st_mode), info.st_ino, data))
        return seen

    def every_route(self, fifos, unavailable, results, unattributed, hold, refused):
        """The recovery, the cleanup hold and the retirement, each
        timeout-guarded. Effects are counted at their calls and must stay
        ZERO: reaps, signals, credential removals, scope deletions. The
        reports must be EXACTLY as given."""
        self.assertEqual(self.bounded(proc_module.owned_scope_refusals, fifos, self.CONTROL,
                                      "wf-live", base=self.base), (hold, None))
        (removal, deletion), made = self.effects()
        with self.no_effects(), removal, deletion:
            report = self.bounded(self.recovered, fifos)
            retired, refusals = self.bounded(proc_module.retire_workflow_scopes, fifos,
                                             self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(made["removals"], [])                  # ZERO credential removals
        self.assertEqual(made["deletions"], [])                 # ZERO scope deletions
        self.assertEqual((report[0], report[1], report.unavailable),
                         (results, unattributed, unavailable))
        self.assertEqual([line for line in self.cli_lines(report) if "UNAVAILABLE" in line],
                         self.unavailable_lines(unavailable))
        self.assertEqual((retired, refusals), ([], refused))
        self.intact()

    def is_fifo(self, path):
        import stat
        return stat.S_ISFIFO(os.lstat(path).st_mode)

    # -- a PRE-EXISTING FIFO, each record of the family -------------------------------

    def test_R26_1a_a_FIFO_group_record_never_waits_and_is_unavailable_everywhere(self):
        """The live root's GROUP record is a FIFO. Every reader returns:
        recovery reports it UNAVAILABLE ("the group record cannot be read
        (NotARegularRecord)"), and the hold and the retirement refuse it as
        unreadable. Zero effects. The FIFO, the credential's bytes and the
        live group are all untouched. Restored, recovery reaps exactly once."""
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        with open(self.credential, "rb") as handle:
            stored = handle.read()
        fifo = self.make_fifo(record)
        gap = [(record, "%s: the group record cannot be read (%s)"
                % (self.UNAVAILABLE, self.NOT_REGULAR))]
        self.assertEqual(self.bounded(proc_module.owned_roots_observed, [fifo], self.scope),
                         ([], gap))
        self.assertEqual(self.bounded(proc_module.retirement_refusal, [fifo], self.scope),
                         proc_module.RETIRE_REFUSED_UNREADABLE)
        unreadable = [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]
        self.every_route([fifo], gap, [], [], unreadable, unreadable)
        self.assertTrue(self.is_fifo(record))
        with open(self.credential, "rb") as handle:
            self.assertEqual(handle.read(), stored)
        self.recovered_once_restored()

    def test_R26_1b_the_first_evidence_read_GATES_the_retirements_readers(self):
        """The retirement's FIRST evidence read already disproves a FIFO group
        record, so NO reader opens it inside the retirement. The record is
        never opened, and ``retirement_refusal`` is never called: the gate
        refuses first, with the readers' own verdict. (The bounded readers
        would also return; this case binds the GATE.)"""
        from unittest.mock import patch
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        fifo = self.make_fifo(record)
        real_open, real_refusal = os.open, proc_module.retirement_refusal
        opened, refusals = [], []

        def counting_open(path, *args, **kwargs):
            if path == record:
                opened.append(path)
            return real_open(path, *args, **kwargs)

        def refusal(directory):
            refusals.append(directory)
            return real_refusal(directory)
        with patch.object(os, "open", counting_open), \
                patch.object(proc_module, "retirement_refusal", refusal):
            result = self.bounded(proc_module.retire_workflow_scopes, [fifo], self.CONTROL,
                                  "wf-live", base=self.base)
        self.assertEqual(result, ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual(opened, [])                  # the record never opened
        self.assertEqual(refusals, [])                # no reader ran for the scope
        self.recovered_once_restored()

    def test_R26_1c_a_FIFO_corroboration_record_never_waits(self):
        """The live root's NONCE record is a FIFO. ``group_is_ours`` raises
        NotARegularRecord at once. Recovery reports the live group
        UNCORROBORATED (unavailable), never signals it, and the hold and the
        retirement refuse it as unreadable. Zero effects. Restored, recovery
        reaps exactly once."""
        nonce = os.path.join(self.root, proc_module.OWNED_ROOT_NONCE_FILE)
        fifo = self.make_fifo(nonce)
        with self.assertRaises(proc_module.NotARegularRecord):
            self.bounded(proc_module.group_is_ours, [fifo], self.root)
        reason = "%s (%s)" % (proc_module.UNCORROBORATED_UNAVAILABLE, self.NOT_REGULAR)
        unreadable = [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]
        self.every_route([fifo], [(self.root, reason)],
                         [(self.identity(), [], [], [], [(self.root, self.pgid, reason)])], [],
                         unreadable, unreadable)
        self.assertTrue(self.is_fifo(nonce))
        self.recovered_once_restored()

    def test_R26_1d_a_FIFO_credential_never_waits_and_is_never_removed(self):
        """The CREDENTIAL is a FIFO. The proof read returns at once as
        UNAVAILABLE ("the assignment record: NotARegularRecord"), never
        MISSING or MALFORMED. Recovery reports the scope unattributed and
        unavailable, and the hold and the retirement refuse it. The FIFO is
        never removed or replaced, and nothing in the store is written.
        Restored, recovery reaps exactly once."""
        name = os.path.basename(self.scope)
        before = self.store_state()
        fifo = self.make_fifo(self.credential)
        reason = "%s (the assignment record: %s)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.NOT_REGULAR)
        self.assertEqual(self.bounded(proc_module.read_assignment, [fifo], name,
                                      base=self.base), (None, reason))
        self.assertEqual(self.bounded(proc_module.validate_assignment, [fifo], self.scope,
                                      base=self.base), (None, reason))
        unattributed = [(self.scope, "%s (%s)" % (proc_module.RETIRE_REFUSED_UNATTRIBUTED,
                                                  reason))]
        self.every_route([fifo], [(self.scope, reason)], [], [(self.scope, reason)],
                         unattributed, unattributed)
        self.assertTrue(self.is_fifo(self.credential))
        self.restore()
        self.assertEqual(self.store_state(), before)     # nothing written in the store
        self.recovered_once_restored()

    def test_R26_1e_a_FIFO_binding_key_never_waits_and_is_never_replaced(self):
        """The store's BINDING KEY is a FIFO. Every proof read returns at once
        as UNAVAILABLE ("the store's binding key: NotARegularRecord"), never
        FORGED. The recovery, the hold and the retirement report and refuse
        with zero effects. The WRITER (``assign_scope``) raises
        NotARegularRecord rather than install a key: an unreadable existing
        key is never authority to replace it, and nothing is written.
        Restored, the pre-existing assignment verifies and recovery reaps
        exactly once."""
        key = os.path.join(proc_module.assignment_base(self.base), proc_module.ASSIGNMENT_KEY_FILE)
        fifo = self.make_fifo(key)
        before = self.store_state()
        reason = "%s (the store's binding key: %s)" % (
            proc_module.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.NOT_REGULAR)
        self.assertEqual(self.bounded(proc_module.validate_assignment, [fifo], self.scope,
                                      base=self.base), (None, reason))
        unattributed = [(self.scope, "%s (%s)" % (proc_module.RETIRE_REFUSED_UNATTRIBUTED,
                                                  reason))]
        self.every_route([fifo], [(self.scope, reason)], [], [(self.scope, reason)],
                         unattributed, unattributed)
        with self.assertRaises(proc_module.NotARegularRecord):
            self.bounded(proc_module.assign_scope, [fifo], proc_module.OWNER_TYPE_WORKFLOW,
                         self.CONTROL, "wf-other", "t-other", base=self.base)
        self.assertEqual(self.store_state(), before)     # no key installed, no credential
        self.assertTrue(self.is_fifo(key))
        self.restore()
        self.assertEqual(proc_module.validate_assignment(
            self.scope, base=self.base, current_owners={self.OWNER}), (self.identity(), None))
        self.recovered_once_restored()

    # -- a TYPE SUBSTITUTED between an observation and the open -------------------------

    def substituted_after(self, owner, name, path, swapped):
        """A patch: ``owner.name`` runs as usual, and after its FIRST return
        ``path`` (seen regular by it) becomes a FIFO, before any later open."""
        from unittest.mock import patch
        real = getattr(owner, name)

        def wrapped(*args, **kwargs):
            result = real(*args, **kwargs)
            if not swapped:
                swapped.append(self.make_fifo(path))
            return result
        return patch.object(owner, name, wrapped)

    def test_R26_1f_a_group_record_substituted_after_the_retirements_evidence(self):
        """The group record is REGULAR when the retirement's first evidence read
        sees it (so the gate passes), and it becomes a FIFO before
        ``retirement_refusal`` opens it. The open does not wait: the scope is
        refused as unreadable, with zero effects. Restored, recovery reaps
        exactly once."""
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        swapped = []
        (removal, deletion), made = self.effects()
        with self.substituted_after(proc_module, "_ownership_evidence", record, swapped), \
                self.no_effects(), removal, deletion:
            retired, refused = self.bounded(proc_module.retire_workflow_scopes, swapped,
                                            self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(swapped, [record])
        self.assertEqual((retired, refused),
                         ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertTrue(self.is_fifo(record))
        self.intact()
        self.recovered_once_restored()

    def test_R26_1g_a_group_record_substituted_after_recoverys_validation(self):
        """The group record is regular when recovery VALIDATES the scope, and it
        becomes a FIFO before recovery reads it. The read does not wait: the
        record is reported UNAVAILABLE, and nothing is reaped or signalled.
        Restored, recovery reaps exactly once."""
        record = os.path.join(self.root, proc_module.OWNED_ROOT_PGID_FILE)
        swapped = []
        gap = [(record, "%s: the group record cannot be read (%s)"
                % (self.UNAVAILABLE, self.NOT_REGULAR))]
        with self.substituted_after(proc_module, "validate_assignment", record, swapped), \
                self.no_effects():
            report = self.bounded(self.recovered, swapped)
        self.assertEqual(swapped, [record])
        self.assertEqual((report[0], report[1], report.unavailable), ([], [], gap))
        self.intact()
        self.recovered_once_restored()

    # -- the VERIFICATION reader ---------------------------------------------------------

    def verification_scope_with_a_spent_root(self):
        """A verification scope, assigned as the producer assigns it, holding one
        owned root stamped with a group that is gone. Returns its group
        record's path."""
        from target_runtime import verification as verification_module
        scope = proc_module.assign_scope(
            proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL, "wf-ver",
            verification_module.VERIFICATION_OWNER_UNIT, base=self.base)
        root = os.path.join(proc_module.owned_root_base(scope), "own-0000000000000000")
        os.makedirs(root)
        record = os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)
        with open(record, "w") as handle:
            handle.write(str(_definitely_dead_pgid()))
        return record

    def test_R26_1h_the_verification_reader_never_waits(self):
        """The verification scope's group record is a FIFO — pre-existing, and
        then substituted after the reader's assignment check. Each time
        ``prior_ownership`` and ``owned_root_count`` return at once with the
        record UNAVAILABLE (never "unstamped", never clear). Restored, the
        scope is clear with its one owned root."""
        from target_runtime import verification as verification_module
        record = self.verification_scope_with_a_spent_root()
        detail = ("owned root own-0000000000000000's group record cannot be read (%s)"
                  % self.NOT_REGULAR)
        fifo = self.make_fifo(record)
        self.assertEqual(self.bounded(verification_module.prior_ownership, [fifo], "wf-ver",
                                      self.CONTROL, scope_base=self.base),
                         (verification_module.PRIOR_UNAVAILABLE, detail))
        self.assertEqual(self.bounded(verification_module.owned_root_count, [fifo], "wf-ver",
                                      self.CONTROL, scope_base=self.base), (None, detail))
        self.restore()
        swapped = []
        with self.substituted_after(proc_module, "validate_assignment", record, swapped):
            self.assertEqual(self.bounded(verification_module.prior_ownership, swapped,
                                          "wf-ver", self.CONTROL, scope_base=self.base),
                             (verification_module.PRIOR_UNAVAILABLE, detail))
        self.assertEqual(swapped, [record])
        self.restore()
        self.assertEqual(verification_module.prior_ownership("wf-ver", self.CONTROL,
                                                             scope_base=self.base),
                         (verification_module.PRIOR_CLEAR,
                          "1 owned root(s), every recorded group gone"))
        self.assertEqual(verification_module.owned_root_count("wf-ver", self.CONTROL,
                                                              scope_base=self.base), (1, None))

    # -- controls: ordinary and valid linked records, directories, bounds ---------------

    def test_R26_1i_the_reader_follows_valid_links_keeps_directories_and_is_bounded(self):
        """The shared reader, on its own, for the controls the routes rely on:
        - an ordinary record, and a VALID link to one, read exactly as before;
        - a FIFO, reached directly or through a link, refused at once;
        - a directory still raises ``IsADirectoryError`` (the existing
          MALFORMED / unavailable distinctions stand);
        - genuine absence still raises ``FileNotFoundError``;
        - a record over its bound raises ``OversizedRecord`` with nothing
          read, while a credential's larger bound reads it whole.
        Each outcome is the bytes read or the CLASS of the OSError raised, so
        a wrong refusal fails an assertion rather than erroring."""
        from unittest.mock import patch
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        regular, link = os.path.join(holding, "record"), os.path.join(holding, "link")
        fifo, fifo_link = os.path.join(holding, "fifo"), os.path.join(holding, "fifo-link")
        directory = os.path.join(holding, "directory")
        with open(regular, "wb") as handle:
            handle.write(b"12345")
        os.symlink(regular, link)
        os.mkfifo(fifo, 0o600)
        os.symlink(fifo, fifo_link)
        os.mkdir(directory)

        def outcome(path, **kwargs):
            try:
                return proc_module.read_ownership_record(path, **kwargs)
            except OSError as exc:
                return exc.__class__.__name__
        self.assertEqual(outcome(regular), b"12345")
        self.assertEqual(outcome(link), b"12345")                   # a VALID link reads
        for path in (fifo, fifo_link):
            self.assertEqual(self.bounded(outcome, [fifo], path), self.NOT_REGULAR)
        self.assertEqual(outcome(directory), "IsADirectoryError")
        self.assertEqual(outcome(os.path.join(holding, "absent")), "FileNotFoundError")
        large = os.path.join(holding, "large")
        with open(large, "wb") as handle:
            handle.write(b"7" * (proc_module.OWNERSHIP_RECORD_BYTES + 1))
        real_read, reads = os.read, []

        def counting_read(descriptor, size):
            data = real_read(descriptor, size)
            reads.append(len(data))
            return data
        with patch.object(os, "read", counting_read):
            self.assertEqual(outcome(large), "OversizedRecord")
            self.assertEqual(reads, [])                         # never read
            self.assertEqual(len(proc_module.read_ownership_record(
                large, limit=proc_module.CREDENTIAL_RECORD_BYTES)),
                proc_module.OWNERSHIP_RECORD_BYTES + 1)
        self.assertLessEqual(sum(reads), proc_module.OWNERSHIP_RECORD_BYTES + 1)
        self.assertTrue(all(size <= proc_module.READ_BLOCK_BYTES for size in reads))
        # A record that GREW after its fstat (the size it reported was small):
        # the read stops at the bound + 1 bytes and refuses.
        grown = os.path.join(holding, "grown")
        with open(grown, "wb") as handle:
            handle.write(b"8" * (4 * proc_module.OWNERSHIP_RECORD_BYTES))
        real_fstat = os.fstat

        def small_fstat(descriptor):
            info = real_fstat(descriptor)
            fields = list(info)
            fields[stat_size_index()] = 8
            return os.stat_result(fields)
        del reads[:]
        with patch.object(os, "read", counting_read), patch.object(os, "fstat", small_fstat):
            self.assertEqual(outcome(grown), "OversizedRecord")
        self.assertLessEqual(sum(reads), proc_module.OWNERSHIP_RECORD_BYTES + 1)

    def test_R26_1j_the_dangling_credentials_first_evidence_read_GATES_its_reader(self):
        """A credential whose scope is already gone is retired by its own record.
        Its FIRST evidence read is unbound here (an injected metadata failure on
        the binding key, every snapshot), so the credential is refused BEFORE
        its reader runs — with the readers' verdict, unchanged — and it is
        never removed. (Without the gate, the reader would run and the
        later check would refuse in its own words.) Restored, the credential
        is removed exactly once."""
        import errno
        from unittest.mock import patch
        scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                         "wf-gone", "t-gone", base=self.base)
        remove(scope)
        credential = proc_module.assignment_path(os.path.basename(scope), self.base)
        key = os.path.join(proc_module.assignment_base(self.base), proc_module.ASSIGNMENT_KEY_FILE)
        with open(credential, "rb") as handle:
            stored = handle.read()
        real_stat = os.stat

        def failing_stat(name, *args, **kwargs):
            if (name == key and kwargs.get("dir_fd") is None
                    and kwargs.get("follow_symlinks") is False):
                raise OSError(errno.EIO, "injected metadata failure")
            return real_stat(name, *args, **kwargs)
        (removal, deletion), made = self.effects()
        with patch.object(os, "stat", failing_stat), removal, deletion:
            retired, refused = self.bounded(proc_module.retire_workflow_scopes, [],
                                            self.CONTROL, "wf-gone", base=self.base)
        self.assertEqual((retired, refused),
                         ([], [(credential, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        with open(credential, "rb") as handle:
            self.assertEqual(handle.read(), stored)
        (removal, deletion), made = self.effects()
        with removal, deletion:
            again = proc_module.retire_workflow_scopes(self.CONTROL, "wf-gone", base=self.base)
        self.assertEqual((again, made["removals"]), (([credential], []), [credential]))
        self.assertFalse(os.path.lexists(credential))

    def test_R26_1l_a_DIRECTORY_credential_stays_MALFORMED_on_every_route(self):
        """A control for the EXISTING malformed evidence: the CREDENTIAL is a
        DIRECTORY. The shared reader keeps ``IsADirectoryError`` for it, so
        every route classifies it exactly as before — MALFORMED (an entry that
        is not a record), never unavailable, never absent. Recovery reports
        the scope unattributed and NOT unavailable; the hold and the
        retirement refuse it; zero effects. Restored, recovery reaps exactly
        once."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        aside = os.path.join(holding, "aside")
        os.rename(self.credential, aside)
        os.mkdir(self.credential)

        def put_back():
            if os.path.isdir(self.credential) and not os.path.islink(self.credential):
                os.rmdir(self.credential)
                os.rename(aside, self.credential)
        self.addCleanup(put_back)
        self.restore = put_back
        malformed = proc_module.UNATTRIBUTED_MALFORMED
        self.assertEqual(proc_module.read_assignment(os.path.basename(self.scope),
                                                     base=self.base), (None, malformed))
        self.assertEqual(proc_module.validate_assignment(self.scope, base=self.base),
                         (None, malformed))
        unattributed = [(self.scope, "%s (%s)" % (proc_module.RETIRE_REFUSED_UNATTRIBUTED,
                                                  malformed))]
        self.every_route([], [], [], [(self.scope, malformed)], unattributed, unattributed)
        self.assertTrue(os.path.isdir(self.credential))
        self.recovered_once_restored()

    # -- Task 8 R26, the shared stamp WRITER, on the production spawn ---------------------
    #
    # ``spawn_owned`` → ``Popen`` of the stamping wrapper → the CHILD's own stamp
    # before ``execvp`` (refusing to exec when it cannot) → the PARENT's
    # confirming stamp (``record_owned_root_group``), both through
    # ``spawn_stamp._write_record``. The command each spawn runs appends ONE byte
    # to a marker file, so the ACTUAL-COMMAND invocation count is read from
    # bytes, apart from every report. Every child these cases start is BOOKED
    # at its creation and settled at cleanup (``settle_owned``): signalled only
    # while freshly proven this process's uncollected child, never again once
    # collected (the fixture's own children only).

    #: The counted command: ONE byte per run, written under an explicit file
    #: lifetime (closed, so flushed, before the optional sleep).
    WRITER_CODE = ("import sys, time\n"
                   "with open(sys.argv[1], 'ab') as handle:\n"
                   "    handle.write(b'x')\n"
                   "time.sleep(float(sys.argv[2]))\n")
    #: How long a started child's wait may take before it is judged NOT
    #: observed ended.
    WAIT_SECONDS = 10.0

    def writer_scope(self):
        """A private ownership scope (its ledger and owned roots) for the
        writer cases, beside the R21 fixture's own. Its BOOKED children are
        settled at cleanup (``reap_writer_scope``) BEFORE any earlier-registered
        cleanup — the tree's removal among them."""
        scope = os.path.join(self.base, "writer-scope")
        os.makedirs(scope)
        self.writer_marker = os.path.join(self.base, "writer-marker")
        self.writer_processes, self.writer_children = [], []
        self.addCleanup(self.reap_writer_scope, scope)
        return scope

    def booking(self, book):
        """FIXTURE BOOKKEEPING, never ownership evidence: while active, every
        child the production spawn starts through the stamping wrapper is
        BOOKED in ``book`` at the point it is CREATED — ``(process, started,
        root)``: its process object, its start time read right then, and its
        owned root — whatever the spawn later returns or raises. A mutant that
        discards ``SpawnUnconfirmed``'s process carrier (WM9, the pre-writer
        code) therefore cannot take away this fixture's access to the child it
        created, and the production contract stays exactly as the mutant made
        it. Nothing about ownership is inferred from a booking: it serves this
        fixture's own cleanup only."""
        from unittest import mock
        real, fixture = subprocess.Popen, self

        class Booked(real):
            def __init__(created, args, *rest, **kwargs):
                super(Booked, created).__init__(args, *rest, **kwargs)
                if (isinstance(args, (list, tuple)) and len(args) > 2
                        and args[1] == proc_module._STAMP_WRAPPER):
                    book.append((created, fixture.start_of(created.pid), str(args[2])))
        return mock.patch.object(subprocess, "Popen", Booked)

    #: The ``Popen`` class as this module found it at IMPORT — before any case's
    #: patch, ``booking``'s own included. ``start_of`` reads through it alone.
    FIXTURE_POPEN = subprocess.Popen

    @classmethod
    def start_of(cls, pid):
        """``pid``'s live start time as ``ps -o lstart=`` reports it NOW, or
        None (no such process, or the query failed). FIXTURE BOOKKEEPING on the
        fixture's OWN path — ``ps`` through ``FIXTURE_POPEN`` — so no case's own
        patch reaches it: not one of the product's ``leader_start_time`` (W1s
        controls the PARENT's query with a literal; a booking read through the
        product's one definition recorded that literal, a later fresh read then
        named the still-pinned child "another child", and it was never
        settled), and not one of ``subprocess``. The booked read and every fresh
        one come from here, so they compare like with like; the product's own
        definition is still what its assertions read."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            return None
        try:
            with cls.FIXTURE_POPEN(["ps", "-o", "lstart=", "-p", str(pid)],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL) as query:
                output = query.communicate()[0]
        except (OSError, subprocess.SubprocessError):
            return None
        if query.returncode != 0:
            return None
        return output.decode("utf-8", "replace").strip() or None

    @staticmethod
    def parent_of(pid):
        """What ``ps`` reports NOW as ``pid``'s parent, read WITH its status —
        nothing is signalled — as ``(kind, value)``: ``("listed", ppid)``;
        ``("unlisted", None)`` when ``ps`` ran cleanly and listed no such
        process (status 1, no output, nothing on stderr); otherwise
        ``("unavailable", why)`` — an instrument failure, never read as gone
        and never as a parent."""
        try:
            listed = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                    capture_output=True, text=True)
        except OSError as exc:
            return "unavailable", "ps could not run (%s)" % exc.__class__.__name__
        text = listed.stdout.strip()
        if listed.returncode == 0 and text.isdigit():
            return "listed", int(text)
        if listed.returncode == 1 and not text and not listed.stderr.strip():
            return "unlisted", None
        return "unavailable", "ps answered status %d, %r, %r" % (
            listed.returncode, text, listed.stderr.strip()[:80])

    def pid_absent(self, pid):
        """Whether NO process holds ``pid`` — OBSERVED by two reads that agree:
        ``ps`` ran cleanly and lists none, and signal 0 (nothing delivered)
        answers ``ESRCH``. Anything else, an unreadable answer included, is not
        observed absent."""
        kind, _parent = self.parent_of(pid)
        return kind == "unlisted" and proc_module._process_exists(pid) is False

    def pinned(self, process, started):
        """What ``process``'s pid names NOW, read fresh — nothing is signalled —
        as ``(state, why)``:
        - ``collected``: its own handle holds a return code — which may be
          FABRICATED (CPython's wait and poll set 0 on ``ECHILD``, after a
          collection elsewhere), so it is never read as an exit status here;
        - ``absent``: no process holds the pid (``pid_absent``);
        - ``pinned``: ``ps`` names THIS process its parent AND its start time is
          the one BOOKED at its creation — still this process's own UNCOLLECTED
          child, so neither its pid nor the group it leads can have been
          released for reuse;
        - ``another parent`` / ``another child``: a process holds the pid, but
          it is not this child — another process's, or another child of this
          process (a different start time): MISMATCHED identity;
        - ``unavailable``: an answer cannot be read (``ps`` failed, the two
          absence reads disagree, a start time is unreadable or was never
          booked).
        The booking is this fixture's own record: it decides this fixture's own
        cleanup and is never read as ownership evidence."""
        if process.returncode is not None:
            return "collected", "its own handle holds return code %r" % (process.returncode,)
        kind, parent = self.parent_of(process.pid)
        if kind == "unlisted":
            if proc_module._process_exists(process.pid) is False:
                return "absent", "no process holds its pid (ps lists none; signal 0: ESRCH)"
            return "unavailable", "ps lists no such process, but signal 0 does not answer ESRCH"
        if kind != "listed":
            return "unavailable", "its parent cannot be read: %s" % parent
        if parent != os.getpid():
            return "another parent", "ps names %d its parent, not this process" % parent
        start = self.start_of(process.pid)
        if started is None or start is None:
            return "unavailable", "its start time cannot be compared (booked %r, now %r)" % (
                started, start)
        if start != started:
            return "another child", "the pid names another child of this process (started" \
                " %s, not the booked %s)" % (start, started)
        return "pinned", "this process's own uncollected child, started %s" % started

    def account(self, process, reason):
        """An UNFINALIZED handle, ACCOUNTED for and left so: ``reason`` says why
        its exit status cannot be recovered — its child was collected OUT OF
        BAND (the production reaper's ``waitpid`` discards the status, and a
        later wait or poll here would FABRICATE 0), or its child is
        unattributable. It is never waited or polled to look finalized. So a
        ``ResourceWarning: subprocess N is still running`` for it, when the
        handle is collected, is EXPECTED and ATTRIBUTABLE: never evidence of a
        survivor — nor is a missing warning evidence of none."""
        self.__dict__.setdefault("unfinalized", []).append((process.pid, reason))
        sys.stderr.write("R26 fixture: handle %d is left UNFINALIZED (%s); a ResourceWarning"
                         " for it is expected and attributable\n" % (process.pid, reason))

    def settle_child(self, process, started):
        """ONE booked child, settled SAFELY (``settle_owned``). Returns what is
        NOT observed ended about it — empty ONLY when it is OBSERVED ENDED.
        Three outcomes, kept distinct:
        - PROVEN OWN-CHILD COLLECTION (``pinned``): collected through its OWN
          handle first — an exited child at once (``poll``), a live one after
          ``kill_group`` signals its own group (``wait``) — so its handle holds
          its TRUE exit status, and nothing collects it out of band;
        - an ALREADY-COLLECTED handle (``collected``), or a pid no process holds
          (``absent``: collected out of band, ``account``): never signalled,
          never waited or polled;
        - UNAVAILABLE or MISMATCHED identity (``unavailable``, ``another
          parent``, ``another child``): NOT observed ended — never signalled,
          never waited or polled (that could only collect another process, or
          FABRICATE a return code), and ACCOUNTED for.
        OBSERVED ENDED means observed ABSENT: no process holds its pid
        (``pid_absent``) and its group's signal-0 answer is the observed-absent
        one (``_group_alive(...) is False``). A return code alone — possibly
        fabricated — is never taken for it."""
        state, why = self.pinned(process, started)
        signalled = "not signalled"
        if state == "pinned":
            if process.poll() is None:                       # alive: signalled while pinned
                self.kill_group(process.pid)                 # its OWN group: pid == pgid
                signalled = "SIGKILL attempted on its group while pinned"
                try:
                    process.wait(timeout=self.WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    return ["child %d (running after its wait; %s)" % (process.pid, signalled)]
            state, why = "collected", "collected by its own handle while pinned (exit status" \
                " %r)" % (process.returncode,)
        elif state != "collected":
            self.account(process, "%s: %s" % (state, why))
        if state not in ("collected", "absent"):
            return ["child %d (%s; %s: %s — NOT observed ended, not waited)" % (
                process.pid, signalled, state, why)]
        if not self.pid_absent(process.pid):
            return ["child %d (%s; %s, but a process holds its pid, or that cannot be read:"
                    " NOT observed ended)" % (process.pid, signalled, why)]
        if proc_module._group_alive(process.pid) is not False:
            return ["child %d (%s; %s, but its group is not observed gone: NOT signalled"
                    " again)" % (process.pid, signalled, why)]
        return []

    def settle_owned(self, scope, children):
        """SAFE, FRESH-PROVEN settlement of ``children`` — this fixture's own
        BOOKED children whose owned root is under ``scope`` — and a REPORT of
        every group ``scope``'s ledger names that is not one of them. Returns
        what is NOT observed ended (empty when all are):
        1. a child freshly proven still this process's own uncollected child
           (``pinned``) — so neither its pid nor its group can have been
           released for reuse — is COLLECTED through its OWN handle: at once if
           it has exited, otherwise after the existing test-cleanup signal
           (``kill_group``) to its own group; its handle then holds its TRUE
           exit status (a wait that times out leaves it NOT observed ended);
        2. any other handle is never signalled, waited or polled — a wait or
           poll could only collect another process, or FABRICATE a return code
           — and is ACCOUNTED for as unfinalized (``account``);
        3. it is never signalled again: it is OBSERVED ENDED only when observed
           ABSENT — no process holds its pid, and its group answers signal 0
           (which delivers nothing) with the observed-absent answer; anything
           else is NOT observed ended, reported, and not signalled;
        4. a ledger group that is not a booked child is NOT signalled: it is
           reported NOT observed ended (the ledger is read for this report
           only).
        Nothing here depends on why a signal was refused. (An UNCONFIRMED
        observation, not a mechanism: in dev26 the pinned ledger reaper
        reported "could not signal … EPERM" for groups whose leader had not
        yet been waited.)"""
        prefix = os.path.join(os.path.realpath(scope), "")
        mine = [(process, started) for process, started, root in children
                if os.path.realpath(root).startswith(prefix)]
        unsettled = []
        for process, started in mine:
            unsettled.extend(self.settle_child(process, started))
        booked = set(process.pid for process, _started in mine)
        for pgid in sorted(proc_module.owned_groups(scope)):
            if pgid not in booked:
                unsettled.append("ledger group %d (not a child this fixture booked: not"
                                 " signalled)" % pgid)
        return unsettled

    def reap_writer_scope(self, scope):
        """Settle the writer cases' BOOKED children (``settle_owned``) — a SAFE
        step, run even while another child is unsettled. Anything not observed
        ended is recorded in the CHILD gate (``unsettled_children``), so every
        later DESTRUCTIVE cleanup is WITHHELD while the safe steps still run, and
        this cleanup FAILS naming what is retained. An error while settling is
        not swallowed: it is recorded the same way, its cause chained."""
        try:
            unsettled = self.settle_owned(scope, self.__dict__.get("writer_children", ()))
        except Exception as exc:                             # noqa: BLE001 - re-raised below
            unsettled, cause = ["the settlement itself raised %r" % (exc,)], exc
        else:
            cause = None
        if not unsettled:
            return
        self.unsettled_children = list(self.__dict__.get("unsettled_children") or ()) + unsettled
        try:
            roots = sorted(os.listdir(proc_module.owned_root_base(scope)))
        except OSError as exc:
            roots = "unlisted (%s)" % exc.__class__.__name__
        raise AssertionError(
            "owned process(es) NOT observed ended: %s — retained: the writer scope %s, its"
            " ledger %s, its owned roots %s" % (", ".join(unsettled), scope,
                                                proc_module.ledger_path(scope), roots)
        ) from cause

    def invocations(self):
        """How many times the ACTUAL command ran: one byte per run."""
        try:
            with open(self.writer_marker, "rb") as handle:
                return len(handle.read())
        except FileNotFoundError:
            return 0

    def writer_spawn(self, scope, sleep=0.0):
        """``spawn_owned`` of the counting command, as production calls it.
        What the spawn REPORTED — the process it returned, or the one
        ``SpawnUnconfirmed`` carries — is kept in ``writer_processes``; every
        child it CREATED is booked for cleanup in ``writer_children``
        (``booking``), whatever it returns or raises."""
        try:
            with self.booking(self.writer_children):
                process = proc_module.spawn_owned(
                    [sys.executable, "-c", self.WRITER_CODE, self.writer_marker, str(sleep)],
                    label="r26-writer", directory=scope, owned_root_base_dir=scope,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except proc_module.SpawnUnconfirmed as unconfirmed:
            self.writer_processes.append(unconfirmed.process)
            raise
        self.writer_processes.append(process)
        return process

    def release_occupant(self, scope, handle):
        """R26: the R21 occupant is settled as every BOOKED child is
        (``settle_child``) — a SAFE step, run even while another child is
        unsettled — and never through a bare id after a collection (R21's own
        release signals through the ledger, and a recovery may already have
        collected the occupant). An occupant this fixture did not book has no
        booked start, so it is never proven pinned: never signalled and never
        waited, and observed ended only if no process holds its pid and its
        group is observed gone. Not observed ended FAILS the case naming what
        is retained, and is recorded in the CHILD gate, so every later
        destructive cleanup is withheld."""
        booked = [(process, started) for process, started, _root
                  in self.__dict__.get("occupants", ()) if process is handle]
        unsettled = []
        for process, started in booked or [(handle, None)]:
            unsettled.extend(self.settle_child(process, started))
        if not unsettled:
            return
        self.unsettled_children = list(self.__dict__.get("unsettled_children") or ()) + unsettled
        raise AssertionError(
            "owned process(es) NOT observed ended: %s — retained: the occupant scope %s,"
            " its ledger %s" % (", ".join(unsettled), scope, proc_module.ledger_path(scope)))

    def at_creation(self, make):
        """A patch: right after ``create_owned_root`` makes a root — BEFORE the
        spawn — ``make(root)`` puts something in it. ``made`` lists the roots."""
        from unittest.mock import patch
        real, made = proc_module.create_owned_root, []

        def create(nonce, base=None):
            root = real(nonce, base)
            make(root)
            made.append(root)
            return root
        return patch.object(proc_module, "create_owned_root", create), made

    def spawn_outcome(self, scope, fifos):
        """Run the spawn, timeout-guarded, and return ``(raised, process)`` —
        what it raised (None when it returned) and the process it started.

        THE SPAWN BOUNDARY is judged FIRST, ahead of every effect and report:
        when the spawn RAISED, what it raised is ``SpawnUnconfirmed`` — never a
        plain ``OSError``, which a handler reads as "refused before any process
        started" (and which carries no process for this fixture to keep). A
        spawn that RETURNED is not judged here: each case asserts its EFFECTS
        next and its full report after (``unconfirmed``)."""
        raised = None
        try:
            self.bounded(self.writer_spawn, fifos, scope)
        except AssertionError:
            raise                                            # e.g. the route WAITED
        except Exception as exc:                             # noqa: BLE001 - judged below
            raised = exc
        if raised is not None:                               # the spawn boundary, FIRST
            self.assertIsInstance(raised, proc_module.SpawnUnconfirmed,
                                  "a spawn that STARTED a process raised %r, not"
                                  " SpawnUnconfirmed" % (raised,))
        self.assertEqual(len(self.writer_processes), 1)      # ONE process started
        return raised, self.writer_processes[0]

    def unconfirmed(self, raised, cause, number=None):
        """The REPORT: the spawn raised ``SpawnUnconfirmed`` — a process STARTED
        and its ownership is UNRESOLVED, never "no spawn" — with the writer's
        refusal chained: an instance of ``cause``, with ``errno`` ``number``
        when given. Any other outcome FAILS an assertion."""
        unconfirmed = raised
        self.assertIsInstance(unconfirmed, proc_module.SpawnUnconfirmed)
        self.assertIn("a process STARTED (pid %d)" % unconfirmed.process.pid, str(unconfirmed))
        self.assertIn("is UNRESOLVED", str(unconfirmed))
        self.assertIsInstance(unconfirmed.__cause__, cause)
        if number is not None:
            self.assertEqual(unconfirmed.__cause__.errno, number)
        return unconfirmed

    def exit_of(self, process):
        """The started child's exit status (it must end within the bound)."""
        try:
            return process.wait(timeout=self.BOUND)
        except subprocess.TimeoutExpired:
            self.fail("the started child never ended")

    def wait_for(self, condition, what):
        deadline = time.monotonic() + self.BOUND - 2
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail(what)

    def nothing_in(self, descriptor):
        """No byte is readable from a FIFO read end held open by this fixture."""
        try:
            data = os.read(descriptor, 64)
        except BlockingIOError:
            data = b""
        self.assertEqual(data, b"")

    def nothing_written(self, fifo):
        """Nothing is readable from ``fifo``, opened for reading without
        waiting."""
        descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            self.nothing_in(descriptor)
        finally:
            os.close(descriptor)

    def test_R26_1q_a_pre_existing_FIFO_group_record_refuses_both_stamps_at_once(self):
        """The new owned root's GROUP record path is a FIFO before the spawn,
        with no reader. The CHILD's stamp refuses it at once (``ENXIO``, never a
        wait inside ``open``) and the child REFUSES to exec
        (``EXIT_UNSTAMPABLE``): the actual command NEVER runs. The PARENT's
        confirming stamp refuses it too, and ``spawn_owned`` RETURNS by raising
        ``SpawnUnconfirmed``. Nothing is written into the FIFO; START, written
        first, is in place; the ledger names the group and no pending record
        is left; recovery's reader reports the record UNAVAILABLE. Removed,
        the root reads as never stamped."""
        import stat
        scope, fifos = self.writer_scope(), []

        def make(root):
            fifos.append(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE))
            os.mkfifo(fifos[-1], 0o600)
        create, made = self.at_creation(make)
        with create:
            raised, process = self.spawn_outcome(scope, fifos)
        [root], [fifo] = made, fifos
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)                 # the command never ran
        self.assertTrue(self.is_fifo(fifo))
        self.nothing_written(fifo)
        start = os.path.join(root, proc_module.OWNED_ROOT_START_FILE)
        self.assertTrue(os.path.lexists(start)                  # written FIRST
                        and stat.S_ISREG(os.lstat(start).st_mode))
        self.assertEqual(proc_module.owned_groups(scope), {process.pid})
        self.assertEqual(proc_module.pending_nonces(scope), [])
        self.assertEqual(proc_module.owned_roots_observed(scope), ([], [(
            fifo, "%s: the group record cannot be read (%s)" % (self.UNAVAILABLE,
                                                               self.NOT_REGULAR))]))
        self.unconfirmed(raised, OSError, errno.ENXIO)
        os.unlink(fifo)
        self.assertEqual(proc_module.owned_roots_observed(scope), ([(root, None)], []))

    def test_R26_1r_a_FIFO_group_record_WITH_a_reader_is_refused_by_what_was_opened(self):
        """The group record path is a FIFO this fixture holds open for reading
        AND writing, so a stamp's write-open SUCCEEDS without waiting. What was
        opened is checked before any byte: not a regular file, so both stamps
        raise ``UnsupportedRecord``; the child refuses to exec; and NOT ONE
        byte reaches the held reader."""
        scope, fifos, held = self.writer_scope(), [], []

        def make(root):
            fifos.append(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE))
            os.mkfifo(fifos[-1], 0o600)
            held.append(os.open(fifos[-1], os.O_RDONLY | os.O_NONBLOCK))
            held.append(os.open(fifos[-1], os.O_WRONLY | os.O_NONBLOCK))
            for descriptor in held[-2:]:
                self.addCleanup(os.close, descriptor)
        create, made = self.at_creation(make)
        with create:
            raised, process = self.spawn_outcome(scope, fifos)
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.nothing_in(held[0])                                # not one byte written
        self.assertTrue(self.is_fifo(fifos[0]))
        self.unconfirmed(raised, stamp_module.UnsupportedRecord, errno.EINVAL)

    def test_R26_1s_a_FIFO_START_record_refuses_before_any_group_record(self):
        """The START record path is a FIFO before the spawn. START is written
        FIRST, so its refusal leaves NO group record at all — from either
        stamp. The child refuses to exec; the parent raises
        ``SpawnUnconfirmed``. (The parent's start-time query is a controlled
        adapter here, so the parent's stamp always reaches START.)"""
        from unittest.mock import patch
        scope, fifos = self.writer_scope(), []

        def make(root):
            fifos.append(os.path.join(root, proc_module.OWNED_ROOT_START_FILE))
            os.mkfifo(fifos[-1], 0o600)
        create, made = self.at_creation(make)
        with create, patch.object(stamp_module, "leader_start_time",
                                  lambda pid: "a controlled start time"):
            raised, process = self.spawn_outcome(scope, fifos)
        [root] = made
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.assertFalse(os.path.lexists(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)))
        self.assertTrue(self.is_fifo(fifos[0]))
        self.nothing_written(fifos[0])
        self.unconfirmed(raised, OSError, errno.ENXIO)

    def test_R26_1t_a_FIFO_substituted_after_the_childs_stamp_refuses_the_parents(self):
        """The CHILD stamps its root and runs the actual command ONCE; then,
        before the PARENT's confirming stamp, the group record becomes a FIFO
        (the child's own record set aside). The parent's stamp refuses it at
        once, and ``spawn_owned`` RETURNS by raising ``SpawnUnconfirmed``: the
        process STARTED — it ran — and its ownership is UNRESOLVED. Nothing is
        written into the FIFO; the child's record stays exactly its pid."""
        from unittest.mock import patch
        scope, fifos, swapped = self.writer_scope(), [], []
        real_group = proc_module.record_owned_group

        def group_then_substitute(pgid, label, directory=None, nonce=None):
            result = real_group(pgid, label, directory, nonce=nonce)
            self.wait_for(lambda: self.invocations() == 1, "the command never ran")
            record = os.path.join(proc_module.owned_root_base(scope), nonce,
                                  proc_module.OWNED_ROOT_PGID_FILE)
            aside = os.path.join(self.base, "child-record")
            os.rename(record, aside)
            os.mkfifo(record, 0o600)
            fifos.append(record)
            swapped.append(aside)
            return result
        with patch.object(proc_module, "record_owned_group", group_then_substitute):
            raised, process = self.spawn_outcome(scope, fifos)
        [aside], [fifo] = swapped, fifos
        self.assertEqual(self.exit_of(process), 0)
        self.assertEqual(self.invocations(), 1)                 # it RAN, exactly once
        self.assertTrue(self.is_fifo(fifo))
        self.nothing_written(fifo)
        with open(aside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), str(process.pid))
        self.unconfirmed(raised, OSError, errno.ENXIO)

    def victim(self):
        path = os.path.join(self.base, "victim")
        with open(path, "wb") as handle:
            handle.write(b"VICTIM BYTES, LONGER THAN ANY GROUP ID")
        return path

    def test_R26_1u_a_SYMBOLIC_LINK_group_record_is_never_written_through(self):
        """The group record path is a symbolic link to a file outside the root.
        Neither stamp follows it (``ELOOP``): the link's target is never opened,
        written or truncated, and the link itself is unchanged. The child
        refuses to exec; the parent raises ``SpawnUnconfirmed``."""
        scope, victim = self.writer_scope(), self.victim()
        create, made = self.at_creation(lambda root: os.symlink(
            victim, os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)))
        with create:
            raised, process = self.spawn_outcome(scope, [])
        [root] = made
        with open(victim, "rb") as handle:
            self.assertEqual(handle.read(), b"VICTIM BYTES, LONGER THAN ANY GROUP ID")
        self.assertEqual(os.readlink(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)),
                         victim)
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.unconfirmed(raised, OSError, errno.ELOOP)

    def test_R26_1v_a_HARD_LINK_group_record_is_never_written_or_truncated(self):
        """The group record path is a second name of a file outside the root (a
        hard link). It opens as a regular file, and its name count is checked
        before any write or truncation: ``UnsupportedRecord``. The file's
        bytes are unchanged; the child refuses to exec; the parent raises
        ``SpawnUnconfirmed``."""
        scope, victim = self.writer_scope(), self.victim()
        create, made = self.at_creation(lambda root: os.link(
            victim, os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)))
        with create:
            raised, process = self.spawn_outcome(scope, [])
        with open(victim, "rb") as handle:
            self.assertEqual(handle.read(), b"VICTIM BYTES, LONGER THAN ANY GROUP ID")
        self.assertEqual(os.stat(victim).st_nlink, 2)
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.unconfirmed(raised, stamp_module.UnsupportedRecord, errno.EMLINK)

    def test_R26_1w_a_DIRECTORY_group_record_is_refused_and_untouched(self):
        """The group record path is a directory: refused (``EISDIR``), left an
        empty directory. The child refuses to exec; the parent raises
        ``SpawnUnconfirmed`` — not the bare ``IsADirectoryError``."""
        scope = self.writer_scope()
        create, made = self.at_creation(lambda root: os.mkdir(
            os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)))
        with create:
            raised, process = self.spawn_outcome(scope, [])
        [root] = made
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.assertEqual(os.listdir(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE)), [])
        self.unconfirmed(raised, OSError, errno.EISDIR)

    def test_R26_1x_a_root_gone_before_the_stamps_is_refused_and_never_created(self):
        """The owned root is removed after it was created, before either stamp:
        the record paths are UNAVAILABLE (``ENOENT``). Nothing is created in
        its place; the child refuses to exec; the parent raises
        ``SpawnUnconfirmed`` — not the bare ``FileNotFoundError``."""
        import shutil
        scope = self.writer_scope()
        create, made = self.at_creation(shutil.rmtree)
        with create:
            raised, process = self.spawn_outcome(scope, [])
        [root] = made
        self.assertEqual(self.exit_of(process), stamp_module.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)
        self.assertFalse(os.path.lexists(root))
        self.unconfirmed(raised, FileNotFoundError, errno.ENOENT)

    def test_R26_1y_ordinary_records_are_replaced_EXACTLY_in_order_and_durably(self):
        """Controls, on the ordinary path.
        - A spawn into a root whose START and GROUP records already hold
          LONGER content: both are replaced EXACTLY — no stale trailing byte —
          the spawn RETURNS its process, and the command runs once.
        - The shared writer's order and durability (Task 8 R27-1: each record
          is REPLACED atomically): START's replacement is ``fsync``-ed BEFORE
          its rename, the directory AFTER it; only then does the group record's
          replacement exist, ``fsync``-ed before its own rename, the directory
          after. No replacement remains."""
        from unittest.mock import patch
        import stat
        scope = self.writer_scope()

        def make(root):
            for name in (proc_module.OWNED_ROOT_START_FILE, proc_module.OWNED_ROOT_PGID_FILE):
                with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                    handle.write("9" * 300)
        create, made = self.at_creation(make)
        with create:
            process = self.bounded(self.writer_spawn, [], scope, 30.0)
        self.wait_for(lambda: self.invocations() == 1, "the command never ran")
        [root] = made
        records = {}
        for name in (proc_module.OWNED_ROOT_START_FILE, proc_module.OWNED_ROOT_PGID_FILE):
            path = os.path.join(root, name)
            info = os.lstat(path)
            self.assertTrue(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, name)
            with open(path, encoding="utf-8") as handle:
                records[name] = handle.read()
        self.assertEqual(records[proc_module.OWNED_ROOT_PGID_FILE], str(process.pid))
        self.assertEqual(records[proc_module.OWNED_ROOT_START_FILE],
                         proc_module.leader_start_time(process.pid))
        self.assertEqual(proc_module.owned_roots_observed(scope), ([(root, process.pid)], []))
        own = tempfile.mkdtemp()
        self.addCleanup(remove, own)
        real_fsync, seen = os.fsync, []

        def named(name):
            if name.startswith(stamp_module.REPLACEMENT_PREFIX):
                return "replacement of " + name[len(stamp_module.REPLACEMENT_PREFIX):].rsplit(
                    "-", 1)[0]
            return name

        def fsync(descriptor):
            seen.append(sorted(named(name) for name in os.listdir(own)))
            return real_fsync(descriptor)
        with patch.object(os, "fsync", fsync):
            stamp_module.stamp(own, os.getpid())
        start, group = proc_module.OWNED_ROOT_START_FILE, proc_module.OWNED_ROOT_PGID_FILE
        self.assertEqual(seen, [["replacement of " + start], [start],
                                [start, "replacement of " + group], [start, group]])
        self.assertEqual(sorted(os.listdir(own)), [start, group])

    # -- Task 8 R27-1: a FAILED or INTERRUPTED parent confirmation AFTER the child's stamp ---
    #
    # The occupant's root holds the CHILD's own valid stamp (``setUp`` waited for it to
    # exec). Each case runs the PRODUCTION parent confirmation
    # (``record_owned_root_group``) on that root again with ONE controlled fault
    # inside the writer (``stamp_faults``, this process's writer only): what a
    # confirmation that fails, or is stopped, AFTER the child's stamp leaves. The
    # occupant is a REAL live group, so "the original group is alive" is OBSERVED,
    # not modeled. Asserted, each by its own assertion: the records as they stand
    # (bytes, inode, no replacement left); the corroboration; ZERO destructive cleanup
    # while the group lives — every route RETAINS, removals and deletions counted at
    # their calls; then exact settlement WITHOUT replay — one reap, one retirement,
    # nothing repeated.

    START, GROUP = proc_module.OWNED_ROOT_START_FILE, proc_module.OWNED_ROOT_PGID_FILE

    def stamp_records(self):
        """The occupant root's START and group records as they stand: ``{name:
        (bytes, inode)}``."""
        found = {}
        for name in (self.START, self.GROUP):
            path = os.path.join(self.root, name)
            with open(path, "rb") as handle:
                found[name] = (handle.read(), os.lstat(path).st_ino)
        return found

    def confirm_with(self, faults):
        """The production PARENT confirmation of the occupant's root, with
        ``faults`` armed inside the writer. Returns what it raised; it must
        raise."""
        with faults.active():
            try:
                proc_module.record_owned_root_group(self.root, self.pgid)
            except BaseException as exc:                     # noqa: BLE001 - judged by each case
                return exc
        self.fail("the faulted parent confirmation RETURNED")

    def retained_while_live(self):
        """While the recorded group lives, the occupant's records still
        CORROBORATE it as ours, and every route RETAINS the scope: the
        predicate, the retirement's verdict, the cleanup hold and the
        retirement itself — ZERO credential removals and ZERO deletions,
        counted at their calls. The scope, credential, root and live group all
        stand."""
        self.assertEqual(proc_module.group_is_ours(self.root), (self.pgid, None))
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        live = [(self.scope, proc_module.RETIRE_REFUSED_LIVE_GROUP)]
        self.assertEqual(proc_module.retirement_refusal(self.scope),
                         proc_module.RETIRE_REFUSED_LIVE_GROUP)
        self.assertEqual(proc_module.owned_scope_refusals(self.CONTROL, "wf-live",
                                                          base=self.base), (live, None))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(made["removals"], [])                  # ZERO credential removals
        self.assertEqual(made["deletions"], [])                 # ZERO scope deletions
        self.assertEqual(result, ([], live))
        self.intact()

    def settled_once_without_replay(self):
        """Recovery settles the live group from the RETAINED records: exactly
        ONE reap of exactly this group, a second recovery repeats nothing; then
        the retirement retires the scope exactly ONCE — one credential removal,
        one deletion — and a second retirement repeats nothing."""
        report = self.recovered()
        self.assertEqual((report[1], report.unavailable), ([], []))
        self.assertEqual(len(report[0]), 1, report[0])
        identity, reaped, stuck, unstamped, uncorroborated = report[0][0]
        self.assertEqual(tuple(identity), self.OWNER)
        self.assertEqual((reaped, stuck, unstamped, uncorroborated), ([self.pgid], [], [], []))
        self.assertFalse(proc_module._group_alive(self.pgid))
        again = self.recovered()
        self.assertEqual((again[0], again[1], again.unavailable), ([], [], []))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(result, ([self.scope], []))
        self.assertEqual((made["removals"], made["deletions"]), ([self.credential], [self.scope]))
        self.assertFalse(os.path.lexists(self.scope))
        self.assertFalse(os.path.lexists(self.credential))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(result, ([], []))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))

    def test_R27_1a_a_SHORT_parent_write_of_the_GROUP_record_keeps_the_childs_and_retains(self):
        """The parent's confirmation republishes START, then its group-record
        replacement takes a SHORT write — the first bytes of the pid, a
        valid-looking DIFFERENT group — and an I/O failure. The child's group
        record is UNTOUCHED (bytes and inode), no replacement remains, the group
        is still corroborated as ours, every route RETAINS with zero effects;
        recovery then reaps it once and the scope retires once."""
        before = self.stamp_records()
        group = str(self.pgid).encode("ascii")
        self.assertEqual(before[self.GROUP][0], group)            # the CHILD's own stamp
        raised = self.confirm_with(stamp_faults.StampFaults().short_write(
            self.GROUP, max(1, len(group) - 2)))
        self.assertIsInstance(raised, OSError)
        self.assertEqual(raised.errno, errno.EIO)
        after = self.stamp_records()
        self.assertEqual(after[self.GROUP], before[self.GROUP])   # bytes AND inode: untouched
        self.assertEqual(after[self.START][0], before[self.START][0])  # republished, identical
        self.assertEqual(stamp_faults.replacements_in(self.root), [])
        self.retained_while_live()
        self.settled_once_without_replay()

    def test_R27_1b_a_SHORT_parent_write_of_the_START_record_keeps_the_childs_and_retains(self):
        """The parent's START replacement takes a SHORT write — a cut start
        time — and an I/O failure; START is written FIRST, so the group record
        is never reached. Both records are UNTOUCHED (bytes and inodes), no
        replacement remains, the group is corroborated, every route RETAINS
        with zero effects; recovery reaps once, the scope retires once."""
        before = self.stamp_records()
        raised = self.confirm_with(stamp_faults.StampFaults().short_write(self.START, 12))
        self.assertIsInstance(raised, OSError)
        self.assertEqual(raised.errno, errno.EIO)
        self.assertEqual(self.stamp_records(), before)            # bytes AND inodes: untouched
        self.assertEqual(stamp_faults.replacements_in(self.root), [])
        self.retained_while_live()
        self.settled_once_without_replay()

    def test_R27_1c_a_parent_STOPPED_before_its_group_rename_leaves_the_childs_record(self):
        """The parent is STOPPED at its group record's rename (START already
        republished) with no chance to discard what it made. The child's group
        record is UNTOUCHED; its unpublished replacement remains beside it,
        opened by no reader — the records read, and the retirement's evidence
        binds, as before. Every route RETAINS with zero effects; recovery reaps
        once, and the scope retires once, the leftover replacement with it."""
        before = self.stamp_records()
        raised = self.confirm_with(stamp_faults.StampFaults().killed_at_rename(after=1))
        self.assertIsInstance(raised, stamp_faults.Killed)
        after = self.stamp_records()
        self.assertEqual(after[self.GROUP], before[self.GROUP])   # bytes AND inode: untouched
        self.assertEqual(after[self.START][0], before[self.START][0])
        [left] = stamp_faults.replacements_in(self.root)
        self.assertTrue(left.startswith(stamp_module.REPLACEMENT_PREFIX + self.GROUP + "-"), left)
        self.assertEqual(proc_module.owned_root_record(self.root)["pgid"], self.pgid)
        self.assertIsNone(proc_module._unbound_reason(proc_module._ownership_evidence(
            self.scope, self.credential, self.base)))
        self.retained_while_live()
        self.settled_once_without_replay()

    def test_R27_1d_a_failed_DIRECTORY_fsync_after_publication_is_unproven_never_undone(self):
        """The group record's replacement is PUBLISHED (renamed into place) and
        the directory's ``fsync`` then fails: ``PublicationUnproven`` — the
        record IS published and stays so (its inode is the replacement's, its
        bytes the same pid), NEVER rolled back. A valid intact identity with a
        truthful UNRESOLVED report: every route RETAINS with zero effects;
        recovery reaps once, the scope retires once."""
        before = self.stamp_records()
        faults = stamp_faults.StampFaults().failed_directory_fsync(after=1)
        raised = self.confirm_with(faults)
        self.assertIsInstance(raised, stamp_module.PublicationUnproven)
        self.assertEqual(raised.errno, errno.EIO)
        self.assertIn("published; the rename's durability is UNPROVEN", str(raised))
        calls = [call for call, _detail in faults.calls]           # EXECUTED order pin:
        last = len(calls) - 1 - calls[::-1].index("rename")        # after the publication,
        self.assertEqual([call for call in calls[last + 1:]        # nothing undone
                          if call in ("unlink", "rename", "link", "write")], [])
        after = self.stamp_records()
        self.assertEqual(after[self.GROUP][0], before[self.GROUP][0])
        self.assertNotEqual(after[self.GROUP][1], before[self.GROUP][1])  # PUBLISHED, not undone
        self.assertEqual(stamp_faults.replacements_in(self.root), [])
        self.retained_while_live()
        self.settled_once_without_replay()

    def test_R27_1e_a_replacement_racing_the_retirements_evidence_read_is_refused(self):
        """Inode identity is load-bearing: once recovery has settled the group,
        a VALID republication of the group record lands between the retirement's
        evidence ``lstat`` and its open of that record. The reader sees a
        different object and the retirement REFUSES it as unreadable — zero
        removals, zero deletions — never a settlement read through it. A
        retirement with nothing racing then retires once."""
        from unittest.mock import patch
        report = self.recovered()
        self.assertEqual([entry[1] for entry in report[0]], [[self.pgid]])
        real_open, raced = proc_module._open_examined, []

        def racing_open(where, name, info, flags=0):
            if name == self.GROUP and not raced:
                raced.append(name)
                stamp_module._write_record(os.path.join(self.root, self.GROUP), str(self.pgid))
            return real_open(where, name, info, flags)
        (removal, deletion), made = self.effects()
        with removal, deletion, patch.object(proc_module, "_open_examined", racing_open):
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(raced, [self.GROUP])
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertEqual(result, ([], [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
        for path in (self.scope, self.credential, self.root):
            self.assertTrue(os.path.exists(path), path)
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(result, ([self.scope], []))
        self.assertEqual((made["removals"], made["deletions"]), ([self.credential], [self.scope]))

    def test_R27_1f_the_production_spawn_keeps_the_childs_record_through_a_SHORT_parent_write(
            self):
        """``spawn_owned`` end to end: the CHILD stamps its root and runs the
        actual command ONCE, and is still running; the PARENT's confirmation
        then takes a SHORT write of the group record and an I/O failure.
        ``spawn_owned`` raises ``SpawnUnconfirmed`` (the I/O failure chained);
        the child's record is exactly its pid; its group is corroborated as
        ours, alive, and never signalled; the scope reports a live group."""
        from unittest.mock import patch
        scope = self.writer_scope()
        real_group, real_confirm = proc_module.record_owned_group, \
            proc_module.record_owned_root_group

        def group_then_wait(pgid, label, directory=None, nonce=None):
            result = real_group(pgid, label, directory, nonce=nonce)
            self.wait_for(lambda: self.invocations() == 1, "the command never ran")
            return result

        def faulted_confirm(root, pgid):
            keep = max(1, len(str(pgid)) - 2)
            with stamp_faults.StampFaults().short_write(self.GROUP, keep).active():
                return real_confirm(root, pgid)
        raised = None
        with patch.object(proc_module, "record_owned_group", group_then_wait), \
                patch.object(proc_module, "record_owned_root_group", faulted_confirm):
            try:
                self.bounded(self.writer_spawn, [], scope, 120.0)
            except Exception as exc:                         # noqa: BLE001 - judged below
                raised = exc
        self.assertIsInstance(raised, proc_module.SpawnUnconfirmed)   # the spawn boundary, FIRST
        [process] = self.writer_processes
        self.assertIsInstance(raised.__cause__, OSError)
        self.assertEqual(raised.__cause__.errno, errno.EIO)
        root = raised.root
        with open(os.path.join(root, self.GROUP), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), str(process.pid))
        self.assertEqual(stamp_faults.replacements_in(root), [])
        self.assertEqual(proc_module.group_is_ours(root), (process.pid, None))
        self.assertTrue(proc_module.scope_has_live_group(scope))
        self.assertIsNone(process.poll())                         # alive, never signalled
        self.assertEqual(self.invocations(), 1)

    # -- Task 8 R27-1: LEGACY records, on the LIVE occupant — retained, never settled ----

    def legacy_record(self, name, damaged):
        """The occupant root's ``name`` record replaced by ``damaged`` with a
        plain write, as an earlier truncating writer could leave it; returns
        what puts the original back."""
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(damaged)

        def put_back():
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original)
        return put_back

    def retained_contradicted(self, record_pgid, reason):
        """Every route RETAINS the scope as CONTRADICTED with ZERO effects —
        no reap, no signal (counted at the calls), no credential removal, no
        deletion — and recovery REPORTS the root (``record_pgid``, ``reason``);
        the live occupant stands."""
        contradicted = [(self.scope, proc_module.RETIRE_REFUSED_CONTRADICTED)]
        self.assertEqual(proc_module.retirement_refusal(self.scope),
                         proc_module.RETIRE_REFUSED_CONTRADICTED)
        self.assertEqual(proc_module.owned_scope_refusals(self.CONTROL, "wf-live",
                                                          base=self.base), (contradicted, None))
        self.assertTrue(proc_module.scope_has_live_group(self.scope))
        (removal, deletion), made = self.effects()
        with self.no_effects(), removal, deletion:
            report = self.recovered()
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertEqual(result, ([], contradicted))
        self.assertEqual((report[1], report.unavailable), ([], []))
        self.assertEqual(len(report[0]), 1, report[0])
        [(identity, reaped, stuck, unstamped, uncorroborated)] = report[0]
        self.assertEqual(tuple(identity), self.OWNER)
        self.assertEqual((reaped, stuck, unstamped, uncorroborated),
                         ([], [], [], [(self.root, record_pgid, reason)]))
        self.intact()

    def test_R27_1g_a_CUT_start_of_the_LIVE_occupant_is_retained_never_read_as_reused(self):
        """The occupant's START record cut short (its first 12 characters): a
        FRAGMENT of this leader's start, never a reused id. Retained on every
        route with zero effects; restored, recovery reaps once and the scope
        retires once."""
        with open(os.path.join(self.root, self.START), encoding="utf-8") as handle:
            start = handle.read()
        put_back = self.legacy_record(self.START, start[:12])
        self.assertEqual(proc_module.group_is_ours(self.root),
                         (None, proc_module.UNCORROBORATED_START_FRAGMENT))
        self.retained_contradicted(self.pgid, proc_module.UNCORROBORATED_START_FRAGMENT)
        put_back()
        self.settled_once_without_replay()

    def test_R27_1h_a_ledger_row_CONTRADICTING_the_LIVE_occupants_record_is_retained(self):
        """The occupant's GROUP record cut to its first two digits — a
        valid-looking DIFFERENT group — while the scope's owner ledger names the
        occupant's group for this spawn: contradictory records. Retained on
        every route with zero effects, the cut group never signalled and the
        ledger's never acted on; restored, recovery reaps once and the scope
        retires once."""
        if len(str(self.pgid)) < 3:
            self.skipTest("a group id this short has no two-digit cut that differs from it")
        cut = int(str(self.pgid)[:2])
        self.assertIn(self.pgid, proc_module.ledger_groups(self.scope)[0][
            os.path.basename(self.root)])
        put_back = self.legacy_record(self.GROUP, str(cut))
        self.retained_contradicted(cut, proc_module.UNCORROBORATED_LEDGER_CONTRADICTS)
        put_back()
        self.settled_once_without_replay()

    def test_R27_1i_a_legacy_fragment_under_an_UNOBSERVED_ledger_is_retained_until_it_reads(
            self):
        """The combination the availability gate names: the occupant's GROUP
        record cut to two digits — a valid-looking DIFFERENT group — while the
        scope's owner ledger is PRESENT but cannot be observed (a FIFO stands
        in its place). Nothing is read as absent and nothing settles: the
        retirement's verdict and the hold are UNREADABLE, the predicate says
        possibly live, the retirement refuses, recovery REPORTS the ledger
        UNAVAILABLE and recovers nothing — ZERO reaps, signals, removals and
        deletions; every route returns (none waits); the live occupant stands.
        The ledger then READS (the observation available): the contradiction
        is visible and retained as CONTRADICTED. The record restored: recovery
        reaps once, the scope retires once, nothing repeats."""
        if len(str(self.pgid)) < 3:
            self.skipTest("a group id this short has no two-digit cut that differs from it")
        cut = int(str(self.pgid)[:2])
        ledger = proc_module.ledger_path(self.scope)
        put_back = self.legacy_record(self.GROUP, str(cut))
        fifo = self.make_fifo(ledger)
        unreadable = [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]
        gap = (ledger, "%s: the owner ledger cannot be read (%s)" % (self.UNAVAILABLE,
                                                                     self.NOT_REGULAR))
        self.assertEqual(self.bounded(proc_module.retirement_refusal, [fifo], self.scope),
                         proc_module.RETIRE_REFUSED_UNREADABLE)
        self.assertEqual(self.bounded(proc_module.owned_scope_refusals, [fifo], self.CONTROL,
                                      "wf-live", base=self.base), (unreadable, None))
        self.assertTrue(self.bounded(proc_module.scope_has_live_group, [fifo], self.scope))
        (removal, deletion), made = self.effects()
        with self.no_effects(), removal, deletion:
            report = self.bounded(self.recovered, [fifo])
            result = self.bounded(proc_module.retire_workflow_scopes, [fifo], self.CONTROL,
                                  "wf-live", base=self.base)
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertEqual(result, ([], unreadable))
        self.assertEqual((report[1], report.unavailable), ([], [gap]))
        for _identity, reaped, stuck, unstamped, _uncorroborated in report[0]:
            self.assertEqual((reaped, stuck, unstamped), ([], [], []))   # nothing recovered
        self.assertTrue(self.is_fifo(ledger))
        self.intact()
        self.restore()                                    # the ledger READS again
        self.retained_contradicted(cut, proc_module.UNCORROBORATED_LEDGER_CONTRADICTS)
        put_back()
        self.settled_once_without_replay()

    def signals_counted(self):
        """``(sent, context)``: inside ``context`` every signal but signal 0 is
        RECORDED at its call — ``(call, target, signal)`` — and still DELIVERED."""
        import contextlib
        from unittest.mock import patch
        sent, real_killpg, real_kill = [], os.killpg, os.kill

        def killpg(pgid, sig):
            if sig != 0:
                sent.append(("killpg", pgid, sig))
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
            return real_kill(pid, sig)

        @contextlib.contextmanager
        def counted():
            with patch.object(os, "killpg", killpg), patch.object(os, "kill", kill):
                yield
        return sent, counted()

    def test_R27_1j_a_LIVE_CORROBORATED_root_under_an_UNOBSERVED_ledger_is_HELD_then_once(
            self):
        """The occupant's root is VALID, its group LIVE and CORROBORATED as ours
        by its own root (AR-3), while the scope's owner ledger is PRESENT but
        cannot be observed (a FIFO stands in its place). The contradiction
        check is part of the ownership proof, so recovery HOLDS (never
        fail-open):
        - given NO list, recovery RAISES ``ObservationUnavailable([gap])``;
        - as production runs it (``recover_attributed``, with a list), it
          REPORTS the gap and acts on NOTHING in the scope;
        - ZERO signals and ZERO reaps, counted at their calls; the group is
          still alive; the retirement's verdict is UNREADABLE and the
          retirement refuses — ZERO credential removals, ZERO deletions; the
          root, scope and credential all stand; every route returns.
        The ledger then READS: recovery reaps EXACTLY ONCE (one SIGKILL, to
        exactly that group), the scope retires exactly ONCE, and neither
        repeats."""
        import signal as signal_module
        from unittest.mock import patch
        ledger = proc_module.ledger_path(self.scope)
        fifo = self.make_fifo(ledger)
        gap = (ledger, "%s: the owner ledger cannot be read (%s)" % (self.UNAVAILABLE,
                                                                     self.NOT_REGULAR))
        unreadable = [(self.scope, proc_module.RETIRE_REFUSED_UNREADABLE)]
        self.assertEqual(proc_module.group_is_ours(self.root), (self.pgid, None))
        real_reap, reaps = proc_module.reap_group_by_recorded_root, []

        def reap(*args, **kwargs):
            reaps.append(args)
            return real_reap(*args, **kwargs)
        sent, counted = self.signals_counted()
        (removal, deletion), made = self.effects()
        with counted, removal, deletion, \
                patch.object(proc_module, "reap_group_by_recorded_root", reap):
            with self.assertRaises(proc_module.ObservationUnavailable) as raised:
                self.bounded(proc_module.recover_orphans, [fifo], self.scope, settle_seconds=10.0)
            report = self.bounded(self.recovered, [fifo])
            result = self.bounded(proc_module.retire_workflow_scopes, [fifo], self.CONTROL,
                                  "wf-live", base=self.base)
        self.assertEqual(raised.exception.unavailable, [gap])
        self.assertEqual((report[0], report[1], report.unavailable), ([], [], [gap]))
        self.assertEqual((sent, reaps), ([], []))               # ZERO signals, ZERO reaps
        self.assertEqual(result, ([], unreadable))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))   # HELD at zero
        self.assertEqual(self.bounded(proc_module.retirement_refusal, [fifo], self.scope),
                         proc_module.RETIRE_REFUSED_UNREADABLE)
        self.intact()                                     # root, scope, credential; group alive
        self.restore()                                    # the ledger READS again
        sent, counted = self.signals_counted()
        with counted:
            report = self.recovered()
            again = self.recovered()
        self.assertEqual(sent, [("killpg", self.pgid, signal_module.SIGKILL)])   # EXACTLY one
        self.assertEqual((report[1], report.unavailable), ([], []))
        self.assertEqual(len(report[0]), 1, report[0])
        [(identity, reaped, stuck, unstamped, uncorroborated)] = report[0]
        self.assertEqual(tuple(identity), self.OWNER)
        self.assertEqual((reaped, stuck, unstamped, uncorroborated), ([self.pgid], [], [], []))
        self.assertEqual((again[0], again[1], again.unavailable), ([], [], []))
        self.assertFalse(proc_module._group_alive(self.pgid))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(result, ([self.scope], []))
        self.assertEqual((made["removals"], made["deletions"]), ([self.credential], [self.scope]))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-live", base=self.base)
        self.assertEqual(result, ([], []))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))

    def test_R27_1k_reap_owned_on_an_UNOBSERVED_ledger_REFUSES_with_ZERO_signals_then_once(self):
        """``reap_owned`` — the reap ``verification.run`` performs after its
        command's wait, and the role turn's ``finally`` — GATES on the owner
        ledger (``owned_groups``). The occupant's group is LIVE and the scope's
        ledger names it, but a FIFO stands in the ledger's place (PRESENT, NOT
        observed):
        - the reap RETURNS (never waits): ``REFUSED_LEDGER_UNAVAILABLE``,
          naming the gap — never "not in the ledger";
        - ``surviving_owned_groups`` and ``sweep_owned`` RAISE
          ``ObservationUnavailable([gap])`` — never "no survivor";
        - ZERO signals, counted at their calls; the group is still alive.
        The ledger then READS — THROUGH A VALID LINK (the real ledger moved
        beside it, a link in its place): the reap proceeds EXACTLY ONCE (one
        SIGKILL, to exactly that group), the group is gone, and a sweep finds
        nothing left to signal and nothing pending."""
        import signal as signal_module
        ledger = proc_module.ledger_path(self.scope)
        self.assertIn(self.pgid, proc_module.owned_groups(self.scope))   # the ledger names it
        fifo = self.make_fifo(ledger)
        gap = (ledger, "%s: the owner ledger cannot be read (%s)" % (self.UNAVAILABLE,
                                                                     self.NOT_REGULAR))
        sent, counted = self.signals_counted()
        patched = self.guarded_patches(counted)          # undone only once threads ended
        verdict, detail = self.bounded(proc_module.reap_owned, [fifo], self.pgid,
                                       directory=self.scope, settle_seconds=10.0)
        with self.assertRaises(proc_module.ObservationUnavailable) as surviving:
            self.bounded(proc_module.surviving_owned_groups, [fifo], self.scope)
        with self.assertRaises(proc_module.ObservationUnavailable) as swept:
            self.bounded(proc_module.sweep_owned, [fifo], self.scope, settle_seconds=10.0)
        patched.close()                                   # every thread observed ended
        self.assertEqual(verdict, proc_module.REFUSED_LEDGER_UNAVAILABLE)
        self.assertIn(gap[1], detail)
        self.assertEqual((surviving.exception.unavailable, swept.exception.unavailable),
                         ([gap], [gap]))
        self.assertEqual(sent, [])                        # ZERO signals
        self.assertTrue(proc_module._group_alive(self.pgid))    # the group stands
        self.restore()                                    # the ledger READS again ...
        real = ledger + ".r27-1k-real"
        os.rename(ledger, real)                           # ... THROUGH A VALID LINK
        os.symlink(real, ledger)
        sent, counted = self.signals_counted()
        patched = self.guarded_patches(counted)
        reaped = self.bounded(proc_module.reap_owned, [], self.pgid, directory=self.scope,
                              settle_seconds=10.0)
        swept = self.bounded(proc_module.sweep_owned, [], self.scope, settle_seconds=10.0)
        patched.close()
        self.assertEqual(reaped, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", self.pgid, signal_module.SIGKILL)])   # EXACTLY one
        self.assertEqual(swept, ([], [], []))             # nothing to signal, nothing pending
        self.assertFalse(proc_module._group_alive(self.pgid))

    #: The stand-in Codex turn of 1l: it waits until the PARENT's group row for
    #: it is in the ledger (so the parent's own append is never reached by the
    #: substitution), starts a same-group DESCENDANT (its stdio detached, so the
    #: runner's ``communicate`` is not held open), puts a FIFO in the ledger's
    #: place (the real ledger moved to ``argv[2]``), answers, and exits.
    TURN_CODE = (
        "import json, os, subprocess, sys, time\n"
        "ledger, aside = sys.argv[1], sys.argv[2]\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline:\n"
        "    with open(ledger, encoding='utf-8') as handle:\n"
        "        rows = [json.loads(line) for line in handle if line.strip()]\n"
        "    if any(row.get('pgid') == os.getpid() for row in rows):\n"
        "        break\n"
        "    time.sleep(0.02)\n"
        "subprocess.Popen(['sleep', '30'], stdin=subprocess.DEVNULL,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "os.rename(ledger, aside)\n"
        "os.mkfifo(ledger, 0o600)\n"
        "sys.stdout.write('turned')\n")

    def test_R27_1l_the_ROLE_TURN_runner_on_an_UNOBSERVED_ledger_RETURNS_with_ZERO_signals(self):
        """``codex_gateway.role_turn._default_runner`` — the production Codex
        spawn — reaps its group in a ``finally`` after the turn
        (``role_turn.py:947``, ``reap_owned``, whose gate READS the owner
        scope's ledger). The turn is unbounded (no deadline), so the ledger can
        change while it runs: the stand-in turn starts a same-group DESCENDANT,
        puts a FIFO in its owner scope's ledger's place and exits. The runner
        RETURNS — never waits — with the turn's own result; ZERO signals in its
        ``finally`` (nothing signalled from a failed proof); the descendant
        still runs. The ledger then READS: the same ledger-gated reap proceeds
        EXACTLY ONCE — one SIGKILL to exactly that group, while its descendant
        still holds the group id — and the group is gone.
        LIMIT: this proves the behaviour of the bytes THIS process imported, in
        this run — nothing about any other process or interval."""
        import signal as signal_module
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL,
                                         "wf-turn", "t-turn", base=self.base)
        ledger = proc_module.ledger_path(scope)
        aside = os.path.join(self.base, "r27-1l-owner-ledger-aside")
        groups = []

        def settle_turn_group():
            """FIXTURE settlement of this case's OWN recorded group, however the
            case ended: the ledger put back first; then the production
            ledger-gated reaper, ONLY while a member is freshly observed (the
            group id is then still held by this case's descendant)."""
            if os.path.lexists(aside) and self.is_fifo(ledger):
                os.unlink(ledger)
                os.rename(aside, ledger)
            if groups and proc_module._group_alive(groups[0]):
                proc_module.reap_owned(groups[0], directory=scope, settle_seconds=5.0)
        self.addCleanup(settle_turn_group)
        real_spawn = proc_module.spawn_owned

        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            groups.append(process.pid)
            return process
        sent, counted = self.signals_counted()
        patched = self.guarded_patches(counted, patch.object(proc_module, "spawn_owned", spawn))
        rc, out, err, pid = self.bounded(
            role_turn_module._default_runner, [ledger],
            [sys.executable, "-c", self.TURN_CODE, ledger, aside], b"", None,
            owner_scope=scope)
        patched.close()                                   # the runner observed ended
        self.assertEqual((rc, out, groups), (0, b"turned", [pid]), err)
        self.assertTrue(self.is_fifo(ledger))             # substituted DURING the turn
        self.assertEqual(sent, [])                        # ZERO signals in the runner's finally
        self.assertTrue(proc_module._group_alive(pid))    # the descendant still runs
        os.unlink(ledger)
        os.rename(aside, ledger)                          # the ledger READS again
        sent, counted = self.signals_counted()
        patched = self.guarded_patches(counted)
        self.assertTrue(proc_module._group_alive(pid))    # still held by the descendant
        reaped = self.bounded(proc_module.reap_owned, [], pid, directory=scope,
                              settle_seconds=10.0)
        patched.close()
        self.assertEqual(reaped, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pid, signal_module.SIGKILL)])   # EXACTLY one
        self.assertFalse(proc_module._group_alive(pid))


class R26BoundedFixtureTests(unittest.TestCase):
    """Task 8 R26-1: the TIMEOUT fixture itself — ``bounded`` and
    ``guarded_cleanup`` of ``R26NonWaitingRecordTests``. One case of that
    class is run on its own, with a minimal ``setUp`` that registers two
    recording cleanups, an INHERITED ``tearDown`` that records itself (it
    runs before any cleanup), and a route that:
    - RETURNS: the inherited ``tearDown`` runs, then every cleanup, the last
      registered first;
    - WAITS on its own FIFO: the FIFO is released, the thread is OBSERVED
      terminated, the case fails (it waited), and only then do the
      ``tearDown`` and the cleanups run;
    - WAITS on what no FIFO release can end: the case fails, the inherited
      ``tearDown`` and EVERY cleanup are WITHHELD — none runs — and each
      fails the case by name. This test's own event then ends the thread,
      which is observed terminated, so nothing outlives the test.
    And the OWNED CHILDREN (1o2, 1o3): a child never observed ended withholds
    every destructive cleanup while a SAFE step still settles another; a
    collected child is never signalled again; a child whose spawn result is
    dropped is still settled through its booking."""

    CASE = R26NonWaitingRecordTests
    AFTER = ["inherited tearDown", "second registered", "first registered"]

    def case(self, route, ran):
        class Inherited(unittest.TestCase):
            def tearDown(inner):
                ran.append("inherited tearDown")

        class Case(self.CASE, Inherited):
            BOUND = 0.2
            RELEASE_PASSES = 2

            def setUp(inner):
                inner.addCleanup(ran.append, "first registered")
                inner.addCleanup(ran.append, "second registered")

            def test_route(inner):
                route(inner)
        case, result = Case("test_route"), unittest.TestResult()
        case.run(result)
        return case, result

    def test_R26_1m_a_route_that_returns_runs_every_cleanup(self):
        ran = []
        _case, result = self.case(
            lambda inner: inner.assertEqual(inner.bounded(lambda: 7, []), 7), ran)
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertEqual(ran, self.AFTER)

    def test_R26_1n_a_released_route_is_observed_terminated_before_any_cleanup(self):
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        fifo = os.path.join(holding, "fifo")
        os.mkfifo(fifo, 0o600)
        ran, alive = [], []

        def read():
            with open(fifo, "rb") as handle:              # waits: no writer
                return handle.read()

        def route(inner):
            try:
                inner.bounded(read, [fifo])
            finally:
                alive.extend(thread.is_alive() for thread, _fifos in inner.routes)
        _case, result = self.case(route, ran)
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("WAITED", texts[0])
            self.assertIn("was then observed terminated", texts[0])
            self.assertEqual(alive, [False])              # ended BEFORE any teardown
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, self.AFTER)
        finally:
            self.assertEqual(self.ended("bounded-route-read", fifo), [])   # none outlives it

    @staticmethod
    def ended(name, fifo=None):
        """This test's OWN release, independent of the fixture code under
        test: while a thread named ``name`` lives, ``fifo`` (when given) is
        opened for writing and for reading, each without waiting, and closed,
        and the thread is joined again, at most 30 times. Returns the names
        still alive."""
        import threading
        for _ in range(30):
            live = [thread for thread in threading.enumerate() if thread.name == name]
            if not live:
                break
            if fifo is not None:
                for mode in (os.O_WRONLY, os.O_RDONLY):
                    try:
                        os.close(os.open(fifo, mode | os.O_NONBLOCK))
                    except OSError:
                        pass
            for thread in live:
                thread.join(1.0)
        return [thread.name for thread in threading.enumerate() if thread.name == name]

    def test_R26_1n2_a_released_WRITER_route_is_observed_terminated_before_any_cleanup(self):
        """A route WAITING to WRITE its own FIFO — as the pre-correction stamp
        writer did, inside ``open`` — is released by the fixture's read-side
        pulse (its write then finds no reader), OBSERVED terminated, and only
        then do the ``tearDown`` and the cleanups run; the case fails (it
        waited)."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        fifo = os.path.join(holding, "fifo")
        os.mkfifo(fifo, 0o600)
        ran, alive = [], []

        def write():
            with open(fifo, "wb") as handle:             # waits: no reader
                handle.write(b"x")

        def route(inner):
            try:
                inner.bounded(write, [fifo])
            finally:
                alive.extend(thread.is_alive() for thread, _fifos in inner.routes)
        _case, result = self.case(route, ran)
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("WAITED", texts[0])
            self.assertIn("was then observed terminated", texts[0])
            self.assertEqual(alive, [False])              # ended BEFORE any teardown
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, self.AFTER)
        finally:
            self.assertEqual(self.ended("bounded-route-write", fifo), [])   # none outlives it

    def test_R26_1o_a_route_never_observed_terminated_WITHHOLDS_every_cleanup(self):
        import threading
        never = threading.Event()
        ran = []
        case, result = self.case(lambda inner: inner.bounded(never.wait, []), ran)
        try:
            self.assertEqual(ran, [])                     # NOTHING ran beneath it
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 4, texts)        # the wait, tearDown, 2 cleanups
            self.assertIn("STILL ALIVE: every cleanup is withheld", texts[0])
            self.assertIn("tearDown WITHHELD: not observed ended — route thread"
                          " bounded-route-wait", texts[1])
            self.assertIn("cleanup WITHHELD (append('second registered',))", texts[2])
            self.assertIn("cleanup WITHHELD (append('first registered',))", texts[3])
            self.assertEqual(result.errors, [])
            self.assertEqual(case.__dict__.get("withheld"), ["tearDown", "append", "append"])
        finally:
            never.set()                                   # this test's own event
            self.assertEqual(self.ended("bounded-route-wait"), [])   # observed terminated

    def outer_settlement(self, case, killed, children):
        """This test's OWN outer settlement on its FAILING path, where its
        settlement assertions never run (an earlier assertion already failed;
        the ``finally`` that calls this is what reaps). RECORDED, NEVER RAISED —
        a raise here would replace that first failure. For each ``(name,
        process)``: whether this test signalled it, its handle's return code
        (possibly FABRICATED, never read as its exit status), and whether it is
        OBSERVED ENDED — no process holds its pid (``pid_absent``) and its group
        answers signal 0 with the observed-absent answer. Written to stderr as
        UNPROVEN by assertion: an observation, never a passed check."""
        try:
            readings = []
            for name, process in children:
                try:
                    ended = (case.pid_absent(process.pid)
                             and proc_module._group_alive(process.pid) is False)
                    state = "observed ENDED" if ended else "NOT observed ended"
                except Exception as exc:                     # noqa: BLE001 - recorded
                    state = "UNREADABLE (%s)" % exc.__class__.__name__
                readings.append("%s %d: signalled by this test %s, return code %r, %s" % (
                    name, process.pid, "yes" if process.pid in killed else "no",
                    process.returncode, state))
            text = "; ".join(readings)
        except Exception as exc:                             # noqa: BLE001 - recorded
            text = "the observation itself failed (%s)" % exc.__class__.__name__
        sys.stderr.write("R26 fixture: OUTER SETTLEMENT of %s on its FAILING path: its"
                         " settlement assertions did NOT run (UNPROVEN by assertion);"
                         " recorded in its finally, never raised: %s\n"
                         % (self._testMethodName, text))

    def test_R26_1o2_an_owned_child_never_observed_ended_WITHHOLDS_every_cleanup(self):
        """An owned CHILD is the same bar as a route thread for every
        DESTRUCTIVE cleanup. A case BOOKS two children, neither on a route
        thread: an occupant whose release (a SAFE step) is registered first,
        and a writer child that keeps running and whose signal is REFUSED (a
        controlled adapter that records it), so it is never observed ended. The
        writer settlement FAILS naming the child, the signal attempted while
        pinned and what is retained (the writer scope, its ledger, its owned
        roots); the occupant is still settled — signalled while pinned,
        collected through its own handle with its TRUE status, observed gone;
        and every DESTRUCTIVE cleanup is WITHHELD — none runs. Failures, never
        errors. This test's own mechanism then signals its own writer group
        while it is still UNCOLLECTED (pinned), collects it through its OWN
        handle (its true status), and observes it ended and its group gone."""
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        made, ran, refused = {}, [], []
        real_kill_group = self.CASE.kill_group

        def route(inner):
            inner.base = holding
            occupant_scope = os.path.join(holding, "occupant-scope")
            os.makedirs(occupant_scope)
            with inner.booking(inner.__dict__.setdefault("occupants", [])):
                occupant = proc_module.spawn_owned(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    label="r26-occupant", directory=occupant_scope,
                    owned_root_base_dir=occupant_scope,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # this TEST's own record of each child (scope, process, start time),
            # apart from the fixture's booking, for this test's own final reap
            made["occupant"] = (occupant_scope, occupant, inner.start_of(occupant.pid))
            inner.addCleanup(inner.release_occupant, occupant_scope, occupant)   # SAFE; after
            scope = inner.writer_scope()
            writer = inner.writer_spawn(scope, 60.0)                          # no route thread
            made["writer"] = (scope, writer, inner.start_of(writer.pid))

        class Refusing(self.CASE):
            WAIT_SECONDS = 0.5

            @staticmethod
            def kill_group(pgid):
                if "writer" in made and pgid == made["writer"][1].pid:
                    refused.append(pgid)                     # REFUSED: nothing is delivered
                    return None
                return real_kill_group(pgid)

        real_case, self.CASE = self.CASE, Refusing
        try:
            case, result = self.case(route, ran)
        finally:
            self.CASE = real_case
        (scope, writer, _start), (_scope, occupant, _started) = made["writer"], made["occupant"]
        killed, completed = [], []
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 3, texts)       # the settlement, two cleanups
            self.assertIn("owned process(es) NOT observed ended: child %d (running after its"
                          " wait; SIGKILL attempted on its group while pinned)" % writer.pid,
                          texts[0])
            self.assertEqual(refused, [writer.pid])           # its signal: REFUSED here
            self.assertIn("retained: the writer scope %s, its ledger %s, its owned roots"
                          % (scope, proc_module.ledger_path(scope)), texts[0])
            self.assertIn("cleanup WITHHELD (append('second registered',)): not observed"
                          " ended — child %d" % writer.pid, texts[1])
            self.assertIn("cleanup WITHHELD (append('first registered',))", texts[2])
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, ["inherited tearDown"])     # NO destructive cleanup ran
            self.assertEqual(case.__dict__.get("withheld"), ["append", "append"])
            self.assertEqual(occupant.returncode, -signal.SIGKILL)   # the SAFE step ran:
            self.assertTrue(case.pid_absent(occupant.pid))    # its TRUE status, observed ended,
            self.assertFalse(proc_module._group_alive(occupant.pid))   # observed gone
            completed.append(True)                            # every assertion above HELD
        finally:
            for _owned_scope, child, start in (made["writer"], made["occupant"]):
                if case.pinned(child, start)[0] == "pinned":  # still its UNCOLLECTED child
                    real_kill_group(child.pid)
                    child.wait(timeout=10)                    # through its OWN handle
                    killed.append(child.pid)
            if not completed:                                 # RECORDED, never raised
                self.outer_settlement(case, killed, (("writer", writer), ("occupant", occupant)))
        self.assertEqual(killed, [writer.pid])                # the writer, by this test
        self.assertEqual(writer.returncode, -signal.SIGKILL)  # its TRUE status
        self.assertTrue(case.pid_absent(writer.pid))          # observed ended,
        self.assertFalse(proc_module._group_alive(writer.pid))   # observed gone

    def test_R26_1o3_a_COLLECTED_child_is_never_signalled_and_a_DROPPED_carrier_is_still_settled(
            self):
        """FIXTURE BOOKKEEPING decides the cleanup, never a bare id. A case BOOKS
        two children, neither on a route thread: one it COLLECTS itself before
        cleanup, and one whose spawn result it DROPS — as a mutant that
        discards ``SpawnUnconfirmed``'s process carrier would. The settlement
        observes both ended — the dropped one through its booking — and sends
        the collected one NO signal but signal 0: every ``os.kill`` /
        ``os.killpg`` aimed at its pid is RECORDED here and only signal 0 is
        delivered to it. Nothing is withheld."""
        from unittest.mock import patch
        holding = tempfile.mkdtemp()
        self.addCleanup(remove, holding)
        children, watched, signals, ran = [], [], [], []

        def recording(real):
            def send(pid, number):
                if number != 0 and abs(pid) in watched:
                    signals.append((pid, number))            # recorded, never delivered
                    return None
                return real(pid, number)
            return send

        def route(inner):
            inner.base = holding
            scope = inner.writer_scope()
            collected = inner.writer_spawn(scope, 0.0)
            collected.wait(timeout=10)                       # COLLECTED by the case itself
            watched.append(collected.pid)
            with inner.booking(inner.writer_children):       # its result DROPPED
                proc_module.spawn_owned(
                    [sys.executable, "-c", inner.WRITER_CODE, inner.writer_marker, "0.0"],
                    label="r26-writer", directory=scope, owned_root_base_dir=scope,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.extend(process for process, _started, _root in inner.writer_children)

        with patch.object(os, "kill", recording(os.kill)), \
                patch.object(os, "killpg", recording(os.killpg)):
            case, result = self.case(route, ran)
        self.assertEqual(len(children), 2, children)
        self.assertEqual(signals, [])                        # the COLLECTED one: never signalled
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertEqual(ran, self.AFTER)                    # nothing withheld
        dropped = children[1]
        self.assertTrue(case.pid_absent(dropped.pid))        # settled through its BOOKING:
        self.assertFalse(proc_module._group_alive(dropped.pid))   # observed ended and gone


class R27AtomicStampWriterTests(unittest.TestCase):
    """Task 8 R27-1: the shared stamp writer (``spawn_stamp._write_record``)
    REPLACES a record ATOMICALLY — on a private directory, no process started,
    one controlled fault at a time inside the writer (``stamp_faults``).

    Each boundary of the writer, the record holding a VALID previous stamp (the
    CHILD's, in production):
    - BEFORE the publication (the replacement's creation, a short write, a
      failed write, its ``fsync``, the rename, a writer STOPPED there): the
      record is UNTOUCHED — bytes and inode — and the replacement is discarded,
      or, when the writer was stopped, left beside it under
      ``REPLACEMENT_PREFIX``, opened by no reader;
    - AFTER the publication (the confirmation, the directory's ``fsync``):
      ``PublicationUnproven`` — the record IS published and stays so; nothing
      is rolled back.
    And what holds at the REPLACEMENT BOUNDARY, stated as exact effects: an
    object EXAMINED unsafe is refused with nothing written or truncated; an
    entry substituted after the last examination is REPLACED by the rename —
    never written through, its other names kept; a directory there makes the
    rename fail, nothing published. (No claim is made that an examination
    proves the state at the rename, nor that the published name's confirmation
    says anything about what was displaced.)"""

    CHILD = {proc_module.OWNED_ROOT_PGID_FILE: "44603",
             proc_module.OWNED_ROOT_START_FILE: "Fri Oct  2 04:17:24 2026"}
    PARENT = {proc_module.OWNED_ROOT_PGID_FILE: "44603",
              proc_module.OWNED_ROOT_START_FILE: "Fri Oct  2 04:17:24 2026"}
    VICTIM = b"VICTIM BYTES, LONGER THAN ANY GROUP ID"

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(remove, self.directory)
        self.outside = tempfile.mkdtemp()
        self.addCleanup(remove, self.outside)
        for name, text in self.CHILD.items():
            stamp_module._write_record(self.path(name), text)   # the CHILD's valid stamp

    def path(self, name):
        return os.path.join(self.directory, name)

    def clear(self, path):
        """Whatever stands at ``path`` removed — a directory, any other entry,
        or nothing at all — by plain calls, never through the writer."""
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        import stat
        os.rmdir(path) if stat.S_ISDIR(info.st_mode) else os.unlink(path)

    def fresh_child_record(self, name):
        """The CHILD's valid stamp of ``name`` re-established by a plain write,
        never through the writer under test: each subtest starts from it, so
        no subtest depends on what an earlier one left."""
        self.clear(self.path(name))
        with open(self.path(name), "w", encoding="utf-8") as handle:
            handle.write(self.CHILD[name])

    def state(self, name):
        """``(bytes, inode, names)`` of the record as it stands."""
        path = self.path(name)
        info = os.lstat(path)
        with open(path, "rb") as handle:
            return handle.read(), info.st_ino, info.st_nlink

    def write(self, name, faults):
        """The writer replacing ``name`` with the PARENT's text, ``faults``
        armed. Returns what it raised, or None."""
        with faults.active():
            try:
                stamp_module._write_record(self.path(name), self.PARENT[name])
            except BaseException as exc:                     # noqa: BLE001 - judged by each case
                return exc
        return None

    def untouched_before_publication(self, name, faults, cause, number, created=True):
        """A failure BEFORE the publication: ``cause`` (``number``) raised; the
        record UNTOUCHED, bytes and inode; no replacement left — and, when a
        replacement was ``created``, the EXECUTED order pin
        (``discarded_own_replacement_only``)."""
        before = self.state(name)
        raised = self.write(name, faults)
        self.assertIsInstance(raised, cause)
        self.assertEqual(raised.errno, number)
        if created:
            self.discarded_own_replacement_only(faults, name)    # the trace, FIRST
        self.assertEqual(self.state(name), before)
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])
        return raised

    def discarded_own_replacement_only(self, faults, name):
        """EXECUTED ORDER PIN (``DestructiveOrderingClosureTests.ORDERING``,
        ``spawn_stamp._write_record``), BEFORE the publication: the writer's
        every ``unlink``, read from its own call trace, names ITS OWN
        replacement (``REPLACEMENT_PREFIX``, beside the record) and never the
        record path; and no rename PUBLISHED it (the record is untouched,
        asserted by the caller)."""
        record = self.path(name)
        unlinks = [detail for call, detail in faults.calls if call == "unlink"]
        self.assertTrue(unlinks, "the writer discarded nothing")
        for path in unlinks:
            self.assertNotEqual(os.path.abspath(path), os.path.abspath(record))
            self.assertEqual(os.path.dirname(os.path.abspath(path)), self.directory)
            self.assertTrue(os.path.basename(path).startswith(
                stamp_module.REPLACEMENT_PREFIX + name + "-"), path)

    def nothing_undone(self, faults):
        """EXECUTED ORDER PIN, AFTER the publication: from the writer's own call
        trace, nothing after its LAST rename is an ``unlink``, a ``rename``, a
        ``link`` or a ``write`` — the published record is never deleted,
        renamed over or rewritten; only the confirmation and the directory's
        ``fsync`` follow."""
        calls = [call for call, _detail in faults.calls]
        self.assertIn("rename", calls)
        last = len(calls) - 1 - calls[::-1].index("rename")
        after = [call for call in calls[last + 1:] if call != "close"]
        self.assertEqual([call for call in after if call in ("unlink", "rename", "link",
                                                                "write")], [])
        self.assertTrue(set(after) <= {"lstat", "fsync"}, after)

    def victim(self):
        path = os.path.join(self.outside, "victim")
        with open(path, "wb") as handle:
            handle.write(self.VICTIM)
        return path

    def held_fifo(self, path):
        """A FIFO at ``path``, with a read end held open here: anything written
        through it would be readable from that end."""
        os.mkfifo(path, 0o600)
        reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        return reader

    def nothing_in(self, reader):
        try:
            data = os.read(reader, 64)
        except BlockingIOError:
            data = b""
        self.assertEqual(data, b"")

    @staticmethod
    def is_fifo(path):
        import stat
        return stat.S_ISFIFO(os.lstat(path).st_mode)

    # -- BEFORE the publication: the record UNTOUCHED -------------------------------------

    def test_R27_W1_a_SHORT_write_leaves_EACH_record_untouched_never_a_fragment(self):
        """The replacement takes a short write (the first bytes: ``446`` of
        ``44603``, a cut start time) and an I/O failure — for EACH record."""
        for name, keep in ((proc_module.OWNED_ROOT_PGID_FILE, 3),
                           (proc_module.OWNED_ROOT_START_FILE, 12)):
            with self.subTest(record=name):
                self.untouched_before_publication(
                    name, stamp_faults.StampFaults().short_write(name, keep), OSError, errno.EIO)

    def test_R27_W2_a_failed_write_fsync_or_rename_leaves_the_record_untouched(self):
        name = proc_module.OWNED_ROOT_PGID_FILE
        for label, faults, number in (
                ("write", stamp_faults.StampFaults().failed_write(name), errno.ENOSPC),
                ("fsync", stamp_faults.StampFaults().failed_record_fsync(name), errno.EIO),
                ("rename", stamp_faults.StampFaults().failed_rename(errno.EXDEV), errno.EXDEV)):
            with self.subTest(failed=label):
                self.fresh_child_record(name)
                self.untouched_before_publication(name, faults, OSError, number)

    def test_R27_W3_a_replacement_that_cannot_be_created_leaves_the_record_untouched(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        os.chmod(self.directory, 0o500)
        self.addCleanup(os.chmod, self.directory, 0o700)
        self.untouched_before_publication(proc_module.OWNED_ROOT_PGID_FILE,
                                          stamp_faults.StampFaults(), PermissionError,
                                          errno.EACCES, created=False)

    def test_R27_W4_a_writer_STOPPED_at_its_rename_leaves_the_record_and_an_unread_leftover(self):
        """Stopped at the rename, with no chance to discard: the record is
        UNTOUCHED and its replacement remains beside it — never published,
        never opened by a reader; a later replacement publishes normally and
        leaves only that earlier leftover."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        before = self.state(name)
        faults = stamp_faults.StampFaults().killed_at_rename()
        raised = self.write(name, faults)
        self.assertIsInstance(raised, stamp_faults.Killed)
        self.assertEqual(self.state(name), before)
        self.discarded_own_replacement_only(faults, name)          # attempted; it failed
        [left] = stamp_faults.replacements_in(self.directory)
        self.assertEqual(proc_module.owned_root_record(self.directory)["pgid"], 44603)
        self.assertIsNone(self.write(name, stamp_faults.StampFaults()))
        self.assertEqual(stamp_faults.replacements_in(self.directory), [left])
        self.assertEqual(self.state(name)[0], b"44603")

    # -- the PUBLICATION, and AFTER it: never undone ---------------------------------------

    def test_R27_W5_a_replacement_is_published_atomically_in_order_and_durably(self):
        """The record is replaced EXACTLY (a longer previous content leaves no
        trailing byte), as a NEW single-named object; in order: the replacement
        written and ``fsync``-ed, the name examined, the rename, the
        confirmation, the directory's ``fsync``. No replacement remains."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        with open(self.path(name), "wb") as handle:
            handle.write(b"9" * 300)
        before = self.state(name)
        faults = stamp_faults.StampFaults()
        self.assertIsNone(self.write(name, faults))
        data, inode, names = self.state(name)
        self.assertEqual((data, names), (b"44603", 1))
        self.assertNotEqual(inode, before[1])
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])
        order = [call for call, _detail in faults.calls if call != "close"]
        self.assertEqual(order, ["write", "fsync", "lstat", "rename", "lstat", "fsync"])

    def test_R27_W6_a_reader_racing_the_publication_reads_one_COMPLETE_record(self):
        """A record read by name just before the rename and just after: the
        previous record, then the new one — each complete."""
        name = proc_module.OWNED_ROOT_START_FILE
        with open(self.path(name), "wb") as handle:
            handle.write(b"Thu Oct  1 23:59:59 2026, the previous start")
        read = []
        faults = stamp_faults.StampFaults().before_rename(
            lambda source, target: read.append(proc_module.read_ownership_record(target)))
        self.assertIsNone(self.write(name, faults))
        read.append(proc_module.read_ownership_record(self.path(name)))
        self.assertEqual(read, [b"Thu Oct  1 23:59:59 2026, the previous start",
                                self.PARENT[name].encode("utf-8")])

    def test_R27_W7_a_failed_DIRECTORY_fsync_is_PUBLISHED_UNPROVEN_never_rolled_back(self):
        name = proc_module.OWNED_ROOT_PGID_FILE
        before = self.state(name)
        faults = stamp_faults.StampFaults().failed_directory_fsync()
        raised = self.write(name, faults)
        self.assertIsInstance(raised, stamp_module.PublicationUnproven)
        self.assertEqual(raised.errno, errno.EIO)
        self.nothing_undone(faults)                               # the trace, FIRST
        data, inode, names = self.state(name)
        self.assertEqual((data, names), (b"44603", 1))
        self.assertNotEqual(inode, before[1])                     # PUBLISHED, and it stays
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])

    def test_R27_W8_an_unconfirmed_publication_is_UNPROVEN_and_nothing_is_undone(self):
        """After the rename the confirmation does not see the record naming the
        very single-named object written: ``PublicationUnproven``, and NOTHING
        is deleted, renamed or rewritten to undo it.
        - another NAME for the published object: ``EMLINK``; both names stay;
        - the record NAME replaced by another object: ``ESTALE``; that object,
          and the published one where it was moved, both stay."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        record, extra = self.path(name), os.path.join(self.outside, "extra-name")
        faults = stamp_faults.StampFaults().after_publication(lambda path: os.link(path, extra))
        raised = self.write(name, faults)
        self.assertIsInstance(raised, stamp_module.PublicationUnproven)
        self.assertEqual(raised.errno, errno.EMLINK)
        self.nothing_undone(faults)
        self.assertEqual(self.state(name)[0], b"44603")
        self.assertTrue(os.path.samefile(record, extra))
        os.unlink(extra)                       # this case's own extra name, before the next write
        moved = os.path.join(self.outside, "moved")

        def swap(path):
            os.rename(path, moved)
            with open(path, "wb") as handle:
                handle.write(b"another object")
        faults = stamp_faults.StampFaults().after_publication(swap)
        raised = self.write(name, faults)
        self.assertIsInstance(raised, stamp_module.PublicationUnproven)
        self.assertEqual(raised.errno, errno.ESTALE)
        self.nothing_undone(faults)
        with open(record, "rb") as handle:
            self.assertEqual(handle.read(), b"another object")
        with open(moved, "rb") as handle:
            self.assertEqual(handle.read(), b"44603")
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])

    # -- the REPLACEMENT BOUNDARY: exact effects (the Lead's R27 §7) -----------------------

    def test_R27_W9_an_EXAMINED_unsafe_object_is_refused_nothing_written_or_truncated(self):
        """At the FIRST examination — before anything is created — R26's
        refusals, errno for errno: a FIFO with no reader, a FIFO with a reader,
        a symbolic link, a hard link, a directory. At the LAST examination —
        after the replacement was written, before the rename — a FIFO, a
        symbolic link and a hard link substituted there are refused too. Either
        way: nothing written or truncated through it, it stays exactly as it
        was, nothing is published, no replacement remains."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        record = self.path(name)

        def fifo_no_reader():
            os.mkfifo(record, 0o600)
            return lambda: self.assertTrue(self.is_fifo(record))

        def fifo_with_reader():
            reader = self.held_fifo(record)
            writer = os.open(record, os.O_WRONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, writer)
            return lambda: (self.nothing_in(reader), self.assertTrue(self.is_fifo(record)))

        def link():
            target = self.victim()
            os.symlink(target, record)
            return lambda: (self.assertEqual(os.readlink(record), target),
                            self.assertEqual(Path(target).read_bytes(), self.VICTIM))

        def hard_link():
            target = self.victim()
            os.link(target, record)
            return lambda: (self.assertEqual(Path(target).read_bytes(), self.VICTIM),
                            self.assertEqual(os.stat(target).st_nlink, 2))

        def directory():
            os.mkdir(record)
            return lambda: self.assertEqual(os.listdir(record), [])
        first = (("FIFO, no reader", fifo_no_reader, OSError, errno.ENXIO),
                 ("FIFO with a reader", fifo_with_reader, stamp_module.UnsupportedRecord,
                  errno.EINVAL),
                 ("symbolic link", link, OSError, errno.ELOOP),
                 ("hard link", hard_link, stamp_module.UnsupportedRecord, errno.EMLINK),
                 ("directory", directory, OSError, errno.EISDIR))
        for label, plant, cause, number in first:
            with self.subTest(first_examination=label):
                self.clear(record)
                kept = plant()
                faults = stamp_faults.StampFaults()
                raised = self.write(name, faults)
                self.assertIsInstance(raised, cause)
                self.assertEqual(raised.errno, number)
                self.assertNotIn("write", [call for call, _detail in faults.calls])
                self.assertFalse(any(os.path.basename(path).startswith(
                    stamp_module.REPLACEMENT_PREFIX) for path in faults.paths.values()))
                kept()
                self.assertEqual(stamp_faults.replacements_in(self.directory), [])
        last = (("FIFO, no reader", fifo_no_reader, errno.EINVAL),
                ("symbolic link", link, errno.ELOOP),
                ("hard link", hard_link, errno.EMLINK))
        for label, plant, number in last:
            with self.subTest(last_examination=label):
                self.fresh_child_record(name)
                checks = []

                def substitute(real, descriptor, plant=plant):
                    result = real(descriptor)
                    if not checks:
                        os.unlink(record)
                        checks.append(plant())
                    return result
                faults = stamp_faults.StampFaults().arm("fsync", substitute)
                raised = self.write(name, faults)
                self.assertIsInstance(raised, stamp_module.UnsupportedRecord)
                self.assertEqual(raised.errno, number)
                self.assertNotIn("rename", [call for call, _detail in faults.calls])
                self.discarded_own_replacement_only(faults, name)   # the trace, FIRST
                checks[0]()
                self.assertEqual(stamp_faults.replacements_in(self.directory), [])

    def test_R27_W10_a_FIFO_or_extra_name_substituted_at_the_rename_is_REPLACED_never_written(
            self):
        """An entry substituted AFTER the last examination and BEFORE the
        rename is REPLACED by it — the stated effect, which rename(2) takes no
        predicate to prevent: the record name then refers to the published,
        single-named record. Nothing was written through what was displaced,
        and every OTHER name it had stays:
        - a FIFO (a second name kept, a read end held): nothing readable from
          it, the second name still a FIFO;
        - an extra name of a file outside the root: that file's bytes unchanged,
          its other name present."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        record, other = self.path(name), os.path.join(self.outside, "fifo-other-name")
        held = []

        def fifo(source, target):
            os.unlink(target)
            held.append(self.held_fifo(target))
            os.link(target, other)
        self.assertIsNone(self.write(name, stamp_faults.StampFaults().before_rename(fifo)))
        self.assertEqual(len(held), 1, "nothing was substituted at the rename")
        self.assertEqual(self.state(name)[0::2], (b"44603", 1))
        self.nothing_in(held[0])
        self.assertTrue(self.is_fifo(other))
        self.assertEqual(os.lstat(other).st_nlink, 1)
        victim, linked = self.victim(), []

        def extra_name(source, target):
            os.unlink(target)
            os.link(victim, target)
            linked.append(os.stat(victim).st_nlink)
        self.assertIsNone(self.write(name, stamp_faults.StampFaults().before_rename(extra_name)))
        self.assertEqual(linked, [2], "no extra name was substituted at the rename")
        self.assertEqual(self.state(name)[0::2], (b"44603", 1))
        self.assertEqual(Path(victim).read_bytes(), self.VICTIM)
        self.assertEqual(os.stat(victim).st_nlink, 1)
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])

    def test_R27_W12_the_replacement_is_created_EXCLUSIVELY_never_an_existing_object(self):
        """With the replacement's random name made predictable (``os.urandom``
        fixed), a file planted at that name BEFORE the writer runs is never
        opened, written or renamed into place: the exclusive create refuses
        (``EEXIST``), the record is UNTOUCHED, and the planted file keeps its
        bytes and its place."""
        from unittest.mock import patch
        name = proc_module.OWNED_ROOT_PGID_FILE
        planted = os.path.join(self.directory, "%s%s-%s" % (stamp_module.REPLACEMENT_PREFIX,
                                                             name, "00" * 8))
        with open(planted, "wb") as handle:
            handle.write(self.VICTIM)
        before = self.state(name)
        with patch.object(os, "urandom", lambda count: b"\x00" * count):
            raised = self.write(name, stamp_faults.StampFaults())
        self.assertIsInstance(raised, FileExistsError)
        self.assertEqual(raised.errno, errno.EEXIST)
        self.assertEqual(self.state(name), before)
        self.assertEqual(Path(planted).read_bytes(), self.VICTIM)
        self.assertEqual(stamp_faults.replacements_in(self.directory), [os.path.basename(planted)])

    def test_R27_W11_a_DIRECTORY_at_the_name_makes_the_rename_fail_nothing_published(self):
        """A directory substituted after the last examination: the rename fails
        and NOTHING is published — the directory stays, empty; the replacement
        is discarded."""
        name = proc_module.OWNED_ROOT_PGID_FILE
        record = self.path(name)

        def directory(source, target):
            os.unlink(target)
            os.mkdir(target)
        faults = stamp_faults.StampFaults().before_rename(directory)
        raised = self.write(name, faults)
        self.assertIsInstance(raised, OSError)
        self.assertIn(raised.errno, (errno.EISDIR, errno.ENOTEMPTY, errno.EEXIST))
        self.discarded_own_replacement_only(faults, name)       # the trace, FIRST
        self.assertTrue(os.path.isdir(record) and not os.path.islink(record))
        self.assertEqual(os.listdir(record), [])
        self.assertEqual(stamp_faults.replacements_in(self.directory), [])


class R27LegacyRecordTests(unittest.TestCase):
    """Task 8 R27-1, the legacy-record checks — no process started; owned roots
    and owner-ledger rows written by the PRODUCTION writers (``create_owned_root``,
    ``record_pending``, ``record_owned_group``) or by plain writes where a legacy
    record is the point; the readers are the production ones.

    The scope's owner ledger (``ledger_groups``) is read in THREE branches,
    never collapsed (the Supervisor's R27 ledger-availability gate):
    - READABLE and CONTRADICTORY — a row naming a DIFFERENT group for a root's
      spawn nonce (live or not): retirement refuses
      (``RETIRE_REFUSED_CONTRADICTED``) with ZERO removals and deletions, the
      hold keeps it, the predicate counts it, recovery REPORTS it and signals
      nothing, verification is never CLEAR (L1, L2);
    - PRESENT but NOT OBSERVED — a FIFO (never waited on), one too large to
      read, a link that does not resolve: NEVER absence. Each consumer gives
      ITS OWN unavailable result (retirement UNREADABLE, the predicate possibly
      live, verification ``PRIOR_UNAVAILABLE``, recovery reporting the gap and
      signalling nothing), retained until the ledger reads — then settled
      exactly once (L4);
    - EVIDENCE OF NOTHING, each decided exactly as without a ledger (the
      parent-death recovery property): a root with only its PENDING row (the
      child stamped itself; the parent never appended), a ledger GENUINELY
      absent, and a readable row that AGREES (L3).
    A cut START of a LIVE leader is a fragment; a complete different one is
    still reuse (L5)."""

    CONTROL = "/control/repo-r27"
    effects = R25HeldRetirementTests.effects
    DEAD = 999999                 # beyond any pid this host allots: a group observed gone
    BOUND = 10.0
    # Task 8 R27 (after dev21; the Lead's fixture check): the ESTABLISHED timeout
    # fixture of ``R26NonWaitingRecordTests``, borrowed — ONE call per thread
    # (``bounded``), REGISTERED before it starts; a call that waits FAILS the case;
    # its own FIFOs are released at most ``RELEASE_PASSES`` times, termination
    # OBSERVED (``is_alive``), never assumed; and every cleanup — a base's
    # removal, a patch's undoing (``guarded_patches``) — is WITHHELD, failing the
    # case by name, while any such thread lives. No child is started here, so no
    # step is SAFE.
    bounded = R26NonWaitingRecordTests.bounded
    release = R26NonWaitingRecordTests.release
    routes_settled = R26NonWaitingRecordTests.routes_settled
    withhold = R26NonWaitingRecordTests.withhold
    safe_step = R26NonWaitingRecordTests.safe_step
    guarded_cleanup = R26NonWaitingRecordTests.guarded_cleanup
    guarded_patches = R26NonWaitingRecordTests.guarded_patches
    RELEASE_PASSES = R26NonWaitingRecordTests.RELEASE_PASSES
    SAFE_STEPS = ()

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def tearDown(self):
        """Run only once every ``bounded`` thread is OBSERVED terminated;
        otherwise WITHHELD, and the case fails."""
        if not self.routes_settled():
            self.withhold("tearDown", None)
        super(R27LegacyRecordTests, self).tearDown()

    def addCleanup(self, function, *args, **kwargs):
        """EVERY cleanup of this case runs through ``guarded_cleanup``."""
        super(R27LegacyRecordTests, self).addCleanup(self.guarded_cleanup, function, args,
                                                     kwargs)

    def fresh_base(self):
        """A private base of this subtest's OWN: whatever a failed subtest
        leaves planted is never reached by the next one (dev21)."""
        self.base = tempfile.mkdtemp()
        self.addCleanup(remove, self.base)

    def scope(self, unit="t-1"):
        return proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, self.CONTROL, "wf-1",
                                        unit, base=self.base)

    def stamped_root(self, scope, nonce, pgid, pending=True, row=None):
        """An owned root stamped with ``pgid`` (and a complete start); the
        ledger gets the spawn's PENDING row when ``pending``, and a group row
        naming ``row`` under this nonce when given."""
        root = proc_module.create_owned_root(nonce, scope)
        with open(os.path.join(root, proc_module.OWNED_ROOT_START_FILE), "w") as handle:
            handle.write("Thu Jan  1 00:00:00 1970")
        with open(os.path.join(root, proc_module.OWNED_ROOT_PGID_FILE), "w") as handle:
            handle.write(str(pgid))
        if pending:
            proc_module.record_pending(nonce, "r27-legacy", directory=scope)
        if row is not None:
            proc_module.record_owned_group(row, "r27-legacy", directory=scope, nonce=nonce)
        return root

    def credential(self, scope):
        return proc_module.assignment_path(os.path.basename(scope), self.base)

    def retired_once(self, scope):
        """Absence ESTABLISHED: retired exactly once — one credential removal,
        one deletion — and nothing repeats."""
        self.assertIsNone(proc_module.retirement_refusal(scope))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-1", base=self.base)
        self.assertEqual(result, ([scope], []))
        self.assertEqual((made["removals"], made["deletions"]), ([self.credential(scope)],
                                                                   [scope]))
        self.assertEqual(proc_module.retire_workflow_scopes(self.CONTROL, "wf-1",
                                                            base=self.base), ([], []))

    def no_signal(self):
        """Every signal but signal 0 is COUNTED and must stay zero."""
        from unittest.mock import patch
        sent, real_killpg, real_kill = [], os.killpg, os.kill

        def killpg(pgid, sig):
            if sig != 0:
                sent.append(("killpg", pgid, sig))
                return None                              # counted, never delivered
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
                return None                              # counted, never delivered
            return real_kill(pid, sig)
        return sent, patch.object(proc_module.os, "killpg", killpg), \
            patch.object(proc_module.os, "kill", kill)

    def test_R27_L1_a_CONTRADICTORY_ledger_row_RETAINS_on_every_route(self):
        """The ledger names group 4242 for the root's spawn; the root records
        a group observed GONE (a valid-looking fragment's shape). Retirement
        refuses CONTRADICTED with zero effects; the hold keeps it; the
        predicate counts it; recovery reports it and signals nothing."""
        scope = self.scope()
        root = self.stamped_root(scope, "own-r27-contradicted", self.DEAD, row=4242)
        contradicted = [(scope, proc_module.RETIRE_REFUSED_CONTRADICTED)]
        self.assertEqual(proc_module.retirement_refusal(scope),
                         proc_module.RETIRE_REFUSED_CONTRADICTED)
        self.assertEqual(proc_module.owned_scope_refusals(self.CONTROL, "wf-1", base=self.base),
                         (contradicted, None))
        self.assertTrue(proc_module.scope_has_live_group(scope))
        (removal, deletion), made = self.effects()
        with removal, deletion:
            result = proc_module.retire_workflow_scopes(self.CONTROL, "wf-1", base=self.base)
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertEqual(result, ([], contradicted))
        sent, killpg, kill = self.no_signal()
        with killpg, kill:
            report = proc_module.recover_orphans(scope, settle_seconds=1.0, unavailable=[])
        self.assertEqual(sent, [])                               # NEITHER group signalled
        self.assertEqual(report, ([], [], [], [(root, self.DEAD,
                                                proc_module.UNCORROBORATED_LEDGER_CONTRADICTS)]))
        for path in (scope, self.credential(scope), root):
            self.assertTrue(os.path.exists(path), path)

    def test_R27_L2_verification_is_never_CLEAR_on_a_contradictory_row(self):
        """The verification scope's root records a gone group, the ledger names
        another: ``PRIOR_UNAVAILABLE`` — never clear. Without the row: CLEAR."""
        from target_runtime import verification as verification_module
        scope = self.scope(verification_module.VERIFICATION_OWNER_UNIT)
        self.stamped_root(scope, "own-r27-verify", self.DEAD, row=4242)
        self.assertEqual(verification_module.prior_ownership("wf-1", self.CONTROL,
                                                             scope_base=self.base), (
            verification_module.PRIOR_UNAVAILABLE,
            "owned root own-r27-verify's group record is contradicted by its owner ledger"))
        os.unlink(proc_module.ledger_path(scope))
        self.assertEqual(verification_module.prior_ownership("wf-1", self.CONTROL,
                                                             scope_base=self.base), (
            verification_module.PRIOR_CLEAR, "1 owned root(s), every recorded group gone"))

    def test_R27_L3_a_MISSING_row_or_ledger_is_evidence_of_NOTHING(self):
        """Decided exactly as without a ledger — absence established, retired
        once: a root with only its PENDING row (the child stamped itself, the
        parent never appended its group row), no ledger at all, and a row that
        AGREES (also among other groups named for the same nonce)."""
        for label, kwargs in (("pending row only", {}),
                              ("no ledger", {"pending": False}),
                              ("an agreeing row", {"row": self.DEAD})):
            with self.subTest(ledger=label):
                scope = self.scope()
                self.stamped_root(scope, "own-r27-%s" % label.replace(" ", "-"), self.DEAD,
                                  **kwargs)
                if label == "no ledger":
                    self.assertFalse(os.path.lexists(proc_module.ledger_path(scope)))
                self.retired_once(scope)
        scope = self.scope()
        self.stamped_root(scope, "own-r27-both", self.DEAD, row=4242)
        proc_module.record_owned_group(self.DEAD, "r27-legacy", directory=scope,
                                       nonce="own-r27-both")
        self.retired_once(scope)

    def bounded_reads(self, scope, fifos):
        """The readers of ``scope`` — EACH on its OWN thread (``bounded``: a
        reader that waits FAILS the case; ``fifos``, this test's own, are
        released at most ``RELEASE_PASSES`` times and termination is OBSERVED;
        while a reader thread lives every cleanup is WITHHELD). Returns
        ``{"groups", "refusal", "live", "prior"}``: the ledger's branch, the
        retirement's verdict, the predicate, and verification's verdict."""
        from target_runtime import verification as verification_module
        return {
            "groups": self.bounded(proc_module.ledger_groups, fifos, scope),
            "refusal": self.bounded(proc_module.retirement_refusal, fifos, scope),
            "live": self.bounded(proc_module.scope_has_live_group, fifos, scope),
            "prior": self.bounded(verification_module.prior_ownership, fifos, "wf-1",
                                  self.CONTROL, scope_base=self.base)}

    @staticmethod
    def put_back(ledger, readable, always=False):
        """A FIXTURE step, never a detection: the owner ledger made a READABLE
        regular file again when a planted shape still stands there — a FIFO or
        a link is unlinked and a directory removed (never opened), and
        ``readable`` rewritten; with ``always``, a regular file is rewritten
        too (a planted CONTENT). A ledger already regular (restored by the
        case, or never replaced) or already gone (its scope retired) is
        otherwise left exactly as it is."""
        import stat
        try:
            info = os.lstat(ledger)
        except FileNotFoundError:
            return
        if stat.S_ISREG(info.st_mode) and not always:
            return
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(ledger)
        else:
            os.unlink(ledger)
        with open(ledger, "wb") as handle:
            handle.write(readable)

    def test_R27_L4_a_PRESENT_ledger_NOT_observed_RETAINS_until_it_reads(self):
        """A ledger that EXISTS and cannot be observed is never read as absent
        (R21-2): a FIFO with no writer (never waited on), one larger than its
        bound (never read whole), and a link whose target is missing (never
        GENUINELY missing). With a stamped root whose recorded group is GONE —
        the settlement branch — every consumer gives ITS OWN unavailable
        result:
        - the retirement's verdict ``RETIRE_REFUSED_UNREADABLE``, and the
          retirement itself refuses with ZERO removals and deletions;
        - the predicate: possibly live;
        - verification ``PRIOR_UNAVAILABLE``, never CLEAR;
        - recovery REPORTS the ledger unavailable (and raises
          ``ObservationUnavailable`` when given no list) and signals nothing —
          an oracle that, HERE, covers only a recorded group that is GONE (the
          settlement branch); the hold of a LIVE corroborated root under an
          unobserved ledger is ``test_R27_1j``'s.
        Once the ledger reads — the observation available — the same scope
        retires exactly ONCE, and nothing repeats."""
        import contextlib
        from unittest.mock import patch
        from target_runtime import verification as verification_module
        unavailable_class = "%s: the owner ledger cannot be read (%%s)" % (
            proc_module.OBSERVATION_UNAVAILABLE)

        # each: (context, the reader's cause or None, a FIFO to release, whether
        # the retirement's FIRST evidence read refuses it before any reader opens it)
        def fifo(ledger):
            os.unlink(ledger)
            os.mkfifo(ledger, 0o600)
            return contextlib.nullcontext(), "NotARegularRecord", ledger, True

        def oversized(ledger):
            return (patch.object(proc_module, "OWNER_LEDGER_BYTES", 16), "OversizedRecord", None,
                    False)

        def dangling(ledger):
            os.unlink(ledger)
            os.symlink(os.path.join(self.base, "no-such-ledger"), ledger)
            return contextlib.nullcontext(), None, None, False
        for label, make in (("FIFO", fifo), ("oversized", oversized), ("dangling link", dangling)):
            with self.subTest(ledger=label):
                # Task 8 R27 (dev21): each shape in its OWN base. The scope is
                # deterministic by unit, and ``stamped_root`` appends to its ledger
                # through ``record_pending``'s plain (blocking) open — so a shape a
                # failed subtest left planted is never handed to the next one, and
                # no destructive step runs in the case on the failure path.
                self.fresh_base()
                scope = self.scope(verification_module.VERIFICATION_OWNER_UNIT)
                nonce = "own-r27-%s" % label.replace(" ", "-")
                root = self.stamped_root(scope, nonce, self.DEAD, row=self.DEAD)
                ledger = proc_module.ledger_path(scope)
                with open(ledger, "rb") as handle:
                    readable = handle.read()
                context, cause, fifo_path, gated = make(ledger)
                fifos = [fifo_path] if fifo_path else []
                shaped = self.guarded_patches(context)
                box = self.bounded_reads(scope, fifos)
                groups, gap = box["groups"]
                self.assertIsNone(groups)
                self.assertEqual(gap[0], ledger)
                if cause is not None:
                    self.assertEqual(gap[1], unavailable_class % cause)
                else:                                    # the traversal's own problem text
                    self.assertTrue(gap[1].startswith("%s: the owner ledger cannot be read ("
                                                      % proc_module.OBSERVATION_UNAVAILABLE),
                                    gap)
                self.assertEqual(box["refusal"], proc_module.RETIRE_REFUSED_UNREADABLE)
                self.assertTrue(box["live"])
                self.assertEqual(box["prior"], (
                    verification_module.PRIOR_UNAVAILABLE,
                    "owned root %s cannot be checked against its owner ledger (%s)"
                    % (nonce, gap[1])))
                (removal, deletion), made = self.effects()
                sent, killpg, kill = self.no_signal()
                opened, real_open = [], os.open

                def counting_open(path, *args, **kwargs):
                    if path == ledger:
                        opened.append(path)
                    return real_open(path, *args, **kwargs)
                counted = self.guarded_patches(removal, deletion, killpg, kill)
                opening = self.guarded_patches(patch.object(os, "open", counting_open))
                result = self.bounded(proc_module.retire_workflow_scopes, fifos, self.CONTROL,
                                      "wf-1", base=self.base)
                opening.close()                          # its thread observed ended
                # a non-regular ledger is refused by the FIRST evidence read,
                # before any reader opens it (R26-1's gate); one the gate
                # binds is opened once, by the retirement's own reader
                self.assertEqual(opened, [] if gated else [ledger])
                unavailable = []
                report = self.bounded(proc_module.recover_orphans, fifos, scope,
                                      settle_seconds=1.0, unavailable=unavailable)
                with self.assertRaises(proc_module.ObservationUnavailable) as raised:
                    self.bounded(proc_module.recover_orphans, fifos, scope, settle_seconds=1.0)
                counted.close()                          # every reader thread observed ended
                self.assertEqual((made["removals"], made["deletions"], sent), ([], [], []))
                self.assertEqual(result, ([], [(scope, proc_module.RETIRE_REFUSED_UNREADABLE)]))
                self.assertEqual(report, ([], [], [], []))
                self.assertEqual(unavailable, [gap])
                self.assertEqual(raised.exception.unavailable, [gap])
                for path in (scope, self.credential(scope), root):
                    self.assertTrue(os.path.exists(path), path)
                shaped.close()
                if label != "oversized":                 # the observation made AVAILABLE
                    os.unlink(ledger)
                    with open(ledger, "wb") as handle:
                        handle.write(readable)
                self.assertEqual(proc_module.ledger_groups(scope), ({nonce: {self.DEAD}}, None))
                self.retired_once(scope)

    def test_R27_L5_a_CUT_start_of_a_LIVE_leader_is_a_fragment_a_complete_one_is_reuse(self):
        """On a LIVE group (this process's own: only asked about, never
        signalled), a recorded start that is a strict PREFIX of the live
        leader's is ``UNCORROBORATED_START_FRAGMENT``; a complete, different
        one is still ``UNCORROBORATED_START_MISMATCH`` (reuse)."""
        group = os.getpgrp()
        live = proc_module.leader_start_time(group)
        if live is None:
            self.skipTest("this process group's leader start time cannot be read")
        scope = self.scope()
        root = self.stamped_root(scope, "own-r27-cut", group, pending=False)
        sent, killpg, kill = self.no_signal()
        with killpg, kill:
            for recorded, reason in ((live[:12], proc_module.UNCORROBORATED_START_FRAGMENT),
                                     ("Thu Jan  1 00:00:00 1970",
                                      proc_module.UNCORROBORATED_START_MISMATCH)):
                with self.subTest(recorded=recorded):
                    with open(os.path.join(root, proc_module.OWNED_ROOT_START_FILE),
                              "w") as handle:
                        handle.write(recorded)
                    self.assertEqual(proc_module.group_is_ours(root), (None, reason))
        self.assertEqual(sent, [])

    def bounded_ledger_reads(self, scope, fifos):
        """The owner ledger's GROUP and PENDING readers, ``reap_owned``'s gate
        (on the GONE group ``DEAD``) and the sweep — EACH on its OWN thread
        (``bounded``: one that waits FAILS the case; ``fifos``, this test's own,
        are released at most ``RELEASE_PASSES`` times and termination is
        OBSERVED; while a thread lives every cleanup is WITHHELD), so a wait is
        attributed to its call. Returns ``{name: ("ok", value) | ("raised",
        exception)}`` — what each call RETURNED or RAISED itself; the wait's own
        failure is never caught here."""
        box = {}
        for name, call, args, kwargs in (
                ("groups", proc_module.owned_groups, (scope,), {}),
                ("pending", proc_module.pending_nonces, (scope,), {}),
                ("surviving", proc_module.surviving_owned_groups, (scope,), {}),
                ("reap", proc_module.reap_owned, (self.DEAD,),
                 {"directory": scope, "settle_seconds": 1.0}),
                ("sweep", proc_module.sweep_owned, (scope,), {"settle_seconds": 1.0})):
            try:
                box[name] = ("ok", self.bounded(call, fifos, *args, **kwargs))
            except AssertionError:
                raise                                    # a WAIT: the case fails here
            except Exception as exc:                     # noqa: BLE001 - judged by the case
                box[name] = ("raised", exc)
        return box

    def test_R27_L6_the_ledger_READERS_and_the_REAP_GATE_never_wait_or_read_UNOBSERVED_as_empty(
            self):
        """Task 8 R27, the owner-ledger READERS: ``owned_groups`` — the gate
        of ``reap_owned``, which ``verification.run`` reaches after its
        command's wait — and ``pending_nonces`` read through the R26-1 reader.
        For a ledger PRESENT but NOT OBSERVED — a FIFO with no writer (never
        waited on), one larger than its bound, a link whose target is missing,
        a directory, bytes that are not UTF-8 — every call RETURNS within the
        bound and none reads it as naming nothing:
        - ``owned_groups``, ``pending_nonces``, ``surviving_owned_groups`` and
          ``sweep_owned`` RAISE ``ObservationUnavailable([gap])`` (one gap, the
          same for all) — never an empty set, an empty pending result or "no
          survivor";
        - ``reap_owned`` REFUSES (``REFUSED_LEDGER_UNAVAILABLE``, naming the gap
          — never "not in the ledger");
        - ZERO signals, counted at their calls.
        THEN the ledger READS (restored): its group is named, its pending
        nonce reported, and the gate PROCEEDS — to the CURRENT-group proof
        (Task 8 R28-1): no process of this one holds the group's leader and no
        group by that number is alive, so it is ALREADY_GONE with ZERO signals
        (before R28-1 it signalled the gone number once, on the ledger's word
        alone), and the sweep signals nothing either. Unchanged: a ledger
        GENUINELY absent names nothing (the gate's existing refusal; zero
        signals), and a VALID link reads exactly as the file it names (the same
        ALREADY_GONE, zero signals)."""
        import contextlib
        import signal as signal_module
        from unittest.mock import patch
        unavailable_class = "%s: the owner ledger cannot be read (%%s)" % (
            proc_module.OBSERVATION_UNAVAILABLE)

        # each: the shape planted, as (context, the reader's cause or None —
        # then the traversal's own text — and a FIFO to release)
        def fifo(ledger):
            os.unlink(ledger)
            os.mkfifo(ledger, 0o600)
            return contextlib.nullcontext(), "NotARegularRecord", ledger

        def oversized(ledger):
            return patch.object(proc_module, "OWNER_LEDGER_BYTES", 16), "OversizedRecord", None

        def dangling(ledger):
            os.unlink(ledger)
            os.symlink(os.path.join(self.base, "no-such-ledger"), ledger)
            return contextlib.nullcontext(), None, None

        def directory(ledger):
            os.unlink(ledger)
            os.mkdir(ledger)
            return contextlib.nullcontext(), "IsADirectoryError", None

        def not_utf8(ledger):
            with open(ledger, "ab") as handle:
                handle.write(b"\xff\xfe not UTF-8\n")
            return contextlib.nullcontext(), "UnicodeDecodeError", None
        pending = "own-r27-l6-pending"
        for label, make in (("FIFO", fifo), ("oversized", oversized), ("dangling link", dangling),
                            ("directory", directory), ("not UTF-8", not_utf8)):
            with self.subTest(ledger=label):
                # each shape in its OWN scope (its own unit): nothing a failed
                # subtest leaves is ever reached by the next one
                scope = self.scope("t-l6-%s" % label.replace(" ", "-").replace("-8", "8"))
                self.stamped_root(scope, "own-r27-l6", self.DEAD, row=self.DEAD)
                proc_module.record_pending(pending, "r27-legacy", directory=scope)
                ledger = proc_module.ledger_path(scope)
                with open(ledger, "rb") as handle:
                    readable = handle.read()
                context, cause, fifo_path = make(ledger)
                fifos = [fifo_path] if fifo_path else []
                sent, killpg, kill = self.no_signal()
                patched = self.guarded_patches(context, killpg, kill)
                box = self.bounded_ledger_reads(scope, fifos)
                patched.close()                          # every reader thread observed ended
                self.assertEqual(sorted(box), ["groups", "pending", "reap", "surviving",
                                               "sweep"])
                gaps = []
                for name in ("groups", "pending", "surviving", "sweep"):
                    kind, value = box[name]
                    self.assertEqual(kind, "raised", (name, value))
                    self.assertIsInstance(value, proc_module.ObservationUnavailable)
                    gaps.append(value.unavailable)
                self.assertEqual(len({repr(gap) for gap in gaps}), 1, gaps)
                [(where, reason)] = gaps[0]
                self.assertEqual(where, ledger)
                if cause is not None:
                    self.assertEqual(reason, unavailable_class % cause)
                else:                                    # the traversal's own problem text
                    self.assertTrue(reason.startswith(unavailable_class.split("(")[0] + "("),
                                    reason)
                kind, (verdict, detail) = box["reap"]
                self.assertEqual((kind, verdict), ("ok", proc_module.REFUSED_LEDGER_UNAVAILABLE))
                self.assertIn(reason, detail)
                self.assertEqual(sent, [])               # ZERO signals
                # the observation made AVAILABLE (every thread observed ended):
                # the ledger READS again
                self.put_back(ledger, readable, always=True)
                sent, killpg, kill = self.no_signal()
                patched = self.guarded_patches(killpg, kill)
                box = self.bounded_ledger_reads(scope, [])
                patched.close()
                # Task 8 R28-1: the gate PROCEEDS past the readable ledger, and
                # the ledger's word alone signals nothing: no process of this one
                # holds ``DEAD``'s leader, and no group by that number is alive —
                # ALREADY_GONE, with ZERO signals (before R28-1: one SIGKILL to a
                # number no process held, on the ledger's word alone).
                self.assertEqual(box, {
                    "groups": ("ok", {self.DEAD}),
                    "pending": ("ok", [pending]),
                    "surviving": ("ok", []),
                    "reap": ("ok", (proc_module.ALREADY_GONE, None)),
                    "sweep": ("ok", ([], [], [pending]))})
                self.assertEqual(sent, [])
        with self.subTest(ledger="GENUINELY absent"):
            scope = self.scope("t-l6-absent")
            self.stamped_root(scope, "own-r27-l6", self.DEAD, pending=False)
            self.assertFalse(os.path.lexists(proc_module.ledger_path(scope)))
            sent, killpg, kill = self.no_signal()
            patched = self.guarded_patches(killpg, kill)
            box = self.bounded_ledger_reads(scope, [])
            patched.close()
            self.assertEqual(box["reap"][1][0], proc_module.REFUSED_NOT_IN_LEDGER)
            self.assertEqual({name: box[name] for name in ("groups", "pending", "surviving",
                                                           "sweep")},
                             {"groups": ("ok", set()), "pending": ("ok", []),
                              "surviving": ("ok", []), "sweep": ("ok", ([], [], []))})
            self.assertEqual(sent, [])
        with self.subTest(ledger="VALID link"):
            scope = self.scope("t-l6-link")
            self.stamped_root(scope, "own-r27-l6", self.DEAD, row=self.DEAD)
            proc_module.record_pending(pending, "r27-legacy", directory=scope)
            ledger = proc_module.ledger_path(scope)
            real = os.path.join(self.base, "r27-l6-real-ledger")
            os.rename(ledger, real)
            os.symlink(real, ledger)
            sent, killpg, kill = self.no_signal()
            patched = self.guarded_patches(killpg, kill)
            box = self.bounded_ledger_reads(scope, [])
            patched.close()
            self.assertEqual(box, {                     # Task 8 R28-1, as above
                "groups": ("ok", {self.DEAD}),
                "pending": ("ok", [pending]),
                "surviving": ("ok", []),
                "reap": ("ok", (proc_module.ALREADY_GONE, None)),
                "sweep": ("ok", ([], [], [pending]))})
            self.assertEqual(sent, [])


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
        workflow scope. Task 8 R22-2: and says so — the answer is
        ``UnavailableOwners``, NOT the empty set a readable empty store
        gives (the value moved; acting on no workflow scope did not)."""
        from unittest.mock import patch
        from target_runtime import runtime as runtime_module
        from workflow_authority import store as wa_store_module
        with patch.object(
            wa_store_module.WorkflowStore, "load",
            side_effect=wa_store_module.StoreError("unreadable"),
        ):
            owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertIsInstance(owners, proc_module.UnavailableOwners)
        self.assertNotEqual(owners, set())
        self.assertEqual((owners.source, owners.reason), (
            os.path.join(self.store_dir, wa_store_module.WORKFLOWS_FILE_NAME),
            "%s: the workflow store cannot be read (StoreError)"
            % proc_module.OBSERVATION_UNAVAILABLE))

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


class R28CurrentGroupOwnershipTests(unittest.TestCase):
    """Task 8 R28-1: the DIRECT reaper (``reap_owned``) signals a group its owner
    ledger names only when the CURRENT group is proven the one recorded — THIS
    process HOLDS its recorded leader (its own spawn, uncollected), or the
    recorded leader is ALIVE and corroborated by its owned root. The ledger's
    membership alone is historical: it names a NUMBER.

    Every process here is one this test spawns. Where a case says MODELED, an OS
    answer is replaced IN MEMORY: CONTROL-FLOW evidence, not a real-process
    observation — no host pid or group is reused, and nothing unrelated is
    signalled, to reproduce it. Effects are counted at their calls."""

    #: A fixture descendant's sleep; every wait here is at most 10 s.
    SLEEP = 3600
    MODELED_START = "Mon Jan  1 00:00:00 2099"

    def setUp(self):
        self.scope = tempfile.mkdtemp(prefix="r28-scope-")
        self.addCleanup(remove, self.scope)
        self.handles, self.started = [], {}
        self.addCleanup(self.settle)

    # -- fixture ---------------------------------------------------------------------

    def spawn(self, code, hold=True, scope=None):
        handle = proc_module.spawn_owned(
            [sys.executable, "-c", code], "r28-current-group",
            directory=scope or self.scope, owned_root_base_dir=scope or self.scope,
            hold_leader=hold, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.handles.append(handle)
        return handle

    def tree(self, descendants, status=0, hold=True, linger=False):
        """A leader in its own session that starts ``descendants`` same-group
        sleepers (stdio detached), prints their pids, and EXITS with ``status``
        (or, with ``linger``, sleeps). Returns ``(handle, sorted pids)``; each
        descendant's start time is booked for this fixture's own cleanup."""
        code = (
            "import subprocess, sys, time\n"
            "pids = [subprocess.Popen(['sleep', '%d'], stdin=subprocess.DEVNULL,"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid"
            " for _ in range(%d)]\n"
            "print(' '.join(map(str, pids)), flush=True)\n"
            "%s\n" % (self.SLEEP, descendants,
                      "time.sleep(%d)" % self.SLEEP if linger else "sys.exit(%d)" % status))
        handle = self.spawn(code, hold=hold)
        pids = sorted(int(pid) for pid in handle.stdout.readline().decode("ascii").split())
        handle.stdout.close()
        self.started[handle.pid] = {pid: stamp_module.leader_start_time(pid) for pid in pids}
        return handle, pids

    @staticmethod
    def live(pgid):
        """The LIVE members of group ``pgid`` — zombies excluded — read by ``ps``."""
        listed = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="],
                                capture_output=True, text=True).stdout
        return sorted(int(fields[0]) for fields in (line.split() for line in listed.splitlines())
                      if len(fields) >= 3 and fields[1] == str(pgid)
                      and not fields[2].startswith("Z"))

    @staticmethod
    def zombie(pid):
        stat_field = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True).stdout.strip()
        return stat_field.startswith("Z")

    def counted(self, deliver=True):
        """``(sent, killpg patch, kill patch)``: every signal but signal 0 is
        RECORDED at its call — and DELIVERED unless ``deliver`` is False."""
        from unittest.mock import patch
        sent, real_killpg, real_kill = [], os.killpg, os.kill

        def killpg(pgid, sig):
            if sig != 0:
                sent.append(("killpg", pgid, sig))
                if not deliver:
                    return None
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
                if not deliver:
                    return None
            return real_kill(pid, sig)
        return sent, patch.object(proc_module.os, "killpg", killpg), \
            patch.object(proc_module.os, "kill", kill)

    def settle(self):
        """FIXTURE cleanup of this case's OWN groups. A group whose leader this
        process still holds is reaped by the production reaper (held: proven). Any
        other group is signalled ONLY while EVERY live member is a descendant this
        case started — its pid AND its start time read now. Then each is waited
        for (bounded) and must be gone."""
        problems = []
        for handle in self.handles:
            pgid = handle.pid
            proc_module.disarm_hold(handle)
            if proc_module._leader_hold(pgid, self.scope)[0] == proc_module.HOLD_HELD:
                proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=5.0)
            mine = self.started.get(pgid, {})
            members = self.live(pgid)
            if members and set(members) <= set(mine) and all(
                    stamp_module.leader_start_time(pid) == mine[pid] for pid in members):
                os.killpg(pgid, signal.SIGKILL)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and self.live(pgid):
                time.sleep(0.05)
            if self.live(pgid):
                problems.append(pgid)
            if not proc_module._is_collected(handle):
                proc_module._collect_leader(handle)
        self.assertEqual(problems, [], "this case's own group(s) were not observed ended")

    # -- cases -----------------------------------------------------------------------

    def test_R28_1a_an_EXITED_held_leaders_descendants_are_reaped_POSITIVELY(self):
        """The leader EXITS (status 7) leaving THREE same-group descendants. Its
        held wait OBSERVES the exit and does not collect it: the pid is a zombie,
        the handle's return code is never fabricated, the number is HELD. The
        reap signals EXACTLY ONCE, every descendant is gone, and the leader is
        collected AFTER the signal with its TRUE status."""
        handle, kids = self.tree(3, status=7)
        pgid = handle.pid
        self.assertEqual(handle.wait(timeout=10), 7)
        self.assertTrue(self.zombie(pgid))                          # NOT collected
        self.assertIsNone(handle.returncode)
        self.assertEqual(proc_module.exit_status_of(handle), 7)
        self.assertEqual(self.live(pgid), kids)
        self.assertEqual(len(kids), 3)
        self.assertEqual(proc_module._leader_hold(pgid, self.scope), (proc_module.HOLD_HELD, None))
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])  # EXACTLY one
        self.assertEqual(self.live(pgid), [])
        self.assertEqual(handle.returncode, 7)                      # its TRUE status
        self.assertFalse(self.zombie(pgid))
        self.assertNotIn(pgid, proc_module._HELD)

    def test_R28_1b_a_COLLECTED_leaders_live_group_is_never_signalled_on_the_ledgers_word(self):
        """REAL — the pre-R28 production order: a spawn that is NOT held (its wait
        collects); the leader exits and is collected; its descendant keeps a group
        by that number alive. The ledger still names the number, and nothing now
        proves the live group the recorded one (its leader is gone: the root's
        proof cannot be made). ZERO signals, the descendant runs: unresolved —
        never REAPED or ALREADY_GONE."""
        handle, kids = self.tree(1, hold=False)
        pgid = handle.pid
        self.assertEqual(handle.wait(timeout=10), 0)                # COLLECTED
        self.assertEqual(self.live(pgid), kids)
        self.assertIn(pgid, proc_module.owned_groups(self.scope))
        self.assertEqual(proc_module._leader_hold(pgid, self.scope)[0], proc_module.HOLD_NOT_HELD)
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict, detail = proc_module.reap_owned(pgid, directory=self.scope,
                                                     settle_seconds=5.0)
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNPROVEN)
        self.assertIn(proc_module.UNCORROBORATED_LEADERLESS, detail)
        self.assertEqual(sent, [])                                   # ZERO signals
        self.assertEqual(self.live(pgid), kids)                      # never signalled

    def test_R28_1c_MODELED_a_REUSED_number_is_never_signalled(self):
        """MODELED OS — control-flow evidence, NOT a real-process observation: a
        held leader exits and is COLLECTED (the number released); the number is
        then MODELED as held by a DIFFERENT live incarnation — signal 0 to it
        answers alive, and its leader's start time is another. The ledger names
        the number; the owned root's proof CONTRADICTS it. ZERO signals (each is
        counted and never delivered): unresolved, never REAPED/ALREADY_GONE."""
        from unittest.mock import patch
        handle, _kids = self.tree(0)
        pgid = handle.pid
        handle.wait(timeout=10)
        self.assertTrue(proc_module._collect_leader(handle))       # the number released
        root = handle._di_owner[2]
        sent, real_killpg, real_kill = [], os.killpg, os.kill
        real_start = stamp_module.leader_start_time

        def killpg(group, sig):
            if group == pgid:
                if sig != 0:
                    sent.append(("killpg", group, sig))
                return None                       # MODELED: a live group by that number
            return real_killpg(group, sig)

        def kill(pid, sig):
            if pid == pgid:
                if sig != 0:
                    sent.append(("kill", pid, sig))
                return None                       # MODELED: a process holds that pid
            return real_kill(pid, sig)
        with patch.object(proc_module.os, "killpg", killpg), \
                patch.object(proc_module.os, "kill", kill), \
                patch.object(stamp_module, "leader_start_time",
                             lambda pid: self.MODELED_START if pid == pgid else real_start(pid)):
            hold = proc_module._leader_hold(pgid, self.scope)
            proof = proc_module.group_is_ours(root)
            verdict, detail = proc_module.reap_owned(pgid, directory=self.scope,
                                                     settle_seconds=1.0)
        self.assertEqual(hold[0], proc_module.HOLD_NOT_HELD)
        self.assertEqual(proof, (None, proc_module.UNCORROBORATED_START_MISMATCH))
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNPROVEN)
        self.assertIn(proc_module.UNCORROBORATED_START_MISMATCH, detail)
        self.assertEqual(sent, [])                                   # ZERO signals

    def test_R28_1d_an_UNAVAILABLE_proof_signals_NOTHING_then_restored_reaps_ONCE(self):
        """The CURRENT-group proof cannot be read at the action boundary — MODELED
        (control-flow evidence): signal 0 to the held leader's pid answers EIO;
        and, apart, SIGCHLD reads as ignored. Each: ZERO signals,
        ``REFUSED_CURRENT_GROUP_UNAVAILABLE`` naming why, the leader still
        uncollected and its descendant running — retained, never settled. The
        proof READS again: the same reap acts EXACTLY ONCE."""
        from unittest.mock import patch
        handle, kids = self.tree(1)
        pgid = handle.pid
        handle.wait(timeout=10)
        real_kill = os.kill

        def unreadable(pid, sig):
            if pid == pgid and sig == 0:
                raise OSError(errno.EIO, "modeled: unreadable")
            return real_kill(pid, sig)
        for label, make, why in (
                ("signal 0 unreadable", lambda: patch.object(proc_module.os, "kill", unreadable),
                 "could not be read (OSError)"),
                ("SIGCHLD ignored", lambda: patch.object(proc_module.signal, "getsignal",
                                                         lambda signum: signal.SIG_IGN),
                 "SIGCHLD is ignored")):
            with self.subTest(label):
                sent, killpg, _kill = self.counted()
                with killpg, make():
                    verdict, detail = proc_module.reap_owned(pgid, directory=self.scope,
                                                             settle_seconds=5.0)
                self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNAVAILABLE)
                self.assertIn(why, detail)
                self.assertEqual(sent, [])                           # ZERO signals
                self.assertTrue(self.zombie(pgid))                   # RETAINED, uncollected
                self.assertEqual(self.live(pgid), kids)
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])  # EXACTLY once
        self.assertEqual(self.live(pgid), [])
        self.assertEqual(handle.returncode, 0)

    def test_R28_1e_ANOTHER_scopes_ledger_naming_the_number_gets_NO_signal(self):
        """No competing owner ledger. A held leader spawned for THIS scope; a SECOND
        scope's ledger is made to name the same number (a row no spawn of that
        scope wrote). Through the second scope: the leader this process holds was
        spawned for another scope, and no row binds the number to a root beside
        the second ledger — ZERO signals. Through its own scope: one SIGKILL."""
        handle, kids = self.tree(1)
        pgid = handle.pid
        handle.wait(timeout=10)
        other = tempfile.mkdtemp(prefix="r28-other-")
        self.addCleanup(remove, other)
        proc_module.record_owned_group(pgid, "r28-forged", directory=other)
        self.assertIn(pgid, proc_module.owned_groups(other))
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict, detail = proc_module.reap_owned(pgid, directory=other, settle_seconds=5.0)
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNPROVEN)
        self.assertIn("spawned for another owner scope", detail)
        self.assertIn("no ledger row binds group %d" % pgid, detail)
        self.assertEqual(sent, [])
        self.assertEqual(self.live(pgid), kids)
        with killpg, kill:
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])
        self.assertEqual(self.live(pgid), [])

    def test_R28_1f_a_spawn_NOT_held_here_is_reaped_only_while_its_root_CORROBORATES(self):
        """A spawn this process does not hold — a harness that exited, a Runtime
        that died; here the in-process registry entry is withdrawn (MODELED: not
        held) while the leader really LIVES. Its owned root's proof is then the
        one recovery uses: with the root's recorded start time CHANGED it
        contradicts the live leader — ZERO signals; restored, it corroborates —
        EXACTLY one SIGKILL, leader and descendant gone."""
        handle, kids = self.tree(1, linger=True)
        pgid = handle.pid
        with proc_module._REGISTRY_LOCK:
            proc_module._OWNED.pop(pgid, None)
            proc_module._HELD.pop(pgid, None)
        self.assertEqual(proc_module._leader_hold(pgid, self.scope)[0], proc_module.HOLD_NOT_HELD)
        start = os.path.join(handle._di_owner[2], proc_module.OWNED_ROOT_START_FILE)
        with open(start, encoding="utf-8") as record:
            recorded = record.read()
        with open(start, "w", encoding="utf-8") as record:
            record.write(self.MODELED_START)
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict, detail = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=5.0)
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNPROVEN)
        self.assertIn(proc_module.UNCORROBORATED_START_MISMATCH, detail)
        self.assertEqual(sent, [])
        self.assertEqual(self.live(pgid), sorted(kids + [pgid]))
        with open(start, "w", encoding="utf-8") as record:
            record.write(recorded)
        with killpg, kill:
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])
        self.assertEqual(self.live(pgid), [])
        handle.wait(timeout=10)                       # its own handle collects it

    def test_R28_1g_the_HOLD_observes_without_collecting_and_is_kept_only_while_something_lives(
            self):
        """The hold itself. A held spawn's ``poll`` and ``wait`` OBSERVE (the pid
        stays a zombie; the return code is the observed status, never written to
        the handle); its ``communicate`` returns the output and leaves it
        uncollected. After the reap, ``disarm_hold`` KEEPS an exited leader
        uncollected while a live member remains in its group, and COLLECTS one
        with nothing alive under its number. A spawn that is not held collects
        on its wait, as before."""
        lone = self.spawn("print('answer')")
        self.assertEqual(lone.communicate(), (b"answer\n", None))
        self.assertTrue(self.zombie(lone.pid))
        self.assertEqual((lone.poll(), lone.returncode), (0, None))
        proc_module.disarm_hold(lone)                                # nothing lives under it
        self.assertEqual(lone.returncode, 0)
        self.assertFalse(self.zombie(lone.pid))
        self.assertNotIn(lone.pid, proc_module._HELD)
        handle, kids = self.tree(1, status=5)
        self.assertEqual(handle.wait(timeout=10), 5)
        proc_module.disarm_hold(handle)                              # a descendant lives
        self.assertTrue(self.zombie(handle.pid))
        self.assertEqual(proc_module._leader_hold(handle.pid, self.scope)[0],
                         proc_module.HOLD_HELD)
        self.assertEqual(self.live(handle.pid), kids)
        plain = self.spawn("pass", hold=False)
        plain.stdout.close()
        self.assertEqual(plain.wait(timeout=10), 0)
        self.assertFalse(self.zombie(plain.pid))                     # collected, as before
        self.assertIsNone(getattr(plain, "_di_hold", None))

    def test_R28_1h_CPython_collects_a_child_ONLY_through__wait_and__internal_poll(self):
        """The hold overrides ``_wait`` and ``_internal_poll`` on the handle. On the
        interpreter running this suite, EVERY route by which ``Popen`` collects
        its child goes through one of the two: only ``_try_wait`` and
        ``_internal_poll`` call ``waitpid`` (and ``_execute_child``, for a child
        whose exec failed, while the constructor raises — no handle, no hold
        yet); only ``_wait`` calls ``_try_wait``;
        ``wait``, ``communicate`` and ``__exit__`` reach ``_wait``; ``poll``,
        ``__del__`` and ``subprocess._cleanup`` reach ``_internal_poll``. A
        future interpreter that collects elsewhere FAILS here."""
        import ast
        tree = ast.parse(inspect.getsource(subprocess))
        [popen] = [node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "Popen"]
        methods = {}
        for node in ast.walk(popen):
            if isinstance(node, ast.FunctionDef):
                methods.setdefault(node.name, []).append(node)

        def names_called(functions):
            found = set()
            for function in functions:
                for node in ast.walk(function):
                    if isinstance(node, ast.Call):
                        target = node.func
                        found.add(target.attr if isinstance(target, ast.Attribute)
                                  else getattr(target, "id", None))
            return found
        collectors = {name for name, functions in methods.items()
                      if names_called(functions) & {"waitpid", "_waitpid"}}
        # ``_execute_child`` collects only a child whose EXEC FAILED, while the
        # constructor RAISES — before any handle exists, so before any hold.
        self.assertEqual(collectors, {"_try_wait", "_internal_poll", "_execute_child"})
        self.assertEqual({name for name, functions in methods.items()
                          if "_try_wait" in names_called(functions)}, {"_wait"})
        for name, route in (("wait", "_wait"), ("communicate", "wait"),
                            ("_communicate", "wait"), ("__exit__", "_wait"),
                            ("poll", "_internal_poll"), ("__del__", "_internal_poll")):
            self.assertIn(route, names_called(methods[name]), name)
        # THE SPAN's basis: both collectors take the handle's collection lock.
        for name in ("_wait", "_internal_poll"):
            self.assertTrue(any(isinstance(node, ast.Attribute) and node.attr == "_waitpid_lock"
                                for function in methods[name] for node in ast.walk(function)),
                            "%s does not take _waitpid_lock" % name)
        # module-level, under the platform branch (every definition of it)
        cleanups = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == "_cleanup" and node not in methods.get("_cleanup", [])]
        self.assertTrue(cleanups)
        self.assertIn("_internal_poll", names_called(cleanups))
        self.assertFalse(names_called(cleanups) & {"waitpid", "_waitpid"})

    def test_R28_1j_a_held_leader_with_NOTHING_under_it_SETTLES_and_is_collected(self):
        """The leader exits (status 4) leaving no descendant: its group holds only
        its zombie (observed on macOS: such a group answers EPERM to a signal).
        The reap never reads that as "could not signal": the held leader is
        collected, THEN the group observed gone — a SETTLEMENT (ALREADY_GONE
        where the platform refuses a zombie-only group a signal, REAPED where it
        accepts one), never a refusal; no zombie after, its status kept."""
        handle, kids = self.tree(0, status=4)
        pgid = handle.pid
        self.assertEqual(handle.wait(timeout=10), 4)
        self.assertEqual((kids, self.live(pgid)), ([], []))
        self.assertTrue(self.zombie(pgid))
        verdict, detail = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=5.0)
        self.assertIn(verdict, (proc_module.ALREADY_GONE, proc_module.REAPED), detail)
        self.assertIsNone(detail)
        self.assertFalse(self.zombie(pgid))
        self.assertEqual(handle.returncode, 4)

    def test_R28_1k_RECOVERY_settles_a_held_leaders_ZOMBIE_ONLY_group(self):
        """A held leader disarmed while it still RAN stays uncollected (the disarm
        keeps a running leader: its number may yet protect something); it then
        exits with nothing under it, its zombie alone holding the number.
        Recovery (``recover_orphans``) corroborates it — the zombie's start time
        is the recorded one — and its one signal meets only the zombie (EPERM on
        this platform): the leader is collected, the group observed gone —
        RECOVERED, never stuck."""
        handle = self.spawn("import time; time.sleep(0.5)")
        handle.stdout.close()                            # nothing is read from it
        pgid = handle.pid
        proc_module.disarm_hold(handle)                  # still running: KEPT
        self.assertFalse(self.zombie(pgid))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.zombie(pgid):
            time.sleep(0.05)
        self.assertTrue(self.zombie(pgid))               # exited, uncollected: number held
        self.assertTrue(proc_module._group_alive(pgid))  # a zombie-only group reads alive
        sent, killpg, kill = self.counted()
        with killpg, kill:
            recovered, stuck, _unstamped, uncorroborated = proc_module.recover_orphans(
                base=self.scope, settle_seconds=5.0)
        self.assertEqual((recovered, stuck, uncorroborated), ([pgid], [], []))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])
        self.assertFalse(self.zombie(pgid))
        self.assertEqual(handle.returncode, 0)

    def test_R28_1l_a_SECOND_collector_cannot_fall_between_the_proof_and_the_signal(self):
        """THE SPAN, on real children, with a real second thread (the test's own).
        (a) A collector started the moment the proof returns HELD —
        ``_collect_leader`` on the leader's own handle — does NOT complete inside
        the span: it waits on the handle's collection lock, and completes only
        AFTER the signal (proof, collector still waiting, signal, collected). (b)
        While another thread HOLDS the handle's collection lock (a collection in
        progress), the reap never waits on it: within its bound it refuses —
        ``REFUSED_CURRENT_GROUP_UNAVAILABLE``, ZERO signals, the leader still
        uncollected; released, the same reap acts EXACTLY ONCE."""
        import threading
        from unittest.mock import patch
        handle, kids = self.tree(1)
        pgid = handle.pid
        handle.wait(timeout=10)
        events, threads = [], []
        real_hold, real_killpg = proc_module._leader_hold, os.killpg

        def proof_then_collector(*args, **kwargs):
            result = real_hold(*args, **kwargs)
            events.append(("proof", result[0]))
            collector = threading.Thread(target=lambda: events.append(
                ("collected", proc_module._collect_leader(handle))))
            threads.append(collector)
            collector.start()
            collector.join(0.5)
            events.append(("collector", "waiting" if collector.is_alive()
                           else "finished inside the span"))
            return result

        def killpg(group, sig):
            if sig != 0:
                events.append(("signal", group))
            return real_killpg(group, sig)
        with patch.object(proc_module, "_leader_hold", proof_then_collector), \
                patch.object(proc_module.os, "killpg", killpg):
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        for thread in threads:
            thread.join(10)
        self.assertEqual(events[:3], [("proof", proc_module.HOLD_HELD), ("collector", "waiting"),
                                      ("signal", pgid)])
        self.assertIn(("collected", True), events[3:])
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(self.live(pgid), [])
        self.assertEqual(handle.returncode, 0)
        other, others = self.tree(1)
        other.wait(timeout=10)
        holding, release = threading.Event(), threading.Event()

        def collection_in_progress():
            with other._waitpid_lock:
                holding.set()
                release.wait(10)
        blocker = threading.Thread(target=collection_in_progress)
        blocker.start()
        self.assertTrue(holding.wait(5))
        sent, killpg, kill = self.counted()
        try:
            with killpg, kill, patch.object(proc_module, "_SPAN_LOCK_SECONDS", 0.2):
                started = time.monotonic()
                verdict, detail = proc_module.reap_owned(other.pid, directory=self.scope,
                                                         settle_seconds=5.0)
                elapsed = time.monotonic() - started
        finally:
            release.set()
            blocker.join(10)
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNAVAILABLE)
        self.assertIn("in progress in this process", detail)
        self.assertEqual(sent, [])                                  # ZERO signals
        self.assertLess(elapsed, 5)                                 # never waited on it
        self.assertTrue(self.zombie(other.pid))                     # uncollected
        self.assertEqual(self.live(other.pid), others)
        with killpg, kill:
            verdict = proc_module.reap_owned(other.pid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", other.pid, signal.SIGKILL)])   # EXACTLY once
        self.assertEqual(self.live(other.pid), [])

    def test_R28_1m_RECOVERY_refuses_a_number_its_proof_no_longer_holds(self):
        """Recovery's span (``reap_group_by_recorded_root``, a leader this process
        registered). Recovery corroborates a held leader's group by its owned root
        (the zombie's start time is the recorded one) — and the leader is then
        COLLECTED before the signal (MODELED: the collection is made right after
        the proof, standing for a second collector at exactly that point; a
        descendant keeps a group by that number alive). Under the span the leader
        is found collected: the number the proof was made on is no longer this
        process's — REFUSED, ZERO signals, reported STUCK, never recovered."""
        from unittest.mock import patch
        handle, kids = self.tree(1)
        pgid = handle.pid
        handle.wait(timeout=10)
        proc_module.disarm_hold(handle)                  # a descendant lives: KEPT
        real_proof = proc_module.group_is_ours

        def proof_then_collected(directory, record=None):
            result = real_proof(directory, record)
            proc_module._collect_leader(handle)          # collected right after the proof
            return result
        sent, killpg, kill = self.counted()
        with killpg, kill, patch.object(proc_module, "group_is_ours", proof_then_collected):
            recovered, stuck, _unstamped, uncorroborated = proc_module.recover_orphans(
                base=self.scope, settle_seconds=5.0)
        self.assertEqual((recovered, stuck, uncorroborated), ([], [pgid], []))
        self.assertEqual(sent, [])                                  # ZERO signals
        self.assertEqual(self.live(pgid), kids)                     # never signalled

    # -- the BOUNDED, STRICT membership observation of ``disarm_hold`` ---------------

    #: How long a STALLED stand-in observer sleeps; it ends by itself.
    STALL = 6
    #: A stand-in observer that prints the host's REAL listing with every row of
    #: group ``argv[1]`` but its leader's made unparseable (a letter in the pid).
    PARTIAL = (
        "import subprocess, sys\n"
        "out = subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='],"
        " stdout=subprocess.PIPE, check=True).stdout.decode('ascii')\n"
        "for line in out.splitlines():\n"
        "    f = line.split()\n"
        "    print('x' + line.strip() if f[1] == sys.argv[1] != f[0] else line)\n")
    #: ... the REAL listing WITHOUT group ``argv[1]``'s rows and this test
    #: process's group's rows (its own, the observer's): every row well formed.
    NO_RECEIPT = (
        "import os, subprocess, sys\n"
        "out = subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='],"
        " stdout=subprocess.PIPE, check=True).stdout.decode('ascii')\n"
        "for line in out.splitlines():\n"
        "    if line.split()[1] not in (sys.argv[1], str(os.getpgrp())):\n"
        "        print(line)\n")
    #: ... the REAL listing, with a diagnostic on stderr.
    DIAGNOSTIC = (
        "import subprocess, sys\n"
        "sys.stdout.write(subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='],"
        " stdout=subprocess.PIPE, check=True).stdout.decode('ascii'))\n"
        "sys.stderr.write('ps: a partial failure\\n')\n")

    def observing(self, code, *args):
        """The production membership read's observer REPLACED by ``code`` (a
        Python child of THIS test): ``(observers, argv patch, Popen patch)`` —
        every observer that read starts is RECORDED, so its own end can be read."""
        from unittest.mock import patch
        observers, real = [], subprocess.Popen

        class Recorded(real):
            def __init__(created, argv, *rest, **kwargs):
                super(Recorded, created).__init__(argv, *rest, **kwargs)
                observers.append(created)
        self.addCleanup(self.observers_ended, observers)
        return observers, patch.object(proc_module, "_MEMBERSHIP_ARGV",
                                       (sys.executable, "-c", code) + args), \
            patch.object(subprocess, "Popen", Recorded)

    def failing_observers(self):
        """The production membership read's observer REPLACED by a stand-in that
        STALLS (sleeps ``STALL`` s; a Python child of THIS test) and whose OWN
        cleanup FAILS — MODELED (control-flow evidence): its handle's ``kill`` and
        ``wait`` and its stdout's ``close`` raise ``OSError`` without reaching the
        child. ``(observers, [argv patch, Popen patch])``; ``observers_ended``
        then waits for each (it ends by itself) and closes its pipes."""
        from unittest.mock import patch
        argv = (sys.executable, "-c", "import time; time.sleep(%d)" % self.STALL)
        observers, real = [], subprocess.Popen

        class Unclosable(object):
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            @property
            def closed(self):
                return self.stream.closed

            def close(self):
                raise OSError(errno.EIO, "modeled: the observer's pipe does not close")

        class Failing(real):
            def kill(created):
                raise OSError(errno.EPERM, "modeled: the observer's kill fails")

            def wait(created, timeout=None):
                if not getattr(created, "released", False):
                    raise OSError(errno.EIO, "modeled: the observer's wait fails")
                return real.wait(created, timeout)

        def popen(args, *rest, **kwargs):
            if tuple(args) != argv:
                return real(args, *rest, **kwargs)
            created = Failing(args, *rest, **kwargs)
            created.stdout = Unclosable(created.stdout)
            observers.append(created)
            return created
        self.addCleanup(self.observers_ended, observers)
        return observers, [patch.object(proc_module, "_MEMBERSHIP_ARGV", argv),
                           patch.object(subprocess, "Popen", popen)]

    def observers_ended(self, observers):
        """FIXTURE cleanup: each recorded observer (or turn child) is WAITED for,
        bounded — never signalled here (each ends by itself within ``STALL``
        seconds, a turn child once its stdin is closed; a MODELED failure stops
        answering first) — and its pipes are closed."""
        for observer in observers:
            observer.released = True
            if observer.stdin is not None and not observer.stdin.closed:
                observer.stdin.close()
            if observer.returncode is None:
                observer.wait(timeout=self.STALL + 10)
            for stream in (observer.stdout, observer.stderr):
                stream = getattr(stream, "stream", stream)
                if stream is not None and not stream.closed:
                    stream.close()

    def test_R28_1n_a_STALLED_membership_observer_is_BOUNDED_and_decides_NOTHING(self):
        """The leader EXITS (status 6) with NOTHING alive under it — so a valid
        observation would collect it. At the disarm the membership observer
        STALLS (a stand-in that sleeps ``STALL`` s; the bound patched to 0.5 s):
        the disarm returns within its bound, says UNAVAILABLE, and decides
        NOTHING — the ONE signal is the SIGKILL to the observer's OWN pid, which
        is observed ended; no signal reaches the group; the leader is NOT
        collected (a zombie, its number still HELD, the hold's lock released).
        A later VALID observation then progresses: the leader is collected with
        its TRUE status. (The reason names the kill as INVOKED and the end as
        OBSERVED, with its status — which proves the end, not its cause.)"""
        from unittest.mock import patch
        handle, kids = self.tree(0, status=6)
        pgid = handle.pid
        self.assertEqual((handle.wait(timeout=10), kids), (6, []))
        observers, argv, popen = self.observing("import time; time.sleep(%d)" % self.STALL)
        sent, killpg, kill = self.counted()
        with argv, popen, killpg, kill, patch.object(proc_module, "_OBSERVER_SECONDS", 0.5):
            began = time.monotonic()
            decided = proc_module.disarm_hold(handle)
            elapsed = time.monotonic() - began
        self.assertLess(elapsed, 3.0, decided)
        self.assertEqual(len(observers), 1, decided)
        self.assertEqual(sent, [("kill", observers[0].pid, signal.SIGKILL)])
        self.assertEqual(observers[0].returncode, -signal.SIGKILL)
        self.assertIsNone(handle.returncode)
        self.assertTrue(self.zombie(pgid))
        self.assertEqual(proc_module._leader_hold(pgid, self.scope)[0], proc_module.HOLD_HELD)
        self.assertFalse(handle._di_hold.lock.locked())
        self.assertIn("UNAVAILABLE", decided)
        self.assertIn("a kill was invoked on the observer's own handle; its end was observed"
                      " (status %d)" % -signal.SIGKILL, decided)
        self.assertEqual(proc_module.disarm_hold(handle), "collected")   # a VALID observation
        self.assertEqual(handle.returncode, 6)
        self.assertFalse(self.zombie(pgid))
        self.assertNotIn(pgid, proc_module._HELD)

    def test_R28_1o_an_UNAVAILABLE_or_UNPARSEABLE_membership_never_reads_as_NONE(self):
        """The leader EXITS (status 5) leaving ONE live same-group descendant. At
        the disarm the membership observer is, in turn: one that exits non-zero;
        one whose output does not parse; the REAL listing with that descendant's
        row made unparseable (a skipped row would count it as none); the REAL
        listing with a diagnostic on stderr; and a well-formed listing that holds
        no row for this process (no POSITIVE RECEIPT; the descendant's row is
        absent from it too). Each is UNAVAILABLE — never "no live member": ZERO
        signals, the leader NOT collected, its number still HELD. The REAL
        observer then counts the descendant (kept held), and the reap reaps it
        POSITIVELY: one signal, the descendant gone, the leader's TRUE status."""
        handle, kids = self.tree(1, status=5)
        pgid = handle.pid
        self.assertEqual(handle.wait(timeout=10), 5)
        self.assertEqual(self.live(pgid), kids)
        for case, code in (("exits non-zero", "import sys; sys.exit(3)"),
                           ("malformed", "print('not a listing')"),
                           ("partially parseable", self.PARTIAL),
                           ("with a diagnostic", self.DIAGNOSTIC),
                           ("no receipt", self.NO_RECEIPT)):
            observers, argv, popen = self.observing(code, str(pgid))
            sent, killpg, kill = self.counted()
            with argv, popen, killpg, kill:
                decided = proc_module.disarm_hold(handle)
            self.assertEqual(sent, [], case)
            self.assertIsNone(handle.returncode, case)
            self.assertIn("UNAVAILABLE", decided, case)
            self.assertEqual([observer.returncode is not None for observer in observers],
                             [True], case)
            self.assertTrue(self.zombie(pgid), case)
            self.assertEqual(proc_module._leader_hold(pgid, self.scope)[0],
                             proc_module.HOLD_HELD, case)
        self.assertEqual(proc_module.disarm_hold(handle),
                         "kept held: 1 live member(s) remain in its group")
        sent, killpg, kill = self.counted()
        with killpg, kill:
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])
        self.assertEqual(self.live(pgid), [])
        self.assertEqual(handle.returncode, 5)

    def test_R28_1p_the_disarm_never_WAITS_on_its_hold_lock_past_its_bound(self):
        """The leader EXITS (status 2) with nothing under it. While ANOTHER thread
        (this test's own) holds the leader's hold lock — an observation or a span
        in progress — the disarm does not wait on it: within its bound (patched
        to 0.2 s) it decides NOTHING — no signal, the leader NOT collected, still
        HELD. Once that lock is free, the disarm collects it."""
        import threading
        from unittest.mock import patch
        handle, kids = self.tree(0, status=2)
        pgid = handle.pid
        self.assertEqual((handle.wait(timeout=10), kids), (2, []))
        holding, done = threading.Event(), threading.Event()

        def holder():
            with handle._di_hold.lock:
                holding.set()
                done.wait(timeout=3)                     # held at most 3 s
        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(holding.wait(timeout=10))
        sent, killpg, kill = self.counted()
        try:
            with killpg, kill, patch.object(proc_module, "_SPAN_LOCK_SECONDS", 0.2):
                began = time.monotonic()
                decided = proc_module.disarm_hold(handle)
                elapsed = time.monotonic() - began
        finally:
            done.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed, 1.0, decided)
        self.assertEqual(sent, [])
        self.assertIsNone(handle.returncode)
        self.assertTrue(self.zombie(pgid))
        self.assertTrue(decided.startswith("kept held: its hold's lock was not acquired"), decided)
        self.assertEqual(proc_module.disarm_hold(handle), "collected")
        self.assertEqual(handle.returncode, 2)

    def test_R28_1q_the_ROLE_TURN_runner_RETURNS_its_known_outcome_when_the_observer_FAILS(self):
        """``codex_gateway.role_turn._default_runner`` — the production Codex spawn
        — runs a turn that answers and exits with nothing under it. Its reap is
        REFUSED (MODELED: ``reap_owned`` answers REFUSED_CURRENT_GROUP_UNAVAILABLE
        in memory, signalling nothing), so the disarm reads the group's membership
        — and that observer STALLS and its OWN cleanup FAILS (``failing_observers``:
        its kill, its wait and its pipe's close raise). Nothing escapes: the runner
        RETURNS the turn's KNOWN outcome — status 0, its answer, its pid — so no
        post-spawn cleanup failure reaches a caller that would read it as "could
        not be executed"; ZERO signals; the leader NOT collected, its number still
        HELD; the disarm's reason UNAVAILABLE, naming each failed step and the
        observer's end UNPROVEN — never settled. The REAL reap then settles it."""
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        base = tempfile.mkdtemp(prefix="r28-turn-")
        self.addCleanup(remove, base)
        scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                                         "wf-r28q", "t-r28q", base=base)
        spawned, decided = [], []
        real_spawn, real_disarm = proc_module.spawn_owned, proc_module.disarm_hold

        def spawn(*args, **kwargs):
            spawned.append(real_spawn(*args, **kwargs))
            return spawned[-1]

        def disarm(process):
            decided.append(real_disarm(process))
            return decided[-1]

        def refused(pgid, directory=None, **_kwargs):
            return proc_module.REFUSED_CURRENT_GROUP_UNAVAILABLE, "modeled: unreadable"

        def settle_turn_group():
            """FIXTURE settlement of this case's OWN held leader, by the production
            reaper (nothing lives under it)."""
            if spawned and not proc_module._is_collected(spawned[0]):
                proc_module.reap_owned(spawned[0].pid, directory=scope, settle_seconds=5.0)
        self.addCleanup(settle_turn_group)
        observers, observing = self.failing_observers()
        sent, killpg, kill = self.counted()
        raised = outcome = None
        with killpg, kill, observing[0], observing[1], \
                patch.object(proc_module, "_OBSERVER_SECONDS", 0.5), \
                patch.object(proc_module, "spawn_owned", spawn), \
                patch.object(proc_module, "reap_owned", refused), \
                patch.object(proc_module, "disarm_hold", disarm):
            try:
                outcome = role_turn_module._default_runner(
                    [sys.executable, "-c", "import sys; sys.stdout.write('answered')"],
                    b"", None, owner_scope=scope)
            except Exception as exc:                     # noqa: BLE001 - asserted next
                raised = exc
        self.assertIsNone(raised)
        self.assertEqual(outcome[:2], (0, b"answered"), outcome)
        self.assertEqual(outcome[3], spawned[0].pid)                 # its KNOWN pid
        self.assertEqual(sent, [])                                   # ZERO signals
        self.assertEqual((len(observers), len(decided)), (1, 1))
        self.assertTrue(decided[0].startswith("kept held: whether anything lives in its group"
                                              " is UNAVAILABLE"), decided[0])
        for step in ("a kill invoked on the observer's own handle FAILED (PermissionError)",
                     "its end could NOT be observed (OSError): UNPROVEN",
                     "the observer's stdout did not close (OSError)"):
            self.assertIn(step, decided[0])
        self.assertIsNone(spawned[0].returncode)                     # NOT collected
        self.assertTrue(self.zombie(spawned[0].pid))
        self.assertEqual(proc_module._leader_hold(spawned[0].pid, scope)[0],
                         proc_module.HOLD_HELD)
        verdict = proc_module.reap_owned(spawned[0].pid, directory=scope, settle_seconds=5.0)
        self.assertIn(verdict[0], (proc_module.ALREADY_GONE, proc_module.REAPED), verdict)
        self.assertFalse(self.zombie(spawned[0].pid))
        self.assertEqual(spawned[0].returncode, 0)

    def test_R28_1r_a_STALLED_leader_query_inside_the_span_is_BOUNDED_and_refuses(self):
        """The not-held proof runs INSIDE the reap's span — the leader's hold lock
        and its handle's collection lock held — and its leader-start query
        (``spawn_stamp.leader_start_time``) STALLS: a stand-in that sleeps
        ``STALL`` s, the bound patched to 0.5 s. The leader is a LIVE spawn of this
        process taken as NOT held (MODELED: ``_leader_hold`` answers HOLD_NOT_HELD
        in memory), so that proof WOULD corroborate it. The query's expiry is None,
        the documented fail-closed answer: the reap REFUSES
        (REFUSED_CURRENT_GROUP_UNAVAILABLE) within its bound; NO signal reaches the
        group (the one kill recorded is ``subprocess.run`` ending its OWN stalled
        query); leader and descendant alive, uncollected; BOTH span locks
        released. With the real query back, the same not-held proof corroborates
        and the reap reaps EXACTLY once."""
        from unittest.mock import patch
        handle, kids = self.tree(1, linger=True)
        pgid = handle.pid
        real_proof, inside = proc_module.group_is_ours, []

        def proof(directory, record=None):
            inside.append(handle._waitpid_lock.locked())
            return real_proof(directory, record)

        def not_held(*_args):
            return proc_module.HOLD_NOT_HELD, "modeled: not held"
        stalled = (sys.executable, "-c", "import time; time.sleep(%d)" % self.STALL)
        sent, killpg, kill = self.counted()
        with killpg, kill, patch.object(proc_module, "_leader_hold", not_held), \
                patch.object(proc_module, "group_is_ours", proof), \
                patch.object(stamp_module, "LEADER_QUERY", stalled), \
                patch.object(stamp_module, "LEADER_QUERY_SECONDS", 0.5):
            began = time.monotonic()
            verdict, detail = proc_module.reap_owned(pgid, directory=self.scope,
                                                     settle_seconds=5.0)
            elapsed = time.monotonic() - began
        self.assertLess(elapsed, 3.0, verdict)
        self.assertEqual(inside, [True])                 # the query ran INSIDE the span
        self.assertEqual(verdict, proc_module.REFUSED_CURRENT_GROUP_UNAVAILABLE, detail)
        self.assertEqual([entry for entry in sent
                          if entry[0] == "killpg" or entry[1] in [pgid] + kids], [])
        self.assertEqual([entry[::2] for entry in sent], [("kill", signal.SIGKILL)])
        self.assertFalse(handle._waitpid_lock.locked())
        self.assertFalse(handle._di_hold.lock.locked())
        self.assertIsNone(handle.returncode)
        self.assertEqual(self.live(pgid), sorted(kids + [pgid]))
        sent, killpg, kill = self.counted()
        with killpg, kill, patch.object(proc_module, "_leader_hold", not_held):
            verdict = proc_module.reap_owned(pgid, directory=self.scope, settle_seconds=10.0)
        self.assertEqual(verdict, (proc_module.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.live(pgid), [])

    # -- PRE-EFFECT refusal versus a KNOWN POST-SPAWN failure, on the production path --

    def turn_fixture(self, failing_io=False):
        """The PRODUCTION role-turn path — ``_spawn_restricted`` with its real
        runner — on a stand-in ``codex``: a shell script on PATH (exec'd by the
        stamping wrapper) that reads its prompt to EOF and exits 0. Returns
        ``(children, scope, patches, turn)``: ``turn()`` runs ONE turn; every
        child the stamping wrapper starts is RECORDED in ``children``; with
        ``failing_io`` that child's ``communicate`` raises ``OSError`` (MODELED:
        control-flow evidence). ``observers_ended`` then closes each child's
        stdin and waits for it — never signalling."""
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        base = tempfile.mkdtemp(prefix="r28-post-spawn-")
        self.addCleanup(remove, base)
        scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                                         "wf-r28s", "t-r28s", base=base)
        control = os.path.realpath(tempfile.mkdtemp(prefix="r28-control-"))
        self.addCleanup(remove, control)
        bindir = os.path.join(base, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "codex"), "w") as handle:
            handle.write("#!/bin/sh\ncat >/dev/null\nexit 0\n")
        os.chmod(os.path.join(bindir, "codex"), 0o755)
        children, real = [], subprocess.Popen

        class FailingIO(real):
            def communicate(created, *args, **kwargs):
                raise OSError(errno.EIO, "modeled: the turn's I/O fails")

        def popen(args, *rest, **kwargs):
            wrapped = (isinstance(args, (list, tuple)) and len(args) > 2
                       and args[1] == proc_module._STAMP_WRAPPER)
            created = (FailingIO if wrapped and failing_io else real)(args, *rest, **kwargs)
            if wrapped:
                children.append(created)
            return created
        self.addCleanup(self.observers_ended, children)
        path = os.pathsep.join((bindir, os.environ.get("PATH", os.defpath)))
        patches = [patch.dict(os.environ, {"PATH": path}),
                   patch.object(subprocess, "Popen", popen)]

        def turn():
            return role_turn_module._spawn_restricted(
                "lead", "the prompt", control, "2026-10-03T00:00:00Z", None, None,
                owner_scope=scope)
        return children, scope, patches, turn

    def test_R28_1s_a_POST_SPAWN_record_failure_keeps_the_turns_KNOWN_pid_UNRESOLVED(self):
        """The production role-turn path: the child STARTS, then its spawn's
        parent-side group record fails (injected: ``record_owned_group`` raises
        ``OSError``, as VL6b does for the verification). Never "the binary could not
        be executed": the turn is FAILED ``codex_spawned_outcome_unresolved`` and
        CARRIES the started child's KNOWN pid, its detail naming the owner scope
        and the failed step; exactly ONE spawn; ZERO signals; the child NOT
        collected and nothing concluded about it (it waits on its prompt); and its
        own owned root names it (the child stamps itself before exec)."""
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        children, scope, patches, turn = self.turn_fixture()
        failed = OSError(errno.EIO, "modeled: the ledger write failed")
        sent, killpg, kill = self.counted()
        raised = result = None
        with killpg, kill, patches[0], patches[1], \
                patch.object(proc_module, "record_owned_group", side_effect=failed):
            try:
                result = turn()
            except Exception as exc:                     # noqa: BLE001 - asserted next
                raised = exc
        self.assertIsNone(raised)
        turn_record, message, error = result
        self.assertEqual((len(children), message), (1, None))
        pid = children[0].pid
        unresolved = (role_turn_module.ROLE_TURN_FAILED,
                      role_turn_module.REASON_SPAWNED_OUTCOME_UNRESOLVED)
        self.assertEqual((error.status, error.reason), unresolved)
        self.assertEqual((turn_record["process_id"], error.turn), (pid, turn_record))
        self.assertIn("pid %d, owner scope %s" % (pid, scope), error.error.detail)
        self.assertIn("its spawn's ownership records failed (OSError", error.error.detail)
        self.assertEqual(sent, [])                                   # ZERO signals
        self.assertIsNone(children[0].returncode)                    # NOT collected
        root = children[0]._di_owner[2]
        deadline = time.monotonic() + 10
        while (time.monotonic() < deadline
               and proc_module.owned_root_record(root)["pgid"] != pid):
            time.sleep(0.05)
        self.assertEqual(proc_module.owned_root_record(root)["pgid"], pid)

    def test_R28_1t_a_POST_SPAWN_IO_failure_is_reaped_and_reported_UNRESOLVED_with_its_pid(self):
        """The production role-turn path: the child STARTS and waits on its
        prompt; then its turn's I/O fails (MODELED: its handle's ``communicate``
        raises ``OSError``). The runner's cleanup still runs on the HELD leader —
        its group reaped EXACTLY ONCE, the leader collected by that reap — and the
        turn is FAILED ``codex_spawned_outcome_unresolved`` with its KNOWN pid:
        never "could not be executed", and no exit status is reported as the
        turn's (the reap's collection settles a group, not a turn)."""
        from codex_gateway import role_turn as role_turn_module
        children, scope, patches, turn = self.turn_fixture(failing_io=True)
        sent, killpg, kill = self.counted()
        raised = result = None
        with killpg, kill, patches[0], patches[1]:
            try:
                result = turn()
            except Exception as exc:                     # noqa: BLE001 - asserted next
                raised = exc
        self.assertIsNone(raised)
        turn_record, message, error = result
        self.assertEqual((len(children), message), (1, None))
        pid = children[0].pid
        unresolved = (role_turn_module.ROLE_TURN_FAILED,
                      role_turn_module.REASON_SPAWNED_OUTCOME_UNRESOLVED)
        self.assertEqual((error.status, error.reason), unresolved)
        self.assertEqual((turn_record["process_id"], error.turn), (pid, turn_record))
        self.assertIn("pid %d, owner scope %s" % (pid, scope), error.error.detail)
        self.assertIn("its turn's I/O failed (OSError", error.error.detail)
        self.assertEqual(sent, [("killpg", pid, signal.SIGKILL)])   # its cleanup's ONE
        self.assertEqual(children[0].returncode, -signal.SIGKILL)    # collected by the reap
        self.assertNotIn(pid, proc_module._HELD)

    def test_R28_1u_a_PRE_SPAWN_refusal_stays_could_not_be_executed_with_NO_turn(self):
        """The CONTROL: the same production path failing BEFORE any process exists
        (injected: the spawn's owned root cannot be created — ``create_owned_root``
        raises ``OSError`` ahead of ``Popen``). That is the genuine PRE-EFFECT
        refusal, unchanged: REFUSED ``codex_binary_unavailable`` with NO turn
        identity — NO child started, nothing signalled."""
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        children, scope, patches, turn = self.turn_fixture()
        refused = OSError(errno.ENOSPC, "modeled: no space for the owned root")
        sent, killpg, kill = self.counted()
        with killpg, kill, patches[0], patches[1], \
                patch.object(proc_module, "create_owned_root", side_effect=refused):
            turn_record, message, error = turn()
        self.assertEqual((turn_record, message, children, sent), (None, None, [], []))
        self.assertEqual((error.status, error.reason), (role_turn_module.ROLE_TURN_REFUSED,
                                                        role_turn_module.REASON_BINARY_UNAVAILABLE))
        self.assertIsNone(error.turn)

    def test_R28_1v_a_CLEANUP_failure_after_a_KNOWN_outcome_keeps_that_outcome(self):
        """The production role-turn path, the runner's CLEANUP boundary: the
        stand-in turn reads its prompt and exits 0 (its output known: empty), then
        the reap RAISES ``OSError`` (MODELED: ``reap_owned`` raises in memory,
        signalling nothing). The runner contains it — the process exists, so never
        "could not be executed" — and RETURNS the turn's known outcome, which the
        pipeline then classifies by its OUTPUT alone: FAILED
        ``malformed_output`` (no terminal message), carrying the KNOWN pid; ZERO
        signals; the disarm still decides (nothing alive: collected, status 0)."""
        from unittest.mock import patch
        from codex_gateway import role_turn as role_turn_module
        children, scope, patches, turn = self.turn_fixture()

        def reap_raises(*_args, **_kwargs):
            raise OSError(errno.EIO, "modeled: the reap's ledger read failed")
        sent, killpg, kill = self.counted()
        raised = result = None
        with killpg, kill, patches[0], patches[1], \
                patch.object(proc_module, "reap_owned", reap_raises):
            try:
                result = turn()
            except Exception as exc:                     # noqa: BLE001 - asserted next
                raised = exc
        self.assertIsNone(raised)
        turn_record, message, error = result
        self.assertEqual((len(children), message), (1, None))
        pid = children[0].pid
        output_classified = (role_turn_module.ROLE_TURN_FAILED,
                             role_turn_module.REASON_MALFORMED_OUTPUT)
        self.assertEqual((error.status, error.reason), output_classified)
        self.assertEqual((turn_record["process_id"], error.turn), (pid, turn_record))
        self.assertEqual(sent, [])                                   # ZERO signals
        self.assertEqual(children[0].returncode, 0)                  # the disarm collected it
        self.assertNotIn(pid, proc_module._HELD)

    def test_R28_1i_the_ROLE_TURN_runner_reaps_what_its_turn_left_POSITIVELY(self):
        """``codex_gateway.role_turn._default_runner`` — the production Codex spawn
        — HOLDS its process: the turn answers, leaves TWO same-group descendants
        (stdio detached, so ``communicate`` returns) and exits. The runner's reap
        signals the group EXACTLY ONCE, both descendants are gone, the process is
        collected, and the runner returns the turn's own status and answer."""
        from codex_gateway import role_turn as role_turn_module
        base = tempfile.mkdtemp(prefix="r28-turn-")
        self.addCleanup(remove, base)
        scope = proc_module.assign_scope(proc_module.OWNER_TYPE_WORKFLOW, "/control/repo",
                                         "wf-r28", "t-r28", base=base)
        marker = os.path.join(base, "r28-turn-descendants")
        turn = ("import subprocess, sys\n"
                "pids = [subprocess.Popen(['sleep', '%d'], stdin=subprocess.DEVNULL,"
                " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid for _ in range(2)]\n"
                "open(sys.argv[1], 'w').write(' '.join(map(str, pids)))\n"
                "sys.stdout.write('answered')\n" % self.SLEEP)
        sent, killpg, kill = self.counted()
        with killpg, kill:
            rc, out, err, pid = role_turn_module._default_runner(
                [sys.executable, "-c", turn, marker], b"", None, owner_scope=scope)
        with open(marker) as handle:
            kids = [int(value) for value in handle.read().split()]
        booked = {kid: stamp_module.leader_start_time(kid) for kid in kids}

        def settle_kids():
            """FIXTURE: this case's own descendants, ONLY while each is proven by
            its pid AND its start time read now (none should remain)."""
            for kid, started in booked.items():
                if started is not None and stamp_module.leader_start_time(kid) == started:
                    os.kill(kid, signal.SIGKILL)
        self.addCleanup(settle_kids)
        self.assertEqual((rc, out), (0, b"answered"), err)
        self.assertEqual(len(kids), 2)
        self.assertEqual(sent, [("killpg", pid, signal.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.live(pid), [])
        self.assertFalse(self.zombie(pid))
        self.assertNotIn(pid, proc_module._HELD)


# `runpy.run_path(..., run_name="__main__")`. Within that runner, a
# module lacking this block imports and exits, so the shim observes no
# git process and the sweep reports having no observation to assert on.
if __name__ == "__main__":
    unittest.main()
