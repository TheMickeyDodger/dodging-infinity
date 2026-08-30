"""I1: workspace trust for the ONE workspace DI just materialized.

Each guarantee about what this code DOES to a configuration file is
pinned by an EXECUTED byte comparison of the file before and after,
not by a source scan and not by asserting on a mock.

HARD SAFETY: no test here writes, moves, truncates or mutates the
developer's real ``~/.claude.json``. Each test operates on a config
inside a temp directory. The one test that must run against the real
dependency operates on a BYTE-COPY and re-verifies the real file's
sha256 before and after.
"""

import ast
import collections
import errno
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))

from target_runtime import broker as broker_module
from target_runtime import workspace as workspace_module
from target_runtime import workspace_trust as trust_module
from workflow_authority import record as record_module

import test_hermetic_git as hermetic_git
import test_target_runtime as runtime_harness
from _di_remote2_surface import (
    DI_REMOTE_2_PYTHON, DI_REMOTE_2_SOURCE_CHECKS,
    DI_REMOTE_2_TEST_PYTHON, I1_DOCUMENT_UNIT_DIGESTS,
    protected_document_units,
)

def _unredirectable_home_path():
    """The user's home from the PASSWORD DATABASE, not `$HOME`.

    Round-02 attack B showed HOME/PATH redirection silently taking a
    disclosed exemption. `REAL_CONFIG` had the same shape: derived
    through `expanduser`, a redirected HOME made the real file look
    absent and Population B skipped with a message claiming the
    machine has no configuration. The password database is not
    redirectable by the environment, so the skip now means what it
    says.
    """
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


REAL_CONFIG = os.path.join(_unredirectable_home_path(), ".claude.json")


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


# A config fixture that CONTAINS the conditions the guards protect:
# several other project entries (one of them already trusted, one
# explicitly untrusted) and a spread of top-level global keys,
# including permission-bearing ones.
OTHER_PROJECTS = {
    "/Users/someone/other-repo": {
        "allowedTools": ["Bash(git status)"],
        "mcpContextUris": [],
        "enabledMcpjsonServers": [],
        "disabledMcpjsonServers": [],
        "hasTrustDialogAccepted": True,
        "hasClaudeMdExternalIncludesApproved": False,
        "hasClaudeMdExternalIncludesWarningShown": False,
    },
    "/Users/someone/untrusted-repo": {
        "allowedTools": [],
        "hasTrustDialogAccepted": False,
    },
    "/Users/someone/café": {"hasTrustDialogAccepted": True},
}

GLOBAL_KEYS = {
    "numStartups": 127,
    "installMethod": "native",
    "hasCompletedOnboarding": True,
    "autoUpdates": False,
    "mcpServers": {},
    "oauthAccount": {"accountUuid": "abc", "emailAddress": "x@y.z"},
    "tipsHistory": {"memory-command": 116},
}


def config_document(projects=None):
    document = dict(GLOBAL_KEYS)
    document["projects"] = dict(
        OTHER_PROJECTS if projects is None else projects
    )
    return document


def write_config(path, document):
    """Write a config the way the CLI does: JSON.stringify(v, null, 2),
    no trailing newline, non-ASCII unescaped, mode 0600."""
    payload = json.dumps(
        document, indent=trust_module.JSON_INDENT,
        ensure_ascii=trust_module.JSON_ENSURE_ASCII,
    ).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)
    return payload


class TrustFixture(unittest.TestCase):
    """A DI-managed workspaces root with one materialized lease."""

    workflow_id = "wf-0001"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.workspaces = os.path.join(self.base, "workspaces")
        os.makedirs(self.workspaces)
        self.lease = workspace_module.lease_path(
            self.workspaces, self.workflow_id
        )
        os.makedirs(self.lease)
        self.config = os.path.join(self.base, ".claude.json")
        self.before = write_config(self.config, config_document())

    def entry(self, path=None, lease=True):
        record = {"workflow_id": self.workflow_id}
        if lease:
            record["workspace_lease"] = {
                "lease_id": "lease-abc",
                "path_realpath": (
                    self.lease if path is None else path
                ),
                "acquired_at": "2026-08-28T00:00:00Z",
                "released_at": None,
            }
        return record

    def establish(self, **kwargs):
        return trust_module.establish(
            self.entry(**kwargs), self.workspaces, self.config
        )

    def assertFileUnchanged(self, message=""):
        self.assertEqual(
            read_bytes(self.config), self.before,
            "the configuration file was modified " + message,
        )

    def assertRefused(self, outcome, problem):
        ok, observed, detail = outcome
        self.assertFalse(ok, "expected a refusal, got success")
        self.assertEqual(observed, problem, detail)
        self.assertIn(observed, trust_module.TRUST_PROBLEM_CODES)


class BoundaryScopeTests(TrustFixture):
    """P1/P2: establishment is possible one for this workflow's own
    lease path under the DI-owned root. Each refusal below is proven
    to write no content by a whole-file byte comparison."""

    def test_boundary_is_derived_from_the_workspace_module(self):
        # The accepted path is not a literal in workspace_trust: it is
        # workspace.lease_path's own answer. Falsifying that
        # derivation (returning a different path) must make the one
        # accepted path unacceptable.
        accepted, problem, _ = trust_module.resolve_managed_target(
            self.entry(), self.workspaces
        )
        self.assertIsNone(problem)
        self.assertEqual(
            accepted,
            os.path.realpath(workspace_module.lease_path(
                self.workspaces, self.workflow_id
            )),
        )

    def test_path_outside_the_managed_root_is_refused_with_no_write(self):
        outside = os.path.join(self.base, "not-managed")
        os.makedirs(outside)
        self.assertRefused(
            self.establish(path=outside),
            trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT,
        )
        self.assertFileUnchanged("for a path outside the root")

    def test_the_managed_root_itself_is_refused(self):
        # Trusting the ROOT would trust each workflow's workspace at
        # once (the CLI resolves trust by walking UP).
        self.assertRefused(
            self.establish(path=self.workspaces),
            trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT,
        )
        self.assertFileUnchanged("for the managed root itself")

    def test_ancestor_of_the_root_is_refused(self):
        self.assertRefused(
            self.establish(path=self.base),
            trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT,
        )
        self.assertFileUnchanged("for an ancestor of the root")

    def test_another_workflows_lease_inside_the_root_is_refused(self):
        other = workspace_module.lease_path(
            self.workspaces, "wf-9999"
        )
        os.makedirs(other)
        self.assertRefused(
            self.establish(path=other),
            trust_module.PROBLEM_NOT_OWN_LEASE,
        )
        self.assertFileUnchanged("for another workflow's lease")

    def test_subdirectory_of_our_own_lease_is_refused(self):
        nested = os.path.join(self.lease, "src")
        os.makedirs(nested)
        self.assertRefused(
            self.establish(path=nested),
            trust_module.PROBLEM_NOT_OWN_LEASE,
        )
        self.assertFileUnchanged("for a subdirectory of the lease")

    def test_symlink_inside_the_root_resolving_outside_is_refused(self):
        outside = os.path.join(self.base, "elsewhere")
        os.makedirs(outside)
        link = workspace_module.lease_path(self.workspaces, "wf-link")
        os.symlink(outside, link)
        # Recorded under this workflow's own id, but the symlink for
        # THIS id resolves out of the root.
        shutil.rmtree(self.lease)
        os.symlink(outside, self.lease)
        self.assertRefused(
            self.establish(),
            trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT,
        )
        self.assertFileUnchanged("for a symlinked lease")

    def test_traversing_recorded_path_is_refused(self):
        traversal = os.path.join(
            self.workspaces, self.workflow_id, "..", "..", "escape"
        )
        os.makedirs(os.path.join(self.base, "escape"))
        self.assertRefused(
            self.establish(path=traversal),
            trust_module.PROBLEM_OUTSIDE_MANAGED_ROOT,
        )
        self.assertFileUnchanged("for a traversing path")

    def test_not_yet_existing_lease_is_refused(self):
        shutil.rmtree(self.lease)
        self.assertRefused(
            self.establish(),
            trust_module.PROBLEM_TARGET_NOT_DIRECTORY,
        )
        self.assertFileUnchanged("for a missing lease directory")

    def test_non_directory_lease_is_refused(self):
        shutil.rmtree(self.lease)
        with open(self.lease, "w", encoding="utf-8") as handle:
            handle.write("not a directory")
        self.assertRefused(
            self.establish(),
            trust_module.PROBLEM_TARGET_NOT_DIRECTORY,
        )
        self.assertFileUnchanged("for a non-directory lease")

    def test_missing_or_malformed_lease_is_refused(self):
        self.assertRefused(
            self.establish(lease=False),
            trust_module.PROBLEM_LEASE_MISSING,
        )
        self.assertFileUnchanged("for a record with no lease")
        record = self.entry()
        record["workspace_lease"]["path_realpath"] = ""
        self.assertRefused(
            trust_module.establish(
                record, self.workspaces, self.config
            ),
            trust_module.PROBLEM_LEASE_MISSING,
        )
        self.assertFileUnchanged("for an empty lease path")


class ConfigShapeRefusalTests(TrustFixture):
    """P5: a config DI does not fully account for is not rewritten."""

    def test_missing_config_is_refused_and_never_created(self):
        os.unlink(self.config)
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_CONFIG_MISSING
        )
        self.assertFalse(
            os.path.exists(self.config),
            "DI created a configuration file that did not exist",
        )

    def test_unreadable_config_is_refused_with_no_write(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses mode bits")
        os.chmod(self.config, 0o000)
        self.addCleanup(os.chmod, self.config, 0o600)
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_CONFIG_UNREADABLE
        )
        os.chmod(self.config, 0o600)
        self.assertFileUnchanged("for an unreadable config")

    def test_corrupt_json_is_refused_and_never_repaired(self):
        with open(self.config, "wb") as handle:
            handle.write(b'{"projects": {"a": ')
        self.before = read_bytes(self.config)
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_CONFIG_UNPARSABLE
        )
        self.assertFileUnchanged("for unparsable JSON")

    def test_non_object_root_is_refused(self):
        for payload in (b"[]", b'"a string"', b"42", b"null"):
            with self.subTest(payload=payload):
                with open(self.config, "wb") as handle:
                    handle.write(payload)
                self.before = read_bytes(self.config)
                self.assertRefused(
                    self.establish(),
                    trust_module.PROBLEM_CONFIG_NOT_OBJECT,
                )
                self.assertFileUnchanged("for a non-object root")

    def test_projects_missing_is_refused_and_never_created(self):
        document = dict(GLOBAL_KEYS)
        self.before = write_config(self.config, document)
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_PROJECTS_MISSING
        )
        self.assertFileUnchanged("for a config with no projects")

    def test_projects_not_an_object_is_refused(self):
        for value in ([], "x", 3, None):
            with self.subTest(value=value):
                document = dict(GLOBAL_KEYS)
                document["projects"] = value
                self.before = write_config(self.config, document)
                self.assertRefused(
                    self.establish(),
                    trust_module.PROBLEM_PROJECTS_NOT_OBJECT,
                )
                self.assertFileUnchanged("for projects not an object")

    def test_existing_entry_not_an_object_is_never_replaced(self):
        projects = dict(OTHER_PROJECTS)
        projects[trust_module.trust_key(self.lease)] = "not an object"
        self.before = write_config(
            self.config, config_document(projects)
        )
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_ENTRY_NOT_OBJECT
        )
        self.assertFileUnchanged("for a non-object project entry")


class MinimalWriteTests(TrustFixture):
    """P3: exactly one project entry, exactly the trust key.

    The proof is a WHOLE-FILE byte reconstruction: strip the one
    entry DI is allowed to have touched out of the resulting file and
    re-serialize; the bytes must equal the original file EXACTLY.
    Within that comparison a further change — a reordered key, a
    reformatted number, a touched sibling entry, an added global key
    — breaks it.
    """

    def establish_ok(self):
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        return read_bytes(self.config)

    def test_write_happened_and_is_the_only_difference(self):
        after = self.establish_ok()
        self.assertNotEqual(after, self.before, "nothing was written")
        document = json.loads(after.decode("utf-8"))
        key = trust_module.trust_key(self.lease)
        self.assertEqual(document["projects"][key],
                         {trust_module.TRUST_KEY: True})
        del document["projects"][key]
        rebuilt = json.dumps(
            document, indent=trust_module.JSON_INDENT,
            ensure_ascii=trust_module.JSON_ENSURE_ASCII,
        ).encode("utf-8")
        self.assertEqual(
            rebuilt, self.before,
            "removing DI's own entry did not restore the original"
            " bytes: something else in the file changed",
        )

    def test_every_other_project_entry_is_byte_identical(self):
        after = json.loads(self.establish_ok().decode("utf-8"))
        original = json.loads(self.before.decode("utf-8"))
        self.assertTrue(original["projects"], "vacuous fixture")
        for name, value in original["projects"].items():
            with self.subTest(project=name):
                self.assertEqual(
                    json.dumps(value, sort_keys=True).encode("utf-8"),
                    json.dumps(after["projects"][name],
                               sort_keys=True).encode("utf-8"),
                )

    def test_no_top_level_global_key_is_added_changed_or_removed(self):
        after = json.loads(self.establish_ok().decode("utf-8"))
        original = json.loads(self.before.decode("utf-8"))
        self.assertEqual(
            sorted(original), sorted(after),
            "the set of top-level keys changed",
        )
        for name, value in original.items():
            if name == "projects":
                continue
            with self.subTest(key=name):
                self.assertEqual(
                    json.dumps(value, sort_keys=True),
                    json.dumps(after[name], sort_keys=True),
                )

    def test_no_permission_surface_is_widened_anywhere(self):
        after = json.loads(self.establish_ok().decode("utf-8"))
        original = json.loads(self.before.decode("utf-8"))
        key = trust_module.trust_key(self.lease)
        self.assertNotIn(
            "allowedTools", after["projects"][key],
            "DI added an allowedTools surface to its own entry",
        )
        for name, value in after["projects"].items():
            if name == key:
                continue
            with self.subTest(project=name):
                self.assertEqual(
                    value.get("allowedTools"),
                    original["projects"][name].get("allowedTools"),
                )

    def test_file_mode_is_preserved(self):
        self.establish_ok()
        self.assertEqual(
            stat.S_IMODE(os.stat(self.config).st_mode), 0o600
        )

    def test_existing_entry_keeps_its_siblings_and_flips_one_key(self):
        key = trust_module.trust_key(self.lease)
        projects = dict(OTHER_PROJECTS)
        projects[key] = {
            "allowedTools": ["Bash(ls)"],
            "hasTrustDialogAccepted": False,
            "lastCost": 1.5,
        }
        self.before = write_config(
            self.config, config_document(projects)
        )
        after = json.loads(self.establish_ok().decode("utf-8"))
        self.assertEqual(after["projects"][key], {
            "allowedTools": ["Bash(ls)"],
            "hasTrustDialogAccepted": True,
            "lastCost": 1.5,
        })

    def test_an_existing_entry_with_the_key_OMITTED_is_established(self):
        # The omitted-key case. An entry the CLI created for some
        # other reason can exist with no trust key at all; an
        # idempotence short-circuit written against the wrong
        # sentinel would return success here without having written to
        # the file,
        # and the workspace would still stop at the dialog. A test
        # that only ever supplies a well-typed False is green for the
        # wrong reason.
        key = trust_module.trust_key(self.lease)
        projects = dict(OTHER_PROJECTS)
        projects[key] = {"lastCost": 0.5}
        self.assertNotIn(
            trust_module.TRUST_KEY, projects[key],
            "the fixture must actually OMIT the key",
        )
        self.before = write_config(
            self.config, config_document(projects)
        )
        after = json.loads(self.establish_ok().decode("utf-8"))
        self.assertEqual(after["projects"][key], {
            "lastCost": 0.5,
            trust_module.TRUST_KEY: True,
        })
        self.assertTrue(
            trust_module.is_trusted(self.config, self.lease)
        )

    def test_reestablishment_on_a_trusted_entry_writes_nothing(self):
        self.establish_ok()
        settled = read_bytes(self.config)
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        self.assertEqual(
            read_bytes(self.config), settled,
            "a re-establishment rewrote an already-correct file",
        )


class ReadBackTests(TrustFixture):
    """P6: the success answer comes from a fresh read of the disk."""

    def test_a_write_that_does_not_land_is_a_refusal(self):
        key = trust_module.trust_key(self.lease)
        original = trust_module._atomic_write

        def losing_write(config_path, payload, mode):
            document = json.loads(payload.decode("utf-8"))
            document["projects"].pop(key, None)
            return original(
                config_path,
                json.dumps(document, indent=2,
                           ensure_ascii=False).encode("utf-8"),
                mode,
            )

        trust_module._atomic_write = losing_write
        self.addCleanup(
            setattr, trust_module, "_atomic_write", original
        )
        self.assertRefused(
            self.establish(),
            trust_module.PROBLEM_READBACK_MISMATCH,
        )

    def test_read_back_reads_the_file_not_the_memory(self):
        # Deleting the persistence must be observable. A pin that
        # asserted on the in-memory document would stay green here.
        original = trust_module._atomic_write
        trust_module._atomic_write = (
            lambda config_path, payload, mode: True
        )
        self.addCleanup(
            setattr, trust_module, "_atomic_write", original
        )
        self.assertRefused(
            self.establish(),
            trust_module.PROBLEM_READBACK_MISMATCH,
        )
        self.assertFileUnchanged("although the write was a no-op")


