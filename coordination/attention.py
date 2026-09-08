"""Attention: "what needs a human now?" projected from observed Mission
conditions into durable, deduplicated, restart-safe presentation
records, and the abstract presentation seam.

A record binds ONE observed condition (``condition_kind`` ×
``condition_key``) of ONE Mission at ONE revision, with the
authorization digest and the condition's content digest as observed,
to ONE destination (``transport`` + ``conversation_ref``). At most one
NON-TERMINAL record exists per ``(mission_id, condition_kind,
condition_key, destination)``; the loader refuses a second.

Priority (deterministic; lower surfaces first): NEEDS_HUMAN 10,
AUTHORIZATION_READY 20, BLOCKED 30, RESULT_READY 40. Rationale (Lead
ruling): the human is the scarce resource, so the two conditions a
human can act on right now come first; a blocked Mission usually needs
diagnosis before a human action exists; a ready result is the least
time-critical. This is a product decision the human may reverse; the
table is ``record.ATTENTION_PRIORITY`` and reversing it is a one-line
change there — every stored priority is re-derived from the table on
load, so no record can disagree with it.

Projection (``project``) is pure over the document and a classified
observation. Only a FRESH observation changes anything; every other
freshness is reported and changes nothing (an unobservable Mission
cannot prove a blocker cleared). For each observed condition: no
non-terminal record → a new PENDING record; an identical record (same
revision, authorization digest and condition digest) → SUPPRESSED, the
durable duplicate suppression; a differing record → OBSOLETE with the
reason ``REVISION_CHANGED`` > ``AUTHORITY_CHANGED`` > ``CONDITION_CHANGED``
and a PENDING successor it names. A non-terminal record whose condition
the observation no longer carries → RESOLVED ``CONDITION_CLEARED``, with
no successor; a condition that returns later is a NEW presentation,
never a reopened one. Projection is per destination: records bound to
another destination are untouched until that destination projects.

Surfacing (``surface``, A-R1): PENDING → SURFACED requires a FRESH
observation that still carries the record's condition at the record's
revision with the same authorization digest and condition digest. A
non-FRESH observation performs NO transition and returns the freshness.
A FRESH observation that contradicts the record obsoletes or resolves
it exactly as projection would, and presents nothing. Only then is the
injected ``AttentionPresenter`` asked, once, for this one record; the
record becomes SURFACED only on an ``ok`` receipt, with the receipt's
message reference and ``surfaced_freshness`` (always FRESH, by
construction). A declined, raising or ill-typed presenter leaves the
record PENDING and discloses the exception class name only.

No exactly-once claim. A crash between a successful presentation and
the durable save leaves the record PENDING, and the next surface may
present it again. This layer cannot prove otherwise and does not say
otherwise.

Acknowledgment (``acknowledge``) records a human act that already
happened, so it needs no observation; refusing it would destroy truth.
It records ``acknowledged_freshness`` (the classification of whatever
observation accompanied the act) and ``acknowledged_observation_cursor``
(only when that observation was FRESH; null otherwise). It changes
nothing else: the condition, the Mission binding and the authorization
digest are untouched, so an acknowledged blocker still projects as
SUPPRESSED, never as resolved, and nothing here authorizes anything.
ACKNOWLEDGED → OBSOLETE and → RESOLVED stay legal: acknowledging does
not immunise a stale presentation. Terminal records never move
(``coordination_attention_terminal``).

Aggregation (``pending``, ``aggregate``) is deterministic: priority,
then creation time, then id. Nothing here sends a message, reads a
clock, observes a Mission or writes durable state; the service does,
under the store lock. A record carries no authority: presenting an
authorization-ready condition is not approval; a result-ready
condition is not delivery permission.
"""

import abc
import json
from dataclasses import dataclass
from typing import List, Optional

from coordination import observation
from coordination import record

