import json
import subprocess
import unittest
from unittest.mock import patch

from herdr.runtime import prompt


def result(
    *,
    returncode=0,
    stdout="",
    stderr="",
):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def agent_state(
    status,
    seq,
    revision,
):
    return result(
        stdout=json.dumps(
            {
                "result": {
                    "agent": {
                        "agent_status": status,
                        "state_change_seq": seq,
                        "revision": revision,
                    }
                }
            }
        )
    )


class HerdrRuntimePromptTests(unittest.TestCase):
    @patch("herdr.runtime.run")
    def test_prompt_without_wait_is_single_submission(
        self,
        mock_run,
    ):
        mock_run.return_value = result()

        response = prompt(
            "agent1",
            "hello",
            60000,
            wait=False,
        )

        self.assertEqual(
            response.returncode,
            0,
        )

        mock_run.assert_called_once_with(
            [
                "herdr",
                "agent",
                "prompt",
                "agent1",
                "hello",
            ]
        )

    @patch(
        "herdr.runtime.time.sleep",
        return_value=None,
    )
    @patch("herdr.runtime.run")
    def test_wait_submits_without_herdr_wait_and_observes_settlement(
        self,
        mock_run,
        mock_sleep,
    ):
        mock_run.side_effect = [
            agent_state(
                "idle",
                100,
                1,
            ),
            result(
                stdout='{"result":{"type":"agent_prompted"}}'
            ),
            agent_state(
                "idle",
                100,
                1,
            ),
            agent_state(
                "working",
                101,
                2,
            ),
            agent_state(
                "done",
                102,
                3,
            ),
        ]

        response = prompt(
            "agent1",
            "bootstrap",
            60000,
            wait=True,
        )

        self.assertEqual(
            response.returncode,
            0,
        )

        submission = (
            mock_run.call_args_list[1]
            .args[0]
        )

        self.assertEqual(
            submission,
            [
                "herdr",
                "agent",
                "prompt",
                "agent1",
                "bootstrap",
            ],
        )

        self.assertNotIn(
            "--wait",
            submission,
        )

        self.assertGreaterEqual(
            mock_sleep.call_count,
            1,
        )

    @patch(
        "herdr.runtime.time.sleep",
        return_value=None,
    )
    @patch(
        "herdr.runtime.time.monotonic",
        side_effect=[
            0.0,
            0.1,
            30.1,
        ],
    )
    @patch("herdr.runtime.run")
    def test_wait_reports_unobserved_prompt_separately(
        self,
        mock_run,
        mock_monotonic,
        mock_sleep,
    ):
        mock_run.side_effect = [
            agent_state(
                "idle",
                100,
                1,
            ),
            result(),
            agent_state(
                "idle",
                100,
                1,
            ),
        ]

        response = prompt(
            "agent1",
            "bootstrap",
            600000,
            wait=True,
        )

        self.assertNotEqual(
            response.returncode,
            0,
        )

        self.assertIn(
            "agent_prompt_unobserved",
            response.stderr,
        )

        self.assertNotIn(
            "agent_prompt_stalled",
            response.stderr,
        )


if __name__ == "__main__":
    unittest.main()
