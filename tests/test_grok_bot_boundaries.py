"""Fail-closed boundaries of the Grok Bot transport: attacker-style tests for

- the ``request`` tool's Outer Operator turn runs under the restrictive
  role-turn posture, pinned in argv and verified before any spawn, and NO
  ``operator_session_id`` is ever continued (refused by name before any
  provider invocation);
- the HTTP MCP endpoint fails CLOSED: it cannot be served without a
  bearer token, and every absent, malformed, wrong-scheme, empty, duplicated
  or wrong-value ``Authorization`` is refused 401 before the body is read;
- the delivery tools refuse any repository outside the configured,
  approved repository/workspace identity BEFORE pr_delivery or any Git
  process is invoked.

Hermetic BY CONSTRUCTION, not by a PATH shim:

- ``NoProcess`` replaces every process entry point (``subprocess`` and the
  ``os`` spawn/exec/fork primitives) with a recorder that RAISES, for every
  test, and asserts in cleanup that none was reached. No git, codex or gh
  process can start from this module, even under a mutant.
- The Codex runner is an injected recorder; the repository check that would
  run ``git rev-parse`` is replaced by a pure resolver where it is reached.
- pr_delivery's ``build_machine``, ``present_dots_cmd`` and
  ``attest_dots_cmd`` are replaced by recorders, so no delivery machine,
  store or transport is built from a real path.
- Every file this module writes is inside a per-test
  ``tempfile.TemporaryDirectory``. Repositories and worktrees are SHAPES on
  disk (a ``.git`` directory, ``gitdir:`` pointer files), never ``git init``.
- The only socket is the endpoint under test, on 127.0.0.1 and an ephemeral
  port, with a client that carries its own timeout.

This module imports only the standard library and product modules (no
other test module), so nothing outside this file runs at import.

Termination rule (CONTRIBUTING.md): a SIGALRM watchdog bounds every test,
independently of the code path under test; every client connection carries
its own timeout; the server thread is shut down and joined (bounded) in
cleanup while the watchdog is still armed.
"""

import ast
import http.client
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from codex_gateway import contract as gateway_contract  # noqa: E402
from codex_gateway import gateway as gateway_module  # noqa: E402
from codex_gateway import repository as repository_module  # noqa: E402
from codex_gateway import role_turn  # noqa: E402
from operator_session import (  # noqa: E402
    FunctionOperatorSession, RestrictedCodexOperatorSession,
)
from pr_delivery import boundary as delivery_boundary  # noqa: E402
from pr_delivery import cli as delivery_cli  # noqa: E402
from workflow_authority.digest import json_digest  # noqa: E402

from grok_bot import adapter as adapter_module  # noqa: E402
from grok_bot import cli as cli_module  # noqa: E402
from grok_bot import delivery as delivery_module  # noqa: E402
from grok_bot import index as index_module  # noqa: E402
from grok_bot import server as server_module  # noqa: E402

WATCHDOG_SECONDS = 60
CLIENT_TIMEOUT_SECONDS = 10
# A client that waits this long for a reply it should get at once fails the
# test instead of passing it: the server's own read timeout is 30 seconds.
BEFORE_BODY_TIMEOUT_SECONDS = 5
NOW = 1_800_000_000
SYNTHETIC_TOKEN = "synthetic-boundary-token-" + "0123456789abcdef" * 2
OPERATOR_SESSION_ID = "019a7c1e-0000-7000-8000-00000000c0de"


class NoProcess(object):
    """Every way this process could start another one RAISES while a test
    runs, and the test's cleanup asserts none was reached."""

    SUBPROCESS_ENTRIES = ("Popen", "run", "call", "check_call", "check_output",
                          "getoutput", "getstatusoutput")
    OS_PRIMITIVES = ("system", "popen", "fork", "forkpty", "posix_spawn",
                     "posix_spawnp", "execl", "execle", "execlp", "execlpe",
                     "execv", "execve", "execvp", "execvpe", "spawnl",
                     "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                     "spawnvp", "spawnvpe")

    def __init__(self, case):
        self.calls = []
        targets = [(subprocess, name) for name in self.SUBPROCESS_ENTRIES]
        targets += [(os, name) for name in self.OS_PRIMITIVES if hasattr(os, name)]
        for owner, name in targets:
            patcher = mock.patch.object(owner, name, self.refuser(
                "%s.%s" % (owner.__name__, name)))
            patcher.start()
            case.addCleanup(patcher.stop)
        case.addCleanup(lambda: case.assertEqual(
            self.calls, [], "a process entry point was reached"))

    def refuser(self, label):
        def refused(*args, **kwargs):
            self.calls.append(label)
            raise AssertionError("process entry point reached: %s" % label)
        return refused


class Bounded(unittest.TestCase):
    """The SIGALRM watchdog, then the process containment, for every test."""

    def setUp(self):
        def expired(signum, frame):
            raise TimeoutError("watchdog: test exceeded %d s" % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        self.no_process = NoProcess(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)

    def refused(self, problem, result):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["problem"], problem, result)
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["transport"], "grok_bot")
        return result


