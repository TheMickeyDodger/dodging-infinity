"""Marshalling for the engineering engagement tool ``di_mission_dispatch``
(Task 8, slice S-IV).

The tool is relayed into an INJECTED bootstrap callable (the Mission
control layer's ``MissionControl.dispatch``, wired by the CLI when both
the Mission store and the workflow store are configured). This module
decides nothing about authority: it validates the call shape, requires
the server-built authenticated ingress, forwards the Mission id and that
ingress, and reports the bootstrap's closed result. When the bootstrap is
not wired, or the ingress is absent, it refuses with an observable
reason and nothing else changes. Every exception is reported by class
name only.
"""

from mission import record as mission_record

from grok_mcp import protocol

STATUS_REFUSED = "refused"
STATUS_STARTED = "started"
STATUS_IDEMPOTENT = "idempotent"

REASON_NOT_WIRED = (
    "the engineering engagement is not wired on this endpoint (configure"
    " both mission_store_dir and workflow_store_dir)"
)
REASON_NO_INGRESS = (
    "no authenticated ingress for this call; the engagement tool accepts"
    " only requests the server authenticated"
)


def _base(status, ok, reason, problem, call_ref):
    return {
        "ok": ok, "reason": reason, "status": status, "problem": problem,
        "mission_id": None, "revision": None, "workflow_id": None,
        "engagement_id": None, "idempotent": False, "missing_guards": [],
        "call_ref": call_ref,
    }


def refusal(reason, problem, call_ref, mission_id=None, missing_guards=()):
    structured = _base(STATUS_REFUSED, False, reason, problem, call_ref)
    structured["mission_id"] = mission_id
    structured["missing_guards"] = list(missing_guards)
    return structured, True


def relay(name, arguments, schema_reason, ingress, bootstrap, call_ref):
    """Execute the engagement tool; returns ``(structured, is_error)``."""
    if name != protocol.TOOL_MISSION_DISPATCH:
        return refusal("unknown engagement tool", None, call_ref)
    if schema_reason is not None:
        return refusal(schema_reason, None, call_ref)
    if bootstrap is None:
        return refusal(REASON_NOT_WIRED, None, call_ref)
    if not isinstance(ingress, mission_record.AuthenticatedContext):
        return refusal(REASON_NO_INGRESS, None, call_ref)
    mission_id = arguments["mission_id"]
    try:
        result = bootstrap(mission_id, ingress)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        return refusal("engagement bootstrap raised %s" % type(exc).__name__,
                       None, call_ref, mission_id=mission_id)
    if not result["ok"]:
        structured, is_error = refusal(
            result["detail"], result["problem"], call_ref,
            mission_id=result["mission_id"] or mission_id,
            missing_guards=result["missing_guards"])
        structured["revision"] = result["revision"]
        structured["workflow_id"] = result["workflow_id"]
        structured["engagement_id"] = result["engagement_id"]
        return structured, is_error
    status = STATUS_IDEMPOTENT if result["idempotent"] else STATUS_STARTED
    structured = _base(status, True, None, None, call_ref)
    structured.update({
        "mission_id": result["mission_id"],
        "revision": result["revision"],
        "workflow_id": result["workflow_id"],
        "engagement_id": result["engagement_id"],
        "idempotent": result["idempotent"],
    })
    return structured, False
