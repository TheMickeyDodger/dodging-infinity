"""The progression runtime, proved through its REAL entry point.

``dirun`` has two modes. The default (workflow) mode is the existing Runtime
and is unchanged; its tests are elsewhere and are re-run with this change. The
MissionCore mode, selected by naming the panel's four locations, advances the
Missions the DI World request panel started: it is what makes work progress
after Start with no browser open and nobody polling.

What is REAL here: ``target_runtime.cli.main`` (argument parsing, mode
selection, the single-owner lock, the pass loop, the pacing, the status file,
exit codes), the shared composition, the panel's journal, the local_request
surface and Mission Core stores, the run guard, and the MissionBridge. What is
SUBSTITUTED, as the bridge suite already does and nowhere else: the bridge's two
EFFECT seams (the spawn and the read-only observer), because there is no live
Herdr. Nothing here is evidence of a live Herdr.

A separate class runs ``dirun.py`` as a real subprocess with NO substitution at
all. With no eligible request it starts nothing, which is exactly what makes it
safe to run the production composition end to end.
"""

import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_panel import composition as composition_module  # noqa: E402
from target_runtime import mission_progress_status as status_module  # noqa: E402
from target_runtime import cli as cli_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from test_mission_bridge import raw_observation  # noqa: E402
from test_mission_panel_progress import PassFixture  # noqa: E402


class ModeFixture(PassFixture):
    """The bridge suite's world, reached through ``cli.main``."""

    def setUp(self):
        super(ModeFixture, self).setUp()
        self.control = os.path.join(self.tmp.name, "control")
        os.mkdir(self.control)
        self.sleeps = []
        self.between = []             # one callable per pause: the world changing

    def flags(self, command="once", **omit):
        flags = ["--mission-state-dir", self.state,
                 "--mission-control-repo", self.control,
                 "--mission-workspace-repository", self.repository,
                 "--mission-workspaces-root", self.root]
        return flags + [command]

    def fake_bridge(self, missions, control, clock, run_guard):
        return bridge_module.MissionBridge(
            missions, "/control-repo", clock, spawn_fn=self.spawn,
            observer_fn=self.observer, spawn_records_fn=self.records,
            transport=self.transport,
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            worker=self.trust, run_guard=run_guard)

    def composition(self, state, control, repository, root):
        return composition_module.compose(
            state, control, repository, root, clock=self.clock,
            bridge_factory=self.fake_bridge)

    def sleeper(self, seconds):
        self.sleeps.append(seconds)
        if self.between:
            self.between.pop(0)()

    def main(self, argv=None, passes=None, composition=True, **kwargs):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli_module.main(
                argv or self.flags("run" if passes else "once"),
                sleeper=self.sleeper, passes=passes,
                mission_composition=self.composition if composition else None,
                **kwargs)
        self.stderr = stderr.getvalue()
        return code

    def probe(self):
        return status_module.probe(
            self.state, status_module.composition_digest(
                self.state, self.control, self.repository, self.root),
            int(time.time()))


