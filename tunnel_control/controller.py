"""The tunnel CONTROLLER: the one long-lived process that owns a Quick Tunnel.

Why it exists. Reliable shutdown needs
ownership that is STRUCTURAL rather than inferred from a recyclable pid and a
one-second ``ps`` timestamp. The controller keeps ``cloudflared`` as its own
UNREAPED child for the tunnel's whole life. While a child is unreaped its pid
stays taken, and so does its process-group id: the child leads its own
group (``setsid``), and no other process can be given either number. So
everything the controller reaches with ``killpg`` on that id is the tunnel's
own group, with no pid-reuse question and no reliance on ``lstart``. It reaps
the child only after it has OBSERVED that no member of the group can still
be signalled.

Startup runs under the state lock:

1. Refuse if a controller is already serving (report it, or name the
   configuration mismatch), or if an earlier record is not observably over.
2. Write a ``starting`` record naming this controller.
3. Spawn the GATED child, ``python -c GATE cloudflared ...``, in its own
   session, with the stop signals blocked across the spawn so the handle is
   never lost. The gate waits on a pipe and execs ``cloudflared`` only when
   the controller writes ``1``. On end-of-file (the controller failed or
   died) it exits WITHOUT ever running cloudflared. ``exec`` keeps the pid
   and the start time, so the recorded identity is cloudflared's.
4. Persist the child's pid, then its start time (used only by the degraded
   path in ``tunnel_control.tunnel``). An unreadable start time is a startup
   failure.
5. Open the gate.
6. Wait for a trycloudflare URL in this run's own log, then require the
   child to stay alive through a settle period. A URL whose process then
   exits is never reported.
7. Persist ``running`` and bind the control socket.

A failure, or a stop signal, anywhere in steps 1-7 runs the anchored
shutdown before the refusal propagates. The record is removed only when that
shutdown was OBSERVED complete; otherwise it is kept as ``stop_unconfirmed``.

Serving: ``status`` and ``stop`` over ``ctl.sock`` in the owner-only state
directory, one JSON line each way. SIGTERM, SIGINT and SIGHUP request a
stop. If cloudflared exits by itself, the controller sweeps its group and
exits.

- A request read has one total deadline, and a size bound checked at
  completion.
- A pending stop is observed within ``POLL_SECONDS`` at every wait: accept,
  receive, and each status-reply piece (``_read_request``, ``_reply``).
- The reply to a client's own ``stop`` is bounded by ``REPLY_SECONDS``.

Record bookkeeping after the shutdown takes the state lock, and that wait is
unbounded. After an OBSERVED stop nothing is running. After retry exhaustion
members may be alive, and the record may not yet be marked
``stop_unconfirmed`` (see ``docs/tunnel.md``).

Shutdown is anchored, in this order:

1. SIGTERM to the group.
2. Wait (bounded) until the leader has exited AND no member can be
   signalled.
3. Otherwise SIGKILL to the group. It is still anchored, because the leader
   is unreaped, alive or a zombie.
4. Wait again.
5. Only then reap the leader, and wait for the group to be GONE.

Members that survive SIGKILL keep the anchor held only for a bounded time.
The controller retries without reaping for up to ``MAX_UNCONFIRMED_SECONDS``.
If a member is still alive then, the record is kept as ``stop_unconfirmed``
and the controller exits, RELEASING the anchor. That is an ordinary
anchor-loss path, without any SIGKILL or crash of the controller. Recovery is
then manual, never by inference.

What this does NOT do:

- A member that moved itself into ANOTHER process group or session is
  outside the group and is not reached. cloudflared is not known to do that,
  and the fixtures do not model it.
- The anchor is also lost when the controller itself is SIGKILLed or
  crashes, or when launchd terminates the job before its stop completes (for
  example, an ``ExitTimeOut`` shorter than the worst-case bounded stop).
- Whenever the anchor is lost, by any path including retry exhaustion, the
  tool signals NOTHING (``tunnel_control.tunnel``). Hard-kill recovery of a
  tunnel whose controller is gone is not supported, and is the operator's,
  done manually.
- It is not access control: like the Git gates, it is a workflow guardrail,
  not designed to contain processes running with the user's own privileges.
"""

