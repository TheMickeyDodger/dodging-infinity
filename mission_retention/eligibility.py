"""Retention eligibility: when a Mission's local data may be archived, reclaimed or deleted.

ONE rule, in TWO halves, both required (Deliverable 2, Addendum A and the Lead's ruling):

  (a) a SOURCE-GROUNDED POSITIVE BASIS, read from the source record itself, that EITHER the
      Mission's execution ENDED, OR, by a source-qualified ordering, it NEVER DISPATCHED;
  (b) the record is TERMINAL (nothing can transition it again).

Nothing else stands in for (a): not a closure flag, not a descriptive progress report, not a fresh
catalog answer, not an empty roster, not a reported completion without success. Without a basis a
record is NOT eligible and keeps its uncertainty with its reason.

This is the backend's own re-expression of the Deliverable 1 capacity-release bases
(``integrations/pixel-agents/webview-ui/src/components/diMissionStatus.ts`` ``DI_CAPACITY_RELEASE``),
rechecked against the trusted Python records at this layer: the layering forbids importing the
webview, so the two are kept in step by a test that pins this table to that one, the way
``DI_MEMBER_ACTIVITY_WORK`` is pinned to ``mission_sources/contract.py``. Each basis names the
source field that supplies it, and why that field can be trusted:

Mission Core (``missions.json``):
- ``verified_target_stopped``: ``state = "COMPLETED"``. Mission Core records COMPLETED only from a
  verification whose every conjunct held, ``target_stopped`` included (``mission/record.py``
  ``VERIFY_CONJUNCTS``).
- ``cancel_quiescent``: ``run.cancel.quiescence`` is ``nothing_started``, ``owned_group_reaped`` or
  ``task_observed_stopped`` (``mission/record.py`` ``CANCEL_QUIESCENCE_*``), written by the bridge's
  cancel completion (``target_runtime/mission_bridge.py`` ``_complete_cancel``) only from an
  affirmative observation. ``unproven`` is NOT a basis. The bridge's ``control`` value
  ``owned_group_already_gone`` is recorded with quiescence ``owned_group_reaped`` (the owned group
  is observed absent) and is read through that quiescence, never through ``control``.
- ``no_start_recorded``: ``run.intent`` absent. The bridge records the run intent durably BEFORE its
  only spawn (``mission_bridge.py``: "The intent is durable. From here on an unknown outcome is a
  HOLD."), so an absent intent is a recorded fact that nothing was dispatched.

Workflow store (``workflows.json``):
- ``workflow_target_stopped``: ``phase = "COMPLETED"``. COMPLETED is reached only from VERIFIED, and
  the broker's one VERIFIED write follows its observation that the target task stopped
  (``target_runtime/broker.py`` ``_verify``: ``target_complete``).
- ``workflow_not_dispatched``: ``phase = "BLOCKED"`` with NO dispatch receipt in ``receipts``. The
  broker appends the dispatch receipt together with the DISPATCHED transition and saves both BEFORE
  the external spawn (``broker.py`` "Durable BEFORE the external spawn."), so a BLOCKED record
  without one never dispatched. (A workflow's ``target_engine`` is NOT evidence: it is recorded AFTER
  the spawn, so a post-effect exception leaves BLOCKED with none and work started.)

Record terminal eligibility (b) is EITHER the catalog's own terminal standing
(``mission_sources/sources.py`` ``MISSION_CORE_STANDING`` / ``WORKFLOW_STORE_STANDING``), OR, for
Mission Core, the record's own TERMINAL Mission State PROGRESS (``mission_state[id].progress`` in
``mission/state.py`` ``TERMINAL_PROGRESS_STATES``): the same closure Deliverable 1 files a record
closed by (``recordClosed``), re-derived here from the Python record, never taken from the office.
A WITHDRAWN proposal (``withdrawal`` present) is final too: Mission Core refuses every later decision
on it (``manifest.py`` ``_validate_withdrawal``).

(c) FUTURE-DISPATCH SAFETY, required whenever (b) rests on progress rather than on a terminal
standing (a terminal standing has no outgoing transition, so nothing can authorize it again).
Terminal progress refuses every later state operation, a NEW contract activation included
(``mission/state_service.py`` ``_apply``: "terminal is terminal"); but the bridge SKIPS activation
when the contract is already active at the dispatch revision (``target_runtime/mission_bridge.py``
``_activate_contract`` returns early), and the run intent records no progress check. So a record is
eligible on progress closure only when NO activation exists at its current revision
(``mission_state[id].contract_activations``, via ``mission/state.py`` ``latest_activation``): any
dispatch would then have to activate, which terminal progress refuses. Revisions only ever grow, so
an activation at an earlier revision can never satisfy that early return.

Every answer is an ``Eligibility``: eligible with its basis, source field and the source time that
dates it, or not eligible with a distinct reason. Pure: it reads only the records it is handed.
"""

