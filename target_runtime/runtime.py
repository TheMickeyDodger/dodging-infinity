"""The deterministic Runtime loop: strict structured request transitions.

Since I3 the phase->action shortcut is gone. Codex PROPOSES and the
Runtime DECIDES:

- At AUTHORIZED (and at WORKSPACE_READY after a crash between the two
  preparation actions) a FRESH ``prepare`` role turn (the I2 pipeline;
  no new spawn path) must return the structured transition
  ``request_prepare`` — parsed with the I2 vocabulary and its per-role
  subset — before the Runtime will touch the workflow.
- At PREPARED the Broker's validate action runs the fresh
  ``handoff_validation`` turn whose own structured outcome
  (``request_dispatch`` / ``needs_reauthorization`` / ``blocked``) IS
  the request selecting the transition.
- At VALIDATED the recorded ``handoff_validation`` turn is the
  standing dispatch request; the Runtime re-validates it (freshness
  included) before dispatching. When that standing request has gone
  stale (I3-L1), the Runtime runs a FRESH ``handoff_validation``
  turn right there — re-validation is read-only and re-runnable —
  so the workflow advances under a fresh request or reaches an
  honest durable stop, NEVER a permanent stall: staleness bounds a
  single proposal's authority, and can never strand a workflow in a
  non-terminal phase (that would be a de-facto mission timeout).

For EVERY proposed transition the Runtime then INDEPENDENTLY
validates, against a FRESH disk read and before any effect: workflow
identity, revision, target identity (via the case-folded
``repository_identity_key`` — never URL string equality), baseline
shape, control identity and the LIVE policy digest, phase, request
freshness, the proposed outcome against the expected action, expiry,
replay, and ambiguity. Only then does it mint a one-shot internal
capability bound to (workflow, action, revision) and call the Broker,
which validates and CONSUMES the capability exactly once at its own
gate. Codex cannot manufacture a transition: an unexpected outcome,
role, phase, or workflow changes NOTHING.

A refusal leaves the workflow exactly where it was. There is NO
mission timeout and NO retry loop: request freshness bounds how long
one Codex proposal stays actionable (a stale one simply requires a
fresh turn), it never cancels any work.
"""

from workflow_authority import canonical as canonical_module
from workflow_authority import record as record_module
from workflow_authority import store as store_module
from workflow_authority.digest import (
    DigestError,
    control_policy_digest,
)

from telegram_operator.protocol import (
    OUTCOME_BLOCKED,
    OUTCOME_NEEDS_REAUTHORIZATION,
    OUTCOME_REQUEST_DISPATCH,
    OUTCOME_REQUEST_FOLLOW_UP,
    OUTCOME_REQUEST_PREPARE,
    OUTCOME_REQUEST_RECOVERY,
    OUTCOME_VERIFIED_RESULT,
)

from target_runtime import broker as broker_module
from target_runtime import capability as capability_module
from target_runtime import dispatch as dispatch_module

# How long one structured Codex proposal stays actionable. A stale
# request authorizes nothing — the Runtime asks for a fresh turn on a
# later pass. Hard constant; pacing of authority, not a mission
# timeout (nothing is ever cancelled by it).
REQUEST_VALIDITY_SECONDS = 900

ROLE_TURN_COMPLETED = "role_turn_completed"

# Each claimable phase, with: the role whose FRESH turn must propose
# the transition ("live"), or the recorded turn that already proposed
# it ("recorded"), or None (the Broker action itself runs the
# proposing turn); the outcome that authorizes the step; and the
# fixed Broker action sequence the authorized step performs.
STEP_LIVE_REQUEST = "live"
STEP_RECORDED_REQUEST = "recorded"
STEP_ACTION_EMBEDDED_REQUEST = "embedded"

