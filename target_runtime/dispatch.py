"""Target-Herdr dispatch: the exact stored handoff, nothing else.

Dispatch goes through the EXISTING structured child-spawn bridge
(``herdr.orchestrator.execute_spawn_request``) — no parallel path.
The spawn request the control layer emits toward the target carries
EXACTLY four fields:

- ``target_repo`` — the leased workspace realpath (resolved from the
  protected record, never from a caller);
- ``task`` — ``record["handoff"]["text"]`` BYTE-EXACT: no prefix, no
  suffix, no template, no re-wrap, no normalization. (The bridge
  strips surrounding whitespace; the authority layer makes padded
  handoff text unrepresentable, so that strip is provably an
  identity for every dispatchable record.)
- ``alias`` — a fixed derivation from the workflow id.
- ``preset`` — the fixed DI-owned unattended target execution
  posture, never sourced from mutable workflow or authority content.

Nothing else: no ``rules``, no ``policy``, no ``task_policy``, no
``test_command``, no ``force``, no ``rejection_drill``. The preset
controls agent permission posture only. The child Herdr's own role
contracts, policy resolution, lifecycle, review depth, recovery, and
Git gates apply untouched — the control layer adds no strategy, which
is what makes the target Supervisor demonstrably the FIRST
strategy-bearing component (plan D-5). Bounded corrective follow-ups
use the SAME fixed execution posture through the SAME gate; new
authority content requires a new authorized revision through the full
mission path.
"""

import secrets

from herdr.orchestrator import execute_spawn_request

ALIAS_PREFIX = "di-remote-2-"

# DI-owned unattended execution posture for every remote target
# Herdr. This is trusted Runtime configuration: it is deliberately
# not read from Mission Authorization, handoff text, role output,
# user text, target instructions, or any mutable workflow field.
DI_TARGET_EXECUTION_PRESET = "max-quality"

# The SINGLE source of the unresolved-task-id sentinel (I4):
# ``target_identity_from_spawn`` NEVER returns None — a spawn result
# carrying no usable id falls back to THIS literal. Consumers (the
# Runtime's dispatch-ambiguity predicate) must reference this
# constant, never retype the literal; a contract test ties the
# constant to the fallback behaviour.
UNRESOLVED_TASK_ID = "unknown"

# Hard bound on corrective follow-up dispatches per Mission
# Authorization, never derived from input. Ruling R-2: this is an
# AUTHORIZATION-SCOPE bound (how much corrective dispatch one human
# approval covers), NOT a Herdr review-round limit and NOT a mission
# timeout. Exceeding it transitions the workflow to
# NEEDS_REAUTHORIZATION (durable, visible), never a stranded dead end.
MAX_FOLLOW_UP_DISPATCHES = 2

DISPATCH_RECEIPT_MARKER = "dispatched handoff revision"
# The marker for the bounded failed-acceptance evidence a verification
# turn records when it requests a corrective follow-up.
CORRECTION_RECEIPT_MARKER = "correction requested after verification"
# The marker for the dispatch-time protected-surface baseline receipt
# (ruling R-2): its digest is the framed digest of the control
# repository's protected surfaces AT DISPATCH, and verification later
# requires the live recomputation to byte-match it. Stamped exactly
# once, at the INITIAL dispatch (the semantic anchor is "the control
# machinery the child ran under"; comparing against the FIRST
# dispatch detects any drift across the whole execution window,
# follow-ups included). A workflow with no such receipt was
# dispatched before the baseline existed and fails closed at
# verification — never retro-fitted, never fabricated.
SURFACE_RECEIPT_MARKER = "protected-surface baseline at dispatch"


def surface_receipt(entry, digest, now, turn_id_factory=None):
    """The E-5 receipt binding the dispatch-time protected-surface
    digest. Capability-free: a digest, exact counts nowhere (they
    live in the digest computation), no path."""
    make_turn_id = turn_id_factory or (
        lambda: "surf-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": digest,
        "bounded_summary": "%s (framed sha256, exact)" % (
            SURFACE_RECEIPT_MARKER
        ),
    }


def surface_baseline_digest(entry):
    """The dispatch-time protected-surface baseline digest, or None
    when no such receipt exists (a pre-baseline workflow — the
    verification gates fail closed on None; nothing is ever
    fabricated). The FIRST stamped receipt wins: the baseline is a
    dispatch-time datum and is never re-stamped."""
    for receipt in entry["receipts"]:
        if receipt.get("kind") == "evidence" and receipt.get(
            "bounded_summary", ""
        ).startswith(SURFACE_RECEIPT_MARKER):
            return receipt.get("digest")
    return None


