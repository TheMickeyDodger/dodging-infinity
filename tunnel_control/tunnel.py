"""On-demand Cloudflare Quick Tunnel control: the client
side of ``ditunnel.py``. Its commands are ``on``, ``off``, ``status`` and
``forget``, plus ``foreground`` for the optional launchd job.

What it runs
------------

``cloudflared tunnel --no-autoupdate --url http://127.0.0.1:PORT
--http-host-header 127.0.0.1:PORT``: a Quick Tunnel.

- Per Cloudflare's documentation it is free and needs no account. That is
  documentation, not evidence that this works end to end.
- It never logs in, creates a named tunnel or installs anything. Without
  ``cloudflared`` it refuses.
- ``--allowed-mail`` (email-PIN access) was considered and REJECTED: it needs
  an interactive browser flow, which a non-interactive MCP client cannot
  complete.
- ``--http-host-header`` presents the ``Host`` the MCP endpoint's
  DNS-rebinding guard requires. That cloudflared honours it for a Quick
  Tunnel is unverified here [U].

Who owns the tunnel
-------------------

A persistent CONTROLLER owns it (``tunnel_control.controller``).

- ``on`` starts the controller, detached in its own session, and relays its
  one-line startup result.
- The controller keeps ``cloudflared`` as its own UNREAPED child, in
  cloudflared's own session and process group. Until it reaps that child,
  the pid and the process-group id cannot be given to any other process. So
  its ``killpg`` reaches the tunnel's group and nothing else, with no
  pid-reuse question and no reliance on a timestamp.
- ``off`` and ``status`` ask the controller over ``ctl.sock`` in the
  owner-only state directory. ``off`` then works from any later shell, after
  the shell that ran ``on`` is gone.
- The controller stops the tunnel ANCHORED:
  1. SIGTERM to the group;
  2. a bounded wait until the leader has exited and no member can be
     signalled;
  3. otherwise SIGKILL to the group;
  4. then reap the leader, and report "stopped" only when the group is
     OBSERVED gone.

  A leader that exits while a member ignores SIGTERM is still an unreaped
  zombie, so the member is reached by the SIGKILL to that same, still
  anchored, group.
- The record is removed only after an observed stop.

When the controller is gone: report, never signal
-------------------------------------------------

The controller is gone if it was SIGKILLed or crashed hard, if launchd
terminated the job before its stop completed, or if it exhausted its retry
budget with a member still alive. The last is an ordinary path, not a crash
(``tunnel_control.controller``). Its anchor is then lost, and with it the
only basis this tool has for signalling safely.

The record still names the tunnel's pid, process group and ``ps -o
lstart=`` start time (``target_runtime.spawn_stamp.leader_start_time``).
That supports an INFERENCE that the tunnel is alive: the recorded pid holds a
process whose live start time is EQUAL to the recorded one, and which leads
its own group (``target_runtime.process_ownership.group_is_verified``).

That equality is "identity unchanged AS OBSERVED". ``lstart`` has
one-second granularity, so equality is not proof that the pid was never
reused. Nothing makes a check and a later signal one operation: acting on it
is NOT atomic identity-safe signalling. Even a "reversible" SIGSTOP
would freeze whatever process holds the pid, and SIGCONT is no undo: it can
resume a process that something else stopped on purpose.

So this tool sends NO signal of any kind on an inferred identity:

- ``off`` refuses, KEEPS the record, and names the recorded pid, process
  group and start time.
- ``status`` reports such a tunnel as ``unverified``. Its URL is given only as
  ``unverified_url``, never as active.

Hard-kill recovery of a tunnel whose controller is gone is NOT SUPPORTED by
this tool. The operator does it manually, under their own authority
(``docs/tunnel.md``, "Manual recovery"), then runs ``ditunnel forget``, or
``ditunnel off`` once the record names nothing running.

``target_runtime.process_ownership.reap_group`` is NOT used anywhere here.
When group verification fails, its fallback SIGKILLs the single pid, which
after a non-atomic identity check could reach a process the pid was reused
for. It was reachable from the earlier draft's ``stop_owned`` (after its
start-time check).

What the ownership rule establishes, and what it does not (Amendment 16)
-----------------------------------------------------------------------

All signalling goes through the anchor. ``off`` signals only through a live
controller, and the controller signals only the process group of its own
unreaped child. Without a controller nothing is signalled. So THIS TOOL never
signals a process it did not start: never an unrelated ``cloudflared``, and
never a pid now held by an unrelated process. Nothing enumerates processes or
matches a name.

The rule is a workflow guardrail, not designed to contain processes running
with the user's own privileges: it does not stop such a process, a Herdr
worker included, from controlling the tunnel. The record and the socket are
the TOOL's bookkeeping, not access control.

No MCP tool exposes this tool, and no worker role is granted it (both pinned
by test). That is necessary, but it is not sufficient. It also cannot tell
Grok Bot's approved local shell from any other process of the same user.

The URL
-------

A Quick Tunnel's URL is temporary, changes on every start, and stops working
when its ``cloudflared`` stops.

- It is read only from the owning run's own log.
- It is reported as ACTIVE only while the controller observes that run's
  process alive.
- If the process is only inferred alive, the URL is reported as
  ``unverified_url``.
- If the process is gone, the URL is reported as ``stale_url``.
"""

