"""The office delivery adapter (owner decision (b), owner-decisions.md:141; the approved proposal reports/owner-delivery-bridge-premise-correction-20261029.md; and the expected-step supplement,
owner-decisions.md:149): status, eligibility, the confirmation check, and ONE confirmed step through pr_delivery's expected-step guard.

VERSION BOUNDARY: until the guard landed, this file pinned a NON-MUTATING adapter (record office-delivery-b1-candidate-20261029.md, approved in round 42 for ITS bound bytes). That description is
HISTORICAL; the adapter now performs one confirmed step, and the blocks below say so.

What this pins:
  * NO TEST CAN REACH THE REAL TRANSPORT. For the whole module, constructing pr_delivery's DeliveryTransport, or calling cli.build_machine (which constructs it), raises. Every test injects its
    machine. A path that reaches the real transport fails the test that takes it.
  * STATUS is pr_delivery's own projection of the record, unchanged, plus the office's block; the machine comes from the INJECTED factory only.
  * ELIGIBILITY IS READ FROM THE RECORD (its next action and the step's recorded state), never inferred from a typical sequence. Eligible: COMMIT, PUSH or PR_CREATE, when the record's next action
    is to advance that step and the step is pending. Not eligible, each with its own stated reason: the base refresh (a prerequisite this office does not perform), the pr_update observation (not one
    of the office's steps), a retry wait, a step recorded as executing, and a complete, revoked, expired or blocked delivery.
  * THE LOCAL CONFIRMATION is labelled as a local check, never as authority: the delivery was authorized once, by the human ceremony, for these steps; the confirmation is not recorded in the
    delivery record.
  * THE CONFIRMATION CHECK binds a confirmation to the displayed delivery id, revision, candidate digest and step, and refuses anything else BEFORE any effect: another delivery, a changed candidate,
    a stale revision, and a confirmation for a different step (a confirmation that would satisfy the NEXT step is refused).
  * THE WINDOW, AND THE GUARD THAT CLOSES IT. Between the adapter's check and the single-step method's own read, a concurrent driver (the CLI's advance, or a second adapter instance) can change
    which step the record selects. The PREVENTION is pr_delivery's expected-step guard: the confirmation is handed to the single-step method, which checks it against the record IT loads, before
    any branch, and refuses a different step. The stand-in here applies the REAL guard function with pr_delivery's REAL step_for_phase; the real method's signature and order are read from
    pr_delivery itself. The adapter's own check before the call, and its re-read after, are ADDITIONAL checks, never the prevention.
  * ONE CONFIRMED STEP: the adapter hands the confirmation to the guard exactly once; returns the step's OWN receipt, copied from the record; reports the guard's refusal as the guard's; and, as a
    defence, never reports as done a step the record does not show. A demonstrated advancement keeps the authorization revision unchanged: it is a re-authorization number, not a write counter.
  * THE ADAPTER'S POSITIVE CONSTRAINTS, by name: one import statement of pr_delivery, of exactly authorization, boundary, cli and machine; the only cli name is build_machine, referenced once,
    inside the production factory; the only machine names are MachineError, PROBLEM_EXPECTED_STEP_REFUSED and OUTCOME_ADVANCED; exactly ONE advance_once call, with expected=; no advance, revoke,
    minting or ceremony name, no direct construction of a transport, machine, store or boundary, and no process, socket or HTTP module.
"""

import ast
import copy
import inspect
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pr_delivery import authorization as auth  # noqa: E402
from pr_delivery import boundary  # noqa: E402
from pr_delivery import cli  # noqa: E402
from pr_delivery import machine as machine_module  # noqa: E402
from pr_delivery import transport as transport_module  # noqa: E402

from office_delivery import adapter as office  # noqa: E402

ADAPTER_FILE = REPO_ROOT / "office_delivery" / "adapter.py"
NOW = 1_800_000_000
DELIVERY = "prd-0123456789abcdef0123456789abcdef"
CANDIDATE = "c" * 64


class _Tripwire(Exception):
    pass


