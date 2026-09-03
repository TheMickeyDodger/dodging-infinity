"""Focused tests for the durable-execution seam.

Three surfaces: the substrate-neutral contract (``durable_execution``),
the Runtime-backed adapter (``target_runtime.durable_execution``), and
the CLI wired through it (``target_runtime.cli``). Every test is
hermetic: temporary directories only, no network, no repository
mutation.
"""

import ast
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from durable_execution import DurableExecution
from durable_execution import contract as contract_module
from target_runtime import broker as broker_module
from target_runtime import cli as cli_module
from target_runtime import durable_execution as adapter_module
from target_runtime import runtime as runtime_module

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "durable_execution"
ADAPTER_PATH = REPO_ROOT / "target_runtime" / "durable_execution.py"
CLI_PATH = REPO_ROOT / "target_runtime" / "cli.py"

SEAM_METHODS = (
    "recover_inherited_processes", "readiness_attention", "process_once",
)
# Roots the neutral package must never load, directly or transitively.
NON_NEUTRAL_ROOTS = (
    "target_runtime", "herdr", "herdctl", "telegram_operator",
    "codex_gateway", "workflow_authority", "operator_session",
    "human_interaction",
)


def _forbidden_runtime_call(name):
    def _fail(*args, **kwargs):
        raise AssertionError(
            "target_runtime.runtime.%s was called while a fake"
            " DurableExecution was injected; the fake path must reach"
            " no Runtime function" % name
        )
    return _fail


@contextlib.contextmanager
def _runtime_functions_forbidden():
    with patch.object(runtime_module, "recover_inherited_processes",
                      _forbidden_runtime_call(
                          "recover_inherited_processes")), \
         patch.object(runtime_module, "readiness_attention",
                      _forbidden_runtime_call("readiness_attention")), \
         patch.object(runtime_module, "process_once",
                      _forbidden_runtime_call("process_once")):
        yield


class FakeExecution(DurableExecution):
    """A seam implementation that records calls and returns fixed
    shapes; it never touches the Runtime."""

    def __init__(self, calls, recovered=None, readiness=None,
                 processed=None, process_error=None):
        self.calls = calls
        self.recovered = recovered if recovered is not None else ([], [])
        self.readiness = readiness if readiness is not None else ([], 0)
        self.processed = processed if processed is not None else {}
        self.process_error = process_error

    def recover_inherited_processes(self):
        self.calls.append("recover")
        return self.recovered

    def readiness_attention(self):
        self.calls.append("readiness")
        return self.readiness

    def process_once(self):
        self.calls.append("process_once")
        if self.process_error is not None:
            raise self.process_error
        return self.processed


class ContractTests(unittest.TestCase):
    """B1-B6, E1, E2, E11: the neutral package."""

    def test_contract_imports_in_isolation(self):
        # A fresh interpreter that loads ONLY the neutral package must
        # end up with none of the substrate, control-chain, or
        # orchestration modules in sys.modules.
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import durable_execution\n"
                    "import durable_execution.contract\n"
                    "roots = %r\n"
                    "bad = sorted(\n"
                    "    name for name in sys.modules\n"
                    "    if name.split('.')[0] in roots\n"
                    ")\n"
                    "print('\\n'.join(bad))\n"
                    "assert durable_execution.DurableExecution is"
                    " durable_execution.contract.DurableExecution\n"
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

    def test_exactly_three_abstract_no_argument_methods(self):
        self.assertTrue(issubclass(DurableExecution, object))
        self.assertEqual(
            DurableExecution.__abstractmethods__, frozenset(SEAM_METHODS)
        )
        for name in SEAM_METHODS:
            method = getattr(DurableExecution, name)
            self.assertEqual(
                list(method.__code__.co_varnames[:method.__code__.co_argcount]),
                ["self"],
                name,
            )
        # No public surface beyond the three methods.
        public = sorted(
            name for name in vars(DurableExecution)
            if not name.startswith("_")
        )
        self.assertEqual(public, sorted(SEAM_METHODS))

    def test_abc_and_partial_subclasses_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            DurableExecution()
        for missing in SEAM_METHODS:
            namespace = {
                name: (lambda self: None)
                for name in SEAM_METHODS if name != missing
            }
            partial = type("Partial", (DurableExecution,), namespace)
            with self.assertRaises(TypeError, msg=missing):
                partial()
        complete = type(
            "Complete", (DurableExecution,),
            {name: (lambda self: None) for name in SEAM_METHODS},
        )
        self.assertIsInstance(complete(), DurableExecution)

    def test_package_imports_stdlib_only(self):
        # B3 / E11: only ``abc`` and the package's own module; no
        # dynamic-import machinery. Every product module the package
        # must stay free of is named here explicitly.
        files = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertEqual(
            [p.name for p in files], ["__init__.py", "contract.py"]
        )
        allowed_roots = {"abc", "durable_execution"}
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
        # B4: nothing that would enter the derived bound-constant pin.
        # B2: no result types, exceptions, loops, locks, or try blocks.
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
                    [c.name for c in classes], ["DurableExecution"]
                )
            else:
                self.assertEqual(classes, [])

    def _assert_not_bound_style(self, name, path):
        self.assertFalse(name.startswith("MAX_"), (path, name))
        self.assertFalse(name.startswith("CANONICAL_"), (path, name))
        for suffix in ("_SECONDS", "_CHARS", "_CHUNKS"):
            self.assertFalse(name.endswith(suffix), (path, name))

    def test_package_exports_the_contract(self):
        self.assertIs(DurableExecution, contract_module.DurableExecution)
        import durable_execution
        self.assertEqual(durable_execution.__all__, ["DurableExecution"])


