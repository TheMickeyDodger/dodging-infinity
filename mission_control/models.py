"""Model-provider abstraction and Mission Control operator review."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .audit import MissionControlAuditLog
from .context import HandoffContext, HandoffContextAssembler


DEFAULT_OPERATOR_PROVIDER = "codex"
DEFAULT_OPERATOR_MODEL = "gpt-5.6-sol"
OPERATOR_REVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class OperatorRecommendation:
    """One model-produced Mission Control operator recommendation."""

    explanation: str
    commands: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "commands": list(self.commands),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "OperatorRecommendation":
        if not isinstance(data, dict):
            raise RuntimeError(
                "Operator recommendation must be a JSON object"
            )

        explanation = data.get("explanation")
        commands = data.get("commands")

        if not isinstance(explanation, str):
            raise RuntimeError(
                "Operator recommendation has invalid explanation"
            )

        explanation = explanation.strip()

        if not explanation:
            raise RuntimeError(
                "Operator recommendation explanation cannot be empty"
            )

        if not isinstance(commands, list):
            raise RuntimeError(
                "Operator recommendation commands must be an array"
            )

        exact_commands = []

        for command in commands:
            if not isinstance(command, str) or not command:
                raise RuntimeError(
                    "Operator recommendation contains invalid command"
                )

            exact_commands.append(command)

        return cls(
            explanation=explanation,
            commands=tuple(exact_commands),
        )


class OperatorModelProvider(Protocol):
    """Provider contract for one-shot Mission Control reasoning."""

    provider_name: str
    model_name: str

    def review(
        self,
        context: HandoffContext,
    ) -> OperatorRecommendation:
        ...


class CodexOperatorProvider:
    """Read-only one-shot Codex provider for operator review."""

    provider_name = "codex"

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPERATOR_MODEL,
        executable: str = "codex",
        timeout: float = 300.0,
        reasoning_effort: str = "high",
    ):
        model = model.strip()
        executable = executable.strip()
        reasoning_effort = reasoning_effort.strip()

        if not model:
            raise ValueError("model cannot be empty")

        if not executable:
            raise ValueError("executable cannot be empty")

        if timeout <= 0:
            raise ValueError("timeout must be positive")

        if not reasoning_effort:
            raise ValueError(
                "reasoning_effort cannot be empty"
            )

        self.model_name = model
        self.executable = executable
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                },
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },
            "required": [
                "explanation",
                "commands",
            ],
            "additionalProperties": False,
        }

    @staticmethod
    def _prompt(
        context: HandoffContext,
    ) -> str:
        payload = json.dumps(
            context.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )

        return f"""You are the Mission Control operator for Dodging Infinity.

Mission Control sits above Herdr. Herdr remains the engineering orchestration engine. You are not the Supervisor and you must not redesign, bypass, or replace Herdr.

You are reviewing a deterministic handoff after control returned from a Herd-owned terminal session.

Your job:
- inspect the complete handoff context below;
- explain what happened and what the human should know;
- recommend the exact next terminal commands, in execution order, if any;
- return no commands when no terminal action is needed.

Hard rules:
- Do NOT execute commands or modify files.
- Do NOT assume any command has been approved.
- Do NOT wrap, chain, rewrite, or silently alter commands for convenience.
- Every item in `commands` must be one exact terminal command intended to be entered independently, in order.
- Do NOT include markdown fences around commands.
- Do NOT bypass Herdr safety controls, Git hooks, commit approval, or push approval.
- Never recommend `--no-verify`, force push, force-with-lease, mirror push, destructive ref rewrites, or safety-control bypasses.
- Treat uncertain execution state, interactive prompts, missing sessions, malformed evidence, or conflicting state as unresolved. Do not guess.
- If an execution is unresolved, recommend only commands that safely inspect or resolve that exact state.
- Base the recommendation on the supplied context. Do not invent successful tests, approvals, commits, pushes, or agent outcomes.
- The human will review the exact returned sequence before anything can execute.