def _tripwire(*_args, **_kwargs):
    raise _Tripwire("a test path reached the REAL delivery transport")


class _TripwireTransport(object):
    def __init__(self, *_args, **_kwargs):
        raise _Tripwire("a test path constructed the REAL DeliveryTransport")


_saved = {}


def setUpModule():
    _saved["transport"] = transport_module.DeliveryTransport
    _saved["cli_transport"] = cli.DeliveryTransport
    _saved["build_machine"] = cli.build_machine
    transport_module.DeliveryTransport = _TripwireTransport
    cli.DeliveryTransport = _TripwireTransport
    cli.build_machine = _tripwire


def tearDownModule():
    transport_module.DeliveryTransport = _saved["transport"]
    cli.DeliveryTransport = _saved["cli_transport"]
    cli.build_machine = _saved["build_machine"]


def _receipt(step, state=auth.RECEIPT_SUCCEEDED):
    return {"receipt_id": "rcpt-%s" % step.lower(), "state": state, "observed": {"step": step}}


def record(mode=auth.MODE_PULL_REQUEST, phase=auth.PHASE_BASE_CURRENT, revision=3, states=None, revoked=False, expires_at=NOW + 3600, blocker=None, candidate=CANDIDATE):
    """A delivery record with every field pr_delivery's status projection reads. Steps before the phase are succeeded with a receipt; the rest pending, unless `states` says otherwise."""
    order = auth.PR_UPDATE_STEPS if mode == auth.MODE_PR_UPDATE else auth.STEPS
    reached = {
        auth.PHASE_AUTHORIZED: 0, auth.PHASE_BASE_CURRENT: 1, auth.PHASE_COMMITTED: 2, auth.PHASE_PUSHED: 3, auth.PHASE_PR_OPENED: 4,
    }
    done = reached.get(phase, 0) if mode == auth.MODE_PULL_REQUEST else {auth.PHASE_AUTHORIZED: 0, auth.PHASE_COMMITTED: 1, auth.PHASE_PUSHED: 2}.get(phase, 0)
    steps = {}
    for step in auth.STEPS:
        if step not in order:
            steps[step] = {"state": auth.STEP_NOT_NEEDED, "receipt": None, "voided": []}
        elif order.index(step) < done:
            steps[step] = {"state": auth.STEP_SUCCEEDED, "receipt": _receipt(step), "voided": []}
        else:
            steps[step] = {"state": auth.STEP_PENDING, "receipt": None, "voided": []}
    for step, state in (states or {}).items():
        steps[step]["state"] = state
        if state == auth.STEP_FAILED_RETRYABLE:
            steps[step]["receipt"] = _receipt(step, auth.RECEIPT_FAILED_RETRYABLE)
        if state == auth.STEP_EXECUTING:
            steps[step]["receipt"] = _receipt(step, auth.RECEIPT_EXECUTING)
    document = {
        "delivery_id": DELIVERY, "revision": revision, "phase": phase, "mode": mode,
        "evidence": {
            "independent_verification": {"command_argv": ["make", "check"], "exit_status": 0, "log_sha256": "a" * 64, "base_oid": "b" * 40},
            "engineering_complete": {"task_id": "t-1", "status": "COMPLETE"},
            "reviewer_approve": {"decision": "APPROVE", "round": 1},
        },
        "steps": steps,
        "human_authorization": {"identity": "travis", "source": "local_terminal", "authorized_at": NOW - 60},
        "expiration": {"expires_at": expires_at},
        "revocation": {"revoked": revoked},
        "allowed_actions": list(order),
        "repository": {"repository_url": "https://github.com/Example/Repo"},
        "source": {"branch": "feature"},
        "target_base": {"branch": "main"},
        "candidate": {"identity_digest_sha256": candidate},
        "original_baseline": {"commit_sha": "d" * 40},
        "base_state": {"current_base_oid": "d" * 40, "refreshed_at": None, "advance_after_commit": False},
        "pull_request": None,
        "blocker": blocker,
    }
    if mode == auth.MODE_PR_UPDATE:
        document["pull_request_number"] = 7
    return document


