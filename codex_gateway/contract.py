"""Versioned request/result contract for the Codex Gateway.

The gateway is a local, transport-neutral interface boundary that routes
human intent from a terminal into the locally installed Codex CLI, which
runs the repository's Operator contract. This module owns the versioned
data shapes that cross that boundary and the deterministic status and
exit-code vocabulary. It knows nothing about Herdr and must never import
it (the gateway does not construct, dispatch, monitor, or control
engineering-execution work).

Every error detail is bounded by ``ERROR_DETAIL_MAX_CHARS``. A capped
value is never presented as complete: when the cap bites,
``detail_truncated`` is True.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

GATEWAY_CONTRACT_VERSION = 1

# Upper bound on error detail text carried in a result. A hard constant,
# never derived from input.
ERROR_DETAIL_MAX_CHARS = 2000

STATUS_COMPLETED = "completed"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_CODEX_UNAVAILABLE = "codex_unavailable"
STATUS_CODEX_FAILED = "codex_failed"
STATUS_MALFORMED_OUTPUT = "malformed_output"

# Deterministic status -> process exit code mapping.
EXIT_CODE_BY_STATUS = {
    STATUS_COMPLETED: 0,
    STATUS_INVALID_REQUEST: 2,
    STATUS_CODEX_UNAVAILABLE: 3,
    STATUS_CODEX_FAILED: 4,
    STATUS_MALFORMED_OUTPUT: 5,
}

# Error codes carried in GatewayError.code.
ERROR_EMPTY_INTENT = "empty_intent"
ERROR_EMPTY_SESSION_ID = "empty_session_id"
ERROR_FLAG_LIKE_SESSION_ID = "flag_like_session_id"
ERROR_BANNED_FLAG = "banned_flag_element"
ERROR_INTENT_NOT_UTF8 = "intent_not_utf8"
ERROR_OUTPUT_NOT_UTF8 = "codex_output_not_utf8"
ERROR_REPOSITORY_MISSING = "repository_missing"
ERROR_GIT_UNAVAILABLE = "git_unavailable"
ERROR_NOT_A_GIT_WORKTREE = "not_a_git_worktree"
ERROR_OPERATOR_CONTRACT_MISSING = "operator_contract_missing"
ERROR_CODEX_NOT_FOUND = "codex_not_found"
ERROR_CODEX_EXIT_NONZERO = "codex_exit_nonzero"
ERROR_CODEX_FAILURE_EVENT = "codex_failure_event"
ERROR_UNRECOGNIZED_OUTPUT = "unrecognized_output"


@dataclass(frozen=True)
class GatewayError:
    """A bounded, machine-readable error carried in a result."""

    code: str
    detail: str
    detail_truncated: bool


@dataclass(frozen=True)
class GatewayRequest:
    """One unit of human intent submitted through the gateway."""

    contract_version: int
    request_id: str
    source: str
    repository: str
    text: str
    session_id: Optional[str] = None


@dataclass(frozen=True)
class GatewayResult:
    """The outcome of one submitted request.

    ``unrecognized_event_lines`` is always present and deliberately has
    no default (every construction site must state it explicitly): the
    number of non-blank Codex output lines that matched no declared event
    shape while producing this result (0 on paths where no stream was
    parsed). A partially-unparsed stream is never presented as a complete
    answer.
    """

    contract_version: int
    request_id: str
    session_id: Optional[str]
    status: str
    message: Optional[str]
    error: Optional[GatewayError]
    unrecognized_event_lines: int


def default_request_id_factory():
    """Default request-id source; tests inject a deterministic factory."""
    return uuid.uuid4().hex


def make_error(code, detail):
    """Build a GatewayError with the detail bound applied honestly."""
    text = "" if detail is None else str(detail)
    truncated = len(text) > ERROR_DETAIL_MAX_CHARS
    if truncated:
        text = text[:ERROR_DETAIL_MAX_CHARS]
    return GatewayError(code=code, detail=text, detail_truncated=truncated)


def exit_code_for_status(status):
    """Map a result status to its deterministic process exit code."""
    return EXIT_CODE_BY_STATUS[status]


def result_to_dict(result):
    """Canonical JSON-ready representation of a GatewayResult."""
    error = None
    if result.error is not None:
        error = {
            "code": result.error.code,
            "detail": result.error.detail,
            "detail_truncated": result.error.detail_truncated,
        }
    return {
        "contract_version": result.contract_version,
        "request_id": result.request_id,
        "session_id": result.session_id,
        "status": result.status,
        "message": result.message,
        "error": error,
        "unrecognized_event_lines": result.unrecognized_event_lines,
    }
