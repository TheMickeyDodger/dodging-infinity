"""Parent/child task dependency tracking for hierarchical Herdrs."""

from __future__ import annotations

import json
import os
import re
import stat
import hashlib
from pathlib import Path

from .instance import HerdrInstance

# Where `herdctl task-complete` writes the whole task record when it marks a
# task COMPLETE (herdctl.TASK_ARCHIVE). The file stays when a later task
# displaces `task.json`, which is why it is the child's own durable evidence.
TASK_ARCHIVE_PARTS = (
    "state",
    "tasks",
)

_SAFE_TASK_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)


def _parent_task(
    herd: HerdrInstance,
) -> dict:
    path = (
        herd.herd_root
        / "state"
        / "task.json"
    )

    if not path.exists():
        return {
            "status": "IDLE",
        }

    try:
        return json.loads(
            path.read_text()
        )
    except Exception:
        return {
            "status": "ERROR",
        }


def durable_complete_evidence(
    child_repo: str | Path,
    task_id: str,
) -> dict:
    """The child's own durable record that `task_id` COMPLETED, or why there is none.

    This is evidence only if ALL of it holds: the id is a plain name (never a
    path); the file is a plain regular file inside the child's own archive
    directory (never a link); it parses to an object; its `id` is the id asked
    for; its status is exactly `COMPLETE`; its `completed_at` is a real time;
    and the repository it names is THIS child. Anything less is "no evidence",
    never a guess, so a genuinely wrong child still reads MISMATCH.
    """

    if (
        not isinstance(task_id, str)
        or not _SAFE_TASK_ID.match(task_id)
    ):
        return {
            "ok": False,
            "reason": "the task id is not a plain name",
        }

    repo = Path(
        child_repo
    ).expanduser().resolve()

    archive_dir = repo.joinpath(
        ".herd",
        *TASK_ARCHIVE_PARTS,
    )

    path = archive_dir / f"{task_id}.json"

    try:
        status = os.lstat(
            path
        )
    except OSError:
        return {
            "ok": False,
            "reason": "no archived record of the task",
        }

    if not stat.S_ISREG(
        status.st_mode
    ):
        return {
            "ok": False,
            "reason": "the archived record is not a plain file",
        }

    resolved = path.resolve()

    # Both sides resolve, so the parent comparison alone proves only that the file sits directly in WHATEVER the archive directory
    # resolves to. If `.herd` (or any ancestor of the archive) is a link pointing out of the child, both follow it to the same
    # place. Containment of the resolved path in the child's own repository is what says WHERE it resolved to.
    if (
        resolved.parent != archive_dir.resolve()
        or not resolved.is_relative_to(
            repo
        )
    ):
        return {
            "ok": False,
            "reason": "the archived record is outside the child's own archive",
        }

    try:
        raw = path.read_bytes()
        record = json.loads(
            raw.decode("utf-8")
        )
    except Exception:
        return {
            "ok": False,
            "reason": "the archived record is unreadable",
        }

    if not isinstance(record, dict):
        return {
            "ok": False,
            "reason": "the archived record is not an object",
        }

    if record.get("id") != task_id:
        return {
            "ok": False,
            "reason": "the archived record is for another task id",
        }

    if record.get("status") != "COMPLETE":
        return {
            "ok": False,
            "reason": "the archived record is not COMPLETE",
        }

    completed_at = record.get(
        "completed_at"
    )

    if (
        isinstance(completed_at, bool)
        or not isinstance(completed_at, int)
        or completed_at <= 0
    ):
        return {
            "ok": False,
            "reason": "the archived record has no real completion time",
        }

    named = [
        value
        for value in (
            record.get("repo"),
            record.get("repo_ref"),
        )
        if value is not None
    ]

    if not named:
        return {
            "ok": False,
            "reason": "the archived record names no repository",
        }

    for value in named:
        if (
            not isinstance(value, str)
            or not value.strip()
            or Path(value).expanduser().resolve() != repo
        ):
            return {
                "ok": False,
                "reason": "the archived record names a different repository",
            }

    return {
        "ok": True,
        "path": str(path),
        "sha256": hashlib.sha256(
            raw
        ).hexdigest(),
        "completed_at": completed_at,
    }


