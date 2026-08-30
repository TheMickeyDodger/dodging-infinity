"""I6: WHEN is this true of? — vintage, turn outcomes, and what a
surface is allowed to render.

BEHAVIOURAL, and the brief says why: a diff, a diffstat, a snapshot and
an AST comparison are blind in exactly the same place. Mutant M6 hid inside a function I2c had just ADDED and defeated all
three verifying parties at once, because each of them was comparing
NEW code against no prior version. Every test here drives an EFFECT — a rendered surface, a
refused write, a selection over a mixed listing.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))

from herdr import identity, observe, turns, vintage       # noqa: E402


def herd_with(state_files):
    """A repository whose `.herd/state` holds exactly these files."""
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name)
    (repo / ".herd" / "state").mkdir(parents=True)
    for name, content in state_files.items():
        path = repo / ".herd" / "state" / name
        path.write_text(
            content if isinstance(content, str)
            else json.dumps(content)
        )
    return temp, repo


CURRENT = "20260828-182050-8807ad"
PRIOR = "20260828-160925-6e31c7"


class CurrentTaskSelectionTests(unittest.TestCase):
    """R-46 AJ-2: current-task selection is EXPLICIT, and a stale
    artifact that DISAGREES is reported rather than reconciled."""

    def herd(self, files):
        temp, repo = herd_with(files)
        self.addCleanup(temp.cleanup)
        return repo

    def test_task_json_is_the_authority(self):
        repo = self.herd({"task.json": {"id": CURRENT,
                                        "status": "ACTIVE"}})
        current = vintage.current_task(repo)
        self.assertEqual(current.task_id, CURRENT)
        self.assertEqual(current.source, vintage.SOURCE_TASK_JSON)
        self.assertEqual(current.disagreements, [])

    def test_a_DISAGREEING_checkpoint_is_REPORTED_not_obeyed(self):
        """SPECIMEN 2, driven. The checkpoint names a COMPLETE prior
        task; a restart that learned "what am I doing" from it would
        resume the wrong mission."""
        repo = self.herd({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "task-checkpoint.md": "# Task checkpoint — %s\n\nbody\n"
                                  % PRIOR,
        })
        current = vintage.current_task(repo)
        self.assertEqual(
            current.task_id, CURRENT,
            "the checkpoint overrode task.json; the authority is"
            " task.json and nothing else (AJ-2)",
        )
        self.assertEqual(
            current.disagreements, [("task-checkpoint.md", PRIOR)],
            "the disagreement was reconciled away instead of"
            " reported; a conflict is evidence about the herd",
        )

    def test_selection_SURVIVES_A_RESTART(self):
        """AJ-2's restart clause, executed: a second observer holding
        no state from the first reads the same answer from disk, and
        the answer CHANGES when the durable authority changes."""
        repo = self.herd({
            "task.json": {"id": PRIOR, "status": "COMPLETE"},
            "task-checkpoint.md": "# Task checkpoint — %s\n" % PRIOR,
        })
        first = vintage.current_task(repo)
        self.assertEqual(first.task_id, PRIOR)
        (repo / ".herd" / "state" / "task.json").write_text(
            json.dumps({"id": CURRENT, "status": "ACTIVE"})
        )
        second = vintage.current_task(repo)
        self.assertEqual(second.task_id, CURRENT)
        self.assertEqual(second.disagreements,
                         [("task-checkpoint.md", PRIOR)])

    def test_NO_task_json_yields_NO_current_task(self):
        """Fail-closed: an observer that is unable to say which task is running shows
        LESS, it does not guess. Every vintage-bearing
        field is then UNKNOWN and omitted."""
        repo = self.herd({
            "task-checkpoint.md": "# Task checkpoint — %s\n" % PRIOR,
        })
        current = vintage.current_task(repo)
        self.assertIsNone(current.task_id)
        self.assertEqual(current.source, vintage.SOURCE_NONE)
        self.assertFalse(current.known)
        self.assertEqual(
            vintage.classify(PRIOR, current.task_id),
            vintage.VINTAGE_UNKNOWN,
            "a field was classified against an unknown current task;"
            " that makes every field read PRIOR, a different wrong"
            " answer rather than a safer one",
        )

    def test_the_heading_is_read_and_the_BODY_is_not(self):
        """A checkpoint body quotes other tasks' ids constantly. Only
        line 1 says what the document is ABOUT."""
        text = (
            "# Task checkpoint — %s\n\n"
            "We reviewed %s and superseded %s.\n" % (CURRENT, PRIOR, PRIOR)
        )
        self.assertEqual(vintage.task_id_of_text(text), CURRENT)
        self.assertIsNone(
            vintage.task_id_of_text("no heading here — %s\n" % PRIOR),
            "a task id from the body was taken as the document's own"
            " subject",
        )


class RenderOmissionTests(unittest.TestCase):
    """R-46 AJ-1: two branches, and the third is the trap.

    `[available]` was a CAVEAT SLOT. It meant "readable" and was read
    as "current", which is how the March-2026 vendor-packet objective
    rendered above a correct current task id under the same marker.
    """

    def herd(self, files):
        temp, repo = herd_with(files)
        self.addCleanup(temp.cleanup)
        return repo

    def render(self, files):
        repo = self.herd(files)
        return observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )

    def test_SPECIMEN_1_the_unvintaged_mission_does_NOT_render(self):
        """THE ORIGINAL SPECIMEN, driven end to end.

        `mission.json` carries no task id — verified against this
        repository's real one — so its objective is not attributed to
        a task and MUST NOT render.
        """
        objective = "Close the two factual-support residuals"
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": objective},
        })
        self.assertNotIn(
            objective, text,
            "an objective with no task identity was rendered; a"
            " reader takes it as the CURRENT mission, which is"
            " specimen 1 exactly",
        )
        self.assertNotIn("Mission [available]", text)
        self.assertIn("Mission: OMITTED", text)
        self.assertIn(
            CURRENT, text,
            "the current task id vanished along with the mission; the"
            " omission is supposed to remove one field, not the"
            " surface's ability to say what is running",
        )

    def test_a_mission_CARRYING_its_task_identity_DOES_render(self):
        """The first branch. Omission is not a blanket refusal to show
        missions — it is a refusal to show one whose age is unknown."""
        objective = "the current objective"
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": objective,
                             "task_id": CURRENT},
        })
        self.assertIn(objective, text)
        self.assertIn("current %s" % CURRENT, text)

    def test_a_PRIOR_mission_renders_as_SUPERSEDED_never_as_current(self):
        """AJ-3: it may render, and it renders labelled."""
        objective = "the march vendor packet"
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": objective,
                             "task_id": PRIOR},
        })
        self.assertIn(objective, text)
        self.assertIn("SUPERSEDED", text)
        self.assertIn(PRIOR, text)
        self.assertNotIn("Mission [available]", text)

    def test_SPECIMEN_2_a_prior_checkpoint_renders_as_SUPERSEDED(self):
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "task-checkpoint.md": "# Task checkpoint — %s\n" % PRIOR,
        })
        self.assertIn("Checkpoint [SUPERSEDED", text)
        self.assertIn(PRIOR, text)
        self.assertIn("DISAGREES: task-checkpoint.md", text)

    def test_SPECIMEN_3_the_surface_says_where_current_truth_LIVES(self):
        """AJ-4. An append-only log presents its STALEST content
        FIRST, which is how a status header said two increments were
        delegated while its tail recorded five closed."""
        text = self.render({"task.json": {"id": CURRENT,
                                          "status": "ACTIVE"}})
        self.assertIn("newest entry is at the END", text)
        self.assertIn("task.json", text)

    def test_there_is_NO_LABEL_for_an_unknown_vintage(self):
        """The construction half of AJ-1: a renderer is unable to invent the third branch, because there
        is no caption available."""
        with self.assertRaises(ValueError):
            vintage.vintage_label(vintage.VINTAGE_UNKNOWN, None)
        self.assertFalse(vintage.renders(vintage.VINTAGE_UNKNOWN))
        self.assertTrue(vintage.renders(vintage.VINTAGE_CURRENT))
        self.assertTrue(vintage.renders(vintage.VINTAGE_PRIOR))

    def test_an_OMISSION_is_itself_disclosed(self):
        """A field that silently disappeared would trade one invisible
        problem for another."""
        obs = observe.observe(
            self.herd({
                "task.json": {"id": CURRENT, "status": "ACTIVE"},
                "mission.json": {"version": 1, "objective": "x"},
            }),
            probe_agents=False,
        )
        reasons = [
            d for d in obs["diagnostics"]
            if d.get("source") == "mission"
            and "no task identity" in (d.get("detail") or "")
        ]
        self.assertEqual(len(reasons), 1, obs["diagnostics"])

    def test_an_omission_does_NOT_demote_completeness(self):
        """A deliberate omission is a fact about the ARTIFACT, not a
        failure of the projection. Demoting here would make each herd PARTIAL for as long as
        mission.json lacks a task id, and a marker that is in each
        case PARTIAL conveys little."""
        obs = observe.observe(
            self.herd({
                "task.json": {"id": CURRENT, "status": "ACTIVE"},
                "mission.json": {"version": 1, "objective": "x"},
            }),
            probe_agents=False,
        )
        self.assertEqual(obs["completeness"], "COMPLETE")


class TaskScopedSelectionTests(unittest.TestCase):
    """R-59 AW-2: selection is task-scoped, in every case.

    `reviews/` is TASK-MIXED and the FILENAME is the only carrier of
    task identity. A selector keyed on round number alone returns a
    confident wrong answer with no warning — the supervisor hit
    exactly this.
    """

    MIXED = [
        "%s-round-01.md" % PRIOR,
        "%s-round-02.md" % PRIOR,
        "%s-round-01.md" % CURRENT,
        "%s-round-17.md" % CURRENT,
        "notes.md",
        "%s-round-xx.md" % CURRENT,
    ]

    def test_a_mixed_directory_selects_only_THIS_task(self):
        rounds = vintage.round_files_for_task(self.MIXED, CURRENT)
        self.assertEqual(
            [n for _r, n in rounds],
            ["%s-round-01.md" % CURRENT, "%s-round-17.md" % CURRENT],
        )

    def test_the_HIGHEST_round_of_ANOTHER_task_is_not_selected(self):
        """The confident wrong answer, driven: round 02 of the prior
        task outranks round 01 of this one by NUMBER, and a
        number-keyed selector would return it."""
        rounds = vintage.round_files_for_task(
            self.MIXED, PRIOR
        )
        self.assertEqual(vintage.latest_round_for_task(self.MIXED, PRIOR),
                         (2, "%s-round-02.md" % PRIOR))
        self.assertEqual(
            vintage.latest_round_for_task(self.MIXED, CURRENT),
            (17, "%s-round-17.md" % CURRENT),
        )
        self.assertNotEqual(rounds, [])

    def test_selecting_WITHOUT_a_task_scope_RAISES(self):
        """The construction half: an unscoped selection is not
        available to be made, so no caller can get an answer by
        omitting the scope."""
        for scope in (None, "", 0):
            with self.subTest(scope=scope):
                with self.assertRaises(vintage.TaskScopeRequired):
                    vintage.round_files_for_task(self.MIXED, scope)

    def test_a_task_with_NO_rounds_returns_EMPTY_not_someone_elses(self):
        """An empty result is a QUESTION — and the question it answers
        here is 'this task has no rounds', not 'there are no rounds'."""
        self.assertEqual(
            vintage.round_files_for_task(self.MIXED,
                                         "20260101-000000-abcdef"),
            [],
        )


class BindingStrengthTests(unittest.TestCase):
    """R-61 AY-4: a surface reporting a role as bound says HOW
    STRONGLY.

    From I2c's residual: `binding_gap` requires a NON-EMPTY stable
    mapping, not a COMPLETE one, so a record missing `pane_id` or
    `cwd` still binds and `classify` then compares fewer fields.
    """

    def binding(self, **stable):
        return {"logical": "supervisor", "agent": "h-sup",
                "session": "s-1", "stable": stable}

    def test_a_COMPLETE_binding_reports_complete(self):
        full = {field: "v" for field in identity.STABLE_FIELDS}
        strength, captured, missing = vintage.binding_strength(
            self.binding(**full)
        )
        self.assertEqual(strength, "complete")
        self.assertEqual(sorted(captured),
                         sorted(identity.STABLE_FIELDS))
        self.assertEqual(missing, [])

    def test_a_PARTIAL_binding_names_the_fields_it_LACKS(self):
        """The residual made visible: this binding is accepted by
        `binding_gap` and compares fewer fields than a complete one."""
        partial = self.binding(name="h-sup", workspace_id="wT")
        self.assertIsNone(
            identity.binding_gap(partial),
            "the premise of this test is that such a binding IS"
            " accepted; if it were rejected there would be no residual",
        )
        strength, captured, missing = vintage.binding_strength(partial)
        self.assertEqual(strength, "partial")
        self.assertEqual(sorted(missing), ["cwd", "pane_id"])

    def test_the_SURFACE_reports_the_weakness(self):
        """Driven through `observe`, not through the helper: the
        requirement is that a SURFACE can say how strongly."""
        temp, repo = herd_with({"task.json": {"id": CURRENT,
                                              "status": "ACTIVE"}})
        self.addCleanup(temp.cleanup)
        identity.save_bindings(repo / ".herd", {
            "version": 1,
            "roles": {"supervisor": self.binding(name="h-sup",
                                                 workspace_id="wT")},
        })
        text = observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )
        self.assertIn("identity=partial", text)
        self.assertIn("missing: cwd, pane_id", text)

    def test_a_binding_with_NO_stable_identity_reports_none(self):
        strength, captured, missing = vintage.binding_strength(
            {"logical": "s", "agent": "a", "session": "x", "stable": {}}
        )
        self.assertEqual(strength, "none")
        self.assertEqual(captured, [])


class TurnOutcomeTests(unittest.TestCase):
    """R-55 AS-1/AS-2: the outcome is RECORDED, inferred from `agent_status` alone."""

    def herd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / ".herd"
        (root / "state").mkdir(parents=True)
        return root

    def turn(self, expected="checkpoint.md"):
        return turns.new_turn("t-1", CURRENT, "executor1",
                              expected_artifact=expected, now=100)

    def test_the_five_outcomes_are_DISTINCT(self):
        self.assertEqual(len(set(turns.TURN_OUTCOMES)), 5)
        for outcome in ("running", "completed", "blocked",
                        "failed_transport", "interrupted"):
            self.assertIn(outcome, turns.TURN_OUTCOMES)

    def test_a_turn_is_RUNNING_before_the_work(self):
        """K-1: the record is written first, which is what makes an
        INTERRUPTED turn distinguishable from one that started."""
        self.assertEqual(self.turn()["outcome"], turns.TURN_RUNNING)

    def test_COMPLETED_without_the_expected_artifact_is_UNWRITABLE(self):
        """AS-2's construction half. 'No artifact' is not the only evidence of failure, because a
        turn that lost its artifact is not written down as a success
        at all."""
        with self.assertRaises(turns.TurnRecordError):
            turns.close(self.turn(), turns.TURN_COMPLETED,
                        artifact_present=False)
        # And the same close SUCCEEDS with the artifact present, so
        # the refusal is about the missing evidence rather than about
        # `completed` being unwritable in general.
        closed = turns.close(self.turn(), turns.TURN_COMPLETED,
                             artifact_present=True, now=200)
        self.assertEqual(closed["outcome"], turns.TURN_COMPLETED)

    def test_a_FAILURE_must_name_its_CAUSE(self):
        """A record that says only `blocked` sends the next reader
        back to the artifacts to guess — which is the absence of
        evidence AS-2 forbids."""
        for outcome in turns.FAILURE_OUTCOMES:
            with self.subTest(outcome=outcome):
                with self.assertRaises(turns.TurnRecordError):
                    turns.close(self.turn(), outcome)
                named = turns.close(self.turn(), outcome,
                                    cause="the reason", now=200)
                self.assertEqual(named["cause"], "the reason")

    def test_an_UNKNOWN_outcome_is_UNWRITABLE(self):
        with self.assertRaises(turns.TurnRecordError):
            turns.close(self.turn(), "fine", cause="x")

    def test_agent_status_ALONE_cannot_name_an_outcome(self):
        """AS-1, driven by the SHAPE of the API: the same status
        yields three different outcomes depending on facts a status
        does not carry, so there is no status-only function to call."""
        status = "idle"
        self.assertEqual(
            turns.outcome_from(status, transport_ok=False,
                               artifact_present=True),
            turns.TURN_FAILED_TRANSPORT,
        )
        self.assertEqual(
            turns.outcome_from(status, transport_ok=True,
                               artifact_present=True, interrupted=True),
            turns.TURN_INTERRUPTED,
        )
        self.assertEqual(
            turns.outcome_from(status, transport_ok=True,
                               artifact_present=True),
            turns.TURN_COMPLETED,
        )
        self.assertEqual(
            turns.outcome_from(status, transport_ok=True,
                               artifact_present=False),
            turns.TURN_BLOCKED,
        )

    def test_transport_is_asked_BEFORE_the_status(self):
        """A turn that reached its agent has no agent status worth
        reading, and reading one anyway is how a transport failure
        became 'the agent is busy'."""
        self.assertEqual(
            turns.outcome_from("working", transport_ok=False,
                               artifact_present=False),
            turns.TURN_FAILED_TRANSPORT,
        )

    def test_a_turn_record_SURVIVES_the_process_that_wrote_it(self):
        root = self.herd()
        turns.append_turn(root, turns.close(
            self.turn(), turns.TURN_BLOCKED, cause="reviewer rejected",
            now=200,
        ))
        reloaded = turns.load_turns(root)["turns"]
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0]["cause"], "reviewer rejected")

    def test_a_CORRUPT_turn_record_is_REFUSED_not_rebuilt(self):
        root = self.herd()
        turns.turns_path(root).write_text("{not json")
        with self.assertRaises(turns.TurnRecordError):
            turns.load_turns(root)
        self.assertEqual(turns.turns_path(root).read_text(), "{not json")

    def test_the_write_is_ATOMIC(self):
        from unittest.mock import patch
        root = self.herd()
        turns.save_turns(root, {"version": 1, "turns": [{"a": 1}]})
        before = turns.turns_path(root).read_bytes()
        with patch("herdr.turns.os.replace",
                   side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                turns.save_turns(root, {"version": 1, "turns": []})
        self.assertEqual(turns.turns_path(root).read_bytes(), before)

    def test_turn_selection_is_TASK_SCOPED(self):
        document = {"version": 1, "turns": [
            {"turn_id": "a", "task_id": CURRENT, "outcome": "completed"},
            {"turn_id": "b", "task_id": PRIOR, "outcome": "blocked"},
        ]}
        self.assertEqual(
            [t["turn_id"] for t in turns.turns_for_task(document, CURRENT)],
            ["a"],
        )
        with self.assertRaises(vintage.TaskScopeRequired):
            turns.turns_for_task(document, None)

    def test_NO_DURATION_BOUND_ON_A_TURN_EXISTS(self):
        """AS-3, asserted rather than described: this module holds no
        deadline on a turn. A duration bound here would be a mission
        timeout wearing a bootstrap costume, which I3's pinned test
        rejects — and the place it would appear is a module that
        records how turns end.

        A NOTE ON THIS CHECK, recorded rather than quietly fixed: the
        first version scanned the module's SOURCE TEXT and failed on
        the module's own docstring, which EXPLAINS that no deadline
        exists. A text scan is unable to tell a BOUND from an explanation of
        why there is none — the fourth instance in this mission of an
        instrument whose domain was wider than its subject. It reads
        the module's CODE now: identifiers, attributes and keywords,
        with docstrings excluded by construction.
        """
        forbidden = ("deadline", "timeout", "max_seconds", "expire")
        self.assertEqual(
            self.time_bound_identifiers(turns), [],
            "a duration bound appears in the turn-outcome module; a"
            " turn is engineering duration and bounding it is what I3"
            " forbids",
        )
        # NOT VACUOUS: the same walk over the readiness module, which
        # legitimately DOES bound a bootstrap, finds one. A detector
        # that reports clean everywhere is worth little.
        from target_runtime import readiness as readiness_module
        self.assertTrue(
            self.time_bound_identifiers(readiness_module),
            "the detector found no duration bound in the module that"
            " has one, so a clean result above proves nothing",
        )

    @staticmethod
    def time_bound_identifiers(module, forbidden=(
        "deadline", "timeout", "max_seconds", "expire",
    )):
        """Duration-bound identifiers in a module's CODE, not prose."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(module))
        found = set()
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.append(node.name)
            for name in names:
                low = name.lower()
                for token in forbidden:
                    if token in low:
                        found.add(name)
        return sorted(found)


