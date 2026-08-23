import tempfile
import unittest
from pathlib import Path

from mission_control.audit import MissionControlAuditLog
from mission_control.models import (
    OperatorRecommendation,
    OperatorReview,
)
from mission_control.approvals import (
    ApprovalItem,
    ApprovalQueue,
)


class ApprovalQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.repo_a = self.root / "repo-a"
        self.repo_b = self.root / "repo-b"
        self.repo_a.mkdir()
        self.repo_b.mkdir()
        self.queue = ApprovalQueue()

    def tearDown(self):
        self.tempdir.cleanup()

    def review(
        self,
        herd_id,
        commands,
        *,
        explanation="Recommended next action.",
    ):
        return OperatorReview(
            herd_id=herd_id,
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation=explanation,
                commands=tuple(commands),
            ),
        )

    def test_global_queue_preserves_exact_commands_across_herds(self):
        first = self.queue.enqueue(
            self.repo_a,
            self.review(
                "herd-a",
                (
                    "git status --short",
                    "python3 -m unittest",
                ),
            ),
        )
        second = self.queue.enqueue(
            self.repo_b,
            self.review(
                "herd-b",
                ("git diff --check",),
            ),
        )

        pending = self.queue.pending()

        self.assertEqual(
            [item.approval_id for item in pending],
            [first.approval_id, second.approval_id],
        )
        self.assertEqual(
            first.commands,
            (
                "git status --short",
                "python3 -m unittest",
            ),
        )
        self.assertEqual(
            second.commands,
            ("git diff --check",),
        )

    def test_approve_returns_exact_immutable_sequence_and_removes_item(self):
        item = self.queue.enqueue(
            self.repo_a,
            self.review(
                "herd-a",
                (
                    "git status --short",
                    "git diff --check",
                ),
            ),
        )

        approved = self.queue.approve(item.approval_id)

        self.assertEqual(
            approved.approval_id,
            item.approval_id,
        )
        self.assertEqual(
            approved.herd_id,
            "herd-a",
        )
        self.assertEqual(
            approved.commands,
            (
                "git status --short",
                "git diff --check",
            ),
        )
        self.assertEqual(
            self.queue.pending(),
            (),
        )

        records = MissionControlAuditLog(
            self.repo_a
        ).read(
            herd_id="herd-a",
        )
        self.assertEqual(
            [record.event_type for record in records],
            [
                "approval.queued",
                "approval.approved",
            ],
        )
        self.assertEqual(
            records[-1].actor,
            "human",
        )
        self.assertEqual(
            records[-1].data["commands"],
            [
                "git status --short",
                "git diff --check",
            ],
        )

    def test_reject_removes_item_and_audits_human_rejection(self):
        item = self.queue.enqueue(
            self.repo_a,
            self.review(
                "herd-a",
                ("git status --short",),
            ),
        )

        rejected = self.queue.reject(item.approval_id)

        self.assertEqual(
            rejected.approval_id,
            item.approval_id,
        )
        self.assertEqual(
            self.queue.pending(),
            (),
        )

        records = MissionControlAuditLog(
            self.repo_a
        ).read(
            herd_id="herd-a",
        )
        self.assertEqual(
            records[-1].event_type,
            "approval.rejected",
        )
        self.assertEqual(
            records[-1].actor,
            "human",
        )

    def test_queue_preserves_source_execution_id(self):
        review = OperatorReview(
            herd_id="herd-a",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Inspect status.",
                commands=("git status --short",),
            ),
            source_execution_id="exec-source-1",
        )

        item = self.queue.enqueue(
            self.repo_a,
            review,
        )

        self.assertEqual(
            item.source_execution_id,
            "exec-source-1",
        )

        records = MissionControlAuditLog(
            self.repo_a
        ).read(
            herd_id="herd-a",
        )

        self.assertEqual(
            records[-1].data["source_execution_id"],
            "exec-source-1",
        )

    def test_empty_operator_recommendation_is_not_queued(self):
        review = self.review(
            "herd-a",
            (),
            explanation="No action is required.",
        )

        result = self.queue.enqueue(
            self.repo_a,
            review,
        )

        self.assertIsNone(result)
        self.assertEqual(
            self.queue.pending(),
            (),
        )

        records = MissionControlAuditLog(
            self.repo_a
        ).read(
            herd_id="herd-a",
        )
        self.assertEqual(
            records[-1].event_type,
            "approval.not_required",
        )

    def test_second_pending_item_for_same_herd_is_refused(self):
        self.queue.enqueue(
            self.repo_a,
            self.review(
                "herd-a",
                ("git status --short",),
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "already has a pending approval",
        ):
            self.queue.enqueue(
                self.repo_a,
                self.review(
                    "herd-a",
                    ("git diff --check",),
                ),
            )

    def test_unknown_approval_cannot_be_approved_or_rejected(self):
        with self.assertRaisesRegex(
            KeyError,
            "Unknown approval",
        ):
            self.queue.approve("missing")

        with self.assertRaisesRegex(
            KeyError,
            "Unknown approval",
        ):
            self.queue.reject("missing")


if __name__ == "__main__":
    unittest.main()

from unittest.mock import Mock

from mission_control.approvals import ApprovalExecutionService
from mission_control.execution import (
    ExecutionResult,
    MissionControlExecutionService,
)
from mission_control.handoff import HandoffMarker
from mission_control.safety import (
    CommandSafetyPolicy,
    SafetyViolation,
)
from mission_control.session import GhosttySession


class ApprovalExecutionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()
        self.execution = Mock(spec=MissionControlExecutionService)
        self.service = ApprovalExecutionService(
            self.queue,
            self.execution,
            safety_policy=CommandSafetyPolicy(),
        )
        self.session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-1",
            repo_path=self.repo,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def review(self, commands):
        return OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Run the validated next step.",
                commands=tuple(commands),
            ),
        )

    def test_approve_and_execute_uses_exact_approved_sequence(self):
        commands = (
            "git status --short",
            "git diff --check",
        )
        item = self.queue.enqueue(
            self.repo,
            self.review(commands),
        )

        self.execution.execute.return_value = ExecutionResult(
            execution_id="exec-1",
            outcomes=(),
            handoff=HandoffMarker(
                execution_id="exec-1",
                exit_code=0,
                raw="HERDR_HANDOFF:exec-1:0",
            ),
        )

        result = self.service.approve_and_execute(
            item.approval_id,
            self.session,
            execution_id="exec-1",
        )

        self.assertEqual(result.execution_id, "exec-1")
        self.execution.execute.assert_called_once_with(
            self.session,
            "exec-1",
            commands,
        )
        self.assertEqual(self.queue.pending(), ())

        records = MissionControlAuditLog(
            self.repo
        ).read(
            herd_id="herd-1",
        )
        self.assertEqual(
            [record.event_type for record in records],
            [
                "approval.queued",
                "approval.approved",
            ],
        )

    def test_wrong_herd_session_does_not_consume_approval(self):
        item = self.queue.enqueue(
            self.repo,
            self.review(("git status --short",)),
        )
        wrong_session = GhosttySession(
            herd_id="herd-other",
            terminal_id="terminal-other",
            repo_path=self.repo,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "different Herd",
        ):
            self.service.approve_and_execute(
                item.approval_id,
                wrong_session,
                execution_id="exec-1",
            )

        self.assertEqual(
            self.queue.pending(),
            (item,),
        )
        self.execution.execute.assert_not_called()

    def test_wrong_repo_session_does_not_consume_approval(self):
        item = self.queue.enqueue(
            self.repo,
            self.review(("git status --short",)),
        )
        other_repo = self.repo / "other"
        other_repo.mkdir()
        wrong_session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-other",
            repo_path=other_repo,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "different repository",
        ):
            self.service.approve_and_execute(
                item.approval_id,
                wrong_session,
                execution_id="exec-1",
            )

        self.assertEqual(
            self.queue.pending(),
            (item,),
        )
        self.execution.execute.assert_not_called()

    def test_safety_block_happens_after_human_approval_before_execution(self):
        item = self.queue.enqueue(
            self.repo,
            self.review(("rm -rf /",)),
        )

        with self.assertRaisesRegex(
            SafetyViolation,
            "filesystem wipe",
        ):
            self.service.approve_and_execute(
                item.approval_id,
                self.session,
                execution_id="exec-1",
            )

        self.assertEqual(self.queue.pending(), ())
        self.execution.execute.assert_not_called()

        records = MissionControlAuditLog(
            self.repo
        ).read(
            herd_id="herd-1",
        )
        self.assertEqual(
            [record.event_type for record in records],
            [
                "approval.queued",
                "approval.approved",
                "approval.safety_blocked",
            ],
        )
        self.assertEqual(
            records[-1].data["approval_id"],
            item.approval_id,
        )

    def test_next_action_commit_cannot_reach_execution(self):
        from mission_control.approvals import (
            APPROVAL_TYPE_NEXT_ACTION,
        )

        item = ApprovalItem(
            approval_id="next-action-commit",
            herd_id="herd-1",
            repo_path=self.repo,
            approval_type=APPROVAL_TYPE_NEXT_ACTION,
            explanation="This must not cross the commit gate.",
            commands=("git commit -m test",),
            provider="fake",
            model="fake-model",
            created_at_ms=123456789,
        )
        self.queue._items[item.approval_id] = item
        self.queue._order.append(item.approval_id)

        with self.assertRaisesRegex(
            SafetyViolation,
            "separate commit approval",
        ):
            self.service.approve_and_execute(
                item.approval_id,
                self.session,
                execution_id="exec-1",
            )

        self.execution.execute.assert_not_called()

