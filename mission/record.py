"""Mission identity grammar, proposal schema, lifecycle table, and the
authenticated-context / provenance shapes shared by every Mission record.

Everything here follows the repository's authority-record conventions:
closed key sets, every key required, hard bounds as module constants
never derived from input, distinct ``mission_*`` problem codes, and
refuse-never-repair. Nothing here reads or writes durable state.

Identity. Every DI-owned identifier is ``<prefix>-`` plus 32 lowercase
hex characters: ``mn-`` Mission, ``mq-`` request (creation idempotency
key), ``md-`` decision, ``ma-`` authorization, ``ml-`` ledger entry.
The prefixes are distinct from each other and from the connector
transport reference prefix ``di-``, so a transport reference can never be mistaken
for a Mission id. The alphabet is ``[a-z0-9-]`` and the length is 35,
which is exactly what the delivery record's identifier grammar
already accepts: a Mission id fits its existing optional parent slot
with no schema change. Minting is injectable for determinism and is
never derived from any provider, model, or orchestration identifier.

Proposal. A closed record of objective, target context, an exact
repository identity when applicable (an already-canonical GitHub
repository URL validated by ``workflow_authority.canonical``; ``null``
otherwise), requested scope text, the requested consequential action
scope (a sorted, duplicate-free subset of a closed vocabulary), and an
optional delivery target from a closed vocabulary. The proposal digest
is the canonical JSON digest of the validated proposal CONTENT ONLY:
no timestamp and no DI-minted id is part of it, so a retry carrying a
fresh receive time still matches.

Lifecycle. ``MISSION_STATES`` declares the full vocabulary the later
roadmap needs; ``ALLOWED_TRANSITIONS`` wires the decision transitions
and (Task 8 increment 2) the run transitions ``RUN_TRANSITIONS``:
``AUTHORIZED -> RUNNING`` only from a recorded read-only observation of
the bound target, never from a recorded intent and never from a model
claim (approved still does not imply running), and the terminal
transitions the run uses (``-> BLOCKED``, ``-> COMPLETED`` from RUNNING
only, ``-> CANCELLED``). ``TERMINAL_STATES`` are terminal: no decision,
state operation or run write applies to them, except completing the
cancel record whose own write made the Mission CANCELLED. ``CLOSED``
stays unreachable. A withdrawn proposal (Task 8, see
``mission.manifest``) is NOT a lifecycle state: its Mission stays
``AWAITING_DECISION`` and carries a withdrawal marker that the decision
path consults as a precondition.

Authenticated context and provenance. ``AuthenticatedContext`` is what
a transport adapter builds from its OWN authenticated state and hands
in per request. ``provenance_record`` turns it into the closed
provenance block stored on every revision, decision and authorization.
The block states only what is known and carries
``human_identity_proof: null`` with a ``proof`` fixed by its principal
kind: ``transport_credential_only`` for the authenticated kinds,
``none_unauthenticated`` and ``operator_attested_not_independently_verified``
for the two kinds below.

The unauthenticated kind (Task 8). ``unauthenticated_local_caller``
names a local caller this layer cannot authenticate at all: the context
carries a fixed transport label and a fixed reference, never anything
the caller supplied, and its provenance block records
``proof: "none_unauthenticated"``, so a well-formed context object of
this kind is never read as transport-credential or human proof. It is
PROPOSAL-ONLY, and that is enforced here in the core, not by any entry
point: ``require_authenticated_context`` refuses it with
``mission_unauthenticated_principal`` wherever an authenticated kind is
required (a decision envelope, a decision or state-operation id
reservation, a state operation), and ``require_decision_provenance``
refuses it on a stored decision or authorization, so an authorization
whose ``human_principal`` is of this kind cannot be issued, loaded, or
validated. The refusal names what is missing: an authenticated
per-message principal assertion this layer can verify, and an exact
human approval event a model cannot mint. A verified principal alone
would still not be enough: it establishes whose account, not whose
intent.

The operator-attested kind (Task 8, user decision). ``operator_attested_relay``
records an APPROVE that an Outer Operator ATTESTS it relayed from the
human's explicit chat reply, under the user's declared trust in that
Operator. Its provenance proof is ``operator_attested_not_independently_verified``.
It travels only in an ``OperatorAttestedContext``, never an
``AuthenticatedContext``; it is NOT authenticated, is not in
``AUTHENTICATED_PRINCIPAL_KINDS``, and
carries an APPROVE only (``APPROVE_ONLY_PRINCIPAL_KINDS``): no EDIT, no
DENY, no state operation. ``OPERATOR_ATTESTED_RESIDUAL_RISK`` states the
residual risk: a mistaken or malicious same-user operator or local process
could fabricate it. The decision path enforces, in the core, that such an
APPROVE binds the exact current proposal digest, the exact requested scope
and targets, and a bounded expiry, for a proposal the unauthenticated local
caller kind made.

Proof contract (Mission State). ``proof_contract`` is the ONE
additive-optional proposal key. It holds the whole consequential
contract of a Mission: the proof requirements, the required artifacts
(each content-identified by an expected digest), the required
prerequisite slots (each naming its target by exact identity or by the
single content-identified eligibility condition), the required resource
readiness keys with their staleness bounds, the degradation policy, and
the continuation budget. Because it lives INSIDE the proposal it is
covered by ``proposal_digest``: approving revision N approves this exact
contract, an EDIT that changes it produces revision N+1 with a different
digest and revokes the authority of revision N, and a fresh APPROVE is
required. No service parameter supplies, widens, or weakens it.

Legacy preservation: when ``proof_contract`` is absent or ``null`` it is
OMITTED from the normalized proposal, so a proposal shaped as before
this key existed normalizes and digests byte for byte as it always did.
``PROPOSAL_KEYS`` is unchanged and still names exactly the six required
keys; ``PROPOSAL_OPTIONAL_KEYS`` names the one optional key.

The contract's identity prefixes (``mt``/``mv``/``mf``/``mb``/``mc``/
``mk``/``mx``/``mo``) are registered here so every Mission State
identifier shares the one identity grammar and minter.
"""

import secrets
from dataclasses import dataclass
from typing import Optional

from workflow_authority import canonical
from workflow_authority.digest import json_digest

SCHEMA_VERSION = 1

# -- identity ---------------------------------------------------------

MISSION_ID_PREFIX = "mn"
REQUEST_ID_PREFIX = "mq"
DECISION_ID_PREFIX = "md"
AUTHORIZATION_ID_PREFIX = "ma"
LEDGER_ENTRY_ID_PREFIX = "ml"
# Mission State identities (Task 5). Proof requirements have no id
# prefix: they are addressed by their contract ``key`` inside the approved
# proposal, and a registered prefix nothing mints would only invite a
# caller-chosen id.
PROOF_CONTRACT_ID_PREFIX = "mt"
EVIDENCE_ID_PREFIX = "mv"
ARTIFACT_ID_PREFIX = "mf"
BLOCKER_ID_PREFIX = "mb"
CLAIM_ID_PREFIX = "mc"
CHECKPOINT_ID_PREFIX = "mk"
DEPENDENCY_ID_PREFIX = "mx"
STATE_OPERATION_ID_PREFIX = "mo"
ID_PREFIXES = (
    MISSION_ID_PREFIX, REQUEST_ID_PREFIX, DECISION_ID_PREFIX,
    AUTHORIZATION_ID_PREFIX, LEDGER_ENTRY_ID_PREFIX,
    PROOF_CONTRACT_ID_PREFIX, EVIDENCE_ID_PREFIX, ARTIFACT_ID_PREFIX, BLOCKER_ID_PREFIX, CLAIM_ID_PREFIX,
    CHECKPOINT_ID_PREFIX, DEPENDENCY_ID_PREFIX, STATE_OPERATION_ID_PREFIX,
)
# Exact-value pinned in the bound-constant table.
ID_HEX_CHARS = 32
_ID_HEX_ALPHABET = frozenset("0123456789abcdef")