class RoutedIsNotDeliveredTests(unittest.TestCase):
    """R-55 AS-4: routed and effect-observed are SEPARATE facts.

    R-26 found that authority is not enacted state. R-49 found a queued ruling that arrived too late to protect what
    it named. This is the third mode:
    RECEIVED, STARTED, THEN ANNIHILATED.
    """

    def turn(self):
        return turns.new_turn("t-1", CURRENT, "lead1", now=100)

    def test_a_ROUTED_turn_is_NOT_delivered(self):
        routed = turns.mark_routed(self.turn(), now=110)
        self.assertTrue(routed["routed_at"])
        self.assertFalse(
            turns.delivered(routed),
            "a turn counted as delivered on the strength of having"
            " been sent; that is the mode this ruling names",
        )

    def test_delivery_requires_the_EFFECT(self):
        seen = turns.mark_effect_observed(
            turns.mark_routed(self.turn(), now=110), now=120
        )
        self.assertTrue(turns.delivered(seen))

    def test_an_effect_cannot_be_observed_for_an_UNROUTED_turn(self):
        with self.assertRaises(turns.TurnRecordError):
            turns.mark_effect_observed(self.turn())

    def test_the_SURFACE_reports_routed_but_unobserved(self):
        """Driven through `observe`: the requirement is that a reader
        can SEE the gap, not that the record contains it."""
        temp, repo = herd_with({"task.json": {"id": CURRENT,
                                              "status": "ACTIVE"}})
        self.addCleanup(temp.cleanup)
        turns.append_turn(
            repo / ".herd", turns.mark_routed(self.turn(), now=110)
        )
        text = observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )
        self.assertIn("routed-but-unobserved=1", text)


