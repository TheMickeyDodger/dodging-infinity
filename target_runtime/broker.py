"""The narrow Target Broker: fixed deterministic lifecycle actions.

Every action's signature is exactly ``(workflow_id, action,
revision)`` — plan D-3 made structural: paths, remote URLs, baselines
and handoff bytes are resolved by the Broker FROM the protected
workflow record; a caller has no parameter through which to supply a
sensitive value. Unknown actions fail closed.

Every ``perform`` runs the SAME fail-closed gate before any effect,
in a fixed order (each with its own problem code, the eleven-case
adversarial matrix's Runtime seam):

unknown action -> unknown workflow -> tampered record (closed-schema
re-validation of RECOVERED DURABLE STATE, which is adversarial
input; validate_record's TOTAL render binding refuses any field
altered independently of the digested text — wrong-revision,
altered-baseline, either target form edited into the other, altered
human intent or authority sections) -> control mismatch -> LIVE
policy-digest drift (D-9: the digest is re-computed from the control
repository's actual bytes on every perform) -> stale revision
argument -> approval not consumed / not approved / superseded
(pre-approval) -> consumption outside the approval validity
(expired) -> crash ambiguity -> wrong phase for the action (replay).
A refusal writes NOTHING: no store save, no workspace touch, no
subprocess, no Codex turn.
"""

import collections
import hashlib
import json
import os

from codex_gateway.role_turn import ROLE_TURN_COMPLETED
from telegram_operator.protocol import (
    MAX_OUTCOME_DETAIL_CHARS,
    OUTCOME_NEEDS_REAUTHORIZATION,
    OUTCOME_REQUEST_DISPATCH,
    OUTCOME_REQUEST_FOLLOW_UP,
    OUTCOME_VERIFIED_RESULT,
)
from workflow_authority import record as record_module
from workflow_authority import store as store_module
from workflow_authority.digest import (
    DigestError,
    control_policy_digest,
    text_digest,
)
# Task 8 S-IV: the effect-boundary Mission gate's vocabulary (boundary
# names and refusal classifications). The gate INSTANCE is injected; this
# module never builds one and never imports the neutral Mission core.
from mission_control import gate as mission_gate_module
# Task 8 S-V (R2-11-b): the ONE candidate identity authority — the P1-A6
# pure candidate module (``parse_raw_z``, ``identity_digest`` and its
# typed error only; no store, transport, machine or authorization). Pinned
# narrow in tests/test_static.py.
from pr_delivery import candidate as candidate_module

# Target-Herdr *lifecycle* statuses that mean the engineering task has
# STOPPED and there is an outcome to verify — the trigger for the
# verification turn.
#
# Read from herdr.observe's task["status"] projection (the task
# LIFECYCLE), NOT task["state"], which is FILE READABILITY of
# task.json ("available"/"missing"/"malformed") — a distinct closed
# vocabulary that says nothing about whether the target finished.
#
# The value set is herd's OWN stopped set, NOT a hand-picked subset:
# herdr/tasks.py:286 treats {"COMPLETE", "ABORTED", "ERROR"} alike as
# "the prior task has stopped" before starting new work. Each is a
# real write site:
#   COMPLETE  herdctl `complete` (herdctl.py) — finished successfully;
#   ABORTED   herdctl `abort`    (herdctl.py) — a human stopped it;
#   ERROR     herdr/tasks.py     — the task failed.
# All three are stopped and hand the outcome to the verification turn,
# which adjudicates success vs. a corrective follow-up vs. a durable
# re-authorization — none may be left waiting. The running/pre-start
# states IDLE and ACTIVE are the ONLY non-terminal values: the
# workflow WAITS with zero store write. No status is deliberately
# excluded; every value herd can write is either terminal here or a
# legitimate wait. A contract test (tests/test_target_runtime.py)
# DERIVES this set from herd's own source (herdr/tasks.py) and drives
# the real herdr.observe over EVERY status, so an omission — herd
# adding a stopped status, or this pair narrowing — fails the suite
# instead of stranding a workflow in production.
_TARGET_TERMINAL_STATUSES = ("COMPLETE", "ABORTED", "ERROR")

# herdr.observe's `completeness` is visibility-only ("COMPLETE" when
# every consulted source was cleanly observed, "PARTIAL" when any was
# malformed/unreadable/unavailable). Since I3 of task 20260826-113247
# the completion decision is SOURCE-SCOPED per ruling R-6 (see
# `_observation_context`): a demoting diagnostic in a CONSUMED source
# is a WAIT, never a finish, while a production observation that is
# globally PARTIAL only because agents are unprobed still advances.
# The global value itself remains recorded/rendered raw everywhere.


def _bounded_detail(result):
    """The verification turn's detail string, bounded (never a raw
    unbounded value into a stored summary)."""
    detail = getattr(result, "detail", None)
    if isinstance(detail, str):
        return detail[:MAX_OUTCOME_DETAIL_CHARS]
    return None

from workflow_authority import canonical as canonical_module

from target_runtime import dispatch as dispatch_module
from target_runtime import evidence as evidence_module
from target_runtime import prepare as prepare_module
from target_runtime import ownership as ownership_module
from target_runtime import readiness as readiness_module
from target_runtime import workspace as workspace_module
from target_runtime import workspace_trust as workspace_trust_module
from target_runtime.capability_authority import RuntimeCapabilityAuthority
from target_runtime import worker as worker_module
from target_runtime.worker import RuntimeWorker

ACTION_MATERIALIZE = "materialize_workspace"
ACTION_PREPARE = "prepare"
ACTION_VALIDATE_HANDOFF = "validate_handoff"
ACTION_DISPATCH = "dispatch"
ACTION_VERIFY = "verify"
ACTION_FOLLOW_UP = "dispatch_follow_up"
ACTION_COMPLETE = "complete"
ACTION_RELEASE = "release_workspace"
ACTION_RECONCILE = "reconcile_dispatch"
BROKER_ACTIONS = (
    ACTION_MATERIALIZE,
    ACTION_PREPARE,
    ACTION_VALIDATE_HANDOFF,
    ACTION_DISPATCH,
    ACTION_VERIFY,
    ACTION_FOLLOW_UP,
    ACTION_COMPLETE,
    ACTION_RELEASE,
    ACTION_RECONCILE,
)

# The phase each action requires; anything else is a replay or an
# out-of-order operation and fails closed. Dispatch requires
# VALIDATED (cleared to dispatch); a second `dispatch` on a
# DISPATCHED workflow is a wrong-phase refusal — double dispatch is
# structurally impossible. Verify and follow-up require DISPATCHED;
# complete requires VERIFIED.
_REQUIRED_PHASE = {
    ACTION_MATERIALIZE: record_module.PHASE_AUTHORIZED,
    ACTION_PREPARE: record_module.PHASE_WORKSPACE_READY,
    ACTION_VALIDATE_HANDOFF: record_module.PHASE_PREPARED,
    ACTION_DISPATCH: record_module.PHASE_VALIDATED,
    ACTION_VERIFY: record_module.PHASE_DISPATCHED,
    ACTION_FOLLOW_UP: record_module.PHASE_DISPATCHED,
    ACTION_COMPLETE: record_module.PHASE_VERIFIED,
    ACTION_RELEASE: None,  # release is phase-checked in its handler
    ACTION_RECONCILE: record_module.PHASE_DISPATCHED,
}


def _production_observer(lease_repo):
    """The production read-only Herdr observation (I5 D2).

    Wired lazily so target_runtime keeps its single herdr import at
    the dispatch bridge; `herdr.observe` is the read-only projection.
    """
    from herdr.observe import observe
    return observe(lease_repo, probe_agents=False)


def _production_spawn_records_observer(control_repo, relevant=None):
    """The production read-only control-repository spawn projection
    (``relevant``: scope it to one resource's records, see
    ``observe_spawn_records``)."""
    from herdr.observe import observe_spawn_records
    return observe_spawn_records(control_repo, relevant=relevant)


def _runtime_state_path(lease):
    """The target herd's persisted runtime state in the lease — the file
    ``herdr.lifecycle.start_herd`` reads before it starts."""
    return os.path.join(lease, ".herd", "state", "runtime.json")


#: The largest persisted runtime state read (it is a few hundred bytes).
MAX_RUNTIME_STATE_BYTES = 65536


def _lease_runtime_state(lease):
    """``(state, None)``; ``(None, None)`` ONLY when the file is genuinely
    absent; or ``(None, (terminal, why))``. ``state`` carries the exact
    bytes, their sha256, and the workspace id and supervisor the native
    start acts on — the id derived EXACTLY as ``start_herd`` derives
    ``old_workspace`` (a truthy ``workspace_id`` field, else the
    ``<workspace>:`` prefix of the ``panes.supervisor`` pane: the LEGACY
    shape, kept), the supervisor from ``agents.supervisor`` as its liveness
    probe reads it.

    A state that EXISTS but whose identity is not projectable — no usable
    workspace id (missing, empty, or no ``<workspace>:`` pane), a truthy
    non-string id, no supervisor or a non-string one, an ``agents`` or
    ``panes`` field that is not an object, a ``repo`` naming another
    directory — is a contradiction, never "no state": discarding it could
    hide the native live-supervisor refusal, and its ownership cannot be
    established. Unreadable is recoverable; over-bound or malformed is a
    contradiction."""
    try:
        with open(_runtime_state_path(lease), "rb") as handle:
            data = handle.read(MAX_RUNTIME_STATE_BYTES + 1)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, (False, "is unreadable (%s)" % exc.__class__.__name__)
    if len(data) > MAX_RUNTIME_STATE_BYTES:
        return None, (True, "exceeds %d bytes" % MAX_RUNTIME_STATE_BYTES)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, (True, "is not valid JSON")
    if not isinstance(document, dict):
        return None, (True, "is not a JSON object")
    agents = document.get("agents", {})
    panes = document.get("panes", {})
    if not isinstance(agents, dict) or not isinstance(panes, dict):
        return None, (True, "has an agents or panes field that is not an object; its"
                            " identity is not projectable")
    repo = document.get("repo")
    if repo is not None and not (isinstance(repo, str)
                                 and os.path.realpath(repo) == os.path.realpath(lease)):
        return None, (True, "names repository %r, not this lease" % (repo,))
    workspace_id = document.get("workspace_id")
    if workspace_id and not isinstance(workspace_id, str):
        return None, (True, "names a workspace id that is not a string (%r)"
                            % (workspace_id,))
    if not workspace_id:
        pane = panes.get("supervisor", "")
        if not isinstance(pane, str):
            return None, (True, "has a supervisor pane that is not a string")
        workspace_id = pane.split(":", 1)[0] if ":" in pane else ""
    if not workspace_id:
        return None, (True, "names no projectable workspace id (no workspace_id and no"
                            " '<workspace>:' supervisor pane); its ownership cannot be"
                            " established")
    supervisor = agents.get("supervisor")
    if not (isinstance(supervisor, str) and supervisor):
        return None, (True, "names no usable supervisor agent (%r); its ownership cannot"
                            " be established" % (supervisor,))
    return {
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "workspace_id": workspace_id,
        "supervisor": supervisor,
    }, None


def _names_lease(lease):
    """The relevance rule that scopes the spawn records to ONE lease: a
    record's ``repo`` string names it when both resolve equal (realpath on
    both sides) — exactly the comparison every ownership matcher applies
    (``_reconcile``, ``owns_workspace``, the canonical child rule), so the
    scoped projection holds every record any of them could match, whatever
    it states about its task, and a differently spelled path to the same
    directory is not missed. Only the path string is resolved: nothing
    under any child repository is opened or read."""
    target = os.path.realpath(lease)

    def names(repo):
        return os.path.realpath(repo) == target
    return names

#: I5: a release that completed what it could PROVE it owned while
#: leaving unprovable resources untouched. Distinct from a plain
#: release so /status can tell a complete cleanup from a partial one
#: without parsing prose.
OUTCOME_RELEASED_DEGRADED = "released_degraded"

#: Fixed marker for the cleanup receipt, so a reader of the durable
#: record can find what a release actually removed.
CLEANUP_RECEIPT_MARKER = "workflow cleanup"

PROBLEM_UNKNOWN_ACTION = "broker_unknown_action"
PROBLEM_UNKNOWN_WORKFLOW = "broker_unknown_workflow"
PROBLEM_STORE_UNREADABLE = "broker_store_unreadable"
PROBLEM_RECORD_INVALID = "broker_record_invalid"
# Task 8 S-III: the Mission-origin workflow kind is NOT ENABLED in this
# Broker, in every constructor: every action on such a record is
# refused at the gate (no handler runs, nothing is written to the
# record, no spawn, no release). Slice S-IV replaces this refusal with
# the gated path.
PROBLEM_MISSION_KIND_NOT_ENABLED = "broker_mission_kind_not_enabled"
# Task 8 S-IV: the Mission gate's durable refusals, recorded as fixed-
# marker E-5 receipts. A HOLD receipt (reversible cause: at most one per
# cause, phase preserved) and a BLOCK receipt (terminal invalidation:
# locked PHASE_BLOCKED transition plus receipt).
MISSION_HOLD_RECEIPT_MARKER = "mission gate hold"
MISSION_BLOCK_RECEIPT_MARKER = "mission gate block"
# The workflow-side receipt of an engagement start (start-claim decision);
# the marker is the record layer's, which reads these receipts for the
# retention rule (an unresolved start is never pruned or released).
MISSION_START_RECEIPT_MARKER = record_module.MISSION_START_RECEIPT_MARKER
MISSION_CLAIM_RECEIPT_MARKER = record_module.MISSION_CLAIM_RECEIPT_MARKER
# Task 8 S-V (R2-11-a / R2-11-b): the Runtime's OWN durable observations,
# one receipt each — every review round the target's review process
# produced, and every CHANGED observation of the delivery candidate (its
# P1-A6 identity, exactness status, base and HEAD). Their vocabulary,
# writers and readers live in ONE pure module shared with the
# reconciliation bridge that reports them to the Mission Core; they are
# observations the Runtime holds, not acceptance.
from mission_control.observation_receipts import (  # noqa: E402
    CANDIDATE_RECEIPT_MARKER,
    CANDIDATE_STATUS_EXACT,
    CANDIDATE_STATUS_NOT_EXACT,
    CANDIDATE_STATUS_UNAVAILABLE,
    CANDIDATE_STATUSES,
    PROBLEM_CANDIDATE_CAPTURE,
    PROBLEM_CANDIDATE_NOT_EXACT,
    PROBLEM_CANDIDATE_RECEIPT_TAMPERED,
    REVIEW_ROUND_RECEIPT_MARKER,
    candidate_receipt,
    head_commit_digest,
    observed_candidate,
    observed_review_rounds,
    parse_candidate_receipt,
    porcelain_outside_candidate,
    review_listing_receipt,
    review_round_reading,
    review_round_receipt,
    listing_statement,
    same_candidate_observation,
    same_review_listing,
    MAX_RECEIPT_NUMBER,
)
PROBLEM_RETENTION_PROTECTED = "broker_retention_protected"
OUTCOME_MISSION_HELD = "mission_held"
OUTCOME_MISSION_BLOCKED = "mission_blocked"
OUTCOME_RECOVERY_PASSED = "mission_recovery_passed"
OUTCOME_RETENTION_RELEASED = "retention_released"
OUTCOME_RETENTION_RETAINED = "retention_retained"
OUTCOME_CANDIDATE_OBSERVED = "candidate_observed"
OUTCOME_CANDIDATE_UNCHANGED = "candidate_unchanged"
OUTCOME_CANDIDATE_NOT_OBSERVED = "candidate_not_observed"

# Task 8 S-V: the Runtime pass's maintenance operations (``maintain``).
MAINTAIN_RECOVERY = "recover_unresolved"
MAINTAIN_RETENTION = "refresh_retention"
MAINTAIN_CANDIDATE = "observe_candidate"
# Task 8 S-VI: the Mission-bound delivery of a COMPLETED record (the
# Runtime-owned verification run, then ``mission_control.delivery``).
MAINTAIN_DELIVERY = "drive_delivery"
# Task 8 startup correction: the PRE-MINT, READ-ONLY re-assessment of a
# refused follow-up whose resumption would retire its earlier runtime.
MAINTAIN_RETIREMENT = "assess_retirement"
MAINTENANCE_OPERATIONS = (MAINTAIN_RECOVERY, MAINTAIN_RETENTION, MAINTAIN_CANDIDATE,
                          MAINTAIN_DELIVERY, MAINTAIN_RETIREMENT)
PROBLEM_UNKNOWN_MAINTENANCE = "broker_unknown_maintenance"
OUTCOME_DELIVERY_NOT_APPLICABLE = "delivery_not_applicable"
OUTCOME_DELIVERY_NOT_WIRED = "delivery_not_wired"
OUTCOME_DELIVERY_PREFIX = "delivery_"
PROBLEM_VERIFICATION_CANDIDATE_MOVED = "broker_verification_candidate_moved"
PROBLEM_VERIFICATION_START = "broker_verification_start_failed"
# The phases in which the pass observes the LEASED workspace's candidate
# (no delivery record yet): once verification has accepted a result, the
# reviewed candidate must not move without an authorized transition.
CANDIDATE_OBSERVATION_PHASES = (record_module.PHASE_VERIFIED,
                                record_module.PHASE_COMPLETED)
PROBLEM_WRONG_CONTROL = "broker_wrong_control_repository"
PROBLEM_POLICY_DRIFT = "broker_policy_digest_drift"
PROBLEM_STALE_REVISION = "broker_stale_revision"
PROBLEM_NOT_AUTHORIZED = "broker_approval_not_consumed"
PROBLEM_NOT_APPROVED = "broker_decision_not_approve"
PROBLEM_SUPERSEDED = "broker_approval_superseded"
PROBLEM_EXPIRED = "broker_consumption_outside_validity"
PROBLEM_CRASH_AMBIGUOUS = "broker_crash_ambiguous"
PROBLEM_WRONG_PHASE = "broker_wrong_phase_for_action"
PROBLEM_TURN_NOT_COMPLETED = "broker_validation_turn_not_completed"
PROBLEM_SPAWN_FAILED = "broker_spawn_failed"
PROBLEM_FOLLOW_UP_BOUND = "broker_follow_up_bound_reached"
PROBLEM_INSTRUCTIONS_DRIFTED = "broker_instructions_drifted"
PROBLEM_VERIFICATION_UNSUPPORTED = "broker_verification_bad_outcome"
PROBLEM_NO_VERIFIED_RESULT = "broker_no_verified_result"
PROBLEM_SURFACE_UNAVAILABLE = "broker_surface_digest_refused"
# DI-REMOTE-3 I4 (RULING R-6, Layer 2 of plan §1.1): the INITIAL
# dispatch refuses while a REQUESTED result placeholder is not yet
# durably bound.
PROBLEM_PLACEHOLDER_NOT_BOUND = "broker_result_placeholder_not_bound"

# --- I5 revision 1: record-growth containment (round-10 F-1) ---------------
#
# Every action handler can grow the record and save; at a hard
# record bound the validator refuses the grown document and
# store.save raises. `perform` contains that raise and stops the
# ONE affected workflow durably instead of killing the Runtime.
# Two truthful codes (the register of I4's
# runtime_codex_turn_capacity_exhausted — the store is readable;
# the RECORD is the problem):
#   - capacity_exhausted: the validator refused on a hard bound
#     (its own PROBLEM_TOO_LARGE message format, pinned by test);
#   - record_unsavable: the grown record was refused for any other
#     reason (never mislabeled as capacity — the wrong-field
#     class).
PROBLEM_RECORD_CAPACITY_EXHAUSTED = "broker_record_capacity_exhausted"
PROBLEM_RECORD_UNSAVABLE = "broker_record_unsavable"
# The validator's own PROBLEM_TOO_LARGE message fragment (every
# hard-bound refusal in workflow_authority/record.py renders it);
# pinned against the real validator by test.
HARD_BOUND_MESSAGE_MARKER = "; the hard bound is "
OUTCOME_RECORD_GROWTH_BLOCKED = "record_growth_blocked"

# --- I5: reconcile_dispatch (ruling R-3 / D-B3) ----------------------------
#
# Bind EXACTLY ONE provable existing child, or BLOCK durably. The
# action reads NOTHING outside this repository: the child evidence
# is the CONTROL repository's own recorded children (via the
# injected read-only observer), the identity proof is the LEASED
# workspace's own observation, and the global Herdr registry is
# off limits — the deterministic alias is a DERIVED EXPECTATION
# label only, never binding evidence (herd's own child records
# carry no alias at all). More BLOCKED outcomes are the accepted
# cost: a BLOCKED a human resolves is correct behaviour.

# A refusal (nothing written; the workflow stays DISPATCHED):
PROBLEM_RECONCILE_ALREADY_BOUND = "broker_reconcile_already_bound"

# Task 8 S-VII correction 2: the durable stops of a refused-claim
# resumption that cannot be PROVEN safe — the settled runtime of a refused
# task handover is not provably the one to reuse (R1), or a refused
# follow-up's corrective objective is no longer the one it reserved (R2).
PROBLEM_RESUME_RUNTIME_UNPROVEN = "broker_resume_runtime_unproven"
PROBLEM_RESUME_OBJECTIVE_DRIFT = "broker_resume_objective_drift"

# Task 8 S-VII correction 3 (S7c-R3): the canonically settled result of a
# RESUMED task handover whose workflow binding was lost cannot be PROVEN to
# be this dispatch's completed handover (or is not settled yet: a refusal,
# nothing written). Never a replay and never a guessed child record.
PROBLEM_RESUME_BINDING_UNPROVEN = "broker_resume_binding_unproven"

# Task 8 (ownership correction): the terminal release of a RESUMED initial
# handover — which has no child record by construction — could not PROVE its
# workspaces from the canonical settled starts (a conflict, an unreadable
# source or listing, child evidence that is present rather than absent, an
# identity neither owned nor absent). The sessions and directory are retained.
PROBLEM_RELEASE_BINDING_UNPROVEN = "broker_release_binding_unproven"
# Task 8 startup correction — a corrective follow-up's runtime start. The
# native start (``herdr.lifecycle.start_herd``) REFUSES while the previous
# supervisor of the same repository is live (production passes no
# ``force``) and, once past that refusal, closes whatever workspace the
# persisted runtime state names — unproven, unconditionally, result not
# inspected. So before the follow-up's start this workflow's own earlier
# runtime is proven, closed and observed absent, and the persisted state
# that names it is preserved and discarded (``_retire_predecessor``).
#: TERMINAL: the earlier runtime or the persisted state is not provably this
#: workflow's own — nothing is closed or discarded; a human resolves it.
PROBLEM_PREDECESSOR_UNPROVEN = "broker_follow_up_predecessor_unproven"
#: HOLD: a recoverable condition (a listing or state read that failed, the
#: previous task still running, absence not yet observed) — nothing further
#: is done; the refused claim is resumed on a later pass.
PROBLEM_PREDECESSOR_PENDING = "broker_follow_up_predecessor_pending"
#: HOLD: a close was CLAIMED (durably recorded BEFORE its engine call, so
#: the claim alone does not prove the call was made) and the workspace's
#: absence is not observed — reported as uncertain, NEVER re-issued; the
#: retirement (and the release) wait for its observed absence.
PROBLEM_PREDECESSOR_UNCERTAIN = "broker_follow_up_predecessor_close_uncertain"
#: The recovery pass's cause for a retirement interrupted by a crash.
PROBLEM_PREDECESSOR_INTERRUPTED = "broker_follow_up_predecessor_interrupted"
RETIREMENT_RECEIPT_MARKER = "follow-up predecessor runtime"
#: The durable close CLAIM's verb — written under the admission immediately
#: BEFORE the engine call; the workspace id follows it JSON-encoded (ASCII),
#: so any admitted identity round-trips exactly (``_retirement_close_claims``).
RETIREMENT_CLOSE_CLAIMED = "close-claimed"
#: Written after the engine call RETURNED (absence not yet observed): it
#: distinguishes a confirmed invocation from a bare claim in what is reported.
RETIREMENT_CLOSE_RETURNED = "close-returned"

# Task 8 R19-2/R19-3: the durable verification ATTEMPT record. The CLAIM —
# carrying how many owned roots the verification scope held BEFORE it —
# is written under the delivery-effect admission (``admit_and_mark``)
# taken AFTER the blocking capture and the barrier's reads, immediately
# BEFORE the producer is invoked; a claim alone does not prove the
# invocation. Its SETTLEMENT names the PHASE the attempt reached (never
# the exception type alone): ``returned`` a record; ``not-started`` —
# refused before any process was started (retryable); ``start-unknown`` —
# the spawn itself raised, so a child may exist (resolved at re-entry from
# the owned roots: none created → ``not-started``, else
# ``outcome-unknown``); ``ran-unrecorded`` — the run happened and storing
# its result failed; ``interrupted`` — claimed and never settled by its
# own pass (its Runtime stopped). The last settlement of an attempt is its
# state; ``outcome-unknown``, ``ran-unrecorded`` and ``interrupted`` are
# NEVER replayed. Numbers and counts are ASCII digits
# (``_verification_attempts`` reads them back). R20-1: the grammar is the
# RECORD LAYER's (``workflow_authority.record``), so pruning reads the same
# attempts the barrier and the release read; these names are its own.
VERIFICATION_ATTEMPT_RECEIPT_MARKER = record_module.VERIFICATION_ATTEMPT_RECEIPT_MARKER
VERIFICATION_ATTEMPT_CLAIMED = record_module.VERIFICATION_ATTEMPT_CLAIMED
VERIFICATION_ATTEMPT_SETTLED = record_module.VERIFICATION_ATTEMPT_SETTLED
VERIFICATION_ATTEMPT_RETURNED = record_module.VERIFICATION_ATTEMPT_RETURNED
VERIFICATION_ATTEMPT_NOT_STARTED = record_module.VERIFICATION_ATTEMPT_NOT_STARTED
VERIFICATION_ATTEMPT_START_UNKNOWN = record_module.VERIFICATION_ATTEMPT_START_UNKNOWN
VERIFICATION_ATTEMPT_UNRECORDED = record_module.VERIFICATION_ATTEMPT_UNRECORDED
VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN = record_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN
VERIFICATION_ATTEMPT_INTERRUPTED = record_module.VERIFICATION_ATTEMPT_INTERRUPTED
VERIFICATION_ATTEMPT_KINDS = record_module.VERIFICATION_ATTEMPT_KINDS
#: The settlements that bar every further attempt: it ran, or may have
#: run, in the lease, and its result is not recorded.
VERIFICATION_ATTEMPT_NOT_REPLAYABLE = record_module.VERIFICATION_ATTEMPT_NOT_REPLAYABLE
#: HOLD: the verification scope's ownership records show a process of an
#: earlier attempt that may still be alive (``verification
#: .prior_ownership``), or cannot be read — no attempt starts; re-checked
#: each pass (startup recovery reaps an owner-dead group).
PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED = "broker_verification_ownership_unresolved"
#: HOLD: the spawn itself raised — whether a process started is not known
#: on this pass; the next pass decides from the owned roots.
PROBLEM_VERIFICATION_START_UNKNOWN = "broker_verification_start_unknown"
#: BLOCKED for delivery: an earlier attempt ran or may have run and its
#: result is not recorded (``VERIFICATION_ATTEMPT_NOT_REPLAYABLE``), or the
#: attempt records do not decode — it is never replayed.
PROBLEM_VERIFICATION_NOT_REPLAYABLE = "broker_verification_attempt_not_replayable"
#: R20-1 — RETAINED, the release refuses with NOTHING released: the
#: workflow's verification evidence is still needed — a verification
#: process of it may be alive (corroborated, leaderless or never stamped),
#: its ownership records cannot be read, or its attempt records do not
#: decode or cannot be settled (``verification_release_hold``). Checked at
#: the top of the release AND again at its destructive boundary, just before
#: the lease and directory are released, and re-checked each pass; the
#: lease, the workflow record and the ownership records stay.
PROBLEM_VERIFICATION_RETAINED = "broker_verification_retained"
#: R20-2 — RETAINED, the release refuses with NOTHING released: a process
#: scope of the workflow other than its verification scope (a task or
#: pre-dispatch scope) cannot be shown absent — a live group, corroborated or
#: leaderless, an unstamped root, unreadable or ambiguous records — or the
#: scopes cannot be enumerated (``scope_release_hold``). Checked at the top of
#: the release and again at its destructive boundary, and at cleanup
#: eligibility; the lease, the workflow record and the scope stay.
PROBLEM_PROCESS_SCOPE_RETAINED = "broker_process_scope_retained"
#: R21-A — RETAINED, the removal-only retry (``_retry_workspace_removal``)
#: and (R21-C, C-1) the first pass at its destructive boundary refuse with
#: NOTHING removed: the absence of the workflow's sessions, proven at the
#: first pass's close, is not established NOW — a workspace is
#: listed live again, a same-lease child record contradicts the canonical
#: history, or the evidence cannot be read (``_sessions_absent_now``). Nothing
#: is closed: the close is an effect the first pass made, never replayed.
PROBLEM_WORKSPACE_SESSIONS_RETAINED = "broker_workspace_sessions_retained"

# Durable BLOCKED causes (ruling R-3: zero, multi, conflicting,
# truncated, or degraded all stop durably) — the closed set the
# fail-closed matrix derives its rows from.
PROBLEM_RECONCILE_NO_MATCH = "broker_reconcile_no_match"
PROBLEM_RECONCILE_MULTIPLE = "broker_reconcile_multiple_matches"
PROBLEM_RECONCILE_CONFLICT = "broker_reconcile_conflicting_identity"
PROBLEM_RECONCILE_TRUNCATED = "broker_reconcile_children_truncated"
PROBLEM_RECONCILE_DEGRADED = "broker_reconcile_observation_degraded"
RECONCILE_BLOCK_CODES = (
    PROBLEM_RECONCILE_NO_MATCH,
    PROBLEM_RECONCILE_MULTIPLE,
    PROBLEM_RECONCILE_CONFLICT,
    PROBLEM_RECONCILE_TRUNCATED,
    PROBLEM_RECONCILE_DEGRADED,
)

# The internal outcome tokens for the reconcile action (Runtime-
# facing status words, never protocol outcomes).
OUTCOME_RECONCILED = "reconciled"
OUTCOME_RECOVERY_BLOCKED = "recovery_blocked"

# Fixed marker for the durable recovery-block receipt — SCOPED TO
# THIS ACTION (the same per-action pattern as
# VERIFICATION_BLOCK_MARKER; deliberately NOT a universal stop-
# reason mechanism, which is the deferred I3b). The adapter
# duplicates it (it may not import target_runtime); pinned equal by
# a cross-boundary test. BLOCKED is terminal, so at most one such
# receipt can ever exist per workflow.
RECOVERY_BLOCK_MARKER = "recovery blocked"

# Task 8 S-VII correction 3 (S7c-R3): the evidence receipt of a binding
# recovered from the CANONICAL settlement of a resumed task handover (at
# most one per workflow: the binding is written exactly once).
RECOVERY_BOUND_MARKER = "recovery bound"

# --- I3: the fail-closed verified_result gates -----------------------------
#
# `verified_result` from the fresh Codex verification turn is
# NECESSARY, NEVER SUFFICIENT. Before anything is recorded, the
# Broker independently applies the D-A4 conjunctive gates against a
# FRESH evidence collection (a fresh disk read through the same
# injected seams). Each gate carries its OWN problem code; one
# failing conjunct refuses `verified_result` and the workflow stops
# DURABLY (BLOCKED with the reason recorded as a fixed-marker
# receipt) — never an indefinite re-poll, never a silent strand.
# Herd lifecycle COMPLETE alone can never produce VERIFIED by
# construction: it is one conjunct of eight.
#
# Wording rule (I3 binding item 6): the canonical Reviewer APPROVE
# conjunct is TARGET-PRODUCED evidence — the child engine's own
# reviewer wrote that artifact inside the leased workspace. It is
# evidence that the target's review process ran and concluded,
# never independent verification.

PROBLEM_VERIFY_EVIDENCE_INCOMPLETE = (
    "broker_verification_evidence_incomplete"
)
PROBLEM_VERIFY_EVIDENCE_INVALID = (
    "broker_verification_evidence_invalid"
)
PROBLEM_VERIFY_TARGET_NOT_STOPPED = (
    "broker_verification_target_not_stopped"
)
PROBLEM_VERIFY_REVIEW_NOT_APPROVE = (
    "broker_verification_review_not_approve"
)
PROBLEM_VERIFY_ORIGIN_MISMATCH = (
    "broker_verification_origin_mismatch"
)
PROBLEM_VERIFY_BASELINE_MOVED = (
    "broker_verification_baseline_moved"
)
PROBLEM_VERIFY_POLICY_DRIFT = (
    "broker_verification_policy_drift"
)
PROBLEM_VERIFY_SURFACE_BASELINE_MISSING = (
    "broker_verification_surface_baseline_missing"
)
PROBLEM_VERIFY_SURFACE_DRIFT = (
    "broker_verification_surface_drift"
)
PROBLEM_VERIFY_DELIVERY_AUTHORITY = (
    "broker_verification_delivery_authority"
)

# Every problem code the verification gates can emit — the complete
# closed set the table-driven refusal matrix derives its rows from
# (a new gate code without a matrix row fails the suite). The eight
# D-A4 conjuncts map to ten codes: conjunct 1 (evidence complete AND
# schema-valid) and conjunct 7 (surface receipt present AND byte
# equal) each split into two.
VERIFICATION_GATE_CODES = (
    PROBLEM_VERIFY_EVIDENCE_INCOMPLETE,
    PROBLEM_VERIFY_EVIDENCE_INVALID,
    PROBLEM_VERIFY_TARGET_NOT_STOPPED,
    PROBLEM_VERIFY_REVIEW_NOT_APPROVE,
    PROBLEM_VERIFY_ORIGIN_MISMATCH,
    PROBLEM_VERIFY_BASELINE_MOVED,
    PROBLEM_VERIFY_POLICY_DRIFT,
    PROBLEM_VERIFY_SURFACE_BASELINE_MISSING,
    PROBLEM_VERIFY_SURFACE_DRIFT,
    PROBLEM_VERIFY_DELIVERY_AUTHORITY,
)

# The internal broker-outcome token for a durably BLOCKED
# verification (like "target_running", it is a Runtime-facing status
# word, never a protocol outcome).
OUTCOME_VERIFICATION_BLOCKED = "verification_blocked"

# Fixed marker for the durable verification-block receipt; the
# Telegram adapter renders it for a BLOCKED workflow and duplicates
# this string (it may not import target_runtime) — pinned equal by a
# cross-boundary test.
VERIFICATION_BLOCK_MARKER = "verification blocked"


def _binding(projection, name):
    return projection["bindings"][name]


def _gate_evidence_complete(entry, projection):
    if projection.get("completeness") != (
        evidence_module.PROJECTION_COMPLETE
    ):
        failing = sorted({
            diagnostic.get("binding")
            for diagnostic in projection.get("diagnostics", [])
            if isinstance(diagnostic, dict)
        })
        return (
            PROBLEM_VERIFY_EVIDENCE_INCOMPLETE,
            "the evidence projection is PARTIAL (unresolved"
            " bindings: %s); the target is stopped, so there is"
            " nothing left to wait for" % ", ".join(
                str(name) for name in failing
            ),
        )
    return None, None


def _gate_evidence_valid(entry, projection):
    try:
        evidence_module.validate_projection(projection)
    except evidence_module.EvidenceError as exc:
        return (
            PROBLEM_VERIFY_EVIDENCE_INVALID,
            "the evidence projection failed schema validation"
            " (%s: %s)" % (exc.problem, exc),
        )
    return None, None


def _gate_target_stopped(entry, projection):
    binding = _binding(projection, "target_task")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["task_status"] not in _TARGET_TERMINAL_STATUSES
    ):
        return (
            PROBLEM_VERIFY_TARGET_NOT_STOPPED,
            "the target task lifecycle status %r is not a stopped"
            " status" % (binding["task_status"],),
        )
    return None, None


def _gate_review_approved(entry, projection):
    binding = _binding(projection, "review_decision")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["decision"] != "APPROVE"
    ):
        return (
            PROBLEM_VERIFY_REVIEW_NOT_APPROVE,
            "the target-produced canonical review record does not"
            " conclude APPROVE (decision %r) — this record is"
            " evidence that the target's own review process ran and"
            " concluded, never independent verification, and without"
            " it the mission is not verified"
            % (binding["decision"],),
        )
    return None, None


def _gate_origin_identity(entry, projection):
    binding = _binding(projection, "live_origin")
    if binding["status"] != evidence_module.BINDING_EXACT:
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live target origin could not be read exactly",
        )
    try:
        live = canonical_module.canonicalize_repository_url(
            binding["url"]
        )
    except canonical_module.CanonicalizationError as exc:
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live target origin URL does not canonicalize"
            " (%s)" % exc,
        )
    approved = canonical_module.canonicalize_repository_url(
        entry["target"]["canonical_url"]
    )
    if canonical_module.repository_identity_key(live) != (
        canonical_module.repository_identity_key(approved)
    ):
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live workspace origin does not name the approved"
            " repository identity (case-folded identity comparison,"
            " never URL string equality)",
        )
    return None, None


def _gate_baseline_unmoved(entry, projection):
    binding = _binding(projection, "baseline_match")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["match"] is not True
    ):
        return (
            PROBLEM_VERIFY_BASELINE_MOVED,
            "the live target HEAD does not equal the approved"
            " baseline commit",
        )
    return None, None


def _gate_control_policy(entry, projection):
    binding = _binding(projection, "control_policy")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["match"] is not True
    ):
        return (
            PROBLEM_VERIFY_POLICY_DRIFT,
            "the LIVE control policy digest does not match the one"
            " this workflow was authorized under",
        )
    return None, None


def _gate_surface_baseline_present(entry, projection):
    if dispatch_module.surface_baseline_digest(entry) is None:
        return (
            PROBLEM_VERIFY_SURFACE_BASELINE_MISSING,
            "no dispatch-time protected-surface baseline receipt"
            " exists (this workflow was dispatched before the"
            " baseline was introduced); verification fails closed"
            " rather than fabricating or retro-fitting one — a fresh"
            " Mission Authorization is required",
        )
    return None, None


