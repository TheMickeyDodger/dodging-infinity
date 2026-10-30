"""Structured bridge between a Supervisor and the Herdr Control Plane."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

from .control_plane import HerdrControlPlane


_ALLOWED_FIELDS = {
    "target_repo",
    "task",
    "preset",
    "test_command",
    "alias",
    "rules",
    "policy",
    "task_policy",
    "force",
    "rejection_drill",
}


# A follow-on reuses an existing, completed child. It carries no `force`, no
# `preset`/`test_command`/`alias`/`policy` (the child is not reconfigured) and no
# `rejection_drill`; rules and `task_policy` ride on the TASK only, and a
# `task_policy` may carry rules and nothing else (see herdr.follow_on).
_FOLLOW_ON_FIELDS = {
    "target_repo",
    "task",
    "rules",
    "task_policy",
    "expected_task_id",
}


def _validate_follow_on_request(
    request: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError(
            "Follow-on request must be a JSON object."
        )

    unknown = set(request) - _FOLLOW_ON_FIELDS

    if unknown:
        raise ValueError(
            "Unknown or refused follow-on request field(s): "
            + ", ".join(sorted(unknown))
            + ". A follow-on never forces, restarts or reconfigures "
            "the child."
        )

    target_raw = request.get("target_repo")

    if (
        not isinstance(target_raw, str)
        or not target_raw.strip()
    ):
        raise ValueError(
            "`target_repo` is required."
        )

    target = Path(
        target_raw
    ).expanduser()

    if not target.is_absolute():
        raise ValueError(
            "`target_repo` must be an absolute path."
        )

    task = request.get("task")

    if (
        not isinstance(task, str)
        or not task.strip()
    ):
        raise ValueError(
            "`task` is required."
        )

    rules = request.get("rules")

    if rules is not None:
        if not isinstance(rules, list):
            raise ValueError(
                "`rules` must be a list of strings."
            )

        for rule in rules:
            if (
                not isinstance(rule, str)
                or not rule.strip()
            ):
                raise ValueError(
                    "`rules` must contain only non-empty strings."
                )

    task_policy = request.get("task_policy")

    if (
        task_policy is not None
        and not isinstance(task_policy, dict)
    ):
        raise ValueError(
            "`task_policy` must be an object."
        )

    expected = request.get("expected_task_id")

    if (
        expected is not None
        and (
            not isinstance(expected, str)
            or not expected.strip()
        )
    ):
        raise ValueError(
            "`expected_task_id` must be a non-empty string."
        )

    clean = dict(request)
    clean["target_repo"] = str(
        target.resolve()
    )
    clean["task"] = task.strip()

    if rules is not None:
        clean["rules"] = [
            rule.strip()
            for rule in rules
        ]

    return clean


def execute_follow_on_request(
    parent_repo: str | Path,
    request: dict[str, Any],
    *,
    control_plane: HerdrControlPlane | None = None,
) -> dict:
    """Execute one validated follow-on request against an existing child."""

    parent = Path(
        parent_repo
    ).expanduser().resolve()

    clean = _validate_follow_on_request(
        request
    )

    cp = (
        control_plane
        or HerdrControlPlane()
    )

    target_repo = clean.pop(
        "target_repo"
    )

    return cp.follow_on_child(
        parent,
        target_repo,
        **clean,
    )


def _validate_request(
    request: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError(
            "Spawn request must be a JSON object."
        )

    unknown = set(request) - _ALLOWED_FIELDS

    if unknown:
        raise ValueError(
            "Unknown spawn request field(s): "
            + ", ".join(sorted(unknown))
        )

    target_raw = request.get("target_repo")

    if (
        not isinstance(target_raw, str)
        or not target_raw.strip()
    ):
        raise ValueError(
            "`target_repo` is required."
        )

    target = Path(
        target_raw
    ).expanduser()

    if not target.is_absolute():
        raise ValueError(
            "`target_repo` must be an absolute path."
        )

    task = request.get("task")

    if (
        not isinstance(task, str)
        or not task.strip()
    ):
        raise ValueError(
            "`task` is required."
        )

    rules = request.get("rules")

    if rules is not None:
        if not isinstance(rules, list):
            raise ValueError(
                "`rules` must be a list of strings."
            )

        for rule in rules:
            if (
                not isinstance(rule, str)
                or not rule.strip()
            ):
                raise ValueError(
                    "`rules` must contain only non-empty strings."
                )

    for field in [
        "policy",
        "task_policy",
    ]:
        value = request.get(field)

        if (
            value is not None
            and not isinstance(value, dict)
        ):
            raise ValueError(
                f"`{field}` must be an object."
            )

    for field in [
        "force",
        "rejection_drill",
    ]:
        value = request.get(field)

        if (
            value is not None
            and not isinstance(value, bool)
        ):
            raise ValueError(
                f"`{field}` must be true or false."
            )

    clean = dict(request)
    clean["target_repo"] = str(
        target.resolve()
    )
    clean["task"] = task.strip()

    if rules is not None:
        clean["rules"] = [
            rule.strip()
            for rule in rules
        ]

    return clean


def execute_spawn_request(
    parent_repo: str | Path,
    request: dict[str, Any],
    *,
    control_plane: HerdrControlPlane | None = None,
) -> dict:
    """Execute one validated child-Herdr spawn request."""

    parent = Path(
        parent_repo
    ).expanduser().resolve()

    clean = _validate_request(
        request
    )

    cp = (
        control_plane
        or HerdrControlPlane()
    )

    target_repo = clean.pop(
        "target_repo"
    )

    return cp.spawn_child(
        parent,
        target_repo,
        **clean,
    )


def _load_request_file(
    parent_repo: str | Path,
    request_file: str | Path,
    label: str = "Spawn request",
) -> tuple[Path, dict]:
    """The parent and the parsed request, for a file inside the parent Herdr state directory."""

    parent = Path(
        parent_repo
    ).expanduser().resolve()

    path = Path(
        request_file
    ).expanduser()

    if not path.is_absolute():
        path = (
            parent
            / path
        ).resolve()
    else:
        path = path.resolve()

    allowed_root = (
        parent
        / ".herd"
        / "state"
    ).resolve()

    try:
        path.relative_to(
            allowed_root
        )
    except ValueError:
        raise ValueError(
            f"{label} files must live inside "
            f"{allowed_root}."
        )

    if not path.exists():
        raise ValueError(
            f"{label} file not found: {path}"
        )

    try:
        request = json.loads(
            path.read_text()
        )
    except Exception as exc:
        raise ValueError(
            f"{label} is not valid JSON: {exc}"
        )

    return parent, request


def execute_spawn_request_file(
    parent_repo: str | Path,
    request_file: str | Path,
    *,
    control_plane: HerdrControlPlane | None = None,
) -> dict:
    """Load a structured request from the parent Herdr state directory."""

    parent, request = _load_request_file(
        parent_repo,
        request_file,
    )

    return execute_spawn_request(
        parent,
        request,
        control_plane=control_plane,
    )


def execute_follow_on_request_file(
    parent_repo: str | Path,
    request_file: str | Path,
    *,
    control_plane: HerdrControlPlane | None = None,
) -> dict:
    """Load a follow-on request from the parent Herdr state directory and execute it."""

    parent, request = _load_request_file(
        parent_repo,
        request_file,
        "Follow-on request",
    )

    return execute_follow_on_request(
        parent,
        request,
        control_plane=control_plane,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="herdr-orchestrator"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    spawn = subparsers.add_parser(
        "spawn"
    )

    spawn.add_argument(
        "--parent",
        required=True,
    )

    spawn.add_argument(
        "--request-file",
        required=True,
    )

    follow_on = subparsers.add_parser(
        "follow-on"
    )

    follow_on.add_argument(
        "--parent",
        required=True,
    )

    follow_on.add_argument(
        "--request-file",
        required=True,
    )

    args = parser.parse_args()

    runner = (
        execute_follow_on_request_file
        if args.command == "follow-on"
        else execute_spawn_request_file
    )

    progress = io.StringIO()

    try:
        with contextlib.redirect_stdout(
            progress
        ):
            result = runner(
                args.parent,
                args.request_file,
            )
    except (
        RuntimeError,
        ValueError,
    ) as exc:
        captured = progress.getvalue()

        if captured:
            print(
                captured,
                file=sys.stderr,
                end="",
            )

        raise SystemExit(
            str(exc)
        )

    captured = progress.getvalue()

    if captured:
        print(
            captured,
            file=sys.stderr,
            end="",
        )

    print(
        json.dumps(
            result,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
