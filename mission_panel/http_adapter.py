"""The DI World request panel's local interface: a bounded, authenticated,
loopback-only JSON-over-HTTP adapter over ``PanelService``.

    dimission_panel.py --state-dir DIR --control-repo PATH --discovery-file PATH
                       [--port N] [--workspace-repository PATH --workspaces-root PATH]

It is what the Pixel server talks to on the renderer's behalf, and nothing
else. The renderer holds no token, no shell, no PTY, no filesystem access and
no command route: it can only send the closed messages the server relays to the
fixed routes below.

Routes (nothing else exists):

    GET  /healthz
    GET  /overview
    GET  /conversations/<cv-id>
    GET  /requests/<lr-ref>            GET /requests/<lr-ref>/result
    POST /conversations/turn           POST /proposals/submit
    POST /requests/<lr-ref>/offer      (issue or re-read the frozen decision offer)
    POST /requests/<lr-ref>/decide     POST /requests/<lr-ref>/check-approval
    POST /requests/<lr-ref>/withdraw   POST /requests/<lr-ref>/run

Access boundary, checked on every request before anything is read:

- LOOPBACK ONLY: the host is a constant with no flag; the server refuses any
  other address before binding, re-checks the address it bound, and refuses a
  peer that is not 127.0.0.1;
- ``Host`` exactly ``127.0.0.1:<bound port>``; ``Origin`` absent or exactly
  ``http://127.0.0.1:<bound port>`` (a browser page, even on loopback, fails);
- a PER-LAUNCH bearer from ``secrets``, exactly one ``Authorization: Bearer``,
  compared in constant time. The token leaves the process only through the
  owner-only discovery file, never stdout, argv or the environment;
- at most ``MAX_CONCURRENT_REQUESTS`` connections in progress (one more is
  closed unanswered) and ``REQUEST_TIMEOUT_SECONDS`` per socket read.

Bodies are one closed JSON object of at most ``MAX_BODY_BYTES``: an exact
``Content-Length`` (no chunked transfer), ``application/json``, no duplicate
key, no NaN, nesting at most ``MAX_JSON_DEPTH``, only the route's own keys. A
transport problem is 400/401/403/404/405/413; a domain answer, refusal
included, is 200 with ``ok`` true or false and a ``problem`` code. No 5xx is
ever sent and no traceback, path or internal message reaches a body.

It starts no work of its own. The only process it can cause is the restricted
read-only conversation turn (one fixed argv, verified before any spawn, with a
deadline) and the run the existing ``MissionBridge`` is already authorized to
make for an approved Mission.
"""

import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import signal
import socketserver
import sys
import threading
import time

from local_request import store as store_module
from local_request import surface as surface_module
from operator_session.codex import RestrictedCodexOperatorSession
from target_runtime import mission_progress_status as progress_status
from target_runtime.catalog_http import (
    LOOPBACK_HOST, MAX_CONCURRENT_REQUESTS, REQUEST_TIMEOUT_SECONDS,
    NotLoopbackError, publish_discovery_file, remove_discovery_file,
    require_loopback,
)
from workflow_authority import canonical

from mission_panel import composition as composition_module
from mission_panel import conversation as conversation_module
from mission_panel import journal as journal_module
from mission_panel import service as service_module
from mission_panel import wire as wire_module

ENDPOINT = "mission_panel"
ENDPOINT_VERSION = 1
CONTENT_TYPE = "application/json; charset=utf-8"
MAX_BODY_BYTES = 65536
MAX_JSON_DEPTH = 8
EXIT_OK = 0
EXIT_USAGE = 2
PROBLEM_INTERNAL = "mission_panel_internal"