class AtomicityAndConcurrencyTests(TrustFixture):
    """P4: temp + rename in the same directory; a concurrent writer
    can neither observe nor persist a torn file."""

    def test_replace_is_used_from_the_same_directory_with_full_bytes(self):
        seen = {}
        real_replace = os.replace

        def recording_replace(src, dst):
            seen["src"] = src
            seen["dst"] = dst
            seen["payload"] = read_bytes(src)
            return real_replace(src, dst)

        os.replace = recording_replace
        self.addCleanup(setattr, os, "replace", real_replace)
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        self.assertEqual(
            os.path.dirname(seen["src"]), os.path.dirname(seen["dst"]),
            "the temp file is not on the same filesystem as the"
            " target, so the rename is not atomic",
        )
        # The bytes were COMPLETE before the rename, so within that
        # window a reader observes no partial document.
        document = json.loads(seen["payload"].decode("utf-8"))
        self.assertIs(
            document["projects"][
                trust_module.trust_key(self.lease)
            ][trust_module.TRUST_KEY],
            True,
        )

    def test_an_interrupted_write_leaves_the_original_intact(self):
        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError(errno.EIO, "simulated interruption")

        os.replace = failing_replace
        self.addCleanup(setattr, os, "replace", real_replace)
        self.assertRefused(
            self.establish(), trust_module.PROBLEM_WRITE_FAILED
        )
        self.assertFileUnchanged("by an interrupted write")
        leftovers = [
            name for name in os.listdir(self.base)
            if "di-trust-" in name
        ]
        self.assertEqual(leftovers, [], "a temp file was left behind")

    def test_a_lock_held_by_another_writer_refuses_without_writing(self):
        lock = self.config + trust_module.LOCK_SUFFIX
        os.mkdir(lock)
        self.addCleanup(os.rmdir, lock)
        calls = []
        self.assertRefused(
            trust_module.establish(
                self.entry(), self.workspaces, self.config,
                sleeper=lambda seconds: calls.append(seconds),
            ),
            trust_module.PROBLEM_CONFIG_LOCKED,
        )
        self.assertFileUnchanged("while another writer held the lock")
        self.assertTrue(calls, "the lock was not waited on at all")

    def test_the_lock_is_the_path_the_cli_itself_takes(self):
        # `${configPath}.lock`, created by mkdir — the protocol the
        # installed CLI runs under. A different path would give DI a
        # private lock that excludes nobody.
        observed = []
        real_mkdir = os.mkdir

        def recording_mkdir(path, *args, **kwargs):
            observed.append(path)
            return real_mkdir(path, *args, **kwargs)

        os.mkdir = recording_mkdir
        self.addCleanup(setattr, os, "mkdir", real_mkdir)
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        self.assertIn(self.config + ".lock", observed)

    def test_the_lock_is_released_on_success_and_on_refusal(self):
        lock = self.config + trust_module.LOCK_SUFFIX
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(lock), "lock leaked on success")
        with open(self.config, "wb") as handle:
            handle.write(b"{ not json")
        self.establish()
        self.assertFalse(os.path.exists(lock), "lock leaked on refusal")

    def test_a_stale_lock_is_reclaimed_exactly_once(self):
        lock = self.config + trust_module.LOCK_SUFFIX
        os.mkdir(lock)
        stale = time.time() - (trust_module.LOCK_STALE_SECONDS + 60)
        os.utime(lock, (stale, stale))
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        self.assertFalse(os.path.exists(lock))

    def test_a_held_lock_is_never_refreshed(self):
        """EXECUTED PIN: holds a real lock and observes its mtime.

        A heartbeat would make a crashed DI process wedge each
        Claude session on the machine, because the CLI's staleness
        rule could not reclaim the lock. This is the guarantee;
        `test_di_never_heartbeats_its_lock` is the fast structural
        feedback in front of it.
        """
        lock = trust_module.resolve_config_path(
            self.config
        ) + trust_module.LOCK_SUFFIX
        acquired, problem, detail = trust_module._acquire_lock(lock)
        self.assertTrue(acquired, "%s: %s" % (problem, detail))
        try:
            before = os.stat(lock).st_mtime_ns
            deadline = time.time() + 1.0
            while time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(
                os.stat(lock).st_mtime_ns, before,
                "the lock's mtime was refreshed while DI held it; a"
                " heartbeating lock is not reclaimable as stale",
            )
        finally:
            trust_module._release_lock(lock)

    def test_di_never_heartbeats_its_lock(self):
        """Fast structural feedback in front of
        `test_a_held_lock_is_never_refreshed`, which is the executed
        guarantee. This scan only notices the obvious construction."""
        source = inspect.getsource(trust_module)
        self.assertNotIn(
            "os.utime", source,
            "workspace_trust refreshes a lock mtime somewhere; a"
            " heartbeating DI lock does not be reclaimed as stale",
        )


class SymlinkedConfigTests(TrustFixture):
    """Round-01 B3. A symlinked ~/.claude.json (a dotfiles repository
    checkout — a common setup) must keep its identity."""

    def setUp(self):
        super(SymlinkedConfigTests, self).setUp()
        # Move the real config behind a symlink, the way a dotfiles
        # setup does.
        self.target = os.path.join(self.base, "dotfiles", "claude.json")
        os.makedirs(os.path.dirname(self.target))
        shutil.move(self.config, self.target)
        os.symlink(self.target, self.config)
        self.before = read_bytes(self.target)

    def test_the_symlink_is_preserved_and_the_target_is_written(self):
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        self.assertTrue(
            os.path.islink(self.config),
            "the symlink was REPLACED by a regular file: the user's"
            " real configuration has been detached and left stale",
        )
        document = json.loads(
            read_bytes(self.target).decode("utf-8")
        )
        self.assertEqual(
            document["projects"][trust_module.trust_key(self.lease)],
            {trust_module.TRUST_KEY: True},
            "the trust key did not land in the file the symlink"
            " actually points at",
        )

    def test_the_write_is_still_minimal_through_the_symlink(self):
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        after = read_bytes(self.target)
        document = json.loads(after.decode("utf-8"))
        del document["projects"][trust_module.trust_key(self.lease)]
        self.assertEqual(
            json.dumps(document, indent=trust_module.JSON_INDENT,
                       ensure_ascii=trust_module.JSON_ENSURE_ASCII
                       ).encode("utf-8"),
            self.before,
        )

    def test_the_lock_is_taken_on_the_resolved_path(self):
        # The CLI locks with realpath:true. Locking the LINK path
        # would take a different lock from the CLI's and exclude
        # nobody — in exactly the case where the lock matters.
        observed = []
        real_mkdir = os.mkdir

        def recording_mkdir(path, *args, **kwargs):
            observed.append(path)
            return real_mkdir(path, *args, **kwargs)

        os.mkdir = recording_mkdir
        self.addCleanup(setattr, os, "mkdir", real_mkdir)
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        self.assertIn(self.target + trust_module.LOCK_SUFFIX, observed)
        self.assertNotIn(
            self.config + trust_module.LOCK_SUFFIX, observed,
            "DI locked the unresolved link path; the CLI locks the"
            " resolved path, so the two would not exclude each other",
        )

    def test_readback_is_not_a_false_green_through_a_symlink(self):
        # Before the fix, establish() replaced the link with a regular
        # file and the read-back re-read THAT file, returning success
        # for a write that had destroyed the config's identity.
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        self.assertTrue(
            trust_module.is_trusted(self.target, self.lease),
            "the read-back passed but the resolved file does not"
            " record trust",
        )


class LockDiagnosticTests(TrustFixture):
    """Round-01 C-2: a lock that does not be created is not contention."""

    def test_an_unusable_lock_directory_is_not_reported_as_contention(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses mode bits")
        os.chmod(self.base, 0o500)
        self.addCleanup(os.chmod, self.base, 0o700)
        ok, problem, detail = self.establish()
        self.assertFalse(ok)
        self.assertEqual(
            problem, trust_module.PROBLEM_LOCK_UNUSABLE,
            "a permission failure was reported as %r, which sends an"
            " operator hunting a competing writer that does not"
            " exist" % problem,
        )
        self.assertNotIn("another writer holds", detail)
        self.assertIn("EACCES", detail)

    def test_real_contention_still_reports_contention(self):
        lock = trust_module.resolve_config_path(
            self.config
        ) + trust_module.LOCK_SUFFIX
        os.mkdir(lock)
        self.addCleanup(os.rmdir, lock)
        ok, problem, _ = trust_module.establish(
            self.entry(), self.workspaces, self.config,
            sleeper=lambda seconds: None,
        )
        self.assertFalse(ok)
        self.assertEqual(problem, trust_module.PROBLEM_CONFIG_LOCKED)

    def test_the_reclaim_window_is_disclosed(self):
        """Source is the only feasible level here, and the reason is
        that the PROPERTY IS ITSELF TEXTUAL: C-3 is a disclosed
        residual, not a behaviour, so what must not regress is the
        disclosure's presence where a maintainer will meet it. There
        is no behaviour to execute — the window is deliberately left
        open."""
        source = inspect.getdoc(trust_module._acquire_lock)
        flat_source = flat(source)
        self.assertIn("c-3", flat_source)
        self.assertIn("it is not closed here", flat_source)


class PointOfUseTrustTests(runtime_harness.RuntimeCase):
    """Round-01 C-1 and H4: trust is re-verified against the config
    the CHILD will read, immediately before the spawn."""

    def disk_entry(self, workflow_id="wf-0001"):
        return self.fresh_workflows()["workflows"][workflow_id]

    def validated_workflow(self):
        self.put_record(self.authorized_record())
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            outcome = self.perform("wf-0001", action, 2)
            self.assertTrue(outcome.ok, outcome.problem)

    def test_dispatch_refuses_when_the_child_reads_another_config(self):
        # The --config production defect: DI wrote the trust key into
        # a file the launched Herdr not reads. Before this check,
        # dispatch SUCCEEDED and the Herdr stopped at the dialog.
        self.validated_workflow()
        elsewhere = os.path.join(self.base, "elsewhere")
        os.makedirs(elsewhere)
        os.environ["HOME"] = elsewhere
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem,
            trust_module.PROBLEM_CONFIG_NOT_CONSUMED,
        )
        self.assertEqual(
            len(self.spawn_requests), spawns_before,
            "a Herdr was started against a configuration that could"
            " not have consumed the establishment",
        )
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")

    def test_dispatch_refuses_when_trust_was_dropped_after_establishment(self):
        # H4: a concurrent CLI writer drops DI's entry between
        # establishment and dispatch. Without the point-of-use check
        # this FAILS OPEN — the exact failure I1 exists to prevent.
        self.validated_workflow()
        document = json.loads(
            read_bytes(self.claude_config).decode("utf-8")
        )
        lease = self.disk_entry()["workspace_lease"]["path_realpath"]
        del document["projects"][trust_module.trust_key(lease)]
        write_config(self.claude_config, document)
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem, trust_module.PROBLEM_TRUST_NOT_PRESENT
        )
        self.assertEqual(len(self.spawn_requests), spawns_before)
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")

    def test_the_refusal_is_durable_and_carries_a_reason(self):
        self.validated_workflow()
        os.environ["HOME"] = self.base + "-nowhere"
        self.perform("wf-0001", broker_module.ACTION_DISPATCH, 2)
        summaries = [
            r["bounded_summary"]
            for r in self.disk_entry()["receipts"]
            if r["bounded_summary"].startswith(
                trust_module.TRUST_BLOCK_RECEIPT_MARKER
            )
        ]
        self.assertEqual(len(summaries), 1, summaries)
        self.assertIn(
            trust_module.PROBLEM_CONFIG_NOT_CONSUMED, summaries[0]
        )

    def test_the_happy_path_still_dispatches(self):
        # Anti-vacuity: the check must not refuse everything.
        self.validated_workflow()
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        self.assertEqual(len(self.spawn_requests), spawns_before + 1)


class BrokerOrderingTests(runtime_harness.RuntimeCase):
    """P7/P8, EXECUTED: establishment sits at the ordering-enforced
    seam, and a failure is DURABLE — each assertion below reads the
    workflow state back FROM DISK, not the in-memory record."""

    def corrupt_the_config(self):
        with open(self.claude_config, "wb") as handle:
            handle.write(b"{ this is not json")

    def disk_entry(self, workflow_id="wf-0001"):
        return self.fresh_workflows()["workflows"][workflow_id]

    def test_a_trust_failure_blocks_the_workflow_durably(self):
        self.put_record(self.authorized_record())
        self.corrupt_the_config()
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem,
            trust_module.PROBLEM_CONFIG_UNPARSABLE,
        )
        entry = self.disk_entry()
        self.assertEqual(entry["phase"], "BLOCKED")

    def test_the_durable_block_carries_an_actionable_reason(self):
        self.put_record(self.authorized_record())
        self.corrupt_the_config()
        self.perform("wf-0001", broker_module.ACTION_MATERIALIZE, 2)
        summaries = [
            receipt["bounded_summary"]
            for receipt in self.disk_entry()["receipts"]
        ]
        matching = [
            summary for summary in summaries
            if summary.startswith(
                trust_module.TRUST_BLOCK_RECEIPT_MARKER
            )
        ]
        self.assertEqual(len(matching), 1, summaries)
        self.assertIn(
            trust_module.PROBLEM_CONFIG_UNPARSABLE, matching[0]
        )
        # Capability-free and path-free (E-5): the reason names the
        # problem rather than the workspace.
        self.assertNotIn(self.workspaces, matching[0])

    def test_a_blocked_workflow_can_never_reach_dispatch(self):
        self.put_record(self.authorized_record())
        self.corrupt_the_config()
        self.perform("wf-0001", broker_module.ACTION_MATERIALIZE, 2)
        spawns_before = len(self.spawn_requests)
        for action in (
            broker_module.ACTION_PREPARE,
            broker_module.ACTION_VALIDATE_HANDOFF,
            broker_module.ACTION_DISPATCH,
        ):
            outcome = self.perform("wf-0001", action, 2)
            self.assertFalse(
                outcome.ok, "%s advanced a BLOCKED workflow" % action
            )
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")
        self.assertEqual(
            len(self.spawn_requests), spawns_before,
            "a Herdr was started for a workflow whose trust was not"
            " established",
        )

    def test_a_trust_failure_is_never_silently_retried(self):
        self.put_record(self.authorized_record())
        self.corrupt_the_config()
        self.perform("wf-0001", broker_module.ACTION_MATERIALIZE, 2)
        # Repair the config and try the SAME action again: BLOCKED is
        # terminal, so recovery is a human decision, not an
        # automatic second attempt.
        write_config(self.claude_config, config_document())
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")

    def test_the_happy_path_trusts_exactly_the_leased_workspace(self):
        self.put_record(self.authorized_record())
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        entry = self.disk_entry()
        self.assertEqual(entry["phase"], "WORKSPACE_READY")
        lease = entry["workspace_lease"]["path_realpath"]
        self.assertTrue(
            trust_module.is_trusted(self.claude_config, lease)
        )
        document = json.loads(
            read_bytes(self.claude_config).decode("utf-8")
        )
        added = set(document["projects"]) - {
            "/Users/someone/other-repo", "/Users/someone/untrusted",
        }
        self.assertEqual(
            added, {trust_module.trust_key(lease)},
            "the Broker wrote a project entry that is not the lease",
        )
        # The pre-existing entries the fixture contains survive
        # untouched, including the explicitly UNTRUSTED one.
        self.assertEqual(
            document["projects"]["/Users/someone/other-repo"],
            {"allowedTools": ["Bash(git status)"],
             "hasTrustDialogAccepted": True},
        )
        self.assertEqual(
            document["projects"]["/Users/someone/untrusted"],
            {"hasTrustDialogAccepted": False},
            "DI trusted a project the user had explicitly refused",
        )

    def test_the_forwarded_config_path_changes_the_outcome(self):
        # Seam clause (d): the value the Broker forwards must REACH
        # its destination and CHANGE an outcome. Two runs differing
        # one in the forwarded config path end in different durable
        # phases, asserted on state re-read from disk.
        self.put_record(self.authorized_record())
        healthy = self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        )
        self.assertTrue(healthy.ok)
        good_phase = self.disk_entry()["phase"]

        self.setUp()
        self.put_record(self.authorized_record())
        self.broker.claude_config_path = os.path.join(
            self.base, "no-such-config.json"
        )
        broken = self.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2
        )
        self.assertFalse(broken.ok)
        self.assertEqual(
            broken.problem, trust_module.PROBLEM_CONFIG_MISSING
        )
        self.assertNotEqual(self.disk_entry()["phase"], good_phase)
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")


class BrokerSourceOrderTests(unittest.TestCase):
    """Fast structural feedback in front of `BrokerOrderingTests`
    and the executed byte comparisons in this module, which are the
    load-bearing guarantees. This class reads SOURCE, so it does not
    say what the program does to a file or to a workflow record; it
    exists only to fail fast if the call is moved out of its
    ordering window."""

    def test_establishment_sits_between_materialize_and_ready(self):
        source = inspect.getsource(
            broker_module.TargetBroker._materialize
        )
        trust_at = source.find("workspace_trust_module.establish")
        ready_at = source.find("PHASE_WORKSPACE_READY")
        materialize_at = source.find("workspace_module.materialize")
        self.assertNotEqual(trust_at, -1)
        self.assertLess(materialize_at, trust_at)
        self.assertLess(trust_at, ready_at)


class SeamPinTests(unittest.TestCase):
    """The call surface between the Broker and workspace_trust,
    pinned BOTH ways and by execution."""

    def caller_keywords(self):
        tree = ast.parse(inspect.getsource(broker_module))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "establish":
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if func.value.id != "workspace_trust_module":
                continue
            found.append((
                len(node.args),
                sorted(k.arg for k in node.keywords),
            ))
        return found

    def test_the_caller_site_exists_and_is_singular(self):
        self.assertEqual(len(self.caller_keywords()), 1)

    def test_production_accepts_exactly_what_the_caller_passes(self):
        (positional, keywords), = self.caller_keywords()
        signature = inspect.signature(trust_module.establish)
        names = list(signature.parameters)
        self.assertLessEqual(positional, len(names))
        for keyword in keywords:
            self.assertIn(keyword, names)
        # Executed proof, not signature reading: the call the Broker
        # actually makes must bind.
        signature.bind(*range(positional),
                       **{k: None for k in keywords})

    def test_no_parameter_of_establish_is_undeclared(self):
        # The COMPLETE derived parameter list, no filter: adding a
        # silently-defaulted parameter fails this row, which is how a
        # future accepted-and-dropped keyword is caught.
        self.assertEqual(
            list(inspect.signature(trust_module.establish).parameters),
            ["entry", "workspaces_root", "config_path", "sleeper",
             "clock"],
        )


