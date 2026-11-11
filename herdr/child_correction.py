"""Clear one stale child-registration reservation, on audited evidence.

A parent herd writes a reservation into ``children.anchor.json`` before it
launches a child (``admit_spawn_registration``). The reservation is an
identity with no task id. It is replaced by the child's real identity only
after a child record is written. If startup fails before that, the
reservation stays, and every later spawn is refused with
``registry_loss_detected``.

Nothing in the launch path may withdraw the reservation: an exception is
not evidence that no task was dispatched. This module is the supervised
correction those messages name (``herdctl children correct``). It removes
exactly one reservation, and only when all of the following are true:

- the anchor holds ``(child repo, no task id)`` for the named parent task
  and does not hold a concrete identity for that child;
- the parent's child registry has no record for that child;
- the evidence directory is the child's own ``.herd/state``, not a copy
  and not a symlink, and its only contents are one
  ``startup-binding-failure-*.json`` diagnostic;
- that diagnostic is a closed-schema record of
  ``BINDINGS_NOT_ESTABLISHED`` at stage ``establish_role_bindings`` for
  this child, and it is not truncated;
- ``herdr agent list`` succeeds, and no listed agent is this child's
  session (its cwd, the diagnostic's workspace id, or an agent name the
  diagnostic records).

A herd that came up stays blocking even when no task file was written.
``start_herd`` writes ``runtime.json`` as it starts and writes
``role-bindings.json`` only after every role binds, which is before the
function returns. Task dispatch runs only after that return. Either file
means the herd process came up (or is still coming up). A diagnostic whose
stage is ``heartbeat_controller`` says the same thing: role bindings had
already been established. "Nothing is running now" is not evidence, and an
agent-start diagnostic or an ``UNKNOWN`` failure does not identify a
pre-dispatch binding failure. Those are refusals. The anchor is left as it
was.

The audit receipt is one JSON line appended to
``.herd/state/child-registration-corrections.jsonl``. It records who, when,
the hash of every evidence file, and the anchor's before and after bytes.
Existing receipt lines are never rewritten. This module does not open
approval grants, the approval ledger, or digest files.

A repeat after a recorded clear, with the same evidence hashes and the
anchor still in the recorded after state, writes nothing. A receipt that
was appended and then not followed by the anchor write (a crash between
the two) is finished by the anchor write alone on a later run, once the
evidence still qualifies; a second line is not appended.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from . import dependencies, follow_on, lifecycle, runtime
from .instance import HerdrInstance

RECEIPT_FILE = "child-registration-corrections.jsonl"
RECEIPT_SCHEMA = "herdr_child_registration_correction_v1"
EVIDENCE_TREE = "sha256-path-digest-size-v1"
COMMAND = "herdctl children correct"

# The writer's cap is on the JSON text. A non-ASCII path can make the file
# longer in bytes than in characters; this is only the read ceiling.
_READ_CEILING = runtime.BINDING_DIAGNOSTIC_FILE_MAX_CHARS * 4
_MAX_DIRECTORY_ENTRIES = 32
_OPERATOR_MAX = 256

_RECORD_KEYS = frozenset((
    "schema", "redaction", "captured_at", "stage", "failure", "workspace",
    "settle_seconds", "roles_total", "roles_truncated", "roles", "limitations",
))
_ROLE_KEYS = frozenset(("logical", "agent", "pane", "reason", "session"))
_FAILURE_KEYS = frozenset(("type", "class"))
_WORKSPACE_KEYS = frozenset(("workspace_id", "repo", "runtime_created_at"))
_DIAGNOSTIC_NAME = re.compile(
    r"^" + re.escape(runtime.BINDING_DIAGNOSTIC_FILE_PREFIX)
    + r"[A-Za-z0-9_.-]{1,120}\.json$"
)

_CAME_UP_NAMES = frozenset(("runtime.json", "role-bindings.json"))
_TASK_NAMES = frozenset(("task.json", "tasks"))


class CorrectionRefused(RuntimeError):
    """The reservation was not cleared. ``check`` names the condition."""

    def __init__(self, check: str, detail: str) -> None:
        self.check = check
        self.detail = detail
        super().__init__(f"{check}: {detail}")


def production_operator() -> str:
    """The operating user, or a refusal when that name cannot be stated."""

    try:
        name = getpass.getuser()
    except Exception as exc:
        raise CorrectionRefused(
            "operator_unknown",
            "the operating user could not be determined "
            f"({exc.__class__.__name__})",
        )
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _OPERATOR_MAX
        or any(char in name for char in "\r\n")
    ):
        raise CorrectionRefused(
            "operator_unknown",
            "the operating user name is empty, too long, or not a single line",
        )
    return name


def production_session_probe() -> dict:
    """One ``herdr agent list``, or ``ok: False`` when it cannot be trusted.

    A failed, unparsable, or unexpected payload is not an empty listing.
    An empty listing is only ``{"result": {"agents": []}}`` from an exit of 0.
    """

    result = runtime.run(["herdr", "agent", "list"])
    if result.returncode:
        return {
            "ok": False,
            "reason": f"`herdr agent list` exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return {
            "ok": False,
            "reason": "`herdr agent list` did not return JSON",
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "`herdr agent list` JSON was not an object",
        }
    body = payload.get("result")
    agents = body.get("agents") if isinstance(body, dict) else None
    if not isinstance(agents, list) or not all(
        isinstance(agent, dict) for agent in agents
    ):
        return {
            "ok": False,
            "reason": "`herdr agent list` has no result.agents list of records",
        }
    return {"ok": True, "agents": agents}


def correct_child_reservation(
    parent_repo,
    parent_task_id: str,
    child_repo,
    evidence_dir,
    *,
    now=None,
    operator: str | None = None,
    session_probe=None,
) -> dict:
    """Clear one stale reservation, or report that the same clear already stands.

    ``evidence_dir`` must be the child repository's own ``.herd/state``
    directory. ``now`` is an aware ``datetime`` or a callable returning one.
    ``session_probe`` defaults to ``production_session_probe``.
    """

    if not isinstance(parent_task_id, str) or not parent_task_id:
        raise CorrectionRefused(
            "parent_task_invalid",
            "the parent task id must be a non-empty string",
        )
    parent = HerdrInstance(parent_repo)
    child = _resolved_dir(child_repo, "child_repo_invalid", "child repository")
    who = operator if operator is not None else production_operator()
    if (
        not isinstance(who, str)
        or not who
        or len(who) > _OPERATOR_MAX
        or any(char in who for char in "\r\n")
    ):
        raise CorrectionRefused(
            "operator_unknown",
            "the operator name is empty, too long, or not a single line",
        )
    moment = _moment(now)
    probe = session_probe or production_session_probe
    state = _evidence_directory(child, evidence_dir)

    with follow_on.registry_lock(parent):
        return _correct_locked(
            parent, parent_task_id, child, state, who, moment, probe,
        )


def _moment(now) -> datetime:
    moment = now() if callable(now) else (
        now if now is not None else datetime.now(timezone.utc)
    )
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise CorrectionRefused(
            "clock_invalid",
            "the correction clock did not return an aware time",
        )
    return moment.astimezone(timezone.utc)


def _resolved_dir(value, check: str, label: str) -> Path:
    raw = value if isinstance(value, Path) else Path(os.fspath(value))
    try:
        resolved = raw.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise CorrectionRefused(
            check,
            f"the {label} path could not be resolved ({exc.__class__.__name__})",
        )
    if not resolved.is_dir() or resolved.is_symlink():
        raise CorrectionRefused(
            check,
            f"the {label} is not a real directory: {resolved}",
        )
    return resolved


def _evidence_directory(child: Path, evidence_dir) -> Path:
    """The child's own state directory, refused when the path is anything else."""

    herd = child / ".herd"
    state = herd / "state"
    if herd.is_symlink() or state.is_symlink():
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the child's .herd or state directory is a symlink; "
            "a link is not the child's own state",
        )
    if not state.is_dir():
        raise CorrectionRefused(
            "evidence_missing",
            f"the child has no state directory at {state}",
        )
    raw = (
        evidence_dir if isinstance(evidence_dir, Path)
        else Path(os.fspath(evidence_dir))
    )
    candidate = raw.expanduser()
    try:
        if candidate.is_symlink():
            raise CorrectionRefused(
                "evidence_ambiguous",
                "the evidence path is a symlink. A link is not the child's own state. "
                "Nothing was written.",
            )
        given = candidate.resolve()
    except CorrectionRefused:
        raise
    except (OSError, RuntimeError) as exc:
        raise CorrectionRefused(
            "evidence_missing",
            f"the evidence path could not be resolved ({exc.__class__.__name__})",
        )
    if given != state or given.is_symlink():
        raise CorrectionRefused(
            "evidence_not_child_state",
            "evidence must be the child repository's own .herd/state directory "
            f"({state}), not a copy or another path ({given})",
        )
    return state


