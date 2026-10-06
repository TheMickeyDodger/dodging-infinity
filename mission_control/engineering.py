"""The engineering dispatch bootstrap (Task 8, slice S-IV; ledger R2-3,
R2-4 initial accounting, R2-12 obligations).

``MissionControl.dispatch`` turns ONE authorized Mission into exactly ONE
Mission-origin workflow record the Runtime can later claim, in the
canonical order the design fixes:

1. the hard missing-dependency gate (``mission_control.integration``):
   while slice S-V's guards are absent, refuse ``mission_dependency_missing``
   with zero effects, whatever the configuration;
2. preconditions re-read at call time from the Mission Core: the Mission
   is AUTHORIZED, its live authorization is valid for the engineering
   action at the CURRENT revision and proposal digest and unexpired, the
   issuing decision's provenance is sufficient (a connector-credential-only
   decision refuses), the proposal names a repository, an approved
   baseline and a proof contract that carries the MANDATORY integration
   obligations (custom contracts that omit them refuse), the Mission is
   not terminally closed;
3. the proof contract is activated if the current revision's is not;
4. readiness is fresh for every resource the contract requires — the
   bootstrap holds no probe, so stale readiness REFUSES naming the
   canonical ``observe_resource_readiness`` operation;
5. the Mission-side fence + initial budget reservation: ONE canonical
   ``reserve_engagement`` operation (durable, journaled) naming the
   deterministic workflow id and, through the activation, the
   authorization digest; the initial dispatch consumes no continuation
   attempt (initial-dispatch accounting is stated on the operation);
6. ONE locked ``add_workflow`` save inserting the Mission-origin record
   in its admission state with the validated linkage, the engagement
   reservation reference and the retention placeholder slice S-V
   enforces; nothing else.

BOOTSTRAP ADMISSION (Supervisor refinement B): the preconditions of step
2 are a snapshot. The bootstrap re-admits — authority live at the current
revision and digest, provenance sufficient, the Mission not terminal, not
held, not cancelled — IMMEDIATELY before each Mission mutation
(activation, the fence reservation) and AGAIN under the publication lock
(the workflow lock with the Mission lock nested inside, the gate's order)
before ``add_workflow``. A hold or cancel seen there yields zero
mutations; an EDIT, expiry, hold or cancel landing after the fence and
before the publication leaves NO stale row and keeps the fence as the
recoverable uncertainty above.

SOURCE AVAILABILITY (Supervisor item E): the store's typed
``MissionStoreError`` (unreadable, invalid or saturated content) is a
REVERSIBLE ``mission_control_source_unavailable`` refusal with zero
effects — never reported as an unknown Mission, never as revocation.

Idempotency and uncertainty: a second call for the same activation finds
the fence already reserved. When the workflow row exists the call is
idempotent (``ok`` with ``idempotent`` true and the same ids); when it
does NOT exist — a crash between the fence and the save, or a later
terminal pruning — the fence is recoverable UNCERTAINTY: the call refuses
``mission_control_engagement_uncertain``, never recreates the row, and
says so. No canonical proof exists in this candidate that a workflow was
never published for that fence (the workflow store keeps no tombstones),
so recreation is refused permanently; the human-visible way forward is an
EDIT plus a fresh approval, which activates a new contract and a new
fence.

This module composes: it never spawns, runs git, or holds a second truth
store. It reads and writes the Mission Core through its service and the
workflow store through its canonical locked save.
"""

import collections
import hashlib

from mission import authorization as mission_authorization
from mission import record as mission_record
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store
from mission_control import authority as mission_control_authority
from mission_control import gate as mission_gate
from mission_control import integration
from mission_control import readiness as mission_readiness
from workflow_authority import canonical as workflow_canonical
from workflow_authority import digest as workflow_digest
from workflow_authority import record as workflow_record
from workflow_authority import store as workflow_store

PHASE_BOOTSTRAP = "bootstrap"

