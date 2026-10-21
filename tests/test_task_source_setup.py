"""The supported setup step for the DI-native task source (part A):
``dicatalog.py task-source``.

Every case drives the REAL operator surface (``catalog.main`` with argv, as
``python3 dicatalog.py`` runs it) over a private registry in an owned
temporary directory, and checks the registry file itself, byte for byte.
The end-to-end cases then run the REAL catalog owner with root discovery on
an ordinary refresh, over REAL on-disk herd state written as the engine
writes it and a FAKE agent listing (the fixture's tripwire fails any test
that reaches the engine). All identities are SYNTHETIC; nothing here reads
or writes the personal configuration.
"""

import contextlib
import errno
import json
import os
import re
import shlex
import shutil
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from herdr import registry as herd_registry  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import herdr_task_source as hts  # noqa: E402
import test_herdr_task_source as hts_tests  # noqa: E402
from test_mission_sources import NOW  # noqa: E402

# Stated here, independently of the code.
SOURCE_ID = "di_herdr_tasks"
PRODUCER = "herdctl"
MAX_AGE = 60
NOT_REGISTERED = "task_native_source_not_registered"
TASK = "20261021-150000-abcdef"           # SYNTHETIC task id
EXIT_OK, EXIT_USAGE, EXIT_UNAVAILABLE, EXIT_REFUSED = 0, 2, 4, 5
KEYS = {"registry", "registry_health", "registry_problem", "store_dir",
        "task_native_sources", "state", "source", "outcome", "problem",
        "detail", "action"}


def ensure_command(registry, store_dir=None):
    text = "python3 dicatalog.py task-source --registry %s" % shlex.quote(
        registry)
    if store_dir is not None:
        text += " --store-dir %s" % shlex.quote(store_dir)
    return text + " --ensure"


class SetupFixture(hts_tests.HerdrFixture):

    def setUp(self):
        super(SetupFixture, self).setUp()
        # The DI-native store: a private repos.json directory, never the
        # personal one.
        self.store = self.herd_registry({}, name="di-config")

    def task_source(self, *extra, store=True):
        argv = ["task-source", "--registry", self.registry.directory]
        if store:
            argv += ["--store-dir", self.store]
        code, out, err = self.run_main(argv + list(extra))
        report = self.one_json_line(out)
        self.assertEqual(set(report), KEYS)
        return code, report, err

    def ensure(self, store=True):
        return self.task_source("--ensure", store=store)

    def snapshot(self):
        """The registry directory exactly: every entry's name, and the
        registry file's bytes, mode, inode and mtime."""
        directory = self.registry.directory
        if not os.path.lexists(directory):
            return None
        entries = sorted(os.listdir(directory))
        path = self.registry.path
        if not os.path.lexists(path):
            return entries, None
        status = os.lstat(path)
        data = (os.readlink(path) if stat.S_ISLNK(status.st_mode)
                else open(path, "rb").read()
                if stat.S_ISREG(status.st_mode) else None)
        return entries, (data, status.st_mode, status.st_ino,
                         status.st_mtime_ns)

    def register_existing(self):
        """An EXISTING office catalog: the two documented registrations over
        readable stores (one canonical Mission, one workflow record, on
        tasks of their own), and no task-native source."""
        core = self.mission_state("mc")
        legacy = self.store_dir("legacy")
        if not os.path.exists(os.path.join(core, "missions.json")):
            self.running_mission(core, task_id="task-0001")
            self.workflow(legacy, workflow_id="wf-0002", task_id="task-0002")
        for argv in (
                ["register", "--registry", self.registry.directory,
                 "--source-id", "mission_core", "--kind",
                 contract.KIND_MISSION_CORE, "--store-dir", core,
                 "--producer", "dodging-infinity"],
                ["register", "--registry", self.registry.directory,
                 "--source-id", "workflows", "--kind",
                 contract.KIND_WORKFLOW_STORE, "--store-dir", legacy,
                 "--producer", "dodging-infinity"]):
            code, _out, err = self.run_main(argv)
            self.assertEqual(code, EXIT_OK, err)
        return core, legacy


# ---------------------------------------------------------------------------
# The check (read-only, the default) and the ensure step.
# ---------------------------------------------------------------------------

