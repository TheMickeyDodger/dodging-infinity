"""The Mission status, control and attention relays (Task 8, slice S-VII).

Four tools, relayed into INJECTED desks the production composition builds
(``grok_mcp.cli``); none of them is imported here, so this surface never
loads a store, engine or coordination module of its own:

- ``di_mission_status`` → the status reader ``(mission_id) -> dict``: a
  PURE read; nothing is written, reserved or waited for.
- ``di_attention_pull`` → ``attention_desk.pull(call_ref)``: surfaces the
  pending attention records into THIS result (the one write: each record's
  surfaced state, recorded by coordination under Task 6's rules).
- ``di_mission_control`` and ``di_attention_ack`` → a desk exposing
  ``card`` / ``reserve`` / ``apply``, through the SAME client-mediated
  discipline as the Mission decision (slice S-I): every precheck before
  anything is reserved; the pending-table claim before the durable
  reservation; ONE elicitation whose answer is the human's form answer —
  never a tool argument; the admitted answer is authoritative; only an
  admitted ACCEPT applies anything; a decline, a cancel of the form or any
  other answer applies nothing; one exit with cleanup through
  ``best_effort``. The elicitation request id is minted per elicitation
  here (``mo-``/``ak-`` shaped), distinct from the desk's durable
  reservation, so a reserved id reused by the core (a Mission's derived
  cancel id) can never collide with an earlier answered form.
"""

import secrets

from mission import record as mission_record
from mission import store as mission_store

from grok_mcp import decision_tools
from grok_mcp import elicitation
from grok_mcp import mission_tools
from grok_mcp import protocol

REASON_STATUS_NOT_WIRED = "mission status is not wired on this endpoint"
REASON_CONTROL_NOT_WIRED = "mission controls are not wired on this endpoint"
REASON_ATTENTION_NOT_WIRED = "attention is not wired on this endpoint"
REASON_NOT_APPLIED = "nothing was applied; the reserved id stays unconsumed"
STATUS_READ = "read"
STATUS_PULLED = "pulled"
# The status reader's view keys copied into ``di_mission_status``.
STATUS_VIEW_KEYS = ("canonical", "stores", "reconciliation", "workflows",
                    "attention", "limitations")
STATUS_DECLINED = "declined"
ELICITATION_PREFIX_CONTROL = "mo-"
ELICITATION_PREFIX_ACK = "ak-"


def _problem_of(exc):
    problem = getattr(exc, "problem", None)
    return problem if isinstance(problem, str) else None


def _ingress_ok(ingress):
    return isinstance(ingress, mission_record.AuthenticatedContext)


# -- di_mission_status -----------------------------------------------------------


def relay_status(name, arguments, schema_reason, ingress, reader, call_ref):
    """``di_mission_status``: the injected reader's PURE status."""
    structured = {
        "ok": False, "reason": None, "status": protocol.STATUS_REFUSED,
        "problem": None, "mission_id": None, "canonical": None, "stores": None,
        "reconciliation": None, "workflows": None, "attention": None,
        "limitations": [], "call_ref": call_ref,
    }
    if name != protocol.TOOL_MISSION_STATUS:
        structured["reason"] = "unknown status tool"
        return structured, True
    if schema_reason is not None:
        structured["reason"] = schema_reason
        return structured, True
    if reader is None:
        structured["reason"] = REASON_STATUS_NOT_WIRED
        return structured, True
    if not _ingress_ok(ingress):
        structured["reason"] = mission_tools.REASON_NO_INGRESS
        return structured, True
    mission_id = arguments["mission_id"]
    structured["mission_id"] = mission_id
    try:
        status = reader(mission_id)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        structured.update(reason="mission status raised %s" % type(exc).__name__,
                          problem=_problem_of(exc))
        return structured, True
    for key in STATUS_VIEW_KEYS:
        structured[key] = status.get(key)
    structured["limitations"] = list(structured["limitations"] or [])
    structured.update(ok=True, status=STATUS_READ)
    return structured, False


# -- di_attention_pull -----------------------------------------------------------