def _correct_locked(parent, parent_task_id, child, state, who, moment, probe):
    child_key = str(child)
    anchor_bytes, registered = _read_anchor(parent)
    reservation = (child_key, None)
    concrete = [
        task_id for repo, task_id in registered.get(parent_task_id, [])
        if repo == child_key and task_id is not None
    ]
    if concrete:
        raise CorrectionRefused(
            "identity_present",
            "the anchor already holds a child identity for this repository "
            f"(task {concrete[0]}). An identity is not removed to unblock a parent. "
            "Nothing was written.",
        )
    receipt_path = _receipt_path(parent)
    receipts = _read_receipts(receipt_path)
    ours = [
        item for item in receipts
        if _is_our_receipt(item[1], parent, parent_task_id, child_key)
    ]
    anchor_sha = _sha256(anchor_bytes) if anchor_bytes is not None else None
    held = registered.get(parent_task_id, [])
    has_reservation = reservation in held

    if not has_reservation:
        return _already_clear(
            parent, parent_task_id, child_key, receipt_path, ours, anchor_sha,
        )

    evidence = _qualify_evidence(state, child_key)
    _refuse_registry_record(parent, child_key)
    _refuse_live_sessions(
        probe, child_key, evidence["workspace_id"], evidence["agent_names"],
    )
    # Re-read after the probe. A change during it is ambiguous, not a clear.
    anchor_again, registered_again = _read_anchor(parent)
    if anchor_again != anchor_bytes or registered_again != registered:
        raise CorrectionRefused(
            "anchor_changed",
            "the registration anchor changed while the evidence was being checked. "
            "Nothing was written.",
        )
    evidence_again = _qualify_evidence(state, child_key)
    if evidence_again["tree_sha256"] != evidence["tree_sha256"]:
        raise CorrectionRefused(
            "evidence_changed",
            "the evidence directory changed while it was being checked. "
            "Nothing was written.",
        )
    _refuse_registry_record(parent, child_key)

    after_map = {
        task: [item for item in entries if item != reservation]
        for task, entries in registered.items()
    }
    after_bytes = dependencies.render_registration_anchor(after_map)
    after_sha = _sha256(after_bytes) if after_bytes is not None else None
    pending = _pending_receipt(ours, evidence["tree_sha256"], anchor_sha, after_sha)
    if pending is not None:
        _publish_anchor(parent, after_map, after_bytes)
        return _result(
            parent, parent_task_id, child_key, receipt_path, pending,
            applied=True, appended=False,
        )
    if ours:
        raise CorrectionRefused(
            "receipt_conflict",
            "a correction receipt for this parent task and child already exists, "
            "and it does not match the evidence and anchor now on disk. "
            "The receipt was not rewritten and the reservation was not cleared.",
        )

    receipt = _receipt_document(
        parent, parent_task_id, child_key, state, who, moment,
        evidence, registered, anchor_sha, after_map, after_sha,
    )
    line = _append_receipt(receipt_path, receipt)
    try:
        _publish_anchor(parent, after_map, after_bytes)
    except Exception:
        # The line already records the before and after bytes. A later run
        # finishes the anchor write without appending another line.
        raise
    return _result(
        parent, parent_task_id, child_key, receipt_path,
        (line, receipt), applied=True, appended=True,
    )


