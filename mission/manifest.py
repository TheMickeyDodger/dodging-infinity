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

Withdrawal marker (Task 8). The one additive-optional Mission key,
``withdrawal``, is a durable invalidation marker: the proposer's
withdrawal of its own revision-1 proposal before any decision, recorded
with the time, the revision (always 1), that revision's digest, and a
provenance block whose context is exactly the proposer's. It is valid
only on a Mission still ``AWAITING_DECISION`` at revision 1 with no
decision and no authorization, and only when the proposer is the
unauthenticated local caller kind. It is NOT a lifecycle state and
changes no transition: the Mission's ``state`` stays
``AWAITING_DECISION``, and ``MissionService.apply_human_decision``
consults the marker as a precondition and refuses every decision
(``mission_proposal_withdrawn``). A record written before the key
existed carries no marker; nothing is supplied on load.

Ownership of the marker is bound HERE, not by any caller convention:
an unauthenticated proposal may carry ``withdrawal_key_digest_sha256``,
the SHA-256 of a random withdrawal key only its creator received, set at
creation and never changed. ``withdraw_proposal`` requires the key
itself and compares its digest; the shared proposer context and the
public request and Mission ids are never enough. A proposal without a
key digest can never be withdrawn. The key is never stored. Its lifetime
is bound here too: ``withdrawal_key_expires_at`` is the key's ORIGINAL
expiry, supplied with the digest by the proposer at creation and recorded
immutably. It is never recomputed from the creation time and never
extended: a delayed creation or a recovery replay binds the same value,
and it may already have passed (such a proposal is simply never
withdrawable). It can be no later than creation plus
``record.WITHDRAWAL_KEY_VALIDITY_SECONDS``. The FIRST withdrawal is
refused at or after it, and a marker is valid only if written before it;
completing an already-recorded withdrawal with the right key stays
idempotent after it.
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
MISSION_OPTIONAL_KEYS = ("withdrawal", "withdrawal_key_digest_sha256",
                         "withdrawal_key_expires_at", "run", "lifecycle")
WITHDRAWAL_KEYS = ("withdrawn_at", "revision", "proposal_digest_sha256",
                   "provenance")
# Task 8 increment 2: the run record and the lifecycle chain (see
# ``record`` and ``MissionService``). Both are additive-optional: a
# record written before they existed carries neither.
RUN_KEYS = ("intent", "receipt", "pauses", "cancel", "verification",
            "pending_proof")
# Task d9e17d, additive-optional (a run block written before them has
# neither): the bounded count of reconciles that saw no task record yet,
# and the one evidence-only late resolution of a run stopped that way.
# Task 8 final, additive-optional too: the workspace binding recorded before
# the intent, and the latest refused workspace preparation.
RUN_OPTIONAL_KEYS = ("reconcile_unobserved", "late_resolution", "workspace",
                     "workspace_refusal")
RUN_RECONCILE_UNOBSERVED_KEYS = ("attempts", "last_attempted_at")
RUN_LATE_RESOLUTION_KEYS = ("recorded_at", "task_id", "observed_task_status")
RUN_WORKSPACE_KEYS = (
    "path_realpath", "repository_realpath", "target_repository_url",
    "revision", "proposal_digest_sha256", "baseline_commit_sha",
    "binding_digest_sha256", "state", "recorded_at", "prepared_at",
)
RUN_WORKSPACE_REFUSAL_KEYS = ("problem", "detail", "recorded_at", "refusals")
# The latest verification attempt whose conjuncts all held but whose
# approved contract was not satisfied (non-terminal, recoverable).
RUN_PENDING_PROOF_KEYS = (
    "decided_at", "attempts", "target_task_id", "observed_task_status",
    "raw_global_completeness", "supports_verification", "result_evidence_id",
    "blockers",
)
PENDING_PROOF_BLOCKER_KEYS = ("code", "detail")
RUN_INTENT_KEYS = (
    "recorded_at", "authorization_id", "revision", "proposal_digest_sha256",
    "target_repository_url", "workspace_realpath",
    "observed_baseline_commit_sha",
    "request_digest_sha256", "handoff_digest_sha256",
    "surface_baseline_digest_sha256", "approved_action_scope",
    "approved_delivery_targets",
)
RUN_RECEIPT_KEYS = ("recorded_at", "task_id", "identity_source",
                    "owned_process_group")
