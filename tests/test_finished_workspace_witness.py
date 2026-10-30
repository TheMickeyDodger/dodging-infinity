"""I2, LAYER 1: the WORKSPACE-SIDE WITNESS of a FINISHED (COMPLETED) Mission's live code review.

Hermetic: product modules only (the legacy observer ``herdr.observe`` is imported ONLY as the comparator of the two-claim parity test, and the route never calls it); the helpers of
the I1 Layer 1 module are imported by MODULE; every workspace is a real temporary directory inside the pin's temp root; no process, no git, no network, no home directory.

What it pins. After the record-side proof, the review route reads the recorded workspace's OWN task record (``.herd/state/task.json``) through the hardened no-follow state reader and
requires that it names THIS run: ``run_association.names_this_run`` (the one shared predicate) over the identity-bearing fields, AND the record's task id equal to the verified receipt's
(a separate check: the predicate does not compare ids). Every refusal is ``finished_workspace_not_corroborated``; the read is charged to ``untracked_bytes`` at the existing read cost against
its existing cap and is repeated at the release-time bracket, where a change is seen. ONE predicate and TWO readers: the parity test is TWO CLAIMS (agreement of the projected identity
fields and of the predicate on the common admissible domain; the hardened reader's STRICTER refusals declared and tested as their own expected outcomes).

I2 ALONE DOES NOT MAKE THE CONTROL WORK: no offer can exist for a finished Mission until the offered flow (I3) is integrated.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_finished_code_review as l1  # noqa: E402  (my own I1 Layer 1 module, imported by MODULE for its fixtures)
from herdr import observe as legacy_observer  # noqa: E402  (the COMPARATOR of the parity test only)
from target_runtime import code_review  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import run_association  # noqa: E402

WITNESS_REASON = "finished_workspace_not_corroborated"
INTENT = {"revision": 3, "proposal_digest_sha256": "d" * 64, "recorded_at": 1000}


def good_record(task=l1.TASK, revision=3, digest="d" * 64, mission_id=l1.NATIVE, started=1000, tail=" The rest of the handoff.", **over):
    record = {"id": task, "status": "running", "started_at": started,
              "description": run_association.handoff_prefix(mission_id, revision, digest) + tail}
    record.update(over)
    return record


class Workspace(object):
    """A real temporary workspace directory (its real path), with an optional ``.herd/state/task.json``."""

    def __init__(self, text=None, record=None, data=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self.tmp.name)
        if record is not None:
            text = json.dumps(record)
        if text is not None or data is not None:
            self.write(text, data)

    def write(self, text=None, data=None):
        directory = os.path.join(self.path, ".herd", "state")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "task.json"), "wb") as handle:
            handle.write(data if data is not None else text.encode("utf-8"))

    def task_path(self):
        return os.path.join(self.path, ".herd", "state", "task.json")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


def proof_for(workspace, **over):
    p = l1.proof(workspace=workspace)
    p.update(over)
    return p


def corroborate(workspace, p=None, accounting=None, task=l1.TASK):
    accounting = code_review.Accounting() if accounting is None else accounting
    p = proof_for(workspace) if p is None else p
    held = l1.answer([l1.mission(workspace=workspace)])
    bound = code_review._corroborated(held, l1.fresh, l1.NOW, l1.REF, task, finished=l1.Provider((p, 11, None)), accounting=accounting)
    return bound, accounting


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(code_review, "FINISHED_WORKSPACE_NOT_CORROBORATED"),
                        "FEATURE MISSING: code_review.FINISHED_WORKSPACE_NOT_CORROBORATED does not exist (the workspace-side witness is not built)")

    def refused(self, workspace, p=None, accounting=None, problem=WITNESS_REASON):
        with self.assertRaises(code_review._Refused) as caught:
            corroborate(workspace, p, accounting)
        self.assertEqual(caught.exception.problem, problem, caught.exception.detail)
        return caught.exception

    def test_a_workspace_task_record_that_names_this_run_corroborates_and_is_carried_in_the_bound(self):
        with Workspace(record=good_record()) as ws:
            bound, accounting = corroborate(ws.path)
            size = os.path.getsize(ws.task_path())
        self.assertEqual(sorted(bound["witness"]), ["description", "digest_sha256", "id", "started_at"])
        self.assertEqual((bound["witness"]["id"], bound["witness"]["started_at"]), (l1.TASK, 1000))
        self.assertEqual(accounting.untracked_bytes, size, "the witness read is charged to untracked_bytes at its actual size")
        self.assertEqual(accounting.registry_bytes, 11, "the record-side bytes stay where they were")

    def test_the_task_record_must_exist_be_a_json_object_and_be_readable(self):
        with Workspace() as ws:                                    # no task.json at all
            self.refused(ws.path)
        for label, text in (("not JSON", "{nope"), ("a list", "[1, 2]"), ("a string", '"x"'), ("null", "null"), ("empty", "")):
            with self.subTest(content=label), Workspace(text=text) as ws:
                self.refused(ws.path)
        with Workspace(data=b"\xff\xfe\x00 not utf-8") as ws:
            self.refused(ws.path)
        with Workspace(data=b"{" + b" " * (prepare_module.MAX_INSTRUCTION_FILE_BYTES + 1) + b"}") as ws:
            self.refused(ws.path)

    def test_every_way_the_record_can_fail_to_name_this_run_is_refused(self):
        other = "e" * 64
        cases = [
            ("another revision", good_record(revision=4)),
            ("another proposal digest", good_record(digest=other)),
            ("another mission", good_record(mission_id="mn-" + "c" * 32)),
            ("no description", dict((k, v) for k, v in good_record().items() if k != "description")),
            ("description not a string", good_record(description=17)),
            ("description is a bare different sentence", good_record(description="AUTHORIZED MISSION mn-x")),
            ("started before the intent was recorded", good_record(started=999)),
            ("no started_at", dict((k, v) for k, v in good_record().items() if k != "started_at")),
            ("started_at a boolean", good_record(started=True)),
            ("started_at a float", good_record(started=1000.5)),
            ("started_at a string", good_record(started="1000")),
        ]
        for label, record in cases:
            with self.subTest(case=label), Workspace(record=record) as ws:
                self.refused(ws.path)

    def test_started_exactly_at_the_recording_is_not_early(self):
        with Workspace(record=good_record(started=1000)) as ws:
            corroborate(ws.path)

    def test_the_task_id_must_equal_the_verified_receipts_SEPARATELY_from_the_shared_predicate(self):
        # The predicate (names_this_run) does not compare ids; this check is the route's own. A record that names this run perfectly but is another task is refused.
        record = good_record(task=l1.OTHER_TASK)
        self.assertTrue(run_association.names_this_run({"task": {"description": record["description"], "started_at": record["started_at"]}}, l1.NATIVE, INTENT),
                        "precondition: the shared predicate is satisfied by this record")
        with Workspace(record=record) as ws:
            self.refused(ws.path)
        for label, record in (("no id", dict((k, v) for k, v in good_record().items() if k != "id")), ("id not a string", good_record(id=5))):
            with self.subTest(case=label), Workspace(record=record) as ws:
                self.refused(ws.path)

    def test_a_record_side_proof_with_no_usable_run_intent_is_refused_rather_than_guessed(self):
        with Workspace(record=good_record()) as ws:
            for label, change in (("no intent", {"intent": None}), ("no mission id", {"mission_id": None}), ("intent not a dict", {"intent": "x"}),
                                  ("revision not an int", {"intent": dict(INTENT, revision="3")}), ("revision a bool", {"intent": dict(INTENT, revision=True)}),
                                  ("digest not a string", {"intent": dict(INTENT, proposal_digest_sha256=5)}),
                                  ("recorded_at not an int", {"intent": dict(INTENT, recorded_at=1000.0)})):
                with self.subTest(case=label):
                    self.refused(ws.path, proof_for(ws.path, **change))

    def test_a_symlinked_task_record_or_directory_chain_is_refused(self):
        with Workspace(record=good_record()) as target, Workspace() as linked, Workspace() as chained:
            os.makedirs(os.path.join(linked.path, ".herd", "state"))
            os.symlink(target.task_path(), os.path.join(linked.path, ".herd", "state", "task.json"))
            self.refused(linked.path)
            os.symlink(os.path.join(target.path, ".herd"), os.path.join(chained.path, ".herd"))
            self.refused(chained.path)

    def test_a_file_that_was_read_is_charged_its_bytes_and_a_refused_read_keeps_its_hold(self):
        with Workspace(text="{nope") as ws:                        # READ (5 bytes) and then judged malformed: charged what was read
            accounting = code_review.Accounting()
            self.refused(ws.path, accounting=accounting)
            self.assertEqual(accounting.untracked_bytes, len("{nope"))
        with Workspace() as ws:                                    # an absent record is a REFUSED read: it keeps the hold (the bound and the byte past it)
            accounting = code_review.Accounting()
            self.refused(ws.path, accounting=accounting)
            self.assertEqual(accounting.untracked_bytes, code_review.MAX_UNTRACKED_READ_COST_BYTES)
        with Workspace(data=b"{" + b" " * (prepare_module.MAX_INSTRUCTION_FILE_BYTES + 1) + b"}") as ws:     # over the bound: refused, holds
            accounting = code_review.Accounting()
            self.refused(ws.path, accounting=accounting)
            self.assertEqual(accounting.untracked_bytes, code_review.MAX_UNTRACKED_READ_COST_BYTES)

    def test_the_budget_refuses_before_the_witness_is_read_when_it_cannot_cover_the_read(self):
        with Workspace(record=good_record()) as ws:
            accounting = code_review.Accounting()
            accounting.untracked_bytes = code_review.MAX_UNTRACKED_TOTAL_BYTES - code_review.MAX_UNTRACKED_READ_COST_BYTES + 1
            before = accounting.untracked_bytes
            self.refused(ws.path, accounting=accounting, problem=code_review.PROBLEM_BUDGET)
            self.assertEqual(accounting.untracked_bytes, before, "nothing was read, so nothing more was charged")

    def test_the_reason_is_a_closed_code_and_is_produced_only_by_the_witness(self):
        # CHANGED IN I3 (was: test_the_reason_is_internal_and_is_produced_only_by_the_witness, which pinned that the closed list did NOT hold it and refusal() rejected it).
        self.assertIn(WITNESS_REASON, code_review.FINISHED_REASONS)
        self.assertIn(WITNESS_REASON, code_review.PROBLEMS, "the closed vocabulary gained it with the TypeScript lists together, in I3")
        self.assertNotIn(WITNESS_REASON, set(code_review.FINISHED_REFUSALS.values()), "no refusal of the record-side entry maps to the witness reason")
        answer = code_review.refusal(WITNESS_REASON, "x", code_review.VIEW_OVERVIEW, l1.REF, l1.TASK, None, None)
        self.assertEqual((answer["state"], answer["problem"]), ("refused", WITNESS_REASON))


class BracketTests(unittest.TestCase):
    """The workspace-side half of the release-time bracket, by HANDSHAKE (the test changes the record between the two corroborations; no timer)."""

    def setUp(self):
        self.assertTrue(hasattr(code_review, "FINISHED_WORKSPACE_NOT_CORROBORATED"),
                        "FEATURE MISSING: the workspace-side witness is not built")

    def run_bracket(self, workspace, change):
        accounting = code_review.Accounting()
        held = l1.answer([l1.mission(workspace=workspace)])
        provider = l1.Provider((proof_for(workspace), 1, None))
        bound = code_review._corroborated(held, l1.fresh, l1.NOW, l1.REF, l1.TASK, finished=provider, accounting=accounting)
        first_untracked = accounting.untracked_bytes
        change()
        try:
            code_review._revalidated(l1.FakeRegistry([l1.registration()]), lambda: held, l1.fresh, l1.clock, bound, accounting, l1.SentinelTransport(),
                                     workspace, {}, l1.REF, l1.TASK, finished=provider)
        finally:
            self.untracked = (first_untracked, accounting.untracked_bytes)

    def test_the_witness_is_read_again_at_the_release_check_and_charged_again(self):
        with Workspace(record=good_record()) as ws:
            with self.assertRaises(l1.Reached):                  # unchanged: the bracket passes and the workspace observation is reached
                self.run_bracket(ws.path, lambda: None)
        first, second = self.untracked
        self.assertGreater(first, 0)
        self.assertEqual(second, 2 * first, "the release-time witness read is charged like the first")

    def test_a_record_rewritten_to_name_another_run_between_the_two_reads_is_refused_with_its_own_reason(self):
        with Workspace(record=good_record()) as ws:
            with self.assertRaises(code_review._Refused) as caught:
                self.run_bracket(ws.path, lambda: ws.write(json.dumps(good_record(revision=9))))
        self.assertEqual(caught.exception.problem, WITNESS_REASON)

    def test_a_record_that_still_names_the_run_but_is_another_reading_is_assignment_changed_during_read(self):
        with Workspace(record=good_record()) as ws:
            with self.assertRaises(code_review._Refused) as caught:
                self.run_bracket(ws.path, lambda: ws.write(json.dumps(good_record(tail=" A different tail, same run."))))
        self.assertEqual(caught.exception.problem, code_review.PROBLEM_ASSIGNMENT_CHANGED)

    def test_a_record_that_disappears_between_the_two_reads_is_refused(self):
        with Workspace(record=good_record()) as ws:
            with self.assertRaises(code_review._Refused) as caught:
                self.run_bracket(ws.path, lambda: os.unlink(ws.task_path()))
        self.assertEqual(caught.exception.problem, WITNESS_REASON)


def legacy_projection(workspace):
    section, _task_id = legacy_observer._task_section(Path(workspace), 0, [])
    return {"state": section["state"], "id": section["id"], "description": section["description"], "started_at": section["started_at"]}


class ParityTests(unittest.TestCase):
    """ONE predicate and TWO readers, as TWO CLAIMS. Claim A: on the COMMON ADMISSIBLE DOMAIN (what both readers accept) the identity-bearing projected fields and the shared
    predicate agree. Claim B: the hardened reader's STRICTER refusals are declared and tested as their own expected outcomes; it is never weakened to force whole-section equality.
    A finite battery establishes only its own cases, not universal observational equivalence."""

    def setUp(self):
        self.assertTrue(hasattr(code_review, "FINISHED_WORKSPACE_NOT_CORROBORATED"),
                        "FEATURE MISSING: the workspace-side witness is not built")

    def review_projection(self, workspace):
        accounting = code_review.Accounting()
        record = code_review._task_record(workspace, accounting)
        return dict((key, record[key]) for key in ("id", "description", "started_at"))

    def predicate(self, projection):
        return run_association.names_this_run({"task": {"description": projection["description"], "started_at": projection["started_at"]}}, l1.NATIVE, INTENT)

    def test_claim_A_agreement_on_the_common_admissible_domain(self):
        long_tail = "x" * 400
        battery = [
            ("a valid record", good_record()),
            ("a description longer than the 200-character projection bound", good_record(tail=long_tail)),
            ("a description exactly the prefix", good_record(tail="")),
            ("a non-ASCII description", good_record(tail=" café ☃ 世界")),
            ("another revision", good_record(revision=4)),
            ("started before the recording", good_record(started=999)),
            ("started_at a boolean", good_record(started=True)),
            ("started_at a float", good_record(started=1000.5)),
            ("started_at a string", good_record(started="1000")),
            ("no started_at", dict((k, v) for k, v in good_record().items() if k != "started_at")),
            ("no description", dict((k, v) for k, v in good_record().items() if k != "description")),
            ("description not a string", good_record(description=3)),
            ("id not a string", good_record(id=7)),
            ("no id", dict((k, v) for k, v in good_record().items() if k != "id")),
            ("an id longer than the bound", good_record(id="t" * 300)),
            ("extra keys", good_record(policy={"rules": [1, 2]}, heartbeat_count=4)),
        ]
        for label, record in battery:
            with self.subTest(case=label), Workspace(record=record) as ws:
                legacy = legacy_projection(ws.path)
                self.assertEqual(legacy["state"], "available", "precondition: the legacy reader accepts this record (it is on the common domain)")
                review = self.review_projection(ws.path)
                self.assertEqual(review, dict((key, legacy[key]) for key in ("id", "description", "started_at")))
                self.assertEqual(self.predicate(review), self.predicate(legacy))

    def test_claim_B_the_stricter_refusals_are_declared_and_the_hardened_reader_is_not_weakened(self):
        declared = []
        # (1) over the hardened reader's 64 KiB bound (the legacy reader reads far more): the review route refuses.
        padded = json.dumps(good_record(note="n" * (prepare_module.MAX_INSTRUCTION_FILE_BYTES + 10)))
        with Workspace(text=padded) as ws:
            declared.append(("over the 64 KiB bound", legacy_projection(ws.path)["state"], self.review_refuses(ws.path)))
        # (2) not UTF-8: the legacy reader decodes with replacement; the review route refuses.
        raw = json.dumps(good_record(note="Z")).encode("utf-8").replace(b'"Z"', b'"\xff"')
        with Workspace(data=raw) as ws:
            declared.append(("not UTF-8", legacy_projection(ws.path)["state"], self.review_refuses(ws.path)))
        # (3) a symlinked task record: the legacy reader follows it; the review route refuses.
        with Workspace(record=good_record()) as target, Workspace() as ws:
            os.makedirs(os.path.join(ws.path, ".herd", "state"))
            os.symlink(target.task_path(), os.path.join(ws.path, ".herd", "state", "task.json"))
            declared.append(("a symlinked task.json", legacy_projection(ws.path)["state"], self.review_refuses(ws.path)))
        # (4) a hard-linked task record: the legacy reader reads it; the review route refuses.
        with Workspace(record=good_record()) as target, Workspace() as ws:
            os.makedirs(os.path.join(ws.path, ".herd", "state"))
            os.link(target.task_path(), os.path.join(ws.path, ".herd", "state", "task.json"))
            declared.append(("a hard-linked task.json", legacy_projection(ws.path)["state"], self.review_refuses(ws.path)))
        # (5) a symlinked directory in the chain: the legacy reader follows it; the review route refuses.
        with Workspace(record=good_record()) as target, Workspace() as ws:
            os.symlink(os.path.join(target.path, ".herd"), os.path.join(ws.path, ".herd"))
            declared.append(("a symlinked .herd directory", legacy_projection(ws.path)["state"], self.review_refuses(ws.path)))
        for label, legacy_state, refused in declared:
            with self.subTest(case=label):
                self.assertTrue(refused, "the hardened reader must REFUSE this (a stricter refusal, declared)")
                self.assertEqual(legacy_state, "available", "the legacy reader's own outcome is stated, not assumed: it accepts the same file")

    def review_refuses(self, workspace):
        try:
            self.review_projection(workspace)
        except code_review._Refused as exc:
            return exc.problem == WITNESS_REASON
        return False


if __name__ == "__main__":
    unittest.main()
