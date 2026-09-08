"""The ONE read-only observation contract through which every canonical
Mission fact reaches coordination, and the pure freshness classification
that decides whether a fact may be acted on.

The surface is a single abstract method, ``observe(mission_id)``,
returning one ``ObservationOutcome``. There is no listing, no search,
no subscription, no cursor argument and no second method: nothing in
this package can enumerate Missions, so nothing in it is a poller, an
observation service or a scheduler. A caller injects the source; this
package defines the contract and consumes values. No implementation
bound to a live Mission store exists here (that binding is a later,
excluded task); tests supply hermetic fakes.

References carry provenance, not just identity (AMD-3). An observation
lists its Mission-local evidence and artifacts as reference RECORDS:
an ``EvidenceReference`` carries the evidence id, its kind, whether it
is ACCEPTED, the acceptance digest and the acceptance time (recorded
whole or not at all); an ``ArtifactReference`` carries the artifact id,
its role, and the validated receipt facts (``available``, content
digest, ``validated_at``) or an explicit null. A non-mapping receipt is
refused outright, never coerced. Presence proves Mission-locality
(``RECORDED``) and nothing more; ``require_reference_level`` is the ONE
place a citation is checked against what the observation positively
proves, and citing above that level is refused
(``coordination_reference_not_proven``): ACCEPTED needs the acceptance
provenance, VALIDATED needs a receipt that is present, available and
not validated in the observation's future. No code path infers
validity, proof or acceptance from presence. Chronology is part of
consistency: an acceptance time or receipt validation time later than
``observed_at`` makes the observation INCONSISTENT, in the same class
as a future-dated read. A condition's references must be the
observation's own records, byte for byte; a condition that carries the
same id with different validity facts makes the observation
INCONSISTENT.

An observation is VALIDATED ON RECEIPT and the source is never trusted:
a malformed value is UNAVAILABLE (nothing is known), a value that
contradicts itself (a condition citing a reference the observation
does not carry, a condition at any revision other than the observed
current revision, a repeated condition identity, an accepted narrative
claim) or that answers for a different Mission than was asked is
INCONSISTENT, and only a value that passes every check can be FRESH.

Freshness, operationally (``classify``):

- UNAVAILABLE: the source said so, raised, returned the wrong type, or
  returned a malformed value. Nothing is known.
- ABSENT: the source positively states the Mission does not exist.
  This package never enumerates Missions and so never asserts
  non-existence itself; it only relays the source's statement.
- STALE: the observation's ``(current_revision, state_cursor)`` pair is
  strictly behind the durable high-water mark this package already
  holds for the Mission (a binding, route decision, attention record or
  handoff recorded at a later point proves the observation is behind
  reality), or the observation is older than
  ``MAX_OBSERVATION_AGE_SECONDS``.
- INCONSISTENT: the observation is future-dated, answers for another
  Mission, contradicts itself, or reports the SAME point as a durably
  recorded ``observation_point`` with DIFFERENT content: a different
  proposal digest, condition-set digest, reference-set digest (which
  covers every reference's identity AND its validity provenance),
  authorization digest (null and non-null differ in either direction),
  or lifecycle state. Content regression at an identical cursor is
  evidence the source is wrong; this package says so rather than
  picking one.
- FRESH: none of the above.

The state cursor is a canonical decimal digit string compared
numerically (A-R3); it matches the Mission State sequence shape without
importing it. ``observation_point`` is the closed record of one read
that every durable record stores so all of the above is checkable from
durable facts. Every consumer treats anything but FRESH conservatively:
clarification or refusal with the reason, never a guess, never a
presentation, never a lane, never an authority statement.
"""

import abc
from dataclasses import dataclass
from typing import Optional, Tuple

from workflow_authority.digest import json_digest

from coordination import record

# Exact-value pinned in the bound-constant table.
# 900 s is the repository's established human-scale validity window.
MAX_OBSERVATION_AGE_SECONDS = 900
MAX_OBSERVED_CONDITIONS = 64
MAX_OBSERVED_REFERENCES = 512

