"""MCP wire shapes for the Grok Bot connector: JSON-RPC 2.0 framing,
the bounded tool table, and schema validation.

This module is PROVIDER-FREE in the other direction: it imports the
standard library and the neutral Mission Core's record vocabulary (for
the exact Mission id grammar and text bounds the Mission tools relay),
and no operator session, no interaction seam, no transport, and no
orchestration machinery. It knows the Model Context Protocol (legacy
initialize-handshake era, Streamable HTTP) and nothing about who is on
either side of it.

What it owns:

- The supported protocol revisions and the version-negotiation rule
  (echo a supported request, otherwise answer the newest supported).
- The eight-tool table: the three original tools plus the five bounded
  Mission tools (propose, get, edit, approve, deny). Every tool carries
  an exact ``inputSchema`` and ``outputSchema`` with
  ``additionalProperties: false``, an explicit ``required`` list, and
  explicit bounds. There is no shell tool, no run-command tool, no file
  tool, no dispatch, capability, Git, delivery, merge, release, or
  deploy tool, no path, argv, or shell argument, and no field that
  names or accepts a Grok conversation id, message id, user id, or
  thread id. No Mission tool input names a principal, actor, subject,
  provenance, decision id, or authorization id: the authenticated
  context comes from the server's own bearer check, never from a
  payload. The only URL-shaped input is the Mission proposal's
  canonical GitHub repository identity, which the neutral core
  validates strictly and which nothing here opens, fetches, or runs.
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

from mission import record as mission_record

JSONRPC_VERSION = "2.0"

# Newest first. A supported requested version is echoed; anything
# else is answered with the first entry.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

CONTRACT_VERSION = 1
SERVER_NAME = "dodging-infinity"
SERVER_VERSION = "1"
SERVER_INSTRUCTIONS = (
    "Dodging Infinity exposes eight tools: di_status and di_ping are"
    " pure liveness/readiness checks; di_operator_turn sends one bounded"
    " text turn to the local operator and returns its reply. Continue"
    " an operator session by passing back the session_ref this server"
    " returned; repeat a turn's recorded result by passing back its"
    " turn_ref. The five di_mission_* tools propose, read, edit, approve"
    " and deny a Mission in the Dodging Infinity Mission registry; a"
    " decision binds the exact revision you pass, approval records"
    " authority and starts nothing, and github_pr names only the"
    " Mission's later delivery scope. Pass back the request_id a"
    " propose call returned to retry it safely. No other references"
    " are accepted."
)

TOOL_STATUS = "di_status"
TOOL_PING = "di_ping"
TOOL_OPERATOR_TURN = "di_operator_turn"
TOOL_MISSION_PROPOSE = "di_mission_propose"
TOOL_MISSION_GET = "di_mission_get"
TOOL_MISSION_EDIT = "di_mission_edit"
TOOL_MISSION_APPROVE = "di_mission_approve"
TOOL_MISSION_DENY = "di_mission_deny"
MISSION_TOOL_NAMES = (
    TOOL_MISSION_PROPOSE, TOOL_MISSION_GET, TOOL_MISSION_EDIT,
    TOOL_MISSION_APPROVE, TOOL_MISSION_DENY,
)
TOOL_NAMES = (TOOL_STATUS, TOOL_PING, TOOL_OPERATOR_TURN) + MISSION_TOOL_NAMES

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

# Mission Core identifiers as relayed on the wire: distinct prefixes
# from the transport reference, so one can never pass for the other.
MISSION_ID_PATTERN = "^mn-[0-9a-f]{32}$"
REQUEST_ID_PATTERN = "^mq-[0-9a-f]{32}$"
DECISION_ID_PATTERN = "^md-[0-9a-f]{32}$"
AUTHORIZATION_ID_PATTERN = "^ma-[0-9a-f]{32}$"
MISSION_TOKEN_CHARS = 35
DIGEST_CHARS = 64

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
    ) + _build_mission_tools()


def _token_schema(pattern):
    return {
        "type": "string", "minLength": MISSION_TOKEN_CHARS,
        "maxLength": MISSION_TOKEN_CHARS, "pattern": pattern,
    }


def _nullable_token_schema(pattern):
    return {
        "type": ["string", "null"], "minLength": MISSION_TOKEN_CHARS,
        "maxLength": MISSION_TOKEN_CHARS, "pattern": pattern,
    }


def _nullable_digest_schema():
    return {
        "type": ["string", "null"], "minLength": DIGEST_CHARS,
        "maxLength": DIGEST_CHARS, "pattern": "^[0-9a-f]{64}$",
    }


def _string_list_schema():
    return {"type": "array", "items": {"type": "string"}}


def _nullable_string_list_schema():
    return {"type": ["array", "null"], "items": {"type": "string"}}


def _proposal_properties():
    """The proposal fields as tool INPUT properties. Bounds are the
    Mission Core's own; the core re-validates every value strictly."""
    return {
        "objective": {
            "type": "string", "minLength": 1,
            "maxLength": mission_record.MAX_OBJECTIVE_CHARS,
        },
        "target_context": {
            "type": "string", "minLength": 0,
            "maxLength": mission_record.MAX_TARGET_CONTEXT_CHARS,
        },
        "repository_url": {
            "type": ["string", "null"], "minLength": 1,
            "maxLength": 512,
        },
        "requested_scope": {
            "type": "string", "minLength": 1,
            "maxLength": mission_record.MAX_SCOPE_TEXT_CHARS,
        },
        "requested_action_scope": _string_list_schema(),
        "requested_delivery_target": {
            "type": ["string", "null"], "minLength": 1, "maxLength": 64,
        },
    }


