"""Top-level task dispatch for a running Herdr."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .instance import HerdrInstance
from .lifecycle import bootstrap_text
from .runtime import agent_info, prompt, run


def _runtime_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / "runtime.json"


def _task_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / "task.json"


def load_runtime(herd: HerdrInstance) -> dict:
    path = _runtime_path(herd)

    if not path.exists():
        raise RuntimeError(
            f"No runtime state for {herd.repo}. Start the herd first."
        )

    return json.loads(path.read_text())


def load_task(herd: HerdrInstance) -> dict:
    path = _task_path(herd)

    if not path.exists():
        return {"status": "IDLE"}

    try:
        return json.loads(path.read_text())
    except Exception:
        return {
            "status": "ERROR",
            "error": "unreadable task state",
        }


def save_task(
    herd: HerdrInstance,
    task: dict,
) -> None:
    path = _task_path(herd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task, indent=2) + "\n"
    )


def role_type_for_logical(logical: str) -> str:
    if logical == "supervisor":
        return "supervisor"

    if logical.startswith("lead"):
        return "lead"

    if logical.startswith("executor"):
        return "executor"

    return "reviewer"


def send_runtime_reset(
    agent: str,
    command: str,
    timeout_ms: int = 30000,
):
    """Reset an interactive agent without destroying its Herdr session."""

    result = prompt(
        agent,
        command,
        timeout_ms,
        False,
    )

    if result.returncode:
        return result

    time.sleep(0.5)

    return run([
        "herdr",
        "agent",
        "wait",
        agent,
        "--until",
        "idle",
        "--until",
        "done",
        "--until",
        "blocked",
        "--timeout",
        str(timeout_ms),
    ])


def clear_contexts(
    herd: HerdrInstance,
) -> None:
    """Clear completed-task model contexts before new work."""

    config = herd.load_config()
    runtime = load_runtime(herd)
    task = load_task(herd)

    if task.get("status") == "ACTIVE":
        raise RuntimeError(
            "Refusing to clear contexts during an ACTIVE top-level task."
        )

    context = config.get("context", {})

    allowed_types = set(
        context.get(
            "clear_roles",
            [
                "supervisor",
                "lead",
                "executor",
                "reviewer",
            ],
        )
    )

    reset_commands = context.get(
        "reset_commands",
        {
            "claude": "/clear",
            "codex": "/new",
        },
    )

    selected = []

    for logical, agent in runtime["agents"].items():
        role_type = role_type_for_logical(logical)

        if role_type not in allowed_types:
            continue

        status = agent_info(agent)["status"]

        if status not in {"idle", "done"}:
            raise RuntimeError(
                f"Refusing to clear `{logical}` while "
                f"status is `{status}`. Resolve/finish it first."
            )

        kind = (
            config
            .get("roles", {})
            .get(role_type, {})
            .get("kind")
        )

        reset = reset_commands.get(kind)

        if not reset:
            raise RuntimeError(
                f"No context reset command configured for runtime "
                f"kind `{kind}` ({logical})."
            )

        selected.append(
            (
                logical,
                agent,
                role_type,
                kind,
                reset,
            )
        )

    print(
        "Checkpointed task context is preserved on disk; "
        "clearing live model context..."
    )

    for logical, agent, _role_type, kind, reset in selected:
        print(
            f"Clearing {logical} -> {agent} "
            f"({kind}: {reset})"
        )

        result = send_runtime_reset(
            agent,
            reset,
        )

        if result.returncode:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or f"Could not clear {logical}"
            )

    timeout = int(
        config["orchestration"].get(
            "agent_task_timeout_ms",
            600000,
        )
    )

    agents = runtime["agents"]

    for logical, agent, role_type, _kind, _reset in selected:
        print(
            f"Re-seeding {logical} contract"
        )

        result = prompt(
            agent,
            bootstrap_text(
                herd,
                logical,
                role_type,
                agents,
                config,
            ),
            timeout,
            True,
        )

        if result.returncode:
            print(
                f"WARNING: contract re-seed for "
                f"{logical} did not settle cleanly: "
                f"{result.stderr.strip()}"
            )


def _rules_text(
    herd: HerdrInstance,
    task_policy: dict | None = None,
) -> str:
    policy = herd.effective_policy(task_policy)

    if not policy.rules:
        return "- No additional repository-specific rules."

    return "\n".join(
        f"- {rule}"
        for rule in policy.rules
    )


def dispatch_task(
    herd: HerdrInstance,
    text: str,
    *,
    rejection_drill: bool = False,
    task_policy: dict | None = None,
) -> dict:
    """Dispatch one top-level objective to the running Supervisor."""

    text = text.strip()

    if not text:
        raise ValueError(
            "Task description cannot be empty."
        )

    config = herd.load_config()
    runtime = load_runtime(herd)
    prior = load_task(herd)

    if prior.get("status") == "ACTIVE":
        raise RuntimeError(
            f"Task {prior.get('id')} is already ACTIVE. "
            "Complete or abort it before starting another."
        )

    if (
        prior.get("status")
        in {"COMPLETE", "ABORTED", "ERROR"}
        and config.get(
            "context",
            {},
        ).get(
            "clear_before_new_task",
            True,
        )
    ):
        print(
            f"Previous task is {prior.get('status')}; "
            "resetting completed-task contexts before new work..."
        )

        clear_contexts(herd)

    supervisor = runtime["agents"]["supervisor"]
    supervisor_status = agent_info(
        supervisor
    )["status"]

    if supervisor_status not in {"idle", "done"}:
        raise RuntimeError(
            f"Supervisor is `{supervisor_status}`, "
            "not ready for a new top-level task."
        )

    task_id = (
        time.strftime("%Y%m%d-%H%M%S")
        + "-"
        + hashlib.sha1(
            text.encode()
        ).hexdigest()[:6]
    )

    task = {
        "version": 1,
        "id": task_id,
        "status": "ACTIVE",
        "description": text,
        "started_at": int(time.time()),
        "repo": str(herd.repo),
        "repo_ref": str(herd.repo),
        "heartbeat_count": 0,
        "manual_prompt_count": 0,
        "rejection_drill": bool(rejection_drill),
    }

    if task_policy:
        task["policy"] = task_policy

    save_task(
        herd,
        task,
    )

    topology = "\n".join(
        f"- {logical}: `{agent}`"
        for logical, agent
        in runtime["agents"].items()
    )

    drill = ""

    if rejection_drill:
        drill = """