EVIDENCE_REFERENCE_KEYS = (
    "evidence_id", "kind", "accepted", "acceptance_digest_sha256", "accepted_at",
)
ARTIFACT_RECEIPT_KEYS = ("available", "content_digest_sha256", "validated_at")
ARTIFACT_REFERENCE_KEYS = ("artifact_id", "role", "receipt")
CONDITION_KEYS = (
    "kind", "key", "revision", "detail", "evidence_refs", "artifact_refs",
)
OBSERVATION_KEYS = (
    "mission_id", "current_revision", "proposal_digest_sha256",
    "lifecycle_state", "authorization_digest_sha256", "state_cursor",
    "repository_url", "conditions", "evidence_refs", "artifact_refs",
    "observed_at", "source",
)
# The closed durable record of one read (A-R3 + AMD-3).
OBSERVATION_POINT_KEYS = (
    "revision", "cursor", "proposal_digest_sha256",
    "authorization_digest_sha256", "lifecycle_state",
    "condition_set_digest_sha256", "reference_set_digest_sha256",
)


# -- reference records ------------------------------------------------


@dataclass(frozen=True)
class EvidenceReference:
    """One Mission-local evidence record as the source proves it."""

    evidence_id: str
    kind: str
    accepted: bool
    acceptance_digest_sha256: Optional[str]
    accepted_at: Optional[int]

    def validate(self, location="evidence_ref"):
        record.require_id(self.evidence_id, record.EVIDENCE_ID_PREFIX,
                          location + ".evidence_id")
        record.require_member(self.kind, record.EVIDENCE_KINDS, location + ".kind")
        record.require_bool(self.accepted, location + ".accepted")
        record.require_optional_hex(self.acceptance_digest_sha256,
                                    location + ".acceptance_digest_sha256", 64)
        record.require_optional_timestamp(self.accepted_at, location + ".accepted_at")
        if self.accepted != (self.acceptance_digest_sha256 is not None) or (
            self.accepted != (self.accepted_at is not None)
        ):
            record.fail(record.PROBLEM_PROVENANCE,
                        "%s records acceptance as (accepted, acceptance digest,"
                        " accepted_at) together or not at all" % location)
        if self.accepted and self.kind not in record.SATISFYING_EVIDENCE_KINDS:
            record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                        "%s claims accepted %s evidence; that kind can never be"
                        " accepted" % (location, self.kind))
        return self

    def as_dict(self):
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "accepted": self.accepted,
            "acceptance_digest_sha256": self.acceptance_digest_sha256,
            "accepted_at": self.accepted_at,
        }

    @property
    def reference_id(self):
        return self.evidence_id

    def chronology_problem(self, observed_at):
        """Why this record cannot belong to a read at ``observed_at``, or
        None: acceptance provenance never postdates the observation."""
        if self.accepted and self.accepted_at > observed_at:
            return ("accepted_at %d is later than observed_at %d"
                    % (self.accepted_at, observed_at))
        return None

    def proves(self, level, observed_at):
        """Whether the source positively proves ``level`` for this record
        as of a read at ``observed_at``. Presence proves RECORDED only."""
        if level == record.REFERENCE_LEVEL_RECORDED:
            return True
        return level == record.REFERENCE_LEVEL_ACCEPTED and self.accepted and (
            self.chronology_problem(observed_at) is None)


def evidence_reference_from_dict(value, location="evidence_ref"):
    record.require_dict(value, location)
    record.require_closed_keys(value, EVIDENCE_REFERENCE_KEYS, location)
    return EvidenceReference(
        evidence_id=value["evidence_id"], kind=value["kind"],
        accepted=value["accepted"],
        acceptance_digest_sha256=value["acceptance_digest_sha256"],
        accepted_at=value["accepted_at"],
    ).validate(location)