class UnreachedSurface(object):
    """The local request surface, where a test proves it is never reached."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def reached(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("the surface was reached: %s" % name)
        return reached


class Codex(object):
    """SYNTHETIC Codex: the injected runner. Records the exact argv, stdin
    and cwd of every would-be spawn and answers with synthetic JSONL; it
    starts nothing."""

    def __init__(self, message="Which repository do you mean?", returncode=0):
        self.calls = []
        self.message = message
        self.returncode = returncode

    def __call__(self, argv, input_bytes, cwd):
        self.calls.append({"argv": list(argv), "input": input_bytes, "cwd": cwd})
        stdout = "\n".join([
            json.dumps({"session_id": OPERATOR_SESSION_ID}),
            json.dumps({"type": "agent_message", "message": self.message}),
        ]).encode("utf-8")
        return subprocess.CompletedProcess(argv, self.returncode, stdout=stdout,
                                           stderr=b"")


def restrictive_argv(repository_realpath):
    """The posture this module expects, stated independently of the builder
    so a change to the builder fails here."""
    return ["codex", "exec", "--json", "-C", repository_realpath,
            "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules",
            "--strict-config", "-c", "approval_policy=never", "-"]


def pure_repository_check(path):
    """``validate_repository`` without its ``git rev-parse``: resolution only."""
    return repository_module.resolve_repository_path(path), None


# ====================================================================
# Restrictive Operator posture, and no session is ever continued
# ====================================================================


class OperatorSessionIdTests(Bounded):
    """An attacker holding the bearer token calls ``request`` with an
    ``operator_session_id``: a foreign Codex session (the user's own
    terminal session, say), or one this transport itself reported. Every
    one is refused by name before any provider call, before the index
    records anything and before the surface is reached."""

    def setUp(self):
        super(OperatorSessionIdTests, self).setUp()
        self.state = os.path.join(self.base, "state")
        os.mkdir(self.state, 0o700)
        self.repository = os.path.join(self.base, "operator-repo")
        os.mkdir(self.repository)
        self.submitted = []
        self.built = []
        self.surface = UnreachedSurface()
        self.adapter = adapter_module.GrokBotAdapter(
            self.surface, FunctionOperatorSession(self.build, self.submit),
            self.repository, index_module.RequestIndex(self.state), lambda: NOW)

    def build(self, text, repository, session_id=None, source="terminal"):
        self.built.append(session_id)
        return gateway_module.build_request(
            text, repository, session_id=session_id, source=source,
            request_id_factory=lambda: "req-%d" % len(self.built))

    def submit(self, request):
        self.submitted.append(request)
        return gateway_contract.GatewayResult(
            contract_version=1, request_id=request.request_id,
            session_id=OPERATOR_SESSION_ID, status="completed",
            message="Which repository do you mean?", error=None,
            unrecognized_event_lines=0)

    def assert_no_provider_call(self):
        self.assertEqual(self.built, [])
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.surface.calls, [])
        self.assertEqual(index_module.RequestIndex(self.state).load()["requests"],
                         {})

    def test_a_foreign_session_id_is_refused_by_name_before_any_provider_call(self):
        for session_id in (OPERATOR_SESSION_ID, "codex-session-1", "a",
                           "--last", "-", "../../etc", " ", "x" * 128):
            with self.subTest(session_id=session_id):
                result = self.adapter.request(
                    text="Return a proposal.", conversation_ref="c" * 40,
                    operator_session_id=session_id)
                self.refused(adapter_module.PROBLEM_OPERATOR_SESSION_REFUSED,
                             result)
                self.assertIn("fresh", result["reason"])
        self.assert_no_provider_call()

    def test_a_session_this_transport_reported_is_still_never_continued(self):
        """The rule is NONE, pinned: even the very session id this
        transport's own reply carried cannot be continued. The restrictive
        posture is fresh-only (its verifier refuses a ``resume``), so there
        is no session it could continue."""
        first = self.adapter.request(text="Fix the flaky probe.",
                                     conversation_ref="c" * 40)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["status"], "operator_reply")
        self.assertEqual(first["operator_session_id"], OPERATOR_SESSION_ID)
        self.assertEqual(self.built, [None])
        result = self.adapter.request(
            text="The one in Example/Repo.", conversation_ref="c" * 40,
            operator_session_id=first["operator_session_id"])
        self.refused(adapter_module.PROBLEM_OPERATOR_SESSION_REFUSED, result)
        self.assertEqual(self.built, [None])
        self.assertEqual(len(self.submitted), 1)

    def test_non_string_or_oversized_ids_keep_their_bad_request_refusal(self):
        for session_id in (123, True, ["s"], {"s": 1}, 1.5,
                           "x" * (adapter_module.MAX_REF_CHARS + 1)):
            with self.subTest(session_id=repr(session_id)[:20]):
                self.refused(adapter_module.PROBLEM_BAD_REQUEST,
                             self.adapter.request(
                                 text="Return a proposal.",
                                 operator_session_id=session_id))
        self.assert_no_provider_call()

    def test_the_cli_builds_the_restricted_session_and_refuses_the_id(self):
        """``grokbot.py call request`` constructs the RESTRICTED session, and
        an id over the command line is refused before it is used."""
        constructed = []

        def restricted():
            session = RestrictedCodexOperatorSession(runner=Codex())
            constructed.append(session)
            return session
        out = io.StringIO()
        with mock.patch.object(cli_module, "RestrictedCodexOperatorSession",
                               restricted), \
                mock.patch.object(cli_module.request_cli, "build_surface",
                                  lambda *args, **kwargs: self.surface):
            code = cli_module.main(
                ["--state-dir", self.state, "--repository", self.repository,
                 "call", "request"],
                stdin=io.StringIO(json.dumps({
                    "text": "Return a proposal.",
                    "operator_session_id": OPERATOR_SESSION_ID})),
                stdout=out)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertEqual(json.loads(out.getvalue())["problem"],
                         adapter_module.PROBLEM_OPERATOR_SESSION_REFUSED)
        self.assertEqual(len(constructed), 1)
        self.assertEqual(constructed[0]._runner.calls, [])

    def test_no_entry_point_constructs_the_ambient_session(self):
        """Static pin: ``grok_bot`` names only the restricted session, for
        ``call request`` and ``serve`` alike, never ``CodexOperatorSession``."""
        names = set()
        for path in sorted((REPO_ROOT / "grok_bot").glob("*.py")) + [
            REPO_ROOT / "grokbot.py"
        ]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Name):
                    names.add((path.name, node.id))
                elif isinstance(node, ast.alias):
                    names.add((path.name, node.asname or node.name))
        self.assertIn(("cli.py", "RestrictedCodexOperatorSession"), names)
        self.assertEqual(sorted(n for n in names if n[1] == "CodexOperatorSession"),
                         [])


class RestrictedOperatorTurnTests(Bounded):
    """The production path for ``request``: ``RestrictedCodexOperatorSession``
    -> ``gateway.submit_restricted`` -> ``role_turn.run_operator_turn``."""

    def setUp(self):
        super(RestrictedOperatorTurnTests, self).setUp()
        self.repository = os.path.join(self.base, "operator-repo")
        os.mkdir(self.repository)
        self.checked = []

        def check(path):
            self.checked.append(path)
            return pure_repository_check(path)
        patcher = mock.patch.object(repository_module, "validate_repository", check)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_argv_is_exactly_the_restrictive_posture_and_verified(self):
        codex = Codex()
        session = RestrictedCodexOperatorSession(runner=codex)
        result = session.execute(session.prepare(
            "Return a proposal.", self.repository, source="grok_bot"))
        self.assertEqual(result.status, gateway_contract.STATUS_COMPLETED, result)
        self.assertEqual(result.message, "Which repository do you mean?")
        self.assertEqual(len(codex.calls), 1)
        call = codex.calls[0]
        self.assertEqual(call["argv"], restrictive_argv(self.repository))
        self.assertEqual(role_turn.verify_restrictive_posture(
            call["argv"], self.repository), (True, None))
        self.assertNotIn("resume", call["argv"])
        self.assertEqual(call["cwd"], self.repository)
        self.assertEqual(call["input"], b"Return a proposal.")

    def test_a_session_id_is_refused_by_the_restricted_gateway_too(self):
        """Defense in depth behind the adapter: the restricted gateway path
        has no resume, so a session id is an invalid request before the
        repository is checked or any argv exists."""
        codex = Codex()
        session = RestrictedCodexOperatorSession(runner=codex)
        result = session.execute(session.prepare(
            "Return a proposal.", self.repository,
            session_id=OPERATOR_SESSION_ID, source="grok_bot"))
        self.assertEqual(result.status, gateway_contract.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code,
                         gateway_contract.ERROR_SESSION_NOT_PERMITTED)
        self.assertEqual(codex.calls, [])
        self.assertEqual(self.checked, [])

    def test_a_posture_the_verifier_rejects_is_refused_before_any_spawn(self):
        real = restrictive_argv(self.repository)
        weakened = {
            "workspace-write sandbox": [
                "workspace-write" if t == "read-only" else t for t in real],
            "ambient user config": [t for t in real if t != "--ignore-user-config"],
            "ambient rules": [t for t in real if t != "--ignore-rules"],
            "no approval override": [t for t in real if t not in (
                "-c", "approval_policy=never")],
            "a resume": real[:2] + ["resume"] + real[2:],
            "another directory": [self.base if t == self.repository else t
                                  for t in real],
            "a bypass flag": real[:-1] + [
                "--dangerously-bypass-approvals-and-sandbox", "-"],
        }
        for label, argv in sorted(weakened.items()):
            with self.subTest(posture=label):
                codex = Codex()
                with mock.patch.object(role_turn, "build_role_turn_argv",
                                       lambda realpath, argv=argv: list(argv)):
                    status, session_id, message, error, _ = (
                        role_turn.run_operator_turn("text", self.repository,
                                                    runner=codex))
                self.assertEqual(status, gateway_contract.STATUS_INVALID_REQUEST)
                self.assertEqual(error.code,
                                 role_turn.REASON_POSTURE_NOT_ESTABLISHED)
                self.assertIsNone(message)
                self.assertEqual(codex.calls, [])

    def test_a_rejected_posture_is_a_failure_never_a_retry_or_a_fallback(self):
        codex = Codex(returncode=2)
        status, _, message, error, _ = role_turn.run_operator_turn(
            "text", self.repository, runner=codex)
        self.assertEqual(status, gateway_contract.STATUS_CODEX_FAILED)
        self.assertEqual(error.code, gateway_contract.ERROR_CODEX_EXIT_NONZERO)
        self.assertIsNone(message)
        self.assertEqual(len(codex.calls), 1)

    def test_the_operator_turn_has_no_session_parameter(self):
        """A resume is not merely refused here: it is unrepresentable."""
        import inspect
        self.assertEqual(
            list(inspect.signature(role_turn.run_operator_turn).parameters),
            ["text", "repository_realpath", "runner"])


# ====================================================================
# The HTTP MCP endpoint fails closed
# ====================================================================


class RecordingAdapter(object):
    """Stands in for the adapter behind the endpoint: records every call."""

    def __init__(self):
        self.calls = []

    def call(self, tool, arguments):
        self.calls.append(tool)
        return {"ok": True, "status": "recorded", "delivery_authority": "none",
                "evidence_status": "synthetic", "transport": "grok_bot"}


class ServerFailsClosedTests(Bounded):

    def setUp(self):
        super(ServerFailsClosedTests, self).setUp()
        self.adapter = RecordingAdapter()
        self.log = io.StringIO()

    def serve(self, token=SYNTHETIC_TOKEN):
        server = server_module.LoopbackMcpServer(self.adapter, log=self.log,
                                                 bearer_token=token)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def headers_only(self, server, method, authorizations):
        """Send headers that announce a body which never arrives. A server
        that reads the body before refusing makes this client time out."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=BEFORE_BODY_TIMEOUT_SECONDS)
        try:
            connection.putrequest(method, server_module.MCP_PATH)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Accept", "application/json")
            connection.putheader("Content-Length", "1000")
            for value in authorizations:
                connection.putheader("Authorization", value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response, response.read()
        finally:
            connection.close()

    def post(self, server, message, authorizations=()):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=CLIENT_TIMEOUT_SECONDS)
        try:
            connection.putrequest("POST", server_module.MCP_PATH)
            body = json.dumps(message).encode("utf-8")
            for name, value in (("Content-Type", "application/json"),
                                ("Accept", "application/json"),
                                ("Content-Length", str(len(body)))):
                connection.putheader(name, value)
            for value in authorizations:
                connection.putheader("Authorization", value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_serving_without_a_token_is_refused_before_any_bind(self):
        binds = []
        with mock.patch.object(server_module.LoopbackMcpServer, "server_bind",
                               lambda server: binds.append(server)):
            with self.assertRaises(server_module.BearerTokenError):
                server_module.LoopbackMcpServer(self.adapter, bearer_token=None)
            with self.assertRaises(server_module.BearerTokenError):
                server_module.LoopbackMcpServer(self.adapter)
        self.assertEqual(binds, [])

    def test_the_cli_refuses_to_serve_without_a_token_file(self):
        constructed = []
        state = os.path.join(self.base, "state")
        out = io.StringIO()
        with mock.patch.object(server_module, "LoopbackMcpServer",
                               lambda *a, **k: constructed.append(a)), \
                mock.patch.object(cli_module.request_cli, "build_surface",
                                  lambda *a, **k: constructed.append("surface")):
            code = cli_module.main(
                ["--state-dir", state, "--repository", self.base,
                 "--control-repo", os.path.join(self.base, "control"), "serve"],
                stdout=out)
        self.assertEqual(code, cli_module.EXIT_USAGE)
        result = json.loads(out.getvalue())
        self.assertEqual(result["problem"], adapter_module.PROBLEM_BAD_REQUEST)
        self.assertIn("--auth-token-file", result["reason"])
        self.assertEqual(constructed, [])
        self.assertFalse(os.path.exists(state))

    def test_a_server_whose_token_is_cleared_refuses_every_request(self):
        """Defense in depth: the per-request check itself fails closed, so
        no state of the server object admits a request without the token."""
        server = self.serve()
        for cleared in (None, b""):
            server.bearer_token = cleared
            for authorizations in ((), ("Bearer " + SYNTHETIC_TOKEN,),
                                   ("Bearer ",)):
                for method in ("GET", "POST"):
                    with self.subTest(cleared=cleared, method=method,
                                      authorizations=authorizations):
                        status, _, _ = self.headers_only(server, method,
                                                         authorizations)
                        self.assertEqual(status, 401)
        self.assertEqual(self.adapter.calls, [])

    def test_every_bad_authorization_is_401_before_the_body_is_read(self):
        server = self.serve()
        token = SYNTHETIC_TOKEN
        cases = {
            "absent": (),
            "empty": ("",),
            "scheme only": ("Bearer",),
            "scheme and space": ("Bearer ",),
            "whitespace value": ("Bearer    ",),
            "no scheme": (token,),
            "wrong scheme": ("Basic " + token,),
            "another scheme": ("Token " + token,),
            "scheme glued": ("Bearer" + token,),
            "wrong value": ("Bearer wrong",),
            "longer": ("Bearer " + token + "x",),
            "prefix": ("Bearer " + token[:-1],),
            "case changed": ("Bearer " + token.upper(),),
            "extra word": ("Bearer " + token + " extra",),
            "non-ascii": ("Bearer " + token[:-1] + "\xe9",),
            "duplicated, wrong first": ("Bearer wrong", "Bearer " + token),
            "duplicated, right first": ("Bearer " + token, "Bearer wrong"),
            "duplicated, both right": ("Bearer " + token, "Bearer " + token),
        }
        for label, authorizations in sorted(cases.items()):
            for method in ("POST", "GET", "DELETE"):
                with self.subTest(case=label, method=method):
                    status, response, body = self.headers_only(
                        server, method, authorizations)
                    self.assertEqual(status, 401, body)
                    self.assertIn("Bearer",
                                  response.getheader("WWW-Authenticate"))
                    self.assertNotIn(token.encode("ascii"), body)
        self.assertEqual(self.adapter.calls, [])

    def test_the_exact_token_is_admitted(self):
        server = self.serve()
        for authorization in ("Bearer " + SYNTHETIC_TOKEN,
                              "bearer " + SYNTHETIC_TOKEN):
            with self.subTest(authorization=authorization[:7]):
                status, body = self.post(server, {
                    "jsonrpc": "2.0", "id": 1, "method": "ping"}, (authorization,))
                self.assertEqual(status, 200, body)

    def test_no_token_is_ever_logged_or_returned(self):
        server = self.serve()
        for authorizations in ((), ("Bearer wrong",),
                               ("Bearer " + SYNTHETIC_TOKEN,)):
            status, body = self.post(server, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "status",
                           "arguments": {"request_ref": "lr-" + "0" * 32}}},
                authorizations)
            self.assertNotIn(SYNTHETIC_TOKEN.encode("ascii"), body)
        self.assertEqual(self.adapter.calls, ["status"])
        self.assertTrue(self.log.getvalue())
        self.assertNotIn(SYNTHETIC_TOKEN, self.log.getvalue())
        self.assertEqual(server.access_control, "bearer_token")


