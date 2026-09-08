"""Bot coordination: per-Mission participant rosters and bot-handoff
records that carry context and a request between eligible participants
— never ownership, authority, or verified-result status.

Roster. ``participants[<mission_id>]`` names the specialist participants
(architecture §3: COORDINATOR, ENGINEERING, RESEARCH, OPERATIONS,
RELEASE, BROWSER_QA, INCIDENT_RECOVERY) eligible for one Mission,
registered under a FRESH observation. Eligibility is answered against
this durable roster, never by a bot deciding for itself. A roster may
grow; it never drops a participant any handoff of the Mission names,
so a stored handoff can never become unloadable by a later edit.

Handoff. A record binds the exact Mission and revision (the observed
``current_revision`` and ``proposal_digest_sha256`` at creation), an
eligible ``source`` and ``destination`` (distinct, both on the roster),
a closed ``purpose``, bounded ``request_text``, the cited Mission-local
references each WITH the validity level the citation claims, the
caller's ``idempotency_key``, the record's own content digest, the
forwarding link, a closed ``status`` with its bounded transition
history, truthful provenance, ``transfers ==
"context_and_request_only"`` and ``authority == "none"``. The loader
refuses any other value of the last two: the shape cannot express a
transfer of ownership, authority or verified-result status.

References (AMD-3). A citation names a reference id and a level
(``RECORDED``, ``ACCEPTED`` for evidence, ``VALIDATED`` for artifacts).
At creation AND at every status transition each citation is checked
against a FRESH observation through the ONE citation check,
``observation.require_reference_level``: a reference the observation
does not carry is not Mission-local (a cross-Mission reference fails
here), and a level the observation does not positively prove is
refused as ``coordination_reference_not_proven``. Presence proves
Mission-locality and nothing more.

Idempotency and duplicate effects. ``(mission_id, idempotency_key)`` is
unique. Creating again with the same key and the same content returns
the existing record and writes nothing; the same key with different
content, or a handoff id already in use, is
``coordination_idempotency_conflict``. A fresh key does not license a
second live identical request (R-4a): a new handoff whose content
digest equals that of an existing NON-TERMINAL handoff of the same
Mission is refused as ``coordination_duplicate_effect``, and the loader
refuses two such live records. Once the earlier handoff is terminal
(ANSWERED, DECLINED, WITHDRAWN) an identical request is a legitimate
new effect. Stated residual limit: if a response is lost before the
caller learns its key, a retry with a new key can still create a second
effect once the first has gone terminal — the same class of limit
Mission Core states for its own id reservation.

Forwarding a parent that is no longer live is refused (Lead ruling 2):
OPEN, ACCEPTED and ANSWERED parents may be forwarded (passing an answer
along is legitimate); DECLINED and WITHDRAWN parents may not, because
the chain would then misrepresent a request its addressee declined or
its source retracted as active. This is checked at creation against the
parent's status THEN; a parent declined or withdrawn after a forward
was made does not unwind the forward, which recorded the parent's exact
content, not its later status.

Forwarding (AMD-2): ONE proven path, root source included.
- ``parent_content_digest_sha256`` equals the parent's content digest
  (the child forwards THAT exact content);
- ``child.source == parent.destination``: you may forward only what was
  addressed to you (``coordination_forward_continuity``);
- the chain's participant set is ``{root.source}`` plus every link's
  destination; a destination already in that set is a loop
  (``coordination_forwarding_loop``), so forwarding back to the root
  source is refused even though no earlier destination repeats;
  ``destination != source`` at every link;
- revision compatibility is EXACT: a forward is created under a FRESH
  observation, the child's revision is the observed current revision,
  and the parent's revision must equal it; a handoff created at a
  superseded revision cannot be forwarded (``coordination_handoff_stale``);
- connectivity is total: the root has depth 0 and no parent; every
  other link's parent exists in the same Mission with depth exactly one
  less; ``forward_depth <= MAX_HANDOFF_FORWARD_DEPTH``.
Every rule is enforced on creation and re-validated on every load.

Transitions. ACCEPT / ANSWER / DECLINE are the destination's acts and
WITHDRAW the source's; the table ``record.HANDOFF_TRANSITIONS`` is
closed and ANSWERED / DECLINED / WITHDRAWN are terminal. Every
transition requires a FRESH observation and re-validates the Mission
binding, exact revision compatibility, and every cited reference at its
cited level; a stale or unavailable observation refuses. Staleness is
never a stored status.

Nothing here reads a clock, observes a Mission, sends anything, or
writes durable state; the service does, under the store lock.
"""