def validate_receipt(value, location):
    """The validated receipt contract facts of an artifact, closed."""
    record.require_dict(value, location)
    record.require_closed_keys(value, ARTIFACT_RECEIPT_KEYS, location)
    record.require_bool(value["available"], location + ".available")
    record.require_hex(value["content_digest_sha256"],
                       location + ".content_digest_sha256", 64)
    record.require_timestamp(value["validated_at"], location + ".validated_at")
    return value


@dataclass(frozen=True)
class ArtifactReference:
    """One Mission-local artifact record as the source proves it;
    ``receipt`` is the validated receipt facts or an explicit null."""

    artifact_id: str
    role: str
    receipt: Optional[dict]

    def validate(self, location="artifact_ref"):
        record.require_id(self.artifact_id, record.ARTIFACT_ID_PREFIX,
                          location + ".artifact_id")
        record.require_member(self.role, record.ARTIFACT_ROLES, location + ".role")
        if self.receipt is not None:
            validate_receipt(self.receipt, location + ".receipt")
        return self

    def as_dict(self):
        return {
            "artifact_id": self.artifact_id,
            "role": self.role,
            "receipt": None if self.receipt is None else dict(
                (key, self.receipt[key]) for key in ARTIFACT_RECEIPT_KEYS),
        }

    @property
    def reference_id(self):
        return self.artifact_id

    def chronology_problem(self, observed_at):
        """Why this record cannot belong to a read at ``observed_at``, or
        None: a receipt's validation never postdates the observation."""
        if self.receipt is not None and self.receipt["validated_at"] > observed_at:
            return ("receipt validated_at %d is later than observed_at %d"
                    % (self.receipt["validated_at"], observed_at))
        return None

    def proves(self, level, observed_at):
        """Whether the source positively proves ``level`` for this record
        as of a read at ``observed_at``: VALIDATED needs a receipt that is
        present, available, and not validated in the observation's
        future. Presence proves RECORDED only."""
        if level == record.REFERENCE_LEVEL_RECORDED:
            return True
        return level == record.REFERENCE_LEVEL_VALIDATED and (
            self.receipt is not None and self.receipt["available"] is True
            and self.chronology_problem(observed_at) is None)


def artifact_reference_from_dict(value, location="artifact_ref"):
    record.require_dict(value, location)
    record.require_closed_keys(value, ARTIFACT_REFERENCE_KEYS, location)
    raw = value["receipt"]
    # Typed BEFORE copied: a non-mapping receipt is refused outright, never
    # coerced into one (a JSON list of pairs must not become a receipt).
    if raw is not None:
        record.require_dict(raw, location + ".receipt")
    return ArtifactReference(
        artifact_id=value["artifact_id"], role=value["role"],
        receipt=None if raw is None else dict(raw),
    ).validate(location)


def _require_reference_tuple(value, cls, location, max_items):
    """A tuple of validated ``cls`` records, sorted by id, duplicate-free
    (deterministic form; an unsorted tuple is refused, never repaired)."""
    if not isinstance(value, tuple):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s must be a tuple, not %s" % (location, type(value).__name__))
    if len(value) > max_items:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s holds %d entries; the hard bound is %d and the record"
                    " is refused, not truncated" % (location, len(value), max_items))
    ids = []
    for index, entry in enumerate(value):
        sub = "%s[%d]" % (location, index)
        if not isinstance(entry, cls):
            record.fail(record.PROBLEM_BAD_TYPE,
                        "%s must be a %s, not %s"
                        % (sub, cls.__name__, type(entry).__name__))
        entry.validate(sub)
        ids.append(entry.reference_id)
    if len(set(ids)) != len(ids):
        record.fail(record.PROBLEM_BAD_VALUE, "%s carries a duplicate id" % location)
    if ids != sorted(ids):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must be sorted by id (deterministic form)" % location)
    return value


def _references_from_list(value, from_dict, location):
    if not isinstance(value, list):
        record.fail(record.PROBLEM_BAD_TYPE, "%s must be a list" % location)
    return tuple(from_dict(entry, "%s[%d]" % (location, index))
                 for index, entry in enumerate(value))


