"""One atomic authority document for the Mission registry and ledger.

``missions.json`` is ONE JSON document holding the Mission registry,
the authorization records, the append-only Authority Ledger, and the
DI-issued id reservations:

    {"mission_store_schema_version": 1,
     "missions": {<mission_id>: <mission record>},
     "authorizations": {<authorization_id>: <authorization record>},
     "authority_ledger": [<entry>, ...],
     "reservations": {<request, decision or state operation id>: <reservation>},
     "mission_state": {<mission_id>: <Mission State record>}}

Registry and ledger therefore commit in the SAME ``os.replace`` and can
never tear apart. A reservation records the id's kind, the authenticated
context that asked for it, and ``consumed_by``: ``null`` while unused,
the Mission id once a request id created a Mission, the decision id
itself once a decision id was applied (the decision record then lives in
its Mission's history). Reservations are never evicted. The file lives in an injected protected directory
(mode 600 in a mode 700 directory) and has its own lock file
(``missions.lock``), so it never serializes against another store.
Writes go through the shared stdlib-only ``workflow_authority.atomic``
primitives; every writer holds ``exclusive_store_lock`` around its whole
load-modify-save cycle.

Loading fails closed: bad JSON, an unknown schema version, an unknown or
missing top-level key, any invalid record or ledger entry, a cross-
record inconsistency, a decision history that does not reproduce the
stored state/authorizations/ledger (``reconcile_mission_history``, the
same function the central validator runs), or a group/other-accessible
mode on the FILE or on the EXISTING DIRECTORY raises ``MissionStoreError``
and the file is NEVER silently reinitialized. The directory boundary is
enforced before every read, lock, and write. A
missing file yields a fresh default document. Every record and every
ledger entry is validated on every load AND every save; ``save``
validates before anything touches the filesystem, so an invalid
document can never clobber a valid store.

Hard caps are module constants, never derived from input. At a cap the
store REFUSES; authority history and reservations are never pruned or
evicted (a retry might still carry a reservation). Counts are exact.

Mission State (Task 5) and the ONE compatibility rule (R-3). The schema
version stays 1 and ``mission_state`` is the single additive-optional
top-level key: when an otherwise-valid document does not carry it,
``load`` supplies ``{}``; every OTHER unknown or missing top-level key
still refuses, ``save`` never supplies it, and there is no version bump,
no migration module and no second compatibility path. Stated residual
limit: this is backward compatibility only. A document this layer has
written (which carries ``mission_state``) read by code from before this
key existed refuses as an unknown key, which is fail-closed and
intended. ``mission_state`` maps ``mission_id`` to one closed state
record (``mission.state``); every record is validated on every load and
save and cross-checked against the same document: the key equals the
record's mission id and names an existing Mission; every contract
activation resolves to a stored revision carrying that proposal digest
and a ``proof_contract`` with that contract digest, and to a stored
authorization of that revision with that authority digest (liveness is
the service's question, asked through the ONE validation path); every
claim and evidence record names a requirement of its activation's
contract, and an accepted evidence kind is one that requirement
declares; every blocker's severity equals what the approved degradation
policy derives; every MISSION dependency names an existing other
Mission, a bound required slot matches its approved target where that
target is immutable (exact mission at a stored revision digest, exact
resource key); the dependency graph across all records is acyclic; every
stored checkpoint recomputes to itself; and every applied state
operation is a consumed ``state_operation`` reservation held by the same
authenticated context. Reservation kind ``state_operation`` (id prefix
``mo-``) joins the existing map with its own cap.

Event Journal and snapshots (Task 7). The per-Mission applied-operation
ledger inside the state record IS the Mission's Event Journal
(``mission.journal``): ordered, stable-identity events, bound to the
revision in force, committed in the SAME ``os.replace`` as the effect
and the consumed reservation they describe, so accepted Mission state
and journal state can never split. The state record's first additive-
optional key, ``snapshot``, caches the supported derived state at a
journal position bound to the schema version, the revision, the
position and the chain digest; the store re-derives the projection
under the contract bound at that position on every load and save and
refuses one that does not recompute (``mission_journal_snapshot_disagrees``),
exactly as it re-proves checkpoints. No top-level key is added: the
journal, the snapshot and the cursor live inside ``mission_state``, so
the R-3 rule above stays the one compatibility rule; a state record
written before ``snapshot`` existed is read as "no snapshot" and
nothing is supplied on load.

Observation and reconciliation (Task 7, Stage 2). Observation
(``mission.observation``) is a read of this document plus caller-
injected source adapters: no lock, no write, no minted id. A
reconciliation that records a meaningful change is an ordinary
``reconcile`` state operation whose one effect is an entry in the state
record's second additive-optional key, ``reconciliations``
(``mission.reconciliation``), committed in the same ``os.replace`` as
its ledger entry, consumed reservation and the re-bound snapshot; the
store re-derives each record's chain binding and findings on every load
and save and refuses one that does not recompute
(``mission_reconciliation_disagrees``). Still no top-level key is added,
and a state record written before the key existed is read as holding
no reconciliation.

What the store re-proves from local durable facts alone (R-24, R-25):
the whole locally provable closure conjunction of every persisted
COMPLETED closure (proof, required artifacts, HARD blockers, required
slots, readiness), over the state as of the closure sequence with the
closure's own ``closed_at`` as the clock, and the asserted reason of an
unsuccessful closure (``budget_exhausted`` requires the attempts to have
reached the approved bound at that sequence, ``hard_blocker_unresolvable``
requires an active HARD blocker) — ``mission_state_closure_not_provable``;
the historical authority window of every activation and COMPLETED
closure against its bound authorization's RECORDED ``issued_at``,
``expires_at`` and ``revocation.revoked_at`` — ``mission_state_authority_window``
(``issued_at <= T``, ``T < expires_at`` STRICT because Task 4 refuses live
use at ``now >= expires_at`` so the service could never have produced
``T == expires_at``, and ``T <= revoked_at`` NON-STRICT because a
``superseded_by_edit`` revocation carries the EDIT's application second
and an operation validated live in that same second is legitimate; the
only state the relaxation admits is a record timestamped at exactly the
revocation instant, which the service cannot produce and which a forgery
could reach only by also defeating the local closure conjunction, the
monotone time base, sequence binding, reservation consumption and
``reconcile_registry``);
and the monotone time base (``mission.state_validation``). Because these
compare recorded timestamps and never the wall clock, a closure recorded
before an expiry stays valid forever after that expiry passes, and one
recorded before a ``superseded_by_edit`` revocation stays valid forever
after that revocation. What it does NOT re-prove, deliberately: another
Mission's current revision, progress or closure binding, and CURRENT
authorization liveness. Those change current eligibility only and belong
to the service's ``complete`` path and the read-time
``closure_eligibility`` projection, so elapsed time and foreign EDITs
never make a valid historical record unreadable.

Attested receipt references (Task 7, Stage 2). Beside the activation
bindings, every artifact carrying the ``receipt_attestation`` marker
must name a stored authorization of ITS Mission with exactly the digest
the marker holds, bound to the revision the attesting operation cites,
permitting the delivery target, and recorded inside that authorization's
recorded window (R-25.1) — the same cross-document discipline an
activation must satisfy. The marker's digests are binding values, not
credentials: one that resolves to nothing, to another Mission, to
another revision or to a non-delivery authorization makes the record
unreadable. Nothing here validates a receipt; the delivery layer's
validator is never imported.
"""

