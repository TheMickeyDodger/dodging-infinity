import subprocess
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
