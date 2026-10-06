"""The local operator request surface: request intake to a bounded
Mission proposal, durable status, one-shot control of the caller's own
pending proposal, an ordinary approval path that ALWAYS refuses, and
(Task 8, user decision) an operator-attested approval path that is NOT
independently verified.

Who the caller is. Any local process that can run this entry point. DI
trusts it for nothing: it is not identified, not authenticated, and not
a human approver. Its proposals are recorded in Mission Core under the
one fixed context ``LOCAL_CALLER_CONTEXT``, built from module constants
only (never from a flag, an environment variable or a request field),
with Mission Core's proposal-only kind ``unauthenticated_local_caller``.
Mission Core itself refuses that kind for every decision, decision or
state-operation id, state operation and authorization, so nothing here
could turn it into authority even by mistake.

What each operation does, exactly:

- ``submit``: validates the request against Mission Core's own proposal
  schema (closed: an unknown field, including any identity, approval or
  control field, refuses), reserves a Mission Core request id, mints the
  request's one-shot control capability, records both in ONE atomic
  write of the surface's document, then calls ``MissionService.propose``
  and binds the Mission id. The response carries the rendered proposal,
  its revision and digest, and the control capability, which is shown
  this once and never stored (only its SHA-256 is).
- ``status``: read-only. The surface record plus Mission Core's own
  read-only observation (``MissionService.observe``) returned UNCHANGED,
  so every fact keeps the standing and freshness Mission Core gave it:
  a recorded fact is never presented as accepted, verified or
  delivered. Nothing is locked, written or created; no chat history is
  needed or consulted.
- ``approve``: refuses, always, with ``local_request_approval_unauthenticated``
  and Mission Core's ``mission_unauthenticated_principal`` reason naming
  what is missing. The attempt is recorded durably (once per distinct
  content) with how it relates to the current revision. An ordinary local
  caller still cannot approve anything.
- ``present`` / ``attest_approval`` (Task 8, user decision): the Outer
  Operator presents ONE exact proposal (an explicit reference is required
  when more than one is pending) and then relays the human's explicit
  "approved" reply as an ATTESTATION. It is applied through Mission Core's
  ``apply_operator_attested_approval`` under ``OPERATOR_ATTESTED_CONTEXT``,
  an ``OperatorAttestedContext``, never an ``AuthenticatedContext``. Its
  provenance proof is ``operator_attested_not_independently_verified``,
  and every record carries the residual risk that a mistaken or malicious
  same-user operator or local process could fabricate it. Exact binding
  (Mission, revision, digest, scope, targets, expiry) and one decision id
  per request, applied only in the call that reserved it. An unknown
  outcome is a HOLD: a later call only reconciles Mission Core's durable
  record (APPLIED if it shows the decision) and never applies or mints.
- ``cancel``: requires the request's control capability, which only its
  creator received. Knowing a request_ref or a Mission id is never
  enough. It is in scope only while the Mission is the caller's own
  proposal, still AWAITING_DECISION at revision 1 with no decision
  recorded; anything else (a decided, edited, authorized or otherwise
  authenticated Mission) is refused before the capability is spent.
  Control of a running Herdr mission is UNPROVEN and not implemented.
  A cancel does two durable things, Mission Core first: it records
  Mission Core's withdrawal marker on that revision-1 proposal (an
  additive guard ``apply_human_decision`` consults before any decision,
  so no authenticated path can approve, edit or deny it afterwards), and
  it commits the local request's terminal CANCELLED state, which every
  write path here consults under the store lock. The Mission's lifecycle
  state stays AWAITING_DECISION: no Mission state or transition exists
  for this and none was added. No authorization existed for it, and the
  cancel stops no running work.
- ``recover``: binds the Mission id after an interrupted ``submit`` by
  replaying ``propose`` with the recorded request id and proposal,
  which Mission Core answers idempotently.

- ``status`` also carries the RUN, from durable records alone
  (``run_status``): not started, run intent recorded, HOLD when an effect's
  outcome is unknown, observed running, verification blocked pending proof
  (the stopped target and its blocker codes), paused (DI progression only,
  external work NOT suspended), cancelled with its achieved state, and
  terminal with its outcome; plus Mission State progress, which is a
  separate record (VERIFIED does not close it), and the delivered state
  DERIVED from attested P1-A6 receipts, never a constant.
- ``run_command`` (Task 8 increment 2b): the callable run route. For the
  request's OWN Mission only (an explicit request_ref, never a guessed or
  foreign Mission), it calls the injected run bridge for dispatch, observe,
  reconcile, verify, result, pause, resume, cancel and prove (the existing
  evidence and acceptance seams, each step explicit, nothing auto-accepted).

Imports. This module imports Mission Core, the capability seam and the
atomic-write helpers, and nothing that can start, route or reach
engineering work: every operation above except ``run_command`` starts
nothing, and that is pinned statically and behaviorally. ``run_command``
reaches engineering work ONLY through a bridge object handed in by its
builder (``local_request.cli`` constructs the real
``target_runtime.mission_bridge.MissionBridge`` lazily, for the run
commands alone); without one it refuses. Delivery authority is ``none``
on every response.
"""

import copy
import json

from mission import record as mission_record
from mission import state as mission_state
from mission import store as mission_store
from workflow_authority.digest import json_digest

from local_request import store as store_module

TRANSPORT = "local_request"
PRINCIPAL_REF = "unauthenticated"
# The ONE context this surface ever presents: constants only.
LOCAL_CALLER_CONTEXT = mission_record.AuthenticatedContext(
    transport=TRANSPORT,
    principal_kind=mission_record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER,
    principal_ref=PRINCIPAL_REF,
)

# Task 8 (user decision): the ONE attestation this surface presents when the
# Outer Operator relays the human's explicit approval reply. Constants only.
# It is an OperatorAttestedContext, never an AuthenticatedContext: trusted by
# the user's declared policy, not independently established by DI.
ATTESTING_OPERATOR_REF = "outer_operator_relay"
OPERATOR_ATTESTED_CONTEXT = mission_record.OperatorAttestedContext(
    transport=TRANSPORT,
    principal_ref=ATTESTING_OPERATOR_REF,
)
PROVENANCE_LABEL = {
    "principal_kind": mission_record.PRINCIPAL_KIND_OPERATOR_ATTESTED,
    "proof": mission_record.PROOF_OPERATOR_ATTESTED,
}

REQUEST_KEYS = mission_record.PROPOSAL_KEYS + mission_record.PROPOSAL_OPTIONAL_KEYS