_STEPS = {
    record_module.PHASE_AUTHORIZED: {
        "mode": STEP_LIVE_REQUEST,
        "role": record_module.TURN_ROLE_PREPARE,
        "outcome": OUTCOME_REQUEST_PREPARE,
        "actions": (
            broker_module.ACTION_MATERIALIZE,
            broker_module.ACTION_PREPARE,
        ),
    },
    # Crash recovery between the two preparation actions: a FRESH
    # prepare turn must re-propose before the second action runs.
    record_module.PHASE_WORKSPACE_READY: {
        "mode": STEP_LIVE_REQUEST,
        "role": record_module.TURN_ROLE_PREPARE,
        "outcome": OUTCOME_REQUEST_PREPARE,
        "actions": (broker_module.ACTION_PREPARE,),
    },
    # The validate action runs the fresh handoff_validation turn
    # itself; that turn's structured outcome is the request and
    # selects the transition inside the Broker.
    record_module.PHASE_PREPARED: {
        "mode": STEP_ACTION_EMBEDDED_REQUEST,
        "role": record_module.TURN_ROLE_HANDOFF_VALIDATION,
        "outcome": None,
        "actions": (broker_module.ACTION_VALIDATE_HANDOFF,),
    },
    # VALIDATED is reachable ONLY through a handoff_validation turn
    # that returned request_dispatch; that recorded turn is the
    # standing dispatch request, re-validated (freshness included)
    # before the dispatch action runs. A STALE standing request is
    # replaced by a fresh turn of the same role in advance_workflow
    # (I3-L1: never a dead end).
    record_module.PHASE_VALIDATED: {
        "mode": STEP_RECORDED_REQUEST,
        "role": record_module.TURN_ROLE_HANDOFF_VALIDATION,
        "outcome": OUTCOME_REQUEST_DISPATCH,
        "actions": (broker_module.ACTION_DISPATCH,),
    },
}

PROBLEM_REQUEST_TURN_INCOMPLETE = "runtime_request_turn_incomplete"
PROBLEM_REQUEST_WRONG_OUTCOME = "runtime_request_wrong_outcome"
PROBLEM_REQUEST_WRONG_ROLE = "runtime_request_wrong_role"
PROBLEM_REQUEST_STALE = "runtime_request_stale"
PROBLEM_REQUEST_MISSING = "runtime_request_missing"
PROBLEM_WRONG_PHASE = "runtime_wrong_phase"
PROBLEM_AMBIGUOUS = "runtime_ambiguous_workflow"
PROBLEM_WRONG_REVISION = "runtime_wrong_revision"
PROBLEM_CONTROL_MISMATCH = "runtime_control_mismatch"
PROBLEM_POLICY_DRIFT = "runtime_policy_digest_drift"
PROBLEM_TARGET_IDENTITY = "runtime_target_identity_mismatch"
PROBLEM_STORE_UNREADABLE = "runtime_store_unreadable"
PROBLEM_CAPABILITY_MINT = "runtime_capability_mint_failed"
# I4 revision 2 (round-08 F-2): the workflow record's codex_turns
# list is at its hard bound, so no further standing recovery request
# can ever be recorded. TRUTHFUL name — the store is readable; the
# RECORD is at capacity (reporting it as store-unreadable was the
# recorded wrong-field class).
PROBLEM_TURN_CAPACITY_EXHAUSTED = (
    "runtime_codex_turn_capacity_exhausted"
)

REQUEST_LABEL_PREFIX = "request:"


def _refusal(problem, detail=None):
    return broker_module.BrokerOutcome(
        False, problem=problem, detail=detail
    )


def claimable_workflows(store_directory):
    """Workflow ids in a phase this Runtime can advance, with the
    revision each claim must present (read from the record — the
    Broker re-checks it against its own read).

    Read-only; fail-closed: an unreadable store yields no claims
    (and /status reports it independently).
    """
    store = store_module.WorkflowStore(store_directory)
    with store_module.exclusive_store_lock(store_directory):
        try:
            workflows = store.load()
        except store_module.StoreError:
            return []
    claims = []
    for workflow_id in sorted(workflows["workflows"]):
        entry = workflows["workflows"][workflow_id]
        try:
            record_module.validate_record(entry)
        except record_module.RecordError:
            continue  # the Broker gate reports invalid records
        if (
            entry["phase"] in _STEPS
            or entry["phase"] in _I5_PHASES
        ) and entry["ambiguity"][
            "state"
        ] == record_module.AMBIGUITY_NONE:
            claims.append(
                (workflow_id, entry["handoff"]["revision"])
            )
    return claims


