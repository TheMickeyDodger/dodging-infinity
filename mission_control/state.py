"""Durable per-Herd Mission Control state."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


STATE_SCHEMA_VERSION = 1
STATE_FILE = "herd-state.json"

LIFECYCLE_STARTING = "STARTING"
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_DISCONNECTED = "DISCONNECTED"
LIFECYCLE_CLOSED = "CLOSED"

VALID_LIFECYCLES = {
    LIFECYCLE_STARTING,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_DISCONNECTED,
    LIFECYCLE_CLOSED,
}

_HERD_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class MissionControlHerdState:
    """Persistent Mission Control identity and lifecycle for one Herd."""

    herd_id: str
    repo_path: Path
    lifecycle: str
    terminal_id: str | None
    active_execution_id: str | None
    created_at_ms: int
    updated_at_ms: int
    disconnected_reason: str | None = None
    closed_at_ms: int | None = None
    schema_version: int = STATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "herd_id": self.herd_id,
            "repo_path": str(self.repo_path),
            "lifecycle": self.lifecycle,
            "terminal_id": self.terminal_id,
            "active_execution_id": self.active_execution_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "disconnected_reason": self.disconnected_reason,
            "closed_at_ms": self.closed_at_ms,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "MissionControlHerdState":
        if not isinstance(data, dict):
            raise RuntimeError(
                "Mission Control Herd state must be a JSON object"
            )

        version = data.get("schema_version")
        if version != STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported Mission Control Herd state schema: {version}"
            )

        herd_id = data.get("herd_id")
        if (
            not isinstance(herd_id, str)
            or not _HERD_ID.fullmatch(herd_id)
        ):
            raise RuntimeError(
                "Mission Control Herd state has invalid herd_id"
            )

        repo_raw = data.get("repo_path")
        if not isinstance(repo_raw, str) or not repo_raw:
            raise RuntimeError(
                "Mission Control Herd state has invalid repo_path"
            )

        lifecycle = data.get("lifecycle")
        if lifecycle not in VALID_LIFECYCLES:
            raise RuntimeError(
                "Mission Control Herd state has invalid lifecycle"
            )

        terminal_id = data.get("terminal_id")
        if terminal_id is not None and not isinstance(
            terminal_id,
            str,
        ):
            raise RuntimeError(
                "Mission Control Herd state has invalid terminal_id"
            )

        active_execution_id = data.get("active_execution_id")
        if active_execution_id is not None and not isinstance(
            active_execution_id,
            str,
        ):
            raise RuntimeError(
                "Mission Control Herd state has invalid "
                "active_execution_id"
            )

        created_at_ms = data.get("created_at_ms")
        updated_at_ms = data.get("updated_at_ms")

        if not isinstance(created_at_ms, int):
            raise RuntimeError(
                "Mission Control Herd state has invalid created_at_ms"
            )
        if not isinstance(updated_at_ms, int):
            raise RuntimeError(
                "Mission Control Herd state has invalid updated_at_ms"
            )

        disconnected_reason = data.get("disconnected_reason")
        if (
            disconnected_reason is not None
            and not isinstance(disconnected_reason, str)
        ):
            raise RuntimeError(
                "Mission Control Herd state has invalid "
                "disconnected_reason"
            )

        closed_at_ms = data.get("closed_at_ms")
        if closed_at_ms is not None and not isinstance(
            closed_at_ms,
            int,
        ):
            raise RuntimeError(
                "Mission Control Herd state has invalid closed_at_ms"
            )

        return cls(
            herd_id=herd_id,
            repo_path=Path(repo_raw).resolve(),
            lifecycle=lifecycle,
            terminal_id=terminal_id,
            active_execution_id=active_execution_id,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            disconnected_reason=disconnected_reason,
            closed_at_ms=closed_at_ms,
            schema_version=version,
        )


class MissionControlStateStore:
    """Atomic durable storage for one repository's active Herd state."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).resolve()
        self.root = (
            self.repo_path
            / ".herd"
            / "state"
            / "mission-control"
        )
        self.path = self.root / STATE_FILE

    def _validate_herd_id(self, herd_id: str) -> str:
        if not _HERD_ID.fullmatch(herd_id):
            raise ValueError(
                "herd_id may contain only letters, numbers, "
                "dot, underscore, and hyphen"
            )
        return herd_id

    def load(self) -> MissionControlHerdState | None:
        if not self.path.exists():
            return None

        try:
            data = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise RuntimeError(
                f"Mission Control Herd state is unreadable: "
                f"{self.path}"
            ) from exc

        state = MissionControlHerdState.from_dict(data)

        if state.repo_path != self.repo_path:
            raise RuntimeError(
                "Mission Control Herd state belongs to a "
                "different repository"
            )

        return state

    def save(
        self,
        state: MissionControlHerdState,
    ) -> MissionControlHerdState:
        if state.repo_path.resolve() != self.repo_path:
            raise ValueError(
                "Cannot save Mission Control state for "
                "a different repository"
            )

        MissionControlHerdState.from_dict(
            state.to_dict()
        )

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        fd, temp_name = tempfile.mkstemp(
            prefix=".herd-state-",
            suffix=".tmp",
            dir=self.root,
        )
        temp_path = Path(temp_name)

        try:
            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    state.to_dict(),
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(
                temp_path,
                self.path,
            )
        finally:
            temp_path.unlink(
                missing_ok=True
            )

        return state

    def create(
        self,
        herd_id: str,
    ) -> MissionControlHerdState:
        herd_id = self._validate_herd_id(herd_id)
        existing = self.load()

        if (
            existing is not None
            and existing.lifecycle != LIFECYCLE_CLOSED
        ):
            raise RuntimeError(
                f"Repository already has active Mission Control "
                f"Herd {existing.herd_id} "
                f"({existing.lifecycle})"
            )

        now = _now_ms()

        return self.save(
            MissionControlHerdState(
                herd_id=herd_id,
                repo_path=self.repo_path,
                lifecycle=LIFECYCLE_STARTING,
                terminal_id=None,
                active_execution_id=None,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )

    def attach_session(
        self,
        terminal_id: str,
    ) -> MissionControlHerdState:
        if not terminal_id:
            raise ValueError(
                "terminal_id cannot be empty"
            )

        state = self._require_open()
        now = _now_ms()

        return self.save(
            replace(
                state,
                lifecycle=LIFECYCLE_ACTIVE,
                terminal_id=terminal_id,
                disconnected_reason=None,
                closed_at_ms=None,
                updated_at_ms=now,
            )
        )

    def begin_execution(
        self,
        execution_id: str,
    ) -> MissionControlHerdState:
        if not execution_id:
            raise ValueError(
                "execution_id cannot be empty"
            )

        state = self._require_active()

        if state.active_execution_id is not None:
            raise RuntimeError(
                f"Herd already has active execution "
                f"{state.active_execution_id}"
            )

        return self.save(
            replace(
                state,
                active_execution_id=execution_id,
                updated_at_ms=_now_ms(),
            )
        )

    def finish_execution(
        self,
        execution_id: str,
    ) -> MissionControlHerdState:
        state = self._require_open()

        if state.active_execution_id != execution_id:
            raise RuntimeError(
                f"Execution {execution_id} is not the "
                "active Herd execution"
            )

        return self.save(
            replace(
                state,
                active_execution_id=None,
                updated_at_ms=_now_ms(),
            )
        )

    def disconnect(
        self,
        reason: str,
    ) -> MissionControlHerdState:
        reason = reason.strip()
        if not reason:
            raise ValueError(
                "disconnect reason cannot be empty"
            )

        state = self._require_open()

        return self.save(
            replace(
                state,
                lifecycle=LIFECYCLE_DISCONNECTED,
                disconnected_reason=reason,
                updated_at_ms=_now_ms(),
            )
        )

    def reconnect(
        self,
        terminal_id: str,
    ) -> MissionControlHerdState:
        state = self.load()

        if state is None:
            raise RuntimeError(
                "No Mission Control Herd state exists"
            )
        if state.lifecycle != LIFECYCLE_DISCONNECTED:
            raise RuntimeError(
                "Only a disconnected Herd can be reconnected"
            )
        if not terminal_id:
            raise ValueError(
                "terminal_id cannot be empty"
            )

        return self.save(
            replace(
                state,
                lifecycle=LIFECYCLE_ACTIVE,
                terminal_id=terminal_id,
                disconnected_reason=None,
                updated_at_ms=_now_ms(),
            )
        )

    def close(self) -> MissionControlHerdState:
        state = self._require_open()
        now = _now_ms()

        return self.save(
            replace(
                state,
                lifecycle=LIFECYCLE_CLOSED,
                terminal_id=None,
                active_execution_id=None,
                disconnected_reason=None,
                closed_at_ms=now,
                updated_at_ms=now,
            )
        )

    def _require_open(self) -> MissionControlHerdState:
        state = self.load()

        if state is None:
            raise RuntimeError(
                "No Mission Control Herd state exists"
            )

        if state.lifecycle == LIFECYCLE_CLOSED:
            raise RuntimeError(
                "Mission Control Herd is closed"
            )

        return state

    def _require_active(self) -> MissionControlHerdState:
        state = self._require_open()

        if state.lifecycle != LIFECYCLE_ACTIVE:
            raise RuntimeError(
                "Mission Control Herd is not active"
            )

        return state
