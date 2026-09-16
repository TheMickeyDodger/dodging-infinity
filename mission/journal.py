"""The Mission Event Journal and its position-bound snapshots (Task 7,
Stage 1): pure functions over one stored Mission State record.

The journal IS the Task 5 applied-operation ledger, read as ordered
events. Nothing is copied into a second list: every journaled event is
one ``applied_operations`` entry, its stable identity is that entry's
DI-minted, durably reserved ``operation_id``, its total order is the
entry's ``sequence`` (the journal position: exact, contiguous, enforced
by ``mission.state_validation``), and it is bound to the Mission and to
the revision in force when it was recorded through the entry's own
provenance block. Because the ledger is appended only inside the one
atomic load-modify-save cycle the persistence layer already performs,
journal events commit in the same ``os.replace`` as the effect they
describe and can never split from it.

Chain. Every position carries a derived digest: the canonical digest of
the previous position's digest together with the whole ledger entry at
that position, from a per-Mission genesis digest at position 0. The
digest at a position therefore binds everything at or before it, so a
cursor or a snapshot that names a position AND its digest names one
history, not merely a count. The chain is re-derived on every read and
is never stored per event (the ledger entry's closed key set is
untouched); the only stored copy is the snapshot's own binding, and the
persistence layer refuses a stored binding that no longer re-derives.

Cursor. A cursor is plain data: ``mission_id``, ``schema_version``,
``revision``, ``position``, ``event_id`` (the event at that position,
None at the origin) and ``journal_digest_sha256``. The head cursor
carries the Mission's CURRENT revision, so a cursor taken before an
EDIT differs from the head afterwards even when no event was appended;
a historical cursor carries the revision in force when its event was
recorded. ``require_cursor_in_journal`` refuses a position the journal
does not hold (``mission_journal_position``) and a cursor from another
Mission or another history (``mission_journal_cursor_mismatch``).

Snapshot. Stage 1's one stored addition is the additive-optional
``snapshot`` key of the state record. A snapshot is a derived projection
cache and never a second source of truth: it records the schema version,
the revision in force, the journal position and the chain digest it was
taken at, and the supported state at that position, every field of
which is re-derivable from the record alone (``supported_state``:
progress and closure reason as of the position, the activation current
then and its contract bindings, proof, readiness, dependency slots and
budget evaluated over ``progress.state_as_of`` with the event's own
recorded time as the clock, and the active blocker, outstanding
dependency and accepted evidence ids). The record validator checks the
closed, typed shape; the persistence layer, which holds the contract,
runs the derived checks LAST, after every primary check of the history:
the ledger bindings (``mission_journal_snapshot_binding``) and the
recomputation of the projection, refusing one that does not recompute
to itself (``mission_journal_snapshot_disagrees``). A tampered history
therefore reports its own problem, never the snapshot's. A snapshot is
written by the operations layer at the new head inside the
same atomic save as the ledger entry; a snapshot whose bindings are not
the head (an EDIT moved the revision, a later event moved the position)
is consistent history and still loads, but ``reload`` refuses to USE it
(``mission_journal_snapshot_stale``) and replays instead, so reload
from record plus journal yields the same supported state and the same
cursor with or without a snapshot.

Effect-free by construction. This module imports only ``copy``, the
pure Task 5 modules and the shared digest helper; it holds no lock,
opens nothing, writes nothing, mints nothing, and neither reads nor
constructs an authorization, a decision, a reservation or a human
identity. Replay reconstructs supported state and a cursor from stored
data and does nothing else: it never starts, hands off, messages,
approves or performs any repository, PR or delivery action.
"""

import copy

from workflow_authority.digest import json_digest

from mission import progress as progress_module
from mission import record
from mission import state as state_module

# -- closed key sets -----------------------------------------------------

EVENT_KEYS = (
    "event_id", "position", "kind", "mission_id", "revision", "recorded_at",
    "content_digest_sha256", "journal_digest_sha256",
)
CURSOR_KEYS = (
    "mission_id", "schema_version", "revision", "position", "event_id",
    "journal_digest_sha256",
)
SNAPSHOT_KEYS = (
    "schema_version", "mission_id", "revision", "position",
    "journal_digest_sha256", "supported_state",
)
SUPPORTED_STATE_KEYS = (
    "progress", "closure_reason", "activation_id", "contract", "proof",
    "readiness", "dependencies", "budget", "active_blocker_ids",
    "outstanding_dependency_ids", "accepted_evidence_ids",
)
SNAPSHOT_CONTRACT_KEYS = state_module.CHECKPOINT_REFS_KEYS
PROOF_KEYS = ("satisfied", "requirements")
READINESS_KEYS = ("satisfied", "resources")
DEPENDENCIES_KEYS = ("satisfied", "slots")

