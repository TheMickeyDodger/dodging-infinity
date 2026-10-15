"""Read-only runtime snapshot service (target_runtime.runtime_snapshot).

Isolated fakes only, with the REAL transport. Every herd root is a private
temp directory. Every listing is a FAKE EXECUTABLE (a Python script written
into the temp root, driven by a plan file) run through the engine's REAL
owned runner, so its process group, deadline, kill-before-reap and read cap
are exercised for real, with deliberately noisy diagnostics, partial output,
hangs, late output and grandchildren.

The real engine is FENCED, structurally: the engine's runner is wrapped by a
guard that lets through ONLY ``[sys.executable, <the fake>, <a plan under the
temp root>]`` and the service's own boundary children (``-I -B -c`` with the
module's boundary code, or a substitute this test registered explicitly), and
raises ``EngineReached`` (a BaseException, so no
``except Exception`` can absorb it) for any other argv BEFORE a process
starts; both of the engine's unbounded ``run`` helpers are tripwires; and a
cleanup fails any test that reached either. Every test inherits the
independent SIGALRM watchdog (``Bounded``).
"""

import contextlib
import inspect
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from herdr import identity as engine_identity  # noqa: E402
from herdr import runtime as engine_runtime  # noqa: E402
from herdr import tasks as engine_tasks  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import query as query_module  # noqa: E402
from target_runtime import cli as runtime_cli  # noqa: E402
from target_runtime import runtime_snapshot as service  # noqa: E402
from test_mission_sources import NOW, StoreFixture  # noqa: E402

# Read-only subset: target_runtime/cli.py stays BASELINE (the one-shot
# `dirun snapshot` command is not on the catalog path), so the command's
# tests are SKIPPED by name and the fence test runs its service half only.
CLI_HAS_SNAPSHOT = hasattr(runtime_cli, "_snapshot_command")

#: The engine's real owned runner, captured before any test patches it.
REAL_RUNNER = engine_runtime._bounded_command
REAL_POPEN = subprocess.Popen
BASELINE = "88543887059ed5944732f3d04d6dfea77188392c"
TASK = "20261014-000000-aaaaaa"
OTHER_TASK = "20261014-000001-bbbbbb"

FAKE_LISTING = r'''
import json, os, subprocess, sys, time
plan = json.load(open(sys.argv[1]))
pids = [os.getpid()]
if plan.get("grandchild"):
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(60)"])
    pids.append(child.pid)
if plan.get("linger"):
    # Outlives this process WITHOUT holding its output open.
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    pids.append(child.pid)
if plan.get("zombie_member"):
    # A member that STAYS in this (owned) group after the group SIGKILL: its
    # parent left the group (setpgid, a stated residual) and never reaps
    # it, so it remains a zombie member until that parent exits.
    group = os.getpgrp()
    reader, writer = os.pipe()
    if os.fork() == 0:
        os.setpgid(0, 0)
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 1)
        os.dup2(null, 2)
        if os.fork() == 0:
            os.setpgid(0, group)
            os.write(writer, b"C %d\n" % os.getpid())
            time.sleep(plan["zombie_member"])
            os._exit(0)
        os.write(writer, b"P %d\n" % os.getpid())
        time.sleep(plan["zombie_member"])
        os._exit(0)
    os.close(writer)
    seen, buffered = {}, b""
    while len(seen) < 2:
        chunk = os.read(reader, 64)
        if not chunk:
            break
        buffered += chunk
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            tag, pid = line.split()
            seen[tag.decode()] = int(pid)
    pids += [seen.get("P"), seen.get("C")]
if plan.get("pidfile"):
    with open(plan["pidfile"], "w") as handle:
        json.dump(pids, handle)
sys.stderr.write(plan.get("stderr", ""))
sys.stderr.flush()
for path, content in plan.get("rewrite", []):
    if content is None:
        os.unlink(path)
    else:
        with open(path, "w") as handle:
            handle.write(content)
time.sleep(plan.get("sleep_before", 0))
sys.stdout.write(plan.get("stdout", ""))
sys.stdout.flush()
time.sleep(plan.get("sleep_after", 0))
sys.exit(plan.get("exit", 0))
'''

#: A SUBSTITUTE boundary child: per mode it delegates to the real
#: ``boundary_main`` or misbehaves (blocks in a FIFO read, hangs, prints late,
#: forges, exits badly), so the REAL outer boundary is what is tested.
SUBSTITUTE_BOUNDARY = r'''
import json, os, subprocess, sys, time
plan = json.load(open(%(plan)r)).get(sys.argv[2], {})
if plan.get("linger"):
    linger = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(60)"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    with open(plan["linger"], "w") as handle:
        json.dump([linger.pid], handle)
time.sleep(plan.get("sleep_first", 0))
if plan.get("delegate"):
    sys.path.insert(0, sys.argv[1])
    from target_runtime.runtime_snapshot import boundary_main
    sys.exit(boundary_main(sys.argv[2:]))
pids = [os.getpid()]
if plan.get("grandchild"):
    pids.append(subprocess.Popen([sys.executable, "-c",
                                  "import time; time.sleep(60)"]).pid)
if plan.get("pidfile"):
    with open(plan["pidfile"], "w") as handle:
        json.dump(pids, handle)
if plan.get("fifo"):
    with open(plan["fifo"]) as handle:   # a genuinely blocking FILE READ
        handle.read()
time.sleep(plan.get("sleep_before", 0))
sys.stdout.write(plan.get("stdout", ""))
sys.stdout.flush()
time.sleep(plan.get("sleep_after", 0))
sys.exit(plan.get("exit", 0))
'''


class EngineReached(BaseException):
    """Raised by the fence; deliberately not an ``Exception``."""


class StalledStart(object):
    """A FAKE ``Popen`` for the runner: it blocks, as a stalled exec blocks
    the real one, until released (or by itself after 20 s), then raises, so
    NOTHING is ever started."""

    def __init__(self):
        self.calls = []
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        self.release.wait(20)
        raise OSError("the stalled start failed: nothing was started")


def engine_row(name="pod-exec1", provider="claude", status="working",
               session="sess-executor1", pane="w1:p3", workspace="w1",
               cwd="/herd", **extra):
    """One row in the shape the engine's listing emits, including fields the
    projection must DROP."""
    row = {
        "agent": provider,
        "agent_session": {"agent": provider, "kind": "id",
                          "source": "herdr:" + str(provider),
                          "value": session},
        "agent_status": status,
        "cwd": cwd,
        "focused": False,
        "foreground_cwd": "/private/should/not/leak",
        "interactive_ready": True,
        "name": name,
        "pane_id": pane,
        "revision": 1,
        "state_change_seq": 0,
        "tab_id": "w1:t1",
        "terminal_id": "term_secret_" + str(name),
        "workspace_id": workspace,
    }
    row.update(extra)
    return row


def listing_stdout(rows=None):
    return json.dumps({"id": "cli:agent:list", "result": {
        "agents": rows if rows is not None else [engine_row()],
        "type": "agent_list"}}, indent=2) + "\n"


def stable(name="pod-exec1", pane="w1:p3", workspace="w1", cwd="/herd"):
    return {"name": name, "cwd": cwd, "workspace_id": workspace,
            "pane_id": pane}


def present_info(row):
    """A native ``agent_info`` result for ``identity.classify``."""
    return {"status": row["agent_status"],
            "raw": {"result": {"agent": row}}}


