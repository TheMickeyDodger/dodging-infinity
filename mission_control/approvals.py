"""Ephemeral global human-approval queue for Mission Control."""

from __future__ import annotations

import threading
import time
import uuid

from dataclasses import dataclass
from pathlib import Path

from .audit import MissionControlAuditLog
from .models import OperatorReview


APPROVAL_TYPE_NEXT_ACTION = "NEXT_ACTION"


@dataclass(frozen=True)
class ApprovalItem:
    """One exact command proposal awaiting human disposition."""

    approval_id: str
    herd_id: str
    repo_path: Path
    approval_type: str
    explanation: str
    commands: tuple[str, ...]
    provider: str
    model: str
    created_at_ms: int
    source_execution_id: str | None = None


class ApprovalQueue:
    """Process-local global queue of pending human approvals."""

    def __init__(self):
        self._items: dict[str, ApprovalItem] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def pending(self) -> tuple[ApprovalItem, ...]:
        with self._lock:
            return tuple(
                self._items[approval_id]
                for approval_id in self._order
            )

    def get(
        self,
        approval_id: str,
    ) -> ApprovalItem:
        with self._lock:
            try:
                return self._items[approval_id]
            except KeyError as exc:
                raise KeyError(
                    f"Unknown approval: {approval_id}"
                ) from exc

    def enqueue(
        self,
        repo_path: str | Path,
        review: OperatorReview,
    ) -> ApprovalItem | None:
        repo = Path(repo_path).resolve()
        commands = tuple(
            review.recommendation.commands
        )

        if not commands:
            MissionControlAuditLog(repo).append(
                review.herd_id,
                "approval.not_required",
                data={
                    "provider": review.provider,
                    "model": review.model,
                    "explanation": (
                        review.recommendation.explanation
                    ),
                    "commands": [],
                },
            )
            return None

        with self._lock:
            if any(
                item.herd_id == review.herd_id
                for item in self._items.values()
            ):
                raise RuntimeError(
                    f"Herd {review.herd_id} already has a pending approval"
                )

            item = ApprovalItem(
                approval_id=uuid.uuid4().hex,
                herd_id=review.herd_id,
                repo_path=repo,
                approval_type=APPROVAL_TYPE_NEXT_ACTION,
                explanation=review.recommendation.explanation,
                commands=commands,
                provider=review.provider,
                model=review.model,
                created_at_ms=time.time_ns() // 1_000_000,
                source_execution_id=review.source_execution_id,
            )

            self._items[item.approval_id] = item
            self._order.append(item.approval_id)

        MissionControlAuditLog(repo).append(
            item.herd_id,
            "approval.queued",
            data={
                "approval_id": item.approval_id,
                "approval_type": item.approval_type,
                "provider": item.provider,
                "model": item.model,
                "source_execution_id": item.source_execution_id,
                "explanation": item.explanation,
                "commands": list(item.commands),
            },
        )

        return item

    def approve(
        self,
        approval_id: str,
    ) -> ApprovalItem:
        item = self._remove(approval_id)

        MissionControlAuditLog(
            item.repo_path
        ).append(
            item.herd_id,
            "approval.approved",
            actor="human",
            data={
                "approval_id": item.approval_id,
                "approval_type": item.approval_type,
                "commands": list(item.commands),
            },
        )

        return item

    def reject(
        self,
        approval_id: str,
    ) -> ApprovalItem:
        item = self._remove(approval_id)

        MissionControlAuditLog(
            item.repo_path
        ).append(
            item.herd_id,
            "approval.rejected",
            actor="human",
            data={
                "approval_id": item.approval_id,
                "approval_type": item.approval_type,
                "commands": list(item.commands),
            },
        )

        return item

    def _remove(
        self,
        approval_id: str,
    ) -> ApprovalItem:
        with self._lock:
            try:
                item = self._items.pop(approval_id)
            except KeyError as exc:
                raise KeyError(
                    f"Unknown approval: {approval_id}"
                ) from exc

            self._order.remove(approval_id)
            return item


