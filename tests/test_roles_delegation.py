"""The config-to-roles derivation exists ONCE (herdr/lifecycle.py: `role_counts`, `logical_roles`), and nothing a caller can see changed.

WHY: tests/test_parity.py is the repository's duplication guard (its own message: that duplication is what produced the `bootstrap_text` NameError). The
follow-on's `_required_roles` was a third hand copy of logic that `lifecycle.start_herd` has inline and `herdctl._health_expected_roles` mirrors, which made
the census flag the herdctl function. The fix is to extract the shared derivation once and have every caller use it, NOT to widen the guard's list of
known duplicates and NOT to hide the copy from the detector.

This file pins two things:
  1. CHARACTERIZATION: over a broad matrix of configurations, `herdctl._health_expected_roles` and `follow_on._required_roles` return exactly what they
     returned BEFORE the extraction, error type and message included. The expected outcomes in tests/fixtures/roles_delegation.golden.json were produced
     by the UNCHANGED code (tools/executor1/gen_roles_golden.py), not by the code under test.
  2. DELEGATION: the shared functions exist and behave as `start_herd`'s own inline expression did; `start_herd`, `_required_roles` and
     `_health_expected_roles` call them (a static pin, labelled as such); none of the three re-derives the names itself.

Containment: nothing here runs a herd or touches the filesystem outside TMPDIR.
"""

import ast
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import herdctl  # noqa: E402
from herdr import follow_on, lifecycle  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "roles_delegation.golden.json"

# (label, the `orchestration` value placed in the config); `ABSENT` means the config has no `orchestration` key at all
ABSENT = object()
CASES = [
    ("absent", ABSENT),
    ("none", None),
    ("empty object", {}),
    ("1/1", {"leads": 1, "pods": 1}),
    ("2/3", {"leads": 2, "pods": 3}),
    ("3/1", {"leads": 3, "pods": 1}),
    ("1/5", {"leads": 1, "pods": 5}),
    ("leads only", {"leads": 4}),
    ("pods only", {"pods": 4}),
    ("strings", {"leads": "2", "pods": "3"}),
    ("float truncates", {"leads": 2.9, "pods": 1.2}),
    ("booleans", {"leads": True, "pods": True}),
    ("zero", {"leads": 0, "pods": 0}),
    ("negative", {"leads": -3, "pods": -1}),
    ("at the health cap", {"leads": 1000, "pods": 1000}),
    ("just over the cap, leads", {"leads": 1001, "pods": 1}),
    ("just over the cap, pods", {"leads": 1, "pods": 1001}),
    ("large but allocatable", {"leads": 10**5, "pods": 1}),
    ("infinity", {"leads": float("inf"), "pods": 1}),
    ("nan", {"leads": float("nan"), "pods": 1}),
    ("a word", {"leads": "many", "pods": 1}),
    ("a list", {"leads": [1], "pods": 1}),
    ("an object", {"leads": {"n": 1}, "pods": 1}),
    ("null count", {"leads": None, "pods": 1}),
    ("orchestration is a list", []),
    ("orchestration is a string", "x"),
    ("orchestration is a number", 3),
    ("orchestration is true", True),
    ("extra keys are ignored", {"leads": 1, "pods": 2, "agent_task_timeout_ms": 5, "other": "x"}),
]


def config_of(value):
    return {} if value is ABSENT else {"orchestration": value}


def outcome(function, value, expected_exception_names):
    """What a caller sees: the result (roles, as a sorted list when the function returns a set), or the exception's type name, check and message."""
    try:
        result = function(config_of(value))
    except Exception as exc:  # noqa: BLE001 - the matrix includes failures on purpose
        if exc.__class__.__name__ not in expected_exception_names:
            return {"unexpected": exc.__class__.__name__, "message": str(exc)}
        check = getattr(exc, "check", None)
        return {"raises": exc.__class__.__name__, "check": check, "message": str(exc)}
    names = sorted(result) if isinstance(result, (set, frozenset)) else list(result)
    if len(names) > 50:  # a very large role list is summarised, not stored whole
        digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
        return {"returns_count": len(names), "head": names[:7], "tail": names[-4:], "sha256": digest, "type": type(result).__name__}
    return {"returns": names, "type": type(result).__name__}


# TWO LATENT GAPS IN `follow_on._required_roles`, CHARACTERIZED HERE AS THEY ARE AND DELIBERATELY NOT CHANGED by this extraction (named findings for later):
#   (a) it catches TypeError and ValueError only, so an infinite count (JSON `Infinity`) escapes as OverflowError instead of a named refusal;
#   (b) it has NO CAP: a child config with a huge `leads`/`pods` makes it build a role list of that length (the "large but allocatable" case below is 10**5
#       names; a value like 10**9 would exhaust memory; that is deliberately not run). The CLI's health check caps both at 1000 (`_HEALTH_MAX_ROLE_COUNT`);
#       the follow-on has no counterpart. That asymmetry is the shape of the later fix.
# BOTH FAIL CLOSED IN EFFECT: a crash, or a stall, and never a permission. `_required_roles` runs inside `_preflight`, the read-only phase that precedes the
# intent record, so when either happens nothing has been registered or dispatched; neither allows anything that should not be allowed, which is what the
# prerequisite exists to prevent. Practical bound: the value is read from the CHILD'S OWN config file, which only the herd's owner writes, so one large enough
# to matter has to be put there by hand.
FOLLOW_ON_RAISES = {"FollowOnRefused", "OverflowError"}