#: A durable record of WHICH children each parent task has registered: their identities, (repository, task id). WHY: the gate used to read
#: an absent `children.json` as "no children", which is right for a parent that never had one and WRONG for a registry that was deleted
#: or lost records after they were written. The registry cannot testify to its own loss, so the writers (`spawn_child` and the follow-on
#: dispatch) record who was registered in a second file and the gate checks that EVERY registered identity is still in the registry.
#:
#: IDENTITIES, NOT A COUNT. An earlier form kept a high-water COUNT. A count is fungible: with two registered, a lost registry followed by
#: two ordinary spawns restores the count while the original children are gone, and the parent could complete. Replacement children
#: cannot satisfy an identity they do not have.
#:
#: A RESERVATION (`task_id` null) is an identity written BEFORE a child is launched, when its task id is not yet known: it is satisfied by
#: any registry record of that repository beyond those already accounted for, so work launched and never recorded is a loss that blocks,
#: not a warning.
#:
#: An absence is only "no children" when nothing says there were any; an absence the anchor contradicts is LOSS, and loss blocks.
#: Residual, stated: deleting BOTH files defeats it, and protection starts at the first registration after the anchor exists.
ANCHOR_FILE = "children.anchor.json"
ANCHOR_VERSION = 2

#: (resolved repository path, task id or None for a reservation)
Identity = tuple


def normal_repo(
    value: object,
) -> str | None:
    if isinstance(
        value,
        os.PathLike,
    ):
        value = os.fspath(
            value
        )

    if not isinstance(
        value,
        str,
    ) or not value:
        return None

    try:
        return str(
            Path(
                value
            ).expanduser().resolve()
        )
    except (
        OSError,
        RuntimeError,
    ):
        return None


def registration_identity(
    repo: object,
    task_id: object,
) -> Identity | None:
    """An identity, or None when either part is unusable (a record with no repository cannot be accounted for by repository)."""

    normalized = normal_repo(
        repo
    )

    if normalized is None:
        return None

    if task_id is None:
        return (
            normalized,
            None,
        )

    if isinstance(
        task_id,
        str,
    ) and task_id:
        return (
            normalized,
            task_id,
        )

    return None


def identities_of(
    records: list,
    parent_task_id: str,
) -> list[Identity]:
    """Every (repository, task id) the registry holds for ``parent_task_id``, in order, without duplicates."""

    found: list[Identity] = []

    for record in records:
        if (
            isinstance(
                record,
                dict,
            )
            and record.get("parent_task_id") == parent_task_id
        ):
            mine = registration_identity(
                record.get(
                    "repo"
                ),
                record.get(
                    "task_id"
                ),
            )

            if mine is not None and mine[1] is not None and mine not in found:
                found.append(
                    mine
                )

    return found


def anchor_path(
    herd: HerdrInstance,
) -> Path:
    return (
        herd.herd_root
        / "state"
        / ANCHOR_FILE
    )


def read_registration_anchor(
    herd: HerdrInstance,
) -> dict[str, list[Identity]]:
    """The anchor: ``{parent_task_id: [identities]}``. Absent is ``{}`` (nothing registered since the anchor existed); unreadable or
    not an anchor document RAISES (fail closed, like the registry)."""

    path = anchor_path(
        herd
    )

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError(
            f"Child registration anchor is unreadable: {path}"
        ) from exc

    try:
        document = json.loads(
            raw.decode("utf-8")
        )
    except Exception:
        raise RuntimeError(
            f"Child registration anchor is unreadable: {path}"
        )

    if isinstance(
        document,
        dict,
    ) and document.get("version") == 1:
        raise RuntimeError(
            f"Child registration anchor is a count-era (version 1) anchor: {path}. It holds a number, not the identities of the "
            "children, and a number cannot be turned into evidence it never carried, so it is refused rather than reinterpreted. "
            "If the child registry holds every child registered for this task, a person removes "
            f"{ANCHOR_FILE} from that state directory; the next registration then records the identities the registry holds."
        )

    registered = (
        document.get("registered")
        if isinstance(
            document,
            dict,
        )
        and document.get("version") == ANCHOR_VERSION
        else None
    )

    result: dict[str, list[Identity]] = {}

    valid = isinstance(
        registered,
        dict,
    )

    if valid:
        for key, entries in registered.items():
            if not isinstance(
                key,
                str,
            ) or not key or not isinstance(
                entries,
                list,
            ):
                valid = False
                break

            parsed: list[Identity] = []

            for entry in entries:
                if (
                    not isinstance(
                        entry,
                        dict,
                    )
                    or set(entry) != {"repo", "task_id"}
                    or not isinstance(
                        entry["repo"],
                        str,
                    )
                    or not entry["repo"]
                    or not (
                        entry["task_id"] is None
                        or (
                            isinstance(
                                entry["task_id"],
                                str,
                            )
                            and entry["task_id"]
                        )
                    )
                ):
                    valid = False
                    break

                mine = (
                    entry["repo"],
                    entry["task_id"],
                )

                if mine not in parsed:
                    parsed.append(
                        mine
                    )

            if not valid:
                break

            result[key] = parsed

    if not valid:
        raise RuntimeError(
            f"Child registration anchor is not a valid anchor document: {path}"
        )

    return result