class OmissionBranchTests(unittest.TestCase):
    """AJ-1's SECOND BRANCH, driven as hard as the first.

    An untested omission path silently becomes a caveat, and that is
    the specific decay mode here: it would reproduce the original
    defect with better wording. So these assert that NO OUTPUT IS EMITTED for an unvintaged
    field — not that something safe is.
    """

    def render(self, files):
        temp, repo = herd_with(files)
        self.addCleanup(temp.cleanup)
        return observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )

    def test_NO_LINE_carries_the_unvintaged_objective(self):
        """Not 'a safe line is emitted' — NO line carries it."""
        objective = "the march vendor packet objective"
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": objective},
        })
        carrying = [
            line for line in text.splitlines()
            if objective in line
        ]
        self.assertEqual(
            carrying, [],
            "the omitted objective appeared on %d line(s); an omission"
            " that still prints the value is the caveat branch"
            % len(carrying),
        )

    def test_NO_AVAILABLE_MARKER_survives_beside_an_omitted_field(self):
        """`[available]` is the caveat slot that produced the
        specimen. It must not appear on the mission line in a form, including a
        renamed one that still reads as health."""
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": "x"},
        })
        mission_lines = [
            line for line in text.splitlines()
            if line.startswith("Mission")
        ]
        self.assertEqual(len(mission_lines), 1, mission_lines)
        for marker in ("[available]", "[current", "[fresh"):
            self.assertNotIn(marker, mission_lines[0])

    def test_the_omission_survives_a_MISSING_current_task(self):
        """With no current task no field has an establishable
        vintage, so every vintage-bearing field omits — the omission
        path has to hold when it is the ONLY path taken."""
        text = self.render({
            "mission.json": {"version": 1, "objective": "an objective",
                             "task_id": CURRENT},
            "task-checkpoint.md": "# Task checkpoint — %s\n" % CURRENT,
        })
        self.assertNotIn("an objective", text)
        self.assertIn("Mission: OMITTED", text)
        self.assertIn("Checkpoint: OMITTED", text)

    def test_an_omitted_field_emits_EXACTLY_ONE_line(self):
        """The omission notice is itself bounded: a field that omits
        does not become three lines of apology."""
        text = self.render({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "mission.json": {"version": 1, "objective": "x"},
        })
        self.assertEqual(
            len([l for l in text.splitlines()
                 if l.startswith("Mission")]), 1,
        )

    def test_the_render_NEVER_RAISES_on_an_omitted_field(self):
        """A surface that crashes instead of omitting has replaced one
        failure with a louder one."""
        for files in (
            {},
            {"mission.json": {"objective": "x"}},
            {"task.json": {"status": "ACTIVE"}},
            {"task.json": "not json", "mission.json": "not json"},
            {"task-checkpoint.md": ""},
        ):
            with self.subTest(files=sorted(files)):
                text = self.render(files)
                self.assertIsInstance(text, str)
                self.assertIn("Mission", text)


