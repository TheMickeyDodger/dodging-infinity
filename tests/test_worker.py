"""Focused tests for the worker seam.

Three surfaces: the substrate-neutral contract (``worker``), the
Runtime-backed adapter (``target_runtime.worker``), and the production
wiring through it (``TargetBroker`` reaches every host-bound workspace,
trust, readiness, live-projection and close operation through its
bound ``worker``). Every test is hermetic: temporary directories only,
no network, no repository mutation, no real workspace closed. The
existing adversarial coverage in ``tests/test_target_runtime.py``,
``tests/test_workspace_trust.py``, ``tests/test_readiness.py`` and
``tests/test_ownership.py`` drives the same host modules through the
default adapter and stays authoritative; this file proves the seam
itself: that a fake replaces the host, that delegation is exact, that
the Domain B tri-state survives the move, and that the seam carries
no identity of its own.
"""

import ast
import contextlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import worker                                             # noqa: E402
from worker import Worker                                 # noqa: E402
from worker import contract as contract_module            # noqa: E402
from target_runtime import broker as broker_module        # noqa: E402
from target_runtime import capability as capability_module  # noqa: E402
from target_runtime import ownership as ownership_module  # noqa: E402
from target_runtime import readiness as readiness_module  # noqa: E402
from target_runtime import worker as adapter_module       # noqa: E402
from target_runtime import workspace as workspace_module  # noqa: E402
from target_runtime import workspace_ownership as ws_module  # noqa: E402
from target_runtime import workspace_trust as trust_module  # noqa: E402
from workflow_authority import record as wa_record        # noqa: E402

from test_target_runtime import NOW, RuntimeCase          # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "worker"
ADAPTER_PATH = REPO_ROOT / "target_runtime" / "worker.py"
BROKER_PATH = REPO_ROOT / "target_runtime" / "broker.py"

SEAM_METHODS = (
    "materialize_workspace", "verify_workspace", "relinquish_workspace",
    "establish_workspace_trust", "workspace_trust_consumable",
    "revoke_workspace_trust", "probe_readiness", "live_workspaces",
    "close_workspace",
)
SEAM_PRESENCE = ("observes_live_workspaces", "closes_workspaces")
# Positional parameters of each seam method, after ``self``: the
# production call graph with the bound inputs removed.
SEAM_SIGNATURES = {
    "materialize_workspace": ["record", "now"],
    "verify_workspace": ["record"],
    "relinquish_workspace": ["record", "now"],
    "establish_workspace_trust": ["record"],
    "workspace_trust_consumable": ["record"],
    "revoke_workspace_trust": ["record"],
    "probe_readiness": ["workspace_path"],
    "live_workspaces": [],
    "close_workspace": ["workspace_id"],
}
# The host module functions the adapter delegates to. A test that
# injects a fake worker installs a tripwire on EVERY one of these, so
# "the fake was called" is never the only evidence.
HOST_FUNCTIONS = (
    (workspace_module, "materialize"),
    (workspace_module, "verify_leased_workspace"),
    (workspace_module, "release"),
    (trust_module, "establish"),
    (trust_module, "revoke"),
    (trust_module, "resolve_config_path"),
    (trust_module, "is_trusted"),
    (trust_module, "default_config_path"),
    (adapter_module, "_production_readiness_probe"),
    (adapter_module, "_production_live_workspaces"),
    (ws_module, "production_close"),
)
# Roots the neutral package must never load, directly or transitively.
NON_NEUTRAL_ROOTS = (
    "target_runtime", "workflow_authority", "telegram_operator",
    "codex_gateway", "operator_session", "human_interaction",
    "durable_execution", "capability", "herdr", "herdctl",
    "git_transport",
)


@contextlib.contextmanager
def host_tripwires(case):
    """Replace every host function behind the seam with one that fails
    the test if reached."""
    def tripwire_for(module, name):
        def tripwire(*args, **kwargs):
            raise AssertionError(
                "host function %s.%s was reached while a fake worker"
                " is injected" % (module.__name__, name)
            )
        return tripwire

    with contextlib.ExitStack() as stack:
        for module, name in HOST_FUNCTIONS:
            stack.enter_context(
                patch.object(module, name, tripwire_for(module, name))
            )
        yield


class FakeWorker(Worker):
    """A scripted implementation: each operation returns the scripted
    value (or calls the scripted callable) and records the call."""

    def __init__(self, observes=False, closes=False, **scripts):
        self.calls = []
        self._observes = observes
        self._closes = closes
        self.scripts = scripts

    @property
    def observes_live_workspaces(self):
        return self._observes

    @property
    def closes_workspaces(self):
        return self._closes

    def _play(self, name, *args):
        self.calls.append((name,) + args)
        value = self.scripts[name]
        if callable(value):
            return value(*args)
        return value

    def materialize_workspace(self, record, now):
        return self._play("materialize_workspace", record, now)

    def verify_workspace(self, record):
        return self._play("verify_workspace", record)

    def relinquish_workspace(self, record, now):
        return self._play("relinquish_workspace", record, now)

    def establish_workspace_trust(self, record):
        return self._play("establish_workspace_trust", record)

    def workspace_trust_consumable(self, record):
        return self._play("workspace_trust_consumable", record)

    def revoke_workspace_trust(self, record):
        return self._play("revoke_workspace_trust", record)

    def probe_readiness(self, workspace_path):
        return self._play("probe_readiness", workspace_path)

    def live_workspaces(self):
        return self._play("live_workspaces")

    def close_workspace(self, workspace_id):
        return self._play("close_workspace", workspace_id)


def _calls_named(fake, name):
    return [call for call in fake.calls if call[0] == name]


# ---------------------------------------------------------------------
# 1. The neutral contract.
# ---------------------------------------------------------------------