import json
import os
import stat

from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

from mission import authorization as authorization_module
from mission import journal
from mission import manifest
from mission import progress as progress_module
from mission import reconciliation
from mission import record
from mission import state as state_module
from mission import state_validation
from mission.manifest import (  # noqa: F401 (re-exported bounds)
    MAX_MISSION_DECISIONS,
    MAX_MISSION_REVISIONS,
)

MISSION_STORE_SCHEMA_VERSION = 1
MISSIONS_FILE_NAME = "missions.json"
MISSIONS_LOCK_FILE_NAME = "missions.lock"
TOP_LEVEL_KEYS = (
    "mission_store_schema_version", "missions", "authorizations",
    "authority_ledger", "reservations", "mission_state",
)
# The ONE additive-optional key (R-3): supplied as {} by ``load`` when a
# document written before it existed does not carry it.
OPTIONAL_TOP_LEVEL_KEY = "mission_state"

# Hard caps, never derived from input. Exact-value pinned.
MAX_MISSION_RECORDS = 1024
MAX_AUTHORIZATION_RECORDS = 4096
MAX_AUTHORITY_LEDGER_ENTRIES = 16384
MAX_RESERVED_REQUEST_IDS = 4096
MAX_RESERVED_DECISION_IDS = 4096
MAX_RESERVED_STATE_OPERATION_IDS = 65536
MAX_MISSION_STATE_RECORDS = 1024

RESERVATION_KIND_REQUEST = "request"
RESERVATION_KIND_DECISION = "decision"
RESERVATION_KIND_STATE_OPERATION = "state_operation"
RESERVATION_KINDS = (RESERVATION_KIND_REQUEST, RESERVATION_KIND_DECISION,
                     RESERVATION_KIND_STATE_OPERATION)
# The id prefix a reservation of each kind reserves and is consumed by.
RESERVATION_PREFIXES = {
    RESERVATION_KIND_REQUEST: record.REQUEST_ID_PREFIX,
    RESERVATION_KIND_DECISION: record.DECISION_ID_PREFIX,
    RESERVATION_KIND_STATE_OPERATION: record.STATE_OPERATION_ID_PREFIX,
}
RESERVATION_CAPS = {
    RESERVATION_KIND_REQUEST: MAX_RESERVED_REQUEST_IDS,
    RESERVATION_KIND_DECISION: MAX_RESERVED_DECISION_IDS,
    RESERVATION_KIND_STATE_OPERATION: MAX_RESERVED_STATE_OPERATION_IDS,
}
RESERVATION_KEYS = ("reserved_at", "kind", "context", "consumed_by")

