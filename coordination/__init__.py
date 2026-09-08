"""Dodging Infinity coordination: Mission Routing, Attention, and Bot
Coordination (Task 6), neutral and read-only toward the Mission.

What this package owns: durable route decisions and the bounded
conversation bindings they resolve through; attention records that
project BLOCKED / NEEDS_HUMAN / AUTHORIZATION_READY / RESULT_READY
conditions with deterministic priority and durable duplicate
suppression; bot-handoff records that carry context and a request
between eligible participants; and the ONE narrow read-only
observation contract (``coordination.observation``) through which every
canonical Mission fact reaches it.

What it refuses, in plain terms. It creates no Mission and no proposal
request id; it never launches a Mission or a Capability; it sends no
message and performs no network, process, Git or delivery action; it
holds no Mission, execution, credential or delivery authority and can
mint, broaden or transfer none. A route is identity resolution, never
permission to run anything; a lane value names where engineering work
WOULD go, never an instruction to go there. Presenting an
authorization-ready condition is not approval; a result-ready condition
is not delivery permission; acknowledging attention neither resolves a
blocker nor authorizes anything; a handoff transfers context and a
request only, never ownership, authority, or verified-result status.
Every durable record carries ``"authority": "none"`` and the store
refuses any other value.

Dependency direction. This package imports the standard library and
exactly three stdlib-only helpers from ``workflow_authority``
(``atomic``, ``digest``, ``canonical``). It imports no transport, no
model provider, no operator session, no orchestration engine, no
delivery package, no execution seam, and — deliberately — not
``mission``: Mission facts arrive only as observation VALUES a caller
injects, so this package can never become a consumer the Mission Core's
consumer pin would have to admit.

What is NOT here, and is not stubbed or prepared for: an Event Journal,
a Mission Observation Service (the binding of the observation contract
to a live Mission store), a Reconciler, a scheduler, a worker fleet,
live bot messaging, live dispatch, and the natural-language routing
turn. Observation that is missing, unavailable, stale or inconsistent
fails conservatively — clarification or refusal with an explicit
reason — never a guess.
"""

from coordination.record import CoordinationError
from coordination.store import CoordinationStoreError

__all__ = ["CoordinationError", "CoordinationStoreError"]
