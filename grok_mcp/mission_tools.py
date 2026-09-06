"""Marshalling for the five Mission tools: arguments in, neutral Mission
Core calls, structured results out. Nothing here owns Mission state or
a Mission rule.

Every Mission rule — identity, revisions, stale-revision protection,
decision idempotency, scope, authorization, ledger, validation — lives
in the neutral ``mission`` package. This module only:

1. requires the per-request authenticated ingress the SERVER built after
   its bearer check (an ``AuthenticatedContext``), refusing with an
   observable reason when it is absent or of the wrong type, so a
   direct controller call mints nothing;
2. requires an injected Mission service, refusing observably when none
   is wired, so current deployments without one keep working unchanged;
3. mints the DI-owned request / decision id through the service (or
   accepts back a request id the service itself issued), builds the
   neutral envelope, calls the service, and packages the outcome into
   the tool's closed output shape.

Refusals from the core are returned as tool errors carrying the core's
distinct ``mission_*`` problem code; no message text from an unexpected
exception is ever copied out — class name only, like the operator turn.

Truthfulness. The ingress context names the transport and the ordinal
of the configured connector credential the server verified. It is not
proof of a human identity, and these structural boundaries are backed
by behavioral tests and call-site pins rather than any claim that an
in-process caller could not construct such an object.
"""

from mission import decision as mission_decision
from mission import record as mission_record
from mission import store as mission_store

from grok_mcp import protocol

STATUS_REFUSED = protocol.STATUS_REFUSED
STATUS_APPLIED = "applied"
STATUS_READ = "read"

REASON_NOT_WIRED = "mission service not wired on this endpoint"
REASON_NO_INGRESS = (
    "no authenticated ingress for this call; Mission tools accept only"
    " requests the server authenticated"
)


def _base(status, ok, reason, problem, call_ref):
    return {
        "ok": ok, "reason": reason, "status": status, "problem": problem,
        "mission_id": None, "revision": None, "state": None,
        "call_ref": call_ref,
    }


def _shape(name, structured):
    """Fill the tool-specific nullable fields a refusal leaves empty."""
    if name == protocol.TOOL_MISSION_PROPOSE:
        structured.setdefault("request_id", None)
        structured.setdefault("idempotent", False)
        structured.setdefault("proposal", None)
        structured.setdefault("proposal_digest_sha256", None)
    elif name == protocol.TOOL_MISSION_GET:
        structured.setdefault("proposal", None)
        structured.setdefault("proposal_digest_sha256", None)
        structured.setdefault("revision_count", 0)
        structured.setdefault("decision_count", 0)
        structured.setdefault("active_authorization_id", None)
    else:
        structured.setdefault("decision_id", None)
        structured.setdefault("proposal_digest_sha256", None)
        structured.setdefault("idempotent", False)
        structured.setdefault("current_revision", None)
        structured.setdefault("current_state", None)
        if name == protocol.TOOL_MISSION_EDIT:
            structured.setdefault("invalidated_authorization_ids", [])
        elif name == protocol.TOOL_MISSION_APPROVE:
            structured.setdefault("authorization_id", None)
            structured.setdefault("authorization_digest_sha256", None)
            structured.setdefault("authorized_action_scope", None)
            structured.setdefault("authorized_delivery_targets", None)
            structured.setdefault("authorization_live", None)
            structured.setdefault("authorization_problem", None)
    return structured


def refusal(name, reason, problem, call_ref):
    return _shape(name, _base(STATUS_REFUSED, False, reason, problem, call_ref)), True


def _proposal_from(arguments):
    return dict((key, arguments[key]) for key in protocol.PROPOSAL_INPUT_NAMES)


def relay(name, arguments, schema_reason, ingress, service, call_ref):
    """Execute one Mission tool; returns ``(structured, is_error)``."""
    if schema_reason is not None:
        return refusal(name, schema_reason, None, call_ref)
    if service is None:
        return refusal(name, REASON_NOT_WIRED, None, call_ref)
    if not isinstance(ingress, mission_record.AuthenticatedContext):
        return refusal(name, REASON_NO_INGRESS, None, call_ref)
    try:
        if name == protocol.TOOL_MISSION_PROPOSE:
            return _propose(arguments, ingress, service, call_ref)
        if name == protocol.TOOL_MISSION_GET:
            return _get(arguments, service, call_ref)
        if name == protocol.TOOL_MISSION_EDIT:
            return _edit(arguments, ingress, service, call_ref)
        return _decide(name, arguments, ingress, service, call_ref)
    except mission_record.MissionError as exc:
        return refusal(name, str(exc), exc.problem, call_ref)
    except mission_store.MissionStoreError as exc:
        return refusal(name, str(exc), exc.problem, call_ref)
    except Exception as exc:  # reported by class name only
        return refusal(name, "mission core raised %s" % type(exc).__name__,
                       None, call_ref)


