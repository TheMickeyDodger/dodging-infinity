"""The local request surface's own durable document, and the one-shot
proposal control capability that lives inside it.

One JSON document, ``local_requests.json``, in the same protected
directory as the Mission store, written with the shared
``workflow_authority.atomic`` primitives under its own cross-process
lock (``local_requests.lock``). The directory and the file are refused
when group or other can reach them, exactly the Mission store's rule. A
missing directory is created mode 700 on the first write; a read never
creates anything. Every load validates the whole document closed and
fails closed (``local_request_store_unreadable``); nothing is repaired.

The document holds two maps:

- ``requests``: one record per request this surface accepted, keyed by
  its ``request_ref``. It names the Mission Core request id it reserved,
  the exact proposal it submitted, the Mission id once Mission Core
  confirmed it, the surface's own terminal state, and every refused
  approval attempt.
- ``control_capabilities``: the one-shot control capabilities, keyed by
  the SHA-256 of the token. The token itself is never stored, logged or
  shown again after the creation response returned it.

``ProposalControlAuthority`` is an implementation of the neutral
``capability.contract.CapabilityAuthority`` seam bound, at construction,
to the ``control_capabilities`` map of ONE loaded document. It mutates
that map only; the caller holds the store lock and saves the document,
so consuming a capability and committing what it permits land in one
atomic write. It grants approval of nothing and dispatches nothing: a
token permits exactly the one action it was minted for, on exactly the
one request it was minted for.
"""

import hmac
import json
import os
import secrets
import stat

from capability import contract as capability_contract
from mission import record as mission_record
from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

SCHEMA_VERSION = 1
REQUESTS_FILE_NAME = "local_requests.json"
REQUESTS_LOCK_FILE_NAME = "local_requests.lock"

REQUEST_REF_PREFIX = "lr"
# Exhaustion stop for a colliding or malformed reference minter (the same
# eight attempts as Mission Core's id minter).
REQUEST_REF_ATTEMPTS = 8
TOKEN_PREFIX = "lc-"
# Exact-value pinned in the bound-constant table.
TOKEN_HEX_CHARS = 64
MAX_REQUEST_RECORDS = 1024
MAX_CONTROL_CAPABILITIES = 1024
MAX_APPROVAL_REFUSALS = 64
# How long the creator may use its cancel capability: seven days.
CONTROL_CAPABILITY_VALIDITY_SECONDS = 604800

ACTION_CANCEL_PENDING_PROPOSAL = "cancel_pending_proposal"
CONTROL_ACTIONS = (ACTION_CANCEL_PENDING_PROPOSAL,)

STATE_OPEN = "OPEN"
STATE_CANCELLED = "CANCELLED"
STATES = (STATE_OPEN, STATE_CANCELLED)

BINDING_PROPOSAL_UNCONFIRMED = "proposal_unconfirmed"
BINDING_NOT_PENDING = "mission_not_pending"
BINDING_STALE_REVISION = "stale_revision"
BINDING_DIGEST_MISMATCH = "proposal_digest_mismatch"
BINDING_EXPIRED = "expired"
BINDING_MISSION_UNAVAILABLE = "mission_unavailable"
BINDING_MATCHES = "matches_current_revision"
BINDINGS = (
    BINDING_DIGEST_MISMATCH, BINDING_EXPIRED, BINDING_MATCHES,
    BINDING_MISSION_UNAVAILABLE, BINDING_NOT_PENDING,
    BINDING_PROPOSAL_UNCONFIRMED, BINDING_STALE_REVISION,
)

PROBLEM_STORE_UNREADABLE = "local_request_store_unreadable"
PROBLEM_STORE_FULL = "local_request_store_full"
# The one refusal every approval through this surface receives.
PROBLEM_APPROVAL_UNAUTHENTICATED = "local_request_approval_unauthenticated"

# Task 8: the operator-attested approval of a request (one per request).
# Additive-optional: a document written before it existed has none.
REQUEST_OPTIONAL_KEYS = ("attested_approval",)
ATTESTED_KEYS = (
    "state", "decision_id", "binding", "relayed_reply", "relay_ref",
    "attested_at", "outcome", "provenance_label", "residual_risk",
    "evidence_status",
)
# The ONLY replies that count as the human's affirmative: the WHOLE reply,
# after trimming surrounding whitespace and case folding, must equal one of
# these. Never a substring ("not approved" contains "approved"), never
# quoted or reported speech, never interpreted.
AFFIRMATIVE_REPLIES = ("approved", "approve")
# What the relayed reply and relay_ref are: the Operator's attestation,
# never independently verified evidence of who replied.
EVIDENCE_STATUS = "operator_attestation_only_not_sender_evidence"


