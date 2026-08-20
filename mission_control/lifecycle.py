"""Mission Control Herd and Ghostty session lifecycle."""

from __future__ import annotations

from pathlib import Path

from .audit import MissionControlAuditLog
from .session import GhosttySession, GhosttySessionDriver
from .state import (
    LIFECYCLE_CLOSED,
    LIFECYCLE_DISCONNECTED,
    MissionControlHerdState,
    MissionControlStateStore,
)


class MissionControlLifecycleService:
    """Coordinate durable Herd state with its dedicated Ghostty session."""

    def __init__(
        self,
        session_driver: GhosttySessionDriver,
    ):
        self.session_driver = session_driver

    def create(
        self,
        repo_path: Path,
        herd_id: str,
    ) -> GhosttySession:
        repo = Path(repo_path).resolve()
        store = MissionControlStateStore(repo)
        audit = MissionControlAuditLog(repo)

        state = store.create(herd_id)

        audit.append(
            herd_id,
            "herd.created",
            data={
                "lifecycle": state.lifecycle,
            },
        )

        try:
            session = self.session_driver.create_session(
                repo,
                herd_id,
            )
        except Exception as exc:
            store.disconnect(
                f"Ghostty session creation failed: {exc}"
            )

            audit.append(
                herd_id,
                "session.create_failed",
                data={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )

            raise

        store.attach_session(
            session.terminal_id
        )

        audit.append(
            herd_id,
            "session.created",
            data={
                "terminal_id": session.terminal_id,
            },
        )

        audit.append(
            herd_id,
            "herd.activated",
            data={
                "terminal_id": session.terminal_id,
            },
        )

        return session

    def reconnect(
        self,
        repo_path: Path,
    ) -> GhosttySession:
        repo = Path(repo_path).resolve()
        store = MissionControlStateStore(repo)
        audit = MissionControlAuditLog(repo)

        state = store.load()

        if state is None:
            raise RuntimeError(
                "No durable Mission Control Herd state exists"
            )

        if state.lifecycle != LIFECYCLE_DISCONNECTED:
            raise RuntimeError(
                "Only a disconnected Herd can be reconnected"
            )

        if not state.terminal_id:
            raise RuntimeError(
                "Disconnected Herd has no recorded Ghostty terminal"
            )

        try:
            session = self.session_driver.reconnect(
                herd_id=state.herd_id,
                terminal_id=state.terminal_id,
                repo_path=repo,
            )
        except Exception as exc:
            audit.append(
                state.herd_id,
                "session.reconnect_failed",
                data={
                    "terminal_id": state.terminal_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        store.reconnect(
            session.terminal_id
        )

        audit.append(
            state.herd_id,
            "session.reconnected",
            data={
                "terminal_id": session.terminal_id,
            },
        )

        return session

    def disconnect(
        self,
        repo_path: Path,
        reason: str,
    ) -> MissionControlHerdState:
        repo = Path(repo_path).resolve()
        store = MissionControlStateStore(repo)
        audit = MissionControlAuditLog(repo)

        state = store.disconnect(reason)

        audit.append(
            state.herd_id,
            "session.disconnected",
            data={
                "terminal_id": state.terminal_id,
                "reason": reason.strip(),
            },
        )

        return state

    def close(
        self,
        repo_path: Path,
    ) -> MissionControlHerdState:
        repo = Path(repo_path).resolve()
        store = MissionControlStateStore(repo)
        audit = MissionControlAuditLog(repo)

        state = store.load()

        if state is None:
            raise RuntimeError(
                "No durable Mission Control Herd state exists"
            )

        if state.lifecycle == LIFECYCLE_CLOSED:
            raise RuntimeError(
                "Mission Control Herd is already closed"
            )

        if state.active_execution_id is not None:
            raise RuntimeError(
                "Cannot close Herd while an execution is active"
            )

        if state.terminal_id:
            session = GhosttySession(
                herd_id=state.herd_id,
                terminal_id=state.terminal_id,
                repo_path=repo,
            )

            try:
                self.session_driver.close_session(
                    session
                )
            except Exception as exc:
                store.disconnect(
                    f"Ghostty session close failed: {exc}"
                )

                audit.append(
                    state.herd_id,
                    "session.close_failed",
                    data={
                        "terminal_id": state.terminal_id,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )

                raise

            audit.append(
                state.herd_id,
                "session.closed",
                data={
                    "terminal_id": state.terminal_id,
                },
            )

        closed = store.close()

        audit.append(
            state.herd_id,
            "herd.closed",
            data={
                "closed_at_ms": closed.closed_at_ms,
            },
        )

        return closed