def _gate_surface_unchanged(entry, projection):
    baseline = dispatch_module.surface_baseline_digest(entry)
    binding = _binding(projection, "protected_surface")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        baseline is None or binding["digest"] != baseline
    ):
        return (
            PROBLEM_VERIFY_SURFACE_DRIFT,
            "the LIVE protected control-surface digest does not"
            " byte-match the dispatch-time baseline receipt; the"
            " control machinery may have changed during target"
            " execution",
        )
    return None, None


def _gate_delivery_authority(entry, projection):
    binding = _binding(projection, "delivery_authority")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["value"] != "none"
    ):
        return (
            PROBLEM_VERIFY_DELIVERY_AUTHORITY,
            "delivery_authority is %r; it must be exactly 'none'"
            % (binding["value"],),
        )
    return None, None


# The ORDERED gate registry. Evaluation order is fixed (the evidence
# shape gates run first because every later gate reads bindings);
# each gate is INDEPENDENT: it has its own problem code(s) and any
# single failure refuses verified_result. DO NOT REORDER the first
# two entries: the validity gate running first is what makes every
# later gate's `projection["bindings"][name]` subscript safe on the
# fresh collection (round-06 N-3).
_VERIFICATION_GATES = (
    ("evidence_complete", _gate_evidence_complete),
    ("evidence_valid", _gate_evidence_valid),
    ("target_stopped", _gate_target_stopped),
    ("review_approved", _gate_review_approved),
    ("origin_identity", _gate_origin_identity),
    ("baseline_unmoved", _gate_baseline_unmoved),
    ("control_policy", _gate_control_policy),
    ("surface_baseline_present", _gate_surface_baseline_present),
    ("surface_unchanged", _gate_surface_unchanged),
    ("delivery_authority", _gate_delivery_authority),
)


_StartClosure = collections.namedtuple(
    "_StartClosure",
    ("stop_requested", "stopped", "admission", "detail", "unsettled",
     "absent_observed"))

# The start receipt states a workflow record carries (the canonical
# facts live in the Mission store; these are the record's own truth).
START_STATE_ADMITTED = "admitted"
START_STATE_UNSETTLED = "unsettled"
# Task 8 S-V (retention crash windows): the PRE-ADMISSION CLAIM receipt,
# written on the workflow record BEFORE the canonical open — a crash
# between the two leaves a ``claiming`` head latest, which protects the
# record until the owner's recovery pass resolves the claim from the
# canonical facts (admitted, or unadmitted when no start exists).
START_STATE_CLAIMING = "claiming"
START_STATE_CLAIM_ADMITTED = "claim:admitted"
START_STATE_CLAIM_REFUSED = "claim:refused"
START_STATE_CLAIM_UNADMITTED = "claim:unadmitted"
# Task 8 startup correction: a corrective follow-up's runtime claim is in
# its PRE-CLAIM retirement of this workflow's earlier runtime
# (``_MissionStartGuard.open``). Written before any read of that step, so a
# crash anywhere in it leaves this head latest; the owner's recovery pass
# resolves it from the canonical facts — ``claim:admitted`` when the start
# was opened after all, else ``claim:refused`` (``PROBLEM_PREDECESSOR_
# INTERRUPTED``): the step precedes every canonical open, so nothing was
# admitted and the point is positively resumable. A retirement that
# COMPLETES re-enters ``claiming`` before the canonical open, so a crash in
# the canonical claiming window resolves exactly as it always did
# (``claim:unadmitted`` from canonical absence: never resumed, never
# replayed).
START_STATE_CLAIM_RETIRING = "claim:retiring"
CLAIM_HEAD_PREFIX = "claim-"
START_STATE_STOP_CONFIRMED = "stop:confirmed"
START_STATE_STOP_OBSERVED_UNCONFIRMED = "stop:absence-observed-unconfirmed"
START_STATE_STOP_PENDING = "stop:pending"
START_STATE_SETTLED_UNCERTAIN_RECOVERY = "settled:uncertain (owner recovery)"
START_STATE_SETTLED_RETAINED = "settled:retained-result (owner pass)"

# Late hand-over bounds (re-checkpoint 2 correction): a late engine result
# whose canonical hand-over is refused by the Mission source (a refused
# settlement, a refused identity observation) is retried by the owner's
# late thread this many times, this far apart, before the process gives
# up on recording it (the owner's next Runtime pass then settles the
# start UNCERTAIN; see `_MissionStartGuard`). Exact-value pinned.
MAX_LATE_HANDOVER_ATTEMPTS = 16
LATE_HANDOVER_RETRY_SECONDS = 0.5


class _RetainedHandovers(object):
    """The RETAINED HAND-OVERS of this Runtime process (S4e): every
    returned engine result the process KNOWS whose canonical record was
    refused — a synchronous result whose settlement the source refused,
    a parked late result consumed by a refused settlement, and every
    late loop (settlement, identity observation, stop observation) that
    exhausted its bound — is kept here, keyed by (owner_ref, start_id),
    until the SAME owner's later pass in this process records its
    obligations canonically (`_recover_pending_stops`). It outlives the
    guard and its bounded thread; it is process memory, so the only
    window that loses a known result is process death before the first
    durable write (stated). One lock per key serialises the late thread
    and the owner pass on the same start (EXACTLY-ONCE STOP RULE: a close
    is issued only by the holder of that lock, only after the identity is
    durable, and only when a fresh listing still shows the workspace —
    a second close for the same known identity can only follow a first
    close whose observation was not recorded, and then only if the
    workspace is still listed)."""

    def __init__(self):
        import threading
        self._guard = threading.Lock()
        self._entries = {}
        self._locks = {}

    def lock(self, key):
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                import threading
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def retain(self, key, data):
        with self._guard:
            self._entries[key] = dict(data)

    def get(self, key):
        with self._guard:
            data = self._entries.get(key)
            return None if data is None else dict(data)

    def drop(self, key):
        with self._guard:
            self._entries.pop(key, None)

    def keys(self):
        with self._guard:
            return list(self._entries)


RETAINED_HANDOVERS = _RetainedHandovers()


# Task 8 S-V (start-claim decision item 5, bounded CLEANUP): the bound on
# EACH engine call the owned stop makes (the live listing, the close).
# The engine's own commands carry no timeout, and the owned stop runs
# under the workflow lock, so an unbounded call would stall every
# workflow; a call that outlasts this bound is ABANDONED — the stop stays
# PENDING (a timeout is never absence proof) and the next pass retries.
# Exact-value pinned in the bound-constant table.
OWNED_STOP_WAIT_SECONDS = 60


class OwnedStopBoundExceeded(Exception):
    """An owned-stop engine call outlasted ``OWNED_STOP_WAIT_SECONDS``
    (or a close of the same workspace abandoned earlier is still in
    flight): the stop is PENDING, never confirmed."""


