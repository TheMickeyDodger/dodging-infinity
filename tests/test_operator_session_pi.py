"""Coverage for the Pi-backed operator session (``operator_session.pi``).

Every behavioral test runs against a SUBSTITUTED process runner: no test
here starts a real ``pi`` process. The only real child processes are the
Python interpreter itself, used to prove ``run_process`` closes stdin and
terminates/reaps a child that exceeds the bound.

Fixture provenance: the failure stream below is the shape RECORDED from
the installed pi 0.85.1 during the single live smoke (provider/model
names and identifiers replaced by placeholders; the event sequence,
keys, roles, and stop reasons are verbatim). The success stream reuses
that recorded skeleton with the assistant message carrying
``stopReason: "stop"`` and one text content item, per the ``pi-ai``
``AssistantMessage`` type; a live success was not observed in the smoke.
"""

import argparse
import ast
import builtins
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_gateway import contract as codex_contract
from operator_session import pi as pi_module
from operator_session.pi import PiOperatorSession
from operator_session.session import OperatorSession, PreparedTurn

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "operator_session" / "pi.py"
FIXED_ID = "a" * 32
SESSION_ID = "01a071a4-592f-7392-a7e0-edfbce58a929"

HEADER = {
    "type": "session", "version": 3, "id": SESSION_ID,
    "timestamp": "2026-09-05T12:56:31.282Z", "cwd": "/repo",
}
USER_MESSAGE = {
    "role": "user", "timestamp": 1,
    "content": [{"type": "text", "text": "Reply with exactly the single word OK."}],
}


def assistant_message(stop_reason, content, error_message=None):
    message = {
        "role": "assistant", "api": "example-api", "provider": "example-provider",
        "model": "example-model", "usage": {}, "stopReason": stop_reason,
        "content": content, "timestamp": 2,
    }
    if error_message is not None:
        message["errorMessage"] = error_message
    return message


def stream(assistant):
    events = [
        HEADER,
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": USER_MESSAGE},
        {"type": "message_end", "message": USER_MESSAGE},
        {"type": "message_start", "message": assistant},
        {"type": "message_end", "message": assistant},
        {"type": "turn_end", "message": assistant, "toolResults": []},
        {"type": "agent_end", "messages": [USER_MESSAGE, assistant], "willRetry": False},
        {"type": "agent_settled"},
    ]
    return "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")


SUCCESS_STDOUT = stream(assistant_message("stop", [{"type": "text", "text": "OK"}]))
FAILURE_STDOUT = stream(
    assistant_message("error", [], error_message="400 provider refused the request")
)


class RunnerSpy(object):
    """Substitutes ``run_process``: records calls, returns or raises."""

    def __init__(self, returncode=0, stdout=SUCCESS_STDOUT, stderr=b"", raises=None):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises

    def __call__(self, argv, cwd, timeout_seconds):
        self.calls.append((list(argv), cwd, timeout_seconds))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            list(argv), self.returncode, self.stdout, self.stderr
        )