class InertMachine(object):
    """Loads from a dict and tells the time. It has NO single-step or advance method: status needs neither."""

    def __init__(self, records):
        self.records = records
        self.loads = 0

    def load(self, delivery_id):
        self.loads += 1
        return copy.deepcopy(self.records[delivery_id])

    def clock(self):
        return NOW


def factory_for(records):
    built = []

    def build():
        machine = InertMachine(records)
        built.append(machine)
        return machine

    return build, built


def binding(step, revision=3, delivery_id=DELIVERY, candidate=CANDIDATE):
    return {"delivery_id": delivery_id, "revision": revision, "candidate_identity_digest_sha256": candidate, "step": step}


class TripwireTests(unittest.TestCase):
    def test_the_real_transport_and_build_machine_are_unreachable_in_this_module(self):
        with self.assertRaises(_Tripwire):
            cli.build_machine()
        with self.assertRaises(_Tripwire):
            transport_module.DeliveryTransport()
        with self.assertRaises(_Tripwire):
            cli.DeliveryTransport()


class StatusTests(unittest.TestCase):
    def test_status_is_pr_deliverys_own_projection_unchanged_plus_the_office_block(self):
        records = {DELIVERY: record()}
        build, built = factory_for(records)
        answer = office.OfficeDeliveryAdapter(build).status(DELIVERY)
        self.assertEqual(answer["delivery"], boundary.project_status(records[DELIVERY], NOW))
        self.assertEqual(set(answer), {"delivery", "office"})
        self.assertEqual(len(built), 1)

    def test_the_machine_comes_only_from_the_injected_factory(self):
        with self.assertRaises(TypeError):
            office.OfficeDeliveryAdapter(None)
        records = {DELIVERY: record()}
        build, built = factory_for(records)
        adapter = office.OfficeDeliveryAdapter(build)
        adapter.status(DELIVERY)
        adapter.status(DELIVERY)
        self.assertEqual([m.loads for m in built], [1, 1])


