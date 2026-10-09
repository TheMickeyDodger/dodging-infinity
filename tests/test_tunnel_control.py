"""On-demand Quick Tunnel control
(``tunnel_control``, ``ditunnel.py``), its persistent controller, and its
launchd artifact.

What is REAL here:

- the tool, its controller, the ownership record, the control socket, and
  the anchored shutdown;
- ``target_runtime.spawn_stamp.leader_start_time`` (a read-only
  ``ps -o lstart=``) and ``target_runtime.process_ownership
  .group_is_verified``;
- real child processes, real process groups, real signals, and
  ``ditunnel.py`` / ``python -m tunnel_control.controller`` in child
  interpreters.

What is SYNTHETIC: ``cloudflared``. Every test passes ``--cloudflared`` the
absolute path of a FIXTURE script written into the test's own temporary
directory.

- It prints a made-up ``https://<random>.trycloudflare.com`` line, or fails,
  or prints nothing, or forks a member that ignores SIGTERM, and so on.
- It opens NO socket and reaches no network.
- The real ``cloudflared`` is never run, no tunnel is opened, ``launchctl``
  is never invoked, and nothing is installed.

Containment and termination (CONTRIBUTING.md):

- every fixture process exits by itself after ``FIXTURE_LIFETIME`` seconds,
  independently of the tool;
- every child interpreter run carries its own ``timeout``;
- every thread is joined with a timeout;
- a SIGALRM watchdog bounds every test;
- cleanup NEVER signals a pid number: not one from a record, not one from a
  pid file, not one already reaped. It uses only:
  - the test's own children, through their live, unreaped ``Popen``
    handles (an unreaped child's pid cannot be recycled);
  - the product's own anchored ``off``, for a controller still serving;
  - COOPERATIVE fixture shutdown: every fixture process exits when its stop
    file appears;
  - the fixtures' bounded self-expiry. A descendant that cannot be owned is
    left to expire on its own.
- The test's own signals go only to its own live children (by handle) and
  to itself (while the in-process controller's handler is installed).
  ``killpg(pid, 0)`` / ``kill(pid, 0)`` probes deliver no signal.

Every write is inside the per-test temporary directory. The ``ps`` reads are
read-only.
"""

import ast
import fcntl
import io
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tunnel_control import cli as cli_module  # noqa: E402
from tunnel_control import common  # noqa: E402
from tunnel_control import controller as controller_module  # noqa: E402
from tunnel_control import tunnel  # noqa: E402
from target_runtime import spawn_stamp  # noqa: E402

WATCHDOG_SECONDS = 120
FIXTURE_LIFETIME = 60
CHILD_TIMEOUT_SECONDS = 90
JOIN_SECONDS = 60
ENTRY_SCRIPT = REPO_ROOT / "ditunnel.py"
PLIST = REPO_ROOT / "scripts" / "ditunnel" / "com.dodginginfinity.ditunnel.plist"
LIVE_LABEL = "com.dodginginfinity.grokbot.task8.tunnel"
PORT, OTHER_PORT = 63999, 63998
FAST = dict(settle=0.3, startup_timeout=10, kill_grace=3, max_unconfirmed=2)
URL_PATTERN = r"https://[0-9a-f]+\.trycloudflare\.com"

FIXTURE = '''#!%(python)s
# SYNTHETIC cloudflared (tests/test_tunnel_control.py): no socket, bounded.
import json, os, secrets, signal, sys, time
mode, lifetime, base, tag = %(mode)r, %(lifetime)d, %(base)r, %(tag)r
start = time.monotonic()


def note(suffix, value):
    with open(os.path.join(base, tag + suffix), "w") as handle:
        handle.write(str(value))


def idle(role):
    """Until self-expiry, or the COOPERATIVE stop file for this fixture (or,
    for the leader only, its own stop file) appears."""
    stop = os.path.join(base, tag + ".stop")
    stop_leader = os.path.join(base, tag + ".stop-leader")
    while time.monotonic() - start < lifetime:
        if os.path.exists(stop) or (role == "leader"
                                    and os.path.exists(stop_leader)):
            return
        time.sleep(0.05)


note(".argv", json.dumps(sys.argv[1:]))
note(".pid", os.getpid())
if mode == "fail":
    sys.stderr.write("ERR synthetic: failed to start\\n")
    sys.exit(1)
if mode in ("member_ignores_term", "exit_later_member", "grandchild"):
    member = os.fork()
    if member == 0:
        if mode != "grandchild":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        note(".member", os.getpid())
        idle("member")
        os._exit(0)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode != "silent":
    time.sleep(0.1)
    sys.stderr.write("INF |  https://%%s.trycloudflare.com  |\\n"
                     %% secrets.token_hex(6))
    sys.stderr.flush()
if mode == "url_then_exit":
    sys.exit(0)
if mode == "exit_later_member":
    time.sleep(2.0)
    sys.exit(0)
idle("leader")
'''


def alive(pid):
    """Whether ``pid`` is a live process. An exited child of THIS process
    (a zombie) is not alive; it is detected without being reaped."""
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        if os.waitid(os.P_PID, pid,
                     os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stopped(pid):
    """Whether ``pid`` is in the stopped state (read-only ``ps``)."""
    done = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                          capture_output=True, text=True, timeout=10)
    return done.stdout.strip().startswith("T")


def worst_case_stop_seconds(grace, kill_grace, max_unconfirmed,
                            retry=controller_module.RETRY_SECONDS,
                            poll=controller_module.POLL_SECONDS,
                            reply=controller_module.REPLY_SECONDS):
    """The controller's worst-case bounded stop from a stop signal, traced
    from its code (``serve`` -> ``shutdown`` -> ``shutdown_until_observed``):

    - observing the stop takes at most ``reply + poll``: one reply in
      progress, then one poll. A request read is abandoned within ``poll``
      (round 11);
    - one attempt is at most ``grace + 2 * kill_grace``: the SIGTERM wait,
      the SIGKILL wait, and the wait for the group to be gone after a reap;
    - ``serve`` runs a first attempt, then replies to a client's ``stop``
      (at most ``reply``);
    - ``shutdown_until_observed`` then opens a ``max_unconfirmed`` window,
      inside which it runs its own first attempt and retries;
    - its last retry can begin just before the window closes, after a
      ``retry`` pause.
    """
    attempt = grace + 2 * kill_grace
    return ((reply + poll) + attempt + reply + max_unconfirmed + retry
            + attempt)


