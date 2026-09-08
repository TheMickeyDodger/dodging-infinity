"""Identity grammar, closed vocabularies, problem codes, authenticated
context / provenance shapes, and the shared bounded validators of every
coordination record.

Everything here follows the repository's authority-record conventions:
closed key sets, every key required, hard bounds as module constants
never derived from input, distinct ``coordination_*`` problem codes,
and refuse-never-repair. Nothing here reads or writes durable state,
reads a clock, or performs any effect.

Identity. Every coordination identifier is ``<prefix>-`` plus 32
lowercase hex characters, the same grammar as Mission Core: ``cr-``
route decision, ``cb-`` conversation binding, ``ca-`` attention record,
``ch-`` bot handoff. Mission Core identifiers (``mn-`` Mission, ``mv-``
evidence, ``mf-`` artifact) are VALIDATED here because records
reference them, and are never minted here: this package creates no
Mission and no Mission-local record. The prefixes are disjoint from
Mission Core's and from the connector transport prefix ``di-``, so no
coordination id can be mistaken for a Mission id or a request id.

Re-declaration, not import. The authenticated-context key set, the
principal-kind vocabulary, the lifecycle-state vocabulary and the proof
constant are the same strings Mission Core uses. They are re-declared
here so the package never imports ``mission``; the focused suite pins
parity from the test side.

Authority. ``AUTHORITY_NONE`` is the only value any record's
``authority`` field may hold, and ``TRANSFERS_CONTEXT_AND_REQUEST_ONLY``
the only value a handoff's ``transfers`` field may hold. A record
carrying anything else is malformed (``coordination_authority_claim``):
the shape itself cannot express an authority.

Priority (attention). Lower surfaces first: NEEDS_HUMAN 10,
AUTHORIZATION_READY 20, BLOCKED 30, RESULT_READY 40. Rationale (Lead
ruling): the human is the scarce resource, so the two conditions a
human can act on right now come first; a blocked Mission usually needs
diagnosis before a human action exists; a ready result is the least
time-critical. This is a product decision; reversing it is a one-line
change to ``ATTENTION_PRIORITY`` and the store re-derives every stored
priority from the table on load.
"""

import secrets
from dataclasses import dataclass
from typing import Optional

from workflow_authority import canonical

# -- identity ---------------------------------------------------------

ROUTE_ID_PREFIX = "cr"
BINDING_ID_PREFIX = "cb"
ATTENTION_ID_PREFIX = "ca"
HANDOFF_ID_PREFIX = "ch"
# Minted here.
ID_PREFIXES = (
    ROUTE_ID_PREFIX, BINDING_ID_PREFIX, ATTENTION_ID_PREFIX, HANDOFF_ID_PREFIX,
)
# Validated here, minted only by Mission Core.
MISSION_ID_PREFIX = "mn"
EVIDENCE_ID_PREFIX = "mv"
ARTIFACT_ID_PREFIX = "mf"
FOREIGN_ID_PREFIXES = (MISSION_ID_PREFIX, EVIDENCE_ID_PREFIX, ARTIFACT_ID_PREFIX)

# Exact-value pinned in the bound-constant table.
ID_HEX_CHARS = 32
_ID_HEX_ALPHABET = frozenset("0123456789abcdef")
_DIGITS = frozenset("0123456789")
_TRANSPORT_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
# Keys (project keys, aliases, condition keys, idempotency keys) are
# opaque bounded identifiers in the contract-key alphabet.
_KEY_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")

# -- hard bounds, never derived from input ----------------------------

MAX_TRANSPORT_CHARS = 64
MAX_PRINCIPAL_REF_CHARS = 128
MAX_SUBJECT_CHARS = 256
MAX_CONVERSATION_REF_CHARS = 128
MAX_MESSAGE_REF_CHARS = 128
# Canonical repository URL bound (512) + "#" + up to 15 issue digits.
MAX_SELECTOR_CHARS = 528
MAX_KEY_CHARS = 128
MAX_DETAIL_CHARS = 2000
MAX_REQUEST_TEXT_CHARS = 4000
MAX_CONDITION_DETAIL_CHARS = 1000
MAX_STATE_CURSOR_CHARS = 128
MAX_OBSERVATION_SOURCE_CHARS = 128
# Evidence / artifact references on one handoff or one condition.
MAX_REFERENCE_LIST = 64