# -- conditions -------------------------------------------------------


@dataclass(frozen=True)
class ObservedCondition:
    """One condition the Mission currently carries, as observed. Its
    references are the observation's own reference records."""

    kind: str
    key: str
    revision: int
    detail: str
    evidence_refs: Tuple[EvidenceReference, ...]
    artifact_refs: Tuple[ArtifactReference, ...]

    def validate(self, location="condition"):
        record.require_member(self.kind, record.ATTENTION_KINDS, location + ".kind")
        record.require_key(self.key, location + ".key")
        record.require_int(self.revision, location + ".revision", minimum=1)
        record.require_str(self.detail, location + ".detail",
                           record.MAX_CONDITION_DETAIL_CHARS)
        _require_reference_tuple(self.evidence_refs, EvidenceReference,
                                 location + ".evidence_refs",
                                 record.MAX_REFERENCE_LIST)
        _require_reference_tuple(self.artifact_refs, ArtifactReference,
                                 location + ".artifact_refs",
                                 record.MAX_REFERENCE_LIST)
        return self

    def as_dict(self):
        return {
            "kind": self.kind,
            "key": self.key,
            "revision": self.revision,
            "detail": self.detail,
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
            "artifact_refs": [ref.as_dict() for ref in self.artifact_refs],
        }


def condition_from_dict(value, location="condition"):
    record.require_dict(value, location)
    record.require_closed_keys(value, CONDITION_KEYS, location)
    return ObservedCondition(
        kind=value["kind"], key=value["key"], revision=value["revision"],
        detail=value["detail"],
        evidence_refs=_references_from_list(
            value["evidence_refs"], evidence_reference_from_dict,
            location + ".evidence_refs"),
        artifact_refs=_references_from_list(
            value["artifact_refs"], artifact_reference_from_dict,
            location + ".artifact_refs"),
    ).validate(location)


def condition_digest(condition):
    """Content identity of one validated condition (references included,
    with their provenance)."""
    return json_digest(condition.validate().as_dict())


# -- the observation --------------------------------------------------


