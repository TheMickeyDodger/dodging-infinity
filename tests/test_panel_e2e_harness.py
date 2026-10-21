"""The end-to-end harness's progression count (``tests/_panel_e2e_server.py``
``Runtime``), which the Pixel browser test A reads as "the runtime advanced".

- A pass is counted BEFORE any of its work, so nothing a pass writes can be read
  before that pass is counted.
- A pass-boundary read is never taken while a pass is in flight, and it runs under
  the lock the loop must take before it counts and begins the next pass.
- A boundary read is refused (None) when the loop is not passing; it is never a read.

The real ``dirun`` loop is replaced by a scripted one of the same shape (passes
separated by calls to the injected sleeper). Outcomes are decided by handshakes and
by what was recorded, never by how the scheduler happened to order the threads;
every wait is bounded and a wait that runs out fails the test.
"""

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)

import _panel_e2e_server as harness  # noqa: E402

BOUND = 10.0


class SignallingCondition(threading.Condition):
    """The runtime's boundary condition, also saying when a reader waits for a boundary."""

    def __init__(self):
        super(SignallingCondition, self).__init__()
        self.reader_waiting = threading.Event()

    def wait(self, timeout=None):
        self.reader_waiting.set()
        return super(SignallingCondition, self).wait(timeout)


class ScriptedLoop(object):
    """dirun's loop shape: a pass, the sleeper, a pass, ... until the sleeper raises."""

    def __init__(self, hold_pass=None):
        self.runtime = None
        self.started = []                 # runtime.passes as each pass began its work
        self.in_flight = False
        self.hold_pass = hold_pass        # this pass waits mid-work for `release`
        self.held = threading.Event()
        self.release = threading.Event()
        self.changed = threading.Condition()

    def main(self, argv, sleeper=None, mission_composition=None):
        try:
            while True:
                with self.changed:
                    self.in_flight = True
                    self.started.append(self.runtime.passes)
                    self.changed.notify_all()
                    number = len(self.started)
                if number == self.hold_pass:
                    self.held.set()
                    if not self.release.wait(BOUND):
                        return 3
                with self.changed:
                    self.in_flight = False
                sleeper(0)
        except KeyboardInterrupt:
            return 0

    def wait_for_passes(self, count):
        with self.changed:
            return self.changed.wait_for(lambda: len(self.started) >= count, BOUND)


def runtime_over(loop, condition=None):
    world = SimpleNamespace(state="s", control="c", repository="r", root="w",
                            mission_composition=None)
    runtime = harness.Runtime(world)
    if condition is not None:
        runtime.boundary = condition
    loop.runtime = runtime
    return runtime


def join_if_started(thread):
    if thread.ident is not None:
        thread.join(BOUND)


class PanelHarnessRuntimeTests(unittest.TestCase):

    def setUp(self):
        patch = mock.patch.object(harness, "PASS_INTERVAL_SECONDS", 0.01)
        patch.start()
        self.addCleanup(patch.stop)

    def run_loop(self, loop, condition=None):
        """Start the harness Runtime over ``loop``. Cleanup (last in, first out) lets a
        held pass go before the loop is stopped, so a failed assertion never strands it."""
        runtime = runtime_over(loop, condition)
        patch = mock.patch.object(harness, "runtime_cli", SimpleNamespace(main=loop.main))
        patch.start()
        self.addCleanup(patch.stop)
        runtime.start()
        self.addCleanup(runtime.end)
        self.addCleanup(loop.release.set)
        return runtime

    def test_a_pass_is_counted_before_any_of_its_work(self):
        loop = ScriptedLoop()
        self.run_loop(loop)
        self.assertTrue(loop.wait_for_passes(4), "the scripted loop stopped passing")
        self.assertEqual(loop.started[:4], [1, 2, 3, 4])

    def test_a_boundary_read_is_never_taken_mid_pass_and_holds_the_next_pass(self):
        condition = SignallingCondition()
        loop = ScriptedLoop(hold_pass=2)
        runtime = self.run_loop(loop, condition)
        seen, other_could_lock, answers = {}, [], []

        def try_lock():
            got = runtime.boundary.acquire(blocking=False)
            other_could_lock.append(got)
            if got:
                runtime.boundary.release()

        def read():
            seen.update(passes=runtime.passes, started=len(loop.started),
                        in_flight=loop.in_flight)
            other = threading.Thread(target=try_lock, daemon=True)
            other.start()
            other.join(BOUND)
            return "read"

        reader = threading.Thread(
            target=lambda: answers.append(runtime.at_boundary(read, BOUND)), daemon=True)
        # Registered after run_loop's, so it runs first: the held pass is let go, then the
        # reader is joined, then the loop is stopped.
        self.addCleanup(join_if_started, reader)
        self.addCleanup(loop.release.set)
        self.assertTrue(loop.held.wait(BOUND), "pass 2 never began")
        reader.start()
        # Asked while pass 2 is in flight: the reader waits for a boundary and reads nothing.
        self.assertTrue(condition.reader_waiting.wait(BOUND), "the reader never waited")
        self.assertEqual(seen, {})
        loop.release.set()
        reader.join(BOUND)
        self.assertEqual(answers, ["read"])
        # Read at A boundary (pass 2's or a later one: which one is the scheduler's choice),
        # never mid-pass: no pass was in flight and every pass counted had begun.
        self.assertFalse(seen["in_flight"])
        self.assertGreaterEqual(seen["passes"], 2)
        self.assertEqual(seen["passes"], seen["started"])
        # Under the lock the loop must take to count and begin the next pass.
        self.assertEqual(other_could_lock, [False])
        # The next pass, once the read returned, is counted as the one after it.
        self.assertTrue(loop.wait_for_passes(seen["passes"] + 1), "the loop did not pass again")
        self.assertEqual(loop.started[seen["passes"]], seen["passes"] + 1)

    def test_a_boundary_read_is_refused_when_the_loop_is_not_passing(self):
        never_started = runtime_over(ScriptedLoop())
        self.assertIsNone(never_started.at_boundary(lambda: "read", BOUND))
        stopped = self.run_loop(ScriptedLoop())
        stopped.end()
        self.assertFalse(stopped.alive())
        self.assertIsNone(stopped.at_boundary(lambda: "read", BOUND))
        stuck = ScriptedLoop(hold_pass=1)
        runtime = self.run_loop(stuck)
        self.assertTrue(stuck.held.wait(BOUND), "pass 1 never began")
        # A loop stuck mid-pass: the bounded wait answers None, never a read.
        self.assertIsNone(runtime.at_boundary(lambda: "read", 0.2))


if __name__ == "__main__":
    unittest.main()