class RealDependencyContractTests(unittest.TestCase):
    """POPULATION B — the ONE read-only, in-process structural
    contract test (supervisor ruling R-5).

    Strategy §1 rule 6 requires a contract test that pins the real
    dependency's own field paths and value domains, because a
    hand-enumerated domain re-encodes the blind spot one layer down.
    R-5 permits exactly this one class to READ the real file, subject
    to four conditions this class is written to satisfy:

    Within this class: B-i, it launches no subprocess, no pty and no
    CLI.
    B-ii.  Within this class the real file is not copied to disk and
           not re-serialized. Each read is into memory. Where an
           on-disk fixture is genuinely needed (``establish`` needs a
           real path), the fixture is a SYNTHETIC document whose
           SHAPE is derived from the real file's structure — entry
           count, key paths, value types — and whose keys and values
           are synthetic. No content from the real file is persisted.
    B-iii. It asserts on STRUCTURE one: key paths and value
           types/domains. It not asserts on, logs, echoes, or
           persists a credential-bearing value.
    B-iv.  P3's byte-identical obligation is met by hashing the real
           file IN PLACE, before and after. That needs no copy.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(REAL_CONFIG):
            raise unittest.SkipTest(
                "no real ~/.claude.json on this machine; the real"
                " dependency contract is UNVERIFIED here"
            )

    def setUp(self):
        self.real_before = sha256_file(REAL_CONFIG)      # B-iv
        self.addCleanup(self.assert_real_file_untouched)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)

    def assert_real_file_untouched(self):
        self.assertEqual(
            sha256_file(REAL_CONFIG), self.real_before,
            "THE REAL ~/.claude.json CHANGED DURING A TEST",
        )

    def real_document(self):
        """The real file, IN MEMORY; within this class it is not
        written to disk."""
        with open(REAL_CONFIG, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def derived_shape(self):
        """The STRUCTURE of the real dependency: the number of project
        entries, the key paths the consumer reads, and the value types
        found at them. Values themselves do not leave this method."""
        document = self.real_document()
        projects = document.get(trust_module.PROJECTS_KEY)
        return {
            "root_type": type(document).__name__,
            "top_level_key_count": len(document),
            "projects_type": type(projects).__name__,
            "entry_count": len(projects) if isinstance(
                projects, dict
            ) else 0,
            "entry_types": sorted({
                type(entry).__name__
                for entry in (projects or {}).values()
            }) if isinstance(projects, dict) else [],
            "trust_value_types": sorted({
                type(entry.get(trust_module.TRUST_KEY)).__name__
                for entry in (projects or {}).values()
                if isinstance(entry, dict)
            }) if isinstance(projects, dict) else [],
            "entries_carrying_the_trust_key": sum(
                1 for entry in (projects or {}).values()
                if isinstance(entry, dict)
                and trust_module.TRUST_KEY in entry
            ) if isinstance(projects, dict) else 0,
        }

    def test_the_real_field_paths_and_value_domains(self):
        # (a) Derived from the DEPENDENCY'S OWN artifact, in memory,
        # rather than from a hand-enumerated list — and structure
        # only.
        shape = self.derived_shape()
        self.assertEqual(shape["root_type"], "dict")
        self.assertEqual(shape["projects_type"], "dict")
        self.assertGreater(
            shape["entry_count"], 1,
            "vacuous: the real file has no other entries to protect",
        )
        self.assertEqual(shape["entry_types"], ["dict"])
        self.assertEqual(
            shape["trust_value_types"], ["bool"],
            "the real file's %s domain is %s; the consumer's strict"
            " `is True` reading assumes booleans"
            % (trust_module.TRUST_KEY, shape["trust_value_types"]),
        )
        self.assertEqual(
            shape["entries_carrying_the_trust_key"],
            shape["entry_count"],
            "not every real project entry carries the trust key",
        )

    def test_the_serializer_round_trips_the_real_bytes_in_memory(self):
        # Bytes in, bytes out, held in memory for the length of this
        # comparison. Without this, a
        # one-key write would silently reformat the whole file.
        with open(REAL_CONFIG, "rb") as handle:
            original = handle.read()
        rebuilt = json.dumps(
            json.loads(original.decode("utf-8")),
            indent=trust_module.JSON_INDENT,
            ensure_ascii=trust_module.JSON_ENSURE_ASCII,
        ).encode("utf-8")
        self.assertEqual(
            len(rebuilt), len(original),
            "DI's serializer does not reproduce the CLI's own byte"
            " length; a write would reformat the whole file",
        )
        self.assertTrue(
            rebuilt == original,
            "DI's serializer does not reproduce the CLI's own bytes;"
            " a write would reformat the whole file",
        )

    def structure_matched_fixture(self, path):
        """A SYNTHETIC config whose SHAPE matches the real one.

        Same root type, same number of project entries, same value
        types at the key paths the consumer reads. Each key string
        and each value is synthetic — no content from the real file is
        written to disk (B-ii).
        """
        shape = self.derived_shape()
        projects = {}
        for index in range(shape["entry_count"]):
            projects["/synthetic/project-%03d" % index] = {
                trust_module.TRUST_KEY: bool(index % 2),
                "allowedTools": [],
            }
        document = {}
        for index in range(shape["top_level_key_count"]):
            document["syntheticGlobalKey%03d" % index] = index
        document[trust_module.PROJECTS_KEY] = projects
        write_config(path, document)
        return document

    def test_minimal_write_against_the_real_structure(self):
        config = os.path.join(self.base, ".claude.json")
        original = self.structure_matched_fixture(config)
        self.assertGreater(
            len(original[trust_module.PROJECTS_KEY]), 1,
            "vacuous: no other entries to protect",
        )
        before = read_bytes(config)
        workspaces = os.path.join(self.base, "workspaces")
        lease = workspace_module.lease_path(workspaces, "wf-contract")
        os.makedirs(lease)
        ok, problem, detail = trust_module.establish(
            {
                "workflow_id": "wf-contract",
                "workspace_lease": {
                    "lease_id": "lease-contract",
                    "path_realpath": lease,
                    "acquired_at": "2026-08-28T00:00:00Z",
                    "released_at": None,
                },
            },
            workspaces, config,
        )
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        after = read_bytes(config)
        self.assertNotEqual(after, before)
        document = json.loads(after.decode("utf-8"))
        key = trust_module.trust_key(lease)
        self.assertEqual(document[trust_module.PROJECTS_KEY][key],
                         {trust_module.TRUST_KEY: True})
        del document[trust_module.PROJECTS_KEY][key]
        self.assertEqual(
            json.dumps(document, indent=trust_module.JSON_INDENT,
                       ensure_ascii=trust_module.JSON_ENSURE_ASCII
                       ).encode("utf-8"),
            before,
            "against a structure matched to the REAL file, DI's write"
            " changed something other than its own entry",
        )
        self.assertTrue(trust_module.is_trusted(config, lease))

    def test_a_double_cannot_express_a_shape_the_real_file_never_has(self):
        # The consumer reads `is True`. Each non-boolean shape a
        # hand-written double might invent must read as NOT trusted.
        # Built from {} — no real content involved.
        config = os.path.join(self.base, "synthetic.json")
        workspaces = os.path.join(self.base, "ws2")
        lease = workspace_module.lease_path(workspaces, "wf-shape")
        os.makedirs(lease)
        for impossible in ("true", 1, [True], {"value": True}, None):
            with self.subTest(value=impossible):
                write_config(config, {
                    "hasCompletedOnboarding": True,
                    trust_module.PROJECTS_KEY: {
                        trust_module.trust_key(lease): {
                            trust_module.TRUST_KEY: impossible,
                        },
                    },
                })
                self.assertFalse(
                    trust_module.is_trusted(config, lease),
                    "a shape the real dependency not produces read"
                    " as trusted",
                )


class PopulationBoundaryTests(unittest.TestCase):
    """Fast structural feedback in front of
    `ZZMustHaveRunTests.test_no_launching_class_opened_the_real_configuration`,
    which is the executed guarantee for this property.

    This class reads SOURCE. It detects lexical references to the
    NAME `REAL_CONFIG` inside a class body — NOT reachability — so a
    module-level helper, an alias, or a literal
    `os.path.expanduser("~/.claude.json")` all walk straight past it.
    reviewer1 demonstrated exactly that in round 02. It is kept
    because it fails fast and names the offending class, but it is
    NOT the guarantee and must not again be described as one.
    """

    #: Population B, by name. Within this module, a class outside
    #: this tuple that touches REAL_CONFIG is a violation.
    POPULATION_B = ("RealDependencyContractTests",)

    def module_source(self):
        return inspect.getsource(sys.modules[__name__])

    def classes_referencing_the_real_config(self):
        tree = ast.parse(self.module_source())
        owners = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and (
                    inner.id == "REAL_CONFIG"
                ):
                    owners.add(node.name)
        return owners

    def test_only_population_B_references_the_real_configuration(self):
        self.assertEqual(
            self.classes_referencing_the_real_config(),
            set(self.POPULATION_B),
            "a class outside Population B references the real"
            " ~/.claude.json",
        )

    def test_no_launching_test_touches_the_real_configuration(self):
        # The load-bearing direction: each process-launching class
        # must be disjoint from the classes that touch the real file.
        tree = ast.parse(self.module_source())
        launching = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and (
                    inner.attr in ("fork", "execve")
                ):
                    launching.add(node.name)
                if isinstance(inner, ast.Name) and inner.id in (
                    "run_cli", "build_minimal_config"
                ):
                    launching.add(node.name)
                if isinstance(inner, ast.Attribute) and (
                    inner.attr in ("run_cli", "build_minimal_config")
                ):
                    launching.add(node.name)
        self.assertTrue(
            launching, "vacuous: no launching class was identified"
        )
        overlap = launching & self.classes_referencing_the_real_config()
        self.assertEqual(
            overlap, set(),
            "these classes both launch a process (or build a config a"
            " launched process reads) AND touch the real"
            " ~/.claude.json, which R-5 forbids absolutely: %s"
            % sorted(overlap),
        )

    def test_population_B_writes_no_copy_of_the_real_file(self):
        # B-ii, structurally: within this scan, the one permitted class
        # must not pass REAL_CONFIG to a writing call it names.
        tree = ast.parse(self.module_source())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name not in self.POPULATION_B:
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                name = getattr(func, "attr", getattr(func, "id", ""))
                if name in ("copyfile", "copy", "copy2", "move",
                            "write_config"):
                    for argument in inner.args:
                        self.assertNotEqual(
                            getattr(argument, "id", None),
                            "REAL_CONFIG",
                            "Population B copies the real file to"
                            " disk; R-5 B-ii forbids any on-disk copy",
                        )

    def test_population_B_launches_nothing(self):
        # B-i.
        tree = ast.parse(self.module_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (
                node.name in self.POPULATION_B
            ):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Attribute):
                        self.assertNotIn(
                            inner.attr,
                            ("fork", "execve", "Popen", "run_cli"),
                            "Population B launched a process",
                        )


CLI_CANDIDATES = (
    os.path.expanduser("~/.local/bin/claude"),
    shutil.which("claude") or "",
)

# Screen markers, each DERIVED by executing the CLI and observing
# which screens actually contain it (recorded in the I1 evidence).
# The interactive renderer emits cursor-column escapes BETWEEN words,
# so transcripts and markers are compared with ALL whitespace removed.
#
# `ClaudeCodev` was the original promptable marker and is a FALSE
# POSITIVE: the onboarding screen reads "Welcome to Claude Code
# v2.1.251" and contains it. The derivation table:
#
#   screen           GATE   WelcometoClaudeCode   ?forshortcuts
#   trust gate       yes    no                    no
#   onboarding       no     yes                   no
#   promptable REPL  no     no                    yes
GATE_PHRASE = "Quicksafetycheck:Isthisaprojectyoucreated"
ONBOARDING_PHRASE = "WelcometoClaudeCode"
PROMPTABLE_PHRASE = "?forshortcuts"

# THE WHITELIST (supervisor ruling R-1). The injected configuration is
# built up from an EMPTY object.
#
# POPULATION A (supervisor ruling R-5): a test that launches a
# process, or that builds a HOME or config a launched process reads,
# does not read, copy, diff against, or derive from the real
# ~/.claude.json. Scope: pinned behaviourally by
# `ZZMustHaveRunTests.test_no_launching_class_opened_the_real_configuration`,
# which observes in-process `open` events; outside it, and disclosed,
# lie reads made by a subprocess this suite launches.
# Blacklisting keys off a real copy would be correct only for the keys
# someone remembered and would re-widen silently the moment the CLI
# adds one.
#
# POPULATION B is exactly one class — `RealDependencyContractTests` —
# which READS the real file IN MEMORY only. Within that class it
# launches no process, writes no copy and no re-serialization of the
# real file, asserts on structure only, and satisfies the
# byte-identical obligation by hashing the real file IN PLACE.
#
# The set below is not hand-enumerated: it is the result of an
# EXECUTED DERIVATION, adding keys to {} one at a time until the
# CONTROL arm reproduced the trust gate. Both members are pinned by
# `MinimalConfigDerivationTests` below.
#
#   {}                                    -> onboarding
#   {"projects": {}}                      -> onboarding
#   {"hasCompletedOnboarding": True}      -> TRUST GATE
#   {"projects": {}, "hasCompletedOnboarding": True} -> TRUST GATE
#
# `hasCompletedOnboarding` is REQUIRED for the gate to be reached at
# all. `projects` is NOT required by the gate; it is required by DI's
# OWN contract, because `establish` refuses a config with no projects
# object (`workspace_trust_config_projects_missing`). Both reasons are
# recorded so neither key looks like folklore.
MINIMAL_CONFIG_KEYS = ("hasCompletedOnboarding", "projects")

# Absent BY CONSTRUCTION rather than by removal: credential- and
# account-bearing keys, and the keys that would make the CLI reach
# out to an external service. Scope: the names listed here; a key
# outside the list is not caught by this constant. `build_minimal_config` asserts their
# absence, so reintroducing one dies by authored assertion.
FORBIDDEN_CONFIG_KEYS = (
    "oauthAccount",
    "mcpServers",
    "userID",
    "machineID",
    "customApiKeyResponses",
    "primaryApiKey",
)

# A belt, deliberately NOT load-bearing: with a config built from {}
# there is no MCP server to ignore in the first place.
# `test_the_belt_flag_is_not_load_bearing` proves the gate reproduces
# without it, which is what makes it a belt rather than the guarantee.
BELT_ARGV = ("--strict-mcp-config",)

# Client-side I/O bounds on THIS TEST'S OWN child process. These are
# not, and must not become, a bound on an engineering mission, on
# the suite, or on the hermetic-git sweep: they bound only how long
# this test waits on a pipe and on a reap it initiated.
CLI_READ_DEADLINE_SECONDS = 40
REAP_DEADLINE_SECONDS = 10


def build_minimal_config(path, projects=None, include_projects=True):
    """The injected configuration, BUILT UP FROM AN EMPTY OBJECT.

    Not derived from, copied from, or diffed against the user's real
    file. Returns the document written.
    """
    document = {}
    document["hasCompletedOnboarding"] = True
    if include_projects:
        document["projects"] = dict(projects or {})
    for forbidden in FORBIDDEN_CONFIG_KEYS:
        assert forbidden not in document, (
            "the injected configuration must not carry %r: it is a"
            " credential-, account- or external-service-bearing key"
            % forbidden
        )
    write_config(path, document)
    return document


# --- THE EXECUTED POPULATION-A GUARANTEE (round-02 B.2 / R-8 C-4) ---
#
# Watches actual `open` calls through an audit hook. This is the
# guarantee that no process-launching test touches the developer's
# real ~/.claude.json; `PopulationBoundaryTests` is the fast
# structural feedback in front of it. The hook needs no read of the
# file's CONTENTS — it only ever compares path strings, so arming it
# is not itself a Population A read.
_WATCHED_REAL_PATHS = frozenset(
    p for p in (REAL_CONFIG, os.path.realpath(REAL_CONFIG)) if p
)
_OPEN_WATCH = {"armed": 0, "events": [], "extra": set(), "seen": 0}


def _population_a_open_audit(event, args):
    # Deliberately allocation-free and O(1) on the hot path: no
    # realpath, no I/O, no formatting unless something matches.
    if event != "open" or not _OPEN_WATCH["armed"]:
        return
    target = args[0] if args else None
    if not isinstance(target, str):
        return
    _OPEN_WATCH["seen"] += 1
    if target in _WATCHED_REAL_PATHS or target in _OPEN_WATCH["extra"]:
        _OPEN_WATCH["events"].append(
            (target, args[1] if len(args) > 1 else None)
        )


sys.addaudithook(_population_a_open_audit)


def arm_population_a_watch():
    _OPEN_WATCH["armed"] += 1


def disarm_population_a_watch():
    if _OPEN_WATCH["armed"]:
        _OPEN_WATCH["armed"] -= 1


def unredirectable_home():
    """The user's home from the PASSWORD DATABASE, not `$HOME`.

    Round-02 B.1 attack B: `installed_cli` resolved
    `~/.local/bin/claude` through `HOME` and fell back to
    `shutil.which` through `PATH` — both ambient. Redirecting them on
    a machine that HAS the binary silently took the exemption and
    deleted the consumed-effect arms from a green run. The exemption
    predicate must not be steerable that way.
    """
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def cli_installed_unredirectably():
    """Is the CLI installed, judged from the password database?

    Scope: `HOME` and `PATH` do not steer this answer, which is the
    exact vector that defeated round 02's exemption, and it is the
    sole basis the must-have-run exemption may rely on. Outside that
    boundary, and disclosed: a passwd entry that itself points
    somewhere unexpected, and a binary installed under a different
    prefix, are both outside what this resolution can see.
    """
    candidate = os.path.join(
        unredirectable_home(), ".local", "bin", "claude"
    )
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def installed_cli():
    """The binary to actually run. Discovery here is deliberately
    WIDER than `cli_installed_unredirectably` (it also honours HOME
    and PATH, which is right for finding a binary) — but a widening
    is not a basis for an exemption, so the must-have-run pin
    judges absence with the narrow, unredirectable resolution."""
    unredirectable = cli_installed_unredirectably()
    if unredirectable is not None:
        return unredirectable
    for candidate in CLI_CANDIDATES:
        if candidate and os.path.isfile(candidate) and os.access(
            candidate, os.X_OK
        ):
            return candidate
    return None


def strip_terminal_control(raw):
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", raw)
    text = re.sub(r"\x1b[\[()][0-9;?<>=]*[A-Za-z]", "", text)
    text = re.sub(r"\x1b.", "", text)
    return re.sub(r"\s+", "", text)


def process_group_members(pgid):
    """Each live process in ``pgid``, excluding the `ps` we ran."""
    listing = subprocess.run(
        ["/bin/ps", "-eo", "pid,pgid,command"],
        capture_output=True, text=True,
    ).stdout
    members = []
    for line in listing.splitlines()[1:]:
        fields = line.split(None, 2)
        if len(fields) < 2 or fields[1] != str(pgid):
            continue
        if len(fields) > 2 and fields[2].startswith("/bin/ps"):
            continue
        members.append(line.strip())
    return members


def reap_process_group(leader_pid):
    """Kill and reap the ENTIRE group led by ``leader_pid``.

    Returns the list of survivors — empty when the tree is gone.

    SAFETY: ``pty.fork`` calls ``setsid`` in the child, so the child's
    process-group id IS its pid. That is VERIFIED before a signal is
    sent, because a group id read during the instant before ``setsid``
    lands is the CALLER'S OWN group — signalling it would kill the
    test runner and each sibling process in it. An unverified group is
    not signalled at all, and the survivors are reported rather than a
    foreign group being touched.
    """
    # R-14 E-3: the reaping DECISION is delegated to the single
    # production construct, `target_runtime.process_ownership`, so
    # this task's test surface has one reaper rather than a copy per
    # site. The local code below only reports survivors, which is what
    # this file's assertions read.
    from target_runtime import process_ownership as _proc
    _proc.reap_group(leader_pid, settle_seconds=REAP_DEADLINE_SECONDS)
    limit = time.time() + REAP_DEADLINE_SECONDS
    survivors = process_group_members(leader_pid)
    while survivors and time.time() < limit:
        time.sleep(0.1)
        survivors = process_group_members(leader_pid)
    return survivors


class ProcessTreeReapingTests(unittest.TestCase):
    """The reaping guarantee, pinned against a process tree that
    DEFINITELY has a survivor.

    The real-CLI arms below also assert zero survivors, but whether
    the CLI happens to leave one depends on the CLI. This class does
    not: it builds a leader with a grandchild that outlives it, so
    the difference between killing the leader and reaping the group
    is observable in each run of this fixture — a reaper that signals
    only the leader dies
    here by authored assertion.
    """

    def test_reaping_removes_a_grandchild_the_leader_left_behind(self):
        pid = os.fork()
        if pid == 0:                                   # pragma: no cover
            try:
                os.setsid()
                if os.fork() == 0:
                    time.sleep(120)                    # the survivor
                    os._exit(0)
                time.sleep(120)
            finally:
                os._exit(0)
        self.addCleanup(reap_process_group, pid)
        limit = time.time() + REAP_DEADLINE_SECONDS
        while time.time() < limit:
            if len(process_group_members(pid)) >= 2:
                break
            time.sleep(0.05)
        members = process_group_members(pid)
        self.assertGreaterEqual(
            len(members), 2,
            "the fixture not produced a leader AND a grandchild, so"
            " this test could not tell the two reapers apart: %s"
            % members,
        )
        survivors = reap_process_group(pid)
        self.assertEqual(
            survivors, [],
            "the process tree outlived the test: %s" % survivors,
        )

    def test_reaping_refuses_to_signal_an_unverified_group(self):
        # The catastrophic case this guard exists for. A child that
        # does NOT setsid stays in the RUNNER'S OWN process group, so
        # its "group" is the test runner and each sibling in it.
        # Signalling that group would kill the suite. The reaper must
        # signal the leader only.
        #
        # `os.kill` is patched as well as `os.killpg`: with a real
        # kill the child would die and the assertion could pass for
        # the wrong reason, with no process left to signal.
        pid = os.fork()
        if pid == 0:                                   # pragma: no cover
            try:
                time.sleep(60)
            finally:
                os._exit(0)
        self.addCleanup(self._force_kill, pid)
        self.assertNotEqual(
            os.getpgid(pid), pid,
            "the fixture child setsid'd; it does not model the"
            " unverified case",
        )
        groups, singles = [], []
        real_killpg, real_kill = os.killpg, os.kill

        def recording_kill(target, sig):
            singles.append(target)
            return real_kill(target, sig)

        os.killpg = lambda pgid, sig: groups.append(pgid)
        os.kill = recording_kill
        self.addCleanup(setattr, os, "kill", real_kill)
        self.addCleanup(setattr, os, "killpg", real_killpg)
        reap_process_group(pid)
        os.kill, os.killpg = real_kill, real_killpg
        self.assertEqual(
            groups, [],
            "reap_process_group signalled process group %s, which is"
            " the test runner's own group" % groups,
        )
        self.assertEqual(
            singles, [pid],
            "the leader itself should still have been signalled once",
        )

    @staticmethod
    def _force_kill(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass


#: Classes that launch the real CLI. Declared at import; populated
#: at run. `ZZMustHaveRunTests` compares the two.
CLI_CLASSES_DECLARED = (
    "MinimalConfigDerivationTests", "RealCliConsumedEffectTests",
)
CLI_CLASSES_THAT_RAN = set()
CLI_CLASSES_EXCLUDED_BY_SWEEP = set()
POPULATION_A_WATCH_ARMED_FOR = set()


class CliArmMixin(unittest.TestCase):
    """Launch the REAL CLI under a SYNTHETIC minimal HOME and reap the
    whole process tree it creates."""

    @classmethod
    def setUpClass(cls):
        # The ONE narrow, disclosed sweep exclusion (lead1 R5).
        # The hermetic-git sweep re-runs this module in a child
        # interpreter to observe its git invocations. These classes
        # execute exactly one git command (`git init`), which is not
        # identity-requiring, so re-running them there buys the sweep
        # ZERO identity coverage while launching the real CLI several
        # more times. The module itself stays in the sweep and its
        # identity-bearing git still runs there through the other
        # classes, so the sweep's module set, its per-module shim-log
        # check, and its floor are all unchanged.
        #
        # The signal is an IN-PROCESS marker the sweep runner sets
        # inside this very process (`hermetic_git.mark_sweep_child`).
        # It is deliberately NOT an environment variable, and not a
        # nonce checked against a file the environment names: both of
        # those are assertable by anyone who can set the environment,
        # which is how round-01 and round-02 were both bypassed. No
        # ambient environment can set a module attribute.
        if hermetic_git.sweep_child_active():
            CLI_CLASSES_EXCLUDED_BY_SWEEP.add(cls.__name__)
            raise unittest.SkipTest(
                "skipped inside the hermetic-git sweep child: these"
                " classes run no identity-requiring git, so they add"
                " real CLI launches and no sweep coverage (they run"
                " in full in the module's own suite leg)"
            )
        cls.cli = installed_cli()
        if cls.cli is None:
            raise unittest.SkipTest(
                "no claude CLI installed; the CONSUMED EFFECT of the"
                " trust key is UNVERIFIED on this machine"
            )
        CLI_CLASSES_THAT_RAN.add(cls.__name__)
        # Arm the executed Population A guarantee for the whole life
        # of this launching class.
        arm_population_a_watch()
        POPULATION_A_WATCH_ARMED_FOR.add(cls.__name__)

    def setUp(self):
        # POPULATION A (R-5): this class launches a real process, so
        # within it the real ~/.claude.json is not read, copied,
        # diffed against or derived from — not even hashed as a
        # safety net.
        # The guarantee is structural instead of runtime, and
        # stronger for it: HOME is injected, the configuration is
        # built up from {}, and
        # `PopulationBoundaryTests.test_no_launching_test_touches_the_
        # real_configuration` proves by AST that no launching class
        # can reach the real file at all.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)

    @classmethod
    def tearDownClass(cls):
        if cls.__name__ in POPULATION_A_WATCH_ARMED_FOR:
            disarm_population_a_watch()

    def run_cli(self, seed_trust, belt=True, include_projects=True,
                document_override=None):
        import pty
        import select

        label = "%s-%s-%s" % (seed_trust, belt, include_projects)
        home = os.path.join(self.base, "home-" + label)
        os.makedirs(home)
        config = os.path.join(home, ".claude.json")
        if document_override is not None:
            write_config(config, document_override)
        else:
            build_minimal_config(
                config, include_projects=include_projects
            )

        workspaces = os.path.join(self.base, "ws-" + label)
        os.makedirs(workspaces)
        lease = workspace_module.lease_path(workspaces, "wf-cli")
        os.makedirs(lease)
        # A materialized workspace IS its own git root.
        subprocess.run(
            ["git", "init", "-q", lease], check=True,
            env=dict(os.environ, HOME=home),
        )
        if seed_trust:
            entry = {
                "workflow_id": "wf-cli",
                "workspace_lease": {
                    "lease_id": "lease-cli",
                    "path_realpath": lease,
                    "acquired_at": "2026-08-28T00:00:00Z",
                    "released_at": None,
                },
            }
            ok, problem, detail = trust_module.establish(
                entry, workspaces, config
            )
            self.assertTrue(ok, "%s: %s" % (problem, detail))

        environment = dict(os.environ)
        environment["HOME"] = home
        environment["TERM"] = "xterm-256color"
        for name in ("CLAUDE_CODE_SANDBOXED", "CLAUDE_JOB_DIR",
                     "CLAUDE_BG_BACKEND"):
            environment.pop(name, None)
        argv = [self.cli] + (list(BELT_ARGV) if belt else [])

        pid, descriptor = pty.fork()
        if pid == 0:                                   # pragma: no cover
            try:
                os.chdir(lease)
                os.execve(self.cli, argv, environment)
            finally:
                os._exit(127)
        # Registered before the read loop, so the tree is reaped even
        # when that loop raises.
        self.addCleanup(reap_process_group, pid)
        buffer = b""
        deadline = time.time() + CLI_READ_DEADLINE_SECONDS
        try:
            while time.time() < deadline:
                ready, _, _ = select.select([descriptor], [], [], 0.5)
                if not ready:
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                text = strip_terminal_control(
                    buffer.decode("utf-8", "replace")
                )
                if any(marker in text for marker in (
                    GATE_PHRASE, ONBOARDING_PHRASE, PROMPTABLE_PHRASE
                )):
                    break
        finally:
            survivors = reap_process_group(pid)
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.assertEqual(
            survivors, [],
            "the launched CLI's process tree outlived the test — an"
            " unowned live process was leaked: %s" % survivors,
        )
        return strip_terminal_control(
            buffer.decode("utf-8", "replace")
        )

    def assertScreen(self, text, expected):
        observed = {
            "gate": GATE_PHRASE in text,
            "onboarding": ONBOARDING_PHRASE in text,
            "promptable": PROMPTABLE_PHRASE in text,
        }
        self.assertEqual(
            {name for name, hit in observed.items() if hit},
            {expected},
            "expected exactly the %s screen; observed %s"
            % (expected, observed),
        )


class MinimalConfigDerivationTests(CliArmMixin):
    """The EXECUTED DERIVATION behind `MINIMAL_CONFIG_KEYS`.

    The whitelist is not hand-enumerated: each member is here because
    removing it changes what the CLI shows, or because DI's own
    contract requires it. Re-running these tests re-derives the set.
    """

    def test_the_fixture_carries_no_credential_or_service_key(self):
        path = os.path.join(self.base, "probe.json")
        document = build_minimal_config(path)
        self.assertEqual(
            sorted(document), sorted(MINIMAL_CONFIG_KEYS),
            "the injected configuration grew a key outside the"
            " derived whitelist",
        )
        for forbidden in FORBIDDEN_CONFIG_KEYS:
            with self.subTest(key=forbidden):
                self.assertNotIn(forbidden, document)
        # "Built up, not filtered down" is NOT re-checked against the
        # real file here — doing so would itself be a Population A
        # read of it (R-5), and was the exact counterexample that
        # falsified this module's earlier over-broad claim. The
        # property is pinned structurally instead, by
        # `PopulationBoundaryTests`: within this module a launching
        # class may not reference REAL_CONFIG, so this fixture is not
        # a filtered copy of it.
        self.assertEqual(
            document["projects"], {},
            "the fixture's projects object must start empty",
        )

    def test_without_hasCompletedOnboarding_the_gate_is_never_reached(self):
        # Removing this key does NOT merely change cosmetics: the CLI
        # stops at onboarding and the trust gate is not reached, so
        # the key is required for the control arm to carry weight.
        text = self.run_cli(
            seed_trust=False,
            document_override={"projects": {}},
        )
        self.assertScreen(text, "onboarding")

    def test_projects_is_required_by_DIs_own_contract_not_by_the_gate(self):
        # Honest derivation: the GATE appears without a projects
        # object at all...
        text = self.run_cli(seed_trust=False, include_projects=False)
        self.assertScreen(text, "gate")
        # ...but establishment refuses such a config, which is why the
        # key is in the whitelist.
        path = os.path.join(self.base, "noprojects.json")
        build_minimal_config(path, include_projects=False)
        workspaces = os.path.join(self.base, "ws-contract")
        lease = workspace_module.lease_path(workspaces, "wf-c")
        os.makedirs(lease)
        ok, problem, _ = trust_module.establish(
            {
                "workflow_id": "wf-c",
                "workspace_lease": {
                    "lease_id": "l", "path_realpath": lease,
                    "acquired_at": "x", "released_at": None,
                },
            },
            workspaces, path,
        )
        self.assertFalse(ok)
        self.assertEqual(
            problem, trust_module.PROBLEM_PROJECTS_MISSING
        )

    def test_the_belt_flag_is_not_load_bearing(self):
        # R-1.4: --strict-mcp-config is a belt, not the guarantee.
        # With a config built from {} there is no MCP server to
        # ignore, so the gate must reproduce identically WITHOUT the
        # flag. If this ever fails, the flag is affecting the thing
        # under test and must be removed.
        text = self.run_cli(seed_trust=False, belt=False)
        self.assertScreen(text, "gate")


class RealCliConsumedEffectTests(CliArmMixin):
    """THE POSITIVE CASE, proven by the CONSUMED EFFECT.

    Runs the REAL installed Claude CLI in a pty, in a directory it has
    not seen, under an INJECTED HOME whose configuration is BUILT UP
    FROM AN EMPTY OBJECT. The property is not "the key is present" —
    it is "the interactive session reaches a promptable state with no
    terminal click", which is the objective itself.

    RESIDUAL BOUNDARY — the complete, derived list of ways this
    invocation differs from the one Herdr uses, and why none can
    affect the gate:

    1. The configuration contains only `hasCompletedOnboarding` and
       `projects` (see `MINIMAL_CONFIG_KEYS`). The trust decision
       reads `projects[<key>].hasTrustDialogAccepted` and the same key
       on ancestors, both of which this fixture can express exactly.
       No credential or MCP key exists to influence the outcome, BY
       CONSTRUCTION rather than by removal — so no external service is
       started, and no OAuth material is ever copied into a test HOME.
    2. `--strict-mcp-config` is passed as a belt.
       `MinimalConfigDerivationTests.test_the_belt_flag_is_not_load_
       bearing` reproduces the gate WITHOUT it, so it is not the
       guarantee and does not be masking the effect under test.
    3. `HOME` is injected and there are no credentials, so the session
       shows `Not logged in`. The trust gate is evaluated before and
       independently of authentication — the control arm reaches the
       gate in exactly the same unauthenticated HOME.
    4. No prompt is ever submitted; the tree is reaped as soon as one
       of the three known screens is observed.

    BOUNDARY, stated plainly: if no `claude` binary is installed this
    class SKIPS, and the consumed effect is then UNVERIFIED on that
    machine — a coverage hole, not a pass.
    """

    def test_an_unseeded_workspace_stops_at_the_trust_gate(self):
        # The CONTROL. Without this arm the seeded arm would prove
        # little: it would stay green even where the gate failed to
        # appear.
        self.assertScreen(self.run_cli(seed_trust=False), "gate")

    def test_an_established_workspace_reaches_a_promptable_session(self):
        self.assertScreen(self.run_cli(seed_trust=True), "promptable")



# The primitives that make a check SOURCE-LEVEL rather than
# behavioural: a test reaching for one of these is reading the
# program text instead of running the program.
#
# Round-03 B.1: this tuple used to be hand-written, and a
# hand-remembered PREDICATE re-encodes the blind spot as surely as a
# hand-remembered row list — reviewer1 walked two undeclared guards
# past the closure using `open().read()` and `getsourcelines`, and
# the first of those is the primitive used by the very check R-8 C-2
# forced this increment to demote. The tuple is now DERIVED-AGAINST:
# `test_the_vocabulary_covers_every_reflection_primitive_in_the_diff`
# fails where an `ast.*`/`inspect.*` attribute appears on an I1-added
# line without being classified here or in `NON_SOURCE_REFLECTION`
# below. Scope of that guarantee: attributes
# spelled on `ast`/`inspect`, plus reads whose argument names a
# repository `.py` or a mapped document (see `_reads_repository_source`).
# Outside it, and disclosed: a primitive reached through an alias
# (`from inspect import getsource`), through `getattr`, or through a
# module this detector does not name.
SOURCE_LEVEL_PRIMITIVES = (
    "parse", "getsource", "getsourcelines", "getdoc",
    "get_docstring", "get_source_segment", "walk",
    "doc_text", "read_repository_source",
)


def _is_ast_node_type(name):
    """`ast.ClassDef`, `ast.Name`, … are NODE TYPES used in isinstance
    checks, not operations that read source. Decided mechanically
    from the `ast` module itself rather than by listing them, so a
    node type this file has not used is classified correctly the
    first time it appears."""
    candidate = getattr(ast, name, None)
    return isinstance(candidate, type) and issubclass(candidate,
                                                     ast.AST)

# Reflection over a LIVE CODE OBJECT, which reviewer1 adjudicated as
# "partly, and less than a source scan is" inside R-8's class: it
# reads the object the program actually built, not the text. Listed
# explicitly so the vocabulary check does not silently absorb a new
# name — each entry is a deliberate classification, not a default.
NON_SOURCE_REFLECTION = {
    "signature": "reads the live callable's own signature object,"
                 " not source text",
    "getfullargspec": "same, older spelling",
    "unwrap": "resolves a live object, reads no text",
}
# The demotion wording, taken from the precedent this repository
# already set for the hermetic-git AST layer
# (`ExecutedIdentitySweepTests`: "the AST guard above is the fast
# structural feedback in front of it").
DEMOTION_PHRASE = "fast structural feedback in front of"


def i1_changed_test_files():
    """The committed DI-REMOTE-2 test surface.

    This is deliberately independent of working-tree dirt.  The explicit
    fixture prevents an unrelated edit from entering the historical domain
    and prevents a clean checkout from emptying it.
    """
    return list(DI_REMOTE_2_TEST_PYTHON)


def _reads_repository_source(node, source_text):
    """True when this call READS a repository `.py` or a mapped
    document — a source read spelled without a `ast`/`inspect`
    name, which is how two guards walked past the round-02
    vocabulary.

    Scope: `open(...)` and `read_text(...)` calls whose first
    argument's source segment names `__file__`, a `.py` path, or a
    mapped document. Outside it, and disclosed: a path assembled at
    runtime from parts, or read through a helper this function does
    not name.
    """
    if not isinstance(node, ast.Call):
        return False
    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
    if name not in ("open", "read_text", "read_bytes"):
        return False
    if not node.args:
        return False
    segment = ast.get_source_segment(source_text, node.args[0]) or ""
    mapped = ("SECURITY.md", "OPERATOR_PROTOCOL.md", MODULE_DOC)
    return (
        "__file__" in segment
        or ".py" in segment
        or any(document in segment for document in mapped)
    )


def _surface_sources(paths):
    sources = {}
    for path in paths:
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
            sources[path] = handle.read()
    return sources


def _source_check_nodes(tree, path, expected_checks):
    """Yield the explicitly protected class/function nodes in a tree."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for function in node.body:
            key = (path, node.name, getattr(function, "name", None))
            if key in expected_checks:
                yield node, function