def _already_clear(parent, parent_task_id, child_key, receipt_path, ours, anchor_sha):
    if not ours:
        raise CorrectionRefused(
            "reservation_absent",
            "the anchor has no reservation for this child on this parent task, "
            "and no correction receipt records a clear. Nothing was written.",
        )
    evidence_dir = Path(child_key) / ".herd" / "state"
    try:
        current = _qualify_evidence(evidence_dir, child_key)
    except CorrectionRefused as exc:
        raise CorrectionRefused(
            "receipt_mismatch",
            "the reservation is already gone, but the evidence directory no longer "
            f"matches a recorded clear ({exc.check}). Nothing was written.",
        )
    matched = [
        item for item in ours
        if item[1]["evidence"]["tree_sha256"] == current["tree_sha256"]
        and _anchor_sha(item[1]["anchor_after"]) == anchor_sha
    ]
    if not matched:
        raise CorrectionRefused(
            "receipt_mismatch",
            "the reservation is already gone, but no correction receipt matches "
            "this evidence and the anchor's current bytes. Nothing was written.",
        )
    return _result(
        parent, parent_task_id, child_key, receipt_path, matched[-1],
        applied=False, appended=False,
    )


def _pending_receipt(ours, tree_sha, before_sha, after_sha):
    """The one receipt that already records this clear, if the anchor write did not land."""

    pending = [
        item for item in ours
        if item[1]["evidence"]["tree_sha256"] == tree_sha
        and _anchor_sha(item[1]["anchor_before"]) == before_sha
        and _anchor_sha(item[1]["anchor_after"]) == after_sha
    ]
    if len(pending) > 1:
        raise CorrectionRefused(
            "receipt_ambiguous",
            "more than one correction receipt records this same clear. "
            "None was rewritten and the reservation was not cleared.",
        )
    return pending[0] if pending else None