class _InflightCloses(object):
    """The workspace ids whose owned close THIS process abandoned at the
    bound and that have not returned yet: a still-running close is never
    issued a second time by this process. Process-local by construction
    (a restart forgets it; the engine's close of an already-closed
    workspace leaves it absent, which the next fresh listing observes)."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._ids = set()

    def claim(self, workspace_id):
        with self._lock:
            if workspace_id in self._ids:
                return False
            self._ids.add(workspace_id)
            return True

    def release(self, workspace_id):
        with self._lock:
            self._ids.discard(workspace_id)

    def holds(self, workspace_id):
        with self._lock:
            return workspace_id in self._ids


INFLIGHT_CLOSES = _InflightCloses()


def live_listing_problem(listing):
    """Why ``listing`` cannot be read as a COMPLETE live workspace listing,
    or None. Absence is derived only from a complete listing: a list in
    which EVERY entry is an object naming a non-empty string
    ``workspace_id``. ``None``, a mapping, a non-object entry or an entry
    without its id is unavailable/malformed evidence — never absence."""
    if not isinstance(listing, list):
        return "the live listing is %s, not a list" % type(listing).__name__
    for index, workspace in enumerate(listing):
        if not isinstance(workspace, dict):
            return ("live listing entry %d is %s, not an object"
                    % (index, type(workspace).__name__))
        workspace_id = workspace.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            return "live listing entry %d names no workspace_id" % index
    return None


def bounded_engine_call(call, seconds, on_late=None):
    """Run ``call`` in a worker thread and wait at most ``seconds``:
    its result, or its exception re-raised; ``OwnedStopBoundExceeded``
    when it outlasts the bound — the abandoned call keeps running, and
    ``on_late`` (if any) runs when it eventually returns or raises."""
    import threading
    box = {"done": False, "abandoned": False}
    lock = threading.Lock()

    def run():
        try:
            box["result"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised or abandoned
            box["error"] = exc
        with lock:
            box["done"] = True
            abandoned = box["abandoned"]
        if abandoned and on_late is not None:
            on_late()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    with lock:
        if not box["done"]:
            box["abandoned"] = True
            raise OwnedStopBoundExceeded(
                "the engine call outlasted the %ss owned-stop bound and was"
                " abandoned" % (seconds,))
    if "error" in box:
        raise box["error"]
    return box["result"]

# The bridge's start points mapped to the Mission core's start points.
_CORE_START_POINT = {
    dispatch_module.START_POINT_RUNTIME: "runtime",
    dispatch_module.START_POINT_TASK: "task",
}


def capture_candidate(transport, repository_path, base_oid):
    """READ-ONLY observation of the delivery candidate in
    ``repository_path`` relative to ``base_oid``, identified exactly as
    the delivery layer identifies it (P1-A6, ``pr_delivery.candidate``:
    ``parse_raw_z`` + ``identity_digest`` over ``diff-index --cached
    --raw --abbrev=40 --no-renames -z <base>`` read through the Runtime
    transport's read-only verb, no index refresh or lock):

    - ``exact``: the staged entries and their identity digest, the
      working tree being exactly the staged candidate;
    - ``not_exact`` (``broker_candidate_not_exact``): the staged identity
      is captured but the working tree holds changes outside it (P1-A6's
      own rule: every porcelain line must be a FULLY staged A/M/D entry) —
      never reported as THE candidate;
    - ``unavailable``: the P1-A6 refusal code (empty candidate, type
      change, gitlink, bad path, duplicate, over limit, raw format) or
      ``broker_candidate_capture_failed`` (a transport failure or an
      over-bound capture). Never a HEAD fallback.

    HEAD is read separately and labelled as HEAD; its read failing leaves
    ``head`` None and changes nothing else."""
    try:
        head = transport.head_commit(repository_path).strip() or None
    except Exception:                                  # noqa: BLE001
        head = None
    if head is not None and not (len(head) == 40 and all(
            c in "0123456789abcdef" for c in head)):
        # R17 self-audit: a HEAD that is not a 40-hex commit id is recorded
        # as unknown — otherwise the honest receipt could never be canonical,
        # never repeat, and a new one would be written every pass.
        head = None
    observation = {"status": None, "problem": None, "detail": None,
                   "entries": None, "digest": None, "head": head,
                   "base": base_oid}
    try:
        porcelain = transport.status_porcelain_readonly(repository_path)
    except Exception as exc:                           # noqa: BLE001
        porcelain = {"status": "error:%s" % exc.__class__.__name__}
    if not isinstance(porcelain, dict) or porcelain.get("status") != "captured":
        observation.update(status=CANDIDATE_STATUS_UNAVAILABLE,
                           problem=PROBLEM_CANDIDATE_CAPTURE,
                           detail="porcelain capture %s" % (
                               porcelain.get("status") if isinstance(porcelain, dict)
                               else type(porcelain).__name__))
        return observation
    try:
        raw = transport.diff_index_raw_readonly(repository_path, base_oid)
        entries = candidate_module.parse_raw_z(raw)
    except candidate_module.CandidateError as exc:
        observation.update(status=CANDIDATE_STATUS_UNAVAILABLE,
                           problem=exc.problem, detail=str(exc)[:500])
        return observation
    except Exception as exc:                           # noqa: BLE001
        observation.update(status=CANDIDATE_STATUS_UNAVAILABLE,
                           problem=PROBLEM_CANDIDATE_CAPTURE,
                           detail="staged capture failed (%s)"
                                  % exc.__class__.__name__)
        return observation
    observation["entries"] = entries
    observation["digest"] = candidate_module.identity_digest(entries)
    outside = porcelain_outside_candidate(porcelain["text"])
    if outside:
        observation.update(status=CANDIDATE_STATUS_NOT_EXACT,
                           problem=PROBLEM_CANDIDATE_NOT_EXACT,
                           detail=("the working tree is not exactly the staged"
                                   " candidate: %d entr%s outside it (%s)" % (
                                       len(outside),
                                       "y" if len(outside) == 1 else "ies",
                                       "; ".join(outside[:4])))[:500])
        return observation
    observation["status"] = CANDIDATE_STATUS_EXACT
    return observation


def claim_head(point, dispatch_sequence):
    """The receipt head of one pre-admission claim: unique per start
    point and dispatch of a record (colon-free, so the receipt parser's
    head/rest split stays exact)."""
    return "%s%s-%d" % (CLAIM_HEAD_PREFIX, point, dispatch_sequence)


def parse_claim_head(head):
    """``(point, dispatch_sequence)`` of a claim head, or None. The sequence
    must be the canonical ASCII decimal ``claim_head`` writes (1..999999,
    the receipts' own number rule): a Unicode digit (which ``int`` rejects
    or re-reads as another number), zero, a sign, padding or a leading zero
    is no claim head — never an exception out of the owner's pass (R17
    self-audit)."""
    if not head.startswith(CLAIM_HEAD_PREFIX):
        return None
    point, _, sequence = head[len(CLAIM_HEAD_PREFIX):].rpartition("-")
    if point not in _CORE_START_POINT or not 1 <= len(sequence) <= 6:
        return None
    if not all("0" <= c <= "9" for c in sequence) or sequence[0] == "0":
        return None
    return point, int(sequence)


def _start_receipt_facts(entry):
    """The LATEST ``mission start`` / ``mission claim`` receipt facts per
    head (a start id, or a pre-admission claim head), from the record's own
    receipts in order: ``{head: {"marker", "point", "dispatch", "state",
    "stop"}}`` — each key's FIRST token in its receipt (the free-text
    ``cause`` follows them)."""
    facts = {}
    for receipt in entry.get("receipts") or []:
        summary = receipt.get("bounded_summary") if isinstance(receipt, dict) else None
        if not isinstance(summary, str):
            continue
        for marker in (MISSION_START_RECEIPT_MARKER, MISSION_CLAIM_RECEIPT_MARKER):
            if summary.startswith(marker + " "):
                break
        else:
            continue
        head, _, rest = summary[len(marker) + 1:].partition(":")
        fact = {"marker": marker, "point": None, "dispatch": None, "state": None,
                "stop": None}
        for token in rest.split(" "):
            key, equals, value = token.partition("=")
            if equals and key in fact and fact[key] is None:
                fact[key] = value
        facts[head] = fact
    return facts


def refused_claim_resumption(entry):
    """Task 8 S-VII (Lead gate F-S7-1; correction 2, R1 and R2): the ONE
    start point of the record's CURRENT dispatch that is PROVABLY unstarted
    because its claim was durably REFUSED — ``(point, dispatch_sequence)``
    — or None. Pure durable state of the workflow record; POSITIVE facts
    only: a claim is resumable only when the live guard's own
    ``claim:refused`` receipt is the LATEST of its head. ``claiming`` (a
    crash window), ``claim:unadmitted`` (resolved from the canonical
    ABSENCE of a start), ``claim:admitted``, an unknown state and a
    missing receipt never are — absence is not proof.

    Common: a Mission-origin record, DISPATCHED; its current dispatch is
    ordinal ``n`` (``n`` markers) and the engagement reference names
    ordinal ``n``; the initial dispatch (``n == 1``) has no bound identity
    yet, a follow-up (``n > 1``) keeps the identity bound at the initial
    dispatch.

    - RUNTIME point (F-S7-1; R2 for ``n > 1``): the latest receipt of the
      runtime-start claim of ``n`` is ``claim:refused``, and NO start
      receipt of ``n`` and no task claim of ``n`` exist — nothing of this
      dispatch was ever admitted.
    - TASK point (R1): the runtime-start claim of ``n`` is
      ``claim:admitted``; EXACTLY ONE start receipt of ``n`` exists, for
      the runtime point, and its latest state is ``settled:completed`` with
      ``stop=none`` (settled completed, no stop owed); the latest receipt of
      the task claim of ``n`` is ``claim:refused``; and no start receipt of
      the task point of ``n`` exists — the objective was never handed over.

    The Broker re-checks the CANONICAL facts before acting (the settled
    runtime start, no stop requirement, no task start, the reservation of
    ``n``) and, for the task point, proves ownership of the settled runtime
    against a fresh live listing; the core opens a start at most once per
    engagement point, so a resumption can never invoke twice. A TERMINAL
    refusal at a claim blocks the record instead, so it never reaches this
    state."""
    if not record_module.is_mission_core_kind(entry):
        return None
    if entry.get("phase") != record_module.PHASE_DISPATCHED:
        return None
    sequence = dispatch_module.dispatch_count(entry)
    if sequence < 1:
        return None
    reference = entry.get(record_module.MISSION_ENGAGEMENT_KEY) or {}
    if reference.get("engagement_sequence") != sequence:
        return None
    engine = entry.get("target_engine")
    if sequence == 1 and engine is not None:
        return None
    if sequence > 1 and not (
        isinstance(engine, dict) and isinstance(engine.get("task_id"), str)
        and engine["task_id"] and engine["task_id"] != dispatch_module.UNRESOLVED_TASK_ID
    ):
        return None
    facts = _start_receipt_facts(entry)
    runtime_claim = facts.get(claim_head(dispatch_module.START_POINT_RUNTIME, sequence))
    task_claim = facts.get(claim_head(dispatch_module.START_POINT_TASK, sequence))
    starts = [fact for head, fact in facts.items()
              if fact["marker"] == MISSION_START_RECEIPT_MARKER
              and fact["dispatch"] == str(sequence)]
    # Task 8 startup correction: a follow-up's claim whose latest state is
    # ``claim:retiring`` is as positive a fact as a refusal — its retirement
    # was interrupted, and a completed retirement re-enters ``claiming``
    # before the canonical open, so this claim never attempted one. It is
    # resumed (the owner's recovery records the refusal first; the retirement
    # is re-assessed read-only before anything is minted), never re-verified
    # into a drifting second correction.
    runtime_resumable = (START_STATE_CLAIM_REFUSED,) + (
        (START_STATE_CLAIM_RETIRING,) if sequence > 1 else ())
    if runtime_claim is not None and runtime_claim["state"] in runtime_resumable:
        if starts or task_claim is not None:
            return None
        return dispatch_module.START_POINT_RUNTIME, sequence
    if task_claim is None or task_claim["state"] != START_STATE_CLAIM_REFUSED:
        return None
    if runtime_claim is None or runtime_claim["state"] != START_STATE_CLAIM_ADMITTED:
        return None
    if len(starts) != 1 or starts[0]["point"] != dispatch_module.START_POINT_RUNTIME:
        return None
    if starts[0]["state"] != record_module.SETTLED_COMPLETED_STATE or (
        starts[0]["stop"] != record_module.START_RECEIPT_STOP_NONE
    ):
        return None
    return dispatch_module.START_POINT_TASK, sequence


def refused_claim_resumable(entry):
    """Whether ``refused_claim_resumption`` names a resumable point."""
    return refused_claim_resumption(entry) is not None


def follow_up_objective_drift(entry, sequence):
    """Why follow-up ``sequence``'s corrective objective may no longer be
    the one it was reserved and marked with, or None (Task 8 S-VII
    correction 2, R2). The corrective brief is built ONLY from the record's
    immutable authority fields and the LATEST correction evidence
    (``dispatch_module.build_follow_up_spawn_request``), and it was built
    before the dispatch marker; so it is provably unchanged exactly when no
    correction evidence was recorded AFTER the ``sequence``-th marker. Any
    later correction receipt is drift, and the resumption fails closed."""
    markers = 0
    marked = False
    for receipt in entry.get("receipts") or []:
        summary = receipt.get("bounded_summary") if isinstance(receipt, dict) else ""
        if not isinstance(summary, str) or receipt.get("kind") != "evidence":
            continue
        if summary.startswith(dispatch_module.DISPATCH_RECEIPT_MARKER):
            markers += 1
            marked = markers == sequence or marked
            continue
        if marked and summary.startswith(dispatch_module.CORRECTION_RECEIPT_MARKER):
            return ("correction evidence was recorded after follow-up %d was"
                    " marked (%s); the reserved corrective objective is not the"
                    " one that would be handed over" % (sequence, summary[:120]))
    if markers != sequence:
        return ("the record holds %d dispatch markers, not %d; the follow-up is"
                " not provably the reserved one" % (markers, sequence))
    return None


def _claim_history(entry, head):
    """Every ``mission claim`` receipt of ``head``, in order: ``[(state,
    cause)]`` — each key's FIRST token (an admitted claim's cause names its
    start id first)."""
    prefix = "%s %s:" % (MISSION_CLAIM_RECEIPT_MARKER, head)
    history = []
    for receipt in entry.get("receipts") or []:
        summary = receipt.get("bounded_summary") if isinstance(receipt, dict) else None
        if not isinstance(summary, str) or not summary.startswith(prefix):
            continue
        state = cause = None
        for token in summary[len(prefix):].split(" "):
            key, equals, value = token.partition("=")
            if equals and key == "state" and state is None:
                state = value
            elif equals and key == "cause" and cause is None:
                cause = value
        history.append((state, cause))
    return history


def resumed_handover_binding(entry):
    """Task 8 S-VII correction 3 (S7c-R3): ``(start_id, dispatch_sequence)``
    of the RESUMED task handover (R1) whose task start the live guard
    durably ADMITTED while the record's target identity is still unbound —
    the binding-save loss window (the handover settled canonically, with
    the task id the engine returned, and the process ended before
    ``target_engine`` was saved) — or None. Pure durable state of the
    workflow record; POSITIVE facts only:

    - a Mission-core record, DISPATCHED, WITHOUT a target identity; its
      current dispatch is ordinal ``n`` (``n`` markers) and the engagement
      reference names ``n``;
    - the runtime-start claim of ``n`` is ``claim:admitted`` and the ONE
      other start receipt of ``n`` is the runtime point's, latest
      ``settled:completed stop=none`` (the resumption's precondition);
    - the task claim of ``n`` was durably REFUSED earlier — the R1 path, the
      only handover that writes no control-repository child record — and
      its LATEST receipt is ``claim:admitted`` naming the start id, whose
      own start receipt exists at the task point.

    It never proves completion: the Broker (``_bind_settled_handover``)
    binds only a CANONICAL settlement proven to be this handover's
    completed result; anything else refuses (nothing written) or blocks.
    An ordinary spawn (no earlier refusal) keeps the D-B1 child-record
    reconciliation unchanged."""
    if not record_module.is_mission_core_kind(entry):
        return None
    if entry.get("phase") != record_module.PHASE_DISPATCHED:
        return None
    if entry.get("target_engine") is not None:
        return None
    sequence = dispatch_module.dispatch_count(entry)
    if sequence < 1:
        return None
    reference = entry.get(record_module.MISSION_ENGAGEMENT_KEY) or {}
    if reference.get("engagement_sequence") != sequence:
        return None
    claims = _claim_history(entry, claim_head(dispatch_module.START_POINT_TASK, sequence))
    if not claims or claims[-1][0] != START_STATE_CLAIM_ADMITTED:
        return None
    start_id = claims[-1][1]
    if not isinstance(start_id, str) or not start_id:
        return None
    if START_STATE_CLAIM_REFUSED not in [state for state, _cause in claims[:-1]]:
        return None
    facts = _start_receipt_facts(entry)
    runtime_claim = facts.get(claim_head(dispatch_module.START_POINT_RUNTIME, sequence))
    if runtime_claim is None or runtime_claim["state"] != START_STATE_CLAIM_ADMITTED:
        return None
    starts = dict((head, fact) for head, fact in facts.items()
                  if fact["marker"] == MISSION_START_RECEIPT_MARKER
                  and fact["dispatch"] == str(sequence))
    task = starts.pop(start_id, None)
    if task is None or task["point"] != dispatch_module.START_POINT_TASK:
        return None
    runtime = list(starts.values())
    if len(runtime) != 1 or runtime[0]["point"] != dispatch_module.START_POINT_RUNTIME:
        return None
    if runtime[0]["state"] != record_module.SETTLED_COMPLETED_STATE or (
        runtime[0]["stop"] != record_module.START_RECEIPT_STOP_NONE
    ):
        return None
    return start_id, sequence


class _MissionStartGuard(object):
    """The Broker's side of an engagement start (Task 8 S-IV, start-claim
    decision), handed to the bridge's guarded control plane for ONE
    dispatch of a Mission-origin record. ``open(point)`` is the ATOMIC
    admission: the canonical ``open_engagement_start`` transaction
    persists one exact start bound to the engagement, the point and this
    guard's single-owner reference — no start, no invocation; an existing
    start for the point (a crash after it, a retry, a restart, another
    owner) refuses terminally and is never replayed. ``close(point,
    failed, result)`` — after the engine call returned — SETTLES the
    start with the returned outcome and execution identity in the
    canonical transaction (the core derives the stop requirement: a
    control's recorded stop request, a superseded revision, a lapsed
    authorization, a terminal Mission, a non-completed outcome, or this
    gate's own terminal refusal re-checked at settlement — a time lapse
    is not a write, so it is inspected here); when a stop is pending it
    performs the owned stop OUTSIDE the Mission lock and records the
    observation (absence is the only confirmation). The Mission lock is
    never held across the engine's start, hand-over, stop or waits. A
    settlement the source cannot record leaves the start UNSETTLED
    (ambiguous, never re-invoked) and the closure says so.

    LATE HAND-OVER (re-checkpoint 2 correction): a late result is never
    left in process memory with no further owner action scheduled.
    Ordering under ``_late_lock``: (1) arrived before the caller's
    settlement → parked, consumed BY the settlement; (2) arrived during a
    SUCCESSFUL settlement → handed over by the caller right after it;
    (3) arrived after a successful settlement → handed over by the worker
    (identity persisted canonically through an observation, then the
    owned stop, then the observation of its result); (4) the caller's
    settlement was REFUSED (source unavailable, stale sequence, capacity)
    → the start is UNSETTLED; a parked or later late result is handed to
    ``_handover_unsettled``, which SETTLES the start itself with the
    returned outcome and identity (bounded retries,
    ``MAX_LATE_HANDOVER_ATTEMPTS`` × ``LATE_HANDOVER_RETRY_SECONDS``) as
    soon as the source answers again, then performs the owned stop and
    the absence-only observation; if the owner's next Runtime pass
    settled the start UNCERTAIN first, the late thread persists the
    identity through an observation instead. Identity-observation
    refusals use the same bounded retry path; the owned stop never
    precedes the identity's durable record. IRREDUCIBLE WINDOW (stated):
    process death between the engine's return and the first durable
    write loses the returned identity — the start then stays UNSETTLED
    until the owner's next pass settles it UNCERTAIN, the Mission stays
    held, and the stop is never reported confirmed. LOCK ORDER: the late
    and hand-over threads take ONLY the Mission store lock (through the
    gate's canonical operations), never the workflow lock; the Runtime's
    recovery pass takes the workflow lock and then the Mission lock, the
    same order as every Broker action — no reverse order exists."""

    def __init__(self, broker, workflows, entry, dispatch_sequence):
        import threading
        self.broker = broker
        self.workflows = workflows
        self.entry = entry
        self.dispatch_sequence = dispatch_sequence
        self.owner_ref = broker.mission_gate.owner_ref(entry, dispatch_sequence)
        self.start_ids = {}
        self.identity = {"workspace_id": None, "agent_names": [], "task_id": None}
        # Late-return ordering (checkpoint-1 correction): the worker thread
        # of an abandoned call and the caller's settlement race; every
        # hand-over goes through this lock so a late result is consumed
        # by whichever side runs second, never dropped.
        self._late_lock = threading.Lock()
        self._late = {}          # point -> (result, error) arrived before settlement
        self._settled = set()    # points whose canonical settlement is recorded
        self._unsettled = set()  # points whose settlement the source refused
        self.late_outcomes = {}  # point -> what happened to the late result
        self.handover_threads = []

    @staticmethod
    def wait_seconds():
        return dispatch_module.START_WAIT_SECONDS

    def late_return(self, point, result, error):
        """An ABANDONED engine call returned after its bound: see the
        class docstring's LATE HAND-OVER orderings. Mission store only —
        the workflow lock is long released, so no workflow receipt is
        written here; the owner's next pass records the canonical state
        on the record (`_recover_pending_stops`)."""
        with self._late_lock:
            if point in self._settled:
                mode = "settled"
            elif point in self._unsettled:
                mode = "unsettled"
            else:
                self._late[point] = (result, error)
                self.late_outcomes[point] = "parked for the caller's settlement"
                return
        # The per-start lock serialises this thread and the owner's pass
        # on the same start (exactly-once stop rule).
        with RETAINED_HANDOVERS.lock(self._key(point)):
            if mode == "settled":
                self._handle_late(point, result, error)
            else:
                self._handover_unsettled(point, result, error)

    def _key(self, point):
        return (self.owner_ref, self.start_ids.get(point))

    def _retain(self, point, outcome, identity, error=None, stop_issued=False,
                absent_observed=None, why=""):
        """Keep a KNOWN result for the owner's later pass (S4e): never a
        memory-only dead end while the process lives."""
        start_id = self.start_ids.get(point)
        if start_id is None:
            return
        RETAINED_HANDOVERS.retain((self.owner_ref, start_id), {
            "point": point,
            "workflow_id": self.entry["workflow_id"],
            "outcome": outcome,
            "identity": None if identity is None else dict(identity),
            "error": None if error is None else error.__class__.__name__,
            "stop_issued": stop_issued,
            "absent_observed": absent_observed,
            "why": why,
        })
        self.late_outcomes[point] = "%s; retained for the owner's next pass" % why

    def _retry_pause(self):
        import time
        time.sleep(LATE_HANDOVER_RETRY_SECONDS)

    def _handover_unsettled(self, point, result, error):
        """The caller's settlement was refused: the owner settles the
        start itself with the late result — bounded retries until the
        source answers — then hands the result over exactly as after a
        successful settlement. If another pass of the same owner settled
        it UNCERTAIN first, the identity is persisted through an
        observation instead. Never an invocation."""
        gate = self.broker.mission_gate
        entry = self.entry
        start_id = self.start_ids.get(point)
        if start_id is None:
            self.late_outcomes[point] = "no start id; nothing to hand over"
            return
        if error is not None:
            outcome, identity = mission_gate_module.START_OUTCOME_FAILED, None
        elif result is None:
            outcome, identity = mission_gate_module.START_OUTCOME_UNCERTAIN, None
        else:
            outcome = mission_gate_module.START_OUTCOME_COMPLETED
            identity = self._identity_from(point, result)
        reason = ("abandoned: no response within %ss; the caller's settlement was"
                  " refused by the Mission source; late result settled by the"
                  " owner" % self.wait_seconds())
        last = None
        for attempt in range(1, MAX_LATE_HANDOVER_ATTEMPTS + 1):
            settled, refusal = gate.settle_start(
                entry, start_id, self.owner_ref, outcome, identity, reason)
            if refusal is None:
                with self._late_lock:
                    self._settled.add(point)
                    self._unsettled.discard(point)
                self.late_outcomes[point] = (
                    "settled %s by the owner after a refused settlement (attempt"
                    " %d)" % (outcome, attempt))
                if result is None or error is not None:
                    return
                # The identity is durable IN the settlement: stop, observe.
                absent, detail = self.broker._owned_stop(entry, identity)
                self._observe_with_retries(
                    point, start_id, absent,
                    "late return after the wait bound (owner settlement): %s"
                    % detail, identity)
                return
            last = refusal
            if refusal.problem == mission_gate_module.PROBLEM_START_STATE:
                # Settled meanwhile (the owner's recovery pass): persist the
                # identity through an observation instead.
                with self._late_lock:
                    self._settled.add(point)
                    self._unsettled.discard(point)
                self._handle_late(point, result, error)
                return
            self.late_outcomes[point] = (
                "hand-over retrying after a refused settlement (attempt %d refused:"
                " %s)" % (attempt, refusal.problem))
            if attempt < MAX_LATE_HANDOVER_ATTEMPTS:
                self._retry_pause()
        self._retain(point, outcome, identity, error,
                     why="hand-over exhausted after %d attempts (%s)"
                     % (MAX_LATE_HANDOVER_ATTEMPTS, last.problem))

    def _observe_with_retries(self, point, start_id, absent, detail, identity):
        """Persist one observation with bounded retries; consume the result."""
        gate = self.broker.mission_gate
        last = None
        for attempt in range(1, MAX_LATE_HANDOVER_ATTEMPTS + 1):
            observed, refusal = gate.observe_stop(
                self.entry, start_id, self.owner_ref, absent, detail, identity)
            if refusal is None:
                self.late_outcomes[point] = (
                    "absent=%s observed; canonical stop_confirmed=%s"
                    % (absent, observed["stop_confirmed"]))
                return observed
            last = refusal
            self.late_outcomes[point] = (
                "absent=%s observed; observation retrying (attempt %d refused: %s)"
                % (absent, attempt, refusal.problem))
            if attempt < MAX_LATE_HANDOVER_ATTEMPTS:
                self._retry_pause()
        # The identity is durable already (settlement or observation);
        # the stop was issued: retain that fact so the owner's pass
        # records the observation from a fresh listing, never a second
        # close of a workspace already observed absent.
        self._retain(point, mission_gate_module.START_OUTCOME_COMPLETED, identity,
                     stop_issued=True, absent_observed=absent,
                     why="absent=%s observed; observation not persisted after %d"
                     " attempts (%s)" % (absent, MAX_LATE_HANDOVER_ATTEMPTS,
                                        last.problem))
        return None

    def _handle_late(self, point, result, error):
        gate = self.broker.mission_gate
        entry = self.entry
        start_id = self.start_ids.get(point)
        if start_id is None:
            self.late_outcomes[point] = "no start id; nothing to hand over"
            return
        if error is not None or result is None:
            self.late_outcomes[point] = (
                "late %s; nothing was returned to own"
                % ("error %s" % error.__class__.__name__ if error is not None
                   else "empty result"))
            return
        identity = self._identity_from(point, result)
        # 1. Persist the late identity canonically BEFORE acting on it
        #    (bounded retries; the owned stop never precedes this record).
        persisted = None
        last = None
        for attempt in range(1, MAX_LATE_HANDOVER_ATTEMPTS + 1):
            observed, refusal = gate.observe_stop(
                entry, start_id, self.owner_ref, False,
                "late return after the wait bound: execution identity received;"
                " owned stop not yet attempted", identity)
            if refusal is None:
                persisted = observed
                break
            last = refusal
            self.late_outcomes[point] = (
                "identity persistence retrying (attempt %d refused: %s); owned"
                " stop not yet attempted" % (attempt, refusal.problem))
            if attempt < MAX_LATE_HANDOVER_ATTEMPTS:
                self._retry_pause()
        if persisted is None:
            # No unrecorded effect: without the canonical identity the
            # owned stop is NOT attempted; the known identity is RETAINED
            # for the owner's next pass (S4e).
            self._retain(point, mission_gate_module.START_OUTCOME_COMPLETED, identity,
                         why="identity not persisted after %d attempts (%s); owned"
                         " stop withheld" % (MAX_LATE_HANDOVER_ATTEMPTS, last.problem))
            return
        # 2. The owned stop, outside every lock.
        absent, detail = self.broker._owned_stop(entry, identity)
        # 3. Persist what was observed; consume the result.
        self._observe_with_retries(
            point, start_id, absent,
            "late return after the wait bound: %s" % detail, identity)

    def open(self, point):
        gate = self.broker.mission_gate
        broker = self.broker
        claim = claim_head(point, self.dispatch_sequence)
        # The claim receipt is durable BEFORE the canonical open: whatever
        # happens next (a crash after the canonical write, a save failure
        # of the admitted receipt), the record carries unresolved start
        # evidence of its own and stays protected from release and
        # pruning until the owner's pass resolves the claim.
        broker._mission_start_receipt(
            self.workflows, self.entry, claim, point, self.dispatch_sequence,
            START_STATE_CLAIMING, marker=MISSION_CLAIM_RECEIPT_MARKER)
        if point == dispatch_module.START_POINT_RUNTIME and self.dispatch_sequence > 1:
            # Task 8 startup correction: a corrective follow-up's runtime
            # start first RETIRES this workflow's own earlier runtime (see
            # ``TargetBroker._retire_predecessor``), BEFORE the canonical
            # open. The retiring head is durable before any of its reads.
            broker._mission_start_receipt(
                self.workflows, self.entry, claim, point, self.dispatch_sequence,
                START_STATE_CLAIM_RETIRING, marker=MISSION_CLAIM_RECEIPT_MARKER)
            refusal = broker._retire_predecessor(
                self.workflows, self.entry, self.dispatch_sequence, claim, point)
            if refusal is not None:
                broker._mission_start_receipt(
                    self.workflows, self.entry, claim, point, self.dispatch_sequence,
                    START_STATE_CLAIM_REFUSED, cause=refusal.problem,
                    marker=MISSION_CLAIM_RECEIPT_MARKER)
                return refusal
            # Retired: re-enter the CANONICAL claiming window before the open,
            # so a crash inside it resolves as it always did (canonical
            # absence: ``claim:unadmitted``, never resumed or replayed).
            broker._mission_start_receipt(
                self.workflows, self.entry, claim, point, self.dispatch_sequence,
                START_STATE_CLAIMING, marker=MISSION_CLAIM_RECEIPT_MARKER)
        start_id, refusal = gate.open_start(
            self.entry, self.dispatch_sequence, _CORE_START_POINT[point],
            self.owner_ref)
        if refusal is not None:
            broker._mission_start_receipt(
                self.workflows, self.entry, claim, point, self.dispatch_sequence,
                START_STATE_CLAIM_REFUSED, cause=refusal.problem,
                marker=MISSION_CLAIM_RECEIPT_MARKER)
            return refusal
        self.start_ids[point] = start_id
        broker._mission_start_receipt(
            self.workflows, self.entry, start_id, point, self.dispatch_sequence,
            START_STATE_ADMITTED, save=False)
        broker._mission_start_receipt(
            self.workflows, self.entry, claim, point, self.dispatch_sequence,
            START_STATE_CLAIM_ADMITTED, cause=start_id,
            marker=MISSION_CLAIM_RECEIPT_MARKER)
        return None

    def _identity_from(self, point, result):
        identity = dict(self.identity)
        if point == dispatch_module.START_POINT_RUNTIME and isinstance(result, dict):
            workspace_id = result.get("workspace_id")
            agents = result.get("agents")
            names = []
            if isinstance(agents, dict):
                names = [v for v in agents.values() if isinstance(v, str) and v]
            elif isinstance(agents, (list, tuple, set)):
                names = [v for v in agents if isinstance(v, str) and v]
            identity["workspace_id"] = (
                workspace_id if isinstance(workspace_id, str) and workspace_id
                else None)
            identity["agent_names"] = sorted(set(names))[
                :mission_gate_module.MAX_START_AGENT_NAMES]
        elif point == dispatch_module.START_POINT_TASK and isinstance(result, dict):
            task_id = result.get("id")
            identity["task_id"] = (task_id if isinstance(task_id, str) and task_id
                                   else None)
        self.identity = identity
        return dict(identity)

    def close(self, point, failed=False, result=None, abandoned=False):
        broker = self.broker
        gate = broker.mission_gate
        entry = self.entry
        start_id = self.start_ids[point]
        late_consumed = False
        if abandoned:
            # A late result that arrived BEFORE this settlement is
            # consumed by it: settled with what the engine returned.
            with self._late_lock:
                late = self._late.pop(point, None)
            if late is not None:
                late_consumed = True
                result, error = late
                failed = error is not None
                abandoned_late = "late result consumed at settlement"
                self.late_outcomes[point] = abandoned_late
        if failed:
            outcome, identity = mission_gate_module.START_OUTCOME_FAILED, None
        elif result is None or (abandoned and not late_consumed):
            outcome, identity = mission_gate_module.START_OUTCOME_UNCERTAIN, None
        else:
            outcome = mission_gate_module.START_OUTCOME_COMPLETED
            identity = self._identity_from(point, result)
        # The gate's own current-authority re-check at settlement
        # (lock-free): a TERMINAL refusal is the stop reason the core
        # records; a HOLD stops nothing already started (no native pause
        # exists) but admits no further step.
        admission = gate.admit(entry, mission_gate_module.BOUNDARY_SPAWN,
                               settling=start_id)
        stop_reason = None
        if not admission.ok and (
            admission.classification == mission_gate_module.CLASS_TERMINAL
        ):
            stop_reason = "%s: %s" % (admission.problem, admission.detail)
        if abandoned:
            # The caller gave the operation up: whatever it started must
            # stop, whether the result arrived late or not at all.
            stop_reason = ("abandoned: no response within %ss%s%s" % (
                self.wait_seconds(),
                "; late result consumed at settlement" if late_consumed else "",
                "; " + stop_reason if stop_reason else ""))
        settled, refusal = gate.settle_start(
            entry, start_id, self.owner_ref, outcome, identity, stop_reason)
        if refusal is None:
            with self._late_lock:
                self._settled.add(point)
                late = self._late.pop(point, None)
            if late is not None:
                # Arrived during the settlement: handed over now, in this
                # thread (identity persisted, owned stop, observation).
                self._handle_late(point, late[0], late[1])
        if refusal is not None:
            # UNSETTLED: a parked late result (or one arriving later) is
            # handed to the owner's bounded hand-over on its own thread —
            # never the caller's, which holds the workflow lock and must
            # not wait on the Mission source.
            import threading
            with self._late_lock:
                self._unsettled.add(point)
                late = self._late.pop(point, None)
            scheduled = ""
            if late is not None:
                key = self._key(point)

                def handover(point=point, late=late, key=key):
                    with RETAINED_HANDOVERS.lock(key):
                        self._handover_unsettled(point, late[0], late[1])
                worker = threading.Thread(target=handover, daemon=True)
                self.handover_threads.append(worker)
                worker.start()
                scheduled = "; late result hand-over scheduled"
            elif failed or result is not None:
                # A KNOWN result (a synchronous return, or a parked late
                # result this settlement consumed) whose record the
                # source refused: retained for the owner's next pass
                # (S4e), never left on this guard alone.
                self._retain(point, outcome, identity,
                             why="settlement refused (%s) with a known %s result"
                             % (refusal.problem, outcome))
                scheduled = "; known result retained for the owner's next pass"
            broker._mission_start_receipt(
                self.workflows, entry, start_id, point, self.dispatch_sequence,
                START_STATE_UNSETTLED, cause=refusal.problem + scheduled)
            return _StartClosure(
                False, None, refusal,
                "start %s at %s could not be settled (%s); its outcome is"
                " UNKNOWN, it is never re-invoked, and the owner settles it"
                " later%s" % (start_id, point, refusal.detail, scheduled), True,
                False)
        # The settlement receipt states its OWN stop requirement (S-V
        # retention crash windows): ``stop=none`` is the only completed
        # settlement the record layer treats as resolved; ``stop=pending``
        # keeps the record protected whether or not the later stop receipt
        # is ever written.
        broker._mission_start_receipt(
            self.workflows, entry, start_id, point, self.dispatch_sequence,
            "settled:%s" % outcome + (" (abandoned after %ss)" % self.wait_seconds()
                                      if abandoned else ""),
            stop=(record_module.START_RECEIPT_STOP_PENDING if settled["stop_pending"]
                  else record_module.START_RECEIPT_STOP_NONE))
        if not settled["stop_pending"]:
            return _StartClosure(False, None, admission, "settled", False, False)
        # A stop is required for what may have been started: perform the
        # owned stop now, outside every lock, record what was OBSERVED,
        # and report canonical CONFIRMATION only when the observation was
        # persisted (an unpersisted observation keeps the stop pending
        # and the observation retry path open).
        if abandoned and not late_consumed and point in self.late_outcomes and (
            self.late_outcomes[point] != "parked for the caller's settlement"
        ):
            # The late result was handed over during this close (above):
            # its own observations are canonical; report them.
            absent = self.late_outcomes[point].startswith("absent=True")
            detail = "late result handed over at settlement: %s" % (
                self.late_outcomes[point])
            confirmed = "stop_confirmed=True" in self.late_outcomes[point]
            stop_state = (START_STATE_STOP_CONFIRMED if confirmed else
                          START_STATE_STOP_OBSERVED_UNCONFIRMED if absent else
                          START_STATE_STOP_PENDING)
            broker._mission_start_receipt(
                self.workflows, entry, start_id, point, self.dispatch_sequence,
                stop_state, cause=stop_reason)
        else:
            absent, detail = broker._owned_stop(
                entry, None if (abandoned and not late_consumed) else self.identity)
            stop_state, confirmed = broker._record_stop_observation(
                self.workflows, entry, start_id, point, self.dispatch_sequence,
                self.owner_ref, absent, detail,
                cause=stop_reason or "stop pending from the canonical facts")
        if admission.ok:
            admission = mission_gate_module.Admission(
                False, mission_gate_module.PROBLEM_START_STOP_REQUIRED,
                "a stop is required for start %s at %s" % (start_id, point),
                mission_gate_module.CLASS_TERMINAL,
                mission_gate_module.BOUNDARY_SPAWN)
        if abandoned and late_consumed:
            detail = ("no response within %ss; the call was abandoned and its"
                      " late result consumed at settlement (settled %s with the"
                      " returned identity); %s"
                      % (self.wait_seconds(), outcome, detail))
        elif abandoned:
            detail = ("no response within %ss; the call was abandoned and the"
                      " start settled uncertain; %s"
                      % (self.wait_seconds(), detail))
        return _StartClosure(True, confirmed, admission,
                             "%s [%s]" % (detail, stop_state), False, absent)


class BrokerOutcome(object):
    """Result of one broker action; refusals never raise."""

    def __init__(self, ok, problem=None, detail=None, phase=None,
                 outcome=None):
        self.ok = ok
        self.problem = problem
        self.detail = detail
        self.phase = phase
        self.outcome = outcome


def _refused(problem, detail=None):
    return BrokerOutcome(False, problem=problem, detail=detail)


def _placeholder_dispatch_refusal(entry):
    """The R-6 INITIAL-dispatch placeholder gate, as a pure record read.

    Returns ``(problem, detail)`` to refuse, or ``(None, None)`` to
    permit. Reads the record only: no I/O, no clock, no write.

    **TRI-STATE, and the three states mean different things.** Reading
    this as a two-state "bound or not" test gets the legacy population
    wrong in the dangerous direction, so each is spelled out:

    1. ``result_placeholder is None`` -> **LEGACY LANE, UNGATED.**
       This does NOT mean "a placeholder is not needed". It means the
       record predates or sits outside the placeholder architecture
       (plan §4), and its verified result is delivered on the legacy
       **at-most-once** path. Gating on absence would refuse every
       pre-existing workflow forever; permitting it is a deliberate,
       disclosed narrowing of the guarantee, not an oversight.

    2. requested but ``state != bound`` (``required``, ``sending``,
       ``failed_unsent``, ``indefinite``, ``unbindable``) ->
       **REFUSE**, fail-closed, with this gate's own problem code.
       Once a placeholder has been REQUESTED, binding is mandatory:
       dispatching now would run a mission whose result has no bound
       object to be delivered into, which is precisely the
       at-most-once gap this task exists to close.

    3. ``state == bound`` -> permit.

    Why absence can be trusted to mean "legacy" (plan §1.1, Layer 1):
    the adapter writes the ``required`` request in the SAME locked
    load-modify-save transaction that arms the mission, so a
    go-forward workflow can never reach VALIDATED with a null
    placeholder. Without that atomicity, ``None`` would be ambiguous
    between "legacy" and "go-forward that lost its request", and this
    gate would be unsound.

    **DISCLOSURE (Supervisor §6, non-negotiable).** The acceptance
    criterion "Runtime dispatch must not occur until the result
    placeholder is durably bound" therefore holds **STRICTLY for
    go-forward, placeholder-requested workflows**. Pre-existing
    records dispatch **UNGATED** on the legacy at-most-once path.
    This is NOT full coverage and must not be read as such.
    """
    placeholder = entry.get("result_placeholder")
    if placeholder is None:
        return None, None
    state = placeholder.get("state")
    if state == record_module.PLACEHOLDER_BOUND:
        return None, None
    return PROBLEM_PLACEHOLDER_NOT_BOUND, (
        "the result placeholder for this workflow was REQUESTED but is"
        " not durably bound (state %r); initial dispatch is refused"
        " fail-closed until it is bound, so a mission never runs"
        " without an object to deliver its verified result into."
        " Nothing was dispatched and nothing was written." % (state,)
    )


class TargetBroker(object):
    """One Broker: one store directory, one control repository, one
    injected git transport, one injected role-turn runner."""

    def __init__(self, store_directory, control_repository_realpath,
                 transport, workspaces_root, role_turn_fn,
                 claude_config_path,
                 spawn_fn=None, clock=None, observer_fn=None,
                 spawn_records_fn=None, readiness_probe_fn=None,
                 workspace_close_fn=None, live_workspaces_fn=None,
                 capability_authority=None, worker=None, mission_gate=None,
                 delivery_store_directory=None, mission_delivery=None,
                 verification_scope_base=None):
        import time
        self.store = store_module.WorkflowStore(store_directory)
        # Task 8 S-VI: the Mission-bound delivery driver
        # (``mission_control.delivery.MissionDelivery``), wired by the
        # production CLI beside the gate; None means no Mission-origin
        # record is ever delivered (``delivery_not_wired``). The
        # verification producer's ownership-scope base defaults to the
        # production base; a hermetic test injects its own.
        self.mission_delivery = mission_delivery
        self.verification_scope_base = verification_scope_base
        # Task 8 S-VII: set by the production composition AFTER construction
        # (deliberately not constructor keywords): the engineering-runtime
        # readiness producer and the client attention desk the Runtime pass
        # drives. None (the default) means the pass does neither.
        self.mission_readiness = None
        self.mission_attention = None
        # Task 8 S-IV: the effect-boundary Mission gate for Mission-origin
        # records. DEFAULTS TO NONE, and None means this Broker refuses
        # every action on a Mission-origin record (fail closed); the
        # production CLI hands one in only when its configuration names a
        # Mission store. v2 records never consult it.
        self.mission_gate = mission_gate
        # Task 8 S-V (R2-11-b): the delivery store the reconciliation
        # bridge READS (P1-A6 candidate identity, validated step
        # receipts); None when no delivery store is configured.
        self.delivery_store_directory = delivery_store_directory
        # The one-shot capability seam (I3, behind the neutral
        # ``capability`` contract). ONE instance per Broker, bound to
        # THIS store's directory, so the Runtime's mint and this
        # Broker's consume read the same store; the production
        # default does no I/O at construction. Injected only so a
        # hermetic test can prove ``perform`` reaches no capability
        # function except through the seam.
        self.capability_authority = (
            capability_authority
            if capability_authority is not None
            else RuntimeCapabilityAuthority(self.store.directory)
        )
        self.control_realpath = control_repository_realpath
        self.transport = transport
        self.workspaces_root = workspaces_root
        # The user-global Claude configuration path (I1), REQUIRED
        # rather than defaulted so a hermetic test is never a writer of
        # the developer's own ~/.claude.json. Retained here only as the
        # value bound into the worker below; the Broker has no other
        # production reader of it.
        self.claude_config_path = claude_config_path
        # The handoff-validation Codex turn (I2 role_turn), injected
        # so hermetic tests never spawn a process; production wires
        # codex_gateway.role_turn.run_role_turn.
        self._role_turn = role_turn_fn
        # The child-spawn bridge (I5), injected the same way;
        # production wires dispatch.production_spawn (the EXISTING
        # herdr orchestrator bridge — no parallel path).
        self._spawn = spawn_fn or dispatch_module.production_spawn
        self._clock = clock or time.time
        # The canonical read-only Herdr observation (I5 D2), injected
        # so hermetic tests never touch a real target tree; production
        # wires herdr.observe. Reconciliation uses it for the LEASED
        # workspace's independently observed task identity.
        self._observe = observer_fn or _production_observer
        # A distinct narrow, read-only seam for ALL spawn records
        # persisted by the CONTROL repository. It never observes or
        # follows a child repository and does not alter canonical
        # observe()["children"] current-task correlation semantics.
        self._spawn_records = (
            spawn_records_fn or _production_spawn_records_observer
        )
        # The host-bound seam (behind the neutral ``worker`` contract):
        # workspace materialization, verification and relinquishment,
        # workspace trust, the I3 bootstrap-readiness probe, the
        # Domain B live-workspace projection, and the workspace close.
        # ONE instance per Broker, bound to THIS Broker's transport,
        # workspace root and configuration path, so the workspace the
        # worker materializes is the one the ownership predicates
        # below check containment against. Construction does no I/O.
        #
        # The readiness probe is optional with a production default,
        # following `observer_fn` rather than `claude_config_path`,
        # because it is a reader within its own scope: the argument
        # for making the config path required was that a defaulted
        # one would make a test a WRITER of the developer's
        # configuration, and that argument does not carry over to a
        # reader.
        #
        # DOMAIN B (R-29 / R-30 V-5): terminal cleanup of the Herdr
        # WORKSPACE a completed workflow leaves behind, and the
        # long-lived agent sessions inside it. `workspace_close_fn`
        # DEFAULTS TO NONE, and None means this Broker has NO
        # capability to close a workspace: it proves ownership and
        # reports, and closes no workspace. There is deliberately no
        # default reaching the real `herdr workspace close`. On a
        # machine carrying fifteen workspaces of which one is ours, a
        # mis-scoped close destroys other people's live sessions and
        # is unrecoverable in a way a leaked helper process is not,
        # so the capability is handed in on purpose or it is absent.
        # The worker reports that absence as `closes_workspaces`
        # (and the projection's as `observes_live_workspaces`), which
        # is a wiring fact and grants nothing.
        #
        # An injected `worker` REPLACES all three host callables. It
        # is refused together with any of them, because a worker plus
        # a host callable beside it is a second path to the same host
        # operation, and silently letting one win would hide it.
        if worker is not None and (
            readiness_probe_fn is not None
            or live_workspaces_fn is not None
            or workspace_close_fn is not None
        ):
            raise TypeError(
                "worker= replaces readiness_probe_fn,"
                " live_workspaces_fn and workspace_close_fn; pass"
                " the worker alone"
            )
        self.worker = (
            worker
            if worker is not None
            else RuntimeWorker(
                transport, workspaces_root, claude_config_path,
                readiness_probe_fn=(
                    readiness_probe_fn
                    or worker_module._production_readiness_probe
                ),
                live_workspaces_fn=live_workspaces_fn,
                workspace_close_fn=workspace_close_fn,
            )
        )

    # -- the fail-closed gate ------------------------------------------

    def _gate(self, workflows, workflow_id, action, revision):
        entry = workflows["workflows"].get(workflow_id)
        if entry is None:
            return None, _refused(PROBLEM_UNKNOWN_WORKFLOW)
        try:
            record_module.validate_record(entry)
        except record_module.RecordError as exc:
            # validate_record's TOTAL render binding (byte-equality
            # of the stored rendered text with the deterministic
            # rendering of the record's own fields) refuses every
            # field-vs-text tamper here; the former per-line
            # containment check was subsumed by it and removed as
            # dead code.
            return None, _refused(PROBLEM_RECORD_INVALID, str(exc))
        if record_module.is_mission_core_kind(entry):
            # Task 8 S-IV: with no Mission gate configured the S-III
            # refusal stands for EVERY action, before any handler, with
            # zero effects; with a gate whose S-V guards are absent the
            # hard missing-dependency refusal stands at the SAME point
            # (R-01 exactly as S-III: the token was consumed once above,
            # nothing else is written). Only a gate whose guards are
            # present runs the admission inside ``perform``'s containment
            # boundary (it may write a receipt).
            if self.mission_gate is None:
                return None, _refused(
                    PROBLEM_MISSION_KIND_NOT_ENABLED,
                    "workflow %s is a Mission-origin record (%s); no Mission"
                    " gate is configured in this Broker, so no action runs"
                    " on it"
                    % (workflow_id, record_module.APPROVAL_KIND_MISSION_CORE),
                )
            missing = self.mission_gate.missing_guards()
            if missing:
                refusal = mission_gate_module.integration.dependency_refusal(
                    missing)
                return None, _refused(refusal["problem"], refusal["detail"])
        if entry["control_identity"]["repository_realpath"] != (
            self.control_realpath
        ):
            return None, _refused(
                PROBLEM_WRONG_CONTROL,
                "record names control %r; this Runtime is pinned to"
                " %r" % (
                    entry["control_identity"]["repository_realpath"],
                    self.control_realpath,
                ),
            )
        # THE CONTROL-POLICY DIGEST, AND THE TWO CONDITIONS THAT MUST
        # NEVER BE CONFLATED (R-07).
        #
        # "Cannot compute the digest" and "computed it and it does not
        # match" are DIFFERENT FACTS, and only the second one is
        # drift. They are written as structurally separate branches
        # here, each reaching ONE explicit outcome, precisely so that
        # a DigestError can never fall through and read as "the digest
        # matched". An earlier shape shared a `live_digest = None`
        # fall-through between them; that is safe for ACTION_VERIFY
        # only by accident of a downstream re-imposition, and would be
        # a strictly LARGER hole for ACTION_RELEASE, which has none.
        digest_error = None
        live_digest = None
        try:
            live_digest = control_policy_digest(self.control_realpath)
        except DigestError as exc:
            digest_error = exc

        if digest_error is not None:
            # CONDITION 1 — THE POLICY SURFACE CANNOT BE READ AT ALL.
            #
            # This is NOT byte drift, and it is MORE severe than
            # drift, not less: drift means we can see the surface and
            # it changed; this means we cannot see it. The operator's
            # objective authorizes unstranding cleanup blocked solely
            # by policy BYTE DRIFT, and a DigestError is not byte
            # drift. So every action is refused here EXCEPT
            # ACTION_VERIFY.
            #
            # ACTION_VERIFY alone continues, and ONLY because the
            # verification precheck chain re-imposes the policy
            # comparison downstream via `_gate_control_policy`, where
            # it stops the workflow DURABLY after a fresh observation.
            # ACTION_RELEASE has NO such downstream re-imposition — it
            # goes straight to `_release` — which is exactly why it is
            # refused here rather than sharing this branch. Do not add
            # it: the exemption below covers a mismatched digest, not
            # an unreadable one.
            if action != ACTION_VERIFY:
                return None, _refused(
                    PROBLEM_POLICY_DRIFT, str(digest_error)
                )
        elif live_digest != entry["control_identity"][
            "policy_digest_sha256"
        ]:
            # CONDITION 2 — THE DIGEST COMPUTED, AND MISMATCHED.
            #
            # True byte drift of a READABLE policy surface. This, and
            # only this, is what the two exemptions defer.
            #
            # ACTION_VERIFY (preserved) and ACTION_RELEASE (R-03) are
            # named by EXACT EQUALITY, one action each — deliberately
            # not a phase predicate and not a set, so the exemption
            # cannot widen by someone adding a member. It defers
            # EXACTLY ONE CONJUNCT, this digest comparison, and
            # nothing else: workflow identity was already enforced
            # ABOVE this branch and is not exempted; revision,
            # approval (superseded / consumed / decision / validity)
            # and ambiguity are enforced BELOW it and are not
            # exempted; and `_release` still re-checks the terminal
            # phase, the recorded lease realpath, proven ownership
            # (UNPROVABLE refuses and removes nothing), ambiguity and
            # idempotent re-entry for itself.
            #
            # Why RELEASE at all: release closes only PROVEN-OWNED
            # resources, and a terminal workflow stranded by drift can
            # otherwise NEVER be released — its workspace, trust key,
            # sessions and scope records are stranded forever, while
            # `process_once` re-mints a capability for it on every
            # poll. Drift is repairable; permanent stranding is not.
            if action != ACTION_VERIFY and action != ACTION_RELEASE:
                return None, _refused(
                    PROBLEM_POLICY_DRIFT,
                    "the LIVE control policy digest does not match"
                    " the one this workflow was authorized under; the"
                    " policy surface drifted between authorization"
                    " and use",
                )
        # else: the digest computed AND matched — nothing is deferred
        # for any action, exempt or not.
        if revision != entry["handoff"]["revision"]:
            return None, _refused(
                PROBLEM_STALE_REVISION,
                "caller revision %r is not the record's handoff"
                " revision %r" % (
                    revision, entry["handoff"]["revision"],
                ),
            )
        approval = entry["approval"]
        if approval["superseded"]:
            return None, _refused(PROBLEM_SUPERSEDED)
        if approval["consumed_at"] is None:
            return None, _refused(
                PROBLEM_NOT_AUTHORIZED,
                "the one-shot approval was never consumed",
            )
        if approval["decision"] != record_module.DECISION_APPROVE:
            return None, _refused(PROBLEM_NOT_APPROVED)
        if approval["consumed_at"] > approval["expires_at"]:
            return None, _refused(
                PROBLEM_EXPIRED,
                "consumption recorded at %r is outside the approval"
                " validity ending %r" % (
                    approval["consumed_at"], approval["expires_at"],
                ),
            )
        if entry["ambiguity"]["state"] != record_module.AMBIGUITY_NONE:
            return None, _refused(
                PROBLEM_CRASH_AMBIGUOUS,
                "ambiguity state is %r (%s); a crash-ambiguous"
                " workflow is never advanced" % (
                    entry["ambiguity"]["state"],
                    entry["ambiguity"]["detail"],
                ),
            )
        required_phase = _REQUIRED_PHASE[action]
        resumption = (refused_claim_resumption(entry)
                      if action == ACTION_DISPATCH else None)
        if resumption is not None and resumption[1] == 1:
            # Task 8 S-VII (F-S7-1; correction 2, R1): the ONE DISPATCHED
            # state a dispatch acts on — the resumption of the SAME initial
            # dispatch whose runtime-start or task claim was durably refused
            # (no marker is written again; no second dispatch exists).
            required_phase = record_module.PHASE_DISPATCHED
        if required_phase is not None and entry["phase"] != (
            required_phase
        ):
            return None, _refused(
                PROBLEM_WRONG_PHASE,
                "action %r requires phase %s; the workflow is %s"
                " (replayed or out-of-order operation)" % (
                    action, required_phase, entry["phase"],
                ),
            )
        return entry, None

    # -- the Mission gate (Task 8 S-IV) -----------------------------------

    def _mission_admission(self, workflows, entry, boundary, mutate=None):
        """Consult the Mission gate for a Mission-origin ``entry`` at
        ``boundary``. With ``mutate`` (the caller's in-memory acceptance of
        a result: a transition, a receipt, a verified result) the check is
        the gate's SHORT Mission-lock critical section: ``mutate()`` and
        the durable save run inside it, only when admitted. Without it the
        check is a snapshot admission (before a long step) or an
        admission-only critical section (``mutate`` None with
        ``critical`` True: the pre-start check right before a spawn).
        Returns None when admitted, else the BrokerOutcome to return:

        - no gate configured -> ``broker_mission_kind_not_enabled``,
          zero effects (the S-III behaviour, every constructor);
        - DEPENDENCY refusal -> the problem, zero effects;
        - HOLD (reversible: an S-V hold, stale readiness, an exhausted
          follow-up budget, an UNAVAILABLE Mission source) -> at most one
          hold receipt per cause, phase preserved, saved; ``ok`` False
          with ``OUTCOME_MISSION_HELD``;
        - TERMINAL -> block receipt + locked PHASE_BLOCKED (when the phase
          allows it) saved; ``ok`` True with ``OUTCOME_MISSION_BLOCKED``.

        A v2 record never reaches here (callers check the kind)."""
        if self.mission_gate is None:
            return _refused(
                PROBLEM_MISSION_KIND_NOT_ENABLED,
                "workflow %s is a Mission-origin record (%s); no Mission"
                " gate is configured in this Broker, so no action runs on it"
                % (entry["workflow_id"],
                   record_module.APPROVAL_KIND_MISSION_CORE),
            )
        if boundary == mission_gate_module.BOUNDARY_CLEANUP:
            # R15-3: the cleanup admission is never an engineering one.
            admission = self.mission_gate.admit_cleanup(entry)
        elif mutate is None:
            admission = self.mission_gate.admit(entry, boundary)
        else:
            admission = self.mission_gate.admit_and_mark(
                entry, boundary,
                lambda: (mutate(), self.store.save(workflows)))
        if admission.ok:
            return None
        return self._mission_refusal_outcome(workflows, entry, admission)

    def _owned_stop(self, entry, identity):
        """The supported owned stop of what an engagement start started
        (start-claim decision, item 5): ownership of the PARTIAL or IDLE
        startup is proven from the execution identity the engine
        RETURNED to the start's owner (workspace id + exact agent set)
        against a fresh live listing through the same proof discipline
        the terminal release uses, the proven workspace is closed
        through the wired close capability, and the stop is CONFIRMED
        ONLY by a fresh listing in which that workspace is absent.
        Returns ``(absent, detail)``: True only on observed absence;
        False (pending) when no close/observation capability is wired,
        the identity cannot be proven, the close is refused, or the
        workspace is still listed — a close call's success is never
        absence proof. Blocking waits happen here, outside every lock."""
        from target_runtime import workspace_ownership as ws_module
        if not self.worker.observes_live_workspaces or (
            not self.worker.closes_workspaces
        ):
            return False, ("no owned workspace close/observation capability is"
                           " wired in this Broker; the stop is PENDING")
        try:
            live = bounded_engine_call(self.worker.live_workspaces,
                                       OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return False, ("live workspace listing unreadable (%s); the stop is"
                           " PENDING" % exc.__class__.__name__)
        malformed = live_listing_problem(live)
        if malformed is not None:
            # R15-5: absence is derived only from a COMPLETE listing.
            return False, ("live workspace listing unavailable or malformed (%s);"
                           " nothing is derived and the stop is PENDING" % malformed)
        verdict, snapshot, problem, detail = ws_module.prove_started_runtime(
            identity, live, ownership_module.recorded_lease_realpath(entry))
        if snapshot is None:
            if problem == ws_module.PROBLEM_WORKSPACE_NOT_FOUND:
                # Fresh absence: no live workspace carries the returned
                # id at all — nothing to close, absence OBSERVED.
                return True, ("workspace %s is ABSENT from a fresh listing;"
                              " nothing to close" % identity.get("workspace_id"))
            return False, ("ownership of the started runtime is %s (%s: %s);"
                           " nothing is closed and the stop is PENDING"
                           % (verdict, problem, detail))
        try:
            live_now = bounded_engine_call(self.worker.live_workspaces,
                                           OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return False, ("live listing unreadable at close time (%s); the"
                           " stop is PENDING" % exc.__class__.__name__)
        malformed = live_listing_problem(live_now)
        if malformed is not None:
            return False, ("live listing unavailable or malformed at close time"
                           " (%s); nothing is closed and the stop is PENDING"
                           % malformed)
        try:
            closed, workspace_id, problem, detail = ws_module.close_proven_workspace(
                snapshot, live_now, self._bounded_close(self.worker.close_workspace))
        except Exception as exc:                          # noqa: BLE001
            # A raising close is NOT a stop: nothing is confirmed, the
            # stop stays pending and the next pass retries it.
            return False, ("the owned close raised %s (%s); the stop is PENDING"
                           % (exc.__class__.__name__, exc))
        if not closed:
            return False, ("the proven close was refused (%s: %s); the stop is"
                           " PENDING" % (problem, detail))
        try:
            after = bounded_engine_call(self.worker.live_workspaces,
                                        OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return False, ("close issued for workspace %s but absence is"
                           " unobservable (%s); the stop is PENDING"
                           % (workspace_id, exc.__class__.__name__))
        malformed = live_listing_problem(after)
        if malformed is not None:
            return False, ("close issued for workspace %s but the fresh listing is"
                           " unavailable or malformed (%s); absence is NOT derived"
                           " and the stop is PENDING" % (workspace_id, malformed))
        present = [w for w in after if w["workspace_id"] == workspace_id]
        if present:
            return False, ("close issued for workspace %s but it is STILL"
                           " listed; the stop is PENDING" % workspace_id)
        return True, ("workspace %s closed and ABSENT from a fresh listing"
                      % workspace_id)

    def _observe_claimed_incarnation(self, entry, identity, claim):
        """The owned stop of a start whose incarnation a follow-up's
        retirement CLAIMED to close (``claim``: ``(follow-up, returned)``,
        or a reason the claims cannot be read). The close is NEVER re-issued
        on that incarnation: the claim was written before its engine call, so
        a new stop duty cannot prove the call was never made. Only a fresh
        COMPLETE listing is read: the workspace absent SETTLES the stop by
        observation (no close issued); still listed, the stop stays PENDING
        and its outcome is reported UNCERTAIN. Returns ``(absent, detail)``
        as ``_owned_stop`` does."""
        workspace_id = identity.get("workspace_id")
        follow_up, returned = claim
        if not isinstance(follow_up, int):
            known = ("the retirement close claims %s, so no close is issued" % follow_up)
        else:
            known = ("follow-up %d's retirement claimed its close before the engine"
                     " call; %s" % (follow_up, "its engine call is recorded as returned"
                                     if returned else "no return of its engine call is"
                                     " recorded, so whether the call was made is unknown"))
        if not self.worker.observes_live_workspaces:
            return False, ("%s; no live-workspace observation is wired; the stop is"
                           " PENDING" % known)
        try:
            live = bounded_engine_call(self.worker.live_workspaces, OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return False, ("%s; the live listing is unreadable (%s); the stop is"
                           " PENDING" % (known, exc.__class__.__name__))
        malformed = live_listing_problem(live)
        if malformed is not None:
            return False, ("%s; the live listing is unavailable or malformed (%s);"
                           " the stop is PENDING" % (known, malformed))
        if not any(w["workspace_id"] == workspace_id for w in live):
            return True, ("workspace %s is ABSENT from a fresh listing — %s; settled"
                          " by observed absence, no close issued" % (workspace_id, known))
        return False, ("workspace %s is still listed — %s; whether the listed"
                       " workspace is that incarnation or a later one is not observable;"
                       " its outcome is UNCERTAIN, the close is never re-issued on this"
                       " incarnation, and the stop is PENDING until its absence is"
                       " observed" % (workspace_id, known))

    @staticmethod
    def _bounded_close(close_fn):
        """``close_fn`` (the worker's close, HANDED IN — never called by the
        Broker itself) wrapped for the proof: bounded by
        ``OWNED_STOP_WAIT_SECONDS``, and never issued twice while an earlier
        close of the same workspace — abandoned at the bound — is still
        running in this process (``INFLIGHT_CLOSES``)."""
        def close(workspace_id):
            if not INFLIGHT_CLOSES.claim(workspace_id):
                raise OwnedStopBoundExceeded(
                    "a close of workspace %s abandoned at the bound earlier is"
                    " still running; it is not issued again" % workspace_id)
            try:
                result = bounded_engine_call(
                    lambda: close_fn(workspace_id), OWNED_STOP_WAIT_SECONDS,
                    on_late=lambda: INFLIGHT_CLOSES.release(workspace_id))
            except OwnedStopBoundExceeded:
                raise  # still in flight: released by its late return
            except BaseException:
                INFLIGHT_CLOSES.release(workspace_id)
                raise
            INFLIGHT_CLOSES.release(workspace_id)
            return result
        return close

    def _record_stop_observation(self, workflows, entry, start_id, point,
                                 dispatch_sequence, owner_ref, absent, detail,
                                 cause=None):
        """Persist the owner's stop observation canonically and receipt
        the record TRUTHFULLY: ``stop:confirmed`` only when absence was
        observed AND the canonical observation was recorded;
        ``stop:absence-observed-unconfirmed`` when absence was observed
        but the observation could not be persisted (source unavailable,
        stale sequence, capacity) — the stop requirement stays and the
        next pass retries the observation without any invocation;
        ``stop:pending`` otherwise. Returns ``(state, confirmed)``."""
        gate = self.mission_gate
        confirmed = False
        if absent:
            observed, refusal = gate.observe_stop(entry, start_id, owner_ref,
                                                  True, detail)
            if refusal is None and observed["stop_confirmed"]:
                state = START_STATE_STOP_CONFIRMED
                confirmed = True
            else:
                state = START_STATE_STOP_OBSERVED_UNCONFIRMED
                cause = ("canonical confirmation pending: %s" % (
                    refusal.problem if refusal is not None else "not confirmed"))
        else:
            observed, refusal = gate.observe_stop(entry, start_id, owner_ref,
                                                  False, detail)
            state = START_STATE_STOP_PENDING
        self._mission_start_receipt(workflows, entry, start_id, point,
                                    dispatch_sequence, state, cause=cause)
        return state, confirmed

    def _recover_pending_stops(self, workflows, entry):
        """Every settled start of this workflow whose stop is required
        and NOT canonically confirmed gets one more owned stop attempt
        and observation on this pass — never an invocation. A receipt is
        written only when the recorded state changes."""
        gate = self.mission_gate
        self._resolve_stale_claims(workflows, entry)
        starts = gate.engagement_starts(entry)
        if not starts:
            return
        points = {"runtime": dispatch_module.START_POINT_RUNTIME,
                  "task": dispatch_module.START_POINT_TASK}
        for start in starts:
            owner_ref = gate.owner_ref(entry, start["engagement_sequence"])
            if start["owner_ref"] != owner_ref:
                continue  # never another owner's start
            key = (owner_ref, start["start_id"])
            lock = RETAINED_HANDOVERS.lock(key)
            if not lock.acquire(False):
                continue  # the late thread is handing this start over right now
            try:
                self._recover_start(workflows, entry, start, owner_ref,
                                    points[start["point"]], RETAINED_HANDOVERS.get(key))
            finally:
                lock.release()

    def _recover_start(self, workflows, entry, start, owner_ref, point, retained):
        """One start's recovery on the owner's pass (S4e): the RETAINED
        known result is consumed FIRST — settle with its outcome and
        identity (or, if settled uncertain meanwhile, persist its identity
        through an observation) — then the owned stop (never before the
        identity's durable record; never a second close of a workspace a
        fresh listing no longer shows), then the absence-only
        observation; the retained entry is dropped only once the identity
        is durable AND an observation was recorded. Without a retained
        result an unsettled start is settled UNCERTAIN (stop pending,
        never confirmed). Never an invocation."""
        gate = self.mission_gate
        start_id = start["start_id"]
        key = (owner_ref, start_id)
        latest = None
        for receipt in reversed(entry["receipts"]):
            summary = receipt.get("bounded_summary") or ""
            if summary.startswith("%s %s:" % (MISSION_START_RECEIPT_MARKER, start_id)):
                latest = summary
                break
        if start["settlement"] is None:
            if retained is not None and retained["outcome"] != (
                mission_gate_module.START_OUTCOME_UNCERTAIN
            ):
                outcome = retained["outcome"]
                identity = retained["identity"]
                reason = ("owner pass: the settlement was refused earlier; the"
                          " retained %s result is settled now (%s)"
                          % (outcome, retained["why"]))
                state = START_STATE_SETTLED_RETAINED
            else:
                outcome, identity = mission_gate_module.START_OUTCOME_UNCERTAIN, None
                reason = ("owner recovery: the settlement was refused earlier and no"
                          " late result was recorded; outcome unknown")
                state = START_STATE_SETTLED_UNCERTAIN_RECOVERY
            settled, refusal = gate.settle_start(entry, start_id, owner_ref, outcome,
                                                 identity, reason)
            if refusal is not None:
                return  # still unsettled (and still retained); the next pass retries
            self._mission_start_receipt(workflows, entry, start_id, point,
                                        start["engagement_sequence"], state)
            if not settled["stop_pending"]:
                RETAINED_HANDOVERS.drop(key)
                return
            fresh = [s for s in gate.engagement_starts(entry) or []
                     if s["start_id"] == start_id]
            start = fresh[0] if fresh else start
        if not mission_gate_module.start_stop_required(start):
            RETAINED_HANDOVERS.drop(key)
            self._repair_settled_receipt(workflows, entry, start, point)
            return
        if mission_gate_module.start_stop_confirmed(start):
            RETAINED_HANDOVERS.drop(key)
            if latest is None or " state=%s" % START_STATE_STOP_CONFIRMED not in latest:
                self._mission_start_receipt(
                    workflows, entry, start_id, point, start["engagement_sequence"],
                    START_STATE_STOP_CONFIRMED, cause="recovery pass: canonical state")
            return
        identity = mission_gate_module.start_identity(start)
        if identity is None and retained is not None and retained["identity"] is not None:
            # Settled uncertain meanwhile: the retained identity becomes
            # durable through an observation BEFORE any stop.
            observed, refusal = gate.observe_stop(
                entry, start_id, owner_ref, False,
                "owner pass: retained execution identity recorded; owned stop not"
                " yet attempted (%s)" % retained["why"], retained["identity"])
            if refusal is not None:
                return  # keep the retained entry; the next pass retries
            identity = retained["identity"]
        # Task 8 startup correction: a start whose INCARNATION a follow-up's
        # retirement already claimed to close is never closed again by this
        # stop — a new stop duty does not prove the claimed close was never
        # invoked; only observed absence settles it (``_retirement_claim_on``,
        # ``_observe_claimed_incarnation``). A later incarnation keeps its own
        # duty.
        claim = (_retirement_claim_on(entry, lambda: gate.engagement_starts(entry), start,
                                      identity.get("workspace_id"))
                 if isinstance(identity, dict) else None)
        if claim is not None:
            absent, detail = self._observe_claimed_incarnation(entry, identity, claim)
        else:
            absent, detail = self._owned_stop(entry, identity)
        if not absent and latest is not None and (
            " state=%s" % START_STATE_STOP_PENDING in latest
        ) and retained is None:
            return
        state, _confirmed = self._record_stop_observation(
            workflows, entry, start_id, point, start["engagement_sequence"],
            owner_ref, absent, detail,
            cause="owner pass" if retained is not None else "recovery pass")
        if state != START_STATE_STOP_OBSERVED_UNCONFIRMED:
            RETAINED_HANDOVERS.drop(key)

    def _repair_settled_receipt(self, workflows, entry, start, point):
        """A canonically SETTLED start that owes no stop, whose DERIVED
        workflow receipt was lost — the record still reads it unresolved
        (e.g. ``admitted``: the process ended between the canonical
        settlement and the receipt) and so keeps the record falsely
        retention-protected — gets that receipt re-recorded FROM the
        canonical settlement. Derived evidence repaired: no Mission operation,
        no authority, nothing invoked; the canonical start stays the truth.

        Only once the record's target identity is bound (a follow-up, or an
        initial dispatch already bound): an unbound initial dispatch's own
        recovery — the R3 binding or D-B1 — owns its receipts."""
        start_id = start["start_id"]
        if start_id not in record_module.unresolved_start_receipts(entry):
            return
        if ownership_module.recorded_task_id(entry) is None:
            return
        settlement = start["settlement"] or {}
        if settlement.get("outcome") != mission_gate_module.START_OUTCOME_COMPLETED:
            return
        self._mission_start_receipt(
            workflows, entry, start_id, point, start["engagement_sequence"],
            record_module.SETTLED_COMPLETED_STATE,
            cause="(recovery pass: the canonical settlement)",
            stop=record_module.START_RECEIPT_STOP_NONE)

    def _update_retention(self, workflows, entry):
        """Task 8 S-V (R2-2): release the record's retention exactly once
        when its Mission's cancel is CONFIRMED or its revision was
        superseded (an EDIT) — read from the Mission source; a source
        that cannot answer changes nothing. The record layer keeps a
        record with an unresolved engagement start protected regardless
        (``record.retention_protects``), so this release never exposes a
        start whose stop is unconfirmed to pruning or cleanup. A release
        is never undone and a deadline is never extended."""
        retention = entry.get(record_module.RETENTION_KEY)
        if not isinstance(retention, dict) or retention["released_at"] is not None:
            return
        gate = self.mission_gate
        if gate is None:
            return
        linkage = entry.get(record_module.MISSION_AUTHORITY_KEY) or {}
        mission_id = linkage.get("mission_id")
        reason = None
        try:
            stored = gate.service.get(mission_id)
            controls = gate.service.mission_controls(mission_id)
        except Exception:                                 # noqa: BLE001
            return  # unavailable or unknown: nothing changes
        if controls.get("cancel_confirmed"):
            reason = record_module.RETENTION_RELEASE_CANCEL_CONFIRMED
        elif controls.get("cancel_requested") and self.mission_delivery is not None and (
            self.mission_delivery.declined(entry, controls)
        ):
            # Task 8 S-VI (S-V's ``declined`` hook): the sticky cancel IS the
            # human's client-confirmed decline of THIS workflow's delivery
            # proposal. The candidate is released from retention; the record
            # layer and the canonical obligations still keep any start whose
            # stop is unconfirmed from cleanup, and confirming the cancel
            # stays a control principal's act.
            reason = record_module.RETENTION_RELEASE_DECLINED
        elif stored["record"]["current_revision"] != linkage.get("revision"):
            reason = record_module.RETENTION_RELEASE_REVISION_SUPERSEDED
        if reason is None:
            return
        retention["released_at"] = self._clock()
        retention["release_reason"] = reason
        self.store.save(workflows)

    def _mission_start_receipt(self, workflows, entry, start_id, point,
                               dispatch_sequence, state, cause=None, stop=None,
                               save=True, marker=MISSION_START_RECEIPT_MARKER):
        """One durable start receipt on the WORKFLOW record (the canonical
        start record lives in the Mission store): claiming, admitted,
        settled (with its own ``stop=`` statement), stop confirmed/pending,
        unsettled. Saved under the workflow lock the action already holds
        (``save`` False: appended only, for a caller that saves several
        receipts in ONE write)."""
        summary = "%s %s: point=%s dispatch=%d state=%s" % (
            marker, start_id, point, dispatch_sequence, state)
        if cause is not None:
            summary += " cause=%s" % cause
        if stop is not None:
            summary += " stop=%s" % stop
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mstart-" + start_id[-12:],
            "recorded_at": self._clock(),
            "digest": entry[record_module.MISSION_AUTHORITY_KEY][
                "authorization_digest_sha256"],
            "bounded_summary": summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }]
        if save:
            self.store.save(workflows)

    def _resolve_stale_claims(self, workflows, entry):
        """S-V retention crash windows: every ``claiming`` head whose
        owner is this Broker's owner reference is resolved from the
        canonical facts on the owner's pass — ``claim:admitted`` when the
        canonical start for its point and dispatch exists (its own
        receipts follow from ``_recover_start``), ``claim:unadmitted``
        when no start was ever admitted (the crash preceded the canonical
        write; nothing was invoked). Never an invocation."""
        gate = self.mission_gate
        unresolved = record_module.unresolved_start_receipts(entry)
        # Task 8 startup correction: a ``claim:retiring`` head (a follow-up's
        # pre-claim retirement, interrupted) resolves the same way, except
        # that with no canonical start it is REFUSED — positively: that step
        # precedes every canonical open, so nothing was admitted — and the
        # point is resumed (the retirement is re-proven, never replayed).
        claims = sorted(head for head, state in unresolved.items()
                        if head.startswith(CLAIM_HEAD_PREFIX)
                        and state in (START_STATE_CLAIMING, START_STATE_CLAIM_RETIRING))
        if not claims:
            return
        starts = gate.engagement_starts(entry)
        if starts is None:
            return  # the source cannot answer: the claim stays protected
        for head in claims:
            parsed = parse_claim_head(head)
            if parsed is None:
                continue
            point, dispatch_sequence = parsed
            owner_ref = gate.owner_ref(entry, dispatch_sequence)
            admitted = [s for s in starts
                        if s["owner_ref"] == owner_ref
                        and s["point"] == _CORE_START_POINT[point]]
            if admitted:
                self._mission_start_receipt(
                    workflows, entry, head, point, dispatch_sequence,
                    START_STATE_CLAIM_ADMITTED,
                    cause=admitted[0]["start_id"] + " (recovery pass)",
                    marker=MISSION_CLAIM_RECEIPT_MARKER)
            elif unresolved[head] == START_STATE_CLAIM_RETIRING:
                self._mission_start_receipt(
                    workflows, entry, head, point, dispatch_sequence,
                    START_STATE_CLAIM_REFUSED,
                    cause=PROBLEM_PREDECESSOR_INTERRUPTED + " (recovery pass: the"
                          " retirement was interrupted before any canonical open)",
                    marker=MISSION_CLAIM_RECEIPT_MARKER)
            else:
                self._mission_start_receipt(
                    workflows, entry, head, point, dispatch_sequence,
                    START_STATE_CLAIM_UNADMITTED,
                    cause="recovery pass: no canonical start exists for this claim",
                    marker=MISSION_CLAIM_RECEIPT_MARKER)

    def _mission_refusal_outcome(self, workflows, entry, admission):
        """The durable consequence of a gate refusal, by classification."""
        if admission.classification == mission_gate_module.CLASS_DEPENDENCY:
            return _refused(admission.problem, admission.detail)
        if admission.classification == mission_gate_module.CLASS_HOLD:
            self._mission_hold(workflows, entry, admission)
            return BrokerOutcome(
                False, problem=admission.problem, detail=admission.detail,
                phase=entry["phase"], outcome=OUTCOME_MISSION_HELD,
            )
        self._mission_block(workflows, entry, admission)
        return BrokerOutcome(
            True, problem=admission.problem, detail=admission.detail,
            phase=entry["phase"], outcome=OUTCOME_MISSION_BLOCKED,
        )

    def _apply_mission_admission(self, workflow_id, admission):
        """The Runtime's durable recording of a gate refusal it obtained
        OUTSIDE an action (before a pre-mint planning/recovery turn, or
        after one whose outcome it discards): under the workflow lock,
        the same hold/block discipline as in-action refusals. Returns the
        BrokerOutcome the Runtime records; a dependency refusal writes
        nothing."""
        if admission.ok:
            return BrokerOutcome(True)
        if admission.classification == mission_gate_module.CLASS_DEPENDENCY:
            return _refused(admission.problem, admission.detail)
        with store_module.exclusive_store_lock(self.store.directory):
            try:
                workflows = self.store.load()
            except store_module.StoreError as exc:
                return _refused(PROBLEM_STORE_UNREADABLE, str(exc))
            entry = workflows["workflows"].get(workflow_id)
            if entry is None:
                return _refused(PROBLEM_UNKNOWN_WORKFLOW)
            return self._mission_refusal_outcome(workflows, entry, admission)

    def maintain(self, workflow_id, operation):
        """Task 8 S-V: the Runtime pass's MAINTENANCE of ONE gated
        Mission-origin record — the only public entry beside ``perform``,
        for work that belongs to no lifecycle action and so has no
        capability to present:

        - ``MAINTAIN_RECOVERY`` — the owner's recovery of unresolved start
          evidence (a ``claiming`` claim, an admitted or unsettled start,
          a pending stop), whatever the record's phase, so a record
          BLOCKED by an unsavable write or a terminal refusal still gets
          its claims resolved, its starts settled and its owned stop
          attempted (workflow lock, then the Mission lock);
        - ``MAINTAIN_RETENTION`` — the retention release (R2-2) when the
          Mission's cancel is confirmed or its revision superseded (a
          protected terminal record is never a cleanup candidate, so no
          action would otherwise reach it);
        - ``MAINTAIN_CANDIDATE`` — the read-only candidate observation
          (R2-11-b), recorded as a receipt only when it changed;
        - ``MAINTAIN_DELIVERY`` — Task 8 S-VI: the Mission-bound delivery of
          a COMPLETED record: the Runtime-owned verification run when the
          delivery needs one (``_delivery_verification``), then one pass of
          the injected ``mission_control.delivery`` driver;
        - ``MAINTAIN_RETIREMENT`` — Task 8 startup correction: the PRE-MINT,
          READ-ONLY re-assessment of a refused follow-up whose resumption
          would retire its earlier runtime (``_maintain_retirement``).

        Never an invocation, never a capability, never a lifecycle phase
        change. Under the workflow lock, with exactly ONE containment try
        catching ``StoreError`` and ``RecordError`` and routing to the
        SAME durable stop as ``perform`` (a record that cannot grow is
        stopped, never a raise out of the Runtime) — derived and pinned by
        the containment test beside ``perform``'s."""
        if operation not in MAINTENANCE_OPERATIONS:
            return _refused(PROBLEM_UNKNOWN_MAINTENANCE,
                            "unknown maintenance operation %r; the set is fixed"
                            % (operation,))
        with store_module.exclusive_store_lock(self.store.directory):
            try:
                workflows = self.store.load()
            except store_module.StoreError as exc:
                return _refused(PROBLEM_STORE_UNREADABLE, str(exc))
            entry = workflows["workflows"].get(workflow_id)
            if entry is None:
                return _refused(PROBLEM_UNKNOWN_WORKFLOW)
            if not record_module.is_mission_core_kind(entry) or self.mission_gate is None:
                return _refused(PROBLEM_MISSION_KIND_NOT_ENABLED,
                                "maintenance applies to gated Mission-origin records only")
            try:
                if operation == MAINTAIN_RECOVERY:
                    return self._maintain_recovery(workflows, entry)
                if operation == MAINTAIN_RETENTION:
                    return self._maintain_retention(workflows, entry)
                if operation == MAINTAIN_DELIVERY:
                    return self._maintain_delivery(workflows, entry)
                if operation == MAINTAIN_RETIREMENT:
                    return self._maintain_retirement(workflows, entry)
                return self._maintain_candidate(workflows, entry)
            except (store_module.StoreError,
                    record_module.RecordError) as exc:
                return self._contain_unsavable_record(workflow_id, exc)

    def _maintain_recovery(self, workflows, entry):
        before = record_module.unresolved_start_receipts(entry)
        self._recover_pending_stops(workflows, entry)
        after = record_module.unresolved_start_receipts(entry)
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_RECOVERY_PASSED,
            detail="unresolved start evidence before: %d, after: %d"
                   % (len(before), len(after)))

    def _maintain_retention(self, workflows, entry):
        self._update_retention(workflows, entry)
        retention = entry.get(record_module.RETENTION_KEY) or {}
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=(OUTCOME_RETENTION_RELEASED if retention.get("released_at") is not None
                     else OUTCOME_RETENTION_RETAINED),
            detail="retention %s" % (retention.get("release_reason") or "retained"))

    def _maintain_candidate(self, workflows, entry):
        """The pass-level candidate observation. WHERE and AGAINST WHAT:
        with a P1-A6 delivery record bound to the record's Mission (read
        through the bridge's read-only delivery read), the delivery's
        repository against its CURRENT base (delivery-phase semantics);
        otherwise, once verification has accepted a result (phase
        VERIFIED or COMPLETED) and while the lease is held, the leased
        workspace against the recorded authorized baseline. Nothing is
        observed while engineering is still running (DISPATCHED: the
        verification collection records the reviewed candidate) or once
        the workspace is released. A NEW observation is recorded as one
        receipt; an unchanged one records nothing."""
        target, reason = self._candidate_target(entry)
        if target is None:
            return BrokerOutcome(True, phase=entry["phase"],
                                 outcome=OUTCOME_CANDIDATE_NOT_OBSERVED,
                                 detail=reason)
        repository_path, base_oid = target
        observation = capture_candidate(self.transport, repository_path, base_oid)
        receipt = candidate_receipt(observation, self._clock())
        if same_candidate_observation(observed_candidate(entry), receipt):
            return BrokerOutcome(True, phase=entry["phase"],
                                 outcome=OUTCOME_CANDIDATE_UNCHANGED,
                                 detail="candidate %s unchanged" % observation["status"])
        entry["receipts"] = list(entry["receipts"]) + [receipt]
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=OUTCOME_CANDIDATE_OBSERVED,
            problem=observation["problem"],
            detail=("candidate %s against base %s (%s)" % (
                observation["status"], base_oid,
                observation["detail"] or "exact"))[:MAX_OUTCOME_DETAIL_CHARS])

    def _maintain_delivery(self, workflows, entry):
        """Task 8 S-VI: one delivery pass of a gated Mission-origin record
        (see ``mission_control.delivery``). The Runtime's own part runs
        here: the VERIFICATION PRODUCER, exactly when the driver's plan
        says no verification record binds the current exact candidate.
        Everything after it — evidence, the preparation write, minting,
        the gated P1-A6 drive, attestation, completion — is the driver's,
        under this workflow lock."""
        delivery = self.mission_delivery
        if delivery is None:
            return BrokerOutcome(True, phase=entry["phase"],
                                 outcome=OUTCOME_DELIVERY_NOT_WIRED,
                                 detail="no Mission delivery driver is wired")
        plan = delivery.plan(entry)
        if not plan["applicable"]:
            return BrokerOutcome(True, phase=entry["phase"],
                                 outcome=OUTCOME_DELIVERY_NOT_APPLICABLE,
                                 problem=plan["problem"], detail=plan["detail"])
        if plan["needs_verification"]:
            refusal = self._delivery_verification(workflows, entry, plan)
            if refusal is not None:
                return refusal
        result = delivery.advance(entry, lambda: self.store.save(workflows))
        return BrokerOutcome(
            result["status"] not in ("held", "blocked"), phase=entry["phase"],
            outcome=OUTCOME_DELIVERY_PREFIX + result["status"],
            problem=result["problem"],
            detail=("%s%s" % (
                result["detail"] or "",
                " (delivery %s)" % result["delivery_id"]
                if result["delivery_id"] else ""))[:MAX_OUTCOME_DETAIL_CHARS]
            or None)

    def _delivery_verification(self, workflows, entry, plan):
        """Run the Mission's APPROVED verification argv once in the leased
        workspace through the owned-process producer
        (``target_runtime.verification``): admitted at the delivery
        boundary, the candidate captured before and after (a candidate that
        moved during the run is never verified — the record is left
        unreceipted), and the result accepted only through the gate's short
        critical section (``admit_and_mark``), so a cancel, hold or EDIT
        during the run keeps it out. Returns None when the receipt was
        recorded, else the refusal outcome.

        Task 8 R19-2: the launch itself is admitted AFRESH after the
        blocking capture and the barrier below, immediately before the
        producer is invoked — the cleanup sites' shape: ``admit_and_mark``
        re-admits under the Mission lock and only then durably CLAIMS the
        attempt, so a hold, cancel, EDIT or source outage that landed while
        the candidate was captured refuses with the producer never invoked.
        R19-3: before any claim, the prior-settlement barrier
        (``_verification_barrier``) refuses while an earlier attempt's
        ownership or settlement is unresolved; every attempt that is
        claimed is SETTLED by this pass once the producer returns or
        refuses to start."""
        from mission_control import delivery_artifacts
        from target_runtime import process_ownership
        from target_runtime import verification as verification_module
        boundary = mission_gate_module.BOUNDARY_DELIVERY_EFFECT
        admission = self.mission_gate.admit(entry, boundary)
        if not admission.ok:
            return BrokerOutcome(False, phase=entry["phase"],
                                 outcome=OUTCOME_MISSION_HELD,
                                 problem=admission.problem, detail=admission.detail)
        lease = entry["workspace_lease"]["path_realpath"]
        before = capture_candidate(self.transport, lease, plan["base"])
        if before["status"] != CANDIDATE_STATUS_EXACT or (
            before["digest"] != plan["identity"] or before["head"] != plan["base"]
        ):
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "waiting",
                problem=PROBLEM_VERIFICATION_CANDIDATE_MOVED,
                detail="the leased candidate no longer matches its observation;"
                       " nothing was verified")
        refusal, roots = self._verification_barrier(workflows, entry)
        if refusal is not None:
            return refusal
        attempts, _undecodable = _verification_attempts(entry)
        attempt = max(set(attempts) | {0}) + 1
        claim = self._verification_attempt_receipt(attempt, (
            "%s roots=%d (candidate %s base %s; claimed before the producer call, not"
            " proof of it)" % (VERIFICATION_ATTEMPT_CLAIMED, roots, plan["identity"],
                               plan["base"])))

        def mark(claim=claim):
            entry["receipts"] = list(entry["receipts"]) + [claim]
            self.store.save(workflows)
        admission = self.mission_gate.admit_and_mark(entry, boundary, mark)
        if not admission.ok:
            return BrokerOutcome(False, phase=entry["phase"],
                                 outcome=OUTCOME_MISSION_HELD,
                                 problem=admission.problem, detail=admission.detail)
        # Settled by the PHASE the producer reached (``verification.produce``).
        try:
            digest, produced = verification_module.produce(
                plan["argv"], lease, entry["workflow_id"], plan["mission_id"],
                plan["revision"], entry["control_identity"]["repository_realpath"],
                plan["identity"], plan["base"], self.store.directory, self._clock,
                scope_base=self.verification_scope_base)
        except verification_module.VerificationStartUnknown as unknown:
            cause = unknown.__cause__.__class__.__name__
            self._settle_verification_attempt(workflows, entry, attempt, (
                VERIFICATION_ATTEMPT_START_UNKNOWN,
                "the spawn raised %s; whether a process started is not known on this"
                " pass" % cause))
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "held",
                problem=PROBLEM_VERIFICATION_START_UNKNOWN,
                detail=("the verification's spawn raised %s: whether a process started"
                         " is not known; the next pass decides from its owned roots, and"
                         " nothing is re-run meanwhile" % cause))
        except verification_module.VerificationOutcomeUnknown as unknown:
            started = ("the verification STARTED and waiting for it failed (%s): its exit"
                       " status is unknown (its process %s%s)" % (
                           unknown.__cause__.__class__.__name__, unknown.settlement,
                           "; its reap failed too (%s)" % unknown.reap_error.__class__.__name__
                           if unknown.reap_error is not None else ""))
            self._settle_verification_attempt(workflows, entry, attempt, (
                VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN, started))
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "blocked",
                problem=PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                detail="%s; it is never re-run, and no further attempt starts" % started)
        except verification_module.VerificationUnrecorded as unrecorded:
            ran = ("the verification RAN (exit status %s, its process %s) and storing its"
                   " result failed (%s)" % (unrecorded.exit_status, unrecorded.settlement,
                                            unrecorded.__cause__.__class__.__name__))
            self._settle_verification_attempt(workflows, entry, attempt, (
                VERIFICATION_ATTEMPT_UNRECORDED, ran))
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "blocked",
                problem=PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                detail="%s; it is never re-run, and no further attempt starts" % ran)
        except (OSError, ValueError, process_ownership.SpawnGated) as exc:
            self._settle_verification_attempt(workflows, entry, attempt, (
                VERIFICATION_ATTEMPT_NOT_STARTED,
                "refused before any process was started (%s)" % exc.__class__.__name__))
            return BrokerOutcome(False, phase=entry["phase"],
                                 outcome=OUTCOME_DELIVERY_PREFIX + "held",
                                 problem=PROBLEM_VERIFICATION_START,
                                 detail=("the verification could not start: %s"
                                         % exc.__class__.__name__))
        self._settle_verification_attempt(workflows, entry, attempt, (
            VERIFICATION_ATTEMPT_RETURNED,
            "record %s (its process %s)" % (digest, produced["settlement"])))
        after = capture_candidate(self.transport, lease, plan["base"])
        if (after["status"], after["digest"], after["head"]) != (
            before["status"], before["digest"], before["head"]
        ):
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "waiting",
                problem=PROBLEM_VERIFICATION_CANDIDATE_MOVED,
                detail="the candidate moved during the verification run; its"
                       " record %s is not used" % digest)
        receipt = delivery_artifacts.verification_receipt(digest, produced,
                                                          self._clock())

        def mark():
            entry["receipts"] = list(entry["receipts"]) + [receipt]
            self.store.save(workflows)
        admission = self.mission_gate.admit_and_mark(entry, boundary, mark)
        if not admission.ok:
            return BrokerOutcome(False, phase=entry["phase"],
                                 outcome=OUTCOME_MISSION_HELD,
                                 problem=admission.problem, detail=admission.detail)
        return None

    def _verification_barrier(self, workflows, entry):
        """Task 8 R19-3 — the PRIOR-SETTLEMENT barrier every verification
        attempt passes before it is claimed. Returns ``(None, roots)`` when
        an attempt may be claimed — ``roots``, the owned-root count its
        claim records — else ``(refusal, None)``; the producer is never
        invoked by a refused pass. Its reads are blocking reads, so the
        launch admission is taken AFTER them.

        1. Attempt records that do not decode BLOCK every attempt.
        2. OWNERSHIP, from the verification scope's existing ownership
           records (``verification.prior_ownership``): a process of an
           earlier attempt that may still be alive, or records that cannot
           be read, HOLD every attempt — re-checked each pass; nothing is
           signalled here (startup recovery reaps an owner-dead group
           under the same assignment).
        3. SETTLEMENT, only once (2) shows no verification process of
           this workflow can be alive: an attempt CLAIMED and never
           settled is durably settled ``interrupted``; a ``start-unknown``
           attempt is resolved from the owned roots its claim counted — none
           created since: ``not-started``; one created: ``outcome-unknown``
           (a process started). Any attempt settled NOT REPLAYABLE blocks
           every further attempt: it ran or may have run, and its result is
           not recorded."""
        from target_runtime import verification as verification_module
        attempts, undecodable = _verification_attempts(entry)
        if undecodable:
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "blocked",
                problem=PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                detail=("%d verification attempt record(s) do not decode, so no earlier"
                        " attempt can be read as settled; no attempt starts" % undecodable)
            ), None
        workflow_id = entry["workflow_id"]
        control = entry["control_identity"]["repository_realpath"]
        state, detail = verification_module.prior_ownership(
            workflow_id, control, scope_base=self.verification_scope_base)
        roots = None
        if state == verification_module.PRIOR_CLEAR:
            roots, unreadable = verification_module.owned_root_count(
                workflow_id, control, scope_base=self.verification_scope_base)
            if roots is None:
                state, detail = verification_module.PRIOR_UNAVAILABLE, unreadable
        if state != verification_module.PRIOR_CLEAR:
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "held",
                problem=PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED,
                detail=("an earlier verification process's ownership is %s: %s; no"
                        " attempt starts" % (state, detail))), None
        resolved = []
        for number in sorted(attempts):
            kind, before = attempts[number]["kind"], attempts[number]["roots"]
            if kind is None:
                resolved.append((number, VERIFICATION_ATTEMPT_INTERRUPTED, (
                    "claimed and never settled by its own pass; no verification process"
                    " of this workflow can be alive now (%s); its outcome is unknown"
                    % detail)))
            elif kind == VERIFICATION_ATTEMPT_START_UNKNOWN and before is not None:
                if roots == before:
                    resolved.append((number, VERIFICATION_ATTEMPT_NOT_STARTED, (
                        "resolved: its spawn created no owned root (%d before it, %d now),"
                        " so no process started" % (before, roots))))
                else:
                    resolved.append((number, VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN, (
                        "resolved: an owned root was created for it (%d before it, %d"
                        " now), so a process started; %s; its outcome is unknown"
                        % (before, roots, detail))))
        if resolved:
            entry["receipts"] = list(entry["receipts"]) + [
                self._verification_attempt_receipt(number, "%s: %s — %s" % (
                    VERIFICATION_ATTEMPT_SETTLED, kind, text))
                for number, kind, text in resolved]
            self.store.save(workflows)
            attempts, _undecodable = _verification_attempts(entry)
        barred = [(number, attempts[number]["kind"]) for number in sorted(attempts)
                  if attempts[number]["kind"] in VERIFICATION_ATTEMPT_NOT_REPLAYABLE
                  or attempts[number]["kind"] == VERIFICATION_ATTEMPT_START_UNKNOWN]
        if barred:
            return BrokerOutcome(
                False, phase=entry["phase"], outcome=OUTCOME_DELIVERY_PREFIX + "blocked",
                problem=PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                detail=("verification attempt(s) %s ran or may have run in the lease and"
                        " their result is not recorded; they are never replayed, and no"
                        " further attempt starts" % ", ".join(
                            "%d (%s)" % pair for pair in barred))), None
        return None, roots

    def _verification_attempt_receipt(self, attempt, text):
        import secrets
        summary = "%s %d %s" % (VERIFICATION_ATTEMPT_RECEIPT_MARKER, attempt, text)
        return {
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mverify-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }

    def _settle_verification_attempt(self, workflows, entry, attempt, settlement):
        """Durably settle ``attempt`` as ``(kind, text)``."""
        kind, text = settlement
        entry["receipts"] = list(entry["receipts"]) + [self._verification_attempt_receipt(
            attempt, "%s: %s — %s" % (VERIFICATION_ATTEMPT_SETTLED, kind, text))]
        self.store.save(workflows)

    def _candidate_target(self, entry):
        """``((repository_path, base_oid), None)`` or ``(None, reason)``."""
        from mission_control import reconciliation_bridge as bridge
        linkage = entry.get(record_module.MISSION_AUTHORITY_KEY) or {}
        if self.delivery_store_directory is not None:
            delivery = bridge.mission_delivery(self.delivery_store_directory,
                                               linkage.get("mission_id"))
            if delivery["record"] is not None:
                record = delivery["record"]
                return ((record["repository"]["realpath"],
                         record["base_state"]["current_base_oid"]), None)
            if delivery["problem"] is not None:
                return None, "delivery store: %s" % delivery["problem"]
        lease = entry.get("workspace_lease")
        if not isinstance(lease, dict) or lease.get("released_at") is not None:
            return None, "no held workspace lease"
        if entry["phase"] not in CANDIDATE_OBSERVATION_PHASES:
            return None, "phase %s observes no candidate" % entry["phase"]
        return ((lease["path_realpath"], entry["approved_baseline"]["commit_sha"]),
                None)

    def _mission_recheck(self, workflows, entry, boundary, mutate):
        """The re-check every long step's RESULT passes through before it
        is accepted. Returns ``(refusal, accepted)``: for a v2 record
        ``(None, False)`` — the caller applies ``mutate`` and saves itself
        (no Mission gate applies); for an admitted Mission-origin record
        ``(None, True)`` — ``mutate`` and the save already ran inside the
        gate's critical section; else ``(outcome, False)``."""
        if not record_module.is_mission_core_kind(entry):
            return None, False
        refusal = self._mission_admission(workflows, entry, boundary, mutate)
        return refusal, refusal is None

    @staticmethod
    def _mission_receipt(marker, problem, detail, now):
        import secrets
        return {
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mgate-" + secrets.token_hex(8),
            "recorded_at": now,
            "digest": hashlib.sha256(problem.encode("utf-8")).hexdigest(),
            "bounded_summary": ("%s: %s — %s" % (marker, problem, detail))[
                :record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }

    def _mission_hold(self, workflows, entry, admission):
        """At most ONE non-terminal hold receipt per cause: the phase is
        preserved and nothing is invalidated; a repeated pass under the
        same cause records nothing more."""
        cause = "%s: %s" % (MISSION_HOLD_RECEIPT_MARKER, admission.problem)
        for receipt in reversed(entry["receipts"]):
            summary = receipt.get("bounded_summary") or ""
            if summary.startswith(MISSION_HOLD_RECEIPT_MARKER + ": "):
                if summary.startswith(cause):
                    return  # the latest hold receipt already names this cause
                break
        entry["receipts"] = list(entry["receipts"]) + [self._mission_receipt(
            MISSION_HOLD_RECEIPT_MARKER, admission.problem, admission.detail,
            self._clock())]
        self.store.save(workflows)

    def _mission_block(self, workflows, entry, admission):
        """Terminal invalidation: a block receipt and the locked
        PHASE_BLOCKED transition (a terminal phase keeps its phase and
        gains the receipt only), saved durably. An UNSETTLED engagement
        start (a crash or lost response between its admission and its
        settlement) is additionally recorded as crash uncertainty on the
        record: never claimable again, never blindly restarted."""
        entry["receipts"] = list(entry["receipts"]) + [self._mission_receipt(
            MISSION_BLOCK_RECEIPT_MARKER, admission.problem, admission.detail,
            self._clock())]
        # An UNSETTLED engagement start is recorded as uncertainty in the
        # Mission store itself (the canonical start with no settlement);
        # the record is BLOCKED with this receipt but NOT marked
        # crash-ambiguous: the owner's later pass must still reach the
        # record (through the terminal release action) to settle the
        # start uncertain and record its stop — the ambiguity marker
        # would refuse every action before that recovery could run.
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            record_module.apply_transition(entry, record_module.PHASE_BLOCKED)
        self.store.save(workflows)

    # -- the single entry point ----------------------------------------

    def perform(self, workflow_id, action, revision, capability=None):
        """Run ONE fixed lifecycle action; fail closed on everything.

        Sensitive values (paths, URLs, baselines, handoff bytes) are
        resolved from the record INSIDE the action handlers — the
        caller has no way to supply one. ``capability`` is the
        Runtime-issued one-shot internal token (I3): it must be bound
        to exactly this (workflow, action, revision), unconsumed and
        unexpired. Codex never sees or supplies one: the only
        production caller is the Runtime, in-process.

        ORDER, AND WHAT EACH STEP WRITES (this is the contract; read
        it as two separate properties, because they are no longer the
        same property):

        1. ``PROBLEM_UNKNOWN_ACTION`` is refused FIRST, outside the
           lock, and consumes NOTHING. An action outside the fixed set
           cannot be the exact binding of any capability, so a token
           presented with one would be refused
           ``capability_binding_mismatch`` regardless; refusing first
           avoids spending authority on a caller-shape error.
        2. The capability is then validated and CONSUMED DURABLY,
           before the workflow store is read and before the gate runs.
           A NON-AUTHENTIC presentation — missing, malformed, unknown
           or forged, already consumed, expired, or bound to a
           different (workflow, action, revision) — is refused writing
           NOTHING, and destroys no other entry in the capability
           store.
        3. Only then are the workflow record loaded and the gate run.

        Consequently:

        * A gate refusal (policy digest drift, wrong phase, stale or
          superseded revision, ambiguity, invalid record, wrong
          control, approval refusals) writes nothing to the WORKFLOW
          RECORD, and neither does a store-unreadable refusal. That
          property is unchanged.
        * An AUTHENTIC presentation is SPENT REGARDLESS OF THE
          OUTCOME — including when the gate refuses afterwards, and
          including when the workflow store cannot be read. It is NOT
          the case that a refusal writes nothing anywhere; it is not
          the case that the capability store is untouched by a
          refusal. Re-presenting that same nonce is refused with its
          own code, durably, across Runtime restarts.

        This is deliberate. Leaving an authentic capability live
        through a gate refusal made every Runtime poll of a
        persistently refusing workflow accrue one more live entry
        against the capability store's hard bound, so one stuck
        workflow degraded the shared authority budget every other
        workflow mints from. Consumption is durable BEFORE any effect:
        a crash between consumption and effect costs one capability —
        the Runtime mints a fresh one after re-validating — never a
        replay.
        """
        if action not in BROKER_ACTIONS:
            return _refused(
                PROBLEM_UNKNOWN_ACTION,
                "unknown broker action %r; the action set is fixed"
                % (action,),
            )
        with store_module.exclusive_store_lock(self.store.directory):
            # R-01 CONSUMPTION ORDER. The capability is validated and
            # consumed FIRST, before the workflow store is even read
            # and before the gate runs. `validate_and_consume` itself
            # refuses every NON-authentic presentation — missing,
            # malformed, unknown/forged, already consumed, expired, or
            # bound to a different (workflow, action, revision) —
            # writing NOTHING and touching no other entry, so nothing
            # a caller can forge destroys authority. What this
            # ordering changes, and the ONLY thing it changes, is that
            # an AUTHENTIC, exactly-bound, unconsumed, unexpired
            # presentation is SPENT even when the gate below refuses.
            # That is the point: a gate refusal used to leave the
            # presented capability live, so a persistently refusing
            # workflow accrued one live entry per Runtime poll against
            # the store's hard bound and starved every other
            # workflow's authority. Running before `self.store.load()`
            # closes the same leak on the store-unreadable path:
            # capability authenticity does not depend on the workflow
            # store, so an unreadable store cannot leak a live
            # capability either.
            consumed, problem, detail = (
                self.capability_authority.validate_and_consume(
                    capability, workflow_id, action, revision,
                    self._clock(),
                )
            )
            if not consumed:
                return _refused(problem, detail)
            try:
                workflows = self.store.load()
            except store_module.StoreError as exc:
                return _refused(PROBLEM_STORE_UNREADABLE, str(exc))
            entry, refusal = self._gate(
                workflows, workflow_id, action, revision
            )
            if refusal is not None:
                return refusal
            # THE CONTAINMENT BOUNDARY (I5 revision 1, round-10
            # F-1 structural closure): every action handler below
            # can GROW the record (turn identities, receipts, the
            # verified result) and then save; at a hard record
            # bound the validator refuses the save and store.save
            # raises. That is a reason to stop ONE workflow
            # durably — never to kill the Runtime process (an
            # uncaught raise here took down every workflow in the
            # store). One boundary contains every present AND
            # future record-growing save beneath `perform`; a
            # derivation test proves no such save exists outside
            # it. This restores perform's stated contract:
            # refusals (and stops) never raise.
            try:
                # Task 8 S-IV: the Mission gate's ACTION ADMISSION for a
                # Mission-origin record — after the workflow lock and the
                # capability consumption (R-01 unchanged), before any
                # handler, inside the containment boundary because a
                # refusal may record a receipt.
                if record_module.is_mission_core_kind(entry):
                    # Pending stop observations of earlier starts are
                    # retried first (owned stop + canonical observation,
                    # never an invocation), so the record's receipts and
                    # the canonical state agree before the admission; then
                    # the retention is released if its Mission was
                    # cancelled (confirmed) or superseded (S-V, R2-2).
                    self._recover_pending_stops(workflows, entry)
                    self._update_retention(workflows, entry)
                    # R15-3: the owned final cleanup has its OWN admission
                    # (a cancelled or terminal Mission still admits it; an
                    # outstanding canonical stop makes it wait), and no
                    # other action ever passes through it.
                    refusal = self._mission_admission(
                        workflows, entry,
                        mission_gate_module.BOUNDARY_CLEANUP
                        if action == ACTION_RELEASE
                        else mission_gate_module.BOUNDARY_ACTION_ADMISSION,
                    )
                    if refusal is not None:
                        return refusal
                if action == ACTION_MATERIALIZE:
                    return self._materialize(workflows, entry)
                if action == ACTION_PREPARE:
                    return self._prepare(workflows, entry)
                if action == ACTION_VALIDATE_HANDOFF:
                    return self._validate_handoff(workflows, entry)
                if action == ACTION_DISPATCH:
                    if entry["phase"] == record_module.PHASE_DISPATCHED:
                        return self._resume_refused_dispatch(workflows, entry)
                    return self._dispatch(workflows, entry,
                                          follow_up=False)
                if action == ACTION_VERIFY:
                    return self._verify(workflows, entry)
                if action == ACTION_FOLLOW_UP:
                    # Task 8 S-VII correction 2 (R2): a follow-up ordinal
                    # whose claim was durably refused is RESUMED — never a
                    # new marker, reservation or charge; and no follow-up is
                    # dispatched while the initial dispatch is unresolved.
                    resumption = refused_claim_resumption(entry)
                    if resumption is not None and resumption[1] > 1:
                        return self._resume_refused_dispatch(workflows, entry)
                    if resumption is not None:
                        return _refused(
                            PROBLEM_WRONG_PHASE,
                            "the initial dispatch of workflow %s holds a durably"
                            " refused claim; it is resumed before any follow-up"
                            % entry["workflow_id"])
                    return self._dispatch(workflows, entry,
                                          follow_up=True)
                if action == ACTION_COMPLETE:
                    return self._complete(workflows, entry)
                if action == ACTION_RECONCILE:
                    return self._reconcile(workflows, entry)
                return self._release(workflows, entry)
            except (store_module.StoreError,
                    record_module.RecordError) as exc:
                return self._contain_unsavable_record(
                    workflow_id, exc
                )

    def _contain_unsavable_record(self, workflow_id, exc):
        """A grown record the store refused: stop ONE workflow
        durably; never raise (the containment boundary's promise).

        The in-memory document is poisoned (it holds the over-bound
        record), so the durable stop starts from a FRESH load — the
        last durably saved state, which validated when it was
        written. The stop is a phase-only transition to BLOCKED: it
        grows nothing, so it cannot re-hit the very failure being
        contained. No reason receipt is written here — a receipt is
        record growth at a growth-failure boundary, and a generic
        cross-action stop-reason mechanism is the deferred I3b
        scope; the truthful code and detail travel in the outcome.
        """
        detail = str(exc)[:MAX_OUTCOME_DETAIL_CHARS]
        if HARD_BOUND_MESSAGE_MARKER in str(exc):
            problem = PROBLEM_RECORD_CAPACITY_EXHAUSTED
        else:
            problem = PROBLEM_RECORD_UNSAVABLE
        try:
            workflows = self.store.load()
            entry = workflows["workflows"].get(workflow_id)
            if entry is None:
                return _refused(PROBLEM_UNKNOWN_WORKFLOW, detail)
            if entry["phase"] not in record_module.TERMINAL_PHASES:
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )
                self.store.save(workflows)
        except (store_module.StoreError,
                record_module.RecordError) as inner:
            # Even the phase-only stop could not be persisted: a
            # store-level failure. Refuse truthfully — the record
            # is unsavable — with both causes in the detail.
            return _refused(
                PROBLEM_RECORD_UNSAVABLE,
                ("durable stop could not be persisted (%s) after"
                 " the record refused to grow (%s)"
                 % (inner, exc))[:MAX_OUTCOME_DETAIL_CHARS],
            )
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_RECORD_GROWTH_BLOCKED,
            problem=problem, detail=detail,
        )

    # -- action handlers (called with the gate already passed) ---------

    def _materialize(self, workflows, entry):
        lease = entry.get("workspace_lease")
        if record_module.is_mission_core_kind(entry) and isinstance(
            lease, dict
        ) and lease.get("released_at") is None:
            # Task 8 S-IV: a REVERSIBLE hold recorded after the clone
            # (before trust establishment or before the lease was
            # accepted as WORKSPACE_READY) kept the lease on the record;
            # a later pass resumes from the clone it already made —
            # re-verified read-only against the approved baseline, never
            # re-cloned into the existing directory — instead of turning
            # the hold into a terminal ``workspace_exists`` stop.
            ok, problem, detail = self.worker.verify_workspace(entry)
        else:
            ok, problem, detail = self.worker.materialize_workspace(
                entry, self._clock()
            )
        if not ok:
            if problem == workspace_module.PROBLEM_WORKSPACE_EXISTS:
                # Crash uncertainty is durable: the workflow is
                # marked ambiguous and BLOCKED so it can never be
                # silently retried into a directory of unknown
                # provenance.
                entry["ambiguity"] = {
                    "state": record_module.AMBIGUITY_CRASH_UNCERTAIN,
                    "detail": detail,
                }
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )
                self.store.save(workflows)
            return _refused(problem, detail)
        # ORDERING (I1 P8): trust for the workspace DI just
        # materialized is established HERE — after materialization
        # succeeded and BEFORE the workflow can advance one phase.
        # Dispatch requires VALIDATED, reachable only through
        # WORKSPACE_READY, so within the phase machine a refusal that
        # stops the transition and goes to the terminal BLOCKED phase
        # puts a Herdr start out of reach — structural, not merely
        # conventional ordering. Outside the phase machine (a direct
        # call to the spawn bridge) this ordering does not apply.
        #
        # Task 8 S-IV (Supervisor refinement D): trust establishment
        # is an EFFECT on the just-cloned workspace and follows the
        # blocking clone; a Mission-origin record is re-admitted in the
        # gate's short critical section (no marking) immediately before
        # it. A refusal leaves the clone recorded as a lease (cleanup can
        # release it) and holds or blocks durably — no trust is
        # established for a Mission whose authority moved during the
        # clone.
        if record_module.is_mission_core_kind(entry):
            refusal = self._mission_admission(
                workflows, entry, mission_gate_module.BOUNDARY_MATERIALIZE,
                lambda: None)
            if refusal is not None:
                return refusal
        ok, problem, detail = self.worker.establish_workspace_trust(
            entry
        )
        if not ok:
            # Durable and actionable: a reason receipt naming the
            # problem code, then a terminal BLOCKED phase. Not a
            # silent retry, not a fallback to an interactive
            # prompt, not a step toward dispatch.
            entry["receipts"] = list(entry["receipts"]) + [
                workspace_trust_module.trust_block_receipt(
                    problem, now=self._clock()
                )
            ]
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
            return _refused(problem, detail)
        # Task 8 S-IV: the clone was a long blocking step; the lease it
        # produced is admitted (recorded, WORKSPACE_READY) only under the
        # gate's short critical section. A refusal still records the lease
        # (cleanup can release it) and holds or blocks durably.
        def accept():
            record_module.apply_transition(
                entry, record_module.PHASE_WORKSPACE_READY
            )

        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_MATERIALIZE, accept,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept()
            self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _prepare(self, workflows, entry):
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return _refused(problem, detail)
        receipts, refused_files = prepare_module.discover_instructions(
            entry, now=self._clock()
        )
        entry["receipts"] = list(entry["receipts"]) + receipts
        record_module.apply_transition(
            entry, record_module.PHASE_PREPARED
        )
        self.store.save(workflows)
        detail = None
        if refused_files:
            detail = "; ".join(refused_files)
        return BrokerOutcome(
            True, phase=entry["phase"], detail=detail
        )

    def _validate_handoff(self, workflows, entry):
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return _refused(problem, detail)
        # I4: resolve the ACTUAL bounded instruction content from the
        # just-verified leased workspace (the Broker resolves it from
        # the protected record's lease — the caller supplies
        # nothing), and cross-check it against the preparation
        # receipts: a file whose bytes drifted since preparation (or
        # vanished/became unreadable) refuses fail-closed — the turn
        # must judge exactly what preparation accounted for.
        target_context = prepare_module.instruction_context(entry)
        current = {
            item["name"]: item for item in target_context
        }
        receipt_digests = {}
        for receipt in entry["receipts"]:
            name = prepare_module.receipt_instruction_name(receipt)
            if name is None:
                continue
            receipt_digests[name] = receipt["digest"]
            item = current.get(name)
            if (
                item is None
                or item["status"] != prepare_module.INSTRUCTION_READ
                or item["digest"] != receipt["digest"]
            ):
                return _refused(
                    PROBLEM_INSTRUCTIONS_DRIFTED,
                    "instruction file %s no longer matches its"
                    " preparation receipt (changed, vanished, or"
                    " unreadable since preparation); the workflow is"
                    " not advanced" % name,
                )
        # F-3: a file that is READABLE now but had NO receipt was
        # ADDED after preparation — the turn must judge exactly what
        # preparation accounted for, so an unaccounted read is
        # refused (an attacker who can time a write into the leased
        # workspace must not choose what the turn sees).
        for item in target_context:
            if item["status"] == prepare_module.INSTRUCTION_READ and (
                item["name"] not in receipt_digests
            ):
                return _refused(
                    PROBLEM_INSTRUCTIONS_DRIFTED,
                    "instruction file %s is present now but was not"
                    " accounted for at preparation (added since); the"
                    " workflow is not advanced" % item["name"],
                )
        result = self._role_turn(
            "handoff_validation", entry, self._clock(),
            target_context=target_context,
        )
        if result.status != ROLE_TURN_COMPLETED or (
            result.outcome is None
        ):
            return _refused(
                PROBLEM_TURN_NOT_COMPLETED,
                "handoff-validation turn did not complete with an"
                " outcome (status %s, reason %s); the workflow was"
                " not advanced" % (result.status, result.reason),
            )
        if result.turn is not None:
            entry["codex_turns"] = list(entry["codex_turns"]) + [
                result.turn
            ]

        # Task 8 S-IV: the model turn was a long blocking step; its
        # proposed transition is accepted only under the gate's short
        # critical section (a v2 record marks directly).
        def accept():
            if result.outcome == OUTCOME_REQUEST_DISPATCH:
                record_module.apply_transition(
                    entry, record_module.PHASE_VALIDATED
                )
            elif result.outcome == OUTCOME_NEEDS_REAUTHORIZATION:
                record_module.apply_transition(
                    entry, record_module.PHASE_NEEDS_REAUTHORIZATION
                )
            else:
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )

        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_HANDOFF_TURN, accept,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept()
            self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=result.outcome
        )

    def _dispatch(self, workflows, entry, follow_up):
        """Dispatch the EXACT stored handoff to the target Herdr.

        Ordering, load-bearing: lease re-verification (read-only) ->
        follow-up bound check -> the durable dispatch marker (phase
        transition on first dispatch, plus the exact-count evidence
        receipt) is SAVED BEFORE the external spawn, so a crash
        between marker and spawn can never lead to a double dispatch
        — the phase gate refuses a second `dispatch` and the receipt
        count is exact. A failed spawn transitions the workflow to
        BLOCKED durably.
        """
        # The follow-up bound is a pure record read and refuses
        # BEFORE any transport verification (truly zero I/O).
        prior_dispatches = dispatch_module.dispatch_count(entry)
        if follow_up:
            follow_ups_used = prior_dispatches - 1
            if follow_ups_used >= (
                dispatch_module.MAX_FOLLOW_UP_DISPATCHES
            ):
                # R-2: the authorization-scope bound is exceeded. This
                # is NEVER a stranded dead end — the workflow
                # transitions DURABLY to NEEDS_REAUTHORIZATION
                # (visible in /status and the result path), preserving
                # evidence, lease, and the record. A human then issues
                # a new Mission Authorization to continue.
                record_module.apply_transition(
                    entry, record_module.PHASE_NEEDS_REAUTHORIZATION
                )
                self.store.save(workflows)
                return BrokerOutcome(
                    True,
                    phase=entry["phase"],
                    outcome=OUTCOME_NEEDS_REAUTHORIZATION,
                    problem=PROBLEM_FOLLOW_UP_BOUND,
                    detail="%d of %d corrective follow-up dispatches"
                    " used (exact); further correction requires a"
                    " freshly authorized revision — the workflow is"
                    " NEEDS_REAUTHORIZATION" % (
                        follow_ups_used,
                        dispatch_module.MAX_FOLLOW_UP_DISPATCHES,
                    ),
                )
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return _refused(problem, detail)
        # I1 round-01 C-1 + H4: trust is re-verified AT THE POINT OF
        # USE, against the configuration the Herdr about to be
        # started will ACTUALLY read. Establishment happened phases
        # ago; between then and now a concurrent CLI writer can drop
        # DI's entry (the disclosed lost-update residual), and under
        # an injected `--config` the file DI wrote is not the file
        # the child reads at all. In either case the child would stop
        # at the trust dialog, which an unattended run is not able to
        # answer — a FAIL-OPEN in the guarantee this increment exists
        # to provide, and success reported for an effect that no
        # component consumed. Both are refused here, durably, before
        # this dispatch spawns, within the window this check covers.
        # Outside
        # this check, and disclosed: a clobber occurring between it
        # and the spawn is not covered.
        ok, problem, detail = self.worker.workspace_trust_consumable(
            entry
        )
        if not ok:
            entry["receipts"] = list(entry["receipts"]) + [
                workspace_trust_module.trust_block_receipt(
                    problem, now=self._clock()
                )
            ]
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
            return _refused(problem, detail)
        # Ruling R-2: the INITIAL dispatch stamps the dispatch-time
        # protected-surface baseline receipt. The digest is computed
        # BEFORE any durable write: a REFUSED digest (over-bound,
        # unreadable, missing root) refuses the whole dispatch fail-
        # closed — stamping nothing, transitioning nothing — because
        # a dispatch without a truthful baseline would create a
        # workflow that can never verify, and an absent or fabricated
        # baseline is forbidden outright.
        # RULING R-6 (Layer 2 of plan §1.1): the INITIAL-dispatch
        # placeholder gate. Scoped to `not follow_up` deliberately —
        # a FOLLOW-UP dispatch continues a mission whose placeholder
        # question was already settled at initial dispatch, so gating
        # it again would strand corrective work for no added
        # guarantee. Placed here with the other initial-dispatch
        # preconditions, and refusing through `_refused` so the
        # refusal writes NOTHING: no receipt, no transition, no save.
        # It is a pure record read and therefore refuses before the
        # protected-surface digest touches the filesystem.
        if not follow_up:
            gate_problem, gate_detail = _placeholder_dispatch_refusal(
                entry
            )
            if gate_problem is not None:
                return _refused(gate_problem, gate_detail)
        surface = None
        if not follow_up:
            surface = evidence_module.protected_surface_digest(
                self.control_realpath
            )
            if surface["status"] != evidence_module.BINDING_EXACT:
                return _refused(
                    PROBLEM_SURFACE_UNAVAILABLE,
                    "the protected control-surface digest REFUSED at"
                    " dispatch time (%s: %s); dispatch fails closed"
                    " rather than stamping a fabricated or absent"
                    " baseline" % (
                        surface["status"], surface["detail"],
                    ),
                )
        # The spawn request: exactly four fields. Target/task/alias
        # are resolved from the protected record; preset is the fixed
        # DI-owned Runtime execution posture. The INITIAL dispatch is
        # the stored handoff text BYTE-EXACT (Supervisor-first); a
        # FOLLOW-UP (D6) is a corrective brief built ONLY from
        # authority fields + recorded failed-acceptance evidence,
        # carrying no technical solution.
        if follow_up:
            request = dispatch_module.build_follow_up_spawn_request(
                entry
            )
        else:
            request = dispatch_module.build_spawn_request(entry)
        # Task 8 S-IV (R2-4): a FOLLOW-UP of a Mission-origin record
        # reserves its Mission-side budget canonically BEFORE the marker
        # and the spawn, bound to the exact revision, workflow and
        # ordinal; the reservation reference is saved WITH the dispatch
        # receipt below. An existing reservation for this ordinal (a
        # crash after it) is recovered, never spent twice.
        if follow_up and record_module.is_mission_core_kind(entry):
            if self.mission_gate is None:
                return self._mission_admission(
                    workflows, entry, mission_gate_module.BOUNDARY_SPAWN)
            reference, admission = self.mission_gate.reserve_follow_up(
                entry, prior_dispatches + 1)
            if admission is not None:
                return self._mission_refusal_outcome(
                    workflows, entry, admission)
            entry[record_module.MISSION_ENGAGEMENT_KEY] = dict(reference)

        def accept():
            if not follow_up:
                record_module.apply_transition(
                    entry, record_module.PHASE_DISPATCHED
                )
            entry["receipts"] = list(entry["receipts"]) + [
                dispatch_module.dispatch_receipt(
                    entry, now=self._clock(),
                    sequence_number=prior_dispatches + 1,
                )
            ]
            if surface is not None:
                entry["receipts"] = list(entry["receipts"]) + [
                    dispatch_module.surface_receipt(
                        entry, surface["digest"], now=self._clock()
                    )
                ]

        # Task 8 S-IV: the durable dispatch MARKER is written under the
        # gate's short critical section (a cancel or EDIT committed before
        # it leaves no marker). The marker is intent/ambiguity, NOT start
        # permission: the START admission below re-checks immediately
        # before the spawn seam. Durable BEFORE the external spawn.
        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_SPAWN, accept,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept()
            self.store.save(workflows)
        return self._spawn_and_bind(workflows, entry, request,
                                    prior_dispatches + 1)

    def _resume_refused_dispatch(self, workflows, entry):
        """Task 8 S-VII (Lead gate F-S7-1; correction 2, R1 and R2): RESUME
        the ONE start point of the current dispatch whose claim was durably
        refused (``refused_claim_resumption``) — the same dispatch ordinal,
        engagement, reservation and objective, at the current revision and
        authorization; no EDIT, no new approval, no second marker,
        reservation or charge. The Runtime reaches this only after the gate
        admitted the spawn boundary again (a lifted hold, fresh readiness, a
        settled sequence, a source answering), and the guard's canonical
        claim then re-checks everything under the Mission lock: a terminal
        cause blocks, a reversible one refuses the claim again (nothing
        invoked), and an admitted claim proceeds exactly as the original
        would have — the core admits a start at most once per engagement
        point, so this can never invoke twice. The lease and the
        point-of-use trust are re-verified exactly as ``_dispatch`` does.

        - RUNTIME point: the guarded spawn of the same ordinal (F-S7-1; R2
          for a follow-up, whose corrective objective is proven unchanged:
          no correction evidence was recorded after its marker).
        - TASK point (R1): ``_resume_task_handover`` — the objective is
          handed to the runtime this dispatch already started, settled and
          owns; the runtime start is never replayed."""
        resumption = refused_claim_resumption(entry)
        if resumption is None:
            return _refused(
                PROBLEM_WRONG_PHASE,
                "workflow %s holds no durably refused, unstarted start point;"
                " only such a point is resumed" % entry["workflow_id"],
            )
        point, sequence = resumption
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return _refused(problem, detail)
        ok, problem, detail = self.worker.workspace_trust_consumable(entry)
        if not ok:
            entry["receipts"] = list(entry["receipts"]) + [
                workspace_trust_module.trust_block_receipt(
                    problem, now=self._clock()
                )
            ]
            record_module.apply_transition(entry, record_module.PHASE_BLOCKED)
            self.store.save(workflows)
            return _refused(problem, detail)
        if sequence == 1:
            request = dispatch_module.build_spawn_request(entry)
        else:
            drift = follow_up_objective_drift(entry, sequence)
            if drift is not None:
                return self._reconcile_block(
                    workflows, entry, PROBLEM_RESUME_OBJECTIVE_DRIFT, drift)
            request = dispatch_module.build_follow_up_spawn_request(entry)
        if point == dispatch_module.START_POINT_RUNTIME:
            return self._spawn_and_bind(workflows, entry, request, sequence)
        return self._resume_task_handover(workflows, entry, request, sequence)

    def _resume_task_handover(self, workflows, entry, request, sequence):
        """Task 8 S-VII correction 2 (R1): hand dispatch ``sequence``'s EXACT
        objective to the runtime that dispatch ALREADY started. The
        canonical facts are re-read first — exactly one runtime start of
        the engagement, settled ``completed``, with no stop requirement
        (a sticky stop is never revived) and an execution identity, and no
        task start of it (the handover is never replayed) — then ownership
        of that runtime is proven FRESH against a live listing
        (``prove_started_runtime``: the settled workspace id and exact agent
        set, under the lease). A contradiction or a failed proof blocks
        durably (``broker_resume_runtime_unproven``: nothing is handed over,
        the runtime start is never replayed); an unanswering source or an
        unreadable listing refuses and writes nothing. Proven, the guard
        (seeded with the settled identity) opens the canonical task start
        through the SAME guarded control plane a spawn uses; the engine's
        start is not invoked."""
        from target_runtime import workspace_ownership as ws_module
        gate = self.mission_gate
        starts = gate.engagement_starts(entry)
        if starts is None:
            return _refused(
                mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE,
                "the Mission source cannot answer for the engagement starts;"
                " nothing is handed over")
        engagement_id = entry[record_module.MISSION_ENGAGEMENT_KEY]["engagement_id"]
        own = [start for start in starts if start["engagement_id"] == engagement_id]
        runtime = [start for start in own if start["point"] == "runtime"]
        task = [start for start in own if start["point"] == "task"]
        contradiction = None
        if task:
            contradiction = ("a task start of engagement %s exists canonically (%s);"
                             " the handover is never replayed"
                             % (engagement_id, task[0]["start_id"]))
        elif len(runtime) != 1:
            contradiction = ("engagement %s holds %d runtime starts canonically;"
                             " exactly one settled start is required"
                             % (engagement_id, len(runtime)))
        else:
            settlement = runtime[0]["settlement"] or {}
            identity = settlement.get("identity")
            if settlement.get("outcome") != mission_gate_module.START_OUTCOME_COMPLETED:
                contradiction = ("the runtime start %s is not settled completed"
                                 % runtime[0]["start_id"])
            elif mission_gate_module.start_stop_required(runtime[0]):
                contradiction = ("the runtime start %s owes a stop; it is never"
                                 " revived" % runtime[0]["start_id"])
            elif not isinstance(identity, dict) or not identity.get("workspace_id"):
                contradiction = ("the runtime start %s carries no execution"
                                 " identity" % runtime[0]["start_id"])
        if contradiction is not None:
            return self._reconcile_block(workflows, entry,
                                         PROBLEM_RESUME_RUNTIME_UNPROVEN, contradiction)
        if not self.worker.observes_live_workspaces:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RESUME_RUNTIME_UNPROVEN,
                "no live-workspace observation capability is wired; ownership of"
                " the settled runtime cannot be proven, so nothing is handed over")
        try:
            live = bounded_engine_call(self.worker.live_workspaces,
                                       OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return _refused(
                PROBLEM_RESUME_RUNTIME_UNPROVEN,
                "the live workspace listing is unreadable (%s); nothing is handed"
                " over" % exc.__class__.__name__)
        malformed = live_listing_problem(live)
        if malformed is not None:
            return _refused(
                PROBLEM_RESUME_RUNTIME_UNPROVEN,
                "the live workspace listing is unavailable or malformed (%s);"
                " nothing is handed over" % malformed)
        verdict, snapshot, why, detail = ws_module.prove_started_runtime(
            identity, live, ownership_module.recorded_lease_realpath(entry))
        if snapshot is None:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RESUME_RUNTIME_UNPROVEN,
                "ownership of the settled runtime %s is %s (%s: %s); nothing is"
                " handed over and the runtime start is never replayed"
                % (identity["workspace_id"], verdict, why, detail))
        guard = _MissionStartGuard(self, workflows, entry, sequence)
        guard.identity = {"workspace_id": identity["workspace_id"],
                          "agent_names": sorted(identity.get("agent_names") or []),
                          "task_id": None}
        try:
            handover = dispatch_module.production_task_handover(request, guard)
        except Exception as exc:                          # noqa: BLE001
            return self._start_failure_outcome(workflows, entry, exc)
        if entry.get("target_engine") is None:
            entry["target_engine"] = dispatch_module.target_identity_from_task(
                handover, entry, self._clock())
            self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _start_failure_outcome(self, workflows, entry, exc):
        """The durable consequence of a guarded start that did not proceed —
        shared by a spawn and a task-only handover: a refused claim
        (``StartRefused``: nothing invoked at that point), a start admitted
        and then stopped or left unsettled (``StartStopped``), or a failed
        bridge (any other exception: BLOCKED, never re-dispatched)."""
        if isinstance(exc, dispatch_module.StartRefused):
            admission = exc.admission
            if exc.point == dispatch_module.START_POINT_TASK:
                admission = admission._replace(detail=(
                    "%s; the child runtime at %s was created and is left"
                    " idle and un-tasked" % (admission.detail,
                                            exc.idle_runtime)))
            return self._mission_refusal_outcome(workflows, entry, admission)
        if isinstance(exc, dispatch_module.StartStopped):
            closure = exc.closure
            handed = exc.point == dispatch_module.START_POINT_TASK
            if closure.unsettled:
                admission = closure.admission._replace(
                    problem=mission_gate_module.PROBLEM_START_UNSETTLED,
                    classification=mission_gate_module.CLASS_TERMINAL,
                    detail=closure.detail)
            else:
                if closure.stopped:
                    stop = "CONFIRMED (absence observed and recorded canonically)"
                elif closure.absent_observed:
                    stop = ("absence OBSERVED; canonical confirmation PENDING"
                            " (the observation could not be recorded; it is"
                            " retried on the next pass without any invocation)")
                else:
                    stop = "PENDING"
                admission = closure.admission._replace(detail=(
                    "%s; the start at %s was admitted before it (canonical"
                    " engagement start), %s; owned stop %s (%s)" % (
                        closure.admission.detail, exc.point,
                        ("the objective was handed over, then a stop was"
                         " required" if handed
                         else "the objective was never handed over"),
                        stop, closure.detail)))
            return self._mission_refusal_outcome(workflows, entry, admission)
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
        return _refused(
            PROBLEM_SPAWN_FAILED,
            "the child-spawn bridge failed (%s); the workflow is"
            " BLOCKED and was NOT re-dispatched" % (exc,),
        )

    def _maintain_retirement(self, workflows, entry):
        """``MAINTAIN_RETIREMENT`` — Task 8 startup correction: the Runtime's
        PRE-MINT, READ-ONLY re-assessment of a refused follow-up whose
        resumption would retire its earlier runtime: exactly the reads and
        proofs ``_retire_predecessor`` begins with (``assess=True``), NOTHING
        preserved, closed or discarded. ``BrokerOutcome(True)`` with no
        problem when nothing refuses (the Runtime may mint and resume);
        otherwise the refusal is recorded like any gate refusal — a hold at
        most once per cause (a waiting retirement — a close whose outcome is
        uncertain, a prior task still running, an unreadable listing — is
        re-assessed each pass without minting, claiming or recording anything
        new), a contradiction blocked — and returned with its problem."""
        resumption = refused_claim_resumption(entry)
        if (resumption is None
                or resumption[0] != dispatch_module.START_POINT_RUNTIME
                or resumption[1] < 2):
            return BrokerOutcome(True)
        refusal = self._retire_predecessor(workflows, entry, resumption[1], None,
                                           resumption[0], assess=True)
        if refusal is None:
            return BrokerOutcome(True)
        return self._mission_refusal_outcome(workflows, entry, refusal)

    def _retire_predecessor(self, workflows, entry, sequence, claim, point, assess=False):
        """Task 8 startup correction — RETIRE this workflow's own earlier
        runtime before corrective follow-up ``sequence``'s runtime start.
        Returns None (nothing of it remains live or persisted: the claim
        proceeds to its canonical open) or the refusal ``Admission`` — HOLD
        for a recoverable condition (the refused claim is resumed on a later
        pass), TERMINAL for a contradiction (the record is blocked).

        WHY. The native start, ``herdr.lifecycle.start_herd``, REFUSES while
        the previous supervisor of the same repository is live (production
        passes no ``force``) and, once past that refusal, closes whatever
        workspace the persisted ``.herd/state/runtime.json`` names — from
        persisted state, unproven, unconditionally, its result not inspected
        — before discarding it. A start settled ``completed`` with
        ``stop=none`` owes no stop, so nothing else makes it absent.

        OWNER AND EFFECT. This workflow's own start guard (the dispatch
        action starting the follow-up), BEFORE the canonical open: close
        exactly the live runtime identities its canonical settled starts own
        (``_canonical_binding``) — each proven (``prove_started_runtime``:
        its id, its exact agent set, the lease) and closed at most once — and
        discard the persisted runtime state naming one of them, preserved
        byte-exact first, so the native start has no stale id to close. Never
        the lease or the directory: reclamation stays with the release.

        ORDER. The reads — the canonical binding (runtime AND task
        identities), the durable close claims, one complete bounded listing,
        the persisted state, the lease's observed task, the lease-scoped child
        evidence — and the proofs over them; then preservation. Then EACH
        CLOSE IS ITS OWN EFFECT BOUNDARY: a fresh complete listing and fresh
        child evidence, the binding re-derived and required EQUAL, the child
        rule again, the lease's task observed AFRESH — its IDENTITY (THE prior
        canonical hand-over) and its TERMINAL STATUS, both, since neither the
        binding nor the child records describe the lease's current task — the
        proof re-matched against that fresh listing (``still_matches``: id,
        exact agents, lease, bound task), then a FRESH spawn-boundary
        admission taken under the gate's critical section with the DURABLE
        close claim written inside it (``admit_and_mark``: a hold, cancel,
        EDIT, lapse or source loss committed before it closes nothing; the
        Mission lock is released before the engine call), then the bounded
        close. Then a fresh complete listing that must show every identity
        ABSENT, and the discard — its own boundary, admitted likewise — of the
        exact preserved bytes only (no task re-read there: every runtime is by
        then observed absent). The claim's canonical open follows and
        re-checks everything atomically. The spawn boundary, not
        ``admit_cleanup``: this is not disposal but the replacement step of
        the start that boundary admits — the step the native start performs
        itself whenever the previous supervisor is not live.

        AT MOST ONE CLAIMED CLOSE PER PROVEN IDENTITY, across passes,
        restarts and follow-ups: every close claim of this workflow is read
        back losslessly (``_retirement_close_claims``: the id JSON-encoded,
        its round trip verified BEFORE the claim is written, so any admitted
        identity — spaces included — is recorded exactly or not at all). The
        claim is written BEFORE the engine call, so by itself it proves only
        that a close MAY have been issued; ``close-returned`` records that the
        call returned. An identity with a claim that no later canonical start
        re-established (``_retired_identities``) is never closed again —
        observed absent, the retirement proceeds; still listed, or its close
        raised, or absence unobservable, the outcome is UNCERTAIN
        (``PROBLEM_PREDECESSOR_UNCERTAIN``, a HOLD reported as such, naming
        whether the call is known to have returned) and nothing is retried.

        NEVER. A foreign or unproven workspace (an identity neither owned nor
        absent, one replaced or reused since its proof, or persisted state
        naming a workspace or supervisor this workflow does not own, or whose
        identity is not projectable at all, refuses TERMINALLY); a lease task
        that is not THE prior canonical hand-over (TERMINAL) or not finished
        (HOLD) — at the first read AND at every close; contradictory child
        evidence, never overridden (TERMINAL); unproven preservation (nothing
        closed or discarded); force; a replayed start or task."""
        from target_runtime import evidence_preservation as preserve_module
        from target_runtime import workspace_ownership as ws_module

        def refused(terminal, detail, problem=None):
            return mission_gate_module.Admission(
                False,
                problem or (PROBLEM_PREDECESSOR_UNPROVEN if terminal
                            else PROBLEM_PREDECESSOR_PENDING),
                detail,
                (mission_gate_module.CLASS_TERMINAL if terminal
                 else mission_gate_module.CLASS_HOLD),
                mission_gate_module.BOUNDARY_SPAWN)

        def uncertain(workspace_id, invocation, why):
            """A close of ``workspace_id`` was CLAIMED (durably, BEFORE its
            engine call) and the workspace is not observed absent: reported
            with exactly what is known of the invocation, never re-issued;
            the retirement waits for its observed absence."""
            return refused(False, "a close of workspace %s was claimed durably before its"
                                  " engine call; %s; %s — its outcome is UNCERTAIN, the close"
                                  " is never re-issued, and the retirement waits for its"
                                  " observed absence" % (workspace_id, invocation, why),
                           PROBLEM_PREDECESSOR_UNCERTAIN)

        def recorded_invocation(workspace_id):
            if workspace_id in returned and max(returned[workspace_id]) >= retired[workspace_id]:
                return "its engine call is recorded as returned"
            return ("no return of its engine call is recorded, so whether the call was made"
                    " is unknown")

        if not (self.worker.observes_live_workspaces and self.worker.closes_workspaces):
            return refused(True, "no live-workspace observation and close capability are"
                                 " wired; this workflow's earlier runtime cannot be proven"
                                 " absent, and the native start refuses while it is live")
        identities, _current, tasks, problem, detail = _canonical_binding(self, entry)
        if identities is None:
            if problem == mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE:
                return refused(False, detail, problem)
            return refused(True, "this workflow's earlier runtimes are not provable from its"
                                 " canonical starts (%s: %s)" % (problem, detail))
        if not tasks:
            return refused(True, "no canonical task hand-over of this workflow precedes"
                                 " follow-up %d" % sequence)
        # THE prior hand-over: the latest canonical completed task start.
        _ordinal, prior_task, _prior_ws, _prior_agents = max(tasks)
        lease = ownership_module.recorded_lease_realpath(entry)
        bound = ownership_module.recorded_task_id(entry)
        # Every durable close claim of this workflow, read back losslessly; an
        # undecodable one is never read as "no claim".
        claims, returned, undecodable = _retirement_close_claims(entry)
        if undecodable:
            return refused(True, "%d durable retirement close claim(s) of this workflow do"
                                 " not decode; a claim that cannot be read is never read as"
                                 " no claim, so nothing is closed" % undecodable)
        retired = _retired_identities(self, entry, claims)
        if retired is None:
            return refused(False, "the Mission source cannot answer for the engagement"
                                  " starts", mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE)
        try:
            live = bounded_engine_call(self.worker.live_workspaces, OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return refused(False, "the live workspace listing is unreadable (%s)"
                                  % exc.__class__.__name__)
        malformed = live_listing_problem(live)
        if malformed is not None:
            return refused(False, "the live workspace listing is unavailable or malformed"
                                  " (%s)" % malformed)
        state, unusable = _lease_runtime_state(lease)
        if unusable is not None:
            return refused(unusable[0], "the persisted runtime state %s; nothing is closed,"
                                        " discarded or started" % unusable[1])
        if state is not None:
            owned = identities.get(state["workspace_id"])
            if owned is None:
                return refused(True, "the persisted runtime state names workspace %s, which"
                                     " no canonical start of this workflow owns; the native"
                                     " start would close it unproven, so nothing is started"
                                     % state["workspace_id"])
            if state["supervisor"] not in owned:
                return refused(True, "the persisted runtime state names supervisor %r, not"
                                     " an agent of workspace %s" % (state["supervisor"],
                                                                     state["workspace_id"]))

        def task_problem(where):
            """No takeover of an active or foreign task, read FRESH each time:
            the lease's observed task must be THE prior canonical hand-over
            (identity) AND finished (terminal status) — ``(terminal,
            detail)`` or None."""
            raw = self._observe_raw(lease)
            task = (raw.get("task") if isinstance(raw, dict)
                    and isinstance(raw.get("task"), dict) else {})
            if task.get("state") != "available":
                return (False, "%sthe lease's task is not observable (state %r); a runtime"
                               " whose task may be running is never closed"
                               % (where, task.get("state")))
            if task.get("id") != prior_task:
                return (True, "%sthe lease's task is %r, not this workflow's prior canonical"
                              " hand-over %r; a runtime whose task is not provably that"
                              " hand-over is never closed" % (where, task.get("id"), prior_task))
            if task.get("status") not in _TARGET_TERMINAL_STATUSES:
                return (False, "%sthe prior hand-over %s is %s; a runtime whose task is"
                               " still running is never closed"
                               % (where, prior_task, task.get("status")))
            return None
        problem = task_problem("")
        if problem is not None:
            return refused(*problem)
        children = self._spawn_records_raw(lease=lease)
        child_problem = _retirement_child_problem(self, entry, children, tasks)
        if child_problem is not None:
            return refused(*child_problem)
        snapshots, absent = {}, []
        for workspace_id, agents in sorted(identities.items()):
            verdict, snapshot, why, detail = ws_module.prove_started_runtime(
                {"workspace_id": workspace_id, "agent_names": sorted(agents),
                 "task_id": bound}, live, lease)
            if snapshot is not None:
                if workspace_id in retired:
                    return uncertain(workspace_id, recorded_invocation(workspace_id),
                                     "it is still listed (claim of follow-up %d)"
                                     % retired[workspace_id])
                snapshots[workspace_id] = snapshot
            elif why == ws_module.PROBLEM_WORKSPACE_NOT_FOUND:
                absent.append(workspace_id)
            else:
                return refused(True, "workspace %s is %s (%s: %s); a workspace not provably"
                                     " this workflow's own is never closed"
                                     % (workspace_id, verdict, why, detail))
        if assess:
            return None     # the read-only assessment ends before any effect
        if not snapshots and state is None:
            return None     # nothing of the earlier runtimes is live or persisted
        preserved = "none"
        if state is not None:
            ok, problem, detail, path = preserve_module.preserve_runtime_state(
                self.store.directory, entry["workflow_id"], sequence, state["data"],
                self._clock())
            if not ok:
                # Unproven preservation closes and discards NOTHING. An intact
                # copy of other bytes, or malformed evidence at the archive
                # path, is a contradiction (kept, never overwritten); an
                # unreadable one or a failed write is recoverable.
                return refused(problem in (preserve_module.PROBLEM_RUNTIME_STATE_CONFLICT,
                                           preserve_module.PROBLEM_RUNTIME_STATE_MALFORMED),
                               "the persisted runtime state to retire was not preserved"
                               " (%s: %s); nothing is closed or discarded" % (problem, detail))
            preserved = "%s (sha256 %s) preserved" % (os.path.basename(path),
                                                      state["sha256"][:12])
        closed_now = []

        def listing():
            fresh = bounded_engine_call(self.worker.live_workspaces, OWNED_STOP_WAIT_SECONDS)
            malformed = live_listing_problem(fresh)
            if malformed is not None:
                raise ValueError(malformed)
            return fresh

        def boundary():
            """The blocking reads of ONE effect boundary — a fresh complete
            listing, fresh lease-scoped child evidence, the lease's task
            observed afresh — and the fresh proof over them: the canonical
            binding re-derived and EQUAL, the child rule, the task's identity
            AND terminal status. Returns ``(listing, None)`` or ``(None,
            refusal)``."""
            try:
                fresh = listing()
            except Exception as exc:                      # noqa: BLE001
                return None, refused(False, "the live workspace listing is unreadable at"
                                            " the effect boundary (%s)" % exc)
            children_now = self._spawn_records_raw(lease=lease)
            identities_now, _current_now, tasks_now, problem, detail = _canonical_binding(
                self, entry)
            if problem == mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE:
                # A source that stopped answering is reported as that —
                # recoverable — never as a changed binding.
                return None, refused(False, "at the effect boundary: %s" % detail, problem)
            if (identities_now, tasks_now) != (identities, tasks):
                return None, refused(True, "the canonical binding changed since the proof (%s)"
                                           % ("%s: %s" % (problem, detail) if problem
                                              else "other identities"))
            child_problem = _retirement_child_problem(self, entry, children_now, tasks)
            if child_problem is not None:
                return None, refused(child_problem[0], "at the effect boundary: %s"
                                     % child_problem[1])
            late = task_problem("at the effect boundary: ")
            if late is not None:
                return None, refused(*late)
            return fresh, None

        # EACH CLOSE IS ITS OWN EFFECT BOUNDARY: its blocking reads and a fresh
        # proof first, then a fresh admission taken under the gate's critical
        # section with the DURABLE close claim written inside it (no Mission
        # lock across the engine call), then the close.
        for workspace_id in sorted(snapshots):
            fresh, refusal = boundary()
            if refusal is not None:
                return refusal
            if not any(item.get("workspace_id") == workspace_id for item in fresh):
                continue    # gone since the proof: nothing to close
            if not snapshots[workspace_id].still_matches(fresh, entry=entry):
                return refused(True, "workspace %s no longer matches its proof (replaced or"
                                     " reused by another party); it is never closed"
                                     % workspace_id)

            # The durable CLAIM, built and its lossless round trip verified
            # BEFORE any admission or effect: an identity that could not be
            # read back exactly is never closed.
            claim_receipt = self._retirement_receipt(
                sequence, "%s %s (proven owned: agents %s; runtime state %s; claimed before"
                          " the engine call, not proof of it)" % (
                              RETIREMENT_CLOSE_CLAIMED, json.dumps(workspace_id),
                              ",".join(sorted(snapshots[workspace_id].agent_names)),
                              preserved))
            if _retirement_close_claims({"receipts": [claim_receipt]}) != (
                    {workspace_id: {sequence}}, {}, 0):
                return refused(True, "workspace %r cannot be recorded losslessly in a durable"
                                     " close claim; it is never closed" % (workspace_id,))

            def mark(claim_receipt=claim_receipt):
                entry["receipts"] = list(entry["receipts"]) + [claim_receipt]
                self.store.save(workflows)
            admission = self.mission_gate.admit_and_mark(
                entry, mission_gate_module.BOUNDARY_SPAWN, mark)
            if not admission.ok:
                return admission
            retired[workspace_id] = sequence
            try:
                closed, _closed_id, why, detail = ws_module.close_proven_workspace(
                    snapshots[workspace_id], fresh,
                    self._bounded_close(self.worker.close_workspace), entry=entry)
            except Exception as exc:                      # noqa: BLE001
                return uncertain(workspace_id, "its engine call raised %s (%s)"
                                 % (exc.__class__.__name__, exc), "absence is not observed")
            if not closed:
                return uncertain(workspace_id, "it was then refused before the engine call"
                                 " (%s: %s), so no close was invoked" % (why, detail),
                                 "it is still listed")
            entry["receipts"] = list(entry["receipts"]) + [self._retirement_receipt(
                sequence, "%s %s (the engine call returned; absence not yet observed)"
                          % (RETIREMENT_CLOSE_RETURNED, json.dumps(workspace_id)))]
            self.store.save(workflows)
            returned.setdefault(workspace_id, set()).add(sequence)
            closed_now.append(workspace_id)
        # OBSERVED ABSENCE of every earlier runtime, after the closes.
        try:
            after = listing()
        except Exception as exc:                          # noqa: BLE001
            if closed_now:
                return uncertain(closed_now[-1], recorded_invocation(closed_now[-1]),
                                 "absence is not observable (%s)" % exc)
            return refused(False, "absence is not observable (%s)" % exc)
        listed = sorted(workspace_id for workspace_id in identities
                        if any(item.get("workspace_id") == workspace_id for item in after))
        for workspace_id in listed:
            if workspace_id in closed_now:
                return uncertain(workspace_id, recorded_invocation(workspace_id),
                                 "it is still listed")
            return refused(True, "workspace %s is listed again after it was observed absent"
                                 " (another party's id reuse, or it reappeared); nothing more"
                                 " is done" % workspace_id)
        # DISCARD the persisted state that names it — its own effect boundary.
        current, unusable = _lease_runtime_state(lease)
        if unusable is not None:
            return refused(unusable[0], "the persisted runtime state %s" % unusable[1])
        if current is not None:
            if state is None or current["sha256"] != state["sha256"]:
                return refused(True, "the persisted runtime state changed since it was"
                                     " preserved (sha256 %s); it is never discarded unproven"
                                     % current["sha256"][:12])

            def mark_discard():
                entry["receipts"] = list(entry["receipts"]) + [self._retirement_receipt(
                    sequence, "discarding the persisted runtime state (sha256 %s): %s"
                              % (current["sha256"][:12], preserved))]
                self.store.save(workflows)
            admission = self.mission_gate.admit_and_mark(
                entry, mission_gate_module.BOUNDARY_SPAWN, mark_discard)
            if not admission.ok:
                return admission
            try:
                os.unlink(_runtime_state_path(lease))
            except FileNotFoundError:
                pass
            except OSError as exc:
                return refused(False, "the persisted runtime state could not be discarded"
                                      " (%s)" % exc.__class__.__name__)
        entry["receipts"] = list(entry["receipts"]) + [self._retirement_receipt(
            sequence, "retired: closed %s; every earlier runtime observed absent; runtime"
                      " state %s" % (", ".join(closed_now) or "none",
                                     "discarded" if current is not None else "none"))]
        self.store.save(workflows)
        return None

    def _retirement_receipt(self, sequence, text):
        import secrets
        summary = "%s: dispatch %d %s" % (RETIREMENT_RECEIPT_MARKER, sequence, text)
        return {
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mretire-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }

    def _spawn_and_bind(self, workflows, entry, request, dispatch_sequence):
        """The guarded spawn of ONE dispatch (``dispatch_sequence``) and the
        durable capture of the target identity — shared by a dispatch
        (after its marker) and the resumption of a refused claim."""
        # Start-claim decision: the canonical ENGAGEMENT START is fused to
        # the bridge's real start boundaries (``dispatch_module``'s
        # guarded control plane opens one start immediately before the
        # child runtime is created and another immediately before the
        # objective is handed over, and settles each afterwards). A
        # cancel or EDIT committed before a start's admission yields no
        # start and no invocation; one committed after it is ordered
        # after the admitted start — the settlement records the stop
        # requirement and the owned stop runs; only observed absence
        # confirms it. The marker stays as recoverable dispatch
        # ambiguity, never blindly retried: only a claim the live guard
        # durably recorded as REFUSED, with no start admitted, is resumed
        # (``refused_claim_resumable``). The Mission lock is never held
        # across the bridge.
        try:
            if record_module.is_mission_core_kind(entry):
                spawn_result = self._spawn(
                    self.control_realpath, request,
                    start_guard=_MissionStartGuard(
                        self, workflows, entry, dispatch_sequence))
            else:
                spawn_result = self._spawn(self.control_realpath, request)
        except Exception as exc:                          # noqa: BLE001
            return self._start_failure_outcome(workflows, entry, exc)
        # D1: capture the durable target-Herdr identity from the
        # spawn result at dispatch time (on the INITIAL dispatch;
        # a follow-up keeps the identity bound at first dispatch).
        if entry.get("target_engine") is None:
            entry["target_engine"] = (
                dispatch_module.target_identity_from_spawn(
                    spawn_result, entry, self._clock()
                )
            )
            self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _observation_context(self, entry):
        """The bounded, capability-free target observation the
        verification/status turn is shown (I5 D2). Read-only: calls
        the injected observer with the leased workspace realpath only,
        and projects a closed key set — never a workspace path, lease
        id, capability, or raw observation blob."""
        lease = entry["workspace_lease"]
        try:
            raw = self._observe(lease["path_realpath"])
        except Exception as exc:
            return {
                "available": False,
                "detail": "observation unavailable (%s)"
                % exc.__class__.__name__,
                "target_complete": False,
                "task_status": None,
                "completeness": None,
            }
        if not isinstance(raw, dict):
            return {
                "available": False, "detail": "no observation",
                "target_complete": False, "task_status": None,
                "completeness": None,
            }
        task = raw.get("task") if isinstance(
            raw.get("task"), dict
        ) else {}
        status = task.get("status")
        completeness = raw.get("completeness")
        # R-6 condition 3 applied to THIS existing gate: "the target
        # has stopped" is decided by the SOURCE-SCOPED support
        # primitive over the registered verification consumed-source
        # set, NEVER by global completeness — a production
        # observation is globally PARTIAL whenever agents are listed
        # unprobed, and a global gate here would stall every
        # dispatched workflow forever. A demoting diagnostic in a
        # CONSUMED source (task/reviews/artifacts/observation) still
        # fails closed to a WAIT exactly as before.
        supported, _blocking = evidence_module.observation_supports(
            raw, evidence_module.VERIFICATION_CONSUMED_SOURCES
        )
        target_complete = (
            supported and status in _TARGET_TERMINAL_STATUSES
        )
        return {
            "available": True,
            "detail": None,
            "task_status": status if isinstance(status, str) else None,
            "target_complete": target_complete,
            "completeness": completeness,
        }

    def _readiness_gate(self, workflows, entry):
        """The I3 bootstrap-readiness gate for a DISPATCHED workflow.

        Returns a BrokerOutcome when the workflow STOPPED durably, and
        None when it did not — including every case where readiness is
        already evidenced, which is the case an engineering mission is
        in for all but the first minutes of its life.

        Writes at most one receipt per STATE CHANGE, following
        `_note_observation`: a target polled repeatedly while its roles
        come up does not churn the store, and a restart re-reads the
        same durable states rather than replaying them, because the
        receipt is written only when the newly derived state differs
        from the last one already on the record.
        """
        previous = readiness_module.last_recorded_state(entry)
        state, detail, _pairs, _probed, stop = readiness_module.evaluate(
            entry,
            lambda: self.worker.probe_readiness(
                entry["workspace_lease"]["path_realpath"]
            ),
            self._clock(),
        )
        if state != previous:
            entry["receipts"] = list(entry["receipts"]) + [
                readiness_module.readiness_receipt(
                    state, detail, now=self._clock()
                )
            ]
            self.store.save(workflows)
        if not stop:
            return None
        problem = readiness_module.problem_for(state)
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
        return _refused(problem, detail)

    def _note_observation(self, entry, observation):
        """Record the last DISTINCT target observation (I6 carried
        item). Mutates ``entry["last_observation"]`` and returns True
        ONLY when the observed (task_status, completeness) pair CHANGES
        — so a target polled repeatedly never churns the store, while a
        target that becomes unobservable records the (None, None) pair
        once, making /status able to surface it. Never authority: a
        bounded projection of the read-only observation."""
        status = (
            observation["task_status"]
            if isinstance(observation["task_status"], str) else None
        )
        completeness = (
            observation["completeness"]
            if isinstance(observation["completeness"], str) else None
        )
        prior = entry["last_observation"]
        if prior is not None and (
            prior["task_status"] == status
            and prior["completeness"] == completeness
        ):
            return False
        entry["last_observation"] = {
            "task_status": status,
            "completeness": completeness,
            "observed_at": self._clock(),
        }
        return True

    def _collect_evidence(self, entry):
        """One I1 evidence collection through the SAME injected
        seams the Broker holds — the caller supplies nothing."""
        return evidence_module.collect_verification_evidence(
            entry, self.transport, self._observe,
            self.control_realpath, self._clock(),
        )

    def _record_verification_observations(self, entry, projection):
        """Append the Runtime's OWN observation receipts of a verification
        collection (idempotent: an identical receipt is never repeated):
        one review-round receipt per round the observer lists (decision
        from the canonical listing, digest read through the hardened
        review read; a round whose record cannot be read is not
        invented), then — in the SAME save — one review LISTING receipt
        stating the complete listing observed (every listed round and
        decision, its completeness, the highest round's content digest as
        read in this pass) unless it repeats the latest listing receipt
        (R17-1: the proof of the review standing), and one candidate
        observation receipt of the leased workspace (the P1-A6 staged
        identity against the authorized baseline, its exactness status and
        HEAD) unless it repeats the latest one. Nothing here decides
        anything; the gates below do."""
        lease = entry.get("workspace_lease") or {}
        lease_path = lease.get("path_realpath")
        bindings = projection.get("bindings") or {}
        target = bindings.get("target_task") or {}
        task_id = target.get("task_id") if target.get("status") == (
            evidence_module.BINDING_EXACT) else None
        existing = set((r.get("bounded_summary"), r.get("digest"))
                       for r in entry["receipts"])
        added = []
        if lease_path is not None and task_id is not None:
            try:
                raw = self._observe(lease_path)
            except Exception:                              # noqa: BLE001
                raw = None
            reviews = raw.get("reviews") if isinstance(raw, dict) else None
            listed = reviews.get("listed") if isinstance(reviews, dict) else None
            read = {}
            for item in listed if isinstance(listed, list) else []:
                if not isinstance(item, dict):
                    continue
                round_number = item.get("round")
                if not isinstance(round_number, int) or isinstance(round_number, bool):
                    continue
                if not 1 <= round_number <= MAX_RECEIPT_NUMBER:
                    continue
                name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (task_id, round_number)
                try:
                    read_status, _count, digest, _text = evidence_module.read_state_artifact(
                        lease_path, evidence_module.REVIEWS_SUBDIRS, name)
                except Exception:                          # noqa: BLE001
                    continue
                if not isinstance(digest, str):
                    continue
                read[round_number] = digest
                receipt = review_round_receipt(
                    round_number, item.get("decision"), name, digest, self._clock())
                if (receipt["bounded_summary"], receipt["digest"]) not in existing:
                    added.append(receipt)
            if isinstance(reviews, dict):
                # R17-1: the complete listing this pass observed, in the SAME
                # save as its round receipts — the only proof of the standing.
                pairs, complete = listing_statement(
                    reviews, raw.get("diagnostics") if isinstance(raw, dict) else None)
                receipt = review_listing_receipt(
                    pairs, read.get(pairs[-1][0]) if pairs else None, complete,
                    self._clock())
                if not same_review_listing(review_round_reading(entry)["listing"],
                                           receipt):
                    added.append(receipt)
        if lease_path is not None:
            # The reviewed candidate: the leased workspace's P1-A6 staged
            # identity against the recorded authorized baseline, recorded
            # unless it repeats the latest observation exactly.
            observation = capture_candidate(
                self.transport, lease_path, entry["approved_baseline"]["commit_sha"])
            receipt = candidate_receipt(observation, self._clock())
            if not same_candidate_observation(observed_candidate(entry), receipt):
                added.append(receipt)
        if added:
            entry["receipts"] = list(entry["receipts"]) + added

    def _verification_block(self, workflows, entry, problem, detail):
        """A DURABLE verification stop (D-A5): with the target
        stopped there is nothing left to wait for, so a failed
        evidence shape or a failed structural gate transitions the
        workflow to BLOCKED — with the reason recorded TRUTHFULLY as
        a fixed-marker E-5 receipt so /status can surface it (ruling
        R-4: no BLOCKED path strands a consumed approval silently).
        Never an indefinite re-poll.

        Task 8 S-IV (Supervisor refinement C): for a Mission-origin
        record this durable stop is itself an ACCEPTED RESULT of the
        verification pass (an incomplete turn, a failed evidence shape,
        a failed fresh conjunct) and passes the gate's short critical
        section like every other returned outcome: a hold seen there
        records one hold receipt and keeps DISPATCHED instead of
        terminalizing; a terminal refusal blocks with the gate's own
        cause."""
        import secrets

        def accept_block():
            entry["receipts"] = list(entry["receipts"]) + [{
                "kind": record_module.RECEIPT_KIND_EVIDENCE,
                "turn_id": "vblock-" + secrets.token_hex(8),
                "recorded_at": self._clock(),
                "digest": entry["handoff"]["digest_sha256"],
                "bounded_summary": (
                    "%s: %s — %s" % (
                        VERIFICATION_BLOCK_MARKER, problem, detail,
                    )
                )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
            }]
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )

        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_VERIFICATION_TURN,
            accept_block,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept_block()
            self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_VERIFICATION_BLOCKED,
            problem=problem, detail=detail,
        )

    def _verify(self, workflows, entry):
        """DISPATCHED: observe the target read-only; when it has
        STOPPED (decided source-scoped per ruling R-6, never on
        global completeness), collect the I1 evidence projection.
        A projection that is incomplete or schema-invalid refuses
        BEFORE ANY MODEL CALL and stops durably (D-A5). A complete
        projection runs the fresh verification turn WITH the
        rendered evidence; a `verified_result` outcome from that
        turn is NECESSARY, NEVER SUFFICIENT — the D-A4 conjunctive
        gates are applied independently against a FRESH collection
        before anything is recorded, and one failing conjunct stops
        the workflow durably with its own problem code. Herd
        lifecycle COMPLETE alone can never produce VERIFIED: it is
        one conjunct of eight. While the target is still running the
        workflow stays DISPATCHED — writing the store ONLY when the
        observed pair changed (I6), never every poll."""
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return self._verification_block(
                workflows, entry, problem, detail
            )
        observation = self._observation_context(entry)
        observation_changed = self._note_observation(entry, observation)
        if not observation["target_complete"]:
            # I3: THIS is where a bootstrap failure and a legitimately
            # long mission were previously indistinguishable — both
            # arrived here and waited. The readiness gate separates
            # them, and separates them in exactly one direction: it
            # can stop a workflow that, within the bootstrap window,
            # has not yet been ready. Outside
            # that case it returns from durable state, and within that
            # path there is no probe, no clock read and no bound once
            # readiness has been evidenced once. An engineering
            # mission does not acquire a deadline here.
            stopped = self._readiness_gate(workflows, entry)
            if stopped is not None:
                return stopped
            # Legitimate wait: no transition. A store write happens
            # ONLY when the observed pair changed (so an indefinitely
            # unobservable target is recorded once, then quiet).
            if observation_changed:
                self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome="target_running",
                detail="target task status: %s"
                % observation["task_status"],
            )
        # Target stopped: collect the evidence projection. Broken
        # evidence refuses BEFORE any model call — no Codex turn is
        # spent on it — and stops durably (nothing left to wait for).
        projection = self._collect_evidence(entry)
        # Task 8 S-V (R2-11-a): what this collection OBSERVED is the
        # Runtime's own durable evidence — every review round's digest
        # and the candidate identity — appended before any gate decides,
        # so the block or the acceptance that follows persists it.
        self._record_verification_observations(entry, projection)
        # Policy drift is already one of the separate verification
        # conjuncts.  It is checked here, after the fresh target
        # observation is captured but BEFORE a verification role turn,
        # so the common Broker gate must not shadow this durable stop.
        # The pre-I9 ordering returned broker_policy_digest_drift before
        # capability consumption on every poll: the Runtime discarded
        # that outcome, last_observation stayed stale, and the existing
        # verification-block receipt was unreachable.
        for precheck in (
                _gate_evidence_complete,
                _gate_evidence_valid,
                _gate_control_policy):
            gate_problem, gate_detail = precheck(entry, projection)
            if gate_problem is not None:
                return self._verification_block(
                    workflows, entry, gate_problem, gate_detail
                )
        result = self._role_turn(
            "verification", entry, self._clock(),
            observation=observation, evidence=projection,
        )
        if result.status != ROLE_TURN_COMPLETED or (
            result.outcome is None
        ):
            return self._verification_block(
                workflows, entry, PROBLEM_TURN_NOT_COMPLETED,
                "verification turn did not complete with an outcome"
                " (status %s, reason %s)"
                % (result.status, result.reason),
            )
        if result.turn is not None:
            entry["codex_turns"] = list(entry["codex_turns"]) + [
                result.turn
            ]
        outcome = result.outcome
        detail_text = _bounded_detail(result)
        if outcome == OUTCOME_VERIFIED_RESULT:
            # NECESSARY, NEVER SUFFICIENT: apply the D-A4 gates
            # independently against a FRESH collection (fresh disk
            # read through the same seams) BEFORE recording
            # anything. One failing conjunct stops durably with its
            # own code; the turn's verified_result cannot override a
            # single gate.
            fresh = self._collect_evidence(entry)
            for _gate_name, check in _VERIFICATION_GATES:
                gate_problem, gate_detail = check(entry, fresh)
                if gate_problem is not None:
                    return self._verification_block(
                        workflows, entry, gate_problem, gate_detail
                    )
            summary = detail_text or "verified"

            # Task 8 S-IV: the verification turn was a long blocking
            # step; its verified result is ACCEPTED only under the gate's
            # short critical section.
            def accept_verified():
                entry["verified_result"] = {
                    "summary": summary,
                    "digest": text_digest(summary),
                    "recorded_at": self._clock(),
                }
                record_module.apply_transition(
                    entry, record_module.PHASE_VERIFIED
                )

            refusal, accepted = self._mission_recheck(
                workflows, entry,
                mission_gate_module.BOUNDARY_VERIFICATION_TURN,
                accept_verified,
            )
            if refusal is not None:
                return refusal
            if not accepted:
                accept_verified()
                self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=outcome
            )
        # Supervisor refinement C: EVERY returned result of the blocking
        # turn is gated before it is accepted — the follow-up request,
        # the re-authorization proposal and the blocked proposal alike.
        if outcome == OUTCOME_REQUEST_FOLLOW_UP:
            # Record the bounded failed-acceptance evidence a
            # subsequent ACTION_FOLLOW_UP builds the corrective brief
            # from; stay DISPATCHED.
            def accept_follow_up():
                entry["receipts"] = list(entry["receipts"]) + [{
                    "kind": "evidence",
                    "turn_id": (
                        result.turn["turn_id"] if result.turn else "verify"
                    ),
                    "recorded_at": self._clock(),
                    "digest": entry["handoff"]["digest_sha256"],
                    "bounded_summary": (
                        dispatch_module.CORRECTION_RECEIPT_MARKER
                        + ": " + (detail_text or "correction requested")
                    )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
                }]

            refusal, accepted = self._mission_recheck(
                workflows, entry,
                mission_gate_module.BOUNDARY_VERIFICATION_TURN,
                accept_follow_up,
            )
            if refusal is not None:
                return refusal
            if not accepted:
                accept_follow_up()
                self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=outcome
            )
        if outcome == OUTCOME_NEEDS_REAUTHORIZATION:
            target_phase = record_module.PHASE_NEEDS_REAUTHORIZATION
        else:
            # blocked (the only remaining verification outcome).
            target_phase = record_module.PHASE_BLOCKED

        def accept_stop():
            record_module.apply_transition(entry, target_phase)

        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_VERIFICATION_TURN,
            accept_stop,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept_stop()
            self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=outcome
        )

    def _observe_raw(self, repo_path):
        """One guarded canonical observation; None on any failure."""
        try:
            raw = self._observe(repo_path)
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _spawn_records_raw(self, lease=None):
        """One guarded control-repository spawn-record projection.

        With ``lease`` (the workflow's recorded lease path) it is SCOPED to
        the records that name that lease (``_names_lease``), classified over
        the WHOLE file: the control repository's unrelated history — which
        only grows, one record per spawn — can then neither push a relevant
        record past the listing bound nor mark the evidence truncated. Only
        a relevant set that itself exceeds the bound is truncated, and every
        consumer still refuses truncated evidence."""
        try:
            if lease is None:
                raw = self._spawn_records(self.control_realpath)
            else:
                raw = self._spawn_records(self.control_realpath,
                                          relevant=_names_lease(lease))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _reconcile_block(self, workflows, entry, problem, detail):
        """A DURABLE recovery stop (D-B3 / ruling R-3): the binding
        could not be PROVEN, so the workflow stops with the reason
        recorded as a fixed-marker E-5 receipt for /status (D-B4) —
        scoped to this action only. Prefer BLOCKED over a probable
        guess; a human resolves it."""
        import secrets
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "rblock-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": entry["handoff"]["digest_sha256"],
            "bounded_summary": (
                "%s: %s — %s" % (
                    RECOVERY_BLOCK_MARKER, problem, detail,
                )
            )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }]
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_RECOVERY_BLOCKED,
            problem=problem, detail=detail,
        )

    def _bind_settled_handover(self, workflows, entry, start_id, sequence):
        """Task 8 S-VII correction 3 (S7c-R3): bind the RESUMED task
        handover's task identity from its CANONICAL settlement when the
        workflow's ``target_engine`` save was lost after the handover was
        admitted (``resumed_handover_binding``). Evidence only: no spawn, no
        start, no task, no marker, reservation or charge — reached only
        through ``reconcile_dispatch`` AFTER the action admission (current
        revision, authorization and controls: a terminal cause blocks first)
        and the owner's pending-stop recovery.

        Proven from evidence the system holds, before anything is written:
        - the canonical start the guard's own ``claim:admitted`` receipt
          names is THE one task start of this workflow's engagement (same
          workflow, engagement and ordinal, this Runtime's owner reference,
          the authorization the record is bound to), settled ``completed``
          with no stop owed and a usable task id;
        - the engagement's one runtime start is settled ``completed`` with no
          stop owed and the SAME workspace and agent set;
        - the approved objective is the one handed over: the marker of this
          ordinal carries the record's (immutable) handoff digest;
        - ownership of that runtime is proven fresh (live listing, the
          settled workspace id and exact agent set, under the lease).

        Not settled yet, a source or a listing that cannot answer → a
        refusal, nothing written (the owner's recovery settles an open
        start; a later pass retries). A contradiction or a failed proof →
        durable BLOCKED ``broker_resume_binding_unproven``. Proven →
        ``target_engine`` exactly as the uninterrupted handover writes it
        (same task id, alias and target), the task start's own
        ``settled:completed stop=none`` receipt when the loss preceded it,
        and one ``recovery bound`` evidence receipt, in ONE save."""
        import secrets
        from target_runtime import workspace_ownership as ws_module
        gate = self.mission_gate
        starts = gate.engagement_starts(entry)
        if starts is None:
            return _refused(
                mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE,
                "the Mission source cannot answer for the engagement starts;"
                " nothing is bound")
        linkage = entry[record_module.MISSION_AUTHORITY_KEY]
        engagement_id = entry[record_module.MISSION_ENGAGEMENT_KEY]["engagement_id"]
        own = [start for start in starts if start["engagement_id"] == engagement_id]
        named = [start for start in starts if start["start_id"] == start_id]
        task = named[0] if len(named) == 1 else None
        contradiction = None
        if task is None:
            contradiction = ("the canonical record holds no single start %s, which"
                             " the admitted task claim names" % start_id)
        elif [start["start_id"] for start in own if start["point"] == "task"] != [
            start_id
        ]:
            contradiction = ("start %s is not the one task start of engagement %s"
                             % (start_id, engagement_id))
        elif (task["workflow_id"], task["engagement_sequence"], task["owner_ref"],
              task["authorization_id"], task["authorization_digest_sha256"]) != (
                  entry["workflow_id"], sequence, gate.owner_ref(entry, sequence),
                  linkage["authorization_id"],
                  linkage["authorization_digest_sha256"]):
            contradiction = ("start %s belongs to another workflow, ordinal, owner or"
                             " authorization" % start_id)
        elif task["settlement"] is None:
            return _refused(
                PROBLEM_RESUME_BINDING_UNPROVEN,
                "the task start %s is not settled; the owner's recovery settles"
                " it and nothing is bound" % start_id)
        identity = task_id = None
        if contradiction is None:
            settlement = task["settlement"]
            identity = settlement.get("identity")
            identity = identity if isinstance(identity, dict) else {}
            task_id = identity.get("task_id")
            runtime = [start for start in own if start["point"] == "runtime"]
            runtime_settlement = (runtime[0]["settlement"] or {}) if len(runtime) == 1 else {}
            runtime_identity = runtime_settlement.get("identity")
            runtime_identity = (runtime_identity if isinstance(runtime_identity, dict)
                                else {})
            markers = [receipt for receipt in entry["receipts"]
                       if receipt.get("kind") == record_module.RECEIPT_KIND_EVIDENCE
                       and receipt["bounded_summary"].startswith(
                           dispatch_module.DISPATCH_RECEIPT_MARKER)]
            if settlement.get("outcome") != mission_gate_module.START_OUTCOME_COMPLETED:
                contradiction = ("the task start %s settled %s, not completed"
                                 % (start_id, settlement.get("outcome")))
            elif mission_gate_module.start_stop_required(task):
                contradiction = ("the task start %s owes a stop; its result is never"
                                 " bound" % start_id)
            elif not isinstance(task_id, str) or not task_id.strip() or task_id == (
                dispatch_module.UNRESOLVED_TASK_ID
            ):
                # The SAME usability rule as ``target_identity_from_task``,
                # which formats the binding: a blank id is refused here and
                # never bound as the unresolved sentinel.
                contradiction = "the task start %s settled no usable task id" % start_id
            elif len(runtime) != 1 or runtime_settlement.get("outcome") != (
                mission_gate_module.START_OUTCOME_COMPLETED
            ) or mission_gate_module.start_stop_required(runtime[0]):
                contradiction = ("engagement %s holds no single runtime start settled"
                                 " completed without a stop" % engagement_id)
            elif not runtime_identity.get("workspace_id") or (
                runtime_identity.get("workspace_id"),
                sorted(runtime_identity.get("agent_names") or []),
            ) != (identity.get("workspace_id"), sorted(identity.get("agent_names") or [])):
                contradiction = ("the task start %s names another runtime than the"
                                 " settled runtime start" % start_id)
            elif len(markers) != sequence or markers[-1]["digest"] != (
                entry["handoff"]["digest_sha256"]
            ) or "(dispatch %d," % sequence not in markers[-1]["bounded_summary"]:
                contradiction = ("the approved objective is not provably the one"
                                 " dispatch %d handed over (%d markers)"
                                 % (sequence, len(markers)))
            elif not self.worker.observes_live_workspaces:
                contradiction = ("no live-workspace observation capability is wired;"
                                 " ownership of the settled runtime cannot be proven")
        if contradiction is not None:
            return self._reconcile_block(workflows, entry,
                                         PROBLEM_RESUME_BINDING_UNPROVEN, contradiction)
        try:
            live = bounded_engine_call(self.worker.live_workspaces,
                                       OWNED_STOP_WAIT_SECONDS)
        except Exception as exc:                          # noqa: BLE001
            return _refused(
                PROBLEM_RESUME_BINDING_UNPROVEN,
                "the live workspace listing is unreadable (%s); nothing is bound"
                % exc.__class__.__name__)
        malformed = live_listing_problem(live)
        if malformed is not None:
            return _refused(
                PROBLEM_RESUME_BINDING_UNPROVEN,
                "the live workspace listing is unavailable or malformed (%s);"
                " nothing is bound" % malformed)
        verdict, snapshot, why, detail = ws_module.prove_started_runtime(
            identity, live, ownership_module.recorded_lease_realpath(entry))
        if snapshot is None:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RESUME_BINDING_UNPROVEN,
                "ownership of the settled runtime %s is %s (%s: %s); nothing is"
                " bound" % (identity["workspace_id"], verdict, why, detail))
        request = dispatch_module.build_spawn_request(entry)
        entry["target_engine"] = dispatch_module.target_identity_from_task(
            {"repo": str(dispatch_module.task_handover_target(request)),
             "task": {"id": task_id}}, entry, self._clock())
        latest = _start_receipt_facts(entry).get(start_id) or {}
        if (latest.get("state"), latest.get("stop")) != (
            record_module.SETTLED_COMPLETED_STATE, record_module.START_RECEIPT_STOP_NONE,
        ):
            self._mission_start_receipt(
                workflows, entry, start_id, dispatch_module.START_POINT_TASK, sequence,
                record_module.SETTLED_COMPLETED_STATE,
                cause="(recovery pass: the canonical settlement)",
                stop=record_module.START_RECEIPT_STOP_NONE, save=False)
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "rbound-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": entry["handoff"]["digest_sha256"],
            "bounded_summary": (
                "%s: task %s from the canonical settlement of start %s (engagement"
                " %s, dispatch %d); nothing was invoked" % (
                    RECOVERY_BOUND_MARKER, task_id, start_id, engagement_id,
                    sequence))[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }]
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=OUTCOME_RECONCILED,
            detail="bound the resumed handover's settled task %s from canonical"
            " start %s" % (task_id, start_id))

    def _reconcile(self, workflows, entry):
        """DISPATCHED + unresolved identity: bind EXACTLY ONE
        provable existing child by writing the EXISTING
        target_engine field, or stop durably. Evidence-only — this
        handler performs no spawn, no dispatch, no replay, and
        accepts no command; its only write is the binding itself or
        the durable block.

        The binding proof (D-B3): the control repository's own
        persisted spawn record (its already-realpath `repo` equal to
        the leased workspace realpath EXACTLY), the leased workspace's
        own canonical observation reporting the SAME task id, clean
        bounded projections on both sides, and EXACTLY ONE candidate.
        The deterministic alias is never consulted: herd's child
        records carry none.

        Task 8 S-VII correction 3 (S7c-R3): a RESUMED task handover writes
        no control-repository child record, so this proof could never bind
        it; its lost binding is recovered from its CANONICAL settlement
        instead (``_bind_settled_handover``). Every other record takes the
        D-B1 proof below, unchanged.
        """
        ok, problem, detail = self.worker.verify_workspace(entry)
        if not ok:
            return _refused(problem, detail)
        engine = entry.get("target_engine")
        if engine is not None and isinstance(
            engine.get("task_id"), str
        ) and engine["task_id"] and engine["task_id"] != (
            dispatch_module.UNRESOLVED_TASK_ID
        ):
            return _refused(
                PROBLEM_RECONCILE_ALREADY_BOUND,
                "the target-engine identity is already durably bound"
                " (task %s); reconcile binds exactly once"
                % engine["task_id"],
            )
        binding = resumed_handover_binding(entry)
        if binding is not None:
            return self._bind_settled_handover(workflows, entry, *binding)
        lease_real = os.path.realpath(
            entry["workspace_lease"]["path_realpath"]
        )
        # Control-side evidence: the persisted spawn records that name THIS
        # lease — classified over ALL of them, so unrelated history cannot
        # truncate or hide them — including valid parent_task_id=None /
        # dependency=False outer spawns.
        control_records = self._spawn_records_raw(lease=lease_real)
        if control_records is None:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn-record projection is"
                " unavailable; a partial view is never a binding"
                " proof",
            )
        if control_records.get("truncated") is True:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_TRUNCATED,
                "the spawn-record listing is TRUNCATED; a listing"
                " that may omit a candidate is never a binding proof",
            )
        if control_records.get("state") not in ("available", "empty"):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn records are not cleanly"
                " readable (state %r)"
                % (control_records.get("state"),),
            )
        listed = (
            control_records.get("listed")
            if isinstance(control_records.get("listed"), list) else None
        )
        count = control_records.get("count")
        if (
            listed is None
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or count != len(listed)
        ):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn-record projection has a"
                " malformed count/listing shape; it is never a"
                " binding proof",
            )
        # Lease-side observation: the workspace's OWN task identity.
        lease_raw = self._observe_raw(lease_real)
        supported, blocking = evidence_module.observation_supports(
            lease_raw, evidence_module.RECONCILE_CONSUMED_SOURCES
        )
        if not supported:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the leased workspace's observation is degraded in a"
                " consumed source (%s); a partial view is never a"
                " binding proof" % ", ".join(sorted({
                    str(d.get("source")) for d in blocking
                })),
            )
        lease_task = (
            lease_raw.get("task")
            if isinstance(lease_raw.get("task"), dict) else {}
        )
        observed_id = lease_task.get("id")
        if lease_task.get("state") != "available" or not isinstance(
            observed_id, str
        ) or not observed_id:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the leased workspace reports no observable task"
                " identity (task state %r); an unobservable identity"
                " is never a binding proof"
                % (lease_task.get("state"),),
            )
        # I4 item 2. BOTH sides are realpath'd here.
        #
        # The audit found the writer already resolves: within
        # `control_plane.spawn_child` the target is rebound to
        # `Path(target_repo).expanduser().resolve()` BEFORE the record
        # is built, so within that writer the `str(target)` fallback
        # is resolved too, and `spawn` has a single return carrying
        # `repo`. So a raw comparison was in fact sound for records
        # that writer produced; records from another writer are
        # outside what was checked.
        #
        # It is not left raw, for two reasons. First, within this
        # module its soundness rested on a property of a DIFFERENT
        # module that no assertion here covered — an undocumented
        # coincidence rather than a guarantee. Second, the failure
        # DIRECTION is bad in a
        # way that "fail-closed" hides: a missed match yields
        # PROBLEM_RECONCILE_NO_MATCH and a durable block, converting a
        # RECOVERABLE dispatch into a permanent stranding, which is
        # the dead-end class this task exists to close. Safe and
        # correct are not the same thing here.
        #
        # Within this comparison, resolving both sides only ADDS a
        # match, and only between two spellings of the SAME FILE, so a
        # workflow is not bound to a different workspace. Outside
        # that: a record naming a path that no longer exists resolves
        # lexically, which is the behaviour the lease side already
        # had.
        matching = [
            candidate for candidate in listed
            if isinstance(candidate, dict)
            and isinstance(candidate.get("repo"), str)
            and os.path.realpath(candidate["repo"]) == lease_real
        ]
        if not matching:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_NO_MATCH,
                "no recorded child names this workflow's leased"
                " workspace (realpath comparison on both sides over"
                " %d recorded child(ren)); nothing provable to bind"
                % len(listed),
            )
        recorded_ids = [
            candidate.get("task_id") for candidate in matching
        ]
        if any(
            not isinstance(recorded_id, str) or not recorded_id
            or recorded_id != observed_id
            for recorded_id in recorded_ids
        ):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_CONFLICT,
                "a workspace-matching child record disagrees with"
                " the workspace's own observed task identity"
                " (recorded %r vs observed %r); a conflicting"
                " identity is never bound"
                % (sorted(set(map(repr, recorded_ids))), observed_id),
            )
        if len(matching) > 1:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_MULTIPLE,
                "%d recorded children name this leased workspace;"
                " EXACTLY ONE provable candidate is required, so an"
                " ambiguous set is never bound" % len(matching),
            )
        entry["target_engine"] = {
            # The deterministic derivation — a LABEL, not evidence
            # (the binding proof above never consulted any alias).
            "alias": dispatch_module.ALIAS_PREFIX + entry[
                "workflow_id"
            ],
            "task_id": observed_id,
            # Identity display (bounded schema field): the canonical
            # target, exactly as the dispatch-time capture stores on
            # its own fallback path. The binding PROOF was the exact
            # lease-realpath equality computed above.
            "repo": entry["target"]["canonical_url"],
            "dispatched_at": self._clock(),
        }
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=OUTCOME_RECONCILED,
            detail="bound the single provable child: target task %s"
            % observed_id,
        )

    def _complete(self, workflows, entry):
        """VERIFIED -> COMPLETED. Mechanical: the verified result was
        recorded at VERIFIED; completion marks the workflow done and
        the result ready for the adapter to deliver."""
        if entry.get("verified_result") is None:
            return _refused(
                PROBLEM_NO_VERIFIED_RESULT,
                "a VERIFIED workflow has no recorded verified result;"
                " refusing to complete",
            )

        # Task 8 S-IV: completion is an accepted result; a Mission-origin
        # record completes only under the gate's short critical section.
        def accept():
            record_module.apply_transition(
                entry, record_module.PHASE_COMPLETED
            )

        refusal, accepted = self._mission_recheck(
            workflows, entry, mission_gate_module.BOUNDARY_COMPLETION, accept,
        )
        if refusal is not None:
            return refusal
        if not accepted:
            accept()
            self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _settle_verification_for_release(self, workflows, entry):
        """Task 8 R20-1: with ABSENCE established (``verification_release_hold``
        returned None), settle every verification attempt the record leaves
        unresolved — through the barrier's own settlement
        (``_verification_barrier``: an attempt claimed and never settled is
        settled ``interrupted``, a ``start-unknown`` one is resolved from its
        claim's owned-root count; the same receipts a delivery pass writes,
        once) — and refuse while one stays unresolved (a ``start-unknown``
        attempt whose claim recorded no count) or the ownership read turns
        unclear meanwhile. An attempt settled NOT REPLAYABLE bars a further
        verification, never the cleanup. Returns None or the refusal."""
        unresolved, undecodable = record_module.unresolved_verification_attempts(entry)
        if not unresolved and not undecodable:
            return None
        refusal, _roots = self._verification_barrier(workflows, entry)
        if refusal is not None and refusal.problem == PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED:
            return _refused(PROBLEM_VERIFICATION_RETAINED, (
                "the verification scope's ownership is not clear (%s); nothing is"
                " released" % refusal.detail))
        unresolved, undecodable = record_module.unresolved_verification_attempts(entry)
        if unresolved or undecodable:
            return _refused(PROBLEM_VERIFICATION_RETAINED, (
                "verification attempt(s) %s%s cannot be settled from the record and its"
                " ownership records; nothing is released" % (
                    ", ".join(str(number) for number in unresolved) or "-",
                    " (%d undecodable)" % undecodable if undecodable else "")))
        return None

    def _retained_at_boundary(self, workflows, entry, report, recorded, hold):
        """R20-1/R20-2/R21: the release HALTS at its destructive boundary
        with the lease, the record and the ownership records kept (and the
        directory, unless an attempted removal took part of it — R21-3); the
        effects already made are recorded truthfully (the cleanup receipt)
        and the workflow stays a cleanup candidate. The ``workspace removal
        pending`` receipt makes its re-entry the removal ALONE
        (``_retry_workspace_removal``): the trust revocation, the evidence
        preservation and the session close are never replayed — and what
        they established is re-established, read-only, before the removal
        (R21-A: the sessions' absence NOW, ``_sessions_absent_now``)."""
        report.record(
            "workspace", recorded, ownership_module.UNPROVABLE,
            detail="the workspace lease is RETAINED at the destructive"
                   " boundary, with no observed removal: %s" % hold[1],
        )
        self._workspace_removal_receipt(
            entry, record_module.WORKSPACE_REMOVAL_PENDING_RECEIPT_MARKER,
            "the release reached its destructive boundary with the sessions"
            " proven closed; the removal was not made or not observed — %s"
            % hold[1])
        entry["receipts"] = list(entry["receipts"]) + [
            _cleanup_receipt(report, now=self._clock())
        ]
        self.store.save(workflows)
        return BrokerOutcome(
            False, phase=entry["phase"], problem=hold[0],
            detail="%s (at the destructive boundary; %s)"
                   % (hold[1], report.summary()),
        )

    def _boundary_admission(self, entry):
        """Task 8 R21-1: the FRESH Mission cleanup admission at the workspace
        relinquish — None, or ``(problem, detail)``. Mission-origin records
        only (legacy and non-Mission releases are unchanged), exactly
        ``_cleanup_admission_problem``'s condition."""
        if self.mission_gate is None or not record_module.is_mission_core_kind(entry):
            return None
        admission = self.mission_gate.admit_cleanup(entry)
        if admission.ok:
            return None
        return admission.problem, (
            "the cleanup admission refused before the workspace relinquish (%s);"
            " nothing is released" % admission.detail)

    def _sessions_absent_now(self, entry):
        """Task 8 R21-A: the FIRST pass's session bar RE-ESTABLISHED, read-only,
        for the removal-only retry — and (R21-C, C-1) at the first pass's own
        destructive boundary, after its two blocking hold reads — None when
        this workflow's sessions are ABSENT NOW, else
        ``(PROBLEM_WORKSPACE_SESSIONS_RETAINED, detail)``.

        A ``workspace removal pending`` receipt proves the close THEN, not
        absence NOW: between the passes — across a restart — a workspace can
        be listed again, a same-lease child record can appear, or the
        evidence can stop being readable, and neither the holds (process
        scopes) nor ``workspace.release`` (lease and path) reads it. So the
        first pass's own evidence is read again, by the first pass's own
        proofs, selected the same way: a wired Mission-origin record by its
        CANONICAL proof (``_canonical_release_proof``: the binding re-derived
        from the canonical starts, one complete fresh listing, the same-lease
        child evidence exactly the canonical history, no retired identity
        listed), which must find EVERY identity absent; any other record by
        the child-record proof (``_domain_b_proof``: a live OWNED workspace
        retains) and the positive-evidence rule (``_domain_b_nothing_to_close``).
        A broker with no Domain B at all (neither observation nor close wired)
        is, as on the first pass, a configuration fact; one that observes but
        cannot close retains, as the first pass does.

        It CLOSES NOTHING. The close is an effect the first pass made and is
        never replayed; a workspace live again is retained, not closed — its
        owner is the first pass's route, and a later pass decides."""
        canonical = self._canonical_release_proof(entry)
        if canonical is not None:
            if canonical.problem is not None:
                return PROBLEM_WORKSPACE_SESSIONS_RETAINED, (
                    "the sessions' absence is not established now (%s: %s); nothing is"
                    " released" % (canonical.problem, canonical.detail))
            if canonical.snapshots:
                return PROBLEM_WORKSPACE_SESSIONS_RETAINED, (
                    "workspace(s) %s of this workflow are listed live again after its"
                    " sessions were proven closed; nothing is closed or released"
                    % ", ".join(sorted(canonical.snapshots)))
            return None
        if not self.worker.observes_live_workspaces and not self.worker.closes_workspaces:
            return None
        if not self.worker.closes_workspaces:
            return PROBLEM_WORKSPACE_SESSIONS_RETAINED, (
                "no workspace-close capability is wired, so the first pass's session"
                " bar cannot be met; nothing is released")
        snapshot = self._domain_b_proof(entry)
        if snapshot is not None:
            return PROBLEM_WORKSPACE_SESSIONS_RETAINED, (
                "workspace %s of this workflow is listed live again after its sessions"
                " were proven closed; nothing is closed or released"
                % snapshot.workspace_id)
        report = ownership_module.CleanupReport()
        if _domain_b_nothing_to_close(self, entry, report) == SESSIONS_RECLAIMED:
            return None
        return PROBLEM_WORKSPACE_SESSIONS_RETAINED, (
            "the sessions' absence is not established now (%s); nothing is released"
            % (report.unprovable[-1][2] if report.unprovable else "no reason recorded"))

    def _workspace_removal_receipt(self, entry, marker, text):
        """Task 8 R21: append the durable ``<marker>: <text>`` workspace-removal
        receipt — unless it is identical to the latest one (a removal that
        keeps failing the same way is retried without growing the record).
        Returns whether it was appended."""
        import secrets
        summary = ("%s: %s" % (marker, text))[:record_module.MAX_BOUNDED_SUMMARY_CHARS]
        if summary == record_module.latest_workspace_removal_receipt(entry):
            return False
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mremoval-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary,
        }]
        return True

    def _retry_workspace_removal(self, workflows, entry):
        """Task 8 R21-1 / R21-3: the REMOVAL-ONLY re-entry for a release that
        reached its destructive boundary without an observed removal (an
        outstanding ``workspace removal pending`` receipt). The trust
        revocation, the evidence preservation and the session close are
        EFFECTS the first pass already made, and they are NEVER replayed; the
        PRECONDITIONS they established are re-established, fresh, before the
        removal (R21-A: a past close proves closure then, not absence now).
        Both holds (the process scopes; refused, nothing is done or written),
        then the sessions' absence NOW (``_sessions_absent_now``, read-only:
        a workspace live again, a contradictory same-lease child record or
        unreadable evidence removes nothing and records that state — never an
        identical receipt), then a FRESH cleanup admission immediately before
        the removal (refused, nothing is done or written), then the removal
        itself. A removal OBSERVED complete releases the lease
        (``workspace.release``), settles the receipt (``workspace removal
        completed``) and retires the process scopes exactly as the release
        does; one still incomplete keeps the lease and records the new state
        (never an identical one)."""
        hold = verification_release_hold(entry, self.verification_scope_base)
        if hold is not None:
            return _refused(*hold)
        hold = scope_release_hold(entry)
        if hold is not None:
            return _refused(*hold)
        hold = self._sessions_absent_now(entry)
        if hold is not None:
            if not self._workspace_removal_receipt(
                    entry, record_module.WORKSPACE_REMOVAL_PENDING_RECEIPT_MARKER,
                    "the retried removal was not made: %s" % hold[1]):
                return _refused(*hold)                     # as recorded: nothing written
            self.store.save(workflows)
            return BrokerOutcome(False, phase=entry["phase"], problem=hold[0],
                                 detail=hold[1])
        hold = self._boundary_admission(entry)
        if hold is not None:
            return _refused(*hold)
        report = ownership_module.CleanupReport()
        recorded = ownership_module.recorded_lease_realpath(entry)
        ok, problem, detail = self.worker.relinquish_workspace(entry, self._clock())
        report.record(
            "workspace", recorded if recorded is not None else "<unrecorded>",
            ownership_module.OWNED if ok else ownership_module.UNPROVABLE,
            ok=ok, detail=problem,
        )
        if not ok and problem == workspace_module.PROBLEM_RELEASE_INCOMPLETE:
            if not self._workspace_removal_receipt(
                    entry, record_module.WORKSPACE_REMOVAL_PENDING_RECEIPT_MARKER,
                    "the retried removal was not observed complete — %s" % detail):
                return _refused(problem, detail)          # as recorded: nothing written
            entry["receipts"] = list(entry["receipts"]) + [
                _cleanup_receipt(report, now=self._clock())
            ]
            self.store.save(workflows)
            return BrokerOutcome(False, phase=entry["phase"], problem=problem,
                                 detail=detail)
        if not ok:
            return _refused(problem, detail)
        self._workspace_removal_receipt(
            entry, record_module.WORKSPACE_REMOVAL_COMPLETED_RECEIPT_MARKER,
            "the workspace directory is observed removed and its lease released")
        self._retire_process_scopes(entry, report)
        entry["receipts"] = list(entry["receipts"]) + [
            _cleanup_receipt(report, now=self._clock())
        ]
        self.store.save(workflows)
        if report.degraded:
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome=OUTCOME_RELEASED_DEGRADED,
                problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                detail=report.summary(),
            )
        return BrokerOutcome(True, phase=entry["phase"])

    def _scope_retained_receipt(self, refused):
        """R20-2: the durable ``process scope retained`` receipt."""
        import secrets
        summary = "%s: %d scope(s) kept after the workspace lease was released — %s" % (
            record_module.PROCESS_SCOPE_RETAINED_RECEIPT_MARKER, len(refused),
            "; ".join("%s: %s" % (os.path.basename(directory.rstrip(os.sep)), reason)
                      for directory, reason in refused))
        return {
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mscope-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }

    def _retry_scope_retirement(self, workflows, entry):
        """Task 8 R20-B (Addendum B): the RETIREMENT-ONLY retry for a
        workflow whose lease was released while a process scope's
        retirement was refused. No trust revocation, preservation, session
        close or relinquish runs again — only this workflow's OWN scope
        retirement (``retire_workflow_scopes``, selected by its assignment
        credential), and only once both holds ESTABLISH absence for every
        scope: unreadable or ambiguous evidence refuses with nothing done. A
        fresh Mission cleanup admission follows the holds (refused: nothing
        retired, settled or written), and the retirement asks it again before
        EACH deletion (Addendum C, C-1 / C-1b). A retirement that refuses
        nothing — every removal OBSERVED (C-2) — SETTLES the retained receipt
        (``process scope settled``), so pruning may take the record; one
        refused again writes a fresh retained receipt (none when identical to
        the latest) and stays outstanding."""
        hold = verification_release_hold(entry, self.verification_scope_base)
        if hold is not None:
            return _refused(*hold)
        hold = scope_release_hold(entry)
        if hold is not None:
            return _refused(*hold)
        # Addendum C (C-1): a FRESH cleanup admission after both holds'
        # blocking reads — R19-2's shape; ``perform`` admitted only at entry.
        # A hold, an unanswering Mission source or an outstanding start
        # landing meanwhile refuses with nothing retired, settled or written.
        # (The cleanup admission's own rule, R15-3: a cancel or a superseded
        # revision does not refuse it.) The retirement re-asks it before each
        # deletion (C-1b, ``_retire_process_scopes``).
        if self.mission_gate is not None and record_module.is_mission_core_kind(entry):
            admission = self.mission_gate.admit_cleanup(entry)
            if not admission.ok:
                return _refused(admission.problem, (
                    "the cleanup admission refused before the scope retirement (%s);"
                    " nothing is retired or settled" % admission.detail))
        report = ownership_module.CleanupReport()
        receipts = len(entry["receipts"])
        result = self._retire_process_scopes(entry, report)
        if result is not None and not result[1]:
            entry["receipts"] = list(entry["receipts"]) + [
                self._scope_settled_receipt(result[0])
            ]
        if result is not None and not result[0] and len(entry["receipts"]) == receipts:
            # Refused exactly as already recorded, nothing retired: nothing
            # to write (a credential that keeps failing is retried each pass
            # without growing the record).
            return _refused(PROBLEM_PROCESS_SCOPE_RETAINED, (
                "%d process scope record(s) of this workflow are still kept, as"
                " recorded; nothing is settled (%s)" % (len(result[1]), report.summary())))
        entry["receipts"] = list(entry["receipts"]) + [
            _cleanup_receipt(report, now=self._clock())
        ]
        self.store.save(workflows)
        if report.degraded:
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome=OUTCOME_RELEASED_DEGRADED,
                problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                detail=report.summary(),
            )
        return BrokerOutcome(True, phase=entry["phase"])

    def _scope_settled_receipt(self, retired):
        """R20-B: the durable ``process scope settled`` receipt."""
        import secrets
        summary = "%s: absence established for every process scope; %d retired — %s" % (
            record_module.PROCESS_SCOPE_SETTLED_RECEIPT_MARKER, len(retired),
            ", ".join(os.path.basename(directory.rstrip(os.sep)) for directory in retired)
            or "none remained")
        return {
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "mscope-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }

    def _retire_process_scopes(self, entry, report):
        """Reclaim this workflow's scope records (R-54 AR-4).

        Separated from `_release` so the destructive step has its own
        named function and its own row in the cleanup report, rather
        than being an unnamed tail of a long method. Returns
        ``(retired, refused)``, or None when no scope could be examined
        (the record names no control repository).
        """
        from target_runtime import process_ownership as _own
        control = (
            entry.get("control_identity") or {}
        ).get("repository_realpath")
        if not isinstance(control, str) or not control:
            report.record(
                "process_scopes", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                detail="the record names no control repository, so no"
                       " scope can be PROVEN to belong to this"
                       " workflow; nothing is removed",
            )
            return
        # Task 8 R20-B (Addendum C, C-1b): for a Mission-origin record, the
        # FRESH cleanup admission at the ACTUAL deletion boundary — taken by
        # the retirement for EACH object, after that object's reads. Task 8
        # R25-1: it is HELD across the object's effect (under the Mission
        # store lock), so it cannot go stale before the removal. The
        # authority stays here; the module only asks. Legacy and non-Mission
        # releases are unchanged.
        admit = {}
        if self.mission_gate is not None and record_module.is_mission_core_kind(entry):
            admit["admit"] = lambda effect: _cleanup_admission_held(
                self, entry, "before a scope's removal", effect)
        retired, refused = _own.retire_workflow_scopes(
            control, entry["workflow_id"], **admit
        )
        for directory, reason in refused:
            report.record(
                "process_scopes", directory,
                ownership_module.UNPROVABLE, detail=reason,
            )
        retained = self._scope_retained_receipt(refused) if refused else None
        if retained is not None and retained["bounded_summary"] != (
                record_module.latest_process_scope_receipt(entry)):
            # Task 8 R20-2: the lease is already released and a scope was
            # KEPT (its state changed after the boundary re-check, or its
            # removal was not observed — Addendum C). The record stays its
            # only recovery owner: this durable receipt keeps it from
            # pruning (``record.cleanup_evidence_outstanding``) until the
            # retirement-only retry settles it (R20-B Addendum B,
            # ``_retry_scope_retirement``). One identical to the latest is
            # not written again.
            entry["receipts"] = list(entry["receipts"]) + [retained]
        if retired:
            report.record(
                "process_scopes", entry["workflow_id"],
                ownership_module.OWNED, ok=True,
                detail="retired %d process-scope record(s)"
                       % len(retired),
            )
        return retired, refused

    def _release(self, workflows, entry):
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            return _refused(
                PROBLEM_WRONG_PHASE,
                "release requires a terminal phase; the workflow is"
                " %s" % entry["phase"],
            )
        # Task 8 S-V (R2-2): a RETAINED delivery candidate is released by
        # nobody — no trust revocation, no session close, no deletion —
        # until its retention is released or its deadline passes, and
        # never while one of its engagement starts is unresolved.
        if store_module.retention_protects(entry, self._clock()):
            retention = entry[record_module.RETENTION_KEY]
            unresolved = record_module.unresolved_start_receipts(entry)
            return _refused(
                PROBLEM_RETENTION_PROTECTED,
                "workflow %s is retained (%s until %d%s); nothing is released"
                % (entry["workflow_id"], retention["reason"],
                   retention["deadline_at"],
                   "; unresolved engagement start(s): %s"
                   % ", ".join(sorted(unresolved)) if unresolved else ""),
            )
        # Task 8 R20-B (Addendum B): a lease ALREADY released while a process
        # scope's retirement was refused (an outstanding ``process scope
        # retained`` receipt) is re-entered for the scope retirement ONLY —
        # nothing the release already did runs again.
        lease = entry.get("workspace_lease")
        if isinstance(lease, dict) and lease.get("released_at") is not None and (
                record_module.process_scope_retention_outstanding(entry)):
            return self._retry_scope_retirement(workflows, entry)
        # Task 8 R21-1 / R21-3: a release that already reached its destructive
        # boundary without an observed removal (an outstanding ``workspace
        # removal pending`` receipt) is re-entered for the removal ONLY.
        if isinstance(lease, dict) and lease.get("released_at") is None and (
                record_module.workspace_removal_outstanding(entry)):
            return self._retry_workspace_removal(workflows, entry)
        # Task 8 R20-1: an unresolved VERIFICATION is carried through the
        # release HERE, before anything is revoked, closed, deleted or
        # released — the scope retirement below runs only after the
        # workspace was relinquished, too late to keep its evidence. While
        # a verification process may be alive, or its ownership records
        # or attempt records cannot be read, nothing is released: the
        # lease, the record and the ownership records stay, and the next
        # pass re-checks. Once absence is established, an unsettled
        # attempt is settled before the destruction.
        hold = verification_release_hold(entry, self.verification_scope_base)
        if hold is not None:
            return _refused(*hold)
        refusal = self._settle_verification_for_release(workflows, entry)
        if refusal is not None:
            return refusal
        # Task 8 R20-2: every OTHER process scope the workflow owns is held
        # to the same rule before anything is destroyed — absence ESTABLISHED
        # (``scope_release_hold``), or nothing is released.
        hold = scope_release_hold(entry)
        if hold is not None:
            return _refused(*hold)
        report = ownership_module.CleanupReport()
        # I5-1. Trust is revoked BEFORE the directory is removed, and
        # the order is load-bearing in one direction only: while the
        # directory still exists the ownership check is at its
        # strictest. `revoke` itself does NOT require the directory,
        # so a crash between these two steps leaves a second release
        # able to finish the job rather than stranding the entry —
        # which is the live condition this increment was pointed at.
        recorded = ownership_module.recorded_lease_realpath(entry)
        if recorded is None:
            report.record(
                "trust", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                detail="no lease realpath is recorded, so no trust"
                       " key can be PROVEN to belong to this"
                       " workflow; nothing is removed",
            )
        else:
            key = workspace_trust_module.trust_key(recorded)
            verdict = ownership_module.owns_trust_entry(
                entry, key, self.workspaces_root
            )
            if verdict != ownership_module.OWNED:
                report.record("trust", key, verdict)
            else:
                ok, problem, detail = (
                    self.worker.revoke_workspace_trust(entry)
                )
                report.record(
                    "trust", key, ownership_module.OWNED,
                    ok=ok, detail=problem,
                )
                if not ok:
                    entry["receipts"] = list(entry["receipts"]) + [
                        workspace_trust_module.revoke_block_receipt(
                            problem, now=self._clock()
                        )
                    ]
        # R-31 W-3: THE DESTRUCTIVE STEP COMES LAST.
        #
        # Sessions are closed BEFORE the managed directory is deleted.
        # The previous order deleted the directory first, so even
        # fully wired it would have killed agents only after their
        # workspace was already gone — the third instance in this
        # increment of an irreversible step running before the step
        # that makes it safe (the first was `Popen` before the record
        # that attributes it; the second was freeze state restored
        # after the fact). `tests/test_ownership.py`'s
        # `DestructiveOrderingClosureTests` is the structural closure
        # for the class rather than a third reorder.
        #
        # Idempotent on re-entry: the session close proves ownership
        # from durable records and finds no live workspace the second
        # time, which is a REFUSAL rather than a second close; and
        # `worker.relinquish_workspace` refuses a lease already released.
        # So closed-but-not-deleted is a re-enterable state, and the
        # receipt says which half completed.
        # R-37 AB-1: PRESERVE THE TARGET EVIDENCE FIRST.
        #
        # Before the sessions are closed and before the directory is
        # deleted, because both destroy what it reads. Reclaiming live
        # resources and preserving forensics are different
        # obligations, and cleanup was satisfying the first while
        # silently destroying the second — the run finished and the
        # proof that the chain had worked went with the workspace.
        #
        # It copies bytes and records a digest of each FULL file, so a
        # preserved artifact is bound to what was actually there;
        # reconstructing or summarising would be worse than losing it,
        # because a reader could not tell.
        from target_runtime import evidence_preservation as preserve_module
        # AC-2: the workspace identity comes from the SAME unique
        # binding the close is about to act on, derived ONCE here and
        # handed to both. # Two independent derivations of one identity is how a preserved
        # record could name one workspace while the close named another,
        # with no check between them.
        # Task 8 (ownership correction): a wired Mission-origin release is
        # proven from its CANONICAL settled starts — every runtime identity
        # they own, the child records cross-checked exactly against them —
        # because the child-record proof matches only the initial task's
        # record and so cannot speak for a resumed handover, an unbound
        # record or a follow-up. Derived ONCE here and handed to the archive
        # and the close. None (legacy, non-Mission or unwired) leaves the
        # child-record path below unchanged.
        canonical = self._canonical_release_proof(entry)
        proof = self._domain_b_proof(entry) if canonical is None else None
        proof_workspace_id = (
            proof.workspace_id if proof is not None
            else canonical.workspace_id if canonical is not None else None
        )
        # THE OWNERSHIP VERDICT IS DERIVED ONCE, HERE, AND USED TWICE.
        #
        # It moved ahead of preservation deliberately. Preservation
        # READS the managed directory and copies its bytes into this
        # workflow's archive, so running it against a directory this
        # workflow has not been PROVEN to own would archive somebody
        # else's evidence under this workflow's id — and, because a
        # missing required artifact HALTS, an unowned or already-gone
        # directory would also mask the refusal that should have been
        # reported (a path mismatch, a lease already released). Only
        # an OWNED directory is read, and only an OWNED directory is
        # the one the destructive steps below will act on, which is
        # the case preservation exists to precede.
        workspace_verdict = ownership_module.owns_workspace(
            entry, recorded if recorded is not None else "",
            self.workspaces_root,
        )
        directory_present = (
            recorded is not None and os.path.isdir(recorded)
        )
        if recorded is not None and (
            workspace_verdict == ownership_module.OWNED
        ) and not directory_present:
            # ALREADY GONE. Recorded rather than skipped silently: a
            # release re-entered after the directory was removed has,
            # within this branch, no evidence left to preserve.
            # Halting here would report degradation in place of the
            # clean refusal `worker.relinquish_workspace` already gives
            # for a lease released once.
            report.record(
                "target_evidence", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                ok=True,
                detail="the managed directory is already absent, so"
                       " there is no target evidence to preserve and"
                       " nothing downstream can destroy",
            )
        elif recorded is not None and (
            workspace_verdict == ownership_module.OWNED
        ):
            # AF-3: PRODUCTION NAMES THE REQUIRED ARTIFACTS.
            #
            # The parameter existed and production supplied an empty
            # set, so within production the required-artifact half of
            # the completeness policy could fire only in a test. The
            # constant is passed HERE, at the one production seam, and
            # within this signature `preserve` has no default for it,
            # so a later caller re-opens the hole only by editing the
            # seam.
            ok, problem, detail, summary = preserve_module.preserve(
                entry, recorded, self.store.directory, self._clock(),
                workspace_id=proof_workspace_id,
                required_names=preserve_module.REQUIRED_ARTIFACTS,
            )
            report.record(
                "target_evidence", entry["workflow_id"],
                ownership_module.OWNED if ok
                else ownership_module.UNPROVABLE,
                ok=ok,
                # BOTH halves. `problem or detail` dropped the
                # detail whenever a problem code existed, which within
                # this path is every failure — so the row named the
                # policy and left the missing artifact unnamed.
                detail=("%s: %s" % (problem, detail)) if problem
                else detail,
            )
            if ok:
                entry["receipts"] = list(entry["receipts"]) + [
                    _preserve_receipt(summary, now=self._clock())
                ]
            else:
                # AC-1 / AC-3: THE CHAIN HALTS HERE.
                #
                # Preservation is a PROVEN PRECONDITION of the two
                # destructive steps that follow, not merely the step
                # before them. The previous form recorded the failure
                # and then proceeded — so a preservation failure could
                # destroy the only source of the evidence it had just
                # failed to preserve.
                #
                # Halting retains the sessions AND the directory, and
                # because the lease stays unreleased the workflow
                # remains a cleanup candidate and the next pass
                # retries from re-derived evidence. Retryable-degraded
                # is not completed.
                report.record(
                    "workspace", recorded, ownership_module.UNPROVABLE,
                    detail="the chain HALTED before the session close:"
                           " the target evidence was not preserved, so"
                           " nothing downstream may destroy it",
                )
                self.store.save(workflows)
                return BrokerOutcome(
                    True, phase=entry["phase"],
                    outcome=OUTCOME_RELEASED_DEGRADED,
                    problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                    # The report summary is COUNTS, deliberately
                    # bounded. Counts alone, within this receipt,
                    # leave an operator unable to act: "1 unprovable"
                    # does not say which required artifact is missing,
                    # and that name is the actionable content of the
                    # halt. Appended here, bounded, rather than
                    # widening the receipt line.
                    detail="%s; preservation halted: %s" % (
                        report.summary(),
                        (detail or problem or "no reason recorded")
                        [:400],
                    ),
                )
        if canonical is not None:
            sessions = self._canonical_release_sessions(entry, report, canonical)
        else:
            sessions = self._release_workspace_sessions(
                entry, report, snapshot=proof
            )
        if workspace_verdict == ownership_module.UNPROVABLE:
            # "We cannot tell" — the record carries no lease to check
            # against. # Within this branch the directory is left as it is, and the
            # release reports itself degraded rather than reporting a
            # removal it did not perform.
            report.record(
                "workspace", "<unrecorded>", ownership_module.UNPROVABLE,
                detail="no lease realpath is recorded",
            )
        else:
            # NOT_OWNED stays a REFUSAL, and deliberately so: a record
            # naming a path this workflow does not own is a corrupt or
            # hostile record, not a partial cleanup, and the existing
            # release hardening already refuses it with its own problem
            # code. Downgrading that to a degraded success would let a
            # caller that checks `ok` read a refusal as a completion —
            # which an existing guarantee test caught when an earlier
            # draft of this method did exactly that. So the call is
            # made in both cases and `worker.relinquish_workspace` decides;
            # # this layer adds a pre-check for the UNPROVABLE case it
            # could not express before, and its scope: additive only.
            if sessions != SESSIONS_RECLAIMED:
                # R-36 AA-1: THE DELETE IS CONDITIONAL, not merely
                # subsequent. Ordering two steps does not sequence
                # them if the second runs unconditionally — and this
                # one deletes the directory, so an unproven close
                # followed by a delete turns a transient unreadable
                # projection into permanent abandonment of a LIVE
                # workspace whose sessions are still running.
                #
                # Retaining also preserves CANDIDACY: the lease stays
                # unreleased, so `terminal_cleanup_candidates` returns
                # this workflow again and the next pass re-derives the
                # evidence. # Idempotency comes from proving current state at retry
                # time, rather than from a flag an attempt wrote.
                report.record(
                    "workspace", recorded,
                    ownership_module.UNPROVABLE,
                    detail="the workspace directory is RETAINED"
                           " because the session close was not"
                           " proven; this workflow remains a cleanup"
                           " candidate",
                )
            else:
                # Task 8 R20-1: the verification hold RE-ESTABLISHED AT
                # THE DESTRUCTIVE BOUNDARY (R19-2's shape: a fresh check
                # after the blocking work, immediately before the effect).
                # The hold at the top was taken before the trust
                # revocation, the preservation and the session I/O; an
                # ownership record that became unreadable or ambiguous,
                # or a group that went leaderless, meanwhile is read HERE,
                # before the lease and the directory are released — so
                # the lease, the record and the ownership records stay,
                # the effects already made are recorded truthfully, and
                # the workflow remains a cleanup candidate for a later
                # pass.
                hold = verification_release_hold(entry, self.verification_scope_base)
                if hold is not None:
                    return self._retained_at_boundary(workflows, entry, report, recorded, hold)
                # Task 8 R20-2: and EVERY other process scope of the workflow
                # (its task and pre-dispatch scopes), re-read at the same
                # boundary: a scope whose absence cannot be established keeps
                # the lease — the record's anchor — so the record is never
                # pruned and stays its recovery owner.
                hold = scope_release_hold(entry)
                if hold is not None:
                    return self._retained_at_boundary(workflows, entry, report, recorded, hold)
                # Task 8 R21-C (C-1): the sessions' ABSENCE re-established AT
                # the boundary, read-only (``_sessions_absent_now``, the
                # retry's own predicate): the close above proved absence
                # BEFORE the two blocking hold reads, and a workspace listed
                # live again, a contradictory same-lease child record or
                # evidence that stopped reading meanwhile is read HERE. The
                # close is never re-run; a refusal relinquishes nothing and
                # records the effects already made.
                hold = self._sessions_absent_now(entry)
                if hold is not None:
                    return self._retained_at_boundary(workflows, entry, report, recorded, hold)
                # Task 8 R21-1: a FRESH Mission cleanup admission AFTER every
                # boundary read and immediately before the relinquish —
                # R19-2's shape. The admission taken at action entry is stale
                # by now: a hold, an unanswering source or an outstanding
                # start that landed during those reads refuses here with ZERO
                # relinquishment, the effects already made recorded.
                hold = self._boundary_admission(entry)
                if hold is not None:
                    return self._retained_at_boundary(workflows, entry, report, recorded, hold)
                ok, problem, detail = self.worker.relinquish_workspace(
                    entry, self._clock()
                )
                report.record(
                    "workspace", recorded, workspace_verdict,
                    ok=ok, detail=problem,
                )
                if not ok and problem == workspace_module.PROBLEM_RELEASE_INCOMPLETE:
                    # Task 8 R21-3: the removal ran and its absence was not
                    # OBSERVED — the lease is kept, and the release is
                    # retried as the removal alone.
                    return self._retained_at_boundary(
                        workflows, entry, report, recorded, (problem, detail))
                if not ok:
                    return _refused(problem, detail)
                # R-54 AR-4: THE PROCESS-SCOPE RECORDS ARE RECLAIMED
                # HERE, and here is the only place they can be.
                #
                # AL-4..AL-7 decided this lifecycle and, within
                # production, no code executed it, so an assignment
                # was written before every spawn and left forever. The bound is THIS
                # WORKFLOW'S OWN terminal cleanup — not an age, not a
                # size, not a sweep — and the selection is by
                # ASSIGNMENT CREDENTIAL, so a directory whose name
                # merely parses is not reclaimed.
                #
                # ORDERING: it runs AFTER the release proved out,
                # because until then the workflow may still be
                # running and its records are what a recovery would
                # need. Within it a scope holding a corroborated live
                # group is refused and reported, so a premature
                # retire leaves the record rather than the process.
                self._retire_process_scopes(entry, report)
        # R-30 V-2: THE UNSCOPED GLOBAL SWEEP THAT WAS HERE IS
        # REMOVED.
        #
        # It called `recover_orphans` with NO BASE, so it read the
        # GLOBAL record space and would have reaped whatever it found
        # — including another workflow's helper groups — and then
        # reported them as this workflow's. That is a cross-workflow
        # reap and a false attribution at once, which is worse than
        # the unwired state it was meant to fix: # production does not REGISTER through the owned path, so
        # reading that space was unfounded in the first place.
        #
        # The rule it violated, now explicit: # NO PRODUCTION REAPING WITHOUT PRODUCTION REGISTRATION, and a
        # recovery must be scoped to the OWNING workflow rather than to
        # a shared root.
        # Terminal cleanup of what a workflow actually leaves behind
        # is a Domain B operation — the workspace and its sessions —
        # and belongs to `target_runtime.workspace_ownership`, not to
        # a sweep of local helper processes.
        entry["receipts"] = list(entry["receipts"]) + [
            _cleanup_receipt(report, now=self._clock())
        ]
        self.store.save(workflows)
        if report.degraded:
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome=OUTCOME_RELEASED_DEGRADED,
                problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                detail=report.summary(),
            )
        return BrokerOutcome(True, phase=entry["phase"])