class CheckTests(SetupFixture):

    def test_an_absent_registry_is_unknown_never_empty_and_nothing_is_made(
            self):
        code, report, err = self.task_source()
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertEqual(
            (report["registry_health"], report["registry_problem"],
             report["state"], report["task_native_sources"],
             report["outcome"]),
            (contract.HEALTH_UNAVAILABLE, "registry_absent", "unknown", None,
             None))
        self.assertIsNone(self.snapshot())          # no directory was made
        self.assertIn("registry_absent", err)

    def test_an_existing_catalog_without_the_source_names_the_command(self):
        self.register_existing()
        before = self.snapshot()
        code, report, err = self.task_source()
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertEqual(
            (report["registry_health"], report["state"], report["problem"],
             report["source"], report["task_native_sources"]),
            (contract.HEALTH_FRESH, "not_registered", NOT_REGISTERED, None,
             []))
        self.assertEqual(report["action"], ensure_command(
            self.registry.directory, self.store))
        self.assertEqual(report["store_dir"], self.store)
        self.assertIn(NOT_REGISTERED, err)
        self.assertEqual(self.snapshot(), before)    # the check wrote nothing

    def test_a_registered_source_reads_registered_with_its_record(self):
        self.register_existing()
        self.ensure()
        code, report, _err = self.task_source()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual((report["state"], report["problem"],
                          report["action"]), ("registered", None, None))
        self.assertEqual(report["source"]["source_id"], SOURCE_ID)
        self.assertEqual(report["task_native_sources"], [report["source"]])

    def test_another_task_native_store_is_listed_but_not_taken_for_this(
            self):
        other = self.herd_registry({}, name="other-config")
        self.register_native(other, source_id="lab")
        code, report, _err = self.task_source()
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertEqual(report["state"], "not_registered")
        self.assertEqual([s["store_dir"] for s in
                          report["task_native_sources"]], [other])


class EnsureTests(SetupFixture):

    def records(self):
        return {s["source_id"]: s for s in self.registry.sources()}

    def test_ensure_adds_exactly_the_source_and_preserves_the_others(self):
        self.register_existing()
        kept = {k: v for k, v in self.records().items()}
        code, report, err = self.ensure()
        self.assertEqual(code, EXIT_OK, err)
        self.assertEqual((report["outcome"], report["state"],
                          report["problem"], report["action"]),
                         ("registered", "registered", None, None))
        self.assertEqual(report["source"], {
            "source_id": SOURCE_ID, "kind": contract.KIND_TASK_NATIVE,
            "store_dir": self.store, "producer": PRODUCER,
            "max_age_seconds": MAX_AGE, "registered_at": NOW})
        records = self.records()
        self.assertEqual(sorted(records),
                         sorted(list(kept) + [SOURCE_ID]))
        for source_id, record in kept.items():
            self.assertEqual(records[source_id], record)
        self.assertEqual(stat.S_IMODE(os.stat(self.registry.path).st_mode),
                         0o600)

    def test_ensure_twice_is_unchanged_and_writes_nothing(self):
        self.register_existing()
        self.ensure()
        before = self.snapshot()
        code, report, _err = self.ensure()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(report["outcome"], "unchanged")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            [s for s in self.registry.sources()
             if s["kind"] == contract.KIND_TASK_NATIVE], [report["source"]])

    def test_the_store_already_registered_under_another_id_is_adopted(self):
        """The live incident's shape: the store registered by hand earlier,
        under its own id and bound. Nothing is rewritten and no second
        source (or team) appears."""
        self.register_existing()
        self.registry.register("herd", contract.KIND_TASK_NATIVE,
                               self.store, "operator", 30, NOW - 5)
        before = self.snapshot()
        code, report, _err = self.ensure()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual((report["outcome"], report["state"]),
                         ("unchanged", "registered"))
        self.assertEqual(report["source"]["source_id"], "herd")
        self.assertEqual(report["source"]["max_age_seconds"], 30)
        self.assertEqual(self.snapshot(), before)

    def test_the_same_store_through_a_symlink_is_the_same_store(self):
        link = os.path.join(self.root, "config-link")
        os.symlink(self.store, link)
        self.registry.register("herd", contract.KIND_TASK_NATIVE, link,
                               PRODUCER, MAX_AGE, NOW)
        before = self.snapshot()
        code, report, _err = self.ensure()
        self.assertEqual((code, report["outcome"]), (EXIT_OK, "unchanged"))
        self.assertEqual(self.snapshot(), before)

    def test_a_conflicting_id_is_the_registrys_refusal_never_repaired(self):
        self.register_existing()
        elsewhere = self.herd_registry({}, name="elsewhere")
        self.registry.register(SOURCE_ID, contract.KIND_TASK_NATIVE,
                               elsewhere, PRODUCER, MAX_AGE, NOW)
        before = self.snapshot()
        code, report, err = self.ensure()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual((report["outcome"], report["problem"],
                          report["state"]),
                         ("refused", "source_id_conflict", "not_registered"))
        self.assertIn("unregister", report["action"])
        self.assertIn("refused (source_id_conflict)", err)
        self.assertEqual(self.snapshot(), before)

    def test_a_missing_registry_directory_is_refused_and_not_created(self):
        code, report, _err = self.ensure()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual((report["outcome"], report["problem"]),
                         ("refused", "registry_directory_absent"))
        self.assertIn("mkdir -m 700 %s" % shlex.quote(
            self.registry.directory), report["action"])
        self.assertIn(ensure_command(self.registry.directory, self.store),
                      report["action"])
        self.assertIsNone(self.snapshot())

    def test_a_fresh_directory_gets_the_registry_file(self):
        os.makedirs(self.registry.directory, mode=0o700)
        code, report, _err = self.ensure()
        self.assertEqual((code, report["outcome"]), (EXIT_OK, "registered"))
        self.assertEqual([s["source_id"] for s in self.registry.sources()],
                         [SOURCE_ID])

    def test_a_relative_store_dir_is_a_usage_error(self):
        code, out, err = self.run_main([
            "task-source", "--registry", self.registry.directory,
            "--store-dir", "relative/config", "--ensure"])
        self.assertEqual((code, out), (EXIT_USAGE, ""))
        self.assertIn("absolute", err)

    def test_a_store_dir_that_is_not_normalised_is_refused_unwritten(self):
        self.register_existing()
        before = self.snapshot()
        code, out, _err = self.run_main([
            "task-source", "--registry", self.registry.directory,
            "--store-dir", self.store + "/", "--ensure"])
        report = self.one_json_line(out)
        self.assertEqual((code, report["problem"]),
                         (EXIT_REFUSED, "source_invalid"))
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------
# Untrusted registries: refused, never opened for writing, never repaired.
# ---------------------------------------------------------------------------