from workflow_authority.digest import json_digest

from coordination import observation
from coordination import record

# Exact-value pinned in the bound-constant table.
MAX_PARTICIPANTS_PER_MISSION = 16
# A chain root plus at most this many forwards.
MAX_HANDOFF_FORWARD_DEPTH = 4
# The longest legal path is OPEN -> ACCEPTED -> ANSWERED (3 entries).
MAX_HANDOFF_TRANSITIONS = 8

ROSTER_KEYS = (
    "mission_id", "participants", "revision", "registered_at", "registered_by",
    "observation_point", "authority",
)
CITATION_KEYS = ("reference_id", "level")
TRANSITION_KEYS = ("status", "at", "actor", "by", "observation_point")
HANDOFF_KEYS = (
    "handoff_id", "mission_id", "revision", "proposal_digest_sha256", "source",
    "destination", "purpose", "request_text", "evidence_refs", "artifact_refs",
    "idempotency_key", "content_digest_sha256", "parent_handoff_id",
    "parent_content_digest_sha256", "forward_depth", "status", "transitions",
    "created_at", "updated_at", "observation_point", "provenance", "transfers",
    "authority",
)
# The fields the content digest covers: what a forward forwards.
CONTENT_KEYS = (
    "mission_id", "revision", "source", "destination", "purpose", "request_text",
    "evidence_refs", "artifact_refs", "parent_handoff_id",
)
# Which participant performs each status change.
_ACTOR_FIELD = {
    record.HANDOFF_ACCEPTED: "destination",
    record.HANDOFF_ANSWERED: "destination",
    record.HANDOFF_DECLINED: "destination",
    record.HANDOFF_WITHDRAWN: "source",
}
_FRESHNESS_PROBLEMS = {
    record.FRESHNESS_UNAVAILABLE: record.PROBLEM_OBSERVATION_UNAVAILABLE,
    record.FRESHNESS_STALE: record.PROBLEM_OBSERVATION_STALE,
    record.FRESHNESS_INCONSISTENT: record.PROBLEM_OBSERVATION_INCONSISTENT,
    record.FRESHNESS_ABSENT: record.PROBLEM_MISSION_NOT_OBSERVED,
}


# -- shared -----------------------------------------------------------


def require_fresh(result, mission_id, what):
    """The FRESH observation of ``mission_id`` in ``result``, or refuse
    with the problem code of its freshness."""
    if not isinstance(result, observation.FreshnessResult):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s requires a classified FreshnessResult; got %s"
                    % (what, type(result).__name__))
    record.require_member(result.freshness, record.FRESHNESS_STATES, "freshness")
    if result.freshness != record.FRESHNESS_FRESH:
        record.fail(_FRESHNESS_PROBLEMS[result.freshness],
                    "%s requires a FRESH observation of mission %s; it is %s: %s"
                    % (what, mission_id, result.freshness, result.problem))
    if result.observation.mission_id != mission_id:
        record.fail(record.PROBLEM_MISSION_MISMATCH,
                    "%s names mission %s but the observation is of %s"
                    % (what, mission_id, result.observation.mission_id))
    return result.observation


def require_transfers(value, location):
    if not isinstance(value, str) or value != record.TRANSFERS_CONTEXT_AND_REQUEST_ONLY:
        record.fail(record.PROBLEM_AUTHORITY_CLAIM,
                    "%s.transfers must be %r: a handoff carries context and a"
                    " request, never ownership, authority or verified-result"
                    " status; got %r"
                    % (location, record.TRANSFERS_CONTEXT_AND_REQUEST_ONLY, value))
    return value