def i1_reflection_attributes(source_by_path=None, expected_checks=None):
    """`ast.*`/`inspect.*` attributes in the committed source-check domain."""
    expected_checks = (DI_REMOTE_2_SOURCE_CHECKS if expected_checks is None
                       else frozenset(expected_checks))
    if source_by_path is None:
        source_by_path = _surface_sources(
            sorted({path for path, _klass, _fn in expected_checks})
        )
    seen = {}
    for path, text in source_by_path.items():
        tree = ast.parse(text)
        for _klass, function in _source_check_nodes(
                tree, path, expected_checks):
            for node in ast.walk(function):
                if not isinstance(node, ast.Attribute):
                    continue
                base = node.value
                if isinstance(base, ast.Name) and base.id in (
                    "ast", "inspect"
                ):
                    seen.setdefault(node.attr, set()).add(path)
    return seen


def _tainting_helpers(tree, active_lines, source_text):
    """Module-level functions whose body reaches a source-reading primitive.

    I4 round 01, finding 7.1. The census attributed a primitive only to
    the test function CONTAINING it, so a test that called a
    module-level helper — `broker_source()` in
    `tests/test_reconcile_audit.py` — read repository source while the
    census reported zero source-level checks in that file. Within that
    file every real use declared correctly, so no undeclared check
    slipped past; what was wrong was the COUNT, and I8's claim-to-pin
    map would have inherited the undercount.

    **THE RESOLUTION STOPS AT DEPTH ONE, so the census it feeds is a
    FLOOR and not a total.** R-13 requires both halves, because
    resolving the hop without saying where the resolution ends leaves
    the NEXT hop silently uncounted, and a count that is really a floor
    presented as a count is the "silent truncation presented as fact"
    class this task hit once already, in I4 itself, in
    `observe_spawn_records`' malformed path.

    The stopping depth, named so a reader can tell from this docstring
    alone where the count stops being exact: within this file, a
    module-level function whose body contains a primitive in the committed
    specimen taints the test functions that CALL IT BY NAME — one level.
    A use reached at depth two or beyond is attributed to the helper
    that holds it and is NOT added to its caller, so it is counted
    once rather than at the test that ultimately drives it. Also
    outside the resolution, and each of these leaves the count a
    floor: a helper imported from ANOTHER module, a method on a
    fixture class, a helper bound to a local alias, and a lookup
    through `getattr`.
    """
    helpers = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if getattr(inner, "lineno", None) not in active_lines:
                continue
            name = None
            if isinstance(inner, ast.Attribute):
                name = inner.attr
            elif isinstance(inner, ast.Name):
                name = inner.id
            if name in SOURCE_LEVEL_PRIMITIVES or (
                _reads_repository_source(inner, source_text)
            ):
                helpers.add(node.name)
                break
    return helpers