PROPOSAL_INPUT_NAMES = tuple(mission_record.PROPOSAL_KEYS)


def _proposal_output_schema():
    properties = dict(_proposal_properties())
    return {
        "type": ["object", "null"],
        "properties": properties,
        "required": list(PROPOSAL_INPUT_NAMES),
        "additionalProperties": False,
    }


def _decision_output(extra_properties, extra_required):
    """Decision tool output. ``revision``, ``state`` and
    ``proposal_digest_sha256`` are the HISTORICAL result of the decision
    (what it did when applied; unchanged on an idempotent replay);
    ``current_revision`` and ``current_state`` are the Mission NOW."""
    properties = {
        "ok": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
        "status": {"type": "string"},
        "problem": {"type": ["string", "null"]},
        "mission_id": _nullable_token_schema(MISSION_ID_PATTERN),
        "revision": {"type": ["integer", "null"], "minimum": 1},
        "state": {"type": ["string", "null"]},
        "decision_id": _nullable_token_schema(DECISION_ID_PATTERN),
        "proposal_digest_sha256": _nullable_digest_schema(),
        "idempotent": {"type": "boolean"},
        "current_revision": {"type": ["integer", "null"], "minimum": 1},
        "current_state": {"type": ["string", "null"]},
        "call_ref": _ref_schema(),
    }
    properties.update(extra_properties)
    return {
        "type": "object",
        "properties": properties,
        "required": [
            "ok", "reason", "status", "problem", "mission_id", "revision",
            "state", "decision_id", "proposal_digest_sha256", "idempotent",
            "current_revision", "current_state", "call_ref",
        ] + list(extra_required),
        "additionalProperties": False,
    }


