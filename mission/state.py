"""Mission State record shapes: the Task 5 durable state of one Mission.

One closed state record per Mission, keyed by ``mission_id`` under the
store's ``mission_state`` map. It holds the contract activation
BINDINGS (never a copy of the contract), claims, evidence with separate
submission and acceptance events, artifact metadata, blockers,
dependencies, resource readiness observations, checkpoints,
continuation attempts, the closure, and the append-only list of
applied state operations. Every field is validated closed, every bound
is a module constant never derived from input, and every refusal has
its own distinct ``mission_state_*`` problem code.

Contract binding, not contract copy (R-5.4). An activation records the
revision, that revision's ``proposal_digest_sha256``, the authorization
it was activated under and that authorization's digest, and the
``contract_digest_sha256`` of the proof contract INSIDE that revision's
approved proposal. The contract content is re-derived from the Mission
record on every read; nothing here can hold an independently mutable
copy of a requirement, a budget, a staleness bound or a degradation
permission. Records that depend on the contract (claims, evidence,
blockers, dependencies, checkpoints) bind the activation that was
current when they were recorded; a stale activation refuses new
records at the service boundary and is visible here as a binding to an
activation whose revision is no longer the Mission's current revision.

A SEPARATE, descriptive progress lifecycle (R-2). ``progress`` is the
Task 5 vocabulary ``NOT_STARTED`` / ``IN_PROGRESS`` / ``BLOCKED`` /
``COMPLETED`` / ``CLOSED_UNSUCCESSFUL`` / ``ABANDONED`` with its own
module-constant transition table ``PROGRESS_TRANSITIONS``. It never
touches Task 4's ``mission["state"]`` (``AWAITING_DECISION`` /
``AUTHORIZED`` / ``DENIED``), which is driven only by human decisions
and whose replay reconciliation is unchanged. Task 4's declared but
unwired Mission states ``RUNNING``, ``BLOCKED``, ``COMPLETED``,
``CLOSED`` and ``CANCELLED`` REMAIN unwired: nothing here assigns them,
and nothing here routes, dispatches, runs, cancels or revokes anything.
This is a descriptive progress lifecycle, not execution. The three
terminal outcomes are terminal: re-opening refuses.

Sequence and operation binding (R-6, R-7, R-11). ``sequence`` is the
number of applied operations; every accepted mutation appends exactly
one applied operation at ``sequence + 1`` and every sub-record (and
every nested acceptance / invalidation / resolution event) names the
operation that produced it and the sequence that operation holds. A
checkpoint can therefore be recomputed from the state exactly as it was
at the checkpoint's own sequence, with the checkpoint's own
``recorded_at`` as the clock (see ``mission.progress``).

Monotone local time base (R-25.3). Applied-operation times are
non-decreasing in sequence order, and every recording timestamp
(activation, claim, artifact, evidence submission, acceptance,
invalidation, blocker open and resolution, dependency bind and
resolution, checkpoint, continuation, closure) EQUALS the ``applied_at``
of the operation whose sequence it carries. The one external datum, a
readiness observation's ``observed_at``, may precede its operation but
never follow it: a future-dated observation is refused outright
(``mission_state_time_inconsistent``), not merely treated as not ready.
Timestamps therefore cannot be shuffled to dodge the historical
authority window the store enforces (``mission_state_authority_window``,
see ``mission.store``).

Evidence is not proof. A claim is a recorded assertion and nothing
more. Evidence is SUBMITTED by one operation and ACCEPTED by a separate,
later operation with its own time, provenance and content digest that
must equal the submitted digest. ``NARRATIVE_CLAIM`` and ``PROCESS_EXIT``
evidence may be recorded but a stored acceptance of either is malformed
(``mission_state_evidence_kind_not_satisfying``): the shape itself
cannot express an accepted narrative.

Artifacts are metadata only: a role, an opaque bounded locator with a
closed ``locator_kind`` (including an opaque delivery-receipt reference),
an optional content digest, an ``available`` flag and ``derived_from``
links to EARLIER artifacts. Nothing here fetches, opens, reads, writes,
executes, publishes or performs a network call; a locator is never
dereferenced.

Blockers carry a severity that the SERVICE derives from the approved
degradation policy (``mission.store`` re-derives and refuses a stored
severity that disagrees). Resolution requires an existing ACCEPTED
evidence id. Dependencies bind a contract slot (``key``) to a reference
at most once per activation; a second record for the same slot is a
rebind and is malformed. A ``MISSION`` dependency may not reference its
own Mission. Readiness observations are provider-neutral: an opaque
``resource_key``, a closed status, an ``observed_at``. Readiness is only
ever a refusal input, never permission to run anything.
"""