class ApprovalExecutionService:
    """Translate one human approval into exact deterministic execution."""

    def __init__(
        self,
        queue: ApprovalQueue,
        execution_service,
        *,
        safety_policy=None,
    ):
        from .safety import CommandSafetyPolicy

        self.queue = queue
        self.execution_service = execution_service
        self.safety_policy = (
            safety_policy
            or CommandSafetyPolicy()
        )

    def approve_and_execute(
        self,
        approval_id: str,
        session,
        *,
        execution_id: str,
    ):
        item = self.queue.get(
            approval_id
        )

        if item.herd_id != session.herd_id:
            raise RuntimeError(
                "Approval belongs to a different Herd"
            )

        if (
            item.repo_path
            != Path(session.repo_path).resolve()
        ):
            raise RuntimeError(
                "Approval belongs to a different repository"
            )

        approved = self.queue.approve(
            approval_id
        )

        try:
            self.safety_policy.validate(
                approved.commands,
                approval_type=approved.approval_type,
            )
        except Exception as exc:
            MissionControlAuditLog(
                approved.repo_path
            ).append(
                approved.herd_id,
                "approval.safety_blocked",
                data={
                    "approval_id": approved.approval_id,
                    "approval_type": approved.approval_type,
                    "commands": list(
                        approved.commands
                    ),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        return self.execution_service.execute(
            session,
            execution_id,
            approved.commands,
        )

class OperatorApprovalService:
    """Turn deterministic handoffs into pending global approvals."""

    def __init__(
        self,
        queue: ApprovalQueue,
        review_service,
    ):
        self.queue = queue
        self.review_service = review_service
        self._review_lock = threading.RLock()
        self._reviewing_execution_ids: set[str] = set()

    def review_and_queue(
        self,
        repo_path: str | Path,
        *,
        herd_id: str | None = None,
    ) -> ApprovalItem | None:
        repo = Path(repo_path).resolve()

        review = self.review_service.review(
            repo,
            herd_id=herd_id,
        )

        return self.queue.enqueue(
            repo,
            review,
        )

    def review_latest_handoff(
        self,
        repo_path: str | Path,
        *,
        herd_id: str,
        on_started=None,
    ) -> ApprovalItem | None:
        repo = Path(repo_path).resolve()
        records = MissionControlAuditLog(repo).read(
            herd_id=herd_id,
        )

        execution_records = [
            record
            for record in records
            if record.event_type.startswith("execution.")
        ]

        latest_started = None

        for record in reversed(execution_records):
            if record.event_type == "execution.started":
                execution_id = record.data.get("execution_id")

                if isinstance(execution_id, str) and execution_id:
                    latest_started = record
                    break

        if latest_started is None:
            return None

        execution_id = latest_started.data["execution_id"]
        latest = latest_started
        seen_start = False

        for record in execution_records:
            if record is latest_started:
                seen_start = True

            if not seen_start:
                continue

            if record.data.get("execution_id") == execution_id:
                latest = record

        if latest.event_type not in {
            "execution.completed",
            "execution.failed",
        }:
            return None

        with self._review_lock:
            if execution_id in self._reviewing_execution_ids:
                return None

            if any(
                item.herd_id == herd_id
                and item.source_execution_id == execution_id
                for item in self.queue.pending()
            ):
                return None

            if any(
                record.event_type.startswith("operator.review.")
                and record.data.get("source_execution_id")
                == execution_id
                for record in records
            ):
                return None

            self._reviewing_execution_ids.add(
                execution_id
            )

        try:
            if on_started is not None:
                try:
                    on_started(execution_id)
                except Exception:
                    pass

            item = self.review_and_queue(
                repo,
                herd_id=herd_id,
            )

            if (
                item is not None
                and item.source_execution_id != execution_id
            ):
                raise RuntimeError(
                    "Operator review source execution does not "
                    "match the triggering handoff"
                )

            return item
        finally:
            with self._review_lock:
                self._reviewing_execution_ids.discard(
                    execution_id
                )
