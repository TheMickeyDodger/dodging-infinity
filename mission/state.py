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

Journal snapshot (Task 7). The applied-operation ledger doubles as the
Mission's Event Journal (``mission.journal``): each entry is an ordered,
stable-identity event bound to the revision in force. The record's first
additive-optional key, ``snapshot``, caches the supported derived state
at a journal position together with the schema version, revision,
position and chain digest it was taken at; it is re-derivable from the
record alone, refused when it does not re-derive, and never a second
source of truth. A record written before the key existed is read as
carrying no snapshot; nothing is supplied on load.

Reconciliation (Task 7, Stage 2). ``reconcile`` is an ordinary state
operation of the ledger (``mission.reconciliation``): it consumes a
reserved operation id, takes ``expected_sequence``, is replay-idempotent
on its invocation digest, and its ONE effect is an entry appended to
the record's second additive-optional key, ``reconciliations``, whose
findings are derived and re-proved on every load and save. It is not
contract-dependent (an EDIT may have moved the revision past the
active contract; recording that drift under the current revision is
its purpose), it changes no progress and no other record, and a record
written before the key existed is read as holding no reconciliation.

Receipt attestation (Task 7, Stage 2, the receipt criterion). A
delivery-receipt reference may be recorded in ATTESTED form: an artifact
that carries the additive-optional key ``receipt_attestation``, a closed
bounded block naming the delivery record, the step, the receipt's actual
state and the step's actual state (both verbatim), the delivery
record's own authority digest the receipt was bound to, and the Mission Authorization (id and digest) the delivery
record named as its parent. The attested form is produced by exactly one
operation kind, ``attest_delivery_receipt``, which is DISTINCT from
``record_artifact``: the generic kind never adds the marker, the
attesting kind always does, and the validators refuse a marker with no
producing operation of the attesting kind, an attesting operation with
no marked artifact, a marker on a generic artifact, two markers for one
operation, and a marker whose fields disagree with the operation's
outcome and invocation. A record written before the marker existed
carries no marker on any artifact and is read as UNATTESTED: nothing is
supplied or backfilled on load. Absence is the KEY being absent: a
present key holding null (or any shape but the closed marker) is a
malformed marker and refuses on load and save; it is never read as a
generic artifact.

