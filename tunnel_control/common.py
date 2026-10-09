"""Shared pieces of the tunnel tool: the owner-only state directory, the
ownership record, the control socket's location, URL extraction, and the
process probes. ``tunnel_control.tunnel`` documents the design."""

import errno
import json
import os
import re
import socket
import stat
import time

from target_runtime import process_ownership
from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

STATE_FILE_NAME = "tunnel.json"
LOCK_FILE_NAME = "tunnel.lock"
SOCKET_NAME = "ctl.sock"
CONTROLLER_LOG_NAME = "controller.log"
RUNS_DIR_NAME = "runs"
RETAINED_DIR_NAME = "retained"
LOG_FILE_NAME = "cloudflared.log"
SCHEMA_VERSION = 1
# AF_UNIX paths are bounded by the platform (104 bytes on macOS).
MAX_SOCKET_PATH_BYTES = 100
MAX_LOG_SCAN_BYTES = 1048576
MAX_MESSAGE_BYTES = 65536
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
NONCE_RE = re.compile(r"[0-9a-f]{32}")

# What a probe of a pid, or of a process group, observed.
ABSENT = "absent"
SAME = "same"
CHANGED = "changed"
UNKNOWN = "unknown"
GROUP_MEMBERS = "members"
GROUP_NO_SIGNALLABLE_MEMBER = "no_signallable_member"
GROUP_GONE = "gone"


# What a record says about a tunnel whose controller does not answer.
OWNED = "owned"
OVER = "over"
UNRESOLVED = "unresolved"


class TunnelError(Exception):
    """A refusal: nothing was started or signalled beyond what it says."""


class ControllerUnresponsive(TunnelError):
    """A controller accepted the connection but gave no answer."""


def state_dir(directory):
    if not isinstance(directory, str) or not os.path.isabs(directory):
        raise TunnelError("--state-dir must be an absolute path")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    mode = stat.S_IMODE(os.stat(directory).st_mode)
    if mode & 0o077:
        raise TunnelError(
            "the state directory %s is accessible by group/other (mode %o);"
            " nothing is read or written" % (directory, mode))
    return directory


def lock(directory):
    return exclusive_store_lock(directory, LOCK_FILE_NAME)


def socket_path(directory):
    path = os.path.join(directory, SOCKET_NAME)
    if len(os.fsencode(path)) > MAX_SOCKET_PATH_BYTES:
        raise TunnelError(
            "the state directory path is too long for its control socket"
            " (%d bytes at most); choose a shorter --state-dir"
            % (MAX_SOCKET_PATH_BYTES - len(SOCKET_NAME) - 1))
    return path