# -- roster -----------------------------------------------------------


def validate_roster(value, location="roster"):
    record.require_dict(value, location)
    record.require_closed_keys(value, ROSTER_KEYS, location)
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_sorted_members(value["participants"], record.PARTICIPANTS,
                                  location + ".participants",
                                  MAX_PARTICIPANTS_PER_MISSION, allow_empty=False)
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_timestamp(value["registered_at"], location + ".registered_at")
    record.validate_context_dict(value["registered_by"], location + ".registered_by")
    point = observation.validate_observation_point(value["observation_point"],
                                                   location + ".observation_point")
    if point["revision"] != value["revision"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.revision %d is not its observation point's revision %d"
                    % (location, value["revision"], point["revision"]))
    record.require_authority(value["authority"], location)
    return value


def validate_rosters(document, path):
    for key in sorted(document["participants"]):
        roster = document["participants"][key]
        validate_roster(roster, "participants[%r]" % key)
        if roster["mission_id"] != key:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "participants[%r] carries mission_id %r"
                        % (key, roster["mission_id"]))
    return document


def require_eligible(document, mission_id, participant, location):
    """``participant`` is on ``mission_id``'s roster, or refuse."""
    record.require_member(participant, record.PARTICIPANTS, location)
    roster = document["participants"].get(mission_id)
    if roster is None:
        record.fail(record.PROBLEM_ROSTER_MISSING,
                    "%s: mission %s has no participant roster; eligibility is"
                    " answered against the roster, never assumed"
                    % (location, mission_id))
    if participant not in roster["participants"]:
        record.fail(record.PROBLEM_PARTICIPANT_INELIGIBLE,
                    "%s: %s is not an eligible participant of mission %s (roster:"
                    " %s)" % (location, participant, mission_id,
                              ", ".join(roster["participants"])))
    return participant


def set_roster(document, mission_id, participants, result, now, context):
    """Register (or replace) the roster of ``mission_id`` under a FRESH
    observation; never drops a participant a stored handoff names."""
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    record.require_context(context)
    record.require_timestamp(now, "now")
    observed = require_fresh(result, mission_id, "roster registration")
    roster = validate_roster({
        "mission_id": mission_id,
        "participants": participants,
        "revision": observed.current_revision,
        "registered_at": now,
        "registered_by": context.as_dict(),
        "observation_point": observation.observation_point(observed),
        "authority": record.AUTHORITY_NONE,
    })
    for key in sorted(document["handoffs"]):
        value = document["handoffs"][key]
        if value["mission_id"] != mission_id:
            continue
        for field in ("source", "destination"):
            if value[field] not in roster["participants"]:
                record.fail(record.PROBLEM_PARTICIPANT_INELIGIBLE,
                            "roster of mission %s cannot drop %s: handoff %s names"
                            " it as %s" % (mission_id, value[field], key, field))
    document["participants"][mission_id] = roster
    return roster


# -- citations --------------------------------------------------------


def validate_citation(value, prefix, location):
    """One ``{reference_id, level}`` citation for ``prefix`` references."""
    record.require_dict(value, location)
    record.require_closed_keys(value, CITATION_KEYS, location)
    record.require_id(value["reference_id"], prefix, location + ".reference_id")
    allowed = (record.EVIDENCE_REFERENCE_LEVELS if prefix == record.EVIDENCE_ID_PREFIX
               else record.ARTIFACT_REFERENCE_LEVELS)
    record.require_member(value["level"], allowed, location + ".level")
    return value


def validate_citations(value, prefix, location):
    record.require_list(value, location, record.MAX_REFERENCE_LIST)
    ids = []
    for index, entry in enumerate(value):
        validate_citation(entry, prefix, "%s[%d]" % (location, index))
        ids.append(entry["reference_id"])
    if len(set(ids)) != len(ids):
        record.fail(record.PROBLEM_BAD_VALUE, "%s cites a reference twice" % location)
    if ids != sorted(ids):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must be sorted by reference id (deterministic form)"
                    % location)
    return value


