"""Tests for the panel's HTTP adapter (``mission_panel.http_adapter``).

A REAL loopback server over the REAL controller, surface, Mission Core,
journal and bridge (the controller suite's fixtures); raw ``http.client`` for
every transport rule. This is the bounded, authenticated local interface the
renderer's server talks to, and nothing else: loopback only, a per-launch
bearer, Host and Origin pinned, fixed routes, closed JSON bodies, no shell, no
PTY, no filesystem and no command route. Nothing here is evidence of a live
provider or Herdr.
"""

import atexit
import contextlib
import http.client
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _hermetic_git import run_git  # noqa: E402
from mission_panel import http_adapter  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from test_local_request import Bounded  # noqa: E402
from test_mission_bridge import raw_observation  # noqa: E402
from test_mission_panel_service import PanelFixture, PROPOSAL_REPLY  # noqa: E402

TOKEN = "t" * 43
# Test-side bound on waiting for a launched process's one startup line.
STARTUP_WAIT_SECONDS = 30

# Every process a fixture launched and has not yet reaped. unittest skips
# ``addCleanup`` when a KeyboardInterrupt propagates (SIGINT, or SIGTERM routed
# the same way below), so the interpreter's exit reaps what is left, bounded.
# This covers ORDINARY interruption and failure; a forced kill of the test
# process itself is not something a fixture can guard against.
_LAUNCHED = []


def reap_process(process):
    """Stop and reap ONE exact process this test launched, bounded."""
    if process in _LAUNCHED:
        _LAUNCHED.remove(process)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _reap_launched_at_exit():
    for process in list(_LAUNCHED):
        reap_process(process)


atexit.register(_reap_launched_at_exit)


def _interrupt(signum, frame):
    raise KeyboardInterrupt("signal %d" % signum)