ATTENTION_KEYS = (
    "attention_id", "mission_id", "revision", "condition_kind", "condition_key",
    "condition_digest_sha256", "authorization_digest_sha256", "destination",
    "priority", "presentation", "created_at", "observation_point",
    "surfaced_at", "surfaced_message_ref", "surfaced_freshness",
    "surfaced_observation_point",
    "acknowledged_at", "acknowledged_by", "acknowledged_freshness",
    "acknowledged_observation_cursor", "acknowledged_observation_point",
    "closed_at", "closed_reason", "closed_observation_point",
    "superseded_by", "authority",
)
# Every observation point a record durably retains: the read that
# created it, the FRESH read it was surfaced under, the FRESH read (if
# any) that accompanied its acknowledgment, and the FRESH read that
# closed it (review F2: every change keeps the observation proving it,
# so the high-water floor never regresses to the creation point).
OBSERVATION_POINT_KEYS = (
    "observation_point", "surfaced_observation_point",
    "acknowledged_observation_point", "closed_observation_point",
)
# What the presenter is shown: the record minus nothing, as a copy.
AGGREGATE_KEYS = ("destination", "by_kind", "by_presentation", "missions",
                  "authority")


# -- the presentation seam --------------------------------------------


@dataclass(frozen=True)
class PresentationReceipt:
    """What the presenter reports for ONE presentation attempt: ``ok``
    with the provider's message reference, or not ok with a problem.
    It is a report, never a proof of exactly-once."""

    ok: bool
    message_ref: Optional[str]
    problem: Optional[str]

    def validate(self, location="receipt"):
        record.require_bool(self.ok, location + ".ok")
        if self.ok:
            record.require_str(self.message_ref, location + ".message_ref",
                               record.MAX_MESSAGE_REF_CHARS)
            if self.problem is not None:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s is ok and carries a problem" % location)
        else:
            record.require_str(self.problem, location + ".problem",
                               record.MAX_DETAIL_CHARS)
            if self.message_ref is not None:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s is not ok and carries a message reference"
                            % location)
        return self


class AttentionPresenter(abc.ABC):
    """The abstract presentation seam. Product code holds only this
    contract; recording and fake implementations live in tests. An
    implementation presents ONE record to ONE destination per call and
    returns a PresentationReceipt; it holds no authority."""

    @abc.abstractmethod
    def present(self, destination, presentation):
        """Present ``presentation`` (a copy of the record) at
        ``destination``; returns a PresentationReceipt."""


# -- record -----------------------------------------------------------


def identity(value):
    return (value["mission_id"], value["condition_kind"], value["condition_key"],
            value["destination"]["transport"],
            value["destination"]["conversation_ref"])


def is_terminal(value):
    return value["presentation"] in record.TERMINAL_PRESENTATION_STATES


def _require_same_binding(value, point, location):
    """A point recorded while the record was live must show the record's
    own revision and authorization digest — anything else would have
    obsoleted it instead."""
    observation.validate_observation_point(point, location)
    if point["revision"] != value["revision"] or (
        point["authorization_digest_sha256"] != value["authorization_digest_sha256"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s records revision %d / authorization %r but the record binds"
                    " revision %d / authorization %r"
                    % (location, point["revision"],
                       point["authorization_digest_sha256"], value["revision"],
                       value["authorization_digest_sha256"]))
    return point


def _all_or_none(value, keys, location, what):
    present = [value[key] is not None for key in keys]
    if any(present) and not all(present):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s records %s as %s together or not at all"
                    % (location, what, ", ".join(keys)))
    return all(present)


