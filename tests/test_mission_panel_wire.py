"""Round 27, F2: the panel's views and the office's relay must AGREE about size, measured in the
serialized BYTES that really cross the relay, never in characters.

The finding: the panel accepts a conversation of up to 50 turns, each with two texts of 8000
characters, and serialized with the ``ensure_ascii`` default an astral character is two ``\\uXXXX``
escapes, twelve bytes: 22 such turns already pass the relay's 4 MiB bound, so accepted data became
unreadable. A character bound cannot be honest here either: even as raw UTF-8 a control character
(which JSON must escape, and which a provider's text may carry) is six bytes. So:

- one serializer (``mission_panel.wire.encode``) and one bound (``MAX_RESPONSE_BYTES``) are shared by
  what builds a view and what sends it;
- the conversation view is cut by whole turns, from the oldest, to fit ``service.MAX_VIEW_BYTES`` as
  measured by that serializer, and the answer says how many turns it holds, shows and leaves out;
- the adapter refuses, in words, an answer that would still exceed the bound.

These tests use the real service over the real journal and the real HTTP adapter, with the ACCEPTED
maximum text (astral, control characters), and measure the bytes on the wire.
"""

import http.client
import json
import os
import re
import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from mission_panel import http_adapter  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from mission_panel import wire  # noqa: E402
from test_mission_panel_service import PanelFixture  # noqa: E402

TOKEN = "t" * 43
ASTRAL = "\U0001F600"          # one character, four UTF-8 bytes, twelve as \uXXXX escapes
CONTROL = "\x01"               # one character, six bytes once JSON-escaped
OUTCOME = {"status": "completed", "error_code": None, "detail": None,
           "unrecognized_event_lines": 0}
NOW = 2_000_000
MAX = journal_module.MAX_TURN_TEXT_CHARS


def maximal_proposal(char):
    """The largest proposal the real validator ACCEPTS, filled with ``char``."""
    key = lambda i: "k%d_%s" % (i, "x" * (mission_record.MAX_CONTRACT_KEY_CHARS - 6))
    requirements = [{
        "key": key(i), "description": char * mission_record.MAX_REQUIREMENT_DESCRIPTION_CHARS,
        "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
        "required_artifact_keys": [], "max_evidence_age_seconds": 3600,
    } for i in range(mission_record.MAX_PROOF_REQUIREMENTS)]
    return mission_record.validate_proposal({
        "objective": char * mission_record.MAX_OBJECTIVE_CHARS,
        "target_context": char * mission_record.MAX_TARGET_CONTEXT_CHARS,
        "repository_url": "https://github.com/Example/Repo",
        "requested_scope": char * mission_record.MAX_SCOPE_TEXT_CHARS,
        "requested_action_scope": ["engineering_change", "repository_read"],
        "requested_delivery_target": None,
        "proof_contract": {
            "requirements": requirements, "required_artifacts": [],
            "required_dependencies": [], "required_resource_readiness": [],
            "degradation_policy": {"permitted_blocker_keys": []},
            "continuation_budget": {"max_attempts": 1, "max_checkpoints": 1}}})


class WireTests(unittest.TestCase):

    def test_W1_the_serializer_is_exact_on_every_kind_of_character(self):
        self.assertEqual(wire.encode({"t": "abc"}), b'{"t": "abc"}\n')
        # ASCII is one byte, astral FOUR (raw), a control character SIX (JSON must escape it).
        base = len(wire.encode({"t": ""}))
        self.assertEqual(len(wire.encode({"t": "a" * MAX})) - base, MAX)
        self.assertEqual(len(wire.encode({"t": ASTRAL * MAX})) - base, 4 * MAX)
        self.assertEqual(len(wire.encode({"t": CONTROL * MAX})) - base, 6 * MAX)
        # The escaped form this replaced was three times the astral size.
        self.assertEqual(len((json.dumps({"t": ASTRAL * MAX}) + "\n").encode("ascii")) - base,
                         12 * MAX)

    def test_W2_what_it_writes_is_always_valid_utf8_and_loses_nothing(self):
        for text in ("plain", ASTRAL * 5, "\x00\x01\x1f", "tab\there\nnew", "\ud800", "a\udc00b",
                     "\ud800\ud800", "quote \" backslash \\ ", "  ", "\U0010ffff"):
            body = wire.encode({"t": text, "n": [text]})
            decoded = json.loads(body.decode("utf-8"))          # raises on any invalid UTF-8
            self.assertEqual(decoded["t"], text, repr(text))
            self.assertEqual(decoded["n"], [text], repr(text))

    def test_W3_the_panels_response_bound_equals_the_relays(self):
        source = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "src" / "di"
                  / "missionPanelClient.ts").read_text(encoding="utf-8")
        match = re.search(r"MAX_PANEL_RESPONSE_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024", source)
        self.assertIsNotNone(match, "the relay's bound could not be read from missionPanelClient.ts")
        self.assertEqual(int(match.group(1)) * 1024 * 1024, wire.MAX_RESPONSE_BYTES,
                         "the panel sends answers the relay will refuse (or the reverse)")
        self.assertEqual(http_adapter.MAX_RESPONSE_BYTES, wire.MAX_RESPONSE_BYTES)

    def test_W4_the_view_budget_leaves_room_under_the_response_bound(self):
        self.assertLess(service_module.MAX_VIEW_BYTES, wire.MAX_RESPONSE_BYTES)
        self.assertGreaterEqual(wire.MAX_RESPONSE_BYTES - service_module.MAX_VIEW_BYTES, 512 * 1024)


