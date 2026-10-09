"""Tests for the Grok Bot MCP transport (``grok_bot.mcp``, ``grok_bot.server``),
Task 8 slice 2: Streamable HTTP, tools only, verified on LOOPBACK ONLY.

What is REAL here: the MCP endpoint (a real HTTP server bound to 127.0.0.1 on
an ephemeral port), a real HTTP client on loopback, the adapter, the local
request surface, Mission Core and the run bridge; and, in one test, the real
``grokbot.py serve`` entry script in a child interpreter.

What is SYNTHETIC (as in ``tests/test_grok_bot.py``): the scripted Operator
behind the operator session seam, the run bridge's injected recorders, and
``SYNTHETIC_TOKEN``, a bearer token generated here and written only to
temporary owner-only files, never installed.

What these tests prove: the endpoint's PROTOCOL SHAPE over real loopback
HTTP with a real client (the MCP revisions it advertises, the transport
rules, loopback-only binding, the bearer check). They prove nothing about
live interoperability: the live Grok Bot client's revision, credential
header and request headers are unknown until the live acceptance exercise
(``grok_bot.server.PUBLIC_REACHABILITY["live_compatibility"]``). Nothing
here reaches a network beyond 127.0.0.1, spawns a Herdr, opens a tunnel or
is evidence of a live Grok Bot conversation.

Termination rule (CONTRIBUTING.md): every test carries the SIGALRM watchdog;
every client connection carries an independent ``timeout``; the server runs in
a daemon thread shut down in cleanup while the watchdog is still armed; the
child interpreter is read under the watchdog and killed in cleanup.
"""

import ast
import contextlib
import http.client
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import unittest  # noqa: E402

from grok_bot import adapter as adapter_module  # noqa: E402
from grok_bot import cli as cli_module  # noqa: E402
from grok_bot import mcp  # noqa: E402
from grok_bot import server as server_module  # noqa: E402

from test_grok_bot import (  # noqa: E402
    EVIDENCE, PROSE, Fixture, raw_observation, run_request,
)
from test_grok_bot_delivery import (  # noqa: E402
    DeliveryFixture, delivery_cli, index_module,
)

CLIENT_TIMEOUT_SECONDS = 20
CHILD_TIMEOUT_SECONDS = 60
ENTRY_SCRIPT = REPO_ROOT / "grokbot.py"
ACCEPT_BOTH = "application/json, text/event-stream"


def rpc(method, params=None, id_=1):
    message = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def strings_in(value):
    """Every string in a decoded JSON value, iteratively."""
    stack, found = [value], []
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


class ServerFixture(Fixture):
    """A real loopback endpoint over the slice-1 fixture's adapter, served
    with the SYNTHETIC bearer token (the endpoint is never served without
    one), which ``http`` sends unless a test names its own Authorization."""

    def setUp(self):
        super(ServerFixture, self).setUp()
        self.server = server_module.LoopbackMcpServer(
            self.adapter, bearer_token=SYNTHETIC_TOKEN)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        # LIFO: shutdown, close, then a bounded join; the watchdog is armed
        # throughout (Bounded registered its disarm first, so it runs last).
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method="POST", body=b"", headers=None, path=None):
        sent = {"Authorization": "Bearer " + SYNTHETIC_TOKEN}
        sent.update(headers or {})
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=CLIENT_TIMEOUT_SECONDS)
        try:
            connection.request(method, path or server_module.MCP_PATH,
                               body=body, headers=sent)
            response = connection.getresponse()
            return response.status, response, response.read()
        finally:
            connection.close()

    def post(self, message, headers=None, raw=None):
        sent = {"Content-Type": "application/json", "Accept": ACCEPT_BOTH}
        sent.update(headers or {})
        body = raw if raw is not None else json.dumps(message).encode("utf-8")
        return self.http("POST", body, sent)

    def call_ok(self, message):
        status, response, body = self.post(message)
        self.assertEqual(status, 200, body)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        return json.loads(body.decode("ascii"))

    def tool(self, name, arguments, id_=7):
        reply = self.call_ok(rpc("tools/call", {"name": name,
                                                "arguments": arguments}, id_))
        self.assertEqual(reply["id"], id_)
        result = reply["result"]
        payload = json.loads(result["content"][-1]["text"])
        self.assertEqual(result["isError"], not payload["ok"])
        return result, payload

    def assert_labels(self, payload):
        self.assertEqual(payload["delivery_authority"], "none")
        self.assertEqual(payload["evidence_status"], EVIDENCE)
        self.assertEqual(payload["transport"], "grok_bot")


# ====================================================================
# Protocol: initialize, tools/list, tools/call over real loopback HTTP
# ====================================================================