def _result(parent, parent_task_id, child_key, receipt_path, item, *, applied, appended):
    line, document = item
    return {
        "applied": applied,
        "receipt_appended": appended,
        "parent_repo": str(parent.repo),
        "parent_task_id": parent_task_id,
        "child_repo": child_key,
        "receipt_path": str(receipt_path),
        "receipt": document,
        "receipt_line": line.decode("utf-8"),
    }


def _read_anchor(parent):
    path = dependencies.anchor_path(parent)
    try:
        if path.is_symlink():
            raise CorrectionRefused(
                "anchor_unreadable",
                f"the registration anchor is a symlink: {path}",
            )
        raw = path.read_bytes() if path.exists() else None
    except CorrectionRefused:
        raise
    except OSError as exc:
        raise CorrectionRefused(
            "anchor_unreadable",
            f"the registration anchor could not be read ({exc.__class__.__name__})",
        )
    try:
        registered = dependencies.read_registration_anchor(parent)
    except RuntimeError as exc:
        raise CorrectionRefused("anchor_unreadable", str(exc))
    if raw is None:
        expected = None
    else:
        expected = dependencies.render_registration_anchor(registered)
        if expected != raw:
            raise CorrectionRefused(
                "anchor_unreadable",
                "the registration anchor's bytes are not the document this "
                "command can rewrite. It was not rewritten.",
            )
    return raw, registered


def _refuse_registry_record(parent, child_key: str) -> None:
    path = parent.herd_root / "state" / "children.json"
    if path.is_symlink():
        raise CorrectionRefused(
            "registry_unreadable",
            f"the child registry is a symlink: {path}",
        )
    if not path.exists():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CorrectionRefused(
            "registry_unreadable",
            f"the child registry could not be read ({exc.__class__.__name__}). "
            "Nothing was written.",
        )
    children = document.get("children") if isinstance(document, dict) else None
    if not isinstance(children, list) or not all(
        isinstance(entry, dict) for entry in children
    ):
        raise CorrectionRefused(
            "registry_invalid",
            "the child registry is not a registry document. Nothing was written.",
        )
    for entry in children:
        if dependencies.normal_repo(entry.get("repo")) == child_key:
            raise CorrectionRefused(
                "registry_record_present",
                "the parent's child registry already has a record for this "
                "repository. Work that was recorded stays blocking. "
                "Nothing was written.",
            )