RUN_PAUSE_KEYS = ("paused_at", "resumed_at")
RUN_CANCEL_KEYS = ("requested_at", "achieved", "control", "quiescence",
                   "completed_at")
RUN_VERIFICATION_KEYS = (
    "decided_at", "verified", "failed_conjunct", "conjuncts",
    "raw_global_completeness", "supports_verification",
    "reported_result_digest_sha256", "result_evidence_id",
    "observed_task_status",
)
VERIFY_CONJUNCT_KEYS = ("name", "holds")
LIFECYCLE_EVENT_KEYS = ("from_state", "to_state", "reason", "recorded_at")
RUN_STATES = (record.STATE_RUNNING, record.STATE_BLOCKED,
              record.STATE_COMPLETED, record.STATE_CANCELLED)
MAX_LIFECYCLE_EVENTS = 2
MAX_RUN_TASK_ID_CHARS = 128

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


def new_mission_record(mission_id, request_id, proposal, created_at, context,
                       withdrawal_key_digest_sha256=None,
                       withdrawal_key_expires_at=None):
    """Revision 1 of a new Mission, AWAITING_DECISION, no authority. The
    optional withdrawal key digest and its ORIGINAL expiry are recorded
    together, exactly as given, only when given."""
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
    if withdrawal_key_digest_sha256 is not None or (
        withdrawal_key_expires_at is not None
    ):
        document["withdrawal_key_digest_sha256"] = withdrawal_key_digest_sha256
        document["withdrawal_key_expires_at"] = withdrawal_key_expires_at
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


def new_withdrawal(mission, context, withdrawn_at):
    """The withdrawal block for ``mission``'s revision-1 proposal, recorded
    with the proposer's context and bound to the creating request id."""
    first = mission["revisions"][0]
    return {
        "withdrawn_at": withdrawn_at,
        "revision": 1,
        "proposal_digest_sha256": first["proposal_digest_sha256"],
        "provenance": record.provenance_record(
            context, withdrawn_at, record.REFERENCE_KIND_REQUEST,
            mission["request_id"], mission["mission_id"], 1,
        ),
    }


