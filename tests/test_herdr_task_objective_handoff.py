"""Plain-English naming at the producer: a bridge-dispatched Mission's display objective is its GOAL, not its handoff's authority line.

The Mission bridge hands a Herdr child a FIXED template (``target_runtime.mission_bridge.handoff_text``). Its first line is authority only: ``AUTHORIZED MISSION <id> (revision <n>, proposal digest
<64 hex>). This is the approved Mission content only, ...``; the goal follows in an ``OBJECTIVE`` section. The child records the handoff as its task description, and the Herdr-task catalog source
(``target_runtime.herdr_task_source._objective``) took the description's FIRST non-empty line, cut at 160 characters, so the office received ``<prefix> This is …`` and titled the Mission "This is …".

What this pins (owner ruling, plain-English naming, 2026-10-28):
  * the bridge template yields the line after its OBJECTIVE heading, bounded as before;
  * recognition is by STRUCTURE only: the first line has the bridge prefix's exact grammar and the next non-empty line is exactly OBJECTIVE; nothing about the goal's words is read;
  * a missing or heading-only objective in that template is unknown (None), never a heading or the authority line;
  * any other description is returned exactly as before (a near-miss prefix, a prefix without OBJECTIVE next, the herdctl format, plain text);
  * the headings this source treats as the template's are the real template's (read from ``handoff_text``'s own output).
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from target_runtime import herdr_task_source as hts  # noqa: E402
from target_runtime.mission_bridge import handoff_text  # noqa: E402

MISSION_ID = "mn-" + "589a2181de974f8b0fee47cb43e475f7"
DIGEST = "3033dab9ca1127b3a8dc907fcbbfbeb522aa461d3035277ff41413fa58820bcb"


def handoff(objective, revision=1, target_context="", requested_scope="Fix it."):
    return handoff_text(MISSION_ID, revision, DIGEST,
                        {"objective": objective, "target_context": target_context,
                         "requested_scope": requested_scope},
                        ["engineering_change"], [])


def first_line_before(description):
    """The rule BEFORE this change (kept to state the defect): the first non-empty line, bounded."""
    for line in description.splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= hts.MAX_OBJECTIVE_CHARS else line[:hts.MAX_OBJECTIVE_CHARS - 1] + "…"
    return None


class HandoffObjectiveTests(unittest.TestCase):

    def test_the_bridge_handoff_yields_its_goal_not_its_authority_line(self):
        text = handoff("Fix the SYNTHETIC login timeout so sessions stay alive.\nKeep the API unchanged.")
        # the precondition, and the defect: the first line is authority, and the old rule kept "... This is …"
        self.assertTrue(text.splitlines()[0].startswith("AUTHORIZED MISSION " + MISSION_ID))
        self.assertTrue(first_line_before(text).endswith("This is …"))
        self.assertEqual(hts._objective(text), "Fix the SYNTHETIC login timeout so sessions stay alive.")

    def test_a_long_goal_line_is_bounded_as_before_and_a_two_digit_revision_is_the_same_template(self):
        goal = "Rework the SYNTHETIC panel " + "and its layout " * 20
        got = hts._objective(handoff(goal, revision=12))
        self.assertEqual(len(got), hts.MAX_OBJECTIVE_CHARS)
        self.assertTrue(got.endswith("…"))
        self.assertTrue(goal.strip().startswith(got[:-1]))

    def test_a_missing_or_heading_only_objective_is_unknown_never_a_heading_or_the_authority_line(self):
        for objective in ("", "   "):
            self.assertIsNone(hts._objective(handoff(objective)), repr(objective))

    def test_the_template_headings_this_source_knows_are_the_real_templates(self):
        lines = handoff("Goal", target_context="Context").splitlines()
        headings = {lines[i] for i in range(1, len(lines)) if lines[i - 1] == "" and lines[i].isupper()}
        self.assertEqual(headings, set(hts._BRIDGE_SECTION_HEADINGS))

    def test_anything_else_is_returned_exactly_as_before(self):
        good = handoff("Fix the SYNTHETIC thing.")
        lines = good.splitlines()
        cases = {
            # one hex character short in the digest: not the bridge prefix's grammar
            "near-miss digest": good.replace(DIGEST, DIGEST[:-1], 1),
            # an uppercase hex id: not the Mission id grammar
            "near-miss id": good.replace(MISSION_ID, MISSION_ID.upper(), 1),
            # the right prefix, but the next non-empty line is not OBJECTIVE
            "no OBJECTIVE next": "\n".join([lines[0], "", "Something else", "OBJECTIVE", "Goal"]),
            # the herdctl task --mission format (its own exception, unchanged)
            "herdctl format": "OBJECTIVE\nMigrate the SYNTHETIC service.\n\nCONSTRAINTS\n- none",
            "plain text": "Just a plain SYNTHETIC task description.\nWith a second line.",
        }
        for name, text in cases.items():
            if name == "herdctl format":
                self.assertEqual(hts._objective(text), "Migrate the SYNTHETIC service.", name)
            else:
                self.assertEqual(hts._objective(text), first_line_before(text), name)


if __name__ == "__main__":
    unittest.main()
