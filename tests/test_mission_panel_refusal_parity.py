"""The Mission request panel's response boundary: the office's table of code-based wording is the producer's own list.

The DI office (TypeScript: integrations/pixel-agents/server/src/di/missionRequests.ts) does not relay the prose of a workspace-preparation refusal, because that prose names absolute paths of
this machine; it states the refusal from its CODE. Which codes? The ones the producer itself declares: ``mission_bridge.DURABLE_WORKSPACE_PROBLEMS``, "the refusals of a workspace preparation". This module reads the office's
table from its source text, between two markers (the way the other parity tests read the office's lists), and fails if the two differ in either direction. It also checks that the list covers every refusal code the
workspace module itself can raise, so a code added there later cannot escape the table silently.

What this does NOT show: that the prose of a refusal OUTSIDE this list carries no path. Nothing here examines it.
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from target_runtime import mission_bridge  # noqa: E402
from target_runtime import mission_workspace  # noqa: E402

OFFICE = REPO_ROOT / "integrations" / "pixel-agents" / "server" / "src" / "di" / "missionRequests.ts"


def office_codes():
    text = OFFICE.read_text()
    block = text.split("// BEGIN WORKSPACE_REFUSAL_CODES", 1)[1].split("// END WORKSPACE_REFUSAL_CODES", 1)[0]
    return re.findall(r"^\s+'(mission_bridge_[a-z_]+)':", block, re.M)


class WorkspaceRefusalTableParityTests(unittest.TestCase):

    def test_the_offices_table_is_exactly_the_producers_declared_workspace_refusals(self):
        office = office_codes()
        self.assertEqual(len(office), len(set(office)), "a code is listed twice in the office's table")
        self.assertEqual(sorted(office), sorted(mission_bridge.DURABLE_WORKSPACE_PROBLEMS),
                         "the office's table and DURABLE_WORKSPACE_PROBLEMS differ: a refusal either has no code-based wording or has wording for a code the producer does not declare")

    def test_the_declared_list_covers_every_refusal_code_the_workspace_module_can_raise(self):
        raised = set(getattr(mission_workspace, name) for name in dir(mission_workspace) if name.startswith("PROBLEM_"))
        self.assertTrue(raised, "the workspace module declares no problem codes (the test would prove nothing)")
        self.assertLessEqual(raised, set(mission_bridge.DURABLE_WORKSPACE_PROBLEMS))

    def test_every_code_has_the_producers_prefix_and_the_table_is_not_empty(self):
        office = office_codes()
        self.assertGreaterEqual(len(office), 12)
        self.assertTrue(all(code.startswith("mission_bridge_") for code in office))


if __name__ == "__main__":
    unittest.main()