def _build_mission_tools():
    proposal_inputs = _proposal_properties()
    return (
        {
            "name": TOOL_MISSION_PROPOSE,
            "title": "Dodging Infinity Mission proposal",
            "description": (
                "Propose a new Mission: objective, target context, the exact"
                " canonical GitHub repository URL when one applies (or"
                " null), requested scope text, requested action scope"
                " (engineering_change, repository_read, verification_run),"
                " and an optional delivery target (github_pr names only the"
                " Mission's later delivery scope, never a Git action)."
                " Returns the stable Mission id, the DI-issued request_id,"
                " and one coherent triple: the current revision, its exact"
                " canonical proposal, and that proposal's digest. Pass the"
                " request_id back to retry safely; a retry reports the"
                " Mission as it is now. A Mission awaits a human decision"
                " and carries no authority."
            ),
            "inputSchema": {
                "type": "object",
                "properties": dict(
                    proposal_inputs,
                    request_id=_token_schema(REQUEST_ID_PATTERN),
                ),
                "required": list(PROPOSAL_INPUT_NAMES),
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "problem": {"type": ["string", "null"]},
                    "mission_id": _nullable_token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": ["integer", "null"], "minimum": 1},
                    "state": {"type": ["string", "null"]},
                    "request_id": _nullable_token_schema(REQUEST_ID_PATTERN),
                    "idempotent": {"type": "boolean"},
                    "proposal": _proposal_output_schema(),
                    "proposal_digest_sha256": _nullable_digest_schema(),
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id",
                    "revision", "state", "request_id", "idempotent",
                    "proposal", "proposal_digest_sha256", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_MISSION_GET,
            "title": "Dodging Infinity Mission read",
            "description": (
                "Read one Mission by id: current revision, state, the exact"
                " current proposal, revision and decision counts, and the"
                " active authorization id if the current revision is"
                " approved. Read-only; safe to retry."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mission_id": _token_schema(MISSION_ID_PATTERN),
                },
                "required": ["mission_id"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "problem": {"type": ["string", "null"]},
                    "mission_id": _nullable_token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": ["integer", "null"], "minimum": 1},
                    "state": {"type": ["string", "null"]},
                    "proposal": _proposal_output_schema(),
                    "proposal_digest_sha256": _nullable_digest_schema(),
                    "revision_count": {"type": "integer", "minimum": 0},
                    "decision_count": {"type": "integer", "minimum": 0},
                    "active_authorization_id": _nullable_token_schema(
                        AUTHORIZATION_ID_PATTERN
                    ),
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id",
                    "revision", "state", "proposal", "proposal_digest_sha256",
                    "revision_count", "decision_count",
                    "active_authorization_id", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_MISSION_EDIT,
            "title": "Dodging Infinity Mission edit",
            "description": (
                "Replace the proposal of a Mission with a new exact revision."
                " Pass the revision you last read as expected_revision; a"
                " stale value is refused. Editing an approved revision"
                " invalidates its authority and returns the Mission to"
                " awaiting decision."
            ),
            "inputSchema": {
                "type": "object",
                "properties": dict(
                    proposal_inputs,
                    mission_id=_token_schema(MISSION_ID_PATTERN),
                    expected_revision={"type": "integer", "minimum": 1},
                ),
                "required": ["mission_id", "expected_revision"]
                + list(PROPOSAL_INPUT_NAMES),
                "additionalProperties": False,
            },
            "outputSchema": _decision_output(
                {"invalidated_authorization_ids": _string_list_schema()},
                ("invalidated_authorization_ids",),
            ),
        },
        {
            "name": TOOL_MISSION_APPROVE,
            "title": "Dodging Infinity Mission approval",
            "description": (
                "Record the human's approval of exactly the revision passed,"
                " with exactly its requested action scope and delivery"
                " target. Issues a durable Mission Authorization and moves"
                " the Mission to AUTHORIZED. It dispatches nothing, runs"
                " nothing, and performs no Git action."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mission_id": _token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": "integer", "minimum": 1},
                },
                "required": ["mission_id", "revision"],
                "additionalProperties": False,
            },
            "outputSchema": _decision_output(
                {
                    "authorization_id": _nullable_token_schema(
                        AUTHORIZATION_ID_PATTERN
                    ),
                    "authorization_digest_sha256": _nullable_digest_schema(),
                    "authorized_action_scope": _nullable_string_list_schema(),
                    "authorized_delivery_targets": (
                        _nullable_string_list_schema()
                    ),
                    "authorization_live": {"type": ["boolean", "null"]},
                    "authorization_problem": {"type": ["string", "null"]},
                },
                ("authorization_id", "authorization_digest_sha256",
                 "authorized_action_scope", "authorized_delivery_targets",
                 "authorization_live", "authorization_problem"),
            ),
        },
        {
            "name": TOOL_MISSION_DENY,
            "title": "Dodging Infinity Mission denial",
            "description": (
                "Record the human's denial of exactly the revision passed."
                " Issues no authority; only a new revision and a new"
                " approval can proceed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mission_id": _token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": "integer", "minimum": 1},
                },
                "required": ["mission_id", "revision"],
                "additionalProperties": False,
            },
            "outputSchema": _decision_output({}, ()),
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
