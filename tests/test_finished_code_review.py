"""I1, LAYER 1: the record-side admission of a FINISHED (COMPLETED) Mission to the live code review.

Hermetic by construction: this module imports PRODUCT modules only and reads tests/test_static.py as TEXT. It imports no pre-existing test module and no test class by
name, starts no process, uses no git, no network, no home directory and no environment. Every input is a plain dict or an injected fake.

What it reaches: the review module's finished branch (the provider is a fake), its release-time comparison, the refusal mapping, the vocabulary fence, the import fences,
and the evidence library's new entry for every refusal that comes BEFORE the Mission store is loaded (the store is a fake that is unavailable, so "reached the store"
is observable). What it does NOT reach (owed to a real-fixture layer that is not approved here): the steps after the store load, a real COMPLETED record, a real owner
answer, and the entry's equality of behaviour with the evidence library's own admission on real records.

I1 ALONE DOES NOT MAKE THE CONTROL WORK: no offer can exist for a finished Mission until the workspace witness and the offered flow are integrated.
"""
import ast
import inspect
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mission import record as mission_record  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from target_runtime import artifact_content  # noqa: E402
from target_runtime import code_review  # noqa: E402
from target_runtime import run_association  # noqa: E402

SRC, OTHER_SRC, OBS_SRC = "mc1", "mc2", "tn1"
NATIVE = "mn-" + "a" * 32
OTHER_NATIVE = "mn-" + "b" * 32
REF = "%s:%s" % (SRC, NATIVE)
TASK = "20261028-120000-abc123"
OTHER_TASK = "20261028-130000-def456"
WS = "/ws/finished"
JOURNAL, IDENT, CURSOR = "1" * 64, "2" * 64, "3" * 64
NOW = 1000


NOT_ESTABLISHED = "finished_execution_not_established"
UNVERIFIABLE = "finished_ownership_unverifiable"
CONTESTED = "finished_ownership_contested"
#: THE DECISION, written independently of the table under test (the Lead accepted three internal reasons; each refusal of the evidence library's
#: admission maps to an existing review code or to one of them). KEY: the evidence library's own code. VALUE: what the review route says.
EXPECTED_MAPPING = {
    artifact_content.PROBLEM_NOT_CORROBORATED: code_review.PROBLEM_NOT_CORROBORATED,
    artifact_content.PROBLEM_MISSION_NOT_LISTED: code_review.PROBLEM_MISSION_NOT_LISTED,
    artifact_content.PROBLEM_MISSION_AMBIGUOUS: code_review.PROBLEM_MISSION_AMBIGUOUS,
    artifact_content.PROBLEM_SOURCE_KIND: code_review.PROBLEM_SOURCE_KIND,
    artifact_content.PROBLEM_NOT_BOUND: NOT_ESTABLISHED,
    artifact_content.PROBLEM_EXECUTION_UNRESOLVED: code_review.PROBLEM_UNRESOLVED,
    artifact_content.PROBLEM_STATE_NOT_OBSERVED: UNVERIFIABLE,
    artifact_content.PROBLEM_CURSOR_NOT_LISTED: UNVERIFIABLE,
    artifact_content.PROBLEM_REGISTRY: code_review.PROBLEM_REGISTRY,
    artifact_content.PROBLEM_SOURCE_NOT_REGISTERED: code_review.PROBLEM_SOURCE_NOT_REGISTERED,
    artifact_content.PROBLEM_STORE: UNVERIFIABLE,
    artifact_content.PROBLEM_MISSION_ABSENT: UNVERIFIABLE,
    artifact_content.PROBLEM_CURSOR_MOVED: UNVERIFIABLE,
    artifact_content.PROBLEM_BINDING_CHANGED: UNVERIFIABLE,
    artifact_content.PROBLEM_WORKSPACE_UNRESOLVED: code_review.PROBLEM_UNRESOLVED,
    artifact_content.PROBLEM_BUDGET: code_review.PROBLEM_BUDGET,
    artifact_content.PROBLEM_HISTORICAL_IDENTITY: NOT_ESTABLISHED,
    artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE: UNVERIFIABLE,
    artifact_content.PROBLEM_HISTORICAL_CONFLICT: CONTESTED,
    artifact_content.PROBLEM_REGISTRATION_CHANGED: code_review.PROBLEM_REGISTRATION_CHANGED,
    artifact_content.PROBLEM_HISTORICAL_CHANGED: UNVERIFIABLE,
}


def registration(source_id=SRC, kind=contract.KIND_MISSION_CORE, store="/store/" + SRC):
    return {"source_id": source_id, "kind": kind, "store_dir": store, "producer": "local_request", "max_age_seconds": 60}


def block(reg, count=1, health=contract.HEALTH_FRESH):
    return dict(reg, health=health, record_count=count)


def fresh(response, now):
    return contract.HEALTH_FRESH, None


def clock():
    return NOW


def mission(state=mission_record.STATE_COMPLETED, standing=contract.STANDING_TERMINAL, binding=("not_applicable", "mission_not_active"),
            task=TASK, workspace=WS, ref=REF, source=SRC, native=NATIVE, **execution):
    m = {
        "mission_ref": ref, "source_id": source, "native_id": native,
        "lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE, "state": state, "standing": standing},
        "binding": {"state": binding[0], "reason": binding[1], "task_id": None},
        "execution": {"task_id": task, "workspace_realpath": workspace, "consistent": True, "record_ambiguous": False,
                      "run_started": True, "receipt_recorded": True, "identity_source": "start_result"},
        "producer": {"declared": "local_request", "recorded": {"transport": "local", "principal_kind": "local_process_user"}},
        "mission_state": {
            "state": contract_v2.MISSION_STATE_OBSERVED, "problem": None,
            "report": {"provenance": {"journal_digest_sha256": JOURNAL}},
            contract_v2.EXECUTION_IDENTITY_KEY: {"state": contract_v2.EXECUTION_IDENTITY_ESTABLISHED, "problem": None,
                                                 "identity_sha256": IDENT, "cursor_sha256": CURSOR}},
    }
    m["execution"].update(execution)
    return m