def _qualify_evidence(state: Path, child_key: str) -> dict:
    """The one accepted diagnostic, or a refusal that leaves the anchor alone."""

    entries = _list_real(state)
    names = [entry[0] for entry in entries]
    if set(names) & _TASK_NAMES:
        raise CorrectionRefused(
            "child_task_present",
            "the child's state holds a task record. Work that was dispatched "
            "stays blocking. Nothing was written.",
        )
    if set(names) & _CAME_UP_NAMES:
        raise CorrectionRefused(
            "herd_came_up",
            "the herd process came up: state holds runtime.json or "
            "role-bindings.json. start_herd writes runtime.json while it is "
            "starting and writes role bindings only after every role binds, "
            "which is before it returns and before any task is dispatched. "
            "No task file does not prove dispatch never started. "
            "The reservation stays. Nothing was written.",
        )
    if names != ["diagnostics"]:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the child's state directory is not exactly one diagnostics directory "
            f"(found {names or 'nothing'}). Anything else stays blocking. "
            "Nothing was written.",
        )
    diagnostic_dir = state / "diagnostics"
    if diagnostic_dir.is_symlink() or not diagnostic_dir.is_dir():
        raise CorrectionRefused(
            "evidence_ambiguous",
            "diagnostics is not a real directory. Nothing was written.",
        )
    files = _list_real(diagnostic_dir)
    file_names = [entry[0] for entry in files]
    if len(file_names) != 1:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "diagnostics must hold exactly one startup-binding-failure record "
            f"(found {file_names or 'nothing'}). Zero, or more than one, stays "
            "blocking. Nothing was written.",
        )
    filename, kind, _path = files[0]
    path = diagnostic_dir / filename
    if kind != "file" or path.is_symlink() or not _DIAGNOSTIC_NAME.match(filename):
        raise CorrectionRefused(
            "evidence_ambiguous",
            f"{filename} is not one startup-binding-failure record. "
            "An agent-start diagnostic, or any other file, is not this evidence. "
            "Nothing was written.",
        )
    raw = _read_limited(path)
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic is not UTF-8 JSON. Nothing was written.",
        )
    parsed = _accept_diagnostic(record, child_key, filename)
    digest = hashlib.sha256(raw).hexdigest()
    rel = "diagnostics/" + filename
    files_meta = [{"path": rel, "sha256": digest, "bytes": len(raw)}]
    tree = _tree_sha256(files_meta)
    return {
        "files": files_meta,
        "tree_sha256": tree,
        "diagnostic": parsed,
        "workspace_id": parsed["workspace_id"],
        "agent_names": parsed["agent_names"],
    }


def _accept_diagnostic(record, child_key: str, filename: str) -> dict:
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic is not a closed startup-binding record. "
            "Nothing was written.",
        )
    if record["schema"] != runtime.BINDING_DIAGNOSTIC_SCHEMA:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic schema is not the startup-binding record. "
            "Nothing was written.",
        )
    if record["stage"] == lifecycle.STAGE_HEARTBEAT_CONTROLLER:
        raise CorrectionRefused(
            "herd_came_up",
            "the recorded failure is heartbeat_controller, which runs only after "
            "role bindings were established. The herd process came up even though "
            "no task file was written. The reservation stays. Nothing was written.",
        )
    if record["stage"] != lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS:
        raise CorrectionRefused(
            "evidence_ambiguous",
            f"the diagnostic stage {record['stage']!r} is not "
            "establish_role_bindings. Nothing was written.",
        )
    failure = record["failure"]
    workspace = record["workspace"]
    if (
        not isinstance(failure, dict)
        or set(failure) != _FAILURE_KEYS
        or not isinstance(workspace, dict)
        or set(workspace) != _WORKSPACE_KEYS
    ):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic's failure or workspace is not the closed record. "
            "Nothing was written.",
        )
    if failure["type"] == "UNKNOWN":
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic records an UNKNOWN failure (its class name only). "
            "That does not establish a pre-dispatch binding failure. "
            "Nothing was written.",
        )
    if failure["type"] != "BINDINGS_NOT_ESTABLISHED":
        raise CorrectionRefused(
            "evidence_ambiguous",
            f"the diagnostic failure type {failure['type']!r} is not "
            "BINDINGS_NOT_ESTABLISHED. Nothing was written.",
        )
    if failure["class"] != "BindingsNotEstablished":
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic's failure class is not BindingsNotEstablished. "
            "Nothing was written.",
        )
    if dependencies.normal_repo(workspace.get("repo")) != child_key:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic names a different repository than the child. "
            "Nothing was written.",
        )
    workspace_id = workspace.get("workspace_id")
    created = workspace.get("runtime_created_at")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic has no workspace id, so a live session cannot be "
            "excluded. Nothing was written.",
        )
    if not isinstance(created, int) or isinstance(created, bool):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic's runtime_created_at is not an integer. "
            "Nothing was written.",
        )
    if not isinstance(record["redaction"], str) or not record["redaction"]:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic has no redaction note. Nothing was written.",
        )
    if not isinstance(record["captured_at"], str) or not record["captured_at"]:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic has no captured_at time. Nothing was written.",
        )
    if not isinstance(record["limitations"], list) or not all(
        isinstance(item, str) for item in record["limitations"]
    ):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic limitations are not a list of text. Nothing was written.",
        )
    settle = record["settle_seconds"]
    if isinstance(settle, bool) or not isinstance(settle, (int, float)):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic settle_seconds is not a number. Nothing was written.",
        )
    roles = record["roles"]
    total = record["roles_total"]
    truncated = record["roles_truncated"]
    if (
        not isinstance(roles, list)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or not isinstance(truncated, bool)
    ):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic's role list is not the closed record. Nothing was written.",
        )
    if truncated or total != len(roles):
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic does not list every affected role, so a live session "
            "could be unnamed. Nothing was written.",
        )
    names = []
    for role in roles:
        if not isinstance(role, dict) or set(role) != _ROLE_KEYS:
            raise CorrectionRefused(
                "evidence_ambiguous",
                "a diagnostic role entry is not the closed record. Nothing was written.",
            )
        if role["session"] not in runtime.BINDING_SESSION_STATES:
            raise CorrectionRefused(
                "evidence_ambiguous",
                "a diagnostic role session is not ABSENT, PRESENT, or UNKNOWN. "
                "Nothing was written.",
            )
        for field in ("logical", "reason"):
            if not isinstance(role[field], str) or not role[field]:
                raise CorrectionRefused(
                    "evidence_ambiguous",
                    f"a diagnostic role {field} is empty. Nothing was written.",
                )
        agent = role["agent"]
        pane = role["pane"]
        if agent is not None and (not isinstance(agent, str) or not agent):
            raise CorrectionRefused(
                "evidence_ambiguous",
                "a diagnostic role agent name is not text. Nothing was written.",
            )
        if pane is not None and not isinstance(pane, str):
            raise CorrectionRefused(
                "evidence_ambiguous",
                "a diagnostic role pane is not text. Nothing was written.",
            )
        if isinstance(agent, str):
            names.append(agent)
    return {
        "name": filename,
        "schema": record["schema"],
        "stage": record["stage"],
        "failure_type": failure["type"],
        "failure_class": failure["class"],
        "workspace_id": workspace_id,
        "captured_at": record["captured_at"],
        "agent_names": names,
    }