def validate_attention(value, location="attention"):
    """One closed attention record and its coherence rules."""
    record.require_dict(value, location)
    record.require_closed_keys(value, ATTENTION_KEYS, location)
    record.require_id(value["attention_id"], record.ATTENTION_ID_PREFIX,
                      location + ".attention_id")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    kind = record.require_member(value["condition_kind"], record.ATTENTION_KINDS,
                                 location + ".condition_kind")
    record.require_key(value["condition_key"], location + ".condition_key")
    record.require_hex(value["condition_digest_sha256"],
                       location + ".condition_digest_sha256", 64)
    record.require_optional_hex(value["authorization_digest_sha256"],
                                location + ".authorization_digest_sha256", 64)
    record.require_destination(value["destination"], location + ".destination")
    if value["priority"] != record.ATTENTION_PRIORITY[kind] or isinstance(
        value["priority"], bool
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.priority %r is not the table's %d for %s; priority is"
                    " derived, never stored independently"
                    % (location, value["priority"], record.ATTENTION_PRIORITY[kind],
                       kind))
    presentation = record.require_member(value["presentation"],
                                         record.PRESENTATION_STATES,
                                         location + ".presentation")
    created_at = record.require_timestamp(value["created_at"],
                                          location + ".created_at")
    point = observation.validate_observation_point(value["observation_point"],
                                                   location + ".observation_point")
    if point["revision"] != value["revision"] or (
        point["authorization_digest_sha256"] != value["authorization_digest_sha256"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s binds revision %d and authorization %r but its observation"
                    " point records revision %d and authorization %r"
                    % (location, value["revision"],
                       value["authorization_digest_sha256"], point["revision"],
                       point["authorization_digest_sha256"]))
    # Surfacing fields.
    record.require_optional_timestamp(value["surfaced_at"], location + ".surfaced_at")
    record.require_optional_str(value["surfaced_message_ref"],
                                location + ".surfaced_message_ref",
                                record.MAX_MESSAGE_REF_CHARS)
    if value["surfaced_freshness"] is not None and (
        value["surfaced_freshness"] != record.FRESHNESS_FRESH
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.surfaced_freshness must be FRESH: nothing is presented on"
                    " any other observation" % location)
    if value["surfaced_observation_point"] is not None:
        _require_same_binding(value, value["surfaced_observation_point"],
                              location + ".surfaced_observation_point")
    surfaced = _all_or_none(value, ("surfaced_at", "surfaced_message_ref",
                                    "surfaced_freshness",
                                    "surfaced_observation_point"),
                            location, "surfacing")
    if surfaced and value["surfaced_at"] < created_at:
        record.fail(record.PROBLEM_BAD_VALUE, "%s was surfaced before it was created"
                    % location)
    # Acknowledgment fields.
    record.require_optional_timestamp(value["acknowledged_at"],
                                      location + ".acknowledged_at")
    if value["acknowledged_by"] is not None:
        record.validate_context_dict(value["acknowledged_by"],
                                     location + ".acknowledged_by")
    if value["acknowledged_freshness"] is not None:
        record.require_member(value["acknowledged_freshness"],
                              record.FRESHNESS_STATES,
                              location + ".acknowledged_freshness")
    record.require_optional_cursor(value["acknowledged_observation_cursor"],
                                   location + ".acknowledged_observation_cursor")
    acknowledged = _all_or_none(value, ("acknowledged_at", "acknowledged_by",
                                        "acknowledged_freshness"), location,
                                "acknowledgment")
    fresh_ack = acknowledged and (
        value["acknowledged_freshness"] == record.FRESHNESS_FRESH)
    if (value["acknowledged_observation_cursor"] is not None) != fresh_ack or (
        (value["acknowledged_observation_point"] is not None) != fresh_ack
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.acknowledged_observation_cursor and _point are recorded"
                    " exactly when the acknowledgment's observation was FRESH"
                    % location)
    if fresh_ack:
        # The acknowledgment's read need not match the record's binding: a
        # human may acknowledge a presentation the Mission has moved past,
        # and that FRESH read is still real evidence (it raises the floor).
        point = observation.validate_observation_point(
            value["acknowledged_observation_point"],
            location + ".acknowledged_observation_point")
        if point["cursor"] != value["acknowledged_observation_cursor"]:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.acknowledged_observation_cursor disagrees with the"
                        " acknowledgment's observation point" % location)
    if acknowledged and value["acknowledged_at"] < created_at:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s was acknowledged before it was created" % location)
    # Closure fields.
    record.require_optional_timestamp(value["closed_at"], location + ".closed_at")
    if value["closed_reason"] is not None:
        record.require_member(value["closed_reason"], record.CLOSED_REASONS,
                              location + ".closed_reason")
    record.require_optional_id(value["superseded_by"], record.ATTENTION_ID_PREFIX,
                               location + ".superseded_by")
    if value["closed_observation_point"] is not None:
        observation.validate_observation_point(
            value["closed_observation_point"], location + ".closed_observation_point")
    closed = _all_or_none(value, ("closed_at", "closed_reason",
                                  "closed_observation_point"), location, "closure")
    if closed and value["closed_at"] < created_at:
        record.fail(record.PROBLEM_BAD_VALUE, "%s was closed before it was created"
                    % location)
    # State coherence.
    terminal = presentation in record.TERMINAL_PRESENTATION_STATES
    if closed != terminal:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is %s but %s closure fields"
                    % (location, presentation, "carries" if closed else "lacks"))
    if presentation == record.PRESENTATION_PENDING and (surfaced or acknowledged):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is PENDING but was surfaced or acknowledged" % location)
    if presentation == record.PRESENTATION_SURFACED and (not surfaced or acknowledged):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is SURFACED exactly when surfaced and not acknowledged"
                    % location)
    if presentation == record.PRESENTATION_ACKNOWLEDGED and not acknowledged:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is ACKNOWLEDGED without an acknowledgment" % location)
    if presentation == record.PRESENTATION_OBSOLETE and (
        value["closed_reason"] not in record.OBSOLESCENCE_REASONS
        or value["superseded_by"] is None
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is OBSOLETE exactly with an obsolescence reason and a"
                    " successor" % location)
    if presentation == record.PRESENTATION_RESOLVED and (
        value["closed_reason"] != record.CLOSED_CONDITION_CLEARED
        or value["superseded_by"] is not None
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is RESOLVED exactly with CONDITION_CLEARED and no"
                    " successor" % location)
    if not terminal and value["superseded_by"] is not None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s names a successor but is not obsolete" % location)
    if value["superseded_by"] == value["attention_id"]:
        record.fail(record.PROBLEM_BAD_VALUE, "%s supersedes itself" % location)
    record.require_authority(value["authority"], location)
    return value


