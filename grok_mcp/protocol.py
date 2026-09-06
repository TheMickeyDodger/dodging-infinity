"""MCP wire shapes for the Grok Bot connector: JSON-RPC 2.0 framing,
the bounded tool table, and schema validation.

This module is stdlib only and PROVIDER-FREE in the other direction:
it imports no operator session, no interaction seam, no transport, and
no orchestration machinery. It knows the Model Context Protocol
(legacy initialize-handshake era, Streamable HTTP) and nothing about
who is on either side of it.

What it owns:

- The supported protocol revisions and the version-negotiation rule
  (echo a supported request, otherwise answer the newest supported).
- The three-tool table. Every tool carries an exact ``inputSchema``
  and ``outputSchema`` with ``additionalProperties: false``, an
  explicit ``required`` list, and explicit bounds. There is no shell
  tool, no run-command tool, no file tool, no path, URL, argv, or
  repository argument, and no field that names or accepts a Grok
  conversation id, message id, user id, or thread id.
- ``schema_problems``: a validator for exactly the JSON-schema subset
  the table uses. Its problem strings name the offending path and the
  violated constraint only — never the caller's value — so a refusal
  reason can be returned verbatim.
- The JSON-RPC error codes and result builders for ``initialize``,
  ``ping``, and ``tools/list``.

What it does not own: no state, no identity minting, no operator call,
no HTTP.
"""

import re

JSONRPC_VERSION = "2.0"

# Newest first. A supported requested version is echoed; anything
# else is answered with the first entry.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

CONTRACT_VERSION = 1
SERVER_NAME = "dodging-infinity"
SERVER_VERSION = "1"
SERVER_INSTRUCTIONS = (
    "Dodging Infinity exposes three tools: di_status and di_ping are"
    " pure liveness/readiness checks; di_operator_turn sends one bounded"
    " text turn to the local operator and returns its reply. Continue"
    " an operator session by passing back the session_ref this server"
    " returned; repeat a turn's recorded result by passing back its"
    " turn_ref. No other references are accepted."
)

TOOL_STATUS = "di_status"
TOOL_PING = "di_ping"
TOOL_OPERATOR_TURN = "di_operator_turn"
TOOL_NAMES = (TOOL_STATUS, TOOL_PING, TOOL_OPERATOR_TURN)

# Bounds. Every one is exact-value pinned in the test suite.
MAX_TURN_TEXT_CHARS = 4000
MAX_ECHO_CHARS = 200
# Largest accepted HTTP request body, enforced by the server before
# any byte of the body is read.
MAX_REQUEST_BYTES = 65536
REF_HEX_CHARS = 32
REF_CHARS = 35

REF_PREFIX = "di-"
REF_PATTERN = "^di-[0-9a-f]{32}$"
_REF_RE = re.compile(REF_PATTERN)

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

METHOD_INITIALIZE = "initialize"
METHOD_INITIALIZED = "notifications/initialized"
METHOD_PING = "ping"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"

STATUS_REFUSED = "refused"
STATUS_OPERATOR_ERROR = "operator_error"
STATUS_COMPLETED = "completed"

SOURCE = "grok_mcp"


def _ref_schema():
    return {
        "type": "string", "minLength": REF_CHARS, "maxLength": REF_CHARS,
        "pattern": REF_PATTERN,
    }


def _nullable_ref_schema():
    return {
        "type": ["string", "null"], "minLength": REF_CHARS,
        "maxLength": REF_CHARS, "pattern": REF_PATTERN,
    }


