"""The pure, non-blocking status read (Task 8, slice S-II).

Read discipline, fixed here for every later field:

- ONE Mission snapshot (``MissionService.snapshot``): one load, one
  clock sample; every Mission fact below comes from that snapshot: the
  durable cursor, the evaluation time, each authorization's issued-only
  digest and its standing AT that time (live / expired / revoked /
  superseded), and the HOLDS;
- holds are DERIVED from every evaluated fact the snapshot already
  computes that withholds progress or closure — never hand-picked, and
  from the OBSERVATION's facts (evaluated from the activated contract
  and the state regardless of the authority's standing), never from the
  authority-gated state projection, so an expired, revoked or superseded
  authority hides nothing; the authority not being live is itself a
  hold, and closure eligibility (the one evaluation the core performs
  only under live authority) reports ``not_evaluated`` when it was not:
  the observation's completion holds, every proof requirement that is not
  satisfied (missing, stale/expired, mismatched, invalidated,
  contradicted, submitted-not-accepted) and stale proof freshness,
  every prerequisite problem (target mismatch, a prerequisite that has
  drifted — resolved or not — or is not complete), every unresolved
  dependency slot, every closure-eligibility failure, every resource
  that is not ready, every active blocker (by record id), the contract
  not being current or its problem, a pending human decision, every
  external report that is not a progressing one (a task reported
  BLOCKED, FAILED or NOT_STARTED; a review that is not APPROVE; a
  delivery that is ABSENT, AMBIGUOUS or INVALID) and every source whose
  standing is unknown or unavailable. Each hold carries its kind, its
  code from the core's own vocabulary (``HOLD_VOCABULARY``, pinned
  complete), its subject, its detail and ``as_of = evaluated_at``;
- every store is read by ITS OWNER's ``read()``: ONE document read
  through the store's own validator (``workflow_authority.atomic.
  ReadResult``), lock-free, creating nothing, that distinguishes
  observed ABSENCE (the file genuinely missing with accessible
  ancestors) from every ACCESS or READ error (permission, a symbolic
  link whose followed target is missing or inaccessible, any other
  OSError, invalid content) — so ``absent`` and ``unavailable`` are
  never conflated and no loader default is ever mistaken for an empty
  store. The Mission snapshot uses the Mission store's ``read()`` the
  same way: an unknown Mission is ``absent`` only against a present or
  genuinely absent store; a read error is the core's typed refusal and
  ``unavailable`` here. The public call is correct with no extra
  argument;
- nothing is written, minted, reserved, reconciled, projected into
  attention, surfaced, enqueued or created (no directory, lock or temp
  file); no operator, engine or orchestration call.

Timing is per source and truthful: the Mission facts are as of the
snapshot's ``evaluated_at``; each other store is read independently
AFTER that snapshot, in the recorded order, and may be older or newer
than it. No cross-store atomicity, ordering or coherence is claimed.
Holds that depend on the evaluation time (expiry, staleness, external
reports, a dependency Mission's own progress) cannot be durable
coordination conditions (same-point rule) and are visible HERE.
"""

from coordination import attention as coordination_attention
from coordination import store as coordination_store
from mission import authorization as mission_authorization
from mission import observation as mission_observation
from mission import progress as mission_progress
from mission import reconciliation as mission_reconciliation
from mission import record as mission_record
from mission import state as mission_state
from mission import store as mission_store
from pr_delivery import store as delivery_store
from workflow_authority import record as workflow_record
from workflow_authority import store as workflow_store

AVAILABILITY_PRESENT = workflow_store.READ_PRESENT
AVAILABILITY_ABSENT = workflow_store.READ_ABSENT
AVAILABILITY_UNAVAILABLE = workflow_store.READ_UNAVAILABLE
AVAILABILITIES = (AVAILABILITY_ABSENT, AVAILABILITY_PRESENT,
                  AVAILABILITY_UNAVAILABLE)

STANDING_LIVE = "live"
STANDING_EXPIRED = "expired"
STANDING_REVOKED = "revoked"
STANDING_SUPERSEDED = "superseded"
STANDING_NOT_LIVE = "not_live"

STORE_READ_ORDER = ("workflow", "delivery", "coordination")
SOURCES = ("task", "review", "candidate", "delivery")

# -- hold kinds and the core vocabulary each one can carry --------------