class InForceTests(unittest.TestCase):
    """R-62 AZ-4: the THIRD AXIS — IS THIS IN FORCE?

    Liveness asks IS THIS TRUE. Vintage asks WHEN IS THIS TRUE OF.
    This asks whether an instruction is doing anything, and the
    specimen is the supervisor's own: a message that named the increment, gave the complete requirement
    set, told the lead to brief, AND attached a hold whose release
    depended on a further message that was sent. Delivered, and not in effect.
    """

    def test_DELIVERED_is_not_IN_EFFECT(self):
        state, detail = turns.instruction_force(delivered_at=100)
        self.assertEqual(state, turns.FORCE_DELIVERED)
        self.assertNotEqual(state, turns.FORCE_IN_EFFECT)
        self.assertIn("has NOT been observed", detail)

    def test_AUTHORIZED_is_not_GATED_and_neither_is_IN_EFFECT(self):
        authorized, _ = turns.instruction_force(authorized=True)
        self.assertEqual(authorized, turns.FORCE_AUTHORIZED)
        gated, _ = turns.instruction_force(
            delivered_at=100, gated_on="a message never sent"
        )
        self.assertEqual(gated, turns.FORCE_GATED)
        self.assertNotEqual(authorized, gated)

    def test_A_GATE_OUTRANKS_DELIVERY(self):
        """THE SPECIMEN, driven: the instruction WAS delivered, and a
        surface reporting `delivered` would mislead exactly as the
        message did."""
        state, detail = turns.instruction_force(
            delivered_at=100, authorized=True,
            gated_on="a confirmation that was never sent",
        )
        self.assertEqual(state, turns.FORCE_GATED)
        self.assertIn("never sent", detail)

    def test_only_OBSERVED_EFFECT_is_in_force(self):
        state, _ = turns.instruction_force(
            delivered_at=100, effect_observed_at=110
        )
        self.assertEqual(state, turns.FORCE_IN_EFFECT)

    def test_a_HOLD_AND_ITS_RELEASE_in_one_instruction_is_DETECTED(self):
        """The shape itself, made checkable. An instruction that both
        withholds and carries actionable requirements is two states at
        once, and a reader takes whichever half is louder — which is
        why proceeding on the actionable half was the REASONABLE
        reading, not a breach."""
        self.assertTrue(turns.hold_and_release_together(
            gated_on="a later message", carries_requirements=True,
        ))
        self.assertFalse(turns.hold_and_release_together(
            gated_on="a later message", carries_requirements=False,
        ))
        self.assertFalse(turns.hold_and_release_together(
            gated_on=None, carries_requirements=True,
        ))

    def test_the_four_states_are_DISTINCT(self):
        self.assertEqual(len(set(turns.FORCE_STATES)), 4)