import json
import os
import select
import shutil
import subprocess
import sys
import time

from target_runtime import spawn_stamp
from tunnel_control import common
from tunnel_control import controller as controller_module
from tunnel_control import support

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_ASK_SECONDS = 5.0
STOP_REPLY_SECONDS = 60.0
ON_REPLY_SECONDS = 90.0
TunnelError = common.TunnelError


def _refuse_unsupported():
    """macOS only: elsewhere every command refuses before touching any
    state or starting anything."""
    refusal = support.unsupported()
    if refusal is not None:
        raise TunnelError(refusal)


def resolve_binary(binary=None):
    """An absolute, executable ``cloudflared``: the one named, or the one on
    PATH. Never installed: absent is a refusal."""
    if binary is None:
        binary = shutil.which("cloudflared")
        if binary is None:
            raise TunnelError(
                "cloudflared is not installed or not on PATH; install it (it"
                " is free) and run again. This tool never installs anything.")
    if not os.path.isabs(binary) or not os.path.isfile(binary) or not (
        os.access(binary, os.X_OK)
    ):
        raise TunnelError("--cloudflared must be the absolute path of an"
                          " executable file")
    return binary


def check_port(port):
    if not isinstance(port, int) or isinstance(port, bool) or not (
        1 <= port <= 65535
    ):
        raise TunnelError("--port must be the MCP endpoint's loopback port,"
                          " 1 to 65535")
    return port


def _unlink_socket(directory):
    try:
        os.unlink(common.socket_path(directory))
    except FileNotFoundError:
        pass


# -- on -----------------------------------------------------------------------


def _read_line(process, timeout):
    deadline = time.monotonic() + timeout
    data = b""
    stream = process.stdout
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([stream], [], [], min(remaining, 0.2))
        if not ready:
            continue
        chunk = os.read(stream.fileno(), 4096)
        if not chunk:
            break
        data += chunk
        if len(data) > common.MAX_MESSAGE_BYTES:
            break
    return data


