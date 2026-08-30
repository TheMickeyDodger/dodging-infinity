"""I3: bootstrap readiness versus engineering duration.

Each class below is named for the adversarial class it covers, and the
one this increment exists for is `EngineeringRunsForHoursTests`: the
distinction has no value if a long, healthy mission acquires a deadline
from the machinery that catches a target which failed to come up.

The seam standing in for `herdr agent list` is pinned by
`AgentListContractTests` against the REAL dependency: the field paths
and value domains come from the dependency's own output, the double is
proven to accept no more than production emits, and a forwarded value is
proven to reach its destination AND change an outcome.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from target_runtime import broker as broker_module     # noqa: E402
from target_runtime import readiness as readiness_module  # noqa: E402
from target_runtime import runtime as runtime_module   # noqa: E402
from workflow_authority import record as wa_record     # noqa: E402

from test_target_runtime import NOW, RuntimeCase       # noqa: E402


def ready_record(name="target-role", revision=1, sequence=2,
                 status="idle", ready=True):
    """One agent record shaped like a real `herdr agent list` entry,
    carrying only the fields this layer consumes."""
    return {
        "name": name,
        "interactive_ready": ready,
        "agent_status": status,
        "revision": revision,
        "state_change_seq": sequence,
    }


def all_ready(**overrides):
    probe = {
        logical: ready_record(name="target-" + logical)
        for logical in readiness_module.REQUIRED_LOGICAL_ROLES
    }
    probe.update(overrides)
    return probe


class ReadinessCase(RuntimeCase):
    """A dispatched workflow whose readiness probe this case drives."""

    def dispatched(self, workflow_id="wf-0001"):
        self.put_record(self.authorized_record(workflow_id))
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF,
                       broker_module.ACTION_DISPATCH):
            self.assertTrue(self.perform(workflow_id, action, 2).ok)
        return self.fresh_workflows()["workflows"][workflow_id]

    def set_probe(self, value):
        """Drive the injected seam. A callable is used as-is; anything
        else is returned from every call."""
        if callable(value):
            self.readiness_probe = value
        else:
            self.readiness_probe = lambda _path: value

    def entry_from_disk(self, workflow_id="wf-0001"):
        """The record read FRESH FROM DISK rather than the in-memory
        object the broker mutated, because within this suite a durable
        claim proven against an in-memory object is not a durable
        claim."""
        return self.fresh_workflows()["workflows"][workflow_id]

    def verify_at(self, now_value, workflow_id="wf-0001", revision=2):
        """One VERIFY action through a broker whose single clock reads
        `now_value`, driven the way the Runtime drives it."""
        import target_runtime.capability as capability_module
        broker = self.broker_at(now_value)
        token = capability_module.mint(
            self.store_dir, workflow_id,
            broker_module.ACTION_VERIFY, revision, now_value,
        )
        return broker.perform(
            workflow_id, broker_module.ACTION_VERIFY, revision,
            capability=token,
        )

    def readiness_states_on_disk(self, workflow_id="wf-0001"):
        return readiness_module.recorded_states(
            self.entry_from_disk(workflow_id)
        )


class BootstrapStuckTests(ReadinessCase):
    """ADVERSARIAL CLASS: bootstrap stuck BEFORE readiness.

    A target that registers no role, or only some of them, or one that
    is registered but not interactive — the shapes an unattended run,
    within its own scope, has no way to answer.
    """

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def test_no_role_registered_is_not_ready(self):
        self.dispatched()
        self.set_probe({})
        outcome = self.verify_at(NOW)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.outcome, "target_running")
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_WAITING],
        )

    def test_three_of_four_roles_is_not_ready(self):
        self.dispatched()
        probe = all_ready()
        del probe["reviewer1"]
        self.set_probe(probe)
        self.verify_at(NOW)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_WAITING],
        )

    def test_registered_but_not_interactive_is_not_ready(self):
        # The startup-prompt shape: the agent exists and is listed, and
        # is NOT able to take a turn.
        self.dispatched()
        self.set_probe(all_ready(
            lead1=ready_record(name="target-lead1", ready=False)
        ))
        self.verify_at(NOW)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_WAITING],
        )

    def test_a_truthy_readiness_value_is_not_True(self):
        # `interactive_ready` is required to be exactly True. A
        # dependency that started emitting the string "true" is a
        # DIFFERENT dependency, and reading it as ready would be
        # readiness on no evidence.
        self.dispatched()
        self.set_probe(all_ready(
            supervisor=ready_record(name="s", ready="true")
        ))
        self.verify_at(NOW)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_WAITING],
        )

    def test_waiting_does_not_block_and_does_not_advance(self):
        self.dispatched()
        self.set_probe({})
        self.verify_at(NOW)
        entry = self.entry_from_disk()
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(entry["ambiguity"]["state"],
                         wa_record.AMBIGUITY_NONE)


class ActionableDurableFailureTests(ReadinessCase):
    """ADVERSARIAL CLASS: bootstrap failure surfaced as ACTIONABLE
    DURABLE STATE, asserted from a FRESH READ OF DISK."""

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def past_the_bound(self):
        return NOW + readiness_module.BOOTSTRAP_MAX_SECONDS + 1

    def test_bootstrap_failure_blocks_durably_with_its_problem_code(self):
        self.dispatched()
        self.set_probe({})
        outcome = self.verify_at(self.past_the_bound())
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem,
            readiness_module.PROBLEM_BOOTSTRAP_INCOMPLETE,
        )
        entry = self.entry_from_disk()
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIn(
            readiness_module.BOOTSTRAP_FAILED,
            readiness_module.recorded_states(entry),
        )

    def test_the_durable_receipt_names_what_happened(self):
        self.dispatched()
        self.set_probe({})
        self.verify_at(self.past_the_bound())
        summaries = [
            receipt["bounded_summary"]
            for receipt in self.entry_from_disk()["receipts"]
            if receipt["bounded_summary"].startswith(
                readiness_module.BOOTSTRAP_RECEIPT_MARKER
            )
        ]
        self.assertTrue(summaries)
        self.assertIn("bootstrap failure, not engineering",
                      summaries[-1])

    def test_unobservable_is_a_DISTINCT_durable_state(self):
        # "We could not look" and "we looked and it was not ready" are
        # different things for whoever reads the durable state, and
        # only the SECOND one can stop a workflow.
        self.dispatched()
        self.set_probe(None)
        outcome = self.verify_at(self.past_the_bound())
        self.assertTrue(outcome.ok)
        self.assertIn(
            readiness_module.BOOTSTRAP_UNOBSERVABLE,
            self.readiness_states_on_disk(),
        )
        self.assertNotIn(
            readiness_module.BOOTSTRAP_FAILED,
            self.readiness_states_on_disk(),
        )

    def test_absence_of_evidence_does_NOT_block_past_the_bound(self):
        # The brief's constraint, driven: absence of evidence is not
        # readiness AND is not a failure that blocks a mission which
        # may be running fine and merely unreadable. An earlier draft
        # of this layer blocked here, and `test_no_mission_timer_
        # behavioral` in test_target_runtime.py caught it.
        self.dispatched()
        self.set_probe(None)
        for elapsed in (1, 3600, 10 ** 9):
            outcome = self.verify_at(NOW + elapsed)
            self.assertTrue(
                outcome.ok,
                "an UNOBSERVABLE target was stopped after %ds; that"
                " is a deadline on a mission we merely cannot read"
                % elapsed,
            )
        self.assertEqual(
            self.entry_from_disk()["phase"], wa_record.PHASE_DISPATCHED
        )

    def test_the_unbounded_unobservable_wait_is_RECORDED_not_silent(self):
        self.dispatched()
        self.set_probe(None)
        self.verify_at(NOW + 10 ** 9)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_UNOBSERVABLE],
        )

    def test_a_probe_that_raises_is_unobservable_not_a_crash(self):
        self.dispatched()

        def boom(_path):
            raise OSError("no such registry")

        self.set_probe(boom)
        outcome = self.verify_at(NOW)
        self.assertTrue(outcome.ok)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_UNOBSERVABLE],
        )

    def test_the_block_leaves_ambiguity_and_target_engine_untouched(self):
        # The crash boundary this increment must PRESERVE:
        # `dispatch_identity_unresolved` derives from `target_engine`,
        # and a non-none `ambiguity` makes a workflow unclaimable.
        # A readiness block writes neither.
        self.dispatched()
        before = self.entry_from_disk()
        self.set_probe({})
        self.verify_at(self.past_the_bound())
        after = self.entry_from_disk()
        self.assertEqual(after["ambiguity"], before["ambiguity"])
        self.assertEqual(after["target_engine"], before["target_engine"])
        self.assertFalse(
            runtime_module.dispatch_identity_unresolved(after)
        )


class ReadinessReachedTests(ReadinessCase):
    """ADVERSARIAL CLASS: readiness genuinely reached."""

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def test_all_four_roles_ready_records_ready(self):
        self.dispatched()
        self.set_probe(all_ready())
        outcome = self.verify_at(NOW)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.outcome, "target_running")
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_READY],
        )

    def test_waiting_then_ready_records_both_in_order(self):
        self.dispatched()
        self.set_probe({})
        self.verify_at(NOW)
        self.set_probe(all_ready())
        self.verify_at(NOW + 1)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_WAITING,
             readiness_module.BOOTSTRAP_READY],
        )

    def test_readiness_survives_the_probe_going_away(self):
        # Once evidenced, a later probe failure does not un-evidence
        # it within this record.
        self.dispatched()
        self.set_probe(all_ready())
        self.verify_at(NOW)
        self.set_probe(None)
        outcome = self.verify_at(
            NOW + readiness_module.BOOTSTRAP_MAX_SECONDS * 100
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(
            self.entry_from_disk()["phase"], wa_record.PHASE_DISPATCHED
        )


class EngineeringRunsForHoursTests(ReadinessCase):
    """ADVERSARIAL CLASS — THE PROPERTY THIS INCREMENT EXISTS FOR:
    the distinction holds when engineering legitimately runs long.

    Every test here would FAIL if the bootstrap bound leaked into the
    mission, which is the failure mode the brief calls the hard line.
    """

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def hours(self, count):
        return NOW + count * 3600

    def test_a_ready_target_is_never_bounded_however_long_it_runs(self):
        self.dispatched()
        self.set_probe(all_ready())
        self.verify_at(NOW)
        for hour in (1, 6, 24, 24 * 30):
            outcome = self.verify_at(self.hours(hour))
            self.assertTrue(
                outcome.ok,
                "a mission ready at dispatch was stopped %d hours"
                " later; the bootstrap bound has leaked into the"
                " engineering mission" % hour,
            )
            self.assertEqual(outcome.outcome, "target_running")
            self.assertEqual(
                self.entry_from_disk()["phase"],
                wa_record.PHASE_DISPATCHED,
            )

    def test_a_ready_target_is_not_even_PROBED_again(self):
        # The bound has no route to leak through a probe that,
        # within this path, is not called.
        self.dispatched()
        self.set_probe(all_ready())
        self.verify_at(NOW)

        def must_not_be_called(_path):
            raise AssertionError(
                "the readiness probe was consulted after readiness was"
                " evidenced; past bootstrap this layer must decide"
                " from durable state alone"
            )

        self.set_probe(must_not_be_called)
        self.assertTrue(self.verify_at(self.hours(48)).ok)

    def test_a_long_run_writes_no_further_readiness_receipts(self):
        self.dispatched()
        self.set_probe(all_ready())
        self.verify_at(NOW)
        before = self.store_bytes()
        for hour in (2, 5, 40):
            self.verify_at(self.hours(hour))
        self.assertEqual(
            self.store_bytes(), before,
            "a long healthy run churned the store; readiness is"
            " recorded on CHANGE, and past bootstrap it does not"
            " change",
        )

    def test_the_module_reads_no_clock_once_readiness_is_evidenced(self):
        # A direct pin on the ordering inside `evaluate`: durable
        # evidence is consulted BEFORE the bound, so no clock value
        # can produce a stop.
        entry = {
            "receipts": [readiness_module.readiness_receipt(
                readiness_module.BOOTSTRAP_READY, "", NOW
            )],
            "target_engine": {"dispatched_at": NOW},
        }
        # Numeric, because the record's timestamp domain IS numeric —
        # an ISO string here would make `bootstrap_deadline_passed`
        # return False for a reason unrelated to the ordering this
        # test exists to pin, and the test would pass vacuously.
        for now in (NOW + 1, NOW + 10 ** 9):
            state, _detail, _pairs, probed, stop = (
                readiness_module.evaluate(
                    entry, lambda: {}, now, max_seconds=0
                )
            )
            self.assertEqual(state, readiness_module.BOOTSTRAP_READY)
            self.assertFalse(stop)
            self.assertFalse(probed)


class RestartReplayTests(ReadinessCase):
    """ADVERSARIAL CLASS: no duplicate replay across a restart."""

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def test_a_fresh_broker_does_not_replay_a_recorded_state(self):
        self.dispatched()
        self.set_probe({})
        self.verify_at(NOW)
        states_before = self.readiness_states_on_disk()
        # A NEW broker object over the SAME store: the restart case.
        self.verify_at(NOW + 1)
        self.assertEqual(self.readiness_states_on_disk(), states_before)

    def test_the_bound_is_measured_from_durable_dispatch_not_startup(self):
        # A restart must neither restart the bound nor forgive it: a
        # workflow already past its window blocks on the first poll
        # after the restart.
        self.dispatched()
        self.set_probe({})
        outcome = self.verify_at(
            NOW + readiness_module.BOOTSTRAP_MAX_SECONDS + 5
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem,
            readiness_module.PROBLEM_BOOTSTRAP_INCOMPLETE,
        )

    def test_repeated_identical_states_write_once(self):
        self.dispatched()
        self.set_probe({})
        self.verify_at(NOW)
        after_first = self.store_bytes()
        self.verify_at(NOW + 1)
        self.verify_at(NOW + 2)
        self.assertEqual(self.store_bytes(), after_first)


class StalenessTests(unittest.TestCase):
    """A status that is true but STALE reads exactly like one that is
    true and CURRENT unless something monotonic is checked alongside
    it. `revision` and `state_change_seq` are that something."""

    def test_a_backward_counter_refuses_to_report_ready(self):
        prior = {logical: (10, 10)
                 for logical in readiness_module.REQUIRED_LOGICAL_ROLES}
        probe = all_ready()
        state, detail, _pairs = readiness_module.probe_verdict(
            probe, prior_pairs=prior
        )
        self.assertEqual(state, readiness_module.BOOTSTRAP_WAITING)
        self.assertIn("BACKWARD", detail)

    def test_an_advancing_counter_reports_ready(self):
        prior = {logical: (0, 0)
                 for logical in readiness_module.REQUIRED_LOGICAL_ROLES}
        state, _detail, pairs = readiness_module.probe_verdict(
            all_ready(), prior_pairs=prior
        )
        self.assertEqual(state, readiness_module.BOOTSTRAP_READY)
        self.assertEqual(len(pairs),
                         len(readiness_module.REQUIRED_LOGICAL_ROLES))

    def test_a_missing_counter_is_not_readiness(self):
        record = ready_record()
        del record["state_change_seq"]
        state, _detail, _pairs = readiness_module.probe_verdict(
            all_ready(supervisor=record)
        )
        self.assertEqual(state, readiness_module.BOOTSTRAP_WAITING)

    def test_a_boolean_counter_is_not_an_integer_counter(self):
        # `isinstance(True, int)` is true in Python; a bool where a
        # counter belongs is a different dependency, not a counter.
        state, _detail, _pairs = readiness_module.probe_verdict(
            all_ready(lead1=ready_record(revision=True))
        )
        self.assertEqual(state, readiness_module.BOOTSTRAP_WAITING)

    def test_no_prior_pairs_reports_no_regression(self):
        self.assertEqual(
            readiness_module._regressed_roles({"a": (1, 1)}, None), []
        )


class FailureDirectionTests(ReadinessCase):
    """The brief requires naming WHICH WAY this signal fails and
    proving it. It fails toward NOT READY. Both halves are driven."""

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def test_it_does_NOT_read_as_ready_on_no_evidence(self):
        for empty in ({}, None, [], "", 0):
            with self.subTest(probe=repr(empty)):
                state, _detail, _pairs = readiness_module.probe_verdict(
                    empty
                )
                self.assertNotEqual(
                    state, readiness_module.BOOTSTRAP_READY,
                    "an empty or absent probe reported READY; a"
                    " readiness signal that passes on no evidence is"
                    " the failure this class exists to rule out",
                )

    def test_it_DOES_block_a_target_it_can_SEE_and_that_is_not_ready(self):
        # The stopping direction, driven: a probe that ANSWERED, whose
        # answer showed the roles absent, exhausts the bound.
        self.dispatched()
        self.set_probe({})
        outcome = self.verify_at(
            NOW + readiness_module.BOOTSTRAP_MAX_SECONDS + 1
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            self.entry_from_disk()["phase"], wa_record.PHASE_BLOCKED
        )

    def test_the_residual_it_leaves_is_the_unobservable_stuck_target(self):
        # Named and driven rather than left in prose: a target that is
        # stuck AND unobservable is indistinguishable from a healthy
        # unobservable one, so it waits — visibly.
        self.dispatched()
        self.set_probe(None)
        self.assertTrue(self.verify_at(NOW + 10 ** 9).ok)
        self.assertEqual(
            self.readiness_states_on_disk(),
            [readiness_module.BOOTSTRAP_UNOBSERVABLE],
        )

    def test_but_it_never_blocks_a_run_that_was_once_ready(self):
        # The same broken probe, on a workflow past bootstrap, is
        # inert — which is what bounds the residual above.
        self.dispatched()
        self.set_probe(all_ready())
        self.verify_at(NOW)
        self.set_probe(None)
        outcome = self.verify_at(
            NOW + readiness_module.BOOTSTRAP_MAX_SECONDS * 1000
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(
            self.entry_from_disk()["phase"], wa_record.PHASE_DISPATCHED
        )


class ObservationPathUntouchedTests(ReadinessCase):
    """The readiness path is SEPARATE from the production observation,
    and this asserts the separation by execution rather than by
    reading the two call sites."""

    def setUp(self):
        super().setUp()
        self.target_task_status = "ACTIVE"

    def test_the_observer_is_still_called_with_probe_agents_false(self):
        # The production observer's argument is pinned by the module
        # that wires it; here the point is that the readiness gate
        # introduced NO second observation call.
        self.dispatched()
        self.set_probe(all_ready())
        before = len(self.observe_calls)
        self.verify_at(NOW)
        self.assertEqual(
            len(self.observe_calls) - before, 1,
            "the readiness gate added an observation call; it is"
            " supposed to use a separate probe, not a second observe",
        )

    def test_the_production_observer_does_not_probe_agents(self):
        """EXECUTED, not read: `_production_observer` is CALLED with
        `herdr.observe.observe` patched, and the argument it actually
        passed is asserted. An earlier version of this test read the
        source with `inspect.getsource`, which R-8 refuses as a
        load-bearing pin for a behavioural property."""
        from unittest.mock import patch
        import herdr.observe
        with patch.object(herdr.observe, "observe") as observe:
            observe.return_value = {}
            broker_module._production_observer("/some/lease")
        self.assertEqual(observe.call_args.args, ("/some/lease",))
        self.assertEqual(
            observe.call_args.kwargs, {"probe_agents": False},
            "the production observation started probing agents; the"
            " readiness design rests on its NOT doing so",
        )

    def test_readiness_consumes_no_registered_source_set(self):
        # Consuming `agents` in a registered set would reproduce the
        # recorded agents-unprobed PARTIAL defect. Within the
        # registry, this layer appears in no entry.
        from target_runtime import evidence as evidence_module
        for name, sources in evidence_module.CONSUMED_SOURCE_SETS.items():
            with self.subTest(source_set=name):
                self.assertNotIn("agents", sources)


class AgentListContractTests(unittest.TestCase):
    """CONTRACT TESTS against the REAL dependency.

    `herdr agent list` is the source the production probe reads. These
    pin its field paths and value domains from the dependency's own
    output, prove the double accepts no more than production emits, and
    prove a forwarded value reaches its destination and CHANGES an
    outcome.

    Scope: within this class the dependency is the `herdr` binary
    installed on the machine running the suite. Where it is absent the
    tests SKIP rather than pass, so a machine without it records an
    unmeasured contract instead of a green one.
    """

    @classmethod
    def setUpClass(cls):
        cls.payload = None
        try:
            completed = subprocess.run(
                ["herdr", "agent", "list"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if completed.returncode != 0:
            return
        try:
            cls.payload = json.loads(completed.stdout)
        except ValueError:
            cls.payload = None

    def agents(self):
        if self.payload is None:
            self.skipTest(
                "`herdr agent list` is unavailable here, so the real"
                " dependency's shape cannot be observed; this contract"
                " is UNMEASURED on this machine rather than satisfied"
            )
        try:
            return self.payload["result"]["agents"]
        except (KeyError, TypeError):
            self.fail(
                "the real payload no longer carries result.agents; the"
                " production probe reads exactly that path"
            )

    def test_the_field_path_the_probe_reads_exists(self):
        listed = self.agents()
        self.assertIsInstance(listed, list)
        self.assertTrue(
            listed,
            "no agents are listed here, so this contract has no"
            " specimen to pin within this run",
        )

    def test_every_consumed_field_is_present_on_real_records(self):
        for record in self.agents():
            for field in readiness_module.CONSUMED_AGENT_FIELDS:
                self.assertIn(
                    field, record,
                    "the real dependency stopped emitting %r, which"
                    " this layer consumes" % field,
                )

    def test_the_real_agent_status_domain_covers_the_pinned_one(self):
        observed = {record.get("agent_status") for record in self.agents()}
        unknown = observed - set(readiness_module.READY_AGENT_STATUSES)
        self.assertEqual(
            unknown, set(),
            "the real dependency emits agent_status value(s) this"
            " layer does not classify: %r" % sorted(unknown),
        )

    def test_interactive_ready_is_a_real_boolean(self):
        for record in self.agents():
            self.assertIsInstance(
                record.get("interactive_ready"), bool,
                "interactive_ready is not a bool on the real"
                " dependency; `is True` would then be the wrong test",
            )

    def test_the_counters_are_real_integers(self):
        for record in self.agents():
            for field in ("revision", "state_change_seq"):
                value = record.get(field)
                self.assertIsInstance(value, int)
                self.assertNotIsInstance(value, bool)

    def test_the_observed_readiness_domain_is_a_SINGLETON_here(self):
        """DISCLOSURE, pinned so that within this suite it does not
        rot into a silent assumption: on this machine every listed
        agent reports `interactive_ready: True`.

        That is why readiness in this layer is NOT built on that flag
        alone: within the observed domain, one value leaves the field
        unable to discriminate. The discriminating evidence is
        REGISTRATION — a role that failed to come up is absent from
        the mapping, which `BootstrapStuckTests` drives. If this test ever fails
        because a `False` appeared, the flag has become discriminating
        and that is worth knowing, not worth suppressing.
        """
        observed = {
            record.get("interactive_ready") for record in self.agents()
        }
        self.assertEqual(
            observed, {True},
            "interactive_ready now takes more than one value on this"
            " machine (%r); the disclosure in readiness.py that the"
            " flag alone cannot discriminate should be revisited"
            % sorted(observed, key=repr),
        )

    def test_the_double_accepts_no_more_than_production_emits(self):
        """The double must not accept a record production could not
        produce: every field the double supplies is a field the real
        dependency emits, with the same Python type."""
        real = self.agents()[0]
        double = ready_record()
        for field, value in double.items():
            self.assertIn(field, real)
            self.assertIsInstance(value, type(real[field]))

    def test_a_forwarded_value_reaches_its_destination(self):
        """A REAL record, taken from the live dependency and given the
        logical names this layer requires, is accepted as ready — so
        the fields are not merely present, they are consumed."""
        real = self.agents()[0]
        probe = {
            logical: dict(real)
            for logical in readiness_module.REQUIRED_LOGICAL_ROLES
        }
        state, _detail, pairs = readiness_module.probe_verdict(probe)
        self.assertEqual(state, readiness_module.BOOTSTRAP_READY)
        self.assertEqual(
            len(pairs), len(readiness_module.REQUIRED_LOGICAL_ROLES)
        )

    def test_a_forwarded_value_CHANGES_the_outcome(self):
        """The same real record with one consumed field flipped
        produces a different verdict — the value is load-bearing, not
        decorative."""
        real = self.agents()[0]
        probe = {
            logical: dict(real)
            for logical in readiness_module.REQUIRED_LOGICAL_ROLES
        }
        ready_state, _d, _p = readiness_module.probe_verdict(probe)
        probe["lead1"] = dict(real, interactive_ready=False)
        changed_state, _d2, _p2 = readiness_module.probe_verdict(probe)
        self.assertEqual(ready_state, readiness_module.BOOTSTRAP_READY)
        self.assertEqual(
            changed_state, readiness_module.BOOTSTRAP_WAITING
        )


class ProductionProbeTests(unittest.TestCase):
    """The production probe itself, driven over real files."""

    def test_a_lease_without_runtime_state_is_unobservable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            self.assertIsNone(
                broker_module._production_readiness_probe(base)
            )

    def test_a_malformed_runtime_state_is_unobservable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            state = os.path.join(base, ".herd", "state")
            os.makedirs(state)
            with open(os.path.join(state, "runtime.json"), "w") as handle:
                handle.write("{ not json")
            self.assertIsNone(
                broker_module._production_readiness_probe(base)
            )

    def test_an_agents_mapping_that_is_not_a_mapping_is_unobservable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            state = os.path.join(base, ".herd", "state")
            os.makedirs(state)
            with open(os.path.join(state, "runtime.json"), "w") as handle:
                json.dump({"agents": ["not", "a", "mapping"]}, handle)
            self.assertIsNone(
                broker_module._production_readiness_probe(base)
            )


# The executed hermetic-git sweep runs a swept module with
# `runpy.run_path(..., run_name="__main__")`. Within that runner, a
# module lacking this block imports and exits, so the shim observes no
# git process and the sweep reports having no observation to assert on
# — which is how the absence of this block was found.
if __name__ == "__main__":
    unittest.main()