def i1_source_level_checks(source_by_path=None, expected_checks=None):
    """Each source/AST check in the committed DI-REMOTE-2 surface.

    The expected function identities are an independent committed fixture;
    this function proves those functions still perform source reflection.
    It accepts controlled source specimens so the one-hop detector can be
    falsified without mutating the repository.

    **The number this returns is a FLOOR, not a total** (R-13). It
    counts direct uses plus ONE HOP of module-level helper
    indirection; `_tainting_helpers` names the depth the resolution
    stops at and what sits beyond it. Within any consumer that reports
    this figure — I8's claim-to-pin map included — the floor label
    must travel with it, because an inherited undercount presented as
    an exhaustive map is the defect this closure exists to prevent.
    """
    expected_checks = (DI_REMOTE_2_SOURCE_CHECKS if expected_checks is None
                       else frozenset(expected_checks))
    if source_by_path is None:
        source_by_path = _surface_sources(
            sorted({path for path, _klass, _fn in expected_checks})
        )
    found = []
    for path, source_text in source_by_path.items():
        tree = ast.parse(source_text)
        active_lines = set(range(1, len(source_text.splitlines()) + 1))
        helpers = _tainting_helpers(tree, active_lines, source_text)
        for node, function in _source_check_nodes(
                tree, path, expected_checks):
            used = set()
            for inner in ast.walk(function):
                name = None
                if isinstance(inner, ast.Attribute):
                    name = inner.attr
                elif isinstance(inner, ast.Name):
                    name = inner.id
                if name not in SOURCE_LEVEL_PRIMITIVES:
                    continue
                used.add(name)
            for inner in ast.walk(function):
                if _reads_repository_source(inner, source_text):
                    used.add("read_repository_source")
            # ONE HOP of helper indirection (I4 round 01, 7.1).
            for inner in ast.walk(function):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in helpers
                ):
                    used.add("via:" + inner.func.id)
            if used:
                found.append(
                    (path, node.name, function.name,
                     tuple(sorted(used)))
                )
    return found


def must_have_run_verdict(ran, declared, excluded_by_sweep,
                          sweep_active, unredirectable_cli):
    """The must-have-run decision, as a pure function.

    Extracted so the DECISION itself can be driven with synthetic
    inputs. Round-03 mutant S01 showed that asserting it only against
    a healthy live registry leaves the comparison unprotected: making
    it compare a set with itself survived the whole suite.
    """
    if sweep_active:
        if excluded_by_sweep != declared:
            return False, ("a sweep child excluded %s, not the"
                           " declared %s" % (sorted(excluded_by_sweep),
                                             sorted(declared)))
        return True, None
    if unredirectable_cli is None:
        if ran:
            return False, ("no claude binary exists, yet the arms"
                           " ran: %s" % sorted(ran))
        return True, None
    if ran != declared:
        return False, (
            "the real-CLI arms did NOT run, and NO disclosed"
            " condition explains it: this is not a sweep child, and a"
            " claude binary DOES exist at %s. If the arms skipped"
            " because HOME or PATH was redirected, that is the defect"
            " — a redirected environment is not the disclosed"
            " condition 'no claude binary installed'. Missing: %s."
            " The increment's only consumed-effect pin is missing"
            " from this run."
            % (unredirectable_cli, sorted(declared - ran))
        )
    return True, None


def require_must_have_run(ran, declared, excluded_by_sweep,
                          sweep_active, unredirectable_cli):
    """Raise unless the arms ran or a disclosed condition explains it.

    Round-03 mutant S01c: when the live pin held the verdict in a
    boolean and asserted on it, weakening that one assertion
    (`assertTrue(True, ...)`) neutered the live check while the
    pure-function pin stayed green. There is no boolean here to
    weaken — the failure is raised by the check itself.
    """
    ok, message = must_have_run_verdict(
        ran, declared, excluded_by_sweep, sweep_active,
        unredirectable_cli,
    )
    if not ok:
        raise AssertionError(message)


# --- R-9: the false-absolute-claim closure --------------------------
#
# Each round of this task produced a sentence whose universal was
# falsified, and each attached to the REPLACEMENT for the previously
# falsified one. The quoted specimens are recorded in the evidence
# artifact rather than restated here, so this comment asserts no
# universal of its own.
#
# Rule: a universal is permitted when the SAME CLAUSE names the
# boundary it holds within, or the reach-arounds outside it.
ABSOLUTE_FORMS = (
    "never", "always", "cannot", "can not", "impossible", "any",
    "does not care how", "strictly stronger",
    # the negative-universal shape R-9 names first
    "no environment", "no file", "no argument", "nothing",
)
# Naming a boundary or a residual in the same sentence.
# How near a scope marker must sit to the absolute it scopes, in
# words within one clause (round-05 finding E.3).
SCOPE_GOVERNS_WORDS = 8

SCOPE_MARKERS = (
    # A restrictive clause immediately after the quantifier scopes it:
    # "anyone WHO CAN set the environment" names its boundary.
    "who can", "that this", "of the kind",
    "outside", "out of scope", "residual", "except", "unless",
    "does not cover", "not covered", "bounded", "within", "beyond",
    "disclosed", "reach-around", "boundary", "scope:", "only when",
    "limit", "in front of", "this detector does not",
)


def i1_changed_python_files():
    """The explicit committed DI-REMOTE-2 Python surface."""
    return list(DI_REMOTE_2_PYTHON)


def i1_added_docstrings(path):
    """Current docstrings of the protected source-check functions."""
    try:
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as h:
            tree = ast.parse(h.read())
    except (OSError, SyntaxError):
        return []
    expected = {
        key for key in DI_REMOTE_2_SOURCE_CHECKS if key[0] == path
    }
    found, seen = [], set()
    for klass, function in _source_check_nodes(tree, path, expected):
        for node in (klass, function):
            text = ast.get_docstring(node)
            if text and text not in seen:
                found.append(text)
                seen.add(text)
    return found


def _protected_comment_units(path):
    """Comment blocks inside protected source-check functions."""
    try:
        with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as h:
            source = h.read()
    except OSError:
        return []
    lines, tree = source.splitlines(), ast.parse(source)
    expected = {
        key for key in DI_REMOTE_2_SOURCE_CHECKS if key[0] == path
    }
    units = []
    for _klass, function in _source_check_nodes(tree, path, expected):
        comments = []
        for number in range(function.lineno, function.end_lineno + 1):
            text = lines[number - 1].strip()
            if text.startswith("#"):
                comments.append((number, text.lstrip("# ").strip()))
        current, previous = [], None
        for number, text in comments:
            if previous is not None and number != previous + 1:
                if current:
                    units.append(" ".join(current))
                current = []
            current.append(text)
            previous = number
        if current:
            units.append(" ".join(current))
    return units


def i1_added_prose_by_kind():
    """Permanent DI-REMOTE-2 normative prose, with provenance:
    ``(path, kind, text)`` where kind is "document", "docstring" or
    "comment".

    Round-05 finding E.1: the vacuity guard counted SOURCES, and
    comments alone supply every source, so disabling the docstring
    extraction outright dropped the set from 299 units to 175 with
    the guard still green — the round-04 closure could be silently
    reverted. Provenance is tracked here so the guard can assert that
    the docstring half actually CONTRIBUTES.
    """
    units = [
        (document, "document", claim)
        for document, _label, claim, _pin in CLAIM_PIN_MAP
    ]
    protected_paths = sorted({
        path for path, _klass, _function in DI_REMOTE_2_SOURCE_CHECKS
    })
    for path in protected_paths:
        for text in i1_added_docstrings(path):
            for paragraph in re.split(r"\n\s*\n", text):
                collapsed = " ".join(paragraph.split())
                if len(collapsed) >= 40:
                    units.append((path, "docstring", collapsed))
        units.extend(
            (path, "comment", text)
            for text in _protected_comment_units(path)
            if len(text) >= 40
        )
    return units


def i1_added_prose():
    """The current mapped claims and their protected detector prose.

    This stable domain retains document, docstring and comment coverage while
    excluding unrelated later edits and working-tree state.
    """
    return [(path, text)
            for path, _kind, text in i1_added_prose_by_kind()]


def _contains_absolute(text):
    """Does this text use one of the banned forms AS A WORD?

    Substring matching was wrong in both directions: it flagged
    `whenever` for containing `never` and `anyone` for containing
    `any`, and the scope markers added to compensate widened the pass
    position. Word boundaries make the vocabulary mean what it says.
    """
    low = flat(text)
    for form in ABSOLUTE_FORMS:
        if re.search(r"\b%s\b" % re.escape(form), low):
            return True
    return False


def _clauses(sentence):
    """Split a sentence into CLAUSES. Round-04 finding 3: a scope
    marker sitting elsewhere in the sentence used to wave an
    absolute in a different clause, so `boundary` or `within`
    occurring incidentally exempted a bare universal beside it.
    """
    # Clause boundaries only: comma, semicolon, colon and em dash.
    # " and " / " so " join parts of ONE clause and are deliberately
    # not split on, or a scope would be severed from what it scopes.
    return [part for part in re.split(r"[,;:—]", sentence)
            if part.strip()]


def _scope_governs(clause):
    """Does a scope marker GOVERN the absolute in this clause?

    Round-05 finding E.3: presence anywhere in the clause was still a
    remembered tuple in the pass position. A marker now has to sit
    within `SCOPE_GOVERNS_WORDS` words of the absolute it claims to
    scope, so an incidental `boundary` or `within` at the far end of
    a long clause no longer waves it.

    A marker is matched as its COMPLETE phrase. Matching only its
    first word made `does not cover` fire on any clause containing
    the word `does`, which waved `DI never does the thing.` — caught
    by this module's own synthetic detector pin.

    Scope: word distance within one clause. Outside it, and
    disclosed: a marker that genuinely governs from further away is
    rejected, which is the conservative direction — it fails the pin
    and the sentence gets rewritten.
    """
    words = flat(clause).split()
    joined = " ".join(words)

    def word_index_of(character_offset):
        return len(joined[:character_offset].split())

    marker_at = []
    for marker in SCOPE_MARKERS:
        needle = flat(marker)
        start = joined.find(needle)
        while start != -1:
            marker_at.append(word_index_of(start))
            start = joined.find(needle, start + 1)
    if not marker_at:
        return False
    absolute_at = []
    for form in ABSOLUTE_FORMS:
        for match in re.finditer(r"\b%s\b" % re.escape(flat(form)),
                                 joined):
            absolute_at.append(word_index_of(match.start()))
    if not absolute_at:
        return True
    return any(abs(marker - absolute) <= SCOPE_GOVERNS_WORDS
               for marker in marker_at for absolute in absolute_at)


def sentence_carries_a_bare_absolute(sentence, pinned_rows=()):
    """Is this ONE sentence an unqualified universal?

    Extracted as a pure function so the DECISION can be driven with
    synthetic sentences. Round-03 mutants T04/T05 showed that a
    checker asserted only against live prose is unprotected: blinding
    its exemption, or deleting a form from its vocabulary, left the
    suite green because healthy prose trips neither.
    """
    if not _contains_absolute(sentence):
        return False
    # LEAD RULING L-3: there is no claim->pin escape. It was not one
    # of D-1's two conditions, R-9 did not authorise it, and it
    # exempted exactly the pinned population where every prior
    # instance of this class occurred. `pinned_rows` is accepted and
    # ignored so existing callers keep working; within this
    # predicate a pinned sentence is judged like an unpinned one.
    for clause in _clauses(sentence):
        if not _contains_absolute(clause):
            continue
        # The scope marker must sit in the SAME CLAUSE as the
        # absolute it claims to scope, and must GOVERN it (E.3).
        if _scope_governs(clause):
            continue
        if ZZAbsoluteClaimClosureTests.is_quoted(clause):
            continue
        return True
    return False