class Fixture(StoreFixture):

    def setUp(self):
        super(Fixture, self).setUp()
        self.engine_calls = []
        self.fake = os.path.join(self.root, "fake-listing.py")
        with open(self.fake, "w") as handle:
            handle.write(FAKE_LISTING)
        self.plans = os.path.join(self.root, "plans")
        os.makedirs(self.plans)
        self.allowed_codes = {service._BOUNDARY_CODE}

        def guarded(argv):
            if (type(argv) is list and len(argv) == 3
                    and argv[0] == sys.executable and argv[1] == self.fake
                    and os.path.dirname(argv[2]) == self.plans):
                return REAL_RUNNER(argv)
            if (type(argv) is list and len(argv) == 8
                    and argv[:4] == [sys.executable, "-I", "-B", "-c"]
                    and argv[4] in self.allowed_codes
                    and argv[5] == service._REPO_ROOT
                    and argv[6] in (service.MODE_PASS, service.MODE_PROJECT)):
                return REAL_RUNNER(argv)
            self.engine_calls.append(list(argv))
            raise EngineReached(argv)

        def tripwire(*args, **kwargs):
            self.engine_calls.append(list(args))
            raise EngineReached(args)
        for owner, name, value in (
                (engine_runtime, "_bounded_command", guarded),
                (engine_runtime, "run", tripwire),
                (engine_tasks, "run", tripwire)):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._engine_untouched)
        self.clock.now = NOW
        self._stalls = []

    def setUp_fence_only(self):
        """Re-arm the fence after ``mock.patch.stopall`` (production runner
        constants, same structural fence)."""
        def guarded(argv):
            if (type(argv) is list and len(argv) == 3
                    and argv[0] == sys.executable and argv[1] == self.fake
                    and os.path.dirname(argv[2]) == self.plans):
                return REAL_RUNNER(argv)
            if (type(argv) is list and len(argv) == 8
                    and argv[:4] == [sys.executable, "-I", "-B", "-c"]
                    and argv[4] in self.allowed_codes
                    and argv[5] == service._REPO_ROOT):
                return REAL_RUNNER(argv)
            self.engine_calls.append(list(argv))
            raise EngineReached(argv)

        def tripwire(*args, **kwargs):
            self.engine_calls.append(list(args))
            raise EngineReached(args)
        for owner, name, value in (
                (engine_runtime, "_bounded_command", guarded),
                (engine_runtime, "run", tripwire),
                (engine_tasks, "run", tripwire)):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _engine_untouched(self):
        self.assertEqual(self.engine_calls, [],
                         "a test reached the real engine")

    # -- herd state, shaped like the engine's own --------------------------

    def herd(self, root=None, task_id=TASK, status="RUNNING", agents=None,
             panes=None, roles=None, runtime=True, task=True,
             workspace_id="w1"):
        if root is None:
            root = os.path.join(self.root, "herd-%d" % len(os.listdir(
                self.root)))
            os.makedirs(root)
            root = os.path.realpath(root)
        state = os.path.join(root, ".herd", "state")
        os.makedirs(state, exist_ok=True)
        agents = {"executor1": "pod-exec1"} if agents is None else agents
        panes = ({logical: "w1:p%d" % (3 + i) for i, logical in
                  enumerate(sorted(agents))} if panes is None else panes)
        if roles is None:
            roles = {logical: {
                "logical": logical, "agent": name,
                "session": "sess-" + logical,
                "stable": stable(name, panes.get(logical), "w1")}
                for logical, name in agents.items()}
        if task:
            self.write(state, "task.json", {
                "version": 1, "id": task_id, "status": status,
                "description": "fixture", "repo": root, "started_at": NOW})
        if runtime:
            self.write(state, "runtime.json", {
                "version": 2, "repo": root, "created_at": NOW,
                "workspace_id": workspace_id, "panes": panes,
                "agents": agents})
        self.write(state, "role-bindings.json", {"version": 1,
                                                 "roles": roles})
        return root

    def write(self, state, name, value, raw=None):
        path = os.path.join(state, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw if raw is not None else json.dumps(value))
        os.chmod(path, 0o644)
        return path

    def fresh_store(self):
        self._stores = getattr(self, "_stores", 0) + 1
        source_id = "mc%d" % self._stores
        self.registry = type(self.registry)(os.path.join(
            self.root, "registry-%d" % self._stores))
        return self.mission_state(source_id), source_id

    def state_path(self, root, name):
        return os.path.join(root, ".herd", "state", name)

    # -- the fake listing ----------------------------------------------------

    def plan(self, **plan):
        plan.setdefault("stdout", listing_stdout())
        plan.setdefault("stderr", "[engine] warning: diagnostics on stderr\n")
        path = os.path.join(self.plans, "plan-%d.json" % len(os.listdir(
            self.plans)))
        with open(path, "w") as handle:
            json.dump(plan, handle)
        return [sys.executable, self.fake, path]

    def capture(self, roots, **plan):
        return service.capture(roots, self.clock, self.plan(**plan))

    def payload(self, roots, stdout=None, observed_at=NOW):
        """An in-process ``_project`` payload (internal helper tests)."""
        return {"roots": list(roots),
                "digests": service._digests(service._state_pass(roots)),
                "listing": listing_stdout() if stdout is None else stdout,
                "observed_at": observed_at}

    def substitute(self, **modes):
        """Replace the boundary code with SUBSTITUTE_BOUNDARY for this test."""
        path = os.path.join(self.root, "boundary-plan-%d.json" % len(
            os.listdir(self.root)))
        with open(path, "w") as handle:
            json.dump(modes, handle)
        code = SUBSTITUTE_BOUNDARY % {"plan": path}
        self.allowed_codes.add(code)
        patcher = mock.patch.object(service, "_BOUNDARY_CODE", code)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pids(self, pidfile):
        with open(pidfile) as handle:
            return json.load(handle)

    def assert_gone(self, pids):
        """Every pid is ALREADY gone (reaped) when this is called, right
        after the call returned: no tolerance window, so this checks the
        before-return cleanup condition, not eventual cleanup."""
        for pid in pids:
            with self.assertRaises(ProcessLookupError,
                                   msg="pid %d survived the capture" % pid):
                os.kill(pid, 0)

    def owner(self, roots, listing_argv):
        """A long-lived owner whose worker is ended (bounded) by cleanup."""
        owner = service.SnapshotService(roots, self.clock, listing_argv)

        def end():
            for stall in self._stalls:
                stall.release.set()
            owner.shutdown()
            state = owner.settle(10)
            self.assertNotEqual(state["work"], service.WORK_PENDING,
                                "an owner's worker outlived its test")
        self.addCleanup(end)
        return owner

    def escaped_parent(self, pid):
        """End the zombie member's escaped parent (``pid``) if it is still
        the leader of its own group; its zombie child is then reaped."""
        try:
            if os.getpgid(pid) == pid != os.getpgrp():
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def wait_reaped(self, pid, within=10.0):
        deadline = time.monotonic() + within
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            self.assertLess(time.monotonic(), deadline,
                            "pid %d was not reaped" % pid)
            time.sleep(0.05)

    def spy_runner(self):
        """Record every owned run's report (the fence still decides what
        may run)."""
        reports = []
        guarded = engine_runtime._bounded_command

        def spy(argv):
            report = guarded(argv)
            reports.append(report)
            return report
        patcher = mock.patch.object(engine_runtime, "_bounded_command", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return reports

    def spy_signals(self):
        """Record (group, signal) for every ``killpg``, then delegate."""
        sent = []
        real = os.killpg

        def spy(pgid, sig):
            sent.append((pgid, sig))
            return real(pgid, sig)
        patcher = mock.patch.object(os, "killpg", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return sent

    def tree(self):
        found = {}
        for directory, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(directory, name)
                with open(path, "rb") as handle:
                    found[path] = handle.read()
        return found


class FenceAndWiringTests(Fixture):

    def test_the_listing_argv_has_no_default(self):
        parameter = inspect.signature(service.capture).parameters[
            "listing_argv"]
        self.assertIs(parameter.default, inspect.Parameter.empty)
        with self.assertRaises(TypeError):
            service.capture([self.herd()], self.clock)
        for argv in ([], (), "herdr agent list", [""], [1], ["a\x00b"],
                     ["x"] * (service.MAX_LISTING_ARGV + 1)):
            with self.assertRaises(TypeError):
                service.capture([self.herd()], self.clock, argv)

    def test_production_argv_is_the_engine_listing_and_is_fenced(self):
        self.assertEqual(service.LISTING_ARGV, ("herdr", "agent", "list"))
        with self.assertRaises(EngineReached):
            service.capture([self.herd()], self.clock, service.LISTING_ARGV)
        if not CLI_HAS_SNAPSHOT:
            self.assertEqual(self.engine_calls, [["herdr", "agent", "list"]])
            self.engine_calls.clear()
            return
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(EngineReached):
                runtime_cli.main(["snapshot", "--herd-root", self.herd()])
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.engine_calls, [["herdr", "agent", "list"]] * 2)
        self.engine_calls.clear()

    def test_the_runner_change_is_one_additive_datum(self):
        """``herdr/runtime.py`` is BASELINE plus exactly ONE line, the group
        id as a datum (its pre-reap SIGKILL and its reap are untouched), and
        ``_command_record``'s explicit projection is not widened."""
        baseline = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show",
             "%s:herdr/runtime.py" % BASELINE],
            capture_output=True, check=True, timeout=30).stdout.decode()
        current = (REPO_ROOT / "herdr" / "runtime.py").read_text()
        added = '        "process_group": proc.pid,\n'
        self.assertEqual(current.count(added), 1)
        self.assertEqual(current.replace(added, "", 1), baseline)
        full = {"started": True, "process_group": 7, "returncode": 0,
                "timed_out": False, "elapsed_seconds": 0.0, "stdout": "",
                "stderr": "", "stdout_truncated_at_read": False,
                "stderr_truncated_at_read": False}
        self.assertNotIn("process_group", engine_runtime._command_record(full))
        pidfile = os.path.join(self.root, "leader.json")
        report = engine_runtime._bounded_command(self.plan(pidfile=pidfile))
        self.assertEqual(report["process_group"], self.pids(pidfile)[0])
        self.assertEqual(report["returncode"], 0)