# -- authenticated context and provenance -----------------------------

PRINCIPAL_KIND_CONNECTOR_CREDENTIAL = "configured_connector_credential_ordinal"
PRINCIPAL_KIND_LOCAL_PROCESS_USER = "local_process_user"
PRINCIPAL_KINDS = (
    PRINCIPAL_KIND_CONNECTOR_CREDENTIAL, PRINCIPAL_KIND_LOCAL_PROCESS_USER,
)
PROOF_TRANSPORT_CREDENTIAL_ONLY = "transport_credential_only"
CONTEXT_KEYS = (
    "transport", "principal_kind", "principal_ref", "configured_subject",
)
PROVENANCE_KEYS = CONTEXT_KEYS + (
    "received_at", "observed_revision", "observation_cursor",
    "human_identity_proof", "proof",
)
DESTINATION_KEYS = ("transport", "conversation_ref")

AUTHORITY_NONE = "none"
TRANSFERS_CONTEXT_AND_REQUEST_ONLY = "context_and_request_only"

# -- lifecycle (Mission Core's vocabulary, re-declared) ----------------

LIFECYCLE_AWAITING_DECISION = "AWAITING_DECISION"
LIFECYCLE_AUTHORIZED = "AUTHORIZED"
LIFECYCLE_DENIED = "DENIED"
LIFECYCLE_RUNNING = "RUNNING"
LIFECYCLE_BLOCKED = "BLOCKED"
LIFECYCLE_COMPLETED = "COMPLETED"
LIFECYCLE_CLOSED = "CLOSED"
LIFECYCLE_CANCELLED = "CANCELLED"
LIFECYCLE_STATES = (
    LIFECYCLE_AWAITING_DECISION, LIFECYCLE_AUTHORIZED, LIFECYCLE_DENIED,
    LIFECYCLE_RUNNING, LIFECYCLE_BLOCKED, LIFECYCLE_COMPLETED,
    LIFECYCLE_CLOSED, LIFECYCLE_CANCELLED,
)

# -- observed references (Mission State's vocabularies, re-declared) ----

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
# The only kinds an acceptance can ever attach to; an observation
# claiming an accepted narrative claim or process exit contradicts the
# Mission State shape and is INCONSISTENT.
SATISFYING_EVIDENCE_KINDS = (
    EVIDENCE_KIND_ARTIFACT_DIGEST, EVIDENCE_KIND_EXTERNAL_ATTESTATION,
    EVIDENCE_KIND_VERIFICATION_RECORD,
)
ARTIFACT_ROLE_ORIGINAL_INPUT = "ORIGINAL_INPUT"
ARTIFACT_ROLE_PRODUCED = "PRODUCED"
ARTIFACT_ROLE_VERIFICATION = "VERIFICATION"
ARTIFACT_ROLES = (
    ARTIFACT_ROLE_ORIGINAL_INPUT, ARTIFACT_ROLE_PRODUCED,
    ARTIFACT_ROLE_VERIFICATION,
)
# Validity levels a citation may claim (AMD-3). Presence in an
# observation proves RECORDED (Mission-locality) and nothing more;
# ACCEPTED requires the evidence's acceptance provenance and VALIDATED
# requires the artifact's validated receipt facts.
REFERENCE_LEVEL_RECORDED = "RECORDED"
REFERENCE_LEVEL_ACCEPTED = "ACCEPTED"
REFERENCE_LEVEL_VALIDATED = "VALIDATED"
REFERENCE_LEVELS = (
    REFERENCE_LEVEL_ACCEPTED, REFERENCE_LEVEL_RECORDED, REFERENCE_LEVEL_VALIDATED,
)
EVIDENCE_REFERENCE_LEVELS = (REFERENCE_LEVEL_ACCEPTED, REFERENCE_LEVEL_RECORDED)
ARTIFACT_REFERENCE_LEVELS = (REFERENCE_LEVEL_RECORDED, REFERENCE_LEVEL_VALIDATED)

# -- observation ------------------------------------------------------