class Filler(object):
    """Fills the real journal with turns whose texts are the ACCEPTED maximum of one character."""

    def fill(self, turns, user, assistant, draft=None):
        conversation_id = None
        for i in range(turns):
            turn = self.journal.reserve_turn(conversation_id, user * MAX, "gw-%d" % i,
                                             NOW + i, set())
            conversation_id = turn["conversation_id"]
            self.journal.finish_turn(turn["turn_id"], journal_module.TURN_COMPLETED,
                                     assistant * MAX, OUTCOME, draft, None, NOW + i)
        return conversation_id


class ViewTests(Filler, PanelFixture):

    def sizes(self, view):
        return [len(wire.encode(t)) for t in view["turns"]]

    def assert_fits(self, view, label):
        wire_bytes = len(wire.encode(view))
        self.assertLessEqual(sum(self.sizes(view)), service_module.MAX_VIEW_BYTES, label)
        self.assertLessEqual(wire_bytes, wire.MAX_RESPONSE_BYTES,
                             "%s: %d serialized bytes cross the relay" % (label, wire_bytes))

    def test_V0_at_the_accepted_maximum_the_BYTE_budget_binds_before_the_turn_bound(self):
        # The arithmetic that makes the truncation path the one that matters: the accepted maximum is
        # 50 turns x 2 texts x 8000 characters, and astral text is 4 bytes a character as raw UTF-8.
        astral_text_bytes = service_module.MAX_VIEW_TURNS * 2 * MAX * len(ASTRAL.encode("utf-8"))
        self.assertEqual(astral_text_bytes, 3_200_000)
        self.assertGreater(astral_text_bytes, service_module.MAX_VIEW_BYTES,
                           "the text alone is over the byte budget, before any envelope")
        # ... so 22 turns (where the OLD serializer failed) tells nothing about truncation: it is far under.
        self.assertLess(22 * 2 * MAX * 4, service_module.MAX_VIEW_BYTES // 2)

    def test_V1_fifty_turns_of_astral_text_fit_and_the_cut_is_stated(self):
        cid = self.fill(50, ASTRAL, ASTRAL)
        view = self.service.conversation_view(cid)
        self.assert_fits(view, "astral")
        self.assertEqual(view["turns_total"], 50)
        # The expectation is computed from the journal's own turns, not from the answer under test: the
        # largest run of the NEWEST turns whose serialized bytes fit the budget.
        sizes = [len(wire.encode(self.service._turn_view(t, set(), {})))
                 for t in self.service._journal.conversation(cid)]
        expected, total = 0, 0
        for size in reversed(sizes):
            if total + size > service_module.MAX_VIEW_BYTES:
                break
            expected, total = expected + 1, total + size
        self.assertEqual(view["turns_shown"], expected)
        self.assertEqual(view["turns_omitted"], 50 - expected)
        self.assertEqual(view["turns_omitted_by_byte_limit"], 50 - expected,
                         "every omitted turn was left out by the BYTE budget")
        self.assertEqual(view["turns_omitted_by_turn_limit"], 0)
        print("MEASURED astral x50: shown=%d omitted=%d (by bytes) turn_bytes=%d view_bytes=%d "
              "answer_bytes=%d budget=%d bound=%d" % (
                  view["turns_shown"], view["turns_omitted"], sizes[-1], sum(self.sizes(view)),
                  len(wire.encode(view)), service_module.MAX_VIEW_BYTES, wire.MAX_RESPONSE_BYTES))
        # The count is not guessed: it is exactly what fits, from the real per-turn size.
        per_turn = max(self.sizes(view))
        self.assertEqual(view["turns_shown"], len(view["turns"]))
        self.assertEqual(view["turns_shown"] + view["turns_omitted"], 50)
        self.assertGreater(view["turns_omitted"], 0, "50 maximal astral turns cannot all fit")
        self.assertTrue(view["turns_truncated"])
        self.assertGreater(service_module.MAX_VIEW_BYTES - sum(self.sizes(view)), 0)
        self.assertLess(service_module.MAX_VIEW_BYTES - sum(self.sizes(view)), per_turn,
                        "a turn that would have fit was left out")
        self.assertEqual(view["view_limits"], {"turns": service_module.MAX_VIEW_TURNS,
                                               "bytes": service_module.MAX_VIEW_BYTES})

    def test_V2_the_reviewers_case_was_real_twenty_two_ascii_escaped_turns_pass_4_MiB(self):
        # What the old serializer did to exactly this data, kept as the record of the defect.
        cid = self.fill(22, ASTRAL, ASTRAL)
        turns = self.service._journal.conversation(cid)
        views = [self.service._turn_view(t, set(), {}) for t in turns]
        old = len((json.dumps({"turns": views}, sort_keys=True) + "\n").encode("ascii"))
        self.assertGreater(old, wire.MAX_RESPONSE_BYTES)
        # ... and the same 22 turns, served now, are within the bound.
        self.assert_fits(self.service.conversation_view(cid), "22 astral turns")

    def test_V3_control_characters_are_six_bytes_each_and_the_bound_still_holds(self):
        cid = self.fill(50, CONTROL, CONTROL)
        view = self.service.conversation_view(cid)
        self.assert_fits(view, "control characters")
        self.assertGreater(view["turns_omitted"], 0)
        # The cut is by serialized bytes: fewer turns than the astral case, though the character
        # counts are identical (a character bound could not have told them apart).
        astral = self.service.conversation_view(self.fill(50, ASTRAL, ASTRAL))
        self.assertLess(view["turns_shown"], astral["turns_shown"])

    def test_V4_ordinary_text_shows_every_turn_up_to_the_count_bound_and_says_nothing_is_cut(self):
        cid = self.fill(50, "a", "b")
        view = self.service.conversation_view(cid)
        self.assertEqual((view["turns_shown"], view["turns_omitted"], view["turns_truncated"]),
                         (50, 0, False))
        self.assertEqual((view["turns_omitted_by_turn_limit"], view["turns_omitted_by_byte_limit"]),
                         (0, 0))

    def test_V5_the_count_bound_still_holds_and_is_stated(self):
        cid = self.fill(service_module.MAX_VIEW_TURNS + 7, "a", "b")
        view = self.service.conversation_view(cid)
        self.assertEqual(view["turns_total"], service_module.MAX_VIEW_TURNS + 7)
        self.assertEqual(view["turns_shown"], service_module.MAX_VIEW_TURNS)
        self.assertEqual(view["turns_omitted"], 7)
        self.assertEqual((view["turns_omitted_by_turn_limit"], view["turns_omitted_by_byte_limit"]),
                         (7, 0), "short text: it is the turn bound that cut, and the answer says so")
        self.assertTrue(view["turns_truncated"])

    def test_V5b_both_bounds_can_cut_in_one_view_and_each_is_counted(self):
        cid = self.fill(service_module.MAX_VIEW_TURNS + 5, ASTRAL, ASTRAL)
        view = self.service.conversation_view(cid)
        self.assertEqual(view["turns_total"], service_module.MAX_VIEW_TURNS + 5)
        self.assertEqual(view["turns_omitted_by_turn_limit"], 5)
        self.assertGreater(view["turns_omitted_by_byte_limit"], 0)
        self.assertEqual(view["turns_omitted"], view["turns_omitted_by_turn_limit"]
                         + view["turns_omitted_by_byte_limit"])
        self.assertEqual(view["turns_shown"] + view["turns_omitted"], view["turns_total"])
        self.assert_fits(view, "both bounds")

    def test_V6_the_turns_shown_are_the_most_recent_contiguous_ones_in_order(self):
        cid = self.fill(50, ASTRAL, ASTRAL)
        view = self.service.conversation_view(cid)
        sequences = [t["sequence"] for t in view["turns"]]
        self.assertEqual(sequences, list(range(51 - len(sequences), 51)))
        self.assertEqual(sequences[-1], 50, "the newest turn must always be shown")

    def test_V7_the_journal_keeps_every_turn_the_view_leaves_out(self):
        cid = self.fill(50, CONTROL, CONTROL)
        view = self.service.conversation_view(cid)
        self.assertGreater(view["turns_omitted"], 0)
        self.assertEqual(len(self.service._journal.conversation(cid)), 50)

    def test_V8_a_single_turn_at_the_accepted_maximum_is_far_below_the_budget(self):
        # The newest turn is always shown, so it must fit by a wide margin at the very maximum:
        # both texts, AND a draft proposal at its accepted maximum, in the worst character.
        for char in (ASTRAL, CONTROL, "a"):
            proposal = maximal_proposal(char)
            draft = {"proposal": proposal,
                     "proposal_digest_sha256": mission_record.proposal_digest(proposal)}
            cid = self.fill(1, char, char, draft=draft)
            view = self.service.conversation_view(cid)
            self.assertEqual(view["turns_shown"], 1)
            size = self.sizes(view)[0]
            self.assertLess(size, service_module.MAX_VIEW_BYTES // 3,
                            "a single maximal turn (%d bytes) leaves too little room" % size)

    def test_V9_every_other_answer_that_carries_accepted_text_fits_at_its_maximum(self):
        # request view: the largest proposal the validator accepts, through the real surface.
        proposal = maximal_proposal(CONTROL)
        fields = {k: proposal[k] for k in proposal if k != "proof_contract"}
        fields["proof_contract"] = proposal["proof_contract"]
        out = self.surface_submit(fields)
        view = self.service.request_view(out["request_ref"])
        self.assertLessEqual(len(wire.encode(view)), wire.MAX_RESPONSE_BYTES)
        self.assertLess(len(wire.encode(view)), wire.MAX_RESPONSE_BYTES // 4)
        # The result view's two texts, at the display bound, in the worst character.
        result = {"ok": True, "result_text": CONTROL * service_module.MAX_RESULT_TEXT_CHARS,
                  "review_text": CONTROL * service_module.MAX_RESULT_TEXT_CHARS}
        self.assertLess(len(wire.encode(result)), wire.MAX_RESPONSE_BYTES // 4)
        # The overview: the most requests it lists, each at the maximal objective.
        summaries = {"requests": [{"objective": CONTROL * mission_record.MAX_OBJECTIVE_CHARS}
                                  for _ in range(service_module.MAX_OVERVIEW_REQUESTS)]}
        self.assertLess(len(wire.encode(summaries)), wire.MAX_RESPONSE_BYTES // 2)

    def surface_submit(self, fields):
        return self.surface.submit(fields)


class WireBoundaryTests(Filler, PanelFixture):
    """The same data through the real HTTP adapter, measured on the raw response."""

    def setUp(self):
        super(WireBoundaryTests, self).setUp()
        self.server = http_adapter.PanelServer(self.service, TOKEN)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        connection.request("GET", path, headers={"Authorization": "Bearer " + TOKEN,
                                                 "Host": "127.0.0.1:%d" % self.port})
        response = connection.getresponse()
        body = response.read()
        declared = response.getheader("Content-Length")
        connection.close()
        return response.status, declared, body

    def test_B1_the_raw_bytes_of_the_worst_accepted_conversation_cross_within_the_bound(self):
        for char in (ASTRAL, CONTROL):
            cid = self.fill(50, char, char)
            status, declared, body = self.get("/conversations/%s" % cid)
            self.assertEqual(status, 200)
            self.assertEqual(int(declared), len(body))
            self.assertLessEqual(len(body), wire.MAX_RESPONSE_BYTES, repr(char))
            answer = json.loads(body.decode("utf-8"))
            self.assertTrue(answer["ok"])
            self.assertEqual(answer["turns_shown"] + answer["turns_omitted"], 50)
            self.assertGreater(answer["turns_omitted"], 0)
            self.assertEqual(answer["turns"][-1]["sequence"], 50)
            self.assertEqual(answer["turns"][-1]["user_text"], char * MAX)

    def test_B2_an_answer_over_the_bound_is_refused_in_words_never_sent(self):
        huge = {"ok": True, "padding": "x" * (wire.MAX_RESPONSE_BYTES + 10)}
        self.service.overview = lambda: huge
        status, declared, body = self.get("/overview")
        self.assertEqual(status, 200)
        self.assertLess(len(body), 4096)
        answer = json.loads(body.decode("utf-8"))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["problem"], "mission_panel_response_too_large")
        self.assertEqual(answer["details"]["limit"], wire.MAX_RESPONSE_BYTES)
        self.assertGreater(answer["details"]["bytes"], wire.MAX_RESPONSE_BYTES)
        self.assertIn("refused instead of being cut", answer["reason"])


if __name__ == "__main__":
    unittest.main()