def active_mission():
    m = mission(state="RUNNING", standing=contract.STANDING_ACTIVE, binding=(contract.BINDING_BOUND, "bound"))
    m["binding"]["task_id"] = TASK
    return m


def answer(missions, sources=None):
    sources = [block(registration())] if sources is None else sources
    return {"response": {"missions": missions, "sources": sources}}


def observation_row(task=TASK, workspace=WS, state="COMPLETE", **over):
    row = mission(state=state, task=task, workspace=workspace, ref="%s:%s" % (OBS_SRC, "tk-1"), source=OBS_SRC, native="tk-1")
    row["lifecycle"] = {"model": artifact_content.OBSERVATION_LIFECYCLE_MODEL, "state": state, "standing": contract.STANDING_TERMINAL}
    row["producer"] = {"declared": "herdr_task", "recorded": {"schema": artifact_content.OBSERVATION_SCHEMA,
                                                              "schema_version": artifact_content.OBSERVATION_SCHEMA_VERSION}}
    row["execution"]["identity_source"] = artifact_content.OBSERVATION_IDENTITY_SOURCE
    row.update(over)
    return row


class FakeRegistry(object):
    def __init__(self, sources, error=None):
        self.sources, self.error = sources, error

    def load_measured(self, absent_is_empty=True):
        if self.error is not None:
            raise registry_module.RegistryError(self.error, "fake registry error")
        return {"sources": [dict(s) for s in self.sources]}, 120


class FakeMissionState(object):
    """The Mission Core load boundary, replaced by one that is UNAVAILABLE: reaching it is observable, and nothing after it runs."""

    class _Unavailable(Exception):
        def __init__(self, reason):
            Exception.__init__(self, reason)
            self.reason = reason

    class _BudgetExhausted(_Unavailable):
        pass

    def __init__(self):
        self.loads = []

    def load_store_document(self, store_dir, budget):
        self.loads.append(store_dir)
        raise self._Unavailable("fake store unavailable")


def entry(missions, sources=None, registry=None, state=None, fresh_fn=fresh):
    state = FakeMissionState() if state is None else state
    held = answer(missions, sources)
    registry = FakeRegistry([registration()]) if registry is None else registry
    return artifact_content.historical_execution(state, registry, held, fresh_fn, clock, REF), state


class Provider(object):
    """A fake finished-execution provider: records its calls, returns what it was given."""

    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, held, mission_ref):
        self.calls.append((held, mission_ref))
        return self.result


#: The run intent facts the record-side entry hands the review route for the workspace-side witness (I2).
INTENT = {"revision": 3, "proposal_digest_sha256": "d" * 64, "recorded_at": 1000}


def proof(task=TASK, workspace=WS, identity=IDENT):
    reg = registration()
    return {"basis": artifact_content.EXECUTION_HISTORICAL, "source_id": SRC, "native_id": NATIVE, "task_id": task,
            "workspace": workspace, "journal": JOURNAL, "identity": identity, "registration": reg, "universe": [reg], "observations": [],
            "mission_id": NATIVE, "intent": dict(INTENT)}


