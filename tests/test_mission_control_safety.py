import unittest

from mission_control.safety import (
    CommandSafetyPolicy,
    SafetyViolation,
)


class CommandSafetyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = CommandSafetyPolicy()

    def test_allows_simple_non_destructive_commands(self):
        self.policy.validate(
            (
                "git status --short",
                "python3 -m unittest",
                "git diff --check",
            ),
            approval_type="NEXT_ACTION",
        )

    def test_rejects_unparseable_shell_command(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "safely parse",
        ):
            self.policy.validate(
                ('echo "unterminated',),
                approval_type="NEXT_ACTION",
            )

    def test_rejects_shell_control_operators(self):
        for command in (
            "echo ok && rm -rf /",
            "echo ok || true",
            "echo ok; true",
            "echo ok | cat",
            "sleep 1 &",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    SafetyViolation,
                    "shell control operator",
                ):
                    self.policy.validate(
                        (command,),
                        approval_type="NEXT_ACTION",
                    )

    def test_next_action_cannot_cross_commit_gate(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "separate commit approval",
        ):
            self.policy.validate(
                ("git commit -m test",),
                approval_type="NEXT_ACTION",
            )

    def test_next_action_cannot_cross_push_gate(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "separate push approval",
        ):
            self.policy.validate(
                ("git push origin main",),
                approval_type="NEXT_ACTION",
            )

    def test_rejects_commit_guard_bypass(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "no-verify",
        ):
            self.policy.validate(
                ("git commit -m test --no-verify",),
                approval_type="COMMIT",
            )

    def test_rejects_force_push_using_existing_herdr_semantics(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "force",
        ):
            self.policy.validate(
                ("git push --force",),
                approval_type="PUSH",
            )

    def test_rejects_destructive_git_reset(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "destructive git reset",
        ):
            self.policy.validate(
                ("git reset --hard HEAD~1",),
                approval_type="NEXT_ACTION",
            )

    def test_rejects_destructive_git_clean(self):
        with self.assertRaisesRegex(
            SafetyViolation,
            "destructive git clean",
        ):
            self.policy.validate(
                ("git clean -fd",),
                approval_type="NEXT_ACTION",
            )

    def test_rejects_filesystem_root_or_home_wipe(self):
        for command in (
            "rm -rf /",
            "rm -rf ~",
            "rm -rf $HOME",
            "rm -rf ${HOME}",
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    SafetyViolation,
                    "filesystem wipe",
                ):
                    self.policy.validate(
                        (command,),
                        approval_type="NEXT_ACTION",
                    )

    def test_rejects_sudo_wrapped_filesystem_wipe(self):
        with self.assertRaises(SafetyViolation):
            self.policy.validate(
                ("sudo rm -rf /",),
                approval_type="NEXT_ACTION",
            )

    def test_next_action_cannot_cross_push_gate_through_env(self):
        with self.assertRaises(SafetyViolation):
            self.policy.validate(
                ("env git push origin main",),
                approval_type="NEXT_ACTION",
            )

    def test_rejects_shell_command_wrappers(self):
        for command in (
            'sh -c "rm -rf /"',
            'bash -c "git push origin main"',
            'zsh -c "git reset --hard HEAD~1"',
        ):
            with self.subTest(command=command):
                with self.assertRaises(SafetyViolation):
                    self.policy.validate(
                        (command,),
                        approval_type="NEXT_ACTION",
                    )

    def test_relative_cleanup_is_not_treated_as_root_wipe(self):
        self.policy.validate(
            ("rm -rf .pytest_cache",),
            approval_type="NEXT_ACTION",
        )

    def test_user_rules_are_additive_only(self):
        policy = CommandSafetyPolicy(
            additional_blocked_executables=("curl",),
        )

        with self.assertRaisesRegex(
            SafetyViolation,
            "additional safety rule",
        ):
            policy.validate(
                ("curl https://example.com",),
                approval_type="NEXT_ACTION",
            )

        with self.assertRaisesRegex(
            SafetyViolation,
            "filesystem wipe",
        ):
            policy.validate(
                ("rm -rf /",),
                approval_type="NEXT_ACTION",
            )


if __name__ == "__main__":
    unittest.main()
