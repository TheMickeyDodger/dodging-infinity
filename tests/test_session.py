import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mission_control.session import GhosttySession, GhosttySessionDriver


class GhosttySessionDriverTests(unittest.TestCase):
    def setUp(self):
        self.driver = GhosttySessionDriver()
        self.repo = Path("/tmp/example-repo").resolve()
        self.terminal_id = "6F874102-537C-4DD7-97E3-4C9CA89BCA1E"

    @patch("mission_control.session.subprocess.run")
    def test_create_session_returns_terminal_identity(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self.terminal_id + "\n",
            stderr="",
        )

        session = self.driver.create_session(self.repo, "herd-1")

        self.assertEqual(
            session,
            GhosttySession(
                herd_id="herd-1",
                terminal_id=self.terminal_id,
                repo_path=self.repo,
            ),
        )
        self.assertIn("osascript", run.call_args.args[0][0])
        self.assertIn(str(self.repo), run.call_args.args[0])

    @patch("mission_control.session.subprocess.run")
    def test_send_text_targets_exact_terminal(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id=self.terminal_id,
            repo_path=self.repo,
        )

        self.driver.send_text(session, "echo hello")

        argv = run.call_args.args[0]
        self.assertIn(self.terminal_id, argv)
        self.assertIn("echo hello", argv)


    @patch("mission_control.session.uuid.uuid4")
    def test_wait_until_ready_requires_observed_shell_marker(self, uuid4):
        uuid4.return_value.hex = "ready123"

        with tempfile.TemporaryDirectory() as tempdir:
            repo = Path(tempdir).resolve()
            session = GhosttySession(
                herd_id="herd-1",
                terminal_id=self.terminal_id,
                repo_path=repo,
            )
            marker = (
                repo
                / ".herd"
                / "state"
                / "mission-control"
                / "sessions"
                / "ready123.ready"
            )
            calls = []

            def send_text(_session, text):
                calls.append(text)
                marker.write_text(
                    "HERDR_SESSION_READY:ready123\n"
                )

            with patch.object(
                self.driver,
                "send_text",
                side_effect=send_text,
            ):
                self.driver.wait_until_ready(
                    session,
                    timeout=0.5,
                    retry_interval=0.05,
                )

            self.assertEqual(len(calls), 1)
            self.assertIn(
                "HERDR_SESSION_READY:ready123",
                calls[0],
            )
            self.assertIn(str(marker), calls[0])
            self.assertFalse(marker.exists())

    def test_wait_until_ready_rejects_invalid_timing(self):
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id=self.terminal_id,
            repo_path=self.repo,
        )

        with self.assertRaisesRegex(
            ValueError,
            "timeout must be positive",
        ):
            self.driver.wait_until_ready(
                session,
                timeout=0,
            )

        with self.assertRaisesRegex(
            ValueError,
            "retry_interval must be positive",
        ):
            self.driver.wait_until_ready(
                session,
                retry_interval=0,
            )

    @patch("mission_control.session.subprocess.run")
    def test_reconnect_verifies_terminal_and_working_directory(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=str(self.repo) + "\n",
            stderr="",
        )

        session = self.driver.reconnect(
            herd_id="herd-1",
            terminal_id=self.terminal_id,
            repo_path=self.repo,
        )

        self.assertEqual(session.terminal_id, self.terminal_id)
        self.assertEqual(session.repo_path, self.repo)

    @patch("mission_control.session.subprocess.run")
    def test_close_targets_exact_terminal(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        session = GhosttySession(
            herd_id="herd-1",
            terminal_id=self.terminal_id,
            repo_path=self.repo,
        )

        self.driver.close_session(session)

        self.assertIn(self.terminal_id, run.call_args.args[0])

    @patch("mission_control.session.subprocess.run")
    def test_osascript_failure_raises_runtime_error(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Ghostty unavailable",
        )

        with self.assertRaisesRegex(RuntimeError, "Ghostty unavailable"):
            self.driver.create_session(self.repo, "herd-1")


if __name__ == "__main__":
    unittest.main()
