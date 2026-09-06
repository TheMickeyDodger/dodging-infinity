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

Package-level names are resolved LAZILY (PEP 562 ``__getattr__``):
``workflow_authority.store`` imports the Telegram adapter's
configuration for its default directory, so an eager import here would
put a provider into the import closure of every neutral consumer of
``workflow_authority.atomic``, ``.digest`` or ``.canonical`` (the
Mission Core must load no provider). Every name in ``__all__`` still
resolves exactly as before, on first attribute access, from the same
submodule that always defined it; nothing is renamed and no import
form changes. The lazy resolution uses ordinary import statements,
never dynamic import machinery.
"""

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

_DIGEST_NAMES = frozenset(("DigestError",))
_RECORD_NAMES = frozenset((
    "WORKFLOW_SCHEMA_VERSION", "RecordError", "validate_record",
    "validate_transition",
))
_STORE_NAMES = frozenset((
    "MAX_WORKFLOW_RECORDS", "StoreError", "WorkflowStore",
))


def __getattr__(name):
    if name in _DIGEST_NAMES:
        from workflow_authority import digest as module
    elif name in _RECORD_NAMES:
        from workflow_authority import record as module
    elif name in _STORE_NAMES:
        from workflow_authority import store as module
    else:
        raise AttributeError(
            "module 'workflow_authority' has no attribute %r" % (name,)
        )
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
