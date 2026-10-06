"""Marshalling for the client-mediated decision tool ``di_mission_decide``:
the authority card, the reserved decision id, the elicitation round
trip, and exactly one Mission Core call per human answer.

Order, load-bearing and fail-closed:

1. schema refusal, unwired service, absent ingress, and an elicitation
   channel that cannot carry a round trip on this request (client did
   not negotiate form elicitation, no event-stream accept, pending
   table full) all refuse BEFORE anything is read or reserved;
2. the Mission is read; a requested revision that is not the current
   one refuses with the core's own stale-revision code, nothing
   reserved;
3. the FULL authority-bearing card of the current revision is rendered
   (every proposal field that carries authority, the complete proof
   contract when present, the engineering baseline when present, and
   the expiry semantics an accept will record); a card over the named
   bound refuses ``elicitation_presentation_oversized``, nothing
   reserved, nothing truncated;
4. a decision id is minted and durably reserved under the CLIENT
   CONFIRMATION context the server built after its bearer check — the
   context under which the answer, if any, will be applied;
5. the card is sent and the answer awaited through the channel;
6. accept -> ``apply_human_decision(APPROVE)`` for exactly the rendered
   revision and its exact requested scope, with ``expires_at`` bounded
   by a named constant; decline -> ``apply_human_decision(DENY)``;
   every other outcome (cancel, expiry, stream closure observed before
   admission, write failure, stream failure, server close, client
   error, binding mismatch) records NOTHING and reports the reservation
   as unconsumed.

Capacity is claimed on the channel BEFORE the decision id is minted and
released on every exit, so a competing call that takes the last pending
slot refuses with nothing reserved; and every refusal after the mint
names the reserved decision id, so reservation accounting is truthful
on the failure paths too.

Store failures are reported by what this relay can PROVE, never by
assumption, because the store's atomic write can fail after the new
document is already visible (the directory fsync follows the replace):

- mint: the store's own typed ``MissionStoreError`` is raised only
  before it writes (load, bound, validation, directory check) and is a
  refusal with nothing reserved; any other exception from the write
  itself is reported ``store_outcome_uncertain`` with no id, because a
  reservation may or may not exist and no read API exposes it;
- apply: EVERY exception out of the apply call — the core's own
  ``MissionError`` included, because the core evaluates its
  authorization projection AFTER its save (``apply_human_decision`` ->
  ``_decision_outcome``) and can raise with the decision already
  durable — is RECONCILED by the supported readback
  (``MissionService.get``). A decision recorded under the reserved id
  is reported as applied from that readback (never re-applied); when
  the current authorization projection cannot be computed, the PROVEN
  recorded decision is still reported (``decision_recorded: true``,
  ``authorization_projection: "unavailable (<Class>)"``); an absent
  decision is a refusal carrying the core problem with the id still
  unconsumed (``decision_recorded: false``); a readback that itself
  fails is ``store_outcome_uncertain`` with the id.

The reserved id, the mission as read for the card, the admitted
elicitation outcome and what the readback proved travel through the
whole post-mint path as ONE state object (``_PostMint``); no helper
re-derives them, so no later exception can drop them, and the whole
reconciliation sits inside one guard whose fallback still reports them.
The relay's outer fallback is unreachable once an id is reserved.

The accept can only be the client's form answer: this module reads
``arguments`` for ``mission_id`` and ``revision`` and for nothing else
(pinned by AST). The Mission Core applies its own stale-revision,
replay and scope rules on apply; an edit between the card and the
answer therefore refuses at the core with nothing issued.
"""

from mission import decision as mission_decision
from mission import record as mission_record
from mission import service as mission_service
from mission import store as mission_store

from grok_mcp import elicitation
from grok_mcp import mission_tools
from grok_mcp import protocol

# How long an authorization recorded by a client-confirmed accept stays
# live, from the decision time. Exact-value pinned.
CLIENT_CONFIRMED_AUTHORITY_SECONDS = 86400

STATUS_APPLIED = mission_tools.STATUS_APPLIED
STATUS_REFUSED = protocol.STATUS_REFUSED
STATUS_NOT_RECORDED = "not_recorded"
# The relay could neither prove nor disprove a durable effect: the store
# write raised and no supported readback settles it. The reserved id is
# reported whenever it is known; nothing is re-applied.
STATUS_UNCERTAIN = "store_outcome_uncertain"

# ``authorization_projection`` values: the current liveness projection
# was computed, or could not be (the recorded decision itself is proven).
PROJECTION_AVAILABLE = "available"
PROJECTION_UNAVAILABLE = "unavailable (%s)"

REASON_OVERSIZED = "elicitation_presentation_oversized"
REASON_BINDING_MISMATCH = "elicitation_binding_mismatch"
REASON_NOT_RECORDED = (
    "no decision was recorded; the reserved decision id stays unconsumed"
)

_CARD_HEADER = "DODGING INFINITY MISSION DECISION REQUEST"


