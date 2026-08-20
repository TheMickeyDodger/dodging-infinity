"""Deterministic shell handoff protocol for Mission Control."""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


HANDOFF_PREFIX = "HERDR_HANDOFF"
COMMAND_PREFIX = "HERDR_COMMAND"
ARMED_PREFIX = "HERDR_ARMED"

_EXECUTION_ID = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class CommandMarker:
    """Observed completion state for one command in an execution."""

    execution_id: str
    command_index: int
    exit_code: int
    raw: str


@dataclass(frozen=True)
class HandoffMarker:
    """Canonical return-to-human marker for one execution."""

    execution_id: str
    exit_code: int
    raw: str


class HandoffChannel:
    """Repo-local side channel used to observe shell completion."""

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path).resolve()
        self.root = (
            self.repo_path
            / ".herd"
            / "state"
            / "mission-control"
            / "executions"
        )

    def _validate_execution_id(self, execution_id: str) -> str:
        if not _EXECUTION_ID.fullmatch(execution_id):
            raise ValueError(
                "execution_id may contain only letters, numbers, "
                "dot, underscore, and hyphen"
            )
        return execution_id

    def execution_dir(self, execution_id: str) -> Path:
        execution_id = self._validate_execution_id(execution_id)
        return self.root / execution_id

    def armed_path(self, execution_id: str, command_index: int) -> Path:
        if command_index < 0:
            raise ValueError("command_index must be non-negative")
        return self.execution_dir(execution_id) / (
            f"command-{command_index:04d}.armed"
        )

    def command_path(self, execution_id: str, command_index: int) -> Path:
        if command_index < 0:
            raise ValueError("command_index must be non-negative")
        return self.execution_dir(execution_id) / (
            f"command-{command_index:04d}.marker"
        )

    def handoff_path(self, execution_id: str) -> Path:
        return self.execution_dir(execution_id) / "handoff.marker"

    def prepare(self, execution_id: str) -> None:
        directory = self.execution_dir(execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.marker", "*.armed"):
            for marker in directory.glob(pattern):
                marker.unlink()

    def read_command(
        self,
        execution_id: str,
        command_index: int,
    ) -> CommandMarker | None:
        path = self.command_path(execution_id, command_index)
        if not path.exists():
            return None

        raw = path.read_text().strip()
        expected = (
            f"{COMMAND_PREFIX}:{execution_id}:{command_index}:"
        )
        if not raw.startswith(expected):
            raise RuntimeError(f"Invalid command marker: {raw}")

        exit_text = raw[len(expected):]
        try:
            exit_code = int(exit_text)
        except ValueError as exc:
            raise RuntimeError(f"Invalid command marker: {raw}") from exc

        return CommandMarker(
            execution_id=execution_id,
            command_index=command_index,
            exit_code=exit_code,
            raw=raw,
        )

    def read_handoff(self, execution_id: str) -> HandoffMarker | None:
        path = self.handoff_path(execution_id)
        if not path.exists():
            return None

        raw = path.read_text().strip()
        expected = f"{HANDOFF_PREFIX}:{execution_id}:"
        if not raw.startswith(expected):
            raise RuntimeError(f"Invalid handoff marker: {raw}")

        exit_text = raw[len(expected):]
        try:
            exit_code = int(exit_text)
        except ValueError as exc:
            raise RuntimeError(f"Invalid handoff marker: {raw}") from exc

        return HandoffMarker(
            execution_id=execution_id,
            exit_code=exit_code,
            raw=raw,
        )


class ZshHandoffProtocol:
    """Arm one-shot zsh hooks without changing the approved command."""

    def __init__(self, channel: HandoffChannel):
        self.channel = channel

    def arm_command(
        self,
        execution_id: str,
        command_index: int,
        *,
        final_command: bool,
    ) -> str:
        armed_path = self.channel.armed_path(
            execution_id,
            command_index,
        )
        command_path = self.channel.command_path(
            execution_id,
            command_index,
        )
        handoff_path = self.channel.handoff_path(execution_id)

        token = hashlib.sha256(
            f"{execution_id}:{command_index}".encode()
        ).hexdigest()[:12]

        seen = f"__mc_seen_{token}"
        preexec = f"__mc_preexec_{token}"
        precmd = f"__mc_precmd_{token}"

        armed_marker = (
            f"{ARMED_PREFIX}:{execution_id}:{command_index}"
        )
        command_marker = (
            f"{COMMAND_PREFIX}:{execution_id}:{command_index}:"
        )
        handoff_marker = f"{HANDOFF_PREFIX}:{execution_id}:"

        final_test = "1" if final_command else "0"

        return " ".join([
            "autoload -Uz add-zsh-hook;",
            f"typeset -g {seen}=0;",
            f"function {preexec}() {{ typeset -g {seen}=1; }};",
            f"function {precmd}() {{",
            "local __mc_rc=$?;",
            f"if (( {seen} )); then",
            "printf '%s%s\\n'",
            shlex.quote(command_marker),
            '"$__mc_rc"',
            ">",
            shlex.quote(str(command_path)),
            ";",
            f"if (( __mc_rc != 0 || {final_test} )); then",
            "printf '%s%s\\n'",
            shlex.quote(handoff_marker),
            '"$__mc_rc"',
            ">",
            shlex.quote(str(handoff_path)),
            "; fi;",
            f"add-zsh-hook -d preexec {preexec};",
            f"add-zsh-hook -d precmd {precmd};",
            f"unfunction {preexec} {precmd};",
            f"unset {seen};",
            "fi;",
            "};",
            f"add-zsh-hook preexec {preexec};",
            f"add-zsh-hook precmd {precmd};",
            "printf '%s\\n'",
            shlex.quote(armed_marker),
            ">",
            shlex.quote(str(armed_path)),
        ])
