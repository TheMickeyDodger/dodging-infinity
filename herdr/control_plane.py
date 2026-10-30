"""Programmatic control plane for Herdr."""

from __future__ import annotations

import copy
import json
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
from .follow_on import (
    FollowOnRefused,
    admit_spawn_registration,
    finalize_spawn_registration,
    follow_on_dispatch,
    registry_lock,
    parent_active_task_id,
)
from .initialize import initialize_herd
from .instance import HerdrInstance
from .lifecycle import start_herd
from .policy import HerdrPolicy
from .tasks import dispatch_task


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

        # The registry lock is taken BEFORE the child is launched and held until its record is written. A lock that cannot be
        # had is refused here, while nothing has started, so a contended registry can never leave a running child with no
        # dependency (the order that used to be: launch, then lock, then record).
        with registry_lock(
            parent,
            wait_seconds=self.REGISTRY_WAIT_SECONDS,
        ):
            # The parent's ACTIVE task, or None when it has none. An unreadable, contradictory or unknown task state REFUSES here, by
            # name, before anything is launched: it used to be read as {} (no task), which registered the child with no parent task id
            # (orphaned from its parent, and skipped by completion once the parent's ACTIVE state returned). See parent_active_task_id.
            parent_task_id = parent_active_task_id(
                parent
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

            # BEFORE anything starts: refuse a registry that cannot be trusted (unreadable, or no longer accounting for what was
            # registered) and make the registration durable by RESERVING this child's repository. A child launched on top of a damaged
            # registry could otherwise "restore" what was lost with replacements, and a child launched and never recorded would have no
            # durable mark.
            reservation = admit_spawn_registration(
                parent,
                parent_task_id,
                target,
            )

            try:
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
            except BaseException as launch_error:
                # THE RESERVATION IS NOT WITHDRAWN. An exception is not evidence that nothing started: `spawn` initializes, starts the
                # herd and only then dispatches, and `dispatch_task` saves the child's task and prompts the Supervisor before it can fail
                # (on a failed prompt it saves the task as ERROR and RAISES), so the signal can arrive after the effect, with no registry
                # record yet. Starting a herd leaves no file in the child until it has finished, so a failed launch cannot establish
                # that nothing started. Withdrawal is allowed only when that is positively established; here it cannot be, so the
                # reservation stays, the real completion command refuses because of it, and a person clears it after checking the child.
                if reservation is not None:
                    print(
                        f"NOTE: the launch into {target} raised {type(launch_error).__name__}: {launch_error}; an exception is not evidence that "
                        f"nothing started, so the reservation for {target} in the registration anchor stays and blocks the parent's completion. "
                        "It is not withdrawn by this process and must not be edited away by hand: if the work never started, a supervised correction "
                        "can clear it on positive evidence of that; if it started, the block stays until its required outcome is resolved and durably tracked."
                    )

                raise

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

            self._append_child_record(
                parent,
                record,
            )

            if reservation is not None:
                problem = finalize_spawn_registration(
                    parent,
                    parent_task_id,
                    reservation,
                    record,
                )

                if problem is not None:
                    # Said where progress is said (the result's shape is a pinned contract of its consumers). This is NOT the only
                    # protection: the reservation written before the launch stays, is satisfied by the record just written, and
                    # would block if that record were lost.
                    print(
                        "NOTE: the child is registered; swapping its reservation for its identity in the registration anchor failed ("
                        + problem
                        + "). The reservation written before the launch still protects the registry."
                    )

            result["parent_repo"] = str(
                parent.repo
            )

            result["child_record"] = record

            return result

    #: How long a spawn (or a completion) waits for the registry lock before it is refused. For a spawn the wait is spent
    #: BEFORE the child is launched.
    REGISTRY_WAIT_SECONDS = 30.0

    def _append_child_record(
        self,
        parent: HerdrInstance,
        record: dict[str, Any],
    ) -> None:
        """Append `record` to the parent's child registry. THE CALLER HOLDS `registry_lock(parent)` and has already admitted the
        registration (`admit_spawn_registration`), so an unreadable registry never reaches here. If one does (a writer that took no
        lock changed it since), it is REFUSED, never replaced by an empty one: the reservation made before the launch then blocks the
        parent, because the record this call could not write is unaccounted for.
        """

        children_path = (
            parent.herd_root
            / "state"
            / "children.json"
        )

        if children_path.exists():
            try:
                children = json.loads(
                    children_path.read_text()
                )
            except Exception:
                raise FollowOnRefused(
                    "registry_unreadable",
                    f"The parent's child registry became unreadable after the child was launched; it was not replaced: {children_path}",
                )

            if not isinstance(
                children,
                dict,
            ) or not isinstance(
                children.get(
                    "children"
                ),
                list,
            ):
                raise FollowOnRefused(
                    "registry_invalid",
                    f"The parent's child registry stopped being a registry after the child was launched; it was not replaced: {children_path}",
                )
        else:
            children = {
                "version": 1,
                "children": [],
            }

        children["children"].append(
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

    def follow_on_child(
        self,
        parent_repo: str | Path,
        target_repo: str | Path,
        *,
        task: str,
        rules: list[str] | None = None,
        task_policy: dict[str, Any] | None = None,
        expected_task_id: str | None = None,
    ) -> dict:
        """Dispatch a SECOND task to an existing, completed, healthy child.

        Reuses the child's live herd: no start, no force, no workspace close,
        no session or context reset, no configuration change. Refuses naming
        the failed check; see `herdr.follow_on`.
        """

        return follow_on_dispatch(
            self.instance(parent_repo),
            target_repo,
            task=task,
            rules=rules,
            task_policy=task_policy,
            expected_task_id=expected_task_id,
        )

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
