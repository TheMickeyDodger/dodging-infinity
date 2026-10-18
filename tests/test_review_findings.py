"""Item 2 E2: reviewer-declared PUBLIC review findings.

The contract (``target_runtime.review_findings``), the LOCAL candidate writer
(``herdctl review-findings-route`` and ``review-decision`` in THIS
repository's ``herdctl.py``, loaded in isolation — never the installed
producer), and the read-only reader (``target_runtime.review_record``).
Every mutation runs in an ISOLATED temporary herd root.

What is proved, each against its near neighbour:

1. THE ENVELOPE: only a whole-line ``HERD_FINDINGS_BEGIN v1`` .. ``END``
   envelope that echoes the CURRENT route's single-use nonce is consumed.
   Terse tail lines, surrounding prose, code examples, other rounds'
   envelopes and canaries never become findings. Missing, duplicate,
   spoofed, incomplete, malformed, over-bound and count-mismatched
   declarations fail CLOSED with distinct codes; a dropped blocker is a
   failure, never a shorter list.
2. LINKAGE against the ROUTING CONTEXT the writer recorded itself (task,
   round, reviewer, candidate, nonce), not two caller-supplied strings: a
   wrong task, round, reviewer or candidate, a route for another round or
   reviewer, or no route at all, refuses or records nothing.
3. VERDICTS UNCHANGED: the round file and the task's decision fields are
   byte-identical with and without a declaration, whatever it says.
4. THE READER: legacy records read as legacy not-recorded (never empty or
   invalid); empty, present and refused are distinct; the record is
   digest-, round-, reviewer- and preamble-bound; no transcript byte is
   ever read; a canary in the transcript never reaches a finding.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from target_runtime import review_findings as rf  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from target_runtime import review_record  # noqa: E402

TASK = "20990101-000000-5e1f00"
REVIEWER = "reviewer1"
AGENT = "pod-reviewer1"
CANDIDATE = "8d56830dc9f22f91dd81620727b06884dcf314168d06867107360e4109d94ef8"
OTHER_CANDIDATE = "e" * 64
NONCE = "3f9a1c0b7d2e4f6a"
CANARY = "TRANSCRIPT-CANARY-NEVER-A-FINDING"


def route(**overrides):
    value = {"schema": rf.ROUTE_SCHEMA, "version": rf.CONTRACT_VERSION,
             "nonce": NONCE, "task_id": TASK, "round": 1,
             "reviewer_logical": REVIEWER, "reviewer_agent": AGENT,
             "candidate_sha256": CANDIDATE, "routed_at": 1800000000}
    value.update(overrides)
    return value


def envelope(findings=(("blocker", "target_runtime/x.py:12",
                        "the read happens before the class check"),),
             nonce=NONCE, task=TASK, rnd=1, reviewer=REVIEWER,
             candidate=CANDIDATE, count=None, end_count=None, end_nonce=None,
             indices=None, indent="  "):
    """An envelope as the Reviewer's TUI renders it (2-space indent)."""
    count = len(findings) if count is None else count
    lines = [rf.BEGIN_LINE, "nonce %s" % nonce, "task %s" % task,
             "round %d" % rnd, "reviewer %s" % reviewer,
             "candidate %s" % candidate, "count %d" % count]
    for i, (severity, area, summary) in enumerate(findings, 1):
        n = i if indices is None else indices[i - 1]
        lines.append("[F%d] %s | %s | %s [/F%d]" % (n, severity, area,
                                                    summary, n))
    lines.append("%s nonce=%s count=%d" % (
        rf.END_PREFIX, end_nonce or nonce,
        count if end_count is None else end_count))
    return "\n".join(indent + line for line in lines)


def capture(*parts):
    """A reviewer capture: private prose and a canary around the parts."""
    return "\n".join(
        ["  I read the diff. " + CANARY,
         "  target_runtime/x.py | read before class | private | admit",
         "  ```", "  [F1] blocker | fake.py:1 | inside a code example [/F1]",
         "  ```"]
        + list(parts) + ["  HERD_DECISION: REJECT"])


def extract(text, the_route=None, task=TASK, rnd=1, logical=REVIEWER,
            agent=AGENT):
    return rf.extract_declaration(text, route() if the_route is None
                                  else the_route, task, rnd, logical, agent)


