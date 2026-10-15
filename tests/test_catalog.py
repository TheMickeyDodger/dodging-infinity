"""The producer-neutral mission catalog endpoint (target_runtime.catalog,
``dicatalog.py``).

Isolated stores and fakes only. Every registry and store is a private temp
directory holding REAL Mission Core and legacy WorkflowStore files; the
runtime snapshot, when configured, comes from a FAKE listing run through
the engine's REAL owned runner behind ``test_runtime_snapshot``'s structural
fence (any other argv raises ``EngineReached`` before a process starts).
A blocked query is a FAKE ``query`` that waits on an event and releases
itself after 20 s. The ``serve`` stdout check runs in an ISOLATED CHILD
process (no herd roots, so it can reach no engine at all) under
``subprocess.run(timeout=...)``. Every test inherits the independent SIGALRM
watchdog (``Bounded``), and every owner is shut down and settled (bounded)
by cleanup.
"""

import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract  # noqa: E402
from mission_sources import query as query_module  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
# Read-only subset: the typed Jarvis DI control surface (jarvis_di) is NOT
# landed in this checkout (correction C2), so the catalog-through-Jarvis
# tests are SKIPPED by name, never satisfied by copying the controls.
try:
    from jarvis_di import protocol as protocol_module  # noqa: E402
    from jarvis_di import server as server_module  # noqa: E402
    from jarvis_di import state as jarvis_state  # noqa: E402
    from jarvis_di import stdio as stdio_module  # noqa: E402
    from jarvis_di import tools as tools_module  # noqa: E402
except ImportError:
    protocol_module = server_module = jarvis_state = None
    stdio_module = tools_module = None
from target_runtime import catalog  # noqa: E402
from target_runtime import runtime_snapshot as snapshot_module  # noqa: E402
import test_runtime_snapshot as snapshot_tests  # noqa: E402
from test_mission_sources import NOW  # noqa: E402
from test_runtime_snapshot import listing_stdout  # noqa: E402

REAL_QUERY = query_module.query

#: The isolated child for the stdout check: the REAL endpoint process path
#: (``catalog.main`` serve: stream isolation, the owner, the real query),
#: with the query wrapped to write NOISE to stdout every way it can.
SERVE_CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from mission_sources import query as query_module
from target_runtime import catalog
real = query_module.query
def noisy(*args, **kwargs):
    print("[query] diagnostic via print")
    sys.stdout.flush()
    os.write(1, b'{"forged": "answer"}\n')
    return real(*args, **kwargs)
query_module.query = noisy
sys.exit(catalog.main(["serve", "--registry", sys.argv[2]],
                      clock=lambda: int(sys.argv[3])))