_REF = r"(lr-[0-9a-f]{32})"
_ROUTES = (
    ("GET", re.compile(r"/healthz"), "healthz"),
    ("GET", re.compile(r"/overview"), "overview"),
    ("GET", re.compile(r"/conversations/(cv-[0-9a-f]{32})"), "conversation"),
    ("GET", re.compile(r"/requests/" + _REF), "request"),
    ("GET", re.compile(r"/requests/" + _REF + r"/result"), "result"),
    ("POST", re.compile(r"/conversations/turn"), "turn"),
    # Issuing an offer records bookkeeping (the frozen offer), so it is not a GET.
    ("POST", re.compile(r"/requests/" + _REF + r"/offer"), "offer"),
    ("POST", re.compile(r"/proposals/submit"), "submit"),
    ("POST", re.compile(r"/requests/" + _REF + r"/decide"), "decide"),
    ("POST", re.compile(r"/requests/" + _REF + r"/check-approval"), "check_approval"),
    ("POST", re.compile(r"/requests/" + _REF + r"/withdraw"), "withdraw"),
    ("POST", re.compile(r"/requests/" + _REF + r"/run"), "run"),
)
ROUTE_NAMES = tuple(sorted(set(name for _, _, name in _ROUTES)))
# route -> (required keys, optional keys) of its closed JSON body.
_BODY_KEYS = {
    "turn": (("conversation_id", "text"), ()),
    "submit": (("turn_id", "draft_digest_sha256"), ("supersedes",)),
    "offer": ((), ()),
    # A decision submits the EXACT offer it displayed: its display digest and every bound field.
    "decide": (("mission_id", "revision", "proposal_digest_sha256", "reply",
                "offer_digest_sha256", "expires_at", "approved_action_scope",
                "approved_delivery_targets", "repository_url"), ()),
    "check_approval": ((), ()),
    "withdraw": (("mission_id", "revision", "proposal_digest_sha256"), ()),
    "run": (("command",), ()),
}
_REFUSED_METHODS = ("HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")
_STDLIB_STATUS = {411: 400, 414: 413, 431: 413, 501: 405}
_REFUSALS = {400: "bad_request", 401: "bearer_token_required", 403: "forbidden",
             404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}


# The wire format and its size bound are the shared module's (see ``wire``), so the views the
# service builds and the bytes this sends are measured by the same function.
encode = wire_module.encode
MAX_RESPONSE_BYTES = wire_module.MAX_RESPONSE_BYTES
PROBLEM_RESPONSE_TOO_LARGE = "mission_panel_response_too_large"


def refusal_of(exc):
    """``(problem, reason, details)`` for an exception, or None when it is not
    a refusal the panel knows. Only a known refusal's own words are shown."""
    if isinstance(exc, (service_module.PanelRefusal, surface_module.LocalRequestRefusal)):
        return exc.problem, exc.reason, dict(exc.details)
    if isinstance(exc, journal_module.JournalError):
        return exc.problem, str(exc), {}
    if isinstance(exc, store_module.LocalRequestStoreError):
        return (getattr(exc, "problem", None) or store_module.PROBLEM_STORE_UNREADABLE,
                "the request store could not be used; see the adapter's log", {})
    return None


def _json_safe(details):
    try:
        json.dumps(details)
        return details
    except (TypeError, ValueError):
        return {}