REJECTION_LOOP_DRILL (explicit harness validation requirement):
- The first Reviewer pass MUST return `HERD_DECISION: REJECT` for the explicit process criterion that a fresh second-pass verification has not yet been produced.
- Do NOT invent or claim a fake code defect.
- The Lead MUST route that rejection back to the SAME Executor session.
- The Executor must perform a fresh second-pass verification/evidence pass and re-handoff from the SAME session.
- The SAME Reviewer session then reviews normally and may approve if the implementation and evidence are sound.
- This drill exists only to validate persistent rejection routing.
"""

    rules = _rules_text(
        herd,
        task_policy,
    )

    task_prompt = f"""# NEW TOP-LEVEL PROJECT — {task_id}

USER REQUEST:
{text}

REPOSITORY BOUNDARY:
- Codebase: {herd.repo.name}
- Repository root: {herd.repo}
- This task is scoped ONLY to this repository/worktree.
- Do not directly inspect or modify another repository. If cross-repository implementation is required, delegate it to a separately repo-scoped child Herdr through the structured Control Plane bridge in your bootstrap contract.
- Before any commit, stop at the human commit-confirmation gate. The harness will enforce it.
- Before any push, stop at the human push-confirmation gate. Never force push or use --no-verify.

EFFECTIVE HERDR RULES:
{rules}

LIVE HERD:
{topology}
{drill}
Own this through completion. Inspect the repo, delegate to Lead(s), require persistent Executor/Reviewer rejection loops, require actual test evidence, and keep `.herd/state/supervisor-status.md` current.

Any child Herdr spawned for this task is a required dependency. Spawning a child is not completion. Monitor required children until their task state is COMPLETE; unresolved child work blocks parent completion.

When genuinely complete, write `.herd/state/task-checkpoint.md`, then run:
`herdctl task-complete --repo "{herd.repo}" --checkpoint-file .herd/state/task-checkpoint.md`

Do not push, deploy, publish, or merge protected branches unless project config explicitly permits it.
Begin now.
"""

    result = prompt(
        supervisor,
        task_prompt,
        int(
            config["orchestration"].get(
                "agent_task_timeout_ms",
                600000,
            )
        ),
        False,
    )

    if result.returncode:
        task["status"] = "ERROR"
        task["error"] = (
            result.stderr.strip()
            or result.stdout.strip()
        )
        task["completed_at"] = int(
            time.time()
        )

        save_task(
            herd,
            task,
        )

        raise RuntimeError(
            task["error"]
        )

    print(
        f"Task {task_id} dispatched to "
        f"{supervisor} in repo {herd.repo}."
    )

    return task