class EnvelopeTests(unittest.TestCase):

    def test_only_the_designated_envelope_becomes_findings(self):
        outcome = extract(capture(envelope()))
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED,
                         outcome["problem"])
        self.assertEqual(outcome["findings"], [{
            "index": 1, "severity": "blocker", "area": "target_runtime/x.py",
            "line": 12, "summary": "the read happens before the class check"}])
        self.assertNotIn(CANARY, json.dumps(outcome))
        self.assertNotIn("fake.py", json.dumps(outcome))
        self.assertNotIn("read before class", json.dumps(outcome))

    def test_a_valid_empty_declaration_is_empty_not_missing(self):
        outcome = extract(capture(envelope(findings=())))
        self.assertEqual((outcome["state"], outcome["findings"]),
                         (rf.OUTCOME_RECORDED, []))

    def test_the_bullet_and_indent_the_tui_adds_are_tolerated_only(self):
        text = envelope(indent="")
        bulleted = "• " + text.split("\n", 1)[0] + "\n" + "\n".join(
            "  " + line for line in text.split("\n")[1:])
        self.assertEqual(extract(bulleted)["state"], rf.OUTCOME_RECORDED)
        quoted = "\n".join("> " + line for line in text.split("\n"))
        self.assertEqual(extract(quoted)["problem"], rf.PROBLEM_MISSING)

    def test_terse_tail_prose_and_code_examples_never_become_findings(self):
        outcome = extract(capture())
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_NOT_RECORDED, rf.PROBLEM_MISSING))
        self.assertIsNone(outcome["findings"])

    # -- round 15 F1: a fenced EXAMPLE is never a declaration ---------------

    IN_EXAMPLE = "findings_declaration_only_in_example"

    def fenced(self, body, opener="```", closer="```", indent="  "):
        lines = [indent + opener, body]
        if closer is not None:
            lines.append(indent + closer)
        return "\n".join(lines)

    def test_a_fenced_current_envelope_is_an_example_never_a_declaration(self):
        """A WHOLE, valid, current-nonce envelope entirely inside a code
        fence is an example: never promoted, never refused as a broken
        declaration, and reported as distinct from no envelope at all."""
        for opener, closer in (("```", "```"), ("```text", "```"),
                               ("~~~", "~~~"), ("````", "````"),
                               ("```", "````")):
            for indent in ("  ", "", "\u2022 "):
                with self.subTest(opener=opener, closer=closer, indent=indent):
                    outcome = extract(capture(self.fenced(
                        envelope(), opener, closer, indent)))
                    self.assertEqual((outcome["state"], outcome["problem"]),
                                     (rf.OUTCOME_NOT_RECORDED, self.IN_EXAMPLE))
                    self.assertIsNone(outcome["findings"])

    def test_a_genuine_declaration_beside_a_fenced_example_is_the_one_kept(self):
        example = envelope(findings=(("blocker", "fake.py:1",
                                      "an example, not a finding"),))
        outcome = extract(capture(self.fenced(example), envelope()))
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED,
                         outcome["problem"])
        self.assertEqual([f["summary"] for f in outcome["findings"]],
                         ["the read happens before the class check"])
        self.assertNotIn("fake.py", json.dumps(outcome))
        self.assertNotIn(CANARY, json.dumps(outcome))

    def test_an_unclosed_fence_hides_everything_after_it(self):
        outcome = extract(capture(self.fenced(envelope(), closer=None)))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_NOT_RECORDED, self.IN_EXAMPLE))
        self.assertIsNone(outcome["findings"])

    def test_a_fence_opened_inside_an_envelope_breaks_it(self):
        lines = envelope().split("\n")
        broken = "\n".join(lines[:8] + ["  ```"] + lines[8:] + ["  ```"])
        outcome = extract(capture(broken))
        self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED)
        self.assertIsNone(outcome["findings"])

    def test_only_a_matching_fence_closes_one(self):
        """``~~~`` is not closed by backticks, nor ```````` by fewer."""
        for opener, wrong in (("~~~", "```"), ("````", "```")):
            with self.subTest(opener=opener):
                text = "\n".join(["  " + opener, "  " + wrong, envelope(),
                                  "  " + wrong])
                outcome = extract(capture(text))
                self.assertEqual((outcome["state"], outcome["problem"]),
                                 (rf.OUTCOME_NOT_RECORDED, self.IN_EXAMPLE))

    def test_an_envelope_mentioned_inside_a_sentence_is_not_a_line(self):
        text = capture("  start with %s then list them" % rf.BEGIN_LINE)
        self.assertEqual(extract(text)["problem"], rf.PROBLEM_MISSING)

    def test_duplicate_and_spoofed_envelopes(self):
        twice = capture(envelope(), envelope(findings=()))
        self.assertEqual(extract(twice)["problem"], rf.PROBLEM_AMBIGUOUS)
        # Another round's (or a forged) nonce is NOT the current route's.
        old = capture(envelope(nonce="0123456789abcdef"))
        outcome = extract(old)
        self.assertEqual(outcome["problem"], rf.PROBLEM_MISSING)
        self.assertEqual(outcome["foreign_envelopes"], 1)
        mixed = capture(envelope(nonce="0123456789abcdef"), envelope())
        self.assertEqual(extract(mixed)["state"], rf.OUTCOME_RECORDED)
        spliced = capture(envelope(end_nonce="0123456789abcdef"))
        self.assertEqual(extract(spliced)["problem"], rf.PROBLEM_INCOMPLETE)

    def test_a_dangling_end_marker_from_scrollback_poisons_the_read(self):
        """Observed live (round 15 transport proof): a stray END marker
        echoing the CURRENT nonce, with no BEGIN of its own (a prompt or
        scrollback fragment above the reviewer's envelope), is
        envelope-looking text that must never be promoted: the round is
        REFUSED as incomplete, and the envelope beside it is not used."""
        dangling = "  %s nonce=%s count=1" % (rf.END_PREFIX, NONCE)
        for text in (capture(dangling, envelope()),
                     capture(envelope(), dangling)):
            with self.subTest(text=text[-60:]):
                outcome = extract(text)
                self.assertEqual((outcome["state"], outcome["problem"]),
                                 (rf.OUTCOME_REFUSED, rf.PROBLEM_INCOMPLETE))
                self.assertIsNone(outcome["findings"])

    def test_incomplete_capture_fails_closed(self):
        text = envelope()
        lines = text.split("\n")
        no_end = "\n".join(lines[:-1])
        self.assertEqual(extract(no_end)["problem"], rf.PROBLEM_INCOMPLETE)
        no_begin = "\n".join(lines[3:])
        self.assertEqual(extract(no_begin)["problem"], rf.PROBLEM_INCOMPLETE)
        # A line cut by a capture window loses its closing tag.
        cut = text.replace(" [/F1]", "")
        self.assertEqual(extract(cut)["problem"], rf.PROBLEM_INCOMPLETE)
        nested = "\n".join(lines[:7] + [lines[0]] + lines[1:])
        self.assertEqual(extract(nested)["problem"], rf.PROBLEM_INCOMPLETE)

    def test_a_dropped_blocker_is_a_failure_never_a_shorter_list(self):
        two = (("blocker", "a.py:1", "first"), ("blocker", "b.py:2", "second"))
        dropped = envelope(findings=two[:1], count=2, end_count=2)
        self.assertEqual(extract(dropped)["problem"],
                         rf.PROBLEM_COUNT_MISMATCH)
        skipped = envelope(findings=two, indices=(1, 3))
        self.assertEqual(extract(skipped)["problem"],
                         rf.PROBLEM_COUNT_MISMATCH)
        disagree = envelope(findings=two, end_count=1)
        self.assertEqual(extract(disagree)["problem"],
                         rf.PROBLEM_COUNT_MISMATCH)

    def test_wrong_task_round_reviewer_or_candidate(self):
        for kwargs, problem in (
                ({"task": "20990101-000000-000000"}, rf.PROBLEM_WRONG_TASK),
                ({"rnd": 2}, rf.PROBLEM_WRONG_ROUND),
                ({"reviewer": "reviewer2"}, rf.PROBLEM_WRONG_REVIEWER),
                ({"candidate": OTHER_CANDIDATE}, rf.PROBLEM_WRONG_CANDIDATE)):
            with self.subTest(problem):
                self.assertEqual(extract(envelope(**kwargs))["problem"],
                                 problem)

    def test_the_route_is_checked_against_what_the_writer_knows(self):
        text = envelope()
        for the_route, kwargs, problem in (
                (route(round=2), {}, rf.PROBLEM_ROUTE_ROUND),
                (route(reviewer_logical="reviewer2"), {},
                 rf.PROBLEM_ROUTE_REVIEWER),
                (route(), {"agent": "someone-else"}, rf.PROBLEM_ROUTE_REVIEWER),
                (route(task_id="20990101-000000-000000"), {},
                 rf.PROBLEM_ROUTE_TASK),
                (route(nonce="short"), {}, rf.PROBLEM_ROUTE_MALFORMED),
                (dict(route(), extra=1), {}, rf.PROBLEM_ROUTE_MALFORMED)):
            with self.subTest(problem):
                outcome = extract(text, the_route, **kwargs)
                self.assertEqual((outcome["state"], outcome["problem"]),
                                 (rf.OUTCOME_REFUSED, problem))

    def test_malformed_lines_refuse(self):
        for bad in ("[F1] urgent | a.py:1 | bad severity [/F1]",
                    "[F1] blocker | /etc/passwd:1 | absolute [/F1]",
                    "[F1] blocker | ../up.py:1 | traversal [/F1]",
                    "[F1] blocker | a.py:0 | line zero [/F1]",
                    "[F1] blocker | a.py:1 |  [/F1]",
                    "[F1] blocker | a.py:1 | ctl \x07 char [/F1]",
                    "[F1] blocker | a.py:1 | bidi ‮ x [/F1]",
                    "[F1] blocker a.py:1 no separators [/F1]"):
            with self.subTest(bad):
                text = envelope().replace(
                    "[F1] blocker | target_runtime/x.py:12 | the read happens"
                    " before the class check [/F1]", bad)
                self.assertEqual(extract(text)["problem"],
                                 rf.PROBLEM_MALFORMED)
        missing_header = envelope().replace("  count 1\n", "")
        self.assertEqual(extract(missing_header)["problem"],
                         rf.PROBLEM_MALFORMED)

    def test_bounds_refuse_and_never_truncate(self):
        many = tuple(("non_blocking", "-", "f%d" % i)
                     for i in range(rf.MAX_FINDINGS + 1))
        self.assertEqual(extract(envelope(findings=many))["problem"],
                         rf.PROBLEM_OVER_BOUND)
        exact = tuple(("non_blocking", "-", "f%d" % i)
                      for i in range(rf.MAX_FINDINGS))
        self.assertEqual(extract(envelope(findings=exact))["state"],
                         rf.OUTCOME_RECORDED)
        long = (("blocker", "-", "x" * (rf.MAX_SUMMARY_CHARS + 1)),)
        self.assertEqual(extract(envelope(findings=long))["problem"],
                         rf.PROBLEM_OVER_BOUND)
        fits = (("blocker", "-", "x" * rf.MAX_SUMMARY_CHARS),)
        self.assertEqual(extract(envelope(findings=fits))["state"],
                         rf.OUTCOME_RECORDED)
        area = (("blocker", "a" * (rf.MAX_AREA_CHARS + 1) + ".py", "s"),)
        self.assertEqual(extract(envelope(findings=area))["problem"],
                         rf.PROBLEM_OVER_BOUND)
        # Aggregate: every summary within its own bound, the sum over it.
        per = rf.MAX_SUMMARY_CHARS
        n = rf.MAX_FINDINGS_AGGREGATE_BYTES // per + 1
        self.assertLessEqual(n, rf.MAX_FINDINGS)
        heavy = tuple(("non_blocking", "-", "y" * per) for _ in range(n))
        self.assertEqual(extract(envelope(findings=heavy))["problem"],
                         rf.PROBLEM_OVER_BOUND)
        huge = "x" * (rf.MAX_CAPTURE_CHARS + 1)
        self.assertEqual(extract(huge)["problem"],
                         rf.PROBLEM_CAPTURE_OVER_BOUND)

    def test_a_synthetic_narrow_window_fails_closed_never_truncates(self):
        """SYNTHETIC narrow-window test, not a transport measurement. The
        window (the last 22 lines, each cut to 37 characters) copies the
        shape of this task's 14 RECORDED canonical captures, which the Lead
        diagnosed as DEGRADED (read while the reviewer was not idle, so the
        read fell back to the narrow visible screen). It says nothing about
        what a supported full-native-idle capture carries. Asserted here:
        an envelope cut by such a window refuses the whole declaration and
        yields no findings; it is never truncated to a shorter list."""
        text = envelope()
        windowed = "\n".join(line[:37] for line in text.split("\n")[-22:])
        outcome = extract(windowed)
        self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED)
        self.assertIn(outcome["problem"], (rf.PROBLEM_INCOMPLETE,
                                           rf.PROBLEM_MALFORMED))
        self.assertIsNone(outcome["findings"])

    @staticmethod
    def wrapped(text, width):
        """SYNTHETIC row wrapping: each line cut at ``width``, continuation
        rows indented two spaces, the way a TUI soft-wraps a long line when
        a capture does NOT rejoin it."""
        rows = []
        for line in text.split("\n"):
            rows.append(line[:width])
            rest = line[width:]
            while rest:
                rows.append("  " + rest[:width - 2])
                rest = rest[width - 2:]
        return "\n".join(rows)

    def test_synthetic_row_wrapping_records_only_at_the_widest_line(self):
        """An extractor property under SYNTHETIC wrapping, not a transport
        measurement. Every width below the envelope's widest rendered line
        fails closed with no findings; from that width up the same findings
        as the unwrapped read are recorded. Below the width of the BEGIN
        line itself the marker is destroyed, so the round reads as NOT
        RECORDED (missing): a declaration cut that badly is not
        distinguishable from none, and never becomes findings."""
        text = envelope()
        widest = max(len(line) for line in text.split("\n"))
        begin = len("  " + rf.BEGIN_LINE)
        whole = extract(capture(text))
        self.assertEqual(whole["state"], rf.OUTCOME_RECORDED)
        for width in (20, begin - 1, begin, 37, 60, 76, widest - 1):
            outcome = extract(capture(self.wrapped(text, width)))
            self.assertIsNone(outcome["findings"], width)
            if width < begin:
                self.assertEqual(
                    (outcome["state"], outcome["problem"]),
                    (rf.OUTCOME_NOT_RECORDED, rf.PROBLEM_MISSING), width)
            else:
                self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED, width)
        for width in (widest, 120, rf.MAX_ENVELOPE_LINE_CHARS):
            outcome = extract(capture(self.wrapped(text, width)))
            self.assertEqual(outcome["findings"], whole["findings"], width)

    def test_a_synthetic_unwrapped_500_line_capture_carries_a_declaration(self):
        """SYNTHETIC model of the read the candidate producer requests
        (``recent-unwrapped`` with ``--lines 500``), ASSUMING it delivers
        whole logical lines. Whether a live read at full native idle does so
        is not measured here; that is the later live-integration proof."""
        prose = ["  " + ("long prose line %03d " % i) * 15 for i in range(480)]
        text = "\n".join(prose + [envelope(), "  HERD_DECISION: REJECT"])
        lines = text.split("\n")
        self.assertLessEqual(len(lines), 500)
        self.assertLess(len(text), rf.MAX_CAPTURE_CHARS)
        self.assertGreater(max(len(line) for line in lines), 76)
        outcome = extract(text)
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED,
                         outcome["problem"])
        self.assertEqual(len(outcome["findings"]), 1)