What the attested form means, exactly. The marker records that the ONE
production path permitted to call the attesting operation (the delivery
layer's parent seam, confined by the static pins to a single calling
function that runs the delivery layer's existing, unchanged receipt
validator and the parent-authority check FIRST) accepted this receipt
for this Mission at this revision. It records evidence and grants
nothing: an attestation cannot approve or close a Mission, alter a human
decision, waive proof, remove a blocker, raise a budget, or permit any
delivery action, and it is not cryptographic authenticity: nothing here
signs anything, and the authority digests it carries are binding values,
not secrets. Structural validity is not success: ``receipt_state`` and
``step_state`` are the delivery layer's own vocabulary stored verbatim,
and only the pair ``RECEIPT_STATE_SUCCEEDED`` / ``STEP_STATE_SUCCEEDED``
(each pinned equal to the delivery layer's constant by the static
tests) is read as a completed effect; a receipt attested in any other
state, or under a step in any other state, proves that the effect was
NOT completed when attested — the same condition the seam reports. The marker is absent on every generic and legacy
artifact, so the mere presence of a receipt-reference artifact never
implies attestation.
"""

from workflow_authority.digest import json_digest

import copy

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
OPERATION_ATTEST_DELIVERY_RECEIPT = "attest_delivery_receipt"
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
OPERATION_RECONCILE = "reconcile"
# Task 8, slice S-IV: the engineering engagement reservation — the
# Mission-side engagement FENCE (exactly one initial engagement per
# activation, naming the deterministic workflow id and the authorization
# digest) and the BUDGET reservation for each follow-up engagement, in one
# canonical, durable, journaled operation.
OPERATION_RESERVE_ENGAGEMENT = "reserve_engagement"
# Task 8, slice S-IV (start-claim decision): the engagement START — the
# atomic current-authority admission that persists ONE exact,
# single-owner, non-replayable claim of one engine operation (a runtime
# creation, or the hand-over of the objective) of a reserved engagement;
# its SETTLEMENT (the returned identity and outcome, plus the stop
# requirement derived from the canonical facts at that instant); and the
# OBSERVATION of its stop (fresh observed absence is the only
# confirmation). Bookkeeping of execution, never new authority: a start
# permits at most the one owner's one invocation and exempts nothing
# from later re-validation.
OPERATION_OPEN_ENGAGEMENT_START = "open_engagement_start"
OPERATION_SETTLE_ENGAGEMENT_START = "settle_engagement_start"
OPERATION_OBSERVE_ENGAGEMENT_STOP = "observe_engagement_stop"
# Task 8, slice S-V: the canonical CONTROL operations — a reversible
# hold (and its lift), a sticky cancel request and its confirmation.
# Each runs through ``_apply`` (reserved id, exact sequence, provenance,
# ledger, journal) but needs NO live contract, authority or budget: a
# human must be able to stop or pause a Mission whose approval expired,
# whose contract is superseded or whose budgets are exhausted. What is
# NOT bypassed: the Mission must exist, the sequence must be current, the
# id must be reserved for the caller's context, the caller's provenance
# must be sufficient, and terminal progress stays terminal.
OPERATION_REQUEST_HOLD = "request_hold"
OPERATION_LIFT_HOLD = "lift_hold"
OPERATION_REQUEST_CANCEL = "request_cancel"
OPERATION_CONFIRM_CANCEL = "confirm_cancel"
CONTROL_OPERATIONS = (
    OPERATION_CONFIRM_CANCEL, OPERATION_LIFT_HOLD, OPERATION_REQUEST_CANCEL,
    OPERATION_REQUEST_HOLD,
)
OPERATION_KINDS = (
    OPERATION_ABANDON, OPERATION_ACCEPT_EVIDENCE, OPERATION_ACTIVATE_CONTRACT,
    OPERATION_ATTEST_DELIVERY_RECEIPT,
    OPERATION_BIND_DEPENDENCY, OPERATION_CLOSE_UNSUCCESSFUL, OPERATION_COMPLETE,
    OPERATION_CONFIRM_CANCEL,
    OPERATION_INVALIDATE_EVIDENCE, OPERATION_LIFT_HOLD,
    OPERATION_OBSERVE_ENGAGEMENT_STOP,
    OPERATION_OBSERVE_RESOURCE_READINESS,
    OPERATION_OPEN_BLOCKER, OPERATION_OPEN_ENGAGEMENT_START, OPERATION_RECONCILE,
    OPERATION_RECORD_ARTIFACT,
    OPERATION_RECORD_CHECKPOINT, OPERATION_RECORD_CLAIM,
    OPERATION_RECORD_CONTINUATION,
    OPERATION_REQUEST_CANCEL, OPERATION_REQUEST_HOLD, OPERATION_RESERVE_ENGAGEMENT,
    OPERATION_RESOLVE_BLOCKER, OPERATION_RESOLVE_DEPENDENCY,
    OPERATION_SETTLE_ENGAGEMENT_START,
    OPERATION_SUBMIT_EVIDENCE,
)
CLOSING_OPERATIONS = {
    OPERATION_COMPLETE: PROGRESS_COMPLETED,
    OPERATION_CLOSE_UNSUCCESSFUL: PROGRESS_CLOSED_UNSUCCESSFUL,
    OPERATION_ABANDON: PROGRESS_ABANDONED,
    # Task 8, slice S-V: a confirmed cancel closes the Mission as
    # abandoned by the caller, only after every start's stop is confirmed.
    OPERATION_CONFIRM_CANCEL: PROGRESS_ABANDONED,
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

STATE_RECORD_REQUIRED_KEYS = (
    "schema_version", "mission_id", "sequence", "created_at", "updated_at",
    "progress", "contract_activations", "claims", "artifacts", "evidence",
    "blockers", "dependencies", "resource_readiness", "checkpoints",
    "continuations", "closure", "applied_operations",
)
# The additive-optional state-record keys (Task 7): the journal
# snapshot (Stage 1) and the reconciliation records (Stage 2). Each is
# absent in a record written before it existed and read as "none";
# every record this layer creates carries both (``snapshot`` None or a
# closed snapshot, ``mission.journal``; ``reconciliations`` a bounded
# list, ``mission.reconciliation``). Nothing supplies either on load.
# ``engagements`` (Task 8, slice S-IV): the engineering engagement
# reservations, additive-optional exactly like the two above (absent in
# a record written before it existed and read as an empty list; every
# record this layer creates carries it).
# ``engagement_starts`` (Task 8, slice S-IV, start-claim decision): the
# engagement START records, additive-optional the same way.
# ``controls`` (Task 8, slice S-V): the canonical control record.
STATE_RECORD_OPTIONAL_KEYS = ("snapshot", "reconciliations", "engagements",
                              "engagement_starts", "controls")
STATE_RECORD_KEYS = STATE_RECORD_REQUIRED_KEYS + STATE_RECORD_OPTIONAL_KEYS
ENGAGEMENT_KEYS = (
    "engagement_id", "activation_id", "workflow_id", "engagement_sequence", "kind",
    "authorization_id", "authorization_digest_sha256", "reserved_at",
    "provenance", "operation_id", "sequence",
)
ENGAGEMENT_KIND_INITIAL = "initial"
ENGAGEMENT_KIND_FOLLOW_UP = "follow_up"
ENGAGEMENT_KINDS = (ENGAGEMENT_KIND_FOLLOW_UP, ENGAGEMENT_KIND_INITIAL)
# The workflow id grammar of the workflow authority layer, mirrored
# without importing it (mission never imports the record layer): 1..128
# characters of lowercase letters, digits and hyphen.
MAX_WORKFLOW_ID_CHARS = 128
_WORKFLOW_ID_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
# Task 8, slice S-IV (start-claim decision): the engagement START record.
ENGAGEMENT_START_KEYS = (
    "start_id", "engagement_id", "activation_id", "workflow_id",
    "engagement_sequence", "point", "authorization_id",
    "authorization_digest_sha256", "owner_ref", "opened_at", "provenance",
    "operation_id", "sequence", "stop_requested", "settlement",
    "stop_observations",
)
# The two engine operations a start claims, each its own claim: the
# creation of the target runtime, then the hand-over of the objective to
# it. A task start requires the engagement's runtime start settled
# COMPLETED with no stop requirement.
START_POINT_RUNTIME = "runtime"
START_POINT_TASK = "task"
START_POINTS = (START_POINT_RUNTIME, START_POINT_TASK)
# What the owner observed the engine return: completed (an identity was
# returned), failed (the engine refused or raised — NOT absence proof),
# uncertain (no response: a hung or lost call). Only ``completed`` may
# carry an identity.
START_OUTCOME_COMPLETED = "completed"
START_OUTCOME_FAILED = "failed"
START_OUTCOME_UNCERTAIN = "uncertain"
START_OUTCOMES = (START_OUTCOME_COMPLETED, START_OUTCOME_FAILED,
                  START_OUTCOME_UNCERTAIN)
# The stop requirement a control records on an OPEN or settled start in
# ITS OWN transaction, never cleared afterwards: a cancel request binds
# its operation (``operation_id``/``sequence``); an EDIT — a Mission
# record decision, not a state operation — binds the superseding
# ``revision`` instead (``operation_id``/``sequence`` None). Exactly one
# of the two bindings is present.
STOP_REQUEST_KEYS = ("requested_at", "reason", "operation_id", "sequence",
                     "revision")

# -- the canonical control record (Task 8, slice S-V) ---------------------
CONTROL_KEY = "controls"
CONTROL_RECORD_VERSION = 1
# ``holds`` keeps EVERY hold (append-only, bounded by the ledger): a
# later hold never orphans the applied operation that recorded an
# earlier one, and every hold's effect stays reconcilable.
CONTROL_RECORD_KEYS = ("version", "holds", "cancel_request", "history")
# A hold: reversible; ``lifted_at``/``lift_*`` None while active.
CONTROL_HOLD_KEYS = ("requested_at", "reason", "revision", "provenance",
                     "operation_id", "sequence", "lifted_at",
                     "lift_operation_id", "lift_sequence")
# A cancel request: sticky; ``confirmed_at``/``confirmation`` None until
# every start's stop is confirmed by observed absence.
CONTROL_CANCEL_KEYS = ("requested_at", "reason", "revision", "provenance",
                       "operation_id", "sequence", "confirmed_at", "confirmation")
CONTROL_CONFIRMATION_KEYS = ("detail", "starts_confirmed", "starts_never_started",
                             "provenance", "operation_id", "sequence")
# Bounded, informational history (the hold / cancel_request fields are
# the canonical facts): oldest entries are dropped at the bound so a
# control is never refused for lack of history room.
CONTROL_HISTORY_KEYS = ("kind", "at", "operation_id", "sequence", "revision")
CONTROL_EVENT_HOLD_REQUESTED = "hold_requested"
CONTROL_EVENT_HOLD_LIFTED = "hold_lifted"
CONTROL_EVENT_CANCEL_REQUESTED = "cancel_requested"
CONTROL_EVENT_CANCEL_CONFIRMED = "cancel_confirmed"
CONTROL_EVENT_REVISION_SUPERSEDED = "revision_superseded"
CONTROL_EVENTS = (
    CONTROL_EVENT_CANCEL_CONFIRMED, CONTROL_EVENT_CANCEL_REQUESTED,
    CONTROL_EVENT_HOLD_LIFTED, CONTROL_EVENT_HOLD_REQUESTED,
    CONTROL_EVENT_REVISION_SUPERSEDED,
)
MAX_CONTROL_HISTORY = 64
# CAPACITY STRATEGY (tiered, so cancel is never starved): the applied-
# operation ledger refuses ORDINARY operations CONTROL_OPERATION_HEADROOM
# entries below its hard bound; a HOLD or LIFT refuses
# CANCEL_OPERATION_RESERVE entries below it; request_cancel refuses only
# when it could not leave the confirmation its slot; confirm_cancel may
# fill the last slot. A cancel request is applied at most once (sticky)
# and its confirmation at most once (it closes the record), so the two
# reserved slots are exactly the pair — no run of holds and lifts can
# consume them. The cancel operation ids are reserved under their OWN
# kind and derived per Mission (``cancel_operation_id``), so no run of
# ordinary or hold reservations can exhaust them either.
CONTROL_OPERATION_HEADROOM = 16
CANCEL_OPERATION_RESERVE = 2
HOLD_OPERATIONS = (OPERATION_LIFT_HOLD, OPERATION_REQUEST_HOLD)
CANCEL_OPERATIONS = (OPERATION_CONFIRM_CANCEL, OPERATION_REQUEST_CANCEL)
_CANCEL_OPERATION_ID_SALT = "mission cancel operation"


def applied_operation_limit(kind):
    """The ledger length at which an operation of ``kind`` is refused."""
    if kind == OPERATION_CONFIRM_CANCEL:
        return MAX_APPLIED_OPERATIONS
    if kind == OPERATION_REQUEST_CANCEL:
        return MAX_APPLIED_OPERATIONS - (CANCEL_OPERATION_RESERVE - 1)
    if kind in HOLD_OPERATIONS:
        return MAX_APPLIED_OPERATIONS - CANCEL_OPERATION_RESERVE
    return MAX_APPLIED_OPERATIONS - CONTROL_OPERATION_HEADROOM


def cancel_operation_id(mission_id, kind):
    """The ONE operation id a Mission's cancel request (or confirmation)
    is reserved under: derived, so each Mission consumes at most two
    cancel reservations and the dedicated cap is never exceeded."""
    if kind not in CANCEL_OPERATIONS:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "only a cancel operation has a derived id, not %r" % kind)
    return "%s-%s" % (record.STATE_OPERATION_ID_PREFIX, json_digest(
        [_CANCEL_OPERATION_ID_SALT, mission_id, kind])[:32])
# State-changing controls need sufficient provenance: a client-confirmed
# decision (the elicited relay) or the local process user; a
# connector-credential-only caller is refused.
CONTROL_PRINCIPAL_KINDS = (
    record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
    record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
)
# The settlement event: the returned identity and outcome, and whether a
# stop is pending for what was started (derived at settlement from the
# canonical facts: a recorded stop request, a superseded revision, an
# authorization no longer live, a terminal Mission — or the owner's own
# reason, the gate's terminal refusal at settlement).
# ``owner_reason`` is the owner's own stop reason as INVOKED (the effect
# gate's terminal refusal at settlement, or None); ``stop_reason`` is the
# derived summary of every reason found.
SETTLEMENT_KEYS = ("settled_at", "outcome", "identity", "stop_pending",
                   "owner_reason", "stop_reason", "provenance", "operation_id",
                   "sequence")
# The execution identity the engine returned: the workspace it created,
# the exact agent name set in it, and the task id once handed over.
START_IDENTITY_KEYS = ("workspace_id", "agent_names", "task_id")
# The owner's observations of the required stop, a bounded append-only
# list (each one bound to its own operation): the LATEST decides, and
# ``absent`` True is the ONLY confirmation (fresh observed absence of
# the identified workspace); once confirmed no further observation is
# recorded.
# ``identity`` (the execution identity returned LATE, after the owner's
# bounded wait, or None) is persisted here BEFORE any owned stop acts on
# it, so recovery after a restart works from the canonical identity.
STOP_OBSERVATION_KEYS = ("observed_at", "absent", "detail", "identity",
                         "provenance", "operation_id", "sequence")
MAX_STOP_OBSERVATIONS = 16
MAX_OWNER_REF_CHARS = 128
MAX_START_IDENTITY_CHARS = 128
MAX_START_AGENT_NAMES = 32
MAX_STOP_DETAIL_CHARS = 2000
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
# The additive-optional artifact key (Task 7, Stage 2): present, with a
# closed bounded block, on exactly the artifacts an
# ``attest_delivery_receipt`` operation produced; ABSENT (not None) on
# every artifact a ``record_artifact`` operation produced and on every
# artifact written before the key existed. Nothing supplies it on load.
ARTIFACT_MARKER_RECEIPT_ATTESTATION = "receipt_attestation"
ARTIFACT_OPTIONAL_KEYS = (ARTIFACT_MARKER_RECEIPT_ATTESTATION,)
# The stored marker: what the delivery layer's validating path bound.
RECEIPT_ATTESTATION_KEYS = (
    "delivery_id", "step", "receipt_state", "step_state",
    "parent_authority_digest_sha256", "authorization_id",
    "authorization_digest_sha256",
)
# The attesting operation's input: the receipt's reference and content
# digest (which become the artifact's locator and content digest), the
# delivery record, step and verbatim receipt state, the delivery
# record's authority digest the receipt is bound to, and the Mission
# Authorization digest the delivery record names as its parent. The
# authorization id is resolved from that digest inside the locked write,
# never supplied.
RECEIPT_ATTESTATION_INPUT_KEYS = (
    "receipt_id", "receipt_digest_sha256", "delivery_id", "step",
    "receipt_state", "step_state", "parent_authority_digest_sha256",
    "authorization_digest_sha256",
)
# The completion condition, exactly the delivery layer's: a completed
# effect is a receipt in the succeeded RECEIPT state recorded under a
# step in the succeeded STEP state — both stored verbatim, both pinned
# equal to that layer's constants by the static tests. Either alone is
# structural validity, never success: the delivery contract accepts a
# succeeded receipt under a pending step, and that is attested as NOT
# completed, exactly as the seam reports it.
RECEIPT_STATE_SUCCEEDED = "succeeded"
STEP_STATE_SUCCEEDED = "succeeded"
# Task 8 S-V (R2-11-a/b): the attested delivery steps whose COMPLETED
# receipt authorizes an observed identity to move WITHIN one authorized
# revision — the base only by a base refresh; the observed head by a base
# refresh (it moves the source ref onto the new base) or by a commit.
# Pinned equal to the delivery layer's step names by the static tests.
TRANSITION_STEP_BASE_REFRESH = "BASE_REFRESH"
TRANSITION_STEP_COMMIT = "COMMIT"
BASELINE_TRANSITION_STEPS = (TRANSITION_STEP_BASE_REFRESH,)
HEAD_TRANSITION_STEPS = (TRANSITION_STEP_BASE_REFRESH, TRANSITION_STEP_COMMIT)
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
    OPERATION_ATTEST_DELIVERY_RECEIPT: (
        "artifact_id", "delivery_id", "step", "receipt_state", "step_state",
        "authorization_id",
    ),
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
    OPERATION_RESERVE_ENGAGEMENT: (
        "engagement_id", "workflow_id", "engagement_sequence", "kind",
        "attempts_remaining",
    ),
    OPERATION_OPEN_ENGAGEMENT_START: ("start_id", "engagement_id", "point"),
    OPERATION_SETTLE_ENGAGEMENT_START: ("start_id", "outcome", "stop_pending"),
    OPERATION_OBSERVE_ENGAGEMENT_STOP: ("start_id", "absent", "stop_confirmed"),
    # R15-1: a hold also reports the OPEN claims it marked with a stop.
    OPERATION_REQUEST_HOLD: ("hold_active", "stops_requested"),
    OPERATION_LIFT_HOLD: ("hold_active",),
    OPERATION_REQUEST_CANCEL: ("cancel_requested", "stops_requested"),
    OPERATION_CONFIRM_CANCEL: ("reason", "detail"),
    OPERATION_RECORD_CHECKPOINT: (
        "checkpoint_id", "next_permitted_step", "refusal", "budget",
        "active_blocker_ids", "outstanding_dependency_ids",
    ),
    OPERATION_COMPLETE: ("reason", "detail"),
    OPERATION_CLOSE_UNSUCCESSFUL: ("reason", "detail"),
    OPERATION_ABANDON: ("reason", "detail"),
    OPERATION_RECONCILE: ("observed_position", "observed_revision", "finding_count"),
}
# Kinds whose operation is contract-dependent at the service boundary and
# whose provenance revision must therefore equal the activation current
# at their sequence (R-33a). ``close_unsuccessful`` is decided per closure:
# an asserting reason needs the contract, a caller reason does not (R-36).
# Task 8, slice S-IV (start-claim decision): settling a start and
# observing its stop are excluded too — they must stay possible after the
# authority lapsed or the revision moved, because they record what
# happened to an already admitted operation and permit nothing new.
# The four control operations (Task 8, slice S-V) are excluded too: a
# hold or cancel must stay available after the authority lapsed.
CONTRACT_DEPENDENT_KINDS = frozenset(OPERATION_KINDS) - frozenset((
    OPERATION_ACTIVATE_CONTRACT, OPERATION_ABANDON, OPERATION_CLOSE_UNSUCCESSFUL,
    OPERATION_SETTLE_ENGAGEMENT_START, OPERATION_OBSERVE_ENGAGEMENT_STOP,
    OPERATION_REQUEST_HOLD, OPERATION_LIFT_HOLD, OPERATION_REQUEST_CANCEL,
    OPERATION_CONFIRM_CANCEL,
    OPERATION_RECONCILE,
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
MAX_ENGAGEMENT_RECORDS = 64
MAX_ENGAGEMENT_START_RECORDS = 128
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
# Every string field of a receipt attestation (the receipt reference,
# delivery record id, step and receipt state) is bounded here BEFORE it
# is compared, copied or formatted; the delivery layer bounds its own
# ids at the same value.
MAX_RECEIPT_ATTESTATION_FIELD_CHARS = 128

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
# Task 7, Stage 2: a receipt attestation (input or stored marker) that is
# not the closed bounded shape, or an attested artifact whose own fields
# are not the ones the attesting operation records.
PROBLEM_RECEIPT_ATTESTATION = "mission_state_receipt_attestation"
# Task 8, slice S-IV: engagement reservations.
PROBLEM_ENGAGEMENT_FENCE = "mission_state_engagement_fence"
PROBLEM_ENGAGEMENT_SEQUENCE = "mission_state_engagement_sequence"
PROBLEM_ENGAGEMENT_WORKFLOW_MISMATCH = "mission_state_engagement_workflow_mismatch"
PROBLEM_WORKFLOW_ID = "mission_state_workflow_id"
# Task 8, slice S-IV (start-claim decision): engagement starts.
PROBLEM_UNKNOWN_ENGAGEMENT = "mission_state_unknown_engagement"
PROBLEM_UNKNOWN_ENGAGEMENT_START = "mission_state_unknown_engagement_start"
# A start for exactly this (engagement, point) already exists: settled
# or not, it is never re-opened — an uncertain or failed start can never
# be retried under the same engagement.
PROBLEM_ENGAGEMENT_START_EXISTS = "mission_state_engagement_start_exists"
# A task start needs the runtime start of the same engagement settled
# COMPLETED without a stop requirement.
PROBLEM_ENGAGEMENT_START_ORDER = "mission_state_engagement_start_order"
# A settlement or observation that does not fit the start's state.
PROBLEM_ENGAGEMENT_START_STATE = "mission_state_engagement_start_state"
# The settling or observing principal is not the start's owner.
PROBLEM_ENGAGEMENT_START_OWNER = "mission_state_engagement_start_owner"
PROBLEM_ENGAGEMENT_START_POINT = "mission_state_engagement_start_point"
# Task 8, slice S-V: controls.
PROBLEM_CONTROL_PROVENANCE = "mission_state_control_provenance"
PROBLEM_CONTROL_STATE = "mission_state_control_state"
PROBLEM_CONTROL_HOLD = "mission_state_control_hold"
PROBLEM_CONTROL_CANCELLED = "mission_state_control_cancelled"
PROBLEM_CANCEL_UNCONFIRMED = "mission_state_cancel_unconfirmed"


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
        "snapshot": None,
        "reconciliations": [],
        "engagements": [],
        "engagement_starts": [],
        "controls": new_controls(),
    }


def new_controls():
    return {"version": CONTROL_RECORD_VERSION, "holds": [],
            "cancel_request": None, "history": []}


def latest_hold(controls):
    """The most recent hold record (active or lifted), or None."""
    holds = controls["holds"]
    return holds[-1] if holds else None


def controls_of(state):
    """The control record, or the empty default for a record written
    before the additive key existed (nothing is supplied on load)."""
    controls = state.get("controls") if state is not None else None
    return controls if controls is not None else new_controls()


def hold_active(state):
    hold = latest_hold(controls_of(state))
    return hold is not None and hold["lifted_at"] is None


def cancel_requested(state):
    return controls_of(state)["cancel_request"] is not None


def cancel_confirmed(state):
    request = controls_of(state)["cancel_request"]
    return request is not None and request["confirmed_at"] is not None


def control_view(state):
    """The closed read every gate consults: the three facts and the
    canonical records behind them (deep copies)."""
    controls = controls_of(state)
    return {
        "hold_active": hold_active(state),
        "cancel_requested": cancel_requested(state),
        "cancel_confirmed": cancel_confirmed(state),
        "hold": copy.deepcopy(latest_hold(controls)),
        "cancel_request": copy.deepcopy(controls["cancel_request"]),
    }


def supersession_view(state, revision):
    """Task 8, slice S-V: what an EDIT to ``revision`` left behind,
    DERIVED from durable facts (identical on replay): the last activation
    of an earlier revision, the checkpoints and engagement workflows
    recorded under earlier activations, the starts whose stop requirement
    is bound to this revision, and whether the supersession event was
    recorded in the control history."""
    view = {"revision": revision, "activation_id": None, "checkpoints": 0,
            "engagements": [], "starts_stop_requested": 0, "recorded": False}
    if state is None:
        return view
    older = [a for a in state["contract_activations"] if a["revision"] < revision]
    if older:
        view["activation_id"] = older[-1]["activation_id"]
    older_ids = set(a["activation_id"] for a in older)
    view["checkpoints"] = sum(
        1 for c in state["checkpoints"] if c["activation_id"] in older_ids)
    view["engagements"] = sorted(set(
        e["workflow_id"] for e in engagements_of(state)
        if e["activation_id"] in older_ids))
    view["starts_stop_requested"] = sum(
        1 for s in engagement_starts_of(state)
        if s["stop_requested"] is not None
        and s["stop_requested"]["operation_id"] is None
        and s["stop_requested"]["revision"] == revision)
    view["recorded"] = any(
        event["kind"] == CONTROL_EVENT_REVISION_SUPERSEDED
        and event["revision"] == revision
        for event in controls_of(state)["history"])
    return view


def append_control_event(controls, kind, at, operation_id, sequence, revision):
    """Bounded history: the oldest entry is dropped at the bound, so a
    control is never refused for lack of history room."""
    history = controls["history"]
    while len(history) >= MAX_CONTROL_HISTORY:
        history.pop(0)
    history.append({"kind": kind, "at": at, "operation_id": operation_id,
                    "sequence": sequence, "revision": revision})


def new_hold(requested_at, reason, revision, provenance, operation_id, sequence):
    return {"requested_at": requested_at, "reason": reason, "revision": revision,
            "provenance": provenance, "operation_id": operation_id,
            "sequence": sequence, "lifted_at": None,
            "lift_operation_id": None, "lift_sequence": None}


def new_cancel_request(requested_at, reason, revision, provenance, operation_id,
                       sequence):
    return {"requested_at": requested_at, "reason": reason, "revision": revision,
            "provenance": provenance, "operation_id": operation_id,
            "sequence": sequence, "confirmed_at": None, "confirmation": None}


def new_stop_request(requested_at, reason, operation_id, sequence, revision):
    return {"requested_at": requested_at, "reason": reason,
            "operation_id": operation_id, "sequence": sequence,
            "revision": revision}


def unresolved_starts(state):
    """Every engagement start whose stop is not confirmed by observed
    absence: an open start, a settled start with a stop required and no
    confirming observation — and any start at all whose stop was never
    required but which was invoked (a running engagement a cancel must
    stop). Cancel confirmation needs this list empty."""
    unresolved = []
    for start in engagement_starts_of(state):
        if start_stop_confirmed(start):
            continue
        unresolved.append(start)
    return unresolved


def engagements_of(state):
    """The engagement reservations, empty for a record written before the
    additive key existed (nothing is supplied on load)."""
    return state.get("engagements") or []


def engagement_starts_of(state):
    """The engagement start records, empty for a record written before
    the additive key existed (nothing is supplied on load)."""
    return state.get("engagement_starts") or []


def engagement_by_id(state, engagement_id):
    for engagement in engagements_of(state):
        if engagement["engagement_id"] == engagement_id:
            return engagement
    return None


def engagement_start_by_id(state, start_id):
    for start in engagement_starts_of(state):
        if start["start_id"] == start_id:
            return start
    return None


def engagement_start_at(state, engagement_id, point):
    """The one start of ``engagement_id`` at ``point``, or None."""
    for start in engagement_starts_of(state):
        if start["engagement_id"] == engagement_id and start["point"] == point:
            return start
    return None


def start_is_open(start):
    """OPEN: admitted, not yet settled (a crash here is ambiguity)."""
    return start["settlement"] is None


def start_stop_required(start):
    """Whether a stop is required for what this start may have started:
    a control recorded a stop request on it, or its settlement found a
    stop pending. Never cleared by a later resume or approval."""
    if start["stop_requested"] is not None:
        return True
    settlement = start["settlement"]
    return settlement is not None and bool(settlement["stop_pending"])


def stop_observations_of(start):
    return start.get("stop_observations") or []


def latest_stop_observation(start):
    observations = stop_observations_of(start)
    return observations[-1] if observations else None


def start_stop_confirmed(start):
    """Confirmed ONLY by the latest recorded observation being fresh
    absence."""
    observation = latest_stop_observation(start)
    return observation is not None and bool(observation["absent"])


def start_identity(start):
    """The canonical execution identity of a start: the settlement's, or
    the latest observation that carries one (a late return), or None."""
    settlement = start["settlement"]
    if settlement is not None and settlement["identity"] is not None:
        return settlement["identity"]
    for observation in reversed(stop_observations_of(start)):
        if observation.get("identity") is not None:
            return observation["identity"]
    return None


def new_engagement_start(start_id, engagement, point, owner_ref, opened_at,
                         provenance, operation_id, sequence):
    return {
        "start_id": start_id,
        "engagement_id": engagement["engagement_id"],
        "activation_id": engagement["activation_id"],
        "workflow_id": engagement["workflow_id"],
        "engagement_sequence": engagement["engagement_sequence"],
        "point": point,
        "authorization_id": engagement["authorization_id"],
        "authorization_digest_sha256": engagement["authorization_digest_sha256"],
        "owner_ref": owner_ref,
        "opened_at": opened_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
        "stop_requested": None,
        "settlement": None,
        "stop_observations": [],
    }


def new_settlement(settled_at, outcome, identity, stop_pending, owner_reason,
                   stop_reason, provenance, operation_id, sequence):
    return {
        "settled_at": settled_at,
        "outcome": outcome,
        "identity": identity,
        "stop_pending": stop_pending,
        "owner_reason": owner_reason,
        "stop_reason": stop_reason,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def new_stop_observation(observed_at, absent, detail, identity, provenance,
                         operation_id, sequence):
    return {
        "observed_at": observed_at,
        "absent": absent,
        "detail": detail,
        "identity": identity,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def validate_start_identity(value, location):
    """The closed execution identity: ``workspace_id`` (str or None),
    ``agent_names`` (a bounded sorted list of distinct names) and
    ``task_id`` (str or None)."""
    record.require_dict(value, location)
    record.require_closed_keys(value, START_IDENTITY_KEYS, location)
    record.require_optional_str(value["workspace_id"], location + ".workspace_id",
                                MAX_START_IDENTITY_CHARS)
    names = value["agent_names"]
    if not isinstance(names, list):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s.agent_names must be a list" % location)
    if len(names) > MAX_START_AGENT_NAMES:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s.agent_names holds %d names; the hard bound is %d"
                    % (location, len(names), MAX_START_AGENT_NAMES))
    for index, name in enumerate(names):
        record.require_str(name, "%s.agent_names[%d]" % (location, index),
                           MAX_START_IDENTITY_CHARS)
    if names != sorted(set(names)):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.agent_names must be sorted and distinct" % location)
    record.require_optional_str(value["task_id"], location + ".task_id",
                                MAX_START_IDENTITY_CHARS)
    return value


def require_workflow_id(value, location):
    """The workflow authority layer's workflow id grammar, mirrored."""
    record.require_str(value, location, MAX_WORKFLOW_ID_CHARS)
    if any(ch not in _WORKFLOW_ID_ALPHABET for ch in value):
        record.fail(PROBLEM_WORKFLOW_ID,
                    "%s must use only lowercase letters, digits and hyphen"
                    % location)
    return value