def _cleanup_receipt(report, now, turn_id_factory=None):
    """The durable, bounded receipt for one release's cleanup (I5).

    It carries `CleanupReport.summary()`, whose degraded state is
    DERIVED from the unprovable and failed lists rather than passed in,
    so within this receipt a complete-cleanup claim over a non-empty
    remainder has no representation.
    """
    import hashlib
    import secrets
    make_turn_id = turn_id_factory or (
        lambda: "cleanup-" + secrets.token_hex(8)
    )
    summary = report.summary()
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "bounded_summary": "%s: %s" % (CLEANUP_RECEIPT_MARKER, summary),
    }


#: Close verdicts that mean THE SESSIONS ARE RECLAIMED — the only
#: states in which the managed directory may be deleted (R-36 AA-1).
#: Everything else, degraded included, retains the directory.
SESSIONS_RECLAIMED = "sessions_reclaimed"
SESSIONS_RETAINED = "sessions_retained"


def _scoped_truncation(children):
    """Why a lease-scoped spawn-record projection is incomplete, or None:
    once unrelated history no longer counts, truncation means the lease's
    OWN relevant set exceeds the listing bound — incomplete evidence that
    is never read as complete, by any route."""
    if isinstance(children, dict) and children.get("truncated") is True:
        return "the lease's child evidence is truncated (%s)" % (
            children.get("detail") or "no detail")
    return None


