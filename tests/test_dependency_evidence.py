"""A COMPLETED child task resolves from its own DURABLE evidence once a second task has started; the MISMATCH rule is NOT weakened.

The dependency evaluation compares each recorded task id with the child's CURRENT task id. Starting a second task in the same
child therefore made the completed, accepted first task read MISMATCH ("Child is no longer reporting the task recorded by the
parent"): a fault that is not one. The fix resolves the first task from the record the child itself wrote when it completed it
(`.herd/state/tasks/<id>.json`, written by `herdctl task-complete`), and ONLY from that. A genuinely wrong child (no archived
record, a record that names another repository or another id, one that is not COMPLETE, one that is not a plain regular file) is
still MISMATCH, exactly as before. Contained synthetic directories only; no real runtime.
"""

import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _follow_on_fixture import (  # noqa: E402
    FIRST,
    PARENT_TASK,
    SECOND,
    Fixture,
    read_json,
    sha256_file,
    write_json,
)
from herdr.dependencies import (  # noqa: E402
    ANCHOR_FILE,
    assert_child_dependencies_complete,
    identities_of,
    missing_registrations,
    read_registration_anchor,
    register_identities,
    registration_identity,
    replace_registration,
    set_registration_anchor,
    child_dependencies,
    durable_complete_evidence,
)
from herdr.instance import HerdrInstance  # noqa: E402


class DurableEvidenceDependencyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.parent = HerdrInstance(self.fx.parent)

    def dependencies(self):
        return child_dependencies(self.parent, PARENT_TASK)

    def by_task(self):
        return {d["task_id"]: d for d in self.dependencies()}

    # -- the claim: a completed task survives a second task starting ---------------------------------------------

    def test_the_first_task_still_resolves_while_it_is_the_childs_current_task(self):
        found = self.dependencies()
        self.assertEqual([d["current_status"] for d in found], ["COMPLETE"])
        self.assertNotIn("resolved_from", found[0], "a task that IS current resolves as it always did")

    def test_a_completed_task_resolves_from_its_durable_evidence_after_a_second_task_starts(self):
        self.fx.second_task_started()
        found = self.dependencies()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["task_id"], FIRST)
        self.assertEqual(found[0]["current_status"], "COMPLETE", found[0].get("dependency_error"))
        self.assertEqual(found[0]["resolved_from"], "durable_evidence")
        self.assertEqual(found[0]["current_task_id"], SECOND, "the child's CURRENT task is still reported, not hidden")
        self.assertNotIn("dependency_error", found[0])

    def test_the_resolution_names_the_evidence_it_stood_on_and_its_digest(self):
        self.fx.second_task_started()
        evidence = self.dependencies()[0]["evidence"]
        archive = self.fx.archive_path(FIRST)
        self.assertEqual(evidence["path"], str(archive))
        self.assertEqual(evidence["sha256"], sha256_file(archive), "auditable: the digest is the archive's own")
        self.assertEqual(evidence["completed_at"], 2)

    def test_both_tasks_are_true_at_once_and_only_the_unresolved_one_blocks_the_parent(self):
        self.fx.second_task_started("ACTIVE")
        self.fx.register_second()
        found = self.by_task()
        self.assertEqual(found[FIRST]["current_status"], "COMPLETE")
        self.assertEqual(found[SECOND]["current_status"], "ACTIVE")
        with self.assertRaises(RuntimeError) as blocked:
            assert_child_dependencies_complete(self.parent, PARENT_TASK)
        self.assertIn(SECOND, str(blocked.exception))
        self.assertNotIn(FIRST, str(blocked.exception), "the completed task is not named as unresolved")

    def test_the_second_task_completing_releases_the_parent(self):
        self.fx.second_task_started("COMPLETE")
        self.fx.register_second()
        assert_child_dependencies_complete(self.parent, PARENT_TASK)

    def test_an_errored_second_task_blocks_the_parent(self):
        self.fx.second_task_started("ERROR")
        self.fx.register_second()
        self.assertEqual(self.by_task()[SECOND]["current_status"], "ERROR")
        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(self.parent, PARENT_TASK)

    def test_an_unreadable_current_state_blocks_the_parent_and_is_not_rescued_by_the_evidence(self):
        self.fx.second_task_started("ACTIVE")
        self.fx.register_second()
        self.fx.child_task_json.write_text("{not json")
        found = self.by_task()
        self.assertEqual(found[FIRST]["current_status"], "UNREADABLE")
        self.assertEqual(found[SECOND]["current_status"], "UNREADABLE")
        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(self.parent, PARENT_TASK)

    def test_an_unregistered_current_task_is_a_mismatch_for_the_record_that_names_it_not_for_the_first(self):
        # the child reports a task the parent never registered as the SECOND record's id: that record is a MISMATCH
        self.fx.second_task_started("ACTIVE", task_id="20261027-130000-ffffff")
        self.fx.register_second(task_id=SECOND)
        found = self.by_task()
        self.assertEqual(found[FIRST]["current_status"], "COMPLETE")
        self.assertEqual(found[SECOND]["current_status"], "MISMATCH")
        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(self.parent, PARENT_TASK)

    # -- the MISMATCH rule is NOT weakened -----------------------------------------------------------------------

    def assert_still_mismatch(self, why):
        found = self.dependencies()[0]
        self.assertEqual(found["current_status"], "MISMATCH", why)
        self.assertIn("no longer reporting the task recorded by the parent", found["dependency_error"])
        self.assertNotIn("resolved_from", found)
        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(self.parent, PARENT_TASK)

    def test_no_archived_record_is_still_a_mismatch(self):
        self.fx.second_task_started()
        self.fx.archive_path(FIRST).unlink()
        self.assert_still_mismatch("a child with no durable record of the recorded task is a genuinely wrong child")
        self.assertIn("No durable COMPLETE evidence", self.dependencies()[0]["dependency_error"])

    def test_an_archive_that_is_not_complete_is_still_a_mismatch(self):
        self.fx.second_task_started()
        for status in ("ACTIVE", "ERROR", "ABORTED", "complete", ""):
            with self.subTest(status=status):
                write_json(self.fx.archive_path(FIRST), dict(self.fx.first_record, status=status))
                self.assert_still_mismatch(f"archived status {status!r}")

    def test_an_archive_for_another_task_id_is_still_a_mismatch(self):
        self.fx.second_task_started()
        write_json(self.fx.archive_path(FIRST), dict(self.fx.first_record, id="20261026-000000-other0"))
        self.assert_still_mismatch("the file is named for the recorded task but says it is another")

    def test_an_archive_for_another_repository_is_still_a_mismatch(self):
        self.fx.second_task_started()
        write_json(
            self.fx.archive_path(FIRST),
            dict(self.fx.first_record, repo="/somewhere/else", repo_ref="/somewhere/else"),
        )
        self.assert_still_mismatch("the record names a different repository: a substitute child")

    def test_an_archive_that_names_no_repository_is_still_a_mismatch(self):
        self.fx.second_task_started()
        record = dict(self.fx.first_record)
        del record["repo"]
        del record["repo_ref"]
        write_json(self.fx.archive_path(FIRST), record)
        self.assert_still_mismatch("an identity that cannot be checked is not evidence")

    def test_an_archive_without_a_real_completion_time_is_still_a_mismatch(self):
        self.fx.second_task_started()
        for value in (None, True, "2", -1, 0, 2.5):
            with self.subTest(completed_at=value):
                write_json(self.fx.archive_path(FIRST), dict(self.fx.first_record, completed_at=value))
                self.assert_still_mismatch(f"completed_at={value!r}")

    def test_an_unreadable_or_non_object_archive_is_still_a_mismatch(self):
        self.fx.second_task_started()
        for text in ("{not json", "[]", '"COMPLETE"', "null", ""):
            with self.subTest(text=text):
                self.fx.archive_path(FIRST).write_text(text)
                self.assert_still_mismatch(f"archive content {text!r}")

    def test_a_symlinked_archive_is_not_evidence(self):
        self.fx.second_task_started()
        real = self.fx.root / "elsewhere.json"
        write_json(real, self.fx.first_record)
        archive = self.fx.archive_path(FIRST)
        archive.unlink()
        os.symlink(real, archive)
        self.assert_still_mismatch("a link can point anywhere; only a plain file in the child's own archive counts")

    def link_directory(self, directory, real):
        """Move `directory` (inside the fake child) to `real` (elsewhere in the SAME fixture tree, outside the child), leaving a link."""
        real.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(real)
        os.symlink(real, directory)

    def test_a_link_in_an_ancestor_of_the_archive_that_leaves_the_child_is_not_evidence(self):
        # the archive's own directory is a link to another directory OUTSIDE the child (the case a parent-equality check misses)
        self.fx.second_task_started()
        outside = self.fx.root / "outside-tasks"
        self.link_directory(self.fx.child_state / "tasks", outside)
        self.assertTrue((outside / f"{FIRST}.json").exists(), "the record really is there, behind the link")
        self.assert_still_mismatch("tasks/ is a link out of the child")
        self.assertIn("outside the child's own archive", self.dependencies()[0]["dependency_error"])

    def test_a_link_at_state_or_at_herd_that_leaves_the_child_is_not_evidence(self):
        for name in ("state", ".herd"):
            with self.subTest(link=name):
                fx = Fixture()
                self.addCleanup(fx.cleanup)
                fx.second_task_started()
                target = fx.child / ".herd" / "state" if name == "state" else fx.child / ".herd"
                self.link_directory(target, fx.root / f"outside-{name.strip('.')}")
                result = durable_complete_evidence(fx.child, FIRST)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["reason"], "the archived record is outside the child's own archive")
                found = child_dependencies(HerdrInstance(fx.parent), PARENT_TASK)[0]
                self.assertEqual(found["current_status"], "MISMATCH")
                self.assertNotIn("resolved_from", found)

    def test_a_final_file_link_is_STILL_rejected_after_the_containment_fix(self):
        self.fx.second_task_started()
        real = self.fx.root / "outside-record.json"
        write_json(real, self.fx.first_record)
        archive = self.fx.archive_path(FIRST)
        archive.unlink()
        os.symlink(real, archive)
        self.assertEqual(durable_complete_evidence(self.fx.child, FIRST)["reason"], "the archived record is not a plain file")

    def test_a_link_whose_target_stays_INSIDE_the_child_is_allowed_containment_is_the_rule_not_no_links(self):
        self.fx.second_task_started()
        inside = self.fx.child / "real-tasks"
        self.link_directory(self.fx.child_state / "tasks", inside)
        result = durable_complete_evidence(self.fx.child, FIRST)
        self.assertTrue(result["ok"], result)

    def test_a_task_id_that_is_not_a_plain_name_is_never_looked_up_as_a_path(self):
        for bad in ("../tasks/x", "a/b", "..", "", ".hidden", "x" * 200, "a\x00b", "a b"):
            with self.subTest(task_id=bad):
                result = durable_complete_evidence(self.fx.child, bad)
                self.assertFalse(result["ok"])
                self.assertIn("task id", result["reason"])

    # -- existing states stay what they were ---------------------------------------------------------------------

    def test_a_missing_child_state_is_still_missing_and_an_unreadable_one_is_still_unreadable(self):
        self.fx.child_task_json.unlink()
        self.assertEqual(self.dependencies()[0]["current_status"], "MISSING")
        self.fx.child_task_json.write_text("{")
        self.assertEqual(self.dependencies()[0]["current_status"], "UNREADABLE")

    def test_an_unreadable_registry_still_raises_and_is_never_read_as_empty(self):
        self.fx.children_json.write_text("{not json")
        with self.assertRaises(RuntimeError):
            self.dependencies()

    def test_a_record_with_a_pending_follow_on_dispatch_is_unresolved_whatever_the_child_says(self):
        self.fx.second_task_started("COMPLETE", task_id=SECOND)
        self.fx.register_second(task_id=None, dispatch_state="PENDING", follow_on_of=FIRST)
        pending = [d for d in self.dependencies() if d.get("dispatch_state") == "PENDING"][0]
        self.assertEqual(pending["current_status"], "PENDING")
        self.assertIn("never finalized", pending["dependency_error"])
        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(self.parent, PARENT_TASK)

    def test_a_RECOVERY_record_is_unresolved_whatever_the_child_says_and_only_a_person_resolves_it(self):
        # written by a follow-on that LOST its own record, so that the parent stays blocked; the child's own task state, even a
        # COMPLETE one with a durable archive, does not resolve it
        self.fx.second_task_started("COMPLETE", task_id=SECOND)
        done = dict(read_json(self.fx.child_task_json), completed_at=20)
        write_json(self.fx.child_task_json, done)
        write_json(self.fx.archive_path(SECOND), done)
        self.fx.register_second(task_id=SECOND, dispatch_state="RECOVERY", follow_on_of=FIRST)
        recovery = [d for d in self.dependencies() if d.get("dispatch_state") == "RECOVERY"][0]
        self.assertEqual(recovery["current_status"], "RECOVERY")
        self.assertIn("lost its registry record", recovery["dependency_error"])
        with self.assertRaises(RuntimeError) as caught:
            assert_child_dependencies_complete(self.parent, PARENT_TASK)
        self.assertIn(SECOND, str(caught.exception))
        self.assertIn("RECOVERY", str(caught.exception))
        first = [d for d in self.dependencies() if d["task_id"] == FIRST][0]
        self.assertEqual(first["current_status"], "COMPLETE", "the first task still resolves from its own record")

    def test_the_registry_is_not_rewritten_by_reading_it(self):
        self.fx.second_task_started()
        before = sha256_file(self.fx.children_json)
        self.dependencies()
        self.assertEqual(sha256_file(self.fx.children_json), before)
        self.assertEqual(self.fx.registry()["children"][0]["task_id"], FIRST, "the first task id is never replaced")