def _base(status, ok, reason, problem, call_ref):
    return {
        "ok": ok, "reason": reason, "status": status, "problem": problem,
        "mission_id": None, "revision": None, "state": None,
        "decision_id": None, "proposal_digest_sha256": None,
        "idempotent": False, "current_revision": None, "current_state": None,
        "decision": None, "authorization_id": None,
        "authorization_digest_sha256": None, "authorized_action_scope": None,
        "authorized_delivery_targets": None, "authorization_live": None,
        "authorization_problem": None, "expires_at": None,
        "elicitation_outcome": None, "decision_recorded": None,
        "authorization_projection": None, "baseline": None,
        "call_ref": call_ref,
    }


def refusal(reason, problem, call_ref, outcome=None, decision_id=None,
            recorded=None):
    structured = _base(STATUS_REFUSED, False, reason, problem, call_ref)
    structured["elicitation_outcome"] = outcome
    structured["decision_id"] = decision_id
    structured["decision_recorded"] = recorded
    return structured, True


class _PostMint(object):
    """State carried through the ENTIRE post-mint path: the reserved id
    (None until the mint returned one), the mission as read for the
    card, the admitted elicitation outcome once known, and what the
    readback proved (``recorded``: True / False / None). Every helper
    after the mint reads from here and never re-derives these, so no
    later exception can drop them."""

    def __init__(self, mission_id, revision, mission, entry, call_ref):
        self.decision_id = None
        self.mission_id = mission_id
        self.revision = revision
        self.mission = mission
        self.entry = entry
        self.call_ref = call_ref
        # Task 8 S-IV: the approved baseline of the exact revision the
        # card showed (None when the revision declares none); reported
        # with an APPROVE result, never rendered as "none" when declared.
        self.baseline = entry["proposal"].get("baseline")
        self.outcome = None
        self.recorded = None
        # The COMMIT POINT facts, set the moment the core returned (or
        # readback proved the decision) and BEFORE any formatting: the
        # core's own projection and what proved the record.
        self.applied = None
        self.proof = None


def _state_result(state, failure, note):
    """The primitives-only result from carried state: attribute reads
    and string formatting only, nothing that can raise. The single exit
    shape for ANY raise after the mint became possible — formatting,
    projection, reconciliation or cleanup — so no helper can lose the
    reserved id, the admitted outcome or the recorded fact."""
    decision_id = state.decision_id
    if state.recorded is True:
        structured = _base(STATUS_APPLIED, True, (
            "%s; decision id %s IS recorded (proven by %s); reported from"
            " carried state, not re-applied" % (note, decision_id, state.proof)
        ), None, state.call_ref)
        structured["ok"] = True
        structured["decision"] = (
            mission_decision.DECISION_APPROVE
            if state.outcome == elicitation.OUTCOME_ACCEPT
            else mission_decision.DECISION_DENY
        )
        structured["decision_recorded"] = True
        structured["authorization_projection"] = PROJECTION_UNAVAILABLE % failure
        is_error = False
    elif state.recorded is False:
        structured = _base(STATUS_REFUSED, False, (
            "%s; decision id %s is not recorded (nothing was applied);"
            " the reservation stays unconsumed" % (note, decision_id)
        ), None, state.call_ref)
        structured["decision_recorded"] = False
        is_error = True
    else:
        structured = _base(STATUS_UNCERTAIN, False, (
            "%s; decision id %s may or may not be recorded; NOT re-applied"
            % (note, decision_id if decision_id is not None
               else "(none: the mint returned no id)")
        ), None, state.call_ref)
        is_error = True
    structured["mission_id"] = state.mission_id
    structured["revision"] = state.revision
    structured["decision_id"] = decision_id
    structured["elicitation_outcome"] = state.outcome
    return structured, is_error


def _quoted(text):
    return ["    " + line for line in (text or "").splitlines()] or ["    "]


def _contract_lines(contract):
    if contract is None:
        return ["proof contract: none in this revision"]
    lines = ["proof contract:"]
    for requirement in contract["requirements"]:
        lines.append(
            "  requirement %s: evidence kinds %s; required artifacts %s;"
            " max evidence age %d seconds" % (
                requirement["key"],
                ", ".join(requirement["evidence_kinds"]) or "(none)",
                ", ".join(requirement["required_artifact_keys"]) or "(none)",
                requirement["max_evidence_age_seconds"],
            )
        )
        lines.extend(_quoted(requirement["description"]))
    lines.append("  required artifacts:")
    for artifact in contract["required_artifacts"]:
        lines.append("    %s role %s digest %s" % (
            artifact["key"], artifact["role"],
            artifact["expected_content_digest_sha256"],
        ))
    if not contract["required_artifacts"]:
        lines.append("    (none)")
    lines.append("  required dependencies:")
    for dependency in contract["required_dependencies"]:
        target = dependency["target"]
        lines.append("    %s kind %s target %s" % (
            dependency["key"], dependency["kind"],
            " ".join("%s=%s" % (key, target[key]) for key in sorted(target)),
        ))
    if not contract["required_dependencies"]:
        lines.append("    (none)")
    lines.append("  required resource readiness:")
    for readiness in contract["required_resource_readiness"]:
        lines.append("    %s within %d seconds" % (
            readiness["resource_key"], readiness["max_age_seconds"],
        ))
    if not contract["required_resource_readiness"]:
        lines.append("    (none)")
    permitted = contract["degradation_policy"]["permitted_blocker_keys"]
    lines.append("  degradation policy: permitted blocker keys %s"
                 % (", ".join(permitted) or "(none)"))
    budget = contract["continuation_budget"]
    lines.append("  continuation budget: %d attempts, %d checkpoints"
                 % (budget["max_attempts"], budget["max_checkpoints"]))
    return lines


