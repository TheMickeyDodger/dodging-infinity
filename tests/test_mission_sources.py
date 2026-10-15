"""Focused tests for ``mission_sources`` (DI mission source registry, the
versioned query contract, read-only source readers and roster semantics).

Isolation: every store, registry and snapshot here lives in a private
temporary directory or is a literal value; no production path is read or
written and nothing is dispatched. Termination (CONTRIBUTING.md rule):
every test class derives from ``Bounded``, a SIGALRM watchdog independent
of the code under test whose expiry cannot be swallowed by it, so a hung
lock wait, read or loop fails fast. Once it fires it RE-ARMS every
``WATCHDOG_REARM_SECONDS``: an expiry that something swallows (a ``subTest``
block catches BaseException and carries on) or that lands before a cleanup
that then blocks is raised AGAIN, so neither leaves the test unbounded.
``WatchdogSelfCheckTests`` proves both properties in ISOLATED CHILD
processes under an external ``subprocess.run`` timeout. Stated limit: an
in-process signal reaches only code that returns to the interpreter; a block
that never does (an uninterruptible kernel read in the main thread) is ended
only by an external bound.
"""

import collections
import copy
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import surface as surface_module  # noqa: E402
from mission import progress as mission_progress  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import state as mission_state  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import fileread  # noqa: E402
from mission_sources import query as query_module  # noqa: E402
from mission_sources import registry as registry_module  # noqa: E402
from mission_sources import roster  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from test_local_request import Clock, request as mission_request  # noqa: E402
from test_workflow_authority import build_relay_record, make_record  # noqa: E402
from workflow_authority import atomic as workflow_atomic  # noqa: E402
from workflow_authority import record as workflow_record  # noqa: E402
from workflow_authority import store as workflow_store  # noqa: E402

# Read-only subset: mission_sources/readiness.py is NOT landed in this
# checkout (it reports Mission state through observe_resource_readiness and
# nothing on the catalog path imports it), so the readiness tests are SKIPPED
# by name, never satisfied by copying the module.
try:
    from mission_sources import readiness as readiness_module  # noqa: E402
except ImportError:
    readiness_module = None
NEEDS_READINESS = unittest.skipIf(
    readiness_module is None,
    "mission_sources.readiness is not landed in the read-only subset")

WATCHDOG_SECONDS = 60
#: Once fired, the watchdog re-arms itself at this period until the test's
#: own cleanup disarms it.
WATCHDOG_REARM_SECONDS = 1
NOW = 1_800_000_000


class WatchdogExpired(BaseException):
    """Raised by the watchdog. Deliberately NOT an ``Exception`` (and so
    not an ``OSError`` like the built-in ``TimeoutError``): code under test
    that handles ``OSError`` or ``Exception`` cannot consume it."""


class Bounded(unittest.TestCase):
    """Independent termination bound (CONTRIBUTING.md termination rule): a
    SIGALRM watchdog that raises ``WatchdogExpired`` inside a hung lock wait,
    read or loop. Its verdict does not depend on the code under test: the
    expiry is also RECORDED, and a cleanup fails the test whenever the alarm
    fired, even if something swallowed the exception and the test then
    "passed". Once fired it RE-ARMS every ``WATCHDOG_REARM_SECONDS`` until
    that cleanup disarms it, so a swallowed expiry (``subTest``) or a cleanup
    that blocks (a one-shot owner's ``settle(None)``) is interrupted again
    (proved by ``WatchdogSelfCheckTests`` and, with owned work outstanding,
    by ``test_runtime_snapshot``)."""

    watchdog_seconds = WATCHDOG_SECONDS

    def setUp(self):
        self._watchdog_fired = False

        def expired(signum, frame):
            self._watchdog_fired = True
            signal.alarm(WATCHDOG_REARM_SECONDS)
            raise WatchdogExpired("watchdog: test exceeded %d s"
                                  % self.watchdog_seconds)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(self.watchdog_seconds)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        self.addCleanup(self._fail_if_watchdog_fired)

    def _fail_if_watchdog_fired(self):
        if self._watchdog_fired:
            raise AssertionError("watchdog fired after %d s: the test is a"
                                 " failure even if the expiry was swallowed"
                                 % self.watchdog_seconds)


#: Regression (a) runs in an ISOLATED CHILD: one inner test whose watchdog
#: expires inside a ``subTest`` block (which swallows the expiry) and which
#: then enters ANOTHER block. ``fixed`` uses ``Bounded``; ``one_shot`` is the
#: pre-fix one-shot handler reproduced verbatim, as the control.
SUBTEST_EXPIRY_CHILD = r'''
import json, os, signal, sys, threading, time, unittest
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(sys.argv[1], "tests"))
import test_mission_sources as base
BLOCK = float(sys.argv[3])
if sys.argv[2] == "fixed":
    Parent = base.Bounded
else:
    class Parent(unittest.TestCase):
        watchdog_seconds = base.WATCHDOG_SECONDS
        def setUp(self):
            self._watchdog_fired = False
            def expired(signum, frame):
                self._watchdog_fired = True
                raise base.WatchdogExpired("watchdog: test exceeded %d s"
                                           % self.watchdog_seconds)
            previous = signal.signal(signal.SIGALRM, expired)
            signal.alarm(self.watchdog_seconds)
            self.addCleanup(signal.signal, signal.SIGALRM, previous)
            self.addCleanup(signal.alarm, 0)
class Inner(Parent):
    watchdog_seconds = 1
    def test_expiry_in_subtest_then_another_block(self):
        with self.subTest("the first block"):
            threading.Event().wait(BLOCK)
        threading.Event().wait(BLOCK)
result = unittest.TestResult()
started = time.monotonic()
Inner("test_expiry_in_subtest_then_another_block").run(result)
elapsed = time.monotonic() - started
signal.alarm(0)
print(json.dumps({"elapsed": elapsed, "problems": [
    text for _test, text in result.errors + result.failures]}))
'''