PROBLEM_STORE_UNREADABLE = authorization_module.PROBLEM_STORE_UNREADABLE
PROBLEM_STORE_FULL = "mission_store_full"

# Any group/other access bit: the store carries authority records.
_FORBIDDEN_STORE_MODE_BITS = 0o077


class MissionStoreError(Exception):
    """The Mission store is unreadable, malformed, or full; ``problem`` is
    ``mission_store_unreadable`` or ``mission_store_full``."""

    def __init__(self, message, problem=PROBLEM_STORE_UNREADABLE):
        super(MissionStoreError, self).__init__(message)
        self.problem = problem


def default_document():
    return {
        "mission_store_schema_version": MISSION_STORE_SCHEMA_VERSION,
        "missions": {},
        "authorizations": {},
        "authority_ledger": [],
        "reservations": {},
        "mission_state": {},
    }


def _unreadable(path, message):
    raise MissionStoreError(
        "mission store %s %s; move the file aside (keeping it for"
        " inspection) — it is NOT safe to delete it: it carries authority"
        " records" % (path, message), PROBLEM_STORE_UNREADABLE,
    )


def _full(path, message):
    raise MissionStoreError("mission store %s %s" % (path, message),
                            PROBLEM_STORE_FULL)


def validate_reservation(value, location):
    record.require_dict(value, location)
    record.require_closed_keys(value, RESERVATION_KEYS, location)
    record.require_timestamp(value["reserved_at"], location + ".reserved_at")
    record.require_member(value["kind"], RESERVATION_KINDS, location + ".kind")
    record.validate_context_dict(value["context"], location + ".context")
    consumed = value["consumed_by"]
    if consumed is not None:
        prefix = (record.MISSION_ID_PREFIX
                  if value["kind"] == RESERVATION_KIND_REQUEST
                  else RESERVATION_PREFIXES[value["kind"]])
        record.require_id(consumed, prefix, location + ".consumed_by")
    return value


def validate_document(document, path="<document>"):
    """Every record, entry and cross-reference, or MissionStoreError."""
    try:
        _validate_document(document, path)
    except record.MissionError as exc:
        if exc.problem == state_module.PROBLEM_STATE_FULL:
            _full(path, "%s" % exc)
        _unreadable(path, "is malformed (%s): %s" % (exc.problem, exc))
    return document


