"""The local operator request surface: a neutral DI entry point that any
local caller can invoke.

A local caller submits a request and receives a bounded Mission
proposal (Mission Core's own proposal, revision and digest), reads
durable status from Mission Core's own read-only observation, and may
cancel its OWN pending proposal with the one-shot control capability it
received at creation. Ordinary approval through this surface is always
refused with a precise missing-capability reason. Separately (Task 8,
user decision), the Outer Operator may relay the human's exact affirmative
reply as an operator-attested approval: trusted by declared policy, NOT
independently verified, with the residual risk that a mistaken or
malicious same-user operator or local process could fabricate it. None of
those operations starts any work. The run commands (Task 8 increment 2b)
drive the request's own AUTHORIZED Mission through a run bridge that only
``local_request.cli`` constructs, lazily, for those commands alone; this
package never imports it otherwise. They confer no delivery authority. See
``local_request.surface``.

It is not an adapter for any vendor or app. Its caller is "a local
caller": unidentified, unauthenticated, and never a human approver. It
happens to be reachable from any local process, including a local Codex
task; nothing here models any other identity, API, session or
principal.

``local_request.cli`` is deliberately not imported here.
"""

from local_request.store import LocalRequestStore, LocalRequestStoreError
from local_request.surface import (
    LOCAL_CALLER_CONTEXT,
    LocalRequestRefusal,
    LocalRequestSurface,
)

__all__ = [
    "LOCAL_CALLER_CONTEXT",
    "LocalRequestRefusal",
    "LocalRequestStore",
    "LocalRequestStoreError",
    "LocalRequestSurface",
]