from workflow_authority.digest import json_digest

from mission import record

STATE_SCHEMA_VERSION = 1

# -- progress lifecycle (Task 5; separate from mission["state"]) --------

PROGRESS_NOT_STARTED = "NOT_STARTED"
PROGRESS_IN_PROGRESS = "IN_PROGRESS"
PROGRESS_BLOCKED = "BLOCKED"
PROGRESS_COMPLETED = "COMPLETED"
PROGRESS_CLOSED_UNSUCCESSFUL = "CLOSED_UNSUCCESSFUL"
PROGRESS_ABANDONED = "ABANDONED"
PROGRESS_STATES = (
    PROGRESS_NOT_STARTED, PROGRESS_IN_PROGRESS, PROGRESS_BLOCKED,
    PROGRESS_COMPLETED, PROGRESS_CLOSED_UNSUCCESSFUL, PROGRESS_ABANDONED,
)
TERMINAL_PROGRESS_STATES = (
    PROGRESS_COMPLETED, PROGRESS_CLOSED_UNSUCCESSFUL, PROGRESS_ABANDONED,
)
PROGRESS_TRANSITIONS = {
    PROGRESS_NOT_STARTED: frozenset((
        PROGRESS_IN_PROGRESS, PROGRESS_CLOSED_UNSUCCESSFUL, PROGRESS_ABANDONED,
    )),
    PROGRESS_IN_PROGRESS: frozenset((
        PROGRESS_IN_PROGRESS, PROGRESS_BLOCKED, PROGRESS_COMPLETED,
        PROGRESS_CLOSED_UNSUCCESSFUL, PROGRESS_ABANDONED,
    )),
    PROGRESS_BLOCKED: frozenset((
        PROGRESS_IN_PROGRESS, PROGRESS_CLOSED_UNSUCCESSFUL, PROGRESS_ABANDONED,
    )),
    PROGRESS_COMPLETED: frozenset(),
    PROGRESS_CLOSED_UNSUCCESSFUL: frozenset(),
    PROGRESS_ABANDONED: frozenset(),
}

# -- closed vocabularies ---------------------------------------------

BLOCKER_SEVERITY_HARD = "HARD"
BLOCKER_SEVERITY_DEGRADED = "DEGRADED"
BLOCKER_SEVERITIES = (BLOCKER_SEVERITY_HARD, BLOCKER_SEVERITY_DEGRADED)

READINESS_READY = "READY"
READINESS_NOT_READY = "NOT_READY"
READINESS_UNKNOWN = "UNKNOWN"
READINESS_STATUSES = (READINESS_READY, READINESS_NOT_READY, READINESS_UNKNOWN)

LOCATOR_KIND_OPAQUE_REFERENCE = "OPAQUE_REFERENCE"
LOCATOR_KIND_REPOSITORY_PATH = "REPOSITORY_PATH"
LOCATOR_KIND_CONTENT_ADDRESS = "CONTENT_ADDRESS"
LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE = "DELIVERY_RECEIPT_REFERENCE"
LOCATOR_KINDS = (
    LOCATOR_KIND_CONTENT_ADDRESS, LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
    LOCATOR_KIND_OPAQUE_REFERENCE, LOCATOR_KIND_REPOSITORY_PATH,
)

CLOSURE_REASON_PROOF_COMPLETE = "proof_complete"
CLOSURE_REASON_BUDGET_EXHAUSTED = "budget_exhausted"
CLOSURE_REASON_HARD_BLOCKER = "hard_blocker_unresolvable"
CLOSURE_REASON_CALLER_CLOSED = "closed_by_caller"
CLOSURE_REASON_CALLER_ABANDONED = "abandoned_by_caller"
# Each terminal outcome admits only its own reasons; exhaustion is never
# a COMPLETED reason.
CLOSURE_REASONS_BY_PROGRESS = {
    PROGRESS_COMPLETED: (CLOSURE_REASON_PROOF_COMPLETE,),
    PROGRESS_CLOSED_UNSUCCESSFUL: (
        CLOSURE_REASON_BUDGET_EXHAUSTED, CLOSURE_REASON_HARD_BLOCKER,
        CLOSURE_REASON_CALLER_CLOSED,
    ),
    PROGRESS_ABANDONED: (CLOSURE_REASON_CALLER_ABANDONED,),
}