HOLD_COMPLETION = "completion"
HOLD_PROOF_REQUIREMENT = "proof_requirement"
HOLD_PROOF_FRESHNESS = "proof_freshness"
HOLD_PREREQUISITE = "prerequisite"
HOLD_DEPENDENCY_SLOT = "dependency_slot"
HOLD_CLOSURE = "closure_eligibility"
HOLD_READINESS = "readiness"
HOLD_BLOCKER = "blocker"
HOLD_CONTRACT = "contract"
HOLD_DECISION = "decision"
HOLD_REPORT = "report"
HOLD_SOURCE_STANDING = "source_standing"

HOLD_AUTHORITY = "authority"
# Task 8, slice S-V: the canonical controls (hold / cancel).
HOLD_CONTROL = "control"
CODE_CONTROL_HOLD_ACTIVE = "hold_active"
CODE_CONTROL_CANCEL_REQUESTED = "cancel_requested"
CODE_CONTROL_CANCEL_CONFIRMED = "cancel_confirmed"

CODE_CONTRACT_NOT_CURRENT = "contract_not_current"
CODE_AWAITING_DECISION = "awaiting_decision"
CODE_AUTHORITY_NOT_LIVE = "authority_not_live"
CODE_CLOSURE_NOT_EVALUATED = "not_evaluated"

# Report values that withhold progress, per source.
WITHHOLDING_REPORTS = {
    "task": (mission_reconciliation.TASK_REPORT_BLOCKED,
             mission_reconciliation.TASK_REPORT_FAILED,
             mission_reconciliation.TASK_REPORT_NOT_STARTED),
    "review": (mission_reconciliation.REVIEW_REPORT_NONE,
               mission_reconciliation.REVIEW_REPORT_PENDING,
               mission_reconciliation.REVIEW_REPORT_REJECT),
    "delivery": (mission_reconciliation.DELIVERY_REPORT_ABSENT,
                 mission_reconciliation.DELIVERY_REPORT_AMBIGUOUS,
                 mission_reconciliation.DELIVERY_REPORT_INVALID),
}

# The complete vocabulary of codes a hold can carry, per kind — pinned
# against the core's own constants by the completeness test.
HOLD_VOCABULARY = {
    HOLD_COMPLETION: tuple(mission_observation.HOLDS),
    HOLD_PROOF_REQUIREMENT: tuple(
        status for status in mission_progress.REQUIREMENT_STATUSES
        if status != mission_progress.REQUIREMENT_SATISFIED),
    HOLD_PROOF_FRESHNESS: (mission_observation.FRESHNESS_STALE,),
    HOLD_PREREQUISITE: (mission_progress.PROBLEM_DEPENDENCY_TARGET_MISMATCH,
                        mission_progress.PROBLEM_PREREQUISITE_DRIFTED,
                        mission_progress.PROBLEM_PREREQUISITE_NOT_COMPLETE),
    HOLD_DEPENDENCY_SLOT: tuple(
        status for status in mission_progress.SLOT_STATUSES
        if status != mission_progress.SLOT_RESOLVED),
    HOLD_CLOSURE: (mission_progress.PROBLEM_PROOF_NOT_SATISFIED,
                   mission_progress.PROBLEM_HARD_BLOCKER_ACTIVE,
                   mission_progress.PROBLEM_DEPENDENCY_UNRESOLVED,
                   mission_progress.PROBLEM_DEPENDENCY_TARGET_MISMATCH,
                   mission_progress.PROBLEM_PREREQUISITE_NOT_COMPLETE,
                   mission_progress.PROBLEM_PREREQUISITE_DRIFTED,
                   mission_progress.PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE,
                   mission_progress.PROBLEM_RESOURCE_NOT_READY,
                   mission_progress.PROBLEM_DEPENDENCY_CYCLE,
                   mission_progress.PROBLEM_CHECKPOINT_DISAGREES,
                   mission_progress.PROBLEM_CLOSURE_NOT_PROVABLE,
                   CODE_CLOSURE_NOT_EVALUATED),
    HOLD_READINESS: (mission_state.READINESS_NOT_READY,),
    HOLD_BLOCKER: tuple(mission_state.BLOCKER_SEVERITIES),
    HOLD_CONTRACT: (CODE_CONTRACT_NOT_CURRENT,),  # plus any core problem code
    HOLD_AUTHORITY: (CODE_AUTHORITY_NOT_LIVE,),
    HOLD_DECISION: (CODE_AWAITING_DECISION,),
    HOLD_CONTROL: (CODE_CONTROL_HOLD_ACTIVE, CODE_CONTROL_CANCEL_REQUESTED,
                   CODE_CONTROL_CANCEL_CONFIRMED),
    HOLD_REPORT: tuple("%s:%s" % (source, value)
                       for source in ("task", "review", "delivery")
                       for value in WITHHOLDING_REPORTS[source]),
    HOLD_SOURCE_STANDING: tuple("%s:%s" % (source, standing)
                                for source in SOURCES
                                for standing in (mission_observation.STANDING_UNKNOWN,
                                                 mission_observation.STANDING_UNAVAILABLE)),
}

