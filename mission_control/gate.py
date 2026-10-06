"""The effect-boundary Mission gate (Task 8, slice S-IV; ledger R2-1).

A Mission-origin workflow record carries a digest-bound CLAIM of origin
(its ``mission_authority`` linkage, slice S-III). Whether that authority
is CURRENT is decided here, from the Mission Core's own answers, at
every effect boundary the Runtime and the Broker cross: action admission
after the workflow lock and capability consumption, every long blocking
step (a model turn, a clone) before its result is accepted, the spawn,
completion, and the Runtime's planning and recovery turns before any
capability is minted.

``admit`` returns an ``Admission`` whose ``classification`` tells the
caller what kind of refusal it holds, because the durable consequence
differs (Supervisor clarification):

- ``DEPENDENCY``: the hard missing-dependency gate — slice S-V's control
  record and retention enforcement are not present in this candidate, or
  the control read/enforcement is not WIRED on the service this gate
  holds (class symbols alone never count). Zero effects: the caller
  refuses without writing anything.
- ``HOLD``: a REVERSIBLE condition — a canonical hold (S-V), readiness
  that is not fresh, a budget that is exhausted for a follow-up, or a
  Mission SOURCE that is UNAVAILABLE right now (the store's own typed
  refusal: unreadable, invalid or saturated content — never reported as
  an unknown or absent Mission, never as a permanent revocation). The
  caller records at most ONE non-terminal receipt per cause, keeps the
  phase, and re-admits on a later pass; nothing is invalidated.
- ``TERMINAL``: the authority is gone for good — expired, revoked,
  superseded by a later revision, insufficient provenance, a cancelled
  or terminally closed Mission, a contract no longer live, a Mission
  the readable registry does not hold. The caller stops the workflow
  durably (a locked ``PHASE_BLOCKED`` transition plus a receipt).

ADMISSION AT THE ACTUAL EFFECT BOUNDARY, and the LOCK ORDER (defined):

1. ``admit`` alone is a lock-free snapshot: every read is a whole-document
   load (``MissionService.get``, ``get_state``, ``validate_authorization``)
   and takes no lock. It is what the callers use BEFORE a long blocking
   step (a model turn, a clone) and AFTER it returns, so a Mission-side
   change during the wait is caught before the step's result is accepted.
   A snapshot can never, by itself, make an irreversible effect safe
   against a concurrent Mission write.
2. ``admit_and_mark`` is the SHORT critical section for every accepted
   result and durable marker (the dispatch marker, the verified result and
   every other returned verification outcome, the completion transition,
   the lease record, the validated-handoff transition): it takes the
   MISSION store lock, re-reads authority / hold / cancel under it, and —
   only when admitted — runs the caller's ``mark`` (the caller's own
   durable save under the workflow lock it already holds) BEFORE
   releasing the Mission lock. A cancel or EDIT committed before the
   section is seen and blocks the marking; one committed after it is
   after the marking. The Mission lock is held for that read-then-mark
   step only, never across a model turn, a clone, a spawn or any other
   blocking wait.
   MARKER ADMISSION IS NOT START ADMISSION (start-claim decision): the
   dispatch marker is written BEFORE the spawn and proves intent and
   ambiguity, not permission to start. The start itself is admitted by
   the canonical ENGAGEMENT START (``open_start``: one atomic Mission
   transaction persisting one exact, single-owner claim per engine
   operation), fused to the bridge's real start boundaries, and settled
   canonically afterwards (``settle_start`` / ``observe_stop``). The
   contract is ADMITTED-OPERATION ORDERING, not a zero-physical-start
   guarantee: a cancel or EDIT committed before the start's admission
   yields no start record and no invocation; one committed after it is
   ordered after the admitted start — the operation may be in flight —
   and the settlement records the stop requirement, the owned stop runs,
   and only OBSERVED absence, recorded canonically, confirms it.
3. Lock order: WORKFLOW lock (the Broker's action lock) → MISSION lock
   (short, inside), never the reverse. Controls that write the Mission
   store (hold, cancel, EDIT, revocation) take ONLY the Mission lock, so
   they stay recordable promptly while a Broker action holds the workflow
   lock through a blocking wait; they simply cannot overlap the short
   read-then-mark step. The bootstrap takes the Mission lock (reservation)
   alone, and the workflow lock (row save) with the Mission lock nested
   inside it for the publication admission — the same order.
4. Source availability (Supervisor item E): every Mission read here may
   raise the store's typed ``MissionStoreError`` (or a ``MissionError``
   from a document that loads but fails validation). ``admit`` and
   ``admit_and_mark`` never let either escape: the answer is the
   reversible ``mission_control_source_unavailable`` refusal naming the
   store's problem, so the caller records at most one hold receipt,
   keeps the phase, makes zero effects, and admits normally once the
   source is repaired.

Cross-Mission isolation: every question is asked of the ONE Mission the
linkage names; nothing here enumerates, reads or touches another
Mission's records. This module imports the neutral core, the workflow
record layer (for the kind predicates) and nothing else: it never spawns,
runs git, or holds a second truth store.
"""