class WitnessedWorkspace(object):
    """A real temporary workspace whose OWN task record (``.herd/state/task.json``) names the run ``proof()`` is about: since I2 the finished branch requires it."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self.tmp.name)
        directory = os.path.join(self.path, ".herd", "state")
        os.makedirs(directory)
        with open(self.task_path(), "w") as handle:
            handle.write(json.dumps({
                "id": TASK, "started_at": INTENT["recorded_at"],
                "description": run_association.handoff_prefix(NATIVE, INTENT["revision"], INTENT["proposal_digest_sha256"]) + " The rest of the handoff."}))

    def task_path(self):
        return os.path.join(self.path, ".herd", "state", "task.json")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()


def source_of(path):
    return (REPO_ROOT / path).read_text()


def pin_block(label):
    """A pin block of tests/test_static.py, VERBATIM from the file: from its '# (label)' line to the next '# (12x)' label line."""
    text = source_of("tests/test_static.py")
    start = re.search(r"^# \(%s\)" % re.escape(label), text, re.M)
    assert start is not None, "no pin block %s in tests/test_static.py" % label
    nxt = re.search(r"^# \(12[a-z]\)", text[start.end():], re.M)
    return text[start.start():start.end() + (nxt.start() if nxt else len(text) - start.end())]


def static_literal(name):
    for node in ast.parse(source_of("tests/test_static.py")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError("no literal %s in tests/test_static.py" % name)


def reachable_problem_constants(module_source, start):
    """The PROBLEM_* constant names loaded inside every module-level function reachable (by name) from ``start``."""
    tree = ast.parse(module_source)
    functions = dict((n.name, n) for n in tree.body if isinstance(n, ast.FunctionDef))
    seen, todo, names = set(), [start], set()
    while todo:
        name = todo.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for n in ast.walk(functions[name]):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if n.id.startswith("PROBLEM_"):
                    names.add(n.id)
                elif n.id in functions:
                    todo.append(n.id)
    return names, seen


class Reached(Exception):
    """Raised by the sentinel transport the moment the release-time comparison has passed and the workspace is observed."""


class SentinelTransport(object):
    def __getattr__(self, name):
        def reached(*args, **kwargs):
            raise Reached(name)
        return reached


# =====================================================================================================================
class FinishedAdmissionEntryTests(unittest.TestCase):
    """The evidence library's new additive entry, for every refusal that comes before the Mission store is loaded."""

    def setUp(self):
        self.assertTrue(hasattr(artifact_content, "historical_execution"),
                        "FEATURE MISSING: artifact_content.historical_execution does not exist")

    def refused(self, result, problem):
        proof_, charged, refusal_ = result
        self.assertIsNone(proof_, "an admitted proof where a refusal was expected: %r" % (refusal_,))
        self.assertIsNotNone(refusal_, "neither a proof nor a refusal")
        self.assertEqual(refusal_[0], problem, refusal_)
        self.assertIsInstance(refusal_[1], str)
        self.assertIsInstance(charged, int)

    def test_a_listing_that_passes_everything_before_the_store_reaches_the_store_load(self):
        result, state = entry([mission()])
        self.refused(result, artifact_content.PROBLEM_STORE)
        self.assertEqual(state.loads, ["/store/" + SRC], "every listing and registry check passed, so the store load is the next step")

    def test_no_held_answer_or_a_stale_one_corroborates_nothing(self):
        state = FakeMissionState()
        result = artifact_content.historical_execution(state, FakeRegistry([registration()]), None, fresh, clock, REF)
        self.refused(result, artifact_content.PROBLEM_NOT_CORROBORATED)
        result, _ = entry([mission()], fresh_fn=lambda response, now: (contract.HEALTH_STALE, "response_stale"))
        self.refused(result, artifact_content.PROBLEM_NOT_CORROBORATED)

    def test_the_mission_must_be_listed_exactly_once(self):
        result, _ = entry([])
        self.refused(result, artifact_content.PROBLEM_MISSION_NOT_LISTED)
        result, _ = entry([mission(), mission()], sources=[block(registration(), count=2)])
        self.refused(result, artifact_content.PROBLEM_MISSION_AMBIGUOUS)

    def test_only_a_mission_core_record_is_admitted(self):
        m = mission()
        m["lifecycle"]["model"] = contract.LIFECYCLE_MODEL_WORKFLOW_STORE
        result, _ = entry([m])
        self.refused(result, artifact_content.PROBLEM_SOURCE_KIND)

    def test_every_terminal_state_but_completed_is_not_established(self):
        for state in (mission_record.STATE_BLOCKED, mission_record.STATE_CANCELLED, mission_record.STATE_CLOSED):
            with self.subTest(state=state):
                result, _ = entry([mission(state=state)])
                self.refused(result, artifact_content.PROBLEM_NOT_BOUND)

    def test_a_completed_mission_whose_binding_is_not_the_expected_not_applicable_is_refused(self):
        for binding in (("unverified", "snapshot_not_fresh"), ("disappeared", "task_not_observed"), ("not_applicable", "covered_by_canonical_record")):
            with self.subTest(binding=binding):
                result, _ = entry([mission(binding=binding)])
                self.refused(result, artifact_content.PROBLEM_NOT_BOUND)

    def test_an_unresolved_execution_is_refused_each_way(self):
        cases = (dict(task=None), dict(workspace=None), dict(consistent=False), dict(record_ambiguous=True),
                 dict(run_started=False), dict(receipt_recorded=False))
        for over in cases:
            with self.subTest(over=over):
                m = mission(**dict((k, v) for k, v in over.items() if k not in ("task", "workspace")))
                if "task" in over:
                    m["execution"]["task_id"] = None
                if "workspace" in over:
                    m["execution"]["workspace_realpath"] = None
                result, _ = entry([m])
                self.refused(result, artifact_content.PROBLEM_EXECUTION_UNRESOLVED)

    def test_a_mission_state_that_was_not_observed_is_refused(self):
        m = mission()
        m["mission_state"]["state"] = "unavailable"
        result, _ = entry([m])
        self.refused(result, artifact_content.PROBLEM_STATE_NOT_OBSERVED)

    def test_an_execution_identity_that_is_absent_or_not_established_is_refused(self):
        m = mission()
        del m["mission_state"][contract_v2.EXECUTION_IDENTITY_KEY]
        result, _ = entry([m])
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_IDENTITY)
        m = mission()
        m["mission_state"][contract_v2.EXECUTION_IDENTITY_KEY].update(state=contract_v2.EXECUTION_IDENTITY_NOT_ESTABLISHED,
                                                                      problem="execution_not_verified", identity_sha256=None, cursor_sha256=None)
        result, _ = entry([m])
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_IDENTITY)

    def test_a_bound_that_stands_on_the_active_basis_is_refused_and_not_admitted(self):
        # A listing whose binding is BOUND is judged by the evidence library on the ACTIVE basis (its journal against the cursor). Make the
        # listed cursor equal the journal digest so that basis is reached; the finished entry must then refuse it, never admit it.
        m = mission(binding=(contract.BINDING_BOUND, "bound"))
        m["binding"]["task_id"] = TASK
        m["mission_state"][contract_v2.EXECUTION_IDENTITY_KEY]["cursor_sha256"] = JOURNAL
        result, state = entry([m])
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_IDENTITY)
        self.assertEqual(state.loads, [], "an active-basis bound must never reach the store load")

    def test_the_late_resolution_case_is_refused_by_the_state_gate_on_its_own(self):
        # A late evidence-only resolution lives in run.late_resolution and leaves the Mission BLOCKED with no receipt task id.
        result, _ = entry([mission(state=mission_record.STATE_BLOCKED, task=None, receipt_recorded=False)])
        self.refused(result, artifact_content.PROBLEM_NOT_BOUND)

    def test_the_late_resolution_case_is_refused_by_the_absent_task_id_on_its_own(self):
        # With the state gate SATISFIED (a COMPLETED, not_applicable Mission), the absent receipt task id still refuses.
        result, _ = entry([mission(task=None, receipt_recorded=False)])
        self.refused(result, artifact_content.PROBLEM_EXECUTION_UNRESOLVED)

    def test_a_second_canonical_row_sharing_the_task_or_the_workspace_is_a_conflict(self):
        other_reg = registration(OTHER_SRC, store="/store/" + OTHER_SRC)
        for label, row in (("task", mission(ref="%s:%s" % (OTHER_SRC, OTHER_NATIVE), source=OTHER_SRC, native=OTHER_NATIVE, workspace="/ws/other")),
                           ("workspace", mission(task=OTHER_TASK, ref="%s:%s" % (OTHER_SRC, OTHER_NATIVE), source=OTHER_SRC, native=OTHER_NATIVE))):
            with self.subTest(shared=label):
                result, _ = entry([mission(), row], sources=[block(registration()), block(other_reg)],
                                  registry=FakeRegistry([registration(), other_reg]))
                self.refused(result, artifact_content.PROBLEM_HISTORICAL_CONFLICT)

    def test_a_supported_observation_of_this_execution_does_not_compete(self):
        obs_reg = registration(OBS_SRC, kind=contract.KIND_TASK_NATIVE, store="/store/" + OBS_SRC)
        result, state = entry([mission(), observation_row()], sources=[block(registration()), block(obs_reg)],
                              registry=FakeRegistry([registration(), obs_reg]))
        self.refused(result, artifact_content.PROBLEM_STORE)
        self.assertEqual(state.loads, ["/store/" + SRC], "the observation is admitted, so the next step is the store load")

    def test_an_observation_that_contradicts_the_completed_execution_competes(self):
        obs_reg = registration(OBS_SRC, kind=contract.KIND_TASK_NATIVE, store="/store/" + OBS_SRC)
        result, _ = entry([mission(), observation_row(state="RUNNING")], sources=[block(registration()), block(obs_reg)],
                          registry=FakeRegistry([registration(), obs_reg]))
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_CONFLICT)

    def test_a_source_that_is_not_fresh_or_not_fully_listed_makes_uniqueness_unverifiable(self):
        result, _ = entry([mission()], sources=[block(registration(), health=contract.HEALTH_STALE)])
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)
        result, _ = entry([mission()], sources=[block(registration(), count=2)])
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_the_registry_must_register_the_source_exactly_as_listed(self):
        result, _ = entry([mission()], registry=FakeRegistry([]))
        self.refused(result, artifact_content.PROBLEM_SOURCE_NOT_REGISTERED)
        result, _ = entry([mission()], registry=FakeRegistry([dict(registration(), store_dir="/store/elsewhere")]))
        self.refused(result, artifact_content.PROBLEM_REGISTRATION_CHANGED)

    def test_a_registry_universe_that_is_not_the_listed_one_makes_uniqueness_unverifiable(self):
        extra = registration("mc9", store="/store/mc9")
        result, _ = entry([mission()], registry=FakeRegistry([registration(), extra]))
        self.refused(result, artifact_content.PROBLEM_HISTORICAL_UNVERIFIABLE)

    def test_an_unreadable_registry_is_refused(self):
        result, _ = entry([mission()], registry=FakeRegistry([], error="registry_absent"))
        self.refused(result, artifact_content.PROBLEM_REGISTRY)

    def test_the_entry_never_raises_for_a_hostile_answer_shape(self):
        for held in ({"response": {}}, {"response": {"missions": [{}], "sources": []}}, {"response": {"missions": "x", "sources": None}}, {}):
            with self.subTest(held=held):
                try:
                    result = artifact_content.historical_execution(FakeMissionState(), FakeRegistry([registration()]), held, fresh, clock, REF)
                except Exception as exc:        # noqa: BLE001 - the contract is: never raises
                    self.fail("the entry raised %r" % (exc,))
                self.assertIsNone(result[0])
                self.assertIsNotNone(result[2])

    def test_the_entry_references_the_same_helper_names_in_the_same_order_as_the_librarys_admission_in_its_source(self):
        """STRUCTURAL, SOURCE TEXT ONLY. Parses both functions and compares the order of eight helper NAMES. It asserts nothing about behaviour and is SILENT on the three rules both functions write inline
        (the journal-moved check, the execution recomputation, the one-resolved-root check). The behavioural parity (the same state refused with the same code by the entry and by the library) is asserted by
        tests/test_finished_code_review_real.py and tests/test_finished_code_review_postload.py. Formerly named test_the_entry_composes_the_same_helpers_in_the_same_order_as_the_evidence_librarys_admission;
        renamed because that name claimed more than this body checks."""
        tree = ast.parse(source_of("target_runtime/artifact_content.py"))
        functions = dict((n.name, n) for n in tree.body if isinstance(n, ast.FunctionDef))
        helpers = ("_corroborated", "_registry_now", "_registered_source", "_registrations_hold", "_load", "_head", "_same_identity", "_mission_core_view")

        def order(name, stop_at_artifact):
            calls = []
            for n in ast.walk(functions[name]):
                if isinstance(n, ast.Call):
                    f = n.func
                    called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if called in helpers or (stop_at_artifact and called == "_artifact"):
                        calls.append((n.lineno, called))
            calls.sort()
            names = [c for _, c in calls]
            return names[:names.index("_artifact")] if stop_at_artifact and "_artifact" in names else names
        self.assertEqual(order("historical_execution", False), order("_admit", True))

    def test_the_entry_reports_the_bytes_it_charged(self):
        result, _ = entry([mission()])
        self.assertGreater(result[1], 0, "the registry read was held and settled, so some bytes were charged")


