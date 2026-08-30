"""I4: the dispatch-identity and reconciliation re-audit.

An AUDIT increment: each dirty-tree correction is a HYPOTHESIS TO
FALSIFY. So this module is organised by what was ATTEMPTED, not by what
was concluded —

- `FalsifiedTests` hold the attempts that SUCCEEDED in breaking
  something. Each one failed before the fix in this increment and
  passes after it.
- `SurvivedFalsificationTests` hold the attempts that FAILED to break
  anything. They are kept, and run, because "I checked and it was
  fine" is not evidence: the executed attempt is.

The contract tests drive the REAL writer — `HerdrControlPlane.spawn_child`
— rather than a hand-built dict, so the field domain comes from the
dependency's own vocabulary. Only the innermost `spawn` is replaced,
because the record-building code under audit sits BETWEEN `spawn_child`'s
entry and that call; replacing anything higher would test the double.
No agent, herd, or process is started by this module.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from herdr.control_plane import HerdrControlPlane      # noqa: E402
from herdr.instance import HerdrInstance               # noqa: E402
from herdr.observe import observe_spawn_records        # noqa: E402
from target_runtime import broker as broker_module     # noqa: E402
from workflow_authority import record as wa_record     # noqa: E402

from test_target_runtime import (                      # noqa: E402
    I5ReconcileTests, RuntimeCase, TARGET_TASK_ID,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def projection_fixture(case):
    """A committed one-child specimen in an isolated temporary herd."""
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    repo = Path(temp.name)
    state = repo / ".herd" / "state"
    state.mkdir(parents=True)
    child_repo = repo / "managed-child"
    (state / "children.json").write_text(json.dumps({
        "version": 1,
        "children": [{
            "parent_task_id": None,
            "dependency": False,
            "repo": str(child_repo),
            "task_id": "20260828-114612-5d92e1",
            "task_status": "ACTIVE",
        }],
    }))
    return observe_spawn_records(repo)


def broker_source():
    """`target_runtime/broker.py` as text, read and closed."""
    with open(os.path.join(REPO_ROOT, "target_runtime", "broker.py"),
              encoding="utf-8") as handle:
        return handle.read()

#: The task identity a patched `spawn` reports, shaped like the real
#: `dispatch_task` result the writer reads (`task_state.get("id")`).
SPAWN_TASK = {"id": "20260828-114612-5d92e1", "status": "ACTIVE"}


class WriterFixture(unittest.TestCase):
    """An initialized, "running" parent Herdr in a temp directory, and
    a child target directory — the minimum `spawn_child` requires
    before it reaches the record-building code under audit."""

    def parent_repo(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove, base)
        parent = base / "parent"
        state = parent / ".herd" / "state"
        state.mkdir(parents=True)
        (parent / ".herd" / "herd.config.json").write_text(
            json.dumps({"version": 4})
        )
        # `spawn_child` refuses a parent that is not running.
        (state / "runtime.json").write_text(
            json.dumps({"agents": {}, "panes": {}})
        )
        return parent

    def target_repo(self, parent):
        target = parent.parent / "target"
        target.mkdir()
        return target

    @staticmethod
    def _remove(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def spawn_child_with(self, spawn_result, target_argument=None,
                         parent=None, target=None):
        """Run the REAL `spawn_child` with only the innermost `spawn`
        replaced. Returns the result dict it produced."""
        parent = parent or self.parent_repo()
        target = target if target is not None else self.target_repo(parent)
        plane = HerdrControlPlane()
        with patch.object(HerdrControlPlane, "spawn") as spawn:
            spawn.return_value = spawn_result
            result = plane.spawn_child(
                str(parent),
                target_argument if target_argument is not None
                else str(target),
                task="do the thing",
            )
        return result

    def resolved(self, path):
        return str(Path(path).expanduser().resolve())


class SurvivedFalsificationTests(WriterFixture):
    """Attempts that FAILED to break something. Each documents what was
    tried, so the conclusion rests on the executed attempt rather than
    on a reading."""

    def test_the_repo_fallback_is_ALSO_resolved(self):
        """ATTEMPT: reach the `result.get("repo", str(target))`
        fallback with an UNRESOLVED path argument, and catch the writer
        persisting that raw spelling.

        OUTCOME: survived. The audit brief expected `str(target)` to be
        the raw `target_repo` argument, but `spawn_child` rebinds
        `target = Path(target_repo).expanduser().resolve()` before the
        record is built, so the fallback is resolved too. Driven here
        with a `spawn` result carrying NO `repo` key at all, which is
        the only way to reach that branch.
        """
        parent = self.parent_repo()
        target = self.target_repo(parent)
        # An unresolved spelling of the same directory: a `..` hop.
        unresolved = str(target.parent / "target" / ".." / "target")
        self.assertNotEqual(unresolved, self.resolved(target))
        result = self.spawn_child_with(
            {"task": SPAWN_TASK},           # no "repo" key: the fallback
            target_argument=unresolved,
            parent=parent, target=target,
        )
        self.assertEqual(
            result["child_record"]["repo"], self.resolved(target),
            "the fallback persisted an unresolved path; the raw"
            " comparison in reconciliation would then have missed it",
        )

    def test_the_primary_repo_value_is_resolved(self):
        """ATTEMPT: same, through the PRIMARY branch — a `spawn` result
        that does carry `repo`. OUTCOME: survived; the value written is
        whatever `spawn` reported, and the real `spawn` reports
        `str(herd.repo)`, which `HerdrInstance` resolves."""
        parent = self.parent_repo()
        target = self.target_repo(parent)
        result = self.spawn_child_with(
            {"repo": self.resolved(target), "task": SPAWN_TASK},
            parent=parent, target=target,
        )
        self.assertEqual(
            result["child_record"]["repo"], self.resolved(target)
        )

    def test_HerdrInstance_resolves_its_repo(self):
        """The property the PRIMARY branch depends on, driven against
        the real class rather than assumed: `spawn` reports
        `str(herd.repo)`, and `HerdrInstance.repo` is resolved."""
        base = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove, base)
        inner = base / "a" / ".." / "a"
        (base / "a").mkdir(parents=True)
        self.assertEqual(
            str(HerdrInstance(str(inner)).repo),
            str((base / "a").resolve()),
        )

    def test_task_id_and_child_record_task_id_agree(self):
        """ATTEMPT: make the "two sources" disagree by driving the real
        writer. OUTCOME: survived, and the reason MATTERS —

        they are not two sources. `task_state = result.get("task")` and
        `record["task_id"] = task_state.get("id")`, with
        `result["task"]` left in place, so both readings come from ONE
        object. Within this writer they are incapable of disagreeing,
        which means `target_identity_from_spawn`'s agreement check has
        less independent strength than its name suggests: it defends
        against a DIFFERENT bridge or a mutated result, not against
        this one.
        """
        result = self.spawn_child_with({"repo": "/x", "task": SPAWN_TASK})
        self.assertEqual(
            result["task"]["id"], result["child_record"]["task_id"]
        )
        # The two readings are the same STRING taken from the same
        # dict, so no input to this writer can separate them: the
        # record's value is `result["task"]["id"]` read once.
        self.assertIs(
            result["task"]["id"], result["child_record"]["task_id"]
        )

    def test_a_task_without_an_id_yields_no_identity_not_a_wrong_one(self):
        """ATTEMPT: get a fabricated identity out of a `spawn` result
        whose task carries no id. OUTCOME: survived — both readings are
        None, and `target_identity_from_spawn` maps that to the
        unresolved sentinel rather than inventing one."""
        from target_runtime import dispatch as dispatch_module
        result = self.spawn_child_with({"repo": "/x", "task": {}})
        self.assertIsNone(result["child_record"]["task_id"])
        identity = dispatch_module.target_identity_from_spawn(
            result, {"workflow_id": "wf-0001",
                     "target": {"canonical_url": "u"}}, 1,
        )
        self.assertEqual(
            identity["task_id"], dispatch_module.UNRESOLVED_TASK_ID
        )

    def test_the_COMMITTED_specimen_projects_as_lead1_reported(self):
        """ATTEMPT: falsify the reported projection shape hermetically.

        Historical `.herd/state/children.json` is live machine evidence, not
        a unit-test fixture.  The same reported one-child shape is constructed
        in an isolated herd so a clean clone and a populated authoring herd
        drive identical assertions.
        """
        projection = projection_fixture(self)
        self.assertEqual(projection["state"], "available")
        self.assertEqual(projection["count"], 1)
        self.assertFalse(projection["truncated"])
        self.assertEqual(len(projection["listed"]), 1)
        record = projection["listed"][0]
        self.assertIsNone(record["parent_task_id"])
        self.assertEqual(record["task_id"], "20260828-114612-5d92e1")
        self.assertTrue(os.path.isabs(record["repo"]))

    def test_listed_carries_what_reconciliation_actually_reads(self):
        """The field question item 4 asks: reconciliation reads exactly
        `repo` and `task_id` from a listed record. Both are projected.

        `task_status` is projected as `recorded_status` and is
        deliberately NOT read as evidence — a control-side recorded
        status is stale by construction, and the identity proof comes
        from the LEASED workspace's own observation instead.

        The second half of this test reads `broker.py` through
        `broker_source()`, so it is fast structural feedback in front
        of `ProductionRecoveryPathTests.test_an_equivalently_spelled_record_BINDS`,
        which EXECUTES a reconciliation that binds from `repo` and
        `task_id` alone."""
        projection = projection_fixture(self)
        record = projection["listed"][0]
        for field in ("repo", "task_id"):
            self.assertIn(field, record)
        self.assertIn("recorded_status", record)
        source = broker_source()
        self.assertNotIn('candidate.get("recorded_status")', source)
        self.assertNotIn('candidate["recorded_status"]', source)


class FalsifiedTests(WriterFixture):
    """Attempts that SUCCEEDED. Each of these failed before this
    increment's change and passes after it."""

    def project(self, children):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove, base)
        state = base / ".herd" / "state"
        state.mkdir(parents=True)
        (state / "children.json").write_text(
            json.dumps({"version": 1, "children": children})
        )
        return observe_spawn_records(base)

    @staticmethod
    def record(index):
        return {
            "parent_task_id": None,
            "dependency": False,
            "repo": "/tmp/child-%d" % index,
            "task_id": "task-%d" % index,
            "task_status": "ACTIVE",
        }

    def test_a_malformed_record_no_longer_reports_a_count_it_cannot_list(self):
        """FALSIFIED (item 3): the projection asserted a complete short
        listing.

        With a malformed record at index 1 of 3, the scan broke out
        after listing one record but returned `count: 3`,
        `listed` of length 1 and `truncated: False` — a trio that says
        "three children, none omitted, here is one". The inconsistency
        was visible only to a caller that also read `state`, which is
        the recorded "silent truncation presented as fact" class.
        """
        projection = self.project([
            self.record(0), {"bogus": True}, self.record(2),
        ])
        self.assertEqual(projection["state"], "malformed")
        self.assertIsNone(
            projection["count"],
            "a projection that could not complete its scan still"
            " asserted an exact count",
        )
        self.assertEqual(projection["listed"], [])
        self.assertFalse(projection["truncated"])
        self.assertIsNotNone(projection["detail"])

    def test_the_unreadable_path_already_behaved_this_way(self):
        """The fix above matches the contract the `unreadable` path
        already had — count None, empty listing — rather than inventing
        one."""
        base = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove, base)
        state = base / ".herd" / "state"
        state.mkdir(parents=True)
        (state / "children.json").write_text("{not json")
        projection = observe_spawn_records(base)
        self.assertEqual(projection["state"], "malformed")
        self.assertIsNone(projection["count"])
        self.assertEqual(projection["listed"], [])

    def test_a_malformed_record_beyond_the_cap_reports_nothing_partial(self):
        """The same defect at its worst: a malformed record AFTER the
        listing cap, where the old return carried a full 32-entry
        listing and a count, both from an incomplete scan."""
        children = [self.record(i) for i in range(40)]
        children[35] = {"bogus": True}
        projection = self.project(children)
        self.assertEqual(projection["state"], "malformed")
        self.assertIsNone(projection["count"])
        self.assertEqual(projection["listed"], [])

    def test_the_truncation_boundary_is_exact_at_the_cap(self):
        """FALSIFIED — but the thing falsified was the TEST COVERAGE,
        not the shipped code.

        Mutant N06 flipped `len(records) > _OBSERVE_MAX_CHILDREN` to
        `>=` and SURVIVED the suite: nothing exercised the boundary at
        exactly the cap. The consequence of getting it wrong is not
        cosmetic. A listing of exactly 32 children has omitted no
        record, yet a `>=` reports `truncated: True`, and
        `Broker._reconcile`
        refuses a truncated listing with PROBLEM_RECONCILE_TRUNCATED
        and a durable block — so a COMPLETE listing would strand a
        recoverable dispatch, the same failure direction as item 2.

        Both sides of the boundary are driven here.
        """
        from herdr.observe import _OBSERVE_MAX_CHILDREN
        at_cap = self.project(
            [self.record(i) for i in range(_OBSERVE_MAX_CHILDREN)]
        )
        self.assertEqual(at_cap["state"], "available")
        self.assertEqual(at_cap["count"], _OBSERVE_MAX_CHILDREN)
        self.assertEqual(len(at_cap["listed"]), _OBSERVE_MAX_CHILDREN)
        self.assertFalse(
            at_cap["truncated"],
            "a listing that omitted nothing reported truncation;"
            " reconciliation would block a complete listing",
        )
        self.assertIsNone(at_cap["detail"])

        over_cap = self.project(
            [self.record(i) for i in range(_OBSERVE_MAX_CHILDREN + 1)]
        )
        self.assertTrue(over_cap["truncated"])
        self.assertEqual(over_cap["count"], _OBSERVE_MAX_CHILDREN + 1)
        self.assertEqual(
            len(over_cap["listed"]), _OBSERVE_MAX_CHILDREN
        )

    def test_the_refusal_message_no_longer_claims_a_comparison_it_did_not_make(self):
        """FALSIFIED (item 2, the operator-facing half): the no-match
        refusal read "exact realpath comparison", while the code
        realpath'd the lease side and raw-compared the record side.

        Source is the only feasible level for THIS assertion and the
        reason is that its subject is the TEXT of a message; the
        BEHAVIOUR the message now describes truthfully is driven by
        `test_an_equivalent_path_spelling_now_matches` below.
        """
        source = broker_source()
        self.assertNotIn("exact realpath comparison over", source)
        self.assertIn("realpath comparison on both sides over", source)
        index = source.index("realpath comparison on both sides over")
        window = source[max(0, index - 1200):index]
        self.assertIn(
            'os.path.realpath(candidate["repo"]) == lease_real', window,
            "the message claims a both-sides comparison that the code"
            " above it does not perform",
        )