class EligibilityTests(unittest.TestCase):
    def office_block(self, rec):
        build, _ = factory_for({DELIVERY: rec})
        return office.OfficeDeliveryAdapter(build).status(DELIVERY)["office"]

    def test_commit_push_and_pr_create_are_each_eligible_where_the_record_says_they_are_next_and_pending(self):
        for phase, step in ((auth.PHASE_BASE_CURRENT, auth.STEP_COMMIT), (auth.PHASE_COMMITTED, auth.STEP_PUSH), (auth.PHASE_PUSHED, auth.STEP_PR_CREATE)):
            block = self.office_block(record(phase=phase))
            self.assertEqual(block["eligible"], {"step": step, "binding": binding(step)}, phase)
            self.assertIsNone(block["not_eligible"], phase)

    def test_a_pr_update_delivery_offers_commit_then_push(self):
        self.assertEqual(self.office_block(record(mode=auth.MODE_PR_UPDATE, phase=auth.PHASE_AUTHORIZED))["eligible"]["step"], auth.STEP_COMMIT)
        self.assertEqual(self.office_block(record(mode=auth.MODE_PR_UPDATE, phase=auth.PHASE_COMMITTED))["eligible"]["step"], auth.STEP_PUSH)

    def test_the_base_refresh_is_a_prerequisite_this_office_does_not_perform_and_says_so(self):
        block = self.office_block(record(phase=auth.PHASE_AUTHORIZED))
        self.assertIsNone(block["eligible"])
        self.assertEqual(block["not_eligible"], {"reason": "base_refresh_unmet", "step": auth.STEP_BASE_REFRESH, "detail": office.BASE_REFRESH_UNMET})
        self.assertIn("cannot start", office.BASE_REFRESH_UNMET)
        self.assertIn("attempts every remaining step", office.BASE_REFRESH_UNMET)

    def test_the_pr_update_observation_is_not_one_of_the_offices_steps(self):
        block = self.office_block(record(mode=auth.MODE_PR_UPDATE, phase=auth.PHASE_PUSHED))
        self.assertIsNone(block["eligible"])
        self.assertEqual(block["not_eligible"]["reason"], "observation_not_an_office_step")

    def test_a_retry_wait_and_a_step_recorded_as_executing_are_not_eligible(self):
        waiting = self.office_block(record(phase=auth.PHASE_BASE_CURRENT, states={auth.STEP_COMMIT: auth.STEP_FAILED_RETRYABLE}))
        self.assertIsNone(waiting["eligible"])
        self.assertEqual(waiting["not_eligible"]["reason"], "waiting_to_retry")
        executing = self.office_block(record(phase=auth.PHASE_BASE_CURRENT, states={auth.STEP_COMMIT: auth.STEP_EXECUTING}))
        self.assertIsNone(executing["eligible"])
        self.assertEqual(executing["not_eligible"]["reason"], "step_recorded_executing")

    def test_a_step_the_record_holds_in_any_state_but_pending_is_not_offered_even_where_the_phase_points_at_it(self):
        for state in (auth.STEP_BLOCKED, auth.STEP_SUCCEEDED, auth.STEP_NOT_NEEDED):
            block = self.office_block(record(phase=auth.PHASE_BASE_CURRENT, states={auth.STEP_COMMIT: state}))
            self.assertIsNone(block["eligible"], state)
            self.assertEqual(block["not_eligible"]["reason"], "step_not_pending", state)

    def test_a_complete_revoked_expired_or_blocked_delivery_offers_nothing(self):
        cases = (
            (record(phase=auth.PHASE_COMPLETE), "complete"),
            (record(revoked=True), "revoked"),
            (record(expires_at=NOW - 1), "expired"),
            (record(phase=auth.PHASE_BLOCKED, blocker={"problem": "candidate_moved", "detail": "x", "recorded_at": NOW}), "blocked"),
        )
        for rec, reason in cases:
            block = self.office_block(rec)
            self.assertIsNone(block["eligible"], reason)
            self.assertEqual(block["not_eligible"]["reason"], reason)

    def test_the_local_confirmation_is_labelled_as_a_local_check_never_as_authority(self):
        block = self.office_block(record())
        self.assertEqual(
            block["local_confirmation_note"],
            "This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps."
            " This confirmation is not recorded in the delivery record.",
        )
        self.assertEqual(block["action_steps"], [auth.STEP_COMMIT, auth.STEP_PUSH, auth.STEP_PR_CREATE])

    def test_the_office_says_it_performs_one_confirmed_step_through_the_guard_and_that_the_confirmation_is_never_written(self):
        block = self.office_block(record())
        self.assertIs(block["performs_steps"], True)
        self.assertEqual(block["performs_steps_note"], office.PERFORMS_ONE_CONFIRMED_STEP)
        for words in ("ONE confirmed step", "expected-step guard", "before any effect", "compared, never written", "not recorded in the delivery record"):
            self.assertIn(words, office.PERFORMS_ONE_CONFIRMED_STEP)
        self.assertNotIn("authoriz", office.PERFORMS_ONE_CONFIRMED_STEP.lower(), "the step note claims no authority; the ceremony is the only authority")


