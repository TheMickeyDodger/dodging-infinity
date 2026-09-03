"""The Codex-backed operator session: the package's ONLY provider import.

The hooks resolve ``build_request`` and ``submit`` as ATTRIBUTES of the
``codex_gateway.gateway`` module at call time — never bound at import
time — so the gateway module remains the single point of substitution.
"""

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
