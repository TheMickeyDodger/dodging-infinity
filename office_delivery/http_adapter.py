"""The office delivery adapter's local interface: a bounded, authenticated, loopback-only JSON-over-HTTP
adapter over ``office_delivery.adapter`` (owner decision (b) and the expected-step supplement).

    didelivery_office.py --discovery-file PATH [--port N]

STARTED BY THE HUMAN, like the request panel (``dimission_panel.py``); the office server is told the
discovery file's path and only READS it. Nothing here grants delivery authority: the one human ceremony
remains the only authority, and the one step this serves goes through pr_delivery's expected-step guard.

Routes (nothing else exists):

    GET  /healthz
    GET  /deliveries/<delivery-id>/status          the adapter's status, unchanged
    POST /deliveries/<delivery-id>/advance-once    body: exactly one JSON object, the confirmation

HEAD is refused FIRST, with 405 and NO body, whoever sends it; it reads nothing and calls nothing.

Access, checked on every other request before anything else and BEFORE ANY BODY BYTE IS READ: LOOPBACK
ONLY (the host is a constant with no option; anything else is refused before binding, and the bound
address is re-checked); ``Host`` exactly ``127.0.0.1:<bound port>``; ``Origin`` absent or exactly
``http://127.0.0.1:<bound port>``; exactly one ``Authorization: Bearer <token>``, compared in constant
time. The token is minted by ``secrets`` per launch and leaves the process ONLY through the owner-only
discovery file (the request panel's helper), never stdout, an argument or the environment.

Framing: a ``Transfer-Encoding`` is refused on EVERY route, before any adapter call. A length is ASCII
digits only, converted without raising however long. Nesting is bounded without recursion. Every input
check here REFUSES; none raises, whatever the request holds.

Answers: a transport problem is 4xx with ``ok`` false and a problem word; a domain refusal is 200 with
``ok`` false and its problem; an adapter exception is ``ok`` false with its problem code when it carries
a plain one, else ``adapter_error``, and never its message.

ALL DECISIONS ARE IN THE PURE FUNCTIONS, in two stages: ``admit`` decides everything the request line
and headers can decide and says how many body bytes to read (ZERO on every refusal); ``respond`` makes
the body's own checks and the one adapter call. ``handle`` only composes the two. ``encode``,
``response_headers`` and ``require_loopback`` are pure helpers. ``_RequestHandler`` only carries bytes:
it always admits, always reads what admit said, always responds, and holds no branch (tests pin that,
and that order). STATED LIMITS: no test exercises the socket-to-handler path, so a defect in the
wrapper's request reading or response writing would not be caught by the tests; and the number of
concurrent requests is not bounded.
"""

import argparse
import hmac
import http.server
import json
import re
import secrets
import signal
import socketserver
from pathlib import Path

from office_delivery import adapter as adapter_module
from target_runtime.catalog_http import NotLoopbackError, publish_discovery_file, remove_discovery_file

LOOPBACK_HOST = "127.0.0.1"
MAX_BODY_BYTES = 4096
MAX_JSON_DEPTH = 4
REQUEST_TIMEOUT_SECONDS = 10
ROUTES = (
    ("GET", "/healthz"),
    ("GET", "/deliveries/<delivery-id>/status"),
    ("POST", "/deliveries/<delivery-id>/advance-once"),
)
_DELIVERY_ROUTE = re.compile(r"^/deliveries/(prd-[0-9a-f]{24})/(status|advance-once)$")
_PROBLEM_WORD = re.compile(r"^[a-z][a-z0-9_]{0,80}$")
_BEARER = "Bearer "
_ASCII_DIGITS = frozenset("0123456789")


def _values(headers, name):
    return [value for key, value in headers if key.lower() == name]


def _problem(word):
    return {"ok": False, "problem": word}


def _problem_of(exc):
    code = getattr(exc, "problem", None)
    return code if isinstance(code, str) and _PROBLEM_WORD.match(code) else "adapter_error"


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constant(name):
    raise ValueError("%s is not JSON" % name)


def _within_depth(value):
    """True when ``value`` nests no deeper than MAX_JSON_DEPTH. An explicit stack and NO recursion, so no nesting the parser accepts can exhaust the interpreter's stack here; it stops
    at the first container past the bound."""
    pending = [(value, 1)]
    while pending:
        item, level = pending.pop()
        if isinstance(item, dict):
            children = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if level > MAX_JSON_DEPTH:
            return False
        pending.extend((child, level + 1) for child in children)
    return True


def _parse_object(body):
    """The one JSON object ``body`` holds, or None: strict UTF-8, no duplicate key, no NaN or Infinity, nesting within the bound. The parser's own refusal of deep nesting is caught."""
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or not _within_depth(value):
        return None
    return value


def _declared_length(headers):
    """("absent" | "invalid" | "too_large" | "ok", n) for the request's Content-Length. It never raises, whatever the header holds: only ASCII digits are a length, leading zeros are
    dropped before conversion, and a value with more significant digits than the bound has is too large without being converted."""
    lengths = _values(headers, "content-length")
    if not lengths:
        return "absent", 0
    if len(lengths) != 1 or not lengths[0] or not set(lengths[0]) <= _ASCII_DIGITS:
        return "invalid", 0
    significant = lengths[0].lstrip("0")
    if len(significant) > len(str(MAX_BODY_BYTES)) or int(significant or "0") > MAX_BODY_BYTES:
        return "too_large", 0
    return "ok", int(significant or "0")


def _plan(refusal=None, read=0, operation=None, delivery_id=None):
    return {"refusal": refusal, "read": read, "operation": operation, "delivery_id": delivery_id}


def _refuse(status, word):
    return _plan(refusal=(status, _problem(word)))