class ConfirmationCheckTests(unittest.TestCase):
    def test_the_exact_displayed_binding_is_admitted(self):
        self.assertEqual(office.check_confirmation(record(), NOW, binding(auth.STEP_COMMIT)), {"admitted": True, "step": auth.STEP_COMMIT})

    def test_a_confirmation_for_the_next_step_is_refused(self):
        answer = office.check_confirmation(record(), NOW, binding(auth.STEP_PUSH))
        self.assertEqual((answer["admitted"], answer["refusal"]), (False, "step_mismatch"))

    def test_another_delivery_a_changed_candidate_and_a_stale_revision_are_each_refused(self):
        for confirmation, refusal in (
            (binding(auth.STEP_COMMIT, delivery_id="prd-" + "f" * 32), "other_delivery"),
            (binding(auth.STEP_COMMIT, candidate="e" * 64), "candidate_changed"),
            (binding(auth.STEP_COMMIT, revision=2), "stale_revision"),
        ):
            answer = office.check_confirmation(record(), NOW, confirmation)
            self.assertEqual((answer["admitted"], answer["refusal"]), (False, refusal))

    def test_a_confirmation_admitted_for_commit_is_refused_once_the_record_moves_on(self):
        moved = record(phase=auth.PHASE_COMMITTED)  # ordinary advancement: the authorization revision is unchanged
        answer = office.check_confirmation(moved, NOW, binding(auth.STEP_COMMIT))
        self.assertEqual((answer["admitted"], answer["refusal"]), (False, "step_mismatch"))

    def test_nothing_is_admitted_where_nothing_is_eligible(self):
        answer = office.check_confirmation(record(phase=auth.PHASE_AUTHORIZED), NOW, binding(auth.STEP_BASE_REFRESH))
        self.assertEqual((answer["admitted"], answer["refusal"]), (False, "not_eligible"))

    def test_a_malformed_confirmation_is_refused(self):
        for bad in (None, "COMMIT", {}, dict(binding(auth.STEP_COMMIT), extra=1), dict(binding(auth.STEP_COMMIT), revision="3")):
            answer = office.check_confirmation(record(), NOW, bad)
            self.assertEqual((answer["admitted"], answer["refusal"]), (False, "malformed"), bad)

    def test_a_confirmation_with_keys_of_mixed_types_is_REFUSED_as_malformed_never_raised(self):
        # round 42, advisory 3: an int key beside the string keys made sorted() raise before anything was refused
        for bad in ({**binding(auth.STEP_COMMIT), 1: "x"}, {1: "x", "delivery_id": DELIVERY},
                    {None: 1, "step": auth.STEP_COMMIT, "revision": 3, "delivery_id": DELIVERY, "candidate_identity_digest_sha256": CANDIDATE}):
            answer = office.check_confirmation(record(), NOW, bad)
            self.assertEqual((answer["admitted"], answer["refusal"]), (False, "malformed"), bad)


class SingleStepStandIn(object):
    """The single-step method as pr_delivery's now is: the step comes from ITS OWN read, through pr_delivery's real step_for_phase, and an `expected` binding is checked against THAT read by
    pr_delivery's REAL guard function before anything is acted on. `before_own_read` is the handshake at which a concurrent driver acts: after the adapter's check, before this read."""

    def __init__(self, records, before_own_read=None):
        self.records = records
        self.before_own_read = before_own_read
        self.acted = []

    def advance_once(self, delivery_id, *, expected=None):
        if self.before_own_read is not None:
            self.before_own_read()
        own = self.records[delivery_id]
        if expected is not None:
            machine_module._require_expected_step(own, expected)
        self.acted.append(auth.step_for_phase(own["mode"], own["phase"]))


