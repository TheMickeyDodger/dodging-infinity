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
roadmap needs; ``ALLOWED_TRANSITIONS`` wires only the decision
transitions of this bundle. ``AUTHORIZED`` has no transition to
``RUNNING``, and no code path in this package ever assigns ``RUNNING``:
approved does not imply running.

Authenticated context and provenance. ``AuthenticatedContext`` is what
a transport adapter builds from its OWN authenticated state and hands
in per request. ``provenance_record`` turns it into the closed
provenance block stored on every revision, decision and authorization.
The block states only what is known and carries
``human_identity_proof: null`` with ``proof: "transport_credential_only"``.
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
ID_PREFIXES = (
    MISSION_ID_PREFIX, REQUEST_ID_PREFIX, DECISION_ID_PREFIX,
    AUTHORIZATION_ID_PREFIX, LEDGER_ENTRY_ID_PREFIX,
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
# The states a Mission can actually hold in this bundle: the only ones a
# wired transition can reach. A stored record in any other state is
# malformed, because nothing here can produce it.
REACHABLE_STATES = (STATE_AWAITING_DECISION, STATE_AUTHORIZED, STATE_DENIED)
# Only the decision transitions are wired. Every later-roadmap state is
# in the vocabulary and in NO transition target.
ALLOWED_TRANSITIONS = {
    STATE_AWAITING_DECISION: frozenset((STATE_AUTHORIZED, STATE_DENIED)),
    STATE_AUTHORIZED: frozenset((STATE_AWAITING_DECISION,)),
    STATE_DENIED: frozenset((STATE_AWAITING_DECISION,)),
    STATE_RUNNING: frozenset(),
    STATE_BLOCKED: frozenset(),
    STATE_COMPLETED: frozenset(),
    STATE_CLOSED: frozenset(),
    STATE_CANCELLED: frozenset(),
}

PRINCIPAL_KIND_CONNECTOR_CREDENTIAL = "configured_connector_credential_ordinal"
PRINCIPAL_KIND_LOCAL_PROCESS_USER = "local_process_user"
PRINCIPAL_KINDS = (
    PRINCIPAL_KIND_CONNECTOR_CREDENTIAL, PRINCIPAL_KIND_LOCAL_PROCESS_USER,
)
PROOF_TRANSPORT_CREDENTIAL_ONLY = "transport_credential_only"

REFERENCE_KIND_REQUEST = "request"
REFERENCE_KIND_DECISION = "decision"
REFERENCE_KINDS = (REFERENCE_KIND_REQUEST, REFERENCE_KIND_DECISION)

# -- hard bounds, never derived from input ----------------------------

MAX_OBJECTIVE_CHARS = 8000
MAX_TARGET_CONTEXT_CHARS = 4000
MAX_SCOPE_TEXT_CHARS = 4000
MAX_TRANSPORT_CHARS = 64
MAX_PRINCIPAL_REF_CHARS = 128
MAX_SUBJECT_CHARS = 256

PROPOSAL_KEYS = (
    "objective", "target_context", "repository_url", "requested_scope",
    "requested_action_scope", "requested_delivery_target",
)
CONTEXT_KEYS = (
    "transport", "principal_kind", "principal_ref", "configured_subject",
)
PROVENANCE_KEYS = CONTEXT_KEYS + (
    "received_at", "reference_kind", "reference_id", "mission_id",
    "revision", "human_identity_proof", "proof",
)

_TRANSPORT_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")

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


def require_closed_keys(value, allowed, location):
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

    Normalization is limited to sorting the action-scope list; a
    repository URL that is not already canonical is refused, never
    repaired.
    """
    require_dict(value, location)
    require_closed_keys(value, PROPOSAL_KEYS, location)
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


def provenance_record(context, received_at, reference_kind, reference_id,
                      mission_id, revision):
    """The closed provenance block: only what is known, nothing more."""
    require_context(context)
    require_timestamp(received_at, "received_at")
    require_member(reference_kind, REFERENCE_KINDS, "reference_kind")
    prefix = (REQUEST_ID_PREFIX if reference_kind == REFERENCE_KIND_REQUEST
              else DECISION_ID_PREFIX)
    require_id(reference_id, prefix, "reference_id")
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
        "proof": PROOF_TRANSPORT_CREDENTIAL_ONLY,
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
    prefix = (REQUEST_ID_PREFIX
              if value["reference_kind"] == REFERENCE_KIND_REQUEST
              else DECISION_ID_PREFIX)
    require_id(value["reference_id"], prefix, location + ".reference_id")
    require_id(value["mission_id"], MISSION_ID_PREFIX, location + ".mission_id")
    require_int(value["revision"], location + ".revision", minimum=1)
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