def _domain_b_proof(broker, entry):
    """The ONE ownership proof for this release (R-40 AD-1).

    Returns a `ProofSnapshot` on an OWNED verdict and None otherwise.
    The previous helper bound the verdict to `_verdict` and returned
    the id regardless, so preservation received an identity derived
    from a proof that had FAILED — and the close then read live state
    a SECOND time and took its own. Both defects are unwritable now:
    there is no id to obtain without a snapshot, and a snapshot exists
    only for OWNED.

    Read-only within this call: it proves and takes no action.
    """
    from target_runtime import workspace_ownership as ws_module
    if not broker.worker.observes_live_workspaces:
        return None
    try:
        live = broker.worker.live_workspaces()
        children = broker._spawn_records_raw(
            lease=ownership_module.recorded_lease_realpath(entry))
    except Exception:                                     # noqa: BLE001
        return None
    if _scoped_truncation(children) is not None:
        return None
    verdict, snapshot, _problem, _detail = ws_module.prove_ownership(
        entry, (children or {}).get("listed"), live,
        broker.workspaces_root,
    )
    return snapshot if verdict == ws_module.OWNED else None


def _domain_b_release(broker, entry, report, snapshot=None):
    """Terminal DOMAIN B cleanup: the workspace and its sessions.

    R-40 AD-3: it CONSUMES the one snapshot the release already
    proved, and re-reads live state only to REVALIDATE it immediately
    before the close (AD-4). It derives no identity of its own, which is what keeps the archive
    and the close from naming different workspaces.
    """
    from target_runtime import workspace_ownership as ws_module
    if (
        not broker.worker.observes_live_workspaces
        and not broker.worker.closes_workspaces
    ):
        # DOMAIN B IS NOT CONFIGURED FOR THIS BROKER AT ALL.
        #
        # A CONFIGURATION fact, not a degraded reading, and the
        # distinction is the point: a Broker given neither a
        # projection nor a close capability is not the actor for the
        # workspace dimension, so the directory release proceeds as it
        # did before Domain B existed. A Broker that IS configured and
        # is then unable to READ the evidence is the dangerous case,
        # and it retains.
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.NOT_OWNED,
        )
        return SESSIONS_RECLAIMED
    if not broker.worker.closes_workspaces:
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.UNPROVABLE,
            ok=False,
            detail="no workspace-close capability is wired, so the"
                   " sessions cannot be reclaimed",
        )
        return SESSIONS_RETAINED
    if snapshot is None:
        # # No proof: within this branch no workspace may be closed. Two shapes are still
        # RECLAIMED because they are positive evidence that no session
        # remains — and both are established from the proof's own
        # refusal rather than guessed at.
        return _domain_b_nothing_to_close(broker, entry, report)
    try:
        live_now = broker.worker.live_workspaces()
    except Exception as exc:                              # noqa: BLE001
        report.record(
            "workspace_session", snapshot.workspace_id,
            ownership_module.UNPROVABLE,
            detail="live state unreadable at close time (%s)"
                   % exc.__class__.__name__,
        )
        return SESSIONS_RETAINED
    try:
        children_now = broker._spawn_records_raw(
            lease=ownership_module.recorded_lease_realpath(entry))
    except Exception:                                     # noqa: BLE001
        children_now = None
    truncated = _scoped_truncation(children_now)
    if truncated is not None:
        report.record(
            "workspace_session", snapshot.workspace_id,
            ownership_module.UNPROVABLE, detail="at the close: %s" % truncated,
        )
        return SESSIONS_RETAINED
    # Task 8 (ownership correction): the FRESH Mission cleanup admission at
    # the effect boundary — after the reads above, immediately before the
    # close — for a Mission-origin record; legacy and non-Mission unchanged.
    admission_problem = _cleanup_admission_problem(broker, entry)
    if admission_problem is not None:
        report.record(
            "workspace_session", snapshot.workspace_id,
            ownership_module.UNPROVABLE, detail=admission_problem,
        )
        return SESSIONS_RETAINED
    closed, workspace_id, problem, detail = (
        ws_module.close_proven_workspace(
            snapshot, live_now, broker.worker.close_workspace,
            child_records=(children_now or {}).get("listed"),
            entry=entry, workspaces_root=broker.workspaces_root,
        )
    )
    if closed:
        # A close that RETURNED is not absence: the sessions count as
        # reclaimed — and the directory and lease become releasable — only
        # once a complete fresh listing no longer shows the workspace.
        try:
            after = broker.worker.live_workspaces()
        except Exception as exc:                          # noqa: BLE001
            after, unreadable = None, exc.__class__.__name__
        else:
            unreadable = live_listing_problem(after)
        if unreadable is not None:
            report.record(
                "workspace_session", workspace_id, ownership_module.UNPROVABLE,
                detail="closed, but absence is not observable (%s)" % unreadable,
            )
            return SESSIONS_RETAINED
        if any(w.get("workspace_id") == workspace_id for w in after):
            report.record(
                "workspace_session", workspace_id, ownership_module.UNPROVABLE,
                detail="closed, but still listed; absence is not observed",
            )
            return SESSIONS_RETAINED
        report.record("workspace_session", workspace_id,
                      ownership_module.OWNED)
        return SESSIONS_RECLAIMED
    report.record(
        "workspace_session", workspace_id or entry["workflow_id"],
        ownership_module.UNPROVABLE,
        detail="%s: %s" % (problem, detail),
    )
    return SESSIONS_RETAINED