# -- vocabularies -----------------------------------------------------

ACTION_SCOPE_REPOSITORY_READ = "repository_read"
ACTION_SCOPE_ENGINEERING_CHANGE = "engineering_change"
ACTION_SCOPE_VERIFICATION_RUN = "verification_run"
ACTION_SCOPES = (
    ACTION_SCOPE_ENGINEERING_CHANGE, ACTION_SCOPE_REPOSITORY_READ,
    ACTION_SCOPE_VERIFICATION_RUN,
)

# ``github_pr`` names a PARENT Mission delivery scope only. Approving it
# authorizes the Mission to later seek a verified pull-request delivery;
# it is not commit, push, PR creation, merge, or execution permission,
# and nothing in this package performs a Git action.
DELIVERY_TARGET_GITHUB_PR = "github_pr"
DELIVERY_TARGETS = (DELIVERY_TARGET_GITHUB_PR,)

STATE_AWAITING_DECISION = "AWAITING_DECISION"
STATE_AUTHORIZED = "AUTHORIZED"
STATE_DENIED = "DENIED"
STATE_RUNNING = "RUNNING"
STATE_BLOCKED = "BLOCKED"
STATE_COMPLETED = "COMPLETED"
STATE_CLOSED = "CLOSED"
STATE_CANCELLED = "CANCELLED"
MISSION_STATES = (
    STATE_AWAITING_DECISION, STATE_AUTHORIZED, STATE_DENIED, STATE_RUNNING,
    STATE_BLOCKED, STATE_COMPLETED, STATE_CLOSED, STATE_CANCELLED,
)
# The states a Mission can actually hold: the only ones a wired
# transition can reach. A stored record in any other state is malformed,
# because nothing here can produce it. Task 8 increment 2 (intentional
# invariant change): the run states became reachable; CLOSED did not.
REACHABLE_STATES = (STATE_AWAITING_DECISION, STATE_AUTHORIZED, STATE_DENIED,
                    STATE_RUNNING, STATE_BLOCKED, STATE_COMPLETED,
                    STATE_CANCELLED)
# The run transitions (Task 8 increment 2), exactly the ones the run uses:
# RUNNING is reachable from AUTHORIZED only, COMPLETED from RUNNING only.
RUN_TRANSITIONS = frozenset((
    (STATE_AUTHORIZED, STATE_RUNNING),
    (STATE_AUTHORIZED, STATE_BLOCKED),
    (STATE_AUTHORIZED, STATE_CANCELLED),
    (STATE_RUNNING, STATE_COMPLETED),
    (STATE_RUNNING, STATE_BLOCKED),
    (STATE_RUNNING, STATE_CANCELLED),
))
TERMINAL_STATES = (STATE_BLOCKED, STATE_COMPLETED, STATE_CANCELLED)
ALLOWED_TRANSITIONS = {
    STATE_AWAITING_DECISION: frozenset((STATE_AUTHORIZED, STATE_DENIED)),
    STATE_AUTHORIZED: frozenset((STATE_AWAITING_DECISION, STATE_RUNNING,
                                 STATE_BLOCKED, STATE_CANCELLED)),
    STATE_DENIED: frozenset((STATE_AWAITING_DECISION,)),
    STATE_RUNNING: frozenset((STATE_COMPLETED, STATE_BLOCKED,
                              STATE_CANCELLED)),
    STATE_BLOCKED: frozenset(),
    STATE_COMPLETED: frozenset(),
    STATE_CLOSED: frozenset(),
    STATE_CANCELLED: frozenset(),
}

# -- the run record (Task 8 increment 2) --------------------------------
# What happened after approval, as distinct durable facts: the INTENT
# (recorded before any effect), the RECEIPT (an effect returned and named
# a target), the observation-backed transition to RUNNING, the
# VERIFICATION DI decided, PAUSES and the CANCEL. Every reason below is a
# closed vocabulary; nothing is free text. Who may write them: only an
# AUTHENTICATED principal kind (``require_authenticated_context``); the
# unauthenticated local caller and the operator-attested relay are
# refused on every run write. The intent's baseline commit is
# ``observed_baseline_commit_sha``: OBSERVED by DI at intent time, never
# human-approved. The human approved a scope (repository, actions,
# delivery targets), not a commit; the observed baseline cannot widen
# that scope, and it is only the starting content the run is measured
# against.
RUN_REASON_OBSERVED_RUNNING = "observed_running"
# The bound target was first observed already stopped: it is present and
# ran, but DI never saw it mid-run. Recorded as its own reason.
RUN_REASON_OBSERVED_AFTER_STOP = "observed_after_stop"
RUN_REASON_VERIFIED = "verified_completion"
CANCEL_BEFORE_INTENT = "cancelled_before_intent"
CANCEL_AFTER_INTENT_TARGET_UNKNOWN = "cancelled_after_intent_target_unknown"
CANCEL_AFTER_OBSERVED_RUNNING = "cancelled_after_observed_running"
CANCEL_AFTER_TARGET_TERMINATED = "cancelled_after_target_terminated"
CANCEL_ACHIEVED_STATES = (
    CANCEL_BEFORE_INTENT, CANCEL_AFTER_INTENT_TARGET_UNKNOWN,
    CANCEL_AFTER_OBSERVED_RUNNING, CANCEL_AFTER_TARGET_TERMINATED,
)
# What control the cancel achieved, and what quiescence DI can establish.
# Quiescence is never claimed beyond its scope: an owned process group
# DI reaped and then found empty, or a target task a supported read-only
# observation reports stopped. Anything else is ``unproven``.
CANCEL_CONTROLS = (
    "not_applicable", "owned_group_reaped", "owned_group_already_gone",
    "unavailable_no_owned_group", "refused_ownership_unverified",
    "failed_group_still_alive", "interrupted_not_resignalled",
)
CANCEL_QUIESCENCE_NOTHING_STARTED = "nothing_started"
CANCEL_QUIESCENCE_OWNED_GROUP_REAPED = "owned_group_reaped"
CANCEL_QUIESCENCE_TASK_OBSERVED_STOPPED = "task_observed_stopped"
CANCEL_QUIESCENCE_UNPROVEN = "unproven"
CANCEL_QUIESCENCE = (
    CANCEL_QUIESCENCE_NOTHING_STARTED, CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
    CANCEL_QUIESCENCE_TASK_OBSERVED_STOPPED, CANCEL_QUIESCENCE_UNPROVEN,
)
RUN_IDENTITY_SOURCES = ("start_result", "reconciliation")
# The verification conjunction, in its fixed evaluation order, and the
# code each failing conjunct stops the Mission with.
# ``target_succeeded`` is separate from ``target_stopped`` on purpose: a
# target that stopped in a failure state, even one with a review
# artifact, never verifies. ``result_bound`` holds only for a reported
# result bound to the observed target, its canonical review artifact and
# a recoverable result artifact.
VERIFY_CONJUNCTS = (
    ("result_reported", "verify_result_not_reported"),
    ("evidence_complete", "verify_evidence_incomplete"),
    ("evidence_valid", "verify_evidence_invalid"),
    ("target_identity", "verify_target_identity_mismatch"),
    ("target_stopped", "verify_target_not_stopped"),
    ("target_succeeded", "verify_target_not_succeeded"),
    ("review_approve", "verify_review_not_approve"),
    ("result_bound", "verify_result_not_bound"),
    ("baseline_unmoved", "verify_baseline_moved"),
    ("surface_receipt_present", "verify_surface_receipt_missing"),
    ("surface_unchanged", "verify_surface_changed"),
    ("delivery_authority_none", "verify_delivery_authority_not_none"),
)
# The approved proof-contract requirement a run's result is bound to:
# VERIFIED needs an ACCEPTED VERIFICATION_RECORD evidence under it.
RUN_RESULT_REQUIREMENT_KEY = "run_result"