def require_same_proposal(observed, value, location):
    """The COMPLETE proposal binding still holds (review F4): the observed
    current revision AND its proposal digest equal the handoff's. The
    revision number alone is not the binding."""
    if observed.current_revision != value["revision"] or (
        observed.proposal_digest_sha256 != value["proposal_digest_sha256"]
    ):
        record.fail(record.PROBLEM_HANDOFF_STALE,
                    "%s is bound to revision %d / proposal %s but mission %s is"
                    " observed at revision %d / proposal %s; a handoff whose"
                    " proposal binding no longer holds cannot change or be"
                    " forwarded"
                    % (location, value["revision"], value["proposal_digest_sha256"],
                       value["mission_id"], observed.current_revision,
                       observed.proposal_digest_sha256))
    return observed


def require_citations_proven(observed, value, location):
    """Every cited reference is Mission-local per ``observed`` and cited
    at a level the observation positively proves."""
    for field in ("evidence_refs", "artifact_refs"):
        for index, entry in enumerate(value[field]):
            observation.require_reference_level(
                observed, entry["reference_id"], entry["level"],
                "%s.%s[%d]" % (location, field, index))


# -- record -----------------------------------------------------------


def content_digest(value):
    """Content identity of a handoff: what a forward forwards."""
    return json_digest(dict((key, value[key]) for key in CONTENT_KEYS))


def _validate_transition_entry(value, location):
    record.require_dict(value, location)
    record.require_closed_keys(value, TRANSITION_KEYS, location)
    record.require_member(value["status"], record.HANDOFF_STATUSES,
                          location + ".status")
    record.require_timestamp(value["at"], location + ".at")
    record.require_member(value["actor"], record.PARTICIPANTS, location + ".actor")
    record.validate_context_dict(value["by"], location + ".by")
    observation.validate_observation_point(value["observation_point"],
                                           location + ".observation_point")
    return value


