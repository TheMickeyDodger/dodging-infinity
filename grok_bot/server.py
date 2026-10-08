"""The Grok Bot MCP endpoint: Streamable HTTP, tools only, bound to LOOPBACK.

One HTTP endpoint, ``MCP_PATH``, on ``127.0.0.1`` and an ephemeral (or
named) port. There is no way to bind anything else: the address is a module
constant with no flag, variable or argument that can change it;
``LoopbackMcpServer.server_bind`` refuses any address that is not exactly
``127.0.0.1`` BEFORE the socket binds, then re-checks the address the
socket actually bound; and ``server_activate`` refuses to listen on a socket
that is not bound to loopback (``listen`` on an unbound socket would bind
the wildcard address itself). IPv4 only. No TLS, no tunnel, no installed
credential and no outbound connection of its own (a delivery tool call
runs pr_delivery's ceremony, whose ``git ls-remote`` and possible
``git fetch`` reach the configured remote; see ``grok_bot.delivery``):
reaching it from Grok Bot needs the human actions in
``PUBLIC_REACHABILITY``, which this code never performs.

Access control of the TRANSPORT (implemented, absent by default): started
with a bearer token (``read_bearer_token``: an owner-only file the human
provisions; never argv, never the environment, never printed), every
request must carry ``Authorization: Bearer <that token>``. A missing or
wrong one is refused 401 before the body is read, by a constant-time
comparison. Without one, the endpoint is exactly the loopback-only default.
The token shows only that the caller holds it. It never reaches the adapter,
and it approves nothing: approval stays the operator-attested relay of the
human's separate reply, unchanged.

Per the Streamable HTTP transport: POST carries exactly one JSON-RPC
message; a notification or client response is answered 202 with no body;
a request is answered with one JSON object, or with one SSE event when the
client accepts only ``text/event-stream``. GET and DELETE are 405: no
server-initiated stream and no session is offered. An unsupported
``MCP-Protocol-Version`` header is a 400 whose body is NOT the modern
``-32022`` error, so a dual-era client falls back to ``initialize``.

DNS rebinding: a request whose ``Host`` is not this loopback listener, or
that carries any ``Origin`` (only a browser sends one), is refused 403
before its body is read. Bounds: ``MAX_REQUEST_BODY_BYTES`` per request
(no chunked bodies) and ``REQUEST_TIMEOUT_SECONDS`` per connection.

What the tests establish, and what they do not: they drive this endpoint
over real loopback HTTP with a real client, which proves the protocol SHAPE
only. Which MCP revision, credential header and request headers the live
Grok Bot client actually uses is unknown (``PUBLIC_REACHABILITY
["live_compatibility"]``) until the live acceptance exercise.

The transport decides nothing: every reply is ``grok_bot.mcp``'s, which is
the adapter's labelled result. Nothing here logs a request body (a control
capability can travel in one) or the bearer token.
"""

import hmac
import http.server
import ipaddress
import json
import os
import socket
import socketserver
import stat
import sys
from urllib.parse import urlsplit

from grok_bot import mcp

LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_ADDRESS = ipaddress.IPv4Address("127.0.0.1")
MCP_PATH = "/mcp"
MAX_REQUEST_BODY_BYTES = 131072
REQUEST_TIMEOUT_SECONDS = 30
# The expected bearer token: visible ASCII (0x21-0x7E), bounded both ways.
MIN_BEARER_TOKEN_CHARS = 32
MAX_BEARER_TOKEN_CHARS = 512

