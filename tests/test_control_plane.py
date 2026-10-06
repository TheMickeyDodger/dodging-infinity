import contextlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from herdr.control_plane import (
    ChildHistoryError,
    ChildRecordDurabilityUnknown,
    ChildRecordLockUnknown,
    ChildRecordNotAppended,
    HerdrControlPlane,
)


class HerdrControlPlaneTests(unittest.TestCase):
    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)

        herd = repo / ".herd"
        herd.mkdir()

        (herd / "herd.config.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "project": {"name": "test"},
                    "policy": {
                        "rules": [],
                        "git": {
                            "commit": "require-human",
                            "push": "require-human",
                        },
                    },
                }
            )
        )

        return temp, repo

    def test_instance(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()
        herd = cp.instance(repo)

        self.assertEqual(herd.repo, repo.resolve())

    def test_policy(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        self.assertEqual(
            cp.policy(repo).get("git", "push"),
            "require-human",
        )

    def test_set_policy(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()
        cp.set_policy(repo, "git.push", "forbidden")

        self.assertEqual(
            cp.policy(repo).get("git", "push"),
            "forbidden",
        )

    def test_spawn_composes_policy_start_and_task(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        from unittest.mock import patch

        with (
            patch.object(
                cp,
                "start",
                return_value={
                    "workspace_id": "ws1",
                },
            ) as mock_start,
            patch.object(
                cp,
                "dispatch_task",
                return_value={
                    "id": "task1",
                    "status": "ACTIVE",
                },
            ) as mock_task,
        ):
            result = cp.spawn(
                repo,
                task="Investigate anomaly",
                policy={
                    "rules": [
                        "Do not touch docs",
                    ],
                    "git": {
                        "push": "forbidden",
                    },
                },
            )

        mock_start.assert_called_once_with(
            repo.resolve(),
            force=False,
        )

        mock_task.assert_called_once_with(
            repo.resolve(),
            "Investigate anomaly",
            rejection_drill=False,
            task_policy=None,
        )

        self.assertEqual(
            result["runtime"]["workspace_id"],
            "ws1",
        )

        self.assertEqual(
            result["task"]["status"],
            "ACTIVE",
        )

        self.assertEqual(
            result["policy"]["git"]["push"],
            "forbidden",
        )

        self.assertIn(
            "Do not touch docs",
            result["policy"]["rules"],
        )

    def test_spawn_auto_initializes_fresh_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        repo = Path(temp.name)
        cp = HerdrControlPlane()

        from unittest.mock import patch

        def initialize_side_effect(
            target,
            **kwargs,
        ):
            herd = Path(target) / ".herd"
            herd.mkdir(parents=True)

            (
                herd
                / "herd.config.json"
            ).write_text(
                json.dumps(
                    {
                        "version": 4,
                        "project": {
                            "name": "test",
                        },
                        "policy": {
                            "rules": [],
                            "git": {
                                "commit": "require-human",
                                "push": "require-human",
                            },
                            "review": {
                                "required": True,
                                "max_rounds": 5,
                            },
                            "scope": {
                                "allowed": [],
                                "blocked": [],
                            },
                        },
                    }
                )
            )

            return {
                "repo": str(target),
                "created": True,
            }

        with (
            patch.object(
                cp,
                "initialize",
                side_effect=initialize_side_effect,
            ) as mock_initialize,
            patch.object(
                cp,
                "start",
                return_value={
                    "workspace_id": "ws1",
                },
            ),
            patch.object(
                cp,
                "dispatch_task",
                return_value={
                    "id": "task1",
                    "status": "ACTIVE",
                },
            ),
        ):
            result = cp.spawn(
                repo,
                task="Do something",
                preset="max-quality",
            )

        mock_initialize.assert_called_once()

        self.assertEqual(
            result["task"]["status"],
            "ACTIVE",
        )

    def test_rule_management(self):
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        cp = HerdrControlPlane()

        cp.add_rule(repo, "Do not touch docs")
        self.assertIn(
            "Do not touch docs",
            cp.policy(repo).rules,
        )

        cp.remove_rule(repo, "Do not touch docs")
        self.assertNotIn(
            "Do not touch docs",
            cp.policy(repo).rules,
        )


class ChildHistoryWriterTests(unittest.TestCase):
    """Task 8 R19-1: the REAL writer (``HerdrControlPlane.spawn_child``;
    only the innermost ``spawn`` is doubled, recording each invocation)
    never resets, loses or half-writes the parent's child-spawn history.
    Two outcomes are distinct: a PRE-EFFECT refusal (``ChildHistoryError``:
    ``spawn`` never invoked, the file untouched) and a POST-SPAWN partial
    effect (``ChildRecordNotAppended``: the child spawned, no record
    appended or synthesised, the file left as it was)."""

    LEASE = "/leases/wf-legacy"

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.parent = self.base / "parent"
        state = self.parent / ".herd" / "state"
        state.mkdir(parents=True)
        (self.parent / ".herd" / "herd.config.json").write_text(
            json.dumps({"version": 4}))
        (state / "runtime.json").write_text(
            json.dumps({"agents": {}, "panes": {}}))
        self.children = state / "children.json"
        # The writer names the history by the parent's RESOLVED path.
        self.history = self.children.resolve()
        self.spawned = []

    def spawn_double(self, repo, *, task, **kwargs):
        """``HerdrControlPlane.spawn`` (patched in as this BOUND method, so no
        control plane is passed)."""
        self.spawned.append(Path(repo).name)
        name = Path(repo).name
        return {"repo": str(repo), "initialization": None,
                "runtime": {"workspace_id": "ws-" + name,
                            "agents": {"supervisor": "sup-" + name}},
                "task": {"id": "20260924-12%04d-%s" % (len(self.spawned), name[:6]),
                         "status": "ACTIVE"},
                "policy": {}}

    def spawn(self, name):
        with mock.patch.object(HerdrControlPlane, "spawn", self.spawn_double):
            return HerdrControlPlane().spawn_child(
                str(self.parent), str(self.base / name), task="work " + name)

    def names(self):
        return [Path(record["repo"]).name
                for record in json.loads(self.children.read_text())["children"]]

    @staticmethod
    def legacy_record(repo, task_id="20260901-000000-aaaaaa", **fields):
        return dict({"requested_at": 1000, "parent_repo": "/p", "parent_task_id": None,
                     "dependency": False, "repo": repo, "task_id": task_id,
                     "task_status": "COMPLETE", "workspace_id": "ws-legacy",
                     "agents": {"supervisor": "legacy-sup"}}, **fields)

    def write(self, raw):
        self.children.write_bytes(raw)
        return raw

    # -- valid legacy history is preserved --------------------------------------

    def test_CH1_valid_legacy_history_is_kept_exactly_and_appended_to(self):
        """Every existing record, extra fields and unknown top-level members
        included, survives unchanged in value; one record is appended. A
        record with malformed IDENTITY fields (``task_id`` None — the writer
        itself records one for a task without an id) is kept: the scoped
        reader refuses it only for its own lease."""
        legacy = {"version": 1, "note": "kept", "children": [
            self.legacy_record(self.LEASE, extra={"nested": [1, 2.5, None]}),
            self.legacy_record("/leases/wf-no-id", task_id=None),
        ]}
        self.write(json.dumps(legacy).encode())       # a compact legacy layout
        self.spawn("child-a")
        after = json.loads(self.children.read_text())
        self.assertEqual(after["children"][:2], legacy["children"])
        self.assertEqual(after["note"], "kept")
        self.assertEqual(self.names(), ["wf-legacy", "wf-no-id", "child-a"])
        self.assertEqual(self.spawned, ["child-a"])

    def test_CH3_a_missing_history_starts_with_the_one_record(self):
        self.spawn("child-a")
        self.assertEqual(json.loads(self.children.read_text())["version"], 1)
        self.assertEqual(self.names(), ["child-a"])

    # -- PRE-EFFECT refusal: nothing spawned, the file untouched ----------------

    def refused_before_any_spawn(self, detail):
        with self.assertRaises(Exception) as refused:
            self.spawn("child-a")
        self.assertIsInstance(refused.exception, ChildHistoryError)
        self.assertNotIsInstance(refused.exception, ChildRecordNotAppended)
        self.assertEqual(self.spawned, [])
        self.assertEqual(str(refused.exception),
                         "Child-spawn history %s%s; it is left as it is on disk."
                         " Nothing was spawned and nothing appended."
                         % (self.history, detail))

    def test_CH2_an_unusable_history_refuses_before_any_spawn(self):
        record = json.dumps(self.legacy_record(self.LEASE))
        cases = (
            ("invalid JSON", b'{"version": 1, "children": [',
             " is not valid JSON (Expecting value: line 1 column 29 (char 28))"),
            ("an empty file", b"",
             " is not valid JSON (Expecting value: line 1 column 1 (char 0))"),
            ("a duplicate children member",
             ('{"children": [%s], "children": []}' % record).encode(),
             " is not valid JSON (duplicate member 'children')"),
            ("not an object", b"[]", " is not a `children` list document"),
            ("children not a list", b'{"children": {}}', " is not a `children` list document"),
            ("no children", b'{"version": 1}', " is not a `children` list document"),
            ("undecodable bytes", b'{"children": [], "x": "\xff"}',
             " is unreadable (UnicodeDecodeError)"),
            ("a scalar record", ('{"children": [%s, 7]}' % record).encode(),
             ": record 1 is not a JSON object"),
            ("a null record", b'{"children": [null]}', ": record 0 is not a JSON object"),
            ("a record naming no repository",
             json.dumps({"children": [self.legacy_record(None)]}).encode(),
             ": record 0 names no repository, so its relevance to any lease cannot be decided"),
            ("a blank repository",
             json.dumps({"children": [self.legacy_record("  ")]}).encode(),
             ": record 0 names no repository, so its relevance to any lease cannot be decided"),
        )
        for label, raw, detail in cases:
            with self.subTest(label):
                self.spawned = []
                self.write(raw)
                self.refused_before_any_spawn(detail)
                self.assertEqual(self.children.read_bytes(), raw)
        with self.subTest("unreadable"):
            self.spawned = []
            raw = self.write(json.dumps({"children": []}).encode())
            os.chmod(str(self.children), 0)
            try:
                self.refused_before_any_spawn(" is unreadable (PermissionError)")
            finally:
                os.chmod(str(self.children), 0o644)
            self.assertEqual(self.children.read_bytes(), raw)
        with self.subTest("a directory"):
            self.spawned = []
            self.children.unlink()
            self.children.mkdir()
            self.refused_before_any_spawn(" is unreadable (IsADirectoryError)")
            self.assertTrue(self.children.is_dir())

    def test_CH2b_the_writer_refuses_exactly_what_the_scoped_reader_refuses_for_every_lease(self):
        """The RECORD contract, stated against its consumer: the scoped
        reader (``observe_spawn_records`` with ``relevant``, the ownership
        routes' read) refuses the WHOLE history, for a lease no record names,
        on exactly the records the writer refuses; a record malformed only in
        its identity fields is refused by that reader for its OWN lease alone,
        and the writer keeps it. (Finite cases, not a proof over every file.)"""
        from herdr.observe import observe_spawn_records
        unrelated = lambda repo: repo == "/leases/nobody"                # noqa: E731
        own = lambda repo: repo == "/leases/wf-no-id"                     # noqa: E731
        refused = [
            [7], [None], ["x"],
            [self.legacy_record(None)], [self.legacy_record("  ")],
            [self.legacy_record(7)],
        ]
        for records in refused:
            with self.subTest(records=repr(records)[:60]):
                self.spawned = []
                raw = self.write(json.dumps({"children": records}).encode())
                self.assertEqual(
                    observe_spawn_records(str(self.parent), relevant=unrelated)["state"],
                    "malformed")
                with self.assertRaises(Exception) as refused:
                    self.spawn("child-a")
                self.assertIsInstance(refused.exception, ChildHistoryError)
                self.assertEqual((self.spawned, self.children.read_bytes()), ([], raw))
        kept = [self.legacy_record("/leases/wf-no-id", task_id=None)]
        self.spawned = []
        self.write(json.dumps({"children": kept}).encode())
        self.assertEqual(
            observe_spawn_records(str(self.parent), relevant=unrelated)["state"], "empty")
        self.assertEqual(observe_spawn_records(str(self.parent), relevant=own)["state"],
                         "malformed")
        self.spawn("child-a")
        self.assertEqual(self.names(), ["wf-no-id", "child-a"])

    # -- POST-SPAWN partial effect: the child exists, its record does not -------

    def test_CH4_a_history_unusable_after_the_spawn_is_a_partial_effect(self):
        before = self.write(json.dumps({"children": [self.legacy_record(self.LEASE)]}).encode())
        corrupted = before[:-3]

        def spawn(plane, repo, **kwargs):
            result = self.spawn_double(repo, **kwargs)
            self.children.write_bytes(corrupted)      # another writer, mid-spawn
            return result
        with mock.patch.object(HerdrControlPlane, "spawn", spawn):
            with self.assertRaises(Exception) as partial:
                HerdrControlPlane().spawn_child(
                    str(self.parent), str(self.base / "child-a"), task="work")
        self.assertIsInstance(partial.exception, ChildRecordNotAppended)
        self.assertNotIsInstance(partial.exception, ChildHistoryError)
        self.assertEqual(self.spawned, ["child-a"])
        self.assertEqual(self.children.read_bytes(), corrupted)       # left as it was
        self.assertEqual(str(partial.exception), (
            "PARTIAL EFFECT: child task 20260924-120001-child- (workspace ws-child-a) WAS"
            " spawned at %s; whether it is still running is not known here. Its record"
            " was NOT appended to %s: the failure came before the new history replaced the"
            " old one, which is left as it was on disk (Child-spawn history %s is not"
            " valid JSON (Expecting ',' delimiter: line 1 column %d (char %d)); it is left"
            " as it is on disk.). No record was synthesised: its ownership is not provable"
            " from the history."
            % (self.base.resolve() / "child-a", self.history, self.history,
               len(corrupted) + 1, len(corrupted))))

    def test_CH8_a_new_record_naming_no_repository_is_never_appended(self):
        """A ``spawn`` result naming no repository would give the NEW record
        no ``repo`` — appending it would make the whole history unreadable to
        every lease. It is not appended (a partial effect: the child
        spawned), and the history is left as it was."""
        before = self.write(json.dumps({"children": [self.legacy_record(self.LEASE)]}).encode())

        def spawn(plane, repo, **kwargs):
            return dict(self.spawn_double(repo, **kwargs), repo=None)
        with mock.patch.object(HerdrControlPlane, "spawn", spawn):
            with self.assertRaises(Exception) as partial:
                HerdrControlPlane().spawn_child(
                    str(self.parent), str(self.base / "child-a"), task="work")
        self.assertIsInstance(partial.exception, ChildRecordNotAppended)
        self.assertEqual(self.spawned, ["child-a"])
        self.assertEqual(self.children.read_bytes(), before)
        self.assertIn("(The new child record names no repository, so its relevance to"
                      " any lease cannot be decided; appending it would make %s unusable"
                      " to every lease, so it is left as it is on disk.)" % self.history,
                      str(partial.exception))

    # -- concurrent and interrupted appends ---------------------------------------

    def test_CH5_concurrent_appends_both_survive(self):
        """Writer A reads the history after its spawn and HOLDS that snapshot
        until writer B has had a full second to append; then A writes. Both
        records survive (B's append re-reads A's result under the lock)."""
        self.spawn("child-0")
        real_read = Path.read_text
        state = {"a_spawned": False}
        a_holds, go, errors = threading.Event(), threading.Event(), []

        def read_text(path, *args, **kwargs):
            text = real_read(path, *args, **kwargs)
            if (path.name == "children.json"
                    and threading.current_thread().name == "writer-a"
                    and state["a_spawned"] and not a_holds.is_set()):
                a_holds.set()
                go.wait(10)
            return text

        def spawn(plane, repo, **kwargs):
            if Path(repo).name == "child-a":
                state["a_spawned"] = True
            return self.spawn_double(repo, **kwargs)

        def writer(name):
            try:
                HerdrControlPlane().spawn_child(
                    str(self.parent), str(self.base / name), task="work " + name)
            except Exception as exc:                    # noqa: BLE001
                errors.append(exc)
        with mock.patch.object(HerdrControlPlane, "spawn", spawn), \
                mock.patch.object(Path, "read_text", read_text):
            a = threading.Thread(target=writer, args=("child-a",), name="writer-a")
            a.start()
            self.assertTrue(a_holds.wait(10))
            b = threading.Thread(target=writer, args=("child-b",), name="writer-b")
            b.start()
            b.join(1.0)
            go.set()
            a.join(10)
            b.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.names()), ["child-0", "child-a", "child-b"])

    def test_CH7_a_failure_after_the_replace_reports_visibility_and_unknown_durability(self):
        """The replace SUCCEEDED, then opening or syncing the directory fails:
        the new history (holding the record) is already in place. The writer
        raises ``ChildRecordDurabilityUnknown`` — never "not appended" — with
        the record's visibility as OBSERVED by reading the history back, and
        its durability unknown; nothing is undone; ``spawn`` ran exactly once."""
        real_open, real_fsync = os.open, os.fsync
        directory = str(self.history.parent)

        def failing_open(path, flags, *args, **kwargs):
            if str(path) == directory and flags == os.O_RDONLY:
                raise OSError(5, "directory open failed")
            return real_open(path, flags, *args, **kwargs)
        calls = []

        def failing_directory_fsync(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:          # the temp file's fsync, then the directory's
                raise OSError(5, "directory fsync failed")
            return real_fsync(descriptor)
        for label, patch, cause in (
                ("the directory open", mock.patch.object(os, "open", failing_open),
                 "[Errno 5] directory open failed"),
                ("the directory fsync", mock.patch.object(os, "fsync", failing_directory_fsync),
                 "[Errno 5] directory fsync failed")):
            with self.subTest(label):
                self.spawned, calls[:] = [], []
                legacy = self.legacy_record(self.LEASE)
                self.write(json.dumps({"version": 1, "children": [legacy]}).encode())
                with patch, self.assertRaises(Exception) as partial:
                    self.spawn("child-a")
                self.assertIsInstance(partial.exception, ChildRecordDurabilityUnknown)
                self.assertNotIsInstance(partial.exception, ChildRecordNotAppended)
                self.assertNotIsInstance(partial.exception, ChildHistoryError)
                self.assertEqual(self.spawned, ["child-a"])
                # The new history IS in place: the legacy record and the new one.
                self.assertEqual(json.loads(self.children.read_text())["children"][0], legacy)
                self.assertEqual(self.names(), ["wf-legacy", "child-a"])
                self.assertEqual(str(partial.exception), (
                    "PARTIAL EFFECT: child task 20260924-120001-child- (workspace ws-child-a)"
                    " WAS spawned at %s; whether it is still running is not known here. Its"
                    " record was written to %s and the new history replaced the old one; it"
                    " IS visible there (read back after the replace). Its DURABILITY is"
                    " UNKNOWN: opening or syncing the directory after the replace failed (%s)."
                    " Nothing was undone or retried."
                    % (self.base.resolve() / "child-a", self.history, cause)))

    def test_CH9_a_lock_release_failure_is_classified_by_the_phase_reached(self):
        """Releasing the history lock fails (``flock(LOCK_UN)``, injected).
        With nothing else in flight the append COMPLETED — reported as
        ``ChildRecordLockUnknown``, the record in place — never as "not
        appended". With another failure in flight, that cause is KEPT, never
        replaced by the release's: after the replace (the directory fsync
        failed) it stays durability-unknown; before it (the replace failed)
        it stays not-appended with the replace's own cause."""
        import fcntl
        real_flock, real_fsync, real_replace = fcntl.flock, os.fsync, os.replace

        def flock(descriptor, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(5, "unlock failed")
            return real_flock(descriptor, operation)
        fsyncs = []

        def directory_fsync_fails(descriptor):
            fsyncs.append(descriptor)
            if len(fsyncs) == 2:
                raise OSError(5, "directory fsync failed")
            return real_fsync(descriptor)

        def replace_fails(source, destination, *args, **kwargs):
            if os.path.basename(str(destination)) == "children.json":
                raise OSError(5, "replace failed")
            return real_replace(source, destination, *args, **kwargs)
        cases = (
            ("the append completed", [], ChildRecordLockUnknown, True,
             "Releasing the history lock afterwards failed ([Errno 5] unlock failed)"),
            ("after the replace: the directory fsync failed", [
                mock.patch.object(os, "fsync", directory_fsync_fails)],
             ChildRecordDurabilityUnknown, True,
             "opening or syncing the directory after the replace failed ([Errno 5]"
             " directory fsync failed)"),
            ("before the replace: the replace failed", [
                mock.patch.object(os, "replace", replace_fails)],
             ChildRecordNotAppended, False,
             "left as it was on disk ([Errno 5] replace failed)"),
        )
        for label, patches, expected, appended, text in cases:
            with self.subTest(label):
                self.spawned, fsyncs[:] = [], []
                legacy = self.legacy_record(self.LEASE)
                before = self.write(json.dumps({"children": [legacy]}).encode())
                with mock.patch.object(fcntl, "flock", flock), \
                        contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    with self.assertRaises(Exception) as partial:
                        self.spawn("child-a")
                self.assertIsInstance(partial.exception, expected)
                self.assertIn(text, str(partial.exception))
                self.assertNotIn("unlock failed", str(partial.exception)
                                 if expected is not ChildRecordLockUnknown else "")
                self.assertEqual(self.spawned, ["child-a"])
                if appended:
                    self.assertEqual(self.names(), ["wf-legacy", "child-a"])
                else:
                    self.assertEqual(self.children.read_bytes(), before)

    def test_CH6_an_interrupted_write_leaves_the_previous_history(self):
        """The write of the new document stops BEFORE the replace completes —
        whichever primitive writes it: a direct rewrite of ``children.json``
        stops half-way; the temp file's fsync or the replace onto
        ``children.json`` fails. The previous document stays whole and the
        record is reported NOT appended."""
        real_write_text, real_replace, real_fsync = Path.write_text, os.replace, os.fsync

        def write_text(path, data, *args, **kwargs):
            if path.name == "children.json":
                real_write_text(path, data[:len(data) // 2], *args, **kwargs)
                raise OSError(5, "interrupted")
            return real_write_text(path, data, *args, **kwargs)

        def replace(source, destination, *args, **kwargs):
            if os.path.basename(str(destination)) == "children.json":
                raise OSError(5, "interrupted")
            return real_replace(source, destination, *args, **kwargs)

        def fsync(descriptor):
            raise OSError(5, "interrupted")
        for label, patches in (
                ("the replace", [mock.patch.object(os, "replace", replace)]),
                ("the fsync", [mock.patch.object(os, "fsync", fsync)])):
            with self.subTest(label):
                self.spawned = []
                before = self.write(json.dumps(
                    {"version": 1, "children": [self.legacy_record(self.LEASE)]},
                    indent=2).encode())
                with mock.patch.object(Path, "write_text", write_text), \
                        contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    with self.assertRaises(Exception) as partial:
                        self.spawn("child-a")
                self.assertIsInstance(partial.exception, ChildRecordNotAppended)
                self.assertIn("left as it was on disk ([Errno 5] interrupted)",
                              str(partial.exception))
                self.assertEqual(self.spawned, ["child-a"])
                self.assertEqual(self.children.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
