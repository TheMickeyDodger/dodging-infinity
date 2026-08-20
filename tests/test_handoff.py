import tempfile
import unittest
from pathlib import Path

from mission_control.handoff import (
    COMMAND_PREFIX,
    HANDOFF_PREFIX,
    HandoffChannel,
    ZshHandoffProtocol,
)


class HandoffChannelTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.channel = HandoffChannel(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prepare_creates_execution_directory_and_clears_markers(self):
        execution_id = "exec-123"
        directory = self.channel.execution_dir(execution_id)
        directory.mkdir(parents=True)
        stale_marker = directory / "old.marker"
        stale_armed = directory / "old.armed"
        stale_marker.write_text("stale")
        stale_armed.write_text("stale")

        self.channel.prepare(execution_id)

        self.assertTrue(directory.is_dir())
        self.assertFalse(stale_marker.exists())
        self.assertFalse(stale_armed.exists())

    def test_read_command_marker(self):
        execution_id = "exec-123"
        self.channel.prepare(execution_id)
        path = self.channel.command_path(execution_id, 2)
        path.write_text(
            f"{COMMAND_PREFIX}:{execution_id}:2:17\n"
        )

        marker = self.channel.read_command(execution_id, 2)

        self.assertIsNotNone(marker)
        self.assertEqual(marker.execution_id, execution_id)
        self.assertEqual(marker.command_index, 2)
        self.assertEqual(marker.exit_code, 17)

    def test_read_handoff_marker(self):
        execution_id = "exec-123"
        self.channel.prepare(execution_id)
        path = self.channel.handoff_path(execution_id)
        path.write_text(
            f"{HANDOFF_PREFIX}:{execution_id}:0\n"
        )

        marker = self.channel.read_handoff(execution_id)

        self.assertIsNotNone(marker)
        self.assertEqual(marker.execution_id, execution_id)
        self.assertEqual(marker.exit_code, 0)

    def test_missing_marker_returns_none(self):
        self.assertIsNone(
            self.channel.read_command("exec-123", 0)
        )
        self.assertIsNone(
            self.channel.read_handoff("exec-123")
        )

    def test_invalid_execution_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.channel.execution_dir("../escape")

    def test_invalid_marker_is_rejected(self):
        execution_id = "exec-123"
        self.channel.prepare(execution_id)
        self.channel.handoff_path(execution_id).write_text(
            "not-a-valid-marker\n"
        )

        with self.assertRaises(RuntimeError):
            self.channel.read_handoff(execution_id)


class ZshHandoffProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.channel = HandoffChannel(self.repo)
        self.protocol = ZshHandoffProtocol(self.channel)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_arm_command_targets_exact_marker_paths(self):
        command = self.protocol.arm_command(
            "exec-123",
            0,
            final_command=True,
        )

        self.assertIn(
            str(self.channel.armed_path("exec-123", 0)),
            command,
        )
        self.assertIn(
            "HERDR_ARMED:exec-123:0",
            command,
        )
        self.assertIn(
            str(self.channel.command_path("exec-123", 0)),
            command,
        )
        self.assertIn(
            str(self.channel.handoff_path("exec-123")),
            command,
        )

    def test_arm_command_expands_exit_code_at_runtime(self):
        command = self.protocol.arm_command(
            "exec-123",
            0,
            final_command=True,
        )

        self.assertIn('"$__mc_rc"', command)
        self.assertNotIn("${__mc_rc}", command)
        self.assertNotIn("| sed", command)

    def test_nonfinal_command_only_handoffs_on_failure(self):
        command = self.protocol.arm_command(
            "exec-123",
            0,
            final_command=False,
        )

        self.assertIn("__mc_rc != 0 || 0", command)

    def test_final_command_always_handoffs(self):
        command = self.protocol.arm_command(
            "exec-123",
            0,
            final_command=True,
        )

        self.assertIn("__mc_rc != 0 || 1", command)


if __name__ == "__main__":
    unittest.main()
