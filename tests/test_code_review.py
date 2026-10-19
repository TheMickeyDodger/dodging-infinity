"""Item 3 (original 20) — the live code review's backend contract
(``target_runtime.code_review``).

Layers, each over REAL files and a REAL git, none over a mock of what it
proves:

1. PURE: git's own output (captured from the fixture repositories, not typed
   by hand) through the parsers; the disclosure policy and its parity with
   the evidence library's lists.
2. THE READ, with a synthetic owner answer (a corroborating catalog row of
   the exact shape ``contract`` defines), a real private registry, a real
   workspace and the real ``GitTransport``: the overview and every file's
   diff exactly, the eligibility refusals, MOVEMENT (a workspace that
   changes under a read, a snapshot that is no longer current, a registration
   that is repointed or removed, an assignment that is replaced), the bounds,
   the policy exclusions (git is never ASKED for a withheld path), and that
   nothing is written.
3. THE REAL OWNER over a REAL running Mission (``test_catalog_v2.V2Fixture``):
   the field names this module relies on are the ones the real catalog row
   carries, a COMPLETED Mission is not an active assignment, and the
   ``/code_review`` route.

The git-state non-mutation bar itself is ``test_code_review_git.py``.
"""

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from target_runtime import artifact_content  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import git_transport  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import state_artifact  # noqa: E402
import test_code_review_git as gitfx  # noqa: E402
from test_mission_sources import Bounded  # noqa: E402

MISSION_REF = "mc-1:mn-" + "a" * 32
TASK = "20261014-000000-aaaaaa"
OTHER_TASK = "20261014-000000-bbbbbb"
SOURCE_ID = "mc-1"
NOW = 1_800_000_000


#: Every path the busy workspace changes (``touched.txt`` is only touched).
BUSY_PATHS = (
    ".env", ".herd/state.json", "bin/blob.bin", "crlf.txt", "docs/gone.md",
    "empty.txt", "notes/new.txt", "src/app.py", "src/moved.py",
    "src/mover.py", "src/util.py")


def fresh(response, now):
    return contract.HEALTH_FRESH, None


class Answer(object):
    """The owner's last refreshed answer, as ``code_review`` reads it: a
    mutable synthetic of exactly the fields ``contract`` defines."""

    def __init__(self, workspace, registration):
        self.workspace = workspace
        self.registration = dict(registration)
        self.mission = {
            "mission_ref": MISSION_REF, "source_id": SOURCE_ID,
            "lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE,
                          "state": "RUNNING",
                          "standing": contract.STANDING_ACTIVE},
            "binding": {"state": contract.BINDING_BOUND, "reason": None,
                        "task_id": TASK},
            "execution": {"task_id": TASK, "workspace_realpath": workspace,
                          "consistent": True, "record_ambiguous": False},
        }
        self.response = {"missions": [self.mission],
                         "sources": [dict(registration)]}
        self.held = {"response": self.response}

    def __call__(self):
        return self.held


class ReviewCase(gitfx.FixtureCase):
    """A real busy workspace, a real registry and the real transport. Bounded
    (CONTRIBUTING.md's termination rule) by ``gitfx.FixtureCase``, which arms
    the repository's own independent watchdog before anything is built."""

    def setUp(self):
        super(ReviewCase, self).setUp()
        self.repo_ = self.busy()
        self.workspace = self.repo_.path
        self.store = os.path.join(self.root, "stores", SOURCE_ID)
        os.makedirs(self.store, mode=0o700)
        self.registry = registry_module.SourceRegistry(
            os.path.join(self.root, "registry"))
        outcome = self.registry.register(
            SOURCE_ID, contract.KIND_MISSION_CORE, self.store,
            "local_request", 60, NOW)
        self.registration = dict(
            (k, outcome["source"][k]) for k in code_review._REGISTRATION_FIELDS)
        self.answer = Answer(self.workspace, self.registration)
        self.transport = git_transport.GitTransport()
        self.freshness = fresh
        self.ticks = 0

    def clock(self):
        self.ticks += 1
        return NOW + self.ticks

    def read(self, snapshot=None, file_id=None, mission_ref=MISSION_REF,
             task_id=TASK, transport=None, answer=None, registry=None):
        answer = answer or self.answer
        reply = code_review.read(
            registry or self.registry, answer, self.freshness, self.clock,
            transport or self.transport, mission_ref, task_id, snapshot,
            file_id)
        self.assertEqual(set(reply), set(code_review.ANSWER_KEYS))
        self.assertEqual((reply["schema"], reply["version"]),
                         (code_review.SCHEMA, code_review.VERSION))
        self.assertEqual(set(reply["accounting"]),
                         set(code_review.ACCOUNTING_KEYS))
        return reply

    def overview(self, **kwargs):
        reply = self.read(**kwargs)
        self.assertEqual((reply["state"], reply["problem"]),
                         (code_review.STATE_OBSERVED, None), reply["detail"])
        return reply

    def files(self, reply=None):
        reply = reply or self.overview()
        return dict((f["path"], f) for f in reply["overview"]["files"])

    def opened(self, path, reply=None):
        reply = reply or self.overview()
        entry = self.files(reply)[path]
        return self.read(snapshot=reply["snapshot"], file_id=entry["file_id"])

    def assert_refused(self, reply, problem):
        self.assertEqual((reply["state"], reply["problem"]),
                         (code_review.STATE_REFUSED, problem), reply["detail"])
        for key in ("target", "comparison", "overview", "file"):
            self.assertIsNone(reply[key], key)
        self.assertIsInstance(reply["detail"], str)
        self.assertLessEqual(len(reply["detail"]), code_review.MAX_DETAIL_CHARS)