import argparse
import json
import os
import secrets
import select
import signal
import socket
import subprocess
import sys
import time

from target_runtime import spawn_stamp
from tunnel_control import common

GATE = (
    "import os, signal, sys\n"
    "signal.pthread_sigmask(signal.SIG_SETMASK, [])\n"
    "if os.read(0, 1) != b'1':\n"
    "    os._exit(0)\n"
    "fd = os.open(os.devnull, os.O_RDONLY)\n"
    "os.dup2(fd, 0)\n"
    "os.close(fd)\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n"
)
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
STARTUP_TIMEOUT_SECONDS = 30.0
STARTUP_SETTLE_SECONDS = 1.0
STOP_GRACE_SECONDS = 10.0
KILL_GRACE_SECONDS = 5.0
RETRY_SECONDS = 1.0
MAX_UNCONFIRMED_SECONDS = 300.0
CLIENT_IO_SECONDS = 5.0
POLL_SECONDS = 0.05
# Serving is bounded per connection: a whole request read has ONE deadline,
# and a whole reply has one too (``sendall``'s timeout covers the full send).
REQUEST_READ_SECONDS = 2.0
REPLY_SECONDS = 1.0
OWNERSHIP = ("structural: the controller holds the tunnel process as its own"
             " unreaped child, so its pid and process-group id cannot be"
             " given to another process")


class Stop(BaseException):
    """A stop signal arrived before the controller was serving. A
    BaseException, so no ``except Exception`` on the way swallows it."""

    outcome = None


def cleanup_sentence(outcome):
    if outcome is None:
        return "nothing had been started"
    if outcome["observed"]:
        return "nothing it started is left running, and its record was removed"
    return ("its process group %s still had a live member after SIGKILL; the"
            " record is KEPT (state stop_unconfirmed) for `ditunnel off`"
            % (outcome.get("signalled") or {}).get("process_group"))