def validate_handoff(value, location="handoff"):
    """One closed handoff record and its own coherence; chain rules that
    need other records live in ``validate_handoffs``."""
    record.require_dict(value, location)
    record.require_closed_keys(value, HANDOFF_KEYS, location)
    record.require_id(value["handoff_id"], record.HANDOFF_ID_PREFIX,
                      location + ".handoff_id")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_hex(value["proposal_digest_sha256"],
                       location + ".proposal_digest_sha256", 64)
    record.require_member(value["source"], record.PARTICIPANTS, location + ".source")
    record.require_member(value["destination"], record.PARTICIPANTS,
                          location + ".destination")
    if value["source"] == value["destination"]:
        record.fail(record.PROBLEM_PARTICIPANT_INELIGIBLE,
                    "%s hands off from %s to itself" % (location, value["source"]))
    record.require_member(value["purpose"], record.HANDOFF_PURPOSES,
                          location + ".purpose")
    record.require_str(value["request_text"], location + ".request_text",
                       record.MAX_REQUEST_TEXT_CHARS)
    validate_citations(value["evidence_refs"], record.EVIDENCE_ID_PREFIX,
                       location + ".evidence_refs")
    validate_citations(value["artifact_refs"], record.ARTIFACT_ID_PREFIX,
                       location + ".artifact_refs")
    record.require_key(value["idempotency_key"], location + ".idempotency_key")
    record.require_hex(value["content_digest_sha256"],
                       location + ".content_digest_sha256", 64)
    if value["content_digest_sha256"] != content_digest(value):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.content_digest_sha256 does not recompute from the record's"
                    " content" % location)
    record.require_optional_id(value["parent_handoff_id"], record.HANDOFF_ID_PREFIX,
                               location + ".parent_handoff_id")
    record.require_optional_hex(value["parent_content_digest_sha256"],
                                location + ".parent_content_digest_sha256", 64)
    depth = record.require_int(value["forward_depth"], location + ".forward_depth",
                               minimum=0, maximum=MAX_HANDOFF_FORWARD_DEPTH)
    forwarded = value["parent_handoff_id"] is not None
    if forwarded != (value["parent_content_digest_sha256"] is not None) or (
        forwarded != (depth > 0)
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s records a forward as (parent id, parent content digest,"
                    " depth > 0) together or not at all" % location)
    if value["parent_handoff_id"] == value["handoff_id"]:
        record.fail(record.PROBLEM_BAD_VALUE, "%s forwards itself" % location)
    status = record.require_member(value["status"], record.HANDOFF_STATUSES,
                                   location + ".status")
    transitions = record.require_list(value["transitions"], location + ".transitions",
                                      MAX_HANDOFF_TRANSITIONS)
    if not transitions:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.transitions must begin with the OPEN entry" % location)
    point = observation.validate_observation_point(value["observation_point"],
                                                   location + ".observation_point")
    previous = None
    for index, entry in enumerate(transitions):
        sub = "%s.transitions[%d]" % (location, index)
        _validate_transition_entry(entry, sub)
        if index > 0 and entry["status"] not in _ACTOR_FIELD:
            # Only the initial entry is OPEN; a later OPEN is not a wired
            # transition and is refused with a structured code, never a
            # bare lookup error.
            record.fail(record.PROBLEM_INVALID_TRANSITION,
                        "%s: %s is only ever the initial entry" % (sub, entry["status"]))
        # Review F5: the loader re-proves what the service enforced — the
        # actor's relationship to the handoff, the exact proposal binding
        # of every transition's read, and a cursor that never moves back.
        expected_actor = (value["source"] if index == 0
                          else value[_ACTOR_FIELD[entry["status"]]])
        if entry["actor"] != expected_actor:
            record.fail(record.PROBLEM_PARTICIPANT_INELIGIBLE,
                        "%s records actor %s but %s is the act of the handoff's %s"
                        " (%s)" % (sub, entry["actor"], entry["status"],
                                   "source" if index == 0
                                   else _ACTOR_FIELD[entry["status"]],
                                   expected_actor))
        at_point = entry["observation_point"]
        if at_point["revision"] != value["revision"] or (
            at_point["proposal_digest_sha256"] != value["proposal_digest_sha256"]
        ):
            record.fail(record.PROBLEM_HANDOFF_STALE,
                        "%s was observed at revision %d / proposal %s, not the"
                        " handoff's revision %d / proposal %s"
                        % (sub, at_point["revision"], at_point["proposal_digest_sha256"],
                           value["revision"], value["proposal_digest_sha256"]))
        floor = point if index == 0 else previous["observation_point"]
        if record.cursor_value(at_point["cursor"]) < record.cursor_value(floor["cursor"]):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s was observed at cursor %s, behind the cursor %s before"
                        " it; a transition never moves the cursor back"
                        % (sub, at_point["cursor"], floor["cursor"]))
        if index == 0:
            if entry["status"] != record.HANDOFF_OPEN or (
                entry["at"] != value["created_at"]
            ) or at_point != point:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s must be OPEN at created_at under the record's own"
                            " observation point" % sub)
        else:
            if entry["status"] not in record.HANDOFF_TRANSITIONS[previous["status"]]:
                record.fail(record.PROBLEM_INVALID_TRANSITION,
                            "%s: %s -> %s is not wired"
                            % (sub, previous["status"], entry["status"]))
            if entry["at"] < previous["at"]:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s precedes the transition before it" % sub)
        previous = entry
    if previous["status"] != status:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.status %s is not the last transition's %s"
                    % (location, status, previous["status"]))
    record.require_timestamp(value["created_at"], location + ".created_at")
    record.require_timestamp(value["updated_at"], location + ".updated_at")
    if value["updated_at"] != previous["at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.updated_at is not the last transition's time" % location)
    point = observation.validate_observation_point(value["observation_point"],
                                                   location + ".observation_point")
    if point["revision"] != value["revision"] or (
        point["proposal_digest_sha256"] != value["proposal_digest_sha256"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s binds revision %d / proposal digest %s but its observation"
                    " point disagrees" % (location, value["revision"],
                                          value["proposal_digest_sha256"]))
    provenance = record.validate_provenance(value["provenance"],
                                            location + ".provenance")
    if provenance["received_at"] != value["created_at"] or (
        provenance["observed_revision"] != value["revision"]
    ) or provenance["observation_cursor"] != point["cursor"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.provenance does not restate created_at and the observation"
                    " point" % location)
    require_transfers(value["transfers"], location)
    record.require_authority(value["authority"], location)
    return value


def chain(document, handoff_id):
    """Handoff ids from the root to ``handoff_id``; refuses a broken link."""
    path = []
    current = handoff_id
    seen = set()
    while current is not None:
        if current in seen or len(path) > MAX_HANDOFF_FORWARD_DEPTH:
            record.fail(record.PROBLEM_FORWARDING_LOOP,
                        "handoff %s: the forward chain does not terminate"
                        % handoff_id)
        value = document["handoffs"].get(current)
        if value is None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "handoff %s names parent %s, which is not in the document"
                        % (handoff_id, current))
        seen.add(current)
        path.append(current)
        current = value["parent_handoff_id"]
    path.reverse()
    return path