class UntrustedRegistryTests(SetupFixture):

    def prepare(self, kind):
        self.register_existing()
        # No lock file either, so a refusal that took the registry's lock
        # (and so created it) before refusing is seen.
        os.remove(os.path.join(self.registry.directory,
                               registry_module.REGISTRY_LOCK_FILE_NAME))
        path = self.registry.path
        if kind == "open_permissions":
            os.chmod(path, 0o644)
        elif kind == "group_writable":
            os.chmod(path, 0o620)
        elif kind == "symlink":
            real = path + ".real"
            os.rename(path, real)
            os.symlink(real, path)
        elif kind == "not_regular":
            os.remove(path)
            os.mkfifo(path, 0o600)
        elif kind == "oversized":
            with open(path, "ab") as handle:
                handle.write(b" " * (registry_module.MAX_REGISTRY_BYTES + 1))
        elif kind == "malformed":
            with open(path, "wb") as handle:
                handle.write(b"{not json")
        elif kind == "incompatible":
            document = json.loads(Path(path).read_text())
            document[registry_module.REGISTRY_VERSION_KEY] = 2
            with open(path, "w") as handle:
                json.dump(document, handle)

    CASES = (("open_permissions", "registry_permissions_open",
              contract.HEALTH_UNAVAILABLE),
             ("group_writable", "registry_permissions_open",
              contract.HEALTH_UNAVAILABLE),
             ("symlink", "registry_unreadable", contract.HEALTH_UNAVAILABLE),
             ("not_regular", "registry_unreadable",
              contract.HEALTH_UNAVAILABLE),
             ("oversized", "registry_malformed", contract.HEALTH_MALFORMED),
             ("malformed", "registry_malformed", contract.HEALTH_MALFORMED),
             ("incompatible", "registry_incompatible",
              contract.HEALTH_INCOMPATIBLE))

    def test_each_is_refused_with_its_own_problem_and_left_exactly(self):
        for index, (kind, problem, health) in enumerate(self.CASES):
            with self.subTest(kind=kind):
                self.fresh_registry("untrusted-%d" % index)
                self.prepare(kind)
                before = self.snapshot()
                for argv in ((), ("--ensure",)):
                    code, report, err = self.task_source(*argv)
                    self.assertEqual(code, EXIT_REFUSED if argv
                                     else EXIT_UNAVAILABLE)
                    self.assertEqual(
                        (report["registry_problem"], report["problem"],
                         report["registry_health"], report["state"],
                         report["task_native_sources"]),
                        (problem, problem, health, "unknown", None))
                    self.assertEqual(report["outcome"],
                                     "refused" if argv else None)
                    self.assertIn(problem, err)
                    self.assertTrue(report["action"])
                    # Not opened for writing, not repaired: no lock file,
                    # same bytes, same mode, same inode, same mtime.
                    self.assertEqual(self.snapshot(), before)
                if kind == "symlink":
                    self.assertEqual(
                        json.loads(Path(self.registry.path + ".real")
                                   .read_text())["sources"][0]["source_id"],
                        "mission_core")