class PiSessionTestCase(unittest.TestCase):
    def setUp(self):
        self.runner = RunnerSpy()
        patcher = patch.object(pi_module, "run_process", self.runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        factory = patch.object(
            pi_module, "default_request_id_factory", lambda: FIXED_ID
        )
        factory.start()
        self.addCleanup(factory.stop)
        self.session = PiOperatorSession()

    def run_turn(self, text="hello", session_id=None, repository="/repo"):
        prepared = self.session.prepare(text, repository, session_id=session_id)
        return self.session.execute(prepared)


class ConstructionIsInertTests(unittest.TestCase):
    """1. Constructing the session touches nothing outside the process."""

    def test_construction_starts_no_process_opens_no_file_reads_no_env(self):
        with patch.object(subprocess, "Popen") as popen, patch.object(
            subprocess, "run"
        ) as run, patch.object(builtins, "open") as opener, patch.dict(
            os.environ, {}, clear=True
        ):
            session = PiOperatorSession()
            custom = PiOperatorSession(binary="other-pi", timeout_seconds=1.5)
        self.assertEqual(popen.call_count, 0)
        self.assertEqual(run.call_count, 0)
        self.assertEqual(opener.call_count, 0)
        self.assertIsInstance(session, OperatorSession)
        self.assertEqual(session._binary, pi_module.PI_BINARY)
        self.assertEqual(session._timeout_seconds, pi_module.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual((custom._binary, custom._timeout_seconds), ("other-pi", 1.5))

    def test_module_never_touches_the_environment_or_imports_os(self):
        tree = ast.parse(MODULE_PATH.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("environ", "getenv", "putenv"))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name.split(".")[0], "os")

    def test_the_session_does_not_override_prepare_or_execute(self):
        self.assertIs(PiOperatorSession.prepare, OperatorSession.prepare)
        self.assertIs(PiOperatorSession.execute, OperatorSession.execute)
        self.assertNotIn("prepare", PiOperatorSession.__dict__)
        self.assertNotIn("execute", PiOperatorSession.__dict__)


class PrepareTests(PiSessionTestCase):
    """2. prepare builds a usable turn and calls no provider."""

    def test_prepare_makes_no_provider_call_and_yields_a_turn(self):
        prepared = self.session.prepare(
            "hello", "/repo", session_id="s-1", source="telegram"
        )
        self.assertEqual(self.runner.calls, [])
        self.assertIsInstance(prepared, PreparedTurn)
        self.assertIsInstance(prepared.request, pi_module.PiRequest)
        self.assertEqual(prepared.request_id, FIXED_ID)
        self.assertEqual(prepared.request.request_id, FIXED_ID)
        self.assertEqual(prepared.request.text, "hello")
        self.assertEqual(prepared.request.repository, "/repo")
        self.assertEqual(prepared.request.session_id, "s-1")
        self.assertEqual(prepared.request.source, "telegram")
        self.assertEqual(
            prepared.request.contract_version, pi_module.PI_CONTRACT_VERSION
        )

    def test_request_and_result_types_are_frozen_and_duck_compatible(self):
        prepared = self.session.prepare("hello", "/repo")
        for field in ("request_id", "session_id", "text", "repository",
                      "source", "contract_version"):
            self.assertTrue(hasattr(prepared.request, field), field)
        with self.assertRaises(Exception):
            prepared.request.text = "other"
        result = self.session.execute(prepared)
        for field in ("request_id", "status", "session_id", "message", "error",
                      "unrecognized_event_lines", "contract_version"):
            self.assertTrue(hasattr(result, field), field)
        error = pi_module.make_error("x", "y")
        for field in ("code", "detail", "detail_truncated"):
            self.assertTrue(hasattr(error, field), field)

    def test_result_fields_cover_every_read_the_transport_makes(self):
        """Round-3 R6: DERIVED, not hand-written. The attribute reads on
        the operator-session result are collected from the transport's
        source: the names bound from ``self._session.execute(...)`` or
        ``self._dispatch_gateway_turn(...)`` in ``adapter.py``, plus the
        parameter of any method those names are passed to (one level:
        ``_report_gateway_failure``). A new read in the transport that
        ``PiResult`` lacks fails here."""
        source = (REPO_ROOT / "telegram_operator" / "adapter.py").read_text()
        tree = ast.parse(source)
        methods = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }

        def called_name(call):
            func = call.func
            return getattr(func, "attr", getattr(func, "id", None))

        def reads_in(fn, names):
            found = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Attribute):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id in names:
                    found.add(node.attr)
                elif (isinstance(node.value, ast.Attribute)
                      and isinstance(node.value.value, ast.Name)
                      and node.value.value.id in names):
                    found.add(node.value.attr + "." + node.attr)
            return found

        reads = set()
        forwarded = []
        for fn in methods.values():
            bound = set()
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Call)
                        and called_name(node.value)
                        in ("execute", "_dispatch_gateway_turn")):
                    bound.update(
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    )
            if not bound:
                continue
            reads |= reads_in(fn, bound)
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    for index, arg in enumerate(node.args):
                        if isinstance(arg, ast.Name) and arg.id in bound:
                            forwarded.append((called_name(node), index))
        for name, index in forwarded:
            target = methods.get(name)
            if target is None:
                continue
            params = [a.arg for a in target.args.args if a.arg != "self"]
            if index < len(params):
                reads |= reads_in(target, {params[index]})
        # Anti-vacuity: the derivation found the known load-bearing reads.
        self.assertTrue({"status", "message", "session_id", "request_id",
                         "error.code"} <= reads, reads)
        result_fields = set(pi_module.PiResult.__dataclass_fields__)
        error_fields = set(pi_module.PiError.__dataclass_fields__)
        for read in sorted(reads):
            if "." in read:
                parent, child = read.split(".", 1)
                self.assertEqual(parent, "error", read)
                self.assertIn(child, error_fields, read)
            else:
                self.assertIn(read, result_fields, read)

    def test_error_detail_bound_is_applied_honestly(self):
        long = "z" * (pi_module.ERROR_DETAIL_MAX_CHARS + 5)
        error = pi_module.make_error("code", long)
        self.assertEqual(len(error.detail), pi_module.ERROR_DETAIL_MAX_CHARS)
        self.assertTrue(error.detail_truncated)
        short = pi_module.make_error("code", "short")
        self.assertEqual((short.detail, short.detail_truncated), ("short", False))


class ExecuteTests(PiSessionTestCase):
    """3. execute makes exactly one process invocation with the original request."""

    def test_execute_invokes_the_runner_exactly_once(self):
        prepared = self.session.prepare("hello", "/repo")
        result = self.session.execute(prepared)
        self.assertEqual(len(self.runner.calls), 1)
        argv, cwd, timeout = self.runner.calls[0]
        self.assertEqual(argv[-1], prepared.request.text)
        self.assertEqual(cwd, prepared.request.repository)
        self.assertEqual(timeout, pi_module.DEFAULT_TIMEOUT_SECONDS)
        self.assertIsInstance(result, pi_module.PiResult)
        self.assertEqual(result.request_id, FIXED_ID)

    def test_execute_hands_the_original_request_to_submit_and_returns_its_result(self):
        prepared = self.session.prepare("hello", "/repo")
        sentinel = object()
        seen = []

        def submit(request):
            seen.append(request)
            return sentinel

        with patch.object(self.session, "_submit", submit):
            returned = self.session.execute(prepared)
        self.assertIs(returned, sentinel)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], prepared.request)

    def test_second_execute_is_a_second_invocation(self):
        prepared = self.session.prepare("hello", "/repo")
        self.session.execute(prepared)
        self.session.execute(prepared)
        self.assertEqual(len(self.runner.calls), 2)