def admit(token, port, method, path, headers):
    """Stage one, from the request line and headers ONLY: a refusal, or the operation and how many body bytes to read. Every refusal reads ZERO bytes, so no body byte is read before
    a request is admitted."""
    if method == "HEAD":
        return _plan(refusal=(405, None))
    if _values(headers, "host") != ["%s:%d" % (LOOPBACK_HOST, port)]:
        return _refuse(403, "host_refused")
    origins = _values(headers, "origin")
    if origins and origins != ["http://%s:%d" % (LOOPBACK_HOST, port)]:
        return _refuse(403, "origin_refused")
    authorization = _values(headers, "authorization")
    if len(authorization) != 1 or not authorization[0].startswith(_BEARER) or not hmac.compare_digest(
            authorization[0][len(_BEARER):].encode("utf-8", "surrogatepass"), token.encode("utf-8")):
        return _refuse(401, "unauthorized")
    if _values(headers, "transfer-encoding"):
        return _refuse(400, "unsupported_framing")
    if path == "/healthz":
        if method != "GET":
            return _refuse(405, "method_not_allowed")
        return _plan(operation="healthz")
    route = _DELIVERY_ROUTE.match(path)
    if route is None:
        return _refuse(404, "not_found")
    delivery_id, operation = route.group(1), route.group(2)
    if operation == "status":
        if method != "GET":
            return _refuse(405, "method_not_allowed")
        if _values(headers, "content-length") not in ([], ["0"]):
            return _refuse(400, "body_not_allowed")
        return _plan(operation="status", delivery_id=delivery_id)
    if method != "POST":
        return _refuse(405, "method_not_allowed")
    if _values(headers, "content-type") != ["application/json"]:
        return _refuse(415, "content_type")
    kind, length = _declared_length(headers)
    if kind == "too_large":
        return _refuse(413, "body_too_large")
    if kind != "ok":
        return _refuse(400, "content_length")
    return _plan(read=length, operation="advance-once", delivery_id=delivery_id)


def respond(office_adapter, plan, body):
    """Stage two: ``plan`` is what ``admit`` returned and ``body`` the bytes read for it. A refusal is answered as it stands; otherwise the body's own checks, then exactly ONE adapter
    call."""
    if plan["refusal"] is not None:
        return plan["refusal"]
    if len(body) != plan["read"]:
        return 400, _problem("content_length")
    if plan["operation"] == "healthz":
        return 200, {"ok": True}
    if plan["operation"] == "status":
        try:
            answer = office_adapter.status(plan["delivery_id"])
        except Exception as exc:  # an adapter failure is an answer, never a crash or a leaked message
            return 200, _problem(_problem_of(exc))
        return 200, {"ok": True, "status": answer}
    confirmation = _parse_object(body)
    if confirmation is None:
        return 400, _problem("malformed_body")
    try:
        result = office_adapter.advance_once(plan["delivery_id"], confirmation)
    except Exception as exc:  # as above
        return 200, _problem(_problem_of(exc))
    if result.get("advanced") is True:
        return 200, {"ok": True, "result": result}
    return 200, {"ok": False, "problem": result.get("refusal") or "not_advanced", "result": result}


def handle(office_adapter, token, port, method, path, headers, body):
    """The two stages composed, for one request whose body is already in hand. It decides nothing itself."""
    return respond(office_adapter, admit(token, port, method, path, headers), body)


def encode(payload):
    """The response body: deterministic JSON, or NO bytes when there is no payload (the answer to HEAD)."""
    if payload is None:
        return b""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def response_headers(data):
    return [("Content-Type", "application/json"), ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]


def require_loopback(host):
    if host != LOOPBACK_HOST:
        raise NotLoopbackError("the office delivery adapter binds %s only, not %r" % (LOOPBACK_HOST, host))


class _RequestHandler(http.server.BaseHTTPRequestHandler):
    """Carries bytes between the socket and the pure functions; decides nothing. It always admits, always reads what admit said (zero on a refusal), always responds."""

    timeout = REQUEST_TIMEOUT_SECONDS

    def _serve(self):
        headers = list(self.headers.items())
        plan = admit(self.server.office_token, self.server.server_address[1], self.command, self.path, headers)
        body = self.rfile.read(plan["read"])
        status, payload = respond(self.server.office_adapter, plan, body)
        data = encode(payload)
        self.send_response(status)
        for name, value in response_headers(data):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _serve

    def log_message(self, *_args):
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _stop(_signum, _frame):
    raise KeyboardInterrupt


def serve(office_adapter, discovery_file, port=0):
    """Bind loopback, mint the token, publish the discovery file, serve until stopped, then remove the file."""
    require_loopback(LOOPBACK_HOST)
    server = _Server((LOOPBACK_HOST, port), _RequestHandler)
    try:
        require_loopback(server.server_address[0])
        server.office_adapter = office_adapter
        server.office_token = secrets.token_hex(32)
        identity = publish_discovery_file(discovery_file, server.server_address[1], server.office_token)
        try:
            signal.signal(signal.SIGTERM, _stop)
            server.serve_forever()
        finally:
            remove_discovery_file(discovery_file, identity)
    finally:
        server.server_close()


def main(arguments=None):
    parser = argparse.ArgumentParser(description="The office delivery adapter's loopback interface. Started by you, never by the office.")
    parser.add_argument("--discovery-file", required=True, help="an absolute path; the owner-only file the office reads")
    parser.add_argument("--port", type=int, default=0, help="the loopback port (default: any free one)")
    options = parser.parse_args(arguments)
    if not Path(options.discovery_file).is_absolute():
        parser.error("--discovery-file must be an absolute path")
    try:
        serve(adapter_module.production_adapter(), options.discovery_file, options.port)
    except KeyboardInterrupt:
        pass
    return 0