class CaptureTests(Fixture):

    def test_snapshot_is_the_contract_shape_with_an_allowlist(self):
        root = self.herd()
        result = self.capture([root])
        self.assertTrue(result.is_available(), result)
        self.assertEqual(result.observed_at, NOW)
        self.assertEqual(result.snapshot, {
            "schema": contract.SNAPSHOT_SCHEMA,
            "version": contract.SNAPSHOT_VERSION,
            "observed_at": NOW,
            "producer": service.PRODUCER,
            "tasks": [{"task_id": TASK, "workspace_realpath": root,
                       "bindings": [{
                           "logical": "executor1", "role": "executor",
                           "agent": "pod-exec1", "session": "sess-executor1",
                           "pane_id": "w1:p3", "workspace_id": "w1"}]}],
            "agents": [{"agent": "pod-exec1", "provider": "claude",
                        "status": "working", "session": "sess-executor1",
                        "pane_id": "w1:p3", "workspace_id": "w1"}],
        })
        text = json.dumps(result.snapshot)
        for dropped in ("should/not/leak", "term_secret", "tab_id",
                        "interactive_ready", "diagnostics"):
            self.assertNotIn(dropped, text)
        self.assertEqual(contract.read_runtime_snapshot(
            result.snapshot, NOW + 60).health, contract.HEALTH_FRESH)
        self.assertEqual(contract.read_runtime_snapshot(
            result.snapshot, NOW + 61).health, contract.HEALTH_STALE)

    def test_observed_at_is_the_observation_start(self):
        ticks = iter((NOW, NOW + 30))
        result = service.capture([self.herd()], lambda: next(ticks),
                                 self.plan(sleep_before=0.2))
        self.assertEqual(result.observed_at, NOW)
        self.assertEqual(result.snapshot["observed_at"], NOW)

    def test_end_to_end_present_member_through_mission_sources(self):
        state_dir = self.mission_state()
        mission_id, workspace = self.running_mission(state_dir, task_id=TASK)
        self.herd(root=workspace)
        self.register(source_id="mc", store=state_dir)
        result = self.capture([workspace])
        self.assertTrue(result.is_available(), result)
        response = query_module.query(self.registry, result.snapshot, NOW)
        [mission] = response["missions"]
        self.assertEqual(mission["mission_ref"], "mc:" + mission_id)
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        [member] = mission["members"]
        self.assertEqual((member["classification"], member["role"],
                          member["provider"]),
                         (contract.MEMBER_PRESENT, "executor", "claude"))
        later = query_module.query(self.registry, result.snapshot, NOW + 61)
        self.assertEqual(later["missions"][0]["members"], [])
        absent = query_module.query(self.registry, None, NOW)
        self.assertEqual(absent["runtime_snapshot"]["health"],
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(absent["missions"][0]["members"], [])

    def test_observed_empty_is_distinct_from_could_not_look(self):
        idle = self.herd(task=False)
        result = self.capture([idle], stdout=listing_stdout([]))
        self.assertTrue(result.is_available())
        self.assertEqual((result.snapshot["tasks"], result.snapshot["agents"]),
                         ([], []))
        failed = self.capture([idle], exit=1)
        self.assertFalse(failed.is_available())
        self.assertEqual(failed.problem, service.PROBLEM_LISTING_UNAVAILABLE)

    def test_a_stopped_herd_records_its_task_without_bindings(self):
        root = self.herd(runtime=False)
        self.assertEqual(self.capture([root]).snapshot["tasks"], [{
            "task_id": TASK, "workspace_realpath": root, "bindings": []}])

    def test_role_is_derived_only_from_the_engine_naming(self):
        names = {"supervisor": "a", "lead1": "b", "executor12": "c",
                 "reviewer1": "d", "controller": "e", "lead0": "f",
                 "executor": "g", "Lead1": "h"}
        result = self.capture([self.herd(agents=names)])
        roles = {b["logical"]: b["role"]
                 for b in result.snapshot["tasks"][0]["bindings"]}
        self.assertEqual(roles, {
            "supervisor": "supervisor", "lead1": "lead",
            "executor12": "executor", "reviewer1": "reviewer",
            "controller": None, "lead0": None, "executor": None,
            "Lead1": None})

    def test_capture_writes_nothing(self):
        root = self.herd()
        argv = self.plan()
        before = self.tree()
        service.capture([root], self.clock, argv)
        self.assertEqual(self.tree(), before)


class BindingParityTests(Fixture):
    """Bound means what ``herdr.identity.binding_gap`` says, and nothing the
    native definition calls unbound can become a member downstream."""

    def mission_with(self, roles, agents=None, panes=None, rows=None):
        # A FRESH store and registry entry per call: two Missions claiming
        # one task would be ambiguous and make "no members" vacuous.
        state_dir, source_id = self.fresh_store()
        _mission_id, workspace = self.running_mission(state_dir,
                                                      task_id=TASK)
        self.herd(root=workspace, agents=agents, panes=panes, roles=roles)
        self.register(source_id=source_id, store=state_dir)
        result = self.capture([workspace],
                              stdout=listing_stdout(rows))
        self.assertTrue(result.is_available(), result)
        response = query_module.query(self.registry, result.snapshot, NOW)
        [mission] = [m for m in response["missions"]
                     if m["mission_ref"].startswith(source_id + ":")]
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        return result.snapshot, mission

    def test_incomplete_bindings_are_unbound_exactly_as_native(self):
        row = engine_row()
        complete = {"logical": "executor1", "agent": "pod-exec1",
                    "session": "sess-executor1", "stable": stable()}
        cases = {
            "stable absent": {k: v for k, v in complete.items()
                              if k != "stable"},
            "stable empty": dict(complete, stable={}),
            "stable not a mapping": dict(complete, stable="w1:p3"),
            "stable a list": dict(complete, stable=[stable()]),
            "session absent": {k: v for k, v in complete.items()
                               if k != "session"},
            "session empty": dict(complete, session=""),
            "agent absent": {k: v for k, v in complete.items()
                             if k != "agent"},
            "entry not a mapping": "pod-exec1",
            "entry absent": None,
        }
        for label, entry in cases.items():
            with self.subTest(label):
                # Native parity: the engine's single definition and its
                # classify both say UNBOUND for this record.
                self.assertIsNotNone(engine_identity.binding_gap(entry))
                verdict = engine_identity.classify(
                    "executor1", entry if isinstance(entry, dict) else None,
                    present_info(row))
                self.assertEqual(verdict.verdict,
                                 engine_identity.VERDICT_UNBOUND)
                roles = {} if entry is None else {"executor1": entry}
                snapshot, mission = self.mission_with(roles)
                self.assertEqual(snapshot["tasks"][0]["bindings"], [])
                self.assertEqual(mission["members"], [])

    def test_the_native_verdict_and_the_roster_agree_never_looser(self):
        entry = {"logical": "executor1", "agent": "pod-exec1",
                 "session": "sess-executor1", "stable": stable()}
        self.assertIsNone(engine_identity.binding_gap(entry))
        expected = {
            engine_identity.VERDICT_PRESENT: contract.MEMBER_PRESENT,
            engine_identity.VERDICT_REPLACED: contract.MEMBER_REPLACED,
            engine_identity.VERDICT_DEGRADED: contract.MEMBER_AMBIGUOUS,
        }
        rows = {
            "matching": engine_row(),
            "new session": engine_row(session="sess-new"),
            # cwd is NOT in the snapshot contract: only the native
            # comparison can see this disagreement.
            "cwd differs": engine_row(cwd="/private/should/not/leak"),
            "pane differs": engine_row(pane="w1:p9"),
            "workspace differs": engine_row(workspace="w9"),
            "no live session": dict(engine_row(), agent_session=None),
        }
        for label, row in rows.items():
            with self.subTest(label):
                native = engine_identity.classify(
                    "executor1", entry, present_info(row)).verdict
                _snapshot, mission = self.mission_with(
                    {"executor1": entry}, rows=[row])
                [member] = mission["members"]
                self.assertEqual(member["classification"], expected[native],
                                 (label, native))

    def test_identity_is_recorded_verbatim_never_inherited(self):
        # A partial stable identity (no pane, no workspace) still BINDS
        # natively, but nothing is inherited from runtime.json: unknown, so
        # the roster cannot call it present.
        entry = {"logical": "executor1", "agent": "pod-exec1",
                 "session": "sess-executor1", "stable": {"name": "pod-exec1"}}
        self.assertIsNone(engine_identity.binding_gap(entry))
        snapshot, mission = self.mission_with({"executor1": entry})
        [binding] = snapshot["tasks"][0]["bindings"]
        self.assertEqual((binding["pane_id"], binding["workspace_id"]),
                         (None, None))
        self.assertEqual(mission["members"][0]["classification"],
                         contract.MEMBER_AMBIGUOUS)

    def test_disagreeing_records_leave_identity_unknown(self):
        cases = {
            "runtime pane differs": dict(stable=stable(pane="w9:p1")),
            "runtime workspace differs": dict(stable=stable(workspace="w9")),
            "stable names another agent": dict(stable=stable(name="other")),
        }
        for label, override in cases.items():
            with self.subTest(label):
                entry = dict({"logical": "executor1", "agent": "pod-exec1",
                              "session": "sess-executor1"}, **override)
                snapshot, mission = self.mission_with({"executor1": entry})
                [binding] = snapshot["tasks"][0]["bindings"]
                self.assertEqual((binding["pane_id"],
                                  binding["workspace_id"]), (None, None))
                self.assertNotEqual(mission["members"][0]["classification"],
                                    contract.MEMBER_PRESENT)

    def test_a_binding_for_another_agent_or_logical_is_not_this_role(self):
        for entry in (
                {"logical": "executor1", "agent": "old-exec", "session": "s",
                 "stable": stable(name="old-exec")},
                {"logical": "reviewer1", "agent": "pod-exec1", "session": "s",
                 "stable": stable()}):
            with self.subTest(entry=entry["agent"] + entry["logical"]):
                snapshot, mission = self.mission_with({"executor1": entry})
                self.assertEqual(snapshot["tasks"][0]["bindings"], [])
                self.assertEqual(mission["members"], [])


class TaskSemanticsTests(Fixture):
    """task.json is the native current-task authority; the association of
    roles with it is derived, so replacement and retained state are handled
    explicitly."""

    def test_a_terminal_task_is_not_a_current_task(self):
        for status in ("COMPLETE", "completed", " Failed ", "ABANDONED"):
            with self.subTest(status):
                result = self.capture([self.herd(status=status)])
                self.assertTrue(result.is_available())
                self.assertEqual(result.snapshot["tasks"], [])
        result = self.capture([self.herd(status=None)])
        self.assertEqual(len(result.snapshot["tasks"]), 1)

    def test_task_replacement_moves_the_association(self):
        state_dir = self.mission_state()
        mission_id, workspace = self.running_mission(state_dir, task_id=TASK)
        self.herd(root=workspace, task_id=OTHER_TASK)
        self.register(source_id="mc", store=state_dir)
        result = self.capture([workspace])
        self.assertEqual([t["task_id"] for t in result.snapshot["tasks"]],
                         [OTHER_TASK])
        mission = query_module.query(self.registry, result.snapshot,
                                     NOW)["missions"][0]
        self.assertEqual(mission["mission_ref"], "mc:" + mission_id)
        self.assertNotEqual(mission["binding"]["state"],
                            contract.BINDING_BOUND)
        self.assertEqual(mission["members"], [])

    def test_retained_runtime_state_cannot_make_anyone_present(self):
        for label, rows in (
                ("no live agents", []),
                ("names reused elsewhere",
                 [engine_row(pane="w7:p1", workspace="w7")]),
                ("a new session", [engine_row(session="sess-new")])):
            with self.subTest(label):
                state_dir, source_id = self.fresh_store()
                _mission_id, workspace = self.running_mission(
                    state_dir, task_id=TASK)
                self.herd(root=workspace)
                self.register(source_id=source_id, store=state_dir)
                result = self.capture([workspace],
                                      stdout=listing_stdout(rows))
                [mission] = query_module.query(
                    self.registry, result.snapshot, NOW)["missions"]
                self.assertEqual(mission["binding"]["state"],
                                 contract.BINDING_BOUND)
                self.assertTrue(all(
                    m["classification"] != contract.MEMBER_PRESENT
                    for m in mission["members"]), mission["members"])


class ListingFailureTests(Fixture):
    """Real transport, fake boundary: fails toward unavailable, and nothing
    is ever adopted from noise."""

    def assert_refused(self, problem, **plan):
        captured = self.capture([self.herd()], **plan)
        self.assertIsNone(captured.snapshot)
        self.assertEqual(captured.problem, problem, captured)
        return captured

    def test_a_listing_that_exited_badly_or_was_not_started(self):
        for code in (1, 2, 127):
            self.assert_refused(service.PROBLEM_LISTING_UNAVAILABLE,
                                exit=code)
        original = service._owned_run
        for result in ({"started": False, "error": "FileNotFoundError"},
                       {"started": True, "error": "OSError",
                        "timed_out": False, "returncode": 0, "stdout": "",
                        "stdout_truncated_at_read": False}, None, "x"):
            def listing_only(argv, result=result):
                return result if argv[1] == self.fake else original(argv)
            with mock.patch.object(service, "_owned_run", listing_only):
                captured = self.capture([self.herd()])
            self.assertEqual(captured.problem,
                             service.PROBLEM_LISTING_UNAVAILABLE)

    def test_noisy_partial_and_malformed_stdout(self):
        good = listing_stdout()
        cases = [
            "warning: config reloaded\n" + good,
            good + "[debug] listed 1 agent\n",
            "[stderr leaked] " + good,
            good[: len(good) // 2],
            good + good,
            "",
            "   \n",
            "null",
            "[]",
            json.dumps({"result": {}}),
            json.dumps({"result": {"agents": {"a": 1}}}),
            "[" * 200 + "]" * 200,
        ]
        for stdout in cases:
            with self.subTest(stdout=stdout[:40]):
                self.assert_refused(service.PROBLEM_LISTING_MALFORMED,
                                    stdout=stdout)

    def test_output_beyond_the_read_cap_is_truncated_never_parsed(self):
        rows = [engine_row(name="a%d" % i) for i in range(200)]
        stdout = listing_stdout(rows)
        self.assertGreater(len(stdout), engine_runtime.DIAGNOSTIC_READ_MAX_BYTES)
        self.assert_refused(service.PROBLEM_LISTING_TRUNCATED, stdout=stdout)

    def test_one_unreadable_row_degrades_the_whole_listing(self):
        rows = [
            "a row",
            dict(engine_row(), name=None),
            dict(engine_row(), name=""),
            dict(engine_row(), name="n" * (contract.MAX_SNAPSHOT_TEXT_CHARS
                                           + 1)),
            dict(engine_row(), agent_status=7),
            dict(engine_row(), agent=""),
            dict(engine_row(), agent_session="sess"),
            dict(engine_row(), agent_session={"value": 5}),
            dict(engine_row(), pane_id=["w1:p3"]),
        ]
        for row in rows:
            with self.subTest(row=repr(row)[:60]):
                self.assert_refused(
                    service.PROBLEM_LISTING_MALFORMED,
                    stdout=listing_stdout([engine_row(name="ok"), row]))

    def test_more_agents_than_the_contract_bound(self):
        rows = [{"name": "a%d" % i, "agent_status": "idle"}
                for i in range(contract.MAX_SNAPSHOT_AGENTS + 1)]
        self.assert_refused(service.PROBLEM_EXCEEDS_BOUND,
                            stdout=json.dumps({"result": {"agents": rows}}))

    def test_optional_fields_absent_are_unknown_not_refused(self):
        row = engine_row()
        for key in ("agent", "agent_session", "pane_id", "workspace_id"):
            del row[key]
        captured = self.capture([self.herd()], stdout=listing_stdout([row]))
        self.assertEqual(captured.snapshot["agents"], [{
            "agent": "pod-exec1", "provider": None, "status": "working",
            "session": None, "pane_id": None, "workspace_id": None}])


class OwnedTerminationTests(Fixture):
    """The listing's process GROUP is killed at the runner's deadline and its
    ABSENCE is established before the call returns: every pid it started is
    already gone AT RETURN (no tolerance window), nothing accumulates, and
    nothing arriving late can be taken as an observation."""

    def setUp(self):
        super(OwnedTerminationTests, self).setUp()
        patcher = mock.patch.object(
            engine_runtime, "DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS", 1.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hung(self, **plan):
        pidfile = os.path.join(self.root, "pids-%d.json" % len(os.listdir(
            self.root)))
        started = time.monotonic()
        captured = self.capture([self.herd()], pidfile=pidfile, **plan)
        return captured, time.monotonic() - started, self.pids(pidfile)

    def test_a_hanging_listing_and_its_grandchild_are_killed(self):
        captured, elapsed, pids = self.hung(sleep_before=30, grandchild=True)
        self.assertEqual(captured.problem, service.PROBLEM_LISTING_TIMED_OUT)
        self.assertIsNone(captured.snapshot)
        self.assertLess(elapsed, 6.0)
        self.assertEqual(len(pids), 2)
        self.assert_gone(pids)

    def test_repeated_hangs_accumulate_nothing(self):
        threads = threading.active_count()
        for _ in range(3):
            captured, elapsed, pids = self.hung(sleep_before=30,
                                                grandchild=True)
            self.assertEqual(captured.problem,
                             service.PROBLEM_LISTING_TIMED_OUT)
            self.assertLess(elapsed, 6.0)
            self.assert_gone(pids)
            self.assertEqual(threading.active_count(), threads)

    def test_a_late_result_is_never_taken_as_an_observation(self):
        captured, _elapsed, pids = self.hung(sleep_before=2.0)
        self.assertEqual(captured.problem, service.PROBLEM_LISTING_TIMED_OUT)
        self.assertIsNone(captured.snapshot)
        self.assert_gone(pids)
        # Its producer is dead and its pipe closed: nothing can arrive
        # later. The next capture is its own observation, at its own time,
        # with only its own content.
        time.sleep(1.2)
        self.clock.now = NOW + 5
        fresh = self.capture([self.herd()], stdout=listing_stdout(
            [engine_row(name="pod-new")]))
        self.assertEqual(fresh.observed_at, NOW + 5)
        self.assertEqual([a["agent"] for a in fresh.snapshot["agents"]],
                         ["pod-new"])

    def test_a_complete_document_from_an_unfinished_listing_is_refused(self):
        captured, _elapsed, pids = self.hung(sleep_after=30)
        self.assertEqual(captured.problem, service.PROBLEM_LISTING_TIMED_OUT)
        self.assertIsNone(captured.snapshot)
        self.assert_gone(pids)


class OwnedBoundaryTests(Fixture):
    """The REAL outer boundary around every capture step: a blocked FILE READ
    inside it is killed with its group, repeated timeouts leave nothing, a
    late or forged child value is never taken, and the normal capture works
    through it (every positive test above runs through the real boundary)."""

    def setUp(self):
        super(OwnedBoundaryTests, self).setUp()
        patcher = mock.patch.object(
            engine_runtime, "DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS", 1.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pidfile(self):
        self._pidfiles = getattr(self, "_pidfiles", 0) + 1
        return os.path.join(self.root, "pids-%d.json" % self._pidfiles)

    def fifo(self):
        path = os.path.join(self.root, "never-written-%d.fifo" % len(
            os.listdir(self.root)))
        os.mkfifo(path)
        return path

    def timed(self, roots, **plan):
        started = time.monotonic()
        captured = self.capture(roots, **plan)
        return captured, time.monotonic() - started

    def test_a_blocked_file_read_inside_the_boundary_is_killed(self):
        for mode in (service.MODE_PASS, service.MODE_PROJECT):
            with self.subTest(mode):
                pids, listing_pids = self.pidfile(), self.pidfile()
                hang = {"fifo": self.fifo(), "grandchild": True,
                        "pidfile": pids}
                self.substitute(**{mode: hang} if mode == service.MODE_PASS
                                else {service.MODE_PASS: {"delegate": True},
                                      mode: hang})
                captured, elapsed = self.timed([self.herd()],
                                               pidfile=listing_pids)
                self.assertIsNone(captured.snapshot)
                self.assertEqual(captured.problem,
                                 service.PROBLEM_BOUNDARY_TIMED_OUT)
                self.assertIn(mode, captured.detail)
                self.assertLess(elapsed, 6.0)
                self.assert_gone(self.pids(pids))
                # A hang in the first pass means the listing never started.
                self.assertEqual(os.path.exists(listing_pids),
                                 mode == service.MODE_PROJECT)

    def test_repeated_timeouts_leave_nothing_running(self):
        threads = threading.active_count()
        survivors = []
        for _ in range(3):
            pids = self.pidfile()
            self.substitute(**{service.MODE_PASS: {
                "fifo": self.fifo(), "grandchild": True, "pidfile": pids}})
            captured, elapsed = self.timed([self.herd()])
            self.assertEqual(captured.problem,
                             service.PROBLEM_BOUNDARY_TIMED_OUT)
            self.assertLess(elapsed, 6.0)
            self.assert_gone(self.pids(pids))
            survivors.extend(self.pids(pids))
            self.assertEqual(threading.active_count(), threads)
        self.assertEqual(len(survivors), 6)
        self.assert_gone(survivors)

    def test_a_late_or_unfinished_child_value_is_never_taken(self):
        root = self.herd()
        valid = json.dumps({"ok": True, "value": service._project(
            self.payload([root]))})
        for label, step in (
                ("printed after the deadline",
                 {"sleep_before": 2.0, "stdout": valid}),
                ("printed, then never finished",
                 {"stdout": valid, "sleep_after": 30})):
            with self.subTest(label):
                pids = self.pidfile()
                self.substitute(**{service.MODE_PASS: {"delegate": True},
                                   service.MODE_PROJECT: dict(
                                       step, pidfile=pids)})
                captured, _elapsed = self.timed([root])
                self.assertIsNone(captured.snapshot)
                self.assertEqual(captured.problem,
                                 service.PROBLEM_BOUNDARY_TIMED_OUT)
                self.assert_gone(self.pids(pids))
        # The next capture is its own observation, with its own content.
        mock.patch.stopall()
        self.allowed_codes = {service._BOUNDARY_CODE}
        self.setUp_fence_only()
        self.clock.now = NOW + 5
        fresh = self.capture([root], stdout=listing_stdout(
            [engine_row(name="pod-new")]))
        self.assertEqual(fresh.observed_at, NOW + 5)
        self.assertEqual([a["agent"] for a in fresh.snapshot["agents"]],
                         ["pod-new"])

    def test_production_constants_return_responsively_while_work_holds(self):
        """PRODUCTION runner deadline (5 s): a FILE READ that never returns.
        A long-lived owner's request returns at ITS wait, not the runner's;
        the slot stays held until the boundary's group is killed and
        observed absent; then the owner is available again."""
        mock.patch.stopall()                 # the class's 1 s deadline off
        self.setUp_fence_only()
        self.assertEqual(engine_runtime.DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS,
                         5.0)
        pids = self.pidfile()
        self.substitute(**{service.MODE_PASS: {
            "fifo": self.fifo(), "grandchild": True, "pidfile": pids}})
        owner = self.owner([self.herd()], self.plan())
        started = time.monotonic()
        first = owner.request(1)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(first.problem, service.PROBLEM_CAPTURE_PENDING)
        self.assertEqual(owner.request(1).problem,
                         service.PROBLEM_SLOT_OCCUPIED)
        state = owner.settle(15)
        self.assertEqual(state["work"], service.WORK_SETTLED, state)
        self.assert_gone(self.pids(pids))

    def test_a_successful_capture_leaves_every_owned_group_quiescent(self):
        firsts, seconds = self.pidfile(), self.pidfile()
        listing_pids = self.pidfile()
        self.substitute(**{
            service.MODE_PASS: {"delegate": True, "linger": firsts},
            service.MODE_PROJECT: {"delegate": True, "linger": seconds}})
        captured = self.capture([self.herd()], linger=True,
                                pidfile=listing_pids)
        self.assertTrue(captured.is_available(), captured)
        lingering = (self.pids(firsts) + self.pids(seconds)
                     + self.pids(listing_pids))
        # One lingerer per boundary child, plus the listing and its own.
        self.assertEqual(len(lingering), 4)
        self.assert_gone(lingering)

    def test_a_child_cannot_choose_the_observation_or_the_outcome(self):
        root = self.herd()
        forged = service._project(self.payload([root], observed_at=NOW - 30))
        cases = {
            "forged observed_at": (json.dumps({"ok": True, "value": forged}),
                                   0, service.PROBLEM_SELF_CHECK),
            "noise around the result": (
                "warning\n" + json.dumps({"ok": True, "value": forged}), 0,
                service.PROBLEM_BOUNDARY_FAILED),
            "unknown refusal code": (json.dumps({
                "ok": False, "problem": "approved", "detail": None}), 0,
                service.PROBLEM_BOUNDARY_FAILED),
            "a defect exit": ("", 1, service.PROBLEM_BOUNDARY_FAILED),
            "output over the cap": ("x" * 70000, 0,
                                    service.PROBLEM_BOUNDARY_FAILED),
        }
        for label, (stdout, code, problem) in cases.items():
            with self.subTest(label):
                self.substitute(**{service.MODE_PASS: {"delegate": True},
                                   service.MODE_PROJECT: {
                                       "stdout": stdout, "exit": code}})
                captured = self.capture([root])
                self.assertIsNone(captured.snapshot)
                self.assertEqual(captured.problem, problem)


#: An isolated child for interpreter-exit and one-shot lifetimes: the runner's
#: ``Popen`` is replaced by a stall that ends by itself and then raises, so
#: NOTHING can be started in it. It prints the request's problem and exits.
LIFETIME_CHILD = r'''
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from target_runtime import runtime_snapshot as service
mode, marker, root = sys.argv[2], sys.argv[3], sys.argv[4]
def stalled(*args, **kwargs):
    time.sleep(1.5)
    with open(marker, "w") as handle:
        handle.write("the owned worker reached the end of its run")
    raise OSError("the stalled start failed: nothing was started")
subprocess.Popen = stalled
argv = [sys.executable, "-c", "pass"]
if mode == "owner":
    # A long-lived owner abandoned by its process: no shutdown, no settle.
    owner = service.SnapshotService([root], lambda: 1800000000, argv)
    print(owner.request(0.2).problem, flush=True)
else:
    print(service.capture([root], lambda: 1800000000, argv, 0.2).problem,
          flush=True)
'''


#: Regression (b) runs in an ISOLATED CHILD: one ``Bounded`` inner test
#: (watchdog 1 s) runs the REAL one-shot ``capture`` while an OWNED listing
#: process is outstanding (a fake listing that sleeps; the runner's own
#: deadline, 4 s here, is what ends it). Nothing in the child is fenced or
#: faked except that listing's argv, which the parent supplies.
OWNED_EXPIRY_CHILD = r'''
import json, os, signal, sys, threading, time, unittest
root, herd, argv, now = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), int(sys.argv[4])
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tests"))
from herdr import runtime as engine_runtime
from target_runtime import runtime_snapshot as service
import test_mission_sources as base
engine_runtime.DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS = 4.0
class Inner(base.Bounded):
    watchdog_seconds = 1
    def test_one_shot_capture_with_owned_work_outstanding(self):
        service.capture([herd], lambda: now, argv, 10)
result = unittest.TestResult()
started = time.monotonic()
Inner("test_one_shot_capture_with_owned_work_outstanding").run(result)
returned = time.monotonic() - started
signal.alarm(0)
print(json.dumps({"returned_after": returned, "problems": [
    text for _test, text in result.errors + result.failures],
    "owner_workers_alive": [t.name for t in threading.enumerate()
                            if t.name == "runtime-snapshot-owner"]}),
      flush=True)
'''


class DiscoveredRootTests(Fixture):
    """Discovered roots are TOLERANT, registered roots stay STRICT, and the
    child's per-root account is checked, through the SAME owner."""

    def test_discovered_roots_are_accounted_root_by_root(self):
        good = self.herd()
        missing = os.path.join(self.root, "no-such-workspace")
        broken = self.herd()
        self.write(self.state_path(broken, "")[:-1], "task.json", None,
                   raw="{not json")
        owner = self.owner([], self.plan())
        got = owner.request(service.MAX_WAIT_SECONDS,
                            sorted([good, missing, broken]))
        self.assertTrue(got.is_available(), got)
        self.assertEqual(got.roots["captured"], [good])
        self.assertEqual(
            sorted((u["root"], u["problem"]) for u in got.roots["unavailable"]),
            sorted([(missing, service.PROBLEM_ROOTS_INVALID),
                    (broken, service.PROBLEM_STATE_MALFORMED)]))
        self.assertEqual([t["workspace_realpath"]
                          for t in got.snapshot["tasks"]], [good])

    def test_registered_roots_stay_strict(self):
        missing = os.path.join(self.root, "no-such-workspace")
        owner = self.owner([missing], self.plan())
        got = owner.request(service.MAX_WAIT_SECONDS, [self.herd()])
        self.assertIsNone(got.snapshot)
        self.assertEqual(got.problem, service.PROBLEM_ROOTS_INVALID)

    def test_a_forged_first_pass_account_is_refused(self):
        good = self.herd()
        forged = json.dumps({"ok": True, "value": {"digests": [], "discovered": [
            {"root": "/somewhere/else", "digests": [[0, None]] * 3}]}})
        self.substitute(**{service.MODE_PASS: {"stdout": forged}})
        got = self.owner([], self.plan()).request(
            service.MAX_WAIT_SECONDS, [good])
        self.assertIsNone(got.snapshot)
        self.assertEqual(got.problem, service.PROBLEM_BOUNDARY_FAILED)


class WatchdogOwnedWorkTests(Fixture):
    """Regression (b), EXTERNALLY bounded (``subprocess.run(timeout=60)``):
    the shared watchdog expires while a one-shot capture's OWNED work is
    outstanding, and the capture's ``finally`` then enters ``settle(None)``,
    a blocking cleanup. The re-armed watchdog interrupts that too, so the
    TEST is bounded; the PROCESS does not abandon the owned work."""

    def test_expiry_with_owned_work_outstanding(self):
        pidfile = os.path.join(self.root, "owned-listing.json")
        argv = self.plan(sleep_before=30, pidfile=pidfile)
        herd = self.herd()
        script = os.path.join(self.root, "owned-expiry-child.py")
        with open(script, "w") as handle:
            handle.write(OWNED_EXPIRY_CHILD)
        started = time.monotonic()
        done = subprocess.run(
            [sys.executable, "-I", "-B", script, service._REPO_ROOT, herd,
             json.dumps(argv), str(NOW)], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=60)
        exited_after = time.monotonic() - started
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout.decode().strip().splitlines()[-1])
        # The TEST was bounded although owned work was outstanding: the
        # expiry and its re-armed repeat ended request() and settle(None).
        self.assertLess(report["returned_after"], 3.5)
        self.assertTrue(any("WatchdogExpired" in text
                            for text in report["problems"]), report)
        self.assertEqual(report["owner_workers_alive"],
                         ["runtime-snapshot-owner"])
        # Disposition. The process did NOT abandon the owned work: it exited
        # only after the runner's own 4 s deadline (non-daemon worker), and
        # the owned listing -- its group's leader -- was killed and reaped
        # by then. Not proven here: anything beyond that leader (this fake
        # listing starts no descendant; no process table was scanned).
        self.assertGreaterEqual(exited_after, 4.0)
        [listing] = self.pids(pidfile)
        with self.assertRaises(ProcessLookupError):
            os.kill(listing, 0)


class OwnerContractTests(Fixture):
    """The owner's caller contract: responsive return, ONE slot, capacity
    released ONLY on proof (the runner's exact not-started report, or a
    reaped leader whose group is then observed ABSENT), honest lifetimes.
    Isolated fakes and real children only; a stalled start is a FAKE
    ``Popen`` that never starts anything."""

    def stalled_start(self):
        stall = StalledStart()
        self._stalls.append(stall)
        stall.patcher = mock.patch.object(engine_runtime.subprocess, "Popen",
                                          stall)
        stall.patcher.start()
        self.addCleanup(stall.patcher.stop)
        self.addCleanup(stall.release.set)
        return stall

    def reported(self, report):
        """The runner replaced by ONE fixed report (no execution at all)."""
        calls = []

        def runner(argv):
            calls.append(list(argv))
            return report
        patcher = mock.patch.object(engine_runtime, "_bounded_command",
                                    runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def foreign_group(self):
        """A REAL live group that is NOT the capture's (as a reused id would
        be): its own session, ended and reaped by this test."""
        child = subprocess.Popen([sys.executable, "-c",
                                  "import time; time.sleep(30)"],
                                 start_new_session=True,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)

        def end():
            child.kill()
            child.wait(10)
        self.addCleanup(end)
        return child.pid

    # -- capacity is released only on proof --------------------------------

    def test_repeated_successful_captures_leave_the_owner_available(self):
        """Would have caught a probe that reads the unreaped leader's zombie
        as a live member: every success would leave the owner unavailable."""
        reports = self.spy_runner()
        sent = self.spy_signals()
        owner = self.owner([self.herd()], self.plan())
        for round_ in range(5):
            self.clock.now = NOW + round_
            got = owner.request(service.MAX_WAIT_SECONDS)
            self.assertTrue(got.is_available(), got)
            self.assertEqual(got.observed_at, NOW + round_)
            self.assertEqual(owner.diagnostics(), {
                "accepting": True, "slot": "free",
                "work": service.WORK_SETTLED})
            # The condition held BEFORE the return: each group is absent.
            for report in reports[-3:]:
                with self.assertRaises(ProcessLookupError):
                    os.killpg(report["process_group"], 0)
        self.assertEqual(len(reports), 15)
        for report in reports:
            group = report["process_group"]
            signals = [sig for pgid, sig in sent if pgid == group]
            # The runner's own pre-reap SIGKILL, then existence-only
            # observations: nothing is ever signalled after the reap.
            self.assertEqual(signals[0], signal.SIGKILL)
            self.assertTrue(signals[1:])
            self.assertEqual(set(signals[1:]), {0})

    def test_a_lingering_member_holds_capacity_until_it_is_absent(self):
        """A REAL member that stays in the owned group after its SIGKILL (a
        zombie whose parent left the group and never reaps it): no fresh
        success while it remains; capacity returns only on its absence."""
        pidfile = os.path.join(self.root, "zombie-pids.json")
        argv = self.plan(zombie_member=20, pidfile=pidfile)
        reports = self.spy_runner()
        owner = self.owner([self.herd()], argv)
        first = owner.request(service.MAX_WAIT_SECONDS)
        _leader, parent, member = self.pids(pidfile)
        self.addCleanup(self.escaped_parent, parent)
        self.assertIsNone(first.snapshot)
        self.assertEqual(first.problem, service.PROBLEM_CLEANUP_UNESTABLISHED)
        state = owner.diagnostics()
        self.assertEqual((state["slot"], state["work"]),
                         ("occupied", service.WORK_UNCERTAIN))
        runs = len(reports)
        for _ in range(2):
            self.assertEqual(owner.request(1).problem,
                             service.PROBLEM_SLOT_OCCUPIED)
        self.assertEqual(len(reports), runs)      # nothing new was started
        self.assertEqual(owner.settle(1)["work"], service.WORK_UNCERTAIN)
        self.escaped_parent(parent)
        self.wait_reaped(member)
        self.assertEqual(owner.settle(1)["work"], service.WORK_SETTLED)
        with open(argv[2], "w") as handle:        # the next listing is plain
            json.dump({"stdout": listing_stdout()}, handle)
        self.assertTrue(owner.request(service.MAX_WAIT_SECONDS)
                        .is_available())

    def test_runner_reports_that_prove_nothing_hold_capacity(self):
        """Only the runner's EXACT not-started report, or a reaped leader
        whose group is observed ABSENT, releases capacity."""
        foreign = self.foreign_group()
        cases = {
            "not a mapping": None,
            "missing started": {"returncode": 0, "timed_out": False},
            "malformed started": {"started": 1, "error": "OSError"},
            "started as text": {"started": "False", "error": "OSError"},
            "not started, beside a group": {
                "started": False, "error": "OSError", "process_group": 4242},
            "not started, without its error": {"started": False},
            "started, group unnamed": {"started": True, "returncode": 0},
            "leader not reaped": {"started": True, "returncode": None,
                                  "process_group": foreign},
            "reaped, but the id exists": {"started": True, "returncode": 0,
                                          "process_group": foreign},
        }
        for label, report in cases.items():
            with self.subTest(label):
                sent = self.spy_signals()
                calls = self.reported(report)
                owner = self.owner([self.herd()], self.plan())
                got = owner.request(5)
                self.assertEqual(got.problem,
                                 service.PROBLEM_CLEANUP_UNESTABLISHED)
                state = owner.diagnostics()
                self.assertEqual((state["slot"], state["work"]),
                                 ("occupied", service.WORK_UNCERTAIN))
                self.assertEqual(owner.request(1).problem,
                                 service.PROBLEM_SLOT_OCCUPIED)
                self.assertEqual(len(calls), 1)
                self.assertEqual({sig for _pgid, sig in sent} - {0}, set())
                if label == "leader not reaped":
                    self.assertEqual(sent, [])    # its zombie is never probed
                mock.patch.stopall()
                self.setUp_fence_only()
        # The positive: the runner's exact native not-started report.
        self.reported({"started": False, "error": "OSError"})
        owner = self.owner([self.herd()], self.plan())
        self.assertEqual(owner.request(5).problem,
                         service.PROBLEM_BOUNDARY_FAILED)
        self.assertEqual(owner.diagnostics()["work"], service.WORK_SETTLED)

    # -- responsive return and one slot -------------------------------------

    def test_a_stalled_start_returns_responsively_and_holds_the_slot(self):
        stall = self.stalled_start()
        owner = self.owner([self.herd()], self.plan())
        started = time.monotonic()
        first = owner.request(0.3)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(first.problem, service.PROBLEM_CAPTURE_PENDING)
        state = owner.diagnostics()
        self.assertEqual((state["slot"], state["work"], state["phase"]),
                         ("occupied", service.WORK_PENDING, "first pass"))
        for _ in range(3):
            self.assertEqual(owner.request(0.1).problem,
                             service.PROBLEM_SLOT_OCCUPIED)
        self.assertEqual(len(stall.calls), 1)     # no replacement started
        stall.release.set()
        self.assertEqual(owner.settle(10)["work"], service.WORK_SETTLED)
        stall.patcher.stop()
        self.assertIs(engine_runtime.subprocess.Popen, REAL_POPEN)
        self.assertTrue(owner.request(service.MAX_WAIT_SECONDS)
                        .is_available())

    def test_an_interrupted_start_holds_the_slot(self):
        owner = self.owner([self.herd()], self.plan())
        with mock.patch.object(service.threading.Thread, "start",
                               side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                owner.request(1)
        state = owner.diagnostics()
        self.assertEqual((state["slot"], state["work"]),
                         ("occupied", service.WORK_UNCERTAIN))
        self.assertIn("start was interrupted", state["held_because"])
        self.assertEqual(owner.request(1).problem,
                         service.PROBLEM_SLOT_OCCUPIED)

    def test_over_length_input_is_refused_before_anything_starts(self):
        calls = self.reported({"started": False, "error": "unused"})
        root = self.herd()
        cases = (
            ([root + "/" + "a" * service.MAX_ROOT_PATH_CHARS], self.plan(),
             service.PROBLEM_INPUT_TOO_LARGE),
            ([root], self.plan() + ["x" * (
                service.MAX_ARGV_ELEMENT_CHARS + 1)],
             service.PROBLEM_INPUT_TOO_LARGE),
            ([root] * (service.MAX_HERD_ROOTS + 1), self.plan(),
             service.PROBLEM_ROOTS_INVALID),
        )
        for roots, argv, problem in cases:
            owner = self.owner(roots, argv)
            self.assertEqual(owner.request(1).problem, problem)
            self.assertEqual(owner.diagnostics()["slot"], "free")
        with mock.patch.object(service, "MAX_PAYLOAD_BYTES", 16):
            owner = self.owner([root], self.plan())
            got = owner.request(1)
        self.assertEqual(got.problem, service.PROBLEM_INPUT_TOO_LARGE)
        self.assertIn("nothing was started", got.detail)
        self.assertEqual(calls, [])

    # -- lifetimes -----------------------------------------------------------

    def test_shutdown_refuses_new_work_and_keeps_existing_work(self):
        stall = self.stalled_start()
        owner = self.owner([self.herd()], self.plan())
        self.assertEqual(owner.request(0.2).problem,
                         service.PROBLEM_CAPTURE_PENDING)
        state = owner.shutdown()
        self.assertEqual((state["accepting"], state["work"]),
                         (False, service.WORK_PENDING))
        refused = owner.request(0.2)
        self.assertEqual(refused.problem, service.PROBLEM_OWNER_SHUT_DOWN)
        self.assertEqual(len(stall.calls), 1)
        stall.release.set()
        state = owner.settle(10)
        self.assertEqual((state["accepting"], state["work"]),
                         (False, service.WORK_SETTLED))
        self.assertEqual(owner.request(0.2).problem,
                         service.PROBLEM_OWNER_SHUT_DOWN)

    def test_shutdown_reports_uncertain_work_as_uncertain(self):
        self.reported({"started": True, "returncode": None,
                       "process_group": self.foreign_group()})
        owner = self.owner([self.herd()], self.plan())
        owner.request(5)
        self.assertEqual(owner.shutdown()["work"], service.WORK_UNCERTAIN)
        self.assertEqual(owner.settle(1)["work"], service.WORK_UNCERTAIN)

    def test_the_one_shot_waits_for_its_owned_work(self):
        stall = self.stalled_start()
        timer = threading.Timer(1.0, stall.release.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        got = service.capture([self.herd()], self.clock, self.plan(), 0.2)
        self.assertGreaterEqual(time.monotonic() - started, 1.0)
        self.assertEqual(got.problem, service.PROBLEM_CAPTURE_PENDING)
        self.assertFalse([t for t in threading.enumerate()
                          if t.name == "runtime-snapshot-owner"])

    def test_the_one_shot_lifecycle_runs_on_error_paths(self):
        calls = []
        real_shutdown = service.SnapshotService.shutdown
        real_settle = service.SnapshotService.settle

        def shutdown(owner):
            calls.append("shutdown")
            return real_shutdown(owner)

        def settle(owner, timeout=None):
            calls.append(("settle", timeout))
            return real_settle(owner, timeout)
        with mock.patch.object(service.SnapshotService, "shutdown",
                               shutdown), \
                mock.patch.object(service.SnapshotService, "settle", settle):
            with self.assertRaises(TypeError):
                service.capture([self.herd()], lambda: None, self.plan())
            self.assertEqual(calls, ["shutdown", ("settle", None)])
            del calls[:]
            with mock.patch.object(service, "_listing_stdout",
                                   side_effect=RuntimeError("defect")):
                with self.assertRaises(RuntimeError):
                    service.capture([self.herd()], self.clock, self.plan())
            self.assertEqual(calls, ["shutdown", ("settle", None)])

    def test_process_exit_never_silently_drops_an_owned_worker(self):
        """In an ISOLATED child: an owner abandoned by its process, and the
        one-shot path, both end only after the worker reached the end of
        its run (a daemon worker would be killed at exit, mid-run)."""
        script = os.path.join(self.root, "lifetime-child.py")
        with open(script, "w") as handle:
            handle.write(LIFETIME_CHILD)
        for mode in ("owner", "one-shot"):
            with self.subTest(mode):
                marker = os.path.join(self.root, "marker-" + mode)
                started = time.monotonic()
                done = subprocess.run(
                    [sys.executable, "-I", "-B", script, service._REPO_ROOT,
                     mode, marker, self.herd()],
                    stdin=subprocess.DEVNULL, capture_output=True,
                    timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertGreaterEqual(time.monotonic() - started, 1.5)
                self.assertTrue(os.path.exists(marker))
                self.assertEqual(done.stdout.decode().strip(),
                                 service.PROBLEM_CAPTURE_PENDING)

class CoherenceTests(Fixture):
    """A visible change between the two passes refuses the capture. (This is
    coherence only; binding is tested above.)"""

    def changed(self, root, name, change):
        path = self.state_path(root, name)
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        change(value)
        return [path, json.dumps(value)]

    def test_task_replaced_during_the_listing_is_refused(self):
        root = self.herd()
        captured = self.capture([root], rewrite=[self.changed(
            root, "task.json", lambda v: v.update(id=OTHER_TASK))])
        self.assertIsNone(captured.snapshot)
        self.assertEqual(captured.problem, service.PROBLEM_STATE_INCOHERENT)
        self.assertIn("task.json", captured.detail)

    def test_runtime_and_binding_changes_are_refused(self):
        for name, change in (
                ("runtime.json",
                 lambda v: v["agents"].update(executor1="pod-exec1-new")),
                ("role-bindings.json",
                 lambda v: v["roles"]["executor1"].update(session="s9"))):
            with self.subTest(name=name):
                root = self.herd()
                captured = self.capture([root], rewrite=[self.changed(
                    root, name, change)])
                self.assertEqual(captured.problem,
                                 service.PROBLEM_STATE_INCOHERENT)
                self.assertIn(name, captured.detail)

    def test_a_file_appearing_or_vanishing_is_refused(self):
        root = self.herd(runtime=False)
        captured = self.capture([root], rewrite=[[
            self.state_path(root, "runtime.json"),
            json.dumps({"version": 2, "repo": root, "agents": {}})]])
        self.assertEqual(captured.problem, service.PROBLEM_STATE_INCOHERENT)
        root = self.herd()
        captured = self.capture([root], rewrite=[[
            self.state_path(root, "role-bindings.json"), None]])
        self.assertEqual(captured.problem, service.PROBLEM_STATE_INCOHERENT)

    def test_identical_bytes_rewritten_are_not_a_visible_change(self):
        root = self.herd()
        with open(self.state_path(root, "task.json")) as handle:
            same = handle.read()
        captured = self.capture([root], rewrite=[[
            self.state_path(root, "task.json"), same]])
        self.assertTrue(captured.is_available(), captured)

    def test_a_change_in_any_root_refuses_the_whole_capture(self):
        first, second = self.herd(), self.herd(task_id=OTHER_TASK)
        captured = self.capture([first, second], rewrite=[self.changed(
            second, "task.json", lambda v: v.update(status="REVIEW"))])
        self.assertIsNone(captured.snapshot)
        self.assertIn("root 1", captured.detail)


class StateFailureTests(Fixture):
    """A registered root whose state cannot be trusted yields NO snapshot."""

    def assert_refused(self, root, problem):
        captured = self.capture([root])
        self.assertIsNone(captured.snapshot)
        self.assertEqual(captured.problem, problem, captured)

    def test_hostile_files_are_refused_by_the_hardened_reader(self):
        root = self.herd()
        path = self.state_path(root, "task.json")
        outside = os.path.join(self.root, "outside.json")
        os.rename(path, outside)
        os.symlink(outside, path)
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)
        os.unlink(path)
        os.link(outside, path)
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)
        os.unlink(path)
        os.mkfifo(path)
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)
        os.unlink(path)
        with open(path, "w") as handle:
            handle.write(" " * 70000 + "{}")
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe{}")
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)

    def test_a_symlinked_state_directory_is_refused(self):
        root = self.herd()
        real = os.path.join(self.root, "elsewhere")
        os.rename(os.path.join(root, ".herd"), real)
        os.symlink(real, os.path.join(root, ".herd"))
        self.assert_refused(root, service.PROBLEM_STATE_REFUSED)

    def test_malformed_and_inconsistent_state(self):
        cases = [
            ("task.json", None, '{"id": "t", "repo"',
             service.PROBLEM_STATE_MALFORMED),
            ("task.json", None, "[" * 100 + "]" * 100,
             service.PROBLEM_STATE_MALFORMED),
            ("task.json", ["a"], None, service.PROBLEM_STATE_MALFORMED),
            ("task.json", {"repo": "{root}"}, None,
             service.PROBLEM_STATE_MALFORMED),
            ("task.json", {"id": "t", "repo": "{root}", "status": 3}, None,
             service.PROBLEM_STATE_MALFORMED),
            ("task.json", {"id": "t", "repo": "/somewhere/else"}, None,
             service.PROBLEM_STATE_INCONSISTENT),
            ("task.json", {"id": "t"}, None, service.PROBLEM_STATE_MALFORMED),
            ("runtime.json", {"version": 1, "repo": "{root}", "agents": {}},
             None, service.PROBLEM_STATE_MALFORMED),
            ("runtime.json", {"version": 2, "repo": "/else", "agents": {}},
             None, service.PROBLEM_STATE_INCONSISTENT),
            ("runtime.json", {"version": 2, "repo": "{root}",
                              "agents": ["executor1"]}, None,
             service.PROBLEM_STATE_MALFORMED),
            ("runtime.json", {"version": 2, "repo": "{root}",
                              "agents": {"executor1": 5}}, None,
             service.PROBLEM_STATE_MALFORMED),
            ("runtime.json", {"version": 2, "repo": "{root}",
                              "agents": {"executor1": "a"}, "panes": []},
             None, service.PROBLEM_STATE_MALFORMED),
            ("role-bindings.json", {"version": 1}, None,
             service.PROBLEM_STATE_MALFORMED),
        ]
        for name, value, raw, problem in cases:
            with self.subTest(name=name, value=value, raw=(raw or "")[:20]):
                root = self.herd()
                if isinstance(value, dict):
                    value = {k: (root if v == "{root}" else v)
                             for k, v in value.items()}
                self.write(os.path.join(root, ".herd", "state"), name, value,
                           raw)
                self.assert_refused(root, problem)

    def test_more_bindings_than_the_contract_bound(self):
        agents = {"executor%d" % i: "a%d" % i
                  for i in range(1, contract.MAX_TASK_BINDINGS + 2)}
        self.assert_refused(self.herd(agents=agents),
                            service.PROBLEM_EXCEEDS_BOUND)


class RootAndSelfCheckTests(Fixture):

    def test_roots_must_be_explicit_resolved_directories(self):
        root = self.herd()
        link = os.path.join(self.root, "link")
        os.symlink(root, link)
        afile = os.path.join(self.root, "a-file")
        open(afile, "w").close()
        for roots in ([], (), None, root, [root, root], ["relative/herd"],
                      [link], [afile], [root + "/"], [7], [""],
                      [os.path.join(self.root, "missing")],
                      [root] * (service.MAX_HERD_ROOTS + 1)):
            with self.subTest(roots=repr(roots)[:60]):
                captured = self.capture(roots)
                self.assertIsNone(captured.snapshot)
                self.assertEqual(captured.problem,
                                 service.PROBLEM_ROOTS_INVALID)

    def test_the_self_check_refuses_a_shape_the_contract_does_not_accept(self):
        def widened(*args):
            return [{"logical": "executor1", "role": "executor",
                     "agent": "pod-exec1", "session": None, "pane_id": None,
                     "workspace_id": None, "extra": "field"}]
        root = self.herd()
        payload = self.payload([root])
        with mock.patch.object(service, "_bindings", widened):
            with self.assertRaises(service._Refused) as caught:
                service._project(payload)
        self.assertEqual(caught.exception.problem, service.PROBLEM_SELF_CHECK)

    def test_a_bad_clock_and_a_defect_are_raised_not_converted(self):
        for value in (1.5, -1, True, None):
            with self.assertRaises(TypeError):
                service.capture([self.herd()], lambda: value, self.plan())
        payload = self.payload([self.herd()])
        with mock.patch.object(service, "_task",
                               side_effect=KeyError("defect")):
            with self.assertRaises(KeyError):
                service._project(payload)


@unittest.skipIf(not CLI_HAS_SNAPSHOT,
                 "target_runtime/cli.py is baseline in the read-only subset:"
                 " no dirun snapshot")
class CommandTests(Fixture):
    """``dirun snapshot``: stdout is exactly one JSON value; diagnostics go
    to stderr; nothing is written anywhere."""

    def run_command(self, argv, **plan):
        listing_argv = self.plan(**plan)
        before = self.tree()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), \
                mock.patch.object(service, "LISTING_ARGV", listing_argv):
            code = runtime_cli.main(argv)
        self.assertEqual(self.tree(), before)
        return code, out.getvalue(), err.getvalue()

    def test_a_snapshot_is_one_json_value_on_stdout(self):
        root = self.herd()
        code, out, err = self.run_command(["snapshot", "--herd-root", root])
        self.assertEqual(code, runtime_cli.EXIT_OK, err)
        self.assertEqual(out.count("\n"), 1)
        value = json.loads(out)
        self.assertEqual(value["tasks"][0]["workspace_realpath"], root)
        self.assertNotIn("diagnostic", out)
        self.assertEqual(contract.read_runtime_snapshot(
            value, service.production_clock()).health, contract.HEALTH_FRESH)

    def test_no_snapshot_is_null_on_stdout_and_unavailable(self):
        root = self.herd()
        code, out, err = self.run_command(
            ["snapshot", "--herd-root", root],
            stdout="warning: x\n" + listing_stdout())
        self.assertEqual(code, runtime_cli.EXIT_SNAPSHOT_UNAVAILABLE)
        self.assertEqual(out, "null\n")
        self.assertIn(service.PROBLEM_LISTING_MALFORMED, err)
        self.assertEqual(contract.read_runtime_snapshot(
            json.loads(out), NOW).health, contract.HEALTH_UNAVAILABLE)

    def test_refused_input_exits_distinctly_with_clean_stdout(self):
        code, out, err = self.run_command([
            "snapshot", "--herd-root",
            self.herd() + "/" + "a" * service.MAX_ROOT_PATH_CHARS])
        self.assertEqual(code, runtime_cli.EXIT_SNAPSHOT_INPUT_REFUSED)
        self.assertEqual(out, "null\n")
        self.assertIn(service.PROBLEM_INPUT_TOO_LARGE, err)

    def test_unsettled_owned_work_exits_distinctly_never_as_done(self):
        """The one-shot waits for its owned work to END; a member that
        stays in its group leaves cleanup unestablished: exit 6, never 0."""
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        pidfile = os.path.join(elsewhere.name, "zombie-pids.json")
        code, out, err = self.run_command(
            ["snapshot", "--herd-root", self.herd()], zombie_member=20,
            pidfile=pidfile)
        _leader, parent, member = self.pids(pidfile)
        self.addCleanup(self.escaped_parent, parent)
        self.assertEqual(code, runtime_cli.EXIT_SNAPSHOT_WORK_UNSETTLED)
        self.assertEqual(out, "null\n")
        self.assertIn(service.PROBLEM_CLEANUP_UNESTABLISHED, err)
        self.assertIn(service.WORK_UNCERTAIN, err)
        self.escaped_parent(parent)
        self.wait_reaped(member)

    def test_configuration_and_missing_roots_are_refused_with_clean_stdout(
            self):
        root = self.herd()
        for argv in (["--config", "/x", "snapshot", "--herd-root", root],
                     ["--runtime-config", "/x", "snapshot", "--herd-root",
                      root],
                     ["snapshot"], ["snapshot", "--output", "/x"]):
            with self.subTest(argv=argv):
                code, out, _err = self.run_command(argv)
                self.assertEqual(code, runtime_cli.EXIT_CONFIG)
                self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