def new_engagement(engagement_id, activation_id, workflow_id, engagement_sequence,
                 kind, authorization_id, authorization_digest_sha256,
                 reserved_at, provenance, operation_id, sequence):
    return {
        "engagement_id": engagement_id,
        "activation_id": activation_id,
        "workflow_id": workflow_id,
        "engagement_sequence": engagement_sequence,
        "kind": kind,
        "authorization_id": authorization_id,
        "authorization_digest_sha256": authorization_digest_sha256,
        "reserved_at": reserved_at,
        "provenance": provenance,
        "operation_id": operation_id,
        "sequence": sequence,
    }


def append_applied_operation(state, operation_id, kind, content_digest_sha256,
                             applied_at, provenance, outcome):
    """Append the applied-operation entry for one accepted mutation and
    advance ``sequence`` by exactly one. Refuses at the tiered bound
    (``applied_operation_limit``): ordinary operations first, holds and
    lifts next, so the cancel pair can always still be recorded."""
    if len(state["applied_operations"]) >= applied_operation_limit(kind):
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


def _require_attestation_str(value, location):
    return record.require_str(value, location, MAX_RECEIPT_ATTESTATION_FIELD_CHARS)


def validate_receipt_attestation_input(value, location="attestation"):
    """The attesting operation's closed, bounded input (see
    ``RECEIPT_ATTESTATION_INPUT_KEYS``): every field is established as
    an exact bounded builtin before it is compared, copied or formatted.
    Refuses with ``mission_state_receipt_attestation``."""
    if type(value) is not dict:
        record.fail(PROBLEM_RECEIPT_ATTESTATION,
                    "%s must be a plain object" % location)
    # Bounds BEFORE work: the key COUNT is checked first, by size alone,
    # so no number of unknown keys is ever walked, sorted or formatted;
    # then every key's exact type and length, before any key is compared,
    # looked up or formatted by the closed-key check.
    if len(value) != len(RECEIPT_ATTESTATION_INPUT_KEYS):
        record.fail(PROBLEM_RECEIPT_ATTESTATION,
                    "%s must carry exactly %d fields"
                    % (location, len(RECEIPT_ATTESTATION_INPUT_KEYS)))
    for key in dict.keys(value):
        if type(key) is not str:
            record.fail(PROBLEM_RECEIPT_ATTESTATION,
                        "%s carries a key that is not a string" % location)
        if len(key) > MAX_RECEIPT_ATTESTATION_FIELD_CHARS:
            record.fail(PROBLEM_RECEIPT_ATTESTATION,
                        "%s carries a key longer than %d characters"
                        % (location, MAX_RECEIPT_ATTESTATION_FIELD_CHARS))
    try:
        record.require_closed_keys(value, RECEIPT_ATTESTATION_INPUT_KEYS, location)
        for key in ("receipt_id", "delivery_id", "step", "receipt_state",
                    "step_state"):
            item = value[key]
            if type(item) is not str:
                record.fail(record.PROBLEM_BAD_TYPE,
                            "%s.%s must be a string" % (location, key))
            _require_attestation_str(item, "%s.%s" % (location, key))
        for key in ("receipt_digest_sha256", "parent_authority_digest_sha256",
                    "authorization_digest_sha256"):
            item = value[key]
            if type(item) is not str:
                record.fail(record.PROBLEM_BAD_TYPE,
                            "%s.%s must be a string" % (location, key))
            record.require_hex(item, "%s.%s" % (location, key), 64)
    except record.MissionError as exc:
        record.fail(PROBLEM_RECEIPT_ATTESTATION, "%s: %s" % (exc.problem, exc))
    return dict((key, value[key]) for key in RECEIPT_ATTESTATION_INPUT_KEYS)


