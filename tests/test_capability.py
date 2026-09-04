"""Focused tests for the one-shot capability seam.

Three surfaces: the substrate-neutral contract (``capability``), the
Runtime-backed adapter (``target_runtime.capability_authority``), and
the production wiring through it (``TargetBroker.perform`` consumes,
the Runtime mints and compacts). Every test is hermetic: temporary
directories only, no network, no repository mutation. The existing
adversarial coverage in ``tests/test_target_runtime.py`` drives the
same store through ``broker.perform`` and ``process_once`` and stays
authoritative; this file proves the seam itself.
"""

import ast
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import capability
from capability import CapabilityAuthority, CapabilityError
from capability import contract as contract_module
from target_runtime import broker as broker_module
from target_runtime import capability as capability_module
from target_runtime import capability_authority as adapter_module
from target_runtime import runtime as runtime_module
from workflow_authority import record as record_module

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "capability"
ADAPTER_PATH = REPO_ROOT / "target_runtime" / "capability_authority.py"
BROKER_PATH = REPO_ROOT / "target_runtime" / "broker.py"
RUNTIME_PATH = REPO_ROOT / "target_runtime" / "runtime.py"

NOW = 1700000000
SEAM_METHODS = ("mint", "validate_and_consume", "compact")
# Positional parameters of each seam method, after ``self``: the
# production call graph with the bound directory removed.
SEAM_SIGNATURES = {
    "mint": ["workflow_id", "action", "revision", "now"],
    "validate_and_consume": [
        "token", "workflow_id", "action", "revision", "now",
    ],
    "compact": ["now", "non_actionable", "oracle_errors"],
}
REFUSAL_CODES = {
    "PROBLEM_CAPABILITY_MISSING": "capability_missing",
    "PROBLEM_CAPABILITY_UNKNOWN": "capability_unknown",
    "PROBLEM_CAPABILITY_CONSUMED": "capability_already_consumed",
    "PROBLEM_CAPABILITY_EXPIRED": "capability_expired",
    "PROBLEM_CAPABILITY_MISMATCH": "capability_binding_mismatch",
    "PROBLEM_CAPABILITY_STORE": "capability_store_unreadable",
    "PROBLEM_CAPABILITY_STORE_FULL": "capability_store_full",
}
# Roots the neutral package must never load, directly or transitively.
NON_NEUTRAL_ROOTS = (
    "target_runtime", "herdr", "herdctl", "telegram_operator",
    "codex_gateway", "workflow_authority", "operator_session",
    "human_interaction", "durable_execution", "git_transport",
)


def _forbidden_capability_call(name):
    def _fail(*args, **kwargs):
        raise AssertionError(
            "target_runtime.capability.%s was called while a fake"
            " CapabilityAuthority was injected; the fake path must"
            " reach no capability function" % name
        )
    return _fail


@contextlib.contextmanager
def _capability_functions_forbidden():
    with patch.object(capability_module, "mint",
                      _forbidden_capability_call("mint")), \
         patch.object(capability_module, "validate_and_consume",
                      _forbidden_capability_call("validate_and_consume")), \
         patch.object(capability_module, "compact",
                      _forbidden_capability_call("compact")):
        yield


class FakeAuthority(CapabilityAuthority):
    """A seam implementation that records calls and returns fixed
    answers; it never touches the store."""

    def __init__(self, calls, token="FAKE-TOKEN",
                 consume=(True, None, None), compacted=None,
                 mint_error=None, compact_error=None):
        self.calls = calls
        self.token = token
        self.consume = consume
        self.compacted = compacted if compacted is not None else []
        self.mint_error = mint_error
        self.compact_error = compact_error

    def mint(self, workflow_id, action, revision, now):
        self.calls.append(("mint", workflow_id, action, revision, now))
        if self.mint_error is not None:
            raise self.mint_error
        return self.token

    def validate_and_consume(self, token, workflow_id, action, revision,
                             now):
        self.calls.append((
            "validate_and_consume", token, workflow_id, action, revision,
            now,
        ))
        return self.consume

    def compact(self, now, non_actionable, oracle_errors):
        self.calls.append(("compact", now, non_actionable, oracle_errors))
        if self.compact_error is not None:
            raise self.compact_error
        return self.compacted


def _counting_nonce_factory(prefix):
    counter = {"n": 0}

    def factory():
        counter["n"] += 1
        return "%s%04d" % (prefix, counter["n"])
    return factory