def _validate_withdrawal(value, location):
    withdrawal = value["withdrawal"]
    where = location + ".withdrawal"
    record.require_dict(withdrawal, where)
    record.require_closed_keys(withdrawal, WITHDRAWAL_KEYS, where)
    withdrawn = record.require_timestamp(withdrawal["withdrawn_at"],
                                         where + ".withdrawn_at")
    if value["state"] != record.STATE_AWAITING_DECISION or (
        value["current_revision"] != 1 or value["decisions"]
        or value["authorization_ids"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is valid only on a Mission still AWAITING_DECISION at"
                    " revision 1 with no decision and no authorization" % where)
    first = value["revisions"][0]
    if first["provenance"]["principal_kind"] != (
        record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER
    ):
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s is valid only on a proposal of the unauthenticated"
                    " local caller kind" % where)
    if withdrawal["revision"] != 1 or withdrawal["proposal_digest_sha256"] != (
        first["proposal_digest_sha256"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must bind revision 1 and its digest" % where)
    if withdrawn < value["created_at"] or value["updated_at"] != withdrawn:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.withdrawn_at must follow creation and be the last"
                    " update" % where)
    if withdrawn >= value["withdrawal_key_expires_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.withdrawn_at must precede the withdrawal key's expiry"
                    " (it was committed while the key was valid)" % where)
    provenance = record.validate_provenance(withdrawal["provenance"],
                                            where + ".provenance")
    if record.provenance_context(provenance) != record.provenance_context(
        first["provenance"]
    ) or provenance["reference_kind"] != record.REFERENCE_KIND_REQUEST or (
        provenance["reference_id"] != value["request_id"]
        or provenance["mission_id"] != value["mission_id"]
        or provenance["revision"] != 1
        or provenance["received_at"] != withdrawn
    ):
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s.provenance must be the proposer's, bound to the"
                    " creating request and revision 1" % where)


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
    record.require_closed_keys(value, MISSION_KEYS, location,
                               optional=MISSION_OPTIONAL_KEYS)
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
        # Revision 1 records the proposer, who may be the unauthenticated
        # local caller kind; every later revision is an EDIT decision and
        # needs an authenticated principal (Task 8).
        if index == 0:
            provenance = record.validate_provenance(entry["provenance"],
                                                    where + ".provenance")
        else:
            provenance = record.require_authenticated_provenance(
                entry["provenance"], where + ".provenance")
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
    if ("withdrawal_key_digest_sha256" in value) != (
        "withdrawal_key_expires_at" in value
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.withdrawal_key_digest_sha256 and"
                    " withdrawal_key_expires_at are recorded together"
                    % location)
    if "withdrawal_key_digest_sha256" in value:
        record.require_hex(value["withdrawal_key_digest_sha256"],
                           location + ".withdrawal_key_digest_sha256", 64)
        expires = record.require_timestamp(value["withdrawal_key_expires_at"],
                                           location + ".withdrawal_key_expires_at")
        # The ORIGINAL expiry: it may already have passed at creation (a
        # delayed binding); it may never exceed a fresh lifetime.
        if expires > created + record.WITHDRAWAL_KEY_VALIDITY_SECONDS:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.withdrawal_key_expires_at exceeds the fixed key"
                        " lifetime from creation" % location)
        if revisions[0]["provenance"]["principal_kind"] != (
            record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER
        ):
            record.fail(record.PROBLEM_PROVENANCE,
                        "%s.withdrawal_key_digest_sha256 is valid only on a"
                        " proposal of the unauthenticated local caller kind"
                        % location)
    if "withdrawal" in value:
        if "withdrawal_key_digest_sha256" not in value:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.withdrawal requires the withdrawal key digest"
                        " recorded at creation" % location)
        _validate_withdrawal(value, location)
    _validate_run_and_lifecycle(value, location)
    return value


def new_run_block():
    return {"intent": None, "receipt": None, "pauses": [], "cancel": None,
            "verification": None, "pending_proof": None}


def run_is_paused(run):
    return bool(run and run["pauses"] and run["pauses"][-1]["resumed_at"] is None)


