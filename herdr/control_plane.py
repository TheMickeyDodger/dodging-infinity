"""Programmatic control plane for Herdr."""

from __future__ import annotations

import copy
import json
import time

from pathlib import Path
from typing import Any

from .events import append_event, read_events
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
from .mission_control import repository_snapshot
from .policy import HerdrPolicy
from .tasks import dispatch_task


class HerdrControlPlane:
    """Primary programmatic interface for managing Herdr instances.

    Human-facing clients such as `herdctl` and higher-level orchestrators
    should delegate to this object rather than owning Herdr behavior.
    """

    def instance(self, repo: str | Path) -> HerdrInstance:
        return HerdrInstance(repo)

    def snapshot(
        self,
        repo: str | Path,
    ) -> dict[str, Any]:
        """Return the canonical Mission Control snapshot for one Herdr."""
        return repository_snapshot(
            self.instance(repo)
        )

    def emit_event(
        self,
        repo: str | Path,
        event_type: str,
        *,
        actor: str = "control-plane",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one durable Mission Control event."""
        return append_event(
            self.instance(repo),
            event_type,
            actor=actor,
            data=data,
        )

    def events(
        self,
        repo: str | Path,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent Mission Control events in chronological order."""
        return read_events(
            self.instance(repo),
            limit=limit,
        )

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
        result = initialize_herd(
            repo,
            preset=preset,
            test_command=test_command,
            alias=alias,
            policy=policy,
        )

        self.emit_event(
            repo,
            "herd.initialized",
            data={
                "preset": preset,
                "alias": alias,
            },
        )

        return result

    def start(
        self,
        repo: str | Path,
        *,
        force: bool = False,
    ) -> dict:
        """Start a complete Herdr without invoking herdctl bootstrap."""
        result = start_herd(
            self.instance(repo),
            force=force,
        )

        self.emit_event(
            repo,
            "runtime.started",
            data={
                "workspace_id": result.get("workspace_id"),
                "agents": sorted(
                    result.get("agents", {}).keys()
                ),
            },
        )

        return result

    def dispatch_task(
        self,
        repo: str | Path,
        text: str,
        *,
        rejection_drill: bool = False,
        task_policy: dict[str, Any] | None = None,
    ) -> dict:
        """Dispatch a top-level task without invoking herdctl task."""
        result = dispatch_task(
            self.instance(repo),
            text,
            rejection_drill=rejection_drill,
            task_policy=task_policy,
        )

        self.emit_event(
            repo,
            "task.dispatched",
            data={
                "task_id": result.get("id"),
                "status": result.get("status"),
                "description": result.get("description", text),
            },
        )

        return result

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

        children_path = (
            parent.herd_root
            / "state"
            / "children.json"
        )

        try:
            children = (
                json.loads(
                    children_path.read_text()
                )
                if children_path.exists()
                else {
                    "version": 1,
                    "children": [],
                }
            )
        except Exception:
            children = {
                "version": 1,
                "children": [],
            }

        children.setdefault(
            "children",
            [],
        ).append(
            record
        )

        children_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        children_path.write_text(
            json.dumps(
                children,
                indent=2,
            )
            + "\n"
        )

        result["parent_repo"] = str(
            parent.repo
        )

        result["child_record"] = record

        self.emit_event(
            parent.repo,
            "child.spawned",
            data={
                "child_repo": record.get("repo"),
                "task_id": record.get("task_id"),
                "task_status": record.get("task_status"),
                "workspace_id": record.get("workspace_id"),
                "dependency": record.get("dependency"),
                "parent_task_id": record.get("parent_task_id"),
                "agents": sorted(
                    record.get("agents", {}).keys()
                ),
            },
        )

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