# The state operations. Each applied operation produces exactly one
# record or one nested event; the table maps the operation kind to the
# list (or nested event) it may produce.
OPERATION_ACTIVATE_CONTRACT = "activate_proof_contract"
OPERATION_RECORD_CLAIM = "record_claim"
OPERATION_RECORD_ARTIFACT = "record_artifact"
OPERATION_SUBMIT_EVIDENCE = "submit_evidence"
OPERATION_ACCEPT_EVIDENCE = "accept_evidence"
OPERATION_INVALIDATE_EVIDENCE = "invalidate_evidence"
OPERATION_OPEN_BLOCKER = "open_blocker"
OPERATION_RESOLVE_BLOCKER = "resolve_blocker"
OPERATION_BIND_DEPENDENCY = "bind_dependency"
OPERATION_RESOLVE_DEPENDENCY = "resolve_dependency"
OPERATION_OBSERVE_RESOURCE_READINESS = "observe_resource_readiness"
OPERATION_RECORD_CONTINUATION = "record_continuation"
OPERATION_RECORD_CHECKPOINT = "record_checkpoint"
OPERATION_COMPLETE = "complete"
OPERATION_CLOSE_UNSUCCESSFUL = "close_unsuccessful"
OPERATION_ABANDON = "abandon"
OPERATION_KINDS = (
    OPERATION_ABANDON, OPERATION_ACCEPT_EVIDENCE, OPERATION_ACTIVATE_CONTRACT,
    OPERATION_BIND_DEPENDENCY, OPERATION_CLOSE_UNSUCCESSFUL, OPERATION_COMPLETE,
    OPERATION_INVALIDATE_EVIDENCE, OPERATION_OBSERVE_RESOURCE_READINESS,
    OPERATION_OPEN_BLOCKER, OPERATION_RECORD_ARTIFACT, OPERATION_RECORD_CHECKPOINT,
    OPERATION_RECORD_CLAIM, OPERATION_RECORD_CONTINUATION,
    OPERATION_RESOLVE_BLOCKER, OPERATION_RESOLVE_DEPENDENCY,
    OPERATION_SUBMIT_EVIDENCE,
)
CLOSING_OPERATIONS = {
    OPERATION_COMPLETE: PROGRESS_COMPLETED,
    OPERATION_CLOSE_UNSUCCESSFUL: PROGRESS_CLOSED_UNSUCCESSFUL,
    OPERATION_ABANDON: PROGRESS_ABANDONED,
}

# The next permitted step a checkpoint may name (closed; derived by
# ``mission.progress``, never chosen).
NEXT_STEP_RESOLVE_BLOCKERS = "RESOLVE_BLOCKERS"
NEXT_STEP_RESOLVE_DEPENDENCIES = "RESOLVE_DEPENDENCIES"
NEXT_STEP_OBSERVE_RESOURCE_READINESS = "OBSERVE_RESOURCE_READINESS"
NEXT_STEP_SUBMIT_EVIDENCE = "SUBMIT_EVIDENCE"
NEXT_STEP_ACCEPT_EVIDENCE = "ACCEPT_EVIDENCE"
NEXT_STEP_CLOSE_COMPLETED = "CLOSE_COMPLETED"
# R-22: derived when every local check passes but the approved contract
# declares a required MISSION prerequisite, whose current standing local
# state cannot see. CLOSE_COMPLETED is derived only when it declares none.
NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE = "CONFIRM_PREREQUISITES_AND_CLOSE"
NEXT_STEPS = (
    NEXT_STEP_ACCEPT_EVIDENCE, NEXT_STEP_CLOSE_COMPLETED,
    NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE,
    NEXT_STEP_OBSERVE_RESOURCE_READINESS, NEXT_STEP_RESOLVE_BLOCKERS,
    NEXT_STEP_RESOLVE_DEPENDENCIES, NEXT_STEP_SUBMIT_EVIDENCE,
)

