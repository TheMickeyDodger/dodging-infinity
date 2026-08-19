"""Lifecycle operations for repository-scoped Herdr instances."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
import time
from pathlib import Path

from .heartbeat import heartbeat_process_command
from .instance import HerdrInstance
from .runtime import jrun, prompt, run, split, start_agent


ROLE_FILES = {
    "supervisor": "supervisor.md",
    "lead": "lead.md",
    "executor": "executor.md",
    "reviewer": "reviewer.md",
}


def _prefix(repo: Path) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        repo.name.lower(),
    ).strip("-")[:10]

    digest = hashlib.sha1(
        str(repo).encode()
    ).hexdigest()[:5]

    return f"h{digest}-{slug}"[:18].rstrip("-")


def _agent_name(prefix: str, short: str) -> str:
    return re.sub(
        r"[^a-z0-9_-]",
        "-",
        f"{prefix}-{short}".lower(),
    )[:32].rstrip("-_")


def _state_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / "runtime.json"


def _save_state(
    herd: HerdrInstance,
    state: dict,
) -> None:
    path = _state_path(herd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2) + "\n"
    )


def bootstrap_text(
    herd: HerdrInstance,
    logical: str,
    role_type: str,
    agents: dict[str, str],
    config: dict,
) -> str:
    """Build the initial persistent role prompt for an agent."""

    repo = herd.repo

    role = (
        herd.herd_root
        / "roles"
        / ROLE_FILES[role_type]
    ).read_text()

    topology = "\n".join(
        f"- `{key}` -> `{value}`"
        for key, value in sorted(agents.items())
    )

    test_command = (
        config["project"].get("test_command")
        or (
            "(not configured; inspect project and choose "
            "appropriate verification)"
        )
    )

    # Use the absolute repository path as the durable repo reference.
    # This removes lifecycle's dependency on herdctl's alias registry.
    repo_ref = str(repo)

    policy = herd.effective_policy()
    rules = policy.rules
    rules_text = (
        "\n".join(f"- {rule}" for rule in rules)
        if rules
        else "- No additional repository-specific rules."
    )

    completion = ""
    child_orchestration = ""

    if role_type == "supervisor":
        package_root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        spawn_command = (
            "env PYTHONPATH="
            + shlex.quote(str(package_root))
            + " "
            + shlex.quote(sys.executable)
            + " -m herdr.orchestrator spawn"
            + " --parent "
            + shlex.quote(repo_ref)
            + " --request-file "
            + ".herd/state/spawn-requests/<name>.json"
        )

        child_orchestration = f"""
## Child Herdr orchestration

You may delegate a genuinely separate repository-scoped objective to a child Herdr through the Control Plane.

This does NOT grant you direct authority over the child repository.

Rules:
- Do not cd into the child repository.
- Do not directly inspect, edit, stage, commit, or push child files.
- The child Herdr owns implementation authority in that repository.
- You may coordinate the child through Herdr agent state and output.
- Use child spawning only when the top-level objective genuinely requires another repository.

To spawn a child Herdr:

1. Create a JSON request under:
   `.herd/state/spawn-requests/`

2. The request may contain:

   target_repo: absolute child repository path
   task: exact child objective
   preset: optional Herdr preset
   rules: optional list of child-specific rules
   policy: optional child policy object
   task_policy: optional task-scoped policy

3. Submit the request with:

   `{spawn_command}`

The Control Plane will validate the request, initialize the child repository if necessary, install its safety guards, apply its rules, start its agents, dispatch its task, and record it in `.herd/state/children.json`.

A spawned child becomes a dependency of the current parent task. Continue monitoring it until its recorded child task reaches `COMPLETE`. Child completion is inside the parent task's scope.

Never use arbitrary Python or direct filesystem access to perform cross-repository implementation when this structured bridge can delegate it to a child Herdr.
"""

        completion = f"""
## Deterministic task lifecycle
Any child Herdr spawned for this top-level task is a required dependency.

Spawn success is NOT child completion.
The parent task remains responsible for the child outcome.

Before completing the parent:
- Every child dependency spawned for this task must reach `COMPLETE`.
- `ACTIVE`, `ERROR`, `ABORTED`, `MISSING`, `UNREADABLE`, or mismatched child state is unresolved.
- Monitor child agents and outcomes rather than treating successful creation as completion.
- If a child fails, resolve or surface the failure. Do not silently ignore it.
- The deterministic completion gate will refuse to complete this parent while a child dependency is unresolved.