# ---------------------------------------------------------------------------
# Capacity: the registry's own bounds, disclosed and never bypassed.
# ---------------------------------------------------------------------------

class CapacityTests(SetupFixture):

    def test_a_full_registry_is_refused_registry_full(self):
        for index in range(registry_module.MAX_REGISTERED_SOURCES):
            self.register(source_id="s%03d" % index)
        before = self.snapshot()
        code, report, _err = self.ensure()
        self.assertEqual((code, report["problem"]),
                         (EXIT_REFUSED, "registry_full"))
        self.assertIn("unregister", report["action"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.registry.sources()),
                         registry_module.MAX_REGISTERED_SOURCES)

    def test_a_registry_at_its_byte_bound_is_refused_before_any_write(self):
        self.register_existing()
        size = os.path.getsize(self.registry.path)
        before = self.snapshot()
        with mock.patch.object(registry_module, "MAX_REGISTRY_BYTES",
                               size + 16):
            code, report, _err = self.ensure()
        self.assertEqual((code, report["problem"]),
                         (EXIT_REFUSED, "registry_serialized_size_exceeded"))
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------
# Races and operational failures at the writer: reported, never repaired.
# ---------------------------------------------------------------------------

class WriterFailureTests(SetupFixture):

    def digest(self):
        """Every byte of the registry directory (name and content), to prove nothing changed."""
        directory = self.registry.directory
        out = {}
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            out[name] = (os.readlink(path) if os.path.islink(path)
                         else open(path, "rb").read() if os.path.isfile(path)
                         else None)
        return out

    def test_a_registry_swapped_for_a_symlink_before_the_locked_read_is_reported_unreadable(self):
        """The status read sees a healthy registry; the writer's locked read then finds a symlink.
        The report must be rebuilt from THAT failure: unknown, unavailable, with its own problem,
        and nothing stale (not_registered, fresh) carried over."""
        self.register_existing()
        real_lock = registry_module.exclusive_store_lock

        @contextlib.contextmanager
        def swap_then_lock(directory, name, **kwargs):
            path = os.path.join(directory, registry_module.REGISTRY_FILE_NAME)
            os.rename(path, path + ".real")
            os.symlink(path + ".real", path)
            with real_lock(directory, name, **kwargs):
                yield

        with mock.patch.object(registry_module, "exclusive_store_lock",
                               swap_then_lock):
            code, report, err = self.ensure()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            (report["state"], report["registry_health"],
             report["registry_problem"], report["problem"],
             report["outcome"], report["task_native_sources"],
             report["source"]),
            ("unknown", contract.HEALTH_UNAVAILABLE, "registry_unreadable",
             "registry_unreadable", "refused", None, None))
        self.assertTrue(report["action"])
        self.assertIn("registry_unreadable", err)
        # not repaired: the symlink is still a symlink and the real bytes are exactly what they were
        path = self.registry.path
        self.assertTrue(os.path.islink(path))
        self.assertEqual(json.loads(Path(path + ".real").read_text())["sources"][0]["source_id"],
                         "mission_core")
        self.assertEqual([s["source_id"] for s in json.loads(Path(path + ".real").read_text())["sources"]],
                         ["mission_core", "workflows"])

    def test_a_write_failure_is_an_actionable_report_and_the_registry_is_not_touched(self):
        cases = (
            ("lock_denied", "exclusive_store_lock",
             PermissionError(errno.EACCES, "Permission denied")),
            ("write_denied", "atomic_write_json",
             PermissionError(errno.EACCES, "Permission denied")),
            ("disk_full", "atomic_write_json",
             OSError(errno.ENOSPC, "No space left on device")),
        )
        for name, target, failure in cases:
            with self.subTest(case=name):
                self.fresh_registry("failure-" + name)
                self.register_existing()
                before = self.digest()
                with mock.patch.object(registry_module, target,
                                       side_effect=failure):
                    code, report, err = self.ensure()
                self.assertEqual(code, EXIT_REFUSED)
                self.assertEqual(
                    (report["outcome"], report["problem"], report["state"],
                     report["registry_health"], report["registry_problem"]),
                    ("failed", "registry_not_writable", "not_registered",
                     contract.HEALTH_FRESH, None))
                self.assertIn(failure.strerror, report["detail"])
                # "nothing changed" is only said because a fresh read proves the registry still reads as before
                self.assertIn("still reads as it did", report["detail"])
                self.assertIn("ls -ld %s" % shlex.quote(self.registry.directory),
                              report["action"])
                self.assertIn(ensure_command(self.registry.directory, self.store),
                              report["action"])
                self.assertIn("registry_not_writable", err)
                self.assertEqual(self.digest(), before,
                                 "nothing was written, repaired or removed")

    def test_a_failure_AFTER_the_replace_never_claims_nothing_changed(self):
        """atomic_write_json replaces the file, THEN opens and fsyncs the directory. An OSError from that
        last step arrives with the new registry already in place: the report must say what is there
        (re-read), must not say the registry is unchanged, and must stay a failure."""
        from workflow_authority import atomic as atomic_module
        self.register_existing()
        before = self.digest()
        # the first fsync (the temp file, before the replace) passes; the second (the directory) fails
        with mock.patch.object(atomic_module.os, "fsync",
                               side_effect=[None, OSError(errno.EIO, "Input/output error")]):
            code, report, err = self.ensure()
        self.assertEqual(code, EXIT_REFUSED, "still a failure, still a non-zero exit")
        self.assertEqual(
            (report["outcome"], report["problem"], report["state"],
             report["registry_health"], report["registry_problem"]),
            ("failed", "registry_write_unconfirmed", "registered",
             contract.HEALTH_FRESH, None))
        self.assertEqual(report["source"]["source_id"], SOURCE_ID, "what is actually there now")
        self.assertNotEqual(self.digest(), before, "the replacement really happened")
        self.assertEqual([s["source_id"] for s in self.registry.sources()],
                         ["di_herdr_tasks", "mission_core", "workflows"])
        for text in (report["detail"], report["action"], err):
            self.assertNotIn("nothing was changed", text)
            self.assertNotIn("still reads as it did", text)
        self.assertIn("Input/output error", report["detail"])
        self.assertIn("not rolled back", report["detail"])
        self.assertIn("python3 dicatalog.py sources --registry %s"
                      % shlex.quote(self.registry.directory), report["action"])
        self.assertIn("python3 dicatalog.py task-source --registry %s"
                      % shlex.quote(self.registry.directory), report["action"])

    def test_a_write_failure_whose_registry_cannot_then_be_read_is_explicit_uncertainty(self):
        """If the registry cannot be re-read after the failure, the report says it MAY have been updated:
        never 'registered', never 'nothing changed', and never the stale pre-write 'not_registered'."""
        self.register_existing()
        real_load = registry_module.SourceRegistry.load
        calls = []

        def load(registry, absent_is_empty=True):
            calls.append(absent_is_empty)
            if len(calls) >= 3:      # 1: the status read, 2: the writer's locked read, 3: the re-read
                raise registry_module.RegistryError(
                    registry_module.PROBLEM_REGISTRY_UNREADABLE,
                    "registry %s refused (file_is_symlink)" % registry.path)
            return real_load(registry, absent_is_empty)

        with mock.patch.object(registry_module.SourceRegistry, "load", load), \
                mock.patch.object(registry_module, "atomic_write_json",
                                  side_effect=OSError(errno.EIO, "Input/output error")):
            code, report, err = self.ensure()
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            (report["outcome"], report["problem"], report["state"],
             report["registry_health"], report["registry_problem"],
             report["source"]),
            ("failed", "registry_write_unconfirmed", "unknown",
             contract.HEALTH_UNAVAILABLE, "registry_unreadable", None))
        self.assertIn("may have been updated", report["detail"])
        self.assertIn("Inspect it", report["action"])
        for text in (report["detail"], report["action"], err):
            self.assertNotIn("nothing was changed", text)
            self.assertNotIn("still reads as it did", text)
        self.assertIn("python3 dicatalog.py sources --registry %s"
                      % shlex.quote(self.registry.directory), report["action"])

    def test_a_directory_that_vanishes_before_the_writer_acts_is_never_recreated(self):
        """The precheck sees the registry directory; it is gone before the writer takes its lock. The writer
        must REFUSE at its own boundary: the directory is not recreated, nothing else is written, and the
        report is refused / registry_directory_absent / exit 5."""
        self.register_existing()
        parent = os.path.dirname(self.registry.directory)
        real_lock = registry_module.exclusive_store_lock
        seen = []

        @contextlib.contextmanager
        def vanish_then_lock(directory, name, **kwargs):
            shutil.rmtree(directory)                  # the directory disappears AFTER the precheck
            seen.append(dict(kwargs))
            with real_lock(directory, name, **kwargs):
                yield

        entries = sorted(os.listdir(parent))
        with mock.patch.object(registry_module, "exclusive_store_lock", vanish_then_lock):
            code, report, err = self.ensure()
        self.assertEqual(seen, [{"create_directory": False}], "the writer was told never to create it")
        self.assertEqual(code, EXIT_REFUSED)
        self.assertEqual(
            (report["outcome"], report["problem"], report["state"],
             report["task_native_sources"], report["source"]),
            ("refused", "registry_directory_absent", "unknown", None, None))
        self.assertIn("mkdir -m 700 %s" % shlex.quote(self.registry.directory), report["action"])
        self.assertIn(ensure_command(self.registry.directory, self.store), report["action"])
        self.assertIn("registry_directory_absent", err)
        self.assertFalse(os.path.exists(self.registry.directory), "NOT recreated")
        self.assertEqual(sorted(os.listdir(parent)),
                         [e for e in entries if e != os.path.basename(self.registry.directory)],
                         "and nothing else was created beside it")

    def test_a_precheck_that_is_wrong_about_a_missing_directory_still_creates_nothing(self):
        """The precheck is only a courtesy: even when it wrongly says the directory exists, the writer
        itself refuses to create it."""
        parent = self.registry.directory + "-parent"
        os.makedirs(parent)
        gone = os.path.join(parent, "office-catalog")
        registry = registry_module.SourceRegistry(gone)
        # the precheck is wrong ONCE (the directory vanished just after it looked), then the world is as it is
        with mock.patch.object(catalog, "_registry_directory_exists", side_effect=[True, False]):
            report = catalog.ensure_task_source(registry, self.store, NOW)
        self.assertEqual((report["outcome"], report["problem"]), ("refused", "registry_directory_absent"))
        self.assertEqual(os.listdir(parent), [], "nothing was created")

    def test_a_write_failure_is_not_mistaken_for_a_refusal_of_the_registry(self):
        """A refusal (conflict, full) and a failure (cannot write) are different problems."""
        self.register_existing()
        with mock.patch.object(registry_module, "atomic_write_json",
                               side_effect=PermissionError(errno.EACCES, "Permission denied")):
            _code, failed, _err = self.ensure()
        self.fresh_registry("refusal")
        for index in range(registry_module.MAX_REGISTERED_SOURCES):
            self.register(source_id="s%03d" % index)
        _code, refused, _err = self.ensure()
        self.assertEqual((failed["outcome"], refused["outcome"]),
                         ("failed", "refused"))
        self.assertNotEqual(failed["problem"], refused["problem"])