def _validate_run_and_lifecycle(value, location):
    """The run record and lifecycle chain agree with each other and with
    the Mission state. A run state needs both; AUTHORIZED may carry a run
    (an intent, a pause); no other state carries either."""
    state = value["state"]
    has_run, has_lifecycle = "run" in value, "lifecycle" in value
    if state in RUN_STATES:
        if not (has_run and has_lifecycle):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s is %s but carries no run record and lifecycle"
                        " chain explaining it" % (location, state))
    elif has_lifecycle or (has_run and state != record.STATE_AUTHORIZED):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is %s and may carry no run record or lifecycle"
                    % (location, state))
    if not has_run:
        return
    run = value["run"]
    where = location + ".run"
    record.require_dict(run, where)
    record.require_closed_keys(run, RUN_KEYS, where, optional=RUN_OPTIONAL_KEYS)
    if "withdrawal" in value:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s cannot exist on a withdrawn proposal" % where)
    events = value.get("lifecycle", [])
    if has_lifecycle:
        _validate_lifecycle(value, events, location + ".lifecycle")
    intent = run["intent"]
    if intent is not None:
        _validate_intent(value, intent, where + ".intent")
    if "workspace" in run:
        _validate_workspace(value, run["workspace"], intent, where + ".workspace")
    if "workspace_refusal" in run:
        _validate_workspace_refusal(run["workspace_refusal"],
                                    where + ".workspace_refusal")
    receipt = run["receipt"]
    if receipt is not None:
        if intent is None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.receipt needs a recorded intent" % where)
        _validate_receipt(receipt, intent, where + ".receipt")
    _validate_pauses(run["pauses"], where + ".pauses")
    reasons = [event["reason"] for event in events]
    if events and events[0]["to_state"] == record.STATE_RUNNING and (
        receipt is None or receipt["task_id"] is None
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s reached RUNNING without a receipt naming the observed"
                    " target" % where)
    verification = run["verification"]
    if verification is not None:
        _validate_verification(verification, where + ".verification")
        expected = (record.RUN_REASON_VERIFIED if verification["verified"]
                    else verification["failed_conjunct"])
        if not reasons or reasons[-1] != expected or len(events) != 2:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.verification disagrees with the lifecycle chain"
                        % where)
    elif state == record.STATE_COMPLETED or any(
        reason in dict(record.VERIFY_CONJUNCTS).values() for reason in reasons
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s reached a verification outcome with no verification"
                    " record" % where)
    pending = run["pending_proof"]
    if pending is not None:
        if not events or events[0]["to_state"] != record.STATE_RUNNING:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.pending_proof needs a run that reached RUNNING"
                        % where)
        _validate_pending_proof(pending, receipt, where + ".pending_proof")
    cancel = run["cancel"]
    if (cancel is not None) != (state == record.STATE_CANCELLED):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.cancel is present exactly when the Mission is CANCELLED"
                    % where)
    if cancel is not None:
        _validate_cancel(cancel, run, events, where + ".cancel")
    exhausted = bool(reasons) and (
        reasons[-1] == record.RUN_STOP_RECONCILE_NOT_OBSERVABLE)
    if "reconcile_unobserved" in run:
        _validate_reconcile_unobserved(run["reconcile_unobserved"], intent,
                                       exhausted, where + ".reconcile_unobserved")
    elif exhausted:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s stopped as not observable with no unobserved"
                    " reconcile attempts" % where)
    if reasons and reasons[-1] == record.RUN_STOP_LATE_CHILD_ABORTED and (
        receipt is None or receipt["task_id"] is None
        or receipt["identity_source"] != "reconciliation"
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s stopped for an aborted late child without binding it"
                    " by reconciliation" % where)
    if "late_resolution" in run:
        _validate_late_resolution(value, run, events, where + ".late_resolution")
    if intent is None and (state in (record.STATE_RUNNING,
                                     record.STATE_COMPLETED)
                           or (state == record.STATE_BLOCKED)):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is %s with no recorded intent" % (where, state))


def _validate_lifecycle(value, events, where):
    if not isinstance(events, list) or not events or (
        len(events) > MAX_LIFECYCLE_EVENTS
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must hold 1 to %d events" % (where, MAX_LIFECYCLE_EVENTS))
    previous_state, previous_at = record.STATE_AUTHORIZED, value["created_at"]
    for index, event in enumerate(events):
        at = "%s[%d]" % (where, index)
        record.require_dict(event, at)
        record.require_closed_keys(event, LIFECYCLE_EVENT_KEYS, at)
        record.require_state(event["from_state"], at + ".from_state")
        record.require_state(event["to_state"], at + ".to_state")
        if event["from_state"] != previous_state or (
            (event["from_state"], event["to_state"]) not in record.RUN_TRANSITIONS
        ):
            record.fail(record.PROBLEM_INVALID_TRANSITION,
                        "%s is not a wired run transition from %s"
                        % (at, previous_state))
        record.require_member(event["reason"],
                              record.RUN_REASONS_BY_TARGET[event["to_state"]],
                              at + ".reason")
        recorded = record.require_timestamp(event["recorded_at"],
                                            at + ".recorded_at")
        if recorded < previous_at:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.recorded_at goes backwards" % at)
        previous_state, previous_at = event["to_state"], recorded
    if previous_state != value["state"] or value["updated_at"] < previous_at:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s does not end at the stored state %s"
                    % (where, value["state"]))