def verification_outcome(conjunct_holds):
    """``(verified, failed_code)``: VERIFIED exactly when every conjunct
    is ``True``; otherwise the code of the FIRST conjunct that is not."""
    for name, code in VERIFY_CONJUNCTS:
        if conjunct_holds.get(name) is not True:
            return False, code
    return True, None
RECONCILE_STOP_REASONS = (
    "reconcile_no_match", "reconcile_multiple_matches",
    "reconcile_conflicting_identity", "reconcile_degraded",
    # The workspace's own task record does not name THIS Mission's intent
    # (or started before it): an old or foreign child, never adopted.
    "reconcile_unproven_association",
)
RUN_STOP_REASONS = RECONCILE_STOP_REASONS + tuple(
    code for _, code in VERIFY_CONJUNCTS)
RUN_REASONS_BY_TARGET = {
    STATE_RUNNING: (RUN_REASON_OBSERVED_RUNNING, RUN_REASON_OBSERVED_AFTER_STOP),
    STATE_COMPLETED: (RUN_REASON_VERIFIED,),
    STATE_BLOCKED: RUN_STOP_REASONS,
    STATE_CANCELLED: CANCEL_ACHIEVED_STATES,
}
# The one action scope a run needs: the started engineering target can
# change its workspace, and nothing here can confine it to reading.
RUN_REQUIRED_ACTION_SCOPE = (ACTION_SCOPE_ENGINEERING_CHANGE,)
# Hard bounds, never derived from input. Exact-value pinned.
MAX_RUN_PAUSES = 16
MAX_RUN_PATH_CHARS = 4096
# The pending-proof record (round-15 state truth): a run whose stopped,
# succeeded target passed every verification conjunct but whose approved
# contract is not yet satisfied is NOT terminal. It stays RUNNING with a
# durable record of the stopped-target observation and the evaluator's
# blocker codes, so its obligations can be met and the SAME consumed run
# verified again, a bounded number of times, without a new dispatch.
MAX_VERIFICATION_ATTEMPTS = 16
MAX_PENDING_PROOF_BLOCKERS = 16
MAX_BLOCKER_DETAIL_CHARS = 2000
MAX_OBSERVED_STATUS_CHARS = 64

PRINCIPAL_KIND_CONNECTOR_CREDENTIAL = "configured_connector_credential_ordinal"
PRINCIPAL_KIND_LOCAL_PROCESS_USER = "local_process_user"
# Task 8: a local caller this layer cannot authenticate. Proposal-only;
# see the module docstring and ``require_authenticated_context``.
PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER = "unauthenticated_local_caller"
# The kinds a transport authenticated from its own state. Only these can
# reserve a state-operation id, run a state operation, carry an EDIT or a
# DENY, or record a revision after the first. A decision id, an APPROVE and
# an authorization's principal may ALSO be of the operator-attested kind
# below, which is not authenticated.
AUTHENTICATED_PRINCIPAL_KINDS = (
    PRINCIPAL_KIND_CONNECTOR_CREDENTIAL, PRINCIPAL_KIND_LOCAL_PROCESS_USER,
)
# Task 8 (user decision): an APPROVE that an Outer Operator ATTESTS it
# relayed from the human's explicit chat reply. This layer cannot verify that
# the human sent it; the kind and its proof say so. It is NOT authenticated
# and is never in AUTHENTICATED_PRINCIPAL_KINDS.
PRINCIPAL_KIND_OPERATOR_ATTESTED = "operator_attested_relay"
PRINCIPAL_KINDS = AUTHENTICATED_PRINCIPAL_KINDS + (
    PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER,
    PRINCIPAL_KIND_OPERATOR_ATTESTED,
)
# The kinds an ``AuthenticatedContext`` may carry: exactly the set it carried
# before Task 8's operator-attested kind existed. The operator-attested kind
# travels only in an ``OperatorAttestedContext``.
ADAPTER_CONTEXT_PRINCIPAL_KINDS = AUTHENTICATED_PRINCIPAL_KINDS + (
    PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER,
)
# Kinds that may reserve a decision id and carry an APPROVE decision, and
# nothing else: no EDIT, no DENY, no state operation.
APPROVE_ONLY_PRINCIPAL_KINDS = (PRINCIPAL_KIND_OPERATOR_ATTESTED,)
PROOF_TRANSPORT_CREDENTIAL_ONLY = "transport_credential_only"
PROOF_NONE_UNAUTHENTICATED = "none_unauthenticated"
PROOF_OPERATOR_ATTESTED = "operator_attested_not_independently_verified"
OPERATOR_ATTESTED_RESIDUAL_RISK = (
    "operator-attested: the Outer Operator reports that the human replied"
    " with this approval; this layer did not verify the human, and a mistaken"
    " or malicious same-user operator or local process could fabricate it"
)
# The longest an operator-attested approval may stay valid. Exact-value
# pinned; equal to the established human-scale approval window.
MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS = 900
# The MAXIMUM PERMITTED withdrawal-key lifetime: seven days. Exact-value
# pinned. It only CAPS the bound: manifest validation refuses a recorded
# ``withdrawal_key_expires_at`` later than the Mission's creation plus this.
# The deadline actually ENFORCED is the key's ORIGINAL expiry, the one the
# proposer minted and passed with its digest, which Mission Core binds
# immutably at creation (never recomputed from the creation time, never
# extended); ``MissionService.withdraw_proposal`` itself refuses a first
# withdrawal at or after it, not any entry point.
WITHDRAWAL_KEY_VALIDITY_SECONDS = 604800
# The proof a provenance block records is fixed by its principal kind.
_PROOF_BY_PRINCIPAL_KIND = {
    PRINCIPAL_KIND_CONNECTOR_CREDENTIAL: PROOF_TRANSPORT_CREDENTIAL_ONLY,
    PRINCIPAL_KIND_LOCAL_PROCESS_USER: PROOF_TRANSPORT_CREDENTIAL_ONLY,
    PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER: PROOF_NONE_UNAUTHENTICATED,
    PRINCIPAL_KIND_OPERATOR_ATTESTED: PROOF_OPERATOR_ATTESTED,
}
# Why ``human_identity_proof`` is always null, stated per kind so that no
# kind is described with a check it does not have.
_NO_HUMAN_IDENTITY_BY_PRINCIPAL_KIND = {
    PRINCIPAL_KIND_CONNECTOR_CREDENTIAL: "for this kind this layer verifies a"
    " transport credential, never a human identity",
    PRINCIPAL_KIND_LOCAL_PROCESS_USER: "for this kind this layer verifies a"
    " transport credential, never a human identity",
    PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER: "this kind carries no"
    " credential and no human identity",
    PRINCIPAL_KIND_OPERATOR_ATTESTED: "this kind records an Outer Operator's"
    " attestation, which this layer does not independently check, and never a"
    " human identity",
}
# What an unauthenticated caller lacks to decide anything, machine-readable
# and in the order the refusal states them.
DECISION_REQUIREMENTS_MISSING = (
    "authenticated_per_message_principal_assertion",
    "exact_human_approval_event_not_mintable_by_a_model",
)
UNAUTHENTICATED_REFUSAL_DETAIL = (
    "an unauthenticated local caller may only propose: it carries no"
    " authenticated per-message principal assertion this layer can verify"
    " and no exact human approval event a model cannot mint. A verified"
    " principal alone would still be insufficient: it establishes whose"
    " account, not whose intent"
)