def build_spawn_request(entry):
    """The complete four-field spawn request.

    Target, task, and alias are resolved from the protected record;
    the unattended permission posture is the fixed DI-owned Runtime
    constant.
    """
    return {
        "target_repo": entry["workspace_lease"]["path_realpath"],
        "task": entry["handoff"]["text"],
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "preset": DI_TARGET_EXECUTION_PRESET,
    }


def target_identity_from_spawn(spawn_result, entry, now):
    """The durable target-Herdr identity, bounded, from the spawn
    result (D1). The real bridge returns the task identity twice:
    ``task.id`` and ``child_record.task_id``. Both must be present,
    non-empty strings and agree exactly; otherwise the identity stays
    unresolved. Alias is display-only and is never identity evidence.
    Never stores a capability or a raw result blob."""
    result = spawn_result if isinstance(spawn_result, dict) else {}

    def _bounded(value, fallback):
        if isinstance(value, str) and value.strip():
            return value[:128]
        return fallback

    task = result.get("task")
    child_record = result.get("child_record")
    task_id = task.get("id") if isinstance(task, dict) else None
    recorded_task_id = (
        child_record.get("task_id")
        if isinstance(child_record, dict) else None
    )
    usable_task_id = (
        task_id
        if isinstance(task_id, str)
        and task_id.strip()
        and isinstance(recorded_task_id, str)
        and recorded_task_id.strip()
        and task_id == recorded_task_id
        else UNRESOLVED_TASK_ID
    )

    return {
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "task_id": _bounded(usable_task_id, UNRESOLVED_TASK_ID),
        "repo": _bounded(
            result.get("repo"),
            entry["target"]["canonical_url"],
        ),
        "dispatched_at": now,
    }


def latest_correction_evidence(entry):
    """The most recent failed-acceptance evidence summary a
    verification turn recorded, or None."""
    for receipt in reversed(entry["receipts"]):
        summary = receipt.get("bounded_summary", "")
        if receipt.get("kind") == "evidence" and summary.startswith(
            CORRECTION_RECEIPT_MARKER
        ):
            return summary
    return None


def build_follow_up_spawn_request(entry):
    """The corrective follow-up spawn request (D6).

    The ``task`` is a CORRECTIVE BRIEF assembled ONLY from the
    record's own already-validated authority fields (objective,
    constraints, acceptance, desired outcome — none of which may
    carry strategy) plus the failed-acceptance evidence a
    verification turn recorded. It carries NO technical solution: the
    text is built from a FIXED template with authority values slotted
    in, so no engineering plan can be introduced here — planning
    returns to the target Supervisor, which remains the first
    strategy-bearing component. Still exactly four fields, including
    the same fixed DI-owned execution posture as the initial dispatch.
    """
    authorization = entry["mission_authorization"]
    correction = latest_correction_evidence(entry) or (
        "verification requested a bounded correction"
    )
    task = (
        "CORRECTIVE FOLLOW-UP for an already-authorized mission. This"
        " is authority and boundaries only — NOT an engineering plan;"
        " the Supervisor owns all technical decisions.\n"
        "\nCORRECTIVE OBJECTIVE\n%s\n"
        "\nFAILED ACCEPTANCE EVIDENCE\n%s\n"
        "\nUNCHANGED CONSTRAINTS\n%s\n"
        "\nDESIRED CORRECTED OUTCOME\n%s\n"
        "\nACCEPTANCE (unchanged)\n%s\n"
    ) % (
        authorization["objective"],
        correction,
        authorization["constraints"],
        authorization["desired_outcome"],
        authorization["acceptance"],
    )
    return {
        "target_repo": entry["workspace_lease"]["path_realpath"],
        "task": task,
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "preset": DI_TARGET_EXECUTION_PRESET,
    }


def dispatch_count(entry):
    """Exact number of dispatches initiated for this workflow."""
    return sum(
        1 for receipt in entry["receipts"]
        if receipt["kind"] == "evidence"
        and receipt["bounded_summary"].startswith(
            DISPATCH_RECEIPT_MARKER
        )
    )


def dispatch_receipt(entry, now, sequence_number,
                     turn_id_factory=None):
    """The E-5 evidence receipt for one initiated dispatch.

    Capability-free: names the target by owner/repo and the handoff
    by revision and digest — never the workspace path or lease id.
    """
    make_turn_id = turn_id_factory or (
        lambda: "disp-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": entry["handoff"]["digest_sha256"],
        "bounded_summary": (
            "%s %d to %s/%s (dispatch %d, exact)" % (
                DISPATCH_RECEIPT_MARKER,
                entry["handoff"]["revision"],
                entry["target"]["owner"],
                entry["target"]["repo"],
                sequence_number,
            )
        ),
    }