# The final setup dependency, as data. This code performs none of it.
PUBLIC_REACHABILITY = {
    "status": "not_configured",
    "performed_by_this_code": False,
    "bind_address": LOOPBACK_HOST,
    "reason": "Grok Bot reaches custom MCP servers only at a public HTTPS URL;"
              " localhost and private addresses are rejected, and a Command"
              " MCP server in a phone conversation runs on xAI's cloud"
              " computer, not on this Mac.",
    "access_control": {
        "implemented": "bearer_token",
        "configuration": "grokbot.py ... serve --auth-token-file <absolute path"
                         " of an owner-only file holding the token>; absent by"
                         " default, which is the loopback-only endpoint",
        "check": "every request must carry Authorization: Bearer <token>; a"
                 " missing or wrong one is refused 401 before the body is read",
        "scope": "transport only: it shows the caller holds the token, never"
                 " who the human is, and approves nothing; approval stays the"
                 " operator-attested relay of the human's separate reply",
    },
    "forwarder_contract": {
        "public_side": "HTTPS, terminated by the forwarder",
        "forwards_to": "http://127.0.0.1:<port>/mcp",
        "host_header": "127.0.0.1:<port>",
        "origin_header": None,
        "authorization_header": "forwarded unchanged",
    },
    "required_human_actions": [
        {"id": "provision_bearer_token", "performed_by_this_code": False,
         "action": "Generate a random token (32 to 512 visible ASCII"
                   " characters), write it to a file readable only by the"
                   " serving user (chmod 600), and serve with"
                   " --auth-token-file pointing at it. Never expose the"
                   " endpoint beyond loopback without it."},
        {"id": "provision_public_https", "performed_by_this_code": False,
         "action": "Provision a public HTTPS forwarder to the loopback listener"
                   " (for example a tunnel, per xAI's custom MCP tunneling"
                   " guide) that meets forwarder_contract: it presents Host"
                   " 127.0.0.1:<port>, sends no Origin, and passes"
                   " Authorization through unchanged."},
        {"id": "register_connector", "performed_by_this_code": False,
         "action": "At grok.com/connectors choose New Connector, Custom, enter"
                   " the public URL ending in /mcp, and give the same token as"
                   " the connector's credential, so Grok sends it as"
                   " Authorization: Bearer <token>."},
        {"id": "run_live_acceptance", "performed_by_this_code": False,
         "action": "Run the bounded, reversible phone acceptance exercise; it"
                   " is what resolves live_compatibility. Nothing in this"
                   " repository is live evidence."},
    ],
    "vendor_evidence": {
        "grok_bot_connectors": {
            "applies_to": "Grok Bot (the phone app's Bots) and its custom"
                          " connectors",
            "states": "Remote HTTPS MCP servers are a supported connector kind,"
                      " using the Bot's own credential or each person's OAuth"
                      " sign-in; a custom MCP server must be reachable over the"
                      " public internet; localhost and private addresses are"
                      " rejected; Command servers run on the computer each"
                      " conversation uses, which is xAI's cloud computer",
            "sources": [
                "https://docs.x.ai/grok-bot/team-bots",
                "https://docs.x.ai/grok/connectors",
                "https://docs.x.ai/grok/connectors/custom-mcp-tunneling",
                "https://docs.x.ai/grok-bot/computer-and-apps",
            ],
        },
        "xai_api_remote_mcp": {
            "applies_to": "the xAI API's remote MCP tool, not the Grok Bot"
                          " surface",
            "states": "only Streaming HTTP and SSE transports; a token is set"
                      " in the Authorization header",
            "sources": ["https://docs.x.ai/developers/tools/remote-mcp"],
        },
    },
    "implemented_and_tested": {
        "transport": "Streamable HTTP, POST only, JSON or one SSE event",
        # Exactly the advertised set; each is negotiated in the tests.
        "mcp_revisions": list(mcp.PROTOCOL_VERSIONS),
        "negotiation": "initialize echoes an advertised revision and answers"
                       " any other with the newest; an MCP-Protocol-Version"
                       " header naming any other revision is a plain 400; a"
                       " request without the header is handled under the"
                       " advertised revisions' rules, so a JSON-RPC batch is"
                       " refused 400 either way",
        "not_implemented": "2025-03-26 (it requires JSON-RPC batch reception),"
                           " the 2026-07-28 per-request revision, the"
                           " deprecated HTTP+SSE transport, sessions, rate"
                           " limiting, OAuth",
        "evidence": "loopback tests with a real HTTP client prove protocol"
                    " shape only, never live interoperability",
    },
    "live_compatibility": {
        "status": "unverified",
        "unknown": [
            "the MCP revision and transport the live Grok Bot client speaks",
            "whether Grok Bot sends the connector credential as Authorization:"
            " Bearer <token>",
            "whether its requests carry an Origin header",
            "the Host the chosen forwarder presents",
            "Grok Bot's tool-call timeout against a Codex Operator turn",
            "whether Grok Bot shows a long display_text to the human whole",
        ],
        "known_gaps": [
            {"id": "mcp_2025_03_26_batch_reception",
             "applies_if": "the live Grok Bot client negotiates only MCP"
                           " revision 2025-03-26",
             "gap": "2025-03-26 requires receiving JSON-RPC batches; this"
                    " server does not advertise that revision and batch"
                    " reception is deliberately not implemented, so such a"
                    " client cannot use this endpoint",
             "covered_by_advertised_revisions": False,
             "minimum_follow_up": "add 2025-03-26 to the advertised revisions"
                                  " together with JSON-RPC batch reception and"
                                  " its tests"},
        ],
        "resolved_by": "run_live_acceptance",
    },
}