def relay_attention_pull(name, arguments, schema_reason, ingress, desk, call_ref):
    structured = {
        "ok": False, "reason": None, "status": protocol.STATUS_REFUSED,
        "problem": None, "surfaced_now": [], "surfaced": [], "acknowledged": [],
        "pending": [], "not_surfaced": [], "call_ref": call_ref,
    }
    if name != protocol.TOOL_ATTENTION_PULL:
        structured["reason"] = "unknown attention tool"
        return structured, True
    if schema_reason is not None:
        structured["reason"] = schema_reason
        return structured, True
    if desk is None:
        structured["reason"] = REASON_ATTENTION_NOT_WIRED
        return structured, True
    if not _ingress_ok(ingress):
        structured["reason"] = mission_tools.REASON_NO_INGRESS
        return structured, True
    try:
        pulled = desk.pull(call_ref)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        structured.update(reason="attention pull raised %s" % type(exc).__name__,
                          problem=_problem_of(exc))
        return structured, True
    structured.update(pulled)
    structured.update(ok=True, status=STATUS_PULLED, call_ref=call_ref)
    return structured, False


# -- the elicited desks: di_mission_control, di_attention_ack -----------------------


class _Elicited(object):
    """The ONE state object the post-claim path carries."""

    def __init__(self, name, call_ref, binding):
        self.name = name
        self.call_ref = call_ref
        self.binding = binding
        self.reserved_id = None
        self.request_id = None
        self.outcome = None
        self.recorded = None


def _skeleton(name, call_ref):
    if name == protocol.TOOL_MISSION_CONTROL:
        return {
            "ok": False, "reason": None, "status": protocol.STATUS_REFUSED,
            "problem": None, "mission_id": None, "revision": None,
            "control": None, "operation": None, "operation_id": None,
            "elicitation_outcome": None, "control_recorded": None,
            "controls": None, "call_ref": call_ref,
        }
    return {
        "ok": False, "reason": None, "status": protocol.STATUS_REFUSED,
        "problem": None, "attention_id": None, "mission_id": None,
        "request_id": None, "elicitation_outcome": None, "acknowledged": None,
        "attention": None, "call_ref": call_ref,
    }


def _refused(name, call_ref, reason, problem=None, **fields):
    structured = _skeleton(name, call_ref)
    structured.update(reason=reason, problem=problem)
    structured.update(fields)
    return structured, True


def _fields(state):
    """What the state knows, in the tool's own output fields."""
    binding = state.binding or {}
    if state.name == protocol.TOOL_MISSION_CONTROL:
        return {"mission_id": binding.get("mission_id"),
                "revision": binding.get("revision"),
                "control": binding.get("control"),
                "operation": binding.get("operation"),
                "operation_id": state.reserved_id,
                "elicitation_outcome": state.outcome,
                "control_recorded": state.recorded}
    return {"attention_id": binding.get("attention_id"),
            "mission_id": binding.get("mission_id"),
            "request_id": state.request_id,
            "elicitation_outcome": state.outcome,
            "acknowledged": state.recorded}


def _uncertain(state, note):
    structured = _skeleton(state.name, state.call_ref)
    structured.update(status=decision_tools.STATUS_UNCERTAIN, reason=(
        "%s; the reserved id %s may or may not be applied; NOT re-applied"
        % (note, state.reserved_id or "(none)")))
    structured.update(_fields(state))
    return structured, True


def _card(name, arguments, desk):
    if name == protocol.TOOL_MISSION_CONTROL:
        return desk.card(arguments["mission_id"], arguments["revision"],
                         arguments["control"])
    return desk.card(arguments["attention_id"])


def relay_elicited(name, arguments, schema_reason, ingress, client_ingress, desk,
                   call_ref, channel):
    """``di_mission_control`` / ``di_attention_ack``: returns
    ``(structured, is_error)``."""
    if name not in (protocol.TOOL_MISSION_CONTROL, protocol.TOOL_ATTENTION_ACK):
        return _refused(protocol.TOOL_ATTENTION_ACK, call_ref, "unknown elicited tool")
    if schema_reason is not None:
        return _refused(name, call_ref, schema_reason)
    if desk is None:
        return _refused(name, call_ref, REASON_CONTROL_NOT_WIRED
                        if name == protocol.TOOL_MISSION_CONTROL
                        else REASON_ATTENTION_NOT_WIRED)
    if not _ingress_ok(ingress) or not _ingress_ok(client_ingress) or (
        client_ingress.principal_kind
        != mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION
    ):
        return _refused(name, call_ref, mission_tools.REASON_NO_INGRESS)
    if channel is None or channel.refusal is not None:
        return _refused(name, call_ref, channel.refusal if channel is not None
                        else elicitation.REFUSAL_SSE_NOT_ACCEPTED)
    try:
        return _elicited(name, arguments, client_ingress, desk, call_ref, channel)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        if channel.last_result is not None:
            return channel.last_result
        if channel.state is not None:
            return _uncertain(channel.state, "relay raised %s" % type(exc).__name__)
        return _refused(name, call_ref, "relay raised %s" % type(exc).__name__,
                        _problem_of(exc))