from collections import namedtuple

from mission import record as mission_record
from mission import state as mission_state

BASIS_VERIFIED_TARGET_STOPPED = "verified_target_stopped"
BASIS_CANCEL_QUIESCENT = "cancel_quiescent"
BASIS_NO_START_RECORDED = "no_start_recorded"
BASIS_WORKFLOW_TARGET_STOPPED = "workflow_target_stopped"
BASIS_WORKFLOW_NOT_DISPATCHED = "workflow_not_dispatched"

# The bases per source, pinned equal to the Deliverable 1 TypeScript table by
# tests/test_mission_retention.py (``workflow_not_dispatched`` here reads the durable dispatch
# receipt, the Python-side counterpart of the wire's undispatched-phase basis).
MISSION_CORE_BASES = (BASIS_VERIFIED_TARGET_STOPPED, BASIS_CANCEL_QUIESCENT, BASIS_NO_START_RECORDED)
WORKFLOW_BASES = (BASIS_WORKFLOW_TARGET_STOPPED, BASIS_WORKFLOW_NOT_DISPATCHED)

# The cancel quiescence values that AFFIRMATIVELY establish the target stopped or never started.
QUIESCENT_CANCELS = (
    mission_record.CANCEL_QUIESCENCE_NOTHING_STARTED,
    mission_record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
    mission_record.CANCEL_QUIESCENCE_TASK_OBSERVED_STOPPED,
)

# The catalog's own standing tables (``mission_sources/sources.py`` ``MISSION_CORE_STANDING`` and
# ``WORKFLOW_STORE_STANDING``), re-declared and pinned equal by tests/test_mission_retention.py:
# retention is not a consumer of ``mission_sources`` (tests/test_static.py pins who is), and the
# catalog may then read retention without an import cycle.
STANDING_ACTIVE = "active"
STANDING_INACTIVE = "inactive"
STANDING_TERMINAL = "terminal"
MISSION_CORE_STANDING = {
    "AWAITING_DECISION": STANDING_INACTIVE, "DENIED": STANDING_INACTIVE,
    "AUTHORIZED": STANDING_ACTIVE, "RUNNING": STANDING_ACTIVE,
    "BLOCKED": STANDING_TERMINAL, "COMPLETED": STANDING_TERMINAL,
    "CANCELLED": STANDING_TERMINAL, "CLOSED": STANDING_TERMINAL,
    "REJECTED": STANDING_TERMINAL, "CHANGES_REQUESTED": STANDING_INACTIVE,
}
WORKFLOW_STORE_STANDING = {
    "PLANNED": STANDING_ACTIVE, "NEEDS_REAUTHORIZATION": STANDING_ACTIVE,
    "AUTHORIZED": STANDING_ACTIVE, "WORKSPACE_READY": STANDING_ACTIVE,
    "PREPARED": STANDING_ACTIVE, "VALIDATED": STANDING_ACTIVE,
    "DISPATCHED": STANDING_ACTIVE, "VERIFIED": STANDING_ACTIVE,
    "COMPLETED": STANDING_TERMINAL, "BLOCKED": STANDING_TERMINAL,
}
# The workflow record literals read here (``workflow_authority/record.py``), pinned equal by test.
WORKFLOW_COMPLETED = "COMPLETED"
WORKFLOW_BLOCKED = "BLOCKED"
WORKFLOW_DISPATCHED = "DISPATCHED"
RECEIPT_KIND_EVIDENCE = "evidence"