LIMITATIONS = (
    "authorization digests are issued-only facts; standing is the Mission"
    " validator's answer at evaluated_at and nothing here validates"
    " authority for use",
    "holds are evaluated at evaluated_at from the one snapshot; the"
    " time-dependent ones (expiry, staleness, external reports, a"
    " dependency Mission's own progress) cannot be durable coordination"
    " conditions and are visible only here",
    "each store is read independently and lock-free by its owner's read:"
    " a document is a whole committed one (old or new), never a mixture,"
    " read AFTER the Mission snapshot in the recorded order; it may be"
    " older or newer than the snapshot, and no cross-store atomicity,"
    " ordering or coherence is claimed",
    "a store's document is validated only by its own package's read"
    " boundary; nothing here validates a second time",
    "external source reports are reflected only through the observation's"
    " own freshness, standing and value; none is dereferenced",
    # Task 8, slice S-V: the exact limits of the engagement controls.
    "the orchestration engine's own start and workspace-close commands"
    " carry no timeout: the Runtime bounds its WAIT on each (a start"
    " outlasting it is settled uncertain with a stop pending; an owned-stop"
    " listing or close outlasting it leaves the stop pending and is never"
    " issued twice by the same process), but the abandoned engine command"
    " itself may keep running; a stop is confirmed only by observed absence",
    "a hold stops every future Dodging Infinity effect at every gate; it"
    " does not pause an engineering session already running (the engine"
    " has no pause primitive) — cancel is the stop",
)


def _store_read(store):
    """The owner's ONE authoritative read, mapped: availability, problem,
    and the validated document (only when present)."""
    read = store.read()
    return {"path": store.path, "availability": read.availability,
            "problem": read.problem, "document": read.document}


def read_workflow_store(directory):
    read = _store_read(workflow_store.WorkflowStore(directory))
    document = read.pop("document")
    read["records"] = (None if document is None
                       else len(document.get("workflows", {})))
    return read


def read_coordination_store(directory):
    read = _store_read(coordination_store.CoordinationStore(directory))
    document = read.pop("document")
    read["store_sequence"] = (None if document is None
                              else document["store_sequence"])
    return read


def read_delivery_store(directory):
    """The delivery store through its own canonical read boundary
    (``DeliveryStore.read``: permissions, JSON, version and every record
    validated by the delivery package itself)."""
    read = _store_read(delivery_store.DeliveryStore(directory))
    document = read.pop("document")
    read["records"] = (None if document is None
                       else len(document.get("deliveries", {})))
    return read


def authorization_standing(authorization, live_authorization_id,
                           current_revision, now):
    """The standing of one authorization AT ``now``, from the snapshot:
    revoked, superseded (another revision), expired, live (the ONE the
    validator accepts now), or not live for another validator reason."""
    if authorization["revocation"]["revoked"]:
        return STANDING_REVOKED
    if authorization["revision"] != current_revision:
        return STANDING_SUPERSEDED
    expires = authorization["expires_at"]
    if expires is not None and now >= expires:
        return STANDING_EXPIRED
    if authorization["authorization_id"] == live_authorization_id:
        return STANDING_LIVE
    return STANDING_NOT_LIVE


def _report_value(source, fact):
    value = fact["value"]
    if source == "delivery" and isinstance(value, dict):
        return value.get("status")
    return value


