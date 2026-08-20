"""Repository registry used by Herdr."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


REGISTRY = (
    Path.home()
    / ".config"
    / "dodging-infinity"
    / "repos.json"
)

LEGACY_REGISTRY = (
    Path.home()
    / ".config"
    / "herd-harness"
    / "repos.json"
)


def registry_load() -> dict:
    source = REGISTRY
    migrating_legacy = False

    if not source.exists() and LEGACY_REGISTRY.exists():
        source = LEGACY_REGISTRY
        migrating_legacy = True

    if not source.exists():
        return {
            "version": 1,
            "repos": {},
        }

    try:
        data = json.loads(
            source.read_text()
        )
    except Exception:
        return {
            "version": 1,
            "repos": {},
        }

    if migrating_legacy:
        registry_save(data)

    return data


def registry_save(
    data: dict,
) -> None:
    REGISTRY.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REGISTRY.write_text(
        json.dumps(
            data,
            indent=2,
        )
        + "\n"
    )


def register_repo(
    repo: str | Path,
    alias: str | None = None,
) -> str:
    repo = Path(
        repo
    ).resolve()

    data = registry_load()
    repos = data.setdefault(
        "repos",
        {},
    )

    base = (
        alias
        or repo.name
    ).strip().lower()

    base = re.sub(
        r"[^a-z0-9._-]+",
        "-",
        base,
    ).strip("-") or "repo"

    if (
        base in repos
        and Path(
            repos[base]["path"]
        ).resolve()
        != repo
    ):
        if alias:
            raise RuntimeError(
                f"Alias `{base}` already points to "
                f"{repos[base]['path']}"
            )

        number = 2

        while (
            f"{base}-{number}"
            in repos
        ):
            number += 1

        base = (
            f"{base}-{number}"
        )

    repos[base] = {
        "path": str(repo),
        "registered_at": int(
            time.time()
        ),
    }

    registry_save(
        data
    )

    return base


def unregister_repo(
    repo: str | Path,
) -> None:
    repo = Path(repo).resolve()

    data = registry_load()
    repos = data.setdefault(
        "repos",
        {},
    )

    remove = [
        alias
        for alias, value in repos.items()
        if Path(value["path"]).resolve() == repo
    ]

    for alias in remove:
        del repos[alias]

    registry_save(
        data
    )