class NotLoopbackError(Exception):
    """The endpoint was asked to bind, or found itself bound, off loopback."""


class BearerTokenError(ValueError):
    """The configured token is unusable. The message never contains it."""


def validate_bearer_token(token):
    if not isinstance(token, str) or not (
        MIN_BEARER_TOKEN_CHARS <= len(token) <= MAX_BEARER_TOKEN_CHARS
    ) or any(not "\x21" <= char <= "\x7e" for char in token):
        raise BearerTokenError(
            "the bearer token must be %d to %d visible ASCII characters"
            % (MIN_BEARER_TOKEN_CHARS, MAX_BEARER_TOKEN_CHARS))
    return token


def read_bearer_token(path):
    """The expected token, from ONE line of a regular file that only its
    owner can read or write (no symlink). Read once, at startup."""
    if not isinstance(path, str) or not os.path.isabs(path):
        raise BearerTokenError("the token file must be an absolute path")
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise BearerTokenError("the token file cannot be read (%s)"
                               % type(exc).__name__)
    if not stat.S_ISREG(mode):
        raise BearerTokenError("the token file must be a regular file, not a"
                               " link or anything else")
    if mode & 0o077:
        raise BearerTokenError(
            "the token file is accessible by group/other (mode %o); fix with"
            " chmod 600" % stat.S_IMODE(mode))
    try:
        with open(path, "r", encoding="ascii") as handle:
            text = handle.read(MAX_BEARER_TOKEN_CHARS + 3)
    except (OSError, ValueError) as exc:
        raise BearerTokenError("the token file cannot be read (%s)"
                               % type(exc).__name__)
    token = text[:-1] if text.endswith("\n") else text
    return validate_bearer_token(token[:-1] if token.endswith("\r") else token)