def _depth(value):
    """The nesting depth of a parsed JSON value, iteratively."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, (dict, list)):
            deepest = max(deepest, level)
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, level + 1) for child in children)
    return deepest


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("the body repeats the key %r" % key)
        seen[key] = value
    return seen


def _no_constant(name):
    raise ValueError("%s is not valid JSON" % name)


class BadBody(Exception):
    pass


def parse_body(raw, required, optional):
    """The route's closed JSON object, or ``BadBody`` naming the problem."""
    try:
        text = raw.decode("utf-8")
        body = json.loads(text, object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BadBody("the body is not one valid JSON object (%s)" % type(exc).__name__)
    if not isinstance(body, dict):
        raise BadBody("the body must be one JSON object")
    if _depth(body) > MAX_JSON_DEPTH:
        raise BadBody("the body nests deeper than %d levels" % MAX_JSON_DEPTH)
    for key in sorted(body):
        if key not in required and key not in optional:
            raise BadBody("the body carries %r, which this route does not take" % key)
    for key in required:
        if key not in body:
            raise BadBody("the body is missing %r" % key)
    return body


class PanelRequestHandler(http.server.BaseHTTPRequestHandler):
    """Answers the fixed routes; refuses everything else."""

    timeout = REQUEST_TIMEOUT_SECONDS

    def version_string(self):
        return "dimission-panel"

    def log_message(self, format, *args):
        """Nothing is logged per request."""

    def send_error(self, code, message=None, explain=None):
        code = _STDLIB_STATUS.get(code, code)
        if code >= 500 or code not in _REFUSALS:
            code = 400
        if self.request_version == "HTTP/0.9":
            self.request_version = self.protocol_version
        self.close_connection = True
        self._reply(code, {"error": _REFUSALS[code] if message is None or code in (
            401, 403, 404, 405, 413) else message},
            (("WWW-Authenticate", "Bearer"),) if code == 401
            else (("Allow", "GET, POST"),) if code == 405 else ())

    def _fail(self, code, message):
        self.close_connection = True
        self._reply(code, {"error": message})

    def _reply(self, status, value, headers=()):
        body = encode(value)
        if len(body) > MAX_RESPONSE_BYTES:
            # Never an answer the relay would have to reject mid-stream: a plain refusal that
            # says what happened, and nothing is shown as changed. The views are built to fit
            # (see ``service.MAX_VIEW_BYTES``), so this is the backstop and not the mechanism.
            bytes_over = len(body)
            status = 200
            body = encode({
                "ok": False, "problem": PROBLEM_RESPONSE_TOO_LARGE,
                "reason": "the answer was larger than the %d bytes this panel sends, so it"
                          " was refused instead of being cut" % MAX_RESPONSE_BYTES,
                "details": {"bytes": bytes_over, "limit": MAX_RESPONSE_BYTES}})
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _admitted(self):
        """The access boundary; refuses (and answers) when not admitted."""
        if self.client_address[0] != LOOPBACK_HOST:
            self.send_error(403)
            return False
        if self.request_version not in ("HTTP/1.0", "HTTP/1.1"):
            self.send_error(400)
            return False
        if len(self.raw_requestline) > 8192:
            self.send_error(413)
            return False
        bound = "%s:%d" % (LOOPBACK_HOST, self.server.server_address[1])
        hosts = self.headers.get_all("Host") or []
        origins = self.headers.get_all("Origin") or []
        if hosts != [bound] or origins not in ([], ["http://" + bound]):
            self.send_error(403)
            return False
        if not self.server.bearer_ok(self.headers.get_all("Authorization")):
            self.send_error(401)
            return False
        return True

    def _route(self, method):
        raw = self.requestline.split()[1]
        if "?" in raw or "#" in raw:
            self.send_error(400)
            return None
        if not raw.startswith("/") or raw.startswith("//"):
            self.send_error(404)
            return None
        for verb, pattern, name in _ROUTES:
            found = pattern.fullmatch(raw)
            if found is not None:
                if verb != method:
                    self.send_error(405)
                    return None
                return name, found.groups()
        self.send_error(404)
        return None

    def _read_body(self, name):
        if self.headers.get_all("Transfer-Encoding"):
            self.send_error(400)
            return None
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not lengths[0].isdigit():
            self.send_error(400)
            return None
        length = int(lengths[0])
        if length > MAX_BODY_BYTES:
            self.send_error(413)
            return None
        types = self.headers.get_all("Content-Type") or []
        if len(types) != 1:
            self.send_error(400)
            return None
        media, _, params = types[0].partition(";")
        if media.strip().lower() != "application/json" or params.strip().lower() not in (
            "", "charset=utf-8"
        ):
            self.send_error(400)
            return None
        raw = self.rfile.read(length)
        required, optional = _BODY_KEYS[name]
        try:
            return parse_body(raw, required, optional)
        except BadBody as exc:
            self._fail(400, str(exc))
            return None

    def _answer(self, name, groups, body):
        service = self.server.service
        if name == "healthz":
            return {"endpoint": ENDPOINT, "endpoint_version": ENDPOINT_VERSION,
                    "healthz": "ok"}
        if name == "overview":
            return service.overview()
        if name == "conversation":
            return service.conversation_view(groups[0])
        if name == "request":
            return service.request_view(groups[0])
        if name == "offer":
            return service.decision_offer(groups[0])
        if name == "result":
            return service.result(groups[0])
        if name == "turn":
            return service.send_turn(body["conversation_id"], body["text"])
        if name == "submit":
            return service.submit_draft(body["turn_id"], body["draft_digest_sha256"],
                                        body.get("supersedes"))
        if name == "decide":
            return service.decide(
                groups[0], body["mission_id"], body["revision"],
                body["proposal_digest_sha256"], body["reply"],
                body["offer_digest_sha256"], body["expires_at"],
                body["approved_action_scope"], body["approved_delivery_targets"],
                body["repository_url"])
        if name == "check_approval":
            return service.check_approval(groups[0])
        if name == "withdraw":
            return service.withdraw(groups[0], body["mission_id"], body["revision"],
                                    body["proposal_digest_sha256"])
        return service.run(groups[0], body["command"])

    def _handle(self, method):
        if not self._admitted():
            return
        routed = self._route(method)
        if routed is None:
            return
        name, groups = routed
        body = None
        if method == "POST":
            body = self._read_body(name)
            if body is None:
                return
        elif self.headers.get_all("Content-Length") or self.headers.get_all(
                "Transfer-Encoding"):
            self.send_error(400)
            return
        try:
            answer = self._answer(name, groups, body)
        except Exception as exc:
            known = refusal_of(exc)
            if known is None:
                print("dimission_panel: a request failed in the panel (%s)"
                      % type(exc).__name__, file=sys.stderr)
                answer = {"ok": False, "problem": PROBLEM_INTERNAL,
                          "reason": "the panel hit an unexpected error; nothing"
                                    " is shown as changed",
                          "details": {}}
            else:
                answer = {"ok": False, "problem": known[0], "reason": known[1],
                          "details": _json_safe(known[2])}
        self._reply(200, answer)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _method_refused(self):
        if self._admitted():
            self.send_error(405)


for _method in _REFUSED_METHODS:
    setattr(PanelRequestHandler, "do_" + _method, PanelRequestHandler._method_refused)


class PanelServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Loopback only, bearer required, a bounded number of connections in
    progress, one shared controller. Handlers are not daemon threads:
    ``server_close`` joins every one."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, service, bearer_token, port=0, host=LOOPBACK_HOST):
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("a bearer token is required")
        require_loopback(host)
        self.service = service
        self._bearer = ("Bearer " + bearer_token).encode("ascii")
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        http.server.HTTPServer.__init__(self, (host, port), PanelRequestHandler)

    def bearer_ok(self, values):
        if not values or len(values) != 1:
            return False
        try:
            offered = values[0].encode("ascii")
        except UnicodeError:
            return False
        return hmac.compare_digest(offered, self._bearer)

    def server_bind(self):
        require_loopback(self.server_address[0])
        http.server.HTTPServer.server_bind(self)
        require_loopback(self.socket.getsockname()[0])

    def server_activate(self):
        require_loopback(self.socket.getsockname()[0])
        http.server.HTTPServer.server_activate(self)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)          # over the bound: unanswered
            return
        try:
            socketserver.ThreadingMixIn.process_request(self, request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            socketserver.ThreadingMixIn.process_request_thread(
                self, request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        print("dimission_panel: a request failed in the adapter", file=sys.stderr)

    def url(self):
        return "http://%s:%d" % (LOOPBACK_HOST, self.server_address[1])


# -- the entry point -----------------------------------------------------------------


def _parser():
    parser = argparse.ArgumentParser(
        prog="dimission_panel.py",
        description="The DI World request panel's local interface: a loopback,"
        " bearer-authenticated JSON adapter over the existing request lifecycle."
        " It grants no approval and names no principal; the only decision is the"
        " exact word a person types.")
    parser.add_argument("--state-dir", required=True, metavar="DIR",
                        help="the Mission Core / request / journal state directory"
                        " (created 0700)")
    parser.add_argument("--control-repo", required=True, metavar="PATH",
                        help="the control repository the read-only conversation runs in")
    parser.add_argument("--discovery-file", required=True, metavar="PATH",
                        help="absolute path of the NEW owner-only file to write"
                        " {url, port, token} to; removed at shutdown")
    parser.add_argument("--port", type=int, default=0,
                        help="loopback port (0: ephemeral, the default)")
    parser.add_argument("--workspace-repository", metavar="PATH",
                        help="a local checkout of the ONE repository this panel works"
                        " on; with --workspaces-root it enables the run")
    parser.add_argument("--workspaces-root", metavar="PATH",
                        help="an existing directory outside every repository where"
                        " each Mission's isolated workspace is prepared")
    return parser


def _origin_url(path):
    from target_runtime.git_transport import GitTransport
    return canonical.canonicalize_repository_url(
        GitTransport().remote_url(path).strip()).canonical_url


def build_service(state_dir, control_repo, workspace_repository=None,
                  workspaces_root=None, clock=None, runner=None):
    """The controller over the real stores; with a workspace pair, the real
    run bridge too. The stack is composed by ``composition.compose``, the same
    call the progression runtime makes, so the two share stores, bridge and
    run guard. ``runner`` is the one conversation process spawn."""
    clock = clock or (lambda: int(time.time()))
    repository_url = None
    if workspace_repository is not None:
        repository_url = _origin_url(workspace_repository)
    composition = composition_module.compose(
        state_dir, control_repo, workspace_repository, workspaces_root, clock)
    registry = conversation_module.InFlight()
    session = RestrictedCodexOperatorSession(
        runner=runner or conversation_module.bounded_runner())
    convo = conversation_module.PanelConversation(
        composition.journal, session, control_repo, repository_url, clock,
        registry=registry)
    return service_module.PanelService(
        composition.surface, composition.journal, convo, registry, clock,
        bridge=composition.bridge, repository_url=repository_url,
        progression=lambda: progress_status.probe(
            state_dir, composition.digest, clock()))


def main(argv=None):
    """Runs in the main thread (it installs the stop signals)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    for path in (args.state_dir, args.control_repo, args.discovery_file) + tuple(
            p for p in (args.workspace_repository, args.workspaces_root) if p):
        if not os.path.isabs(path):
            print("dimission_panel: paths must be absolute: %s" % path, file=sys.stderr)
            return EXIT_USAGE
    if (args.workspace_repository is None) != (args.workspaces_root is None):
        print("dimission_panel: --workspace-repository and --workspaces-root come"
              " as a pair", file=sys.stderr)
        return EXIT_USAGE
    if not 0 <= args.port <= 65535:
        print("dimission_panel: --port must be in [0, 65535]", file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isdir(args.control_repo):
        print("dimission_panel: --control-repo is not a directory", file=sys.stderr)
        return EXIT_USAGE
    try:
        service = build_service(args.state_dir, args.control_repo,
                                args.workspace_repository, args.workspaces_root)
    except Exception as exc:
        print("dimission_panel: cannot start (%s): the workspace repository must"
              " be a git checkout with a canonical github.com origin"
              % type(exc).__name__, file=sys.stderr)
        return EXIT_USAGE
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    server = None
    identity = None
    serving = False
    try:
        token = secrets.token_urlsafe(32)
        server = PanelServer(service, token, args.port)
        try:
            identity = publish_discovery_file(
                args.discovery_file, server.server_address[1], token)
        except (OSError, ValueError) as exc:
            print("dimission_panel: cannot publish the discovery file (%s)"
                  % type(exc).__name__, file=sys.stderr)
            return EXIT_USAGE
        finally:
            del token
        service.start()
        threading.Thread(target=server.serve_forever, name="mission-panel-http",
                         daemon=True).start()
        serving = True
        sys.stdout.write(json.dumps({
            "endpoint": ENDPOINT, "endpoint_version": ENDPOINT_VERSION,
            "url": server.url(), "routes": list(ROUTE_NAMES),
            "discovery_file": args.discovery_file, "pid": os.getpid(),
            "run_available": args.workspace_repository is not None},
            sort_keys=True) + "\n")
        sys.stdout.flush()
        while not stop.wait(1):
            pass
        return EXIT_OK
    finally:
        # The discovery file first (consumers stop finding a closing server),
        # then new work is refused and in-flight requests are joined.
        if identity is not None:
            remove_discovery_file(args.discovery_file, identity)
        if server is not None:
            # ``shutdown`` waits for ``serve_forever`` to finish, so it is only
            # called for a server that was ever serving: a launch refused
            # before it started (the discovery file exists) must exit, not hang.
            if serving:
                server.shutdown()
            server.server_close()
        service._conversation.idle(10)


__all__ = ["PanelServer", "PanelRequestHandler", "build_service", "main",
           "NotLoopbackError"]