class ObserverDerivedOutcomeTests(unittest.TestCase):
    """R-63 BA-5: within this design a turn killed by transport is
    unable to write its own epitaph.

    The dying party does not run. Every test here therefore closes a
    turn WITHOUT the turn's own participation: the record is written
    by an observer that survives, which is the property AS-1's
    motivating specimen needed and did not have — lead1's turn killed
    by ENOTFOUND, leaving no artifact, no reason, and a status
    indistinguishable from healthy.

        NO CLOCK APPEARS IN THESE. Within this class no test passes
    elapsed time and no code under test reads it, so a long
    legitimate turn and a dead one are separated by evidence alone.
    """

    ROLES = {"supervisor": "h-sup", "lead1": "h-lead1",
             "executor1": "h-exec1", "reviewer1": "h-rev1"}

    def herd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / ".herd"
        (root / "state").mkdir(parents=True)
        return root

    def prober(self, statuses):
        return lambda agent: {"status": statuses.get(agent, "idle"),
                              "raw": None}

    def observe(self, root, statuses, **kwargs):
        return turns.observe_control_roles(
            root, self.ROLES, CURRENT, self.prober(statuses), **kwargs
        )

    def test_a_turn_BEGINS_durably_when_a_role_starts_working(self):
        root = self.herd()
        events, _doc = self.observe(root, {"h-exec1": "working"})
        self.assertEqual([e[0] for e in events], ["opened"])
        stored = turns.load_turns(root)["turns"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["logical"], "executor1")
        self.assertEqual(stored[0]["outcome"], turns.TURN_RUNNING)

    def test_THE_ENOTFOUND_SPECIMEN_yields_a_FAILURE_RECORD(self):
        """THE CASE THAT MOTIVATED AS-1, driven end to end.

        A role is working; its transport then dies.         The turn itself does not run again — within this test it is
        given no opportunity to. The observer derives
        `failed_transport` and NAMES THE CAUSE.
        """
        root = self.herd()
        self.observe(root, {"h-lead1": "working"})
        events, _doc = self.observe(root, {"h-lead1": "missing"})
        self.assertEqual(
            [(o, l) for o, l, _t in events],
            [(turns.TURN_FAILED_TRANSPORT, "lead1")],
        )
        stored = turns.load_turns(root)["turns"]
        self.assertEqual(stored[0]["outcome"],
                         turns.TURN_FAILED_TRANSPORT)
        self.assertIn("could not be reached", stored[0]["cause"])
        self.assertIn(
            "cannot record its own end", stored[0]["cause"],
            "the record does not say the outcome was DERIVED; a"
            " reader cannot tell an epitaph from a self-report",
        )

    def test_an_UNPARSABLE_probe_is_ALSO_transport_failure(self):
        """`unknown` is the other probe sentinel: an answer arrived and
        could not be read, which is a fact about the transport."""
        root = self.herd()
        self.observe(root, {"h-rev1": "working"})
        events, _doc = self.observe(root, {"h-rev1": "unknown"})
        self.assertEqual(events[0][0], turns.TURN_FAILED_TRANSPORT)

    def test_a_role_that_SETTLES_completes(self):
        root = self.herd()
        self.observe(root, {"h-sup": "working"})
        events, _doc = self.observe(root, {"h-sup": "idle"})
        self.assertEqual(events[0][0], turns.TURN_COMPLETED)

    def test_a_settled_role_MISSING_its_artifact_is_BLOCKED(self):
        """AS-2: the turn ended without producing what it was for, and
        the record NAMES that as the cause."""
        root = self.herd()
        self.observe(root, {"h-exec1": "working"},
                     expected={"executor1": "checkpoint.md"})
        events, _doc = self.observe(
            root, {"h-exec1": "done"},
            expected={"executor1": "checkpoint.md"},
            artifact_probe=lambda name: False,
        )
        self.assertEqual(events[0][0], turns.TURN_BLOCKED)
        stored = turns.load_turns(root)["turns"][0]
        self.assertIn("is ABSENT", stored["cause"])
        self.assertIn("checkpoint.md", stored["cause"])

    def test_a_settled_role_WITH_its_artifact_completes(self):
        root = self.herd()
        self.observe(root, {"h-exec1": "working"},
                     expected={"executor1": "checkpoint.md"})
        events, _doc = self.observe(
            root, {"h-exec1": "done"},
            expected={"executor1": "checkpoint.md"},
            artifact_probe=lambda name: True,
        )
        self.assertEqual(events[0][0], turns.TURN_COMPLETED)

    def test_a_STILL_WORKING_role_stays_RUNNING_and_churns_NOTHING(self):
        """Idempotent by construction: a controller running every few
        seconds must not rewrite the record on every pass."""
        root = self.herd()
        self.observe(root, {"h-sup": "working"})
        before = turns.turns_path(root).read_bytes()
        events, _doc = self.observe(root, {"h-sup": "working"})
        self.assertEqual(events, [])
        self.assertEqual(turns.turns_path(root).read_bytes(), before)

    def test_UNREADABLE_EVIDENCE_leaves_the_turn_RUNNING(self):
        """R-63's explicit instruction: where the evidence fails to
        distinguish a long legitimate turn from a dead one, the honest
        outcome is RUNNING — within this gate, neither a guess nor a
        deadline."""
        root = self.herd()
        self.observe(root, {"h-sup": "working"})
        events, _doc = self.observe(root, {"h-sup": "a-status-nobody-emits"})
        self.assertEqual(events, [])
        stored = turns.load_turns(root)["turns"][0]
        self.assertEqual(stored["outcome"], turns.TURN_RUNNING)
        self.assertEqual(stored["cause"], turns.EVIDENCE_INSUFFICIENT)

    def test_NO_ELAPSED_TIME_REACHES_THE_DERIVATION(self):
        """AS-3 at the seam that could most easily break it. `derive`
        takes status, artifact and interruption — within its signature
        there is no time parameter to pass, so introducing a deadline
        means changing the signature."""
        import inspect
        params = list(
            inspect.signature(turns.derive).parameters
        )
        self.assertEqual(
            params,
            ["record", "status", "artifact_present", "interrupted"],
        )
        # And the same derivation twice, with everything else equal,
        # returns the same answer — there is no state that could be
        # standing in for a clock.
        record = turns.new_turn("t", CURRENT, "lead1", now=1)
        first = turns.derive(record, "working")
        second = turns.derive(record, "working")
        self.assertEqual(first, second)
        self.assertEqual(first[0], None)

    def test_the_observation_is_TASK_SCOPED(self):
        root = self.herd()
        with self.assertRaises(vintage.TaskScopeRequired):
            turns.observe_control_roles(
                root, self.ROLES, None, self.prober({})
            )

    def test_ALL_FOUR_control_roles_are_observed(self):
        """BA-4's bound: the four control roles of this herd."""
        root = self.herd()
        events, _doc = self.observe(root, {
            agent: "working" for agent in self.ROLES.values()
        })
        self.assertEqual(
            sorted(l for _o, l, _t in events),
            ["executor1", "lead1", "reviewer1", "supervisor"],
        )

    def test_ROLE_STATE_is_determinable_from_DURABLE_STATE_ALONE(self):
        """BA-4's fourth property: a reader holding only the file can
        say each role's current turn, last transition and recovery
        state."""
        root = self.herd()
        self.observe(root, {"h-lead1": "working", "h-sup": "working"})
        self.observe(root, {"h-lead1": "missing", "h-sup": "working"})
        document = turns.load_turns(root)
        lead = turns.role_state(document, "lead1")
        self.assertEqual(lead["last_outcome"],
                         turns.TURN_FAILED_TRANSPORT)
        self.assertEqual(lead["recovery"], "needs_recovery")
        self.assertIsNone(lead["current"])
        supervisor = turns.role_state(document, "supervisor")
        self.assertEqual(supervisor["recovery"], "in_flight")
        self.assertIsNotNone(supervisor["current"])
        untouched = turns.role_state(document, "reviewer1")
        self.assertEqual(untouched["recovery"], "no_turn_recorded")

    def test_the_record_SURVIVES_the_observer_that_wrote_it(self):
        """A second reader holding no state reads the same answer."""
        root = self.herd()
        self.observe(root, {"h-exec1": "working"})
        self.observe(root, {"h-exec1": "missing"})
        reloaded = turns.load_turns(root)
        self.assertEqual(
            turns.role_state(reloaded, "executor1")["last_outcome"],
            turns.TURN_FAILED_TRANSPORT,
        )


