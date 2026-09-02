"""DI-REMOTE-3 increment I4: the Broker INITIAL-dispatch gate (R-6).

Scope of THIS file: Layer 2 of plan §1.1 — the tri-state placeholder
precondition on initial dispatch, living in `target_runtime/broker.py`
beside `_trust_still_consumable` and the protected-surface refusal.
The adapter is untouched and gains no authority; the edit-based
delivery engine is I5.

**The disclosure this increment must carry (Supervisor §6, plan
§6a.1), stated here as plainly as in the code:** the acceptance
criterion "Runtime dispatch must not occur until the result
placeholder is durably bound" holds **STRICTLY for go-forward,
placeholder-requested workflows**. Pre-existing records — those with
`result_placeholder is None` — dispatch **UNGATED** on the legacy
at-most-once path. That is a deliberate, disclosed narrowing, and
`test_B4_legacy_null_placeholder_dispatches_ungated` is where it is
made visible rather than left implicit.

New file, new tests only: no pre-existing test is modified.

Test ids map to plan §5: T-B1..T-B5.
"""

import unittest

from target_runtime import broker as broker_module
from workflow_authority import record as wa_record

from test_target_runtime import NOW, RuntimeCase


# Independently authored from plan §3.1 — NOT derived from
# `wa_record.PLACEHOLDER_STATES`, so a state added or renamed in the
# schema cannot silently drop out of this gate's coverage.
EXPECTED_UNBOUND_STATES = (
    "required", "sending", "failed_unsent", "indefinite", "unbindable",
)
EXPECTED_BOUND_STATE = "bound"


def placeholder(state, chat_id=1001):
    """A schema-VALID placeholder in `state` (plan §3.1 table)."""
    value = {
        "state": state, "chat_id": chat_id, "message_id": None,
        "requested_at": 10, "sent_at": None, "bound_at": None,
        "text_digest": None,
    }
    if state in ("sending", "failed_unsent", "indefinite", "bound",
                 "unbindable"):
        value["sent_at"] = 11
        value["text_digest"] = "b" * 64
    if state in ("bound", "unbindable"):
        value["message_id"] = 77
        value["bound_at"] = 12
    return value


class DispatchGateCase(RuntimeCase):
    """A validated workflow, one step short of initial dispatch.

    `validated()` is reimplemented here rather than imported: the
    pre-existing helper of that name lives on a test-bearing class in
    `test_target_runtime.py`, and importing that class would re-run
    its whole suite under this module.
    """

    def validated(self, handoff_text="HANDOFF DESTINATION TEXT"):
        entry = self.authorized_record(handoff_text=handoff_text)
        self.put_record(entry)
        for action in (
            broker_module.ACTION_MATERIALIZE,
            broker_module.ACTION_PREPARE,
            broker_module.ACTION_VALIDATE_HANDOFF,
        ):
            outcome = self.perform("wf-0001", action, 2)
            self.assertTrue(outcome.ok, (action, outcome.problem))

    def set_placeholder(self, document, placeholder_value,
                        workflow_id="wf-0001"):
        """Attach a placeholder, copying `chat_id` FROM THE RECORD.

        The record schema requires `result_placeholder.chat_id ==
        telegram.chat_id` (I1 T-M9), which is exactly what the adapter
        does at request time. Hard-coding a chat id here made the
        store refuse to load — the schema catching a test fixture,
        which is the guard working.
        """
        entry = document["workflows"][workflow_id]
        if isinstance(placeholder_value, dict):
            placeholder_value = dict(
                placeholder_value,
                chat_id=entry["telegram"]["chat_id"],
            )
        entry["result_placeholder"] = placeholder_value
        return document

    def ready_to_dispatch(self, placeholder_value="absent"):
        self.validated()
        if placeholder_value != "absent":
            document = self.set_placeholder(
                self.fresh_workflows(), placeholder_value
            )
            self.write_raw(document)
        return self.fresh_workflows()["workflows"]["wf-0001"]

    def stored_placeholder(self, workflow_id="wf-0001"):
        return self.fresh_workflows()["workflows"][workflow_id].get(
            "result_placeholder"
        )