class StartRefused(Exception):
    """Raised INSIDE the bridge, at a real start boundary, when the
    Mission start guard refuses to OPEN a start claim (Task 8 S-IV,
    correction A-2): nothing was started at the point it names.
    ``admission`` is the gate's refusal; ``point`` is
    ``START_POINT_RUNTIME`` (before the child runtime is created — zero
    processes) or ``START_POINT_TASK`` (before the objective is handed
    to an already-created runtime, which is then left IDLE and
    un-tasked; ``idle_runtime`` names its repository truthfully)."""

    def __init__(self, admission, point, idle_runtime=None):
        super(StartRefused, self).__init__(
            "start refused at %s: %s" % (point, admission.problem))
        self.admission = admission
        self.point = point
        self.idle_runtime = idle_runtime


class StartStopped(Exception):
    """Raised INSIDE the bridge after a start that WAS admitted and
    invoked, when its settlement found a stop requirement (a Mission
    write committed after the start's admission — ordered after it, so
    never "prevented" — a lapsed authority re-checked at settlement, or
    a non-completed engine outcome) and the guard performed the owned
    stop, OR when the settlement itself could not be recorded (the start
    stays UNSETTLED: ambiguous, never retried). ``closure`` is the
    guard's closure (``admission`` — the refusal; ``stopped`` — True only
    when absence was OBSERVED, False when the stop is pending;
    ``unsettled``; ``detail``). At ``START_POINT_RUNTIME`` the objective
    was never handed over; at ``START_POINT_TASK`` it was."""

    def __init__(self, point, closure):
        super(StartStopped, self).__init__(
            "start at %s admitted, then stop requested: %s"
            % (point, closure.admission.problem))
        self.point = point
        self.closure = closure


START_POINT_RUNTIME = "runtime_start"
START_POINT_TASK = "task_dispatch"
START_POINTS = (START_POINT_RUNTIME, START_POINT_TASK)

# The BOUND on each guarded engine call (the runtime start, the task
# hand-over): the engine's own commands (``herdr.runtime.run`` →
# ``subprocess.run`` without a timeout) are unbounded, so the bound is
# applied at THIS seam — the call runs in a worker thread and is
# ABANDONED when it outlasts the bound: its start is settled UNCERTAIN
# (a stop pends, nothing is retried) and whatever it returns later is
# handed to the guard's ``late_return`` for the owned stop and the
# canonical observation. Exact-value pinned in the bound-constant table.
START_WAIT_SECONDS = 600