def validate_attention_records(document, path):
    """Every record, key equals id, one non-terminal record per identity,
    and every successor exists with the same identity, no earlier
    creation, and exactly one predecessor."""
    live = {}
    successors = {}
    records = document["attention"]
    for key in sorted(records):
        value = records[key]
        where = "attention %r" % key
        validate_attention(value, where)
        if value["attention_id"] != key:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries attention_id %r" % (where, value["attention_id"]))
        who = identity(value)
        if not is_terminal(value):
            if who in live:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s and attention %r are both non-terminal for %r; at"
                            " most one non-terminal record per mission, condition"
                            " kind, condition key and destination"
                            % (where, live[who], who))
            live[who] = key
        successor = value["superseded_by"]
        if successor is not None:
            other = records.get(successor)
            if other is None or identity(other) != who or (
                other["created_at"] < value["created_at"]
            ):
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s names successor %r, which is absent, binds another"
                            " identity, or predates it" % (where, successor))
            if successor in successors:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s and attention %r both name %r as successor"
                            % (where, successors[successor], successor))
            successors[successor] = key
    # Cycle detection (review F7): successor chains are followed to their
    # end; equal timestamps cannot hide a loop.
    for key in sorted(records):
        seen = set()
        current = key
        while current is not None:
            if current in seen:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "attention %r: the successor chain forms a cycle"
                            " through %r; history is acyclic" % (key, current))
            seen.add(current)
            current = records[current]["superseded_by"]
    return document