class ArgvShapeTests(PiSessionTestCase):
    """4 and 5. The exact argv, the two session forms, cwd, no credential."""

    DISABLED = [
        "--mode", "json", "--no-tools", "--no-extensions", "--no-skills",
        "--no-prompt-templates", "--no-context-files", "--no-approve",
    ]

    def test_ephemeral_argv_is_exact(self):
        self.run_turn(text="- do it", repository="/work/repo")
        argv, cwd, _ = self.runner.calls[0]
        self.assertEqual(
            argv, ["pi"] + self.DISABLED + ["--no-session", "--", "- do it"]
        )
        self.assertEqual(cwd, "/work/repo")

    def test_session_carrying_argv_is_exact(self):
        self.run_turn(text="next", session_id=SESSION_ID)
        argv, _, _ = self.runner.calls[0]
        self.assertEqual(
            argv,
            ["pi"] + self.DISABLED
            + ["--session-id", SESSION_ID, "--", "next"],
        )
        self.assertNotIn("--no-session", argv)

    def test_double_dash_precedes_the_intent_and_intent_is_last(self):
        self.run_turn(text="--tools looks like a flag but is text")
        argv, _, _ = self.runner.calls[0]
        self.assertEqual(argv[-2], "--")
        self.assertEqual(argv[-1], "--tools looks like a flag but is text")
        self.assertEqual(argv.index("--"), len(argv) - 2)

    def test_no_provider_model_or_credential_appears_in_argv(self):
        self.run_turn(session_id=SESSION_ID)
        argv, _, _ = self.runner.calls[0]
        for element in argv:
            lowered = element.lower()
            for banned in ("--provider", "--model", "--api-key", "token",
                           "secret", "password", "--system-prompt"):
                self.assertNotIn(banned, lowered, argv)
        self.assertNotIn("--print", argv)
        self.assertNotIn("-p", argv)

    def test_binary_constant_is_the_first_element(self):
        session = PiOperatorSession(binary="/opt/homebrew/bin/pi")
        prepared = session.prepare("hello", "/repo")
        session.execute(prepared)
        self.assertEqual(self.runner.calls[0][0][0], "/opt/homebrew/bin/pi")

    def test_configured_timeout_reaches_the_runner(self):
        session = PiOperatorSession(timeout_seconds=42.0)
        session.execute(session.prepare("hello", "/repo"))
        self.assertEqual(self.runner.calls[0][2], 42.0)


class GuardTests(PiSessionTestCase):
    """6. Refusals produce invalid_request with no process started."""

    def assert_refused(self, result, code):
        self.assertEqual(result.status, pi_module.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, code)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.unrecognized_event_lines, 0)
        self.assertEqual(self.runner.calls, [])

    def test_banned_flag_in_a_constructed_argv_is_refused_before_any_process(self):
        # Force the constructed argv to contain a banned token: the
        # guard, not the runner, must be what stops it.
        with patch.object(
            pi_module, "BANNED_FLAGS", pi_module.BANNED_FLAGS + ("--no-tools",)
        ):
            result = self.run_turn()
        self.assert_refused(result, pi_module.ERROR_BANNED_FLAG)

    def test_guard_refuses_each_banned_flag_with_or_without_value(self):
        for flag in pi_module.BANNED_FLAGS:
            for form in (flag, flag + "=x"):
                with self.assertRaises(pi_module.BannedFlagError):
                    pi_module.assert_argv_allowed(["pi", form, "--", "t"])
        self.assertEqual(
            pi_module.assert_argv_allowed(["pi", "--mode", "json"]),
            ["pi", "--mode", "json"],
        )

    def test_guard_covers_tools_trust_extensions_skills_templates_credentials_prompt(self):
        for flag in ("--tools", "-t", "--approve", "-a", "--extension", "-e",
                     "--skill", "--prompt-template", "--api-key",
                     "--system-prompt", "--append-system-prompt",
                     "--session", "--fork", "--continue", "--resume"):
            self.assertIn(flag, pi_module.BANNED_FLAGS)

    def test_blank_session_id_is_refused(self):
        for blank in ("", "   "):
            self.assert_refused(
                self.run_turn(session_id=blank), pi_module.ERROR_EMPTY_SESSION_ID
            )

    def test_non_string_session_id_is_refused(self):
        self.assert_refused(
            self.run_turn(session_id=7), pi_module.ERROR_EMPTY_SESSION_ID
        )

    def test_flag_like_session_id_is_refused(self):
        for flagged in ("-", "--no-tools", "-x", "--session-id=other"):
            self.assert_refused(
                self.run_turn(session_id=flagged),
                pi_module.ERROR_FLAG_LIKE_SESSION_ID,
            )

    def test_at_leading_intent_is_refused_as_a_file_include_hazard(self):
        for intent in ("@/etc/hosts", "@secrets.txt read this", "@"):
            result = self.run_turn(text=intent)
            self.assert_refused(result, pi_module.ERROR_INTENT_FILE_INCLUDE)
            self.assertIn("file", result.error.detail)

    def test_intent_with_an_at_sign_elsewhere_is_allowed(self):
        result = self.run_turn(text="email me @ noon")
        self.assertEqual(result.status, pi_module.STATUS_COMPLETED)
        self.assertEqual(len(self.runner.calls), 1)

    def test_empty_intent_is_refused(self):
        for empty in ("", "  \n"):
            self.assert_refused(
                self.run_turn(text=empty), pi_module.ERROR_EMPTY_INTENT
            )

    def test_non_utf8_encodable_intent_is_refused_not_crashed(self):
        self.assert_refused(
            self.run_turn(text="bad \ud800 surrogate"),
            pi_module.ERROR_INTENT_NOT_UTF8,
        )