def _write_registration_anchor(
    herd: HerdrInstance,
    registered: dict[str, list[Identity]],
) -> None:
    """Atomically replace the anchor (temporary file, fsync, ``os.replace``): a reader sees the old whole file or the new whole one.
    An anchor holding nothing is no anchor: the file is removed, so an undo leaves the directory exactly as it was found."""

    path = anchor_path(
        herd
    )

    registered = {
        key: entries
        for key, entries in registered.items()
        if entries
    }

    if not registered:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        return

    data = (
        json.dumps(
            {
                "version": ANCHOR_VERSION,
                "registered": {
                    key: [
                        {
                            "repo": repo,
                            "task_id": task_id,
                        }
                        for repo, task_id in entries
                    ]
                    for key, entries in sorted(
                        registered.items()
                    )
                },
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    temporary = path.with_name(
        f"{path.name}.tmp-{os.getpid()}"
    )

    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(
            fd,
            "wb",
        ) as handle:
            handle.write(
                data
            )
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temporary,
            path,
        )
    except BaseException:
        try:
            os.unlink(
                temporary
            )
        except OSError:
            pass

        raise


def register_identities(
    herd: HerdrInstance,
    parent_task_id: str,
    identities: list[Identity],
) -> None:
    """Record that these identities are registered for ``parent_task_id``. A UNION: it never removes one, so a writer that replaced a
    damaged registry by a smaller one cannot erase the evidence of what was lost."""

    if any(
        mine is None
        or len(mine) != 2
        for mine in identities
    ):
        raise ValueError(
            "An unusable identity cannot be registered."
        )

    current = read_registration_anchor(
        herd
    )

    held = list(
        current.get(
            parent_task_id,
            [],
        )
    )

    added = False

    for mine in identities:
        if mine not in held:
            held.append(
                mine
            )
            added = True

    if added:
        current[parent_task_id] = held

        _write_registration_anchor(
            herd,
            current,
        )


def set_registration_anchor(
    herd: HerdrInstance,
    parent_task_id: str,
    identities: list[Identity],
) -> None:
    """Set this task's identities EXACTLY. Used only by the writer that is undoing its own registration (it restores what it found,
    BEFORE it removes the record, so a failure leaves the anchor holding less than the registry, never more)."""

    current = read_registration_anchor(
        herd
    )

    if current.get(parent_task_id, []) == list(identities):
        return

    current[parent_task_id] = list(
        identities
    )

    _write_registration_anchor(
        herd,
        current,
    )


def replace_registration(
    herd: HerdrInstance,
    parent_task_id: str,
    old: Identity,
    new: Identity,
) -> None:
    """Finalize a reservation: swap ``old`` (a reservation) for the concrete ``new`` in one atomic write."""

    current = read_registration_anchor(
        herd
    )

    held = [
        mine
        for mine in current.get(
            parent_task_id,
            [],
        )
        if mine != old
    ]

    if new not in held:
        held.append(
            new
        )

    current[parent_task_id] = held

    _write_registration_anchor(
        herd,
        current,
    )


def missing_registrations(
    anchored: list[Identity],
    records: list,
    parent_task_id: str,
) -> list[str]:
    """What the anchor says was registered and the registry no longer shows, in words. Empty means the registry accounts for all of it.

    A concrete identity must be present exactly. And for every repository, the registry must hold at least as many records as the anchor
    holds identities (this is what an unfulfilled RESERVATION, or a lost record whose replacement is in the same repository, looks like)."""

    present: list[Identity] = []

    for record in records:
        if (
            isinstance(
                record,
                dict,
            )
            and record.get("parent_task_id") == parent_task_id
        ):
            normalized = normal_repo(
                record.get(
                    "repo"
                )
            )

            if normalized is not None:
                tid = record.get(
                    "task_id"
                )

                present.append(
                    (
                        normalized,
                        tid if isinstance(tid, str) and tid else None,
                    )
                )

    present_pairs = set(
        present
    )

    missing: list[str] = []

    concrete_missing_repos = set()

    for repo, task_id in anchored:
        if task_id is not None and (repo, task_id) not in present_pairs:
            missing.append(
                f"{repo} task={task_id}"
            )
            concrete_missing_repos.add(
                repo
            )

    for repo in sorted(
        {repo for repo, _ in anchored}
    ):
        wanted = sum(
            1
            for mine in anchored
            if mine[0] == repo
        )

        have = sum(
            1
            for mine in present
            if mine[0] == repo
        )

        if have < wanted and repo not in concrete_missing_repos:
            missing.append(
                f"{repo} ({wanted - have} registration(s) reserved or recorded for it are not in the registry. A reservation is written BEFORE a "
                "child is launched, so work may have begun there. Clearing it would need positive evidence that it NEVER started (for example a "
                "recorded refusal raised before the herd was started); work that DID start stays blocking until its required outcome is resolved "
                "and durably tracked; anything ambiguous stays blocking. 'Nothing is running now' is not such evidence: stopped or ERROR work can "
                f"still be required and unresolved. Do not edit children.json or {ANCHOR_FILE} by hand; clearing needs a supervised correction)"
            )

    return missing


def child_dependencies(
    herd: HerdrInstance,
    parent_task_id: str | None = None,
) -> list[dict]:
    """Return current child dependency state for one parent task.

    Includes a ``REGISTRY_LOSS`` entry when the registration anchor records more registrations for this task than the registry
    now holds (the registry was deleted or lost records): that entry is unresolved, so the parent cannot complete over a registry
    that no longer shows what was registered."""

    if parent_task_id is None:
        task = _parent_task(
            herd
        )

        if task.get("status") != "ACTIVE":
            return []

        parent_task_id = task.get(
            "id"
        )

    if not parent_task_id:
        return []

    path = (
        herd.herd_root
        / "state"
        / "children.json"
    )

    # Read FIRST and fail closed if it cannot be read: an unreadable anchor says nothing about what was registered.
    anchored = read_registration_anchor(
        herd
    ).get(
        parent_task_id,
        [],
    )

    if not path.exists():
        data = {
            "children": [],
        }
    else:
        try:
            data = json.loads(
                path.read_text()
            )
        except Exception:
            raise RuntimeError(
                f"Child dependency state is unreadable: {path}"
            )

    dependencies = []

    lost = missing_registrations(
        anchored,
        data.get(
            "children",
            [],
        ),
        parent_task_id,
    )

    if lost:
        shown = "; ".join(
            lost[:4]
        ) + (
            f"; and {len(lost) - 4} more"
            if len(lost) > 4
            else ""
        )

        dependencies.append(
            {
                "parent_task_id": parent_task_id,
                "repo": "(child registry)",
                "task_id": "(registry loss)",
                "current_status": "REGISTRY_LOSS",
                "dependency_error": (
                    "The child registry no longer accounts for what was registered "
                    f"for this task: {shown}. Records were lost or replaced, or the "
                    "registry was deleted. Completion stays blocked. Restoring the registry, or correcting the anchor, needs a supervised "
                    f"correction. Do not edit children.json or {ANCHOR_FILE} by hand, and never remove an identity from the anchor to unblock a parent."
                ),
            }
        )

    for record in data.get(
        "children",
        [],
    ):
        if (
            record.get("parent_task_id")
            != parent_task_id
        ):
            continue

        current = dict(
            record
        )

        if record.get("dispatch_state") == "PENDING":
            # A follow-on dispatch recorded its intent and never finalized it:
            # whether the child accepted the task is not established, so the
            # parent cannot complete over it.
            current["current_status"] = "PENDING"
            current["dependency_error"] = (
                "A follow-on dispatch was recorded and "
                "never finalized."
            )
            dependencies.append(
                current
            )
            continue

        if record.get("dispatch_state") == "RECOVERY":
            # A follow-on dispatch LOST its own registry record and wrote this one so that the parent stays blocked.
            # It is never resolved by what the child's task.json says: a person decides.
            current["current_status"] = "RECOVERY"
            current["dependency_error"] = (
                "A follow-on dispatch lost its registry record; this "
                "recovery record keeps the parent blocked until a person "
                "resolves it."
            )
            dependencies.append(
                current
            )
            continue

        repo_raw = record.get(
            "repo"
        )

        child_task_id = record.get(
            "task_id"
        )

        if not repo_raw:
            current["current_status"] = "MISSING"
            current["dependency_error"] = (
                "Child record has no repository."
            )
            dependencies.append(
                current
            )
            continue

        child_repo = Path(
            repo_raw
        ).expanduser().resolve()

        child_task_path = (
            child_repo
            / ".herd"
            / "state"
            / "task.json"
        )

        if not child_task_path.exists():
            current["current_status"] = "MISSING"
            current["dependency_error"] = (
                "Child task state does not exist."
            )
            dependencies.append(
                current
            )
            continue

        try:
            child_task = json.loads(
                child_task_path.read_text()
            )
        except Exception:
            current["current_status"] = "UNREADABLE"
            current["dependency_error"] = (
                "Child task state is unreadable."
            )
            dependencies.append(
                current
            )
            continue

        current_task_id = child_task.get(
            "id"
        )

        current["current_task_id"] = (
            current_task_id
        )

        if (
            child_task_id
            and current_task_id
            != child_task_id
        ):
            # The child now reports a different task. That is a fault ONLY if
            # the recorded task is not accounted for: a task the child itself
            # recorded as COMPLETE stays resolved from that record. The
            # comparison is not relaxed; without the evidence it is MISMATCH.
            evidence = durable_complete_evidence(
                child_repo,
                child_task_id,
            )

            if evidence["ok"]:
                current["current_status"] = "COMPLETE"
                current["resolved_from"] = "durable_evidence"
                current["evidence"] = {
                    "path": evidence["path"],
                    "sha256": evidence["sha256"],
                    "completed_at": evidence["completed_at"],
                }
            else:
                current["current_status"] = "MISMATCH"
                current["dependency_error"] = (
                    "Child is no longer reporting "
                    "the task recorded by the parent. "
                    "No durable COMPLETE evidence for it: "
                    + evidence["reason"]
                    + "."
                )
        else:
            current["current_status"] = str(
                child_task.get(
                    "status",
                    "UNKNOWN",
                )
            ).upper()

        dependencies.append(
            current
        )

    return dependencies


def unresolved_child_dependencies(
    herd: HerdrInstance,
    parent_task_id: str | None = None,
) -> list[dict]:
    return [
        dependency
        for dependency
        in child_dependencies(
            herd,
            parent_task_id,
        )
        if dependency.get(
            "current_status"
        )
        != "COMPLETE"
    ]


def assert_child_dependencies_complete(
    herd: HerdrInstance,
    parent_task_id: str,
) -> None:
    """Fail closed while any child dependency is unresolved."""

    unresolved = unresolved_child_dependencies(
        herd,
        parent_task_id,
    )

    if not unresolved:
        return

    rows = []

    for dependency in unresolved:
        rows.append(
            "- "
            + str(
                dependency.get(
                    "repo",
                    "(unknown repo)",
                )
            )
            + " | task="
            + str(
                dependency.get(
                    "task_id",
                    "(unknown)",
                )
            )
            + " | status="
            + str(
                dependency.get(
                    "current_status",
                    "UNKNOWN",
                )
            )
        )

    raise RuntimeError(
        "Parent task cannot complete while child Herdr "
        "dependencies are unresolved:\n"
        + "\n".join(rows)
    )