@dataclass(frozen=True)
class MissionObservation:
    """One read of one Mission: exact Mission and proposal-revision
    reference, state cursor, provenance, conditions, and the
    Mission-local evidence / artifact reference records."""

    mission_id: str
    current_revision: int
    proposal_digest_sha256: str
    lifecycle_state: str
    authorization_digest_sha256: Optional[str]
    state_cursor: str
    repository_url: Optional[str]
    conditions: Tuple[ObservedCondition, ...]
    evidence_refs: Tuple[EvidenceReference, ...]
    artifact_refs: Tuple[ArtifactReference, ...]
    observed_at: int
    source: str

    def validate(self, location="observation"):
        record.require_id(self.mission_id, record.MISSION_ID_PREFIX,
                          location + ".mission_id")
        record.require_int(self.current_revision, location + ".current_revision",
                           minimum=1)
        record.require_hex(self.proposal_digest_sha256,
                           location + ".proposal_digest_sha256", 64)
        record.require_member(self.lifecycle_state, record.LIFECYCLE_STATES,
                              location + ".lifecycle_state")
        record.require_optional_hex(self.authorization_digest_sha256,
                                    location + ".authorization_digest_sha256", 64)
        record.require_cursor(self.state_cursor, location + ".state_cursor")
        record.require_optional_repository_url(self.repository_url,
                                               location + ".repository_url")
        if not isinstance(self.conditions, tuple):
            record.fail(record.PROBLEM_BAD_TYPE,
                        "%s.conditions must be a tuple, not %s"
                        % (location, type(self.conditions).__name__))
        if len(self.conditions) > MAX_OBSERVED_CONDITIONS:
            record.fail(record.PROBLEM_TOO_LARGE,
                        "%s.conditions holds %d entries; the hard bound is %d"
                        % (location, len(self.conditions), MAX_OBSERVED_CONDITIONS))
        _require_reference_tuple(self.evidence_refs, EvidenceReference,
                                 location + ".evidence_refs",
                                 MAX_OBSERVED_REFERENCES)
        _require_reference_tuple(self.artifact_refs, ArtifactReference,
                                 location + ".artifact_refs",
                                 MAX_OBSERVED_REFERENCES)
        record.require_timestamp(self.observed_at, location + ".observed_at")
        record.require_str(self.source, location + ".source",
                           record.MAX_OBSERVATION_SOURCE_CHARS)
        known = dict((ref.reference_id, ref) for ref in
                     self.evidence_refs + self.artifact_refs)
        # Chronology: no acceptance or receipt validation may postdate the
        # read that reports it (the same class as a future-dated read).
        for ref in self.evidence_refs + self.artifact_refs:
            problem = ref.chronology_problem(self.observed_at)
            if problem is not None:
                record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                            "%s reference %s: %s" % (location, ref.reference_id,
                                                     problem))
        identities = set()
        for index, condition in enumerate(self.conditions):
            sub = "%s.conditions[%d]" % (location, index)
            if not isinstance(condition, ObservedCondition):
                record.fail(record.PROBLEM_BAD_TYPE,
                            "%s must be an ObservedCondition" % sub)
            condition.validate(sub)
            # Exact condition/revision compatibility (review F1): a
            # condition behind or ahead of the Mission's current revision
            # is the source contradicting itself, never a current fact.
            if condition.revision != self.current_revision:
                record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                            "%s is at revision %d but the mission is observed at"
                            " revision %d; a condition is current only at the"
                            " current revision" % (sub, condition.revision,
                                                   self.current_revision))
            identity = (condition.kind, condition.key)
            if identity in identities:
                record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                            "%s repeats condition %s/%s; a condition identity"
                            " appears at most once" % (sub, condition.kind,
                                                       condition.key))
            identities.add(identity)
            for cited in condition.evidence_refs + condition.artifact_refs:
                own = known.get(cited.reference_id)
                if own is None:
                    record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                                "%s cites %s, which the observation does not"
                                " carry as Mission-local"
                                % (sub, cited.reference_id))
                if own != cited:
                    record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                                "%s cites %s with different provenance than the"
                                " observation's own record of it"
                                % (sub, cited.reference_id))
        return self

    def as_dict(self):
        return {
            "mission_id": self.mission_id,
            "current_revision": self.current_revision,
            "proposal_digest_sha256": self.proposal_digest_sha256,
            "lifecycle_state": self.lifecycle_state,
            "authorization_digest_sha256": self.authorization_digest_sha256,
            "state_cursor": self.state_cursor,
            "repository_url": self.repository_url,
            "conditions": [condition.as_dict() for condition in self.conditions],
            "evidence_refs": [ref.as_dict() for ref in self.evidence_refs],
            "artifact_refs": [ref.as_dict() for ref in self.artifact_refs],
            "observed_at": self.observed_at,
            "source": self.source,
        }

    def reference(self, reference_id):
        """The observation's own record for ``reference_id`` or None."""
        for ref in self.evidence_refs + self.artifact_refs:
            if ref.reference_id == reference_id:
                return ref
        return None


def observation_from_dict(value, location="observation"):
    record.require_dict(value, location)
    record.require_closed_keys(value, OBSERVATION_KEYS, location)
    if not isinstance(value["conditions"], list):
        record.fail(record.PROBLEM_BAD_TYPE, "%s.conditions must be a list" % location)
    conditions = tuple(
        condition_from_dict(entry, "%s.conditions[%d]" % (location, index))
        for index, entry in enumerate(value["conditions"])
    )
    return MissionObservation(
        mission_id=value["mission_id"],
        current_revision=value["current_revision"],
        proposal_digest_sha256=value["proposal_digest_sha256"],
        lifecycle_state=value["lifecycle_state"],
        authorization_digest_sha256=value["authorization_digest_sha256"],
        state_cursor=value["state_cursor"],
        repository_url=value["repository_url"],
        conditions=conditions,
        evidence_refs=_references_from_list(
            value["evidence_refs"], evidence_reference_from_dict,
            location + ".evidence_refs"),
        artifact_refs=_references_from_list(
            value["artifact_refs"], artifact_reference_from_dict,
            location + ".artifact_refs"),
        observed_at=value["observed_at"],
        source=value["source"],
    ).validate(location)