class ProtocolTests(ServerFixture):

    def test_initialize_echoes_only_an_advertised_revision(self):
        """The documented negotiation contract (the initialization
        handshake): an advertised revision is echoed; any other, 2025-03-26
        and the modern 2026-07-28 among them, is NOT accepted: the reply
        names this server's newest revision instead, which a client that
        cannot speak it must treat as a refusal and disconnect."""
        for requested, chosen in (
            ("2025-11-25", "2025-11-25"), ("2025-06-18", "2025-06-18"),
            ("2025-03-26", mcp.PROTOCOL_VERSIONS[0]),
            ("2026-07-28", mcp.PROTOCOL_VERSIONS[0]),
            ("1999-01-01", mcp.PROTOCOL_VERSIONS[0]),
            (None, mcp.PROTOCOL_VERSIONS[0]),
        ):
            with self.subTest(requested=requested):
                params = {"capabilities": {},
                          "clientInfo": {"name": "loopback-test", "version": "0"}}
                if requested is not None:
                    params["protocolVersion"] = requested
                result = self.call_ok(rpc("initialize", params))["result"]
                self.assertEqual(result["protocolVersion"], chosen)
                self.assertIn(result["protocolVersion"], mcp.PROTOCOL_VERSIONS)
                if requested not in mcp.PROTOCOL_VERSIONS:
                    self.assertNotEqual(result["protocolVersion"], requested)
                self.assertEqual(result["capabilities"],
                                 {"tools": {"listChanged": False}})
                self.assertEqual(result["serverInfo"], mcp.SERVER_INFO)
                for phrase in ("operator-attested",
                               "not cryptographically authenticated",
                               "separate message", "no commit, push"):
                    self.assertIn(phrase, result["instructions"])

    def test_notifications_and_client_responses_are_accepted_with_202(self):
        for message in ({"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": 1}},
                        {"jsonrpc": "2.0", "id": 9, "result": {}}):
            with self.subTest(message=message):
                status, response, body = self.post(message)
                self.assertEqual((status, body), (202, b""))

    def test_tools_list_is_exactly_the_adapter_surface(self):
        tools = self.call_ok(rpc("tools/list"))["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], sorted(adapter_module.TOOLS))
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(sorted(schema["properties"]),
                                 sorted(adapter_module.TOOLS[tool["name"]]))
                self.assertTrue(tool["description"])
        approve = [t for t in tools if t["name"] == "approve"][0]
        for phrase in ("separate", "approved", "approval_binding",
                       "operator-attested"):
            self.assertIn(phrase, approve["description"])
        run = [t for t in tools if t["name"] == "run"][0]
        self.assertEqual(sorted(run["inputSchema"]["properties"]["command"]["enum"]),
                         sorted(adapter_module.surface_module.LocalRequestSurface
                                .RUN_COMMANDS))

    def test_a_prose_request_to_one_dispatch_over_loopback(self):
        """B10's real tool calls, the whole slice-1 path, over HTTP."""
        result, out = self.tool("request", {"text": PROSE})
        self.assertFalse(result["isError"])
        self.assertEqual(out["status"], "proposed")
        self.assert_labels(out)
        self.assertEqual(self.operator.requests[0].source, "grok_bot")
        result, shown = self.tool("present", {"request_ref": out["request_ref"]})
        self.assertEqual(result["content"][0]["text"], shown["display_text"])
        # Local arming: unarmed, the relayed reply approves nothing.
        result, unarmed = self.tool("approve", self.unarmed(shown))
        self.assertEqual(unarmed["problem"], "grok_bot_approval_not_armed")
        result, approved = self.tool("approve", self.approval(shown))
        self.assertEqual(approved["status"], "approved_by_operator_attestation")
        self.assert_labels(approved)
        result, ran = self.tool("run", {
            "request_ref": out["request_ref"], "command": "dispatch",
            "arguments": {}})
        self.assertEqual(ran["phase"], "dispatched_not_yet_observed")
        self.observer.raw = raw_observation()
        result, observed = self.tool("run", {"request_ref": out["request_ref"],
                                             "command": "observe"})
        self.assertEqual(observed["phase"], "running_observed")
        result, status = self.tool("status", {"request_ref": out["request_ref"]})
        self.assertEqual(status["run"]["phase"], "running_observed")
        self.assertEqual(status["run"]["delivery_authority"], "none")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_a_refusal_is_a_labelled_tool_error_not_a_protocol_error(self):
        result, out = self.tool("approve", {"request_ref": "lr-" + "0" * 32})
        self.assertTrue(result["isError"])
        self.assertEqual(out["problem"], "grok_bot_not_presented")
        self.assert_labels(out)
        result, out = self.tool("status", {"request_ref": []})
        self.assertTrue(result["isError"])
        self.assertEqual(out["problem"], "grok_bot_bad_request")
        self.assert_labels(out)

    def test_unknown_tools_and_methods_are_protocol_errors(self):
        for name in ("merge", "release", "deploy", "deliver", "push", "commit"):
            with self.subTest(tool=name):
                reply = self.call_ok(rpc("tools/call", {"name": name,
                                                        "arguments": {}}))
                self.assertEqual(reply["error"]["code"], mcp.INVALID_PARAMS)
                self.assertEqual(reply["error"]["data"]["delivery_authority"],
                                 "none")
        for method in ("resources/list", "prompts/list", "sampling/createMessage",
                       "server/discover"):
            with self.subTest(method=method):
                reply = self.call_ok(rpc(method))
                self.assertEqual(reply["error"]["code"], mcp.METHOD_NOT_FOUND)
        self.assertEqual(self.call_ok(rpc("ping"))["result"], {})

    def test_every_response_body_is_ascii_json_without_lone_surrogates(self):
        """An accepted proposal holding a lone surrogate (V2) travels as
        pure ASCII; no decoded string anywhere in the reply is unencodable,
        so a strict client JSON parser accepts it."""
        self.operator.proposal = run_request(objective="Fix \ud800")
        result, out = self.tool("request", {"text": PROSE})
        status, response, body = self.post(rpc("tools/call", {
            "name": "present", "arguments": {"request_ref": out["request_ref"]}}))
        self.assertEqual(status, 200)
        body.decode("ascii")
        reply = json.loads(body.decode("ascii"))
        for text in strings_in(reply):
            text.encode("utf-8")
        shown = json.loads(reply["result"]["content"][-1]["text"])
        self.assertEqual(shown["proposal"]["objective"], "Fix \ud800")
        self.assertIn('proposal.objective: "Fix \\ud800"',
                      reply["result"]["content"][0]["text"])


# ====================================================================
# Transport: Streamable HTTP rules, DNS-rebinding guard, bounds
# ====================================================================


class TransportTests(ServerFixture):

    def test_get_and_delete_are_405_with_post_allowed(self):
        for method in ("GET", "DELETE"):
            with self.subTest(method=method):
                status, response, body = self.http(
                    method, headers={"Accept": "text/event-stream"})
                self.assertEqual(status, 405)
                self.assertEqual(response.getheader("Allow"), "POST")

    def test_only_the_mcp_path_is_served(self):
        status, _, _ = self.http("POST", b"{}", {"Content-Type": "application/json"},
                                 path="/other")
        self.assertEqual(status, 404)

    def test_a_foreign_host_or_any_origin_is_refused(self):
        """DNS rebinding: a request naming another host, or carrying a
        browser Origin, is refused before any message is read."""
        for headers in ({"Host": "evil.example:%d" % self.port},
                        {"Host": "127.0.0.1:%d" % (self.port + 1)},
                        {"Origin": "https://evil.example"},
                        {"Origin": "http://127.0.0.1:%d" % self.port}):
            with self.subTest(headers=headers):
                status, _, body = self.post(rpc("tools/list"), headers)
                self.assertEqual(status, 403, body)
                self.assertEqual(json.loads(body)["error"]["data"][
                    "delivery_authority"], "none")
        self.assertEqual(self.operator.requests, [])
        for host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port):
            with self.subTest(host=host):
                self.assertEqual(self.post(rpc("ping"), {"Host": host})[0], 200)

    def test_content_type_accept_length_and_body_are_enforced(self):
        message = json.dumps(rpc("ping")).encode("utf-8")
        cases = (
            (415, {"Content-Type": "text/plain", "Accept": ACCEPT_BOTH}, message),
            (406, {"Content-Type": "application/json", "Accept": "text/html"},
             message),
            (413, {"Content-Type": "application/json", "Accept": ACCEPT_BOTH},
             b" " * (server_module.MAX_REQUEST_BODY_BYTES + 1)),
            (400, {"Content-Type": "application/json", "Accept": ACCEPT_BOTH},
             b"{not json"),
            (400, {"Content-Type": "application/json", "Accept": ACCEPT_BOTH},
             b"\xff\xfe"),
            (400, {"Content-Type": "application/json", "Accept": ACCEPT_BOTH},
             json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}).encode()),
        )
        for status, headers, body in cases:
            with self.subTest(status=status, body=body[:20]):
                self.assertEqual(self.http("POST", body, headers)[0], status)

    def test_a_missing_content_length_is_411(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=CLIENT_TIMEOUT_SECONDS)
        try:
            connection.putrequest("POST", server_module.MCP_PATH)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Accept", ACCEPT_BOTH)
            connection.putheader("Authorization", "Bearer " + SYNTHETIC_TOKEN)
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 411)
        finally:
            connection.close()

    def test_an_unsupported_protocol_version_header_is_a_legacy_400(self):
        """400 with a body that is NOT the modern -32022 error, so a
        dual-era client falls back to initialize (spec compatibility
        matrix). 2025-03-26 is now unsupported like any other; the message
        names exactly the advertised revisions; an advertised header is
        served."""
        for unsupported in ("2025-03-26", "2026-07-28", "1999-01-01"):
            with self.subTest(header=unsupported):
                status, _, body = self.post(
                    rpc("tools/list"), {"MCP-Protocol-Version": unsupported})
                self.assertEqual(status, 400)
                error = json.loads(body)["error"]
                self.assertEqual(error["code"], mcp.INVALID_REQUEST)
                self.assertNotEqual(error["code"], -32022)
                self.assertIn("unsupported MCP-Protocol-Version", error["message"])
                self.assertTrue(error["message"].endswith(
                    "revisions " + ", ".join(mcp.PROTOCOL_VERSIONS)),
                    error["message"])
        for version in mcp.PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                self.assertEqual(self.post(
                    rpc("ping"), {"MCP-Protocol-Version": version})[0], 200)

    def test_a_batch_is_refused_under_every_advertised_revision(self):
        """JSON-RPC batching was removed in 2025-06-18, and both advertised
        revisions (2025-06-18, 2025-11-25) are without it, so a batch is an
        invalid request under each: correct behaviour for what is
        advertised, not a known incompatibility. A batch under 2025-03-26
        (which required batches) is refused earlier, as an unsupported
        revision: that revision is not advertised (the named live gap)."""
        batch = json.dumps([rpc("ping"), rpc("tools/list", id_=2)]).encode()
        for version in mcp.PROTOCOL_VERSIONS + (None,):
            with self.subTest(version=version):
                headers = {"Content-Type": "application/json",
                           "Accept": ACCEPT_BOTH}
                if version is not None:
                    headers["MCP-Protocol-Version"] = version
                status, _, body = self.http("POST", batch, headers)
                self.assertEqual(status, 400, body)
                error = json.loads(body)["error"]
                self.assertEqual(error["code"], mcp.INVALID_REQUEST)
                self.assertIn("batching", error["message"])
                self.assertIn("removed in 2025-06-18", error["message"])
        status, _, body = self.http("POST", batch, {
            "Content-Type": "application/json", "Accept": ACCEPT_BOTH,
            "MCP-Protocol-Version": "2025-03-26"})
        self.assertEqual(status, 400)
        self.assertIn("unsupported MCP-Protocol-Version",
                      json.loads(body)["error"]["message"])
        self.assertEqual(self.operator.requests, [])

    def test_an_sse_only_client_receives_one_sse_event(self):
        status, response, body = self.post(
            rpc("tools/list"), {"Accept": "text/event-stream"})
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        text = body.decode("ascii")
        self.assertTrue(text.startswith("event: message\ndata: "), text[:40])
        self.assertTrue(text.endswith("\n\n"))
        data = json.loads(text[len("event: message\ndata: "):].strip())
        self.assertEqual(len(data["result"]["tools"]), len(adapter_module.TOOLS))

    def test_deep_nesting_never_escapes_as_an_exception(self):
        deep = "[" * 5000 + "0" + "]" * 5000
        body = ('{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params":'
                ' {"name": "status", "arguments": {"request_ref": %s}}}' % deep)
        status, _, reply = self.post(None, raw=body.encode("ascii"))
        if status == 400:  # this interpreter's decoder recursed out first
            self.assertEqual(json.loads(reply)["error"]["code"], mcp.PARSE_ERROR)
        else:
            self.assertEqual(status, 200)
            result = json.loads(reply)["result"]
            self.assertTrue(result["isError"])
            payload = json.loads(result["content"][-1]["text"])
            self.assertEqual(payload["problem"], "grok_bot_bad_request")
            self.assert_labels(payload)
        self.assertEqual(self.post(rpc("ping"))[0], 200)


# ====================================================================
# B10: the bind is loopback, and no public bind can occur
# ====================================================================


