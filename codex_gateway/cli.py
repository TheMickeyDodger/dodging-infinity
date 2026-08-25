"""Terminal adapter for the Codex Gateway.

Parses arguments and stdin into a GatewayRequest, submits it, renders
the result, and exits with the deterministic status code. The CLI
interprets NO approval, clarification, mission, or delivery semantics
and reads or writes no orchestration state (it never touches Herdr or
.herd). Stdout carries the result; errors go to stderr as one actionable
line; on the byte boundaries the gateway controls (intent in, Codex
streams out) a traceback is never printed — though, as in any Python
CLI, an interpreter stdout encoding overridden to one that cannot
represent a valid message can still fail during rendering.
"""

import argparse
import json
import os
import sys

from codex_gateway import gateway
from codex_gateway.contract import (
    EXIT_CODE_BY_STATUS,
    STATUS_INVALID_REQUEST,
    exit_code_for_status,
    result_to_dict,
)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="codexgw",
        description=(
            "Route human intent from this terminal into the local Codex CLI"
            " operator workflow for one repository."
        ),
        epilog=(
            "With no intent arguments, the intent text is read from piped"
            " stdin. Exit codes: 0 completed, 2 invalid request, 3 codex"
            " unavailable, 4 codex failed, 5 malformed output."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        default=None,
        help="resume the given Codex session instead of starting a new one",
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        default=None,
        help="target repository (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the result contract as JSON instead of text",
    )
    parser.add_argument(
        "intent",
        nargs="*",
        help="intent text; when omitted, read from stdin",
    )
    return parser


def _read_intent(namespace):
    """Intent from argv words or piped stdin; identical text either way.

    Piped stdin is read as BYTES and decoded as strict UTF-8, so invalid
    byte sequences map to an actionable invalid_request instead of a
    decode crash — and a C/POSIX-locale surrogateescape decode can never
    smuggle lone surrogates in through this path.
    """
    if namespace.intent:
        return " ".join(namespace.intent).strip(), None
    stdin = sys.stdin
    if stdin is None or stdin.isatty():
        return None, (
            "no intent text: pass it as arguments or pipe it on stdin"
        )
    buffer = getattr(stdin, "buffer", None)
    if buffer is not None:
        try:
            text = buffer.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            return None, (
                "stdin is not valid UTF-8 (%s); re-send the intent as"
                " UTF-8 text" % exc
            )
        return text.strip(), None
    return stdin.read().strip(), None


def _render(result, as_json):
    exit_code = exit_code_for_status(result.status)
    if as_json:
        print(json.dumps(result_to_dict(result), sort_keys=True), file=sys.stdout)
        return exit_code
    if result.message is not None:
        print(result.message, file=sys.stdout)
    if result.error is not None:
        detail = " ".join(result.error.detail.split()) or "(no detail)"
        suffix = " [detail truncated]" if result.error.detail_truncated else ""
        print(
            "codexgw: %s: %s: %s%s"
            % (result.status, result.error.code, detail, suffix),
            file=sys.stderr,
        )
    if result.unrecognized_event_lines > 0:
        print(
            "codexgw: %d unrecognized event line(s) in codex output; the"
            " declared compatibility surface may have drifted"
            % result.unrecognized_event_lines,
            file=sys.stderr,
        )
    if result.session_id:
        print(
            "codexgw: session %s (continue with: codexgw --resume %s ...)"
            % (result.session_id, result.session_id),
            file=sys.stderr,
        )
    return exit_code


def main(argv=None):
    parser = _build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse has already printed an actionable usage message; its
        # error exit code (2) matches invalid_request.
        code = exc.code
        return code if isinstance(code, int) else EXIT_CODE_BY_STATUS[STATUS_INVALID_REQUEST]
    text, intent_problem = _read_intent(namespace)
    if intent_problem is not None:
        print("codexgw: invalid_request: %s" % intent_problem, file=sys.stderr)
        return EXIT_CODE_BY_STATUS[STATUS_INVALID_REQUEST]
    repository_path = namespace.repo if namespace.repo else os.getcwd()
    request = gateway.build_request(
        text=text,
        repository_path=repository_path,
        session_id=namespace.resume,
        source="terminal",
    )
    result = gateway.submit(request)
    return _render(result, namespace.as_json)