class TimeoutTests(PiSessionTestCase):
    """7. A timeout is a truthful failure; the child is terminated."""

    def test_timeout_maps_to_a_failure_naming_the_bound(self):
        # Round-3 R7: the spy raises the exception the REAL runner would
        # raise, built by the module from the bound the runner RECEIVED
        # — the test injects no wording, so the assertion below checks
        # the module's own message against the constructor's bound.
        class TimeoutRunner(RunnerSpy):
            def __call__(self, argv, cwd, timeout_seconds):
                self.calls.append((list(argv), cwd, timeout_seconds))
                raise pi_module.ProcessTimeout(timeout_seconds)

        runner = TimeoutRunner()
        with patch.object(pi_module, "run_process", runner):
            session = PiOperatorSession(timeout_seconds=7.25)
            result = session.execute(session.prepare("hello", "/repo"))
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_TIMEOUT)
        self.assertEqual(runner.calls[0][2], session._timeout_seconds)
        self.assertIn(str(session._timeout_seconds), result.error.detail)
        self.assertIn("terminated", result.error.detail)
        # A clean termination discloses no drain or reap problem.
        self.assertNotIn("could not be drained", result.error.detail)
        self.assertNotIn("could not be reaped", result.error.detail)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_undrained_timeout_discloses_the_grandchild_hazard(self):
        class TimeoutRunner(RunnerSpy):
            def __call__(self, argv, cwd, timeout_seconds):
                self.calls.append((list(argv), cwd, timeout_seconds))
                raise pi_module.ProcessTimeout(
                    timeout_seconds, reaped=True, drained=False
                )

        with patch.object(pi_module, "run_process", TimeoutRunner()):
            session = PiOperatorSession(timeout_seconds=3.0)
            result = session.execute(session.prepare("hello", "/repo"))
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_TIMEOUT)
        self.assertIn("could not be drained", result.error.detail)
        self.assertIn("grandchild", result.error.detail)
        self.assertIn(str(pi_module.TERMINATE_GRACE_SECONDS), result.error.detail)
        self.assertIsNone(result.message)



class RealRunnerTests(unittest.TestCase):
    """The real runner's pinned process posture, proven on a Python child."""

    def test_real_runner_terminates_and_reaps_a_child_that_exceeds_the_bound(self):
        # A Python child, never pi. The Popen object is captured so the
        # reap can be observed: returncode set and negative (signal).
        created = []
        real_popen = subprocess.Popen

        def capturing_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            created.append(process)
            return process

        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        with patch.object(subprocess, "Popen", capturing_popen):
            with self.assertRaises(pi_module.ProcessTimeout) as caught:
                pi_module.run_process(argv, os.getcwd(), 0.3)
        self.assertIn("0.3", str(caught.exception))
        self.assertEqual(len(created), 1)
        process = created[0]
        self.assertIsNotNone(process.returncode)
        self.assertLess(process.returncode, 0)
        self.assertIsNotNone(process.poll())

    def test_stdin_is_closed_and_streams_are_bytes(self):
        argv = [
            sys.executable, "-c",
            "import sys; data = sys.stdin.read();"
            " sys.stdout.write('stdin=%d' % len(data)); sys.stderr.write('e')",
        ]
        completed = pi_module.run_process(argv, os.getcwd(), 30)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"stdin=0")
        self.assertEqual(completed.stderr, b"e")

    def test_runner_uses_cwd_and_no_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = [sys.executable, "-c", "import os; print(os.getcwd())"]
            completed = pi_module.run_process(argv, tmp, 30)
        self.assertEqual(
            os.path.realpath(completed.stdout.decode().strip()),
            os.path.realpath(tmp),
        )
        source = MODULE_PATH.read_text()
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("popen(", source.replace("subprocess.Popen(", ""))

    def test_missing_binary_raises_oserror_naming_the_binary(self):
        # The discriminator _start_failure relies on: exec failure ->
        # filename is the executable. Pinned against the real
        # interpreter so a change there fails here, loudly.
        binary = "/nonexistent/definitely-not-pi-binary"
        with self.assertRaises(OSError) as caught:
            pi_module.run_process([binary], os.getcwd(), 5)
        self.assertEqual(caught.exception.filename, binary)

    def test_unusable_cwd_raises_oserror_naming_the_cwd(self):
        # chdir failure -> filename is the cwd, even with a runnable binary.
        cwd = os.path.join(tempfile.gettempdir(), "definitely-missing-repo-dir")
        self.assertFalse(os.path.exists(cwd))
        with self.assertRaises(OSError) as caught:
            pi_module.run_process([sys.executable, "-c", "pass"], cwd, 5)
        self.assertEqual(caught.exception.filename, cwd)

    def test_pipe_holding_grandchild_cannot_defeat_the_bound(self):
        # Round-3 R2. The child spawns a grandchild that inherits the
        # stdout/stderr pipes and outlives it, then sleeps. SIGTERM ends
        # the child (Python installs no SIGTERM handler), the pipes stay
        # open in the grandchild, and every drain must give up within
        # the (patched, short) grace instead of waiting for its EOF.
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = os.path.join(tmp, "grandchild.pid")
            child = (
                "import subprocess, sys, time\n"
                "g = subprocess.Popen([sys.executable, '-c',"
                " 'import time; time.sleep(20)'])\n"
                "open(%r, 'w').write(str(g.pid))\n"
                "time.sleep(30)\n" % pid_file
            )
            created = []
            real_popen = subprocess.Popen

            def capturing_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                created.append(process)
                return process

            import time
            started = time.monotonic()
            try:
                with patch.object(pi_module, "TERMINATE_GRACE_SECONDS", 0.5), \
                        patch.object(subprocess, "Popen", capturing_popen):
                    with self.assertRaises(pi_module.ProcessTimeout) as caught:
                        pi_module.run_process(
                            [sys.executable, "-c", child], os.getcwd(), 1.0
                        )
                elapsed = time.monotonic() - started
            finally:
                # Best-effort cleanup of the orphaned grandchild (it also
                # exits on its own after 20 s).
                try:
                    with open(pid_file) as handle:
                        os.kill(int(handle.read().strip()), 9)
                except (OSError, ValueError):
                    pass
        exc = caught.exception
        self.assertFalse(exc.drained)
        self.assertTrue(exc.reaped)
        self.assertIn("could not be drained", str(exc))
        self.assertIn("grandchild", str(exc))
        self.assertIn("1.0", str(exc))
        # Bounded: timeout + at most three grace periods, plus slack.
        self.assertLess(elapsed, 1.0 + 3 * 0.5 + 3.0)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].returncode)
        self.assertTrue(created[0].stdout.closed)
        self.assertTrue(created[0].stderr.closed)

    def test_timeout_message_is_the_single_source_of_wording(self):
        clean = pi_module.timeout_message(2.0, True, True)
        self.assertEqual(str(pi_module.ProcessTimeout(2.0)), clean)
        self.assertIn("2.0", clean)
        undrained = pi_module.timeout_message(2.0, True, False)
        self.assertIn("could not be drained", undrained)
        unreaped = pi_module.timeout_message(2.0, False, False)
        self.assertIn("could not be reaped", unreaped)
        self.assertIn("could not be drained", unreaped)

    def test_when_both_are_bad_the_cwd_is_named_because_chdir_runs_first(self):
        cwd = os.path.join(tempfile.gettempdir(), "definitely-missing-repo-dir")
        with self.assertRaises(OSError) as caught:
            pi_module.run_process(["/nonexistent/definitely-not-pi"], cwd, 5)
        self.assertEqual(caught.exception.filename, cwd)


