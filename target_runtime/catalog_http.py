"""A PASSIVE loopback HTTP adapter over the mission catalog owner
(``target_runtime.catalog.CatalogService``).

    dicatalog_http.py --registry DIR --discovery-file PATH [--port N]
                      [--herd-root PATH ...] [--discover-roots]
                      [--contract-version {1,2}] [--delivery-store DIR]
                      [--delivery-presentations DIR]

What it is: ``dicatalog.py serve`` carried over HTTP instead of JSON lines,
for a SERVER-SIDE consumer. ONE long-lived ``CatalogService`` is built at
startup exactly as ``serve`` builds it (same registry, roots, discovery
switch, clock and listing argv), so the owner's cadence, slot, wait shares
and cache hold across every request unchanged. Nothing here queries,
caches or restamps: those are the owner's.

CONSUMER SETTINGS (parent decision, pinned): ``GET /catalog?wait_seconds=1``,
polled every 6 seconds measured AFTER the previous response was received.
The core cadence is unchanged (a refresh starts at most once per 5 s,
anchored to the previous refresh start), so a 6 s poll after receipt is
clear of it; ``served`` ``cached`` and ``catalog_refresh_throttled`` are
NORMAL answers, never errors.

CONTRACT VERSION SELECTION is a LAUNCH-TIME flag, ``--contract-version``:
``1`` (the default, the accepted contract, built exactly as before) or ``2``
(``mission_sources.contract_v2``). ONE version per process for its whole
life: no query field selects a version (``GET /catalog?contract_version=2``
is refused like any other unknown field, 400), nothing switches it at run
time, and the default never changes by itself. Comparing the two versions
takes TWO processes, one launched with each. Serve-time freshness is judged
by the served version's own consumer operation (``response_freshness`` for
1, ``response_freshness_v2`` for 2). ``--delivery-store`` (version 2 only)
explicitly registers the delivery store the version 2 composer may read.

A RUNTIME SOURCE IS REQUIRED: ``--discover-roots`` (the roots that canonical
bindings in the REGISTERED sources name; nothing is scanned) or at least one
explicit ``--herd-root``. Without one the owner takes no runtime snapshot,
so no binding and no member could ever be reported; the adapter refuses to
start (exit 2) rather than serve a usable-looking, memberless catalog.

Routes, ALL ``GET``; nothing else exists:

- ``/catalog`` with an optional ``wait_seconds`` (the only query field, at
  most once, digits with an optional fraction, within ``(0,
  catalog.MAX_WAIT_SECONDS]``; absent is ``catalog.DEFAULT_WAIT_SECONDS``;
  ``0`` is refused, the frozen core's own rule): one catalog answer, the
  frozen nine-key envelope, serialised EXACTLY as ``serve`` writes an
  answer line (``json.dumps(answer, sort_keys=True)`` plus a newline). Every
  key is always present; a null value is JSON ``null``, never an absent key.
- ``/diagnostics``: the same diagnostics answer ``serve`` gives.
- ``/healthz``: liveness only; it starts nothing and reads no source.
- ``/artifact`` with EXACTLY ``mission_ref``, ``artifact_id`` and ``cursor``
  (each once, each of a fixed grammar: a reference, never a path, root or
  URL): the evidence library's one content read
  (``CatalogService.artifact_content``, ``target_runtime.artifact_content``),
  ``200`` for every answer, admitted or refused by its own code; any other
  query is ``400``. It never starts a refresh: it is corroborated by the
  owner's last refreshed answer, or refused.
- ``/code_review`` with ``mission_ref`` and ``task_id`` (each once, each of a
  fixed grammar: a reference, never a path, root, ref or git option), and,
  for ONE file's diff, also the ``snapshot`` the overview carried and the
  ``file_id`` it listed: the live code review's read-only view of an ACTIVE
  assignment's workspace (``CatalogService.code_review``,
  ``target_runtime.code_review``), ``200`` for every answer, observed or
  refused by its own code; any other query is ``400``. It never starts a
  refresh and never writes to the workspace it reads.

SERVE-TIME FRESHNESS (``serve_time_answer``). Immediately before a
``/catalog`` body is written, on the owner's own clock, the response is
judged by the canonical ``mission_sources.contract.response_freshness``
(``now <= valid_until`` is fresh; stale iff ``now > valid_until``). GUARANTEE:
a ``200`` body with a non-null ``response`` evaluated ``fresh`` at serve
time. A response evaluating anything else is WITHHELD: the body is the same
nine-key envelope with ``response`` null, ``problem`` the freshness problem,
``detail`` saying so and ``served`` ``none`` (``requested_at``,
``runtime_capture`` and ``retry_after_seconds`` as the owner returned them).
A fresh answer, a cached one included, is the owner's object UNCHANGED:
never re-wrapped, enriched or restamped. This narrows, and never extends,
what may be shown live; the time it judges is the serve time, so a consumer
still re-checks ``now <= valid_until`` while it shows anything. The JSONL
``serve`` path does not withhold; its consumer applies the same function.

NESTED RUNTIME FAILURE. A FRESH envelope may legitimately carry an
UNAVAILABLE runtime (the capture did not finish within its share of the
wait, its slot was occupied, the listing timed out or failed, a boundary
child timed out, ...). That is honest and is never rewritten here, so the
freshness verdict (body and headers) judges the RESPONSE, not the runtime.
"Fresh + no members" is therefore NEVER "observed empty" by itself: the
runtime is unobserved when ``runtime_capture.available`` is false (its
``problem`` names why), when ``response.runtime_snapshot.health`` is not
``fresh``, or when a mission's ``binding`` is ``unverified`` with reason
``runtime_snapshot_not_fresh``; its ``members`` list is then empty because
nothing was observed. An observed-empty roster has ``available`` true, a
fresh ``runtime_snapshot`` and a binding decided on the observation.

Headers on every ``/catalog`` answer: ``X-DI-Freshness-Health`` is ``fresh``
for a body carrying a response, the evaluated health for a withheld one, and
``unavailable`` when the owner itself gave no response;
``X-DI-Freshness-Problem`` equals the body's ``problem`` and is OMITTED when
that is null (exactly when the body carries a response). Content type
``application/json; charset=utf-8``; no CORS header, no cookie, ever.

Status: ``200`` for EVERY catalog envelope (refreshed, cached, throttled,
slot occupied, query pending or failed, owner shut down, no runtime root,
withheld); ``400`` a malformed request (a bad query, ``wait_seconds`` of the
wrong form or out of range, a request body, malformed HTTP, any HTTP version
but 1.0 and 1.1); ``401`` a
missing or wrong bearer; ``403`` a ``Host``/``Origin`` mismatch or a
non-loopback peer; ``404`` any other path; ``405`` any other method; ``413``
a request line over ``catalog.MAX_REQUEST_LINE_BYTES`` (``serve``'s line
bound) or headers over the standard library's own bounds. No 5xx is ever
sent.

Access boundary, checked on every request before anything else (no body is
ever read):

- LOOPBACK ONLY: ``LOOPBACK_HOST`` is a constant with no flag; the server
  refuses any other address before binding, re-checks the address it bound,
  and refuses to listen on anything else; a peer that is not 127.0.0.1 is
  refused. IPv4 only, no TLS, no tunnel, no public endpoint.
- ``Host`` exactly ``127.0.0.1:<bound port>``; ``Origin`` absent or exactly
  ``http://127.0.0.1:<bound port>``.
- A PER-LAUNCH bearer token from ``secrets``; every request carries exactly
  ONE ``Authorization: Bearer <token>``, compared in constant time.
- At most ``MAX_CONCURRENT_REQUESTS`` connections in progress (one more is
  closed unanswered) and ``REQUEST_TIMEOUT_SECONDS`` per socket read/write.

THE DISCOVERY FILE is the only way the token leaves the process
(``publish_discovery_file``). Its directory is created owner-only (0700) if
absent, or must already be a real, owner-only directory of this user. The
object ``{"port": <int>, "token": <str>, "url": "http://127.0.0.1:<port>"}``
is written to an exclusive 0600 temp file in the same directory (a name of
its own per call), fsynced, and PUBLISHED with ``os.link``, which is atomic
AND no-clobber: it fails if the path exists, so of any number of concurrent
launches exactly one publishes and every other is refused (exit 2) without
touching the published file; a leftover is refused the same way, never
overwritten (this launch does not own it). A filesystem without hard links
refuses too. It is removed at shutdown only while it is still the file
this launch published. The adapter never rewrites, recreates or repairs it:
if it disappears while serving, it stays gone. The token is never printed,
logged, put in argv or read from the environment. Startup prints ONE JSON
line on stdout (the URL, the routes and the discovery file's path, never
the token).

The CONSUMER reads the file only; it never launches, supervises or restarts
this process. A discovery file that is missing, unreadable, not owner-only,
a link, or not exactly that object is an honest ``unavailable`` condition
(``read_discovery_file``, the reference reader), never a reason to invent
state; a file left by a process that was killed points at a closed port,
which is unavailable too. The reader trusts only the file it actually
read: it opens without following links and without blocking, then re-checks
the OPENED descriptor (regular file, this user's, owner-only, within the
size bound, and the same file the path named when it was checked); a path
replaced between the check and the open is unavailable.

``SIGTERM`` or ``SIGINT``: the discovery file is removed, serving stops, the
owner is shut down (refusing new work), and EVERY in-flight request handler
is joined: handlers are NOT daemon threads, so closing the server waits for
each to finish its answer (each bounded by its wait and the socket timeout)
and nothing is abandoned mid-request; then, when a runtime snapshot is
configured, the runtime owner's owned work is waited for, as ``dicatalog.py
query`` does. The stop report on stderr counts the handlers still in
progress (``handlers_in_progress``), which is 0 only once every one ended.

No route registers or unregisters a source (registration stays the
``dicatalog.py`` CLI), and nothing here reaches a control, delivery,
terminal, model or execution path: the only imports are the catalog owner,
the runtime snapshot clock and the canonical freshness rule. Nothing is
logged per request.
"""