def render_card(mission, entry, confirm_value):
    """The complete authority-bearing card for ``entry`` (the current
    revision of ``mission``), or None when it exceeds the named bound.
    Nothing is truncated: over the bound is a refusal."""
    proposal = entry["proposal"]
    # Task 8 S-IV: the ONE canonical approved-baseline field of the
    # stored proposal (never a key the core does not store).
    baseline = proposal.get("baseline")
    lines = [
        _CARD_HEADER,
        "mission id: %s" % mission["mission_id"],
        "revision: %d" % entry["revision"],
        "proposal digest sha256: %s" % entry["proposal_digest_sha256"],
        "current state: %s" % mission["state"],
        "objective:",
    ]
    lines.extend(_quoted(proposal["objective"]))
    lines.append("target context:")
    lines.extend(_quoted(proposal["target_context"]))
    lines.append("repository: %s" % (proposal["repository_url"] or "(none)"))
    lines.append("requested scope:")
    lines.extend(_quoted(proposal["requested_scope"]))
    lines.append("requested action scope: %s"
                 % ", ".join(proposal["requested_action_scope"]))
    lines.append("requested delivery target: %s"
                 % (proposal["requested_delivery_target"] or "(none)"))
    lines.extend(_contract_lines(proposal.get("proof_contract")))
    if isinstance(baseline, dict):
        # The exact ref and commit the approval binds, visibly.
        lines.append("baseline: ref=%s commit_sha=%s"
                     % (baseline["ref"], baseline["commit_sha"]))
    else:
        lines.append("baseline: none declared in this revision (the Mission"
                     " cannot be engaged until an EDIT declares one)")
    verification = proposal.get("verification")
    if isinstance(verification, dict):
        # Task 8 S-VI: the exact argv the Runtime will run in the leased
        # workspace to verify a delivery candidate — authority-bearing.
        lines.append("verification argv (run by the Runtime in the leased"
                     " workspace before any delivery): %s"
                     % (verification["argv"],))
    lines.extend([
        "expiry semantics: ACCEPT records an authorization for exactly this"
        " revision and its requested scope, valid until the decision time"
        " plus %d seconds; DECLINE records a denial; CANCEL records nothing."
        % CLIENT_CONFIRMED_AUTHORITY_SECONDS,
        "this decision dispatches nothing, runs nothing and performs no"
        " repository action.",
        "to accept, answer the form field with the first %d characters of"
        " the proposal digest: %s"
        % (protocol.ELICITATION_CONFIRM_CHARS, confirm_value),
    ])
    text = "\n".join(lines)
    if len(text) > protocol.MAX_ELICITATION_MESSAGE_CHARS:
        return None
    return text


def _uncertain(state, reason):
    """Truthful uncertainty from the carried state: ``ok`` false, the
    reserved id when the mint returned one, the admitted outcome, the
    mission as read for the card, and what readback proved (if it did)."""
    structured = _base(STATUS_UNCERTAIN, False, reason, None, state.call_ref)
    structured.update({
        "mission_id": state.mission["mission_id"],
        "revision": state.entry["revision"],
        "state": state.mission["state"],
        "decision_id": state.decision_id,
        "proposal_digest_sha256": state.entry["proposal_digest_sha256"],
        "current_revision": state.mission["current_revision"],
        "current_state": state.mission["state"],
        "elicitation_outcome": state.outcome,
        "decision_recorded": state.recorded,
    })
    return structured, True


def _recorded_only(stored, recorded):
    """The PROVEN part of a recorded decision, from the validated
    decision record and the mission record only: what it did and which
    authorization it issued. The current liveness projection is left
    unknown (None); the caller marks it unavailable."""
    result = recorded["outcome"]
    mission = stored["record"]
    approved = recorded["decision"] == mission_decision.DECISION_APPROVE
    return {
        "decision": recorded["decision"],
        "decision_id": recorded["decision_id"],
        "mission_id": recorded["mission_id"],
        "revision": result["resulting_revision"],
        "state": result["resulting_state"],
        "proposal_digest_sha256": result["proposal_digest_sha256"],
        "authorization_id": result["authorization_id"],
        "authorization_digest_sha256": None,
        "authorized_action_scope": (
            list(recorded["approved_action_scope"]) if approved else None
        ),
        "authorized_delivery_targets": (
            list(recorded["approved_delivery_targets"]) if approved else None
        ),
        "expires_at": recorded["expires_at"] if approved else None,
        "idempotent": False,
        "current_revision": mission["current_revision"],
        "current_state": mission["state"],
        "authorization_live": None,
        "authorization_problem": None,
    }