def _load_entry(broker, workflow_id):
    with store_module.exclusive_store_lock(broker.store.directory):
        try:
            workflows = broker.store.load()
        except store_module.StoreError as exc:
            return None, _refusal(PROBLEM_STORE_UNREADABLE, str(exc))
    entry = workflows["workflows"].get(workflow_id)
    return entry, None


def validate_transition_request(broker, entry, step, request_turn,
                                revision, now):
    """The Runtime's INDEPENDENT validation, before any effect.

    ``entry`` is a FRESH disk read. Returns ``(True, None)`` or
    ``(False, refusal)``; every refusal writes nothing and leaves the
    workflow exactly where it was.
    """
    if entry["phase"] not in _STEPS or _STEPS[entry["phase"]] is not (
        step
    ):
        return False, _refusal(
            PROBLEM_WRONG_PHASE,
            "the workflow moved to phase %r while the request was in"
            " flight; the request authorizes nothing"
            % entry["phase"],
        )
    if entry["ambiguity"]["state"] != record_module.AMBIGUITY_NONE:
        return False, _refusal(
            PROBLEM_AMBIGUOUS,
            "ambiguity state %r; a crash-ambiguous workflow is never"
            " advanced" % entry["ambiguity"]["state"],
        )
    if revision != entry["handoff"]["revision"]:
        return False, _refusal(
            PROBLEM_WRONG_REVISION,
            "claim revision %r is not the record's handoff revision"
            " %r" % (revision, entry["handoff"]["revision"]),
        )
    if entry["control_identity"]["repository_realpath"] != (
        broker.control_realpath
    ):
        return False, _refusal(
            PROBLEM_CONTROL_MISMATCH,
            "record names control %r; this Runtime is pinned to %r"
            % (
                entry["control_identity"]["repository_realpath"],
                broker.control_realpath,
            ),
        )
    try:
        live_digest = control_policy_digest(broker.control_realpath)
    except DigestError as exc:
        return False, _refusal(PROBLEM_POLICY_DRIFT, str(exc))
    if live_digest != entry["control_identity"][
        "policy_digest_sha256"
    ]:
        return False, _refusal(
            PROBLEM_POLICY_DRIFT,
            "the LIVE control policy digest does not match the one"
            " this workflow was authorized under",
        )
    # Target identity: the record's stored owner/repo must name the
    # SAME repository identity as its canonical URL — compared with
    # the case-folded identity key (binding I1 ruling), never URL
    # string equality.
    try:
        parsed_target = canonical_module.canonicalize_repository_url(
            entry["target"]["canonical_url"]
        )
    except canonical_module.CanonicalizationError as exc:
        return False, _refusal(PROBLEM_TARGET_IDENTITY, str(exc))
    stored_identity = "%s/%s/%s" % (
        entry["target"]["canonical_host"],
        entry["target"]["owner"].casefold(),
        entry["target"]["repo"].casefold(),
    )
    if canonical_module.repository_identity_key(parsed_target) != (
        stored_identity
    ):
        return False, _refusal(
            PROBLEM_TARGET_IDENTITY,
            "the record's owner/repo do not name the same repository"
            " identity as its canonical URL",
        )
    # BELT ONLY (round-05 F-2): both request-shape checks below are
    # unreachable through advance_workflow — a missing standing
    # request routes to the refresh path (which supplies a fresh turn
    # of the required role), and `_latest_turn` filters by exactly
    # `step["role"]`, so a wrong-role turn can never be selected.
    # Those two invariants are pinned by
    # test_missing_or_wrong_role_requests_route_to_refresh; the belts
    # stay for DIRECT callers of this function and are driven
    # directly by their own tests.
    if request_turn is None:
        return False, _refusal(
            PROBLEM_REQUEST_MISSING,
            "no structured request turn authorizes this transition",
        )
    if request_turn.get("role") != step["role"]:
        return False, _refusal(
            PROBLEM_REQUEST_WRONG_ROLE,
            "the request turn role %r is not the required %r"
            % (request_turn.get("role"), step["role"]),
        )
    staleness = _request_staleness(request_turn, now)
    if staleness is not None:
        return False, _refusal(
            PROBLEM_REQUEST_STALE,
            "the request turn is outside the request validity window"
            " (%s); a stale proposal authorizes nothing — the"
            " Runtime obtains a fresh turn instead" % staleness,
        )
    return True, None


