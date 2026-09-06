"""Streamable HTTP MCP endpoint for the Grok Bot connector (stdlib only).

``GrokMcpServer`` is a ``ThreadingHTTPServer`` serving ONE endpoint
path. It implements the legacy (initialize-handshake) Streamable HTTP
era: POST carries a single JSON-RPC request, notification, or
response; GET answers 405 (no server-initiated stream); DELETE ends a
session. SSE streaming, resumability, pagination, resources, prompts,
logging, sampling, elicitation, tasks, and OAuth are NOT implemented,
and that is stated here rather than hidden.

Every request passes these checks, in this order, before any JSON is
parsed and before the controller is reached:

1. path must equal the configured endpoint path (else 404);
2. ``Authorization: Bearer <token>`` must match the configured token
   under a constant-time comparison (else 401 with
   ``WWW-Authenticate: Bearer`` and a generic body);
3. an ``Origin`` header, when present, must be allowlisted (else 403,
   the DNS-rebinding defence the specification requires);
4. ``Content-Length`` must not exceed ``MAX_REQUEST_BYTES`` (else 413,
   before a byte of the body is read);
5. ``Accept`` must list ``application/json`` (else 406);
6. ``MCP-Protocol-Version``, when present, must be supported (else
   400). An absent header is accepted as-is: the implemented subset is
   identical across every supported revision, so no assumed default
   changes any behaviour and none is recorded;
7. every request other than ``initialize`` must carry a known
   ``Mcp-Session-Id`` (absent 400, unknown or terminated 404).

Then framing: unparseable JSON is ``-32700``; a non-object, a wrong
``jsonrpc`` member, or a non-string/non-integer id is ``-32600``; an
unknown method is ``-32601``; non-object ``params``, non-object tool
arguments, or an unknown tool name is ``-32602``. Notifications and
responses are accepted with 202 and no body. Bounded-input violations
are NOT protocol errors: they come back from the controller as tool
results with ``isError: true``.

Secret hygiene: the bearer token is compared and never copied into a
log line, a response body, a JSON-RPC error, or an exception message.
Request logging goes through an injectable writer and carries the
request line and status only. An unexpected exception inside request
handling is answered by a generic ``-32603`` / 500; one that escapes
the handler reaches ``socketserver``'s ``handle_error``, which this
server overrides. Both paths log the exception CLASS NAME only, through
the same injectable writer; no traceback and no exception text is
written anywhere by this module.

Every refusal drains the declared request body first so a keep-alive
client's next request parses cleanly; a body that cannot be drained
(oversize, negative, or non-integer ``Content-Length``) is answered
with ``Connection: close`` instead, on every method.

Authenticated ingress for the Mission tools: once the bearer check
passes, the handler builds ONE neutral ``AuthenticatedContext`` for that
request — transport ``grok_mcp``, principal kind "configured connector
credential ordinal", the ordinal of the matched credential, no
configured subject — and hands it to the controller per call. It is
built only here, only after the check, and never from anything in the
request body. It records that a configured credential was verified; it
is not proof of the human behind it.

Binding defaults to 127.0.0.1 (the CLI's default); this module creates
no tunnel and knows nothing about Grok accounts. Session ids are
random visible-ASCII tokens held in a bounded in-memory LRU table
(``MAX_SESSIONS``): a session in use is never evicted by newer idle
ones, and an evicted session answers 404 like a terminated one.
"""

import collections
import hmac
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mission import record as mission_record

from grok_mcp import controller as controller_module
from grok_mcp import protocol
from grok_mcp.protocol import MAX_REQUEST_BYTES

DEFAULT_ENDPOINT_PATH = "/mcp"
SESSION_HEADER = "Mcp-Session-Id"
VERSION_HEADER = "MCP-Protocol-Version"
BEARER_SCHEME = "bearer "

# Bound on live MCP sessions (LRU eviction). Exact-value pinned.
MAX_SESSIONS = 256

# The one configured connector credential this endpoint verifies. The
# authenticated ingress names its ORDINAL, never a Grok identity.
CONNECTOR_CREDENTIAL_ORDINAL = 1


