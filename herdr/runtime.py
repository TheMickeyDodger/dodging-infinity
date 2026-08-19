"""Low-level Herdr runtime operations.

These functions talk to the underlying `herdr` binary. They contain no
argparse or herdctl-specific behavior and may be used by the control plane
directly.
"""

from __future__ import annotations

import json
import subprocess
import time


def run(cmd, cwd=None, check=False):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=check,
    )


def jrun(cmd):
    p = run(cmd)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return json.loads(p.stdout)


def split(pane, direction):
    d = jrun([
        "herdr",
        "pane",
        "split",
        pane,
        "--direction",
        direction,
        "--no-focus",
    ])
    return d["result"]["pane"]["pane_id"]


def start_agent(
    name,
    pane,
    role_cfg,
    timeout,
    shell_ready_timeout_ms=30000,
):
    """Start an agent after its pane reaches an available shell."""
    cmd = [
        "herdr",
        "agent",
        "start",
        name,
        "--kind",
        role_cfg["kind"],
        "--pane",
        pane,
        "--timeout",
        str(timeout),
    ]

    if role_cfg.get("args"):
        cmd += ["--"] + role_cfg["args"]

    deadline = (
        time.monotonic()
        + max(1.0, shell_ready_timeout_ms / 1000.0)
    )
    attempt = 0

    while True:
        attempt += 1
        p = run(cmd)

        if p.returncode == 0:
            return

        blob = f"{p.stderr}\n{p.stdout}"

        if (
            "agent_pane_busy" in blob
            and time.monotonic() < deadline
        ):
            if attempt == 1:
                print(
                    f"Waiting for pane {pane} to reach an "
                    f"interactive shell before starting {name}..."
                )

            time.sleep(0.5)
            continue

        diagnostic = ""

        if "agent_pane_busy" in blob:
            info = run([
                "herdr",
                "pane",
                "process-info",
                pane,
            ])
            recent = run([
                "herdr",
                "pane",
                "read",
                pane,
                "--source",
                "recent-unwrapped",
                "--lines",
                "40",
            ])
            diagnostic = (
                f"\nPane diagnostics ({pane}):\n"
                f"process-info:\n"
                f"{info.stdout}{info.stderr}\n"
                f"recent output:\n"
                f"{recent.stdout}{recent.stderr}"
            )

        raise RuntimeError(
            f"start {name} failed after {attempt} attempt(s):\n"
            f"{p.stderr}\n"
            f"{p.stdout}"
            f"{diagnostic}"
        )


PROMPT_MOVEMENT_TIMEOUT_MS = 30000
PROMPT_POLL_SECONDS = 0.25
PROMPT_SETTLED_STATES = {
    "idle",
    "done",
    "blocked",
}


def _find_int_field(obj, field):
    """Best-effort recursive integer field extraction."""
    if isinstance(obj, dict):
        value = obj.get(field)

        if isinstance(value, int):
            return value

        for child in obj.values():
            found = _find_int_field(
                child,
                field,
            )

            if found is not None:
                return found

    elif isinstance(obj, list):
        for child in obj:
            found = _find_int_field(
                child,
                field,
            )

            if found is not None:
                return found

    return None


def _prompt_snapshot(agent):
    """Read the observable Herdr state used to settle a prompt."""
    result = run([
        "herdr",
        "agent",
        "get",
        agent,
    ])

    if result.returncode:
        return {
            "status": "missing",
            "state_change_seq": None,
            "revision": None,
        }

    try:
        data = json.loads(
            result.stdout
        )
    except Exception:
        return {
            "status": "unknown",
            "state_change_seq": None,
            "revision": None,
        }

    return {
        "status": (
            find_agent_status(data)
            or "unknown"
        ),
        "state_change_seq": _find_int_field(
            data,
            "state_change_seq",
        ),
        "revision": _find_int_field(
            data,
            "revision",
        ),
    }


def _prompt_state_moved(
    baseline,
    current,
):
    """Determine whether the submitted prompt produced observable activity."""
    before_seq = baseline.get(
        "state_change_seq"
    )

    after_seq = current.get(
        "state_change_seq"
    )

    # state_change_seq is Herdr's strongest signal. Prefer it whenever
    # both snapshots expose it.
    if (
        before_seq is not None
        and after_seq is not None
    ):
        return after_seq != before_seq

    before_revision = baseline.get(
        "revision"
    )

    after_revision = current.get(
        "revision"
    )

    # Older/different Herdr schemas may lack state_change_seq.
    if (
        before_revision is not None
        and after_revision is not None
    ):
        return (
            after_revision
            != before_revision
        )

    return (
        current.get("status")
        != baseline.get("status")
    )


def _prompt_failure(
    cmd,
    submitted,
    code,
    message,
):
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=1,
        stdout=submitted.stdout,
        stderr=json.dumps(
            {
                "error": {
                    "code": code,
                    "message": message,
                }
            }
        ),
    )


def prompt(agent, text, timeout, wait=True):
    """Submit a prompt, optionally settling it without Herdr's 5s wait gate."""
    cmd = [
        "herdr",
        "agent",
        "prompt",
        agent,
        text,
    ]

    if not wait:
        return run(cmd)

    baseline = _prompt_snapshot(
        agent
    )

    submitted = run(
        cmd
    )

    if submitted.returncode:
        return submitted

    started = time.monotonic()

    overall_deadline = (
        started
        + max(
            0.001,
            timeout / 1000.0,
        )
    )

    movement_deadline = min(
        overall_deadline,
        started
        + (
            PROMPT_MOVEMENT_TIMEOUT_MS
            / 1000.0
        ),
    )

    moved = False

    while time.monotonic() < overall_deadline:
        current = _prompt_snapshot(
            agent
        )

        if (
            not moved
            and _prompt_state_moved(
                baseline,
                current,
            )
        ):
            moved = True

        if (
            moved
            and current.get("status")
            in PROMPT_SETTLED_STATES
        ):
            return submitted

        now = time.monotonic()

        if (
            not moved
            and now >= movement_deadline
        ):
            return _prompt_failure(
                cmd,
                submitted,
                "agent_prompt_unobserved",
                (
                    "prompt submission succeeded but "
                    f"{agent} showed no observable "
                    "state change within "
                    f"{PROMPT_MOVEMENT_TIMEOUT_MS} ms"
                ),
            )

        time.sleep(
            PROMPT_POLL_SECONDS
        )

    return _prompt_failure(
        cmd,
        submitted,
        "agent_prompt_settle_timeout",
        (
            f"{agent} showed prompt activity but "
            "did not reach idle, done, or blocked "
            f"within {timeout} ms"
        ),
    )


HERDR_STATES = {
    "idle",
    "working",
    "blocked",
    "done",
    "unknown",
}


def find_agent_status(obj):
    """Best-effort extraction across Herdr response schema revisions."""
    if isinstance(obj, dict):
        for key in (
            "agent_status",
            "effective_status",
            "status",
            "state",
        ):
            value = obj.get(key)

            if (
                isinstance(value, str)
                and value.lower() in HERDR_STATES
            ):
                return value.lower()

        for value in obj.values():
            found = find_agent_status(value)

            if found:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = find_agent_status(value)

            if found:
                return found

    return None


def agent_info(agent):
    p = run([
        "herdr",
        "agent",
        "get",
        agent,
    ])

    if p.returncode:
        return {
            "status": "missing",
            "raw": None,
        }

    try:
        data = json.loads(p.stdout)
    except Exception:
        return {
            "status": "unknown",
            "raw": None,
        }

    return {
        "status": find_agent_status(data) or "unknown",
        "raw": data,
    }
