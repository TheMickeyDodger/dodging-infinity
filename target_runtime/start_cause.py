"""Project the startup diagnostic a failed child start RETAINED onto the attempt it belongs to.

``herdr.lifecycle.start_herd`` leaves a bounded, private diagnostic in the child workspace
(``.herd/state/diagnostics/startup-binding-failure-*.json``) before it cleans up, when startup fails after the
roles were contracted. That file is the durable artifact. This module reads it back, READ-ONLY and server-side, so
the bridge and the panel can report a bounded cause without either schema growing a field and without any renderer
touching the disk.

BINDING, NOT RECENCY. A file is attributed to a run only if it is bound to that run's durable intent by identifiers
the intent itself holds:

- it is read only from the intent's own ``workspace_realpath`` (a diagnostic anywhere else is never opened);
- the record's own recorded repository must resolve to that same realpath, and its last component must be the
  Mission id (the bridge derives every Mission's workspace as ``<root>/<mission_id>``);
- its stage, schema, failure type and every role field must be in the closed vocabulary, with no other key at any level;
- its runtime must have begun no earlier than the intent was recorded (the intent is recorded BEFORE the spawn, so a
  file from before it cannot belong to this attempt). That is a lower-bound REJECT of stale files, never a way to choose
  between files;
- exactly ONE file qualifies. Zero is NONE, two or more is AMBIGUOUS and none is used. A newer file never wins.

The Herdr layer does not know the Mission id or revision, so the file cannot carry them itself: they bind
transitively, through the once-only intent (which records the revision) and the per-Mission workspace path.

Everything returned is bounded, and a file that does not match is simply absent: it is counted (``rejected``) and never
described. The output holds no path, no session value, no prompt or pane text and no exception message, because the file
holds none. The only file access is the one hardened bounded read of a fixed ``.herd/state`` artifact
(``state_artifact.read_state_artifact``) plus a name listing of that one directory.
"""

import json
import os
import re

from target_runtime import prepare as prepare_module
from target_runtime import state_artifact as state_artifact_module

DIAGNOSTIC_SUBDIRS = (".herd", "state", "diagnostics")

# The writer's vocabulary (``herdr.runtime`` / ``herdr.lifecycle``), repeated here so this READ-ONLY module does not
# import the process-spawning one. ``tests/test_mission_start_cause.py`` asserts each value equals its source.
FILE_PREFIX = "startup-binding-failure-"
SCHEMA = "herdr_startup_binding_failure_diagnostic_v1"
FAILURE_TYPES = ("BINDINGS_NOT_ESTABLISHED", "UNKNOWN")
SESSION_STATES = ("ABSENT", "PRESENT", "UNKNOWN")
STAGES = ("establish_role_bindings", "heartbeat_controller")

# Bounds. The writer retains at most 20 files of this kind; examining more names than twice that is not a scan this
# read will do (such a directory is AMBIGUOUS, not slow).
MAX_START_CAUSE_CANDIDATES = 40
MAX_START_CAUSE_ROLES = 16
MAX_START_CAUSE_FIELD_CHARS = 300

STATE_BOUND = "BOUND"
STATE_NONE = "NONE"
STATE_AMBIGUOUS = "AMBIGUOUS"

_NAME = re.compile(r"^" + re.escape(FILE_PREFIX) + r"[A-Za-z0-9_.-]{1,120}\.json$")
_RECORD_KEYS = frozenset((
    "schema", "redaction", "captured_at", "stage", "failure", "workspace",
    "settle_seconds", "roles_total", "roles_truncated", "roles", "limitations"))
_ROLE_KEYS = frozenset(("logical", "agent", "pane", "reason", "session"))


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value):
    return isinstance(value, str)


def _cap(text):
    return text[:MAX_START_CAUSE_FIELD_CHARS]