def _validate_document(document, path):
    if not isinstance(document, dict):
        _unreadable(path, "must contain a JSON object, not %s"
                    % type(document).__name__)
    version = document.get("mission_store_schema_version")
    # Typed before compared (R-41): bool and integral float are refused,
    # not compared away.
    if not isinstance(version, int) or isinstance(version, bool) or (
        version != MISSION_STORE_SCHEMA_VERSION
    ):
        _unreadable(path, "has mission_store_schema_version %r; this layer"
                    " understands only %d" % (version, MISSION_STORE_SCHEMA_VERSION))
    unknown = sorted(set(document) - set(TOP_LEVEL_KEYS))
    if unknown:
        _unreadable(path, "has unknown top-level keys: %s; the key set is"
                    " closed" % ", ".join(map(repr, unknown)))
    missing = sorted(set(TOP_LEVEL_KEYS) - set(document))
    if missing:
        _unreadable(path, "is missing required keys: %s"
                    % ", ".join(map(repr, missing)))
    missions = document["missions"]
    authorizations = document["authorizations"]
    ledger = document["authority_ledger"]
    reservations = document["reservations"]
    mission_state = document["mission_state"]
    for name, value, kind in (("missions", missions, dict),
                              ("authorizations", authorizations, dict),
                              ("authority_ledger", ledger, list),
                              ("reservations", reservations, dict),
                              ("mission_state", mission_state, dict)):
        if not isinstance(value, kind):
            _unreadable(path, "key %r must be a JSON %s, not %s"
                        % (name, kind.__name__, type(value).__name__))
    if len(missions) > MAX_MISSION_RECORDS:
        _full(path, "holds %d missions; the hard bound is %d"
              % (len(missions), MAX_MISSION_RECORDS))
    if len(authorizations) > MAX_AUTHORIZATION_RECORDS:
        _full(path, "holds %d authorizations; the hard bound is %d"
              % (len(authorizations), MAX_AUTHORIZATION_RECORDS))
    if len(ledger) > MAX_AUTHORITY_LEDGER_ENTRIES:
        _full(path, "holds %d ledger entries; the hard bound is %d"
              % (len(ledger), MAX_AUTHORITY_LEDGER_ENTRIES))
    held = dict((kind, 0) for kind in RESERVATION_KINDS)
    for reserved_id, reservation in reservations.items():
        where = "reservation %r" % reserved_id
        validate_reservation(reservation, where)
        record.require_id(reserved_id, RESERVATION_PREFIXES[reservation["kind"]],
                          where + " key")
        held[reservation["kind"]] += 1
    for kind in RESERVATION_KINDS:
        if held[kind] > RESERVATION_CAPS[kind]:
            _full(path, "holds %d reserved %s ids; the hard bound is %d and a"
                  " reservation is never evicted"
                  % (held[kind], kind, RESERVATION_CAPS[kind]))
    if len(mission_state) > MAX_MISSION_STATE_RECORDS:
        _full(path, "holds %d mission state records; the hard bound is %d"
              % (len(mission_state), MAX_MISSION_STATE_RECORDS))
    request_ids = {}
    for mission_id, mission in missions.items():
        manifest.validate_mission_record(mission, "mission %r" % mission_id)
        if mission["mission_id"] != mission_id:
            _unreadable(path, "mission keyed %r carries mission_id %r"
                        % (mission_id, mission["mission_id"]))
        if mission["request_id"] in request_ids:
            _unreadable(path, "missions %r and %r share request id %r"
                        % (request_ids[mission["request_id"]], mission_id,
                           mission["request_id"]))
        request_ids[mission["request_id"]] = mission_id
        reservation = reservations.get(mission["request_id"])
        if reservation is None or reservation["kind"] != (
            RESERVATION_KIND_REQUEST
        ) or reservation["consumed_by"] != mission_id:
            _unreadable(path, "mission %r request id %r is not a consumed"
                        " request reservation" % (mission_id, mission["request_id"]))
        for decision in mission["decisions"]:
            reservation = reservations.get(decision["decision_id"])
            if reservation is None or reservation["kind"] != (
                RESERVATION_KIND_DECISION
            ) or reservation["consumed_by"] != decision["decision_id"]:
                _unreadable(path, "mission %r decision %r is not a consumed"
                            " decision reservation"
                            % (mission_id, decision["decision_id"]))
        for authorization_id in mission["authorization_ids"]:
            authorization = authorizations.get(authorization_id)
            if authorization is None or authorization.get("mission_id") != (
                mission_id
            ):
                _unreadable(path, "mission %r references authorization %r,"
                            " which is absent or bound to another mission"
                            % (mission_id, authorization_id))
    for authorization_id, authorization in authorizations.items():
        authorization_module.validate_authorization_record(
            authorization, "authorization %r" % authorization_id
        )
        if authorization["authorization_id"] != authorization_id:
            _unreadable(path, "authorization keyed %r carries id %r"
                        % (authorization_id, authorization["authorization_id"]))
        mission = missions.get(authorization["mission_id"])
        if mission is None or authorization_id not in mission["authorization_ids"]:
            _unreadable(path, "authorization %r is not referenced by its"
                        " mission %r" % (authorization_id,
                                         authorization["mission_id"]))
        if manifest.revision_entry(mission, authorization["revision"]) is None:
            _unreadable(path, "authorization %r binds revision %d, which"
                        " mission %r does not hold"
                        % (authorization_id, authorization["revision"],
                           authorization["mission_id"]))
    seen_entries = set()
    for index, entry in enumerate(ledger):
        authorization_module.validate_ledger_entry(
            entry, "authority_ledger[%d]" % index
        )
        if entry["entry_id"] in seen_entries:
            _unreadable(path, "authority_ledger[%d] repeats entry id %r"
                        % (index, entry["entry_id"]))
        seen_entries.add(entry["entry_id"])
        if entry["mission_id"] not in missions:
            _unreadable(path, "authority_ledger[%d] names unknown mission %r"
                        % (index, entry["mission_id"]))
        if entry["authorization_id"] is not None and (
            entry["authorization_id"] not in authorizations
        ):
            _unreadable(path, "authority_ledger[%d] names unknown"
                        " authorization %r" % (index, entry["authorization_id"]))
    # R-44: every revision ordinal Task 5 CONSUMES from a Mission record
    # (looked up, keyed into the registry view, copied into an activation
    # or an outcome) is a genuine int — bool and float refused — checked
    # here so the document refuses on load and save, never later at
    # activation construction. manifest.py's own acceptance is untouched.
    for mission_id, mission in missions.items():
        _require_revision_identities(mission, mission_id, path)
    for mission_id, state in mission_state.items():
        _validate_mission_state(document, mission_id, state, path)
    disagreement = progress_module.dependency_graph_problem(mission_state)
    if disagreement is not None:
        _unreadable(path, "does not reconcile (%s): %s" % disagreement)
    # The decision history is the authority of record: every mission's
    # stored state, revisions, authorizations, reservations and ledger
    # must be exactly what replaying its decisions produces (the SAME
    # function the central validator runs; no second checking path).
    disagreement = authorization_module.reconcile_registry(document)
    if disagreement is not None:
        _unreadable(path, "does not reconcile (%s): %s" % disagreement)
    for mission_id, mission in missions.items():
        disagreement = authorization_module.reconcile_mission_history(
            document, mission
        )
        if disagreement is not None:
            _unreadable(path, "does not reconcile (%s): %s" % disagreement)


