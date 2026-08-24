import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import herdctl


class HerdrRulesCliTests(unittest.TestCase):
    @patch.object(
        herdctl,
        "repo_alias",
        return_value="demo",
    )
    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_rules_lists_effective_rules(
        self,
        mock_cp_type,
        mock_resolve,
        mock_alias,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        policy = Mock()
        policy.rules = [
            "Never modify migrations",
            "Do not add dependencies",
        ]

        cp.policy.return_value = policy

        output = io.StringIO()

        with patch(
            "sys.stdout",
            output,
        ):
            herdctl.rules_cmd(
                SimpleNamespace(
                    repo=None,
                    action=None,
                    rule=None,
                )
            )

        rendered = output.getvalue()

        self.assertIn(
            "1. Never modify migrations",
            rendered,
        )

        self.assertIn(
            "2. Do not add dependencies",
            rendered,
        )

    @patch.object(
        herdctl,
        "repo_alias",
        return_value="demo",
    )
    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_rules_add_delegates_to_control_plane(
        self,
        mock_cp_type,
        mock_resolve,
        mock_alias,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        policy = Mock()
        policy.rules = [
            "Never modify migrations",
        ]

        cp.add_rule.return_value = policy

        herdctl.rules_cmd(
            SimpleNamespace(
                repo=None,
                action="add",
                rule="Never modify migrations",
            )
        )

        cp.add_rule.assert_called_once_with(
            Path("/tmp/demo"),
            "Never modify migrations",
        )

    @patch.object(
        herdctl,
        "repo_alias",
        return_value="demo",
    )
    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_rules_remove_delegates_to_control_plane(
        self,
        mock_cp_type,
        mock_resolve,
        mock_alias,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        policy = Mock()
        policy.rules = []

        cp.remove_rule.return_value = policy

        herdctl.rules_cmd(
            SimpleNamespace(
                repo=None,
                action="remove",
                rule="Never modify migrations",
            )
        )

        cp.remove_rule.assert_called_once_with(
            Path("/tmp/demo"),
            "Never modify migrations",
        )

    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_task_rules_become_task_policy(
        self,
        mock_cp_type,
        mock_resolve,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        herdctl.task(
            SimpleNamespace(
                repo=None,
                text="Fix auth",
                rejection_drill=False,
                rule=[
                    "Do not modify schema",
                    "Do not add dependencies",
                ],
            )
        )

        cp.dispatch_task.assert_called_once_with(
            Path("/tmp/demo"),
            "Fix auth",
            rejection_drill=False,
            task_policy={
                "rules": [
                    "Do not modify schema",
                    "Do not add dependencies",
                ],
            },
        )

    @patch.object(
        herdctl,
        "load_mission",
        return_value={
            "objective": "Fix authentication bug",
            "constraints": [
                "Do not change database schema",
            ],
            "rules": [
                "Preserve backward compatibility",
            ],
            "acceptance_criteria": [
                "Authentication tests pass",
            ],
            "verification": [
                "python3 -m unittest discover -s tests",
            ],
        },
    )
    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_task_mission_dispatches_objective_and_rules(
        self,
        mock_cp_type,
        mock_resolve,
        mock_mission,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        herdctl.task(
            SimpleNamespace(
                repo=None,
                text=None,
                mission=True,
                rejection_drill=False,
                rule=[],
            )
        )

        cp.dispatch_task.assert_called_once_with(
            Path("/tmp/demo"),
            """OBJECTIVE
Fix authentication bug

CONSTRAINTS
- Do not change database schema

RULES
- Preserve backward compatibility

ACCEPTANCE CRITERIA
- Authentication tests pass

VERIFICATION
- python3 -m unittest discover -s tests""",
            rejection_drill=False,
            task_policy={
                "rules": [
                    "Preserve backward compatibility",
                ],
            },
        )


    @patch.object(
        herdctl,
        "resolve_repo_ref",
        return_value=Path("/tmp/demo"),
    )
    @patch.object(
        herdctl,
        "HerdrControlPlane",
    )
    def test_task_without_rules_preserves_none_policy(
        self,
        mock_cp_type,
        mock_resolve,
    ):
        cp = Mock()
        mock_cp_type.return_value = cp

        herdctl.task(
            SimpleNamespace(
                repo=None,
                text="Fix auth",
                rejection_drill=False,
                rule=[],
            )
        )

        cp.dispatch_task.assert_called_once_with(
            Path("/tmp/demo"),
            "Fix auth",
            rejection_drill=False,
            task_policy=None,
        )


if __name__ == "__main__":
    unittest.main()