def validate_receipt_attestation(value, location):
    """The stored marker's closed, bounded, typed shape. The bindings it
    must satisfy against the ledger, the Mission and its authorizations
    are checked by ``state_validation`` and the store."""
    if type(value) is not dict:
        record.fail(PROBLEM_RECEIPT_ATTESTATION,
                    "%s must be a plain object; a present marker is never null"
                    % location)
    if len(value) != len(RECEIPT_ATTESTATION_KEYS):
        record.fail(PROBLEM_RECEIPT_ATTESTATION,
                    "%s must carry exactly %d fields"
                    % (location, len(RECEIPT_ATTESTATION_KEYS)))
    for key in dict.keys(value):
        if type(key) is not str or len(key) > MAX_RECEIPT_ATTESTATION_FIELD_CHARS:
            record.fail(PROBLEM_RECEIPT_ATTESTATION,
                        "%s carries a key that is not a bounded string" % location)
    try:
        record.require_closed_keys(value, RECEIPT_ATTESTATION_KEYS, location)
        for key in ("delivery_id", "step", "receipt_state", "step_state"):
            _require_attestation_str(value[key], "%s.%s" % (location, key))
        record.require_hex(value["parent_authority_digest_sha256"],
                           location + ".parent_authority_digest_sha256", 64)
        record.require_id(value["authorization_id"], record.AUTHORIZATION_ID_PREFIX,
                          location + ".authorization_id")
        record.require_hex(value["authorization_digest_sha256"],
                           location + ".authorization_digest_sha256", 64)
    except record.MissionError as exc:
        record.fail(PROBLEM_RECEIPT_ATTESTATION, "%s: %s" % (exc.problem, exc))
    return value