# -- Mission State cross-references --------------------------------------


def _require_revision_identities(mission, mission_id, path):
    try:
        record.require_int(mission["current_revision"],
                           "mission %r current_revision" % mission_id, minimum=1)
        for index, entry in enumerate(mission["revisions"]):
            record.require_int(entry["revision"],
                               "mission %r revisions[%d].revision" % (mission_id, index),
                               minimum=1)
    except record.MissionError as exc:
        _unreadable(path, "(%s): %s" % (state_module.PROBLEM_REVISION_IDENTITY_MALFORMED,
                                        exc))


def activation_contract(document, mission, activation, where):
    """The proof contract the activation binds, re-derived from the
    stored revision's approved proposal; refuses when the binding does
    not resolve exactly. Never a copy held elsewhere (R-5.4)."""
    entry = manifest.revision_entry(mission, activation["revision"])
    if entry is None:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "%s binds revision %r, which mission %s does not hold"
                    % (where, activation["revision"], mission["mission_id"]))
    if entry["proposal_digest_sha256"] != activation["proposal_digest_sha256"]:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "%s binds a proposal digest revision %d does not carry"
                    % (where, activation["revision"]))
    contract = entry["proposal"].get("proof_contract")
    if contract is None:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "%s binds revision %d, whose approved proposal carries no"
                    " proof_contract" % (where, activation["revision"]))
    if record.proof_contract_digest(contract) != activation["contract_digest_sha256"]:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "%s binds a contract digest that revision %d's proof_contract"
                    " does not have" % (where, activation["revision"]))
    return contract


def registry_view(document):
    """The plain-data view the pure prerequisite checks read, per Mission:
    ``current_revision``, ``revision_digests``, the Task 5 ``progress``
    (None without a state record), and the closure's bound activation id
    with that activation's revision and proposal digest (None when not
    closed or closed without an activation). It carries no authorization
    or expiry input: a prerequisite's completion is read from its own
    record and its current revision, nothing else (R-21)."""
    view = {}
    states = document.get("mission_state", {})
    for mission_id, mission in document.get("missions", {}).items():
        state = states.get(mission_id)
        closure_activation = None
        if state is not None and state.get("closure") is not None:
            closure_activation = state_module.activation_by_id(
                state, state["closure"]["activation_id"])
        view[mission_id] = {
            "current_revision": mission["current_revision"],
            "revision_digests": dict(
                (entry["revision"], entry["proposal_digest_sha256"])
                for entry in mission["revisions"]
            ),
            "progress": None if state is None else state.get("progress"),
            "closure_activation_id": (
                None if closure_activation is None
                else closure_activation["activation_id"]),
            "closure_revision": (
                None if closure_activation is None
                else closure_activation["revision"]),
            "closure_proposal_digest_sha256": (
                None if closure_activation is None
                else closure_activation["proposal_digest_sha256"]),
        }
    return view


def _authority_window(authorization, recorded_at, what, where, path):
    """R-25.1: ``recorded_at`` lies inside the window the authorization
    itself records. Recorded numbers only; never the wall clock."""
    expires = authorization["expires_at"]
    revocation = authorization["revocation"]
    # Asymmetry, deliberate (R-26): expiry is STRICT (``T < expires_at``)
    # because Task 4's live check refuses at ``now >= expires_at``, so no
    # legitimate record can carry ``T == expires_at``; revocation is
    # NON-STRICT (``T <= revoked_at``) because ``revoked_at`` is the EDIT
    # decision's application second and a live-validated operation in
    # that same second is legitimate — strict comparison would make a
    # genuine authority document permanently unreadable.
    if recorded_at < authorization["issued_at"] or (
        expires is not None and recorded_at >= expires
    ) or (revocation["revoked"] and recorded_at > revocation["revoked_at"]):
        _unreadable(path, "%s (%s): %s at %d lies outside authorization %s's"
                    " recorded window (issued %d, expires %r, revoked_at %r)"
                    % (where, state_module.PROBLEM_AUTHORITY_WINDOW, what,
                       recorded_at, authorization["authorization_id"],
                       authorization["issued_at"], expires,
                       revocation["revoked_at"]))