def derive_holds(snapshot):
    """Every hold the snapshot's evaluated facts carry, as
    ``{kind, code, subject, detail, as_of}``, in a deterministic order.
    Pure over the snapshot; nothing is hand-picked.

    Every hold family is derived from the OBSERVATION report — the facts
    the core evaluates from the activated contract and the state
    regardless of the authority's standing (proof requirements and
    freshness, dependency slots and prerequisite problems, readiness,
    blockers, the completion holds, the external reports) — never from
    the authority-gated state projection, so an expired, revoked or
    superseded authority hides nothing. The two facts the core evaluates
    only under live authority are reported truthfully: closure
    eligibility failures when they were evaluated, and an explicit
    ``closure_eligibility:not_evaluated`` hold when they were not; and
    the authority not being live is itself a hold (``authority``)."""
    observation = snapshot["observation"]
    state = snapshot["state"]
    record = snapshot["record"]["record"]
    now = snapshot["evaluated_at"]
    holds = []

    def hold(kind, code, subject=None, detail=None):
        holds.append({"kind": kind, "code": code, "subject": subject,
                      "detail": detail, "as_of": now})

    for code in observation["completion"]["holds"]:
        hold(HOLD_COMPLETION, code)
    proof = observation["proof"]["value"]
    if proof is not None:
        for key in sorted(proof["requirements"]):
            status = proof["requirements"][key]
            if status != mission_progress.REQUIREMENT_SATISFIED:
                hold(HOLD_PROOF_REQUIREMENT, status, key)
    if observation["proof"]["freshness"] == mission_observation.FRESHNESS_STALE:
        hold(HOLD_PROOF_FRESHNESS, mission_observation.FRESHNESS_STALE)
    dependencies = observation["dependencies"]["value"]
    if dependencies is not None:
        for problem in dependencies["prerequisite_problems"]:
            hold(HOLD_PREREQUISITE, problem["problem"], detail=problem["detail"])
        for key in sorted(dependencies["slots"]):
            status = dependencies["slots"][key]
            if status != mission_progress.SLOT_RESOLVED:
                hold(HOLD_DEPENDENCY_SLOT, status, key)
    contract = observation["contract"]["value"]
    eligibility = state["closure_eligibility"]
    if eligibility is not None:
        for failure in eligibility["failures"]:
            hold(HOLD_CLOSURE, failure["problem"], detail=failure["detail"])
    elif contract["active"]:
        hold(HOLD_CLOSURE, CODE_CLOSURE_NOT_EVALUATED, contract["activation_id"],
             "closure eligibility is evaluated only under live authority")
    readiness = observation["readiness"]["value"]
    if readiness is not None:
        for key in sorted(readiness["resources"]):
            status = readiness["resources"][key]
            if status != mission_state.READINESS_READY:
                hold(HOLD_READINESS, status, key)
    descriptions = {}
    if state["record"] is not None:
        for blocker in state["record"]["blockers"]:
            descriptions[blocker["blocker_id"]] = blocker["description"]
    for blocker in observation["blockers"]["value"]["active"]:
        hold(HOLD_BLOCKER, blocker["severity"], blocker["key"],
             "%s: %s" % (blocker["blocker_id"],
                         descriptions.get(blocker["blocker_id"], "")))
    if contract["active"] and not contract["current"]:
        hold(HOLD_CONTRACT, CODE_CONTRACT_NOT_CURRENT, contract["activation_id"])
    if contract["problem"] is not None:
        hold(HOLD_CONTRACT, contract["problem"], contract["activation_id"])
    if contract["active"] and not contract["authority_live"]:
        hold(HOLD_AUTHORITY, CODE_AUTHORITY_NOT_LIVE, contract["activation_id"],
             "the activated contract's authority is not live: %s"
             % contract["problem"])
    if record["state"] == mission_record.STATE_AWAITING_DECISION:
        hold(HOLD_DECISION, CODE_AWAITING_DECISION,
             "revision %d" % record["current_revision"])
    for source in SOURCES:
        fact = observation[source]
        if fact["standing"] == mission_observation.STANDING_REPORTED:
            value = _report_value(source, fact)
            if value in WITHHOLDING_REPORTS.get(source, ()):
                hold(HOLD_REPORT, "%s:%s" % (source, value), source,
                     "observed_at %s" % fact["observed_at"])
        else:
            hold(HOLD_SOURCE_STANDING, "%s:%s" % (source, fact["standing"]),
                 source)
    # Task 8, slice S-V: the canonical controls, from the state record.
    controls = mission_state.control_view(state["record"])
    if controls["hold_active"]:
        hold(HOLD_CONTROL, CODE_CONTROL_HOLD_ACTIVE, "revision %d"
             % controls["hold"]["revision"], controls["hold"]["reason"])
    if controls["cancel_confirmed"]:
        hold(HOLD_CONTROL, CODE_CONTROL_CANCEL_CONFIRMED, "revision %d"
             % controls["cancel_request"]["revision"],
             controls["cancel_request"]["confirmation"]["detail"])
    elif controls["cancel_requested"]:
        hold(HOLD_CONTROL, CODE_CONTROL_CANCEL_REQUESTED, "revision %d"
             % controls["cancel_request"]["revision"],
             controls["cancel_request"]["reason"])
    return holds