def _bounded_call(call, point, start_guard):
    """Run ``call`` (one engine step) in a worker thread and wait at most
    ``start_guard.wait_seconds()``. Returns ``(result, abandoned)``; a
    call that raised re-raises here. An abandoned call keeps running:
    when it eventually returns (or raises) the guard's ``late_return``
    receives what it produced, so a runtime created after the bound is
    still owned, stopped and observed — never silently continued."""
    import threading
    box = {"done": False, "abandoned": False}
    lock = threading.Lock()

    def run():
        try:
            box["result"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised or handed over
            box["error"] = exc
        with lock:
            box["done"] = True
            abandoned = box["abandoned"]
        if abandoned:
            start_guard.late_return(point, box.get("result"), box.get("error"))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(start_guard.wait_seconds())
    with lock:
        if not box["done"]:
            box["abandoned"] = True
            return None, True
    if "error" in box:
        raise box["error"]
    return box["result"], False


def _guarded_control_plane(start_guard):
    """The production control plane with the canonical ENGAGEMENT START
    fused to its two process-affecting steps (Task 8 S-IV, start-claim
    decision: admitted-operation ordering, not a zero-physical-start
    guarantee). ``start_guard.open(point)`` is the atomic admission —
    the Mission core's ``open_engagement_start`` transaction persists one
    exact start under the Mission store lock and returns None, or
    returns the refusal (no start record, no invocation);
    ``start_guard.close(point, failed, result)`` — after the blocking
    engine call returns — settles the start canonically with the
    returned identity/outcome and the stop requirement derived at that
    instant, performs the owned stop OUTSIDE the lock when one is
    pending and returns the closure. A Mission write committed after the
    admission is ordered after it: the operation may already be in
    flight, and the outcome is "admitted, then stop required", never a
    silent continuation. The Mission lock is never held across the
    engine's start, hand-over, stop or waits. A crash or lost response
    between open and close leaves the start UNSETTLED in the Mission
    store: recovered as ambiguity, never blindly re-started; a start that
    never returns is bounded only by the engine's own waits and leaves
    the same unsettled state.

    - ``start``: the runtime-creation start point. Open refused → zero
      processes. Stop required at settlement → the runtime may exist, the
      objective is NOT handed over, the owned stop runs.
    - ``dispatch_task``: the task hand-over start point (its own start;
      it needs the runtime start settled completed with no stop
      requirement). Open refused → the runtime is left idle and un-tasked
      (stated). Stop required at settlement → the task was handed over,
      then the owned stop runs.

    BOUNDED WAIT (start-claim decision, items 3 and 5): each engine call
    runs under ``_bounded_call`` with ``START_WAIT_SECONDS``; a call that
    outlasts it is abandoned — settled UNCERTAIN with a stop pending, the
    Broker refuses durably, and the eventual return is handed to the
    guard's ``late_return`` (owned stop + canonical observation). STATED
    LIMITATION: the engine's own commands (``herdr.runtime.run``) carry no
    timeout, so an abandoned call's thread may outlive the bound inside
    this process; the bound limits how long a dispatch waits and what it
    may claim, not the engine's own execution."""
    from herdr.control_plane import HerdrControlPlane

    class GuardedControlPlane(HerdrControlPlane):

        def _guarded(self, point, call, idle_runtime=None):
            refusal = start_guard.open(point)
            if refusal is not None:
                raise StartRefused(refusal, point, idle_runtime)
            try:
                result, abandoned = _bounded_call(call, point, start_guard)
            except BaseException:
                # A returned error is settled FAILED (not absence proof);
                # the exception still surfaces to the Broker.
                start_guard.close(point, failed=True)
                raise
            if abandoned:
                # No response within the bound: settled UNCERTAIN, a stop
                # pends; the late return (if any) is owned and observed.
                closure = start_guard.close(point, result=None, abandoned=True)
                raise StartStopped(point, closure)
            closure = start_guard.close(point, result=result)
            if closure.stop_requested or closure.unsettled:
                raise StartStopped(point, closure)
            return result

        def start(self, repo, **kwargs):
            return self._guarded(
                START_POINT_RUNTIME,
                lambda: HerdrControlPlane.start(self, repo, **kwargs))

        def dispatch_task(self, repo, text, **kwargs):
            return self._guarded(
                START_POINT_TASK,
                lambda: HerdrControlPlane.dispatch_task(self, repo, text, **kwargs),
                idle_runtime=str(repo))

    return GuardedControlPlane()


def production_spawn(parent_repo, request, start_guard=None):
    """The real bridge; hermetic tests inject a recorder instead. With a
    ``start_guard`` (a Mission-origin dispatch) the bridge's control
    plane opens and closes the start claims at the real start boundaries
    (see ``_guarded_control_plane``); without one (a v2 record) the
    bridge is exactly what it was."""
    if start_guard is None:
        return execute_spawn_request(parent_repo, request)
    return execute_spawn_request(
        parent_repo, request, control_plane=_guarded_control_plane(start_guard))


def production_task_handover(request, start_guard):
    """Task 8 S-VII correction 2 (R1): hand ``request``'s EXACT objective to
    the runtime this dispatch ALREADY started — its runtime start was
    admitted, settled ``completed`` without a stop and proven owned by the
    caller; only the task point, whose claim was durably refused before any
    invocation, is left. It is the hand-over step ``spawn`` itself performs
    after its start (``dispatch_task`` on the same repository, same text),
    through the SAME guarded control plane: the canonical task start is
    opened (refused: nothing is handed over), the call is bounded, and the
    start is settled. The engine's start is never invoked. Returns
    ``{"repo", "task"}``. No control-repository child record is written:
    the spawn that writes it did not complete for this runtime."""
    repo = task_handover_target(request)
    task_state = _guarded_control_plane(start_guard).dispatch_task(
        repo, request["task"])
    return {"repo": str(repo), "task": task_state}


def task_handover_target(request):
    """The resolved target repository a task-only hand-over addresses — the
    ONE resolution ``production_task_handover`` uses, shared with the
    binding recovered from its canonical settlement (Task 8 S-VII
    correction 3), so the recovered identity names the same target."""
    from pathlib import Path
    return Path(request["target_repo"]).expanduser().resolve()


def target_identity_from_task(handover, entry, now):
    """The durable target identity after a task-only hand-over (Task 8
    S-VII correction 2, R1): the task id the engine RETURNED for it — a
    non-empty string, else unresolved — which the guard also settled
    canonically. Bounded exactly as ``target_identity_from_spawn``; never a
    capability or a raw result blob."""
    result = handover if isinstance(handover, dict) else {}
    task = result.get("task")
    task_id = task.get("id") if isinstance(task, dict) else None
    usable = (task_id if isinstance(task_id, str) and task_id.strip()
              else UNRESOLVED_TASK_ID)
    repo = result.get("repo")
    return {
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "task_id": usable[:128],
        "repo": (repo[:128] if isinstance(repo, str) and repo.strip()
                 else entry["target"]["canonical_url"]),
        "dispatched_at": now,
    }