class RoleStateSurfacedTests(unittest.TestCase):
    """R-66 DECISION: `role_state` is WIRED, not deleted.

    It had six call sites and all six were tests. An unreachable
    production helper invites a future reader to believe per-role
    recovery state is surfaced when it is not, and "someone will wire
    it later" is how R-63 was very nearly justified. It answers the
    question a restarting reader actually has — WHICH ROLES NEED
    RECOVERY — which is BA-4's fourth destination property, so the
    honest resolution is to reach it rather than remove it.

    R-12's condition applies: SURFACED, not merely stored. And AJ-1's
    two branches apply PER ROLE — a role with a recorded turn carries
    its state, a role without one renders NO ROW at all.
    """

    ROLES = {"supervisor": "h-sup", "lead1": "h-lead1",
             "executor1": "h-exec1", "reviewer1": "h-rev1"}

    def herd(self, roles=None):
        temp, repo = herd_with({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "runtime.json": {"agents": roles or self.ROLES,
                             "panes": {}},
        })
        self.addCleanup(temp.cleanup)
        return repo

    def render(self, repo):
        return observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )

    def observe_pass(self, repo, statuses):
        return turns.observe_control_roles(
            repo / ".herd", self.ROLES, CURRENT,
            lambda agent: {"status": statuses.get(agent, "idle"),
                           "raw": None},
        )

    # --- THE CARRY BRANCH ---------------------------------------

    def test_a_role_NEEDING_RECOVERY_is_SURFACED_with_its_cause(self):
        """The whole reason to wire it: after a restart a reader can
        see which role died and why, from durable state."""
        repo = self.herd()
        self.observe_pass(repo, {"h-lead1": "working"})
        self.observe_pass(repo, {"h-lead1": "missing"})
        obs = observe.observe(repo, probe_agents=False)
        rows = {r["logical"]: r for r in obs["turns"]["roles"]}
        self.assertIn("lead1", rows)
        self.assertEqual(rows["lead1"]["recovery"], "needs_recovery")
        self.assertEqual(rows["lead1"]["last_outcome"],
                         turns.TURN_FAILED_TRANSPORT)
        text = self.render(repo)
        self.assertIn("recovery=needs_recovery", text)
        self.assertIn("could not be reached", text)

    def test_an_IN_FLIGHT_role_is_surfaced_as_in_flight(self):
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        text = self.render(repo)
        self.assertIn("recovery=in_flight", text)

    def test_a_role_needing_recovery_is_also_DIAGNOSED(self):
        """R-12: surfaced, not merely stored — and a role that needs
        attention says so in the diagnostics too, where an operator
        scanning for problems looks."""
        repo = self.herd()
        self.observe_pass(repo, {"h-rev1": "working"})
        self.observe_pass(repo, {"h-rev1": "missing"})
        obs = observe.observe(repo, probe_agents=False)
        self.assertTrue([
            d for d in obs["diagnostics"]
            if d.get("source") == "turns"
            and "needs attention" in (d.get("detail") or "")
        ], obs["diagnostics"])

    # --- THE OMISSION BRANCH, TESTED AS HARD --------------------

    def test_a_role_with_NO_TURN_renders_NO_ROW(self):
        """AJ-1's second branch per role. Not 'a safe row is shown' —
        NO ROW EXISTS, so there is no slot for a reader to misread as
        health."""
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        obs = observe.observe(repo, probe_agents=False)
        rendered = {r["logical"] for r in obs["turns"]["roles"]}
        self.assertEqual(rendered, {"executor1"})
        for absent in ("lead1", "reviewer1", "supervisor"):
            self.assertNotIn(absent, rendered)
        text = self.render(repo)
        rows = [l for l in text.splitlines() if "recovery=" in l]
        self.assertEqual(
            len(rows), 1,
            "a row was rendered for a role with no recorded turn: %s"
            % rows,
        )

    def test_the_OMISSION_is_DISCLOSED_and_names_the_roles(self):
        """A row that silently disappeared would trade one invisible
        problem for another."""
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(
            sorted(obs["turns"]["omitted_roles"]),
            ["lead1", "reviewer1", "supervisor"],
        )
        text = self.render(repo)
        self.assertIn("no turn recorded for", text)
        self.assertIn("OMITTED rather than shown as healthy", text)

    def test_NO_ROLE_ROWS_render_without_a_task_SCOPE(self):
        """With no current task there is no scope to select turns
        within, so no role state renders at all — the omission branch
        when it is the ONLY branch."""
        temp, repo = herd_with({
            "runtime.json": {"agents": self.ROLES, "panes": {}},
        })
        self.addCleanup(temp.cleanup)
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["roles"], [])
        self.assertEqual(obs["turns"]["omitted_roles"], [])
        text = observe.render_observation(obs)
        self.assertNotIn("recovery=", text)

    def test_a_role_ABSENT_from_runtime_is_never_invented(self):
        """The domain is the runtime's own agent mapping. A role that
        is not part of this herd gets no row, no omission line, and no
        invented turn."""
        repo = self.herd(roles={"supervisor": "h-sup"})
        self.observe_pass(repo, {"h-lead1": "working"})
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(
            [r["logical"] for r in obs["turns"]["roles"]], [],
        )
        self.assertEqual(obs["turns"]["omitted_roles"], ["supervisor"])

    def test_role_state_HAS_A_PRODUCTION_CALLER(self):
        """The decision itself, pinned. `role_state` had six call
        sites and all six were tests; the defect was reachability,
        and this asserts the reach EXISTS by driving the surface —
        not by scanning source, which R-8 forbids as the load-bearing
        pin and which passes on a call that is unreachable.
        """
        repo = self.herd()
        self.observe_pass(repo, {"h-sup": "working"})
        obs = observe.observe(repo, probe_agents=False)
        self.assertTrue(
            obs["turns"]["roles"],
            "no per-role state reached the observation, so"
            " `role_state` is still unreachable from production",
        )


class CaveatSlotTests(unittest.TestCase):
    """THE QUESTION I6b IS AIMED AT: can `omitted_roles` ever become a
    CAVEAT SLOT?

    The original defect was not that `[available]` was wrong. It was
    that it rendered AS A PROPERTY OF THE THING DESCRIBED, in a field
    a reader resolves — "readable" written, "current" read. A caveat
    slot is a position in the output that a reader fills in.

    `omitted_roles` NAMES roles. A name is empty of anything to resolve, which is the safe shape. These tests attack the ways it could stop
    being that shape: a describing value reaching a row, a second
    element appearing beside the name, a role described AND omitted,
    or the omission line acquiring status vocabulary.
    """

    ROLES = {"supervisor": "h-sup", "lead1": "h-lead1",
             "executor1": "h-exec1", "reviewer1": "h-rev1"}

    def herd(self):
        temp, repo = herd_with({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "runtime.json": {"agents": self.ROLES, "panes": {}},
        })
        self.addCleanup(temp.cleanup)
        return repo

    def observe_pass(self, repo, statuses):
        return turns.observe_control_roles(
            repo / ".herd", self.ROLES, CURRENT,
            lambda agent: {"status": statuses.get(agent, "idle"),
                           "raw": None},
        )

    def test_the_CAVEAT_VALUE_never_reaches_the_SURFACE(self):
        """`no_turn_recorded` is the value with the dangerous shape:
        in a `recovery=` column a reader resolves it to a state the
        role is IN. Within this surface it is absent from the output entirely."""
        repo = self.herd()
        for statuses in (
            {},
            {"h-exec1": "working"},
            {"h-exec1": "working", "h-lead1": "working"},
            {"h-exec1": "missing"},
        ):
            self.observe_pass(repo, statuses)
            text = observe.render_observation(
                observe.observe(repo, probe_agents=False)
            )
            self.assertNotIn(
                "no_turn_recorded", text,
                "the caveat value reached the surface for %s; in a"
                " status column a reader resolves it" % statuses,
            )

    def test_omitted_roles_contains_ONLY_BARE_ROLE_NAMES(self):
        """A name is empty of anything to resolve. A pair, a dict or
        a description would put something in the slot."""
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        obs = observe.observe(repo, probe_agents=False)
        omitted = obs["turns"]["omitted_roles"]
        self.assertTrue(omitted)
        for entry in omitted:
            self.assertIsInstance(entry, str)
            self.assertIn(entry, self.ROLES)

    def test_a_role_is_NEVER_both_described_and_omitted(self):
        """Being in both lists is the state that would let a reader
        take the omission as a qualifier ON the description — which is
        precisely a caveat."""
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working",
                                 "h-lead1": "working"})
        obs = observe.observe(repo, probe_agents=False)
        described = {r["logical"] for r in obs["turns"]["roles"]}
        omitted = set(obs["turns"]["omitted_roles"])
        self.assertEqual(described & omitted, set())
        self.assertEqual(described | omitted, set(self.ROLES))

    def test_the_OMISSION_LINE_carries_NO_STATUS_VOCABULARY(self):
        """The line names roles and says they are omitted. If it
        acquired a recovery word, that word becomes the slot."""
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        text = observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )
        line, = [l for l in text.splitlines()
                 if "no turn recorded for" in l]
        for word in ("none", "in_flight", "needs_recovery",
                     "blocked_needs_decision", "healthy", "ok",
                     "available", "fresh"):
            self.assertNotIn(
                word, line.replace("shown as healthy", ""),
                "the omission line carries %r, which a reader"
                " resolves as a state" % word,
            )
        self.assertIn("lead1", line)
        self.assertIn("OMITTED", line)

    def test_the_RENDERABLE_SET_is_an_ALLOWLIST(self):
        """CONSTRUCTION: a recovery value added later renders only if
        it is added to the allowlist deliberately, so within this gate
        a new value stays out of a reader-resolvable column."""
        from unittest.mock import patch
        repo = self.herd()
        self.observe_pass(repo, {"h-exec1": "working"})
        with patch.object(
            observe.turns_module if hasattr(observe, "turns_module")
            else turns, "role_state",
            return_value={"logical": "executor1", "current": None,
                          "last_outcome": "completed",
                          "last_cause": None,
                          "recovery": "a-state-invented-later"},
        ):
            obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(
            obs["turns"]["roles"], [],
            "an unrecognised recovery value rendered a row; the"
            " allowlist is behaving as a denylist",
        )
        self.assertEqual(
            sorted(obs["turns"]["omitted_roles"]), sorted(self.ROLES),
        )

    def test_a_ROW_carries_the_ROLE_and_a_RECOVERY_a_reader_can_act_on(self):
        """The CARRY branch, tested as hard: omission is not achieved
        by rendering a row empty of anything useful."""
        repo = self.herd()
        self.observe_pass(repo, {"h-lead1": "working"})
        self.observe_pass(repo, {"h-lead1": "missing"})
        text = observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )
        row, = [l for l in text.splitlines()
                if "recovery=needs_recovery" in l]
        self.assertIn("lead1", row)
        self.assertIn("failed_transport", row)
        self.assertIn("could not be reached", row)


