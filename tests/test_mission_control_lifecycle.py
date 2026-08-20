import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mission_control.audit import MissionControlAuditLog
from mission_control.lifecycle import MissionControlLifecycleService
from mission_control.session import (
    GhosttySession,
    GhosttySessionDriver,
)
from mission_control.state import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_CLOSED,
    LIFECYCLE_DISCONNECTED,
    MissionControlStateStore,
)


class MissionControlLifecycleServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.driver = Mock(spec=GhosttySessionDriver)
        self.service = MissionControlLifecycleService(
            self.driver
        )
        self.store = MissionControlStateStore(self.repo)
        self.audit = MissionControlAuditLog(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_binds_durable_herd_to_exact_terminal(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session

        result = self.service.create(
            self.repo,
            "herd-1",
        )

        self.assertEqual(result, session)

        state = self.store.load()
        self.assertEqual(state.herd_id, "herd-1")
        self.assertEqual(state.lifecycle, LIFECYCLE_ACTIVE)
        self.assertEqual(
            state.terminal_id,
            "terminal-123",
        )

        self.assertEqual(
            [
                record.event_type
                for record in self.audit.read()
            ],
            [
                "herd.created",
                "session.created",
                "herd.activated",
            ],
        )

    def test_create_failure_marks_herd_disconnected(self):
        self.driver.create_session.side_effect = RuntimeError(
            "Ghostty unavailable"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Ghostty unavailable",
        ):
            self.service.create(
                self.repo,
                "herd-1",
            )

        state = self.store.load()
        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_DISCONNECTED,
        )
        self.assertIn(
            "Ghostty session creation failed",
            state.disconnected_reason,
        )

        self.assertEqual(
            self.audit.read()[-1].event_type,
            "session.create_failed",
        )

    def test_disconnect_is_durable_and_audited(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )

        state = self.service.disconnect(
            self.repo,
            "terminal disappeared",
        )

        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_DISCONNECTED,
        )
        self.assertEqual(
            state.disconnected_reason,
            "terminal disappeared",
        )
        self.assertEqual(
            self.audit.read()[-1].event_type,
            "session.disconnected",
        )

    def test_reconnect_uses_recorded_terminal_identity(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )
        self.service.disconnect(
            self.repo,
            "temporary disconnect",
        )

        self.driver.reconnect.return_value = session

        result = self.service.reconnect(
            self.repo
        )

        self.assertEqual(result, session)
        self.driver.reconnect.assert_called_once_with(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )

        state = self.store.load()
        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_ACTIVE,
        )
        self.assertEqual(
            self.audit.read()[-1].event_type,
            "session.reconnected",
        )

    def test_reconnect_failure_stays_disconnected(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )
        self.service.disconnect(
            self.repo,
            "temporary disconnect",
        )

        self.driver.reconnect.side_effect = RuntimeError(
            "terminal missing"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "terminal missing",
        ):
            self.service.reconnect(
                self.repo
            )

        self.assertEqual(
            self.store.load().lifecycle,
            LIFECYCLE_DISCONNECTED,
        )
        self.assertEqual(
            self.audit.read()[-1].event_type,
            "session.reconnect_failed",
        )

    def test_close_closes_exact_terminal_then_marks_herd_closed(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )

        state = self.service.close(
            self.repo
        )

        self.driver.close_session.assert_called_once_with(
            session
        )
        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_CLOSED,
        )
        self.assertIsNone(state.terminal_id)

        self.assertEqual(
            [
                record.event_type
                for record in self.audit.read()
            ][-2:],
            [
                "session.closed",
                "herd.closed",
            ],
        )

    def test_close_failure_marks_herd_disconnected(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )

        self.driver.close_session.side_effect = RuntimeError(
            "close failed"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "close failed",
        ):
            self.service.close(
                self.repo
            )

        state = self.store.load()
        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_DISCONNECTED,
        )
        self.assertEqual(
            self.audit.read()[-1].event_type,
            "session.close_failed",
        )

    def test_close_refuses_while_execution_is_active(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-123",
            repo_path=self.repo,
        )
        self.driver.create_session.return_value = session
        self.service.create(
            self.repo,
            "herd-1",
        )
        self.store.begin_execution(
            "exec-1"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "execution is active",
        ):
            self.service.close(
                self.repo
            )

        self.driver.close_session.assert_not_called()
        self.assertEqual(
            self.store.load().lifecycle,
            LIFECYCLE_ACTIVE,
        )


if __name__ == "__main__":
    unittest.main()