def compact(findings=(("blocker |", "target_runtime/x.py:12 |",
                        "the read happens before the class check"),),
            nonce=NONCE, task=TASK, rnd=1, reviewer=REVIEWER, count=None,
            end_count=None, parts=None, end_nonce=None, indent="  ",
            numbering=None):
    """A COMPACT (v2) envelope: each finding a tuple of PART texts, numbered
    ``[F<i> <k>/<n>]``; ``numbering`` overrides the (i, k, n) per part line."""
    count = len(findings) if count is None else count
    body = []
    for i, texts in enumerate(findings, 1):
        for k, text in enumerate(texts, 1):
            body.append((i, k, len(texts), text))
    if numbering is not None:
        body = [(i, k, n, t) for (i, k, n), (_, _, _, t) in zip(numbering, body)]
    lines = ["HERD_FINDINGS_BEGIN v2", "nonce %s" % nonce, "task %s" % task,
             "round %d" % rnd, "reviewer %s" % reviewer, "count %d" % count]
    lines += ["[F%d %d/%d] %s" % part for part in body]
    lines.append("HERD_FINDINGS_END v2 nonce=%s count=%d parts=%d" % (
        end_nonce or nonce, count if end_count is None else end_count,
        len(body) if parts is None else parts))
    return "\n".join(indent + line for line in lines)


#: Round 15's four findings (all BLOCKER, as the Lead recovered them), written
#: the compact way with this Mission's paths. The summaries are paraphrased.
ROUND15 = (
    ("blocker | target_runtime/review_findings.py:235 |",
     "a fenced example envelope carrying the current nonce is promoted",
     "as a real declaration; fenced regions must be excluded"),
    ("blocker |",
     "integrations/pixel-agents/webview-ui/src/components/diOfficeFormat.ts:228",
     "| disconnect or expiry leaves evidence current and content visible"),
    ("blocker | target_runtime/review_record.py:249 |",
     "a findings state of [] or {} raises TypeError and drops the review"),
    ("blocker | integrations/pixel-agents/server/src/di/catalogProjection.ts:1108",
     "| 64 recorded artifact links are silently sliced to 32"),
)


