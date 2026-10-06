"""The canonical Mission observation source for coordination (Task 8,
slice S-II): ``coordination.observation.MissionObservationSource`` over
ONE ``MissionService.snapshot`` read.

What one observation says, and from where:

- ``state_cursor`` is the snapshot's ``durable_cursor`` (the canonical
  decimal that strictly increases on every durable Mission change and on
  nothing else; see ``MissionStateOperations.durable_cursor``);
- ``observed_at`` is the snapshot's ``evaluated_at``: the ONE pinned
  evaluation point every time-dependent fact was evaluated at;
- ``lifecycle_state``, ``authorization_digest_sha256``, ``conditions``,
  ``evidence_refs`` and ``artifact_refs`` are pure functions of the
  DURABLE document as loaded once for that snapshot — never of the clock
  and never of another Mission's cursor — so a later read at the same
  ``(revision, cursor)`` reproduces them exactly and coordination's
  same-point check never sees this source equivocate.

What is deliberately NOT a condition here, and why (Supervisor S-II
constraint): authorization liveness (expiry is clock-driven), readiness
staleness (an age test against ``now``), external source reports and a
dependency Mission's own progress (another Mission's cursor). Each of
those can change without any durable change to THIS Mission; carrying
it in the observation's content would either fabricate progress or
flip the condition set at an unchanged point. They are all visible, as
of ``evaluated_at``, in the canonical status (``mission_control.status``).
The ``authorization_digest_sha256`` is therefore the ISSUED-ONLY digest
of the authorization the Mission holds for its current revision: a
durable, issued fact, not a claim of live authority (coordination never
validates Mission authority; the Mission validator does, at use).

Outcomes: an unknown Mission (or a store that does not exist yet) is
``absent()``; an unreadable or refused store is ``unavailable(<class>)``,
never absent. Nothing here loads twice, writes, locks or mints.
"""

import hashlib

from coordination import observation as coordination_observation
from coordination import record as coordination_record
from mission import authorization as mission_authorization
from mission import record as mission_record
from mission import state as mission_state
from mission import store as mission_store

SOURCE = "mission_control.observation_adapter"
# Explicit reasons for an ``unavailable`` outcome this adapter itself
# decides (a valid Mission the neutral observation cannot represent).
REASON_TOO_MANY_CONDITIONS = (
    "the Mission holds %d durable conditions; the observation contract"
    " carries at most %d, so no observation is emitted (the canonical"
    " status carries them all)"
)
REASON_CONSTRUCTION = "observation construction failed: %s"
# Condition-key namespaces (coordination identity = (kind, key)).
KEY_NAMESPACE_BLOCKER = "blocker."
KEY_NAMESPACE_DEPENDENCY = "dependency."
KEY_HASHED_MARK = "h."
_KEY_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")

# Durable progress -> coordination lifecycle (the Mission Core's own
# vocabulary, re-declared by coordination); NOT_STARTED falls back to the
# Mission record's state.
_PROGRESS_LIFECYCLE = {
    mission_state.PROGRESS_IN_PROGRESS: coordination_record.LIFECYCLE_RUNNING,
    mission_state.PROGRESS_BLOCKED: coordination_record.LIFECYCLE_BLOCKED,
    mission_state.PROGRESS_COMPLETED: coordination_record.LIFECYCLE_COMPLETED,
    mission_state.PROGRESS_CLOSED_UNSUCCESSFUL: coordination_record.LIFECYCLE_CLOSED,
    mission_state.PROGRESS_ABANDONED: coordination_record.LIFECYCLE_CANCELLED,
}

CONDITION_KEY_DECISION = "decision"
CONDITION_KEY_COMPLETION = "completion"


def bounded_key(namespace, raw):
    """A deterministic, bounded, collision-free coordination key for one
    Mission key under a kind namespace. Two disjoint forms:

    - RAW: ``<namespace><raw>`` when the result fits the coordination
      bound, ``raw`` is in the key alphabet and does not begin with the
      hashed mark;
    - HASHED: ``<namespace>h.<sha256(raw)>`` otherwise.

    The raw form never begins with ``h.`` after the namespace and the
    hashed form always does, so the two forms cannot coincide; two raw
    keys map to distinct raw forms trivially, and two hashed forms
    coincide only on a sha256 collision. Different namespaces never
    coincide (``blocker.dependency.x`` vs ``dependency.x``)."""
    fits = (
        len(namespace) + len(raw) <= coordination_record.MAX_KEY_CHARS
        and raw and all(ch in _KEY_ALPHABET for ch in raw)
        and not raw.startswith(KEY_HASHED_MARK)
    )
    if fits:
        return namespace + raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return namespace + KEY_HASHED_MARK + digest