def _bound(record, root, mission_id, recorded_at):
    """The bounded projection of ``record`` if it binds to this attempt, else None."""
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        return None
    if record["schema"] != SCHEMA or record["stage"] not in STAGES:
        return None
    failure, workspace = record["failure"], record["workspace"]
    if not isinstance(failure, dict) or set(failure) != {"type", "class"}:
        return None
    if failure["type"] not in FAILURE_TYPES or not (
        failure["class"] is None or _text(failure["class"])
    ):
        return None
    if not isinstance(workspace, dict) or set(workspace) != {
        "workspace_id", "repo", "runtime_created_at"
    }:
        return None
    repo, created = workspace["repo"], workspace["runtime_created_at"]
    if not _text(repo) or os.path.realpath(repo) != root:
        return None
    if os.path.basename(root) != mission_id:
        return None
    if not _is_count(created) or created < recorded_at:
        return None
    roles = record["roles"]
    if not isinstance(roles, list) or not _is_count(record["roles_total"]) or not isinstance(
        record["roles_truncated"], bool
    ):
        return None
    shown = []
    for role in roles:
        if not isinstance(role, dict) or set(role) != _ROLE_KEYS:
            return None
        if not _text(role["logical"]) or not _text(role["reason"]) or (
            role["session"] not in SESSION_STATES
        ):
            return None
        shown.append({"logical": _cap(role["logical"]), "session": role["session"],
                      "reason": _cap(role["reason"])})
    return {
        "kind": failure["type"],
        "stage": record["stage"],
        "failure_class": None if failure["class"] is None else _cap(failure["class"]),
        "roles": shown[:MAX_START_CAUSE_ROLES],
        "roles_total": record["roles_total"],
        "roles_truncated": bool(record["roles_truncated"]) or len(shown) > MAX_START_CAUSE_ROLES
        or record["roles_total"] > len(shown[:MAX_START_CAUSE_ROLES]),
    }


def project(workspace_realpath, mission_id, intent_recorded_at):
    """The retained startup diagnostic bound to the run intent whose workspace is ``workspace_realpath``, as one of

    - ``{"state": "BOUND", "kind", "stage", "failure_class", "roles": [{"logical", "session", "reason"}],
      "roles_total", "roles_truncated", "reference"}`` (``reference`` is the file's NAME, never a path);
    - ``{"state": "NONE", "rejected": n}`` (nothing binds; ``n`` files were examined and rejected);
    - ``{"state": "AMBIGUOUS", "candidates": n}`` (more than one claims the attempt, or too many files to examine:
      none is used).

    Read-only; never raises for what it finds."""
    if not _text(workspace_realpath) or not _text(mission_id) or not _is_count(intent_recorded_at):
        return {"state": STATE_NONE, "rejected": 0}
    root = os.path.realpath(workspace_realpath)
    directory = os.path.join(root, *DIAGNOSTIC_SUBDIRS)
    if os.path.realpath(directory) != directory:
        return {"state": STATE_NONE, "rejected": 0}
    try:
        names = sorted(name for name in os.listdir(directory) if _NAME.match(name))
    except OSError:
        return {"state": STATE_NONE, "rejected": 0}
    if len(names) > MAX_START_CAUSE_CANDIDATES:
        return {"state": STATE_AMBIGUOUS, "candidates": len(names)}
    bound, rejected = [], 0
    for name in names:
        status, _, _, text = state_artifact_module.read_state_artifact(
            root, DIAGNOSTIC_SUBDIRS, name)
        if status != prepare_module.INSTRUCTION_READ or text is None:
            rejected += 1
            continue
        try:
            projected = _bound(json.loads(text), root, mission_id, intent_recorded_at)
        except Exception:
            projected = None
        if projected is None:
            rejected += 1
            continue
        projected["state"] = STATE_BOUND
        projected["reference"] = name
        bound.append(projected)
    if len(bound) == 1:
        return bound[0]
    if bound:
        return {"state": STATE_AMBIGUOUS, "candidates": len(bound)}
    return {"state": STATE_NONE, "rejected": rejected}
