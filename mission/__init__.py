"""Dodging Infinity Mission Core: proposal and authorization, neutral.

This package owns the canonical Mission: a DI-owned stable Mission
identity, exact revisioned proposals, lifecycle state, transport-neutral
human APPROVE / EDIT / DENY decisions with truthful provenance, exact
Mission Authorization, a durable append-only Authority Ledger, and ONE
centralized fail-closed validation path. It is a data-authority layer
and nothing more: it routes nothing, dispatches nothing, creates no
workspace, runs no capability, performs no Git or PR action, and exposes
no merge, release, deploy, or publish path. Approval changes a Mission's
state to AUTHORIZED; it never starts anything.

Dependency direction. This package imports the standard library and
exactly three stdlib-only helpers from ``workflow_authority``
(``atomic``, ``digest``, ``canonical``). It imports no transport, no
model provider, no operator session, no orchestration engine, no
delivery package, and no execution seam. Adapters (the connector
transport's controller, the delivery layer's parent-authority seam)
depend on this package; it depends on none of them. A fresh interpreter
importing this package loads no provider (pinned behaviorally).

Trust separation, stated exactly. A Mission Authorization is issued in
exactly one function, ``MissionService.apply_human_decision``, and only
from a ``HumanDecisionEnvelope`` carrying an ``AuthenticatedContext``
that the TRANSPORT ADAPTER built from its own authenticated state (a
connector server builds it after its bearer check; a local ceremony
would build it from the local process user). No field of that context is
reachable from a tool payload. Provenance records state only what is
known: the transport, the KIND of authenticated principal and its
reference, the configured subject if one is known, the receive time,
and the request / decision, Mission and revision bindings, with
``human_identity_proof`` explicitly ``null``. DI verifies the configured
transport credential, not the human behind it: whoever holds that
credential can issue decisions. This is structural, in-process
separation backed by behavioral tests and call-site pins; it is NOT
cryptographic security, arbitrary in-process Python can construct any
object, and no field here proves a human's identity.

Identity and idempotency. ``request_id`` and ``decision_id`` are minted
by DI and durably reserved (with the reserving authenticated context)
before use; a caller-chosen string that merely fits the grammar is
refused. A reserved id replayed with identical content returns the same
Mission (or the same recorded decision); replayed with different
content it refuses; presented from a different principal it refuses.
Stated residual limit: if the first response is lost before the client
learns its DI-issued id, a retry mints a new id and creates a second
Mission. That duplicate carries no authority; authority still requires
a separate human decision on a specific Mission and revision.
"""

from mission.record import MissionError
from mission.store import MissionStoreError

__all__ = ["MissionError", "MissionStoreError"]