class WatchdogSelfCheckTests(unittest.TestCase):
    """The watchdog cannot be consumed by the code under test. This class's
    own termination is bounded by ``time.monotonic`` deadlines inside the
    inner tests, independent of the watchdog it checks."""

    def test_expiry_fails_the_test_even_when_swallowed(self):
        class Inner(Bounded):
            watchdog_seconds = 1

            def test_swallows_everything(self):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    try:
                        time.sleep(0.05)
                    except BaseException:
                        pass

            def test_handles_os_error_like_fileread(self):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    try:
                        time.sleep(0.05)
                    except OSError:
                        return

            def test_quick_test_is_unaffected(self):
                self.assertTrue(True)

        result = unittest.TestResult()
        unittest.TestSuite([
            Inner("test_swallows_everything"),
            Inner("test_handles_os_error_like_fileread"),
            Inner("test_quick_test_is_unaffected"),
        ]).run(result)
        failed = {t.id().rsplit(".", 1)[-1]
                  for t, _ in result.failures + result.errors}
        self.assertEqual(failed, {"test_swallows_everything",
                                  "test_handles_os_error_like_fileread"})
        self.assertTrue(issubclass(WatchdogExpired, BaseException))
        self.assertFalse(issubclass(WatchdogExpired, Exception))

    def test_a_swallowed_expiry_cannot_unbound_the_next_block(self):
        """Regression (a), EXTERNALLY bounded (each child under
        ``subprocess.run(timeout=60)``): the expiry lands in a ``subTest``
        block, which swallows it; the test then blocks AGAIN. The re-armed
        watchdog ends that second block a second later; the pre-fix one-shot
        watchdog (the control) leaves it to run out its own time. Owned
        processes: none exist in this case but the child, which
        ``subprocess.run`` waits for and reaps."""
        block = 6.0
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "subtest-expiry-child.py")
            with open(script, "w") as handle:
                handle.write(SUBTEST_EXPIRY_CHILD)
            runs = {}
            for mode in ("fixed", "one_shot"):
                done = subprocess.run(
                    [sys.executable, "-I", "-B", script, str(REPO_ROOT), mode,
                     str(block)], stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                runs[mode] = json.loads(
                    done.stdout.decode().strip().splitlines()[-1])
        fixed, control = runs["fixed"], runs["one_shot"]
        # Fixed: the second block was interrupted by the re-armed alarm.
        self.assertLess(fixed["elapsed"], block - 1)
        self.assertTrue(any("WatchdogExpired" in text
                            for text in fixed["problems"]), fixed)
        # Control: the swallowed one-shot expiry left the second block
        # unbounded by the watchdog (it ran its own full time).
        self.assertGreaterEqual(control["elapsed"], block)


def binding(logical="executor1", role="executor", agent="pod-exec1",
            session="sess-exec-1", pane_id="w1:p3", workspace_id="w1"):
    return {"logical": logical, "role": role, "agent": agent,
            "session": session, "pane_id": pane_id,
            "workspace_id": workspace_id}


def live_agent(agent="pod-exec1", provider="claude", status="working",
               session="sess-exec-1", pane_id="w1:p3", workspace_id="w1"):
    return {"agent": agent, "provider": provider, "status": status,
            "session": session, "pane_id": pane_id,
            "workspace_id": workspace_id}


def snapshot(observed_at=NOW, tasks=None, agents=None, producer="fixture"):
    return {
        "schema": contract.SNAPSHOT_SCHEMA,
        "version": contract.SNAPSHOT_VERSION,
        "observed_at": observed_at,
        "producer": producer,
        "tasks": tasks if tasks is not None else [{
            "task_id": "20261014-000000-aaaaaa",
            "workspace_realpath": "/fixture/ws/mn-1",
            "bindings": [binding()],
        }],
        "agents": agents if agents is not None else [live_agent()],
    }


# -- contract -------------------------------------------------------------

class SnapshotReadingTests(Bounded):

    def assertDegraded(self, reading, health, problem=None):
        self.assertEqual(reading.health, health)
        if problem is not None:
            self.assertEqual(reading.problem, problem)
        self.assertFalse(reading.is_live())
        self.assertEqual(reading.tasks, ())
        self.assertEqual(reading.agents, ())

    def test_absent_snapshot_is_unavailable_never_live(self):
        self.assertDegraded(contract.read_runtime_snapshot(None, NOW),
                            contract.HEALTH_UNAVAILABLE,
                            contract.PROBLEM_SNAPSHOT_ABSENT)

    def test_non_object_snapshot_is_malformed(self):
        for value in ([], "snapshot", 7, True):
            self.assertDegraded(contract.read_runtime_snapshot(value, NOW),
                                contract.HEALTH_MALFORMED,
                                contract.PROBLEM_SNAPSHOT_SHAPE)

    def test_other_schema_or_version_is_incompatible(self):
        cases = []
        other_schema = snapshot()
        other_schema["schema"] = "some.other.schema"
        cases.append(other_schema)
        for version in (2, 0, True, "1", None):
            value = snapshot()
            value["version"] = version
            cases.append(value)
        missing = snapshot()
        del missing["version"]
        cases.append(missing)
        for value in cases:
            self.assertDegraded(contract.read_runtime_snapshot(value, NOW),
                                contract.HEALTH_INCOMPATIBLE,
                                contract.PROBLEM_SNAPSHOT_SCHEMA)

    def test_closed_keys_at_every_level(self):
        mutations = [
            lambda s: s.pop("agents"),
            lambda s: s.update(extra=1),
            lambda s: s["tasks"][0].pop("workspace_realpath"),
            lambda s: s["tasks"][0].update(status="RUNNING"),
            lambda s: s["tasks"][0]["bindings"][0].pop("session"),
            lambda s: s["tasks"][0]["bindings"][0].update(model="x"),
            lambda s: s["agents"][0].pop("status"),
            lambda s: s["agents"][0].update(cost=0),
        ]
        for mutate in mutations:
            value = snapshot()
            mutate(value)
            self.assertDegraded(contract.read_runtime_snapshot(value, NOW),
                                contract.HEALTH_MALFORMED)

    def test_exact_types_refuse_never_repair(self):
        mutations = [
            lambda s: s.update(observed_at=True),
            lambda s: s.update(observed_at=float(NOW)),
            lambda s: s.update(observed_at=-1),
            lambda s: s.update(producer=""),
            lambda s: s.update(producer=None),
            lambda s: s.update(tasks={}),
            lambda s: s["tasks"][0].update(task_id=None),
            lambda s: s["tasks"][0].update(task_id=""),
            lambda s: s["tasks"][0].update(task_id="a\x00b"),
            lambda s: s["tasks"][0]["bindings"][0].update(agent=None),
            lambda s: s["tasks"][0]["bindings"][0].update(role=7),
            lambda s: s["agents"][0].update(status=None),
            lambda s: s["agents"][0].update(provider=""),
        ]
        for mutate in mutations:
            value = snapshot()
            mutate(value)
            self.assertDegraded(contract.read_runtime_snapshot(value, NOW),
                                contract.HEALTH_MALFORMED)

    def test_bounds_are_refused_not_truncated(self):
        too_many_tasks = snapshot(tasks=[{
            "task_id": "t%d" % i, "workspace_realpath": "/w/%d" % i,
            "bindings": []} for i in range(contract.MAX_SNAPSHOT_TASKS + 1)])
        too_many_agents = snapshot(agents=[
            live_agent(agent="a%d" % i)
            for i in range(contract.MAX_SNAPSHOT_AGENTS + 1)])
        too_many_bindings = snapshot(tasks=[{
            "task_id": "t", "workspace_realpath": "/w",
            "bindings": [binding(logical="l%d" % i)
                         for i in range(contract.MAX_TASK_BINDINGS + 1)]}])
        too_long = snapshot()
        too_long["producer"] = "p" * (contract.MAX_SNAPSHOT_TEXT_CHARS + 1)
        for value in (too_many_tasks, too_many_agents, too_many_bindings,
                      too_long):
            self.assertDegraded(contract.read_runtime_snapshot(value, NOW),
                                contract.HEALTH_MALFORMED)
        at_bound = snapshot()
        at_bound["producer"] = "p" * contract.MAX_SNAPSHOT_TEXT_CHARS
        self.assertTrue(contract.read_runtime_snapshot(at_bound, NOW).is_live())

    def test_future_observation_is_malformed_not_recent(self):
        reading = contract.read_runtime_snapshot(snapshot(NOW + 1), NOW)
        self.assertEqual(reading.health, contract.HEALTH_MALFORMED)
        self.assertEqual(reading.problem, contract.PROBLEM_OBSERVED_IN_FUTURE)
        self.assertEqual(reading.tasks, ())

    def test_stale_exactly_past_the_bound(self):
        bound = contract.DEFAULT_MAX_AGE_SECONDS
        fresh = contract.read_runtime_snapshot(snapshot(NOW - bound), NOW)
        stale = contract.read_runtime_snapshot(snapshot(NOW - bound - 1), NOW)
        self.assertTrue(fresh.is_live())
        self.assertEqual(stale.health, contract.HEALTH_STALE)
        self.assertEqual(stale.problem, contract.PROBLEM_OBSERVED_TOO_OLD)
        self.assertFalse(stale.is_live())
        # A stale reading keeps its parsed facts for inspection only.
        self.assertEqual(len(stale.tasks), 1)
        tight = contract.read_runtime_snapshot(snapshot(NOW - 5), NOW,
                                               max_age_seconds=4)
        self.assertEqual(tight.health, contract.HEALTH_STALE)

    def test_fresh_reading_preserves_values_and_unknowns(self):
        value = snapshot(tasks=[{
            "task_id": "t1", "workspace_realpath": "/w/1",
            "bindings": [binding(role=None, session=None, pane_id=None,
                                 workspace_id=None)]}],
            agents=[live_agent(provider=None, session=None)])
        reading = contract.read_runtime_snapshot(value, NOW)
        self.assertTrue(reading.is_live())
        self.assertEqual(reading.observed_at, NOW)
        self.assertEqual(reading.producer, "fixture")
        b = reading.tasks[0].bindings[0]
        self.assertIsNone(b.role)
        self.assertIsNone(b.session)
        self.assertIsNone(reading.agents[0].provider)
        self.assertEqual(reading.describe()["health"], contract.HEALTH_FRESH)

    def test_duplicates_are_kept_for_ambiguity_not_resolved(self):
        task = {"task_id": "t1", "workspace_realpath": "/w/1",
                "bindings": [binding(), binding()]}
        value = snapshot(tasks=[task, copy.deepcopy(task)],
                         agents=[live_agent(), live_agent()])
        reading = contract.read_runtime_snapshot(value, NOW)
        self.assertEqual(len(reading.tasks), 2)
        self.assertEqual(len(reading.tasks[0].bindings), 2)
        self.assertEqual(len(reading.agents), 2)

    def test_max_age_and_now_are_validated(self):
        for bad in (0, contract.MAX_MAX_AGE_SECONDS + 1, True, 1.5):
            with self.assertRaises(contract.ContractError):
                contract.read_runtime_snapshot(snapshot(), NOW, bad)
        with self.assertRaises(contract.ContractError):
            contract.read_runtime_snapshot(snapshot(), True)


class FreshnessTests(Bounded):

    def test_freshness_edges(self):
        self.assertEqual(contract.freshness(NOW, NOW, 60),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(contract.freshness(NOW - 60, NOW, 60),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(contract.freshness(NOW - 61, NOW, 60)[0],
                         contract.HEALTH_STALE)
        self.assertEqual(contract.freshness(NOW + 1, NOW, 60)[0],
                         contract.HEALTH_MALFORMED)
        for bad in (None, True, "1", -1):
            self.assertEqual(contract.freshness(bad, NOW, 60)[0],
                             contract.HEALTH_MALFORMED)


class ResponseContractTests(Bounded):

    def response(self, generated_at=NOW, max_age=60, valid_until=None):
        return contract.envelope(
            generated_at, max_age,
            generated_at + max_age if valid_until is None else valid_until,
            {}, {"health": "fresh"}, [], {}, [], 0)

    def test_every_response_carries_its_contract_version(self):
        r = self.response()
        self.assertEqual(r["contract"], contract.QUERY_CONTRACT)
        self.assertEqual(r["contract_version"], contract.QUERY_CONTRACT_VERSION)
        self.assertEqual(set(r), set(contract.RESPONSE_KEYS))

    def test_consumer_freshness(self):
        self.assertEqual(contract.response_freshness(self.response(), NOW + 60),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(
            contract.response_freshness(self.response(), NOW + 61)[0],
            contract.HEALTH_STALE)
        self.assertEqual(
            contract.response_freshness(self.response(NOW + 5), NOW)[0],
            contract.HEALTH_MALFORMED)

    def test_unknown_contract_or_version_is_incompatible(self):
        for key, value in (("contract", "other"), ("contract_version", 2),
                           ("contract_version", True)):
            r = self.response()
            r[key] = value
            self.assertEqual(contract.response_freshness(r, NOW),
                             (contract.HEALTH_INCOMPATIBLE,
                              contract.PROBLEM_RESPONSE_CONTRACT))

    def test_wrong_shape_is_malformed(self):
        r = self.response()
        r["extra"] = 1
        self.assertEqual(contract.response_freshness(r, NOW)[0],
                         contract.HEALTH_MALFORMED)
        r = self.response()
        r["max_age_seconds"] = 0
        self.assertEqual(contract.response_freshness(r, NOW)[0],
                         contract.HEALTH_MALFORMED)
        self.assertEqual(contract.response_freshness([], NOW)[0],
                         contract.HEALTH_MALFORMED)


# -- registry -------------------------------------------------------------

class RegistryFixture(Bounded):

    def setUp(self):
        super(RegistryFixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.directory = os.path.join(self.root, "registry")
        self.registry = registry_module.SourceRegistry(self.directory)

    def store_dir(self, name):
        path = os.path.join(self.root, "stores", name)
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path

    def register(self, source_id="mc-local", kind=contract.KIND_MISSION_CORE,
                 store=None, producer="local_request", max_age=60, now=NOW):
        return self.registry.register(
            source_id, kind, store or self.store_dir(source_id), producer,
            max_age, now)


class RegistryTests(RegistryFixture):

    def test_load_of_absent_registry_is_empty_and_touches_nothing(self):
        self.assertEqual(self.registry.load(),
                         registry_module.default_document())
        self.assertFalse(os.path.exists(self.directory))

    def test_register_persists_private_inspectable_data(self):
        outcome = self.register()
        self.assertEqual(outcome["outcome"], registry_module.OUTCOME_REGISTERED)
        mode = stat.S_IMODE(os.stat(self.registry.path).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.registry.path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk[registry_module.REGISTRY_VERSION_KEY], 1)
        self.assertEqual(on_disk["sources"][0]["source_id"], "mc-local")
        self.assertEqual(on_disk["sources"][0]["producer"], "local_request")
        self.assertEqual(self.registry.sources(), on_disk["sources"])

    def test_identical_reregistration_is_idempotent(self):
        store = self.store_dir("mc")
        first = self.register(store=store, now=NOW)
        with open(self.registry.path, "rb") as handle:
            before = handle.read()
        again = self.register(store=store, now=NOW + 100)
        self.assertEqual(again["outcome"], registry_module.OUTCOME_UNCHANGED)
        self.assertEqual(again["source"], first["source"])
        with open(self.registry.path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_conflicting_id_is_refused(self):
        store = self.store_dir("mc")
        self.register(store=store)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.register(store=store, producer="grok_bot")
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_SOURCE_ID_CONFLICT)

    def test_same_store_cannot_be_registered_twice_even_via_symlink(self):
        store = self.store_dir("mc")
        self.register(source_id="mc-a", store=store)
        alias = os.path.join(self.root, "alias")
        os.symlink(store, alias)
        for path in (store, alias):
            with self.assertRaises(registry_module.RegistryError) as caught:
                self.register(source_id="mc-b", store=path)
            self.assertEqual(caught.exception.problem,
                             registry_module.PROBLEM_STORE_ALREADY_REGISTERED)
        # The same directory as a DIFFERENT kind is a different store.
        self.register(source_id="wf-a", kind=contract.KIND_WORKFLOW_STORE,
                      store=store)

    def test_invalid_sources_are_refused(self):
        good = self.store_dir("good")
        bad_calls = [
            dict(source_id="Upper"), dict(source_id="-lead"),
            dict(source_id="x" * (registry_module.MAX_SOURCE_ID_CHARS + 1)),
            dict(source_id=7), dict(kind="other_kind"),
            dict(store="relative/dir"), dict(store=good + "/../good"),
            dict(store=good + "/"), dict(producer=""), dict(producer=None),
            dict(producer="p" * (registry_module.MAX_PRODUCER_CHARS + 1)),
            dict(max_age=0), dict(max_age=contract.MAX_MAX_AGE_SECONDS + 1),
            dict(max_age=True), dict(now=True), dict(now=-1),
        ]
        for kwargs in bad_calls:
            kwargs.setdefault("store", good)
            with self.assertRaises(registry_module.RegistryError) as caught:
                self.register(**kwargs)
            self.assertEqual(caught.exception.problem,
                             registry_module.PROBLEM_SOURCE_INVALID, kwargs)
        self.assertFalse(os.path.exists(self.registry.path))

    def test_bound_on_registered_sources(self):
        for i in range(registry_module.MAX_REGISTERED_SOURCES):
            self.register(source_id="mc-%02d" % i)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.register(source_id="mc-over")
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_FULL)

    def test_unregister(self):
        self.register()
        self.assertEqual(self.registry.unregister("mc-local")["outcome"],
                         registry_module.OUTCOME_REMOVED)
        self.assertEqual(self.registry.sources(), [])
        self.assertEqual(self.registry.unregister("mc-local")["outcome"],
                         registry_module.OUTCOME_ABSENT)

    def write_raw(self, document, mode=0o600):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        with open(self.registry.path, "w", encoding="utf-8") as handle:
            if isinstance(document, str):
                handle.write(document)
            else:
                json.dump(document, handle)
        os.chmod(self.registry.path, mode)

    def test_tampered_registry_is_refused_with_distinct_problems(self):
        self.register()
        valid = self.registry.load()
        cases = [
            ("{not json", registry_module.PROBLEM_REGISTRY_MALFORMED),
            ([], registry_module.PROBLEM_REGISTRY_MALFORMED),
            (dict(valid, **{registry_module.REGISTRY_VERSION_KEY: 2}),
             registry_module.PROBLEM_REGISTRY_INCOMPATIBLE),
            (dict(valid, extra=1), registry_module.PROBLEM_REGISTRY_MALFORMED),
            (dict(valid, sources=valid["sources"] * 2),
             registry_module.PROBLEM_REGISTRY_MALFORMED),
            (dict(valid, sources=[dict(valid["sources"][0], kind="x")]),
             registry_module.PROBLEM_REGISTRY_MALFORMED),
        ]
        for document, problem in cases:
            self.write_raw(document)
            with self.assertRaises(registry_module.RegistryError) as caught:
                self.registry.load()
            self.assertEqual(caught.exception.problem, problem, document)

    def test_open_permissions_are_refused(self):
        self.register()
        os.chmod(self.registry.path, 0o644)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.registry.load()
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_PERMISSIONS)

    def test_registry_directory_must_be_absolute(self):
        with self.assertRaises(registry_module.RegistryError):
            registry_module.SourceRegistry("relative/registry")


# -- bounded, no-follow file reads ----------------------------------------

class FileReadTests(Bounded):

    def setUp(self):
        super(FileReadTests, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def private(self, name, data=b"{}", mode=0o600):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        os.chmod(path, mode)
        return path

    def refused(self, path, max_bytes=1024):
        with self.assertRaises(fileread.FileRefused) as caught:
            fileread.read_private_file(path, max_bytes)
        return caught.exception.problem

    def test_regular_private_file_is_read_exactly(self):
        path = self.private("ok.json", b'{"a": 1}')
        self.assertEqual(fileread.read_private_file(path, 1024), b'{"a": 1}')

    def test_only_a_nonexistent_path_is_absent(self):
        self.assertEqual(self.refused(os.path.join(self.root, "none")),
                         fileread.PROBLEM_FILE_ABSENT)
        dangling = os.path.join(self.root, "dangling")
        os.symlink(os.path.join(self.root, "none"), dangling)
        self.assertEqual(self.refused(dangling), fileread.PROBLEM_FILE_SYMLINK)

    def test_symlink_to_a_valid_file_is_refused(self):
        target = self.private("target.json")
        link = os.path.join(self.root, "link.json")
        os.symlink(target, link)
        self.assertEqual(self.refused(link), fileread.PROBLEM_FILE_SYMLINK)

    def test_fifo_is_refused_without_blocking(self):
        fifo = os.path.join(self.root, "fifo")
        os.mkfifo(fifo, 0o600)
        self.assertEqual(self.refused(fifo), fileread.PROBLEM_FILE_NOT_REGULAR)

    def test_directory_is_refused(self):
        os.mkdir(os.path.join(self.root, "dir"))
        self.assertEqual(self.refused(os.path.join(self.root, "dir")),
                         fileread.PROBLEM_FILE_NOT_REGULAR)

    def test_byte_bound_is_refused_not_truncated(self):
        path = self.private("big", b"x" * 1025)
        self.assertEqual(self.refused(path, 1024),
                         fileread.PROBLEM_FILE_TOO_LARGE)
        self.assertEqual(fileread.read_private_file(path, 1025), b"x" * 1025)

    def test_open_permissions_are_refused(self):
        path = self.private("open", mode=0o644)
        self.assertEqual(self.refused(path),
                         fileread.PROBLEM_FILE_PERMISSIONS_OPEN)

    def test_file_swapped_after_lstat_is_unreadable_not_absent(self):
        path = self.private("swapped")
        other = self.private("other")
        real_lstat = os.lstat
        with mock.patch.object(fileread.os, "lstat",
                               side_effect=lambda p: real_lstat(other)):
            self.assertEqual(self.refused(path),
                             fileread.PROBLEM_FILE_UNREADABLE)

    def test_fifo_raced_in_after_lstat_does_not_block(self):
        regular = self.private("regular")
        fifo = os.path.join(self.root, "raced-fifo")
        os.mkfifo(fifo, 0o600)
        real_lstat = os.lstat
        with mock.patch.object(fileread.os, "lstat",
                               side_effect=lambda p: real_lstat(regular)):
            self.assertIn(self.refused(fifo), (
                fileread.PROBLEM_FILE_NOT_REGULAR,
                fileread.PROBLEM_FILE_UNREADABLE))

    def test_file_removed_after_lstat_is_unreadable_not_absent(self):
        path = self.private("vanishing")
        real_lstat = os.lstat
        def lstat_then_remove(p):
            result = real_lstat(p)
            os.remove(p)
            return result
        with mock.patch.object(fileread.os, "lstat",
                               side_effect=lstat_then_remove):
            self.assertEqual(self.refused(path),
                             fileread.PROBLEM_FILE_UNREADABLE)


class RegistryHardeningTests(RegistryFixture):

    def test_hostile_registry_files_never_read_as_empty(self):
        os.makedirs(self.directory, mode=0o700)
        target = os.path.join(self.root, "elsewhere.json")
        with open(target, "w") as handle:
            json.dump(registry_module.default_document(), handle)
        os.chmod(target, 0o600)
        os.symlink(target, self.registry.path)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.registry.load()
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_UNREADABLE)
        os.remove(self.registry.path)
        os.symlink(os.path.join(self.root, "missing"), self.registry.path)
        with self.assertRaises(registry_module.RegistryError):
            self.registry.load()
        os.remove(self.registry.path)
        os.mkfifo(self.registry.path, 0o600)
        with self.assertRaises(registry_module.RegistryError):
            self.registry.load()
        os.remove(self.registry.path)
        with open(self.registry.path, "wb") as handle:
            handle.write(b" " * (registry_module.MAX_REGISTRY_BYTES + 1))
        os.chmod(self.registry.path, 0o600)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.registry.load()
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_MALFORMED)
        with open(self.registry.path, "wb") as handle:
            handle.write(b"\xff\xfe not utf-8")
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.registry.load()
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_MALFORMED)

    def test_degraded_registry_is_an_explicit_degraded_response(self):
        self.register()
        os.chmod(self.registry.path, 0o644)
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["registry"]["health"],
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(response["registry"]["problem"],
                         registry_module.PROBLEM_REGISTRY_PERMISSIONS)
        self.assertIsNone(response["registry"]["source_count"])
        self.assertEqual(response["sources"], [])
        self.assertEqual(response["missions"], [])
        self.assertNotEqual(contract.response_freshness(response, NOW)[0],
                            contract.HEALTH_FRESH)

    def test_bound_is_exposed_and_overflow_is_explicit_never_silent(self):
        for i in range(registry_module.MAX_REGISTERED_SOURCES):
            self.register(source_id="mc-%03d" % i)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.register(source_id="mc-overflow")
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_FULL)
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["registry"]["max_sources"],
                         registry_module.MAX_REGISTERED_SOURCES)
        self.assertEqual(response["registry"]["source_count"],
                         registry_module.MAX_REGISTERED_SOURCES)
        self.assertEqual(len(response["sources"]),
                         registry_module.MAX_REGISTERED_SOURCES)


# -- real isolated stores --------------------------------------------------

AUTHENTICATED = mission_record.AuthenticatedContext(
    transport="synthetic_test", principal_kind="local_process_user",
    principal_ref="synthetic-tester")