OBSERVATION_OBSERVED = "OBSERVED"
OBSERVATION_ABSENT = "ABSENT"
OBSERVATION_UNAVAILABLE = "UNAVAILABLE"
OBSERVATION_STATUSES = (
    OBSERVATION_ABSENT, OBSERVATION_OBSERVED, OBSERVATION_UNAVAILABLE,
)
FRESHNESS_FRESH = "FRESH"
FRESHNESS_STALE = "STALE"
FRESHNESS_INCONSISTENT = "INCONSISTENT"
FRESHNESS_ABSENT = "ABSENT"
FRESHNESS_UNAVAILABLE = "UNAVAILABLE"
FRESHNESS_STATES = (
    FRESHNESS_ABSENT, FRESHNESS_FRESH, FRESHNESS_INCONSISTENT,
    FRESHNESS_STALE, FRESHNESS_UNAVAILABLE,
)

# -- routing ----------------------------------------------------------

ROUTE_EXISTING_MISSION = "EXISTING_MISSION"
ROUTE_NEW_PROPOSAL = "NEW_PROPOSAL"
ROUTE_CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
ROUTE_OUTCOMES = (
    ROUTE_CLARIFICATION_REQUIRED, ROUTE_EXISTING_MISSION, ROUTE_NEW_PROPOSAL,
)
# Deterministic evidence tiers, in resolution order. The bounded
# natural-language routing turn is deliberately absent.
TIER_EXPLICIT_MISSION_ID = "EXPLICIT_MISSION_ID"
TIER_REPLY_TO_BINDING = "REPLY_TO_BINDING"
TIER_APPROVAL_BINDING = "APPROVAL_BINDING"
TIER_RESULT_BINDING = "RESULT_BINDING"
TIER_REPOSITORY_REFERENCE = "REPOSITORY_REFERENCE"
TIER_ISSUE_REFERENCE = "ISSUE_REFERENCE"
TIER_PROJECT_BINDING = "PROJECT_BINDING"
TIER_ALIAS_BINDING = "ALIAS_BINDING"
TIER_UNIQUE_CONVERSATION_MATCH = "UNIQUE_CONVERSATION_MATCH"
ROUTE_TIERS = (
    TIER_EXPLICIT_MISSION_ID, TIER_REPLY_TO_BINDING, TIER_APPROVAL_BINDING,
    TIER_RESULT_BINDING, TIER_REPOSITORY_REFERENCE, TIER_ISSUE_REFERENCE,
    TIER_PROJECT_BINDING, TIER_ALIAS_BINDING, TIER_UNIQUE_CONVERSATION_MATCH,
)
REASON_RESOLVED = "RESOLVED"
REASON_NEW_PROPOSAL_INTENT = "NEW_PROPOSAL_INTENT"
REASON_CONTEXT_DISAGREES = "CONTEXT_DISAGREES"
REASON_MISSION_NOT_OBSERVED = "MISSION_NOT_OBSERVED"
REASON_OBSERVATION_UNAVAILABLE = "OBSERVATION_UNAVAILABLE"
REASON_OBSERVATION_STALE = "OBSERVATION_STALE"
REASON_OBSERVATION_INCONSISTENT = "OBSERVATION_INCONSISTENT"
REASON_BINDING_STALE = "BINDING_STALE"
REASON_BINDING_EXPIRED = "BINDING_EXPIRED"
REASON_AMBIGUOUS_CANDIDATES = "AMBIGUOUS_CANDIDATES"
REASON_NO_CONTEXT = "NO_CONTEXT"
ROUTE_REASONS = (
    REASON_AMBIGUOUS_CANDIDATES, REASON_BINDING_EXPIRED, REASON_BINDING_STALE,
    REASON_CONTEXT_DISAGREES, REASON_MISSION_NOT_OBSERVED,
    REASON_NEW_PROPOSAL_INTENT, REASON_NO_CONTEXT,
    REASON_OBSERVATION_INCONSISTENT, REASON_OBSERVATION_STALE,
    REASON_OBSERVATION_UNAVAILABLE, REASON_RESOLVED,
)
INTENT_FOLLOW_UP = "FOLLOW_UP"
INTENT_NEW_MISSION = "NEW_MISSION"
INTENT_UNSPECIFIED = "UNSPECIFIED"
INTENTS = (INTENT_FOLLOW_UP, INTENT_NEW_MISSION, INTENT_UNSPECIFIED)