def mission_view(snapshot):
    """The canonical Mission part of a status, from ONE snapshot."""
    record = snapshot["record"]["record"]
    state = snapshot["state"]["record"]
    now = snapshot["evaluated_at"]
    live_id = snapshot["record"]["live_authorization_id"]
    authorizations = [{
        "authorization_id": authorization["authorization_id"],
        "revision": authorization["revision"],
        "authorization_digest_sha256": authorization["authorization_digest_sha256"],
        "issued_at": authorization["issued_at"],
        "expires_at": authorization["expires_at"],
        "revoked": authorization["revocation"]["revoked"],
        "standing": authorization_standing(authorization, live_id,
                                           record["current_revision"], now),
        "as_of": now,
        "digest_is": "issued-only",
    } for authorization in snapshot["record"]["authorizations"]]
    observation = snapshot["observation"]
    entries = derive_holds(snapshot)
    holds = {
        "entries": entries,
        "codes": sorted(set("%s:%s" % (h["kind"], h["code"]) for h in entries)),
        "active_blockers": observation["blockers"]["value"]["active"],
        "hard_blocker_active": observation["blockers"]["value"]["hard_active"],
        "unresolved_dependencies": [] if state is None else [
            dependency["key"] for dependency in state["dependencies"]
            if dependency["resolution"] is None],
        "readiness": snapshot["state"]["readiness"],
        "contract": snapshot["state"]["contract"],
        "awaiting_decision": record["state"] == mission_record.STATE_AWAITING_DECISION,
        "as_of": now,
    }
    sources = {}
    for name in SOURCES:
        fact = observation[name]
        sources[name] = {
            "standing": fact["standing"], "freshness": fact["freshness"],
            "observed_at": fact["observed_at"], "source": fact["source"],
            "value": fact["value"],
        }
    return {
        "availability": AVAILABILITY_PRESENT,
        "problem": None,
        "mission_id": record["mission_id"],
        "durable_cursor": snapshot["durable_cursor"],
        "evaluated_at": now,
        "state": record["state"],
        "current_revision": record["current_revision"],
        "progress": (mission_state.PROGRESS_NOT_STARTED if state is None
                     else state["progress"]),
        # Task 8 S-VII (Lead disposition L7): the budget exactly as the
        # Mission Core's own state projection in this ONE snapshot states it
        # (attempts and checkpoints consumed/remaining; None before an
        # activated contract) — projected, never recomputed here.
        "budget": snapshot["state"]["budget"],
        "sequence": snapshot["state"]["sequence"],
        "live_authorization_id": live_id,
        "authorizations": authorizations,
        "holds": holds,
        "completion": observation["completion"],
        "sources": sources,
        "time": observation["time"],
        "engagements": engagement_view(state),
        "controls": mission_state.control_view(state),
    }