class RegistrationAnchorGateTests(unittest.TestCase):
    """Round 12/13: the gate used to read an absent `children.json` as "no children", so a deleted or truncated registry PERMITTED
    completion. The registration anchor records WHICH children were registered (their identities), so the gate can tell an absence
    nothing contradicts (a parent that never had a child) from a loss, and so replacement children cannot restore the accounting."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.parent = HerdrInstance(self.fx.parent)
        self.anchor_file = self.fx.parent_state / ANCHOR_FILE
        self.first = registration_identity(self.fx.child, FIRST)
        self.second = registration_identity(self.fx.child, SECOND)

    def put(self, *identities):
        write_json(self.anchor_file, {"version": 2, "registered": {PARENT_TASK: [{"repo": r, "task_id": t} for r, t in identities]}})

    def losses(self):
        return [d for d in child_dependencies(self.parent, PARENT_TASK) if d.get("current_status") == "REGISTRY_LOSS"]

    def test_no_anchor_and_no_registry_is_the_normal_no_children_state(self):
        fx = Fixture(registry=False)
        self.addCleanup(fx.cleanup)
        self.assertEqual(child_dependencies(HerdrInstance(fx.parent), PARENT_TASK), [])
        assert_child_dependencies_complete(HerdrInstance(fx.parent), PARENT_TASK)

    def test_a_deleted_registry_the_anchor_remembers_is_an_unresolved_LOSS_that_names_who_is_missing(self):
        self.put(self.first, self.second)
        self.fx.children_json.unlink()
        loss = self.losses()
        self.assertEqual(len(loss), 1)
        self.assertEqual(loss[0]["current_status"], "REGISTRY_LOSS")
        self.assertIn(f"task={FIRST}", loss[0]["dependency_error"])
        self.assertIn(f"task={SECOND}", loss[0]["dependency_error"])
        with self.assertRaises(RuntimeError) as caught:
            assert_child_dependencies_complete(self.parent, PARENT_TASK)
        self.assertIn("status=REGISTRY_LOSS", str(caught.exception))

    def test_a_registry_that_lost_a_registered_child_is_a_LOSS(self):
        self.put(self.first, self.second)  # the fixture registry holds only the first
        self.assertIn(f"task={SECOND}", self.losses()[0]["dependency_error"])

    def test_REPLACEMENT_children_do_not_satisfy_the_anchor_a_count_is_fungible_identities_are_not(self):
        # anchored at two; the registry now holds two OTHER children: two expected, two present, and still a loss
        self.put(self.first, self.second)
        other_a = registration_identity(self.fx.root / "o1", "other-1")
        other_b = registration_identity(self.fx.root / "o2", "other-2")
        write_json(
            self.fx.children_json,
            {"version": 1, "children": [
                dict(self.fx.registry()["children"][0], repo=other_a[0], task_id=other_a[1]),
                dict(self.fx.registry()["children"][0], repo=other_b[0], task_id=other_b[1]),
            ]},
        )
        text = self.losses()[0]["dependency_error"]
        self.assertIn(f"task={FIRST}", text)
        self.assertIn(f"task={SECOND}", text)

    def test_a_registry_holding_everything_registered_and_more_is_not_a_loss(self):
        self.put(self.first)
        self.assertEqual(self.losses(), [])
        self.fx.second_task_started()
        self.fx.register_second()
        self.assertEqual(self.losses(), [])

    def test_an_anchor_for_another_parent_task_changes_nothing_for_this_one(self):
        write_json(self.anchor_file, {"version": 2, "registered": {"another-parent-task": [{"repo": "/x", "task_id": "t"}]}})
        self.assertEqual(self.losses(), [])

    def test_restoring_the_registry_clears_the_loss_and_nothing_else_does(self):
        self.put(self.first, self.second)
        self.assertEqual(len(self.losses()), 1)
        self.fx.second_task_started()
        self.fx.register_second()  # the second child is in the registry again
        self.assertEqual(self.losses(), [])

    def test_a_RESERVATION_is_satisfied_by_a_record_of_its_repository_beyond_those_already_accounted_for(self):
        reserved = registration_identity(self.fx.root / "new", None)
        self.put(self.first, reserved)
        self.assertIn("reserved or recorded", self.losses()[0]["dependency_error"], "launched, never recorded: unfulfilled")
        write_json(
            self.fx.children_json,
            {"version": 1, "children": self.fx.registry()["children"] + [dict(self.fx.registry()["children"][0], repo=reserved[0], task_id="whatever")]},
        )
        self.assertEqual(self.losses(), [], "a record of that repository fulfils it")

    def test_an_unfulfilled_RESERVATION_distinguishes_never_started_started_and_ambiguous_and_authorizes_no_manual_edit(self):
        # A reservation exists BECAUSE work may have begun. The only safe condition for clearing one is POSITIVE evidence that the work never
        # started; "nothing is running now" is not that (stopped or ERROR work can still be required and unresolved). This pins what the
        # block SAYS. It does not, and cannot, show that any particular clearing is correct.
        reserved = registration_identity(self.fx.root / "new", None)
        self.put(self.first, reserved)
        text = self.losses()[0]["dependency_error"]
        self.assertIn("reserved or recorded", text)
        self.assertIn("work may have begun", text)
        # the three cases, each with its consequence
        self.assertRegex(text, r"positive evidence that it NEVER started")
        self.assertRegex(text, r"work that DID start stays blocking until its required outcome is resolved and durably tracked")
        self.assertRegex(text, r"anything ambiguous stays blocking")
        # and the inversion it must not make
        self.assertRegex(text, r"[Nn]othing is running now.{0,40}not such evidence")
        self.assertIn("stopped or ERROR work can still be required and unresolved", text)
        # no instruction to clear it on weak evidence, and none to edit the real files
        for forbidden in ("removes the reservation", "removed the reservation", "confirmed nothing is running", "remove the reservation"):
            self.assertNotIn(forbidden, text)
        self.assertIn("Do not edit", text)
        self.assertIn("supervised correction", text)

    def test_the_generic_loss_text_authorizes_no_manual_edit_and_never_says_to_correct_the_anchor_to_unblock(self):
        self.put(self.first, self.second)  # the fixture registry holds only the first
        text = self.losses()[0]["dependency_error"]
        self.assertIn("supervised correction", text)
        self.assertIn("Do not edit", text)
        self.assertNotIn("corrects", text)
        self.assertNotIn("until a person", text)

    def test_a_reservation_for_a_repository_that_already_has_a_record_still_needs_ANOTHER_record(self):
        reserved = registration_identity(self.fx.child, None)  # the child repository that already holds the first record
        self.put(self.first, reserved)
        self.assertEqual(len(self.losses()), 1, "the first record does not also stand for the reservation")

    def test_an_unreadable_or_invalid_anchor_makes_the_gate_raise_fail_closed_and_is_never_rewritten(self):
        good = {"repo": str(self.fx.child), "task_id": FIRST}
        for text in ("{nope", "[]", json.dumps({"version": 9, "registered": {}}), json.dumps({"version": 1, "registered": {PARENT_TASK: 1}}),
                     json.dumps({"version": 2}), json.dumps({"version": 2, "registered": {PARENT_TASK: 2}}),
                     json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "/r", "task_id": 5}]}}),
                     json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "", "task_id": "t"}]}}),
                     json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "/r"}]}}),
                     json.dumps({"version": 2, "registered": {PARENT_TASK: [dict(good, extra=1)]}})):
            with self.subTest(anchor=text):
                self.anchor_file.write_text(text)
                with self.assertRaises(RuntimeError):
                    child_dependencies(self.parent, PARENT_TASK)
                with self.assertRaises(RuntimeError):
                    assert_child_dependencies_complete(self.parent, PARENT_TASK)
                self.assertEqual(self.anchor_file.read_text(), text)

    def test_a_COUNT_ERA_anchor_is_refused_and_the_refusal_names_the_file_and_how_a_person_clears_it(self):
        # A version-1 anchor holds a COUNT, and a count cannot be upgraded into the identities it never carried, so it is refused rather
        # than reinterpreted. The block is deliberate and lasts until a person acts, so the refusal must say what it found and what to do.
        text = json.dumps({"version": 1, "registered": {PARENT_TASK: 2}})
        self.anchor_file.write_text(text)
        for call in (
            lambda: child_dependencies(self.parent, PARENT_TASK),
            lambda: assert_child_dependencies_complete(self.parent, PARENT_TASK),
        ):
            with self.assertRaises(RuntimeError) as caught:
                call()
            message = str(caught.exception)
            self.assertIn("count-era (version 1)", message)
            self.assertIn(ANCHOR_FILE, message)
            self.assertIn("a person removes", message)
        self.assertEqual(self.anchor_file.read_text(), text, "a refused anchor is never rewritten")
        self.anchor_file.unlink()  # the named step
        self.assertEqual(self.losses(), [])
        self.assertEqual(read_registration_anchor(self.parent), {})

    def test_the_count_era_wording_is_only_for_a_version_1_anchor_other_damage_is_not_described_as_it(self):
        for text in ("{nope", "[]", json.dumps({"version": 9, "registered": {}}), json.dumps({"version": 2, "registered": {PARENT_TASK: 2}})):
            with self.subTest(anchor=text):
                self.anchor_file.write_text(text)
                with self.assertRaises(RuntimeError) as caught:
                    child_dependencies(self.parent, PARENT_TASK)
                self.assertNotIn("count-era", str(caught.exception).replace(str(self.anchor_file), ""), "the words, not the path")

    def test_reading_the_gate_never_creates_or_rewrites_the_anchor(self):
        child_dependencies(self.parent, PARENT_TASK)
        self.assertFalse(self.anchor_file.exists())
        self.put(self.first)
        before = self.anchor_file.read_bytes()
        child_dependencies(self.parent, PARENT_TASK)
        self.assertEqual(self.anchor_file.read_bytes(), before)

    def test_registering_is_a_UNION_it_never_removes_an_identity(self):
        register_identities(self.parent, PARENT_TASK, [self.first, self.second])
        register_identities(self.parent, PARENT_TASK, [self.first])
        self.assertEqual(read_registration_anchor(self.parent), {PARENT_TASK: [self.first, self.second]})
        register_identities(self.parent, "other", [registration_identity(self.fx.root / "z", "z")])
        self.assertEqual(set(read_registration_anchor(self.parent)), {PARENT_TASK, "other"})

    def test_an_unusable_identity_is_refused_not_written(self):
        with self.assertRaises(ValueError):
            register_identities(self.parent, PARENT_TASK, [None])
        self.assertFalse(self.anchor_file.exists())
        self.assertIsNone(registration_identity("", "t"))
        self.assertIsNone(registration_identity(self.fx.child, ""))
        self.assertEqual(registration_identity(self.fx.child, None), (str(self.fx.child), None))

    def test_setting_is_exact_and_an_empty_anchor_is_no_anchor(self):
        register_identities(self.parent, PARENT_TASK, [self.first, self.second])
        set_registration_anchor(self.parent, PARENT_TASK, [self.first])
        self.assertEqual(read_registration_anchor(self.parent), {PARENT_TASK: [self.first]})
        set_registration_anchor(self.parent, PARENT_TASK, [])
        self.assertFalse(self.anchor_file.exists(), "the directory is as it was found")
        set_registration_anchor(self.parent, PARENT_TASK, [])  # already absent: nothing happens
        self.assertFalse(self.anchor_file.exists())

    def test_replacing_a_reservation_swaps_it_for_the_identity_in_one_write(self):
        reserved = registration_identity(self.fx.root / "n", None)
        register_identities(self.parent, PARENT_TASK, [self.first, reserved])
        concrete = registration_identity(self.fx.root / "n", "n-task")
        replace_registration(self.parent, PARENT_TASK, reserved, concrete)
        self.assertEqual(read_registration_anchor(self.parent), {PARENT_TASK: [self.first, concrete]})

    def test_the_write_is_atomic_an_error_before_the_replace_leaves_the_old_anchor_and_no_temporary_file(self):
        self.put(self.first)
        before = self.anchor_file.read_bytes()
        with mock.patch("herdr.dependencies.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                register_identities(self.parent, PARENT_TASK, [self.second])
        self.assertEqual(self.anchor_file.read_bytes(), before)
        self.assertEqual([p.name for p in self.fx.parent_state.iterdir() if ".tmp-" in p.name], [])

    def test_identities_of_lists_each_record_of_the_task_once_and_skips_what_it_cannot_account_for(self):
        records = [
            {"parent_task_id": "p", "repo": str(self.fx.child), "task_id": "a"},
            {"parent_task_id": "p", "repo": str(self.fx.child), "task_id": "a"},
            {"parent_task_id": "p", "repo": "", "task_id": "b"},
            {"parent_task_id": "p", "repo": str(self.fx.child), "task_id": None},
            {"parent_task_id": "q", "repo": str(self.fx.child), "task_id": "c"},
            "junk",
            None,
        ]
        self.assertEqual(identities_of(records, "p"), [(str(self.fx.child), "a")])
        self.assertEqual(identities_of([], "p"), [])
        self.assertEqual(missing_registrations([], records, "p"), [])



if __name__ == "__main__":
    unittest.main()
