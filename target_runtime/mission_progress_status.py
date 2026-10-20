"""The progression runtime's status file, and the probe the panel reads it with.

This module lives in ``target_runtime`` because the file is the runtime's: it
writes it, and the panel (whose package holds no file, signal or process code
by a structural pin) is handed the probe as a plain callable.

The progression runtime (``dirun`` in its MissionCore mode) advances the
Missions this panel started. It publishes one small file beside the panel's
state, atomically, after every pass. The panel reads it to say honestly whether
progression is running, because "nothing visibly advanced" and "nothing is
running" are different things for a person waiting on a Mission.

The status is EVIDENCE OF LIVENESS, not authority and not a lock: it can be
stale, forged by a same-user process, or left behind by a crash. So the probe
believes it only as far as it can check it: the file must be a plain private
file of a bounded size with exactly the documented fields; its process must
still exist; it must have passed recently; and it must have been composed from
the SAME paths the panel is (a digest of the four configured paths, so a runtime
pointed at a different state directory is reported as such and never as this
panel's). It carries no path, token or secret, only Mission ids and request
references the panel already shows.

Nothing here writes a Mission, a request or a journal, and nothing here is read
back as a claim about what a Mission did: a Mission's own record is that.
"""

import json
import os
import stat

from workflow_authority.digest import json_digest

STATUS_FILE_NAME = "mission_progress_status.json"
STATUS_SCHEMA = 1
# A status whose last pass is older than this is STALE: the runtime may be
# wedged or gone without having said so. A pass is seconds apart.
STATUS_STALE_SECONDS = 30
MAX_STATUS_BYTES = 65536
# The Missions a status lists, and the longest explanation each carries.
MAX_STATUS_MISSIONS = 32
MAX_REPORT_DETAIL_CHARS = 300
MAX_PROBLEM_CHARS = 80

STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_STALE = "stale"
STATE_MISMATCH = "mismatch"
STATE_UNREADABLE = "unreadable"
# Passing, but the last pass failed as a whole (its records could not be read):
# the runtime is up and NOTHING is being advanced. Never reported as running.
STATE_FAILING = "failing"
PROBE_STATES = (STATE_RUNNING, STATE_STOPPED, STATE_STALE, STATE_MISMATCH,
                STATE_UNREADABLE, STATE_FAILING)

_HEX = frozenset("0123456789abcdef")
STATUS_KEYS = (
    "schema", "pid", "started_at", "last_pass_at", "passes", "interval_seconds",
    "composition_digest", "stopping", "examined", "eligible", "truncated",
    "unknown_requests", "problem", "missions")
ENTRY_KEYS = ("mission_id", "request_ref", "state", "action", "waiting_for",
              "detail")


def composition_digest(state_directory, control_repo, workspace_repository,
                       workspaces_root):
    """The identity of one panel composition: the four configured locations,
    resolved. A runtime and a panel configured the same way compute the same
    digest; one pointed anywhere else computes another."""
    def resolved(path):
        return None if path is None else os.path.realpath(path)
    return json_digest({
        "state_directory": resolved(state_directory),
        "control_repo": resolved(control_repo),
        "workspace_repository": resolved(workspace_repository),
        "workspaces_root": resolved(workspaces_root),
    })


def _path(state_directory):
    return os.path.join(state_directory, STATUS_FILE_NAME)


def _clean_entry(entry):
    """One listed Mission, reduced to exactly the documented fields."""
    cleaned = {key: entry.get(key) for key in ENTRY_KEYS}
    detail = cleaned["detail"]
    cleaned["detail"] = None if detail is None else str(detail)[:MAX_REPORT_DETAIL_CHARS]
    return cleaned