class InitialDispatchGateTests(DispatchGateCase):

    def test_B1_initial_dispatch_refused_until_placeholder_bound(self):
        """T-B1: every UNBOUND placeholder state refuses initial
        dispatch, fail-closed, with the gate's OWN problem code — and
        with ZERO SPAWN.

        The spawn count is asserted per state rather than inferred:
        under a mutant that removes the gate the record WOULD dispatch,
        and this must die by ASSERTION on the recorded spawn list, not
        by a hang or a crash (the recorded stall-vs-kill rule).
        """
        # Anti-vacuity on the state set itself: the states driven here
        # are exactly the schema's non-bound states, so a new state
        # cannot appear without this test noticing.
        self.assertEqual(
            set(wa_record.PLACEHOLDER_STATES),
            set(EXPECTED_UNBOUND_STATES) | {EXPECTED_BOUND_STATE},
        )
        for state in EXPECTED_UNBOUND_STATES:
            with self.subTest(state=state):
                self.setUp()
                self.ready_to_dispatch(placeholder(state))
                spawns_before = len(self.spawn_requests)
                outcome = self.perform(
                    "wf-0001", broker_module.ACTION_DISPATCH, 2
                )
                self.assertFalse(
                    outcome.ok,
                    "state %r must REFUSE initial dispatch" % state,
                )
                self.assertEqual(
                    outcome.problem,
                    broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
                    state,
                )
                # ZERO SPAWN — the guarantee, asserted directly.
                self.assertEqual(
                    len(self.spawn_requests), spawns_before,
                    "state %r must spawn NOTHING; spawned %r"
                    % (state, self.spawn_requests[spawns_before:]),
                )
                # The refusal names the state, so /status and the
                # operator see WHY rather than a bare code.
                self.assertIn(state, outcome.detail, state)
                self.assertIn("not durably bound", outcome.detail)

    def test_B2_bound_placeholder_permits_initial_dispatch(self):
        """T-B2: the positive case, and the anti-vacuity for T-B1 —
        without it, a gate that refused EVERYTHING would satisfy
        T-B1 completely."""
        self.ready_to_dispatch(placeholder("bound"))
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        self.assertEqual(
            len(self.spawn_requests), spawns_before + 1,
            "a BOUND placeholder must permit the dispatch to spawn",
        )
        self.assertEqual(
            self.fresh_workflows()["workflows"]["wf-0001"]["phase"],
            wa_record.PHASE_DISPATCHED,
        )

    def test_B4_legacy_null_placeholder_dispatches_ungated(self):
        """T-B4: `result_placeholder is None` is the LEGACY LANE and
        dispatches UNGATED.

        This is NOT "placeholder not needed". The record predates or
        sits outside the placeholder architecture and its result is
        delivered at-most-once. Gating on ABSENCE would refuse every
        pre-existing workflow forever.

        It is also the ANTI-REGRESSION PIN for the ~30 pre-existing
        dispatch tests across test_target_runtime.py,
        test_workspace_trust.py, test_release_narrative.py,
        test_readiness.py, test_evidence.py and test_role_turn.py,
        every one of which drives ACTION_DISPATCH on a
        placeholder-free record. If the tri-state were wrong, those
        would break — and the fix would be here, never in them.
        """
        entry = self.ready_to_dispatch()
        self.assertIsNone(
            entry["result_placeholder"],
            "the pre-existing dispatch fixture must really be"
            " placeholder-free, or this pin proves nothing",
        )
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        self.assertEqual(
            len(self.spawn_requests), spawns_before + 1,
            "a legacy (null) placeholder must dispatch UNGATED",
        )
        # The gate did not fabricate a placeholder either.
        self.assertIsNone(self.stored_placeholder())

    def test_B5_dispatch_gate_refusal_writes_no_state(self):
        """T-B5: a gate refusal writes NOTHING to the workflow store —
        no phase transition, no receipt, not one byte.

        Driven through the REAL capability path (`self.perform`), not
        the raw `broker.perform`: a raw call is refused by the
        capability precondition FIRST and never reaches the gate at
        all, so it would prove nothing about this guard. The
        capability store legitimately changes (the harness mints and
        the Broker consumes the one-shot token, which is the normal
        contract for any action); the guarantee under test is that the
        WORKFLOW store is byte-identical across the refusal.
        """
        self.ready_to_dispatch(placeholder("indefinite"))
        receipts_before = (
            self.fresh_workflows()["workflows"]["wf-0001"]["receipts"]
        )
        before_bytes = self.store_bytes()
        self.assertIsNotNone(
            before_bytes,
            "the store must exist before the refusal, or the"
            " byte-comparison below is vacuous",
        )
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem,
            broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
            "the refusal must come from THIS gate, not from an"
            " earlier precondition; got %r" % (outcome.problem,),
        )
        self.assertEqual(
            self.store_bytes(), before_bytes,
            "a gate refusal must not change one byte of the workflow"
            " store",
        )
        entry = self.fresh_workflows()["workflows"]["wf-0001"]
        self.assertEqual(entry["phase"], wa_record.PHASE_VALIDATED)
        self.assertEqual(entry["receipts"], receipts_before)
        # Anti-vacuity for the byte comparison: the SAME fixture with a
        # bound placeholder DOES change the store, so an unchanged
        # store is the gate refusing rather than the harness being
        # inert.
        self.setUp()
        self.ready_to_dispatch(placeholder("bound"))
        changed_before = self.store_bytes()
        permitted = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertTrue(permitted.ok, permitted.problem)
        self.assertNotEqual(self.store_bytes(), changed_before)

    def test_the_gate_refuses_before_any_filesystem_work(self):
        """The gate is a PURE RECORD READ and refuses before the
        protected-surface digest touches the filesystem — the same
        shape as the follow-up bound already in this function."""
        self.ready_to_dispatch(placeholder("required"))
        calls = []
        from target_runtime import evidence as evidence_module
        original = evidence_module.protected_surface_digest

        def recording(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        evidence_module.protected_surface_digest = recording
        try:
            outcome = self.perform(
                "wf-0001", broker_module.ACTION_DISPATCH, 2
            )
        finally:
            evidence_module.protected_surface_digest = original
        self.assertFalse(outcome.ok)
        self.assertEqual(
            calls, [],
            "the placeholder gate must refuse BEFORE the surface"
            " digest does filesystem work",
        )


class FollowUpScopeTests(DispatchGateCase):

    def dispatched_with(self, placeholder_value):
        """Drive a workflow to DISPATCHED, then set the placeholder to
        whatever the follow-up case needs."""
        self.ready_to_dispatch(placeholder("bound"))
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_DISPATCH, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        self.write_raw(
            self.set_placeholder(self.fresh_workflows(),
                                 placeholder_value)
        )

    def test_B3_follow_up_dispatch_is_not_placeholder_gated(self):
        """T-B3: R-6 scopes the gate to `not follow_up`.

        A follow-up continues a mission whose placeholder question was
        settled at initial dispatch, so it is NOT gated — driven for
        every unbound state, and asserted on the spawn actually
        recorded. Widening the gate to follow-ups would strand
        corrective work for no added guarantee.
        """
        for state in EXPECTED_UNBOUND_STATES:
            with self.subTest(state=state):
                self.setUp()
                self.dispatched_with(placeholder(state))
                spawns_before = len(self.spawn_requests)
                outcome = self.perform(
                    "wf-0001", broker_module.ACTION_FOLLOW_UP, 2
                )
                self.assertNotEqual(
                    outcome.problem,
                    broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
                    "a FOLLOW-UP must never be refused by the"
                    " placeholder gate (state %r)" % state,
                )
                self.assertTrue(outcome.ok, (state, outcome.problem))
                self.assertEqual(
                    len(self.spawn_requests), spawns_before + 1,
                    "the follow-up must actually spawn (state %r)"
                    % state,
                )

    def test_B3_follow_up_is_ungated_for_a_legacy_record_too(self):
        """T-B3 (b): and for the legacy lane, so the follow-up scope
        does not accidentally depend on a placeholder existing."""
        self.dispatched_with(None)
        spawns_before = len(self.spawn_requests)
        outcome = self.perform(
            "wf-0001", broker_module.ACTION_FOLLOW_UP, 2
        )
        self.assertTrue(outcome.ok, outcome.problem)
        self.assertEqual(
            len(self.spawn_requests), spawns_before + 1
        )


class GateProblemCodeTests(unittest.TestCase):
    """The gate has its OWN problem code, pinned to an independently
    authored literal.

    Asserting `outcome.problem == broker.PROBLEM_PLACEHOLDER_NOT_BOUND`
    everywhere is self-consistent: alias the constant to another
    precondition's code and both sides move together, so the "own
    problem code" requirement stays green while being false. The
    literal below is transcribed once, here.
    """

    def test_the_gate_problem_code_is_its_own_exact_string(self):
        self.assertEqual(
            broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
            "broker_result_placeholder_not_bound",
        )

    def test_the_gate_code_is_distinct_from_every_other_broker_problem(
        self
    ):
        others = sorted(
            value for name, value in vars(broker_module).items()
            if name.startswith("PROBLEM_")
            and name != "PROBLEM_PLACEHOLDER_NOT_BOUND"
            and isinstance(value, str)
        )
        # Anti-vacuity: there really are other problem codes to
        # collide with.
        self.assertGreater(len(others), 20)
        self.assertNotIn(
            broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND, others,
            "the R-6 gate must refuse with its OWN code, never reuse"
            " another precondition's",
        )


class GatePredicateTests(unittest.TestCase):
    """The gate predicate directly — a pure function, so the tri-state
    can be driven exhaustively without a broker."""

    def refusal(self, placeholder_value):
        return broker_module._placeholder_dispatch_refusal(
            {"result_placeholder": placeholder_value}
        )

    def test_the_tri_state_is_exactly_three_valued(self):
        # 1. LEGACY: absent -> permit.
        self.assertEqual(self.refusal(None), (None, None))
        # 2. REQUESTED but not bound -> refuse, every state.
        for state in EXPECTED_UNBOUND_STATES:
            problem, detail = self.refusal(placeholder(state))
            self.assertEqual(
                problem,
                broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND, state,
            )
            self.assertIsNotNone(detail, state)
        # 3. BOUND -> permit.
        self.assertEqual(
            self.refusal(placeholder(EXPECTED_BOUND_STATE)),
            (None, None),
        )

    def test_an_unrecognized_placeholder_state_fails_closed(self):
        """Belt coverage: the schema forbids it, but a state this gate
        does not recognise must REFUSE rather than fall through to
        permit. Fail-closed is the only safe default here."""
        for state in ("teleported", "", None, 0, True, "BOUND"):
            problem, _ = self.refusal(
                dict(placeholder("required"), state=state)
            )
            self.assertEqual(
                problem,
                broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
                repr(state),
            )

    def test_a_missing_state_key_fails_closed(self):
        problem, _ = self.refusal({"chat_id": 1})
        self.assertEqual(
            problem, broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND
        )

    def test_the_gate_never_mutates_the_record(self):
        """The gate is a PURE READ: it decides, it does not repair.

        A gate that "helpfully" fabricated a placeholder onto the entry
        would make absence indistinguishable from bound in the DURABLE
        record the moment any later step saved it — inventing a binding
        for an object that was never created.
        """
        import copy
        for placeholder_value in (
            None, placeholder("required"), placeholder("bound"),
            placeholder("indefinite"), {"chat_id": 1},
        ):
            entry = {"result_placeholder": placeholder_value,
                     "phase": "VALIDATED"}
            before = copy.deepcopy(entry)
            broker_module._placeholder_dispatch_refusal(entry)
            self.assertEqual(
                entry, before,
                "the gate must not modify the record it inspects"
                " (%r)" % (placeholder_value,),
            )

    def test_a_refusal_always_carries_an_actionable_detail(self):
        """A bare problem code tells an operator nothing. Every refusal
        names the state it saw and says what is required."""
        for state in EXPECTED_UNBOUND_STATES:
            problem, detail = self.refusal(placeholder(state))
            self.assertEqual(
                problem,
                broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND, state,
            )
            self.assertTrue(detail, "state %r: empty detail" % state)
            self.assertIn(state, detail, state)
            self.assertIn("not durably bound", detail, state)
            self.assertIn("Nothing was dispatched", detail, state)

    def test_the_permit_case_is_bound_and_only_bound(self):
        # Anti-vacuity for the two fail-closed tests above: exactly ONE
        # value of `state` permits, and it is the schema's own
        # constant rather than a string this file invented.
        self.assertEqual(
            wa_record.PLACEHOLDER_BOUND, EXPECTED_BOUND_STATE
        )
        permitted = [
            state for state in list(wa_record.PLACEHOLDER_STATES)
            + ["teleported", None]
            if self.refusal(
                dict(placeholder("required"), state=state)
            )[0] is None
        ]
        self.assertEqual(permitted, [EXPECTED_BOUND_STATE])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