def _build_tools(bounds):
    return (
        {
            "name": TOOL_STATUS,
            "title": "Dodging Infinity status",
            "description": (
                "Report whether the Dodging Infinity tool surface is"
                " wired (ready reports the endpoint, not the operator),"
                " the contract version, supported MCP protocol versions,"
                " enforced bounds, and the tool names. Takes no arguments"
                " and invokes no operator; safe to retry."
            ),
            "inputSchema": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "ready": {"type": "boolean"},
                    "contract_version": {"type": "integer"},
                    "protocol_versions": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "bounds": {
                        "type": "object",
                        "properties": dict(
                            (name, {"type": "integer"}) for name in bounds
                        ),
                        "required": list(bounds),
                        "additionalProperties": False,
                    },
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "ready", "contract_version",
                    "protocol_versions", "tools", "bounds", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_PING,
            "title": "Dodging Infinity ping",
            "description": (
                "Liveness check. Optionally echoes a short string back."
                " Invokes no operator; safe to retry."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "echo": {"type": "string", "minLength": 0,
                             "maxLength": MAX_ECHO_CHARS},
                },
                "required": [],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "pong": {"type": "boolean"},
                    "echo": {"type": ["string", "null"],
                             "maxLength": MAX_ECHO_CHARS},
                    "call_ref": _ref_schema(),
                },
                "required": ["ok", "reason", "pong", "echo", "call_ref"],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_OPERATOR_TURN,
            "title": "Dodging Infinity operator turn",
            "description": (
                "Send one bounded text turn to the Dodging Infinity"
                " operator and return its reply. Pass back the returned"
                " session_ref to continue the same operator session."
                " Pass back a returned turn_ref to fetch that turn's"
                " recorded result again without re-running it."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1,
                             "maxLength": MAX_TURN_TEXT_CHARS},
                    "session_ref": _ref_schema(),
                    "turn_ref": _ref_schema(),
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "request_id": {"type": ["string", "null"]},
                    "session_ref": _nullable_ref_schema(),
                    "turn_ref": _ref_schema(),
                    "message": {"type": ["string", "null"]},
                    "chunks_sent": {"type": "integer", "minimum": 0},
                    "truncated_chars": {"type": "integer", "minimum": 0},
                    "replayed": {"type": "boolean"},
                },
                "required": [
                    "ok", "reason", "status", "request_id", "session_ref",
                    "turn_ref", "message", "chunks_sent",
                    "truncated_chars", "replayed",
                ],
                "additionalProperties": False,
            },
        },
    )


# The names reported under di_status "bounds", in order. The values
# are assembled by the controller, which owns the modules they live in.
BOUND_NAMES = (
    "max_turn_text_chars", "max_echo_chars", "max_request_bytes",
    "max_replay_entries", "max_session_entries", "max_message_chars",
    "max_message_chunks",
)

TOOLS = _build_tools(BOUND_NAMES)
_TOOLS_BY_NAME = dict((tool["name"], tool) for tool in TOOLS)


def tool_by_name(name):
    """The tool entry for ``name``, or None."""
    if not isinstance(name, str):
        return None
    return _TOOLS_BY_NAME.get(name)


def is_ref(value):
    """True for a syntactically valid DI-minted reference."""
    return isinstance(value, str) and _REF_RE.match(value) is not None


def _is_type(kind, value):
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "null":
        return value is None
    return False


def schema_problems(schema, value, path="arguments"):
    """Problems of ``value`` against ``schema``; empty when it conforms.

    Each problem names the path and the violated constraint only; the
    value itself is never included, so the list is safe to return to
    the caller verbatim.
    """
    problems = []
    kinds = schema.get("type")
    if kinds is not None:
        allowed = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is_type(kind, value) for kind in allowed):
            problems.append("%s: expected %s" % (path, "/".join(allowed)))
            return problems
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            problems.append("%s: shorter than minLength" % path)
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append("%s: longer than maxLength" % path)
        pattern = schema.get("pattern")
        if pattern is not None and re.match(pattern, value) is None:
            problems.append("%s: does not match pattern" % path)
    elif isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append("%s: below minimum" % path)
    elif isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                problems.append("%s.%s: required" % (path, name))
        for key in sorted(value):
            if key in properties:
                problems.extend(schema_problems(
                    properties[key], value[key], "%s.%s" % (path, key)
                ))
            elif schema.get("additionalProperties", True) is False:
                problems.append("%s.%s: unknown property" % (path, key))
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            problems.extend(schema_problems(
                schema["items"], item, "%s[%d]" % (path, index)
            ))
    return problems


def negotiate_version(requested):
    """The protocol version to answer an initialize request with."""
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return SUPPORTED_PROTOCOL_VERSIONS[0]


def initialize_result(requested):
    return {
        "protocolVersion": negotiate_version(requested),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": SERVER_INSTRUCTIONS,
    }


def ping_result():
    return {}


def tools_list_result():
    return {"tools": [dict(tool) for tool in TOOLS]}


def error_object(code, message):
    return {"code": code, "message": message}