class ZZAbsoluteClaimClosureTests(unittest.TestCase):
    """Supervisor ruling R-9: no unqualified universal in protected prose.

    Source is the only feasible level here, and the reason is that
    the PROPERTY IS ITSELF TEXTUAL: an overclaimed SENTENCE is the
    defect, and a sentence has no behaviour to execute. The precedent
    for that declaration is `test_the_reclaim_window_is_disclosed`.

    Scope: current mapped claims plus docstrings and comments belonging to
    the committed source-check functions. Outside it, and disclosed: prose
    outside that explicit surface and an overclaim expressible without one
    of the listed forms.
    """

    def test_the_absolute_detector_is_not_vacuous(self):
        """EXECUTED PIN on the DECISION itself, driven with synthetic
        sentences. Source is the only feasible level for the closure
        it guards — a sentence has no behaviour — but the PREDICATE
        does, and this drives it."""
        bare = [
            "DI never loses a trust entry.",
            "This cannot be bypassed.",
            "A torn file is impossible here.",
            "The check always fires.",
            "It refuses any path it is given.",
        ]
        for sentence in bare:
            with self.subTest(sentence=sentence):
                self.assertTrue(
                    sentence_carries_a_bare_absolute(sentence),
                    "an unqualified universal was not flagged",
                )
        scoped = [
            "Within this module a trust entry is not lost; outside it,"
            " a concurrent writer is not covered.",
            "This is not bypassable from the ambient environment;"
            " in-process code injection is out of scope.",
        ]
        for sentence in scoped:
            with self.subTest(sentence=sentence):
                self.assertFalse(
                    sentence_carries_a_bare_absolute(sentence),
                    "a properly scoped sentence was flagged",
                )
        # LEAD RULING L-3: a claim->pin row is NOT an escape. A
        # pinned sentence is judged like an unpinned one — that
        # population is exactly where rounds 01-03's failures were.
        self.assertTrue(
            sentence_carries_a_bare_absolute(
                "It never rains.", pinned_rows=("it never rains",)
            ),
            "a claim->pin row still waves a bare absolute",
        )
        # Round-05 finding E.3, and mutant V04: the scope marker must
        # GOVERN the absolute, not merely share a clause with it: a
        # marker beyond the governing distance leaves it flagged.
        distant = (
            "DI never does the thing that the following long list of"
            " unrelated qualifying words eventually mentions a"
            " boundary about"
        )
        self.assertTrue(
            sentence_carries_a_bare_absolute(distant),
            "an absolute was waved by a scope word %d+ words away;"
            " presence in the clause is not governance"
            % SCOPE_GOVERNS_WORDS,
        )
        near = "Within this module DI never does the thing."
        self.assertFalse(
            sentence_carries_a_bare_absolute(near),
            "a scope marker governing the absolute beside it was"
            " rejected; the distance rule is too tight",
        )
        # Each banned form must actually be detectable: a form
        # deleted from the vocabulary fails here.
        for form in ABSOLUTE_FORMS:
            with self.subTest(form=form):
                self.assertTrue(
                    sentence_carries_a_bare_absolute(
                        "DI %s does the thing." % form
                    ),
                    "the vocabulary no longer detects %r" % form,
                )

    def test_the_prose_set_is_derived_and_not_vacuous(self):
        units = i1_added_prose()
        self.assertGreater(
            len(units), 20,
            "almost no added prose derived — the derivation is"
            " broken, so a clean result below proves nothing",
        )
        sources = {unit[0] for unit in units}
        self.assertIn(
            MODULE_DOC, sources,
            "the production module's own prose is not in the set",
        )
        # Round-04 finding 1, and round-05 mutant U05: the derivation
        # must reach docstrings across the committed function domain.
        self.assertGreaterEqual(
            len(sources), 5,
            "the prose set covers only %d sources (%s); a derivation"
            " narrowed to a few files is how the round-03 guarantee"
            " sentences escaped it" % (len(sources), sorted(sources)),
        )
        test_sources = {s for s in sources
                        if s.startswith("tests/") and s.endswith(".py")}
        self.assertTrue(
            test_sources,
            "no TEST module prose in the set — the guarantee"
            " sentences rewritten in round 03 live there",
        )
        # Round-05 finding E.1: counting SOURCES is the wrong
        # granularity. Comments alone supply every source, so
        # disabling the DOCSTRING extraction outright left this guard
        # green while the round-04 closure was silently reverted and
        # an overclaim injected into a test docstring went undetected.
        # The docstring half must be shown to CONTRIBUTE.
        by_kind = i1_added_prose_by_kind()
        kinds = collections.Counter(kind for _, kind, _ in by_kind)
        for kind in ("document", "docstring", "comment"):
            with self.subTest(kind=kind):
                self.assertGreater(
                    kinds[kind], 0,
                    "the %s extraction contributed NOTHING; the prose"
                    " set is %s" % (kind, dict(kinds)),
                )
        docstring_units_in_tests = [
            text for path, kind, text in by_kind
            if kind == "docstring" and path.startswith("tests/")
        ]
        self.assertTrue(
            docstring_units_in_tests,
            "no unit came from a DOCSTRING in a test module — that is"
            " exactly where round 03's guarantee sentences live, and"
            " where an injected overclaim went undetected in round 05",
        )

    @classmethod
    def is_quoted(cls, clause):
        """Is the absolute inside quotes or backticks — named rather
        than asserted?

        Round-04 finding 3: the previous version ALSO exempted a
        text mentioning a review round, which waved real claims that
        merely cited one. That branch is gone; only ACTUAL quotation
        exempts, which is a property of the text rather than a
        remembered list of topics.
        """
        low = flat(clause)
        for form in ABSOLUTE_FORMS:
            index = low.find(form)
            if index == -1:
                continue
            before, after = low[:index], low[index + len(form):]
            if before.count('"') % 2 or before.count("`") % 2:
                return True
            if "`" in before[-3:] and "`" in after[:3]:
                return True
        return False

    def test_no_added_guarantee_sentence_carries_a_bare_absolute(self):
        # ONE implementation of the decision, shared with
        # `test_the_absolute_detector_is_not_vacuous`, which drives it
        # with synthetic sentences. Duplicating the logic here left
        # round-03 mutant T04b alive: blinding this copy was invisible
        # to the pin on the other one.
        offenders = []
        rows = [flat(row[2]) for row in CLAIM_PIN_MAP]
        for where, unit in i1_added_prose():
            for sentence in re.split(r"(?<=[.;:])\s+", unit):
                if sentence_carries_a_bare_absolute(sentence, rows):
                    offenders.append(
                        "%s: %s" % (where, sentence[:130])
                    )
        self.assertEqual(
            offenders, [],
            "UNQUALIFIED ABSOLUTE(S) in prose this increment adds"
            " (%d). Each must name, IN THE SAME SENTENCE, the"
            " boundary it holds within or the reach-arounds outside"
            " it — or carry a claim->pin row:\n  %s"
            % (len(offenders), "\n  ".join(offenders)),
        )


class ZZSourceLevelClosureTests(unittest.TestCase):
    """Supervisor ruling R-8: the structural closure of the
    source-guard class, which recurred three times in this task.

    NO source/AST scan I1 introduces may be the load-bearing pin for
    a behavioural property. Each must say so in its own docstring, in
    the wording this repository already set for the hermetic-git AST
    layer, and must NAME the executed guarantee it fronts.

    The enumeration is an explicit committed structural fixture, independent
    of the working tree.  The closure fails when an expected function or its
    source read disappears, so a stale fixture does not keep a check alive.

    Source is the only feasible level for THIS class, and the reason
    is that its subject IS the source: it checks whether each scan
    DECLARES its executed guarantee. There is no behaviour to run —
    a declaration is text, and its absence is the defect.
    """

    def test_the_enumeration_is_derived_and_not_vacuous(self):
        files = i1_changed_test_files()
        self.assertTrue(
            files, "no committed test files derived — the enumeration"
            " below would be vacuous",
        )
        self.assertIn("tests/test_workspace_trust.py", files)
        self.assertTrue(
            i1_source_level_checks(),
            "no source-level checks derived at all; the detector is"
            " broken, so a clean result proves nothing",
        )
        observed = {row[:3] for row in i1_source_level_checks()}
        self.assertEqual(
            observed, DI_REMOTE_2_SOURCE_CHECKS,
            "the committed source-check domain no longer matches the"
            " functions that actually read source",
        )

    def test_the_vocabulary_covers_every_reflection_primitive_in_the_diff(self):
        """Round-03 B.1: membership was once decided by a hand-written
        tuple, so a source-reading primitive could be invisible.  Each
        `ast.*`/`inspect.*` attribute in the committed function domain must
        be classified as source reflection, live-object reflection, or
        (mechanically) an AST node type.

        Scope: attributes spelled on `ast`/`inspect`. Outside it, and
        disclosed: an alias import, a `getattr` lookup, or another
        module's reader.
        """
        referenced = i1_reflection_attributes()
        self.assertTrue(
            referenced,
            "no reflection attributes derived at all — the detector"
            " is broken, so a clean result proves nothing",
        )
        unclassified = sorted(
            name for name in referenced
            if name not in SOURCE_LEVEL_PRIMITIVES
            and name not in NON_SOURCE_REFLECTION
            and not _is_ast_node_type(name)
        )
        self.assertEqual(
            unclassified, [],
            "UNCLASSIFIED REFLECTION PRIMITIVE(S) %s appear on"
            " protected functions. Add each to SOURCE_LEVEL_PRIMITIVES (it"
            " reads program TEXT) or to NON_SOURCE_REFLECTION with"
            " the reason (it reads a live OBJECT). Until then the"
            " closure's enumeration is not complete."
            % unclassified,
        )

    def test_a_plain_source_read_counts_as_a_primitive(self):
        """The specimen that walked past the round-02 vocabulary:
        `open(<a repository .py>).read()` with a substring test, which
        is also the shape of `test_di_never_heartbeats_its_lock`."""
        sample = (
            "def f():\n"
            "    with open(trust_module.__file__) as h:\n"
            "        return h.read()\n"
        )
        call = None
        for node in ast.walk(ast.parse(sample)):
            if isinstance(node, ast.Call):
                call = node
                break
        self.assertIsNotNone(call)
        self.assertTrue(
            _reads_repository_source(call, sample),
            "a plain read of a repository .py is not recognised as a"
            " source-level primitive",
        )

    @staticmethod
    def _owner(path, klass):
        """Resolve the class from the FILE the check was derived from.

        This used to look only in this module, so a source-level check
        added to another test file resolved to None, read as having no
        docstring, and was reported undeclared even when it declared
        correctly. I2 added such checks in `tests/test_parity.py` and
        surfaced it.
        """
        import importlib
        module_name = os.path.basename(path)[:-3]
        for candidate in (module_name, "tests." + module_name):
            try:
                module = importlib.import_module(candidate)
            except Exception:                          # noqa: BLE001
                continue
            owner = getattr(module, klass, None)
            if owner is not None:
                return owner
        return getattr(sys.modules[__name__], klass, None)

    def test_the_census_follows_one_hop_of_helper_indirection(self):
        """R-13 half one, driven with a controlled helper specimen.

        Source is the only feasible level for this class and the
        reason is that its subject IS a source-analysis method — what
        a census reports about a body of text has no runtime behaviour
        of its own — but the DECISION does, and this drives it over
        the real derived rows rather than over a fixture.
        """
        specimen = (
            "def source_helper():\n"
            "    with open('controlled.py') as handle:\n"
            "        return handle.read()\n\n"
            "class ControlledTests:\n"
            "    def test_through_helper(self):\n"
            "        return source_helper()\n"
        )
        key = ("tests/controlled.py", "ControlledTests",
               "test_through_helper")
        specimen_rows = i1_source_level_checks(
            {key[0]: specimen}, {key},
        )
        self.assertEqual(len(specimen_rows), 1)
        self.assertIn("via:source_helper", specimen_rows[0][3])

        rows = i1_source_level_checks()
        audit_rows = [
            row for row in rows
            if row[0] == "tests/test_reconcile_audit.py"
        ]
        self.assertEqual(
            len(audit_rows), 3,
            "the three helper-mediated uses in the audit module are"
            " counted as %d; reviewer1 derived three"
            % len(audit_rows),
        )

    def test_the_source_detector_ignores_prose_and_worktree_state(self):
        """Self-observation and historical-state resistance controls."""
        prose = (
            "class ProseOnlyTests:\n"
            "    def test_words_only(self):\n"
            "        '''ast.parse open repository.py getsource walk'''\n"
            "        return True\n"
        )
        key = ("tests/prose_only.py", "ProseOnlyTests",
               "test_words_only")
        self.assertEqual(
            i1_source_level_checks({key[0]: prose}, {key}), [],
            "describing source reflection in test prose became evidence",
        )
        from unittest.mock import patch
        with patch.object(
            subprocess, "run",
            side_effect=AssertionError("working-tree state was consulted"),
        ):
            self.assertEqual(
                {row[:3] for row in i1_source_level_checks()},
                DI_REMOTE_2_SOURCE_CHECKS,
            )

    def test_the_census_labels_its_number_a_FLOOR(self):
        """R-13 half two, driven: the count is a floor and says so,
        and the stopping depth is named where a reader will find it.

        Source is the only feasible level here for the same reason as
        above, and the property is textual besides: the defect being
        prevented is a floor PRESENTED as a total.
        """
        census_doc = flat(inspect.getdoc(i1_source_level_checks) or "")
        self.assertIn(
            flat("FLOOR, not a total"), census_doc,
            "the census stopped labelling its number a floor; a count"
            " that is really a floor, presented as a count, is the"
            " silent-truncation-as-fact class",
        )
        self.assertIn(flat("ONE HOP"), census_doc)
        depth_doc = flat(inspect.getdoc(_tainting_helpers) or "")
        self.assertIn(
            flat("THE RESOLUTION STOPS AT DEPTH ONE"), depth_doc,
            "the stopping depth is no longer named, so a reader"
            " cannot tell from the docstring where the count stops"
            " being exact",
        )
        for beyond in ("depth two", "ANOTHER module", "getattr"):
            self.assertIn(
                flat(beyond), depth_doc,
                "the residual no longer names %r as outside the"
                " resolution" % beyond,
            )

    def test_every_source_level_check_declares_its_executed_pin(self):
        undeclared = []
        for path, klass, function, used in i1_source_level_checks():
            owner = self._owner(path, klass)
            texts = [inspect.getdoc(owner) or ""]
            member = getattr(owner, function, None)
            texts.append(inspect.getdoc(member) or "")
            blob = flat(" ".join(texts))
            if any(flat(marker) in blob for marker in (
                DEMOTION_PHRASE,          # demoted in front of a pin
                "only feasible level",    # source is the only level
                "EXECUTED PIN:",          # itself drives production
            )):
                continue
            undeclared.append(
                "%s::%s.%s uses %s" % (path, klass, function,
                                       ", ".join(used))
            )
        self.assertEqual(
            undeclared, [],
            "SOURCE-LEVEL CHECK WITH NO DECLARED EXECUTED GUARANTEE"
            " (%d). Each must say in its own docstring that it is"
            " %r a NAMED executed guarantee, or state that source is"
            " the only feasible level with the reason:\n  %s"
            % (len(undeclared), DEMOTION_PHRASE,
               "\n  ".join(undeclared)),
        )


class ZZMustHaveRunTests(unittest.TestCase):
    """Round-01 B2, requirement 2: a POSITIVE must-have-run pin that
    FAILS rather than skips.

    The consumed-effect arms are the only pin for this increment's
    entire objective. A skip is exactly how that guarantee vanished
    behind a green suite, so their absence must be an explicit
    FAILURE unless one of two disclosed conditions holds: an
    AUTHENTICATED sweep child, or no `claude` binary installed.

    Named ZZ so the default loader runs it after the classes it
    observes. (Running this class ALONE will fail by design — the
    registries are populated by the other classes in the same
    process.)
    """

    def test_the_cli_arms_ran_or_a_disclosed_condition_explains_why(self):
        require_must_have_run(
            CLI_CLASSES_THAT_RAN,
            set(CLI_CLASSES_DECLARED),
            CLI_CLASSES_EXCLUDED_BY_SWEEP,
            hermetic_git.sweep_child_active(),
            cli_installed_unredirectably(),
        )

    def test_the_must_have_run_decision_is_not_vacuous(self):
        """EXECUTED PIN on the DECISION itself, driven with synthetic
        registries. Round-03 mutant S01: a comparison that compares a
        set with itself passes each healthy run, so the live
        assertion above does not protect it."""
        declared = {"A", "B"}
        cli = "/somewhere/claude"
        # The case the whole pin exists for: arms missing, with no
        # disclosed condition explaining it.
        ok, message = must_have_run_verdict(
            set(), declared, set(), False, cli
        )
        self.assertFalse(ok, "a completely empty run was accepted")
        self.assertIn("did NOT run", message)
        self.assertIn("HOME or PATH", message)
        self.assertIn(cli, message)
        # A PARTIAL run is also a failure.
        ok, message = must_have_run_verdict(
            {"A"}, declared, set(), False, cli
        )
        self.assertFalse(ok, "a partial run was accepted")
        self.assertIn("B", message)
        # The healthy case passes.
        self.assertEqual(
            must_have_run_verdict({"A", "B"}, declared, set(), False,
                                  cli),
            (True, None),
        )
        # Sweep exemption: only when the excluded set is the whole
        # declared set.
        self.assertEqual(
            must_have_run_verdict(set(), declared, declared, True,
                                  cli),
            (True, None),
        )
        ok, _ = must_have_run_verdict(set(), declared, {"A"}, True,
                                      cli)
        self.assertFalse(ok, "a partial sweep exclusion was accepted")
        # Absent-binary exemption: allowed only when no content ran.
        self.assertEqual(
            must_have_run_verdict(set(), declared, set(), False,
                                  None),
            (True, None),
        )
        ok, _ = must_have_run_verdict({"A"}, declared, set(), False,
                                      None)
        self.assertFalse(
            ok, "arms ran while claiming no binary exists"
        )
        # And the raising wrapper the live pin uses must raise on
        # exactly those inputs — there is no boolean at the live call
        # site that a mutant could weaken.
        with self.assertRaises(AssertionError):
            require_must_have_run(set(), declared, set(), False, cli)
        require_must_have_run({"A", "B"}, declared, set(), False, cli)

    def test_no_launching_class_opened_the_real_configuration(self):
        """THE EXECUTED POPULATION A GUARANTEE (round-02 B.2).

        Asserts on `open` events that ACTUALLY HAPPENED in THIS
        process while a launching class was live.

        Scope of the guarantee: in-process opens whose path string
        matches a known spelling of the real configuration — the
        `expanduser`-style path and its realpath. Within that scope
        the construction does not matter: a module-level helper, an
        alias, or a literal path all land here, which is what
        defeated the source-level guard that preceded it.

        Outside that boundary, and disclosed: a read performed by a
        SUBPROCESS this suite launches (the audit hook is per
        process); a path spelled differently but resolving to the
        same file (a dotted path, or a symlink alias created during
        the test); and a read through a syscall this hook does not
        observe. reviewer1 demonstrated the first three and did not
        treat them as defects of this pin; they are named here so the
        next reader does not mistake its reach.

        `PopulationBoundaryTests` is the fast structural feedback in
        front of this test.
        """
        if not POPULATION_A_WATCH_ARMED_FOR:
            self.skipTest(
                "no launching class ran in this process (see"
                " test_the_cli_arms_ran_or_a_disclosed_condition_"
                "explains_why, which FAILS if that is not explained)"
            )
        self.assertEqual(
            POPULATION_A_WATCH_ARMED_FOR, set(CLI_CLASSES_DECLARED),
            "the watch was not armed for every launching class",
        )
        self.assertGreater(
            _OPEN_WATCH["seen"], 0,
            "the audit hook observed NO opens at all while armed —"
            " it is not wired up, so a zero result proves nothing",
        )
        self.assertEqual(
            _OPEN_WATCH["events"], [],
            "a process-launching class opened the developer's real"
            " ~/.claude.json: %s" % _OPEN_WATCH["events"],
        )

    def test_the_open_watch_actually_detects_an_open(self):
        # Anti-vacuity for the guarantee above, WITHOUT reading the
        # real file: arm the watch over a sentinel path and prove the
        # hook records it. If this fails, the zero-events result
        # above is meaningless.
        with tempfile.TemporaryDirectory() as base:
            sentinel = os.path.join(base, "sentinel.json")
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("{}")
            before = len(_OPEN_WATCH["events"])
            _OPEN_WATCH["extra"].add(sentinel)
            arm_population_a_watch()
            try:
                with open(sentinel, "rb") as handle:
                    handle.read()
            finally:
                disarm_population_a_watch()
                _OPEN_WATCH["extra"].discard(sentinel)
            recorded = _OPEN_WATCH["events"][before:]
            del _OPEN_WATCH["events"][before:]
            self.assertEqual(
                [path for path, _ in recorded], [sentinel],
                "the audit hook did not record an open it was armed"
                " for; the Population A guarantee is vacuous",
            )

    def test_no_environment_variable_can_disable_the_arms(self):
        """Round-02 B.1 attack A, inverted into a guard.

        The reviewer forged the previous nonce with two variables and
        a file it wrote itself. There is now NO environment input at
        all within this predicate: the exclusion is an in-process
        attribute.

        This reads SOURCE, and is fast structural feedback in front of
        `test_the_marker_is_reachable_only_in_process` (which flips
        the marker and observes the predicate) and
        `test_the_cli_arms_ran_or_a_disclosed_condition_explains_why`
        (which FAILS if the arms are missing for a undisclosed
        reason) — those are the executed guarantees.
        """
        source = inspect.getsource(hermetic_git)
        for banned in ("SWEEP_CHILD_MARKER", "SWEEP_CHILD_NONCE_FILE",
                       "sweep_child_authenticated"):
            self.assertNotIn(
                banned, source,
                "%s still exists; an environment-supplied signal is"
                " assertable by anyone" % banned,
            )
        signature = inspect.signature(hermetic_git.sweep_child_active)
        self.assertEqual(
            list(signature.parameters), [],
            "sweep_child_active takes an input; the only correct"
            " input is none at all",
        )

    def test_the_exemption_predicate_ignores_HOME_and_PATH(self):
        """EXECUTED PIN: redirects HOME and PATH and observes the
        predicate. Round-02 attack B took the "no claude binary"
        exemption on a machine that HAS the binary, because both
        resolutions were ambient."""
        from unittest.mock import patch as mock_patch
        expected = cli_installed_unredirectably()
        # Recomputed locally rather than read from the module
        # constant: naming REAL_CONFIG here would (correctly) trip
        # PopulationBoundaryTests, which allows that name only in
        # Population B.
        config_expected = os.path.join(
            _unredirectable_home_path(), ".claude.json"
        )
        with tempfile.TemporaryDirectory() as base:
            with mock_patch.dict(
                os.environ, {"HOME": base, "PATH": "/nonexistent"}
            ):
                self.assertEqual(
                    cli_installed_unredirectably(), expected,
                    "the exemption predicate followed a redirected"
                    " HOME/PATH; a redirected environment is not the"
                    " disclosed condition",
                )
                self.assertEqual(
                    os.path.join(_unredirectable_home_path(),
                                 ".claude.json"),
                    config_expected,
                    "the real-config path followed a redirected HOME,"
                    " so"
                    " Population B would skip claiming the machine"
                    " has no configuration",
                )

    def test_the_marker_is_reachable_only_in_process(self):
        # Positive control: the mechanism is not dead code. It flips
        # only through the function the child runner calls, and the
        # sweep runner is the only caller.
        # Save and restore rather than assuming the flag starts
        # unset: inside a sweep child it is LEGITIMATELY set, and
        # asserting False on entry made this test fail there — the
        # sweep is exactly where the mechanism is in use.
        previous = hermetic_git._SWEEP_CHILD_ACTIVE
        try:
            hermetic_git._SWEEP_CHILD_ACTIVE = False
            self.assertFalse(hermetic_git.sweep_child_active())
            hermetic_git.mark_sweep_child()
            self.assertTrue(hermetic_git.sweep_child_active())
            hermetic_git._SWEEP_CHILD_ACTIVE = False
            self.assertFalse(hermetic_git.sweep_child_active())
        finally:
            hermetic_git._SWEEP_CHILD_ACTIVE = previous
        self.assertIn(
            "mark_sweep_child()", hermetic_git._CHILD_RUNNER,
            "the sweep's child runner no longer sets the marker, so"
            " the exclusion would not engage",
        )


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def doc_text(name):
    """The normative text of one mapped document. For the Python
    module that is its MODULE DOCSTRING, which is the normative
    surface — not the whole file."""
    if name == MODULE_DOC:
        # The module's normative surface is its module docstring PLUS
        # each function docstring in it — security prose lives in
        # both, and round-01 B4 was precisely about normative text
        # that no row covered.
        tree = ast.parse(inspect.getsource(trust_module))
        parts = [ast.get_docstring(tree) or ""]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                parts.append(ast.get_docstring(node) or "")
        return "\n\n".join(parts)
    with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as h:
        return h.read()