REFERENCE_KIND_REQUEST = "request"
REFERENCE_KIND_DECISION = "decision"
REFERENCE_KIND_STATE_OPERATION = "state_operation"
REFERENCE_KINDS = (REFERENCE_KIND_REQUEST, REFERENCE_KIND_DECISION,
                   REFERENCE_KIND_STATE_OPERATION)
# The reference kind binds the id prefix a provenance block may carry.
_REFERENCE_PREFIXES = {
    REFERENCE_KIND_REQUEST: REQUEST_ID_PREFIX,
    REFERENCE_KIND_DECISION: DECISION_ID_PREFIX,
    REFERENCE_KIND_STATE_OPERATION: STATE_OPERATION_ID_PREFIX,
}

# -- proof contract vocabularies --------------------------------------

# Evidence kinds. The table below declares which kinds can EVER satisfy
# a requirement. NARRATIVE_CLAIM and PROCESS_EXIT may be recorded and can
# never be accepted as satisfying proof; a contract cannot even declare
# them acceptable, because ``evidence_kinds`` must be a subset of
# ``SATISFYING_EVIDENCE_KINDS``.
EVIDENCE_KIND_ARTIFACT_DIGEST = "ARTIFACT_DIGEST"
EVIDENCE_KIND_VERIFICATION_RECORD = "VERIFICATION_RECORD"
EVIDENCE_KIND_EXTERNAL_ATTESTATION = "EXTERNAL_ATTESTATION"
EVIDENCE_KIND_NARRATIVE_CLAIM = "NARRATIVE_CLAIM"
EVIDENCE_KIND_PROCESS_EXIT = "PROCESS_EXIT"
EVIDENCE_KINDS = (
    EVIDENCE_KIND_ARTIFACT_DIGEST, EVIDENCE_KIND_EXTERNAL_ATTESTATION,
    EVIDENCE_KIND_NARRATIVE_CLAIM, EVIDENCE_KIND_PROCESS_EXIT,
    EVIDENCE_KIND_VERIFICATION_RECORD,
)
SATISFYING_EVIDENCE_KINDS = (
    EVIDENCE_KIND_ARTIFACT_DIGEST, EVIDENCE_KIND_EXTERNAL_ATTESTATION,
    EVIDENCE_KIND_VERIFICATION_RECORD,
)
NON_SATISFYING_EVIDENCE_KINDS = (
    EVIDENCE_KIND_NARRATIVE_CLAIM, EVIDENCE_KIND_PROCESS_EXIT,
)

ARTIFACT_ROLE_ORIGINAL_INPUT = "ORIGINAL_INPUT"
ARTIFACT_ROLE_PRODUCED = "PRODUCED"
ARTIFACT_ROLE_VERIFICATION = "VERIFICATION"
ARTIFACT_ROLES = (
    ARTIFACT_ROLE_ORIGINAL_INPUT, ARTIFACT_ROLE_PRODUCED,
    ARTIFACT_ROLE_VERIFICATION,
)

DEPENDENCY_KIND_MISSION = "MISSION"
DEPENDENCY_KIND_RESOURCE = "RESOURCE"
DEPENDENCY_KINDS = (DEPENDENCY_KIND_MISSION, DEPENDENCY_KIND_RESOURCE)

# Prerequisite target forms (R-18). A required prerequisite is identified
# by the approved contract, never chosen afterwards. ELIGIBILITY exists
# only because a prerequisite Mission's id may not be known at proposal
# time; its condition vocabulary is deliberately ONE content-identified
# member and admits no free-form predicate and no caller parameter.
TARGET_FORM_EXACT_MISSION = "EXACT_MISSION"
TARGET_FORM_EXACT_RESOURCE = "EXACT_RESOURCE"
TARGET_FORM_ELIGIBILITY = "ELIGIBILITY"
TARGET_FORMS = (
    TARGET_FORM_ELIGIBILITY, TARGET_FORM_EXACT_MISSION,
    TARGET_FORM_EXACT_RESOURCE,
)
ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST = "MISSION_WITH_PROPOSAL_DIGEST"
ELIGIBILITY_CONDITIONS = (ELIGIBILITY_MISSION_WITH_PROPOSAL_DIGEST,)
# Which target forms a dependency kind may carry.
_TARGET_FORMS_BY_KIND = {
    DEPENDENCY_KIND_MISSION: (TARGET_FORM_EXACT_MISSION, TARGET_FORM_ELIGIBILITY),
    DEPENDENCY_KIND_RESOURCE: (TARGET_FORM_EXACT_RESOURCE,),
}

# -- hard bounds, never derived from input ----------------------------

MAX_OBJECTIVE_CHARS = 8000
MAX_TARGET_CONTEXT_CHARS = 4000
MAX_SCOPE_TEXT_CHARS = 4000
MAX_TRANSPORT_CHARS = 64
MAX_PRINCIPAL_REF_CHARS = 128
MAX_SUBJECT_CHARS = 256
# Proof contract bounds. Exact-value pinned.
MAX_PROOF_REQUIREMENTS = 32
MAX_REQUIRED_ARTIFACTS = 64
MAX_REQUIRED_DEPENDENCIES = 32
MAX_REQUIRED_RESOURCE_READINESS = 16
MAX_PERMITTED_BLOCKER_KEYS = 32
MAX_CONTRACT_KEY_CHARS = 128
MAX_REQUIREMENT_DESCRIPTION_CHARS = 2000
# Upper bound on every staleness bound a contract may declare (ten years).
MAX_STALENESS_BOUND_SECONDS = 315360000
MAX_CONTINUATION_ATTEMPTS = 64
MAX_CONTINUATION_CHECKPOINTS = 256

PROPOSAL_KEYS = (
    "objective", "target_context", "repository_url", "requested_scope",
    "requested_action_scope", "requested_delivery_target",
)
PROPOSAL_OPTIONAL_KEYS = ("proof_contract",)
PROOF_CONTRACT_KEYS = (
    "requirements", "required_artifacts", "required_dependencies",
    "required_resource_readiness", "degradation_policy",
    "continuation_budget",
)
PROOF_REQUIREMENT_KEYS = (
    "key", "description", "evidence_kinds", "required_artifact_keys",
    "max_evidence_age_seconds",
)
REQUIRED_ARTIFACT_KEYS = ("key", "role", "expected_content_digest_sha256")
REQUIRED_DEPENDENCY_KEYS = ("key", "kind", "target")
TARGET_KEYS_BY_FORM = {
    TARGET_FORM_EXACT_MISSION: (
        "form", "mission_id", "revision", "proposal_digest_sha256",
    ),
    TARGET_FORM_EXACT_RESOURCE: ("form", "resource_key"),
    TARGET_FORM_ELIGIBILITY: ("form", "condition", "proposal_digest_sha256"),
}
REQUIRED_RESOURCE_READINESS_KEYS = ("resource_key", "max_age_seconds")
DEGRADATION_POLICY_KEYS = ("permitted_blocker_keys",)
CONTINUATION_BUDGET_KEYS = ("max_attempts", "max_checkpoints")
CONTEXT_KEYS = (
    "transport", "principal_kind", "principal_ref", "configured_subject",
)
PROVENANCE_KEYS = CONTEXT_KEYS + (
    "received_at", "reference_kind", "reference_id", "mission_id",
    "revision", "human_identity_proof", "proof",
)

_TRANSPORT_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
# Contract keys (requirement, artifact, dependency slot, resource and
# blocker keys) are opaque bounded identifiers in this alphabet.
_CONTRACT_KEY_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")

# -- problem codes ----------------------------------------------------