class EquivalentSpellingTests(unittest.TestCase):
    """FALSIFIED (item 2, the behavioural half), driven directly on the
    matching predicate the reconciliation uses.

    The failure DIRECTION is the point. A missed match is fail-closed
    and therefore safe, but it yields PROBLEM_RECONCILE_NO_MATCH and a
    durable block, converting a RECOVERABLE dispatch into a permanent
    stranding — the dead-end class this task exists to close.
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove, self.base)
        (self.base / "lease").mkdir()

    @staticmethod
    def _remove(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def matching(listed, lease_real):
        """The predicate as reconciliation applies it."""
        return [
            candidate for candidate in listed
            if isinstance(candidate, dict)
            and isinstance(candidate.get("repo"), str)
            and os.path.realpath(candidate["repo"]) == lease_real
        ]

    def test_an_equivalent_path_spelling_now_matches(self):
        lease_real = os.path.realpath(str(self.base / "lease"))
        spelling = str(self.base / "lease" / ".." / "lease")
        self.assertNotEqual(spelling, lease_real)
        self.assertEqual(
            len(self.matching([{"repo": spelling}], lease_real)), 1,
            "a record naming the SAME directory by another spelling"
            " did not match; that no-match is a durable block on a"
            " recoverable dispatch",
        )

    def test_a_different_directory_still_does_not_match(self):
        """Within this predicate, resolving both sides adds matches
        only between spellings of the SAME file: a genuinely different
        directory stays unmatched, which is the boundary that keeps
        the change from binding a workflow to the wrong workspace."""
        (self.base / "other").mkdir()
        lease_real = os.path.realpath(str(self.base / "lease"))
        self.assertEqual(
            self.matching([{"repo": str(self.base / "other")}], lease_real),
            [],
        )

    def test_a_non_string_repo_is_still_skipped(self):
        lease_real = os.path.realpath(str(self.base / "lease"))
        self.assertEqual(
            self.matching(
                [{"repo": None}, {"repo": 7}, {}], lease_real
            ),
            [],
        )

    def test_the_predicate_is_what_the_broker_runs(self):
        """Anti-vacuity: the predicate above is asserted to be the one
        in `broker.py`, so a change there that diverged from this class
        fails here rather than leaving these tests measuring a copy.

        This reads source through `broker_source()`, and it is fast
        structural feedback in front of
        `ProductionRecoveryPathTests.test_an_equivalently_spelled_record_BINDS`
        and `test_a_different_workspace_still_does_not_bind`, which
        drive the real `ACTION_RECONCILE` and would change OUTCOME if
        the broker's predicate reverted."""
        source = broker_source()
        self.assertIn(
            'and os.path.realpath(candidate["repo"]) == lease_real',
            source,
        )