def _list_real(directory: Path) -> list[tuple[str, str, Path]]:
    """Sorted ``(name, 'file'|'dir', path)``. A symlink or odd node is a refusal."""

    try:
        found = list(os.scandir(directory))
    except OSError as exc:
        raise CorrectionRefused(
            "evidence_unreadable",
            f"{directory} could not be listed ({exc.__class__.__name__}). "
            "Nothing was written.",
        )
    if len(found) > _MAX_DIRECTORY_ENTRIES:
        raise CorrectionRefused(
            "evidence_ambiguous",
            f"{directory} has more than {_MAX_DIRECTORY_ENTRIES} entries. "
            "That is not the one-diagnostic state. Nothing was written.",
        )
    listed = []
    for entry in sorted(found, key=lambda item: item.name):
        if entry.is_symlink():
            raise CorrectionRefused(
                "evidence_ambiguous",
                f"{entry.path} is a symlink. Nothing was written.",
            )
        path = Path(entry.path)
        if entry.is_file(follow_symlinks=False):
            kind = "file"
        elif entry.is_dir(follow_symlinks=False):
            kind = "dir"
        else:
            raise CorrectionRefused(
                "evidence_ambiguous",
                f"{entry.path} is not a regular file or directory. Nothing was written.",
            )
        listed.append((entry.name, kind, path))
    return listed


def _read_limited(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CorrectionRefused(
            "evidence_unreadable",
            f"the diagnostic could not be opened ({exc.__class__.__name__}). "
            "Nothing was written.",
        )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CorrectionRefused(
                "evidence_ambiguous",
                "the diagnostic is not a regular file. Nothing was written.",
            )
        chunks = []
        remaining = _READ_CEILING + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except CorrectionRefused:
        raise
    except OSError as exc:
        raise CorrectionRefused(
            "evidence_unreadable",
            f"the diagnostic could not be read ({exc.__class__.__name__}). "
            "Nothing was written.",
        )
    finally:
        os.close(fd)
    if len(raw) > _READ_CEILING:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic is larger than a startup-binding record. "
            "Nothing was written.",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic is not UTF-8. Nothing was written.",
        )
    if len(text) > runtime.BINDING_DIAGNOSTIC_FILE_MAX_CHARS:
        raise CorrectionRefused(
            "evidence_ambiguous",
            "the diagnostic exceeds the startup-binding record cap. "
            "Nothing was written.",
        )
    return raw


