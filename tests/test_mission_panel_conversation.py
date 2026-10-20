"""Tests for the panel's conversation runner (``mission_panel.conversation``).

They run the REAL production path: ``RestrictedCodexOperatorSession`` ->
``codex_gateway.role_turn`` (the read-only posture, verified on the exact argv
before any spawn) -> ``interpret_completed`` (the real Codex event parser) ->
the real journal. The ONLY thing substituted is the single process spawn, at
the injection point ``role_turn.run_operator_turn`` documents for exactly this
purpose; its fake returns realistic Codex JSONL. Nothing here talks to a real
provider and nothing here is evidence of one.

What is pinned: one provider call per turn and never a retry; the intent is
durable BEFORE the call; an unknown outcome is UNCERTAIN, not guessed; a
provider's words are untrusted and yield only a validated draft; a second
window cannot interleave a busy conversation.
"""

import json
import os
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
from mission import record as mission_record  # noqa: E402
from mission_panel import conversation as conversation_module  # noqa: E402
from mission_panel import draft as draft_module  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from test_local_request import Bounded  # noqa: E402
from operator_session.codex import RestrictedCodexOperatorSession  # noqa: E402

REPO_URL = "https://github.com/Example/Repo"
GOOD = {
    "objective": "Make the readiness probe stable under load",
    "target_context": "the readiness subsystem",
    "requested_scope": "the readiness probe and its tests; no schema change",
    "requested_action_scope": ["repository_read", "engineering_change"],
}
PROPOSAL_REPLY = "Here is my proposal.\n\n```mission-proposal\n%s\n```\n" % json.dumps(GOOD)


def codex_stdout(message, session_id="s-1"):
    return (json.dumps({"session_id": session_id, "last_agent_message": message})
            + "\n").encode("utf-8")