import argparse
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import signal
import socketserver
import stat
import sys
import threading
from urllib.parse import parse_qsl, urlsplit

from mission_sources import contract as contract_module
from target_runtime import catalog as catalog_module
from target_runtime import runtime_snapshot as snapshot_module

LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_ADDRESS = ipaddress.IPv4Address(LOOPBACK_HOST)
ROUTE_CATALOG = "/catalog"
ROUTE_DIAGNOSTICS = "/diagnostics"
ROUTE_HEALTHZ = "/healthz"
#: The evidence library's content route (``target_runtime.artifact_content``).
ROUTE_ARTIFACT = "/artifact"
#: The live code review's read route (``target_runtime.code_review``).
ROUTE_CODE_REVIEW = "/code_review"
ROUTES = (ROUTE_CATALOG, ROUTE_DIAGNOSTICS, ROUTE_HEALTHZ, ROUTE_ARTIFACT,
          ROUTE_CODE_REVIEW)
CONTENT_TYPE = "application/json; charset=utf-8"
HEADER_FRESHNESS_HEALTH = "X-DI-Freshness-Health"
HEADER_FRESHNESS_PROBLEM = "X-DI-Freshness-Problem"
MAX_CONCURRENT_REQUESTS = 8
REQUEST_TIMEOUT_SECONDS = 10
MAX_DISCOVERY_FILE_BYTES = 4096
DISCOVERY_KEYS = ("port", "token", "url")
DISCOVERY_ABSENT = "discovery_file_absent"
DISCOVERY_UNREADABLE = "discovery_file_unreadable"
DISCOVERY_MALFORMED = "discovery_file_malformed"
_WAIT_FIELD = "wait_seconds"
_WAIT_TEXT = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3})?")
#: ``/artifact``'s three fields and their grammar: a RE-DECLARATION of
#: ``artifact_content``'s (this module may not import it; parity pinned by
#: test). A reference, never a path, root or URL.
_ARTIFACT_FIELDS = (
    ("mission_ref", re.compile(r"[a-z0-9][a-z0-9._-]{0,63}:mn-[0-9a-f]{32}")),
    ("artifact_id", re.compile(r"mf-[0-9a-f]{32}")),
    ("cursor", re.compile(r"[0-9a-f]{64}")),
)
#: ``/code_review``'s fields and their grammar: a RE-DECLARATION of
#: ``code_review``'s (parity pinned by test). The overview takes the first
#: two; one file's diff takes all four. A reference, never a path, root,
#: ref, pathspec or git option.
_CODE_REVIEW_FIELDS = (
    ("mission_ref", re.compile(r"[a-z0-9][a-z0-9._-]{0,63}:mn-[0-9a-f]{32}")),
    ("task_id", re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")),
    ("snapshot", re.compile(r"[0-9a-f]{64}")),
    ("file_id", re.compile(r"rf-[0-9a-f]{32}")),
)
_REFUSED_METHODS = ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
                    "TRACE", "CONNECT")