class LoopbackBindTests(ServerFixture):

    def test_the_bound_socket_is_127_0_0_1_on_an_ephemeral_port(self):
        host, port = self.server.socket.getsockname()[:2]
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)
        self.assertEqual(self.server.url, "http://127.0.0.1:%d/mcp" % port)
        self.assertEqual(self.server.socket.family, server_module.socket.AF_INET)

    def test_a_non_loopback_address_is_refused_before_any_bind(self):
        """The guard runs before socketserver's bind AND its listen, both
        recorded and both required never to be reached (listen on an
        unbound socket would bind the wildcard address by itself). Even a
        broken guard therefore cannot make this test listen anywhere, and
        anything it built is closed."""
        for host in ("0.0.0.0", "", "192.168.1.10", "10.0.0.1", "::",
                     "localhost", "127.0.0.2", "8.8.8.8"):
            with self.subTest(host=host):
                with mock.patch.object(server_module, "LOOPBACK_HOST", host), \
                        mock.patch.object(socketserver.TCPServer,
                                          "server_bind") as bind, \
                        mock.patch.object(socketserver.TCPServer,
                                          "server_activate") as listen:
                    try:
                        built = server_module.LoopbackMcpServer(
                            self.adapter, bearer_token=SYNTHETIC_TOKEN)
                    except server_module.NotLoopbackError:
                        built = None
                    if built is not None:
                        built.server_close()
                    self.assertIsNone(built, "a non-loopback endpoint was built")
                    bind.assert_not_called()
                    listen.assert_not_called()

    def test_listen_is_refused_on_a_socket_not_bound_to_loopback(self):
        """Third layer: ``server_activate`` never calls listen on an unbound
        socket. No socket here is ever bound or listening."""
        unbound = object.__new__(server_module.LoopbackMcpServer)
        unbound.socket = server_module.socket.socket(
            server_module.socket.AF_INET, server_module.socket.SOCK_STREAM)
        self.addCleanup(unbound.socket.close)
        with mock.patch.object(socketserver.TCPServer, "server_activate") as listen:
            with self.assertRaises(server_module.NotLoopbackError):
                unbound.server_activate()
            listen.assert_not_called()
        self.assertEqual(unbound.socket.getsockname()[1], 0)

    def test_a_bind_that_lands_off_loopback_is_closed_and_refused(self):
        """Second layer: even if the socket reported another address after
        binding, the server refuses to exist."""
        real = socketserver.TCPServer.server_bind

        def lands_elsewhere(server):
            real(server)
            server.socket.close()
            server.socket = mock.Mock(getsockname=lambda: ("0.0.0.0", 1),
                                      close=lambda: None)
        with mock.patch.object(socketserver.TCPServer, "server_bind",
                               lands_elsewhere):
            with self.assertRaises(server_module.NotLoopbackError):
                server_module.LoopbackMcpServer(self.adapter,
                                                bearer_token=SYNTHETIC_TOKEN)

    def test_the_cli_cannot_name_a_host(self):
        for flag in ("--host", "--bind", "--listen", "--address"):
            with self.subTest(flag=flag):
                errors = __import__("io").StringIO()
                with mock.patch.object(sys, "stderr", errors):
                    code = cli_module.main(
                        ["--state-dir", self.state, "--repository", str(REPO_ROOT),
                         "--control-repo", "/control-repo", "serve", flag,
                         "0.0.0.0"], stdout=__import__("io").StringIO())
                self.assertEqual(code, cli_module.EXIT_USAGE)
                self.assertIn("unrecognized arguments", errors.getvalue())

    def test_B11_no_product_source_names_a_wildcard_or_unguarded_bind(self):
        for path in sorted((REPO_ROOT / "grok_bot").glob("*.py")) + [ENTRY_SCRIPT]:
            with self.subTest(path=path.name):
                self.assertEqual(bind_violations(path.read_text(encoding="utf-8"),
                                                 path.name), [])

    def test_B11_the_bind_detector_fires_on_planted_probes(self):
        self.assertEqual(bind_violations("HOST = '0.0.0.0'\n", "x.py"),
                         ["'0.0.0.0'"])
        self.assertEqual(bind_violations("import socket\nsocket.INADDR_ANY\n",
                                         "x.py"), ["INADDR_ANY"])
        self.assertEqual(bind_violations("s.bind(('', 0))\n", "x.py"),
                         ["bind() outside the guarded server_bind"])
        self.assertEqual(bind_violations("s.listen()\n", "x.py"),
                         ["listen() outside the guarded server_activate"])
        self.assertEqual(bind_violations(
            "class S:\n    def server_bind(self):\n        s.listen()\n",
            "server.py"), ["listen() outside the guarded server_activate"])
        self.assertEqual(bind_violations("HOST = '127.0.0.1'\n", "x.py"), [])


GUARDED_CALLS = {"bind": "server_bind", "listen": "server_activate"}


def bind_violations(source, name):
    """Wildcard addresses anywhere, and any ``bind(...)`` or
    ``listen(...)`` call outside the one guarded ``server_bind`` /
    ``server_activate`` in ``server.py``."""
    found = []
    tree = ast.parse(source)
    guarded = {}
    if name == "server.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                GUARDED_CALLS.values()
            ):
                guarded.update((id(n), node.name) for n in ast.walk(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in ("0.0.0.0", "::"):
            found.append(repr(node.value))
        elif isinstance(node, ast.Attribute) and node.attr == "INADDR_ANY":
            found.append("INADDR_ANY")
        elif isinstance(node, ast.Call):
            called = getattr(node.func, "attr", getattr(node.func, "id", None))
            if called in GUARDED_CALLS and guarded.get(id(node)) != (
                GUARDED_CALLS[called]
            ):
                found.append("%s() outside the guarded %s"
                             % (called, GUARDED_CALLS[called]))
    return found


# ====================================================================
# The real entry script in a child interpreter, and the setup contract
# ====================================================================


class EntryScriptServeTests(Fixture):

    def start_child(self, *extra):
        """``grokbot.py serve`` in a child interpreter; returns the parsed
        startup line and its raw text. Read under the watchdog (a child that
        never prints fails fast) and terminated, then killed, in cleanup."""
        temp = tempfile.mkdtemp(dir=self.tmp.name)
        stderr = open(os.path.join(temp, "stderr"), "w+")
        self.addCleanup(stderr.close)
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        child = subprocess.Popen(
            [sys.executable, str(ENTRY_SCRIPT), "--state-dir", self.state,
             "--repository", str(REPO_ROOT), "--control-repo",
             os.path.join(temp, "control"), "serve", "--port", "0"] + list(extra),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
            env=env, cwd=temp, text=True)

        def stop():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=CHILD_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=CHILD_TIMEOUT_SECONDS)
            child.stdout.close()
        self.addCleanup(stop)
        line = child.stdout.readline()
        self.assertTrue(line, stderr.seek(0) or stderr.read())
        started = json.loads(line)
        self.assertEqual(started["status"], "serving")
        self.assertEqual(started["bind_address"], "127.0.0.1")
        self.assertEqual(started["delivery_authority"], "none")
        self.assertEqual(started["public_reachability"],
                         server_module.PUBLIC_REACHABILITY)
        self.assertEqual(started["url"],
                         "http://127.0.0.1:%d/mcp" % started["port"])
        return started, line

    def status_call(self, port, authorization=None):
        headers = {"Content-Type": "application/json", "Accept": ACCEPT_BOTH}
        if authorization is not None:
            headers["Authorization"] = authorization
        connection = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=CLIENT_TIMEOUT_SECONDS)
        try:
            connection.request("POST", "/mcp", body=json.dumps(rpc(
                "tools/call", {"name": "status",
                               "arguments": {"request_ref": "lr-" + "0" * 32}})),
                headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_grokbot_serve_without_a_token_file_refuses_to_start(self):
        """Fail closed: without --auth-token-file the
        real entry script prints one labelled refusal and exits 2; it never
        listens. The child is bounded by its own timeout."""
        temp = tempfile.mkdtemp(dir=self.tmp.name)
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        result = subprocess.run(
            [sys.executable, str(ENTRY_SCRIPT), "--state-dir", self.state,
             "--repository", str(REPO_ROOT), "--control-repo",
             os.path.join(temp, "control"), "serve", "--port", "0"],
            stdin=subprocess.DEVNULL, capture_output=True, env=env, cwd=temp,
            text=True, timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, cli_module.EXIT_USAGE, result.stderr)
        refusal = json.loads(result.stdout)
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["problem"], "grok_bot_bad_request")
        self.assertIn("--auth-token-file", refusal["reason"])
        self.assertNotIn("url", refusal)

    def test_grokbot_serve_with_a_token_file_requires_the_bearer(self):
        """SYNTHETIC token in a temporary owner-only file; never printed."""
        path = os.path.join(self.tmp.name, "token")
        with open(path, "w", encoding="ascii") as handle:
            handle.write(SYNTHETIC_TOKEN + "\n")
        os.chmod(path, 0o600)
        started, line = self.start_child("--auth-token-file", path)
        self.assertEqual(started["access_control"], "bearer_token")
        self.assertNotIn(SYNTHETIC_TOKEN, line)
        self.assertEqual(self.status_call(started["port"])[0], 401)
        self.assertEqual(self.status_call(started["port"], "Bearer wrong")[0], 401)
        status, reply = self.status_call(started["port"],
                                         "Bearer " + SYNTHETIC_TOKEN)
        self.assertEqual(status, 200)
        payload = json.loads(reply["result"]["content"][-1]["text"])
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(payload["problem"], "local_request_unknown_request")
        self.assertEqual(payload["delivery_authority"], "none")


# ====================================================================
# Mutation self-check: the transport's guarding tests fail when the
# property breaks (patched IN MEMORY; nothing on disk changes)
# ====================================================================

MUTANTS = (
    ("the loopback guard accepts any address",
     server_module, "require_loopback", lambda host: None,
     ("LoopbackBindTests.test_a_non_loopback_address_is_refused_before_any"
      "_bind",)),
    ("listen is not guarded",
     server_module.LoopbackMcpServer, "server_activate",
     lambda self: socketserver.TCPServer.server_activate(self),
     ("LoopbackBindTests.test_listen_is_refused_on_a_socket_not_bound_to"
      "_loopback",)),
    ("Host and Origin admission is skipped",
     server_module.McpRequestHandler, "_admitted", lambda self: True,
     ("TransportTests.test_a_foreign_host_or_any_origin_is_refused",
      "TransportTests.test_only_the_mcp_path_is_served")),
    ("the bearer check is skipped",
     server_module.McpRequestHandler, "_bearer_ok", lambda self: True,
     ("BearerTokenTests.test_a_missing_or_wrong_token_is_refused_with_401",
      "BearerTokenTests.test_a_wrong_token_is_refused_before_the_body_is"
      "_read")),
    ("reply bodies are not ASCII-escaped",
     mcp, "ascii_json",
     lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
     ("ProtocolTests.test_every_response_body_is_ascii_json_without_lone"
      "_surrogates",)),
    ("a refusal is not marked isError",
     mcp, "tool_result",
     lambda name, result: {"content": [{"type": "text",
                                        "text": mcp.ascii_json(result)}],
                           "isError": False},
     ("ProtocolTests.test_a_refusal_is_a_labelled_tool_error_not_a_protocol"
      "_error",)),
)