# ---------------------------------------------------------------------------
# The documented default store: herdctl's own repository registry.
# ---------------------------------------------------------------------------

class DefaultStoreTests(SetupFixture):

    def test_the_default_is_where_herdctl_init_writes_repos_json(self):
        self.assertEqual(Path(catalog.default_task_store_dir()),
                         herd_registry.REGISTRY.parent)
        self.assertEqual(herd_registry.REGISTRY.name, hts.REGISTRY_FILE)

    def test_without_store_dir_the_default_is_used_and_shown(self):
        home = os.path.join(self.root, "home")
        os.makedirs(home)
        self.register_existing()
        with mock.patch.dict(os.environ, {"HOME": home}):
            expected = os.path.join(home, ".config", "dodging-infinity")
            code, report, _err = self.task_source(store=False)
            self.assertEqual((code, report["store_dir"]),
                             (EXIT_UNAVAILABLE, expected))
            # The default needs no --store-dir in the action.
            self.assertEqual(report["action"],
                             ensure_command(self.registry.directory))
            code, report, _err = self.ensure(store=False)
        self.assertEqual((code, report["outcome"]), (EXIT_OK, "registered"))
        self.assertEqual(report["source"]["store_dir"], expected)
        # Registration is a locator only: the store was never created.
        self.assertFalse(os.path.exists(expected))


