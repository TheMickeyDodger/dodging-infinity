"""Gateway orchestration: validate the request, then invoke Codex.

``submit`` is the single entry point: repository and intent validation
run first, and only a fully valid request reaches the Codex adapter.
The gateway is stateless — session continuation is the caller passing a
session id back in — and it writes nothing anywhere at runtime. It must
never import or invoke Herdr in any form; it only routes intent into the
local Codex CLI, which runs the repository's Operator contract.
"""

from dataclasses import replace

from codex_gateway import codex_adapter
from codex_gateway import repository as repository_module
from codex_gateway.contract import (
    ERROR_BANNED_FLAG,
    ERROR_EMPTY_INTENT,
    ERROR_EMPTY_SESSION_ID,
    ERROR_FLAG_LIKE_SESSION_ID,
    ERROR_INTENT_NOT_UTF8,
    GATEWAY_CONTRACT_VERSION,
    GatewayRequest,
    GatewayResult,
    STATUS_INVALID_REQUEST,
    default_request_id_factory,
    make_error,
)


def build_request(text, repository_path, session_id=None, source="terminal",
                  request_id_factory=None):
    """Build a GatewayRequest with a normalized absolute repository path."""
    factory = request_id_factory or default_request_id_factory
    return GatewayRequest(
        contract_version=GATEWAY_CONTRACT_VERSION,
        request_id=factory(),
        source=source,
        repository=repository_module.resolve_repository_path(repository_path),
        text=text,
        session_id=session_id,
    )


def _error_result(request, status, error):
    return GatewayResult(
        contract_version=GATEWAY_CONTRACT_VERSION,
        request_id=request.request_id,
        session_id=None,
        status=status,
        message=None,
        error=error,
        unrecognized_event_lines=0,
    )


def submit(request):
    """Validate a request, invoke Codex, and return a GatewayResult."""
    if not isinstance(request.text, str) or not request.text.strip():
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(
                ERROR_EMPTY_INTENT,
                "intent text is empty or whitespace; provide the request text",
            ),
        )
    try:
        request.text.encode("utf-8")
    except UnicodeEncodeError as exc:
        # Lone surrogates (for example from a C/POSIX-locale stdin read
        # elsewhere) can never be sent to codex; reject them before any
        # argv is built or any process is invoked.
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(
                ERROR_INTENT_NOT_UTF8,
                "intent text is not UTF-8 encodable (%s); re-send the"
                " intent as valid UTF-8 text" % exc,
            ),
        )
    if request.session_id is not None and (
        not isinstance(request.session_id, str) or not request.session_id.strip()
    ):
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(
                ERROR_EMPTY_SESSION_ID,
                "session id is empty or whitespace; pass the session id"
                " reported by the previous result",
            ),
        )
    if request.session_id is not None and request.session_id.lstrip().startswith("-"):
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(
                ERROR_FLAG_LIKE_SESSION_ID,
                "session id %r looks like a flag; pass the session id"
                " reported by the previous result" % request.session_id,
            ),
        )
    resolved, error = repository_module.validate_repository(request.repository)
    if error is not None:
        return _error_result(request, STATUS_INVALID_REQUEST, error)
    request = replace(request, repository=resolved)
    try:
        status, session_id, message, error, unrecognized = (
            codex_adapter.run_codex_turn(request)
        )
    except codex_adapter.BannedFlagError as exc:
        # Defense in depth: a request element that would place a banned
        # Codex flag in the argv is an invalid request, never a crash.
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(ERROR_BANNED_FLAG, str(exc)),
        )
    except UnicodeEncodeError as exc:
        # Defense in depth behind the pre-invocation encodability check:
        # a request that cannot be encoded for the codex process is an
        # invalid request, never a crash.
        return _error_result(
            request,
            STATUS_INVALID_REQUEST,
            make_error(
                ERROR_INTENT_NOT_UTF8,
                "request could not be encoded for the codex process (%s);"
                " re-send the intent as valid UTF-8 text" % exc,
            ),
        )
    return GatewayResult(
        contract_version=GATEWAY_CONTRACT_VERSION,
        request_id=request.request_id,
        session_id=session_id,
        status=status,
        message=message,
        error=error,
        unrecognized_event_lines=unrecognized,
    )