def _readback_outcome(stored, recorded, service):
    """The decision projection rebuilt from the supported readback
    (``MissionService.get`` plus the public authorization validator):
    the same keys ``apply_human_decision`` returns, read, not re-applied."""
    result = recorded["outcome"]
    mission = stored["record"]
    authorization_id = result["authorization_id"]
    approved = recorded["decision"] == mission_decision.DECISION_APPROVE
    digest = None
    live = None
    problem = None
    if authorization_id is not None:
        by_id = dict(zip(mission["authorization_ids"], stored["authorizations"]))
        digest = by_id[authorization_id]["authorization_digest_sha256"]
        check = service.validate_authorization(
            authorization_id, recorded["mission_id"], recorded["revision"],
        )
        live = check.valid
        problem = check.problem
    return {
        "decision": recorded["decision"],
        "decision_id": recorded["decision_id"],
        "mission_id": recorded["mission_id"],
        "revision": result["resulting_revision"],
        "state": result["resulting_state"],
        "proposal_digest_sha256": result["proposal_digest_sha256"],
        "authorization_id": authorization_id,
        "authorization_digest_sha256": digest,
        "authorized_action_scope": (
            list(recorded["approved_action_scope"]) if approved else None
        ),
        "authorized_delivery_targets": (
            list(recorded["approved_delivery_targets"]) if approved else None
        ),
        "expires_at": recorded["expires_at"] if approved else None,
        "idempotent": False,
        "current_revision": mission["current_revision"],
        "current_state": mission["state"],
        "authorization_live": live,
        "authorization_problem": problem,
    }


def _settle(state, exc, service):
    """ONE guard around the whole reconciliation. Whatever raises inside
    it degrades to the primitives-only state result, which still carries
    the reserved id, the admitted outcome and the recorded fact (proven
    by the apply's return or by readback). Nothing here re-applies."""
    try:
        return _reconcile(state, exc, service)
    except Exception as inner:  # noqa: BLE001 - reported by class only
        return _state_result(state, type(inner).__name__, (
            "applying the %s decision raised %s; reconciling that raised %s"
            % (state.outcome, type(exc).__name__, type(inner).__name__)
        ))


def _reconcile(state, exc, service):
    """After the answer, something raised. If the core had already
    RETURNED (commit point recorded in state), its retained projection
    is reported — no readback needed. Otherwise the exception type alone
    proves nothing (the core can raise after its save), so settle by
    readback of the reserved id: recorded -> applied from the readback
    (never re-applied; if only the current authorization projection
    cannot be computed, the proven decision is still reported); absent
    -> refused with the core problem, id unconsumed; readback failing ->
    uncertain. The id and the admitted outcome are always named."""
    name = type(exc).__name__
    problem = getattr(exc, "problem", None)
    if state.applied is not None:
        return _applied(state.applied, state.outcome, state.call_ref, (
            "the apply returned; formatting its result raised %s; reported"
            " from the apply's retained result, not re-applied" % name
        ), baseline=state.baseline)
    try:
        stored = service.get(state.mission_id)
    except Exception as read_exc:  # noqa: BLE001 - reported by class only
        return _uncertain(state, (
            "applying the %s decision raised %s and the readback raised %s;"
            " whether decision id %s was recorded is not known to this relay"
            " and it was NOT re-applied"
            % (state.outcome, name, type(read_exc).__name__,
               state.decision_id)
        ))
    recorded = None
    for candidate in stored["record"]["decisions"]:
        if candidate["decision_id"] == state.decision_id:
            recorded = candidate
    if recorded is None:
        state.recorded = False
        return refusal(
            "%s (%s); the readback shows no decision recorded under reserved"
            " id %s, which stays unconsumed; it was not re-applied"
            % (exc, name, state.decision_id),
            problem, state.call_ref, state.outcome, state.decision_id,
            recorded=False,
        )
    state.recorded = True
    state.proof = "readback"
    note = (
        "applying the %s decision raised %s after the decision was durably"
        " recorded; reported from readback, not re-applied"
        % (state.outcome, name)
    )
    try:
        projection = _readback_outcome(stored, recorded, service)
    except Exception as projection_exc:  # noqa: BLE001 - class name only
        kind = type(projection_exc).__name__
        return _applied(
            _recorded_only(stored, recorded), state.outcome, state.call_ref,
            note + "; the current authorization projection could not be"
            " computed (%s)" % kind, PROJECTION_UNAVAILABLE % kind,
            baseline=state.baseline,
        )
    return _applied(projection, state.outcome, state.call_ref, note,
                    baseline=state.baseline)


