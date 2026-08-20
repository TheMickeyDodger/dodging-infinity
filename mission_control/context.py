"""Full handoff-context assembly for Mission Control operator review."""

from __future__ import annotations

import subprocess
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from herdr.control_plane import HerdrControlPlane
from herdr.instance import HerdrInstance

from .audit import MissionControlAuditLog
from .state import MissionControlStateStore


CONTEXT_SCHEMA_VERSION = 1
DEFAULT_AGENT_LINES = 500
DEFAULT_EVENT_LIMIT = 200

CommandRunner = Callable[
    [Sequence[str], Path],
    subprocess.CompletedProcess,
]


@dataclass(frozen=True)
class HandoffContext:
    """One complete, JSON-serializable Mission Control handoff record."""

    herd_id: str
    repo_path: Path
    generated_at_ms: int
    objective: dict[str, Any]
    herdr: dict[str, Any]
    mission_control: dict[str, Any]
    git: dict[str, Any]
    artifacts: dict[str, Any]
    schema_version: int = CONTEXT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at_ms": self.generated_at_ms,
            "herd_id": self.herd_id,
            "repo_path": str(self.repo_path),
            "objective": self.objective,
            "herdr": self.herdr,
            "mission_control": self.mission_control,
            "git": self.git,
            "artifacts": self.artifacts,
        }