# -- closed key sets --------------------------------------------------

STATE_RECORD_KEYS = (
    "schema_version", "mission_id", "sequence", "created_at", "updated_at",
    "progress", "contract_activations", "claims", "artifacts", "evidence",
    "blockers", "dependencies", "resource_readiness", "checkpoints",
    "continuations", "closure", "applied_operations",
)
ACTIVATION_KEYS = (
    "activation_id", "revision", "proposal_digest_sha256", "authorization_id",
    "authorization_digest_sha256", "contract_digest_sha256", "activated_at",
    "provenance", "operation_id", "sequence",
)
CLAIM_KEYS = (
    "claim_id", "activation_id", "requirement_key", "statement", "claimed_at",
    "provenance", "operation_id", "sequence",
)
ARTIFACT_KEYS = (
    "artifact_id", "key", "role", "locator_kind", "locator",
    "content_digest_sha256", "available", "derived_from", "recorded_at",
    "provenance", "operation_id", "sequence",
)
EVIDENCE_KEYS = (
    "evidence_id", "activation_id", "requirement_key", "kind",
    "content_digest_sha256", "artifact_ids", "submitted_at", "provenance",
    "operation_id", "sequence", "acceptance", "invalidation",
)
ACCEPTANCE_KEYS = (
    "accepted_at", "content_digest_sha256", "activation_id", "provenance",
    "operation_id", "sequence",
)
INVALIDATION_KEYS = (
    "invalidated_at", "reason", "provenance", "operation_id", "sequence",
)
BLOCKER_KEYS = (
    "blocker_id", "activation_id", "key", "severity", "description",
    "opened_at", "provenance", "operation_id", "sequence", "resolution",
)
RESOLUTION_KEYS = (
    "resolved_at", "evidence_id", "provenance", "operation_id", "sequence",
)
DEPENDENCY_KEYS = (
    "dependency_id", "activation_id", "key", "kind", "reference",
    "declared_at", "provenance", "operation_id", "sequence", "resolution",
)
READINESS_OBSERVATION_KEYS = (
    "resource_key", "status", "observed_at", "provenance", "operation_id",
    "sequence",
)
CHECKPOINT_KEYS = (
    "checkpoint_id", "activation_id", "recorded_at", "completed_work",
    "outstanding_work", "refs", "active_blocker_ids",
    "outstanding_dependency_ids", "budget", "retry_condition",
    "stop_condition", "next_permitted_step", "refusal", "provenance",
    "operation_id", "sequence",
)
CHECKPOINT_REFS_KEYS = (
    "revision", "proposal_digest_sha256", "contract_digest_sha256",
)
CHECKPOINT_BUDGET_KEYS = (
    "attempts_consumed", "attempts_remaining", "checkpoints_consumed",
    "checkpoints_remaining",
)
REFUSAL_KEYS = ("problem", "detail")
CONTINUATION_KEYS = (
    "attempt", "reason", "recorded_at", "provenance", "operation_id",
    "sequence",
)
CLOSURE_KEYS = (
    "progress", "reason", "detail", "closed_at", "activation_id", "provenance",
    "operation_id", "sequence",
)
APPLIED_OPERATION_KEYS = (
    "operation_id", "kind", "content_digest_sha256", "applied_at",
    "provenance", "outcome", "sequence",
)
# Closed outcome schema per operation kind (R-31.1, R-31.5). Every
# outcome carries the COMMON keys — the identity of the operation that
# produced it, its sequence, and the progress the record held AT that
# sequence — plus the kind's own keys. ``bind_dependency`` outcomes carry
# ``new_binding``: True when a dependency record was appended, False for
# the identical-rebind no-op whose expected effect is deliberately empty
# (R-28, R-31.3). Kinds whose effect is a nested event (acceptance,
# invalidation, resolution) name the record the event lives on.
OUTCOME_COMMON_KEYS = ("mission_id", "operation_id", "sequence", "progress")
OUTCOME_KEYS_BY_KIND = {
    OPERATION_ACTIVATE_CONTRACT: (
        "activation_id", "revision", "proposal_digest_sha256",
        "contract_digest_sha256", "authorization_id",
    ),
    OPERATION_RECORD_CLAIM: ("claim_id", "requirement_key"),
    OPERATION_RECORD_ARTIFACT: ("artifact_id", "key", "role"),
    OPERATION_SUBMIT_EVIDENCE: (
        "evidence_id", "requirement_key", "kind", "accepted",
    ),
    OPERATION_ACCEPT_EVIDENCE: ("evidence_id", "accepted"),
    OPERATION_INVALIDATE_EVIDENCE: ("evidence_id", "invalidated"),
    OPERATION_OPEN_BLOCKER: ("blocker_id", "key", "severity"),
    OPERATION_RESOLVE_BLOCKER: ("blocker_id", "resolved", "evidence_id"),
    OPERATION_BIND_DEPENDENCY: (
        "dependency_id", "slot_key", "reference", "new_binding",
    ),
    OPERATION_RESOLVE_DEPENDENCY: ("dependency_id", "resolved", "evidence_id"),
    OPERATION_OBSERVE_RESOURCE_READINESS: ("resource_key", "status"),
    OPERATION_RECORD_CONTINUATION: ("attempt", "attempts_remaining"),
    OPERATION_RECORD_CHECKPOINT: (
        "checkpoint_id", "next_permitted_step", "refusal", "budget",
        "active_blocker_ids", "outstanding_dependency_ids",
    ),
    OPERATION_COMPLETE: ("reason", "detail"),
    OPERATION_CLOSE_UNSUCCESSFUL: ("reason", "detail"),
    OPERATION_ABANDON: ("reason", "detail"),
}
# Kinds whose operation is contract-dependent at the service boundary and
# whose provenance revision must therefore equal the activation current
# at their sequence (R-33a). ``close_unsuccessful`` is decided per closure:
# an asserting reason needs the contract, a caller reason does not (R-36).
CONTRACT_DEPENDENT_KINDS = frozenset(OPERATION_KINDS) - frozenset((
    OPERATION_ACTIVATE_CONTRACT, OPERATION_ABANDON, OPERATION_CLOSE_UNSUCCESSFUL,
))
ASSERTING_CLOSURE_REASONS = (
    CLOSURE_REASON_BUDGET_EXHAUSTED, CLOSURE_REASON_HARD_BLOCKER,
)