def wait_until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class TunnelFixture(unittest.TestCase):

    def setUp(self):
        def expired(signum, frame):
            raise TimeoutError("watchdog: test exceeded %d s" % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        # Short, so the control socket's path fits the platform's bound.
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="dit"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.state = os.path.join(self.base, "st")
        self.children = []
        self.addCleanup(self.clean_up)

    def clean_up(self):
        """The TEST's own containment (never the tool's), and NEVER a pid
        number (see the module docstring)."""
        if os.path.isdir(self.state):
            try:
                tunnel.off(self.state)
            except Exception:  # noqa: BLE001
                pass
        self.stop_fixtures()
        for child in self.children:
            if child.poll() is None:
                child.kill()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass

    def stop_fixtures(self, tag=None):
        """Cooperative: every fixture process (of ``tag``, or all) exits."""
        tags = [tag] if tag else [path.name[len("bin-"):] for path in
                                  Path(self.base).glob("bin-*")]
        for name in tags:
            Path(self.base, name + ".stop").touch()

    def fixture(self, mode="ok", tag=None):
        tag = tag or mode
        directory = os.path.join(self.base, "bin-" + tag)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "cloudflared")
        with open(path, "w") as handle:
            handle.write(FIXTURE % {"python": sys.executable, "mode": mode,
                                    "lifetime": FIXTURE_LIFETIME,
                                    "base": self.base, "tag": tag})
        os.chmod(path, 0o755)
        return path

    def noted(self, tag, suffix=".pid", seconds=10):
        path = os.path.join(self.base, tag + suffix)
        self.assertTrue(wait_until(lambda: os.path.exists(path)
                                   and open(path).read(), seconds), path)
        return int(open(path).read())

    def unrelated(self, own_session=True):
        """A process this tool did not start, running a fixture NAMED
        ``cloudflared``."""
        process = subprocess.Popen(
            [self.fixture("ok", tag="unrelated")], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=own_session)
        self.children.append(process)
        self.noted("unrelated")
        return process

    def ditunnel(self, *args):
        done = subprocess.run(
            [sys.executable, str(ENTRY_SCRIPT), "--state-dir", self.state]
            + list(args), capture_output=True, text=True,
            timeout=CHILD_TIMEOUT_SECONDS)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

    def on(self, mode="ok", tag=None, port=PORT, grace="1"):
        return self.ditunnel("on", "--port", str(port), "--cloudflared",
                             self.fixture(mode, tag), "--stop-grace", grace)

    def foreground(self, mode="ok", out=None, **seams):
        """The launchd shape, IN-PROCESS: this test process is the
        controller. Returns (exit code, the startup result)."""
        out = out if out is not None else io.StringIO()
        merged = dict(FAST)
        merged.update(seams)
        code = tunnel.foreground(self.state, PORT, self.fixture(mode), out=out,
                                 stop_grace=merged.pop("stop_grace", 1),
                                 **merged)
        text = out.getvalue() if isinstance(out, io.StringIO) else ""
        lines = text.strip().splitlines()
        return code, (json.loads(lines[0]) if lines else None)

    def start_controller(self, mode="ok"):
        """A controller started exactly as ``on`` starts it, but as THIS
        test's own child, so the test holds a live handle to it. Returns
        (handle, startup result)."""
        process = subprocess.Popen(
            [sys.executable, "-m", "tunnel_control.controller",
             "--state-dir", common.state_dir(self.state), "--port", str(PORT),
             "--cloudflared", self.fixture(mode), "--stop-grace", "1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(REPO_ROOT),
            start_new_session=True)
        self.children.append(process)
        line = process.stdout.readline()
        process.stdout.close()
        return process, json.loads(line)

    def kill_controller(self, process):
        """Model the controller's own hard death, through the test's live
        handle to its own child."""
        process.kill()
        process.wait(timeout=CHILD_TIMEOUT_SECONDS)

    def operator_recovers(self, tag, group):
        """The SHAPE of the operator's manual recovery, performed
        cooperatively (the fixture's stop file), never by signalling an
        inferred identity."""
        self.stop_fixtures(tag)
        self.assertTrue(wait_until(
            lambda: common.group_state(group) == common.GROUP_GONE))


# ====================================================================
# on / status / off, repetition, and configuration mismatch
# ====================================================================


class LifecycleTests(TunnelFixture):

    def test_on_reports_the_current_url_and_off_stops_the_owned_group(self):
        code, started = self.on()
        self.assertEqual(code, 0, started)
        self.assertRegex(started["url"], URL_PATTERN)
        leader = self.noted("ok")
        self.assertEqual(started["pid"], leader)
        self.assertEqual(self.ditunnel("status")[1]["url"], started["url"])
        code, result = self.ditunnel("off")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["observed"])
        self.assertEqual(result["signalled"]["process_group"], leader)
        self.assertTrue(wait_until(lambda: not alive(leader)))
        self.assertTrue(wait_until(lambda: not alive(started["controller_pid"])))
        self.assertIsNone(common.load(self.state))

    def test_the_quick_tunnel_argv_is_free_and_accountless(self):
        self.on()
        argv = json.loads(open(os.path.join(self.base, "ok.argv")).read())
        self.assertEqual(argv, ["tunnel", "--no-autoupdate", "--url",
                                "http://127.0.0.1:%d" % PORT,
                                "--http-host-header", "127.0.0.1:%d" % PORT])
        for absent in ("login", "--allowed-mail", "--token", "run", "create"):
            self.assertNotIn(absent, argv)
        self.ditunnel("off")

    def test_the_tunnel_leads_its_own_session_and_group(self):
        """Amendment 8, question 2: ``start_new_session=True`` performs
        ``setsid`` for the tunnel (and, separately, for the controller), so
        the tunnel's pid IS its process group and session, and it is
        neither the caller's nor the controller's."""
        started = self.on()[1]
        leader = self.noted("ok")
        self.assertEqual((os.getpgid(leader), os.getsid(leader)),
                         (leader, leader))
        controller = started["controller_pid"]
        self.assertEqual(os.getsid(controller), controller)
        self.assertNotIn(os.getpgid(leader), (os.getpgrp(), controller))
        self.ditunnel("off")

    def test_repeated_on_with_the_same_configuration_starts_nothing(self):
        first = self.on()[1]
        code, again = self.on()
        self.assertEqual(code, 0, again)
        self.assertTrue(again["already_on"])
        self.assertEqual((again["pid"], again["url"]),
                         (first["pid"], first["url"]))
        self.ditunnel("off")

    def test_on_against_a_different_configuration_is_refused_by_name(self):
        """Matrix addition (a): never a silent success for another port or
        another cloudflared. The running tunnel is left exactly as it was."""
        first = self.on()[1]
        code, refused = self.on(port=OTHER_PORT)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("DIFFERENT configuration (http_host_header, origin, port)",
                      refused["reason"])
        self.assertIn('"port": %d' % PORT, refused["reason"])
        self.assertIn('"port": %d' % OTHER_PORT, refused["reason"])
        code, refused = self.on(tag="another-binary")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("DIFFERENT configuration (cloudflared)", refused["reason"])
        # The client's comparison, in-process (the mutant check's target).
        with self.assertRaises(common.TunnelError) as caught:
            tunnel.on(self.state, OTHER_PORT, self.fixture())
        self.assertIn("DIFFERENT configuration", str(caught.exception))
        status = self.ditunnel("status")[1]
        self.assertEqual((status["pid"], status["url"]),
                         (first["pid"], first["url"]))
        self.assertFalse(os.path.exists(
            os.path.join(self.base, "another-binary.pid")))
        self.ditunnel("off")

    def test_repeated_off_signals_nothing(self):
        self.on()
        self.ditunnel("off")
        code, again = self.ditunnel("off")
        self.assertEqual(code, 0)
        self.assertTrue(again["already_off"])
        self.assertIsNone(again["signalled"])

    def test_a_new_start_prints_a_new_url(self):
        first = self.on()[1]
        self.ditunnel("off")
        second = self.on(tag="ok2")[1]
        self.assertNotEqual(first["url"], second["url"])
        self.ditunnel("off")

    def test_off_works_after_the_shell_that_ran_on_has_exited(self):
        """Amendment 8, question 4: ``on`` returned and its process is gone;
        a NEW process's ``off`` reaches the controller over its socket, and
        the controller signals only its own child's group."""
        started = self.on()[1]
        self.assertNotEqual(started["controller_pid"], os.getpid())
        code, result = self.ditunnel("off")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["stopped_by"], "controller")
        self.assertTrue(wait_until(lambda: not alive(started["pid"])))


# ====================================================================
# Reliable shutdown: the acceptance bar
# ====================================================================


