"""The Model Context Protocol over the Grok Bot adapter: tools only.

Pure message handling, no socket and no state of its own: one JSON-RPC
message in, an HTTP status and at most one JSON-RPC message out
(``handle``). ``tools/list`` is exactly ``grok_bot.adapter.TOOLS``, and the
``run`` tool's per-command arguments are derived from
``grok_bot.adapter.RUN_ARGUMENTS`` (never restated here);
``tools/call`` is exactly ``GrokBotAdapter.call``, and its result is the
adapter's labelled result, unchanged. This module decides nothing: an
unknown tool or method is a protocol error, and everything else is the
adapter's (and so the local request surface's) answer. The ``tools/call``
branch is closed: a failure no refusal anticipated becomes a labelled
JSON-RPC internal error (-32603), never a dropped connection.

Revisions, kept apart from what the vendor documents:

- IMPLEMENTED AND TESTED here: exactly ``PROTOCOL_VERSIONS``, the
  initialization-based revisions 2025-11-25 and 2025-06-18, each negotiated
  in the loopback tests. JSON-RPC batching was removed in 2025-06-18, so a
  batch is refused under both. Nothing else is advertised:
  - 2025-03-26 is not, because it requires receiving batches and batch
    reception is deliberately not implemented;
  - the modern 2026-07-28 per-request revision is not either.
  ``initialize`` echoes an advertised revision and answers any other with
  the newest one. A client that cannot speak that must disconnect. A modern
  client that falls back to ``initialize`` is served.
- Grok Bot's connector documentation says Remote HTTPS MCP servers are
  supported. It names no MCP revision.
- The xAI API's remote-MCP page (a separate surface, not Grok Bot) says
  only Streaming HTTP and SSE transports are supported.
- The live Grok Bot client's revision is therefore UNKNOWN. It is a live
  compatibility dependency (``grok_bot.server.PUBLIC_REACHABILITY``), and
  the loopback tests prove protocol shape only.

Every reply body the transport writes is ASCII JSON. Tool results are
carried as text content holding ASCII-escaped JSON. For ``present`` and
``present_delivery`` they also carry, first and WHOLE, the human-facing
``display_text``: the complete proposal with nothing truncated or elided,
whose lone surrogates
and line boundaries the adapter already escaped. So no decoded string in a
reply is unencodable for a strict client.
"""

import json

from grok_bot import adapter as adapter_module