# =====================================================================================================================
class FinishedReviewBranchTests(unittest.TestCase):
    """The review module's finished branch: provider faked."""

    def setUp(self):
        self.assertTrue(hasattr(code_review, "FINISHED_REFUSALS") and hasattr(code_review, "FINISHED_REASONS"),
                        "FEATURE MISSING: code_review.FINISHED_REFUSALS / FINISHED_REASONS do not exist")

    def corroborate(self, held, finished, accounting=None, task=TASK):
        accounting = code_review.Accounting() if accounting is None else accounting
        return code_review._corroborated(held, fresh, NOW, REF, task, finished=finished, accounting=accounting), accounting

    def test_the_provider_is_never_consulted_for_active_inactive_or_unknown_standing(self):
        provider = Provider((proof(), 5, None))
        for standing in (contract.STANDING_INACTIVE, contract.STANDING_UNKNOWN_FUTURE):
            with self.subTest(standing=standing):
                with self.assertRaises(code_review._Refused) as caught:
                    self.corroborate(answer([mission(standing=standing)]), provider)
                self.assertEqual(caught.exception.problem, code_review.PROBLEM_NOT_ACTIVE)
        bound, _ = self.corroborate(answer([active_mission()]), provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(bound["task_id"], TASK)

    def test_an_active_assignment_is_unchanged_by_passing_a_provider(self):
        held = answer([active_mission()])
        without = code_review._corroborated(held, fresh, NOW, REF, TASK)
        with_provider, _ = self.corroborate(held, Provider((proof(), 5, None)))
        self.assertEqual(with_provider, without)
        self.assertNotIn("finished", with_provider)

    def test_a_terminal_mission_with_a_proof_gives_a_bound_that_carries_the_whole_proof_and_the_witness_and_charges_their_bytes(self):
        # CHANGED IN I2 (was: ..._carries_the_whole_proof_and_charges_its_bytes): the finished branch now also requires the workspace's own task record, carried in the bound.
        with WitnessedWorkspace() as ws:
            p = proof(workspace=ws.path)
            provider = Provider((p, 321, None))
            bound, accounting = self.corroborate(answer([mission(workspace=ws.path)]), provider)
            size = os.path.getsize(ws.task_path())
        witness = bound.pop("witness")
        self.assertEqual(bound, {"source_id": SRC, "task_id": TASK, "workspace": ws.path, "registration": registration(), "finished": p})
        self.assertEqual((witness["id"], witness["started_at"]), (TASK, INTENT["recorded_at"]))
        self.assertEqual(accounting.registry_bytes, 321)
        self.assertEqual(accounting.untracked_bytes, size)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1], REF)

    def test_a_proof_for_another_task_than_the_one_the_review_was_opened_for_is_assignment_task_changed(self):
        provider = Provider((proof(task=OTHER_TASK), 9, None))
        with self.assertRaises(code_review._Refused) as caught:
            self.corroborate(answer([mission()]), provider)
        self.assertEqual(caught.exception.problem, code_review.PROBLEM_TASK_CHANGED)

    def test_the_table_is_exactly_the_decided_mapping(self):
        self.assertEqual(code_review.FINISHED_REFUSALS, EXPECTED_MAPPING)
        self.assertEqual(len(EXPECTED_MAPPING), 21)

    def test_every_refusal_of_the_entry_maps_as_DECIDED_and_the_bytes_are_charged_even_on_refusal(self):
        for key, expected in sorted(EXPECTED_MAPPING.items()):
            with self.subTest(refusal=key):
                accounting = code_review.Accounting()
                with self.assertRaises(code_review._Refused) as caught:
                    code_review._corroborated(answer([mission()]), fresh, NOW, REF, TASK,
                                              finished=Provider((None, 7, (key, "a detail"))), accounting=accounting)
                self.assertEqual(caught.exception.problem, expected)
                self.assertEqual(accounting.registry_bytes, 7)

    def test_a_refusal_the_table_does_not_know_fails_closed_to_unverifiable_and_never_raises_outside_the_contract(self):
        with self.assertRaises(code_review._Refused) as caught:
            code_review._corroborated(answer([mission()]), fresh, NOW, REF, TASK,
                                      finished=Provider((None, 0, ("a_code_nobody_declared", "x"))), accounting=code_review.Accounting())
        self.assertEqual(caught.exception.problem, "finished_ownership_unverifiable")

    def test_the_mapping_is_total_in_both_directions(self):
        names, reached = reachable_problem_constants(source_of("target_runtime/artifact_content.py"), "historical_execution")
        self.assertIn("_corroborated", reached)
        self.assertNotIn("_admit", reached, "the entry must not reach the artifact stage")
        reachable = set(getattr(artifact_content, n) for n in names)
        mapped = set(code_review.FINISHED_REFUSALS)
        self.assertEqual(reachable, mapped, "a reachable refusal with no mapping, or a mapping nothing can produce")
        constants = set(getattr(artifact_content, n) for n in dir(artifact_content) if n.startswith("PROBLEM_"))
        self.assertLessEqual(mapped, constants, "every mapped key is one of the evidence library's own codes")
        allowed = set(code_review.PROBLEMS) | set(code_review.FINISHED_REASONS)
        self.assertLessEqual(set(code_review.FINISHED_REFUSALS.values()), allowed)
        # CHANGED IN I2 (was: every internal reason is produced by at least one refusal of the entry): the fourth reason is the review route's OWN, from the workspace witness.
        record_side = set(code_review.FINISHED_REASONS) - set(["finished_workspace_not_corroborated"])
        self.assertEqual(set(code_review.FINISHED_REASONS) & set(code_review.FINISHED_REFUSALS.values()), record_side,
                         "every record-side internal reason is produced by at least one refusal of the entry; the witness reason is not one of them")

    def test_the_four_finished_reasons_are_closed_codes_and_the_closed_list_gained_exactly_them(self):
        # CHANGED IN I3 (was: test_the_internal_reasons_are_four_and_not_in_the_closed_vocabulary_until_the_vocabulary_is_extended_in_i3, which pinned the LIMITATION that refusal() rejected them).
        # The four enter PROBLEMS and the TypeScript lists TOGETHER (OfficeParityTests pins the TypeScript list equal to PROBLEMS, in order). The mapping is pinned in BOTH directions: every internal
        # reason is a closed code that refusal() accepts, and every finished_ code the closed list holds is an internal reason; the list is the 30 codes of the active route followed by exactly the four.
        witness = "finished_workspace_not_corroborated"
        self.assertEqual(sorted(code_review.FINISHED_REASONS),
                         ["finished_execution_not_established", "finished_ownership_contested", "finished_ownership_unverifiable", witness])
        self.assertLessEqual(set(code_review.FINISHED_REASONS), set(code_review.PROBLEMS))
        self.assertEqual(tuple(code_review.PROBLEMS[-4:]), tuple(code_review.FINISHED_REASONS))
        self.assertEqual([p for p in code_review.PROBLEMS if p.startswith("finished_")], list(code_review.FINISHED_REASONS))
        self.assertEqual(len(code_review.PROBLEMS), 30 + 4)
        self.assertEqual(len(set(code_review.PROBLEMS)), len(code_review.PROBLEMS))
        # every one of the four can be PRODUCED: three by a refusal of the record-side entry, the fourth by the route's own witness
        self.assertEqual(set(code_review.FINISHED_REASONS), (set(code_review.FINISHED_REFUSALS.values()) & set(code_review.FINISHED_REASONS)) | set([witness]))
        for reason in code_review.FINISHED_REASONS:
            answer = code_review.refusal(reason, "x", code_review.VIEW_OVERVIEW, REF, TASK, None, None)
            self.assertEqual((answer["state"], answer["problem"]), ("refused", reason))

    def revalidate(self, first, second, registry=None, workspace=WS):
        # CHANGED IN I2: the workspace is a parameter (a real, witnessed one for the proofs that must pass the witness).
        accounting = code_review.Accounting()
        state = {"calls": 0}

        def corroboration():
            return answer([mission(workspace=workspace)])

        results = [first, second]

        def provider(held, mission_ref):
            state["calls"] += 1
            return results[min(state["calls"] - 1, 1)]
        bound = code_review._corroborated(corroboration(), fresh, NOW, REF, TASK, finished=provider, accounting=accounting)
        registry = FakeRegistry([registration()]) if registry is None else registry
        return code_review._revalidated(registry, corroboration, fresh, clock, bound, accounting, SentinelTransport(), workspace, {}, REF, TASK,
                                        finished=provider)

    def test_the_release_time_check_refuses_a_proof_that_changed_while_the_workspace_was_read(self):
        with WitnessedWorkspace() as ws, self.assertRaises(code_review._Refused) as caught:
            self.revalidate((proof(workspace=ws.path), 1, None), (proof(workspace=ws.path, identity="9" * 64), 1, None), workspace=ws.path)
        self.assertEqual(caught.exception.problem, code_review.PROBLEM_ASSIGNMENT_CHANGED)

    def test_the_release_time_check_passes_an_unchanged_proof_on_to_the_workspace_observation(self):
        with WitnessedWorkspace() as ws, self.assertRaises(Reached):
            self.revalidate((proof(workspace=ws.path), 1, None), (proof(workspace=ws.path), 1, None), workspace=ws.path)

    def test_a_refusal_at_release_time_surfaces_its_own_reason(self):
        with WitnessedWorkspace() as ws, self.assertRaises(code_review._Refused) as caught:
            self.revalidate((proof(workspace=ws.path), 1, None), (None, 1, ("historical_identity_conflict", "another row now claims it")),
                            workspace=ws.path)
        self.assertEqual(caught.exception.problem, "finished_ownership_contested")

    def test_the_provider_is_forwarded_at_both_call_sites_of_the_corroboration(self):
        tree = ast.parse(source_of("target_runtime/code_review.py"))
        for name in ("_read", "_revalidated"):
            fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name][0]
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_corroborated"]
            self.assertEqual(len(calls), 1, name)
            self.assertIn("finished", [k.arg for k in calls[0].keywords], "%s must forward the provider to _corroborated" % name)

    def test_read_carries_a_corroborated_finished_assignment_through_to_the_workspace_observation(self):
        # CHANGED IN I2: the workspace carries its own task record naming the run (the witness), not just a directory.
        with WitnessedWorkspace() as ws:
            provider = Provider((proof(workspace=ws.path), 5, None))
            held = answer([mission(workspace=ws.path)])
            with self.assertRaises(Reached):
                code_review.read(FakeRegistry([registration()]), lambda: held, fresh, clock, SentinelTransport(), REF, TASK, finished=provider)
            self.assertEqual(len(provider.calls), 1, "the provider is consulted once before the observation; the release-time call comes after it")

    def test_read_gains_exactly_one_optional_trailing_parameter(self):
        params = list(inspect.signature(code_review.read).parameters.values())
        self.assertEqual([p.name for p in params],
                         ["registry", "corroboration", "freshness", "clock", "transport", "mission_ref", "task_id", "snapshot", "file_id", "finished"])
        self.assertIsNone(params[-1].default)