def _default_runner(
    command: Sequence[str],
    cwd: Path,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class HandoffContextAssembler:
    """Assemble the evidence an operator model needs after shell handoff."""

    def __init__(
        self,
        *,
        control_plane: HerdrControlPlane | None = None,
        command_runner: CommandRunner | None = None,
        agent_lines: int = DEFAULT_AGENT_LINES,
        event_limit: int = DEFAULT_EVENT_LIMIT,
    ):
        if not isinstance(agent_lines, int) or agent_lines < 1:
            raise ValueError("agent_lines must be a positive integer")

        if not isinstance(event_limit, int) or event_limit < 1:
            raise ValueError("event_limit must be a positive integer")

        self.control_plane = control_plane or HerdrControlPlane()
        self.command_runner = command_runner or _default_runner
        self.agent_lines = agent_lines
        self.event_limit = event_limit

    def _run(
        self,
        repo: Path,
        command: Sequence[str],
    ) -> subprocess.CompletedProcess:
        return self.command_runner(tuple(command), repo)

    def _git_text(
        self,
        repo: Path,
        command: Sequence[str],
        label: str,
    ) -> str:
        result = self._run(repo, command)

        if result.returncode:
            detail = (
                (result.stderr or "").strip()
                or (result.stdout or "").strip()
                or f"exit {result.returncode}"
            )
            raise RuntimeError(
                f"Unable to collect Git {label}: {detail}"
            )

        return result.stdout or ""

    def _git_context(self, repo: Path) -> dict[str, Any]:
        head = self._git_text(
            repo,
            ("git", "rev-parse", "HEAD"),
            "HEAD",
        ).strip()

        branch = self._git_text(
            repo,
            ("git", "branch", "--show-current"),
            "branch",
        ).strip()

        status = self._git_text(
            repo,
            ("git", "status", "--porcelain=v1", "--branch"),
            "status",
        )

        unstaged_diff = self._git_text(
            repo,
            ("git", "diff", "--no-ext-diff", "--"),
            "unstaged diff",
        )

        staged_diff = self._git_text(
            repo,
            ("git", "diff", "--cached", "--no-ext-diff", "--"),
            "staged diff",
        )

        untracked_raw = self._git_text(
            repo,
            (
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            "untracked files",
        )

        untracked_files = []

        for relative in untracked_raw.split("\0"):
            if not relative:
                continue

            path = repo / relative

            if path.is_symlink():
                untracked_files.append(
                    {
                        "path": relative,
                        "kind": "symlink",
                        "target": path.readlink().as_posix(),
                    }
                )
                continue

            try:
                raw = path.read_bytes()
            except OSError as exc:
                untracked_files.append(
                    {
                        "path": relative,
                        "kind": "unreadable",
                        "error": str(exc),
                    }
                )
                continue

            if b"\0" in raw:
                untracked_files.append(
                    {
                        "path": relative,
                        "kind": "binary",
                        "size_bytes": len(raw),
                    }
                )
                continue

            untracked_files.append(
                {
                    "path": relative,
                    "kind": "text",
                    "text": raw.decode(
                        "utf-8",
                        errors="replace",
                    ),
                }
            )

        return {
            "head": head,
            "branch": branch or None,
            "status": status,
            "unstaged_diff": unstaged_diff,
            "staged_diff": staged_diff,
            "untracked_files": untracked_files,
        }

    def _agent_output(
        self,
        repo: Path,
        agent_record: dict[str, Any],
    ) -> dict[str, Any]:
        logical_name = str(
            agent_record.get("logical_name") or ""
        )
        agent = str(agent_record.get("agent") or "")
        status = str(
            agent_record.get("status") or "unknown"
        ).lower()

        result = {
            "logical_name": logical_name,
            "agent": agent,
            "status": status,
            "source": None,
            "text": "",
            "error": None,
        }

        if not agent or status == "missing":
            result["error"] = "agent unavailable"
            return result

        source = (
            "visible"
            if status in {"working", "blocked", "unknown"}
            else "recent-unwrapped"
        )

        command = [
            "herdr",
            "agent",
            "read",
            agent,
            "--source",
            source,
        ]

        if source == "recent-unwrapped":
            command.extend(
                ["--lines", str(self.agent_lines)]
            )

        completed = self._run(repo, command)

        combined_error = (
            (completed.stderr or "")
            + (completed.stdout or "")
        )

        if (
            completed.returncode
            and source != "visible"
            and "agent_not_idle" in combined_error
        ):
            source = "visible"
            completed = self._run(
                repo,
                (
                    "herdr",
                    "agent",
                    "read",
                    agent,
                    "--source",
                    "visible",
                ),
            )

        result["source"] = source

        if completed.returncode:
            result["error"] = (
                (completed.stderr or "").strip()
                or (completed.stdout or "").strip()
                or f"agent read exited {completed.returncode}"
            )
            return result

        result["text"] = completed.stdout or ""
        return result

    @staticmethod
    def _read_optional_text(path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None

        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _current_task_artifact(
        path: Path,
        task: dict[str, Any] | None,
    ) -> str | None:
        if not path.exists() or not path.is_file():
            return None

        if (
            not isinstance(task, dict)
            or not isinstance(task.get("id"), str)
            or not task.get("id")
        ):
            return None

        started_at = task.get("started_at")

        if isinstance(started_at, (int, float)):
            try:
                if path.stat().st_mtime < float(started_at):
                    return None
            except OSError:
                return None

        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    def _review_artifacts(
        self,
        herd_root: Path,
        task: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(task, dict):
            return []

        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            return []

        review_root = herd_root / "state" / "reviews"

        if not review_root.exists():
            return []

        reviews = []

        for path in sorted(
            review_root.glob(
                f"{task_id}-round-*.md"
            )
        ):
            if not path.is_file():
                continue

            reviews.append(
                {
                    "path": str(path),
                    "text": path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ),
                }
            )

        return reviews

    def _artifact_context(
        self,
        repo: Path,
        task: dict[str, Any] | None,
    ) -> dict[str, Any]:
        herd_root = repo / ".herd"

        known_context = {
            "CLAUDE.md": repo / "CLAUDE.md",
            "AGENTS.md": repo / "AGENTS.md",
            "architecture": (
                herd_root / "memory" / "architecture.md"
            ),
            "conventions": (
                herd_root / "memory" / "conventions.md"
            ),
            "decisions": (
                herd_root / "memory" / "decisions.md"
            ),
            "mistakes": (
                herd_root / "memory" / "mistakes.md"
            ),
            "task_history": (
                herd_root / "memory" / "task-history.md"
            ),
        }

        return {
            "supervisor_status": self._current_task_artifact(
                herd_root
                / "state"
                / "supervisor-status.md",
                task,
            ),
            "task_checkpoint": self._current_task_artifact(
                herd_root
                / "state"
                / "task-checkpoint.md",
                task,
            ),
            "reviews": self._review_artifacts(
                herd_root,
                task,
            ),
            "shared_context": {
                name: text
                for name, path in known_context.items()
                if (
                    text := self._read_optional_text(path)
                )
                is not None
            },
        }

    @staticmethod
    def _latest_execution(
        audit_records: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        execution_records = [
            record
            for record in audit_records
            if str(record.get("event_type") or "").startswith(
                "execution."
            )
        ]

        if not execution_records:
            return None

        latest_started = None

        for record in reversed(execution_records):
            if record.get("event_type") == "execution.started":
                execution_id = record.get("data", {}).get(
                    "execution_id"
                )

                if isinstance(execution_id, str) and execution_id:
                    latest_started = record
                    break

        if latest_started is None:
            return execution_records[-1]

        execution_id = latest_started["data"]["execution_id"]
        latest = latest_started
        seen_start = False

        for record in execution_records:
            if record is latest_started:
                seen_start = True

            if not seen_start:
                continue

            if (
                record.get("data", {}).get("execution_id")
                == execution_id
            ):
                latest = record

        return latest

    def assemble(
        self,
        repo_path: str | Path,
        *,
        herd_id: str | None = None,
    ) -> HandoffContext:
        repo = Path(repo_path).expanduser().resolve()

        store = MissionControlStateStore(repo)
        state = store.load()

        if state is None:
            raise RuntimeError(
                "No durable Mission Control Herd state exists"
            )

        if herd_id is not None:
            herd_id = herd_id.strip()

            if not herd_id:
                raise ValueError("herd_id cannot be empty")

            if state.herd_id != herd_id:
                raise RuntimeError(
                    "Requested Herd does not match durable "
                    "Mission Control state"
                )

        snapshot = self.control_plane.snapshot(repo)
        task = snapshot.get("task")
        task_dict = task if isinstance(task, dict) else {}

        instance = HerdrInstance(repo)
        config = (
            instance.load_config()
            if instance.initialized
            else {}
        )

        project = config.get("project", {})
        test_command = (
            project.get("test_command")
            if isinstance(project, dict)
            else None
        )

        objective = {
            "task_id": task_dict.get("id"),
            "status": task_dict.get("status", "IDLE"),
            "description": task_dict.get("description"),
            "rules": {
                "repository": snapshot.get("policy", {}),
                "task": task_dict.get("policy"),
            },
            "verification": {
                "test_command": test_command or None,
            },
        }

        agents = snapshot.get("runtime", {}).get(
            "agents",
            [],
        )

        agent_outputs = [
            self._agent_output(repo, record)
            for record in agents
            if isinstance(record, dict)
        ]

        herdr_events = self.control_plane.events(
            repo,
            limit=self.event_limit,
        )

        audit_records = [
            record.to_dict()
            for record in MissionControlAuditLog(repo).read(
                herd_id=state.herd_id,
            )
        ]

        mission_control = {
            "state": state.to_dict(),
            "audit": audit_records,
            "latest_execution": self._latest_execution(
                audit_records
            ),
        }

        herdr = {
            "snapshot": snapshot,
            "events": herdr_events,
            "agent_outputs": agent_outputs,
        }

        return HandoffContext(
            herd_id=state.herd_id,
            repo_path=repo,
            generated_at_ms=(
                time.time_ns() // 1_000_000
            ),
            objective=objective,
            herdr=herdr,
            mission_control=mission_control,
            git=self._git_context(repo),
            artifacts=self._artifact_context(
                repo,
                task_dict,
            ),
        )