def _domain_b_nothing_to_close(broker, entry, report):
    """The two POSITIVE-evidence cases in which no session remains.

    Reached only when the proof produced no snapshot. Everything else
    RETAINS, because a transient unreadable projection must not become
    permanent abandonment of a live workspace.
    """
    from target_runtime import workspace_ownership as ws_module
    try:
        live = broker.worker.live_workspaces()
        children = broker._spawn_records_raw(
            lease=ownership_module.recorded_lease_realpath(entry))
    except Exception as exc:                              # noqa: BLE001
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.UNPROVABLE,
            detail="workspace evidence unreadable (%s)"
                   % exc.__class__.__name__,
        )
        return SESSIONS_RETAINED
    truncated = _scoped_truncation(children)
    if truncated is not None:
        # An incomplete relevant set can neither prove a session absent nor
        # establish that no record exists: nothing is reclaimed.
        report.record("workspace_session", entry["workflow_id"],
                      ownership_module.UNPROVABLE, detail=truncated)
        return SESSIONS_RETAINED
    _verdict, _snapshot, problem, detail = ws_module.prove_ownership(
        entry, (children or {}).get("listed"), live,
        broker.workspaces_root,
    )
    report.record(
        "workspace_session", entry["workflow_id"],
        ownership_module.UNPROVABLE,
        detail="%s: %s" % (problem, detail),
    )
    if problem == ws_module.PROBLEM_WORKSPACE_NOT_FOUND:
        return SESSIONS_RECLAIMED
    if (
        problem == ws_module.PROBLEM_NO_CHILD_RECORD
        and ownership_module.recorded_task_id(entry) is None
    ):
        return SESSIONS_RECLAIMED
    return SESSIONS_RETAINED