DELIVERY_AUTHORITY = "none"
DISPATCH = "none"

PROBLEM_UNKNOWN_FIELD = "local_request_unknown_field"
PROBLEM_BAD_REQUEST = "local_request_bad_request"
PROBLEM_INCOMPLETE_REQUEST = "local_request_proof_contract_required"
PROBLEM_UNKNOWN_REQUEST = "local_request_unknown_request"
PROBLEM_CANCELLED = "local_request_cancelled"
PROBLEM_CANCELLATION_INCOMPLETE = "local_request_cancellation_incomplete"
PROBLEM_UNCONFIRMED = "local_request_proposal_unconfirmed"
PROBLEM_OUT_OF_SCOPE = "local_request_control_out_of_scope"
PROBLEM_CONTROL_CAPABILITY = "local_request_control_capability"
PROBLEM_MISSION_UNAVAILABLE = "local_request_mission_unavailable"
PROBLEM_RUN_UNAVAILABLE = "local_request_run_unavailable"
PROBLEM_NOTHING_PENDING = "local_request_nothing_pending"
PROBLEM_NOT_AFFIRMATIVE = "local_request_reply_not_affirmative"
PROBLEM_AMBIGUOUS = "local_request_ambiguous_reference"
PROBLEM_MISATTRIBUTED = "local_request_misattributed"
PROBLEM_BINDING_MISMATCH = "local_request_binding_mismatch"
PROBLEM_ATTESTATION_CONFLICT = "local_request_attestation_conflict"
PROBLEM_ATTESTATION_REFUSED = "local_request_attestation_refused"
PROBLEM_ATTESTATION_HOLD = "local_request_attestation_hold"

APPROVAL_REASON = "approval refused: " + mission_record.UNAUTHENTICATED_REFUSAL_DETAIL
OUT_OF_SCOPE_REASON = (
    "this surface controls only its caller's own pending proposal: still"
    " AWAITING_DECISION at revision 1 with no decision recorded. Control of"
    " a decided, edited, authorized or running Mission is UNPROVEN here and"
    " not implemented"
)
HOLD_REASON = (
    "HOLD: the attested approval's outcome is unknown (Mission Core could not"
    " be read, or a write failed and may or may not have committed). Nothing"
    " more will be applied or minted for this request. Only reconciliation"
    " against Mission Core's durable record can resolve it: it becomes"
    " APPLIED only if that record shows the decision. The HOLD does not mean"
    " no authorization exists. To abandon it, cancel the request with its"
    " control capability (which withdraws the proposal in Mission Core) and"
    " propose again"
)
CANCELLED_REASON = (
    "this local request is CANCELLED, a durable terminal state of this"
    " surface; nothing more is written for it here"
)


def withdrawal_effect(request_ref, mission_id, mission_state):
    """The exact statement of what a cancel did, and what it did not."""
    return (
        "local request %s is CANCELLED on this surface, and Mission %s carries"
        " a withdrawal marker for its revision-1 proposal: Mission Core"
        " refuses every decision on it. Its lifecycle state is still %s (no"
        " Mission state or transition was changed). No Mission Authorization"
        " had been issued for it, and this stopped and changed no running"
        " work." % (request_ref, mission_id, mission_state))


def incomplete_cancellation_effect(request_ref, mission_id, mission_state):
    """An interrupted cancel: the Mission-side marker landed, the local
    terminal state did not."""
    return (
        "local cancellation INCOMPLETE: Mission %s carries a withdrawal marker"
        " for its revision-1 proposal (Mission Core refuses every decision on"
        " it; its lifecycle state is still %s), but local request %s is still"
        " OPEN on this surface because the local save did not land. Run cancel"
        " again with the same control capability to complete it; that works"
        " after the capability's expiry too. Nothing more is recorded for this"
        " request until then." % (mission_id, mission_state, request_ref))


# -- run status (Task 8 increment 2b): one projection, durable records only --

RUN_PHASE_NOT_STARTED = "not_started"
RUN_PHASE_NOT_DISPATCHED = "authorized_not_dispatched"
RUN_PHASE_HOLD_OUTCOME_UNKNOWN = "hold_intent_outcome_unknown"
RUN_PHASE_HOLD_IDENTITY_UNKNOWN = "hold_target_identity_unknown"
RUN_PHASE_DISPATCHED = "dispatched_not_yet_observed"
RUN_PHASE_RUNNING = "running_observed"
RUN_PHASE_PENDING_PROOF = "verification_blocked_pending_proof"
RUN_PHASE_COMPLETED = "completed_verified"
RUN_PHASE_BLOCKED = "blocked"
RUN_PHASE_CANCELLED = "cancelled"
RUN_PAUSE_STATEMENT = (
    "paused for DI progression only: DI initiates no further dispatch,"
    " RUNNING transition, reconciliation or verification until an explicit"
    " resume. External in-flight work is NOT suspended: a running target"
    " keeps executing and may keep writing to its own workspace; DI stops"
    " only its own further progression and its own record writes. No"
    " supported suspend seam exists here"
)
RUN_HOLD_STATEMENT = (
    "HOLD: the outcome is unknown. Nothing is retried or re-authorized;"
    " only reconcile can bind exactly one provable child or stop durably"
)
RUN_BASELINE_STATEMENT = (
    "observed by DI at dispatch, not human-approved: the human approved a"
    " scope, not a commit"
)
RUN_PROGRESS_NOTE = (
    "Mission State progress is a separate record: VERIFIED completes the"
    " Mission lifecycle but does not close Mission State progress, so a"
    " COMPLETED Mission may still show IN_PROGRESS here"
)
# The delivered state is DERIVED: an attested P1-A6 receipt (recorded only
# through the delivery layer's validating seam) for the final delivery
# step, in the succeeded receipt state under the succeeded step state.
DELIVERED_STEP = "PR_CREATE"
DELIVERY_BASIS = (
    "derived from attested P1-A6 receipts: delivered only when the %s"
    " receipt is succeeded under a succeeded step; engineering approval and"
    " verification confer no delivery" % DELIVERED_STEP
)


def _unattributed_verification(run):
    """A verification read that did NOT establish it observed the bound
    target (its recorded ``target_identity`` or ``evidence_complete``
    conjunct failed): reported separately, never as the target's."""
    verification = run.get("verification")
    if verification is None:
        return None
    holds = dict((c["name"], c["holds"]) for c in verification["conjuncts"])
    if holds.get("target_identity") is True and (
        holds.get("evidence_complete") is True
    ):
        return None
    return {"at": verification["decided_at"], "source": "verification",
            "target_identity_established": holds.get("target_identity") is True,
            "observation_supported": holds.get("evidence_complete") is True,
            "task_status": verification["observed_task_status"],
            "note": "not attributed to the bound target: a foreign or"
                    " unsupported observation"}