def _propose(arguments, ingress, service, call_ref):
    request_id = arguments.get("request_id")
    if request_id is None:
        request_id = service.mint_request_id(ingress)
    outcome = service.propose(request_id, _proposal_from(arguments), ingress)
    structured = _base(STATUS_APPLIED, True, None, None, call_ref)
    structured.update({
        "mission_id": outcome["mission_id"],
        "revision": outcome["revision"],
        "state": outcome["state"],
        "request_id": outcome["request_id"],
        "idempotent": outcome["idempotent"],
        # The exact canonical proposal of the reported revision, with its
        # digest: one coherent triple from the same stored revision.
        "proposal": dict(outcome["proposal"]),
        "proposal_digest_sha256": outcome["proposal_digest_sha256"],
    })
    return structured, False


def _get(arguments, service, call_ref):
    stored = service.get(arguments["mission_id"])
    mission = stored["record"]
    current = mission["revisions"][-1]
    structured = _base(STATUS_READ, True, None, None, call_ref)
    structured.update({
        "mission_id": mission["mission_id"],
        "revision": mission["current_revision"],
        "state": mission["state"],
        "proposal": dict(current["proposal"]),
        "proposal_digest_sha256": current["proposal_digest_sha256"],
        "revision_count": len(mission["revisions"]),
        "decision_count": len(mission["decisions"]),
        # Only what the ONE central validator accepts right now.
        "active_authorization_id": stored["live_authorization_id"],
    })
    return structured, False


def _decision_result(name, outcome, call_ref):
    structured = _base(STATUS_APPLIED, True, None, None, call_ref)
    structured.update({
        "mission_id": outcome["mission_id"],
        "revision": outcome["revision"],
        "state": outcome["state"],
        "decision_id": outcome["decision_id"],
        "proposal_digest_sha256": outcome["proposal_digest_sha256"],
        "idempotent": outcome["idempotent"],
        "current_revision": outcome["current_revision"],
        "current_state": outcome["current_state"],
    })
    if name == protocol.TOOL_MISSION_EDIT:
        structured["invalidated_authorization_ids"] = list(
            outcome["invalidated_authorization_ids"]
        )
    elif name == protocol.TOOL_MISSION_APPROVE:
        structured.update({
            "authorization_id": outcome["authorization_id"],
            "authorization_digest_sha256": outcome["authorization_digest_sha256"],
            "authorized_action_scope": outcome["authorized_action_scope"],
            "authorized_delivery_targets": outcome["authorized_delivery_targets"],
            "authorization_live": outcome["authorization_live"],
            "authorization_problem": outcome["authorization_problem"],
        })
    return structured, False


def _edit(arguments, ingress, service, call_ref):
    decision_id = service.mint_decision_id(ingress)
    outcome = service.edit(
        arguments["mission_id"], arguments["expected_revision"],
        _proposal_from(arguments), decision_id, ingress,
    )
    return _decision_result(protocol.TOOL_MISSION_EDIT, outcome, call_ref)


def _decide(name, arguments, ingress, service, call_ref):
    """APPROVE exactly the requested scope of the revision passed, or DENY."""
    mission_id = arguments["mission_id"]
    revision = arguments["revision"]
    if name == protocol.TOOL_MISSION_APPROVE:
        stored = service.get(mission_id)
        current = stored["record"]["revisions"][-1]["proposal"]
        # Approval through this surface is exact: the requested scope of
        # the CURRENT revision. A stale ``revision`` is refused by the core.
        target = current["requested_delivery_target"]
        decision = mission_decision.DECISION_APPROVE
        actions = list(current["requested_action_scope"])
        targets = [] if target is None else [target]
    else:
        decision = mission_decision.DECISION_DENY
        actions = None
        targets = None
    decision_id = service.mint_decision_id(ingress)
    envelope = mission_decision.HumanDecisionEnvelope(
        context=ingress, decision_id=decision_id, mission_id=mission_id,
        revision=revision, decision=decision, received_at=service.now(),
        approved_action_scope=actions, approved_delivery_targets=targets,
    )
    outcome = service.apply_human_decision(envelope)
    return _decision_result(name, outcome, call_ref)