def closure_is_contract_dependent(closure):
    """COMPLETED, or an unsuccessful closure asserting a contract fact."""
    return closure["progress"] == PROGRESS_COMPLETED or (
        closure["reason"] in ASSERTING_CLOSURE_REASONS)

# -- hard bounds, never derived from input. Exact-value pinned. --------

MAX_CONTRACT_ACTIVATIONS = 64
MAX_CLAIMS = 256
MAX_EVIDENCE_RECORDS = 512
MAX_ARTIFACT_RECORDS = 512
MAX_BLOCKER_RECORDS = 256
MAX_CHECKPOINT_RECORDS = 256
MAX_DEPENDENCY_RECORDS = 128
MAX_RESOURCE_READINESS_OBSERVATIONS = 1024
MAX_CONTINUATION_RECORDS = 64
MAX_APPLIED_OPERATIONS = 4096
MAX_CLAIM_STATEMENT_CHARS = 4000
MAX_BLOCKER_DESCRIPTION_CHARS = 2000
MAX_LOCATOR_CHARS = 2048
MAX_ARTIFACT_LINKS = 64
MAX_WORK_ITEMS = 64
MAX_WORK_ITEM_CHARS = 512
MAX_CONDITION_CHARS = 1000
MAX_STATE_REASON_CHARS = 1000
MAX_RESOURCE_REFERENCE_CHARS = 512
MAX_REFUSAL_DETAIL_CHARS = 2000

# -- problem codes: one distinct code per failure ---------------------