def lifecycle_of(record, state):
    """The durable lifecycle: the Task 5 progress once it has started,
    otherwise the Mission record's decision state."""
    if state is not None:
        mapped = _PROGRESS_LIFECYCLE.get(state["progress"])
        if mapped is not None:
            return mapped
    return record["state"]


def issued_authorization_digest(snapshot):
    """The ISSUED-ONLY digest of the most recently issued authorization
    bound to the current revision, or None. Revocation is outside the
    digest by the core's own definition; liveness is not a durable fact
    and is not consulted here."""
    record = snapshot["record"]["record"]
    digest = None
    for authorization in snapshot["record"]["authorizations"]:
        if authorization["revision"] == record["current_revision"]:
            digest = authorization["authorization_digest_sha256"]
    return digest


def _detail(text):
    limit = coordination_record.MAX_CONDITION_DETAIL_CHARS
    return text if len(text) <= limit else text[:limit]


def durable_conditions(snapshot):
    """Conditions that are pure functions of the durable document: active
    blockers and unresolved bound dependencies (BLOCKED), a pending human
    decision (NEEDS_HUMAN), a completed Mission (RESULT_READY). Keys are
    kind-namespaced and bounded (``bounded_key``); a blocker's identity
    is its own record id joined to its key, so two active blockers that
    share a key are two conditions, each with its own description, and
    nothing is ever merged or dropped: the returned tuple holds one
    condition per Mission fact, in identity order, and its length is the
    un-deduplicated count the contract bound is applied to."""
    record = snapshot["record"]["record"]
    state = snapshot["state"]["record"]
    revision = record["current_revision"]
    conditions = []

    def add(kind, key, detail):
        conditions.append(coordination_observation.ObservedCondition(
            kind=kind, key=key, revision=revision, detail=_detail(detail),
            evidence_refs=(), artifact_refs=(),
        ))

    if state is not None:
        for blocker in mission_state.active_blockers(state):
            add(coordination_record.ATTENTION_BLOCKED,
                bounded_key(KEY_NAMESPACE_BLOCKER,
                            "%s.%s" % (blocker["key"], blocker["blocker_id"])),
                "blocker %s (%s, %s) open since %d: %s" % (
                    blocker["key"], blocker["blocker_id"], blocker["severity"],
                    blocker["opened_at"], blocker["description"]))
        for dependency in state["dependencies"]:
            if dependency["resolution"] is None:
                add(coordination_record.ATTENTION_BLOCKED,
                    bounded_key(KEY_NAMESPACE_DEPENDENCY, dependency["key"]),
                    "dependency slot %s bound and unresolved (the dependency"
                    " Mission's own progress is not part of this record)"
                    % dependency["key"])
    if record["state"] == mission_record.STATE_AWAITING_DECISION:
        add(coordination_record.ATTENTION_NEEDS_HUMAN, CONDITION_KEY_DECISION,
            "revision %d awaits a human decision" % revision)
    if state is not None and state["progress"] == mission_state.PROGRESS_COMPLETED:
        add(coordination_record.ATTENTION_RESULT_READY, CONDITION_KEY_COMPLETION,
            "the Mission's progress is COMPLETED at revision %d" % revision)
    return tuple(sorted(conditions, key=lambda c: (c.kind, c.key)))


def evidence_references(state):
    """Every Mission-local evidence record as the core proves it:
    accepted only when an acceptance is recorded and not invalidated."""
    if state is None:
        return ()
    references = []
    for evidence in sorted(state["evidence"], key=lambda e: e["evidence_id"]):
        acceptance = evidence["acceptance"]
        accepted = acceptance is not None and evidence["invalidation"] is None
        references.append(coordination_observation.EvidenceReference(
            evidence_id=evidence["evidence_id"], kind=evidence["kind"],
            accepted=accepted,
            acceptance_digest_sha256=(
                acceptance["content_digest_sha256"] if accepted else None),
            accepted_at=acceptance["accepted_at"] if accepted else None,
        ))
    return tuple(references)