def current_outcomes():
    return {
        label: {
            "health": outcome(herdctl._health_expected_roles, value, {"ValueError"}),
            "follow_on": outcome(follow_on._required_roles, value, FOLLOW_ON_RAISES),
        }
        for label, value in CASES
    }


class CharacterizationTests(unittest.TestCase):
    """Green before the extraction (they describe the old code) and green after (nothing a caller can see changed)."""

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text())

    def test_the_golden_covers_exactly_the_matrix(self):
        self.assertEqual(sorted(self.golden), sorted(label for label, _ in CASES))

    def test_the_matrix_is_not_vacuous_it_contains_successes_and_each_kind_of_failure(self):
        kinds = {
            (side, "returns" if ("returns" in o or "returns_count" in o) else o.get("raises", "unexpected"))
            for g in self.golden.values()
            for side, o in g.items()
        }
        self.assertIn(("health", "returns"), kinds)
        self.assertIn(("health", "ValueError"), kinds)
        self.assertIn(("follow_on", "returns"), kinds)
        self.assertIn(("follow_on", "FollowOnRefused"), kinds)
        self.assertFalse([k for k in kinds if k[1] == "unexpected"], "an exception type nobody expected is in the golden")

    def test_herdctl_health_expected_roles_returns_and_raises_exactly_what_it_did(self):
        for label, value in CASES:
            with self.subTest(case=label):
                self.assertEqual(outcome(herdctl._health_expected_roles, value, {"ValueError"}), self.golden[label]["health"])

    def test_follow_on_required_roles_returns_and_raises_exactly_what_it_did(self):
        for label, value in CASES:
            with self.subTest(case=label):
                self.assertEqual(outcome(follow_on._required_roles, value, FOLLOW_ON_RAISES), self.golden[label]["follow_on"])


class SharedDerivationTests(unittest.TestCase):
    def test_logical_roles_is_the_bootstrap_order_supervisor_leads_then_each_executor_with_its_reviewer(self):
        self.assertEqual(lifecycle.logical_roles(1, 1), ["supervisor", "lead1", "executor1", "reviewer1"])
        self.assertEqual(
            lifecycle.logical_roles(2, 2),
            ["supervisor", "lead1", "lead2", "executor1", "reviewer1", "executor2", "reviewer2"],
        )

    def test_role_counts_is_exactly_the_expression_start_herd_used_inline_including_which_exceptions_escape(self):
        def old(orchestration):
            return (max(1, int(orchestration.get("leads", 1))), max(1, int(orchestration.get("pods", 1))))

        for label, value in CASES:
            if not isinstance(value, dict):
                continue
            with self.subTest(case=label):
                try:
                    expected = old(value)
                except Exception as exc:  # noqa: BLE001
                    with self.assertRaises(type(exc)):
                        lifecycle.role_counts(value)
                else:
                    self.assertEqual(lifecycle.role_counts(value), expected)
        counts = [v.get("leads") for _, v in CASES if isinstance(v, dict)]
        self.assertTrue(any(isinstance(c, float) and math.isinf(c) for c in counts), "the matrix includes infinity (OverflowError)")
        self.assertTrue(any(isinstance(c, float) and math.isnan(c) for c in counts), "the matrix includes nan (ValueError)")


class DelegationTests(unittest.TestCase):
    """STATIC pins (they read source): each caller uses the shared functions and none re-derives the role names itself."""

    @staticmethod
    def function_node(module, name):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found in {module.__file__}")

    @staticmethod
    def called_names(node):
        return {getattr(c.func, "attr", getattr(c.func, "id", None)) for c in ast.walk(node) if isinstance(c, ast.Call)}

    def test_start_herd_derives_its_counts_through_role_counts(self):
        node = self.function_node(lifecycle, "start_herd")
        self.assertIn("role_counts", self.called_names(node))
        source = ast.unparse(node)
        self.assertNotIn("orchestration.get('leads', 1)", source, "the inline derivation is gone")
        self.assertNotIn("orchestration.get('pods', 1)", source)

    def test_the_follow_on_and_the_health_check_delegate_and_do_not_build_role_names_themselves(self):
        for module, name in ((follow_on, "_required_roles"), (herdctl, "_health_expected_roles")):
            with self.subTest(function=name):
                node = self.function_node(module, name)
                self.assertTrue({"role_counts", "logical_roles"} <= self.called_names(node), f"{name} must call both shared functions")
                source = ast.unparse(node)
                for fragment in ("f'lead{", "f'executor{", "f'reviewer{", "range(1, pods + 1)"):
                    self.assertNotIn(fragment, source, f"{name} still builds role names itself")

    def test_the_health_check_keeps_its_own_messages_and_its_own_cap(self):
        # the delegation must not move the cap or the wording into the package: they are the CLI's, and the golden pins them
        source = ast.unparse(self.function_node(herdctl, "_health_expected_roles"))
        self.assertIn("_HEALTH_MAX_ROLE_COUNT", source)
        self.assertIn("must be integers", source)
        self.assertIn("is not a JSON object", source)


if __name__ == "__main__":
    unittest.main()
