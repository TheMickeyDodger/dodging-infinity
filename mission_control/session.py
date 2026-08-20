"""Ghostty session transport for Mission Control."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GhosttySession:
    """Stable identity for one Herd-owned Ghostty terminal."""

    herd_id: str
    terminal_id: str
    repo_path: Path


class GhosttySessionDriver:
    """Create and control dedicated Ghostty terminals on macOS."""

    def _run(self, script: str, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["osascript", "-e", script, *args],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or "Ghostty AppleScript command failed"
            )
        return result

    def create_session(self, repo_path: Path, herd_id: str) -> GhosttySession:
        repo = Path(repo_path).resolve()
        script = r"""
on run argv
    set repoPath to item 1 of argv
    tell application "Ghostty"
        set cfg to new surface configuration
        set initial working directory of cfg to repoPath
        set w to new window with configuration cfg
        set t to terminal 1 of selected tab of w
        return id of t
    end tell
end run
"""
        result = self._run(script, str(repo))
        terminal_id = result.stdout.strip()
        if not terminal_id:
            raise RuntimeError("Ghostty returned no terminal ID")
        return GhosttySession(
            herd_id=herd_id,
            terminal_id=terminal_id,
            repo_path=repo,
        )

    def send_text(self, session: GhosttySession, text: str) -> None:
        script = r"""
on run argv
    set terminalId to item 1 of argv
    set commandText to item 2 of argv
    tell application "Ghostty"
        set t to first terminal whose id is terminalId
        input text commandText to t
        send key "enter" to t
    end tell
end run
"""
        self._run(script, session.terminal_id, text)

    def reconnect(
        self,
        herd_id: str,
        terminal_id: str,
        repo_path: Path,
    ) -> GhosttySession:
        repo = Path(repo_path).resolve()
        script = r"""
on run argv
    set terminalId to item 1 of argv
    tell application "Ghostty"
        return working directory of first terminal whose id is terminalId
    end tell
end run
"""
        result = self._run(script, terminal_id)
        observed = Path(result.stdout.strip()).resolve()
        if observed != repo:
            raise RuntimeError(
                f"Ghostty terminal {terminal_id} is attached to "
                f"{observed}, expected {repo}"
            )
        return GhosttySession(
            herd_id=herd_id,
            terminal_id=terminal_id,
            repo_path=repo,
        )

    def close_session(self, session: GhosttySession) -> None:
        script = r"""
on run argv
    set terminalId to item 1 of argv
    tell application "Ghostty"
        close (first terminal whose id is terminalId)
    end tell
end run
"""
        self._run(script, session.terminal_id)