# The standard library's own refusals, mapped onto this surface's statuses:
# over-long request line or headers -> 413, unknown method -> 405, any other
# server-class status (e.g. an unsupported HTTP version) -> 400.
_STDLIB_STATUS = {414: 413, 431: 413, 501: 405}
_REFUSALS = {400: "bad_request", 401: "bearer_token_required",
             403: "host_origin_or_peer_refused", 404: "no_such_route",
             405: "method_not_allowed", 413: "request_over_bound"}

EXIT_OK = 0
EXIT_USAGE = 2


class NotLoopbackError(Exception):
    """Asked to bind, or found bound, anywhere but ``LOOPBACK_HOST``."""


def require_loopback(host):
    """Refuse anything that is not exactly 127.0.0.1: no wildcard, no other
    interface, no other loopback address, no name to resolve."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address != _LOOPBACK_ADDRESS:
        raise NotLoopbackError("the catalog adapter binds only to %s;"
                               " refused %r" % (LOOPBACK_HOST, host))


def encode_answer(answer):
    """The body: exactly the bytes ``catalog.serve`` writes for an answer
    line."""
    return (json.dumps(answer, sort_keys=True) + "\n").encode("utf-8")


def serve_time_answer(answer, now,
                      freshness=contract_module.response_freshness):
    """``(answer, health, problem)`` for one owner answer at serve time
    ``now`` (see the module's SERVE-TIME FRESHNESS), judged by ``freshness``,
    the served version's consumer operation. The owner's object is never
    modified; a withheld answer is a new envelope."""
    response = answer["response"]
    if response is None:
        return answer, contract_module.HEALTH_UNAVAILABLE, answer["problem"]
    health, problem = freshness(response, now)
    if health == contract_module.HEALTH_FRESH:
        return answer, health, None
    return dict(
        answer, response=None, problem=problem,
        detail="the response evaluated %s (%s) at serve time %d; it is"
        " withheld and nothing in it may be presented as live"
        % (health, problem, now),
        served=catalog_module.SERVED_NONE), health, problem


def _wait_seconds(query):
    """The ``wait_seconds`` of a ``/catalog`` query string, or None when the
    query is refused."""
    if not query:
        return catalog_module.DEFAULT_WAIT_SECONDS
    try:
        fields = parse_qsl(query, keep_blank_values=True,
                           strict_parsing=True, max_num_fields=1)
    except ValueError:
        return None
    if len(fields) != 1 or fields[0][0] != _WAIT_FIELD or (
            _WAIT_TEXT.fullmatch(fields[0][1]) is None):
        return None
    text = fields[0][1]
    wait = float(text) if "." in text else int(text)
    if not 0 < wait <= catalog_module.MAX_WAIT_SECONDS:
        return None
    return wait


def _artifact_query(query):
    """``(mission_ref, artifact_id, cursor)`` of an ``/artifact`` query
    string: exactly those three fields, each once, each of its fixed
    grammar; None when refused."""
    try:
        fields = parse_qsl(query, keep_blank_values=True,
                           strict_parsing=True,
                           max_num_fields=len(_ARTIFACT_FIELDS))
    except ValueError:
        return None
    values = dict(fields)
    if len(fields) != len(_ARTIFACT_FIELDS) or len(values) != len(fields):
        return None
    found = []
    for name, grammar in _ARTIFACT_FIELDS:
        value = values.get(name)
        if value is None or grammar.fullmatch(value) is None:
            return None
        found.append(value)
    return tuple(found)


def _code_review_query(query):
    """``(mission_ref, task_id, snapshot, file_id)`` of a ``/code_review``
    query string, each once and of its fixed grammar: the overview is
    exactly ``mission_ref`` and ``task_id`` (``snapshot`` and ``file_id``
    None); one file is all four. None when refused (any other field, a
    repeat, or ``snapshot`` without ``file_id`` or the reverse)."""
    names = [name for name, _grammar in _CODE_REVIEW_FIELDS]
    try:
        fields = parse_qsl(query, keep_blank_values=True,
                           strict_parsing=True, max_num_fields=len(names))
    except ValueError:
        return None
    values = dict(fields)
    if len(values) != len(fields) or not set(values) <= set(names) or (
            "mission_ref" not in values or "task_id" not in values):
        return None
    if ("snapshot" in values) != ("file_id" in values):
        return None
    for name, grammar in _CODE_REVIEW_FIELDS:
        if name in values and grammar.fullmatch(values[name]) is None:
            return None
    return tuple(values.get(name) for name in names)


# -- the discovery file ----------------------------------------------------

def _private_directory(directory):
    """Create the leaf directory 0700 if absent; otherwise require a real
    directory owned by this user with no group/other access."""
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or (
            info.st_mode & 0o077):
        raise PermissionError("the discovery directory must be a real,"
                              " owner-only (0700) directory of this user")


def publish_discovery_file(path, port, token):
    """Publish the discovery object at ``path`` ATOMICALLY and NO-CLOBBER: an
    exclusive 0600 temp file of this call's own, fsynced, then hard-linked to
    ``path`` with ``os.link``, which FAILS if ``path`` exists in any form, so
    of concurrent publications exactly one wins and no loser ever touches the
    winner's file; the temp name is removed either way. Returns the published
    file's ``(st_dev, st_ino)``."""
    if not os.path.isabs(path) or os.path.basename(path) in ("", ".", ".."):
        raise ValueError("the discovery file must be an absolute file path")
    directory = os.path.dirname(path)
    _private_directory(directory)
    temp = os.path.join(directory, ".%s.%d.%s.tmp" % (
        os.path.basename(path), os.getpid(), secrets.token_hex(8)))
    data = (json.dumps({"port": port, "token": token,
                        "url": "http://%s:%d" % (LOOPBACK_HOST, port)},
                       sort_keys=True) + "\n").encode("ascii")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_CLOEXEC, 0o600)
    try:
        try:
            while data:
                data = data[os.write(fd, data):]
            os.fsync(fd)
            info = os.fstat(fd)
        finally:
            os.close(fd)
        try:
            os.link(temp, path)
        except FileExistsError:
            raise FileExistsError("a discovery file already exists; this"
                                  " launch does not own it and will not"
                                  " overwrite it")
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass
    return info.st_dev, info.st_ino