def _validate_intent(value, intent, where):
    record.require_dict(intent, where)
    record.require_closed_keys(intent, RUN_INTENT_KEYS, where)
    record.require_timestamp(intent["recorded_at"], where + ".recorded_at")
    authorization_id = record.require_id(
        intent["authorization_id"], record.AUTHORIZATION_ID_PREFIX,
        where + ".authorization_id")
    if authorization_id not in value["authorization_ids"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s names an authorization the Mission does not hold" % where)
    if intent["revision"] != value["current_revision"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must bind the current revision" % where)
    entry = current_revision_entry(value)
    if intent["proposal_digest_sha256"] != entry["proposal_digest_sha256"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must bind the current revision's digest" % where)
    if entry["proposal"]["repository_url"] is None or (
        intent["target_repository_url"] != entry["proposal"]["repository_url"]
    ):
        record.fail(record.PROBLEM_REPOSITORY_IDENTITY,
                    "%s must name the approved repository exactly" % where)
    path = record.require_str(intent["workspace_realpath"],
                              where + ".workspace_realpath",
                              record.MAX_RUN_PATH_CHARS)
    if not path.startswith("/"):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.workspace_realpath must be absolute" % where)
    # OBSERVED by DI at intent time; the human approved a scope, not this.
    record.require_hex(intent["observed_baseline_commit_sha"],
                       where + ".observed_baseline_commit_sha", 40)
    for key in ("request_digest_sha256", "handoff_digest_sha256",
                "surface_baseline_digest_sha256"):
        record.require_hex(intent[key], "%s.%s" % (where, key), 64)
    scope = record.require_sorted_subset(
        intent["approved_action_scope"], record.ACTION_SCOPES,
        where + ".approved_action_scope", record.PROBLEM_ACTION_SCOPE,
        allow_empty=False)
    if scope != intent["approved_action_scope"] or not set(
        record.RUN_REQUIRED_ACTION_SCOPE
    ) <= set(scope):
        record.fail(record.PROBLEM_ACTION_SCOPE,
                    "%s.approved_action_scope must be sorted and include %s"
                    % (where, ", ".join(record.RUN_REQUIRED_ACTION_SCOPE)))
    targets = record.require_sorted_subset(
        intent["approved_delivery_targets"], record.DELIVERY_TARGETS,
        where + ".approved_delivery_targets", record.PROBLEM_DELIVERY_TARGET,
        allow_empty=True)
    if targets != intent["approved_delivery_targets"]:
        record.fail(record.PROBLEM_DELIVERY_TARGET,
                    "%s.approved_delivery_targets must be sorted" % where)
    for decision in value["decisions"]:
        if decision["decided_at"] > intent["recorded_at"]:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s: no decision may follow a recorded intent" % where)


def _require_absolute(value, where):
    path = record.require_str(value, where, record.MAX_RUN_PATH_CHARS)
    if not path.startswith("/"):
        record.fail(record.PROBLEM_BAD_VALUE, "%s must be absolute" % where)
    return path


def workspace_binding(value, path_realpath, repository_realpath,
                      baseline_commit_sha):
    """One workspace binding for the Mission record ``value`` at its current
    revision, validated, with its digest; state and times are the caller's.
    It names the worktree's path and the configured repository it was
    prepared from."""
    entry = current_revision_entry(value)
    binding = {
        "path_realpath": path_realpath,
        "repository_realpath": repository_realpath,
        "target_repository_url": entry["proposal"]["repository_url"],
        "revision": entry["revision"],
        "proposal_digest_sha256": entry["proposal_digest_sha256"],
        "baseline_commit_sha": baseline_commit_sha,
    }
    where = "workspace binding"
    _require_absolute(path_realpath, where + ".path_realpath")
    _require_absolute(repository_realpath, where + ".repository_realpath")
    if binding["target_repository_url"] is None:
        record.fail(record.PROBLEM_REPOSITORY_IDENTITY,
                    "%s: the approved Mission names no repository" % where)
    record.require_hex(baseline_commit_sha, where + ".baseline_commit_sha", 40)
    binding["binding_digest_sha256"] = record.workspace_binding_digest(
        value["mission_id"], path_realpath, repository_realpath,
        binding["target_repository_url"], binding["revision"],
        binding["proposal_digest_sha256"], baseline_commit_sha)
    return binding


def _validate_workspace(value, binding, intent, where):
    """The stored binding is exactly what ``workspace_binding`` derives for
    this Mission, its state agrees with its times, and an intent names
    exactly the path and baseline prepared before it."""
    record.require_dict(binding, where)
    record.require_closed_keys(binding, RUN_WORKSPACE_KEYS, where)
    state = record.require_member(binding["state"], record.WORKSPACE_STATES,
                                  where + ".state")
    recorded = record.require_timestamp(binding["recorded_at"],
                                        where + ".recorded_at")
    prepared = record.require_optional_timestamp(binding["prepared_at"],
                                                 where + ".prepared_at")
    expected = workspace_binding(
        value, binding["path_realpath"], binding["repository_realpath"],
        binding["baseline_commit_sha"])
    if any(binding[key] != expected[key] for key in expected):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s does not bind this Mission's current revision exactly"
                    " (or its digest does not name it)" % where)
    if (state == record.WORKSPACE_STATE_PREPARED) != (prepared is not None) or (
        prepared is not None and prepared < recorded
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.state disagrees with its times" % where)
    if intent is not None and (
        state != record.WORKSPACE_STATE_PREPARED
        or intent["workspace_realpath"] != binding["path_realpath"]
        or intent["observed_baseline_commit_sha"] != binding[
            "baseline_commit_sha"]
        or prepared > intent["recorded_at"]
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s: the intent must name exactly the workspace prepared"
                    " before it" % where)


def _validate_workspace_refusal(refusal, where):
    record.require_dict(refusal, where)
    record.require_closed_keys(refusal, RUN_WORKSPACE_REFUSAL_KEYS, where)
    record.require_str(refusal["problem"], where + ".problem",
                       record.MAX_WORKSPACE_PROBLEM_CHARS)
    record.require_str(refusal["detail"], where + ".detail",
                       record.MAX_WORKSPACE_DETAIL_CHARS)
    record.require_timestamp(refusal["recorded_at"], where + ".recorded_at")
    record.require_int(refusal["refusals"], where + ".refusals", minimum=1)


def _validate_receipt(receipt, intent, where):
    record.require_dict(receipt, where)
    record.require_closed_keys(receipt, RUN_RECEIPT_KEYS, where)
    recorded = record.require_timestamp(receipt["recorded_at"],
                                        where + ".recorded_at")
    if recorded < intent["recorded_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.recorded_at precedes the intent" % where)
    if receipt["task_id"] is not None:
        record.require_str(receipt["task_id"], where + ".task_id",
                           MAX_RUN_TASK_ID_CHARS)
    record.require_member(receipt["identity_source"],
                          record.RUN_IDENTITY_SOURCES,
                          where + ".identity_source")
    group = receipt["owned_process_group"]
    if group is not None and (not isinstance(group, int)
                              or isinstance(group, bool) or group <= 1):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.owned_process_group must be null or a group id above 1"
                    % where)


def _validate_pauses(pauses, where):
    if not isinstance(pauses, list) or len(pauses) > record.MAX_RUN_PAUSES:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must be a list of at most %d pauses"
                    % (where, record.MAX_RUN_PAUSES))
    previous = 0
    for index, pause in enumerate(pauses):
        at = "%s[%d]" % (where, index)
        record.require_dict(pause, at)
        record.require_closed_keys(pause, RUN_PAUSE_KEYS, at)
        paused = record.require_timestamp(pause["paused_at"], at + ".paused_at")
        resumed = record.require_optional_timestamp(pause["resumed_at"],
                                                    at + ".resumed_at")
        if paused < previous or (resumed is not None and resumed < paused) or (
            resumed is None and index != len(pauses) - 1
        ):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s is out of order or an earlier pause never resumed"
                        % at)
        previous = paused if resumed is None else resumed