class ReliableShutdownTests(TunnelFixture):

    def test_a_member_that_outlives_its_leader_is_cleaned_up(self):
        """THE ACCEPTANCE CASE. SIGTERM makes the leader exit while a member
        of its group ignores SIGTERM. The leader is still the controller's
        unreaped child (a zombie), so the group stays anchored and SIGKILL
        reaches the member. ``off`` reports stopped only once the group is
        observed gone. An unrelated process named cloudflared is untouched."""
        stranger = self.unrelated()
        code, started = self.on("member_ignores_term")
        self.assertEqual(code, 0, started)
        leader = self.noted("member_ignores_term")
        member = self.noted("member_ignores_term", ".member")
        self.assertEqual(os.getpgid(member), leader)
        code, result = self.ditunnel("off")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["observed"])
        self.assertEqual(result["signalled"]["signals"], ["SIGTERM", "SIGKILL"])
        self.assertTrue(wait_until(lambda: not alive(leader)))
        self.assertTrue(wait_until(lambda: not alive(member)))
        self.assertEqual(common.group_state(leader), common.GROUP_GONE)
        self.assertIsNone(stranger.poll())
        self.assertFalse(stopped(stranger.pid))
        self.assertIsNone(common.load(self.state))

    def test_the_anchored_sweep_in_process(self):
        """The acceptance case through the IN-PROCESS controller (the mutant
        check's target): ``off`` from another thread while this process
        holds the anchor."""
        results = {}

        def stop():
            if wait_until(lambda: tunnel.status(self.state)["state"] == "on", 30):
                results["off"] = tunnel.off(self.state)
        thread = threading.Thread(target=stop)
        thread.start()
        code, started = self.foreground("member_ignores_term")
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertEqual(code, 0, started)
        self.assertTrue(results["off"]["observed"])
        member = self.noted("member_ignores_term", ".member")
        self.assertTrue(wait_until(lambda: not alive(member)))
        self.assertTrue(wait_until(lambda: not alive(started["pid"])))

    def test_a_leader_ignoring_sigterm_is_killed(self):
        self.on("stubborn")
        leader = self.noted("stubborn")
        result = self.ditunnel("off")[1]
        self.assertTrue(result["observed"])
        self.assertIn("SIGKILL", result["signalled"]["signals"])
        self.assertTrue(wait_until(lambda: not alive(leader)))

    def test_a_cooperative_group_stops_on_sigterm_alone(self):
        self.on("grandchild")
        leader = self.noted("grandchild")
        member = self.noted("grandchild", ".member")
        result = self.ditunnel("off")[1]
        self.assertEqual(result["signalled"]["signals"], ["SIGTERM"])
        self.assertTrue(wait_until(lambda: not alive(member)))
        self.assertTrue(wait_until(lambda: not alive(leader)))

    def test_retry_exhaustion_releases_the_anchor_within_the_bound(self):
        """Round 10: when the stop is never observed (modelled: every group
        probe reports a live member), the controller retries for its retry
        window, then KEEPS the record as ``stop_unconfirmed`` and EXITS. The
        anchor is released on this ordinary path, with no crash. The elapsed
        time is within the stated worst-case bound, and a later ``off``
        (no controller) resolves the record once nothing runs."""
        grace, kill_grace, window = 0.5, 0.5, 1.5
        results = {}

        def stop():
            if wait_until(lambda: tunnel.status(self.state)["state"] == "on", 30):
                results["t0"] = time.monotonic()
                try:
                    tunnel.off(self.state)
                except common.TunnelError as exc:
                    results["refused"] = str(exc)
        thread = threading.Thread(target=stop)
        thread.start()
        with mock.patch.object(common, "group_state",
                               lambda pgid: common.GROUP_MEMBERS):
            code, started = self.foreground(stop_grace=grace,
                                            kill_grace=kill_grace,
                                            max_unconfirmed=window)
        elapsed = time.monotonic() - results["t0"]
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertEqual(code, 1, started)
        self.assertIn("STILL alive", results["refused"])
        record = common.load(self.state)
        self.assertEqual(record["state"], "stop_unconfirmed")
        self.assertGreaterEqual(elapsed, window)
        self.assertLessEqual(elapsed, worst_case_stop_seconds(
            grace, kill_grace, window) + 1.0)
        self.assertTrue(wait_until(lambda: not alive(started["pid"])))
        code, resolved = self.ditunnel("off")
        self.assertEqual(code, 0, resolved)
        self.assertTrue(resolved["already_off"])

    def test_a_tunnel_that_exits_by_itself_has_its_members_swept(self):
        """cloudflared exits on its own while a member of its group remains:
        the controller sees the exit without reaping, sweeps the anchored
        group, observes it gone, removes the record and exits."""
        started = self.on("exit_later_member")[1]
        member = self.noted("exit_later_member", ".member")
        self.assertTrue(wait_until(lambda: not alive(member), 30))
        self.assertTrue(wait_until(lambda: common.load(self.state) is None, 30))
        self.assertTrue(wait_until(lambda: not alive(started["controller_pid"])))
        self.assertEqual(self.ditunnel("status")[1]["state"], "off")


# ====================================================================
# Startup: failure, no URL, URL then exit, crash windows, unownable
# ====================================================================


