"""Behavioral tests for Task 7 Stage 2, roadmap item 15: the bounded,
read-only, NON-INVOKING Mission Observation with provenance and
freshness.

Everything here drives the REAL store and the REAL service in a
temporary protected directory with an injected clock. The Task-6-facing
seam is exercised the way a caller uses it: the TEST HARNESS runs its
controlled (counting, raising, misbehaving) adapters OUTSIDE the
observation path and hands the materialized plain-data result in
(``materialize``); ``observe`` itself never calls anything the caller
supplied. Nothing is launched, messaged or delivered.

Sections:
  O1  read-only and non-invoking: the shared pin (tests/_non_invoking.py)
      proves on the AST that no caller value is called, no method is
      invoked on a value derived from an argument, the sanitizers read
      raw values by exact type only, and every hostile probe fails it;
      behaviorally, bytes, authority bytes, directory listing, minted
      ids, load count, lock never taken; a hostile caller cannot run
      code through dunder dispatch (metaclass ``__name__``, a colliding
      key's ``__eq__``, a str subclass ``__len__``) or mutate anything
  O2  the canonical facts, each bound to the revision and cursor it was
      observed at; the binding moves with an EDIT and with an event; a
      report collected at an older cursor is reported as moved
  O3  the six-term vocabulary stays distinct: reported is not verified,
      unavailable is not unknown, fresh and stale are age against a
      module-constant bound and never substitute for a standing
  O4  the input seam fails closed with its own code on malformed,
      oversized, deep, non-plain or unknown-kind input; misbehaving
      sources are materialized truthfully by the caller and stay
      distinct; a busy Mission observes with one load and no lock
  O5  false completion is blocked: historical closure is distinct from
      present verified success; a reported COMPLETE without the
      canonical closure, a REJECT, contradicted, drifted or expired
      proof is never verified success; Task 5's limits stay stated
  O6  drift invalidates derived claims now, live from the report in
      hand; an unavailable, stale or moved report preserves detected
      drift and affirms nothing
  O7  reload preserves provenance, freshness semantics, blockers,
      reconciliation position, cursor and snapshot bindings
  O8  a Mission with no state record observes at the origin
  O9  Task 7 Stage 2: attested receipts are read from the re-proved
      marker, apart from unattested references; success is the one
      pinned state; an attestation moves no completion term
  O10 round 11: an omission is not a confirmation — an applicable
      candidate report that omits a recorded artifact or the anchored
      baseline leaves it unconfirmed and withholds verified success
  O11 round 12: a re-recorded key holds several artifacts; evidence
      follows the artifact it references, never the latest under the key
  O12 round 13: drift has a relevance boundary — never-accepted,
      invalidated and superseded-activation evidence blocks nothing;
      accepted current evidence that is unobserved or contradicted still
      does; both directions in one test
"""

import ast
import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from mission import journal as mj  # noqa: E402
from mission import observation as ob  # noqa: E402
from mission import reconciliation as rc  # noqa: E402
from mission import state as ms  # noqa: E402
from mission import state_service as mss  # noqa: E402
from mission import store as mst  # noqa: E402
import _non_invoking as ni  # noqa: E402
from test_mission_state import (  # noqa: E402
    HEX_A, ServiceStateFixture, contract, hexid,
)

ADAPTER_FACTS = ("task", "review", "candidate", "delivery")


class Counting(object):
    """A caller-side adapter that records every call and answers as
    told. It is run by the HARNESS, never by the package."""

    def __init__(self, answer=None, raise_=None):
        self.answer = answer
        self.raise_ = raise_
        self.calls = []

    def __call__(self, mission_id, cursor):
        self.calls.append((mission_id, copy.deepcopy(cursor)))
        if self.raise_ is not None:
            raise self.raise_
        return copy.deepcopy(self.answer)


def materialize(adapters, mission_id, cursor):
    """The caller's job, outside the observation path: consult each
    controlled adapter once and hand the plain result in, an adapter
    that raises becoming an ``unavailable`` report with its reason."""
    reports = {}
    for kind, adapter in adapters.items():
        try:
            reports[kind] = adapter(mission_id, copy.deepcopy(cursor))
        except Exception as exc:  # noqa: BLE001 - the harness reports it
            reports[kind] = {"unavailable": "%s: %s" % (type(exc).__name__, exc)}
    return {"cursor": copy.deepcopy(cursor), "reports": reports}


class ObservationFixture(ServiceStateFixture):

    def setUp(self):
        super(ObservationFixture, self).setUp()
        self.mission_id = self.ready_mission(required_dependencies=[])
        self.claim = self.call("record_claim", self.mission_id, "tests_pass",
                               "the suite passes")
        self.clock.advance(5)
        self.artifact = self.call("record_artifact", self.mission_id, "test_log",
                                  mission_record.ARTIFACT_ROLE_VERIFICATION,
                                  ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log",
                                  HEX_A, True, [])

    def answer(self, value, age=0):
        return {"value": value, "observed_at": self.clock() - age}

    def head(self, mission_id=None):
        return self.service.get_journal(mission_id or self.mission_id)["cursor"]

    def inputs(self, adapters=None, mission_id=None):
        mission_id = mission_id or self.mission_id
        return materialize(adapters or {}, mission_id, self.head(mission_id))

    def observe(self, adapters=None, mission_id=None):
        mission_id = mission_id or self.mission_id
        return self.service.observe(mission_id, self.inputs(adapters, mission_id))

    def observe_raw(self, inputs, mission_id=None):
        return self.service.observe(mission_id or self.mission_id, inputs)

    def document(self):
        return json.loads(self.read_bytes())

    def candidate(self, baseline="b" * 64, **digests):
        return {"baseline_digest_sha256": baseline, "artifact_digests": digests}

    def delivery(self, status, artifact=None, locator=None, digest=None):
        if status == rc.DELIVERY_REPORT_ABSENT:
            return {"status": status, "receipt_artifact_id": None, "locator": None,
                    "receipt_digest_sha256": None}
        return {"status": status,
                "receipt_artifact_id": artifact or hexid("mf", 0x4242),
                "locator": locator or "receipt:42",
                "receipt_digest_sha256": digest or "d" * 64}

    def busy_adapters(self):
        """Fresh reported task, stale reported candidate, unknown review,
        raising delivery: every standing and both freshness terms."""
        return {
            "task": Counting(self.answer(rc.TASK_REPORT_ACTIVE)),
            "candidate": Counting(self.answer(
                self.candidate(), age=ob.REPORTED_FRESHNESS_BOUND_SECONDS + 1)),
            "review": Counting(None),
            "delivery": Counting(raise_=RuntimeError("busy source")),
        }

    def assert_fact(self, fact, standing, freshness=None, source=ob.SOURCE_RECORD):
        self.assertEqual(set(fact), set(ob.FACT_KEYS))
        self.assertEqual(fact["standing"], standing)
        self.assertEqual(fact["freshness"], freshness)
        self.assertEqual(fact["source"], source)


# ====================================================================
# O1. Read-only and non-invoking
# ====================================================================