def _validate_verification(verification, where):
    record.require_dict(verification, where)
    record.require_closed_keys(verification, RUN_VERIFICATION_KEYS, where)
    record.require_timestamp(verification["decided_at"], where + ".decided_at")
    record.require_bool(verification["verified"], where + ".verified")
    conjuncts = verification["conjuncts"]
    names = [name for name, _ in record.VERIFY_CONJUNCTS]
    if not isinstance(conjuncts, list) or len(conjuncts) != len(names):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.conjuncts must list every conjunct" % where)
    for index, (conjunct, name) in enumerate(zip(conjuncts, names)):
        at = "%s.conjuncts[%d]" % (where, index)
        record.require_dict(conjunct, at)
        record.require_closed_keys(conjunct, VERIFY_CONJUNCT_KEYS, at)
        if conjunct["name"] != name:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s must be %r (fixed order)" % (at, name))
        record.require_bool(conjunct["holds"], at + ".holds")
    _, failed = record.verification_outcome(
        dict((c["name"], c["holds"]) for c in conjuncts))
    if verification["verified"] != (failed is None) or (
        verification["failed_conjunct"] != failed
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is VERIFIED exactly when every conjunct holds, and"
                    " otherwise names the first failing conjunct" % where)
    if verification["raw_global_completeness"] not in (None, "COMPLETE",
                                                       "PARTIAL"):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.raw_global_completeness is outside the observed"
                    " vocabulary" % where)
    if verification["supports_verification"] is not None:
        record.require_bool(verification["supports_verification"],
                            where + ".supports_verification")
    if verification["reported_result_digest_sha256"] is not None:
        record.require_hex(verification["reported_result_digest_sha256"],
                           where + ".reported_result_digest_sha256", 64)
    # The bound target's task status the verification's own read observed.
    record.require_str(verification["observed_task_status"],
                       where + ".observed_task_status",
                       record.MAX_OBSERVED_STATUS_CHARS)
    if verification["result_evidence_id"] is not None:
        record.require_id(verification["result_evidence_id"],
                          record.EVIDENCE_ID_PREFIX, where + ".result_evidence_id")
    elif verification["verified"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s: a VERIFIED run names its accepted result evidence"
                    % where)