class StartupTests(TunnelFixture):

    def assert_nothing_left(self, spawned=None):
        if spawned is not None:
            self.assertTrue(wait_until(lambda: not alive(spawned)))
        self.assertIsNone(common.load(self.state))

    def test_a_cloudflared_that_exits_before_its_url_leaves_nothing(self):
        code, refused = self.on("fail")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("exited before printing", refused["reason"])
        self.assertIn("nothing it started is left running", refused["reason"])
        self.assert_nothing_left(self.noted("fail"))

    def test_no_url_in_time_is_stopped_and_refused(self):
        code, result = self.foreground("silent", startup_timeout=1.5)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("no tunnel URL", result["reason"])
        self.assert_nothing_left(self.noted("silent"))

    def test_a_url_followed_by_exit_is_never_reported_as_on(self):
        """Review target 1(a): the URL is NOT the success signal. The child
        must still be alive after the settle period, so a URL whose process
        has exited is never returned."""
        code, refused = self.on("url_then_exit")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("already dead", refused["reason"])
        self.assertNotIn("url", refused)
        self.assert_nothing_left()
        self.assertIsNone(self.ditunnel("status")[1]["url"])

    def test_a_url_followed_by_exit_is_refused_in_process(self):
        """The same, through the in-process controller (the mutant check's
        target)."""
        code, result = self.foreground("url_then_exit")
        self.assertEqual(code, cli_module.EXIT_REFUSED, result)
        self.assertIn("already dead", result["reason"])
        self.assert_nothing_left()

    def checkpoint_pids(self):
        seen = {}

        def checkpoint(name, controller):
            if controller.process is not None:
                seen["spawned"] = controller.process.pid
        return seen, checkpoint

    def test_an_unreadable_start_time_is_a_startup_failure(self):
        """Review target 1(b): a tunnel whose identity cannot be recorded is
        never started, so no record can name one that cannot be owned."""
        seen, checkpoint = self.checkpoint_pids()
        code, result = self.foreground(start_time=lambda pid: None,
                                       checkpoint=checkpoint)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("start time could not be read", result["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.base, "ok.pid")),
                         "cloudflared ran although its identity was unrecorded")
        self.assert_nothing_left(seen["spawned"])

    def test_the_crash_windows_after_the_spawn_leave_no_orphan(self):
        """Review target 3: an exception after the spawn, while (a) the
        start time is read, (b) the pid is persisted, or (c) the identity
        is persisted. The gate never released cloudflared, the gate process
        is reaped, and the record is removed only because that was
        observed."""
        original_save = common.save

        def failing_save(at):
            calls = []

            def save(directory, record):
                calls.append(record.get("state"))
                if len(calls) == at:
                    raise OSError("injected save failure")
                return original_save(directory, record)
            return save

        def raising(pid):
            raise RuntimeError("injected start-time failure")
        for label, patch in (
            ("start time raises", mock.patch.object(
                spawn_stamp, "leader_start_time", raising)),
            ("pid save fails", mock.patch.object(common, "save",
                                                 failing_save(2))),
            ("identity save fails", mock.patch.object(common, "save",
                                                      failing_save(3))),
        ):
            with self.subTest(window=label):
                seen, checkpoint = self.checkpoint_pids()
                with patch:
                    code, result = self.foreground(checkpoint=checkpoint)
                self.assertEqual(code, cli_module.EXIT_REFUSED, result)
                self.assertIn("nothing it started is left running",
                              result["reason"])
                self.assertFalse(os.path.exists(
                    os.path.join(self.base, "ok.pid")))
                self.assert_nothing_left(seen["spawned"])

    def test_a_missing_cloudflared_is_refused_and_never_installed(self):
        with self.assertRaises(common.TunnelError):
            tunnel.on(self.state, PORT, os.path.join(self.base, "absent"))
        with mock.patch.object(tunnel.shutil, "which", lambda name: None):
            with self.assertRaises(common.TunnelError) as caught:
                tunnel.on(self.state, PORT)
        self.assertIn("never installs", str(caught.exception))

    def test_a_shared_or_overlong_state_directory_is_refused(self):
        os.makedirs(self.state, mode=0o755)
        os.chmod(self.state, 0o755)
        with self.assertRaises(common.TunnelError):
            tunnel.status(self.state)
        deep = os.path.join(self.base, "d" * 60, "e" * 40)
        os.makedirs(deep, mode=0o700)
        with self.assertRaises(common.TunnelError) as caught:
            tunnel.on(deep, PORT, self.fixture())
        self.assertIn("too long", str(caught.exception))


# ====================================================================
# A signal during foreground startup and state persistence (matrix (b))
# ====================================================================

STARTUP_CHECKPOINTS = ("record_starting", "spawned", "pid_persisted",
                       "identity_persisted", "released", "ready")


class SignalDuringStartupTests(TunnelFixture):

    def test_a_stop_signal_at_every_startup_step_leaves_nothing_owned(self):
        """A launchd stop can land anywhere in startup. At each step:
        SIGTERM to the controller (this process). No owned child is left,
        and the record is removed only because the cleanup was OBSERVED."""
        stranger = self.unrelated()
        for step in STARTUP_CHECKPOINTS:
            with self.subTest(step=step):
                seen = {}

                def checkpoint(name, controller, step=step):
                    if controller.process is not None:
                        seen["spawned"] = controller.process.pid
                    if name == step:
                        os.kill(os.getpid(), signal.SIGTERM)
                code, result = self.foreground(checkpoint=checkpoint)
                self.assertEqual(code, 0, result)
                self.assertTrue(result["stopped"], result)
                self.assertIsNone(common.load(self.state))
                if "spawned" in seen:
                    self.assertTrue(wait_until(lambda: not alive(seen["spawned"])))
                for name in ("ok.pid", "ok.member"):
                    path = os.path.join(self.base, name)
                    if os.path.exists(path):
                        pid = int(open(path).read())
                        self.assertTrue(wait_until(lambda: not alive(pid)))
                        os.unlink(path)
        self.assertIsNone(stranger.poll())

    def test_an_unobservable_cleanup_keeps_the_recovery_record(self):
        """When the cleanup can NOT be observed complete (modelled: every
        probe of the group reports a live member), the record is KEPT with
        the tunnel's identity, never erased, and a later ``off`` resolves
        it once the group is in fact gone."""
        seen = {}

        def checkpoint(name, controller):
            if controller.process is not None:
                seen["spawned"] = controller.process.pid
            if name == "released":
                os.kill(os.getpid(), signal.SIGTERM)
        with mock.patch.object(common, "group_state",
                               lambda pgid: common.GROUP_MEMBERS):
            code, result = self.foreground(checkpoint=checkpoint,
                                           kill_grace=0.5, max_unconfirmed=0.5)
        self.assertTrue(result["stopped"], result)
        self.assertIn("record is KEPT", result["reason"])
        record = common.load(self.state)
        self.assertEqual(record["state"], "stop_unconfirmed")
        self.assertEqual(record["tunnel"]["pid"], seen["spawned"])
        self.assertTrue(wait_until(lambda: not alive(seen["spawned"])))
        code, resolved = self.ditunnel("off")
        self.assertEqual(code, 0, resolved)
        self.assertTrue(resolved["already_off"])
        self.assertIn("gone", resolved["stale_record_cleared"])
        self.assertIsNone(common.load(self.state))


# ====================================================================
# Round 11: a slow client can neither hold the controller nor delay a stop
# ====================================================================


class ServingFixture(TunnelFixture):
    """Helpers (no tests) for driving this test's own in-process controller
    from a helper thread, over this test's own AF_UNIX connections."""

    def dribble(self, results, seconds=6.0, every=0.3):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(every)
        try:
            client.connect(common.socket_path(self.state))
            results["connected"] = time.monotonic()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                client.send(b"{")
                try:
                    if client.recv(1) == b"":
                        results["closed"] = time.monotonic()
                        return
                except socket.timeout:
                    pass
        except OSError:
            results["closed"] = time.monotonic()
        finally:
            client.close()

    def run_with(self, orchestrate, **seams):
        results = {}
        thread = threading.Thread(target=orchestrate, args=(results,))
        thread.start()
        code, started = self.foreground(**seams)
        results["ended"] = time.monotonic()
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        return code, started, results


class SlowClientTests(ServingFixture):
    """A CONTAINED synthetic slow client: this test's own AF_UNIX
    connection to this test's own in-process controller. It sends one byte
    at a time, well inside any per-receive timeout, and never a newline."""

    def test_a_dribbling_client_is_abandoned_within_the_read_deadline(self):
        """The whole request read has one deadline: the dribbling connection
        is abandoned within ``REQUEST_READ_SECONDS``, and a ``status`` asked
        meanwhile is answered once it is."""
        def orchestrate(results):
            if not wait_until(lambda: tunnel.status(self.state)["state"] == "on",
                              30):
                return
            slow = threading.Thread(target=self.dribble, args=(results,))
            slow.start()
            time.sleep(0.3)
            asked = time.monotonic()
            results["status"] = tunnel.status(self.state)["state"]
            results["status_latency"] = time.monotonic() - asked
            slow.join(JOIN_SECONDS)
            results["off"] = tunnel.off(self.state)
        code, _, results = self.run_with(orchestrate)
        self.assertEqual(code, 0)
        self.assertIn("closed", results, "the slow read was never abandoned")
        self.assertLessEqual(results["closed"] - results["connected"],
                             controller_module.REQUEST_READ_SECONDS + 1.0)
        self.assertEqual(results["status"], "on")
        self.assertLessEqual(results["status_latency"],
                             controller_module.REQUEST_READ_SECONDS + 1.0)
        self.assertTrue(results["off"]["observed"])

    def test_a_stop_during_a_slow_read_is_handled_promptly(self):
        """SIGTERM to the controller while it is in the middle of the slow
        client's read: the read is abandoned within ``POLL_SECONDS`` and the
        anchored stop begins at once. The tunnel's leader (which exits on
        SIGTERM) is gone within about a second, not when the dribble
        ends."""
        def orchestrate(results):
            if not wait_until(lambda: tunnel.status(self.state)["state"] == "on",
                              30):
                return
            leader = self.noted("ok")
            slow = threading.Thread(target=self.dribble, args=(results,))
            slow.start()
            time.sleep(0.5)
            results["signalled"] = time.monotonic()
            signal.pthread_kill(threading.main_thread().ident, signal.SIGTERM)
            if wait_until(lambda: not alive(leader), 10):
                results["leader_gone"] = time.monotonic()
            slow.join(JOIN_SECONDS)
        code, _, results = self.run_with(orchestrate)
        self.assertEqual(code, 0)
        self.assertLessEqual(results["leader_gone"] - results["signalled"],
                             controller_module.POLL_SECONDS + 1.0)
        self.assertIsNone(common.load(self.state))


class RequestBoundsTests(ServingFixture):
    """Round 12: the size bound holds at request COMPLETION, and a reply to
    a client that never reads cannot delay a stop. Each test uses this
    test's own AF_UNIX connection to its own in-process controller."""

    def raw(self, *pieces, pause=0.3):
        """Send ``pieces`` (pausing between them), then read the reply until
        a newline or end-of-file. Returns the reply bytes (b"" if the
        request was refused)."""
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        try:
            client.connect(common.socket_path(self.state))
            for index, piece in enumerate(pieces):
                if index:
                    time.sleep(pause)
                client.sendall(piece)
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
            return data
        except OSError:
            return b""
        finally:
            client.close()

    @staticmethod
    def status_request(length):
        """A valid status request of exactly ``length`` bytes, newline
        included."""
        head, tail = b'{"op": "status", "pad": "', b'"}\n'
        return head + b"x" * (length - len(head) - len(tail)) + tail

    def test_the_size_bound_holds_at_request_completion(self):
        limit = common.MAX_MESSAGE_BYTES

        def orchestrate(results):
            if not wait_until(lambda: tunnel.status(self.state)["state"] == "on",
                              30):
                return
            over = self.status_request(limit + 50)
            # Exactly the limit is read first, then the completing chunk:
            # the chunk that would have bypassed a check made only before
            # each receive.
            results["completing_chunk"] = self.raw(over[:limit], over[limit:])
            results["limit_plus_one"] = self.raw(self.status_request(limit + 1))
            results["exactly_limit"] = self.raw(self.status_request(limit))
            results["off"] = tunnel.off(self.state)
        code, _, results = self.run_with(orchestrate)
        self.assertEqual(code, 0)
        self.assertEqual(results["completing_chunk"], b"")
        self.assertEqual(results["limit_plus_one"], b"")
        self.assertTrue(json.loads(results["exactly_limit"])["ok"])
        self.assertTrue(results["off"]["observed"])

    def test_a_client_that_never_reads_cannot_delay_a_stop(self):
        """A status reply too large for the socket buffers, to a client that
        never reads, with the reply bound raised to 3 s: SIGTERM during that
        reply is observed within ``POLL_SECONDS`` and the anchored stop
        begins at once (the tunnel's leader is gone within about a second),
        not when the reply bound runs out."""
        def big(self_):
            return {"ok": True, "pad": "x" * 2000000}

        def orchestrate(results):
            if not wait_until(lambda: os.path.exists(common.socket_path(self.state))
                              and (common.load(self.state) or {}).get("state")
                              == "running", 30):
                return
            leader = self.noted("ok")
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5)
            try:
                client.connect(common.socket_path(self.state))
                client.sendall(b'{"op": "status"}\n')
                time.sleep(0.5)
                results["signalled"] = time.monotonic()
                signal.pthread_kill(threading.main_thread().ident,
                                    signal.SIGTERM)
                if wait_until(lambda: not alive(leader), 10):
                    results["leader_gone"] = time.monotonic()
            finally:
                client.close()
        with mock.patch.object(controller_module.Controller, "status_reply",
                               big), \
                mock.patch.object(controller_module, "REPLY_SECONDS", 3.0):
            code, _, results = self.run_with(orchestrate)
        self.assertEqual(code, 0)
        self.assertLessEqual(results["leader_gone"] - results["signalled"],
                             controller_module.POLL_SECONDS + 1.0)
        self.assertIsNone(common.load(self.state))

    def test_bookkeeping_after_retry_exhaustion_can_leave_a_stale_record(self):
        """Round 12, the unobserved branch: when the retries run out, the
        controller takes the (unbounded) state lock to mark the record. Held
        here by the test, that wait leaves the record saying ``running``
        (stale) with the controller still waiting, which is exactly what a
        launchd kill at that moment would leave. Released, the record
        becomes ``stop_unconfirmed``."""
        grace, kill_grace, window = 0.5, 0.5, 1.0

        def orchestrate(results):
            if not wait_until(lambda: tunnel.status(self.state)["state"] == "on",
                              30):
                return
            descriptor = os.open(os.path.join(self.state, common.LOCK_FILE_NAME),
                                 os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    tunnel.off(self.state)
                except common.TunnelError as exc:
                    results["refused"] = str(exc)
                time.sleep(worst_case_stop_seconds(grace, kill_grace, window)
                           + 0.5)
                results["while_waiting"] = common.load(self.state)["state"]
                results["controller_waiting"] = "ended" not in results
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        with mock.patch.object(common, "group_state",
                               lambda pgid: common.GROUP_MEMBERS):
            code, _, results = self.run_with(
                orchestrate, stop_grace=grace, kill_grace=kill_grace,
                max_unconfirmed=window)
        self.assertEqual(code, 1)
        self.assertIn("STILL alive", results["refused"])
        self.assertEqual(results["while_waiting"], "running")
        self.assertTrue(results["controller_waiting"])
        self.assertEqual(common.load(self.state)["state"], "stop_unconfirmed")


# ====================================================================
# The owner never exits without cleanup or a deliberately kept anchor
# ====================================================================


class _BrokenPipe(io.StringIO):
    def write(self, text):
        raise BrokenPipeError(32, "the on client is gone")


class _SignalOnEmission(io.StringIO):
    def write(self, text):
        os.kill(os.getpid(), signal.SIGTERM)
        return super(_SignalOnEmission, self).write(text)


class OwnerExitTests(TunnelFixture):

    def test_a_client_gone_before_the_result_keeps_the_anchor_served(self):
        """Review target 1, case 1: the result cannot be delivered (broken
        pipe) after a successful start. The controller deliberately KEEPS
        the anchor and goes on serving, the record intact; a later ``off``
        reaches it and the stop is observed."""
        results = {}

        def stop():
            if wait_until(lambda: tunnel.status(self.state)["state"] == "on", 30):
                results["record"] = common.load(self.state)
                results["off"] = tunnel.off(self.state)
        thread = threading.Thread(target=stop)
        thread.start()
        code, _ = self.foreground(out=_BrokenPipe())
        thread.join(JOIN_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertEqual(code, 0)
        self.assertEqual(results["record"]["state"], "running")
        self.assertTrue(results["off"]["observed"])
        self.assertTrue(wait_until(lambda: not alive(self.noted("ok"))))
        self.assertIsNone(common.load(self.state))

    def test_the_detached_controller_survives_its_on_client_vanishing(self):
        """Case 1 through the REAL process: the controller is started as
        ``on`` would start it, and its stdout pipe is closed unread. It logs
        that the result was not delivered, keeps serving, and ``off``
        works."""
        process = subprocess.Popen(
            [sys.executable, "-m", "tunnel_control.controller",
             "--state-dir", common.state_dir(self.state), "--port", str(PORT),
             "--cloudflared", self.fixture(), "--stop-grace", "1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(REPO_ROOT),
            start_new_session=True)
        self.children.append(process)
        process.stdout.close()
        self.assertTrue(wait_until(
            lambda: tunnel.status(self.state)["state"] == "on", 30))
        result = tunnel.off(self.state)
        self.assertTrue(result["observed"])
        self.assertEqual(process.wait(timeout=CHILD_TIMEOUT_SECONDS), 0)
        log = open(os.path.join(self.state, common.CONTROLLER_LOG_NAME)).read()
        self.assertIn("could not be delivered", log)

    def test_a_signal_during_result_emission_stops_and_cleans_up(self):
        """Case 2: SIGTERM between a successful start and the completed
        result. The controller is already serving, so the signal only
        requests a stop, and the anchored shutdown runs."""
        code, result = self.foreground(out=_SignalOnEmission())
        self.assertEqual(code, 0, result)
        self.assertTrue(wait_until(lambda: not alive(self.noted("ok"))))
        self.assertIsNone(common.load(self.state))

    def test_an_exception_out_of_serve_runs_the_anchored_shutdown(self):
        """Case 3: an arbitrary error out of the serving loop. The child is
        cleaned up (observed) before the error propagates."""
        stranger = self.unrelated()
        with mock.patch.object(controller_module.Controller, "wait_for_client",
                               side_effect=OSError("injected serve failure")):
            with self.assertRaises(OSError):
                self.foreground()
        self.assertTrue(wait_until(lambda: not alive(self.noted("ok"))))
        self.assertIsNone(common.load(self.state))
        self.assertFalse(os.path.exists(common.socket_path(self.state)))
        self.assertIsNone(stranger.poll())


# ====================================================================
# Stale state, reused pids, and the controller gone: report, never signal
# ====================================================================


class OwnershipTests(TunnelFixture):

    def write_record(self, pid, start, url=None):
        """A record as the controller writes it, naming ``pid``, with no
        controller serving it."""
        common.state_dir(self.state)
        nonce = os.urandom(16).hex()
        run = common.run_dir(self.state, nonce)
        os.makedirs(run, mode=0o700)
        log = os.path.join(run, common.LOG_FILE_NAME)
        with open(log, "w") as handle:
            handle.write("INF |  %s  |\n" % url if url else "")
        common.save(self.state, {
            "schema_version": 1, "state": "running", "nonce": nonce,
            "mode": "shell", "config": common.tunnel_config(self.fixture(), PORT),
            "controller": {"pid": None}, "log": log, "started_at": 1,
            "tunnel": {"pid": pid, "pgid": pid, "start": start}})

    def test_a_stale_pid_record_reports_off_and_its_url_only_as_stale(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=CHILD_TIMEOUT_SECONDS)
        self.write_record(dead.pid, "Thu Jan  1 00:00:00 1970",
                          "https://0ld.trycloudflare.com")
        status = self.ditunnel("status")[1]
        self.assertEqual((status["state"], status["url"]), ("off", None))
        self.assertEqual(status["stale_url"], "https://0ld.trycloudflare.com")
        code, cleared = self.ditunnel("off")
        self.assertEqual(code, 0)
        self.assertIsNone(cleared["signalled"])
        self.assertTrue(cleared["stale_record_cleared"])
        started = self.on()[1]
        self.assertNotEqual(started["url"], "https://0ld.trycloudflare.com")
        self.ditunnel("off")

    def test_a_pid_reused_by_an_unrelated_group_leader_is_never_signalled(self):
        """The recorded pid now holds an UNRELATED process that leads its own
        group: it is not this tool's. ``status`` never reports its URL as
        active, ``off`` signals nothing and KEEPS the record, ``on`` will
        not start over it, and ``forget`` (a human's decision) moves the
        record aside intact."""
        stranger = self.unrelated()
        self.write_record(stranger.pid, "Thu Jan  1 00:00:00 1970",
                          "https://0e05ed.trycloudflare.com")
        status = tunnel.status(self.state)
        self.assertEqual((status["state"], status["url"]), ("unknown", None))
        with self.assertRaises(common.TunnelError) as caught:
            tunnel.off(self.state)
        self.assertIn("Nothing was signalled", str(caught.exception))
        self.assertIsNotNone(common.load(self.state))
        code, refused = self.on(tag="ok-after")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("not resolved", refused["reason"])
        record = common.load(self.state)
        code, forgot = self.ditunnel("forget")
        self.assertEqual(code, 0, forgot)
        self.assertEqual(json.load(open(forgot["forgot"])), record)
        self.assertIsNone(stranger.poll())
        self.assertFalse(stopped(stranger.pid))

    def test_a_pid_reused_by_a_non_leader_is_over_and_never_signalled(self):
        stranger = self.unrelated(own_session=False)
        self.write_record(stranger.pid, "Thu Jan  1 00:00:00 1970")
        code, cleared = self.ditunnel("off")
        self.assertEqual(code, 0, cleared)
        self.assertIsNone(cleared["signalled"])
        self.assertIn("now belongs to another process",
                      cleared["stale_record_cleared"])
        self.assertIsNone(stranger.poll())

    def test_an_unrelated_cloudflared_is_never_signalled(self):
        stranger = self.unrelated()
        self.ditunnel("off")
        self.on()
        self.ditunnel("off")
        self.assertIsNone(stranger.poll())
        self.assertFalse(stopped(stranger.pid))

    def test_start_time_equality_is_the_test(self):
        """An EQUAL ``ps -o lstart=`` value is required for even the
        inference; any other is a different process. (Equality is
        "unchanged as observed", at one-second granularity: not proof of no
        reuse, and never used to signal.)"""
        stranger = self.unrelated()
        live = spawn_stamp.leader_start_time(stranger.pid)
        record = {"tunnel": {"pid": stranger.pid, "start": live}}
        self.assertEqual(common.classify(record, spawn_stamp.leader_start_time)[0],
                         common.OWNED)
        self.assertNotEqual(common.classify(
            record, lambda pid: live + "x")[0], common.OWNED)

    def test_without_its_controller_off_signals_nothing_and_keeps_the_record(self):
        """The controller itself is SIGKILLed while the tunnel lives. Its
        anchor is gone, so the tool sends NO signal of any kind: ``status``
        reports ``unverified`` (the URL only as ``unverified_url``, never
        active), ``off`` refuses naming the recorded group for MANUAL
        recovery, and the record is kept. After the operator's own manual
        recovery, ``off`` clears the record."""
        controller, started = self.start_controller("member_ignores_term")
        leader = self.noted("member_ignores_term")
        member = self.noted("member_ignores_term", ".member")
        self.kill_controller(controller)
        status = tunnel.status(self.state)
        self.assertEqual((status["state"], status["url"]), ("unverified", None))
        self.assertEqual(status["unverified_url"], started["url"])
        self.assertTrue(status["ownership"].startswith("unverified"))
        record = common.load(self.state)
        with self.assertRaises(common.TunnelError) as caught:
            tunnel.off(self.state)
        refused = str(caught.exception)
        self.assertIn("sends NO signal", refused)
        self.assertIn("NOT SUPPORTED", refused)
        self.assertIn("process group %d" % leader, refused)
        for pid in (leader, member):
            self.assertTrue(alive(pid))
            self.assertFalse(stopped(pid))
        self.assertEqual(common.load(self.state), record)
        code, refused = self.ditunnel("forget")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("stop it yourself", refused["reason"])
        self.operator_recovers("member_ignores_term", leader)
        code, cleared = self.ditunnel("off")
        self.assertEqual(code, 0, cleared)
        self.assertIsNone(cleared["signalled"])
        self.assertIsNone(common.load(self.state))

    def test_the_record_is_bookkeeping_not_access_control(self):
        """Amendment 16, shown rather than asserted: a same-user process
        that writes the state directory (as any worker could) with a
        stranger's REAL start time makes the tool REPORT that stranger as
        an unverified tunnel. The tool still signals nothing for it; but the
        record does not stop a same-user writer, who could signal the
        stranger, or the real tunnel, directly anyway."""
        stranger = self.unrelated()
        self.write_record(stranger.pid,
                          spawn_stamp.leader_start_time(stranger.pid))
        self.assertEqual(tunnel.status(self.state)["state"], "unverified")
        with self.assertRaises(common.TunnelError):
            tunnel.off(self.state)
        self.assertIsNone(stranger.poll())
        self.assertFalse(stopped(stranger.pid))

    def test_the_unresolved_case_is_reported_not_guessed(self):
        """The controller is gone AND the leader has already exited while a
        member of its group survives. Nothing anchors the group: nothing is
        signalled, ``status`` reports ``unknown`` (never a live URL) and the
        record is kept."""
        controller, started = self.start_controller("member_ignores_term")
        leader = self.noted("member_ignores_term")
        member = self.noted("member_ignores_term", ".member")
        self.kill_controller(controller)
        # The leader exits by itself (cooperatively; modelling a crash).
        Path(self.base, "member_ignores_term.stop-leader").touch()
        self.assertTrue(wait_until(lambda: not alive(leader)))
        status = self.ditunnel("status")[1]
        self.assertEqual((status["state"], status["url"]), ("unknown", None))
        code, refused = self.ditunnel("off")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("still has members", refused["reason"])
        self.assertTrue(alive(member))
        self.assertIsNotNone(common.load(self.state))


# ====================================================================
# The launchd shape (foreground) and its artifact
# ====================================================================


class ForegroundJobTests(TunnelFixture):

    def start_job(self, mode="ok"):
        job = subprocess.Popen(
            [sys.executable, str(ENTRY_SCRIPT), "--state-dir", self.state,
             "foreground", "--port", str(PORT), "--cloudflared",
             self.fixture(mode), "--stop-grace", "1"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.children.append(job)
        self.addCleanup(job.stdout.close)
        started = json.loads(job.stdout.readline())
        return job, started

    def test_the_job_is_the_controller_and_stops_on_sigterm(self):
        """What ``launchctl kill TERM`` (or a bootout) sends: the job's main
        process, the controller, runs the anchored shutdown and exits 0."""
        job, started = self.start_job("member_ignores_term")
        self.assertEqual(started["mode"], "launchd")
        self.assertEqual(started["controller_pid"], job.pid)
        member = self.noted("member_ignores_term", ".member")
        job.send_signal(signal.SIGTERM)
        self.assertEqual(job.wait(timeout=CHILD_TIMEOUT_SECONDS), 0)
        self.assertTrue(wait_until(lambda: not alive(started["pid"])))
        self.assertTrue(wait_until(lambda: not alive(member)))
        self.assertIsNone(common.load(self.state))

    def test_termination_before_the_stop_completes_loses_the_anchor(self):
        """Round 10: what launchd does if ``ExitTimeOut`` runs out before the
        controller's stop completes, modelled (no launchctl): SIGTERM to the
        job, then SIGKILL through the test's own handle while the stop is
        still in its SIGTERM grace. The anchor is LOST. The tunnel (whose
        leader ignores SIGTERM) is left running, the record is kept as last
        saved, and the tool then signals nothing: recovery is manual."""
        job = subprocess.Popen(
            [sys.executable, str(ENTRY_SCRIPT), "--state-dir", self.state,
             "foreground", "--port", str(PORT), "--cloudflared",
             self.fixture("stubborn"), "--stop-grace", "5"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.children.append(job)
        self.addCleanup(job.stdout.close)
        started = json.loads(job.stdout.readline())
        job.send_signal(signal.SIGTERM)
        time.sleep(1.0)
        job.kill()
        job.wait(timeout=CHILD_TIMEOUT_SECONDS)
        self.assertTrue(alive(started["pid"]))
        record = common.load(self.state)
        self.assertEqual(record["state"], "running")
        self.assertEqual(tunnel.status(self.state)["state"], "unverified")
        with self.assertRaises(common.TunnelError) as caught:
            tunnel.off(self.state)
        self.assertIn("NOT SUPPORTED", str(caught.exception))
        self.assertTrue(alive(started["pid"]))

    def test_off_from_a_shell_stops_the_job_and_the_job_exits(self):
        job, started = self.start_job()
        result = self.ditunnel("off")[1]
        self.assertTrue(result["observed"])
        self.assertEqual(job.wait(timeout=CHILD_TIMEOUT_SECONDS), 0)

    def test_a_killed_job_leaves_a_tunnel_for_manual_recovery(self):
        """Amendment 8, question 1, the part fixtures CAN show: the tunnel is
        in its own session, so it outlives a SIGKILLed job. (launchd's
        default process-group cleanup would target the JOB's group, which
        the tunnel is not in: [U] until a live install.) The tool then
        signals nothing; the record is kept for manual recovery."""
        job, started = self.start_job()
        job.kill()
        job.wait(timeout=CHILD_TIMEOUT_SECONDS)
        self.assertTrue(alive(started["pid"]))
        self.assertNotEqual(os.getpgid(started["pid"]), job.pid)
        code, refused = self.ditunnel("off")
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertIn("NOT SUPPORTED", refused["reason"])
        self.assertTrue(alive(started["pid"]))
        self.assertIsNotNone(common.load(self.state))


class LaunchdArtifactTests(unittest.TestCase):

    def test_the_artifact_defaults_off_and_runs_the_controller(self):
        with open(PLIST, "rb") as handle:
            job = plistlib.load(handle)
        self.assertIs(job.get("RunAtLoad"), False)
        self.assertNotIn("KeepAlive", job)
        self.assertNotEqual(job["Label"], LIVE_LABEL)
        arguments = job["ProgramArguments"]
        self.assertIn("foreground", arguments)
        self.assertNotIn("on", arguments)
        self.assertIn("PORT", arguments)
        for value in arguments:
            if value.startswith("/"):
                self.assertTrue(value.startswith("/ABSOLUTE/PATH/TO/"), value)
        # Round 10: ExitTimeOut must cover the WORST-CASE bounded stop
        # (retries included) at the grace the plist actually runs with: it
        # passes no --stop-grace, so the default.
        self.assertNotIn("--stop-grace", arguments)
        bound = worst_case_stop_seconds(
            controller_module.STOP_GRACE_SECONDS,
            controller_module.KILL_GRACE_SECONDS,
            controller_module.MAX_UNCONFIRMED_SECONDS)
        self.assertAlmostEqual(bound, 343.05)
        self.assertGreater(job["ExitTimeOut"], bound)

    def test_no_tool_code_drives_launchctl(self):
        for path in sorted((REPO_ROOT / "tunnel_control").glob("*.py")) + [
            ENTRY_SCRIPT
        ]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set(id(node.body[0].value) for node in ast.walk(tree)
                             if isinstance(node, (ast.Module, ast.FunctionDef,
                                                  ast.ClassDef))
                             and node.body and isinstance(node.body[0], ast.Expr))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(
                    node.value, str
                ) and id(node) not in docstrings:
                    self.assertNotIn("launchctl", node.value, path.name)


# ====================================================================
# Local-shell only: no MCP tool, no worker grant (necessary, NOT
# sufficient: a same-user process can still run the tool itself)
# ====================================================================

TUNNEL_NAMES = ("ditunnel", "tunnel_control", "cloudflared")

# Descriptive data blocks allowed to NAME the tool outside a docstring, as
# (module relpath, top-level name). Production needs none: grok_bot/server.py's
# setup text names the tunnel by its document, and its PUBLIC_REACHABILITY is
# not wholly literal (it holds a name, an attribute and a call), so it could
# not qualify anyway. The rule is exercised by parsed-source fixtures.
DESCRIPTIVE_DATA = frozenset()

# The ONLY node types a descriptive block's value may contain.
_LITERAL_NODES = (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Constant,
                  ast.Load, ast.UnaryOp, ast.UAdd, ast.USub)


def _descriptive_block(statement, relpath, descriptive):
    """Whether a TOP-LEVEL statement is exactly the intended descriptive
    case: a single plain-name target that ``descriptive`` lists for this
    module, assigned a dict whose value is WHOLLY literal (no call, name,
    attribute, walrus, lambda, comprehension or any other expression
    anywhere in it)."""
    return (isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and (relpath, statement.targets[0].id) in descriptive
            and isinstance(statement.value, ast.Dict)
            and all(isinstance(node, _LITERAL_NODES)
                    for node in ast.walk(statement.value)))


def tool_references(source, relpath, descriptive=DESCRIPTIVE_DATA):
    """What a STATIC scan of parsed ``source`` finds that names the tool. It
    scans IMPORTS and STRING LITERALS, and nothing else:

    - an import of ``tunnel_control`` or ``ditunnel``;
    - a string literal naming either, wherever it sits: inline in a call,
      bound to a variable before use (the known aliased-argv form), or a
      module path held in a name;
    - ``cloudflared`` anywhere in the text at all.

    Allowed, and only these: docstrings and bare prose string statements
    (no-ops), and every string in a ``_descriptive_block``. That is the exact
    case only: top level, single target, listed, a wholly literal dict.

    What it does NOT establish: it is not proof of every possible runtime
    reference, and not universal alias-proof reachability. Outside it:
    - a dynamically constructed name or a reference assembled at run time
      (for example ``"ditu" + "nnel"``, formatting, or ``getattr``);
    - a value read from elsewhere (another module, a file, the
      environment);
    - reachability through any indirection.

    Catching the known aliased form is not completeness."""
    found = []
    if "cloudflared" in source:
        found.append("mentions cloudflared")
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            allowed.add(id(node.value))
    for statement in tree.body:
        if _descriptive_block(statement, relpath, descriptive):
            allowed.update(id(node) for node in ast.walk(statement.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            if name.split(".")[0] in ("tunnel_control", "ditunnel"):
                found.append("imports %s" % name)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in allowed:
            for name in ("ditunnel", "tunnel_control"):
                if name in node.value:
                    found.append("line %d names %s" % (node.lineno, name))
    return found


class AbsenceTests(unittest.TestCase):

    def product_files(self):
        excluded = {"tests", "roles", "scripts", "__pycache__"}
        return sorted(
            path for path in REPO_ROOT.rglob("*.py")
            if not any(part in excluded or part.startswith(".")
                       for part in path.relative_to(REPO_ROOT).parts))

    def test_no_mcp_tool_exposes_tunnel_lifecycle(self):
        from grok_bot import adapter as adapter_module
        from grok_bot import mcp
        names = list(adapter_module.TOOLS) + [
            tool["name"] for tool in mcp.tool_definitions()]
        for name in names:
            for word in ("tunnel", "cloudflared", "ditunnel"):
                self.assertNotIn(word, name)
        text = json.dumps(mcp.tool_definitions()) + mcp.INSTRUCTIONS
        for word in TUNNEL_NAMES:
            self.assertNotIn(word, text)

    def test_no_other_product_module_reaches_the_tool(self):
        """A STATIC scan (``tool_references``: imports and string literals in
        parsed source) of every other product and herdr module finds nothing
        naming the tool outside docstrings and prose. This is not proof
        against every runtime reference (see ``tool_references``)."""
        own = {REPO_ROOT / "ditunnel.py"} | set(
            (REPO_ROOT / "tunnel_control").glob("*.py"))
        for path in self.product_files() + sorted((REPO_ROOT / "herdr").glob("*.py")):
            if path in own:
                continue
            relpath = path.relative_to(REPO_ROOT).as_posix()
            self.assertEqual(tool_references(
                path.read_text(encoding="utf-8"), relpath), [], relpath)

    def test_the_reference_scan_catches_an_aliased_argv(self):
        """Round 14 regression: the scan catches the known aliased form (an
        argv bound to a variable first, a module path held in a name), not
        only inline literals, and allows descriptive prose only."""
        caught = {
            "aliased argv": 'import subprocess\n'
                            'argv = ["/opt/dodging/ditunnel.py", "on"]\n'
                            'subprocess.run(argv)\n',
            "aliased module path": 'TOOL = "tunnel_control.cli"\n'
                                   '__import__(TOOL)\n',
            "inline argv": 'import subprocess\n'
                           'subprocess.run(["ditunnel.py", "off"])\n',
            "keyword": 'run(args=("x", "tunnel_control"))\n',
            "import": 'from tunnel_control import tunnel\n',
            "cloudflared, even in prose": '"""runs cloudflared"""\n',
        }
        for label, source in caught.items():
            with self.subTest(planted=label):
                self.assertTrue(tool_references(source, "planted/module.py"))
        descriptive = ('"""See ditunnel.py and tunnel_control."""\n'
                       'def f():\n'
                       '    """Names ditunnel, runs nothing."""\n'
                       '    "a prose note about tunnel_control"\n'
                       '    return 1\n')
        self.assertEqual(tool_references(descriptive, "planted/module.py"), [])

    def test_the_descriptive_exemption_is_the_exact_literal_case_only(self):
        """Round 15: the data exemption covers ONLY a top-level,
        single-target, wholly literal dict that is listed for its module.
        Parsed-source fixtures (never run), one per escape, must be CAUGHT;
        the genuine literal-only block stays exempt."""
        listed = frozenset({("planted/module.py", "SETUP")})
        escapes = {
            "a call inside the exempt assignment":
                'SETUP = {"a": __import__("subprocess").run(["ditunnel.py"])}\n',
            "a call next to the literal text":
                'SETUP = {"a": "or ditunnel.py", "b": run(["x"])}\n',
            "a name inside the value":
                'SETUP = {"a": "or ditunnel.py", "b": HOST}\n',
            "a mixed (chained) assignment":
                'SETUP = other = {"a": "or ditunnel.py"}\n',
            "a tuple target":
                'SETUP, other = {"a": "or ditunnel.py"}, 1\n',
            "an annotated assignment":
                'SETUP: dict = {"a": "or ditunnel.py"}\n',
            "a nested lookalike in a function":
                'def f():\n    SETUP = {"a": "or ditunnel.py"}\n',
            "a nested lookalike under if":
                'if True:\n    SETUP = {"a": "or ditunnel.py"}\n',
            "a walrus binding the same name":
                'if (SETUP := {"a": "or ditunnel.py"}):\n    pass\n',
            "a non-dict value":
                'SETUP = ["or ditunnel.py"]\n',
            "a comprehension inside the value":
                'SETUP = {"a": [x for x in ("tunnel_control",)]}\n',
        }
        for label, source in escapes.items():
            with self.subTest(escape=label):
                self.assertTrue(tool_references(source, "planted/module.py",
                                                listed), label)
        genuine = ('SETUP = {"action": "or ditunnel.py",\n'
                   '         "steps": ["on", "off", -1, ("a", 2.5)]}\n')
        self.assertEqual(tool_references(genuine, "planted/module.py", listed),
                         [])
        self.assertTrue(tool_references(genuine, "planted/other.py", listed))
        self.assertTrue(tool_references(genuine, "planted/module.py"))
        self.assertEqual(DESCRIPTIVE_DATA, frozenset())

    def test_no_worker_role_or_operator_contract_is_granted_the_tool(self):
        for path in sorted((REPO_ROOT / "roles").glob("*")) + [
            REPO_ROOT / "AGENTS.md", REPO_ROOT / "OPERATOR_PROTOCOL.md"
        ]:
            text = path.read_text(encoding="utf-8")
            for name in TUNNEL_NAMES:
                self.assertNotIn(name, text, path.name)

    def test_the_tool_imports_only_its_seams(self):
        allowed_roots = {"argparse", "errno", "json", "os", "re", "secrets",
                         "select", "shutil", "signal", "socket", "stat",
                         "subprocess", "sys", "time", "pathlib",
                         "target_runtime", "workflow_authority",
                         "tunnel_control"}
        for path in sorted((REPO_ROOT / "tunnel_control").glob("*.py")) + [
            REPO_ROOT / "ditunnel.py"
        ]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                self.assertEqual(sorted(set(roots) - allowed_roots), [],
                                 path.name)

    def test_only_the_controller_signals_and_reap_group_is_unused(self):
        """Structural: the client module (``tunnel.py``) and the shared
        module (``common.py``) send no signal at all (signal 0 probes
        excepted, which deliver nothing); only the controller, which holds
        the anchor, calls ``killpg`` with a real signal. And
        ``process_ownership.reap_group`` (single-pid fallback) appears
        nowhere."""
        for path in sorted((REPO_ROOT / "tunnel_control").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "reap_group", path.name)
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("kill", "killpg")):
                    continue
                probe = (len(node.args) == 2
                         and isinstance(node.args[1], ast.Constant)
                         and node.args[1].value == 0)
                if path.name != "controller.py":
                    self.assertTrue(probe, "%s signals at line %d"
                                    % (path.name, node.lineno))

    def test_the_scope_and_limits_are_stated_where_the_design_is(self):
        """A DOCUMENTATION PIN (not behavioural evidence): the Amendment 16
        scope, the ``lstart`` limit and the unsupported recovery are stated
        in the module and in docs/tunnel.md."""
        module = " ".join(tunnel.__doc__.split())
        doc = " ".join((REPO_ROOT / "docs" / "tunnel.md").read_text(
            encoding="utf-8").split())
        for text in (module, doc):
            for phrase in ("not designed to contain processes running with"
                           " the user's own privileges",
                           "unchanged AS OBSERVED",
                           "NOT atomic identity-safe signalling",
                           "necessary, but it is not sufficient",
                           "is NOT SUPPORTED by this tool",
                           "exhausted its retry budget with a member still"
                           " alive"):
                self.assertIn(phrase, text)
        self.assertIn("the anchor is RELEASED", doc)
        self.assertIn("ExitTimeOut` is 420 s", doc)
        self.assertIn("at most 343.05 s", doc)
        self.assertIn("--allowed-mail", doc)
        self.assertIn("No SSE", doc)


# ====================================================================
# Mutation self-check: each guard, removed IN MEMORY, turns red
# ====================================================================


def _no_settle_check(self, log):
    deadline = time.monotonic() + self.startup_timeout
    while time.monotonic() < deadline:
        url = common.last_url(log)
        if url:
            return url
        time.sleep(controller_module.POLL_SECONDS)
    raise common.TunnelError("no URL")


def _per_recv_timeout_only(self, conn):
    """The round-11 defect, reproduced: a per-receive timeout only, no
    total deadline, no stop check during the read."""
    conn.settimeout(controller_module.CLIENT_IO_SECONDS)
    data = b""
    try:
        while not data.endswith(b"\n") and len(data) <= common.MAX_MESSAGE_BYTES:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        request = json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return None
    return request if isinstance(request, dict) else None


def _size_checked_before_receive_only(self, conn):
    """The round-11 reader, reproduced: the size is tested only before each
    receive, so the completing chunk is never checked."""
    deadline = time.monotonic() + controller_module.REQUEST_READ_SECONDS
    data = b""
    while not data.endswith(b"\n"):
        if self.stop_requested or self.exited():
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0 or len(data) > common.MAX_MESSAGE_BYTES:
            return None
        conn.settimeout(min(remaining, controller_module.POLL_SECONDS))
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            return None
        if not chunk:
            break
        data += chunk
    try:
        request = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    return request if isinstance(request, dict) else None


def _reply_without_stop_poll(self, conn, document, poll_stop=True):
    """The round-11 reply, reproduced: one ``sendall`` bounded as a whole,
    with no stop poll."""
    try:
        conn.settimeout(controller_module.REPLY_SECONDS)
        conn.sendall((json.dumps(document, sort_keys=True) + "\n")
                     .encode("utf-8"))
    except OSError:
        pass


def _claims_to_stop_on_inference(directory, start_time):
    """The defect lead1 corrected (acting on an inferred identity), modelled
    WITHOUT sending anything: it reports a stop it would have signalled."""
    record = common.load(directory)
    if record and common.classify(record, start_time)[0] == common.OWNED:
        return {"ok": True, "state": "off",
                "signalled": {"process_group": record["tunnel"]["pgid"]}}
    return {"ok": True, "state": "off", "signalled": None}


MUTANTS = (
    ("a URL is success without the settle liveness check",
     controller_module.Controller, "_await_url", _no_settle_check,
     ("StartupTests.test_a_url_followed_by_exit_is_refused_in_process",)),
    ("the leader's exit alone counts as a quiet group",
     controller_module.Controller, "_group_quiet",
     lambda self, pgid: self.exited(),
     ("ReliableShutdownTests.test_the_anchored_sweep_in_process",)),
    ("a running tunnel is reported whatever its configuration", common,
     "compare_running",
     lambda reply, requested: {"ok": True, "already_on": True,
                               "url": reply.get("url"), "pid": reply.get("pid")},
     ("LifecycleTests.test_on_against_a_different_configuration_is_refused"
      "_by_name",)),
    ("any live recorded pid is inferred to be owned", common, "classify",
     lambda record, start_time: (common.OWNED, (record.get("tunnel") or {})
                                 .get("pid"), "mutant"),
     ("OwnershipTests.test_a_pid_reused_by_an_unrelated_group_leader_is"
      "_never_signalled",)),
    ("a request read has only a per-receive timeout (round 11)",
     controller_module.Controller, "_read_request", _per_recv_timeout_only,
     ("SlowClientTests.test_a_dribbling_client_is_abandoned_within_the_read"
      "_deadline",
      "SlowClientTests.test_a_stop_during_a_slow_read_is_handled_promptly")),
    ("the completing chunk escapes the size check (round 12)",
     controller_module.Controller, "_read_request",
     _size_checked_before_receive_only,
     ("RequestBoundsTests.test_the_size_bound_holds_at_request_completion",)),
    ("a reply does not poll for a pending stop (round 12)",
     controller_module.Controller, "_reply", _reply_without_stop_poll,
     ("RequestBoundsTests.test_a_client_that_never_reads_cannot_delay_a"
      "_stop",)),
    ("off signals on an inferred identity (controller gone)", tunnel,
     "_off_without_controller", _claims_to_stop_on_inference,
     ("OwnershipTests.test_without_its_controller_off_signals_nothing_and"
      "_keeps_the_record",)),
)


class MutationSelfCheckTests(unittest.TestCase):
    """A plain TestCase: every inner test keeps its own SIGALRM watchdog."""

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_every_tunnel_mutant_is_caught_and_the_original_passes(self):
        for label, owner, name, mutant, names in MUTANTS:
            with self.subTest(mutant=label):
                with mock.patch.object(owner, name, mutant):
                    broken = self.run_named(names)
                failed = set(getattr(test, "test_case", test).id()
                             for test, _ in broken.failures + broken.errors)
                self.assertEqual(len(failed), len(names), (label, failed))
                restored = self.run_named(names)
                self.assertTrue(restored.wasSuccessful(),
                                (label, restored.failures, restored.errors))


if __name__ == "__main__":
    unittest.main()