class Controller(object):

    def __init__(self, directory, port, binary, mode, start_time=None,
                 checkpoint=None, settle=None, startup_timeout=None,
                 grace=None, kill_grace=None, max_unconfirmed=None):
        self.directory, self.port, self.binary = directory, port, binary
        self.mode = mode
        self.start_time = start_time or spawn_stamp.leader_start_time
        self._checkpoint = checkpoint
        self.settle = STARTUP_SETTLE_SECONDS if settle is None else settle
        self.startup_timeout = (STARTUP_TIMEOUT_SECONDS if startup_timeout
                                is None else startup_timeout)
        self.grace = STOP_GRACE_SECONDS if grace is None else grace
        self.kill_grace = KILL_GRACE_SECONDS if kill_grace is None else kill_grace
        self.max_unconfirmed = (MAX_UNCONFIRMED_SECONDS if max_unconfirmed
                                is None else max_unconfirmed)
        self.process = self.nonce = self.record = self.listener = None
        self.gate_w = None
        self.phase = "idle"
        self.stop_requested = self.stopping = False
        self.exit_reason = None
        self._previous = {}

    def checkpoint(self, name):
        """A named point in startup (``record_starting``, ``spawned``,
        ``pid_persisted``, ``identity_persisted``, ``released``, ``ready``):
        the seam the tests use to land a signal or a fault exactly there."""
        if self._checkpoint is not None:
            self._checkpoint(name, self)

    @staticmethod
    def log(message):
        try:
            sys.stderr.write("ditunnel controller: %s\n" % message)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass

    # -- signals -----------------------------------------------------------

    def _on_signal(self, signum, frame):
        if self.stopping:
            return
        if self.phase == "serving":
            self.stop_requested = True
            return
        raise Stop(signum)

    def install_signals(self):
        self._previous = dict((sig, signal.signal(sig, self._on_signal))
                              for sig in STOP_SIGNALS)

    def restore_signals(self):
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        self._previous = {}

    # -- the child, observed without reaping it ----------------------------

    def exited(self):
        """Whether the child has exited, WITHOUT reaping it (``WNOWAIT``):
        an exited child stays a zombie, so the anchor holds."""
        if self.process is None or self.process.returncode is not None:
            return True
        try:
            return os.waitid(os.P_PID, self.process.pid,
                             os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True

    def _group_quiet(self, pgid):
        """The leader has exited and no member can be signalled."""
        return self.exited() and common.group_state(pgid) != common.GROUP_MEMBERS

    def _wait(self, predicate, seconds):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                return predicate()
            time.sleep(POLL_SECONDS)
        return True

    # -- startup -----------------------------------------------------------

    def start(self):
        """Start the tunnel and return the startup result, or raise
        ``TunnelError`` / ``Stop`` AFTER the anchored cleanup."""
        self.phase = "starting"
        with common.lock(self.directory):
            existing = self._existing()
            if existing is not None:
                return existing
            try:
                return self._start_locked()
            except BaseException as exc:
                outcome = self.shutdown_until_observed()
                self._finish_record(outcome)
                if isinstance(exc, Stop):
                    exc.outcome = outcome
                    raise
                reason = str(exc) if isinstance(exc, common.TunnelError) else (
                    "startup failed (%s: %s)" % (type(exc).__name__, exc))
                raise common.TunnelError("%s; %s" % (
                    reason, cleanup_sentence(outcome))) from exc

    def _existing(self):
        """A live tunnel's report (or a mismatch refusal), or None once
        nothing is left that could be running."""
        reply = common.ask(self.directory, "status", CLIENT_IO_SECONDS)
        if reply is not None:
            return common.compare_running(
                reply, common.tunnel_config(self.binary, self.port))
        record = common.load(self.directory)
        if record is not None:
            verdict, _, reason = common.classify(record, self.start_time)
            if verdict != common.OVER:
                raise common.TunnelError(
                    "an earlier tunnel record is not resolved (%s); run"
                    " `ditunnel status`. A tunnel whose controller is gone is"
                    " stopped manually (docs/tunnel.md, \"Manual recovery\"),"
                    " then `ditunnel forget`. Nothing was started" % reason)
            common.clear(self.directory)
        try:
            os.unlink(common.socket_path(self.directory))
        except FileNotFoundError:
            pass
        return None

    def _start_locked(self):
        d = self.directory
        self.nonce = secrets.token_hex(16)
        run = common.run_dir(d, self.nonce)
        os.makedirs(run, mode=0o700)
        log = os.path.join(run, common.LOG_FILE_NAME)
        config = common.tunnel_config(self.binary, self.port)
        self.record = {
            "schema_version": common.SCHEMA_VERSION, "nonce": self.nonce,
            "state": "starting", "mode": self.mode, "config": config,
            "controller": {"pid": os.getpid()}, "tunnel": None, "log": log,
            "started_at": int(time.time()),
        }
        common.save(d, self.record)
        self.checkpoint("record_starting")
        gate_r, self.gate_w = os.pipe()
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
        try:
            with open(log, "ab") as output:
                self.process = subprocess.Popen(
                    [sys.executable, "-c", GATE]
                    + common.quick_tunnel_argv(self.binary, self.port),
                    stdin=gate_r, stdout=output, stderr=output, cwd=run,
                    close_fds=True, start_new_session=True)
        finally:
            os.close(gate_r)
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        self.checkpoint("spawned")
        pid = self.process.pid
        self.record = dict(self.record, state="spawned",
                           tunnel={"pid": pid, "pgid": pid, "start": None})
        common.save(d, self.record)
        self.checkpoint("pid_persisted")
        start = self.start_time(pid)
        if not start:
            raise common.TunnelError(
                "the tunnel process's start time could not be read, so its"
                " identity could not be recorded; cloudflared was never"
                " started")
        self.record = dict(self.record,
                           tunnel=dict(self.record["tunnel"], start=start))
        common.save(d, self.record)
        self.checkpoint("identity_persisted")
        os.write(self.gate_w, b"1")
        os.close(self.gate_w)
        self.gate_w = None
        self.checkpoint("released")
        url = self._await_url(log)
        self.record = dict(self.record, state="running")
        common.save(d, self.record)
        self._listen()
        self.checkpoint("ready")
        # From here a stop signal only REQUESTS a stop (it never raises), so
        # nothing between this point and ``serve`` can lose the anchor.
        self.phase = "serving"
        return {"ok": True, "state": "on", "already_on": False, "url": url,
                "pid": pid, "config": config, "controller_pid": os.getpid(),
                "mode": self.mode, "ownership": OWNERSHIP}

    def _await_url(self, log):
        deadline = time.monotonic() + self.startup_timeout
        while True:
            if self.exited():
                printed = common.last_url(log)
                if printed:
                    raise common.TunnelError(
                        "cloudflared printed %s and then exited; that URL is"
                        " already dead, so it is not reported" % printed)
                raise common.TunnelError(
                    "cloudflared exited before printing a tunnel URL")
            url = common.last_url(log)
            if url:
                break
            if time.monotonic() >= deadline:
                raise common.TunnelError(
                    "cloudflared printed no tunnel URL within %.0fs"
                    % self.startup_timeout)
            time.sleep(POLL_SECONDS)
        if self._wait(self.exited, self.settle):
            raise common.TunnelError(
                "cloudflared printed %s and then exited; that URL is already"
                " dead, so it is not reported" % url)
        return url

    def _listen(self):
        path = common.socket_path(self.directory)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(path)
            os.chmod(path, 0o600)
            listener.listen(8)
        except BaseException:
            listener.close()
            raise
        self.listener = listener

    # -- serving -----------------------------------------------------------

    def status_reply(self):
        alive = not self.exited()
        url = common.last_url(self.record["log"]) if alive else None
        return {"ok": True, "nonce": self.nonce,
                "state": ("on" if url else "starting") if alive else "exited",
                "url": url, "pid": self.process.pid,
                "pgid": self.process.pid, "config": self.record["config"],
                "controller_pid": os.getpid(), "mode": self.mode,
                "ownership": OWNERSHIP}

    def _read_request(self, conn):
        """One request line, read under ONE TOTAL deadline
        (``REQUEST_READ_SECONDS``), not merely a per-``recv`` timeout, so a
        client dribbling bytes cannot hold the controller: past the deadline
        the request is abandoned.

        Size, settled deliberately: a request is at most
        ``common.MAX_MESSAGE_BYTES`` bytes, its terminating newline
        included. Exactly that many is accepted, and one more is refused.
        The size is checked after EVERY append, the chunk that completes the
        line included, and each ``recv`` asks for no more than the remaining
        allowance plus one.

        Each ``recv`` waits at most ``POLL_SECONDS``. A pending stop (the
        signal handler's flag) or the tunnel's own exit abandons the read:
        between receives, and again once the line is complete, so a
        completed request is never acted on while a stop is pending.
        Anything abandoned gets no reply."""
        deadline = time.monotonic() + REQUEST_READ_SECONDS
        data = b""
        while True:
            if self.stop_requested or self.exited():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            conn.settimeout(min(remaining, POLL_SECONDS))
            try:
                chunk = conn.recv(min(4096, common.MAX_MESSAGE_BYTES + 1
                                      - len(data)))
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            data += chunk
            if len(data) > common.MAX_MESSAGE_BYTES:
                return None
            if data.endswith(b"\n"):
                break
        if self.stop_requested or self.exited():
            return None
        try:
            request = json.loads(data.decode("utf-8"))
        except ValueError:
            return None
        return request if isinstance(request, dict) else None

    def _reply(self, conn, document, poll_stop=True):
        """One reply, bounded as a whole by ``REPLY_SECONDS`` and sent in
        pieces that wait at most ``POLL_SECONDS`` each. With ``poll_stop``
        (every reply but the one to a client's own ``stop``), a pending stop
        abandons the reply at once, so a client that does not read never
        delays a stop by more than ``POLL_SECONDS``. The reply to a ``stop``
        request is sent while the controller is already stopping (further
        stop signals are ignored then); it can take up to ``REPLY_SECONDS``,
        and the worst-case arithmetic counts that. Returns whether the whole
        reply was sent."""
        data = memoryview((json.dumps(document, sort_keys=True) + "\n")
                          .encode("utf-8"))
        deadline = time.monotonic() + REPLY_SECONDS
        while data:
            if poll_stop and self.stop_requested:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                conn.settimeout(min(remaining, POLL_SECONDS))
                sent = conn.send(data)
            except socket.timeout:
                continue
            except OSError:
                return False
            data = data[sent:]
        return True

    def wait_for_client(self, timeout):
        readable, _, _ = select.select([self.listener], [], [], timeout)
        return bool(readable)

    def serve(self):
        """Serve until a stop is requested or cloudflared exits, then shut
        down (anchored) and return the observed outcome."""
        self.phase = "serving"
        stop_conn = None
        try:
            while not self.stop_requested:
                if self.exited():
                    self.exit_reason = "tunnel_exited"
                    break
                if not self.wait_for_client(POLL_SECONDS):
                    continue
                try:
                    conn, _ = self.listener.accept()
                except OSError:
                    continue
                request = self._read_request(conn)
                if request is None:
                    # Abandoned (deadline, malformed, or a stop/exit observed
                    # mid-read): no reply, so a pending stop is not delayed.
                    conn.close()
                    continue
                op = request.get("op")
                if op == "stop":
                    stop_conn = conn
                    self.exit_reason = "stop_requested"
                    break
                if not self.stop_requested:
                    self._reply(conn, self.status_reply() if op == "status"
                                else {"ok": False, "reason": "unknown request"})
                conn.close()
            if self.exit_reason is None:
                self.exit_reason = "signal"
            outcome = self.shutdown()
            if stop_conn is not None:
                self._reply(stop_conn, dict(outcome, ok=True, nonce=self.nonce),
                            poll_stop=False)
                stop_conn.close()
                stop_conn = None
            if not outcome["observed"]:
                outcome = self.shutdown_until_observed()
        finally:
            if stop_conn is not None:
                stop_conn.close()
            self._close_listener()
        with common.lock(self.directory):
            self._finish_record(outcome)
        return outcome

    def _close_listener(self):
        if self.listener is not None:
            self.listener.close()
            self.listener = None
            try:
                os.unlink(common.socket_path(self.directory))
            except FileNotFoundError:
                pass

    # -- the anchored shutdown ---------------------------------------------

    def shutdown(self):
        """One anchored attempt; returns what was OBSERVED."""
        self.stopping = True
        if self.gate_w is not None:
            os.close(self.gate_w)
            self.gate_w = None
        process = self.process
        if process is None:
            return {"observed": True, "outcome": "nothing_was_started",
                    "signalled": None}
        group, sent = process.pid, []
        if process.returncode is None:
            for sig, seconds in ((signal.SIGTERM, self.grace),
                                 (signal.SIGKILL, self.kill_grace)):
                if self._group_quiet(group):
                    break
                try:
                    os.killpg(group, sig)
                except OSError:
                    pass
                sent.append(signal.Signals(sig).name)
                self._wait(lambda: self._group_quiet(group), seconds)
            signalled = {"process_group": group, "signals": sent}
            if not self._group_quiet(group):
                return {"observed": False, "outcome": "members_survive",
                        "signalled": signalled}
            process.wait()
        signalled = {"process_group": group, "signals": sent}
        gone = self._wait(
            lambda: common.group_state(group) == common.GROUP_GONE,
            self.kill_grace)
        return {"observed": gone,
                "outcome": "stopped" if gone else "group_lingers",
                "signalled": signalled}

    def shutdown_until_observed(self):
        deadline = time.monotonic() + self.max_unconfirmed
        outcome = self.shutdown()
        while not outcome["observed"] and time.monotonic() < deadline:
            time.sleep(RETRY_SECONDS)
            outcome = self.shutdown()
        return outcome

    def _finish_record(self, outcome):
        """The caller holds the state lock. Removed only when the shutdown
        was OBSERVED complete; otherwise kept, marked."""
        record = common.load(self.directory)
        if record is None or record.get("nonce") != self.nonce:
            return
        if outcome["observed"]:
            common.clear(self.directory)
        else:
            common.save(self.directory, dict(record, state="stop_unconfirmed",
                                             stop_outcome=outcome))


def _emit(out, result):
    """Write the one startup-result line. False when it could not be
    delivered (the ``on`` client is gone: a broken pipe)."""
    try:
        out.write(json.dumps(result, sort_keys=True) + "\n")
        out.flush()
    except OSError:
        return False
    return True


def run(controller, out):
    """Start, report one JSON line to ``out``, then serve. Returns the exit
    status: 0 stopped on request (or already running elsewhere, or stopped
    during startup), 1 the tunnel exited by itself, 3 startup refused or
    failed.

    Once ``start`` has succeeded this process OWNS a live, anchored child,
    and every way out of here either runs the anchored shutdown or keeps the
    anchor deliberately:

    - a startup result that cannot be delivered (the ``on`` client is gone)
      KEEPS the anchor: the controller goes on serving, the record stays
      intact, and ``ditunnel status`` / ``ditunnel off`` reach it;
    - a stop signal during the result's emission only sets a flag (the
      controller is already serving), and ``serve`` then shuts down;
    - any other exception (from emission or from ``serve``) runs the
      anchored shutdown, settles the record, and then propagates."""
    controller.install_signals()
    try:
        try:
            result = controller.start()
        except Stop as exc:
            _emit(out, {"ok": False, "stopped": True,
                        "reason": "stopped by a signal during startup; "
                                  + cleanup_sentence(exc.outcome)})
            return 0
        except common.TunnelError as exc:
            _emit(out, {"ok": False, "reason": str(exc)})
            return 3
        if result.get("already_on"):
            _emit(out, result)
            return 0
        try:
            if not _emit(out, result):
                controller.log(
                    "the startup result could not be delivered (the client is"
                    " gone); the tunnel is KEPT, anchored and served, so"
                    " `ditunnel status` and `ditunnel off` still reach it")
            outcome = controller.serve()
        except BaseException:
            outcome = controller.shutdown_until_observed()
            with common.lock(controller.directory):
                controller._finish_record(outcome)
            controller._close_listener()
            raise
        if controller.exit_reason == "tunnel_exited":
            return 1
        return 0 if outcome["observed"] else 1
    finally:
        controller.restore_signals()


def main(argv=None):
    """The detached controller ``ditunnel on`` starts (``python -m
    tunnel_control.controller``). Its startup result is one JSON line on
    stdout, read by ``on``; stdout is then detached so nothing later can
    block on a closed pipe."""
    parser = argparse.ArgumentParser(prog="tunnel_control.controller")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cloudflared", required=True)
    parser.add_argument("--stop-grace", type=float, default=STOP_GRACE_SECONDS)
    args = parser.parse_args(argv)
    directory = common.state_dir(args.state_dir)
    log = os.open(os.path.join(directory, common.CONTROLLER_LOG_NAME),
                  os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 2)
    os.close(log)

    class _Detaching(object):
        """stdout for exactly one line: after the flush (delivered or not)
        fd 1 is pointed at /dev/null, so nothing later can block on, or be
        broken by, the ``on`` client's closed pipe."""

        def write(self, text):
            sys.stdout.write(text)

        def flush(self):
            try:
                sys.stdout.flush()
            finally:
                null = os.open(os.devnull, os.O_WRONLY)
                os.dup2(null, 1)
                os.close(null)

    controller = Controller(directory, args.port, args.cloudflared, "shell",
                            grace=args.stop_grace)
    return run(controller, _Detaching())


if __name__ == "__main__":
    raise SystemExit(main())