class RaceDemonstrationTests(unittest.TestCase):
    def test_the_real_single_step_method_checks_the_guard_on_its_own_read_before_it_selects(self):
        parameters = inspect.signature(machine_module.DeliveryMachine.advance_once).parameters
        self.assertEqual(list(parameters), ["self", "delivery_id", "expected"])
        self.assertEqual((parameters["expected"].kind, parameters["expected"].default), (inspect.Parameter.KEYWORD_ONLY, None))
        source = inspect.getsource(machine_module.DeliveryMachine.advance_once)
        own_read = source.index("record = self.load(delivery_id)")
        guard = source.index("_require_expected_step(record, expected)")
        selection = source.index('step = auth.step_for_phase(record["mode"], record["phase"])')
        self.assertLess(own_read, guard)
        self.assertLess(guard, selection)

    def test_THE_WINDOW_CLOSED_the_check_admits_COMMIT_a_concurrent_driver_moves_the_record_and_the_guard_refuses_before_anything_is_acted_on(self):
        records = {DELIVERY: record(phase=auth.PHASE_BASE_CURRENT)}
        confirmation = binding(auth.STEP_COMMIT)
        self.assertEqual(office.check_confirmation(records[DELIVERY], NOW, confirmation), {"admitted": True, "step": auth.STEP_COMMIT})

        def concurrent_driver():
            records[DELIVERY] = record(phase=auth.PHASE_COMMITTED)  # ordinary advancement: the authorization revision is unchanged

        machine = SingleStepStandIn(records, before_own_read=concurrent_driver)
        with self.assertRaises(machine_module.MachineError) as refused:
            machine.advance_once(DELIVERY, expected=confirmation)
        self.assertEqual(refused.exception.problem, machine_module.PROBLEM_EXPECTED_STEP_REFUSED)
        self.assertEqual(machine.acted, [], "no step was acted on")

    def test_CONTROL_with_no_concurrent_driver_the_confirmed_step_is_the_one_acted_on(self):
        records = {DELIVERY: record(phase=auth.PHASE_BASE_CURRENT)}
        confirmation = binding(auth.STEP_COMMIT)
        self.assertTrue(office.check_confirmation(records[DELIVERY], NOW, confirmation)["admitted"])
        machine = SingleStepStandIn(records)
        machine.advance_once(DELIVERY, expected=confirmation)
        self.assertEqual(machine.acted, [auth.STEP_COMMIT])

    def test_a_move_BEFORE_the_check_is_refused_by_the_check_itself(self):
        moved = record(phase=auth.PHASE_COMMITTED)  # ordinary advancement: the authorization revision is unchanged
        self.assertEqual(office.check_confirmation(moved, NOW, binding(auth.STEP_COMMIT))["refusal"], "step_mismatch")


class SteppingMachine(InertMachine):
    """An inert machine whose single-step method takes `expected` as pr_delivery's does and records every call. Scripted: raise an error; or mark the confirmed step attempted in the record (state
    succeeded, with a receipt); or claim an advance while the record shows nothing ("nothing")."""

    def __init__(self, records, behaviour="step", error=None):
        super(SteppingMachine, self).__init__(records)
        self.calls = []
        self.behaviour = behaviour
        self.error = error

    def advance_once(self, delivery_id, *, expected=None):
        self.calls.append((delivery_id, expected))
        if self.error is not None:
            raise self.error
        if self.behaviour == "step":
            entry = self.records[delivery_id]["steps"][expected["step"]]
            entry["state"], entry["receipt"] = auth.STEP_SUCCEEDED, _receipt(expected["step"])
        return machine_module.OUTCOME_ADVANCED


class AdvanceTests(unittest.TestCase):
    def run_once(self, confirmation, **machine):
        records = {DELIVERY: record(phase=auth.PHASE_BASE_CURRENT)}
        stepping = SteppingMachine(records, **machine)
        return office.OfficeDeliveryAdapter(lambda: stepping).advance_once(DELIVERY, confirmation), stepping, records

    def test_an_admitted_confirmation_is_handed_to_the_guard_exactly_once_and_the_steps_own_receipt_is_returned(self):
        confirmation = binding(auth.STEP_COMMIT)
        result, stepping, records = self.run_once(confirmation)
        self.assertEqual(stepping.calls, [(DELIVERY, confirmation)])
        self.assertIsNot(stepping.calls[0][1], confirmation, "the guard gets a copy, not the caller's object")
        self.assertEqual((result["advanced"], result["step"], result["outcome"]), (True, auth.STEP_COMMIT, machine_module.OUTCOME_ADVANCED))
        own = records[DELIVERY]["steps"][auth.STEP_COMMIT]["receipt"]
        self.assertEqual(result["receipt"], own)
        self.assertIsNot(result["receipt"], own, "the receipt is copied from the record, never composed")

    def test_a_confirmation_the_office_check_refuses_never_reaches_the_single_step_method(self):
        result, stepping, _ = self.run_once(binding(auth.STEP_PUSH))
        self.assertEqual((result["advanced"], result["refusal"], result["receipt"]), (False, "step_mismatch", None))
        self.assertEqual(stepping.calls, [])

    def test_the_guards_refusal_is_reported_as_the_guards_and_nothing_is_presented_as_done(self):
        error = machine_module.MachineError("moved", machine_module.PROBLEM_EXPECTED_STEP_REFUSED)
        result, _, _ = self.run_once(binding(auth.STEP_COMMIT), error=error)
        self.assertEqual((result["advanced"], result["refusal"], result["receipt"]), (False, "guard_refused", None))

    def test_any_other_machine_refusal_is_reported_with_its_own_problem(self):
        error = machine_module.MachineError("gone", machine_module.PROBLEM_MISSING_RECORD)
        result, _, _ = self.run_once(binding(auth.STEP_COMMIT), error=error)
        self.assertEqual((result["advanced"], result["refusal"], result["problem"]), (False, "machine_refused", machine_module.PROBLEM_MISSING_RECORD))

    def test_DEFENCE_a_step_the_record_does_not_show_as_attempted_is_never_reported_as_done(self):
        result, _, _ = self.run_once(binding(auth.STEP_COMMIT), behaviour="nothing")
        self.assertEqual((result["advanced"], result["refusal"], result["receipt"]), (False, "not_shown_in_record", None))