class ContractTests(unittest.TestCase):

    def test_contract_imports_in_isolation(self):
        probe = subprocess.run(
            [
                sys.executable, "-c",
                "import sys\n"
                "import worker, worker.contract\n"
                "roots = %r\n"
                "bad = sorted(n for n in sys.modules"
                " if n.split('.')[0] in roots)\n"
                "print('\\n'.join(bad))\n"
                "sys.exit(1 if bad else 0)\n" % (NON_NEUTRAL_ROOTS,),
            ],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(
            probe.returncode, 0, (probe.stdout, probe.stderr)
        )

    def test_exactly_eleven_abstract_members_with_exact_signatures(self):
        self.assertEqual(
            set(Worker.__abstractmethods__),
            set(SEAM_METHODS) | set(SEAM_PRESENCE),
        )
        for name in SEAM_PRESENCE:
            member = inspect.getattr_static(Worker, name)
            self.assertIsInstance(member, property, name)
            self.assertIsNone(member.fset, name)
        for name, expected in SEAM_SIGNATURES.items():
            params = list(inspect.signature(getattr(Worker, name)).parameters)
            self.assertEqual(params, ["self"] + expected, name)

    def test_no_public_member_beyond_the_eleven(self):
        public = {
            name for name in vars(Worker)
            if not name.startswith("_")
        }
        self.assertEqual(public, set(SEAM_METHODS) | set(SEAM_PRESENCE))

    def test_abc_and_partial_subclasses_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            Worker()

        members = set(SEAM_METHODS) | set(SEAM_PRESENCE)
        for missing in sorted(members):
            partial = type("Partial", (Worker,), {
                name: getattr(FakeWorker, name)
                for name in members if name != missing
            })
            with self.assertRaises(TypeError, msg=missing):
                partial()
        FakeWorker()

    def test_package_imports_stdlib_only(self):
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertIn(
                        name.split(".")[0], ("abc", "worker"),
                        (path.name, name),
                    )

    def test_package_exports_the_contract_and_nothing_else(self):
        self.assertEqual(worker.__all__, ["Worker"])
        self.assertIs(worker.Worker, contract_module.Worker)
        # No refusal vocabulary and no exception type: every call
        # returns the substrate's own result, so the codes live with
        # the modules that define them.
        for module in (worker, contract_module):
            for name, value in vars(module).items():
                self.assertFalse(name.startswith("PROBLEM_"), name)
                self.assertFalse(
                    isinstance(value, type) and issubclass(value, Exception),
                    name,
                )

    def test_contract_exposes_no_identifier(self):
        for name in vars(Worker):
            if name.startswith("_"):
                continue
            lowered = name.lower()
            for token in ("_id", "identity", "name", "label", "token",
                          "authorization", "host"):
                self.assertNotIn(token, lowered, name)
        for name, expected in SEAM_SIGNATURES.items():
            for parameter in expected:
                self.assertIn(
                    parameter, ("record", "now", "workspace_path",
                                "workspace_id"),
                    (name, parameter),
                )


# ---------------------------------------------------------------------
# 2. The reference implementation.
# ---------------------------------------------------------------------

class AdapterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transport = object()
        self.root = os.path.join(self.tmp.name, "workspaces")
        self.config = os.path.join(self.tmp.name, "claude.json")

    def make(self, **kwargs):
        return adapter_module.RuntimeWorker(
            self.transport, self.root, self.config, **kwargs
        )

    def test_is_a_worker_and_binds_inputs_unchanged(self):
        probe, live, close = object(), object(), object()
        made = self.make(
            readiness_probe_fn=probe, live_workspaces_fn=live,
            workspace_close_fn=close,
        )
        self.assertIsInstance(made, Worker)
        self.assertIs(made.transport, self.transport)
        self.assertIs(made.workspaces_root, self.root)
        self.assertIs(made.config_path, self.config)
        self.assertEqual(
            sorted(vars(made)),
            sorted([
                "transport", "workspaces_root", "config_path",
                "_readiness_probe_fn", "_live_workspaces_fn",
                "_workspace_close_fn",
            ]),
            "the adapter holds exactly its bound inputs: no identity,"
            " no store, no cached verdict",
        )

    def test_constructor_does_no_io_and_creates_no_store(self):
        with host_tripwires(self):
            self.make(
                readiness_probe_fn=lambda p: None,
                live_workspaces_fn=lambda: [],
                workspace_close_fn=lambda w: True,
            )
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assertFalse(os.path.exists(self.root))
        self.assertFalse(os.path.exists(self.config))

    def test_presence_is_computed_from_the_bound_callables(self):
        absent = self.make()
        self.assertFalse(absent.observes_live_workspaces)
        self.assertFalse(absent.closes_workspaces)
        wired = self.make(
            live_workspaces_fn=lambda: [], workspace_close_fn=lambda w: True
        )
        self.assertTrue(wired.observes_live_workspaces)
        self.assertTrue(wired.closes_workspaces)
        only_close = self.make(workspace_close_fn=lambda w: True)
        self.assertFalse(only_close.observes_live_workspaces)
        self.assertTrue(only_close.closes_workspaces)
        for name in SEAM_PRESENCE:
            with self.assertRaises(AttributeError, msg=name):
                setattr(absent, name, True)

    def test_module_binds_no_host_function_at_import_time(self):
        tree = ast.parse(ADAPTER_PATH.read_text())
        for node in tree.body:
            self.assertIsInstance(
                node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                       ast.ClassDef, ast.Expr),
                ast.dump(node)[:80],
            )
            if isinstance(node, ast.Expr):
                self.assertIsInstance(node.value, ast.Constant)
        klass = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RuntimeWorker"
        )
        init = next(
            node for node in klass.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        for node in ast.walk(init):
            self.assertFalse(isinstance(node, ast.Call), (
                "the constructor calls something; it must only bind",
                ast.dump(node)[:80],
            ))

    def test_adapter_body_is_pure_delegation(self):
        tree = ast.parse(ADAPTER_PATH.read_text())
        klass = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RuntimeWorker"
        )
        methods = {
            node.name: node for node in klass.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            set(methods),
            {"__init__"} | set(SEAM_METHODS) | set(SEAM_PRESENCE),
        )
        for name in SEAM_METHODS:
            if name == "workspace_trust_consumable":
                # The one body that moved rather than delegated: the
                # Broker's point-of-use trust check, verbatim.
                continue
            body = [
                node for node in methods[name].body
                if not (isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant))
            ]
            self.assertEqual(len(body), 1, name)
            self.assertIsInstance(body[0], ast.Return, name)
            self.assertIsInstance(body[0].value, ast.Call, name)
            func = body[0].value.func
            self.assertIsInstance(func, ast.Attribute, name)
            owner = func.value
            if name in ("probe_readiness", "live_workspaces",
                        "close_workspace"):
                self.assertIsInstance(owner, ast.Name, name)
                self.assertEqual(owner.id, "self", name)
                self.assertTrue(func.attr.endswith("_fn"), name)
            else:
                self.assertIsInstance(owner, ast.Name, name)
                self.assertIn(
                    owner.id, ("workspace_module", "workspace_trust_module"),
                    name,
                )

    def test_moved_readers_live_here_and_nowhere_else(self):
        for name in ("_production_readiness_probe",
                     "_production_live_workspaces"):
            self.assertTrue(callable(getattr(adapter_module, name)), name)
            self.assertFalse(hasattr(broker_module, name), name)
        self.assertFalse(
            hasattr(broker_module.TargetBroker, "_trust_still_consumable")
        )

    # -- exact delegation, resolved at call time ------------------------

    def _recorder(self, result):
        calls = []

        def recorded(*args, **kwargs):
            calls.append((args, kwargs))
            return result
        return recorded, calls

    def test_materialize_delegates_exactly(self):
        made = self.make()
        record, result = {"r": 1}, (False, "problem", "detail")
        recorded, calls = self._recorder(result)
        with patch.object(workspace_module, "materialize", recorded):
            self.assertIs(made.materialize_workspace(record, NOW), result)
        self.assertEqual(
            calls, [((record, self.transport, self.root), {"now": NOW})]
        )

    def test_verify_delegates_exactly(self):
        made = self.make()
        record, result = {"r": 1}, (True, None, None)
        recorded, calls = self._recorder(result)
        with patch.object(workspace_module, "verify_leased_workspace",
                          recorded):
            self.assertIs(made.verify_workspace(record), result)
        self.assertEqual(calls, [((record, self.transport, self.root), {})])

    def test_relinquish_delegates_exactly(self):
        made = self.make()
        record, result = {"r": 1}, (True, None, None)
        recorded, calls = self._recorder(result)
        with patch.object(workspace_module, "release", recorded):
            self.assertIs(made.relinquish_workspace(record, NOW), result)
        self.assertEqual(calls, [((record, self.root), {"now": NOW})])

    def test_establish_delegates_exactly(self):
        made = self.make()
        record = {"r": 1}
        result = (False, trust_module.PROBLEM_CONFIG_MISSING, "gone")
        recorded, calls = self._recorder(result)
        with patch.object(trust_module, "establish", recorded):
            self.assertIs(made.establish_workspace_trust(record), result)
        self.assertEqual(calls, [((record, self.root, self.config), {})])

    def test_revoke_delegates_exactly(self):
        made = self.make()
        record = {"r": 1}
        result = (False, trust_module.PROBLEM_LEASE_MISSING, "no lease")
        recorded, calls = self._recorder(result)
        with patch.object(trust_module, "revoke", recorded):
            self.assertIs(made.revoke_workspace_trust(record), result)
        self.assertEqual(calls, [((record, self.root, self.config), {})])

    def test_bound_callables_delegate_exactly(self):
        probe, probe_calls = self._recorder({"lead1": {}})
        live, live_calls = self._recorder([])
        close, close_calls = self._recorder(True)
        made = self.make(
            readiness_probe_fn=probe, live_workspaces_fn=live,
            workspace_close_fn=close,
        )
        self.assertEqual(made.probe_readiness("/lease"), {"lead1": {}})
        self.assertEqual(made.live_workspaces(), [])
        self.assertIs(made.close_workspace("wTEST"), True)
        self.assertEqual(probe_calls, [(("/lease",), {})])
        self.assertEqual(live_calls, [((), {})])
        self.assertEqual(close_calls, [(("wTEST",), {})])

    def test_an_absent_callable_raises_the_same_type_error_as_before(self):
        made = self.make()
        with self.assertRaises(TypeError):
            made.live_workspaces()
        with self.assertRaises(TypeError):
            made.close_workspace("wTEST")
        with self.assertRaises(TypeError):
            made.probe_readiness("/lease")

    def test_exceptions_propagate_as_the_same_instance(self):
        made = self.make(
            readiness_probe_fn=lambda p: (_ for _ in ()).throw(boom),
            live_workspaces_fn=lambda: (_ for _ in ()).throw(boom),
            workspace_close_fn=lambda w: (_ for _ in ()).throw(boom),
        )
        boom = RuntimeError("host failed")

        def raiser(*args, **kwargs):
            raise boom

        with contextlib.ExitStack() as stack:
            for module, name in HOST_FUNCTIONS[:5]:
                stack.enter_context(patch.object(module, name, raiser))
            for call in (
                lambda: made.materialize_workspace({}, NOW),
                lambda: made.verify_workspace({}),
                lambda: made.relinquish_workspace({}, NOW),
                lambda: made.establish_workspace_trust({}),
                lambda: made.revoke_workspace_trust({}),
                lambda: made.probe_readiness("/lease"),
                made.live_workspaces,
                lambda: made.close_workspace("wTEST"),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    call()
                self.assertIs(caught.exception, boom)

    def test_trust_consumable_is_the_moved_point_of_use_check(self):
        made = self.make()
        self.assertEqual(
            made.workspace_trust_consumable({"workspace_lease": None}),
            (False, trust_module.PROBLEM_LEASE_MISSING,
             "no workspace lease is recorded to verify trust for"),
        )
        record = {"workspace_lease": {"path_realpath": "/lease"}}
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        with patch.dict(os.environ, {"HOME": elsewhere}):
            ok, problem, detail = made.workspace_trust_consumable(record)
        self.assertEqual(
            (ok, problem), (False, trust_module.PROBLEM_CONFIG_NOT_CONSUMED)
        )
        self.assertIn("could not be consumed", detail)
        with patch.dict(os.environ, {"HOME": self.tmp.name}):
            same = self.make()
            same.config_path = trust_module.default_config_path()
            with patch.object(trust_module, "is_trusted",
                              lambda config, path: False):
                ok, problem, detail = same.workspace_trust_consumable(record)
            self.assertEqual(
                (ok, problem),
                (False, trust_module.PROBLEM_TRUST_NOT_PRESENT),
            )
            with patch.object(trust_module, "is_trusted",
                              lambda config, path: True):
                self.assertEqual(
                    same.workspace_trust_consumable(record),
                    (True, None, None),
                )


# ---------------------------------------------------------------------
# 3. Broker construction and the seam.
# ---------------------------------------------------------------------

class _BrokerCase(unittest.TestCase):
    """A hermetic Broker whose non-worker seams are inert objects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_dir = os.path.join(self.tmp.name, "store")
        os.makedirs(self.store_dir, mode=0o700)
        self.transport = object()
        self.workspaces = os.path.join(self.tmp.name, "workspaces")
        self.config = os.path.join(self.tmp.name, "claude.json")

    def make_broker(self, **kwargs):
        def never(*args, **kwargs):
            raise AssertionError("an inert seam was reached")

        return broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=os.path.join(
                self.tmp.name, "control"
            ),
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=never,
            claude_config_path=self.config,
            spawn_fn=never,
            clock=lambda: NOW,
            observer_fn=never,
            spawn_records_fn=kwargs.pop(
                "spawn_records_fn", lambda control: {"listed": []}
            ),
            **kwargs
        )


class BrokerSeamTests(_BrokerCase):

    def test_default_is_the_reference_worker_over_the_broker_inputs(self):
        broker = self.make_broker()
        seam = broker.worker
        self.assertIsInstance(seam, adapter_module.RuntimeWorker)
        self.assertIs(seam.transport, broker.transport)
        self.assertIs(seam.workspaces_root, broker.workspaces_root)
        self.assertIs(seam.config_path, broker.claude_config_path)
        self.assertIs(
            seam._readiness_probe_fn,
            adapter_module._production_readiness_probe,
        )
        self.assertFalse(seam.observes_live_workspaces)
        self.assertFalse(seam.closes_workspaces)

    def test_host_callables_are_bound_into_the_default_worker(self):
        def probe(path):
            return None

        def live():
            return []

        def close(workspace_id):
            return True

        broker = self.make_broker(
            readiness_probe_fn=probe, live_workspaces_fn=live,
            workspace_close_fn=close,
        )
        self.assertIs(broker.worker._readiness_probe_fn, probe)
        self.assertIs(broker.worker._live_workspaces_fn, live)
        self.assertIs(broker.worker._workspace_close_fn, close)
        self.assertTrue(broker.worker.observes_live_workspaces)
        self.assertTrue(broker.worker.closes_workspaces)

    def test_default_construction_does_no_io(self):
        with host_tripwires(self):
            broker = self.make_broker()
        self.assertFalse(os.path.exists(self.workspaces))
        self.assertFalse(os.path.exists(self.config))
        self.assertEqual(os.listdir(self.store_dir), [])
        self.assertIsInstance(broker.worker, adapter_module.RuntimeWorker)

    def test_injected_worker_is_used_as_given(self):
        fake = FakeWorker()
        broker = self.make_broker(worker=fake)
        self.assertIs(broker.worker, fake)

    def test_worker_beside_a_host_callable_is_refused(self):
        fake = FakeWorker()
        for kwargs in (
            {"readiness_probe_fn": lambda p: None},
            {"live_workspaces_fn": lambda: []},
            {"workspace_close_fn": lambda w: True},
        ):
            with self.assertRaises(TypeError, msg=repr(kwargs)):
                self.make_broker(worker=fake, **kwargs)
        self.assertEqual(os.listdir(self.store_dir), [])

    def test_no_duplicate_host_state_remains_on_the_broker(self):
        broker = self.make_broker()
        for name in ("_readiness_probe", "_live_workspaces",
                     "_workspace_close", "_trust_still_consumable"):
            self.assertFalse(hasattr(broker, name), name)

    def test_broker_keyword_surface_grew_by_exactly_one_default_none(self):
        tree = ast.parse(BROKER_PATH.read_text())
        init = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            and any(arg.arg == "store_directory" for arg in node.args.args)
        )
        names = [arg.arg for arg in init.args.args]
        self.assertEqual(names[-1], "worker")
        self.assertEqual(names.count("worker"), 1)
        last_default = init.args.defaults[-1]
        self.assertIsInstance(last_default, ast.Constant)
        self.assertIsNone(last_default.value)
        constructions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeWorker"
        ]
        self.assertEqual(len(constructions), 1)
        self.assertTrue(
            any(node is constructions[0] for node in ast.walk(init))
        )


class DomainBTriStateTests(_BrokerCase):
    """The Domain B configuration tri-state, through the seam, on the
    reference implementation: the branch each wiring reaches is the
    branch it reached before the seam existed."""

    ENTRY = {"workflow_id": "wf-0001", "target_engine": {"task_id": "t1"}}

    def release_with(self, live_fn, close_fn):
        broker = self.make_broker(
            live_workspaces_fn=live_fn, workspace_close_fn=close_fn
        )
        report = ownership_module.CleanupReport()
        snapshot = broker_module._domain_b_proof(broker, dict(self.ENTRY))
        result = broker_module._domain_b_release(
            broker, dict(self.ENTRY), report, snapshot=snapshot
        )
        return result, report

    def test_not_configured_reclaims(self):
        result, report = self.release_with(None, None)
        self.assertEqual(result, broker_module.SESSIONS_RECLAIMED)
        self.assertEqual(
            report.skipped_not_owned, [("workspace_session", "wf-0001")]
        )
        self.assertEqual(report.unprovable, [])

    def test_close_absent_with_projection_present_retains(self):
        result, report = self.release_with(lambda: [], None)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(report.unprovable, [(
            "workspace_session", "wf-0001",
            "no workspace-close capability is wired, so the sessions"
            " cannot be reclaimed",
        )])

    def test_configured_but_unreadable_retains(self):
        closed = []
        result, report = self.release_with(lambda: None, closed.append)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(closed, [])
        self.assertEqual(report.unprovable, [(
            "workspace_session", "wf-0001",
            "%s: the live workspace listing is unreadable"
            % ws_module.PROBLEM_EVIDENCE_DEGRADED,
        )])

    def test_configured_but_raising_retains(self):
        closed = []

        def raising():
            raise RuntimeError("listing failed")

        result, report = self.release_with(raising, closed.append)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(closed, [])
        self.assertEqual(report.unprovable, [(
            "workspace_session", "wf-0001",
            "workspace evidence unreadable (RuntimeError)",
        )])

    def test_projection_absent_with_close_present_retains_with_type_error(self):
        # Close wired, projection absent: the proof yields no snapshot,
        # the nothing-to-close path calls the absent projection, and
        # the resulting TypeError is caught and recorded exactly as it
        # was when the Broker called None() itself. Preserved, not
        # fixed: it is recorded behavior.
        closed = []
        result, report = self.release_with(None, closed.append)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(closed, [])
        self.assertEqual(report.unprovable, [(
            "workspace_session", "wf-0001",
            "workspace evidence unreadable (TypeError)",
        )])


# ---------------------------------------------------------------------
# 4. A fake worker replaces the host on the production path.
# ---------------------------------------------------------------------

class FakeWorkerCase(RuntimeCase):
    """The real store, record and phase machine of ``RuntimeCase``,
    with the host replaced by a fake for the actions under test."""

    def fake_broker(self, fake):
        return broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn,
            clock=lambda: NOW,
            observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            worker=fake,
        )

    def perform_with(self, broker, workflow_id, action, revision=2):
        token = capability_module.mint(
            self.store_dir, workflow_id, action, revision, NOW
        )
        return broker.perform(
            workflow_id, action, revision, capability=token
        )

    def disk_entry(self, workflow_id="wf-0001"):
        return self.fresh_workflows()["workflows"][workflow_id]

    def real_lease(self):
        """One REAL materialization through the default worker, so a
        later fake can hand out a lease of the exact recorded shape."""
        self.put_record(self.authorized_record())
        outcome = self.perform("wf-0001", broker_module.ACTION_MATERIALIZE)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        return dict(self.disk_entry()["workspace_lease"])


class FakeWorkerMaterializeTests(FakeWorkerCase):

    def test_refusal_passes_through_and_no_host_function_runs(self):
        self.put_record(self.authorized_record())
        fake = FakeWorker(
            materialize_workspace=(
                False, workspace_module.PROBLEM_REMOTE_MISMATCH, "wrong"
            ),
        )
        broker = self.fake_broker(fake)
        with host_tripwires(self):
            outcome = self.perform_with(
                broker, "wf-0001", broker_module.ACTION_MATERIALIZE
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            (outcome.problem, outcome.detail),
            (workspace_module.PROBLEM_REMOTE_MISMATCH, "wrong"),
        )
        self.assertEqual(
            [call[0] for call in fake.calls], ["materialize_workspace"]
        )
        (_, record, now), = fake.calls
        self.assertEqual(record["workflow_id"], "wf-0001")
        self.assertEqual(now, NOW)
        self.assertEqual(self.disk_entry()["phase"], "AUTHORIZED")
        self.assertIsNone(self.disk_entry()["workspace_lease"])

    def test_crash_ambiguity_still_blocks_durably_through_the_seam(self):
        self.put_record(self.authorized_record())
        fake = FakeWorker(
            materialize_workspace=(
                False, workspace_module.PROBLEM_WORKSPACE_EXISTS, "reused"
            ),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0001",
                broker_module.ACTION_MATERIALIZE,
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            outcome.problem, workspace_module.PROBLEM_WORKSPACE_EXISTS
        )
        entry = self.disk_entry()
        self.assertEqual(entry["phase"], "BLOCKED")
        self.assertEqual(entry["ambiguity"], {
            "state": wa_record.AMBIGUITY_CRASH_UNCERTAIN,
            "detail": "reused",
        })

    def test_trust_refusal_passes_through_after_a_fake_materialization(self):
        lease = self.real_lease()
        self.put_record(self.authorized_record("wf-0002"))
        second = dict(lease, path_realpath=workspace_module.lease_path(
            self.workspaces, "wf-0002"
        ))
        os.makedirs(second["path_realpath"])

        def materialize(record, now):
            record["workspace_lease"] = dict(second)
            return True, None, None

        fake = FakeWorker(
            materialize_workspace=materialize,
            establish_workspace_trust=(
                False, trust_module.PROBLEM_CONFIG_MISSING, "no config"
            ),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0002",
                broker_module.ACTION_MATERIALIZE,
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            (outcome.problem, outcome.detail),
            (trust_module.PROBLEM_CONFIG_MISSING, "no config"),
        )
        self.assertEqual(
            [call[0] for call in fake.calls],
            ["materialize_workspace", "establish_workspace_trust"],
        )
        entry = self.disk_entry("wf-0002")
        self.assertEqual(entry["phase"], "BLOCKED")
        self.assertEqual(
            entry["receipts"][-1]["bounded_summary"],
            trust_module.trust_block_receipt(
                trust_module.PROBLEM_CONFIG_MISSING, now=NOW
            )["bounded_summary"],
        )

    def test_success_advances_to_workspace_ready_through_the_seam(self):
        lease = self.real_lease()
        self.put_record(self.authorized_record("wf-0002"))
        second = dict(lease, path_realpath=workspace_module.lease_path(
            self.workspaces, "wf-0002"
        ))
        os.makedirs(second["path_realpath"])

        def materialize(record, now):
            record["workspace_lease"] = dict(second)
            return True, None, None

        fake = FakeWorker(
            materialize_workspace=materialize,
            establish_workspace_trust=(True, None, None),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0002",
                broker_module.ACTION_MATERIALIZE,
            )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        entry = self.disk_entry("wf-0002")
        self.assertEqual(entry["phase"], "WORKSPACE_READY")
        self.assertEqual(entry["workspace_lease"], second)
        self.assertEqual(
            [call[0] for call in fake.calls],
            ["materialize_workspace", "establish_workspace_trust"],
        )


class FakeWorkerLifecycleTests(FakeWorkerCase):
    """PREPARE, VALIDATE_HANDOFF and DISPATCH over a real lease, with
    verification and the point-of-use trust check answered by the
    fake and every host function tripwired."""

    def test_verify_and_consumable_are_reached_only_through_the_fake(self):
        self.real_lease()
        fake = FakeWorker(
            verify_workspace=(True, None, None),
            workspace_trust_consumable=(True, None, None),
        )
        broker = self.fake_broker(fake)
        with host_tripwires(self):
            for action in (broker_module.ACTION_PREPARE,
                           broker_module.ACTION_VALIDATE_HANDOFF,
                           broker_module.ACTION_DISPATCH):
                outcome = self.perform_with(broker, "wf-0001", action)
                self.assertTrue(
                    outcome.ok, (action, outcome.problem, outcome.detail)
                )
        self.assertEqual(self.disk_entry()["phase"], "DISPATCHED")
        names = [call[0] for call in fake.calls]
        self.assertEqual(names.count("verify_workspace"), 3)
        self.assertEqual(names.count("workspace_trust_consumable"), 1)
        self.assertEqual(
            names.index("workspace_trust_consumable"),
            len(names) - 1,
            "the trust check runs at the point of use, after the"
            " dispatch verification",
        )
        for call in fake.calls:
            self.assertEqual(call[1]["workflow_id"], "wf-0001")
        self.assertEqual(len(self.spawn_requests), 1)

    def test_a_verification_refusal_passes_through_unchanged(self):
        self.real_lease()
        fake = FakeWorker(
            verify_workspace=(
                False, workspace_module.PROBLEM_BASELINE_MISMATCH, "moved"
            ),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0001",
                broker_module.ACTION_PREPARE,
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            (outcome.problem, outcome.detail),
            (workspace_module.PROBLEM_BASELINE_MISMATCH, "moved"),
        )
        self.assertEqual(self.disk_entry()["phase"], "WORKSPACE_READY")

    def test_a_trust_refusal_at_dispatch_blocks_durably_through_the_seam(self):
        self.real_lease()
        fake = FakeWorker(
            verify_workspace=(True, None, None),
            workspace_trust_consumable=(
                False, trust_module.PROBLEM_TRUST_NOT_PRESENT, "dropped"
            ),
        )
        broker = self.fake_broker(fake)
        with host_tripwires(self):
            for action in (broker_module.ACTION_PREPARE,
                           broker_module.ACTION_VALIDATE_HANDOFF):
                self.assertTrue(
                    self.perform_with(broker, "wf-0001", action).ok
                )
            outcome = self.perform_with(
                broker, "wf-0001", broker_module.ACTION_DISPATCH
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            (outcome.problem, outcome.detail),
            (trust_module.PROBLEM_TRUST_NOT_PRESENT, "dropped"),
        )
        self.assertEqual(self.disk_entry()["phase"], "BLOCKED")
        self.assertEqual(len(self.spawn_requests), 0)


class ReadinessThroughTheSeamTests(FakeWorkerCase):
    """The readiness probe reaches the gate only through the worker,
    and each probe outcome yields the state ``readiness.evaluate``
    derives for it today."""

    def setUp(self):
        super().setUp()
        # A target still in flight, so VERIFY consults the readiness
        # gate rather than collecting evidence from a finished one.
        self.target_task_status = "ACTIVE"

    def dispatched_with(self, fake):
        self.real_lease()
        broker = self.fake_broker(fake)
        with host_tripwires(self):
            for action in (broker_module.ACTION_PREPARE,
                           broker_module.ACTION_VALIDATE_HANDOFF,
                           broker_module.ACTION_DISPATCH):
                self.assertTrue(
                    self.perform_with(broker, "wf-0001", action).ok
                )
        return broker

    def assert_gate_matches_evaluate(self, probe_value):
        fake = FakeWorker(
            verify_workspace=(True, None, None),
            workspace_trust_consumable=(True, None, None),
            probe_readiness=probe_value,
        )
        broker = self.dispatched_with(fake)
        before = dict(self.disk_entry())
        expected_state, _d, _p, _probed, _stop = readiness_module.evaluate(
            before,
            lambda: (probe_value(None) if callable(probe_value)
                     else probe_value),
            NOW,
        )
        with host_tripwires(self):
            self.perform_with(broker, "wf-0001", broker_module.ACTION_VERIFY)
        probes = _calls_named(fake, "probe_readiness")
        self.assertEqual(
            probes, [("probe_readiness",
                      before["workspace_lease"]["path_realpath"])],
        )
        self.assertEqual(
            self.readiness_probe_calls, [],
            "the fixture's injected probe ran; the gate bypassed the"
            " worker",
        )
        states = readiness_module.recorded_states(self.disk_entry())
        self.assertEqual(states[-1], expected_state)
        return expected_state

    def test_a_mapping_reaches_ready(self):
        probe = {
            logical: {
                "name": "target-" + logical, "interactive_ready": True,
                "agent_status": "idle", "revision": 1,
                "state_change_seq": 2,
            }
            for logical in readiness_module.REQUIRED_LOGICAL_ROLES
        }
        state = self.assert_gate_matches_evaluate(probe)
        self.assertEqual(state, readiness_module.BOOTSTRAP_READY)

    def test_none_is_unobservable_not_ready(self):
        state = self.assert_gate_matches_evaluate(None)
        self.assertEqual(state, readiness_module.BOOTSTRAP_UNOBSERVABLE)

    def test_a_raising_probe_is_not_converted_into_ready(self):
        def raising(path):
            raise OSError("no registry")

        state = self.assert_gate_matches_evaluate(raising)
        self.assertNotEqual(state, readiness_module.BOOTSTRAP_READY)
        self.assertEqual(state, readiness_module.BOOTSTRAP_UNOBSERVABLE)

    def test_no_readiness_field_implies_authority(self):
        for name in vars(Worker):
            self.assertNotIn("authoriz", name.lower(), name)
            self.assertNotIn("grant", name.lower(), name)
        self.assertEqual(
            set(readiness_module.BOOTSTRAP_STATES),
            {readiness_module.BOOTSTRAP_WAITING,
             readiness_module.BOOTSTRAP_READY,
             readiness_module.BOOTSTRAP_UNOBSERVABLE,
             readiness_module.BOOTSTRAP_FAILED},
        )


class FakeWorkerReleaseTests(FakeWorkerCase):

    def terminal(self):
        self.real_lease()
        for action in (broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform("wf-0001", action).ok)
        workflows = self.fresh_workflows()
        wa_record.apply_transition(
            workflows["workflows"]["wf-0001"], wa_record.PHASE_BLOCKED
        )
        self.write_raw(workflows)
        return self.disk_entry()

    def test_revoke_and_relinquish_are_reached_only_through_the_fake(self):
        entry = self.terminal()
        lease = entry["workspace_lease"]["path_realpath"]
        fake = FakeWorker(
            revoke_workspace_trust=(True, None, None),
            relinquish_workspace=(True, None, None),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0001",
                broker_module.ACTION_RELEASE,
            )
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        names = [call[0] for call in fake.calls]
        self.assertEqual(
            names, ["revoke_workspace_trust", "relinquish_workspace"],
            "trust is revoked before the directory is relinquished, and"
            " an unconfigured Domain B calls no projection and no close",
        )
        self.assertEqual(fake.calls[1][2], NOW)
        # The fake removed nothing and the real release was tripwired:
        # the directory's survival is the proof that nothing beside the
        # seam deletes it.
        self.assertTrue(os.path.isdir(lease))

    def test_a_relinquish_refusal_passes_through_unchanged(self):
        self.terminal()
        fake = FakeWorker(
            revoke_workspace_trust=(True, None, None),
            relinquish_workspace=(
                False, workspace_module.PROBLEM_LEASE_MISSING, "gone"
            ),
        )
        with host_tripwires(self):
            outcome = self.perform_with(
                self.fake_broker(fake), "wf-0001",
                broker_module.ACTION_RELEASE,
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(
            (outcome.problem, outcome.detail),
            (workspace_module.PROBLEM_LEASE_MISSING, "gone"),
        )

    def test_the_module_release_is_still_the_substitution_point(self):
        # Call-time resolution: replacing `workspace.release` on the
        # module is picked up by the default adapter, so the existing
        # ordering pins that patch it keep reaching the production path.
        self.terminal()
        seen = []
        real = workspace_module.release

        def watching(*args, **kwargs):
            seen.append(args)
            return real(*args, **kwargs)

        with patch.object(workspace_module, "release", watching):
            outcome = self.perform("wf-0001", broker_module.ACTION_RELEASE)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(len(seen), 1)


# ---------------------------------------------------------------------
# 5. Identity separation.
# ---------------------------------------------------------------------

class IdentitySeparationTests(FakeWorkerCase):
    """Worker identity is not a proof input. The seam carries none,
    the proof functions accept none, and a fake worker's answers can
    only be compared against the record's own recorded identifiers,
    never substituted for them."""

    def test_proof_functions_take_no_worker(self):
        for function, expected in (
            (ws_module.prove_ownership,
             ["entry", "child_records", "live_workspaces",
              "workspaces_root"]),
            (ws_module.close_proven_workspace,
             ["snapshot", "live_now", "close_fn", "child_records",
              "entry", "workspaces_root"]),
            (ownership_module.owns_workspace,
             ["entry", "path", "workspaces_root"]),
        ):
            params = list(inspect.signature(function).parameters)
            self.assertEqual(params, expected, function.__name__)
            self.assertNotIn("worker", params)

    def test_broker_seam_calls_pass_only_the_record_and_the_clock(self):
        tree = ast.parse(BROKER_PATH.read_text())
        seen = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "worker"
                    and node.func.attr in SEAM_METHODS):
                continue
            self.assertEqual(node.keywords, [], node.func.attr)
            shapes = []
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    shapes.append(arg.id)
                elif isinstance(arg, ast.Call):
                    shapes.append(ast.unparse(arg))
                elif isinstance(arg, ast.Subscript):
                    shapes.append(ast.unparse(arg))
                else:
                    shapes.append(ast.dump(arg))
            seen.setdefault(node.func.attr, []).append(shapes)
        self.assertEqual(seen, {
            "materialize_workspace": [["entry", "self._clock()"]],
            "verify_workspace": [["entry"]] * 5,
            "establish_workspace_trust": [["entry"]],
            "workspace_trust_consumable": [["entry"]],
            "revoke_workspace_trust": [["entry"]],
            "relinquish_workspace": [["entry", "self._clock()"]],
            "probe_readiness": [[
                "entry['workspace_lease']['path_realpath']"
            ]],
            "live_workspaces": [[]] * 3,
        })

    def _bound_terminal(self, task_id="20260828-114612-5d92e1"):
        self.real_lease()
        for action in (broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            self.assertTrue(self.perform("wf-0001", action).ok)
        workflows = self.fresh_workflows()
        entry = workflows["workflows"]["wf-0001"]
        wa_record.apply_transition(entry, wa_record.PHASE_BLOCKED)
        entry["target_engine"] = {
            "alias": "di-wf-0001", "task_id": task_id, "repo": "u",
            "dispatched_at": NOW,
        }
        self.write_raw(workflows)
        entry = self.disk_entry()
        self.spawn_record_overrides.update({"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": entry["workspace_lease"]["path_realpath"],
            "task_id": task_id, "workspace_id": "wTEST",
            "agents": {"supervisor": "a-sup", "lead1": "a-lead"},
        }]})
        return entry

    def _domain_b(self, fake):
        broker = self.fake_broker(fake)
        entry = self.disk_entry()
        report = ownership_module.CleanupReport()
        with host_tripwires(self):
            snapshot = broker_module._domain_b_proof(broker, entry)
            result = broker_module._domain_b_release(
                broker, entry, report, snapshot=snapshot
            )
        return result, report

    def test_a_projection_that_disagrees_with_the_record_cannot_close(self):
        self._bound_terminal()
        fake = FakeWorker(
            observes=True, closes=True,
            live_workspaces=[{"workspace_id": "wTEST",
                              "agent_names": {"a-sup", "somebody-else"}}],
            close_workspace=True,
        )
        result, report = self._domain_b(fake)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(_calls_named(fake, "close_workspace"), [])
        self.assertTrue(report.unprovable)
        self.assertIn(ws_module.PROBLEM_AGENTS_DISAGREE,
                      report.unprovable[-1][2])

    def test_a_projection_naming_another_workspace_cannot_redirect_the_close(self):
        self._bound_terminal()
        fake = FakeWorker(
            observes=True, closes=True,
            live_workspaces=[{"workspace_id": "wOTHER",
                              "agent_names": {"a-sup", "a-lead"}}],
            close_workspace=True,
        )
        result, report = self._domain_b(fake)
        self.assertEqual(_calls_named(fake, "close_workspace"), [])
        self.assertTrue(report.unprovable)
        self.assertIn(ws_module.PROBLEM_WORKSPACE_NOT_FOUND,
                      report.unprovable[-1][2])
        # Positive evidence that nothing of ours is live: reclaimed,
        # and still no close of the workspace the worker DID name.
        self.assertEqual(result, broker_module.SESSIONS_RECLAIMED)

    def test_an_exact_and_unique_proof_closes_the_recorded_id_only(self):
        self._bound_terminal()
        fake = FakeWorker(
            observes=True, closes=True,
            live_workspaces=[{"workspace_id": "wTEST",
                              "agent_names": {"a-sup", "a-lead"}}],
            close_workspace=True,
        )
        result, report = self._domain_b(fake)
        self.assertEqual(result, broker_module.SESSIONS_RECLAIMED)
        self.assertEqual(
            _calls_named(fake, "close_workspace"),
            [("close_workspace", "wTEST")],
        )
        self.assertEqual(report.removed, [("workspace_session", "wTEST")])
        # The projection was re-read immediately before the close.
        self.assertEqual(len(_calls_named(fake, "live_workspaces")), 2)

    def test_a_proof_gone_stale_before_the_close_refuses(self):
        self._bound_terminal()
        answers = iter([
            [{"workspace_id": "wTEST", "agent_names": {"a-sup", "a-lead"}}],
            [{"workspace_id": "wTEST", "agent_names": {"a-sup"}}],
        ])
        fake = FakeWorker(
            observes=True, closes=True,
            live_workspaces=lambda: next(answers),
            close_workspace=True,
        )
        result, report = self._domain_b(fake)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(_calls_named(fake, "close_workspace"), [])
        self.assertIn(ws_module.PROBLEM_STALE_PROOF,
                      report.unprovable[-1][2])

    def test_presence_flags_grant_nothing_without_a_bound_task(self):
        # Wired to observe and close, but the record binds no task id:
        # no proof, no close, and no verdict the worker can supply.
        self.real_lease()
        fake = FakeWorker(
            observes=True, closes=True,
            live_workspaces=[{"workspace_id": "wTEST",
                              "agent_names": set()}],
            close_workspace=True,
        )
        result, report = self._domain_b(fake)
        self.assertEqual(result, broker_module.SESSIONS_RETAINED)
        self.assertEqual(_calls_named(fake, "close_workspace"), [])
        self.assertIn(ws_module.PROBLEM_EVIDENCE_DEGRADED,
                      report.unprovable[-1][2])


# The executed hermetic-git sweep runs a swept module with
# `runpy.run_path(..., run_name="__main__")`. Within that runner, a
# module lacking this block imports and exits, so the shim observes no
# git process and the sweep reports having no observation to assert on.
if __name__ == "__main__":
    unittest.main()