def _append_turn_durably(broker, workflow_id, turn):
    """Record a request turn's identity in the workflow record."""
    with store_module.exclusive_store_lock(broker.store.directory):
        try:
            workflows = broker.store.load()
        except store_module.StoreError as exc:
            return _refusal(PROBLEM_STORE_UNREADABLE, str(exc))
        entry = workflows["workflows"].get(workflow_id)
        if entry is None:
            return _refusal(
                PROBLEM_STORE_UNREADABLE,
                "workflow %r vanished before its request turn could"
                " be recorded" % workflow_id,
            )
        entry["codex_turns"] = list(entry["codex_turns"]) + [
            dict(turn)
        ]
        try:
            broker.store.save(workflows)
        except store_module.StoreError as exc:
            return _refusal(PROBLEM_STORE_UNREADABLE, str(exc))
    return None


def _apply_proposed_stop(broker, workflow_id, outcome, turn):
    """Durably apply a needs_reauthorization/blocked proposal."""
    target_phase = (
        record_module.PHASE_NEEDS_REAUTHORIZATION
        if outcome == OUTCOME_NEEDS_REAUTHORIZATION
        else record_module.PHASE_BLOCKED
    )
    with store_module.exclusive_store_lock(broker.store.directory):
        try:
            workflows = broker.store.load()
        except store_module.StoreError as exc:
            return _refusal(PROBLEM_STORE_UNREADABLE, str(exc))
        entry = workflows["workflows"].get(workflow_id)
        if entry is None:
            return _refusal(PROBLEM_STORE_UNREADABLE, "vanished")
        if turn is not None:
            entry["codex_turns"] = list(entry["codex_turns"]) + [
                dict(turn)
            ]
        try:
            record_module.apply_transition(entry, target_phase)
            broker.store.save(workflows)
        except (record_module.RecordError,
                store_module.StoreError) as exc:
            return _refusal(PROBLEM_STORE_UNREADABLE, str(exc))
    return broker_module.BrokerOutcome(
        True, phase=target_phase, outcome=outcome
    )


def _latest_turn(entry, role):
    for turn in reversed(entry["codex_turns"]):
        if turn["role"] == role:
            return turn
    return None


def _request_staleness(request_turn, now):
    """Reason string when the request turn is missing or outside the
    validity window, else None. The SINGLE staleness predicate: the
    independent validation and the refresh decision must never
    disagree about what "stale" means."""
    if request_turn is None:
        return "no request turn is recorded"
    recorded_at = request_turn.get("recorded_at")
    if (
        not isinstance(recorded_at, (int, float))
        or isinstance(recorded_at, bool)
        or now - recorded_at > REQUEST_VALIDITY_SECONDS
        or recorded_at > now
    ):
        return (
            "recorded_at %r is outside the %d-second validity window"
            " at %r" % (recorded_at, REQUEST_VALIDITY_SECONDS, now)
        )
    return None


