"""Fail-closed terminal safety policy for Mission Control approvals."""

from __future__ import annotations

import shlex

from pathlib import Path
from typing import Sequence

from herdr.guards import (
    simple_git_commit,
    simple_git_push,
)


class SafetyViolation(RuntimeError):
    """Raised when an approved command violates the safety floor."""


class CommandSafetyPolicy:
    """Immutable core safety rules plus additive user restrictions."""

    def __init__(
        self,
        *,
        additional_blocked_executables: Sequence[str] = (),
    ):
        blocked = []

        for executable in additional_blocked_executables:
            if not isinstance(executable, str):
                raise ValueError(
                    "additional blocked executables must be strings"
                )

            executable = executable.strip()

            if not executable:
                raise ValueError(
                    "additional blocked executables cannot be empty"
                )

            blocked.append(executable)

        self.additional_blocked_executables = tuple(blocked)

    def validate(
        self,
        commands: Sequence[str],
        *,
        approval_type: str,
    ) -> None:
        exact_commands = tuple(commands)

        if not exact_commands:
            raise SafetyViolation(
                "Approved command sequence cannot be empty"
            )

        for command in exact_commands:
            self._validate_command(
                command,
                approval_type=approval_type,
            )

    def _validate_command(
        self,
        command: str,
        *,
        approval_type: str,
    ) -> None:
        if not isinstance(command, str) or not command:
            raise SafetyViolation(
                "Approved commands must be non-empty strings"
            )

        try:
            lexer = shlex.shlex(
                command,
                posix=True,
                punctuation_chars=";&|",
            )
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except Exception as exc:
            raise SafetyViolation(
                "Could not safely parse approved command"
            ) from exc

        if not tokens:
            raise SafetyViolation(
                "Approved command cannot be empty"
            )

        if any(
            token in {
                "&&",
                "||",
                ";",
                "|",
                "&",
            }
            for token in tokens
        ):
            raise SafetyViolation(
                "Approved command contains a shell control operator"
            )

        executable = Path(tokens[0]).name

        if executable in self.additional_blocked_executables:
            raise SafetyViolation(
                f"Command blocked by additional safety rule: "
                f"{executable}"
            )

        if executable == "sudo":
            raise SafetyViolation(
                "Mission Control blocks privilege escalation wrappers"
            )

        if (
            executable in {"sh", "bash", "zsh", "dash", "ksh"}
            and "-c" in tokens[1:]
        ):
            raise SafetyViolation(
                "Mission Control blocks shell command wrappers"
            )

        if executable == "env":
            nested = self._unwrap_env(tokens)

            if nested is not None:
                self._validate_command(
                    shlex.join(nested),
                    approval_type=approval_type,
                )

            return

        self._validate_filesystem_wipe(
            executable,
            tokens,
        )

        if executable != "git":
            return

        self._validate_git(
            command,
            tokens,
            approval_type=approval_type,
        )

    def _unwrap_env(
        self,
        tokens: list[str],
    ) -> list[str] | None:
        index = 1

        if index < len(tokens) and tokens[index] == "--":
            index += 1

        while index < len(tokens):
            token = tokens[index]

            if token.startswith("-"):
                raise SafetyViolation(
                    "Mission Control blocks complex env wrappers"
                )

            name, separator, _value = token.partition("=")

            if (
                separator
                and name
                and (
                    name[0].isalpha()
                    or name[0] == "_"
                )
                and all(
                    character.isalnum()
                    or character == "_"
                    for character in name
                )
            ):
                index += 1
                continue

            break

        if index >= len(tokens):
            return None

        return tokens[index:]

    def _validate_git(
        self,
        command: str,
        tokens: list[str],
        *,
        approval_type: str,
    ) -> None:
        if len(tokens) < 2:
            return

        subcommand = tokens[1]

        if subcommand == "commit":
            ok, reason = simple_git_commit(command)

            if not ok:
                raise SafetyViolation(
                    reason
                    or "Git commit violates the Herdr commit guard"
                )

            if approval_type != "COMMIT":
                raise SafetyViolation(
                    "git commit requires the separate commit approval gate"
                )

            return

        if subcommand == "push":
            ok, reason = simple_git_push(command)

            if not ok:
                raise SafetyViolation(
                    reason
                    or "Git push violates the Herdr push guard"
                )

            if approval_type != "PUSH":
                raise SafetyViolation(
                    "git push requires the separate push approval gate"
                )

            return

        if subcommand == "reset":
            if any(
                token == "--hard"
                or token.startswith("--hard=")
                for token in tokens[2:]
            ):
                raise SafetyViolation(
                    "Mission Control blocks destructive git reset"
                )

        if subcommand == "clean":
            flags = tokens[2:]

            has_force = any(
                token == "--force"
                or (
                    token.startswith("-")
                    and not token.startswith("--")
                    and "f" in token[1:]
                )
                for token in flags
            )
            has_delete = any(
                token == "--directories"
                or token == "-d"
                or (
                    token.startswith("-")
                    and not token.startswith("--")
                    and "d" in token[1:]
                )
                for token in flags
            )

            if has_force and has_delete:
                raise SafetyViolation(
                    "Mission Control blocks destructive git clean"
                )

    def _validate_filesystem_wipe(
        self,
        executable: str,
        tokens: list[str],
    ) -> None:
        if executable != "rm":
            return

        recursive = False
        force = False
        operands = []

        for token in tokens[1:]:
            if token == "--recursive":
                recursive = True
                continue

            if token == "--force":
                force = True
                continue

            if token.startswith("-") and token != "-":
                if not token.startswith("--"):
                    flags = token[1:]
                    recursive = recursive or "r" in flags or "R" in flags
                    force = force or "f" in flags
                    continue

            operands.append(token)

        if not (recursive and force):
            return

        protected = {
            "/",
            "~",
            "$HOME",
            "${HOME}",
        }

        if any(operand in protected for operand in operands):
            raise SafetyViolation(
                "Mission Control blocks destructive filesystem wipe"
            )