def _applied(outcome, elicitation_outcome, call_ref, reason=None,
             projection=PROJECTION_AVAILABLE, baseline=None):
    structured = _base(STATUS_APPLIED, True, reason, None, call_ref)
    structured["decision_recorded"] = True
    structured["authorization_projection"] = projection
    structured["baseline"] = (
        dict(baseline)
        if baseline is not None
        and outcome["decision"] == mission_decision.DECISION_APPROVE
        else None)
    structured.update({
        "mission_id": outcome["mission_id"],
        "revision": outcome["revision"],
        "state": outcome["state"],
        "decision_id": outcome["decision_id"],
        "proposal_digest_sha256": outcome["proposal_digest_sha256"],
        "idempotent": outcome["idempotent"],
        "current_revision": outcome["current_revision"],
        "current_state": outcome["current_state"],
        "decision": outcome["decision"],
        "authorization_id": outcome["authorization_id"],
        "authorization_digest_sha256": outcome["authorization_digest_sha256"],
        "authorized_action_scope": outcome["authorized_action_scope"],
        "authorized_delivery_targets": outcome["authorized_delivery_targets"],
        "authorization_live": outcome["authorization_live"],
        "authorization_problem": outcome["authorization_problem"],
        "expires_at": outcome["expires_at"],
        "elicitation_outcome": elicitation_outcome,
    })
    return structured, False


def relay(name, arguments, schema_reason, ingress, client_ingress, service,
          call_ref, channel, delivery_desk=None):
    """Execute the decision tool; returns ``(structured, is_error)``."""
    if name == protocol.TOOL_DELIVERY_DECIDE:
        return _delivery_relay(arguments, schema_reason, ingress, client_ingress,
                               service, delivery_desk, call_ref, channel)
    if schema_reason is not None:
        return refusal(schema_reason, None, call_ref)
    if service is None:
        return refusal(mission_tools.REASON_NOT_WIRED, None, call_ref)
    if not isinstance(ingress, mission_record.AuthenticatedContext) or (
        not isinstance(client_ingress, mission_record.AuthenticatedContext)
    ):
        return refusal(mission_tools.REASON_NO_INGRESS, None, call_ref)
    if client_ingress.principal_kind != (
        mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION
    ):
        return refusal(mission_tools.REASON_NO_INGRESS, None, call_ref)
    if channel is None or channel.refusal is not None:
        return refusal(
            channel.refusal if channel is not None
            else elicitation.REFUSAL_SSE_NOT_ACCEPTED, None, call_ref,
        )
    try:
        return _decide(arguments, client_ingress, service, call_ref, channel)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        # A raise that reaches here AFTER an id was reserved can only be
        # one at a call boundary (the relay's own path never lets one
        # out): the recorded result, or the carried state, is what is
        # returned — never a refusal that forgets the id.
        if channel.last_result is not None:
            return channel.last_result
        if channel.state is not None:
            return _state_result(channel.state, type(exc).__name__,
                                 "decision relay raised %s"
                                 % type(exc).__name__)
        if isinstance(exc, (mission_record.MissionError,
                            mission_store.MissionStoreError)):
            return refusal(str(exc), exc.problem, call_ref)
        return refusal("decision relay raised %s" % type(exc).__name__,
                       None, call_ref)


def _decide(arguments, client_ingress, service, call_ref, channel):
    mission_id = arguments["mission_id"]
    revision = arguments["revision"]
    stored = service.get(mission_id)
    mission = stored["record"]
    entry = mission["revisions"][-1]
    if revision != entry["revision"]:
        return refusal(
            "decision names revision %d but the mission is at revision %d;"
            " re-read the mission and decide on the current revision"
            % (revision, entry["revision"]),
            mission_service.PROBLEM_STALE_REVISION, call_ref,
        )
    confirm_value = entry["proposal_digest_sha256"][
        :protocol.ELICITATION_CONFIRM_CHARS
    ]
    card = render_card(mission, entry, confirm_value)
    if card is None:
        return refusal(
            "the rendered authority card exceeds %d characters; it is"
            " refused rather than truncated and nothing was reserved"
            % protocol.MAX_ELICITATION_MESSAGE_CHARS,
            REASON_OVERSIZED, call_ref,
        )
    # The slot is claimed BEFORE the durable reservation and released on
    # every exit; a claim that fails reserves nothing.
    if not channel.claim():
        return refusal(elicitation.REFUSAL_TABLE_FULL, None, call_ref)
    state = _PostMint(mission_id, revision, mission, entry, call_ref)
    channel.state = state
    # SINGLE EXIT SHAPE: from here on ANY raise — mint, elicitation,
    # apply, reconciliation, or the formatting of any refusal or result —
    # becomes the primitives-only state result, and the cleanup below can
    # never replace whatever result was computed.
    try:
        result = _post_mint(state, card, confirm_value, client_ingress,
                            service, channel)
    except Exception as exc:  # noqa: BLE001 - class name only
        result = _state_result(state, type(exc).__name__,
                               "post-mint path raised %s" % type(exc).__name__)
    channel.last_result = result
    # Cleanup never replaces the result: the ONE best-effort primitive,
    # noting a failure into the result's reason (class name only).
    elicitation.best_effort("cleanup release", channel.release,
                            note=lambda text: _annotate(result[0], text))
    return result


def _annotate(structured, text):
    structured["reason"] = "%s%s" % (
        (structured["reason"] + "; ") if structured["reason"] else "", text)


