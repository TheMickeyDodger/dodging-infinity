"""The Codex-backed operator sessions: the package's ONLY provider import.

The hooks resolve ``build_request``, ``submit``, ``submit_restricted`` and
``submit_planning`` as ATTRIBUTES of the ``codex_gateway.gateway`` module at
call time — never bound at import time — so the gateway module remains the
single point of substitution.

``CodexOperatorSession`` runs the gateway's ambient turn (the user's own
Codex configuration; a session id resumes). ``RestrictedCodexOperatorSession``
runs ``gateway.submit_restricted``: ONE fresh turn under the role-turn
read-only posture, verified on the exact argv before any spawn, which
continues no session (a session id is refused before any argv exists).
``RestrictedPlanningOperatorSession`` runs ``gateway.submit_planning``: ONE
fresh DI-REMOTE-2 planning turn under the same posture (the Grok Bot
conforming route's planning step), equally fresh-only.
"""

import time

from codex_gateway import gateway as gateway_module

from operator_session.session import OperatorSession


class CodexOperatorSession(OperatorSession):
    """Prepare/execute through the Codex Gateway."""

    def _build_request(self, text, repository, session_id=None,
                       source="terminal"):
        return gateway_module.build_request(
            text, repository, session_id=session_id, source=source
        )

    def _submit(self, request):
        return gateway_module.submit(request)


class RestrictedCodexOperatorSession(CodexOperatorSession):
    """Prepare/execute through the gateway's restricted, fresh-only turn.

    ``runner`` exists for hermetic tests to inject a recorder in place of
    the one Codex spawn; production passes none.
    """

    def __init__(self, runner=None):
        self._runner = runner

    def _submit(self, request):
        return gateway_module.submit_restricted(request, runner=self._runner)


class RestrictedPlanningOperatorSession(CodexOperatorSession):
    """Prepare/execute through the gateway's fresh restrictive PLANNING turn.

    ``prepare`` builds the gateway request (its text the human's exact
    request, its repository the control repository) exactly as every Codex
    session does; ``execute`` makes the one provider call,
    ``gateway.submit_planning``, resolved at call time, and returns its
    ``RoleTurnResult`` unchanged. ``clock`` supplies the turn's recorded
    time; ``runner`` exists for hermetic tests to inject a recorder in place
    of the one Codex spawn. Production passes neither runner nor clock.
    """

    def __init__(self, clock=None, runner=None):
        self._clock = clock or (lambda: int(time.time()))
        self._runner = runner

    def _submit(self, request):
        return gateway_module.submit_planning(request, now=self._clock(),
                                              runner=self._runner)