class FailureMappingTests(PiSessionTestCase):
    """8. Each failure has its own honest status and the unrecognized count."""

    def test_missing_binary_maps_to_unavailable_naming_the_binary(self):
        self.runner.raises = FileNotFoundError(2, "No such file", "pi")
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_UNAVAILABLE)
        self.assertEqual(result.error.code, pi_module.ERROR_PI_NOT_FOUND)
        self.assertIn("pi binary", result.error.detail)
        self.assertIsNone(result.message)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_unusable_repository_maps_to_invalid_request_naming_the_repository(self):
        # Round-2 finding 3: the OSError from a failed chdir names the
        # cwd; it must not be reported as a missing binary.
        self.runner.raises = FileNotFoundError(2, "No such file", "/repo")
        result = self.run_turn(repository="/repo")
        self.assertEqual(result.status, pi_module.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, pi_module.ERROR_REPOSITORY_UNUSABLE)
        self.assertIn("repository /repo", result.error.detail)
        self.assertIn("not started", result.error.detail)
        self.assertNotIn("install pi", result.error.detail)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_unattributable_start_failure_asserts_neither_cause(self):
        for exc in (PermissionError(13, "Permission denied"),
                    FileNotFoundError(2, "No such file", "/somewhere/else")):
            self.runner.raises = exc
            result = self.run_turn(repository="/repo")
            self.assertEqual(result.status, pi_module.STATUS_PI_UNAVAILABLE)
            self.assertEqual(
                result.error.code, pi_module.ERROR_PROCESS_START_FAILED
            )
            self.assertIn("could not be attributed", result.error.detail)
            self.assertIn("pi binary", result.error.detail)
            self.assertIn("repository /repo", result.error.detail)
            self.assertIn(str(exc), result.error.detail)
            self.assertNotIn("install pi and ensure", result.error.detail)
            self.assertIsNone(result.message)

    def test_missing_binary_with_a_custom_binary_path_is_matched_on_argv0(self):
        self.runner.raises = FileNotFoundError(2, "No such file", "/opt/x/pi")
        session = PiOperatorSession(binary="/opt/x/pi")
        result = session.execute(session.prepare("hello", "/repo"))
        self.assertEqual(result.error.code, pi_module.ERROR_PI_NOT_FOUND)
        self.assertIn("/opt/x/pi binary", result.error.detail)

    def test_nonzero_exit_maps_to_failed_with_bounded_stderr(self):
        self.runner.returncode = 1
        self.runner.stdout = b""
        self.runner.stderr = b"Error: Session id must be non-empty\n"
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_PI_EXIT_NONZERO)
        self.assertIn("status 1", result.error.detail)
        self.assertIn("Session id must be non-empty", result.error.detail)
        self.assertFalse(result.error.detail_truncated)
        self.assertIsNone(result.message)

    def test_nonzero_exit_with_long_stderr_is_truncated_honestly(self):
        self.runner.returncode = 2
        self.runner.stdout = b""
        self.runner.stderr = b"x" * (pi_module.ERROR_DETAIL_MAX_CHARS * 2)
        result = self.run_turn()
        self.assertTrue(result.error.detail_truncated)
        self.assertEqual(len(result.error.detail), pi_module.ERROR_DETAIL_MAX_CHARS)

    def test_nonzero_exit_with_non_utf8_stderr_discloses_escaping(self):
        self.runner.returncode = 3
        self.runner.stdout = b""
        self.runner.stderr = b"bad \xff byte"
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertIn("shown escaped", result.error.detail)
        self.assertIn("\\xff", result.error.detail)

    def test_nonzero_exit_still_reports_parsed_session_and_unrecognized(self):
        self.runner.returncode = 1
        self.runner.stdout = json.dumps(HEADER).encode() + b"\nnot json\n"
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.session_id, SESSION_ID)
        self.assertEqual(result.unrecognized_event_lines, 1)

    def test_non_utf8_stdout_is_malformed_output_and_unparsed(self):
        self.runner.stdout = b'{"type":"session","id":"x"}\n\xff\xfe\n'
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.error.code, pi_module.ERROR_OUTPUT_NOT_UTF8)
        self.assertIn("not parsed", result.error.detail)
        self.assertIsNone(result.session_id)
        self.assertIsNone(result.message)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_unparseable_stream_is_malformed_with_every_line_counted(self):
        self.runner.stdout = b"garbage\n\n[1, 2]\n{\"type\": \"mystery\"}\n42\n"
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.error.code, pi_module.ERROR_UNRECOGNIZED_OUTPUT)
        self.assertEqual(result.unrecognized_event_lines, 4)
        self.assertIn("4 unrecognized line(s)", result.error.detail)
        self.assertIn(pi_module.COMPATIBILITY_SURFACE_NOTE, result.error.detail)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)

    def test_no_terminal_message_is_malformed_never_invented(self):
        events = [HEADER, {"type": "agent_start"}, {"type": "turn_start"},
                  {"type": "message_end", "message": USER_MESSAGE},
                  {"type": "agent_end", "messages": []}]
        self.runner.stdout = "".join(json.dumps(e) + "\n" for e in events).encode()
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.error.code, pi_module.ERROR_UNRECOGNIZED_OUTPUT)
        self.assertIsNone(result.message)
        # The header WAS recognized: its identity is reported, not inferred.
        self.assertEqual(result.session_id, SESSION_ID)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_assistant_message_without_text_is_not_a_message(self):
        self.runner.stdout = stream(
            assistant_message("stop", [{"type": "thinking", "thinking": "hmm"}])
        )
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
        self.assertIsNone(result.message)

    def test_failure_stop_reason_maps_to_failed_with_the_error_message(self):
        # The RECORDED shape: exit 0, assistant stopReason "error".
        self.runner.stdout = FAILURE_STDOUT
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_PI_FAILURE_EVENT)
        self.assertEqual(result.error.detail, "400 provider refused the request")
        self.assertEqual(result.session_id, SESSION_ID)
        self.assertIsNone(result.message)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_length_stop_is_an_incomplete_answer_not_a_completion(self):
        # Round-3 R1: a truncated answer must never be reported complete,
        # and its partial text is withheld.
        self.runner.stdout = stream(
            assistant_message("length", [{"type": "text", "text": "partial an"}])
        )
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_INCOMPLETE_ANSWER)
        self.assertIn("'length'", result.error.detail)
        self.assertIn("truncated", result.error.detail)
        self.assertIsNone(result.message)
        self.assertNotIn("partial an", result.error.detail)
        self.assertEqual(result.session_id, SESSION_ID)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_every_non_terminal_declared_stop_reason_fails_closed(self):
        for stop_reason in ("pending", "toolUse", "deferred"):
            self.runner.stdout = stream(
                assistant_message(stop_reason, [{"type": "text", "text": "x"}])
            )
            result = self.run_turn()
            self.assertEqual(result.status, pi_module.STATUS_PI_FAILED, stop_reason)
            self.assertEqual(
                result.error.code, pi_module.ERROR_INCOMPLETE_ANSWER, stop_reason
            )
            self.assertIn(repr(stop_reason), result.error.detail)
            self.assertIsNone(result.message)

    def test_undeclared_or_missing_stop_reason_is_malformed_output(self):
        undeclared = assistant_message("banana", [{"type": "text", "text": "x"}])
        missing = assistant_message("stop", [{"type": "text", "text": "x"}])
        del missing["stopReason"]
        for message, shown in ((undeclared, "'banana'"), (missing, "None")):
            self.runner.stdout = stream(message)
            result = self.run_turn()
            self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
            self.assertEqual(
                result.error.code, pi_module.ERROR_UNRECOGNIZED_STOP_REASON
            )
            self.assertIn(shown, result.error.detail)
            self.assertIn("/".join(pi_module.STOP_REASONS), result.error.detail)
            self.assertIsNone(result.message)

    def test_a_later_complete_message_supersedes_an_earlier_truncated_one(self):
        truncated = assistant_message("length", [{"type": "text", "text": "pa"}])
        complete = assistant_message("stop", [{"type": "text", "text": "OK"}])
        events = [HEADER, {"type": "message_end", "message": truncated},
                  {"type": "message_end", "message": complete}]
        self.runner.stdout = "".join(json.dumps(e) + "\n" for e in events).encode()
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_COMPLETED)
        self.assertEqual(result.message, "OK")
        # And the reverse order fails closed on the LAST message.
        events.reverse()
        events.insert(0, events.pop(events.index(HEADER)))
        self.runner.stdout = "".join(json.dumps(e) + "\n" for e in events).encode()
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertEqual(result.error.code, pi_module.ERROR_INCOMPLETE_ANSWER)

    def test_declared_stop_reason_vocabulary_is_complete_and_partitioned(self):
        # types.d.ts line 287 of the installed pi-ai package, as cited in
        # the module docstring.
        self.assertEqual(
            pi_module.STOP_REASONS,
            ("pending", "stop", "length", "toolUse", "error", "aborted",
             "deferred"),
        )
        groups = (pi_module.COMPLETE_STOP_REASONS, pi_module.FAILURE_STOP_REASONS,
                  pi_module.INCOMPLETE_STOP_REASONS)
        union = set()
        for group in groups:
            self.assertEqual(union & set(group), set())
            union |= set(group)
        self.assertEqual(union, set(pi_module.STOP_REASONS))
        self.assertEqual(pi_module.COMPLETE_STOP_REASONS, ("stop",))
        self.assertIn("stop reasons", pi_module.COMPATIBILITY_SURFACE_NOTE)

    def test_aborted_without_error_message_still_fails_honestly(self):
        self.runner.stdout = stream(assistant_message("aborted", []))
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_PI_FAILED)
        self.assertIn("aborted", result.error.detail)

    def test_unknown_event_types_are_counted_on_the_success_path(self):
        self.runner.stdout = SUCCESS_STDOUT + b'{"type":"future_event"}\nplain\n'
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_COMPLETED)
        self.assertEqual(result.message, "OK")
        self.assertEqual(result.unrecognized_event_lines, 2)