def artifact_references(state):
    """Every Mission-local artifact record; the receipt facts are carried
    only for an artifact the delivery layer ATTESTED (the core's
    receipt-attestation marker), read from the artifact's own recorded
    availability, content digest and recording time."""
    if state is None:
        return ()
    references = []
    for artifact in sorted(state["artifacts"], key=lambda a: a["artifact_id"]):
        receipt = None
        if mission_state.receipt_attestation_of(artifact) is not None:
            receipt = {
                "available": artifact["available"],
                "content_digest_sha256": artifact["content_digest_sha256"],
                "validated_at": artifact["recorded_at"],
            }
        references.append(coordination_observation.ArtifactReference(
            artifact_id=artifact["artifact_id"], role=artifact["role"],
            receipt=receipt,
        ))
    return tuple(references)


def observation_from_snapshot(snapshot, source=SOURCE):
    """The validated ``MissionObservation`` of one snapshot. Pure."""
    record = snapshot["record"]["record"]
    state = snapshot["state"]["record"]
    current = record["revisions"][-1]
    observation = coordination_observation.MissionObservation(
        mission_id=record["mission_id"],
        current_revision=record["current_revision"],
        proposal_digest_sha256=current["proposal_digest_sha256"],
        lifecycle_state=lifecycle_of(record, state),
        authorization_digest_sha256=issued_authorization_digest(snapshot),
        state_cursor=snapshot["durable_cursor"],
        repository_url=current["proposal"]["repository_url"],
        conditions=durable_conditions(snapshot),
        evidence_refs=evidence_references(state),
        artifact_refs=artifact_references(state),
        observed_at=snapshot["evaluated_at"],
        source=source,
    )
    return observation.validate()


class MissionSnapshotSource(coordination_observation.MissionObservationSource):
    """The source coordination consults: one snapshot per ``observe``.
    The snapshot reads the Mission store through the store's OWN
    ``read()`` (one validated document read): a genuinely absent store or
    an unknown id is ``absent``; any access or read error is a typed
    ``MissionStoreError`` from the core and is ``unavailable`` here, so
    the public call is correct with no extra argument."""

    def __init__(self, service, source=SOURCE):
        self._service = service
        self._source = source

    def observe(self, mission_id):
        try:
            snapshot = self._service.snapshot(mission_id)
        except mission_record.MissionError as exc:
            if exc.problem == mission_authorization.PROBLEM_UNKNOWN_MISSION:
                return coordination_observation.ObservationOutcome.absent()
            return coordination_observation.ObservationOutcome.unavailable(
                type(exc).__name__)
        except mission_store.MissionStoreError as exc:
            return coordination_observation.ObservationOutcome.unavailable(
                "%s: %s" % (type(exc).__name__, exc))
        except Exception as exc:  # noqa: BLE001 - class name only, never absent
            return coordination_observation.ObservationOutcome.unavailable(
                type(exc).__name__)
        # From here the Mission exists: any failure to REPRESENT it is
        # ``unavailable`` with an explicit reason, never absent, never
        # raised out of this boundary.
        try:
            conditions = durable_conditions(snapshot)
            if len(conditions) > coordination_observation.MAX_OBSERVED_CONDITIONS:
                return coordination_observation.ObservationOutcome.unavailable(
                    REASON_TOO_MANY_CONDITIONS % (
                        len(conditions),
                        coordination_observation.MAX_OBSERVED_CONDITIONS))
            observed = observation_from_snapshot(snapshot, self._source)
        except coordination_record.CoordinationError as exc:
            return coordination_observation.ObservationOutcome.unavailable(
                REASON_CONSTRUCTION % ("%s: %s" % (exc.problem, exc)))
        except Exception as exc:  # noqa: BLE001 - class name only
            return coordination_observation.ObservationOutcome.unavailable(
                REASON_CONSTRUCTION % type(exc).__name__)
        return coordination_observation.ObservationOutcome.observed(observed)