class GrokMcpServer(ThreadingHTTPServer):
    """The single-endpoint MCP server; construction opens the socket only."""

    # Handler threads are joined by server_close, so a stopped server
    # leaves nothing running.
    daemon_threads = False

    def __init__(self, address, controller, bearer_token,
                 endpoint_path=DEFAULT_ENDPOINT_PATH, allowed_origins=(),
                 log_writer=None):
        self.controller = controller
        self._bearer_token = bearer_token
        self.endpoint_path = endpoint_path
        self.allowed_origins = tuple(allowed_origins)
        self.log_writer = log_writer or sys.stderr.write
        self.sessions = collections.OrderedDict()
        self.sessions_lock = threading.Lock()
        ThreadingHTTPServer.__init__(self, address, GrokMcpRequestHandler)

    def handle_error(self, request, client_address):
        """An exception escaped a handler: class name only, to the writer."""
        exc = sys.exc_info()[1]
        self.log_writer(
            "handler error %s\n" % (type(exc).__name__ if exc else "unknown")
        )

    def bearer_matches(self, supplied):
        if not isinstance(supplied, str) or not self._bearer_token:
            return False
        return hmac.compare_digest(
            supplied.encode("utf-8"), self._bearer_token.encode("utf-8")
        )

    def new_session(self, protocol_version):
        session_id = secrets.token_hex(16)
        with self.sessions_lock:
            self.sessions[session_id] = protocol_version
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.popitem(last=False)
        return session_id

    def has_session(self, session_id):
        with self.sessions_lock:
            if session_id not in self.sessions:
                return False
            # LRU: a session in use is never evicted by newer idle ones.
            self.sessions.move_to_end(session_id)
            return True

    def drop_session(self, session_id):
        with self.sessions_lock:
            return self.sessions.pop(session_id, None) is not None


def _jsonrpc_error(request_id, code, message):
    return {
        "jsonrpc": protocol.JSONRPC_VERSION,
        "id": request_id,
        "error": protocol.error_object(code, message),
    }


def _jsonrpc_result(request_id, result):
    return {
        "jsonrpc": protocol.JSONRPC_VERSION, "id": request_id,
        "result": result,
    }


def _valid_id(value):
    return isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


class GrokMcpRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DodgingInfinityMCP/1"
    sys_version = ""
    # Set per request by ``_gate`` after the bearer check; never before.
    ingress = None

    # -- logging: request line and status only -------------------------

    def log_message(self, format, *args):
        self.server.log_writer((format % args) + "\n")

    # -- replies ----------------------------------------------------------

    def _reply(self, status, body, content_type="text/plain; charset=utf-8",
               headers=(), close=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        if close:
            # The request body could not be drained, so the connection
            # must not be reused: its tail would parse as a request.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _reply_json(self, payload, headers=()):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self._reply(200, body, "application/json", headers)

    def _reply_empty(self, status, headers=()):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()

    # -- gate checks shared by every method -------------------------------

    def _gate(self):
        """The pre-parse checks; returns True when the request may proceed.

        Every refusal drains the declared body first so a keep-alive
        client's next request is parsed cleanly; a body that cannot be
        drained (oversize or undeclared) closes the connection instead.
        """
        # A handler instance serves every request on a keep-alive
        # connection: clear the previous request's ingress first, so a
        # refused request can never inherit an earlier context.
        self.ingress = None
        if self.path != self.server.endpoint_path:
            self._refuse(404, b"not found")
            return False
        authorization = self.headers.get("Authorization", "")
        supplied = None
        # RFC 7235: the auth scheme is case-insensitive.
        if authorization[:len(BEARER_SCHEME)].lower() == BEARER_SCHEME:
            supplied = authorization[len(BEARER_SCHEME):].strip()
        if not self.server.bearer_matches(supplied):
            self._refuse(401, b"unauthorized",
                         headers=(("WWW-Authenticate", "Bearer"),))
            return False
        # The bearer check passed: this request's authenticated ingress.
        self.ingress = mission_record.AuthenticatedContext(
            transport=protocol.SOURCE,
            principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
            principal_ref=str(CONNECTOR_CREDENTIAL_ORDINAL),
            configured_subject=None,
        )
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip() not in self.server.allowed_origins:
            self._refuse(403, b"forbidden origin")
            return False
        return True

    def _refuse(self, status, body, headers=()):
        """Reply without processing; drains first, closes if it cannot."""
        self._reply(status, body, headers=headers, close=not self._drain())

    def _drain(self):
        """Discard the declared body; True when the connection is clean."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return True
        try:
            length = int(raw)
        except ValueError:
            return False
        if length < 0 or length > MAX_REQUEST_BYTES:
            return False
        if length > 0:
            self.rfile.read(length)
        return True

    # -- HTTP methods ------------------------------------------------------

    def do_GET(self):
        if not self._gate():
            return
        self._refuse(405, b"method not allowed",
                     headers=(("Allow", "POST, DELETE"),))

    def do_DELETE(self):
        if not self._gate():
            return
        close = not self._drain()
        session_id = self.headers.get(SESSION_HEADER)
        if session_id is None:
            self._reply(400, b"missing session", close=close)
            return
        if not self.server.drop_session(session_id.strip()):
            self._reply(404, b"unknown session", close=close)
            return
        self._reply(200, b"", close=close)

    def do_POST(self):
        try:
            self._post()
        except Exception as exc:  # generic; class name only
            self.log_message("handler failure %s", type(exc).__name__)
            try:
                self._reply(500, b"internal error")
            except Exception:
                pass

    def _post(self):
        if not self._gate():
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply(411, b"length required", close=True)
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            # Not read, so not drainable: close rather than let the
            # tail be parsed as the next request.
            self._reply(413, b"request too large", close=True)
            return
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept and "*/*" not in accept:
            self._drain()
            self._reply(406, b"accept must include application/json")
            return
        version = self.headers.get(VERSION_HEADER)
        if version is not None and (
            version.strip() not in protocol.SUPPORTED_PROTOCOL_VERSIONS
        ):
            self._drain()
            self._reply(400, b"unsupported protocol version")
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            message = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._reply_json(_jsonrpc_error(
                None, protocol.PARSE_ERROR, "parse error"
            ))
            return
        if not isinstance(message, dict) or (
            message.get("jsonrpc") != protocol.JSONRPC_VERSION
        ):
            self._reply_json(_jsonrpc_error(
                None, protocol.INVALID_REQUEST, "invalid request"
            ))
            return
        method = message.get("method")
        has_id = "id" in message
        if has_id and not _valid_id(message["id"]):
            self._reply_json(_jsonrpc_error(
                None, protocol.INVALID_REQUEST, "invalid request id"
            ))
            return
        request_id = message.get("id")
        if method is None:
            # A JSON-RPC response from the client; accepted, unused.
            self._reply_empty(202)
            return
        if not isinstance(method, str):
            self._reply_json(_jsonrpc_error(
                request_id, protocol.INVALID_REQUEST, "invalid request"
            ))
            return
        # Session gate: everything but initialize needs a known session.
        if method == protocol.METHOD_INITIALIZE:
            params = message.get("params")
            requested = None
            if isinstance(params, dict):
                requested = params.get("protocolVersion")
            negotiated = protocol.negotiate_version(requested)
            if not has_id:
                self._reply_empty(202)
                return
            session_id = self.server.new_session(negotiated)
            self._reply_json(
                _jsonrpc_result(request_id, protocol.initialize_result(requested)),
                headers=((SESSION_HEADER, session_id),),
            )
            return
        session_id = self.headers.get(SESSION_HEADER)
        if session_id is None:
            self._reply(400, b"missing session")
            return
        if not self.server.has_session(session_id.strip()):
            self._reply(404, b"unknown session")
            return
        if not has_id:
            # Notification (notifications/initialized or any other).
            self._reply_empty(202)
            return
        self._reply_json(self._dispatch(request_id, method, message))

    def _dispatch(self, request_id, method, message):
        params = message.get("params")
        if method == protocol.METHOD_PING:
            return _jsonrpc_result(request_id, protocol.ping_result())
        if method == protocol.METHOD_TOOLS_LIST:
            return _jsonrpc_result(request_id, protocol.tools_list_result())
        if method != protocol.METHOD_TOOLS_CALL:
            return _jsonrpc_error(
                request_id, protocol.METHOD_NOT_FOUND, "method not found"
            )
        if not isinstance(params, dict):
            return _jsonrpc_error(
                request_id, protocol.INVALID_PARAMS, "params must be an object"
            )
        try:
            result = self.server.controller.call_tool(
                params.get("name"), params.get("arguments"),
                ingress=self.ingress,
            )
        except controller_module.UnknownToolError:
            return _jsonrpc_error(
                request_id, protocol.INVALID_PARAMS, "unknown tool"
            )
        except controller_module.InvalidParamsError:
            return _jsonrpc_error(
                request_id, protocol.INVALID_PARAMS,
                "arguments must be an object",
            )
        except Exception as exc:  # generic; class name only
            self.log_message("tool failure %s", type(exc).__name__)
            return _jsonrpc_error(
                request_id, protocol.INTERNAL_ERROR, "internal error"
            )
        return _jsonrpc_result(request_id, result.to_jsonrpc_result())