def on(directory, port, binary=None, stop_grace=None, reply_timeout=None):
    """Start the tunnel under a new controller, or report the running one
    if, and only if, its effective configuration is exactly the requested
    one. Returns only on a live, owned tunnel with its CURRENT URL."""
    _refuse_unsupported()
    directory = common.state_dir(directory)
    port = check_port(port)
    binary = resolve_binary(binary)
    common.socket_path(directory)
    reply = common.ask(directory, "status", STATUS_ASK_SECONDS)
    if reply is not None:
        return common.compare_running(reply, common.tunnel_config(binary, port))
    argv = [sys.executable, "-m", "tunnel_control.controller",
            "--state-dir", directory, "--port", str(port),
            "--cloudflared", binary]
    if stop_grace is not None:
        argv += ["--stop-grace", str(stop_grace)]
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=PACKAGE_ROOT, close_fds=True,
        start_new_session=True)
    try:
        line = _read_line(process, ON_REPLY_SECONDS if reply_timeout is None
                          else reply_timeout)
    finally:
        process.stdout.close()
    if not line.endswith(b"\n"):
        if process.poll() is not None:
            raise TunnelError(
                "the controller exited (status %s) without reporting; see %s"
                % (process.returncode,
                   os.path.join(directory, common.CONTROLLER_LOG_NAME)))
        raise TunnelError(
            "the controller did not report within the wait; it keeps any"
            " tunnel it starts anchored and served. Run `ditunnel status`")
    result = json.loads(line.decode("utf-8"))
    if not result.get("ok"):
        raise TunnelError(result.get("reason") or "the controller refused")
    return result


# -- off ----------------------------------------------------------------------


def off(directory, start_time=None):
    """Stop this tool's tunnel, ONLY through its controller (the anchored
    stop). It never reports "stopped" unless the group was observed gone.
    Without a controller it signals NOTHING: a record that names nothing
    running is cleared, and any other record is kept and reported (hard-kill
    recovery without the controller is not supported; see ``docs/tunnel.md``,
    "Manual recovery")."""
    _refuse_unsupported()
    directory = common.state_dir(directory)
    start_time = start_time or spawn_stamp.leader_start_time
    for _ in range(3):
        reply = common.ask(directory, "stop", STOP_REPLY_SECONDS)
        if reply is not None:
            return _stopped_by_controller(reply)
        with common.lock(directory):
            if common.ask(directory, "status", STATUS_ASK_SECONDS) is not None:
                continue
            return _off_without_controller(directory, start_time)
    raise TunnelError("a controller kept appearing while `off` ran; run it again")


def _stopped_by_controller(reply):
    if reply.get("observed"):
        return {"ok": True, "state": "off", "already_off": False,
                "stopped_by": "controller", "observed": True,
                "signalled": reply.get("signalled")}
    raise TunnelError(
        "the controller signalled process group %s (%s) and a member is STILL"
        " alive; it keeps the group anchored, retries, and keeps the record"
        % ((reply.get("signalled") or {}).get("process_group"),
           ", ".join((reply.get("signalled") or {}).get("signals") or [])))


def _off_without_controller(directory, start_time):
    """The caller holds the state lock and no controller answers."""
    record = common.load(directory)
    if record is None:
        _unlink_socket(directory)
        return {"ok": True, "state": "off", "already_off": True,
                "signalled": None}
    verdict, pid, reason = common.classify(record, start_time)
    if verdict == common.OVER:
        common.clear(directory)
        _unlink_socket(directory)
        return {"ok": True, "state": "off", "already_off": True,
                "signalled": None, "stale_record_cleared": reason}
    if verdict == common.UNRESOLVED:
        raise TunnelError(
            "%s. Nothing was signalled and the record is kept: inspect it"
            " (`ditunnel status`), deal with any process yourself, then"
            " `ditunnel forget`" % reason)
    tunnel = record.get("tunnel") or {}
    raise TunnelError(
        "the controller is gone, so this tool has no ownership anchor and"
        " sends NO signal: %s, but that is inferred from an equal one-second"
        " start time, not established. Hard-kill recovery of a tunnel whose"
        " controller is gone is NOT SUPPORTED by this tool. The record is kept."
        " Manual recovery, under your own authority: process group %s (pid %s,"
        " recorded start %r); see docs/tunnel.md, then `ditunnel forget`"
        % (reason, tunnel.get("pgid"), tunnel.get("pid"), tunnel.get("start")))


# -- status, forget, foreground --------------------------------------------------