import collections

from mission import authorization as mission_authorization
from mission import record as mission_record
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store
from mission_control import authority as mission_control_authority
from mission_control import integration
from workflow_authority import record as workflow_record

# Refusal classifications.
CLASS_NONE = "none"
CLASS_DEPENDENCY = "dependency"
CLASS_HOLD = "hold"
CLASS_TERMINAL = "terminal"
CLASSIFICATIONS = (CLASS_DEPENDENCY, CLASS_HOLD, CLASS_NONE, CLASS_TERMINAL)

# The effect boundaries a caller names; readiness freshness is required
# at the boundaries that commit engineering effort.
BOUNDARY_ACTION_ADMISSION = "action_admission"
BOUNDARY_PLANNING_TURN = "planning_turn"
BOUNDARY_RECOVERY_TURN = "recovery_turn"
BOUNDARY_HANDOFF_TURN = "handoff_turn"
BOUNDARY_VERIFICATION_TURN = "verification_turn"
BOUNDARY_MATERIALIZE = "materialize"
BOUNDARY_SPAWN = "spawn"
BOUNDARY_COMPLETION = "completion"
# Task 8 S-VI: every delivery-phase effect of a Mission-origin record —
# the Runtime's verification run, the proposal preparation and each P1-A6
# effect admission (``mission_control.delivery``). Readiness is required.
BOUNDARY_DELIVERY_EFFECT = "delivery_effect"
BOUNDARIES = (
    BOUNDARY_ACTION_ADMISSION, BOUNDARY_COMPLETION, BOUNDARY_DELIVERY_EFFECT,
    BOUNDARY_HANDOFF_TURN, BOUNDARY_MATERIALIZE, BOUNDARY_PLANNING_TURN,
    BOUNDARY_RECOVERY_TURN, BOUNDARY_SPAWN, BOUNDARY_VERIFICATION_TURN,
)
READINESS_BOUNDARIES = frozenset((BOUNDARY_SPAWN, BOUNDARY_COMPLETION,
                                  BOUNDARY_DELIVERY_EFFECT))

PROBLEM_UNKNOWN_MISSION = "mission_control_unknown_mission"
PROBLEM_REVISION_SUPERSEDED = "mission_control_revision_superseded"
PROBLEM_AUTHORIZATION_MISMATCH = "mission_control_authorization_mismatch"
PROBLEM_MISSION_TERMINAL = "mission_control_mission_terminal"
PROBLEM_CONTRACT_NOT_LIVE = "mission_control_contract_not_live"
PROBLEM_HOLD_ACTIVE = "mission_control_hold_active"
PROBLEM_CANCEL_REQUESTED = "mission_control_cancel_requested"
PROBLEM_READINESS_STALE = "mission_control_readiness_stale"
PROBLEM_NOT_MISSION_ORIGIN = "mission_control_not_mission_origin"
PROBLEM_SOURCE_UNAVAILABLE = "mission_control_source_unavailable"
PROBLEM_START_NOT_RESERVED = "mission_control_start_not_reserved"
PROBLEM_START_UNSETTLED = "mission_control_start_unsettled"
# The core's own "does not fit the start's state" refusal (already
# settled, already confirmed), re-exported for the Broker.
PROBLEM_START_STATE = mission_state.PROBLEM_ENGAGEMENT_START_STATE
PROBLEM_START_STOP_REQUIRED = "mission_control_start_stop_required"
# The core's readiness refusal at the start transaction (a HOLD).
PROBLEM_STATE_RESOURCE_NOT_READY = "mission_state_resource_not_ready"

# Task 8 S-V (R15-3): OWNERSHIP-SAFE CLEANUP is not engineering. Its
# admission is its own (``admit_cleanup``) and never one of BOUNDARIES,
# so no fact that ends engineering can strand the owned cleanup, and no
# engineering step can pass through the cleanup door.
BOUNDARY_CLEANUP = "cleanup"
# The reversible wait of a cleanup while a start of the workflow is
# unsettled or owes a stop that observed absence has not confirmed.
PROBLEM_CLEANUP_AWAITS_STOP = "mission_control_cleanup_awaits_stop"

ENGINEERING_ACTIONS = (mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,)

# Task 8 S-VII (item E): the ordinary state operation ids an admitted step
# may need before its outcome is recorded — a start's admission,
# settlement and stop observation at each of its two points (6), or a
# delivery's per-step attestations and completion — with a margin.
EFFECT_OPERATION_RESERVE = 8

