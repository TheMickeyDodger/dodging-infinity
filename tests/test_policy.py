import unittest

from herdr.policy import HerdrPolicy


class HerdrPolicyTests(unittest.TestCase):
    def test_defaults(self):
        policy = HerdrPolicy.resolve()

        self.assertEqual(
            policy.get("git", "commit"),
            "require-human",
        )
        self.assertEqual(
            policy.get("git", "push"),
            "require-human",
        )
        self.assertTrue(policy.get("review", "required"))

    def test_layers_override_more_specific_values(self):
        policy = HerdrPolicy.resolve(
            {"review": {"max_rounds": 3}},
            {"review": {"max_rounds": 7}},
        )

        self.assertEqual(policy.get("review", "max_rounds"), 7)

    def test_rules_accumulate(self):
        policy = HerdrPolicy.resolve(
            {"rules": ["Do not refactor unrelated code"]},
            {"rules": ["Only touch tests/**"]},
        )

        self.assertEqual(
            policy.rules,
            [
                "Do not refactor unrelated code",
                "Only touch tests/**",
            ],
        )

    def test_duplicate_rules_are_removed(self):
        policy = HerdrPolicy.resolve(
            {"rules": ["Run tests"]},
            {"rules": ["Run tests"]},
        )

        self.assertEqual(policy.rules, ["Run tests"])

    def test_invalid_git_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            HerdrPolicy.resolve(
                {"git": {"push": "YOLO"}}
            )


if __name__ == "__main__":
    unittest.main()