def remove_discovery_file(path, identity):
    """Remove the discovery file only while it is still the one written."""
    try:
        current = os.lstat(path)
    except OSError:
        return
    if (current.st_dev, current.st_ino) == identity:
        os.unlink(path)


def _discovery_safe(info):
    """A regular file of this user, owner-only, within the size bound."""
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and (
        not info.st_mode & 0o077) and info.st_size <= MAX_DISCOVERY_FILE_BYTES


def read_discovery_file(path):
    """The CONSUMER's reference reader: ``(discovery, None)`` or ``(None,
    problem)``, where any problem means the adapter is UNAVAILABLE to this
    consumer. Reads only; never creates, repairs or removes anything. Every
    check is applied to the OPENED descriptor as well as to the path, and
    the descriptor must be the very file the path named when checked."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, DISCOVERY_ABSENT
    except OSError:
        return None, DISCOVERY_UNREADABLE
    if not _discovery_safe(info):
        return None, DISCOVERY_UNREADABLE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC)
    except OSError:
        return None, DISCOVERY_UNREADABLE
    try:
        opened = os.fstat(fd)
        if not _discovery_safe(opened) or (opened.st_dev, opened.st_ino) != (
                info.st_dev, info.st_ino):
            return None, DISCOVERY_UNREADABLE     # replaced since the check
        raw = os.read(fd, MAX_DISCOVERY_FILE_BYTES + 1)
    except OSError:
        return None, DISCOVERY_UNREADABLE
    finally:
        os.close(fd)
    if len(raw) > MAX_DISCOVERY_FILE_BYTES:
        return None, DISCOVERY_UNREADABLE
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, RecursionError):
        return None, DISCOVERY_MALFORMED
    if type(value) is not dict or set(value) != set(DISCOVERY_KEYS) or (
            type(value["port"]) is not int) or not (
                1 <= value["port"] <= 65535) or type(
                    value["token"]) is not str or not value["token"] or (
                        value["url"] != "http://%s:%d" % (LOOPBACK_HOST,
                                                          value["port"])):
        return None, DISCOVERY_MALFORMED
    return value, None


# -- the HTTP surface ------------------------------------------------------

class CatalogRequestHandler(http.server.BaseHTTPRequestHandler):
    """Answers the four read routes; refuses everything else."""

    timeout = REQUEST_TIMEOUT_SECONDS

    def version_string(self):
        return "dicatalog-http"

    def log_message(self, format, *args):
        """Nothing is logged per request."""

    def send_error(self, code, message=None, explain=None):
        """Every refusal, the standard library's included, as this
        surface's JSON refusal with its mapped status; never a 5xx."""
        code = _STDLIB_STATUS.get(code, code)
        if code >= 500 or code not in _REFUSALS:
            code = 400
        # A refusal always carries its status line and headers, including
        # one for a request still taken for HTTP/0.9 (which the standard
        # library would otherwise answer with a bare body).
        if self.request_version == "HTTP/0.9":
            self.request_version = self.protocol_version
        self.close_connection = True
        self._reply(code, encode_answer({"error": _REFUSALS[code]}),
                    (("WWW-Authenticate", "Bearer"),) if code == 401
                    else (("Allow", "GET"),) if code == 405 else ())

    def _reply(self, status, body, headers=()):
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
            self.send_error(400)          # e.g. HTTP/0.9: no status line,
            return False                  # no headers, so never answered
        if len(self.raw_requestline) > catalog_module.MAX_REQUEST_LINE_BYTES:
            self.send_error(413)
            return False
        bound = "%s:%d" % (LOOPBACK_HOST, self.server.server_address[1])
        hosts = self.headers.get_all("Host") or []
        origins = self.headers.get_all("Origin") or []
        if hosts != [bound] or origins not in ([], ["http://" + bound]):
            self.send_error(403)
            return False
        if self.headers.get_all("Content-Length") or self.headers.get_all(
                "Transfer-Encoding"):
            self.send_error(400)
            return False
        if not self.server.bearer_ok(self.headers.get_all("Authorization")):
            self.send_error(401)
            return False
        return True

    def _catalog(self, status, answer):
        answer, health, problem = serve_time_answer(answer,
                                                    self.server.clock(),
                                                    self.server.freshness)
        headers = [(HEADER_FRESHNESS_HEALTH, health)]
        if problem is not None:
            headers.append((HEADER_FRESHNESS_PROBLEM, problem))
        self._reply(status, encode_answer(answer), headers)

    def do_GET(self):
        if not self._admitted():
            return
        # Routed on the request target EXACTLY as sent: the standard
        # library rewrites a leading "//" in ``self.path``, which would
        # otherwise make "//catalog" a fourth spelling of a route.
        raw = self.requestline.split()[1]
        target = urlsplit(raw)
        if not raw.startswith("/") or raw.startswith("//") or (
                target.fragment) or target.path not in ROUTES:
            self.send_error(404)
            return
        owner = self.server.owner
        if target.path == ROUTE_CATALOG:
            wait = _wait_seconds(target.query)
            if wait is None:
                self._catalog(400, catalog_module.catalog_answer(
                    self.server.clock(), None,
                    catalog_module.PROBLEM_BAD_REQUEST,
                    "expected GET /catalog with an optional wait_seconds in"
                    " (0, %d]" % catalog_module.MAX_WAIT_SECONDS, None))
                return
            self._catalog(200, owner.request(wait))
            return
        if target.path == ROUTE_ARTIFACT:
            request = _artifact_query(target.query)
            if request is None:
                self.send_error(400)
                return
            self._reply(200, encode_answer(owner.artifact_content(*request)))
            return
        if target.path == ROUTE_CODE_REVIEW:
            request = _code_review_query(target.query)
            if request is None:
                self.send_error(400)
                return
            self._reply(200, encode_answer(owner.code_review(*request)))
            return
        if target.query:
            self.send_error(400)
            return
        if target.path == ROUTE_DIAGNOSTICS:
            self._reply(200, encode_answer({
                "endpoint": catalog_module.ENDPOINT,
                "endpoint_version": catalog_module.ENDPOINT_VERSION,
                "diagnostics": owner.diagnostics()}))
            return
        self._reply(200, encode_answer({
            "endpoint": catalog_module.ENDPOINT,
            "endpoint_version": catalog_module.ENDPOINT_VERSION,
            "healthz": "ok"}))

    def _method_refused(self):
        self.send_error(405)