PROBLEM_UNKNOWN_MISSION = "mission_control_unknown_mission"
PROBLEM_NOT_AUTHORIZED = "mission_control_mission_not_authorized"
PROBLEM_NO_LIVE_AUTHORIZATION = "mission_control_no_live_authorization"
PROBLEM_NO_REPOSITORY = "mission_control_no_repository"
PROBLEM_BASELINE_MISSING = "mission_control_baseline_missing"
PROBLEM_CONTRACT_MISSING = "mission_control_contract_missing"
PROBLEM_CONTRACT_INCOMPLETE = "mission_control_contract_incomplete"
PROBLEM_MISSION_TERMINAL = "mission_control_mission_terminal"
PROBLEM_READINESS_STALE = "mission_control_readiness_stale"
PROBLEM_ENGAGEMENT_UNCERTAIN = "mission_control_engagement_uncertain"
PROBLEM_WORKFLOW_STORE = "mission_control_workflow_store"
PROBLEM_RECORD_REFUSED = "mission_control_record_refused"
PROBLEM_SOURCE_UNAVAILABLE = mission_gate.PROBLEM_SOURCE_UNAVAILABLE
PROBLEM_HOLD_ACTIVE = mission_gate.PROBLEM_HOLD_ACTIVE
PROBLEM_CANCEL_REQUESTED = mission_gate.PROBLEM_CANCEL_REQUESTED
PROBLEM_REVISION_SUPERSEDED = mission_gate.PROBLEM_REVISION_SUPERSEDED

# The bootstrap admission points, named in each refusal.
ADMISSION_ACTIVATION = "activation"
ADMISSION_FENCE = "fence"
ADMISSION_PUBLICATION = "publication"

# The mandatory integration obligations every dispatched contract must
# declare as requirement keys (R2-12): engineering verified, reviewer
# approve, candidate identity; plus the delivery obligation when the
# Mission names a delivery target.
MANDATORY_REQUIREMENT_KEYS = (
    "engineering_verified", "reviewer_approve", "candidate_identity",
)
DELIVERY_REQUIREMENT_KEY = "delivery_recorded"
# Task 8 S-VI: a Mission naming a delivery target also declares the
# client-confirmed DELIVERY DECISION obligation: the decision is recorded
# and accepted as its evidence before any delivery authority is minted.
DELIVERY_DECISION_REQUIREMENT_KEY = "delivery_decision"

ENGINEERING_ACTIONS = (
    mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
    mission_record.ACTION_SCOPE_REPOSITORY_READ,
)

WORKFLOW_ID_PREFIX = "wf-m-"
WORKFLOW_ID_HEX_CHARS = 26
# The Mission-origin workflow's approval validity mirrors the Mission
# authorization's expiry when it has one; otherwise this bound applies.
DEFAULT_APPROVAL_VALIDITY_SECONDS = 30 * 24 * 3600
HANDOFF_REVISION = 1

NOT_DECLARED = "not declared by the Mission proposal"

RESULT_KEYS = (
    "ok", "problem", "detail", "phase", "mission_id", "revision",
    "workflow_id", "engagement_id", "idempotent", "missing_guards",
    "admission_point", "effects",
)

# The bootstrap's durable effects, each reported TRUTHFULLY in every
# result (Supervisor item E): ``none`` — proven not attempted;
# ``written`` — the canonical operation returned, so the effect is
# durable; ``unknown`` — the operation was in flight when the source
# failed, so the save outcome is not known from this call (a retry
# re-reads the durable stores and recovers without recreating anything).
EFFECT_ACTIVATION = "activation"
EFFECT_FENCE = "fence"
EFFECT_ROW = "row"
EFFECT_NAMES = (EFFECT_ACTIVATION, EFFECT_FENCE, EFFECT_ROW)
EFFECT_NONE = "none"
EFFECT_WRITTEN = "written"
EFFECT_UNKNOWN = "unknown"
EFFECT_STATES = (EFFECT_NONE, EFFECT_UNKNOWN, EFFECT_WRITTEN)

# What a bootstrap admission re-reads and compares against (refinement B).
_Expected = collections.namedtuple(
    "_Expected", ("mission_id", "revision", "authorization_id",
                  "proposal_digest", "delivery_target"))


