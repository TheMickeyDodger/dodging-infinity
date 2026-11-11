"""Supervised clear of one stale child-registration reservation.

A reservation is the anchor identity written before a child is launched, with
no task id. This command may remove that one identity only when the child's
own state shows startup failed with BindingsNotEstablished before any task
was dispatched, and a live session probe can exclude this child. A herd that
came up stays blocking even when no task file exists. The receipt is
append-only: a repeat does not rewrite it, and a crash between the receipt
and the anchor write is finished without a second line.

Contained directories only. The session probe is injected; the production
probe is tested against a stand-in for `herdr agent list`.
"""

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import herdctl
from herdr import dependencies, lifecycle, runtime
from herdr.child_correction import (
    RECEIPT_FILE,
    RECEIPT_SCHEMA,
    CorrectionRefused,
    correct_child_reservation,
    production_session_probe,
)
from herdr.dependencies import (
    ANCHOR_FILE,
    missing_registrations,
    read_registration_anchor,
    registration_identity,
    set_registration_anchor,
)
from herdr.instance import HerdrInstance

TASK = "20261111-140800-parent"
WHEN = datetime(2026, 11, 11, 14, 8, tzinfo=timezone.utc)
OPERATOR = "ada"
DIAGNOSTIC_NAME = "startup-binding-failure-20261111T140800Z-abcd1234.json"


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class CorrectionFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent = self.root / "parent"
        self.child = self.root / "child"
        self.other = self.root / "other"
        self.parent_state = self.parent / ".herd" / "state"
        self.child_state = self.child / ".herd" / "state"
        self.parent_state.mkdir(parents=True)
        self.other.mkdir()
        self.parent_herd = HerdrInstance(self.parent)
        self.reservation = registration_identity(self.child, None)
        self.sibling = registration_identity(self.other, "task-other")
        self.probes = 0
        self.write_registry([])
        self.write_reservation_only()
        self.write_diagnostic()
        self.grant = self.parent_state / "commit-approval.json"
        self.push = self.parent_state / "push-approval.json"
        self.ledger = self.parent_state / "approval-ledger.jsonl"
        self.digest = self.parent_state / "evidence.digest"
        self.grant.write_bytes(b'{"grant":"commit"}\n')
        self.push.write_bytes(b'{"grant":"push"}\n')
        self.ledger.write_bytes(b'{"event":"mint","token_sha256":"abc"}\n')
        self.digest.write_bytes(b"digest-bytes\n")
        self.prior_receipt = b'{"note":"do-not-rewrite","schema":"not-this-command"}\n'
        self.receipt_path = self.parent_state / RECEIPT_FILE
        self.receipt_path.write_bytes(self.prior_receipt)
        self.untouched = {
            self.grant: self.grant.read_bytes(),
            self.push: self.push.read_bytes(),
            self.ledger: self.ledger.read_bytes(),
            self.digest: self.digest.read_bytes(),
        }

    def write_registry(self, children):
        path = self.parent_state / "children.json"
        path.write_text(json.dumps({"version": 1, "children": children}, indent=2) + "\n")
        return path

    def write_reservation_only(self):
        set_registration_anchor(self.parent_herd, TASK, [self.reservation])

    def write_diagnostic(self, **overrides):
        directory = self.child_state / "diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        record = runtime.build_binding_failure_record(
            stage=overrides.pop("stage", lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS),
            failure=overrides.pop("failure", {
                "type": "BINDINGS_NOT_ESTABLISHED",
                "class": "BindingsNotEstablished",
            }),
            workspace=overrides.pop("workspace", {
                "workspace_id": "ws-child",
                "repo": str(self.child.resolve()),
                "runtime_created_at": 10,
            }),
            settle_seconds=overrides.pop("settle_seconds", 30.0),
            roles=overrides.pop("roles", [{
                "logical": "supervisor",
                "agent": "h-sup",
                "pane": "pane-1",
                "reason": "the recorded binding carries no session",
                "session": "ABSENT",
            }]),
        )
        record.update(overrides)
        path = directory / DIAGNOSTIC_NAME
        path.write_text(json.dumps(record, indent=1, sort_keys=True))
        return path

    def diagnostic_bytes(self):
        return (self.child_state / "diagnostics" / DIAGNOSTIC_NAME).read_bytes()

    def probe(self, agents=None, ok=True, reason="down"):
        def run():
            self.probes += 1
            if not ok:
                return {"ok": False, "reason": reason}
            return {"ok": True, "agents": [] if agents is None else agents}
        return run

    def clear(self, agents=None, ok=True, evidence=None, child=None, task=TASK):
        return correct_child_reservation(
            self.parent,
            task,
            self.child if child is None else child,
            self.child_state if evidence is None else evidence,
            now=WHEN,
            operator=OPERATOR,
            session_probe=self.probe(agents, ok),
        )

    def refuse(self, **kwargs):
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        receipt = self.receipt_path.read_bytes()
        registry = (self.parent_state / "children.json").read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear(**kwargs)
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)
        self.assertEqual(self.receipt_path.read_bytes(), receipt)
        self.assertEqual((self.parent_state / "children.json").read_bytes(), registry)
        self.assert_untouched()
        return caught.exception

    def assert_untouched(self):
        for path, raw in self.untouched.items():
            self.assertEqual(path.read_bytes(), raw, path)

    def test_a_binding_failure_before_dispatch_clears_only_the_reservation(self):
        set_registration_anchor(
            self.parent_herd, TASK, [self.sibling, self.reservation],
        )
        self.write_registry([{
            "parent_task_id": TASK,
            "repo": str(self.other.resolve()),
            "task_id": "task-other",
        }])
        registry = (self.parent_state / "children.json").read_bytes()
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with mock.patch(
            "herdr.dependencies.set_registration_anchor",
            side_effect=AssertionError("not the supervised path"),
        ), mock.patch(
            "herdr.dependencies.replace_registration",
            side_effect=AssertionError("not the supervised path"),
        ):
            result = self.clear(agents=[{
                "name": "someone-else",
                "cwd": str(self.other.resolve()),
                "workspace_id": "ws-other",
            }])
        self.assertTrue(result["applied"])
        self.assertTrue(result["receipt_appended"])
        self.assertEqual(self.probes, 1)
        anchored = read_registration_anchor(self.parent_herd)
        self.assertEqual(anchored[TASK], [self.sibling])
        self.assertNotIn(self.reservation, anchored[TASK])
        self.assertEqual(
            missing_registrations(anchored[TASK], [{
                "parent_task_id": TASK,
                "repo": str(self.other.resolve()),
                "task_id": "task-other",
            }], TASK),
            [],
        )
        raw = self.receipt_path.read_bytes()
        self.assertTrue(raw.startswith(self.prior_receipt))
        self.assertEqual(raw.splitlines(keepends=True)[0], self.prior_receipt)
        line = raw.splitlines(keepends=True)[1]
        document = json.loads(line)
        self.assertEqual(document["schema"], RECEIPT_SCHEMA)
        self.assertEqual(document["action"], "clear_reservation")
        self.assertEqual(document["operator"], OPERATOR)
        self.assertEqual(document["operator_uid"], os.getuid())
        self.assertEqual(document["at"], "2026-11-11T14:08:00.000000Z")
        self.assertEqual(document["parent_task_id"], TASK)
        self.assertEqual(document["child_repo"], str(self.child.resolve()))
        self.assertEqual(document["evidence_dir"], str(self.child_state.resolve()))
        evidence_file = document["evidence"]["files"][0]
        self.assertEqual(evidence_file["path"], "diagnostics/" + DIAGNOSTIC_NAME)
        self.assertEqual(evidence_file["sha256"], sha256(self.diagnostic_bytes()))
        self.assertEqual(evidence_file["bytes"], len(self.diagnostic_bytes()))
        tree = f"{evidence_file['path']}\0{evidence_file['sha256']}\0{evidence_file['bytes']}\n"
        self.assertEqual(document["evidence"]["tree_sha256"], sha256(tree.encode()))
        self.assertEqual(document["anchor_before"]["sha256"], sha256(before))
        after = (self.parent_state / ANCHOR_FILE).read_bytes()
        self.assertEqual(document["anchor_after"]["sha256"], sha256(after))
        self.assertEqual(
            document["anchor_before"]["registered"][TASK][1],
            {"repo": str(self.child.resolve()), "task_id": None},
        )
        self.assertEqual(
            document["anchor_after"]["registered"][TASK],
            [{"repo": str(self.other.resolve()), "task_id": "task-other"}],
        )
        self.assertEqual(document["diagnostic"]["failure_type"], "BINDINGS_NOT_ESTABLISHED")
        self.assertEqual(document["diagnostic"]["stage"], "establish_role_bindings")
        self.assertEqual(document["diagnostic"]["name"], DIAGNOSTIC_NAME)
        self.assertNotIn("agent_names", document["diagnostic"])
        self.assertEqual((self.parent_state / "children.json").read_bytes(), registry)
        self.assert_untouched()

    def test_the_only_reservation_removes_the_anchor_and_records_a_null_after_hash(self):
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        result = self.clear()
        self.assertTrue(result["applied"])
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())
        document = json.loads(result["receipt_line"])
        self.assertEqual(document["anchor_before"]["sha256"], sha256(before))
        self.assertIsNone(document["anchor_after"]["sha256"])
        self.assertEqual(document["anchor_after"]["registered"], {})
        self.assert_untouched()

    def test_an_absent_child_registry_is_not_treated_as_a_record(self):
        registry = self.parent_state / "children.json"
        registry.unlink()
        result = self.clear()
        self.assertTrue(result["applied"])
        self.assertFalse(registry.exists())
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())
        self.assert_untouched()

    def test_a_repeat_writes_nothing_and_does_not_probe_again(self):
        self.clear()
        first = self.receipt_path.read_bytes()
        probes = self.probes
        again = self.clear()
        self.assertFalse(again["applied"])
        self.assertFalse(again["receipt_appended"])
        self.assertEqual(self.receipt_path.read_bytes(), first)
        self.assertEqual(self.probes, probes)
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())
        self.assertEqual(again["receipt"]["action"], "clear_reservation")

    def test_a_repeat_after_the_evidence_changes_refuses_without_rewriting_the_receipt(self):
        self.clear()
        first = self.receipt_path.read_bytes()
        path = self.child_state / "diagnostics" / DIAGNOSTIC_NAME
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "receipt_mismatch")
        self.assertEqual(self.receipt_path.read_bytes(), first)
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())

    def test_a_crash_after_the_receipt_is_finished_without_a_second_line(self):
        real = dependencies._write_registration_anchor
        calls = {"n": 0}

        def fail_once(herd, registered):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return real(herd, registered)

        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with mock.patch(
            "herdr.dependencies._write_registration_anchor", side_effect=fail_once,
        ):
            with self.assertRaises(OSError):
                self.clear()
            self.assertTrue((self.parent_state / ANCHOR_FILE).exists())
            self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)
            lines = self.receipt_path.read_bytes().splitlines(keepends=True)
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], self.prior_receipt)
            result = self.clear()
        self.assertTrue(result["applied"])
        self.assertFalse(result["receipt_appended"])
        again = self.receipt_path.read_bytes().splitlines(keepends=True)
        self.assertEqual(again, lines)
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())
        self.assertEqual(json.loads(lines[1])["anchor_before"]["sha256"], sha256(before))

    def test_the_gate_names_this_command_and_still_forbids_editing_by_hand(self):
        text = missing_registrations([self.reservation], [], TASK)[0]
        self.assertIn("herdctl children correct", text)
        self.assertIn("supervised correction", text)
        self.assertIn("positive evidence that it NEVER started", text)
        self.assertNotIn("corrects", text)

    def test_a_copy_of_the_state_directory_is_refused(self):
        copy = self.root / "evidence-copy"
        copy.mkdir()
        caught = self.refuse(evidence=copy)
        self.assertEqual(caught.check, "evidence_not_child_state")

    def test_a_symlink_to_the_state_directory_is_refused(self):
        link = self.root / "state-link"
        os.symlink(self.child_state, link)
        caught = self.refuse(evidence=link)
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_a_missing_state_directory_is_refused(self):
        os.replace(self.child_state, self.root / "moved-state")
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "evidence_missing")
        self.assertTrue(self.reservation in read_registration_anchor(self.parent_herd)[TASK])

    def test_an_empty_state_directory_is_refused(self):
        for path in (self.child_state / "diagnostics").iterdir():
            path.unlink()
        (self.child_state / "diagnostics").rmdir()
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_two_binding_diagnostics_are_refused(self):
        other = self.child_state / "diagnostics" / (
            "startup-binding-failure-20261111T140801Z-abcd1235.json"
        )
        other.write_bytes(self.diagnostic_bytes())
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_an_unknown_failure_is_refused(self):
        self.write_diagnostic(failure={"type": "UNKNOWN", "class": "RuntimeError"})
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")
        self.assertIn("UNKNOWN", caught.detail)

    def test_a_heartbeat_stage_failure_means_the_herd_came_up(self):
        self.write_diagnostic(stage=lifecycle.STAGE_HEARTBEAT_CONTROLLER)
        caught = self.refuse()
        self.assertEqual(caught.check, "herd_came_up")
        self.assertIn("no task file", caught.detail)

    def test_an_agent_start_diagnostic_is_not_this_evidence(self):
        path = self.child_state / "diagnostics" / DIAGNOSTIC_NAME
        path.unlink()
        (self.child_state / "diagnostics" / "agent-start-failure-h-sup.json").write_text("{}")
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_any_other_file_beside_the_diagnostic_is_refused(self):
        (self.child_state / "notes.txt").write_text("looks quiet")
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_a_task_record_stays_blocking(self):
        (self.child_state / "task.json").write_text('{"id":"t","status":"ERROR"}')
        caught = self.refuse()
        self.assertEqual(caught.check, "child_task_present")

    def test_a_task_archive_stays_blocking(self):
        archive = self.child_state / "tasks"
        archive.mkdir()
        (archive / "old.json").write_text('{"id":"t","status":"ERROR"}')
        caught = self.refuse()
        self.assertEqual(caught.check, "child_task_present")

    def test_a_herd_that_came_up_without_a_task_stays_blocking(self):
        (self.child_state / "runtime.json").write_text('{"version":2,"workspace_id":"ws-child"}')
        caught = self.refuse()
        self.assertEqual(caught.check, "herd_came_up")
        self.assertFalse((self.child_state / "task.json").exists())
        self.assertIn("runtime.json", caught.detail)

    def test_role_bindings_mean_the_herd_came_up_even_without_runtime_json(self):
        (self.child_state / "role-bindings.json").write_text('{"version":1,"roles":{}}')
        caught = self.refuse()
        self.assertEqual(caught.check, "herd_came_up")
        self.assertIn("role-bindings.json", caught.detail)

    def test_a_registry_record_for_the_child_is_refused(self):
        self.write_registry([{
            "parent_task_id": TASK,
            "repo": str(self.child),
            "task_id": "dispatched",
        }])
        caught = self.refuse()
        self.assertEqual(caught.check, "registry_record_present")

    def test_a_concrete_identity_is_not_removed(self):
        concrete = registration_identity(self.child, "dispatched")
        set_registration_anchor(self.parent_herd, TASK, [concrete])
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "identity_present")
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)
        self.assertEqual(self.receipt_path.read_bytes(), self.prior_receipt)

    def test_a_reservation_beside_a_concrete_identity_removes_neither(self):
        concrete = registration_identity(self.child, "dispatched")
        set_registration_anchor(self.parent_herd, TASK, [self.reservation, concrete])
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "identity_present")
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)
        self.assertEqual(
            read_registration_anchor(self.parent_herd)[TASK],
            [self.reservation, concrete],
        )

    def test_a_live_session_in_the_child_repository_is_refused(self):
        caught = self.refuse(agents=[{
            "name": "h-other",
            "cwd": str(self.child.resolve()),
            "workspace_id": "ws-else",
        }])
        self.assertEqual(caught.check, "live_session")

    def test_a_live_session_in_the_diagnostic_workspace_is_refused(self):
        caught = self.refuse(agents=[{
            "name": "h-other",
            "cwd": str(self.other.resolve()),
            "workspace_id": "ws-child",
        }])
        self.assertEqual(caught.check, "live_session")

    def test_a_live_session_named_by_the_diagnostic_is_refused(self):
        caught = self.refuse(agents=[{
            "name": "h-sup",
            "cwd": str(self.other.resolve()),
            "workspace_id": "ws-else",
        }])
        self.assertEqual(caught.check, "live_session")

    def test_an_agent_with_no_absolute_cwd_is_ambiguous(self):
        caught = self.refuse(agents=[{"name": "h-other", "cwd": "relative", "workspace_id": "ws-else"}])
        self.assertEqual(caught.check, "live_session_ambiguous")

    def test_a_failed_session_probe_is_refused(self):
        caught = self.refuse(ok=False)
        self.assertEqual(caught.check, "session_probe_failed")

    def test_a_probe_that_raises_is_refused(self):
        def boom():
            raise RuntimeError("herdr down")
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            correct_child_reservation(
                self.parent, TASK, self.child, self.child_state,
                now=WHEN, operator=OPERATOR, session_probe=boom,
            )
        self.assertEqual(caught.exception.check, "session_probe_failed")
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)

    def test_there_is_nothing_to_clear_without_a_reservation_or_a_receipt(self):
        (self.parent_state / ANCHOR_FILE).unlink()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "reservation_absent")
        self.assertEqual(self.receipt_path.read_bytes(), self.prior_receipt)

    def test_an_unreadable_registry_is_refused(self):
        (self.parent_state / "children.json").write_text("{")
        caught = self.refuse()
        self.assertEqual(caught.check, "registry_unreadable")

    def test_a_truncated_role_list_is_refused(self):
        record = json.loads(self.diagnostic_bytes())
        record["roles_truncated"] = True
        (self.child_state / "diagnostics" / DIAGNOSTIC_NAME).write_text(json.dumps(record))
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_an_extra_diagnostic_field_is_refused(self):
        record = json.loads(self.diagnostic_bytes())
        record["note"] = "not part of the record"
        (self.child_state / "diagnostics" / DIAGNOSTIC_NAME).write_text(json.dumps(record))
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_a_symlink_inside_diagnostics_is_refused(self):
        path = self.child_state / "diagnostics" / DIAGNOSTIC_NAME
        raw = path.read_bytes()
        path.unlink()
        held = self.root / "held.json"
        held.write_bytes(raw)
        os.symlink(held, path)
        caught = self.refuse()
        self.assertEqual(caught.check, "evidence_ambiguous")

    def test_a_corrupt_receipt_is_not_rewritten(self):
        self.receipt_path.write_bytes(b"not json\n")
        before = (self.parent_state / ANCHOR_FILE).read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "receipt_unreadable")
        self.assertEqual(self.receipt_path.read_bytes(), b"not json\n")
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before)

    def test_a_receipt_without_a_trailing_newline_is_not_appended_onto(self):
        self.receipt_path.write_bytes(b'{"schema":"not-this-command"}')
        before_anchor = (self.parent_state / ANCHOR_FILE).read_bytes()
        with self.assertRaises(CorrectionRefused) as caught:
            self.clear()
        self.assertEqual(caught.exception.check, "receipt_unreadable")
        self.assertEqual(self.receipt_path.read_bytes(), b'{"schema":"not-this-command"}')
        self.assertEqual((self.parent_state / ANCHOR_FILE).read_bytes(), before_anchor)

    def test_the_command_refuses_on_the_same_checks(self):
        (self.child_state / "runtime.json").write_text("{}")
        args = SimpleNamespace(
            repo=str(self.parent),
            parent_task=TASK,
            child_repo=str(self.child),
            evidence=str(self.child_state),
        )
        with mock.patch.object(
            herdctl, "resolve_repo_ref", side_effect=lambda ref: Path(ref).resolve(),
        ):
            with self.assertRaises(SystemExit) as caught:
                herdctl.children_correct_cmd(args)
        self.assertIn("herd_came_up", str(caught.exception))
        self.assertIn(self.reservation, read_registration_anchor(self.parent_herd)[TASK])

    def test_the_command_clears_and_a_second_invocation_writes_nothing(self):
        args = SimpleNamespace(
            repo=str(self.parent),
            parent_task=TASK,
            child_repo=str(self.child),
            evidence=str(self.child_state),
        )
        with mock.patch.object(
            herdctl, "resolve_repo_ref", side_effect=lambda ref: Path(ref).resolve(),
        ), mock.patch(
            "herdctl.correct_child_reservation",
            wraps=correct_child_reservation,
        ) as wrapped, mock.patch(
            "herdr.child_correction.production_session_probe",
            return_value={"ok": True, "agents": []},
        ), mock.patch(
            "herdr.child_correction.production_operator",
            return_value=OPERATOR,
        ):
            with mock.patch("sys.stdout"):
                herdctl.children_correct_cmd(args)
                first = self.receipt_path.read_bytes()
                herdctl.children_correct_cmd(args)
        self.assertEqual(wrapped.call_count, 2)
        self.assertEqual(self.receipt_path.read_bytes(), first)
        self.assertFalse((self.parent_state / ANCHOR_FILE).exists())


class SessionProbeTests(unittest.TestCase):
    def test_a_failed_listing_is_not_an_empty_listing(self):
        with mock.patch(
            "herdr.child_correction.runtime.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="no"),
        ):
            answer = production_session_probe()
        self.assertFalse(answer["ok"])

    def test_a_bare_object_is_not_a_successful_empty_listing(self):
        with mock.patch(
            "herdr.child_correction.runtime.run",
            return_value=SimpleNamespace(returncode=0, stdout="{}", stderr=""),
        ):
            answer = production_session_probe()
        self.assertFalse(answer["ok"])

    def test_an_empty_agent_list_is_success(self):
        payload = json.dumps({"result": {"agents": []}})
        with mock.patch(
            "herdr.child_correction.runtime.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ):
            answer = production_session_probe()
        self.assertEqual(answer, {"ok": True, "agents": []})


class CommandHelpTests(unittest.TestCase):
    def test_children_correct_is_a_herdctl_command(self):
        with mock.patch("sys.argv", ["herdctl", "children", "correct", "--help"]):
            with mock.patch("sys.stdout"):
                with self.assertRaises(SystemExit) as caught:
                    herdctl.main()
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