class SuccessParsingTests(PiSessionTestCase):
    """9. Success parses the terminal text and session identity."""

    def test_success_from_the_recorded_stream_shape(self):
        result = self.run_turn(text="Reply with exactly the single word OK.")
        self.assertEqual(result.status, pi_module.STATUS_COMPLETED)
        self.assertEqual(result.message, "OK")
        self.assertEqual(result.session_id, SESSION_ID)
        self.assertIsNone(result.error)
        self.assertEqual(result.unrecognized_event_lines, 0)
        self.assertEqual(result.request_id, FIXED_ID)
        self.assertEqual(result.contract_version, pi_module.PI_CONTRACT_VERSION)

    def test_last_assistant_message_wins_and_text_parts_join(self):
        first = assistant_message("stop", [{"type": "text", "text": "draft"}])
        last = assistant_message("stop", [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "line one"},
            {"type": "text", "text": "line two"},
        ])
        events = [HEADER, {"type": "message_end", "message": first},
                  {"type": "message_end", "message": last}]
        self.runner.stdout = "".join(json.dumps(e) + "\n" for e in events).encode()
        result = self.run_turn()
        self.assertEqual(result.message, "line one\nline two")

    def test_message_update_deltas_are_never_assembled_into_an_answer(self):
        events = [HEADER, {"type": "message_update", "usage": {},
                           "assistantMessageEvent": {"type": "text_delta",
                                                     "contentIndex": 0,
                                                     "delta": "OK"}}]
        self.runner.stdout = "".join(json.dumps(e) + "\n" for e in events).encode()
        result = self.run_turn()
        self.assertEqual(result.status, pi_module.STATUS_MALFORMED_OUTPUT)
        self.assertIsNone(result.message)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_parse_events_returns_the_four_tuple(self):
        parsed = pi_module.parse_events(SUCCESS_STDOUT.decode("utf-8"))
        self.assertEqual(parsed, (SESSION_ID, "OK", None, 0))
        self.assertEqual(pi_module.parse_events(""), (None, None, None, 0))
        self.assertEqual(pi_module.parse_events(None), (None, None, None, 0))


