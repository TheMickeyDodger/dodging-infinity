"""C1: the request panel's declared clarifying questions
(``mission_panel.draft.extract_questions``, the optional ``questions`` journal
turn field, and the panel overview/conversation fields built from it).

What is REAL: the extractor, the journal over a real temporary protected
directory, the panel service over the real local request surface and Mission
Core fixtures of ``tests/test_mission_panel_service.py``. What is SYNTHETIC,
labelled so: every "provider reply" is scripted text (``FakeSpawn``); no model
is called.

The questions are UNTRUSTED display text. Nothing here lets them approve,
start or change anything, and the panel's ``delivery_authority`` stays
``none``. The claims below, each against its near neighbour:

- only a closed ``mission-questions`` fenced block is read; the same question
  asked in prose is NOT read;
- a bad block is refused with a recorded problem, never repaired or
  truncated, and never makes the turn look like "none declared";
- a journal written before this field existed validates unchanged and reads
  "not reported by this panel version", which is different from "read; none
  declared";
- "awaiting your answer" is derived from canonical journal fields: the newest
  COMPLETED turn has questions, no draft, and NO later turn of any state.
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_panel import draft as draft_module  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
import test_mission_panel_journal as journal_tests  # noqa: E402
import test_mission_panel_service as service_tests  # noqa: E402
from test_mission_panel_conversation import GOOD, completed  # noqa: E402

FENCE = draft_module.QUESTIONS_FENCE


def block(payload):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return "Before you start:\n%s\n%s\n```\nThank you.\n" % (FENCE, body)


def questions_reply(*items):
    return block({"questions": list(items)})


class PinTests(unittest.TestCase):

    def test_the_bounds_are_pinned_by_exact_value(self):
        self.assertEqual(journal_module.MAX_QUESTIONS, 3)
        self.assertEqual(journal_module.MAX_QUESTION_CHARS, 280)
        self.assertEqual(draft_module.QUESTIONS_FENCE, "```mission-questions")
        self.assertEqual(journal_module.OPTIONAL_TURN_KEYS, ("questions",))
        self.assertEqual(journal_module.QUESTIONS_KEYS, ("items", "problem"))
        self.assertEqual(service_module.QUESTIONS_NOT_REPORTED,
                         "not_reported_by_this_panel_version")
        self.assertEqual(service_module.QUESTIONS_READ, "read")
        self.assertEqual(service_module.QUESTIONS_NOT_APPLICABLE,
                         "not_applicable")

    def test_the_prompt_teaches_the_block_and_its_bounds_and_claims_no_authority(
            self):
        prompt = draft_module.render_turn_prompt([], "hello", None)
        self.assertIn(FENCE, prompt)
        self.assertIn("at\nmost 3 strings", prompt)
        self.assertIn("at most 280 characters", prompt)
        self.assertIn("A question is never a request for approval.", prompt)
        self.assertIn("This turn carries no authority.", prompt)
        self.assertEqual(prompt.count(FENCE), 1)


class ExtractionTests(unittest.TestCase):

    def extract(self, message):
        return draft_module.extract_questions(message)

    def test_a_valid_block_is_read_in_order(self):
        self.assertEqual(
            self.extract(questions_reply("Which branch?", "Which tests?")),
            {"items": ["Which branch?", "Which tests?"], "problem": None})

    def test_no_block_reads_as_none_declared_and_prose_is_never_parsed(self):
        for message in ("", "All clear.",
                        "Which branch should I use? And which tests?",
                        "1. Which branch?\n2. Which tests?\n",
                        "questions: [\"Which branch?\"]"):
            with self.subTest(message=message[:30]):
                self.assertEqual(self.extract(message),
                                 {"items": [], "problem": None})

    def test_the_count_and_length_bounds_are_the_edge_not_a_truncation(self):
        three = ["q%d?" % i for i in range(3)]
        self.assertEqual(self.extract(questions_reply(*three))["items"], three)
        four = self.extract(questions_reply(*(three + ["q3?"])))
        self.assertEqual(four["items"], [])
        self.assertIn("at most 3", four["problem"])
        exact = "x" * 280
        self.assertEqual(self.extract(questions_reply(exact))["items"], [exact])
        over = self.extract(questions_reply("x" * 281))
        self.assertEqual(over["items"], [])
        self.assertIn("at most 280", over["problem"])

    def test_every_malformed_or_ambiguous_block_is_refused_with_a_problem(self):
        cases = {
            "invalid json": block("{not json"),
            "not an object": block("[\"a?\"]"),
            "extra key": block({"questions": ["a?"], "approved": True}),
            "missing key": block({}),
            "questions not a list": block({"questions": "a?"}),
            "non-string item": block({"questions": [1]}),
            "empty item": block({"questions": ["   "]}),
            "newline in item": block({"questions": ["a?\nb?"]}),
            "control character": block({"questions": ["a\u0007?"]}),
            "duplicate question": block({"questions": ["a?", "a?"]}),
            "duplicate key": block('{"questions": ["a?"], "questions": ["b?"]}'),
            "NaN constant": block('{"questions": [NaN]}'),
            "two blocks": questions_reply("a?") + questions_reply("b?"),
            "never closed": "x\n%s\n{\"questions\": [\"a?\"]}\n" % FENCE,
        }
        for label, message in cases.items():
            with self.subTest(label):
                got = self.extract(message)
                self.assertEqual(got["items"], [], label)
                self.assertIsInstance(got["problem"], str, label)
                self.assertTrue(0 < len(got["problem"])
                                <= journal_module.MAX_DETAIL_CHARS, label)

    def test_a_block_that_is_not_at_the_start_of_a_line_is_not_read(self):
        message = "see ```mission-questions {\"questions\": [\"a?\"]} ``` ok"
        self.assertEqual(self.extract(message), {"items": [], "problem": None})

    def test_questions_inside_another_fence_are_not_read(self):
        message = ("```text\n%s\n{\"questions\": [\"a?\"]}\n```\n```\n"
                   % FENCE)
        self.assertEqual(self.extract(message), {"items": [], "problem": None})

    def test_questions_and_a_proposal_block_are_read_independently(self):
        proposal = "```mission-proposal\n%s\n```" % json.dumps(GOOD)
        message = questions_reply("Which branch?") + "\n" + proposal + "\n"
        self.assertEqual(self.extract(message)["items"], ["Which branch?"])
        draft, problem = draft_module.extract_draft(
            message, "https://github.com/octo/repo")
        self.assertIsNone(problem)
        # the proposal extractor never sees the questions fence
        self.assertEqual(draft_module._proposal_blocks(message)[0],
                         [json.dumps(GOOD)])

    def test_proposal_extraction_is_unchanged_by_the_refactor(self):
        message = ("x\n```mission-proposal\n%s\n```\n" % json.dumps(GOOD))
        blocks, unterminated = draft_module._proposal_blocks(message)
        self.assertEqual((blocks, unterminated), ([json.dumps(GOOD)], False))
        blocks, unterminated = draft_module._proposal_blocks(
            "```mission-proposal\n{}\n")
        self.assertEqual((blocks, unterminated), ([], True))


class JournalTests(journal_tests.Fixture):

    def completed_turn(self, **overrides):
        fields = dict(
            state=journal_module.TURN_COMPLETED, assistant_text="Asking.",
            outcome={"status": "completed", "error_code": None, "detail": None,
                     "unrecognized_event_lines": 0},
            draft=None, draft_problem=None)
        fields.update(overrides)
        turn = self.journal.reserve_turn(None, "hello", "gw-1", journal_tests.NOW,
                                         set())
        return self.journal.finish_turn(turn["turn_id"],
                                        now=journal_tests.NOW + 5, **fields)

    def test_a_legacy_journal_validates_and_round_trips_byte_for_byte(self):
        turn = self.completed_turn()
        self.assertNotIn("questions", turn)         # written without the field
        before = self.raw()
        document = self.journal.load()
        self.assertNotIn("questions", next(iter(document["turns"].values())))
        journal_module.validate_document(copy.deepcopy(document))
        self.journal.save(document)
        self.assertEqual(self.raw(), before)
        # a record that never had the key still reads, and a reserved one too
        self.journal.reserve_turn(turn["conversation_id"], "again", "gw-2",
                                  journal_tests.NOW + 9, set())
        self.assertEqual(len(self.journal.load()["turns"]), 2)

    def test_a_turn_with_questions_validates_and_round_trips(self):
        turn = self.completed_turn(
            questions={"items": ["Which branch?"], "problem": None})
        self.assertEqual(turn["questions"],
                         {"items": ["Which branch?"], "problem": None})
        self.assertEqual(self.journal.turn(turn["turn_id"])["questions"],
                         turn["questions"])
        again = self.completed_turn(questions={"items": [], "problem": None})
        self.assertEqual(again["questions"], {"items": [], "problem": None})

    def test_invalid_questions_fail_closed(self):
        turn = self.completed_turn(
            questions={"items": ["Which branch?"], "problem": None})
        document = json.loads(self.raw())
        record = document["turns"][turn["turn_id"]]
        tampers = {
            "unknown key": lambda q: q.update(approved=True),
            "items not a list": lambda q: q.update(items="Which branch?"),
            "four items": lambda q: q.update(items=["a?", "b?", "c?", "d?"]),
            "item too long": lambda q: q.update(items=["x" * 281]),
            "item not a string": lambda q: q.update(items=[7]),
            "repeated item": lambda q: q.update(items=["a?", "a?"]),
            "control character": lambda q: q.update(items=["a\x00?"]),
            "items and a problem": lambda q: q.update(problem="and a problem"),
            "problem too long": lambda q: q.update(items=[],
                                                   problem="p" * 501),
            "missing problem": lambda q: q.pop("problem"),
        }
        for label, tamper in tampers.items():
            with self.subTest(label):
                bad = copy.deepcopy(document)
                tamper(bad["turns"][turn["turn_id"]]["questions"])
                with self.assertRaises(journal_module.JournalError):
                    journal_module.validate_document(bad)
        for label, change in {
                "questions not an object": {"questions": ["a?"]},
                "questions on a RESERVED turn": {
                    "state": "RESERVED", "finished_at": None,
                    "assistant_text": None, "outcome": None},
                "questions on a FAILED turn": {"state": "FAILED"},
        }.items():
            with self.subTest(label):
                bad = copy.deepcopy(document)
                bad["turns"][turn["turn_id"]].update(change)
                with self.assertRaises(journal_module.JournalError):
                    journal_module.validate_document(bad)
        del record

    def test_a_recorded_problem_is_kept_and_no_items_with_it(self):
        turn = self.completed_turn(questions={
            "items": [], "problem": "the questions block repeats a question"})
        self.assertEqual(turn["questions"]["items"], [])
        self.assertIn("repeats", turn["questions"]["problem"])


class PanelViewTests(service_tests.PanelFixture):
    """Through the real panel service: what a window is told."""

    def conversation(self, cid):
        return next(c for c in self.service.overview()["conversations"]
                    if c["conversation_id"] == cid)

    def fail_next(self):
        self.provider.script.append(lambda argv: completed(
            argv, b"", b"boom", returncode=1))

    def test_asking_questions_means_awaiting_your_answer(self):
        turn = self.say("make it stable", questions_reply("Which branch?",
                                                          "Which tests?"))
        self.assertEqual(turn["questions"], {
            "reporting": "read", "items": ["Which branch?", "Which tests?"],
            "problem": None})
        entry = self.conversation(turn["conversation_id"])
        self.assertTrue(entry["awaiting_answer"])
        self.assertEqual(entry["awaiting_questions"],
                         ["Which branch?", "Which tests?"])
        self.assertEqual(entry["awaiting_turn_id"], turn["turn_id"])
        self.assertEqual(entry["questions_reporting"], "read")
        self.assertIsNone(entry["questions_problem"])
        self.assertEqual(entry["latest_turn_state"], "COMPLETED")
        self.assertEqual(self.service.capabilities()["delivery_authority"],
                         "none")
        self.assertEqual(turn["provider_text"], "untrusted")

    def test_a_later_turn_of_any_kind_ends_the_waiting_without_claiming_an_answer(
            self):
        first = self.say("make it stable", questions_reply("Which branch?"))
        cid = first["conversation_id"]
        self.assertTrue(self.conversation(cid)["awaiting_answer"])
        # a later FAILED turn: not awaiting, and the state is named
        self.fail_next()
        self.service.send_turn(cid, "main")
        self.assertTrue(self.convo.idle(15))
        entry = self.conversation(cid)
        self.assertFalse(entry["awaiting_answer"])
        self.assertEqual(entry["latest_turn_state"], "FAILED")
        self.assertEqual(entry["awaiting_questions"], [])
        self.assertIsNone(entry["awaiting_turn_id"])
        # a later COMPLETED turn that asks again: awaiting THAT turn, once
        second = self.say("main", questions_reply("Which tests?"), cid)
        entry = self.conversation(cid)
        self.assertTrue(entry["awaiting_answer"])
        self.assertEqual(entry["awaiting_turn_id"], second["turn_id"])
        self.assertEqual(entry["awaiting_questions"], ["Which tests?"])
        self.assertEqual(sum(1 for c in self.service.overview()[
            "conversations"] if c["conversation_id"] == cid), 1)
        # a later COMPLETED turn that asks nothing: answered, not awaiting
        self.say("the unit tests", "Thanks, that is enough.", cid)
        entry = self.conversation(cid)
        self.assertFalse(entry["awaiting_answer"])
        self.assertEqual(entry["questions_reporting"], "read")
        self.assertEqual(entry["latest_turn_state"], "COMPLETED")

    def test_a_reserved_or_uncertain_later_turn_is_not_awaiting_either(self):
        first = self.say("make it stable", questions_reply("Which branch?"))
        cid = first["conversation_id"]

        def explode(argv):
            raise RuntimeError("pipe broke")
        self.provider.script.append(explode)
        self.service.send_turn(cid, "main")
        self.assertTrue(self.convo.idle(15))
        entry = self.conversation(cid)
        self.assertFalse(entry["awaiting_answer"])
        self.assertEqual(entry["latest_turn_state"], "UNCERTAIN")

    def test_a_draft_in_the_same_reply_is_not_a_question_for_the_person(self):
        reply = (questions_reply("Which branch?") + "\n```mission-proposal\n"
                 + json.dumps(GOOD) + "\n```\n")
        turn = self.say("make it stable", reply)
        self.assertIsNotNone(turn["draft"])
        entry = self.conversation(turn["conversation_id"])
        self.assertFalse(entry["awaiting_answer"])
        self.assertEqual(entry["questions_reporting"], "read")

    def test_a_malformed_block_is_a_recorded_problem_never_none_declared(self):
        turn = self.say("make it stable", block({"questions": ["a?", "a?"]}))
        self.assertEqual(turn["questions"]["reporting"], "read")
        self.assertEqual(turn["questions"]["items"], [])
        self.assertIn("repeats", turn["questions"]["problem"])
        entry = self.conversation(turn["conversation_id"])
        self.assertFalse(entry["awaiting_answer"])
        self.assertIn("repeats", entry["questions_problem"])

    def test_read_none_declared_is_distinct_from_not_reported(self):
        turn = self.say("hello", "Nothing to ask.")
        self.assertEqual(turn["questions"], {
            "reporting": "read", "items": [], "problem": None})
        cid = turn["conversation_id"]
        # make the stored turn look like one a legacy panel wrote
        document = self.journal.load()
        for record in document["turns"].values():
            record.pop("questions", None)
        self.journal.save(document)
        entry = self.conversation(cid)
        self.assertEqual(entry["questions_reporting"],
                         "not_reported_by_this_panel_version")
        self.assertFalse(entry["awaiting_answer"])
        view = self.service.conversation_view(cid)["turns"][-1]
        self.assertEqual(view["questions"], {
            "reporting": "not_reported_by_this_panel_version", "items": [],
            "problem": None})

    def test_a_conversation_with_no_completed_reply_reads_not_applicable(self):
        self.fail_next()
        turn = self.service.send_turn(None, "hello")["turn"]
        self.assertTrue(self.convo.idle(15))
        entry = self.conversation(turn["conversation_id"])
        self.assertFalse(entry["awaiting_answer"])
        self.assertEqual(entry["questions_reporting"], "not_applicable")
        self.assertEqual(entry["latest_turn_state"], "FAILED")

    def test_the_overview_stays_inside_its_response_bound(self):
        from mission_panel import wire as wire_module
        for index in range(service_module.MAX_OVERVIEW_CONVERSATIONS + 3):
            self.say("request %d" % index,
                     questions_reply(*("q%d?" % i + "x" * 270
                                       for i in range(3))))
        overview = self.service.overview()
        self.assertEqual(len(overview["conversations"]),
                         service_module.MAX_OVERVIEW_CONVERSATIONS)
        self.assertLess(len(wire_module.encode(overview)),
                        wire_module.MAX_RESPONSE_BYTES)


if __name__ == "__main__":
    unittest.main()