class EntryPointTests(ModeFixture):

    def test_E1_the_mode_is_selected_before_any_workflow_machinery_is_built(self):
        tripwire = mock.Mock(side_effect=AssertionError("workflow machinery was built"))
        with mock.patch.object(cli_module, "_build_broker", tripwire), \
                mock.patch.object(cli_module, "TargetBroker", tripwire), \
                mock.patch.object(cli_module, "RuntimeDurableExecution", tripwire), \
                mock.patch.object(cli_module.runtime_module, "process_once", tripwire):
            self.assertEqual(self.main(), cli_module.EXIT_OK, self.stderr)
        tripwire.assert_not_called()

    def test_E2_work_progresses_after_start_with_no_browser_and_no_panel_running(self):
        mission_id, _ = self.linked(started=True)          # the person pressed Start
        working = []

        def team_finishes():
            working.append(self.mission(mission_id)["state"])
            self.finish_team(mission_id)
            self.satisfy_tests_pass(mission_id)
        self.between = [lambda: working.append(self.mission(mission_id)["state"]),
                        team_finishes]
        code = self.main(self.flags("run"), passes=3)
        self.assertEqual(code, cli_module.EXIT_OK, self.stderr)
        self.assertEqual(working, ["RUNNING", "RUNNING"],
                         "pass 1 should have observed the target into RUNNING")
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        self.assertTrue(self.bridge.result(mission_id)["verified_result"])
        self.assertEqual(len(self.spawn.calls), 1, "progression spawned a second time")
        self.assertEqual(self.sleeps, [cli_module.RUNTIME_POLL_INTERVAL_SECONDS] * 2)

    def test_E3_an_approved_but_unstarted_mission_is_never_started_by_the_runtime(self):
        mission_id, _ = self.linked(started=False)
        before = self.mission_bytes()
        self.assertEqual(self.main(self.flags("run"), passes=4), cli_module.EXIT_OK)
        self.assertEqual(self.spawn.calls, [])
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_E4_a_second_runtime_on_the_same_state_is_refused_by_the_real_lock(self):
        held = cli_module.acquire_runtime_lock(
            self.state, cli_module.MISSION_RUNTIME_LOCK_FILE_NAME)
        self.assertIsNotNone(held)
        self.addCleanup(os.close, held)
        mission_id, _ = self.linked(started=True)
        before = self.mission_bytes()
        self.assertEqual(self.main(), cli_module.EXIT_LOCKED)
        self.assertIn("already holds the lock", self.stderr)
        self.assertEqual(self.mission_bytes(), before, "a duplicate owner acted")
        self.assertEqual(self.mission(mission_id)["state"], "AUTHORIZED")

    def test_E5_the_workflow_runtime_lock_is_a_different_lock(self):
        # No shared claim or lease state with the workflow Runtime: a held
        # workflow lock in the same directory does not stop this mode.
        workflow_lock = cli_module.acquire_runtime_lock(self.state)
        self.assertIsNotNone(workflow_lock)
        self.addCleanup(os.close, workflow_lock)
        self.assertEqual(self.main(), cli_module.EXIT_OK, self.stderr)
        self.assertNotEqual(cli_module.MISSION_RUNTIME_LOCK_FILE_NAME,
                            cli_module.RUNTIME_LOCK_FILE_NAME)

    def test_E6_no_workflow_record_or_id_is_created_or_read(self):
        self.linked(started=True)
        self.main(self.flags("run"), passes=2)
        names = sorted(os.listdir(self.state))
        for name in names:
            self.assertNotIn("workflow", name.lower(), names)
        self.assertEqual(
            [n for n in names if n.startswith("mission_progress_status")],
            ["mission_progress_status.json"])

    def test_E7_the_status_file_says_running_while_passing_and_stopped_after(self):
        seen = []
        self.between = [lambda: seen.append(self.probe())]
        self.main(self.flags("run"), passes=2)
        self.assertEqual(seen[0]["state"], "running", seen)
        self.assertEqual(seen[0]["passes"], 1)
        self.assertEqual(self.probe()["state"], "stopped",
                         "a finished runtime still reads as running")

    def test_E8_an_unrelated_mission_is_untouched_by_the_whole_loop(self):
        stranger = self.approved()                       # not this panel's
        self.dispatch(stranger)
        self.observer.raw = raw_observation()
        self.bridge.observe(stranger)
        self.finish_team(stranger)
        self.satisfy_tests_pass(stranger)
        mine, _ = self.linked(started=True)
        self.main(self.flags("run"), passes=3)
        self.assertEqual(self.mission(stranger)["state"], "RUNNING")
        self.assertIsNone(self.mission(stranger)["run"].get("verification"))
        self.assertEqual(self.mission(mine)["state"], "RUNNING")

    def test_E9_a_restart_continues_from_the_durable_records_without_respawning(self):
        mission_id, _ = self.linked(started=True)
        self.assertEqual(self.main(), cli_module.EXIT_OK)         # observed -> RUNNING
        self.finish_team(mission_id)
        self.satisfy_tests_pass(mission_id)
        self.assertEqual(self.main(), cli_module.EXIT_OK)         # a new process's pass
        self.assertEqual(self.mission(mission_id)["state"], "COMPLETED")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_E10_a_pass_that_fails_as_a_whole_is_visible_and_the_loop_survives(self):
        self.linked(started=True)
        with open(os.path.join(self.state, "mission_panel_journal.json"), "w") as handle:
            handle.write("{ not json")
        seen = []
        self.between = [lambda: seen.append(self.probe())]
        code = self.main(self.flags("run"), passes=2)
        self.assertEqual(code, cli_module.EXIT_OK, self.stderr)
        self.assertEqual(seen[0]["state"], "failing", seen)
        self.assertIn("JournalError", seen[0]["detail"])
        self.assertIn("pass failed", self.stderr)
        self.assertEqual(self.stderr.count("pass failed"), 1,
                         "an unchanged failure was repeated every pass")

    def test_E11_a_closed_stderr_does_not_end_the_loop(self):
        mission_id, _ = self.linked(started=True)
        self.finish_team(mission_id)
        self.observer.raw = raw_observation(status="ERROR")      # a notable hold to report
        self.main(self.flags("run"), passes=1)                    # observed into RUNNING

        class Broken(object):
            def write(self, _):
                raise BrokenPipeError()

            def flush(self):
                raise BrokenPipeError()
        with contextlib.redirect_stderr(Broken()):
            code = cli_module.main(self.flags("run"), sleeper=self.sleeper, passes=3,
                                   mission_composition=self.composition)
        self.assertEqual(code, cli_module.EXIT_OK)
        self.assertEqual(len(self.sleeps), 2, "the loop died on its first diagnostic")

    def test_E12_a_stopped_failed_team_is_a_visible_hold_the_runtime_never_resolves(self):
        mission_id, _ = self.linked(started=True)
        self.finish_team(mission_id)
        self.observer.raw = raw_observation(status="ERROR")
        self.main(self.flags("run"), passes=1)           # observe
        self.main(self.flags("run"), passes=3)
        self.assertEqual(self.mission(mission_id)["state"], "RUNNING")
        self.assertIn("hold", self.stderr)
        self.assertIn("verify_target_not_succeeded", self.stderr)
        reports = [line for line in self.stderr.splitlines()
                   if "verify_target_not_succeeded" in line]
        self.assertEqual(len(reports), 1, "an unchanged hold was repeated every pass")
        self.assertFalse(self.bridge.result(mission_id)["verified_result"])