# Re-exported for the Broker (target_runtime never imports ``mission``).
START_OUTCOME_COMPLETED = mission_state.START_OUTCOME_COMPLETED
START_OUTCOME_FAILED = mission_state.START_OUTCOME_FAILED
START_OUTCOME_UNCERTAIN = mission_state.START_OUTCOME_UNCERTAIN
MAX_START_AGENT_NAMES = mission_state.MAX_START_AGENT_NAMES
start_stop_required = mission_state.start_stop_required
start_stop_confirmed = mission_state.start_stop_confirmed
start_identity = mission_state.start_identity

Admission = collections.namedtuple(
    "Admission", ("ok", "problem", "detail", "classification", "boundary"))


def _admitted(boundary):
    return Admission(True, None, None, CLASS_NONE, boundary)


def _refused(problem, detail, classification, boundary):
    return Admission(False, problem, detail, classification, boundary)


def source_unavailable(exc, boundary):
    """The REVERSIBLE refusal for a Mission source that cannot answer
    right now: the store's typed problem, never an absence claim."""
    problem = getattr(exc, "problem", None) or type(exc).__name__
    return _refused(
        PROBLEM_SOURCE_UNAVAILABLE,
        "the Mission source is unavailable (%s: %s); nothing is decided"
        " until it answers again" % (problem, exc),
        CLASS_HOLD, boundary)


def is_unknown_mission(exc):
    """True for the core's own 'not in the registry' refusal of a
    readable store; every other MissionError is not an absence."""
    return (isinstance(exc, mission_record.MissionError)
            and exc.problem == mission_authorization.PROBLEM_UNKNOWN_MISSION)


def outstanding_starts(starts):
    """R15-2: the canonical obligations ``starts`` (one workflow's start
    records) still hold — ``[(start_id, why)]`` for every start that is
    UNSETTLED or owes a stop that observed absence has not confirmed
    (a cancel after the workflow's terminal completion included: its stop
    request marks the settled start)."""
    outstanding = []
    for start in starts:
        if start["settlement"] is None:
            outstanding.append((start["start_id"], "unsettled"))
        elif mission_state.start_stop_required(start) and not (
            mission_state.start_stop_confirmed(start)
        ):
            outstanding.append((start["start_id"], "stop not confirmed"))
    return outstanding


def canonical_obligations(service, entry):
    """R15-2: the outstanding canonical start obligations of ``entry``'s
    workflow (see ``outstanding_starts``), read lock-free from the Mission
    source through ``service`` — or None when the source cannot answer
    (the caller must then treat the record as obligated). A Mission the
    readable registry does not hold has none (``[]``); a v2 record has
    none. The record's own receipts are never consulted here: the
    canonical record is the authority for what is still owed."""
    if not workflow_record.is_mission_core_kind(entry):
        return []
    mission_id = (entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}).get(
        "mission_id")
    try:
        state = service.get_state(mission_id)
    except mission_store.MissionStoreError:
        return None
    except mission_record.MissionError as exc:
        return [] if is_unknown_mission(exc) else None
    return outstanding_starts(
        [start for start in mission_state.engagement_starts_of(state["record"] or {})
         if start["workflow_id"] == entry["workflow_id"]])


def canonically_protected(service):
    """The pruning predicate (``workflow_authority.store.add_workflow``'s
    ``protected``) of a caller that can read the Mission source: a
    Mission-origin record is protected while a canonical obligation is
    outstanding or cannot be read (R15-2)."""
    def protected(entry):
        return canonical_obligations(service, entry) != []
    return protected


def local_process_context(principal_ref):
    """The authenticated context the Runtime presents for its own
    Mission-side writes (follow-up engagement reservations): a decision
    of the trusted node's own process, never a transport credential."""
    return mission_record.AuthenticatedContext(
        transport="runtime",
        principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
        principal_ref=principal_ref,
    ).validate()


def production_gate(mission_store_directory, principal_ref, clock=None):
    """The gate the Runtime's CLI wires when its configuration names a
    Mission store: the neutral service over that store (the real clock)
    and the local-process context of this Runtime. Composition only."""
    import time

    from mission import service as mission_service
    from mission import store as mission_store
    service = mission_service.MissionService(
        mission_store.MissionStore(mission_store_directory), clock or time.time)
    return MissionEffectGate(service, local_process_context(principal_ref))


def reservation_reference(engagement, operation_id=None, reserved_at=None):
    """The closed reservation reference a workflow record carries, from
    an engagement record of the Mission state (or the just-applied
    outcome plus its operation id and time)."""
    return {
        "engagement_id": engagement["engagement_id"],
        "engagement_sequence": engagement["engagement_sequence"],
        "operation_id": (engagement["operation_id"] if operation_id is None
                         else operation_id),
        "reserved_at": (engagement["reserved_at"] if reserved_at is None
                        else reserved_at),
    }


