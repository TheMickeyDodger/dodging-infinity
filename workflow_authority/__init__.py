"""Durable logical workflow authority for DI-REMOTE-2.

This package owns the durable workflow authority record that exactly
binds control identity and policy digest, the canonical GitHub target
and OPTIONAL issue or PR, the approved baseline, the exact original
human intent, the exact rendered Mission Authorization (its digest,
revision, and every authority-content field — objective, constraints,
rules, desired outcome, acceptance, unresolved questions, execution
scope), the Telegram approval bindings, nonce/expiry/consumption,
handoff revision and digest, lifecycle phase, workspace lease,
preparation/evidence receipts, Codex turn identities, ambiguity
state, and ``delivery_authority`` fixed to ``"none"``.

It is the ONLY coupling medium between the control chain (Telegram ->
Gateway -> fresh read-only Codex) and the execution chain (Runtime ->
Broker -> target engineering). It is a data-authority layer, not an
execution system: it must never import or invoke the orchestration
engine or its control CLI, and the static suite enforces that boundary
both statically and behaviorally. Every load and every save validates
a CLOSED key set and fails closed; a malformed or unknown-version
store is never silently reinitialized.
"""

from workflow_authority.digest import DigestError
from workflow_authority.record import (
    WORKFLOW_SCHEMA_VERSION,
    RecordError,
    validate_record,
    validate_transition,
)
from workflow_authority.store import (
    MAX_WORKFLOW_RECORDS,
    StoreError,
    WorkflowStore,
)

__all__ = [
    "DigestError",
    "MAX_WORKFLOW_RECORDS",
    "RecordError",
    "StoreError",
    "WORKFLOW_SCHEMA_VERSION",
    "WorkflowStore",
    "validate_record",
    "validate_transition",
]