PROBLEM_PROGRESS_UNKNOWN = "mission_state_unknown_progress"
PROBLEM_PROGRESS_TRANSITION = "mission_state_invalid_transition"
PROBLEM_PROGRESS_TERMINAL = "mission_state_terminal"
PROBLEM_PROGRESS_DISAGREES = "mission_state_progress_disagrees"
PROBLEM_SEQUENCE = "mission_state_sequence"
PROBLEM_OPERATION_BINDING = "mission_state_operation_binding"
PROBLEM_ACTIVATION_BINDING = "mission_state_activation_binding"
PROBLEM_ACTIVATION_ORDER = "mission_state_activation_order"
PROBLEM_EVIDENCE_DIGEST = "mission_state_evidence_digest_mismatch"
PROBLEM_EVIDENCE_KIND_NOT_SATISFYING = "mission_state_evidence_kind_not_satisfying"
PROBLEM_EVIDENCE_NOT_ACCEPTED = "mission_state_evidence_not_accepted"
PROBLEM_UNKNOWN_ARTIFACT = "mission_state_unknown_artifact"
PROBLEM_UNKNOWN_EVIDENCE = "mission_state_unknown_evidence"
PROBLEM_UNKNOWN_BLOCKER = "mission_state_unknown_blocker"
PROBLEM_UNKNOWN_DEPENDENCY = "mission_state_unknown_dependency"
PROBLEM_ARTIFACT_DERIVATION = "mission_state_artifact_derivation"
PROBLEM_DEPENDENCY_SELF = "mission_state_dependency_self_reference"
PROBLEM_DEPENDENCY_REBIND = "mission_state_dependency_rebind_refused"
PROBLEM_CONTINUATION_ORDER = "mission_state_continuation_order"
PROBLEM_CHECKPOINT_STEP = "mission_state_checkpoint_step"
PROBLEM_CHECKPOINT_REFS = "mission_state_checkpoint_refs"
PROBLEM_CLOSURE = "mission_state_closure"
PROBLEM_STATE_FULL = "mission_state_full"
PROBLEM_BUDGET_EXHAUSTED = "mission_state_budget_exhausted"
# R-25.1: a recording time outside its authorization's recorded window.
PROBLEM_AUTHORITY_WINDOW = "mission_state_authority_window"
# R-25.3: the monotone local time base.
PROBLEM_TIME_INCONSISTENT = "mission_state_time_inconsistent"
# R-31: the applied-operation ledger and the effect records reconcile.
PROBLEM_EFFECT_INCONSISTENT = "mission_state_effect_inconsistent"
PROBLEM_OUTCOME_MALFORMED = "mission_state_outcome_malformed"
# R-32: a resolution may not name evidence invalidated at or before it.
PROBLEM_EVIDENCE_INVALIDATED = "mission_state_evidence_invalidated"
# R-33: a sub-record's provenance equals its producing operation's.
PROBLEM_PROVENANCE_MISMATCH = "mission_state_provenance_mismatch"
# R-34: the invocation digest re-derived from the effect payload must equal
# the stored one, so every payload field is bound by construction.
PROBLEM_INVOCATION_MISMATCH = "mission_state_invocation_mismatch"
# R-35: an acceptance may not sit at or after the invalidation, nor under
# another activation than its submission.
PROBLEM_ACCEPTANCE_AFTER_INVALIDATION = "mission_state_acceptance_after_invalidation"
PROBLEM_ACCEPTANCE_ACTIVATION_MISMATCH = "mission_state_acceptance_activation_mismatch"
# R-39: a reconstructible service precondition did not hold in history.
PROBLEM_HISTORY_IMPOSSIBLE = "mission_state_history_impossible"
# R-40: a cited provenance revision never existed at that point.
PROBLEM_REVISION_IMPOSSIBLE = "mission_state_revision_impossible"
# R-43: cited revisions never move backward across the operation sequence.
PROBLEM_REVISION_REGRESSED = "mission_state_revision_regressed"
# R-44: a revision ordinal Task 5 consumes from a Mission record is an int.
PROBLEM_REVISION_IDENTITY_MALFORMED = "mission_state_revision_identity_malformed"


# -- invocation identity (R-6, R-34) -------------------------------------


def invocation_digest(kind, mission_id, expected_sequence, arguments):
    """The content digest of one state operation invocation: the ONE
    definition the service stores at application time and the store
    re-derives from the effect payload on every load and save (R-34).
    ``expected_sequence`` is the operation's sequence minus one, because
    the service accepts an invocation only when ``expected_sequence``
    equals the record's sequence and then appends at sequence plus one."""
    return json_digest({
        "kind": kind, "mission_id": mission_id,
        "expected_sequence": expected_sequence, "arguments": arguments,
    })