HANDOFF_CONTEXT_JSON:
{payload}
"""

    def review(
        self,
        context: HandoffContext,
    ) -> OperatorRecommendation:
        if not isinstance(context, HandoffContext):
            raise TypeError(
                "context must be a HandoffContext"
            )

        with tempfile.TemporaryDirectory(
            prefix="dodging-infinity-operator-"
        ) as td:
            root = Path(td)
            schema_path = root / "schema.json"
            output_path = root / "response.json"

            schema_path.write_text(
                json.dumps(self._schema()),
                encoding="utf-8",
            )

            command = [
                self.executable,
                "exec",
                "-m",
                self.model_name,
                "-s",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                (
                    "model_reasoning_effort="
                    + json.dumps(self.reasoning_effort)
                ),
                "--ephemeral",
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ]

            try:
                result = subprocess.run(
                    command,
                    cwd=context.repo_path,
                    input=self._prompt(context),
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "Operator model invocation timed out"
                ) from exc

            if result.returncode:
                detail = (
                    (result.stderr or "").strip()
                    or (result.stdout or "").strip()
                    or f"exit {result.returncode}"
                )

                raise RuntimeError(
                    "Operator model invocation failed: "
                    + detail
                )

            if not output_path.exists():
                raise RuntimeError(
                    "Operator model produced no final response"
                )

            try:
                parsed = json.loads(
                    output_path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    "Operator model response is not valid JSON"
                ) from exc

        return OperatorRecommendation.from_dict(
            parsed
        )


def create_operator_provider(
    provider: str = DEFAULT_OPERATOR_PROVIDER,
    *,
    model: str = DEFAULT_OPERATOR_MODEL,
) -> OperatorModelProvider:
    """Create a configured Mission Control operator provider."""

    provider = provider.strip().lower()

    if provider == "codex":
        return CodexOperatorProvider(
            model=model,
        )

    raise ValueError(
        f"Unsupported operator provider: {provider}"
    )


@dataclass(frozen=True)
class OperatorReview:
    """Auditable result of one Mission Control operator review."""

    herd_id: str
    provider: str
    model: str
    generated_at_ms: int
    recommendation: OperatorRecommendation
    schema_version: int = OPERATOR_REVIEW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "herd_id": self.herd_id,
            "provider": self.provider,
            "model": self.model,
            "generated_at_ms": self.generated_at_ms,
            "recommendation": (
                self.recommendation.to_dict()
            ),
        }


class OperatorReviewService:
    """Assemble context, invoke an operator model, and audit its proposal."""

    def __init__(
        self,
        provider: OperatorModelProvider,
        *,
        context_assembler: HandoffContextAssembler | None = None,
    ):
        self.provider = provider
        self.context_assembler = (
            context_assembler
            or HandoffContextAssembler()
        )

    def review(
        self,
        repo_path: str | Path,
        *,
        herd_id: str | None = None,
    ) -> OperatorReview:
        context = self.context_assembler.assemble(
            repo_path,
            herd_id=herd_id,
        )

        audit = MissionControlAuditLog(
            context.repo_path
        )

        audit.append(
            context.herd_id,
            "operator.review.started",
            data={
                "provider": self.provider.provider_name,
                "model": self.provider.model_name,
            },
        )

        try:
            recommendation = self.provider.review(
                context
            )
        except Exception as exc:
            audit.append(
                context.herd_id,
                "operator.review.error",
                data={
                    "provider": self.provider.provider_name,
                    "model": self.provider.model_name,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        review = OperatorReview(
            herd_id=context.herd_id,
            provider=self.provider.provider_name,
            model=self.provider.model_name,
            generated_at_ms=(
                time.time_ns() // 1_000_000
            ),
            recommendation=recommendation,
        )

        audit.append(
            context.herd_id,
            "operator.review.completed",
            data={
                "provider": review.provider,
                "model": review.model,
                "explanation": (
                    recommendation.explanation
                ),
                "commands": list(
                    recommendation.commands
                ),
            },
        )

        return review
