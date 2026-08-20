"""Deterministic command execution over a Herd-owned Ghostty session."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .handoff import (
    HandoffChannel,
    HandoffMarker,
    ZshHandoffProtocol,
)
from .session import GhosttySession, GhosttySessionDriver


class ExecutionTimeout(RuntimeError):
    """Raised when Ghostty does not reach an expected deterministic marker."""

    def __init__(
        self,
        execution_id: str,
        stage: str,
        command_index: int,
    ):
        self.execution_id = execution_id
        self.stage = stage
        self.command_index = command_index
        super().__init__(
            f"Execution {execution_id} timed out waiting for "
            f"{stage} at command {command_index}"
        )


@dataclass(frozen=True)
class CommandOutcome:
    """Observed result for one exact approved command."""

    command_index: int
    command: str
    exit_code: int


@dataclass(frozen=True)
class ExecutionResult:
    """Deterministic result for one serialized command sequence."""

    execution_id: str
    outcomes: tuple[CommandOutcome, ...]
    handoff: HandoffMarker

    @property
    def exit_code(self) -> int:
        return self.handoff.exit_code

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class CommandExecutionEngine:
    """Execute exact commands serially through one Ghostty session."""

    def __init__(
        self,
        session_driver: GhosttySessionDriver,
        *,
        poll_interval: float = 0.05,
        timeout: float = 30.0,
    ):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.session_driver = session_driver
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _wait_for_path(
        self,
        path: Path,
        *,
        execution_id: str,
        stage: str,
        command_index: int,
    ) -> None:
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(self.poll_interval)

        raise ExecutionTimeout(
            execution_id,
            stage,
            command_index,
        )

    def execute(
        self,
        session: GhosttySession,
        execution_id: str,
        commands: Sequence[str],
    ) -> ExecutionResult:
        exact_commands = tuple(commands)

        if not exact_commands:
            raise ValueError("commands must contain at least one command")

        if any(
            not isinstance(command, str) or not command
            for command in exact_commands
        ):
            raise ValueError("every command must be a non-empty string")

        self.session_driver.wait_until_ready(
            session,
            timeout=self.timeout,
            retry_interval=min(0.25, self.timeout),
        )

        channel = HandoffChannel(session.repo_path)
        protocol = ZshHandoffProtocol(channel)
        channel.prepare(execution_id)

        outcomes: list[CommandOutcome] = []

        for index, command in enumerate(exact_commands):
            final_command = index == len(exact_commands) - 1

            arm = protocol.arm_command(
                execution_id,
                index,
                final_command=final_command,
            )
            self.session_driver.send_text(session, arm)

            self._wait_for_path(
                channel.armed_path(execution_id, index),
                execution_id=execution_id,
                stage="shell arm marker",
                command_index=index,
            )

            # This is the approved command exactly as supplied. Mission Control
            # must not wrap, rewrite, chain, or otherwise mutate it.
            self.session_driver.send_text(session, command)

            command_path = channel.command_path(
                execution_id,
                index,
            )
            self._wait_for_path(
                command_path,
                execution_id=execution_id,
                stage="command completion marker",
                command_index=index,
            )

            marker = channel.read_command(
                execution_id,
                index,
            )
            if marker is None:
                raise RuntimeError(
                    "Command marker disappeared after observation"
                )

            outcomes.append(
                CommandOutcome(
                    command_index=index,
                    command=command,
                    exit_code=marker.exit_code,
                )
            )

            if marker.exit_code != 0 or final_command:
                handoff_path = channel.handoff_path(execution_id)
                self._wait_for_path(
                    handoff_path,
                    execution_id=execution_id,
                    stage="handoff marker",
                    command_index=index,
                )

                handoff = channel.read_handoff(execution_id)
                if handoff is None:
                    raise RuntimeError(
                        "Handoff marker disappeared after observation"
                    )

                if handoff.exit_code != marker.exit_code:
                    raise RuntimeError(
                        "Handoff exit code does not match "
                        "the final command result"
                    )

                return ExecutionResult(
                    execution_id=execution_id,
                    outcomes=tuple(outcomes),
                    handoff=handoff,
                )

        raise RuntimeError(
            "Execution ended without a deterministic handoff"
        )