class UnboundRoleSurfaceTests(unittest.TestCase):
    """R-61 AY-4 completed: "which roles are bound" is unanswered
    while the UNBOUND ones are invisible.

    R-53's finding was that an unbound role must not read as healthy.
    A surface that lists three bound roles and is silent about the
    fourth lets a reader count three and stop.
    """

    ROLES = {"supervisor": "h-sup", "lead1": "h-lead1",
             "executor1": "h-exec1", "reviewer1": "h-rev1"}

    def herd(self, bound):
        temp, repo = herd_with({
            "task.json": {"id": CURRENT, "status": "ACTIVE"},
            "runtime.json": {"agents": self.ROLES, "panes": {}},
        })
        self.addCleanup(temp.cleanup)
        identity.save_bindings(repo / ".herd", {
            "version": 1,
            "roles": {
                logical: {
                    "logical": logical,
                    "agent": self.ROLES.get(logical, logical),
                    "session": "sess-" + logical,
                    "stable": {
                        "name": self.ROLES.get(logical, logical),
                        "cwd": "/repo",
                        "workspace_id": "wT",
                        "pane_id": "wT:p1",
                    },
                } for logical in bound
            },
        })
        return repo

    def test_a_role_with_NO_BINDING_is_NAMED(self):
        repo = self.herd(bound=["supervisor", "lead1"])
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(
            sorted(obs["roles"]["unbound_roles"]),
            ["executor1", "reviewer1"],
        )
        text = observe.render_observation(obs)
        self.assertIn("UNBOUND in this herd: executor1, reviewer1",
                      text)

    def test_an_unbound_role_is_NAMED_and_NOT_DESCRIBED(self):
        """Same shape as `omitted_roles`: a name, with no value beside
        it for a reader to resolve into an identity."""
        repo = self.herd(bound=["supervisor"])
        obs = observe.observe(repo, probe_agents=False)
        for entry in obs["roles"]["unbound_roles"]:
            self.assertIsInstance(entry, str)
        text = observe.render_observation(obs)
        line, = [l for l in text.splitlines() if "UNBOUND in this herd" in l]
        for word in ("complete", "partial", "identity="):
            self.assertNotIn(word, line)

    def test_an_unbound_role_is_DIAGNOSED(self):
        """R-12: surfaced, not merely stored."""
        repo = self.herd(bound=["supervisor"])
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(len([
            d for d in obs["diagnostics"]
            if d.get("source") == "roles"
            and "NO recorded binding" in (d.get("detail") or "")
        ]), 3)

    def test_ALL_ROLES_BOUND_names_NOBODY(self):
        """The omission line is absent when no role is omitted — within
        this render it does not become a permanent fixture reading as
        a warning."""
        repo = self.herd(bound=list(self.ROLES))
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["roles"]["unbound_roles"], [])
        self.assertNotIn("UNBOUND in this herd",
                         observe.render_observation(obs))

    def test_a_role_bound_but_ABSENT_from_the_herd_is_not_invented(self):
        """The domain is the runtime's agent mapping."""
        repo = self.herd(bound=list(self.ROLES) + ["ghost1"])
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["roles"]["unbound_roles"], [])
        self.assertIn("ghost1",
                      [r["logical"] for r in obs["roles"]["listed"]])


class ObserverBuildSkewTests(unittest.TestCase):
    """Version skew between the RUNNING process and the code on disk.

    THE SPECIMEN IS THIS INCREMENT'S OWN, and it happened while I6 was
    being written: for eight minutes `herdr/heartbeat.py` on disk had
    the observer wired and the RUNNING controller was a process
    started before that edit. The surface said one thing, the running
    system did another, and no instrument this mission has built —
    diffs, snapshots, AST walks, the census, mine included — reported
    the disagreement. A durable claim that depends on a running
    process has to say which BUILD made it.
    """

    def herd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / ".herd"
        (root / "state").mkdir(parents=True)
        return root

    def test_a_new_turn_RECORDS_the_build_that_made_it(self):
        record = turns.new_turn("t-1", CURRENT, "lead1", now=1)
        self.assertEqual(record["observer_build"],
                         turns.observer_build())
        self.assertTrue(record["observer_build"])

    def test_the_build_CHANGES_when_the_deciding_code_changes(self):
        """NOT VACUOUS: the fingerprint has to move when the logic
        that derives outcomes moves, or within this check it certifies
        nothing."""
        from unittest.mock import patch
        first = turns.observer_build()
        real_read = Path.read_bytes

        def altered(self, *args, **kwargs):
            data = real_read(self, *args, **kwargs)
            if self.name == "turns.py":
                return data + b"\n# a change to the deciding code\n"
            return data

        with patch.object(Path, "read_bytes", altered):
            second = turns.observer_build()
        self.assertNotEqual(
            first, second,
            "the build fingerprint did not move when the code that"
            " derives outcomes moved; it would certify a claim made by"
            " different logic as made by this one",
        )

    def test_an_UNREADABLE_source_yields_an_UNKNOWN_build(self):
        """An unknown build is recorded as unknown rather than as a
        match — the same fail-closed direction the rest of this
        increment takes."""
        from unittest.mock import patch
        with patch.object(Path, "read_bytes",
                          side_effect=OSError("gone")):
            self.assertIsNone(turns.observer_build())

    def test_the_SURFACE_reports_a_record_from_ANOTHER_build(self):
        """Driven through `observe`: the requirement is that a reader
        SEES the disagreement, not that the record contains it."""
        temp, repo = herd_with({"task.json": {"id": CURRENT,
                                              "status": "ACTIVE"}})
        self.addCleanup(temp.cleanup)
        stale = turns.new_turn("t-old", CURRENT, "lead1", now=1)
        stale["observer_build"] = "0000deadbeef"
        turns.append_turn(repo / ".herd", stale)
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["skewed"], ["0000deadbeef"])
        text = observe.render_observation(obs)
        self.assertIn("BUILD SKEW", text)
        self.assertIn("0000deadbeef", text)

    def test_a_record_from_THIS_build_reports_NO_skew(self):
        temp, repo = herd_with({"task.json": {"id": CURRENT,
                                              "status": "ACTIVE"}})
        self.addCleanup(temp.cleanup)
        turns.append_turn(
            repo / ".herd",
            turns.new_turn("t-now", CURRENT, "lead1", now=1),
        )
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["skewed"], [])
        self.assertNotIn("BUILD SKEW",
                         observe.render_observation(obs))

    def test_a_record_with_NO_build_reports_skew_as_UNKNOWN(self):
        """Records written before this existed carry no build. They
        are reported as skew rather than assumed current: within this
        surface a claim silent about its build is no evidence that
        this build made it."""
        temp, repo = herd_with({"task.json": {"id": CURRENT,
                                              "status": "ACTIVE"}})
        self.addCleanup(temp.cleanup)
        old = turns.new_turn("t-ancient", CURRENT, "lead1", now=1)
        del old["observer_build"]
        turns.append_turn(repo / ".herd", old)
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["skewed"], [None])
        self.assertIn("unknown", observe.render_observation(obs))


