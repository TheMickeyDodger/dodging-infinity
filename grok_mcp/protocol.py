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
- The sixteen-tool table: the three original tools, the five bounded
  Mission tools (propose, get, edit, approve, deny), the client-mediated
  decision tools (the Mission decision and, since Task 8 S-VI, the
  delivery decision), the engineering engagement relay, the pure delivery
  status, and (Task 8 S-VII) the pure Mission status, the client-mediated
  Mission control (hold, resume, cancel), the attention pull and the
  client-mediated attention acknowledgment. Every client-mediated answer
  can arrive ONLY as the client's answer to a server-originated
  elicitation request and never as a tool argument. Every tool carries
  an exact ``inputSchema`` and ``outputSchema`` with
  ``additionalProperties: false``, an explicit ``required`` list, and
  explicit bounds. There is no shell tool, no run-command tool, no file
  tool, no capability, Git, delivery-effect, merge, release, or
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
    "Dodging Infinity exposes sixteen tools: di_status and di_ping are"
    " pure liveness/readiness checks; di_operator_turn sends one bounded"
    " text turn to the local operator and returns its reply. Continue"
    " an operator session by passing back the session_ref this server"
    " returned; repeat a turn's recorded result by passing back its"
    " turn_ref. The five di_mission_* tools propose, read, edit, approve"
    " and deny a Mission in the Dodging Infinity Mission registry; a"
    " decision binds the exact revision you pass, approval records"
    " authority and starts nothing, and github_pr names only the"
    " Mission's later delivery scope. Pass back the request_id a"
    " propose call returned to retry it safely. di_mission_decide asks"
    " the human, through this client's own elicitation form, to accept"
    " or decline exactly one revision; the accept is the human's form"
    " answer and cannot be supplied as an argument. di_mission_dispatch"
    " asks the Mission control layer to start the engineering engagement"
    " of an AUTHORIZED Mission; it refuses unless every precondition"
    " holds. di_delivery_decide asks the human, through the same"
    " elicitation form, to accept or decline the prepared pull-request"
    " delivery proposal of exactly one revision; di_delivery_status reads"
    " that delivery without changing anything. di_mission_status reads one"
    " Mission without changing anything. di_mission_control asks the human,"
    " through the same elicitation form, to confirm a hold, resume or"
    " cancel of exactly one revision. di_attention_pull lists what needs"
    " the human's attention; di_attention_ack asks the human to acknowledge"
    " one record. No merge, tag, release or deploy tool exists. No other"
    " references are accepted."
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
# The client-mediated decision tool (Task 8, slice S-I). Kept apart from
# MISSION_TOOL_NAMES: it is relayed by ``grok_mcp.decision_tools`` and
# needs an elicitation channel the plain Mission tools never touch.
TOOL_MISSION_DECIDE = "di_mission_decide"
# Task 8, slice S-VI: the client-mediated DELIVERY decision rides the same
# elicitation path (a distinct decision kind and reservation), so it is a
# decision tool too.
TOOL_DELIVERY_DECIDE = "di_delivery_decide"
DECISION_TOOL_NAMES = (TOOL_MISSION_DECIDE, TOOL_DELIVERY_DECIDE)
# The engineering engagement tool (Task 8, slice S-IV): relayed by
# ``grok_mcp.engagement_tools`` into the INJECTED Mission-control
# bootstrap; it refuses unless every precondition holds and, until the
# integrated candidate carries slice S-V's guards, always refuses with
# ``mission_dependency_missing`` and performs zero effects.
TOOL_MISSION_DISPATCH = "di_mission_dispatch"
ENGAGEMENT_TOOL_NAMES = (TOOL_MISSION_DISPATCH,)
# Task 8, slice S-VI: the PURE delivery status read, relayed into the
# injected Mission-control delivery desk.
TOOL_DELIVERY_STATUS = "di_delivery_status"
DELIVERY_TOOL_NAMES = (TOOL_DELIVERY_STATUS,)
# Task 8, slice S-VII: the READ-ONLY Mission status (relayed into the
# injected status reader), the elicited Mission control (hold, resume,
# cancel — the human's act, relayed into the injected control desk) and the
# attention pull / elicited acknowledgment (relayed into the injected
# attention desk).
TOOL_MISSION_STATUS = "di_mission_status"
STATUS_TOOL_NAMES = (TOOL_MISSION_STATUS,)
TOOL_MISSION_CONTROL = "di_mission_control"
CONTROL_TOOL_NAMES = (TOOL_MISSION_CONTROL,)
TOOL_ATTENTION_PULL = "di_attention_pull"
TOOL_ATTENTION_ACK = "di_attention_ack"
ATTENTION_TOOL_NAMES = (TOOL_ATTENTION_PULL, TOOL_ATTENTION_ACK)
# Every tool whose answer is the human's elicitation form answer: the
# server gives exactly these an event-stream channel.
ELICITED_TOOL_NAMES = DECISION_TOOL_NAMES + (TOOL_MISSION_CONTROL,
                                             TOOL_ATTENTION_ACK)
