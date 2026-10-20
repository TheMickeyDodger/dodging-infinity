"""The retained startup diagnostic, PROJECTED onto the attempt it belongs to — through the real bridge and panel.

The design (no schema grows): the diagnostic ``start_herd`` retains in the child workspace is itself the durable
artifact. ``MissionBridge.startup_cause`` reads it back, server-side and read-only, through the hardened bounded
workspace reader, and ``PanelService`` puts the bounded result on the request view. Nothing is stored a second time
and the renderer reads nothing from disk.

The hazard these tests exist for is BINDING, NOT RECENCY. A diagnostic is attributed to a run only if it is bound to
that run's durable intent by identifiers the intent itself holds: the workspace realpath (the record's own recorded
repo must equal it, and its last component must be the Mission id), the stage vocabulary, and a runtime that began no
earlier than the intent was recorded. A file that does not match is ABSENT, not "probably related", and when more than
one file qualifies NONE is used: there is no newest-wins anywhere.

REAL pieces: the bridge, the panel service, Mission Core, the local request surface, ``lifecycle.establish_role_bindings``
and the retention helper (a spawn double runs them and raises what the real startup raises). SCRIPTED: Herdr's answers
for each role. No provider, no real Herdr, no transport. Nothing here is evidence of a live run.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from herdr import lifecycle  # noqa: E402
from herdr import runtime as herdr_runtime  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import start_cause  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    Fixture as BridgeFixture, raw_observation, run_request, unobserved_raw,
)
from test_mission_panel_service import PanelFixture  # noqa: E402

AGENTS = {"supervisor": "h-sup", "lead1": "h-lead1", "executor1": "h-exec1", "reviewer1": "h-rev1"}
PANES = {"supervisor": "pane-root", "lead1": "pane-lead", "executor1": "pane-executor",
         "reviewer1": "pane-reviewer", "controller": "pane-controller"}
SESSION_CANARY = "SESSION-VALUE-CANARY-7731"
PROMPT_CANARY = "PROMPT-TEXT-CANARY-5520"
PATH_CANARY = "PRIVATE-PATH-CANARY-8841"
EXCEPTION_CANARY = "EXCEPTION-MESSAGE-CANARY-2207"


def answer(agent, session=True, stable=True):
    record = {}
    if stable:
        record.update(name=agent, cwd="/repo", workspace_id="ws1", pane_id="pane-" + agent)
    if session:
        record["agent_session"] = {"value": SESSION_CANARY}
    record["prompt"] = PROMPT_CANARY
    return {"status": "idle", "raw": {"result": {"agent": record}}}


def lead_has_no_session(agent):
    return answer(agent, session=(agent != "h-lead1"))


def write_diagnostic(workspace, created_at, repo=None, stage="establish_role_bindings", mutate=None,
                     roles=None, name=None, mtime=None):
    """A diagnostic file built by the REAL record builder, placed where the real startup leaves it."""
    record = herdr_runtime.build_binding_failure_record(
        stage=stage,
        failure={"type": "BINDINGS_NOT_ESTABLISHED", "class": "BindingsNotEstablished"},
        workspace={"workspace_id": "ws1", "repo": repo if repo is not None else workspace,
                   "runtime_created_at": created_at},
        settle_seconds=30.0,
        roles=roles if roles is not None else [
            {"logical": "lead1", "agent": "h-lead1", "pane": "pane-lead",
             "reason": "the recorded binding carries no session", "session": "ABSENT"}])
    if mutate is not None:
        mutate(record)
    directory = Path(workspace) / ".herd" / "state" / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or "%s%s.json" % (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX,
                                              "t%d-%s" % (created_at, os.urandom(3).hex())))
    path.write_text(json.dumps(record, indent=1, sort_keys=True))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class BindingFailureSpawn(object):
    """What the real spawn does when startup fails after bootstrap: the roles do not bind, the real retention helper
    writes the diagnostic into the child workspace, and the typed failure is raised. No child record exists."""

    def __init__(self, clock, prober=lead_has_no_session):
        self.clock, self.prober, self.calls = clock, prober, []

    def __call__(self, parent_repo, request):
        self.calls.append((parent_repo, dict(request)))
        workspace = request["target_repo"]
        herd = SimpleNamespace(herd_root=Path(workspace) / ".herd", repo=Path(workspace))
        try:
            lifecycle.establish_role_bindings(herd, AGENTS, prober=self.prober, settle_seconds=0.0,
                                              sleeper=lambda _seconds: None)
        except lifecycle.BindingsNotEstablished as exc:
            lifecycle._retain_post_bootstrap_failure(
                exc, herd, lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS, "ws1",
                {"panes": PANES, "created_at": self.clock()})
            raise
        raise AssertionError("the scripted roles were meant not to bind")


class ProjectionFixture(BridgeFixture):

    def held_with_diagnostic(self, **overrides):
        mission_id = self.approved(**overrides)
        self.bridge._spawn = BindingFailureSpawn(self.clock)
        result = self.dispatch(mission_id)
        self.assertTrue(result.get("hold"), result)
        return mission_id

    def intent(self, mission_id):
        return self.mission(mission_id)["run"]["intent"]

    def diagnostics_dir(self, mission_id):
        return Path(self.real_ws(mission_id)) / ".herd" / "state" / "diagnostics"


class BoundProjectionTests(ProjectionFixture):

    def test_a_real_failed_startup_is_projected_with_every_affected_role_and_its_file_reference(self):
        mission_id = self.held_with_diagnostic()
        cause = self.bridge.startup_cause(mission_id)
        self.assertEqual(cause["state"], start_cause.STATE_BOUND)
        self.assertEqual(cause["kind"], "BINDINGS_NOT_ESTABLISHED")
        self.assertEqual(cause["stage"], "establish_role_bindings")
        self.assertEqual(cause["failure_class"], "BindingsNotEstablished")
        self.assertEqual(cause["roles"], [{"logical": "lead1", "session": "ABSENT",
                                           "reason": "the recorded binding carries no session"}])
        self.assertEqual(cause["roles_total"], 1)
        self.assertFalse(cause["roles_truncated"])
        files = sorted(self.diagnostics_dir(mission_id).glob("*.json"))
        self.assertEqual([f.name for f in files], [cause["reference"]])
        self.assertNotIn(os.sep, cause["reference"], "a file NAME, never a path")

    def test_the_projection_names_no_path_value_prompt_or_session_id(self):
        mission_id = self.held_with_diagnostic()
        text = json.dumps(self.bridge.startup_cause(mission_id))
        for forbidden in (self.root, self.tmp.name, SESSION_CANARY, PROMPT_CANARY, "/repo", "pane-"):
            self.assertNotIn(forbidden, text)

    def test_no_diagnostic_is_the_UNKNOWN_fallback_not_a_guess(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        self.assertTrue(self.dispatch(mission_id).get("hold"))
        self.assertEqual(self.bridge.startup_cause(mission_id),
                         {"state": start_cause.STATE_NONE, "rejected": 0})

    def test_before_any_intent_there_is_no_cause_to_report(self):
        mission_id = self.approved()
        self.assertIsNone(self.bridge.startup_cause(mission_id))

    def test_a_missing_workspace_is_the_fallback_not_an_error(self):
        mission_id = self.held_with_diagnostic()
        import shutil
        shutil.rmtree(self.diagnostics_dir(mission_id))
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_NONE)

    def test_it_is_read_only_and_never_starts_anything(self):
        mission_id = self.held_with_diagnostic()
        before = (self.mission_bytes(), sorted(p.name for p in self.diagnostics_dir(mission_id).iterdir()),
                  len(self.spawn.calls), len(self.bridge._spawn.calls))
        for _ in range(3):
            self.bridge.startup_cause(mission_id)
        after = (self.mission_bytes(), sorted(p.name for p in self.diagnostics_dir(mission_id).iterdir()),
                 len(self.spawn.calls), len(self.bridge._spawn.calls))
        self.assertEqual(before, after)

    def test_a_duplicate_start_never_respawns_and_the_cause_is_unchanged(self):
        mission_id = self.held_with_diagnostic()
        first = self.bridge.startup_cause(mission_id)
        again = self.dispatch(mission_id)
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(len(self.bridge._spawn.calls), 1, "a second Start must not spawn")
        self.assertEqual(self.bridge.startup_cause(mission_id), first)


class BindingNotRecencyTests(ProjectionFixture):
    """A file is the attempt's only if the intent's own identifiers say so. Recency never chooses."""

    def held_without_diagnostic(self, **overrides):
        mission_id = self.approved(**overrides)
        self.bridge._spawn = self.spawn           # the plain recorder: it raises before any child, retains nothing
        self.spawn.raises = "before_child"
        self.dispatch(mission_id)
        return mission_id

    def test_a_diagnostic_older_than_the_intent_is_ABSENT_even_when_it_is_the_newest_file(self):
        mission_id = self.held_without_diagnostic()
        intent = self.intent(mission_id)
        stale = write_diagnostic(self.real_ws(mission_id), created_at=intent["recorded_at"] - 1,
                                 mtime=self.clock() + 5000)             # newest on disk, older than the attempt
        self.assertTrue(stale.exists())
        self.assertEqual(self.bridge.startup_cause(mission_id),
                         {"state": start_cause.STATE_NONE, "rejected": 1})

    def test_a_diagnostic_recording_another_workspace_is_ABSENT(self):
        mission_id = self.held_without_diagnostic()
        other = os.path.join(self.root, "mn-" + "f" * 32)
        write_diagnostic(self.real_ws(mission_id), created_at=self.intent(mission_id)["recorded_at"] + 1,
                         repo=other)
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_NONE)

    def test_a_diagnostic_whose_workspace_is_not_named_by_this_mission_is_ABSENT(self):
        # The workspace realpath matches the intent's, but the intent's last component is the Mission id; a record
        # that claims a repo whose last component is not this Mission's is not this attempt's.
        mission_id = self.held_without_diagnostic()
        wrong = os.path.join(os.path.dirname(self.real_ws(mission_id)), "not-this-mission")
        write_diagnostic(self.real_ws(mission_id), created_at=self.intent(mission_id)["recorded_at"] + 1,
                         repo=wrong)
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_NONE)

    def test_a_later_mission_never_inherits_an_earlier_missions_diagnostic(self):
        first = self.held_with_diagnostic(objective="first attempt")
        second = self.held_without_diagnostic(objective="second attempt")
        self.assertEqual(self.bridge.startup_cause(first)["state"], start_cause.STATE_BOUND)
        self.assertEqual(self.bridge.startup_cause(second),
                         {"state": start_cause.STATE_NONE, "rejected": 0})
        # ... and the earlier Mission's file, copied into the later Mission's workspace, is not that Mission's either
        # (its recorded workspace is the earlier one's), so even a stray copy is rejected rather than inherited.
        earlier = next(self.diagnostics_dir(first).glob("*.json"))
        later = self.diagnostics_dir(second)
        later.mkdir(parents=True)
        (later / earlier.name).write_text(earlier.read_text())
        self.assertEqual(self.bridge.startup_cause(second), {"state": start_cause.STATE_NONE, "rejected": 1})

    def test_two_qualifying_files_are_AMBIGUOUS_and_NEITHER_is_used_the_newer_does_not_win(self):
        mission_id = self.held_with_diagnostic()
        at = self.intent(mission_id)["recorded_at"] + 1
        write_diagnostic(self.real_ws(mission_id), created_at=at, mtime=self.clock() + 9000)   # clearly the newest
        cause = self.bridge.startup_cause(mission_id)
        self.assertEqual(cause, {"state": start_cause.STATE_AMBIGUOUS, "candidates": 2})
        self.assertNotIn("roles", cause)

    def test_one_qualifying_file_beside_a_newer_stale_one_is_still_the_attempts_own(self):
        mission_id = self.held_with_diagnostic()
        genuine = self.bridge.startup_cause(mission_id)
        write_diagnostic(self.real_ws(mission_id), created_at=self.intent(mission_id)["recorded_at"] - 100,
                         mtime=self.clock() + 9000, roles=[{"logical": "supervisor", "agent": "x", "pane": "y",
                                                           "reason": "a stale attempt", "session": "ABSENT"}])
        cause = self.bridge.startup_cause(mission_id)
        self.assertEqual(cause["state"], start_cause.STATE_BOUND)
        self.assertEqual(cause["reference"], genuine["reference"])
        self.assertEqual(cause["roles"], genuine["roles"], "the newer, stale file's roles must not appear")

    def test_BEHAVIOURAL_PROPERTY_the_result_depends_on_the_intent_and_the_content_never_on_the_name_or_the_time(self):
        """The property the whole design rests on. For every file NAME the writer may use and every file TIME (the
        epoch, now, the far future): a record that matches the intent's identifiers is FOUND and projects to the same
        content; a record that does not match (stale, another workspace, a stage outside the vocabulary) is ABSENT. If
        the projection chose by name order or by modification time, some cell of this grid would differ."""
        import shutil
        mission_id = self.held_without_diagnostic()
        intent, workspace = self.intent(mission_id), self.real_ws(mission_id)
        directory = self.diagnostics_dir(mission_id)
        other = os.path.join(os.path.dirname(workspace), "mn-" + "e" * 32)
        names = ("a", "m-middle", "z-last")
        times = (0, self.clock(), self.clock() + 10 ** 7)
        mismatches = (
            ("stale: began before the intent", dict(created_at=intent["recorded_at"] - 1)),
            ("another workspace", dict(created_at=intent["recorded_at"] + 1, repo=other)),
            ("a stage outside the vocabulary", dict(created_at=intent["recorded_at"] + 1, stage="somewhere_else")),
        )
        contents = set()
        for name in names:
            for moment in times:
                file_name = herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX + name + ".json"
                shutil.rmtree(directory, ignore_errors=True)
                write_diagnostic(workspace, intent["recorded_at"] + 1, name=file_name, mtime=moment)
                found = self.bridge.startup_cause(mission_id)
                self.assertEqual(found["state"], start_cause.STATE_BOUND, (name, moment))
                self.assertEqual(found["reference"], file_name)
                contents.add(json.dumps(dict(found, reference=None), sort_keys=True))
                for why, arguments in mismatches:
                    shutil.rmtree(directory, ignore_errors=True)
                    write_diagnostic(workspace, name=file_name, mtime=moment, **arguments)
                    self.assertEqual(self.bridge.startup_cause(mission_id),
                                     {"state": start_cause.STATE_NONE, "rejected": 1}, (why, name, moment))
        self.assertEqual(len(contents), 1, "the projected content varied with the file's name or time")

    def test_BEHAVIOURAL_PROPERTY_two_matching_files_are_ambiguous_whichever_is_named_last_or_written_last(self):
        import shutil
        mission_id = self.held_without_diagnostic()
        intent, workspace = self.intent(mission_id), self.real_ws(mission_id)
        directory = self.diagnostics_dir(mission_id)
        prefix = herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX
        for first_time, second_time in ((0, 10 ** 9), (10 ** 9, 0)):
            shutil.rmtree(directory, ignore_errors=True)
            write_diagnostic(workspace, intent["recorded_at"] + 1, name=prefix + "a.json", mtime=first_time)
            write_diagnostic(workspace, intent["recorded_at"] + 2, name=prefix + "z.json", mtime=second_time)
            self.assertEqual(self.bridge.startup_cause(mission_id),
                             {"state": start_cause.STATE_AMBIGUOUS, "candidates": 2}, (first_time, second_time))

    def test_STRUCTURAL_the_projection_has_no_input_or_code_path_that_could_choose_by_name_or_time(self):
        """A structural proof only: the signature and the source. It shows the function CANNOT consult a file time or
        pick a favourite; the grid above is what shows the behaviour. Neither stands in for the other."""
        import inspect
        self.assertEqual(list(inspect.signature(start_cause.project).parameters),
                         ["workspace_realpath", "mission_id", "intent_recorded_at"])
        source = Path(start_cause.__file__).read_text()
        for forbidden in ("mtime", "ctime", "atime", "utime", "os.stat", ".stat(", "getmtime", "key=", "max(", "min(",
                          "reverse"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_a_stage_outside_the_vocabulary_is_ABSENT(self):
        mission_id = self.held_without_diagnostic()
        write_diagnostic(self.real_ws(mission_id), created_at=self.intent(mission_id)["recorded_at"] + 1,
                         stage="somewhere_else")
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_NONE)

    def test_a_runtime_that_began_exactly_when_the_intent_was_recorded_is_accepted(self):
        mission_id = self.held_without_diagnostic()
        write_diagnostic(self.real_ws(mission_id), created_at=self.intent(mission_id)["recorded_at"])
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_BOUND)


class HostileFilesTests(ProjectionFixture):

    def held(self):
        mission_id = self.approved()
        self.spawn.raises = "before_child"
        self.dispatch(mission_id)
        return mission_id, self.intent(mission_id)["recorded_at"] + 1

    def assert_absent(self, mission_id, rejected=1):
        self.assertEqual(self.bridge.startup_cause(mission_id),
                         {"state": start_cause.STATE_NONE, "rejected": rejected})

    def test_malformed_json_a_wrong_schema_and_an_unknown_key_are_each_ABSENT(self):
        mission_id, at = self.held()
        directory = self.diagnostics_dir(mission_id)
        directory.mkdir(parents=True)
        (directory / (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX + "bad.json")).write_text("{not json")
        self.assert_absent(mission_id)
        write_diagnostic(self.real_ws(mission_id), at, mutate=lambda r: r.update(schema="someone_elses_v9"))
        self.assert_absent(mission_id, rejected=2)
        write_diagnostic(self.real_ws(mission_id), at, mutate=lambda r: r.update(extra=PROMPT_CANARY))
        self.assert_absent(mission_id, rejected=3)

    def test_an_unknown_key_inside_a_role_or_the_workspace_is_ABSENT_and_never_copied(self):
        mission_id, at = self.held()

        def smuggle(record):
            record["roles"][0]["transcript"] = PROMPT_CANARY
            record["workspace"]["env"] = PROMPT_CANARY
        write_diagnostic(self.real_ws(mission_id), at, mutate=smuggle)
        self.assertNotIn(PROMPT_CANARY, json.dumps(self.bridge.startup_cause(mission_id)))
        self.assert_absent(mission_id)

    def test_an_oversized_file_is_refused_by_the_hardened_reader(self):
        mission_id, at = self.held()
        path = write_diagnostic(self.real_ws(mission_id), at)
        path.write_text(path.read_text() + " " * (prepare_module.MAX_INSTRUCTION_FILE_BYTES + 10))
        self.assert_absent(mission_id)

    def test_a_symlinked_file_a_directory_and_a_symlinked_diagnostics_directory_are_ABSENT(self):
        mission_id, at = self.held()
        directory = self.diagnostics_dir(mission_id)
        directory.mkdir(parents=True)
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text(write_diagnostic(self.real_ws(mission_id), at).read_text())
        for child in directory.glob("*.json"):
            child.unlink()
        (directory / (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX + "link.json")).symlink_to(outside)
        (directory / (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX + "dir.json")).mkdir()
        self.assert_absent(mission_id, rejected=2)
        import shutil
        shutil.rmtree(directory)
        real = Path(self.tmp.name) / "elsewhere"
        real.mkdir()
        (real / (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX + "x.json")).write_text(outside.read_text())
        directory.symlink_to(real)
        self.assertEqual(self.bridge.startup_cause(mission_id)["state"], start_cause.STATE_NONE)

    def test_a_file_whose_name_is_outside_the_pattern_is_not_even_examined(self):
        mission_id, at = self.held()
        directory = self.diagnostics_dir(mission_id)
        directory.mkdir(parents=True)
        for name in ("agent-start-failure-x.json", "notes.txt", "startup-binding-failure-.json",
                     "startup-binding-failure-a b.json", "startup-binding-failure-x.json.bak"):
            (directory / name).write_text(json.dumps({"schema": herdr_runtime.BINDING_DIAGNOSTIC_SCHEMA}))
        self.assert_absent(mission_id, rejected=0)

    def test_more_files_than_the_examination_bound_is_AMBIGUOUS_not_a_slow_scan(self):
        mission_id, at = self.held()
        directory = self.diagnostics_dir(mission_id)
        directory.mkdir(parents=True)
        for index in range(start_cause.MAX_START_CAUSE_CANDIDATES + 1):
            (directory / ("%sn%03d.json" % (herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX, index))).write_text("{}")
        cause = self.bridge.startup_cause(mission_id)
        self.assertEqual(cause["state"], start_cause.STATE_AMBIGUOUS)
        self.assertEqual(cause["candidates"], start_cause.MAX_START_CAUSE_CANDIDATES + 1)

    def test_oversized_text_and_too_many_roles_are_bounded_in_the_projection(self):
        mission_id, at = self.held()
        many = [{"logical": "r%d" % i, "agent": "a", "pane": "p", "reason": "R" * 5000, "session": "ABSENT"}
                for i in range(30)]
        write_diagnostic(self.real_ws(mission_id), at, roles=many)
        cause = self.bridge.startup_cause(mission_id)
        self.assertEqual(cause["state"], start_cause.STATE_BOUND)
        self.assertEqual(len(cause["roles"]), start_cause.MAX_START_CAUSE_ROLES)
        self.assertTrue(cause["roles_truncated"])
        self.assertEqual(cause["roles_total"], 30)
        for role in cause["roles"]:
            self.assertLessEqual(len(role["reason"]), start_cause.MAX_START_CAUSE_FIELD_CHARS)
        for key in ("kind", "stage", "failure_class", "reference"):
            self.assertLessEqual(len(cause[key]), start_cause.MAX_START_CAUSE_FIELD_CHARS)

    def test_a_non_vocabulary_session_or_kind_never_leaves_the_projection(self):
        mission_id, at = self.held()
        write_diagnostic(self.real_ws(mission_id), at,
                         mutate=lambda r: r["roles"][0].update(session=SESSION_CANARY))
        self.assert_absent(mission_id)
        write_diagnostic(self.real_ws(mission_id), at,
                         mutate=lambda r: r["failure"].update(type=EXCEPTION_CANARY))
        self.assertNotIn(EXCEPTION_CANARY, json.dumps(self.bridge.startup_cause(mission_id)))


class DirectProjectionTests(unittest.TestCase):
    """The pure projection, called directly: the consistency checks a bridge-derived workspace can never violate."""

    def test_a_workspace_not_named_for_the_mission_is_never_bound_even_when_the_file_names_that_workspace(self):
        import tempfile
        mission_id = "mn-" + "a" * 32
        with tempfile.TemporaryDirectory() as raw:
            for name, expected in (("some-other-directory", start_cause.STATE_NONE), (mission_id, start_cause.STATE_BOUND)):
                workspace = os.path.join(os.path.realpath(raw), name)
                os.makedirs(workspace)
                write_diagnostic(workspace, created_at=100, repo=workspace)
                got = start_cause.project(workspace, mission_id, 100)
                self.assertEqual(got["state"], expected, name)
                if expected == start_cause.STATE_NONE:
                    self.assertEqual(got["rejected"], 1)

    def test_bad_arguments_are_the_fallback_not_an_error(self):
        for arguments in ((None, "mn-x", 1), ("/x", None, 1), ("/x", "mn-x", -1), ("/x", "mn-x", True),
                          ("/does/not/exist", "mn-x", 1)):
            self.assertEqual(start_cause.project(*arguments)["state"], start_cause.STATE_NONE, arguments)


class ConstantsTests(unittest.TestCase):

    def test_every_new_bound_is_exact_pinned_and_agrees_with_the_writer(self):
        self.assertEqual(start_cause.MAX_START_CAUSE_CANDIDATES, 40)
        self.assertEqual(start_cause.MAX_START_CAUSE_ROLES, 16)
        self.assertEqual(start_cause.MAX_START_CAUSE_FIELD_CHARS, 300)
        self.assertEqual(start_cause.DIAGNOSTIC_SUBDIRS, (".herd", "state", "diagnostics"))
        # the projection duplicates the writer's vocabulary rather than importing the process-spawning module,
        # so each shared value is asserted equal to its source
        self.assertEqual(start_cause.FILE_PREFIX, herdr_runtime.BINDING_DIAGNOSTIC_FILE_PREFIX)
        self.assertEqual(start_cause.SCHEMA, herdr_runtime.BINDING_DIAGNOSTIC_SCHEMA)
        self.assertEqual(start_cause.FAILURE_TYPES, herdr_runtime.BINDING_FAILURE_TYPES)
        self.assertEqual(start_cause.SESSION_STATES, herdr_runtime.BINDING_SESSION_STATES)
        self.assertEqual(start_cause.STAGES, (lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS,
                                              lifecycle.STAGE_HEARTBEAT_CONTROLLER))
        self.assertGreaterEqual(start_cause.MAX_START_CAUSE_CANDIDATES, herdr_runtime.DIAGNOSTIC_MAX_FILES,
                                "the examination bound must cover what retention keeps")

    def test_the_projection_reads_through_the_hardened_reader_alone(self):
        source = Path(start_cause.__file__).read_text()
        self.assertIn("read_state_artifact", source)
        for forbidden in ("open(", "subprocess", "os.system", "shutil", "unlink", "rmtree", "os.remove",
                          "os.rename", "os.replace", "chmod", "write("):
            self.assertNotIn(forbidden, source, forbidden)


class PanelProjectionTests(PanelFixture):
    """The same cause, through the actual panel service: derived at view time, never stored."""

    def started_and_failed(self, spawn=None):
        view = self.of_record()
        self.approve(view)
        self.bridge._spawn = spawn or BindingFailureSpawn(self.clock)
        self.service.run(view["request_ref"], "dispatch")
        return view

    def test_the_request_view_carries_the_bound_cause_beside_the_unchanged_hold(self):
        view = self.started_and_failed()
        held = self.service.request_view(view["request_ref"])
        self.assertEqual(held["lifecycle"]["stage"], "dispatch_uncertain")
        self.assertEqual(held["lifecycle"]["actions"], ["reconcile"])
        cause = held["start_cause"]
        self.assertEqual(cause["state"], start_cause.STATE_BOUND)
        self.assertEqual(cause["roles"][0]["logical"], "lead1")
        self.assertEqual(cause["roles"][0]["session"], "ABSENT")
        text = json.dumps(held)
        for forbidden in (self.root, self.tmp.name, SESSION_CANARY, PROMPT_CANARY):
            self.assertNotIn(forbidden, text)

    def test_the_run_answer_itself_carries_it(self):
        view = self.of_record()
        self.approve(view)
        self.bridge._spawn = BindingFailureSpawn(self.clock)
        answered = self.service.run(view["request_ref"], "dispatch")
        self.assertEqual(answered["request"]["start_cause"]["state"], start_cause.STATE_BOUND)

    def test_with_no_bound_diagnostic_the_view_says_so_plainly(self):
        view = self.of_record()
        self.approve(view)
        self.spawn.raises = "before_child"
        self.service.run(view["request_ref"], "dispatch")
        held = self.service.request_view(view["request_ref"])
        self.assertEqual(held["start_cause"], {"state": start_cause.STATE_NONE, "rejected": 0})

    def test_a_reload_or_a_second_window_sees_the_same_cause_because_the_file_is_the_store(self):
        view = self.started_and_failed()
        first = self.service.request_view(view["request_ref"])["start_cause"]
        restarted = self.build()                         # a new PanelService over the same durable state
        self.assertEqual(restarted.request_view(view["request_ref"])["start_cause"], first)

    def test_it_is_absent_unless_the_start_is_in_doubt_and_never_in_the_overview(self):
        view = self.of_record()
        self.assertIsNone(self.service.request_view(view["request_ref"])["start_cause"])
        self.approve(view)
        self.assertIsNone(self.service.request_view(view["request_ref"])["start_cause"])
        calls = []
        real = self.bridge.startup_cause
        self.bridge.startup_cause = lambda mission_id: calls.append(mission_id) or real(mission_id)
        self.bridge._spawn = BindingFailureSpawn(self.clock)
        self.service.run(view["request_ref"], "dispatch")
        calls.clear()
        overview = self.service.overview()
        self.assertEqual(calls, [], "the overview must not read the child workspace")
        self.assertNotIn("start_cause", json.dumps(overview["requests"]))

    def test_a_start_that_dispatched_normally_has_no_cause(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        shown = self.service.request_view(view["request_ref"])
        self.assertNotEqual(shown["lifecycle"]["stage"], "dispatch_uncertain")
        self.assertIsNone(shown["start_cause"])

    def test_a_failing_read_degrades_to_the_fallback_never_breaks_the_view(self):
        view = self.started_and_failed()

        def broken(mission_id):
            raise OSError("the workspace is unreadable")
        self.bridge.startup_cause = broken
        held = self.service.request_view(view["request_ref"])
        self.assertEqual(held["lifecycle"]["stage"], "dispatch_uncertain")
        self.assertEqual(held["start_cause"], {"state": start_cause.STATE_NONE, "rejected": 0})

    def test_it_is_asked_only_while_the_intent_exists_and_no_receipt_does(self):
        from local_request import surface as surface_module
        asked = []
        self.bridge.startup_cause = lambda mission_id: asked.append(mission_id) or {"state": "NONE", "rejected": 0}

        def ask(phase, intent, receipt):
            del asked[:]
            status = {"run": {"phase": phase, "run": {"intent": intent, "receipt": receipt}}}
            result = self.service._start_cause({"mission_id": "mn-" + "b" * 32}, status)
            return result, list(asked)

        run = surface_module
        intent, receipt = {"workspace_realpath": "/w", "recorded_at": 1}, {"task_id": "t-1"}
        yes = {"state": "NONE", "rejected": 0}
        self.assertEqual(ask(run.RUN_PHASE_HOLD_OUTCOME_UNKNOWN, intent, None), (yes, ["mn-" + "b" * 32]))
        self.assertEqual(ask(run.RUN_PHASE_BLOCKED, intent, None), (yes, ["mn-" + "b" * 32]),
                         "a run that stopped durably with no receipt is still a start that never confirmed")
        for label, phase, held_intent, held_receipt in (
            ("a blocked run that DID get a receipt (the start worked)", run.RUN_PHASE_BLOCKED, intent, receipt),
            ("an identity-unknown hold (a receipt row exists)", run.RUN_PHASE_HOLD_IDENTITY_UNKNOWN, intent,
             {"task_id": None}),
            ("a dispatched run", run.RUN_PHASE_DISPATCHED, intent, receipt),
            ("a running run", run.RUN_PHASE_RUNNING, intent, receipt),
            ("a hold with no intent", run.RUN_PHASE_HOLD_OUTCOME_UNKNOWN, None, None),
        ):
            self.assertEqual(ask(phase, held_intent, held_receipt), (None, []), label)
        self.assertIsNone(self.service._start_cause({"mission_id": "mn-x"}, {"run": None}))
        bridge, self.service._bridge = self.service._bridge, None
        self.assertIsNone(self.service._start_cause({"mission_id": "mn-x"}, {"run": {"phase": run.RUN_PHASE_BLOCKED,
                          "run": {"intent": intent, "receipt": None}}}))
        self.service._bridge = bridge

    def test_BEHAVIOURAL_a_run_stopped_durably_with_no_receipt_still_reports_the_start_that_never_confirmed(self):
        spawn = BindingFailureSpawn(self.clock)
        view = self.started_and_failed(spawn)
        self.observer.raw = unobserved_raw()                    # the child workspace holds no task record
        for _ in range(mission_record.MAX_RECONCILE_ATTEMPTS):
            self.bridge.reconcile(view["mission_id"])          # evidence only; the last one stops the run durably
        self.assertIsNone(self.mission(view["mission_id"])["run"]["receipt"])
        shown = self.service.request_view(view["request_ref"])
        self.assertEqual(shown["lifecycle"]["stage"], "blocked")
        self.assertTrue(shown["lifecycle"]["terminal"])
        self.assertEqual(shown["start_cause"]["state"], start_cause.STATE_BOUND,
                         "the start never confirmed, so its retained cause is still the honest report")
        self.assertEqual(len(spawn.calls), 1, "reconcile started nothing")

    def test_BEHAVIOURAL_a_blocked_run_that_DID_get_a_receipt_reports_no_startup_cause_and_asks_nothing(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")             # the plain recorder: a receipt is bound
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")              # RUNNING
        self.finish_team(view["mission_id"], status="ERROR")
        self.service.run(view["request_ref"], "verify")               # the decision: blocked, WITH its receipt
        self.assertIsNotNone(self.mission(view["mission_id"])["run"]["receipt"])
        asked = []
        real = self.bridge.startup_cause
        self.bridge.startup_cause = lambda mission_id: asked.append(mission_id) or real(mission_id)
        shown = self.service.request_view(view["request_ref"])
        self.assertEqual(shown["lifecycle"]["stage"], "blocked")
        self.assertIsNone(shown["start_cause"], "the start worked; a startup cause here would be a stage it never reached")
        self.assertEqual(asked, [], "the child workspace was not even read")

    def test_BEHAVIOURAL_the_cause_is_a_report_only_it_cannot_change_the_lifecycle_or_what_is_offered(self):
        view = self.started_and_failed()
        baseline = self.service.request_view(view["request_ref"])
        report = {"state": start_cause.STATE_BOUND, "kind": "BINDINGS_NOT_ESTABLISHED", "stage": "x",
                  "failure_class": None, "roles": [], "roles_total": 0, "roles_truncated": False, "reference": "r.json"}
        for answer in (report, {"state": start_cause.STATE_NONE, "rejected": 9},
                       {"state": start_cause.STATE_AMBIGUOUS, "candidates": 9}, None):
            self.bridge.startup_cause = lambda mission_id, answer=answer: answer
            shown = self.service.request_view(view["request_ref"])
            self.assertEqual({k: v for k, v in shown.items() if k != "start_cause"},
                             {k: v for k, v in baseline.items() if k != "start_cause"},
                             "nothing but the report itself may differ")
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run, view["request_ref"], "dispatch")

    def test_STRUCTURAL_the_cause_is_read_only_to_fill_the_view_and_by_no_decision_or_run_function(self):
        """Structural only: which functions of the panel mention the cause. It shows no decision, withdrawal, run or
        submit path READS it; the test above shows that varying it changes nothing else."""
        import ast
        path = REPO_ROOT / "mission_panel" / "service.py"
        source = path.read_text()
        mentioning = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if "start_cause" in (ast.get_source_segment(source, node) or ""):
                    mentioning.add(node.name)
        self.assertEqual(mentioning, {"_start_cause", "_view", "request_view"})

    def test_a_second_start_is_refused_and_the_scripted_spawn_ran_once(self):
        spawn = BindingFailureSpawn(self.clock)
        view = self.started_and_failed(spawn)
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run, view["request_ref"], "dispatch")
        self.assertEqual(len(spawn.calls), 1)
        self.assertEqual(self.service.request_view(view["request_ref"])["lifecycle"]["actions"], ["reconcile"])

    def test_no_stored_state_grew_a_field_for_this(self):
        # Projection stores nothing: the Mission's run block and the panel journal hold only the keys they held
        # before this delta existed.
        view = self.started_and_failed()
        self.service.request_view(view["request_ref"])
        with open(os.path.join(self.state, "mission_panel_journal.json")) as handle:
            journal = json.load(handle)
        self.assertEqual(set(journal), {"schema_version", "offers", "turns"})
        known = {"intent", "receipt", "pauses", "cancel", "verification", "pending_proof", "workspace",
                 "reconcile_unobserved", "workspace_refusal", "late_resolution"}
        for mission in json.loads(self.mission_bytes())["missions"].values():
            self.assertLessEqual(set(mission["run"]), known)
            self.assertNotIn("startup", json.dumps(mission["run"]).lower())
            self.assertNotIn("diagnostic", json.dumps(mission["run"]).lower())


if __name__ == "__main__":
    unittest.main(verbosity=1)