def _adapter_tree():
    return ast.parse(ADAPTER_FILE.read_text())


class PositiveConstraintTests(unittest.TestCase):
    def test_one_import_statement_of_pr_delivery_of_exactly_authorization_boundary_cli_and_machine(self):
        statements = []
        for node in ast.walk(_adapter_tree()):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "pr_delivery" for a in node.names):
                statements.append(("import", sorted(a.name for a in node.names)))
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pr_delivery":
                statements.append((node.module, sorted(a.name for a in node.names)))
        self.assertEqual(statements, [("pr_delivery", ["authorization", "boundary", "cli", "machine"])])

    def test_the_only_machine_names_are_its_error_its_guard_problem_and_one_outcome(self):
        names = {n.attr for n in ast.walk(_adapter_tree()) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "machine"}
        self.assertEqual(names, {"MachineError", "PROBLEM_EXPECTED_STEP_REFUSED", "OUTCOME_ADVANCED"})

    def test_exactly_one_single_step_call_with_expected_inside_the_adapters_own_step_method(self):
        tree = _adapter_tree()
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "advance_once"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(([k.arg for k in calls[0].keywords], len(calls[0].args)), (["expected"], 1))
        adapter_class = [c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "OfficeDeliveryAdapter"][0]
        method = [f for f in adapter_class.body if isinstance(f, ast.FunctionDef) and f.name == "advance_once"][0]
        self.assertIn(calls[0], list(ast.walk(method)))

    def test_the_only_cli_name_is_build_machine_referenced_once_inside_the_production_factory(self):
        tree = _adapter_tree()
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "cli"]
        self.assertEqual([u.attr for u in uses], ["build_machine"])
        production = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "production_adapter"]
        self.assertEqual(len(production), 1)
        self.assertIn(uses[0], list(ast.walk(production[0])))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.func is uses[0]]
        self.assertEqual(calls, [], "build_machine is passed as the factory, never called here")

    def test_no_advance_all_revoke_minting_ceremony_construction_process_or_network_name(self):
        forbidden = {
            "advance", "revoke", "_mint", "authorize_cmd", "present_dots_cmd", "attest_dots_cmd", "assemble_authority", "status_cmd", "advance_cmd", "revoke_cmd",
            "DeliveryTransport", "DeliveryMachine", "DeliveryStore", "PrDeliveryBoundary", "store_directory", "add_delivery",
        }
        found = set()
        for node in ast.walk(_adapter_tree()):
            if isinstance(node, ast.Name) and node.id in forbidden:
                found.add(node.id)
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                found.add(node.attr)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                roots = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
                found.update(r for r in roots if r in {"subprocess", "socket", "http", "socketserver", "pty", "urllib", "os"})
        self.assertEqual(found, set())


if __name__ == "__main__":
    unittest.main()