def condition_set_digest(observation):
    """Content identity of the whole observed condition set, order-free."""
    observation.validate()
    return json_digest(sorted(condition_digest(c) for c in observation.conditions))


def reference_set_digest(observation):
    """Content identity of every Mission-local reference WITH its validity
    provenance, order-free (AMD-3)."""
    observation.validate()
    return json_digest(sorted(
        json_digest(ref.as_dict())
        for ref in observation.evidence_refs + observation.artifact_refs))


def observation_point(observation):
    """The closed durable record of one read: the point and every content
    digest a later read at the same point must reproduce."""
    observation.validate()
    return {
        "revision": observation.current_revision,
        "cursor": observation.state_cursor,
        "proposal_digest_sha256": observation.proposal_digest_sha256,
        "authorization_digest_sha256": observation.authorization_digest_sha256,
        "lifecycle_state": observation.lifecycle_state,
        "condition_set_digest_sha256": condition_set_digest(observation),
        "reference_set_digest_sha256": reference_set_digest(observation),
    }


def validate_observation_point(value, location="observation_point"):
    record.require_dict(value, location)
    record.require_closed_keys(value, OBSERVATION_POINT_KEYS, location)
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_cursor(value["cursor"], location + ".cursor")
    record.require_hex(value["proposal_digest_sha256"],
                       location + ".proposal_digest_sha256", 64)
    record.require_optional_hex(value["authorization_digest_sha256"],
                                location + ".authorization_digest_sha256", 64)
    record.require_member(value["lifecycle_state"], record.LIFECYCLE_STATES,
                          location + ".lifecycle_state")
    record.require_hex(value["condition_set_digest_sha256"],
                       location + ".condition_set_digest_sha256", 64)
    record.require_hex(value["reference_set_digest_sha256"],
                       location + ".reference_set_digest_sha256", 64)
    return value


def require_reference_level(observation, reference_id, level, location):
    """The observation's own record for ``reference_id``, provided the
    observation positively proves ``level`` for it. The ONE place a
    citation's validity is checked; presence proves RECORDED only."""
    observation.validate()
    record.require_member(level, record.REFERENCE_LEVELS, location + ".level")
    if record.id_problem(reference_id, record.EVIDENCE_ID_PREFIX) is None:
        allowed = record.EVIDENCE_REFERENCE_LEVELS
    elif record.id_problem(reference_id, record.ARTIFACT_ID_PREFIX) is None:
        allowed = record.ARTIFACT_REFERENCE_LEVELS
    else:
        record.fail(record.PROBLEM_ID_GRAMMAR,
                    "%s must be an evidence (%s-) or artifact (%s-) id; got %r"
                    % (location, record.EVIDENCE_ID_PREFIX,
                       record.ARTIFACT_ID_PREFIX, reference_id))
    if level not in allowed:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s: level %s does not apply to %s" % (location, level,
                                                           reference_id))
    own = observation.reference(reference_id)
    if own is None:
        record.fail(record.PROBLEM_REFERENCE_NOT_MISSION_LOCAL,
                    "%s cites %s, which mission %s's observation does not carry"
                    % (location, reference_id, observation.mission_id))
    if not own.proves(level, observation.observed_at):
        record.fail(record.PROBLEM_REFERENCE_NOT_PROVEN,
                    "%s cites %s as %s but the observation proves only that it"
                    " is recorded (%s requires %s); presence never upgrades"
                    " status"
                    % (location, reference_id, level, level,
                       "accepted provenance no later than the read"
                       if level == record.REFERENCE_LEVEL_ACCEPTED else
                       "a receipt that is present, available and validated no"
                       " later than the read"))
    return own