'''


class BlockedQuery(object):
    """A FAKE query: blocks until released (or by itself after 20 s), then
    returns a marked value that must never reach any caller."""

    LATE = {"late": "never returned"}

    def __init__(self):
        self.calls = 0
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.release.wait(20)
        return dict(self.LATE)


class Ticks(object):
    """A FAKE monotonic clock for the cadence: moves only when told."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class CountingQuery(object):
    """Counts every ACTUAL query start, then runs the real query (or
    raises, for the failure path)."""

    def __init__(self, fail=False):
        self.starts = 0
        self.fail = fail

    def __call__(self, *args, **kwargs):
        self.starts += 1
        if self.fail:
            raise ValueError("a failing source read")
        return REAL_QUERY(*args, **kwargs)


class CatalogFixture(snapshot_tests.Fixture):

    def setUp(self):
        super(CatalogFixture, self).setUp()
        self.ticks = Ticks()

    def endpoint(self, herd_roots=None, listing_argv=None, discover=False):
        owner = catalog.CatalogService(self.directory, self.clock, herd_roots,
                                       listing_argv, monotonic=self.ticks,
                                       discover=discover)

        def end():
            for blocked in getattr(self, "_blocked", []):
                blocked.release.set()
            owner.shutdown()
            state = owner.settle(10)
            self.assertNotEqual(state["query_work"], catalog.WORK_PENDING,
                                "a query thread outlived its test")
        self.addCleanup(end)
        return owner

    def blocked_query(self):
        blocked = BlockedQuery()
        self._blocked = getattr(self, "_blocked", []) + [blocked]
        patcher = mock.patch.object(query_module, "query", blocked)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(blocked.release.set)
        return blocked

    def run_main(self, argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            code = catalog.main(argv, clock=self.clock, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def read_answers(self, reader):
        chunks = []
        while True:
            chunk = os.read(reader, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return [json.loads(line) for line in
                b"".join(chunks).decode().splitlines()]

    def serve_lines(self, owner, stream):
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        result = {}

        def run():
            try:
                result["code"] = catalog.serve(owner, stream, writer,
                                               self.clock)
            finally:
                os.close(writer)
        server = threading.Thread(target=run)
        server.start()
        answers = self.read_answers(reader)
        server.join(30)
        self.assertFalse(server.is_alive())
        self.assertEqual(result.get("code"), catalog.EXIT_OK)
        return answers

    def split_pipe(self, pieces):
        """A REAL pipe whose writer delivers ``pieces`` as separate writes,
        then closes; read through a buffered reader, as ``serve`` is."""
        reader, writer = os.pipe()

        def feed():
            try:
                for piece in pieces:
                    os.write(writer, piece)
                    time.sleep(0.02)
            finally:
                os.close(writer)
        feeder = threading.Thread(target=feed)
        feeder.start()
        self.addCleanup(feeder.join, 10)
        stream = os.fdopen(reader, "rb")
        self.addCleanup(stream.close)
        return stream

    def one_json_line(self, out):
        self.assertEqual(out.count("\n"), 1, out)
        return json.loads(out)


class EndpointTests(CatalogFixture):
    """The runnable endpoint over the accepted query contract."""

    def test_query_serves_registered_sources_with_the_runtime_injected(self):
        state_dir = self.mission_state()
        mission_id, workspace = self.running_mission(
            state_dir, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        code, _out, err = self.run_main([
            "register", "--registry", self.directory, "--source-id", "mc",
            "--kind", contract.KIND_MISSION_CORE, "--store-dir", state_dir,
            "--producer", "local_request"])
        self.assertEqual(code, catalog.EXIT_OK, err)
        code, out, err = self.run_main(
            ["query", "--registry", self.directory, "--herd-root", workspace],
            listing_argv=self.plan())
        self.assertEqual(code, catalog.EXIT_OK, err)
        answer = self.one_json_line(out)
        self.assertEqual(tuple(sorted(answer)), tuple(sorted(
            catalog.ANSWER_KEYS)))
        self.assertEqual((answer["endpoint"], answer["problem"]),
                         (catalog.ENDPOINT, None))
        self.assertTrue(answer["runtime_capture"]["available"])
        response = answer["response"]
        self.assertEqual(contract.response_freshness(response, NOW),
                         (contract.HEALTH_FRESH, None))
        [mission] = response["missions"]
        self.assertEqual(mission["mission_ref"], "mc:" + mission_id)
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        [member] = mission["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)
        self.assertNotIn("{", err)                    # no JSON on stderr

    def test_a_new_source_appears_with_no_code_change_and_models_stay_apart(
            self):
        core = self.mission_state("core")
        mission_id, _workspace = self.running_mission(core)
        legacy = self.store_dir("legacy")
        self.workflow(legacy, workflow_id="wf-0002")
        unregistered = self.store_dir("never-registered")
        self.workflow(unregistered, workflow_id="wf-0099")
        for source_id, kind, store, producer in (
                ("core", contract.KIND_MISSION_CORE, core, "local_request"),
                ("grok", contract.KIND_WORKFLOW_STORE, legacy, "grok_bot")):
            before = self.one_json_line(self.run_main(
                ["query", "--registry", self.directory])[1])
            code, _out, err = self.run_main([
                "register", "--registry", self.directory, "--source-id",
                source_id, "--kind", kind, "--store-dir", store,
                "--producer", producer])
            self.assertEqual(code, catalog.EXIT_OK, err)
            after = self.one_json_line(self.run_main(
                ["query", "--registry", self.directory])[1])
            refs = {m["mission_ref"] for m in (
                after["response"]["missions"])}
            self.assertEqual(len(refs), len(
                (before["response"] or {}).get("missions", [])) + 1)
        self.assertEqual(refs, {"core:" + mission_id, "grok:wf-0002"})
        models = {m["mission_ref"].split(":")[0]: m["lifecycle"]["model"]
                  for m in after["response"]["missions"]}
        self.assertEqual(models, {
            "core": "mission_core.mission_state/1",
            "grok": "workflow_authority.workflow_phase/2"})
        # Registration is inspectable data, exactly as recorded.
        listed = self.one_json_line(self.run_main(
            ["sources", "--registry", self.directory])[1])
        self.assertEqual([(s["source_id"], s["kind"], s["store_dir"],
                           s["producer"]) for s in listed["sources"]], [
            ("core", contract.KIND_MISSION_CORE, core, "local_request"),
            ("grok", contract.KIND_WORKFLOW_STORE, legacy, "grok_bot")])
        code, _out, _err = self.run_main(["unregister", "--registry",
                                          self.directory, "--source-id",
                                          "grok"])
        self.assertEqual(code, catalog.EXIT_OK)
        final = self.one_json_line(self.run_main(
            ["query", "--registry", self.directory])[1])
        self.assertEqual([m["mission_ref"] for m in final["response"][
            "missions"]], ["core:" + mission_id])

    def test_an_absent_registry_is_unavailable_never_an_empty_catalog(self):
        code, out, _err = self.run_main(["query", "--registry",
                                         self.directory])
        self.assertEqual(code, catalog.EXIT_OK)
        response = self.one_json_line(out)["response"]
        self.assertEqual(response["registry"]["problem"], "registry_absent")
        self.assertNotEqual(contract.response_freshness(response, NOW)[0],
                            contract.HEALTH_FRESH)
        self.assertEqual(response["missions"], [])
        self.assertFalse(os.path.exists(self.directory))   # nothing created

    def test_an_unavailable_runtime_never_yields_a_binding(self):
        state_dir = self.mission_state()
        _mission_id, workspace = self.running_mission(
            state_dir, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        self.register(source_id="mc", store=state_dir)
        code, out, _err = self.run_main(
            ["query", "--registry", self.directory, "--herd-root", workspace],
            listing_argv=self.plan(stdout="warning: noise\n"))
        self.assertEqual(code, catalog.EXIT_OK)
        answer = self.one_json_line(out)
        self.assertFalse(answer["runtime_capture"]["available"])
        self.assertEqual(answer["runtime_capture"]["problem"],
                         snapshot_module.PROBLEM_LISTING_MALFORMED)
        [mission] = answer["response"]["missions"]
        self.assertEqual(mission["members"], [])
        self.assertNotEqual(mission["binding"]["state"],
                            contract.BINDING_BOUND)

    def test_registration_refusals_and_usage_are_distinct(self):
        store = self.store_dir("mc")
        argv = ["register", "--registry", self.directory, "--source-id",
                "mc", "--kind", contract.KIND_MISSION_CORE, "--store-dir",
                store, "--producer", "local_request"]
        self.assertEqual(self.run_main(argv)[0], catalog.EXIT_OK)
        code, out, err = self.run_main(argv[:-1] + ["someone-else"])
        self.assertEqual(code, catalog.EXIT_REGISTRATION_REFUSED)
        self.assertEqual(out, "")
        self.assertIn("source_id_conflict", err)
        self.assertEqual(self.run_main(argv[:6] + ["discovered"] + argv[7:])[
            0], catalog.EXIT_USAGE)                    # unknown kind
        self.assertEqual(self.run_main(["query", "--registry",
                                        "relative/dir"])[0],
                         catalog.EXIT_USAGE)


class ResponsivenessTests(CatalogFixture):
    """Query and source work never block a caller: the same one-slot,
    responsive-return and lifetime discipline as the runtime owner."""

    def test_a_blocked_query_answers_at_its_wait_and_holds_the_one_slot(self):
        self.register(source_id="mc", store=self.mission_state())
        blocked = self.blocked_query()
        owner = self.endpoint()
        started = time.monotonic()
        first = owner.request(1)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(first["problem"], catalog.PROBLEM_QUERY_PENDING)
        self.assertIsNone(first["response"])
        self.assertEqual(owner.diagnostics()["query_work"],
                         catalog.WORK_PENDING)
        for _ in range(3):
            again = owner.request(1)
            self.assertEqual(again["problem"], catalog.PROBLEM_SLOT_OCCUPIED)
        self.assertEqual(blocked.calls, 1)            # nothing accumulated
        blocked.release.set()
        self.assertEqual(owner.settle(10)["query_work"],
                         catalog.WORK_SETTLED)
        mock.patch.object(query_module, "query", REAL_QUERY).start()
        self.addCleanup(mock.patch.stopall)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        fresh = owner.request(5)
        self.assertIsNotNone(fresh["response"])
        self.assertNotIn("late", fresh["response"])   # the late value: never

    def test_shutdown_refuses_new_work_and_reports_existing_work(self):
        blocked = self.blocked_query()
        owner = self.endpoint()
        owner.request(0.5)
        state = owner.shutdown()
        self.assertEqual((state["accepting"], state["query_work"]),
                         (False, catalog.WORK_PENDING))
        self.assertEqual(owner.request(1)["problem"],
                         catalog.PROBLEM_OWNER_SHUT_DOWN)
        self.assertEqual(blocked.calls, 1)
        blocked.release.set()
        self.assertEqual(owner.settle(10)["query_work"],
                         catalog.WORK_SETTLED)
        self.assertEqual(owner.request(1)["problem"],
                         catalog.PROBLEM_OWNER_SHUT_DOWN)

    def test_an_interrupted_query_start_holds_the_slot(self):
        owner = self.endpoint()
        with mock.patch.object(catalog.threading.Thread, "start",
                               side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                owner.request(1)
        self.assertEqual(owner.diagnostics()["query_work"],
                         catalog.WORK_UNCERTAIN)
        self.assertEqual(owner.request(1)["problem"],
                         catalog.PROBLEM_SLOT_OCCUPIED)

    def test_a_failed_query_is_reported_never_shown_as_a_response(self):
        owner = self.endpoint()
        with mock.patch.object(query_module, "query",
                               side_effect=ValueError("defect")):
            failed = owner.request(2)
        self.assertEqual((failed["problem"], failed["response"]),
                         (catalog.PROBLEM_QUERY_FAILED, None))
        self.assertIn("ValueError", failed["detail"])
        self.assertEqual(owner.diagnostics()["query_work"],
                         catalog.WORK_SETTLED)

    def test_the_runtime_capture_uses_only_its_share_of_the_wait(self):
        """A stalled runtime start (a FAKE ``Popen`` that starts nothing)
        neither stalls the catalog answer nor its sources."""
        self.register(source_id="mc", store=self.mission_state())
        stall = snapshot_tests.StalledStart()
        self._stalls.append(stall)
        patcher = mock.patch.object(
            snapshot_tests.engine_runtime.subprocess, "Popen", stall)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(stall.release.set)
        owner = self.endpoint([self.herd()], self.plan())
        started = time.monotonic()
        answer = owner.request(2)
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertEqual(answer["runtime_capture"]["problem"],
                         snapshot_module.PROBLEM_CAPTURE_PENDING)
        self.assertIsNotNone(answer["response"])
        self.assertEqual(answer["response"]["runtime_snapshot"]["health"],
                         contract.HEALTH_UNAVAILABLE)
        stall.release.set()

    def test_the_one_shot_answers_at_its_wait_without_joining_a_read(self):
        """No herd roots: the one-shot answers at its wait and exits; a
        query thread blocked in a read is NOT waited for (it owns nothing
        that outlives the process)."""
        self.register(source_id="mc", store=self.mission_state())
        self.blocked_query()
        started = time.monotonic()
        code, out, err = self.run_main(["query", "--registry",
                                        self.directory, "--wait-seconds",
                                        "1"])
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertEqual(code, catalog.EXIT_UNAVAILABLE)
        self.assertEqual(self.one_json_line(out)["problem"],
                         catalog.PROBLEM_QUERY_PENDING)
        self.assertIn(catalog.PROBLEM_QUERY_PENDING, err)


class CadenceTests(CatalogFixture):
    """The accepted poll interval, ENFORCED by the long-lived owner: tests
    count ACTUAL capture and query starts, not answer shapes."""

    def test_rapid_repeats_start_one_refresh_and_never_restamp(self):
        state_dir = self.mission_state()
        _mission_id, workspace = self.running_mission(
            state_dir, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        self.register(source_id="mc", store=state_dir)
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        runs = self.spy_runner()
        owner = self.endpoint([workspace], self.plan())
        first = owner.request(10)
        self.assertEqual(first["served"], catalog.SERVED_REFRESHED)
        self.assertTrue(first["runtime_capture"]["available"])
        for step in range(20):
            self.clock.now = NOW + (step % 3)       # wall time may move
            self.ticks.t += 0.2                     # 4 s in all: < 5 s
            again = owner.request(10)
            self.assertEqual(again["served"], catalog.SERVED_CACHED)
            for key in catalog.ANSWER_KEYS:
                if key not in ("served", "retry_after_seconds"):
                    self.assertEqual(again[key], first[key], key)
            self.assertEqual(
                (again["response"]["generated_at"],
                 again["response"]["valid_until"]),
                (first["response"]["generated_at"],
                 first["response"]["valid_until"]))
        self.assertEqual(queries.starts, 1)          # ONE query started
        self.assertEqual(len(runs), 3)               # ONE runtime capture

    def test_the_interval_is_anchored_to_the_refresh_never_to_requests(self):
        self.register(source_id="mc", store=self.mission_state())
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        owner = self.endpoint()
        interval = catalog.MIN_REFRESH_INTERVAL_SECONDS
        served = []
        # Requests every 1 s for 12 s: anchored to REQUESTS the interval
        # would never elapse; anchored to the REFRESH it elapses at 0, 5, 10.
        for second in range(13):
            self.ticks.t = 1000.0 + second
            served.append(owner.request(5)["served"])
        self.assertEqual(queries.starts, 3)
        self.assertEqual([i for i, s in enumerate(served)
                          if s == catalog.SERVED_REFRESHED], [0, interval,
                                                               2 * interval])
        # Just under the interval: still no start, whatever the request.
        self.ticks.t = 1000.0 + 2 * interval + interval - 0.001
        self.assertEqual(owner.request(1)["served"], catalog.SERVED_CACHED)
        self.assertEqual(queries.starts, 3)

    def test_failed_refreshes_count_and_are_never_retried_early(self):
        self.register(source_id="mc", store=self.mission_state())
        queries = CountingQuery(fail=True)
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        owner = self.endpoint()
        self.assertEqual(owner.request(5)["problem"],
                         catalog.PROBLEM_QUERY_FAILED)
        for _ in range(10):
            throttled = owner.request(5)
            self.assertEqual((throttled["problem"], throttled["response"]),
                             (catalog.PROBLEM_REFRESH_THROTTLED, None))
            self.assertGreater(throttled["retry_after_seconds"], 0)
        self.assertEqual(queries.starts, 1)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        owner.request(5)
        self.assertEqual(queries.starts, 2)

    def test_an_unfinished_refresh_counts_and_its_late_value_is_not_cached(
            self):
        self.register(source_id="mc", store=self.mission_state())
        blocked = self.blocked_query()
        owner = self.endpoint()
        self.assertEqual(owner.request(0.5)["problem"],
                         catalog.PROBLEM_QUERY_PENDING)
        blocked.release.set()
        owner.settle(10)
        late = owner.request(5)
        self.assertEqual(late["problem"], catalog.PROBLEM_REFRESH_THROTTLED)
        self.assertEqual(blocked.calls, 1)

    def test_a_cached_answer_past_its_own_validity_is_not_served(self):
        self.register(source_id="mc", store=self.mission_state())
        owner = self.endpoint()
        first = owner.request(5)
        self.assertEqual(first["served"], catalog.SERVED_REFRESHED)
        self.ticks.t += 1
        self.clock.now = first["response"]["valid_until"] + 1
        later = owner.request(5)
        self.assertEqual((later["problem"], later["response"]),
                         (catalog.PROBLEM_REFRESH_THROTTLED, None))

    def test_serve_requests_cannot_bypass_the_cadence(self):
        self.register(source_id="mc", store=self.mission_state())
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        owner = self.endpoint()
        lines = (b'{"request": "catalog", "wait_seconds": 30}\n'
                 b'{"request": "diagnostics"}\n'
                 b'{"request": "catalog", "wait_seconds": 0.1}\n'
                 b'{"request": "catalog"}\n') * 5
        answers = self.serve_lines(owner, io.BytesIO(lines))
        self.assertEqual(len(answers), 20)
        self.assertEqual(queries.starts, 1)
        self.assertEqual([a.get("served") for a in answers if "served" in a],
                         [catalog.SERVED_REFRESHED] + [
                             catalog.SERVED_CACHED] * 14)


class DiscoveryTests(CatalogFixture):
    """Canonical runtime-root discovery on refresh: ONLY canonical ACTIVE,
    consistent and unambiguous bindings in REGISTERED sources name a root to
    observe; membership is still decided only by the roster against the
    snapshot. Real stores, real herd state, the FENCED fake listing."""

    def refresh(self, owner):
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        answer = owner.request(10)
        self.assertEqual(answer["served"], catalog.SERVED_REFRESHED, answer)
        return answer

    def by_ref(self, answer):
        return {m["mission_ref"]: m for m in answer["response"]["missions"]}

    def legacy_workflow(self, workflow_id, task_id, workspace=None,
                        store="legacy", **changes):
        legacy = self.store_dir(store)
        if workspace is None:
            workspace = os.path.realpath(os.path.join(self.root,
                                                      "ws-" + workflow_id))
            os.makedirs(workspace, exist_ok=True)
        self.workflow(legacy, workflow_id=workflow_id, task_id=task_id,
                      workspace=workspace, **changes)
        return legacy, workspace

    def test_a_new_mission_in_a_registered_producer_is_acquired_on_refresh(
            self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)    # BEFORE the mission
        runs = self.spy_runner()
        owner = self.endpoint(listing_argv=self.plan(), discover=True)
        first = owner.request(10)
        self.assertEqual(first["response"]["missions"], [])
        self.assertEqual(first["runtime_capture"]["problem"],
                         catalog.PROBLEM_NO_RUNTIME_ROOT)
        self.assertEqual(runs, [])                 # nothing to observe
        # The SAME service lifetime; no launch flag changes. A Mission is
        # materialized canonically (run intent, workspace, receipt task id).
        mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        answer = self.refresh(owner)
        [mission] = answer["response"]["missions"]
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        [member] = mission["members"]
        self.assertEqual((member["classification"], member["role"]),
                         (contract.MEMBER_PRESENT, "executor"))
        runtime = answer["runtime_capture"]
        self.assertEqual(runtime["discovery"]["roots"], [workspace])
        self.assertEqual(runtime["roots"]["captured"], [workspace])
        self.assertEqual(runtime["registered_roots"], [])
        self.assertEqual(len(runs), 3)             # ONE capture, 3 runs

    def test_replaced_removed_and_released_bindings_keep_no_live_team(self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        _mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        legacy, lease = self.legacy_workflow("wf-0002", "task-0002")
        self.herd(root=lease, task_id="task-0002",
                  agents={"executor1": "pod-wf"})
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        owner = self.endpoint(listing_argv=self.plan(stdout=listing_stdout(
            [snapshot_tests.engine_row(),
             snapshot_tests.engine_row(name="pod-wf", session="sess-wf",
                                       pane="w1:p9")])), discover=True)
        start = self.by_ref(self.refresh(owner))
        self.assertEqual({r: m["binding"]["state"] for r, m in start.items()},
                         {r: contract.BINDING_BOUND for r in start})
        # REPLACED: the herd at the Mission's workspace now runs another
        # task. The root was observed, the task is not there: no team.
        self.herd(root=workspace, task_id=snapshot_tests.OTHER_TASK)
        # RELEASED: the legacy lease is released, so no canonical workspace.
        self.workflow(legacy, workflow_id="wf-0002", task_id="task-0002",
                      workspace=lease, released=True)
        after = self.by_ref(self.refresh(owner))
        core_ref = [r for r in after if r.startswith("core:")][0]
        self.assertEqual(after[core_ref]["binding"]["reason"],
                         "task_not_in_snapshot")
        self.assertEqual(after["grok:wf-0002"]["binding"]["state"],
                         contract.BINDING_UNBOUND)
        for mission in after.values():
            self.assertEqual(mission["members"], [])
        # REMOVED: the producer is unregistered; its missions and its root
        # are gone on the next refresh.
        self.registry.unregister("grok")
        final = self.refresh(owner)
        self.assertNotIn("grok:wf-0002", self.by_ref(final))
        self.assertEqual(final["runtime_capture"]["discovery"]["roots"],
                         [workspace])

    def test_an_unavailable_root_is_explicit_and_blinds_only_its_mission(
            self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        _mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        missing = os.path.join(self.root, "workspace-that-is-gone")
        legacy, _lease = self.legacy_workflow("wf-0003", "task-0003",
                                              workspace=missing)
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        owner = self.endpoint(listing_argv=self.plan(), discover=True)
        answer = self.refresh(owner)
        roots = answer["runtime_capture"]["roots"]
        self.assertEqual(roots["captured"], [workspace])
        self.assertEqual([u["root"] for u in roots["unavailable"]], [missing])
        self.assertEqual(roots["unavailable"][0]["problem"],
                         snapshot_module.PROBLEM_ROOTS_INVALID)
        missions = self.by_ref(answer)
        gone = missions["grok:wf-0003"]
        self.assertEqual((gone["binding"]["state"], gone["binding"]["reason"]),
                         (contract.BINDING_UNVERIFIED,
                          "workspace_not_observed"))
        self.assertEqual(gone["members"], [])
        core_ref = [r for r in missions if r.startswith("core:")][0]
        self.assertEqual(missions[core_ref]["binding"]["state"],
                         contract.BINDING_BOUND)

    def test_one_workspace_claimed_twice_is_a_reported_conflict(self):
        """Neither claim wins, whichever source is read first; the
        conflicted root is never observed, a third mission still binds."""
        for core_id, legacy_id in (("a-core", "b-grok"), ("b-core", "a-grok")):
            with self.subTest(order=(core_id, legacy_id)):
                self.directory = os.path.join(self.root, "registry-" + core_id)
                self.registry = type(self.registry)(self.directory)
                core = self.mission_state(core_id)
                mission_id, workspace = self.running_mission(
                    core, task_id=snapshot_tests.TASK, objective="claimed")
                self.herd(root=workspace)
                other_id, other_ws = self.running_mission(
                    core, task_id="20261014-000002-cccccc",
                    objective="unclaimed")
                self.herd(root=other_ws, task_id="20261014-000002-cccccc")
                legacy, _ = self.legacy_workflow(
                    "wf-" + legacy_id, "task-0009", workspace=workspace,
                    store="legacy-" + legacy_id)
                self.register(source_id=core_id, store=core)
                self.register(source_id=legacy_id, store=legacy,
                              kind=contract.KIND_WORKFLOW_STORE,
                              producer="grok_bot")
                answer = self.endpoint(listing_argv=self.plan(),
                                       discover=True).request(10)
                missions = self.by_ref(answer)
                conflicted = sorted(["%s:%s" % (core_id, mission_id),
                                     "%s:wf-%s" % (legacy_id, legacy_id)])
                for ref in conflicted:
                    mission = missions[ref]
                    self.assertEqual(
                        (mission["binding"]["state"],
                         mission["binding"]["reason"], mission["members"]),
                        (contract.BINDING_AMBIGUOUS,
                         "workspace_claimed_by_multiple_missions", []))
                self.assertEqual(missions["%s:%s" % (core_id, other_id)][
                    "binding"]["state"], contract.BINDING_BOUND)
                runtime = answer["runtime_capture"]
                self.assertEqual(runtime["discovery"]["conflicts"], [{
                    "workspace_realpath": workspace,
                    "mission_refs": conflicted}])
                self.assertEqual(runtime["discovery"]["roots"], [other_ws])
                self.assertEqual(runtime["roots"]["captured"], [other_ws])

    def test_capacity_is_explicit_never_a_silent_truncation(self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        _id_a, ws_a = self.running_mission(core, task_id=snapshot_tests.TASK,
                                           objective="first")
        self.herd(root=ws_a)
        legacy, ws_b = self.legacy_workflow("wf-0004", "task-0004")
        self.herd(root=ws_b, task_id="task-0004",
                  agents={"executor1": "pod-wf"})
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        with mock.patch.object(snapshot_module, "MAX_HERD_ROOTS", 1):
            answer = self.endpoint(listing_argv=self.plan(),
                                   discover=True).request(10)
        roots = answer["runtime_capture"]["roots"]
        expected = sorted([ws_a, ws_b])
        self.assertEqual(roots["captured"], expected[:1])
        self.assertEqual(roots["over_capacity"], expected[1:])
        missions = self.by_ref(answer)
        not_seen = [m for m in missions.values()
                    if m["execution"]["workspace_realpath"] == expected[1]]
        [mission] = not_seen
        self.assertEqual((mission["binding"]["reason"], mission["members"]),
                         ("workspace_not_observed", []))

    def test_models_provenance_and_no_membership_from_observation_alone(self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        legacy, lease = self.legacy_workflow("wf-0005", "task-0005")
        self.herd(root=lease, task_id="task-0005",
                  agents={"executor1": "pod-wf"})
        # A PLANNED legacy record with no resolved binding, and a live herd
        # with agents that NO canonical mission names.
        self.workflow(legacy, workflow_id="wf-0006", task_id=None,
                      phase="PLANNED")
        stray = os.path.realpath(os.path.join(self.root, "stray-herd"))
        os.makedirs(stray)
        self.herd(root=stray, task_id="20261014-000009-cccccc",
                  agents={"executor1": "pod-stray"})
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        argv = self.plan(stdout=listing_stdout(
            [snapshot_tests.engine_row(),
             snapshot_tests.engine_row(name="pod-wf", session="sess-wf",
                                       pane="w1:p9"),
             snapshot_tests.engine_row(name="pod-stray",
                                       session="sess-stray",
                                       pane="w1:p8")]))
        before = self.tree()
        answer = self.endpoint(listing_argv=argv, discover=True).request(10)
        missions = self.by_ref(answer)
        self.assertEqual(
            {r: (m["lifecycle"]["model"], m["producer"]["declared"])
             for r, m in missions.items()},
            {"core:" + mission_id: ("mission_core.mission_state/1",
                                    "local_request"),
             "grok:wf-0005": ("workflow_authority.workflow_phase/2",
                              "grok_bot"),
             "grok:wf-0006": ("workflow_authority.workflow_phase/2",
                              "grok_bot")})
        self.assertNotIn(stray, answer["runtime_capture"]["discovery"]["roots"])
        self.assertEqual(sorted(answer["runtime_capture"]["roots"][
            "captured"]), sorted([workspace, lease]))
        self.assertEqual(missions["grok:wf-0006"]["members"], [])
        listed = {member["agent"] for m in missions.values()
                  for member in m["members"]}
        self.assertNotIn("pod-stray", listed)
        self.assertEqual(self.tree(), before)       # NO state mutation

    def test_a_binding_that_appears_between_the_passes_is_not_observed(self):
        """The two source passes are SEQUENTIAL: a Mission materialized
        after the discovery pass is in the answer but its root was not
        captured, so it is UNOBSERVED (never absent, never live) until the
        next refresh observes it."""
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        early_id, early_ws = self.running_mission(
            core, task_id=snapshot_tests.TASK, objective="early")
        self.herd(root=early_ws)
        made = {}

        def between(*args, **kwargs):
            if made.get("passes", 0) == 1 and "mission" not in made:
                made["mission"], made["workspace"] = self.running_mission(
                    core, task_id="20261014-000003-dddddd", objective="late")
                self.herd(root=made["workspace"],
                          task_id="20261014-000003-dddddd",
                          agents={"executor1": "pod-late"})
            made["passes"] = made.get("passes", 0) + 1
            return REAL_QUERY(*args, **kwargs)
        mock.patch.object(query_module, "query", between).start()
        self.addCleanup(mock.patch.stopall)
        owner = self.endpoint(listing_argv=self.plan(), discover=True)
        first = self.by_ref(owner.request(10))
        late = first["core:" + made["mission"]]
        self.assertEqual((late["binding"]["state"], late["binding"]["reason"],
                          late["members"]),
                         (contract.BINDING_UNVERIFIED, "workspace_not_observed",
                          []))
        self.assertEqual(first["core:" + early_id]["binding"]["state"],
                         contract.BINDING_BOUND)
        second = self.by_ref(self.refresh(owner))
        self.assertEqual(second["core:" + made["mission"]]["binding"]["state"],
                         contract.BINDING_BOUND)

    def before_the_answering_pass(self, change):
        """Run ``change`` once, just before the ANSWERING source pass of the
        next refresh (after its discovery pass and its capture)."""
        state = {"passes": 0}

        def query(*args, **kwargs):
            state["passes"] += 1
            if state["passes"] == 2:
                change()
            return REAL_QUERY(*args, **kwargs)
        mock.patch.object(query_module, "query", query).start()
        self.addCleanup(mock.patch.stopall)

    def test_a_post_only_binding_at_a_captured_manual_root_waits(self):
        """Round 11, case 1. The root is a MANUAL root, so it is captured and
        observed whatever discovery selects; a canonical binding to it that
        exists ONLY in the answering pass must not be live. The root, the
        captured task and the answering pass all agree, so only the
        cross-pass comparison can refuse it (the earlier between-passes test
        used a different, unobserved workspace and could not)."""
        legacy = self.store_dir("legacy")
        self.workflow(legacy, workflow_id="wf-0000", task_id=None,
                      phase="PLANNED")
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        manual = os.path.realpath(os.path.join(self.root, "ws-manual"))
        os.makedirs(manual)
        self.herd(root=manual, task_id="task-0010",
                  agents={"executor1": "pod-wf"})
        self.before_the_answering_pass(lambda: self.workflow(
            legacy, workflow_id="wf-0010", task_id="task-0010",
            workspace=manual))
        owner = self.endpoint([manual], self.plan(stdout=listing_stdout(
            [snapshot_tests.engine_row(name="pod-wf")])), discover=True)
        first = owner.request(10)
        post = self.by_ref(first)["grok:wf-0010"]
        self.assertEqual((post["binding"]["state"], post["binding"]["reason"],
                          post["members"]),
                         (contract.BINDING_UNVERIFIED,
                          "binding_not_confirmed_by_both_passes", []))
        self.assertIn(manual, first["runtime_capture"]["roots"]["captured"]
                      if first["runtime_capture"]["roots"] else [manual])
        stable = self.by_ref(self.refresh(owner))["grok:wf-0010"]
        self.assertEqual(stable["binding"]["state"], contract.BINDING_BOUND)
        [member] = stable["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)

    def test_a_changed_binding_at_a_selected_root_waits(self):
        """Round 11, case 2. Discovery selects the root for task T1; the
        herd there already runs T2 and the record changes to T2 before the
        answering pass. Workspace, root and captured task then all agree
        with the answering pass; only the cross-pass comparison can refuse
        the CHANGED binding."""
        legacy, workspace = self.legacy_workflow("wf-0011", "task-0011")
        self.herd(root=workspace, task_id="task-0012",
                  agents={"executor1": "pod-wf"})
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        self.before_the_answering_pass(lambda: self.workflow(
            legacy, workflow_id="wf-0011", task_id="task-0012",
            workspace=workspace))
        owner = self.endpoint(listing_argv=self.plan(stdout=listing_stdout(
            [snapshot_tests.engine_row(name="pod-wf")])), discover=True)
        first = owner.request(10)
        changed = self.by_ref(first)["grok:wf-0011"]
        self.assertEqual((changed["binding"]["state"],
                          changed["binding"]["reason"], changed["members"]),
                         (contract.BINDING_UNVERIFIED,
                          "binding_not_confirmed_by_both_passes", []))
        discovery = first["runtime_capture"]["discovery"]
        self.assertEqual(discovery["roots"], [workspace])
        self.assertEqual(discovery["bindings"]["grok:wf-0011"]["task_id"],
                         "task-0011")              # the first pass's identity
        stable = self.by_ref(self.refresh(owner))["grok:wf-0011"]
        self.assertEqual(stable["binding"]["state"], contract.BINDING_BOUND)
        [member] = stable["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)

    def test_the_refresh_budget_is_the_sum_of_two_bounded_passes(self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        _mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        budgets = []
        real_budget = sources_module.QueryBudget

        class Recorded(real_budget):
            def __init__(self, *args, **kwargs):
                super(Recorded, self).__init__(*args, **kwargs)
                budgets.append((self.bytes_remaining,
                                self.missions_remaining))
        mock.patch.object(sources_module, "QueryBudget", Recorded).start()
        self.addCleanup(mock.patch.stopall)
        per_pass = (sources_module.MAX_QUERY_INPUT_BYTES,
                    sources_module.MAX_QUERY_MISSIONS)
        for discover, passes in ((True, 2), (False, 1)):
            with self.subTest(discover=discover):
                del budgets[:]
                answer = self.endpoint(
                    [workspace] if not discover else None, self.plan(),
                    discover=discover).request(10)
                self.assertEqual(budgets, [per_pass] * passes)
                budget = answer["runtime_capture"]["refresh_budget"]
                self.assertEqual(budget["source_passes"], passes)
                self.assertEqual(
                    (budget["sum_max_query_input_bytes"],
                     budget["sum_max_query_missions"]),
                    (passes * per_pass[0], passes * per_pass[1]))

    def test_refreshes_stay_bounded_and_never_bypass_the_slot(self):
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        _mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        runs = self.spy_runner()
        owner = self.endpoint(listing_argv=self.plan(), discover=True)
        runtime_owner = owner._runtime
        threads = threading.active_count()
        for _ in range(10):
            owner.request(10)                        # within the interval
        self.assertEqual((queries.starts, len(runs)), (2, 3))
        self.refresh(owner)
        self.assertEqual((queries.starts, len(runs)), (4, 6))
        self.assertIs(owner._runtime, runtime_owner)  # one owner, always
        self.assertEqual(threading.active_count(), threads)
        # An OUTSTANDING capture is never bypassed by a discovered root.
        stall = snapshot_tests.StalledStart()
        self._stalls.append(stall)
        patcher = mock.patch.object(
            snapshot_tests.engine_runtime.subprocess, "Popen", stall)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(stall.release.set)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        pending = owner.request(2)
        self.assertEqual(pending["runtime_capture"]["problem"],
                         snapshot_module.PROBLEM_CAPTURE_PENDING)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        refused = owner.request(2)
        self.assertEqual(refused["runtime_capture"]["problem"],
                         snapshot_module.PROBLEM_SLOT_OCCUPIED)
        self.assertEqual(len(stall.calls), 1)        # no second capture
        for answer in (pending, refused):
            [mission] = answer["response"]["missions"]
            self.assertEqual(mission["members"], [])


class ServeTests(CatalogFixture):

    def test_exact_limit_and_over_limit_split_frames(self):
        """A request line of EXACTLY the bound (newline included), split
        across writes, is served; one byte over, also split, is refused and
        serving continues; an UNTERMINATED over-long line ends the session
        and nothing after it is read."""
        self.register(source_id="mc", store=self.mission_state())
        limit = catalog.MAX_REQUEST_LINE_BYTES
        head, tail = b'{"request": "catalog"', b'}\n'
        exact = head + b" " * (limit - len(head) - len(tail)) + tail
        over = head + b" " * (limit - len(head) - len(tail) + 1) + tail
        self.assertEqual((len(exact), len(over)), (limit, limit + 1))
        owner = self.endpoint()
        answers = self.serve_lines(owner, self.split_pipe(
            [exact[:100], exact[100:limit - 1], exact[limit - 1:],
             over[:limit], over[limit:], b'{"request": "diagnostics"}\n']))
        self.assertEqual(len(answers), 3)
        self.assertEqual(answers[0]["served"], catalog.SERVED_REFRESHED)
        self.assertEqual(answers[1]["problem"], catalog.PROBLEM_BAD_REQUEST)
        self.assertNotIn("session ends", answers[1]["detail"])
        self.assertIn("diagnostics", answers[2])

        class Counting(io.BytesIO):
            reads = 0

            def readline(self, *args):
                Counting.reads += 1
                return io.BytesIO.readline(self, *args)
        stream = Counting(b"x" * (limit + 1) + b'{"request": "catalog"}\n'
                          + b'{"request": "catalog"}\n')
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        code = catalog.serve(self.endpoint(), stream, writer, self.clock)
        os.close(writer)
        self.assertEqual(code, catalog.EXIT_FRAME_REFUSED)
        self.assertEqual(Counting.reads, 1)          # nothing more was read
        [refused] = self.read_answers(reader)
        self.assertIn("session ends", refused["detail"])

    def test_serve_answers_line_by_line_and_refuses_bad_lines(self):
        self.register(source_id="mc", store=self.mission_state())
        owner = self.endpoint()
        lines = b"".join([
            b'{"request": "catalog", "wait_seconds": 5}\n',
            b'\n',
            b'{"request": "diagnostics"}\n',
            b'{"request": "approve"}\n',
            b'{"request": "catalog", "store_dir": "/elsewhere"}\n',
            b"not json\n",
        ])
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        code = catalog.serve(owner, io.BytesIO(lines), writer, self.clock)
        os.close(writer)
        self.assertEqual(code, catalog.EXIT_OK)
        chunks = []
        while True:
            chunk = os.read(reader, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        answers = [json.loads(line) for line in
                   b"".join(chunks).decode().splitlines()]
        self.assertEqual(len(answers), 5)
        self.assertIsNotNone(answers[0]["response"])
        self.assertIn("diagnostics", answers[1])
        for refused in answers[2:]:
            self.assertEqual(refused["problem"], catalog.PROBLEM_BAD_REQUEST)
        self.assertFalse(owner.diagnostics()["accepting"])   # EOF: shut down

    def test_the_serve_process_keeps_stdout_to_answers_only(self):
        """An ISOLATED CHILD runs the real ``serve`` path while its query
        writes noise to stdout by print and by raw fd 1 writes."""
        self.register(source_id="mc", store=self.mission_state())
        script = os.path.join(self.root, "serve-child.py")
        with open(script, "w") as handle:
            handle.write(SERVE_CHILD)
        done = subprocess.run(
            [sys.executable, "-I", "-B", script, str(REPO_ROOT),
             self.directory, str(NOW)],
            input=b'{"request": "catalog"}\n{"request": "catalog"}\n',
            capture_output=True, timeout=60)
        self.assertEqual(done.returncode, catalog.EXIT_OK, done.stderr)
        lines = done.stdout.decode().splitlines()
        self.assertEqual(len(lines), 2, done.stdout)
        answers = [json.loads(line) for line in lines]
        for answer in answers:
            self.assertEqual(answer["endpoint"], catalog.ENDPOINT)
            self.assertIsNotNone(answer["response"])
        self.assertEqual([a["served"] for a in answers],
                         [catalog.SERVED_REFRESHED, catalog.SERVED_CACHED])
        self.assertNotIn(b"forged", done.stdout)
        self.assertIn(b"forged", done.stderr)          # the noise went here
        self.assertIn(b"diagnostic via print", done.stderr)


@unittest.skipIf(server_module is None,
                 "jarvis_di (typed controls) is not landed in the read-only"
                 " subset")
class JarvisCatalogToolTests(CatalogFixture):
    """The catalog THROUGH Jarvis's tool surface: actual MCP initialize,
    tools/list and tools/call over the real stdio transport and the real
    server composition (``server.build_tools`` with the operator's
    ``_LazyCatalog``), over isolated stores and the FENCED fake runtime. The
    run bridge is a tripwire: no catalog call may build it."""

    def jarvis(self, herd_roots=None, listing_argv=None, configured=True,
               discover=False):
        state = os.path.join(self.root, "jarvis-state-%d" % len(
            os.listdir(self.root)))
        record = jarvis_state.initialize(state, self.clock)
        self.owners = []

        def factory(registry, roots, clock, discover_roots):
            owner = catalog.CatalogService(registry, clock, roots,
                                           listing_argv, monotonic=self.ticks,
                                           discover=discover_roots)
            self.owners.append(owner)

            def end():
                owner.shutdown()
                owner.settle(10)
            self.addCleanup(end)
            return owner

        def no_bridge(*args):
            raise snapshot_tests.EngineReached("the run bridge was built")
        lazy = server_module._LazyCatalog(
            factory, self.directory, herd_roots, self.clock, discover) \
            if configured else None
        return server_module.build_tools(state, "/control-repo", self.clock,
                                         no_bridge, record, catalog=lazy)

    def mcp(self, tools, calls):
        """ONE MCP session: initialize, tools/list, then each tools/call;
        returns (tools listed, [(isError, result dict)...])."""
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        for index, arguments in enumerate(calls):
            messages.append({"jsonrpc": "2.0", "id": 10 + index,
                             "method": "tools/call",
                             "params": {"name": "mission_catalog",
                                        "arguments": arguments}})
        with open(os.path.join(self.root, "mcp-in-%d" % len(
                os.listdir(self.root))), "w+b") as inbound, open(
                os.path.join(self.root, "mcp-out-%d" % len(
                    os.listdir(self.root))), "w+b") as outbound:
            inbound.write(b"".join(json.dumps(m).encode() + b"\n"
                                   for m in messages))
            inbound.seek(0)
            code = stdio_module.serve(tools, inbound.fileno(),
                                      outbound.fileno())
            outbound.seek(0)
            replies = [json.loads(line) for line in
                       outbound.read().decode("ascii").splitlines()]
        self.assertEqual(code, stdio_module.EXIT_OK)
        self.assertEqual(replies[0]["result"]["serverInfo"],
                         protocol_module.SERVER_INFO)
        listed = {t["name"]: t for t in replies[1]["result"]["tools"]}
        results = []
        for reply in replies[2:]:
            content = reply["result"]["content"]
            results.append((reply["result"]["isError"],
                            json.loads(content[-1]["text"])))
        self.assertEqual(len(results), len(calls))
        return listed, results

    def tree_bytes(self):
        found = {}
        for directory, _dirs, files in os.walk(self.root):
            for name in files:
                if name.startswith("mcp-"):
                    continue
                with open(os.path.join(directory, name), "rb") as handle:
                    found[os.path.join(directory, name)] = handle.read()
        return found

    def two_producers(self):
        core = self.mission_state("core")
        mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        legacy = self.store_dir("legacy")
        self.workflow(legacy, workflow_id="wf-0002")
        self.register(source_id="core", store=core)
        self.register(source_id="grok", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy, producer="grok_bot")
        return mission_id, workspace

    def test_discovery_and_monitoring_across_both_models_through_mcp(self):
        mission_id, workspace = self.two_producers()
        tools = self.jarvis([workspace], self.plan())
        before = self.tree_bytes()
        listed, [(is_error, result)] = self.mcp(tools, [{}])
        schema = listed["mission_catalog"]["inputSchema"]
        self.assertEqual((schema["properties"], schema["required"],
                          schema["additionalProperties"]), ({}, [], False))
        self.assertIn(tools_module.EFFECT_CATALOG_READS,
                      listed["mission_catalog"]["description"])
        self.assertFalse(is_error, result)
        self.assertEqual((result["tool"], result["effect"],
                          result["delivery_authority"]),
                         ("mission_catalog", tools_module.EFFECT_CATALOG_READS,
                          "none"))
        self.assertIn("controls nothing", result["scope"])
        response = result["catalog_answer"]["response"]
        self.assertEqual(contract.response_freshness(response, NOW),
                         (contract.HEALTH_FRESH, None))
        by_ref = {m["mission_ref"]: m for m in response["missions"]}
        self.assertEqual(set(by_ref), {"core:" + mission_id, "grok:wf-0002"})
        self.assertEqual(by_ref["core:" + mission_id]["lifecycle"]["model"],
                         "mission_core.mission_state/1")
        self.assertEqual(by_ref["grok:wf-0002"]["lifecycle"]["model"],
                         "workflow_authority.workflow_phase/2")
        [member] = by_ref["core:" + mission_id]["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)
        self.assertEqual(sorted(s["source_id"] for s in response["sources"]),
                         ["core", "grok"])
        self.assertEqual(self.tree_bytes(), before)        # NO effects

    def test_a_call_naming_what_is_read_is_refused_before_any_read(self):
        self.two_producers()
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        runs = self.spy_runner()
        tools = self.jarvis([self.herd()], self.plan())
        attempts = [{"registry": "/elsewhere"}, {"herd_root": "/elsewhere"},
                    {"herd_roots": ["/elsewhere"]}, {"store_dir": "/x"},
                    {"source_id": "core"}, {"wait_seconds": 1},
                    {"principal": "human"}]
        _listed, results = self.mcp(tools, attempts)
        for is_error, result in results:
            self.assertTrue(is_error)
            self.assertEqual(result["problem"],
                             tools_module.PROBLEM_BAD_ARGUMENTS)
        self.assertEqual((queries.starts, len(runs), self.owners),
                         (0, 0, []))                 # nothing was even built

    def test_what_is_read_is_launch_configuration_checked_at_start(self):
        state = os.path.join(self.root, "never-opened")
        base = ["serve", "--state-dir", state, "--control-repo", "/c"]
        for extra, factory in (
                (["--catalog-herd-root", "/abs/herd"], object()),
                (["--catalog-registry", "relative/registry"], object()),
                (["--catalog-registry", self.directory,
                  "--catalog-herd-root", "relative"], object()),
                (["--catalog-discover-roots"], object()),
                (["--catalog-registry", self.directory], None)):
            with mock.patch.object(sys, "stderr", io.StringIO()):
                code = server_module.main(base + extra, clock=self.clock,
                                          catalog_factory=factory)
            self.assertEqual(code, server_module.EXIT_USAGE, extra)
        self.assertFalse(os.path.exists(state))

    def test_an_unconfigured_server_answers_not_configured(self):
        _listed, [(is_error, result)] = self.mcp(
            self.jarvis(configured=False), [{}])
        self.assertTrue(is_error)
        self.assertEqual(result["problem"],
                         tools_module.PROBLEM_CATALOG_NOT_CONFIGURED)

    def test_unavailable_registry_and_runtime_through_mcp(self):
        _listed, [(is_error, result)] = self.mcp(self.jarvis(), [{}])
        self.assertFalse(is_error)
        response = result["catalog_answer"]["response"]
        self.assertEqual(response["registry"]["problem"], "registry_absent")
        self.assertNotEqual(contract.response_freshness(response, NOW)[0],
                            contract.HEALTH_FRESH)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        mission_id, workspace = self.two_producers()
        tools = self.jarvis([workspace], self.plan(stdout="noise\n"))
        _listed, [(is_error, result)] = self.mcp(tools, [{}])
        answer = result["catalog_answer"]
        self.assertFalse(answer["runtime_capture"]["available"])
        core = [m for m in answer["response"]["missions"]
                if m["mission_ref"] == "core:" + mission_id][0]
        self.assertEqual(core["members"], [])

    def test_a_new_mission_is_acquired_through_mcp_without_a_restart(self):
        """The ACTUAL Jarvis connection, fixed trusted configuration (the
        registry and the discovery switch only; no herd root): a Mission
        materialized in the already registered producer DURING the same
        server lifetime is acquired on the next refresh."""
        core = self.mission_state("core")
        self.register(source_id="core", store=core)
        tools = self.jarvis(listing_argv=self.plan(), discover=True)
        _listed, [(_e, first)] = self.mcp(tools, [{}])
        self.assertEqual(first["catalog_answer"]["response"]["missions"], [])
        mission_id, workspace = self.running_mission(
            core, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        _listed, [(is_error, second)] = self.mcp(tools, [{}])
        self.assertFalse(is_error, second)
        answer = second["catalog_answer"]
        [mission] = answer["response"]["missions"]
        self.assertEqual(mission["mission_ref"], "core:" + mission_id)
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        [member] = mission["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)
        self.assertEqual(answer["runtime_capture"]["discovery"][
            "missions_by_root"], {workspace: "core:" + mission_id})
        self.assertEqual(len(self.owners), 1)          # one owner, built once

    def test_rapid_repeat_and_stale_through_mcp_count_actual_starts(self):
        _mission_id, workspace = self.two_producers()
        queries = CountingQuery()
        mock.patch.object(query_module, "query", queries).start()
        self.addCleanup(mock.patch.stopall)
        runs = self.spy_runner()
        tools = self.jarvis([workspace], self.plan())
        _listed, results = self.mcp(tools, [{}] * 15)
        answers = [r["catalog_answer"] for _e, r in results]
        self.assertEqual([a["served"] for a in answers],
                         [catalog.SERVED_REFRESHED]
                         + [catalog.SERVED_CACHED] * 14)
        for answer in answers[1:]:
            self.assertEqual(answer["response"], answers[0]["response"])
        self.assertEqual((queries.starts, len(runs)), (1, 3))
        # Stale: wall time past the cached answer's own validity, still
        # inside the interval: throttled, never a stale answer, no start.
        self.clock.now = answers[0]["response"]["valid_until"] + 1
        self.ticks.t += 1
        _listed, [(is_error, result)] = self.mcp(tools, [{}])
        self.assertTrue(is_error)
        self.assertEqual(result["problem"], catalog.PROBLEM_REFRESH_THROTTLED)
        self.assertEqual((queries.starts, len(runs)), (1, 3))


if __name__ == "__main__":
    unittest.main()