class _Effects(object):
    """The per-call effect ledger: ``attempt(name)`` right before the
    canonical operation, ``done(name)`` right after it returns."""

    def __init__(self):
        self.states = dict((name, EFFECT_NONE) for name in EFFECT_NAMES)

    def attempt(self, name):
        self.states[name] = EFFECT_UNKNOWN

    def done(self, name):
        self.states[name] = EFFECT_WRITTEN

    def as_dict(self):
        return dict(self.states)

    @property
    def proven_zero(self):
        return all(state == EFFECT_NONE for state in self.states.values())


def _result(ok, problem=None, detail=None, mission_id=None, revision=None,
            workflow_id=None, engagement_id=None, idempotent=False,
            missing_guards=(), admission_point=None, effects=None):
    result = {
        "ok": ok, "problem": problem, "detail": detail, "phase": PHASE_BOOTSTRAP,
        "mission_id": mission_id, "revision": revision,
        "workflow_id": workflow_id, "engagement_id": engagement_id,
        "idempotent": idempotent, "missing_guards": list(missing_guards),
        "admission_point": admission_point,
        "effects": (_Effects().as_dict() if effects is None
                    else effects.as_dict()),
    }
    assert tuple(sorted(result)) == tuple(sorted(RESULT_KEYS))
    return result


def _effects_sentence(effects):
    """The truthful effect statement a failure detail ends with."""
    states = effects.as_dict()
    if effects.proven_zero:
        return "nothing was written (proven: no operation was attempted)"
    written = sorted(n for n, s in states.items() if s == EFFECT_WRITTEN)
    unknown = sorted(n for n, s in states.items() if s == EFFECT_UNKNOWN)
    parts = []
    if written:
        parts.append("durable progress retained: %s" % ", ".join(written))
    if unknown:
        parts.append("save outcome unknown for: %s (a retry re-reads the"
                     " durable stores and never recreates execution)"
                     % ", ".join(unknown))
    return "; ".join(parts)


def effects_summary(result):
    """One of ``proven_zero``, ``retained`` (durable progress is known
    written and stays), ``unknown`` (a save outcome is not known)."""
    states = result["effects"].values()
    if all(state == EFFECT_NONE for state in states):
        return "proven_zero"
    if any(state == EFFECT_UNKNOWN for state in states):
        return "unknown"
    return "retained"


def deterministic_workflow_id(mission_id, activation_id):
    """The one workflow id an activation can ever be dispatched under."""
    digest = hashlib.sha256(
        ("%s:%s" % (mission_id, activation_id)).encode("utf-8")).hexdigest()
    return WORKFLOW_ID_PREFIX + digest[:WORKFLOW_ID_HEX_CHARS]


def contract_obligation_problems(contract, delivery_target):
    """The mandatory obligations ``contract`` omits, in fixed order."""
    declared = set(requirement["key"] for requirement in contract["requirements"])
    missing = [key for key in MANDATORY_REQUIREMENT_KEYS if key not in declared]
    if delivery_target is not None:
        for key in (DELIVERY_DECISION_REQUIREMENT_KEY, DELIVERY_REQUIREMENT_KEY):
            if key not in declared:
                missing.append(key)
    return missing


def authority_content(proposal):
    """The seven authority-content sections rendered from the proposal —
    each from the field that carries it, or an explicit statement that
    the proposal declares none. Nothing is fabricated."""
    scope = ", ".join(proposal["requested_action_scope"])
    delivery = proposal.get("requested_delivery_target") or "none"
    return {
        "objective": proposal["objective"],
        "constraints": ("requested action scope: %s; requested delivery"
                        " target: %s" % (scope, delivery)),
        "rules": ("The target repository's own instructions never override"
                  " the control authority; closure is decided by the"
                  " Mission's proof contract, never by the target's report."),
        "desired_outcome": proposal["requested_scope"],
        "acceptance": ("the Mission proof contract's requirements (%s)"
                       % ", ".join(sorted(
                           r["key"] for r in proposal["proof_contract"]["requirements"]))),
        "unresolved_questions": NOT_DECLARED,
        "execution_scope": proposal["target_context"] or NOT_DECLARED,
    }


def handoff_text(proposal):
    """The byte-exact handoff the target engine receives: the proposal's
    own words under fixed headers."""
    return "\n".join([
        "OBJECTIVE", proposal["objective"], "",
        "SCOPE", proposal["requested_scope"], "",
        "CONTEXT", proposal["target_context"] or NOT_DECLARED,
    ])