class StoreFixture(RegistryFixture):
    """Real Mission Core and WorkflowStore files in private temp dirs."""

    def setUp(self):
        super(StoreFixture, self).setUp()
        self.clock = Clock()

    def mission_state(self, name="mc"):
        return self.store_dir(name)

    def propose(self, state_dir, objective="Fixture mission"):
        surface = surface_module.build_bridgeless_surface(state_dir, self.clock)
        out = surface.submit(mission_request(objective=objective))
        return surface, out["request_ref"], out["mission_id"]

    def authorize(self, surface, ref):
        presented = surface.present(ref)
        surface.attest_approval(
            request_ref=ref, mission_id=presented["mission_id"],
            revision=presented["revision"],
            proposal_digest_sha256=presented["proposal_digest_sha256"],
            approved_action_scope=list(presented["approved_action_scope"]),
            approved_delivery_targets=list(
                presented["approved_delivery_targets"]),
            expires_at=presented["latest_expires_at"],
            relayed_reply="approved", relay_ref="synthetic-relay")
        return presented

    def drive_run(self, state_dir, mission_id, presented, task_id="task-0001",
            receipt=True, running=True, workspace_name=None):
        missions = mission_service.MissionService(
            mission_store.MissionStore(state_dir), self.clock)
        mission = missions.get(mission_id)["record"]
        url = mission["revisions"][-1]["proposal"]["repository_url"]
        workspace = os.path.realpath(os.path.join(
            self.root, workspace_name or ("ws-" + mission_id)))
        repository = os.path.realpath(os.path.join(self.root, "repo"))
        os.makedirs(workspace, exist_ok=True)
        os.makedirs(repository, exist_ok=True)
        bound = missions.record_workspace_binding(
            mission_id, workspace, repository, url, "a" * 40, AUTHENTICATED)
        missions.record_workspace_prepared(
            mission_id, bound["binding_digest_sha256"], AUTHENTICATED)
        missions.activate_proof_contract(
            mission_id, missions.mint_state_operation_id(AUTHENTICATED),
            missions.get_state(mission_id)["sequence"], AUTHENTICATED)
        missions.record_run_intent(
            mission_id, mission["authorization_ids"][-1],
            presented["revision"], presented["proposal_digest_sha256"], url,
            workspace, "a" * 40, "c" * 64, "d" * 64, "b" * 64,
            list(presented["approved_action_scope"]),
            list(presented["approved_delivery_targets"]), AUTHENTICATED)
        if receipt:
            missions.record_run_receipt(mission_id, task_id, "start_result",
                                        None, AUTHENTICATED)
            if running:
                missions.record_observed_running(mission_id, task_id, False,
                                                 AUTHENTICATED)
        return workspace

    def running_mission(self, state_dir, task_id="task-0001",
                        objective="Fixture mission"):
        surface, ref, mission_id = self.propose(state_dir, objective)
        presented = self.authorize(surface, ref)
        workspace = self.drive_run(state_dir, mission_id, presented, task_id)
        return mission_id, workspace

    def workflow(self, store_dir, workflow_id="wf-0002", phase=None,
                 task_id="task-0002", workspace=None, relay=True,
                 released=False, ambiguity=None):
        store = workflow_store.WorkflowStore(store_dir)
        document = store.load()
        record = (build_relay_record(workflow_id=workflow_id) if relay
                  else make_record(workflow_id=workflow_id))
        record["phase"] = phase or workflow_record.PHASE_DISPATCHED
        if workspace is not None:
            record["workspace_lease"] = {
                "lease_id": "lease-" + "0" * 24, "path_realpath": workspace,
                "acquired_at": 100,
                "released_at": 300 if released else None}
        if task_id is not None:
            record["target_engine"] = {
                "alias": "di-remote-2-" + workflow_id, "task_id": task_id,
                "repo": "https://github.com/octocat/target",
                "dispatched_at": 200}
        if ambiguity is not None:
            record["ambiguity"] = ambiguity
        document["workflows"][workflow_id] = record
        store.save(document)
        return record

    def source(self, source_id, kind, store_dir, producer="fixture"):
        return {"source_id": source_id, "kind": kind, "store_dir": store_dir,
                "producer": producer, "max_age_seconds": 60,
                "registered_at": NOW}


class MissionCoreReaderTests(StoreFixture):

    def read(self, state_dir, source_id="mc"):
        return sources_module.read_mission_core(
            self.source(source_id, contract.KIND_MISSION_CORE, state_dir,
                        producer="local_request"), NOW,
            sources_module.QueryBudget())

    def test_unmaterialised_and_missing_stores_are_unavailable(self):
        # This reader does not own the store: a registered store whose file
        # does not exist is unavailable, never the owner's empty default.
        empty = self.read(self.mission_state("empty"))
        self.assertEqual((empty["health"], empty["problem"],
                          empty["record_count"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_STORE_FILE_ABSENT, 0))
        self.assertEqual(os.listdir(self.mission_state("empty")), [])
        missing = self.read(os.path.join(self.root, "absent-store"))
        self.assertEqual(missing["health"], contract.HEALTH_UNAVAILABLE)
        self.assertEqual(missing["problem"],
                         sources_module.PROBLEM_STORE_DIRECTORY_MISSING)
        self.assertEqual(missing["missions"], [])

    def test_views_carry_native_lifecycle_identity_and_provenance(self):
        state = self.mission_state()
        mission_id, workspace = self.running_mission(state)
        surface, _, proposed_id = self.propose(state, "Second mission")
        reading = self.read(state)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)
        views = {v["native_id"]: v for v in reading["missions"]}
        running = views[mission_id]
        self.assertEqual(running["mission_ref"], "mc:" + mission_id)
        self.assertEqual(running["lifecycle"], {
            "model": contract.LIFECYCLE_MODEL_MISSION_CORE,
            "state": mission_record.STATE_RUNNING,
            "standing": contract.STANDING_ACTIVE})
        self.assertEqual(running["producer"]["declared"], "local_request")
        self.assertEqual(running["producer"]["recorded"], {
            "transport": "local_request",
            "principal_kind": "unauthenticated_local_caller"})
        self.assertEqual(running["execution"]["task_id"], "task-0001")
        self.assertEqual(running["execution"]["identity_source"],
                         "start_result")
        self.assertEqual(running["execution"]["workspace_realpath"], workspace)
        self.assertTrue(running["execution"]["consistent"])
        proposed = views[proposed_id]
        self.assertEqual(proposed["lifecycle"]["state"],
                         mission_record.STATE_AWAITING_DECISION)
        self.assertEqual(proposed["lifecycle"]["standing"],
                         contract.STANDING_INACTIVE)
        self.assertIsNone(proposed["execution"]["task_id"])
        self.assertFalse(proposed["execution"]["run_started"])

    def test_reader_document_is_the_native_load_document(self):
        state = self.mission_state()
        self.running_mission(state)
        captured = {}
        real = mission_store.accept_loaded_document
        def capture(document, path):
            captured["document"] = real(document, path)
            return captured["document"]
        with mock.patch.object(mission_store, "accept_loaded_document",
                               side_effect=capture):
            self.read(state)
        self.assertEqual(captured["document"],
                         mission_store.MissionStore(state).load())

    def test_store_file_is_opened_once_and_never_by_the_legacy_loader(self):
        state = self.mission_state()
        self.running_mission(state)
        with mock.patch.object(mission_store.MissionStore, "load",
                               side_effect=AssertionError("re-open")):
            reading = self.read(state)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)

    def test_degraded_stores_contribute_no_missions(self):
        state = self.mission_state()
        self.running_mission(state)
        path = os.path.join(state, mission_store.MISSIONS_FILE_NAME)
        with open(path, encoding="utf-8") as handle:
            valid = json.load(handle)
        def write(document_or_bytes):
            os.chmod(path, 0o600)
            with open(path, "wb") as handle:
                if isinstance(document_or_bytes, bytes):
                    handle.write(document_or_bytes)
                else:
                    handle.write(json.dumps(document_or_bytes).encode())
        write(b"{broken")
        reading = self.read(state)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_MALFORMED,
                          sources_module.PROBLEM_STORE_NOT_JSON))
        self.assertEqual(reading["missions"], [])
        incompatible = copy.deepcopy(valid)
        incompatible["mission_store_schema_version"] = 2
        tampered = copy.deepcopy(valid)
        next(iter(tampered["missions"].values()))["state"] = "PAUSED_FOREVER"
        for document, expected in (
                (incompatible, (contract.HEALTH_INCOMPATIBLE,
                                sources_module.PROBLEM_STORE_SCHEMA)),
                (tampered, (contract.HEALTH_MALFORMED,
                            sources_module.PROBLEM_STORE_REFUSED))):
            write(document)
            reading = self.read(state)
            self.assertEqual((reading["health"], reading["problem"]), expected)
            self.assertEqual(reading["missions"], [])
        write(valid)
        os.chmod(path, 0o644)
        reading = self.read(state)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_STORE_PERMISSIONS_OPEN))
        os.chmod(path, 0o600)
        os.chmod(state, 0o755)
        reading = self.read(state)
        self.assertEqual(reading["problem"],
                         sources_module.PROBLEM_STORE_PERMISSIONS_OPEN)
        os.chmod(state, 0o700)
        os.rename(path, path + ".real")
        os.symlink(path + ".real", path)
        reading = self.read(state)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_STORE_UNREADABLE))
        self.assertEqual(reading["missions"], [])
        os.remove(path)
        os.mkfifo(path, 0o600)
        reading = self.read(state)
        self.assertEqual(reading["health"], contract.HEALTH_UNAVAILABLE)
        self.assertEqual(reading["missions"], [])

    def test_reader_writes_nothing_and_takes_no_lock(self):
        state = self.mission_state()
        self.running_mission(state)
        os.remove(os.path.join(state, mission_store.MISSIONS_LOCK_FILE_NAME))
        before = {name: os.stat(os.path.join(state, name)).st_mtime_ns
                  for name in os.listdir(state)}
        with mock.patch.object(workflow_atomic, "atomic_write_json",
                               side_effect=AssertionError("write")), \
                mock.patch.object(workflow_atomic, "exclusive_store_lock",
                                  side_effect=AssertionError("lock")), \
                mock.patch.object(mission_store.MissionStore, "save",
                                  side_effect=AssertionError("save")), \
                mock.patch.object(mission_store.MissionStore, "lock",
                                  side_effect=AssertionError("lock")):
            self.read(state)
        after = {name: os.stat(os.path.join(state, name)).st_mtime_ns
                 for name in os.listdir(state)}
        self.assertEqual(before, after)


class WorkflowReaderTests(StoreFixture):

    def read(self, store_dir):
        return sources_module.read_workflow_store(
            self.source("grok-wf", contract.KIND_WORKFLOW_STORE, store_dir,
                        producer="grok_bot"), NOW,
            sources_module.QueryBudget())

    def test_views_carry_their_own_model_and_recorded_origin(self):
        store_dir = self.store_dir("wf")
        workspace = os.path.realpath(self.store_dir("wf-ws"))
        self.workflow(store_dir, workspace=workspace)
        self.workflow(store_dir, workflow_id="wf-0003", relay=False,
                      phase=workflow_record.PHASE_PLANNED, task_id=None)
        reading = self.read(store_dir)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)
        views = {v["native_id"]: v for v in reading["missions"]}
        relay = views["wf-0002"]
        self.assertEqual(relay["mission_ref"], "grok-wf:wf-0002")
        self.assertEqual(relay["lifecycle"], {
            "model": contract.LIFECYCLE_MODEL_WORKFLOW_STORE,
            "state": workflow_record.PHASE_DISPATCHED,
            "standing": contract.STANDING_ACTIVE})
        self.assertEqual(relay["producer"], {
            "declared": "grok_bot",
            "recorded": {"origin_transport": "grok_bot",
                         "telegram_bound": False}})
        self.assertEqual(relay["execution"]["task_id"], "task-0002")
        self.assertEqual(relay["execution"]["workspace_realpath"], workspace)
        self.assertIsNone(relay["updated_at"])
        planned = views["wf-0003"]
        # Native eligibility (non-terminal) — membership still needs the
        # canonical execution chain, which a PLANNED record does not have.
        self.assertEqual(planned["lifecycle"]["standing"],
                         contract.STANDING_ACTIVE)
        self.assertIsNone(planned["execution"]["task_id"])
        self.assertEqual(planned["producer"]["recorded"],
                         {"origin_transport": None, "telegram_bound": True})

    def test_unresolved_task_released_lease_and_crash_ambiguity(self):
        store_dir = self.store_dir("wf")
        workspace = os.path.realpath(self.store_dir("wf-ws"))
        self.workflow(store_dir, task_id=sources_module.WORKFLOW_UNRESOLVED_TASK_ID,
                      workspace=workspace)
        self.workflow(store_dir, workflow_id="wf-0004", workspace=workspace,
                      released=True)
        self.workflow(store_dir, workflow_id="wf-0005", workspace=workspace,
                      ambiguity={"state": "crash_uncertain",
                                 "detail": "fixture"})
        views = {v["native_id"]: v for v in self.read(store_dir)["missions"]}
        self.assertIsNone(views["wf-0002"]["execution"]["task_id"])
        self.assertTrue(views["wf-0002"]["execution"]["receipt_recorded"])
        self.assertIsNone(views["wf-0004"]["execution"]["workspace_realpath"])
        self.assertTrue(views["wf-0005"]["execution"]["record_ambiguous"])

    def test_unresolved_sentinel_matches_the_runtime_constant(self):
        from target_runtime import dispatch as dispatch_module
        self.assertEqual(sources_module.WORKFLOW_UNRESOLVED_TASK_ID,
                         dispatch_module.UNRESOLVED_TASK_ID)

    def test_reader_uses_the_native_boundary_without_reopening(self):
        store_dir = self.store_dir("wf")
        self.workflow(store_dir, workspace=os.path.realpath(
            self.store_dir("wf-ws")))
        with mock.patch.object(workflow_store.WorkflowStore, "load",
                               side_effect=AssertionError("re-open")):
            reading = self.read(store_dir)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)
        path = os.path.join(store_dir, workflow_store.WORKFLOWS_FILE_NAME)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["workflow_store_schema_version"] = 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        reading = self.read(store_dir)
        self.assertEqual(reading["health"], contract.HEALTH_INCOMPATIBLE)
        document["workflow_store_schema_version"] = 2
        document["workflows"]["wf-0002"]["phase"] = "SOMEWHERE_NEW"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        reading = self.read(store_dir)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_MALFORMED,
                          sources_module.PROBLEM_STORE_REFUSED))
        self.assertEqual(reading["missions"], [])


class StandingTableTests(Bounded):

    def test_each_table_covers_exactly_its_own_models_literals(self):
        self.assertEqual(set(sources_module.MISSION_CORE_STANDING),
                         set(mission_record.MISSION_STATES))
        self.assertEqual(set(sources_module.WORKFLOW_STORE_STANDING),
                         set(workflow_record.PHASES))

    def test_unknown_literal_is_unknown_future_in_either_model(self):
        for table in (sources_module.MISSION_CORE_STANDING,
                      sources_module.WORKFLOW_STORE_STANDING):
            self.assertEqual(sources_module.standing_of(table, "NEW_STATE"),
                             contract.STANDING_UNKNOWN_FUTURE)

    def test_models_are_not_merged(self):
        # The same literal means different things in the two models and is
        # classified by each model's own table, never through the other.
        self.assertNotEqual(contract.LIFECYCLE_MODEL_MISSION_CORE,
                            contract.LIFECYCLE_MODEL_WORKFLOW_STORE)
        self.assertEqual(sources_module.standing_of(
            sources_module.MISSION_CORE_STANDING, "BLOCKED"),
            contract.STANDING_TERMINAL)
        self.assertEqual(sources_module.standing_of(
            sources_module.MISSION_CORE_STANDING, "DISPATCHED"),
            contract.STANDING_UNKNOWN_FUTURE)
        self.assertEqual(sources_module.standing_of(
            sources_module.WORKFLOW_STORE_STANDING, "RUNNING"),
            contract.STANDING_UNKNOWN_FUTURE)


# -- roster semantics (pure) ------------------------------------------------

def view(ref="mc:mn-1", standing=contract.STANDING_ACTIVE, task_id="task-1",
         workspace="/fixture/ws/mn-1", run_started=True, receipt=True,
         consistent=True, ambiguous=False, state="RUNNING"):
    source_id, native = ref.split(":", 1)
    return {
        "mission_ref": ref, "source_id": source_id, "native_id": native,
        "lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE,
                      "state": state, "standing": standing},
        "producer": {"declared": "fixture", "recorded": {}},
        "revision": 1, "objective": "o", "updated_at": NOW,
        "execution": {"task_id": task_id, "identity_source": None,
                      "workspace_realpath": workspace,
                      "run_started": run_started, "receipt_recorded": receipt,
                      "consistent": consistent, "record_ambiguous": ambiguous},
        "native": {},
    }


def task(task_id="task-1", workspace="/fixture/ws/mn-1", bindings=None):
    return {"task_id": task_id, "workspace_realpath": workspace,
            "bindings": bindings if bindings is not None else [binding()]}


def classify(views, tasks=None, agents=None, observed_at=NOW, now=NOW):
    reading = contract.read_runtime_snapshot(
        snapshot(observed_at, tasks=tasks if tasks is not None else [task()],
                 agents=agents), now)
    return roster.classify(views, reading)