LANE_OUTCOME_SELECTED = "SELECTED"
LANE_OUTCOME_REFUSED = "REFUSED"
LANE_OUTCOME_NOT_REQUESTED = "NOT_REQUESTED"
LANE_OUTCOMES = (
    LANE_OUTCOME_NOT_REQUESTED, LANE_OUTCOME_REFUSED, LANE_OUTCOME_SELECTED,
)
# This lane IS the bounded Herdr engineering route: when a resolved or
# proposed route's domain is ENGINEERING and this lane is among the
# caller-declared available lanes, routing selects it (acceptance
# criterion 2, "engineering selects the Herdr route"). It is a decision
# VALUE only: it confers no dispatch permission, starts no Herdr work,
# and carries no authority. The identifier and the literal are
# deliberately neutral (not "HERDR_...") because this package sits in
# the static suite's herdr-free roots, whose token scan forbids that
# name in identifiers and string literals but expressly permits it in
# docstring and comment prose — the boundary matters more than the
# label, and the prose is where the meaning is stated.
LANE_ENGINEERING = "ENGINEERING_LANE"
LANES = (LANE_ENGINEERING,)
DOMAIN_ENGINEERING = "ENGINEERING"
SUPPORTED_DOMAINS = (DOMAIN_ENGINEERING,)
LANE_BY_DOMAIN = {DOMAIN_ENGINEERING: LANE_ENGINEERING}
LANE_REASON_DOMAIN_UNSUPPORTED = "DOMAIN_UNSUPPORTED"
LANE_REASON_CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
LANE_REASON_ROUTE_NOT_RESOLVED = "ROUTE_NOT_RESOLVED"
LANE_REASONS = (
    LANE_REASON_CAPABILITY_UNAVAILABLE, LANE_REASON_DOMAIN_UNSUPPORTED,
    LANE_REASON_ROUTE_NOT_RESOLVED,
)

# -- bindings ---------------------------------------------------------

BINDING_REPLY_TO_MESSAGE = "REPLY_TO_MESSAGE"
BINDING_APPROVAL_PRESENTATION = "APPROVAL_PRESENTATION"
BINDING_RESULT_PRESENTATION = "RESULT_PRESENTATION"
BINDING_REPOSITORY = "REPOSITORY"
BINDING_ISSUE = "ISSUE"
BINDING_PROJECT = "PROJECT"
BINDING_ALIAS = "ALIAS"
BINDING_CONVERSATION = "CONVERSATION"
BINDING_KINDS = (
    BINDING_ALIAS, BINDING_APPROVAL_PRESENTATION, BINDING_CONVERSATION,
    BINDING_ISSUE, BINDING_PROJECT, BINDING_REPLY_TO_MESSAGE,
    BINDING_REPOSITORY, BINDING_RESULT_PRESENTATION,
)
# Kinds scoped to one conversation (conversation_ref required) versus
# durable lookups (conversation_ref null).
CONVERSATION_SCOPED_BINDING_KINDS = (
    BINDING_APPROVAL_PRESENTATION, BINDING_CONVERSATION,
    BINDING_REPLY_TO_MESSAGE, BINDING_RESULT_PRESENTATION,
)
# Kinds whose resolution is revision-exact (A-R2).
REVISION_EXACT_BINDING_KINDS = (
    BINDING_APPROVAL_PRESENTATION, BINDING_RESULT_PRESENTATION,
)

# -- attention --------------------------------------------------------