def _refuse_live_sessions(probe, child_key, workspace_id, agent_names) -> None:
    answer = _probe(probe)
    named = set(agent_names)
    for record in answer:
        name = record.get("name")
        if isinstance(name, str) and name in named:
            raise CorrectionRefused(
                "live_session",
                f"agent {name} is still listed, and the diagnostic names it. "
                "A live child session stays blocking. Nothing was written.",
            )
        workspace = record.get("workspace_id")
        if isinstance(workspace, str) and workspace == workspace_id:
            raise CorrectionRefused(
                "live_session",
                "a listed agent is still in the diagnostic's workspace "
                f"{workspace_id}. A live child session stays blocking. "
                "Nothing was written.",
            )
        cwd = record.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd):
            raise CorrectionRefused(
                "live_session_ambiguous",
                "a listed agent has no absolute cwd, so it cannot be excluded "
                "as someone else's session. Nothing was written.",
            )
        resolved = dependencies.normal_repo(cwd)
        if resolved is None:
            raise CorrectionRefused(
                "live_session_ambiguous",
                f"a listed agent's cwd {cwd!r} could not be resolved. "
                "Nothing was written.",
            )
        if resolved == child_key:
            shown = name if isinstance(name, str) else "(unnamed)"
            raise CorrectionRefused(
                "live_session",
                f"agent {shown} has cwd {resolved}, the child repository. "
                "A live child session stays blocking. Nothing was written.",
            )


def _probe(probe) -> list:
    try:
        answer = probe()
    except CorrectionRefused:
        raise
    except Exception as exc:
        raise CorrectionRefused(
            "session_probe_failed",
            f"the session probe raised {exc.__class__.__name__}. Nothing was written.",
        )
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        reason = answer.get("reason") if isinstance(answer, dict) else None
        if not isinstance(reason, str) or not reason:
            reason = "the session probe did not succeed"
        raise CorrectionRefused(
            "session_probe_failed",
            reason + " Nothing was written.",
        )
    agents = answer.get("agents")
    if not isinstance(agents, list) or not all(
        isinstance(agent, dict) for agent in agents
    ):
        raise CorrectionRefused(
            "session_probe_failed",
            "the session probe did not return a list of agent records. "
            "Nothing was written.",
        )
    return agents