def _validate_reconcile_unobserved(unobserved, intent, exhausted, where):
    record.require_dict(unobserved, where)
    record.require_closed_keys(unobserved, RUN_RECONCILE_UNOBSERVED_KEYS, where)
    if intent is None:
        record.fail(record.PROBLEM_BAD_VALUE, "%s needs a recorded intent" % where)
    attempts = record.require_int(unobserved["attempts"], where + ".attempts",
                                  minimum=1)
    if attempts > record.MAX_RECONCILE_ATTEMPTS:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.attempts exceeds the hard bound %d"
                    % (where, record.MAX_RECONCILE_ATTEMPTS))
    # The attempt that reaches the bound is the one that stopped the run.
    if (attempts == record.MAX_RECONCILE_ATTEMPTS) != exhausted:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s reaches the bound exactly when the run stopped as not"
                    " observable" % where)
    last = record.require_timestamp(unobserved["last_attempted_at"],
                                    where + ".last_attempted_at")
    if last < intent["recorded_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.last_attempted_at precedes the intent" % where)


def _validate_late_resolution(value, run, events, where):
    resolution = run["late_resolution"]
    record.require_dict(resolution, where)
    record.require_closed_keys(resolution, RUN_LATE_RESOLUTION_KEYS, where)
    receipt = run["receipt"]
    if value["state"] != record.STATE_BLOCKED or len(events) != 1 or (
        events[0]["reason"] not in record.LATE_RESOLUTION_STOP_REASONS
    ) or (receipt is not None and receipt["task_id"] is not None):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s is valid only on a run stopped because nothing was"
                    " observable yet (%s), with no bound target"
                    % (where, ", ".join(record.LATE_RESOLUTION_STOP_REASONS)))
    recorded = record.require_timestamp(resolution["recorded_at"],
                                        where + ".recorded_at")
    if recorded < events[0]["recorded_at"] or value["updated_at"] < recorded:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.recorded_at must follow the stop" % where)
    record.require_str(resolution["task_id"], where + ".task_id",
                       MAX_RUN_TASK_ID_CHARS)
    record.require_str(resolution["observed_task_status"],
                       where + ".observed_task_status",
                       record.MAX_OBSERVED_STATUS_CHARS)