def advance_workflow(broker, workflow_id, revision):
    """Advance ONE workflow as far as validated requests allow.

    Returns the ordered list of (label, BrokerOutcome); labels are
    Broker action names, or ``request:<role>`` for the Runtime's own
    request/validation refusals and applied stop proposals. Stops at
    the first refusal — which writes nothing — or at a phase with no
    step.

    ONE clock (I4 D5, closing the I3 review carry-over): every
    time-dependent decision in a pass — request freshness, capability
    mint, capability consumption inside ``broker.perform`` — draws
    from ``broker._clock``. There is deliberately NO separate clock
    parameter: a second clock source made mint/consume skew
    representable and produced a spurious ``capability_expired``.
    """
    now_fn = broker._clock
    results = []
    # Room for the I3 forward steps plus the I5 completion phases
    # (DISPATCHED verify + follow-up, VERIFIED complete).
    for _ in range(len(_STEPS) + 3):
        entry, refusal = _load_entry(broker, workflow_id)
        if refusal is not None:
            results.append((REQUEST_LABEL_PREFIX + "load", refusal))
            return results
        if entry is None:
            return results
        phase = entry["phase"]
        if phase in _I5_PHASES:
            keep_going = _advance_i5_phase(
                broker, workflow_id, revision, phase, entry, now_fn,
                results,
            )
            if keep_going:
                continue
            return results
        step = _STEPS.get(phase)
        if step is None:
            return results
        label = REQUEST_LABEL_PREFIX + (step["role"] or "none")
        now = now_fn()
        request_turn = None
        run_fresh_turn = step["mode"] == STEP_LIVE_REQUEST
        if step["mode"] == STEP_RECORDED_REQUEST:
            request_turn = _latest_turn(entry, step["role"])
            if _request_staleness(request_turn, now) is not None:
                # I3-L1: a stale (or missing) STANDING request is
                # never a dead end — re-validation is read-only and
                # re-runnable, so the Runtime obtains a FRESH turn of
                # the same role right here. The workflow either
                # advances under the fresh request or reaches an
                # honest durable stop; it can never sit permanently
                # unadvanceable in a non-terminal phase (that would
                # be a de-facto mission timeout, which the mission
                # forbids).
                run_fresh_turn = True
        if run_fresh_turn:
            result = broker._role_turn(step["role"], entry, now)
            if result.status != ROLE_TURN_COMPLETED or (
                result.outcome is None
            ):
                results.append((label, _refusal(
                    PROBLEM_REQUEST_TURN_INCOMPLETE,
                    "the fresh %s request turn did not complete with"
                    " a structured outcome (status %s, reason %s);"
                    " nothing was advanced"
                    % (step["role"], result.status, result.reason),
                )))
                return results
            # C3, ONE load-bearing check: the only acceptable
            # proposals are this step's expected transition or a
            # fail-closed stop — and the pinned vocabulary table
            # guarantees all three sit inside the role's allowed
            # subset, so a token outside the subset (or outside the
            # vocabulary entirely) is refused here INDEPENDENTLY of
            # the parse layer, which the injected turn seam may
            # bypass. Codex cannot manufacture a transition.
            acceptable = (
                step["outcome"],
                OUTCOME_NEEDS_REAUTHORIZATION,
                OUTCOME_BLOCKED,
            )
            if result.outcome not in acceptable:
                results.append((label, _refusal(
                    PROBLEM_REQUEST_WRONG_OUTCOME,
                    "the %s turn proposed %r; the only acceptable"
                    " proposals at this step are %s — the proposal"
                    " authorizes nothing"
                    % (step["role"], result.outcome,
                       ", ".join(acceptable)),
                )))
                return results
            if result.outcome in (
                OUTCOME_NEEDS_REAUTHORIZATION, OUTCOME_BLOCKED
            ):
                results.append((label, _apply_proposed_stop(
                    broker, workflow_id, result.outcome, result.turn
                )))
                return results
            request_turn = result.turn
            # The role turn took real time; the independent
            # validation below must run against a FRESH disk read,
            # not the pre-turn snapshot.
            entry, refusal = _load_entry(broker, workflow_id)
            if refusal is not None or entry is None:
                results.append((label, refusal or _refusal(
                    PROBLEM_STORE_UNREADABLE,
                    "workflow %r vanished during its request turn"
                    % workflow_id,
                )))
                return results
        if step["mode"] != STEP_ACTION_EMBEDDED_REQUEST:
            ok, refusal = validate_transition_request(
                broker, entry, step, request_turn, revision, now
            )
            if not ok:
                results.append((label, refusal))
                return results
            if run_fresh_turn:
                append_refusal = _append_turn_durably(
                    broker, workflow_id, request_turn
                )
                if append_refusal is not None:
                    results.append((label, append_refusal))
                    return results
        for action in step["actions"]:
            try:
                token = capability_module.mint(
                    broker.store.directory, workflow_id, action,
                    revision, now_fn(),
                )
            except capability_module.CapabilityError as exc:
                results.append((action, _refusal(
                    PROBLEM_CAPABILITY_MINT, str(exc)
                )))
                return results
            outcome = broker.perform(
                workflow_id, action, revision, capability=token
            )
            results.append((action, outcome))
            if not outcome.ok:
                return results
    return results