def _post_mint(state, card, confirm_value, client_ingress, service, channel):
    try:
        state.decision_id = service.mint_decision_id(client_ingress)
    except mission_store.MissionStoreError as exc:
        # The store's own typed error is raised only BEFORE it writes
        # (load, bound, validation, directory check): nothing reserved.
        return refusal(str(exc), exc.problem, state.call_ref)
    except Exception as exc:  # the write itself raised
        # Pre- or post-replace is not knowable here and no read API
        # exposes reservations: a reservation may exist without an id.
        return _uncertain(state, (
            "reserving the decision id raised %s; whether a reservation"
            " was recorded is not known to this relay, which holds no id;"
            " nothing was asked and nothing was applied"
            % type(exc).__name__
        ))
    try:
        outcome, detail = channel.elicit(state.decision_id, card,
                                         confirm_value)
    except Exception as exc:  # unexpected on the stream path
        if channel.answer is not None:
            # ADMITTED before the raise: the answer was handed to the
            # channel at admission and is what proceeds to the record,
            # exactly as if the stream path had returned it; the raise
            # (a cleanup, a log, a call boundary) changes nothing.
            outcome, detail = channel.answer
        else:
            state.recorded = False  # the elicitation applies nothing
            return refusal(
                "decision relay raised %s after reserving the decision id"
                % type(exc).__name__, None, state.call_ref,
                elicitation.OUTCOME_STREAM_FAILED, state.decision_id,
                recorded=False,
            )
    if channel.answer is not None:
        outcome, detail = channel.answer
    state.outcome = outcome
    try:
        return _record(state, detail, client_ingress, service)
    except Exception as exc:  # anything before the apply guard
        return _settle(state, exc, service)


def _record(state, detail, client_ingress, service):
    outcome = state.outcome
    call_ref = state.call_ref
    if outcome == elicitation.OUTCOME_BINDING_MISMATCH:
        state.recorded = False
        return refusal(detail or REASON_BINDING_MISMATCH,
                       REASON_BINDING_MISMATCH, call_ref, outcome,
                       state.decision_id, recorded=False)
    if outcome not in (elicitation.OUTCOME_ACCEPT,
                       elicitation.OUTCOME_DECLINE):
        state.recorded = False
        structured = _base(STATUS_NOT_RECORDED, False, REASON_NOT_RECORDED,
                           None, call_ref)
        structured.update({
            "mission_id": state.mission["mission_id"],
            "revision": state.entry["revision"],
            "state": state.mission["state"],
            "decision_id": state.decision_id,
            "proposal_digest_sha256": state.entry["proposal_digest_sha256"],
            "current_revision": state.mission["current_revision"],
            "current_state": state.mission["state"],
            "elicitation_outcome": outcome,
            "decision_recorded": False,
        })
        return structured, True
    received_at = service.now()
    if outcome == elicitation.OUTCOME_ACCEPT:
        proposal = state.entry["proposal"]
        target = proposal["requested_delivery_target"]
        envelope = mission_decision.HumanDecisionEnvelope(
            context=client_ingress, decision_id=state.decision_id,
            mission_id=state.mission_id, revision=state.revision,
            decision=mission_decision.DECISION_APPROVE,
            received_at=received_at,
            approved_action_scope=list(proposal["requested_action_scope"]),
            approved_delivery_targets=[] if target is None else [target],
            expires_at=received_at + CLIENT_CONFIRMED_AUTHORITY_SECONDS,
        )
    else:
        envelope = mission_decision.HumanDecisionEnvelope(
            context=client_ingress, decision_id=state.decision_id,
            mission_id=state.mission_id, revision=state.revision,
            decision=mission_decision.DECISION_DENY, received_at=received_at,
        )
    try:
        applied = service.apply_human_decision(envelope)
    except Exception as exc:  # noqa: BLE001 - EVERY exception, see below
        # The core's own MissionError included: apply_human_decision
        # saves first and evaluates its authorization projection after
        # (mission/service.py, _decision_outcome), so the type of the
        # exception never proves "not recorded". Readback decides.
        return _settle(state, exc, service)
    # COMMIT POINT, recorded FIRST: the core returned, so the decision is
    # durable. Nothing after this line — formatting, projection, cleanup
    # — can lose that fact or the core's own projection.
    state.recorded = True
    state.applied = applied
    state.proof = "the apply's return"
    try:
        return _applied(applied, outcome, call_ref, baseline=state.baseline)
    except Exception as exc:  # noqa: BLE001 - formatting only
        return _settle(state, exc, service)


# -- Task 8, slice S-VI: the client-mediated DELIVERY decision ----------------
#
# ``di_delivery_decide`` rides the SAME admission, claim, reservation,
# elicitation and best-effort cleanup as ``di_mission_decide`` above, with
# a distinct decision kind and reservation: the card is the FULL prepared
# delivery proposal rendered by the injected Mission-control desk
# (``mission_control.delivery.DeliveryDesk``), the confirm value is the
# candidate identity prefix, and the reserved id is a Mission STATE
# OPERATION id (``mo-``) reserved under the client-confirmation context —
# the id the accept's evidence submission consumes. Accept -> the desk
# submits and accepts ``delivery_decision`` evidence; decline -> the desk
# records the S-V cancel request; every other outcome records nothing and
# reports the reservation unconsumed. A Mission approval is never recorded
# here and this relay never calls the Mission decision path.