def new_attested_artifact(artifact_id, attestation, authorization_id, recorded_at,
                          provenance, operation_id, sequence):
    """The artifact an ``attest_delivery_receipt`` operation records: a
    delivery-receipt reference (the receipt's reference as the locator,
    the receipt's own content digest as the artifact digest, available,
    a VERIFICATION artifact under no contract key, deriving from nothing)
    carrying the closed marker. Every field is derived from the validated
    input and the resolved authorization; none is free."""
    artifact = new_artifact(
        artifact_id, None, record.ARTIFACT_ROLE_VERIFICATION,
        LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, attestation["receipt_id"],
        attestation["receipt_digest_sha256"], True, [], recorded_at, provenance,
        operation_id, sequence)
    artifact[ARTIFACT_MARKER_RECEIPT_ATTESTATION] = {
        "delivery_id": attestation["delivery_id"],
        "step": attestation["step"],
        "receipt_state": attestation["receipt_state"],
        "step_state": attestation["step_state"],
        "parent_authority_digest_sha256": attestation["parent_authority_digest_sha256"],
        "authorization_id": authorization_id,
        "authorization_digest_sha256": attestation["authorization_digest_sha256"],
    }
    return artifact


def has_receipt_attestation(artifact):
    """Whether the marker KEY is present: the one test the validators use
    to decide the producing kind. A present key with a null value is not
    absence — it is a malformed marker and refuses in validation."""
    return ARTIFACT_MARKER_RECEIPT_ATTESTATION in artifact