When the full top-level task is genuinely complete:
1. Update durable shared memory only where the finding will matter to future tasks.
2. Write a concise checkpoint to `.herd/state/task-checkpoint.md` containing outcome, changed files, verification, child outcomes, unresolved risks, and reusable context.
3. Run exactly:
   `herdctl task-complete --repo "{repo_ref}" --checkpoint-file .herd/state/task-checkpoint.md`
4. Then deliver the final human-facing summary.

Do not mark a task complete before the Lead acceptance/review/verification gate and all child dependency gates are satisfied.
"""

    return f"""# HERD BOOTSTRAP

You are `{logical}` in a persistent Herdr engineering herd.
Repository: `{repo}`
Repository reference: `{repo_ref}`

IMPORTANT REPOSITORY PIN:
- Your current codebase is exactly `{repo.name}`.
- Your repository root is exactly `{repo}`.
- Do not directly change to, inspect, edit, stage, or commit another repository. Cross-repository implementation must be delegated to a separately repo-scoped child Herdr. Only the Supervisor may request that delegation through the Control Plane.
- All commits require the deterministic human commit-confirmation gate. Never bypass it or use `--no-verify`.
- All pushes require the deterministic human push-confirmation gate. Never force push or use `git push --no-verify`.

## Deterministic review protocol
After a Reviewer finishes, the Lead must validate and persist the decision with:
`herdctl review-decision --repo "{repo_ref}" --reviewer <reviewer-logical-name>`
Only exact `HERD_DECISION: APPROVE` or `HERD_DECISION: REJECT` tokens are valid.

## Effective Herdr rules
{rules_text}
{child_orchestration}
## Live topology
{topology}

## Verification command
`{test_command}`

## Shared context
Read if present before substantive work:
- `CLAUDE.md`
- `AGENTS.md`
- `.herd/memory/architecture.md`
- `.herd/memory/conventions.md`
- `.herd/memory/decisions.md`
- `.herd/memory/mistakes.md`
- `.herd/memory/task-history.md`

## Peer control
Use Herdr when coordinating:
- `herdr agent get <agent>`
- `herdr agent prompt <agent> "<text>" --wait --timeout 120000`
- `herdr agent wait <agent> --until idle --until done --until blocked --timeout 120000`
- `herdr agent read <agent> --source recent-unwrapped --lines 160`

Do not kill a slow agent without inspecting it first.
{completion}
---