class ConfigurationTests(ModeFixture):

    def refused(self, argv):
        code = self.main(argv, composition=True)
        self.assertEqual(code, cli_module.EXIT_CONFIG, self.stderr)
        return self.stderr

    def test_C1_all_four_locations_are_required_together(self):
        base = self.flags()
        for drop in range(0, 8, 2):
            argv = base[:drop] + base[drop + 2:]
            with self.subTest(dropped=base[drop]):
                self.assertIn("all four", self.refused(argv))

    def test_C2_relative_paths_are_refused(self):
        for index in (1, 3, 5, 7):
            argv = self.flags()
            argv[index] = "relative/path"
            with self.subTest(flag=argv[index - 1]):
                self.assertIn("absolute", self.refused(argv))

    def test_C3_it_never_combines_with_the_workflow_configurations(self):
        for extra in (["--config", "/x.json"], ["--runtime-config", "/y.json"]):
            with self.subTest(extra=extra):
                self.assertIn("never combines", self.refused(extra + self.flags()))

    def test_C4_a_missing_repository_or_control_directory_is_a_configuration_error(self):
        argv = self.flags()
        argv[argv.index("--mission-control-repo") + 1] = os.path.join(self.tmp.name, "nope")
        self.assertIn("control repository", self.refused(argv))
        argv = self.flags()
        argv[argv.index("--mission-workspace-repository") + 1] = os.path.join(
            self.tmp.name, "nope")
        self.assertIn("workspace repository", self.refused(argv))

    def test_C5_a_composition_that_cannot_be_built_is_a_configuration_error(self):
        def broken(*_):
            raise OSError("synthetic: /private/path/that/must/not/leak")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli_module.main(self.flags(), mission_composition=broken)
        self.assertEqual(code, cli_module.EXIT_CONFIG)
        self.assertIn("OSError", stderr.getvalue())
        self.assertNotIn("private", stderr.getvalue())

    def test_C6_the_workflow_mode_is_still_selected_by_default(self):
        # No MissionCore flag: the existing path, built exactly as before.
        built = []

        def build(namespace):
            built.append(namespace)
            raise cli_module.ConfigError("synthetic: stop after selection")
        with mock.patch.object(cli_module, "_build_broker", build):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli_module.main(["once"])
        self.assertEqual(code, cli_module.EXIT_CONFIG)
        self.assertEqual(len(built), 1, "the default mode no longer builds its broker")


class RealSubprocessTests(unittest.TestCase):
    """``dirun.py`` itself, no substitution: the production composition."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.state = os.path.join(self.tmp.name, "state")
        self.control = os.path.join(self.tmp.name, "control")
        self.repository = os.path.join(self.tmp.name, "repository")
        self.roots = os.path.join(self.tmp.name, "workspaces")
        for path in (self.home, self.control, self.repository):
            os.mkdir(path)
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=20)
            for stream in (child.stdout, child.stderr):
                if stream:
                    stream.close()

    def start(self, command):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home,
               "PYTHONPATH": str(REPO_ROOT)}
        child = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "dirun.py"),
             "--mission-state-dir", self.state, "--mission-control-repo", self.control,
             "--mission-workspace-repository", self.repository,
             "--mission-workspaces-root", self.roots, command],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=self.tmp.name)
        self.children.append(child)
        return child

    def digest(self):
        return status_module.composition_digest(
            self.state, self.control, self.repository, self.roots)

    def probe(self):
        return status_module.probe(self.state, self.digest(), int(time.time()))

    def wait_for(self, predicate, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.2)
        return False

    def test_R1_the_production_composition_runs_once_and_stops_cleanly(self):
        child = self.start("once")
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, err)
        self.assertIn("examined 0", err)
        self.assertEqual(self.probe()["state"], "stopped")
        self.assertTrue(os.path.isfile(os.path.join(self.state, "mission_progress_status.json")))

    def test_R2_the_production_runtime_runs_is_seen_running_refuses_a_twin_and_stops(self):
        first = self.start("run")
        self.assertTrue(self.wait_for(lambda: self.probe()["state"] == "running"),
                        "the runtime never reported running: %r" % self.probe())
        twin = self.start("run")
        twin_out, twin_err = twin.communicate(timeout=60)
        self.assertEqual(twin.returncode, cli_module.EXIT_LOCKED, twin_err)
        self.assertEqual(self.probe()["state"], "running", "the twin disturbed the owner")
        first.send_signal(signal.SIGTERM)
        out, err = first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, err)
        self.assertEqual(self.probe()["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