def chain_participants(document, handoff_id):
    """The root source followed by every link's destination, root first."""
    path = chain(document, handoff_id)
    root = document["handoffs"][path[0]]
    return [root["source"]] + [document["handoffs"][key]["destination"]
                               for key in path]


def _require_link(document, value, where):
    """The AMD-2 rules that bind ``value`` to its parent, or refuse."""
    parent_id = value["parent_handoff_id"]
    if parent_id is None:
        return
    parent = document["handoffs"].get(parent_id)
    if parent is None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s names parent %s, which is not in the document"
                    % (where, parent_id))
    if parent["mission_id"] != value["mission_id"]:
        record.fail(record.PROBLEM_MISSION_MISMATCH,
                    "%s forwards handoff %s of mission %s inside mission %s"
                    % (where, parent_id, parent["mission_id"], value["mission_id"]))
    if value["parent_content_digest_sha256"] != parent["content_digest_sha256"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.parent_content_digest_sha256 is not parent %s's content"
                    " digest; a forward carries that exact content"
                    % (where, parent_id))
    if value["source"] != parent["destination"]:
        record.fail(record.PROBLEM_FORWARD_CONTINUITY,
                    "%s is forwarded by %s but parent %s was addressed to %s; only"
                    " the addressee may forward"
                    % (where, value["source"], parent_id, parent["destination"]))
    if parent["revision"] != value["revision"] or (
        parent["proposal_digest_sha256"] != value["proposal_digest_sha256"]
    ):
        record.fail(record.PROBLEM_HANDOFF_STALE,
                    "%s forwards parent %s bound to revision %d / proposal %s inside"
                    " revision %d / proposal %s; a handoff whose proposal binding"
                    " no longer holds cannot be forwarded"
                    % (where, parent_id, parent["revision"],
                       parent["proposal_digest_sha256"], value["revision"],
                       value["proposal_digest_sha256"]))
    if value["forward_depth"] != parent["forward_depth"] + 1:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.forward_depth %d is not parent %s's depth %d plus one"
                    % (where, value["forward_depth"], parent_id,
                       parent["forward_depth"]))
    upstream = chain_participants(document, parent_id)
    if value["destination"] in upstream:
        record.fail(record.PROBLEM_FORWARDING_LOOP,
                    "%s forwards to %s, which is already on the path %s; a"
                    " forward never returns to the root source or any earlier"
                    " destination" % (where, value["destination"],
                                      " -> ".join(upstream)))