# The I5 completion phases the Runtime advances beyond DISPATCH.
# DISPATCHED: observe read-only + verify (and, on request_follow_up, a
# bounded corrective follow-up); VERIFIED: complete. Each action is
# capability-gated exactly like the I3 forward actions.
_I5_PHASES = frozenset(
    (record_module.PHASE_DISPATCHED, record_module.PHASE_VERIFIED)
)

def dispatch_identity_unresolved(entry):
    """The D-B1 dispatch-ambiguity predicate — PURE DURABLE STATE,
    zero model calls.

    True exactly when a dispatch happened but the exact target-engine
    identity was never durably established. The two durable
    unresolved shapes come from the dispatch layer's own contract
    (``target_identity_from_spawn`` NEVER returns None; an absent id
    falls back to ``dispatch.UNRESOLVED_TASK_ID``):

    - ``target_engine is None`` — a crash between the durable
      dispatch marker and the identity save;
    - ``task_id`` equal to the dispatch layer's unresolved sentinel
      (referenced from ``dispatch_module``, never retyped), or not a
      non-empty string — the bridge returned no usable id.

    A bound identity (any other non-empty string) means an exact
    existing child is already bound: the predicate is False.

    Deliberately DERIVED from ``target_engine`` and NEVER stored in
    (or read from) the record's ``ambiguity`` field:
    ``AMBIGUITY_CRASH_UNCERTAIN`` is a different concept (workspace
    crash uncertainty) and ``claimable_workflows`` refuses to claim
    an ambiguous workflow — writing dispatch ambiguity there would
    strand the workflow unclaimable, the exact dead-end class this
    task closes.
    """
    if entry["phase"] != record_module.PHASE_DISPATCHED:
        return False
    if dispatch_module.dispatch_count(entry) < 1:
        return False
    engine = entry["target_engine"]
    if engine is None:
        return True
    task_id = engine.get("task_id")
    return (
        not isinstance(task_id, str)
        or not task_id
        or task_id == dispatch_module.UNRESOLVED_TASK_ID
    )


def _perform_capability_action(broker, workflow_id, revision, action,
                               now_fn, results):
    """Mint a one-shot capability and perform ONE Broker action,
    recording the outcome. Returns the BrokerOutcome, or None on a
    capability-mint failure (already recorded)."""
    try:
        token = capability_module.mint(
            broker.store.directory, workflow_id, action, revision,
            now_fn(),
        )
    except capability_module.CapabilityError as exc:
        results.append((action, _refusal(
            PROBLEM_CAPABILITY_MINT, str(exc)
        )))
        return None
    outcome = broker.perform(
        workflow_id, action, revision, capability=token
    )
    results.append((action, outcome))
    return outcome