def _validate_mission_state(document, mission_id, state, path):
    where = "mission_state[%r]" % mission_id
    missions = document["missions"]
    state_validation.validate_state_record(state, where)
    if state["mission_id"] != mission_id:
        _unreadable(path, "%s carries mission_id %r" % (where, state["mission_id"]))
    mission = missions.get(mission_id)
    if mission is None:
        _unreadable(path, "%s names a mission the registry does not hold" % where)
    contracts = {}
    for index, activation in enumerate(state["contract_activations"]):
        sub = "%s.contract_activations[%d]" % (where, index)
        contracts[activation["activation_id"]] = activation_contract(
            document, mission, activation, sub)
        authorization = document["authorizations"].get(activation["authorization_id"])
        if authorization is None or (
            activation["authorization_id"] not in mission["authorization_ids"]
            or authorization["revision"] != activation["revision"]
            or authorization["authorization_digest_sha256"] != (
                activation["authorization_digest_sha256"])
        ):
            _unreadable(path, "%s binds authorization %s, which is not a stored"
                        " authorization of mission %s at revision %d with that"
                        " digest" % (sub, activation["authorization_id"],
                                     mission_id, activation["revision"]))
        _authority_window(authorization, activation["activated_at"],
                          "activation", sub, path)
    # Task 7, Stage 2: an attested receipt-reference artifact names a
    # stored Mission Authorization of THIS mission carrying exactly the
    # digest the marker holds, bound to the revision the attesting
    # operation cites, permitting the one delivery target, and recorded
    # inside that authorization's recorded window (R-25.1) — the same
    # cross-document discipline an activation binding must satisfy. The
    # marker is a binding value, not a credential: a digest that resolves
    # to nothing, to another Mission, to another revision or to a
    # non-delivery authorization makes the record unreadable.
    for index, artifact in enumerate(state["artifacts"]):
        attestation = state_module.receipt_attestation_of(artifact)
        if attestation is None:
            continue
        sub = "%s.artifacts[%d].%s" % (
            where, index, state_module.ARTIFACT_MARKER_RECEIPT_ATTESTATION)
        authorization = document["authorizations"].get(attestation["authorization_id"])
        if authorization is None or (
            attestation["authorization_id"] not in mission["authorization_ids"]
            or authorization["authorization_digest_sha256"] != (
                attestation["authorization_digest_sha256"])
            or authorization["revision"] != artifact["provenance"]["revision"]
            or record.DELIVERY_TARGET_GITHUB_PR not in (
                authorization["authorized_delivery_targets"])
        ):
            _unreadable(path, "%s (%s): names authorization %s, which is not a"
                        " stored authorization of mission %s at revision %d with"
                        " that digest permitting the delivery target"
                        % (sub, state_module.PROBLEM_RECEIPT_ATTESTATION,
                           attestation["authorization_id"], mission_id,
                           artifact["provenance"]["revision"]))
        _authority_window(authorization, artifact["recorded_at"],
                          "receipt attestation", sub, path)

    def requirement_of(entry, sub):
        contract = contracts[entry["activation_id"]]
        for requirement in contract["requirements"]:
            if requirement["key"] == entry["requirement_key"]:
                return requirement
        _unreadable(path, "%s names requirement %r, which the approved contract"
                    " does not declare" % (sub, entry["requirement_key"]))

    for index, claim in enumerate(state["claims"]):
        requirement_of(claim, "%s.claims[%d]" % (where, index))
    for index, evidence in enumerate(state["evidence"]):
        sub = "%s.evidence[%d]" % (where, index)
        requirement = requirement_of(evidence, sub)
        if evidence["acceptance"] is not None and (
            evidence["kind"] not in requirement["evidence_kinds"]
        ):
            _unreadable(path, "%s is accepted %s evidence but requirement %r"
                        " does not declare that kind"
                        % (sub, evidence["kind"], requirement["key"]))
    for index, blocker in enumerate(state["blockers"]):
        sub = "%s.blockers[%d]" % (where, index)
        policy = contracts[blocker["activation_id"]]["degradation_policy"]
        derived = (state_module.BLOCKER_SEVERITY_DEGRADED
                   if blocker["key"] in policy["permitted_blocker_keys"]
                   else state_module.BLOCKER_SEVERITY_HARD)
        if blocker["severity"] != derived:
            _unreadable(path, "%s records severity %s but the approved"
                        " degradation policy derives %s for key %r"
                        % (sub, blocker["severity"], derived, blocker["key"]))
    for index, dependency in enumerate(state["dependencies"]):
        sub = "%s.dependencies[%d]" % (where, index)
        if dependency["kind"] == record.DEPENDENCY_KIND_MISSION and (
            dependency["reference"] not in missions
        ):
            _unreadable(path, "%s references unknown mission %s"
                        % (sub, dependency["reference"]))
        if dependency["key"] is None:
            continue
        contract = contracts[dependency["activation_id"]]
        slot = None
        for candidate in contract["required_dependencies"]:
            if candidate["key"] == dependency["key"]:
                slot = candidate
        if slot is None:
            _unreadable(path, "%s binds slot %r, which the approved contract does"
                        " not declare" % (sub, dependency["key"]))
        if slot["kind"] != dependency["kind"]:
            _unreadable(path, "%s binds slot %r as %s; the approved slot is %s"
                        % (sub, dependency["key"], dependency["kind"], slot["kind"]))
        target = slot["target"]
        if target["form"] != record.TARGET_FORM_ELIGIBILITY and (
            not progress_module.target_matches(target, dependency["reference"],
                                               registry_view(document))
        ):
            _unreadable(path, "%s binds slot %r to %r, which does not match the"
                        " approved target" % (sub, dependency["key"],
                                              dependency["reference"]))
    closure = state["closure"]
    if closure is not None:
        sub = where + ".closure"
        asserting = closure["reason"] in (
            state_module.CLOSURE_REASON_BUDGET_EXHAUSTED,
            state_module.CLOSURE_REASON_HARD_BLOCKER,
        )
        if closure["activation_id"] is None:
            if asserting:
                _unreadable(path, "%s (%s): reason %r asserts a contract fact but"
                            " no contract was ever activated"
                            % (sub, progress_module.PROBLEM_CLOSURE_NOT_PROVABLE,
                               closure["reason"]))
        else:
            activation = state_module.activation_by_id(state, closure["activation_id"])
            if closure["progress"] == state_module.PROGRESS_COMPLETED:
                _authority_window(
                    document["authorizations"][activation["authorization_id"]],
                    closure["closed_at"], "completion", sub, path)
            detail = progress_module.closure_proof_problem(
                contracts[closure["activation_id"]], state, closure)
            if detail is not None:
                _unreadable(path, "%s (%s): %s"
                            % (sub, progress_module.PROBLEM_CLOSURE_NOT_PROVABLE,
                               detail))
    # R-43: revisions are append-only and applied_at is non-decreasing in
    # sequence order, so the revision an operation cites can never move
    # BACKWARD across the sequence. The floor is built from this record's
    # own earlier operations and activations (an activation is explicit
    # proof the Mission stood at its revision), never from the present
    # revision, so later EDITs keep a valid record readable, and equal
    # citations in one second stay legitimate in both directions.
    floor = 1
    activation_revision_at = dict(
        (a["operation_id"], a["revision"]) for a in state["contract_activations"])
    for index, operation in enumerate(state["applied_operations"]):
        sub = "%s.applied_operations[%d]" % (where, index)
        cited_here = operation["provenance"]["revision"]
        if cited_here < floor:
            _unreadable(path, "%s (%s): cites revision %d after an earlier operation"
                        " already stood at revision %d; a citation never moves"
                        " backward" % (sub, state_module.PROBLEM_REVISION_REGRESSED,
                                       cited_here, floor))
        floor = max(floor, cited_here,
                    activation_revision_at.get(operation["operation_id"], 1))
        # R-40 / R-40a: the cited provenance revision is the one the
        # service WOULD have recorded when the operation was applied,
        # derived from the Mission's own append-only revision history
        # (each entry's created_at is the applying EDIT's decided_at,
        # which reconcile_mission_history already enforces):
        #   1 <= c <= len(revisions);
        #   revisions[c-1].created_at <= applied_at  (it existed by then);
        #   c == len(revisions) or revisions[c].created_at >= applied_at
        #     (no LATER revision had been created strictly before this
        #     operation).
        # The >= in the third clause admits BOTH orderings of an EDIT and
        # an operation applied in the same second (R-26); only the
        # immediately next revision is consulted, so later EDITs keep a
        # valid record readable; and nothing here compares against a
        # stale activation, so the R-36 exemption stands.
        cited = operation["provenance"]["revision"]
        applied_at = operation["applied_at"]
        revisions = mission["revisions"]
        entry = manifest.revision_entry(mission, cited)
        impossible = entry is None or entry["created_at"] > applied_at or (
            cited < len(revisions) and revisions[cited]["created_at"] < applied_at)
        if impossible:
            _unreadable(path, "%s (%s): cites revision %r, which is not the revision"
                        " mission %s was at when the operation was applied at %d"
                        % (sub, state_module.PROBLEM_REVISION_IMPOSSIBLE, cited,
                           mission_id, applied_at))
        bound = None
        for activation in state["contract_activations"]:
            if activation["sequence"] < operation["sequence"]:
                bound = activation
        if operation["kind"] == state_module.OPERATION_RECORD_ARTIFACT:
            # R-39: the role the contract bound THEN declared for a required
            # key is the only role the service would have recorded.
            artifact = [a for a in state["artifacts"]
                        if a["operation_id"] == operation["operation_id"]][0]
            for declared in contracts[bound["activation_id"]]["required_artifacts"]:
                if declared["key"] == artifact["key"] and (
                    declared["role"] != artifact["role"]
                ):
                    _unreadable(path, "%s (%s): records required artifact %r with"
                                " role %s but the contract bound then declares %s"
                                % (sub, state_module.PROBLEM_HISTORY_IMPOSSIBLE,
                                   artifact["key"], artifact["role"],
                                   declared["role"]))
        if operation["kind"] != state_module.OPERATION_RECORD_CONTINUATION:
            continue
        declared = contracts[bound["activation_id"]]["continuation_budget"][
            "max_attempts"]
        outcome = operation["outcome"]
        # R-31.6: a DERIVED field is reconciled against the contract bound
        # at that operation (never the current one) and the ledger count.
        if outcome["attempts_remaining"] != declared - outcome["attempt"]:
            _unreadable(path, "%s (%s): outcome.attempts_remaining %r but the bound"
                        " contract permits %d and attempt %d was consumed"
                        % (sub, state_module.PROBLEM_EFFECT_INCONSISTENT,
                           outcome["attempts_remaining"], declared,
                           outcome["attempt"]))
    for index, checkpoint in enumerate(state["checkpoints"]):
        sub = "%s.checkpoints[%d]" % (where, index)
        detail = progress_module.checkpoint_disagreement(
            contracts[checkpoint["activation_id"]], state, checkpoint)
        if detail is not None:
            _unreadable(path, "%s does not recompute to itself (%s): %s"
                        % (sub, progress_module.PROBLEM_CHECKPOINT_DISAGREES, detail))
    for index, operation in enumerate(state["applied_operations"]):
        sub = "%s.applied_operations[%d]" % (where, index)
        reservation = document["reservations"].get(operation["operation_id"])
        if reservation is None or reservation["kind"] != (
            RESERVATION_KIND_STATE_OPERATION
        ) or reservation["consumed_by"] != operation["operation_id"]:
            _unreadable(path, "%s operation %s is not a consumed state_operation"
                        " reservation" % (sub, operation["operation_id"]))
        context = dict((key, operation["provenance"][key])
                       for key in record.CONTEXT_KEYS)
        if reservation["context"] != context:
            _unreadable(path, "%s operation %s was reserved by a different"
                        " authenticated context than its provenance records"
                        % (sub, operation["operation_id"]))
    # Task 7, LAST on purpose: a stored journal snapshot is a projection
    # cache bound to the history proved above. Its bindings (schema
    # version, Mission, held position, revision in force there, chain
    # digest there) and its recomputation under the contract bound THEN
    # are derived checks, so a tampered history reports its own problem
    # first and a stale-but-consistent snapshot never makes a valid
    # record unreadable.
    snapshot = state.get("snapshot")
    if snapshot is not None:
        sub = where + ".snapshot"
        journal.require_snapshot_bindings(snapshot, state, sub)
        bound = journal.activation_at(state, snapshot["position"])
        detail = journal.snapshot_disagreement(
            snapshot, state,
            None if bound is None else contracts[bound["activation_id"]])
        if detail is not None:
            _unreadable(path, "%s does not recompute to itself (%s): %s"
                        % (sub, journal.PROBLEM_SNAPSHOT_DISAGREES, detail))
    # Task 7, Stage 2, equally last: every reconciliation record binds
    # the chain at the position it observed and its findings recompute
    # from the record as of that position under the contract bound
    # THEN, the sources it stored and the record before it.
    for index, entry in enumerate(state.get("reconciliations", [])):
        sub = "%s.reconciliations[%d]" % (where, index)
        reconciliation.require_bindings(entry, state, sub)
        bound = journal.activation_at(state, entry["observed_position"])
        detail = reconciliation.disagreement(
            entry, state, None if bound is None else contracts[bound["activation_id"]])
        if detail is not None:
            _unreadable(path, "%s does not recompute to itself (%s): %s"
                        % (sub, reconciliation.PROBLEM_RECONCILIATION_DISAGREES,
                           detail))


