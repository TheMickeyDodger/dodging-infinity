"""Append-only durable Mission Control audit journal."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


AUDIT_SCHEMA_VERSION = 1
AUDIT_FILE = "audit.jsonl"


@dataclass(frozen=True)
class AuditRecord:
    """One durable Mission Control audit record."""

    id: str
    timestamp_ms: int
    herd_id: str
    repo_path: Path
    event_type: str
    actor: str
    data: dict[str, Any]
    schema_version: int = AUDIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "timestamp_ms": self.timestamp_ms,
            "herd_id": self.herd_id,
            "repo_path": str(self.repo_path),
            "event_type": self.event_type,
            "actor": self.actor,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "AuditRecord":
        if not isinstance(data, dict):
            raise RuntimeError(
                "Mission Control audit record must be a JSON object"
            )

        version = data.get("schema_version")
        if version != AUDIT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported Mission Control audit schema: {version}"
            )

        record_id = data.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise RuntimeError(
                "Mission Control audit record has invalid id"
            )

        timestamp_ms = data.get("timestamp_ms")
        if not isinstance(timestamp_ms, int):
            raise RuntimeError(
                "Mission Control audit record has invalid timestamp_ms"
            )

        herd_id = data.get("herd_id")
        if not isinstance(herd_id, str) or not herd_id:
            raise RuntimeError(
                "Mission Control audit record has invalid herd_id"
            )

        repo_raw = data.get("repo_path")
        if not isinstance(repo_raw, str) or not repo_raw:
            raise RuntimeError(
                "Mission Control audit record has invalid repo_path"
            )

        event_type = data.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise RuntimeError(
                "Mission Control audit record has invalid event_type"
            )

        actor = data.get("actor")
        if not isinstance(actor, str) or not actor.strip():
            raise RuntimeError(
                "Mission Control audit record has invalid actor"
            )

        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError(
                "Mission Control audit record has invalid data"
            )

        return cls(
            id=record_id,
            timestamp_ms=timestamp_ms,
            herd_id=herd_id,
            repo_path=Path(repo_raw).resolve(),
            event_type=event_type,
            actor=actor,
            data=dict(payload),
            schema_version=version,
        )


class MissionControlAuditLog:
    """Append and read Mission Control audit records for one repository."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).resolve()
        self.root = (
            self.repo_path
            / ".herd"
            / "state"
            / "mission-control"
        )
        self.path = self.root / AUDIT_FILE

    def append(
        self,
        herd_id: str,
        event_type: str,
        *,
        actor: str = "mission-control",
        data: dict[str, Any] | None = None,
    ) -> AuditRecord:
        herd_id = herd_id.strip()
        event_type = event_type.strip()
        actor = actor.strip()

        if not herd_id:
            raise ValueError("herd_id cannot be empty")
        if not event_type:
            raise ValueError("event_type cannot be empty")
        if not actor:
            raise ValueError("actor cannot be empty")
        if data is not None and not isinstance(data, dict):
            raise ValueError("audit data must be an object")

        record = AuditRecord(
            id=uuid.uuid4().hex,
            timestamp_ms=time.time_ns() // 1_000_000,
            herd_id=herd_id,
            repo_path=self.repo_path,
            event_type=event_type,
            actor=actor,
            data=dict(data or {}),
        )

        encoded = (
            json.dumps(
                record.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )

        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)

        return record

    def read(
        self,
        *,
        limit: int | None = None,
        herd_id: str | None = None,
        event_types: Iterable[str] | None = None,
    ) -> list[AuditRecord]:
        if limit is not None and (
            not isinstance(limit, int)
            or limit < 1
        ):
            raise ValueError(
                "limit must be a positive integer or None"
            )

        if herd_id is not None:
            herd_id = herd_id.strip()
            if not herd_id:
                raise ValueError(
                    "herd_id cannot be empty"
                )

        type_filter = None
        if event_types is not None:
            type_filter = {
                item.strip()
                for item in event_types
                if isinstance(item, str)
                and item.strip()
            }

            if not type_filter:
                raise ValueError(
                    "event_types must contain at least one event type"
                )

        if not self.path.exists():
            return []

        records: list[AuditRecord] = []

        for line_number, raw in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            start=1,
        ):
            if not raw.strip():
                continue

            try:
                parsed = json.loads(raw)
                record = AuditRecord.from_dict(parsed)
            except Exception as exc:
                raise RuntimeError(
                    f"Mission Control audit journal is corrupt "
                    f"at line {line_number}"
                ) from exc

            if record.repo_path != self.repo_path:
                raise RuntimeError(
                    "Mission Control audit record belongs to "
                    "a different repository"
                )

            if (
                herd_id is not None
                and record.herd_id != herd_id
            ):
                continue

            if (
                type_filter is not None
                and record.event_type not in type_filter
            ):
                continue

            records.append(record)

        if limit is not None:
            records = records[-limit:]

        return records