def _elicited(name, arguments, client_ingress, desk, call_ref, channel):
    card = _card(name, arguments, desk)
    if not card["ok"]:
        return _refused(name, call_ref, card["detail"], card["problem"])
    if len(card["card"]) > protocol.MAX_ELICITATION_MESSAGE_CHARS:
        return _refused(name, call_ref, (
            "the rendered card exceeds %d characters; it is refused rather than"
            " truncated and nothing was reserved"
            % protocol.MAX_ELICITATION_MESSAGE_CHARS),
            decision_tools.REASON_OVERSIZED)
    if not channel.claim():
        return _refused(name, call_ref, elicitation.REFUSAL_TABLE_FULL)
    state = _Elicited(name, call_ref, card["binding"])
    channel.state = state
    try:
        result = _post_claim(state, card, client_ingress, desk, channel)
    except Exception as exc:  # noqa: BLE001 - class name only
        result = _uncertain(state, "post-claim path raised %s" % type(exc).__name__)
    channel.last_result = result
    elicitation.best_effort("cleanup release", channel.release,
                            note=lambda text: decision_tools._annotate(result[0], text))
    return result


def _post_claim(state, card, client_ingress, desk, channel):
    try:
        state.reserved_id = desk.reserve(state.binding, client_ingress)
    except (mission_record.MissionError, mission_store.MissionStoreError) as exc:
        # The core's typed refusals are raised BEFORE it writes: nothing
        # was reserved and nothing is asked.
        structured, is_error = _refused(state.name, state.call_ref, str(exc),
                                        exc.problem)
        structured.update(_fields(state))
        return structured, is_error
    prefix = (ELICITATION_PREFIX_CONTROL if state.name == protocol.TOOL_MISSION_CONTROL
              else ELICITATION_PREFIX_ACK)
    state.request_id = (state.reserved_id
                        if state.name == protocol.TOOL_ATTENTION_ACK
                        else prefix + secrets.token_hex(16))
    try:
        outcome, detail = channel.elicit(state.request_id, card["card"],
                                         card["confirm_value"])
    except Exception as exc:  # unexpected on the stream path
        if channel.answer is None:
            state.recorded = False
            state.outcome = elicitation.OUTCOME_STREAM_FAILED
            structured, is_error = _refused(
                state.name, state.call_ref,
                "relay raised %s after the reservation; %s"
                % (type(exc).__name__, REASON_NOT_APPLIED))
            structured.update(_fields(state))
            return structured, is_error
        outcome, detail = channel.answer
    if channel.answer is not None:
        outcome, detail = channel.answer
    state.outcome = outcome
    if outcome != elicitation.OUTCOME_ACCEPT:
        state.recorded = False
        mismatch = outcome == elicitation.OUTCOME_BINDING_MISMATCH
        structured = _skeleton(state.name, state.call_ref)
        structured.update(
            status=(protocol.STATUS_REFUSED if mismatch
                    else STATUS_DECLINED if outcome == elicitation.OUTCOME_DECLINE
                    else decision_tools.STATUS_NOT_RECORDED),
            reason=detail if mismatch and detail else REASON_NOT_APPLIED,
            problem=decision_tools.REASON_BINDING_MISMATCH if mismatch else None)
        structured.update(_fields(state))
        return structured, True
    applied = desk.apply(state.binding, state.reserved_id, client_ingress)
    state.recorded = applied["recorded"]
    structured = _skeleton(state.name, state.call_ref)
    structured.update(_fields(state))
    structured.update(
        ok=applied["ok"],
        status=(decision_tools.STATUS_APPLIED if applied["ok"]
                else protocol.STATUS_REFUSED if applied["recorded"] is False
                else decision_tools.STATUS_UNCERTAIN),
        reason=applied["detail"], problem=applied["problem"])
    if state.name == protocol.TOOL_MISSION_CONTROL:
        structured["controls"] = applied.get("controls")
    else:
        structured["attention"] = applied.get("attention")
    return structured, not applied["ok"]