REASON_DELIVERY_NOT_WIRED = (
    "the delivery desk is not wired on this endpoint (configure both"
    " mission_store_dir and workflow_store_dir)"
)
DELIVERY_ACCEPT = "accept"
DELIVERY_DECLINE = "decline"


def _delivery_base(status, ok, reason, problem, call_ref):
    return {
        "ok": ok, "reason": reason, "status": status, "problem": problem,
        "mission_id": None, "revision": None, "delivery_decision_id": None,
        "proposal_digest_sha256": None, "candidate_identity_digest_sha256": None,
        "decision": None, "evidence_id": None,
        "decision_document_digest_sha256": None, "decision_recorded": None,
        "cancel_requested": None, "elicitation_outcome": None,
        "expires_at": None, "call_ref": call_ref,
    }


def _delivery_refusal(reason, problem, call_ref, **fields):
    structured = _delivery_base(STATUS_REFUSED, False, reason, problem, call_ref)
    structured.update(fields)
    return structured, True


class _DeliveryPostMint(object):
    """State carried through the delivery decision's post-mint path (the
    same single-exit discipline as ``_PostMint``)."""

    def __init__(self, mission_id, revision, binding, call_ref):
        self.decision_id = None
        self.mission_id = mission_id
        self.revision = revision
        self.binding = binding
        self.call_ref = call_ref
        self.outcome = None
        self.recorded = None


def _delivery_fields(state):
    binding = state.binding
    return {
        "mission_id": state.mission_id, "revision": state.revision,
        "delivery_decision_id": state.decision_id,
        "proposal_digest_sha256": binding["proposal_digest_sha256"],
        "candidate_identity_digest_sha256":
            binding["candidate_identity_digest_sha256"],
        "elicitation_outcome": state.outcome,
        "expires_at": binding["expires_at"],
        "decision_recorded": state.recorded,
    }


def _delivery_state_result(state, note):
    """The primitives-only result for ANY raise after the claim: the
    reserved id (if any), the admitted outcome and what is known."""
    structured = _delivery_base(STATUS_UNCERTAIN, False, (
        "%s; delivery decision id %s may or may not be recorded; NOT"
        " re-applied" % (note, state.decision_id if state.decision_id is not None
                         else "(none: the reservation returned no id)")
    ), None, state.call_ref)
    structured.update(_delivery_fields(state))
    return structured, True


def _delivery_relay(arguments, schema_reason, ingress, client_ingress, service,
                    desk, call_ref, channel):
    if schema_reason is not None:
        return _delivery_refusal(schema_reason, None, call_ref)
    if service is None:
        return _delivery_refusal(mission_tools.REASON_NOT_WIRED, None, call_ref)
    if desk is None:
        return _delivery_refusal(REASON_DELIVERY_NOT_WIRED, None, call_ref)
    if not isinstance(ingress, mission_record.AuthenticatedContext) or (
        not isinstance(client_ingress, mission_record.AuthenticatedContext)
    ):
        return _delivery_refusal(mission_tools.REASON_NO_INGRESS, None, call_ref)
    if client_ingress.principal_kind != (
        mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION
    ):
        return _delivery_refusal(mission_tools.REASON_NO_INGRESS, None, call_ref)
    if channel is None or channel.refusal is not None:
        return _delivery_refusal(
            channel.refusal if channel is not None
            else elicitation.REFUSAL_SSE_NOT_ACCEPTED, None, call_ref,
        )
    try:
        return _delivery_decide(arguments, client_ingress, service, desk,
                                call_ref, channel)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        if channel.last_result is not None:
            return channel.last_result
        if channel.state is not None:
            return _delivery_state_result(
                channel.state, "delivery decision relay raised %s"
                % type(exc).__name__)
        if isinstance(exc, (mission_record.MissionError,
                            mission_store.MissionStoreError)):
            return _delivery_refusal(str(exc), exc.problem, call_ref)
        return _delivery_refusal("delivery decision relay raised %s"
                                 % type(exc).__name__, None, call_ref)


def _delivery_decide(arguments, client_ingress, service, desk, call_ref, channel):
    mission_id = arguments["mission_id"]
    revision = arguments["revision"]
    card = desk.card(mission_id, revision)
    if not card["ok"]:
        return _delivery_refusal(card["detail"], card["problem"], call_ref,
                                 mission_id=mission_id, revision=revision)
    if len(card["card"]) > protocol.MAX_ELICITATION_MESSAGE_CHARS:
        return _delivery_refusal(
            "the rendered delivery proposal exceeds %d characters; it is"
            " refused rather than truncated and nothing was reserved"
            % protocol.MAX_ELICITATION_MESSAGE_CHARS, REASON_OVERSIZED, call_ref,
            mission_id=mission_id, revision=revision)
    if not channel.claim():
        return _delivery_refusal(elicitation.REFUSAL_TABLE_FULL, None, call_ref)
    state = _DeliveryPostMint(mission_id, revision, card["binding"], call_ref)
    channel.state = state
    try:
        result = _delivery_post_mint(state, card, client_ingress, service, desk,
                                     channel)
    except Exception as exc:  # noqa: BLE001 - class name only
        result = _delivery_state_result(state, "post-mint path raised %s"
                                        % type(exc).__name__)
    channel.last_result = result
    elicitation.best_effort("cleanup release", channel.release,
                            note=lambda text: _annotate(result[0], text))
    return result


