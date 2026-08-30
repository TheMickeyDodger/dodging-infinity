"""Idle-aware heartbeat controller for a running Herdr."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path

from herdr.instance import HerdrInstance
from herdr import turns
from herdr.runtime import agent_info, prompt, run


def _runtime_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / "runtime.json"


def _task_path(herd: HerdrInstance) -> Path:
    return herd.herd_root / "state" / "task.json"


def _load_runtime(herd: HerdrInstance) -> dict:
    path = _runtime_path(herd)

    if not path.exists():
        raise RuntimeError(
            f"No runtime state for {herd.repo}. Start the herd first."
        )

    return json.loads(path.read_text())


def _load_task(herd: HerdrInstance) -> dict:
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


def _save_task(
    herd: HerdrInstance,
    task: dict,
) -> None:
    path = _task_path(herd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task, indent=2) + "\n"
    )


def heartbeat_message(
    herd: HerdrInstance,
    runtime: dict,
    task: dict,
) -> str:
    rows = []

    for logical, agent in runtime["agents"].items():
        rows.append(
            f"- {logical}: {agent_info(agent)['status']}"
        )

    return f"""# HERD HEARTBEAT — ACTIVE TASK {task.get('id')}

Repository: {herd.repo}
Deterministic controller health-check.

Observed states:
{chr(10).join(rows)}