def _tree_sha256(files: list[dict]) -> str:
    blob = "".join(
        f"{item['path']}\0{item['sha256']}\0{item['bytes']}\n" for item in files
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _registered_state(registered: dict) -> dict:
    return {
        task: [
            {"repo": repo, "task_id": task_id}
            for repo, task_id in entries
        ]
        for task, entries in sorted(registered.items())
        if entries
    }


def _receipt_path(parent) -> Path:
    return parent.herd_root / "state" / RECEIPT_FILE


def _read_receipts(path: Path) -> list[tuple[bytes, dict]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise CorrectionRefused(
            "receipt_unreadable",
            f"the correction receipt is not a regular file: {path}. "
            "It was not rewritten.",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CorrectionRefused(
            "receipt_unreadable",
            f"the correction receipt could not be read ({exc.__class__.__name__}). "
            "It was not rewritten.",
        )
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise CorrectionRefused(
            "receipt_unreadable",
            "the correction receipt does not end on a line boundary. "
            "It was not rewritten.",
        )
    found = []
    for line in raw.splitlines(keepends=True):
        if line != line.rstrip(b"\r\n") + b"\n":
            raise CorrectionRefused(
                "receipt_unreadable",
                "a correction receipt line is not a single LF-terminated record. "
                "The file was not rewritten.",
            )
        try:
            document = json.loads(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise CorrectionRefused(
                "receipt_unreadable",
                "a correction receipt line is not JSON. The file was not rewritten.",
            )
        if not isinstance(document, dict):
            raise CorrectionRefused(
                "receipt_unreadable",
                "a correction receipt line is not an object. The file was not rewritten.",
            )
        if document.get("schema") == RECEIPT_SCHEMA and not _receipt_shape(document):
            raise CorrectionRefused(
                "receipt_unreadable",
                "a correction receipt is missing its audit fields. "
                "The file was not rewritten.",
            )
        found.append((line, document))
    return found


def _receipt_shape(document: dict) -> bool:
    required = {
        "schema", "action", "operator", "operator_uid", "at",
        "parent_repo", "parent_task_id", "child_repo", "evidence_dir",
        "evidence", "diagnostic", "anchor_before", "anchor_after",
    }
    if not required <= set(document):
        return False
    evidence = document["evidence"]
    if not isinstance(evidence, dict) or not isinstance(
        evidence.get("tree_sha256"), str
    ):
        return False
    for key in ("anchor_before", "anchor_after", "diagnostic"):
        body = document[key]
        if not isinstance(body, dict):
            return False
    for key in ("anchor_before", "anchor_after"):
        body = document[key]
        if "sha256" not in body or "registered" not in body:
            return False
    return True


def _is_our_receipt(document, parent, parent_task_id, child_key) -> bool:
    return (
        document.get("schema") == RECEIPT_SCHEMA
        and document.get("action") == "clear_reservation"
        and document.get("parent_repo") == str(parent.repo)
        and document.get("parent_task_id") == parent_task_id
        and document.get("child_repo") == child_key
    )


def _anchor_sha(anchor: dict):
    sha = anchor.get("sha256") if isinstance(anchor, dict) else None
    if sha is None:
        return None
    if not isinstance(sha, str) or len(sha) != 64:
        raise CorrectionRefused(
            "receipt_unreadable",
            "a correction receipt's anchor hash is not a sha256. "
            "The file was not rewritten.",
        )
    return sha


def _receipt_document(
    parent, parent_task_id, child_key, state, who, moment,
    evidence, registered, anchor_sha, after_map, after_sha,
) -> dict:
    diagnostic = dict(evidence["diagnostic"])
    diagnostic["sha256"] = evidence["files"][0]["sha256"]
    diagnostic.pop("agent_names", None)
    return {
        "schema": RECEIPT_SCHEMA,
        "action": "clear_reservation",
        "command": COMMAND,
        "operator": who,
        "operator_uid": os.getuid(),
        "at": moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "parent_repo": str(parent.repo),
        "parent_task_id": parent_task_id,
        "child_repo": child_key,
        "evidence_dir": str(state),
        "evidence": {
            "tree": EVIDENCE_TREE,
            "tree_sha256": evidence["tree_sha256"],
            "files": evidence["files"],
        },
        "diagnostic": diagnostic,
        "anchor_before": {
            "sha256": anchor_sha,
            "registered": _registered_state(registered),
        },
        "anchor_after": {
            "sha256": after_sha,
            "registered": _registered_state(after_map),
        },
    }


def _append_receipt(path: Path, document: dict) -> bytes:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise CorrectionRefused(
            "receipt_unreadable",
            f"the correction receipt is not a regular file: {path}",
        )
    line = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if b"\n" in line[:-1]:
        raise CorrectionRefused(
            "receipt_unreadable",
            "the new receipt line would contain a line break. Nothing was appended.",
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CorrectionRefused(
            "receipt_unwritable",
            f"the correction receipt could not be opened ({exc.__class__.__name__}). "
            "The anchor was not changed.",
        )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CorrectionRefused(
                "receipt_unreadable",
                "the correction receipt is not a regular file. The anchor was not changed.",
            )
        written = os.write(fd, line)
        if written != len(line):
            raise CorrectionRefused(
                "receipt_unwritable",
                "the correction receipt was only partly written. It was not rewritten, "
                "and the anchor was not changed.",
            )
        os.fsync(fd)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except CorrectionRefused:
        raise
    except OSError as exc:
        raise CorrectionRefused(
            "receipt_unwritable",
            f"the correction receipt could not be appended ({exc.__class__.__name__}). "
            "The anchor was not changed.",
        )
    finally:
        os.close(fd)
    return line


def _publish_anchor(parent, after_map, after_bytes) -> None:
    dependencies._write_registration_anchor(parent, after_map)
    path = dependencies.anchor_path(parent)
    if after_bytes is None:
        if path.exists():
            raise CorrectionRefused(
                "anchor_unreadable",
                "the anchor file is still present after it should have been removed. "
                "The receipt was not rewritten.",
            )
        return
    try:
        found = path.read_bytes()
    except OSError as exc:
        raise CorrectionRefused(
            "anchor_unreadable",
            f"the anchor could not be read back ({exc.__class__.__name__}). "
            "The receipt was not rewritten.",
        )
    if found != after_bytes:
        raise CorrectionRefused(
            "anchor_unreadable",
            "the anchor bytes after the write are not the bytes the receipt names. "
            "The receipt was not rewritten.",
        )