def flat(text):
    return re.sub(r"\s+", " ", text).lower()


def document_units(text):
    """Group a document into BULLET / PARAGRAPH units, not raw lines:
    these documents are hard-wrapped, so a line-level split would
    fragment single sentences and produce claims no row could ever
    match.

    ONE CANONICAL DEFINITION, consumed by `ClaimPinMapTests` (I1's
    two documents against their own baselines) and by
    `tests/test_docs_i8.py` (I8's four documents against theirs).
    Two copies of a splitter drift, and the drift shows up as a
    map that silently stops being exhaustive on one side.
    """
    units, current_unit = [], []
    for line in text.splitlines():
        stripped = line.strip()
        starts_unit = (
            stripped.startswith(("-", "*"))
            or re.match(r"^\d+\.\s", stripped)
        )
        if not stripped or starts_unit:
            if current_unit:
                units.append(" ".join(current_unit))
            current_unit = []
        if not stripped:
            continue
        current_unit.append(stripped.lstrip("-*#").strip())
    if current_unit:
        units.append(" ".join(current_unit))
    return units


#: The third document in the map: this module's own security
#: docstring, which round-01 B4 correctly identified as materially
#: new normative surface with no rows.
MODULE_DOC = "target_runtime/workspace_trust.py"


# The I1 claim->pin map. The ROW SET was derived mechanically from the
# diff of the two changed documents against their pre-I1 baselines
# (SECURITY.md against HEAD, OPERATOR_PROTOCOL.md against its
# preservation baseline), by taking each added claim and counting its
# occurrences in the previous version: a claim with zero occurrences
# is new surface and needs a row.
#
# `doc<->code`  falsifying the DOCUMENT fails the named pin.
# `fact-only`   true of the code, but a doc-only edit is not caught.
CLAIM_PIN_MAP = (
    ("SECURITY.md", "doc<->code",
     "writes exactly one key, `hasTrustDialogAccepted`",
     "test_doc_key_name_is_the_key_production_writes"),
    ("SECURITY.md", "doc<->code",
     "`workspace.lease_path(workspaces_root, workflow_id)`",
     "test_doc_boundary_derivation_is_the_one_production_uses"),
    ("SECURITY.md", "doc<->code",
     "holding `<config>.lock`",
     "test_doc_lock_path_is_the_one_production_creates"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "`workspace trust not established`",
     "test_doc_receipt_prefix_is_the_one_production_writes"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "durable BLOCKED workflow",
     "test_doc_blocked_phase_is_the_one_production_reaches"),
    ("SECURITY.md", "fact-only",
     "each top-level key is byte-identical before and after",
     "MinimalWriteTests.test_no_top_level_global_key_is_added_"
     "changed_or_removed"),
    ("SECURITY.md", "fact-only",
     "Each other project entry",
     "MinimalWriteTests.test_every_other_project_entry_is_byte_"
     "identical"),
    ("SECURITY.md", "fact-only",
     "leave the file byte-unchanged",
     "BoundaryScopeTests (all refusal rows)"),
    ("SECURITY.md", "fact-only",
     "root and its ancestors are refused",
     "BoundaryScopeTests.test_ancestor_of_the_root_is_refused"),
    ("SECURITY.md", "fact-only",
     "no `allowedTools` or other permission surface is widened",
     "MinimalWriteTests.test_no_permission_surface_is_widened_"
     "anywhere"),
    ("SECURITY.md", "fact-only",
     "a refusal rather than a repair, a re-creation or a rewrite"
     " from scratch",
     "ConfigShapeRefusalTests (all rows)"),
    ("SECURITY.md", "fact-only",
     "an interrupted write leaves the original byte-for-byte intact",
     "AtomicityAndConcurrencyTests.test_an_interrupted_write_leaves_"
     "the_original_intact"),
    ("SECURITY.md", "fact-only",
     "a lock held by another writer is not reclaimed or broken",
     "AtomicityAndConcurrencyTests.test_a_lock_held_by_another_"
     "writer_refuses_without_writing"),
    ("SECURITY.md", "fact-only",
     "a lock DI holds is not heartbeated",
     "AtomicityAndConcurrencyTests.test_di_never_heartbeats_its_lock"),
    ("SECURITY.md", "fact-only",
     "falls back to an UNLOCKED read-modify-write",
     "NO PIN — derived from the installed binary, recorded in the I1"
     " evidence artifact; a disclosed residual, not a guarantee"),
    ("SECURITY.md", "fact-only",
     "fresh read\n  of the file from disk rather than the in-memory document",
     "ReadBackTests.test_read_back_reads_the_file_not_the_memory"),
    ("SECURITY.md", "fact-only",
     "not a step toward dispatch",
     "BrokerOrderingTests.test_a_blocked_workflow_can_never_reach_"
     "dispatch"),
    ("SECURITY.md", "fact-only",
     "that already records trust performs no write",
     "MinimalWriteTests.test_reestablishment_on_a_trusted_entry_"
     "writes_nothing"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "immediately after materialization succeeds and before the"
     " workflow can advance one phase",
     "BrokerOrderingTests.test_the_happy_path_trusts_exactly_the_"
     "leased_workspace"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "for exactly one path",
     "BrokerOrderingTests.test_the_happy_path_trusts_exactly_the_"
     "leased_workspace"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "not retried, not turned into a",
     "BrokerOrderingTests.test_a_trust_failure_is_never_silently_"
     "retried"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "the single `hasTrustDialogAccepted` key of that one entry",
     "test_operator_doc_key_name_is_the_key_production_writes"),
    # --- I8: the three-part boundary block and the operator limits --
    ("SECURITY.md", "doc<->code",
     "writes the single `hasTrustDialogAccepted` key into one"
     " `projects` entry",
     "test_three_part_boundary_key_is_the_key_production_writes"),
    ("SECURITY.md", "doc<->code",
     "moves the workflow to the terminal BLOCKED phase",
     "test_three_part_failure_receipt_is_the_one_production_writes"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "each is pinned by a named test recorded in the I8 claim-to-pin",
     "test_docs_i8.ClaimPinMapI8Tests.test_every_added_unit_has_a_row"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "`observe` reports `configured_model`",
     "test_docs_i8.OperatorLimitsAreProductionsTests."
     "test_operator_model_key_is_the_key_production_emits"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "A verdict does not distinguish a model substitution from a"
     " restart",
     "test_model_substitution.SubstitutionWithSessionPRESERVEDTests."
     "test_the_two_scenarios_are_UNREPRESENTABLY_DIFFERENT"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "reported as BUILD SKEW, naming both the build that wrote the"
     " record and the build on disk",
     "test_docs_i8.OperatorLimitsAreProductionsTests."
     "test_operator_skew_label_is_the_one_the_render_emits"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "OMITTED from the turn listing rather than shown as healthy",
     "test_docs_i8.OperatorLimitsAreProductionsTests."
     "test_operator_omitted_label_is_the_one_the_render_emits"),
    # --- round-01 B4: the module docstring is normative surface ---
    (MODULE_DOC, "fact-only",
     "no other key or path is written",
     "MinimalWriteTests.test_write_happened_and_is_the_only_"
     "difference"),
    (MODULE_DOC, "fact-only",
     "EXACTLY ``workspace.lease_path(workspaces_root, workflow_id)``",
     "BoundaryScopeTests.test_boundary_is_derived_from_the_workspace_"
     "module"),
    (MODULE_DOC, "fact-only",
     "refusal no byte of the file is written",
     "BoundaryScopeTests (all refusal rows, byte-proven)"),
    (MODULE_DOC, "fact-only",
     "It writes no ANCESTOR of the workspace",
     "BoundaryScopeTests.test_ancestor_of_the_root_is_refused"),
    (MODULE_DOC, "fact-only",
     "an ancestor entry did not trust the workspace",
     "recorded Arm D in the I1 evidence; the refusal itself is pinned"
     " by test_ancestor_of_the_root_is_refused"),
    (MODULE_DOC, "fact-only",
     "not repaired, not re-created and not rewritten from scratch",
     "ConfigShapeRefusalTests (all rows)"),
    (MODULE_DOC, "fact-only",
     "NO TORN FILE, within one filesystem",
     "AtomicityAndConcurrencyTests.test_replace_is_used_from_the_"
     "same_directory_with_full_bytes"),
    (MODULE_DOC, "fact-only",
     "LOST UPDATES ARE BOUNDED, NOT ELIMINATED",
     "NO PIN — a disclosed residual about the CLI's own behaviour;"
     " its CONSEQUENCE is now refused at the point of use by"
     " PointOfUseTrustTests"),
    (MODULE_DOC, "doc<->code",
     "the directory ``<config>.lock``",
     "test_doc_lock_path_is_the_one_production_creates"),
    (MODULE_DOC, "fact-only",
     "does not heartbeat it",
     "AtomicityAndConcurrencyTests.test_di_never_heartbeats_its_lock"),
    (MODULE_DOC, "fact-only",
     "sustained contention is a REFUSAL, not a forced write",
     "AtomicityAndConcurrencyTests.test_a_lock_held_by_another_"
     "writer_refuses_without_writing"),
    (MODULE_DOC, "fact-only",
     "Each failure returns ``(False, problem_code, detail)``",
     "every assertRefused row in this module"),
    (MODULE_DOC, "fact-only",
     "not reach dispatch",
     "BrokerOrderingTests.test_a_blocked_workflow_can_never_reach_"
     "dispatch"),
    (MODULE_DOC, "doc<->code",
     "must act on the RESOLVED path",
     "SymlinkedConfigTests.test_the_lock_is_taken_on_the_resolved_"
     "path"),
    (MODULE_DOC, "fact-only",
     "REPLACES THE SYMLINK",
     "SymlinkedConfigTests.test_the_symlink_is_preserved_and_the_"
     "target_is_written"),
    (MODULE_DOC, "fact-only",
     "is reported with its own problem",
     "LockDiagnosticTests.test_an_unusable_lock_directory_is_not_"
     "reported_as_contention"),
    # I5-1 retracted the permanence rows in the SAME edit that added
    # revocation, which is what the (now inverted) pin below demands.
    ("SECURITY.md", "doc<->code",
     "The grant is REVOKED at release",
     "test_doc_revocation_matches_the_present_revocation_surface"),
    ("SECURITY.md", "doc<->code",
     "removes exactly the entry this workflow established",
     "TrustRevocationTests.test_revocation_removes_exactly_its_own_"
     "entry (byte-compares the surrounding configuration)"),
    ("SECURITY.md", "fact-only",
     "does not require that directory to exist",
     "TrustRevocationTests.test_revocation_works_after_the_directory_"
     "is_gone"),
    (MODULE_DOC, "fact-only",
     "bounded in WHAT is written",
     "MinimalWriteTests.test_write_happened_and_is_the_only_"
     "difference (the WHAT half); the HOW LONG half is now pinned by"
     " UnrelatedResourceTests.test_release_revokes_only_its_own_"
     "trust_entry"),
    (MODULE_DOC, "doc<->code",
     "exposes ``revoke`` beside ``establish``",
     "test_doc_revocation_matches_the_present_revocation_surface"),
    ("SECURITY.md", "doc<->code",
     "revocation reaches ONE `projects` key",
     "TrustRevocationTests.test_revocation_removes_exactly_its_own_"
     "entry and UnrelatedResourceTests.test_release_revokes_only_its_"
     "own_trust_entry"),
    ("SECURITY.md", "fact-only",
     "an entry written by an earlier build",
     "NO PIN — a stated NON-action about records this build never"
     " wrote; its residual is named in the same sentence"),
    (MODULE_DOC, "doc<->code",
     "LIFETIME — THE GRANT IS REVOKED AT RELEASE",
     "test_doc_revocation_matches_the_present_revocation_surface"),
    (MODULE_DOC, "doc<->code",
     "Revocation reaches exactly one ``projects`` key",
     "TrustRevocationTests.test_revocation_removes_exactly_its_own_"
     "entry"),
    (MODULE_DOC, "doc<->code",
     "``require_directory`` is True for establishment",
     "TrustRevocationTests.test_revocation_works_after_the_directory_"
     "is_gone and test_it_still_refuses_a_path_outside_the_managed_"
     "root"),
    (MODULE_DOC, "fact-only",
     "The durable, actionable receipt for a refused revocation",
     "NO PIN — a description of a receipt builder; the receipt's"
     " CONTENT is pinned by UnrelatedResourceTests.test_the_cleanup_"
     "receipt_records_what_happened"),
    (MODULE_DOC, "fact-only",
     "Same shape as `trust_block_receipt`",
     "NO PIN — a statement about shape reuse, carrying no runtime"
     " contract of its own"),
    (MODULE_DOC, "doc<->code",
     "Remove THIS workflow's own trust entry, and only that entry",
     "TrustRevocationTests.test_it_refuses_another_workflows_lease_"
     "path and test_the_decoy_project_survives_byte_identically"),
    (MODULE_DOC, "fact-only",
     "The mirror of `establish`, under the same discipline",
     "TrustRevocationTests.test_establish_then_revoke_returns_the_"
     "config_to_its_start"),
    (MODULE_DOC, "doc<->code",
     "What may be removed, and the boundary in the same breath",
     "TrustRevocationTests.test_revocation_removes_exactly_its_own_"
     "entry"),
    (MODULE_DOC, "doc<->code",
     "only when a fresh read from disk shows the key gone",
     "TrustRevocationTests.test_a_corrupt_config_is_a_refusal_and_"
     "changes_nothing"),
    (MODULE_DOC, "doc<->code",
     "Idempotent: an entry already absent is success",
     "TrustRevocationTests.test_revocation_is_idempotent"),
    (MODULE_DOC, "fact-only",
     "is not swept retroactively",
     "NO PIN — a stated NON-action, and the residual it names is"
     " that an entry with no surviving workflow record has no owner"
     " able to prove it"),
    ("SECURITY.md", "doc<->code",
     "Vendor facts are pinned to a CLI version",
     "test_doc_cli_version_is_the_one_the_module_derived_from"),
    ("SECURITY.md", "fact-only",
     "keeps its identity",
     "SymlinkedConfigTests (all four rows)"),
    ("SECURITY.md", "fact-only",
     "reporting success for an effect that no component would",
     "PointOfUseTrustTests.test_dispatch_refuses_when_the_child_"
     "reads_another_config"),
    ("SECURITY.md", "fact-only",
     "trust is re-verified at the POINT OF USE",
     "PointOfUseTrustTests.test_dispatch_refuses_when_trust_was_"
     "dropped_after_establishment"),
    ("OPERATOR_PROTOCOL.md", "fact-only",
     "Trust is checked again immediately before the Herdr is"
     " started",
     "PointOfUseTrustTests (all four rows)"),
    ("OPERATOR_PROTOCOL.md", "doc<->code",
     "was derived from `claude",
     "test_doc_cli_version_is_the_one_the_module_derived_from"),
    (MODULE_DOC, "fact-only",
     "into exactly one ``projects`` entry",
     "MinimalWriteTests.test_write_happened_and_is_the_only_"
     "difference"),
    (MODULE_DOC, "fact-only",
     "falls back to an UNLOCKED read-modify-write",
     "NO PIN — a fact about the vendor binary (evidence §1.7); its"
     " CONSEQUENCE is refused by PointOfUseTrustTests"),
    (MODULE_DOC, "fact-only",
     "Capability-free and path-free",
     "BrokerOrderingTests.test_the_durable_block_carries_an_"
     "actionable_reason"),
    (MODULE_DOC, "fact-only",
     "returns a FALSE GREEN",
     "SymlinkedConfigTests.test_readback_is_not_a_false_green_"
     "through_a_symlink"),
    (MODULE_DOC, "fact-only",
     "the two processes would take DIFFERENT locks",
     "SymlinkedConfigTests.test_the_lock_is_taken_on_the_resolved_"
     "path"),
    (MODULE_DOC, "fact-only",
     "is the chosen position",
     "SymlinkedConfigTests.test_the_symlink_is_preserved_and_the_"
     "target_is_written"),
    (MODULE_DOC, "fact-only",
     "getWorkspacePersistedTrustKey",
     "NO PIN — derived from the installed binary, recorded in"
     " evidence §1.5; the behaviour it describes is pinned by the"
     " real-CLI consumed-effect arms"),
    (MODULE_DOC, "fact-only",
     "a lock another writer holds is not reclaimed, broken or"
     " removed",
     "AtomicityAndConcurrencyTests.test_a_lock_held_by_another_"
     "writer_refuses_without_writing"),
    (MODULE_DOC, "fact-only",
     "Temp file in the SAME directory",
     "AtomicityAndConcurrencyTests.test_replace_is_used_from_the_"
     "same_directory_with_full_bytes"),
    (MODULE_DOC, "fact-only",
     "an interrupted write leaves the original",
     "AtomicityAndConcurrencyTests.test_an_interrupted_write_leaves_"
     "the_original_intact"),
    (MODULE_DOC, "fact-only",
     "Within this reader no content is repaired",
     "ConfigShapeRefusalTests (all rows)"),
    (MODULE_DOC, "fact-only",
     "EXACTLY ``lease_path(workspaces_root, workflow_id)`` resolved",
     "BoundaryScopeTests.test_boundary_is_derived_from_the_workspace_"
     "module"),
    (MODULE_DOC, "fact-only",
     "only when a fresh read of the config from disk shows the"
     " intended state",
     "ReadBackTests (both rows)"),
    (MODULE_DOC, "fact-only",
     "It is not closed here",
     "LockDiagnosticTests.test_the_reclaim_window_is_disclosed"),
)