class MissionControl(object):
    """The composition object: one Mission service, one workflow store,
    one control repository. Rebuilt freely (reconnect, restart): every
    decision is re-read from the durable stores at call time."""

    def __init__(self, service, workflow_store_directory, control_realpath,
                 policy_digest_fn=None, readiness_producer=None):
        self._service = service
        self._workflow_store = workflow_store.WorkflowStore(workflow_store_directory)
        self._control_realpath = control_realpath
        self._policy_digest = policy_digest_fn or workflow_digest.control_policy_digest
        # Task 8 S-VII: the engineering-runtime readiness producer
        # (``mission_control.readiness.RuntimeReadinessProducer``) the
        # production composition wires; None leaves readiness to whatever
        # observation already exists (the bootstrap then refuses stale).
        self._readiness = readiness_producer

    @property
    def workflow_store(self):
        return self._workflow_store

    # -- the bootstrap ------------------------------------------------------

    def dispatch(self, mission_id, context):
        """Bootstrap the engineering dispatch of ``mission_id`` for the
        authenticated ``context``; returns a closed result dictionary and
        never raises for a refusal."""
        # The guards must be present AND wired on THIS service instance.
        missing = integration.missing_guards(self._service)
        if missing:
            refusal = integration.dependency_refusal(missing)
            return _result(False, refusal["problem"], refusal["detail"],
                           mission_id=mission_id, missing_guards=missing)
        mission_record.require_context(context)
        effects = _Effects()
        try:
            return self._dispatch(mission_id, context, effects)
        except mission_store.MissionStoreError as exc:
            return _result(False, PROBLEM_SOURCE_UNAVAILABLE,
                           "the Mission source is unavailable (%s: %s); %s"
                           % (exc.problem or type(exc).__name__, exc,
                              _effects_sentence(effects)),
                           mission_id=mission_id, effects=effects)
        except mission_record.MissionError as exc:
            return _result(False, exc.problem,
                           "%s; %s" % (exc, _effects_sentence(effects)),
                           mission_id=mission_id, effects=effects)
        except workflow_record.RecordError as exc:
            return _result(False, PROBLEM_RECORD_REFUSED,
                           "%s: %s; %s" % (exc.problem, exc,
                                           _effects_sentence(effects)),
                           mission_id=mission_id, effects=effects)
        except workflow_store.StoreError as exc:
            return _result(False, PROBLEM_WORKFLOW_STORE,
                           "%s; %s" % (exc, _effects_sentence(effects)),
                           mission_id=mission_id, effects=effects)

    def _dispatch(self, mission_id, context, effects):
        service = self._service
        try:
            stored = service.get(mission_id)
        except mission_record.MissionError as exc:
            if exc.problem == mission_authorization.PROBLEM_UNKNOWN_MISSION:
                return _result(False, PROBLEM_UNKNOWN_MISSION, str(exc),
                               mission_id=mission_id)
            raise
        record = stored["record"]
        revision = record["current_revision"]
        if record["state"] != mission_record.STATE_AUTHORIZED:
            return _result(False, PROBLEM_NOT_AUTHORIZED,
                           "mission %s is %s, not AUTHORIZED"
                           % (mission_id, record["state"]),
                           mission_id=mission_id, revision=revision)
        authorization_id = stored["live_authorization_id"]
        if authorization_id is None:
            return _result(False, PROBLEM_NO_LIVE_AUTHORIZATION,
                           "mission %s has no live authorization for revision %d"
                           % (mission_id, revision),
                           mission_id=mission_id, revision=revision)
        current = record["revisions"][-1]
        proposal = current["proposal"]
        check = service.validate_authorization(
            authorization_id, mission_id, revision,
            required_actions=ENGINEERING_ACTIONS,
            required_delivery_target=proposal.get("requested_delivery_target"),
            expected_proposal_digest=current["proposal_digest_sha256"])
        if not check.valid and check.problem in (mission_store.PROBLEM_STORE_UNREADABLE,
                                                 mission_store.PROBLEM_STORE_FULL):
            # Task 8 S-VII: the validator could not read the store — the
            # SAME typed source-unavailable refusal as every other bootstrap
            # read, never an authorization verdict. Nothing was written.
            return _result(False, PROBLEM_SOURCE_UNAVAILABLE,
                           "the Mission source is unavailable (%s: %s); %s"
                           % (check.problem, check.detail, _effects_sentence(effects)),
                           mission_id=mission_id, revision=revision, effects=effects)
        if not check.valid:
            return _result(False, check.problem, check.detail,
                           mission_id=mission_id, revision=revision)
        provenance = mission_control_authority.consequential_decision_provenance(
            stored, authorization_id)
        if not provenance["sufficient"]:
            return _result(False, provenance["problem"], provenance["detail"],
                           mission_id=mission_id, revision=revision)
        if proposal.get("repository_url") is None:
            return _result(False, PROBLEM_NO_REPOSITORY,
                           "the proposal names no repository; nothing can be"
                           " dispatched", mission_id=mission_id, revision=revision)
        baseline = proposal.get("baseline")
        if baseline is None:
            return _result(False, PROBLEM_BASELINE_MISSING,
                           "the proposal declares no approved baseline; the"
                           " bootstrap never chooses one the human did not see",
                           mission_id=mission_id, revision=revision)
        contract = proposal.get("proof_contract")
        if contract is None:
            return _result(False, PROBLEM_CONTRACT_MISSING,
                           "the proposal carries no proof contract",
                           mission_id=mission_id, revision=revision)
        omitted = contract_obligation_problems(
            contract, proposal.get("requested_delivery_target"))
        if omitted:
            return _result(False, PROBLEM_CONTRACT_INCOMPLETE,
                           "the proof contract omits the mandatory integration"
                           " obligations: %s" % ", ".join(omitted),
                           mission_id=mission_id, revision=revision)
        state = service.get_state(mission_id)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return _result(False, PROBLEM_MISSION_TERMINAL,
                           "mission %s is %s" % (mission_id, state["progress"]),
                           mission_id=mission_id, revision=revision)
        # Task 8 S-VII (item E): the workflow store must be READABLE and
        # have ROOM for the row BEFORE the first Mission mutation — an
        # unavailable workflow store then refuses with zero effects
        # (reversible), never leaving an activation or a fence behind it.
        # (A stored row of this Mission's current revision — an idempotent
        # re-call — skips the room check; the fenced step answers it.)
        # A load failure raises StoreError: ``dispatch`` answers it.
        workflows = self._workflow_store.load()
        own = any(
            (row.get(workflow_record.MISSION_AUTHORITY_KEY) or {}).get("mission_id")
            == mission_id
            and row[workflow_record.MISSION_AUTHORITY_KEY].get("revision") == revision
            for row in workflows["workflows"].values())
        if not own and not workflow_store.has_room(
            workflows, now=service.now(),
            protected=mission_gate.canonically_protected(service)
        ):
            return _result(False, PROBLEM_WORKFLOW_STORE,
                           "the workflow store has no room for the row (%s); nothing"
                           " was written" % workflow_store.PROBLEM_STORE_FULL,
                           mission_id=mission_id, revision=revision)
        # 3. Activate the current revision's contract when it is not the
        # one activated (the canonical activation operation; its own
        # refusals surface as MissionErrors).
        contract_view = state["contract"]
        expected = _Expected(mission_id, revision, authorization_id,
                             current["proposal_digest_sha256"],
                             proposal.get("requested_delivery_target"))
        if not contract_view["active"] or contract_view["revision"] != revision:
            # Bootstrap admission immediately before the first mutation.
            refusal = self._bootstrap_admission(expected, ADMISSION_ACTIVATION)
            if refusal is not None:
                return refusal
            operation_id = service.mint_state_operation_id(context)
            effects.attempt(EFFECT_ACTIVATION)
            service.activate_proof_contract(mission_id, operation_id,
                                            state["sequence"], context)
            effects.done(EFFECT_ACTIVATION)
            state = service.get_state(mission_id)
        if not state["contract"]["authority_live"]:
            return _result(False, PROBLEM_NOT_AUTHORIZED,
                           "the activated contract's authority is not live (%s)"
                           % state["contract"]["problem"],
                           mission_id=mission_id, revision=revision,
                           effects=effects)
        activation_id = state["contract"]["activation_id"]
        # 4. Readiness must be fresh for every required resource. Task 8
        # S-VII: when the engineering Runtime is a required resource and its
        # readiness is not fresh, the producer probes the Runtime NOW and
        # records the canonical observation (READY or NOT_READY, exactly as
        # probed) before the check below reads it.
        readiness = state["readiness"]
        if self._readiness is not None and readiness is not None and (
            readiness["resources"].get(mission_readiness.ENGINEERING_RUNTIME_RESOURCE)
            == mission_state.READINESS_NOT_READY
        ):
            self._readiness.observe(mission_id, context)
            state = service.get_state(mission_id)
            readiness = state["readiness"]
        if readiness is not None and not readiness["satisfied"]:
            stale = sorted(key for key, status in readiness["resources"].items()
                           if status != mission_state.READINESS_READY)
            return _result(False, PROBLEM_READINESS_STALE,
                           "resource readiness is not fresh for %s; refresh it"
                           " through the canonical observe_resource_readiness"
                           " operation and retry" % ", ".join(stale),
                           mission_id=mission_id, revision=revision,
                           effects=effects)
        workflow_id = deterministic_workflow_id(mission_id, activation_id)
        # 5 + 6. The fence + initial reservation and the one row save,
        # BOTH under the WORKFLOW lock (the Mission lock nests inside it
        # for the reservation and the publication admission — the gate's
        # lock order). Concurrent bootstraps therefore serialize here: the
        # one that loses the fence re-reads the Mission state under the
        # lock, finds the fence, and answers from the row the winner has
        # already saved — idempotent, never a false uncertainty and never
        # a second reservation.
        with workflow_store.exclusive_store_lock(self._workflow_store.directory):
            workflows = self._workflow_store.load()
            state = service.get_state(mission_id)
            engagements = [e for e in mission_state.engagements_of(state["record"] or {})
                           if e["activation_id"] == activation_id]
            if engagements:
                return self._after_fence(mission_id, revision, workflow_id,
                                         engagements[0], effects, workflows)
            # Bootstrap admission immediately before the fence.
            refusal = self._bootstrap_admission(expected, ADMISSION_FENCE)
            if refusal is not None:
                refusal["effects"] = effects.as_dict()
                return refusal
            operation_id = service.mint_state_operation_id(context)
            effects.attempt(EFFECT_FENCE)
            try:
                outcome = service.reserve_engagement(
                    mission_id, operation_id, state["sequence"], workflow_id, 1,
                    context)
            except mission_record.MissionError as exc:
                if exc.problem in (mission_state.PROBLEM_ENGAGEMENT_FENCE,
                                   mission_state_service.PROBLEM_STALE_SEQUENCE):
                    # Another writer moved the state: answer from the
                    # durable stores, never by a second reservation. This
                    # call's own reservation was refused before any write.
                    effects.states[EFFECT_FENCE] = EFFECT_NONE
                    fresh = service.get_state(mission_id)
                    engagements = [
                        e for e in mission_state.engagements_of(fresh["record"] or {})
                        if e["activation_id"] == activation_id]
                    if engagements:
                        return self._after_fence(mission_id, revision, workflow_id,
                                                 engagements[0], effects, workflows)
                raise
            effects.done(EFFECT_FENCE)
            reference = {
                "engagement_id": outcome["engagement_id"],
                "engagement_sequence": outcome["engagement_sequence"],
                "operation_id": operation_id,
                "reserved_at": service.now(),
            }
            return self._publish(stored, state, mission_id, revision, workflow_id,
                                 reference, authorization_id, expected, effects,
                                 workflows)

    def _bootstrap_admission(self, expected, point):
        """The re-admission before a bootstrap mutation (refinement B):
        the Mission still AUTHORIZED at the expected revision, the same
        live authorization valid for the engineering action at the same
        proposal digest and delivery target, provenance sufficient, the
        Mission not terminal, not held, not cancelled — all re-read from
        the durable source at this instant. Returns None (admitted) or
        the closed refusal naming ``point``. Raises the store's typed
        error for an unavailable source (the caller turns it into the
        reversible refusal)."""
        service = self._service
        mission_id = expected.mission_id
        common = {"mission_id": mission_id, "revision": expected.revision,
                  "admission_point": point}
        try:
            stored = service.get(mission_id)
        except mission_record.MissionError as exc:
            if mission_gate.is_unknown_mission(exc):
                return _result(False, PROBLEM_UNKNOWN_MISSION, str(exc), **common)
            raise
        record = stored["record"]
        if record["current_revision"] != expected.revision:
            return _result(False, PROBLEM_REVISION_SUPERSEDED,
                           "mission %s moved to revision %d before the %s;"
                           " the bootstrap for revision %d authorizes nothing"
                           % (mission_id, record["current_revision"], point,
                              expected.revision), **common)
        if record["state"] != mission_record.STATE_AUTHORIZED:
            return _result(False, PROBLEM_NOT_AUTHORIZED,
                           "mission %s is %s, not AUTHORIZED, at the %s"
                           % (mission_id, record["state"], point), **common)
        if stored["live_authorization_id"] != expected.authorization_id:
            return _result(False, PROBLEM_NO_LIVE_AUTHORIZATION,
                           "authorization %s is no longer the live"
                           " authorization of mission %s at the %s"
                           % (expected.authorization_id, mission_id, point),
                           **common)
        check = service.validate_authorization(
            expected.authorization_id, mission_id, expected.revision,
            required_actions=ENGINEERING_ACTIONS,
            required_delivery_target=expected.delivery_target,
            expected_proposal_digest=expected.proposal_digest)
        if not check.valid:
            if check.problem == mission_authorization.PROBLEM_STORE_UNREADABLE:
                raise mission_store.MissionStoreError(
                    check.detail, mission_store.PROBLEM_STORE_UNREADABLE)
            return _result(False, check.problem, check.detail, **common)
        provenance = mission_control_authority.consequential_decision_provenance(
            stored, expected.authorization_id)
        if not provenance["sufficient"]:
            return _result(False, provenance["problem"], provenance["detail"],
                           **common)
        state = service.get_state(mission_id)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return _result(False, PROBLEM_MISSION_TERMINAL,
                           "mission %s is %s at the %s"
                           % (mission_id, state["progress"], point), **common)
        # Slice S-V's canonical controls through the WIRED service (the
        # dependency gate in ``dispatch`` already refused an unwired one).
        view = service.mission_controls(mission_id)
        if view.get("cancel_requested"):
            return _result(False, PROBLEM_CANCEL_REQUESTED,
                           "mission %s has a cancel request at the %s; it is"
                           " sticky and nothing is mutated"
                           % (mission_id, point), **common)
        if view.get("hold_active"):
            return _result(False, PROBLEM_HOLD_ACTIVE,
                           "mission %s is on hold at the %s; nothing is"
                           " mutated until the hold is released"
                           % (mission_id, point), **common)
        return None

    def _after_fence(self, mission_id, revision, workflow_id, engagement,
                     effects, workflows):
        """The fence is already reserved for this activation: idempotent
        when the row exists (``workflows`` is the document loaded under
        the workflow lock the caller holds), uncertainty when it does
        not."""
        if workflow_id in workflows["workflows"]:
            return _result(True, mission_id=mission_id, revision=revision,
                           workflow_id=workflow_id,
                           engagement_id=engagement["engagement_id"],
                           idempotent=True, effects=effects)
        return _result(
            False, PROBLEM_ENGAGEMENT_UNCERTAIN,
            "mission %s reserved engagement %s for workflow %s but no such"
            " workflow row exists (a crash before its save, or a later terminal"
            " pruning); the row is never recreated — an EDIT plus a fresh"
            " approval starts a new activation and a new fence"
            % (mission_id, engagement["engagement_id"], workflow_id),
            mission_id=mission_id, revision=revision, workflow_id=workflow_id,
            engagement_id=engagement["engagement_id"], effects=effects)

    def _publish(self, stored, state, mission_id, revision, workflow_id,
                 reference, authorization_id, expected, effects, workflows):
        """The one row save, under the workflow lock the caller holds
        (``workflows`` is its loaded document)."""
        record = stored["record"]
        current = record["revisions"][-1]
        proposal = current["proposal"]
        target = workflow_canonical.canonicalize_repository_url(
            proposal["repository_url"])
        issued = [a for a in stored["authorizations"]
                  if a["authorization_id"] == authorization_id][0]
        decision_id = [d["decision_id"] for d in record["decisions"]
                       if (d.get("outcome") or {}).get("authorization_id")
                       == authorization_id][0]
        now = self._service.now()
        expires_at = issued["expires_at"]
        if expires_at is None:
            expires_at = now + DEFAULT_APPROVAL_VALIDITY_SECONDS
        entry = workflow_record.new_mission_origin_record(
            workflow_id=workflow_id,
            human_intent=proposal["objective"],
            repository_realpath=self._control_realpath,
            policy_digest_sha256=self._policy_digest(self._control_realpath),
            canonical_host=target.host,
            owner=target.owner,
            repo=target.repo,
            canonical_url=target.canonical_url,
            baseline_ref=proposal["baseline"]["ref"],
            baseline_commit_sha=proposal["baseline"]["commit_sha"],
            authority_content=authority_content(proposal),
            mission_revision=revision,
            mission_authority={
                "mission_id": mission_id,
                "revision": revision,
                "authorization_id": authorization_id,
                "decision_id": decision_id,
                "authorization_digest_sha256": issued["authorization_digest_sha256"],
            },
            mission_engagement=dict(reference),
            created_at=now,
            expires_at=expires_at,
            handoff_revision=HANDOFF_REVISION,
            handoff_text=handoff_text(proposal),
        )
        if workflow_id in workflows["workflows"]:
            return _result(True, mission_id=mission_id, revision=revision,
                           workflow_id=workflow_id,
                           engagement_id=reference["engagement_id"],
                           idempotent=True, effects=effects)
        # Publication admission (refinement B): under the workflow lock
        # WITH the Mission lock nested inside — the gate's lock order —
        # so no Mission write can land between this read and the row
        # save. A refusal here writes NO row; the fence stays as the
        # recoverable uncertainty ``_after_fence`` reports.
        with self._service.store_lock():
            refusal = self._bootstrap_admission(expected, ADMISSION_PUBLICATION)
            if refusal is not None:
                refusal["workflow_id"] = workflow_id
                refusal["engagement_id"] = reference["engagement_id"]
                refusal["effects"] = effects.as_dict()
                refusal["detail"] += (
                    "; engagement %s stays reserved for workflow %s and"
                    " no row was written" % (reference["engagement_id"],
                                             workflow_id))
                return refusal
            # Retention (R2-2) is established IN this same locked save: the
            # record carries its immutable deadline from its insertion, and
            # a store full of protected records refuses the row. Pruning
            # to make room consults the CANONICAL start obligations of
            # every Mission-origin record (R15-2), read under this same
            # Mission lock.
            ok, problem, _ = workflow_store.add_workflow(
                workflows, entry, now=now,
                protected=mission_gate.canonically_protected(self._service))
            if not ok:
                return _result(False, PROBLEM_WORKFLOW_STORE,
                               "the workflow store refused the row: %s"
                               % problem, mission_id=mission_id,
                               revision=revision, workflow_id=workflow_id,
                               engagement_id=reference["engagement_id"],
                               effects=effects)
            effects.attempt(EFFECT_ROW)
            try:
                self._workflow_store.save(workflows)
            except workflow_store.StoreError as exc:
                # The save outcome is UNKNOWN; the fence stays reserved
                # and the retry answers from the durable stores.
                return _result(False, PROBLEM_WORKFLOW_STORE,
                               "%s; %s" % (exc, _effects_sentence(effects)),
                               mission_id=mission_id, revision=revision,
                               workflow_id=workflow_id,
                               engagement_id=reference["engagement_id"],
                               effects=effects)
            effects.done(EFFECT_ROW)
        return _result(True, mission_id=mission_id, revision=revision,
                       workflow_id=workflow_id,
                       engagement_id=reference["engagement_id"],
                       effects=effects)