def validate_handoffs(document, path):
    """Every handoff record, key equals id, unique idempotency key per
    Mission, roster eligibility, and the AMD-2 chain rules."""
    validate_rosters(document, path)
    keys = {}
    for key in sorted(document["handoffs"]):
        value = document["handoffs"][key]
        where = "handoff %r" % key
        validate_handoff(value, where)
        if value["handoff_id"] != key:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries handoff_id %r" % (where, value["handoff_id"]))
        who = (value["mission_id"], value["idempotency_key"])
        if who in keys:
            record.fail(record.PROBLEM_IDEMPOTENCY_CONFLICT,
                        "%s and handoff %r share idempotency key %r in mission %s"
                        % (where, keys[who], who[1], who[0]))
        keys[who] = key
        require_eligible(document, value["mission_id"], value["source"],
                         where + ".source")
        require_eligible(document, value["mission_id"], value["destination"],
                         where + ".destination")
    live = {}
    for key in sorted(document["handoffs"]):
        value = document["handoffs"][key]
        if value["status"] in record.TERMINAL_HANDOFF_STATUSES:
            continue
        who = (value["mission_id"], value["content_digest_sha256"])
        if who in live:
            record.fail(record.PROBLEM_DUPLICATE_EFFECT,
                        "handoff %r and handoff %r are both live with identical"
                        " content in mission %s; a request is live once"
                        % (key, live[who], who[0]))
        live[who] = key
    for key in sorted(document["handoffs"]):
        _require_link(document, document["handoffs"][key], "handoff %r" % key)
    return document


# Parents that may be forwarded (Lead ruling 2): a live request or an
# answer being passed along, never a declined or withdrawn request.
FORWARDABLE_STATUSES = (record.HANDOFF_ACCEPTED, record.HANDOFF_ANSWERED,
                        record.HANDOFF_OPEN)


# -- creation ---------------------------------------------------------


def _transition_entry(status, now, actor, context, observed):
    return {
        "status": status,
        "at": now,
        "actor": actor,
        "by": context.as_dict(),
        "observation_point": observation.observation_point(observed),
    }


def create_handoff(document, handoff_id, mission_id, source, destination,
                   purpose, request_text, evidence_refs, artifact_refs,
                   idempotency_key, parent_handoff_id, result, now, context):
    """A new OPEN handoff under a FRESH observation, or the existing
    record for a same-content replay of ``idempotency_key``."""
    record.require_id(handoff_id, record.HANDOFF_ID_PREFIX, "handoff_id")
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    record.require_context(context)
    record.require_timestamp(now, "now")
    record.require_key(idempotency_key, "idempotency_key")
    observed = require_fresh(result, mission_id, "handoff creation")
    require_eligible(document, mission_id, source, "source")
    require_eligible(document, mission_id, destination, "destination")
    point = observation.observation_point(observed)
    candidate = {
        "handoff_id": handoff_id,
        "mission_id": mission_id,
        "revision": observed.current_revision,
        "proposal_digest_sha256": observed.proposal_digest_sha256,
        "source": source,
        "destination": destination,
        "purpose": purpose,
        "request_text": request_text,
        "evidence_refs": evidence_refs,
        "artifact_refs": artifact_refs,
        "idempotency_key": idempotency_key,
        "content_digest_sha256": None,
        "parent_handoff_id": parent_handoff_id,
        "parent_content_digest_sha256": None,
        "forward_depth": 0,
        "status": record.HANDOFF_OPEN,
        "transitions": [_transition_entry(record.HANDOFF_OPEN, now, source, context,
                                          observed)],
        "created_at": now,
        "updated_at": now,
        "observation_point": point,
        "provenance": record.provenance_record(context, now,
                                               observed.current_revision,
                                               point["cursor"]),
        "transfers": record.TRANSFERS_CONTEXT_AND_REQUEST_ONLY,
        "authority": record.AUTHORITY_NONE,
    }
    if parent_handoff_id is not None:
        parent = document["handoffs"].get(parent_handoff_id)
        if parent is None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "parent handoff %s is not in the document" % parent_handoff_id)
        if parent["status"] not in FORWARDABLE_STATUSES:
            record.fail(record.PROBLEM_INVALID_TRANSITION,
                        "parent handoff %s is %s and is no longer a live request;"
                        " only an OPEN, ACCEPTED or ANSWERED handoff is forwarded"
                        % (parent_handoff_id, parent["status"]))
        candidate["parent_content_digest_sha256"] = parent["content_digest_sha256"]
        candidate["forward_depth"] = parent["forward_depth"] + 1
        if candidate["forward_depth"] > MAX_HANDOFF_FORWARD_DEPTH:
            record.fail(record.PROBLEM_FORWARD_DEPTH_EXCEEDED,
                        "forwarding %s would reach depth %d; the hard bound is %d"
                        % (parent_handoff_id, candidate["forward_depth"],
                           MAX_HANDOFF_FORWARD_DEPTH))
    candidate["content_digest_sha256"] = content_digest(candidate)
    validate_handoff(candidate)
    require_citations_proven(observed, candidate, "handoff")
    for key in sorted(document["handoffs"]):
        existing = document["handoffs"][key]
        if existing["mission_id"] == mission_id and (
            existing["idempotency_key"] == idempotency_key
        ):
            if existing["content_digest_sha256"] == candidate["content_digest_sha256"]:
                return existing
            record.fail(record.PROBLEM_IDEMPOTENCY_CONFLICT,
                        "idempotency key %r of mission %s was already used with"
                        " different content" % (idempotency_key, mission_id))
    if handoff_id in document["handoffs"]:
        record.fail(record.PROBLEM_IDEMPOTENCY_CONFLICT,
                    "handoff id %s is already in use" % handoff_id)
    for key in sorted(document["handoffs"]):
        existing = document["handoffs"][key]
        if existing["mission_id"] == mission_id and (
            existing["status"] not in record.TERMINAL_HANDOFF_STATUSES
        ) and existing["content_digest_sha256"] == candidate["content_digest_sha256"]:
            record.fail(record.PROBLEM_DUPLICATE_EFFECT,
                        "handoff %s is already live with identical content in"
                        " mission %s; a fresh idempotency key does not license a"
                        " second live request" % (key, mission_id))
    _require_link(document, candidate, "handoff %r" % handoff_id)
    document["handoffs"][handoff_id] = candidate
    return candidate