class CompactEnvelopeTests(unittest.TestCase):
    """The compact (v2) declaration (round 15 decision): lines that fit the
    MEASURED supported capture, no wire candidate (the stored route pins
    it; the echoed nonce binds), explicit numbered parts joined by exactly
    one space, and every order / count / completeness check fail-closed."""

    def test_a_compact_nonempty_declaration_is_recorded_exactly(self):
        text = compact(findings=(
            ("blocker | target_runtime/x.py:12 |", "the read happens",
             "before the class check"),
            ("non_blocking | - | one part only",)))
        outcome = extract(capture(text))
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED,
                         outcome["problem"])
        self.assertEqual(outcome["findings"], [
            {"index": 1, "severity": "blocker", "area": "target_runtime/x.py",
             "line": 12, "summary": "the read happens before the class check"},
            {"index": 2, "severity": "non_blocking", "area": None,
             "line": None, "summary": "one part only"}])
        self.assertNotIn(CANARY, json.dumps(outcome))

    def test_a_compact_empty_declaration_is_empty_not_missing(self):
        outcome = extract(capture(compact(findings=())))
        self.assertEqual((outcome["state"], outcome["findings"]),
                         (rf.OUTCOME_RECORDED, []))

    def test_this_missions_declaration_fits_the_measured_width(self):
        """The Lead's measurement (zoomed supported capture: 44 lines, at
        most 95 characters, a finding line wrapping at ~93 effective
        characters including the 2-character decoration). Every emitted line
        of round 15's four findings, with this Mission's real task id,
        reviewer and round, stays within it; the whole envelope is far
        below 44 lines; it round-trips exactly."""
        text = compact(findings=ROUND15, task="20261017-133442-1bfec8",
                       rnd=16, reviewer="reviewer1")
        lines = text.split("\n")
        self.assertLessEqual(max(len(line) for line in lines), 2 + 88)
        self.assertLessEqual(max(len(line) for line in lines), 93)
        self.assertLessEqual(len(lines), 20)
        outcome = rf.extract_declaration(
            capture(text), route(task_id="20261017-133442-1bfec8", round=16,
                                 reviewer_logical="reviewer1"),
            "20261017-133442-1bfec8", 16, "reviewer1", AGENT)
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED,
                         outcome["problem"])
        self.assertEqual(len(outcome["findings"]), 4)
        self.assertEqual(outcome["findings"][1]["area"], (
            "integrations/pixel-agents/webview-ui/src/components/"
            "diOfficeFormat.ts"))
        self.assertEqual(outcome["findings"][3]["summary"],
                         "64 recorded artifact links are silently sliced to 32")

    def test_a_line_longer_than_the_compact_bound_refuses(self):
        long_part = "x" * (88 - len("[F1 2/2] ") + 1)
        outcome = extract(capture(compact(findings=(
            ("blocker | - |", long_part),))))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_OVER_BOUND))

    def test_missing_duplicate_reordered_or_miscounted_parts_fail_closed(self):
        two = (("blocker | - |", "first half", "second half"),)
        cases = {
            "missing part": dict(findings=(("blocker | - |", "first half"),),
                                 numbering=[(1, 1, 3), (1, 2, 3)]),
            "duplicate part": dict(findings=two,
                                   numbering=[(1, 1, 3), (1, 2, 3), (1, 2, 3)]),
            "reordered parts": dict(findings=two,
                                    numbering=[(1, 1, 3), (1, 3, 3), (1, 2, 3)]),
            "inconsistent total": dict(findings=two,
                                       numbering=[(1, 1, 3), (1, 2, 2), (1, 3, 3)]),
            "end parts disagree": dict(findings=two, parts=4),
            "end count disagrees": dict(findings=two, end_count=2),
            "header count disagrees": dict(findings=two, count=2),
            "a finding beyond the declared count": dict(
                findings=(("blocker | - | a",), ("blocker | - | b",)),
                count=1),
            "findings out of order": dict(
                findings=(("blocker | - | a",), ("blocker | - | b",)),
                numbering=[(2, 1, 1), (1, 1, 1)]),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                outcome = extract(capture(compact(**kw)))
                self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED, name)
                self.assertIn(outcome["problem"], (rf.PROBLEM_COUNT_MISMATCH,
                                                   rf.PROBLEM_MALFORMED), name)
                self.assertIsNone(outcome["findings"])

    def test_a_soft_wrapped_part_is_never_stitched(self):
        """The live failure mode: a part line wrapped onto a second physical
        line. The continuation is not a part line, so the declaration is
        REFUSED; nothing is rejoined by guesswork."""
        lines = compact(findings=ROUND15[:1]).split("\n")
        part = lines[7]
        wrapped = lines[:7] + [part[:40], "  " + part[40:]] + lines[8:]
        outcome = extract(capture("\n".join(wrapped)))
        self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED)
        self.assertIsNone(outcome["findings"])

    def test_a_path_split_across_parts_is_malformed(self):
        outcome = extract(capture(compact(findings=(
            ("blocker | target_runtime/", "x.py:12 | split path"),))))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_MALFORMED))

    def test_part_and_count_bounds_refuse(self):
        nine = (("blocker | - |",) + ("w",) * 8,)
        outcome = extract(capture(compact(findings=nine)))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_OVER_BOUND))
        many = tuple(("non_blocking | - | f%d" % i,) for i in range(33))
        outcome = extract(capture(compact(findings=many)))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_OVER_BOUND))

    def test_wrong_task_round_or_reviewer_refuse(self):
        for kw, problem in ((dict(task="other-task"), rf.PROBLEM_WRONG_TASK),
                            (dict(rnd=2), rf.PROBLEM_WRONG_ROUND),
                            (dict(reviewer="reviewer2"),
                             rf.PROBLEM_WRONG_REVIEWER)):
            with self.subTest(problem):
                outcome = extract(capture(compact(**kw)))
                self.assertEqual((outcome["state"], outcome["problem"]),
                                 (rf.OUTCOME_REFUSED, problem))

    def test_another_nonce_or_a_spliced_end_never_counts(self):
        outcome = extract(capture(compact(nonce="0123456789abcdef")))
        self.assertEqual((outcome["state"], outcome["problem"],
                          outcome["foreign_envelopes"]),
                         (rf.OUTCOME_NOT_RECORDED, rf.PROBLEM_MISSING, 1))
        outcome = extract(capture(compact(end_nonce="0123456789abcdef")))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_INCOMPLETE))

    def test_an_end_of_the_other_version_refuses(self):
        lines = compact().split("\n")
        lines[-1] = "  HERD_FINDINGS_END v1 nonce=%s count=1" % NONCE
        outcome = extract(capture("\n".join(lines)))
        self.assertEqual(outcome["state"], rf.OUTCOME_REFUSED)
        self.assertIsNone(outcome["findings"])

    def test_a_compact_and_a_long_envelope_for_one_route_are_ambiguous(self):
        outcome = extract(capture(compact(), envelope()))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_AMBIGUOUS))

    def test_a_fenced_compact_example_is_never_a_declaration(self):
        outcome = extract(capture("  ```\n" + compact() + "\n  ```"))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_NOT_RECORDED,
                          "findings_declaration_only_in_example"))
        outcome = extract(capture("  ```\n" + compact(findings=(
            ("blocker | - | an example",),)) + "\n  ```", compact()))
        self.assertEqual(outcome["state"], rf.OUTCOME_RECORDED)
        self.assertEqual(outcome["findings"][0]["summary"],
                         "the read happens before the class check")

    def test_a_dangling_compact_end_poisons_the_read(self):
        dangling = "  HERD_FINDINGS_END v2 nonce=%s count=1 parts=1" % NONCE
        outcome = extract(capture(dangling, compact()))
        self.assertEqual((outcome["state"], outcome["problem"]),
                         (rf.OUTCOME_REFUSED, rf.PROBLEM_INCOMPLETE))


class RecordTests(unittest.TestCase):

    def record(self, findings=None):
        return rf.build_record(route(), TASK, 1, REVIEWER, AGENT,
                               b"preamble\n", findings or [])

    def test_the_record_round_trips_and_states_its_ceiling(self):
        record = self.record([{"index": 1, "severity": "blocker",
                               "area": "a.py", "line": 3, "summary": "s"}])
        data = rf.encode_record(record)
        self.assertLessEqual(len(data), rf.MAX_RECORD_BYTES)
        self.assertEqual(rf.validate_record(json.loads(data)), record)
        self.assertEqual(record["candidate_sha256"], CANDIDATE)
        self.assertEqual(record["preamble_sha256"],
                         hashlib.sha256(b"preamble\n").hexdigest())
        for words in ("not a cryptographic", "not a verdict",
                      "not approval"):
            self.assertIn(words, record["provenance"])

    def test_the_validator_is_closed(self):
        good = self.record()
        for edit in (lambda r: r.update(extra=1),
                     lambda r: r.update(count=1),
                     lambda r: r.update(round=0),
                     lambda r: r.update(candidate_sha256="nothex"),
                     lambda r: r.update(provenance="anything else"),
                     lambda r: r["findings"].append(
                         {"index": 1, "severity": "blocker", "area": None,
                          "line": None, "summary": "s", "x": 1})):
            bad = json.loads(json.dumps(good))
            edit(bad)
            with self.assertRaises(rf.RecordRefused):
                rf.validate_record(bad)