TOOL_NAMES = (
    (TOOL_STATUS, TOOL_PING, TOOL_OPERATOR_TURN) + MISSION_TOOL_NAMES
    + DECISION_TOOL_NAMES + ENGAGEMENT_TOOL_NAMES + DELIVERY_TOOL_NAMES
    + STATUS_TOOL_NAMES + CONTROL_TOOL_NAMES + ATTENTION_TOOL_NAMES
)
# The control a ``di_mission_control`` call names (the canonical operation
# is resolved by the Mission-control desk from the control record).
CONTROL_PATTERN = "^(hold|resume|cancel)$"
CONTROL_CHARS = 6
ATTENTION_ID_PATTERN = "^ca-[0-9a-f]{32}$"
ELICITATION_REQUEST_ID_PATTERN = "^(mo|ak)-[0-9a-f]{32}$"

# -- server-originated elicitation (MCP client feature) ---------------
METHOD_ELICITATION_CREATE = "elicitation/create"
ELICITATION_MODE_FORM = "form"
ELICITATION_ACTION_ACCEPT = "accept"
ELICITATION_ACTION_DECLINE = "decline"
ELICITATION_ACTION_CANCEL = "cancel"
ELICITATION_ACTIONS = (
    ELICITATION_ACTION_ACCEPT, ELICITATION_ACTION_DECLINE,
    ELICITATION_ACTION_CANCEL,
)
# The one form field the human answers, and the exact number of
# proposal-digest hex characters it must equal (the same twelve the local
# delivery ceremony asks a human to type).
ELICITATION_CONFIRM_FIELD = "confirm"
ELICITATION_CONFIRM_CHARS = 12
# Hard bound on the rendered authority card sent in ``message``. A card
# that would exceed it is REFUSED before anything is reserved; it is
# never truncated or partially rendered.
MAX_ELICITATION_MESSAGE_CHARS = 12000
# Protocol revisions that define elicitation at all (2025-03-26 does not).
ELICITATION_VERSIONS = ("2025-11-25", "2025-06-18")

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
# Task 8 S-VI: a delivery decision is reserved as a Mission state
# operation (``mo-``) and recorded as evidence (``mv-``).
STATE_OPERATION_ID_PATTERN = "^mo-[0-9a-f]{32}$"
EVIDENCE_ID_PATTERN = "^mv-[0-9a-f]{32}$"
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
    ) + _build_mission_tools() + _build_decision_tools() + _build_engagement_tools() + (
        _build_delivery_tools()) + _build_state_tools()


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
        # Task 8, slice S-IV: the ONE canonical approved-baseline field,
        # end to end — proposed here, stored in the revision, digested,
        # rendered on the elicitation card, returned with every read and
        # approval, and required before any engineering engagement.
        # Optional: absent means the Mission declares none (and cannot be
        # engaged until an EDIT declares one).
        "baseline": _baseline_schema(),
        # Task 8, slice S-VII: the proof contract and the verification argv
        # are proposal INPUTS on propose AND edit. Both are the Mission
        # Core's own optional proposal keys, validated strictly by the core
        # (closed keys, bounds, the mandatory integration obligations at
        # dispatch); nothing here interprets, runs or opens them.
        "proof_contract": {"type": ["object", "null"]},
        "verification": {"type": ["object", "null"]},
    }


