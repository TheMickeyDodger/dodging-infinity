"""Parent/child task dependency tracking for hierarchical Herdrs."""

from __future__ import annotations

import json
from pathlib import Path

from .instance import HerdrInstance


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


def child_dependencies(
    herd: HerdrInstance,
    parent_task_id: str | None = None,
) -> list[dict]:
    """Return current child dependency state for one parent task."""

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

    if not path.exists():
        return []

    try:
        data = json.loads(
            path.read_text()
        )
    except Exception:
        raise RuntimeError(
            f"Child dependency state is unreadable: {path}"
        )

    dependencies = []

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
            current["current_status"] = "MISMATCH"
            current["dependency_error"] = (
                "Child is no longer reporting "
                "the task recorded by the parent."
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
