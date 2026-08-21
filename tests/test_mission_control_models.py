import json
import subprocess
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from mission_control.audit import MissionControlAuditLog
from mission_control.context import HandoffContext
from mission_control.models import (
    DEFAULT_OPERATOR_MODEL,
    CodexOperatorProvider,
    OperatorRecommendation,
    OperatorReviewService,
    create_operator_provider,
)
from mission_control.state import MissionControlStateStore


class FakeAssembler:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def assemble(self, repo_path, *, herd_id=None):
        self.calls.append(
            (Path(repo_path).resolve(), herd_id)
        )
        return self.context


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, recommendation=None, error=None):
        self.recommendation = recommendation
        self.error = error
        self.contexts = []

    def review(self, context):
        self.contexts.append(context)

        if self.error is not None:
            raise self.error

        return self.recommendation


class MissionControlModelTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()

        store = MissionControlStateStore(self.repo)
        store.create("herd-1")
        store.attach_session("terminal-1")

        self.context = HandoffContext(
            herd_id="herd-1",
            repo_path=self.repo,
            generated_at_ms=123,
            objective={
                "description": "Review the handoff",
            },
            herdr={
                "snapshot": {},
                "events": [],
                "agent_outputs": [],
            },
            mission_control={
                "state": store.load().to_dict(),
                "audit": [],
                "latest_execution": None,
            },
            git={
                "head": "abc123",
                "branch": "main",
                "status": "",
                "unstaged_diff": "",
                "staged_diff": "",
                "untracked_files": [],
            },
            artifacts={
                "supervisor_status": None,
                "task_checkpoint": None,
                "reviews": [],
                "shared_context": {},
            },
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_recommendation_round_trip_preserves_exact_commands(self):
        recommendation = OperatorRecommendation.from_dict(
            {
                "explanation": "Inspect state first.",
                "commands": [
                    "git status --short",
                    "python -m unittest",
                ],
            }
        )

        self.assertEqual(
            recommendation.explanation,
            "Inspect state first.",
        )
        self.assertEqual(
            recommendation.commands,
            (
                "git status --short",
                "python -m unittest",
            ),
        )
        self.assertEqual(
            recommendation.to_dict()["commands"],
            [
                "git status --short",
                "python -m unittest",
            ],
        )

    def test_recommendation_rejects_invalid_shape(self):
        invalid = [
            None,
            {},
            {
                "explanation": "",
                "commands": [],
            },
            {
                "explanation": "ok",
                "commands": "git status",
            },
            {
                "explanation": "ok",
                "commands": [""],
            },
        ]

        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    OperatorRecommendation.from_dict(
                        payload
                    )

    def test_factory_uses_codex_and_default_model(self):
        provider = create_operator_provider()

        self.assertIsInstance(
            provider,
            CodexOperatorProvider,
        )
        self.assertEqual(
            provider.model_name,
            DEFAULT_OPERATOR_MODEL,
        )

    def test_factory_rejects_unknown_provider(self):
        with self.assertRaisesRegex(
            ValueError,
            "Unsupported operator provider",
        ):
            create_operator_provider("unknown")

    def test_codex_provider_is_read_only_ephemeral_and_schema_constrained(self):
        provider = CodexOperatorProvider(
            model="gpt-test",
            reasoning_effort="high",
        )

        captured = {}

        def fake_run(
            command,
            *,
            cwd,
            input,
            text,
            capture_output,
            check,
            timeout,
        ):
            captured["command"] = list(command)
            captured["cwd"] = Path(cwd)
            captured["input"] = input
            captured["timeout"] = timeout

            output_index = command.index("-o") + 1
            output_path = Path(
                command[output_index]
            )
            output_path.write_text(
                json.dumps(
                    {
                        "explanation": (
                            "Run the exact inspection command."
                        ),
                        "commands": [
                            "git status --short"
                        ],
                    }
                )
            )

            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr="",
            )

        with patch(
            "mission_control.models.subprocess.run",
            side_effect=fake_run,
        ):
            recommendation = provider.review(
                self.context
            )

        command = captured["command"]

        self.assertEqual(
            command[:2],
            ["codex", "exec"],
        )
        self.assertIn("gpt-test", command)
        self.assertIn("read-only", command)
        self.assertIn(
            'approval_policy="never"',
            command,
        )
        self.assertIn("--ephemeral", command)
        self.assertIn("--output-schema", command)
        self.assertEqual(
            captured["cwd"],
            self.repo,
        )
        self.assertIn(
            "Do NOT execute commands",
            captured["input"],
        )
        self.assertIn(
            '"herd_id":"herd-1"',
            captured["input"],
        )
        self.assertEqual(
            recommendation.commands,
            ("git status --short",),
        )

    def test_codex_provider_fails_on_process_error(self):
        provider = CodexOperatorProvider()

        failed = subprocess.CompletedProcess(
            ["codex"],
            1,
            stdout="",
            stderr="provider failed",
        )

        with patch(
            "mission_control.models.subprocess.run",
            return_value=failed,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "provider failed",
            ):
                provider.review(self.context)

    def test_review_service_audits_completed_proposal(self):
        recommendation = OperatorRecommendation(
            explanation="Inspect status.",
            commands=("git status --short",),
        )
        provider = FakeProvider(
            recommendation=recommendation
        )
        assembler = FakeAssembler(self.context)

        service = OperatorReviewService(
            provider,
            context_assembler=assembler,
        )

        review = service.review(
            self.repo,
            herd_id="herd-1",
        )

        self.assertEqual(
            review.herd_id,
            "herd-1",
        )
        self.assertEqual(
            review.provider,
            "fake",
        )
        self.assertEqual(
            review.model,
            "fake-model",
        )
        self.assertEqual(
            review.recommendation,
            recommendation,
        )
        self.assertEqual(
            provider.contexts,
            [self.context],
        )

        records = MissionControlAuditLog(
            self.repo
        ).read(
            herd_id="herd-1",
        )

        self.assertEqual(
            [r.event_type for r in records],
            [
                "operator.review.started",
                "operator.review.completed",
            ],
        )
        self.assertEqual(
            records[-1].data["commands"],
            ["git status --short"],
        )

    def test_review_service_audits_provider_error(self):
        provider = FakeProvider(
            error=RuntimeError("model unavailable")
        )
        service = OperatorReviewService(
            provider,
            context_assembler=FakeAssembler(
                self.context
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "model unavailable",
        ):
            service.review(
                self.repo,
                herd_id="herd-1",
            )

        records = MissionControlAuditLog(
            self.repo
        ).read(
            herd_id="herd-1",
        )

        self.assertEqual(
            [r.event_type for r in records],
            [
                "operator.review.started",
                "operator.review.error",
            ],
        )
        self.assertEqual(
            records[-1].data["error_type"],
            "RuntimeError",
        )

    def test_review_service_does_not_execute_recommended_commands(self):
        marker = self.repo / "must-not-exist"

        provider = FakeProvider(
            recommendation=OperatorRecommendation(
                explanation="Proposal only.",
                commands=(
                    f"touch {marker}",
                ),
            )
        )

        service = OperatorReviewService(
            provider,
            context_assembler=FakeAssembler(
                self.context
            ),
        )

        review = service.review(self.repo)

        self.assertEqual(
            review.recommendation.commands,
            (f"touch {marker}",),
        )
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