class AdapterTests(unittest.TestCase):
    """C1-C6, E3: exact delegation through the module object."""

    def setUp(self):
        self.broker = object()
        self.state_directory = object()
        self.adapter = adapter_module.RuntimeDurableExecution(
            self.broker, self.state_directory
        )

    def test_is_a_durable_execution_and_stores_arguments_unchanged(self):
        self.assertIsInstance(self.adapter, DurableExecution)
        self.assertIs(self.adapter.broker, self.broker)
        self.assertIs(self.adapter.state_directory, self.state_directory)
        self.assertEqual(
            sorted(vars(self.adapter)), ["broker", "state_directory"]
        )

    def test_constructor_touches_neither_argument(self):
        class Untouchable(object):
            def __getattribute__(self, name):
                raise AssertionError("constructor read %s" % name)

        # Construction must succeed even when any attribute read on
        # either argument would raise.
        adapter_module.RuntimeDurableExecution(Untouchable(), Untouchable())

    def test_module_binds_no_runtime_function_at_import_time(self):
        # Mirrors the operator-session precedent: the only substrate
        # import is the ``runtime`` MODULE, resolved through
        # ``target_runtime``; no name from ``target_runtime.runtime``
        # is bound in the adapter module.
        tree = ast.parse(ADAPTER_PATH.read_text())
        substrate_froms = [
            (node.module, [alias.name for alias in node.names])
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("target_runtime")
        ]
        self.assertEqual(substrate_froms, [("target_runtime", ["runtime"])])
        for name in SEAM_METHODS:
            self.assertFalse(hasattr(adapter_module, name), name)
        self.assertIs(adapter_module.runtime_module, runtime_module)
        self.assertIs(cli_module.runtime_module, runtime_module)

    def _assert_call_time_resolution(self, method, expected_argument):
        # The replacement is installed AFTER the adapter exists, so a
        # function bound at construction would never see it.
        sentinel = object()
        recorded = []

        def replacement(*args, **kwargs):
            recorded.append((args, kwargs))
            return sentinel

        with patch.object(runtime_module, method, replacement):
            result = getattr(self.adapter, method)()
        self.assertIs(result, sentinel)
        self.assertEqual(len(recorded), 1)
        args, kwargs = recorded[0]
        self.assertEqual(len(args), 1)
        self.assertIs(args[0], expected_argument)
        self.assertEqual(kwargs, {})

    def test_recover_inherited_processes_delegates_exactly(self):
        self._assert_call_time_resolution(
            "recover_inherited_processes", self.state_directory
        )

    def test_readiness_attention_delegates_exactly(self):
        self._assert_call_time_resolution(
            "readiness_attention", self.state_directory
        )

    def test_process_once_delegates_exactly(self):
        self._assert_call_time_resolution("process_once", self.broker)

    def test_replacement_through_cli_module_name_is_observed(self):
        # The exact patch shape the existing CLI tests use.
        recorded = []
        with patch.object(
            cli_module.runtime_module, "process_once",
            side_effect=lambda broker: recorded.append(broker) or {},
        ):
            self.adapter.process_once()
        self.assertEqual(len(recorded), 1)
        self.assertIs(recorded[0], self.broker)

    def test_exceptions_propagate_as_the_same_instance(self):
        for method in SEAM_METHODS:
            error = RuntimeError("substrate failure for %s" % method)

            def raising(*args, **kwargs):
                raise error

            with patch.object(runtime_module, method, raising):
                with self.assertRaises(RuntimeError) as caught:
                    getattr(self.adapter, method)()
            self.assertIs(caught.exception, error, method)

    def test_adapter_body_is_pure_delegation(self):
        # C4/C5: no try, loop, with, default, logging, or extra state;
        # each seam method is a single return of one call on
        # ``runtime_module.<same name>``.
        tree = ast.parse(ADAPTER_PATH.read_text())
        for node in ast.walk(tree):
            self.assertNotIsInstance(
                node, (ast.Try, ast.For, ast.While, ast.With), node
            )
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        self.assertEqual([c.name for c in classes], ["RuntimeDurableExecution"])
        methods = {
            n.name: n for n in classes[0].body
            if isinstance(n, ast.FunctionDef)
        }
        self.assertEqual(
            sorted(methods), sorted(("__init__",) + SEAM_METHODS)
        )
        for name in SEAM_METHODS:
            body = methods[name].body
            self.assertEqual(len(body), 1, name)
            self.assertIsInstance(body[0], ast.Return, name)
            call = body[0].value
            self.assertIsInstance(call, ast.Call, name)
            self.assertIsInstance(call.func, ast.Attribute, name)
            self.assertEqual(call.func.value.id, "runtime_module", name)
            self.assertEqual(call.func.attr, name)
            self.assertEqual(len(call.args), 1, name)
            self.assertEqual(call.keywords, [], name)
        init_body = methods["__init__"].body
        self.assertEqual(len(init_body), 2)
        for statement in init_body:
            self.assertIsInstance(statement, ast.Assign)
            self.assertIsInstance(statement.value, ast.Name)