# -- projection -------------------------------------------------------


@dataclass(frozen=True)
class ProjectionOutcome:
    """What one projection (or one surface reconciliation) did. Ids are
    in deterministic order."""

    freshness: str
    problem: Optional[str]
    created: List[str]
    suppressed: List[str]
    obsoleted: List[str]
    resolved: List[str]


def _require_fresh_of(result, mission_id):
    if not isinstance(result, observation.FreshnessResult):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "a classified FreshnessResult is required; got %s"
                    % type(result).__name__)
    record.require_member(result.freshness, record.FRESHNESS_STATES, "freshness")
    if result.observation is not None and result.observation.mission_id != mission_id:
        record.fail(record.PROBLEM_MISSION_MISMATCH,
                    "the observation is of mission %s, not %s"
                    % (result.observation.mission_id, mission_id))
    return result.freshness == record.FRESHNESS_FRESH


def _mint(mint):
    value = mint()
    return record.require_id(value, record.ATTENTION_ID_PREFIX, "minted attention id")


def _new_record(attention_id, observed, condition, destination, now):
    return validate_attention({
        "attention_id": attention_id,
        "mission_id": observed.mission_id,
        "revision": observed.current_revision,
        "condition_kind": condition.kind,
        "condition_key": condition.key,
        "condition_digest_sha256": observation.condition_digest(condition),
        "authorization_digest_sha256": observed.authorization_digest_sha256,
        "destination": dict((k, destination[k]) for k in record.DESTINATION_KEYS),
        "priority": record.ATTENTION_PRIORITY[condition.kind],
        "presentation": record.PRESENTATION_PENDING,
        "created_at": now,
        "observation_point": observation.observation_point(observed),
        "surfaced_at": None,
        "surfaced_message_ref": None,
        "surfaced_freshness": None,
        "surfaced_observation_point": None,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "acknowledged_freshness": None,
        "acknowledged_observation_cursor": None,
        "acknowledged_observation_point": None,
        "closed_at": None,
        "closed_reason": None,
        "closed_observation_point": None,
        "superseded_by": None,
        "authority": record.AUTHORITY_NONE,
    })


def _disagreement(value, observed, condition):
    """Why ``value`` no longer matches the observed condition, or None."""
    if observed.current_revision != value["revision"]:
        return record.CLOSED_REVISION_CHANGED
    if observed.authorization_digest_sha256 != value["authorization_digest_sha256"]:
        return record.CLOSED_AUTHORITY_CHANGED
    if observation.condition_digest(condition) != value["condition_digest_sha256"]:
        return record.CLOSED_CONDITION_CHANGED
    return None


def _close(value, reason, now, successor, observed):
    value["presentation"] = (record.PRESENTATION_RESOLVED
                             if reason == record.CLOSED_CONDITION_CLEARED
                             else record.PRESENTATION_OBSOLETE)
    value["closed_at"] = now
    value["closed_reason"] = reason
    value["closed_observation_point"] = observation.observation_point(observed)
    value["superseded_by"] = successor
    validate_attention(value)


def _live_records(document, mission_id, destination):
    """Non-terminal records of one Mission at one destination, keyed by
    condition identity, in id order."""
    found = {}
    for key in sorted(document["attention"]):
        value = document["attention"][key]
        if value["mission_id"] == mission_id and not is_terminal(value) and (
            value["destination"] == destination
        ):
            found[(value["condition_kind"], value["condition_key"])] = value
    return found