# ---------------------------------------------------------------------------
# Viewer paths stay READ-ONLY: only the explicit step registers.
# ---------------------------------------------------------------------------

class ViewersStayReadOnlyTests(SetupFixture):

    def test_query_and_serve_never_register_the_source(self):
        self.register_existing()
        before = self.snapshot()
        code, _out, _err = self.run_main(
            ["query", "--registry", self.registry.directory,
             "--discover-roots", "--contract-version", "2"],
            listing_argv=self.plan(stdout=hts_tests.listing_stdout([])))
        self.assertEqual(code, EXIT_OK)
        owner = self.owner(version=2)
        catalog.serve(owner, _Lines([b'{"request": "catalog"}\n']),
                      os.open(os.devnull, os.O_WRONLY), self.clock)
        self.assertEqual(self.snapshot(), before)
        code, report, _err = self.task_source()
        self.assertEqual(report["state"], "not_registered")


class _Lines(object):
    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self, limit):
        return self.lines.pop(0) if self.lines else b""


# ---------------------------------------------------------------------------
# End to end: the supported setup, then an ORDINARY refresh of the REAL
# catalog owner discovers the synthetic team with its exact identity.
# ---------------------------------------------------------------------------

LOGICALS = hts_tests.LOGICALS
ACTIVITY = {"lead1": "working", "executor1": "idle", "reviewer1": "idle",
            "supervisor": "working"}