# -- hard bounds, never derived from input ------------------------------

# The journal is the ledger: one cap, shared, never pruned.
MAX_JOURNAL_EVENTS = state_module.MAX_APPLIED_OPERATIONS
# A page of events a reader may ask for at once.
MAX_JOURNAL_PAGE_EVENTS = 256

SOURCE_SNAPSHOT = "snapshot"
SOURCE_REPLAY = "replay"

# -- problem codes: one distinct code per refusal -----------------------

PROBLEM_JOURNAL_POSITION = "mission_journal_position"
PROBLEM_CURSOR_MISMATCH = "mission_journal_cursor_mismatch"
PROBLEM_SNAPSHOT_BINDING = "mission_journal_snapshot_binding"
PROBLEM_SNAPSHOT_DISAGREES = "mission_journal_snapshot_disagrees"
PROBLEM_SNAPSHOT_STALE = "mission_journal_snapshot_stale"
PROBLEM_JOURNAL_PAGE_BOUND = "mission_journal_page_bound"

_CLOSURE_REASONS = tuple(sorted(set(
    reason for reasons in state_module.CLOSURE_REASONS_BY_PROGRESS.values()
    for reason in reasons)))


# -- the chain ----------------------------------------------------------


def genesis_digest(mission_id):
    """The digest at position 0 of one Mission's journal."""
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    return json_digest({"journal_genesis": mission_id,
                        "schema_version": state_module.STATE_SCHEMA_VERSION})


def _chain(state):
    """The digest at every position 0..sequence, in order."""
    digests = [genesis_digest(state["mission_id"])]
    for entry in state["applied_operations"]:
        digests.append(json_digest({"previous": digests[-1], "event": entry}))
    return digests


def require_position(value, location, sequence):
    """A journal position: an int (bool refused) within 0..sequence."""
    record.require_int(value, location)
    if value < 0 or value > sequence:
        record.fail(PROBLEM_JOURNAL_POSITION,
                    "%s is %d but the journal holds positions 0..%d"
                    % (location, value, sequence))
    return value


def journal_digest_at(state, position):
    require_position(position, "position", state["sequence"])
    return _chain(state)[position]


def events(state):
    """The ledger as ordered events, position 1..sequence."""
    chain = _chain(state)
    result = []
    for entry in state["applied_operations"]:
        result.append({
            "event_id": entry["operation_id"],
            "position": entry["sequence"],
            "kind": entry["kind"],
            "mission_id": entry["provenance"]["mission_id"],
            "revision": entry["provenance"]["revision"],
            "recorded_at": entry["applied_at"],
            "content_digest_sha256": entry["content_digest_sha256"],
            "journal_digest_sha256": chain[entry["sequence"]],
        })
    return result


def page_events(state, after_position, limit):
    """Up to ``limit`` events after ``after_position``, and the position
    to continue from (None when nothing follows)."""
    record.require_int(limit, "limit", minimum=1)
    if limit > MAX_JOURNAL_PAGE_EVENTS:
        record.fail(PROBLEM_JOURNAL_PAGE_BOUND,
                    "a page of %d events exceeds the bound of %d"
                    % (limit, MAX_JOURNAL_PAGE_EVENTS))
    require_position(after_position, "after_position", state["sequence"])
    page = [e for e in events(state) if e["position"] > after_position][:limit]
    next_after = None
    if page and page[-1]["position"] < state["sequence"]:
        next_after = page[-1]["position"]
    return page, next_after


# -- cursors -------------------------------------------------------------


def _cursor(state, position, revision, chain):
    entry = None if position == 0 else state["applied_operations"][position - 1]
    return {
        "mission_id": state["mission_id"],
        "schema_version": state_module.STATE_SCHEMA_VERSION,
        "revision": revision,
        "position": position,
        "event_id": None if entry is None else entry["operation_id"],
        "journal_digest_sha256": chain[position],
    }


def cursor_at(state, position):
    """A historical cursor: the revision in force when its event was
    recorded (None at the origin)."""
    require_position(position, "position", state["sequence"])
    revision = None
    if position > 0:
        revision = state["applied_operations"][position - 1]["provenance"]["revision"]
    return _cursor(state, position, revision, _chain(state))


def head_cursor(mission, state):
    """The cursor at the head, carrying the Mission's CURRENT revision.
    ``state`` may be None (no event yet): the origin."""
    if state is None:
        state = state_module.new_state_record(mission["mission_id"], 0)
    return _cursor(state, state["sequence"], mission["current_revision"],
                   _chain(state))