def _validate_pending_proof(pending, receipt, where):
    record.require_dict(pending, where)
    record.require_closed_keys(pending, RUN_PENDING_PROOF_KEYS, where)
    record.require_timestamp(pending["decided_at"], where + ".decided_at")
    attempts = record.require_int(pending["attempts"], where + ".attempts",
                                  minimum=1)
    if attempts > record.MAX_VERIFICATION_ATTEMPTS:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.attempts exceeds the hard bound %d"
                    % (where, record.MAX_VERIFICATION_ATTEMPTS))
    if receipt is None or pending["target_task_id"] != receipt["task_id"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must name the receipt's bound target" % where)
    record.require_str(pending["observed_task_status"],
                       where + ".observed_task_status",
                       record.MAX_OBSERVED_STATUS_CHARS)
    if pending["raw_global_completeness"] not in (None, "COMPLETE", "PARTIAL"):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.raw_global_completeness is outside the observed"
                    " vocabulary" % where)
    if pending["supports_verification"] is not None:
        record.require_bool(pending["supports_verification"],
                            where + ".supports_verification")
    record.require_id(pending["result_evidence_id"], record.EVIDENCE_ID_PREFIX,
                      where + ".result_evidence_id")
    blockers = pending["blockers"]
    if not isinstance(blockers, list) or not blockers or len(blockers) > (
        record.MAX_PENDING_PROOF_BLOCKERS
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.blockers must hold 1 to %d blockers"
                    % (where, record.MAX_PENDING_PROOF_BLOCKERS))
    for index, blocker in enumerate(blockers):
        at = "%s.blockers[%d]" % (where, index)
        record.require_dict(blocker, at)
        record.require_closed_keys(blocker, PENDING_PROOF_BLOCKER_KEYS, at)
        code = record.require_str(blocker["code"], at + ".code",
                                  record.MAX_CONTRACT_KEY_CHARS)
        if not code.startswith("mission_state_"):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.code must be a Mission State closure code" % at)
        record.require_str(blocker["detail"], at + ".detail",
                           record.MAX_BLOCKER_DETAIL_CHARS)


_CANCEL_QUIESCENCE_BY_ACHIEVED = {
    record.CANCEL_BEFORE_INTENT: (record.CANCEL_QUIESCENCE_NOTHING_STARTED,),
    record.CANCEL_AFTER_INTENT_TARGET_UNKNOWN: (
        record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
        record.CANCEL_QUIESCENCE_UNPROVEN),
    record.CANCEL_AFTER_OBSERVED_RUNNING: (
        record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
        record.CANCEL_QUIESCENCE_UNPROVEN),
    record.CANCEL_AFTER_TARGET_TERMINATED: (
        record.CANCEL_QUIESCENCE_TASK_OBSERVED_STOPPED,
        record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
        record.CANCEL_QUIESCENCE_UNPROVEN),
}


def _validate_cancel(cancel, run, events, where):
    record.require_dict(cancel, where)
    record.require_closed_keys(cancel, RUN_CANCEL_KEYS, where)
    record.require_timestamp(cancel["requested_at"], where + ".requested_at")
    achieved = record.require_member(cancel["achieved"],
                                     record.CANCEL_ACHIEVED_STATES,
                                     where + ".achieved")
    if events[-1]["reason"] != achieved:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.achieved disagrees with the lifecycle chain" % where)
    observed = events[0]["to_state"] == record.STATE_RUNNING
    if (achieved == record.CANCEL_BEFORE_INTENT) != (run["intent"] is None) or (
        observed != (achieved in (record.CANCEL_AFTER_OBSERVED_RUNNING,
                                  record.CANCEL_AFTER_TARGET_TERMINATED))
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.achieved does not match what had happened" % where)
    if cancel["completed_at"] is None:
        if cancel["control"] is not None or cancel["quiescence"] is not None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s: an incomplete cancel records no outcome" % where)
        return
    record.require_timestamp(cancel["completed_at"], where + ".completed_at")
    control = record.require_member(cancel["control"], record.CANCEL_CONTROLS,
                                    where + ".control")
    quiescence = record.require_member(
        cancel["quiescence"], _CANCEL_QUIESCENCE_BY_ACHIEVED[achieved],
        where + ".quiescence")
    reaped = control in ("owned_group_reaped", "owned_group_already_gone")
    if (quiescence == record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED) != reaped or (
        reaped and (run["receipt"] is None
                    or run["receipt"]["owned_process_group"] is None)
    ) or (achieved == record.CANCEL_BEFORE_INTENT and control != "not_applicable"):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s claims a control or quiescence its facts do not"
                    " support" % where)
