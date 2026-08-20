import json
import tempfile
import unittest
from pathlib import Path

from mission_control.state import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_CLOSED,
    LIFECYCLE_DISCONNECTED,
    LIFECYCLE_STARTING,
    MissionControlStateStore,
)


class MissionControlStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.store = MissionControlStateStore(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_persists_starting_state(self):
        state = self.store.create("herd-1")

        self.assertEqual(state.herd_id, "herd-1")
        self.assertEqual(state.repo_path, self.repo)
        self.assertEqual(state.lifecycle, LIFECYCLE_STARTING)
        self.assertIsNone(state.terminal_id)
        self.assertIsNone(state.active_execution_id)
        self.assertTrue(self.store.path.exists())

        loaded = self.store.load()
        self.assertEqual(loaded, state)

    def test_second_open_herd_for_same_repo_is_rejected(self):
        self.store.create("herd-1")

        with self.assertRaisesRegex(
            RuntimeError,
            "already has active Mission Control Herd",
        ):
            self.store.create("herd-2")

    def test_attach_session_activates_herd(self):
        self.store.create("herd-1")

        state = self.store.attach_session("terminal-123")

        self.assertEqual(state.lifecycle, LIFECYCLE_ACTIVE)
        self.assertEqual(state.terminal_id, "terminal-123")

    def test_execution_is_serialized_per_herd(self):
        self.store.create("herd-1")
        self.store.attach_session("terminal-123")

        state = self.store.begin_execution("exec-1")
        self.assertEqual(state.active_execution_id, "exec-1")

        with self.assertRaisesRegex(
            RuntimeError,
            "already has active execution",
        ):
            self.store.begin_execution("exec-2")

        state = self.store.finish_execution("exec-1")
        self.assertIsNone(state.active_execution_id)

    def test_finish_requires_matching_execution(self):
        self.store.create("herd-1")
        self.store.attach_session("terminal-123")
        self.store.begin_execution("exec-1")

        with self.assertRaisesRegex(
            RuntimeError,
            "is not the active Herd execution",
        ):
            self.store.finish_execution("exec-other")

    def test_disconnect_and_reconnect(self):
        self.store.create("herd-1")
        self.store.attach_session("terminal-123")

        state = self.store.disconnect("Ghostty session missing")

        self.assertEqual(
            state.lifecycle,
            LIFECYCLE_DISCONNECTED,
        )
        self.assertEqual(
            state.disconnected_reason,
            "Ghostty session missing",
        )
        self.assertEqual(
            state.terminal_id,
            "terminal-123",
        )

        state = self.store.reconnect("terminal-123")

        self.assertEqual(state.lifecycle, LIFECYCLE_ACTIVE)
        self.assertIsNone(state.disconnected_reason)
        self.assertEqual(state.terminal_id, "terminal-123")

    def test_close_preserves_durable_record_but_clears_live_identity(self):
        self.store.create("herd-1")
        self.store.attach_session("terminal-123")
        self.store.begin_execution("exec-1")

        state = self.store.close()

        self.assertEqual(state.lifecycle, LIFECYCLE_CLOSED)
        self.assertIsNone(state.terminal_id)
        self.assertIsNone(state.active_execution_id)
        self.assertIsNotNone(state.closed_at_ms)

        loaded = self.store.load()
        self.assertEqual(loaded, state)

    def test_closed_repo_can_create_new_herd_identity(self):
        self.store.create("herd-1")
        self.store.attach_session("terminal-123")
        self.store.close()

        state = self.store.create("herd-2")

        self.assertEqual(state.herd_id, "herd-2")
        self.assertEqual(state.lifecycle, LIFECYCLE_STARTING)

    def test_corrupt_state_fails_closed(self):
        self.store.root.mkdir(parents=True)
        self.store.path.write_text("{not-json")

        with self.assertRaisesRegex(
            RuntimeError,
            "state is unreadable",
        ):
            self.store.load()

    def test_wrong_repo_in_state_is_rejected(self):
        state = self.store.create("herd-1")
        data = state.to_dict()
        data["repo_path"] = "/tmp/different-repo"
        self.store.path.write_text(
            json.dumps(data)
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "different repository",
        ):
            self.store.load()

    def test_invalid_herd_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create("../escape")

    def test_begin_execution_requires_active_herd(self):
        self.store.create("herd-1")

        with self.assertRaisesRegex(
            RuntimeError,
            "not active",
        ):
            self.store.begin_execution("exec-1")


if __name__ == "__main__":
    unittest.main()
