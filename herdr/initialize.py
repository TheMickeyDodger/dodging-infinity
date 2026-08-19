"""Initialization of a repository as a Herdr instance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .config import (
    CFG,
    DEFAULT,
    ROLE_FILES,
    apply_preset_to_config,
)
from .guards import (
    gitout,
    install_git_guard,
)
from .instance import HerdrInstance
from .registry import register_repo


MEMORY_FILES = [
    "architecture.md",
    "conventions.md",
    "decisions.md",
    "mistakes.md",
    "task-history.md",
]


def local_exclude_path(
    repo: str | Path,
) -> Path:
    repo = Path(
        repo
    ).resolve()

    raw = gitout(
        repo,
        "rev-parse",
        "--git-path",
        "info/exclude",
        allow_fail=True,
    )

    if raw:
        path = Path(
            raw
        )

        if not path.is_absolute():
            path = (
                repo
                / path
            ).resolve()

        return path

    return (
        repo
        / ".git"
        / "info"
        / "exclude"
    )


def ensure_local_herd_exclude(
    repo: str | Path,
) -> Path:
    path = local_exclude_path(
        repo
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    old = (
        path.read_text()
        if path.exists()
        else ""
    )

    lines = {
        line.strip()
        for line
        in old.splitlines()
    }

    if ".herd/" not in lines:
        with path.open(
            "a"
        ) as handle:
            if (
                old
                and not old.endswith("\n")
            ):
                handle.write(
                    "\n"
                )

            handle.write(
                "\n"
                "# Local Herd harness\n"
                ".herd/\n"
            )

    return path


def _verify_git_repo(
    repo: Path,
) -> Path:
    root = gitout(
        repo,
        "rev-parse",
        "--show-toplevel",
        allow_fail=True,
    )

    if not root:
        raise RuntimeError(
            f"Not a Git repository: {repo}"
        )

    resolved = Path(
        root
    ).resolve()

    if resolved != repo:
        raise RuntimeError(
            f"Repository path must be the Git root. "
            f"Resolved root: {resolved}"
        )

    return resolved


def initialize_herd(
    repo: str | Path,
    *,
    preset: str | None = None,
    test_command: str | None = None,
    alias: str | None = None,
    policy: dict | None = None,
) -> dict:
    """Initialize or update Herdr metadata for one Git repository."""

    repo = Path(
        repo
    ).expanduser().resolve()

    _verify_git_repo(
        repo
    )

    herd = HerdrInstance(
        repo
    )

    for directory in [
        "roles",
        "memory",
        "state",
    ]:
        (
            herd.herd_root
            / directory
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

    package_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    for name in ROLE_FILES.values():
        destination = (
            herd.herd_root
            / "roles"
            / name
        )

        if not destination.exists():
            source = (
                package_root
                / "roles"
                / name
            )

            if not source.exists():
                raise RuntimeError(
                    f"Missing Herdr role template: {source}"
                )

            destination.write_text(
                source.read_text()
            )

    for name in MEMORY_FILES:
        destination = (
            herd.herd_root
            / "memory"
            / name
        )

        if destination.exists():
            continue

        source = (
            package_root
            / "memory"
            / name
        )

        destination.write_text(
            source.read_text()
            if source.exists()
            else f"# {name}\n"
        )

    config_path = (
        herd.herd_root
        / CFG
    )

    created = (
        not config_path.exists()
    )

    if created:
        config = copy.deepcopy(
            DEFAULT
        )

        config["project"]["name"] = (
            repo.name
        )

        apply_preset_to_config(
            config,
            preset,
        )

        if test_command is not None:
            config["project"]["test_command"] = (
                test_command
            )

        config_path.write_text(
            json.dumps(
                config,
                indent=2,
            )
            + "\n"
        )

    elif (
        preset is not None
        or test_command is not None
    ):
        config = json.loads(
            config_path.read_text()
        )

        apply_preset_to_config(
            config,
            preset,
        )

        if test_command is not None:
            config.setdefault(
                "project",
                {},
            )["test_command"] = (
                test_command
            )

        config_path.write_text(
            json.dumps(
                config,
                indent=2,
            )
            + "\n"
        )

    if policy:
        herd.merge_policy(
            policy
        )

    exclude = ensure_local_herd_exclude(
        repo
    )

    registered_alias = register_repo(
        repo,
        alias,
    )

    install_git_guard(
        repo
    )

    return {
        "repo": str(repo),
        "herd_root": str(
            herd.herd_root
        ),
        "config_path": str(
            config_path
        ),
        "created": created,
        "alias": registered_alias,
        "exclude_path": str(
            exclude
        ),
        "policy": (
            herd
            .effective_policy()
            .to_dict()
        ),
    }