def _module_facts(relpath):
    source = (REPO_ROOT / relpath).read_text()
    tree = ast.parse(source)
    roots = set()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
            if node.module == "mission":
                imported.update(a.name for a in node.names)
    calls = {getattr(n.func, "id", getattr(n.func, "attr", None))
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    return source, tree, roots, imported, calls, identifiers


FORBIDDEN_CALLS = ("open", "save", "load", "lock", "apply_human_decision",
                   "issue_mission_authorization", "mint_id", "_apply", "_reserve",
                   "atomic_write_json", "exclusive_store_lock", "sleep", "wait",
                   "input", "print", "callable", "getattr", "setattr", "eval", "exec",
                   "compile", "__import__", "import_module")
FORBIDDEN_NAMES = ("store", "store_module", "service", "MissionService",
                   "authorization_module", "issue_mission_authorization",
                   "apply_human_decision", "os", "json", "sys", "time", "threading",
                   "subprocess", "adapter", "adapters", "collect", "callable")
READ_PATH_METHODS = ("observe", "reconcile", "_require_at_cursor", "_contract_status")


def _function_calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    raise AssertionError("no function %s" % name)


class O1ReadOnlyTests(ObservationFixture):

    def test_O1_module_is_pure_and_non_invoking_by_construction(self):
        source, tree, roots, imported, calls, identifiers = _module_facts(
            "mission/observation.py")
        self.assertEqual(roots, {"copy", "mission"})
        self.assertEqual(imported, {"journal", "progress", "reconciliation", "record",
                                    "state"})
        for forbidden in FORBIDDEN_CALLS:
            self.assertNotIn(forbidden, calls, forbidden)
        for word in FORBIDDEN_NAMES:
            self.assertNotIn(word, identifiers, word)
        for word in ("issue_mission_authorization", "apply_human_decision",
                     "atomic_write_json", "exclusive_store_lock", "callable(",
                     "__name__", "__class__", "isinstance(value", "isinstance(inputs"):
            self.assertNotIn(word, source, word)
        # The shared pin: the detector fires on every hostile probe and
        # passes the benign shapes, then every function in the module
        # passes it, ``normalize_inputs`` as the raw surface.
        self.assertEqual(ni.self_check(), len(ni.PROBES) + len(ni.PASSING_PROBES))
        self.assertGreaterEqual(len(ni.PROBES), 20)
        checked = ni.check_module(tree, "mission/observation.py",
                                  raw_surface=("normalize_inputs",))
        self.assertGreaterEqual(checked, 20)
        for name in ni.TIER1:
            self.assertIn(name, ni.module_known(tree), name)
        known = ni.module_known(tree)
        self.assertIn("normalize_inputs", known)
        self.assertNotIn("collect", known)
        # ``normalize_report`` reads only data ``require_plain_data`` proved:
        # its one call site is inside ``normalize_inputs`` after that call.
        sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and getattr(
                        call.func, "id", None) == "normalize_report":
                        sites.append((node.name, call.lineno))
        self.assertEqual([site[0] for site in sites], ["normalize_inputs"])
        body = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "normalize_inputs"][0]
        first_plain = [st.lineno for st in body.body
                       if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
                       and getattr(st.value.func, "id", None) == "require_plain_data"]
        self.assertEqual(len(first_plain), 1)
        self.assertLess(first_plain[0], sites[0][1])
        # The vocabulary is closed and exactly six terms on two axes.
        self.assertEqual(len(ob.FACT_TERMS), 6)
        self.assertEqual(set(ob.FACT_TERMS), set(ob.STANDINGS) | set(ob.FRESHNESS_TERMS))
        self.assertFalse(set(ob.STANDINGS) & set(ob.FRESHNESS_TERMS))
        # The seam's problem code is distinct and the bounds are constants.
        self.assertEqual(ob.PROBLEM_OBSERVATION_INPUT, "mission_observation_input")
        self.assertEqual(ob.MAX_OBSERVATION_INPUT_ITEMS, 512)
        self.assertEqual(ob.MAX_OBSERVATION_INPUT_DEPTH, 6)
        self.assertEqual(ob.MAX_OBSERVATION_INPUT_STR_CHARS, 2048)
        self.assertEqual(ob.MAX_OBSERVATION_INPUT_KEY_CHARS, 128)
        self.assertEqual(ob.MAX_OBSERVATION_INPUT_INT_BITS, 63)
        self.assertEqual(ob.MAX_MISSION_ID_CHARS, 64)
        self.assertEqual(ob.MAX_REPORT_DETAIL_CHARS, 500)
        self.assertIn("CONSULTS NOTHING", ob.__doc__)
        self.assertIn("never runs its code", ob.__doc__)

    def test_O1_service_read_path_invokes_no_caller_value(self):
        source = (REPO_ROOT / "mission" / "state_service.py").read_text()
        tree = ast.parse(source)
        checked = ni.check_functions(tree, READ_PATH_METHODS, "state_service",
                                     raw_surface=("observe", "reconcile"))
        self.assertEqual(checked, len(READ_PATH_METHODS))
        for name in READ_PATH_METHODS:
            names = {getattr(n.func, "id", getattr(n.func, "attr", None))
                     for n in _function_calls(tree, name)}
            for forbidden in ("callable", "getattr", "eval", "exec", "sleep", "wait",
                              "open", "atomic_write_json", "apply_human_decision",
                              "issue_mission_authorization", "collect"):
                self.assertNotIn(forbidden, names, (name, forbidden))
        observe_calls = {getattr(n.func, "id", getattr(n.func, "attr", None))
                         for n in _function_calls(tree, "observe")}
        for forbidden in ("lock", "save", "_apply", "_reserve", "mint_state_operation_id"):
            self.assertNotIn(forbidden, observe_calls, forbidden)
        self.assertIn("load", observe_calls)
        self.assertIn("normalize_inputs", observe_calls)
        self.assertIn("require_exact_str", observe_calls)
        self.assertIn("report", observe_calls)
        reconcile_calls = {getattr(n.func, "id", getattr(n.func, "attr", None))
                           for n in _function_calls(tree, "reconcile")}
        for sanitizer in ("require_exact_str", "require_exact_int",
                          "require_exact_context", "normalize_inputs"):
            self.assertIn(sanitizer, reconcile_calls, sanitizer)
        # Round-6 blocker 3, exactly: the real observation module with its
        # unsupported-type rejection replaced by ``return value`` FAILS the
        # whole-module pin, and so does the context rejection replaced by
        # ``return value``, and a refusal helper that returns.
        module_source = (REPO_ROOT / "mission" / "observation.py").read_text()
        start = module_source.index('    if kind is not dict:\n        _input("at %s is not plain data')
        end = module_source.index("\n", module_source.index("% location)", start)) + 1
        doctored_module = (module_source[:start]
                           + "    if kind is not dict:\n        return value\n"
                           + module_source[end:])
        self.assertNotEqual(doctored_module, module_source)
        with self.assertRaises(ni.Violation):
            ni.check_module(ast.parse(doctored_module), "doctored",
                            raw_surface=("normalize_inputs",))
        start = module_source.index(
            "    if type(value) is not record.AuthenticatedContext:\n        _input(")
        end = module_source.index("\n", start + 60) + 1
        doctored_context = (module_source[:start]
                            + "    if type(value) is not record.AuthenticatedContext:\n"
                            "        return value\n" + module_source[end:])
        with self.assertRaises(ni.Violation):
            ni.check_module(ast.parse(doctored_context), "doctored",
                            raw_surface=("normalize_inputs",))
        doctored_helper = module_source.replace(
            'def _input(detail):\n    record.fail(PROBLEM_OBSERVATION_INPUT, "observation input " + detail)',
            "def _input(detail):\n    return None", 1)
        self.assertNotEqual(doctored_helper, module_source)
        with self.assertRaises(ni.Violation):
            ni.check_module(ast.parse(doctored_helper), "doctored",
                            raw_surface=("normalize_inputs",))
        # A doctored service body with a raw parameter used first fails.
        doctored = source.replace(
            "        mission_id = observation.require_exact_str(mission_id, \"mission_id\",\n"
            "                                                   observation.MAX_MISSION_ID_CHARS)\n"
            "        collected_at, answers = observation.normalize_inputs(inputs)\n"
            "        document = self._store.load()",
            "        document = self._store.load()\n"
            "        mission_id = observation.require_exact_str(mission_id, \"mission_id\",\n"
            "                                                   observation.MAX_MISSION_ID_CHARS)\n"
            "        collected_at, answers = observation.normalize_inputs(inputs)\n"
            "        record.require_id(mission_id, record.MISSION_ID_PREFIX, str(inputs))", 1)
        self.assertNotEqual(doctored, source)
        with self.assertRaises(ni.Violation):
            ni.check_functions(ast.parse(doctored), ("observe",), "doctored",
                               raw_surface=("observe",))
        self.assertEqual(set(ob.ADAPTER_KINDS), {"task", "review", "candidate",
                                                 "delivery"})
        self.assertEqual(ob.ADAPTER_KINDS, rc.SOURCE_KINDS)

    def test_O1_a_hostile_caller_runs_no_code_and_mutates_nothing_through_the_read(self):
        ran = []
        fixture = self
        mission_id = self.mission_id

        class Trap(dict):
            """A dict subclass whose every access runs caller code."""

            def __getitem__(self, key):
                ran.append(("getitem", key))
                return dict.__getitem__(self, key)

            def keys(self):
                ran.append(("keys",))
                return dict.keys(self)

            def __iter__(self):
                ran.append(("iter",))
                return dict.__iter__(self)

            def __contains__(self, key):
                ran.append(("contains", key))
                return dict.__contains__(self, key)

        class Hostile(object):
            def __call__(self, *args):
                ran.append(("call",))
                fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return {"value": "ACTIVE", "observed_at": 1}

        class Meta(type):
            """Round-2 finding 1: a metaclass whose __name__ runs code."""

            @property
            def __name__(cls):
                ran.append(("name",))
                fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return "dict"

        class Named(metaclass=Meta):
            pass

        class Colliding(object):
            """Round-2 finding 1: a key colliding with 'task' whose __eq__
            and __hash__ run code inside an exact dict."""

            def __hash__(self):
                ran.append(("hash",))
                return hash("task")

            def __eq__(self, other):
                ran.append(("eq",))
                fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return True

        class Text(str):
            """Round-2 finding 1: a str subclass whose __len__ runs code."""

            def __len__(self):
                ran.append(("len",))
                fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return str.__len__(self)

        class Number(int):
            def __index__(self):
                ran.append(("index",))
                return 0

            def bit_length(self):
                ran.append(("bits",))
                return 1

        class Context(mission_record.AuthenticatedContext):
            def validate(self, location="context"):
                ran.append(("validate",))
                fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return self

        def context_with(**fields):
            values = {"transport": "local",
                      "principal_kind": mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
                      "principal_ref": "uid:501", "configured_subject": None}
            values.update(fields)
            return mission_record.AuthenticatedContext(**values)

        before = self.read_bytes()
        sequence = self.seq(mission_id)
        report = self.answer("ACTIVE")
        hostile = [
            ("metaclass report", {"cursor": None, "reports": {"task": Named()}}),
            ("metaclass inputs", Named()),
            ("metaclass value", {"cursor": None, "reports": {"task": {
                "value": Named(), "observed_at": 1}}}),
            ("colliding key in reports", {"cursor": None,
                                          "reports": {Colliding(): None}}),
            ("colliding key in inputs", {Colliding(): None, "reports": {}}),
            ("colliding key in value", {"cursor": None, "reports": {"candidate": {
                "value": {"baseline_digest_sha256": None,
                          "artifact_digests": {Colliding(): "c" * 64}},
                "observed_at": 1}}}),
            ("str subclass value", {"cursor": None, "reports": {"task": {
                "value": Text("ACTIVE"), "observed_at": 1}}}),
            ("str subclass key", {"cursor": None, "reports": {Text("task"): None}}),
            ("int subclass timestamp", {"cursor": None, "reports": {"task": {
                "value": "ACTIVE", "observed_at": Number(1)}}}),
            ("callable report", {"cursor": None, "reports": {"task": Hostile()}}),
            ("callable inputs", Hostile()),
            ("dict subclass inputs", Trap(cursor=None, reports={"task": report})),
            ("dict subclass reports", {"cursor": None, "reports": Trap(task=report)}),
            ("dict subclass report", {"cursor": None, "reports": {"task": Trap(report)}}),
            ("dict subclass value", {"cursor": None, "reports": {"candidate": {
                "value": Trap(self.candidate()), "observed_at": 1}}}),
            ("dict subclass cursor", {"cursor": Trap(self.head()), "reports": {}}),
            ("list", {"cursor": None, "reports": {"task": [report]}}),
            ("bool timestamp", {"cursor": None, "reports": {"task": {
                "value": "ACTIVE", "observed_at": True}}}),
            ("float timestamp", {"cursor": None, "reports": {"task": {
                "value": "ACTIVE", "observed_at": 1.0}}}),
            ("object", {"cursor": None, "reports": {"task": object()}}),
            ("bytes", {"cursor": None, "reports": {"task": b"ACTIVE"}}),
        ]
        # Building the dict literals above hashed the colliding keys once
        # each: that is the harness constructing its own input. Nothing the
        # package does afterwards may run any of them.
        self.assertTrue(all(entry == ("hash",) for entry in ran), ran)
        del ran[:]
        for label, inputs in hostile:
            with self.subTest(label):
                exc = self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT,
                                         self.observe_raw, inputs)
                self.assertIn("observation input", str(exc))
                self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                                   mission_id, self.oid(), sequence, inputs,
                                   self.context)
                self.assertEqual(ran, [], label)
        # Every other caller argument is established by exact type before
        # any other use: a str subclass mission id, a str subclass
        # operation id, an int subclass sequence, a context subclass.
        good = self.inputs({"task": Counting(self.answer("ACTIVE"))})
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.observe,
                           Text(mission_id), good)
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           Text(mission_id), self.oid(), sequence, good, self.context)
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           mission_id, Text(self.oid()), sequence, good, self.context)
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           mission_id, self.oid(), Number(sequence), good, self.context)
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           mission_id, self.oid(), sequence, good,
                           Context(transport="local",
                                   principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
                                   principal_ref="uid:501"))
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.observe,
                           "m" * (ob.MAX_MISSION_ID_CHARS + 1), good)
        # Round-4 findings 1 and 2, exactly: an exact-type context whose
        # FIELDS the caller built hostile — a str subclass ``transport``
        # whose __len__ runs code, the same in every other field, a
        # million-character ``principal_kind``, a non-str field, a bool
        # subject — each refused at the seam before the baseline validator
        # reads it, with a bounded refusal that echoes nothing.
        for label, fields in (
            ("transport subclass", {"transport": Text("local")}),
            ("principal_kind subclass", {"principal_kind": Text(
                mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER)}),
            ("principal_ref subclass", {"principal_ref": Text("uid:501")}),
            ("subject subclass", {"configured_subject": Text("s")}),
            ("million-char principal_kind", {"principal_kind": "x" * 1000000}),
            ("million-char transport", {"transport": "x" * 1000000}),
            ("over-bound subject", {"configured_subject":
                                    "s" * (ob.MAX_CONTEXT_FIELD_CHARS + 1)}),
            ("int field", {"principal_ref": 5}),
            ("bool subject", {"configured_subject": True}),
            ("bytes transport", {"transport": b"local"}),
        ):
            with self.subTest(label):
                exc = self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT,
                                         self.service.reconcile, mission_id, self.oid(),
                                         sequence, good, context_with(**fields))
                self.assertLess(len(str(exc)), 200, label)
                self.assertNotIn("xxxx", str(exc))
                self.assertEqual(ran, [], label)
        self.assertEqual(ob.MAX_CONTEXT_FIELD_CHARS, 256)
        # Round-6 blocker 2, exactly: an exact-type context whose mutable
        # instance dictionary carries a key COLLIDING with "transport"
        # whose __eq__ runs code. Inserting it hashes and compares once in
        # the harness (asserted, cleared); the package then reads the
        # instance dictionary without hashing or comparing any key.
        class Colliding(object):
            armed = False

            def __hash__(self):
                ran.append(("hash",))
                return hash("transport")

            def __eq__(self, other):
                ran.append(("eq",))
                if self.armed:
                    fixture.call("record_claim", mission_id, "tests_pass", "sneak")
                return False

        planted = context_with()
        colliding = Colliding()
        planted.__dict__[colliding] = "x"
        # The harness's own insertion hashed and compared once; from here
        # on any comparison mutates the Mission, and none may happen.
        self.assertTrue(all(entry in (("hash",), ("eq",)) for entry in ran), ran)
        del ran[:]
        colliding.armed = True
        exc = self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                                 mission_id, self.oid(), sequence, good, planted)
        self.assertIn("field name that is not a string", str(exc))
        self.assertEqual(ran, [])
        self.assertEqual(self.seq(mission_id), sequence)
        # A planted EXTRA string field, and a missing field, refuse too.
        extra = context_with()
        extra.__dict__["transport_"] = "x"
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           mission_id, self.oid(), sequence, good, extra)
        missing = context_with()
        del missing.__dict__["principal_ref"]
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           mission_id, self.oid(), sequence, good, missing)
        self.assertEqual(ran, [])
        # A well-formed exact context passes the seam and comes back
        # rebuilt by the package, equal in every field.
        rebuilt = ob.require_exact_context(context_with())
        self.assertIs(type(rebuilt), mission_record.AuthenticatedContext)
        self.assertEqual(rebuilt.as_dict(), context_with().as_dict())
        # Nothing the caller supplied ever ran, and nothing moved.
        self.assertEqual(ran, [])
        self.assertEqual(self.seq(mission_id), sequence)
        self.assertEqual(json.loads(self.read_bytes())["mission_state"],
                         json.loads(before)["mission_state"])
        self.assertEqual(json.loads(self.read_bytes())["missions"],
                         json.loads(before)["missions"])

    def test_O1_observation_writes_mints_locks_and_reads_nothing_it_should_not(self):
        minted = []

        def counting_mint(prefix):
            minted.append(prefix)
            return mission_record.mint_id(prefix)

        loads = []
        store = mst.MissionStore(self.directory)
        real_load = store.load

        def counted_load():
            loads.append(1)
            return real_load()

        def refuse_lock():
            raise AssertionError("observation must never take the store lock")

        store.load = counted_load
        store.lock = refuse_lock
        store.save = lambda document: (_ for _ in ()).throw(
            AssertionError("observation must never save"))
        service = self.service.__class__(store, self.clock, counting_mint)
        adapters = self.busy_adapters()
        before_bytes = self.read_bytes()
        authority = self.authority_bytes()
        listing = sorted(os.listdir(self.directory))
        head = self.head()
        inputs = materialize(adapters, self.mission_id, head)
        for _ in range(3):
            report = service.observe(self.mission_id, copy.deepcopy(inputs))
        self.assertEqual(self.read_bytes(), before_bytes)
        self.assertEqual(self.authority_bytes(), authority)
        self.assertEqual(sorted(os.listdir(self.directory)), listing)
        self.assertEqual(minted, [])
        self.assertEqual(len(loads), 3)
        # The harness consulted each adapter exactly once, with the
        # Mission id and the head cursor; the package consulted nothing.
        for kind, adapter in adapters.items():
            self.assertEqual(len(adapter.calls), 1, kind)
            self.assertEqual(adapter.calls[0], (self.mission_id, head))
        self.assertIsNone(report["provenance"]["moved_during_collection"])
        flat = json.dumps(report)
        for absent in ("authorization_digest", "decision_id", "consumed_by",
                       "authority_ledger", "expires_at", "revoked", "reserved_at"):
            self.assertNotIn(absent, flat, absent)
        self.assertEqual(self.service.get(self.mission_id)["record"]["state"],
                         mission_record.STATE_AUTHORIZED)
        self.assertIsNone(self.ma.reconcile_registry(self.store.load()))

    def test_O1_a_pure_report_over_a_copy_changes_nothing(self):
        document = self.store.load()
        frozen = copy.deepcopy(document)
        mission = document["missions"][self.mission_id]
        state = document["mission_state"][self.mission_id]
        activation = ms.latest_activation(state)
        contract_ = mst.activation_contract(document, mission, activation, "activation")
        _, answers = ob.normalize_inputs(self.inputs(self.busy_adapters()))
        status = {"active": True, "current": True, "authority_live": True,
                  "problem": None, "live_authorization": True}
        report = ob.report(mission, state, contract_, status,
                           mst.registry_view(document), self.clock(), answers)
        report["progress"]["value"]["progress"] = "tampered"
        report["artifacts"]["value"][0]["key"] = "tampered"
        self.assertEqual(document, frozen)
        ob.report(mission, None, None, dict(status, active=False, current=False,
                                            authority_live=False),
                  mst.registry_view(document), self.clock(), answers)
        self.assertEqual(document, frozen)