class PureParserTests(ReviewCase):

    def real_diff(self, *paths):
        return self.transport.live_file_diff(self.workspace, list(paths), ())["data"]

    def test_the_inventory_of_the_busy_workspace(self):
        data = self.transport.live_inventory(self.workspace, ())["data"]
        entries = dict((path, xy) for xy, path in
                       code_review.parse_inventory(data))
        self.assertEqual(entries["src/app.py"], "MM")
        self.assertEqual(entries["notes/new.txt"], "??")
        self.assertEqual(sorted(entries), sorted(BUSY_PATHS))

    def test_a_malformed_or_unsafe_inventory_entry_is_refused(self):
        for raw, problem in (
                (b"M\x00", code_review.PROBLEM_INVENTORY_INCONSISTENT),
                (b"MMsrc/a.py\x00", code_review.PROBLEM_INVENTORY_INCONSISTENT),
                (b"?? \xff\xfe.txt\x00", code_review.PROBLEM_PATH_UNSUPPORTED),
                (b"?? ../outside\x00", code_review.PROBLEM_PATH_UNSUPPORTED),
                (b"?? /etc/passwd\x00", code_review.PROBLEM_PATH_UNSUPPORTED),
                (b"?? a\\b\x00", code_review.PROBLEM_PATH_UNSUPPORTED),
                (b"?? a//b\x00", code_review.PROBLEM_PATH_UNSUPPORTED),
                (b"?? " + b"x" * 2000 + b"\x00",
                 code_review.PROBLEM_PATH_UNSUPPORTED)):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(code_review._Refused) as caught:
                    code_review.parse_inventory(raw)
                self.assertEqual(caught.exception.problem, problem)

    def test_a_nested_repository_is_one_untracked_directory_entry(self):
        self.assertEqual(
            code_review.parse_inventory(b"?? vendor/clone/\x00 M a.py\x00"),
            [("??", "vendor/clone/"), (" M", "a.py")])
        for raw in (b"?? vendor//\x00", b"?? /\x00", b"?? ../x/\x00",
                    b" M vendor/\x00", b"?? a/b//\x00"):
            with self.subTest(raw=raw):
                with self.assertRaises(code_review._Refused) as caught:
                    code_review.parse_inventory(raw)
                self.assertEqual(caught.exception.problem,
                                 code_review.PROBLEM_PATH_UNSUPPORTED)

    def test_the_modes_of_a_raw_listing(self):
        zero, sha = "0" * 40, "a" * 40
        raw = (
            ":100644 100644 %s %s M\x00src/a.py\x00" % (sha, zero)
            + ":160000 160000 %s %s M\x00vendor/lib\x00" % (sha, sha)
            + ":000000 160000 %s %s A\x00vendor/new\x00" % (zero, sha)
            + ":100644 100644 %s %s R086\x00src/old.py\x00src/new.py\x00"
            % (sha, zero)).encode()
        modes = code_review.parse_modes(raw)
        self.assertEqual(modes["vendor/lib"], ("160000", "160000", "M"))
        self.assertEqual(modes["vendor/new"], ("000000", "160000", "A"))
        self.assertEqual(modes["src/new.py"], ("100644", "100644", "R"))
        self.assertNotIn("src/old.py", modes, "a rename is under its new name")
        self.assertEqual(sorted(modes), ["src/a.py", "src/new.py",
                                         "vendor/lib", "vendor/new"])
        self.assertEqual(code_review.parse_modes(b""), {})

    def test_a_malformed_raw_listing_is_refused(self):
        sha = "a" * 40
        good = ":100644 100644 %s %s M\x00" % (sha, sha)
        for raw in (b"garbage\x00x\x00", good.encode(),            # no path
                    (good + "\x00").encode(),                        # empty path
                    (":100644 100644 %s %s Q\x00x\x00" % (sha, sha)).encode(),
                    (":10064 100644 %s %s M\x00x\x00" % (sha, sha)).encode(),
                    (":100644 100644 %s %s R100\x00only\x00" % (sha, sha)).encode(),
                    (":100644 100644 %s %s M\x00\xff\x00" % (sha, sha)).encode("latin1")):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(code_review._Refused):
                    code_review.parse_modes(raw)

    def test_the_counts_of_the_busy_workspace(self):
        data = self.transport.live_counts(self.workspace, ())["data"]
        counts = code_review.parse_counts(data)
        self.assertEqual(counts["src/app.py"], (1, 1, None))
        self.assertEqual(counts["bin/blob.bin"], (None, None, None))
        self.assertEqual(counts["src/moved.py"][2], "src/mover.py")
        self.assertEqual(counts["src/moved.py"][:2], (1, 0))
        self.assertNotIn("src/mover.py", counts, "the rename is ONE entry")

    def test_malformed_counts_are_refused(self):
        for raw in (b"1\t2\x00", b"x\ty\tpath\x00", b"1\t2\t\x00old\x00",
                    b"1\t1\ta\x001\t1\ta\x00", b"1\t1\t../a\x00"):
            with self.subTest(raw=raw):
                with self.assertRaises(code_review._Refused):
                    code_review.parse_counts(raw)

    def test_a_modified_file_has_exact_hunks(self):
        shown = code_review.parse_file_diff(self.real_diff("src/util.py"))
        self.assertEqual((shown["additions"], shown["deletions"]), (2, 1))
        self.assertEqual(len(shown["hunks"]), 1)
        hunk = shown["hunks"][0]
        self.assertEqual((hunk["old_start"], hunk["old_lines"],
                          hunk["new_start"], hunk["new_lines"]), (1, 3, 1, 4))
        self.assertEqual(
            [(l["kind"], l["old"], l["new"], l["text"]) for l in hunk["lines"]],
            [("context", 1, 1, "A = 1"), ("del", 2, None, "B = 2"),
             ("add", None, 2, "B = 20"), ("context", 3, 3, "C = 3"),
             ("add", None, 4, "D = 4")])
        self.assertEqual((shown["lines_total"], shown["lines_retained"],
                          shown["truncated"]), (5, 5, False))

    def test_a_deleted_file_and_an_added_empty_file(self):
        shown = code_review.parse_file_diff(self.real_diff("docs/gone.md"))
        self.assertEqual((shown["additions"], shown["deletions"]), (0, 1))
        shown = code_review.parse_file_diff(self.real_diff("empty.txt"))
        self.assertEqual((shown["hunks"], shown["binary"]), ([], False))

    def test_a_rename_carries_its_similarity(self):
        shown = code_review.parse_file_diff(
            self.real_diff("src/mover.py", "src/moved.py"))
        self.assertGreaterEqual(shown["similarity"], 50)
        self.assertEqual((shown["additions"], shown["deletions"]), (1, 0))

    def test_a_binary_file_is_a_state_not_a_diff(self):
        shown = code_review.parse_file_diff(self.real_diff("bin/blob.bin"))
        self.assertTrue(shown["binary"])
        self.assertEqual(shown["hunks"], [])

    def test_a_mode_change_alone(self):
        os.chmod(os.path.join(self.workspace, "src", "app.py"), 0o755)
        self.repo_.git("update-index", "--chmod=+x", "src/app.py")
        shown = code_review.parse_file_diff(self.real_diff("src/app.py"))
        self.assertEqual(shown["mode_change"], {"old": "100644",
                                                "new": "100755"})

    def test_carriage_returns_and_a_missing_final_newline_are_kept(self):
        shown = code_review.parse_file_diff(self.real_diff("crlf.txt"))
        texts = [l["text"] for l in shown["hunks"][0]["lines"]]
        self.assertTrue(all(t.endswith("\r") for t in texts), texts)
        self.repo_.write("src/util.py", "A = 1\nB = 20\nC = 3\nlast")
        shown = code_review.parse_file_diff(self.real_diff("src/util.py"))
        kinds = [l["kind"] for l in shown["hunks"][0]["lines"]]
        self.assertEqual(kinds[-1], "no_newline")

    def test_a_very_long_line_is_clipped_with_the_count_kept(self):
        self.repo_.write("src/util.py", "x" * 5000 + "\n")
        shown = code_review.parse_file_diff(self.real_diff("src/util.py"))
        add = [l for l in shown["hunks"][0]["lines"] if l["kind"] == "add"][0]
        self.assertEqual(len(add["text"]), code_review.MAX_REVIEW_LINE_CHARS)
        self.assertEqual(add["clipped"],
                         5000 - code_review.MAX_REVIEW_LINE_CHARS)

    def test_retention_truncates_and_the_totals_stay_exact(self):
        self.repo_.write("src/util.py",
                         "".join("n%d\n" % n for n in range(50)))
        diff = self.real_diff("src/util.py")
        with mock.patch.object(code_review, "MAX_REVIEW_FILE_LINES", 10):
            shown = code_review.parse_file_diff(diff)
        self.assertTrue(shown["truncated"])
        self.assertEqual(shown["lines_retained"], 10)
        self.assertEqual(shown["lines_total"], 53)
        self.assertEqual((shown["additions"], shown["deletions"]), (50, 3))
        with mock.patch.object(code_review, "MAX_REVIEW_FILE_CHARS", 20):
            shown = code_review.parse_file_diff(diff)
        self.assertTrue(shown["truncated"])
        self.assertEqual(shown["lines_total"], 53)

    def test_retention_at_the_character_cap_is_a_contiguous_prefix(self):
        # ROUND 20 F4. Unequal line lengths at the character cap: the third
        # line does not fit, the two short ones after it would. Keeping those
        # leaves a HOLE under a "first N lines" claim; retention must stop at
        # the first line that does not fit, and the remainder still be COUNTED.
        content = "a" * 8 + "\n" + "b" * 8 + "\n" + "c" * 30 + "\nd\ne\n"
        self.repo_.write("lines.txt", content)
        self.repo_.stage("lines.txt")
        diff = self.real_diff("lines.txt")
        with mock.patch.object(code_review, "MAX_REVIEW_FILE_CHARS", 20):
            shown = code_review.parse_file_diff(diff)
            synthetic = code_review.synthesize_new_file(content)
        for label, result in (("a tracked file's diff", shown),
                              ("an untracked file shown as additions", synthetic)):
            with self.subTest(label):
                kept = [line["new"] for hunk in result["hunks"]
                        for line in hunk["lines"] if line["kind"] == "add"]
                self.assertEqual(kept, [1, 2],
                                 "the retained lines are exactly a prefix")
                self.assertTrue(result["truncated"])
                self.assertEqual(result["lines_retained"], 2)
                self.assertEqual((result["lines_total"], result["additions"],
                                  result["deletions"]), (5, 5, 0),
                                 "the remainder is counted, not dropped")

    def test_private_locations_are_the_shortest_withheld_prefixes(self):
        self.assertEqual(code_review.private_locations([
            "src/app.py", "secrets/a.txt", "secrets/deep/b.txt",
            "docs/token.md", ".env", ".env.local", "a/.claude/x/y",
            "a/b/id_rsa", "keys/k.pem", "KEYS/Server.PEM", "src/app.py"]),
            (".env", ".env.local", "KEYS/Server.PEM", "a/.claude",
             "a/b/id_rsa", "docs/token.md", "keys/k.pem", "secrets"))
        self.assertEqual(code_review.private_locations([]), ())
        self.assertEqual(code_review.private_locations(["src/a.py"]), ())

    def test_a_path_has_a_private_location_exactly_when_it_is_withheld(self):
        # the SAME policy, asked of every prefix: no second copy of the rules
        parts = ("src", "docs", ".github", ".git", ".claude", ".herd", ".env",
                 ".env.local", ".foo", "secrets", "Token", "a_token", "id_rsa",
                 "k.pem", "K.PEM", "keys", "x.crt", "plain.txt", "caf\u00e9",
                 "SECRET", "api-key.json", "passwords", "normal", ".eslintrc")
        corpus = list(parts)
        corpus += [a + "/" + b for a in parts for b in parts]
        corpus += [a + "/" + b + "/" + c for a in parts[:12] for b in parts
                   for c in ("f.py", "secret.txt")]
        for path in corpus:
            found = code_review.private_locations([path])
            self.assertEqual(code_review.withheld_reason(path) is not None,
                             bool(found), path)
            for location in found:
                self.assertTrue(path == location or path.startswith(
                    location + "/"), (path, location))
                self.assertIsNotNone(code_review.withheld_reason(location))

    def test_a_private_removal_and_a_public_addition_of_one_blob_are_a_move(self):
        zero, empty = "0" * 40, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        a, b = "a" * 40, "b" * 40
        raw = (
            ":100644 000000 %s %s D\x00secrets/old.txt\x00" % (a, zero)
            + ":000000 100644 %s %s A\x00docs/new.txt\x00" % (zero, a)
            + ":100644 000000 %s %s D\x00secrets/z.txt\x00" % (b, zero)
            + ":000000 100644 %s %s A\x00docs/other.txt\x00" % (zero, a)
            + ":000000 100644 %s %s A\x00docs/different.txt\x00" % (zero, "c" * 40)
            + ":100644 000000 %s %s D\x00docs/removed.txt\x00" % (b, zero)
            + ":000000 100644 %s %s A\x00secrets/added.txt\x00" % (zero, b)
            + ":100644 000000 %s %s D\x00secrets/empty.txt\x00" % (empty, zero)
            + ":000000 100644 %s %s A\x00docs/empty.txt\x00" % (zero, empty)
        ).encode()
        ids = code_review.parse_raw_ids(raw)
        self.assertEqual(ids["secrets/old.txt"], (a, zero, "D"))
        self.assertEqual(code_review.exact_private_moves(ids), {
            "docs/new.txt": "secrets/old.txt",
            "docs/other.txt": "secrets/old.txt"})
        self.assertEqual(code_review.exact_private_moves({}), {})
        self.assertEqual(code_review.parse_modes(raw)["docs/new.txt"],
                         ("000000", "100644", "A"),
                         "parse_modes keeps its own shape")

    def test_the_index_record_of_an_entry_is_parsed_exactly(self):
        sha = b"a" * 40

        def record(stage, path, size=4, mtime=1792427141):
            return (b"100644 " + sha + b" " + stage + b"\t" + path + b"\x00"
                    + b"  ctime: 1:2\n  mtime: " + str(mtime).encode()
                    + b":496917956\n  dev: 1\tino: 2\n  uid: 3\tgid: 4\n"
                    + b"  size: " + str(size).encode() + b"\tflags: 0\n")
        data = (record(b"0", b"secrets/a.txt") + record(b"1", b"secrets/u.txt")
                + record(b"0", b"secrets/we ird\nname.txt", 9, 4294967295))
        self.assertEqual(code_review.parse_index_stat(data), {
            "secrets/a.txt": ("100644", 4, 1792427141),
            "secrets/we ird\nname.txt": ("100644", 9, 4294967295)})
        self.assertEqual(code_review.parse_index_stat(b""), {})
        for bad in (b"garbage", record(b"0", b"a")[:-5],
                    record(b"0", b"../a"), record(b"0", b"/abs"),
                    record(b"0", b"a") + b"x"):
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(code_review._Refused):
                    code_review.parse_index_stat(bad)

    def test_stat_changes_have_no_horizon_in_them(self):
        # ROUND 21 F4. The comparison is the index's record against the working
        # tree's stat, in this module: git is not asked to compare a file, so no
        # recorded time, however far ahead, can make a file be read. Pure.
        now = {"gone": None, "same": [0o100644, 4, 5 * 10**9, 1],
               "edited": [0o100644, 4, 6 * 10**9, 1],
               "grown": [0o100644, 5, 5 * 10**9, 1],
               "dir": [0o040755, 64, 5 * 10**9, 1],
               "link": [0o120777, 7, 5 * 10**9, 1],
               "sub": [0o040755, 64, 9 * 10**9, 1],
               "far": [0o100644, 4, 4294967295 * 10**9, 1],
               "farther": [0o100644, 4, (4294967295 + 3) * 10**9, 1],
               "wrapped": [0o100644, 4, (2**32 + 5) * 10**9, 1]}
        recorded = {"gone": ("100644", 4, 5), "same": ("100644", 4, 5),
                    "edited": ("100644", 4, 5), "grown": ("100644", 4, 5),
                    "dir": ("100644", 4, 5), "link": ("120000", 7, 5),
                    "sub": ("160000", 0, 1), "far": ("100644", 4, 4294967295),
                    "farther": ("100644", 4, 4294967295),
                    "wrapped": ("100644", 4, 5)}
        opened = []

        def current(path):
            opened.append(path)
            return now[path]
        changes = code_review.unstaged_changes(recorded, current)
        self.assertEqual(dict((p, c[2]) for p, c in changes.items()), {
            "gone": "D", "edited": "M", "grown": "M", "dir": "T",
            "farther": "M"})
        self.assertEqual(sorted(opened), sorted(recorded),
                         "each path looked at once, by stat")

    def test_an_end_of_file_marker_is_not_a_line_and_never_truncates_alone(self):
        # ROUND 21 F3, the backend half. Six thousand lines and no final newline:
        # the marker is not a line (lines_total never counts it), so refusing it
        # for the line cap would claim a truncation with nothing left out.
        for tracked in (True, False):
            body = "".join("n%d\n" % n for n in range(6000))[:-1]
            if tracked:
                self.repo_.write("six.txt", body)
                self.repo_.stage("six.txt")
                shown = code_review.parse_file_diff(self.real_diff("six.txt"))
            else:
                shown = code_review.synthesize_new_file(body)
            with self.subTest(tracked=tracked):
                self.assertEqual((shown["truncated"], shown["lines_total"],
                                  shown["lines_retained"]),
                                 (False, 6000, 6000))
                self.assertEqual(shown["hunks"][-1]["lines"][-1]["kind"],
                                 "no_newline")
        shown = code_review.parse_file_diff(self.real_diff("six.txt"))
        more = "".join("n%d\n" % n for n in range(6001))[:-1]
        self.repo_.write("six.txt", more)
        self.repo_.stage("six.txt")
        shown = code_review.parse_file_diff(self.real_diff("six.txt"))
        self.assertEqual((shown["truncated"], shown["lines_total"],
                          shown["lines_retained"]), (True, 6001, 6000))
        self.assertNotEqual(shown["hunks"][-1]["lines"][-1]["kind"],
                            "no_newline", "nothing after a cut is kept")

    def test_the_producer_counts_what_the_office_counts_utf16_code_units(self):
        # ROUND 23. A character beyond the BMP is ONE code point and TWO UTF-16 code units; the office's
        # every bound is JavaScript's .length, so the producer must measure, and cut, in that unit.
        astral = "\U0001F600"
        self.assertEqual(code_review._units("abc"), 3)
        self.assertEqual(code_review._units("\u00e9\u4e2d"), 2)           # BMP: one unit each
        self.assertEqual(code_review._units(astral), 2)
        self.assertEqual(code_review._units("a" + astral + "\ud800"), 4)    # a lone half counts one: as JavaScript does
        kept, omitted = code_review._clip_units(astral * 601, 1200)
        self.assertEqual((kept, omitted), (astral * 600, 1))
        kept, omitted = code_review._clip_units("a" * 1199 + astral + "tail", 1200)
        self.assertEqual((kept, omitted), ("a" * 1199, 5), "an astral that does not fit is left out WHOLE")
        self.assertEqual(code_review._clip_units("short", 1200), ("short", 0))
        # a path, a branch and a section are bounded in the same unit
        self.assertTrue(code_review._safe_path("a/" + astral * 511))        # 2 + 1,022 = 1,024 units
        self.assertFalse(code_review._safe_path("a/" + astral * 512))       # 2 + 1,024 = 1,026 units, 513 characters
        self.assertTrue(code_review._safe_path("a/" + "\u4e2d" * 1022))     # BMP: 1,024 units
        self.assertFalse(code_review._safe_path("a/" + "\u4e2d" * 1023))

    def test_clipping_never_splits_a_character_and_is_maximal(self):
        import random
        alphabet = ["a", "\u00e9", "\u4e2d", "\U0001F600", "\U00010000", "\n"]
        rng = random.Random(23)
        for _ in range(400):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
            limit = rng.randint(1, 12)
            kept, omitted = code_review._clip_units(text, limit)
            self.assertTrue(text.startswith(kept), (text, limit))
            self.assertLessEqual(code_review._units(kept), limit, (text, limit))
            kept.encode("utf-16-le")                 # strict: a lone half of a pair would raise
            self.assertEqual(omitted, len(text) - len(kept), (text, limit))
            if omitted:
                self.assertGreater(code_review._units(text[:len(kept) + 1]), limit,
                                   (text, limit, "maximal: the next character would not fit"))

    def test_the_retained_text_is_measured_in_the_offices_unit(self):
        astral = "\U0001F600"
        # 4 astral = 8 units per line: three lines are 24, a fourth would be 32 > 25
        body = (astral * 4 + "\n") * 4
        with mock.patch.object(code_review, "MAX_REVIEW_FILE_CHARS", 25):
            result = code_review.synthesize_new_file(body)
        self.assertEqual((result["lines_retained"], result["lines_total"], result["truncated"]), (3, 4, True))
        with mock.patch.object(code_review, "MAX_REVIEW_LINE_CHARS", 10):
            result = code_review.synthesize_new_file(astral * 6 + "\n")
        line = result["hunks"][0]["lines"][0]
        self.assertEqual((line["text"], line["clipped"]), (astral * 5, 1))

    def test_a_line_and_its_end_of_file_marker_are_kept_together_or_cut_together(self):
        # ROUND 22 F2. The marker's characters count against the character bound
        # (the office counts them), and it belongs to the line before it: the line
        # is kept only if it AND its marker fit. Never the marker silently dropped
        # while its line stays: that loses "no newline at end of file" with nothing
        # said (the office accepts it). A line cut with its marker is a truncation
        # like any other, and is reported.
        marker = len(" No newline at end of file")
        body = "x" * 10 + "\n" + "y" * 20                 # 30 characters
        for cap, together in ((30 + marker, True), (30 + marker - 1, False)):
            self.repo_.write("cap.txt", body)
            self.repo_.stage("cap.txt")
            diff = self.real_diff("cap.txt")
            with mock.patch.object(code_review, "MAX_REVIEW_FILE_CHARS", cap):
                shown = code_review.parse_file_diff(diff)
                synthetic = code_review.synthesize_new_file(body)
            for label, result in (("diff", shown), ("untracked", synthetic)):
                with self.subTest(cap=cap, label=label):
                    kinds = [l["kind"] for h in result["hunks"]
                             for l in h["lines"]]
                    self.assertEqual(result["lines_total"], 2)
                    self.assertEqual("no_newline" in kinds, together)
                    self.assertEqual(result["truncated"], not together,
                                     "the cut is said when it happens")
                    self.assertEqual(result["lines_retained"],
                                     2 if together else 1)
                    self.assertLessEqual(
                        sum(len(l["text"]) for h in result["hunks"]
                            for l in h["lines"]), cap)
                    if not together:
                        self.assertNotIn("no_newline", kinds,
                                         "a cut line leaves no marker behind")

    def test_what_is_not_understood_is_never_guessed(self):
        good = self.real_diff("src/util.py")
        cases = {
            "an unknown header": good.replace(b"index ", b"surprise ", 1),
            "a hunk longer than its header": good.replace(
                b"@@ -1,3 +1,4 @@", b"@@ -1,2 +1,4 @@"),
            "a hunk shorter than its header": good.replace(
                b"@@ -1,3 +1,4 @@", b"@@ -1,9 +1,4 @@"),
            "two files": good + good,
            "not a diff": b"hello\n",
            "an odd line": good + b"?stray\n",
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(code_review._Unparsed):
                    code_review.parse_file_diff(data)

    def test_a_diff_that_is_not_utf8_is_its_own_state(self):
        self.repo_.write("src/util.py", b"caf\xe9\n")
        with self.assertRaises(code_review._NotUtf8):
            code_review.parse_file_diff(self.real_diff("src/util.py"))

    def test_an_untracked_file_is_shown_as_all_additions(self):
        shown = code_review.synthesize_new_file("a\nb\n")
        self.assertEqual((shown["additions"], shown["deletions"],
                          shown["lines_total"]), (2, 0, 2))
        hunk = shown["hunks"][0]
        self.assertEqual((hunk["old_start"], hunk["old_lines"],
                          hunk["new_start"], hunk["new_lines"]), (0, 0, 1, 2))
        self.assertEqual([l["new"] for l in hunk["lines"]], [1, 2])
        noeol = code_review.synthesize_new_file("a\nb")
        self.assertEqual(noeol["hunks"][0]["lines"][-1]["kind"], "no_newline")
        self.assertEqual(code_review.synthesize_new_file("")["hunks"], [])


class PolicyTests(Bounded):

    def test_what_is_withheld_and_what_is_shown(self):
        withheld = {
            ".env": "private_location", "app/.env.local": "private_location",
            ".herd/state/task.json": "private_location",
            ".git/config": "private_location",
            ".claude/settings.json": "private_location",
            ".ssh/id_rsa": "private_location",
            "src/.hidden/x.py": "hidden_name_not_listed",
            ".secrets-dir/a.txt": "hidden_name_not_listed",
            "config/credentials.json": "credential_like_name",
            "src/password_store.py": "credential_like_name",
            "keys/server.pem": "key_material_extension",
            "deploy/app.p12": "key_material_extension",
            "src/tokenizer.py": "credential_like_name",
        }
        for path, reason in withheld.items():
            with self.subTest(path=path):
                self.assertEqual(code_review.withheld_reason(path), reason)
        for path in (".github/workflows/ci.yml", ".gitignore", ".gitleaks.toml",
                     ".eslintrc.json", ".prettierrc", ".vscode/settings.json",
                     "src/app.py", "docs/guide.md", "README.md", "a/b/c.tsx",
                     ".editorconfig", "tests/test_x.py"):
            with self.subTest(path=path):
                self.assertIsNone(code_review.withheld_reason(path))

    def test_the_credential_and_key_lists_are_the_evidence_librarys(self):
        self.assertEqual(code_review.CREDENTIAL_NAME_PARTS,
                         artifact_content.CREDENTIAL_NAME_PARTS)
        self.assertEqual(code_review.KEY_MATERIAL_EXTENSIONS,
                         artifact_content.KEY_MATERIAL_EXTENSIONS)

    def test_the_request_grammar_is_the_routes_re_declaration(self):
        declared = dict((n, g.pattern) for n, g in
                        catalog_http._CODE_REVIEW_FIELDS)
        self.assertEqual(declared["mission_ref"],
                         code_review.MISSION_REF_PATTERN)
        self.assertEqual(declared["task_id"], code_review.TASK_ID_PATTERN)
        self.assertEqual(declared["snapshot"], code_review.SNAPSHOT_PATTERN)
        self.assertEqual(declared["file_id"], code_review.FILE_ID_PATTERN)
        self.assertEqual(code_review.MISSION_REF_PATTERN,
                         artifact_content.MISSION_REF_PATTERN)

    def test_every_untracked_reader_status_is_mapped(self):
        statuses = set(prepare_module.INSTRUCTION_STATUSES) - {
            prepare_module.INSTRUCTION_READ, prepare_module.INSTRUCTION_ABSENT}
        self.assertEqual(statuses, set(code_review._UNTRACKED_REFUSALS))

    def test_the_problem_vocabulary_is_closed_and_unique(self):
        self.assertEqual(len(set(code_review.PROBLEMS)),
                         len(code_review.PROBLEMS))
        with self.assertRaises(ValueError):
            code_review.refusal("made_up", "x", "overview", MISSION_REF, TASK,
                                None, None)


class OfficeParityTests(Bounded):
    """The office (TypeScript) re-declares the backend's vocabulary, bounds and
    request grammar; it reads from its own source, so the two cannot drift."""

    SERVER = REPO_ROOT / "integrations" / "pixel-agents" / "server" / "src" / "di"

    def ts(self, name):
        return (self.SERVER / name).read_text()

    def test_the_closed_problem_vocabulary_is_the_same_list(self):
        text = self.ts("codeReview.ts")
        block = text.split("// BEGIN CODE_REVIEW_PROBLEMS", 1)[1].split(
            "// END CODE_REVIEW_PROBLEMS", 1)[0]
        office = re.findall(r"'([a-z0-9_]+)',", block)
        self.assertEqual(office, list(code_review.PROBLEMS))

    def test_the_bounds_are_the_same_numbers(self):
        text = self.ts("codeReview.ts")

        def value(name):
            match = re.search(r"export const %s = ([0-9_]+);" % name, text)
            self.assertIsNotNone(match, name)
            return int(match.group(1).replace("_", ""))
        for ts_name, python_value in (
                ("MAX_REVIEW_FILES", code_review.MAX_REVIEW_FILES),
                ("MAX_REVIEW_PATH_CHARS", code_review.MAX_REVIEW_PATH_CHARS),
                ("MAX_REVIEW_FILE_LINES", code_review.MAX_REVIEW_FILE_LINES),
                ("MAX_REVIEW_FILE_CHARS", code_review.MAX_REVIEW_FILE_CHARS),
                ("MAX_REVIEW_LINE_CHARS", code_review.MAX_REVIEW_LINE_CHARS),
                ("MAX_REVIEW_SECTION_CHARS", code_review.MAX_SECTION_CHARS),
                ("MAX_REVIEW_BRANCH_CHARS", code_review.MAX_BRANCH_CHARS),
                ("MAX_REVIEW_FILTERS",
                 git_transport.MAX_REVIEW_FILTER_DRIVERS)):
            self.assertEqual(value(ts_name), python_value, ts_name)

    def test_the_request_grammar_is_the_same_patterns(self):
        text = self.ts("backendClient.ts")
        block = text.split("export const CODE_REVIEW_GRAMMAR", 1)[1].split(
            "} as const", 1)[0]
        patterns = dict(re.findall(r"(\w+): /\^(.*)\$/,", block))
        self.assertEqual(patterns["taskId"], code_review.TASK_ID_PATTERN)
        self.assertEqual(patterns["snapshot"], code_review.SNAPSHOT_PATTERN)
        self.assertEqual(patterns["fileId"], code_review.FILE_ID_PATTERN)
        reference = text.split("export const ARTIFACT_REFERENCE_GRAMMAR", 1)[1]
        mission = re.search(r"missionRef: /\^(.*)\$/", reference).group(1)
        self.assertEqual(mission, code_review.MISSION_REF_PATTERN)

    def test_the_closed_enumerations_are_the_same_lists(self):
        text = self.ts("codeReview.ts")

        def listed(name):
            block = text.split("export const %s = [" % name, 1)[1].split(
                "] as const;", 1)[0]
            return re.findall(r"'([a-z_0-9]+)'", block)
        self.assertEqual(listed("REVIEW_CHANGES"), list(code_review.CHANGES))
        self.assertEqual(listed("REVIEW_STAGINGS"), list(code_review.STAGINGS))
        self.assertEqual(listed("REVIEW_FILE_STATES"),
                         list(code_review.FILE_STATES))
        self.assertEqual(listed("REVIEW_LINE_KINDS"),
                         list(code_review.LINE_KINDS))
        reasons = [code_review.REASON_PRIVATE, code_review.REASON_HIDDEN,
                   code_review.REASON_CREDENTIAL,
                   code_review.REASON_KEY_MATERIAL,
                   code_review.REASON_NOT_REGULAR,
                   code_review.REASON_UNREADABLE, code_review.REASON_HARDLINK,
                   code_review.REASON_TYPE_CHANGE,
                   code_review.REASON_NESTED_REPOSITORY]
        self.assertEqual(listed("REVIEW_REASONS"), reasons)

        def plain(name):
            block = text.split("const %s = [" % name, 1)[1].split("];", 1)[0]
            return re.findall(r"'([a-z_0-9]+)'", block)
        self.assertEqual(plain("COVERS"), list(code_review.COVERS))
        self.assertEqual(plain("DOES_NOT_COVER"),
                         list(code_review.DOES_NOT_COVER))
        self.assertEqual(plain("SHOWN_STATES"), list(code_review.SHOWN_STATES))

    def test_the_answer_key_sets_are_the_same(self):
        text = self.ts("codeReview.ts")

        def keys(name):
            block = text.split("const %s = [" % name, 1)[1].split("];", 1)[0]
            return sorted(re.findall(r"'([a-z_0-9]+)'", block))
        self.assertEqual(keys("ANSWER_KEYS"), sorted(code_review.ANSWER_KEYS))
        self.assertEqual(keys("TARGET_KEYS"), sorted(code_review.TARGET_KEYS))
        self.assertEqual(keys("COMPARISON_KEYS"),
                         sorted(code_review.COMPARISON_KEYS))
        self.assertEqual(keys("OVERVIEW_KEYS"),
                         sorted(code_review.OVERVIEW_KEYS))
        self.assertEqual(keys("ENTRY_KEYS"),
                         sorted(code_review.OVERVIEW_FILE_KEYS))
        self.assertEqual(keys("TOTALS_KEYS"), sorted(code_review.TOTALS_KEYS))
        self.assertEqual(keys("HUNK_KEYS"), sorted(code_review.HUNK_KEYS))
        self.assertEqual(keys("LINE_KEYS"), sorted(code_review.LINE_KEYS))
        self.assertEqual(keys("ACCOUNTING_KEYS"),
                         sorted(code_review.ACCOUNTING_KEYS))
        file_keys = sorted(set(re.findall(
            r"'([a-z_0-9]+)'", text.split("const FILE_KEYS = [", 1)[1].split(
                "];", 1)[0])) | set(code_review.OVERVIEW_FILE_KEYS))
        self.assertEqual(file_keys, sorted(code_review.FILE_KEYS))


if __name__ == "__main__":
    unittest.main()