# -- the LOCAL candidate writer (this repository's herdctl.py) ---------------

class HerdRoot(unittest.TestCase):
    """An ISOLATED herd root with an ACTIVE task; this repository's
    ``herdctl.py`` loaded in isolation with the transcript reader and the
    role resolution stubbed (the installed producer is never touched)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(self.root, ".herd", "state")
        os.makedirs(self.state)
        self.save({"id": TASK, "status": "ACTIVE", "review_rounds": 0})
        spec = importlib.util.spec_from_file_location(
            "herdctl_review_findings_under_test", REPO_ROOT / "herdctl.py")
        self.herdctl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.herdctl)
        self.herdctl.resolve_repo_ref = lambda ref: Path(self.root)
        self.herdctl.resolve_role = lambda repo, name: {
            REVIEWER: AGENT, "reviewer2": "pod-reviewer2"}[name]
        self.captured = ""
        self.herdctl.reviewer_transcript = lambda agent, lines=500: (
            self.captured)

    def save(self, task):
        with open(os.path.join(self.state, "task.json"), "w") as handle:
            json.dump(task, handle)

    def task(self):
        with open(os.path.join(self.state, "task.json")) as handle:
            return json.load(handle)

    def route_cmd(self, candidate=CANDIDATE, reviewer=REVIEWER):
        args = types.SimpleNamespace(repo=self.root, reviewer=reviewer,
                                     candidate_sha256=candidate)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.herdctl.review_findings_route_cmd(args)
        return json.loads(out.getvalue())

    def decide(self, transcript, reviewer=REVIEWER):
        self.captured = transcript
        args = types.SimpleNamespace(repo=self.root, reviewer=reviewer,
                                     lines=500)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.herdctl.review_decision_cmd(args)
        return json.loads(out.getvalue())

    def round_file(self, n=1):
        return os.path.join(self.state, "reviews",
                            "%s-round-%02d.md" % (TASK, n))

    def findings_file(self, n=1):
        return os.path.join(self.state, "reviews",
                            "%s-round-%02d.findings.json" % (TASK, n))

    def declared(self, nonce, findings=(("blocker", "a.py:1", "public"),),
                 **kw):
        return capture(envelope(findings=findings, nonce=nonce, **kw))


class WriterTests(HerdRoot):

    def test_route_then_decision_records_the_declared_findings(self):
        routed = self.route_cmd()
        self.assertEqual((routed["round"], routed["reviewer_logical"],
                          routed["candidate_sha256"]), (1, REVIEWER, CANDIDATE))
        result = self.decide(self.declared(routed["nonce"]))
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["findings"], {"state": rf.OUTCOME_RECORDED,
                                              "problem": None, "count": 1})
        reference = self.task()["last_review_findings"]
        self.assertEqual(reference["file"],
                         os.path.basename(self.findings_file()))
        with open(self.findings_file(), "rb") as handle:
            data = handle.read()
        self.assertEqual(reference["sha256"], hashlib.sha256(data).hexdigest())
        record = rf.validate_record(json.loads(data))
        self.assertEqual(record["findings"][0]["summary"], "public")
        self.assertNotIn(CANARY, data.decode())
        self.assertNotIn("review_findings_route", self.task(), "single use")
        mode = stat.S_IMODE(os.stat(self.findings_file()).st_mode)
        self.assertFalse(mode & 0o022)

    def test_verdict_semantics_are_byte_identical_with_or_without(self):
        """The round file and the decision fields do not depend on, and are
        not altered by, the findings metadata."""
        outcomes = {}
        for label, transcript, routed in (
                ("plain", capture(), False),
                ("declared", None, True),
                ("garbage", capture("  HERD_FINDINGS_BEGIN v1", "  nonce zz"),
                 True)):
            with self.subTest(label):
                self.setUp()
                if routed:
                    nonce = self.route_cmd()["nonce"]
                    transcript = transcript or self.declared(nonce)
                result = self.decide(transcript)
                with open(self.round_file(), "rb") as handle:
                    round_bytes = handle.read()
                task = self.task()
                outcomes[label] = (
                    round_bytes.replace(transcript.encode(), b"<T>"),
                    result["decision"], result["raw_token"], result["valid"],
                    task["review_rounds"], task["last_review_decision"],
                    task["last_review_file"].replace(self.root, "<R>"))
        self.assertEqual(outcomes["plain"], outcomes["declared"])
        self.assertEqual(outcomes["plain"], outcomes["garbage"])

    def test_a_fenced_example_is_never_persisted_by_the_writer(self):
        """Round 15 F1 at the REAL local writer: route, then decide over a
        capture whose only current envelope is a fenced example."""
        nonce = self.route_cmd()["nonce"]
        example = envelope(nonce=nonce)
        result = self.decide(capture("  ```\n" + example + "\n  ```"))
        self.assertEqual(result["decision"], "REJECT", "verdict untouched")
        self.assertEqual(result["findings"], {
            "state": rf.OUTCOME_NOT_RECORDED,
            "problem": "findings_declaration_only_in_example", "count": None})
        self.assertFalse(os.path.exists(self.findings_file()))
        reference = self.task()["last_review_findings"]
        self.assertEqual((reference["state"], reference["file"]),
                         (rf.OUTCOME_NOT_RECORDED, None))
        block = review_record.canonical_review(self.root, self.task(), TASK)
        self.assertEqual(block["verdict"], "REJECT")
        self.assertEqual(
            (block["findings"]["state"], block["findings"]["problem"]),
            (v2.FINDINGS_NOT_RECORDED, "findings_declaration_only_in_example"))

    def test_the_writer_records_a_compact_declaration_exactly(self):
        """The COMPACT format through the REAL local writer: route, decide,
        persist; the reader returns exactly the declared findings, and the
        record's candidate is the ROUTE's (the wire carries none)."""
        nonce = self.route_cmd()["nonce"]
        text = compact(findings=ROUND15, nonce=nonce)
        result = self.decide(capture(text))
        self.assertEqual(result["decision"], "REJECT", "verdict untouched")
        self.assertEqual(result["findings"], {
            "state": rf.OUTCOME_RECORDED, "problem": None, "count": 4})
        with open(self.findings_file(), "rb") as handle:
            record = rf.validate_record(json.loads(handle.read()))
        self.assertEqual(record["candidate_sha256"], CANDIDATE)
        self.assertNotIn(CANDIDATE, text, "no candidate on the wire")
        self.assertEqual([f["severity"] for f in record["findings"]],
                         ["blocker"] * 4)
        self.assertEqual(record["findings"][0]["summary"], (
            "a fenced example envelope carrying the current nonce is promoted"
            " as a real declaration; fenced regions must be excluded"))
        block = review_record.canonical_review(self.root, self.task(), TASK)
        self.assertEqual(block["verdict"], "REJECT")
        self.assertEqual(block["findings"]["state"], v2.FINDINGS_PRESENT)
        self.assertEqual([f["summary"] for f in block["findings"]["items"]],
                         [f["summary"] for f in record["findings"]])
        self.assertNotIn(CANARY, json.dumps(block))

    def test_no_route_records_nothing_and_never_looks(self):
        with mock.patch.object(rf, "extract_declaration",
                               side_effect=AssertionError("looked")):
            result = self.decide(self.declared(NONCE))
        self.assertNotIn("findings", result)
        self.assertNotIn("last_review_findings", self.task())
        self.assertFalse(os.path.exists(self.findings_file()))

    def test_metadata_failures_are_recorded_as_such(self):
        for name, build, problem in (
                ("missing", lambda n: capture(), rf.PROBLEM_MISSING),
                ("spoofed", lambda n: self.declared("0123456789abcdef"),
                 rf.PROBLEM_MISSING),
                ("duplicate", lambda n: capture(
                    envelope(nonce=n), envelope(nonce=n, findings=())),
                 rf.PROBLEM_AMBIGUOUS),
                ("wrong candidate", lambda n: self.declared(
                    n, candidate=OTHER_CANDIDATE), rf.PROBLEM_WRONG_CANDIDATE),
                ("wrong round", lambda n: self.declared(n, rnd=2),
                 rf.PROBLEM_WRONG_ROUND),
                ("wrong reviewer", lambda n: self.declared(
                    n, reviewer="reviewer2"), rf.PROBLEM_WRONG_REVIEWER)):
            with self.subTest(name):
                self.setUp()
                nonce = self.route_cmd()["nonce"]
                result = self.decide(build(nonce))
                reference = self.task()["last_review_findings"]
                self.assertEqual((reference["problem"], reference["file"]),
                                 (problem, None))
                self.assertEqual(result["findings"]["problem"], problem)
                self.assertFalse(os.path.exists(self.findings_file()))

    def test_a_route_for_another_reviewer_fails_closed(self):
        nonce = self.route_cmd(reviewer="reviewer2")["nonce"]
        self.decide(self.declared(nonce), reviewer=REVIEWER)
        self.assertEqual(self.task()["last_review_findings"]["problem"],
                         rf.PROBLEM_ROUTE_REVIEWER)

    def test_a_stale_reference_is_replaced_not_left_pointing_back(self):
        nonce = self.route_cmd()["nonce"]
        self.decide(self.declared(nonce))
        self.decide(capture())             # round 2: no route
        reference = self.task()["last_review_findings"]
        self.assertEqual((reference["state"], reference["problem"],
                          reference["round"]),
                         (rf.OUTCOME_NOT_RECORDED, rf.PROBLEM_NO_ROUTE, 2))

    def test_the_route_requires_an_active_task_and_a_digest(self):
        with self.assertRaises(SystemExit):
            self.route_cmd(candidate="not-a-digest")
        self.save({"id": TASK, "status": "COMPLETE", "review_rounds": 0})
        with self.assertRaises(SystemExit):
            self.route_cmd()


# -- the READER (target_runtime.review_record) --------------------------------

class ReaderTests(HerdRoot):

    def review(self, transcript=None, routed=True, **kw):
        if routed:
            nonce = self.route_cmd()["nonce"]
            transcript = transcript or self.declared(nonce, **kw)
        self.decide(transcript or capture())
        return review_record.canonical_review(self.root, self.task(), TASK)

    def test_present_findings_are_read_from_the_record_alone(self):
        opened = []
        real_open = os.open

        def spy(path, *args, **kwargs):
            opened.append(path)
            return real_open(path, *args, **kwargs)
        nonce = self.route_cmd()["nonce"]
        self.decide(self.declared(nonce))
        with mock.patch.object(os, "open", spy):
            block = review_record.canonical_review(self.root, self.task(),
                                                   TASK)
        v2.validate_review(block, "review")
        findings = block["findings"]
        self.assertEqual(findings["state"], v2.FINDINGS_PRESENT)
        self.assertEqual(findings["items"][0]["summary"], "public")
        self.assertEqual(findings["candidate_sha256"], CANDIDATE)
        self.assertNotIn(CANARY, json.dumps(block))
        self.assertIn(os.path.basename(self.findings_file()), opened)

    def test_the_transcript_is_never_read_for_findings(self):
        """Every byte read from the ROUND file is preamble (the marker line
        stops the read), whatever the transcript holds."""
        nonce = self.route_cmd()["nonce"]
        self.decide(self.declared(nonce))
        reads = []
        real_read = os.read

        def spy(fd, n):
            data = real_read(fd, n)
            reads.append(data)
            return data
        with mock.patch.object(os, "read", spy):
            block = review_record.canonical_review(self.root, self.task(),
                                                   TASK)
        joined = b"".join(reads)
        self.assertNotIn(CANARY.encode(), joined)
        self.assertNotIn(b"HERD_DECISION", joined)
        self.assertEqual(block["findings"]["state"], v2.FINDINGS_PRESENT)

    def test_valid_empty_is_empty(self):
        block = self.review(findings=())
        self.assertEqual((block["findings"]["state"],
                          block["findings"]["items"]),
                         (v2.FINDINGS_EMPTY, []))

    def test_a_legacy_record_reads_as_legacy_not_recorded(self):
        """Written by the writer BEFORE this field existed: no reference."""
        self.decide(capture())
        task = self.task()
        self.assertNotIn("last_review_findings", task)
        block = review_record.canonical_review(self.root, task, TASK)
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED)
        self.assertEqual((block["findings"]["state"],
                          block["findings"]["problem"]),
                         (v2.FINDINGS_NOT_RECORDED, v2.PROBLEM_FINDINGS_LEGACY))
        v2.validate_review(block, "review")

    def test_writer_failures_read_as_not_recorded_or_refused(self):
        missing = self.review(transcript=capture())
        self.assertEqual((missing["findings"]["state"],
                          missing["findings"]["problem"]),
                         (v2.FINDINGS_NOT_RECORDED, rf.PROBLEM_MISSING))
        self.setUp()
        nonce = self.route_cmd()["nonce"]
        self.decide(capture(envelope(nonce=nonce), envelope(nonce=nonce)))
        refused = review_record.canonical_review(self.root, self.task(), TASK)
        self.assertEqual((refused["findings"]["state"],
                          refused["findings"]["problem"]),
                         (v2.FINDINGS_REFUSED, rf.PROBLEM_AMBIGUOUS))
        for block in (missing, refused):
            v2.validate_review(block, "review")
            self.assertEqual(block["state"], v2.REVIEW_ADMITTED)

    def tamper(self, edit):
        self.review()
        edit()
        return review_record.canonical_review(self.root, self.task(), TASK)

    def assert_refused(self, block, problem):
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED, "verdict kept")
        self.assertEqual((block["findings"]["state"],
                          block["findings"]["problem"]),
                         (v2.FINDINGS_REFUSED, problem))
        self.assertEqual(block["findings"]["items"], [])
        v2.validate_review(block, "review")

    def rewrite_record(self, change):
        path = self.findings_file()
        with open(path) as handle:
            record = json.load(handle)
        change(record)
        data = rf.encode_record_unchecked(record)
        with open(path, "wb") as handle:
            handle.write(data)
        task = self.task()
        task["last_review_findings"]["sha256"] = hashlib.sha256(
            data).hexdigest()
        self.save(task)

    def test_a_record_that_changed_is_refused_by_digest(self):
        def edit():
            with open(self.findings_file(), "ab") as handle:
                handle.write(b" ")
        self.assert_refused(self.tamper(edit),
                            v2.PROBLEM_FINDINGS_DIGEST_MISMATCH)

    def test_mismatched_round_reviewer_and_preamble_are_refused(self):
        for change, problem in (
                (lambda r: r.update(round=2), v2.PROBLEM_FINDINGS_ROUND),
                (lambda r: r.update(reviewer_agent="pod-other"),
                 v2.PROBLEM_FINDINGS_REVIEWER),
                (lambda r: r.update(task_id="20990101-000000-000000"),
                 v2.PROBLEM_FINDINGS_TASK),
                (lambda r: r.update(preamble_sha256="0" * 64),
                 v2.PROBLEM_FINDINGS_PREAMBLE)):
            with self.subTest(problem):
                self.setUp()
                self.assert_refused(self.tamper(
                    lambda: self.rewrite_record(change)), problem)

    def test_a_malformed_or_over_bound_record_is_refused(self):
        def over():
            path = self.findings_file()
            with open(path, "wb") as handle:
                handle.write(b" " * (rf.MAX_RECORD_BYTES + 1))
            task = self.task()
            with open(path, "rb") as handle:
                task["last_review_findings"]["sha256"] = hashlib.sha256(
                    handle.read()).hexdigest()
            self.save(task)
        self.assert_refused(self.tamper(over), v2.PROBLEM_FINDINGS_OVER_BOUND)
        self.setUp()
        self.assert_refused(self.tamper(lambda: self.rewrite_record(
            lambda r: r.update(extra=1))), v2.PROBLEM_FINDINGS_SHAPE)

    def test_a_linked_or_writable_record_is_refused(self):
        def symlinked():
            os.rename(self.findings_file(), self.findings_file() + ".real")
            os.symlink(self.findings_file() + ".real", self.findings_file())
        self.assert_refused(self.tamper(symlinked),
                            v2.PROBLEM_FINDINGS_NOT_CONTAINED)
        self.setUp()
        self.assert_refused(self.tamper(lambda: os.chmod(
            self.findings_file(), 0o666)), v2.PROBLEM_FINDINGS_WRITABLE)
        self.setUp()
        self.assert_refused(self.tamper(lambda: os.link(
            self.findings_file(), self.findings_file() + ".twin")),
            v2.PROBLEM_FINDINGS_HARDLINK)

    def test_a_malformed_reference_is_refused_never_followed(self):
        for reference in ({"state": "recorded"},
                          {"state": "recorded", "problem": None,
                           "file": "../../etc/passwd", "sha256": "0" * 64,
                           "count": 1, "round": 1},
                          {"state": "made_up", "problem": None, "file": None,
                           "sha256": None, "count": None, "round": 1}):
            with self.subTest(reference):
                self.setUp()
                block = self.tamper(lambda: self.save(dict(
                    self.task(), last_review_findings=reference)))
                self.assert_refused(block, v2.PROBLEM_FINDINGS_REFERENCE)

    def test_unhashable_reference_values_refuse_and_never_raise(self):
        """Round 15 F3: a reference whose ``state`` (or any field) arrived
        as ``[]`` or ``{}`` once raised ``TypeError: unhashable type`` out of
        the reader, dropping the whole review. Every field, both shapes: the
        verdict and preamble stay admitted and findings are REFUSED."""
        for key in review_record._REFERENCE_KEYS:
            for odd in ([], {}):
                with self.subTest(key=key, value=odd):
                    self.setUp()

                    def edit():
                        task = self.task()
                        task["last_review_findings"][key] = odd
                        self.save(task)
                    block = self.tamper(edit)
                    self.assert_refused(block, v2.PROBLEM_FINDINGS_REFERENCE)
                    self.assertEqual(block["verdict"], "REJECT")
                    self.assertEqual(block["record"]["round"], 1)

    def test_a_reference_for_another_round_is_refused(self):
        def bump():
            task = self.task()
            task["last_review_findings"]["round"] = 7
            self.save(task)
        self.assert_refused(self.tamper(bump), v2.PROBLEM_FINDINGS_ROUND)

    def test_findings_never_change_the_verdict_the_reader_reports(self):
        with_findings = self.review()
        self.setUp()
        self.decide(capture())
        without = review_record.canonical_review(self.root, self.task(), TASK)
        for key in ("state", "problem", "record", "gate", "verdict",
                    "currency", "task_status", "integrity"):
            self.assertEqual(with_findings[key], without[key], key)


class CaptureBudgetTests(unittest.TestCase):
    """The capture's boundary child returns ONE JSON value read under
    ``herdr.runtime.DIAGNOSTIC_READ_MAX_BYTES``: declared findings must never
    push the whole capture over it. Present findings degrade (last root
    first) to an honest refusal until the value fits; nothing is cut."""

    def value(self, roots, items_per_root, summary="s" * 200):
        from target_runtime import runtime_snapshot as rs
        findings = {"state": v2.FINDINGS_PRESENT, "problem": None,
                    "count": items_per_root,
                    "items": [{"index": i, "severity": "blocker",
                               "area": None, "line": None,
                               "summary": summary}
                              for i in range(1, items_per_root + 1)],
                    "candidate_sha256": CANDIDATE, "routed_at": 1,
                    "linkage": v2.FINDINGS_LINKAGE,
                    "provenance": v2.FINDINGS_PROVENANCE}
        return rs, {"snapshot": {}, "chain_observation": {"roots": [
            {"root": "/r%d" % n, "review": dict(
                review_record.block(v2.REVIEW_ADMITTED),
                findings=json.loads(json.dumps(findings)))}
            for n in range(roots)]}}

    def size(self, rs, value):
        return len(json.dumps({"ok": True, "value": value}, sort_keys=True,
                              separators=(",", ":")))

    def test_findings_that_fit_are_untouched(self):
        rs, value = self.value(1, 4)
        before = json.dumps(value, sort_keys=True)
        rs._fit_findings(value)
        self.assertEqual(json.dumps(value, sort_keys=True), before)

    def test_over_the_cap_the_last_roots_degrade_until_it_fits(self):
        rs, value = self.value(16, 32)
        self.assertGreater(self.size(rs, value), rs.MAX_FINDINGS_OUTPUT_BYTES)
        rs._fit_findings(value)
        self.assertLessEqual(self.size(rs, value), rs.MAX_FINDINGS_OUTPUT_BYTES)
        states = [r["review"]["findings"]["state"]
                  for r in value["chain_observation"]["roots"]]
        kept = states.index(v2.FINDINGS_REFUSED)
        self.assertGreater(kept, 0)
        self.assertEqual(set(states[:kept]), {v2.FINDINGS_PRESENT})
        for root in value["chain_observation"]["roots"][kept:]:
            findings = root["review"]["findings"]
            self.assertEqual((findings["state"], findings["problem"],
                              findings["items"]),
                             (v2.FINDINGS_REFUSED,
                              v2.PROBLEM_FINDINGS_CAPTURE_BUDGET, []))
            v2.validate_findings(findings, "findings")
        for root in value["chain_observation"]["roots"][:kept]:
            self.assertEqual(len(root["review"]["findings"]["items"]), 32,
                             "a kept block is never cut")

    def test_the_projection_fits_its_findings_before_returning(self):
        import ast
        import inspect
        from target_runtime import runtime_snapshot as rs
        tree = ast.parse(inspect.getsource(rs._project_chains))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "_fit_findings"]
        self.assertEqual(len(calls), 1)
        returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
        self.assertTrue(all(r.lineno > calls[0].lineno for r in returns
                            if r.value is not None and getattr(
                                r.value, "id", None) == "out"))

    def test_the_cap_is_the_engine_read_cap(self):
        from herdr import runtime as engine_runtime
        from target_runtime import runtime_snapshot as rs
        self.assertLess(rs.MAX_FINDINGS_OUTPUT_BYTES,
                        engine_runtime.DIAGNOSTIC_READ_MAX_BYTES)


class ContractParityTests(unittest.TestCase):

    def test_the_contract_mirrors_the_writer_vocabulary(self):
        """``mission_sources`` does not import ``target_runtime`` (the
        dependency runs the other way): its re-declared
        vocabulary is pinned equal here."""
        self.assertEqual(v2.FINDINGS_WRITER_PROBLEMS, rf.WRITER_PROBLEMS)
        self.assertEqual(v2.FINDINGS_NOT_RECORDED_PROBLEMS,
                         rf.NOT_RECORDED_PROBLEMS)
        self.assertEqual(v2.FINDINGS_SEVERITIES, rf.SEVERITIES)
        self.assertEqual(v2.MAX_FINDINGS, rf.MAX_FINDINGS)
        self.assertEqual(v2.MAX_FINDING_SUMMARY_CHARS, rf.MAX_SUMMARY_CHARS)
        self.assertEqual(v2.MAX_FINDING_AREA_CHARS, rf.MAX_AREA_CHARS)
        self.assertEqual(v2.FINDINGS_PROVENANCE, rf.PROVENANCE)
        self.assertEqual(v2.FINDINGS_LINKAGE, rf.LINKAGE)
        self.assertEqual(v2.FINDINGS_RECORD_KEYS, rf.RECORD_KEYS)
        self.assertEqual(review_record._REFERENCE_KEYS, rf.REFERENCE_KEYS)
        self.assertEqual(v2.FINDING_ITEM_KEYS, rf.FINDING_KEYS)
        self.assertEqual((v2.FINDINGS_RECORD_SCHEMA, v2.FINDINGS_RECORD_VERSION),
                         (rf.RECORD_SCHEMA, rf.CONTRACT_VERSION))
        self.assertEqual((v2.MAX_FINDING_LINE_NUMBER,
                          v2.MAX_FINDINGS_AGGREGATE_BYTES,
                          v2.MAX_FINDINGS_RECORD_BYTES),
                         (rf.MAX_LINE_NUMBER, rf.MAX_FINDINGS_AGGREGATE_BYTES,
                          rf.MAX_RECORD_BYTES))
        self.assertEqual((v2.FINDINGS_REFERENCE_RECORDED,
                          v2.FINDINGS_NOT_RECORDED, v2.FINDINGS_REFUSED),
                         (rf.OUTCOME_RECORDED, rf.OUTCOME_NOT_RECORDED,
                          rf.OUTCOME_REFUSED))

    def test_the_panel_has_words_for_exactly_every_findings_code(self):
        import re
        text = (REPO_ROOT / "integrations" / "pixel-agents" / "webview-ui"
                / "src" / "components" / "diEvidenceFormat.ts").read_text()
        block = text.split("// BEGIN FINDINGS_CODES")[1].split(
            "// END FINDINGS_CODES")[0]
        self.assertEqual(
            set(re.findall(r"^\s+([a-z_]+):", block, re.M)),
            set(v2.FINDINGS_NOT_RECORDED_PROBLEMS
                + v2.FINDINGS_REFUSED_PROBLEMS
                + (v2.PROBLEM_FINDINGS_LEGACY,)))

    def test_both_record_validators_agree_on_the_same_inputs(self):
        good = rf.build_record(route(), TASK, 1, REVIEWER, AGENT, b"p\n", [
            {"index": 1, "severity": "blocker", "area": "a/b.py", "line": 9,
             "summary": "s"},
            {"index": 2, "severity": "non_blocking", "area": None,
             "line": None, "summary": "t"}])
        edits = [lambda r: None,
                 lambda r: r.update(extra=1), lambda r: r.pop("count"),
                 lambda r: r.update(count=5), lambda r: r.update(round=0),
                 lambda r: r.update(round=True),
                 lambda r: r.update(routed_at=-1),
                 lambda r: r.update(route_nonce="ABCDEF0123456789"),
                 lambda r: r.update(candidate_sha256="0" * 63),
                 lambda r: r.update(provenance=rf.PROVENANCE + "."),
                 lambda r: r.update(linkage="other"),
                 lambda r: r.update(schema="x"),
                 lambda r: r["findings"][0].update(index=2),
                 lambda r: r["findings"][0].update(severity="urgent"),
                 lambda r: r["findings"][0].update(area="/abs.py"),
                 lambda r: r["findings"][0].update(area="a/../b"),
                 lambda r: r["findings"][1].update(line=3),
                 lambda r: r["findings"][0].update(line=0),
                 lambda r: r["findings"][0].update(summary=""),
                 lambda r: r["findings"][0].update(summary=" pad"),
                 lambda r: r["findings"][0].update(summary="x\u202e"),
                 lambda r: r["findings"][0].update(summary="x" * 281),
                 lambda r: r["findings"][0].update(extra=1)]
        for i, edit in enumerate(edits):
            record = json.loads(json.dumps(good))
            edit(record)
            try:
                rf.validate_record(json.loads(json.dumps(record)))
                writer = True
            except rf.RecordRefused:
                writer = False
            try:
                v2.validate_findings_record(json.loads(json.dumps(record)))
                reader = True
            except v2.contract.ContractError:
                reader = False
            with self.subTest(i):
                self.assertEqual(writer, reader)
                self.assertEqual(writer, i == 0)

    def test_an_absent_findings_key_is_an_older_producer(self):
        """External pinned answers predate the field: still valid."""
        block = review_record.block(v2.REVIEW_NOT_RECORDED,
                                    currency=v2.CURRENCY_CURRENT,
                                    task_status="ACTIVE")
        old = dict((k, v) for k, v in block.items() if k != "findings")
        v2.validate_review(old, "review")
        v2.validate_review(block, "review")



class DocumentedProcessTests(unittest.TestCase):
    """``docs/pixel-agents-office.md`` (Reviewer-declared findings): every
    concrete example there is checked against THIS code — the declaration
    runs through the documented isolated proof command, and the documented
    commands exist with exactly the options shown."""

    DOC = os.path.join(REPO_ROOT, "docs", "pixel-agents-office.md")

    def block(self, name):
        with open(self.DOC, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("<!-- %s:begin -->" % name)
        end = text.index("<!-- %s:end -->" % name)
        lines = [line.strip() for line in text[start:end].split("\n")[1:]]
        return [line for line in lines if line and not line.startswith("```")]

    def test_the_documented_declaration_is_accepted_exactly(self):
        declaration = self.block("findings-example")
        self.assertTrue(all(len(line) <= rf.MAX_COMPACT_LINE_CHARS
                            for line in declaration))
        nonce = declaration[1].split(" ", 1)[1]
        the_route = rf.new_route(nonce, "20261017-133442-1bfec8", 16,
                                 "reviewer1", "pod-reviewer1", CANDIDATE,
                                 1800000000)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        work = holder.name
        with open(os.path.join(work, "route.json"), "w") as handle:
            json.dump(the_route, handle)
        with open(os.path.join(work, "capture.txt"), "w") as handle:
            handle.write(capture("\n".join("  " + line
                                           for line in declaration)))
        out = os.path.join(work, "store")
        os.makedirs(out)
        command = self.block("findings-proof-command")[0].split()
        self.assertEqual(command[:2], ["python3", "tests/review_findings_proof.py"])
        result = subprocess.run(
            [sys.executable, command[1], "--route", os.path.join(work, "route.json"),
             "--capture", os.path.join(work, "capture.txt"), "--out", out],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertIn("LOCAL TEST", proof["isolated_candidate_proof"])
        self.assertEqual(proof["decision"], "REJECT")
        self.assertEqual(proof["findings_outcome"],
                         {"state": "recorded", "problem": None, "count": 2})
        self.assertEqual(proof["reader"]["findings_state"], "present")
        self.assertEqual(
            [(f["severity"], f["area"], f["line"], f["summary"])
             for f in proof["reader"]["findings"]],
            [("blocker", "target_runtime/review_findings.py", 235,
              "a fenced example envelope is promoted as a real declaration"),
             ("non_blocking", None, None,
              "the request must not echo envelope marker lines")])
        self.assertNotIn(CANARY, result.stdout)

    def test_the_documented_commands_exist_with_those_options(self):
        route_cmd = self.block("findings-route-command")[0].split()
        self.assertEqual(route_cmd[:2], ["herdctl", "review-findings-route"])
        shown = {word for word in route_cmd if word.startswith("--")}
        self.assertEqual(shown, {"--repo", "--reviewer", "--candidate-sha256"})
        result = subprocess.run(
            [sys.executable, "herdctl.py", "review-findings-route", "--help"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in shown:
            self.assertIn(option, result.stdout)
        proof_cmd = self.block("findings-proof-command")[0].split()
        result = subprocess.run(
            [sys.executable, proof_cmd[1], "--help"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--route", "--capture", "--out"):
            self.assertIn(option, proof_cmd)
            self.assertIn(option, result.stdout)

    def test_the_proof_tool_refuses_a_directory_that_is_not_empty(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        work = holder.name
        with open(os.path.join(work, "kept.txt"), "w") as handle:
            handle.write("not empty")
        sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
        import review_findings_proof
        with self.assertRaises(SystemExit):
            review_findings_proof.prove(route(), capture(envelope()), work)
        self.assertEqual(os.listdir(work), ["kept.txt"])


if __name__ == "__main__":
    unittest.main()
