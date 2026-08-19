import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import herdctl


class HerdrCompletionGateTests(unittest.TestCase):
    def test_task_complete_blocks_unresolved_child(self):
        repo = Path(
            "/tmp/herdr-completion-test"
        )

        task = {
            "id": "parent-task",
            "status": "ACTIVE",
        }

        cp = Mock()

        cp.require_child_dependencies_complete.side_effect = (
            RuntimeError(
                "Parent task cannot complete while child "
                "Herdr dependencies are unresolved:\n"
                "- /tmp/child | task=child-task | status=ACTIVE"
            )
        )

        with (
            patch.object(
                herdctl,
                "resolve_repo_ref",
                return_value=repo,
            ),
            patch.object(
                herdctl,
                "load_task",
                return_value=task,
            ),
            patch.object(
                herdctl,
                "HerdrControlPlane",
                return_value=cp,
            ),
        ):
            with self.assertRaises(
                SystemExit
            ) as caught:
                herdctl.task_complete_cmd(
                    SimpleNamespace(
                        repo=str(repo),
                        checkpoint_file=None,
                        note=None,
                    )
                )

        self.assertIn(
            "status=ACTIVE",
            str(caught.exception),
        )

        cp.require_child_dependencies_complete.assert_called_once_with(
            repo,
            "parent-task",
        )


if __name__ == "__main__":
    unittest.main()