def _baseline_schema():
    return {
        "type": ["object", "null"],
        "properties": {
            "ref": {"type": "string", "minLength": 1,
                    "maxLength": mission_record.MAX_BASELINE_REF_CHARS},
            "commit_sha": {"type": "string", "minLength": 40, "maxLength": 40,
                           "pattern": "^[0-9a-f]{40}$"},
        },
        "required": ["ref", "commit_sha"],
        "additionalProperties": False,
    }


PROPOSAL_INPUT_NAMES = tuple(mission_record.PROPOSAL_KEYS)
# The optional proposal inputs the relay passes through when present (the
# Mission Core's own optional proposal keys, all three since S-VII).
PROPOSAL_OPTIONAL_INPUT_NAMES = ("baseline", "proof_contract", "verification")


def _proposal_output_schema():
    """The proposal as the Mission Core RETURNS it, including the optional
    ``baseline``, ``proof_contract`` and ``verification`` keys a revision
    carries when it was proposed with them (tool INPUTS on propose and edit
    since Task 8 S-VII); ``required`` is the core's required keys only."""
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
                {"invalidated_authorization_ids": _string_list_schema(),
                 # Task 8, slice S-V: what the EDIT superseded (the
                 # activation, checkpoints, engagement workflows and the
                 # starts whose stop requirement it recorded), derived
                 # from durable state; null only for a non-EDIT outcome.
                 "superseded": {
                     "type": ["object", "null"],
                     "properties": {
                         "revision": {"type": "integer", "minimum": 1},
                         "activation_id": {"type": ["string", "null"]},
                         "checkpoints": {"type": "integer", "minimum": 0},
                         "engagements": _string_list_schema(),
                         "starts_stop_requested": {"type": "integer", "minimum": 0},
                         "recorded": {"type": "boolean"},
                     },
                     "required": ["revision", "activation_id", "checkpoints",
                                  "engagements", "starts_stop_requested",
                                  "recorded"],
                     "additionalProperties": False,
                 }},
                ("invalidated_authorization_ids", "superseded"),
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
                    # Task 8 S-IV: the exact baseline the approval binds
                    # (null when the approved revision declares none).
                    "baseline": _baseline_schema(),
                },
                ("authorization_id", "authorization_digest_sha256",
                 "authorized_action_scope", "authorized_delivery_targets",
                 "authorization_live", "authorization_problem", "baseline"),
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


