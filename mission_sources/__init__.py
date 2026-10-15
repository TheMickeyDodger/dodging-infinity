"""Dodging Infinity mission sources: a read-only, versioned query contract
over explicitly registered DI mission stores, plus roster semantics for
the agents canonically bound to their ACTIVE missions.

What this package owns: an explicit, durable, inspectable source registry
(``mission_sources.registry``) in which a store is registered as DATA; the
shared versioned query contract and its freshness / health model
(``mission_sources.contract``); read-only readers for exactly two store
kinds, canonical Mission Core and the legacy Grok WorkflowStore, whose
lifecycle models are reported side by side and never merged into one
vocabulary; the roster derivation that classifies agents of active,
canonically bound missions; and readiness for the three proof-contract
resources through Mission Core's own readiness path
(``mission_sources.readiness``).

Facts about the engineering runtime (Herdr tasks, role bindings, live
agents) arrive only as INJECTED observation VALUES in the runtime
snapshot shape this package defines; it imports no runtime engine, no
transport and no orchestration package, and a runtime snapshot it cannot
validate, or one that is stale, never renders as live and never yields a
binding.

What it refuses, in plain terms: it creates, edits, approves, dispatches,
pauses, resumes, cancels and closes no mission; it starts, restores,
restarts, archives, messages, types into or queues work for no agent or
session; it holds no mission, execution, credential or delivery
authority. Its durable writes are exactly two: its own registry file, and
readiness observations for the proof contract's resources reported through
Mission Core's own validated ``observe_resource_readiness`` with a
caller-supplied authenticated context and reserved operation ids. Unknown or
unsupported facts (model, cost, tokens, progress, a lifecycle literal it
does not know) are reported as unknown, never defaulted. A store, a
registry or a runtime snapshot that is absent is unavailable, never an
observed-empty, healthy one.
"""

from mission_sources.contract import ContractError
from mission_sources.registry import RegistryError

__all__ = ["ContractError", "RegistryError"]