class ContractTests(unittest.TestCase):
    """The neutral package: importable alone, exactly three abstract
    methods, standard library only, exact refusal vocabulary."""

    def test_contract_imports_in_isolation(self):
        # A fresh interpreter that loads ONLY the neutral package must
        # end up with none of the substrate, control-chain, sibling-
        # seam, or orchestration modules in sys.modules.
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import capability\n"
                    "import capability.contract\n"
                    "roots = %r\n"
                    "bad = sorted(\n"
                    "    name for name in sys.modules\n"
                    "    if name.split('.')[0] in roots\n"
                    ")\n"
                    "print('\\n'.join(bad))\n"
                    "assert capability.CapabilityAuthority is"
                    " capability.contract.CapabilityAuthority\n"
                    "assert capability.CapabilityError is"
                    " capability.contract.CapabilityError\n"
                    "sys.exit(1 if bad else 0)\n"
                ) % (NON_NEUTRAL_ROOTS,),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            probe.returncode, 0,
            (probe.returncode, probe.stdout, probe.stderr),
        )

    def test_exactly_three_abstract_methods_with_exact_signatures(self):
        self.assertEqual(
            CapabilityAuthority.__abstractmethods__, frozenset(SEAM_METHODS)
        )
        for name in SEAM_METHODS:
            method = getattr(CapabilityAuthority, name)
            code = method.__code__
            self.assertEqual(
                list(code.co_varnames[:code.co_argcount]),
                ["self"] + SEAM_SIGNATURES[name],
                name,
            )
            self.assertIsNone(method.__defaults__, name)
            self.assertIsNone(method.__kwdefaults__, name)
        # No public surface beyond the three methods.
        public = sorted(
            name for name in vars(CapabilityAuthority)
            if not name.startswith("_")
        )
        self.assertEqual(public, sorted(SEAM_METHODS))

    def test_abc_and_partial_subclasses_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            CapabilityAuthority()
        for missing in SEAM_METHODS:
            namespace = {
                name: (lambda self, *args: None)
                for name in SEAM_METHODS if name != missing
            }
            partial = type("Partial", (CapabilityAuthority,), namespace)
            with self.assertRaises(TypeError, msg=missing):
                partial()
        complete = type(
            "Complete", (CapabilityAuthority,),
            {name: (lambda self, *args: None) for name in SEAM_METHODS},
        )
        self.assertIsInstance(complete(), CapabilityAuthority)

    def test_package_imports_stdlib_only(self):
        # Only ``abc`` and the package's own module; no dynamic-import
        # machinery; no relative import. Every product module the
        # package must stay free of is named here explicitly.
        files = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertEqual(
            [p.name for p in files], ["__init__.py", "contract.py"]
        )
        allowed_roots = {"abc", "capability"}
        for path in files:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertIn(root, allowed_roots, (path, alias.name))
                        self.assertNotIn(root, NON_NEUTRAL_ROOTS)
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    self.assertIn(root, allowed_roots, (path, node.module))
                    self.assertNotIn(root, NON_NEUTRAL_ROOTS)
                    self.assertEqual(node.level, 0, path)
                elif isinstance(node, ast.Call):
                    name = getattr(
                        node.func, "id", getattr(node.func, "attr", None)
                    )
                    self.assertNotIn(
                        name, {"__import__", "import_module"}, path
                    )
                elif isinstance(node, ast.Name):
                    self.assertNotEqual(node.id, "__import__", path)
        contract_tree = ast.parse((PACKAGE_DIR / "contract.py").read_text())
        contract_imports = [
            node for node in ast.walk(contract_tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertEqual(len(contract_imports), 1)
        self.assertEqual(contract_imports[0].names[0].name, "abc")

    def test_package_defines_no_bound_style_constant_and_no_extras(self):
        # Nothing that would enter the derived bound-constant pin; no
        # loops, locks, or try blocks; exactly the error type and the
        # contract class, and nothing in the package's own module.
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(
                    node.ctx, ast.Store
                ):
                    self._assert_not_bound_style(node.id, path)
                self.assertNotIsInstance(
                    node, (ast.Try, ast.For, ast.While, ast.With), path
                )
            classes = [
                n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
            ]
            if path.name == "contract.py":
                self.assertEqual(
                    [c.name for c in classes],
                    ["CapabilityError", "CapabilityAuthority"],
                )
            else:
                self.assertEqual(classes, [])
        # The package must not name the store file, the substrate
        # module, or the substrate's alias for it anywhere.
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            source = path.read_text()
            for forbidden in (
                "capabilities.json", "target_runtime.capability",
                "capability_module",
            ):
                self.assertNotIn(forbidden, source, (path, forbidden))

    def _assert_not_bound_style(self, name, path):
        self.assertFalse(name.startswith("MAX_"), (path, name))
        self.assertFalse(name.startswith("CANONICAL_"), (path, name))
        for suffix in ("_SECONDS", "_CHARS", "_CHUNKS"):
            self.assertFalse(name.endswith(suffix), (path, name))

    def test_package_exports_the_contract(self):
        self.assertIs(CapabilityAuthority, contract_module.CapabilityAuthority)
        self.assertIs(CapabilityError, contract_module.CapabilityError)
        self.assertEqual(
            capability.__all__,
            ["CapabilityAuthority", "CapabilityError"]
            + list(REFUSAL_CODES),
        )
        for name in REFUSAL_CODES:
            self.assertIs(getattr(capability, name),
                          getattr(contract_module, name))

    def test_refusal_codes_have_their_exact_current_values(self):
        for name, value in REFUSAL_CODES.items():
            self.assertEqual(getattr(contract_module, name), value, name)
        self.assertTrue(issubclass(CapabilityError, Exception))
        self.assertEqual(CapabilityError.__bases__, (Exception,))

    def test_substrate_module_rebinds_to_the_neutral_vocabulary(self):
        # The persistence module's error type is a SUBCLASS of the
        # neutral one, and its seven refusal names are the SAME string
        # objects, so nothing observable changed for its direct
        # callers and the Runtime can catch the neutral type.
        self.assertTrue(
            issubclass(capability_module.CapabilityError, CapabilityError)
        )
        self.assertIsNot(capability_module.CapabilityError, CapabilityError)
        for name in REFUSAL_CODES:
            self.assertIs(
                getattr(capability_module, name),
                getattr(contract_module, name),
                name,
            )
        raised = capability_module.CapabilityError("store unusable")
        self.assertIsInstance(raised, CapabilityError)
        self.assertEqual(str(raised), "store unusable")


class AdapterTests(unittest.TestCase):
    """Exact delegation through the module object, no I/O at
    construction, pure delegation by AST."""

    def setUp(self):
        self.store_directory = object()
        self.adapter = adapter_module.RuntimeCapabilityAuthority(
            self.store_directory
        )

    def test_is_a_capability_authority_and_stores_directory_unchanged(self):
        self.assertIsInstance(self.adapter, CapabilityAuthority)
        self.assertIs(self.adapter.store_directory, self.store_directory)
        self.assertEqual(sorted(vars(self.adapter)), ["store_directory"])

    def test_constructor_reads_nothing_from_its_argument(self):
        class Untouchable(object):
            def __getattribute__(self, name):
                raise AssertionError("constructor read %s" % name)

        adapter_module.RuntimeCapabilityAuthority(Untouchable())

    def test_constructor_does_no_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, "never-created")
            adapter_module.RuntimeCapabilityAuthority(absent)
            self.assertFalse(os.path.exists(absent))
            self.assertEqual(os.listdir(tmp), [])

    def test_module_binds_no_capability_function_at_import_time(self):
        # The only substrate import is the ``capability`` MODULE,
        # resolved through ``target_runtime``; no function name from
        # it is bound in the adapter module.
        tree = ast.parse(ADAPTER_PATH.read_text())
        substrate_froms = [
            (node.module, [alias.name for alias in node.names])
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("target_runtime")
        ]
        self.assertEqual(substrate_froms, [("target_runtime", ["capability"])])
        for name in SEAM_METHODS:
            self.assertFalse(hasattr(adapter_module, name), name)
        self.assertIs(adapter_module.capability_module, capability_module)

    def _assert_call_time_resolution(self, method, arguments):
        # The replacement is installed AFTER the adapter exists, so a
        # function bound at import time or at construction would
        # never see it.
        sentinel = object()
        recorded = []

        def replacement(*args, **kwargs):
            recorded.append((args, kwargs))
            return sentinel

        with patch.object(capability_module, method, replacement):
            result = getattr(self.adapter, method)(*arguments)
        self.assertIs(result, sentinel)
        self.assertEqual(len(recorded), 1)
        args, kwargs = recorded[0]
        self.assertEqual(len(args), 1 + len(arguments))
        self.assertIs(args[0], self.store_directory)
        for given, passed in zip(arguments, args[1:]):
            self.assertIs(passed, given)
        self.assertEqual(kwargs, {})

    def test_mint_delegates_exactly(self):
        self._assert_call_time_resolution(
            "mint", [object(), object(), object(), object()]
        )

    def test_validate_and_consume_delegates_exactly(self):
        self._assert_call_time_resolution(
            "validate_and_consume",
            [object(), object(), object(), object(), object()],
        )

    def test_compact_delegates_exactly(self):
        self._assert_call_time_resolution(
            "compact", [object(), object(), object()]
        )

    def test_exceptions_propagate_as_the_same_instance(self):
        for method in SEAM_METHODS:
            error = RuntimeError("substrate failure for %s" % method)

            def raising(*args, **kwargs):
                raise error

            arguments = [object()] * len(SEAM_SIGNATURES[method])
            with patch.object(capability_module, method, raising):
                with self.assertRaises(RuntimeError) as caught:
                    getattr(self.adapter, method)(*arguments)
            self.assertIs(caught.exception, error, method)

    def test_adapter_body_is_pure_delegation(self):
        # No try, loop, with, default, or extra state; each seam
        # method is a single return of one call on
        # ``capability_module.<same name>`` whose arguments are the
        # bound directory first and then the method's own parameters
        # in order, positionally.
        tree = ast.parse(ADAPTER_PATH.read_text())
        for node in ast.walk(tree):
            self.assertNotIsInstance(
                node, (ast.Try, ast.For, ast.While, ast.With), node
            )
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        self.assertEqual(
            [c.name for c in classes], ["RuntimeCapabilityAuthority"]
        )
        methods = {
            n.name: n for n in classes[0].body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertEqual(
            sorted(methods), sorted(("__init__",) + SEAM_METHODS)
        )
        for name in SEAM_METHODS:
            function = methods[name]
            parameters = [arg.arg for arg in function.args.args]
            self.assertEqual(parameters, ["self"] + SEAM_SIGNATURES[name])
            self.assertEqual(function.args.defaults, [], name)
            body = function.body
            self.assertEqual(len(body), 1, name)
            self.assertIsInstance(body[0], ast.Return, name)
            call = body[0].value
            self.assertIsInstance(call, ast.Call, name)
            self.assertIsInstance(call.func, ast.Attribute, name)
            self.assertEqual(call.func.value.id, "capability_module", name)
            self.assertEqual(call.func.attr, name)
            self.assertEqual(call.keywords, [], name)
            self.assertEqual(len(call.args), 1 + len(SEAM_SIGNATURES[name]))
            first = call.args[0]
            self.assertIsInstance(first, ast.Attribute, name)
            self.assertEqual(first.value.id, "self", name)
            self.assertEqual(first.attr, "store_directory", name)
            self.assertEqual(
                [arg.id for arg in call.args[1:]], SEAM_SIGNATURES[name],
                name,
            )
        init_body = methods["__init__"].body
        self.assertEqual(len(init_body), 1)
        self.assertIsInstance(init_body[0], ast.Assign)
        self.assertIsInstance(init_body[0].value, ast.Name)
        self.assertEqual(init_body[0].value.id, "store_directory")


class _BrokerCase(unittest.TestCase):
    """A hermetic Broker whose non-capability seams are inert objects:
    nothing here reaches a transport, a role turn, or a workspace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_dir = os.path.join(self.tmp.name, "store")
        os.makedirs(self.store_dir, mode=0o700)

    def make_broker(self, store_directory=None, **kwargs):
        def never(*args, **kwargs):
            raise AssertionError("an inert seam was reached")

        return broker_module.TargetBroker(
            store_directory=(
                store_directory if store_directory is not None
                else self.store_dir
            ),
            control_repository_realpath=os.path.join(
                self.tmp.name, "control"
            ),
            transport=object(),
            workspaces_root=os.path.join(self.tmp.name, "workspaces"),
            role_turn_fn=never,
            claude_config_path=os.path.join(self.tmp.name, "claude.json"),
            spawn_fn=never,
            clock=lambda: NOW,
            observer_fn=never,
            spawn_records_fn=never,
            readiness_probe_fn=never,
            **kwargs
        )

    def capability_path(self):
        return os.path.join(
            self.store_dir, capability_module.CAPABILITIES_FILE_NAME
        )


class BrokerSeamTests(_BrokerCase):
    """Production construction and fake injection at the consume
    site."""

    def test_default_is_the_production_adapter_over_the_store_directory(self):
        broker = self.make_broker()
        seam = broker.capability_authority
        self.assertIsInstance(seam, adapter_module.RuntimeCapabilityAuthority)
        self.assertIs(seam.store_directory, broker.store.directory)

    def test_default_construction_does_no_io(self):
        absent = os.path.join(self.tmp.name, "absent-store")
        broker = self.make_broker(store_directory=absent)
        self.assertFalse(os.path.exists(absent))
        self.assertIs(broker.capability_authority.store_directory, absent)
        self.assertFalse(os.path.exists(self.capability_path()))

    def test_injected_seam_is_used_as_given(self):
        fake = FakeAuthority([])
        broker = self.make_broker(capability_authority=fake)
        self.assertIs(broker.capability_authority, fake)

    def test_unknown_action_is_refused_before_the_seam(self):
        calls = []
        broker = self.make_broker(capability_authority=FakeAuthority(calls))
        with _capability_functions_forbidden():
            outcome = broker.perform("wf-0001", "not_an_action", 2,
                                     capability="anything")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_UNKNOWN_ACTION)
        self.assertEqual(calls, [])

    def test_fake_refusal_is_returned_verbatim_before_the_store_is_read(self):
        calls = []
        fake = FakeAuthority(
            calls, consume=(False, "fake_problem", "fake detail text")
        )
        broker = self.make_broker(capability_authority=fake)
        loads = []
        original_load = broker.store.load

        def observed_load():
            loads.append(True)
            return original_load()

        broker.store.load = observed_load
        with _capability_functions_forbidden():
            outcome = broker.perform(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2,
                capability="presented-token",
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem, "fake_problem")
        self.assertEqual(outcome.detail, "fake detail text")
        self.assertEqual(calls, [(
            "validate_and_consume", "presented-token", "wf-0001",
            broker_module.ACTION_MATERIALIZE, 2, NOW,
        )])
        self.assertEqual(loads, [])
        self.assertFalse(os.path.exists(self.capability_path()))

    def test_fake_acceptance_lets_perform_proceed_to_the_store(self):
        calls = []
        fake = FakeAuthority(calls, consume=(True, None, None))
        broker = self.make_broker(capability_authority=fake)
        loads = []
        original_load = broker.store.load

        def observed_load():
            loads.append(True)
            return original_load()

        broker.store.load = observed_load
        with _capability_functions_forbidden():
            outcome = broker.perform(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2,
                capability="presented-token",
            )
        # The fake said yes, so consumption is behind us: the store
        # was read and the gate (not the seam) produced the refusal
        # for a workflow the empty store does not hold.
        self.assertEqual(len(calls), 1)
        self.assertEqual(loads, [True])
        self.assertFalse(outcome.ok)
        self.assertNotIn(outcome.problem, REFUSAL_CODES.values())
        self.assertEqual(outcome.problem, broker_module.PROBLEM_UNKNOWN_WORKFLOW)

    def test_perform_consumes_under_the_lock_before_the_store_is_read(self):
        # The R-01 consumption order, pinned on the source: inside
        # ``perform`` the seam call is the FIRST statement under the
        # exclusive store lock, and ``self.store.load()`` comes after
        # it inside the same ``with``; no other ``validate_and_consume``
        # call exists in the module.
        tree = ast.parse(BROKER_PATH.read_text())
        perform = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "perform"
        )
        withs = [
            node for node in perform.body if isinstance(node, ast.With)
        ]
        self.assertEqual(len(withs), 1)
        lock = withs[0].items[0].context_expr
        self.assertEqual(lock.func.attr, "exclusive_store_lock")
        first = withs[0].body[0]
        self.assertIsInstance(first, ast.Assign)
        call = first.value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual(call.func.attr, "validate_and_consume")
        self.assertEqual(call.func.value.attr, "capability_authority")
        self.assertEqual(call.func.value.value.id, "self")
        self.assertEqual(
            [getattr(arg, "id", None) for arg in call.args[:4]],
            ["capability", "workflow_id", "action", "revision"],
        )
        self.assertEqual(call.keywords, [])
        load_index = next(
            index for index, node in enumerate(withs[0].body)
            if any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "load"
                and getattr(inner.func.value, "attr", None) == "store"
                for inner in ast.walk(node)
            )
        )
        self.assertGreater(load_index, 0)
        consume_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "validate_and_consume"
        ]
        self.assertEqual(len(consume_calls), 1)
        self.assertIs(consume_calls[0], call)

    def test_broker_keyword_surface_grew_by_exactly_one_default_none(self):
        tree = ast.parse(BROKER_PATH.read_text())
        init = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            and any(arg.arg == "store_directory" for arg in node.args.args)
        )
        names = [arg.arg for arg in init.args.args]
        self.assertEqual(names.count("capability_authority"), 1)
        # The keyword's default is located by NAME rather than by
        # position: the worker seam added its own trailing keyword
        # after this one, and this pin is about the capability
        # keyword existing exactly once with a None default.
        defaults = dict(zip(
            names[len(names) - len(init.args.defaults):],
            init.args.defaults,
        ))
        default = defaults["capability_authority"]
        self.assertIsInstance(default, ast.Constant)
        self.assertIsNone(default.value)
        constructions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeCapabilityAuthority"
        ]
        self.assertEqual(len(constructions), 1)
        self.assertTrue(
            any(node is constructions[0] for node in ast.walk(init))
        )


class RuntimeSeamTests(_BrokerCase):
    """Both mint sites and compaction reach the Broker's bound seam,
    and the Runtime catches the NEUTRAL error type."""

    def test_perform_capability_action_mints_and_presents_through_the_fake(self):
        calls = []
        fake = FakeAuthority(
            calls, token="FAKE-TOKEN",
            consume=(False, "fake_problem", "fake detail"),
        )
        broker = self.make_broker(capability_authority=fake)
        results = []
        with _capability_functions_forbidden():
            outcome = runtime_module._perform_capability_action(
                broker, "wf-0001", 2, broker_module.ACTION_VERIFY,
                broker._clock, results,
            )
        self.assertEqual(calls, [
            ("mint", "wf-0001", broker_module.ACTION_VERIFY, 2, NOW),
            ("validate_and_consume", "FAKE-TOKEN", "wf-0001",
             broker_module.ACTION_VERIFY, 2, NOW),
        ])
        self.assertEqual(results, [(broker_module.ACTION_VERIFY, outcome)])
        self.assertEqual(outcome.problem, "fake_problem")
        self.assertFalse(os.path.exists(self.capability_path()))

    def test_perform_capability_action_catches_the_neutral_error(self):
        calls = []
        error = CapabilityError("fake store full")
        fake = FakeAuthority(calls, mint_error=error)
        broker = self.make_broker(capability_authority=fake)
        results = []
        with _capability_functions_forbidden():
            outcome = runtime_module._perform_capability_action(
                broker, "wf-0001", 2, broker_module.ACTION_VERIFY,
                broker._clock, results,
            )
        self.assertIsNone(outcome)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "mint")
        self.assertEqual(len(results), 1)
        label, refusal = results[0]
        self.assertEqual(label, broker_module.ACTION_VERIFY)
        self.assertFalse(refusal.ok)
        self.assertEqual(refusal.problem, runtime_module.PROBLEM_CAPABILITY_MINT)
        self.assertEqual(refusal.detail, "fake store full")

    def test_transition_step_mints_and_presents_through_the_fake(self):
        # The transition-step loop in ``advance_workflow``: a PREPARED
        # record's step embeds its request in the action, so no role
        # turn runs and the loop goes straight to mint + perform. The
        # record loader is replaced so no store schema is needed.
        calls = []
        fake = FakeAuthority(
            calls, token="FAKE-TOKEN",
            consume=(False, "fake_problem", "fake detail"),
        )
        broker = self.make_broker(capability_authority=fake)
        entry = {"phase": record_module.PHASE_PREPARED}
        with _capability_functions_forbidden(), \
             patch.object(runtime_module, "_load_entry",
                          return_value=(entry, None)):
            results = runtime_module.advance_workflow(broker, "wf-0001", 2)
        self.assertEqual(calls, [
            ("mint", "wf-0001", broker_module.ACTION_VALIDATE_HANDOFF, 2,
             NOW),
            ("validate_and_consume", "FAKE-TOKEN", "wf-0001",
             broker_module.ACTION_VALIDATE_HANDOFF, 2, NOW),
        ])
        self.assertEqual(len(results), 1)
        label, outcome = results[0]
        self.assertEqual(label, broker_module.ACTION_VALIDATE_HANDOFF)
        self.assertEqual(outcome.problem, "fake_problem")

    def test_transition_step_catches_the_neutral_error(self):
        calls = []
        fake = FakeAuthority(calls, mint_error=CapabilityError("no room"))
        broker = self.make_broker(capability_authority=fake)
        entry = {"phase": record_module.PHASE_PREPARED}
        with _capability_functions_forbidden(), \
             patch.object(runtime_module, "_load_entry",
                          return_value=(entry, None)):
            results = runtime_module.advance_workflow(broker, "wf-0001", 2)
        self.assertEqual([call[0] for call in calls], ["mint"])
        self.assertEqual(len(results), 1)
        label, refusal = results[0]
        self.assertEqual(label, broker_module.ACTION_VALIDATE_HANDOFF)
        self.assertEqual(refusal.problem, runtime_module.PROBLEM_CAPABILITY_MINT)
        self.assertEqual(refusal.detail, "no room")

    def test_compact_capabilities_reaches_the_fake(self):
        calls = []
        fake = FakeAuthority(calls, compacted=["retired-a", "retired-b"])
        broker = self.make_broker(capability_authority=fake)
        with _capability_functions_forbidden():
            removed = runtime_module.compact_capabilities(broker)
        self.assertIs(removed, fake.compacted)
        self.assertEqual(len(calls), 1)
        name, now, oracle, oracle_errors = calls[0]
        self.assertEqual(name, "compact")
        self.assertEqual(now, NOW)
        self.assertTrue(callable(oracle))
        self.assertEqual(oracle_errors, [])

    def test_compact_capabilities_contains_the_neutral_error(self):
        calls = []
        fake = FakeAuthority(
            calls, compact_error=CapabilityError("unreadable")
        )
        broker = self.make_broker(capability_authority=fake)
        with _capability_functions_forbidden():
            removed = runtime_module.compact_capabilities(broker)
        self.assertEqual(removed, [])
        self.assertEqual(len(calls), 1)

    def test_runtime_and_broker_reach_the_capability_module_only_via_the_seam(self):
        # Neither module imports ``target_runtime.capability``; every
        # call of a seam method name goes through an attribute named
        # ``capability_authority`` (the Broker's bound instance).
        expected = {
            BROKER_PATH: {"validate_and_consume": 1},
            RUNTIME_PATH: {"mint": 2, "compact": 1},
        }
        for path, counts in expected.items():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotEqual(module, "target_runtime.capability")
                    if module == "target_runtime":
                        for alias in node.names:
                            self.assertNotEqual(alias.name, "capability", path)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name, "target_runtime.capability", path
                        )
            seen = {}
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in SEAM_METHODS
                ):
                    owner = node.func.value
                    self.assertIsInstance(owner, ast.Attribute, (path, node.lineno))
                    self.assertEqual(
                        owner.attr, "capability_authority", (path, node.lineno)
                    )
                    self.assertEqual(node.keywords, [], (path, node.lineno))
                    seen[node.func.attr] = seen.get(node.func.attr, 0) + 1
            self.assertEqual(seen, counts, path)


class RealAdapterParityTests(_BrokerCase):
    """Every adversarial case through the REAL adapter answers exactly
    as the persistence module does when called directly."""

    WF = "wf-0001"
    ACTION = broker_module.ACTION_MATERIALIZE
    REVISION = 2

    def setUp(self):
        super().setUp()
        self.adapter = adapter_module.RuntimeCapabilityAuthority(
            self.store_dir
        )
        self.direct_dir = os.path.join(self.tmp.name, "direct")
        os.makedirs(self.direct_dir, mode=0o700)

    @contextlib.contextmanager
    def deterministic_nonces(self, prefix="nonce"):
        with patch.object(capability_module, "_default_nonce_factory",
                          _counting_nonce_factory(prefix)):
            yield

    def direct(self):
        """The module's functions, bound to a SECOND directory, so a
        scenario can be replayed against both and compared."""
        directory = self.direct_dir
        return (
            lambda *args: capability_module.mint(directory, *args),
            lambda *args: capability_module.validate_and_consume(
                directory, *args
            ),
            lambda *args: capability_module.compact(directory, *args),
        )

    def through_seam(self):
        return (
            self.adapter.mint, self.adapter.validate_and_consume,
            self.adapter.compact,
        )

    def run_both(self, scenario):
        """Run ``scenario(mint, consume, compact, directory)`` through
        the seam and directly, with identical deterministic nonces,
        and return the two transcripts."""
        with self.deterministic_nonces():
            seam = scenario(*self.through_seam(), directory=self.store_dir)
        with self.deterministic_nonces():
            direct = scenario(*self.direct(), directory=self.direct_dir)
        return seam, direct

    def assert_parity(self, scenario):
        seam, direct = self.run_both(scenario)
        self.assertEqual(seam, direct)
        return seam

    def consume_args(self, token, workflow_id=None, action=None,
                     revision=None, now=NOW):
        return (
            token,
            workflow_id if workflow_id is not None else self.WF,
            action if action is not None else self.ACTION,
            revision if revision is not None else self.REVISION,
            now,
        )

    def test_missing_and_malformed_presentations(self):
        def scenario(mint, consume, compact, directory):
            return [
                consume(*self.consume_args(None)),
                consume(*self.consume_args("")),
                consume(*self.consume_args(123)),
                consume(*self.consume_args(b"bytes")),
                os.path.exists(os.path.join(
                    directory, capability_module.CAPABILITIES_FILE_NAME
                )),
            ]
        transcript = self.assert_parity(scenario)
        for ok, problem, _ in transcript[:4]:
            self.assertFalse(ok)
            self.assertEqual(
                problem, contract_module.PROBLEM_CAPABILITY_MISSING
            )
        # Refusals write nothing: the store file was never created.
        self.assertFalse(transcript[4])

    def test_forged_unknown_token(self):
        def scenario(mint, consume, compact, directory):
            mint(self.WF, self.ACTION, self.REVISION, NOW)
            return [consume(*self.consume_args("f" * 64))]
        transcript = self.assert_parity(scenario)
        self.assertEqual(
            transcript[0][1], contract_module.PROBLEM_CAPABILITY_UNKNOWN
        )

    def test_consumed_then_replayed(self):
        def scenario(mint, consume, compact, directory):
            token = mint(self.WF, self.ACTION, self.REVISION, NOW)
            first = consume(*self.consume_args(token))
            replay = consume(*self.consume_args(token))
            # After an intervening mint the consumed entry is pruned,
            # so the replay may surface UNKNOWN instead; both refuse.
            mint(self.WF, self.ACTION, self.REVISION, NOW)
            later = consume(*self.consume_args(token))
            return [token, first, replay, later]
        transcript = self.assert_parity(scenario)
        self.assertEqual(transcript[1], (True, None, None))
        self.assertEqual(
            transcript[2][1], contract_module.PROBLEM_CAPABILITY_CONSUMED
        )
        self.assertFalse(transcript[3][0])
        self.assertIn(transcript[3][1], (
            contract_module.PROBLEM_CAPABILITY_CONSUMED,
            contract_module.PROBLEM_CAPABILITY_UNKNOWN,
        ))

    def test_expired(self):
        def scenario(mint, consume, compact, directory):
            token = mint(self.WF, self.ACTION, self.REVISION, NOW)
            expiry = NOW + capability_module.CAPABILITY_VALIDITY_SECONDS
            return [
                consume(*self.consume_args(token, now=expiry - 1))[0],
                consume(*self.consume_args(token, now=expiry)),
            ]
        transcript = self.assert_parity(scenario)
        # Presented one second before expiry it is spent; at expiry
        # the same token (already consumed) is refused. Prove the
        # expiry code itself on a fresh token:
        self.assertTrue(transcript[0])

        def fresh(mint, consume, compact, directory):
            token = mint(self.WF, self.ACTION, self.REVISION, NOW)
            expiry = NOW + capability_module.CAPABILITY_VALIDITY_SECONDS
            return [consume(*self.consume_args(token, now=expiry))]
        transcript = self.assert_parity(fresh)
        self.assertEqual(
            transcript[0][1], contract_module.PROBLEM_CAPABILITY_EXPIRED
        )

    def test_binding_mismatch_on_each_field(self):
        def scenario(mint, consume, compact, directory):
            token = mint(self.WF, self.ACTION, self.REVISION, NOW)
            return [
                consume(*self.consume_args(token, workflow_id="wf-0002")),
                consume(*self.consume_args(
                    token, action=broker_module.ACTION_VERIFY
                )),
                consume(*self.consume_args(token, revision=3)),
                consume(*self.consume_args(token)),
            ]
        transcript = self.assert_parity(scenario)
        for ok, problem, _ in transcript[:3]:
            self.assertFalse(ok)
            self.assertEqual(
                problem, contract_module.PROBLEM_CAPABILITY_MISMATCH
            )
        # None of the mismatches spent it.
        self.assertEqual(transcript[3], (True, None, None))

    def test_unreadable_store_refuses_consume_and_raises_for_mint_and_compact(self):
        def scenario(mint, consume, compact, directory):
            path = os.path.join(
                directory, capability_module.CAPABILITIES_FILE_NAME
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            transcript = [consume(*self.consume_args("x" * 64))[:2]]
            for call in (
                lambda: mint(self.WF, self.ACTION, self.REVISION, NOW),
                lambda: compact(NOW, None, []),
            ):
                try:
                    call()
                except CapabilityError as exc:
                    transcript.append((
                        type(exc) is capability_module.CapabilityError,
                        isinstance(exc, CapabilityError),
                        str(exc).replace(directory, "<dir>"),
                    ))
                else:
                    transcript.append("did not raise")
            with open(path, "r", encoding="utf-8") as handle:
                transcript.append(handle.read())
            return transcript
        transcript = self.assert_parity(scenario)
        self.assertEqual(
            transcript[0],
            (False, contract_module.PROBLEM_CAPABILITY_STORE),
        )
        for raised in transcript[1:3]:
            self.assertEqual(raised[:2], (True, True), raised)
        # Never reinitialized.
        self.assertEqual(transcript[3], "{not json")

    def crafted_full_store(self, consumed_at, expires_at):
        return {
            "capability_store_schema_version": (
                capability_module.CAPABILITY_STORE_SCHEMA_VERSION
            ),
            "capabilities": {
                "live%04d" % index: {
                    "workflow_id": "wf-%04d" % index,
                    "action": self.ACTION,
                    "revision": 1,
                    "issued_at": NOW - 10,
                    "expires_at": expires_at,
                    "consumed_at": consumed_at,
                }
                for index in range(capability_module.MAX_CAPABILITIES)
            },
        }

    def test_full_store_refuses_mint_and_never_evicts(self):
        def scenario(mint, consume, compact, directory):
            capability_module._save(
                directory,
                self.crafted_full_store(None, NOW + 900),
            )
            try:
                mint(self.WF, self.ACTION, self.REVISION, NOW)
            except CapabilityError as exc:
                raised = (
                    type(exc) is capability_module.CapabilityError,
                    str(exc),
                )
            else:
                raised = "did not raise"
            with open(os.path.join(
                directory, capability_module.CAPABILITIES_FILE_NAME
            ), encoding="utf-8") as handle:
                count = len(json.load(handle)["capabilities"])
            return [raised, count]
        transcript = self.assert_parity(scenario)
        self.assertTrue(transcript[0][0])
        self.assertIn("never evicted", transcript[0][1])
        self.assertIn(str(capability_module.MAX_CAPABILITIES), transcript[0][1])
        self.assertEqual(transcript[1], capability_module.MAX_CAPABILITIES)

    def test_full_of_dead_entries_prunes_then_mints(self):
        def scenario(mint, consume, compact, directory):
            transcript = []
            for dead in (
                self.crafted_full_store(None, NOW - 1),
                self.crafted_full_store(NOW - 1, NOW + 900),
            ):
                capability_module._save(directory, dead)
                token = mint(self.WF, self.ACTION, self.REVISION, NOW)
                with open(os.path.join(
                    directory, capability_module.CAPABILITIES_FILE_NAME
                ), encoding="utf-8") as handle:
                    remaining = sorted(json.load(handle)["capabilities"])
                transcript.append((token, remaining))
            return transcript
        transcript = self.assert_parity(scenario)
        for token, remaining in transcript:
            self.assertEqual(remaining, [token])

    def test_compaction_grounds_and_fail_closed_oracle(self):
        def scenario(mint, consume, compact, directory):
            # ``mint`` prunes consumed and expired entries as a side
            # effect, so the entries that must be dead at compaction
            # time are minted LAST, at an earlier clock, and the
            # consumed one is spent without any mint after it.
            proven = mint("wf-gone", self.ACTION, 1, NOW)
            ambiguous = mint("wf-maybe", self.ACTION, 1, NOW)
            live = mint(self.WF, self.ACTION, self.REVISION, NOW)
            expired = mint(self.WF, self.ACTION, self.REVISION, NOW - 5000)
            consumed = mint(self.WF, self.ACTION, self.REVISION, NOW - 5000)
            consume(*self.consume_args(consumed, now=NOW - 4999))
            verdicts = {
                "wf-gone": True, "wf-maybe": "yes", self.WF: False,
            }

            def oracle(workflow_id, action, revision):
                return verdicts[workflow_id]

            transcript = [compact(NOW, oracle, [])]
            # A second pass removes nothing and leaves the file as is.
            with open(os.path.join(
                directory, capability_module.CAPABILITIES_FILE_NAME
            ), "rb") as handle:
                before = handle.read()
            transcript.append(compact(NOW, oracle, []))
            with open(os.path.join(
                directory, capability_module.CAPABILITIES_FILE_NAME
            ), "rb") as handle:
                transcript.append(handle.read() == before)
            # No oracle at all: only the self-evident grounds apply.
            transcript.append(compact(NOW, None, None))
            # A broken oracle proves nothing: every entry is kept and
            # the failure is reported through ``oracle_errors``.
            errors = []

            def broken(workflow_id, action, revision):
                raise ValueError("oracle down for %s" % workflow_id)

            transcript.append(compact(NOW, broken, errors))
            transcript.append([
                (nonce, type(exc).__name__, str(exc)) for nonce, exc in errors
            ])
            with open(os.path.join(
                directory, capability_module.CAPABILITIES_FILE_NAME
            ), encoding="utf-8") as handle:
                transcript.append(sorted(json.load(handle)["capabilities"]))
            return [consumed, expired, proven, ambiguous, live] + transcript
        transcript = self.assert_parity(scenario)
        consumed, expired, proven, ambiguous, live = transcript[:5]
        self.assertEqual(transcript[5], sorted([consumed, expired, proven]))
        self.assertEqual(transcript[6], [])
        self.assertTrue(transcript[7])
        self.assertEqual(transcript[8], [])
        self.assertEqual(transcript[9], [])
        self.assertEqual(
            transcript[10],
            [(ambiguous, "ValueError", "oracle down for wf-maybe"),
             (live, "ValueError", "oracle down for wf-0001")],
        )
        self.assertEqual(transcript[11], sorted([ambiguous, live]))

    def test_consume_persists_before_returning(self):
        # Success through the seam is durable BEFORE the caller acts.
        with self.deterministic_nonces():
            token = self.adapter.mint(self.WF, self.ACTION, self.REVISION, NOW)
            self.assertEqual(
                self.adapter.validate_and_consume(*self.consume_args(token)),
                (True, None, None),
            )
        with open(self.capability_path(), encoding="utf-8") as handle:
            stored = json.load(handle)["capabilities"][token]
        self.assertEqual(stored["consumed_at"], NOW)
        self.assertEqual(
            oct(os.stat(self.capability_path()).st_mode & 0o777), oct(0o600)
        )


class LeakTests(_BrokerCase):
    """No token value reaches a Broker outcome surface beyond what the
    current code emits (which is: never)."""

    def outcome_text(self, outcome):
        return json.dumps(vars(outcome), sort_keys=True, default=repr)

    def test_no_token_value_in_any_refusal_or_gate_outcome(self):
        broker = self.make_broker()
        probe = "LEAKPROBENONCE"
        with patch.object(capability_module, "_default_nonce_factory",
                          _counting_nonce_factory(probe)):
            token = broker.capability_authority.mint(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2, NOW
            )
        self.assertTrue(token.startswith(probe))
        forged = probe + "FORGED"
        outcomes = [
            ("missing", broker.perform(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2
            )),
            ("forged", broker.perform(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2,
                capability=forged,
            )),
            ("wrong workflow", broker.perform(
                "wf-0002", broker_module.ACTION_MATERIALIZE, 2,
                capability=token,
            )),
            ("wrong action", broker.perform(
                "wf-0001", broker_module.ACTION_VERIFY, 2, capability=token
            )),
            ("wrong revision", broker.perform(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 3,
                capability=token,
            )),
            ("unknown action", broker.perform(
                "wf-0001", "not_an_action", 2, capability=token
            )),
        ]
        # An authentic presentation is spent and the gate then refuses
        # (the empty store holds no such workflow); then a replay.
        outcomes.append(("spent then gate-refused", broker.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2, capability=token
        )))
        outcomes.append(("replay", broker.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2, capability=token
        )))
        # The only place the value exists is the store file itself
        # (checked before the next mint, whose pruning drops the
        # consumed entry).
        with open(self.capability_path(), encoding="utf-8") as handle:
            self.assertIn(token, json.load(handle)["capabilities"])
        expired_broker = self.make_broker()
        expired_broker._clock = (
            lambda: NOW + capability_module.CAPABILITY_VALIDITY_SECONDS
        )
        with patch.object(capability_module, "_default_nonce_factory",
                          _counting_nonce_factory(probe + "LATE")):
            late = expired_broker.capability_authority.mint(
                "wf-0001", broker_module.ACTION_MATERIALIZE, 2, NOW
            )
        outcomes.append(("expired", expired_broker.perform(
            "wf-0001", broker_module.ACTION_MATERIALIZE, 2, capability=late
        )))
        problems = {}
        for label, outcome in outcomes:
            self.assertFalse(outcome.ok, label)
            text = self.outcome_text(outcome)
            self.assertNotIn(probe, text, (label, text))
            problems[label] = outcome.problem
        self.assertEqual(problems, {
            "missing": contract_module.PROBLEM_CAPABILITY_MISSING,
            "forged": contract_module.PROBLEM_CAPABILITY_UNKNOWN,
            "wrong workflow": contract_module.PROBLEM_CAPABILITY_MISMATCH,
            "wrong action": contract_module.PROBLEM_CAPABILITY_MISMATCH,
            "wrong revision": contract_module.PROBLEM_CAPABILITY_MISMATCH,
            "unknown action": broker_module.PROBLEM_UNKNOWN_ACTION,
            "spent then gate-refused": broker_module.PROBLEM_UNKNOWN_WORKFLOW,
            "replay": contract_module.PROBLEM_CAPABILITY_CONSUMED,
            "expired": contract_module.PROBLEM_CAPABILITY_EXPIRED,
        })


if __name__ == "__main__":
    unittest.main()