# =====================================================================================================================
class LateRefusalTests(unittest.TestCase):
    """ROUND 29 (the Reviewer's finding): a finished refusal can arise at the END-OF-READ re-check, after the workspace was observed (and, for a file, after its content was read), and the answer is then
    DISCARDED. The view's four sentences used to say "Nothing was read", which is false on exactly this path. Two groups, at the PUBLIC ``read()`` boundary (not ``_revalidated`` alone), with their
    different strengths stated:

    OVERVIEW PATH (four tests). The observation (``_observe_stable``) is replaced by a stub with an EMPTY listing and no git. They establish: the observation ran once, the re-check consulted the provider a
    second time at release, the refusal carries no target, comparison, overview or file, and each of the four closed codes is produced on that late path. THEY DO NOT ESTABLISH THAT CONTENT WAS READ: no file is
    listed, so ``_file_block`` is never executed, and the accounting the stub charges is the stub's own constant, which is why it is not asserted.

    FILE PATH (two tests). The observation (``_observe``, which a file request uses) is replaced by a stub that lists ONE UNTRACKED file that really exists in the temporary workspace and hands the caller a
    private-git-directory stand-in; everything after it is the PRODUCT's: the real ``_annotate`` and the real ``_file_block`` read that file's real bytes through the hardened no-follow reader (a spy
    wraps ``_file_block`` and records what it returned), and only then does the re-check refuse. They establish that the file's content WAS read and parsed (the recorded block holds the file's own
    lines, computed from the real bytes) and that none of it is in the refusal. No git, no real-git fixture, no write outside the temporary workspace."""

    def read_with(self, workspace, second, during_read=None):
        calls = {"provider": 0, "observed": 0}

        def provider(held, mission_ref):
            calls["provider"] += 1
            return (proof(workspace=workspace.path), 1, None) if calls["provider"] == 1 else second

        def observed(transport, path, bound, accounting):
            calls["observed"] += 1
            if during_read is not None:
                during_read()
            return {"head": "a" * 40}, []
        real = code_review._observe_stable
        code_review._observe_stable = observed
        try:
            result = code_review.read(FakeRegistry([registration()]), lambda: answer([mission(workspace=workspace.path)]), fresh, clock, SentinelTransport(), REF, TASK, finished=provider)
        finally:
            code_review._observe_stable = real
        return result, calls

    def assert_refused_late_with_no_content(self, result, calls, problem):
        self.assertEqual((result["state"], result["problem"]), ("refused", problem), result["detail"])
        for key in ("target", "comparison", "overview", "file"):
            self.assertIsNone(result[key], "a refusal carries no content: %s" % key)
        self.assertEqual(calls["observed"], 1, "the observation ran before the refusal")
        self.assertEqual(calls["provider"], 2, "the re-check consulted the provider a second time, at release")

    def test_a_record_that_became_contested_during_the_read_is_refused_at_release_with_no_content_on_the_overview_path(self):
        with WitnessedWorkspace() as ws:
            result, calls = self.read_with(ws, (None, 1, ("historical_identity_conflict", "another row now claims it")))
        self.assert_refused_late_with_no_content(result, calls, "finished_ownership_contested")

    def test_a_record_that_became_unverifiable_during_the_read_is_refused_at_release_with_no_content_on_the_overview_path(self):
        with WitnessedWorkspace() as ws:
            result, calls = self.read_with(ws, (None, 1, ("mission_moved_since_listing", "the record moved")))
        self.assert_refused_late_with_no_content(result, calls, "finished_ownership_unverifiable")

    def test_an_execution_that_stopped_being_established_during_the_read_is_refused_at_release_with_no_content_on_the_overview_path(self):
        with WitnessedWorkspace() as ws:
            result, calls = self.read_with(ws, (None, 1, ("historical_identity_not_established", "no longer established")))
        self.assert_refused_late_with_no_content(result, calls, "finished_execution_not_established")

    def test_a_workspace_whose_own_record_was_rewritten_during_the_read_is_refused_at_release_with_no_content_on_the_overview_path(self):
        with WitnessedWorkspace() as ws:
            def rewrite():
                with open(ws.task_path(), "w") as handle:
                    handle.write(json.dumps({"id": TASK, "started_at": INTENT["recorded_at"], "description": "an unrelated handoff that names no Mission"}))
            result, calls = self.read_with(ws, (proof(workspace=ws.path), 1, None), during_read=rewrite)
        self.assert_refused_late_with_no_content(result, calls, "finished_workspace_not_corroborated")

    # -- the FILE path: the product reads a real file's bytes, then the re-check refuses ---------------------------------------------------------------------------------------------------------------
    FILE_PATH = "scratch/new.txt"
    FILE_LINES = ["the first line of the file", "the second line of the file"]

    class _Manager(object):
        def __exit__(self, *exc):
            return None

    class _View(object):
        oid = "a" * 40

        def verify(self):
            return None

    def read_file_with(self, workspace, second, after_content=None):
        """A file request (a snapshot and a file id). The listing holds one untracked file that REALLY EXISTS in ``workspace``; ``_annotate`` and ``_file_block`` are the product's."""
        directory = os.path.join(workspace.path, "scratch")
        os.makedirs(directory)
        content = ("\n".join(self.FILE_LINES) + "\n").encode("utf-8")
        with open(os.path.join(directory, "new.txt"), "wb") as handle:
            handle.write(content)
        identity = {"head": "a" * 40}
        snapshot = code_review._snapshot(identity)
        file_id = code_review._file_id(snapshot, self.FILE_PATH)
        calls = {"provider": 0, "observed": 0, "blocks": []}

        def provider(held, mission_ref):
            calls["provider"] += 1
            return (proof(workspace=workspace.path), 1, None) if calls["provider"] == 1 else second

        def observed(transport, path, bound, accounting, hold=None):
            calls["observed"] += 1
            if hold is not None:
                hold.extend((self._Manager(), self._View()))
            entry = {"path": self.FILE_PATH, "old_path": None, "change": "untracked", "staging": "untracked", "nested": False, "gitlink": False}
            return identity, [entry]
        real_observe, real_block = code_review._observe, code_review._file_block

        def spy(*args):
            block = real_block(*args)
            calls["blocks"].append(block)
            if after_content is not None:
                after_content()
            return block
        code_review._observe, code_review._file_block = observed, spy
        try:
            result = code_review.read(FakeRegistry([registration()]), lambda: answer([mission(workspace=workspace.path)]), fresh, clock, SentinelTransport(), REF, TASK,
                                      snapshot=snapshot, file_id=file_id, finished=provider)
        finally:
            code_review._observe, code_review._file_block = real_observe, real_block
        return result, calls, content

    def assert_content_was_read_then_discarded(self, result, calls, content, problem):
        self.assert_refused_late_with_no_content(result, calls, problem)
        self.assertEqual(len(calls["blocks"]), 1, "the product's own _file_block ran once, before the refusal")
        shown = [line["text"] for hunk in calls["blocks"][0]["hunks"] for line in hunk["lines"]]
        self.assertEqual(shown, self.FILE_LINES, "the file's own lines were read and parsed from its real bytes")
        self.assertGreaterEqual(result["accounting"]["untracked_bytes"], len(content), "the account holds at least the real file's size, read by the product's reader")
        everything = json.dumps(result)
        for line in self.FILE_LINES:
            self.assertNotIn(line, everything, "none of what was read is in the refusal")

    def test_a_file_whose_content_was_read_is_discarded_when_the_release_check_finds_the_record_contested(self):
        with WitnessedWorkspace() as ws:
            result, calls, content = self.read_file_with(ws, (None, 1, ("historical_identity_conflict", "another row now claims it")))
        self.assert_content_was_read_then_discarded(result, calls, content, "finished_ownership_contested")

    def test_a_file_whose_content_was_read_is_discarded_when_the_workspaces_own_record_is_rewritten_after_the_read(self):
        with WitnessedWorkspace() as ws:
            def rewrite():
                with open(ws.task_path(), "w") as handle:
                    handle.write(json.dumps({"id": TASK, "started_at": INTENT["recorded_at"], "description": "an unrelated handoff that names no Mission"}))
            result, calls, content = self.read_file_with(ws, (proof(workspace=ws.path), 1, None), after_content=rewrite)
        self.assert_content_was_read_then_discarded(result, calls, content, "finished_workspace_not_corroborated")