def _delivery_post_mint(state, card, client_ingress, service, desk, channel):
    try:
        state.decision_id = service.mint_state_operation_id(client_ingress)
    except mission_store.MissionStoreError as exc:
        return _delivery_refusal(str(exc), exc.problem, state.call_ref,
                                 mission_id=state.mission_id, revision=state.revision)
    except Exception as exc:  # the write itself raised
        return _delivery_state_result(state, (
            "reserving the delivery decision id raised %s; nothing was asked"
            " and nothing was recorded" % type(exc).__name__))
    try:
        outcome, detail = channel.elicit(state.decision_id, card["card"],
                                         card["confirm_value"])
    except Exception as exc:  # unexpected on the stream path
        if channel.answer is None:
            state.recorded = False
            state.outcome = elicitation.OUTCOME_STREAM_FAILED
            structured, is_error = _delivery_refusal(
                "delivery decision relay raised %s after reserving the decision"
                " id" % type(exc).__name__, None, state.call_ref)
            structured.update(_delivery_fields(state))
            return structured, is_error
        outcome, detail = channel.answer
    if channel.answer is not None:
        outcome, detail = channel.answer
    state.outcome = outcome
    if outcome == elicitation.OUTCOME_BINDING_MISMATCH or outcome not in (
        elicitation.OUTCOME_ACCEPT, elicitation.OUTCOME_DECLINE
    ):
        state.recorded = False
        structured = _delivery_base(
            STATUS_REFUSED if outcome == elicitation.OUTCOME_BINDING_MISMATCH
            else STATUS_NOT_RECORDED, False,
            detail if outcome == elicitation.OUTCOME_BINDING_MISMATCH and detail
            else REASON_NOT_RECORDED,
            REASON_BINDING_MISMATCH if outcome == elicitation.OUTCOME_BINDING_MISMATCH
            else None, state.call_ref)
        structured.update(_delivery_fields(state))
        return structured, True
    accept = outcome == elicitation.OUTCOME_ACCEPT
    record = desk.accept if accept else desk.decline
    recorded = record(state.binding, state.decision_id, client_ingress)
    state.recorded = recorded["recorded"]
    if recorded["ok"]:
        structured = _delivery_base(STATUS_APPLIED, True, recorded["detail"], None,
                                    state.call_ref)
    else:
        structured = _delivery_base(
            STATUS_REFUSED if recorded["recorded"] is not None else STATUS_UNCERTAIN,
            False, recorded["detail"], recorded["problem"], state.call_ref)
    structured.update(_delivery_fields(state))
    structured.update({
        "decision": DELIVERY_ACCEPT if accept else DELIVERY_DECLINE,
        "evidence_id": recorded["evidence_id"],
        "decision_document_digest_sha256":
            recorded["decision_document_digest_sha256"],
        "cancel_requested": recorded["cancel_requested"] if not accept else False,
    })
    return structured, not recorded["ok"]


def relay_delivery_status(name, arguments, schema_reason, ingress, desk, call_ref):
    """``di_delivery_status``: the desk's PURE status read; returns
    ``(structured, is_error)``."""
    structured = {
        "ok": False, "reason": None, "status": STATUS_REFUSED, "problem": None,
        "mission_id": None, "revision": None, "mission_state": None,
        "progress": None, "delivery_requested": None, "cancel_requested": None,
        "proposal": None, "preparation": None, "decision": None, "delivery": None,
        "uncertainty": [],
        "next_action": None, "call_ref": call_ref,
    }
    if name != protocol.TOOL_DELIVERY_STATUS:
        structured["reason"] = "unknown delivery tool"
        return structured, True
    if schema_reason is not None:
        structured["reason"] = schema_reason
        return structured, True
    if desk is None:
        structured["reason"] = REASON_DELIVERY_NOT_WIRED
        return structured, True
    if not isinstance(ingress, mission_record.AuthenticatedContext):
        structured["reason"] = mission_tools.REASON_NO_INGRESS
        return structured, True
    try:
        status = desk.status(arguments["mission_id"])
    except (mission_record.MissionError, mission_store.MissionStoreError) as exc:
        structured.update(reason=str(exc), problem=exc.problem)
        return structured, True
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        structured["reason"] = "delivery status raised %s" % type(exc).__name__
        return structured, True
    structured.update(status)
    structured.update(ok=True, status="read", call_ref=call_ref)
    return structured, False