# -- transitions ------------------------------------------------------


def transition(document, handoff_id, status, actor, result, now, context):
    """One status change by its eligible actor under a FRESH observation
    that re-proves the Mission binding, the exact revision, and every
    cited reference at its cited level."""
    record.require_id(handoff_id, record.HANDOFF_ID_PREFIX, "handoff_id")
    record.require_member(status, record.HANDOFF_STATUSES, "status")
    record.require_member(actor, record.PARTICIPANTS, "actor")
    record.require_context(context)
    record.require_timestamp(now, "now")
    value = document["handoffs"].get(handoff_id)
    if value is None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "handoff %s is not in the document" % handoff_id)
    if status not in record.HANDOFF_TRANSITIONS[value["status"]]:
        record.fail(record.PROBLEM_INVALID_TRANSITION,
                    "handoff %s: %s -> %s is not wired (allowed: %s)"
                    % (handoff_id, value["status"], status,
                       ", ".join(sorted(record.HANDOFF_TRANSITIONS[value["status"]]))
                       or "none"))
    field = _ACTOR_FIELD[status]
    if actor != value[field]:
        record.fail(record.PROBLEM_PARTICIPANT_INELIGIBLE,
                    "handoff %s: only its %s (%s) may mark it %s; %s may not"
                    % (handoff_id, field, value[field], status, actor))
    observed = require_fresh(result, value["mission_id"], "handoff transition")
    require_same_proposal(observed, value, "handoff %s" % handoff_id)
    require_citations_proven(observed, value, "handoff %s" % handoff_id)
    last_cursor = value["transitions"][-1]["observation_point"]["cursor"]
    if record.cursor_value(observed.state_cursor) < record.cursor_value(last_cursor):
        record.fail(record.PROBLEM_OBSERVATION_STALE,
                    "handoff %s: the observation at cursor %s is behind the cursor %s"
                    " of its last transition" % (handoff_id, observed.state_cursor,
                                                  last_cursor))
    if now < value["updated_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "handoff %s: a transition never precedes the one before it"
                    % handoff_id)
    updated = dict(value)
    updated["transitions"] = list(value["transitions"]) + [
        _transition_entry(status, now, actor, context, observed)]
    updated["status"] = status
    updated["updated_at"] = now
    validate_handoff(updated)
    document["handoffs"][handoff_id] = updated
    return updated
