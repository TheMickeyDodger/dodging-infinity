"""Attention for the client surface: projection, pull and acknowledgment
(Task 8, slice S-VII).

This module PRESERVES the Task 6 attention semantics exactly: it composes
coordination's ``CoordinationService`` over the production Mission
observation bridge (``observation_adapter.MissionSnapshotSource``) for ONE
stable destination, ``CLIENT_DESTINATION`` — the configured connector
credential's ordinal is the only stable identity Dodging Infinity holds for
this surface; a per-call id is never a destination. No new attention state,
record or rule exists here.

- ``project(mission_id)`` is a WRITE the Runtime pass performs for each live
  Mission-origin workflow's Mission, so records exist before any client
  call. Only a FRESH observation changes anything (coordination's rule).
- ``pull(call_ref)`` backs the explicit ``di_attention_pull`` tool: each
  PENDING record is surfaced through ``ToolResultPresenter`` — the
  presentation goes into THIS tool result and the receipt's message
  reference is the call's own DI-minted reference, so SURFACED truthfully
  means "included in tool result <call_ref>; client receipt unconfirmed".
  A presenter refusal leaves the record PENDING with no surfaced time (Task
  6); a transport failure AFTER surfacing leaves it SURFACED, and the next
  pull lists it under ``surfaced`` — never re-created, never reverted.
- ``card`` / ``reserve`` / ``apply`` back ``di_attention_ack``: an
  acknowledgment is recorded ONLY as the human's answer to this client's
  elicitation form, with the client-confirmation context mapped to
  coordination's own context type. Acknowledging authorizes and resolves
  nothing.
"""

import secrets

from coordination import record as coordination_record
from coordination import service as coordination_service
from coordination import store as coordination_store
from coordination.attention import AttentionPresenter, PresentationReceipt
from mission_control import observation_adapter
from workflow_authority.digest import text_digest

CLIENT_DESTINATION = {"transport": "grok_mcp",
                      "conversation_ref": "connector-credential-1"}
ACK_REQUEST_PREFIX = "ak-"
PROBLEM_ATTENTION_UNKNOWN = "attention_unknown"
PROBLEM_ATTENTION_CLOSED = "attention_closed"
PROBLEM_ATTENTION_ACKNOWLEDGED = "attention_already_acknowledged"
CLIENT_RECEIPT_UNCONFIRMED = "unconfirmed"


def view(value):
    """The bounded client view of one attention record."""
    surfaced = value["presentation"] in (
        coordination_record.PRESENTATION_SURFACED,
        coordination_record.PRESENTATION_ACKNOWLEDGED)
    return {
        "attention_id": value["attention_id"],
        "mission_id": value["mission_id"],
        "revision": value["revision"],
        "condition_kind": value["condition_kind"],
        "condition_key": value["condition_key"],
        "priority": value["priority"],
        "presentation": value["presentation"],
        "created_at": value["created_at"],
        "surfaced_at": value["surfaced_at"],
        "surfaced_in": value["surfaced_message_ref"],
        "client_receipt": CLIENT_RECEIPT_UNCONFIRMED if surfaced and (
            value["presentation"] == coordination_record.PRESENTATION_SURFACED
        ) else None,
        "acknowledged_at": value["acknowledged_at"],
    }


class ToolResultPresenter(AttentionPresenter):
    """Presents a record by including it in the current tool result: the
    receipt's message reference is that call's DI-minted reference."""

    def __init__(self, call_ref):
        self.call_ref = call_ref
        self.presented = []

    def present(self, destination, presentation):
        self.presented.append(presentation["attention_id"])
        return PresentationReceipt(True, self.call_ref, None)


def coordination_context(context):
    """The caller's authenticated context in coordination's own type."""
    return coordination_record.AuthenticatedContext(
        transport=context.transport, principal_kind=context.principal_kind,
        principal_ref=context.principal_ref,
        configured_subject=context.configured_subject).validate()