# ====================================================================
# The delivery tools refuse repositories outside the approved identity
# ====================================================================

EXPIRES_AT = NOW + 3600


class DeliveryScopeTests(Bounded):
    """Shapes on disk, never ``git init``:

    - ``approved``: the configured repository (a ``.git`` directory);
    - ``workspaces/m1``: one of its worktrees, by reciprocal ``gitdir``
      pointers, directly under the configured workspaces root;
    - ``foreign``: another repository; ``plain``: no repository at all.
    """

    def setUp(self):
        super(DeliveryScopeTests, self).setUp()
        self.ceremony = []
        for name in ("build_machine", "present_dots_cmd", "attest_dots_cmd"):
            patcher = mock.patch.object(delivery_cli, name, self.recorder(name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state = os.path.join(self.base, "state")
        os.mkdir(self.state, 0o700)
        self.approved = self.repository("approved")
        self.root = os.path.join(self.base, "workspaces")
        os.mkdir(self.root)
        self.worktree = self.add_worktree(self.approved, "m1")
        self.foreign = self.repository("foreign")
        self.plain = os.path.join(self.base, "plain")
        os.mkdir(self.plain)
        self.evidence = os.path.join(self.base, "herd-evidence.json")
        self.log = os.path.join(self.base, "verification.log")
        self.adapter = self.make_adapter(self.approved, self.root)

    def recorder(self, name):
        def reached(*args, **kwargs):
            self.ceremony.append((name, args))
            if name == "build_machine":
                raise AssertionError("a delivery machine was built")
            raise delivery_cli.CeremonyError("synthetic: the ceremony was reached")
        return reached

    def repository(self, name):
        path = os.path.join(self.base, name)
        os.makedirs(os.path.join(path, ".git", "worktrees"))
        return path

    def add_worktree(self, repository, name, root=None, commondir="../.."):
        """A linked worktree as Git lays one out: the checkout's ``.git``
        file names an administrative directory, whose ``gitdir`` names the
        checkout's ``.git`` back and whose ``commondir`` (relative, as Git
        writes it) names the repository's common directory. ``commondir``
        None writes none."""
        checkout = os.path.join(root or self.root, name)
        os.mkdir(checkout)
        admin = os.path.join(repository, ".git", "worktrees", name)
        os.mkdir(admin)
        with open(os.path.join(checkout, ".git"), "w") as handle:
            handle.write("gitdir: %s\n" % admin)
        with open(os.path.join(admin, "gitdir"), "w") as handle:
            handle.write(os.path.join(checkout, ".git") + "\n")
        if commondir is not None:
            with open(os.path.join(admin, "commondir"), "w") as handle:
                handle.write(commondir + "\n")
        return checkout

    def make_adapter(self, repository, root):
        return adapter_module.GrokBotAdapter(
            UnreachedSurface(), None, self.base,
            index_module.RequestIndex(self.state), lambda: NOW,
            delivery_repository=repository, delivery_workspaces_root=root)

    def arguments(self, repo):
        return {"repo": repo, "workflow_id": "wf-delivery",
                "herd_evidence": self.evidence, "verification_log": self.log,
                "verification_command": "python3 tests/test_x.py",
                "verification_exit_status": 0, "title": "Delivery"}

    def assert_never_reached(self):
        self.assertEqual(self.ceremony, [])
        self.assertEqual(index_module.RequestIndex(self.state).load().get(
            "delivery_presentations", {}), {})

    def assert_refused(self, repo, adapter=None):
        result = (adapter or self.adapter).present_delivery(**self.arguments(repo))
        self.refused(delivery_module.PROBLEM_REPOSITORY_NOT_APPROVED, result)
        self.assert_never_reached()
        return result

    def test_a_foreign_repository_is_refused_before_git(self):
        self.assert_refused(self.foreign)

    def test_a_dotdot_escape_is_refused_before_git(self):
        for repo in (os.path.join(self.approved, "..", "foreign"),
                     os.path.join(self.root, "..", "foreign"),
                     os.path.join(self.root, "m1", "..", "..", "foreign"),
                     # Even a .. path that lands on the approved repository
                     # is not its name: only the realpath is.
                     os.path.join(self.approved, "..", "approved"),
                     self.approved + os.sep,
                     os.path.join(self.approved, ".")):
            with self.subTest(repo=repo):
                self.assert_refused(repo)

    def test_a_symlink_whose_realpath_leaves_the_approved_identity_is_refused(self):
        inside_root = os.path.join(self.root, "m2")
        os.symlink(self.foreign, inside_root)
        inside_repo = os.path.join(self.approved, "linked")
        os.symlink(self.foreign, inside_repo)
        # A worktree whose .git pointer is itself a symlink to a pointer.
        pointer = os.path.join(self.base, "pointer")
        os.rename(os.path.join(self.worktree, ".git"), pointer)
        os.symlink(pointer, os.path.join(self.worktree, ".git"))
        for repo in (inside_root, inside_repo, self.worktree):
            with self.subTest(repo=repo):
                self.assert_refused(repo)

    def test_a_symlinked_alias_of_the_approved_repository_is_not_its_name(self):
        alias = os.path.join(self.base, "alias")
        os.symlink(self.approved, alias)
        result = self.assert_refused(alias)
        self.assertIn("realpath", result["reason"])
        self.assertNotIn(self.approved, result["reason"])

    def test_a_non_repository_is_refused_before_git(self):
        unrelated = os.path.join(self.root, "plain")
        os.mkdir(unrelated)
        for repo in (self.plain, unrelated, self.root,
                     os.path.join(self.base, "absent")):
            with self.subTest(repo=repo):
                self.assert_refused(repo)
        # A configured "repository" that is not one approves nothing.
        self.assert_refused(self.plain, self.make_adapter(self.plain, self.root))

    def test_a_nested_repository_that_is_not_the_approved_one_is_refused(self):
        nested_in_repo = self.repository(os.path.join("approved", "vendor"))
        nested_in_worktree = os.path.join(self.worktree, "sub")
        os.makedirs(os.path.join(nested_in_worktree, ".git"))
        standalone = os.path.join(self.root, "standalone")
        os.makedirs(os.path.join(standalone, ".git"))
        # Another repository's worktree, placed under the approved root.
        stray = self.add_worktree(self.foreign, "stray")
        # A worktree pointer naming the approved repository's administrative
        # directory, but that directory names a different checkout back.
        forged = os.path.join(self.root, "forged")
        os.mkdir(forged)
        with open(os.path.join(forged, ".git"), "w") as handle:
            handle.write("gitdir: %s\n" % os.path.join(
                self.approved, ".git", "worktrees", "m1"))
        # A worktree of the approved repository, but deeper than the root's
        # own children.
        deep_parent = os.path.join(self.root, "deep")
        os.mkdir(deep_parent)
        deep = self.add_worktree(self.approved, "deep-child", root=deep_parent)
        for repo in (nested_in_repo, nested_in_worktree, standalone, stray,
                     forged, deep):
            with self.subTest(repo=repo):
                self.assert_refused(repo)

    def test_a_worktree_whose_commondir_names_a_foreign_repository_is_refused(self):
        """The review's shape: an administrative directory under the
        APPROVED repository's ``.git/worktrees``, a reciprocal ``gitdir``,
        and a ``commondir`` naming a FOREIGN repository, from which Git would
        take objects, refs and config. Building it needs write access to the
        approved repository's Git directory and the workspaces root (a
        same-user process, never a bearer-token-only caller)."""
        foreign_common = os.path.join(self.foreign, ".git")
        cases = {
            "absolute foreign commondir": self.add_worktree(
                self.approved, "evil-absolute", commondir=foreign_common),
            "relative foreign commondir": self.add_worktree(
                self.approved, "evil-relative", commondir=os.path.relpath(
                    foreign_common, os.path.join(self.approved, ".git",
                                                 "worktrees", "evil-relative"))),
            "no commondir": self.add_worktree(self.approved, "evil-absent",
                                              commondir=None),
            "commondir naming nothing": self.add_worktree(
                self.approved, "evil-missing",
                commondir=os.path.join(self.base, "absent")),
        }
        linked = self.add_worktree(self.approved, "evil-linked", commondir=None)
        os.symlink(os.path.join(self.base, "approved", ".git"), os.path.join(
            self.approved, ".git", "worktrees", "evil-linked", "commondir"))
        cases["commondir as a symlink"] = linked
        for label, repo in sorted(cases.items()):
            with self.subTest(shape=label):
                self.assert_refused(repo)
        # The same shape with Git's own ``../..`` is accepted (the control).
        self.assertEqual(delivery_module.approved_repository(
            self.add_worktree(self.approved, "honest"), self.approved,
            self.root), os.path.join(self.root, "honest"))

    def test_delivery_without_a_configured_repository_is_refused(self):
        adapter = self.make_adapter(None, None)
        for repo in (self.approved, self.worktree):
            with self.subTest(repo=repo):
                result = adapter.present_delivery(**self.arguments(repo))
                self.refused(delivery_module.PROBLEM_NOT_CONFIGURED, result)
        self.assert_never_reached()

    def test_the_approved_repository_and_its_worktree_reach_the_ceremony(self):
        for repo in (self.approved, self.worktree):
            with self.subTest(repo=repo):
                del self.ceremony[:]
                result = self.adapter.present_delivery(**self.arguments(repo))
                self.refused(delivery_module.PROBLEM_REFUSED, result)
                self.assertEqual([name for name, _ in self.ceremony],
                                 ["present_dots_cmd"])
                self.assertEqual(self.ceremony[0][1][0].repo, repo)

    def test_a_configured_symlink_is_resolved_once_and_named_by_its_realpath(self):
        configured = os.path.join(self.base, "configured-link")
        os.symlink(self.approved, configured)
        adapter = self.make_adapter(configured, self.root)
        self.assert_refused(configured, adapter)
        result = adapter.present_delivery(**self.arguments(self.approved))
        self.refused(delivery_module.PROBLEM_REFUSED, result)

    # -- approve_delivery and delivery_status, under the same boundary ----

    def seed_receipt(self, repository_realpath):
        proposal = {"binding": {"repository": {"realpath": repository_realpath}},
                    "expires_at": EXPIRES_AT, "presented_at": NOW}
        digest = json_digest(proposal)
        index = index_module.RequestIndex(self.state)
        with index.serialized():
            index.record_delivery_presentation(digest, proposal, "0" * 64)
        return digest

    def approve(self, digest, adapter=None, approval_code=None):
        return (adapter or self.adapter).approve_delivery(
            proposal_digest_sha256=digest, expires_at=EXPIRES_AT,
            relayed_reply="approved", reply_to="m-1", relay_ref="r-1",
            approval_code=approval_code)

    def test_approve_delivery_refuses_a_receipt_outside_the_identity(self):
        for repository in (self.foreign, self.plain,
                           os.path.join(self.approved, "..", "foreign")):
            with self.subTest(repository=repository):
                self.refused(delivery_module.PROBLEM_REPOSITORY_NOT_APPROVED,
                             self.approve(self.seed_receipt(repository)))
        self.assertEqual(self.ceremony, [])
        self.refused(delivery_module.PROBLEM_NOT_CONFIGURED, self.approve(
            self.seed_receipt(self.approved), self.make_adapter(None, None)))
        self.assertEqual(self.ceremony, [])

    def test_approve_delivery_refuses_a_receipt_without_a_repository(self):
        proposal = {"binding": {}, "expires_at": EXPIRES_AT, "presented_at": NOW}
        digest = json_digest(proposal)
        index = index_module.RequestIndex(self.state)
        with index.serialized():
            index.record_delivery_presentation(digest, proposal, "0" * 64)
        self.refused(delivery_module.PROBLEM_REPOSITORY_NOT_APPROVED,
                     self.approve(digest))
        self.assertEqual(self.ceremony, [])

    def test_approve_delivery_reaches_attest_for_the_approved_identity(self):
        """The approved identity passes the scope gate: unarmed it stops at
        the NEXT gate (local arming), and armed with a
        known code (a commitment stored directly through the one writer,
        since this seeded receipt has no renderable display) it reaches the
        ceremony."""
        from grok_bot import arming
        from grok_bot import authorize
        for repository in (self.approved, self.worktree):
            with self.subTest(repository=repository):
                del self.ceremony[:]
                digest = self.seed_receipt(repository)
                self.refused(arming.PROBLEM_NOT_ARMED, self.approve(digest))
                self.assertEqual(self.ceremony, [])
                code = "c" * arming.CODE_HEX_CHARS
                index = index_module.RequestIndex(self.state)
                with index.serialized():
                    authorize._store(self.state, arming.delivery_key(digest),
                                     arming.KIND_DELIVERY, arming.commitment(
                                         arming.delivery_preimage(
                                             digest, EXPIRES_AT, "0" * 64,
                                             repository, code)),
                                     EXPIRES_AT, NOW, index)
                self.refused(delivery_module.PROBLEM_REFUSED,
                             self.approve(digest, approval_code=code))
                self.assertEqual([name for name, _ in self.ceremony],
                                 ["attest_dots_cmd"])

    def status_with(self, record, adapter=None):
        projected = []

        class Machine(object):
            def load(machine, delivery_id):
                return record

            def clock(machine):
                return NOW
        with mock.patch.object(delivery_cli, "build_machine",
                               lambda store_dir=None: Machine()), \
                mock.patch.object(delivery_boundary, "project_status",
                                  lambda r, now: projected.append(r) or {
                                      "phase": "AUTHORIZED"}):
            result = (adapter or self.adapter).delivery_status(delivery_id="prd-1")
        return result, projected

    def test_delivery_status_is_under_the_same_boundary(self):
        for repository in (self.foreign, self.plain):
            with self.subTest(repository=repository):
                result, projected = self.status_with(
                    {"repository": {"realpath": repository}})
                self.refused(delivery_module.PROBLEM_REPOSITORY_NOT_APPROVED,
                             result)
                self.assertEqual(projected, [])
        result, projected = self.status_with({"repository": {}})
        self.refused(delivery_module.PROBLEM_REPOSITORY_NOT_APPROVED, result)
        result = self.make_adapter(None, None).delivery_status(
            delivery_id="prd-1")
        self.refused(delivery_module.PROBLEM_NOT_CONFIGURED, result)
        self.assertEqual(self.ceremony, [])
        result, projected = self.status_with(
            {"repository": {"realpath": self.worktree}})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["delivery"], {"phase": "AUTHORIZED"})
        self.assertEqual(len(projected), 1)

    def test_the_scope_check_reads_the_filesystem_only(self):
        """The identity check alone, with every process entry point
        refusing (``NoProcess``): it decides without Git."""
        for repo, expected in ((self.approved, True), (self.worktree, True),
                               (self.foreign, False), (self.plain, False)):
            with self.subTest(repo=repo):
                try:
                    named = delivery_module.approved_repository(
                        repo, self.approved, self.root)
                except adapter_module.surface_module.LocalRequestRefusal:
                    named = None
                self.assertEqual(named == repo, expected)


# ====================================================================
# What the posture does NOT confine is stated, never called containment
# ====================================================================


class PostureDisclosureTests(Bounded):

    @staticmethod
    def flat(text):
        return " ".join(text.split())

    def test_the_read_residual_is_stated_where_the_posture_is_described(self):
        import grok_bot
        for label, text in (
            ("grokbot --help", cli_module.DESCRIPTION),
            ("grok_bot/cli.py", cli_module.__doc__),
            ("codex_gateway.gateway.submit_restricted",
             gateway_module.submit_restricted.__doc__),
        ):
            with self.subTest(where=label):
                self.assertIn("confines writes, not reads", self.flat(text))
        package = self.flat(grok_bot.__doc__)
        for phrase in ("it does not confine READS",
                       "Read-only is not secret isolation",
                       "nothing here scopes readable paths",
                       "no exfiltration has been demonstrated here, and"
                       " nothing here prevents it",
                       "mitigations, not boundaries"):
            self.assertIn(phrase, package)
        # The comment block, as prose: each line's leading "#" removed.
        source = self.flat(" ".join(
            line.strip().lstrip("#") for line in
            (REPO_ROOT / "codex_gateway" / "role_turn.py")
            .read_text(encoding="utf-8").splitlines()))
        self.assertIn("It does not confine READS", source)
        self.assertIn("It is not secret isolation", source)
        # Neither direction is overclaimed: no exploit, no containment.
        for text in (package, self.flat(cli_module.DESCRIPTION)):
            for claim in ("prevents exfiltration", "contains exfiltration",
                          "demonstrated exfiltration", "exfiltration exploit"):
                self.assertNotIn(claim, text)

    def test_the_tool_schema_admits_no_session_id(self):
        from grok_bot import mcp
        schema = mcp.TOOL_DEFINITIONS["request"][1]["operator_session_id"]
        self.assertEqual(schema["type"], "null")
        self.assertIn("fresh", schema["description"])


# ====================================================================
# Integration pins: runnable, AST only
# ====================================================================

# The bound-naming convention of tests/test_workflow_authority.py's
# BoundConstantPinTests.
OWNED_BOUND_CONSTANTS = {
    # Approval arming, and the delivery identity's one worktree pointer read.
    "grok_bot/arming.py": {"CODE_HEX_CHARS": 32, "MAX_CODE_FAILURES": 5,
                           "MAX_COMMITMENTS": 1024, "NONCE_BYTES": 16},
    "grok_bot/delivery.py": {"MAX_POINTER_BYTES": 4096},
    # The on-demand tunnel.
    "tunnel_control/common.py": {"MAX_SOCKET_PATH_BYTES": 100,
                                 "MAX_LOG_SCAN_BYTES": 1048576,
                                 "MAX_MESSAGE_BYTES": 65536},
    "tunnel_control/controller.py": {
        "STARTUP_TIMEOUT_SECONDS": 30.0, "STARTUP_SETTLE_SECONDS": 1.0,
        "STOP_GRACE_SECONDS": 10.0, "KILL_GRACE_SECONDS": 5.0,
        "RETRY_SECONDS": 1.0, "MAX_UNCONFIRMED_SECONDS": 300.0,
        "CLIENT_IO_SECONDS": 5.0, "POLL_SECONDS": 0.05,
        "REQUEST_READ_SECONDS": 2.0, "REPLY_SECONDS": 1.0},
    "tunnel_control/tunnel.py": {"STATUS_ASK_SECONDS": 5.0,
                                 "STOP_REPLY_SECONDS": 60.0,
                                 "ON_REPLY_SECONDS": 90.0},
    "tunnel_control/cli.py": {},
    "tunnel_control/__init__.py": {},
    "ditunnel.py": {},
}


# Bounding constants named OUTSIDE the convention, pinned here by name. The
# registry derives only convention names, so a name listed in its PINNED table
# but never derived would trip its own stale-entry guard: these are pinned in
# this runnable test instead.
EXPLICIT_BOUND_CONSTANTS = {
    # The commitment nonce's entropy: the 128 bits the guessing argument
    # rests on (the approval code is this nonce, in hex).
    "grok_bot/arming.py": ("NONCE_BYTES",),
}


def _matches_bound_convention(name):
    return not name.startswith("_") and (
        name.startswith(("MAX_", "CANONICAL_"))
        or name.endswith(("_SECONDS", "_CHARS", "_CHUNKS")))


def _derived_bound_constants(relpath):
    """Every convention-matching module constant in ``relpath`` (the
    registry's own rule), plus the names ``EXPLICIT_BOUND_CONSTANTS`` lists
    for it, with literal values, over the WHOLE module AST."""
    explicit = EXPLICIT_BOUND_CONSTANTS.get(relpath, ())
    tree = ast.parse((REPO_ROOT / relpath).read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            value = node.value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    targets.append(target)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    targets.extend(element for element in target.elts
                                   if isinstance(element, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                           ast.Name):
            targets, value = [node.target], node.value
        for target in targets:
            if _matches_bound_convention(target.id) or target.id in explicit:
                found[target.id] = ast.literal_eval(value)
    return found


def _registry_pins():
    """The PINNED table of BoundConstantPinTests, read from its SOURCE (the
    module is parsed, never imported or executed)."""
    tree = ast.parse((REPO_ROOT / "tests" / "test_workflow_authority.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BoundConstantPinTests":
            for item in node.body:
                if isinstance(item, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "PINNED"
                    for target in item.targets
                ):
                    return ast.literal_eval(item.value)
    raise AssertionError("BoundConstantPinTests.PINNED not found")


class IntegrationPinTests(unittest.TestCase):

    def test_arming_delivery_and_tunnel_bound_constants_are_pinned(self):
        """Each bound constant that approval arming, the delivery identity
        check and the on-demand tunnel introduced has exactly its pinned
        value in the module that owns it, and the registry that
        tests/test_workflow_authority.py's BoundConstantPinTests enforces
        pins the same values."""
        registry = _registry_pins()
        for relpath, expected in sorted(OWNED_BOUND_CONSTANTS.items()):
            with self.subTest(module=relpath):
                self.assertEqual(_derived_bound_constants(relpath), expected)
                convention = dict((name, value) for name, value
                                  in expected.items()
                                  if _matches_bound_convention(name))
                if convention:
                    self.assertEqual(registry.get(relpath), convention)
        # The code IS the nonce in hex: 16 bytes, 32 hex characters, 128 bits.
        from grok_bot import arming
        self.assertEqual(arming.NONCE_BYTES, 16)
        self.assertEqual(arming.CODE_HEX_CHARS, 2 * arming.NONCE_BYTES)

    def test_the_pr_delivery_import_boundary(self):
        """``grok_bot/delivery.py`` stays the only module outside
        ``pr_delivery/`` and ``herdr/guards.py`` that imports pr_delivery:
        the rule tests/test_static.py pins, re-stated here (product files
        exclude tests, roles, scripts, caches and dot-directories; herdr is
        added back, as there)."""
        excluded = {"tests", "herdr", "roles", "scripts", "__pycache__"}
        files = sorted(
            path for path in REPO_ROOT.rglob("*.py")
            if not any(part in excluded or part.startswith(".")
                       for part in path.relative_to(REPO_ROOT).parts))
        files += sorted((REPO_ROOT / "herdr").glob("*.py"))
        importers = {}
        for path in files:
            relpath = path.relative_to(REPO_ROOT).as_posix()
            if relpath.startswith("pr_delivery/"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name.split(".")[0] == "pr_delivery" for name in names):
                    importers[relpath] = importers.get(relpath, 0) + 1
        self.assertEqual(importers, {"herdr/guards.py": 1,
                                     "grok_bot/delivery.py": 1})

    def test_the_tunnel_tool_imports_no_pr_delivery_and_is_on_no_mcp_tool(self):
        from grok_bot import mcp
        for relpath in OWNED_BOUND_CONSTANTS:
            if not relpath.startswith(("tunnel_control/", "ditunnel.py")):
                continue
            source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
            self.assertNotIn("pr_delivery", source, relpath)
        surface = json.dumps(mcp.tool_definitions()) + mcp.INSTRUCTIONS
        for name in ("ditunnel", "tunnel_control", "cloudflared"):
            self.assertNotIn(name, surface)


# ====================================================================
# Mutation self-check: each guard, removed IN MEMORY, turns the named
# tests red, and restored they pass (nothing on disk changes)
# ====================================================================

ORIGINAL_BEARER_OK = server_module.McpRequestHandler._bearer_ok


def _fail_open_without_token(handler):
    """The pre-task behaviour: no configured token admits every request."""
    return handler.server.bearer_token is None or ORIGINAL_BEARER_OK(handler)


def _first_header_only(handler):
    """The pre-task parse: only the first Authorization header is read."""
    import hmac
    expected = handler.server.bearer_token
    scheme, _, supplied = (handler.headers.get("Authorization") or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.strip().encode("latin-1", "replace"), expected)


def _two_way_worktree(checkout, common_dir):
    """The pre-review proof: gitdir both ways, commondir never read."""
    dot_git = os.path.join(checkout, ".git")
    admin = delivery_module._pointer_target(checkout, dot_git,
                                            delivery_module.GITDIR_PREFIX)
    return admin is not None and os.path.dirname(admin) == os.path.join(
        common_dir, "worktrees") and delivery_module._pointer_target(
        admin, os.path.join(admin, "gitdir")) == dot_git


MUTANTS = (
    ("a worktree's commondir is never read",
     delivery_module, "_is_worktree_of", _two_way_worktree,
     ("DeliveryScopeTests.test_a_worktree_whose_commondir_names_a_foreign"
      "_repository_is_refused",)),
    ("the delivery identity check is skipped",
     delivery_module, "approved_repository",
     lambda repo, configured, root: repo,
     ("DeliveryScopeTests.test_a_foreign_repository_is_refused_before_git",
      "DeliveryScopeTests.test_a_dotdot_escape_is_refused_before_git",
      "DeliveryScopeTests.test_a_nested_repository_that_is_not_the_approved"
      "_one_is_refused")),
    ("approve and status skip the identity check",
     delivery_module, "bound_repository",
     lambda document, configured, root: None,
     ("DeliveryScopeTests.test_approve_delivery_refuses_a_receipt_outside_the"
      "_identity",
      "DeliveryScopeTests.test_delivery_status_is_under_the_same_boundary")),
    ("the posture verifier accepts any argv",
     role_turn, "verify_restrictive_posture",
     lambda argv, realpath: (True, None),
     ("RestrictedOperatorTurnTests.test_a_posture_the_verifier_rejects_is"
      "_refused_before_any_spawn",)),
    ("an unconfigured token admits every request",
     server_module.McpRequestHandler, "_bearer_ok", _fail_open_without_token,
     ("ServerFailsClosedTests.test_a_server_whose_token_is_cleared_refuses"
      "_every_request",)),
    ("only the first Authorization header is read",
     server_module.McpRequestHandler, "_bearer_ok", _first_header_only,
     ("ServerFailsClosedTests.test_every_bad_authorization_is_401_before_the"
      "_body_is_read",)),
)


class MutationSelfCheckTests(unittest.TestCase):
    """Each inner test arms (and in cleanup disarms) its own watchdog, so
    this test re-arms its own, against one fixed deadline, after every
    inner run."""

    MUTATION_WATCHDOG_SECONDS = 240

    def setUp(self):
        previous = signal.signal(signal.SIGALRM, self.expired)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        self.deadline = time.monotonic() + self.MUTATION_WATCHDOG_SECONDS
        self.rearm()

    def expired(self, signum, frame):
        raise TimeoutError("watchdog: mutation self-check exceeded %d s"
                           % self.MUTATION_WATCHDOG_SECONDS)

    def rearm(self):
        remaining = int(self.deadline - time.monotonic())
        if remaining < 1:
            self.expired(None, None)
        signal.signal(signal.SIGALRM, self.expired)
        signal.alarm(remaining)

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        self.rearm()
        return result

    def test_every_boundary_mutant_is_caught_and_the_original_passes(self):
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


if __name__ == "__main__":
    unittest.main()