class DiscoveryProjectionTests(Bounded):
    """``roster.discovery``: a root to OBSERVE comes only from a canonical
    active, consistent, unambiguous binding; a shared workspace is a
    reported conflict whatever the order; and ``classify`` reads an
    unobserved workspace as unverified, never absent and never present."""

    def test_only_canonical_active_bindings_name_a_root(self):
        views = [
            view("mc:good", workspace="/w/good", task_id="t-good"),
            view("mc:idle", workspace="/w/idle", task_id="t-idle",
                 standing=contract.STANDING_INACTIVE),
            view("mc:done", workspace="/w/done", task_id="t-done",
                 standing=contract.STANDING_TERMINAL),
            view("mc:amb", workspace="/w/amb", task_id="t-amb",
                 ambiguous=True),
            view("mc:inc", workspace="/w/inc", task_id="t-inc",
                 consistent=False),
            view("mc:unres", workspace="/w/unres", task_id=None),
            view("mc:nows", workspace=None, task_id="t-nows"),
            view("mc:dup1", workspace="/w/dup1", task_id="t-dup"),
            view("wf:dup2", workspace="/w/dup2", task_id="t-dup"),
        ]
        found = roster.discovery(views)
        self.assertEqual(found["roots"], ["/w/good"])
        self.assertEqual(found["missions_by_root"], {"/w/good": "mc:good"})
        self.assertEqual(found["conflicts"], [])

    def test_a_shared_workspace_is_a_conflict_in_any_order(self):
        pair = [view("mc:a", workspace="/w/shared", task_id="t-a"),
                view("wf:b", workspace="/w/shared", task_id="t-b")]
        for views in (pair, list(reversed(pair))):
            found = roster.discovery(views)
            self.assertEqual(found["roots"], [])
            self.assertEqual(found["conflicts"], [{
                "workspace_realpath": "/w/shared",
                "mission_refs": ["mc:a", "wf:b"]}])
            missions, _ = classify(views, tasks=[
                task("t-a", "/w/shared")])
            for mission in missions:
                self.assertEqual(
                    (mission["binding"]["state"], mission["binding"]["reason"],
                     mission["members"]),
                    (contract.BINDING_AMBIGUOUS,
                     roster.REASON_WORKSPACE_MULTIPLY_CLAIMED, []))

    def test_unobserved_is_neither_absent_nor_present(self):
        reading = contract.read_runtime_snapshot(snapshot(tasks=[task()]), NOW)
        unobserved, _ = roster.classify([view()], reading, observed_roots=set())
        self.assertEqual((unobserved[0]["binding"]["state"],
                          unobserved[0]["binding"]["reason"],
                          unobserved[0]["members"]),
                         (contract.BINDING_UNVERIFIED,
                          roster.REASON_WORKSPACE_NOT_OBSERVED, []))
        empty = contract.read_runtime_snapshot(snapshot(tasks=[]), NOW)
        looked, _ = roster.classify([view()], empty,
                                    observed_roots={"/fixture/ws/mn-1"})
        self.assertEqual(looked[0]["binding"]["state"],
                         contract.BINDING_DISAPPEARED)
        observed, _ = roster.classify([view()], reading,
                                      observed_roots={"/fixture/ws/mn-1"})
        unknown, _ = roster.classify([view()], reading)
        for mission in (observed[0], unknown[0]):
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_BOUND)