class MutationSelfCheckTests(unittest.TestCase):

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_B11_every_transport_mutant_is_caught_and_the_original_passes(self):
        for label, owner, name, mutant, names in MUTANTS:
            with self.subTest(mutant=label):
                with mock.patch.object(owner, name, mutant):
                    broken = self.run_named(names)
                failed = set(getattr(test, "test_case", test).id()
                             for test, _ in broken.failures + broken.errors)
                self.assertEqual(len(failed), len(names), (label, failed))
                restored = self.run_named(names)
                self.assertTrue(restored.wasSuccessful(),
                                (label, restored.failures, restored.errors))


# ====================================================================
# X1: transport access control (a bearer token), separate from approval
# ====================================================================

SYNTHETIC_TOKEN = "synthetic-test-token-" + "0123456789abcdef" * 2


class BearerTokenTests(Fixture):
    """SYNTHETIC token, generated for this test and never installed. It
    stands in for the credential a human provisions; it authenticates the
    transport caller only and approves nothing."""

    def serve(self, token=SYNTHETIC_TOKEN):
        server = server_module.LoopbackMcpServer(self.adapter, bearer_token=token)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def post(self, server, message, authorization=None, timeout=None):
        headers = {"Content-Type": "application/json", "Accept": ACCEPT_BOTH}
        if authorization is not None:
            headers["Authorization"] = authorization
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port,
            timeout=timeout or CLIENT_TIMEOUT_SECONDS)
        try:
            connection.request("POST", "/mcp", body=json.dumps(message),
                               headers=headers)
            response = connection.getresponse()
            return response.status, response, response.read()
        finally:
            connection.close()

    def test_without_a_token_the_endpoint_is_never_served(self):
        """Fail closed: no token, no endpoint."""
        with self.assertRaises(server_module.BearerTokenError):
            server_module.LoopbackMcpServer(self.adapter)
        with self.assertRaises(server_module.BearerTokenError):
            server_module.LoopbackMcpServer(self.adapter, bearer_token=None)

    def test_a_missing_or_wrong_token_is_refused_with_401(self):
        server = self.serve()
        self.assertEqual(server.access_control, "bearer_token")
        for authorization in (None, "", "Bearer", "Bearer wrong",
                              "Bearer " + SYNTHETIC_TOKEN + "x",
                              "Basic " + SYNTHETIC_TOKEN, SYNTHETIC_TOKEN,
                              "Bearer " + SYNTHETIC_TOKEN[:-1]):
            with self.subTest(authorization=authorization):
                status, response, body = self.post(
                    server, rpc("tools/list"), authorization)
                self.assertEqual(status, 401, body)
                self.assertIn("Bearer", response.getheader("WWW-Authenticate"))
                self.assertNotIn(SYNTHETIC_TOKEN.encode(), body)
                self.assertEqual(json.loads(body)["error"]["data"][
                    "delivery_authority"], "none")
        self.assertEqual(self.operator.requests, [])
        status, _, body = self.post(server, rpc("tools/list"),
                                    "Bearer " + SYNTHETIC_TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.post(server, rpc("ping"),
                                   "bearer " + SYNTHETIC_TOKEN)[0], 200)

    def test_a_wrong_token_is_refused_before_the_body_is_read(self):
        """Headers announce a body that never arrives. Refusing first is
        answered at once; reading first would wait for the body until this
        client's 5-second timeout fails the test (the server's own read
        timeout is 30)."""
        server = self.serve()
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5)
        try:
            connection.putrequest("POST", "/mcp")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Accept", ACCEPT_BOTH)
            connection.putheader("Content-Length", "1000")
            connection.putheader("Authorization", "Bearer wrong")
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 401)
        finally:
            connection.close()

    def test_the_token_never_approves_and_never_reaches_the_adapter(self):
        """Transport access control and operator-attested approval stay
        separate: with the right token, approval still needs the displayed
        binding and the human's exact reply, is still labelled
        operator-attested, and the token appears in no adapter argument and
        in no durable record."""
        seen = []
        real_call = self.adapter.call

        def recording_call(tool, arguments):
            seen.append(json.dumps([tool, arguments], default=repr))
            return real_call(tool, arguments)
        self.adapter.call = recording_call
        server = self.serve()
        bearer = "Bearer " + SYNTHETIC_TOKEN

        def tool(name, arguments):
            status, _, body = self.post(server, rpc("tools/call", {
                "name": name, "arguments": arguments}), bearer)
            self.assertEqual(status, 200, body)
            return json.loads(json.loads(body)["result"]["content"][-1]["text"])
        out = tool("request", {"text": PROSE})
        shown = tool("present", {"request_ref": out["request_ref"]})
        unarmed = tool("approve", dict(shown["approval_binding"],
                                       relayed_reply="approved", relay_ref="r"))
        self.assertEqual(unarmed["problem"], "grok_bot_approval_not_armed")
        refused = tool("approve", dict(shown["approval_binding"],
                                       relayed_reply="", relay_ref="r",
                                       approval_code=self.arm(shown)))
        self.assertEqual(refused["problem"], "local_request_reply_not_affirmative")
        approved = tool("approve", dict(shown["approval_binding"],
                                        relayed_reply="approved", relay_ref="r",
                                        approval_code=self.arm(shown)))
        self.assertEqual(approved["status"], "approved_by_operator_attestation")
        self.assertEqual(approved["provenance_label"]["proof"],
                         "operator_attested_not_independently_verified")
        self.assertEqual(approved["evidence_status"], EVIDENCE)
        self.assertTrue(seen)
        self.assertFalse([s for s in seen if SYNTHETIC_TOKEN in s])
        for name in os.listdir(self.state):
            path = os.path.join(self.state, name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    self.assertNotIn(SYNTHETIC_TOKEN.encode(), handle.read(), name)

    def test_a_malformed_configured_token_is_refused_at_startup(self):
        for token in ("short", "x" * 31, "has space " + "x" * 40,
                      "x" * (server_module.MAX_BEARER_TOKEN_CHARS + 1),
                      "é" * 40, "", 123):
            with self.subTest(token=repr(token)[:30]):
                with self.assertRaises(server_module.BearerTokenError):
                    server_module.LoopbackMcpServer(self.adapter,
                                                    bearer_token=token)


class BearerTokenFileTests(unittest.TestCase):
    """The token is read once, from an owner-only file the human provisions;
    never from argv or the environment, and never printed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def token_file(self, text, mode=0o600):
        path = os.path.join(self.tmp.name, "token")
        with open(path, "w", encoding="ascii") as handle:
            handle.write(text)
        os.chmod(path, mode)
        return path

    def test_an_owner_only_file_is_read_once_and_trimmed_of_its_newline(self):
        self.assertEqual(server_module.read_bearer_token(
            self.token_file(SYNTHETIC_TOKEN + "\n")), SYNTHETIC_TOKEN)

    def test_an_unsafe_file_is_refused_without_echoing_the_token(self):
        cases = [("relative", lambda: "token"),
                 ("group readable", lambda: self.token_file(SYNTHETIC_TOKEN, 0o640)),
                 ("world readable", lambda: self.token_file(SYNTHETIC_TOKEN, 0o604)),
                 ("missing", lambda: os.path.join(self.tmp.name, "absent")),
                 ("too short", lambda: self.token_file("abc")),
                 ("two lines", lambda: self.token_file(SYNTHETIC_TOKEN + "\nmore"))]
        link = os.path.join(self.tmp.name, "link")
        os.symlink(self.token_file(SYNTHETIC_TOKEN), link)
        cases.append(("symlink", lambda: link))
        for label, path in cases:
            with self.subTest(label=label):
                with self.assertRaises(server_module.BearerTokenError) as caught:
                    server_module.read_bearer_token(path())
                self.assertNotIn(SYNTHETIC_TOKEN, str(caught.exception))

    def test_the_cli_refuses_a_bad_token_file_without_serving(self):
        out = __import__("io").StringIO()
        code = cli_module.main(
            ["--state-dir", os.path.join(self.tmp.name, "state"),
             "--repository", str(REPO_ROOT), "--control-repo", "/control-repo",
             "serve", "--auth-token-file",
             self.token_file(SYNTHETIC_TOKEN, 0o644)], stdout=out)
        self.assertEqual(code, cli_module.EXIT_USAGE)
        result = json.loads(out.getvalue())
        self.assertEqual(result["problem"], "grok_bot_bad_request")
        self.assertNotIn(SYNTHETIC_TOKEN, out.getvalue())


# ====================================================================
# X3: the complete display travels whole over the transport
# ====================================================================


class CompleteDisplayTests(ServerFixture):

    def test_present_carries_the_complete_display_untruncated(self):
        from test_grok_bot import leaf_lines
        long_scope = ("readiness probe and its tests; " * 120).strip()
        self.operator.proposal = run_request(requested_scope=long_scope)
        result, out = self.tool("request", {"text": PROSE})
        result, shown = self.tool("present", {"request_ref": out["request_ref"]})
        text = result["content"][0]["text"]
        self.assertEqual(text, shown["display_text"])
        self.assertGreater(len(text), len(long_scope))
        lines = text.splitlines()
        for line in leaf_lines("proposal", shown["proposal"]):
            self.assertIn(line, lines)


class DeliveryOverMcpTests(ServerFixture, DeliveryFixture):
    """Slice 3's delivery ceremony tools over the loopback endpoint
    (protocol shape only), under ``test_grok_bot_delivery``'s STRUCTURAL
    containment: every process seam raises and is asserted unreached, and
    pr_delivery runs over its recording transport double."""

    def test_present_and_approve_delivery_over_loopback(self):
        result, shown = self.tool("present_delivery", self.present_arguments())
        self.assertEqual(result["content"][0]["text"], shown["display_text"])
        self.assertEqual(shown["status"], "delivery_presented")
        result, unarmed = self.tool("approve_delivery", dict(
            shown["approval_binding"], relayed_reply="approved",
            reply_to="grok-message-1", relay_ref="relay-1"))
        self.assertEqual(unarmed["problem"], "grok_bot_approval_not_armed")
        self.assertEqual(self.deliveries(), {})
        result, approved = self.tool("approve_delivery", dict(
            shown["approval_binding"], relayed_reply="approved",
            reply_to="grok-message-1", relay_ref="relay-1",
            approval_code=self.arm_delivery(shown)))
        self.assertEqual(approved["status"],
                         "delivery_authorized_by_operator_attestation")
        self.assertIs(approved["delivery_authorization"][
            "granted_by_this_transport"], False)
        self.assertEqual(approved["delivery_authority"], "none")
        self.assertEqual(len(self.deliveries()), 1)
        self.assert_nothing_performed()

    def test_an_unexpected_failure_is_a_labelled_error_and_serving_continues(self):
        """SYNTHETIC: the ceremony fails in a way no refusal anticipates (an
        OverflowError, the S3-N1 class). The tools/call branch of
        ``mcp.handle`` is closed: the client gets a labelled JSON-RPC
        internal error, never a dropped connection, and the endpoint keeps
        serving. Nothing is presented, recorded or performed."""
        def overflowing(*args, **kwargs):
            raise OverflowError("int too large to convert to float")
        with mock.patch.object(delivery_cli, "present_dots_cmd", overflowing):
            # The adapter's own guard is NOT closed (it enumerates, and an
            # accepted slice-1 test relies on a synthetic crash
            # propagating), so the labelled reply below can only come from
            # mcp.handle's closure; without it this call drops the
            # connection.
            with self.assertRaises(OverflowError):
                self.adapter.present_delivery(**self.present_arguments())
            reply = self.call_ok(rpc("tools/call", {
                "name": "present_delivery",
                "arguments": self.present_arguments()}, 9))
        self.assertEqual(reply["id"], 9)
        self.assertNotIn("result", reply)
        self.assertEqual(reply["error"]["code"], mcp.INTERNAL_ERROR)
        self.assertEqual(mcp.INTERNAL_ERROR, -32603)
        self.assertIn("OverflowError", reply["error"]["message"])
        self.assertIn("read status before retrying", reply["error"]["message"])
        self.assert_labels(reply["error"]["data"])
        self.assertEqual(index_module.RequestIndex(self.state).load().get(
            "delivery_presentations", {}), {})
        # The same connection path still serves the next call, whole.
        result, shown = self.tool("present_delivery", self.present_arguments())
        self.assertEqual(shown["status"], "delivery_presented")
        self.assertEqual(self.deliveries(), {})
        self.assert_nothing_performed()


class SetupContractTests(unittest.TestCase):

    def test_public_reachability_is_data_and_not_performed(self):
        contract = server_module.PUBLIC_REACHABILITY
        self.assertEqual(contract["status"], "not_configured")
        self.assertIs(contract["performed_by_this_code"], False)
        self.assertEqual(contract["bind_address"], "127.0.0.1")
        actions = contract["required_human_actions"]
        self.assertEqual([a["id"] for a in actions], [
            "provision_bearer_token", "provision_public_https",
            "register_connector", "run_live_acceptance"])
        for action in actions:
            self.assertTrue(action["action"])
            self.assertIs(action["performed_by_this_code"], False)
        # X1: the check is implemented here; only the credential is a
        # human step, and nothing describes a deferred engineering change.
        access = contract["access_control"]
        self.assertEqual(access["implemented"], "bearer_token")
        self.assertIn("--auth-token-file", access["configuration"])
        self.assertIn("approves nothing", access["scope"])
        everything = json.dumps(contract)
        for stale in ("separate, authorized change", "decide_access_control"):
            self.assertNotIn(stale, everything)
        forwarder = contract["forwarder_contract"]
        self.assertEqual(forwarder["host_header"], "127.0.0.1:<port>")
        self.assertIs(forwarder["origin_header"], None)
        self.assertEqual(forwarder["authorization_header"],
                         "forwarded unchanged")

    def test_vendor_evidence_keeps_the_three_sources_apart(self):
        """X2: Grok Bot connector support, the SEPARATE xAI API remote-MCP
        surface, and what this repository implemented and tested. Grok Bot's
        SEPARATE local-computer capability is kept apart and limited to
        documentation."""
        evidence = server_module.PUBLIC_REACHABILITY["vendor_evidence"]
        self.assertEqual(sorted(evidence), ["grok_bot_connectors",
                                            "grok_bot_local_computer",
                                            "xai_api_remote_mcp"])
        local = evidence["grok_bot_local_computer"]
        self.assertEqual(local["sources"], [
            "https://docs.x.ai/grok-bot/computer-and-apps",
            "https://docs.x.ai/grok-bot/approvals-security-and-privacy"])
        for phrase in ("separate capabilities", "2026-10-08", "2026-10-06",
                       "default Ask every time"):
            self.assertIn(phrase, local["states"])
        self.assertIn("vendor documentation only", local["limits"])
        self.assertIn("not proof", local["limits"])
        for url in evidence["grok_bot_connectors"]["sources"]:
            self.assertTrue(url.startswith(("https://docs.x.ai/grok-bot/",
                                            "https://docs.x.ai/grok/")), url)
        api = evidence["xai_api_remote_mcp"]
        self.assertEqual(api["sources"],
                         ["https://docs.x.ai/developers/tools/remote-mcp"])
        self.assertIn("not the Grok Bot surface", api["applies_to"])
        tested = server_module.PUBLIC_REACHABILITY["implemented_and_tested"]
        self.assertEqual(tested["mcp_revisions"], list(mcp.PROTOCOL_VERSIONS))
        self.assertIn("protocol shape only", tested["evidence"])

    def test_reachability_is_not_described_as_cloud_only(self):
        """The cloud-only characterization is
        corrected, and the Quick Tunnel's SSE limit is stated with the SSE
        branch kept."""
        contract = server_module.PUBLIC_REACHABILITY
        self.assertNotIn("not on this Mac", contract["reason"])
        self.assertIn("LOCAL-COMPUTER execution is a SEPARATE capability",
                      contract["reason"])
        self.assertIn("public HTTPS URL", contract["reason"])
        self.assertIn("JSON answer is the one carried",
                      contract["forwarder_contract"]["quick_tunnel_sse"])
        doc = " ".join(server_module.__doc__.split())
        self.assertIn("Quick Tunnels do not support Server-Sent Events", doc)
        self.assertIn("The SSE branch stays", doc)

    def test_the_advertised_revisions_are_exactly_the_tested_ones(self):
        """Each advertised revision is negotiated, accepted in its header
        and has its batch refusal checked by ProtocolTests and
        TransportTests. Nothing else is advertised: not 2025-03-26 (which
        requires batch reception, deliberately not implemented) and not the
        modern 2026-07-28."""
        self.assertEqual(mcp.PROTOCOL_VERSIONS, ("2025-11-25", "2025-06-18"))
        for absent in ("2025-03-26", "2026-07-28", "2024-11-05"):
            self.assertNotIn(absent, mcp.PROTOCOL_VERSIONS)
        self.assertEqual(server_module.PUBLIC_REACHABILITY[
            "implemented_and_tested"]["mcp_revisions"], list(mcp.PROTOCOL_VERSIONS))

    def test_the_march_batching_gap_is_a_named_live_dependency(self):
        live = server_module.PUBLIC_REACHABILITY["live_compatibility"]
        gaps = dict((gap["id"], gap) for gap in live["known_gaps"])
        gap = gaps["mcp_2025_03_26_batch_reception"]
        self.assertIn("2025-03-26", gap["applies_if"])
        self.assertIn("batch", gap["gap"])
        self.assertIn("not implemented", gap["gap"])
        self.assertIn("batch reception", gap["minimum_follow_up"])
        self.assertIs(gap["covered_by_advertised_revisions"], False)

    def test_the_live_bot_revision_is_an_explicit_unknown(self):
        live = server_module.PUBLIC_REACHABILITY["live_compatibility"]
        self.assertEqual(live["status"], "unverified")
        unknowns = " ".join(live["unknown"])
        for phrase in ("MCP revision", "Authorization", "Origin", "Host",
                       "timeout", "whole"):
            self.assertIn(phrase, unknowns)
        self.assertIn("run_live_acceptance", live["resolved_by"])

    def test_the_document_states_every_human_action_and_unknown(self):
        text = (REPO_ROOT / "docs" / "grok-bot.md").read_text(encoding="utf-8")
        for action in server_module.PUBLIC_REACHABILITY["required_human_actions"]:
            self.assertIn("`%s`" % action["id"], text)
        for phrase in ("127.0.0.1", "loopback", "operator-attested",
                       "not cryptographically authenticated", "grokbot.py",
                       "serve", "--auth-token-file", "2025-11-25",
                       "2025-06-18", "`mcp_2025_03_26_batch_reception`",
                       "batch reception", "protocol shape only", "approves nothing",
                       "developers/tools/remote-mcp", "not the Grok Bot",
                       "nothing is truncated"):
            self.assertIn(phrase, text)
        self.assertNotIn("separate, authorized change", text)


# ====================================================================
# D1 (task d9e17d): no response but the request reply ever carries a
#     control capability, including over the MCP tools/call payload
# ====================================================================

RECOVERY_CONV = "grok-conversation-private-mcp"


class D1NoSecretExposureTests(DeliveryFixture):
    """Every tool's exact MCP reply, refusals, display text and index-
    validation errors included, carries none of D1's secret material: the
    control capability (except in the request reply itself), its sealed
    form, the conversation_ref, the origin digest (the index entry key) or
    the derived seal key. The request TEXT is not secret: it reaches the
    Operator and may appear in the proposal.

    The three delivery tools run under ``test_grok_bot_delivery``'s
    STRUCTURAL containment (``DeliveryFixture``): every process seam
    (subprocess entries, ``os`` process primitives, the real transport's
    runners) raises and is asserted unreached in cleanup, and pr_delivery
    runs over the recording double. No git or gh process can start.
    ``mcp.handle`` is pure message handling: no socket is opened."""

    def tool_reply(self, name, arguments):
        status, reply = mcp.handle(self.adapter, rpc(
            "tools/call", {"name": name, "arguments": arguments}))
        self.assertEqual(status, 200)
        self.assertIn("result", reply, reply)
        return reply, json.loads(reply["result"]["content"][-1]["text"])

    def requested_with_recovery(self):
        reply, out = self.tool_reply("request", {
            "text": PROSE, "conversation_ref": RECOVERY_CONV})
        self.assertIn(out["control_capability"], json.dumps(reply))
        # Retained: the first reply is checked too (round-2 review), with
        # only the capability and its body allowed in it.
        self.first_reply = json.dumps(reply)
        return out

    # The ONLY secrets the first successful request reply may carry.
    FIRST_REPLY_ALLOWED = ("control capability", "capability body")

    def assert_first_reply_carries_only_the_capability(self, secrets):
        for label, secret in sorted(secrets.items()):
            if label in self.FIRST_REPLY_ALLOWED:
                continue
            with self.subTest(reply="first request reply", secret=label):
                self.assertNotIn(secret, self.first_reply)

    def secrets(self, out):
        """Every D1 secret, by name. The sealed form is read from the index
        file, where it legitimately lives."""
        token = out["control_capability"]
        origin = {"text": PROSE, "conversation_ref": RECOVERY_CONV}
        [entry] = json.loads(self.file_bytes(index_module.INDEX_FILE_NAME))[
            "requests"].values()
        found = {
            "control capability": token,
            "capability body": token[len("lc-"):],
            "sealed capability": entry["recovery"]["sealed_capability"],
            "conversation_ref": RECOVERY_CONV,
            "origin digest (index key)": adapter_module.json_digest(origin),
            "seal key": index_module.seal_key(origin).hex(),
        }
        self.assertIn(found["origin digest (index key)"], self.file_bytes(
            index_module.INDEX_FILE_NAME).decode("ascii"))
        return found

    def sweep(self, out):
        """One call per tool, and more where a tool has distinct paths."""
        ref = out["request_ref"]
        recovery = {"request_ref": ref, "text": PROSE,
                    "conversation_ref": RECOVERY_CONV}
        calls = [
            ("request", {"text": PROSE, "conversation_ref": RECOVERY_CONV}),
            ("present", {"request_ref": ref}),
            ("approve", {"request_ref": ref, "revision": 9}),
            ("status", {"request_ref": ref}),
            ("recover", {"request_ref": ref}),
            ("cancel", {"request_ref": ref,
                        "control_capability": "lc-" + "0" * 64}),
            ("cancel", {"request_ref": ref}),
            ("cancel", dict(recovery, conversation_ref="someone-else")),
            ("cancel", dict(recovery, control_capability=out["control_capability"])),
            ("run", {"request_ref": ref, "command": "dispatch",
                     "arguments": {}}),
            ("run", {"request_ref": ref, "command": "observe",
                     "arguments": {"x": 1}}),
            ("present_delivery", self.present_arguments()),
            ("delivery_status", {"delivery_id": "prd-unknown"}),
        ]
        replies = [(name, arguments) + self.tool_reply(name, arguments)
                   for name, arguments in calls]
        shown = replies[11][3]
        if shown.get("ok"):
            replies.append(("approve_delivery", None) + self.tool_reply(
                "approve_delivery", self.delivery_approval(shown)))
            approved = replies[-1][3]
            if approved.get("ok"):
                replies.append(("delivery_status", None) + self.tool_reply(
                    "delivery_status", {"delivery_id": approved["delivery_id"]}))
        else:
            replies.append(("approve_delivery", None) + self.tool_reply(
                "approve_delivery", {"proposal_digest_sha256": "f" * 64,
                                     "expires_at": 1}))
        for name, arguments in (("cancel", recovery), ("status", {"request_ref": ref}),
                                ("recover", {"request_ref": ref}),
                                ("cancel", recovery), ("present", {})):
            replies.append((name, arguments) + self.tool_reply(name, arguments))
        return replies

    def assert_no_secret(self, replies, secrets):
        listed = json.dumps(mcp.handle(self.adapter, rpc("tools/list"))[1])
        for name, _, reply, payload in replies + [("tools/list", None, listed, {})]:
            text = reply if isinstance(reply, str) else json.dumps(reply)
            for label, secret in sorted(secrets.items()):
                with self.subTest(tool=name, problem=payload.get("problem"),
                                  secret=label):
                    self.assertNotIn(secret, text)
            self.assertNotIn("control_capability_recovery", payload)

    def test_D1_only_the_request_reply_carries_the_capability(self):
        out = self.requested_with_recovery()
        secrets = self.secrets(out)
        self.assertEqual(len(secrets), 6)
        self.assert_first_reply_carries_only_the_capability(secrets)
        replies = self.sweep(out)
        self.assertEqual(sorted(set(r[0] for r in replies)),
                         sorted(adapter_module.TOOLS))
        cancelled = [r[3] for r in replies
                     if r[0] == "cancel" and r[3].get("ok")]
        self.assertEqual([c["cancelled_through"] for c in cancelled],
                         ["origin_recovery"])
        delivery = [r[3] for r in replies if r[0].endswith("_delivery")]
        self.assertTrue(all(p["ok"] for p in delivery), delivery)
        self.assert_no_secret(replies, secrets)
        self.assert_nothing_performed()
        self.assertEqual(self.seams.take(), [])

    def test_D1_index_validation_errors_expose_no_origin_material(self):
        out = self.requested_with_recovery()
        secrets = self.secrets(out)
        self.assert_first_reply_carries_only_the_capability(secrets)
        path = os.path.join(self.state, index_module.INDEX_FILE_NAME)
        with open(path) as handle:
            original = json.load(handle)
        [(key, entry)] = original["requests"].items()

        def corrupt(mutate):
            document = json.loads(json.dumps(original))
            mutate(document)
            with open(path, "w") as handle:
                json.dump(document, handle)

        corruptions = (
            ("malformed recovery material", lambda d: d["requests"][key][
                "recovery"].update(seal="plain")),
            ("malformed entry", lambda d: d["requests"][key].update(
                recorded_at=-1)),
            ("unknown entry key", lambda d: d["requests"][key].update(extra=1)),
            ("a secret stored as the key", lambda d: d["requests"].update(
                {RECOVERY_CONV: d["requests"].pop(key)})),
            ("the origin digest as a bad key", lambda d: d["requests"].update(
                {key.upper(): d["requests"].pop(key)})),
        )
        for label, mutate in corruptions:
            with self.subTest(corruption=label):
                corrupt(mutate)
                replies = self.sweep(out)
                errors = [r for r in replies
                          if r[3].get("problem") == index_module.PROBLEM_UNREADABLE]
                self.assertTrue(any(r[0] == "request" for r in errors), replies)
                self.assertTrue(any(r[0] == "cancel" for r in errors), replies)
                secrets_here = dict(secrets, upper_key=key.upper())
                self.assert_no_secret(replies, secrets_here)
        self.assert_nothing_performed()
        self.assertEqual(self.seams.take(), [])


def first_reply_detection(outcome, sweep, label, value):
    """``(detected, why)`` for one planted run of the sweep test ``sweep``.

    Detected ONLY when the run's sole problem is ONE failure, raised by the
    sweep's own first-reply assertion for exactly the planted secret
    (subTest ``reply="first request reply", secret=label``), whose message
    shows the planted value as unexpectedly found. Any error, any other or
    additional failure (a containment or unrelated assertion, a later
    reply's check), or a message without the value is NOT detection
    (round-3 review: any failure used to count)."""
    if outcome.errors:
        return False, "errors: %s" % [test.id() for test, _ in outcome.errors]
    if len(outcome.failures) != 1:
        return False, "%d failures: %s" % (
            len(outcome.failures), [test.id() for test, _ in outcome.failures])
    [(test, trace)] = outcome.failures
    case = getattr(test, "test_case", None)
    if case is None or not case.id().endswith(sweep):
        return False, "the failure is not a subtest of the sweep: %s" % test.id()
    params = dict(getattr(test, "params", {}))
    if params != {"reply": "first request reply", "secret": label}:
        return False, "the failure is another assertion: %r" % (params,)
    if value not in trace or "unexpectedly found" not in trace:
        return False, "the failure does not show the planted value"
    return True, "the planted secret's own first-reply assertion failed"


class D1FirstReplyProbeTests(unittest.TestCase):
    """Round-2 review: the FIRST successful request reply is the one reply
    allowed to carry the capability, and it must carry none of the other
    four secrets. Planted probes: a mutant adapter that adds one of them to
    that first reply ONLY (never to a duplicate) must make the sweep test
    fail, and (round-3 review) fail BECAUSE of that secret's own first-reply
    assertion: ``first_reply_detection`` counts nothing else as detection.
    A plain TestCase (no watchdog of its own), like the mutation
    self-checks: every inner test keeps its own SIGALRM watchdog."""

    SWEEP = ("D1NoSecretExposureTests."
             "test_D1_only_the_request_reply_carries_the_capability")
    LABELS = ("conversation_ref", "origin digest (index key)", "seal key",
              "sealed capability")

    def leaking(self, label, planted, where="first"):
        """A mutant ``_request``. ``where``: plant ``label``'s value into the
        first successful reply ("first") or into duplicate replies only
        ("later"); or raise an unrelated error on the first one ("raise")."""
        original = adapter_module.GrokBotAdapter._request

        def _request(adapter, text=None, conversation_ref=None,
                     operator_session_id=None):
            result = original(adapter, text=text,
                              conversation_ref=conversation_ref,
                              operator_session_id=operator_session_id)
            first = result.get("duplicate") is False and (
                "control_capability" in result)
            if where == "raise" and first:
                raise RuntimeError("synthetic failure unrelated to any secret")
            if (where == "first" and first) or (
                where == "later" and result.get("duplicate") is True
            ):
                origin = {"text": text, "conversation_ref": conversation_ref}
                digest = adapter_module.json_digest(origin)
                planted["value"] = result["planted"] = {
                    "conversation_ref": conversation_ref,
                    "origin digest (index key)": digest,
                    "seal key": index_module.seal_key(origin).hex(),
                    "sealed capability": adapter._index.entry(digest)[
                        "recovery"]["sealed_capability"],
                }[label]
            return result
        return _request

    def run_sweep(self, mutant=None):
        module = sys.modules[type(self).__module__]
        outcome = unittest.TestResult()
        patch = (mock.patch.object(adapter_module.GrokBotAdapter, "_request",
                                   mutant) if mutant else contextlib.nullcontext())
        with patch:
            unittest.defaultTestLoader.loadTestsFromName(self.SWEEP, module).run(
                outcome)
        return outcome

    def test_D1_a_secret_in_the_first_reply_fails_the_sweep(self):
        for label in self.LABELS:
            with self.subTest(planted=label):
                planted = {}
                outcome = self.run_sweep(self.leaking(label, planted))
                self.assertIn("value", planted, "the mutant planted nothing")
                detected, why = first_reply_detection(
                    outcome, self.SWEEP, label, planted["value"])
                self.assertTrue(detected, (label, why, outcome.failures,
                                           outcome.errors))
        # Negative control: nothing planted, the sweep passes.
        outcome = self.run_sweep()
        self.assertTrue(outcome.wasSuccessful(),
                        (outcome.failures, outcome.errors))

    def test_D1_an_unrelated_failure_is_never_counted_as_detection(self):
        """The vacuous-pass guard (round-3 review). Each mutant makes the
        sweep FAIL without the planted secret's first-reply assertion
        firing: the round-3 criterion (any failure) counted both as
        detection; ``first_reply_detection`` counts neither."""
        for where in ("raise", "later"):
            with self.subTest(mutant=where):
                planted = {}
                outcome = self.run_sweep(self.leaking(
                    "conversation_ref", planted, where))
                self.assertFalse(outcome.wasSuccessful())
                detected, why = first_reply_detection(
                    outcome, self.SWEEP, "conversation_ref", RECOVERY_CONV)
                self.assertFalse(detected, why)


class D1ClientContractTests(Fixture):
    """Round-2 review: what the live client actually receives must state the
    real prerequisite of cancel recovery. Its only protection is a PRIVATE,
    UNGUESSABLE, RANDOM conversation_ref (the request text is not secret);
    a visible or sequential identifier satisfies uniqueness and defeats it."""

    PHRASES = ("private", "unguessable", "random",
               "never a visible or sequential")

    def served(self):
        status, init = mcp.handle(self.adapter, rpc("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "contract-test", "version": "0"}}))
        self.assertEqual(status, 200)
        tools = dict((t["name"], t) for t in mcp.handle(
            self.adapter, rpc("tools/list"))[1]["result"]["tools"])
        return tools, {
            "initialize instructions": init["result"]["instructions"],
            "request.conversation_ref": tools["request"]["inputSchema"][
                "properties"]["conversation_ref"]["description"],
            "cancel.conversation_ref": tools["cancel"]["inputSchema"][
                "properties"]["conversation_ref"]["description"],
        }

    def test_D1_the_client_contract_requires_a_private_unguessable_random_ref(self):
        _, strings = self.served()
        for surface, text in sorted(strings.items()):
            flat = " ".join(text.split())
            for phrase in self.PHRASES:
                with self.subTest(surface=surface, phrase=phrase):
                    self.assertIn(phrase, flat)

    def test_D1_a_client_facing_mention_of_uniqueness_also_states_unguessability(self):
        """A LEXICAL tripwire, not a semantic guarantee (round-3 review): it
        fails if any served string, or any string literal in grok_bot/mcp.py,
        mentions "unique" without also saying "unguessable". That catches a
        reintroduction of the round-2 wording; a string that says both and
        still treats uniqueness as sufficient would pass it."""
        tools, strings = self.served()
        served = list(strings.values()) + [t["description"] for t in tools.values()]
        source = ast.parse((REPO_ROOT / "grok_bot" / "mcp.py").read_text())
        literals = [node.value for node in ast.walk(source)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)]
        for text in served + literals:
            if "unique" in text.lower():
                with self.subTest(text=" ".join(text.split())[:80]):
                    self.assertIn("unguessable", text)


# ====================================================================
# D3 (task d9e17d): the run tool's schema exposes each command's exact
#     arguments, derived from adapter.RUN_ARGUMENTS
# ====================================================================

RUN_COMMANDS = adapter_module.surface_module.LocalRequestSurface.RUN_COMMANDS
# The test's OWN reading of the adapter's argument kinds (None: any value).
JSON_TYPE_OF_KIND = {str: "string", dict: "object", None: None}
ABSENT = object()


def json_type_holds(value, name):
    return {
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }[name]


def schema_admits(schema, value, root=None):
    """The JSON Schema subset the tool schemas use: type, const, enum,
    properties, required, additionalProperties (false or a schema), items,
    oneOf, anyOf, local ``$ref`` into ``$defs``, and the empty schema (any
    value). Anything else in a schema fails this test loudly."""
    root = schema if root is None else root
    known = {"type", "const", "enum", "properties", "required",
             "additionalProperties", "oneOf", "anyOf", "items", "$ref",
             "$defs", "description", "maxLength"}
    unknown = set(schema) - known
    assert not unknown, unknown
    if "$ref" in schema:
        prefix = "#/$defs/"
        assert schema["$ref"].startswith(prefix), schema["$ref"]
        if not schema_admits(root["$defs"][schema["$ref"][len(prefix):]],
                             value, root):
            return False
    if "anyOf" in schema and not any(schema_admits(sub, value, root)
                                     for sub in schema["anyOf"]):
        return False
    if isinstance(value, list) and "items" in schema and not all(
        schema_admits(schema["items"], item, root) for item in value
    ):
        return False
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [
            schema["type"]]
        if not any(json_type_holds(value, t) for t in types):
            return False
    if "const" in schema and not (type(value) is type(schema["const"])
                                  and value == schema["const"]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str) and "maxLength" in schema and (
        len(value) > schema["maxLength"]
    ):
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(name not in value for name in schema.get("required", [])):
            return False
        extra = set(value) - set(properties)
        additional = schema.get("additionalProperties", {})
        if additional is False and extra:
            return False
        if isinstance(additional, dict) and not all(
            schema_admits(additional, value[name], root) for name in extra
        ):
            return False
        if any(name in value and not schema_admits(sub, value[name], root)
               for name, sub in properties.items()):
            return False
    if "oneOf" in schema and sum(
        1 for sub in schema["oneOf"] if schema_admits(sub, value, root)
    ) != 1:
        return False
    return True


def nested(depth, kind=list):
    """A value whose containers nest exactly ``depth`` deep (a scalar at 0)."""
    value = "leaf"
    for _ in range(depth):
        value = [value] if kind is list else {"k": value}
    return value


class StubRunSurface(object):
    """Records every run command that passed the adapter's shape checks."""

    def __init__(self):
        self.calls = []

    def run_command(self, request_ref, command, **arguments):
        self.calls.append((request_ref, command, arguments))
        return {"ok": True, "status": command}


class D3RunSchemaTests(Fixture):

    def run_tool(self):
        [run] = [t for t in mcp.tool_definitions() if t["name"] == "run"]
        return run

    def variants(self, run=None):
        schema = (run or self.run_tool())["inputSchema"]
        return dict((v["properties"]["command"]["const"], v)
                    for v in schema["oneOf"])

    def test_D3_each_commands_arguments_are_exactly_RUN_ARGUMENTS(self):
        self.assertLessEqual(set(adapter_module.RUN_ARGUMENTS), set(RUN_COMMANDS))
        variants = self.variants()
        self.assertEqual(sorted(variants), sorted(RUN_COMMANDS))
        for command in RUN_COMMANDS:
            with self.subTest(command=command):
                expected = adapter_module.RUN_ARGUMENTS.get(command, {})
                optional = adapter_module.RUN_OPTIONAL_ARGUMENTS.get(command, {})
                variant = variants[command]
                arguments = variant["properties"]["arguments"]
                self.assertIs(arguments["additionalProperties"], False)
                self.assertEqual(sorted(arguments["properties"]),
                                 sorted(set(expected) | set(optional)))
                self.assertEqual(sorted(arguments["required"]), sorted(expected))
                for name, kind in list(expected.items()) + list(optional.items()):
                    self.assertEqual(arguments["properties"][name].get("type"),
                                     JSON_TYPE_OF_KIND[kind])
                self.assertEqual("arguments" in variant.get("required", []),
                                 bool(expected))
        # Task 8 final: an ordinary dispatch takes no path; the optional
        # workspace_path is the operator recovery override, stated so.
        dispatch = variants["dispatch"]["properties"]["arguments"]
        self.assertEqual(dispatch["properties"]["workspace_path"]["type"], "string")
        self.assertEqual(dispatch["required"], [])
        self.assertNotIn("required", variants["dispatch"])
        self.assertIn("operator recovery only",
                      dispatch["properties"]["workspace_path"]["description"])
        self.assertIn("workspace_path", self.run_tool()["description"])
        self.assertIn("never ask the human for one", self.run_tool()["description"])

    def test_D3_the_schema_follows_RUN_ARGUMENTS_when_it_changes(self):
        surface = adapter_module.surface_module.LocalRequestSurface
        with mock.patch.dict(adapter_module.RUN_ARGUMENTS,
                             {"observe": {"since": str}}), \
                mock.patch.object(surface, "RUN_COMMANDS",
                                  RUN_COMMANDS + ("rewind",)):
            run = self.run_tool()
            variants = self.variants(run)
            self.assertEqual(variants["observe"]["properties"]["arguments"][
                "required"], ["since"])
            self.assertEqual(variants["rewind"]["properties"]["arguments"][
                "properties"], {})
            self.assertIn("rewind", run["inputSchema"]["properties"]["command"][
                "enum"])
        with mock.patch.dict(adapter_module.RUN_ARGUMENTS,
                             {"observe": {"count": int}}):
            # A kind the schema cannot state exactly is refused, never
            # silently widened.
            with self.assertRaises(ValueError):
                mcp.tool_definitions()
        self.assertNotIn("since", json.dumps(self.run_tool()))

    def corpus(self, command):
        names = sorted(set(n for table in (adapter_module.RUN_ARGUMENTS,
                                           adapter_module.RUN_OPTIONAL_ARGUMENTS)
                           for args in table.values() for n in args))
        samples = {str: "/abs/workspace", dict: {"k": 1}, None: {"task_id": "t"}}
        wrong = (7, None, ["x"], True, "text", {"k": 1})
        expected = adapter_module.RUN_ARGUMENTS.get(command, {})
        optional = adapter_module.RUN_OPTIONAL_ARGUMENTS.get(command, {})
        exact = dict((n, samples[k]) for n, k in expected.items())
        values = [ABSENT, None, {}, [], "x", 1, True, exact]
        for name in expected:
            values.append(dict((n, v) for n, v in exact.items() if n != name))
            for bad in wrong:
                values.append(dict(exact, **{name: bad}))
        # Task 8 final: an optional argument is admitted with its kind and
        # refused with any other, present or absent beside the required.
        for name, kind in optional.items():
            for value in (samples[kind],) + wrong:
                values.append(dict(exact, **{name: value}))
        for name in names:
            if name not in expected and name not in optional:
                values.append(dict(exact, **{name: "extra"}))
        # Round-1 review: the adapter refuses any call nesting containers
        # more than framing.MAX_NESTING_DEPTH deep, counting the call itself
        # (depth 1) and its ``arguments`` object (depth 2). Probe both sides
        # of that boundary inside every argument that can hold containers,
        # and deep extras where no argument may appear.
        room = adapter_module.framing.MAX_NESTING_DEPTH - 2
        for name, kind in expected.items():
            for depth in (room - 1, room, room + 1, room + 3):
                for shape in (list, dict):
                    if kind is None:
                        values.append(dict(exact, **{name: nested(depth, shape)}))
                    elif kind is dict:
                        values.append(dict(exact, **{name: {
                            "a": nested(depth - 1, shape)}}))
        values.append({"deep": nested(room + 3)})
        return values

    def test_D3_the_schema_admits_exactly_what_the_adapter_admits(self):
        stub = StubRunSurface()
        adapter = adapter_module.GrokBotAdapter(
            stub, None, str(REPO_ROOT), index_module.RequestIndex(self.state),
            self.clock)
        schema = self.run_tool()["inputSchema"]
        checked = 0
        for command in RUN_COMMANDS:
            for value in self.corpus(command):
                call = {"request_ref": "lr-" + "1" * 32, "command": command}
                if value is not ABSENT:
                    call["arguments"] = value
                with self.subTest(command=command, arguments=repr(value)):
                    admitted_by_schema = schema_admits(schema, call)
                    before = len(stub.calls)
                    result = adapter.run(**call)
                    admitted_by_adapter = result["ok"]
                    if not admitted_by_adapter:
                        self.assertEqual(result["problem"], "grok_bot_bad_request")
                    else:
                        self.assertEqual(len(stub.calls), before + 1)
                    self.assertEqual(admitted_by_schema, admitted_by_adapter)
                    checked += 1
        self.assertGreater(checked, 150)

    def test_D3_the_nesting_bound_is_stated_at_its_boundary(self):
        """Both sides of the adapter's bound, for the two arguments that can
        hold containers, and the bound DERIVED from the adapter's own."""
        schema = self.run_tool()["inputSchema"]
        room = adapter_module.framing.MAX_NESTING_DEPTH - 2
        self.assertEqual(room, 14)

        def call(command, arguments):
            return {"request_ref": "lr-" + "1" * 32, "command": command,
                    "arguments": arguments}
        for shape in (list, dict):
            with self.subTest(shape=shape.__name__):
                self.assertTrue(schema_admits(schema, call(
                    "verify", {"reported_result": nested(room, shape)})))
                self.assertFalse(schema_admits(schema, call(
                    "verify", {"reported_result": nested(room + 1, shape)})))
                self.assertTrue(schema_admits(schema, call("prove", {
                    "operation": "x", "arguments": {"a": nested(room - 1, shape)}})))
                self.assertFalse(schema_admits(schema, call("prove", {
                    "operation": "x", "arguments": {"a": nested(room, shape)}})))
        with mock.patch.object(adapter_module.framing, "MAX_NESTING_DEPTH", 6):
            narrowed = self.run_tool()["inputSchema"]
        self.assertTrue(schema_admits(narrowed, call(
            "verify", {"reported_result": nested(4)})))
        self.assertFalse(schema_admits(narrowed, call(
            "verify", {"reported_result": nested(5)})))

    def test_D3_the_document_states_the_bound_without_contradiction(self):
        text = (REPO_ROOT / "docs" / "grok-bot.md").read_text(encoding="utf-8")
        section = " ".join(text.split(
            "### `run` commands and their arguments", 1)[1].split("\n#", 1)[0]
            .split())
        self.assertNotIn("does not state the adapter's nesting bound", section)
        self.assertIn("nesting bound", section)
        self.assertIn("MAX_NESTING_DEPTH", section)

    def test_D3_no_new_tool_and_no_new_revision(self):
        self.assertEqual(sorted(t["name"] for t in mcp.tool_definitions()),
                         sorted(adapter_module.TOOLS))
        self.assertEqual(len(adapter_module.TOOLS), 10)
        self.assertEqual(mcp.PROTOCOL_VERSIONS, ("2025-11-25", "2025-06-18"))

    def test_D3_the_document_names_each_commands_arguments(self):
        text = (REPO_ROOT / "docs" / "grok-bot.md").read_text(encoding="utf-8")
        section = text.split("### `run` commands and their arguments", 1)[1]
        section = section.split("\n#", 1)[0]
        rows = dict((line.split("|")[1].strip(), line)
                    for line in section.splitlines() if line.startswith("| `"))
        for command in RUN_COMMANDS:
            with self.subTest(command=command):
                row = rows["`%s`" % command]
                expected = adapter_module.RUN_ARGUMENTS.get(command, {})
                optional = adapter_module.RUN_OPTIONAL_ARGUMENTS.get(command, {})
                for name in list(expected) + list(optional):
                    self.assertIn("`%s`" % name, row)
                if not expected:
                    self.assertIn("none", row)
        self.assertIn("`workspace_path`", rows["`dispatch`"])
        self.assertIn("operator recovery only", rows["`dispatch`"])


class D3RunSchemaOverLoopbackTests(ServerFixture):

    def test_D3_tools_list_exposes_dispatch_without_a_required_path(self):
        tools = self.call_ok(rpc("tools/list"))["result"]["tools"]
        [run] = [t for t in tools if t["name"] == "run"]
        variants = dict((v["properties"]["command"]["const"], v)
                        for v in run["inputSchema"]["oneOf"])
        arguments = variants["dispatch"]["properties"]["arguments"]
        self.assertEqual(arguments["properties"]["workspace_path"]["type"],
                         "string")
        # Task 8 final: dispatch needs no path; the recovery override is
        # optional.
        self.assertEqual(arguments["required"], [])
        self.assertEqual(arguments["type"], ["object", "null"])
        self.assertIs(arguments["additionalProperties"], False)
        self.assertNotIn("required", variants["dispatch"])


if __name__ == "__main__":
    unittest.main()