# Every socket name-resolution entry point raises an audit event BEFORE it runs, and
# ``socket.getfqdn`` reaches one (``gethostbyaddr``). A hook that raises there stops a lookup
# before it can block, so a regression fails at once even on a machine whose resolver stalls.
# Audit hooks cannot be removed: this one is installed on first use and inert unless armed,
# and it only acts on the thread that armed it.
RESOLVER_EVENTS = frozenset((
    "socket.getaddrinfo", "socket.getnameinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "socket.gethostname"))
_armed_thread = []                    # [thread id] while armed, else []
_hook_installed = []


class ResolverConsulted(AssertionError):
    pass


def _resolver_tripwire(event, args):
    if event in RESOLVER_EVENTS and _armed_thread == [threading.get_ident()]:
        raise ResolverConsulted("a name resolver was consulted: %s%r" % (event, args))


@contextlib.contextmanager
def no_resolver():
    """While active, a name-resolution call on THIS thread raises ``ResolverConsulted``."""
    if not _hook_installed:
        sys.addaudithook(_resolver_tripwire)
        _hook_installed.append(True)
    _armed_thread.append(threading.get_ident())
    try:
        yield
    finally:
        _armed_thread.pop()


def startup_line(case, process, wait=STARTUP_WAIT_SECONDS):
    """The launched process's one startup line, parsed.

    Bounded at BOTH ends. The wait for the line is bounded (``select``), and
    the process's stderr is read ONLY on the failure path and only once the
    process is known to have exited: a healthy launcher never closes its
    stderr, so reading it eagerly (for instance as an assertion's message
    argument, which Python evaluates before the assertion runs) blocks forever
    on SUCCESS. The failure message therefore carries the real stderr, bounded,
    and is computed only when there is no line."""
    ready, _, _ = select.select([process.stdout], [], [], wait)
    line = process.stdout.readline() if ready else b""
    if not line:
        how = "it had already exited on its own"
        if process.poll() is None:
            how = ("it was still running, so this test sent it SIGTERM; the exit status after"
                   " that is the test's doing and says nothing about whether it would have started")
            process.terminate()                 # this test's own exact child
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                how = ("it was still running, so this test sent it SIGTERM and, when it did"
                       " not exit, killed it")
                process.kill()
                process.wait(10)
        evidence = process.stderr.read().decode("utf-8", "replace")[:2000]
        case.assertTrue(line, "the launcher printed no startup line within %ss; %s (exit %r);"
                        " stderr: %s" % (wait, how, process.returncode, evidence))
    return json.loads(line)


class Server(PanelFixture):

    def setUp(self):
        super(Server, self).setUp()
        self.server = http_adapter.PanelServer(self.service, TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def call(self, method, path, body=None, headers=None, token=TOKEN, raw_body=None):
        """One request; returns ``(status, headers, parsed_body_or_text)``."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": "127.0.0.1:%d" % self.port}
        if token is not None:
            sent["Authorization"] = "Bearer " + token
        if body is not None or raw_body is not None:
            payload = raw_body if raw_body is not None else json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
            sent["Content-Length"] = str(len(payload))
        else:
            payload = None
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in sent.items():
                connection.putheader(name, value)
            connection.endheaders(payload)
            response = connection.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data)
            except ValueError:
                parsed = data.decode("utf-8", "replace")
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def ok(self, method, path, body=None):
        status, _, answer = self.call(method, path, body)
        self.assertEqual(status, 200, answer)
        return answer


class AccessBoundaryTests(Server):

    def test_H1_the_bearer_is_required_exactly_once(self):
        for headers, token in ((None, None), (None, "wrong"), (None, TOKEN + "x"),
                               ({"Authorization": "Bearer " + TOKEN + ", Bearer " + TOKEN}, None)):
            with self.subTest(token=token, headers=headers):
                status, reply_headers, answer = self.call(
                    "GET", "/healthz", headers=headers, token=token)
                self.assertEqual(status, 401)
                self.assertEqual(answer, {"error": "bearer_token_required"})
                self.assertEqual(reply_headers.get("WWW-Authenticate"), "Bearer")
        self.assertEqual(self.call("GET", "/healthz")[0], 200)

    def test_H2_host_and_origin_are_pinned(self):
        bound = "127.0.0.1:%d" % self.port
        for headers in ({"Host": "localhost:%d" % self.port}, {"Host": "evil.example"},
                        {"Host": "127.0.0.1:1"}, {"Origin": "http://evil.example"},
                        {"Origin": "http://127.0.0.1:1"},
                        {"Origin": "http://localhost:%d" % self.port},
                        {"Origin": "null"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.call("GET", "/healthz", headers=headers)[0], 403)
        self.assertEqual(self.call("GET", "/healthz",
                                   headers={"Origin": "http://" + bound})[0], 200)

    def test_H3_a_browser_page_cannot_post_here(self):
        # A cross-origin page (even one on loopback) fails the Origin pin before
        # the body is read or the bearer is compared.
        for origin in ("http://evil.example", "http://127.0.0.1:9"):
            status, _, _ = self.call("POST", "/conversations/turn",
                                     {"conversation_id": None, "text": "x"},
                                     headers={"Origin": origin})
            self.assertEqual(status, 403)
        self.assertEqual(self.journal.load()["turns"], {})

    def test_H4_only_the_documented_methods_and_paths_exist(self):
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                self.assertEqual(self.call(method, "/overview")[0], 405)
        self.assertEqual(self.call("GET", "/requests/lr-" + "0" * 32 + "/offer")[0], 405,
                         "issuing an offer records bookkeeping, so it is not a GET")
        for path in ("/", "/catalog", "/diagnostics", "/artifact", "/shell", "/exec",
                     "/files", "/requests", "/requests/", "/requests/lr-" + "0" * 32 + "/x",
                     "/requests/../overview", "//overview", "/overview/", "/Overview",
                     "/requests/lr-" + "G" * 32, "/conversations/cv-short",
                     "/requests/lr-%s" % ("0" * 32) + "%2f..", "/%2e%2e/etc/passwd"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0], 404)

    def test_H5_a_query_string_or_fragment_is_refused_there_are_no_query_fields(self):
        for path in ("/overview?x=1", "/healthz?", "/requests/lr-%s?a=b" % ("0" * 32),
                     "/overview#frag"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0], 400)

    def test_H6_a_get_with_a_body_is_refused(self):
        status, _, _ = self.call("GET", "/overview", body={"a": 1})
        self.assertEqual(status, 400)

    def test_H7_responses_carry_no_cors_cookie_or_cache_and_are_json(self):
        status, headers, _ = self.call("GET", "/overview")
        lowered = dict((k.lower(), v) for k, v in headers.items())
        self.assertEqual(lowered["content-type"], "application/json; charset=utf-8")
        self.assertEqual(lowered["cache-control"], "no-store")
        self.assertEqual(lowered["x-content-type-options"], "nosniff")
        for forbidden in ("access-control-allow-origin", "set-cookie",
                          "access-control-allow-credentials"):
            self.assertNotIn(forbidden, lowered)

    def test_H8_the_server_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with self.assertRaises(catalog_http.NotLoopbackError):
            http_adapter.PanelServer(self.service, TOKEN, host="0.0.0.0")

    def test_H9_a_bearer_is_mandatory_at_construction(self):
        for bad in ("", None, 7):
            with self.assertRaises(ValueError):
                http_adapter.PanelServer(self.service, bad)

    def test_H10_connections_past_the_bound_are_closed_unanswered(self):
        held = []
        try:
            for _ in range(http_adapter.MAX_CONCURRENT_REQUESTS):
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                held.append(sock)         # connected, sending nothing: a slot held
            time.sleep(0.3)
            extra = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            extra.sendall(("GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                           "Authorization: Bearer %s\r\n\r\n" % (self.port, TOKEN)).encode())
            extra.settimeout(5)
            try:
                answer = extra.recv(100)
            except ConnectionResetError:
                answer = b""            # closed with our request unread: a reset
            self.assertEqual(answer, b"", "a connection past the bound was answered")
            extra.close()
        finally:
            for sock in held:
                sock.close()

    def test_H11_binding_consults_no_resolver(self):
        # The PROPERTY, not a symptom. HTTPServer.server_bind resolves the server's own name
        # (socket.getfqdn): an unbounded reverse lookup on the way to the startup line, whose
        # result nothing here reads. A bind that never asks a resolver cannot stall on one;
        # timing a fast start would pass on any machine with working DNS and prove nothing.
        with no_resolver():
            with self.assertRaises(ResolverConsulted):      # the wire is live: the stdlib call
                socket.getfqdn("127.0.0.1")                 # is stopped before it can run
            server = http_adapter.PanelServer(self.service, TOKEN)
        try:
            host, port = server.server_address[:2]
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 0)
            self.assertEqual((server.server_name, server.server_port), (host, port))
        finally:
            server.server_close()


class BodyTests(Server):

    def post(self, path, body=None, **kwargs):
        return self.call("POST", path, body, **kwargs)

    def test_B1_a_post_needs_a_content_length_and_json_content_type(self):
        self.assertEqual(self.post("/conversations/turn", raw_body=b"{}",
                                   headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.post("/conversations/turn", raw_body=b"{}",
                                   headers={"Content-Type": None})[0], 400)
        self.assertEqual(self.post("/conversations/turn", raw_body=b"{}",
                                   headers={"Content-Type": "application/json; charset=utf-16"})[0], 400)
        self.assertEqual(self.post("/conversations/turn", raw_body=b"{}",
                                   headers={"Content-Length": None})[0], 400)

    def test_B2_chunked_transfer_is_refused(self):
        status, _, _ = self.post("/conversations/turn", raw_body=b"{}",
                                 headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(status, 400)

    def test_B3_an_oversize_body_is_refused_before_it_is_read(self):
        # Refused on the DECLARED length alone: the body is never sent or read.
        status, _, _ = self.post(
            "/conversations/turn", raw_body=b"",
            headers={"Content-Length": str(http_adapter.MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.journal.load()["turns"], {})

    def test_B4_bodies_that_are_not_one_closed_json_object_are_refused(self):
        for raw in (b"", b"[]", b"7", b"null", b'"x"', b"{", b'{"a":1,"a":2}',
                    b'{"text": NaN}', b'{"text": Infinity}', b"\xff\xfe",
                    b'{"conversation_id": null, "text": "x", "extra": 1}',
                    b'{"text": "x"}',
                    b"[" * 5000 + b"]" * 5000,
                    b'{"text": "' + b"x" * 10 + b'"',
                    ):
            with self.subTest(raw=raw[:30]):
                status, _, answer = self.post("/conversations/turn", raw_body=raw)
                self.assertEqual(status, 400, answer)
        self.assertEqual(self.journal.load()["turns"], {})

    def test_B5_a_refusal_names_the_offending_key_and_nothing_else(self):
        status, _, answer = self.post("/conversations/turn", {
            "conversation_id": None, "text": "x", "approved": True})
        self.assertEqual(status, 400)
        self.assertIn("approved", answer["error"])

    def test_B6_deeply_nested_json_is_refused_not_a_crash(self):
        nested = json.dumps({"conversation_id": None,
                             "text": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}})
        status, _, answer = self.post("/conversations/turn", raw_body=nested.encode())
        self.assertEqual(status, 400, answer)

    def test_B7_domain_refusals_are_200_with_a_problem_code_and_no_traceback(self):
        status, _, answer = self.post("/conversations/turn",
                                      {"conversation_id": None, "text": "   "})
        self.assertEqual(status, 200)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["problem"], "mission_panel_bad_text")
        self.assertNotIn("Traceback", json.dumps(answer))

    def test_B8_an_unexpected_failure_is_a_200_internal_refusal_never_a_5xx(self):
        def explode(*args, **kwargs):
            raise RuntimeError("secret internal detail /Users/someone/private")
        self.service.overview = explode
        status, _, answer = self.call("GET", "/overview")
        self.assertEqual(status, 200)
        self.assertEqual(answer["problem"], "mission_panel_internal")
        text = json.dumps(answer)
        self.assertNotIn("secret internal detail", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("RuntimeError", text)

    def test_B9_the_bearer_never_appears_in_any_answer(self):
        everything = json.dumps([self.call("GET", "/overview"),
                                 self.call("GET", "/nope"),
                                 self.call("GET", "/healthz", token="wrong")])
        self.assertNotIn(TOKEN, everything)


class EndToEndOverHttpTests(Server):

    def test_E1_the_whole_lifecycle_through_the_routes_and_nothing_else(self):
        self.provider.script.append(PROPOSAL_REPLY)
        turn = self.ok("POST", "/conversations/turn",
                       {"conversation_id": None, "text": "stabilise the probe"})["turn"]
        self.assertTrue(self.convo.idle(15))
        conversation = self.ok("GET", "/conversations/%s" % turn["conversation_id"])
        shown = conversation["turns"][-1]
        self.assertEqual(shown["state"], "COMPLETED")
        self.assertEqual(self.ok("GET", "/overview")["requests"], [])
        submitted = self.ok("POST", "/proposals/submit", {
            "turn_id": shown["turn_id"],
            "draft_digest_sha256": shown["draft"]["proposal_digest_sha256"],
            "supersedes": None})
        request = submitted["request"]
        ref = request["request_ref"]
        self.assertEqual(request["lifecycle"]["stage"], "of_record")
        offer = self.ok("POST", "/requests/%s/offer" % ref, {})
        self.assertEqual(offer["reply"], "approve")
        again = self.ok("POST", "/requests/%s/offer" % ref, {})
        self.assertEqual(again["offer_digest_sha256"], offer["offer_digest_sha256"],
                         "a second window is shown the same frozen offer")

        def bound(reply):
            return {"mission_id": offer["mission_id"], "revision": offer["revision"],
                    "proposal_digest_sha256": offer["proposal_digest_sha256"], "reply": reply,
                    "offer_digest_sha256": offer["offer_digest_sha256"],
                    "expires_at": offer["expires_at"],
                    "approved_action_scope": offer["approved_action_scope"],
                    "approved_delivery_targets": offer["approved_delivery_targets"],
                    "repository_url": offer["repository_url"]}
        refused = self.ok("POST", "/requests/%s/decide" % ref, bound("yes please"))
        self.assertEqual(refused["problem"], "local_request_reply_not_affirmative")
        decided = self.ok("POST", "/requests/%s/decide" % ref, bound("approve"))
        self.assertEqual(decided["request"]["lifecycle"]["approval_expires_at"], offer["expires_at"])
        self.assertEqual(decided["request"]["lifecycle"]["stage"], "approved")
        self.ok("POST", "/requests/%s/run" % ref, {"command": "dispatch"})
        self.observer.raw = raw_observation()
        self.ok("POST", "/requests/%s/run" % ref, {"command": "observe"})
        self.write_artifacts(offer["mission_id"])
        self.observer.raw = raw_observation(status="COMPLETE")
        done = self.ok("POST", "/requests/%s/run" % ref, {"command": "verify"})
        self.assertEqual(done["request"]["lifecycle"]["stage"], "verified")
        result = self.ok("GET", "/requests/%s/result" % ref)
        self.assertTrue(result["recoverable"])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_E2_a_second_window_reads_the_same_records(self):
        self.provider.script.append(PROPOSAL_REPLY)
        turn = self.ok("POST", "/conversations/turn",
                       {"conversation_id": None, "text": "stabilise the probe"})["turn"]
        self.assertTrue(self.convo.idle(15))
        shown = self.ok("GET", "/conversations/%s" % turn["conversation_id"])["turns"][-1]
        first = self.ok("POST", "/proposals/submit", {
            "turn_id": shown["turn_id"],
            "draft_digest_sha256": shown["draft"]["proposal_digest_sha256"]})
        again = self.ok("POST", "/proposals/submit", {
            "turn_id": shown["turn_id"],
            "draft_digest_sha256": shown["draft"]["proposal_digest_sha256"]})
        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertEqual(len(self.ok("GET", "/overview")["requests"]), 1)

    def test_E3_the_withdrawn_proposal_stays_in_the_overview_with_its_stage(self):
        self.provider.script.append(PROPOSAL_REPLY)
        turn = self.ok("POST", "/conversations/turn",
                       {"conversation_id": None, "text": "x"})["turn"]
        self.assertTrue(self.convo.idle(15))
        shown = self.ok("GET", "/conversations/%s" % turn["conversation_id"])["turns"][-1]
        request = self.ok("POST", "/proposals/submit", {
            "turn_id": shown["turn_id"],
            "draft_digest_sha256": shown["draft"]["proposal_digest_sha256"]})["request"]
        withdrawn = self.ok("POST", "/requests/%s/withdraw" % request["request_ref"], {
            "mission_id": request["mission_id"], "revision": request["revision"],
            "proposal_digest_sha256": request["proposal_digest_sha256"]})
        self.assertEqual(withdrawn["request"]["lifecycle"]["stage"], "withdrawn")
        listed = self.ok("GET", "/overview")["requests"]
        self.assertEqual([r["lifecycle"]["stage"] for r in listed], ["withdrawn"])


class LauncherTests(Bounded):
    """The real entry point, as a real process, with owned temporary state."""

    @classmethod
    def setUpClass(cls):
        cls._previous_term = signal.signal(signal.SIGTERM, _interrupt)

    @classmethod
    def tearDownClass(cls):
        signal.signal(signal.SIGTERM, cls._previous_term)

    def setUp(self):
        super(LauncherTests, self).setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="di-pnl-")
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.control = os.path.join(self.tmp.name, "control")
        os.makedirs(self.control)
        run_git("init", "-q", self.control)
        for name in ("AGENTS.md", "OPERATOR_PROTOCOL.md"):
            with open(os.path.join(self.control, name), "w") as handle:
                handle.write("# fixture\n")
        self.discovery = os.path.join(self.tmp.name, "disc", "panel.json")

    def launch(self, *extra):
        argv = [sys.executable, str(REPO_ROOT / "dimission_panel.py"),
                "--state-dir", self.state, "--control-repo", self.control,
                "--discovery-file", self.discovery] + list(extra)
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, cwd=self.tmp.name)
        _LAUNCHED.append(process)
        self.addCleanup(reap_process, process)
        return process

    def started(self, process):
        return startup_line(self, process)

    def test_L1_it_starts_publishes_a_private_discovery_file_and_stops_cleanly(self):
        process = self.launch()
        hello = self.started(process)
        self.assertEqual(hello["endpoint"], "mission_panel")
        self.assertTrue(hello["url"].startswith("http://127.0.0.1:"))
        document, problem = catalog_http.read_discovery_file(self.discovery)
        self.assertIsNone(problem)
        self.assertEqual(oct(os.stat(self.discovery).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(self.discovery)).st_mode & 0o777), "0o700")
        self.assertNotIn(document["token"], json.dumps(hello))
        connection = http.client.HTTPConnection("127.0.0.1", document["port"], timeout=10)
        connection.request("GET", "/healthz", headers={
            "Authorization": "Bearer " + document["token"]})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        process.terminate()
        self.assertEqual(process.wait(15), 0)
        self.assertFalse(os.path.exists(self.discovery))

    def test_L2_a_second_launch_is_refused_and_leaves_the_first_alone(self):
        first = self.launch()
        self.started(first)
        second = self.launch()
        self.assertEqual(second.wait(30), 2)
        self.assertTrue(os.path.exists(self.discovery))
        self.assertIsNone(first.poll())

    def test_L3_no_flag_can_name_a_principal_grant_approval_or_pick_a_provider(self):
        for flag in ("--principal", "--approve", "--model", "--provider",
                     "--token", "--host", "--allow-origin", "--shell"):
            with self.subTest(flag=flag):
                process = self.launch(flag, "x")
                self.assertEqual(process.wait(30), 2)
                self.assertFalse(os.path.exists(self.discovery))

    def test_L4_workspace_flags_come_as_a_pair(self):
        process = self.launch("--workspace-repository", self.control)
        self.assertEqual(process.wait(30), 2)

    def test_L5_with_a_workspace_the_repository_is_derived_from_its_origin(self):
        repo = os.path.join(self.tmp.name, "repo")
        root = os.path.join(self.tmp.name, "workspaces")
        os.makedirs(repo)
        os.makedirs(root)
        run_git("init", "-q", repo)
        run_git("remote", "add", "origin", "https://github.com/Example/Repo", cwd=repo)
        process = self.launch("--workspace-repository", repo, "--workspaces-root", root)
        hello = self.started(process)
        document, _ = catalog_http.read_discovery_file(self.discovery)
        connection = http.client.HTTPConnection("127.0.0.1", document["port"], timeout=10)
        connection.request("GET", "/overview", headers={
            "Authorization": "Bearer " + document["token"]})
        overview = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual(overview["capabilities"]["repository_url"],
                         "https://github.com/Example/Repo")
        self.assertTrue(overview["capabilities"]["run_available"])
        self.assertEqual(hello["run_available"], True)

    def test_L6_a_workspace_with_no_canonical_origin_refuses_to_start(self):
        repo = os.path.join(self.tmp.name, "repo")
        root = os.path.join(self.tmp.name, "workspaces")
        os.makedirs(repo)
        os.makedirs(root)
        run_git("init", "-q", repo)
        process = self.launch("--workspace-repository", repo, "--workspaces-root", root)
        self.assertEqual(process.wait(30), 2)
        self.assertFalse(os.path.exists(self.discovery))

    def test_L7_the_process_reads_no_state_it_was_not_given(self):
        # Owned state only: it creates its files under --state-dir, 0700.
        process = self.launch()
        self.started(process)
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), "0o700")

    # A stop that arrives while startup is under way, made deterministic: the real main(), with
    # the service's start sending this process its own SIGTERM (the handler only sets the event).
    STOP_DURING_STARTUP = (
        "import os, signal, sys\n"
        "sys.path.insert(0, %r)\n"
        "from mission_panel import service\n"
        "from mission_panel.http_adapter import main\n"
        "real_start = service.PanelService.start\n"
        "def start(self):\n"
        "    os.kill(os.getpid(), signal.SIGTERM)\n"
        "    return real_start(self)\n"
        "service.PanelService.start = start\n"
        "sys.exit(main(sys.argv[1:]))\n" % str(REPO_ROOT))

    def test_L8_a_stop_during_startup_is_a_failure_not_a_start(self):
        # Only a stop that was already set BEFORE readiness is a failed start; a stop after
        # readiness is the ordinary clean shutdown (exit 0), which L1 asserts.
        argv = [sys.executable, "-c", self.STOP_DURING_STARTUP, "--state-dir", self.state,
                "--control-repo", self.control, "--discovery-file", self.discovery]
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, cwd=self.tmp.name)
        _LAUNCHED.append(process)
        self.addCleanup(reap_process, process)
        code = process.wait(30)                 # bounded: the cleanup is bounded
        self.assertNotEqual(code, http_adapter.EXIT_OK)
        self.assertEqual(code, http_adapter.EXIT_INTERRUPTED)
        self.assertEqual(process.stdout.read(), b"", "readiness was advertised")
        said = process.stderr.read()
        self.assertIn(b"stopped during startup", said)
        self.assertLess(len(said), 300)
        self.assertFalse(os.path.exists(self.discovery), "the cleanup did not run")


class StartupHelperTests(Bounded):
    """The harness's own helper, on the two cases that matter."""

    def spawn(self, code):
        cwd = tempfile.TemporaryDirectory(prefix="di-pnl-")
        self.addCleanup(cwd.cleanup)             # runs after the reap below (LIFO)
        process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd.name)
        _LAUNCHED.append(process)
        self.addCleanup(reap_process, process)
        return process

    def test_S1_a_healthy_launcher_that_never_closes_stderr_returns_at_once(self):
        # The exact shape that stalled the harness: a line on stdout, stderr
        # held open for a long life. It must return, not wait for EOF.
        process = self.spawn(
            "import sys, time\nsys.stderr.write('held open\\n')\nsys.stderr.flush()\n"
            "print('{\"ok\": 1}', flush=True)\ntime.sleep(60)\n")
        started = time.monotonic()
        self.assertEqual(startup_line(self, process), {"ok": 1})
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNone(process.poll(), "the helper must not kill a healthy launcher")

    def test_S2_a_launcher_that_fails_surfaces_its_stderr_in_the_failure(self):
        process = self.spawn(
            "import sys\nsys.stderr.write('refused: the discovery file exists\\n')\n"
            "sys.exit(2)\n")
        with self.assertRaises(AssertionError) as caught:
            startup_line(self, process)
        message = str(caught.exception)
        self.assertIn("refused: the discovery file exists", message)
        self.assertIn("exit 2", message)
        self.assertIn("had already exited on its own", message)
        self.assertNotIn("SIGTERM", message)        # nothing here was stopped by this test

    def test_S3_a_launcher_that_hangs_without_a_line_is_bounded_and_reaped(self):
        process = self.spawn("import time\ntime.sleep(60)\n")
        started = time.monotonic()
        with self.assertRaises(AssertionError) as caught:
            startup_line(self, process, wait=1)
        self.assertLess(time.monotonic() - started, 30)
        self.assertIsNotNone(process.poll(), "a hung launcher must be reaped")
        message = str(caught.exception)
        self.assertIn("within 1s", message)
        self.assertIn("this test sent it SIGTERM", message)

    def test_S4_a_slow_launcher_stopped_by_this_test_is_not_reported_as_a_clean_exit(self):
        # The shape that read as success in CI: a launcher still starting when the bound ends,
        # whose SIGTERM handler lets it finish and exit 0. That exit is this test's doing.
        process = self.spawn(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: None)\n"
            "time.sleep(2)\n"
            "print('{\"ok\": 1}', flush=True)\n"
            "sys.exit(0)\n")
        with self.assertRaises(AssertionError) as caught:
            startup_line(self, process, wait=1)
        message = str(caught.exception)
        self.assertEqual(process.returncode, 0, "the exit really is a clean 0 ...")
        self.assertIn("exit 0", message)
        self.assertIn("this test sent it SIGTERM", message)      # ... and the report says whose
        self.assertIn("says nothing about whether it would have started", message)


class OrdinaryInterruptionTests(Bounded):
    """A test process that is INTERRUPTED (SIGINT) or TERMINATED (SIGTERM)
    while it owns a launched panel does not leave that panel running. The
    test process is a real child interpreter running the real fixture; this
    test signals it and then looks for the exact launcher PID it printed."""

    # A job started with ``&`` in a non-interactive shell inherits SIGINT as
    # IGNORED, and Python then leaves it ignored. The harness states the
    # ordinary disposition itself, so this test means the same thing whether it
    # runs in a terminal or as a background job.
    HARNESS = (
        "import signal, sys, time, unittest\n"
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        "sys.path[:0] = [%r, %r]\n"
        "import test_mission_panel_http as m\n"
        "class Blocking(m.LauncherTests):\n"
        "    def test_block_while_owning_a_launcher(self):\n"
        "        process = self.launch()\n"
        "        self.started(process)\n"
        "        print(process.pid, flush=True)\n"
        "        time.sleep(120)\n"
        "unittest.main(argv=['harness', 'Blocking'], module='__main__')\n"
        % (str(REPO_ROOT), str(REPO_ROOT / "tests")))

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def interrupted_by(self, number):
        cwd = tempfile.TemporaryDirectory(prefix="di-pnl-")
        self.addCleanup(cwd.cleanup)             # runs after the reap below (LIFO)
        harness = subprocess.Popen(
            [sys.executable, "-c", self.HARNESS], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=cwd.name,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(reap_process, harness)
        ready, _, _ = select.select([harness.stdout], [], [], 60)
        self.assertTrue(ready, "the harness never launched its panel")
        launcher_pid = int(harness.stdout.readline())
        self.assertTrue(self.alive(launcher_pid))
        harness.send_signal(number)
        harness.wait(30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.alive(launcher_pid):
            time.sleep(0.1)
        self.assertFalse(self.alive(launcher_pid),
                         "the launched panel outlived its interrupted test")

    def test_I1_an_interrupted_test_does_not_leave_its_panel_running(self):
        self.interrupted_by(signal.SIGINT)

    def test_I2_a_terminated_test_does_not_leave_its_panel_running(self):
        self.interrupted_by(signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