Inspect active work. Read output before intervening. If blocked, determine what is needed. If a Lead rejected work, ensure it returns to the SAME Pod sessions. Never directly leave this repository. Cross-repository implementation must be delegated to child Herdrs through the Control Plane. Update `.herd/state/supervisor-status.md`.
"""


def heartbeat_once(
    herd: HerdrInstance,
) -> str:
    """Run one idle-aware heartbeat check."""

    runtime = _load_runtime(herd)
    task = _load_task(herd)

    supervisor = runtime["agents"]["supervisor"]
    now = time.strftime("%H:%M:%S")

    # R-63 BA-5: THE OBSERVER RUNS HERE, and here is the only place it
    # can run.
    #
        # A turn killed by transport is, within this design, unable to
    # write its own epitaph — the dying party does not run — so the
    # outcome has to be derived by something that SURVIVES the death. This controller is a separate
    # process that already probes every control role on every pass and
    # keeps running when a role's turn dies, which is exactly the
    # property AS-1's motivating specimen needed and did not have.
    #
    # It runs BEFORE the heartbeat prompt below, so a role that died
    # since the last pass is recorded before anything new is sent —
    # and it runs on every pass, including the ones that then SKIP the
    # prompt, because a skipped heartbeat is not a reason to stop
    # observing.
    observed = []
    if isinstance(task.get("id"), str) and task.get("id"):
        try:
            observed, _document = turns.observe_control_roles(
                herd.herd_root, runtime["agents"], task["id"],
                agent_info,
            )
        except Exception as exc:                      # noqa: BLE001
                        # The observation is EVIDENCE, not control. Within this
            # controller a failure here must not stop the heartbeat
            # that keeps the herd alive, and it must not pass silently.
            print(
                f"[{now}] turn observation failed: "
                f"{exc.__class__.__name__}: {exc}",
                flush=True,
            )
        for outcome, logical, turn_id in observed:
            print(
                f"[{now}] turn {turn_id} ({logical}): {outcome}",
                flush=True,
            )

    if task.get("status") != "ACTIVE":
        print(
            f"[{now}] heartbeat skipped: no ACTIVE task "
            f"({task.get('status', 'IDLE')})",
            flush=True,
        )
        return "skipped"

    status = agent_info(supervisor)["status"]

    if status not in {"idle", "done"}:
        print(
            f"[{now}] heartbeat skipped: "
            f"supervisor is {status}",
            flush=True,
        )
        return "skipped"

    result = prompt(
        supervisor,
        heartbeat_message(
            herd,
            runtime,
            task,
        ),
        120000,
        False,
    )

    # R-55 AS-4: ROUTED and EFFECT-OBSERVED, recorded as two facts.
    #
    # A returncode of 0 means the prompt was ACCEPTED by the
    # transport. It does not mean the supervisor acted on it — that is
    # the mode R-49 and R-62 each cost this mission once, and the
    # third mode this increment names. The EFFECT is observed on a
    # LATER pass, when the supervisor's `state_change_seq` has
    # advanced past the value recorded when it was routed.
    if result.returncode == 0:
        try:
            _record_heartbeat_routing(herd, task, supervisor)
        except Exception as exc:                      # noqa: BLE001
            print(
                f"[{now}] heartbeat routing record failed: "
                f"{exc.__class__.__name__}",
                flush=True,
            )

    if result.returncode == 0:
        task["heartbeat_count"] = (
            int(task.get("heartbeat_count", 0)) + 1
        )
        task["last_heartbeat_at"] = int(time.time())
        _save_task(herd, task)

    outcome = (
        "ok"
        if result.returncode == 0
        else "failed"
    )

    print(
        f"[{now}] heartbeat -> "
        f"{supervisor}: {outcome}",
        flush=True,
    )

    return outcome


def _state_change_seq(agent):
    """The agent's own change counter, or None.

    THE EFFECT EVIDENCE, and it is deliberately not a clock: it
    advances when the agent's state changes and stands still when it
    does not, so "the instruction was acted on" is derived from the
    agent's own behaviour rather than from elapsed time.
    """
    record = (agent_info(agent).get("raw") or {}).get("result") or {}
    value = (record.get("agent") or {}).get("state_change_seq")
    return value if isinstance(value, int) else None


def _record_heartbeat_routing(herd, task, supervisor):
    """Record the heartbeat prompt as ROUTED, and observe its EFFECT.

    Two facts on one record: `routed_at` when the transport accepted
    it, `effect_observed_at` only once the supervisor's own change
    counter has moved past the value captured at routing. A pass that
    finds it unmoved leaves the turn ROUTED-and-unobserved, which is
    what the surface then reports.
    """
    document = turns.load_turns(herd.herd_root)
    seq = _state_change_seq(supervisor)
    pending = None
    for entry in reversed(document["turns"]):
        if (isinstance(entry, dict)
                and entry.get("logical") == "supervisor-heartbeat"
                and entry.get("routed_at")
                and not entry.get("effect_observed_at")):
            pending = entry
            break
    if pending is not None:
        routed_seq = pending.get("routed_state_change_seq")
        if (isinstance(seq, int) and isinstance(routed_seq, int)
                and seq > routed_seq):
            observed = turns.mark_effect_observed(pending)
            observed = turns.close(
                observed, turns.TURN_COMPLETED, artifact_present=None,
            )
            document["turns"] = [
                observed if entry is pending else entry
                for entry in document["turns"]
            ]
            turns.save_turns(herd.herd_root, document)
        return
    record = turns.new_turn(
        turns._default_turn_id("heartbeat"), task["id"],
        "supervisor-heartbeat",
    )
    record["agent"] = supervisor
    record = turns.mark_routed(record)
    record["routed_state_change_seq"] = seq
    document["turns"].append(record)
    turns.save_turns(herd.herd_root, document)


def run_heartbeat(
    herd: HerdrInstance,
    *,
    once: bool = False,
) -> None:
    """Run the heartbeat controller."""

    config = herd.load_config()

    interval = int(
        config["orchestration"].get(
            "heartbeat_seconds",
            900,
        )
    )

    if once:
        heartbeat_once(herd)
        return

    print(
        f"Idle-aware heartbeat every "
        f"{interval}s for {herd.repo}",
        flush=True,
    )

    while True:
        try:
            heartbeat_once(herd)
            time.sleep(interval)

        except KeyboardInterrupt:
            print("Heartbeat stopped.")
            return

        except Exception as exc:
            print(
                "heartbeat error:",
                exc,
                flush=True,
            )
            time.sleep(
                min(60, interval)
            )


def heartbeat_process_command(
    repo: str | Path,
) -> str:
    """Build the controller-pane heartbeat process command.

    The harness is not installed as a Python package yet, so explicitly
    expose its package root through PYTHONPATH.
    """

    repo = Path(repo).expanduser().resolve()
    package_root = Path(__file__).resolve().parents[1]
    script = Path(__file__).resolve()

    return " ".join(
        [
            "env",
            f"PYTHONPATH={shlex.quote(str(package_root))}",
            shlex.quote(sys.executable),
            shlex.quote(str(script)),
            "--repo",
            shlex.quote(str(repo)),
        ]
    )


def stop_heartbeat(
    herd: HerdrInstance,
) -> None:
    runtime = _load_runtime(herd)
    pane = runtime["panes"]["controller"]

    result = run([
        "herdr",
        "pane",
        "send-keys",
        pane,
        "ctrl+c",
    ])

    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Could not stop heartbeat controller."
        )

    print(
        "Sent ctrl+c to heartbeat controller."
    )


def restart_heartbeat(
    herd: HerdrInstance,
) -> None:
    runtime = _load_runtime(herd)
    pane = runtime["panes"]["controller"]

    run([
        "herdr",
        "pane",
        "send-keys",
        pane,
        "ctrl+c",
    ])

    time.sleep(0.8)

    command = heartbeat_process_command(
        herd.repo
    )

    deadline = time.monotonic() + 10

    while True:
        result = run([
            "herdr",
            "pane",
            "run",
            pane,
            command,
        ])

        if result.returncode == 0:
            print(
                "Restarted idle-aware "
                "heartbeat controller."
            )
            return

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

        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Could not restart heartbeat controller."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="herdr-heartbeat"
    )

    parser.add_argument(
        "--repo",
        required=True,
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    args = parser.parse_args()

    try:
        run_heartbeat(
            HerdrInstance(args.repo),
            once=args.once,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