class RosterTests(Bounded):

    def one(self, views=None, **kwargs):
        missions, unmatched = classify(views or [view()], **kwargs)
        return missions[0], unmatched

    def test_canonical_chain_yields_a_present_member(self):
        mission, unmatched = self.one()
        self.assertEqual(mission["binding"], {
            "state": contract.BINDING_BOUND,
            "reason": roster.REASON_BOUND, "task_id": "task-1"})
        [member] = mission["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)
        self.assertEqual(member["activity"], contract.ACTIVITY_BUSY)
        self.assertEqual(member["member_ref"], "mc:mn-1#executor1")
        self.assertEqual((member["role"], member["provider"]),
                         ("executor", "claude"))
        self.assertEqual(member["session"],
                         {"recorded": "sess-exec-1", "live": "sess-exec-1"})
        self.assertEqual(member["observed_at"], NOW)
        self.assertEqual(unmatched, 0)

    def test_unsupported_facts_are_unknown_never_zero_or_empty(self):
        mission, _ = self.one(tasks=[task(bindings=[binding(role=None)])],
                              agents=[live_agent(provider=None)])
        [member] = mission["members"]
        for fact in contract.UNREPORTED_FACTS:
            self.assertEqual(member[fact], contract.UNKNOWN, fact)
        self.assertEqual(member["role"], contract.UNKNOWN)
        self.assertEqual(member["provider"], contract.UNKNOWN)

    def test_absent_role_or_provider_is_never_present_or_replaced(self):
        cases = [dict(tasks=[task(bindings=[binding(role=None)])]),
                 dict(agents=[live_agent(provider=None)]),
                 dict(tasks=[task(bindings=[binding(role=None)])],
                      agents=[live_agent(provider=None, session="sess-new")]),
                 dict(agents=[live_agent(provider=None, session="sess-new")])]
        for kwargs in cases:
            mission, _ = self.one(**kwargs)
            [member] = mission["members"]
            self.assertEqual((member["classification"], member["reason"]),
                             (contract.MEMBER_AMBIGUOUS,
                              roster.MEMBER_REASON_PROVENANCE_UNCONFIRMED),
                             kwargs)
        # With both reported, the same inputs classify normally.
        mission, _ = self.one()
        self.assertEqual(mission["members"][0]["classification"],
                         contract.MEMBER_PRESENT)

    def test_idle_and_done_are_not_completion(self):
        for status, activity in (("idle", contract.ACTIVITY_IDLE),
                                 ("done", contract.ACTIVITY_DONE)):
            mission, _ = self.one(agents=[live_agent(status=status)])
            self.assertEqual(mission["members"][0]["activity"], activity)
            self.assertEqual(mission["lifecycle"]["state"], "RUNNING")
            self.assertEqual(mission["lifecycle"]["standing"],
                             contract.STANDING_ACTIVE)
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_BOUND)
            self.assertEqual(mission["members"][0]["classification"],
                             contract.MEMBER_PRESENT)

    def test_unknown_runtime_status_is_unknown_future(self):
        mission, _ = self.one(agents=[live_agent(status="hibernating")])
        self.assertEqual(mission["members"][0]["activity"],
                         contract.ACTIVITY_UNKNOWN_FUTURE)
        mission, _ = self.one(agents=[live_agent(status="unknown")])
        self.assertEqual(mission["members"][0]["activity"],
                         contract.ACTIVITY_UNKNOWN)

    def test_replaced_disappeared_and_ambiguous_stay_distinct(self):
        cases = [
            (dict(agents=[live_agent(session="sess-new")]),
             contract.MEMBER_REPLACED, roster.MEMBER_REASON_SESSION_CHANGED),
            (dict(agents=[]), contract.MEMBER_DISAPPEARED,
             roster.MEMBER_REASON_NOT_LIVE),
            (dict(agents=[live_agent(), live_agent()]),
             contract.MEMBER_AMBIGUOUS, roster.MEMBER_REASON_NAME_DUPLICATED),
            (dict(tasks=[task(bindings=[binding(), binding(agent="other")])]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_LOGICAL_DUPLICATED),
            (dict(agents=[live_agent(pane_id="w9:p9")]),
             contract.MEMBER_AMBIGUOUS, roster.MEMBER_REASON_STABLE_CONFLICT),
            (dict(agents=[live_agent(workspace_id="w9")]),
             contract.MEMBER_AMBIGUOUS, roster.MEMBER_REASON_STABLE_CONFLICT),
            (dict(agents=[live_agent(session=None)]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_SESSION_UNCONFIRMED),
            (dict(agents=[live_agent(pane_id=None)]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_STABLE_UNCONFIRMED),
            (dict(agents=[live_agent(workspace_id=None)]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_STABLE_UNCONFIRMED),
            (dict(tasks=[task(bindings=[binding(pane_id=None)])]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_STABLE_UNCONFIRMED),
            (dict(tasks=[task(bindings=[binding(workspace_id=None)])]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_STABLE_UNCONFIRMED),
            (dict(tasks=[task(bindings=[
                binding(), binding(logical="lead1", session="s-l",
                                   pane_id="w1:p2")])]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_AGENT_MULTIPLY_CLAIMED),
            (dict(tasks=[task(bindings=[
                binding(), binding(logical="lead1", agent="pod-lead1",
                                   pane_id="w1:p2")])],
                  agents=[live_agent(),
                          live_agent(agent="pod-lead1", pane_id="w1:p2")]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_SESSION_MULTIPLY_CLAIMED),
            (dict(agents=[live_agent(),
                          live_agent(agent="intruder", pane_id="w1:p9")]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_LIVE_SESSION_SHARED),
            (dict(tasks=[task(bindings=[binding(session=None)])]),
             contract.MEMBER_AMBIGUOUS,
             roster.MEMBER_REASON_SESSION_UNCONFIRMED),
        ]
        for kwargs, classification, reason in cases:
            mission, _ = self.one(**kwargs)
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_BOUND, kwargs)
            for member in mission["members"]:
                self.assertEqual((member["classification"], member["reason"]),
                                 (classification, reason), kwargs)
                if classification != contract.MEMBER_PRESENT:
                    self.assertNotEqual(member["classification"],
                                        contract.MEMBER_PRESENT)
        self.assertEqual(len(set(contract.MEMBER_CLASSIFICATIONS)), 4)

    def test_one_agent_is_never_two_members_across_tasks(self):
        # Two missions, bound to two distinct tasks, whose bindings both
        # name the same physical agent: neither may claim it.
        views = [view("mc:mn-1"),
                 view("mc:mn-2", task_id="task-2", workspace="/w/2")]
        tasks = [task(), task("task-2", "/w/2",
                              [binding(logical="reviewer1",
                                       session="sess-other")])]
        missions, _ = classify(views, tasks=tasks)
        for mission in missions:
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_BOUND)
            [member] = mission["members"]
            self.assertEqual(member["classification"],
                             contract.MEMBER_AMBIGUOUS)
            self.assertEqual(member["reason"],
                             roster.MEMBER_REASON_AGENT_MULTIPLY_CLAIMED)
        present = [m for mission in missions for m in mission["members"]
                   if m["classification"] == contract.MEMBER_PRESENT]
        self.assertEqual(present, [])

    def test_identity_is_checked_before_present_or_replaced(self):
        # A changed session would read as "replaced" — but an unconfirmed
        # pane must make it ambiguous first, never a replacement claim.
        mission, _ = self.one(agents=[live_agent(session="sess-new",
                                                 pane_id=None)])
        self.assertEqual(mission["members"][0]["reason"],
                         roster.MEMBER_REASON_STABLE_UNCONFIRMED)

    def test_mission_level_failures_never_invent_a_binding(self):
        cases = [
            (view(task_id=None, run_started=False, receipt=False),
             contract.BINDING_UNBOUND, roster.REASON_NOT_STARTED),
            (view(task_id=None, run_started=True, receipt=False),
             contract.BINDING_UNBOUND, roster.REASON_RUN_OUTCOME_UNKNOWN),
            (view(task_id=None, receipt=True),
             contract.BINDING_UNBOUND, roster.REASON_TASK_UNRESOLVED),
            (view(workspace=None), contract.BINDING_UNBOUND,
             roster.REASON_WORKSPACE_UNRECORDED),
            (view(consistent=False), contract.BINDING_AMBIGUOUS,
             roster.REASON_WORKSPACE_INCONSISTENT),
            (view(ambiguous=True), contract.BINDING_AMBIGUOUS,
             roster.REASON_RECORD_AMBIGUITY),
            (view(task_id="task-absent"), contract.BINDING_DISAPPEARED,
             roster.REASON_TASK_NOT_OBSERVED),
            (view(workspace="/fixture/ws/other"), contract.BINDING_AMBIGUOUS,
             roster.REASON_TASK_WORKSPACE_MISMATCH),
        ]
        for mission_view, state, reason in cases:
            mission, _ = self.one([mission_view])
            self.assertEqual((mission["binding"]["state"],
                              mission["binding"]["reason"]), (state, reason))
            self.assertEqual(mission["members"], [])

    def test_no_guessing_by_name_when_unbound(self):
        # The snapshot holds a task whose id merely LOOKS like this mission
        # (the runtime alias convention); with no recorded task id it stays
        # unbound and the agent is an unrelated runtime session.
        mission, unmatched = self.one(
            [view(task_id=None, run_started=True, receipt=False)],
            tasks=[task(task_id="di-remote-2-mn-1")])
        self.assertEqual(mission["binding"]["state"], contract.BINDING_UNBOUND)
        self.assertEqual(mission["members"], [])
        self.assertEqual(unmatched, 1)

    def test_duplicates_hold_as_ambiguous(self):
        missions, _ = classify([view("mc:mn-1"), view("wf:wf-1")])
        for mission in missions:
            self.assertEqual(mission["binding"]["reason"],
                             roster.REASON_TASK_MULTIPLY_CLAIMED)
            self.assertEqual(mission["members"], [])
        mission, _ = self.one(tasks=[task(), task()])
        self.assertEqual(mission["binding"]["reason"],
                         roster.REASON_TASK_DUPLICATED)
        self.assertEqual(mission["members"], [])

    def test_inactive_and_terminal_missions_have_no_members(self):
        for standing in (contract.STANDING_INACTIVE, contract.STANDING_TERMINAL,
                         contract.STANDING_UNKNOWN_FUTURE):
            mission, unmatched = self.one([view(standing=standing)])
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_NOT_APPLICABLE)
            self.assertEqual(mission["members"], [])
            # Its still-live agent is now an unrelated session: counted,
            # never listed, and never terminated (nothing here can).
            self.assertEqual(unmatched, 1)

    def test_degraded_snapshot_is_never_live_and_never_binds(self):
        stale = classify([view()], observed_at=NOW - 61)
        future = classify([view()], observed_at=NOW + 1)
        for missions, unmatched in (stale, future):
            self.assertEqual(missions[0]["binding"]["state"],
                             contract.BINDING_UNVERIFIED)
            self.assertEqual(missions[0]["members"], [])
            self.assertIsNone(unmatched)
        for value in (None, {"schema": "other"}, []):
            reading = contract.read_runtime_snapshot(value, NOW)
            missions, unmatched = roster.classify([view()], reading)
            self.assertEqual(missions[0]["binding"]["state"],
                             contract.BINDING_UNVERIFIED)
            self.assertEqual(missions[0]["members"], [])
            self.assertIsNone(unmatched)

    def test_unrelated_runtime_sessions_are_counted_not_listed(self):
        mission, unmatched = self.one(agents=[
            live_agent(), live_agent(agent="stranger", session="s-x"),
            live_agent(agent="user-owned-jarvis", session="s-y")])
        self.assertEqual([m["agent"] for m in mission["members"]],
                         ["pod-exec1"])
        self.assertEqual(unmatched, 2)

    def test_pure_deterministic_and_input_unchanged(self):
        views = [view("mc:mn-2", task_id="task-2", workspace="/w/2"), view()]
        frozen = copy.deepcopy(views)
        tasks = [task(), task("task-2", "/w/2",
                              [binding(logical="lead1", agent="pod-lead1",
                                       session="s-l", pane_id="w1:p2")])]
        agents = [live_agent(), live_agent(agent="pod-lead1", session="s-l",
                                           pane_id="w1:p2")]
        first = classify(views, tasks=tasks, agents=agents)
        second = classify(list(reversed(views)), tasks=list(reversed(tasks)),
                          agents=list(reversed(agents)))
        self.assertEqual(first, second)
        self.assertEqual(views, frozen)
        self.assertEqual([m["mission_ref"] for m in first[0]],
                         ["mc:mn-1", "mc:mn-2"])


# -- the query: data-driven, idempotent, honest ---------------------------

class QueryTests(StoreFixture):

    def runtime(self, task_id, workspace, agents=None, observed_at=NOW,
                session="sess-exec-1"):
        return snapshot(observed_at, tasks=[task(task_id, workspace, [
            binding(session=session)])], agents=agents if agents is not None
            else [live_agent(session=session)])

    def test_new_source_and_new_mission_appear_with_no_code_change(self):
        first_state = self.mission_state("first")
        first_id, first_ws = self.running_mission(first_state)
        self.register(source_id="mc-first", store=first_state)
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual([m["native_id"] for m in response["missions"]],
                         [first_id])
        # A NEW store, registered as data only, appears in the next query.
        second_state = self.mission_state("second")
        second_id, _ = self.running_mission(second_state, task_id="task-2")
        self.registry.register("mc-second", contract.KIND_MISSION_CORE,
                               second_state, "grok_bot", 60, NOW)
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(sorted(m["mission_ref"] for m in response["missions"]),
                         sorted(["mc-first:" + first_id,
                                 "mc-second:" + second_id]))
        # A NEW mission created in a registered store appears too.
        _, _, third_id = self.propose(first_state, "A brand new mission")
        response = query_module.query(self.registry, None, NOW)
        self.assertIn("mc-first:" + third_id,
                      [m["mission_ref"] for m in response["missions"]])
        self.assertEqual(response["registry"]["source_count"], 2)

    def test_both_models_side_by_side_bound_through_one_contract(self):
        state = self.mission_state()
        mission_id, workspace = self.running_mission(state)
        wf_dir = self.store_dir("wf")
        wf_ws = os.path.realpath(self.store_dir("wf-ws"))
        self.workflow(wf_dir, workspace=wf_ws)
        self.register(source_id="mc", store=state)
        self.registry.register("grok-wf", contract.KIND_WORKFLOW_STORE, wf_dir,
                               "grok_bot", 60, NOW)
        runtime = snapshot(NOW, tasks=[
            task("task-0001", workspace, [binding()]),
            task("task-0002", wf_ws, [binding(logical="lead1",
                                              agent="pod-lead1",
                                              session="s-l",
                                              pane_id="w1:p2")])],
            agents=[live_agent(), live_agent(agent="pod-lead1", session="s-l",
                                             pane_id="w1:p2")])
        response = query_module.query(self.registry, runtime, NOW)
        self.assertEqual(contract.response_freshness(response, NOW),
                         (contract.HEALTH_FRESH, None))
        by_ref = {m["mission_ref"]: m for m in response["missions"]}
        mission = by_ref["mc:" + mission_id]
        workflow = by_ref["grok-wf:wf-0002"]
        self.assertEqual(mission["lifecycle"]["model"],
                         contract.LIFECYCLE_MODEL_MISSION_CORE)
        self.assertEqual(workflow["lifecycle"]["model"],
                         contract.LIFECYCLE_MODEL_WORKFLOW_STORE)
        self.assertEqual(mission["lifecycle"]["state"], "RUNNING")
        self.assertEqual(workflow["lifecycle"]["state"], "DISPATCHED")
        self.assertEqual(mission["members"][0]["classification"],
                         contract.MEMBER_PRESENT)
        self.assertEqual(workflow["members"][0]["agent"], "pod-lead1")
        self.assertEqual(response["unmatched_runtime_agent_count"], 0)
        self.assertEqual([s["health"] for s in response["sources"]],
                         [contract.HEALTH_FRESH, contract.HEALTH_FRESH])

    def test_duplicate_events_and_reconnects_are_idempotent(self):
        state = self.mission_state()
        _, workspace = self.running_mission(state)
        self.register(source_id="mc", store=state)
        runtime = self.runtime("task-0001", workspace)
        first = query_module.query(self.registry, runtime, NOW)
        again = query_module.query(self.registry, copy.deepcopy(runtime), NOW)
        self.assertEqual(first, again)
        self.assertEqual(len(first["missions"]), 1)
        self.assertEqual(len(first["missions"][0]["members"]), 1)

    def test_session_replacement_is_classified_never_duplicated(self):
        state = self.mission_state()
        _, workspace = self.running_mission(state)
        self.register(source_id="mc", store=state)
        runtime = self.runtime("task-0001", workspace,
                               agents=[live_agent(session="sess-new")])
        [member] = query_module.query(self.registry, runtime,
                                      NOW)["missions"][0]["members"]
        self.assertEqual(member["classification"], contract.MEMBER_REPLACED)
        self.assertEqual(member["session"],
                         {"recorded": "sess-exec-1", "live": "sess-new"})

    def test_interrupted_run_holds_unbound_and_reconciles_from_records(self):
        state = self.mission_state()
        surface, ref, mission_id = self.propose(state)
        presented = self.authorize(surface, ref)
        workspace = self.drive_run(state, mission_id, presented,
                                   receipt=False)
        self.register(source_id="mc", store=state)
        # The runtime already shows a task in that very workspace; without
        # the recorded receipt nothing is bound to it.
        runtime = self.runtime("task-0001", workspace)
        [mission] = query_module.query(self.registry, runtime, NOW)["missions"]
        self.assertEqual(mission["binding"]["state"], contract.BINDING_UNBOUND)
        self.assertEqual(mission["binding"]["reason"],
                         roster.REASON_RUN_OUTCOME_UNKNOWN)
        self.assertEqual(mission["members"], [])
        # Once Mission Core records the receipt, the next read binds.
        missions = mission_service.MissionService(
            mission_store.MissionStore(state), self.clock)
        missions.record_run_receipt(mission_id, "task-0001", "reconciliation",
                                    None, AUTHENTICATED)
        [mission] = query_module.query(self.registry, runtime, NOW)["missions"]
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)

    def test_degraded_source_lists_health_and_contributes_nothing(self):
        good = self.mission_state("good")
        self.running_mission(good)
        self.register(source_id="mc-good", store=good)
        self.registry.register("mc-gone", contract.KIND_MISSION_CORE,
                               os.path.join(self.root, "gone"), "fixture",
                               60, NOW)
        response = query_module.query(self.registry, None, NOW)
        health = {s["source_id"]: (s["health"], s["record_count"])
                  for s in response["sources"]}
        self.assertEqual(health["mc-gone"], (contract.HEALTH_UNAVAILABLE, 0))
        self.assertEqual(health["mc-good"], (contract.HEALTH_FRESH, 1))
        self.assertEqual({m["source_id"] for m in response["missions"]},
                         {"mc-good"})
        for block in response["sources"]:
            self.assertEqual(block["observed_at"], NOW)
            self.assertNotIn("missions", block)

    def test_query_is_read_only_end_to_end(self):
        state = self.mission_state()
        _, workspace = self.running_mission(state)
        wf_dir = self.store_dir("wf")
        self.workflow(wf_dir, workspace=os.path.realpath(
            self.store_dir("wf-ws")))
        self.register(source_id="mc", store=state)
        self.registry.register("grok-wf", contract.KIND_WORKFLOW_STORE, wf_dir,
                               "grok_bot", 60, NOW)
        watched = [state, wf_dir, self.directory]
        before = {d: sorted((n, os.stat(os.path.join(d, n)).st_mtime_ns)
                            for n in os.listdir(d)) for d in watched}
        guards = [
            mock.patch.object(workflow_atomic, "atomic_write_json",
                              side_effect=AssertionError("write")),
            mock.patch.object(workflow_atomic, "exclusive_store_lock",
                              side_effect=AssertionError("lock")),
            mock.patch.object(registry_module, "atomic_write_json",
                              side_effect=AssertionError("write")),
            mock.patch.object(registry_module, "exclusive_store_lock",
                              side_effect=AssertionError("lock")),
            mock.patch.object(mission_store.MissionStore, "save",
                              side_effect=AssertionError("save")),
            mock.patch.object(mission_store.MissionStore, "lock",
                              side_effect=AssertionError("lock")),
            mock.patch.object(workflow_store.WorkflowStore, "save",
                              side_effect=AssertionError("save")),
        ]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)
        response = query_module.query(
            self.registry, self.runtime("task-0001", workspace), NOW)
        self.assertEqual(len(response["missions"]), 2)
        after = {d: sorted((n, os.stat(os.path.join(d, n)).st_mtime_ns)
                           for n in os.listdir(d)) for d in watched}
        self.assertEqual(before, after)

    def test_query_gate_drops_missions_of_any_non_fresh_reading(self):
        # The gate is the query's own: even a reader that returned views
        # alongside a degraded health contributes nothing.
        self.register(source_id="mc-fake")
        for health in (contract.HEALTH_STALE, contract.HEALTH_UNAVAILABLE,
                       contract.HEALTH_MALFORMED, contract.HEALTH_INCOMPATIBLE):
            def degraded_reader(source, now, budget, health=health):
                return {"source_id": source["source_id"], "kind": source["kind"],
                        "producer": source["producer"],
                        "store_dir": source["store_dir"],
                        "max_age_seconds": source["max_age_seconds"],
                        "health": health, "problem": "fixture",
                        "observed_at": now, "record_count": 1,
                        "missions": [view("mc-fake:mn-1")]}
            response = query_module.query(
                self.registry, None, NOW,
                readers={contract.KIND_MISSION_CORE: degraded_reader})
            self.assertEqual(response["missions"], [], health)
            self.assertEqual(response["sources"][0]["health"], health)

    def test_stale_response_is_refused_by_consumers(self):
        self.register()
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(contract.response_freshness(response, NOW)[0],
                         contract.HEALTH_FRESH)
        self.assertEqual(
            contract.response_freshness(response, NOW + 61)[0],
            contract.HEALTH_STALE)


# -- absence is never observed-empty (F6 / F8) -----------------------------

class AbsenceTests(StoreFixture):

    def test_query_of_absent_registry_is_unavailable_not_empty(self):
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["registry"]["health"],
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(response["registry"]["problem"],
                         registry_module.PROBLEM_REGISTRY_ABSENT)
        self.assertEqual((response["sources"], response["missions"]), ([], []))
        self.assertNotEqual(contract.response_freshness(response, NOW)[0],
                            contract.HEALTH_FRESH)
        self.assertFalse(os.path.exists(self.directory))

    def test_read_empty_registry_is_fresh_observed_empty(self):
        self.register()
        self.registry.unregister("mc-local")
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual((response["registry"]["health"],
                          response["registry"]["source_count"]),
                         (contract.HEALTH_FRESH, 0))

    def test_populated_registry_deleted_then_reconnected(self):
        state = self.mission_state()
        mission_id, _ = self.running_mission(state)
        self.register(source_id="mc", store=state)
        self.assertEqual(len(query_module.query(self.registry, None,
                                                NOW)["missions"]), 1)
        os.remove(self.registry.path)
        lost = query_module.query(self.registry, None, NOW)
        self.assertEqual(lost["registry"]["problem"],
                         registry_module.PROBLEM_REGISTRY_ABSENT)
        self.assertEqual(lost["missions"], [])
        self.register(source_id="mc", store=state)
        back = query_module.query(self.registry, None, NOW)
        self.assertEqual(back["registry"]["health"], contract.HEALTH_FRESH)
        self.assertEqual([m["native_id"] for m in back["missions"]],
                         [mission_id])

    def test_store_absent_then_created(self):
        state = self.mission_state()
        self.register(source_id="mc", store=state)
        before = query_module.query(self.registry, None, NOW)
        self.assertEqual((before["sources"][0]["health"],
                          before["sources"][0]["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_STORE_FILE_ABSENT))
        self.assertFalse(os.path.exists(
            os.path.join(state, mission_store.MISSIONS_FILE_NAME)))
        _, _, mission_id = self.propose(state)
        after = query_module.query(self.registry, None, NOW)
        self.assertEqual(after["sources"][0]["health"], contract.HEALTH_FRESH)
        self.assertEqual([m["native_id"] for m in after["missions"]],
                         [mission_id])

    def test_store_existing_then_disappeared_then_restored(self):
        state = self.mission_state()
        self.running_mission(state)
        self.register(source_id="mc", store=state)
        path = os.path.join(state, mission_store.MISSIONS_FILE_NAME)
        os.rename(path, path + ".away")
        gone = query_module.query(self.registry, None, NOW)
        self.assertEqual((gone["sources"][0]["health"],
                          gone["sources"][0]["problem"],
                          gone["sources"][0]["record_count"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_STORE_FILE_ABSENT, 0))
        self.assertEqual(gone["missions"], [])
        self.assertFalse(os.path.exists(path))
        os.rename(path + ".away", path)
        back = query_module.query(self.registry, None, NOW)
        self.assertEqual(back["sources"][0]["health"], contract.HEALTH_FRESH)
        self.assertEqual(len(back["missions"]), 1)

    def test_workflow_store_disappeared_is_unavailable(self):
        wf_dir = self.store_dir("wf")
        self.workflow(wf_dir, workspace=os.path.realpath(
            self.store_dir("wf-ws")))
        self.registry.register("grok-wf", contract.KIND_WORKFLOW_STORE, wf_dir,
                               "grok_bot", 60, NOW)
        os.remove(os.path.join(wf_dir, workflow_store.WORKFLOWS_FILE_NAME))
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["sources"][0]["problem"],
                         sources_module.PROBLEM_STORE_FILE_ABSENT)
        self.assertEqual(response["missions"], [])


# -- aggregate bounds act before expansion (F7) ---------------------------

class BudgetTests(StoreFixture):

    def test_every_limit_is_exposed(self):
        self.register()
        limits = query_module.query(self.registry, None, NOW)["limits"]
        self.assertEqual(limits, {
            "max_sources": 128, "max_registry_bytes": 262144,
            "max_json_depth": 64,
            "max_store_read_bytes": 16777216,
            "max_query_input_bytes": 67108864, "max_query_missions": 2048,
            "max_snapshot_tasks": 256, "max_task_bindings": 32,
            "max_snapshot_agents": 512, "min_poll_interval_seconds": 5,
        })

    def two_stores(self):
        first, second = self.mission_state("a"), self.mission_state("b")
        self.running_mission(first)
        self.running_mission(second, task_id="task-2")
        self.registry.register("mc-a", contract.KIND_MISSION_CORE, first,
                               "fixture", 60, NOW)
        self.registry.register("mc-b", contract.KIND_MISSION_CORE, second,
                               "fixture", 60, NOW)
        return os.path.getsize(os.path.join(first,
                                            mission_store.MISSIONS_FILE_NAME))

    def test_input_budget_refuses_the_overflowing_store_explicitly(self):
        first_size = self.two_stores()
        with mock.patch.object(sources_module, "MAX_QUERY_INPUT_BYTES",
                               first_size + 16):
            response = query_module.query(self.registry, None, NOW)
        health = {s["source_id"]: (s["health"], s["problem"])
                  for s in response["sources"]}
        self.assertEqual(health["mc-a"], (contract.HEALTH_FRESH, None))
        self.assertEqual(health["mc-b"],
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))
        self.assertEqual({m["source_id"] for m in response["missions"]},
                         {"mc-a"})

    def test_mission_budget_refuses_before_building_views(self):
        state = self.mission_state()
        self.running_mission(state)
        self.propose(state, "Another")
        self.register(source_id="mc", store=state)
        with mock.patch.object(sources_module, "MAX_QUERY_MISSIONS", 1), \
                mock.patch.object(sources_module, "_mission_core_view",
                                  side_effect=AssertionError("expanded")):
            response = query_module.query(self.registry, None, NOW)
        self.assertEqual((response["sources"][0]["health"],
                          response["sources"][0]["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_MISSION_BUDGET))
        self.assertEqual(response["missions"], [])

    def test_per_store_bound_is_explicit(self):
        state = self.mission_state()
        self.running_mission(state)
        self.register(source_id="mc", store=state)
        with mock.patch.object(sources_module, "MAX_STORE_READ_BYTES", 64):
            response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["sources"][0]["problem"],
                         sources_module.PROBLEM_STORE_TOO_LARGE)


# -- readiness through Mission Core's own path ------------------------------

READINESS_KEYS = {
    "mission_sources": "di-mission-sources",
    "runtime_snapshot": "herdr-snapshot",
    "app_compatibility": "original-munder-compatibility",
}


@NEEDS_READINESS
class ReadinessTests(StoreFixture):

    def readiness_mission(self, state):
        from test_local_request import contract as base_contract
        proof = base_contract()
        proof["required_resource_readiness"] = [
            {"resource_key": key, "max_age_seconds": 60}
            for key in sorted(READINESS_KEYS.values())]
        surface = surface_module.build_bridgeless_surface(state, self.clock)
        out = surface.submit(mission_request(objective="Readiness fixture",
                                             proof_contract=proof))
        self.authorize(surface, out["request_ref"])
        missions = mission_service.MissionService(
            mission_store.MissionStore(state), self.clock)
        mission_id = out["mission_id"]
        missions.activate_proof_contract(
            mission_id, missions.mint_state_operation_id(AUTHENTICATED),
            missions.get_state(mission_id)["sequence"], AUTHENTICATED)
        return missions, mission_id

    def response(self, state, snapshot_at, now):
        self.register(source_id="mc", store=state)
        return query_module.query(
            self.registry, snapshot(snapshot_at, tasks=[], agents=[]), now)

    def compatible(self, observed_at, compatible=True):
        return {"observed_at": observed_at, "compatible": compatible,
                "detail": "fixture"}

    def test_assess_uses_each_datas_own_observation_time(self):
        state = self.mission_state()
        self.running_mission(state)
        now = self.clock.now
        assessment = readiness_module.assess(
            self.response(state, now - 10, now), self.compatible(now - 5), now)
        self.assertEqual(assessment, {
            "assessed_at": now,
            "mission_sources": {"status": "READY", "observed_at": now,
                                "expires_at": now + 60, "problem": None},
            "runtime_snapshot": {"status": "READY", "observed_at": now - 10,
                                 "expires_at": now + 50, "problem": None},
            "app_compatibility": {"status": "READY", "observed_at": now - 5,
                                  "expires_at": now + 55, "problem": None},
        })

    def test_degradations_are_not_ready_or_unknown(self):
        state = self.mission_state()
        now = self.clock.now
        # Unmaterialised store, stale snapshot, absent compatibility.
        assessment = readiness_module.assess(
            self.response(state, now - 61, now), None, now)
        self.assertEqual(assessment["mission_sources"]["status"], "NOT_READY")
        self.assertEqual(assessment["mission_sources"]["problem"],
                         readiness_module.PROBLEM_SOURCE_DEGRADED)
        self.assertEqual(assessment["runtime_snapshot"]["status"], "NOT_READY")
        self.assertEqual(assessment["app_compatibility"]["status"], "UNKNOWN")
        for value, problem in (
                (self.compatible(now - 61), contract.PROBLEM_OBSERVED_TOO_OLD),
                (self.compatible(now + 1), contract.PROBLEM_OBSERVED_IN_FUTURE),
                (self.compatible(now, compatible=False),
                 readiness_module.PROBLEM_COMPATIBILITY_FALSE),
                ({"observed_at": now, "compatible": 1, "detail": ""},
                 readiness_module.PROBLEM_COMPATIBILITY_SHAPE)):
            slot = readiness_module.assess(
                self.response(state, now, now), value,
                now)["app_compatibility"]
            self.assertEqual((slot["status"], slot["problem"]),
                             ("NOT_READY", problem))
        # A response a consumer would refuse makes every response slot
        # NOT_READY.
        stale = self.response(state, now, now - 100)
        assessment = readiness_module.assess(stale, None, now)
        self.assertEqual(assessment["mission_sources"]["status"], "NOT_READY")
        self.assertEqual(assessment["runtime_snapshot"]["status"], "NOT_READY")
        unregistered = query_module.query(
            registry_module.SourceRegistry(os.path.join(self.root, "none")),
            None, now)
        self.assertEqual(readiness_module.assess(
            unregistered, None, now)["mission_sources"]["status"], "NOT_READY")

    def test_reported_through_mission_core_and_evaluated_by_it(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        assessment = readiness_module.assess(
            self.response(state, now - 10, now), self.compatible(now - 5), now)
        operation_ids = [missions.mint_state_operation_id(AUTHENTICATED)
                         for _ in readiness_module.SLOTS]
        readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, operation_ids,
            missions.get_state(mission_id)["sequence"], AUTHENTICATED)
        projection = missions.get_state(mission_id)
        self.assertEqual(projection["readiness"], {
            "satisfied": True,
            "resources": {key: "READY" for key in READINESS_KEYS.values()}})
        recorded = {o["resource_key"]: o["observed_at"]
                    for o in missions.get_state(mission_id)["record"][
                        "resource_readiness"]}
        self.assertEqual(recorded["herdr-snapshot"], now - 10)
        # 51 s later the runtime data is 61 s old: Mission Core's own
        # evaluator, not this package, says NOT_READY for that key only.
        self.clock.now = now + 51
        later = missions.get_state(mission_id)["readiness"]
        self.assertFalse(later["satisfied"])
        self.assertEqual(later["resources"]["herdr-snapshot"], "NOT_READY")
        self.assertEqual(later["resources"]["di-mission-sources"], "READY")

    def test_evaluate_is_mission_cores_own_evaluator(self):
        sentinel = object()
        with mock.patch.object(readiness_module.mission_progress, "readiness",
                               return_value=sentinel) as evaluator:
            self.assertIs(readiness_module.evaluate({"c": 1}, {"s": 1}, 7),
                          sentinel)
        evaluator.assert_called_once_with({"c": 1}, {"s": 1}, 7)

    def test_key_map_and_operation_ids_are_required(self):
        for bad in ({}, dict(READINESS_KEYS, extra="x"),
                    dict(READINESS_KEYS, mission_sources="")):
            with self.assertRaises(contract.ContractError):
                readiness_module.report_readiness(
                    None, "mn-x", {}, bad, ["a", "b", "c"], 1, None)
        with self.assertRaises(contract.ContractError):
            readiness_module.report_readiness(
                None, "mn-x", {}, READINESS_KEYS, ["a"], 1, None)


# -- round-2 review findings ------------------------------------------------

class ExpiryTests(StoreFixture):
    """Finding 1: an envelope never outlives what it presents as live."""

    def fresh_store(self, max_age=60):
        state = self.mission_state()
        _, workspace = self.running_mission(state)
        self.registry.register("mc", contract.KIND_MISSION_CORE, state,
                               "fixture", max_age, NOW)
        return workspace

    def test_envelope_expires_with_an_almost_expired_snapshot(self):
        workspace = self.fresh_store()
        runtime = snapshot(NOW - 59, tasks=[task("task-0001", workspace)])
        response = query_module.query(self.registry, runtime, NOW)
        self.assertEqual(response["valid_until"], NOW + 1)
        self.assertEqual(response["missions"][0]["members"][0]["classification"],
                         contract.MEMBER_PRESENT)
        self.assertEqual(contract.response_freshness(response, NOW + 1),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(contract.response_freshness(response, NOW + 2),
                         (contract.HEALTH_STALE,
                          contract.PROBLEM_RESPONSE_EXPIRED))
        # The snapshot's readiness slot follows the snapshot's own expiry;
        # the sources slot is judged by the sources' own bounds.
        if readiness_module is None:
            return      # readiness not landed in the read-only subset
        later = readiness_module.assess(response, None, NOW + 2)
        self.assertEqual(later["runtime_snapshot"]["status"], "NOT_READY")
        self.assertEqual(later["mission_sources"]["status"], "READY")

    def test_shorter_source_bound_constrains_the_envelope(self):
        self.fresh_store(max_age=10)
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["max_age_seconds"], 60)
        self.assertEqual(response["valid_until"], NOW + 10)
        self.assertEqual(contract.response_freshness(response, NOW + 11)[0],
                         contract.HEALTH_STALE)

    def test_degraded_inputs_present_nothing_live_and_do_not_shorten(self):
        self.fresh_store()
        for value in (None, snapshot(NOW - 61), {"schema": "x"}):
            response = query_module.query(self.registry, value, NOW)
            self.assertEqual(response["valid_until"], NOW + 60)
            self.assertEqual(response["missions"][0]["members"], [])

    def test_valid_until_shape_is_enforced(self):
        base = contract.envelope(NOW, 60, NOW + 30, {}, {"health": "fresh"},
                                 [], {}, [], 0)
        for value in (NOW - 1, NOW + 61, True, None, "1"):
            broken = dict(base, valid_until=value)
            self.assertEqual(contract.response_freshness(broken, NOW)[0],
                             contract.HEALTH_MALFORMED, value)
        for value in (NOW - 1, NOW + 61):
            with self.assertRaises(contract.ContractError):
                contract.envelope(NOW, 60, value, {}, {"health": "fresh"},
                                  [], {}, [], 0)


class HostileInputTests(StoreFixture):
    """Finding 2: byte bounds do not bound parse recursion; every malformed
    shape fails explicitly and boundedly."""

    def test_bounded_json_parse(self):
        depth = fileread.MAX_JSON_DEPTH
        ok = ("[" * depth + "]" * depth).encode()
        self.assertEqual(fileread.load_bounded_json(ok)[0:0], [])
        cases = [
            (("[" * (depth + 1) + "]" * (depth + 1)).encode(),
             fileread.PROBLEM_JSON_TOO_DEEP),
            (("[" * 200000).encode(), fileread.PROBLEM_JSON_TOO_DEEP),
            (('{"a":' * 200000).encode(), fileread.PROBLEM_JSON_TOO_DEEP),
            (b"\xff\xfe", fileread.PROBLEM_JSON_NOT_UTF8),
            (b"{broken", fileread.PROBLEM_JSON_INVALID),
        ]
        for data, problem in cases:
            with self.assertRaises(fileread.JsonRefused) as caught:
                fileread.load_bounded_json(data)
            self.assertEqual(caught.exception.problem, problem)
        # Brackets and escaped quotes inside strings are not nesting.
        quoted = json.dumps({"a": "[" * 500 + '\\"{' * 100}).encode()
        self.assertEqual(set(fileread.load_bounded_json(quoted)), {"a"})

    def test_deeply_nested_registry_is_explicitly_malformed(self):
        os.makedirs(self.directory, mode=0o700)
        for body in ("[" * 100000 + "]" * 100000,
                     json.dumps({registry_module.REGISTRY_VERSION_KEY: 1,
                                 "sources": [{"source_id": [[[[[[1]]]]]]}]})):
            with open(self.registry.path, "w") as handle:
                handle.write(body)
            os.chmod(self.registry.path, 0o600)
            with self.assertRaises(registry_module.RegistryError) as caught:
                self.registry.load()
            self.assertEqual(caught.exception.problem,
                             registry_module.PROBLEM_REGISTRY_MALFORMED)
            response = query_module.query(self.registry, None, NOW)
            self.assertEqual(response["registry"]["health"],
                             contract.HEALTH_MALFORMED)
            self.assertEqual(response["missions"], [])

    def test_deeply_nested_and_mistyped_stores_are_explicitly_malformed(self):
        state = self.mission_state()
        self.running_mission(state)
        path = os.path.join(state, mission_store.MISSIONS_FILE_NAME)
        with open(path, encoding="utf-8") as handle:
            valid = json.load(handle)
        reader = lambda: sources_module.read_mission_core(
            self.source("mc", contract.KIND_MISSION_CORE, state), NOW,
            sources_module.QueryBudget())
        mistyped = copy.deepcopy(valid)
        mission_id = next(iter(mistyped["missions"]))
        mistyped["missions"][mission_id]["run"] = [[[["not", "a", "run"]]]]
        renested = copy.deepcopy(valid)
        renested["missions"][mission_id]["revisions"] = {"0": {"1": {"2": 3}}}
        cases = [
            ('{"mission_store_schema_version": 1, "missions": '
             + "[" * 100000 + "]" * 100000 + "}",
             sources_module.PROBLEM_STORE_TOO_DEEP),
            (json.dumps(mistyped), sources_module.PROBLEM_STORE_REFUSED),
            (json.dumps(renested), sources_module.PROBLEM_STORE_REFUSED),
        ]
        for body, problem in cases:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            reading = reader()
            self.assertEqual((reading["health"], reading["problem"]),
                             (contract.HEALTH_MALFORMED, problem))
            self.assertEqual(reading["missions"], [])

    def test_invalid_snapshot_keys_and_nested_types_are_malformed(self):
        cyclic = []
        cyclic.append(cyclic)
        mutations = [
            lambda s: s.update({1: "x"}),
            # reviewer1's reproduction: an int key next to a str unknown key
            # (unorderable together) must not escape as a TypeError.
            lambda s: s.update({1: "x", "extra": "y"}),
            lambda s: s["tasks"][0].update({2: "x", "status": "y"}),
            lambda s: s["agents"][0].update({3.5: "x", "cost": 0}),
            lambda s: s.update({("tuple", "key"): "x"}),
            lambda s: s["tasks"][0].update({None: 1}),
            lambda s: s["tasks"][0]["bindings"].append([[[[[[["deep"]]]]]]]),
            lambda s: s["tasks"].__setitem__(0, "a task"),
            lambda s: s.update(tasks=cyclic),
            lambda s: s["agents"][0].update(status={"nested": {"x": 1}}),
            lambda s: s.update(producer=["p"]),
            lambda s: s["agents"].__setitem__(0, collections.OrderedDict(
                live_agent())),
        ]
        for mutate in mutations:
            value = snapshot()
            mutate(value)
            reading = contract.read_runtime_snapshot(value, NOW)
            self.assertEqual(reading.health, contract.HEALTH_MALFORMED)
            self.assertEqual((reading.tasks, reading.agents), ((), ()))
        self.assertEqual(contract.read_runtime_snapshot(
            collections.OrderedDict(snapshot()), NOW).health,
            contract.HEALTH_MALFORMED)

    def test_a_failing_reader_degrades_only_its_source(self):
        good = self.mission_state("good")
        self.running_mission(good)
        self.registry.register("mc-good", contract.KIND_MISSION_CORE, good,
                               "fixture", 60, NOW)
        self.registry.register("wf-bad", contract.KIND_WORKFLOW_STORE,
                               self.store_dir("wf"), "fixture", 60, NOW)
        for broken in (lambda source, now, budget: 1 / 0,
                       lambda source, now, budget: None,
                       lambda source, now, budget: {"health": "fresh"}):
            readers = dict(sources_module.READERS)
            readers[contract.KIND_WORKFLOW_STORE] = broken
            response = query_module.query(self.registry, None, NOW,
                                          readers=readers)
            health = {s["source_id"]: (s["health"], s["problem"])
                      for s in response["sources"]}
            self.assertEqual(health["wf-bad"],
                             (contract.HEALTH_MALFORMED,
                              sources_module.PROBLEM_READER_FAILED))
            self.assertEqual(health["mc-good"][0], contract.HEALTH_FRESH)
            self.assertEqual({m["source_id"] for m in response["missions"]},
                             {"mc-good"})

    @NEEDS_READINESS
    def test_malformed_response_shapes_do_not_crash_readiness(self):
        self.register()
        response = query_module.query(self.registry, None, NOW)
        for sources in ("x", [None], [1, {"health": "fresh"}]):
            broken = dict(response, sources=sources)
            slot = readiness_module.assess(broken, None, NOW)["mission_sources"]
            self.assertEqual(slot["status"], "NOT_READY")


class RegistrySizeTests(RegistryFixture):
    """Finding 4: the serialized-size dimension is contained explicitly."""

    def long_store(self, index):
        return os.path.join(self.root, "%03d" % index,
                            *(["\u00e9" * 100] * 9))

    def test_oversize_registration_is_refused_before_any_write(self):
        index = 0
        producer = "\u00e9" * registry_module.MAX_PRODUCER_CHARS
        while True:
            before = None
            if os.path.exists(self.registry.path):
                with open(self.registry.path, "rb") as handle:
                    before = handle.read()
            try:
                self.registry.register("big-%03d" % index,
                                       contract.KIND_MISSION_CORE,
                                       self.long_store(index), producer, 60,
                                       NOW)
            except registry_module.RegistryError as exc:
                self.assertEqual(exc.problem,
                                 registry_module.PROBLEM_REGISTRY_SIZE)
                break
            index += 1
            self.assertLess(index, registry_module.MAX_REGISTERED_SOURCES,
                            "size bound never reached")
        with open(self.registry.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        document = self.registry.load()
        self.assertEqual(len(document["sources"]), index)
        self.assertLessEqual(len(before), registry_module.MAX_REGISTRY_BYTES)
        self.assertEqual(registry_module.serialized_size(document), len(before))
        response = query_module.query(self.registry, None, NOW)
        self.assertEqual(response["registry"]["serialized_bytes"], len(before))
        self.assertEqual(response["registry"]["max_serialized_bytes"],
                         registry_module.MAX_REGISTRY_BYTES)
        self.assertEqual(len(response["sources"]), index)


@NEEDS_READINESS
class ReadinessExpiryTests(ReadinessTests):
    """Finding 1, readiness half, decided design: the horizon is enforced
    OUTSIDE Mission Core by REFUSING to publish READY, never by encoding a
    time. Every assertion that matters inspects Mission Core's DURABLE
    record and its own, unchanged evaluator."""

    def durable(self, missions, mission_id):
        return {o["resource_key"]: o for o in missions.get_state(mission_id)[
            "record"]["resource_readiness"]}

    def publish(self, missions, mission_id, assessment):
        operation_ids = [missions.mint_state_operation_id(AUTHENTICATED)
                         for _ in readiness_module.SLOTS]
        return readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, operation_ids,
            missions.get_state(mission_id)["sequence"], AUTHENTICATED)

    def resources(self, missions, mission_id):
        return missions.get_state(mission_id)["readiness"]["resources"]

    def test_normal_configuration_publishes_ready_with_actual_times(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        response = self.response(state, now - 10, now)
        report = self.publish(missions, mission_id, readiness_module.assess(
            response, self.compatible(now - 5), now))
        self.assertEqual([r["published_status"] for r in report],
                         ["READY", "READY", "READY"])
        record = self.durable(missions, mission_id)
        actual = {"di-mission-sources": now, "herdr-snapshot": now - 10,
                  "original-munder-compatibility": now - 5}
        for key, observed_at in actual.items():
            self.assertEqual(record[key]["status"], "READY")
            # The durable observed_at is the ACTUAL observation time and the
            # durable record has exactly Mission Core's own keys.
            self.assertEqual(record[key]["observed_at"], observed_at)
            self.assertEqual(set(record[key]),
                             set(mission_state.READINESS_OBSERVATION_KEYS))
        self.assertEqual(response["generated_at"], now)
        self.assertEqual(response["sources"][0]["observed_at"], now)
        self.assertTrue(missions.get_state(mission_id)["readiness"]["satisfied"])
        # Mission Core's own age check retires each at observed_at + 60,
        # which here never exceeds that data's inner horizon.
        self.clock.now = now + 50
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "READY")
        self.clock.now = now + 51
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "NOT_READY")
        self.assertEqual(self.resources(missions, mission_id)[
            "di-mission-sources"], "READY")

    def test_short_source_bound_is_refused_with_its_reason(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        self.registry.register("mc", contract.KIND_MISSION_CORE, state,
                               "fixture", 10, now)
        response = query_module.query(
            self.registry, snapshot(now, tasks=[], agents=[]), now)
        assessment = readiness_module.assess(response,
                                             self.compatible(now), now)
        # Assessed READY with actual time and explicit expiry ...
        self.assertEqual(assessment["mission_sources"],
                         {"status": "READY", "observed_at": now,
                          "expires_at": now + 10, "problem": None})
        # ... and the assessment itself turns NOT_READY once that source's
        # own bound has passed (exact boundary: +10 still within, +11 not).
        self.assertEqual(readiness_module.assess(
            response, self.compatible(now), now + 10)["mission_sources"][
                "status"], "READY")
        self.assertEqual(readiness_module.assess(
            response, self.compatible(now), now + 11)["mission_sources"][
                "problem"], readiness_module.PROBLEM_SOURCE_EXPIRED)
        report = self.publish(missions, mission_id, assessment)
        sources_entry = report[0]
        # ... but REFUSED at publication, with a distinguishable reason.
        self.assertEqual(
            (sources_entry["assessed_status"], sources_entry["published_status"],
             sources_entry["problem"], sources_entry["contract_bound"]),
            ("READY", "NOT_READY",
             readiness_module.PROBLEM_HORIZON_SHORTER_THAN_BOUND, 60))
        record = self.durable(missions, mission_id)
        self.assertEqual(record["di-mission-sources"]["status"], "NOT_READY")
        # The refusal's durable observed_at is the actual refusal time.
        self.assertEqual(record["di-mission-sources"]["observed_at"],
                         missions.now())
        # Readable is not READY: the same source is fresh and its mission is
        # listed by the query, it just cannot be represented as READY.
        self.assertEqual(response["sources"][0]["health"],
                         contract.HEALTH_FRESH)
        self.assertEqual(len(response["missions"]), 1)
        # Never READY at any point, while the other resources are.
        for offset in (0, 5, 10, 11):
            self.clock.now = now + offset
            self.assertEqual(self.resources(missions, mission_id)[
                "di-mission-sources"], "NOT_READY", offset)
        self.clock.now = now
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "READY")

    def test_short_snapshot_bound_is_refused(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        self.register(source_id="mc", store=state)
        response = query_module.query(
            self.registry, snapshot(now, tasks=[], agents=[]), now,
            max_age_seconds=30)
        report = self.publish(missions, mission_id, readiness_module.assess(
            response, self.compatible(now), now))
        by_slot = {r["slot"]: r for r in report}
        for slot in ("mission_sources", "runtime_snapshot"):
            self.assertEqual(
                (by_slot[slot]["published_status"], by_slot[slot]["problem"]),
                ("NOT_READY",
                 readiness_module.PROBLEM_HORIZON_SHORTER_THAN_BOUND), slot)
        self.assertEqual(by_slot["app_compatibility"]["published_status"],
                         "READY")

    def test_horizon_equal_to_bound_is_ready_until_exactly_the_horizon(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        response = self.response(state, now - 59, now)
        report = self.publish(missions, mission_id, readiness_module.assess(
            response, self.compatible(now), now))
        snapshot_entry = report[1]
        self.assertEqual((snapshot_entry["published_status"],
                          snapshot_entry["observed_at"],
                          snapshot_entry["expires_at"]),
                         ("READY", now - 59, now + 1))
        self.assertEqual(self.durable(missions, mission_id)[
            "herdr-snapshot"]["observed_at"], now - 59)
        self.clock.now = now + 1
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "READY")
        self.clock.now = now + 2
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "NOT_READY")

    def test_a_late_write_never_extends_freshness(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        assessment = readiness_module.assess(
            self.response(state, now - 59, now), self.compatible(now), now)
        # Written 5 s after the decision, i.e. after the snapshot's horizon.
        self.clock.now = now + 5
        report = self.publish(missions, mission_id, assessment)
        self.assertEqual((report[1]["published_status"],
                          report[1]["observed_at"]), ("READY", now - 59))
        record = self.durable(missions, mission_id)
        self.assertEqual(record["herdr-snapshot"]["observed_at"], now - 59)
        # The durable observation keeps its ORIGINAL time, so Mission Core's
        # own evaluator already says NOT_READY: no freshness was extended.
        self.assertEqual(self.resources(missions, mission_id)["herdr-snapshot"],
                         "NOT_READY")

    def test_delay_within_the_horizon_publishes_the_actual_time(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        assessment = readiness_module.assess(
            self.response(state, now - 10, now), self.compatible(now), now)
        self.clock.now = now + 5
        report = self.publish(missions, mission_id, assessment)
        self.assertEqual([r["published_status"] for r in report],
                         ["READY", "READY", "READY"])
        record = self.durable(missions, mission_id)
        # The query happened at `now`; the delay does not move the time.
        self.assertEqual(record["di-mission-sources"]["observed_at"], now)
        self.assertEqual(record["herdr-snapshot"]["observed_at"], now - 10)

    def test_reload_and_native_idempotency_preserve_the_durable_record(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        now = self.clock.now
        assessment = readiness_module.assess(
            self.response(state, now - 10, now), self.compatible(now), now)
        operation_ids = [missions.mint_state_operation_id(AUTHENTICATED)
                         for _ in readiness_module.SLOTS]
        sequence = missions.get_state(mission_id)["sequence"]
        readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, operation_ids,
            sequence, AUTHENTICATED)
        first = missions.get_state(mission_id)["record"]["resource_readiness"]
        # Identical replay: idempotent, nothing appended.
        replay = readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, operation_ids,
            sequence, AUTHENTICATED)
        self.assertTrue(all(r["writer_result"].get("idempotent")
                            for r in replay))
        self.assertEqual(missions.get_state(mission_id)["record"][
            "resource_readiness"], first)
        # A conflicting replay of the same operation ids is refused.
        altered = copy.deepcopy(assessment)
        altered["runtime_snapshot"] = {"status": "NOT_READY",
                                       "observed_at": now, "expires_at": None,
                                       "problem": "fixture"}
        with self.assertRaises(mission_record.MissionError):
            readiness_module.report_readiness(
                missions, mission_id, altered, READINESS_KEYS, operation_ids,
                sequence, AUTHENTICATED)
        self.assertEqual(missions.get_state(mission_id)["record"][
            "resource_readiness"], first)
        # Reload: a fresh service over the same store reads the same durable
        # record and its own evaluator gives the same answer.
        reloaded = mission_service.MissionService(
            mission_store.MissionStore(state), self.clock)
        self.assertEqual(reloaded.get_state(mission_id)["record"][
            "resource_readiness"], first)
        self.assertEqual(reloaded.get_state(mission_id)["readiness"],
                         missions.get_state(mission_id)["readiness"])
        self.assertTrue(reloaded.get_state(mission_id)["readiness"]["satisfied"])

    def test_publication_rule_never_publishes_ready_past_the_horizon(self):
        bound = 60
        for observed in range(0, 40, 13):
            for horizon_gap in (0, 10, 59, 60, 61, 600):
                for now_gap in (0, 30, 60, 61, 700):
                    entry = {"status": "READY", "observed_at": 1000 + observed,
                             "expires_at": 1000 + observed + horizon_gap,
                             "problem": None}
                    now = 1000 + observed + now_gap
                    status, observed_at, _ = readiness_module.publication(
                        entry, bound, now)
                    if status == "READY":
                        # Actual time, and Mission Core's age check (READY
                        # until observed_at + bound) ends by the horizon.
                        self.assertEqual(observed_at, entry["observed_at"])
                        self.assertLessEqual(observed_at + bound,
                                             entry["expires_at"])
                        self.assertLessEqual(now, entry["expires_at"])
                    else:
                        self.assertEqual(observed_at, now)
        # Contract bound unknown (key not required) is refused, never guessed.
        entry = {"status": "READY", "observed_at": 1000, "expires_at": 2000,
                 "problem": None}
        self.assertEqual(readiness_module.publication(entry, None, 1000),
                         ("NOT_READY", 1000,
                          readiness_module.PROBLEM_CONTRACT_BOUND_UNKNOWN))


# -- round-3 review findings ------------------------------------------------

class WorkflowEligibilityTests(StoreFixture):
    """Round-3 finding 1: WorkflowStore membership = the store's own
    non-terminal eligibility PLUS the canonical execution chain."""

    def setUp(self):
        super(WorkflowEligibilityTests, self).setUp()
        self.wf_dir = self.store_dir("wf")
        self.workspace = os.path.realpath(self.store_dir("wf-ws"))
        self.registry.register("grok-wf", contract.KIND_WORKFLOW_STORE,
                               self.wf_dir, "grok_bot", 60, NOW)

    def runtime(self):
        return snapshot(NOW, tasks=[task("task-0002", self.workspace)])

    def by_native(self):
        response = query_module.query(self.registry, self.runtime(), NOW)
        return {m["native_id"]: m for m in response["missions"]}, response

    def test_standing_matches_the_stores_own_is_active(self):
        for phase in workflow_record.PHASES:
            record = make_record()
            record["phase"] = phase
            native_active = workflow_store.is_active(record)
            standing = sources_module.standing_of(
                sources_module.WORKFLOW_STORE_STANDING, phase)
            self.assertEqual(standing == contract.STANDING_ACTIVE,
                             native_active, phase)
            self.assertEqual(standing == contract.STANDING_TERMINAL,
                             not native_active, phase)

    def test_reauthorization_keeps_a_retained_binding(self):
        record = self.workflow(
            self.wf_dir, phase=workflow_record.PHASE_NEEDS_REAUTHORIZATION,
            workspace=self.workspace)
        self.assertTrue(workflow_store.is_active(record))
        missions, response = self.by_native()
        mission = missions["wf-0002"]
        self.assertEqual(mission["lifecycle"]["state"],
                         workflow_record.PHASE_NEEDS_REAUTHORIZATION)
        self.assertEqual(mission["lifecycle"]["standing"],
                         contract.STANDING_ACTIVE)
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual([m["classification"] for m in mission["members"]],
                         [contract.MEMBER_PRESENT])
        self.assertEqual(response["unmatched_runtime_agent_count"], 0)

    def test_eligibility_alone_never_creates_members(self):
        cases = {
            # (workflow_id, phase, task_id, workspace, released) ->
            #   expected (binding state, reason)
            ("wf-0010", workflow_record.PHASE_PLANNED, None, None, False):
                (contract.BINDING_UNBOUND, roster.REASON_NOT_STARTED),
            ("wf-0011", workflow_record.PHASE_AUTHORIZED, None, None, False):
                (contract.BINDING_UNBOUND, roster.REASON_NOT_STARTED),
            ("wf-0012", workflow_record.PHASE_NEEDS_REAUTHORIZATION, None,
             None, False):
                (contract.BINDING_UNBOUND, roster.REASON_NOT_STARTED),
            ("wf-0013", workflow_record.PHASE_NEEDS_REAUTHORIZATION,
             "task-0002", "ws", True):
                (contract.BINDING_UNBOUND, roster.REASON_WORKSPACE_UNRECORDED),
            ("wf-0014", workflow_record.PHASE_COMPLETED, "task-0002", "ws",
             False):
                (contract.BINDING_NOT_APPLICABLE, roster.REASON_NOT_ACTIVE),
            ("wf-0015", workflow_record.PHASE_BLOCKED, "task-0002", "ws",
             False):
                (contract.BINDING_NOT_APPLICABLE, roster.REASON_NOT_ACTIVE),
        }
        for (workflow_id, phase, task_id, workspace, released), _ in \
                cases.items():
            self.workflow(self.wf_dir, workflow_id=workflow_id, phase=phase,
                          task_id=task_id,
                          workspace=self.workspace if workspace else None,
                          released=released)
        missions, response = self.by_native()
        for (workflow_id, phase, _, _, _), expected in cases.items():
            mission = missions[workflow_id]
            self.assertEqual((mission["binding"]["state"],
                              mission["binding"]["reason"]), expected,
                             (workflow_id, phase))
            self.assertEqual(mission["members"], [], workflow_id)
        # The runtime agent belongs to no bound task here: unrelated.
        self.assertEqual(response["unmatched_runtime_agent_count"], 1)


class InterruptingService(object):
    """Delegates to a real MissionService and raises after ``allowed``
    readiness writes — a report interrupted part-way."""

    def __init__(self, real, allowed):
        self._real = real
        self._allowed = allowed

    def __getattr__(self, name):
        return getattr(self._real, name)

    def observe_resource_readiness(self, *args, **kwargs):
        if self._allowed <= 0:
            raise OSError("synthetic: interrupted before this slot")
        self._allowed -= 1
        return self._real.observe_resource_readiness(*args, **kwargs)


@NEEDS_READINESS
class RetryIdempotencyTests(StoreFixture):
    """Round-3 finding 2: an EXACT retry of a report succeeds after the clock
    advances, while a retry under the same ids with CHANGED content or
    context is still refused by the unchanged native conflict checks — both
    directions in the same pass. Freshness never extends."""

    # Helper functions borrowed (not inherited) so no test runs twice.
    readiness_mission = ReadinessTests.readiness_mission
    compatible = ReadinessTests.compatible
    resources = ReadinessExpiryTests.resources

    def setUp(self):
        super(RetryIdempotencyTests, self).setUp()
        self.state_dir = self.mission_state()
        self.missions, self.mission_id = self.readiness_mission(self.state_dir)
        self.t0 = self.clock.now
        # Sources at a 10 s bound (refused), a 59 s-old snapshot (READY until
        # exactly +1), a fresh compatibility observation (READY).
        self.registry.register("mc", contract.KIND_MISSION_CORE,
                               self.state_dir, "fixture", 10, self.t0)
        response = query_module.query(
            self.registry, snapshot(self.t0 - 59, tasks=[], agents=[]),
            self.t0)
        self.assessment = readiness_module.assess(
            response, self.compatible(self.t0), self.t0)
        self.ids = [self.missions.mint_state_operation_id(AUTHENTICATED)
                    for _ in readiness_module.SLOTS]
        self.sequence = self.missions.get_state(self.mission_id)["sequence"]

    def report(self, service=None, assessment=None, keys=None,
               context=AUTHENTICATED):
        return readiness_module.report_readiness(
            service or self.missions, self.mission_id,
            assessment or self.assessment, keys or READINESS_KEYS, self.ids,
            self.sequence, context)

    def records(self):
        return copy.deepcopy(self.missions.get_state(self.mission_id)[
            "record"]["resource_readiness"])

    def refused(self, problem, **kwargs):
        before = self.records()
        with self.assertRaises(mission_record.MissionError) as caught:
            self.report(**kwargs)
        self.assertEqual(caught.exception.problem, problem)
        self.assertEqual(self.records(), before)

    def test_exact_retry_succeeds_and_changed_input_is_refused(self):
        from mission import state_service
        first = self.report()
        self.assertEqual([r["published_status"] for r in first],
                         ["NOT_READY", "READY", "READY"])
        durable = self.records()
        self.assertEqual([o["observed_at"] for o in durable],
                         [self.t0, self.t0 - 59, self.t0])
        # The clock advances across the snapshot's horizon AND past the
        # refusal's decision time; the EXACT retry is idempotent and changes
        # nothing durable.
        for later in (self.t0 + 1, self.t0 + 2, self.t0 + 600):
            self.clock.now = later
            retry = self.report()
            self.assertTrue(all(r["writer_result"]["idempotent"]
                                for r in retry), later)
            self.assertEqual(
                [(r["published_status"], r["observed_at"]) for r in retry],
                [(r["published_status"], r["observed_at"]) for r in first])
            self.assertEqual(self.records(), durable, later)
        # Same pass, other direction: changed content under the same ids is
        # refused by the unchanged native conflict check ...
        changed = copy.deepcopy(self.assessment)
        changed["app_compatibility"]["observed_at"] -= 1
        self.refused(state_service.PROBLEM_STATE_OPERATION_CONFLICT,
                     assessment=changed)
        later_assessment = copy.deepcopy(self.assessment)
        later_assessment["assessed_at"] += 1
        self.refused(state_service.PROBLEM_STATE_OPERATION_CONFLICT,
                     assessment=later_assessment)
        swapped = dict(READINESS_KEYS,
                       mission_sources=READINESS_KEYS["app_compatibility"],
                       app_compatibility=READINESS_KEYS["mission_sources"])
        self.refused(state_service.PROBLEM_STATE_OPERATION_CONFLICT,
                     keys=swapped)
        # ... and a changed context is refused at the reservation.
        other = mission_record.AuthenticatedContext(
            transport="synthetic_test", principal_kind="local_process_user",
            principal_ref="someone-else")
        self.refused(state_service.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                     context=other)
        # Freshness never extended: Mission Core's own evaluator.
        self.clock.now = self.t0 + 2
        resources = self.resources(self.missions, self.mission_id)
        self.assertEqual(resources["herdr-snapshot"], "NOT_READY")
        self.assertEqual(resources["di-mission-sources"], "NOT_READY")

    def test_interrupted_report_recovers_later_without_refreshing(self):
        with self.assertRaises(OSError):
            self.report(service=InterruptingService(self.missions, 1))
        self.assertEqual(len(self.records()), 1)
        # Recovered 30 s later — well past the snapshot's horizon.
        self.clock.now = self.t0 + 30
        # A recovery with CHANGED content is refused at the slot already
        # written; nothing more is written ...
        changed = copy.deepcopy(self.assessment)
        changed["mission_sources"]["observed_at"] -= 1
        changed["assessed_at"] -= 1
        with self.assertRaises(mission_record.MissionError):
            self.report(assessment=changed)
        self.assertEqual(len(self.records()), 1)
        # ... while the EXACT recovery completes with the ORIGINAL decision.
        recovered = self.report()
        self.assertTrue(recovered[0]["writer_result"]["idempotent"])
        self.assertFalse(recovered[1]["writer_result"].get("idempotent", False))
        durable = self.records()
        self.assertEqual([(o["status"], o["observed_at"]) for o in durable],
                         [("NOT_READY", self.t0), ("READY", self.t0 - 59),
                          ("READY", self.t0)])
        # The late write did not refresh anything: the snapshot observation
        # keeps its original time and is NOT_READY now; nothing is READY past
        # its own horizon.
        resources = self.resources(self.missions, self.mission_id)
        self.assertEqual(resources["herdr-snapshot"], "NOT_READY")
        self.assertEqual(resources["di-mission-sources"], "NOT_READY")
        self.assertEqual(resources["original-munder-compatibility"], "READY")

    def test_assessment_without_decision_time_is_refused(self):
        broken = copy.deepcopy(self.assessment)
        del broken["assessed_at"]
        with self.assertRaises(contract.ContractError):
            self.report(assessment=broken)
        self.assertEqual(self.records(), [])


class RegistryRemovalSizeTests(RegistryFixture):
    """Round-3 finding 3: EVERY rewrite is size-guarded; a removal that would
    re-serialise beyond the bound is refused and preserves the prior bytes."""

    def write_compact_unicode_registry(self, count):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        sources = [{
            "source_id": "u-%03d" % i, "kind": contract.KIND_MISSION_CORE,
            "store_dir": os.path.join(self.root, "%03d" % i,
                                      *(["\u00e9" * 100] * 4)),
            "producer": "\u00e9" * 64,
            "max_age_seconds": 60, "registered_at": NOW,
        } for i in range(count)]
        document = {registry_module.REGISTRY_VERSION_KEY: 1, "sources": sources}
        data = json.dumps(document, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        with open(self.registry.path, "wb") as handle:
            handle.write(data)
        os.chmod(self.registry.path, 0o600)
        return data

    def test_removal_that_would_overflow_is_refused_and_preserves_bytes(self):
        data = self.write_compact_unicode_registry(
            registry_module.MAX_REGISTERED_SOURCES)
        self.assertLessEqual(len(data), registry_module.MAX_REGISTRY_BYTES)
        document, size = self.registry.load_measured()
        self.assertEqual(size, len(data))
        self.assertGreater(registry_module.serialized_size(document),
                           registry_module.MAX_REGISTRY_BYTES)
        with self.assertRaises(registry_module.RegistryError) as caught:
            self.registry.unregister("u-000")
        self.assertEqual(caught.exception.problem,
                         registry_module.PROBLEM_REGISTRY_SIZE)
        with open(self.registry.path, "rb") as handle:
            self.assertEqual(handle.read(), data)
        self.assertEqual(len(self.registry.load()["sources"]),
                         registry_module.MAX_REGISTERED_SOURCES)
        # Any other removal is refused the same way; bytes still preserved.
        with self.assertRaises(registry_module.RegistryError):
            self.registry.unregister("u-127")
        with open(self.registry.path, "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_ordinary_removal_still_works(self):
        self.register()
        self.register(source_id="mc-two")
        self.assertEqual(self.registry.unregister("mc-two")["outcome"],
                         registry_module.OUTCOME_REMOVED)
        self.assertEqual([s["source_id"] for s in self.registry.sources()],
                         ["mc-local"])

    def test_every_rewrite_goes_through_the_one_guarded_writer(self):
        calls = []
        real = registry_module.require_serialized_size

        def recording(document):
            calls.append(len(document["sources"]))
            return real(document)
        with mock.patch.object(registry_module, "require_serialized_size",
                               side_effect=recording):
            self.register()
            self.register(source_id="mc-two")
            self.registry.unregister("mc-two")
        self.assertEqual(calls, [1, 2, 1])


# -- round-4 review finding: replay across authorization expiry -------------

@NEEDS_READINESS
class AuthorizationExpiryReplayTests(StoreFixture):
    """Exact replay stays stable when the authorization expires, because the
    bounds come from the activation that governed the original sequence
    (Mission Core's own re-derivation), not from the liveness-filtered view.
    The unchanged native writer still refuses changed input, changed context
    and every UNCONSUMED operation once authority is no longer live."""

    readiness_mission = ReadinessTests.readiness_mission
    compatible = ReadinessTests.compatible

    def setUp(self):
        super(AuthorizationExpiryReplayTests, self).setUp()
        self.state_dir = self.mission_state()
        self.missions, self.mission_id = self.readiness_mission(self.state_dir)
        self.t0 = self.clock.now
        self.expires = self.missions.get(self.mission_id)[
            "authorizations"][0]["expires_at"]
        self.register(source_id="mc", store=self.state_dir)
        response = query_module.query(
            self.registry, snapshot(self.t0 - 59, tasks=[], agents=[]),
            self.t0)
        self.assessment = readiness_module.assess(
            response, self.compatible(self.t0), self.t0)
        self.ids = [self.missions.mint_state_operation_id(AUTHENTICATED)
                    for _ in readiness_module.SLOTS]
        self.sequence = self.missions.get_state(self.mission_id)["sequence"]
        self.contract = self.missions.get_state(self.mission_id)[
            "contract"]["content"]

    def report(self, service=None, assessment=None, context=AUTHENTICATED):
        return readiness_module.report_readiness(
            service or self.missions, self.mission_id,
            assessment or self.assessment, READINESS_KEYS, self.ids,
            self.sequence, context)

    def records(self):
        return copy.deepcopy(self.missions.get_state(self.mission_id)[
            "record"]["resource_readiness"])

    def native_readiness(self, now):
        # Mission Core's own evaluator over the durable record, judged by
        # the contract that governed it (the projection hides it once the
        # authorization expires).
        state = self.missions.get_state(self.mission_id)["record"]
        return mission_progress.readiness(self.contract, state, now)

    def test_governing_contract_equals_the_live_projection(self):
        self.assertIsNotNone(self.contract)
        self.assertEqual(readiness_module.governing_contract(
            self.missions, self.mission_id, self.sequence), self.contract)

    def test_exact_replay_across_expiry_and_changed_input_refused(self):
        from mission import state_service
        first = self.report()
        durable = self.records()
        self.assertEqual([o["status"] for o in durable],
                         ["READY", "READY", "READY"])
        for later in (self.expires - 1, self.expires, self.expires + 1,
                      self.expires + 3600):
            self.clock.now = later
            live = self.missions.get_state(self.mission_id)["contract"]
            self.assertEqual(live["content"] is None, later >= self.expires)
            retry = self.report()
            self.assertTrue(all(r["writer_result"]["idempotent"]
                                for r in retry), later)
            self.assertEqual(
                [(r["published_status"], r["observed_at"]) for r in retry],
                [(r["published_status"], r["observed_at"]) for r in first])
            self.assertEqual(self.records(), durable, later)
        # Same pass, after expiry: changed content and changed context are
        # still refused by the unchanged native checks, nothing written.
        self.clock.now = self.expires + 1
        changed = copy.deepcopy(self.assessment)
        changed["app_compatibility"]["observed_at"] -= 1
        with self.assertRaises(mission_record.MissionError) as caught:
            self.report(assessment=changed)
        self.assertEqual(caught.exception.problem,
                         state_service.PROBLEM_STATE_OPERATION_CONFLICT)
        other = mission_record.AuthenticatedContext(
            transport="synthetic_test", principal_kind="local_process_user",
            principal_ref="someone-else")
        with self.assertRaises(mission_record.MissionError) as caught:
            self.report(context=other)
        self.assertEqual(caught.exception.problem,
                         state_service.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT)
        self.assertEqual(self.records(), durable)
        # Freshness never extended: every durable observation keeps its
        # original time, so Mission Core's own evaluator is NOT_READY long
        # after the inner horizons.
        self.assertEqual([o["observed_at"] for o in self.records()],
                         [self.t0, self.t0 - 59, self.t0])
        self.assertFalse(self.native_readiness(self.expires + 1)["satisfied"])
        self.assertEqual(self.native_readiness(self.t0 + 2)["resources"][
            "herdr-snapshot"], "NOT_READY")

    def test_unconsumed_slot_after_expiry_is_refused_natively(self):
        with self.assertRaises(OSError):
            self.report(service=InterruptingService(self.missions, 1))
        self.assertEqual(len(self.records()), 1)
        self.clock.now = self.expires + 1
        # The consumed slot replays idempotently; the next, UNCONSUMED slot
        # meets the native authorization/contract-liveness checks and is
        # refused: replay stability is no bypass for new operations.
        with self.assertRaises(mission_record.MissionError) as caught:
            self.report()
        self.assertNotIn(caught.exception.problem, (
            "mission_state_operation_conflict",
            "mission_state_operation_context_conflict"))
        self.assertEqual(len(self.records()), 1)
        # Fresh, never-used ids after expiry are refused the same way.
        fresh_ids = [self.missions.mint_state_operation_id(AUTHENTICATED)
                     for _ in readiness_module.SLOTS]
        with self.assertRaises(mission_record.MissionError):
            readiness_module.report_readiness(
                self.missions, self.mission_id, self.assessment,
                READINESS_KEYS, fresh_ids,
                self.missions.get_state(self.mission_id)["sequence"],
                AUTHENTICATED)
        self.assertEqual(len(self.records()), 1)


@NEEDS_READINESS
class HistoricalActivationTests(StoreFixture):
    """A LATER activation cannot rewrite what a retry of an EARLIER sequence
    reconstructs. Real Mission Core: revision 1 (bound 60) activated, a report
    written, then EDIT + fresh APPROVE + activation of revision 2 (bound 20);
    resolution is Mission Core's own read-only ``activation_contract_at``."""

    readiness_mission = ReadinessTests.readiness_mission
    compatible = ReadinessTests.compatible

    def proof(self, bound):
        from test_local_request import contract as base_contract
        value = base_contract()
        value["required_resource_readiness"] = [
            {"resource_key": key, "max_age_seconds": bound}
            for key in sorted(READINESS_KEYS.values())]
        return value

    def reactivate(self, missions, mission_id, bound):
        from mission import decision as mission_decision
        self.clock.now += 1
        current = missions.get(mission_id)["record"]
        missions.edit(mission_id, current["current_revision"],
                      mission_request(objective="Readiness fixture v2",
                                      proof_contract=self.proof(bound)),
                      missions.mint_decision_id(AUTHENTICATED), AUTHENTICATED)
        revision = missions.get(mission_id)["record"]["revisions"][-1]
        self.clock.now += 1
        missions.apply_human_decision(mission_decision.HumanDecisionEnvelope(
            context=AUTHENTICATED,
            decision_id=missions.mint_decision_id(AUTHENTICATED),
            mission_id=mission_id, revision=revision["revision"],
            decision=mission_decision.DECISION_APPROVE,
            received_at=self.clock.now,
            approved_action_scope=list(
                revision["proposal"]["requested_action_scope"]),
            approved_delivery_targets=[]))
        missions.activate_proof_contract(
            mission_id, missions.mint_state_operation_id(AUTHENTICATED),
            missions.get_state(mission_id)["sequence"], AUTHENTICATED)

    def test_retry_of_an_earlier_sequence_keeps_its_original_bounds(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        t0 = self.clock.now
        self.register(source_id="mc", store=state)
        # Inner horizons of 30 s: refused under bound 60, READY under 20.
        response = query_module.query(
            self.registry, snapshot(t0, tasks=[], agents=[]), t0,
            max_age_seconds=30)
        assessment = readiness_module.assess(response, self.compatible(t0), t0)
        ids = [missions.mint_state_operation_id(AUTHENTICATED)
               for _ in readiness_module.SLOTS]
        sequence = missions.get_state(mission_id)["sequence"]
        first = readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, ids, sequence,
            AUTHENTICATED)
        self.assertEqual([(r["published_status"], r["contract_bound"])
                          for r in first[:2]],
                         [("NOT_READY", 60), ("NOT_READY", 60)])
        durable = copy.deepcopy(missions.get_state(mission_id)["record"][
            "resource_readiness"])
        self.reactivate(missions, mission_id, 20)
        activations = missions.get_state(mission_id)["record"][
            "contract_activations"]
        self.assertEqual(len(activations), 2)
        self.assertGreater(activations[1]["sequence"], sequence)
        # The EXACT retry of the original sequence reconstructs revision 1's
        # bounds — not the latest activation's — and is idempotent.
        retry = readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS, ids, sequence,
            AUTHENTICATED)
        self.assertTrue(all(r["writer_result"]["idempotent"] for r in retry))
        self.assertEqual([(r["published_status"], r["contract_bound"])
                          for r in retry],
                         [(r["published_status"], r["contract_bound"])
                          for r in first])
        self.assertEqual(missions.get_state(mission_id)["record"][
            "resource_readiness"], durable)
        # A NEW report at the current sequence is governed by revision 2.
        now_sequence = missions.get_state(mission_id)["sequence"]
        self.assertEqual(readiness_module.governing_contract(
            missions, mission_id, now_sequence)["required_resource_readiness"][
                0]["max_age_seconds"], 20)
        fresh = readiness_module.report_readiness(
            missions, mission_id, assessment, READINESS_KEYS,
            [missions.mint_state_operation_id(AUTHENTICATED)
             for _ in readiness_module.SLOTS], now_sequence, AUTHENTICATED)
        self.assertEqual([(r["published_status"], r["contract_bound"])
                          for r in fresh[:2]], [("READY", 20), ("READY", 20)])

    def test_a_tampered_activation_is_refused_by_mission_core(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        sequence = missions.get_state(mission_id)["sequence"]
        path = os.path.join(state, mission_store.MISSIONS_FILE_NAME)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["mission_state"][mission_id]["contract_activations"][0][
            "contract_digest_sha256"] = "0" * 64
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with open(path, "rb") as handle:
            tampered = handle.read()
        with self.assertRaises((mission_record.MissionError,
                                mission_store.MissionStoreError)):
            readiness_module.governing_contract(missions, mission_id, sequence)
        with self.assertRaises((mission_record.MissionError,
                                mission_store.MissionStoreError)):
            readiness_module.report_readiness(
                missions, mission_id, {"assessed_at": 1}, READINESS_KEYS,
                ["a", "b", "c"], sequence, AUTHENTICATED)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), tampered)

    def test_no_activation_before_the_sequence_refuses_ready(self):
        state = self.mission_state()
        missions, mission_id = self.readiness_mission(state)
        [activation] = missions.get_state(mission_id)["record"][
            "contract_activations"]
        self.assertIsNone(readiness_module.governing_contract(
            missions, mission_id, activation["sequence"] - 1))
        entry = {"status": "READY", "observed_at": 5000, "expires_at": 6000,
                 "problem": None}
        self.assertEqual(readiness_module.publication(entry, None, 5000)[2],
                         readiness_module.PROBLEM_CONTRACT_BOUND_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