class EndToEndTests(SetupFixture):

    def synthetic_team(self):
        team = self.team("e2e", TASK)
        for row in team.rows:
            logical = [k for k, v in team.agents.items()
                       if v == row["name"]][0]
            row["agent_status"] = ACTIVITY[logical]
        # herdctl init's locator: the synthetic root, in the DI store.
        self.herd_registry({"e2e-repo": team.root}, name="di-config")
        return team

    def mission(self, answer, team):
        ref = "%s:%s@%s" % (SOURCE_ID, TASK, hts.root_key(team.root))
        missions = [m for m in answer["response"]["missions"]
                    if m["execution"]["task_id"] == TASK]
        return ref, missions

    def assert_discovered(self, answer, team):
        ref, missions = self.mission(answer, team)
        self.assertEqual([m["mission_ref"] for m in missions], [ref])
        [mission] = missions
        self.assertEqual(mission["source_id"], SOURCE_ID)
        self.assertEqual(mission["lifecycle"], {
            "model": hts.LIFECYCLE_MODEL, "state": "ACTIVE",
            "standing": contract.STANDING_ACTIVE})
        self.assertEqual(mission["execution"]["workspace_realpath"],
                         team.root)
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual(mission["producer"]["declared"], PRODUCER)
        members = {m["logical"]: m for m in mission["members"]}
        self.assertEqual(sorted(members), sorted(LOGICALS))
        expected_activity = {"working": contract.ACTIVITY_BUSY,
                             "idle": contract.ACTIVITY_IDLE}
        for logical, member in members.items():
            # Contract 2: the member ref is task-qualified.
            self.assertEqual(member["member_ref"],
                             "%s#%s#%s" % (ref, TASK, logical))
            self.assertEqual(member["task_id"], TASK)
            self.assertEqual(member["agent"], team.agents[logical])
            self.assertEqual(member["session"], {
                "recorded": team.sessions[logical],
                "live": team.sessions[logical]})
            self.assertEqual(member["pane_id"], team.panes[logical])
            self.assertEqual(member["classification"],
                             contract.MEMBER_PRESENT)
            # Idle stays idle; busy only where the listing says working.
            self.assertEqual(member["activity"],
                             expected_activity[ACTIVITY[logical]])
        runtime = answer["response"]["runtime_agents"]
        self.assertEqual(runtime["state"], contract.RUNTIME_AGENTS_OBSERVED)
        self.assertEqual(runtime["counts"]["represented_by_teams"], 4)
        self.assertEqual(runtime["agents"], [])

    def test_fresh_setup_then_an_ordinary_refresh_discovers_the_team(self):
        team = self.synthetic_team()
        os.makedirs(self.registry.directory, mode=0o700)
        self.register_existing()
        code, report, err = self.ensure()
        self.assertEqual((code, report["outcome"]), (EXIT_OK, "registered"),
                         err)
        owner = self.owner(team.rows, version=2)
        self.assert_discovered(self.answer(owner), team)
        # An ordinary SECOND refresh of the same owner: the same identity,
        # still one team.
        self.assert_discovered(self.answer(owner), team)

    def test_an_existing_catalog_adopts_the_source_without_a_duplicate(self):
        team = self.synthetic_team()
        self.register_existing()
        owner = self.owner(team.rows, version=2)
        before = self.answer(owner)
        _ref, missions = self.mission(before, team)
        self.assertEqual(missions, [])               # invisible before
        runtime = before["response"]["runtime_agents"]
        self.assertEqual((runtime["state"], runtime["problem"]),
                         (contract.RUNTIME_AGENTS_NOT_APPLICABLE,
                          NOT_REGISTERED))
        # What the operator sees leads to the command; the command fixes it.
        _code, check, _err = self.task_source()
        self.assertEqual(check["problem"], runtime["problem"])
        argv = shlex.split(check["action"])
        self.assertEqual(argv[:3], ["python3", "dicatalog.py", "task-source"])
        code, _out, err = self.run_main(argv[2:])
        self.assertEqual(code, EXIT_OK, err)
        # The SAME long-lived owner picks it up on its next ordinary refresh.
        self.assert_discovered(self.answer(owner), team)
        # Repeated setup: unchanged, and still exactly one team.
        code, report, _err = self.ensure()
        self.assertEqual((code, report["outcome"]), (EXIT_OK, "unchanged"))
        self.assert_discovered(self.answer(owner), team)
        self.assertEqual(
            [s["source_id"] for s in self.registry.sources()],
            ["di_herdr_tasks", "mission_core", "workflows"])

    def test_a_failed_setup_changes_no_team_and_no_mission_record(self):
        team = self.synthetic_team()
        core, _legacy = self.register_existing()
        self.running_mission(core, task_id="task-0001")
        missions_file = os.path.join(core, "missions.json")
        recorded = open(missions_file, "rb").read()
        os.chmod(self.registry.path, 0o644)            # untrusted now
        code, report, _err = self.ensure()
        self.assertEqual((code, report["problem"]),
                         (EXIT_REFUSED, "registry_permissions_open"))
        self.assertEqual(open(missions_file, "rb").read(), recorded)
        os.chmod(self.registry.path, 0o600)
        answer = self.answer(self.owner(team.rows, version=2))
        self.assertEqual(self.mission(answer, team)[1], [])