def status(directory, start_time=None):
    """What is actually running, observed now. A URL is reported as active
    only from a live tunnel's own run; any other is ``stale_url``."""
    _refuse_unsupported()
    directory = common.state_dir(directory)
    start_time = start_time or spawn_stamp.leader_start_time
    for _ in range(3):
        try:
            reply = common.ask(directory, "status", STATUS_ASK_SECONDS)
        except common.ControllerUnresponsive as exc:
            record = common.load(directory) or {}
            return {"ok": True, "state": "unknown", "url": None,
                    "stale_url": common.last_url(record.get("log")),
                    "reason": str(exc)}
        if reply is not None:
            return {"ok": True, "state": reply.get("state"),
                    "url": reply.get("url") if reply.get("state") == "on" else None,
                    "pid": reply.get("pid"), "config": reply.get("config"),
                    "controller_pid": reply.get("controller_pid"),
                    "mode": reply.get("mode"),
                    "ownership": reply.get("ownership")}
        with common.lock(directory):
            if common.ask(directory, "status", STATUS_ASK_SECONDS) is not None:
                continue
            record = common.load(directory)
            if record is None:
                return {"ok": True, "state": "off", "url": None}
            verdict, pid, reason = common.classify(record, start_time)
            url = common.last_url(record.get("log"))
            if verdict == common.OWNED:
                return {"ok": True, "state": "unverified", "url": None,
                        "unverified_url": url, "pid": pid,
                        "config": record.get("config"), "controller_pid": None,
                        "ownership": "unverified: the controller is gone; "
                                     + reason + " (an equal one-second start"
                                     " time, not established ownership)"}
            if verdict == common.OVER:
                return {"ok": True, "state": "off", "url": None,
                        "stale_url": url, "stale_reason": reason}
            return {"ok": True, "state": "unknown", "url": None,
                    "stale_url": url, "reason": reason}
    raise TunnelError("a controller kept appearing while `status` ran")


def forget(directory, start_time=None):
    """A human's decision, after inspection, that a record this tool cannot
    resolve is done with: it is MOVED aside intact (``retained/``), never
    deleted, and nothing is signalled. Refused while a controller serves
    the tunnel or while the record still names a live tunnel this tool
    owns (use ``off``)."""
    _refuse_unsupported()
    directory = common.state_dir(directory)
    start_time = start_time or spawn_stamp.leader_start_time
    if common.ask(directory, "status", STATUS_ASK_SECONDS) is not None:
        raise TunnelError("a controller is serving this tunnel; use `ditunnel off`")
    with common.lock(directory):
        if common.ask(directory, "status", STATUS_ASK_SECONDS) is not None:
            raise TunnelError("a controller is serving this tunnel; use"
                              " `ditunnel off`")
        record = common.load(directory)
        if record is None:
            return {"ok": True, "forgot": None}
        verdict, _, reason = common.classify(record, start_time)
        if verdict == common.OWNED:
            raise TunnelError(
                "the record still names a process that appears to be the"
                " tunnel (%s); the controller is gone, so stop it yourself"
                " first (docs/tunnel.md, \"Manual recovery\"), then forget it"
                % reason)
        target = common.retain(directory, record)
        _unlink_socket(directory)
        return {"ok": True, "forgot": target, "reason": reason,
                "signalled": None}


def foreground(directory, port, binary=None, out=None, stop_grace=None,
               **seams):
    """The launchd job's long-running process: this process IS the
    controller (mode ``launchd``), so the job's main process holds the
    anchor. ``seams`` are the controller's test seams (``start_time``,
    ``checkpoint``, ``settle``, ``startup_timeout``, ``kill_grace``,
    ``max_unconfirmed``)."""
    _refuse_unsupported()
    directory = common.state_dir(directory)
    port = check_port(port)
    binary = resolve_binary(binary)
    common.socket_path(directory)
    controller = controller_module.Controller(
        directory, port, binary, "launchd", grace=stop_grace, **seams)
    return controller_module.run(controller, out if out is not None else sys.stdout)
