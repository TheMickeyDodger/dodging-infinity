"""Focused tests for the Grok Bot MCP interaction spike (``grok_mcp``).

The protocol proof is a REAL MCP client fixture (``urllib``) speaking
real JSON-RPC over real HTTP to a real ``ThreadingHTTPServer`` bound to
``127.0.0.1:0`` in a daemon thread, through the provider-side
``HumanInteractionAdapter`` implementation, across the neutral seam,
into an injected ``FunctionOperatorSession``, and back out as a
structured MCP tool result. Nothing on the route is mocked.

Sections mirror the Lead plan's test matrix:
A protocol over the wire, B interaction across the seam, C identity,
D idempotency, E authority/security, F regression.
"""

import ast
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import tokenize
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from human_interaction import (  # noqa: E402
    EVENT_MESSAGE,
    HumanInteractionAdapter,
    InteractionEvent,
    ReceiveOutcome,
    SendOutcome,
)
from operator_session import FunctionOperatorSession  # noqa: E402

import grok_mcp  # noqa: E402
from grok_mcp import adapter as adapter_module  # noqa: E402
from grok_mcp import cli as cli_module  # noqa: E402
from grok_mcp import config as config_module  # noqa: E402
from grok_mcp import controller as controller_module  # noqa: E402
from grok_mcp import protocol  # noqa: E402
from grok_mcp import server as server_module  # noqa: E402

TOKEN = "test-bearer-token"
REPOSITORY = "/repo/example"
REF_RE = re.compile(r"^di-[0-9a-f]{32}$")

GROK_MCP_FILES = sorted((REPO_ROOT / "grok_mcp").glob("*.py"))
FORBIDDEN_IMPORT_ROOTS = (
    "telegram_operator", "codex_gateway", "workflow_authority",
    "target_runtime", "pr_delivery", "capability", "worker",
    "durable_execution", "herdr", "herdctl", "subprocess", "shutil",
    "tempfile",
)
# Names that would make DI depend on a Grok-private identity.
GROK_PRIVATE_ID_NAMES = (
    "conversation_id", "message_id", "user_id", "thread_id", "chat_id",
    "grok_conversation_id", "grok_message_id", "grok_user_id",
)


# --------------------------------------------------------------------
# Fakes for the operator seam (real FunctionOperatorSession, injected
# callables).
# --------------------------------------------------------------------


class FakeRequest(object):
    def __init__(self, request_id, text, repository, session_id, source):
        self.request_id = request_id
        self.text = text
        self.repository = repository
        self.session_id = session_id
        self.source = source


class FakeResult(object):
    def __init__(self, request_id, session_id, status, message, error=None):
        self.request_id = request_id
        self.session_id = session_id
        self.status = status
        self.message = message
        self.error = error


class RecordingOperator(object):
    """Records every prepare/execute crossing; replies deterministically."""

    def __init__(self, reply="operator reply", status="completed",
                 session_id="provider-session-1", session_ids=None):
        self.built = []
        self.submitted = []
        self.reply = reply
        self.status = status
        self.session_id = session_id
        # Optional per-turn session ids (an iterator); wins over
        # ``session_id`` while it lasts.
        self.session_ids = iter(session_ids) if session_ids else None
        self.raise_on_submit = None

    def build_request(self, text, repository, session_id=None,
                      source="terminal"):
        request = FakeRequest(
            "req-%d" % (len(self.built) + 1), text, repository,
            session_id, source,
        )
        self.built.append(request)
        return request

    def submit(self, request):
        self.submitted.append(request)
        if self.raise_on_submit is not None:
            raise self.raise_on_submit
        session_id = self.session_id
        if self.session_ids is not None:
            session_id = next(self.session_ids, self.session_id)
        return FakeResult(
            request.request_id, session_id, self.status, self.reply
        )

    @property
    def calls(self):
        return len(self.submitted)

    def session(self):
        return FunctionOperatorSession(self.build_request, self.submit)


def deterministic_refs():
    counter = {"n": 0}

    def mint():
        counter["n"] += 1
        return "di-" + ("%032x" % counter["n"])

    return mint


def make_controller(operator=None, **kwargs):
    operator = operator or RecordingOperator()
    controller = controller_module.GrokMcpController(
        operator.session(), REPOSITORY, mint_ref=deterministic_refs(),
        **kwargs
    )
    return controller, operator


# --------------------------------------------------------------------
# Real HTTP fixture: a real server in a daemon thread, a real urllib
# MCP client.
# --------------------------------------------------------------------