class ProductionRecoveryPathTests(RuntimeCase):
    """ITEM 5 and the behavioural half of ITEM 2, driven END TO END
    through the REAL `Broker._reconcile` rather than through a copy of
    its predicate.

    `EquivalentSpellingTests` above reimplements the matching
    expression, which makes it a measurement of a copy — mutant N01
    died there only by the anti-vacuity source assertion. This class
    closes that: it drives the actual broker action, so restoring the
    raw comparison changes the OUTCOME of a real reconciliation.

    The four fixture helpers are BORROWED from `I5ReconcileTests`
    rather than copied, and this class does not inherit from it, so
    its tests are not re-run here.
    """

    unresolved_dispatched = I5ReconcileTests.unresolved_dispatched
    lease_real = I5ReconcileTests.lease_real
    child = I5ReconcileTests.child
    reconcile = I5ReconcileTests.reconcile

    def equivalent_spelling(self, workflow_id="wf-0001"):
        """A different, equivalent spelling of the leased workspace —
        a `..` hop through the same directory."""
        real = self.lease_real(workflow_id)
        parent, name = os.path.split(real)
        return os.path.join(parent, name, "..", name)

    def test_an_equivalently_spelled_record_BINDS(self):
        self.unresolved_dispatched(children=lambda: [
            self.child(repo=self.equivalent_spelling())
        ])
        spawns_before = len(self.spawn_requests)
        outcome = self.reconcile()
        self.assertTrue(
            outcome.ok,
            "a child record naming the leased workspace by an"
            " equivalent spelling did not bind (%s: %s); that no-match"
            " is a durable block on a RECOVERABLE dispatch"
            % (outcome.problem, outcome.detail),
        )
        self.assertEqual(
            outcome.outcome, broker_module.OUTCOME_RECONCILED
        )
        reloaded = self.fresh_workflows()["workflows"]["wf-0001"]
        wa_record.validate_record(reloaded)
        self.assertEqual(
            reloaded["target_engine"]["task_id"], TARGET_TASK_ID
        )
        self.assertEqual(
            len(self.spawn_requests), spawns_before,
            "reconciliation spawned something; it is evidence-only",
        )

    def test_a_different_workspace_still_does_not_bind(self):
        """Within this action, resolving both sides adds matches only
        between spellings of the SAME directory: a record naming a
        different directory still fails closed, which is the boundary
        that keeps a wrong target from being bound."""
        self.unresolved_dispatched(children=lambda: [
            self.child(repo=os.path.join(self.workspaces, "elsewhere"))
        ])
        outcome = self.reconcile()
        # A reconcile block is a DURABLE STOP, not a refusal, so it
        # reports ok with a problem code — the shape the existing
        # I5 tests assert on.
        self.assertEqual(
            outcome.problem, broker_module.PROBLEM_RECONCILE_NO_MATCH
        )
        self.assertIsNone(
            self.fresh_workflows()["workflows"]["wf-0001"][
                "target_engine"
            ],
            "a failed reconciliation fabricated a target_engine",
        )

    def test_the_no_match_refusal_describes_what_it_did(self):
        """The operator-facing half, driven rather than read: the
        refusal a human acts on now names the comparison the code
        performs."""
        self.unresolved_dispatched(children=lambda: [
            self.child(repo=os.path.join(self.workspaces, "elsewhere"))
        ])
        outcome = self.reconcile()
        self.assertIn("realpath comparison on both sides", outcome.detail)

    def test_two_spellings_of_the_same_workspace_are_AMBIGUOUS(self):
        """A consequence of resolving both sides that had to be
        checked rather than assumed: two records naming the same
        workspace by different spellings now BOTH match, so the
        exactly-one requirement refuses them as ambiguous instead of
        silently binding the first. That is the correct direction —
        two spawn records for one workspace is a real ambiguity."""
        self.unresolved_dispatched(children=lambda: [
            self.child(),
            self.child(repo=self.equivalent_spelling()),
        ])
        outcome = self.reconcile()
        self.assertEqual(
            outcome.problem, broker_module.PROBLEM_RECONCILE_MULTIPLE
        )
        self.assertIsNone(
            self.fresh_workflows()["workflows"]["wf-0001"][
                "target_engine"
            ]
        )


# The executed hermetic-git sweep runs a swept module with
# `runpy.run_path(..., run_name="__main__")`. Within that runner, a
# module lacking this block imports and exits, so the shim observes no
# git process and the sweep reports having no observation to assert on.
if __name__ == "__main__":
    unittest.main()