class HeartbeatProducerTests(unittest.TestCase):
    """R-63 BA-4: the PRODUCTION path writes, so `turns.json` is
    non-empty in normal operation.

    The heartbeat controller is the producer because it is the one
    process that survives a turn's death. These drive
    `heartbeat_once`, not the observer directly — a unit test of the
    observer proves it works while leaving open whether anything runs
    it, which is the gap R-28 named and R-63 is the sixth recurrence
    of.
    """

    def herd(self, status="idle"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / ".herd" / "state").mkdir(parents=True)
        (repo / ".herd" / "state" / "runtime.json").write_text(
            json.dumps({"agents": {
                "supervisor": "h-sup", "lead1": "h-lead1",
                "executor1": "h-exec1", "reviewer1": "h-rev1",
            }, "panes": {}})
        )
        (repo / ".herd" / "state" / "task.json").write_text(
            json.dumps({"id": CURRENT, "status": "ACTIVE"})
        )
        return repo

    def run_once(self, repo, statuses, prompt_rc=0):
        from unittest.mock import patch
        from types import SimpleNamespace
        from herdr import heartbeat
        from herdr.instance import HerdrInstance
        with patch.object(
            heartbeat, "agent_info",
            side_effect=lambda a: {"status": statuses.get(a, "idle"),
                                   "raw": None},
        ), patch.object(
            heartbeat, "prompt",
            return_value=SimpleNamespace(returncode=prompt_rc,
                                         stdout="", stderr=""),
        ):
            return heartbeat.heartbeat_once(HerdrInstance(repo))

    def test_the_HEARTBEAT_writes_turn_records(self):
        """THE PRODUCER, driven. Before this the store had no writer
        and the surface could only ever answer 'no turn failed'."""
        repo = self.herd()
        self.run_once(repo, {"h-exec1": "working", "h-sup": "idle"})
        stored = turns.load_turns(repo / ".herd")["turns"]
        self.assertTrue(
            stored,
            "the production path recorded NOTHING; a surface fed by"
            " nothing reports 'no failures' forever",
        )
        self.assertIn("executor1", [t["logical"] for t in stored])

    def test_the_HEARTBEAT_derives_a_TRANSPORT_FAILURE(self):
        """End to end through production: a role dies between passes
        and the controller — which survived — records the epitaph."""
        repo = self.herd()
        self.run_once(repo, {"h-lead1": "working", "h-sup": "idle"})
        self.run_once(repo, {"h-lead1": "missing", "h-sup": "idle"})
        document = turns.load_turns(repo / ".herd")
        state = turns.role_state(document, "lead1")
        self.assertEqual(state["last_outcome"],
                         turns.TURN_FAILED_TRANSPORT)
        self.assertIn("could not be reached", state["last_cause"])

    def test_a_SKIPPED_heartbeat_still_OBSERVES(self):
        """A skipped prompt is not a reason to stop observing — the
        role that died is exactly the one that makes the heartbeat
        skip."""
        repo = self.herd()
        self.run_once(repo, {"h-exec1": "working", "h-sup": "working"})
        outcome = self.run_once(
            repo, {"h-exec1": "missing", "h-sup": "working"}
        )
        self.assertEqual(outcome, "skipped")
        document = turns.load_turns(repo / ".herd")
        self.assertEqual(
            turns.role_state(document, "executor1")["last_outcome"],
            turns.TURN_FAILED_TRANSPORT,
        )

    def test_ROUTED_and_EFFECT_OBSERVED_are_recorded_SEPARATELY(self):
        """AS-4 through production. The prompt is accepted — that is
        ROUTED. The effect is observed on a LATER pass, and only when
        the supervisor's own change counter has moved."""
        from unittest.mock import patch
        repo = self.herd()
        from herdr import heartbeat
        with patch.object(heartbeat, "_state_change_seq",
                          return_value=5):
            self.run_once(repo, {"h-sup": "idle"})
        document = turns.load_turns(repo / ".herd")
        routed = [t for t in document["turns"]
                  if t["logical"] == "supervisor-heartbeat"]
        self.assertEqual(len(routed), 1)
        self.assertTrue(routed[0]["routed_at"])
        self.assertFalse(
            turns.delivered(routed[0]),
            "the heartbeat counted as delivered on the strength of"
            " having been sent",
        )
        # A pass where the counter has NOT moved leaves it undelivered.
        with patch.object(heartbeat, "_state_change_seq",
                          return_value=5):
            self.run_once(repo, {"h-sup": "idle"})
        document = turns.load_turns(repo / ".herd")
        still = [t for t in document["turns"]
                 if t["logical"] == "supervisor-heartbeat"][0]
        self.assertFalse(turns.delivered(still))
        # It moves: the EFFECT is observed.
        with patch.object(heartbeat, "_state_change_seq",
                          return_value=9):
            self.run_once(repo, {"h-sup": "idle"})
        document = turns.load_turns(repo / ".herd")
        done = [t for t in document["turns"]
                if t["logical"] == "supervisor-heartbeat"][0]
        self.assertTrue(turns.delivered(done))
        self.assertEqual(done["outcome"], turns.TURN_COMPLETED)

    def test_an_OBSERVATION_FAILURE_does_not_stop_the_heartbeat(self):
        """The observation is EVIDENCE, not control. Within this
        controller it must not stop the thing that keeps the herd
        alive, and it must not pass silently either."""
        from unittest.mock import patch
        repo = self.herd()
        with patch.object(turns, "observe_control_roles",
                          side_effect=RuntimeError("probe exploded")):
            outcome = self.run_once(repo, {"h-sup": "idle"})
        self.assertEqual(outcome, "ok")


class TurnsSurfaceTests(unittest.TestCase):
    """The turn outcomes reach a surface, task-scoped."""

    def herd(self, task=CURRENT):
        temp, repo = herd_with(
            {"task.json": {"id": task, "status": "ACTIVE"}}
            if task else {}
        )
        self.addCleanup(temp.cleanup)
        return repo

    def test_a_FAILED_turn_appears_with_its_CAUSE(self):
        repo = self.herd()
        turns.append_turn(repo / ".herd", turns.close(
            turns.new_turn("t-9", CURRENT, "executor1", now=100),
            turns.TURN_FAILED_TRANSPORT,
            cause="the control message was accepted and never acted on",
            now=200,
        ))
        text = observe.render_observation(
            observe.observe(repo, probe_agents=False)
        )
        self.assertIn("failed_transport=1", text)
        self.assertIn("never acted on", text)

    def test_ANOTHER_tasks_turns_are_NOT_counted(self):
        repo = self.herd()
        turns.append_turn(repo / ".herd", turns.close(
            turns.new_turn("t-old", PRIOR, "executor1", now=1),
            turns.TURN_BLOCKED, cause="prior task", now=2,
        ))
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["counts"]["blocked"], 0)
        self.assertEqual(obs["turns"]["state"], "empty")

    def test_NO_current_task_means_NO_SCOPE_not_NO_TURNS(self):
        """An empty result is a QUESTION. Here the question is whether
        the selector could run at all, and the answer is recorded."""
        repo = self.herd(task=None)
        turns.append_turn(repo / ".herd", turns.close(
            turns.new_turn("t-x", PRIOR, "executor1", now=1),
            turns.TURN_BLOCKED, cause="prior", now=2,
        ))
        obs = observe.observe(repo, probe_agents=False)
        self.assertEqual(obs["turns"]["state"], "unavailable")
        self.assertTrue([
            d for d in obs["diagnostics"]
            if d.get("source") == "turns"
            and "absent SELECTOR" in (d.get("detail") or "")
        ], obs["diagnostics"])


if __name__ == "__main__":
    unittest.main()