{role}
"""


def start_herd(
    herd: HerdrInstance,
    *,
    force: bool = False,
) -> dict:
    """Start one complete Herdr runtime.

    This is a programmatic lifecycle operation. It does not invoke
    `herdctl bootstrap`.
    """

    if not herd.initialized:
        raise RuntimeError(
            f"{herd.repo} is not an initialized Herdr repository."
        )

    repo = herd.repo
    config = herd.load_config()
    orchestration = config["orchestration"]
    prefix = _prefix(repo)
    state_path = _state_path(herd)

    # Clean up stale/partial runtime state from an earlier bootstrap.
    if state_path.exists():
        old = json.loads(state_path.read_text())

        supervisor = old.get(
            "agents",
            {},
        ).get("supervisor")

        supervisor_live = bool(
            supervisor
            and run([
                "herdr",
                "agent",
                "get",
                supervisor,
            ]).returncode == 0
        )

        if supervisor_live and not force:
            raise RuntimeError(
                f"Existing live herd detected ({supervisor}). "
                "Use force=True only intentionally."
            )

        old_workspace = old.get("workspace_id")

        if not old_workspace:
            old_pane = old.get(
                "panes",
                {},
            ).get("supervisor", "")

            if ":" in old_pane:
                old_workspace = old_pane.split(
                    ":",
                    1,
                )[0]

        if old_workspace:
            print(
                "Cleaning previous harness workspace "
                f"{old_workspace} before startup..."
            )

            run([
                "herdr",
                "workspace",
                "close",
                old_workspace,
            ])

        try:
            state_path.unlink()
        except FileNotFoundError:
            pass

    workspace = jrun([
        "herdr",
        "workspace",
        "create",
        "--cwd",
        str(repo),
        "--label",
        f"herd-{repo.name}"[:40],
        "--no-focus",
    ])

    workspace_id = (
        workspace["result"]["workspace"]["workspace_id"]
    )
    root_pane = (
        workspace["result"]["root_pane"]["pane_id"]
    )

    try:
        leads = max(
            1,
            int(orchestration.get("leads", 1)),
        )
        pods = max(
            1,
            int(orchestration.get("pods", 1)),
        )

        panes = {
            "supervisor": root_pane,
        }

        anchor = root_pane

        for i in range(1, leads + 1):
            panes[f"lead{i}"] = split(
                anchor,
                "right",
            )
            anchor = panes[f"lead{i}"]

        for i in range(1, pods + 1):
            panes[f"executor{i}"] = split(
                anchor,
                "down",
            )
            panes[f"reviewer{i}"] = split(
                panes[f"executor{i}"],
                "right",
            )
            anchor = panes[f"reviewer{i}"]

        panes["controller"] = split(
            root_pane,
            "down",
        )

        agents = {
            "supervisor": _agent_name(
                prefix,
                "sup",
            ),
        }

        for i in range(1, leads + 1):
            agents[f"lead{i}"] = _agent_name(
                prefix,
                f"lead{i}",
            )

        for i in range(1, pods + 1):
            agents[f"executor{i}"] = _agent_name(
                prefix,
                f"exec{i}",
            )
            agents[f"reviewer{i}"] = _agent_name(
                prefix,
                f"rev{i}",
            )

        runtime_state = {
            "version": 2,
            "repo": str(repo),
            "created_at": int(time.time()),
            "workspace_id": workspace_id,
            "panes": panes,
            "agents": agents,
        }

        _save_state(
            herd,
            runtime_state,
        )

        start_timeout = int(
            orchestration.get(
                "agent_start_timeout_ms",
                60000,
            )
        )

        shell_wait = int(
            orchestration.get(
                "shell_ready_timeout_ms",
                30000,
            )
        )

        start_agent(
            agents["supervisor"],
            panes["supervisor"],
            config["roles"]["supervisor"],
            start_timeout,
            shell_wait,
        )

        for i in range(1, leads + 1):
            start_agent(
                agents[f"lead{i}"],
                panes[f"lead{i}"],
                config["roles"]["lead"],
                start_timeout,
                shell_wait,
            )

        for i in range(1, pods + 1):
            start_agent(
                agents[f"executor{i}"],
                panes[f"executor{i}"],
                config["roles"]["executor"],
                start_timeout,
                shell_wait,
            )

            start_agent(
                agents[f"reviewer{i}"],
                panes[f"reviewer{i}"],
                config["roles"]["reviewer"],
                start_timeout,
                shell_wait,
            )

        task_timeout = int(
            orchestration.get(
                "agent_task_timeout_ms",
                600000,
            )
        )

        for logical, agent in agents.items():
            if logical == "supervisor":
                role_type = "supervisor"
            elif logical.startswith("lead"):
                role_type = "lead"
            elif logical.startswith("executor"):
                role_type = "executor"
            else:
                role_type = "reviewer"

            print(
                f"Bootstrapping {logical} -> {agent}"
            )

            bootstrap = bootstrap_text(
                herd,
                logical,
                role_type,
                agents,
                config,
            )

            result = prompt(
                agent,
                bootstrap,
                task_timeout,
                True,
            )

            # Herdr may occasionally report a successful first prompt
            # submission without the agent showing any observable activity.
            # Bootstrap delivery is safe to retry because this prompt only
            # establishes the persistent role/repository contract; arbitrary
            # task prompts must never inherit this retry behavior.
            if (
                result.returncode
                and "agent_prompt_unobserved"
                in result.stderr
            ):
                print(
                    f"Retrying {logical} bootstrap after "
                    "unobserved first delivery..."
                )

                result = prompt(
                    agent,
                    bootstrap,
                    task_timeout,
                    True,
                )

            if result.returncode:
                print(
                    f"WARNING: {logical} bootstrap "
                    f"did not settle: {result.stderr.strip()}"
                )

        # Run the package-owned heartbeat controller.
        if orchestration.get(
            "heartbeat_autostart",
            True,
        ):
            controller = panes["controller"]

            command = heartbeat_process_command(
                repo
            )

            deadline = (
                time.monotonic()
                + max(
                    1.0,
                    shell_wait / 1000.0,
                )
            )

            while True:
                result = run([
                    "herdr",
                    "pane",
                    "run",
                    controller,
                    command,
                ])

                if result.returncode == 0:
                    break

                blob = (
                    f"{result.stderr}\n"
                    f"{result.stdout}"
                ).lower()

                if (
                    time.monotonic() < deadline
                    and (
                        "busy" in blob
                        or "available shell" in blob
                    )
                ):
                    time.sleep(0.5)
                    continue

                print(
                    "WARNING: heartbeat controller "
                    "failed to start:",
                    result.stderr.strip(),
                )
                break

        return runtime_state

    except Exception:
        print(
            "Startup failed; closing partial harness "
            f"workspace {workspace_id}..."
        )

        run([
            "herdr",
            "workspace",
            "close",
            workspace_id,
        ])

        try:
            state_path.unlink()
        except FileNotFoundError:
            pass

        raise