from mission_control.approvals import OperatorApprovalService


class OperatorApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()
        self.review_service = Mock()
        self.service = OperatorApprovalService(
            self.queue,
            self.review_service,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_review_and_queue_connects_operator_review_to_global_queue(self):
        review = OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Inspect status.",
                commands=("git status --short",),
            ),
        )
        self.review_service.review.return_value = review

        item = self.service.review_and_queue(
            self.repo,
            herd_id="herd-1",
        )

        self.review_service.review.assert_called_once_with(
            self.repo,
            herd_id="herd-1",
        )
        self.assertEqual(item.herd_id, "herd-1")
        self.assertEqual(
            item.commands,
            ("git status --short",),
        )
        self.assertEqual(
            self.queue.pending(),
            (item,),
        )

    def test_no_action_review_creates_no_pending_approval(self):
        review = OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="No action required.",
                commands=(),
            ),
        )
        self.review_service.review.return_value = review

        item = self.service.review_and_queue(
            self.repo,
            herd_id="herd-1",
        )

        self.assertIsNone(item)
        self.assertEqual(self.queue.pending(), ())

import threading


class ApprovalQueueConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.queue = ApprovalQueue()

    def tearDown(self):
        self.tempdir.cleanup()

    def review(self, herd_id, command):
        return OperatorReview(
            herd_id=herd_id,
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Concurrent proposal.",
                commands=(command,),
            ),
        )

    def test_concurrent_same_herd_enqueue_allows_only_one_pending_item(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def enqueue(command):
            try:
                barrier.wait()
                results.append(
                    self.queue.enqueue(
                        self.repo,
                        self.review("herd-1", command),
                    )
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=enqueue, args=("echo one",)),
            threading.Thread(target=enqueue, args=("echo two",)),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn(
            "already has a pending approval",
            str(errors[0]),
        )
        self.assertEqual(len(self.queue.pending()), 1)

    def test_get_returns_pending_item_without_consuming_it(self):
        item = self.queue.enqueue(
            self.repo,
            self.review("herd-1", "git status --short"),
        )

        observed = self.queue.get(item.approval_id)

        self.assertEqual(observed, item)
        self.assertEqual(self.queue.pending(), (item,))

    def test_get_rejects_unknown_approval(self):
        with self.assertRaisesRegex(
            KeyError,
            "Unknown approval",
        ):
            self.queue.get("missing")


class OperatorApprovalHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()
        self.review_service = Mock()
        self.service = OperatorApprovalService(
            self.queue,
            self.review_service,
        )
        self.audit = MissionControlAuditLog(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def review_for(self, execution_id):
        return OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Inspect the completed handoff.",
                commands=("git status --short",),
            ),
            source_execution_id=execution_id,
        )

    def test_latest_completed_execution_is_reviewed_once(self):
        self.audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-1"},
        )
        self.audit.append(
            "herd-1",
            "execution.completed",
            data={
                "execution_id": "exec-1",
                "exit_code": 0,
            },
        )
        self.review_service.review.return_value = self.review_for(
            "exec-1"
        )

        first = self.service.review_latest_handoff(
            self.repo,
            herd_id="herd-1",
        )
        second = self.service.review_latest_handoff(
            self.repo,
            herd_id="herd-1",
        )

        self.assertIsNotNone(first)
        self.assertEqual(
            first.source_execution_id,
            "exec-1",
        )
        self.assertIsNone(second)
        self.review_service.review.assert_called_once_with(
            self.repo,
            herd_id="herd-1",
        )

    def test_review_latest_handoff_signals_when_review_actually_starts(self):
        self.audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-signal"},
        )
        self.audit.append(
            "herd-1",
            "execution.completed",
            data={
                "execution_id": "exec-signal",
                "exit_code": 0,
            },
        )
        self.review_service.review.return_value = self.review_for(
            "exec-signal"
        )

        started = []

        self.service.review_latest_handoff(
            self.repo,
            herd_id="herd-1",
            on_started=started.append,
        )

        self.assertEqual(
            started,
            ["exec-signal"],
        )

    def test_failed_execution_is_also_reviewable_handoff(self):
        self.audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-failed"},
        )
        self.audit.append(
            "herd-1",
            "execution.failed",
            data={
                "execution_id": "exec-failed",
                "exit_code": 1,
            },
        )
        self.review_service.review.return_value = self.review_for(
            "exec-failed"
        )

        item = self.service.review_latest_handoff(
            self.repo,
            herd_id="herd-1",
        )

        self.assertEqual(
            item.source_execution_id,
            "exec-failed",
        )

    def test_newer_unresolved_execution_blocks_older_handoff(self):
        self.audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-old"},
        )
        self.audit.append(
            "herd-1",
            "execution.completed",
            data={
                "execution_id": "exec-old",
                "exit_code": 0,
            },
        )
        self.audit.append(
            "herd-1",
            "execution.started",
            data={"execution_id": "exec-new"},
        )

        item = self.service.review_latest_handoff(
            self.repo,
            herd_id="herd-1",
        )

        self.assertIsNone(item)
        self.review_service.review.assert_not_called()
        self.assertEqual(self.queue.pending(), ())


class GitGateTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()
        self.execution = Mock(spec=MissionControlExecutionService)
        self.session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-1",
            repo_path=self.repo,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_commit_gate_translates_to_existing_herdctl_approval_then_exact_commit(self):
        from mission_control.approvals import (
            APPROVAL_TYPE_COMMIT,
            GitGateApprovalService,
        )

        item = ApprovalItem(
            approval_id="commit-approval",
            herd_id="herd-1",
            repo_path=self.repo,
            approval_type=APPROVAL_TYPE_COMMIT,
            explanation="Commit the validated work.",
            commands=("git commit -m 'validated change'",),
            provider="fake",
            model="fake-model",
            created_at_ms=123456789,
        )
        self.queue._items[item.approval_id] = item
        self.queue._order.append(item.approval_id)

        service = GitGateApprovalService(
            self.queue,
            self.execution,
            safety_policy=CommandSafetyPolicy(),
        )

        service.approve_and_execute(
            item.approval_id,
            self.session,
            execution_id="exec-commit",
        )

        self.execution.execute.assert_called_once_with(
            self.session,
            "exec-commit",
            (
                f"herdctl approve-commit --repo {self.repo} --yes",
                "git commit -m 'validated change'",
            ),
        )

    def test_push_gate_translates_to_existing_herdctl_approval_then_exact_push(self):
        from mission_control.approvals import (
            APPROVAL_TYPE_PUSH,
            GitGateApprovalService,
        )

        item = ApprovalItem(
            approval_id="push-approval",
            herd_id="herd-1",
            repo_path=self.repo,
            approval_type=APPROVAL_TYPE_PUSH,
            explanation="Push the validated commit.",
            commands=("git push origin main",),
            provider="fake",
            model="fake-model",
            created_at_ms=123456789,
        )
        self.queue._items[item.approval_id] = item
        self.queue._order.append(item.approval_id)

        service = GitGateApprovalService(
            self.queue,
            self.execution,
            safety_policy=CommandSafetyPolicy(),
        )

        service.approve_and_execute(
            item.approval_id,
            self.session,
            execution_id="exec-push",
        )

        self.execution.execute.assert_called_once_with(
            self.session,
            "exec-push",
            (
                f"herdctl approve-push --repo {self.repo} "
                "--remote origin --target-branch main --yes",
                "git push origin main",
            ),
        )


class GitGateQueueClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()

    def tearDown(self):
        self.tempdir.cleanup()

    def review(self, commands):
        return OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Perform the validated Git action.",
                commands=tuple(commands),
            ),
        )

    def test_standalone_commit_becomes_commit_approval(self):
        from mission_control.approvals import APPROVAL_TYPE_COMMIT

        item = self.queue.enqueue(
            self.repo,
            self.review(("git commit -m 'validated change'",)),
        )

        self.assertEqual(
            item.approval_type,
            APPROVAL_TYPE_COMMIT,
        )

    def test_standalone_push_becomes_push_approval(self):
        from mission_control.approvals import APPROVAL_TYPE_PUSH

        item = self.queue.enqueue(
            self.repo,
            self.review(("git push origin main",)),
        )

        self.assertEqual(
            item.approval_type,
            APPROVAL_TYPE_PUSH,
        )

    def test_mixed_sequence_with_commit_remains_next_action(self):
        from mission_control.approvals import APPROVAL_TYPE_NEXT_ACTION

        item = self.queue.enqueue(
            self.repo,
            self.review(
                (
                    "git status --short",
                    "git commit -m 'validated change'",
                )
            ),
        )

        self.assertEqual(
            item.approval_type,
            APPROVAL_TYPE_NEXT_ACTION,
        )


class GenericExecutionRejectsGitGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()
        self.execution = Mock(spec=MissionControlExecutionService)
        self.service = ApprovalExecutionService(
            self.queue,
            self.execution,
            safety_policy=CommandSafetyPolicy(),
        )
        self.session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-1",
            repo_path=self.repo,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_generic_execution_service_rejects_git_gate_approvals(self):
        from mission_control.approvals import (
            APPROVAL_TYPE_COMMIT,
            APPROVAL_TYPE_PUSH,
        )

        for approval_type, command in (
            (
                APPROVAL_TYPE_COMMIT,
                "git commit -m 'validated change'",
            ),
            (
                APPROVAL_TYPE_PUSH,
                "git push origin main",
            ),
        ):
            with self.subTest(approval_type=approval_type):
                item = ApprovalItem(
                    approval_id=f"gate-{approval_type.lower()}",
                    herd_id="herd-1",
                    repo_path=self.repo,
                    approval_type=approval_type,
                    explanation="Use the dedicated Git gate.",
                    commands=(command,),
                    provider="fake",
                    model="fake-model",
                    created_at_ms=123456789,
                )
                self.queue._items[item.approval_id] = item
                self.queue._order.append(item.approval_id)

                with self.assertRaisesRegex(
                    RuntimeError,
                    "Git gate",
                ):
                    self.service.approve_and_execute(
                        item.approval_id,
                        self.session,
                        execution_id=f"exec-{approval_type.lower()}",
                    )

                self.assertEqual(
                    self.queue.get(item.approval_id),
                    item,
                )

        self.execution.execute.assert_not_called()


class GitGatePushAmbiguityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.queue = ApprovalQueue()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bare_push_does_not_become_push_approval(self):
        from mission_control.approvals import (
            APPROVAL_TYPE_NEXT_ACTION,
        )

        review = OperatorReview(
            herd_id="herd-1",
            provider="fake",
            model="fake-model",
            generated_at_ms=123456789,
            recommendation=OperatorRecommendation(
                explanation="Push the validated commit.",
                commands=("git push",),
            ),
        )

        item = self.queue.enqueue(
            self.repo,
            review,
        )

        self.assertEqual(
            item.approval_type,
            APPROVAL_TYPE_NEXT_ACTION,
        )