# ====================================================================
# O2. The canonical facts, bound to revision and cursor
# ====================================================================


class O2CanonicalFactTests(ObservationFixture):

    def test_O2_every_fact_is_present_closed_and_bound_to_the_head_cursor(self):
        report = self.observe(self.busy_adapters())
        self.assertEqual(set(report), set(ob.OBSERVATION_KEYS))
        head = self.head()
        self.assertEqual(report["cursor"], head)
        self.assertEqual(report["revision"], 1)
        self.assertEqual(report["sequence"], 3)
        self.assertEqual(report["observed_at"], self.clock())
        facts = [k for k in ob.OBSERVATION_KEYS
                 if isinstance(report[k], dict) and set(report[k]) == set(ob.FACT_KEYS)]
        self.assertEqual(set(facts), {
            "phase", "pending_decisions", "progress", "contract", "task", "review",
            "candidate", "proof", "evidence", "artifacts", "blockers", "dependencies",
            "readiness", "budget", "continuation", "delivery_receipts", "delivery"})
        for key in facts:
            self.assertEqual(report[key]["revision"], head["revision"], key)
            self.assertEqual(report[key]["position"], head["position"], key)
        self.assertEqual(report["provenance"]["cursor"], head)
        self.assertEqual(report["provenance"]["journal_digest_sha256"],
                         head["journal_digest_sha256"])
        self.assertEqual(report["provenance"]["record_source"], ob.SOURCE_RECORD)
        self.assertEqual(report["provenance"]["freshness_bound_seconds"],
                         ob.REPORTED_FRESHNESS_BOUND_SECONDS)
        self.assertIsNone(report["provenance"]["moved_during_collection"])
        self.assertEqual(report["provenance"]["drift"], [])
        for kind in ADAPTER_FACTS:
            self.assertEqual(report["provenance"]["sources"][kind]["source"],
                             "adapter:" + kind)
        updated_at = self.document()["mission_state"][self.mission_id]["updated_at"]
        self.assertEqual(report["time"], {"now": self.clock(),
                                          "record_updated_at": updated_at,
                                          "source": ob.SOURCE_CLOCK})

    def test_O2_the_canonical_facts_are_verified_from_the_record(self):
        report = self.observe(self.busy_adapters())
        self.assert_fact(report["phase"], ob.STANDING_VERIFIED)
        self.assertEqual(report["phase"]["value"], {
            "state": mission_record.STATE_AUTHORIZED, "current_revision": 1,
            "revisions": 1})
        self.assert_fact(report["pending_decisions"], ob.STANDING_VERIFIED)
        self.assertEqual(report["pending_decisions"]["value"]["awaiting_decision"], False)
        self.assertTrue(report["pending_decisions"]["value"]["live_authorization"])
        self.assert_fact(report["progress"], ob.STANDING_VERIFIED)
        self.assertEqual(report["progress"]["value"],
                         {"progress": ms.PROGRESS_IN_PROGRESS, "closure_reason": None})
        self.assert_fact(report["contract"], ob.STANDING_VERIFIED)
        self.assertEqual(report["contract"]["value"]["current"], True)
        self.assertEqual(report["contract"]["value"]["authority_live"], True)
        self.assertEqual(report["contract"]["value"]["revision"], 1)
        self.assert_fact(report["proof"], ob.STANDING_VERIFIED)
        self.assertFalse(report["proof"]["value"]["satisfied"])
        self.assertEqual(report["proof"]["value"]["requirements"],
                         {"tests_pass": self.mp.REQUIREMENT_MISSING})
        self.assert_fact(report["evidence"], ob.STANDING_VERIFIED)
        self.assertEqual(report["evidence"]["value"], [])
        self.assert_fact(report["artifacts"], ob.STANDING_VERIFIED)
        (artifact,) = report["artifacts"]["value"]
        self.assertEqual(artifact["artifact_id"], self.artifact["artifact_id"])
        self.assertEqual(artifact["available_reported"], True)
        self.assertIsNone(artifact["observed_digest_sha256"])
        self.assertIsNone(artifact["matches_observed"])
        self.assertIn("nothing is dereferenced", report["artifacts"]["detail"])
        self.assert_fact(report["blockers"], ob.STANDING_VERIFIED)
        self.assertEqual(report["blockers"]["value"], {"active": [], "hard_active": False})
        self.assert_fact(report["dependencies"], ob.STANDING_VERIFIED,
                         source=ob.SOURCE_REGISTRY)
        self.assertEqual(report["dependencies"]["value"]["prerequisite_problems"], [])
        self.assertTrue(report["dependencies"]["value"]["satisfied"])
        self.assert_fact(report["readiness"], ob.STANDING_VERIFIED)
        self.assertFalse(report["readiness"]["value"]["satisfied"])
        self.assert_fact(report["budget"], ob.STANDING_VERIFIED)
        self.assertEqual(report["budget"]["value"]["attempts_consumed"], 0)
        self.assert_fact(report["continuation"], ob.STANDING_VERIFIED)
        self.assertEqual(report["continuation"]["value"],
                         {"attempts": 0, "checkpoints": 0, "latest_checkpoint": None})
        self.assert_fact(report["delivery_receipts"], ob.STANDING_VERIFIED)
        self.assertEqual(report["delivery_receipts"]["value"],
                         {"recorded": [], "attested": [], "unattested": [],
                          "effects_completed": [], "report_bound_to": None,
                          "report_bound_attested": None, "report_fresh": None})
        self.assertIsNone(report["reconciliation"])
        blocker = self.call("open_blocker", self.mission_id, "disk_full", "no space")
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        receipt = self.call("record_artifact", self.mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, "receipt:42",
                            None, True, [])
        report = self.observe()
        self.assertEqual(report["progress"]["value"]["progress"], ms.PROGRESS_BLOCKED)
        self.assertEqual(report["blockers"]["value"]["hard_active"], True)
        self.assertEqual(report["blockers"]["value"]["active"][0]["blocker_id"],
                         blocker["blocker_id"])
        self.assertTrue(report["readiness"]["value"]["satisfied"])
        self.assertEqual(report["readiness"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(report["delivery_receipts"]["value"]["recorded"],
                         [receipt["artifact_id"]])
        # A generic receipt reference is recorded, readable and UNATTESTED:
        # never valid, never a completed effect, by its mere presence.
        self.assertEqual(report["delivery_receipts"]["value"]["unattested"],
                         [receipt["artifact_id"]])
        self.assertEqual(report["delivery_receipts"]["value"]["attested"], [])
        self.assertEqual(report["delivery_receipts"]["value"]["effects_completed"], [])
        self.assertIn("recorded receipt references", report["delivery_receipts"]["detail"])

    def test_O2_bindings_move_with_an_edit_and_with_an_event(self):
        before = self.observe()
        self.edit(self.mission_id, 1, proof_contract=contract(required_dependencies=[]))
        after_edit = self.observe()
        self.assertEqual(after_edit["cursor"]["position"], before["cursor"]["position"])
        self.assertEqual(after_edit["cursor"]["revision"], 2)
        self.assertEqual(after_edit["revision"], 2)
        for key in ("phase", "progress", "proof"):
            self.assertEqual(after_edit[key]["revision"], 2, key)
            self.assertEqual(after_edit[key]["position"], 3, key)
        self.assertEqual(after_edit["phase"]["value"]["state"],
                         mission_record.STATE_AWAITING_DECISION)
        self.assertTrue(after_edit["pending_decisions"]["value"]["awaiting_decision"])
        self.assertFalse(after_edit["pending_decisions"]["value"]["live_authorization"])
        self.assertEqual(after_edit["contract"]["value"]["current"], False)
        self.assertEqual(after_edit["contract"]["value"]["problem"],
                         mss.PROBLEM_CONTRACT_STALE)
        self.assertIn(ob.HOLD_CONTRACT_NOT_CURRENT, after_edit["completion"]["holds"])
        self.approve(self.mission_id, 2)
        self.call("activate_proof_contract", self.mission_id)
        after_event = self.observe()
        self.assertEqual(after_event["cursor"]["position"], 4)
        self.assertEqual(after_event["cursor"]["revision"], 2)
        self.assertEqual(after_event["progress"]["position"], 4)
        self.assertEqual(after_event["contract"]["value"]["current"], True)
        self.assertNotEqual(after_event["cursor"]["journal_digest_sha256"],
                            before["cursor"]["journal_digest_sha256"])

    def test_O2_reports_collected_at_an_older_cursor_are_reported_as_moved(self):
        stale_inputs = self.inputs({"task": Counting(self.answer("ACTIVE"))})
        same = self.observe_raw(stale_inputs)
        self.assertIsNone(same["provenance"]["moved_during_collection"])
        self.call("record_claim", self.mission_id, "tests_pass", "another")
        moved = self.observe_raw(stale_inputs)
        self.assertEqual(moved["provenance"]["moved_during_collection"], {
            "collected_at": stale_inputs["cursor"], "head": self.head()})
        self.assertEqual(moved["cursor"], self.head())
        self.assertEqual(moved["task"]["position"], 4)
        self.assertNotEqual(stale_inputs["cursor"], moved["cursor"])
        self.edit(self.mission_id, 1, proof_contract=contract(required_dependencies=[]))
        revised = self.observe_raw(stale_inputs)
        self.assertEqual(revised["provenance"]["moved_during_collection"]["head"][
            "revision"], 2)
        # No cursor supplied: not moved, but not current either — the
        # collection provenance is UNKNOWN, and stays so.
        unknown = self.observe_raw({"cursor": None, "reports": {}})
        self.assertIsNone(unknown["provenance"]["moved_during_collection"])
        self.assertEqual(unknown["provenance"]["collection"]["status"],
                         ob.COLLECTION_UNKNOWN)
        self.assertEqual(same["provenance"]["collection"]["status"], ob.COLLECTION_CURRENT)
        self.assertEqual(moved["provenance"]["collection"]["status"], ob.COLLECTION_MOVED)


# ====================================================================
# O3. The six-term vocabulary stays distinct
# ====================================================================


class O3VocabularyTests(ObservationFixture):

    def test_O3_all_six_terms_appear_and_none_substitutes(self):
        report = self.observe(self.busy_adapters())
        self.assert_fact(report["task"], ob.STANDING_REPORTED, ob.FRESHNESS_FRESH,
                         source="adapter:task")
        self.assertEqual(report["task"]["value"], rc.TASK_REPORT_ACTIVE)
        self.assertEqual(report["task"]["observed_at"], self.clock())
        self.assert_fact(report["candidate"], ob.STANDING_REPORTED, ob.FRESHNESS_STALE,
                         source="adapter:candidate")
        self.assert_fact(report["review"], ob.STANDING_UNKNOWN, source="adapter:review")
        self.assertIsNone(report["review"]["value"])
        self.assertIsNone(report["review"]["observed_at"])
        self.assert_fact(report["delivery"], ob.STANDING_UNAVAILABLE,
                         source="adapter:delivery")
        self.assertEqual(report["delivery"]["detail"], "RuntimeError: busy source")
        self.assertIsNone(report["delivery"]["value"])
        without = self.observe({"review": Counting(None)})
        self.assertEqual(without["review"]["standing"], ob.STANDING_UNKNOWN)
        self.assertEqual(without["task"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertEqual(without["task"]["detail"], "no report supplied")
        self.assertNotEqual(without["task"]["standing"], without["review"]["standing"])
        loud = self.observe({
            "task": Counting(self.answer(rc.TASK_REPORT_COMPLETE)),
            "review": Counting(self.answer(rc.REVIEW_REPORT_APPROVE)),
        })
        self.assertEqual(loud["task"]["standing"], ob.STANDING_REPORTED)
        self.assertEqual(loud["review"]["standing"], ob.STANDING_REPORTED)
        self.assertEqual(loud["progress"]["standing"], ob.STANDING_VERIFIED)
        self.assertFalse(loud["completion"]["verified_success"])
        seen = set()
        for key in ob.OBSERVATION_KEYS:
            fact = report[key]
            if isinstance(fact, dict) and set(fact) == set(ob.FACT_KEYS):
                seen.add(fact["standing"])
                if fact["freshness"] is not None:
                    seen.add(fact["freshness"])
                self.assertIn(fact["standing"], ob.STANDINGS)
                self.assertIn(fact["freshness"], (None,) + ob.FRESHNESS_TERMS)
                self.assertNotIn(fact["freshness"], ob.STANDINGS)
                self.assertNotIn(fact["standing"], ob.FRESHNESS_TERMS)
        self.assertEqual(seen, set(ob.FACT_TERMS))

    def test_O3_freshness_is_age_against_a_module_constant_bound(self):
        bound = ob.REPORTED_FRESHNESS_BOUND_SECONDS
        self.assertEqual(bound, 600)
        self.assertEqual(bound, rc.REPORTED_FRESHNESS_BOUND_SECONDS)
        at_bound = self.observe({"task": Counting(self.answer("ACTIVE", age=bound))})
        self.assertEqual(at_bound["task"]["freshness"], ob.FRESHNESS_FRESH)
        past = self.observe({"task": Counting(self.answer("ACTIVE", age=bound + 1))})
        self.assertEqual(past["task"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(past["task"]["standing"], ob.STANDING_REPORTED)
        future = self.observe({"task": Counting(self.answer("ACTIVE", age=-1))})
        self.assertEqual(future["task"]["freshness"], ob.FRESHNESS_STALE)
        self.assertIsNone(self.observe({"task": Counting(None)})["task"]["freshness"])
        self.assertIsNone(self.observe({})["task"]["freshness"])
        inputs = self.inputs({"task": Counting(self.answer("ACTIVE"))})
        fresh = self.observe_raw(inputs)
        self.clock.advance(bound + 1)
        stale = self.observe_raw(inputs)
        self.assertEqual(fresh["task"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(stale["task"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(dict(fresh["task"], freshness=None),
                         dict(stale["task"], freshness=None))
        self.make_local_complete(self.mission_id)
        report = self.observe()
        self.assertEqual(report["evidence"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(report["proof"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(report["readiness"]["freshness"], ob.FRESHNESS_FRESH)
        self.clock.advance(3601)
        report = self.observe()
        self.assertEqual(report["evidence"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(report["proof"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(report["proof"]["value"]["requirements"]["tests_pass"],
                         self.mp.REQUIREMENT_STALE)
        self.assertEqual(report["readiness"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(report["evidence"]["standing"], ob.STANDING_VERIFIED)


# ====================================================================
# O4. The input seam fails closed; busy fixtures stay readable
# ====================================================================


class O4InputSeamTests(ObservationFixture):

    def test_O4_malformed_input_refuses_with_its_own_code_and_nothing_else_happens(self):
        big = dict(("k%d" % i, "c" * 64) for i in range(rc.MAX_OBSERVED_CANDIDATE_KEYS + 1))
        deep = "x"
        for _ in range(ob.MAX_OBSERVATION_INPUT_DEPTH + 1):
            deep = {"d": deep}
        many = dict(("k%d" % i, i) for i in range(ob.MAX_OBSERVATION_INPUT_ITEMS))
        cases = {
            "unknown top key": {"cursor": None, "reports": {}, "extra": 1},
            "missing reports": {"cursor": None},
            "reports not an object": {"cursor": None, "reports": ["task"]},
            "unknown kind": {"cursor": None, "reports": {"herd": None}},
            "report wrong keys": {"cursor": None, "reports": {"task": {"status": "x"}}},
            "report extra key": {"cursor": None, "reports": {"task": dict(
                self.answer("ACTIVE"), extra=1)}},
            "wrong vocabulary": {"cursor": None, "reports": {"task": self.answer("DONE")}},
            "bool timestamp": {"cursor": None, "reports": {"task": {
                "value": "ACTIVE", "observed_at": True}}},
            "negative timestamp": {"cursor": None, "reports": {"task": {
                "value": "ACTIVE", "observed_at": -1}}},
            "candidate over bound": {"cursor": None, "reports": {"candidate": self.answer(
                {"baseline_digest_sha256": None, "artifact_digests": big})}},
            "candidate bad digest": {"cursor": None, "reports": {"candidate": self.answer(
                {"baseline_digest_sha256": None, "artifact_digests": {"k": "zz"}})}},
            "delivery enum only": {"cursor": None, "reports": {"delivery": self.answer(
                "VALID")}},
            "delivery absent with receipt": {"cursor": None, "reports": {
                "delivery": self.answer(dict(self.delivery("ABSENT"),
                                             receipt_artifact_id=hexid("mf", 1)))}},
            "delivery valid without receipt": {"cursor": None, "reports": {
                "delivery": self.answer(dict(self.delivery("VALID"),
                                             receipt_artifact_id=None))}},
            "delivery valid without digest": {"cursor": None, "reports": {
                "delivery": self.answer(dict(self.delivery("VALID"),
                                             receipt_digest_sha256=None))}},
            "delivery old shape": {"cursor": None, "reports": {"delivery": self.answer(
                {"status": "VALID", "receipt_artifact_id": hexid("mf", 1),
                 "locator": "r"})}},
            "unavailable detail over bound": {"cursor": None, "reports": {"task": {
                "unavailable": "x" * (ob.MAX_REPORT_DETAIL_CHARS + 1)}}},
            "unavailable detail not a string": {"cursor": None,
                                                "reports": {"task": {"unavailable": 1}}},
            "unavailable with extra key": {"cursor": None, "reports": {"task": {
                "unavailable": "x", "value": "ACTIVE"}}},
            "too deep": {"cursor": None, "reports": {"task": deep}},
            "too many items": {"cursor": None, "reports": {"task": many}},
            "cursor malformed": {"cursor": {"position": 1}, "reports": {}},
            "cursor bad digest": {"cursor": dict(self.head(),
                                                 journal_digest_sha256="zz"),
                                  "reports": {}},
            "not an object": "ACTIVE",
            "a list": [],
        }
        # Round-2 finding 3: oversized input is refused by size before it
        # is copied, formatted or scanned, and no refusal echoes it.
        cases["million-char value"] = {"cursor": None, "reports": {"task": {
            "value": "x" * 1000000, "observed_at": 1}}}
        cases["million-char key"] = {"cursor": None, "reports": {"task": {
            "x" * 1000000: 1}}}
        cases["million-char unknown top key"] = {"cursor": None, "reports": {},
                                                 "x" * 1000000: 1}
        cases["hundred-thousand keys"] = {"cursor": None, "reports": {"task": dict(
            ("k%d" % i, i) for i in range(100000))}}
        cases["huge int"] = {"cursor": None, "reports": {"task": {
            "value": "ACTIVE", "observed_at": 10 ** 400}}}
        cases["over-long key at depth"] = {"cursor": None, "reports": {"candidate": {
            "value": {"baseline_digest_sha256": None,
                      "artifact_digests": {"k" * (ob.MAX_OBSERVATION_INPUT_KEY_CHARS + 1):
                                           "c" * 64}},
            "observed_at": 1}}}
        before = self.read_bytes()
        sequence = self.seq(self.mission_id)
        for label, inputs in cases.items():
            with self.subTest(label):
                exc = self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT,
                                         self.observe_raw, inputs)
                self.assertIn("observation input", str(exc))
                self.assertLess(len(str(exc)), 400, label)
                self.assertNotIn("xxxx", str(exc))
                self.assertNotIn("DONE", str(exc))
                self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                                   self.mission_id, self.oid(), sequence, inputs,
                                   self.context)
        self.assertEqual(json.loads(self.read_bytes())["mission_state"],
                         json.loads(before)["mission_state"])
        ok = dict(("k%d" % i, "c" * 64) for i in range(rc.MAX_OBSERVED_CANDIDATE_KEYS))
        accepted = self.observe_raw({"cursor": self.head(), "reports": {
            "candidate": self.answer({"baseline_digest_sha256": None,
                                      "artifact_digests": ok}),
            "task": None,
            "review": {"unavailable": "x" * ob.MAX_REPORT_DETAIL_CHARS},
        }})
        self.assertEqual(accepted["candidate"]["standing"], ob.STANDING_REPORTED)
        self.assertEqual(accepted["task"]["standing"], ob.STANDING_UNKNOWN)
        self.assertEqual(accepted["review"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertEqual(len(accepted["review"]["detail"]), ob.MAX_REPORT_DETAIL_CHARS)
        self.assertEqual(accepted["delivery"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertEqual(accepted["delivery"]["detail"], "no report supplied")
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           self.mission_id, self.oid(), sequence,
                           {"cursor": None, "reports": {}}, self.context)
        self.assertRefuses(self.ma.PROBLEM_UNKNOWN_MISSION, self.observe, None,
                           hexid("mn", 0x77))

    def test_O4_misbehaving_sources_are_materialized_truthfully_and_stay_distinct(self):
        huge = "x" * (ob.MAX_REPORT_DETAIL_CHARS * 3)
        raising = Counting(raise_=ValueError(huge))
        inputs = self.inputs({"task": raising})
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.observe_raw, inputs)
        inputs["reports"]["task"]["unavailable"] = inputs["reports"]["task"][
            "unavailable"][:ob.MAX_REPORT_DETAIL_CHARS]
        report = self.observe_raw(inputs)
        self.assertEqual(report["task"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertEqual(len(report["task"]["detail"]), ob.MAX_REPORT_DETAIL_CHARS)
        self.assertEqual(len(raising.calls), 1)
        cases = {
            "raises": (Counting(raise_=RuntimeError("down")), ob.STANDING_UNAVAILABLE),
            "no fact": (Counting(None), ob.STANDING_UNKNOWN),
            "null value": (Counting(self.answer(None)), ob.STANDING_UNKNOWN),
            "fresh": (Counting(self.answer("ACTIVE")), ob.STANDING_REPORTED),
        }
        for label, (adapter, standing) in cases.items():
            with self.subTest(label):
                report = self.observe({"task": adapter})
                self.assertEqual(report["task"]["standing"], standing)
                self.assertEqual(len(adapter.calls), 1)

    def test_O4_busy_operator_and_herdr_fixture_one_load_no_lock_no_wait(self):
        for index in range(12):
            self.call("record_claim", self.mission_id, "tests_pass", "claim %d" % index)
        self.call("open_blocker", self.mission_id, "flaky_network", "degraded")
        self.call("record_continuation", self.mission_id, "again")
        self.call("record_checkpoint", self.mission_id, ["a"], ["b"], "retry", "stop")
        adapters = self.busy_adapters()
        store = mst.MissionStore(self.directory)
        loads = []
        real_load = store.load
        store.load = lambda: (loads.append(1), real_load())[1]
        store.lock = lambda: (_ for _ in ()).throw(AssertionError("no lock"))
        service = self.service.__class__(store, self.clock)
        before = self.read_bytes()
        report = service.observe(self.mission_id, self.inputs(adapters))
        self.assertEqual(len(loads), 1)
        self.assertEqual(self.read_bytes(), before)
        for adapter in adapters.values():
            self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(report["sequence"], 18)
        self.assertEqual(report["task"]["standing"], ob.STANDING_REPORTED)
        self.assertEqual(report["task"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(report["candidate"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(report["review"]["standing"], ob.STANDING_UNKNOWN)
        self.assertEqual(report["delivery"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertEqual(report["continuation"]["value"]["attempts"], 1)
        self.assertEqual(report["continuation"]["value"]["checkpoints"], 1)
        self.assertEqual(report["blockers"]["value"]["active"][0]["severity"],
                         ms.BLOCKER_SEVERITY_DEGRADED)
        self.assertEqual(report["progress"]["value"]["progress"], ms.PROGRESS_IN_PROGRESS)


# ====================================================================
# O5. False completion is blocked
# ====================================================================


class O5FalseCompletionTests(ObservationFixture):

    def loud(self, review=rc.REVIEW_REPORT_APPROVE, task=rc.TASK_REPORT_COMPLETE,
             **more):
        adapters = {"task": Counting(self.answer(task))}
        if review is not None:
            adapters["review"] = Counting(self.answer(review))
        adapters.update(more)
        return adapters

    def test_O5_reported_complete_without_canonical_closure_is_not_verified(self):
        report = self.observe(self.loud())
        completion = report["completion"]
        self.assertEqual(set(completion), set(ob.COMPLETION_KEYS))
        self.assertTrue(completion["reported_complete"])
        self.assertTrue(completion["reported_success"])
        self.assertFalse(completion["closure_verified"])
        self.assertFalse(completion["verified_success"])
        self.assertTrue(completion["contradicted"])
        self.assertIn(ob.HOLD_NO_CANONICAL_CLOSURE, completion["holds"])
        self.assertIn(ob.HOLD_PROOF_NOT_SATISFIED, completion["holds"])
        self.assertIsNone(completion["closure"])
        self.assertIn(ob.HOLD_REVIEW_NOT_APPROVED,
                      self.observe(self.loud(rc.REVIEW_REPORT_REJECT))["completion"]["holds"])
        self.assertIn(ob.HOLD_REVIEW_NOT_APPROVED,
                      self.observe(self.loud(rc.REVIEW_REPORT_PENDING))["completion"]["holds"])
        unknown = self.observe(dict(self.loud(None), review=Counting(None)))
        self.assertIn(ob.HOLD_REVIEW_UNKNOWN, unknown["completion"]["holds"])
        self.assertNotIn(ob.HOLD_REVIEW_UNAVAILABLE, unknown["completion"]["holds"])
        unavailable = self.observe(self.loud(None))
        self.assertIn(ob.HOLD_REVIEW_UNAVAILABLE, unavailable["completion"]["holds"])
        self.assertNotIn(ob.HOLD_REVIEW_UNKNOWN, unavailable["completion"]["holds"])
        self.assertFalse(unavailable["completion"]["reported_success"])
        for report_ in (report, unknown, unavailable):
            self.assertTrue(report_["completion"]["holds"])
            for hold in report_["completion"]["holds"]:
                self.assertIn(hold, ob.HOLDS)

    def test_O5_contradictory_evidence_blocks_a_loud_completion(self):
        first = self.call("submit_evidence", self.mission_id, "tests_pass",
                          mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                          [self.artifact["artifact_id"]])
        second = self.call("submit_evidence", self.mission_id, "tests_pass",
                           mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "f" * 64,
                           [self.artifact["artifact_id"]])
        self.call("accept_evidence", self.mission_id, first["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("accept_evidence", self.mission_id, second["evidence_id"], "f" * 64,
                  context=self.other)
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        report = self.observe(self.loud())
        self.assertEqual(report["proof"]["value"]["requirements"]["tests_pass"],
                         self.mp.REQUIREMENT_CONTRADICTED)
        completion = report["completion"]
        self.assertFalse(completion["verified_success"])
        self.assertTrue(completion["contradicted"])
        self.assertIn(ob.HOLD_EVIDENCE_CONTRADICTED, completion["holds"])
        self.assertIn(ob.HOLD_PROOF_NOT_SATISFIED, completion["holds"])
        self.assertRefuses(self.mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", self.mission_id, "done")
        standings = [e["standing"] for e in report["evidence"]["value"]]
        self.assertEqual(standings, [ob.STANDING_VERIFIED, ob.STANDING_VERIFIED])

    def test_O5_historical_closure_is_distinct_from_present_verified_success(self):
        self.make_local_complete(self.mission_id)
        self.call("complete_successfully", self.mission_id, "proof accepted")
        quiet = self.observe()
        self.assertTrue(quiet["completion"]["closure_verified"])
        self.assertTrue(quiet["completion"]["verified_success"])
        self.assertFalse(quiet["completion"]["reported_complete"])
        self.assertFalse(quiet["completion"]["contradicted"])
        self.assertEqual(quiet["completion"]["closure"]["progress"], ms.PROGRESS_COMPLETED)
        self.assertEqual(quiet["completion"]["holds"], [ob.HOLD_REVIEW_UNAVAILABLE])
        agreeing = self.observe(self.loud())
        self.assertTrue(agreeing["completion"]["verified_success"])
        self.assertTrue(agreeing["completion"]["reported_success"])
        self.assertFalse(agreeing["completion"]["contradicted"])
        self.assertEqual(agreeing["completion"]["holds"], [])
        # Round-2 finding 6: a fresh FAILED beside an APPROVE. The closure
        # stands as history; present success is BLOCKED and the hold says
        # why.
        contrary = self.observe(self.loud(task=rc.TASK_REPORT_FAILED))
        self.assertTrue(contrary["completion"]["closure_verified"])
        self.assertTrue(contrary["completion"]["contradicted"])
        self.assertFalse(contrary["completion"]["verified_success"])
        self.assertEqual(contrary["completion"]["holds"], [ob.HOLD_TASK_CONTRADICTS])
        self.assertEqual(contrary["task"]["value"], rc.TASK_REPORT_FAILED)
        for task in (rc.TASK_REPORT_BLOCKED, rc.TASK_REPORT_NOT_STARTED):
            blocked = self.observe(self.loud(task=task))
            self.assertFalse(blocked["completion"]["verified_success"], task)
            self.assertIn(ob.HOLD_TASK_CONTRADICTS, blocked["completion"]["holds"])
        # A stale or moved APPROVE affirms nothing: present success needs
        # an applicable one.
        stale_approve = self.observe({"task": Counting(self.answer("COMPLETE")),
                                      "review": Counting(self.answer("APPROVE", age=601))})
        self.assertFalse(stale_approve["completion"]["verified_success"])
        self.assertEqual(stale_approve["completion"]["holds"],
                         [ob.HOLD_REVIEW_INAPPLICABLE])
        self.assertFalse(stale_approve["completion"]["reported_success"])
        # Finding 5's reproduction: a REJECT review and a mismatching
        # candidate against a completed record.
        rejected = self.observe(self.loud(
            rc.REVIEW_REPORT_REJECT,
            candidate=Counting(self.answer(self.candidate(test_log="d" * 64)))))
        self.assertTrue(rejected["completion"]["closure_verified"])
        self.assertFalse(rejected["completion"]["verified_success"])
        self.assertTrue(rejected["completion"]["contradicted"])
        self.assertIn(ob.HOLD_REVIEW_NOT_APPROVED, rejected["completion"]["holds"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, rejected["completion"]["holds"])
        self.assertEqual(rejected["completion"]["closure"]["progress"],
                         ms.PROGRESS_COMPLETED)
        self.assertEqual(self.service.get_state(self.mission_id)["progress"],
                         ms.PROGRESS_COMPLETED)
        # Proof expiry: closure stands, present success withdrawn.
        self.clock.advance(3601)
        expired = self.observe(self.loud())
        self.assertTrue(expired["completion"]["closure_verified"])
        self.assertFalse(expired["completion"]["verified_success"])
        self.assertIn(ob.HOLD_PROOF_NOT_SATISFIED, expired["completion"]["holds"])
        self.assertEqual(expired["proof"]["value"]["requirements"]["tests_pass"],
                         self.mp.REQUIREMENT_STALE)
        self.assertEqual(expired["proof"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(ob.INFORMATION_HOLDS,
                         frozenset((ob.HOLD_REVIEW_UNAVAILABLE, ob.HOLD_REVIEW_UNKNOWN)))

    # The holds that CAN coexist with a COMPLETED closure. Each is isolated
    # below against a completed record, where "present success is False"
    # is decisive because nothing else blocks it. The remaining three
    # holds cannot arise on a completed record at all: a HARD blocker
    # cannot be opened after closure (terminal), contradicted evidence
    # prevents the closure in the first place, and no_canonical_closure
    # is the absence of one — so for those the table proves only that the
    # hold is produced, and claims nothing more.
    HOLDS_ON_A_COMPLETED_RECORD = (
        ob.HOLD_REVIEW_UNAVAILABLE, ob.HOLD_REVIEW_UNKNOWN, ob.HOLD_REVIEW_NOT_APPROVED,
        ob.HOLD_REVIEW_INAPPLICABLE, ob.HOLD_TASK_CONTRADICTS, ob.HOLD_CANDIDATE_DRIFTED,
        ob.HOLD_BASELINE_DRIFTED, ob.HOLD_PROOF_NOT_SATISFIED,
        ob.HOLD_CONTRACT_NOT_CURRENT, ob.HOLD_PREREQUISITES,
        # Round 11: an applicable candidate report that omits a recorded
        # artifact, or the anchored baseline, confirms nothing.
        ob.HOLD_CANDIDATE_UNCONFIRMED, ob.HOLD_BASELINE_UNCONFIRMED,
    )
    HOLDS_ONLY_ON_AN_OPEN_RECORD = (
        ob.HOLD_HARD_BLOCKER_ACTIVE, ob.HOLD_EVIDENCE_CONTRADICTED,
        ob.HOLD_NO_CANONICAL_CLOSURE,
    )

    def test_O5_every_hold_is_produced_and_each_completed_record_hold_is_decisive(self):
        """Two claims, each exactly what the table proves. (1) Every member
        of the closed ``HOLDS`` tuple is produced by some scenario, so a
        new hold without a scenario fails here. (2) For every hold that can
        coexist with a COMPLETED closure, a scenario on a completed record
        that produces THAT hold (and at most the information holds) has
        ``closure_verified`` True and ``verified_success`` False when the
        hold blocks, True when it is an information hold — the decisive
        value, with nothing else blocking."""
        self.assertEqual(set(self.HOLDS_ON_A_COMPLETED_RECORD)
                         | set(self.HOLDS_ONLY_ON_AN_OPEN_RECORD), set(ob.HOLDS))
        self.assertFalse(set(self.HOLDS_ON_A_COMPLETED_RECORD)
                         & set(self.HOLDS_ONLY_ON_AN_OPEN_RECORD))
        self.make_local_complete(self.mission_id)
        self.call("complete_successfully", self.mission_id, "proof accepted")

        def base():
            # Fresh at the moment of materialization, whatever the clock.
            return {"task": Counting(self.answer("COMPLETE")),
                    "review": Counting(self.answer("APPROVE"))}

        def scenario(**over):
            adapters = base()
            adapters.update(over)
            return self.observe(adapters)

        completed = {}
        completed[ob.HOLD_REVIEW_UNAVAILABLE] = scenario(
            review=Counting(raise_=RuntimeError("down")))
        completed[ob.HOLD_REVIEW_UNKNOWN] = scenario(review=Counting(None))
        completed[ob.HOLD_REVIEW_NOT_APPROVED] = scenario(
            review=Counting(self.answer("REJECT")))
        completed[ob.HOLD_REVIEW_INAPPLICABLE] = scenario(review=Counting(
            self.answer("APPROVE", age=ob.REPORTED_FRESHNESS_BOUND_SECONDS + 1)))
        completed[ob.HOLD_TASK_CONTRADICTS] = scenario(task=Counting(self.answer("FAILED")))
        completed[ob.HOLD_CANDIDATE_DRIFTED] = scenario(candidate=Counting(
            self.answer(self.candidate(test_log="d" * 64))))
        completed[ob.HOLD_CANDIDATE_UNCONFIRMED] = scenario(candidate=Counting(
            self.answer(self.candidate())))
        sibling = self.ready_mission(required_dependencies=[])
        self.make_local_complete(sibling)
        # (round 11: the anchoring and the drifting reports NAME the recorded
        # artifact, so each scenario isolates the baseline hold it claims)
        self.service.reconcile(sibling, self.oid(), self.seq(sibling), self.inputs(
            {"candidate": Counting(self.answer(
                self.candidate(baseline="1" * 64, test_log=HEX_A)))},
            sibling), self.context)
        self.call("complete_successfully", sibling, "done")
        completed[ob.HOLD_BASELINE_DRIFTED] = self.service.observe(sibling, self.inputs(
            dict(base(), candidate=Counting(self.answer(
                self.candidate(baseline="2" * 64, test_log=HEX_A)))),
            sibling))
        completed[ob.HOLD_BASELINE_UNCONFIRMED] = self.service.observe(sibling, self.inputs(
            dict(base(), candidate=Counting(self.answer(
                self.candidate(baseline=None, test_log=HEX_A)))),
            sibling))
        expired = self.ready_mission(required_dependencies=[])
        self.make_local_complete(expired)
        self.call("complete_successfully", expired, "done")
        self.clock.advance(3601)
        completed[ob.HOLD_PROOF_NOT_SATISFIED] = self.service.observe(
            expired, self.inputs(base(), expired))
        self.clock.advance(-3601)
        edited = self.ready_mission(required_dependencies=[])
        self.make_local_complete(edited)
        self.call("complete_successfully", edited, "done")
        self.edit(edited, 1, proof_contract=contract(required_dependencies=[]))
        completed[ob.HOLD_CONTRACT_NOT_CURRENT] = self.service.observe(
            edited, self.inputs(base(), edited))
        t_id = self.completed_prerequisite()
        dependent = self.dependent_on(t_id)
        self.make_local_complete(dependent, bind=t_id)
        self.call("complete_successfully", dependent, "done")
        self.edit(t_id, 1, proof_contract=contract(required_dependencies=[]))
        completed[ob.HOLD_PREREQUISITES] = self.service.observe(
            dependent, self.inputs(base(), dependent))
        self.assertEqual(set(completed), set(self.HOLDS_ON_A_COMPLETED_RECORD))
        for hold, report in completed.items():
            with self.subTest(hold):
                self.assertTrue(report["completion"]["closure_verified"], hold)
                self.assertIn(hold, report["completion"]["holds"])
                others = set(report["completion"]["holds"]) - {hold}
                self.assertTrue(others <= set(ob.INFORMATION_HOLDS), (hold, others))
                if hold in ob.INFORMATION_HOLDS:
                    self.assertTrue(report["completion"]["verified_success"], hold)
                else:
                    self.assertFalse(report["completion"]["verified_success"], hold)
        # The open-record holds: produced, on records that are not closed.
        open_only = {}
        open_ = self.ready_mission(required_dependencies=[])
        artifact = self.call("record_artifact", open_, "test_log",
                             mission_record.ARTIFACT_ROLE_VERIFICATION,
                             ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log", HEX_A, True, [])
        for digest in ("e" * 64, "f" * 64):
            submitted = self.call("submit_evidence", open_, "tests_pass",
                                  "VERIFICATION_RECORD", digest, [artifact["artifact_id"]])
            self.call("accept_evidence", open_, submitted["evidence_id"], digest,
                      context=self.other)
        open_only[ob.HOLD_EVIDENCE_CONTRADICTED] = self.service.observe(
            open_, self.inputs(base(), open_))
        blocked = self.ready_mission(required_dependencies=[])
        self.make_local_complete(blocked)
        self.call("open_blocker", blocked, "disk_full", "no space")
        open_only[ob.HOLD_HARD_BLOCKER_ACTIVE] = self.service.observe(
            blocked, self.inputs(base(), blocked))
        open_only[ob.HOLD_NO_CANONICAL_CLOSURE] = self.service.observe(
            blocked, self.inputs(base(), blocked))
        self.assertEqual(set(open_only), set(self.HOLDS_ONLY_ON_AN_OPEN_RECORD))
        for hold, report in open_only.items():
            with self.subTest(hold):
                self.assertIn(hold, report["completion"]["holds"])
                self.assertFalse(report["completion"]["closure_verified"])
        # And the service itself refuses to produce a completed record
        # carrying those: a HARD blocker after closure is terminal, and a
        # contradicted proof cannot close.
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.call, "open_blocker",
                           self.mission_id, "disk_full", "late")
        self.assertRefuses(self.mp.PROBLEM_PROOF_NOT_SATISFIED, self.call,
                           "complete_successfully", open_, "x")

    def test_O5_task5_limits_stay_truthful_in_the_report(self):
        narrative = self.call("submit_evidence", self.mission_id, "tests_pass",
                              mission_record.EVIDENCE_KIND_NARRATIVE_CLAIM, "9" * 64, [])
        submitted = self.call("submit_evidence", self.mission_id, "tests_pass",
                              mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                              "e" * 64, [self.artifact["artifact_id"]])
        report = self.observe(self.loud())
        by_id = dict((e["evidence_id"], e) for e in report["evidence"]["value"])
        self.assertEqual(by_id[narrative["evidence_id"]]["standing"],
                         ob.STANDING_REPORTED)
        self.assertFalse(by_id[narrative["evidence_id"]]["verifying_kind"])
        self.assertEqual(by_id[submitted["evidence_id"]]["standing"],
                         ob.STANDING_REPORTED)
        self.assertFalse(by_id[submitted["evidence_id"]]["accepted"])
        self.assertIsNone(by_id[submitted["evidence_id"]]["freshness"])
        self.call("accept_evidence", self.mission_id, submitted["evidence_id"], "e" * 64)
        report = self.observe(self.loud())
        by_id = dict((e["evidence_id"], e) for e in report["evidence"]["value"])
        self.assertEqual(by_id[submitted["evidence_id"]]["standing"],
                         ob.STANDING_VERIFIED)
        limits = report["completion"]["limits"]
        self.assertEqual(limits["identity"], mission_record.PROOF_TRANSPORT_CREDENTIAL_ONLY)
        self.assertEqual(limits["separation_of_duties"], "not_enforced")
        self.assertEqual(sorted(limits["non_verifying_evidence_kinds"]),
                         sorted(mission_record.NON_SATISFYING_EVIDENCE_KINDS))
        self.assertEqual(report["artifacts"]["value"][0]["available_reported"], True)
        flat = json.dumps(report)
        self.assertNotIn("human_identity_proof\": \"", flat)


# ====================================================================
# O6. Drift invalidates derived claims now
# ====================================================================


class O6DriftTests(ObservationFixture):

    def test_O6_a_live_mismatching_candidate_marks_evidence_drifted_without_reconciling(self):
        evidence_id = self.make_local_complete(self.mission_id)
        clean = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log=HEX_A)))})
        item = [e for e in clean["evidence"]["value"] if e["evidence_id"] == evidence_id][0]
        self.assertFalse(item["drifted"])
        self.assertNotIn(ob.HOLD_CANDIDATE_DRIFTED, clean["completion"]["holds"])
        before = self.read_bytes()
        live = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log="d" * 64)))})
        item = [e for e in live["evidence"]["value"] if e["evidence_id"] == evidence_id][0]
        self.assertEqual(item["standing"], ob.STANDING_VERIFIED)
        self.assertTrue(item["drifted"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, live["completion"]["holds"])
        self.assertEqual([f["kind"] for f in live["provenance"]["drift"]],
                         [rc.FINDING_CANDIDATE_DRIFT])
        artifact = [a for a in live["artifacts"]["value"] if a["key"] == "test_log"][-1]
        self.assertEqual(artifact["matches_observed"], False)
        self.assertIsNone(live["reconciliation"])
        self.assertEqual(self.read_bytes(), before)
        # A terminal Mission, which can never reconcile, still shows it.
        self.call("complete_successfully", self.mission_id, "done")
        terminal = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log="d" * 64)))})
        self.assertTrue(terminal["completion"]["closure_verified"])
        self.assertFalse(terminal["completion"]["verified_success"])
        self.assertTrue(terminal["completion"]["contradicted"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, terminal["completion"]["holds"])

    def test_O6_an_unavailable_source_preserves_previously_detected_drift(self):
        self.make_local_complete(self.mission_id)
        drifted = self.inputs({"candidate": Counting(self.answer(
            self.candidate(test_log="d" * 64)))})
        self.service.reconcile(self.mission_id, self.oid(), self.seq(self.mission_id),
                               drifted, self.context)
        self.assertEqual([f["kind"] for f in self.observe()["provenance"]["drift"]],
                         [rc.FINDING_CANDIDATE_DRIFT])
        gone = self.observe({"candidate": Counting(raise_=RuntimeError("down"))})
        self.assertEqual(gone["candidate"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertTrue([e for e in gone["evidence"]["value"] if e["accepted"]][0][
            "drifted"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, gone["completion"]["holds"])
        result = self.service.reconcile(
            self.mission_id, self.oid(), self.seq(self.mission_id),
            self.inputs({"candidate": Counting(raise_=RuntimeError("down"))}),
            self.context)
        self.assertIn(rc.FINDING_CANDIDATE_DRIFT, [f["kind"] for f in result["findings"]])
        partial = self.observe({"candidate": Counting(self.answer(self.candidate()))})
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, partial["completion"]["holds"])
        # Round-2 finding 5: a MATCHING report that is stale cannot resolve
        # the recorded drift (observation entry point).
        stale = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log=HEX_A), age=999999))})
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, stale["completion"]["holds"])
        self.assertEqual(stale["candidate"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual([f["kind"] for f in stale["provenance"]["drift"]],
                         [rc.FINDING_CANDIDATE_DRIFT])
        # Round-2 finding 4: a matching report collected at a cursor the
        # document has left cannot resolve it either, and affirms nothing.
        old = self.inputs({"candidate": Counting(self.answer(self.candidate(test_log=HEX_A))),
                           "task": Counting(self.answer("COMPLETE")),
                           "review": Counting(self.answer("APPROVE"))})
        self.call("record_claim", self.mission_id, "tests_pass", "meanwhile")
        moved = self.observe_raw(old)
        self.assertIsNotNone(moved["provenance"]["moved_during_collection"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, moved["completion"]["holds"])
        self.assertIn(ob.HOLD_REVIEW_INAPPLICABLE, moved["completion"]["holds"])
        self.assertFalse(moved["completion"]["reported_success"])
        self.assertFalse(moved["completion"]["verified_success"])
        # Round-4 finding 5, exactly: a fresh matching report with NO
        # collection cursor cannot resolve it either — absent provenance is
        # not evidence of currency.
        receipt = self.call("record_artifact", self.mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, "receipt:42",
                            "d" * 64, True, [])["artifact_id"]
        reports = {"candidate": self.answer(self.candidate(test_log=HEX_A)),
                   "review": self.answer("APPROVE"), "task": self.answer("COMPLETE"),
                   "delivery": self.answer(self.delivery("VALID", artifact=receipt))}
        # The same fresh, bound delivery report DOES bind when the
        # collection cursor is current, so the cursorless assertion below
        # tests what it names.
        current = self.observe_raw({"cursor": self.head(), "reports": reports})
        self.assertEqual(current["delivery_receipts"]["value"]["report_bound_to"], receipt)
        cursorless = self.observe_raw({"cursor": None, "reports": reports})
        self.assertEqual(cursorless["provenance"]["collection"]["status"],
                         ob.COLLECTION_UNKNOWN)
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, cursorless["completion"]["holds"])
        self.assertIn(ob.HOLD_REVIEW_INAPPLICABLE, cursorless["completion"]["holds"])
        self.assertFalse(cursorless["completion"]["reported_success"])
        self.assertIsNone(cursorless["delivery_receipts"]["value"]["report_bound_to"])
        self.assertEqual(cursorless["delivery"]["standing"], ob.STANDING_REPORTED)
        # Only a fresh, current, matching report resolves it. Round 11: the
        # same report carries no digest for the recorded ``pr_receipt``
        # artifact, so that artifact is UNOBSERVED — an omission is not a
        # confirmation — while the drift it does resolve is gone; no
        # accepted evidence references the receipt, so no hold arises.
        resolved = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log=HEX_A)))})
        self.assertNotIn(ob.HOLD_CANDIDATE_DRIFTED, resolved["completion"]["holds"])
        self.assertEqual([(f["kind"], f["subject"]) for f in resolved["provenance"]["drift"]],
                         [(rc.FINDING_CANDIDATE_UNOBSERVED, "pr_receipt")])
        self.assertNotIn(ob.HOLD_CANDIDATE_UNCONFIRMED, resolved["completion"]["holds"])
        complete = self.observe({"candidate": Counting(self.answer(
            self.candidate(test_log=HEX_A, pr_receipt="d" * 64)))})
        self.assertEqual(complete["provenance"]["drift"], [])

    def test_O6_reviewers_moved_inputs_reproduction_cannot_restore_success(self):
        # Round-2 finding 4, exactly: matching inputs collected at position
        # p, candidate drift recorded, Mission closed on local proof; the
        # old inputs reused at a later position must not remove the drift
        # hold nor restore present success, and are not stamped at the
        # later position as applicable.
        self.make_local_complete(self.mission_id)
        receipt = self.call("record_artifact", self.mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, "receipt:42",
                            "d" * 64, True, [])["artifact_id"]
        old = self.inputs({"candidate": Counting(self.answer(self.candidate(test_log=HEX_A))),
                           "review": Counting(self.answer("APPROVE")),
                           "delivery": Counting(self.answer(
                               self.delivery("VALID", artifact=receipt)))})
        # At the cursor they were collected at, the reports bind and affirm.
        at_collection = self.observe_raw(old)
        self.assertEqual(at_collection["delivery_receipts"]["value"]["report_bound_to"],
                         receipt)
        self.service.reconcile(self.mission_id, self.oid(), self.seq(self.mission_id),
                               self.inputs({"candidate": Counting(self.answer(
                                   self.candidate(test_log="d" * 64)))}), self.context)
        self.call("complete_successfully", self.mission_id, "done")
        reused = self.observe_raw(old)
        self.assertEqual(reused["provenance"]["moved_during_collection"]["collected_at"],
                         old["cursor"])
        self.assertTrue(reused["completion"]["closure_verified"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, reused["completion"]["holds"])
        self.assertFalse(reused["completion"]["verified_success"])
        self.assertTrue(reused["completion"]["contradicted"])
        self.assertIsNone(reused["delivery_receipts"]["value"]["report_bound_to"])
        self.assertEqual(reused["delivery"]["standing"], ob.STANDING_REPORTED)

    def test_O6_baseline_drift_invalidates_derived_claims_without_touching_authority(self):
        self.make_local_complete(self.mission_id)
        anchor = self.inputs({"candidate": Counting(self.answer(
            self.candidate(baseline="1" * 64, test_log=HEX_A)))})
        self.service.reconcile(self.mission_id, self.oid(), self.seq(self.mission_id),
                               anchor, self.context)
        authority = self.authority_bytes()
        evidence_before = self.document()["mission_state"][self.mission_id]["evidence"]
        moved = self.observe({"candidate": Counting(self.answer(
            self.candidate(baseline="2" * 64, test_log=HEX_A)))})
        self.assertIn(ob.HOLD_BASELINE_DRIFTED, moved["completion"]["holds"])
        self.assertEqual([f["kind"] for f in moved["provenance"]["drift"]],
                         [rc.FINDING_BASELINE_DRIFT])
        self.assertIn("1" * 64, moved["provenance"]["drift"][0]["detail"])
        self.assertFalse(moved["completion"]["verified_success"])
        self.assertEqual(self.authority_bytes(), authority)
        self.assertEqual(self.document()["mission_state"][self.mission_id]["evidence"],
                         evidence_before)
        back = self.observe({"candidate": Counting(self.answer(
            self.candidate(baseline="1" * 64, test_log=HEX_A)))})
        self.assertNotIn(ob.HOLD_BASELINE_DRIFTED, back["completion"]["holds"])


# ====================================================================
# O7. Reload preserves
# ====================================================================


class O7ReloadTests(ObservationFixture):

    def test_O7_restart_snapshot_present_and_absent_observe_identically(self):
        self.make_local_complete(self.mission_id)
        self.call("open_blocker", self.mission_id, "flaky_network", "degraded")
        adapters = {"task": Counting(self.answer(rc.TASK_REPORT_ACTIVE)),
                    "review": Counting(self.answer(rc.REVIEW_REPORT_PENDING))}
        self.service.reconcile(self.mission_id, self.oid(), self.seq(self.mission_id),
                               self.inputs(adapters), self.context)
        self.clock.advance(7)
        inputs = self.inputs(adapters)
        before = self.observe_raw(inputs)
        self.assertIsNotNone(before["reconciliation"])
        restarted = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        after = restarted.observe(self.mission_id, inputs)
        self.assertEqual(after, before)
        document = self.document()
        document["mission_state"][self.mission_id]["snapshot"] = None
        self.write_raw(json.dumps(document))
        replayed = restarted.observe(self.mission_id, inputs)
        self.assertEqual(replayed, before)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_REPLAY)
        self.assertEqual(before["reconciliation"]["observed_position"], 8)
        self.assertEqual(before["reconciliation"]["sequence"], 9)
        self.assertTrue(before["reconciliation"]["current"])
        self.assertEqual(before["evidence"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(before["blockers"]["value"]["active"][0]["key"], "flaky_network")
        self.assertEqual(before["provenance"]["sources"]["task"]["standing"],
                         ob.STANDING_REPORTED)


# ====================================================================
# O8. No state record yet
# ====================================================================


class O8OriginTests(ObservationFixture):

    def test_O8_an_approved_mission_without_events_observes_at_the_origin(self):
        other, _ = self.approved_mission()
        before = self.read_bytes()
        report = self.observe(self.busy_adapters(), mission_id=other)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(report["cursor"]["position"], 0)
        self.assertIsNone(report["cursor"]["event_id"])
        self.assertEqual(report["sequence"], 0)
        self.assertEqual(report["progress"]["value"]["progress"], ms.PROGRESS_NOT_STARTED)
        self.assertEqual(report["contract"]["value"]["active"], False)
        self.assertIsNone(report["proof"]["value"])
        self.assertIsNone(report["budget"]["value"])
        self.assertIsNone(report["time"]["record_updated_at"])
        self.assertIsNone(report["reconciliation"])
        self.assertIn(ob.HOLD_NO_CANONICAL_CLOSURE, report["completion"]["holds"])
        self.assertNotIn(other, self.document()["mission_state"])
        self.assertEqual(report["task"]["freshness"], ob.FRESHNESS_FRESH)
        self.assertEqual(report["delivery"]["standing"], ob.STANDING_UNAVAILABLE)


# ====================================================================
# O9. Task 7, Stage 2: attested receipts in the observation
# ====================================================================


class O9AttestedReceiptTests(ObservationFixture):
    """Condition 5 in the read path: the attested form is read from the
    marker the persistence layer re-proves, never inferred from a locator
    kind, a digest or a report; structural validity (attested) and
    success (the one pinned state) are reported apart; the completion
    terms never move for an attestation; the read stays non-invoking
    and one-load over the new facts."""

    def test_O9_attested_and_unattested_are_read_apart_and_success_is_the_one_state(self):
        generic = self.call("record_artifact", self.mission_id, "pr_receipt",
                            mission_record.ARTIFACT_ROLE_PRODUCED,
                            ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                            "rcpt-" + "a" * 24, "d" * 64, True, [])["artifact_id"]
        attested = {}
        for state in ("derived", "executing", "failed_retryable", "void",
                      ms.RECEIPT_STATE_SUCCEEDED):
            outcome = self.call("attest_delivery_receipt", self.mission_id,
                                self.attestation(self.mission_id,
                                                 receipt_id="rcpt-" + state[:3] * 8,
                                                 receipt_state=state))
            attested[outcome["artifact_id"]] = state
        report = self.observe()
        value = report["delivery_receipts"]["value"]
        self.assert_fact(report["delivery_receipts"], ob.STANDING_VERIFIED)
        self.assertEqual(set(value), {"recorded", "attested", "unattested",
                                      "effects_completed", "report_bound_to",
                                      "report_bound_attested", "report_fresh"})
        self.assertEqual(value["recorded"], [generic] + list(attested))
        self.assertEqual(value["unattested"], [generic])
        self.assertEqual([a["artifact_id"] for a in value["attested"]], list(attested))
        for item in value["attested"]:
            self.assertEqual(set(item), {"artifact_id", "delivery_id", "step",
                                         "receipt_state", "step_state",
                                         "authorization_id", "effect_completed"})
            self.assertEqual(item["receipt_state"], attested[item["artifact_id"]])
            self.assertEqual(item["effect_completed"],
                             attested[item["artifact_id"]] == ms.RECEIPT_STATE_SUCCEEDED)
        self.assertEqual(value["effects_completed"],
                         [a for a, s in attested.items() if s == ms.RECEIPT_STATE_SUCCEEDED])
        self.assertEqual(len(value["effects_completed"]), 1)
        # Round 08 finding 5 at the read path: a succeeded RECEIPT state
        # under a step state that is not succeeded (the delivery contract
        # accepts a succeeded receipt under a pending step) is attested,
        # listed, and NOT a completed effect — the seam's own condition.
        pending = self.call("attest_delivery_receipt", self.mission_id,
                            self.attestation(self.mission_id,
                                             receipt_id="rcpt-" + "pnd" * 8,
                                             receipt_state=ms.RECEIPT_STATE_SUCCEEDED,
                                             step_state="pending"))
        value = self.observe()["delivery_receipts"]["value"]
        listed = [a for a in value["attested"] if a["artifact_id"] == pending["artifact_id"]]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["receipt_state"], ms.RECEIPT_STATE_SUCCEEDED)
        self.assertEqual(listed[0]["step_state"], "pending")
        self.assertIs(listed[0]["effect_completed"], False)
        self.assertNotIn(pending["artifact_id"], value["effects_completed"])
        self.assertEqual(len(value["effects_completed"]), 1)
        items = {a["artifact_id"]: a for a in report["artifacts"]["value"]}
        self.assertFalse(items[generic]["attested"])
        self.assertIsNone(items[generic]["receipt_attestation"])
        for artifact_id in attested:
            self.assertTrue(items[artifact_id]["attested"])
            self.assertEqual(items[artifact_id]["receipt_attestation"]["receipt_state"],
                             attested[artifact_id])
        # An attestation moves no completion term and removes no hold.
        completion = report["completion"]
        self.assertFalse(completion["closure_verified"])
        self.assertFalse(completion["verified_success"])
        self.assertFalse(completion["reported_complete"])
        self.assertIn(ob.HOLD_NO_CANONICAL_CLOSURE, completion["holds"])
        self.assertEqual(report["progress"]["value"]["progress"], ms.PROGRESS_IN_PROGRESS)
        # The generic reference carries the SAME reference and digest as
        # the succeeded attestation's twin would: presence, locator kind
        # and digest never imply attestation.
        twin = self.call("record_artifact", self.mission_id, None,
                         mission_record.ARTIFACT_ROLE_VERIFICATION,
                         ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                         "rcpt-" + "suc" * 8, "d" * 64, True, [])["artifact_id"]
        value = self.observe()["delivery_receipts"]["value"]
        self.assertIn(twin, value["unattested"])
        self.assertNotIn(twin, [a["artifact_id"] for a in value["attested"]])
        self.assertNotIn(twin, value["effects_completed"])
        # One load, no lock, no write, nothing minted on the read.
        before = self.read_bytes()
        loads = {"n": 0}
        original = self.store.load

        def counting_load():
            loads["n"] += 1
            return original()

        inputs = self.inputs({"delivery": Counting(self.answer({
            "status": rc.DELIVERY_REPORT_VALID, "receipt_artifact_id": twin,
            "locator": "rcpt-" + "suc" * 8, "receipt_digest_sha256": "d" * 64}))})
        self.store.load = counting_load
        try:
            with_report = self.observe_raw(inputs)
        finally:
            self.store.load = original
        self.assertEqual(loads["n"], 1)
        self.assertEqual(self.read_bytes(), before)
        value = with_report["delivery_receipts"]["value"]
        self.assertEqual(value["report_bound_to"], twin)
        self.assertIs(value["report_bound_attested"], False)
        self.assertEqual(with_report["delivery"]["standing"], ob.STANDING_REPORTED)


# ====================================================================
# O10. Round 11: an omission is not a confirmation
# ====================================================================


class O10OmittedArtifactTests(ObservationFixture):
    """The Reviewer's probe, decisively: a candidate report that carries
    no digest for a recorded artifact (empty, or partial) must not leave
    the accepted evidence that references it undrifted-and-confirmed;
    ``verified_success`` is withheld, without asserting a contradiction.
    Only an applicable report creates the unconfirmed state; a stale
    report and an absent source are what they were."""

    def test_O10_empty_and_partial_reports_withhold_verified_success(self):
        self.make_local_complete(self.mission_id)
        self.call("complete_successfully", self.mission_id, "proof accepted")
        base = {"task": Counting(self.answer("COMPLETE")),
                "review": Counting(self.answer("APPROVE"))}

        def observe(candidate):
            adapters = dict(base)
            if candidate is not None:
                adapters["candidate"] = candidate
            return self.observe(adapters)

        def accepted(report):
            items = [e for e in report["evidence"]["value"] if e["accepted"]]
            self.assertEqual(len(items), 1)
            return items[0]

        # Positive control: the report that names the artifact with its
        # recorded digest confirms it — verified success stands.
        confirming = observe(Counting(self.answer(self.candidate(test_log=HEX_A))))
        self.assertFalse(accepted(confirming)["drifted"])
        self.assertFalse(accepted(confirming)["unconfirmed"])
        self.assertTrue(confirming["completion"]["verified_success"])
        self.assertEqual(confirming["provenance"]["drift"], [])
        # The Reviewer's probe, both shapes: an EMPTY artifact_digests, and
        # a report that omits the recorded artifact while supplying another
        # key. Each is accepted at the boundary (option b), the evidence is
        # UNCONFIRMED (not drifted), the hold blocks, verified success is
        # withheld, and nothing is called a contradiction.
        for label, candidate in (
            ("empty artifact_digests", self.candidate()),
            ("omits the recorded artifact", self.candidate(other_key="1" * 64)),
        ):
            with self.subTest(label):
                self.assertIs(rc.validate_candidate_value(candidate, "c"), candidate)
                report = observe(Counting(self.answer(candidate)))
                item = accepted(report)
                self.assertFalse(item["drifted"])
                self.assertTrue(item["unconfirmed"])
                self.assertTrue(report["completion"]["closure_verified"])
                self.assertFalse(report["completion"]["verified_success"])
                self.assertIn(ob.HOLD_CANDIDATE_UNCONFIRMED, report["completion"]["holds"])
                self.assertNotIn(ob.HOLD_CANDIDATE_DRIFTED, report["completion"]["holds"])
                self.assertFalse(report["completion"]["contradicted"])
                self.assertEqual(
                    [(f["kind"], f["subject"]) for f in report["provenance"]["drift"]],
                    [(rc.FINDING_CANDIDATE_UNOBSERVED, "test_log")])
        # The deletion the finding describes: the artifact is gone from the
        # candidate, so the observer reports every other key and not this
        # one — indistinguishable from the partial shape above, and now
        # equally withheld.
        # A contradicting report is DRIFT, a different thing: drifted, and
        # contradicted.
        contradicting = observe(Counting(self.answer(self.candidate(test_log="f" * 64))))
        self.assertTrue(accepted(contradicting)["drifted"])
        self.assertFalse(accepted(contradicting)["unconfirmed"])
        self.assertFalse(contradicting["completion"]["verified_success"])
        self.assertTrue(contradicting["completion"]["contradicted"])
        # A STALE empty report is not a statement about the candidate now:
        # it neither confirms nor creates, exactly as no report; and a
        # source that is not reported at all is what it was (unavailable /
        # unknown, nothing created). Both stated, neither changed.
        stale = observe(Counting(self.answer(self.candidate(),
                                             age=ob.REPORTED_FRESHNESS_BOUND_SECONDS + 1)))
        self.assertEqual(stale["candidate"]["freshness"], ob.FRESHNESS_STALE)
        self.assertFalse(accepted(stale)["unconfirmed"])
        self.assertNotIn(ob.HOLD_CANDIDATE_UNCONFIRMED, stale["completion"]["holds"])
        absent = observe(None)
        self.assertEqual(absent["candidate"]["standing"], ob.STANDING_UNAVAILABLE)
        self.assertFalse(accepted(absent)["unconfirmed"])
        self.assertTrue(absent["completion"]["verified_success"])
        # Nothing was written or changed in authority by any observation.
        self.assertEqual(self.service.get_state(self.mission_id)["record"]["evidence"][0][
            "acceptance"]["content_digest_sha256"], "e" * 64)

    def test_O10_an_omitted_baseline_is_unconfirmed_once_an_anchor_exists(self):
        self.make_local_complete(self.mission_id)
        # No anchor yet: a report naming no baseline confirms and
        # contradicts nothing about the baseline.
        report = self.observe({"candidate": Counting(self.answer(
            self.candidate(baseline=None, test_log=HEX_A)))})
        self.assertNotIn(ob.HOLD_BASELINE_UNCONFIRMED, report["completion"]["holds"])
        # A reconciliation anchors the baseline; the Mission completes.
        self.service.reconcile(self.mission_id, self.oid(), self.seq(self.mission_id),
                               self.inputs({"candidate": Counting(self.answer(
                                   self.candidate(baseline="1" * 64, test_log=HEX_A)))}),
                               self.context)
        self.call("complete_successfully", self.mission_id, "proof accepted")
        base = {"task": Counting(self.answer("COMPLETE")),
                "review": Counting(self.answer("APPROVE"))}
        omitted = self.observe(dict(base, candidate=Counting(self.answer(
            self.candidate(baseline=None, test_log=HEX_A)))))
        self.assertIn(ob.HOLD_BASELINE_UNCONFIRMED, omitted["completion"]["holds"])
        self.assertNotIn(ob.HOLD_BASELINE_DRIFTED, omitted["completion"]["holds"])
        self.assertFalse(omitted["completion"]["verified_success"])
        self.assertFalse(omitted["completion"]["contradicted"])
        self.assertEqual([f["kind"] for f in omitted["provenance"]["drift"]],
                         [rc.FINDING_BASELINE_UNOBSERVED])
        named = self.observe(dict(base, candidate=Counting(self.answer(
            self.candidate(baseline="1" * 64, test_log=HEX_A)))))
        self.assertTrue(named["completion"]["verified_success"])
        self.assertEqual(named["provenance"]["drift"], [])
        moved = self.observe(dict(base, candidate=Counting(self.answer(
            self.candidate(baseline="2" * 64, test_log=HEX_A)))))
        self.assertIn(ob.HOLD_BASELINE_DRIFTED, moved["completion"]["holds"])
        self.assertTrue(moved["completion"]["contradicted"])


# ====================================================================
# O11. Round 12: a re-recorded key — evidence follows its own artifact
# ====================================================================


class O11DuplicateKeyTests(ObservationFixture):
    """A key may hold several recorded artifacts; proof binds the one the
    evidence references, which need not be the latest. The Reviewer's
    case: an applicable report omitting the latest artifact under the key
    left the older-referencing evidence confirmed and verified success
    standing. Decisive values asserted: the evidence flags, the exact
    holds list and ``verified_success``."""

    def accepted(self, report, evidence_id=None):
        items = [e for e in report["evidence"]["value"] if e["accepted"]]
        if evidence_id is not None:
            items = [e for e in items if e["evidence_id"] == evidence_id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_O11_reviewers_case_omitting_the_latest_artifact_under_a_re_recorded_key(self):
        # Evidence accepted on artifact A (test_log, the approved digest);
        # then test_log re-recorded as a NEWER artifact B with the same
        # approved digest (R-20.2 requires the latest required artifact to
        # carry it); the Mission completes on A.
        evidence = self.call("submit_evidence", self.mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                             [self.artifact["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        newer = self.call("record_artifact", self.mission_id, "test_log",
                          mission_record.ARTIFACT_ROLE_VERIFICATION,
                          ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log2", HEX_A, True, [])
        self.assertNotEqual(newer["artifact_id"], self.artifact["artifact_id"])
        self.call("complete_successfully", self.mission_id, "proof accepted")
        record = self.service.get_state(self.mission_id)["record"]
        self.assertEqual([a["artifact_id"] for a in record["artifacts"] if a["key"] == "test_log"],
                         [self.artifact["artifact_id"], newer["artifact_id"]])
        self.assertEqual(record["evidence"][0]["artifact_ids"], [self.artifact["artifact_id"]])
        base = {"task": Counting(self.answer("COMPLETE")),
                "review": Counting(self.answer("APPROVE"))}

        def observe(candidate, age=0):
            return self.observe(dict(base, candidate=Counting(self.answer(candidate, age))))

        # The Reviewer's case: an applicable report that omits the latest
        # artifact. Before the fix: holds [] and verified_success True.
        omitted = observe(self.candidate())
        item = self.accepted(omitted)
        self.assertFalse(item["drifted"])
        self.assertTrue(item["unconfirmed"])
        self.assertEqual(omitted["completion"]["holds"], [ob.HOLD_CANDIDATE_UNCONFIRMED])
        self.assertFalse(omitted["completion"]["verified_success"])
        self.assertFalse(omitted["completion"]["contradicted"])
        self.assertTrue(omitted["completion"]["closure_verified"])
        # Naming the key with the approved digest confirms BOTH recorded
        # artifacts (same digest): no hold, success stands.
        named = observe(self.candidate(test_log=HEX_A))
        self.assertFalse(self.accepted(named)["drifted"])
        self.assertFalse(self.accepted(named)["unconfirmed"])
        self.assertEqual(named["completion"]["holds"], [])
        self.assertTrue(named["completion"]["verified_success"])
        # Naming the key with another digest contradicts the artifact the
        # evidence rests on: drifted, contradicted, success withheld.
        other = observe(self.candidate(test_log="c" * 64))
        self.assertTrue(self.accepted(other)["drifted"])
        self.assertEqual(other["completion"]["holds"], [ob.HOLD_CANDIDATE_DRIFTED])
        self.assertFalse(other["completion"]["verified_success"])
        self.assertTrue(other["completion"]["contradicted"])
        # A STALE empty report creates nothing (applicable-only rule,
        # accepted last round): stated, unchanged.
        stale = observe(self.candidate(), age=ob.REPORTED_FRESHNESS_BOUND_SECONDS + 1)
        self.assertFalse(self.accepted(stale)["unconfirmed"])
        self.assertEqual(stale["completion"]["holds"], [])
        self.assertTrue(stale["completion"]["verified_success"])
        # Nothing moved in the record or its authority.
        self.assertEqual(self.service.get_state(self.mission_id)["record"], record)

    def test_O11_evidence_on_an_older_artifact_follows_its_own_digest_not_the_latest(self):
        # The twin door on a key the contract does not require: evidence
        # accepted on extra_log X; extra_log re-recorded as Y with a
        # DIFFERENT digest. A report that confirms the latest Y confirms
        # nothing about X, which the proof rests on.
        older = self.call("record_artifact", self.mission_id, "extra_log",
                          mission_record.ARTIFACT_ROLE_PRODUCED,
                          ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", "1" * 64, True, [])
        evidence = self.call("submit_evidence", self.mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                             [self.artifact["artifact_id"], older["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        newer = self.call("record_artifact", self.mission_id, "extra_log",
                          mission_record.ARTIFACT_ROLE_PRODUCED,
                          ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:y", "2" * 64, True, [])
        self.call("complete_successfully", self.mission_id, "proof accepted")
        base = {"task": Counting(self.answer("COMPLETE")),
                "review": Counting(self.answer("APPROVE"))}

        def observe(candidate):
            return self.observe(dict(base, candidate=Counting(self.answer(candidate))))

        # Confirms the LATEST Y: the evidence's own artifact X is
        # contradicted — drifted, hold, success withheld; the record-level
        # finding names the key.
        latest_only = observe(self.candidate(test_log=HEX_A, extra_log="2" * 64))
        item = self.accepted(latest_only)
        self.assertTrue(item["drifted"])
        self.assertFalse(item["unconfirmed"])
        self.assertEqual(latest_only["completion"]["holds"], [ob.HOLD_CANDIDATE_DRIFTED])
        self.assertFalse(latest_only["completion"]["verified_success"])
        self.assertEqual([(f["kind"], f["subject"]) for f in latest_only["provenance"]["drift"]],
                         [(rc.FINDING_CANDIDATE_DRIFT, "extra_log")])
        # Confirms the evidence's own X: the evidence is confirmed and
        # success stands; the RECORD still reports drift for the key (the
        # latest Y is not in the candidate), truthfully, as a finding that
        # affects no derived claim.
        own = observe(self.candidate(test_log=HEX_A, extra_log="1" * 64))
        self.assertFalse(self.accepted(own)["drifted"])
        self.assertFalse(self.accepted(own)["unconfirmed"])
        self.assertEqual(own["completion"]["holds"], [])
        self.assertTrue(own["completion"]["verified_success"])
        self.assertEqual([(f["kind"], f["subject"]) for f in own["provenance"]["drift"]],
                         [(rc.FINDING_CANDIDATE_DRIFT, "extra_log")])
        # Omits extra_log: unconfirmed, whichever artifact the evidence
        # rests on.
        omitted = observe(self.candidate(test_log=HEX_A))
        self.assertTrue(self.accepted(omitted)["unconfirmed"])
        self.assertEqual(omitted["completion"]["holds"], [ob.HOLD_CANDIDATE_UNCONFIRMED])
        self.assertFalse(omitted["completion"]["verified_success"])
        self.assertIsNotNone(newer)


# ====================================================================
# O12. Round 13: drift has a relevance boundary — both directions
# ====================================================================


class O12RelevanceBoundaryTests(ObservationFixture):
    """Only evidence that bears on the claim (accepted, current
    activation — the proof evaluator's line) may withhold verified
    success. Both directions in ONE test, so neither can quietly reopen:
    irrelevant evidence — never accepted, invalidated, superseded
    activation — no longer blocks; an accepted current record whose
    referenced artifact is unobserved or contradicted still does."""

    def items(self, report):
        return dict((e["evidence_id"], e) for e in report["evidence"]["value"])

    def test_O12_irrelevant_evidence_never_blocks_and_relevant_evidence_still_does(self):
        # Activation 1: E_old accepted on extra_log X — soon superseded.
        x = self.call("record_artifact", self.mission_id, "extra_log",
                      mission_record.ARTIFACT_ROLE_PRODUCED,
                      ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", "1" * 64, True, [])
        e_old = self.call("submit_evidence", self.mission_id, "tests_pass",
                          mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "d" * 64,
                          [x["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, e_old["evidence_id"], "d" * 64,
                  context=self.other)
        # EDIT, approve and activate revision 2: E_old now belongs to a
        # superseded activation.
        self.edit(self.mission_id, 1, proof_contract=contract(required_dependencies=[]))
        self.approve(self.mission_id, 2)
        self.clock.advance(1)
        self.call("activate_proof_contract", self.mission_id)
        # Activation 2: E_new accepted on test_log A (proof); a NEVER-
        # ACCEPTED submission E_sub on X; an INVALIDATED record E_inv on X.
        e_new = self.call("submit_evidence", self.mission_id, "tests_pass",
                          mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                          [self.artifact["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, e_new["evidence_id"], "e" * 64,
                  context=self.other)
        e_sub = self.call("submit_evidence", self.mission_id, "tests_pass",
                          mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "f" * 64,
                          [x["artifact_id"]])
        e_inv = self.call("submit_evidence", self.mission_id, "tests_pass",
                          mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                          [x["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, e_inv["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("invalidate_evidence", self.mission_id, e_inv["evidence_id"], "superseded")
        self.call("observe_resource_readiness", self.mission_id, "build_host",
                  ms.READINESS_READY, self.clock())
        self.call("complete_successfully", self.mission_id, "proof accepted")
        record = self.service.get_state(self.mission_id)["record"]
        activation = ms.latest_activation(record)["activation_id"]
        by_id = dict((e["evidence_id"], e) for e in record["evidence"])
        self.assertNotEqual(by_id[e_old["evidence_id"]]["activation_id"], activation)
        self.assertEqual(by_id[e_new["evidence_id"]]["activation_id"], activation)
        base = {"task": Counting(self.answer("COMPLETE")),
                "review": Counting(self.answer("APPROVE"))}

        def observe(candidate):
            return self.observe(dict(base, candidate=Counting(self.answer(candidate))))

        # DIRECTION 1 — irrelevant evidence never blocks. The report
        # confirms test_log (the claim's artifact) and CONTRADICTS extra_log,
        # which only the never-accepted, the invalidated and the
        # superseded records reference. Each of those is truthfully
        # drifted and truthfully irrelevant; proof is satisfied; the holds
        # are empty; verified success stands. (Before the fix: holds
        # ['candidate_drifted'], verified_success False.)
        irrelevant = observe(self.candidate(test_log=HEX_A, extra_log="9" * 64))
        items = self.items(irrelevant)
        self.assertTrue(irrelevant["proof"]["value"]["satisfied"])
        for label, evidence_id in (("never accepted", e_sub["evidence_id"]),
                                   ("invalidated", e_inv["evidence_id"]),
                                   ("superseded activation", e_old["evidence_id"])):
            with self.subTest(label):
                self.assertTrue(items[evidence_id]["drifted"], label)
                self.assertFalse(items[evidence_id]["bears_on_claim"], label)
        self.assertTrue(items[e_new["evidence_id"]]["bears_on_claim"])
        self.assertFalse(items[e_new["evidence_id"]]["drifted"])
        self.assertEqual(irrelevant["completion"]["holds"], [])
        self.assertTrue(irrelevant["completion"]["verified_success"])
        self.assertFalse(irrelevant["completion"]["contradicted"])
        # The record-level finding for extra_log is still truthfully
        # reported; it affects no claim.
        self.assertEqual([(f["kind"], f["subject"]) for f in irrelevant["provenance"]["drift"]],
                         [(rc.FINDING_CANDIDATE_DRIFT, "extra_log")])
        # Omitting extra_log: the irrelevant records are unconfirmed and
        # still block nothing.
        omitted_irrelevant = observe(self.candidate(test_log=HEX_A))
        items = self.items(omitted_irrelevant)
        self.assertTrue(items[e_sub["evidence_id"]]["unconfirmed"])
        self.assertTrue(items[e_inv["evidence_id"]]["unconfirmed"])
        self.assertTrue(items[e_old["evidence_id"]]["unconfirmed"])
        self.assertFalse(items[e_new["evidence_id"]]["unconfirmed"])
        self.assertEqual(omitted_irrelevant["completion"]["holds"], [])
        self.assertTrue(omitted_irrelevant["completion"]["verified_success"])
        # DIRECTION 2 — relevant evidence still blocks (rounds 11–13). The
        # SAME record, a report contradicting test_log: E_new is drifted
        # AND bears on the claim; the hold is exactly candidate_drifted;
        # success is withheld and contradicted.
        contradicted = observe(self.candidate(test_log="c" * 64, extra_log="1" * 64))
        items = self.items(contradicted)
        self.assertTrue(contradicted["proof"]["value"]["satisfied"])
        self.assertTrue(items[e_new["evidence_id"]]["drifted"])
        self.assertTrue(items[e_new["evidence_id"]]["bears_on_claim"])
        self.assertEqual(contradicted["completion"]["holds"], [ob.HOLD_CANDIDATE_DRIFTED])
        self.assertFalse(contradicted["completion"]["verified_success"])
        self.assertTrue(contradicted["completion"]["contradicted"])
        # And a report that omits test_log: E_new unconfirmed, relevant;
        # the hold is exactly candidate_unconfirmed; success withheld.
        omitted_relevant = observe(self.candidate(extra_log="1" * 64))
        items = self.items(omitted_relevant)
        self.assertTrue(items[e_new["evidence_id"]]["unconfirmed"])
        self.assertEqual(omitted_relevant["completion"]["holds"],
                         [ob.HOLD_CANDIDATE_UNCONFIRMED])
        self.assertFalse(omitted_relevant["completion"]["verified_success"])
        self.assertFalse(omitted_relevant["completion"]["contradicted"])
        # Nothing moved in the record or its authority.
        self.assertEqual(self.service.get_state(self.mission_id)["record"], record)


if __name__ == "__main__":
    unittest.main()