def _build_decision_tools():
    return (
        {
            "name": TOOL_MISSION_DECIDE,
            "title": "Dodging Infinity Mission decision (client-mediated)",
            "description": (
                "Ask the human to decide exactly the revision passed. The"
                " server renders the complete authority-bearing proposal"
                " and sends it to THIS client as an elicitation form; the"
                " human's accept records the approval and its bounded"
                " expiry, decline records a denial, cancel records nothing."
                " The decision is the human's form answer: no argument"
                " can supply it. Requires a client that negotiated form"
                " elicitation and accepts an event-stream response."
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
                    "decision": {"type": ["string", "null"]},
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
                    "expires_at": {"type": ["integer", "null"], "minimum": 0},
                    "elicitation_outcome": {"type": ["string", "null"]},
                    # What the relay PROVED about the reserved decision id:
                    # true = a decision is recorded under it (applied, or
                    # found by readback), false = readback/refusal proved
                    # none, null = not applicable or not knowable.
                    "decision_recorded": {"type": ["boolean", "null"]},
                    # Whether the CURRENT authorization projection (live,
                    # problem, digest) was computed: "available", or
                    # "unavailable (<Class>)" when a recorded decision is
                    # proven but that projection could not be built.
                    "authorization_projection": {"type": ["string", "null"]},
                    # Task 8 S-IV: the exact baseline an ACCEPT bound (the
                    # one the card showed); null for any other outcome.
                    "baseline": _baseline_schema(),
                },
                ("decision", "authorization_id",
                 "authorization_digest_sha256", "authorized_action_scope",
                 "authorized_delivery_targets", "authorization_live",
                 "authorization_problem", "expires_at",
                 "elicitation_outcome", "decision_recorded",
                 "authorization_projection", "baseline"),
            ),
        },
        {
            "name": TOOL_DELIVERY_DECIDE,
            "title": "Dodging Infinity delivery decision (client-mediated)",
            "description": (
                "Ask the human to decide the prepared pull-request delivery"
                " proposal of exactly the Mission revision passed. The server"
                " renders the FULL proposal (every value the delivery"
                " authority will bind, its absolute expiry and the only"
                " allowed actions: base refresh, commit, push, pull-request"
                " creation) and sends it to THIS client as an elicitation"
                " form confirmed by the candidate identity prefix. Accept"
                " records the human's delivery decision as Mission evidence;"
                " decline records a sticky cancel request of the Mission;"
                " cancel records nothing. A Mission approval never authorizes"
                " delivery and this decision approves nothing else. Nothing"
                " here merges, tags, releases or deploys."
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
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "problem": {"type": ["string", "null"]},
                    "mission_id": _nullable_token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": ["integer", "null"], "minimum": 1},
                    # The reserved Mission state-operation id the decision
                    # is recorded under (reported unconsumed otherwise).
                    "delivery_decision_id": _nullable_token_schema(
                        STATE_OPERATION_ID_PATTERN),
                    "proposal_digest_sha256": _nullable_digest_schema(),
                    "candidate_identity_digest_sha256": _nullable_digest_schema(),
                    "decision": {"type": ["string", "null"]},
                    "evidence_id": _nullable_token_schema(EVIDENCE_ID_PATTERN),
                    "decision_document_digest_sha256": _nullable_digest_schema(),
                    "decision_recorded": {"type": ["boolean", "null"]},
                    "cancel_requested": {"type": ["boolean", "null"]},
                    "elicitation_outcome": {"type": ["string", "null"]},
                    "expires_at": {"type": ["integer", "null"], "minimum": 0},
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id",
                    "revision", "delivery_decision_id", "proposal_digest_sha256",
                    "candidate_identity_digest_sha256", "decision", "evidence_id",
                    "decision_document_digest_sha256", "decision_recorded",
                    "cancel_requested", "elicitation_outcome", "expires_at",
                    "call_ref",
                ],
                "additionalProperties": False,
            },
        },
    )


