"""Tests for ``target_runtime.mission_run_guard``: the cross-process, per-Mission
guard that serializes every effect the bridge has on ONE Mission's run.

Why it exists: the panel's explicit Start (a request handler) and the
progression runtime (a separate process) both act on the same Mission through
the same MissionBridge. Mission Core's store serializes each individual write,
but the bridge's operations are read-then-act sequences: a runtime pass that
reconciled while the panel was mid-dispatch would count an attempt against a
child that was about to be bound, and a verify racing an observe could decide
from reads the other had already made stale. A per-process lock cannot stop a
second process, so the guard is an OS file lock (``flock``), which the kernel
releases when its holder exits however it exits.

These tests use REAL file locks and a REAL second process (a short-lived
Python child this test owns and ends itself); nothing is mocked.
"""

import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from target_runtime import mission_run_guard as guard_module  # noqa: E402

MISSION = "mn-" + "a" * 32
OTHER = "mn-" + "b" * 32

HOLDER = r"""
import sys
sys.path.insert(0, sys.argv[1])
from target_runtime import mission_run_guard as g
guard = g.RunGuard(sys.argv[2])
with guard.hold(sys.argv[3], blocking=False) as held:
    print("held" if held else "busy", flush=True)
    if held:
        sys.stdin.readline()          # hold until the test closes our stdin
"""


class Holder(object):
    """A second PROCESS holding one Mission's guard until released."""

    def __init__(self, directory, mission_id):
        self.process = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(REPO_ROOT), directory, mission_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.first = self.process.stdout.readline().strip()

    def release(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=20)
        self.process.stdout.close()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, "state")
        os.mkdir(self.directory, 0o700)
        self.guard = guard_module.RunGuard(self.directory, wait_seconds=0.3)
        self.holders = []
        self.addCleanup(self.release_all)

    def release_all(self):
        for holder in self.holders:
            holder.release()

    def held_elsewhere(self, mission_id=MISSION):
        holder = Holder(self.directory, mission_id)
        self.holders.append(holder)
        self.assertEqual(holder.first, "held")
        return holder

    def test_G1_an_uncontended_guard_is_held_and_then_released(self):
        with self.guard.hold(MISSION) as held:
            self.assertTrue(held)
        # Released on exit: another hold succeeds at once.
        with self.guard.hold(MISSION, blocking=False) as held:
            self.assertTrue(held)

    def test_G2_a_second_process_is_excluded_not_queued_behind_nothing(self):
        self.held_elsewhere()
        with self.guard.hold(MISSION, blocking=False) as held:
            self.assertFalse(held, "a second process held the same Mission's guard")

    def test_G3_a_blocking_hold_waits_a_bounded_time_then_says_busy(self):
        self.held_elsewhere()
        started = time.monotonic()
        with self.assertRaises(guard_module.RunGuardBusy) as caught:
            with self.guard.hold(MISSION):
                self.fail("the guard was granted while another process held it")
        waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 0.25)
        self.assertLess(waited, 5.0)
        self.assertIn(MISSION, str(caught.exception))

    def test_G4_a_blocking_hold_succeeds_once_the_other_process_lets_go(self):
        holder = self.held_elsewhere()
        patient = guard_module.RunGuard(self.directory, wait_seconds=20)
        holder.process.stdin.close()          # the holder exits shortly
        started = time.monotonic()
        with patient.hold(MISSION) as held:
            self.assertTrue(held)
        self.assertLess(time.monotonic() - started, 15)

    def test_G5_guards_are_per_mission(self):
        self.held_elsewhere(MISSION)
        with self.guard.hold(OTHER, blocking=False) as held:
            self.assertTrue(held, "one Mission's guard blocked another's")

    def test_G6_a_holder_that_dies_releases_the_guard(self):
        holder = self.held_elsewhere()
        # Ending the holder's process by its OWN exit (we own it) is the
        # kernel's release, with no cleanup code of the holder's running.
        holder.process.stdin.close()
        holder.process.wait(timeout=20)
        with self.guard.hold(MISSION, blocking=False) as held:
            self.assertTrue(held)

    def test_G7_the_same_process_does_not_deadlock_on_a_second_hold(self):
        # flock on separate descriptors conflicts even inside one process.
        with self.guard.hold(MISSION) as outer:
            self.assertTrue(outer)
            with self.guard.hold(MISSION, blocking=False) as inner:
                self.assertFalse(inner)
            with self.assertRaises(guard_module.RunGuardBusy):
                with self.guard.hold(MISSION):
                    pass

    def test_G8_only_a_mission_id_names_a_guard(self):
        for bad in ("", "mn-short", "../" + MISSION, MISSION + "/x", MISSION + "\n",
                    "MN-" + "A" * 32, None, 7, "mn-" + "g" * 32):
            with self.subTest(bad=bad):
                with self.assertRaises(guard_module.RunGuardRefused):
                    with self.guard.hold(bad):
                        pass
        # Nothing was created for a refused id.
        self.assertEqual(os.listdir(self.directory), [])

    def test_G9_the_guard_directory_is_private_and_owned(self):
        with self.guard.hold(MISSION):
            pass
        directory = os.path.join(self.directory, guard_module.GUARD_DIRECTORY_NAME)
        mode = stat.S_IMODE(os.stat(directory).st_mode)
        self.assertEqual(mode, 0o700)
        names = os.listdir(directory)
        self.assertEqual(names, [MISSION + ".lock"])
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(directory, names[0])).st_mode), 0o600)

    def test_G10_a_symlinked_guard_directory_is_refused_not_followed(self):
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.mkdir(elsewhere, 0o700)
        os.symlink(elsewhere, os.path.join(self.directory,
                                           guard_module.GUARD_DIRECTORY_NAME))
        with self.assertRaises(guard_module.RunGuardRefused):
            with self.guard.hold(MISSION):
                pass
        self.assertEqual(os.listdir(elsewhere), [])

    def test_G11_a_symlinked_lock_file_is_refused_not_followed(self):
        directory = os.path.join(self.directory, guard_module.GUARD_DIRECTORY_NAME)
        os.mkdir(directory, 0o700)
        target = os.path.join(self.tmp.name, "target")
        Path(target).write_text("x")
        os.symlink(target, os.path.join(directory, MISSION + ".lock"))
        with self.assertRaises(guard_module.RunGuardRefused):
            with self.guard.hold(MISSION):
                pass
        self.assertEqual(Path(target).read_text(), "x")

    def test_G12_an_unusable_state_directory_is_refused_not_guessed(self):
        missing = guard_module.RunGuard(os.path.join(self.tmp.name, "absent"))
        with self.assertRaises(guard_module.RunGuardRefused):
            with missing.hold(MISSION):
                pass

    def test_G13_the_guard_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with self.guard.hold(MISSION):
                raise RuntimeError("synthetic")
        with self.guard.hold(MISSION, blocking=False) as held:
            self.assertTrue(held)

    def test_G14_the_bounds_are_the_documented_ones(self):
        self.assertEqual(guard_module.GUARD_WAIT_SECONDS, 30)
        self.assertEqual(guard_module.GUARD_POLL_SECONDS, 0.05)


if __name__ == "__main__":
    unittest.main()