def engagement_view(state):
    """Task 8 S-IV: the engineering engagements and their starts, from
    the same snapshot, each start reported in three SEPARATE facts —
    admitted (the canonical start exists), what its owner OBSERVED the
    engine return (settled outcome and identity, or unsettled), and
    whether its required stop is CONFIRMED by observed absence. Pure
    projection: nothing here infers a start from a settlement or a stop
    from a close call."""
    if state is None:
        return {"reservations": [], "starts": []}
    reservations = [{
        "engagement_id": e["engagement_id"], "workflow_id": e["workflow_id"],
        "engagement_sequence": e["engagement_sequence"], "kind": e["kind"],
        "activation_id": e["activation_id"], "reserved_at": e["reserved_at"],
    } for e in mission_state.engagements_of(state)]
    starts = []
    for start in mission_state.engagement_starts_of(state):
        settlement = start["settlement"]
        observation = mission_state.latest_stop_observation(start)
        starts.append({
            "start_id": start["start_id"],
            "engagement_id": start["engagement_id"],
            "workflow_id": start["workflow_id"],
            "point": start["point"],
            "admitted_at": start["opened_at"],
            "settled": settlement is not None,
            "observed_outcome": None if settlement is None else settlement["outcome"],
            "identity": mission_state.start_identity(start),
            "stop_required": mission_state.start_stop_required(start),
            "stop_reason": (
                (settlement or {}).get("stop_reason")
                or (start["stop_requested"] or {}).get("reason")),
            "stop_confirmed": mission_state.start_stop_confirmed(start),
            "stop_observed_at": None if observation is None else observation["observed_at"],
            "stop_observation": None if observation is None else observation["detail"],
        })
    return {"reservations": reservations, "starts": starts}


def read_mission(service, mission_id, inputs=None):
    """The Mission part: ONE snapshot over the Mission store's own
    ``read()``. An unknown Mission (or a genuinely absent store) is
    ``absent``; the core's typed store refusal — any access or read
    error, invalid content — is ``unavailable`` naming it."""
    try:
        snapshot = service.snapshot(mission_id, inputs)
    except mission_record.MissionError as exc:
        if exc.problem != mission_authorization.PROBLEM_UNKNOWN_MISSION:
            return {"availability": AVAILABILITY_UNAVAILABLE,
                    "problem": "%s: %s" % (type(exc).__name__, exc),
                    "mission_id": mission_id}
        return {"availability": AVAILABILITY_ABSENT, "problem": None,
                "mission_id": mission_id}
    except mission_store.MissionStoreError as exc:
        return {"availability": AVAILABILITY_UNAVAILABLE,
                "problem": "%s: %s" % (type(exc).__name__, exc),
                "mission_id": mission_id}
    except Exception as exc:  # noqa: BLE001 - class name only, never absent
        return {"availability": AVAILABILITY_UNAVAILABLE,
                "problem": type(exc).__name__, "mission_id": mission_id}
    return mission_view(snapshot)


def read_status(service, mission_id, workflow_directory=None,
                delivery_directory=None, coordination_directory=None,
                inputs=None):
    """One status: the Mission part from ONE snapshot, each configured
    store's owner read with its own timing, and the limitations."""
    mission = read_mission(service, mission_id, inputs)
    stores = {}
    readers = {
        "workflow": (workflow_directory, read_workflow_store),
        "delivery": (delivery_directory, read_delivery_store),
        "coordination": (coordination_directory, read_coordination_store),
    }
    order = 0
    for name in STORE_READ_ORDER:
        directory, reader = readers[name]
        if directory is None:
            continue
        order += 1
        read = reader(directory)
        # Per-source timing, truthfully: this store was read on its own,
        # after the Mission snapshot, at this position in the read
        # order; nothing relates its commit time to the snapshot's.
        read["timing"] = {
            "read_after_mission_snapshot": True,
            "read_order": order,
            "coherence_with_mission_snapshot": "none",
        }
        stores[name] = read
    return {"mission": mission, "stores": stores, "limitations": list(LIMITATIONS)}


# -- the client status tool (Task 8, slice S-VII) --------------------------------

STATUS_TOOL_LIMITATIONS = (
    "the external sources (task, review, candidate, delivery) are the ones the"
    " Runtime's latest reconciliation stored in the Mission record, observed at"
    " the time each reports; this read consults no engine, repository or remote",
    "the workflow rows and attention records are read from their own stores"
    " after the Mission snapshot, lock-free; no coherence with it is claimed",
    "attention listed as SURFACED was included in an earlier tool result; the"
    " client's receipt of it is unconfirmed until it is acknowledged",
)


