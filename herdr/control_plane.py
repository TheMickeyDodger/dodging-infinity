"""Programmatic control plane for Herdr."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import time

from pathlib import Path
from typing import Any

from .dependencies import (
    assert_child_dependencies_complete as enforce_child_dependencies_complete,
    child_dependencies as inspect_child_dependencies,
)
from .heartbeat import (
    restart_heartbeat as restart_heartbeat_runtime,
    run_heartbeat,
    stop_heartbeat as stop_heartbeat_runtime,
)
from .initialize import initialize_herd
from .instance import HerdrInstance
from .lifecycle import start_herd
from .policy import HerdrPolicy
from .tasks import dispatch_task


class ChildHistoryError(RuntimeError):
    """The parent's child-spawn history (``.herd/state/children.json``)
    cannot be read as a usable history. From ``spawn_child`` this is the
    PRE-EFFECT refusal: nothing was spawned and nothing appended.

    Task 8 R19-1: that history is ownership evidence — cleanup and a
    follow-up's retirement prove a child's workspace from its record — so
    a history that cannot be read is surfaced, never replaced by an empty
    one that looks valid and then overwritten."""


class ChildSpawnPartialEffect(RuntimeError):
    """POST-SPAWN, PARTIAL effect (Task 8 R19-1): ``spawn_child`` spawned
    the child — its runtime and task exist as far as ``spawn`` reported,
    and whether it is still running is not known here — but appending its
    record did not complete cleanly. One of three states, classified by
    the PHASE the append reached and never conflated:
    ``ChildRecordNotAppended`` (before the replace),
    ``ChildRecordDurabilityUnknown`` (after the replace, before the
    directory sync completed) or ``ChildRecordLockUnknown`` (after both,
    only the lock's release failed). No record is ever synthesised,
    nothing is undone and nothing is retried.

    Deliberately NOT a ``ChildHistoryError``: that is the PRE-EFFECT
    refusal (nothing spawned), and the outcomes must not be caught as one."""


class ChildRecordNotAppended(ChildSpawnPartialEffect):
    """The failure came BEFORE the new history replaced the old one — the
    history could not be re-read under the lock, the temp file could not
    be written or synced, or the replace (``os.replace``, an atomic rename)
    failed — so the record was NOT appended and the history is left as it
    was on disk. A spawned child with no record is exactly what the
    release treats as unproven and retains."""


class ChildRecordDurabilityUnknown(ChildSpawnPartialEffect):
    """The failure came AFTER the replace succeeded: the new history — the
    one holding the record — had already been moved into place, and only
    the directory's open or fsync failed. The record's VISIBILITY is
    reported as observed by re-reading the history (visible, absent, or
    unknown when that read fails); its DURABILITY across a crash is
    unknown. The history is not rolled back."""


class ChildRecordLockUnknown(ChildSpawnPartialEffect):
    """The append COMPLETED — the new history replaced the old one and its
    directory was synced — and only releasing the history's lock
    afterwards failed: the record is in place, and whether the lock is
    still held (a later append would wait on it) is unknown."""


class _ReplacedNotSynced(Exception):
    """Internal: ``_append_child_record`` replaced the history, then could
    not open or fsync its directory (the cause is chained)."""


class _AppendedLockNotReleased(Exception):
    """Internal: ``_append_child_record`` replaced the history and synced
    its directory, then could not release the lock (the cause is
    chained)."""


def _release_history_lock(lock):
    """Unlock and close the history's lock descriptor. NEVER raises —
    returns the first ``OSError``, or None — so it cannot replace an
    exception already propagating out of the append."""

    problem = None

    try:
        fcntl.flock(
            lock,
            fcntl.LOCK_UN,
        )
    except OSError as exc:
        problem = exc

    try:
        os.close(lock)
    except OSError as exc:
        problem = problem or exc

    return problem


def _refuse_duplicate_members(pairs):
    """``json.loads`` keeps the LAST of two same-named members; rewriting
    that document would silently drop the first. Refused instead."""

    document = {}

    for key, value in pairs:
        if key in document:
            raise ValueError(
                f"duplicate member {key!r}"
            )

        document[key] = value

    return document


def _load_child_history(children_path: Path) -> dict:
    """The existing child-spawn history, validated and never reset. It
    only READS: whatever it raises, every existing record is left exactly
    as it is on disk.

    A MISSING file is the only empty history. Raises ``ChildHistoryError``
    for an unreadable file, undecodable bytes, invalid JSON, a duplicate
    member, a document that is not an object, a ``children`` member that
    is absent or not a list, and — the RECORD contract — a record the
    scoped reader (``herdr.observe.observe_spawn_records`` with
    ``relevant``, which every ownership route reads through) cannot
    classify for ANY lease: one that is not a JSON object, or that names
    no repository (no non-blank ``repo`` string). Such a history proves
    no child's ownership, including the next one's. A record's identity
    fields are checked by that reader only when the record is relevant
    to the lease being read, so a record malformed there is KEPT: the
    reader refuses it for its own lease, and refusing the whole history
    for it would deny every unrelated spawn evidence it never reads."""

    try:
        text = children_path.read_text(
            encoding="utf-8"
        )
    except FileNotFoundError:
        return {
            "version": 1,
            "children": [],
        }
    except (OSError, UnicodeDecodeError) as exc:
        raise ChildHistoryError(
            f"Child-spawn history {children_path} is unreadable "
            f"({exc.__class__.__name__}); it is left as it is on disk."
        ) from exc

    try:
        document = json.loads(
            text,
            object_pairs_hook=_refuse_duplicate_members,
        )
    except ValueError as exc:
        raise ChildHistoryError(
            f"Child-spawn history {children_path} is not valid JSON "
            f"({exc}); it is left as it is on disk."
        ) from exc

    if (
        not isinstance(document, dict)
        or not isinstance(document.get("children"), list)
    ):
        raise ChildHistoryError(
            f"Child-spawn history {children_path} is not a "
            "`children` list document; it is left as it is on disk."
        )

    for index, record in enumerate(document["children"]):
        problem = _record_problem(record)

        if problem is not None:
            raise ChildHistoryError(
                f"Child-spawn history {children_path}: record {index} "
                f"{problem}; it is left as it is on disk."
            )

    return document


def _record_problem(record) -> str | None:
    """Why ``record`` breaks the history-wide RECORD contract (see
    ``_load_child_history``), or None."""

    if not isinstance(record, dict):
        return "is not a JSON object"

    if (
        not isinstance(record.get("repo"), str)
        or not record["repo"].strip()
    ):
        return (
            "names no repository, so its relevance to any lease "
            "cannot be decided"
        )

    return None


def _append_child_record(children_path: Path, record: dict) -> None:
    """Append ONE record to the child-spawn history.

    Under an exclusive lock on a sibling lock file (the history file
    itself is replaced, so it cannot carry the lock), the history is
    re-read and validated, the record appended, and the document written
    to a temp file in the same directory, fsynced and moved into place
    with ``os.replace`` (then the directory is fsynced) — the
    ``identity.save_bindings`` shape. A concurrent append waits for the
    lock and re-reads the result, so no record is lost between a read and
    a write; an interrupted write leaves the previous document, never a
    half-written one. BEFORE the replace, a history that cannot be read
    raises ``ChildHistoryError`` and a failed write raises its ``OSError``
    — nothing is moved into place (a leftover ``.partial`` temp file is
    never read). AFTER the replace, a failed directory open or fsync
    raises ``_ReplacedNotSynced``: the new history is already in place.
    Releasing the lock never masks either: a release failure is raised
    only when nothing else is propagating — the append then COMPLETED —
    as ``_AppendedLockNotReleased``.

    NOT a destructive site: no existing record is removed and no file is
    unlinked; ``os.replace`` supersedes the previous document."""

    children_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock = os.open(
        str(children_path.with_name(children_path.name + ".lock")),
        os.O_RDWR | os.O_CREAT,
        0o600,
    )

    in_flight = None

    try:
        fcntl.flock(
            lock,
            fcntl.LOCK_EX,
        )

        document = _load_child_history(
            children_path
        )

        # The new record meets the same contract, or it would make the
        # whole history unreadable to every lease.
        problem = _record_problem(
            record
        )

        if problem is not None:
            raise ChildHistoryError(
                f"The new child record {problem}; appending it would "
                f"make {children_path} unusable to every lease, so it "
                "is left as it is on disk."
            )

        document["children"].append(
            record
        )

        payload = (
            json.dumps(
                document,
                indent=2,
            )
            + "\n"
        )

        temporary = children_path.with_name(
            children_path.name + ".%d.partial" % os.getpid()
        )

        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            str(temporary),
            str(children_path),
        )

        try:
            directory = os.open(
                str(children_path.parent),
                os.O_RDONLY,
            )

            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise _ReplacedNotSynced() from exc
    except BaseException as exc:
        in_flight = exc
        raise
    finally:
        # Classified by the phase reached: a release failure never
        # replaces a cause already propagating (the replace's own outcome
        # included); with nothing in flight the append has COMPLETED.
        released = _release_history_lock(
            lock
        )

        if released is not None and in_flight is None:
            raise _AppendedLockNotReleased() from released


class HerdrControlPlane:
    """Primary programmatic interface for managing Herdr instances.

    Human-facing clients such as `herdctl` and higher-level orchestrators
    should delegate to this object rather than owning Herdr behavior.
    """

    def instance(self, repo: str | Path) -> HerdrInstance:
        return HerdrInstance(repo)

    def initialize(
        self,
        repo: str | Path,
        *,
        preset: str | None = None,
        test_command: str | None = None,
        alias: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict:
        """Initialize or update a Herdr without invoking herdctl init."""
        return initialize_herd(
            repo,
            preset=preset,
            test_command=test_command,
            alias=alias,
            policy=policy,
        )

    def start(
        self,
        repo: str | Path,
        *,
        force: bool = False,
    ) -> dict:
        """Start a complete Herdr without invoking herdctl bootstrap."""
        return start_herd(
            self.instance(repo),
            force=force,
        )

    def dispatch_task(
        self,
        repo: str | Path,
        text: str,
        *,
        rejection_drill: bool = False,
        task_policy: dict[str, Any] | None = None,
    ) -> dict:
        """Dispatch a top-level task without invoking herdctl task."""
        return dispatch_task(
            self.instance(repo),
            text,
            rejection_drill=rejection_drill,
            task_policy=task_policy,
        )

    def heartbeat(
        self,
        repo: str | Path,
        *,
        once: bool = False,
    ) -> None:
        return run_heartbeat(
            self.instance(repo),
            once=once,
        )

    def stop_heartbeat(
        self,
        repo: str | Path,
    ) -> None:
        return stop_heartbeat_runtime(
            self.instance(repo)
        )

    def restart_heartbeat(
        self,
        repo: str | Path,
    ) -> None:
        return restart_heartbeat_runtime(
            self.instance(repo)
        )

    def policy(
        self,
        repo: str | Path,
        task_policy: dict[str, Any] | None = None,
    ) -> HerdrPolicy:
        return self.instance(repo).effective_policy(task_policy)

    def merge_policy(
        self,
        repo: str | Path,
        policy: dict[str, Any],
    ) -> HerdrPolicy:
        return self.instance(repo).merge_policy(
            policy
        )

    def child_dependencies(
        self,
        parent_repo: str | Path,
        parent_task_id: str | None = None,
    ) -> list[dict]:
        """Inspect child Herdr dependencies for a parent task."""
        return inspect_child_dependencies(
            self.instance(parent_repo),
            parent_task_id,
        )

    def require_child_dependencies_complete(
        self,
        parent_repo: str | Path,
        parent_task_id: str,
    ) -> None:
        """Fail closed while a parent task has unresolved child Herdrs."""
        return enforce_child_dependencies_complete(
            self.instance(parent_repo),
            parent_task_id,
        )

    def spawn_child(
        self,
        parent_repo: str | Path,
        target_repo: str | Path,
        *,
        task: str,
        preset: str | None = None,
        test_command: str | None = None,
        alias: str | None = None,
        rules: list[str] | None = None,
        policy: dict[str, Any] | None = None,
        task_policy: dict[str, Any] | None = None,
        force: bool = False,
        rejection_drill: bool = False,
    ) -> dict:
        """Spawn a separately repo-scoped child Herdr."""

        parent = self.instance(
            parent_repo
        )

        if not parent.initialized:
            raise RuntimeError(
                f"Parent repository {parent.repo} "
                "is not an initialized Herdr."
            )

        runtime_path = (
            parent.herd_root
            / "state"
            / "runtime.json"
        )

        if not runtime_path.exists():
            raise RuntimeError(
                f"Parent Herdr {parent.repo} is not running."
            )

        parent_task_id = None

        parent_task_path = (
            parent.herd_root
            / "state"
            / "task.json"
        )

        if parent_task_path.exists():
            try:
                parent_task = json.loads(
                    parent_task_path.read_text()
                )
            except Exception:
                parent_task = {}

            if (
                parent_task.get("status")
                == "ACTIVE"
            ):
                parent_task_id = (
                    parent_task.get("id")
                )

        target = Path(
            target_repo
        ).expanduser().resolve()

        if target == parent.repo:
            raise ValueError(
                "A Herdr cannot spawn itself as a child."
            )

        child_policy = copy.deepcopy(
            policy or {}
        )

        if rules is not None:
            if not isinstance(rules, list):
                raise ValueError(
                    "Child rules must be a list."
                )

            policy_rules = child_policy.setdefault(
                "rules",
                [],
            )

            if not isinstance(policy_rules, list):
                raise ValueError(
                    "policy.rules must be a list."
                )

            for rule in rules:
                if (
                    not isinstance(rule, str)
                    or not rule.strip()
                ):
                    raise ValueError(
                        "Child rules must contain "
                        "only non-empty strings."
                    )

                rule = rule.strip()

                if rule not in policy_rules:
                    policy_rules.append(
                        rule
                    )

        children_path = (
            parent.herd_root
            / "state"
            / "children.json"
        )

        # Task 8 R19-1: the existing history must be usable BEFORE any
        # child is started — an unreadable or malformed one refuses here,
        # PRE-EFFECT: nothing spawned, nothing appended, the file untouched.
        try:
            _load_child_history(
                children_path
            )
        except ChildHistoryError as exc:
            raise ChildHistoryError(
                f"{exc} Nothing was spawned and nothing appended."
            ) from exc

        result = self.spawn(
            target,
            task=task,
            preset=preset,
            test_command=test_command,
            alias=alias,
            policy=child_policy or None,
            task_policy=task_policy,
            force=force,
            rejection_drill=rejection_drill,
        )

        runtime = result.get(
            "runtime"
        ) or {}

        task_state = result.get(
            "task"
        ) or {}

        record = {
            "requested_at": int(
                time.time()
            ),
            "parent_repo": str(
                parent.repo
            ),
            "parent_task_id": parent_task_id,
            "dependency": bool(parent_task_id),
            "repo": result.get(
                "repo",
                str(target),
            ),
            "task_id": task_state.get(
                "id"
            ),
            "task_status": task_state.get(
                "status"
            ),
            "workspace_id": runtime.get(
                "workspace_id"
            ),
            "agents": runtime.get(
                "agents",
                {},
            ),
        }

        # Appended under the history's lock, re-read and validated there,
        # written atomically. The child is ALREADY spawned here, so every
        # failure is a PARTIAL effect, raised as such — never as a refusal —
        # with NO record synthesised, nothing undone and nothing retried.
        spawned = (
            f"PARTIAL EFFECT: child task {record['task_id']} "
            f"(workspace {record['workspace_id']}) WAS spawned at "
            f"{record['repo']}; whether it is still running is not "
            "known here."
        )

        try:
            _append_child_record(
                children_path,
                record,
            )
        except _ReplacedNotSynced as exc:
            # AFTER the replace: the new history is in place. Its
            # visibility is OBSERVED by reading it back; its durability
            # is unknown and is not claimed either way.
            try:
                visible = record in _load_child_history(
                    children_path
                )["children"]
            except ChildHistoryError as unread:
                seen = (
                    f"whether it is visible there is UNKNOWN (reading "
                    f"the history back failed: {unread})"
                )
            else:
                seen = (
                    "it IS visible there (read back after the replace)"
                    if visible
                    else "it is NOT visible there when the history is "
                    "read back after the replace"
                )

            raise ChildRecordDurabilityUnknown(
                f"{spawned} Its record was written to {children_path} "
                f"and the new history replaced the old one; {seen}. Its "
                "DURABILITY is UNKNOWN: opening or syncing the directory "
                f"after the replace failed ({exc.__cause__}). Nothing "
                "was undone or retried."
            ) from exc.__cause__
        except _AppendedLockNotReleased as exc:
            raise ChildRecordLockUnknown(
                f"{spawned} Its record was appended to {children_path}: the new "
                "history replaced the old one and its directory was synced. "
                "Releasing the history lock afterwards failed "
                f"({exc.__cause__}), so whether the lock is still held is "
                "unknown; a later append would wait on it."
            ) from exc.__cause__
        except (ChildHistoryError, OSError) as exc:
            raise ChildRecordNotAppended(
                f"{spawned} Its record was NOT appended to "
                f"{children_path}: the failure came before the new "
                "history replaced the old one, which is left as it was "
                f"on disk ({exc}). No record was synthesised: its "
                "ownership is not provable from the history."
            ) from exc

        result["parent_repo"] = str(
            parent.repo
        )

        result["child_record"] = record

        return result

    def spawn(
        self,
        repo: str | Path,
        *,
        task: str,
        preset: str | None = None,
        test_command: str | None = None,
        alias: str | None = None,
        policy: dict[str, Any] | None = None,
        task_policy: dict[str, Any] | None = None,
        force: bool = False,
        rejection_drill: bool = False,
    ) -> dict:
        """Initialize if needed, configure, start, and task a Herdr."""

        herd = self.instance(repo)
        initialization = None

        if (
            not herd.initialized
            or preset is not None
            or test_command is not None
            or alias is not None
        ):
            initialization = self.initialize(
                herd.repo,
                preset=preset,
                test_command=test_command,
                alias=alias,
                policy=policy,
            )

        elif policy:
            herd.merge_policy(
                policy
            )

        runtime = self.start(
            herd.repo,
            force=force,
        )

        task_state = self.dispatch_task(
            herd.repo,
            task,
            rejection_drill=rejection_drill,
            task_policy=task_policy,
        )

        return {
            "repo": str(herd.repo),
            "initialization": initialization,
            "runtime": runtime,
            "task": task_state,
            "policy": herd.effective_policy().to_dict(),
        }

    def set_policy(
        self,
        repo: str | Path,
        dotted_path: str,
        value: Any,
    ) -> HerdrPolicy:
        return self.instance(repo).set_policy(dotted_path, value)

    def add_rule(
        self,
        repo: str | Path,
        rule: str,
    ) -> HerdrPolicy:
        return self.instance(repo).add_rule(rule)

    def remove_rule(
        self,
        repo: str | Path,
        rule: str,
    ) -> HerdrPolicy:
        return self.instance(repo).remove_rule(rule)