# ---------------------------------------------------------------------------
# The documented setup IS the supported command, and what the office shows
# for a missing source leads to it.
# ---------------------------------------------------------------------------

class DocumentedStepTests(SetupFixture):

    PLACEHOLDER = "/absolute/path/to/office-catalog"

    def office_section(self):
        text = (REPO_ROOT / "README.md").read_text()
        start = text.index("### 4. Open the visual office")
        return text[start:text.index("\n## ", start)]

    def documented_commands(self):
        commands = []
        for block in re.findall(r"```bash\n(.*?)```", self.office_section(),
                                re.S):
            for line in block.replace("\\\n", " ").splitlines():
                if "dicatalog.py task-source" in line:
                    commands.append(shlex.split(line))
        return commands

    def test_the_readme_step_run_as_written_ensures_the_source(self):
        commands = self.documented_commands()
        self.assertEqual(len(commands), 1, commands)
        argv = commands[0]
        self.assertEqual(argv[:3], ["python3", "dicatalog.py", "task-source"])
        self.assertIn("--ensure", argv)
        self.register_existing()
        argv = [self.registry.directory if word == self.PLACEHOLDER else word
                for word in argv[2:]]
        self.assertIn(self.registry.directory, argv)
        home = os.path.join(self.root, "home")
        os.makedirs(home)
        for expected in ("registered", "unchanged"):
            with mock.patch.dict(os.environ, {"HOME": home}):
                code, out, err = self.run_main(argv)
            report = self.one_json_line(out)
            self.assertEqual((code, report["outcome"]), (EXIT_OK, expected),
                             err)
        self.assertEqual(report["source"]["store_dir"],
                         os.path.join(home, ".config", "dodging-infinity"))

    def test_the_office_line_for_a_missing_source_is_quoted_with_it(self):
        self.register_existing()
        runtime = self.answer(self.owner(version=2))["response"][
            "runtime_agents"]
        # The office's own sentence (officeView.ts presentRuntimeAgents).
        line = "Runtime agents: %s (%s)" % (runtime["state"],
                                            runtime["problem"])
        self.assertEqual(line, "Runtime agents: not_applicable (%s)"
                         % NOT_REGISTERED)
        section = " ".join(self.office_section().split())
        self.assertIn("`%s`" % line, section)
        # ... in the paragraph that follows the documented command.
        self.assertLess(section.index("dicatalog.py task-source"),
                        section.index(line))


@unittest.skipUnless(os.environ.get("DI_EXPORT_TASK_SOURCE_SETUP"),
                     "exports the consumer fixtures only when asked")
class ExportForConsumerTests(EndToEndTests):
    """Writes the REAL answers of the adoption case (before and after the
    setup step, contract 2), NORMALIZED like every consumer fixture
    (``test_herdr_task_source.normalize_export``), for the office adapter's
    test (``server/__tests__/diTaskSourceSetup.test.ts``)."""

    def test_export(self):
        out = os.environ["DI_EXPORT_TASK_SOURCE_SETUP"]
        team = self.synthetic_team()
        self.register_existing()
        owner = self.owner(team.rows, version=2)
        temp_root = os.path.realpath(self.root)
        answers = {"before": self.answer(owner)}
        self.assertEqual(self.ensure()[0], EXIT_OK)
        answers["after"] = self.answer(owner)
        self.assert_discovered(answers["after"], team)
        digests = hts_tests.journal_digests(
            os.path.join(self.root, "stores", "mc"), temp_root)
        for name, answer in answers.items():
            answer, _ = hts_tests.normalize_export(answer, temp_root,
                                                   digests=digests)
            hts_tests.assert_root_keys_coherent(self, answer)
            with open(os.path.join(out, "task-source-setup-%s-v2.FIXTURE.json"
                                   % name), "w") as handle:
                json.dump(answer, handle, indent=2, sort_keys=True)
                handle.write("\n")


if __name__ == "__main__":
    unittest.main()