def _observations(run, lifecycle):
    """Every durable observation of the BOUND target, oldest first, each
    ``{at, source, target_stopped, task_status}``. ``target_stopped`` is
    None where the record does not establish either way (a cancel whose
    observation did not show the target stopped). A verification read is
    included only when its identity and support checks established that it
    observed the bound target; otherwise it is ``_unattributed_verification``.
    The pending-proof record and a terminated-target cancel are attributable
    by construction (written only after those same checks held)."""
    observations = []
    if lifecycle and lifecycle[0]["to_state"] == mission_record.STATE_RUNNING:
        stopped = lifecycle[0]["reason"] == mission_record.RUN_REASON_OBSERVED_AFTER_STOP
        observations.append({"at": lifecycle[0]["recorded_at"],
                             "source": "running_transition",
                             "target_stopped": stopped, "task_status": None})
    pending = run.get("pending_proof")
    if pending is not None:
        observations.append({"at": pending["decided_at"],
                             "source": "verification_pending_proof",
                             "target_stopped": True,
                             "task_status": pending["observed_task_status"]})
    verification = run.get("verification")
    if verification is not None and _unattributed_verification(run) is None:
        holds = dict((c["name"], c["holds"]) for c in verification["conjuncts"])
        observations.append({"at": verification["decided_at"],
                             "source": "verification",
                             "target_stopped": holds.get("target_stopped") is True,
                             "task_status": verification["observed_task_status"]})
    cancel = run.get("cancel")
    if cancel is not None and cancel["achieved"] in (
        mission_record.CANCEL_AFTER_TARGET_TERMINATED,
        mission_record.CANCEL_AFTER_OBSERVED_RUNNING,
    ):
        observations.append({
            "at": cancel["requested_at"], "source": "cancel_classification",
            "target_stopped": (True if cancel["achieved"]
                               == mission_record.CANCEL_AFTER_TARGET_TERMINATED
                               else None),
            "task_status": None})
    # Stable by time; equal times keep the record order above, which is the
    # order those writes can happen in.
    return sorted(observations, key=lambda o: o["at"])


def run_status(mission, state_projection):
    """The run, as the durable records state it: no caller context, no
    observation, nothing inferred. ``mission`` is the Mission record and
    ``state_projection`` Mission Core's ``get_state`` projection."""
    run = mission.get("run") or {}
    intent, receipt = run.get("intent"), run.get("receipt")
    cancel, verification = run.get("cancel"), run.get("verification")
    pending = run.get("pending_proof")
    pauses = run.get("pauses") or []
    lifecycle = mission.get("lifecycle") or []
    state = mission["state"]
    if state == mission_record.STATE_AUTHORIZED:
        if intent is None:
            phase = RUN_PHASE_NOT_DISPATCHED
        elif receipt is None:
            phase = RUN_PHASE_HOLD_OUTCOME_UNKNOWN
        elif receipt["task_id"] is None:
            phase = RUN_PHASE_HOLD_IDENTITY_UNKNOWN
        else:
            phase = RUN_PHASE_DISPATCHED
    elif state == mission_record.STATE_RUNNING:
        phase = RUN_PHASE_PENDING_PROOF if pending is not None else RUN_PHASE_RUNNING
    elif state == mission_record.STATE_COMPLETED:
        phase = RUN_PHASE_COMPLETED
    elif state == mission_record.STATE_BLOCKED:
        phase = RUN_PHASE_BLOCKED
    elif state == mission_record.STATE_CANCELLED:
        phase = RUN_PHASE_CANCELLED
    else:
        phase = RUN_PHASE_NOT_STARTED
    paused = bool(pauses and pauses[-1]["resumed_at"] is None)
    hold = phase in (RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                     RUN_PHASE_HOLD_IDENTITY_UNKNOWN) or bool(
        cancel and cancel["achieved"] != mission_record.CANCEL_BEFORE_INTENT
        and cancel["quiescence"] in (None, mission_record.CANCEL_QUIESCENCE_UNPROVEN))
    observations = _observations(run, lifecycle)
    state_record = (state_projection or {}).get("record")
    receipts = []
    for artifact in (state_record["artifacts"] if state_record else []):
        marker = mission_state.receipt_attestation_of(artifact)
        if marker is not None:
            receipts.append({
                "delivery_id": marker["delivery_id"], "step": marker["step"],
                "receipt_state": marker["receipt_state"],
                "step_state": marker["step_state"],
                "completed_effect": mission_state.receipt_effect_completed(marker),
            })
    return {
        "phase": phase, "lifecycle_state": state, "hold": hold,
        "hold_statement": RUN_HOLD_STATEMENT if hold else None,
        "paused": paused,
        "pause_statement": RUN_PAUSE_STATEMENT if paused else None,
        "external_work_suspended": False,
        "outcome": (lifecycle[-1]["reason"]
                    if state in mission_record.TERMINAL_STATES else None),
        # Recorded observations of the bound target, with their times: never
        # a claim about now. ``first_observation`` is the HISTORICAL one that
        # moved the run to RUNNING; ``latest_observation`` is the most recent
        # durable one; once any recorded the target stopped, it stays so.
        "first_observation": observations[0] if observations else None,
        "latest_observation": observations[-1] if observations else None,
        "target_stopped_observed": any(o["target_stopped"] is True
                                       for o in observations),
        "unattributed_observation": _unattributed_verification(run),
        "target_task_id": receipt["task_id"] if receipt else None,
        "pending_proof": copy.deepcopy(pending) if (
            pending is not None and state == mission_record.STATE_RUNNING) else None,
        "verification": None if verification is None else {
            "verified": verification["verified"],
            "failed_conjunct": verification["failed_conjunct"],
            "decided_at": verification["decided_at"]},
        "engineering_verified": bool(verification and verification["verified"]),
        "cancel": copy.deepcopy(cancel),
        "mission_state_progress": (state_projection or {}).get("progress"),
        "progress_note": RUN_PROGRESS_NOTE,
        "delivery": {
            "receipts": receipts,
            "delivered": any(r["step"] == DELIVERED_STEP and r["completed_effect"]
                             for r in receipts),
            "basis": DELIVERY_BASIS,
        },
        "delivery_authority": DELIVERY_AUTHORITY,
        "baseline": RUN_BASELINE_STATEMENT if intent else None,
        "run": copy.deepcopy(run),
        "lifecycle": copy.deepcopy(lifecycle),
    }