def _build_delivery_tools():
    """Task 8, slice S-VI: the PURE delivery status read."""
    return (
        {
            "name": TOOL_DELIVERY_STATUS,
            "title": "Dodging Infinity delivery status",
            "description": (
                "Read the pull-request delivery of one Mission: the prepared"
                " proposal, the human's delivery decision, the delivery"
                " authorization's source, each step's receipt and whether the"
                " Mission attested it, the pull-request URL, what is"
                " uncertain and the next action. Read-only: it prepares,"
                " records and performs nothing; safe to retry."
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
                    "mission_state": {"type": ["string", "null"]},
                    "progress": {"type": ["string", "null"]},
                    "delivery_requested": {"type": ["boolean", "null"]},
                    "cancel_requested": {"type": ["boolean", "null"]},
                    "proposal": {"type": ["object", "null"]},
                    # The recorded source-branch preparation state (never a
                    # Git read).
                    "preparation": {"type": ["object", "null"]},
                    "decision": {"type": ["object", "null"]},
                    "delivery": {"type": ["object", "null"]},
                    "uncertainty": _string_list_schema(),
                    "next_action": {"type": ["string", "null"]},
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id",
                    "revision", "mission_state", "progress",
                    "delivery_requested", "cancel_requested", "proposal",
                    "preparation", "decision", "delivery", "uncertainty",
                    "next_action", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
    )


def _object_list_schema():
    return {"type": "array", "items": {"type": "object"}}


def _build_state_tools():
    """Task 8, slice S-VII: the read-only Mission status, the elicited
    Mission control, and the attention pull and elicited acknowledgment."""
    return (
        {
            "name": TOOL_MISSION_STATUS,
            "title": "Dodging Infinity Mission status",
            "description": (
                "Read one Mission: where it is (state, revision, progress,"
                " workflow and task), what holds it (blockers, proof,"
                " readiness, controls, pending decisions), what the Reviewer"
                " said and the candidate and delivery facts the Runtime's"
                " latest reconciliation recorded (with their standing and"
                " freshness), the evidence and artifacts, and its live"
                " attention. Read-only: it writes, prepares, reconciles and"
                " waits for nothing, consults no engine or model and takes no"
                " lock; safe to retry while work is running."
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
                    # The canonical Mission part (one Mission snapshot).
                    "canonical": {"type": ["object", "null"]},
                    "stores": {"type": ["object", "null"]},
                    "reconciliation": {"type": ["object", "null"]},
                    "workflows": {"type": ["object", "null"]},
                    "attention": {"type": ["object", "null"]},
                    "limitations": _string_list_schema(),
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id", "canonical",
                    "stores", "reconciliation", "workflows", "attention",
                    "limitations", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_MISSION_CONTROL,
            "title": "Dodging Infinity Mission control (client-mediated)",
            "description": (
                "Ask the human to confirm one control of exactly the Mission"
                " revision passed: hold (every Dodging Infinity effect stops at"
                " its gate until resumed; a running engineering session is not"
                " paused), resume (lifts the hold and starts nothing; every"
                " later step re-validates), or cancel (a sticky request the"
                " Runtime acts on by stopping what it started; once the stops"
                " are confirmed by observed absence, the same control asks the"
                " human to CONFIRM the cancel, which closes the Mission). The"
                " server renders the exact control and its limits and sends it"
                " to THIS client as an elicitation form; only the human's accept"
                " applies it. Nothing already completed is undone. Requires a"
                " client that negotiated form elicitation and accepts an"
                " event-stream response."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mission_id": _token_schema(MISSION_ID_PATTERN),
                    "revision": {"type": "integer", "minimum": 1},
                    "control": {"type": "string", "minLength": 4,
                                "maxLength": CONTROL_CHARS,
                                "pattern": CONTROL_PATTERN},
                },
                "required": ["mission_id", "revision", "control"],
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
                    "control": {"type": ["string", "null"]},
                    "operation": {"type": ["string", "null"]},
                    "operation_id": _nullable_token_schema(
                        STATE_OPERATION_ID_PATTERN),
                    "elicitation_outcome": {"type": ["string", "null"]},
                    "control_recorded": {"type": ["boolean", "null"]},
                    "controls": {"type": ["object", "null"]},
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id", "revision",
                    "control", "operation", "operation_id",
                    "elicitation_outcome", "control_recorded", "controls",
                    "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_ATTENTION_PULL,
            "title": "Dodging Infinity attention pull",
            "description": (
                "Pull what needs the human's attention for this client: every"
                " pending attention record is surfaced into THIS result;"
                " records surfaced by an earlier pull whose receipt is not yet"
                " acknowledged are listed again under surfaced, never"
                " duplicated. It records only that each record was included in"
                " this result; it authorizes and changes nothing else."
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
                    "status": {"type": "string"},
                    "problem": {"type": ["string", "null"]},
                    "surfaced_now": _object_list_schema(),
                    "surfaced": _object_list_schema(),
                    "acknowledged": _object_list_schema(),
                    "pending": _object_list_schema(),
                    "not_surfaced": _object_list_schema(),
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "surfaced_now",
                    "surfaced", "acknowledged", "pending", "not_surfaced",
                    "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": TOOL_ATTENTION_ACK,
            "title": "Dodging Infinity attention acknowledgment (client-mediated)",
            "description": (
                "Ask the human to acknowledge one attention record through this"
                " client's elicitation form. Only the human's accept records the"
                " acknowledgment; it authorizes, resolves and starts nothing."
                " Requires a client that negotiated form elicitation and accepts"
                " an event-stream response."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "attention_id": _token_schema(ATTENTION_ID_PATTERN),
                },
                "required": ["attention_id"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "reason": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "problem": {"type": ["string", "null"]},
                    "attention_id": _nullable_token_schema(ATTENTION_ID_PATTERN),
                    "mission_id": _nullable_token_schema(MISSION_ID_PATTERN),
                    "request_id": _nullable_token_schema(
                        ELICITATION_REQUEST_ID_PATTERN),
                    "elicitation_outcome": {"type": ["string", "null"]},
                    "acknowledged": {"type": ["boolean", "null"]},
                    "attention": {"type": ["object", "null"]},
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "attention_id",
                    "mission_id", "request_id", "elicitation_outcome",
                    "acknowledged", "attention", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
    )


def _build_engagement_tools():
    """Task 8, slice S-IV: the engineering engagement tool. Its only input
    is the Mission id; every precondition is re-read by the Mission
    control layer at call time and nothing here carries authority."""
    return (
        {
            "name": TOOL_MISSION_DISPATCH,
            "title": "Dodging Infinity Mission engineering engagement",
            "description": (
                "Start the engineering engagement of exactly one AUTHORIZED"
                " Mission: the Mission control layer re-reads the live"
                " authorization, its provenance, the proof contract's"
                " mandatory obligations, readiness and budget, records the"
                " canonical engagement reservation and publishes exactly one"
                " workflow row for the Runtime. Refuses with an exact problem"
                " code when any precondition fails; repeating the call for"
                " the same Mission is idempotent. Runs nothing itself."
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
                    "workflow_id": {"type": ["string", "null"], "minLength": 1,
                                    "maxLength": 128},
                    "engagement_id": {"type": ["string", "null"], "minLength": 35,
                                      "maxLength": 35},
                    "idempotent": {"type": "boolean"},
                    "missing_guards": _string_list_schema(),
                    "call_ref": _ref_schema(),
                },
                "required": [
                    "ok", "reason", "status", "problem", "mission_id",
                    "revision", "workflow_id", "engagement_id", "idempotent",
                    "missing_guards", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
    )


def form_elicitation_negotiated(protocol_version, capabilities):
    """Whether the client declared FORM-mode elicitation for the
    negotiated protocol revision; False for anything else, including an
    absent, malformed or URL-only declaration.

    Rules, per revision (from the MCP specification, "Client Features >
    Elicitation > Capabilities", and "Base Protocol > Lifecycle"):

    - 2025-11-25: the ``elicitation`` capability object may carry the
      sub-capabilities ``form`` and ``url``, each an object, naming the
      modes the client supports. The specification keeps an EMPTY
      ``elicitation`` object meaningful for backwards compatibility with
      2025-06-18 clients, which had form mode only: an empty object is
      read as form support. An object that names ONLY ``url`` is a
      URL-only client and is NOT form-capable here.
    - 2025-06-18: elicitation has a single (form) mode and the capability
      is declared as ``"elicitation": {}``; any object value declares it.
    - 2025-03-26: elicitation does not exist; never form-capable.
    """
    if protocol_version not in ELICITATION_VERSIONS:
        return False
    if not isinstance(capabilities, dict):
        return False
    declared = capabilities.get("elicitation")
    if not isinstance(declared, dict):
        return False
    if protocol_version == "2025-06-18":
        return True
    if not declared:
        return True
    return isinstance(declared.get(ELICITATION_MODE_FORM), dict)


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
