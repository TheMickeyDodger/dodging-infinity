"""The canonical Mission record (manifest): identity, exact revisions,
lifecycle state, decision history, and authorization references.

One record per Mission, keyed by its DI-owned ``mission_id``. The
``revisions`` list is append-only and complete: entry N carries the
exact validated proposal of revision N, its content digest, its
creation (application) time and its truthful provenance, whose
``received_at`` is the transport receipt time of the request or EDIT
decision that produced it (revision 1 is received and created in the
same service call, so both equal there); a stored revision is never
mutated, and ``current_revision`` always equals the last entry's number.
``decisions`` is the append-only decision history. ``authorization_ids``
references the authorization records held at the store's top level,
where the P1-A6 parent check can resolve them by digest.

Hard bounds (``MAX_MISSION_REVISIONS``, ``MAX_MISSION_DECISIONS``) are
module constants never derived from input; at a bound the operation is
refused and history is never pruned.
"""

from mission import decision as decision_module
from mission import record

MISSION_KEYS = (
    "schema_version", "mission_id", "request_id", "created_at", "updated_at",
    "state", "current_revision", "revisions", "decisions",
    "authorization_ids",
)
REVISION_KEYS = (
    "revision", "proposal", "proposal_digest_sha256", "created_at",
    "provenance",
)

# Hard bounds, never derived from input. Exact-value pinned.
MAX_MISSION_REVISIONS = 64
MAX_MISSION_DECISIONS = 256

PROBLEM_MALFORMED_STATE = "mission_malformed_state"
PROBLEM_REVISIONS_FULL = "mission_revisions_full"
PROBLEM_DECISIONS_FULL = "mission_decisions_full"


def new_revision_entry(revision, proposal, created_at, provenance):
    clean = record.validate_proposal(proposal)
    return {
        "revision": revision,
        "proposal": clean,
        "proposal_digest_sha256": record.proposal_digest(clean),
        "created_at": created_at,
        "provenance": provenance,
    }


def new_mission_record(mission_id, request_id, proposal, created_at, context):
    """Revision 1 of a new Mission, AWAITING_DECISION, no authority."""
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    record.require_id(request_id, record.REQUEST_ID_PREFIX, "request_id")
    record.require_timestamp(created_at, "created_at")
    provenance = record.provenance_record(
        context, created_at, record.REFERENCE_KIND_REQUEST, request_id,
        mission_id, 1,
    )
    document = {
        "schema_version": record.SCHEMA_VERSION,
        "mission_id": mission_id,
        "request_id": request_id,
        "created_at": created_at,
        "updated_at": created_at,
        "state": record.STATE_AWAITING_DECISION,
        "current_revision": 1,
        "revisions": [new_revision_entry(1, proposal, created_at, provenance)],
        "decisions": [],
        "authorization_ids": [],
    }
    return validate_mission_record(document)


def append_revision(mission, proposal, created_at, decision_id, context,
                    received_at):
    """Append revision N+1 in place; returns the new entry. Refuses at
    the bound rather than dropping history. ``created_at`` is the
    APPLICATION time (when the service applied the EDIT); ``received_at``
    is the TRANSPORT RECEIPT time of the EDIT decision and is what the
    revision's provenance records. The two are distinct and are never
    required to be equal."""
    if len(mission["revisions"]) >= MAX_MISSION_REVISIONS:
        record.fail(PROBLEM_REVISIONS_FULL,
                    "mission %s already holds %d revisions; the hard bound"
                    " is %d and history is never pruned"
                    % (mission["mission_id"], len(mission["revisions"]),
                       MAX_MISSION_REVISIONS))
    revision = mission["current_revision"] + 1
    provenance = record.provenance_record(
        context, received_at, record.REFERENCE_KIND_DECISION, decision_id,
        mission["mission_id"], revision,
    )
    entry = new_revision_entry(revision, proposal, created_at, provenance)
    mission["revisions"].append(entry)
    mission["current_revision"] = revision
    mission["updated_at"] = created_at
    return entry


def append_decision(mission, decision_record):
    if len(mission["decisions"]) >= MAX_MISSION_DECISIONS:
        record.fail(PROBLEM_DECISIONS_FULL,
                    "mission %s already holds %d decisions; the hard bound"
                    " is %d and history is never pruned"
                    % (mission["mission_id"], len(mission["decisions"]),
                       MAX_MISSION_DECISIONS))
    mission["decisions"].append(decision_record)


def current_revision_entry(mission):
    return mission["revisions"][-1]


def revision_entry(mission, revision):
    """The stored entry for ``revision``, or None."""
    if not isinstance(revision, int) or isinstance(revision, bool):
        return None
    if 1 <= revision <= len(mission["revisions"]):
        entry = mission["revisions"][revision - 1]
        if entry["revision"] == revision:
            return entry
    return None