def _resumed_handover(entry, ordinal):
    """Whether dispatch ``ordinal`` handed its objective over as a RESUMED
    task handover (R1): its task claim was durably refused and later
    admitted. That handover (``production_task_handover``) writes no
    control-repository child record by construction; a SPAWN
    (``production_spawn`` → ``spawn_child``) always appends one naming the
    task and runtime it returned."""
    claims = [state for state, _cause in _claim_history(
        entry, claim_head(dispatch_module.START_POINT_TASK, ordinal))]
    return (START_STATE_CLAIM_REFUSED in claims
            and START_STATE_CLAIM_ADMITTED in claims[claims.index(START_STATE_CLAIM_REFUSED):])


def _canonical_binding(broker, entry):
    """The runtime workspaces THIS workflow's canonical settled starts own,
    and the task each handed over: ``({workspace_id: frozenset(agent
    names)}, current_workspace_id, frozenset((ordinal, task_id,
    workspace_id, frozenset(agent names))), None, None)`` or ``(None, None,
    None, problem, detail)``. The task identities are exactly what the
    engine RETURNED and the start's owner settled — a follow-up's task id is
    its own (the engine mints one per hand-over), never the record's
    initial binding.

    Every canonical start of the workflow is bound — none is picked — to the
    exact workflow (the source's own filter), dispatch ordinal (1..n, n the
    record's markers), this Runtime's owner reference for that ordinal, and
    the record's authorization; ordinal n's to the record's engagement.
    Per ordinal: at most one runtime and one task start, of one engagement.
    A runtime start settled ``completed`` contributes its execution identity
    (workspace id + exact agent set); its task start, when completed, must
    name the SAME runtime, and ordinal 1's must carry the record's bound
    task id — or, with NO task bound yet (a runtime started before any task
    was handed over or bound; only the initial dispatch can exist), it binds
    no task: ordinal 1 may hold no task start or only one that did not
    complete (a completed one is a binding the record has not settled yet,
    and nothing is derived); no canonical start at all is then positive
    evidence that nothing was started. A start
    settled otherwise must be stop-CONFIRMED (observed absent). One
    workspace id reused across ordinals must carry one agent set; it is one
    identity. Anything else is a conflict and nothing is derived."""
    gate = broker.mission_gate
    starts = gate.engagement_starts(entry)
    if starts is None:
        return None, None, None, mission_gate_module.PROBLEM_SOURCE_UNAVAILABLE, (
            "the Mission source cannot answer for the engagement starts")
    linkage = entry[record_module.MISSION_AUTHORITY_KEY]
    reference = entry.get(record_module.MISSION_ENGAGEMENT_KEY) or {}
    sequence = dispatch_module.dispatch_count(entry)
    bound = ownership_module.recorded_task_id(entry)
    if ownership_module.recorded_lease_realpath(entry) is None:
        return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
            "the record binds no lease")
    if bound is None and sequence > 1:
        return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
            "the record binds no task yet holds %d dispatches" % sequence)
    ordinals = {}
    for start in starts:
        ordinal = start["engagement_sequence"]
        if not (isinstance(ordinal, int) and 1 <= ordinal <= sequence):
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "start %s is of ordinal %r, outside the record's %d dispatches"
                % (start["start_id"], ordinal, sequence))
        if start["owner_ref"] != gate.owner_ref(entry, ordinal):
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "start %s belongs to another owner" % start["start_id"])
        if (start["authorization_id"], start["authorization_digest_sha256"]) != (
            linkage["authorization_id"], linkage["authorization_digest_sha256"]
        ):
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "start %s is under another authorization" % start["start_id"])
        if ordinal == sequence and start["engagement_id"] != reference.get("engagement_id"):
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "start %s is not of the record's engagement" % start["start_id"])
        ordinals.setdefault(ordinal, []).append(start)
    identities, current, tasks = {}, None, set()
    for ordinal in sorted(ordinals):
        runtime = [s for s in ordinals[ordinal] if s["point"] == "runtime"]
        task = [s for s in ordinals[ordinal] if s["point"] == "task"]
        if len(runtime) > 1 or len(task) > 1 or len(set(
                s["engagement_id"] for s in ordinals[ordinal])) != 1:
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "ordinal %d holds %d runtime and %d task starts over %d"
                " engagements; exactly one of each, of one engagement, binds"
                % (ordinal, len(runtime), len(task),
                   len(set(s["engagement_id"] for s in ordinals[ordinal]))))
        if not runtime:
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "ordinal %d holds a task start without a runtime start" % ordinal)
        settlement = runtime[0]["settlement"]
        if settlement is None or (task and task[0]["settlement"] is None):
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "a start of ordinal %d is unsettled" % ordinal)
        if settlement["outcome"] != mission_gate_module.START_OUTCOME_COMPLETED:
            if not mission_gate_module.start_stop_confirmed(runtime[0]):
                return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                    "the runtime start of ordinal %d settled %s without an"
                    " observed-absent stop" % (ordinal, settlement["outcome"]))
            continue
        identity = settlement.get("identity") or {}
        workspace_id = identity.get("workspace_id")
        agents = frozenset(identity.get("agent_names") or [])
        if not isinstance(workspace_id, str) or not workspace_id or not agents:
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "the runtime start of ordinal %d carries no workspace identity"
                % ordinal)
        if task:
            task_settlement = task[0]["settlement"]
            task_identity = task_settlement.get("identity") or {}
            if task_settlement["outcome"] == mission_gate_module.START_OUTCOME_COMPLETED:
                if (task_identity.get("workspace_id"),
                        frozenset(task_identity.get("agent_names") or [])) != (
                            workspace_id, agents):
                    return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                        "the task start of ordinal %d names another runtime than"
                        " its runtime start" % ordinal)
                if ordinal == 1 and bound is None:
                    return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                        "the task start of ordinal 1 settled task %r, which the"
                        " record has not bound; the binding is not settled"
                        % (task_identity.get("task_id"),))
                if ordinal == 1 and task_identity.get("task_id") != bound:
                    return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                        "the task start of ordinal 1 settled task %r, not the"
                        " record's bound %r" % (task_identity.get("task_id"), bound))
                task_id = task_identity.get("task_id")
                if not isinstance(task_id, str) or not task_id.strip():
                    return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                        "the task start of ordinal %d settled no usable task id"
                        % ordinal)
                tasks.add((ordinal, task_id, workspace_id, agents))
            elif not mission_gate_module.start_stop_confirmed(task[0]):
                return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                    "the task start of ordinal %d settled %s without an"
                    " observed-absent stop" % (ordinal, task_settlement["outcome"]))
        elif ordinal == 1 and bound is not None:
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "ordinal 1 holds no task start although the record binds task %r"
                % bound)
        if identities.get(workspace_id, agents) != agents:
            return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
                "workspace %s is named with two agent sets across ordinals"
                % workspace_id)
        identities[workspace_id] = agents
        current = workspace_id
    if bound is not None and 1 not in ordinals:
        return None, None, None, PROBLEM_RELEASE_BINDING_UNPROVEN, (
            "the initial dispatch holds no canonical start")
    return identities, current, frozenset(tasks), None, None