# -- lifecycle ----------------------------------------------------------


def require_progress(value, location):
    return record.require_member(value, PROGRESS_STATES, location,
                                 PROBLEM_PROGRESS_UNKNOWN)


def validate_progress_transition(current, target):
    """Refuse unless ``current -> target`` is in the Task 5 table. A
    terminal state refuses with its own code: terminal is terminal."""
    require_progress(current, "current progress")
    require_progress(target, "target progress")
    if current in TERMINAL_PROGRESS_STATES:
        record.fail(PROBLEM_PROGRESS_TERMINAL,
                    "progress %s is terminal; it is never re-opened" % current)
    if target not in PROGRESS_TRANSITIONS[current]:
        record.fail(PROBLEM_PROGRESS_TRANSITION,
                    "progress transition %s -> %s is not in the table; the"
                    " allowed targets from %s are %s"
                    % (current, target, current,
                       ", ".join(sorted(PROGRESS_TRANSITIONS[current]))))
    return target


# -- constructors -------------------------------------------------------
# Each returns the record dict in its closed shape. Full validation
# (bindings across the record) happens in ``validate_state_record``,
# which the store runs on every load and every save.


def new_state_record(mission_id, created_at):
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    record.require_timestamp(created_at, "created_at")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "mission_id": mission_id,
        "sequence": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "progress": PROGRESS_NOT_STARTED,
        "contract_activations": [],
        "claims": [],
        "artifacts": [],
        "evidence": [],
        "blockers": [],
        "dependencies": [],
        "resource_readiness": [],
        "checkpoints": [],
        "continuations": [],
        "closure": None,
        "applied_operations": [],
    }


def append_applied_operation(state, operation_id, kind, content_digest_sha256,
                             applied_at, provenance, outcome):
    """Append the applied-operation entry for one accepted mutation and
    advance ``sequence`` by exactly one. Refuses at the bound."""
    if len(state["applied_operations"]) >= MAX_APPLIED_OPERATIONS:
        record.fail(PROBLEM_STATE_FULL,
                    "mission %s already holds %d applied operations; the hard"
                    " bound is %d and history is never pruned"
                    % (state["mission_id"], len(state["applied_operations"]),
                       MAX_APPLIED_OPERATIONS))
    sequence = state["sequence"] + 1
    state["applied_operations"].append({
        "operation_id": operation_id,
        "kind": kind,
        "content_digest_sha256": content_digest_sha256,
        "applied_at": applied_at,
        "provenance": provenance,
        "outcome": outcome,
        "sequence": sequence,
    })
    state["sequence"] = sequence
    state["updated_at"] = applied_at
    return state["applied_operations"][-1]


