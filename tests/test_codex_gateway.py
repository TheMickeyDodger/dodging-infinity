"""Regression coverage for the Codex Gateway.

Hermetic: the real codex binary, models, orchestration machinery, and
the network are never invoked. Every codex invocation is intercepted at
the true subprocess.run boundary, so assertions run against the ARGV
ACTUALLY EXECUTED and the keyword surface actually passed — never
against source text. Event fixtures are SYNTHETIC, authored here from
the adapter's declared compatibility-surface constants only.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_gateway import cli, codex_adapter, contract, gateway, repository

# The complete keyword surface invoke_codex may pass to subprocess.run.
# Exact-set equality is the guarantee: no shell, no deadline-style
# keyword, nothing beyond these three. `text` is deliberately absent:
# the child's streams are captured as bytes and decoded explicitly in
# the adapter (round 4, elevated N10).
ALLOWED_RUN_KWARGS = {"input", "cwd", "capture_output"}

FIXED_ID = "f" * 32


def fixed_id_factory():
    return FIXED_ID


def completed_process(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


@contextlib.contextmanager
def intercept_codex(returncode=0, stdout="", stderr="", raise_exc=None):
    """Intercept subprocess.run: git passes through, codex is captured.

    Yields the list of intercepted codex calls as dicts with the argv and
    keyword surface actually passed to subprocess.run.
    """
    real_run = subprocess.run
    calls = []

    def fake_run(argv, **kwargs):
        if argv and argv[0] == "git":
            return real_run(argv, **kwargs)
        calls.append({"argv": list(argv), "kwargs": dict(kwargs)})
        if raise_exc is not None:
            raise raise_exc
        # Mirror the real contract: the adapter captures BYTES streams.
        out = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
        err = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
        return completed_process(argv, returncode, stdout=out, stderr=err)

    with patch("subprocess.run", side_effect=fake_run):
        yield calls


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self.make_repo("repo")

    def make_repo(self, name, git=True, agents=True, protocol=True):
        repo = Path(self._tmp.name) / name
        repo.mkdir()
        if git:
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        if agents:
            (repo / "AGENTS.md").write_text("operator contract\n")
        if protocol:
            (repo / "OPERATOR_PROTOCOL.md").write_text("protocol\n")
        return repo

    def request(self, text="do the thing", repo=None, session_id=None):
        return gateway.build_request(
            text=text,
            repository_path=str(repo if repo is not None else self.repo),
            session_id=session_id,
            request_id_factory=fixed_id_factory,
        )


class ContractTests(unittest.TestCase):
    def test_exit_code_mapping_is_exact(self):
        self.assertEqual(
            contract.EXIT_CODE_BY_STATUS,
            {
                contract.STATUS_COMPLETED: 0,
                contract.STATUS_INVALID_REQUEST: 2,
                contract.STATUS_CODEX_UNAVAILABLE: 3,
                contract.STATUS_CODEX_FAILED: 4,
                contract.STATUS_MALFORMED_OUTPUT: 5,
            },
        )
        for status, code in contract.EXIT_CODE_BY_STATUS.items():
            self.assertEqual(contract.exit_code_for_status(status), code)

    def test_error_detail_below_bound_is_not_truncated(self):
        error = contract.make_error("code", "x" * contract.ERROR_DETAIL_MAX_CHARS)
        self.assertEqual(len(error.detail), contract.ERROR_DETAIL_MAX_CHARS)
        self.assertFalse(error.detail_truncated)

    def test_error_detail_above_bound_sets_truncated_flag(self):
        error = contract.make_error("code", "x" * (contract.ERROR_DETAIL_MAX_CHARS + 1))
        self.assertEqual(len(error.detail), contract.ERROR_DETAIL_MAX_CHARS)
        self.assertTrue(error.detail_truncated)

    def test_default_request_id_factory_is_hex(self):
        request_id = contract.default_request_id_factory()
        self.assertEqual(len(request_id), 32)
        int(request_id, 16)

    def test_contract_version_present_in_request_and_result(self):
        request = gateway.build_request(
            text="t", repository_path=".", request_id_factory=fixed_id_factory
        )
        self.assertEqual(request.contract_version, contract.GATEWAY_CONTRACT_VERSION)
        result = contract.GatewayResult(
            contract_version=contract.GATEWAY_CONTRACT_VERSION,
            request_id=FIXED_ID,
            session_id=None,
            status=contract.STATUS_COMPLETED,
            message="m",
            error=None,
            unrecognized_event_lines=0,
        )
        rendered = contract.result_to_dict(result)
        self.assertEqual(
            rendered["contract_version"], contract.GATEWAY_CONTRACT_VERSION
        )
        self.assertEqual(
            set(rendered),
            {
                "contract_version",
                "request_id",
                "session_id",
                "status",
                "message",
                "error",
                "unrecognized_event_lines",
            },
        )
        self.assertEqual(rendered["unrecognized_event_lines"], 0)

    def test_unrecognized_field_is_required_not_defaulted(self):
        with self.assertRaises(TypeError):
            contract.GatewayResult(
                contract_version=contract.GATEWAY_CONTRACT_VERSION,
                request_id=FIXED_ID,
                session_id=None,
                status=contract.STATUS_COMPLETED,
                message="m",
                error=None,
            )


class RequestValidationTests(GatewayCase):
    def assert_invalid(self, result, error_code, detail_fragment, calls):
        self.assertEqual(result.status, contract.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, error_code)
        self.assertIn(detail_fragment, result.error.detail)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.unrecognized_event_lines, 0)
        self.assertEqual(calls, [], "codex must never be invoked for an invalid request")

    def test_empty_intent(self):
        with intercept_codex() as calls:
            result = gateway.submit(self.request(text=""))
        self.assert_invalid(result, contract.ERROR_EMPTY_INTENT, "empty", calls)

    def test_whitespace_intent(self):
        with intercept_codex() as calls:
            result = gateway.submit(self.request(text=" \n\t "))
        self.assert_invalid(result, contract.ERROR_EMPTY_INTENT, "empty", calls)

    def test_whitespace_session_id(self):
        with intercept_codex() as calls:
            result = gateway.submit(self.request(session_id="  "))
        self.assert_invalid(result, contract.ERROR_EMPTY_SESSION_ID, "session id", calls)

    def test_flag_like_session_id_is_invalid_request(self):
        for flag_like in ("--sandbox=danger-full-access", "-s", "--last", " --json"):
            with intercept_codex() as calls:
                result = gateway.submit(self.request(session_id=flag_like))
            self.assertEqual(result.status, contract.STATUS_INVALID_REQUEST, flag_like)
            self.assertEqual(
                result.error.code, contract.ERROR_FLAG_LIKE_SESSION_ID, flag_like
            )
            self.assertIn(flag_like.strip(), result.error.detail)
            self.assertEqual(calls, [], flag_like)

    def test_non_utf8_encodable_intent_is_invalid_request(self):
        # Lone surrogates as they arrive from a C/POSIX-locale
        # surrogateescape decode of the bytes b"\xff\xfe". The detail
        # wording pins the PRE-INVOCATION validation layer specifically —
        # the seam catch behind it words its detail differently.
        with intercept_codex() as calls:
            result = gateway.submit(self.request(text="do it \udcff\udcfe"))
        self.assert_invalid(
            result,
            contract.ERROR_INTENT_NOT_UTF8,
            "intent text is not UTF-8 encodable",
            calls,
        )

    def test_unicode_encode_error_is_mapped_at_the_gateway_seam(self):
        # Defense in depth: even if a future path bypasses the
        # pre-invocation encodability check, an encode failure at the
        # invocation seam must map to invalid_request, never escape.
        encode_error = UnicodeEncodeError(
            "utf-8", "\udcff", 0, 1, "surrogates not allowed"
        )
        with patch.object(
            codex_adapter, "run_codex_turn", side_effect=encode_error
        ):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, contract.ERROR_INTENT_NOT_UTF8)
        self.assertIn("encoded", result.error.detail)

    def test_banned_flag_error_is_mapped_at_the_gateway_seam(self):
        # Simulate a future argv-construction bug: the guard must surface
        # as an invalid_request result, never escape as an exception.
        banned_argv = ["codex", "exec", "--sandbox", "danger-full-access", "-"]
        with intercept_codex() as calls:
            with patch.object(
                codex_adapter, "build_new_session_argv", return_value=banned_argv
            ):
                result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, contract.ERROR_BANNED_FLAG)
        self.assertIn("--sandbox", result.error.detail)
        self.assertEqual(calls, [], "banned argv must never reach subprocess")

    def test_missing_repository(self):
        missing = os.path.join(self._tmp.name, "does-not-exist")
        with intercept_codex() as calls:
            result = gateway.submit(self.request(repo=missing))
        self.assert_invalid(result, contract.ERROR_REPOSITORY_MISSING, missing, calls)

    def test_non_git_directory(self):
        plain = self.make_repo("plain", git=False)
        with intercept_codex() as calls:
            result = gateway.submit(self.request(repo=plain))
        self.assert_invalid(
            result, contract.ERROR_NOT_A_GIT_WORKTREE, str(plain), calls
        )

    def test_missing_agents_md(self):
        repo = self.make_repo("noagents", agents=False)
        with intercept_codex() as calls:
            result = gateway.submit(self.request(repo=repo))
        self.assert_invalid(
            result, contract.ERROR_OPERATOR_CONTRACT_MISSING, "AGENTS.md", calls
        )
        self.assertIn(str(repo), result.error.detail)

    def test_missing_operator_protocol_md(self):
        repo = self.make_repo("noproto", protocol=False)
        with intercept_codex() as calls:
            result = gateway.submit(self.request(repo=repo))
        self.assert_invalid(
            result,
            contract.ERROR_OPERATOR_CONTRACT_MISSING,
            "OPERATOR_PROTOCOL.md",
            calls,
        )

    def test_git_probe_argv_and_kwarg_surface(self):
        recorded = []
        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            recorded.append({"argv": list(argv), "kwargs": dict(kwargs)})
            return real_run(argv, **kwargs)

        with patch("subprocess.run", side_effect=fake_run):
            resolved, error = repository.validate_repository(str(self.repo))
        self.assertIsNone(error)
        self.assertEqual(resolved, repository.resolve_repository_path(str(self.repo)))
        self.assertEqual(len(recorded), 1)
        self.assertEqual(
            recorded[0]["argv"], ["git", "rev-parse", "--is-inside-work-tree"]
        )
        # Exact-set equality: no shell, no deadline-style keyword, and no
        # `text` — the probe captures bytes and decodes explicitly.
        self.assertEqual(set(recorded[0]["kwargs"]), {"cwd", "capture_output"})

    def test_git_probe_undecodable_output_is_escaped_not_crash(self):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 1, stdout=b"", stderr=b"fatal \xff\xfe"
            )

        with patch("subprocess.run", side_effect=fake_run):
            resolved, error = repository.validate_repository(str(self.repo))
        self.assertIsNone(resolved)
        self.assertEqual(error.code, contract.ERROR_NOT_A_GIT_WORKTREE)
        self.assertIn("\\xff", error.detail)

    def test_git_unavailable_is_invalid_request(self):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError("no git")

        with patch("subprocess.run", side_effect=fake_run):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_INVALID_REQUEST)
        self.assertEqual(result.error.code, contract.ERROR_GIT_UNAVAILABLE)
        self.assertIn("git", result.error.detail)


class ArgvConstructionTests(GatewayCase):
    COMPLETED_STDOUT = (
        json.dumps({"type": "session.created", "session_id": "sess-1"})
        + "\n"
        + json.dumps({"type": "turn.completed", "last_agent_message": "done"})
        + "\n"
    )

    def resolved(self):
        return repository.resolve_repository_path(str(self.repo))

    def test_new_session_argv_exactly(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
            result = gateway.submit(self.request(text="hello there"))
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["argv"],
            ["codex", "exec", "--json", "-C", self.resolved(), "-"],
        )

    def test_resume_argv_exactly_and_carries_no_cd_flag(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
            result = gateway.submit(
                self.request(text="continue", session_id="sess-9")
            )
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["argv"],
            ["codex", "exec", "resume", "--json", "sess-9", "-"],
        )
        self.assertNotIn("-C", calls[0]["argv"])
        self.assertNotIn("--cd", calls[0]["argv"])

    def test_intent_travels_on_stdin_not_argv(self):
        text = "intent that must never appear in argv"
        with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
            gateway.submit(self.request(text=text))
        self.assertEqual(calls[0]["kwargs"]["input"], text.encode("utf-8"))
        self.assertIsInstance(calls[0]["kwargs"]["input"], bytes)
        for element in calls[0]["argv"]:
            self.assertNotIn(text, element)

    def test_kwarg_surface_is_exactly_the_allowed_set(self):
        # Exact-set equality proves shell is never enabled and no
        # deadline-style keyword is ever passed.
        for session_id in (None, "sess-2"):
            with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
                gateway.submit(self.request(session_id=session_id))
            kwargs = calls[0]["kwargs"]
            self.assertEqual(set(kwargs), ALLOWED_RUN_KWARGS)
            self.assertNotIn("shell", kwargs)

    def test_cwd_is_resolved_repository_for_both_paths(self):
        for session_id in (None, "sess-3"):
            with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
                gateway.submit(self.request(session_id=session_id))
            self.assertEqual(calls[0]["kwargs"]["cwd"], self.resolved())


class BannedFlagGuardTests(GatewayCase):
    BANNED_EXAMPLES = [
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--dangerously-anything-future",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--ignore-user-config",
        "--sandbox",
        "--sandbox=danger-full-access",
        "-s",
        "-s=read-only",
        "-sworkspace-write",
        "--add-dir",
        "--add-dir=/anywhere",
        "--ephemeral",
    ]

    def test_guard_rejects_every_banned_flag(self):
        for flag in self.BANNED_EXAMPLES:
            with self.assertRaises(codex_adapter.BannedFlagError, msg=flag):
                codex_adapter.assert_argv_allowed(["codex", "exec", flag, "-"])

    def test_guard_allows_the_constructed_argvs(self):
        codex_adapter.assert_argv_allowed(
            codex_adapter.build_new_session_argv("/some/repo")
        )
        codex_adapter.assert_argv_allowed(codex_adapter.build_resume_argv("sess-1"))

    def test_builders_guard_at_build_time(self):
        # The guard must fire when the argv is CONSTRUCTED, not only at
        # invocation: the builders are public functions and must never
        # return a banned argv to any caller.
        with self.assertRaises(codex_adapter.BannedFlagError):
            codex_adapter.build_resume_argv("--sandbox=danger-full-access")
        with self.assertRaises(codex_adapter.BannedFlagError):
            codex_adapter.build_new_session_argv("--dangerously-x")

    def test_guard_runs_before_invocation(self):
        with intercept_codex() as calls:
            with self.assertRaises(codex_adapter.BannedFlagError):
                codex_adapter.invoke_codex(
                    ["codex", "exec", "--sandbox", "danger-full-access", "-"],
                    "text",
                    str(self.repo),
                )
        self.assertEqual(calls, [], "banned argv must never reach subprocess")


class EventParsingTests(unittest.TestCase):
    def parse(self, *lines):
        return codex_adapter.parse_events("\n".join(lines))

    def test_each_session_key_top_level(self):
        for key in codex_adapter.SESSION_ID_KEYS:
            session_id, _, _, _ = self.parse(json.dumps({key: "s-1"}))
            self.assertEqual(session_id, "s-1", key)

    def test_each_session_key_under_each_wrapper(self):
        for wrapper in codex_adapter.EVENT_WRAPPER_KEYS:
            for key in codex_adapter.SESSION_ID_KEYS:
                session_id, _, _, _ = self.parse(
                    json.dumps({"type": "wrapped", wrapper: {key: "s-2"}})
                )
                self.assertEqual(session_id, "s-2", (wrapper, key))

    def test_each_final_message_key(self):
        for key in codex_adapter.FINAL_MESSAGE_KEYS:
            _, message, _, _ = self.parse(json.dumps({key: "final words"}))
            self.assertEqual(message, "final words", key)

    def test_message_under_wrapper(self):
        for wrapper in codex_adapter.EVENT_WRAPPER_KEYS:
            _, message, _, _ = self.parse(
                json.dumps({wrapper: {"agent_message": "wrapped msg"}})
            )
            self.assertEqual(message, "wrapped msg", wrapper)

    def test_last_message_bearing_event_wins(self):
        _, message, _, _ = self.parse(
            json.dumps({"agent_message": "first"}),
            json.dumps({"agent_message": "second"}),
        )
        self.assertEqual(message, "second")

    def test_first_session_handle_wins(self):
        session_id, _, _, _ = self.parse(
            json.dumps({"session_id": "first"}),
            json.dumps({"session_id": "second"}),
        )
        self.assertEqual(session_id, "first")

    def test_each_failure_event_type(self):
        for event_type in codex_adapter.FAILURE_EVENT_TYPES:
            _, _, failure_detail, _ = self.parse(
                json.dumps({"type": event_type, "message": "boom"})
            )
            self.assertEqual(failure_detail, "boom", event_type)

    def test_failure_event_without_message_still_reports_detail(self):
        _, _, failure_detail, _ = self.parse(json.dumps({"type": "error"}))
        self.assertIsNotNone(failure_detail)
        self.assertIn("failure event", failure_detail)

    def test_blank_lines_skipped_without_counting(self):
        _, message, _, unrecognized = self.parse(
            "", "   ", json.dumps({"text": "ok"}), ""
        )
        self.assertEqual(message, "ok")
        self.assertEqual(unrecognized, 0)

    def test_non_json_and_undeclared_shapes_are_counted(self):
        session_id, message, failure_detail, unrecognized = self.parse(
            "not json at all",
            json.dumps({"type": "undeclared.shape", "payload": 1}),
            json.dumps([1, 2, 3]),
        )
        self.assertIsNone(session_id)
        self.assertIsNone(message)
        self.assertIsNone(failure_detail)
        self.assertEqual(unrecognized, 3)

    def test_empty_output(self):
        self.assertEqual(self.parse(""), (None, None, None, 0))


class TurnOutcomeTests(GatewayCase):
    def test_codex_missing_from_path_maps_to_unavailable(self):
        with intercept_codex(raise_exc=FileNotFoundError("codex not found")) as calls:
            result = gateway.submit(self.request())
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.status, contract.STATUS_CODEX_UNAVAILABLE)
        self.assertEqual(result.error.code, contract.ERROR_CODEX_NOT_FOUND)
        self.assertIn("PATH", result.error.detail)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_codex_not_executable_maps_to_unavailable(self):
        with intercept_codex(raise_exc=PermissionError("denied")):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_UNAVAILABLE)

    def test_codex_nonzero_exit_maps_to_failed_with_bounded_stderr(self):
        with intercept_codex(returncode=17, stderr="x" * 100000):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_FAILED)
        self.assertEqual(result.error.code, contract.ERROR_CODEX_EXIT_NONZERO)
        self.assertIn("17", result.error.detail)
        self.assertEqual(len(result.error.detail), contract.ERROR_DETAIL_MAX_CHARS)
        self.assertTrue(result.error.detail_truncated)

    def test_failure_event_maps_to_failed(self):
        stdout = (
            json.dumps({"session_id": "s-f"})
            + "\n"
            + json.dumps({"type": "turn_failed", "message": "model rejected"})
        )
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_FAILED)
        self.assertEqual(result.error.code, contract.ERROR_CODEX_FAILURE_EVENT)
        self.assertIn("model rejected", result.error.detail)
        self.assertEqual(result.session_id, "s-f")

    def test_empty_output_is_malformed(self):
        with intercept_codex(stdout=""):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.error.code, contract.ERROR_UNRECOGNIZED_OUTPUT)
        self.assertIn("compatibility surface", result.error.detail)

    def test_non_json_output_is_malformed_not_success(self):
        with intercept_codex(stdout="plain text progress\nmore text\n"):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertIn("2 unrecognized line(s)", result.error.detail)

    def test_undeclared_event_shapes_are_malformed_not_success(self):
        stdout = json.dumps({"type": "undeclared", "data": {"x": 1}}) + "\n"
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_MALFORMED_OUTPUT)

    def test_completed_without_session_handle_reports_null_honestly(self):
        stdout = json.dumps({"last_agent_message": "all done"}) + "\n"
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(result.message, "all done")
        self.assertIsNone(result.session_id)

    def test_completed_alongside_unrecognized_lines_discloses_count(self):
        stdout = (
            "not json at all\n"
            + json.dumps({"type": "undeclared.progress", "pct": 40})
            + "\n"
            + json.dumps({"session_id": "s-u", "last_agent_message": "done anyway"})
            + "\n"
            + "trailing garbage\n"
        )
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(result.message, "done anyway")
        self.assertEqual(result.unrecognized_event_lines, 3)

    def test_clean_completed_stream_reports_zero_unrecognized(self):
        stdout = json.dumps({"session_id": "s-c", "last_agent_message": "clean"}) + "\n"
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_nonzero_exit_alongside_unrecognized_lines_discloses_count(self):
        with intercept_codex(returncode=3, stdout="garbage line\n", stderr="boom"):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_FAILED)
        self.assertEqual(result.unrecognized_event_lines, 1)

    def test_failure_event_alongside_unrecognized_lines_discloses_count(self):
        stdout = (
            "garbage before\n"
            + json.dumps({"type": "turn_failed", "message": "model refused"})
            + "\n"
            + "garbage after\n"
        )
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_FAILED)
        self.assertEqual(result.error.code, contract.ERROR_CODEX_FAILURE_EVENT)
        self.assertEqual(result.unrecognized_event_lines, 2)

    def test_malformed_output_reports_unrecognized_field(self):
        # Assert the FIELD itself, not the count embedded in the error
        # detail text, so the structural value is verified on this path.
        with intercept_codex(stdout="garbage one\ngarbage two\n"):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.unrecognized_event_lines, 2)

    def test_non_utf8_stdout_is_malformed_with_distinct_code(self):
        # Elevated N10: an undecodable output stream maps to
        # malformed_output with its own error code — never a crash, never
        # a silent lossy decode presented as fact.
        with intercept_codex(stdout=b"\xff\xfe not utf-8 \xff\n"):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(result.error.code, contract.ERROR_OUTPUT_NOT_UTF8)
        self.assertIn("not valid UTF-8", result.error.detail)
        self.assertIsNone(result.message)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.unrecognized_event_lines, 0)

    def test_undecodable_stream_code_differs_from_plain_malformed(self):
        with intercept_codex(stdout=b"\xff\xfe"):
            undecodable = gateway.submit(self.request())
        with intercept_codex(stdout="plain garbage text\n"):
            plain = gateway.submit(self.request())
        self.assertEqual(undecodable.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(plain.status, contract.STATUS_MALFORMED_OUTPUT)
        self.assertEqual(undecodable.error.code, contract.ERROR_OUTPUT_NOT_UTF8)
        self.assertEqual(plain.error.code, contract.ERROR_UNRECOGNIZED_OUTPUT)
        self.assertNotEqual(undecodable.error.code, plain.error.code)

    def test_undecodable_stderr_is_escaped_and_disclosed(self):
        with intercept_codex(returncode=7, stdout="", stderr=b"boom \xff\xfe"):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_CODEX_FAILED)
        self.assertEqual(result.error.code, contract.ERROR_CODEX_EXIT_NONZERO)
        self.assertIn("\\xff", result.error.detail)
        self.assertIn("shown escaped", result.error.detail)

    def test_stderr_disclosure_survives_the_detail_bound(self):
        huge = b"\xff" + b"y" * 100000
        with intercept_codex(returncode=7, stdout="", stderr=huge):
            result = gateway.submit(self.request())
        self.assertTrue(result.error.detail_truncated)
        self.assertIn("shown escaped", result.error.detail)

    def test_invoke_codex_encodes_intent_strictly(self):
        # The adapter-level encode is strict: lone surrogates raise
        # rather than being silently replaced (the gateway maps this at
        # its seam; direct callers get the honest exception).
        with intercept_codex() as calls:
            with self.assertRaises(UnicodeEncodeError):
                codex_adapter.invoke_codex(
                    ["codex", "exec", "--json", "-C", str(self.repo), "-"],
                    "bad \udcff",
                    str(self.repo),
                )
        self.assertEqual(calls, [])

    def test_completed_with_session_handle(self):
        stdout = (
            json.dumps({"thread_id": "t-1"})
            + "\n"
            + json.dumps({"msg": {"last_agent_message": "finished"}})
        )
        with intercept_codex(stdout=stdout):
            result = gateway.submit(self.request())
        self.assertEqual(result.status, contract.STATUS_COMPLETED)
        self.assertEqual(result.session_id, "t-1")
        self.assertEqual(result.message, "finished")
        self.assertIsNone(result.error)
        self.assertEqual(result.request_id, FIXED_ID)


class BytesStdin:
    """A faithful stand-in for a real piped sys.stdin: exposes the raw
    bytes via .buffer, and a .read() that decodes the way a C/POSIX
    locale would (surrogateescape) — so a mutant that skips the strict
    buffer decode falls through to realistic text, not a crash."""

    def __init__(self, data):
        self._data = data
        self.buffer = io.BytesIO(data)

    def read(self):
        return self._data.decode("utf-8", "surrogateescape")

    def isatty(self):
        return False


class CliTests(GatewayCase):
    COMPLETED_STDOUT = (
        json.dumps({"session_id": "s-cli"})
        + "\n"
        + json.dumps({"last_agent_message": "cli done"})
        + "\n"
    )

    def run_cli(self, argv, stdin_text=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            if stdin_text is not None:
                stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_argv_and_stdin_produce_identical_requests(self):
        captured = []

        def capture(request):
            captured.append(request)
            return contract.GatewayResult(
                contract_version=contract.GATEWAY_CONTRACT_VERSION,
                request_id=request.request_id,
                session_id=None,
                status=contract.STATUS_COMPLETED,
                message="ok",
                error=None,
                unrecognized_event_lines=0,
            )

        with patch.object(gateway, "submit", side_effect=capture), patch.object(
            gateway, "default_request_id_factory", fixed_id_factory
        ):
            base = ["--repo", str(self.repo)]
            # Padded fixture: leading/trailing whitespace on BOTH routes,
            # so removing the normalization on either side breaks parity.
            code_a, _, _ = self.run_cli(base + ["  fix", "the", "bug  "])
            code_b, _, _ = self.run_cli(base, stdin_text="  fix the bug  \n")
        self.assertEqual((code_a, code_b), (0, 0))
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0], captured[1])
        self.assertEqual(captured[0].text, "fix the bug")
        self.assertEqual(captured[0].source, "terminal")

    def test_completed_prints_message_and_exits_zero(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT):
            code, out, err = self.run_cli(
                ["--repo", str(self.repo), "do", "something"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(out, "cli done\n")
        self.assertIn("s-cli", err)
        self.assertIn("--resume", err)

    def test_resume_flow_passes_session_id(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT) as calls:
            code, _, _ = self.run_cli(
                ["--resume", "s-77", "--repo", str(self.repo), "go", "on"]
            )
        self.assertEqual(code, 0)
        self.assertIn("s-77", calls[0]["argv"])

    def test_json_mode_emits_result_contract(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT):
            code, out, err = self.run_cli(
                ["--json", "--repo", str(self.repo), "do", "something"]
            )
        self.assertEqual(code, 0)
        rendered = json.loads(out)
        self.assertEqual(
            rendered["contract_version"], contract.GATEWAY_CONTRACT_VERSION
        )
        self.assertEqual(rendered["status"], "completed")
        self.assertEqual(rendered["message"], "cli done")
        self.assertEqual(rendered["session_id"], "s-cli")
        self.assertIsNone(rendered["error"])

    def test_json_mode_emits_error_contract(self):
        code, out, _ = self.run_cli(["--json", "--repo", str(self.repo)], stdin_text="")
        self.assertEqual(code, 2)
        rendered = json.loads(out)
        self.assertEqual(rendered["status"], "invalid_request")
        self.assertEqual(rendered["error"]["code"], contract.ERROR_EMPTY_INTENT)
        self.assertIn("detail_truncated", rendered["error"])

    def test_every_status_exit_code_mapping_end_to_end(self):
        repo_args = ["--repo", str(self.repo)]
        with intercept_codex(stdout=self.COMPLETED_STDOUT):
            self.assertEqual(self.run_cli(repo_args + ["ok"])[0], 0)
        self.assertEqual(self.run_cli(repo_args, stdin_text=" ")[0], 2)
        with intercept_codex(raise_exc=FileNotFoundError("gone")):
            self.assertEqual(self.run_cli(repo_args + ["x"])[0], 3)
        with intercept_codex(returncode=1, stderr="bad"):
            self.assertEqual(self.run_cli(repo_args + ["x"])[0], 4)
        with intercept_codex(stdout="garbage\n"):
            self.assertEqual(self.run_cli(repo_args + ["x"])[0], 5)

    def test_errors_are_single_actionable_stderr_line(self):
        plain = self.make_repo("plaincli", git=False)
        code, out, err = self.run_cli(["--repo", str(plain), "hi"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn("not_a_git_worktree", lines[0])
        self.assertIn(str(plain), lines[0])
        self.assertNotIn("Traceback", err)

    def test_truncated_detail_is_disclosed_in_text_mode(self):
        with intercept_codex(returncode=9, stderr="y" * 100000):
            code, _, err = self.run_cli(["--repo", str(self.repo), "x"])
        self.assertEqual(code, 4)
        self.assertIn("[detail truncated]", err)

    def test_flag_like_resume_value_exits_two_with_no_traceback(self):
        code, out, err = self.run_cli(
            ["--repo", str(self.repo), "--resume=--sandbox=danger-full-access", "hello"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn("--sandbox=danger-full-access", lines[0])
        self.assertNotIn("Traceback", err)

    def test_bytes_stdin_route_has_argv_parity(self):
        # Third intent-input path (real piped stdin reads bytes via
        # .buffer): same normalization guarantee as the other two routes.
        captured = []

        def capture(request):
            captured.append(request)
            return contract.GatewayResult(
                contract_version=contract.GATEWAY_CONTRACT_VERSION,
                request_id=request.request_id,
                session_id=None,
                status=contract.STATUS_COMPLETED,
                message="ok",
                error=None,
                unrecognized_event_lines=0,
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(gateway, "submit", side_effect=capture), patch.object(
            gateway, "default_request_id_factory", fixed_id_factory
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ), patch(
            "sys.stdin", BytesStdin(b"  fix the bug  \n")
        ):
            code = cli.main(["--repo", str(self.repo)])
        self.assertEqual(code, 0)
        self.assertEqual(captured[0].text, "fix the bug")

    def test_non_utf8_stdin_bytes_exit_two_with_no_traceback(self):
        # Face 1 of reviewer finding B6: invalid UTF-8 bytes on piped
        # stdin (e.g. `cat something.pdf | codexgw`). The CLI must decode
        # strictly and refuse — the stderr line is asserted to come from
        # the strict-decode layer specifically.
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ), patch("sys.stdin", BytesStdin(b"\xff\xfe\x00garbage")):
            code = cli.main(["--repo", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        lines = [line for line in stderr.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn("stdin is not valid UTF-8", lines[0])
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_surrogateescape_stdin_exits_two_with_no_traceback(self):
        # Face 2 of reviewer finding B6: under a C/POSIX locale, a text
        # stdin decodes b"\xff\xfe" with surrogateescape into lone
        # surrogates instead of raising. Driven hermetically through the
        # text-stream fallback path (io.StringIO has no .buffer); the
        # request-validation layer must refuse before any invocation.
        code, out, err = self.run_cli(
            ["--repo", str(self.repo)], stdin_text="\udcff\udcfe"
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn(contract.ERROR_INTENT_NOT_UTF8, lines[0])
        self.assertNotIn("Traceback", err)

    def test_non_utf8_stdout_exits_five_with_no_traceback(self):
        # Elevated N10 at the CLI seam: undecodable Codex output must
        # exit 5 with one actionable stderr line, never a traceback.
        with intercept_codex(stdout=b"\xff\xfegarbage\n"):
            code, out, err = self.run_cli(["--repo", str(self.repo), "go"])
        self.assertEqual(code, 5)
        self.assertEqual(out, "")
        lines = [line for line in err.splitlines() if line]
        self.assertEqual(len(lines), 1)
        self.assertIn(contract.ERROR_OUTPUT_NOT_UTF8, lines[0])
        self.assertIn("not valid UTF-8", lines[0])
        self.assertNotIn("Traceback", err)

    def test_unrecognized_lines_disclosed_on_stderr_in_text_mode(self):
        stdout = (
            "garbage one\n"
            + "garbage two\n"
            + json.dumps({"session_id": "s-d", "last_agent_message": "done"})
            + "\n"
        )
        with intercept_codex(stdout=stdout):
            code, out, err = self.run_cli(["--repo", str(self.repo), "go"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "done\n")
        self.assertIn("2 unrecognized event line(s)", err)

    def test_clean_stream_prints_no_unrecognized_diagnostic(self):
        with intercept_codex(stdout=self.COMPLETED_STDOUT):
            code, _, err = self.run_cli(["--repo", str(self.repo), "go"])
        self.assertEqual(code, 0)
        self.assertNotIn("unrecognized", err)

    def test_json_mode_includes_unrecognized_count(self):
        stdout = (
            "garbage\n"
            + json.dumps({"session_id": "s-j", "last_agent_message": "fine"})
            + "\n"
        )
        with intercept_codex(stdout=stdout):
            code, out, _ = self.run_cli(["--json", "--repo", str(self.repo), "go"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["unrecognized_event_lines"], 1)

    def test_help_exits_zero(self):
        code, out, _ = self.run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("codexgw", out)

    def test_malformed_usage_exits_two(self):
        code, _, err = self.run_cli(["--bogus-flag", "hi"])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_no_intent_and_tty_stdin_exits_two(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ), patch("sys.stdin", Tty()):
            code = cli.main(["--repo", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertIn("stdin", stderr.getvalue())

    def test_default_repository_is_cwd(self):
        captured = []

        def capture(request):
            captured.append(request)
            return contract.GatewayResult(
                contract_version=contract.GATEWAY_CONTRACT_VERSION,
                request_id=request.request_id,
                session_id=None,
                status=contract.STATUS_COMPLETED,
                message="ok",
                error=None,
                unrecognized_event_lines=0,
            )

        with patch.object(gateway, "submit", side_effect=capture), patch(
            "os.getcwd", return_value=str(self.repo)
        ):
            code, _, _ = self.run_cli(["hello"])
        self.assertEqual(code, 0)
        self.assertEqual(
            captured[0].repository,
            repository.resolve_repository_path(str(self.repo)),
        )


if __name__ == "__main__":
    unittest.main()
