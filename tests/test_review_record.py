"""NARROW canonical review record inspection (target_runtime.review_record),
query contract version 2's ONE admitted artifact content class.

Isolated temp roots only. Every review file is written exactly as the
reviewer gate writes one (``herdctl review-decision``: preamble, then the
``## Transcript`` line, then a transcript that carries a CANARY). Covered:
the writer's two-vocabulary corroboration (APPROVE/APPROVE, REJECT/REJECT,
MISSING/MALFORMED accepted; an out-of-enum raw token and every other pair
refused precisely); the GATE fact kept apart from the VERDICT; staleness of
a task that is not ACTIVE; the linkage checks that run BEFORE any read; the
recorded path never opened (descent through the EXPECTED components only)
and no other file in the directory ever read; symlinked ancestors and
leaves, ``..``, hard links, group/other-writable modes, special files,
oversize and markerless preambles, the strict preamble shape; a root or
component replaced during the read; and NO transcript byte ever read.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract_v2 as v2  # noqa: E402
from target_runtime import review_record  # noqa: E402
import test_mission_sources as base  # noqa: E402

TASK_ID = "20990101-000000-5e1f00"
CANARY = "TRANSCRIPT-CANARY-NEVER-PROJECTED"
MARKER_LINE = b"## Transcript\n"


def preamble(rounds=1, logical="reviewer1", agent="pod-reviewer1",
             token="APPROVE"):
    return ("# Reviewer round %d\n\nReviewer: `%s` / `%s`\n\nProtocol token:"
            " `%s`\n\n" % (rounds, logical, agent, token)).encode("utf-8")


class ReviewFixture(base.Bounded):

    def setUp(self):
        super(ReviewFixture, self).setUp()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(os.path.join(self.tmp.name, "herd"))
        self.reviews = os.path.join(self.root, ".herd", "state", "reviews")
        os.makedirs(self.reviews)

    def name(self, rounds=1, task_id=TASK_ID):
        return "%s-round-%02d.md" % (task_id, rounds)

    def review(self, rounds=1, token="APPROVE", head=None, mode=0o644,
               body=None, name=None):
        data = (head if head is not None else preamble(rounds, token=token))
        data += MARKER_LINE if body is None else b""
        data += (b"\n" + CANARY.encode() + b"\nHERD_DECISION: APPROVE\n"
                 if body is None else body)
        path = os.path.join(self.reviews, name or self.name(rounds))
        with open(path, "wb") as handle:
            handle.write(data)
        os.chmod(path, mode)
        return path

    def task(self, rounds=1, decision="APPROVE", status="ACTIVE",
             recorded=None, task_id=TASK_ID):
        value = {"id": task_id, "status": status, "repo": self.root}
        if rounds is not None:
            value["review_rounds"] = rounds
        if decision is not None:
            value["last_review_decision"] = decision
        value["last_review_file"] = (
            recorded if recorded is not None
            else os.path.join(self.reviews, self.name(rounds or 1)))
        return value

    def read(self, task, expected=TASK_ID, root=None):
        return review_record.canonical_review(root or self.root, task,
                                              expected)

    def assertRefused(self, block, problem):
        self.assertEqual((block["state"], block["problem"]),
                         (v2.REVIEW_REFUSED, problem), block)
        for field in ("record", "gate", "verdict", "integrity"):
            self.assertIsNone(block[field], block)
        v2.validate_review(block, "review")

    def spy_opens(self):
        opened = []
        real = os.open

        def spy(path, flags, *args, **kwargs):
            opened.append(path)
            return real(path, flags, *args, **kwargs)
        patcher = mock.patch.object(review_record.os, "open", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opened


class CorroborationTests(ReviewFixture):
    """The writer's own pairing of the RAW preamble token with the PARSED
    task decision, and the gate fact kept apart from the verdict."""

    def test_approve_pair_is_admitted_with_exactly_four_fields(self):
        self.review(token="APPROVE")
        block = self.read(self.task(decision="APPROVE"))
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED, block)
        self.assertEqual(block["record"], {
            "round": 1, "reviewer_logical": "reviewer1",
            "reviewer_agent": "pod-reviewer1", "protocol_token": "APPROVE"})
        self.assertEqual((block["gate"], block["verdict"]),
                         (v2.GATE_PARSED, "APPROVE"))
        self.assertEqual(block["currency"], v2.CURRENCY_CURRENT)
        self.assertEqual(block["integrity"],
                         v2.INTEGRITY_LINKAGE_CORROBORATED)
        self.assertNotIn(CANARY, repr(block))
        v2.validate_review(block, "review")

    def test_reject_pair_is_admitted_as_a_reject_verdict(self):
        self.review(token="REJECT")
        block = self.read(self.task(decision="REJECT"))
        self.assertEqual((block["state"], block["gate"], block["verdict"]),
                         (v2.REVIEW_ADMITTED, v2.GATE_PARSED, "REJECT"))
        v2.validate_review(block, "review")

    def test_missing_malformed_is_admitted_as_an_unparsed_gate_not_a_verdict(
            self):
        self.review(token="MISSING")
        block = self.read(self.task(decision="MALFORMED"))
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED, block)
        self.assertEqual(block["record"]["protocol_token"], "MISSING")
        # The GATE fact, never a negative verdict.
        self.assertEqual(block["gate"], v2.GATE_UNPARSED)
        self.assertIsNone(block["verdict"])
        v2.validate_review(block, "review")

    def test_an_out_of_enum_raw_token_refuses_by_its_own_reason(self):
        for raw in ("LGTM", "ACCEPT", "APPROVED_WITH_NITS"):
            with self.subTest(raw=raw):
                self.review(token=raw)
                block = self.read(self.task(decision="MALFORMED"))
                self.assertRefused(block,
                                   v2.PROBLEM_REVIEW_TOKEN_OUT_OF_ENUM)
                self.assertNotIn(raw, repr(block))

    def test_a_genuine_disagreement_refuses(self):
        self.review(token="APPROVE")
        self.assertRefused(self.read(self.task(decision="REJECT")),
                           v2.PROBLEM_REVIEW_DISAGREES)

    def test_every_other_pair_refuses(self):
        for token, decision in (("APPROVE", "MALFORMED"),
                                ("REJECT", "APPROVE"),
                                ("MISSING", "APPROVE"),
                                ("MISSING", "REJECT"),
                                ("REJECT", "MALFORMED")):
            with self.subTest(token=token, decision=decision):
                self.review(token=token)
                self.assertRefused(self.read(self.task(decision=decision)),
                                   v2.PROBLEM_REVIEW_DISAGREES)

    def test_a_task_decision_outside_the_writer_vocabulary_refuses(self):
        self.review(token="APPROVE")
        self.assertRefused(self.read(self.task(decision="LGTM")),
                           v2.PROBLEM_REVIEW_DECISION_UNKNOWN)


class CurrencyTests(ReviewFixture):
    """The writer updates the task record's review fields only while the
    task is ACTIVE; any other status is reported possibly stale."""

    def test_a_non_active_task_is_possibly_stale_never_current(self):
        self.review(token="APPROVE")
        for status in ("COMPLETE", "FAILED", "BLOCKED", None):
            with self.subTest(status=status):
                block = self.read(self.task(status=status))
                self.assertEqual(block["state"], v2.REVIEW_ADMITTED)
                self.assertEqual(block["currency"],
                                 v2.CURRENCY_POSSIBLY_STALE)
                v2.validate_review(block, "review")

    def test_a_task_recording_no_review_is_not_recorded(self):
        task = {"id": TASK_ID, "status": "ACTIVE", "repo": self.root}
        block = self.read(task)
        self.assertEqual((block["state"], block["currency"]),
                         (v2.REVIEW_NOT_RECORDED, v2.CURRENCY_CURRENT))
        stale = self.read(dict(task, status="COMPLETE", review_rounds=0))
        self.assertEqual((stale["state"], stale["currency"]),
                         (v2.REVIEW_NOT_RECORDED, v2.CURRENCY_POSSIBLY_STALE))
        v2.validate_review(stale, "review")


class LinkageTests(ReviewFixture):
    """Every linkage check runs BEFORE any read: a refusal opens nothing."""

    def test_no_mission_binding_reads_nothing(self):
        self.review()
        opened = self.spy_opens()
        block = self.read(self.task(), expected=None)
        self.assertEqual((block["state"], block["problem"]),
                         (v2.REVIEW_NOT_APPLICABLE,
                          v2.PROBLEM_REVIEW_NO_BINDING))
        self.assertEqual(opened, [])

    def test_linkage_refusals_open_nothing(self):
        self.review()
        elsewhere = os.path.join(self.root, "elsewhere", self.name())
        cases = (
            (None, TASK_ID, v2.PROBLEM_REVIEW_TASK_ABSENT),
            (self.task(task_id="20990101-000000-0ther0"), TASK_ID,
             v2.PROBLEM_REVIEW_TASK_MISMATCH),
            (self.task(decision=None), TASK_ID,
             v2.PROBLEM_REVIEW_REFERENCE_INCOMPLETE),
            (self.task(rounds=True), TASK_ID, v2.PROBLEM_REVIEW_ROUND_INVALID),
            (self.task(rounds=0, recorded=os.path.join(
                self.reviews, self.name(1))), TASK_ID,
             v2.PROBLEM_REVIEW_ROUND_INVALID),
            (self.task(rounds=100), TASK_ID,
             v2.PROBLEM_REVIEW_ROUND_OUT_OF_CLASS),
            (self.task(recorded=elsewhere), TASK_ID,
             v2.PROBLEM_REVIEW_FILE_OUTSIDE),
            (self.task(recorded=os.path.join(
                self.reviews, "..", "reviews", self.name())), TASK_ID,
             v2.PROBLEM_REVIEW_FILE_OUTSIDE),
            (self.task(recorded="reviews/" + self.name()), TASK_ID,
             v2.PROBLEM_REVIEW_FILE_OUTSIDE),
            (self.task(recorded=os.path.join(
                self.reviews, "%s-round-1.md" % TASK_ID)), TASK_ID,
             v2.PROBLEM_REVIEW_FILE_NAME),
            (self.task(task_id="../%s" % TASK_ID), "../%s" % TASK_ID,
             v2.PROBLEM_REVIEW_FILE_NAME),
        )
        for task, expected, problem in cases:
            with self.subTest(problem=problem, task=task):
                opened = self.spy_opens()
                self.assertRefused(self.read(task, expected), problem)
                self.assertEqual(opened, [], "a refusal before any read")
                mock.patch.stopall()

    def test_the_recorded_path_is_never_opened_only_compared(self):
        self.review()
        recorded = os.path.join(self.reviews, self.name())
        opened = self.spy_opens()
        block = self.read(self.task(recorded=recorded))
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED)
        # Descent through the EXPECTED components, never the recorded path.
        self.assertEqual(opened, [self.root, ".herd", "state", "reviews",
                                  self.name()])
        self.assertNotIn(recorded, opened)

    def test_no_other_file_and_no_frozen_capture_is_ever_read(self):
        for sibling in ("round01-full-capture-provenance.json",
                        "%s-round-01.frozen-capture.md" % TASK_ID,
                        "%s-round-02.md" % TASK_ID):
            with open(os.path.join(self.reviews, sibling), "wb") as handle:
                handle.write(preamble(token="REJECT") + MARKER_LINE)
        opened = self.spy_opens()
        block = self.read(self.task(rounds=1, decision="REJECT"))
        # The canonical round file is absent: refused, with NO fallback.
        self.assertRefused(block, v2.PROBLEM_REVIEW_ABSENT)
        self.assertEqual(opened[-1], self.name(1))
        self.assertFalse(set(opened) & {
            "round01-full-capture-provenance.json",
            "%s-round-01.frozen-capture.md" % TASK_ID,
            "%s-round-02.md" % TASK_ID})


class ContainmentTests(ReviewFixture):
    """Descriptor-relative descent: links, specials, links-out and modes."""

    def test_a_symlinked_ancestor_refuses(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(os.path.join(outside, "reviews"))
        self.review()
        os.rename(self.reviews, os.path.join(outside, "reviews", "real"))
        os.symlink(os.path.join(outside, "reviews", "real"), self.reviews)
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_CONTAINED)

    def test_a_symlinked_herd_directory_refuses(self):
        self.review()
        herd = os.path.join(self.root, ".herd")
        moved = os.path.join(self.tmp.name, "moved-herd")
        os.rename(herd, moved)
        os.symlink(moved, herd)
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_CONTAINED)

    def test_a_symlinked_leaf_refuses(self):
        target = self.review(name="real-target.md")
        os.symlink(target, os.path.join(self.reviews, self.name()))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_CONTAINED)

    def test_a_hardlinked_leaf_refuses(self):
        path = self.review()
        os.link(path, os.path.join(self.tmp.name, "second-link.md"))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_HARDLINK)

    def test_a_group_or_other_writable_file_refuses(self):
        for mode in (0o664, 0o646, 0o666, 0o620):
            with self.subTest(mode=oct(mode)):
                self.review(mode=mode)
                self.assertRefused(self.read(self.task()),
                                   v2.PROBLEM_REVIEW_WRITABLE)

    def test_world_readable_files_are_admitted_deliberately(self):
        # The deliberate deviation from the 0o077 private-file rule: the
        # writer produces 0644 files, and readability is not the threat.
        for mode in (0o644, 0o640, 0o600, 0o444):
            with self.subTest(mode=oct(mode)):
                self.review(mode=mode)
                self.assertEqual(self.read(self.task())["state"],
                                 v2.REVIEW_ADMITTED)

    def test_a_fifo_leaf_refuses_without_blocking(self):
        os.mkfifo(os.path.join(self.reviews, self.name()))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_REGULAR)

    def test_a_directory_leaf_refuses(self):
        os.makedirs(os.path.join(self.reviews, self.name()))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_REGULAR)

    def test_an_unresolved_root_refuses(self):
        self.review()
        link = os.path.join(self.tmp.name, "root-link")
        os.symlink(self.root, link)
        self.assertRefused(self.read(self.task(recorded=os.path.join(
            link, ".herd", "state", "reviews", self.name())), root=link),
            v2.PROBLEM_REVIEW_NOT_CONTAINED)

    def test_a_component_replaced_during_the_read_refuses(self):
        self.review()
        real = review_record._read_until_marker

        def swap_then_read(descriptor):
            lines = real(descriptor)
            os.rename(self.reviews, self.reviews + ".old")
            os.makedirs(self.reviews)
            return lines
        with mock.patch.object(review_record, "_read_until_marker",
                               swap_then_read):
            self.assertRefused(self.read(self.task()),
                               v2.PROBLEM_REVIEW_ROOT_CHANGED)

    def test_the_root_replaced_during_the_read_refuses(self):
        self.review()
        real = review_record._read_until_marker

        def swap_then_read(descriptor):
            lines = real(descriptor)
            os.rename(self.root, self.root + ".old")
            os.makedirs(self.root)
            return lines
        with mock.patch.object(review_record, "_read_until_marker",
                               swap_then_read):
            self.assertRefused(self.read(self.task()),
                               v2.PROBLEM_REVIEW_ROOT_CHANGED)


class PreambleTests(ReviewFixture):
    """Only the preamble is read, byte by byte, in the writer's exact
    shape; nothing after the marker line ever leaves the descriptor."""

    def test_no_transcript_byte_is_ever_read(self):
        self.review()
        reads = []
        real = os.read

        def spy(descriptor, size):
            data = real(descriptor, size)
            reads.append((size, data))
            return data
        with mock.patch.object(review_record.os, "read", spy):
            block = self.read(self.task())
        self.assertEqual(block["state"], v2.REVIEW_ADMITTED)
        self.assertTrue(all(size == 1 for size, _ in reads))
        consumed = b"".join(data for _, data in reads)
        self.assertEqual(consumed, preamble() + MARKER_LINE)
        self.assertNotIn(CANARY.encode(), consumed)

    def test_a_preamble_over_its_byte_bound_refuses(self):
        padding = b"x" * (v2.MAX_REVIEW_PREAMBLE_BYTES + 10)
        self.review(head=padding + b"\n")
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_OVER_BOUND)

    def test_a_preamble_over_its_line_bound_refuses(self):
        self.review(head=b"\n" * (v2.MAX_REVIEW_PREAMBLE_LINES + 2))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_OVER_BOUND)

    def test_a_missing_marker_refuses(self):
        self.review(body=b"no marker anywhere\n")
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NO_MARKER)

    def test_the_shape_is_exact(self):
        good = preamble().decode()
        for label, head in (
                ("extra line", good.replace("\n\nProtocol", "\nnote\n\n"
                                            "Protocol")),
                ("missing blank", good.replace("1\n\nReviewer",
                                               "1\nReviewer")),
                ("indented token", good.replace("Protocol token",
                                                "  Protocol token")),
                ("crlf", good.replace("\n", "\r\n")),
                ("backtick in name", good.replace("pod-reviewer1",
                                                  "pod`x")),
                ("space in name", good.replace("pod-reviewer1",
                                               "pod reviewer1")),
                ("empty token", good.replace("`APPROVE`", "``")),
                ("zero round", good.replace("round 1", "round 0"))):
            with self.subTest(label=label):
                self.review(head=head.encode())
                self.assertRefused(self.read(self.task()),
                                   v2.PROBLEM_REVIEW_SHAPE)

    def test_a_non_utf8_preamble_refuses(self):
        self.review(head=preamble().replace(b"reviewer1`", b"\xff\xfe`"))
        self.assertRefused(self.read(self.task()),
                           v2.PROBLEM_REVIEW_NOT_UTF8)

    def test_a_heading_round_other_than_the_task_round_refuses(self):
        self.review(rounds=1, head=preamble(rounds=2))
        self.assertRefused(self.read(self.task(rounds=1)),
                           v2.PROBLEM_REVIEW_ROUND_DISAGREES)

    def test_a_two_digit_round_is_admitted(self):
        self.review(rounds=12)
        block = self.read(self.task(rounds=12))
        self.assertEqual((block["state"], block["record"]["round"]),
                         (v2.REVIEW_ADMITTED, 12))


if __name__ == "__main__":
    unittest.main()