class LocalRequestRefusal(Exception):
    """A refusal with a distinct ``local_request_*`` problem code."""

    def __init__(self, problem, reason, **details):
        super(LocalRequestRefusal, self).__init__(reason)
        self.problem = problem
        self.reason = reason
        self.details = details

    def as_dict(self):
        result = {"ok": False, "status": "refused", "problem": self.problem,
                  "reason": self.reason,
                  "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH}
        result.update(self.details)
        return result


def _refuse(problem, reason, **details):
    raise LocalRequestRefusal(problem, reason, **details)


def approval_refusal_details():
    """The machine-readable refusal every approval gets."""
    return {
        "problem": store_module.PROBLEM_APPROVAL_UNAUTHENTICATED,
        "mission_problem": mission_record.PROBLEM_UNAUTHENTICATED_PRINCIPAL,
        "missing": list(mission_record.DECISION_REQUIREMENTS_MISSING),
        "reason": APPROVAL_REASON,
    }


def validate_request(request):
    """The closed request: exactly Mission Core's proposal keys, validated
    by Mission Core. Nothing else is accepted, so no field can name a
    principal, an approval, a control action or a dispatch."""
    if not isinstance(request, dict):
        _refuse(PROBLEM_BAD_REQUEST, "the request must be a JSON object")
    unknown = sorted(key for key in request if key not in REQUEST_KEYS)
    if unknown:
        _refuse(PROBLEM_UNKNOWN_FIELD,
                "unknown request field(s) %s; the request carries only %s and"
                " never an identity, an approval, a control action or a"
                " dispatch" % (", ".join(repr(k) for k in unknown[:8]),
                               ", ".join(REQUEST_KEYS)))
    # This surface requires the complete proof contract (evidence
    # requirements, required artifacts, dependencies, readiness,
    # degradation policy, continuation budget). Mission Core's legacy
    # compatibility (absent or null means none) stays Mission Core's.
    if request.get("proof_contract") is None:
        _refuse(PROBLEM_INCOMPLETE_REQUEST,
                "the request must carry a complete proof_contract (evidence"
                " requirements and a continuation budget); an absent or null"
                " proof_contract is refused by this surface")
    try:
        return mission_record.validate_proposal(request)
    except mission_record.MissionError as exc:
        _refuse(PROBLEM_BAD_REQUEST, str(exc), mission_problem=exc.problem)


class LocalRequestSurface(object):

    def __init__(self, mission_service, store, clock, mint_ref=None,
                 bridge=None):
        self._missions = mission_service
        self._store = store
        self._clock = clock
        self._mint_ref = mint_ref or store_module.new_request_ref
        # The run bridge, handed in by the builder for the run commands
        # alone; this module never imports or constructs it.
        self._bridge = bridge

    def _fresh_request_ref(self, taken):
        """An unused request_ref, with an exhaustion stop (never a loop that
        waits on the minter to behave)."""
        for _ in range(store_module.REQUEST_REF_ATTEMPTS):
            candidate = self._mint_ref()
            if store_module.request_ref_problem(candidate) is None and (
                candidate not in taken
            ):
                return candidate
        raise store_module.LocalRequestStoreError(
            "the request_ref minter returned no unused, well-formed reference"
            " in %d attempts" % store_module.REQUEST_REF_ATTEMPTS)

    def _now(self):
        return mission_record.require_timestamp(self._clock(), "clock")

    @staticmethod
    def _record(document, request_ref):
        if store_module.request_ref_problem(request_ref) is not None:
            _refuse(PROBLEM_UNKNOWN_REQUEST,
                    "request_ref must be an lr- reference issued by this"
                    " surface; a Mission id or any other identifier is not one")
        found = document["requests"].get(request_ref)
        if found is None:
            _refuse(PROBLEM_UNKNOWN_REQUEST,
                    "request %s is not held by this surface on this machine"
                    % request_ref)
        return found

    def _mission(self, mission_id):
        try:
            return self._missions.get(mission_id)
        except MISSION_CORE_ERRORS as exc:
            _refuse(PROBLEM_MISSION_UNAVAILABLE,
                    "Mission Core could not return mission %s (%s)"
                    % (mission_id, exc),
                    mission_problem=getattr(exc, "problem", None))

    # -- intake ----------------------------------------------------------

    def submit(self, request):
        clean = validate_request(request)
        digest = mission_record.proposal_digest(clean)
        with self._store.lock():
            document = self._store.load()
            if len(document["requests"]) >= store_module.MAX_REQUEST_RECORDS:
                raise store_module.LocalRequestStoreError(
                    "%d requests are held; the hard bound is %d and records"
                    " are never evicted" % (len(document["requests"]),
                                            store_module.MAX_REQUEST_RECORDS),
                    store_module.PROBLEM_STORE_FULL)
            now = self._now()
            request_ref = self._fresh_request_ref(document["requests"])
            mission_request_id = self._missions.mint_request_id(
                LOCAL_CALLER_CONTEXT)
            token = store_module.ProposalControlAuthority(
                document["control_capabilities"]
            ).mint(request_ref, store_module.ACTION_CANCEL_PENDING_PROPOSAL, 1,
                   now)
            document["requests"][request_ref] = {
                "request_ref": request_ref,
                "mission_request_id": mission_request_id,
                "proposal": copy.deepcopy(clean),
                "proposal_digest_sha256": digest,
                "mission_id": None,
                "created_at": now,
                "state": store_module.STATE_OPEN,
                "cancellation": None,
                "approval_refusals": [],
            }
            self._store.save(document)
        outcome = self._bind(request_ref)
        outcome.update({
            "control_capability": token,
            "control_capability_note": (
                "shown once and never stored; it is the only way to cancel"
                " this pending proposal and confers no approval"),
        })
        return outcome

    def recover(self, request_ref):
        """Bind the Mission id of an interrupted submit (idempotent)."""
        return self._bind(request_ref)

    def _bind(self, request_ref):
        with self._store.lock():
            document = self._store.load()
            entry = self._record(document, request_ref)
            if entry["state"] == store_module.STATE_CANCELLED:
                _refuse(PROBLEM_CANCELLED, CANCELLED_REASON,
                        request_ref=request_ref)
            # Bind the capability's digest AND its ORIGINAL expiry (from mint
            # time), so a delayed creation or recovery never extends it.
            key_digest, capability = [
                (k, c) for k, c in document["control_capabilities"].items()
                if c["workflow_id"] == request_ref][0]
            outcome = self._missions.propose(
                entry["mission_request_id"], entry["proposal"],
                LOCAL_CALLER_CONTEXT, key_digest, capability["expires_at"])
            if entry["mission_id"] not in (None, outcome["mission_id"]):
                raise store_module.LocalRequestStoreError(
                    "request %s is bound to mission %s but Mission Core"
                    " returned %s" % (request_ref, entry["mission_id"],
                                      outcome["mission_id"]))
            if entry["mission_id"] is None:
                entry["mission_id"] = outcome["mission_id"]
                self._store.save(document)
        return {
            "ok": True, "status": "proposed", "request_ref": request_ref,
            "mission_id": outcome["mission_id"],
            "revision": outcome["revision"],
            "mission_state": outcome["state"],
            "proposal": outcome["proposal"],
            "proposal_digest_sha256": outcome["proposal_digest_sha256"],
            "approval": approval_refusal_details(),
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
        }

    # -- status (read-only) ----------------------------------------------

    def status(self, request_ref):
        document = self._store.load()
        entry = self._record(document, request_ref)
        result = {
            "ok": True, "status": "read", "request_ref": request_ref,
            "surface_state": entry["state"],
            "cancellation": copy.deepcopy(entry["cancellation"]),
            "mission_request_id": entry["mission_request_id"],
            "mission_id": entry["mission_id"],
            "proposal_digest_sha256": entry["proposal_digest_sha256"],
            "approval": approval_refusal_details(),
            "approval_refusals": copy.deepcopy(entry["approval_refusals"]),
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
            "mission_observation": None,
            "mission_observation_problem": None,
            "attested_approval": copy.deepcopy(entry.get("attested_approval")),
        }
        if entry["mission_id"] is None:
            result["mission_observation_problem"] = PROBLEM_UNCONFIRMED
            return result
        try:
            # Mission Core's own read-only report, unchanged, and the
            # withdrawal marker read from the same durable record.
            result["mission_observation"] = self._missions.observe(
                entry["mission_id"])
            mission = self._missions.get(entry["mission_id"])["record"]
            withdrawal = mission.get("withdrawal")
            # Task 8 increment 2b: the run, from the same durable records.
            result["run"] = run_status(
                mission, self._missions.get_state(entry["mission_id"]))
        except MISSION_CORE_ERRORS as exc:
            result["mission_observation"] = None
            result["mission_observation_problem"] = PROBLEM_MISSION_UNAVAILABLE
            result["mission_observation_detail"] = str(exc)
            return result
        # ``dispatch`` names what is RECORDED, never what this read did.
        result["dispatch"] = ("recorded" if (mission.get("run") or {}).get(
            "intent") else DISPATCH)
        result["mission_withdrawal"] = withdrawal
        result["mission_decisions_refused_by_withdrawal"] = withdrawal is not None
        result["local_cancellation"] = None
        if withdrawal is not None:
            mission_state = result["mission_observation"]["phase"]["value"]["state"]
            if entry["state"] == store_module.STATE_CANCELLED:
                result["local_cancellation"] = "COMPLETE"
                result["effect"] = withdrawal_effect(
                    request_ref, entry["mission_id"], mission_state)
            else:
                # The core marker was recorded but the local save did not
                # land: say exactly that, never "CANCELLED".
                result["local_cancellation"] = "INCOMPLETE"
                result["effect"] = incomplete_cancellation_effect(
                    request_ref, entry["mission_id"], mission_state)
        return result

    # -- the run route (Task 8 increment 2b) ------------------------------

    RUN_COMMANDS = ("dispatch", "observe", "reconcile", "verify", "result",
                    "pause", "resume", "cancel", "prove")

    def run_command(self, request_ref, command, **arguments):
        """One run command for the request's OWN Mission, through the
        injected bridge. The explicit request_ref is the only target: no
        Mission id, guess or default is accepted. Refusals keep the
        bridge's own problem code; nothing here retries or interprets."""
        if command not in self.RUN_COMMANDS:
            _refuse(PROBLEM_BAD_REQUEST, "unknown run command %r" % (command,))
        if self._bridge is None:
            _refuse(PROBLEM_RUN_UNAVAILABLE,
                    "this surface was built without the run bridge; the run"
                    " commands are reachable only through direquest.py")
        document = self._store.load()
        entry = self._record(document, request_ref)
        if entry["state"] == store_module.STATE_CANCELLED:
            _refuse(PROBLEM_CANCELLED, CANCELLED_REASON, request_ref=request_ref)
        if entry["mission_id"] is None:
            _refuse(PROBLEM_UNCONFIRMED,
                    "the proposal is not confirmed in Mission Core yet")
        operation = getattr(self._bridge, command)
        try:
            outcome = operation(entry["mission_id"], **arguments)
        except Exception as exc:
            problem, reason = getattr(exc, "problem", None), getattr(
                exc, "reason", None)
            if not isinstance(problem, str) or not isinstance(reason, str):
                raise
            details = dict(getattr(exc, "details", None) or {})
            details.pop("request_ref", None)
            _refuse(problem, reason, request_ref=request_ref, **details)
        result = copy.deepcopy(outcome)
        result.update({"ok": True, "status": command, "request_ref": request_ref,
                       "mission_id": entry["mission_id"],
                       "delivery_authority": DELIVERY_AUTHORITY})
        return result

    # -- approval: always refused ----------------------------------------

    def approve(self, request_ref, revision, proposal_digest_sha256,
                expires_at=None):
        refusal = approval_refusal_details()
        with self._store.lock():
            document = self._store.load()
            entry = self._record(document, request_ref)
            if entry["state"] == store_module.STATE_CANCELLED:
                _refuse(PROBLEM_CANCELLED, CANCELLED_REASON,
                        request_ref=request_ref, approval=refusal,
                        recorded=False)
            if not isinstance(revision, int) or isinstance(revision, bool) or (
                revision < 1
            ):
                _refuse(PROBLEM_BAD_REQUEST, "revision must be a positive integer",
                        approval=refusal, recorded=False)
            if not isinstance(proposal_digest_sha256, str) or len(
                proposal_digest_sha256
            ) != 64 or any(ch not in "0123456789abcdef"
                           for ch in proposal_digest_sha256):
                _refuse(PROBLEM_BAD_REQUEST,
                        "proposal_digest_sha256 must be 64 lowercase hex",
                        approval=refusal, recorded=False)
            if expires_at is not None and (
                not isinstance(expires_at, int) or isinstance(expires_at, bool)
                or expires_at < 0
            ):
                _refuse(PROBLEM_BAD_REQUEST,
                        "expires_at must be null or a non-negative integer",
                        approval=refusal, recorded=False)
            now = self._now()
            if self._withdrawn(entry):
                # An interrupted cancel: nothing more is written for it.
                _refuse(PROBLEM_CANCELLATION_INCOMPLETE,
                        "local cancellation of request %s is INCOMPLETE (the"
                        " Mission carries its withdrawal marker); nothing more"
                        " is recorded for it. Run cancel again to complete it"
                        % request_ref, request_ref=request_ref,
                        approval=refusal, recorded=False)
            binding = self._binding(entry, revision, proposal_digest_sha256,
                                    expires_at, now)
            attempt = json_digest({"revision": revision,
                                   "proposal_digest_sha256": proposal_digest_sha256,
                                   "expires_at": expires_at})
            recorded = [r for r in entry["approval_refusals"]
                        if r["attempt_digest_sha256"] == attempt]
            duplicate = bool(recorded)
            stored = True
            if not duplicate:
                if len(entry["approval_refusals"]) >= (
                    store_module.MAX_APPROVAL_REFUSALS
                ):
                    stored = False
                else:
                    entry["approval_refusals"].append({
                        "attempt_digest_sha256": attempt, "revision": revision,
                        "proposal_digest_sha256": proposal_digest_sha256,
                        "expires_at": expires_at, "binding": binding,
                        "refused_at": now,
                        "problem": store_module.PROBLEM_APPROVAL_UNAUTHENTICATED,
                    })
                    self._store.save(document)
        details = dict(refusal)
        details.update({
            "ok": False, "status": "refused", "request_ref": request_ref,
            "binding": binding if not duplicate else recorded[0]["binding"],
            "duplicate": duplicate, "recorded": duplicate or stored,
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
        })
        return details

    def _withdrawn(self, entry):
        if entry["mission_id"] is None:
            return False
        try:
            mission = self._missions.get(entry["mission_id"])["record"]
        except MISSION_CORE_ERRORS:
            return False
        return mission.get("withdrawal") is not None

    def _binding(self, entry, revision, digest, expires_at, now):
        """How the attempt relates to the current revision. Diagnostic
        only: every binding is refused the same way."""
        if entry["mission_id"] is None:
            return store_module.BINDING_PROPOSAL_UNCONFIRMED
        try:
            mission = self._missions.get(entry["mission_id"])["record"]
        except MISSION_CORE_ERRORS:
            return store_module.BINDING_MISSION_UNAVAILABLE
        current = mission["revisions"][-1]
        if mission["state"] != mission_record.STATE_AWAITING_DECISION:
            return store_module.BINDING_NOT_PENDING
        if revision != mission["current_revision"]:
            return store_module.BINDING_STALE_REVISION
        if digest != current["proposal_digest_sha256"]:
            return store_module.BINDING_DIGEST_MISMATCH
        if expires_at is not None and expires_at <= now:
            return store_module.BINDING_EXPIRED
        return store_module.BINDING_MATCHES

    # -- operator-attested approval (Task 8, user decision) --------------

    def _pending_refs(self, document):
        pending = []
        for ref, entry in sorted(document["requests"].items()):
            if entry["state"] != store_module.STATE_OPEN or entry["mission_id"] is None:
                continue
            try:
                mission = self._missions.get(entry["mission_id"])["record"]
            except MISSION_CORE_ERRORS:
                continue
            if mission["state"] == mission_record.STATE_AWAITING_DECISION and (
                mission.get("withdrawal") is None
            ):
                pending.append(ref)
        return pending

    def present(self, request_ref=None):
        """Read-only: the ONE exact proposal to show the human. Without a
        reference it presents the single pending proposal, and refuses when
        there is none or more than one (an explicit reference is then
        required; it never guesses)."""
        document = self._store.load()
        if request_ref is None:
            pending = self._pending_refs(document)
            if not pending:
                _refuse(PROBLEM_NOTHING_PENDING,
                        "no pending proposal on this surface")
            if len(pending) > 1:
                _refuse(PROBLEM_AMBIGUOUS,
                        "%d pending proposals; name one request_ref explicitly"
                        % len(pending), candidates=pending)
            request_ref = pending[0]
        entry = self._record(document, request_ref)
        if entry["state"] != store_module.STATE_OPEN or entry["mission_id"] is None:
            _refuse(PROBLEM_BINDING_MISMATCH,
                    "request %s has no open, confirmed proposal" % request_ref)
        mission = self._mission(entry["mission_id"])["record"]
        if mission["state"] != mission_record.STATE_AWAITING_DECISION or (
            mission.get("withdrawal") is not None
        ):
            _refuse(PROBLEM_BINDING_MISMATCH,
                    "mission %s is not awaiting a decision" % entry["mission_id"])
        current = mission["revisions"][-1]
        target = current["proposal"]["requested_delivery_target"]
        return {
            "ok": True, "status": "presented", "request_ref": request_ref,
            "mission_id": mission["mission_id"],
            "revision": current["revision"],
            "proposal": copy.deepcopy(current["proposal"]),
            "proposal_digest_sha256": current["proposal_digest_sha256"],
            "approved_action_scope": sorted(
                current["proposal"]["requested_action_scope"]),
            "approved_delivery_targets": [] if target is None else [target],
            "latest_expires_at": self._now()
            + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS,
            "provenance_label": dict(PROVENANCE_LABEL),
            "residual_risk": mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK,
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
        }

    def _decision_recorded(self, mission_id, decision_id):
        """From durable Mission Core state: the recorded decision, False when
        Mission Core is readable and holds none, None when it is unreadable."""
        try:
            mission = self._missions.get(mission_id)["record"]
        except MISSION_CORE_ERRORS:
            return None
        for decision in mission["decisions"]:
            if decision["decision_id"] == decision_id:
                return decision
        return False

    def attest_approval(self, request_ref, mission_id, revision,
                        proposal_digest_sha256, approved_action_scope,
                        approved_delivery_targets, expires_at, relayed_reply,
                        relay_ref):
        """Record the Outer Operator's ATTESTATION that the human explicitly
        replied "approved" to exactly this proposal, and apply it through
        Mission Core's operator-attested APPROVE. Trusted by the user's
        declared policy; DI does not independently establish that the human
        sent it (residual risk recorded). One decision id per request, applied
        ONLY in the call that reserved it. A repeat of an APPLIED attestation
        returns the recorded outcome; a repeat of a RESERVED or HOLD one only
        reconciles Mission Core's durable record and never applies or mints."""
        # An explicit target, always: never guess which proposal a reply meant.
        if not isinstance(request_ref, str) or not request_ref or not isinstance(
            mission_id, str
        ) or not mission_id:
            _refuse(PROBLEM_AMBIGUOUS,
                    "an explicit request_ref and Mission id are required; a"
                    " reply is never applied to a guessed proposal")
        # The WHOLE reply must be an exact affirmative ("approved" or
        # "approve", case-insensitive, surrounding whitespace ignored).
        # Checked before any read or write; anything else is refused.
        if not isinstance(relayed_reply, str) or len(relayed_reply) > (
            store_module.MAX_RELAYED_REPLY_CHARS
        ) or not store_module.is_exact_affirmative(relayed_reply):
            _refuse(PROBLEM_NOT_AFFIRMATIVE,
                    "the relayed reply is not an exact affirmative: only the"
                    " whole reply %s (case-insensitive, surrounding whitespace"
                    " ignored) counts; nothing was recorded"
                    % " or ".join(repr(r) for r in store_module.AFFIRMATIVE_REPLIES))
        if not isinstance(relay_ref, str) or len(relay_ref) > (
            store_module.MAX_RELAY_REF_CHARS
        ):
            _refuse(PROBLEM_BAD_REQUEST, "relay_ref must be a short string")
        if not isinstance(approved_action_scope, list) or not isinstance(
            approved_delivery_targets, list
        ):
            _refuse(PROBLEM_BAD_REQUEST, "scope and targets must be lists")
        binding = {
            "mission_id": mission_id, "revision": revision,
            "proposal_digest_sha256": proposal_digest_sha256,
            "approved_action_scope": sorted(approved_action_scope),
            "approved_delivery_targets": sorted(approved_delivery_targets),
            "expires_at": expires_at,
        }
        with self._store.lock():
            document = self._store.load()
            entry = self._record(document, request_ref)
            if entry["state"] == store_module.STATE_CANCELLED:
                _refuse(PROBLEM_CANCELLED, CANCELLED_REASON, request_ref=request_ref)
            if entry["mission_id"] is None:
                _refuse(PROBLEM_UNCONFIRMED,
                        "the proposal is not confirmed in Mission Core yet")
            if mission_id != entry["mission_id"]:
                _refuse(PROBLEM_MISATTRIBUTED,
                        "request %s is bound to mission %s, not %s"
                        % (request_ref, entry["mission_id"], mission_id))
            existing = entry.get("attested_approval")
            if existing is not None and existing["state"] != (
                store_module.ATTESTED_REFUSED
            ):
                if existing["binding"] != binding:
                    _refuse(PROBLEM_ATTESTATION_CONFLICT,
                            "request %s already holds a %s attestation with a"
                            " different binding; nothing was recorded"
                            % (request_ref, existing["state"]))
                if existing["state"] == store_module.ATTESTED_APPLIED:
                    return self._attested_result(request_ref, entry, True)
                # RESERVED or HOLD found by a LATER call: whether an earlier
                # apply committed is unknown. Reconcile ONLY an existing
                # durable outcome; NEVER apply, NEVER mint.
                return self._reconcile_unresolved(document, entry, request_ref)
            else:
                self._require_current_binding(binding)
                decision_id = self._missions.mint_decision_id(
                    OPERATOR_ATTESTED_CONTEXT)
                entry["attested_approval"] = {
                    "state": store_module.ATTESTED_RESERVED,
                    "decision_id": decision_id, "binding": binding,
                    "relayed_reply": relayed_reply, "relay_ref": relay_ref,
                    "attested_at": self._now(), "outcome": None,
                    "provenance_label": dict(PROVENANCE_LABEL),
                    "residual_risk": mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK,
                    "evidence_status": store_module.EVIDENCE_STATUS,
                }
                self._store.save(document)
            # The FIRST and only application, in the same call that reserved.
            attestation = entry["attested_approval"]
            try:
                outcome = self._missions.apply_operator_attested_approval(
                    OPERATOR_ATTESTED_CONTEXT, decision_id, mission_id, revision,
                    proposal_digest_sha256, approved_action_scope,
                    approved_delivery_targets, expires_at)
            except mission_record.MissionError as exc:
                recorded = self._decision_recorded(mission_id, decision_id)
                if recorded is None:
                    attestation["state"] = store_module.ATTESTED_HOLD
                    self._store.save(document)
                    _refuse(PROBLEM_ATTESTATION_HOLD, HOLD_REASON,
                            request_ref=request_ref, decision_id=decision_id)
                if recorded is False:
                    attestation["state"] = store_module.ATTESTED_REFUSED
                    attestation["outcome"] = {"authorization_id": None,
                                              "problem": exc.problem}
                    self._store.save(document)
                    _refuse(PROBLEM_ATTESTATION_REFUSED,
                            "Mission Core refused the attested approval: %s" % exc,
                            mission_problem=exc.problem, request_ref=request_ref)
                outcome = {"authorization_id":
                           recorded["outcome"]["authorization_id"]}
            except Exception:
                # The commit may or may not have landed: HOLD, never a refusal
                # and never non-application.
                attestation["state"] = store_module.ATTESTED_HOLD
                self._store.save(document)
                _refuse(PROBLEM_ATTESTATION_HOLD, HOLD_REASON,
                        request_ref=request_ref, decision_id=decision_id)
            attestation["state"] = store_module.ATTESTED_APPLIED
            attestation["outcome"] = {
                "authorization_id": outcome["authorization_id"], "problem": None}
            self._store.save(document)
            return self._attested_result(request_ref, entry, False)

    def _reconcile_unresolved(self, document, entry, request_ref):
        """A RESERVED or HOLD attestation seen by a later call. Only an
        EXISTING durable outcome is reconciled: if Mission Core durably
        records this decision id, the attestation becomes APPLIED with that
        recorded outcome. If the decision is absent or Mission Core is
        unreadable, it stays on HOLD: nothing is applied, nothing is minted,
        and the absence is never read as non-application."""
        attestation = entry["attested_approval"]
        recorded = self._decision_recorded(entry["mission_id"],
                                           attestation["decision_id"])
        if recorded:
            attestation["state"] = store_module.ATTESTED_APPLIED
            attestation["outcome"] = {
                "authorization_id": recorded["outcome"]["authorization_id"],
                "problem": None}
            self._store.save(document)
            result = self._attested_result(request_ref, entry, True)
            result["reconciled_from_durable_record"] = True
            return result
        if attestation["state"] != store_module.ATTESTED_HOLD:
            attestation["state"] = store_module.ATTESTED_HOLD
            self._store.save(document)
        _refuse(PROBLEM_ATTESTATION_HOLD, HOLD_REASON, request_ref=request_ref,
                decision_id=attestation["decision_id"])

    def _require_current_binding(self, binding):
        mission = self._mission(binding["mission_id"])["record"]
        current = mission["revisions"][-1]
        target = current["proposal"]["requested_delivery_target"]
        now = self._now()
        for ok, why in (
            (mission["state"] == mission_record.STATE_AWAITING_DECISION
             and mission.get("withdrawal") is None, "mission is not pending"),
            (binding["revision"] == current["revision"], "stale revision"),
            (binding["proposal_digest_sha256"] == current["proposal_digest_sha256"],
             "proposal digest does not match the current revision"),
            (binding["approved_action_scope"]
             == sorted(current["proposal"]["requested_action_scope"]),
             "action scope is not exactly the requested scope"),
            (binding["approved_delivery_targets"]
             == ([] if target is None else [target]),
             "delivery targets are not exactly the requested target"),
            (isinstance(binding["expires_at"], int)
             and now < binding["expires_at"]
             <= now + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS,
             "expiry is past or beyond the %d-second limit"
             % mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS),
        ):
            if not ok:
                _refuse(PROBLEM_BINDING_MISMATCH,
                        "attested approval refused: %s" % why,
                        mission_id=binding["mission_id"])

    def _attested_result(self, request_ref, entry, idempotent):
        attestation = entry["attested_approval"]
        return {
            "ok": True, "status": "approved_by_operator_attestation",
            "request_ref": request_ref,
            "mission_id": entry["mission_id"],
            "binding": copy.deepcopy(attestation["binding"]),
            "decision_id": attestation["decision_id"],
            "authorization_id": attestation["outcome"]["authorization_id"],
            "idempotent": idempotent,
            "provenance_label": dict(PROVENANCE_LABEL),
            "residual_risk": mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK,
            "evidence_status": store_module.EVIDENCE_STATUS,
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
        }

    # -- control: the caller's own pending proposal only -----------------

    def cancel(self, request_ref, control_capability):
        with self._store.lock():
            document = self._store.load()
            entry = self._record(document, request_ref)
            if entry["state"] == store_module.STATE_CANCELLED:
                _refuse(PROBLEM_CANCELLED, CANCELLED_REASON,
                        request_ref=request_ref)
            if entry["mission_id"] is None:
                _refuse(PROBLEM_UNCONFIRMED,
                        "the proposal is not confirmed in Mission Core yet;"
                        " run recover first", request_ref=request_ref)
            mission = self._require_own_pending_proposal(entry)
            now = self._now()
            authority = store_module.ProposalControlAuthority(
                document["control_capabilities"])
            # An interrupted cancel: Mission Core already recorded the
            # withdrawal with this request's key before the capability's
            # original expiry, which Mission Core binds and enforces. Only
            # binding and single use are checked to COMPLETE it; Mission Core
            # re-verifies the key itself and answers idempotently.
            consume = (authority.consume_to_complete
                       if mission.get("withdrawal") is not None
                       else authority.validate_and_consume)
            ok, problem, detail = consume(
                control_capability, request_ref,
                store_module.ACTION_CANCEL_PENDING_PROPOSAL, 1, now)
            if not ok:
                _refuse(PROBLEM_CONTROL_CAPABILITY,
                        "cancel of local request %s refused: %s; knowing a"
                        " request_ref or a Mission id never permits control"
                        % (request_ref, detail),
                        capability_problem=problem, request_ref=request_ref)
            # The capability is consumed only in memory so far. Mission Core
            # records the withdrawal marker FIRST (idempotent); if it
            # refuses, nothing here is saved and nothing is spent.
            try:
                withdrawn = self._missions.withdraw_proposal(
                    entry["mission_id"], entry["mission_request_id"],
                    control_capability, LOCAL_CALLER_CONTEXT)
            except MISSION_CORE_ERRORS as exc:
                _refuse(PROBLEM_OUT_OF_SCOPE,
                        "%s (Mission Core: %s)" % (OUT_OF_SCOPE_REASON, exc),
                        mission_problem=getattr(exc, "problem", None),
                        request_ref=request_ref)
            entry["state"] = store_module.STATE_CANCELLED
            entry["cancellation"] = {
                "cancelled_at": now, "mission_id": entry["mission_id"],
                "revision": 1,
                "proposal_digest_sha256": entry["proposal_digest_sha256"],
            }
            self._store.save(document)
        return {
            "ok": True, "status": "cancelled", "request_ref": request_ref,
            "cancelled": "local request %s" % request_ref,
            "surface_state": store_module.STATE_CANCELLED,
            "cancellation": copy.deepcopy(entry["cancellation"]),
            "mission_id": entry["mission_id"],
            "mission_state": withdrawn["state"],
            "mission_withdrawal": withdrawn["withdrawal"],
            "effect": withdrawal_effect(request_ref, entry["mission_id"],
                                        withdrawn["state"]),
            "delivery_authority": DELIVERY_AUTHORITY, "dispatch": DISPATCH,
        }

    def _require_own_pending_proposal(self, entry):
        mission = self._mission(entry["mission_id"])["record"]
        first = mission["revisions"][0]["provenance"]
        own = (
            mission["request_id"] == entry["mission_request_id"]
            and first["transport"] == TRANSPORT
            and first["principal_kind"]
            == mission_record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER
            and mission["revisions"][0]["proposal_digest_sha256"]
            == entry["proposal_digest_sha256"]
        )
        pending = (
            mission["state"] == mission_record.STATE_AWAITING_DECISION
            and mission["current_revision"] == 1
            and not mission["decisions"]
            and not mission["authorization_ids"]
        )
        if not (own and pending):
            _refuse(PROBLEM_OUT_OF_SCOPE, OUT_OF_SCOPE_REASON,
                    mission_id=entry["mission_id"],
                    mission_state=mission["state"],
                    current_revision=mission["current_revision"])
        return mission


MISSION_CORE_ERRORS = (mission_record.MissionError, mission_store.MissionStoreError)


def dumps(value):
    return json.dumps(value, indent=2, sort_keys=True)