def validate_cursor(value, location="cursor"):
    record.require_dict(value, location)
    record.require_closed_keys(value, CURSOR_KEYS, location)
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["schema_version"], location + ".schema_version")
    if value["schema_version"] != state_module.STATE_SCHEMA_VERSION:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.schema_version must be %d"
                    % (location, state_module.STATE_SCHEMA_VERSION))
    if value["revision"] is not None:
        record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_int(value["position"], location + ".position")
    if value["position"] < 0:
        record.fail(PROBLEM_JOURNAL_POSITION,
                    "%s.position must be >= 0" % location)
    if value["event_id"] is not None:
        record.require_id(value["event_id"], record.STATE_OPERATION_ID_PREFIX,
                          location + ".event_id")
    record.require_hex(value["journal_digest_sha256"],
                       location + ".journal_digest_sha256", 64)
    return value


def require_cursor_in_journal(state, cursor, location="cursor"):
    """``cursor`` names a position this journal holds, on THIS Mission's
    history: same Mission, same event identity, same chain digest."""
    validate_cursor(cursor, location)
    if cursor["mission_id"] != state["mission_id"]:
        record.fail(PROBLEM_CURSOR_MISMATCH,
                    "%s names mission %s, not %s"
                    % (location, cursor["mission_id"], state["mission_id"]))
    require_position(cursor["position"], location + ".position", state["sequence"])
    expected = cursor_at(state, cursor["position"])
    for key in ("event_id", "journal_digest_sha256"):
        if cursor[key] != expected[key]:
            record.fail(PROBLEM_CURSOR_MISMATCH,
                        "%s.%s does not name this journal's history at position"
                        " %d" % (location, key, cursor["position"]))
    return None


# -- replay ---------------------------------------------------------------


def activation_at(state, position):
    """The activation current at ``position`` (None before any)."""
    current = None
    for activation in state["contract_activations"]:
        if activation["sequence"] <= position:
            current = activation
    return current


def supported_state(state, contract, position):
    """The supported derived state as of ``position``, from stored data
    alone: ``contract`` is the proof contract of the activation current
    at that position (None when there is none), supplied by the caller
    that holds the Mission record. Pure and deterministic: the clock is
    the recorded time of the event at the position."""
    require_position(position, "position", state["sequence"])
    as_of = progress_module.state_as_of(state, position)
    activation = state_module.latest_activation(as_of)
    projection = {
        "progress": as_of["progress"],
        "closure_reason": (None if as_of["closure"] is None
                           else as_of["closure"]["reason"]),
        "activation_id": None,
        "contract": None,
        "proof": None,
        "readiness": None,
        "dependencies": None,
        "budget": None,
        "active_blocker_ids": sorted(
            b["blocker_id"] for b in state_module.active_blockers(as_of)),
        "outstanding_dependency_ids": sorted(
            d["dependency_id"] for d in as_of["dependencies"]
            if d["resolution"] is None),
        "accepted_evidence_ids": sorted(
            e["evidence_id"] for e in as_of["evidence"]
            if state_module.is_accepted(e)),
    }
    if activation is None:
        return projection
    if contract is None:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "position %d is under activation %s but no contract was"
                    " supplied for it" % (position, activation["activation_id"]))
    clock = as_of["applied_operations"][-1]["applied_at"]
    activation_id = activation["activation_id"]
    projection.update({
        "activation_id": activation_id,
        "contract": {
            "revision": activation["revision"],
            "proposal_digest_sha256": activation["proposal_digest_sha256"],
            "contract_digest_sha256": activation["contract_digest_sha256"],
        },
        "proof": progress_module.evaluate_proof(contract, as_of, activation_id, clock),
        "readiness": progress_module.readiness(contract, as_of, clock),
        "dependencies": progress_module.dependency_status(contract, as_of,
                                                          activation_id),
        "budget": progress_module.budget(contract, as_of),
    })
    return projection


# -- snapshots ------------------------------------------------------------


def new_snapshot(state, contract, position=None):
    """A snapshot at ``position`` (the head by default), bound three ways
    plus the chain digest, holding the supported state re-derived there.
    The revision bound is the one in force when the head event was
    recorded, which at the head is the Mission's current revision."""
    if position is None:
        position = state["sequence"]
    cursor = cursor_at(state, position)
    if cursor["revision"] is None:
        record.fail(PROBLEM_JOURNAL_POSITION,
                    "a snapshot needs at least one event; position 0 has none")
    return {
        "schema_version": state_module.STATE_SCHEMA_VERSION,
        "mission_id": state["mission_id"],
        "revision": cursor["revision"],
        "position": position,
        "journal_digest_sha256": cursor["journal_digest_sha256"],
        "supported_state": supported_state(state, contract, position),
    }