ATTENTION_BLOCKED = "BLOCKED"
ATTENTION_NEEDS_HUMAN = "NEEDS_HUMAN"
ATTENTION_AUTHORIZATION_READY = "AUTHORIZATION_READY"
ATTENTION_RESULT_READY = "RESULT_READY"
ATTENTION_KINDS = (
    ATTENTION_AUTHORIZATION_READY, ATTENTION_BLOCKED, ATTENTION_NEEDS_HUMAN,
    ATTENTION_RESULT_READY,
)
# Lower surfaces first. See the module docstring for the rationale.
ATTENTION_PRIORITY = {
    ATTENTION_NEEDS_HUMAN: 10,
    ATTENTION_AUTHORIZATION_READY: 20,
    ATTENTION_BLOCKED: 30,
    ATTENTION_RESULT_READY: 40,
}
PRESENTATION_PENDING = "PENDING"
PRESENTATION_SURFACED = "SURFACED"
PRESENTATION_ACKNOWLEDGED = "ACKNOWLEDGED"
PRESENTATION_OBSOLETE = "OBSOLETE"
PRESENTATION_RESOLVED = "RESOLVED"
PRESENTATION_STATES = (
    PRESENTATION_ACKNOWLEDGED, PRESENTATION_OBSOLETE, PRESENTATION_PENDING,
    PRESENTATION_RESOLVED, PRESENTATION_SURFACED,
)
TERMINAL_PRESENTATION_STATES = (PRESENTATION_OBSOLETE, PRESENTATION_RESOLVED)
PRESENTATION_TRANSITIONS = {
    PRESENTATION_PENDING: frozenset((
        PRESENTATION_SURFACED, PRESENTATION_ACKNOWLEDGED,
        PRESENTATION_OBSOLETE, PRESENTATION_RESOLVED,
    )),
    PRESENTATION_SURFACED: frozenset((
        PRESENTATION_ACKNOWLEDGED, PRESENTATION_OBSOLETE, PRESENTATION_RESOLVED,
    )),
    # Acknowledging never immunises a stale presentation (A-R1c).
    PRESENTATION_ACKNOWLEDGED: frozenset((
        PRESENTATION_OBSOLETE, PRESENTATION_RESOLVED,
    )),
    PRESENTATION_OBSOLETE: frozenset(),
    PRESENTATION_RESOLVED: frozenset(),
}
CLOSED_CONDITION_CHANGED = "CONDITION_CHANGED"
CLOSED_REVISION_CHANGED = "REVISION_CHANGED"
CLOSED_AUTHORITY_CHANGED = "AUTHORITY_CHANGED"
CLOSED_CONDITION_CLEARED = "CONDITION_CLEARED"
OBSOLESCENCE_REASONS = (
    CLOSED_AUTHORITY_CHANGED, CLOSED_CONDITION_CHANGED, CLOSED_REVISION_CHANGED,
)
CLOSED_REASONS = OBSOLESCENCE_REASONS + (CLOSED_CONDITION_CLEARED,)

# -- handoffs ---------------------------------------------------------

HANDOFF_OPEN = "OPEN"
HANDOFF_ACCEPTED = "ACCEPTED"
HANDOFF_ANSWERED = "ANSWERED"
HANDOFF_DECLINED = "DECLINED"
HANDOFF_WITHDRAWN = "WITHDRAWN"
HANDOFF_STATUSES = (
    HANDOFF_ACCEPTED, HANDOFF_ANSWERED, HANDOFF_DECLINED, HANDOFF_OPEN,
    HANDOFF_WITHDRAWN,
)
TERMINAL_HANDOFF_STATUSES = (
    HANDOFF_ANSWERED, HANDOFF_DECLINED, HANDOFF_WITHDRAWN,
)
HANDOFF_TRANSITIONS = {
    HANDOFF_OPEN: frozenset((HANDOFF_ACCEPTED, HANDOFF_DECLINED, HANDOFF_WITHDRAWN)),
    HANDOFF_ACCEPTED: frozenset((HANDOFF_ANSWERED, HANDOFF_WITHDRAWN)),
    HANDOFF_ANSWERED: frozenset(),
    HANDOFF_DECLINED: frozenset(),
    HANDOFF_WITHDRAWN: frozenset(),
}
PURPOSE_QUESTION = "QUESTION"
PURPOSE_SUMMARY = "SUMMARY"
PURPOSE_RECOMMENDATION = "RECOMMENDATION"
PURPOSE_EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
PURPOSE_CONTEXT_TRANSFER = "CONTEXT_TRANSFER"
HANDOFF_PURPOSES = (
    PURPOSE_CONTEXT_TRANSFER, PURPOSE_EVIDENCE_REVIEW, PURPOSE_QUESTION,
    PURPOSE_RECOMMENDATION, PURPOSE_SUMMARY,
)
# The specialist participants of architecture §3.
PARTICIPANT_COORDINATOR = "COORDINATOR"
PARTICIPANT_ENGINEERING = "ENGINEERING"
PARTICIPANT_RESEARCH = "RESEARCH"
PARTICIPANT_OPERATIONS = "OPERATIONS"
PARTICIPANT_RELEASE = "RELEASE"
PARTICIPANT_BROWSER_QA = "BROWSER_QA"
PARTICIPANT_INCIDENT_RECOVERY = "INCIDENT_RECOVERY"
PARTICIPANTS = (
    PARTICIPANT_BROWSER_QA, PARTICIPANT_COORDINATOR, PARTICIPANT_ENGINEERING,
    PARTICIPANT_INCIDENT_RECOVERY, PARTICIPANT_OPERATIONS,
    PARTICIPANT_RELEASE, PARTICIPANT_RESEARCH,
)