PROBLEM_NOT_AN_OBJECT = "mission_not_an_object"
PROBLEM_UNKNOWN_KEY = "mission_unknown_key"
PROBLEM_MISSING_KEY = "mission_missing_key"
PROBLEM_BAD_TYPE = "mission_bad_type"
PROBLEM_BAD_VALUE = "mission_bad_value"
PROBLEM_TOO_LARGE = "mission_too_large"
PROBLEM_ID_GRAMMAR = "mission_id_grammar"
PROBLEM_REPOSITORY_IDENTITY = "mission_repository_identity"
PROBLEM_ACTION_SCOPE = "mission_action_scope"
PROBLEM_DELIVERY_TARGET = "mission_delivery_target"
PROBLEM_UNKNOWN_STATE = "mission_unknown_state"
PROBLEM_INVALID_TRANSITION = "mission_invalid_transition"
PROBLEM_PROVENANCE = "mission_provenance"
PROBLEM_UNAUTHENTICATED_PRINCIPAL = "mission_unauthenticated_principal"
PROBLEM_ATTESTED_APPROVAL = "mission_attested_approval"
# Task 8 increment 2: the run record and the terminal guard.
PROBLEM_MISSION_TERMINAL = "mission_terminal"
PROBLEM_RUN = "mission_run"
PROBLEM_RUN_ALREADY_RECORDED = "mission_run_already_recorded"
PROBLEM_RUN_RECORDED = "mission_run_recorded"
PROBLEM_RUN_PAUSED = "mission_run_paused"
PROBLEM_RUN_WORKSPACE_BOUND = "mission_run_workspace_bound"
PROBLEM_PROOF_CONTRACT = "mission_proof_contract"
# R-20.1: every declared required artifact must be named by a
# requirement and every named one must be declared. A "required"
# artifact nothing references is refused, never silently ignored.
PROBLEM_CONTRACT_INCOHERENT = "mission_state_contract_incoherent"


class MissionError(Exception):
    """A Mission Core refusal; ``problem`` is a distinct ``mission_*`` code
    and the message is actionable."""

    def __init__(self, message, problem):
        super(MissionError, self).__init__(message)
        self.problem = problem


def fail(problem, message):
    raise MissionError(message, problem)


# -- identity ---------------------------------------------------------


