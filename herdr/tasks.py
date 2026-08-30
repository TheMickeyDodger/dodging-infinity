"""Top-level task dispatch for a running Herdr."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import identity
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


RESET_STATE_FILE = "context-reset.json"
RESET_STATE_VERSION = 1

RESET_PHASE_PLANNED = "planned"
RESET_PHASE_CLEARED = "cleared"
RESET_PHASE_RESEEDED = "reseeded"
RESET_PHASE_BLOCKED = "blocked"

PROBLEM_RESET_UNAVAILABLE = "context_reset_command_unavailable"
PROBLEM_RESET_FAILED = "context_reset_clear_failed"
PROBLEM_RESEED_FAILED = "context_reset_reseed_failed"


def reset_state_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / RESET_STATE_FILE


def load_reset_state(herd: HerdrInstance) -> dict:
    """The durable record of the last context reset, or an empty one.

    A file that will not parse is reported empty rather than raised:
    the caller's next action is to write a fresh record anyway.
    Outside that, and disclosed: this does not repair the file.
    """
    try:
        document = json.loads(reset_state_path(herd).read_text())
    except (OSError, ValueError):
        return {"version": RESET_STATE_VERSION, "roles": {}}
    if not isinstance(document, dict) or not isinstance(
        document.get("roles"), dict
    ):
        return {"version": RESET_STATE_VERSION, "roles": {}}
    return document


def save_reset_state(herd: HerdrInstance, document: dict) -> None:
    path = reset_state_path(herd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n"
    )


def _record(document, logical, **fields):
    entry = document["roles"].setdefault(logical, {})
    entry.update(fields)
    return entry


def clear_contexts(
    herd: HerdrInstance,
    lister=None,
) -> dict:
    """Clear completed-task model contexts before new work.

    Returns the durable reset record. Every step is written to disk as
    it happens, so an interruption between clearing a context and
    re-seeding its contract leaves a record a human can act on: the
    role's phase stays `cleared` and carries its problem code and the
    failure's text, whether the re-seed returned non-zero or raised —
    including a KeyboardInterrupt. That is what replaces the four
    silently unseeded agents the previous implementation left behind.
    The record is the state; the return value is a copy of it.

    Scope of that guarantee: failures inside the clear and re-seed
    loops, which are wrapped. Outside it, and disclosed: a failure
    between `_record` and the `save_reset_state` that follows it, or a
    SIGKILL, leaves the record at its previous step — the phases are
    ordered so that step is the conservative one, but no reason is
    written for it.

    Refusal shapes, stated because one of them CHANGED in the collapse
    (round-01 finding E.1):

    - A role whose runtime kind has no configured reset command is
      recorded BLOCKED and the OTHER roles continue. The CLI copy this
      replaced raised and aborted the whole operation. Per-role
      blocking is the brief's "durable, reasoned BLOCK", and it means
      one unconfigured role no longer prevents the rest from being
      reset — a deliberate widening of what completes, not of what is
      permitted.
    - A BUSY agent still aborts the whole operation by raising. That
      check runs ahead of the identity work, so within this function a
      refused role leaves no durable binding write behind it. Outside
      that ordering, and disclosed: a role already recorded `planned`
      in an earlier iteration keeps that record, which is the point of
      writing it.

    Identity is decided per role before a command is sent (see
    `herdr.identity`). A role whose agent Herdr fails to resolve is
    rediscovered from the live listing on exact evidence only; one
    that is neither present nor exactly rediscoverable is recorded
    BLOCKED and receives no command. Scope: decided from the listing
    `lister` returns at that moment. Outside it, and disclosed: an
    agent replaced after the decision and before the send is not
    caught here.
    """

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

    document = {"version": RESET_STATE_VERSION, "roles": {}}
    save_reset_state(herd, document)

    selected = []
    blocked = []

    # R-53 AQ-5: a CORRUPT bindings document is a REFUSAL, and the
    # refusal is recorded durably before it is raised. The reader used
    # to map corruption onto an empty document, so a damaged file
    # became indistinguishable from a first run and this reset would
    # have proceeded to rediscovery — and the next save would have
    # overwritten the damaged file, destroying the only evidence that
    # anything was wrong. Every role is blocked, because within this
    # reset an identity record that is unreadable is no basis at all.
    try:
        bindings = identity.load_bindings(herd.herd_root)
    except identity.BindingsCorrupt as exc:
        for logical, agent in runtime["agents"].items():
            _record(
                document, logical, phase=RESET_PHASE_BLOCKED,
                agent=agent,
                problem=identity.PROBLEM_BINDINGS_CORRUPT,
                detail=str(exc),
            )
            blocked.append(logical)
        document["blocked"] = blocked
        save_reset_state(herd, document)
        return document

    for logical, agent in runtime["agents"].items():
        role_type = role_type_for_logical(logical)

        if role_type not in allowed_types:
            continue

        kind = (
            config
            .get("roles", {})
            .get(role_type, {})
            .get("kind")
        )

        reset = reset_commands.get(kind)

        if not reset:
            _record(
                document, logical, phase=RESET_PHASE_BLOCKED,
                agent=agent, problem=PROBLEM_RESET_UNAVAILABLE,
                detail=(
                    "no context reset command configured for runtime "
                    "kind `%s`" % kind
                ),
            )
            save_reset_state(herd, document)
            blocked.append(logical)
            continue

        # Round-01 E.2: the busy refusal runs ahead of the identity
        # work, so within this iteration a refused role leaves no
        # durable binding write. It used to run after `classify` and
        # after the
        # REDISCOVER branch's `save_bindings`, which meant a busy
        # agent could have its binding rewritten to disk and THEN be
        # refused — a durable effect that disagreed with the
        # operation's outcome, which is this increment's own subject.
        #
        # The probe is taken ONCE and reused, so the refusal and the
        # classification decide on the same observation rather than on
        # two reads that could disagree. A probe SENTINEL (`missing`,
        # `unknown`) is not a busy agent and routes to classification;
        # refusing it here would restore the conflation I2 removes.
        probe = agent_info(agent)
        status = probe["status"]

        if identity.is_busy(status):
            raise RuntimeError(
                f"Refusing to clear `{logical}` while "
                f"status is `{status}`. Resolve/finish it first."
            )

        binding = bindings["roles"].get(logical, {})
        verdict = identity.classify(logical, binding, probe)

        if verdict.action == identity.ACTION_REDISCOVER:
            listing = (lister or _production_listing)()
            record, problem, detail = identity.rediscover(
                logical, binding, listing
            )
            if record is None:
                _record(
                    document, logical, phase=RESET_PHASE_BLOCKED,
                    agent=agent, problem=problem, detail=detail,
                    verdict=verdict.verdict,
                )
                save_reset_state(herd, document)
                blocked.append(logical)
                continue
            agent = record.get("name") or agent
            # The REDISCOVERED agent gets the same busy check the
            # recorded one got, and it runs BEFORE the binding is
            # written. Moving the original check ahead of the identity
            # work (round-01 E.2) left this path with no status check
            # at all, because the probe that ran earlier was of the
            # name that no longer resolves. Checking here keeps the
            # refusal and keeps it free of durable side effects.
            rediscovered_status = record.get("agent_status")
            if identity.is_busy(rediscovered_status):
                raise RuntimeError(
                    f"Refusing to clear `{logical}` while "
                    f"status is `{rediscovered_status}`. "
                    f"Resolve/finish it first."
                )
            binding = identity.binding_for(logical, agent, record)
            bindings["roles"][logical] = binding
            identity.save_bindings(herd.herd_root, bindings)
            verdict = identity.Verdict(
                logical, identity.VERDICT_REPLACED,
                identity.ACTION_REBOOTSTRAP,
                identity.PROBLEM_SESSION_REPLACED,
                "rediscovered `%s` on exact evidence" % logical,
            )

        if verdict.action == identity.ACTION_BIND:
            # R-53 AQ-3: an UNBOUND role BLOCKS here, and it does not
            # get bound here. Binding is the BOOTSTRAP's job, from
            # exact live evidence, before the herd is reported ready
            # (`lifecycle.establish_role_bindings`). Binding it inside
            # a reset would put a durable write back into the path
            # round-01 E.2 cleaned of them, and would bind whatever is
            # live at reset time — which is precisely the "adopt
            # whatever answers to the name" behaviour this module
            # exists to refuse.
            _record(
                document, logical, phase=RESET_PHASE_BLOCKED,
                agent=agent, problem=verdict.problem,
                detail=verdict.detail, verdict=verdict.verdict,
            )
            save_reset_state(herd, document)
            blocked.append(logical)
            continue

        if verdict.action == identity.ACTION_BLOCK:
            _record(
                document, logical, phase=RESET_PHASE_BLOCKED,
                agent=agent, problem=verdict.problem,
                detail=verdict.detail, verdict=verdict.verdict,
            )
            save_reset_state(herd, document)
            blocked.append(logical)
            continue

        _record(
            document, logical, phase=RESET_PHASE_PLANNED, agent=agent,
            verdict=verdict.verdict, problem=None, detail=None,
        )
        save_reset_state(herd, document)

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

        try:
            result = send_runtime_reset(
                agent,
                reset,
            )
        except BaseException as exc:
            # Round-01 finding B, the clear half. An ESCAPING
            # exception used to record no reason, leaving the durable
            # record at "planned" with no failure text on it. Caught
            # as
            # BaseException because a KeyboardInterrupt at this point
            # is one of the shapes that motivated the record.
            _record(
                document, logical, phase=RESET_PHASE_PLANNED,
                problem=PROBLEM_RESET_FAILED,
                detail="%s: %s" % (type(exc).__name__, exc),
            )
            save_reset_state(herd, document)
            raise

        if result.returncode:
            _record(
                document, logical, phase=RESET_PHASE_BLOCKED,
                problem=PROBLEM_RESET_FAILED,
                detail=(
                    result.stderr.strip()
                    or result.stdout.strip()
                    or f"Could not clear {logical}"
                ),
            )
            save_reset_state(herd, document)
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or f"Could not clear {logical}"
            )

        _record(document, logical, phase=RESET_PHASE_CLEARED)
        save_reset_state(herd, document)

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

        try:
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
        except BaseException as exc:
            # Round-01 finding B, and the historical defect itself.
            # The failure that motivated this increment was an
            # EXCEPTION — a NameError raised from this loop — not a
            # non-zero returncode. Before this, the returncode path
            # recorded its reason two lines below and the exception
            # path recorded none, which is the one failure mode the
            # record exists for. BaseException because case C in the
            # round-01 review is a KeyboardInterrupt.
            _record(
                document, logical, phase=RESET_PHASE_CLEARED,
                problem=PROBLEM_RESEED_FAILED,
                detail="%s: %s" % (type(exc).__name__, exc),
            )
            save_reset_state(herd, document)
            raise

        if result.returncode:
            _record(
                document, logical, phase=RESET_PHASE_CLEARED,
                problem=PROBLEM_RESEED_FAILED,
                detail=(
                    result.stderr.strip()
                    or f"contract re-seed for {logical} did not settle"
                ),
            )
            save_reset_state(herd, document)
            print(
                f"WARNING: contract re-seed for "
                f"{logical} did not settle cleanly: "
                f"{result.stderr.strip()}"
            )
            continue

        _record(
            document, logical, phase=RESET_PHASE_RESEEDED,
            problem=None, detail=None,
        )
        save_reset_state(herd, document)

    document["blocked"] = blocked
    save_reset_state(herd, document)
    return document


def _production_listing() -> dict:
    """The real `herdr agent list` payload, or an empty envelope.

    A failed or unparsable listing yields an envelope with no agents,
    which rediscovery reads as "no candidate" and BLOCKS on. Outside
    that, and disclosed: this does not distinguish a genuinely empty
    Herdr from an unreachable one — both block, which is the
    fail-closed direction.
    """
    result = run(["herdr", "agent", "list"])

    if result.returncode:
        return {}

    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


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