def _typed_map(value, location, max_items, members):
    record.require_dict(value, location)
    if len(value) > max_items:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s holds %d entries; the hard bound is %d"
                    % (location, len(value), max_items))
    for key, status in value.items():
        record.require_contract_key(key, location + " key")
        record.require_member(status, members, "%s[%r]" % (location, key))


def _typed_id_list(value, location, prefix, max_items):
    if not isinstance(value, list):
        record.fail(record.PROBLEM_BAD_TYPE, "%s must be a list" % location)
    if len(value) > max_items:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s holds %d entries; the hard bound is %d"
                    % (location, len(value), max_items))
    for index, item in enumerate(value):
        record.require_id(item, prefix, "%s[%d]" % (location, index))
    if value != sorted(set(value)):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must be sorted and duplicate-free" % location)


def _validate_evaluation(value, location, keys, member_key, max_items, members):
    if value is None:
        return
    record.require_dict(value, location)
    record.require_closed_keys(value, keys, location)
    record.require_bool(value["satisfied"], location + ".satisfied")
    _typed_map(value[member_key], "%s.%s" % (location, member_key), max_items,
               members)


def validate_supported_state(value, location):
    """Closed, typed shape of a supported-state projection; every value
    passes the repository's own validators BEFORE any comparison."""
    record.require_dict(value, location)
    record.require_closed_keys(value, SUPPORTED_STATE_KEYS, location)
    state_module.require_progress(value["progress"], location + ".progress")
    if value["closure_reason"] is not None:
        record.require_member(value["closure_reason"], _CLOSURE_REASONS,
                              location + ".closure_reason")
    if value["activation_id"] is not None:
        record.require_id(value["activation_id"], record.PROOF_CONTRACT_ID_PREFIX,
                          location + ".activation_id")
    contract = value["contract"]
    if contract is not None:
        sub = location + ".contract"
        record.require_dict(contract, sub)
        record.require_closed_keys(contract, SNAPSHOT_CONTRACT_KEYS, sub)
        record.require_int(contract["revision"], sub + ".revision", minimum=1)
        record.require_hex(contract["proposal_digest_sha256"],
                           sub + ".proposal_digest_sha256", 64)
        record.require_hex(contract["contract_digest_sha256"],
                           sub + ".contract_digest_sha256", 64)
    _validate_evaluation(value["proof"], location + ".proof", PROOF_KEYS,
                         "requirements", record.MAX_PROOF_REQUIREMENTS,
                         progress_module.REQUIREMENT_STATUSES)
    _validate_evaluation(value["readiness"], location + ".readiness", READINESS_KEYS,
                         "resources", record.MAX_REQUIRED_RESOURCE_READINESS,
                         state_module.READINESS_STATUSES)
    _validate_evaluation(value["dependencies"], location + ".dependencies",
                         DEPENDENCIES_KEYS, "slots", record.MAX_REQUIRED_DEPENDENCIES,
                         progress_module.SLOT_STATUSES)
    budget = value["budget"]
    if budget is not None:
        sub = location + ".budget"
        record.require_dict(budget, sub)
        record.require_closed_keys(budget, state_module.CHECKPOINT_BUDGET_KEYS, sub)
        for key in state_module.CHECKPOINT_BUDGET_KEYS:
            record.require_int(budget[key], "%s.%s" % (sub, key), minimum=0)
    _typed_id_list(value["active_blocker_ids"], location + ".active_blocker_ids",
                   record.BLOCKER_ID_PREFIX, state_module.MAX_BLOCKER_RECORDS)
    _typed_id_list(value["outstanding_dependency_ids"],
                   location + ".outstanding_dependency_ids",
                   record.DEPENDENCY_ID_PREFIX, state_module.MAX_DEPENDENCY_RECORDS)
    _typed_id_list(value["accepted_evidence_ids"], location + ".accepted_evidence_ids",
                   record.EVIDENCE_ID_PREFIX, state_module.MAX_EVIDENCE_RECORDS)
    return value