class BoundaryScanTests(unittest.TestCase):
    """10 and 11. Imports, authority vocabulary, and the status pin."""

    FORBIDDEN_IMPORT_ROOTS = {
        "codex_gateway", "telegram_operator", "target_runtime", "herdr",
        "herdctl", "workflow_authority", "capability", "worker",
        "pr_delivery", "durable_execution", "git_transport",
    }
    AUTHORITY_WORDS = (
        "mission", "authoriz", "capabilit", "mint", "deliver", "push",
        "tag", "release", "deploy", "merge", "revision", "commit",
        "lifecycle", "durable",
    )

    def test_imports_are_stdlib_plus_the_neutral_seam_only(self):
        tree = ast.parse(MODULE_PATH.read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                roots.add((node.module or "").split(".")[0])
                if (node.module or "").startswith("operator_session"):
                    self.assertEqual(node.module, "operator_session.session")
        self.assertEqual(roots & self.FORBIDDEN_IMPORT_ROOTS, set())
        stdlib = {"json", "subprocess", "uuid", "dataclasses", "typing"}
        self.assertEqual(roots - stdlib, {"operator_session"})

    def test_no_dynamic_import_shell_or_exec_machinery(self):
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", None))
                self.assertNotIn(
                    name, {"__import__", "import_module", "system", "popen",
                           "exec", "execv", "execvp", "execve", "spawn"}
                )
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        self.fail("shell keyword in a call")
            if isinstance(node, ast.Name):
                self.assertNotEqual(node.id, "__import__")
        self.assertNotIn("shell=True", source)

    def test_no_authority_shaped_name_or_literal_outside_docstrings(self):
        source = MODULE_PATH.read_text()
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(
                        (body[0].value.lineno, body[0].value.col_offset)
                    )
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.NAME or (
                token.type == tokenize.STRING and token.start not in docstrings
            ):
                lowered = token.string.lower()
                for word in self.AUTHORITY_WORDS:
                    self.assertNotIn(word, lowered, (token.start, token.string))
                for word in ("herdr", "herdctl", ".herd"):
                    self.assertNotIn(word, lowered, (token.start, token.string))
        # And no dataclass field is authority-shaped either.
        for cls in (pi_module.PiRequest, pi_module.PiResult, pi_module.PiError):
            for field in cls.__dataclass_fields__:
                for word in self.AUTHORITY_WORDS:
                    self.assertNotIn(word, field.lower())

    def test_success_status_is_pinned_to_the_transport_literal(self):
        # The TEST imports both; the module imports neither.
        self.assertEqual(
            pi_module.STATUS_COMPLETED, codex_contract.STATUS_COMPLETED
        )
        self.assertEqual(pi_module.STATUS_COMPLETED, "completed")
        self.assertEqual(
            pi_module.STATUS_INVALID_REQUEST, codex_contract.STATUS_INVALID_REQUEST
        )
        self.assertEqual(
            pi_module.STATUS_MALFORMED_OUTPUT, codex_contract.STATUS_MALFORMED_OUTPUT
        )
        # Pi-specific statuses do not borrow the Codex spelling.
        self.assertNotEqual(
            pi_module.STATUS_PI_FAILED, codex_contract.STATUS_CODEX_FAILED
        )
        self.assertNotEqual(
            pi_module.STATUS_PI_UNAVAILABLE, codex_contract.STATUS_CODEX_UNAVAILABLE
        )

    def test_importing_the_pi_module_loads_no_provider_or_forbidden_module(self):
        probe = subprocess.run(
            [
                sys.executable, "-c",
                # The package __init__ deliberately imports the Codex
                # session, so the probe measures what importing the Pi
                # module ADDS on top of the package: nothing forbidden.
                "import sys\n"
                "import operator_session\n"
                "before = set(sys.modules)\n"
                "import operator_session.pi\n"
                "added = set(sys.modules) - before\n"
                "bad = sorted(name for name in added if name.split('.')[0] in\n"
                "    ('codex_gateway', 'telegram_operator', 'herdr', 'herdctl',\n"
                "     'workflow_authority', 'target_runtime', 'capability',\n"
                "     'worker', 'pr_delivery', 'durable_execution'))\n"
                "print('\\n'.join(sorted(added)))\n"
                "sys.exit(1 if bad else 0)\n",
            ],
            cwd=str(REPO_ROOT),
            env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
            capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, (probe.stdout, probe.stderr))
        self.assertIn("operator_session.pi", probe.stdout.split())

    def test_neutral_package_init_does_not_export_pi(self):
        import operator_session
        self.assertNotIn("PiOperatorSession", operator_session.__all__)
        self.assertFalse(hasattr(operator_session, "PiOperatorSession"))


class CliSelectionTests(unittest.TestCase):
    """Section 4: the explicit `tgop run --operator-provider` flag."""

    def setUp(self):
        from telegram_operator import cli
        self.cli = cli
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        confdir = os.path.join(self.tmp.name, "conf")
        os.makedirs(confdir, mode=0o700)
        self.config_path = os.path.join(confdir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"bot_token": "123:abc", "allowed_user_ids": [42],
                       "repository": self.repo}, handle)
        os.chmod(self.config_path, 0o600)

    def run_with(self, extra):
        constructed = []

        class RecordingAdapter(object):
            def __init__(self, config, store, api, **kwargs):
                constructed.append(kwargs)

            def run(self):
                return None

        with patch.object(self.cli, "Adapter", RecordingAdapter), patch.object(
            pi_module, "run_process"
        ) as run_process:
            code = self.cli.main(["--config", self.config_path, "run"] + extra)
        self.assertEqual(run_process.call_count, 0)
        self.assertEqual(code, self.cli.EXIT_OK)
        self.assertEqual(len(constructed), 1)
        return constructed[0]

    def test_flag_defaults_to_the_reference_provider(self):
        namespace = self.cli._build_parser().parse_args(["run"])
        self.assertEqual(namespace.operator_provider, "codex")
        self.assertEqual(self.cli.DEFAULT_OPERATOR_PROVIDER, "codex")

    def test_absent_flag_passes_no_session_argument(self):
        self.assertEqual(self.run_with([]), {})

    def test_explicit_codex_passes_no_session_argument(self):
        self.assertEqual(self.run_with(["--operator-provider", "codex"]), {})

    def test_explicit_pi_passes_a_pi_session(self):
        kwargs = self.run_with(["--operator-provider", "pi"])
        self.assertEqual(sorted(kwargs), ["operator_session"])
        self.assertIsInstance(kwargs["operator_session"], PiOperatorSession)

    def test_unknown_provider_is_a_usage_error(self):
        with patch.object(self.cli, "Adapter") as adapter:
            with patch("sys.stderr", new_callable=io.StringIO):
                code = self.cli.main(
                    ["--config", self.config_path, "run",
                     "--operator-provider", "other"]
                )
        self.assertEqual(code, self.cli.EXIT_CONFIG)
        self.assertEqual(adapter.call_count, 0)

    def test_the_flag_is_not_a_config_key(self):
        from telegram_operator import config as config_module
        source = (REPO_ROOT / "telegram_operator" / "config.py").read_text()
        self.assertNotIn("operator_provider", source)
        self.assertNotIn("operator-provider", source)
        self.assertNotIn(
            "operator_provider",
            config_module.AdapterConfig.__dataclass_fields__
            if hasattr(config_module.AdapterConfig, "__dataclass_fields__")
            else (),
        )

    def test_adapter_selection_order_is_untouched(self):
        source = (REPO_ROOT / "telegram_operator" / "adapter.py").read_text()
        self.assertNotIn("PiOperatorSession", source)
        self.assertNotIn("operator_session.pi", source)

    def test_help_states_the_flag_selects_the_session_only(self):
        # Round-4 R8: the help must claim exactly the flag's reach — the
        # OperatorSession (intent and decision turns) — and must say the
        # planning turn stays on the reference provider. It must not
        # drift back to "each turn" / "every turn".
        parser = self.cli._build_parser()
        run_parser = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ).choices["run"]
        action = next(
            a for a in run_parser._actions
            if "--operator-provider" in a.option_strings
        )
        help_text = action.help
        lowered = help_text.lower()
        self.assertIn("operatorsession", lowered)
        self.assertIn("planning turn", lowered)
        self.assertIn("reference provider", lowered)
        self.assertIn("only", lowered)
        self.assertIn("launchagent job this cli installs", lowered)
        for over_broad in ("each turn", "every turn", "all turns"):
            self.assertNotIn(over_broad, lowered)

    def test_pi_selection_leaves_the_planning_turn_on_the_reference_seam(self):
        # Behavioral pin of the scope claim: with the Pi session
        # selected, the Adapter's planning turn is STILL the reference
        # provider's role-turn function, wired through its own seam.
        from codex_gateway import role_turn as role_turn_module
        from telegram_operator import adapter as adapter_module
        from telegram_operator import config, state
        with tempfile.TemporaryDirectory() as tmp:
            store = state.StateStore(tmp)
            cfg = config.AdapterConfig(
                bot_token="T", allowed_user_ids=(42,),
                repository="/resolved/repo",
            )
            with patch.object(pi_module, "run_process") as run_process, \
                    patch("subprocess.run") as run:
                adapter = adapter_module.Adapter(
                    cfg, store, object(),
                    operator_session=PiOperatorSession(),
                )
            self.assertEqual(run_process.call_count, 0)
            self.assertEqual(run.call_count, 0)
        self.assertIsInstance(adapter._session, PiOperatorSession)
        self.assertIs(
            adapter._planning_turn, role_turn_module.run_planning_turn
        )

    def test_installed_agent_carries_no_provider_flag(self):
        # The LaunchAgent path never passes the flag, so it keeps running
        # the reference provider — an intended property.
        source = (REPO_ROOT / "telegram_operator" / "launchagent.py").read_text()
        self.assertNotIn("operator-provider", source)
        self.assertNotIn("operator_provider", source)


if __name__ == "__main__":
    unittest.main()