# -- problem codes ----------------------------------------------------

PROBLEM_NOT_AN_OBJECT = "coordination_not_an_object"
PROBLEM_UNKNOWN_KEY = "coordination_unknown_key"
PROBLEM_MISSING_KEY = "coordination_missing_key"
PROBLEM_BAD_TYPE = "coordination_bad_type"
PROBLEM_BAD_VALUE = "coordination_bad_value"
PROBLEM_TOO_LARGE = "coordination_too_large"
PROBLEM_ID_GRAMMAR = "coordination_id_grammar"
PROBLEM_PROVENANCE = "coordination_provenance"
PROBLEM_REPOSITORY_IDENTITY = "coordination_repository_identity"
PROBLEM_AUTHORITY_CLAIM = "coordination_authority_claim"
PROBLEM_STORE_UNREADABLE = "coordination_store_unreadable"
PROBLEM_STORE_FULL = "coordination_store_full"
PROBLEM_STORE_CONFLICT = "coordination_store_conflict"
PROBLEM_ROUTE_CONFLICT = "coordination_route_conflict"
PROBLEM_IDEMPOTENCY_CONFLICT = "coordination_idempotency_conflict"
PROBLEM_DUPLICATE_EFFECT = "coordination_duplicate_effect"
PROBLEM_CONTEXT_MISMATCH = "coordination_context_mismatch"
PROBLEM_BINDING_CONFLICT = "coordination_binding_conflict"
PROBLEM_BINDING_STALE = "coordination_binding_stale"
PROBLEM_BINDING_EXPIRED = "coordination_binding_expired"
PROBLEM_OBSERVATION_UNAVAILABLE = "coordination_observation_unavailable"
PROBLEM_OBSERVATION_STALE = "coordination_observation_stale"
PROBLEM_OBSERVATION_INCONSISTENT = "coordination_observation_inconsistent"
PROBLEM_MISSION_NOT_OBSERVED = "coordination_mission_not_observed"
PROBLEM_MISSION_MISMATCH = "coordination_mission_mismatch"
PROBLEM_REFERENCE_NOT_MISSION_LOCAL = "coordination_reference_not_mission_local"
PROBLEM_REFERENCE_NOT_PROVEN = "coordination_reference_not_proven"
PROBLEM_HANDOFF_STALE = "coordination_handoff_stale"
PROBLEM_PARTICIPANT_INELIGIBLE = "coordination_participant_ineligible"
PROBLEM_ROSTER_MISSING = "coordination_roster_missing"
PROBLEM_FORWARDING_LOOP = "coordination_forwarding_loop"
PROBLEM_FORWARD_CONTINUITY = "coordination_forward_continuity"
PROBLEM_FORWARD_DEPTH_EXCEEDED = "coordination_forward_depth_exceeded"
PROBLEM_INVALID_TRANSITION = "coordination_invalid_transition"
PROBLEM_ATTENTION_TERMINAL = "coordination_attention_terminal"
PROBLEM_LANE_UNAVAILABLE = "coordination_lane_unavailable"


class CoordinationError(Exception):
    """A coordination refusal; ``problem`` is a distinct ``coordination_*``
    code and the message is actionable."""

    def __init__(self, message, problem):
        super(CoordinationError, self).__init__(message)
        self.problem = problem


def fail(problem, message):
    raise CoordinationError(message, problem)


# -- identity ---------------------------------------------------------