class AttentionDesk(object):
    """Projection (the Runtime's write), pull and acknowledgment for the
    client destination. ``presenter_factory(call_ref)`` builds the pull's
    presenter (``ToolResultPresenter`` in production)."""

    def __init__(self, service, coordination_directory, clock=None, mint_id=None,
                 presenter_factory=None):
        self._coordination = coordination_service.CoordinationService(
            coordination_store.CoordinationStore(coordination_directory),
            clock or service.now,
            observation_adapter.MissionSnapshotSource(service),
            frozenset(), mint_id=mint_id)
        self._presenter_factory = presenter_factory or ToolResultPresenter

    @property
    def coordination(self):
        return self._coordination

    def project(self, mission_id):
        """The Runtime's projection WRITE for one Mission."""
        outcome = self._coordination.project_attention(mission_id, CLIENT_DESTINATION)
        return {"freshness": outcome.freshness, "problem": outcome.problem,
                "created": list(outcome.created), "obsoleted": list(outcome.obsoleted),
                "resolved": list(outcome.resolved)}

    def pull(self, call_ref):
        """Surface every PENDING record into this call's result; list what
        was surfaced now, what was surfaced earlier (client receipt still
        unconfirmed), what is acknowledged, and what stays pending."""
        presenter = self._presenter_factory(call_ref)
        not_surfaced = []
        for value in self._coordination.pending_attention(CLIENT_DESTINATION):
            if value["presentation"] != coordination_record.PRESENTATION_PENDING:
                continue
            try:
                outcome = self._coordination.surface_attention(value["attention_id"],
                                                               presenter)
            except coordination_record.CoordinationError as exc:
                not_surfaced.append({"attention_id": value["attention_id"],
                                     "problem": exc.problem})
                continue
            if not outcome.surfaced:
                not_surfaced.append({"attention_id": value["attention_id"],
                                     "problem": outcome.problem})
        listed = self._coordination.pending_attention(CLIENT_DESTINATION)
        now = [view(v) for v in listed
               if v["presentation"] == coordination_record.PRESENTATION_SURFACED
               and v["surfaced_message_ref"] == call_ref]
        return {
            "surfaced_now": now,
            "surfaced": [view(v) for v in listed
                         if v["presentation"] == coordination_record.PRESENTATION_SURFACED
                         and v["surfaced_message_ref"] != call_ref],
            "acknowledged": [view(v) for v in listed if v["presentation"]
                             == coordination_record.PRESENTATION_ACKNOWLEDGED],
            "pending": [view(v) for v in listed if v["presentation"]
                        == coordination_record.PRESENTATION_PENDING],
            "not_surfaced": not_surfaced,
        }

    def _record(self, attention_id):
        for value in self._coordination.pending_attention(CLIENT_DESTINATION):
            if value["attention_id"] == attention_id:
                return value
        return None

    def card(self, attention_id):
        """PURE read: the acknowledgment card of one live record."""
        value = self._record(attention_id)
        if value is None:
            return {"ok": False, "problem": PROBLEM_ATTENTION_UNKNOWN,
                    "detail": "no live attention record %s for this client"
                    % attention_id, "card": None, "confirm_value": None,
                    "binding": None}
        if value["presentation"] == coordination_record.PRESENTATION_ACKNOWLEDGED:
            return {"ok": False, "problem": PROBLEM_ATTENTION_ACKNOWLEDGED,
                    "detail": "attention %s is already acknowledged" % attention_id,
                    "card": None, "confirm_value": None, "binding": None}
        confirm_value = text_digest("acknowledge|" + attention_id)[:12]
        card = "\n".join([
            "Dodging Infinity attention — acknowledge exactly this:",
            "",
            "ATTENTION %s" % attention_id,
            "MISSION %s REVISION %d" % (value["mission_id"], value["revision"]),
            "CONDITION %s %s" % (value["condition_kind"], value["condition_key"]),
            "",
            "Acknowledging records that you saw it. It authorizes, resolves and"
            " starts nothing.",
            "",
            "To confirm, answer with the value %s." % confirm_value,
        ])
        return {"ok": True, "problem": None, "detail": None, "card": card,
                "confirm_value": confirm_value,
                "binding": {"attention_id": attention_id,
                            "mission_id": value["mission_id"],
                            "revision": value["revision"]}}

    def reserve(self, binding, context):
        """The elicitation request id (in-memory; the acknowledgment itself
        is idempotent by refusal)."""
        return ACK_REQUEST_PREFIX + secrets.token_hex(16)

    def apply(self, binding, request_id, context):
        """Record the human's acknowledgment: ``{"ok", "recorded", "problem",
        "detail", "attention"}``."""
        try:
            acked = self._coordination.acknowledge_attention(
                binding["attention_id"], coordination_context(context))
        except coordination_record.CoordinationError as exc:
            return {"ok": False, "recorded": False, "problem": exc.problem,
                    "detail": str(exc), "attention": None}
        return {"ok": True, "recorded": True, "problem": None,
                "detail": "acknowledged; nothing was authorized or resolved",
                "attention": view(acked)}