def validate_mission_record(value, location="mission"):
    try:
        return _validate_mission_record(value, location)
    except record.MissionError as exc:
        if exc.problem in (PROBLEM_REVISIONS_FULL, PROBLEM_DECISIONS_FULL):
            raise
        raise record.MissionError(str(exc), PROBLEM_MALFORMED_STATE)


def _validate_mission_record(value, location):
    record.require_dict(value, location)
    record.require_closed_keys(value, MISSION_KEYS, location)
    if value["schema_version"] != record.SCHEMA_VERSION or isinstance(
        value["schema_version"], bool
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.schema_version must be %d" % (location, record.SCHEMA_VERSION))
    mission_id = record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                                   location + ".mission_id")
    record.require_id(value["request_id"], record.REQUEST_ID_PREFIX,
                      location + ".request_id")
    created = record.require_timestamp(value["created_at"], location + ".created_at")
    updated = record.require_timestamp(value["updated_at"], location + ".updated_at")
    if updated < created:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.updated_at precedes created_at" % location)
    record.require_state(value["state"], location + ".state")
    if value["state"] not in record.REACHABLE_STATES:
        record.fail(record.PROBLEM_UNKNOWN_STATE,
                    "%s.state %r is declared but not reachable by any wired"
                    " transition; the record is malformed"
                    % (location, value["state"]))
    current = record.require_int(value["current_revision"],
                                 location + ".current_revision", minimum=1)
    revisions = value["revisions"]
    if not isinstance(revisions, list) or not revisions:
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s.revisions must be a non-empty list" % location)
    if len(revisions) > MAX_MISSION_REVISIONS:
        record.fail(PROBLEM_REVISIONS_FULL,
                    "%s holds %d revisions; the hard bound is %d"
                    % (location, len(revisions), MAX_MISSION_REVISIONS))
    if len(revisions) != current:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.current_revision %d disagrees with %d stored revisions"
                    % (location, current, len(revisions)))
    for index, entry in enumerate(revisions):
        where = "%s.revisions[%d]" % (location, index)
        record.require_dict(entry, where)
        record.require_closed_keys(entry, REVISION_KEYS, where)
        if entry["revision"] != index + 1 or isinstance(entry["revision"], bool):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.revision must be %d (revisions are exact and"
                        " contiguous)" % (where, index + 1))
        clean = record.validate_proposal(entry["proposal"], where + ".proposal")
        if clean != entry["proposal"]:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.proposal is not in normalized form" % where)
        record.require_hex(entry["proposal_digest_sha256"],
                           where + ".proposal_digest_sha256", 64)
        if entry["proposal_digest_sha256"] != record.proposal_digest(clean):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.proposal_digest_sha256 does not match the stored"
                        " proposal" % where)
        record.require_timestamp(entry["created_at"], where + ".created_at")
        provenance = record.validate_provenance(entry["provenance"],
                                                where + ".provenance")
        if provenance["mission_id"] != mission_id or (
            provenance["revision"] != index + 1
        ):
            record.fail(record.PROBLEM_PROVENANCE,
                        "%s.provenance bindings disagree with the revision"
                        % where)
        expected_kind = (record.REFERENCE_KIND_REQUEST if index == 0
                         else record.REFERENCE_KIND_DECISION)
        if provenance["reference_kind"] != expected_kind:
            record.fail(record.PROBLEM_PROVENANCE,
                        "%s.provenance.reference_kind must be %r"
                        % (where, expected_kind))
        if index == 0 and provenance["reference_id"] != value["request_id"]:
            record.fail(record.PROBLEM_PROVENANCE,
                        "%s.provenance.reference_id must be the request id"
                        % where)
    decisions = value["decisions"]
    if not isinstance(decisions, list):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s.decisions must be a list" % location)
    if len(decisions) > MAX_MISSION_DECISIONS:
        record.fail(PROBLEM_DECISIONS_FULL,
                    "%s holds %d decisions; the hard bound is %d"
                    % (location, len(decisions), MAX_MISSION_DECISIONS))
    seen = set()
    for index, entry in enumerate(decisions):
        where = "%s.decisions[%d]" % (location, index)
        decision_module.validate_decision_record(entry, where)
        if entry["mission_id"] != mission_id:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s names another mission" % where)
        if entry["revision"] > current:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s names a revision that does not exist" % where)
        produced = entry["outcome"]["resulting_revision"]
        if produced > current or revisions[produced - 1][
            "proposal_digest_sha256"
        ] != entry["outcome"]["proposal_digest_sha256"]:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.outcome binds a manifest digest that revision %d"
                        " does not carry" % (where, produced))
        if entry["decision_id"] in seen:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s repeats a decision id" % where)
        seen.add(entry["decision_id"])
    ids = value["authorization_ids"]
    if not isinstance(ids, list):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s.authorization_ids must be a list" % location)
    for index, item in enumerate(ids):
        record.require_id(item, record.AUTHORIZATION_ID_PREFIX,
                          "%s.authorization_ids[%d]" % (location, index))
    if len(set(ids)) != len(ids):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.authorization_ids repeats an id" % location)
    return value