# Re-declared from target_runtime/dispatch.py (``DISPATCH_RECEIPT_MARKER``, ``dispatch_count``): the
# runtime package pulls in the Herdr orchestrator, so it is not imported here; the focused suite
# pins parity with the runtime's own constant and predicate.
DISPATCH_RECEIPT_MARKER = "dispatched handoff revision"

# Refusal reasons: distinct, so the refusal matrix can name each row.
REASON_NOT_TERMINAL = "not_terminal"
REASON_RUNNING = "running"
REASON_UNKNOWN_STATE = "unknown_state"
REASON_NO_POSITIVE_BASIS = "no_positive_basis"
REASON_DISPATCH_POSSIBLE = "dispatch_still_possible"

Eligibility = namedtuple(
    "Eligibility",
    ("eligible", "basis", "basis_source", "terminal_at", "terminal_at_source", "reason", "detail"))


def _no(reason, detail):
    return Eligibility(False, None, None, None, None, reason, detail)


def _yes(basis, basis_source, terminal_at, terminal_at_source):
    return Eligibility(True, basis, basis_source, terminal_at, terminal_at_source, None, None)


def _timestamp(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _terminal_event_at(mission):
    """When the Mission entered its CURRENT terminal state, from its own lifecycle chain or the
    decision that produced it; None when the record dates nothing trustworthy."""
    state = mission.get("state")
    for event in reversed(mission.get("lifecycle") or []):
        if event.get("to_state") == state:
            at = _timestamp(event.get("recorded_at"))
            if at is not None:
                return at, "lifecycle[to_state = %s].recorded_at" % state
    for decision in reversed(mission.get("decisions") or []):
        outcome = decision.get("outcome") or {}
        if outcome.get("resulting_state") == state:
            at = _timestamp(decision.get("decided_at"))
            if at is not None:
                return at, "decisions[resulting_state = %s].decided_at" % state
    return None, None


def _progress_closure(mission_state_record):
    """``(terminal progress, closed_at)`` from the Mission State record, or ``(None, None)``."""
    if not isinstance(mission_state_record, dict):
        return None, None
    progress = mission_state_record.get("progress")
    if progress not in mission_state.TERMINAL_PROGRESS_STATES:
        return None, None
    closure = mission_state_record.get("closure") or {}
    at = _timestamp(closure.get("closed_at")) if closure.get("progress") == progress else None
    return progress, at


def mission_core(mission, mission_state_record=None):
    """Eligibility of one Mission Core record (a ``missions.json`` Mission), with its Mission State
    record (``missions.json`` ``mission_state[mission_id]``, or None when it has none)."""
    state = mission.get("state")
    if state not in MISSION_CORE_STANDING:
        return _no(REASON_UNKNOWN_STATE, "Mission Core state %r is not one this layer knows" % (state,))
    run = mission.get("run") or {}
    withdrawal = mission.get("withdrawal")
    standing = MISSION_CORE_STANDING[state]
    if withdrawal is not None:
        # A withdrawn proposal: Mission Core refuses every later decision on it, it never had an
        # authorization (manifest.py ``_validate_withdrawal``), so it can never record a run intent.
        if run.get("intent") is not None:
            return _no(REASON_NO_POSITIVE_BASIS, "a withdrawn Mission carries a run intent")
        at = _timestamp(withdrawal.get("withdrawn_at"))
        return _yes(BASIS_NO_START_RECORDED, "run.intent = null", at,
                    None if at is None else "withdrawal.withdrawn_at")
    if standing != STANDING_TERMINAL:
        if state == mission_record.STATE_RUNNING:
            return _no(REASON_RUNNING, "the Mission is RUNNING")
        progress, closed_at = _progress_closure(mission_state_record)
        if progress is None:
            return _no(REASON_NOT_TERMINAL, "the Mission is %s, not finished or cancelled" % state)
        # (b) by progress closure: (c) and (a) must both still hold.
        activation = mission_state.latest_activation(mission_state_record)
        if activation is not None and activation.get("revision") == mission.get("current_revision"):
            return _no(REASON_DISPATCH_POSSIBLE,
                       "its Mission State progress is %s, but its proof contract is active at"
                       " revision %s, so a dispatch would skip the activation that terminal"
                       " progress refuses" % (progress, activation.get("revision")))
        if run.get("intent") is not None:
            return _no(REASON_NO_POSITIVE_BASIS,
                       "its Mission State progress is %s, but a run intent is recorded and"
                       " nothing in its record establishes that the run stopped" % progress)
        return _yes(BASIS_NO_START_RECORDED, "run.intent = null", closed_at,
                    None if closed_at is None else "mission_state.closure.closed_at")
    entered_at, entered_source = _terminal_event_at(mission)
    if state == mission_record.STATE_COMPLETED:
        return _yes(BASIS_VERIFIED_TARGET_STOPPED, 'state = "COMPLETED"', entered_at, entered_source)
    cancel = run.get("cancel")
    if state == mission_record.STATE_CANCELLED and cancel is not None:
        quiescence = cancel.get("quiescence")
        completed_at = _timestamp(cancel.get("completed_at"))
        if quiescence in QUIESCENT_CANCELS and completed_at is not None:
            # dated by the observation that established it, never by the earlier cancel request
            return _yes(BASIS_CANCEL_QUIESCENT, 'run.cancel.quiescence = "%s"' % quiescence,
                        completed_at, "run.cancel.completed_at")
    if run.get("intent") is None:
        return _yes(BASIS_NO_START_RECORDED, "run.intent = null", entered_at, entered_source)
    return _no(REASON_NO_POSITIVE_BASIS,
               "the Mission is %s after a run intent was recorded, and nothing in its record"
               " establishes that its target stopped" % state)


def dispatch_count(workflow):
    """The broker's own count of dispatches initiated (target_runtime/dispatch.py, pinned), or
    ``None`` when the record carries no readable receipts list (round 10: an absent list is NOT
    zero dispatches; it establishes nothing)."""
    receipts = workflow.get("receipts")
    if not isinstance(receipts, list) or not all(isinstance(r, dict) for r in receipts):
        return None
    return sum(
        1 for receipt in receipts
        if receipt.get("kind") == RECEIPT_KIND_EVIDENCE
        and str(receipt.get("bounded_summary") or "").startswith(DISPATCH_RECEIPT_MARKER))


def workflow(record):
    """Eligibility of one workflow-store record (a ``workflows.json`` workflow)."""
    phase = record.get("phase")
    if phase not in WORKFLOW_STORE_STANDING:
        return _no(REASON_UNKNOWN_STATE, "workflow phase %r is not one this layer knows" % (phase,))
    if WORKFLOW_STORE_STANDING[phase] != STANDING_TERMINAL:
        if phase == WORKFLOW_DISPATCHED:
            return _no(REASON_RUNNING, "the workflow is DISPATCHED")
        return _no(REASON_NOT_TERMINAL, "the workflow is %s, not finished" % phase)
    # A workflow record dates no terminal transition (approval.created_at dates the approval, never
    # completion), so the caller dates it by its first local observation of this eligibility.
    if phase == WORKFLOW_COMPLETED:
        return _yes(BASIS_WORKFLOW_TARGET_STOPPED, 'phase = "COMPLETED"', None, None)
    if phase == WORKFLOW_BLOCKED and dispatch_count(record) == 0:     # never None: not known is no basis
        return _yes(BASIS_WORKFLOW_NOT_DISPATCHED, "receipts: no dispatch receipt", None, None)
    return _no(REASON_NO_POSITIVE_BASIS,
               "the workflow is BLOCKED after a dispatch, and nothing in its record establishes"
               " that its target stopped")