# -- outcomes and the source contract ----------------------------------


@dataclass(frozen=True)
class ObservationOutcome:
    """The source's answer: OBSERVED with a value, ABSENT, or UNAVAILABLE
    with a bounded problem."""

    status: str
    observation: Optional[MissionObservation] = None
    problem: Optional[str] = None

    @classmethod
    def observed(cls, observation):
        return cls(record.OBSERVATION_OBSERVED, observation, None)

    @classmethod
    def absent(cls):
        return cls(record.OBSERVATION_ABSENT, None, None)

    @classmethod
    def unavailable(cls, problem):
        return cls(record.OBSERVATION_UNAVAILABLE, None, problem)

    def validate(self, location="outcome"):
        """Shape only; the observation value itself is validated by
        ``classify`` so a malformed value classifies as UNAVAILABLE."""
        record.require_member(self.status, record.OBSERVATION_STATUSES,
                              location + ".status")
        has_value = self.observation is not None
        if has_value != (self.status == record.OBSERVATION_OBSERVED):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries an observation exactly when OBSERVED" % location)
        if has_value and not isinstance(self.observation, MissionObservation):
            record.fail(record.PROBLEM_BAD_TYPE,
                        "%s.observation must be a MissionObservation" % location)
        has_problem = self.problem is not None
        if has_problem != (self.status == record.OBSERVATION_UNAVAILABLE):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries a problem exactly when UNAVAILABLE" % location)
        if has_problem:
            record.require_str(self.problem, location + ".problem",
                               record.MAX_DETAIL_CHARS)
        return self


class MissionObservationSource(abc.ABC):
    """The provider-neutral read-only observation contract."""

    @abc.abstractmethod
    def observe(self, mission_id):
        """One read of one Mission; returns an ObservationOutcome. Never
        mutates anything and may answer UNAVAILABLE."""


def observe_safely(source, mission_id):
    """Ask the injected source for one Mission, refusing to trust it: an
    exception becomes UNAVAILABLE carrying the class name only (never the
    message), and a value of the wrong type becomes UNAVAILABLE."""
    if not isinstance(source, MissionObservationSource):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "observation source must implement MissionObservationSource;"
                    " got %s" % type(source).__name__)
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    try:
        outcome = source.observe(mission_id)
    except Exception as exc:  # noqa: BLE001 - the boundary; class name only
        return ObservationOutcome.unavailable(
            "observation source raised %s" % type(exc).__name__)
    if not isinstance(outcome, ObservationOutcome):
        return ObservationOutcome.unavailable(
            "observation source returned %s, not an ObservationOutcome"
            % type(outcome).__name__)
    return outcome


# -- freshness --------------------------------------------------------


@dataclass(frozen=True)
class HighWaterMark:
    """The durable floor this package already holds for one Mission: the
    maximum recorded (revision, cursor) pair and, when the content at
    that exact point was recorded, its ``observation_point``."""

    revision: int
    cursor: str
    point: Optional[dict]

    def validate(self, location="high_water_mark"):
        record.require_int(self.revision, location + ".revision", minimum=1)
        record.require_cursor(self.cursor, location + ".cursor")
        if self.point is not None:
            validate_observation_point(self.point, location + ".point")
            if (self.point["revision"], self.point["cursor"]) != (
                self.revision, self.cursor
            ):
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s.point records revision %d cursor %s, not the"
                            " mark's revision %d cursor %s"
                            % (location, self.point["revision"],
                               self.point["cursor"], self.revision, self.cursor))
        return self

    def pair(self):
        return (self.revision, record.cursor_value(self.cursor))