def write_status(state_directory, digest, pid, started_at, passes,
                 interval_seconds, report, stopping, now, problem=None):
    """Replace the status file whole and atomically (private, 0600): a reader
    sees the previous status or this one, never half of either. ``problem`` is
    the CLASS NAME of a failure of the whole pass, or None."""
    document = {
        "schema": STATUS_SCHEMA, "pid": pid, "started_at": started_at,
        "last_pass_at": now, "passes": passes,
        "interval_seconds": interval_seconds, "composition_digest": digest,
        "stopping": bool(stopping),
        "examined": report["examined"], "eligible": report["eligible"],
        "truncated": bool(report["truncated"]),
        "unknown_requests": report["unknown_requests"],
        "problem": None if problem is None else str(problem)[:MAX_PROBLEM_CHARS],
        "missions": [_clean_entry(entry)
                     for entry in report["missions"][:MAX_STATUS_MISSIONS]],
    }
    target = _path(state_directory)
    temporary = "%s.%d.tmp" % (target, os.getpid())
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _is_int(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid(document):
    if not isinstance(document, dict) or sorted(document) != sorted(STATUS_KEYS):
        return False
    digest = document["composition_digest"]
    ok = (document["schema"] == STATUS_SCHEMA
          and _is_int(document["pid"], 1)
          and _is_int(document["started_at"]) and _is_int(document["last_pass_at"])
          and _is_int(document["passes"]) and _is_int(document["interval_seconds"], 1)
          and isinstance(digest, str) and len(digest) == 64 and set(digest) <= _HEX
          and isinstance(document["stopping"], bool)
          and isinstance(document["truncated"], bool)
          and _is_int(document["examined"]) and _is_int(document["eligible"])
          and _is_int(document["unknown_requests"])
          and (document["problem"] is None
               or (isinstance(document["problem"], str)
                   and len(document["problem"]) <= MAX_PROBLEM_CHARS))
          and isinstance(document["missions"], list)
          and len(document["missions"]) <= MAX_STATUS_MISSIONS)
    if not ok:
        return False
    for entry in document["missions"]:
        if not isinstance(entry, dict) or sorted(entry) != sorted(ENTRY_KEYS):
            return False
        if not all(entry[key] is None or isinstance(entry[key], str)
                   for key in ENTRY_KEYS):
            return False
    return True


def read_status(state_directory):
    """``(document, None)``, ``(None, None)`` when there is no status, or
    ``(None, reason)`` for one that cannot be believed. Never follows a link."""
    try:
        descriptor = os.open(_path(state_directory), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "the status file cannot be opened (a link is never followed)"
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None, "the status file is not a plain file"
        if info.st_size > MAX_STATUS_BYTES:
            return None, "the status file is larger than %d bytes" % MAX_STATUS_BYTES
        data = os.read(descriptor, MAX_STATUS_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        document = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "the status file is not JSON"
    if not _valid(document):
        return None, "the status file does not have the documented shape"
    return document, None


def _pid_alive(pid):
    """Does a process with this id exist? A query, never a signal."""
    try:
        os.getpgid(pid)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # it exists; it is simply not ours to look at
    except (OverflowError, ValueError, OSError):
        return False
    return True


def probe(state_directory, digest, now, pid_alive=_pid_alive):
    """Is a progression runtime composed for THIS panel passing now? One of
    ``PROBE_STATES``, with a plain explanation and the ages a person can use.
    Believed only as far as it checks out (see the module docstring)."""
    def answer(state, detail, document=None):
        return {
            "state": state, "detail": detail,
            "passes": None if document is None else document["passes"],
            "interval_seconds": None if document is None else document["interval_seconds"],
            "last_pass_age_seconds": None if document is None
            else max(0, now - document["last_pass_at"]),
        }
    document, problem = read_status(state_directory)
    if problem is not None:
        return answer(STATE_UNREADABLE, problem)
    if document is None:
        return answer(STATE_STOPPED, "no progression runtime has reported here")
    if document["stopping"] or not pid_alive(document["pid"]):
        return answer(STATE_STOPPED,
                      "the progression runtime that reported here is no longer"
                      " running", document)
    if document["composition_digest"] != digest:
        return answer(STATE_MISMATCH,
                      "a progression runtime is running but was started with"
                      " different locations than this panel, so it is not"
                      " advancing this panel's Missions", document)
    if now - document["last_pass_at"] > STATUS_STALE_SECONDS:
        return answer(STATE_STALE,
                      "the progression runtime has not completed a pass in over"
                      " %d seconds" % STATUS_STALE_SECONDS, document)
    if document["problem"] is not None:
        return answer(STATE_FAILING,
                      "the progression runtime is passing but its last pass"
                      " failed (%s), so nothing is being advanced"
                      % document["problem"], document)
    return answer(STATE_RUNNING, "the progression runtime is passing", document)