# Newest first. A requested revision outside this set is answered with
# the first one, as the initialization handshake specifies.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
SERVER_INFO = {"name": "dodging-infinity-grok-bot", "version": "1"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

LABELS = {"delivery_authority": adapter_module.DELIVERY_AUTHORITY,
          "evidence_status": adapter_module.EVIDENCE_STATUS,
          "transport": adapter_module.TRANSPORT}

INSTRUCTIONS = (
    "Dodging Infinity, through its Grok Bot transport. Send the human's plain"
    " request with request; the Codex Outer Operator authors a Mission"
    " proposal (or asks a question). Show the human the exact display_text"
    " from present. Before approving, the human ARMS it on the DI machine:"
    " run present's arming command, unchanged, in your local shell, where the"
    " human approves that exact command; it prints a one-time approval_code."
    " Only after the human sends a separate message containing"
    " only 'approved', call approve with the approval_binding from that"
    " present copied exactly, the reply, a relay_ref and that approval_code"
    " (delivery: present_delivery's arming, then approve_delivery the same"
    " way). Nothing reachable here can arm an approval. That approval is"
    " operator-attested, not cryptographically authenticated: DI does not"
    " establish who sent the reply. status, recover, cancel and run report"
    " and drive the request's own Mission from DI's durable records. Give"
    " request a conversation_ref that is private, unguessable and random (a"
    " long random value made for this conversation, kept to it, and never a"
    " visible or sequential identifier such as a chat or message id): if a"
    " request's reply is lost, cancel then takes that request's exact text"
    " and conversation_ref instead of its control_capability, and because the"
    " text is not secret, that reference is the only protection of that path."
    " An"
    " engineering approval never authorizes delivery. Delivery is a SEPARATE"
    " ceremony: present_delivery shows the complete delivery proposal, and"
    " only after the human's own separate 'approved' reply to THAT display"
    " does approve_delivery relay it; pr_delivery records an operator-attested"
    " delivery authorization. This transport performs no commit, push or pull"
    " request, and nothing here merges, tags, releases or deploys.")

_REF = {"type": "string", "description": "the lr- request_ref a request returned"}
_NULLABLE_REF = {"type": ["string", "null"],
                 "maxLength": adapter_module.MAX_REF_CHARS}
_TEXT = {"type": "string", "description": "the human's request, exactly",
         "maxLength": adapter_module.framing.MAX_TEXT_CHARS}
_CONVERSATION_REF = dict(_NULLABLE_REF, description=(
    "a private, unguessable, random reference made for this conversation and"
    " kept to it: a long random value, never a visible or sequential"
    " identifier such as a chat or message id. With it the adapter keeps a"
    " sealed way to cancel this request's pending proposal if the request's"
    " reply (and its control_capability) is lost. The request text is not"
    " secret, so this reference is that path's only protection: anyone who"
    " knows or guesses it could cancel the proposal. Without it nothing is"
    " kept"))
_APPROVAL_CODE = {"type": "string", "description": (
    "the one-time code the local arming command printed on the DI machine"
    " (its 32 lowercase hex characters); it fires exactly the binding armed,"
    " once")}
_OPERATOR_SESSION_ID = {"type": "null", "description": (
    "omit it: no Operator session is ever continued (every request is one"
    " fresh, read-only Operator turn), so any value is refused")}
TOOL_DEFINITIONS = {
    "request": (
        "Send the human's plain-text request to the Codex Outer Operator, which"
        " authors a Mission proposal or asks a clarifying question. Proposes"
        " only; approves and starts nothing. Every call is one fresh Operator"
        " session: to answer its question, send the whole request again with"
        " the answer in text.",
        {"text": _TEXT, "conversation_ref": _CONVERSATION_REF,
         "operator_session_id": _OPERATOR_SESSION_ID},
        ["text"]),
    "present": (
        "The exact proposal to show the human: display_text (show it whole) and"
        " approval_binding (copy it exactly into approve). Records what was"
        " displayed.",
        {"request_ref": _REF}, []),
    "approve": (
        "Relay the human's approval. Call ONLY after the human sent a separate"
        " message whose whole text is 'approved' in reply to the displayed"
        " proposal. Pass the approval_binding from present exactly as shown,"
        " that reply as relayed_reply, and a relay_ref naming the message. Any"
        " field that differs from what was displayed is refused, never"
        " corrected. approval_code is the one-time code the local arming"
        " command printed for exactly this binding; without it, or with a"
        " wrong or used one, nothing is approved. The approval is"
        " operator-attested, not cryptographically authenticated, and grants"
        " no delivery authority.",
        {"request_ref": _REF, "mission_id": {"type": "string"},
         "revision": {"type": "integer"},
         "proposal_digest_sha256": {"type": "string"},
         "approved_action_scope": {"type": "array", "items": {"type": "string"}},
         "approved_delivery_targets": {"type": "array",
                                       "items": {"type": "string"}},
         "expires_at": {"type": "integer"},
         "relayed_reply": {"type": "string"}, "relay_ref": {"type": "string"},
         "approval_code": _APPROVAL_CODE},
        list(adapter_module.APPROVE_ARGUMENTS)),
    "status": (
        "The request's durable status and its run, from DI's own records.",
        {"request_ref": _REF}, ["request_ref"]),
    "recover": (
        "Bind the Mission of a request whose proposal was interrupted.",
        {"request_ref": _REF}, ["request_ref"]),
    "cancel": (
        "Withdraw the caller's own pending proposal: pass the one-shot"
        " control_capability its request returned or, if that reply was lost,"
        " the request's exact text and conversation_ref instead (never both)."
        " Works only for a proposal still awaiting a decision.",
        {"request_ref": _REF, "control_capability": {"type": "string"},
         "text": _TEXT, "conversation_ref": _CONVERSATION_REF},
        ["request_ref"]),
}
# Exactly one way to prove control, never both (the adapter refuses both).
TOOL_SCHEMA_EXTRAS = {"cancel": {"oneOf": [
    {"required": ["control_capability"],
     "not": {"anyOf": [{"required": ["text"]},
                       {"required": ["conversation_ref"]}]}},
    {"required": ["text"], "not": {"required": ["control_capability"]}},
]}}

# The run tool's arguments, DERIVED from ``adapter.RUN_ARGUMENTS`` (the
# adapter's exact shape check) and from its nesting bound
# (``framing.MAX_NESTING_DEPTH``) each time the tools are listed: one source
# of truth. A kind the schema cannot state exactly is refused, never widened.
#
# The nesting bound. The adapter refuses a call whose lists and objects nest
# more than MAX_NESTING_DEPTH deep, counting the call's own object (depth 1)
# and its ``arguments`` object (depth 2). An argument value therefore has
# MAX_NESTING_DEPTH - 2 levels of room. ``json_depth_<n>`` below is any JSON
# value nesting at most n containers (``json_depth_0``: no container), so
# the schema admits exactly the depths the adapter does.
_RUN_ARGUMENT_FRAME_DEPTH = 2


def _json_depth_defs(room):
    defs = {"json_depth_0": {"type": ["string", "number", "boolean", "null"]}}
    for level in range(1, room + 1):
        inner = {"$ref": "#/$defs/json_depth_%d" % (level - 1)}
        defs["json_depth_%d" % level] = {"anyOf": [
            {"$ref": "#/$defs/json_depth_0"},
            {"type": "array", "items": inner},
            {"type": "object", "additionalProperties": inner}]}
    return defs


def _argument_room():
    room = adapter_module.framing.MAX_NESTING_DEPTH - _RUN_ARGUMENT_FRAME_DEPTH
    if room < 1:
        raise ValueError("the adapter's nesting bound leaves run arguments no"
                         " room the tool schema could state")
    return room


_RUN_ARGUMENT_NOTES = {
    ("dispatch", "workspace_path"): "operator recovery only: omit it. DI"
    " prepares the Mission's own isolated workspace. An operator recovering"
    " a dispatch may name that same, already prepared workspace (its absolute"
    " path on the DI machine); any other path is refused, and every check"
    " still applies",
    ("verify", "reported_result"): "the target's reported result (DI checks"
    " it against its own fresh reads)",
    ("prove", "operation"): "one existing Mission State proof seam",
    ("prove", "arguments"): "exactly that seam's own arguments",
}


def _run_argument_schema(command, name, kind, room):
    if kind is str:
        schema, prose = {"type": "string"}, "string"
    elif kind is dict:
        # The object itself takes one level of the room.
        schema = {"type": "object", "additionalProperties": {
            "$ref": "#/$defs/json_depth_%d" % (room - 1)}}
        prose = "object nesting at most %d deep" % room
    elif kind is None:
        schema = {"$ref": "#/$defs/json_depth_%d" % room}
        prose = "any JSON value nesting at most %d deep" % room
    else:
        raise ValueError("run %s argument %r has kind %r, which the tool"
                         " schema cannot state exactly" % (command, name, kind))
    note = _RUN_ARGUMENT_NOTES.get((command, name))
    if note:
        schema["description"] = note
    return schema, prose


def run_tool_definition():
    commands = adapter_module.surface_module.LocalRequestSurface.RUN_COMMANDS
    room = _argument_room()
    variants, prose = [], []
    for command in commands:
        expected = adapter_module.RUN_ARGUMENTS.get(command, {})
        optional = adapter_module.RUN_OPTIONAL_ARGUMENTS.get(command, {})
        described = dict((name, _run_argument_schema(command, name, kind, room))
                         for name, kind in sorted(expected.items()))
        extra = dict((name, _run_argument_schema(command, name, kind, room))
                     for name, kind in sorted(optional.items()))
        properties = dict((name, schema) for name, (schema, _) in
                          list(described.items()) + list(extra.items()))
        arguments = {
            # The adapter treats absent or null arguments as none.
            "type": "object" if expected else ["object", "null"],
            "properties": properties,
            "required": sorted(expected), "additionalProperties": False}
        variant = {"properties": {"command": {"const": command},
                                  "arguments": arguments}}
        if expected:
            variant["required"] = ["arguments"]
        variants.append(variant)
        prose.append("%s takes %s%s" % (command, ", ".join(
            "%s (%s)" % (name, text)
            for name, (_, text) in sorted(described.items())) or "no arguments",
            "".join(" (optional %s: %s)" % (name, schema.get("description", text))
                    for name, (schema, text) in sorted(extra.items()))))
    return (
        "One run command for the request's own AUTHORIZED Mission: dispatch,"
        " observe, reconcile, prove, verify, result, pause, resume or cancel."
        " An unknown outcome is a HOLD that only reconcile resolves. dispatch"
        " needs no path: DI prepares the Mission's own isolated workspace, so"
        " never ask the human for one. Its arguments object takes exactly:"
        " %s." % "; ".join(prose),
        {"request_ref": _REF,
         "command": {"type": "string", "enum": list(commands)},
         "arguments": {"type": ["object", "null"]}},
        ["request_ref", "command"],
        {"oneOf": variants, "$defs": _json_depth_defs(room)})


# Refused at import too: a kind added to RUN_ARGUMENTS that the schema
# cannot state stops the transport loading rather than listing a wider tool.
run_tool_definition()


_DELIVERY_KIND_SCHEMA = {
    "path": {"type": "string", "description": "an absolute path on the DI"
             " machine"},
    "text": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
}
TOOL_DEFINITIONS["present_delivery"] = (
    "Present the SEPARATE delivery of an engineered candidate: pr_delivery's"
    " present-dots reads the live repository and proposes exactly"
    " BASE_REFRESH, COMMIT, PUSH and PR_CREATE for it. Show the human the"
    " complete display_text; copy approval_binding into approve_delivery."
    " Proposes only; performs no delivery step (present-dots reads the live"
    " repository, including git ls-remote and possibly a fetch of the base"
    " branch from the configured remote). objective, architecture_notes and"
    " nonblocking_risks are literal text.",
    dict((name, dict(_DELIVERY_KIND_SCHEMA[kind]))
         for name, kind, _, _ in adapter_module.DELIVERY_PRESENT_FIELDS),
    [name for name, _, required, _ in adapter_module.DELIVERY_PRESENT_FIELDS
     if required])
TOOL_DEFINITIONS["approve_delivery"] = (
    "Relay the human's approval of a presented delivery. Call ONLY after the"
    " human sent a separate message whose whole text is 'approved' in reply to"
    " that delivery's display. Pass its approval_binding exactly, the reply,"
    " the reply's chat reference as reply_to, and a relay_ref. pr_delivery's"
    " attest-dots records an operator-attested (not cryptographically"
    " authenticated) delivery authorization after re-reading the live"
    " repository, including the configured remote; no delivery step is"
    " performed, and an"
    " engineering approval never stands in for this. The delivery must first"
    " be armed by present_delivery's arming command in the local shell on the"
    " DI machine; pass the approval_code it printed.",
    {"proposal_digest_sha256": {"type": "string"},
     "expires_at": {"type": "number"},
     "relayed_reply": {"type": "string"}, "reply_to": {"type": "string"},
     "relay_ref": {"type": "string"}, "approval_code": _APPROVAL_CODE},
    list(adapter_module.DELIVERY_APPROVAL_FIELDS))
TOOL_DEFINITIONS["delivery_status"] = (
    "A delivery's status from pr_delivery's durable record (read-only). The"
    " record is loaded first, then the repository it names is checked by its"
    " .git pointer files; no Git runs and no step is performed.",
    {"delivery_id": {"type": "string"}}, ["delivery_id"])


def tool_definitions():
    tools = []
    for name in sorted(adapter_module.TOOLS):
        if name == "run":
            description, properties, required, extras = run_tool_definition()
        else:
            description, properties, required = TOOL_DEFINITIONS[name]
            extras = TOOL_SCHEMA_EXTRAS.get(name, {})
        schema = {"type": "object", "properties": properties,
                  "required": required, "additionalProperties": False}
        schema.update(extras)
        tools.append({"name": name, "description": description,
                      "inputSchema": schema})
    return tools


def ascii_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def error(id_, code, message):
    """A JSON-RPC error, carrying the transport's labels as its data."""
    return {"jsonrpc": "2.0", "id": id_,
            "error": {"code": code, "message": message, "data": dict(LABELS)}}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def tool_result(name, result):
    content = []
    if name in ("present", "present_delivery") and result.get("ok"):
        content.append({"type": "text", "text": result["display_text"]})
    content.append({"type": "text", "text": ascii_json(result)})
    return {"content": content, "isError": not result.get("ok")}


def handle(adapter, message):
    """``(http_status, reply)``; ``reply`` is None for 202 Accepted."""
    if not isinstance(message, dict):
        return 400, error(None, INVALID_REQUEST,
                          "JSON-RPC batching was removed in 2025-06-18 and is"
                          " not part of the revisions this server speaks; send"
                          " one JSON-RPC object per POST")
    if message.get("jsonrpc") != "2.0":
        return 400, error(None, INVALID_REQUEST, "jsonrpc must be \"2.0\"")
    method = message.get("method")
    if method is None:
        if "id" in message and ("result" in message or "error" in message):
            return 202, None  # a response from the client: accepted
        return 400, error(None, INVALID_REQUEST, "no method")
    if not isinstance(method, str):
        return 400, error(None, INVALID_REQUEST, "method must be a string")
    if "id" not in message:
        return 202, None  # a notification: accepted, nothing to answer
    id_ = message["id"]
    if isinstance(id_, bool) or not isinstance(id_, (str, int)):
        return 400, error(None, INVALID_REQUEST, "id must be a string or integer")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return 200, error(id_, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return 200, _result(id_, {
            "protocolVersion": (requested if requested in PROTOCOL_VERSIONS
                                else PROTOCOL_VERSIONS[0]),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": dict(SERVER_INFO), "instructions": INSTRUCTIONS})
    if method == "ping":
        return 200, _result(id_, {})
    if method == "tools/list":
        return 200, _result(id_, {"tools": tool_definitions()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in adapter_module.TOOLS:
            return 200, error(id_, INVALID_PARAMS, "unknown tool %r; the tools"
                              " are %s" % (name, ", ".join(sorted(
                                  adapter_module.TOOLS))))
        arguments = params.get("arguments")
        # The ONE closed boundary of this transport. Every other branch
        # here only inspects a JSON-decoded value; this one runs DI. The
        # adapter answers every anticipated refusal with its own label, but
        # its guard ENUMERATES exception types, so a failure nothing
        # anticipated (S3-N1 was an OverflowError) is answered here: a
        # labelled internal error, never a dropped connection.
        try:
            reply = _result(id_, tool_result(name, adapter.call(
                name, {} if arguments is None else arguments)))
        except Exception as exc:
            return 200, error(id_, INTERNAL_ERROR, "the tool call failed"
                              " unexpectedly inside DI (%s); this reply cannot"
                              " say what, if anything, was recorded: DI's"
                              " durable records are authoritative, so read"
                              " status before retrying" % type(exc).__name__)
        return 200, reply
    return 200, error(id_, METHOD_NOT_FOUND, "method %r is not served here"
                      " (tools only)" % (method,))