def require_loopback(host):
    """Refuse anything that is not exactly 127.0.0.1: no wildcard, no other
    interface, no other loopback address, no name to resolve."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address != _LOOPBACK_ADDRESS:
        raise NotLoopbackError(
            "the Grok Bot MCP endpoint binds only to %s; refused %r"
            % (LOOPBACK_HOST, host))


class McpRequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "grokbot-mcp"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, format, *args):
        log = self.server.log
        if log is not None:
            log.write("grokbot serve: %s %s\n"
                      % (self.address_string(), format % args))

    def _reply(self, status, body=None, content_type="application/json",
               headers=()):
        data = b"" if body is None else body
        self.send_response(status)
        if data:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _refuse(self, status, message, headers=()):
        body = mcp.ascii_json(mcp.error(None, mcp.INVALID_REQUEST, message))
        self._reply(status, (body + "\n").encode("ascii"), headers=headers)

    def _admitted(self):
        """Path, Host and Origin, before anything else is read."""
        if urlsplit(self.path).path != MCP_PATH:
            self._refuse(404, "the MCP endpoint is %s" % MCP_PATH)
            return False
        if self.headers.get("Host") not in self.server.allowed_hosts:
            self._refuse(403, "Host is not this loopback listener")
            return False
        if self.headers.get("Origin") is not None:
            self._refuse(403, "browser Origin refused")
            return False
        if not self._bearer_ok():
            self._refuse(401, "a valid bearer token is required",
                         headers=(("WWW-Authenticate", 'Bearer realm="grokbot"'),))
            return False
        return True

    def _bearer_ok(self):
        """True when no token is configured, or the request's Authorization
        header carries exactly it (constant-time). Transport only."""
        expected = self.server.bearer_token
        if expected is None:
            return True
        scheme, _, supplied = (self.headers.get("Authorization") or "").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode("latin-1", "replace"), expected)

    def do_GET(self):
        if self._admitted():
            self._refuse(405, "no server-initiated stream is offered",
                         headers=(("Allow", "POST"),))

    do_DELETE = do_GET

    def do_POST(self):
        if not self._admitted():
            return
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in mcp.PROTOCOL_VERSIONS:
            self._refuse(400, "unsupported MCP-Protocol-Version %r; this server"
                         " speaks the initialization-based revisions %s"
                         % (version, ", ".join(mcp.PROTOCOL_VERSIONS)))
            return
        media = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if media.lower() != "application/json":
            self._refuse(415, "Content-Type must be application/json")
            return
        accept = (self.headers.get("Accept") or "").lower()
        if not accept or "application/json" in accept or "*/*" in accept:
            sse = False
        elif "text/event-stream" in accept:
            sse = True
        else:
            self._refuse(406, "Accept application/json or text/event-stream")
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._refuse(411, "a Content-Length body is required")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._refuse(411, "a Content-Length body is required")
            return
        if length < 0 or length > MAX_REQUEST_BODY_BYTES:
            self._refuse(413, "the body exceeds %d bytes" % MAX_REQUEST_BODY_BYTES)
            return
        raw = self.rfile.read(length)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            body = mcp.ascii_json(mcp.error(
                None, mcp.PARSE_ERROR,
                "the body is not JSON this endpoint can parse (%s)"
                % type(exc).__name__))
            self._reply(400, (body + "\n").encode("ascii"))
            return
        status, reply = mcp.handle(self.server.adapter, message)
        if reply is None:
            self._reply(status)
        elif sse and status == 200:
            event = "event: message\ndata: %s\n\n" % mcp.ascii_json(reply)
            self._reply(status, event.encode("ascii"), "text/event-stream")
        else:
            self._reply(status, (mcp.ascii_json(reply) + "\n").encode("ascii"))


class LoopbackMcpServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """The endpoint over one adapter. ``port`` 0 asks for an ephemeral port."""

    address_family = socket.AF_INET
    daemon_threads = True

    def __init__(self, adapter, port=0, log=None, bearer_token=None):
        self.adapter = adapter
        self.log = log
        # Held as bytes for the comparison; never logged, never handed on.
        self.bearer_token = (None if bearer_token is None else
                             validate_bearer_token(bearer_token).encode("ascii"))
        http.server.HTTPServer.__init__(self, (LOOPBACK_HOST, port),
                                        McpRequestHandler)

    @property
    def access_control(self):
        return "none" if self.bearer_token is None else "bearer_token"

    def server_bind(self):
        # The ONE bind in the package: refused off loopback before it
        # happens, and re-checked against the address it landed on.
        require_loopback(self.server_address[0])
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        try:
            require_loopback(host)
        except NotLoopbackError:
            self.socket.close()
            raise
        # Set directly: HTTPServer.server_bind would resolve a name (getfqdn).
        self.server_name, self.server_port = host, port
        self.allowed_hosts = frozenset(("%s:%d" % (LOOPBACK_HOST, port),
                                        "localhost:%d" % port))

    def server_activate(self):
        # The ONE listen in the package. listen() on a socket that is not
        # yet bound would itself bind the wildcard address, so it is refused
        # unless this socket is already bound to loopback on a real port.
        host, port = self.socket.getsockname()[:2]
        require_loopback(host)
        if not port:
            raise NotLoopbackError("the socket is not bound; refusing to listen")
        socketserver.TCPServer.server_activate(self)

    def handle_error(self, request, client_address):
        """One line to the log, never a request body or a traceback on
        stdout; the connection is closed and the server keeps serving."""
        if self.log is not None:
            self.log.write("grokbot serve: request from %s failed: %r\n"
                           % (client_address[0], sys.exc_info()[1]))

    @property
    def url(self):
        return "http://%s:%d%s" % (LOOPBACK_HOST, self.server_port, MCP_PATH)