def parse_body(body):
    """JSON when the server sent JSON; otherwise the plain text."""
    if not body:
        return None
    text = body.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def http_request(method, path, body, extra_headers, declared_length=None):
    """A raw HTTP/1.1 request; ``declared_length`` overrides Content-Length."""
    length = len(body) if declared_length is None else declared_length
    lines = [
        "%s %s HTTP/1.1" % (method, path), "Host: 127.0.0.1",
        "Content-Type: application/json",
        "Accept: application/json, text/event-stream",
        "Content-Length: %s" % length,
    ] + list(extra_headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def read_http_response(sock):
    """(status line, lower-cased headers, body) from one raw socket."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            return data.decode("iso-8859-1"), {}, b""
        data += chunk
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    while len(body) < length:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return lines[0], headers, body[:length]


INITIALIZE_BODY = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-11-25",
               "capabilities": {}, "clientInfo": {"name": "raw"}},
}).encode("utf-8")


class McpClient(object):
    """A minimal Streamable HTTP MCP client over urllib."""

    def __init__(self, port, path="/mcp", token=TOKEN):
        self.url = "http://127.0.0.1:%d%s" % (port, path)
        self.token = token
        self.session_id = None
        self.protocol_version = None
        self.next_id = 1

    def raw(self, body, headers=None, method="POST", omit=()):
        base = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token is not None:
            base["Authorization"] = "Bearer " + self.token
        if self.session_id is not None:
            base["Mcp-Session-Id"] = self.session_id
        if self.protocol_version is not None:
            base["MCP-Protocol-Version"] = self.protocol_version
        base.update(headers or {})
        for name in omit:
            base.pop(name, None)
        data = body if isinstance(body, bytes) else (
            None if body is None else json.dumps(body).encode("utf-8")
        )
        request = urllib.request.Request(
            self.url, data=data, headers=base, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def rpc(self, method, params=None, **kwargs):
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        self.next_id += 1
        if params is not None:
            payload["params"] = params
        status, headers, body = self.raw(payload, **kwargs)
        return status, headers, parse_body(body)

    def initialize(self, version="2025-11-25"):
        status, headers, body = self.rpc("initialize", {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"},
        })
        if status == 200 and "result" in body:
            self.session_id = {
                k.lower(): v for k, v in headers.items()
            }.get("mcp-session-id")
            self.protocol_version = body["result"]["protocolVersion"]
            self.raw({"jsonrpc": "2.0",
                      "method": "notifications/initialized"})
        return status, headers, body

    def call(self, name, arguments=None):
        status, headers, body = self.rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        return status, body


class ServerFixture(unittest.TestCase):
    """Starts a real server per test and proves it leaves nothing behind."""

    def setUp(self):
        self.threads_before = threading.active_count()
        self.logs = []

    def serve(self, controller, token=TOKEN, origins=(), path="/mcp"):
        server = server_module.GrokMcpServer(
            ("127.0.0.1", 0), controller, bearer_token=token,
            endpoint_path=path, allowed_origins=origins,
            log_writer=self.logs.append,
        )
        self.server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.assert_no_stray_threads)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return McpClient(server.server_address[1], path=path, token=token)

    def assert_no_stray_threads(self):
        self.assertEqual(threading.active_count(), self.threads_before)

    def ready_client(self, operator=None, **kwargs):
        controller, operator = make_controller(operator)
        client = self.serve(controller, **kwargs)
        status, headers, body = client.initialize()
        self.assertEqual(status, 200, body)
        return client, controller, operator


def expected_tools():
    """The EXACT expected tool table (plan A5)."""
    ref = {"type": "string", "minLength": 35, "maxLength": 35,
           "pattern": "^di-[0-9a-f]{32}$"}
    nullable_ref = {"type": ["string", "null"], "minLength": 35,
                    "maxLength": 35, "pattern": "^di-[0-9a-f]{32}$"}
    return [
        {
            "name": "di_status",
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
                        "properties": {
                            "max_turn_text_chars": {"type": "integer"},
                            "max_echo_chars": {"type": "integer"},
                            "max_request_bytes": {"type": "integer"},
                            "max_replay_entries": {"type": "integer"},
                            "max_session_entries": {"type": "integer"},
                            "max_message_chars": {"type": "integer"},
                            "max_message_chunks": {"type": "integer"},
                        },
                        "required": [
                            "max_turn_text_chars", "max_echo_chars",
                            "max_request_bytes", "max_replay_entries",
                            "max_session_entries", "max_message_chars",
                            "max_message_chunks",
                        ],
                        "additionalProperties": False,
                    },
                    "call_ref": ref,
                },
                "required": [
                    "ok", "reason", "ready", "contract_version",
                    "protocol_versions", "tools", "bounds", "call_ref",
                ],
                "additionalProperties": False,
            },
        },
        {
            "name": "di_ping",
            "title": "Dodging Infinity ping",
            "description": (
                "Liveness check. Optionally echoes a short string back."
                " Invokes no operator; safe to retry."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "echo": {"type": "string", "minLength": 0,
                             "maxLength": 200},
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
                    "echo": {"type": ["string", "null"], "maxLength": 200},
                    "call_ref": ref,
                },
                "required": ["ok", "reason", "pong", "echo", "call_ref"],
                "additionalProperties": False,
            },
        },
        {
            "name": "di_operator_turn",
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
                             "maxLength": 4000},
                    "session_ref": ref,
                    "turn_ref": ref,
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
                    "session_ref": nullable_ref,
                    "turn_ref": ref,
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
    ]


def conforms(schema, value):
    """Independent (test-side) conformance check for the schema subset."""
    def type_ok(kind, item):
        return {
            "string": lambda v: isinstance(v, str),
            "boolean": lambda v: isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "null": lambda v: v is None,
        }[kind](item)

    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(type_ok(k, value) for k in kinds):
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return False
        if len(value) > schema.get("maxLength", len(value)):
            return False
        if "pattern" in schema and not re.match(schema["pattern"], value):
            return False
    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value):
            return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                return False
        for key, item in value.items():
            if key in props:
                if not conforms(props[key], item):
                    return False
            elif schema.get("additionalProperties", True) is False:
                return False
    if isinstance(value, list) and "items" in schema:
        return all(conforms(schema["items"], item) for item in value)
    return True


# ====================================================================
# A. MCP protocol over the real wire
# ====================================================================


class AProtocolOverTheWireTests(ServerFixture):

    def test_A1_initialize_returns_result_and_visible_ascii_session_id(self):
        controller, _ = make_controller()
        client = self.serve(controller)
        status, headers, body = client.initialize()
        self.assertEqual(status, 200, body)
        result = body["result"]
        self.assertEqual(result["protocolVersion"], "2025-11-25")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], protocol.SERVER_NAME)
        self.assertTrue(client.session_id)
        self.assertTrue(all(0x21 <= ord(c) <= 0x7E for c in client.session_id))

    def test_A2_initialized_notification_is_202_with_empty_body(self):
        client, _, _ = self.ready_client()
        status, headers, body = client.raw(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_A3_ping_returns_empty_result_object(self):
        client, _, _ = self.ready_client()
        status, headers, body = client.rpc("ping")
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], {})

    def test_A4_tools_list_enumerates_exactly_three_tools(self):
        client, _, _ = self.ready_client()
        status, headers, body = client.rpc("tools/list")
        self.assertEqual(status, 200)
        names = [tool["name"] for tool in body["result"]["tools"]]
        self.assertEqual(names, ["di_status", "di_ping", "di_operator_turn"])
        self.assertNotIn("nextCursor", body["result"])

    def test_A5_schemas_are_exact(self):
        client, _, _ = self.ready_client()
        status, headers, body = client.rpc("tools/list")
        self.assertEqual(body["result"]["tools"], expected_tools())
        for tool in body["result"]["tools"]:
            for key in ("inputSchema", "outputSchema"):
                self.assertIs(tool[key]["additionalProperties"], False)
                self.assertIsInstance(tool[key]["required"], list)

    def test_A6_valid_operator_turn_succeeds_with_structured_content(self):
        client, controller, operator = self.ready_client(
            RecordingOperator(reply="hello from the operator")
        )
        status, body = client.call("di_operator_turn", {"text": "status?"})
        self.assertEqual(status, 200, body)
        result = body["result"]
        self.assertFalse(result.get("isError", False))
        structured = result["structuredContent"]
        schema = expected_tools()[2]["outputSchema"]
        self.assertTrue(conforms(schema, structured), structured)
        self.assertEqual(structured["message"], "hello from the operator")
        self.assertEqual(structured["status"], "completed")
        self.assertTrue(structured["ok"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), structured)
        self.assertEqual(operator.calls, 1)

    def test_A7_bounded_input_refusals_are_tool_errors_that_never_run(self):
        client, controller, operator = self.ready_client()
        schema = expected_tools()[2]["outputSchema"]
        cases = [
            ("over-long text", {"text": "x" * 4001}),
            ("empty text", {"text": ""}),
            ("wrong type", {"text": 12}),
            ("unknown property", {"text": "hi", "extra": 1}),
            ("unknown session_ref",
             {"text": "hi", "session_ref": "di-" + "f" * 32}),
            ("unknown turn_ref",
             {"text": "hi", "turn_ref": "di-" + "e" * 32}),
            ("malformed session_ref",
             {"text": "hi", "session_ref": "not-a-ref"}),
        ]
        for label, arguments in cases:
            status, body = client.call("di_operator_turn", arguments)
            self.assertEqual(status, 200, (label, body))
            result = body["result"]
            self.assertIs(result["isError"], True, label)
            structured = result["structuredContent"]
            self.assertTrue(conforms(schema, structured), (label, structured))
            self.assertFalse(structured["ok"], label)
            self.assertEqual(structured["status"], "refused", label)
            self.assertTrue(structured["reason"], label)
            self.assertIsNone(structured["session_ref"], label)
            self.assertEqual(operator.calls, 0, label)
            self.assertEqual(len(operator.built), 0, label)
        status, body = client.call("di_ping", {"echo": "y" * 201})
        self.assertIs(body["result"]["isError"], True)
        self.assertTrue(
            conforms(expected_tools()[1]["outputSchema"],
                     body["result"]["structuredContent"])
        )
        status, body = client.call("di_status", {"anything": True})
        self.assertIs(body["result"]["isError"], True)
        self.assertTrue(
            conforms(expected_tools()[0]["outputSchema"],
                     body["result"]["structuredContent"])
        )
        self.assertEqual(operator.calls, 0)

    def test_A8_malformed_protocol_requests_fail_safely(self):
        client, controller, operator = self.ready_client()
        # bad JSON -> -32700
        status, headers, body = client.raw(b"{not json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)
        # non-object / bad id -> -32600
        status, headers, body = client.raw([1, 2, 3])
        self.assertEqual(json.loads(body)["error"]["code"], -32600)
        status, headers, body = client.raw(
            {"jsonrpc": "2.0", "id": {"x": 1}, "method": "ping"}
        )
        self.assertEqual(json.loads(body)["error"]["code"], -32600)
        status, headers, body = client.raw(
            {"jsonrpc": "1.0", "id": 1, "method": "ping"}
        )
        self.assertEqual(json.loads(body)["error"]["code"], -32600)
        # unknown method -> -32601
        status, headers, body = client.rpc("resources/list")
        self.assertEqual(body["error"]["code"], -32601)
        # unknown tool / non-object params -> -32602
        status, headers, body = client.rpc(
            "tools/call", {"name": "di_shell", "arguments": {}}
        )
        self.assertEqual(body["error"]["code"], -32602)
        status, headers, body = client.rpc("tools/call", [1])
        self.assertEqual(body["error"]["code"], -32602)
        status, headers, body = client.rpc(
            "tools/call", {"name": "di_ping", "arguments": [1]}
        )
        self.assertEqual(body["error"]["code"], -32602)
        # oversize body -> 413
        big = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping",
             "params": {"pad": "x" * server_module.MAX_REQUEST_BYTES}}
        ).encode("utf-8")
        status, headers, body = client.raw(big)
        self.assertEqual(status, 413)
        # missing Accept -> 406
        status, headers, body = client.rpc("ping", omit=("Accept",))
        self.assertEqual(status, 406)
        # GET -> 405
        status, headers, body = client.raw(None, method="GET")
        self.assertEqual(status, 405)
        # bad protocol version header -> 400
        status, headers, body = client.rpc(
            "ping", headers={"MCP-Protocol-Version": "1999-01-01"}
        )
        self.assertEqual(status, 400)
        # unknown session -> 404
        status, headers, body = client.rpc(
            "ping", headers={"Mcp-Session-Id": "nope"}
        )
        self.assertEqual(status, 404)
        # missing session header -> 400
        status, headers, body = client.rpc("ping", omit=("Mcp-Session-Id",))
        self.assertEqual(status, 400)
        # wrong path -> 404
        status, headers, body = McpClient(
            int(client.url.rsplit(":", 1)[1].split("/")[0]), path="/other"
        ).raw({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(status, 404)
        # DELETE drops the session; a second DELETE is 404
        status, headers, body = client.raw(None, method="DELETE")
        self.assertEqual(status, 200)
        status, headers, body = client.raw(None, method="DELETE")
        self.assertEqual(status, 404)
        self.assertEqual(operator.calls, 0)

    def test_A8b_refused_post_drains_its_body_so_keep_alive_retry_works(
        self
    ):
        """Two POSTs on ONE HTTP/1.1 socket: a refused request WITH a
        body, then a legitimate one. The second must get a normal
        JSON-RPC response, and no refused body may echo back. urllib
        opens a fresh connection per call, so this needs a raw socket."""
        controller, operator = make_controller()
        client = self.serve(controller, origins=("https://grok.example",))
        port = int(client.url.rsplit(":", 1)[1].split("/")[0])
        initialize = INITIALIZE_BODY
        marker = b"REFUSED-BODY-MARKER"
        refused_body = json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "method": "initialize",
                                   "params": {"pad": marker.decode()}}
                                  ).encode("utf-8")

        def request(path, body, extra_headers):
            return http_request("POST", path, body, extra_headers)

        read_response = read_http_response

        refusals = (
            ("401", "/mcp", ["Authorization: Bearer wrong"]),
            ("403", "/mcp", ["Authorization: Bearer " + TOKEN,
                             "Origin: https://evil.example"]),
            ("404", "/other", ["Authorization: Bearer " + TOKEN]),
        )
        for expected, path, headers in refusals:
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                sock.sendall(request(path, refused_body, headers))
                status_line, resp_headers, body = read_response(sock)
                self.assertIn(" %s " % expected, status_line, status_line)
                self.assertNotIn(marker, body)
                sock.sendall(request(
                    "/mcp", initialize, ["Authorization: Bearer " + TOKEN]
                ))
                status_line, resp_headers, body = read_response(sock)
                self.assertIn(" 200 ", status_line, (expected, status_line))
                self.assertNotIn(marker, body)
                parsed = json.loads(body.decode("utf-8"))
                self.assertEqual(parsed["result"]["protocolVersion"],
                                 "2025-11-25")
                self.assertTrue(resp_headers.get("mcp-session-id"))
            finally:
                sock.close()
        # An oversize body cannot be drained; the server must close the
        # connection rather than parse the tail as a request.
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            oversize = b"x" * (server_module.MAX_REQUEST_BYTES + 1)
            sock.sendall(request("/mcp", oversize,
                                 ["Authorization: Bearer " + TOKEN]))
            status_line, resp_headers, body = read_response(sock)
            self.assertIn(" 413 ", status_line, status_line)
            self.assertEqual(resp_headers.get("connection"), "close")
        finally:
            sock.close()
        self.assertEqual(operator.calls, 0)

    def test_A8c_delete_drains_or_closes_on_one_socket(self):
        """The DELETE path must honour the drain result like every other
        reply: a drainable body leaves the connection usable; an oversize
        or non-integer Content-Length closes it, and the refused body
        never echoes back."""
        client, controller, operator = self.ready_client()
        port = int(client.url.rsplit(":", 1)[1].split("/")[0])
        marker = b"ZZZZZZZZZZZZZZZZ"
        auth = "Authorization: Bearer " + TOKEN
        # (a) known session, ordinary body: 200, drained, then a fresh
        # initialize on the SAME socket succeeds.
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            sock.sendall(http_request(
                "DELETE", "/mcp", marker,
                [auth, "Mcp-Session-Id: " + client.session_id],
            ))
            status_line, headers, body = read_http_response(sock)
            self.assertIn(" 200 ", status_line, status_line)
            sock.sendall(http_request("POST", "/mcp", INITIALIZE_BODY, [auth]))
            status_line, headers, body = read_http_response(sock)
            self.assertIn(" 200 ", status_line, status_line)
            self.assertNotIn(marker, body)
            self.assertIn("protocolVersion", body.decode("utf-8"))
        finally:
            sock.close()
        # (b) oversize declared length, (c) non-integer length: the
        # server must close rather than leave the tail in the socket.
        for label, declared, session_header, expected in (
            ("oversize", server_module.MAX_REQUEST_BYTES + 1,
             "Mcp-Session-Id: nope", " 404 "),
            ("non-integer", "abc", "Mcp-Session-Id: nope", " 404 "),
            ("oversize-no-session", server_module.MAX_REQUEST_BYTES + 1,
             None, " 400 "),
        ):
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                extra = [auth] + ([session_header] if session_header else [])
                sock.sendall(http_request(
                    "DELETE", "/mcp", marker, extra, declared_length=declared
                ))
                status_line, headers, body = read_http_response(sock)
                self.assertIn(expected, status_line, (label, status_line))
                self.assertEqual(headers.get("connection"), "close", label)
                self.assertNotIn(marker, body, label)
                # The server closed: a further request gets EOF, never
                # a parse of the leftover body.
                sock.sendall(http_request("POST", "/mcp", INITIALIZE_BODY,
                                          [auth]))
                status_line, headers, body = read_http_response(sock)
                self.assertNotIn(marker.decode(), status_line, label)
                self.assertNotIn(b"Unsupported method", body, label)
            except (ConnectionError, socket.timeout):
                pass  # a closed connection is the acceptable outcome
            finally:
                sock.close()
        self.assertEqual(operator.calls, 0)

    def test_A1b_server_session_table_is_bounded_lru(self):
        """initialize MAX_SESSIONS + 1 times: the table never exceeds the
        bound, a session in active use survives, the idle oldest is
        evicted and answers 404 afterwards."""
        controller, _ = make_controller()
        client = self.serve(controller)
        port = int(client.url.rsplit(":", 1)[1].split("/")[0])
        bound = server_module.MAX_SESSIONS
        active = McpClient(port)
        self.assertEqual(active.initialize()[0], 200)
        idle = McpClient(port)
        self.assertEqual(idle.initialize()[0], 200)
        for _ in range(bound - 2):
            self.assertEqual(McpClient(port).initialize()[0], 200)
        self.assertEqual(len(self.server.sessions), bound)
        self.assertEqual(active.rpc("ping")[0], 200)  # touch
        self.assertEqual(McpClient(port).initialize()[0], 200)
        self.assertEqual(len(self.server.sessions), bound)
        self.assertEqual(active.rpc("ping")[0], 200)
        self.assertEqual(idle.rpc("ping")[0], 404)

    def test_A8d_bearer_scheme_is_case_insensitive(self):
        controller, _ = make_controller()
        client = self.serve(controller)
        status, headers, body = client.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25"}},
            headers={"Authorization": "bearer " + TOKEN},
        )
        self.assertEqual(status, 200, body)
        status, headers, body = client.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25"}},
            headers={"Authorization": "BEARER " + TOKEN},
        )
        self.assertEqual(status, 200, body)
        status, headers, body = client.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Basic " + TOKEN},
        )
        self.assertEqual(status, 401)

    def test_A9_version_negotiation_echoes_supported_else_newest(self):
        controller, _ = make_controller()
        client = self.serve(controller)
        status, headers, body = client.initialize("2025-06-18")
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        client2 = McpClient(int(client.url.rsplit(":", 1)[1].split("/")[0]))
        status, headers, body = client2.initialize("1999-01-01")
        self.assertEqual(body["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(
            protocol.SUPPORTED_PROTOCOL_VERSIONS,
            ("2025-11-25", "2025-06-18", "2025-03-26"),
        )

    def test_A10_origin_absent_served_allowlisted_served_foreign_403(self):
        client, _, _ = self.ready_client(origins=("https://grok.example",))
        status, headers, body = client.rpc("ping")
        self.assertEqual(status, 200)
        status, headers, body = client.rpc(
            "ping", headers={"Origin": "https://grok.example"}
        )
        self.assertEqual(status, 200)
        status, headers, body = client.rpc(
            "ping", headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(status, 403)


# ====================================================================
# B. Interaction across the neutral seam
# ====================================================================


class BInteractionAcrossTheSeamTests(unittest.TestCase):

    def test_B11_one_neutral_interaction_event_per_call(self):
        seen = []
        original = controller_module.GrokMcpController._build_adapter

        def spy(self, event):
            seen.append(event)
            return original(self, event)

        controller_module.GrokMcpController._build_adapter = spy
        try:
            controller, operator = make_controller()
            controller.call_tool("di_operator_turn", {"text": "the text"})
        finally:
            controller_module.GrokMcpController._build_adapter = original
        self.assertEqual(len(seen), 1)
        event = seen[0]
        self.assertIsInstance(event, InteractionEvent)
        self.assertIs(event.allowed, True)
        self.assertEqual(event.kind, EVENT_MESSAGE)
        self.assertEqual(event.content, "the text")

    def test_B12_adapter_is_a_real_subclass_with_the_full_member_set(self):
        self.assertTrue(
            issubclass(adapter_module.GrokMcpInteractionAdapter,
                       HumanInteractionAdapter)
        )
        event = InteractionEvent(
            sequence=1, allowed=True, reason="ok", kind=EVENT_MESSAGE,
            principal_id=1, conversation_id=1, message_id=1,
            action_id=None, content="hi",
        )
        adapter = adapter_module.GrokMcpInteractionAdapter(
            event, iter(range(100, 200)).__next__
        )
        self.assertEqual(
            HumanInteractionAdapter.__abstractmethods__ & set(dir(adapter)),
            HumanInteractionAdapter.__abstractmethods__,
        )
        outcome = adapter.receive(None)
        self.assertIsInstance(outcome, ReceiveOutcome)
        self.assertEqual(outcome.events, (event,))
        self.assertTrue(adapter.receive(None).idle)
        sent = adapter.send(1, "reply")
        self.assertIsInstance(sent, SendOutcome)
        self.assertTrue(sent.ok)
        self.assertEqual(sent.message_ids, (100,))
        self.assertEqual(adapter.delivered_text(), "reply")
        self.assertEqual(adapter.chunk_count("x" * 8001), 3)
        self.assertTrue(adapter.would_truncate("x" * 16001))
        self.assertEqual(adapter.max_message_chars, 4000)
        self.assertEqual(adapter.max_deliverable_chars, 16000)
        long_send = adapter.send(1, "y" * 16001)
        self.assertEqual(long_send.chunks_sent, 4)
        # One dropped chunk character plus the reserve carved out of
        # the last kept chunk for the omission notice.
        self.assertEqual(
            long_send.truncated_chars,
            1 + adapter_module.TRUNCATION_NOTICE_RESERVE_CHARS,
        )
        once = adapter.send_once(1, "once")
        self.assertEqual(once.classification, "applied")
        edited = adapter.edit(1, once.message_id, "edited")
        self.assertTrue(edited.ok)
        self.assertTrue(adapter.edit(1, once.message_id, "edited").already_applied)
        self.assertTrue(adapter.edit(1, 999, "x").target_missing)
        self.assertEqual(adapter.acknowledge("a", "t")[0], False)
        self.assertEqual(adapter.offer_controls(1, 1, None)[0], False)

    def test_B12b_truncated_chars_is_exact_invariant(self):
        """Contract: ``truncated_chars`` is the EXACT number of original
        characters omitted, so kept-original + truncated == len(text)."""
        event = InteractionEvent(
            sequence=1, allowed=True, reason="ok", kind=EVENT_MESSAGE,
            principal_id=1, conversation_id=1, message_id=1,
            action_id=None, content="hi",
        )
        for length in (16000, 16001, 17000, 20000, 100000):
            adapter = adapter_module.GrokMcpInteractionAdapter(
                event, iter(range(1, 1000)).__next__
            )
            text = "y" * length
            sent = adapter.send(1, text)
            delivered = adapter.delivered_text()
            kept = len(os.path.commonprefix([delivered, text]))
            self.assertEqual(kept + sent.truncated_chars, length, length)
            self.assertLessEqual(
                max(len(chunk) for _, chunk in adapter.outbox),
                adapter.max_message_chars, length,
            )
            if length <= adapter.max_deliverable_chars:
                self.assertEqual(sent.truncated_chars, 0, length)
                self.assertEqual(delivered, text, length)
            else:
                self.assertIn("%d" % sent.truncated_chars, delivered[kept:],
                              length)
        # The Lead's reproduction: 17000 chars.
        adapter = adapter_module.GrokMcpInteractionAdapter(
            event, iter(range(1, 1000)).__next__
        )
        sent = adapter.send(1, "y" * 17000)
        self.assertEqual(
            sent.truncated_chars,
            1000 + adapter_module.TRUNCATION_NOTICE_RESERVE_CHARS,
        )

    def test_B13_adapter_transmits_nothing_outward(self):
        source = (REPO_ROOT / "grok_mcp" / "adapter.py").read_text()
        roots = import_roots(REPO_ROOT / "grok_mcp" / "adapter.py")
        for banned in ("socket", "http", "urllib", "ssl", "asyncio",
                       "requests"):
            self.assertNotIn(banned, roots)
        self.assertNotIn("urlopen", source)

        def explode(*args, **kwargs):
            raise AssertionError("adapter opened a socket")

        saved = (socket.socket, socket.create_connection)
        socket.socket = explode
        socket.create_connection = explode
        try:
            controller, operator = make_controller()
            result = controller.call_tool("di_operator_turn", {"text": "hi"})
        finally:
            socket.socket, socket.create_connection = saved
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured["message"], "operator reply")

    def test_B14_prepare_then_execute_exactly_once_with_text_and_repository(
        self
    ):
        controller, operator = make_controller()
        controller.call_tool("di_operator_turn", {"text": "do the thing"})
        self.assertEqual(len(operator.built), 1)
        self.assertEqual(operator.calls, 1)
        request = operator.built[0]
        self.assertEqual(request.text, "do the thing")
        self.assertEqual(request.repository, REPOSITORY)
        self.assertIsNone(request.session_id)
        self.assertEqual(request.source, controller_module.SOURCE)
        self.assertIs(operator.submitted[0], request)

    def test_B15_provider_fields_reach_the_caller(self):
        controller, operator = make_controller(
            RecordingOperator(reply="the message", status="completed")
        )
        result = controller.call_tool("di_operator_turn", {"text": "hi"})
        self.assertEqual(result.structured["message"], "the message")
        self.assertEqual(result.structured["status"], "completed")
        self.assertEqual(result.structured["request_id"], "req-1")
        failing = RecordingOperator(reply=None, status="codex_failed")
        controller, _ = make_controller(failing)
        result = controller.call_tool("di_operator_turn", {"text": "hi"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured["status"], "codex_failed")
        self.assertFalse(result.structured["ok"])


# ====================================================================
# C. Identity
# ====================================================================


class CIdentityTests(unittest.TestCase):

    def test_C16_every_event_id_is_di_minted_and_grok_ids_are_rejected(self):
        seen = []
        original = controller_module.GrokMcpController._build_adapter

        def spy(self, event):
            seen.append(event)
            return original(self, event)

        controller_module.GrokMcpController._build_adapter = spy
        try:
            controller, operator = make_controller()
            controller.call_tool("di_operator_turn", {"text": "a"})
            controller.call_tool("di_operator_turn", {"text": "b"})
            refused = controller.call_tool(
                "di_operator_turn",
                {"text": "c", "conversation_id": 777},
            )
        finally:
            controller_module.GrokMcpController._build_adapter = original
        self.assertEqual(len(seen), 2)
        first, second = seen
        for event in seen:
            for value in (event.sequence, event.conversation_id,
                          event.message_id):
                self.assertIsInstance(value, int)
        self.assertLess(first.sequence, second.sequence)
        self.assertTrue(refused.is_error)
        self.assertIn("conversation_id", refused.structured["reason"])
        self.assertEqual(operator.calls, 2)

    def test_C17_no_input_schema_names_a_grok_private_id(self):
        for tool in protocol.TOOLS:
            names = set(tool["inputSchema"]["properties"])
            self.assertFalse(names & set(GROK_PRIVATE_ID_NAMES), tool["name"])
            self.assertIs(tool["inputSchema"]["additionalProperties"], False)

    def test_C18_di_minted_session_ref_continues_and_forged_ref_refuses(self):
        controller, operator = make_controller(
            RecordingOperator(session_id="provider-session-A")
        )
        first = controller.call_tool("di_operator_turn", {"text": "one"})
        session_ref = first.structured["session_ref"]
        self.assertTrue(REF_RE.match(session_ref))
        second = controller.call_tool(
            "di_operator_turn", {"text": "two", "session_ref": session_ref}
        )
        self.assertFalse(second.is_error)
        self.assertEqual(operator.built[1].session_id, "provider-session-A")
        self.assertEqual(second.structured["session_ref"], session_ref)
        forged = "di-" + "0" * 32
        third = controller.call_tool(
            "di_operator_turn", {"text": "three", "session_ref": forged}
        )
        self.assertTrue(third.is_error)
        self.assertIn("session_ref", third.structured["reason"])
        self.assertEqual(operator.calls, 2)
        self.assertEqual(controller.session_count, 1)
        self.assertNotIn(forged, controller.known_session_refs())

    def test_C18b_continuation_keeps_session_ref_when_provider_reports_none(
        self
    ):
        """Turn 1 mints a ref; turn 2 succeeds but the provider reports
        no session id; the caller must still get the SAME ref back, and
        turn 3 with it must still continue the provider session."""
        controller, operator = make_controller(
            RecordingOperator(session_ids=["provider-session-A", None, None])
        )
        first = controller.call_tool("di_operator_turn", {"text": "one"})
        ref = first.structured["session_ref"]
        self.assertTrue(REF_RE.match(ref))
        second = controller.call_tool(
            "di_operator_turn", {"text": "two", "session_ref": ref}
        )
        self.assertTrue(second.structured["ok"])
        self.assertEqual(second.structured["session_ref"], ref)
        third = controller.call_tool(
            "di_operator_turn", {"text": "three", "session_ref": ref}
        )
        self.assertTrue(third.structured["ok"])
        self.assertEqual(third.structured["session_ref"], ref)
        self.assertEqual(operator.built[2].session_id, "provider-session-A")
        self.assertEqual(controller.session_count, 1)
        # A refusal and an operator error still return null.
        refused = controller.call_tool(
            "di_operator_turn", {"text": "", "session_ref": ref}
        )
        self.assertIsNone(refused.structured["session_ref"])
        operator.raise_on_submit = RuntimeError("down")
        failed = controller.call_tool(
            "di_operator_turn", {"text": "four", "session_ref": ref}
        )
        self.assertTrue(failed.is_error)
        self.assertIsNone(failed.structured["session_ref"])

    def test_C18c_session_table_is_lru_not_fifo(self):
        """An actively used ref survives MAX_SESSION_ENTRIES newer idle
        ones; the idle oldest is the one evicted."""
        bound = controller_module.MAX_SESSION_ENTRIES
        # a, b, (bound-2) fills, then the touch turn reports the SAME
        # provider id as `a`, then one genuinely new session.
        ids = ["provider-%d" % n for n in range(bound)]
        ids += ["provider-0", "provider-%d" % bound]
        controller, operator = make_controller(
            RecordingOperator(session_ids=ids)
        )
        active = controller.call_tool(
            "di_operator_turn", {"text": "a"}
        ).structured["session_ref"]
        idle = controller.call_tool(
            "di_operator_turn", {"text": "b"}
        ).structured["session_ref"]
        for _ in range(bound - 2):
            controller.call_tool("di_operator_turn", {"text": "fill"})
        self.assertEqual(controller.session_count, bound)
        # Touch the active ref, then add one more new session.
        touched = controller.call_tool(
            "di_operator_turn", {"text": "touch", "session_ref": active}
        )
        self.assertEqual(touched.structured["session_ref"], active)
        controller.call_tool("di_operator_turn", {"text": "new"})
        self.assertEqual(controller.session_count, bound)
        self.assertIn(active, controller.known_session_refs())
        self.assertNotIn(idle, controller.known_session_refs())

    def test_C19_principal_id_is_the_credential_ordinal_only(self):
        seen = []
        original = controller_module.GrokMcpController._build_adapter

        def spy(self, event):
            seen.append(event)
            return original(self, event)

        controller_module.GrokMcpController._build_adapter = spy
        try:
            controller, operator = make_controller()
            controller.call_tool("di_operator_turn", {"text": "x"})
            controller, operator = make_controller(principal_id=2)
            controller.call_tool("di_operator_turn", {"text": "y"})
        finally:
            controller_module.GrokMcpController._build_adapter = original
        self.assertEqual([e.principal_id for e in seen], [1, 2])


# ====================================================================
# D. Idempotency
# ====================================================================


class DIdempotencyTests(unittest.TestCase):

    def test_D20_pure_tools_are_identical_across_retries_modulo_call_ref(
        self
    ):
        controller, operator = make_controller()
        for name, arguments in (("di_status", {}), ("di_ping", {"echo": "z"})):
            first = controller.call_tool(name, arguments).structured
            second = controller.call_tool(name, arguments).structured
            self.assertNotEqual(first["call_ref"], second["call_ref"])
            strip = lambda d: {k: v for k, v in d.items() if k != "call_ref"}
            self.assertEqual(strip(first), strip(second))
        self.assertEqual(operator.calls, 0)
        self.assertEqual(controller.replay_count, 0)

    def test_D21_replay_by_turn_ref_returns_recorded_result_without_rerun(
        self
    ):
        controller, operator = make_controller()
        first = controller.call_tool("di_operator_turn", {"text": "run"})
        turn_ref = first.structured["turn_ref"]
        replay = controller.call_tool(
            "di_operator_turn", {"text": "run", "turn_ref": turn_ref}
        )
        self.assertEqual(operator.calls, 1)
        self.assertTrue(replay.structured["replayed"])
        self.assertFalse(first.structured["replayed"])
        expected = dict(first.structured)
        expected["replayed"] = True
        self.assertEqual(replay.structured, expected)

    def test_D21b_forged_session_ref_is_refused_even_with_a_valid_turn_ref(
        self
    ):
        """A non-DI-minted ref is refused consistently: riding alongside
        a valid turn_ref does not let it be silently ignored. A DI-minted
        session_ref alongside the turn_ref still replays."""
        controller, operator = make_controller()
        first = controller.call_tool("di_operator_turn", {"text": "run"})
        turn_ref = first.structured["turn_ref"]
        session_ref = first.structured["session_ref"]
        forged = "di-" + "0" * 32
        result = controller.call_tool(
            "di_operator_turn",
            {"text": "run", "turn_ref": turn_ref, "session_ref": forged},
        )
        self.assertTrue(result.is_error)
        self.assertIn("session_ref", result.structured["reason"])
        self.assertFalse(result.structured["replayed"])
        self.assertEqual(operator.calls, 1)
        self.assertNotIn(forged, controller.known_session_refs())
        replay = controller.call_tool(
            "di_operator_turn",
            {"text": "run", "turn_ref": turn_ref, "session_ref": session_ref},
        )
        self.assertTrue(replay.structured["replayed"])
        self.assertEqual(operator.calls, 1)

    def test_D22_replay_table_is_bounded_fifo(self):
        controller, operator = make_controller()
        bound = controller_module.MAX_REPLAY_ENTRIES
        refs = []
        for index in range(bound + 1):
            result = controller.call_tool("di_operator_turn", {"text": "t"})
            refs.append(result.structured["turn_ref"])
        self.assertEqual(controller.replay_count, bound)
        oldest = controller.call_tool(
            "di_operator_turn", {"text": "t", "turn_ref": refs[0]}
        )
        self.assertTrue(oldest.is_error)
        newest = controller.call_tool(
            "di_operator_turn", {"text": "t", "turn_ref": refs[-1]}
        )
        self.assertTrue(newest.structured["replayed"])
        self.assertEqual(operator.calls, bound + 1)


# ====================================================================
# E. Authority / security
# ====================================================================


def import_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return roots


def non_docstring_strings(path):
    source = path.read_text(encoding="utf-8")
    positions = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                positions.add((body[0].value.lineno,
                               body[0].value.col_offset))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and token.start not in positions:
            yield token


class EAuthorityAndSecurityTests(ServerFixture):

    def test_E23_static_import_and_call_bans(self):
        self.assertTrue(GROK_MCP_FILES)
        for path in GROK_MCP_FILES:
            roots = import_roots(path)
            self.assertEqual(roots & set(FORBIDDEN_IMPORT_ROOTS), set(), path)
            source = path.read_text()
            self.assertNotIn("shell=True", source, path)
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id",
                                   getattr(node.func, "attr", None))
                    self.assertNotIn(name, {
                        "system", "__import__", "import_module", "popen",
                        "spawn", "execv", "execvp", "exec", "eval",
                    }, (path, name))
                if isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, path)
        # operator_session is consumed only by the CLI wiring.
        for path in GROK_MCP_FILES:
            if path.name != "cli.py":
                self.assertNotIn("operator_session", import_roots(path), path)

    def test_E24_no_write_mode_open(self):
        for path in GROK_MCP_FILES:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(
                    node.func, "id", None
                ) == "open":
                    modes = [a.value for a in node.args[1:2]
                             if isinstance(a, ast.Constant)]
                    modes += [k.value.value for k in node.keywords
                              if k.arg == "mode"
                              and isinstance(k.value, ast.Constant)]
                    for mode in modes:
                        self.assertNotRegex(mode, r"[wax+]", (path, mode))

    def test_E25_no_authority_literal_in_the_package(self):
        forbidden = set()
        for word in ("commit", "push", "merge", "tag", "release", "deploy",
                     "publish", "dispatch", "mission", "capability",
                     "authorization", "authorize", "approve", "git",
                     "--force", "--no-verify"):
            forbidden.add('"%s"' % word)
            forbidden.add("'%s'" % word)
        for path in GROK_MCP_FILES:
            for token in non_docstring_strings(path):
                self.assertNotIn(token.string, forbidden, (path, token.start))
                self.assertNotIn(".herd", token.string, (path, token.start))

    def test_E26_tool_table_is_narrow(self):
        self.assertEqual(len(protocol.TOOLS), 3)
        self.assertEqual(
            [t["name"] for t in protocol.TOOLS],
            ["di_status", "di_ping", "di_operator_turn"],
        )
        for tool in protocol.TOOLS:
            names = set(tool["inputSchema"]["properties"])
            for banned in ("command", "cmd", "argv", "args", "path", "url",
                           "repository", "repo", "file", "shell", "script"):
                self.assertNotIn(banned, names, tool["name"])
            self.assertIn("outputSchema", tool)

    def test_E27_auth_failures_refuse_before_the_controller(self):
        controller, operator = make_controller()
        client = self.serve(controller)
        no_token = McpClient(
            int(client.url.rsplit(":", 1)[1].split("/")[0]), token=None
        )
        status, headers, body = no_token.initialize()
        self.assertEqual(status, 401)
        self.assertEqual(
            {k.lower(): v for k, v in headers.items()}.get("www-authenticate"),
            "Bearer",
        )
        wrong = McpClient(
            int(client.url.rsplit(":", 1)[1].split("/")[0]), token="wrong"
        )
        status, headers, body = wrong.initialize()
        self.assertEqual(status, 401)
        status, headers, body = wrong.raw(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "di_operator_turn",
                        "arguments": {"text": "hi"}}}
        )
        self.assertEqual(status, 401)
        self.assertEqual(operator.calls, 0)
        self.assertEqual(len(operator.built), 0)
        self.assertEqual(controller.calls_received, 0)

    def test_E28_sentinel_token_never_leaks(self):
        sentinel = "SENTINEL-TOKEN-7f3a9c"
        operator = RecordingOperator()
        controller, operator = make_controller(operator)
        client = self.serve(controller, token=sentinel)
        captured = []

        def record(status, headers, body):
            captured.append(json.dumps(headers))
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            captured.append(body if isinstance(body, str) else json.dumps(body))

        record(*client.initialize())
        record(*client.rpc("tools/list"))
        record(*client.raw({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "di_operator_turn",
                                       "arguments": {"text": "hi"}}}))
        record(*client.raw(b"{bad"))
        record(*client.rpc("nope"))
        record(*McpClient(
            int(client.url.rsplit(":", 1)[1].split("/")[0]), token="wrong"
        ).rpc("ping"))
        operator.raise_on_submit = RuntimeError("boom " + sentinel)
        record(*client.raw({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                            "params": {"name": "di_operator_turn",
                                       "arguments": {"text": "again"}}}))
        for line in list(self.logs) + captured:
            self.assertNotIn(sentinel, line)
        # The config object hides the token and config errors never
        # echo it.
        config = config_module.ServerConfig(
            bind_host="127.0.0.1", port=0, endpoint_path="/mcp",
            bearer_token=sentinel, allowed_origins=(), repository="/r",
        )
        self.assertNotIn(sentinel, repr(config))
        self.assertNotIn(sentinel, str(config))
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as handle:
                json.dump({"repository": "/r",
                           "bearer_token": sentinel + " bad"}, handle)
            os.chmod(path, 0o600)
            with self.assertRaises(config_module.ConfigError) as caught:
                config_module.load_config(path, {})
            self.assertNotIn(sentinel, str(caught.exception))

    def test_E28b_socketserver_handle_error_routes_through_log_writer(self):
        """The claim "every unexpected exception is logged by class name
        only" must cover socketserver's handle_error too: it must reach
        the injected writer with the class name, never a traceback or
        the exception text, and write nothing to raw stderr."""
        sentinel = "SENTINEL-IN-EXCEPTION-TEXT"
        controller, _ = make_controller()
        logs = []
        server = server_module.GrokMcpServer(
            ("127.0.0.1", 0), controller, bearer_token=TOKEN,
            log_writer=logs.append,
        )
        self.addCleanup(server.server_close)
        captured = io.StringIO()
        saved = sys.stderr
        sys.stderr = captured
        try:
            try:
                raise ValueError(sentinel)
            except ValueError:
                server.handle_error(None, ("127.0.0.1", 0))
        finally:
            sys.stderr = saved
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(len(logs), 1)
        self.assertIn("ValueError", logs[0])
        self.assertNotIn(sentinel, logs[0])
        self.assertNotIn("Traceback", logs[0])

    def test_no_dead_protocol_default_or_empty_session_headers(self):
        self.assertFalse(hasattr(protocol, "DEFAULT_PROTOCOL_VERSION"))
        self.assertNotIn(
            "DEFAULT_PROTOCOL_VERSION",
            (REPO_ROOT / "grok_mcp" / "protocol.py").read_text(),
        )
        server_source = (REPO_ROOT / "grok_mcp" / "server.py").read_text()
        self.assertNotIn("session_headers", server_source)
        self.assertNotIn("specification's default", server_source)

    def test_E29_fresh_interpreter_import_probe(self):
        # http.server itself imports shutil (for its file-serving
        # handler, which grok_mcp never uses); the static ban above
        # covers shutil, so the behavioral probe lists the remaining
        # roots.
        roots = tuple(r for r in FORBIDDEN_IMPORT_ROOTS if r != "shutil")
        code = (
            "import sys\n"
            "import grok_mcp\n"
            "import grok_mcp.server, grok_mcp.controller, grok_mcp.adapter\n"
            "import grok_mcp.protocol, grok_mcp.config\n"
            "roots = %r\n"
            "bad = sorted(name for name in sys.modules if any(\n"
            "    name == r or name.startswith(r + '.') for r in roots))\n"
            "print('\\n'.join(bad))\n"
            "print('LOADED', 'grok_mcp.server' in sys.modules)\n"
            "sys.exit(1 if bad else 0)\n"
        ) % (roots,)
        probe = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, (probe.stdout, probe.stderr))
        self.assertIn("LOADED True", probe.stdout)


# ====================================================================
# F. Regression
# ====================================================================


class FRegressionTests(unittest.TestCase):

    def test_F30_human_interaction_pin_lists_grok_mcp(self):
        source = (REPO_ROOT / "tests" / "test_human_interaction.py").read_text()
        block = source[source.index("FORBIDDEN_ROOTS = ("):]
        block = block[:block.index(")")]
        self.assertIn('"grok_mcp"', block)

    def test_F31_neutral_packages_never_name_grok_mcp(self):
        for package in ("human_interaction", "operator_session"):
            for path in (REPO_ROOT / package).glob("*.py"):
                self.assertNotIn("grok_mcp", path.read_text(), path)

    def test_F32_no_telegram_symbol_and_no_poll_loop(self):
        for path in GROK_MCP_FILES:
            source = path.read_text()
            self.assertNotIn("telegram_operator", import_roots(path), path)
            # Identifiers and non-docstring strings only: the package
            # docstrings may state the boundary in prose.
            for token in tokenize.generate_tokens(
                io.StringIO(source).readline
            ):
                if token.type == tokenize.NAME:
                    self.assertNotIn("telegram", token.string.lower(),
                                     (path, token.start))
            for token in non_docstring_strings(path):
                self.assertNotIn("telegram", token.string.lower(),
                                 (path, token.start))
            self.assertNotIn("poll_updates", source, path)
            self.assertNotIn("getUpdates", source, path)
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id",
                                   getattr(node.func, "attr", None))
                    self.assertNotIn(name, {"sleep", "Thread"}, (path, name))

    def test_F33_no_durable_state_and_config_read_only_at_main(self):
        # No open() at all outside config.load_config; no json.dump.
        for path in GROK_MCP_FILES:
            source = path.read_text()
            self.assertNotIn("json.dump(", source, path)
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and getattr(
                    node.func, "id", None
                ) == "open":
                    self.assertEqual(path.name, "config.py", path)
        cli_source = (REPO_ROOT / "grok_mcp" / "cli.py").read_text()
        for path in GROK_MCP_FILES:
            if path.name not in ("cli.py", "config.py"):
                self.assertNotIn("load_config", path.read_text(), path)
                self.assertNotIn("environ", path.read_text(), path)
        self.assertIn("load_config(", cli_source)
        # Constructing every object performs no read.
        controller, _ = make_controller()
        self.assertEqual(controller.calls_received, 0)

    def test_cli_wires_config_session_controller_server_at_main_time(self):
        built = []

        def serve_forever(server):
            built.append(server)

        operator = RecordingOperator()
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as handle:
                json.dump({"repository": REPOSITORY, "port": 0,
                           "allowed_origins": ["https://grok.example"]},
                          handle)
            os.chmod(path, 0o600)
            code = cli_module.main(
                ["--config", path, "serve"],
                session_factory=operator.session,
                serve_forever=serve_forever,
                environ={config_module.BEARER_TOKEN_ENV: TOKEN},
                error_writer=lambda text: None,
            )
            self.assertEqual(code, cli_module.EXIT_OK)
            self.assertEqual(len(built), 1)
            server = built[0]
            self.assertEqual(server.server_address[0], "127.0.0.1")
            self.assertEqual(server.endpoint_path, "/mcp")
            self.assertEqual(server.allowed_origins, ("https://grok.example",))
            self.assertIsInstance(
                server.controller, controller_module.GrokMcpController
            )
            # Missing token -> config exit, nothing built.
            code = cli_module.main(
                ["--config", path, "serve"],
                session_factory=operator.session,
                serve_forever=serve_forever, environ={},
                error_writer=lambda text: None,
            )
            self.assertEqual(code, cli_module.EXIT_CONFIG)
            self.assertEqual(len(built), 1)
        self.assertEqual(
            cli_module.main([], error_writer=lambda text: None),
            cli_module.EXIT_CONFIG,
        )

    def test_entry_script_delegates_to_cli_main(self):
        source = (REPO_ROOT / "grokmcp.py").read_text()
        self.assertIn("from grok_mcp.cli import main", source)
        self.assertFalse((REPO_ROOT / "grok_mcp.py").exists())
        self.assertEqual(threading.active_count(), 1)


if __name__ == "__main__":
    unittest.main()