def _handle_dispatch_recovery(broker, workflow_id, revision, entry,
                              now_fn, results):
    """The recovery step for an identity-unresolved DISPATCHED
    workflow (I4 predicate + turn, I5 action). A fresh Codex turn
    runs when and ONLY when the D-B1 predicate fires AND no FRESH
    standing recovery request exists (the SINGLE staleness
    predicate, ``_request_staleness`` — TRAP 2: without the
    freshness clause a fresh Codex process would run every pass, an
    unbounded model-call loop). A FRESH standing request maps
    DETERMINISTICALLY to the ONE fixed Broker action,
    ``reconcile_dispatch`` — evidence-only, capability-gated, zero
    model calls — which binds the single provable child, stops
    durably BLOCKED, or (on a gate refusal) changes nothing for a
    later pass. When the standing request goes stale, one fresh
    turn per validity window re-establishes it — bounded pacing,
    the same standing-request behaviour every other step has.

    A completed ``request_recovery`` records the turn durably as
    the standing recovery request and then performs the mapped
    action in the same pass. ``blocked`` applies the existing
    durable stop. Every other token — outside the role's subset or
    outside the vocabulary entirely — authorizes nothing and writes
    nothing (the C3 check, INDEPENDENT of the parse layer). The
    record's ``ambiguity`` field is never touched.
    """
    label = REQUEST_LABEL_PREFIX + (
        record_module.TURN_ROLE_STATUS_RECOVERY
    )
    now = now_fn()
    standing = _latest_turn(
        entry, record_module.TURN_ROLE_STATUS_RECOVERY
    )
    if standing is not None and _request_staleness(
        standing, now
    ) is None:
        # A FRESH standing recovery request exists: request_recovery
        # maps DETERMINISTICALLY to the ONE fixed Broker action (I5)
        # — evidence-only, capability-gated, zero model calls. It
        # either binds the single provable child, stops durably
        # BLOCKED, or (on a gate/transport refusal) leaves the
        # workflow exactly where it was for a later pass.
        _perform_capability_action(
            broker, workflow_id, revision,
            broker_module.ACTION_RECONCILE, now_fn, results,
        )
        return
    # CAPACITY BEFORE SPAWN (round-08 F-2): a fresh turn is only
    # worth its process cost if its identity can be RECORDED as the
    # standing request. At the codex_turns hard bound the append
    # would be refused after the spawn, no standing request would
    # ever exist again, and the freshness guard above could never be
    # true — one fresh Codex process per pass, forever. So at
    # capacity the workflow stops DURABLY with a truthful code
    # (BLOCKED, not NEEDS_REAUTHORIZATION: re-authorization reuses
    # this same at-capacity record and would hit the bound on its
    # very next turn — a fresh Mission Authorization, i.e. a new
    # workflow, is the honest continuation). The audit trail is
    # append-only and is NOT rewritten to make room: ~250 recorded
    # recovery attempts are exactly the evidence an operator needs.
    if len(entry["codex_turns"]) >= record_module.MAX_CODEX_TURNS:
        stop = _apply_proposed_stop(
            broker, workflow_id, OUTCOME_BLOCKED, None
        )
        if not stop.ok:
            results.append((label, stop))
            return
        results.append((label, broker_module.BrokerOutcome(
            True, phase=stop.phase, outcome=OUTCOME_BLOCKED,
            problem=PROBLEM_TURN_CAPACITY_EXHAUSTED,
            detail="the workflow record already holds %d codex turn"
            " identities (hard bound %d); no further standing"
            " recovery request can be recorded, so dispatch recovery"
            " cannot proceed — stopped durably rather than spawning"
            " an unrecordable turn every pass. A fresh Mission"
            " Authorization (a new workflow) is required to continue"
            % (
                len(entry["codex_turns"]),
                record_module.MAX_CODEX_TURNS,
            ),
        )))
        return
    result = broker._role_turn(
        record_module.TURN_ROLE_STATUS_RECOVERY, entry, now
    )
    if result.status != ROLE_TURN_COMPLETED or (
        result.outcome is None
    ):
        results.append((label, _refusal(
            PROBLEM_REQUEST_TURN_INCOMPLETE,
            "the fresh status_recovery turn did not complete with a"
            " structured outcome (status %s, reason %s); nothing was"
            " advanced" % (result.status, result.reason),
        )))
        return
    # C3, independent of the parse layer: exactly the role's two
    # allowed outcomes; anything else authorizes nothing.
    acceptable = (OUTCOME_REQUEST_RECOVERY, OUTCOME_BLOCKED)
    if result.outcome not in acceptable:
        results.append((label, _refusal(
            PROBLEM_REQUEST_WRONG_OUTCOME,
            "the status_recovery turn proposed %r; the only"
            " acceptable proposals are %s — the proposal authorizes"
            " nothing" % (result.outcome, ", ".join(acceptable)),
        )))
        return
    # Belt: the recorded turn must CARRY the status_recovery role, or
    # the standing-request derivation could never find it and the
    # turn would re-run every pass (the loop TRAP 2 closes).
    if not isinstance(result.turn, dict) or result.turn.get(
        "role"
    ) != record_module.TURN_ROLE_STATUS_RECOVERY:
        results.append((label, _refusal(
            PROBLEM_REQUEST_WRONG_ROLE,
            "the recovery turn's recorded role %r is not"
            " status_recovery; recording it would not establish a"
            " standing recovery request"
            % (result.turn.get("role")
               if isinstance(result.turn, dict) else None),
        )))
        return
    if result.outcome == OUTCOME_BLOCKED:
        results.append((label, _apply_proposed_stop(
            broker, workflow_id, OUTCOME_BLOCKED, result.turn
        )))
        return
    # request_recovery: record the turn durably as the STANDING
    # recovery request, then perform the ONE mapped action in the
    # same pass (I5): the deterministic consumption of the request.
    append_refusal = _append_turn_durably(
        broker, workflow_id, result.turn
    )
    if append_refusal is not None:
        results.append((label, append_refusal))
        return
    results.append((label, broker_module.BrokerOutcome(
        True, phase=entry["phase"],
        outcome=OUTCOME_REQUEST_RECOVERY,
        detail="standing recovery request recorded; performing the"
        " reconcile action",
    )))
    _perform_capability_action(
        broker, workflow_id, revision,
        broker_module.ACTION_RECONCILE, now_fn, results,
    )