def _refuse_open_permissions(path):
    mode = os.stat(path).st_mode
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        _unreadable(path, "is accessible by group/other (mode %o); it carries"
                    " authority records. Fix with: chmod 600 %r"
                    % (stat.S_IMODE(mode), path))


def _refuse_open_directory(directory):
    """The protected-directory boundary: an EXISTING store directory that
    group/other can reach is refused before any read, lock, or write. A
    writable containing directory would let another local account replace
    the authority document or the lock regardless of the file's own mode.
    A missing directory is fine: it is created mode 700 on first write."""
    try:
        mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        _unreadable(directory, "is not a directory")
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        raise MissionStoreError(
            "mission store directory %s is accessible by group/other (mode"
            " %o); it holds the authority document and its lock, so nothing"
            " is read, locked, or written. Fix with: chmod 700 %r"
            % (directory, stat.S_IMODE(mode), directory),
            PROBLEM_STORE_UNREADABLE,
        )


class MissionStore(object):
    """Atomic load/save of the one Mission store document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, MISSIONS_FILE_NAME)

    def lock(self):
        """The cross-process lock every load-modify-save cycle holds. The
        directory boundary is checked before the lock file is touched."""
        _refuse_open_directory(self.directory)
        return exclusive_store_lock(self.directory, MISSIONS_LOCK_FILE_NAME)

    def load(self):
        _refuse_open_directory(self.directory)
        if not os.path.exists(self.path):
            return default_document()
        _refuse_open_permissions(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            _unreadable(self.path, "could not be read as JSON (%s)" % exc)
        # The ONE compatibility rule (R-3): a document written before the
        # Mission State key existed loads with an empty map. Nothing else
        # is supplied, and ``save`` supplies nothing.
        if isinstance(document, dict) and OPTIONAL_TOP_LEVEL_KEY not in document:
            document[OPTIONAL_TOP_LEVEL_KEY] = {}
        return validate_document(document, self.path)

    def save(self, document):
        _refuse_open_directory(self.directory)
        validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".missions-")