def _reconcile(document, observed, destination, live, conditions, now, mint):
    """Apply the projection rules for ``conditions`` (the observed ones,
    sorted) against ``live`` (the non-terminal records); returns the
    id lists. Records in ``live`` for conditions not observed resolve."""
    created, suppressed, obsoleted, resolved = [], [], [], []
    seen = set()
    for condition in conditions:
        who = (condition.kind, condition.key)
        seen.add(who)
        current = live.get(who)
        if current is None:
            fresh_id = _mint(mint)
            document["attention"][fresh_id] = _new_record(
                fresh_id, observed, condition, destination, now)
            created.append(fresh_id)
            continue
        reason = _disagreement(current, observed, condition)
        if reason is None:
            suppressed.append(current["attention_id"])
            continue
        fresh_id = _mint(mint)
        document["attention"][fresh_id] = _new_record(
            fresh_id, observed, condition, destination, now)
        _close(current, reason, now, fresh_id, observed)
        obsoleted.append(current["attention_id"])
        created.append(fresh_id)
    for who in sorted(live):
        if who not in seen:
            _close(live[who], record.CLOSED_CONDITION_CLEARED, now, None, observed)
            resolved.append(live[who]["attention_id"])
    return created, suppressed, obsoleted, resolved


def project(document, mission_id, destination, result, now, mint):
    """Project ``result`` (a classified observation of ``mission_id``)
    into presentation records for ``destination``. Only FRESH changes
    anything; every other freshness is reported and changes nothing."""
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    record.require_destination(destination, "destination")
    record.require_timestamp(now, "now")
    if not _require_fresh_of(result, mission_id):
        return ProjectionOutcome(result.freshness, result.problem, [], [], [], [])
    observed = result.observation
    conditions = sorted(observed.conditions, key=lambda c: (c.kind, c.key))
    live = _live_records(document, mission_id, destination)
    created, suppressed, obsoleted, resolved = _reconcile(
        document, observed, destination, live, conditions, now, mint)
    return ProjectionOutcome(record.FRESHNESS_FRESH, None, created, suppressed,
                             obsoleted, resolved)


# -- surfacing --------------------------------------------------------


@dataclass(frozen=True)
class SurfaceOutcome:
    """``surfaced`` is True only on an ok receipt; ``contradicted`` is
    True when a FRESH observation obsoleted or resolved the record
    instead; ``freshness`` and ``problem`` explain a non-transition."""

    surfaced: bool
    contradicted: bool
    freshness: str
    problem: Optional[str]
    message_ref: Optional[str]
    created: List[str]
    obsoleted: List[str]
    resolved: List[str]


def _require_record(document, attention_id):
    record.require_id(attention_id, record.ATTENTION_ID_PREFIX, "attention_id")
    value = document["attention"].get(attention_id)
    if value is None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "attention %s is not in the document" % attention_id)
    if is_terminal(value):
        record.fail(record.PROBLEM_ATTENTION_TERMINAL,
                    "attention %s is %s and never moves"
                    % (attention_id, value["presentation"]))
    return value


def _isolated(value):
    """A deep copy of plain JSON data with no shared nested container."""
    return json.loads(json.dumps(value))


def _present(presenter, destination, value):
    """Ask the seam once; refuse to trust it. Class name only on error."""
    if not isinstance(presenter, AttentionPresenter):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "presenter must implement AttentionPresenter; got %s"
                    % type(presenter).__name__)
    # Isolated copies (review F6): the presenter receives a deep copy of
    # the destination and of the record, so nothing it does to what it was
    # handed can reach the stored record.
    try:
        receipt = presenter.present(_isolated(destination), _isolated(value))
    except Exception as exc:  # noqa: BLE001 - the boundary; class name only
        return PresentationReceipt(False, None,
                                   "presenter raised %s" % type(exc).__name__)
    if not isinstance(receipt, PresentationReceipt):
        return PresentationReceipt(False, None,
                                   "presenter returned %s, not a PresentationReceipt"
                                   % type(receipt).__name__)
    try:
        return receipt.validate()
    except record.CoordinationError as exc:
        return PresentationReceipt(False, None, "presenter receipt malformed: %s"
                                   % exc.problem)