def validate_snapshot_shape(value, location):
    """The closed, typed shape of a stored snapshot, self-contained: what
    the record validator checks. Its bindings to the ledger and its
    recomputation are derived checks and come LAST, in the persistence
    layer (``require_snapshot_bindings``, ``snapshot_disagreement``), so a
    tampered history reports the history's own problem first."""
    record.require_dict(value, location)
    record.require_closed_keys(value, SNAPSHOT_KEYS, location)
    record.require_int(value["schema_version"], location + ".schema_version")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_int(value["position"], location + ".position")
    record.require_hex(value["journal_digest_sha256"],
                       location + ".journal_digest_sha256", 64)
    validate_supported_state(value["supported_state"], location + ".supported_state")
    return value


def require_snapshot_bindings(value, state, location):
    """The ledger bindings of a stored (shape-valid) snapshot: schema
    version, Mission, a position the journal holds (at least 1), the
    revision in force at that position and the chain digest there.
    Contract-free; the projection's recomputation is
    ``snapshot_disagreement``."""
    if value["schema_version"] != state_module.STATE_SCHEMA_VERSION:
        record.fail(PROBLEM_SNAPSHOT_BINDING,
                    "%s binds schema version %d; this layer writes %d"
                    % (location, value["schema_version"],
                       state_module.STATE_SCHEMA_VERSION))
    if value["mission_id"] != state["mission_id"]:
        record.fail(PROBLEM_SNAPSHOT_BINDING,
                    "%s binds mission %s, not %s"
                    % (location, value["mission_id"], state["mission_id"]))
    if value["position"] < 1 or value["position"] > state["sequence"]:
        record.fail(PROBLEM_JOURNAL_POSITION,
                    "%s binds position %d but the journal holds positions 1..%d"
                    % (location, value["position"], state["sequence"]))
    expected = cursor_at(state, value["position"])
    if value["revision"] != expected["revision"]:
        record.fail(PROBLEM_SNAPSHOT_BINDING,
                    "%s binds revision %d but the event at position %d was"
                    " recorded under revision %d"
                    % (location, value["revision"], value["position"],
                       expected["revision"]))
    if value["journal_digest_sha256"] != expected["journal_digest_sha256"]:
        record.fail(PROBLEM_SNAPSHOT_BINDING,
                    "%s.journal_digest_sha256 does not re-derive from the journal"
                    " at position %d; the history it was taken over is not this"
                    " one" % (location, value["position"]))
    return value


def snapshot_disagreement(snapshot, state, contract):
    """Recompute the snapshot's supported state at its own position and
    compare field by field; the first disagreement's detail, or None."""
    derived = supported_state(state, contract, snapshot["position"])
    stored = snapshot["supported_state"]
    for key in SUPPORTED_STATE_KEYS:
        if stored[key] != derived[key]:
            return ("snapshot field %s is %r; recomputation at position %d gives %r"
                    % (key, stored[key], snapshot["position"], derived[key]))
    return None


def snapshot_is_current(snapshot, cursor):
    """The snapshot's bindings ARE the head cursor's."""
    return (snapshot["mission_id"] == cursor["mission_id"]
            and snapshot["schema_version"] == cursor["schema_version"]
            and snapshot["revision"] == cursor["revision"]
            and snapshot["position"] == cursor["position"]
            and snapshot["journal_digest_sha256"] == cursor["journal_digest_sha256"])


def snapshot_view(mission, state):
    """The stored snapshot's bindings and whether they are the head, for
    a reader; None without a snapshot."""
    snapshot = None if state is None else state.get("snapshot")
    if snapshot is None:
        return None
    return {
        "schema_version": snapshot["schema_version"],
        "revision": snapshot["revision"],
        "position": snapshot["position"],
        "journal_digest_sha256": snapshot["journal_digest_sha256"],
        "current": snapshot_is_current(snapshot, head_cursor(mission, state)),
    }


def reload(mission, state, contract):
    """Supported state and cursor at the head from stored data: the
    snapshot when its bindings are the head, else a replay over record
    plus journal. Equivalent either way; a stale snapshot is refused for
    use, never served and never repaired here."""
    if state is None:
        state = state_module.new_state_record(mission["mission_id"], 0)
    cursor = head_cursor(mission, state)
    snapshot = state.get("snapshot")
    source = SOURCE_REPLAY
    problem = None
    if snapshot is not None:
        if snapshot_is_current(snapshot, cursor):
            source = SOURCE_SNAPSHOT
        else:
            problem = PROBLEM_SNAPSHOT_STALE
    if source == SOURCE_SNAPSHOT:
        supported = copy.deepcopy(snapshot["supported_state"])
    else:
        supported = supported_state(state, contract, cursor["position"])
    return {
        "mission_id": mission["mission_id"],
        "cursor": cursor,
        "source": source,
        "snapshot_problem": problem,
        "supported_state": supported,
    }