def is_exact_affirmative(reply):
    return isinstance(reply, str) and reply.strip().casefold() in AFFIRMATIVE_REPLIES
ATTESTED_BINDING_KEYS = (
    "mission_id", "revision", "proposal_digest_sha256",
    "approved_action_scope", "approved_delivery_targets", "expires_at",
)
ATTESTED_RESERVED = "RESERVED"
ATTESTED_APPLIED = "APPLIED"
ATTESTED_REFUSED = "REFUSED"
# Outcome unknown: never applied again, reconciled only from a durable record.
ATTESTED_HOLD = "HOLD"
ATTESTED_STATES = (ATTESTED_APPLIED, ATTESTED_HOLD, ATTESTED_REFUSED,
                   ATTESTED_RESERVED)
# Exact-value pinned. The relayed reply and the relay reference are what
# the Operator REPORTS; they are evidence of the claim, not proof of it.
MAX_RELAYED_REPLY_CHARS = 200
MAX_RELAY_REF_CHARS = 128

DOCUMENT_KEYS = ("schema_version", "requests", "control_capabilities")
REQUEST_KEYS = (
    "request_ref", "mission_request_id", "proposal", "proposal_digest_sha256",
    "mission_id", "created_at", "state", "cancellation", "approval_refusals",
)
CANCELLATION_KEYS = ("cancelled_at", "mission_id", "revision",
                     "proposal_digest_sha256")
REFUSAL_KEYS = (
    "attempt_digest_sha256", "revision", "proposal_digest_sha256",
    "expires_at", "binding", "refused_at", "problem",
)
CAPABILITY_KEYS = ("workflow_id", "action", "revision", "issued_at",
                   "expires_at", "consumed_at")

_FORBIDDEN_MODE_BITS = 0o077
_HEX = frozenset("0123456789abcdef")


class LocalRequestStoreError(Exception):
    """The surface's document is unusable or full; ``problem`` is a
    distinct ``local_request_*`` code and the message is actionable."""

    def __init__(self, message, problem=PROBLEM_STORE_UNREADABLE):
        super(LocalRequestStoreError, self).__init__(message)
        self.problem = problem


def default_document():
    return {"schema_version": SCHEMA_VERSION, "requests": {},
            "control_capabilities": {}}