def _advance_i5_phase(broker, workflow_id, revision, phase, entry,
                      now_fn, results):
    """Advance ONE I5 completion phase. Returns True to keep advancing
    (the phase moved and a further step may apply), False to stop.

    Every action is capability-gated; a refusal writes nothing and
    stops. DISPATCHED with a still-running target stays DISPATCHED
    with no store write — a legitimate external wait, never a dead
    end (the Runtime re-observes on a later pass).

    ORDER WITHIN DISPATCHED, deliberate (I4): the dispatch-identity
    recovery check runs BEFORE the verify action. A workflow whose
    exact target-engine identity was never durably established must
    NOT silently verify as if it were fully bound — the identity
    binding is part of the durable audit chain behind a verified
    result, and verifying while I5's reconcile could concurrently
    bind would race two writers over one record. The predicate is
    pure durable state, so a healthy (resolved) workflow pays zero
    cost and proceeds straight to verify; an unresolved one takes
    EXACTLY ONE deterministic path per pass (wait, one fresh turn,
    or a durable stop) — no dead end, no double work."""
    if phase == record_module.PHASE_DISPATCHED:
        if dispatch_identity_unresolved(entry):
            _handle_dispatch_recovery(
                broker, workflow_id, revision, entry, now_fn, results
            )
            return False
        outcome = _perform_capability_action(
            broker, workflow_id, revision, broker_module.ACTION_VERIFY,
            now_fn, results,
        )
        if outcome is None or not outcome.ok:
            return False
        if outcome.outcome == "target_running":
            # Legitimate wait: nothing to advance this pass.
            return False
        if outcome.outcome == OUTCOME_REQUEST_FOLLOW_UP:
            # A bounded corrective follow-up: dispatch it (the bound,
            # R-2, is enforced inside ACTION_FOLLOW_UP and transitions
            # to NEEDS_REAUTHORIZATION when exceeded).
            follow = _perform_capability_action(
                broker, workflow_id, revision,
                broker_module.ACTION_FOLLOW_UP, now_fn, results,
            )
            if follow is None or not follow.ok:
                return False
            # Whether the follow-up dispatched (still DISPATCHED) or
            # hit the bound (now NEEDS_REAUTHORIZATION), stop this
            # pass; a fresh verify happens on the next pass if still
            # DISPATCHED.
            return False
        # verified_result -> VERIFIED, or a durable stop
        # (needs_reauthorization / blocked). Keep advancing so a
        # VERIFIED workflow completes in the same pass.
        return outcome.outcome == OUTCOME_VERIFIED_RESULT
    # VERIFIED -> COMPLETED.
    outcome = _perform_capability_action(
        broker, workflow_id, revision, broker_module.ACTION_COMPLETE,
        now_fn, results,
    )
    return False


def process_once(broker):
    """One full Runtime pass over every claimable workflow.

    Returns {workflow_id: [(label, BrokerOutcome), ...]} — exact,
    nothing sampled or capped.
    """
    processed = {}
    for workflow_id, revision in claimable_workflows(
        broker.store.directory
    ):
        processed[workflow_id] = advance_workflow(
            broker, workflow_id, revision
        )
    return processed