def completed(argv, stdout=b"", stderr=b"", returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class FakeSpawn(object):
    """The single process spawn: records every call, answers from a script."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, argv, input_bytes, cwd):
        with self.lock:
            self.calls.append({"argv": list(argv), "input": input_bytes, "cwd": cwd})
            step = self.script.pop(0)
        return step(argv) if callable(step) else completed(argv, codex_stdout(step))


class Fixture(Bounded):

    def setUp(self):
        super(Fixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.control = os.path.join(self.tmp.name, "control")
        os.makedirs(self.control)
        run_git("init", "-q", self.control)
        for name in ("AGENTS.md", "OPERATOR_PROTOCOL.md"):
            with open(os.path.join(self.control, name), "w") as handle:
                handle.write("# fixture\n")
        self.directory = os.path.join(self.tmp.name, "state")
        self.journal = journal_module.PanelJournal(self.directory)
        self.now = 3_000_000

    def clock(self):
        self.now += 1
        return self.now

    def conversation(self, spawn, **kwargs):
        session = RestrictedCodexOperatorSession(runner=spawn)
        convo = conversation_module.PanelConversation(
            self.journal, session, self.control, kwargs.pop("repository", REPO_URL),
            self.clock, **kwargs)
        self.addCleanup(convo.idle, 10)
        return convo

    def settle(self, convo, turn):
        self.assertTrue(convo.idle(15), "the provider turn did not finish")
        return self.journal.turn(turn["turn_id"])


class TurnTests(Fixture):

    def test_C1_a_clarifying_turn_goes_through_the_real_restricted_posture(self):
        spawn = FakeSpawn("Which repository do you mean?")
        convo = self.conversation(spawn)
        turn = convo.send(None, "make the probe stable")
        done = self.settle(convo, turn)
        self.assertEqual(done["state"], journal_module.TURN_COMPLETED)
        self.assertEqual(done["assistant_text"], "Which repository do you mean?")
        self.assertIsNone(done["draft"])
        self.assertIsNone(done["draft_problem"])
        self.assertEqual(len(spawn.calls), 1, "exactly one provider call per turn")
        argv = spawn.calls[0]["argv"]
        self.assertIn("read-only", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertNotIn("resume", argv)
        self.assertEqual(spawn.calls[0]["cwd"], os.path.realpath(self.control))
        prompt = spawn.calls[0]["input"].decode("utf-8")
        self.assertIn(draft_module.QUOTE_PREFIX + "make the probe stable", prompt)
        self.assertEqual(done["gateway_request_id"] is not None, True)

    def test_C2_a_proposal_block_yields_a_validated_draft_not_a_proposal_of_record(self):
        convo = self.conversation(FakeSpawn(PROPOSAL_REPLY))
        done = self.settle(convo, convo.send(None, "stabilise the probe"))
        self.assertEqual(done["state"], journal_module.TURN_COMPLETED)
        draft = done["draft"]
        self.assertEqual(draft["proposal"]["repository_url"], REPO_URL)
        self.assertEqual(draft["proposal_digest_sha256"],
                         mission_record.proposal_digest(draft["proposal"]))
        self.assertIsNone(done["submission"])

    def test_C3_an_invalid_block_is_a_problem_shown_not_a_draft(self):
        bad = "```mission-proposal\n%s\n```" % json.dumps(dict(GOOD, approved=True))
        convo = self.conversation(FakeSpawn(bad))
        done = self.settle(convo, convo.send(None, "stabilise the probe"))
        self.assertEqual(done["state"], journal_module.TURN_COMPLETED)
        self.assertIsNone(done["draft"])
        self.assertIn("approved", done["draft_problem"])

    def test_C4_a_reported_provider_failure_is_FAILED_and_never_retried(self):
        spawn = FakeSpawn(lambda argv: completed(argv, b"", b"boom", returncode=1))
        convo = self.conversation(spawn)
        done = self.settle(convo, convo.send(None, "x"))
        self.assertEqual(done["state"], journal_module.TURN_FAILED)
        self.assertEqual(done["outcome"]["error_code"], "codex_exit_nonzero")
        self.assertIsNone(done["assistant_text"])
        self.assertEqual(len(spawn.calls), 1)

    def test_C5_a_provider_that_cannot_start_is_REFUSED(self):
        def missing(argv):
            raise FileNotFoundError("codex")
        convo = self.conversation(FakeSpawn(missing))
        done = self.settle(convo, convo.send(None, "x"))
        self.assertEqual(done["state"], journal_module.TURN_REFUSED)
        self.assertEqual(done["outcome"]["status"], "codex_unavailable")

    def test_C6_unreadable_provider_output_is_UNCERTAIN_not_guessed(self):
        spawn = FakeSpawn(lambda argv: completed(argv, b"not json at all\n"))
        convo = self.conversation(spawn)
        done = self.settle(convo, convo.send(None, "x"))
        self.assertEqual(done["state"], journal_module.TURN_UNCERTAIN)
        self.assertEqual(done["outcome"]["status"], "malformed_output")
        self.assertIsNone(done["assistant_text"])
        self.assertEqual(len(spawn.calls), 1)

    def test_C7_a_call_that_raises_is_UNCERTAIN_and_never_retried(self):
        def explode(argv):
            raise RuntimeError("the pipe broke")
        spawn = FakeSpawn(explode)
        convo = self.conversation(spawn)
        done = self.settle(convo, convo.send(None, "x"))
        self.assertEqual(done["state"], journal_module.TURN_UNCERTAIN)
        self.assertEqual(done["outcome"]["error_code"], "provider_call_raised")
        self.assertIn("RuntimeError", done["outcome"]["detail"])
        self.assertEqual(len(spawn.calls), 1)

    def test_C8_the_intent_is_durable_before_the_provider_is_called(self):
        seen = {}

        def look(argv):
            turns = list(self.journal.load()["turns"].values())
            seen["states"] = [t["state"] for t in turns]
            seen["user_text"] = [t["user_text"] for t in turns]
            return completed(argv, codex_stdout("ok"))
        convo = self.conversation(FakeSpawn(look))
        self.settle(convo, convo.send(None, "durable first"))
        self.assertEqual(seen["states"], [journal_module.TURN_RESERVED])
        self.assertEqual(seen["user_text"], ["durable first"])

    def test_C9_a_second_window_cannot_interleave_a_busy_conversation(self):
        release = threading.Event()

        def slow(argv):
            release.wait(10)
            return completed(argv, codex_stdout("first answer"))
        spawn = FakeSpawn(slow, "second answer")
        convo = self.conversation(spawn)
        first = convo.send(None, "first")
        with self.assertRaises(journal_module.JournalError) as caught:
            convo.send(first["conversation_id"], "second, from another window")
        self.assertEqual(caught.exception.problem,
                         journal_module.PROBLEM_CONVERSATION_BUSY)
        release.set()
        self.settle(convo, first)
        later = convo.send(first["conversation_id"], "third")
        self.assertEqual(self.settle(convo, later)["state"], journal_module.TURN_COMPLETED)
        self.assertEqual(len(spawn.calls), 2)

    def test_C10_a_turn_left_in_flight_by_a_dead_process_is_UNCERTAIN_and_not_rerun(self):
        dead = self.journal.reserve_turn(None, "was in flight", "gw-0", self.clock(), set())
        spawn = FakeSpawn()
        convo = self.conversation(spawn)
        self.assertEqual(convo.start(), [dead["turn_id"]])
        self.assertEqual(self.journal.turn(dead["turn_id"])["state"],
                         journal_module.TURN_UNCERTAIN)
        self.assertEqual(spawn.calls, [])

    def test_C11_provider_turns_in_flight_are_bounded(self):
        release = threading.Event()

        def hold(argv):
            release.wait(10)
            return completed(argv, codex_stdout("ok"))
        n = conversation_module.MAX_CONCURRENT_TURNS
        convo = self.conversation(FakeSpawn(*([hold] * n)))
        started = [convo.send(None, "ask %d" % i) for i in range(n)]
        with self.assertRaises(journal_module.JournalError) as caught:
            convo.send(None, "one too many")
        self.assertEqual(caught.exception.problem,
                         conversation_module.PROBLEM_PROVIDER_BUSY)
        self.assertEqual(len(self.journal.load()["turns"]), n)
        release.set()
        for turn in started:
            self.settle(convo, turn)

    def test_C12_a_long_reply_is_cut_for_display_but_its_draft_is_read_whole(self):
        padding = "x" * (journal_module.MAX_TURN_TEXT_CHARS + 500)
        reply = padding + "\n" + PROPOSAL_REPLY
        convo = self.conversation(FakeSpawn(reply))
        done = self.settle(convo, convo.send(None, "stabilise"))
        self.assertEqual(len(done["assistant_text"]), journal_module.MAX_TURN_TEXT_CHARS)
        self.assertIn("truncated", done["outcome"]["detail"])
        self.assertIsNotNone(done["draft"])

    def test_C13_no_session_is_ever_resumed(self):
        spawn = FakeSpawn("one", "two")
        convo = self.conversation(spawn)
        first = self.settle(convo, convo.send(None, "a"))
        self.settle(convo, convo.send(first["conversation_id"], "b"))
        for call in spawn.calls:
            self.assertNotIn("resume", call["argv"])
        replay = spawn.calls[1]["input"].decode("utf-8")
        self.assertIn(draft_module.QUOTE_PREFIX + "a", replay)
        self.assertIn(draft_module.QUOTE_PREFIX + "one", replay)

    def test_C14_bad_words_are_refused_before_the_journal_or_the_provider(self):
        spawn = FakeSpawn()
        convo = self.conversation(spawn)
        for bad in ("", "   ", "x" * (journal_module.MAX_TURN_TEXT_CHARS + 1)):
            with self.assertRaises(journal_module.JournalError):
                convo.send(None, bad)
        self.assertFalse(os.path.exists(self.directory))
        self.assertEqual(spawn.calls, [])

    def test_C15_a_repository_that_is_not_a_worktree_is_REFUSED_with_no_spawn(self):
        spawn = FakeSpawn()
        session = RestrictedCodexOperatorSession(runner=spawn)
        elsewhere = os.path.join(self.tmp.name, "plain")
        os.makedirs(elsewhere)
        convo = conversation_module.PanelConversation(
            self.journal, session, elsewhere, REPO_URL, self.clock)
        done = self.settle(convo, convo.send(None, "x"))
        self.assertEqual(done["state"], journal_module.TURN_REFUSED)
        self.assertEqual(done["outcome"]["status"], "invalid_request")
        self.assertEqual(spawn.calls, [])


class SharedRegistryTests(Fixture):

    def test_R1_a_submission_in_flight_is_not_settled_by_another_turns_reservation(self):
        registry = conversation_module.InFlight()
        convo = conversation_module.PanelConversation(
            self.journal, RestrictedCodexOperatorSession(runner=FakeSpawn("ok")),
            self.control, REPO_URL, self.clock, registry=registry)
        self.addCleanup(convo.idle, 10)
        first = self.journal.reserve_turn(None, "drafted", "gw-0", self.clock(), set())
        proposal = mission_record.validate_proposal(
            draft_module.extract_draft(PROPOSAL_REPLY, REPO_URL)[0]["proposal"])
        digest = mission_record.proposal_digest(proposal)
        self.journal.finish_turn(
            first["turn_id"], journal_module.TURN_COMPLETED, "ok",
            {"status": "completed", "error_code": None, "detail": None,
             "unrecognized_event_lines": 0},
            {"proposal": proposal, "proposal_digest_sha256": digest}, None,
            self.clock())
        self.journal.reserve_submission(first["turn_id"], digest, None, self.clock())
        registry.add(first["turn_id"])        # the service is mid-submit
        other = convo.send(None, "an unrelated ask from another window")
        self.settle(convo, other)
        self.assertEqual(self.journal.turn(first["turn_id"])["submission"]["state"],
                         journal_module.SUBMISSION_RESERVED)
        registry.discard(first["turn_id"])
        self.assertEqual(convo.start(), [first["turn_id"]])
        self.assertEqual(self.journal.turn(first["turn_id"])["submission"]["state"],
                         journal_module.SUBMISSION_UNCERTAIN)


class DeadlineTests(Fixture):

    def test_D1_the_bounded_runner_spawns_the_same_argv_and_returns_its_output(self):
        runner = conversation_module.bounded_runner(30)
        done = runner([sys.executable, "-c",
                       "import sys; sys.stdout.write(sys.stdin.read().upper())"],
                      b"abc", self.tmp.name)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, b"ABC")

    def test_D2_a_provider_past_its_deadline_is_killed_and_the_turn_is_UNCERTAIN(self):
        pid_file = os.path.join(self.tmp.name, "child.pid")
        script = ("import os, time; open(%r, 'w').write(str(os.getpid())); "
                  "time.sleep(60)" % pid_file)
        runner = conversation_module.bounded_runner(1)

        def spawn(argv, input_bytes, cwd):
            return runner([sys.executable, "-c", script], input_bytes, cwd)
        convo = self.conversation(spawn)
        started = time.monotonic()
        done = self.settle(convo, convo.send(None, "x"))
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(done["state"], journal_module.TURN_UNCERTAIN)
        self.assertEqual(done["outcome"]["error_code"],
                         conversation_module.DEADLINE_ERROR_CODE)
        with open(pid_file) as handle:
            pid = int(handle.read())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the provider child survived its deadline")


if __name__ == "__main__":
    unittest.main()