def mint_id(prefix):
    """A fresh DI-owned identifier for ``prefix``."""
    if prefix not in ID_PREFIXES:
        fail(PROBLEM_ID_GRAMMAR, "unknown id prefix %r" % (prefix,))
    return "%s-%s" % (prefix, secrets.token_hex(ID_HEX_CHARS // 2))


def id_problem(value, prefix):
    """None when ``value`` is a well-formed id for ``prefix``; else why."""
    if not isinstance(value, str):
        return "must be a string"
    expected = len(prefix) + 1 + ID_HEX_CHARS
    if len(value) != expected:
        return "must be exactly %d characters" % expected
    if value[:len(prefix) + 1] != prefix + "-":
        return "must start with %r" % (prefix + "-",)
    if any(ch not in _ID_HEX_ALPHABET for ch in value[len(prefix) + 1:]):
        return "must end in %d lowercase hex characters" % ID_HEX_CHARS
    return None


def require_id(value, prefix, location):
    reason = id_problem(value, prefix)
    if reason is not None:
        fail(PROBLEM_ID_GRAMMAR, "%s %s" % (location, reason))
    return value


# -- generic validators -----------------------------------------------


def require_dict(value, location):
    if not isinstance(value, dict):
        fail(PROBLEM_NOT_AN_OBJECT,
             "%s must be an object, not %s" % (location, type(value).__name__))
    return value


def require_closed_keys(value, allowed, location, optional=()):
    """Every key in ``allowed`` present, no key outside ``allowed`` plus
    ``optional``. Task 4 records pass no ``optional`` and are unchanged."""
    unknown = sorted(set(value) - set(allowed) - set(optional))
    if unknown:
        fail(PROBLEM_UNKNOWN_KEY,
             "%s has unknown keys: %s (the key set is closed)"
             % (location, ", ".join(repr(key) for key in unknown)))
    missing = sorted(set(allowed) - set(value))
    if missing:
        fail(PROBLEM_MISSING_KEY,
             "%s is missing required keys: %s"
             % (location, ", ".join(repr(key) for key in missing)))


def require_str(value, location, max_chars, allow_empty=False):
    if not isinstance(value, str):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a string, not %s" % (location, type(value).__name__))
    if not allow_empty and not value:
        fail(PROBLEM_BAD_VALUE, "%s must be non-empty" % location)
    if len(value) > max_chars:
        fail(PROBLEM_TOO_LARGE,
             "%s is %d characters; the hard bound is %d and the record is"
             " refused, not truncated" % (location, len(value), max_chars))
    return value


def require_optional_str(value, location, max_chars):
    if value is None:
        return None
    return require_str(value, location, max_chars)


def require_int(value, location, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        fail(PROBLEM_BAD_TYPE,
             "%s must be an integer (bool is not accepted), not %r"
             % (location, value))
    if minimum is not None and value < minimum:
        fail(PROBLEM_BAD_VALUE,
             "%s must be >= %d; got %d" % (location, minimum, value))
    return value


def require_timestamp(value, location):
    return require_int(value, location, minimum=0)


def require_optional_timestamp(value, location):
    if value is None:
        return None
    return require_timestamp(value, location)


def require_bool(value, location):
    if not isinstance(value, bool):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a boolean, not %s" % (location, type(value).__name__))
    return value


def require_hex(value, location, length):
    require_str(value, location, max_chars=length)
    if len(value) != length or any(ch not in _ID_HEX_ALPHABET for ch in value):
        fail(PROBLEM_BAD_VALUE,
             "%s must be exactly %d lowercase hex characters" % (location, length))
    return value


def require_member(value, allowed, location, problem=PROBLEM_BAD_VALUE):
    if not isinstance(value, str) or value not in allowed:
        fail(problem,
             "%s must be one of %s; got %r (unknown values fail closed)"
             % (location, ", ".join(allowed), value))
    return value


def require_sorted_subset(value, allowed, location, problem, allow_empty):
    """A list that is a sorted, duplicate-free subset of ``allowed``."""
    if not isinstance(value, list):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a list, not %s" % (location, type(value).__name__))
    for index, item in enumerate(value):
        require_member(item, allowed, "%s[%d]" % (location, index), problem)
    if len(set(value)) != len(value):
        fail(problem, "%s carries a duplicate entry" % location)
    if not allow_empty and not value:
        fail(problem, "%s must name at least one entry" % location)
    return sorted(value)


# -- proposal ---------------------------------------------------------


def validate_proposal(value, location="proposal"):
    """The validated, normalized proposal (a new dict), or refuse.

    Normalization is limited to sorting the action-scope list and the
    deterministic normalization of a present ``proof_contract``; a
    repository URL that is not already canonical is refused, never
    repaired. An absent or ``null`` ``proof_contract`` is OMITTED from
    the result, so a proposal without one normalizes and digests exactly
    as it did before the key existed (R-5.2).
    """
    require_dict(value, location)
    require_closed_keys(value, PROPOSAL_KEYS, location,
                        optional=PROPOSAL_OPTIONAL_KEYS)
    clean = {
        "objective": require_str(
            value["objective"], location + ".objective", MAX_OBJECTIVE_CHARS
        ),
        "target_context": require_str(
            value["target_context"], location + ".target_context",
            MAX_TARGET_CONTEXT_CHARS, allow_empty=True,
        ),
        "repository_url": _validate_repository_url(
            value["repository_url"], location + ".repository_url"
        ),
        "requested_scope": require_str(
            value["requested_scope"], location + ".requested_scope",
            MAX_SCOPE_TEXT_CHARS,
        ),
        "requested_action_scope": require_sorted_subset(
            value["requested_action_scope"], ACTION_SCOPES,
            location + ".requested_action_scope", PROBLEM_ACTION_SCOPE,
            allow_empty=False,
        ),
        "requested_delivery_target": _validate_delivery_target(
            value["requested_delivery_target"],
            location + ".requested_delivery_target",
        ),
    }
    if value.get("proof_contract") is not None:
        clean["proof_contract"] = validate_proof_contract(
            value["proof_contract"], location + ".proof_contract"
        )
    return clean


def _validate_repository_url(value, location):
    if value is None:
        return None
    if not isinstance(value, str):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a string or null, not %s" % (location, type(value).__name__))
    try:
        target = canonical.canonicalize_repository_url(value)
    except canonical.CanonicalizationError as exc:
        fail(PROBLEM_REPOSITORY_IDENTITY,
             "%s is not a canonical repository identity (%s: %s)"
             % (location, exc.problem, exc))
    if target.canonical_url != value:
        fail(PROBLEM_REPOSITORY_IDENTITY,
             "%s must already be canonical; it is refused, not repaired"
             % location)
    return value


def _validate_delivery_target(value, location):
    if value is None:
        return None
    return require_member(value, DELIVERY_TARGETS, location,
                          PROBLEM_DELIVERY_TARGET)


def proposal_digest(value):
    """Canonical JSON sha256 of the validated proposal CONTENT only."""
    return json_digest(validate_proposal(value))


# -- proof contract ---------------------------------------------------


def require_contract_key(value, location):
    """An opaque bounded contract key: ``[a-z0-9_.-]``, 1..MAX chars."""
    require_str(value, location, MAX_CONTRACT_KEY_CHARS)
    if any(ch not in _CONTRACT_KEY_ALPHABET for ch in value):
        fail(PROBLEM_PROOF_CONTRACT,
             "%s must use only [a-z0-9_.-]; got %r" % (location, value))
    return value


def require_bounded_seconds(value, location):
    """A staleness bound: an integer in 1..MAX_STALENESS_BOUND_SECONDS."""
    require_int(value, location, minimum=1)
    if value > MAX_STALENESS_BOUND_SECONDS:
        fail(PROBLEM_TOO_LARGE,
             "%s is %d seconds; the hard bound is %d"
             % (location, value, MAX_STALENESS_BOUND_SECONDS))
    return value


def _require_list(value, location, max_items):
    if not isinstance(value, list):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a list, not %s" % (location, type(value).__name__))
    if len(value) > max_items:
        fail(PROBLEM_TOO_LARGE,
             "%s holds %d entries; the hard bound is %d and the record is"
             " refused, not truncated" % (location, len(value), max_items))
    return value


def _require_keyed_entries(value, location, max_items, key_name, validator):
    """A list of closed objects each carrying a unique contract key under
    ``key_name``; returned sorted by that key (deterministic form)."""
    _require_list(value, location, max_items)
    clean = []
    for index, entry in enumerate(value):
        clean.append(validator(entry, "%s[%d]" % (location, index)))
    keys = [entry[key_name] for entry in clean]
    if len(set(keys)) != len(keys):
        fail(PROBLEM_PROOF_CONTRACT,
             "%s repeats a %s; keys must be unique" % (location, key_name))
    return sorted(clean, key=lambda entry: entry[key_name])


def _require_sorted_keys(value, location, max_items):
    """A sorted, duplicate-free list of contract keys."""
    _require_list(value, location, max_items)
    for index, item in enumerate(value):
        require_contract_key(item, "%s[%d]" % (location, index))
    if len(set(value)) != len(value):
        fail(PROBLEM_PROOF_CONTRACT, "%s carries a duplicate key" % location)
    return sorted(value)


def _validate_requirement(value, location):
    require_dict(value, location)
    require_closed_keys(value, PROOF_REQUIREMENT_KEYS, location)
    kinds = value["evidence_kinds"]
    if not isinstance(kinds, list):
        fail(PROBLEM_BAD_TYPE, "%s.evidence_kinds must be a list" % location)
    for index, kind in enumerate(kinds):
        if kind not in EVIDENCE_KINDS:
            fail(PROBLEM_PROOF_CONTRACT,
                 "%s.evidence_kinds[%d] %r is not an evidence kind"
                 % (location, index, kind))
        if kind not in SATISFYING_EVIDENCE_KINDS:
            fail(PROBLEM_PROOF_CONTRACT,
                 "%s.evidence_kinds[%d] %r can never satisfy a requirement;"
                 " a contract cannot declare it acceptable"
                 % (location, index, kind))
    if len(set(kinds)) != len(kinds):
        fail(PROBLEM_PROOF_CONTRACT,
             "%s.evidence_kinds carries a duplicate" % location)
    if not kinds:
        fail(PROBLEM_PROOF_CONTRACT,
             "%s.evidence_kinds must name at least one satisfying kind"
             % location)
    return {
        "key": require_contract_key(value["key"], location + ".key"),
        "description": require_str(
            value["description"], location + ".description",
            MAX_REQUIREMENT_DESCRIPTION_CHARS,
        ),
        "evidence_kinds": sorted(kinds),
        "required_artifact_keys": _require_sorted_keys(
            value["required_artifact_keys"],
            location + ".required_artifact_keys", MAX_REQUIRED_ARTIFACTS,
        ),
        "max_evidence_age_seconds": require_bounded_seconds(
            value["max_evidence_age_seconds"],
            location + ".max_evidence_age_seconds",
        ),
    }


def _validate_required_artifact(value, location):
    require_dict(value, location)
    require_closed_keys(value, REQUIRED_ARTIFACT_KEYS, location)
    # R-19: a required artifact is content-identified; null is refused.
    return {
        "key": require_contract_key(value["key"], location + ".key"),
        "role": require_member(value["role"], ARTIFACT_ROLES,
                               location + ".role", PROBLEM_PROOF_CONTRACT),
        "expected_content_digest_sha256": require_hex(
            value["expected_content_digest_sha256"],
            location + ".expected_content_digest_sha256", 64,
        ),
    }


def validate_dependency_target(value, location):
    """The closed exactly-one-of prerequisite target block (R-18)."""
    require_dict(value, location)
    form = value.get("form")
    require_member(form, TARGET_FORMS, location + ".form", PROBLEM_PROOF_CONTRACT)
    require_closed_keys(value, TARGET_KEYS_BY_FORM[form], location)
    if form == TARGET_FORM_EXACT_MISSION:
        return {
            "form": form,
            "mission_id": require_id(value["mission_id"], MISSION_ID_PREFIX,
                                     location + ".mission_id"),
            "revision": require_int(value["revision"], location + ".revision",
                                    minimum=1),
            "proposal_digest_sha256": require_hex(
                value["proposal_digest_sha256"],
                location + ".proposal_digest_sha256", 64,
            ),
        }
    if form == TARGET_FORM_EXACT_RESOURCE:
        return {
            "form": form,
            "resource_key": require_contract_key(value["resource_key"],
                                                 location + ".resource_key"),
        }
    return {
        "form": form,
        "condition": require_member(
            value["condition"], ELIGIBILITY_CONDITIONS,
            location + ".condition", PROBLEM_PROOF_CONTRACT,
        ),
        "proposal_digest_sha256": require_hex(
            value["proposal_digest_sha256"],
            location + ".proposal_digest_sha256", 64,
        ),
    }


def _validate_required_dependency(value, location):
    require_dict(value, location)
    require_closed_keys(value, REQUIRED_DEPENDENCY_KEYS, location)
    kind = require_member(value["kind"], DEPENDENCY_KINDS, location + ".kind",
                          PROBLEM_PROOF_CONTRACT)
    target = validate_dependency_target(value["target"], location + ".target")
    if target["form"] not in _TARGET_FORMS_BY_KIND[kind]:
        fail(PROBLEM_PROOF_CONTRACT,
             "%s: a %s prerequisite cannot carry a %s target"
             % (location, kind, target["form"]))
    return {
        "key": require_contract_key(value["key"], location + ".key"),
        "kind": kind,
        "target": target,
    }


def _validate_required_readiness(value, location):
    require_dict(value, location)
    require_closed_keys(value, REQUIRED_RESOURCE_READINESS_KEYS, location)
    return {
        "resource_key": require_contract_key(value["resource_key"],
                                             location + ".resource_key"),
        "max_age_seconds": require_bounded_seconds(
            value["max_age_seconds"], location + ".max_age_seconds"
        ),
    }


def _validate_bounded_count(value, location, minimum, maximum):
    require_int(value, location, minimum=minimum)
    if value > maximum:
        fail(PROBLEM_TOO_LARGE,
             "%s is %d; the hard bound is %d" % (location, value, maximum))
    return value


def validate_proof_contract(value, location="proof_contract"):
    """The validated, deterministically normalized contract (a new dict),
    or refuse. Every list is sorted by its key and duplicate-free, so the
    digest of the normalized form is stable across key and list order."""
    require_dict(value, location)
    require_closed_keys(value, PROOF_CONTRACT_KEYS, location)
    requirements = _require_keyed_entries(
        value["requirements"], location + ".requirements",
        MAX_PROOF_REQUIREMENTS, "key", _validate_requirement,
    )
    if not requirements:
        fail(PROBLEM_PROOF_CONTRACT,
             "%s.requirements must name at least one requirement" % location)
    artifacts = _require_keyed_entries(
        value["required_artifacts"], location + ".required_artifacts",
        MAX_REQUIRED_ARTIFACTS, "key", _validate_required_artifact,
    )
    artifact_keys = set(entry["key"] for entry in artifacts)
    named = set()
    for requirement in requirements:
        for key in requirement["required_artifact_keys"]:
            if key not in artifact_keys:
                fail(PROBLEM_CONTRACT_INCOHERENT,
                     "%s.requirements[%r] names required artifact %r, which"
                     " required_artifacts does not declare"
                     % (location, requirement["key"], key))
            named.add(key)
    unused = sorted(artifact_keys - named)
    if unused:
        fail(PROBLEM_CONTRACT_INCOHERENT,
             "%s.required_artifacts declares %s, which no requirement names;"
             " a required artifact nothing references is refused, not ignored"
             % (location, ", ".join(repr(key) for key in unused)))
    dependencies = _require_keyed_entries(
        value["required_dependencies"], location + ".required_dependencies",
        MAX_REQUIRED_DEPENDENCIES, "key", _validate_required_dependency,
    )
    readiness = _require_keyed_entries(
        value["required_resource_readiness"],
        location + ".required_resource_readiness",
        MAX_REQUIRED_RESOURCE_READINESS, "resource_key",
        _validate_required_readiness,
    )
    policy = value["degradation_policy"]
    require_dict(policy, location + ".degradation_policy")
    require_closed_keys(policy, DEGRADATION_POLICY_KEYS,
                        location + ".degradation_policy")
    budget = value["continuation_budget"]
    require_dict(budget, location + ".continuation_budget")
    require_closed_keys(budget, CONTINUATION_BUDGET_KEYS,
                        location + ".continuation_budget")
    return {
        "requirements": requirements,
        "required_artifacts": artifacts,
        "required_dependencies": dependencies,
        "required_resource_readiness": readiness,
        "degradation_policy": {
            "permitted_blocker_keys": _require_sorted_keys(
                policy["permitted_blocker_keys"],
                location + ".degradation_policy.permitted_blocker_keys",
                MAX_PERMITTED_BLOCKER_KEYS,
            ),
        },
        "continuation_budget": {
            "max_attempts": _validate_bounded_count(
                budget["max_attempts"],
                location + ".continuation_budget.max_attempts",
                0, MAX_CONTINUATION_ATTEMPTS,
            ),
            "max_checkpoints": _validate_bounded_count(
                budget["max_checkpoints"],
                location + ".continuation_budget.max_checkpoints",
                1, MAX_CONTINUATION_CHECKPOINTS,
            ),
        },
    }


def proof_contract_digest(value):
    """Canonical JSON sha256 of the validated contract CONTENT only."""
    return json_digest(validate_proof_contract(value))


# -- lifecycle --------------------------------------------------------


def require_state(value, location):
    return require_member(value, MISSION_STATES, location, PROBLEM_UNKNOWN_STATE)


def validate_transition(current, target):
    """Refuse unless ``current -> target`` is an explicitly wired transition."""
    require_state(current, "current state")
    require_state(target, "target state")
    if target not in ALLOWED_TRANSITIONS[current]:
        fail(PROBLEM_INVALID_TRANSITION,
             "transition %s -> %s is not wired; the allowed targets from %s"
             " are %s" % (current, target, current,
                          ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none"))
    return target


# -- authenticated context and provenance -----------------------------


@dataclass(frozen=True)
class AuthenticatedContext:
    """What a transport adapter knows about the caller from its OWN
    authenticated state: the transport, the KIND of principal it
    verified, that principal's reference, and the configured subject if
    one is known. It is built by the adapter per request and never from
    a tool payload; it does not claim to identify a human."""

    transport: str
    principal_kind: str
    principal_ref: str
    configured_subject: Optional[str] = None

    def validate(self, location="context"):
        require_str(self.transport, location + ".transport", MAX_TRANSPORT_CHARS)
        if any(ch not in _TRANSPORT_ALPHABET for ch in self.transport):
            fail(PROBLEM_BAD_VALUE,
                 "%s.transport must use only [a-z0-9_]" % location)
        require_member(self.principal_kind, ADAPTER_CONTEXT_PRINCIPAL_KINDS,
                       location + ".principal_kind", PROBLEM_PROVENANCE)
        require_str(self.principal_ref, location + ".principal_ref",
                    MAX_PRINCIPAL_REF_CHARS)
        require_optional_str(self.configured_subject,
                             location + ".configured_subject", MAX_SUBJECT_CHARS)
        return self

    def as_dict(self):
        return {
            "transport": self.transport,
            "principal_kind": self.principal_kind,
            "principal_ref": self.principal_ref,
            "configured_subject": self.configured_subject,
        }


@dataclass(frozen=True)
class OperatorAttestedContext:
    """An Outer Operator's ATTESTATION that it relayed the human's explicit
    chat reply (Task 8, user decision). The Operator is trusted by the
    user's declared policy; this layer does NOT independently verify the
    human or the relay. Residual risk (``OPERATOR_ATTESTED_RESIDUAL_RISK``):
    a mistaken or malicious same-user operator or local process could
    fabricate it. It may only reserve a decision id and carry an APPROVE.
    It shares the provenance dictionary SHAPE of the adapter context, not
    its name or its meaning."""

    transport: str
    principal_ref: str
    principal_kind: str = PRINCIPAL_KIND_OPERATOR_ATTESTED
    configured_subject: Optional[str] = None

    def validate(self, location="attestation"):
        require_str(self.transport, location + ".transport", MAX_TRANSPORT_CHARS)
        if any(ch not in _TRANSPORT_ALPHABET for ch in self.transport):
            fail(PROBLEM_BAD_VALUE,
                 "%s.transport must use only [a-z0-9_]" % location)
        if self.principal_kind != PRINCIPAL_KIND_OPERATOR_ATTESTED:
            fail(PROBLEM_ATTESTED_APPROVAL,
                 "%s.principal_kind must be %r"
                 % (location, PRINCIPAL_KIND_OPERATOR_ATTESTED))
        require_str(self.principal_ref, location + ".principal_ref",
                    MAX_PRINCIPAL_REF_CHARS)
        require_optional_str(self.configured_subject,
                             location + ".configured_subject", MAX_SUBJECT_CHARS)
        return self

    def as_dict(self):
        return {
            "transport": self.transport,
            "principal_kind": self.principal_kind,
            "principal_ref": self.principal_ref,
            "configured_subject": self.configured_subject,
        }


def require_operator_attested_context(value, location="attestation"):
    """An ``OperatorAttestedContext``, validated, or refuse. It is trusted
    by declared policy only, never independently verified."""
    if not isinstance(value, OperatorAttestedContext):
        fail(PROBLEM_ATTESTED_APPROVAL,
             "%s must be an OperatorAttestedContext; got %s"
             % (location, type(value).__name__))
    return value.validate(location)


def context_from_dict(value, location="context"):
    """The stored context: an ``OperatorAttestedContext`` for the
    operator-attested kind, otherwise an ``AuthenticatedContext``."""
    require_dict(value, location)
    require_closed_keys(value, CONTEXT_KEYS, location)
    if value["principal_kind"] == PRINCIPAL_KIND_OPERATOR_ATTESTED:
        return OperatorAttestedContext(
            transport=value["transport"],
            principal_ref=value["principal_ref"],
            configured_subject=value["configured_subject"],
        ).validate(location)
    return AuthenticatedContext(
        transport=value["transport"],
        principal_kind=value["principal_kind"],
        principal_ref=value["principal_ref"],
        configured_subject=value["configured_subject"],
    ).validate(location)


def validate_context_dict(value, location="context"):
    return context_from_dict(value, location).as_dict()


def require_context(value, location="context"):
    """An ``AuthenticatedContext`` instance, validated, or refuse."""
    if not isinstance(value, AuthenticatedContext):
        fail(PROBLEM_PROVENANCE,
             "%s must be an AuthenticatedContext built by the transport"
             " adapter from its own authenticated state; got %s"
             % (location, type(value).__name__))
    return value.validate(location)


def _refuse_unauthenticated(principal_kind, location):
    if principal_kind not in AUTHENTICATED_PRINCIPAL_KINDS:
        fail(PROBLEM_UNAUTHENTICATED_PRINCIPAL,
             "%s.principal_kind is %r: %s" % (location, principal_kind,
                                              UNAUTHENTICATED_REFUSAL_DETAIL))


def require_authenticated_context(value, location="context"):
    """``require_context`` plus an AUTHENTICATED principal kind. Every
    state-operation id reservation and state operation goes through this,
    and so does every decision or decision-id reservation carried by an
    ``AuthenticatedContext``. (An ``OperatorAttestedContext`` is handled by
    ``require_decision_context`` and is never authenticated.) The
    unauthenticated kind is refused here, in the core, whichever entry point
    called."""
    require_context(value, location)
    _refuse_unauthenticated(value.principal_kind, location)
    return value


def _require_decision_kind(principal_kind, approve, location):
    if principal_kind in AUTHENTICATED_PRINCIPAL_KINDS:
        return
    if principal_kind in APPROVE_ONLY_PRINCIPAL_KINDS:
        if approve:
            return
        fail(PROBLEM_ATTESTED_APPROVAL,
             "%s.principal_kind %r may carry only an APPROVE decision"
             % (location, principal_kind))
    _refuse_unauthenticated(principal_kind, location)


def require_decision_context(value, approve, location="context"):
    """The context a decision may carry: an ``AuthenticatedContext`` of an
    authenticated kind (``require_authenticated_context``, unchanged), or,
    for an APPROVE only, an ``OperatorAttestedContext`` (trusted by declared
    policy, not independently established). The unauthenticated local caller
    kind is always refused."""
    if isinstance(value, OperatorAttestedContext):
        require_operator_attested_context(value, location)
        if not approve:
            fail(PROBLEM_ATTESTED_APPROVAL,
                 "%s: an operator attestation may carry only an APPROVE"
                 " decision" % location)
        return value
    return require_authenticated_context(value, location)


def require_decision_reservation_context(value, location="context"):
    """A decision id may be reserved for an ``AuthenticatedContext`` of an
    authenticated kind or for an ``OperatorAttestedContext``; never for the
    unauthenticated local caller kind."""
    if isinstance(value, OperatorAttestedContext):
        return require_operator_attested_context(value, location)
    return require_authenticated_context(value, location)


def require_any_context(value, location="context"):
    """Either context type, validated: the provenance builder's input."""
    if isinstance(value, OperatorAttestedContext):
        return require_operator_attested_context(value, location)
    return require_context(value, location)


def require_decision_provenance(value, approve, location="provenance"):
    """A stored decision's or authorization's provenance: an authenticated
    kind, or the operator-attested kind on an APPROVE."""
    validate_provenance(value, location)
    _require_decision_kind(value["principal_kind"], approve, location)
    return value


def require_authenticated_provenance(value, location="provenance"):
    """A validated provenance block whose principal kind is authenticated:
    required of every revision after the first (an EDIT), every
    state-operation record and every reconciliation record. Stored
    decisions and authorizations use ``require_decision_provenance``
    instead, which also admits the operator-attested kind on an APPROVE."""
    validate_provenance(value, location)
    _refuse_unauthenticated(value["principal_kind"], location)
    return value


def withdrawal_key_digest(key):
    """The digest recorded for a withdrawal key (Task 8); the key itself is
    never stored. A non-string or empty key has no digest."""
    if not isinstance(key, str) or not key:
        return None
    return json_digest({"withdrawal_key": key})


def provenance_record(context, received_at, reference_kind, reference_id,
                      mission_id, revision):
    """The closed provenance block: only what is known, nothing more. The
    ``proof`` it records is fixed by the context's principal kind."""
    require_any_context(context)
    require_timestamp(received_at, "received_at")
    require_member(reference_kind, REFERENCE_KINDS, "reference_kind")
    require_id(reference_id, _REFERENCE_PREFIXES[reference_kind], "reference_id")
    require_id(mission_id, MISSION_ID_PREFIX, "mission_id")
    require_int(revision, "revision", minimum=1)
    record = context.as_dict()
    record.update({
        "received_at": received_at,
        "reference_kind": reference_kind,
        "reference_id": reference_id,
        "mission_id": mission_id,
        "revision": revision,
        "human_identity_proof": None,
        "proof": _PROOF_BY_PRINCIPAL_KIND[context.principal_kind],
    })
    return record


def validate_provenance(value, location="provenance"):
    require_dict(value, location)
    require_closed_keys(value, PROVENANCE_KEYS, location)
    context_from_dict(
        dict((key, value[key]) for key in CONTEXT_KEYS), location
    )
    require_timestamp(value["received_at"], location + ".received_at")
    require_member(value["reference_kind"], REFERENCE_KINDS,
                   location + ".reference_kind", PROBLEM_PROVENANCE)
    require_id(value["reference_id"], _REFERENCE_PREFIXES[value["reference_kind"]],
               location + ".reference_id")
    require_id(value["mission_id"], MISSION_ID_PREFIX, location + ".mission_id")
    require_int(value["revision"], location + ".revision", minimum=1)
    if value["human_identity_proof"] is not None:
        fail(PROBLEM_PROVENANCE,
             "%s.human_identity_proof must be null: %s"
             % (location, _NO_HUMAN_IDENTITY_BY_PRINCIPAL_KIND[
                 value["principal_kind"]]))
    expected_proof = _PROOF_BY_PRINCIPAL_KIND[value["principal_kind"]]
    if value["proof"] != expected_proof:
        fail(PROBLEM_PROVENANCE,
             "%s.proof must be %r for principal kind %r"
             % (location, expected_proof, value["principal_kind"]))
    return value


def provenance_context(value):
    """The context recorded in a provenance block: an
    ``OperatorAttestedContext`` for the operator-attested kind, otherwise an
    ``AuthenticatedContext``."""
    return context_from_dict(dict((key, value[key]) for key in CONTEXT_KEYS))