class CliSeamTests(unittest.TestCase):
    """D1-D7, E4-E10: production construction and fake injection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_directory = os.path.join(self.tmp.name, "state")

    def run_main(self, argv, **kwargs):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = cli_module.main(argv, **kwargs)
        return code, stream.getvalue()

    @contextlib.contextmanager
    def _broker_built(self, broker=None):
        broker = broker if broker is not None else object()
        with patch.object(cli_module, "_build_broker",
                          return_value=(broker, self.state_directory)):
            yield broker

    # -- production construction (E4, D1) ------------------------------

    def test_main_constructs_the_adapter_from_build_broker(self):
        built = []
        seen = {}

        class Spy(adapter_module.RuntimeDurableExecution):
            def __init__(self, broker, state_directory):
                built.append((broker, state_directory))
                super().__init__(broker, state_directory)

        def recover(state_directory):
            seen["recover"] = state_directory
            return ([], [])

        def readiness(state_directory):
            seen["readiness"] = state_directory
            return ([], 0)

        def process(broker):
            seen["process"] = broker
            return {}

        with self._broker_built() as broker, \
             patch.object(cli_module, "RuntimeDurableExecution", Spy), \
             patch.object(runtime_module, "recover_inherited_processes",
                          recover), \
             patch.object(runtime_module, "readiness_attention",
                          readiness), \
             patch.object(runtime_module, "process_once", process):
            code, stderr = self.run_main(["once"])
        self.assertEqual(code, 0)
        self.assertEqual(len(built), 1)
        self.assertIs(built[0][0], broker)
        self.assertIs(built[0][1], self.state_directory)
        self.assertIs(seen["recover"], self.state_directory)
        self.assertIs(seen["readiness"], self.state_directory)
        self.assertIs(seen["process"], broker)
        self.assertIn("dirun: processed 0 workflow(s) (exact)", stderr)

    def test_production_wiring_drives_the_real_adapter_end_to_end(self):
        # No seam injected and no class replaced: the real adapter is
        # constructed and the patched module attributes are what it
        # reaches, in order, through ``cli.runtime_module``.
        calls = []
        with self._broker_built(), \
             patch.object(cli_module.runtime_module,
                          "recover_inherited_processes",
                          side_effect=lambda d: calls.append("recover")
                          or ([], [])), \
             patch.object(cli_module.runtime_module, "readiness_attention",
                          side_effect=lambda d: calls.append("readiness")
                          or ([], 0)), \
             patch.object(cli_module.runtime_module, "process_once",
                          side_effect=lambda b: calls.append("process_once")
                          or {}):
            code, _ = self.run_main(["once"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["recover", "readiness", "process_once"])

    # -- fake injection (E5, E6) --------------------------------------

    def test_fake_drives_once_without_any_runtime_function(self):
        calls = []
        fake = FakeExecution(calls)
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["recover", "readiness", "process_once"])
        self.assertIn(
            "dirun: readiness attention: 0 of 0 workflow(s)", stderr
        )
        self.assertIn("dirun: processed 0 workflow(s) (exact)", stderr)

    def test_fake_drives_run_with_pacing_without_any_runtime_function(self):
        calls = []
        pauses = []
        fake = FakeExecution(calls)
        with self._broker_built(), _runtime_functions_forbidden():
            code, _ = self.run_main(
                ["run"], sleeper=pauses.append, passes=2, execution=fake
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            calls, ["recover", "readiness", "process_once", "process_once"]
        )
        self.assertEqual(pauses, [cli_module.RUNTIME_POLL_INTERVAL_SECONDS])
        self.assertEqual(cli_module.RUNTIME_POLL_INTERVAL_SECONDS, 5)

    def test_run_with_three_passes_pauses_twice(self):
        calls = []
        pauses = []
        fake = FakeExecution(calls)
        with self._broker_built(), _runtime_functions_forbidden():
            code, _ = self.run_main(
                ["run"], sleeper=pauses.append, passes=3, execution=fake
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls.count("process_once"), 3)
        self.assertEqual(calls[:2], ["recover", "readiness"])
        self.assertEqual(
            pauses, [cli_module.RUNTIME_POLL_INTERVAL_SECONDS] * 2
        )

    def test_fake_return_values_reach_the_printed_lines_verbatim(self):
        # The seam passes the substrate's own shapes through; the CLI
        # renders them exactly as before.
        class Identity(object):
            owner_type = "workflow"
            owner_id = "wf-0001"
            unit_id = "task-1"
            control_digest = "abc123"

        recovered = (
            [(Identity(), ["g1"], [], ["g2"], [("/tmp/x", 4242, "REUSED")])],
            [("/tmp/stray", "no_label")],
        )
        readiness = (
            [{"workflow_id": "wf-0001", "state": "BOOTSTRAP_UNOBSERVABLE",
              "phase": "DISPATCHED", "stops_the_workflow": False}],
            3,
        )
        calls = []
        fake = FakeExecution(calls, recovered=recovered, readiness=readiness)
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        self.assertIn(
            "dirun: inherited processes for workflow wf-0001/task-1"
            " (control abc123): reaped 1, stuck 0, unstamped 1,"
            " uncorroborated 1",
            stderr,
        )
        self.assertIn(
            "dirun: group 4242 under /tmp/x is REPORTED and left alone"
            " (REUSED)",
            stderr,
        )
        self.assertIn(
            "dirun: unattributed process record directory REPORTED and"
            " left alone (no_label): /tmp/stray",
            stderr,
        )
        self.assertIn(
            "dirun: readiness attention: 1 of 3 workflow(s)", stderr
        )
        self.assertIn(
            "dirun:   wf-0001 is BOOTSTRAP_UNOBSERVABLE in phase"
            " DISPATCHED (does NOT stop the workflow", stderr,
        )

    def test_unreadable_readiness_denominator_line_is_unchanged(self):
        calls = []
        fake = FakeExecution(calls, readiness=([], None))
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        self.assertIn(
            "dirun: readiness could not be enumerated (store"
            " unreadable); this is an absent MEASUREMENT, not an"
            " absence of problems",
            stderr,
        )

    # -- exit codes (E7) -----------------------------------------------

    def test_bad_config_exits_2_before_the_seam_is_reached(self):
        calls = []
        fake = FakeExecution(calls)
        missing = os.path.join(self.tmp.name, "nope.json")
        with _runtime_functions_forbidden():
            code, stderr = self.run_main(
                ["--config", missing, "once"], execution=fake
            )
        self.assertEqual(code, 2)
        self.assertIn("dirun: config:", stderr)
        self.assertEqual(calls, [])

    def test_bad_config_never_constructs_the_adapter(self):
        missing = os.path.join(self.tmp.name, "nope.json")

        def refuse(*args, **kwargs):
            raise AssertionError("adapter constructed despite a bad config")

        with patch.object(cli_module, "RuntimeDurableExecution", refuse):
            code, _ = self.run_main(["--config", missing, "once"])
        self.assertEqual(code, 2)

    def test_held_lock_exits_3_before_the_seam_is_called(self):
        calls = []
        fake = FakeExecution(calls)
        descriptor = cli_module.acquire_runtime_lock(self.state_directory)
        self.assertIsNotNone(descriptor)
        self.addCleanup(os.close, descriptor)
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 3)
        self.assertIn("refusing to run twice", stderr)
        self.assertIn(self.state_directory, stderr)
        self.assertEqual(calls, [])

    def test_missing_subcommand_exits_2_before_the_seam(self):
        calls = []
        fake = FakeExecution(calls)
        with _runtime_functions_forbidden():
            code, _ = self.run_main([], execution=fake)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_keyboard_interrupt_from_the_seam_exits_0(self):
        calls = []
        fake = FakeExecution(calls, process_error=KeyboardInterrupt())
        with self._broker_built(), _runtime_functions_forbidden():
            code, _ = self.run_main(["run"], execution=fake)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["recover", "readiness", "process_once"])

    # -- refusal reporting (E8) ----------------------------------------

    def _refusal_pass(self):
        refusal = broker_module.BrokerOutcome(
            False, problem=broker_module.PROBLEM_POLICY_DRIFT,
            detail="policy changed after authorization",
        )
        return {"wf-live": [(broker_module.ACTION_VERIFY, refusal)]}

    def test_once_reports_a_refusal_returned_through_the_seam(self):
        calls = []
        fake = FakeExecution(calls, processed=self._refusal_pass())
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        self.assertEqual(stderr.count("REFUSED"), 1)
        self.assertIn(
            "dirun: workflow wf-live action verify REFUSED (%s):"
            " policy changed after authorization"
            % broker_module.PROBLEM_POLICY_DRIFT,
            stderr,
        )
        self.assertIn("dirun: processed 1 workflow(s) (exact)", stderr)

    def test_run_suppresses_a_persistent_refusal_after_the_first_pass(self):
        calls = []
        pauses = []
        fake = FakeExecution(calls, processed=self._refusal_pass())
        with self._broker_built(), _runtime_functions_forbidden():
            code, stderr = self.run_main(
                ["run"], sleeper=pauses.append, passes=3, execution=fake
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls.count("process_once"), 3)
        self.assertEqual(stderr.count("REFUSED"), 1)

    # -- lock behaviour (E9, D5) ---------------------------------------

    def test_lock_is_acquired_before_the_first_seam_call(self):
        calls = []
        fake = FakeExecution(calls)
        real_acquire = cli_module.acquire_runtime_lock

        def observed_acquire(state_directory):
            calls.append("lock")
            return real_acquire(state_directory)

        with self._broker_built(), _runtime_functions_forbidden(), \
             patch.object(cli_module, "acquire_runtime_lock",
                          observed_acquire):
            code, _ = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            calls, ["lock", "recover", "readiness", "process_once"]
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self.state_directory,
                             cli_module.RUNTIME_LOCK_FILE_NAME)
            )
        )

    def test_lock_descriptor_is_closed_when_the_seam_raises(self):
        calls = []
        error = RuntimeError("substrate failure")
        fake = FakeExecution(calls, process_error=error)
        real_acquire = cli_module.acquire_runtime_lock
        descriptors = []

        def observed_acquire(state_directory):
            descriptor = real_acquire(state_directory)
            descriptors.append(descriptor)
            return descriptor

        with self._broker_built(), _runtime_functions_forbidden(), \
             patch.object(cli_module, "acquire_runtime_lock",
                          observed_acquire):
            with self.assertRaises(RuntimeError) as caught:
                self.run_main(["once"], execution=fake)
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, ["recover", "readiness", "process_once"])
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])
        # The lock is free again: a fresh acquisition succeeds.
        reacquired = cli_module.acquire_runtime_lock(self.state_directory)
        self.assertIsNotNone(reacquired)
        os.close(reacquired)

    def test_lock_descriptor_is_closed_after_a_clean_once(self):
        calls = []
        fake = FakeExecution(calls)
        real_acquire = cli_module.acquire_runtime_lock
        descriptors = []

        def observed_acquire(state_directory):
            descriptor = real_acquire(state_directory)
            descriptors.append(descriptor)
            return descriptor

        with self._broker_built(), _runtime_functions_forbidden(), \
             patch.object(cli_module, "acquire_runtime_lock",
                          observed_acquire):
            code, _ = self.run_main(["once"], execution=fake)
        self.assertEqual(code, 0)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    # -- structural pins on cli.py (E10, D2, D3, D4) -------------------

    def test_cli_calls_no_runtime_module_function_directly(self):
        tree = ast.parse(CLI_PATH.read_text())
        direct = [
            (node.func.attr, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "runtime_module"
        ]
        self.assertEqual(direct, [])
        # And no other route to those names either: the only calls of
        # the three seam names in cli.py are ``execution.<name>()``.
        seam_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SEAM_METHODS
        ]
        self.assertEqual(len(seam_calls), 4)
        for node in seam_calls:
            self.assertIsInstance(node.func.value, ast.Name)
            self.assertEqual(node.func.value.id, "execution")
            self.assertEqual(node.args, [])
            self.assertEqual(node.keywords, [])

    def test_cli_still_binds_runtime_module(self):
        tree = ast.parse(CLI_PATH.read_text())
        bindings = [
            (node.module, alias.name, alias.asname)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname == "runtime_module"
        ]
        self.assertEqual(bindings, [("target_runtime", "runtime", "runtime_module")])
        self.assertIs(cli_module.runtime_module, runtime_module)

    def test_cli_has_exactly_one_production_construction_site(self):
        tree = ast.parse(CLI_PATH.read_text())
        constructions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeDurableExecution"
        ]
        self.assertEqual(len(constructions), 1)
        call = constructions[0]
        self.assertEqual(
            [arg.id for arg in call.args], ["broker", "state_directory"]
        )
        self.assertEqual(call.keywords, [])
        main = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        self.assertTrue(
            any(node is call for node in ast.walk(main)),
            "the adapter is constructed inside main",
        )

    def test_main_keyword_surface_grew_by_exactly_one_default_none(self):
        tree = ast.parse(CLI_PATH.read_text())
        main = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        names = [arg.arg for arg in main.args.args]
        self.assertEqual(names, ["argv", "sleeper", "passes", "execution"])
        self.assertEqual(len(main.args.defaults), 4)
        for default in main.args.defaults:
            self.assertIsInstance(default, ast.Constant)
            self.assertIsNone(default.value)
        self.assertIsNone(main.args.vararg)
        self.assertIsNone(main.args.kwarg)
        self.assertEqual(main.args.kwonlyargs, [])

    def test_acquire_runtime_lock_and_call_site_are_unchanged(self):
        tree = ast.parse(CLI_PATH.read_text())
        definitions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "acquire_runtime_lock"
        ]
        self.assertEqual(len(definitions), 1)
        self.assertEqual(
            [arg.arg for arg in definitions[0].args.args], ["state_directory"]
        )
        main = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        statements = main.body
        lock_index = next(
            index for index, node in enumerate(statements)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "acquire_runtime_lock"
        )
        recover_index = next(
            index for index, node in enumerate(statements)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "attr", None)
            == "recover_inherited_processes"
        )
        readiness_index = next(
            index for index, node in enumerate(statements)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "attr", None)
            == "readiness_attention"
        )
        # The lock-holding try is the one with a ``finally``; the
        # earlier argument-parsing try has none.
        try_index = next(
            index for index, node in enumerate(statements)
            if isinstance(node, ast.Try) and node.finalbody
        )
        self.assertLess(lock_index, recover_index)
        self.assertLess(recover_index, readiness_index)
        self.assertLess(readiness_index, try_index)
        finalbody = statements[try_index].finalbody
        self.assertEqual(len(finalbody), 1)
        close = finalbody[0].value
        self.assertEqual(close.func.attr, "close")
        self.assertEqual(close.args[0].id, "lock_descriptor")


if __name__ == "__main__":
    unittest.main()