class UnchangedBehaviourGuards(unittest.TestCase):
    """GUARDS, not red tests: behaviour that must be the same before and after I1. They are deliberately NOT gated on the new feature, so they
    PASS on the unchanged code and keep passing after it (the first red run showed two of them failing for the gate, which was the wrong reason)."""

    def test_the_default_without_a_provider_is_unchanged_for_every_non_active_standing(self):
        for standing in (contract.STANDING_TERMINAL, contract.STANDING_INACTIVE, contract.STANDING_UNKNOWN_FUTURE):
            with self.subTest(standing=standing):
                m = mission(standing=standing)
                with self.assertRaises(code_review._Refused) as caught:
                    code_review._corroborated(answer([m]), fresh, NOW, REF, TASK)
                self.assertEqual(caught.exception.problem, code_review.PROBLEM_NOT_ACTIVE)

    def test_read_by_default_carries_an_active_assignment_through_to_the_workspace_observation(self):
        # read() -> _read() -> _corroborated -> the registry check -> the workspace check -> the first git touch (a sentinel). The whole changed
        # plumbing runs; the workspace is a real temporary directory (isdir is the only filesystem question the route asks before git).
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.realpath(tmp)
            held = answer([dict(active_mission(), execution=dict(active_mission()["execution"], workspace_realpath=workspace))])
            with self.assertRaises(Reached):
                code_review.read(FakeRegistry([registration()]), lambda: held, fresh, clock, SentinelTransport(), REF, TASK)

    def test_the_active_path_without_a_provider_is_unchanged(self):
        bound = code_review._corroborated(answer([active_mission()]), fresh, NOW, REF, TASK)
        self.assertEqual(bound, {"source_id": SRC, "task_id": TASK, "workspace": WS, "registration": registration()})

    def test_the_catalog_view_never_reads_a_late_resolution(self):
        # The premise of the two late-resolution tests: the view builds execution.task_id from run.receipt alone.
        tree = ast.parse(source_of("mission_sources/sources.py"))
        view = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_mission_core_view"][0]
        mentioned = [n for n in ast.walk(view)
                     if (isinstance(n, ast.Constant) and isinstance(n.value, str) and "late_resolution" in n.value)
                     or (isinstance(n, ast.Name) and "late_resolution" in n.id)
                     or (isinstance(n, ast.Attribute) and "late_resolution" in n.attr)]
        self.assertEqual(mentioned, [])