def surface(document, attention_id, result, presenter, now, mint):
    """PENDING → SURFACED under A-R1, or a truthful non-transition."""
    record.require_timestamp(now, "now")
    value = _require_record(document, attention_id)
    if value["presentation"] != record.PRESENTATION_PENDING:
        record.fail(record.PROBLEM_INVALID_TRANSITION,
                    "attention %s is %s; only PENDING is surfaced"
                    % (attention_id, value["presentation"]))
    if not _require_fresh_of(result, value["mission_id"]):
        return SurfaceOutcome(False, False, result.freshness, result.problem, None,
                              [], [], [])
    observed = result.observation
    who = (value["condition_kind"], value["condition_key"])
    matching = [c for c in observed.conditions if (c.kind, c.key) == who]
    reason = (record.CLOSED_CONDITION_CLEARED if not matching
              else _disagreement(value, observed, matching[0]))
    if reason is not None:
        created, _, obsoleted, resolved = _reconcile(
            document, observed, value["destination"], {who: value}, matching, now,
            mint)
        return SurfaceOutcome(False, True, record.FRESHNESS_FRESH,
                              "the observation contradicts the record (%s); it was"
                              " %s, not presented"
                              % (reason, "obsoleted" if obsoleted else "resolved"),
                              None, created, obsoleted, resolved)
    receipt = _present(presenter, value["destination"], value)
    if not receipt.ok:
        return SurfaceOutcome(False, False, record.FRESHNESS_FRESH, receipt.problem,
                              None, [], [], [])
    value["presentation"] = record.PRESENTATION_SURFACED
    value["surfaced_at"] = now
    value["surfaced_message_ref"] = receipt.message_ref
    value["surfaced_freshness"] = record.FRESHNESS_FRESH
    value["surfaced_observation_point"] = observation.observation_point(observed)
    validate_attention(value)
    return SurfaceOutcome(True, False, record.FRESHNESS_FRESH, None,
                          receipt.message_ref, [], [], [])


# -- acknowledgment ---------------------------------------------------


def acknowledge(document, attention_id, context, result, now):
    """Record the human's acknowledgment truthfully; resolves nothing,
    authorizes nothing, never moves a terminal record."""
    record.require_context(context)
    record.require_timestamp(now, "now")
    value = _require_record(document, attention_id)
    if value["presentation"] == record.PRESENTATION_ACKNOWLEDGED:
        record.fail(record.PROBLEM_INVALID_TRANSITION,
                    "attention %s is already acknowledged" % attention_id)
    fresh = _require_fresh_of(result, value["mission_id"])
    value["presentation"] = record.PRESENTATION_ACKNOWLEDGED
    value["acknowledged_at"] = now
    value["acknowledged_by"] = context.as_dict()
    value["acknowledged_freshness"] = result.freshness
    value["acknowledged_observation_cursor"] = (
        result.observation.state_cursor if fresh else None)
    value["acknowledged_observation_point"] = (
        observation.observation_point(result.observation) if fresh else None)
    return validate_attention(value)


# -- aggregation ------------------------------------------------------


def _order(value):
    return (value["priority"], value["created_at"], value["attention_id"])


def pending(document, destination):
    """Every non-terminal record for ``destination``: priority, then
    creation time, then id."""
    record.require_destination(destination, "destination")
    return sorted((value for value in document["attention"].values()
                   if value["destination"] == destination and not is_terminal(value)),
                  key=_order)


def aggregate(document, destination):
    """Deterministic counts of what needs attention at ``destination``."""
    live = pending(document, destination)
    by_kind = dict((kind, 0) for kind in record.ATTENTION_KINDS)
    by_presentation = dict((state, 0) for state in record.PRESENTATION_STATES)
    for value in live:
        by_kind[value["condition_kind"]] += 1
        by_presentation[value["presentation"]] += 1
    return {
        "destination": dict(destination),
        "by_kind": by_kind,
        "by_presentation": by_presentation,
        "missions": sorted(set(value["mission_id"] for value in live)),
        "authority": record.AUTHORITY_NONE,
    }
