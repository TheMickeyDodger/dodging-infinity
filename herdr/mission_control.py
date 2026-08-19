"""Serializable state projection for Dodging Infinity Mission Control."""

from __future__ import annotations

import json
import time

from pathlib import Path
from typing import Any

from .instance import HerdrInstance
from .runtime import agent_info


SNAPSHOT_VERSION = 1


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def repository_snapshot(herd: HerdrInstance) -> dict[str, Any]:
    """Return a JSON-serializable Mission Control view of one Herdr."""
    config = _read_json(herd.config_path, {}) if herd.initialized else {}
    state_root = herd.herd_root / "state"

    runtime = _read_json(state_root / "runtime.json", {})
    task = _read_json(state_root / "task.json", None)
    children_state = _read_json(
        state_root / "children.json",
        {"version": 1, "children": []},
    )

    agents = []
    for logical_name, agent_name in sorted(runtime.get("agents", {}).items()):
        info = agent_info(agent_name)
        agents.append(
            {
                "logical_name": logical_name,
                "agent": agent_name,
                "status": info.get("status", "unknown"),
            }
        )

    children = []
    for child in children_state.get("children", []):
        record = dict(child)
        child_repo = record.get("repo")
        current_status = None

        if child_repo:
            child_task = _read_json(
                Path(child_repo) / ".herd" / "state" / "task.json",
                {},
            )
            current_status = child_task.get("status")

        record["current_status"] = (
            current_status
            or record.get("task_status")
            or "UNKNOWN"
        )
        children.append(record)

    live_agents = [
        item
        for item in agents
        if item["status"] != "missing"
    ]

    runtime_status = (
        "RUNNING"
        if live_agents
        else ("UNAVAILABLE" if runtime else "STOPPED")
    )

    project = config.get("project", {})

    return {
        "schema_version": SNAPSHOT_VERSION,
        "generated_at": int(time.time()),
        "repo": {
            "path": str(herd.repo),
            "name": project.get("name", herd.repo.name),
            "initialized": herd.initialized,
        },
        "runtime": {
            "status": runtime_status,
            "workspace_id": runtime.get("workspace_id"),
            "agents": agents,
            "panes": runtime.get("panes", {}),
        },
        "task": task,
        "children": children,
        "policy": config.get("policy", {}),
    }
