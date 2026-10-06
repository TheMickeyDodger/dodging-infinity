"""Streamable HTTP MCP endpoint for the Grok Bot connector (stdlib only).

``GrokMcpServer`` is a ``ThreadingHTTPServer`` serving ONE endpoint
path. It implements the legacy (initialize-handshake) Streamable HTTP
era: POST carries a single JSON-RPC request, notification, or
response; GET answers 405 (no server-initiated stream); DELETE ends a
session. Resumability, pagination, resources, prompts, logging,
sampling, tasks, and OAuth are NOT implemented, and that is stated here
rather than hidden. Server-originated FORM elicitation inside ONE tool
call IS implemented (below), and only there: the event stream a
decision call opens carries exactly one server request and then that
call's own result, and nothing is ever streamed outside a tool call.

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
   ``Mcp-Session-Id`` (absent 400, unknown or terminated 404). A
   client's JSON-RPC RESPONSE is gated exactly like a request: it is
   routed to the pending elicitation table only after its session is
   validated, and a response with no or an unknown session is refused
   like any other message.

Then framing: unparseable JSON is ``-32700``; a non-object, a wrong
``jsonrpc`` member, or a non-string/non-integer id is ``-32600``; an
unknown method is ``-32601``; non-object ``params``, non-object tool
arguments, or an unknown tool name is ``-32602``. Notifications and
responses are accepted with 202 and no body. Bounded-input violations
are NOT protocol errors: they come back from the controller as tool
results with ``isError: true``.

Elicitation transport lifecycle (Task 8, slice S-I). On ``initialize``
the session records whether the client declared FORM-mode elicitation
for the negotiated revision (``protocol.form_elicitation_negotiated``).
A decision tool call on a session without it, or whose ``Accept`` does
not list ``text/event-stream``, is answered as ordinary JSON with a
refusal and reserves nothing. Otherwise the handler answers ``200
text/event-stream``, ``Connection: close``, emits one
``elicitation/create`` request event, waits on the pending table
(``grok_mcp.elicitation``) — no thread is started and nothing sleeps;
the handler thread that owns the connection polls an Event at a bounded
interval and probes its own socket for an observed stream closure between
polls — and finally emits that call's own JSON-RPC result as the last
event. Two situations are kept apart, in code, log and result: a stream
closure OBSERVED by the server before a response was admitted records no
decision; a response admitted and durably applied, followed by a failed
or doubtful result presentation, keeps the decision and is logged as
"presentation write failed after admission" or "stream closure observed
after admission" (the same lines say "with no response admitted" when
the call ended without admitting one). Nothing here claims what the
peer received. The
client's answer arrives as a JSON-RPC response in a separate POST on the
same session and is handed to the waiter exactly once. Every socket
read and write is bounded by ``REQUEST_SOCKET_TIMEOUT_SECONDS``; the
wait is bounded by the table's validity; ``server_close`` wakes every
waiter with ``server_closed`` BEFORE joining handler threads, so it
never hangs on one.

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
passes, the handler builds the neutral ``AuthenticatedContext`` pair for
that request — transport ``grok_mcp``, principal kind "configured
connector credential ordinal" for the plain tools and "configured
connector client confirmation" for the decision tool, the ordinal of the
matched credential, no configured subject — and hands them to the
controller per call. They are built only here, only after the check,
and never from anything in the request body. They record that a
configured credential was verified; neither is proof of the human
behind it.

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
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mission import record as mission_record

from grok_mcp import controller as controller_module
from grok_mcp import elicitation
from grok_mcp import protocol
from grok_mcp.protocol import MAX_REQUEST_BYTES

DEFAULT_ENDPOINT_PATH = "/mcp"
SESSION_HEADER = "Mcp-Session-Id"
VERSION_HEADER = "MCP-Protocol-Version"
BEARER_SCHEME = "bearer "
EVENT_STREAM = "text/event-stream"

# Bound on live MCP sessions (LRU eviction). Exact-value pinned.
MAX_SESSIONS = 256

# Bound on every socket read and write of one request, including the
# event-stream writes of a decision call. Exact-value pinned.
REQUEST_SOCKET_TIMEOUT_SECONDS = 30

# The one configured connector credential this endpoint verifies. The
# authenticated ingress names its ORDINAL, never a Grok identity.
CONNECTOR_CREDENTIAL_ORDINAL = 1

_SESSION_VERSION = 0
_SESSION_FORM_ELICITATION = 1


class GrokMcpServer(ThreadingHTTPServer):
    """The single-endpoint MCP server; construction opens the socket only."""

    # Handler threads are joined by server_close, so a stopped server
    # leaves nothing running.
    daemon_threads = False

    def __init__(self, address, controller, bearer_token,
                 endpoint_path=DEFAULT_ENDPOINT_PATH, allowed_origins=(),
                 log_writer=None, clock=None,
                 elicitation_validity_seconds=None,
                 elicitation_poll_seconds=None, presentation_fault=None):
        self.controller = controller
        self._bearer_token = bearer_token
        self.endpoint_path = endpoint_path
        self.allowed_origins = tuple(allowed_origins)
        self.log_writer = log_writer or sys.stderr.write
        # session id -> (negotiated protocol version, form elicitation)
        self.sessions = collections.OrderedDict()
        self.sessions_lock = threading.Lock()
        self.elicitations = elicitation.PendingTable(
            clock or time.time, elicitation_validity_seconds,
            elicitation_poll_seconds,
        )
        # Test seam only: a callable invoked with the request id right
        # before the FINAL event of a decision call is written; raising
        # from it is an injected write failure. Production leaves None.
        self.presentation_fault = presentation_fault
        ThreadingHTTPServer.__init__(self, address, GrokMcpRequestHandler)

    def handle_error(self, request, client_address):
        """An exception escaped a handler: class name only, to the writer,
        best-effort (a failing writer never raises out of here)."""
        exc = sys.exc_info()[1]
        elicitation.best_effort(
            "log", self.log_writer,
            "handler error %s\n" % (type(exc).__name__ if exc else "unknown"),
        )

    def server_close(self):
        # Wake every waiting decision call BEFORE the handler threads are
        # joined, so a close never waits on a pending elicitation.
        self.elicitations.close_all()
        ThreadingHTTPServer.server_close(self)

    def bearer_matches(self, supplied):
        if not isinstance(supplied, str) or not self._bearer_token:
            return False
        return hmac.compare_digest(
            supplied.encode("utf-8"), self._bearer_token.encode("utf-8")
        )

    def new_session(self, protocol_version, form_elicitation=False):
        session_id = secrets.token_hex(16)
        with self.sessions_lock:
            self.sessions[session_id] = (protocol_version, bool(form_elicitation))
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.popitem(last=False)
        return session_id

    def has_session(self, session_id):
        return self.session_state(session_id) is not None

    def session_state(self, session_id):
        """The session's ``(version, form_elicitation)`` or None; a
        known session is touched as most recently used."""
        with self.sessions_lock:
            state = self.sessions.get(session_id)
            if state is None:
                return None
            # LRU: a session in use is never evicted by newer idle ones.
            self.sessions.move_to_end(session_id)
            return state

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


def _decision_tool_name(method, params):
    """The client-mediated tool a ``tools/call`` names, or None: the
    decision tools and (Task 8 S-VII) the Mission control and the attention
    acknowledgment — every tool whose answer is the human's form answer."""
    if method != protocol.METHOD_TOOLS_CALL or not isinstance(params, dict):
        return None
    name = params.get("name")
    if name in protocol.ELICITED_TOOL_NAMES:
        return name
    return None


class GrokMcpRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DodgingInfinityMCP/1"
    sys_version = ""
    # Every socket read and write of a request is bounded (socketserver
    # applies this to the connection at setup).
    timeout = REQUEST_SOCKET_TIMEOUT_SECONDS
    # Set per request by ``_gate`` after the bearer check; never before.
    ingress = None
    client_ingress = None
    # True once an event-stream response has been started for this
    # request: after that a failure cannot be answered with a status.
    streaming = False

    # -- logging: request line and status only, always best-effort ------

    def log_message(self, format, *args):
        """Every log line of a request goes through the ONE best-effort
        primitive: the configured writer is not authoritative and a
        failure of it never interrupts a request, a cleanup or a
        presentation. This body is pinned to be exactly that call."""
        elicitation.best_effort("log", self.server.log_writer,
                                (format % args) + "\n")

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
        self.client_ingress = None
        self.streaming = False
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
        # The bearer check passed: this request's authenticated ingress
        # pair, built from the same verified credential — the plain
        # ordinal context for the Mission tools and the client
        # confirmation context under which a form answer is applied.
        self.ingress, self.client_ingress = [
            mission_record.AuthenticatedContext(
                transport=protocol.SOURCE,
                principal_kind=kind,
                principal_ref=str(CONNECTOR_CREDENTIAL_ORDINAL),
                configured_subject=None,
            )
            for kind in (
                mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
                mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
            )
        ]
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
            if self.streaming:
                # The status line is already on the wire; nothing more
                # can be answered. The connection closes.
                self.close_connection = True
                return
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
        if method is not None and not isinstance(method, str):
            self._reply_json(_jsonrpc_error(
                request_id, protocol.INVALID_REQUEST, "invalid request"
            ))
            return
        # Session gate: everything but initialize needs a known session —
        # a client RESPONSE included, which is routed only past this gate.
        if method == protocol.METHOD_INITIALIZE:
            params = message.get("params")
            requested = None
            capabilities = None
            if isinstance(params, dict):
                requested = params.get("protocolVersion")
                capabilities = params.get("capabilities")
            negotiated = protocol.negotiate_version(requested)
            if not has_id:
                self._reply_empty(202)
                return
            session_id = self.server.new_session(
                negotiated,
                protocol.form_elicitation_negotiated(negotiated, capabilities),
            )
            self._reply_json(
                _jsonrpc_result(request_id, protocol.initialize_result(requested)),
                headers=((SESSION_HEADER, session_id),),
            )
            return
        session_id = self.headers.get(SESSION_HEADER)
        if session_id is None:
            self._reply(400, b"missing session")
            return
        session_id = session_id.strip()
        session_state = self.server.session_state(session_id)
        if session_state is None:
            self._reply(404, b"unknown session")
            return
        if method is None:
            # A JSON-RPC response from the client: handed to the waiting
            # decision call of THIS session exactly once, or reported as
            # unsolicited / replayed with no effect. Never logged by text.
            kind = self.server.elicitations.deliver(session_id, message)
            self.log_message("client response %s", kind)
            self._reply_empty(202)
            return
        if not has_id:
            # Notification (notifications/initialized or any other).
            self._reply_empty(202)
            return
        decision_tool = _decision_tool_name(method, message.get("params"))
        if decision_tool is not None:
            try:
                self._decision_call(request_id, method, message, session_id,
                                    session_state, accept)
            except Exception as exc:  # noqa: BLE001 - class name only
                # A raise AT the decision call's own boundary: a decided
                # call is still answered when nothing was streamed yet.
                self.log_message("decision call raised %s at its boundary",
                                 type(exc).__name__)
                if not self.streaming:
                    self._reply_json(_jsonrpc_error(
                        request_id, protocol.INTERNAL_ERROR, "internal error"
                    ))
            return
        self._reply_json(self._dispatch(request_id, method, message))

    # -- the decision call: one elicitation on this request's stream ------

    def _decision_call(self, request_id, method, message, session_id,
                       session_state, accept):
        if not session_state[_SESSION_FORM_ELICITATION]:
            channel = elicitation.refused_channel(
                elicitation.REFUSAL_NOT_NEGOTIATED
            )
        elif EVENT_STREAM not in accept:
            channel = elicitation.refused_channel(
                elicitation.REFUSAL_SSE_NOT_ACCEPTED
            )
        else:
            channel = None
        if channel is not None:
            # No round trip is possible on this request: an ordinary JSON
            # refusal, and nothing reserved (the relay refuses first).
            self._reply_json(self._dispatch(request_id, method, message,
                                            channel))
            return
        table = self.server.elicitations
        version = session_state[_SESSION_VERSION]
        best_effort = elicitation.best_effort

        def elicit(elicitation_id, card, confirm_value, channel):
            # The stream probe is bound at registration, BEFORE the entry
            # is visible to a response and before the request is written,
            # so no response is ever admitted without it. The slot claim
            # stays with the channel until this registration succeeds: a
            # None here, or a clock that raises, leaves the claim with the
            # channel, which releases it on exit; the "claim consumed"
            # note runs inside the registration's own lock, so the two
            # can never disagree. The binding value and the channel's
            # admission note are bound too: the answer is evaluated and
            # handed to the channel AT ADMISSION, so nothing on this
            # frame's way out can change it.
            entry = None
            # UNCONDITIONAL cleanup: whatever happens on the stream path
            # after registration, the entry never outlives this call —
            # including a registration whose entry never reached this
            # frame, which is discarded by key.
            try:
                entry = table.register(session_id, elicitation_id,
                                       self._stream_open,
                                       claimed=channel.holding,
                                       on_registered=channel.consume,
                                       confirm_value=confirm_value,
                                       on_admitted=channel.admit)
                if entry is None:
                    return elicitation.OUTCOME_TABLE_FULL, None
                try:
                    # The event stream starts with its first event: a
                    # refusal that never reaches this point is an ordinary
                    # JSON reply.
                    self._start_stream()
                    self._write_event(elicitation.build_request(
                        elicitation_id, version, card, confirm_value,
                    ))
                except (OSError, ValueError):
                    return elicitation.OUTCOME_WRITE_FAILED, None
                except Exception as exc:  # unexpected; class name only
                    self.log_message("stream failure %s", type(exc).__name__)
                    return (elicitation.OUTCOME_STREAM_FAILED,
                            "stream raised %s" % type(exc).__name__)
                outcome, response = table.wait(entry)
                if outcome == elicitation.DELIVERY_DELIVERED:
                    # The answer evaluated at admission; never re-derived.
                    return entry.answer
                return outcome, None
            finally:
                # Cleanup can never replace the answer: best-effort, noted
                # by class name, with one attempt by key after a failed
                # discard (and by key alone when the entry never reached
                # this frame).
                if entry is None or not best_effort(
                    "cleanup discard", table.discard, entry,
                    note=self.log_message,
                ):
                    best_effort("cleanup discard by key", table.discard_key,
                                session_id, elicitation_id,
                                note=self.log_message)

        channel = elicitation.ElicitationChannel(
            elicit_fn=elicit, claim_fn=table.claim,
            release_fn=table.release_claim,
        )
        result = None
        try:
            result = self._dispatch(request_id, method, message, channel)
        except Exception as exc:  # only a raise AT the dispatch boundary
            self.log_message("decision dispatch raised %s after the call",
                             type(exc).__name__)
        try:
            best_effort("cleanup release", channel.release,
                        note=self.log_message)
        finally:
            # PRESENTATION, inline in a ``finally``: it runs even if the
            # cleanup call boundary itself raised, so a decided call is
            # always answered. The decision, if any, is durable by now
            # and is NEVER rolled back by anything below.
            if channel.last_result is not None and (
                result is None or "error" in result
            ):
                # The relay recorded its own result after reserving an
                # id; a raise at a call boundary above it can never lose
                # that id or the admitted outcome: the recorded result is
                # what is presented whenever the ordinary path handed
                # back nothing or a bare protocol error.
                structured, is_error = channel.last_result
                result = _jsonrpc_result(
                    request_id,
                    controller_module.ToolResult(
                        structured, is_error).to_jsonrpc_result(),
                )
            elif result is None:
                result = _jsonrpc_error(request_id, protocol.INTERNAL_ERROR,
                                        "internal error")
            phase = ("after admission" if channel.answer is not None
                     else "with no response admitted")
            if not self.streaming:
                try:
                    self._reply_json(result)
                except Exception as exc:  # noqa: BLE001 - class name only
                    self.log_message(
                        "reply failed %s %s; result presentation uncertain",
                        phase, type(exc).__name__,
                    )
            else:
                # What is logged is what the server observed: a closure
                # already visible on its socket before the result write,
                # or a write that failed — and whether a response had
                # been admitted. Any other raise on the presentation path
                # is logged and the write is attempted once more.
                try:
                    if self.server.presentation_fault is not None:
                        self.server.presentation_fault(request_id)
                    if not self._stream_open():
                        self.log_message(
                            "stream closure observed %s before result"
                            " write; result presentation uncertain", phase,
                        )
                    self._write_event(result)
                except (OSError, ValueError) as exc:
                    self.log_message(
                        "presentation write failed %s %s; result"
                        " presentation uncertain", phase,
                        type(exc).__name__,
                    )
                except Exception as exc:  # noqa: BLE001 - class name only
                    self.log_message(
                        "result presentation raised %s %s; result"
                        " presentation uncertain", phase,
                        type(exc).__name__,
                    )
                    try:
                        self._write_event(result)
                    except Exception as again:  # noqa: BLE001
                        self.log_message(
                            "presentation write failed %s %s; result"
                            " presentation uncertain", phase,
                            type(again).__name__,
                        )

    def _start_stream(self):
        """Send the event-stream status and headers once; the connection
        closes after the final event."""
        if self.streaming:
            return
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", EVENT_STREAM)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        # Only now: a raise above (the status line's own log call
        # included) leaves this request answerable with a status.
        self.streaming = True

    def _write_event(self, payload):
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.wfile.write(b"event: message\ndata: " + data + b"\n\n")
        self.wfile.flush()

    def _stream_open(self):
        """Whether this handler has NOT observed a closure of its event
        stream: a readable socket that yields no byte, or a socket error,
        is a closure observed. It states nothing about the peer."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return True
            return self.connection.recv(1, socket.MSG_PEEK) != b""
        except (OSError, ValueError):
            return False

    def _dispatch(self, request_id, method, message, channel=None):
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
                ingress=self.ingress, elicitation=channel,
                client_ingress=self.client_ingress,
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