def receipt_attestation_of(artifact):
    """The artifact's stored marker on a VALIDATED record, or None for a
    generic or legacy artifact (key absent). Presence of the KEY decides,
    never a locator kind; on a validated record a present key is always
    the closed marker, because a present null refuses on load and save."""
    if not has_receipt_attestation(artifact):
        return None
    return artifact[ARTIFACT_MARKER_RECEIPT_ATTESTATION]


def attestation_input_of(artifact):
    """The attesting operation's input, rebuilt from the attested
    artifact (the inverse of ``new_attested_artifact``), for the
    invocation-digest re-derivation."""
    marker = artifact[ARTIFACT_MARKER_RECEIPT_ATTESTATION]
    return {
        "receipt_id": artifact["locator"],
        "receipt_digest_sha256": artifact["content_digest_sha256"],
        "delivery_id": marker["delivery_id"],
        "step": marker["step"],
        "receipt_state": marker["receipt_state"],
        "step_state": marker["step_state"],
        "parent_authority_digest_sha256": marker["parent_authority_digest_sha256"],
        "authorization_digest_sha256": marker["authorization_digest_sha256"],
    }


def receipt_effect_completed(attestation):
    """Structural validity is not success: a completed effect is the
    pinned succeeded RECEIPT state under the pinned succeeded STEP state,
    the same condition the delivery layer's seam reports as
    ``succeeded``; the stored answer can never be more positive than the
    seam's."""
    return (attestation["receipt_state"] == RECEIPT_STATE_SUCCEEDED
            and attestation["step_state"] == STEP_STATE_SUCCEEDED)


def attested_artifacts(state):
    return [a for a in state["artifacts"] if has_receipt_attestation(a)]


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