def new_activation(activation_id, revision, proposal_digest_sha256,
                   authorization_id, authorization_digest_sha256,
                   contract_digest_sha256, activated_at, provenance,
                   operation_id, sequence):
    return {
        "activation_id": activation_id,
        "revision": revision,
        "proposal_digest_sha256": proposal_digest_sha256,
        "authorization_id": authorization_id,
        "authorization_digest_sha256": authorization_digest_sha256,
        "contract_digest_sha256": contract_digest_sha256,
        "activated_at": activated_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_claim(claim_id, activation_id, requirement_key, statement, claimed_at,
              provenance, operation_id, sequence):
    return {
        "claim_id": claim_id,
        "activation_id": activation_id,
        "requirement_key": requirement_key,
        "statement": statement,
        "claimed_at": claimed_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_artifact(artifact_id, key, role, locator_kind, locator,
                 content_digest_sha256, available, derived_from, recorded_at,
                 provenance, operation_id, sequence):
    return {
        "artifact_id": artifact_id,
        "key": key,
        "role": role,
        "locator_kind": locator_kind,
        "locator": locator,
        "content_digest_sha256": content_digest_sha256,
        "available": available,
        "derived_from": sorted(derived_from),
        "recorded_at": recorded_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_evidence(evidence_id, activation_id, requirement_key, kind,
                 content_digest_sha256, artifact_ids, submitted_at, provenance,
                 operation_id, sequence):
    return {
        "evidence_id": evidence_id,
        "activation_id": activation_id,
        "requirement_key": requirement_key,
        "kind": kind,
        "content_digest_sha256": content_digest_sha256,
        "artifact_ids": sorted(artifact_ids),
        "submitted_at": submitted_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
        "acceptance": None,
        "invalidation": None,
    }


def new_acceptance(accepted_at, content_digest_sha256, activation_id, provenance,
                   operation_id, sequence):
    return {
        "accepted_at": accepted_at,
        "content_digest_sha256": content_digest_sha256,
        "activation_id": activation_id,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_invalidation(invalidated_at, reason, provenance, operation_id,
                     sequence):
    return {
        "invalidated_at": invalidated_at,
        "reason": reason,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_blocker(blocker_id, activation_id, key, severity, description,
                opened_at, provenance, operation_id, sequence):
    return {
        "blocker_id": blocker_id,
        "activation_id": activation_id,
        "key": key,
        "severity": severity,
        "description": description,
        "opened_at": opened_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
        "resolution": None,
    }


def new_resolution(resolved_at, evidence_id, provenance, operation_id,
                   sequence):
    return {
        "resolved_at": resolved_at,
        "evidence_id": evidence_id,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_dependency(dependency_id, activation_id, key, kind, reference,
                   declared_at, provenance, operation_id, sequence):
    return {
        "dependency_id": dependency_id,
        "activation_id": activation_id,
        "key": key,
        "kind": kind,
        "reference": reference,
        "declared_at": declared_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
        "resolution": None,
    }


def new_readiness_observation(resource_key, status, observed_at, provenance,
                              operation_id, sequence):
    return {
        "resource_key": resource_key,
        "status": status,
        "observed_at": observed_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_continuation(attempt, reason, recorded_at, provenance, operation_id,
                     sequence):
    return {
        "attempt": attempt,
        "reason": reason,
        "recorded_at": recorded_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_checkpoint(checkpoint_id, activation_id, recorded_at, completed_work,
                   outstanding_work, refs, active_blocker_ids,
                   outstanding_dependency_ids, budget, retry_condition,
                   stop_condition, next_permitted_step, refusal, provenance,
                   operation_id, sequence):
    return {
        "checkpoint_id": checkpoint_id,
        "activation_id": activation_id,
        "recorded_at": recorded_at,
        "completed_work": list(completed_work),
        "outstanding_work": list(outstanding_work),
        "refs": dict(refs),
        "active_blocker_ids": sorted(active_blocker_ids),
        "outstanding_dependency_ids": sorted(outstanding_dependency_ids),
        "budget": dict(budget),
        "retry_condition": retry_condition,
        "stop_condition": stop_condition,
        "next_permitted_step": next_permitted_step,
        "refusal": None if refusal is None else dict(refusal),
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_closure(progress, reason, detail, closed_at, activation_id, provenance,
                operation_id, sequence):
    """``activation_id`` is the activation current at closure (None only
    when the Mission never activated a contract). A COMPLETED closure
    always names one: that is what binds a completion to a revision so a
    dependent Mission can require THIS revision's completion (R-21.2)."""
    return {
        "progress": progress,
        "reason": reason,
        "detail": detail,
        "closed_at": closed_at,
        "activation_id": activation_id,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


# -- projections used by validation and by the pure evaluators -----------


def active_blockers(state):
    return [b for b in state["blockers"] if b["resolution"] is None]


def active_hard_blockers(state):
    return [b for b in active_blockers(state)
            if b["severity"] == BLOCKER_SEVERITY_HARD]


def latest_activation(state):
    activations = state["contract_activations"]
    return activations[-1] if activations else None


def activation_by_id(state, activation_id):
    for activation in state["contract_activations"]:
        if activation["activation_id"] == activation_id:
            return activation
    return None


def evidence_by_id(state, evidence_id):
    for evidence in state["evidence"]:
        if evidence["evidence_id"] == evidence_id:
            return evidence
    return None


def artifact_by_id(state, artifact_id):
    for artifact in state["artifacts"]:
        if artifact["artifact_id"] == artifact_id:
            return artifact
    return None


def is_accepted(evidence):
    """Accepted and not invalidated."""
    return evidence["acceptance"] is not None and evidence["invalidation"] is None