class MissionEffectGate(object):
    """The one gate the Runtime and the Broker consult for Mission-origin
    records. ``service`` is the neutral ``MissionService``; ``context`` is
    the local-process context used for the gate's own Mission-side
    writes (reservations)."""

    def __init__(self, service, context):
        self._service = service
        self._context = mission_record.require_context(context)

    @property
    def service(self):
        return self._service

    @property
    def context(self):
        """The authenticated context this gate's Runtime presents for its
        own Mission-side writes (S-V: the reconciliation bridge too)."""
        return self._context

    def missing_guards(self):
        """The absent or UNWIRED S-V guards for this gate's own service
        instance (``integration.missing_guards`` with the instance)."""
        return integration.missing_guards(self._service)

    def enabled(self):
        """True only when slice S-V's guards are present in this build and
        wired on this gate's service: while False, ``admit`` refuses with
        the dependency classification and the Runtime claims no
        Mission-origin record at all."""
        return not self.missing_guards()

    # -- admission (lock-free snapshot) ---------------------------------------

    def admit(self, entry, boundary, settling=None):
        """Whether ``entry`` (a workflow record) may cross ``boundary``
        right now, from a lock-free snapshot of the Mission store. Never
        writes, mints, spawns or locks, and never raises for a source
        that cannot answer (``mission_control_source_unavailable``, a
        HOLD). A workflow whose engagement starts hold an UNSETTLED start
        (other than ``settling``, the one its owner is settling right
        now) or a start with a stop requirement is refused TERMINALLY at
        every boundary: an in-flight or stopped start authorizes no
        further step, follow-up, accepted result or completion. See the
        module docstring for when a snapshot is enough and when
        ``admit_and_mark`` is required. The ownership-safe cleanup is NOT
        one of these boundaries: it has its own admission
        (``admit_cleanup``)."""
        if boundary not in BOUNDARIES:
            raise ValueError("unknown boundary %r" % (boundary,))
        missing = self.missing_guards()
        if missing:
            refusal = integration.dependency_refusal(missing)
            return _refused(refusal["problem"], refusal["detail"],
                            CLASS_DEPENDENCY, boundary)
        if not workflow_record.is_mission_core_kind(entry):
            return _refused(PROBLEM_NOT_MISSION_ORIGIN,
                            "the gate admits Mission-origin records only",
                            CLASS_TERMINAL, boundary)
        try:
            return self._admit_from_source(entry, boundary, settling)
        except mission_store.MissionStoreError as exc:
            return source_unavailable(exc, boundary)
        except mission_record.MissionError as exc:
            if is_unknown_mission(exc):
                return _refused(PROBLEM_UNKNOWN_MISSION, str(exc),
                                CLASS_TERMINAL, boundary)
            return source_unavailable(exc, boundary)

    def admit_cleanup(self, entry):
        """Whether the Broker may run the OWNERSHIP-SAFE final cleanup of
        ``entry`` (``ACTION_RELEASE``: trust revocation, evidence
        preservation, the proven session close, the managed-directory
        removal, the lease release) — an admission SEPARATE from every
        engineering boundary (R15-3). Cleanup continues nothing: it starts
        nothing, accepts no result and spends no authority, so the facts
        that END engineering — a cancel (confirmed or not), terminal
        progress, a superseded revision, lapsed or revoked authority, a
        contract no longer live — do not refuse it; the Broker's own
        terminal-phase, retention and ownership checks in its release
        handler still decide what is released. It WAITS (a reversible
        HOLD; nothing is done) while:

        - the Mission source cannot answer (no obligation can be read);
        - a start of this workflow is unsettled or owes a stop that
          observed absence has not confirmed (``outstanding_starts``): the
          owner's pass settles and stops it first — the cleanup never
          stands in for the canonical stop observation;
        - a hold is active and no cancel was requested (a hold pauses
          every DI-initiated effect; once a cancel is requested the hold
          can no longer be lifted, and the cleanup follows the cancel).

        The dependency gate and the Mission-origin kind check are
        ``admit``'s; a Mission the readable registry does not hold is
        refused TERMINALLY there as here. Lock-free snapshot, never
        writes, never raises for a source that cannot answer."""
        missing = self.missing_guards()
        if missing:
            refusal = integration.dependency_refusal(missing)
            return _refused(refusal["problem"], refusal["detail"],
                            CLASS_DEPENDENCY, BOUNDARY_CLEANUP)
        if not workflow_record.is_mission_core_kind(entry):
            return _refused(PROBLEM_NOT_MISSION_ORIGIN,
                            "the gate admits Mission-origin records only",
                            CLASS_TERMINAL, BOUNDARY_CLEANUP)
        try:
            return self._admit_cleanup_from_source(entry)
        except mission_store.MissionStoreError as exc:
            return source_unavailable(exc, BOUNDARY_CLEANUP)
        except mission_record.MissionError as exc:
            if is_unknown_mission(exc):
                return _refused(PROBLEM_UNKNOWN_MISSION, str(exc),
                                CLASS_TERMINAL, BOUNDARY_CLEANUP)
            return source_unavailable(exc, BOUNDARY_CLEANUP)

    def _admit_cleanup_from_source(self, entry):
        mission_id = (entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}).get(
            "mission_id")
        view = self._service.mission_controls(mission_id)
        state = self._service.get_state(mission_id)
        outstanding = outstanding_starts(
            [start for start in mission_state.engagement_starts_of(state["record"] or {})
             if start["workflow_id"] == entry["workflow_id"]])
        if outstanding:
            return _refused(
                PROBLEM_CLEANUP_AWAITS_STOP,
                "workflow %s still owes canonical start obligation(s) %s; the"
                " cleanup waits for the owner's settlement and observed absence"
                % (entry["workflow_id"],
                   ", ".join("%s (%s)" % item for item in outstanding)),
                CLASS_HOLD, BOUNDARY_CLEANUP)
        if view.get("hold_active") and not view.get("cancel_requested"):
            return _refused(PROBLEM_HOLD_ACTIVE,
                            "mission %s is on hold; the cleanup waits" % mission_id,
                            CLASS_HOLD, BOUNDARY_CLEANUP)
        return _admitted(BOUNDARY_CLEANUP)

    @staticmethod
    def _start_refusal(entry, state, boundary, settling):
        """The terminal refusal an unresolved engagement start of this
        workflow imposes, or None."""
        for start in mission_state.engagement_starts_of(state["record"] or {}):
            if start["workflow_id"] != entry["workflow_id"]:
                continue
            if start["settlement"] is None and start["start_id"] != settling:
                return _refused(
                    PROBLEM_START_UNSETTLED,
                    "engagement start %s (%s, engagement %s) is admitted and"
                    " UNSETTLED: its outcome is unknown until its owner settles"
                    " it; nothing further is admitted and nothing is retried"
                    % (start["start_id"], start["point"], start["engagement_id"]),
                    CLASS_TERMINAL, boundary)
            if mission_state.start_stop_required(start):
                confirmed = mission_state.start_stop_confirmed(start)
                return _refused(
                    PROBLEM_START_STOP_REQUIRED,
                    "engagement start %s (%s) requires a stop (%s); the stop is"
                    " %s; the engagement authorizes nothing further"
                    % (start["start_id"], start["point"],
                       (start["settlement"] or {}).get("stop_reason")
                       or (start["stop_requested"] or {}).get("reason"),
                       "confirmed by observed absence" if confirmed
                       else "NOT confirmed"),
                    CLASS_TERMINAL, boundary)
        return None

    def _admit_from_source(self, entry, boundary, settling=None):
        linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
        mission_id = linkage.get("mission_id")
        stored = self._service.get(mission_id)
        record = stored["record"]
        if record["current_revision"] != linkage["revision"]:
            return _refused(
                PROBLEM_REVISION_SUPERSEDED,
                "the workflow binds revision %d of mission %s, which is now at"
                " revision %d; a superseded revision authorizes nothing"
                % (linkage["revision"], mission_id, record["current_revision"]),
                CLASS_TERMINAL, boundary)
        check = self._service.validate_authorization(
            linkage["authorization_id"], mission_id, linkage["revision"],
            required_actions=ENGINEERING_ACTIONS)
        if not check.valid:
            if check.problem == mission_authorization.PROBLEM_STORE_UNREADABLE:
                # The one validator answers a store failure with its own
                # typed problem instead of raising: still a HOLD.
                return _refused(
                    PROBLEM_SOURCE_UNAVAILABLE,
                    "the Mission source is unavailable (%s: %s); nothing is"
                    " decided until it answers again"
                    % (check.problem, check.detail), CLASS_HOLD, boundary)
            return _refused(check.problem, check.detail, CLASS_TERMINAL, boundary)
        issued = None
        for authorization in stored["authorizations"]:
            if authorization["authorization_id"] == linkage["authorization_id"]:
                issued = authorization
        if issued is None or issued["authorization_digest_sha256"] != (
            linkage["authorization_digest_sha256"]
        ):
            return _refused(
                PROBLEM_AUTHORIZATION_MISMATCH,
                "the workflow's authorization digest is not the digest the"
                " Mission Core issued for %s" % linkage["authorization_id"],
                CLASS_TERMINAL, boundary)
        provenance = mission_control_authority.consequential_decision_provenance(
            stored, linkage["authorization_id"])
        if not provenance["sufficient"]:
            return _refused(provenance["problem"], provenance["detail"],
                            CLASS_TERMINAL, boundary)
        state = self._service.get_state(mission_id)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return _refused(PROBLEM_MISSION_TERMINAL,
                            "mission %s is %s" % (mission_id, state["progress"]),
                            CLASS_TERMINAL, boundary)
        contract = state["contract"]
        if not contract["active"] or not contract["authority_live"]:
            return _refused(
                PROBLEM_CONTRACT_NOT_LIVE,
                "mission %s has no live activated contract (%s)"
                % (mission_id, contract["problem"]), CLASS_TERMINAL, boundary)
        # Slice S-V's canonical controls, read through the WIRED service
        # (the dependency gate above already refused an unwired one). The
        # sticky cancel is checked BEFORE this workflow's start refusals:
        # a cancel records the stop requirement on every unconfirmed start
        # in its own transaction, and the refusal names the cause (the
        # cancel), not only its consequence (the stop). A hold, being
        # reversible, never masks a terminal start refusal.
        view = self._service.mission_controls(mission_id)
        if view.get("cancel_requested"):
            return _refused(PROBLEM_CANCEL_REQUESTED,
                            "mission %s has a cancel request%s; it is sticky"
                            % (mission_id, " (confirmed)" if view.get(
                                "cancel_confirmed") else ""),
                            CLASS_TERMINAL, boundary)
        start_refusal = self._start_refusal(entry, state, boundary, settling)
        if start_refusal is not None:
            return start_refusal
        if view.get("hold_active"):
            return _refused(PROBLEM_HOLD_ACTIVE,
                            "mission %s is on hold" % mission_id,
                            CLASS_HOLD, boundary)
        if boundary in READINESS_BOUNDARIES:
            readiness = state["readiness"]
            if readiness is not None and not readiness["satisfied"]:
                stale = sorted(key for key, status in readiness["resources"].items()
                               if status != mission_state.READINESS_READY)
                return _refused(
                    PROBLEM_READINESS_STALE,
                    "resource readiness is not fresh for %s; refresh it through"
                    " the canonical observe_resource_readiness operation"
                    % ", ".join(stale), CLASS_HOLD, boundary)
        # Task 8 S-VII (item E): a store that can still be READ but could
        # not record what an admitted step must write next (its start and
        # settlement, its receipts' attestation, its result) is as
        # unavailable as an unreadable one — the step WAITS (reversible)
        # before any marker, invocation or effect, never after it.
        if settling is None:
            headroom = self._service.ordinary_operation_headroom()
            if headroom < EFFECT_OPERATION_RESERVE:
                return source_unavailable(mission_store.MissionStoreError(
                    "%d ordinary state operation ids remain; an admitted step"
                    " needs %d to record its outcome"
                    % (headroom, EFFECT_OPERATION_RESERVE),
                    mission_store.PROBLEM_STORE_FULL), boundary)
        return _admitted(boundary)

    # -- admission at the effect boundary (short critical section) ---------------

    def admit_and_mark(self, entry, boundary, mark):
        """Re-admit ``entry`` at ``boundary`` UNDER the Mission store lock
        and, only when admitted, run ``mark()`` — the caller's own durable
        marking of the irreversible effect (its workflow save, under the
        workflow lock it already holds) — before releasing the lock.
        Returns the admission; ``mark`` ran exactly when it is ok. The
        lock is held for the read-then-mark step only. A Mission store
        whose lock cannot even be taken is the same reversible
        ``source_unavailable`` refusal (``mark`` did not run)."""
        try:
            with self._service.store_lock():
                admission = self.admit(entry, boundary)
                if admission.ok:
                    mark()
        except mission_store.MissionStoreError as exc:
            return source_unavailable(exc, boundary)
        return admission

    def admit_cleanup_held(self, entry, effect):
        """Task 8 R25-1: ``admit_cleanup`` taken UNDER the Mission store lock
        and HELD across ``effect()``. ``effect`` runs only when admitted,
        before the lock is released. Returns ``(admission, result)``; the
        result is None unless ``effect`` ran.

        This is ``admit_and_mark``'s section, with the effect itself in place
        of its marking. A hold or other Mission write committed before the
        section is seen, and one committed after it is truthfully after the
        effect. So the admission cannot go stale between its read and the
        effect. That ordering holds for every write that takes this lock:
        each current Mission ``save`` call sits inside a store-lock cycle
        (by inspection of its call sites; ``MissionStore.save`` does not
        itself enforce it).

        ``effect`` must be local and NON-WAITING (the store lock's
        contract). The Broker passes one process scope's bounded evidence
        re-read and its removal (an ``unlink`` and one scope's ``rmtree``),
        never a subprocess or another lock. A LIMIT, stated: the ``rmtree``
        lasts as long as the scope's own local files take to remove. The
        evidence bound limits the owned roots it covers, not every file the
        scope holds. A Mission store whose lock cannot even be taken is the
        same reversible ``source_unavailable`` refusal, and ``effect`` did
        not run. An effect that DID run is reported as run, even if
        releasing the lock fails."""
        ran = []
        try:
            with self._service.store_lock():
                admission = self.admit_cleanup(entry)
                if admission.ok:
                    ran.append(effect())
        except mission_store.MissionStoreError as exc:
            if ran:
                return admission, ran[0]
            return source_unavailable(exc, BOUNDARY_CLEANUP), None
        return admission, (ran[0] if ran else None)

    # -- the follow-up budget reservation (R2-4) ----------------------------

    def reserve_follow_up(self, entry, dispatch_sequence):
        """Reserve the Mission-side budget for follow-up dispatch number
        ``dispatch_sequence`` (>= 2) of ``entry``'s workflow, bound to the
        exact Mission revision, workflow id and ordinal, BEFORE the
        Broker's durable marker and spawn. IDEMPOTENT: an existing
        reservation for exactly (current activation, workflow, ordinal)
        — a crash after the reservation save but before the marker — is
        RECOVERED and returned with its recorded operation id and time,
        minting and spending nothing. Returns ``(reference, None)`` or
        ``(None, Admission)`` with a classified refusal. Takes the Mission
        store lock briefly (inside the caller's workflow lock; never the
        reverse)."""
        admission = self.admit(entry, BOUNDARY_SPAWN)
        if not admission.ok:
            return None, admission
        try:
            return self._reserve_follow_up(entry, dispatch_sequence)
        except mission_store.MissionStoreError as exc:
            return None, source_unavailable(exc, BOUNDARY_SPAWN)

    def _reserve_follow_up(self, entry, dispatch_sequence):
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        mission_id = linkage["mission_id"]
        existing = self._existing_reservation(mission_id, entry["workflow_id"],
                                              dispatch_sequence)
        if existing is not None:
            return reservation_reference(existing), None
        try:
            operation_id = self._service.mint_state_operation_id(self._context)
            sequence = self._service.get_state(mission_id)["sequence"]
            outcome = self._service.reserve_engagement(
                mission_id, operation_id, sequence, entry["workflow_id"],
                dispatch_sequence, self._context)
        except mission_record.MissionError as exc:
            # A concurrent reservation may have won: recover it before
            # reporting anything.
            existing = self._existing_reservation(mission_id, entry["workflow_id"],
                                                  dispatch_sequence)
            if existing is not None:
                return reservation_reference(existing), None
            # Task 8 S-VII: a SATURATED state record (``mission_state_full``)
            # is reversible exactly as ``open_start`` treats it — the
            # workflow waits; it is never blocked for a capacity bound.
            classification = (CLASS_HOLD if exc.problem in (
                mission_state.PROBLEM_BUDGET_EXHAUSTED,
                mission_state.PROBLEM_CONTROL_HOLD,
                mission_state.PROBLEM_STATE_FULL,
                mission_state_service.PROBLEM_STALE_SEQUENCE) else CLASS_TERMINAL)
            return None, _refused(exc.problem, str(exc), classification,
                                  BOUNDARY_SPAWN)
        recorded = self._existing_reservation(mission_id, entry["workflow_id"],
                                              dispatch_sequence)
        if recorded is not None:
            return reservation_reference(recorded), None
        return reservation_reference({
            "engagement_id": outcome["engagement_id"],
            "engagement_sequence": outcome["engagement_sequence"],
            "operation_id": operation_id,
            "reserved_at": self._service.now(),
        }), None

    # -- engagement starts (start-claim decision) ------------------------------

    def owner_ref(self, entry, dispatch_sequence):
        """The single-owner reference of this Runtime principal's starts
        for one dispatch of ``entry``: deterministic, so the SAME owner
        (this principal, after a restart or on a later pass) can settle
        and observe what it started; the core binds the principal too.
        Invocation replay is refused separately (a start is admitted
        once per point, never re-opened)."""
        return "%s:%s:%d" % (self._context.principal_ref, entry["workflow_id"],
                             dispatch_sequence)

    def open_start(self, entry, dispatch_sequence, point, owner_ref):
        """The ATOMIC admission of one engine operation of ``entry``'s
        current engagement: the lock-free gate admission first (the
        immutable decision provenance, the S-V controls read, readiness
        — a HOLD or TERMINAL refusal admits nothing), then the canonical
        ``open_engagement_start`` transaction, which re-validates the
        current revision, the live authorization at its digest, the
        Mission's progress and readiness UNDER the Mission store lock and
        persists ONE exact start bound to the engagement, the point and
        ``owner_ref``. Returns ``(start_id, None)`` or ``(None,
        Admission)``. An existing start for this (engagement, point) —
        a crash after it, a retry, another owner — refuses TERMINALLY:
        it is never re-opened and never replayed."""
        admission = self.admit(entry, BOUNDARY_SPAWN)
        if not admission.ok:
            return None, admission
        reference = entry.get(workflow_record.MISSION_ENGAGEMENT_KEY) or {}
        if reference.get("engagement_sequence") != dispatch_sequence:
            return None, _refused(
                PROBLEM_START_NOT_RESERVED,
                "the record's engagement reference names ordinal %r, not"
                " dispatch %d; a start is admitted only for the reserved"
                " engagement" % (reference.get("engagement_sequence"),
                                 dispatch_sequence), CLASS_TERMINAL, BOUNDARY_SPAWN)
        mission_id = entry[workflow_record.MISSION_AUTHORITY_KEY]["mission_id"]
        try:
            operation_id = self._service.mint_state_operation_id(self._context)
            sequence = self._service.get_state(mission_id)["sequence"]
            outcome = self._service.open_engagement_start(
                mission_id, operation_id, sequence, reference["engagement_id"],
                point, owner_ref, self._context)
        except mission_store.MissionStoreError as exc:
            return None, source_unavailable(exc, BOUNDARY_SPAWN)
        except mission_record.MissionError as exc:
            # A stale sequence (a concurrent write landed), stale
            # readiness and a saturated record are REVERSIBLE: nothing
            # was admitted, nothing was invoked, and the workflow waits.
            classification = (CLASS_HOLD if exc.problem in (
                mission_state_service.PROBLEM_STALE_SEQUENCE,
                mission_state.PROBLEM_STATE_FULL,
                mission_state.PROBLEM_CONTROL_HOLD,
                PROBLEM_STATE_RESOURCE_NOT_READY) else CLASS_TERMINAL)
            return None, _refused(exc.problem, str(exc), classification,
                                  BOUNDARY_SPAWN)
        return outcome["start_id"], None

    def settle_start(self, entry, start_id, owner_ref, outcome, identity,
                     stop_reason):
        """SETTLE the owner's start with the returned outcome/identity in
        the canonical transaction (the core derives the stop requirement
        from a recorded stop request, a superseded revision, a lapsed
        authorization, a terminal Mission, a non-completed outcome, or
        ``stop_reason`` — this gate's own terminal refusal at settlement).
        Returns ``(settlement_outcome, None)`` or ``(None, Admission)``
        when the source cannot answer or the start is not this owner's:
        the caller then holds the start as UNSETTLED (ambiguous), never
        as completed."""
        mission_id = entry[workflow_record.MISSION_AUTHORITY_KEY]["mission_id"]
        try:
            operation_id = self._service.mint_state_operation_id(self._context)
            sequence = self._service.get_state(mission_id)["sequence"]
            settled = self._service.settle_engagement_start(
                mission_id, operation_id, sequence, start_id, owner_ref, outcome,
                identity, stop_reason, self._context)
        except mission_store.MissionStoreError as exc:
            return None, source_unavailable(exc, BOUNDARY_SPAWN)
        except mission_record.MissionError as exc:
            return None, _refused(exc.problem, str(exc), CLASS_TERMINAL,
                                  BOUNDARY_SPAWN)
        return settled, None

    def observe_stop(self, entry, start_id, owner_ref, absent, detail,
                     identity=None):
        """Record the owner's observation of a required stop; ``absent``
        True is the only confirmation; ``identity`` persists a
        late-returned execution identity canonically. Returns
        ``(outcome, None)`` or ``(None, Admission)``."""
        mission_id = entry[workflow_record.MISSION_AUTHORITY_KEY]["mission_id"]
        try:
            operation_id = self._service.mint_state_operation_id(self._context)
            sequence = self._service.get_state(mission_id)["sequence"]
            observed = self._service.observe_engagement_stop(
                mission_id, operation_id, sequence, start_id, owner_ref, absent,
                detail, identity, self._context)
        except mission_store.MissionStoreError as exc:
            return None, source_unavailable(exc, BOUNDARY_SPAWN)
        except mission_record.MissionError as exc:
            return None, _refused(exc.problem, str(exc), CLASS_TERMINAL,
                                  BOUNDARY_SPAWN)
        return observed, None

    def obligations(self, entry):
        """R15-2: ``canonical_obligations`` of ``entry`` through this
        gate's service — ``[(start_id, why)]``, or None when the source
        cannot answer. Recovery scheduling, cleanup candidacy and pruning
        read THIS, not the record's own settlement text."""
        return canonical_obligations(self._service, entry)

    def engagement_starts(self, entry):
        """The canonical start records of every engagement of ``entry``'s
        workflow (deep copies), or None when the source cannot answer.
        Read-only; recovery and status read this rather than any
        in-memory claim."""
        mission_id = (entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}).get(
            "mission_id")
        try:
            state = self._service.get_state(mission_id)
        except (mission_store.MissionStoreError, mission_record.MissionError):
            return None
        return [start for start in mission_state.engagement_starts_of(
                    state["record"] or {})
                if start["workflow_id"] == entry["workflow_id"]]

    def _existing_reservation(self, mission_id, workflow_id, dispatch_sequence):
        """The engagement record of the CURRENT activation for exactly
        (workflow, ordinal), or None."""
        state = self._service.get_state(mission_id)
        activation_id = state["contract"]["activation_id"]
        for engagement in mission_state.engagements_of(state["record"] or {}):
            if (engagement["activation_id"] == activation_id
                    and engagement["workflow_id"] == workflow_id
                    and engagement["engagement_sequence"] == dispatch_sequence):
                return engagement
        return None