def load(directory):
    path = os.path.join(directory, STATE_FILE_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return {"state": "unreadable"}
    return record if isinstance(record, dict) else {"state": "unreadable"}


def save(directory, record):
    atomic_write_json(directory, os.path.join(directory, STATE_FILE_NAME),
                      record, temp_prefix=".ditunnel-")


def clear(directory):
    try:
        os.unlink(os.path.join(directory, STATE_FILE_NAME))
    except FileNotFoundError:
        pass


def retain(directory, record):
    """Move the record aside INTACT (never deleted) and return where."""
    retained = os.path.join(directory, RETAINED_DIR_NAME)
    os.makedirs(retained, mode=0o700, exist_ok=True)
    name = "%d-%s.json" % (int(time.time()), str(record.get("nonce", "x"))[:32])
    target = os.path.join(retained, name)
    os.replace(os.path.join(directory, STATE_FILE_NAME), target)
    return target


def run_dir(directory, nonce):
    return os.path.join(directory, RUNS_DIR_NAME, nonce)


def last_url(log_path):
    """The LAST trycloudflare URL in a run's own log, or None."""
    if not log_path:
        return None
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - MAX_LOG_SCAN_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    found = URL_RE.findall(text)
    return found[-1] if found else None


def pid_exists(pid):
    """Whether a process holds ``pid`` now. Only ESRCH means absent; any
    other answer counts as present (fail closed)."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def group_state(pgid):
    """``killpg(pgid, 0)``: GROUP_MEMBERS when a member can be signalled,
    GROUP_GONE on ESRCH, GROUP_NO_SIGNALLABLE_MEMBER on EPERM. On macOS a
    group whose only remaining member is an UNREAPED zombie answers EPERM
    (observed with synthetic processes), as does a group of another user's
    processes."""
    try:
        os.killpg(pgid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return GROUP_GONE
        if exc.errno == errno.EPERM:
            return GROUP_NO_SIGNALLABLE_MEMBER
        return GROUP_MEMBERS
    return GROUP_MEMBERS


def identity(pid, recorded, start_time):
    """What holds ``pid`` now, judged against the recorded start time:
    ABSENT (no process), SAME (an EQUAL ``ps -o lstart=`` value), CHANGED (a
    different value, so a different process), or UNKNOWN (the query failed
    while a process exists). EQUAL is "unchanged as observed" at a
    one-second granularity: not proof that the pid was never reused, and
    not atomic with any signal that follows."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return UNKNOWN
    if not pid_exists(pid):
        return ABSENT
    try:
        live = start_time(pid)
    except Exception:  # noqa: BLE001
        live = None
    if live is None:
        return ABSENT if not pid_exists(pid) else UNKNOWN
    return SAME if live == recorded else CHANGED


def tunnel_config(binary, port):
    """The effective configuration a tunnel is started with; ``on`` compares
    it, not just liveness, before reporting an existing tunnel."""
    return {"cloudflared": os.path.realpath(binary), "port": port,
            "origin": "http://127.0.0.1:%d" % port,
            "http_host_header": "127.0.0.1:%d" % port}


def quick_tunnel_argv(binary, port):
    return [binary, "tunnel", "--no-autoupdate",
            "--url", "http://127.0.0.1:%d" % port,
            "--http-host-header", "127.0.0.1:%d" % port]


def ask(directory, op, timeout):
    """One request to the controller over its socket: the reply, or None
    when NO controller is listening (no socket file, or nothing accepting
    on it). Raises ``ControllerUnresponsive`` when one accepted but did not
    answer in time."""
    path = socket_path(directory)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    data = b""
    try:
        try:
            client.connect(path)
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        client.sendall((json.dumps({"op": op}) + "\n").encode("utf-8"))
        while not data.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_MESSAGE_BYTES:
                raise TunnelError("the controller's reply is oversized")
    except socket.timeout:
        raise ControllerUnresponsive(
            "the controller accepted the %r request but did not answer within"
            " %.0fs" % (op, timeout))
    except OSError as exc:
        raise ControllerUnresponsive(
            "the controller connection failed during %r (%s)" % (op, exc))
    finally:
        client.close()
    try:
        reply = json.loads(data.decode("utf-8"))
    except ValueError:
        raise ControllerUnresponsive(
            "the controller closed the connection without a complete answer")
    if not isinstance(reply, dict):
        raise ControllerUnresponsive("the controller's answer is not an object")
    return reply


def compare_running(reply, requested):
    """``on`` against a tunnel a controller is serving: report it ONLY if
    its effective configuration is exactly the requested one. A different
    port or cloudflared is refused, naming every difference, so a URL is
    never presented as forwarding somewhere it does not."""
    running = reply.get("config") or {}
    if reply.get("state") != "on":
        raise TunnelError(
            "a controller is serving a tunnel that is not on (state %r); run"
            " `ditunnel status`, then `ditunnel on` again"
            % reply.get("state"))
    if running != requested:
        differing = sorted(key for key in set(running) | set(requested)
                           if running.get(key) != requested.get(key))
        raise TunnelError(
            "a tunnel is already running with a DIFFERENT configuration (%s):"
            " running %s, requested %s. It is not reported as the tunnel you"
            " asked for; run `ditunnel off` first"
            % (", ".join(differing), json.dumps(running, sort_keys=True),
               json.dumps(requested, sort_keys=True)))
    return {"ok": True, "state": "on", "already_on": True,
            "url": reply.get("url"), "pid": reply.get("pid"),
            "config": running, "controller_pid": reply.get("controller_pid"),
            "mode": reply.get("mode"), "ownership": reply.get("ownership")}


def classify(record, start_time):
    """What a record names when NO controller answers: ``(verdict, pid,
    reason)``. OWNED: the recorded pid holds a process whose start time is
    EQUAL to the recorded one and which still leads its own group (inferred
    ownership, "unchanged as observed", see ``identity``). OVER: nothing
    the record names can still be running (the recorded leader is absent,
    or its pid now holds a different process, AND its group is gone; or an
    interrupted start that the gate kept from ever running cloudflared).
    UNRESOLVED: anything else; nothing may be signalled for it."""
    if record.get("state") == "unreadable":
        return UNRESOLVED, None, "the record is unreadable"
    tunnel = record.get("tunnel") or {}
    pid, recorded = tunnel.get("pid"), tunnel.get("start")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return OVER, None, ("an interrupted start: no tunnel process was"
                            " recorded, so the gate never ran cloudflared")
    if not recorded:
        if pid_exists(pid):
            return UNRESOLVED, pid, ("an interrupted start: pid %d was recorded"
                                     " without its start time and is still"
                                     " present" % pid)
        return OVER, pid, ("an interrupted start: the gate never ran"
                           " cloudflared and pid %d is gone" % pid)
    seen = identity(pid, recorded, start_time)
    group = group_state(pid)
    if seen == SAME:
        if process_ownership.group_is_verified(pid):
            return OWNED, pid, ("pid %d has the recorded start time and leads"
                                " its own process group" % pid)
        return UNRESOLVED, pid, ("pid %d has the recorded start time but no"
                                 " longer leads its own process group" % pid)
    if seen in (ABSENT, CHANGED) and group == GROUP_GONE:
        return OVER, pid, (
            "the recorded process is gone" if seen == ABSENT else
            "pid %d now belongs to another process (its start time differs)"
            " and the recorded process group is gone" % pid)
    if seen == UNKNOWN:
        return UNRESOLVED, pid, ("pid %d is present but its start time could"
                                 " not be read" % pid)
    return UNRESOLVED, pid, (
        "the recorded leader is %s but process group %d still has members;"
        " without the controller this tool cannot establish that they are"
        " its own" % ("gone" if seen == ABSENT else "replaced", pid))