def new_request_ref():
    return "%s-%s" % (REQUEST_REF_PREFIX,
                      secrets.token_hex(mission_record.ID_HEX_CHARS // 2))


def request_ref_problem(value):
    return mission_record.id_problem(value, REQUEST_REF_PREFIX)


def token_digest(token):
    """The same digest Mission Core records for the withdrawal key."""
    return mission_record.withdrawal_key_digest(token)


def _token_problem(token):
    if not isinstance(token, str) or not token:
        return capability_contract.PROBLEM_CAPABILITY_MISSING
    body = token[len(TOKEN_PREFIX):]
    if not token.startswith(TOKEN_PREFIX) or len(body) != TOKEN_HEX_CHARS or (
        any(ch not in _HEX for ch in body)
    ):
        return capability_contract.PROBLEM_CAPABILITY_UNKNOWN
    return None


# -- validation (closed, fail-closed) ----------------------------------


def _bad(where, message):
    raise LocalRequestStoreError("%s %s" % (where, message))


def _closed(value, keys, where):
    if not isinstance(value, dict):
        _bad(where, "must be an object")
    if sorted(value) != sorted(keys):
        _bad(where, "must carry exactly the keys %s" % (", ".join(keys),))


def _int(value, where, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _bad(where, "must be a non-negative integer")


def _hex64(value, where):
    if not isinstance(value, str) or len(value) != 64 or any(
        ch not in _HEX for ch in value
    ):
        _bad(where, "must be 64 lowercase hex characters")


def _validate_attested(value, request, where):
    _closed(value, ATTESTED_KEYS, where)
    if value["state"] not in ATTESTED_STATES:
        _bad(where, "state is not in the closed vocabulary")
    if mission_record.id_problem(value["decision_id"],
                                 mission_record.DECISION_ID_PREFIX):
        _bad(where, "decision_id must be an md- id")
    binding = value["binding"]
    _closed(binding, ATTESTED_BINDING_KEYS, where + ".binding")
    if binding["mission_id"] != request["mission_id"]:
        _bad(where, "binding must name the request's own Mission")
    _int(binding["revision"], where + ".binding.revision")
    _hex64(binding["proposal_digest_sha256"],
           where + ".binding.proposal_digest_sha256")
    for name in ("approved_action_scope", "approved_delivery_targets"):
        if not isinstance(binding[name], list) or binding[name] != sorted(
            binding[name]
        ) or not all(isinstance(item, str) for item in binding[name]):
            _bad(where, "binding.%s must be a sorted list of strings" % name)
    _int(binding["expires_at"], where + ".binding.expires_at")
    for name, limit in (("relayed_reply", MAX_RELAYED_REPLY_CHARS),
                        ("relay_ref", MAX_RELAY_REF_CHARS)):
        if not isinstance(value[name], str) or len(value[name]) > limit:
            _bad(where, "%s must be a string of at most %d" % (name, limit))
    if not is_exact_affirmative(value["relayed_reply"]):
        _bad(where, "relayed_reply is not an exact affirmative")
    if value["evidence_status"] != EVIDENCE_STATUS:
        _bad(where, "evidence_status must be %r" % EVIDENCE_STATUS)
    _int(value["attested_at"], where + ".attested_at")
    outcome = value["outcome"]
    if value["state"] in (ATTESTED_RESERVED, ATTESTED_HOLD):
        if outcome is not None:
            _bad(where, "a %s attestation carries no outcome" % value["state"])
    else:
        _closed(outcome, ("authorization_id", "problem"), where + ".outcome")
        if value["state"] == ATTESTED_APPLIED and mission_record.id_problem(
            outcome["authorization_id"], mission_record.AUTHORIZATION_ID_PREFIX
        ):
            _bad(where, "an APPLIED attestation names its authorization")
        if value["state"] == ATTESTED_REFUSED and (
            outcome["authorization_id"] is not None
            or not isinstance(outcome["problem"], str)
        ):
            _bad(where, "a REFUSED attestation names its problem, no"
                 " authorization")
    if value["provenance_label"] != {
        "principal_kind": mission_record.PRINCIPAL_KIND_OPERATOR_ATTESTED,
        "proof": mission_record.PROOF_OPERATOR_ATTESTED,
    } or value["residual_risk"] != mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK:
        _bad(where, "provenance_label and residual_risk must state the"
             " operator-attested trust exactly")


def _validate_request(ref, value, where):
    if not isinstance(value, dict):
        _bad(where, "must be an object")
    keys = sorted(k for k in value if k not in REQUEST_OPTIONAL_KEYS)
    if keys != sorted(REQUEST_KEYS):
        _bad(where, "must carry exactly the keys %s (plus optional %s)"
             % (", ".join(REQUEST_KEYS), ", ".join(REQUEST_OPTIONAL_KEYS)))
    if "attested_approval" in value:
        if value["mission_id"] is None:
            _bad(where, "an attested approval needs the request's confirmed"
                 " Mission")
        _validate_attested(value["attested_approval"], value,
                           where + ".attested_approval")
    if request_ref_problem(ref) is not None or value["request_ref"] != ref:
        _bad(where, "request_ref must be its own key and an lr- id")
    if mission_record.id_problem(value["mission_request_id"],
                                 mission_record.REQUEST_ID_PREFIX):
        _bad(where, "mission_request_id must be an mq- id")
    try:
        clean = mission_record.validate_proposal(value["proposal"])
    except mission_record.MissionError as exc:
        _bad(where, "proposal is not a valid Mission proposal (%s)" % exc)
    if clean != value["proposal"]:
        _bad(where, "proposal is not in normalized form")
    if value["proposal_digest_sha256"] != mission_record.proposal_digest(clean):
        _bad(where, "proposal_digest_sha256 does not match the proposal")
    if value["mission_id"] is not None and mission_record.id_problem(
        value["mission_id"], mission_record.MISSION_ID_PREFIX
    ):
        _bad(where, "mission_id must be null or an mn- id")
    _int(value["created_at"], where + ".created_at")
    if value["state"] not in STATES:
        _bad(where, "state must be one of %s" % (", ".join(STATES),))
    cancellation = value["cancellation"]
    if value["state"] == STATE_CANCELLED:
        _closed(cancellation, CANCELLATION_KEYS, where + ".cancellation")
        _int(cancellation["cancelled_at"], where + ".cancellation.cancelled_at")
        if cancellation["mission_id"] != value["mission_id"] or (
            value["mission_id"] is None
        ):
            _bad(where, "cancellation must bind the request's confirmed Mission")
        if cancellation["revision"] != 1 or cancellation[
            "proposal_digest_sha256"
        ] != value["proposal_digest_sha256"]:
            _bad(where, "cancellation must bind revision 1 and its digest")
    elif cancellation is not None:
        _bad(where, "an OPEN request carries no cancellation")
    refusals = value["approval_refusals"]
    if not isinstance(refusals, list) or len(refusals) > MAX_APPROVAL_REFUSALS:
        _bad(where, "approval_refusals must be a list of at most %d"
             % MAX_APPROVAL_REFUSALS)
    seen = set()
    for index, refusal in enumerate(refusals):
        sub = "%s.approval_refusals[%d]" % (where, index)
        _closed(refusal, REFUSAL_KEYS, sub)
        _hex64(refusal["attempt_digest_sha256"], sub + ".attempt_digest_sha256")
        if refusal["attempt_digest_sha256"] in seen:
            _bad(sub, "repeats an earlier attempt")
        seen.add(refusal["attempt_digest_sha256"])
        _int(refusal["revision"], sub + ".revision")
        _hex64(refusal["proposal_digest_sha256"], sub + ".proposal_digest_sha256")
        _int(refusal["expires_at"], sub + ".expires_at", optional=True)
        if refusal["binding"] not in BINDINGS:
            _bad(sub, "binding is not in the closed vocabulary")
        _int(refusal["refused_at"], sub + ".refused_at")
        if refusal["problem"] != PROBLEM_APPROVAL_UNAUTHENTICATED:
            _bad(sub, "problem must be %r" % PROBLEM_APPROVAL_UNAUTHENTICATED)


def _validate_capability(key, value, requests, where):
    _hex64(key, where)
    _closed(value, CAPABILITY_KEYS, where)
    if value["workflow_id"] not in requests:
        _bad(where, "binds a request this document does not hold")
    if value["action"] not in CONTROL_ACTIONS or value["revision"] != 1:
        _bad(where, "binds an action or revision outside the closed set")
    _int(value["issued_at"], where + ".issued_at")
    _int(value["expires_at"], where + ".expires_at")
    if value["expires_at"] != value["issued_at"] + (
        CONTROL_CAPABILITY_VALIDITY_SECONDS
    ):
        _bad(where, "expires_at must be issued_at plus the fixed validity")
    _int(value["consumed_at"], where + ".consumed_at", optional=True)


def validate_document(document, path="<document>"):
    _closed(document, DOCUMENT_KEYS, path)
    if document["schema_version"] != SCHEMA_VERSION:
        _bad(path, "schema_version must be %d" % SCHEMA_VERSION)
    requests = document["requests"]
    capabilities = document["control_capabilities"]
    if not isinstance(requests, dict) or len(requests) > MAX_REQUEST_RECORDS:
        _bad(path, "requests must be a map of at most %d" % MAX_REQUEST_RECORDS)
    if not isinstance(capabilities, dict) or len(capabilities) > (
        MAX_CONTROL_CAPABILITIES
    ):
        _bad(path, "control_capabilities must be a map of at most %d"
             % MAX_CONTROL_CAPABILITIES)
    for ref, value in requests.items():
        _validate_request(ref, value, "%s.requests[%s]" % (path, ref))
    per_request = {}
    for key, value in capabilities.items():
        _validate_capability(key, value, requests,
                             "%s.control_capabilities[%s]" % (path, key))
        per_request[value["workflow_id"]] = per_request.get(
            value["workflow_id"], 0) + 1
    # Every request holds EXACTLY one control capability (finding 5: an
    # OPEN request with none is malformed, not recoverable).
    for ref in requests:
        count = per_request.get(ref, 0)
        if count != 1:
            _bad(path, "request %s holds %d control capabilities, not one"
                 % (ref, count))
    for ref, value in requests.items():
        if value["state"] == STATE_CANCELLED and not any(
            c["workflow_id"] == ref and c["consumed_at"] is not None
            for c in capabilities.values()
        ):
            _bad(path, "request %s is CANCELLED without its consumed control"
                 " capability" % ref)
    return document


# -- the protected store -------------------------------------------------


def _refuse_open_directory(directory):
    try:
        mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        raise LocalRequestStoreError("%s is not a directory" % directory)
    if mode & _FORBIDDEN_MODE_BITS:
        raise LocalRequestStoreError(
            "state directory %s is accessible by group/other (mode %o);"
            " nothing is read, locked, or written. Fix with: chmod 700 %r"
            % (directory, stat.S_IMODE(mode), directory))


class LocalRequestStore(object):
    """Atomic load/save of the surface's one document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, REQUESTS_FILE_NAME)

    def lock(self):
        _refuse_open_directory(self.directory)
        return exclusive_store_lock(self.directory, REQUESTS_LOCK_FILE_NAME)

    def load(self):
        _refuse_open_directory(self.directory)
        if not os.path.exists(self.path):
            return default_document()
        mode = os.stat(self.path).st_mode
        if mode & _FORBIDDEN_MODE_BITS:
            raise LocalRequestStoreError(
                "%s is accessible by group/other (mode %o). Fix with:"
                " chmod 600 %r" % (self.path, stat.S_IMODE(mode), self.path))
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise LocalRequestStoreError(
                "%s could not be read as JSON (%s)" % (self.path, exc))
        return validate_document(document, self.path)

    def save(self, document):
        _refuse_open_directory(self.directory)
        validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".local-requests-")


# -- the one-shot proposal control capability ----------------------------


class ProposalControlAuthority(capability_contract.CapabilityAuthority):
    """``CapabilityAuthority`` over ONE loaded document's
    ``control_capabilities`` map. ``workflow_id`` is the request_ref,
    ``action`` is the one control action, ``revision`` is always 1 (the
    revision the creator proposed). The caller holds the store lock and
    saves the document."""

    def __init__(self, capabilities):
        self._capabilities = capabilities

    def mint(self, workflow_id, action, revision, now):
        if action not in CONTROL_ACTIONS or revision != 1:
            raise capability_contract.CapabilityError(
                "only %r at revision 1 can be minted"
                % ACTION_CANCEL_PENDING_PROPOSAL)
        if len(self._capabilities) >= MAX_CONTROL_CAPABILITIES:
            raise capability_contract.CapabilityError(
                "%d control capabilities are held; the hard bound is %d"
                % (len(self._capabilities), MAX_CONTROL_CAPABILITIES))
        if any(c["workflow_id"] == workflow_id
               for c in self._capabilities.values()):
            raise capability_contract.CapabilityError(
                "request %s already holds its control capability" % workflow_id)
        token = TOKEN_PREFIX + secrets.token_hex(TOKEN_HEX_CHARS // 2)
        self._capabilities[token_digest(token)] = {
            "workflow_id": workflow_id, "action": action, "revision": revision,
            "issued_at": now,
            "expires_at": now + CONTROL_CAPABILITY_VALIDITY_SECONDS,
            "consumed_at": None,
        }
        return token

    def validate_and_consume(self, token, workflow_id, action, revision, now):
        return self._consume(token, workflow_id, action, revision, now, True)

    def consume_to_complete(self, token, workflow_id, action, revision, now):
        """Consume the capability to COMPLETE a withdrawal Mission Core has
        already recorded with this same key (an interrupted cancel):
        binding and single use are checked; expiry is not. Mission Core
        binds THIS capability's original expiry (minted here, passed with
        its digest, never recomputed or extended) and refuses a first
        withdrawal at or after it, so a recorded marker was committed
        before that original expiry."""
        return self._consume(token, workflow_id, action, revision, now, False)

    def _consume(self, token, workflow_id, action, revision, now, check_expiry):
        problem = _token_problem(token)
        if problem is not None:
            return False, problem, "no well-formed control capability presented"
        digest = token_digest(token)
        entry = None
        for key, value in self._capabilities.items():
            if hmac.compare_digest(key, digest):
                entry = value
        if entry is None:
            return (False, capability_contract.PROBLEM_CAPABILITY_UNKNOWN,
                    "this control capability was not issued here")
        if entry["workflow_id"] != workflow_id or entry["action"] != action or (
            entry["revision"] != revision
        ):
            return (False, capability_contract.PROBLEM_CAPABILITY_MISMATCH,
                    "this control capability belongs to a different request"
                    " or action")
        if entry["consumed_at"] is not None:
            return (False, capability_contract.PROBLEM_CAPABILITY_CONSUMED,
                    "this control capability was already used")
        if check_expiry and now >= entry["expires_at"]:
            return (False, capability_contract.PROBLEM_CAPABILITY_EXPIRED,
                    "this control capability expired")
        entry["consumed_at"] = now
        return True, None, None

    def compact(self, now, non_actionable, oracle_errors):
        """Retire nothing: a consumed capability is the durable record a
        CANCELLED request is validated against, and an unconsumed one is
        bound to a request record that is never evicted."""
        return []
