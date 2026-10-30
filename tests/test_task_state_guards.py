"""An unreadable task file must not defeat the two guards that protect an ACTIVE task, and the two task writers must not produce the unreadable window.

WHY (canonical round 13, the pattern behind finding 2): `tasks.save_task` and the heartbeat's `_save_task` were plain `path.write_text(...)`, which
opens the file for writing (so EMPTIES it) before it writes the new content. A reader in that window got `{}`-like nothing, and `tasks.load_task` turned
an unreadable file into `{"status": "ERROR"}`, which is "not ACTIVE". Two guards read it that way:

  * `dispatch_task`: "Task ... is already ACTIVE" did not fire, so an ACTIVE task could be overwritten;
  * `clear_contexts`: "Refusing to clear contexts during an ACTIVE top-level task" did not fire, so the agents' contexts could be CLEARED mid-task.
    The owner required that sessions and contexts are preserved; this was a supported-transient path to the exact harm forbidden.

Reachable from, read from the code: `herdctl task` (ordinary dispatch into a running herd), `herdctl clear-contexts` (the guard is its only protection),
the safe-reuse follow-on (overwrite only: it passes `preserve_contexts=True`, so it never reaches `clear_contexts`), and `spawn_child` only under
`force` (`start_herd` refuses a live Supervisor otherwise).

The fix is deliberately small: ONE strict reader (`load_task_strict`) used by exactly those two reads, and the two writers made atomic with a temporary
file and a rename. Nothing else is changed; `load_task`'s sentinel stays for its other callers.

Containment: TMPDIR must be bound inside the pin (checked). Every function that could reach a real agent or CLI is replaced by a mock that FAILS the test
if it is called when the test says it must not be. Run behind the PATH stubs (tools/executor1/stubbin) as well.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from herdr import heartbeat, tasks  # noqa: E402
from herdr.instance import HerdrInstance  # noqa: E402

PIN = Path(__file__).resolve().parent.parent
AGENTS = {"supervisor": "sup1", "lead1": "lead1", "executor1": "exec1", "reviewer1": "rev1"}
ACTIVE_TASK = {"version": 1, "id": "task-active-1", "status": "ACTIVE", "description": "in flight", "started_at": 1, "heartbeat_count": 7}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


class HerdCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(str(Path(tempfile.gettempdir()).resolve()).startswith(str(PIN)), "TMPDIR must be bound inside the pin")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "herd"
        self.state = self.repo / ".herd" / "state"
        write_json(
            self.repo / ".herd" / "herd.config.json",
            {
                "version": 4,
                "orchestration": {"leads": 1, "pods": 1, "agent_task_timeout_ms": 600000},
                "context": {"clear_before_new_task": True},
                "policy": {"rules": ["rule"], "git": {"commit": "require-human", "push": "require-human"}},
            },
        )
        write_json(self.state / "runtime.json", {"version": 2, "workspace_id": "w-1", "agents": dict(AGENTS), "panes": {}})
        self.herd = HerdrInstance(self.repo)
        self.task_path = self.state / "task.json"

        def must_not(name):
            def fail(*args, **kwargs):
                raise AssertionError(f"{name} must not be reached here: {args!r}")

            return fail

        # everything that could touch a real agent or CLI fails the test unless a test replaces it on purpose
        self.agent_info = mock.Mock(side_effect=lambda name: {"status": "idle", "raw": None})
        self.prompt = mock.Mock(side_effect=must_not("prompt"))
        self.reset = mock.Mock(side_effect=must_not("send_runtime_reset"))
        for target, replacement in (
            ("herdr.tasks.agent_info", self.agent_info),
            ("herdr.tasks.prompt", self.prompt),
            ("herdr.tasks.send_runtime_reset", self.reset),
            ("herdr.tasks.run", mock.Mock(side_effect=must_not("run"))),
        ):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def put_task(self, task):
        write_json(self.task_path, task)

    def reset_state_files(self):
        return sorted(p.name for p in self.state.glob("*reset*"))


class StrictReaderTests(HerdCase):
    def test_an_absent_file_is_idle_and_a_readable_object_is_returned(self):
        self.assertEqual(tasks.load_task_strict(self.herd), {"status": "IDLE"})
        self.put_task(ACTIVE_TASK)
        self.assertEqual(tasks.load_task_strict(self.herd), ACTIVE_TASK)

    def test_an_unreadable_file_REFUSES_it_is_never_read_as_a_task_and_never_as_ERROR(self):
        for label, content in (("emptied", ""), ("partly written", '{"id": "task-act'), ("not an object", "[]"), ("null", "null"), ("a number", "3")):
            with self.subTest(file=label):
                self.task_path.write_text(content)
                with self.assertRaises(RuntimeError) as caught:
                    tasks.load_task_strict(self.herd)
                self.assertIn("unreadable", str(caught.exception))
                self.assertIn("try again", str(caught.exception))
                self.assertIn("Nothing was changed", str(caught.exception))
                self.assertEqual(self.task_path.read_text(), content, "reading never rewrites the file")

    def test_a_file_that_cannot_be_opened_refuses_and_one_that_vanishes_between_check_and_read_is_idle(self):
        self.put_task(ACTIVE_TASK)
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("denied")), self.assertRaises(RuntimeError) as caught:
            tasks.load_task_strict(self.herd)
        self.assertIn("unreadable", str(caught.exception))
        with mock.patch.object(Path, "read_bytes", side_effect=FileNotFoundError()):
            self.assertEqual(tasks.load_task_strict(self.herd), {"status": "IDLE"})

    def test_the_old_sentinel_loader_is_unchanged_for_its_other_callers(self):
        self.task_path.write_text("")
        self.assertEqual(tasks.load_task(self.herd), {"status": "ERROR", "error": "unreadable task state"})


class ClearContextsGuardTests(HerdCase):
    def test_an_unreadable_task_REFUSES_and_no_agent_is_reset_and_no_reset_record_is_written(self):
        self.put_task(ACTIVE_TASK)
        original = self.task_path.read_bytes()
        with open(self.task_path, "w"):  # what write_text does first: the file is opened for writing, and so emptied
            with self.assertRaises(RuntimeError) as caught:
                tasks.clear_contexts(self.herd)
        self.assertIn("unreadable", str(caught.exception))
        self.reset.assert_not_called()
        self.prompt.assert_not_called()
        self.assertEqual(self.reset_state_files(), [], "the guard fired before the first effect (the reset record)")
        self.assertNotEqual(original, b"")

    def test_an_ACTIVE_task_still_refuses_as_before(self):
        self.put_task(ACTIVE_TASK)
        with self.assertRaises(RuntimeError) as caught:
            tasks.clear_contexts(self.herd)
        self.assertIn("Refusing to clear contexts during an ACTIVE", str(caught.exception))
        self.reset.assert_not_called()

    def test_a_finished_task_gets_past_the_guard_the_strict_reader_is_not_over_strict(self):
        self.put_task({"version": 1, "id": "t0", "status": "COMPLETE"})
        sentinel = RuntimeError("reached the first effect after the guard")
        with mock.patch.object(tasks, "save_reset_state", side_effect=sentinel), self.assertRaises(RuntimeError) as caught:
            tasks.clear_contexts(self.herd)
        self.assertIs(caught.exception, sentinel)

    def test_an_absent_task_gets_past_the_guard(self):
        sentinel = RuntimeError("reached the first effect after the guard")
        with mock.patch.object(tasks, "save_reset_state", side_effect=sentinel), self.assertRaises(RuntimeError) as caught:
            tasks.clear_contexts(self.herd)
        self.assertIs(caught.exception, sentinel)


class DispatchGuardTests(HerdCase):
    def setUp(self):
        super().setUp()
        self.clear = mock.Mock()
        patcher = mock.patch("herdr.tasks.clear_contexts", self.clear)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.saves = []
        real_save = tasks.save_task

        def spy(herd, task):
            self.saves.append(dict(task))
            return real_save(herd, task)

        patcher = mock.patch("herdr.tasks.save_task", side_effect=spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dispatch(self, **kwargs):
        return tasks.dispatch_task(self.herd, "a new objective", **kwargs)

    def test_an_unreadable_prior_task_REFUSES_nothing_is_written_no_context_is_cleared_and_no_prompt_is_sent(self):
        for clear_before in (True, False):
            for preserve in (False, True):
                with self.subTest(clear_before_new_task=clear_before, preserve_contexts=preserve):
                    self.setUp()
                    config = json.loads((self.repo / ".herd" / "herd.config.json").read_text())
                    config["context"]["clear_before_new_task"] = clear_before
                    write_json(self.repo / ".herd" / "herd.config.json", config)
                    self.put_task(ACTIVE_TASK)
                    with open(self.task_path, "w"):  # emptied, as during a heartbeat write
                        with self.assertRaises(RuntimeError) as caught:
                            self.dispatch(preserve_contexts=preserve)
                    self.assertIn("unreadable", str(caught.exception))
                    self.assertEqual(self.saves, [], "nothing overwrote the task")
                    self.clear.assert_not_called()
                    self.prompt.assert_not_called()

    def test_the_ACTIVE_task_survives_the_transient_intact_and_the_guard_still_names_it_afterwards(self):
        self.put_task(ACTIVE_TASK)
        original = self.task_path.read_bytes()
        with open(self.task_path, "w") as writing:
            with self.assertRaises(RuntimeError):
                self.dispatch()
            writing.write(original.decode())  # the heartbeat's write completes
        self.assertEqual(self.task_path.read_bytes(), original, "the ACTIVE task was not overwritten")
        with self.assertRaises(RuntimeError) as caught:
            self.dispatch()
        self.assertIn("already ACTIVE", str(caught.exception))
        self.assertEqual(self.saves, [])

    def test_a_readable_finished_prior_still_dispatches_and_still_clears_unless_told_to_preserve(self):
        self.put_task({"version": 1, "id": "t0", "status": "COMPLETE"})
        self.prompt.side_effect = lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")
        task = self.dispatch()
        self.assertEqual(task["status"], "ACTIVE")
        self.clear.assert_called_once()
        self.clear.reset_mock()
        self.put_task({"version": 1, "id": "t0", "status": "COMPLETE"})
        self.dispatch(preserve_contexts=True)
        self.clear.assert_not_called()

    def test_an_absent_prior_task_dispatches(self):
        self.prompt.side_effect = lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")
        self.assertEqual(self.dispatch()["status"], "ACTIVE")


class AtomicWriterTests(HerdCase):
    """Both writers: a temporary file in the SAME directory, then a rename. At the instant of the rename a reader still sees the old COMPLETE file."""

    WRITERS = (("tasks.save_task", lambda herd, task: tasks.save_task(herd, task)), ("heartbeat._save_task", lambda herd, task: heartbeat._save_task(herd, task)))

    def test_during_the_replace_a_reader_sees_the_old_complete_task_and_afterwards_the_new_one(self):
        for name, write in self.WRITERS:
            with self.subTest(writer=name):
                self.put_task(ACTIVE_TASK)
                new = dict(ACTIVE_TASK, heartbeat_count=8)
                seen = {}
                real_replace = os.replace

                def probe(source, destination):
                    seen["same_directory"] = Path(source).parent == Path(destination).parent
                    seen["source_holds_new"] = json.loads(Path(source).read_text()) == new
                    seen["old_seen_by_a_reader"] = tasks.load_task_strict(self.herd)
                    return real_replace(source, destination)

                with mock.patch("os.replace", side_effect=probe):
                    write(self.herd, new)
                self.assertTrue(seen["same_directory"], "a rename across directories is not atomic")
                self.assertTrue(seen["source_holds_new"], "the new content was complete BEFORE the swap")
                self.assertEqual(seen["old_seen_by_a_reader"], ACTIVE_TASK, "at the swap a reader still sees the old COMPLETE task, never an empty file")
                self.assertEqual(json.loads(self.task_path.read_text()), new)
                self.assertEqual([p.name for p in self.state.iterdir() if ".tmp" in p.name], [], "no temporary file is left")

    def test_the_bytes_written_are_exactly_what_write_text_produced_before(self):
        for name, write in self.WRITERS:
            with self.subTest(writer=name):
                task = {"id": "x", "status": "ACTIVE", "nested": {"a": [1, 2]}, "text": "é"}
                write(self.herd, task)
                self.assertEqual(self.task_path.read_bytes(), (json.dumps(task, indent=2) + "\n").encode())

    def test_a_failed_replace_leaves_the_old_task_intact_and_readable_and_raises(self):
        # No cleanup is attempted: an `unlink` here would be a new destructive site in the ownership census (herdr/tasks.py and herdr/heartbeat.py are
        # in its production surface), and the writers are meant to be a temporary file and a rename, nothing more. A failure between the write and the
        # rename can therefore leave an inert `task.json.tmp-<pid>`: nothing reads it, and the next write by the same process replaces it.
        for name, write in self.WRITERS:
            with self.subTest(writer=name):
                self.put_task(ACTIVE_TASK)
                before = self.task_path.read_bytes()
                with mock.patch("os.replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
                    write(self.herd, dict(ACTIVE_TASK, heartbeat_count=99))
                self.assertEqual(self.task_path.read_bytes(), before)
                self.assertEqual(tasks.load_task_strict(self.herd), ACTIVE_TASK, "a reader still gets the old whole task")
                leftovers = [p.name for p in self.state.iterdir() if ".tmp" in p.name]
                self.assertTrue(all(n.startswith("task.json.tmp-") for n in leftovers), leftovers)

    def test_the_writers_add_no_destructive_call_the_ownership_census_would_have_to_account_for(self):
        # STATIC pin: herdr/tasks.py and herdr/heartbeat.py are inside tests/test_ownership.py's destructive-operation census. A writer that unlinks,
        # removes, kills or deletes would need ordering, proof and gate rows there; the atomic writers must not need any.
        import ast

        destructive = {"rmtree", "unlink", "remove", "killpg", "kill", "close_fn", "release", "revoke", "terminate"}
        for module, function in ((tasks, "save_task"), (heartbeat, "_save_task")):
            with self.subTest(writer=function):
                tree = ast.parse(Path(module.__file__).read_text())
                node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
                called = {getattr(c.func, "attr", getattr(c.func, "id", None)) for c in ast.walk(node) if isinstance(c, ast.Call)}
                self.assertEqual(called & destructive, set())
                self.assertFalse(any(isinstance(n, ast.Delete) for n in ast.walk(node)))

    def test_a_first_write_creates_the_state_directory_as_before(self):
        for name, write in self.WRITERS:
            with self.subTest(writer=name):
                fresh = Path(self.temp.name) / f"fresh-{name.split('.')[0]}"
                (fresh / ".herd").mkdir(parents=True)
                write(HerdrInstance(fresh), {"id": "n", "status": "ACTIVE"})
                self.assertEqual(json.loads((fresh / ".herd" / "state" / "task.json").read_text())["id"], "n")


if __name__ == "__main__":
    unittest.main()