class _CanonicalReleaseProof(object):
    """The ONE canonical proof of a release (derived once, before the
    preservation, consumed by the close): the workspace identities the
    binding owns, the snapshot of each that was live and OWNED, and the ones
    already ABSENT from a complete listing — or why nothing is proven."""

    __slots__ = ("identities", "tasks", "snapshots", "absent", "workspace_id",
                 "problem", "detail")

    def __init__(self, identities=None, tasks=None, snapshots=None, absent=None,
                 workspace_id=None, problem=None, detail=None):
        self.identities = dict(identities or {})
        self.tasks = frozenset(tasks or ())
        self.snapshots = dict(snapshots or {})
        self.absent = set(absent or ())
        self.workspace_id = workspace_id
        self.problem = problem
        self.detail = detail


def _child_record_identity(record):
    """``(task_id, workspace_id, frozenset(agent names))`` a child record
    states EXACTLY, or None when any part is absent or unusable."""
    task_id, workspace_id = record.get("task_id"), record.get("workspace_id")
    agents = record.get("agents")
    if not (isinstance(task_id, str) and task_id and isinstance(workspace_id, str)
            and workspace_id and isinstance(agents, dict) and agents):
        return None
    names = list(agents.values())
    if not all(isinstance(name, str) and name for name in names):
        return None
    return task_id, workspace_id, frozenset(names)


def _canonical_child_evidence_problem(broker, entry, children, tasks):
    """None when the control-side child evidence of this workflow's lease is
    cleanly readable and EXACTLY the history its canonical starts establish,
    else why not. Evaluated on the proof's reading AND again on a fresh
    reading at the close, so child evidence that appears, truncates, degrades
    or changes in between is never overridden.

    ``tasks`` is the binding's settled task identities (ordinal, task id,
    workspace id, agent set) — exactly what the engine returned per hand-over
    (a follow-up's task id is its own). Every child record naming the lease
    must state one of them EXACTLY (task, workspace AND agents), each at most
    as often as the canonical history holds it: legitimate history is only
    what the canonical starts establish, and a record that differs, is
    duplicated or is unusable is unexplained — it retains. And every
    hand-over made by a SPAWN (not a resumed task handover, which writes
    none) must have its record: truly absent child evidence of a spawn is
    never read as clean either."""
    if not isinstance(children, dict) or children.get("truncated") is not False or (
        children.get("state") not in ("available", "empty")
    ):
        return ("the control-side child evidence is not cleanly readable, so its"
                " absence cannot be established (%s)" % (
                    "unreadable" if not isinstance(children, dict) else
                    "state %s, truncated %s: %s" % (
                        children.get("state"), children.get("truncated"),
                        children.get("detail") or "no detail")))
    lease = ownership_module.recorded_lease_realpath(entry)
    named = [record for record in children.get("listed") or []
             if isinstance(record, dict) and isinstance(record.get("repo"), str)
             and os.path.realpath(record["repo"]) == os.path.realpath(lease)]
    held = collections.Counter((task_id, workspace_id, agents)
                               for _ordinal, task_id, workspace_id, agents in tasks)
    written = collections.Counter(
        (task_id, workspace_id, agents) for ordinal, task_id, workspace_id, agents in tasks
        if not _resumed_handover(entry, ordinal))
    stated = collections.Counter(_child_record_identity(record) for record in named)
    unexplained = sorted(
        (repr(record.get("task_id")), repr(record.get("workspace_id")))
        for record in named
        if stated[_child_record_identity(record)] > held.get(
            _child_record_identity(record), 0))
    if unexplained:
        return ("child evidence names this workflow's lease with %d record(s) no"
                " canonical start of it establishes exactly (task, workspace: %s);"
                " it is never overridden" % (len(unexplained), ", ".join(
                    "%s %s" % pair for pair in unexplained)))
    missing = sorted(identity[0] for identity, count in written.items()
                     if stated[identity] < count)
    if missing:
        return ("the child record a spawn of this workflow writes is absent (task(s)"
                " %s); absent child evidence is never read as clean"
                % ", ".join(repr(task_id) for task_id in missing))
    return None


def _retirement_child_problem(broker, entry, children, tasks):
    """The OWN child-evidence rule (``_canonical_child_evidence_problem``)
    for a follow-up's retirement of an earlier runtime — every same-lease
    record stated EXACTLY by this workflow's canonical history, every spawn's
    record present, the evidence cleanly readable — as ``(terminal, detail)``
    or None. A read that failed or a source that is unavailable is
    recoverable; truncated, malformed, unexplained or missing evidence is a
    contradiction. Not ``close_proven_workspace``'s own child binding: that
    matches only the INITIAL bound task's record, so it could never speak for
    a follow-up's runtime."""
    problem = _canonical_child_evidence_problem(broker, entry, children, tasks)
    if problem is None:
        return None
    transient = not isinstance(children, dict) or children.get("state") == "unavailable"
    return (not transient, "child evidence: %s" % problem)


def _verification_attempts(entry):
    """Every durable verification attempt record of this workflow, read
    back by the RECORD LAYER (``record.verification_attempts``, R20-1 — the
    one reader the barrier, the release, cleanup eligibility and pruning
    share): ``(attempts, undecodable)``, an undecodable record never read as
    "no attempt"."""
    return record_module.verification_attempts(entry)


def verification_release_hold(entry, scope_base=None):
    """Task 8 R20-1 — whether ``entry``'s VERIFICATION evidence still needs
    its lease, its workflow record and its ownership records: the ONE
    read-only check cleanup eligibility (``runtime
    .terminal_cleanup_candidates``) and the release (``_release``, BEFORE
    anything is revoked, closed, deleted or released, and AGAIN at its
    destructive boundary, immediately before the lease and the directory
    are released) consult, so an
    unresolved verification is carried through both — and, because a
    retained release keeps the lease, through pruning
    (``record.verification_evidence_outstanding``) and scope retirement
    too. Returns None when ABSENCE is established, else ``(problem,
    detail)`` with ``PROBLEM_VERIFICATION_RETAINED``:

    - attempt records that do not decode — ambiguity is not settlement;
    - attempt records on a record that names no control repository — its
      verification scope cannot be found, so its absence cannot be
      established;
    - the verification scope's ownership, read STRICTLY by the R19-3
      reader (``verification.prior_ownership``), is not clear: a process of
      an attempt may be alive — corroborated as ours, alive with its
      recorded leader gone (surviving descendants), or a root never
      stamped — or the evidence cannot be read (an unreadable record is
      not an absent one). A scope holding a live verification group with
      NO claim (one started before R19) retains the workflow too.

    An attempt merely UNSETTLED is not a hold here: once absence is
    established the release settles it (``_verification_barrier``'s
    settlement, the same receipts) before it destroys anything. Reads
    only: nothing is written, signalled or removed."""
    from target_runtime import verification as verification_module
    attempts, undecodable = _verification_attempts(entry)
    if undecodable:
        return PROBLEM_VERIFICATION_RETAINED, (
            "%d verification attempt record(s) do not decode, so no attempt can be read"
            " as settled; nothing is released" % undecodable)
    control = (entry.get("control_identity") or {}).get("repository_realpath")
    if not isinstance(control, str) or not control:
        if attempts:
            return PROBLEM_VERIFICATION_RETAINED, (
                "the record carries verification attempt(s) and names no control"
                " repository, so its verification scope cannot be examined; nothing is"
                " released")
        return None
    state, detail = verification_module.prior_ownership(
        entry["workflow_id"], control, scope_base=scope_base)
    if state != verification_module.PRIOR_CLEAR:
        return PROBLEM_VERIFICATION_RETAINED, (
            "the verification scope's ownership is %s: %s; nothing is released"
            % (state, detail))
    return None


def scope_release_hold(entry):
    """Task 8 R20-2 — whether a process scope of ``entry``'s workflow OTHER
    than its verification scope (which ``verification_release_hold`` reads
    with the R19-3 reader) still needs the lease, the workflow record and
    its ownership records: the read-only check cleanup eligibility and the
    release (at its top AND at its destructive boundary) consult beside the
    verification hold. The rule is retirement's own
    (``process_ownership.owned_scope_refusals`` → ``retirement_refusal``), so
    the release releases the lease only when every scope it is about to
    retire can be retired — a refused retirement after the lease is gone
    is what left a scope with no owner. Returns None when absence is
    ESTABLISHED for every such scope, else ``(PROBLEM_PROCESS_SCOPE_RETAINED,
    detail)``:

    - a scope holds a live group of ours, a live group whose leader is gone
      (surviving descendants — never signalled), an unstamped root, or
      records that cannot be read;
    - a directory whose name claims this workflow carries no valid
      assignment — ambiguous ownership;
    - an entry whose name claims this workflow is not a directory (a
      symbolic link, even to a live scope; a file, FIFO, socket or device);
    - the scopes cannot be enumerated at all.

    A record naming no control repository owns no scope (every assignment
    is written under the record's control repository): None. Reads only."""
    from target_runtime import process_ownership as _own
    from target_runtime.verification import VERIFICATION_OWNER_UNIT
    control = (entry.get("control_identity") or {}).get("repository_realpath")
    if not isinstance(control, str) or not control:
        return None
    refusals, problem = _own.owned_scope_refusals(
        control, entry["workflow_id"], skip_units=(VERIFICATION_OWNER_UNIT,))
    if problem is not None:
        return PROBLEM_PROCESS_SCOPE_RETAINED, (
            "this workflow's process scopes cannot be enumerated (%s); nothing is"
            " released" % problem)
    if refusals:
        directory, reason = refusals[0]
        return PROBLEM_PROCESS_SCOPE_RETAINED, (
            "%d process scope(s) of this workflow cannot be retired — scope %s: %s;"
            " nothing is released" % (
                len(refusals), os.path.basename(directory.rstrip(os.sep)), reason))
    return None


def _retirement_close_claims(entry):
    """Every durable retirement close record of this workflow, read back
    LOSSLESSLY: ``(claims, returned, undecodable)`` — ``claims`` and
    ``returned`` map a workspace id (decoded exactly from its JSON form) to
    the follow-up dispatches that claimed its close / saw the engine call
    return; ``undecodable`` counts close records whose dispatch or id does
    not decode, which a caller must never read as "no claim"."""
    head = RETIREMENT_RECEIPT_MARKER + ": dispatch "
    decoder = json.JSONDecoder()
    claims, returned, undecodable = {}, {}, 0
    for receipt in entry.get("receipts") or []:
        summary = receipt.get("bounded_summary") if isinstance(receipt, dict) else None
        if not isinstance(summary, str) or not summary.startswith(head):
            continue
        number, _, rest = summary[len(head):].partition(" ")
        verb, _, rest = rest.partition(" ")
        if verb not in (RETIREMENT_CLOSE_CLAIMED, RETIREMENT_CLOSE_RETURNED):
            continue
        try:
            workspace_id, _end = decoder.raw_decode(rest)
        except ValueError:
            workspace_id = None
        if not (number and all(c in "0123456789" for c in number)
                and isinstance(workspace_id, str) and workspace_id):
            undecodable += 1
            continue
        target = claims if verb == RETIREMENT_CLOSE_CLAIMED else returned
        target.setdefault(workspace_id, set()).add(int(number))
    return claims, returned, undecodable


def _retirement_claim_on(entry, read_starts, start, workspace_id):
    """Whether a follow-up's retirement CLAIMED the close of the incarnation
    of ``workspace_id`` that ``start`` belongs to: ``(follow-up, returned)``,
    ``(reason, False)`` when that cannot be read (a close record that does
    not decode, or no canonical starts to place the incarnation) — both mean
    the close is never re-issued — or None. The incarnation is the one the
    latest completed canonical runtime start of ordinal <= the start's
    established; a claim by follow-up k retires the incarnation established
    by the latest ordinal < k, so it names this one exactly when it falls
    after it and no later than the next start re-establishing the id. The
    canonical starts are read (``read_starts()``) only when a claim of this
    id exists."""
    if not isinstance(workspace_id, str) or not workspace_id:
        return None
    claims, returned, undecodable = _retirement_close_claims(entry)
    if undecodable:
        return ("do not decode (%d)" % undecodable, False)
    if workspace_id not in claims:
        return None
    starts = read_starts()
    if starts is None:
        return ("cannot be placed: the Mission source cannot answer for the starts", False)
    establishing = sorted(
        s["engagement_sequence"] for s in starts
        if s.get("point") == "runtime"
        and (s.get("settlement") or {}).get("outcome")
        == mission_gate_module.START_OUTCOME_COMPLETED
        and ((s["settlement"].get("identity") or {}).get("workspace_id")) == workspace_id)
    earlier = [o for o in establishing if o <= start["engagement_sequence"]]
    if not earlier:
        return None
    established = max(earlier)
    later = [o for o in establishing if o > established]
    ceiling = min(later) if later else None
    for follow_up in sorted(claims[workspace_id]):
        if follow_up > established and (ceiling is None or follow_up <= ceiling):
            return (follow_up, follow_up in returned.get(workspace_id, set()))
    return None


def _retired_identities(broker, entry, claims):
    """The workspace ids whose close a follow-up's retirement CLAIMED and that
    no later canonical runtime start re-established: ``{workspace_id: the
    latest claiming follow-up}``, or None when the Mission source cannot
    answer. A claim by follow-up k retires the incarnations established
    before k (its retirement precedes k's own start); a completed runtime
    start of ordinal >= k naming the same id is a NEW incarnation — a fact
    of the canonical history, not of the listing. Such an identity is never
    closed again, by a later retirement or by the release."""
    if not claims:
        return {}
    starts = broker.mission_gate.engagement_starts(entry)
    if starts is None:
        return None
    established = {}
    for start in starts:
        settlement = start.get("settlement") or {}
        if (start.get("point") != "runtime"
                or settlement.get("outcome") != mission_gate_module.START_OUTCOME_COMPLETED):
            continue
        workspace_id = (settlement.get("identity") or {}).get("workspace_id")
        if isinstance(workspace_id, str):
            established[workspace_id] = max(established.get(workspace_id, 0),
                                            start["engagement_sequence"])
    return dict((workspace_id, max(sequences)) for workspace_id, sequences in claims.items()
                if max(sequences) > established.get(workspace_id, 0))


def _cleanup_admission_problem(broker, entry, where="at the close"):
    """The FRESH Mission cleanup admission at the effect boundary — after the
    potentially blocking evidence reads, immediately before a close
    (``where``; a process scope's removal now HOLDS its admission instead,
    ``_cleanup_admission_held``, Task 8 R25-1) — so a
    hold, a Mission source that stopped answering, or an outstanding start
    arriving after the preservation retains. A lock-free snapshot: no Mission
    lock is held across the engine call that follows. None when admitted, and
    for a record that is not Mission-origin (legacy and non-Mission releases
    are unchanged)."""
    if broker.mission_gate is None or not record_module.is_mission_core_kind(entry):
        return None
    admission = broker.mission_gate.admit_cleanup(entry)
    if admission.ok:
        return None
    return ("the cleanup admission refused %s (%s: %s)"
            % (where, admission.problem, admission.detail))


def _cleanup_admission_held(broker, entry, where, effect):
    """Task 8 R25-1: the FRESH Mission cleanup admission, HELD across
    ``effect`` (``MissionEffectGate.admit_cleanup_held``) — the retirement's
    ``admit``. Returns ``(refusal, result)``: the refusal's text (worded as
    ``_cleanup_admission_problem``'s) with ``effect`` NOT run, or None and
    the effect's result. A record that is not Mission-origin runs ``effect``
    directly (legacy and non-Mission releases are unchanged)."""
    if broker.mission_gate is None or not record_module.is_mission_core_kind(entry):
        return None, effect()
    admission, result = broker.mission_gate.admit_cleanup_held(entry, effect)
    if admission.ok:
        return None, result
    return ("the cleanup admission refused %s (%s: %s)"
            % (where, admission.problem, admission.detail)), None


def _canonical_release_proof(broker, entry):
    """Task 8 (ownership correction): the ownership proof of a Mission-origin
    release from the workflow's CANONICAL settled starts — EVERY runtime
    identity they own (``prove_started_runtime`` over each, against one
    complete fresh listing, under the recorded lease and bound task), with
    the control-side child records cross-checked EXACTLY against the history
    those starts establish (``_canonical_child_evidence_problem``).

    The child-record proof alone cannot answer for a Mission workflow: it
    matches only the record of the INITIAL bound task, so it can never speak
    for a RESUMED task handover (which writes no record: cause 2), for a
    record with NO task bound (cause 1), or for a FOLLOW-UP — whose spawn
    appends a record under its own, newly minted task id and whose runtime
    start replaces the previous harness workspace with a new one, so the
    initial record's workspace can be gone while the follow-up's is live.

    None for a record that is not Mission-origin, or when no live
    observation AND close capability is wired: the existing child-record
    path then decides, unchanged. Otherwise it is the answer: any conflict,
    unreadable source or listing, child evidence that is degraded,
    unexplained or missing, or an identity neither owned nor absent is a
    proof with a ``problem`` — which retains. Missing child evidence is
    never read as "not ours"."""
    from target_runtime import workspace_ownership as ws_module
    if not record_module.is_mission_core_kind(entry) or broker.mission_gate is None:
        return None
    if not (broker.worker.observes_live_workspaces and broker.worker.closes_workspaces):
        return None
    bound = ownership_module.recorded_task_id(entry)
    identities, current, tasks, problem, detail = _canonical_binding(broker, entry)
    if identities is None:
        return _CanonicalReleaseProof(problem=problem, detail=detail)
    try:
        children = broker._spawn_records_raw(
            lease=ownership_module.recorded_lease_realpath(entry))
        live = bounded_engine_call(broker.worker.live_workspaces,
                                   OWNED_STOP_WAIT_SECONDS)
    except Exception as exc:                              # noqa: BLE001
        return _CanonicalReleaseProof(
            problem=PROBLEM_RELEASE_BINDING_UNPROVEN,
            detail="the workspace evidence is unreadable (%s)" % exc.__class__.__name__)
    malformed = live_listing_problem(live)
    if malformed is not None:
        return _CanonicalReleaseProof(
            problem=PROBLEM_RELEASE_BINDING_UNPROVEN,
            detail="the live listing is unavailable or malformed (%s)" % malformed)
    child_problem = _canonical_child_evidence_problem(broker, entry, children, tasks)
    if child_problem is not None:
        return _CanonicalReleaseProof(problem=PROBLEM_RELEASE_BINDING_UNPROVEN,
                                      detail=child_problem)
    lease = ownership_module.recorded_lease_realpath(entry)
    snapshots, absent = {}, set()
    for workspace_id, agents in sorted(identities.items()):
        verdict, snapshot, why, detail = ws_module.prove_started_runtime(
            {"workspace_id": workspace_id, "agent_names": sorted(agents),
             "task_id": bound}, live, lease)
        if snapshot is not None:
            snapshots[workspace_id] = snapshot
        elif why == ws_module.PROBLEM_WORKSPACE_NOT_FOUND:
            absent.add(workspace_id)
        else:
            return _CanonicalReleaseProof(
                problem=PROBLEM_RELEASE_BINDING_UNPROVEN,
                detail="workspace %s is %s (%s: %s)" % (workspace_id, verdict, why,
                                                         detail))
    # Task 8 startup correction: at most one claimed close per proven identity
    # over the WHOLE workflow — an identity whose close a follow-up's
    # retirement claimed (and no later canonical start re-established) is
    # never closed again by the release; still listed, it is uncertain.
    claims, _returned, undecodable = _retirement_close_claims(entry)
    retired = {} if undecodable else _retired_identities(broker, entry, claims)
    if undecodable or retired is None:
        return _CanonicalReleaseProof(
            problem=PROBLEM_RELEASE_BINDING_UNPROVEN,
            detail="the retirement close claims are %s" % (
                "undecodable (%d)" % undecodable if undecodable
                else "not answerable (the Mission source is unavailable)"))
    for workspace_id in sorted(snapshots):
        if workspace_id in retired:
            return _CanonicalReleaseProof(
                problem=PROBLEM_PREDECESSOR_UNCERTAIN,
                detail="workspace %s is still listed after follow-up %d's retirement"
                       " claimed its close; that close is never re-issued"
                       % (workspace_id, retired[workspace_id]))
    return _CanonicalReleaseProof(
        identities=identities, tasks=tasks, snapshots=snapshots, absent=absent,
        workspace_id=current if current in snapshots else None)


def _canonical_release_sessions(broker, entry, report, proof):
    """Close exactly the workspaces the ONE canonical proof owns, each at most
    once, and report RECLAIMED only when every one is OBSERVED ABSENT.

    Per identity, after its potentially blocking reads (one complete fresh
    listing and a fresh child-evidence reading) and immediately before its
    close: the FULL binding re-derived from fresh canonical reads and
    required EQUAL to the one proven — runtime identities AND settled task
    identities (a changed owner, ordinal, authorization or identity
    retains), the fresh child evidence still cleanly readable and exactly
    the history that binding establishes (child evidence that appeared,
    truncated, degraded or changed since the proof retains — it is never
    overridden), a FRESH cleanup
    admission at the effect boundary (a hold, an unanswering source or an
    outstanding start arriving after the preservation retains), and
    ``close_proven_workspace`` over the proof's own snapshot with the record
    (workspace, agent set, lease and bound task revalidated). After the
    close, a further complete listing must show the workspace ABSENT — a
    close that returned is not absence. A workspace already absent is
    nothing to close. Anything else RETAINS the directory and candidacy."""
    from target_runtime import workspace_ownership as ws_module
    if proof.problem is not None:
        report.record("workspace_session", entry["workflow_id"],
                      ownership_module.UNPROVABLE,
                      detail="%s: %s" % (proof.problem, proof.detail))
        return SESSIONS_RETAINED

    def retained(workspace_id, detail):
        report.record("workspace_session", workspace_id, ownership_module.UNPROVABLE,
                      detail=detail)
        return SESSIONS_RETAINED

    def listing():
        live = bounded_engine_call(broker.worker.live_workspaces,
                                   OWNED_STOP_WAIT_SECONDS)
        malformed = live_listing_problem(live)
        if malformed is not None:
            raise ValueError(malformed)
        return live

    if not proof.identities:
        # Positive canonical evidence: no start of this workflow holds a
        # live runtime identity (none started, or each stop observed absent).
        report.record("workspace_session", entry["workflow_id"], ownership_module.OWNED,
                      detail="no canonical start of this workflow holds a live runtime;"
                             " nothing to close")
    for workspace_id in sorted(proof.identities):
        # The potentially blocking evidence reads come FIRST; every
        # revalidation of what they could have let change follows them.
        try:
            live_now = listing()
        except Exception as exc:                          # noqa: BLE001
            return retained(workspace_id, "live listing unreadable at the close (%s)"
                            % exc)
        children_now = broker._spawn_records_raw(
            lease=ownership_module.recorded_lease_realpath(entry))
        identities, _current, tasks, problem, detail = _canonical_binding(broker, entry)
        if (identities, tasks) != (proof.identities, proof.tasks):
            return retained(workspace_id, "the canonical binding changed since the proof"
                            " (%s)" % ("%s: %s" % (problem, detail) if problem
                                       else "other identities"))
        child_problem = _canonical_child_evidence_problem(
            broker, entry, children_now, tasks)
        if child_problem is not None:
            return retained(workspace_id, "at the close: %s" % child_problem)
        admission_problem = _cleanup_admission_problem(broker, entry)
        if admission_problem is not None:
            return retained(workspace_id, admission_problem)
        present = [w for w in live_now if w.get("workspace_id") == workspace_id]
        if workspace_id in proof.absent or not present:
            if present:
                return retained(workspace_id, "absent at the proof, listed again at"
                                " the close; not proven")
            report.record("workspace_session", workspace_id, ownership_module.OWNED,
                          detail="absent from a complete fresh listing; nothing to close")
            continue
        try:
            closed, _closed_id, why, detail = ws_module.close_proven_workspace(
                proof.snapshots[workspace_id], live_now,
                broker._bounded_close(broker.worker.close_workspace),
                entry=entry)
        except Exception as exc:                          # noqa: BLE001
            return retained(workspace_id, "the owned close raised %s (%s)"
                            % (exc.__class__.__name__, exc))
        if not closed:
            return retained(workspace_id, "%s: %s" % (why, detail))
        try:
            after = listing()
        except Exception as exc:                          # noqa: BLE001
            return retained(workspace_id, "closed, but absence is unobservable (%s)"
                            % exc)
        if any(w.get("workspace_id") == workspace_id for w in after):
            return retained(workspace_id, "closed, but still listed; absence is not"
                            " observed")
        report.record("workspace_session", workspace_id, ownership_module.OWNED,
                      detail="closed and absent from a complete fresh listing")
    return SESSIONS_RECLAIMED


TargetBroker._release_workspace_sessions = _domain_b_release
TargetBroker._domain_b_proof = _domain_b_proof
TargetBroker._canonical_release_proof = _canonical_release_proof
TargetBroker._canonical_release_sessions = _canonical_release_sessions


def _preserve_receipt(summary, now, turn_id_factory=None):
    """The durable receipt for one evidence preservation (AB-1/AB-3).

    Carries the projection's own summary, including its TRUNCATION
    disclosure when the listing was capped, so a reader of the record
    can tell a complete archive from a partial one without opening the
    projection.
    """
    import hashlib
    import secrets
    make_turn_id = turn_id_factory or (
        lambda: "preserve-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "bounded_summary": summary[:400],
    }
