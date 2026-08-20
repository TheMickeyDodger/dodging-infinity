import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mission_control.execution import (
    CommandExecutionEngine,
    ExecutionTimeout,
)
from mission_control.handoff import (
    COMMAND_PREFIX,
    HANDOFF_PREFIX,
    HandoffChannel,
)
from mission_control.session import (
    GhosttySession,
    GhosttySessionDriver,
)


class CommandExecutionEngineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.session = GhosttySession(
            herd_id="herd-1",
            terminal_id="terminal-1",
            repo_path=self.repo,
        )
        self.driver = Mock(spec=GhosttySessionDriver)
        self.channel = HandoffChannel(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def engine(self, *, timeout=0.2):
        return CommandExecutionEngine(
            self.driver,
            poll_interval=0.001,
            timeout=timeout,
        )

    def test_successful_sequence_is_serial_and_verbatim(self):
        execution_id = "exec-success"
        commands = (
            'printf "first command\\n"',
            "echo second && echo untouched",
        )

        def send_text(_session, text):
            if "HERDR_ARMED:exec-success:0" in text:
                self.channel.armed_path(execution_id, 0).touch()
            elif text == commands[0]:
                self.channel.command_path(
                    execution_id,
                    0,
                ).write_text(
                    f"{COMMAND_PREFIX}:{execution_id}:0:0\n"
                )
            elif "HERDR_ARMED:exec-success:1" in text:
                self.channel.armed_path(execution_id, 1).touch()
            elif text == commands[1]:
                self.channel.command_path(
                    execution_id,
                    1,
                ).write_text(
                    f"{COMMAND_PREFIX}:{execution_id}:1:0\n"
                )
                self.channel.handoff_path(
                    execution_id
                ).write_text(
                    f"{HANDOFF_PREFIX}:{execution_id}:0\n"
                )

        self.driver.send_text.side_effect = send_text

        result = self.engine().execute(
            self.session,
            execution_id,
            commands,
        )

        sent = [
            call.args[1]
            for call in self.driver.send_text.call_args_list
        ]

        self.assertEqual(sent[1], commands[0])
        self.assertEqual(sent[3], commands[1])
        self.assertEqual(
            [outcome.command for outcome in result.outcomes],
            list(commands),
        )
        self.assertEqual(
            [outcome.exit_code for outcome in result.outcomes],
            [0, 0],
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.exit_code, 0)

    def test_failure_stops_before_next_command(self):
        execution_id = "exec-failure"
        commands = ("false", "echo must-not-run")

        def send_text(_session, text):
            if "HERDR_ARMED:exec-failure:0" in text:
                self.channel.armed_path(execution_id, 0).touch()
            elif text == commands[0]:
                self.channel.command_path(
                    execution_id,
                    0,
                ).write_text(
                    f"{COMMAND_PREFIX}:{execution_id}:0:7\n"
                )
                self.channel.handoff_path(
                    execution_id
                ).write_text(
                    f"{HANDOFF_PREFIX}:{execution_id}:7\n"
                )

        self.driver.send_text.side_effect = send_text

        result = self.engine().execute(
            self.session,
            execution_id,
            commands,
        )

        sent = [
            call.args[1]
            for call in self.driver.send_text.call_args_list
        ]

        self.assertIn(commands[0], sent)
        self.assertNotIn(commands[1], sent)
        self.assertEqual(len(result.outcomes), 1)
        self.assertEqual(result.exit_code, 7)
        self.assertFalse(result.succeeded)

    def test_command_is_not_wrapped_or_rewritten(self):
        execution_id = "exec-verbatim"
        command = (
            "git status --short && "
            "printf '%s\\n' 'literal $HOME ; && text'"
        )

        def send_text(_session, text):
            if "HERDR_ARMED:exec-verbatim:0" in text:
                self.channel.armed_path(execution_id, 0).touch()
            elif text == command:
                self.channel.command_path(
                    execution_id,
                    0,
                ).write_text(
                    f"{COMMAND_PREFIX}:{execution_id}:0:0\n"
                )
                self.channel.handoff_path(
                    execution_id
                ).write_text(
                    f"{HANDOFF_PREFIX}:{execution_id}:0\n"
                )

        self.driver.send_text.side_effect = send_text

        self.engine().execute(
            self.session,
            execution_id,
            (command,),
        )

        self.assertEqual(
            self.driver.send_text.call_args_list[1].args[1],
            command,
        )

    def test_waits_for_armed_marker_before_sending_command(self):
        execution_id = "exec-unarmed"

        self.driver.send_text.side_effect = lambda *_args: None

        with self.assertRaises(ExecutionTimeout) as raised:
            self.engine(timeout=0.01).execute(
                self.session,
                execution_id,
                ("echo never-sent",),
            )

        self.assertEqual(raised.exception.stage, "shell arm marker")
        self.assertEqual(self.driver.send_text.call_count, 1)

    def test_command_timeout_never_sends_next_command(self):
        execution_id = "exec-prompt-timeout"
        commands = (
            "read -r 'reply?prompt> '",
            "echo must-not-run",
        )

        def send_text(_session, text):
            if "HERDR_ARMED:exec-prompt-timeout:0" in text:
                self.channel.armed_path(execution_id, 0).touch()

        self.driver.send_text.side_effect = send_text

        with self.assertRaises(ExecutionTimeout) as raised:
            self.engine(timeout=0.01).execute(
                self.session,
                execution_id,
                commands,
            )

        sent = [
            call.args[1]
            for call in self.driver.send_text.call_args_list
        ]

        self.assertEqual(
            raised.exception.stage,
            "command completion marker",
        )
        self.assertEqual(
            raised.exception.command_index,
            0,
        )
        self.assertIn(commands[0], sent)
        self.assertNotIn(commands[1], sent)

    def test_handoff_exit_code_must_match_command_marker(self):
        execution_id = "exec-mismatch"
        command = "false"

        def send_text(_session, text):
            if "HERDR_ARMED:exec-mismatch:0" in text:
                self.channel.armed_path(execution_id, 0).touch()
            elif text == command:
                self.channel.command_path(
                    execution_id,
                    0,
                ).write_text(
                    f"{COMMAND_PREFIX}:{execution_id}:0:9\n"
                )
                self.channel.handoff_path(
                    execution_id
                ).write_text(
                    f"{HANDOFF_PREFIX}:{execution_id}:1\n"
                )

        self.driver.send_text.side_effect = send_text

        with self.assertRaisesRegex(
            RuntimeError,
            "Handoff exit code does not match",
        ):
            self.engine().execute(
                self.session,
                execution_id,
                (command,),
            )

    def test_empty_sequence_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "at least one command",
        ):
            self.engine().execute(
                self.session,
                "exec-empty",
                (),
            )

    def test_empty_command_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "non-empty string",
        ):
            self.engine().execute(
                self.session,
                "exec-empty-command",
                ("",),
            )


if __name__ == "__main__":
    unittest.main()