for _method in _REFUSED_METHODS:
    setattr(CatalogRequestHandler, "do_" + _method,
            CatalogRequestHandler._method_refused)


class LoopbackCatalogServer(socketserver.ThreadingMixIn,
                            http.server.HTTPServer):
    """The HTTP listener: loopback only, bearer required, a bounded number
    of connections in progress, one shared catalog owner whose clock is the
    serve-time clock. A handler can hold the owner's slot, so handlers are
    NOT daemon threads: ``server_close`` joins every one (the standard
    library joins only non-daemon handlers), and ``handlers_in_progress``
    counts those not yet finished."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, owner, bearer_token, clock, port=0,
                 freshness=contract_module.response_freshness):
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("a bearer token is required")
        self.owner = owner
        self.clock = clock
        self.freshness = freshness
        self._bearer = ("Bearer " + bearer_token).encode("ascii")
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self._active = 0
        self._active_lock = threading.Lock()
        http.server.HTTPServer.__init__(self, (LOOPBACK_HOST, port),
                                        CatalogRequestHandler)

    def handlers_in_progress(self):
        with self._active_lock:
            return self._active

    def _handler_done(self):
        with self._active_lock:
            self._active -= 1
        self._slots.release()

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
            self.shutdown_request(request)      # over the bound: unanswered
            return
        with self._active_lock:
            self._active += 1      # counted BEFORE its thread can start
        try:
            socketserver.ThreadingMixIn.process_request(self, request,
                                                        client_address)
        except BaseException:
            self._handler_done()
            raise

    def process_request_thread(self, request, client_address):
        try:
            socketserver.ThreadingMixIn.process_request_thread(
                self, request, client_address)
        finally:
            self._handler_done()

    def handle_error(self, request, client_address):
        print("dicatalog_http: a request failed in the adapter",
              file=sys.stderr)

    def url(self):
        return "http://%s:%d" % (LOOPBACK_HOST, self.server_address[1])


def _parser():
    parser = argparse.ArgumentParser(
        prog="dicatalog_http.py",
        description="A passive loopback HTTP adapter over the mission catalog"
        " owner: GET /catalog, /diagnostics and /healthz only, a per-launch"
        " bearer token, 127.0.0.1 only. It reads registered sources and"
        " controls nothing; registration stays dicatalog.py.")
    parser.add_argument("--registry", required=True, metavar="DIR")
    parser.add_argument("--discovery-file", required=True, metavar="PATH",
                        help="absolute path of the NEW owner-only file to"
                        " write {url, port, token} to (its directory is"
                        " created 0700 if absent); removed at shutdown")
    parser.add_argument("--port", type=int, default=0,
                        help="loopback port (0: ephemeral, the default)")
    parser.add_argument("--herd-root", action="append", metavar="PATH",
                        help="as dicatalog.py serve: an absolute, resolved"
                        " herd root for the runtime snapshot")
    parser.add_argument("--discover-roots", action="store_true",
                        help="as dicatalog.py serve --discover-roots")
    parser.add_argument("--contract-version", type=int, choices=(1, 2),
                        default=1,
                        help="the ONE query contract version this process"
                        " serves for its whole life (default 1); comparing"
                        " two versions takes two processes")
    parser.add_argument("--delivery-store", metavar="DIR",
                        help="version 2 only: the explicitly registered"
                        " delivery store directory")
    parser.add_argument("--delivery-presentations", metavar="DIR",
                        help="version 2 only: the explicitly registered Grok"
                        " Bot state directory whose delivery presentations"
                        " are read (read-only; only the"
                        " delivery_presentations map)")
    return parser


def main(argv=None, clock=None, listing_argv=None):
    """``listing_argv`` exists for hermetic tests, as in ``catalog.main``.
    Runs in the main thread (it installs the stop signals)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else EXIT_USAGE
    clock = clock or snapshot_module.production_clock
    paths = [args.registry, args.discovery_file] + list(
        args.herd_root or []) + ([args.delivery_store]
                                 if args.delivery_store else []) + (
            [args.delivery_presentations]
            if args.delivery_presentations else [])
    for path in paths:
        if not os.path.isabs(path):
            print("dicatalog_http: paths must be absolute: %s" % path,
                  file=sys.stderr)
            return EXIT_USAGE
    if not 0 <= args.port <= 65535:
        print("dicatalog_http: --port must be in [0, 65535]", file=sys.stderr)
        return EXIT_USAGE
    if args.delivery_store and args.contract_version == 1:
        print("dicatalog_http: --delivery-store needs --contract-version 2",
              file=sys.stderr)
        return EXIT_USAGE
    if args.delivery_presentations and args.contract_version == 1:
        print("dicatalog_http: --delivery-presentations needs"
              " --contract-version 2", file=sys.stderr)
        return EXIT_USAGE
    roots = args.herd_root or None
    discover = bool(args.discover_roots)
    if not roots and not discover:
        # Without a runtime source the owner takes no snapshot, so no
        # binding and no member can ever be reported: refused, never served
        # as a usable but memberless catalog.
        print("dicatalog_http: a runtime source is required: --discover-roots"
              " (roots named by canonical bindings in the REGISTERED sources)"
              " or --herd-root PATH; without one the catalog has no runtime"
              " snapshot and reports no member", file=sys.stderr)
        return EXIT_USAGE
    argv_for_listing = listing_argv or snapshot_module.LISTING_ARGV
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    owner = None
    server = None
    identity = None
    try:
        options = {} if args.contract_version == 1 else {
            "contract_version": args.contract_version,
            "delivery_store": args.delivery_store,
            "delivery_presentations": args.delivery_presentations}
        owner = catalog_module.CatalogService(args.registry, clock, roots,
                                              argv_for_listing,
                                              discover=discover, **options)
        token = secrets.token_urlsafe(32)
        server = LoopbackCatalogServer(owner, token, clock, args.port,
                                       owner.response_freshness)
        try:
            identity = publish_discovery_file(
                args.discovery_file, server.server_address[1], token)
        except (OSError, ValueError) as exc:
            print("dicatalog_http: cannot publish the discovery file (%s: %s)"
                  % (type(exc).__name__, exc), file=sys.stderr)
            return EXIT_USAGE
        finally:
            del token
        threading.Thread(target=server.serve_forever, name="catalog-http",
                         daemon=True).start()
        started = {
            "endpoint": catalog_module.ENDPOINT,
            "endpoint_version": catalog_module.ENDPOINT_VERSION,
            "url": server.url(), "routes": list(ROUTES),
            "discovery_file": args.discovery_file, "pid": os.getpid()}
        if args.contract_version != 1:
            started["contract_version"] = args.contract_version
        sys.stdout.write(json.dumps(started, sort_keys=True) + "\n")
        sys.stdout.flush()
        while not stop.wait(1):
            pass
        remove_discovery_file(args.discovery_file, identity)
        server.shutdown()
        return EXIT_OK
    finally:
        # The discovery file first (consumers stop finding a closing server),
        # then new work is refused, in-flight requests are joined (each
        # bounded by its wait), then the runtime owner's owned work.
        if identity is not None:
            remove_discovery_file(args.discovery_file, identity)
        if owner is not None:
            owner.shutdown()
        if server is not None:
            server.server_close()
        if owner is not None:
            owner.settle_runtime(None)
            print("dicatalog_http: stopped %s" % json.dumps({
                "handlers_in_progress": 0 if server is None
                else server.handlers_in_progress(),
                "owner": owner.diagnostics()}, sort_keys=True),
                file=sys.stderr)