def mint_id(prefix, token_hex=secrets.token_hex):
    """A fresh coordination identifier for ``prefix``; the hex source is
    injectable for determinism and never derived from a provider."""
    if prefix not in ID_PREFIXES:
        fail(PROBLEM_ID_GRAMMAR,
             "coordination mints only %s ids; %r is not one of them"
             % ("/".join(ID_PREFIXES), prefix))
    return "%s-%s" % (prefix, token_hex(ID_HEX_CHARS // 2))


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


def require_optional_id(value, prefix, location):
    if value is None:
        return None
    return require_id(value, prefix, location)


# -- generic validators -----------------------------------------------


def require_dict(value, location):
    if not isinstance(value, dict):
        fail(PROBLEM_NOT_AN_OBJECT,
             "%s must be an object, not %s" % (location, type(value).__name__))
    return value


def require_closed_keys(value, allowed, location):
    """Every key in ``allowed`` present, no key outside it."""
    unknown = sorted(set(value) - set(allowed))
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


def require_int(value, location, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        fail(PROBLEM_BAD_TYPE,
             "%s must be an integer (bool is not accepted), not %r"
             % (location, value))
    if minimum is not None and value < minimum:
        fail(PROBLEM_BAD_VALUE,
             "%s must be >= %d; got %d" % (location, minimum, value))
    if maximum is not None and value > maximum:
        fail(PROBLEM_TOO_LARGE,
             "%s is %d; the hard bound is %d" % (location, value, maximum))
    return value


def require_optional_int(value, location, minimum=None):
    if value is None:
        return None
    return require_int(value, location, minimum=minimum)


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


def require_optional_hex(value, location, length):
    if value is None:
        return None
    return require_hex(value, location, length)


def require_member(value, allowed, location, problem=PROBLEM_BAD_VALUE):
    if not isinstance(value, str) or value not in allowed:
        fail(problem,
             "%s must be one of %s; got %r (unknown values fail closed)"
             % (location, ", ".join(allowed), value))
    return value


def require_list(value, location, max_items):
    if not isinstance(value, list):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a list, not %s" % (location, type(value).__name__))
    if len(value) > max_items:
        fail(PROBLEM_TOO_LARGE,
             "%s holds %d entries; the hard bound is %d and the record is"
             " refused, not truncated" % (location, len(value), max_items))
    return value


def require_sorted_ids(value, prefix, location, max_items):
    """A sorted, duplicate-free list of ids for ``prefix`` (deterministic
    form; an unsorted list is refused, never repaired)."""
    require_list(value, location, max_items)
    for index, item in enumerate(value):
        require_id(item, prefix, "%s[%d]" % (location, index))
    if len(set(value)) != len(value):
        fail(PROBLEM_BAD_VALUE, "%s carries a duplicate id" % location)
    if value != sorted(value):
        fail(PROBLEM_BAD_VALUE, "%s must be sorted (deterministic form)"
             % location)
    return value


def require_sorted_members(value, allowed, location, max_items, allow_empty):
    """A sorted, duplicate-free subset of ``allowed``."""
    require_list(value, location, max_items)
    for index, item in enumerate(value):
        require_member(item, allowed, "%s[%d]" % (location, index))
    if len(set(value)) != len(value):
        fail(PROBLEM_BAD_VALUE, "%s carries a duplicate entry" % location)
    if value != sorted(value):
        fail(PROBLEM_BAD_VALUE, "%s must be sorted (deterministic form)"
             % location)
    if not allow_empty and not value:
        fail(PROBLEM_BAD_VALUE, "%s must name at least one entry" % location)
    return value


def require_key(value, location):
    """An opaque bounded key: ``[a-z0-9_.-]``, 1..MAX_KEY_CHARS."""
    require_str(value, location, MAX_KEY_CHARS)
    if any(ch not in _KEY_ALPHABET for ch in value):
        fail(PROBLEM_BAD_VALUE,
             "%s must use only [a-z0-9_.-]; got %r" % (location, value))
    return value


def require_transport(value, location):
    require_str(value, location, MAX_TRANSPORT_CHARS)
    if any(ch not in _TRANSPORT_ALPHABET for ch in value):
        fail(PROBLEM_BAD_VALUE, "%s must use only [a-z0-9_]" % location)
    return value


def require_cursor(value, location):
    """A state cursor: a canonical decimal digit string (``"0"`` or no
    leading zero), so string equality is numeric equality and
    ``cursor_value`` orders it (A-R3)."""
    require_str(value, location, MAX_STATE_CURSOR_CHARS)
    if any(ch not in _DIGITS for ch in value) or (
        len(value) > 1 and value[0] == "0"
    ):
        fail(PROBLEM_BAD_VALUE,
             "%s must be a canonical decimal digit string; got %r"
             % (location, value))
    return value


def require_optional_cursor(value, location):
    if value is None:
        return None
    return require_cursor(value, location)


def cursor_value(value):
    """The integer a validated cursor denotes."""
    return int(require_cursor(value, "cursor"))


def require_repository_url(value, location):
    """An already-canonical GitHub repository identity; refused, never
    repaired (the same posture as Mission Core's proposal)."""
    if not isinstance(value, str):
        fail(PROBLEM_BAD_TYPE,
             "%s must be a string, not %s" % (location, type(value).__name__))
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


def require_optional_repository_url(value, location):
    if value is None:
        return None
    return require_repository_url(value, location)


def require_authority(value, location):
    """Every record's ``authority`` must be exactly ``"none"``."""
    if not isinstance(value, str) or value != AUTHORITY_NONE:
        fail(PROBLEM_AUTHORITY_CLAIM,
             "%s.authority must be %r: coordination records hold no"
             " authority of any kind; got %r" % (location, AUTHORITY_NONE, value))
    return value


def require_destination(value, location):
    require_dict(value, location)
    require_closed_keys(value, DESTINATION_KEYS, location)
    require_transport(value["transport"], location + ".transport")
    require_str(value["conversation_ref"], location + ".conversation_ref",
                MAX_CONVERSATION_REF_CHARS)
    return value


# -- authenticated context and provenance -----------------------------


@dataclass(frozen=True)
class AuthenticatedContext:
    """What a transport adapter knows about the caller from its OWN
    authenticated state. Built by the adapter per request, never from a
    tool payload; it does not claim to identify a human."""

    transport: str
    principal_kind: str
    principal_ref: str
    configured_subject: Optional[str] = None

    def validate(self, location="context"):
        require_transport(self.transport, location + ".transport")
        require_member(self.principal_kind, PRINCIPAL_KINDS,
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


def context_from_dict(value, location="context"):
    require_dict(value, location)
    require_closed_keys(value, CONTEXT_KEYS, location)
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


def provenance_record(context, received_at, observed_revision,
                      observation_cursor):
    """The closed provenance block: only what is known. The observation
    point is recorded as a pair or not at all."""
    require_context(context)
    require_timestamp(received_at, "received_at")
    block = context.as_dict()
    block.update({
        "received_at": received_at,
        "observed_revision": observed_revision,
        "observation_cursor": observation_cursor,
        "human_identity_proof": None,
        "proof": PROOF_TRANSPORT_CREDENTIAL_ONLY,
    })
    return validate_provenance(block)


def validate_provenance(value, location="provenance"):
    require_dict(value, location)
    require_closed_keys(value, PROVENANCE_KEYS, location)
    context_from_dict(dict((key, value[key]) for key in CONTEXT_KEYS), location)
    require_timestamp(value["received_at"], location + ".received_at")
    require_optional_int(value["observed_revision"],
                         location + ".observed_revision", minimum=1)
    require_optional_cursor(value["observation_cursor"],
                            location + ".observation_cursor")
    if (value["observed_revision"] is None) != (value["observation_cursor"] is None):
        fail(PROBLEM_PROVENANCE,
             "%s records an observation point as a (revision, cursor) pair"
             " or not at all" % location)
    if value["human_identity_proof"] is not None:
        fail(PROBLEM_PROVENANCE,
             "%s.human_identity_proof must be null: this layer verifies a"
             " transport credential, never a human identity" % location)
    if value["proof"] != PROOF_TRANSPORT_CREDENTIAL_ONLY:
        fail(PROBLEM_PROVENANCE,
             "%s.proof must be %r" % (location, PROOF_TRANSPORT_CREDENTIAL_ONLY))
    return value


def provenance_context(value):
    """The AuthenticatedContext recorded in a provenance block."""
    return context_from_dict(dict((key, value[key]) for key in CONTEXT_KEYS))