# =====================================================================================================================
class FenceTests(unittest.TestCase):
    """The structural pins that must keep holding. The blocks are taken VERBATIM from tests/test_static.py and executed against the changed files."""

    def run_block(self, label):
        namespace = {"ast": ast, "R": REPO_ROOT, "RUNTIME_SNAPSHOT_FORBIDDEN_CALLS": static_literal("RUNTIME_SNAPSHOT_FORBIDDEN_CALLS")}
        exec(compile(pin_block(label), "pin-%s" % label, "exec"), namespace)

    def test_pin_12j_holds_for_the_evidence_library(self):
        self.run_block("12j")

    def test_pin_12k_holds_for_the_live_code_review(self):
        self.run_block("12k")

    def test_pin_12o_holds_for_the_run_association_module(self):
        self.run_block("12o")

    def test_no_new_import_was_added_to_either_changed_module(self):
        # CHANGED IN I2 (was 9 for code_review.py): the ONE added import is target_runtime.run_association, the tenth entry of pin 12k.
        for path, limit in (("target_runtime/code_review.py", 10), ("target_runtime/artifact_content.py", 13)):
            tree = ast.parse(source_of(path))
            count = sum(len(n.names) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
            self.assertEqual(count, limit, path)

    def test_the_closed_list_holds_every_finished_code_and_no_other_new_one(self):
        # CHANGED IN I3 (was: test_the_vocabulary_fence_the_closed_list_gained_no_finished_code). The fence now says the OPPOSITE on purpose: the active route's thirty codes are unchanged and in their
        # order, and the four finished codes follow them. A thirty-first code of any other kind would fail the first assertion.
        active = [p for p in code_review.PROBLEMS if not p.startswith("finished_")]
        self.assertEqual(len(active), 30)
        self.assertEqual(list(code_review.PROBLEMS[:30]), active)
        self.assertEqual(list(code_review.PROBLEMS[30:]), list(code_review.FINISHED_REASONS))


if __name__ == "__main__":
    unittest.main()