class ClaimPinMapTests(TrustFixture):
    """The I1 claim->pin map, and the five rows where falsifying the
    DOCUMENT fails the pin. Presence rows are early-warning; the five
    doc<->code rows below PARSE the document and drive production
    with what they parsed, so a false sentence does not stay green."""

    def test_every_mapped_claim_is_present_in_its_document(self):
        """Textual by nature (a phantom row is a text defect), and
        fast structural feedback in front of the seven `doc<->code`
        methods below, each of which parses its document and then
        DRIVES production with what it parsed."""
        for document, label, claim, pin in CLAIM_PIN_MAP:
            with self.subTest(claim=claim[:48]):
                self.assertIn(
                    flat(claim), flat(doc_text(document)),
                    "%s row is a phantom: the claim is not in %s"
                    % (label, document),
                )

    # Added text that carries no contract claim, each with the reason
    # it is exempt. An exemption is a deliberate, reviewable act; the
    # test below refuses a case that is neither covered nor listed.
    NON_NORMATIVE_EXEMPTIONS = (
        ("heading", "Managed workspace trust"),
        ("heading", "Operator-visible rules"),
        ("heading", "WHY THIS EXISTS"),
        ("heading", "THE SECURITY BOUNDARY"),
        ("heading", "CONCURRENCY"),
        ("heading", "FAIL-CLOSED"),
        ("motivation, not a contract claim",
         "A Herdr the Runtime starts in a freshly materialized"),
        ("motivation, not a contract claim",
         "A Herdr the Runtime starts in a freshly materialized"
         " managed workspace is an interactive TTY session"),
        ("quotes the vendor's own diagnostic",
         "accept the trust dialog, or set"),
        ("cross-reference to the evidence artifact",
         "see the I1 evidence artifact"),
        # One-line docstring SUMMARIES. Each names what a callable is,
        # and each contract it states is carried by a row above on
        # the body paragraph that states it. Listed individually
        # rather than matched by a rule, so adding a summary that
        # smuggles in a new contract still fails this test.
        ("summary line",
         "Workspace trust for the ONE managed workspace DI just"
         " materialized."),
        ("section lead-in, no contract claim",
         "This module is a security surface, not a convenience."),
        ("section lead-in, no contract claim",
         "The config file is shared with each live Claude session"),
        ("summary line", "The user-global Claude config the CLI"
                         " reads."),
        ("summary line", "The REAL path of the configuration file."),
        ("summary line", "The config key the CLI looks a directory up"
                         " under."),
        ("summary line", "Read-only: does the config record trust"),
        ("summary line", "The CLI's own lock protocol: an atomic"),
        ("summary line", "The one path establishment may target, or a"
                         " refusal."),
        ("summary line", "Record trust for THIS workflow's own"
                         " materialized workspace."),
    )

    @staticmethod
    def added_claims(document):
        """The independently fingerprinted current normative domain."""
        current = doc_text(document)
        if document == MODULE_DOC:
            return [
                unit for unit in document_units(current)
                if len(unit) >= 40
            ]
        units, missing = protected_document_units(
            current, I1_DOCUMENT_UNIT_DIGESTS[document],
            document_units,
        )
        if missing:
            raise AssertionError(
                "%s lost or changed protected normative unit(s): %s"
                % (document, missing)
            )
        return units

    def test_every_added_normative_sentence_has_a_row(self):
        """The DOCUMENT -> MAP direction (round-01 B4).

        Without this, a normative sentence added tomorrow with no row
        is undetectable and the map silently stops being exhaustive.

        Textual by nature — an unpinned SENTENCE is the defect — and
        fast structural feedback in front of the `doc<->code` methods
        below, which are the executed guarantees.
        """
        covered = 0
        unpinned = []
        for document in ("SECURITY.md", "OPERATOR_PROTOCOL.md",
                         MODULE_DOC):
            claims = self.added_claims(document)
            self.assertTrue(
                claims,
                "derived NO added text for %s — the baseline is"
                " wrong, so this exhaustiveness check is vacuous"
                % document,
            )
            rows = [flat(row[2]) for row in CLAIM_PIN_MAP
                    if row[0] == document]
            self.assertTrue(rows, "no rows at all for %s" % document)
            for claim in claims:
                flat_claim = flat(claim)
                if any(row in flat_claim for row in rows):
                    covered += 1
                    continue
                if any(flat(phrase) in flat_claim
                       for _, phrase in
                       self.NON_NORMATIVE_EXEMPTIONS):
                    continue
                unpinned.append("%s: %s" % (document, claim[:150]))
        self.assertEqual(
            unpinned, [],
            "UNPINNED NORMATIVE SURFACE (%d). Each needs a"
            " claim->pin row, or an exemption naming why it carries"
            " no contract claim:\n  %s"
            % (len(unpinned), "\n  ".join(unpinned)),
        )
        self.assertGreater(
            covered, 0, "vacuous: nothing was actually covered"
        )

    def test_the_permanent_claim_detector_bites_without_self_observation(self):
        rows = [flat(row[2]) for row in CLAIM_PIN_MAP]
        invented = (
            "This synthetic normative statement has no independent"
            " claim-to-pin row and therefore must be rejected."
        )
        self.assertFalse(
            any(row in flat(invented) for row in rows),
            "the synthetic unmapped claim was accepted",
        )
        # Describing the claim in this test cannot provide document evidence:
        # only the real mapped document is passed to the unit selector.
        expected = {hashlib.sha256(flat(invented).encode()).hexdigest()}
        _units, missing = protected_document_units(
            doc_text("SECURITY.md"), expected, document_units,
        )
        self.assertEqual(missing, sorted(expected))

    def test_the_map_labels_are_closed(self):
        self.assertEqual(
            {row[1] for row in CLAIM_PIN_MAP},
            {"doc<->code", "fact-only"},
        )

    # -- the five doc<->code rows ----------------------------------

    def test_doc_key_name_is_the_key_production_writes(self):
        """EXECUTED PIN: parses the document, then runs `establish` and asserts on the key list actually written.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"writes exactly one key,\s*`([A-Za-z]+)`",
            re.sub(r"\s+", " ", doc_text("SECURITY.md")),
        )
        self.assertIsNotNone(match, "SECURITY.md no longer names a key")
        parsed = match.group(1)
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        document = json.loads(
            read_bytes(self.config).decode("utf-8")
        )
        entry = document["projects"][
            trust_module.trust_key(self.lease)
        ]
        self.assertEqual(
            list(entry), [parsed],
            "the document names %r but production wrote %r"
            % (parsed, list(entry)),
        )

    def test_doc_boundary_derivation_is_the_one_production_uses(self):
        """EXECUTED PIN: parses the document, then resolves the named function and runs it against production's accepted path.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"`workspace\.([a-z_]+)\(workspaces_root, workflow_id\)`",
            re.sub(r"\s+", " ", doc_text("SECURITY.md")),
        )
        self.assertIsNotNone(match)
        derive = getattr(workspace_module, match.group(1))
        accepted, problem, _ = trust_module.resolve_managed_target(
            self.entry(), self.workspaces
        )
        self.assertIsNone(problem)
        self.assertEqual(
            accepted,
            os.path.realpath(derive(self.workspaces,
                                    self.workflow_id)),
        )

    def test_doc_lock_path_is_the_one_production_creates(self):
        """EXECUTED PIN: parses the document, then records the real `os.mkdir` argument production issues.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"holding\s*`<config>(\.[a-z]+)`",
            re.sub(r"\s+", " ", doc_text("SECURITY.md")),
        )
        self.assertIsNotNone(match)
        parsed_suffix = match.group(1)
        observed = []
        real_mkdir = os.mkdir

        def recording_mkdir(path, *args, **kwargs):
            observed.append(path)
            return real_mkdir(path, *args, **kwargs)

        os.mkdir = recording_mkdir
        self.addCleanup(setattr, os, "mkdir", real_mkdir)
        ok, _, _ = self.establish()
        self.assertTrue(ok)
        self.assertIn(
            self.config + parsed_suffix, observed,
            "the document names %r but production locked %r"
            % (self.config + parsed_suffix, observed),
        )

    def test_doc_receipt_prefix_is_the_one_production_writes(self):
        """EXECUTED PIN: parses the document, then builds a real refusal receipt and asserts on its summary.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"summary begins\s*`([^`]+)`",
            re.sub(r"\s+", " ", doc_text("OPERATOR_PROTOCOL.md")),
        )
        self.assertIsNotNone(match)
        parsed = match.group(1)
        receipt = trust_module.trust_block_receipt(
            trust_module.PROBLEM_CONFIG_MISSING,
            now="2026-08-28T00:00:00Z",
        )
        self.assertTrue(
            receipt["bounded_summary"].startswith(parsed),
            "the document says receipts begin %r; production wrote %r"
            % (parsed, receipt["bounded_summary"]),
        )

    def test_three_part_boundary_key_is_the_key_production_writes(self):
        """EXECUTED PIN: on the I8 three-part boundary block, parses WHAT IT DOES, then runs `establish` and asserts on the key list actually written.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"writes the single `([A-Za-z]+)` key into one `projects`"
            r" entry",
            re.sub(r"\s+", " ", doc_text("SECURITY.md")),
        )
        self.assertIsNotNone(
            match, "the three-part boundary block no longer names a key"
        )
        parsed = match.group(1)
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        document = json.loads(
            read_bytes(trust_module.resolve_config_path(self.config))
            .decode("utf-8")
        )
        entry = document["projects"][trust_module.trust_key(self.lease)]
        self.assertEqual(
            list(entry), [parsed],
            "the block names %r but production wrote %r"
            % (parsed, list(entry)),
        )

    def test_three_part_failure_receipt_is_the_one_production_writes(self):
        """EXECUTED PIN: on HOW IT FAILS CLOSED, parses the receipt prefix and the phase name out of the block, then builds a real refusal receipt and reads the phase off production.
        Falsifying either half of the sentence fails this test."""
        text = re.sub(r"\s+", " ", doc_text("SECURITY.md"))
        prefix = re.search(
            r"appends a receipt whose summary begins `([^`]+)`", text
        )
        phase = re.search(
            r"moves the workflow to the terminal ([A-Z_]+) phase", text
        )
        self.assertIsNotNone(prefix, "the block names no receipt prefix")
        self.assertIsNotNone(phase, "the block names no terminal phase")
        receipt = trust_module.trust_block_receipt(
            trust_module.PROBLEM_CONFIG_MISSING,
            now="2026-08-28T00:00:00Z",
        )
        self.assertTrue(
            receipt["bounded_summary"].startswith(prefix.group(1)),
            "the block says receipts begin %r; production wrote %r"
            % (prefix.group(1), receipt["bounded_summary"]),
        )
        parsed_phase = phase.group(1)
        self.assertIn(parsed_phase, record_module.PHASES)
        self.assertIn(
            parsed_phase, record_module.TERMINAL_PHASES,
            "the block calls %r terminal; production does not"
            % parsed_phase,
        )
        self.assertEqual(parsed_phase, record_module.PHASE_BLOCKED)

    def test_operator_doc_key_name_is_the_key_production_writes(self):
        """EXECUTED PIN: parses the document, then runs `establish` and asserts on the key list actually written.
        Falsifying the sentence fails this test."""
        match = re.search(
            r"the single `([A-Za-z]+)` key of that one entry",
            re.sub(r"\s+", " ", doc_text("OPERATOR_PROTOCOL.md")),
        )
        self.assertIsNotNone(
            match, "OPERATOR_PROTOCOL.md no longer names a key"
        )
        ok, problem, detail = self.establish()
        self.assertTrue(ok, "%s: %s" % (problem, detail))
        document = json.loads(
            read_bytes(trust_module.resolve_config_path(self.config))
            .decode("utf-8")
        )
        self.assertEqual(
            list(document["projects"][
                trust_module.trust_key(self.lease)
            ]),
            [match.group(1)],
        )

    def test_doc_revocation_matches_the_present_revocation_surface(self):
        """EXECUTED PIN, INVERTED IN I5 — and the inversion is the
        point.

        Source is the only feasible level for the document half of
        this check and the reason is that its subject IS the text of
        two documents; the behavioural half it fronts is
        `TrustRevocationTests` and
        `UnrelatedResourceTests.test_release_revokes_only_its_own_trust_entry`,
        which execute the revocation this text describes.

        In I1 this asserted the OPPOSITE: no revocation surface
        existed, both documents said the grant was permanent, and the
        test failed if code grew revocation without the sentence being
        retracted in the same edit. I5 added revocation, this test
        failed exactly as designed, and the sentences in
        `workspace_trust.py` and `SECURITY.md` were retracted in the
        same change. It now pins the new direction: the surface EXISTS
        and the release path uses it, so removing revocation while the
        documents advertise it fails here.
        """
        surface = [
            name for name in dir(trust_module)
            if not name.startswith("_")
            and callable(getattr(trust_module, name))
        ]
        self.assertIn(
            "revoke", surface,
            "workspace_trust lost its revocation surface while both"
            " documents say the grant is revoked at release —"
            " retract the sentence in the same edit",
        )
        release_source = inspect.getsource(
            broker_module.TargetBroker._release
        )
        self.assertIn(
            "workspace_trust_module", release_source,
            "release no longer touches trust; the revocation"
            " disclosure is stale",
        )
        # And both documents must carry the disclosure's SUBSTANCE.
        # Round-05 mutant U07 retitled the section "not permanent at
        # all" and still contained the word "permanent", so the word
        # alone is not the check.
        # Both documents must carry the new disclosure's SUBSTANCE,
        # not merely the word "revoke". Round-05 mutant U07 retitled a
        # section and still contained the key word, # so within this test the word alone is not the check; the same
        # rule applies to the inverted direction.
        required = (
            "revoked at release",        # the claim itself
            "own lease realpath",        # the boundary it holds within
            "read-back",                 # the discipline it inherits
            "idempotent",                # the restart property
        )
        for document in ("SECURITY.md", MODULE_DOC):
            flat_text = flat(doc_text(document))
            for phrase in required:
                with self.subTest(document=document, phrase=phrase):
                    self.assertIn(
                        flat(phrase), flat_text,
                        "%s no longer discloses %r — the grant still"
                        " outlives the workspace, so the disclosure"
                        " may not be retracted" % (document, phrase),
                    )

    def test_doc_cli_version_is_the_one_the_module_derived_from(self):
        """Parses both documents and compares the version against the
        production constant — a text-to-constant comparison, NOT a
        drive of production, so it is fast structural feedback in
        front of `RealCliConsumedEffectTests`, which drives the
        actual installed binary and fails if the gate changes.
        Falsifying the sentence fails this test."""
        # Both operator-facing documents must name the SAME CLI
        # version the code says its vendor facts came from. Editing
        # one without the other fails here.
        for document, pattern in (
            ("SECURITY.md", r"\*\*`claude ([0-9.]+)`\*\*"),
            ("OPERATOR_PROTOCOL.md", r"derived from `claude ([0-9.]+)`"),
        ):
            with self.subTest(document=document):
                match = re.search(
                    pattern, re.sub(r"\s+", " ", doc_text(document))
                )
                self.assertIsNotNone(
                    match, "%s no longer names a CLI version"
                    % document,
                )
                self.assertEqual(
                    match.group(1), trust_module.CLI_DERIVED_VERSION,
                )

    def test_doc_blocked_phase_is_the_one_production_reaches(self):
        """Parses the document and compares the phase name against
        the record module's constants — a text-to-constant
        comparison, NOT a drive of production, so it is fast
        structural feedback in front of
        `BrokerOrderingTests.test_a_trust_failure_blocks_the_workflow_durably`,
        which asserts the phase actually reached, read back from
        disk. Falsifying the sentence fails this test."""
        match = re.search(
            r"durable ([A-Z_]+) workflow",
            doc_text("OPERATOR_PROTOCOL.md"),
        )
        self.assertIsNotNone(match)
        parsed = match.group(1)
        self.assertIn(parsed, record_module.PHASES)
        self.assertIn(parsed, record_module.TERMINAL_PHASES)
        self.assertEqual(parsed, record_module.PHASE_BLOCKED)



if __name__ == "__main__":
    unittest.main()