@dataclass(frozen=True)
class FreshnessResult:
    """``freshness`` is one of FRESHNESS_STATES; ``observation`` is the
    validated value when one exists (FRESH, STALE and some INCONSISTENT
    cases), and ``problem`` explains anything that is not FRESH."""

    freshness: str
    observation: Optional[MissionObservation]
    problem: Optional[str]


def _result(freshness, observation, problem):
    return FreshnessResult(freshness, observation, problem)


# What a read at an identical point must reproduce, with the word used
# to name a disagreement.
_POINT_CONTENT = (
    ("proposal_digest_sha256", "proposal digest"),
    ("authorization_digest_sha256", "authorization digest"),
    ("lifecycle_state", "lifecycle state"),
    ("condition_set_digest_sha256", "condition set"),
    ("reference_set_digest_sha256", "reference set or reference provenance"),
)


def _same_point_disagreement(observation, floor):
    """The first content fact a read at the floor's exact point fails to
    reproduce, or None."""
    current = observation_point(observation)
    for key, name in _POINT_CONTENT:
        if current[key] != floor.point[key]:
            return name
    return None


def classify(outcome, now, mission_id, floor=None):
    """The freshness of ``outcome`` for ``mission_id`` at ``now`` against
    the durable ``floor`` (a HighWaterMark or None). Pure."""
    record.require_timestamp(now, "now")
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    if floor is not None:
        floor.validate()
    if not isinstance(outcome, ObservationOutcome):
        return _result(record.FRESHNESS_UNAVAILABLE, None,
                       "outcome is %s, not an ObservationOutcome"
                       % type(outcome).__name__)
    try:
        outcome.validate()
    except record.CoordinationError as exc:
        return _result(record.FRESHNESS_UNAVAILABLE, None,
                       "%s: %s" % (exc.problem, exc))
    if outcome.status == record.OBSERVATION_UNAVAILABLE:
        return _result(record.FRESHNESS_UNAVAILABLE, None, outcome.problem)
    if outcome.status == record.OBSERVATION_ABSENT:
        return _result(record.FRESHNESS_ABSENT, None,
                       "the source states mission %s does not exist" % mission_id)
    observation = outcome.observation
    try:
        observation.validate()
    except record.CoordinationError as exc:
        if exc.problem == record.PROBLEM_OBSERVATION_INCONSISTENT:
            return _result(record.FRESHNESS_INCONSISTENT, None,
                           "%s: %s" % (exc.problem, exc))
        return _result(record.FRESHNESS_UNAVAILABLE, None,
                       "%s: %s" % (exc.problem, exc))
    if observation.mission_id != mission_id:
        return _result(record.FRESHNESS_INCONSISTENT, None,
                       "asked for mission %s but the source answered for %s"
                       % (mission_id, observation.mission_id))
    if observation.observed_at > now:
        return _result(record.FRESHNESS_INCONSISTENT, observation,
                       "observed_at %d is later than now %d"
                       % (observation.observed_at, now))
    if floor is not None:
        point = (observation.current_revision,
                 record.cursor_value(observation.state_cursor))
        if point < floor.pair():
            return _result(record.FRESHNESS_STALE, observation,
                           "observation at revision %d cursor %s is behind the"
                           " durable point revision %d cursor %s"
                           % (point[0], observation.state_cursor,
                              floor.revision, floor.cursor))
        if point == floor.pair() and floor.point is not None:
            disagreement = _same_point_disagreement(observation, floor)
            if disagreement is not None:
                return _result(record.FRESHNESS_INCONSISTENT, observation,
                               "same point revision %d cursor %s but a different"
                               " %s than durably recorded; the source"
                               " equivocates" % (floor.revision, floor.cursor,
                                                 disagreement))
    if now - observation.observed_at > MAX_OBSERVATION_AGE_SECONDS:
        return _result(record.FRESHNESS_STALE, observation,
                       "observation is %d seconds old; the bound is %d"
                       % (now - observation.observed_at,
                          MAX_OBSERVATION_AGE_SECONDS))
    return _result(record.FRESHNESS_FRESH, observation, None)
