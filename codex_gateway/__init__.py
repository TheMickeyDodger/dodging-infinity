"""Codex Gateway: a local, transport-neutral interface boundary.

The gateway takes human intent from a terminal and routes it ONLY into
the existing Codex Operator workflow — the locally installed ``codex``
CLI running this repository's Operator contract. It does not understand,
construct, dispatch, monitor, or control Herdr work, and it must never
import or invoke Herdr, herdctl, or any orchestration machinery; the
static suite enforces that boundary. It is stateless (session
continuation is the caller passing a session id back in), uses no
network, runs no daemon, and implements no authentication.
"""

from codex_gateway.contract import (
    ERROR_DETAIL_MAX_CHARS,
    EXIT_CODE_BY_STATUS,
    GATEWAY_CONTRACT_VERSION,
    GatewayError,
    GatewayRequest,
    GatewayResult,
    STATUS_CODEX_FAILED,
    STATUS_CODEX_UNAVAILABLE,
    STATUS_COMPLETED,
    STATUS_INVALID_REQUEST,
    STATUS_MALFORMED_OUTPUT,
    default_request_id_factory,
    exit_code_for_status,
    make_error,
    result_to_dict,
)
from codex_gateway.gateway import build_request, submit

__all__ = [
    "ERROR_DETAIL_MAX_CHARS",
    "EXIT_CODE_BY_STATUS",
    "GATEWAY_CONTRACT_VERSION",
    "GatewayError",
    "GatewayRequest",
    "GatewayResult",
    "STATUS_CODEX_FAILED",
    "STATUS_CODEX_UNAVAILABLE",
    "STATUS_COMPLETED",
    "STATUS_INVALID_REQUEST",
    "STATUS_MALFORMED_OUTPUT",
    "build_request",
    "default_request_id_factory",
    "exit_code_for_status",
    "make_error",
    "result_to_dict",
    "submit",
]