def reconciled_inputs(state_record, current_revision):
    """The observation inputs the Runtime's LATEST reconciliation stored
    (its normalized sources and each source's own observed-at time), as
    plain data for the snapshot: reported → ``{value, observed_at}``;
    unknown → the observed-at time with no value (or none); unavailable →
    unavailable. None without a reconciliation. The collected-at cursor is
    not asserted (None)."""
    position = mission_reconciliation.position_view(state_record, current_revision)
    if position is None:
        return None, None
    reports = {}
    for kind, source in position["sources"].items():
        observed_at = position["source_provenance"][kind]["observed_at"]
        if source["standing"] == mission_observation.STANDING_REPORTED:
            reports[kind] = {"value": source["value"], "observed_at": observed_at}
        elif source["standing"] == mission_observation.STANDING_UNKNOWN:
            reports[kind] = (None if observed_at is None
                             else {"value": None, "observed_at": observed_at})
        else:
            reports[kind] = {"unavailable": "the latest reconciliation reported"
                                            " this source unavailable"}
    return {"cursor": None, "reports": reports}, position


def mission_workflow_rows(directory, mission_id):
    """This Mission's workflow rows, read by the workflow store's own
    lock-free ``read``: ``(availability, problem, rows)``."""
    read = workflow_store.WorkflowStore(directory).read()
    if read.availability != AVAILABILITY_PRESENT:
        return read.availability, read.problem, []
    rows = []
    for workflow_id in sorted(read.document.get("workflows", {})):
        entry = read.document["workflows"][workflow_id]
        linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
        if linkage.get("mission_id") != mission_id:
            continue
        engine = entry.get("target_engine") or {}
        rows.append({"workflow_id": workflow_id, "phase": entry.get("phase"),
                     "revision": linkage.get("revision"),
                     "task_id": engine.get("task_id"),
                     "receipt_count": len(entry.get("receipts") or [])})
    return AVAILABILITY_PRESENT, None, rows


def client_attention(directory, mission_id, destination):
    """This Mission's live attention records at ``destination``, read by the
    coordination store's own lock-free ``read`` (pure listing: nothing is
    projected, surfaced or acknowledged)."""
    read = coordination_store.CoordinationStore(directory).read()
    if read.availability != AVAILABILITY_PRESENT:
        return read.availability, read.problem, []
    records = [{
        "attention_id": v["attention_id"], "condition_kind": v["condition_kind"],
        "condition_key": v["condition_key"], "presentation": v["presentation"],
        "created_at": v["created_at"], "surfaced_at": v["surfaced_at"],
    } for v in coordination_attention.pending(read.document, destination)
        if v["mission_id"] == mission_id]
    return AVAILABILITY_PRESENT, None, records


def mission_status(service, mission_id, workflow_directory=None,
                   delivery_directory=None, coordination_directory=None,
                   destination=None):
    """The bounded, READ-ONLY client status (``di_mission_status``): the
    canonical status (one Mission snapshot, evaluated WITH the reports the
    Runtime's latest reconciliation stored), the latest reconciliation
    position (sources, findings, whether it is current), this Mission's
    workflow rows and live attention records. Nothing is written, reserved,
    prepared, reconciled, projected, surfaced or waited for; no lock is
    taken; no engine or model is consulted."""
    inputs = position = None
    first = read_mission(service, mission_id)
    if first["availability"] == AVAILABILITY_PRESENT:
        try:
            snapshot = service.snapshot(mission_id)
            inputs, position = reconciled_inputs(snapshot["state"]["record"],
                                                 first["current_revision"])
        except (mission_record.MissionError, mission_store.MissionStoreError):
            inputs = position = None
    status = read_status(service, mission_id, workflow_directory, delivery_directory,
                         coordination_directory, inputs)
    status["reconciliation"] = None if position is None else {
        "reconciled_at": position["reconciled_at"], "sequence": position["sequence"],
        "observed_revision": position["observed_revision"],
        "current": position["current"], "findings": position["findings"]}
    workflows = {"availability": None, "problem": None, "rows": []}
    if workflow_directory is not None:
        availability, problem, rows = mission_workflow_rows(workflow_directory,
                                                            mission_id)
        workflows = {"availability": availability, "problem": problem, "rows": rows}
    attention = {"availability": None, "problem": None, "records": []}
    if coordination_directory is not None and destination is not None:
        availability, problem, records = client_attention(coordination_directory,
                                                          mission_id, destination)
        attention = {"availability": availability, "problem": problem,
                     "records": records}
    status["workflows"] = workflows
    status["attention"] = attention
    status["limitations"] = list(status["limitations"]) + list(STATUS_TOOL_LIMITATIONS)
    # The client tool names the canonical Mission part ``canonical``.
    status["canonical"] = status.pop("mission")
    return status
