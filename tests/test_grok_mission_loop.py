"""Task 8, slice S-VII: production-composition acceptance of the Grok-to-PR
loop — the fifteen hermetic acceptance rows and Supervisor item E.

COMPOSITION (what is real, what is controlled):

- The Grok side is the REAL endpoint as ``grokmcp serve`` composes it:
  ``grok_mcp.cli.main`` reads a real mode-600 config file (repository,
  Mission store, workflow store = the Runtime's state directory,
  coordination store, bearer token, an ephemeral port) and wires the real
  ``MissionService``, ``MissionControl`` bootstrap (with the real
  engineering-runtime readiness producer), ``DeliveryDesk``, status reader,
  ``ControlDesk`` and ``AttentionDesk`` into the real ``GrokMcpController``
  and ``GrokMcpServer`` (bearer auth, sessions, SSE elicitation). The only
  seams: the operator session (a recorder; never called by these rows), the
  clock (``grok_mcp.cli._unix_seconds`` → the test clock) and the serve
  loop (run on a thread). A scripted Streamable-HTTP client stands in for
  the Grok client UI and answers every elicitation form.
- The Runtime side is the REAL ``dirun`` pass: ``RuntimeDurableExecution``
  over the real ``TargetBroker`` with the real Mission gate, the real
  bridge, the real workflow/capability stores and the real
  ``production_spawn`` through the real guarded control plane — with the
  controlled seams the brief names: the role turns (``FakeRoleTurn``), the
  engine's start/task/close/listing (``Engine`` at the ``HerdrControlPlane``
  seams), the Herdr observation and spawn records, and the Git transport
  (real git over hermetic repositories). The Broker's readiness producer
  and attention desk are wired exactly as ``target_runtime.cli`` wires
  them; the Runtime's single-instance lock is HELD by the test (a running
  Runtime), so readiness is the real probe's answer.
- The delivery side is the REAL P1-A6 ``DeliveryMachine`` + the Mission
  delivery driver over a hermetic bare remote (``tests/_hermetic_git``),
  with the recording ``gh`` half of the test transport: no network, no
  real remote. Because the Broker's verification compares the lease's
  origin URL with the canonical one, the hermetic ``insteadOf`` rewrite is
  set only after COMPLETE, and the delivery driver is wired then (the
  S-VI fixture's constraint; production wires it at start).

Every row asserts exact payload fields AND effect counts read from disk or
recorders.
"""

import contextlib
import errno
import fcntl
import functools
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 - installs the shared-base guard (module form)

from _hermetic_git import run_git
from coordination import attention as coordination_attention
from coordination import record as coordination_record
from coordination import store as coordination_store
from grok_mcp import cli as grok_cli
from grok_mcp import decision_tools
from grok_mcp import elicitation as elicitation_module
from grok_mcp import protocol
from grok_mcp import server as server_module
from mission import authorization as authorization_module
from mission import observation as observation_module
from mission import progress as progress_module
from mission import record as mission_record
from mission import service as mission_service_module
from mission import state as mission_state
from mission import state_service as mission_state_service_module
from mission import store as mission_store_module
from mission_control import attention as attention_module
from mission_control import controls as controls_module
from mission_control import delivery as delivery_module
from mission_control import delivery_artifacts as artifacts
from mission_control import engineering as engineering_module
from mission_control import gate as gate_module
from mission_control import readiness as readiness_module
from pr_delivery import cli as delivery_cli
from pr_delivery import machine as machine_module
from pr_delivery import store as delivery_store_module
from pr_delivery import transport as transport_module
from target_runtime import broker as broker_module
from target_runtime import cli as runtime_cli
from target_runtime import dispatch as dispatch_module
from target_runtime import evidence_preservation as preservation_module
from target_runtime import ownership as ownership_module
from target_runtime import runtime as runtime_module
from target_runtime import workspace_ownership as workspace_ownership_module
from target_runtime.durable_execution import RuntimeDurableExecution
from telegram_operator import state as runtime_state
from workflow_authority import record as wa_record
from workflow_authority import store as wa_store

import test_mission_controls
from test_grok_mcp import RecordingOperator
from test_mission_delivery import MissionTransport, VERIFY_ARGV
from test_mission_engagement import AGENTS, EngagementCase, WiredService, contract
from test_target_runtime import (
    CANONICAL_URL, TARGET_TASK_ID, FakeRoleTurnResult, real_shaped_observation)
from herdr.control_plane import ChildHistoryError, ChildRecordNotAppended, HerdrControlPlane

#: The REAL control-side child-record writer, captured at import — before any
#: fixture patches the control plane's engine seams (the cause-3 tests).
REAL_SPAWN_CHILD = HerdrControlPlane.__dict__["spawn_child"]
assert REAL_SPAWN_CHILD.__module__ == "herdr.control_plane", REAL_SPAWN_CHILD
#: The REAL runtime start (``HerdrControlPlane.start`` → ``herdr.lifecycle.
#: start_herd``), captured likewise (the startup-correction tests).
REAL_START = HerdrControlPlane.__dict__["start"]
assert REAL_START.__module__ == "herdr.control_plane", REAL_START

from herdr import lifecycle as lifecycle_module  # noqa: E402


class _Completed(object):
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _HerdrHost(object):
    """A CONTROLLED herdr CLI host behind ``herdr.lifecycle`` — the one doubled
    layer of the production startup composition. The REAL
    ``HerdrControlPlane.start`` runs the REAL ``start_herd``: its persisted
    runtime-state read, the live-supervisor refusal, its stale-state
    ``workspace close``, the unlink and the workspace creation; its ``herdr``
    CLI calls land here. The host's workspaces ARE the engine's live listing
    (what the Broker observes and closes), so a supervisor is live exactly
    while a listed workspace carries its name. EVERY ``workspace close`` the
    lifecycle issues is recorded in ``native_closes`` — the Broker's own
    closes are the engine's ``close_calls`` — so a second close of a retired
    id, or a close aimed at a reused one, is visible."""

    ROLES = ("supervisor", "lead", "executor", "reviewer")

    def __init__(self, engine, id_format="w-host-%d"):
        self.engine = engine
        self.id_format = id_format
        self.panes = {}
        self.created = []
        self.native_closes = []
        self.probes = []

    def run(self, cmd, cwd=None, check=False):
        if cmd[:3] == ["herdr", "agent", "get"]:
            live = any(cmd[3] in w["agent_names"] for w in self.engine.live)
            self.probes.append((cmd[3], live))
            return _Completed(0 if live else 1)
        if cmd[:3] == ["herdr", "workspace", "close"]:
            self.native_closes.append(cmd[3])
            present = any(w["workspace_id"] == cmd[3] for w in self.engine.live)
            self.engine.live = [w for w in self.engine.live if w["workspace_id"] != cmd[3]]
            return _Completed(0 if present else 1, stderr="" if present else "not found")
        return _Completed(0)

    def jrun(self, cmd):
        assert cmd[:3] == ["herdr", "workspace", "create"], cmd
        workspace_id = self.id_format % (len(self.created) + 1)
        self.created.append(workspace_id)
        root = "%s:1" % workspace_id
        self.panes[root] = workspace_id
        self.engine.live.append({"workspace_id": workspace_id, "agent_names": []})
        return {"result": {"workspace": {"workspace_id": workspace_id},
                           "root_pane": {"pane_id": root}}}

    def split(self, pane, direction):
        workspace_id = self.panes[pane]
        new = "%s:%d" % (workspace_id,
                         1 + sum(1 for owner in self.panes.values() if owner == workspace_id))
        self.panes[new] = workspace_id
        return new

    def start_agent(self, name, pane, role_cfg, timeout, shell_wait):
        for workspace in self.engine.live:
            if workspace["workspace_id"] == self.panes[pane]:
                workspace["agent_names"] = sorted(set(workspace["agent_names"]) | {name})

    def prompt(self, agent, text, timeout, wait=True):
        return _Completed(0)

    @staticmethod
    def bootstrap_text(*args, **kwargs):
        return "bootstrap"

    @staticmethod
    def establish_role_bindings(*args, **kwargs):
        return None

    def initialize(self, repo):
        """What production ``spawn`` does first for a target that is not yet an
        initialized Herdr (``initialize_herd``), reduced to the configuration
        ``start_herd`` reads."""
        from herdr.instance import HerdrInstance
        herd = HerdrInstance(repo)
        if not herd.initialized:
            herd.save_config({
                "project": {"test_command": None},
                "roles": dict((role, {}) for role in self.ROLES),
                "orchestration": {"leads": 1, "pods": 1, "heartbeat_autostart": False,
                                  "agent_start_timeout_ms": 1, "shell_ready_timeout_ms": 1,
                                  "agent_task_timeout_ms": 1},
            })

    def names(self, workspace_id):
        return [w["agent_names"] for w in self.engine.live
                if w["workspace_id"] == workspace_id]

TOKEN = "loop-bearer-token"
SSE_ACCEPT = "application/json, text/event-stream"
FORM_CAPABILITIES = {"elicitation": {"form": {}}}
READINESS = [{"resource_key": readiness_module.ENGINEERING_RUNTIME_RESOURCE,
              "max_age_seconds": 900}]


def loop_contract(**overrides):
    """The S-VI obligation contract, requiring the ENGINEERING RUNTIME's
    readiness (the production producer's resource) instead of a fixture
    resource."""
    overrides.setdefault("required_resource_readiness", READINESS)
    return contract(**overrides)


def proposal_arguments(baseline, **overrides):
    arguments = {
        "objective": "Resolve the defect in the target",
        "target_context": "the target repository only",
        "repository_url": CANONICAL_URL,
        "requested_scope": "the readiness probe and its tests",
        "requested_action_scope": [mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
                                   mission_record.ACTION_SCOPE_REPOSITORY_READ],
        "requested_delivery_target": mission_record.DELIVERY_TARGET_GITHUB_PR,
        "baseline": {"ref": "refs/heads/main", "commit_sha": baseline},
        "proof_contract": loop_contract(),
        "verification": {"argv": list(VERIFY_ARGV)},
    }
    arguments.update(overrides)
    return arguments


def accept(request):
    """The honest human: confirm the value the card asks for."""
    schema = request["params"]["requestedSchema"]
    return "accept", {"confirm": schema["properties"]["confirm"]["enum"][0]}


def decline(request):
    return "decline", None


def wrong_confirm(request):
    return "accept", {"confirm": "0" * 12}


class Crash(BaseException):
    """A process death at a named point (the process seam): it escapes
    every ``except Exception`` exactly as the process's end would, and
    what the process held only in memory is lost with it (see
    ``LoopCase.restart_dirun``)."""


class LoopClient(object):
    """The scripted Grok client: one MCP session over raw HTTP; JSON tool
    calls and event-stream tool calls whose elicitation it answers."""

    def __init__(self, port, token=TOKEN):
        self.port = port
        self.token = token
        self.session_id = None
        self.next_id = 1

    def _headers(self, accept_header=SSE_ACCEPT):
        headers = {"Content-Type": "application/json", "Accept": accept_header}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload, accept_header=SSE_ACCEPT):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                               self._headers(accept_header))
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def initialize(self, capabilities=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            payload = {"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                       "params": {"protocolVersion": "2025-11-25",
                                  "capabilities": (FORM_CAPABILITIES
                                                   if capabilities is None
                                                   else capabilities),
                                  "clientInfo": {"name": "loop", "version": "0"}}}
            headers = self._headers()
            headers.pop("Mcp-Session-Id", None)
            connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                               headers)
            response = connection.getresponse()
            response.read()
            self.session_id = response.getheader("Mcp-Session-Id")
            return response.status
        finally:
            connection.close()

    def _id(self):
        value = self.next_id
        self.next_id += 1
        return value

    def call(self, name, arguments=None):
        """A plain tool call: ``(structuredContent, isError)``."""
        status, body = self.post({"jsonrpc": "2.0", "id": self._id(),
                                  "method": "tools/call",
                                  "params": {"name": name,
                                             "arguments": arguments or {}}})
        assert status == 200, (status, body)
        message = json.loads(body.decode("utf-8"))
        result = message["result"]
        return result["structuredContent"], bool(result.get("isError"))

    def open_elicited(self, name, arguments, accept_header=SSE_ACCEPT):
        payload = {"jsonrpc": "2.0", "id": self._id(), "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                           self._headers(accept_header))
        return connection, connection.getresponse()

    @staticmethod
    def read_event(response):
        data = []
        while True:
            line = response.fp.readline()
            if not line:
                return None
            line = line.decode("utf-8").rstrip("\n")
            if line == "":
                if data:
                    return json.loads("\n".join(data))
                continue
            if line.startswith("data: "):
                data.append(line[len("data: "):])

    def respond(self, request_id, action, content=None):
        result = {"action": action}
        if content is not None:
            result["content"] = content
        return self.post({"jsonrpc": "2.0", "id": request_id, "result": result})

    def elicited(self, name, arguments, answer=accept):
        """An event-stream tool call answered by ``answer(request)`` →
        ``(action, content)`` or None (no answer). Returns ``(request,
        structured, is_error)``; ``request`` is None when the server
        answered with plain JSON (a refusal before any elicitation)."""
        connection, response = self.open_elicited(name, arguments)
        try:
            if response.getheader("Content-Type") != "text/event-stream":
                message = json.loads(response.read().decode("utf-8"))
                result = message["result"]
                return None, result["structuredContent"], bool(result.get("isError"))
            request = self.read_event(response)
            if request is None or request.get("method") != "elicitation/create":
                result = request["result"]
                return None, result["structuredContent"], bool(result.get("isError"))
            reply = answer(request)
            if reply is not None:
                action, content = reply
                status, _ = self.respond(request["id"], action, content)
                assert status == 202, status
            final = self.read_event(response)
            result = final["result"]
            return request, result["structuredContent"], bool(result.get("isError"))
        finally:
            try:
                response.close()
            finally:
                connection.close()


class LoopCase(EngagementCase):
    """The production composition of both processes over shared stores."""

    _S5 = test_mission_controls.RIntegrationTests
    lease_path = _S5.lease_path
    clean_herd_state = _S5.clean_herd_state
    git = _S5.git
    write = _S5.write
    stage = _S5.stage

    def setUp(self):
        super(LoopCase, self).setUp()
        self.logs = []
        self.observe_calls = []
        # The hermetic remote holding the baseline on main.
        self.bare = os.path.join(self.base, "remote.git")
        run_git("init", "-q", "--bare", "-b", "main", self.bare)
        run_git("-C", self.target_fixture, "push", "-q", self.bare,
                "%s:refs/heads/main" % self.baseline)
        self.coordination_dir = os.path.join(self.base, "coordination")
        os.makedirs(self.coordination_dir, mode=0o700)
        # The OBSERVED lease task follows the hand-over, as in production:
        # ``herdr.tasks.dispatch_task`` writes the task it mints into the
        # lease's task state, and the reviewer names its round files after
        # that task (Task 8 startup correction — a follow-up's retirement binds
        # the observed task to the prior canonical hand-over). Every engine
        # task hand-over is recorded here; before the first, the observation
        # names ``TARGET_TASK_ID`` as it always did.
        self.handed_over = []
        engine_dispatch = self.engine.dispatch_task

        def dispatch_task(plane, repo, text, **kwargs):
            task = engine_dispatch(plane, repo, text, **kwargs)
            self.note_hand_over(task["id"])
            return task
        self.engine.dispatch_task = dispatch_task
        self.observer = self.lease_observer
        self.broker._observe = self.lease_observer
        # The Runtime is RUNNING: its single-instance lock is held.
        self.hold_runtime_lock()
        # The dirun composition (target_runtime.cli._build_broker's wiring).
        self.wire_runtime(self.broker)
        self.driver = None
        self.delivery_transport = None
        self.serve_grok()

    # -- the observed lease task (follows the hand-over) ---------------------------

    def observed_task_id(self):
        return self.handed_over[-1] if self.handed_over else TARGET_TASK_ID

    def note_hand_over(self, task_id):
        """A task was handed over: the lease's observed task is now that task,
        and a freshly handed-over task is ACTIVE (the previous one stays
        whatever it was until this moment)."""
        self.handed_over.append(task_id)
        self.target_task_status = "ACTIVE"

    def observation(self, rounds=None):
        overrides = dict(task_id=self.observed_task_id())
        overrides.update(self.observation_overrides)
        raw = real_shaped_observation(status=self.target_task_status, **overrides)
        if rounds is not None:
            raw["reviews"]["rounds"] = len(rounds)
            raw["reviews"]["total_files"] = len(rounds)
            raw["reviews"]["listed"] = [
                {"file": "%s-round-%02d.md" % (overrides["task_id"], n), "round": n,
                 "decision": decision, "size": 120, "mtime": 1_000_040 + n}
                for n, decision in rounds]
        return raw

    def lease_observer(self, repo_path):
        self.observe_calls.append(repo_path)
        return self.observation()

    def write_round(self, workflow_id, round_number, decision):
        """The reviewer's canonical round file, named after the OBSERVED task."""
        directory = os.path.join(self.lease_path(workflow_id), ".herd", "state", "reviews")
        os.makedirs(directory, exist_ok=True)
        name = "%s-round-%02d.md" % (self.observed_task_id(), round_number)
        text = ("# Reviewer round %d\n\nReviewer: `reviewer1` / `sess-target`\n\n"
                "Protocol token: `%s`\n\n## Transcript\n\nHERD_DECISION: %s\n"
                % (round_number, decision, decision))
        with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        return name, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def listing_with_rounds(self, rounds):
        """An observer listing EVERY round the target produced."""
        def observer(repo_path):
            self.observe_calls.append(repo_path)
            return self.observation(rounds)
        self.observer = observer
        self.broker._observe = observer

    # -- the two processes ------------------------------------------------------

    def hold_runtime_lock(self):
        """The Runtime is RUNNING: its single-instance lock is taken by the
        Runtime's OWN ``acquire_runtime_lock`` (the Runtime-owned file
        name), so the readiness producer's probe answers from the real
        lock — a drift of the producer's duplicated name fails here."""
        descriptor = runtime_cli.acquire_runtime_lock(self.store_dir)
        self.assertIsNotNone(descriptor, "the Runtime lock is already held")
        self.runtime_lock = descriptor
        self.addCleanup(self.release_runtime_lock)

    def release_runtime_lock(self):
        if self.runtime_lock is not None:
            fcntl.flock(self.runtime_lock, fcntl.LOCK_UN)
            os.close(self.runtime_lock)
            self.runtime_lock = None

    def wire_runtime(self, broker):
        broker.mission_readiness = readiness_module.RuntimeReadinessProducer(
            self.gate.service, self.store_dir)
        broker.mission_attention = attention_module.AttentionDesk(
            self.gate.service, self.coordination_dir)
        return broker

    def runtime_pass(self, broker=None):
        broker = broker or self.broker
        return RuntimeDurableExecution(broker, self.store_dir).process_once()

    def grok_config(self):
        directory = os.path.join(self.base, "grokmcp")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"repository": self.control, "port": 0,
                       "bearer_token": TOKEN,
                       "mission_store_dir": self.mission_dir,
                       "workflow_store_dir": self.store_dir,
                       "coordination_store_dir": self.coordination_dir}, handle)
        os.chmod(path, 0o600)
        return path

    def serve_grok(self):
        """``grokmcp serve`` through the real ``cli.main``, on a thread."""
        self.operator = RecordingOperator()
        served = threading.Event()
        holder = {}

        def serve_forever(server):
            holder["server"] = server
            served.set()
            server.serve_forever()

        clock = mock.patch.object(grok_cli, "_unix_seconds", lambda: self.clock())
        clock.start()
        self.addCleanup(clock.stop)
        # The elicitation table's clock (``server.py`` reads ``time`` only
        # for it, at construction) → the same controlled clock.
        table_clock = mock.patch.object(
            server_module, "time", types.SimpleNamespace(time=lambda: self.clock()))
        table_clock.start()
        self.addCleanup(table_clock.stop)
        path = self.grok_config()
        thread = threading.Thread(target=grok_cli.main, kwargs={
            "argv": ["--config", path, "serve"],
            "session_factory": self.operator.session,
            "serve_forever": serve_forever,
            "environ": {},
            "error_writer": self.logs.append,
        }, daemon=True)
        thread.start()
        self.assertTrue(served.wait(10), self.logs)
        self.grok_server = holder["server"]
        self.grok_thread = thread
        self.addCleanup(self.stop_grok)
        self.client = self.new_client()

    def stop_grok(self):
        if self.grok_server is not None:
            self.grok_server.shutdown()
            self.grok_thread.join(10)
            self.grok_server = None

    def restart_grok(self):
        """A ``grokmcp`` restart: a fresh process composition over the SAME
        stores (new controller, service, desks and server)."""
        self.stop_grok()
        self.serve_grok()

    def restart_dirun(self):
        """A ``dirun`` restart: a fresh process composition over the SAME
        stores — a new Mission service, gate, Broker, readiness producer,
        attention desk and delivery machine, and EMPTY process memory (the
        Broker's retained hand-overs). The recording transport stands for
        the external world (the bare remote and GitHub) and persists; its
        counters are therefore totals across the restart."""
        memory = mock.patch.object(broker_module, "RETAINED_HANDOVERS",
                                   broker_module._RetainedHandovers())
        memory.start()
        self.addCleanup(memory.stop)
        service = WiredService(mission_store_module.MissionStore(self.mission_dir),
                               self.clock)
        self.gate = gate_module.MissionEffectGate(
            service, gate_module.local_process_context("dirun-test"))
        self.broker = self.wire_runtime(self.gated_broker(gate=self.gate))
        if self.delivery_transport is not None:
            self.wire_delivery(None, transport=self.delivery_transport)
        return self.broker

    def new_client(self, capabilities=None):
        client = LoopClient(self.grok_server.server_address[1])
        self.assertEqual(client.initialize(capabilities), 200)
        return client

    # -- Grok conversation ------------------------------------------------------

    def grok_propose(self, client=None, **overrides):
        structured, is_error = (client or self.client).call(
            protocol.TOOL_MISSION_PROPOSE, proposal_arguments(self.baseline, **overrides))
        self.assertFalse(is_error, structured)
        return structured["mission_id"], structured["revision"]

    def grok_decide(self, mission_id, revision, answer=accept, client=None):
        return (client or self.client).elicited(
            protocol.TOOL_MISSION_DECIDE, {"mission_id": mission_id,
                                           "revision": revision}, answer)

    def grok_dispatch(self, mission_id, client=None):
        return (client or self.client).call(protocol.TOOL_MISSION_DISPATCH,
                                            {"mission_id": mission_id})

    def grok_status(self, mission_id, client=None):
        return (client or self.client).call(protocol.TOOL_MISSION_STATUS,
                                            {"mission_id": mission_id})

    def grok_control(self, mission_id, revision, control, answer=accept, client=None):
        return (client or self.client).elicited(
            protocol.TOOL_MISSION_CONTROL, {"mission_id": mission_id,
                                            "revision": revision,
                                            "control": control}, answer)

    def grok_delivery_decide(self, mission_id, revision, answer=accept, client=None):
        return (client or self.client).elicited(
            protocol.TOOL_DELIVERY_DECIDE, {"mission_id": mission_id,
                                            "revision": revision}, answer)

    def authorized(self, **overrides):
        """propose → di_mission_decide (the human's accept) over the wire."""
        mission_id, revision = self.grok_propose(**overrides)
        request, structured, is_error = self.grok_decide(mission_id, revision)
        self.assertIsNotNone(request)
        self.assertFalse(is_error, structured)
        self.assertEqual(structured["decision"], "APPROVE")
        return mission_id, revision

    def dispatched(self, **overrides):
        """authorized → di_mission_dispatch (the real bootstrap, readiness
        probed) → one Runtime pass (dispatched, the target running)."""
        mission_id, revision = self.authorized(**overrides)
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        workflow_id = structured["workflow_id"]
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        return mission_id, revision, workflow_id

    def engineering_finishes(self, workflow_id, decision="APPROVE"):
        """What the target engine produces: its canonical review round, and
        the task observed COMPLETE."""
        self.write_round(workflow_id, 1, decision)
        self.listing_with_rounds([(1, decision)])
        self.target_task_status = "COMPLETE"

    def completed(self, **overrides):
        """dispatched → the engine finishes → one pass verifies and
        completes → the candidate (``fix.txt``) and the hermetic remote
        rewrite in the lease → the delivery driver wired."""
        mission_id, revision, workflow_id = self.dispatched(**overrides)
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.clean_herd_state(workflow_id)
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        self.stage(workflow_id, "fix.txt", "fixed\n")
        self.wire_delivery(workflow_id)
        return mission_id, revision, workflow_id

    def wire_delivery(self, workflow_id, broker=None, transport=None):
        """The Mission delivery driver as ``production_delivery`` composes it
        (delivery store = the state directory), over the recording
        transport."""
        broker = broker or self.broker
        transport = transport or MissionTransport(self.lease_path(workflow_id))
        machine = machine_module.DeliveryMachine(
            delivery_store_module.DeliveryStore(self.store_dir), transport,
            lambda: self.clock())
        self.driver = delivery_module.MissionDelivery(
            self.gate, self.store_dir, self.store_dir, machine,
            lambda repo, remote, base: delivery_cli.live_bindings(
                transport, repo, remote, base),
            delivery_cli.authorize_client_confirmed, lambda: self.clock())
        broker.mission_delivery = self.driver
        broker.delivery_store_directory = self.store_dir
        self.delivery_transport = transport
        return self.driver

    # -- readers ------------------------------------------------------------------

    def mission_document(self):
        return self.mstore.load()

    def reservations(self, kind):
        return [r for r in self.mission_document()["reservations"].values()
                if r["kind"] == kind]

    def deliveries(self):
        read = delivery_store_module.DeliveryStore(self.store_dir).read()
        return {} if read.document is None else read.document["deliveries"]

    def remote_refs(self):
        text = run_git("--git-dir", self.bare, "for-each-ref",
                       "--format=%(refname) %(objectname)")
        return dict(line.split(" ", 1) for line in text.splitlines() if line)

    def attention_records(self):
        read = coordination_store.CoordinationStore(self.coordination_dir).read()
        if read.document is None:
            return {}
        return read.document["attention"]

    def store_bytes(self):
        """Every byte of every store a status read could touch (locks and
        the Runtime lock excluded)."""
        files = {}
        for root in (self.mission_dir, self.store_dir, self.coordination_dir):
            for directory, _dirs, names in os.walk(root):
                if "workspaces" in directory.split(os.sep):
                    continue
                for name in names:
                    if name.endswith(".lock"):
                        continue
                    path = os.path.join(directory, name)
                    with open(path, "rb") as handle:
                        files[path] = handle.read()
        return files


def outcome_view(outcomes):
    """``{workflow_id: [(label, ok, problem, outcome)]}`` of one pass."""
    return dict((workflow_id, [(label, o.ok, o.problem, o.outcome)
                               for label, o in listed])
                for workflow_id, listed in outcomes.items())


# ======================================================================
# Row 1 — proposal → edit/approve/deny with durable revision protection
# ======================================================================


class R1ProposalDecisionTests(LoopCase):

    def decisions(self, mission_id):
        return self.mission_document()["missions"][mission_id]["decisions"]

    def test_R1_propose_with_contract_inputs_decide_edit_stale_and_deny(self):
        mission_id, revision = self.grok_propose()
        self.assertEqual(revision, 1)
        got, is_error = self.client.call(protocol.TOOL_MISSION_GET,
                                         {"mission_id": mission_id})
        self.assertFalse(is_error, got)
        # The proof contract and the verification argv are proposal INPUTS
        # and are stored in the revision the human decides exactly as the
        # core normalizes them (requirements in key order).
        self.assertEqual(got["proposal"]["proof_contract"],
                         mission_record.validate_proof_contract(
                             json.loads(json.dumps(loop_contract()))))
        self.assertEqual(got["proposal"]["verification"], {"argv": list(VERIFY_ARGV)})
        self.assertEqual(got["state"], mission_record.STATE_AWAITING_DECISION)
        # The human accepts revision 1 through the elicitation form.
        request, decided, is_error = self.grok_decide(mission_id, 1)
        self.assertFalse(is_error, decided)
        self.assertEqual((decided["status"], decided["decision"], decided["revision"]),
                         ("applied", "APPROVE", 1))
        self.assertTrue(decided["authorization_live"])
        first_authorization = decided["authorization_id"]
        self.assertEqual(request["params"]["requestedSchema"]["properties"]["confirm"]
                         ["enum"], [got["proposal_digest_sha256"][:12]])
        # A material EDIT: revision 2, the revision-1 authorization invalidated.
        edited, is_error = self.client.call(
            protocol.TOOL_MISSION_EDIT,
            dict(proposal_arguments(self.baseline, objective="Resolve it, revised"),
                 mission_id=mission_id, expected_revision=1))
        self.assertFalse(is_error, edited)
        self.assertEqual((edited["revision"], edited["current_state"]),
                         (2, mission_record.STATE_AWAITING_DECISION))
        self.assertEqual(edited["invalidated_authorization_ids"], [first_authorization])
        # A decision on the stale revision refuses BEFORE anything is
        # reserved or asked.
        reserved = len(self.reservations("decision"))
        request, stale, is_error = self.grok_decide(mission_id, 1)
        self.assertIsNone(request)
        self.assertTrue(is_error)
        self.assertEqual((stale["status"], stale["problem"]),
                         ("refused", "mission_stale_revision"))
        self.assertEqual(len(self.reservations("decision")), reserved)
        # The human declines revision 2: a DENY, no authorization.
        request, denied, is_error = self.grok_decide(mission_id, 2, answer=decline)
        self.assertFalse(is_error, denied)
        self.assertEqual((denied["decision"], denied["authorization_id"],
                          denied["current_state"]),
                         ("DENY", None, mission_record.STATE_DENIED))
        kinds = [d["decision"] for d in self.decisions(mission_id)]
        self.assertEqual(kinds, ["APPROVE", "EDIT", "DENY"])
        authorizations = self.mission_document()["authorizations"]
        self.assertEqual(list(authorizations), [first_authorization])
        self.assertTrue(authorizations[first_authorization]["revocation"]["revoked"])
        # Every reserved decision id is consumed exactly once (accept, edit,
        # decline) and none is left unaccounted.
        self.assertEqual(sorted(r["consumed_by"] is not None
                                for r in self.reservations("decision")), [True] * 3)


# ======================================================================
# Row 2 — no dispatch before valid current human authority
# ======================================================================


class R2NoDispatchWithoutAuthorityTests(LoopCase):

    def assert_nothing_dispatched(self, refused, problem):
        self.assertTrue(refused[1], refused[0])
        self.assertEqual(refused[0]["problem"], problem, refused[0])
        self.assertEqual(self.rows(), {})                    # add_workflow = 0
        self.assertEqual(self.runtime_pass(), {})           # nothing claimable
        self.assertEqual((len(self.engine.starts), len(self.spawn_requests)), (0, 0))
        self.assertEqual(self.capability_entries(), {})      # nothing minted/consumed

    def test_R2_awaiting_decision_refuses(self):
        mission_id, _ = self.grok_propose()
        self.assert_nothing_dispatched(self.grok_dispatch(mission_id),
                                       engineering_module.PROBLEM_NOT_AUTHORIZED)

    def test_R2_declined_refuses(self):
        mission_id, revision = self.grok_propose()
        self.grok_decide(mission_id, revision, answer=decline)
        self.assert_nothing_dispatched(self.grok_dispatch(mission_id),
                                       engineering_module.PROBLEM_NOT_AUTHORIZED)

    def test_R2_edited_revision_refuses(self):
        mission_id, _ = self.authorized()
        self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed"),
            mission_id=mission_id, expected_revision=1))
        self.assert_nothing_dispatched(self.grok_dispatch(mission_id),
                                       engineering_module.PROBLEM_NOT_AUTHORIZED)

    def test_R2_connector_credential_approval_refuses(self):
        # di_mission_approve is the connector credential's own act, not the
        # human's client-confirmed decision: it authorizes the Mission but
        # never a consequential dispatch.
        mission_id, revision = self.grok_propose()
        approved, is_error = self.client.call(protocol.TOOL_MISSION_APPROVE,
                                              {"mission_id": mission_id,
                                               "revision": revision})
        self.assertFalse(is_error, approved)
        self.assertTrue(approved["authorization_live"])
        self.assert_nothing_dispatched(self.grok_dispatch(mission_id),
                                       "mission_control_provenance_insufficient")

    def test_R2_expired_authorization_refuses(self):
        mission_id, _ = self.authorized()
        self.clock.advance(86400 + 1)
        refused = self.grok_dispatch(mission_id)
        self.assertEqual(refused[0]["problem"], "mission_control_no_live_authorization",
                         refused[0])
        self.assert_nothing_dispatched(refused, "mission_control_no_live_authorization")

    def test_R2_runtime_not_running_refuses_on_probed_readiness(self):
        mission_id, _ = self.authorized()
        self.release_runtime_lock()
        refused = self.grok_dispatch(mission_id)
        self.assert_nothing_dispatched(refused, "mission_control_readiness_stale")
        # The producer recorded exactly what it probed: ONE NOT_READY.
        state = self.service.get_state(mission_id)
        self.assertEqual(len(state["record"]["resource_readiness"]), 1,
                         "the readiness producer recorded no observation")
        latest = readiness_module.latest_observation(state["record"])
        self.assertEqual(latest["status"], mission_state.READINESS_NOT_READY)


# ======================================================================
# Row 3 — exactly one engineering task for repeated accepted requests
# ======================================================================


class R3ExactlyOnceTests(LoopCase):

    def test_R3_five_requests_reconnect_restart_and_passes_one_workflow_one_spawn(self):
        mission_id, _ = self.authorized()
        results = [self.grok_dispatch(mission_id), self.grok_dispatch(mission_id)]
        results.append(self.grok_dispatch(mission_id, client=self.new_client()))
        self.restart_grok()                                  # grokmcp restart
        results.append(self.grok_dispatch(mission_id))
        results.append(self.grok_dispatch(mission_id, client=self.new_client()))
        workflow_ids = set(r[0]["workflow_id"] for r in results)
        self.assertEqual(len(workflow_ids), 1)
        self.assertEqual([r[0]["idempotent"] for r in results],
                         [False, True, True, True, True])
        workflow_id = workflow_ids.pop()
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        # A dirun restart: a fresh Broker composition over the same stores.
        fresh = self.wire_runtime(self.gated_broker())
        self.runtime_pass(fresh)
        self.runtime_pass(fresh)
        self.assertEqual(list(self.rows()), [workflow_id])
        self.assertEqual(len(self.receipts(workflow_id,
                                           "dispatched handoff revision")), 1)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"],
                         "task-started-1")
        self.assertEqual(len(self.engagements(mission_id)), 1)


# ======================================================================
# Row 4 — status while busy, without interruption or steering
# ======================================================================


class R4StatusWhileBusyTests(LoopCase):

    def test_R4_status_during_a_blocked_turn_holding_the_workflow_lock(self):
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real = self.broker._role_turn

        def blocking(role, entry, now, **kwargs):
            if role == "verification":
                entered.set()
                self.assertTrue(release.wait(60))
            return real(role, entry, now, **kwargs)
        self.broker._role_turn = blocking
        worker = threading.Thread(target=self.runtime_pass, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(30))
        # The pass holds the workflow store's exclusive lock during the turn.
        descriptor = os.open(os.path.join(self.store_dir, "workflows.lock"), os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        before = self.store_bytes()
        engine = (list(self.engine.starts), list(self.engine.tasks),
                  list(self.engine.close_calls))
        turns = len(self.role_turn.calls)
        started = time.monotonic()
        status, status_error = self.grok_status(mission_id)
        delivery, delivery_error = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                                    {"mission_id": mission_id})
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertFalse(status_error, status)
        self.assertFalse(delivery_error, delivery)
        self.assertEqual(status["canonical"]["mission_id"], mission_id)
        self.assertEqual(status["workflows"]["rows"][0]["phase"],
                         wa_record.PHASE_DISPATCHED)
        # Nothing changed, nothing was asked of the operator, the engine or
        # a model; the blocked turn is still the only one in flight.
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual((self.operator.built, self.operator.submitted), ([], []))
        self.assertEqual((list(self.engine.starts), list(self.engine.tasks),
                          list(self.engine.close_calls)), engine)
        self.assertEqual(len(self.role_turn.calls), turns)
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(60)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)


# ======================================================================
# Row 5 — blocker, review, proof, artifact, result, freshness, unavailable
# ======================================================================


class R5PropagationTests(LoopCase):

    def status(self, mission_id):
        status, is_error = self.grok_status(mission_id)
        self.assertFalse(is_error, status)
        return status

    @staticmethod
    def standings(status):
        return dict((kind, (source["standing"], source["freshness"]))
                    for kind, source in status["canonical"]["sources"].items())

    @staticmethod
    def values(status):
        sources = status["canonical"]["sources"]
        return (sources["task"]["value"], sources["review"]["value"],
                sources["delivery"]["value"]["status"])

    @staticmethod
    def finding_kinds(status):
        return [(f["kind"], f["subject"]) for f in status["reconciliation"]["findings"]]

    def test_R5_runtime_reports_propagate_to_status_and_attention(self):
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        # Before any Runtime report every source is UNAVAILABLE — never
        # folded into unknown — and no reconciliation exists.
        before = self.status(mission_id)
        self.assertEqual(self.standings(before),
                         dict((k, ("unavailable", None))
                              for k in ("task", "review", "candidate", "delivery")))
        self.assertIsNone(before["reconciliation"])
        self.assertEqual(sorted(c for c in before["canonical"]["holds"]["codes"]
                                if c.startswith("source_standing:")),
                         ["source_standing:%s:unavailable" % k
                          for k in ("candidate", "delivery", "review", "task")])
        # The dispatch pass reports; the reconciliation is current.
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        reported = self.status(mission_id)
        self.assertEqual(self.standings(reported),
                         dict((k, ("reported", "fresh"))
                              for k in ("task", "review", "candidate", "delivery")))
        self.assertEqual(self.values(reported), ("ACTIVE", "PENDING", "ABSENT"))
        self.assertTrue(reported["reconciliation"]["current"])
        self.assertEqual(self.finding_kinds(reported), [
            ("delivery_absent", None), ("proof_missing", "candidate_identity"),
            ("proof_missing", "delivery_decision"), ("proof_missing", "delivery_recorded"),
            ("proof_missing", "engineering_verified"),
            ("proof_missing", "reviewer_approve")])
        for code in ("report:review:PENDING", "report:delivery:ABSENT",
                     "proof_requirement:MISSING"):
            self.assertIn(code, reported["canonical"]["holds"]["codes"])
        # Freshness: past the bound with no newer report, every source is
        # STALE at the evaluation time.
        self.clock.advance(observation_module.REPORTED_FRESHNESS_BOUND_SECONDS + 5)
        self.assertEqual(self.standings(self.status(mission_id)),
                         dict((k, ("reported", "stale"))
                              for k in ("task", "review", "candidate", "delivery")))
        # The engine's reviewer REJECTS: verification is blocked, the
        # workflow BLOCKED, and both propagate with the round's digest.
        _name, round_digest = self.write_round(workflow_id, 1, "REJECT")
        self.listing_with_rounds([(1, "REJECT")])
        self.target_task_status = "COMPLETE"
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0],
                         ("verify", True, "broker_verification_review_not_approve",
                          "verification_blocked"))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        blocked = self.status(mission_id)
        self.assertEqual(self.values(blocked), ("BLOCKED", "REJECT", "ABSENT"))
        for code in ("report:review:REJECT", "report:task:BLOCKED"):
            self.assertIn(code, blocked["canonical"]["holds"]["codes"])
        self.assertIn(("review_changed", None), self.finding_kinds(blocked))
        self.assertIn(("task_changed", None), self.finding_kinds(blocked))
        digests = blocked["canonical"]["sources"]["candidate"]["value"]["artifact_digests"]
        self.assertEqual(sorted(digests), ["handoff", "observed_head", "review_round_1"])
        self.assertEqual(digests["review_round_1"], round_digest)
        self.assertEqual(blocked["workflows"]["rows"][0]["phase"], wa_record.PHASE_BLOCKED)
        # Stated limit: a blocked workflow opens no Mission blocker record,
        # so no BLOCKED attention exists (the status carries it).
        self.assertEqual(self.attention_records(), {})
        # A material EDIT: revision 2 awaits the human; the Runtime's pass
        # projects NEEDS_HUMAN at the client destination and the pull
        # surfaces it into this call's result.
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="Resolve it, revised"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.runtime_pass()
        records = list(self.attention_records().values())
        self.assertEqual([(r["condition_kind"], r["revision"], r["destination"],
                           r["presentation"]) for r in records],
                         [("NEEDS_HUMAN", 2, attention_module.CLIENT_DESTINATION,
                           "PENDING")])
        pulled, is_error = self.client.call(protocol.TOOL_ATTENTION_PULL, {})
        self.assertFalse(is_error, pulled)
        self.assertEqual([(a["condition_kind"], a["presentation"], a["surfaced_in"])
                          for a in pulled["surfaced_now"]],
                         [("NEEDS_HUMAN", "SURFACED", pulled["call_ref"])])
        self.assertEqual(self.status(mission_id)["attention"]["records"][0]
                         ["presentation"], "SURFACED")


# ======================================================================
# Row 6 — pause / resume / cancel and material change, runtime semantics
# ======================================================================


class R6ControlTests(LoopCase):

    def starts(self, mission_id):
        return mission_state.engagement_starts_of(
            self.service.get_state(mission_id)["record"] or {})

    def test_R6_hold_stops_the_next_effect_and_resume_revalidates(self):
        mission_id, revision, workflow_id = self.dispatched()
        request, held, is_error = self.grok_control(mission_id, revision, "hold")
        self.assertFalse(is_error, held)
        self.assertEqual((held["status"], held["operation"], held["control_recorded"]),
                         ("applied", mission_state.OPERATION_REQUEST_HOLD, True))
        self.assertTrue(held["controls"]["hold_active"])
        self.assertIn("NOT paused", request["params"]["message"])
        # The engine finishes, yet the gated pass performs NOTHING: the hold
        # refuses at the very first boundary (the recovery turn's admission)
        # — no model turn, no verification, no completion.
        self.engineering_finishes(workflow_id)
        turns = [call[0] for call in self.role_turn.calls]
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("request:recovery", False, gate_module.PROBLEM_HOLD_ACTIVE))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual([call[0] for call in self.role_turn.calls], turns)
        # Resume: lifted; the next pass re-validates and advances.
        request, resumed, is_error = self.grok_control(mission_id, revision, "resume")
        self.assertFalse(is_error, resumed)
        self.assertEqual(resumed["operation"], mission_state.OPERATION_LIFT_HOLD)
        self.assertFalse(resumed["controls"]["hold_active"])
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R6_a_hold_before_the_claim_means_zero_invocations(self):
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.grok_control(mission_id, revision, "hold")
        self.runtime_pass()
        self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (0, 0, 0))
        self.assertEqual(self.starts(mission_id), [])
        self.grok_control(mission_id, revision, "resume")
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R6_cancel_is_confirmed_only_after_the_observed_absence(self):
        mission_id, revision, workflow_id = self.dispatched()
        self.assertEqual(len(self.engine.live), 1)
        request, requested, is_error = self.grok_control(mission_id, revision, "cancel")
        self.assertFalse(is_error, requested)
        self.assertEqual(requested["operation"], mission_state.OPERATION_REQUEST_CANCEL)
        self.assertTrue(requested["controls"]["cancel_requested"])
        # Asked again before the Runtime observed the absence: refused before
        # anything is reserved or asked.
        cancel_ids = len(self.reservations("cancel_operation"))
        request, early, is_error = self.grok_control(mission_id, revision, "cancel")
        self.assertIsNone(request)
        self.assertEqual(early["problem"], mission_state.PROBLEM_CANCEL_UNCONFIRMED)
        self.assertEqual(len(self.reservations("cancel_operation")), cancel_ids)
        # The Runtime's owned stop, confirmed only by observed absence.
        self.runtime_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.engine.live, [])
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        # Now the human confirms: the Mission closes as abandoned.
        request, confirmed, is_error = self.grok_control(mission_id, revision, "cancel")
        self.assertFalse(is_error, confirmed)
        self.assertEqual(confirmed["operation"], mission_state.OPERATION_CONFIRM_CANCEL)
        self.assertTrue(confirmed["controls"]["cancel_confirmed"])
        status, _ = self.grok_status(mission_id)
        self.assertEqual(status["canonical"]["progress"], mission_state.PROGRESS_ABANDONED)
        self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          self.engine.close_calls), (1, 1, ["ws-started-1"]))

    def test_R6_cancel_after_the_claim_allows_at_most_one_invocation(self):
        mission_id, revision = self.authorized()
        self.grok_dispatch(mission_id)
        started, proceed = threading.Event(), threading.Event()
        self.addCleanup(proceed.set)
        self.engine.block_start = (started, proceed)
        self.target_task_status = "ACTIVE"
        worker = threading.Thread(target=self.runtime_pass, daemon=True)
        worker.start()
        self.assertTrue(started.wait(30))
        # The claim is admitted and the engine start is in flight: the
        # human's cancel records the durable stop intent on the OPEN start
        # without waiting for the Runtime.
        request, requested, is_error = self.grok_control(mission_id, revision, "cancel")
        self.assertFalse(is_error, requested)
        open_starts = self.starts(mission_id)
        self.assertEqual(len(open_starts), 1)
        self.assertIsNotNone(open_starts[0]["stop_requested"])
        proceed.set()
        worker.join(60)
        self.runtime_pass()
        # At most one invocation (the admitted runtime start), no task handed
        # over, and the stop confirmed only by the observed absence.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        request, confirmed, is_error = self.grok_control(mission_id, revision, "cancel")
        self.assertFalse(is_error, confirmed)
        self.assertEqual(confirmed["operation"], mission_state.OPERATION_CONFIRM_CANCEL)

    def test_R6_material_edit_stops_the_old_workflow_and_lists_invalidations(self):
        mission_id, revision, workflow_id = self.dispatched()
        live = self.service.get(mission_id)["live_authorization_id"]
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="Resolve it, revised"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.assertEqual(edited["invalidated_authorization_ids"], [live])
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertTrue(any("revision_superseded" in r
                            for r in self.receipts(workflow_id, "")))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))
        # The old dispatch never continues under the stale approval: a
        # further pass performs nothing for it.
        self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))


# ======================================================================
# Row 7 — no verified closure when proof or review is missing/contradictory
# ======================================================================


class R7NoVerifiedClosureTests(LoopCase):

    def assert_never_closed(self, mission_id, *codes):
        status, _ = self.grok_status(mission_id)
        canonical = status["canonical"]
        self.assertEqual(canonical["progress"], mission_state.PROGRESS_IN_PROGRESS)
        self.assertIsNone(canonical["completion"]["closure"])
        self.assertFalse(canonical["completion"]["verified_success"])
        for code in ("closure_eligibility:mission_state_proof_not_satisfied",
                     "completion:no_canonical_closure") + codes:
            self.assertIn(code, canonical["holds"]["codes"])
        # The core itself refuses a closure attempt now.
        with self.assertRaises(mission_record.MissionError) as refused:
            self.gate.service.complete_successfully(
                mission_id, self.gate.service.mint_state_operation_id(self.gate.context),
                self.gate.service.get_state(mission_id)["sequence"], "closure attempt",
                self.gate.context)
        self.assertEqual(refused.exception.problem, "mission_state_proof_not_satisfied")
        return status

    def test_R7_complete_without_approve_never_closes(self):
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id, decision="REJECT")
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assert_never_closed(mission_id, "completion:review_not_approved",
                                 "report:review:REJECT")
        self.assertEqual(self.deliveries(), {})

    def test_R7_approve_without_passing_verification_never_delivers_or_closes(self):
        mission_id, revision, workflow_id = self.completed()
        self.stage(workflow_id, "fix.txt", "broken\n")      # verification exits 3
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][1][:3],
                         ("mission_delivery", False,
                          "mission_delivery_verification_failed"))
        self.runtime_pass()
        self.assert_never_closed(mission_id, "proof_requirement:MISSING")
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        self.assertEqual(self.delivery_transport.performed["run_reverification"], 0)

    def test_R7_a_later_reject_round_drifts_the_candidate_and_nothing_proceeds(self):
        # Stated limit: after COMPLETE the Runtime observes no further review
        # rounds; a later REJECT written into the lease changes the candidate,
        # which is no longer exact — so no proposal, delivery or closure.
        mission_id, revision, workflow_id = self.completed()
        self.write_round(workflow_id, 2, "REJECT")
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("mission_candidate", True, "broker_candidate_not_exact"))
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        self.assertIsNone(status["proposal"])
        self.assert_never_closed(mission_id)
        self.assertEqual(self.deliveries(), {})


# ======================================================================
# Row 9 — expired, mismatched, stale, unauthorized or replayed decisions
# ======================================================================


class R9RefusalTests(LoopCase):

    def decisions(self, mission_id):
        return self.mission_document()["missions"][mission_id]["decisions"]

    def awaiting_delivery_decision(self):
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        return mission_id, revision, workflow_id

    def assert_no_delivery_effects(self):
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        self.assertEqual(self.delivery_transport.performed["push"], 0)
        self.assertEqual(self.delivery_transport.performed["gh_pr_create"], 0)

    def test_R9_a_replayed_answer_records_nothing(self):
        mission_id, revision = self.grok_propose()
        request, decided, is_error = self.grok_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        document = json.dumps(self.mission_document(), sort_keys=True)
        status, body = self.client.respond(request["id"], *accept(request))
        self.assertEqual((status, body), (202, b""))
        self.assertEqual(self.logs.count("client response replayed\n"), 1)
        self.assertEqual(self.logs.count("client response delivered\n"), 1)
        self.assertEqual(json.dumps(self.mission_document(), sort_keys=True), document)
        self.assertEqual([d["decision"] for d in self.decisions(mission_id)], ["APPROVE"])

    def test_R9_a_wrong_confirm_value_records_nothing_and_is_accounted(self):
        mission_id, revision = self.grok_propose()
        request, refused, is_error = self.grok_decide(mission_id, revision,
                                                      answer=wrong_confirm)
        self.assertTrue(is_error)
        self.assertEqual((refused["status"], refused["problem"],
                          refused["decision_recorded"]),
                         ("refused", "elicitation_binding_mismatch", False))
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.mission_document()["authorizations"], {})
        # The reserved decision id is reported and stays unconsumed.
        unconsumed = [r for r in self.reservations("decision") if r["consumed_by"] is None]
        self.assertEqual(len(unconsumed), 1)
        self.assertEqual(refused["decision_id"], [
            key for key, r in self.mission_document()["reservations"].items()
            if r["kind"] == "decision"][0])

    def late(self, answer=accept):
        """The human answers after the elicitation's validity passed."""
        def answered(request):
            self.clock.advance(elicitation_module.ELICITATION_VALIDITY_SECONDS + 1)
            return answer(request)
        return answered

    def test_R9_an_answer_after_the_elicitation_expired_records_nothing(self):
        mission_id, revision = self.grok_propose()
        request, result, is_error = self.grok_decide(mission_id, revision,
                                                     answer=self.late())
        self.assertIsNotNone(request)
        self.assertEqual((result["elicitation_outcome"], result["decision_recorded"]),
                         (elicitation_module.OUTCOME_EXPIRED, False), result)
        self.assertEqual(self.logs.count("client response unsolicited\n"), 1)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.mission_document()["authorizations"], {})
        unconsumed = [r for r in self.reservations("decision") if r["consumed_by"] is None]
        self.assertEqual(len(unconsumed), 1)
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertTrue(is_error)
        self.assertEqual(self.rows(), {})

    def test_R9_a_delivery_answer_after_the_elicitation_expired_moves_nothing(self):
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        request, result, is_error = self.grok_delivery_decide(mission_id, revision,
                                                              answer=self.late())
        self.assertIsNotNone(request)
        self.assertEqual((result["elicitation_outcome"], result["decision_recorded"]),
                         (elicitation_module.OUTCOME_EXPIRED, False), result)
        self.assertEqual(self.logs.count("client response unsolicited\n"), 1)
        for _ in range(2):
            self.runtime_pass()
        self.assert_no_delivery_effects()

    def test_R9_a_lapsed_client_authority_refuses_the_delivery_decision(self):
        """The client-confirmed Mission authority and the delivery proposal
        share one validity, and the proposal is always prepared AFTER the
        authority was issued: the authority lapses first, so the refusal is
        the exact parent-currency code, never the proposal's own expiry."""
        self.assertEqual(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS,
                         delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS)
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        self.clock.advance(delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1)
        before = dict((kind, len(self.reservations(kind)))
                      for kind in ("decision", "state_operation"))
        request, refused, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertIsNone(request)
        self.assertTrue(is_error)
        self.assertEqual((refused["problem"], refused["reason"]), (
            delivery_module.PROBLEM_PROPOSAL_STALE,
            "the proposal binds a Mission revision or authorization that is"
            " no longer current"))
        self.assertEqual(dict((kind, len(self.reservations(kind))) for kind in before),
                         before)
        self.runtime_pass()
        self.assert_no_delivery_effects()

    def test_R9_a_moved_revision_refuses_the_delivery_decision(self):
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed"),
            mission_id=mission_id, expected_revision=revision))
        request, refused, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertIsNone(request)
        self.assertTrue(is_error)
        self.assertIsNotNone(refused["problem"])
        self.runtime_pass()
        self.assert_no_delivery_effects()

    def test_R9_a_client_without_elicitation_is_refused_with_zero_reservations(self):
        mission_id, revision = self.grok_propose()
        plain = self.new_client(capabilities={})
        before = dict((kind, len(self.reservations(kind)))
                      for kind in ("decision", "state_operation", "cancel_operation"))
        for name, arguments in (
            (protocol.TOOL_MISSION_DECIDE, {"mission_id": mission_id, "revision": revision}),
            (protocol.TOOL_DELIVERY_DECIDE, {"mission_id": mission_id, "revision": revision}),
            (protocol.TOOL_MISSION_CONTROL, {"mission_id": mission_id, "revision": revision,
                                             "control": "hold"}),
        ):
            request, refused, is_error = plain.elicited(name, arguments)
            self.assertIsNone(request, name)
            self.assertTrue(is_error, name)
            self.assertEqual(refused["reason"], "client_elicitation_not_negotiated", name)
        self.assertEqual(dict((kind, len(self.reservations(kind))) for kind in before),
                         before)
        self.assertEqual(self.decisions(mission_id), [])

    def test_R9_a_connector_credential_approval_authorizes_no_engagement(self):
        mission_id, revision = self.grok_propose()
        self.client.call(protocol.TOOL_MISSION_APPROVE, {"mission_id": mission_id,
                                                          "revision": revision})
        refused, is_error = self.grok_dispatch(mission_id)
        self.assertTrue(is_error)
        self.assertEqual(refused["problem"], "mission_control_provenance_insufficient")
        self.assertEqual(self.rows(), {})


# ======================================================================
# Row 10 — candidate, target and baseline drift
# ======================================================================


class R10DriftTests(LoopCase):

    _S5 = test_mission_controls.RIntegrationTests
    base_advance = _S5.base_advance
    _hash = _S5._hash

    def awaiting_delivery_decision(self):
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        return mission_id, revision, workflow_id

    def test_R10_a_candidate_change_before_the_answer_refuses_the_decision(self):
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        self.stage(workflow_id, "fix.txt", "fixed differently\n")
        self.runtime_pass()                       # the change is observed
        request, refused, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertIsNone(request)
        self.assertTrue(is_error)
        self.assertEqual(refused["problem"], delivery_module.PROBLEM_PROPOSAL_STALE)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])

    def test_R10_a_candidate_change_after_the_decision_pushes_nothing(self):
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        self.stage(workflow_id, "fix.txt", "fixed differently\n")
        remote = self.remote_refs()
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.remote_refs(), remote)              # ls-remote unchanged
        self.assertEqual((self.delivery_transport.performed["push"],
                          self.delivery_transport.performed["gh_pr_create"]), (0, 0))
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        self.assertIsNone(status["delivery"]["pr_url"] if status["delivery"] else None)

    def test_R10_an_authorized_base_advance_is_refreshed_without_false_drift(self):
        mission_id, revision, workflow_id = self.awaiting_delivery_decision()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        advanced = self.base_advance(workflow_id)
        run_git("-C", self.lease_path(workflow_id), "push", "-q", self.bare,
                "%s:refs/heads/main" % advanced)
        self.runtime_pass()
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        steps = dict((s["step"], s["state"]) for s in status["delivery"]["steps"])
        self.assertEqual(status["delivery"]["phase"], "COMPLETE")
        self.assertEqual(steps["BASE_REFRESH"], "succeeded")
        self.assertEqual((self.delivery_transport.performed["push"],
                          self.delivery_transport.performed["gh_pr_create"]), (1, 1))


# ======================================================================
# Rows 11 and 12 — restart/reconnect recovery; ambiguous external outcomes
# ======================================================================


def crash_once(point):
    """A transport hook: the process dies right AFTER the real effect."""
    fired = []

    def hook(when):
        if when == "after" and not fired:
            fired.append(when)
            raise Crash(point)
    return hook


class DeliveryRecovery(object):
    """Shared by rows 11 and 12 (a mixin, so neither collects the other's
    tests)."""

    def effects(self):
        performed = self.delivery_transport.performed
        return (performed["commit_step"], performed["push"], performed["gh_pr_create"])

    def decided_delivery(self):
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        return mission_id, revision, workflow_id

    def delivered_exactly_once(self, mission_id, workflow_id):
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        delivery = status["delivery"]
        self.assertEqual((delivery["phase"], delivery["pr_url"]),
                         ("COMPLETE", "%s/pull/41" % CANONICAL_URL))
        self.assertEqual(self.effects(), (1, 1, 1))
        self.assertEqual(len(self.delivery_transport.open_prs), 1)
        self.assertEqual(sorted(self.remote_refs()),
                         ["refs/heads/di-mission/%s-r1" % mission_id, "refs/heads/main"])
        self.assertEqual(list(self.rows()), [workflow_id])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        self.assertEqual(len(self.deliveries()), 1)
        return delivery


class R11RecoveryTests(DeliveryRecovery, LoopCase):

    def test_R11_a_restarts_at_every_boundary_one_of_each(self):
        mission_id, revision = self.grok_propose()
        self.restart_grok()                                    # after the proposal
        request, decided, is_error = self.grok_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        self.restart_grok()                                    # after the decision
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        workflow_id = structured["workflow_id"]
        self.restart_dirun()                                   # after the bootstrap
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.restart_dirun()                                   # after the dispatch
        self.restart_grok()
        self.engineering_finishes(workflow_id)                 # the review
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.restart_dirun()                                   # after the result
        self.clean_herd_state(workflow_id)
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        self.stage(workflow_id, "fix.txt", "fixed\n")
        self.wire_delivery(workflow_id)
        self.runtime_pass()                                    # the proposal
        self.restart_dirun()
        self.restart_grok()
        request, delivered, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, delivered)
        self.restart_grok()                                    # after the delivery decision
        self.restart_dirun()
        self.runtime_pass()
        self.restart_dirun()                                   # after the delivery
        self.runtime_pass()
        self.delivered_exactly_once(mission_id, workflow_id)
        self.assertEqual(self.logs.count("grokmcp: serving http://127.0.0.1:%d/mcp\n"
                                         % self.grok_server.server_address[1]), 1)

    def test_R11_a_crash_after_the_commit_recovers_one_of_each(self):
        mission_id, revision, workflow_id = self.decided_delivery()
        self.delivery_transport.hooks["commit_step"] = crash_once("after the commit")
        with self.assertRaises(Crash):
            self.runtime_pass()
        self.assertEqual(self.effects(), (1, 0, 0))
        self.restart_dirun()
        self.runtime_pass()
        self.delivered_exactly_once(mission_id, workflow_id)

    def test_R11_a_R12_b_a_crash_after_the_push_adopts_it_no_second_push(self):
        mission_id, revision, workflow_id = self.decided_delivery()
        self.delivery_transport.hooks["push"] = crash_once("after the push")
        with self.assertRaises(Crash):
            self.runtime_pass()
        self.assertEqual(self.effects(), (1, 1, 0))
        pushed = self.remote_refs()["refs/heads/di-mission/%s-r1" % mission_id]
        record = list(self.deliveries().values())[0]
        self.assertEqual((record["phase"], record["steps"]["PUSH"]["state"],
                          record["steps"]["PUSH"]["receipt"]["state"],
                          record["steps"]["PUSH"]["receipt"]["observed"],
                          record["steps"]["PR_CREATE"]["state"], record["pull_request"]),
                         ("COMMITTED", "executing", "executing", None, "pending", None))
        self.restart_dirun()
        self.restart_grok()
        self.runtime_pass()
        delivery = self.delivered_exactly_once(mission_id, workflow_id)
        self.assertEqual(self.remote_refs()["refs/heads/di-mission/%s-r1" % mission_id],
                         pushed)
        steps = dict((s["step"], s) for s in delivery["steps"])
        self.assertEqual((steps["PUSH"]["state"], steps["PR_CREATE"]["state"]),
                         ("succeeded", "succeeded"))

    def test_R11_a_R12_a_a_crash_after_the_pr_adopts_it_gh_pr_create_total_one(self):
        mission_id, revision, workflow_id = self.decided_delivery()
        self.delivery_transport.hooks["gh_pr_create"] = crash_once("after the PR")
        with self.assertRaises(Crash):
            self.runtime_pass()
        self.assertEqual(self.effects(), (1, 1, 1))
        record = list(self.deliveries().values())[0]
        self.assertEqual((record["steps"]["PR_CREATE"]["state"],
                          record["steps"]["PR_CREATE"]["receipt"]["state"],
                          record["pull_request"]),
                         ("executing", "executing", None))
        self.restart_dirun()
        self.restart_grok()
        self.runtime_pass()
        delivery = self.delivered_exactly_once(mission_id, workflow_id)
        steps = dict((s["step"], s) for s in delivery["steps"])
        self.assertEqual(steps["PR_CREATE"]["state"], "succeeded")
        self.assertEqual(len(self.delivery_transport.created), 1)

    # -- dispatch boundaries ------------------------------------------------------

    def dispatch_crash(self, method, crash_after):
        """Dispatch through the gated Broker with the process dying at the
        ``_MissionStartGuard.<method>`` boundary: after the real call when
        ``crash_after`` (the canonical start admitted), else instead of it."""
        mission_id, revision = self.authorized()
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        workflow_id = structured["workflow_id"]
        self.target_task_status = "ACTIVE"
        real = getattr(broker_module._MissionStartGuard, method)

        def crashing(guard, point, *args, **kwargs):
            if crash_after:
                real(guard, point, *args, **kwargs)
            raise Crash("%s %s" % (method, point))
        with mock.patch.object(broker_module._MissionStartGuard, method, crashing):
            with self.assertRaises(Crash):
                self.runtime_pass()
        return mission_id, revision, workflow_id

    def test_R11_b_a_crash_after_the_marker_before_the_spawn_never_spawns_twice(self):
        mission_id, revision, workflow_id = self.dispatch_crash("open", True)
        entry = self.record(workflow_id)
        self.assertEqual(len(self.receipts(workflow_id, "dispatched handoff revision")), 1)
        self.assertIsNone(entry["target_engine"])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (0, 0))
        starts = self.starts(mission_id)
        self.assertEqual(len(starts), 1)
        self.assertIsNone(starts[0]["settlement"])
        self.restart_dirun()
        self.restart_grok()
        for _ in range(3):
            self.runtime_pass()
        self.grok_dispatch(mission_id)
        self.runtime_pass()
        # Nothing was invoked before the crash and the durable state proves
        # no identity: zero spawns in total, never a retried start.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          self.engine.close_calls), (0, 0, []))
        self.assertEqual(list(self.rows()), [workflow_id])
        self.assert_uncertain_start(mission_id, workflow_id)

    def assert_uncertain_start(self, mission_id, workflow_id):
        """The truthful uncertain status (the production vocabulary: an
        engagement start settled ``uncertain`` by its owner's recovery, a
        stop required and NOT confirmed, no identity; the workflow BLOCKED
        without a task id)."""
        status, is_error = self.grok_status(mission_id)
        self.assertFalse(is_error, status)
        starts = status["canonical"]["engagements"]["starts"]
        self.assertEqual([(s["point"], s["observed_outcome"], s["settled"], s["identity"],
                           s["stop_required"], s["stop_confirmed"]) for s in starts],
                         [("runtime", mission_state.START_OUTCOME_UNCERTAIN, True, None,
                           True, False)])
        self.assertTrue(starts[0]["stop_observation"].endswith(
            "nothing is closed and the stop is PENDING"), starts[0])
        self.assertEqual([(r["workflow_id"], r["phase"], r["task_id"])
                          for r in status["workflows"]["rows"]],
                         [(workflow_id, wa_record.PHASE_BLOCKED, None)])
        self.assertEqual(status["canonical"]["progress"], mission_state.PROGRESS_IN_PROGRESS)
        entry = self.record(workflow_id)
        self.assertEqual(entry["ambiguity"]["state"], wa_record.AMBIGUITY_NONE)
        self.assertEqual(len(self.receipts(
            workflow_id, "mission gate block: %s" % gate_module.PROBLEM_START_UNSETTLED)), 1)

    def test_R11_c_R12_c_an_unresolved_identity_starts_nothing_new(self):
        mission_id, revision, workflow_id = self.dispatch_crash("close", False)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        entry = self.record(workflow_id)
        self.assertIsNone(entry["target_engine"])
        self.assertTrue(runtime_module.dispatch_identity_unresolved(entry))
        self.restart_dirun()
        self.restart_grok()
        for _ in range(3):
            self.runtime_pass()
        self.grok_dispatch(mission_id)
        self.runtime_pass()
        # The one pre-crash start only: zero NEW starts, tasks or closes
        # (the started runtime's ownership is unprovable, so nothing is
        # closed and it stays listed).
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          self.engine.close_calls, len(self.engine.live)), (1, 0, [], 1))
        self.assertEqual(list(self.rows()), [workflow_id])
        self.assert_uncertain_start(mission_id, workflow_id)


class R12AmbiguityTests(DeliveryRecovery, LoopCase):
    """Rows 12(a)-(c) are the adoption and unresolved-identity cases of
    ``R11RecoveryTests`` (named ``..._R12_a_...``, ``..._R12_b_...``,
    ``..._R12_c_...``); (d) is here."""

    def failing(self, verb):
        def fail(*args, **kwargs):
            raise transport_module.DeliveryTransportError("%s unreachable" % verb)
        return fail

    def delivery_outcome(self, workflow_id):
        outcomes = dict(self.runtime_pass()[workflow_id])
        return outcomes[runtime_module.DELIVERY_LABEL]

    def test_R12_d_a_failed_ls_remote_pushes_nothing_and_names_the_failure(self):
        mission_id, revision, workflow_id = self.decided_delivery()
        transport = self.delivery_transport
        transport.ls_remote = self.failing("ls-remote")
        for _ in range(2):
            outcome = self.delivery_outcome(workflow_id)
            self.assertEqual((outcome.ok, outcome.outcome, outcome.problem),
                             (False, "delivery_held",
                              delivery_module.PROBLEM_TRANSPORT_FAILED))
            self.assertTrue(outcome.detail.startswith(
                "step BASE_REFRESH failed retryably (transport: ls-remote"
                " unreachable); nothing is assumed absent"), outcome.detail)
        self.assertEqual(self.effects(), (0, 0, 0))
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        # The failure precedes every receipt (the P1-A6 record has no field
        # for it): the Runtime recorded it ONCE on the workflow record it
        # owns (two failing passes, one ``dheld-`` receipt), and the
        # read-only delivery status names it (Lead disposition L1).
        held = [r for r in self.record(workflow_id)["receipts"]
                if r["turn_id"].startswith(artifacts.HELD_TURN_PREFIX)]
        self.assertEqual(len(held), 1)
        expected = ("%s: step BASE_REFRESH, before any receipt: transport: ls-remote"
                    " unreachable (recorded by the Runtime at %s on workflow %s)"
                    % (delivery_module.PROBLEM_TRANSPORT_FAILED, held[0]["recorded_at"],
                       workflow_id))

        def read_status(client):
            stores = self.store_bytes()
            status, is_error = client.call(protocol.TOOL_DELIVERY_STATUS,
                                           {"mission_id": mission_id})
            self.assertFalse(is_error, status)
            self.assertEqual(self.store_bytes(), stores)      # a pure read
            return status
        status = read_status(self.client)
        self.assertEqual((status["delivery"]["phase"], status["delivery"]["blocker_problem"],
                          [s["state"] for s in status["delivery"]["steps"]],
                          status["uncertainty"]),
                         ("AUTHORIZED", None, ["pending"] * 4, [expected]))
        # Durable: a fresh Runtime object, a grokmcp restart and a new
        # client read the same named failure; still no effect.
        self.restart_dirun()
        self.restart_grok()
        self.assertEqual(read_status(self.new_client())["uncertainty"], [expected])
        self.assertEqual(self.effects(), (0, 0, 0))
        # The remote answers again: delivered exactly once, nothing skipped,
        # and the recorded failure no longer describes the delivery.
        del transport.ls_remote
        self.runtime_pass()
        self.delivered_exactly_once(mission_id, workflow_id)
        self.assertEqual(read_status(self.client)["uncertainty"], [])

    def test_R12_d_a_failed_pr_lookup_creates_no_second_pr(self):
        mission_id, revision, workflow_id = self.decided_delivery()
        transport = self.delivery_transport
        transport.hooks["gh_pr_create"] = crash_once("after the PR")
        with self.assertRaises(Crash):
            self.runtime_pass()
        self.restart_dirun()
        transport.gh_pr_list = self.failing("gh pr list")
        for _ in range(2):
            outcome = self.delivery_outcome(workflow_id)
            self.assertEqual((outcome.ok, outcome.outcome, outcome.problem),
                             (False, "delivery_held",
                              delivery_module.PROBLEM_TRANSPORT_FAILED))
            self.assertIn("step PR_CREATE failed retryably", outcome.detail)
        self.assertEqual(self.effects(), (1, 1, 1))              # no second PR
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        steps = dict((s["step"], s) for s in status["delivery"]["steps"])
        self.assertEqual((status["delivery"]["phase"], status["delivery"]["pr_url"],
                          steps["PR_CREATE"]["state"], steps["PR_CREATE"]["receipt_state"]),
                         ("PUSHED", None, "failed_retryable", "failed_retryable"))
        self.assertIn("%s: step PR_CREATE failed retryably (transport: gh pr list"
                      " unreachable); nothing is assumed absent — the next pass"
                      " re-queries before any effect"
                      % delivery_module.PROBLEM_TRANSPORT_FAILED, status["uncertainty"])
        # GitHub answers again: the existing PR is ADOPTED, never re-created.
        del transport.gh_pr_list
        self.runtime_pass()
        self.delivered_exactly_once(mission_id, workflow_id)
        self.assertEqual(len(self.delivery_transport.created), 1)


# ======================================================================
# Row 13 — recovery preserves identity, revision, budget, checkpoint, linkage
# ======================================================================


class R13PreservationTests(LoopCase):

    def preserved(self, mission_id, workflow_id, service):
        """What must survive: ids, revision, linkage AND budget as the Grok
        status reports them (the budget is the Mission Core's own
        projection, read-only — Lead disposition L7: pinned equal to the
        core's ``get_state`` over the same store, with every store byte
        unchanged by the read); the latest checkpoint — which the status
        view does not carry, the required behaviour naming budget, not
        checkpoints — through ``service``, a Mission service object."""
        stores = self.store_bytes()
        status, is_error = self.grok_status(mission_id)
        self.assertFalse(is_error, status)
        self.assertEqual(self.store_bytes(), stores)
        canonical = status["canonical"]
        state = service.get_state(mission_id)
        self.assertEqual(canonical.get("budget"), state["budget"])
        entry = self.record(workflow_id)
        return {
            "mission_id": canonical["mission_id"],
            "revision": canonical["current_revision"],
            "authorization": canonical["live_authorization_id"],
            "activation": state["contract"]["activation_id"],
            "budget": canonical.get("budget"),
            "checkpoint": state["latest_checkpoint"],
            "engagements": canonical["engagements"]["reservations"],
            "workflow": [(r["workflow_id"], r["revision"], r["task_id"])
                         for r in status["workflows"]["rows"]],
            "linkage": (entry[wa_record.MISSION_AUTHORITY_KEY],
                        entry[wa_record.MISSION_ENGAGEMENT_KEY]),
            "task_id": entry["target_engine"]["task_id"],
        }

    def test_R13_fresh_objects_over_the_same_stores_preserve_everything(self):
        mission_id, revision, workflow_id = self.dispatched()
        # A checkpoint through the Task 5 core (no client tool writes one).
        recorded = self.op("record_checkpoint", mission_id, ["dispatched"],
                           ["verification"], "retry on the next pass", "stop on cancel")
        before, _ = self.grok_status(mission_id)
        kept = self.preserved(mission_id, workflow_id, self.gate.service)
        self.assertEqual((kept["checkpoint"]["checkpoint_id"], kept["budget"]),
                         (recorded["checkpoint_id"], recorded["budget"]))
        # L7: the budget as the human reads it through di_mission_status —
        # the contract's continuation budget (2 attempts, 8 checkpoints),
        # none spent on the initial engagement, one checkpoint recorded.
        self.assertEqual(before["canonical"].get("budget"), {
            "attempts_consumed": 0, "attempts_remaining": 2,
            "checkpoints_consumed": 1, "checkpoints_remaining": 7})
        self.assertEqual(kept["budget"]["checkpoints_consumed"], 1)
        self.assertEqual(kept["linkage"][0]["mission_id"], mission_id)
        self.assertEqual(kept["linkage"][0]["authorization_id"], kept["authorization"])
        self.assertEqual(kept["linkage"][1]["engagement_id"],
                         kept["engagements"][0]["engagement_id"])
        self.assertEqual((kept["revision"], kept["task_id"]), (revision, "task-started-1"))
        stores = self.store_bytes()
        # Fresh objects of BOTH processes: the same answer, byte for byte
        # (the per-call reference aside), and nothing written by reading.
        self.restart_grok()
        self.restart_dirun()
        after, _ = self.grok_status(mission_id, client=self.new_client())
        before.pop("call_ref")
        after.pop("call_ref")
        self.assertEqual(json.dumps(after, sort_keys=True), json.dumps(before, sort_keys=True))
        self.assertEqual(self.store_bytes(), stores)
        # The fresh service (restart_dirun's) reads them identically, and a
        # pass by the fresh Runtime changes none of them.
        fresh = self.gate.service
        self.assertEqual(self.preserved(mission_id, workflow_id, fresh), kept)
        self.runtime_pass()
        self.assertEqual(self.preserved(mission_id, workflow_id, fresh), kept)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))


# ======================================================================
# Row 14 — cross-Mission isolation; unauthorized control refusal
# ======================================================================


class R14IsolationTests(LoopCase):

    def mission_slice(self, mission_id):
        """Every stored byte that belongs to ONE Mission: its record, state
        and authorizations, its workflow rows, its deliveries and its
        attention records. (Reservations are principal-bound, not
        Mission-bound; they are counted separately.)"""
        document = self.mission_document()
        mission = document["missions"][mission_id]
        return json.dumps({
            "mission": mission,
            "state": document["mission_state"].get(mission_id),
            "authorizations": dict((a, document["authorizations"][a])
                                   for a in mission["authorization_ids"]),
            "workflows": dict((w, row) for w, row in self.rows().items()
                              if (row.get(wa_record.MISSION_AUTHORITY_KEY) or {})
                              .get("mission_id") == mission_id),
            "deliveries": dict((d, row) for d, row in self.deliveries().items()
                               if row["mission"]["workflow_id"] == mission_id),
            "attention": dict((a, row) for a, row in self.attention_records().items()
                              if row["mission_id"] == mission_id),
        }, sort_keys=True)

    def core_service(self):
        """The Mission service exactly as ``grokmcp`` composes it (the test
        fixture's ``WiredService`` replaces the control operations with
        one-argument conveniences, so it is not used here)."""
        return mission_service_module.MissionService(
            mission_store_module.MissionStore(self.mission_dir), lambda: self.clock())

    def connector_context(self):
        """Exactly the ordinary ingress context ``server.py`` builds from
        the verified bearer credential (no client confirmation)."""
        return mission_record.AuthenticatedContext(
            transport=protocol.SOURCE,
            principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
            principal_ref=str(server_module.CONNECTOR_CREDENTIAL_ORDINAL),
            configured_subject=None)

    def two_missions(self):
        """A dispatched and running; B approved, then EDITED (revision 2,
        awaiting a decision, no workflow)."""
        a, a_revision, workflow_id = self.dispatched()
        b, _ = self.authorized(objective="Another objective")
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="Another objective, revised"),
            mission_id=b, expected_revision=1))
        self.assertFalse(is_error, edited)
        self.assertEqual(edited["revision"], 2)
        return a, a_revision, workflow_id, b

    def test_R14_a_controls_and_decisions_with_the_other_missions_ids(self):
        a, a_revision, workflow_id, b = self.two_missions()
        slice_b = self.mission_slice(b)
        # 1. B decided at A's revision number: stale for B.
        request, refused, is_error = self.grok_decide(b, a_revision)
        self.assertIsNone(request)
        self.assertEqual(refused["problem"], "mission_stale_revision")
        # 2. A's hold card answered with B's confirm value: mismatch.
        b_value = controls_module.ControlDesk(self.core_service()).card(b, 2, "hold")
        self.assertTrue(b_value["ok"], b_value)

        def b_confirm(request):
            return "accept", {"confirm": b_value["confirm_value"]}
        request, refused, is_error = self.grok_control(a, a_revision, "hold",
                                                       answer=b_confirm)
        self.assertEqual((refused["status"], refused["problem"]),
                         ("refused", "elicitation_binding_mismatch"))
        self.assertFalse(self.gate.service.mission_controls(a)["hold_active"])
        # 3. A delivery decision for B, which has no proposal.
        request, refused, is_error = self.grok_delivery_decide(b, 2)
        self.assertIsNone(request)
        self.assertEqual(refused["problem"], delivery_module.PROBLEM_PROPOSAL_ABSENT)
        # 4. The honest hold of A applies to A alone; the Runtime honours it.
        request, applied, is_error = self.grok_control(a, a_revision, "hold")
        self.assertEqual(applied["status"], "applied", applied)
        self.runtime_pass()
        self.assertTrue(self.gate.service.mission_controls(a)["hold_active"])
        self.assertEqual(self.mission_slice(b), slice_b)
        self.assertEqual([r["workflow_id"] for r in self.rows().values()], [workflow_id])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R14_b_connector_credential_controls_are_refused(self):
        a, a_revision, workflow_id, b = self.two_missions()
        slices = (self.mission_slice(a), self.mission_slice(b))
        connector = self.connector_context()
        core = self.core_service()
        desk = controls_module.ControlDesk(core)
        reservations = len(self.mission_document()["reservations"])
        # A cancel id is never even issued to the connector credential.
        card = desk.card(a, a_revision, "cancel")
        with self.assertRaises(mission_record.MissionError) as caught:
            desk.reserve(card["binding"], connector)
        self.assertEqual(caught.exception.problem, mission_state.PROBLEM_CONTROL_PROVENANCE)
        # A hold's ordinary operation id is reserved, and the core refuses
        # the control itself: nothing recorded, for either Mission.
        for mission_id, revision in ((a, a_revision), (b, 2)):
            card = desk.card(mission_id, revision, "hold")
            self.assertTrue(card["ok"], card)
            operation_id = desk.reserve(card["binding"], connector)
            applied = desk.apply(card["binding"], operation_id, connector)
            self.assertEqual((applied["ok"], applied["recorded"], applied["problem"]),
                             (False, False, mission_state.PROBLEM_CONTROL_PROVENANCE),
                             mission_id)
        # The core itself, directly, with the connector credential.
        with self.assertRaises(mission_record.MissionError) as caught:
            core.request_hold(a, core.mint_state_operation_id(connector),
                              core.get_state(a)["sequence"], "connector hold",
                              context=connector)
        self.assertEqual(caught.exception.problem, mission_state.PROBLEM_CONTROL_PROVENANCE)
        # Accounted: three ordinary reservations (two hold ids, one state
        # operation id), none consumed; no cancel reservation.
        document = self.mission_document()
        self.assertEqual(len(document["reservations"]), reservations + 3)
        self.assertEqual(self.reservations("cancel_operation"), [])
        self.runtime_pass()
        self.assertEqual((self.mission_slice(a), self.mission_slice(b)), slices)


# ======================================================================
# Row 15 — user-visible provider, observation and transport failures
# ======================================================================


class RefusingPresenter(attention_module.ToolResultPresenter):
    """The presenter refuses BEFORE surfacing (a provider refusal)."""

    def present(self, destination, presentation):
        self.presented.append(presentation["attention_id"])
        return coordination_attention.PresentationReceipt(
            False, None, "presenter refused: destination unavailable")


class R15FailureBehaviourTests(LoopCase):

    def test_R15_an_operator_raise_is_reported_by_class_name_only(self):
        sentinel = "SENTINEL-IN-OPERATOR-EXCEPTION"
        self.operator.raise_on_submit = RuntimeError("boom " + sentinel)
        structured, is_error = self.client.call(protocol.TOOL_OPERATOR_TURN,
                                                {"text": "status please"})
        self.assertTrue(is_error)
        self.assertIn("RuntimeError", json.dumps(structured))
        for text in [json.dumps(structured)] + self.logs:
            self.assertNotIn(sentinel, text)
        # The Mission tools are unaffected by the provider's failure.
        mission_id, revision = self.grok_propose()
        self.assertEqual(revision, 1)

    def test_R15_an_unreadable_store_is_unavailable_never_absent_and_nothing_is_written(self):
        mission_id, revision, workflow_id = self.dispatched()
        stores = self.store_bytes()
        for path, check in (
            (os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME), "workflow"),
            (os.path.join(self.mission_dir, mission_store_module.MISSIONS_FILE_NAME),
             "mission"),
        ):
            os.chmod(path, 0)
            try:
                status, is_error = self.grok_status(mission_id)
            finally:
                os.chmod(path, 0o600)
            if check == "workflow":
                self.assertFalse(is_error, status)
                self.assertEqual((status["workflows"]["availability"],
                                  status["stores"]["workflow"]["availability"]),
                                 ("unavailable", "unavailable"), status)
                self.assertEqual(status["canonical"]["availability"], "present")
            else:
                self.assertEqual(status["canonical"]["availability"], "unavailable",
                                 status)
                # Named by class only (no path, no content).
                self.assertEqual(status["canonical"]["problem"],
                                 "MissionStoreError: the Mission store could not be"
                                 " read: PermissionError")
        self.assertEqual(self.store_bytes(), stores)              # zero writes

    def test_R15_an_sse_write_failure_after_admission_leaves_the_decision_standing(self):
        mission_id, revision = self.grok_propose()

        def fault(request_id):
            raise BrokenPipeError("injected final-event write failure")
        self.grok_server.presentation_fault = fault
        connection, response = self.client.open_elicited(
            protocol.TOOL_MISSION_DECIDE, {"mission_id": mission_id, "revision": revision})
        try:
            request = self.client.read_event(response)
            status, _ = self.client.respond(request["id"], *accept(request))
            self.assertEqual(status, 202)
            self.assertIsNone(self.client.read_event(response))   # no result event
        finally:
            response.close()
            connection.close()
        self.grok_server.presentation_fault = None
        self.assertIn("presentation write failed after admission BrokenPipeError;"
                      " result presentation uncertain\n", self.logs)
        # The decision stands: recorded, authorizing, never rolled back.
        document = self.mission_document()
        self.assertEqual([d["decision"] for d in document["missions"][mission_id]
                          ["decisions"]], ["APPROVE"])
        self.assertEqual(len(document["authorizations"]), 1)
        self.assertEqual([r["consumed_by"] is not None
                          for r in self.reservations("decision")], [True])
        status, _ = self.grok_status(mission_id)
        self.assertEqual(status["canonical"]["live_authorization_id"],
                         list(document["authorizations"])[0])
        # ... and it authorizes: the dispatch the unseen result allowed.
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        self.assertEqual(list(self.rows()), [structured["workflow_id"]])

    def needs_human(self):
        """A NEEDS_HUMAN attention record (an EDIT of a dispatched Mission),
        projected by the Runtime and still PENDING."""
        mission_id, revision, workflow_id = self.dispatched()
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.runtime_pass()
        records = list(self.attention_records().values())
        self.assertEqual([(r["condition_kind"], r["presentation"]) for r in records],
                         [("NEEDS_HUMAN", "PENDING")])
        return mission_id, records[0]["attention_id"]

    def test_R15_a_presenter_refusal_before_surfacing_keeps_the_record_pending(self):
        mission_id, attention_id = self.needs_human()
        refusing = mock.patch.object(attention_module, "ToolResultPresenter",
                                     RefusingPresenter)
        refusing.start()
        self.addCleanup(refusing.stop)
        self.restart_grok()                        # the desk takes the presenter
        pulled, is_error = self.client.call(protocol.TOOL_ATTENTION_PULL, {})
        self.assertEqual(pulled["surfaced_now"], [])
        record = self.attention_records()[attention_id]
        self.assertEqual((record["presentation"], record["surfaced_at"],
                          record["surfaced_message_ref"]), ("PENDING", None, None))
        self.assertEqual(len(self.attention_records()), 1)

    def test_R15_b_a_response_lost_after_surfacing_stays_surfaced_unconfirmed(self):
        mission_id, attention_id = self.needs_human()
        # The pull is sent and its response is never read (the transport
        # fails after the presenter succeeded).
        connection = http.client.HTTPConnection("127.0.0.1", self.grok_server.server_address[1],
                                                timeout=30)
        connection.request("POST", "/mcp", json.dumps({
            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
            "params": {"name": protocol.TOOL_ATTENTION_PULL, "arguments": {}}}
        ).encode("utf-8"), self.client._headers("application/json, text/event-stream"))
        connection.close()
        deadline = time.monotonic() + 10
        while self.attention_records()[attention_id]["presentation"] != "SURFACED":
            self.assertLess(time.monotonic(), deadline, "the lost pull never surfaced")
            time.sleep(0.05)
        surfaced = self.attention_records()[attention_id]
        # The next pull lists it under ``surfaced`` (receipt unconfirmed):
        # never re-created, never reverted to PENDING, never surfaced again.
        pulled, is_error = self.client.call(protocol.TOOL_ATTENTION_PULL, {})
        self.assertFalse(is_error, pulled)
        self.assertEqual(pulled["surfaced_now"], [])
        self.assertEqual([(a["attention_id"], a["presentation"], a["client_receipt"])
                          for a in pulled["surfaced"]],
                         [(attention_id, "SURFACED", "unconfirmed")])
        self.assertEqual(self.attention_records(), {attention_id: surfaced})


# ======================================================================
# Supervisor item E — a Mission store that cannot be used fails CLOSED,
# typed and reversible, through bootstrap, gate, Runtime and status
# ======================================================================


class ItemEStoreUnavailableTests(LoopCase):
    """Three conditions of the ONE Mission store: CORRUPT (bytes that do
    not parse), UNREADABLE (mode 000) and SATURATED (the state-operation
    reservation bound reached — a BOUND SEAM: ``RESERVATION_CAPS`` is
    lowered to the held count plus the control headroom, because the real
    bound, 65536, cannot be filled by minting in a test). Recovery is the
    operator's: the original bytes back, the mode back, the bound lifted."""

    CONDITIONS = ("corrupt", "unreadable", "saturated")

    def store_path(self, store):
        return {
            "mission": os.path.join(self.mission_dir, mission_store_module.MISSIONS_FILE_NAME),
            "workflow": os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME),
            "delivery": os.path.join(self.store_dir, delivery_store_module.STORE_FILE_NAME),
        }[store]

    def make_unavailable(self, condition, store="mission"):
        """Apply ``condition`` to ``store``; returns the recovery callable.
        Saturation is a BOUND SEAM per store: the Mission state-operation
        reservation cap, ``MAX_WORKFLOW_RECORDS`` or
        ``MAX_PR_DELIVERY_RECORDS`` lowered to what is held."""
        path = self.store_path(store)
        if condition == "corrupt":
            with open(path, "rb") as handle:
                original = handle.read()
            with open(path, "wb") as handle:
                handle.write(b"{\"truncated\": [")

            def recover():
                with open(path, "wb") as handle:
                    handle.write(original)
            return recover
        if condition == "unreadable":
            os.chmod(path, 0)
            return lambda: os.chmod(path, 0o600)
        if store == "workflow":
            bound = mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", len(self.rows()))
        elif store == "delivery":
            bound = mock.patch.object(delivery_store_module, "MAX_PR_DELIVERY_RECORDS",
                                      len(self.deliveries()))
        else:
            document = self.mission_document()
            kind = mission_store_module.RESERVATION_KIND_STATE_OPERATION
            held = sum(1 for r in document["reservations"].values() if r["kind"] == kind)
            bound = mock.patch.dict(mission_store_module.RESERVATION_CAPS, {
                kind: held + mission_state.CONTROL_OPERATION_HEADROOM})
        bound.start()
        return bound.stop

    @staticmethod
    def changed_paths(before, after, path="", depth=5):
        """The JSON paths at which ``after`` differs from ``before``."""
        if before == after:
            return []
        if depth == 0 or not isinstance(before, (dict, list)) or type(before) is not type(after):
            return [path or "/"]
        if isinstance(before, list):
            if len(before) != len(after):
                return ["%s[len %d->%d]" % (path, len(before), len(after))]
            keys = range(len(before))
        else:
            keys = sorted(set(before) | set(after))
        found = []
        for key in keys:
            if isinstance(before, dict) and (key not in before or key not in after):
                found.append("%s/%s(%s)" % (path, key, "added" if key in after else "removed"))
                continue
            found.extend(ItemEStoreUnavailableTests.changed_paths(
                before[key], after[key], "%s/%s" % (path, key), depth - 1))
        return found

    def mission_document_or_none(self):
        try:
            return json.dumps(self.mission_document(), sort_keys=True)
        except mission_store_module.MissionStoreError:
            return None

    def effects(self):
        """Every count that proves nothing was minted, started, moved or
        closed: engine calls, spawns, workflow phases and task ids,
        capabilities minted and consumed, git refs, deliveries, attention."""
        entries = self.capability_entries()
        return {
            "engine": (len(self.engine.starts), len(self.engine.tasks),
                       list(self.engine.close_calls), len(self.spawn_requests)),
            "workflows": dict((w, (r["phase"], (r["target_engine"] or {}).get("task_id")))
                              for w, r in self.rows().items()),
            "capabilities": (len(entries), sum(1 for e in entries.values()
                                               if e["consumed_at"] is not None)),
            "remote": self.remote_refs(),
            "deliveries": dict((d, r["phase"]) for d, r in self.deliveries().items()),
            "attention": dict((a, r["presentation"])
                              for a, r in self.attention_records().items()),
            "turns": len(self.role_turn.calls),
        }

    # -- bootstrap -------------------------------------------------------------

    def bootstrap_while(self, condition):
        mission_id, revision = self.authorized()
        before = self.effects()
        document = json.dumps(self.mission_document(), sort_keys=True)
        recover = self.make_unavailable(condition)
        try:
            for _ in range(2):
                refused, is_error = self.grok_dispatch(mission_id)
                self.assertTrue(is_error, refused)
                self.assertEqual(refused["problem"],
                                 engineering_module.PROBLEM_SOURCE_UNAVAILABLE,
                                 (condition, refused))
            self.runtime_pass()
            self.assertEqual(self.effects(), before)
        finally:
            recover()
        self.assertEqual(json.dumps(self.mission_document(), sort_keys=True), document)
        self.assertEqual(self.rows(), {})
        # Recovery: the same Mission dispatches exactly once.
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        self.assertEqual(self.record(structured["workflow_id"])["phase"],
                         wa_record.PHASE_DISPATCHED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_E_bootstrap_corrupt(self):
        self.bootstrap_while("corrupt")

    def test_E_bootstrap_unreadable(self):
        self.bootstrap_while("unreadable")

    def test_E_bootstrap_saturated(self):
        self.bootstrap_while("saturated")

    # -- gate and Runtime --------------------------------------------------------

    def gate_while(self, condition):
        mission_id, revision = self.authorized()
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        workflow_id = structured["workflow_id"]
        self.target_task_status = "ACTIVE"
        before = self.effects()
        recover = self.make_unavailable(condition)
        try:
            for _ in range(2):
                outcomes = self.runtime_pass()
            after = self.effects()
            if after != before:
                self.fail(json.dumps({"before": before, "after": after,
                                      "outcomes": outcome_view(outcomes),
                                      "receipts": [r["bounded_summary"][:200] for r in
                                                   self.record(workflow_id)["receipts"]]},
                                     default=str))
            problems = set(o.problem for _, o in outcomes.get(workflow_id, []))
            self.assertIn(gate_module.PROBLEM_SOURCE_UNAVAILABLE, problems,
                          (condition, outcome_view(outcomes)))
            entry = self.record(workflow_id)
            self.assertNotEqual(entry["phase"], wa_record.PHASE_BLOCKED)
            self.assertEqual(entry["ambiguity"]["state"], wa_record.AMBIGUITY_NONE)
        finally:
            recover()
        state = self.gate.service.get_state(mission_id)
        self.assertEqual(state["progress"], mission_state.PROGRESS_IN_PROGRESS)
        self.assertFalse(mission_state.control_view(state["record"])["cancel_requested"])
        self.assertIsNotNone(self.gate.service.get(mission_id)["live_authorization_id"])
        # Recovery: the next pass starts exactly once.
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))

    def test_E_gate_corrupt(self):
        self.gate_while("corrupt")

    def test_E_gate_unreadable(self):
        self.gate_while("unreadable")

    def test_E_gate_saturated(self):
        self.gate_while("saturated")

    # -- completion and delivery ---------------------------------------------------

    def delivery_while(self, condition):
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        before = self.effects()
        recover = self.make_unavailable(condition)
        try:
            for _ in range(2):
                outcomes = self.runtime_pass()
            after = self.effects()
            if after != before:
                self.fail(json.dumps({"before": before, "after": after,
                                      "outcomes": outcome_view(outcomes),
                                      "delivery": self.deliveries()}, default=str))
        finally:
            recover()
        self.assertEqual(self.delivery_transport.performed["push"], 0)
        self.assertEqual(self.gate.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_IN_PROGRESS)
        # Recovery: delivered exactly once, never revoked.
        self.runtime_pass()
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        self.assertEqual((status["delivery"]["phase"], status["delivery"]["revoked"]),
                         ("COMPLETE", False))
        self.assertEqual(self.gate.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_COMPLETED)

    def test_E_delivery_corrupt(self):
        self.delivery_while("corrupt")

    def test_E_delivery_unreadable(self):
        self.delivery_while("unreadable")

    def test_E_delivery_saturated(self):
        self.delivery_while("saturated")

    # -- the workflow store -------------------------------------------------------

    def workflow_bootstrap_while(self, condition):
        """Mission A holds the one workflow row; Mission B's bootstrap runs
        while the WORKFLOW store cannot be used."""
        a, a_revision = self.authorized()
        structured, is_error = self.grok_dispatch(a)
        self.assertFalse(is_error, structured)
        b, b_revision = self.authorized(objective="Another objective")
        before = self.effects()
        document = json.dumps(self.mission_document(), sort_keys=True)
        recover = self.make_unavailable(condition, "workflow")
        try:
            for _ in range(2):
                refused, is_error = self.grok_dispatch(b)
                self.assertTrue(is_error, refused)
                self.assertEqual(refused["problem"], engineering_module.PROBLEM_WORKFLOW_STORE,
                                 (condition, refused))
            self.assertEqual(json.dumps(self.mission_document(), sort_keys=True), document)
        finally:
            recover()
        self.assertEqual(self.effects(), before)
        # Recovery: B's bootstrap creates exactly its one row.
        structured, is_error = self.grok_dispatch(b)
        self.assertFalse(is_error, structured)
        self.assertEqual(len(self.rows()), 2)

    def test_E_workflow_store_bootstrap_corrupt(self):
        self.workflow_bootstrap_while("corrupt")

    def test_E_workflow_store_bootstrap_unreadable(self):
        self.workflow_bootstrap_while("unreadable")

    def test_E_workflow_store_bootstrap_saturated(self):
        self.workflow_bootstrap_while("saturated")

    def workflow_runtime_while(self, condition):
        mission_id, revision = self.authorized()
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        workflow_id = structured["workflow_id"]
        self.target_task_status = "ACTIVE"
        document = json.dumps(self.mission_document(), sort_keys=True)
        path = self.store_path("workflow")
        with open(path, "rb") as handle:
            workflow_bytes = handle.read()
        recover = self.make_unavailable(condition, "workflow")
        try:
            for _ in range(2):
                self.runtime_pass()
            status, is_error = self.grok_status(mission_id)
            self.assertEqual(status["workflows"]["availability"], "unavailable", status)
            self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                              len(self.spawn_requests), len(self.role_turn.calls)),
                             (0, 0, 0, 0))
        finally:
            recover()
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), workflow_bytes)
        self.assertEqual(json.dumps(self.mission_document(), sort_keys=True), document)
        self.assertEqual(self.capability_entries(), {})
        # Recovery: one start, one task.
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_E_workflow_store_runtime_corrupt(self):
        self.workflow_runtime_while("corrupt")

    def test_E_workflow_store_runtime_unreadable(self):
        self.workflow_runtime_while("unreadable")

    # -- the delivery store --------------------------------------------------------

    def delivery_store_while(self, condition):
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        path = self.store_path("delivery")
        if not os.path.exists(path):
            # A valid EMPTY store, written by the store's own API, so the
            # condition has bytes to act on.
            delivery_store_module.DeliveryStore(self.store_dir).save(
                delivery_store_module.default_document())
        before = self.effects() if condition == "saturated" else None
        document = json.dumps(self.mission_document(), sort_keys=True)
        # The source branch was prepared with the proposal (before the
        # decision): those two preparation verbs are already counted.
        performed = dict(self.delivery_transport.performed)
        self.assertEqual((performed["prepare_ref"], performed["attach_head"]), (1, 1))
        lease_head = self.git(workflow_id, "rev-parse", "HEAD")
        recover = self.make_unavailable(condition, "delivery")
        try:
            for _ in range(2):
                outcome = dict(self.runtime_pass()[workflow_id])[runtime_module.DELIVERY_LABEL]
                self.assertFalse(outcome.ok, outcome)
                self.assertEqual(outcome.outcome, "delivery_held", outcome)
            status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                         {"mission_id": mission_id})
            self.assertEqual(self.delivery_transport.performed, performed)
            self.assertEqual(self.git(workflow_id, "rev-parse", "HEAD"), lease_head)
            self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        finally:
            recover()
        if before is not None:
            self.assertEqual(self.effects(), before)
        # The ONLY Mission write while the delivery store was unusable is
        # the Runtime's routine reconciliation of the delivery decision
        # recorded just before (identical for all three conditions): one
        # ``reconcile`` operation, an OBSERVATION — nothing minted,
        # started, moved or closed.
        earlier, now = json.loads(document), self.mission_document()
        was, is_ = earlier["mission_state"][mission_id], now["mission_state"][mission_id]
        self.assertEqual([op["kind"] for op in
                          is_["applied_operations"][len(was["applied_operations"]):]],
                         [mission_state.OPERATION_RECONCILE])
        self.assertEqual(len(is_["reconciliations"]), len(was["reconciliations"]) + 1)
        added = [key for key in now["reservations"] if key not in earlier["reservations"]]
        self.assertEqual([now["reservations"][key]["kind"] for key in added],
                         [mission_store_module.RESERVATION_KIND_STATE_OPERATION] * 2)
        self.assertLessEqual(sum(1 for key in added
                                 if now["reservations"][key]["consumed_by"] is not None), 1)
        self.assertEqual(sorted(set(path.split("/")[1] for path in self.changed_paths(
            earlier, now))), ["mission_state", "reservations"])
        self.assertEqual(sorted(set(path.split("/")[3] for path in self.changed_paths(
            earlier, now) if path.startswith("/mission_state/"))),
            ["applied_operations[len %d->%d]" % (len(was["applied_operations"]),
                                                 len(is_["applied_operations"])),
             "reconciliations[len %d->%d]" % (len(was["reconciliations"]),
                                              len(is_["reconciliations"])),
             "sequence", "snapshot"])
        self.assertNotEqual(self.gate.service.get_state(mission_id)["progress"],
                            mission_state.PROGRESS_COMPLETED)
        # Recovery: delivered exactly once.
        self.runtime_pass()
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        self.assertEqual(self.gate.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_COMPLETED)
        return outcome, status

    def test_E_delivery_store_corrupt(self):
        outcome, status = self.delivery_store_while("corrupt")
        self.assertIsNotNone(outcome.problem)

    def test_E_delivery_store_unreadable(self):
        outcome, status = self.delivery_store_while("unreadable")
        self.assertIsNotNone(outcome.problem)

    def test_E_delivery_store_saturated(self):
        outcome, status = self.delivery_store_while("saturated")
        self.assertIsNotNone(outcome.problem)

    def test_E_the_mission_store_lost_mid_drive_holds_the_next_effect_never_revokes(self):
        """The Mission store becomes unreadable right AFTER the commit
        effect: the push's effect admission (the parent check) HOLDS —
        reversible, the delivery record is never revoked — and once the
        store answers the same record delivers exactly once."""
        mission_id, revision, workflow_id = self.completed()
        self.runtime_pass()
        request, decided, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, decided)
        path = self.store_path("mission")
        self.addCleanup(os.chmod, path, 0o600)

        def lose(when):
            if when == "after":
                os.chmod(path, 0)
        self.delivery_transport.hooks["commit_step"] = lose
        outcome = dict(self.runtime_pass()[workflow_id])[runtime_module.DELIVERY_LABEL]
        self.delivery_transport.hooks.pop("commit_step")
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 0, 0))
        self.assertEqual((outcome.ok, outcome.outcome, outcome.problem),
                         (False, "delivery_held", delivery_module.PROBLEM_SOURCE), outcome)
        record = list(self.deliveries().values())[0]
        self.assertEqual((record["phase"], record["revocation"]["revoked"],
                          record["revocation"]["reason"]), ("COMMITTED", False, None))
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        os.chmod(path, 0o600)
        self.runtime_pass()
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        self.assertEqual((status["delivery"]["phase"], status["delivery"]["revoked"]),
                         ("COMPLETE", False))

    # -- status ------------------------------------------------------------------

    def test_E_status_reports_unavailable_never_absent_and_reads_through_saturation(self):
        mission_id, revision, workflow_id = self.dispatched()
        for condition in self.CONDITIONS:
            stores = None if condition == "unreadable" else self.store_bytes()
            recover = self.make_unavailable(condition)
            try:
                if condition == "corrupt":
                    stores = self.store_bytes()
                status, is_error = self.grok_status(mission_id)
                delivery, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                               {"mission_id": mission_id})
                if condition == "saturated":
                    # A full store is still READABLE: status is exact.
                    self.assertEqual(status["canonical"]["availability"], "present")
                    self.assertEqual(status["canonical"]["progress"],
                                     mission_state.PROGRESS_IN_PROGRESS)
                else:
                    self.assertEqual(status["canonical"]["availability"], "unavailable",
                                     (condition, status))
                    self.assertTrue(status["canonical"]["problem"].startswith(
                        "MissionStoreError: "), status["canonical"]["problem"])
                    self.assertNotEqual(delivery.get("problem"), "mission_unknown")
                    self.assertTrue(delivery["problem"] in (
                        delivery_module.PROBLEM_SOURCE,
                        mission_store_module.PROBLEM_STORE_UNREADABLE), delivery)
                if condition != "unreadable":
                    self.assertEqual(self.store_bytes(), stores)
            finally:
                recover()
        status, _ = self.grok_status(mission_id)
        self.assertEqual(status["canonical"]["availability"], "present")


class ItemEBoundTests(unittest.TestCase):
    """The capacity admission's bound and arithmetic, pinned exactly."""

    def test_E_the_effect_reserve_and_the_ordinary_headroom(self):
        self.assertEqual(gate_module.EFFECT_OPERATION_RESERVE, 8)
        self.assertLess(gate_module.EFFECT_OPERATION_RESERVE,
                        mission_state.CONTROL_OPERATION_HEADROOM)
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            service = mission_service_module.MissionService(
                mission_store_module.MissionStore(directory), lambda: 1000000)
            kind = mission_store_module.RESERVATION_KIND_STATE_OPERATION
            cap = mission_store_module.RESERVATION_CAPS[kind]
            self.assertEqual(service.ordinary_operation_headroom(), cap - 16)
            context = mission_record.AuthenticatedContext(
                transport="local",
                principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
                principal_ref="uid:501")
            for _ in range(3):
                service.mint_state_operation_id(context)
            self.assertEqual(service.ordinary_operation_headroom(), cap - 16 - 3)
            with mock.patch.dict(mission_store_module.RESERVATION_CAPS, {kind: 3 + 16}):
                self.assertEqual(service.ordinary_operation_headroom(), 0)
                with self.assertRaises(mission_store_module.MissionStoreError) as caught:
                    service.mint_state_operation_id(context)
                self.assertEqual(caught.exception.problem,
                                 mission_store_module.PROBLEM_STORE_FULL)
            # Inside the control headroom: never negative.
            with mock.patch.dict(mission_store_module.RESERVATION_CAPS, {kind: 10}):
                self.assertEqual(service.ordinary_operation_headroom(), 0)
            # Beyond the bound the load itself refuses, typed, as every load.
            with mock.patch.dict(mission_store_module.RESERVATION_CAPS, {kind: 2}):
                with self.assertRaises(mission_store_module.MissionStoreError) as caught:
                    service.ordinary_operation_headroom()
                self.assertEqual(caught.exception.problem,
                                 mission_store_module.PROBLEM_STORE_FULL)


# ======================================================================
# Lead gate F-S7-2 / F-S7-3 — the readiness probe: the Runtime-owned lock
# name, and readiness only from OBSERVED contention
# ======================================================================


def _flock_raising(number):
    """A ``fcntl`` stand-in for the readiness module whose ``flock`` fails
    with ``OSError(number)`` (Python maps EAGAIN/EWOULDBLOCK to
    ``BlockingIOError`` by itself)."""
    def flock(descriptor, operation):
        raise OSError(number, os.strerror(number))
    return types.SimpleNamespace(flock=flock, LOCK_EX=fcntl.LOCK_EX,
                                 LOCK_NB=fcntl.LOCK_NB, LOCK_UN=fcntl.LOCK_UN)


class ReadinessProbeTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def test_F2_the_lock_name_is_pinned_equal_to_the_runtime_owned_constant(self):
        self.assertEqual(readiness_module.RUNTIME_LOCK_FILE_NAME,
                         runtime_state.RUNTIME_LOCK_FILE_NAME)

    def test_F2_the_probe_observes_the_lock_the_runtime_itself_takes(self):
        self.assertEqual(readiness_module.probe_runtime(self.directory)[0], False)
        descriptor = runtime_cli.acquire_runtime_lock(self.directory)
        self.assertIsNotNone(descriptor)
        try:
            running, detail = readiness_module.probe_runtime(self.directory)
            self.assertEqual((running, detail),
                             (True, "the engineering Runtime is running (its lock is held)"))
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        running, detail = readiness_module.probe_runtime(self.directory)
        self.assertIs(running, False)
        self.assertIn("is not running", detail)

    def test_F3_only_contention_is_running(self):
        import errno
        lock = os.path.join(self.directory, runtime_state.RUNTIME_LOCK_FILE_NAME)
        with open(lock, "w"):
            pass
        for number in (errno.EWOULDBLOCK, errno.EAGAIN):
            with mock.patch.object(readiness_module, "fcntl", _flock_raising(number)):
                self.assertEqual(readiness_module.probe_runtime(self.directory),
                                 (True, "the engineering Runtime is running (its"
                                        " lock is held)"), number)
        for number in (errno.ENOTSUP, errno.EIO, errno.EINTR, errno.EACCES,
                       errno.EPERM, errno.EBADF):
            with mock.patch.object(readiness_module, "fcntl", _flock_raising(number)):
                running, detail = readiness_module.probe_runtime(self.directory)
            self.assertIsNone(running, number)
            self.assertIn("could not be probed (flock failed: ", detail)
            self.assertIn("errno %d (%s), not contention); readiness is unknown"
                          % (number, os.strerror(number)), detail)

    def test_F3_an_unopenable_lock_is_unknown_not_absent(self):
        lock = os.path.join(self.directory, runtime_state.RUNTIME_LOCK_FILE_NAME)
        with open(lock, "w"):
            pass
        os.chmod(lock, 0)
        self.addCleanup(os.chmod, lock, 0o600)
        running, detail = readiness_module.probe_runtime(self.directory)
        self.assertIsNone(running)
        self.assertIn("could not be probed (open failed: PermissionError errno", detail)

    def test_F3_the_producer_records_ready_only_from_contention(self):
        recorded = []

        class Service(object):
            def mint_state_operation_id(self, context):
                return "mo-" + "0" * 32

            def get_state(self, mission_id):
                return {"sequence": 7}

            def now(self):
                return 1000000

            def observe_resource_readiness(self, *args):
                recorded.append(args[4])
        for answer, status in ((True, mission_state.READINESS_READY),
                               (False, mission_state.READINESS_NOT_READY),
                               (None, mission_state.READINESS_UNKNOWN)):
            producer = readiness_module.RuntimeReadinessProducer(
                Service(), self.directory, probe=lambda _d, a=answer: (a, "probed"))
            self.assertEqual(producer.observe("mn-x", None)["status"], status)
        self.assertEqual(recorded, [mission_state.READINESS_READY,
                                    mission_state.READINESS_NOT_READY,
                                    mission_state.READINESS_UNKNOWN])


class F3ProbeFailureInTheLoopTests(LoopCase):

    def test_F3_a_failing_probe_records_unknown_and_dispatches_nothing(self):
        import errno
        mission_id, _ = self.authorized()
        with mock.patch.object(readiness_module, "fcntl", _flock_raising(errno.EIO)):
            refused, is_error = self.grok_dispatch(mission_id)
        self.assertTrue(is_error)
        self.assertEqual(refused["problem"], "mission_control_readiness_stale")
        latest = readiness_module.latest_observation(
            self.service.get_state(mission_id)["record"])
        self.assertEqual(latest["status"], mission_state.READINESS_UNKNOWN)
        self.assertEqual((self.rows(), len(self.engine.starts), self.capability_entries()),
                         ({}, 0, {}))
        # The probe observes the held lock again: READY, dispatched once.
        structured, is_error = self.grok_dispatch(mission_id)
        self.assertFalse(is_error, structured)
        self.target_task_status = "ACTIVE"
        self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))


# ======================================================================
# Lead gate F-S7-1 — a durably REFUSED start claim with no admitted start
# resumes once its cause clears; a terminal cause stays terminal
# ======================================================================


class F1RefusedClaimResumptionTests(LoopCase):

    def refused_in_the_gap(self, inject=None, patches=None):
        """Authorize and bootstrap; the first pass writes the dispatch
        marker, and the cause lands right before the runtime start claim
        (``inject(mission_id)``) or inside its canonical admission
        (``patches(mission_id)``: context managers active for the pass)."""
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.target_task_status = "ACTIVE"
        self.authority = (self.service.get(mission_id)["live_authorization_id"],
                          len(self.mission_document()["missions"][mission_id]["decisions"]))
        real = broker_module._MissionStartGuard.open
        fired = []

        def open_after(guard, point):
            if not fired and inject is not None:
                fired.append(point)
                inject(mission_id)
            return real(guard, point)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                broker_module._MissionStartGuard, "open", open_after))
            for patch in (patches(mission_id) if patches else ()):
                stack.enter_context(patch)
            self.runtime_pass()
        return mission_id, revision, workflow_id

    @contextlib.contextmanager
    def minted(self):
        """Every capability the Runtime mints inside the block, by action."""
        from target_runtime.capability_authority import RuntimeCapabilityAuthority
        real = RuntimeCapabilityAuthority.mint
        actions = []

        def counting(authority, workflow_id, action, *args, **kwargs):
            actions.append(action)
            return real(authority, workflow_id, action, *args, **kwargs)
        with mock.patch.object(RuntimeCapabilityAuthority, "mint", counting):
            yield actions

    def claim_states(self, workflow_id, head="claim-runtime_start-1"):
        return [r.split(" state=", 1)[1] for r in self.receipts(
            workflow_id, "mission claim %s:" % head)]

    def assert_refused(self, mission_id, workflow_id, cause):
        """Provably refused and unstarted: nothing invoked, the claim
        durably refused naming the cause, no canonical start, one marker."""
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]),
                         (wa_record.PHASE_DISPATCHED, None))
        self.assertEqual(self.claim_states(workflow_id),
                         ["claiming", "claim:refused cause=%s" % cause])
        self.assertTrue(broker_module.refused_claim_resumable(entry))
        self.assertFalse(runtime_module.dispatch_identity_unresolved(entry))
        self.assertEqual(mission_state.engagement_starts_of(
            self.service.get_state(mission_id)["record"]), [])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (0, 0))
        self.assertEqual(len(self.receipts(workflow_id, "dispatched handoff revision")), 1)

    def assert_resumed(self, mission_id, revision, workflow_id):
        """Resumed at the current revision and authorization, with no new
        decision: ONE runtime start and ONE task handover in total (the
        refused bridge request invoked nothing), ONE dispatch marker, one
        canonical start per point, the identity bound, not BLOCKED."""
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]["task_id"]),
                         (wa_record.PHASE_DISPATCHED, "task-started-1"))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))
        self.assertEqual(len(self.spawn_requests), 2)
        self.assertEqual(len(self.receipts(workflow_id, "dispatched handoff revision")), 1)
        starts = mission_state.engagement_starts_of(
            self.service.get_state(mission_id)["record"])
        self.assertEqual(sorted(s["point"] for s in starts), ["runtime", "task"])
        self.assertEqual(len(self.engagements(mission_id)), 1)
        stored = self.service.get(mission_id)
        self.assertEqual((stored["record"]["current_revision"], stored["live_authorization_id"],
                          len(self.mission_document()["missions"][mission_id]["decisions"])),
                         (revision,) + self.authority)
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def frozen_effects(self, workflow_id):
        return (len(self.engine.starts), len(self.engine.tasks), len(self.spawn_requests),
                self.record(workflow_id)["phase"], self.record(workflow_id)["target_engine"])

    # -- reversible causes ------------------------------------------------------

    def test_F1_a_lifted_hold_resumes_once_across_reconnect_and_a_fresh_runtime(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_hold(mission_id, "hold in the gap"))
        self.assert_refused(mission_id, workflow_id, gate_module.PROBLEM_HOLD_ACTIVE)
        frozen = self.frozen_effects(workflow_id)
        with self.minted() as actions:
            for _ in range(2):                          # still held: nothing
                self.runtime_pass()
        self.assertEqual((actions, self.frozen_effects(workflow_id)), ([], frozen))
        self.restart_dirun()                            # a fresh Runtime process
        self.restart_grok()
        request, resumed, is_error = self.grok_control(mission_id, revision, "resume",
                                                       client=self.new_client())
        self.assertEqual(resumed["status"], "applied", resumed)
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:2], ("dispatch", True))
        self.assertEqual(self.claim_states(workflow_id), [
            "claiming", "claim:refused cause=%s" % gate_module.PROBLEM_HOLD_ACTIVE,
            "claiming", self.claim_states(workflow_id)[3]])
        self.assertTrue(self.claim_states(workflow_id)[3].startswith("claim:admitted cause=ms-"))
        self.assert_resumed(mission_id, revision, workflow_id)

    def test_F1_a_source_outage_has_zero_effects_then_resumes(self):
        path = os.path.join(self.mission_dir, mission_store_module.MISSIONS_FILE_NAME)
        self.addCleanup(os.chmod, path, 0o600)
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: os.chmod(path, 0))
        frozen = self.frozen_effects(workflow_id)
        receipts = len(self.record(workflow_id)["receipts"])
        with self.minted() as actions:
            for _ in range(2):                          # the source still cannot answer
                self.runtime_pass()
        self.assertEqual((actions, self.frozen_effects(workflow_id)), ([], frozen))
        self.assertEqual(len(self.record(workflow_id)["receipts"]), receipts)
        os.chmod(path, 0o600)
        self.assert_refused(mission_id, workflow_id, gate_module.PROBLEM_SOURCE_UNAVAILABLE)
        self.restart_dirun()
        self.runtime_pass()
        self.assert_resumed(mission_id, revision, workflow_id)

    def test_F1_stale_readiness_resumes_once_it_is_fresh_again(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.clock.advance(900 + 1))
        self.assert_refused(mission_id, workflow_id, gate_module.PROBLEM_READINESS_STALE)
        # The next pass refreshes the Runtime's readiness first, then resumes.
        self.runtime_pass()
        self.assert_resumed(mission_id, revision, workflow_id)

    def test_F1_persisting_stale_readiness_mints_and_claims_nothing(self):
        """While readiness stays stale (the Runtime's lock is not held, so
        every refresh records NOT_READY) the resumption's own spawn-boundary
        admission waits: no capability is minted, no claim is re-attempted."""
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.clock.advance(900 + 1))
        self.release_runtime_lock()
        claims = self.claim_states(workflow_id)
        frozen = self.frozen_effects(workflow_id)
        with self.minted() as actions:
            for _ in range(2):
                self.runtime_pass()
        self.assertEqual((actions, self.frozen_effects(workflow_id),
                          self.claim_states(workflow_id)), ([], frozen, claims))
        self.hold_runtime_lock()                        # the Runtime runs again
        self.runtime_pass()
        self.assert_resumed(mission_id, revision, workflow_id)

    def test_F1_a_stale_sequence_resumes_once_the_sequence_settles(self):
        def concurrent_write(mission_id):
            real = self.service.open_engagement_start
            fired = []

            def open_after_a_concurrent_write(*args, **kwargs):
                if not fired:
                    fired.append(True)
                    self.op("observe_resource_readiness", mission_id,
                            readiness_module.ENGINEERING_RUNTIME_RESOURCE,
                            mission_state.READINESS_READY, self.clock())
                return real(*args, **kwargs)
            return [mock.patch.object(self.service, "open_engagement_start",
                                      open_after_a_concurrent_write)]
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            patches=concurrent_write)
        self.assert_refused(mission_id, workflow_id,
                            mission_state_service_module.PROBLEM_STALE_SEQUENCE)
        self.runtime_pass()
        self.assert_resumed(mission_id, revision, workflow_id)

    def test_F1_a_refused_resumption_is_resumed_again_never_twice(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_hold(mission_id, "first hold"))
        self.grok_control(mission_id, revision, "resume")
        real = broker_module._MissionStartGuard.open
        fired = []

        def open_after_hold(guard, point):
            if not fired:
                fired.append(point)
                self.service.request_hold(mission_id, "second hold")
            return real(guard, point)
        with mock.patch.object(broker_module._MissionStartGuard, "open", open_after_hold):
            self.runtime_pass()
        # Refused again at the resumed claim: still nothing invoked.
        self.assertEqual(self.claim_states(workflow_id)[-1],
                         "claim:refused cause=%s" % gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (0, 0))
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assertEqual(len(self.spawn_requests), 3)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"],
                         "task-started-1")

    # -- terminal causes stay terminal ----------------------------------------------

    def assert_terminal(self, mission_id, workflow_id, problem):
        for _ in range(2):
            self.runtime_pass()
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertFalse(broker_module.refused_claim_resumable(entry))
        self.assertTrue(any(problem in r for r in self.receipts(
            workflow_id, "mission gate block: ")), self.receipts(workflow_id, ""))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (0, 0))
        self.assertEqual(mission_state.engagement_starts_of(
            self.service.get_state(mission_id)["record"]), [])

    def test_F1_a_cancel_in_the_gap_is_terminal_at_the_claim(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_cancel(mission_id, "cancel in the gap"))
        self.assertEqual(self.claim_states(workflow_id)[-1],
                         "claim:refused cause=%s" % gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assert_terminal(mission_id, workflow_id, gate_module.PROBLEM_CANCEL_REQUESTED)

    def test_F1_a_cancel_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_hold(mission_id, "hold"))
        self.service.request_cancel(mission_id, "cancelled while held")
        self.assert_terminal(mission_id, workflow_id, gate_module.PROBLEM_CANCEL_REQUESTED)

    def test_F1_an_edit_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_hold(mission_id, "hold"))
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed while held"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.assert_terminal(mission_id, workflow_id, gate_module.PROBLEM_REVISION_SUPERSEDED)

    def test_F1_an_expiry_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id = self.refused_in_the_gap(
            lambda mission_id: self.service.request_hold(mission_id, "hold"))
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.assert_terminal(mission_id, workflow_id, "mission_authorization_expired")

    # -- only a durably refused claim resumes (absence is not proof) --------------

    def test_F1_a_crash_in_the_claiming_window_is_never_replayed(self):
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.target_task_status = "ACTIVE"

        def dies(entry, dispatch_sequence, point, owner_ref):
            raise Crash("after the claiming receipt, before the canonical open")
        with mock.patch.object(self.gate, "open_start", dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(self.claim_states(workflow_id), ["claiming"])
        self.assertFalse(broker_module.refused_claim_resumable(self.record(workflow_id)))
        self.restart_dirun()
        for _ in range(3):
            self.runtime_pass()
        # The owner resolved the claim from the canonical ABSENCE of a
        # start — which is never a replay authorization: nothing resumed.
        states = self.claim_states(workflow_id)
        self.assertEqual(states[:2], ["claiming", states[1]])
        self.assertTrue(states[1].startswith("claim:unadmitted"), states)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (0, 0))


# ======================================================================
# S7 correction 2 (R1) — a refused TASK handover after the runtime started
# resumes on that same runtime; (R2) a refused FOLLOW-UP resumes its own
# reserved ordinal. Positive evidence only; terminal causes stay terminal.
# ======================================================================


class _ResumptionCase(LoopCase):

    def hold_at(self, mission_id, sequence, point, reason="hold"):
        """A hold committed immediately before the canonical claim of
        dispatch ``sequence``'s ``point`` (once): a context manager."""
        real = broker_module._MissionStartGuard.open
        fired = []

        def open_after_hold(guard, at):
            if not fired and guard.dispatch_sequence == sequence and at == point:
                fired.append(at)
                self.service.request_hold(mission_id, reason)
            return real(guard, at)
        return mock.patch.object(broker_module._MissionStartGuard, "open",
                                 open_after_hold)

    @contextlib.contextmanager
    def minted(self):
        from target_runtime.capability_authority import RuntimeCapabilityAuthority
        real = RuntimeCapabilityAuthority.mint
        actions = []

        def counting(authority, workflow_id, action, *args, **kwargs):
            actions.append(action)
            return real(authority, workflow_id, action, *args, **kwargs)
        with mock.patch.object(RuntimeCapabilityAuthority, "mint", counting):
            yield actions

    def claim_states(self, workflow_id, head):
        return [r.split(" state=", 1)[1] for r in self.receipts(
            workflow_id, "mission claim %s:" % head)]

    def canonical_starts(self, mission_id):
        return sorted((s["engagement_sequence"], s["point"],
                       (s["settlement"] or {}).get("outcome"))
                      for s in self.starts(mission_id))

    def engine_counts(self):
        """(initial runtime starts, initial tasks, follow-up tasks) — the
        follow-up objective is the corrective brief."""
        follow_up = sum(1 for text in self.engine.tasks if "CORRECTIVE FOLLOW-UP" in text)
        return len(self.engine.starts), len(self.engine.tasks) - follow_up, follow_up

    def markers(self, workflow_id):
        return len(self.receipts(workflow_id, "dispatched handoff revision"))

    def authority(self, mission_id):
        stored = self.service.get(mission_id)
        return (stored["record"]["current_revision"], stored["live_authorization_id"],
                len(self.mission_document()["missions"][mission_id]["decisions"]))

    def read_status(self, mission_id):
        """di_mission_status answers and is read-only: no store byte, engine
        call or close moves."""
        stores = self.store_bytes()
        engine = (list(self.engine.starts), list(self.engine.tasks),
                  list(self.engine.close_calls))
        status, is_error = self.grok_status(mission_id)
        self.assertFalse(is_error, status)
        self.assertEqual(self.store_bytes(), stores)
        self.assertEqual((list(self.engine.starts), list(self.engine.tasks),
                          list(self.engine.close_calls)), engine)
        return status

    def status_view(self, mission_id):
        status = self.read_status(mission_id)
        row = status["workflows"]["rows"][0]
        canonical = status["canonical"]
        ordinal = dict((r["engagement_id"], r["engagement_sequence"])
                       for r in canonical["engagements"]["reservations"])
        return (row["phase"], row["task_id"],
                canonical["controls"]["hold"]["lifted_at"] is None,
                sorted((ordinal[s["engagement_id"]], s["point"])
                       for s in canonical["engagements"]["starts"]))

    def revoked(self, authorization_id):
        return self.mission_document()["authorizations"][authorization_id][
            "revocation"]["revoked"]

    def children_file(self):
        return os.path.join(self.control, ".herd", "state", "children.json")

    def cleanup_receipts_of(self, workflow_id):
        """FINAL CLEANUP's durable receipts (the release) — distinct from a
        follow-up's lifetime RETIREMENT receipts."""
        return self.receipts(workflow_id, broker_module.CLEANUP_RECEIPT_MARKER)

    def retirement_receipts(self, workflow_id):
        return self.receipts(workflow_id, broker_module.RETIREMENT_RECEIPT_MARKER)

    def real_child_records(self, prefix=0):
        """Production composition for the ordinary spawn (with ``prefix``:
        that many UNRELATED spawn records — other workflows' leases, the real
        writer's shape — already persisted before this workflow's): the control
        repository is an initialized, running parent Herdr (its config and
        runtime files exist); the spawn writes its child record through the
        REAL ``HerdrControlPlane.spawn_child`` — only the engine's runtime
        start and task hand-over are doubles, as everywhere in this fixture
        (``spawn`` is reduced to exactly those two calls: no target
        initialization) — and the Broker reads it through the PRODUCTION
        projection ``observe_spawn_records``."""
        parent = HerdrControlPlane().instance(self.control)
        os.makedirs(str(parent.herd_root / "state"))
        for path in (parent.config_path, parent.herd_root / "state" / "runtime.json"):
            with open(str(path), "w") as handle:
                handle.write("{}\n")
        self.assertTrue(parent.initialized)
        if prefix:
            history = [{
                "requested_at": 1000 + n, "parent_repo": self.control,
                "parent_task_id": None, "dependency": False,
                "repo": os.path.join(os.path.realpath(self.workspaces), "wf-m-old-%03d" % n),
                "task_id": "20260901-0000%02d-%06x" % (n % 60, n), "task_status": "COMPLETE",
                "workspace_id": "ws-old-%d" % n,
                "agents": {"supervisor": "old-sup-%d" % n, "lead": "old-lead-%d" % n},
            } for n in range(prefix)]
            with open(self.children_file(), "w") as handle:
                handle.write(json.dumps({"version": 1, "children": history}, indent=2) + "\n")

        def spawn(plane, repo, *, task, **kwargs):
            runtime = plane.start(repo)
            return {"repo": str(repo), "initialization": None, "runtime": runtime,
                    "task": plane.dispatch_task(repo, task), "policy": {}}

        def spawn_child(plane, parent_repo, target_repo, **kwargs):
            with mock.patch.object(HerdrControlPlane, "spawn", spawn):
                return REAL_SPAWN_CHILD(plane, parent_repo, target_repo, **kwargs)
        self.engine.spawn_child = spawn_child
        self.spawn_records = broker_module._production_spawn_records_observer
        self.broker._spawn_records = self.spawn_records

    def refused_at_the_task_claim(self):
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.target_task_status = "ACTIVE"
        authority = self.authority(mission_id)
        with self.hold_at(mission_id, 1, "task_dispatch", "hold before the handover"):
            self.runtime_pass()
        entry = self.record(workflow_id)
        # The refusal: the runtime started and settled, the objective was
        # never handed over, positive refused fact latest.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.engine.live), self.engine.close_calls), (1, 0, 1, []))
        self.assertEqual(self.claim_states(workflow_id, "claim-task_dispatch-1"),
                         ["claiming", "claim:refused cause=mission_control_hold_active"])
        self.assertEqual(self.canonical_starts(mission_id), [(1, "runtime", "completed")])
        self.assertEqual((entry["phase"], entry["target_engine"]),
                         (wa_record.PHASE_DISPATCHED, None))
        # Status is truthful (no task handed over, the hold standing) and
        # read-only.
        self.assertEqual(self.status_view(mission_id),
                         (wa_record.PHASE_DISPATCHED, None, True, [(1, "runtime")]))
        return mission_id, revision, workflow_id, authority

    def refused_follow_up(self, **overrides):
        """The REAL path: dispatched, the engine finishes, the verification
        turn requests a corrective follow-up, the Runtime reserves and marks
        follow-up 2 — and a hold lands before its runtime-start claim. The
        spawns write their REAL child records (the follow-up's retirement of
        the earlier runtime reads them, Task 8 startup correction)."""
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched(**overrides)
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        authority = self.authority(mission_id)
        with self.hold_at(mission_id, 2, "runtime_start", "hold before the follow-up"):
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual([o[:2] for o in outcomes[workflow_id][:2]],
                         [("verify", True), ("dispatch_follow_up", False)])
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="the corrected mission is verified")
        # (The prior task stays COMPLETE until the follow-up hands over its
        # own, which then starts ACTIVE — ``note_hand_over``.)
        entry = self.record(workflow_id)
        # Refused: the follow-up reserved and marked, nothing invoked for it —
        # and the earlier runtime NOT retired: the hold landed first, so the
        # retirement's admission refused before any close.
        self.assertEqual(self.engine_counts(), (1, 1, 0))
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(self.retirement_receipts(workflow_id), [])     # no claim written
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        self.assertEqual([e["kind"] for e in self.engagements(mission_id)],
                         ["initial", "follow_up"])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"),
                         ["claiming", "claim:retiring",
                          "claim:refused cause=mission_control_hold_active"])
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        budget = self.service.get_state(mission_id)["budget"]
        self.assertEqual(budget["attempts_consumed"], 1)
        self.assertEqual(self.status_view(mission_id),
                         (wa_record.PHASE_DISPATCHED, "task-started-1", True,
                          [(1, "runtime"), (1, "task")]))
        return mission_id, revision, workflow_id, authority, budget


class R1TaskHandoverResumptionTests(_ResumptionCase):

    def assert_handed_over_once(self, mission_id, revision, workflow_id, authority):
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]["task_id"]),
                         (wa_record.PHASE_DISPATCHED, "task-started-1"))
        # Starts stay 1, tasks go 0 -> 1; one bridge request (the refused
        # handover was part of it), one marker, one engagement.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        self.assertEqual(self.engine.tasks, [self.record(workflow_id)["handoff"]["text"]])
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))),
                         (1, 1))
        self.assertEqual(self.canonical_starts(mission_id),
                         [(1, "runtime", "completed"), (1, "task", "completed")])
        self.assertEqual(self.authority(mission_id), authority)
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-1")[-1][:14],
                         "claim:admitted")
        self.assertEqual(self.status_view(mission_id),
                         (wa_record.PHASE_DISPATCHED, "task-started-1", False,
                          [(1, "runtime"), (1, "task")]))
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R1_the_refused_handover_is_the_positively_resumable_point(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        entry = self.record(workflow_id)
        self.assertEqual(broker_module.refused_claim_resumption(entry),
                         ("task_dispatch", 1))
        self.assertTrue(broker_module.refused_claim_resumable(entry))
        # Resumable, so never the unresolved-identity recovery (which would
        # stop the runtime and block the Mission).
        self.assertFalse(runtime_module.dispatch_identity_unresolved(entry))

    def test_R1_a_lifted_hold_hands_the_objective_to_the_same_runtime_once(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        with self.minted() as actions:
            for _ in range(2):                          # still held: nothing
                self.runtime_pass()
        self.assertEqual((actions, len(self.engine.starts), len(self.engine.tasks)),
                         ([], 1, 0))
        self.restart_dirun()                            # a fresh Runtime process
        self.restart_grok()
        request, resumed, is_error = self.grok_control(mission_id, revision, "resume",
                                                       client=self.new_client())
        self.assertEqual(resumed["status"], "applied", resumed)
        with self.minted() as actions:
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(actions[:1], [broker_module.ACTION_DISPATCH])
        self.assertEqual(outcomes[workflow_id][0][:2], ("dispatch", True))
        states = self.claim_states(workflow_id, "claim-task_dispatch-1")
        self.assertEqual([state.split(" ")[0] for state in states],
                         ["claiming", "claim:refused", "claiming", "claim:admitted"])
        self.assertTrue(states[3].startswith("claim:admitted cause=ms-"), states)
        self.assert_handed_over_once(mission_id, revision, workflow_id, authority)
        self.restart_dirun()
        self.runtime_pass()
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R1_a_repeated_refusal_retries_only_from_a_fresh_refused_fact(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        with self.hold_at(mission_id, 1, "task_dispatch", "second hold"):
            self.runtime_pass()
        # A FRESH claim, refused again: the next retry rests on this new
        # positive fact, never on the first.
        self.assertEqual(self.claim_states(workflow_id, "claim-task_dispatch-1"), [
            "claiming", "claim:refused cause=mission_control_hold_active",
            "claiming", "claim:refused cause=mission_control_hold_active"])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assert_handed_over_once(mission_id, revision, workflow_id, authority)

    def assert_terminal(self, mission_id, workflow_id, problem):
        for _ in range(2):
            self.runtime_pass()
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(broker_module.refused_claim_resumption(entry))
        self.assertTrue(any(problem in r for r in self.receipts(workflow_id, "")),
                        problem)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        self.assertEqual(self.canonical_starts(mission_id), [(1, "runtime", "completed")])

    def test_R1_a_cancel_while_waiting_stays_terminal_and_stops_the_owned_runtime(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.service.request_cancel(mission_id, "cancelled while held")
        self.assert_terminal(mission_id, workflow_id, gate_module.PROBLEM_CANCEL_REQUESTED)
        # The cancel owes the settled runtime's stop: the owned stop ran and
        # only the observed absence confirms it.
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))

    def test_R1_an_edit_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed while held"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        # The EDIT is the product's revocation: it revokes the bound
        # authorization (``superseded_by_edit``, the only revocation reason).
        self.assertTrue(self.revoked(authority[1]))
        self.assert_terminal(mission_id, workflow_id, gate_module.PROBLEM_REVISION_SUPERSEDED)
        # The EDIT records the stop obligation on the settled start; the
        # owner's recovery performed the owned stop, confirmed by absence.
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))

    def test_R1_an_expiry_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.assert_terminal(mission_id, workflow_id, "mission_authorization_expired")
        # STATED (pre-existing Core semantics): expiry records no stop on a
        # settled start; the idle un-tasked runtime is left to the release
        # cleanup and is never handed the objective.
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        self.assertFalse(any(mission_state.start_stop_required(s)
                             for s in self.starts(mission_id)))

    def test_R1_a_sticky_stop_obligation_holds_until_absence_is_observed(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.engine.close_error = OSError("the close was refused")
        self.service.request_cancel(mission_id, "cancelled while held")
        for _ in range(2):
            self.runtime_pass()
        # The owed stop failed: it stays required (sticky), nothing is
        # handed over, the runtime is still visible.
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.engine.live)), (1, 0, 1))
        self.assertTrue(self.engine.close_calls)
        self.assertTrue(all(mission_state.start_stop_required(s)
                            and not mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        self.engine.close_error = None
        self.runtime_pass()
        self.assertEqual((len(self.engine.tasks), self.engine.live), (0, []))
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))

    def test_R1_an_ambiguous_handover_is_terminal_cleaned_up_and_never_replayed(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")

        def dies_mid_handover():
            raise RuntimeError("the handover transport died mid-call")
        self.engine.before_task = dies_mid_handover
        outcomes = outcome_view(self.runtime_pass())
        self.engine.before_task = None
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("dispatch", False, broker_module.PROBLEM_SPAWN_FAILED))
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(broker_module.refused_claim_resumption(self.record(workflow_id)))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 0, 1))
        self.assertEqual(self.canonical_starts(mission_id),
                         [(1, "runtime", "completed"), (1, "task", "failed")])
        # The failed handover's own start owes the stop (the settled runtime
        # start owes none): owned, confirmed by the observed absence.
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(sorted((s["point"], mission_state.start_stop_required(s),
                                 mission_state.start_stop_confirmed(s))
                                for s in self.starts(mission_id)),
                         [("runtime", False, False), ("task", True, True)])

    def test_R1_a_runtime_no_longer_live_is_never_restarted(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.engine.live = []                           # the runtime is gone
        self.grok_control(mission_id, revision, "resume")
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("dispatch", True, broker_module.PROBLEM_RESUME_RUNTIME_UNPROVEN))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 0, 1))
        self.assertEqual(self.canonical_starts(mission_id), [(1, "runtime", "completed")])

    def test_R1_a_runtime_with_foreign_agents_is_never_handed_the_objective(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.engine.live = [{"workspace_id": "ws-started-1", "agent_names": ["someone-else"]}]
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertTrue(any("workspace_agents_do_not_match" in r for r in self.receipts(
            workflow_id, "recovery blocked: %s" % broker_module.PROBLEM_RESUME_RUNTIME_UNPROVEN)))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          self.engine.close_calls), (1, 0, []))

    def test_R1_an_unreadable_listing_hands_nothing_over_and_writes_nothing(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        self.engine.live_error = OSError("listing unavailable")
        receipts = len(self.record(workflow_id)["receipts"])
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("dispatch", False, broker_module.PROBLEM_RESUME_RUNTIME_UNPROVEN))
        self.assertEqual((self.record(workflow_id)["phase"],
                          len(self.record(workflow_id)["receipts"])),
                         (wa_record.PHASE_DISPATCHED, receipts))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        self.engine.live_error = None                   # the listing answers again
        self.runtime_pass()
        self.assert_handed_over_once(mission_id, revision, workflow_id, authority)

    def test_R1_a_crash_in_the_task_claiming_window_is_never_replayed(self):
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.target_task_status = "ACTIVE"
        real = self.gate.open_start

        def dies_at_the_task_claim(entry, dispatch_sequence, point, owner_ref):
            if point == "task":
                raise Crash("after the task claiming receipt, before the canonical open")
            return real(entry, dispatch_sequence, point, owner_ref)
        with mock.patch.object(self.gate, "open_start", dies_at_the_task_claim):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(self.claim_states(workflow_id, "claim-task_dispatch-1"), ["claiming"])
        self.restart_dirun()
        for _ in range(3):
            self.runtime_pass()
        # Never replayed: the claiming window resolves from the canonical
        # absence (unadmitted), nothing is handed over.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        states = self.claim_states(workflow_id, "claim-task_dispatch-1")
        self.assertTrue(states[1].startswith("claim:unadmitted"), states)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(broker_module.refused_claim_resumption(self.record(workflow_id)))


class R2FollowUpResumptionTests(_ResumptionCase):

    def assert_follow_up_resumed_once(self, mission_id, workflow_id, authority, budget):
        # Initial (1 start, 1 task) and follow-up (1 start, 1 task) counted
        # separately; the SAME ordinal 2, no third marker or reservation, no
        # extra charge.
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        self.assertEqual(self.service.get_state(mission_id)["budget"], budget)
        self.assertEqual(self.canonical_starts(mission_id), [
            (1, "runtime", "completed"), (1, "task", "completed"),
            (2, "runtime", "completed"), (2, "task", "completed")])
        follow_up_task = [t for t in self.engine.tasks if "CORRECTIVE FOLLOW-UP" in t][0]
        self.assertIn("acceptance criterion 3 not met", follow_up_task)
        self.assertEqual(self.authority(mission_id), authority)
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"],
                         "task-started-1")
        # ADDED effect, pinned separately (Task 8 startup correction): the
        # follow-up's RETIREMENT closed the earlier runtime exactly once
        # before its start — a lifetime close, not cleanup (no release ran).
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.cleanup_receipts_of(workflow_id), [])
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual(self.markers(workflow_id), 2)
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])

    def test_R2_the_refused_follow_up_is_the_positively_resumable_point(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        entry = self.record(workflow_id)
        self.assertEqual(broker_module.refused_claim_resumption(entry), ("runtime_start", 2))
        self.assertIsNone(broker_module.follow_up_objective_drift(entry, 2))

    def test_R2_a_lifted_hold_resumes_the_same_follow_up_across_restarts(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        with self.minted() as actions:
            for _ in range(2):
                self.runtime_pass()
        self.assertEqual((actions, self.engine_counts()), ([], (1, 1, 0)))
        self.restart_dirun()
        self.restart_grok()
        request, resumed, is_error = self.grok_control(mission_id, revision, "resume",
                                                       client=self.new_client())
        self.assertEqual(resumed["status"], "applied", resumed)
        with self.minted() as actions:
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(actions[:1], [broker_module.ACTION_FOLLOW_UP])
        self.assertEqual(outcomes[workflow_id][0][:2], ("dispatch_follow_up", True))
        self.assert_follow_up_resumed_once(mission_id, workflow_id, authority, budget)
        self.restart_dirun()
        self.runtime_pass()
        self.assertEqual(self.engine_counts(), (2, 1, 1))

    def test_R2_a_follow_up_refused_at_its_task_claim_hands_over_on_its_own_runtime(self):
        """R1 within R2: follow-up 2's runtime started and settled, the hold
        lands at its TASK claim; the lift hands the corrective objective to
        THAT runtime once (no third runtime start, no new marker)."""
        # The spawn writes its REAL child record (the follow-up's retirement
        # of the earlier runtime reads it, Task 8 startup correction).
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        authority = self.authority(mission_id)
        with self.hold_at(mission_id, 2, "task_dispatch", "hold before the corrective task"):
            self.runtime_pass()
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="the corrected mission is verified")
        self.target_task_status = "ACTIVE"
        self.assertEqual(self.engine_counts(), (2, 1, 0))
        self.assertEqual(self.claim_states(workflow_id, "claim-task_dispatch-2"),
                         ["claiming", "claim:refused cause=mission_control_hold_active"])
        self.assertEqual(broker_module.refused_claim_resumption(self.record(workflow_id)),
                         ("task_dispatch", 2))
        budget = self.service.get_state(mission_id)["budget"]
        # ADDED effect, pinned separately: the follow-up's RETIREMENT closed
        # the earlier runtime once before its start, so ONE runtime is live
        # (this fixture engine re-lists the reused id) — the listing the
        # fixture previously had to trim by hand.
        self.assertEqual((self.engine.close_calls, len(self.engine.live)),
                         (["ws-started-1"], 1))
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[:3],
                         ["claiming", "claim:retiring", "claiming"])
        self.grok_control(mission_id, revision, "resume")
        with self.minted() as actions:
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(actions[:1], [broker_module.ACTION_FOLLOW_UP])
        self.assertEqual(outcomes[workflow_id][0][:2], ("dispatch_follow_up", True))
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[-1][:14],
                         "claim:admitted")
        self.assert_follow_up_resumed_once(mission_id, workflow_id, authority, budget)

    def test_R2_a_repeated_refusal_retries_only_from_a_fresh_refused_fact(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        self.grok_control(mission_id, revision, "resume")
        with self.hold_at(mission_id, 2, "runtime_start", "second hold"):
            self.runtime_pass()
        # Each refusal came from a FRESH claim; each claim retired first and
        # its admission refused at the close boundary (the added state).
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"), [
            "claiming", "claim:retiring", "claim:refused cause=mission_control_hold_active",
            "claiming", "claim:retiring", "claim:refused cause=mission_control_hold_active"])
        self.assertEqual((self.engine_counts(), self.markers(workflow_id)), ((1, 1, 0), 2))
        self.assertEqual(self.engine.close_calls, [])
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assert_follow_up_resumed_once(mission_id, workflow_id, authority, budget)

    def assert_follow_up_terminal(self, mission_id, workflow_id, problem, budget):
        for _ in range(2):
            self.runtime_pass()
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(broker_module.refused_claim_resumption(entry))
        self.assertTrue(any(problem in r for r in self.receipts(workflow_id, "")), problem)
        self.assertEqual(self.engine_counts()[2], 0)
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        # The ledger count (the projected budget is absent once the contract
        # is unbound): no refund, no second charge.
        self.assertEqual(progress_module.consumed_attempts(
            self.service.get_state(mission_id)["record"]), budget["attempts_consumed"])

    def test_R2_a_cancel_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        self.service.request_cancel(mission_id, "cancelled while held")
        self.assert_follow_up_terminal(mission_id, workflow_id,
                                       gate_module.PROBLEM_CANCEL_REQUESTED, budget)

    def test_R2_an_edit_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed while held"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.assertTrue(self.revoked(authority[1]))
        self.assert_follow_up_terminal(mission_id, workflow_id,
                                       gate_module.PROBLEM_REVISION_SUPERSEDED, budget)

    def test_R2_an_expiry_while_waiting_stays_terminal(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.assert_follow_up_terminal(mission_id, workflow_id,
                                       "mission_authorization_expired", budget)

    def test_R2_the_paid_follow_up_resumes_and_an_exhausted_budget_admits_no_third(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up(
            proof_contract=loop_contract(
                continuation_budget={"max_attempts": 1, "max_checkpoints": 8}))
        self.assertEqual((budget["attempts_consumed"], budget["attempts_remaining"]), (1, 0))
        # The reservation already paid for the follow-up: resuming it is no
        # new attempt, so the exhausted budget neither refunds nor drops it.
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assert_follow_up_resumed_once(mission_id, workflow_id, authority, budget)
        # The follow-up finishes and verification asks for a THIRD attempt:
        # refused before any reservation, marker or invocation.
        self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE"), (2, "APPROVE")])
        self.target_task_status = "COMPLETE"
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu3", "role": "verification", "process_id": 3},
            detail="still not met")
        for _ in range(2):
            outcomes = outcome_view(self.runtime_pass())
            self.assertEqual(outcomes[workflow_id][1][:3],
                             ("dispatch_follow_up", False,
                              mission_state.PROBLEM_BUDGET_EXHAUSTED))
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        self.assertEqual(progress_module.consumed_attempts(
            self.service.get_state(mission_id)["record"]), 1)

    def test_R2_objective_drift_after_the_marker_fails_closed(self):
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        # A correction receipt recorded AFTER follow-up 2's marker (a fault
        # injected at the workflow store): the reserved objective is no
        # longer provably the one that would be handed over.
        store = wa_store.WorkflowStore(self.store_dir)
        workflows = store.load()
        entry = workflows["workflows"][workflow_id]
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": "evidence", "turn_id": "corr-late0000000", "recorded_at": self.clock(),
            "digest": entry["handoff"]["digest_sha256"],
            "bounded_summary": "correction requested after verification: a later finding"}]
        store.save(workflows)
        self.grok_control(mission_id, revision, "resume")
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3],
                         ("dispatch_follow_up", True,
                          broker_module.PROBLEM_RESUME_OBJECTIVE_DRIFT))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(self.engine_counts(), (1, 1, 0))
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))

    def test_R2_a_crash_in_the_follow_up_claiming_window_is_never_replayed(self):
        # The spawn writes its REAL child record: the retirement that now
        # precedes the canonical claim reads it (Task 8 startup correction).
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        real = self.gate.open_start

        def dies_at_follow_up_claim(entry, dispatch_sequence, point, owner_ref):
            if dispatch_sequence == 2:
                raise Crash("after the follow-up claiming receipt")
            return real(entry, dispatch_sequence, point, owner_ref)
        with mock.patch.object(self.gate, "open_start", dies_at_follow_up_claim):
            with self.assertRaises(Crash):
                self.runtime_pass()
        # The crash is in the CANONICAL claiming window: the retirement
        # completed (its added states and its one close, pinned separately)
        # and re-entered ``claiming`` before the canonical open.
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"),
                         ["claiming", "claim:retiring", "claiming"])
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="verified")
        self.restart_dirun()
        for _ in range(3):
            self.runtime_pass()
        # Never replayed: no follow-up invocation, the same two markers and
        # reservations (resolved from the canonical absence, never a resume).
        self.assertEqual(self.engine_counts()[2], 0)
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        self.assertTrue(self.claim_states(workflow_id, "claim-runtime_start-2")[3]
                        .startswith("claim:unadmitted"))
        self.assertIsNone(broker_module.refused_claim_resumption(self.record(workflow_id)))
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])


# ======================================================================
# S7 correction 3 (S7c-R3) — a RESUMED task handover (R1) whose binding
# save is lost after its canonical settlement is bound ONCE, by a fresh
# Runtime, from that settlement: never a replay, never a guessed child
# record, nothing re-consumed; anything not proven refuses or blocks.
# ======================================================================


class R3SettledHandoverBindingTests(_ResumptionCase):

    @contextlib.contextmanager
    def crash_after_the_task_settlement(self):
        """Window W1: the process dies right AFTER the canonical settlement of
        the task start (completed, with the engine's task id) — before the
        workflow's settled receipt and its binding are saved."""
        real = self.gate.settle_start
        fired = []

        def settles_then_dies(entry, start_id, owner_ref, outcome, identity, reason):
            result = real(entry, start_id, owner_ref, outcome, identity, reason)
            if not fired and result[1] is None and (identity or {}).get("task_id"):
                fired.append(start_id)
                raise Crash("after the canonical task settlement")
            return result
        with mock.patch.object(self.gate, "settle_start", settles_then_dies):
            yield fired

    @contextlib.contextmanager
    def crash_before_the_binding_save(self):
        """Window W2: the handover returned (settled canonically AND on the
        workflow record) and the process dies right before the workflow's
        ``target_engine`` is assigned and saved."""
        fired = []

        def dies(handover, entry, now):
            fired.append(handover["task"]["id"])
            raise Crash("before the target_engine save")
        with mock.patch.object(dispatch_module, "target_identity_from_task", dies):
            yield fired

    def start_states(self, workflow_id, start_id):
        return [r.split(" state=", 1)[1] for r in self.receipts(
            workflow_id, "mission start %s:" % start_id)]

    def linkage_view(self, mission_id, workflow_id):
        """Row 13: everything the recovery must leave exactly as it was —
        ids, revision, authorization, lease, reservation, provenance,
        markers, budget, checkpoint and the canonical starts themselves."""
        entry = self.record(workflow_id)
        state = self.service.get_state(mission_id)
        return {
            "authority": self.authority(mission_id),
            "linkage": (entry[wa_record.MISSION_AUTHORITY_KEY],
                        entry[wa_record.MISSION_ENGAGEMENT_KEY]),
            "lease": entry["workspace_lease"],
            "handoff": entry["handoff"],
            "approval": entry["approval"],
            "markers": self.markers(workflow_id),
            "engagements": self.engagements(mission_id),
            # Request and decision reservations; the Core's per-pass
            # state-operation ids (every pass reconciles) are not linkage.
            "reservations": dict(
                (key, value) for key, value
                in self.mission_document()["reservations"].items()
                if value["kind"] not in mission_store_module.OPERATION_RESERVATION_KINDS),
            "budget": state["budget"],
            "checkpoint": state["latest_checkpoint"],
            "activation": state["contract"]["activation_id"],
            "starts": self.starts(mission_id),
        }

    def crashed(self, window):
        """Refused at the task claim, lifted, and the resumed handover dies in
        ``window``: the objective WAS handed over and the canonical task start
        settled completed with the engine's task id, yet the workflow holds
        no binding."""
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        with window() as fired:
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(len(fired), 1)
        (task_start,) = [s for s in self.starts(mission_id) if s["point"] == "task"]
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests), self.engine.close_calls), (1, 1, 1, []))
        self.assertEqual(self.canonical_starts(mission_id),
                         [(1, "runtime", "completed"), (1, "task", "completed")])
        self.assertEqual(task_start["settlement"]["identity"]["task_id"], "task-started-1")
        self.assertEqual(self.claim_states(workflow_id, "claim-task_dispatch-1")[-1],
                         "claim:admitted cause=%s" % task_start["start_id"])
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]),
                         (wa_record.PHASE_DISPATCHED, None))
        return mission_id, revision, workflow_id, authority, task_start["start_id"]

    def recover(self):
        """A FRESH Runtime (and Grok) process over the same stores, one pass:
        ``(minted actions, outcome view, model turns run)``."""
        self.restart_dirun()
        self.restart_grok()
        turns = len(self.role_turn.calls)
        with self.minted() as actions:
            outcomes = outcome_view(self.runtime_pass())
        return actions, outcomes, len(self.role_turn.calls) - turns

    def assert_bound_once(self, mission_id, workflow_id, start_id, before):
        entry = self.record(workflow_id)
        engine = entry["target_engine"]
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        self.assertIsNotNone(engine, "the binding was not recovered")
        (task_start,) = [s for s in self.starts(mission_id) if s["start_id"] == start_id]
        # The ORIGINAL task id, from the canonical settlement, bound exactly as
        # the uninterrupted handover binds it (alias and resolved target).
        self.assertEqual(engine["task_id"], task_start["settlement"]["identity"]["task_id"])
        self.assertEqual((engine["task_id"], engine["alias"], engine["repo"]), (
            "task-started-1", dispatch_module.ALIAS_PREFIX + workflow_id,
            os.path.realpath(entry["workspace_lease"]["path_realpath"])[:128]))
        # Nothing replayed, spawned, stopped or re-consumed.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests), self.engine.close_calls), (1, 1, 1, []))
        self.assertEqual(len(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER)), 1)
        self.assertEqual(self.receipts(workflow_id, broker_module.RECOVERY_BLOCK_MARKER), [])
        # The record's start evidence agrees with the canonical settlement.
        self.assertEqual(wa_record.unresolved_start_receipts(entry), {})
        latest = self.start_states(workflow_id, start_id)[-1]
        self.assertTrue(latest.startswith("settled:completed") and latest.endswith(" stop=none"),
                        latest)
        # Row 13: every id, the linkage, reservation, marker, budget,
        # checkpoint and the canonical starts are exactly as before the loss.
        self.assertEqual(self.linkage_view(mission_id, workflow_id), before)
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(self.status_view(mission_id),
                         (wa_record.PHASE_DISPATCHED, "task-started-1", False,
                          [(1, "runtime"), (1, "task")]))

    def assert_idempotent(self, mission_id, workflow_id):
        bound = self.record(workflow_id)["target_engine"]
        for _ in range(2):
            self.runtime_pass()
        self.restart_dirun()
        self.runtime_pass()
        self.grok_status(mission_id, client=self.new_client())
        self.assertEqual(self.record(workflow_id)["target_engine"], bound)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        self.assertEqual(len(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER)), 1)

    # -- the recovery ---------------------------------------------------------

    def test_R3_the_lost_binding_is_the_positively_recoverable_shape(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        entry = self.record(workflow_id)
        self.assertEqual(broker_module.resumed_handover_binding(entry), (start_id, 1))
        # Not a refused claim, and (without S7c-R3) the D-B1 child-record path
        # would take it — no control-repository child record exists for it.
        self.assertIsNone(broker_module.refused_claim_resumption(entry))
        self.assertTrue(runtime_module.dispatch_identity_unresolved(entry))

    def test_R3_W2_a_fresh_runtime_binds_the_settled_task_once(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.assertEqual(self.start_states(workflow_id, start_id),
                         ["admitted", "settled:completed stop=none"])
        before = self.linkage_view(mission_id, workflow_id)
        actions, outcomes, turns = self.recover()
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], (entry["target_engine"] or {}).get("task_id")),
                         (wa_record.PHASE_DISPATCHED, "task-started-1"))
        # ONE capability-gated reconcile action; zero model calls; no dispatch.
        self.assertEqual((actions, turns), ([broker_module.ACTION_RECONCILE], 0))
        self.assertEqual(outcomes[workflow_id][0], (
            broker_module.ACTION_RECONCILE, True, None, broker_module.OUTCOME_RECONCILED))
        self.assert_bound_once(mission_id, workflow_id, start_id, before)
        self.assert_idempotent(mission_id, workflow_id)

    def test_R3_W1_a_fresh_runtime_binds_it_and_records_the_lost_settlement(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_after_the_task_settlement)
        # The workflow never recorded the settlement: its start evidence is
        # unresolved, only the canonical record holds the outcome.
        self.assertEqual(self.start_states(workflow_id, start_id), ["admitted"])
        self.assertEqual(wa_record.unresolved_start_receipts(self.record(workflow_id)),
                         {start_id: "admitted"})
        before = self.linkage_view(mission_id, workflow_id)
        actions, outcomes, turns = self.recover()
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], (entry["target_engine"] or {}).get("task_id")),
                         (wa_record.PHASE_DISPATCHED, "task-started-1"))
        self.assertEqual((actions, turns), ([broker_module.ACTION_RECONCILE], 0))
        self.assertEqual(outcomes[workflow_id][0][:2], (broker_module.ACTION_RECONCILE, True))
        self.assert_bound_once(mission_id, workflow_id, start_id, before)
        self.assert_idempotent(mission_id, workflow_id)

    def test_R3_the_recovered_binding_carries_the_mission_to_exactly_one_delivery(self):
        """Row 11(a) in total: one workflow, one spawn request, one runtime
        start, one task — then exactly one commit, one push and one PR."""
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.recover()
        self.assertEqual((self.record(workflow_id)["target_engine"] or {}).get("task_id"),
                         "task-started-1")
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.clean_herd_state(workflow_id)
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        self.stage(workflow_id, "fix.txt", "fixed\n")
        self.wire_delivery(workflow_id)
        self.runtime_pass()
        request, delivered, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, delivered)
        self.runtime_pass()
        self.runtime_pass()
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(sorted(self.remote_refs()),
                         ["refs/heads/di-mission/%s-r1" % mission_id, "refs/heads/main"])
        self.assertEqual(list(wa_store.WorkflowStore(self.store_dir).load()["workflows"]),
                         [workflow_id])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests), self.markers(workflow_id),
                          len(self.engagements(mission_id))), (1, 1, 1, 1, 1))
        self.assertEqual(len(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER)), 1)

    # -- refusals: nothing proven, nothing bound, nothing invoked ------------

    def assert_unbound(self, workflow_id, phase):
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]), (phase, None))
        self.assertEqual(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER), [])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))

    def test_R3_an_unreadable_listing_or_source_binds_nothing_and_writes_nothing(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.restart_dirun()
        receipts = len(self.record(workflow_id)["receipts"])
        self.engine.live_error = OSError("listing unavailable")
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3], (
            broker_module.ACTION_RECONCILE, False, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))
        self.engine.live_error = None
        with mock.patch.object(self.gate, "engagement_starts", lambda entry: None):
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3], (
            broker_module.ACTION_RECONCILE, False, gate_module.PROBLEM_SOURCE_UNAVAILABLE))
        self.assertEqual(len(self.record(workflow_id)["receipts"]), receipts)
        self.assert_unbound(workflow_id, wa_record.PHASE_DISPATCHED)
        # Both answer again: bound once.
        self.runtime_pass()
        self.assertEqual((self.record(workflow_id)["target_engine"] or {}).get("task_id"),
                         "task-started-1")
        self.assertEqual(len(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER)), 1)

    def test_R3_an_unsettled_handover_is_never_bound_or_replayed(self):
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        real_close = broker_module._MissionStartGuard.close

        def dies_before_settling(guard, point, *args, **kwargs):
            if point == dispatch_module.START_POINT_TASK:
                raise Crash("after the handover, before its settlement")
            return real_close(guard, point, *args, **kwargs)
        with mock.patch.object(broker_module._MissionStartGuard, "close", dies_before_settling):
            with self.assertRaises(Crash):
                self.runtime_pass()
        (task_start,) = [s for s in self.starts(mission_id) if s["point"] == "task"]
        self.assertIsNone(task_start["settlement"])
        self.assertEqual(len(self.engine.tasks), 1)
        self.restart_dirun()
        for _ in range(3):
            self.runtime_pass()
        # The outcome is UNKNOWN (the irreducible window): the gate blocks an
        # admitted, unsettled start before any action; the owner settles it
        # uncertain with no identity, so its owed stop cannot be proven and
        # stays pending; never bound, never handed over again.
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        self.assertTrue(any(gate_module.PROBLEM_START_UNSETTLED in r
                            for r in self.receipts(workflow_id, "mission gate block:")))
        self.assertEqual(self.receipts(workflow_id, "%s: %s" % (
            broker_module.RECOVERY_BLOCK_MARKER, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN)),
            [])
        self.assertEqual(self.canonical_starts(mission_id),
                         [(1, "runtime", "completed"), (1, "task", "uncertain")])
        (task_start,) = [s for s in self.starts(mission_id) if s["point"] == "task"]
        self.assertTrue(mission_state.start_stop_required(task_start))
        self.assertFalse(mission_state.start_stop_confirmed(task_start))
        self.assertEqual(self.engine.close_calls, [])

    # -- conflicts: the canonical view the binding reads (a CONTROLLED
    #    adapter scoped to that read; the Mission store itself is
    #    tamper-evident and refuses a changed settlement at load) --------

    def binding_reads(self, change):
        real_bind = broker_module.TargetBroker._bind_settled_handover

        def bind_reading_the_changed_view(broker, *args):
            gate = broker.mission_gate
            real_starts = gate.engagement_starts

            def changed(entry):
                starts = real_starts(entry)
                if starts is not None:
                    starts = json.loads(json.dumps(starts))
                    change(starts)
                return starts
            with mock.patch.object(gate, "engagement_starts", changed):
                return real_bind(broker, *args)
        return mock.patch.object(broker_module.TargetBroker, "_bind_settled_handover",
                                 bind_reading_the_changed_view)

    def assert_conflict_blocks(self, change, reason):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.restart_dirun()

        def the_task(starts):
            return [s for s in starts if s["start_id"] == start_id]
        with self.binding_reads(lambda starts: change(starts, the_task(starts))):
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3], (
            broker_module.ACTION_RECONCILE, True, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        blocks = self.receipts(workflow_id, "%s: %s" % (
            broker_module.RECOVERY_BLOCK_MARKER, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))
        self.assertEqual(len(blocks), 1)
        self.assertIn(reason, blocks[0])
        self.assertEqual(self.engine.close_calls, [])

    def test_R3_conflict_a_settlement_naming_another_runtime(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0]["settlement"]["identity"].update(
                workspace_id="ws-another-runtime"),
            "names another runtime")

    def test_R3_conflict_a_start_of_another_workflow(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0].update(workflow_id="wf-m-another-workflow"),
            "belongs to another workflow")

    def test_R3_conflict_a_start_under_another_authorization(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0].update(authorization_id="ma-" + "0" * 32),
            "authorization")

    def test_R3_conflict_a_start_of_another_ordinal(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0].update(engagement_sequence=2), "ordinal")

    def test_R3_conflict_a_start_of_another_owner(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0].update(owner_ref="another-owner"), "owner")

    def test_R3_conflict_a_failed_or_uncertain_settlement(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0]["settlement"].update(outcome="failed"),
            "settled failed, not completed")

    def test_R3_conflict_a_settlement_that_owes_a_stop(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0]["settlement"].update(stop_pending=True),
            "owes a stop")

    def test_R3_conflict_a_settlement_without_a_usable_task_id(self):
        self.assert_conflict_blocks(
            lambda starts, task: task[0]["settlement"]["identity"].update(
                task_id=dispatch_module.UNRESOLVED_TASK_ID),
            "no usable task id")

    def test_R3_conflict_a_settlement_with_a_blank_task_id(self):
        """Q1: a whitespace-only id is a non-empty string the canonical record
        can hold, but the binding's formatter treats it as unresolved — it is
        refused before any write, never bound as ``unknown``."""
        self.assert_conflict_blocks(
            lambda starts, task: task[0]["settlement"]["identity"].update(task_id=" \t "),
            "no usable task id")

    def test_R3_conflict_a_runtime_start_not_settled_completed(self):
        self.assert_conflict_blocks(
            lambda starts, task: [s["settlement"].update(outcome="uncertain")
                                  for s in starts if s["point"] == "runtime"],
            "no single runtime start")

    def test_R3_conflict_the_named_start_is_absent(self):
        self.assert_conflict_blocks(
            lambda starts, task: starts.remove(task[0]), "no single start")

    def test_R3_conflict_a_second_task_start(self):
        self.assert_conflict_blocks(
            lambda starts, task: starts.append(dict(task[0], start_id="ms-" + "1" * 32)),
            "not the one task start")

    def test_R3_an_objective_not_provably_the_approved_one_is_never_bound(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        store = wa_store.WorkflowStore(self.store_dir)
        workflows = store.load()
        entry = workflows["workflows"][workflow_id]
        for receipt in entry["receipts"]:
            if receipt["bounded_summary"].startswith(dispatch_module.DISPATCH_RECEIPT_MARKER):
                receipt["digest"] = "0" * 64
        store.save(workflows)
        self.restart_dirun()
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:3], (
            broker_module.ACTION_RECONCILE, True, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)

    def test_R3_an_unowned_or_absent_runtime_is_never_bound(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.restart_dirun()
        self.engine.live = [{"workspace_id": "ws-started-1", "agent_names": ["someone-else"]}]
        self.runtime_pass()
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        self.assertTrue(any("workspace_agents_do_not_match" in r for r in self.receipts(
            workflow_id, "%s: %s" % (broker_module.RECOVERY_BLOCK_MARKER,
                                     broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))))
        self.assertEqual(self.engine.close_calls, [])

    def test_R3_another_missions_completed_task_is_never_substituted(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        # Held, so the OTHER Mission's dispatch pass leaves this one alone.
        self.service.request_hold(mission_id, "held while another Mission runs")
        other_mission, other_revision, other_workflow = self.dispatched(
            objective="another mission's objective")
        (other_task,) = [s for s in self.starts(other_mission) if s["point"] == "task"]
        self.assertEqual(other_task["settlement"]["outcome"], "completed")
        effects = (len(self.engine.starts), len(self.engine.tasks), len(self.spawn_requests))
        self.assertEqual(effects, (2, 2, 2))
        # The record's own admitted claim and start receipts rewritten to name
        # the OTHER Mission's completed task start.
        store = wa_store.WorkflowStore(self.store_dir)
        workflows = store.load()
        entry = workflows["workflows"][workflow_id]
        for receipt in entry["receipts"]:
            receipt["bounded_summary"] = receipt["bounded_summary"].replace(
                start_id, other_task["start_id"])
        store.save(workflows)
        self.assertEqual(broker_module.resumed_handover_binding(self.record(workflow_id)),
                         (other_task["start_id"], 1))
        other_before = self.record(other_workflow)
        self.grok_control(mission_id, revision, "resume")
        self.restart_dirun()
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["target_engine"], None)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        blocks = self.receipts(workflow_id, "%s: %s" % (
            broker_module.RECOVERY_BLOCK_MARKER, broker_module.PROBLEM_RESUME_BINDING_UNPROVEN))
        self.assertEqual(len(blocks), 1)
        self.assertIn("no single start", blocks[0])
        self.assertEqual(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER), [])
        self.assertEqual(self.record(other_workflow)["target_engine"],
                         other_before["target_engine"])
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), effects)

    # -- competing controls ---------------------------------------------------

    def test_R3_a_hold_defers_the_binding_and_the_lift_binds_once(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.service.request_hold(mission_id, "held after the crash")
        self.restart_dirun()
        with self.minted() as actions:
            for _ in range(2):
                self.runtime_pass()
        self.assertEqual(actions, [])
        self.assert_unbound(workflow_id, wa_record.PHASE_DISPATCHED)
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assertEqual((self.record(workflow_id)["target_engine"] or {}).get("task_id"),
                         "task-started-1")
        self.assertEqual(len(self.receipts(workflow_id, broker_module.RECOVERY_BOUND_MARKER)), 1)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_R3_a_cancel_after_the_loss_is_terminal_and_stops_the_owned_runtime(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.service.request_cancel(mission_id, "cancelled after the crash")
        self.restart_dirun()
        for _ in range(2):
            self.runtime_pass()
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))

    def test_R3_an_edit_after_the_loss_is_terminal(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        edited, is_error = self.client.call(protocol.TOOL_MISSION_EDIT, dict(
            proposal_arguments(self.baseline, objective="changed after the crash"),
            mission_id=mission_id, expected_revision=revision))
        self.assertFalse(is_error, edited)
        self.assertTrue(self.revoked(authority[1]))
        self.restart_dirun()
        for _ in range(2):
            self.runtime_pass()
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        self.assertTrue(any(gate_module.PROBLEM_REVISION_SUPERSEDED in r
                            for r in self.receipts(workflow_id, "")))

    def test_R3_an_expiry_after_the_loss_is_terminal(self):
        mission_id, revision, workflow_id, authority, start_id = self.crashed(
            self.crash_before_the_binding_save)
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.restart_dirun()
        for _ in range(2):
            self.runtime_pass()
        self.assert_unbound(workflow_id, wa_record.PHASE_BLOCKED)
        self.assertTrue(any("mission_authorization_expired" in r
                            for r in self.receipts(workflow_id, "")))

    # -- the follow-up boundary (R2): no binding is ever lost ----------------

    def test_R3_a_follow_up_dying_after_its_settlement_loses_no_binding_and_replays_nothing(self):
        """The follow-up's actual boundary: its settlement is canonical, the
        process dies before the record's settled receipt; a follow-up keeps
        the identity bound at the initial dispatch, so no binding is lost —
        it continues, completes and delivers exactly once."""
        mission_id, revision, workflow_id, authority, budget = self.refused_follow_up()
        bound = self.record(workflow_id)["target_engine"]
        self.grok_control(mission_id, revision, "resume")
        with self.crash_after_the_task_settlement() as fired:
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(len(fired), 1)
        # Follow-up 2 was handed over and settled canonically; the record
        # keeps the identity bound at the initial dispatch (a follow-up never
        # rebinds it), so there is no binding to lose.
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual(self.canonical_starts(mission_id), [
            (1, "runtime", "completed"), (1, "task", "completed"),
            (2, "runtime", "completed"), (2, "task", "completed")])
        entry = self.record(workflow_id)
        self.assertEqual(entry["target_engine"], bound)
        self.assertIsNone(broker_module.resumed_handover_binding(entry))
        self.assertFalse(runtime_module.dispatch_identity_unresolved(entry))
        (follow_up_task,) = [s for s in self.starts(mission_id)
                             if s["point"] == "task" and s["engagement_sequence"] == 2]
        # The lost DERIVED receipt: the record still reads the settled
        # follow-up task start ``admitted`` — falsely unresolved.
        self.assertEqual(wa_record.unresolved_start_receipts(entry),
                         {follow_up_task["start_id"]: "admitted"})
        before = self.linkage_view(mission_id, workflow_id)
        self.restart_dirun()
        self.restart_grok()
        with self.minted() as actions:
            for _ in range(3):
                self.runtime_pass()
        self.assertNotIn(broker_module.ACTION_RECONCILE, actions)
        self.assertNotIn(broker_module.ACTION_FOLLOW_UP, actions)
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))
        self.assertEqual(self.service.get_state(mission_id)["budget"], budget)
        self.assertEqual(self.record(workflow_id)["target_engine"], bound)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        # The owner's recovery pass re-recorded the lost receipt FROM the
        # canonical settlement: no longer falsely unresolved (so no longer
        # falsely retention-protected), with NO new authority — every
        # canonical start, reservation, marker, the budget and the linkage
        # are exactly as before.
        self.assertEqual(wa_record.unresolved_start_receipts(self.record(workflow_id)), {})
        self.assertEqual(self.start_states(workflow_id, follow_up_task["start_id"]), [
            "admitted",
            "settled:completed cause=(recovery pass: the canonical settlement) stop=none"])
        self.assertEqual(self.linkage_view(mission_id, workflow_id), before)
        # The corrected work completes and is delivered exactly once.
        self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE"), (2, "APPROVE")])
        self.target_task_status = "COMPLETE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.clean_herd_state(workflow_id)
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        self.stage(workflow_id, "fix.txt", "fixed\n")
        self.wire_delivery(workflow_id)
        self.runtime_pass()
        request, delivered, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, delivered)
        self.runtime_pass()
        self.runtime_pass()
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual((self.markers(workflow_id), len(self.engagements(mission_id))), (2, 2))


# ======================================================================
# Task 8 ownership correction — the terminal release of a RESUMED initial
# handover (no child record by construction) proves its workspaces from the
# canonical settled starts: exact binding, one proof, revalidated at the
# close, observed absence before any reclamation; anything else retains.
# ======================================================================


class OwnershipReleaseTests(_ResumptionCase):

    RELEASE = broker_module.ACTION_RELEASE

    def deliver(self, mission_id, revision, workflow_id):
        """Completion → the human's delivery decision → the PR: the pass that
        delivers releases the retention (PR created); the release has NOT run."""
        self.clean_herd_state(workflow_id)
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        self.stage(workflow_id, "fix.txt", "fixed\n")
        self.wire_delivery(workflow_id)
        self.runtime_pass()
        request, delivered, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, delivered)
        self.runtime_pass()
        entry = self.record(workflow_id)
        self.assertEqual(self.delivery_transport.performed["gh_pr_create"], 1)
        self.assertEqual(entry[wa_record.RETENTION_KEY]["release_reason"],
                         wa_record.RETENTION_RELEASE_PR_CREATED)
        self.assertIsNone(entry["workspace_lease"].get("released_at"))

    def resumed_and_delivered(self):
        """A RESUMED initial handover (R1) carried to a delivered PR."""
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"], "task-started-1")
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        return mission_id, revision, workflow_id

    def resumed_and_expired(self):
        """A RESUMED initial handover (R1), bound; then its authority lapses —
        the workflow BLOCKED, its runtime idle and owed no stop, the Mission
        itself not terminal (controls still apply) — and the delivery-
        candidate retention's deadline passes, so the release is admitted."""
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"], "task-started-1")
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertFalse(any(mission_state.start_stop_required(s)
                             for s in self.starts(mission_id)))
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        return mission_id, revision, workflow_id

    def cleanup_receipts(self, workflow_id):
        return self.receipts(workflow_id, broker_module.CLEANUP_RECEIPT_MARKER)

    def release_pass(self):
        """One pass; the release's report rows (kind, name, verdict, ok,
        detail) are collected in ``self.rows`` — the per-item reasons, which
        the durable cleanup receipt carries only as counts."""
        real = ownership_module.CleanupReport.record
        rows = self.__dict__.setdefault("rows", [])

        def record(report, kind, name, verdict, ok=True, detail=None):
            rows.append((kind, name, verdict, ok, detail))
            return real(report, kind, name, verdict, ok=ok, detail=detail)
        with mock.patch.object(ownership_module.CleanupReport, "record", record):
            with self.minted() as actions:
                outcomes = outcome_view(self.runtime_pass())
        return actions, outcomes

    def released(self, workflow_id):
        lease = self.record(workflow_id)["workspace_lease"]
        return lease.get("released_at") is not None, os.path.isdir(lease["path_realpath"])

    def canonical_snapshot(self, mission_id):
        document = self.mission_document()
        return (self.starts(mission_id), document["authorizations"],
                document["missions"][mission_id]["decisions"],
                dict((k, v) for k, v in document["reservations"].items()
                     if v["kind"] not in mission_store_module.OPERATION_RESERVATION_KINDS))

    def assert_retained(self, workflow_id, reason, closes=()):
        self.assertEqual(self.engine.close_calls, list(closes))
        self.assertEqual(self.released(workflow_id), (False, True))
        receipts = self.cleanup_receipts(workflow_id)
        self.assertTrue(receipts)
        self.assertIn("cleanup DEGRADED", receipts[-1])
        sessions = [row for row in self.__dict__.get("rows", [])
                    if row[0] == "workspace_session"]
        self.assertTrue(any(row[2] == ownership_module.UNPROVABLE
                            and reason in (row[4] or "") for row in sessions), sessions)

    def binding_reads(self, change, calls=None):
        """A CONTROLLED canonical adapter scoped to the release binding's own
        reads of the engagement starts (``calls``: which of its invocations,
        1 = the proof, 2 = the re-derivation at the close; all when None)."""
        real = broker_module._canonical_binding
        count = []

        def binding(broker, entry):
            count.append(1)
            if calls is not None and len(count) not in calls:
                return real(broker, entry)
            gate = broker.mission_gate
            real_starts = gate.engagement_starts

            def changed(e):
                starts = real_starts(e)
                if starts is not None:
                    starts = json.loads(json.dumps(starts))
                    change(starts)
                return starts
            with mock.patch.object(gate, "engagement_starts", changed):
                return real(broker, entry)
        return mock.patch.object(broker_module, "_canonical_binding", binding)

    # -- the working release ---------------------------------------------------

    def test_OW1_the_resumed_handover_is_released_from_its_canonical_starts(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        canonical = self.canonical_snapshot(mission_id)
        self.assertFalse(os.path.exists(self.children_file()))
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        release = [o for o in outcomes[workflow_id] if o[0] == self.RELEASE]
        self.assertEqual(release, [(self.RELEASE, True, None, None)])
        # The ONE owned workspace closed exactly once, OBSERVED absent, then the
        # directory removed and the lease released; nothing replayed.
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        receipts = self.cleanup_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        self.assertIn("cleanup complete", receipts[0])
        self.assertIn("0 unprovable, 0 failed", receipts[0])
        preserved = self.receipts(workflow_id, "target evidence preserved")
        self.assertEqual(len(preserved), 1)
        self.assertIn("complete", preserved[0])
        # No child record fabricated, no canonical write, no fresh authority.
        self.assertFalse(os.path.exists(self.children_file()))
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        # Released once: later passes close and remove nothing more.
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(len(self.cleanup_receipts(workflow_id)), 1)

    def test_OW2_an_already_absent_workspace_is_reclaimed_without_a_close(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.engine.live = []
        self.release_pass()
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertIn("cleanup complete", self.cleanup_receipts(workflow_id)[-1])

    def test_OW3_the_same_workspace_across_ordinals_is_closed_at_most_once(self):
        # The follow-up is a SPAWN: its real child record is written (and is
        # required); this fixture's engine reuses one workspace id and one
        # task id, so both ordinals name the same identity.
        self.real_child_records()
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        self.grok_control(mission_id, revision, "resume")
        self.runtime_pass()
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        self.runtime_pass()
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        # The follow-up's RETIREMENT closed the earlier incarnation before its
        # start (Task 8 startup correction); the start then listed the reused
        # workspace id once more.
        self.assertEqual((self.engine.close_calls, len(self.engine.live)),
                         (["ws-started-1"], 1))
        starts = self.starts(mission_id)
        self.assertEqual(sorted(set(s["settlement"]["identity"]["workspace_id"]
                                    for s in starts)), ["ws-started-1"])
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="the corrected mission is verified")
        self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE"), (2, "APPROVE")])
        self.target_task_status = "COMPLETE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        with open(self.children_file()) as handle:
            self.assertEqual([(c["task_id"], c["workspace_id"])
                              for c in json.load(handle)["children"]],
                             [("task-started-1", "ws-started-1")])
        self.assert_two_incarnations_of_one_id(mission_id, workflow_id)
        self.release_pass()
        # No repeated effect on the SAME incarnation: the retirement closed
        # incarnation 1 (established by ordinal 1, claimed by follow-up 2
        # BEFORE ordinal 2's canonical open); the release closed incarnation
        # 2, which ordinal 2's canonical start RE-ESTABLISHED under the same
        # string id — two closes of one id, one per incarnation.
        self.assertEqual((self.engine.close_calls, self.engine.live),
                         (["ws-started-1", "ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual(self.engine_counts(), (2, 1, 1))

    def assert_two_incarnations_of_one_id(self, mission_id, workflow_id):
        """The shape behind two closes of ONE string id, made visible: the
        retirement's durable claim is of follow-up 2 and precedes ordinal 2's
        canonical open (the claim trail), both canonical runtime starts
        establish that id, so the claimed incarnation (ordinal 1's) is NOT the
        one still listed (``_retired_identities`` is empty) — only a
        re-established incarnation is left for the release to close."""
        entry = self.record(workflow_id)
        claims, returned, undecodable = broker_module._retirement_close_claims(entry)
        self.assertEqual((claims, returned, undecodable),
                         ({"ws-started-1": {2}}, {"ws-started-1": {2}}, 0))
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[:3],
                         ["claiming", "claim:retiring", "claiming"])
        self.assertTrue(self.claim_states(workflow_id, "claim-runtime_start-2")[3]
                        .startswith("claim:admitted"))
        self.assertEqual(sorted((s["engagement_sequence"],
                                 s["settlement"]["identity"]["workspace_id"])
                                for s in self.starts(mission_id) if s["point"] == "runtime"),
                         [(1, "ws-started-1"), (2, "ws-started-1")])
        self.assertEqual(broker_module._retired_identities(self.broker, entry, claims), {})

    def test_OW4_a_crash_after_the_close_is_reconciled_without_a_second_close(self):
        from target_runtime import workspace_ownership as ws_module
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        real = ws_module.close_proven_workspace

        def closes_then_dies(*args, **kwargs):
            outcome = real(*args, **kwargs)
            raise Crash("after the close, before the release is persisted")
        with mock.patch.object(ws_module, "close_proven_workspace", closes_then_dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        # Closed, but nothing persisted: the lease and directory are held.
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.cleanup_receipts(workflow_id), [])
        self.restart_dirun()
        self.release_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertIn("cleanup complete", self.cleanup_receipts(workflow_id)[-1])

    # -- refusals: nothing proven, nothing closed or reclaimed ----------------

    def test_OW5_another_partys_workspace_is_never_closed(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.engine.live = [{"workspace_id": "ws-started-1", "agent_names": ["someone-else"]}]
        for _ in range(2):
            self.release_pass()
        self.assert_retained(workflow_id, "workspace_agents_do_not_match")
        self.assertEqual(self.engine.live[0]["agent_names"], ["someone-else"])

    def test_OW6_an_ambiguous_listing_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.engine.live = self.engine.live * 2
        self.release_pass()
        self.assert_retained(workflow_id, "workspace_id_not_unique")

    def test_OW7_a_close_that_leaves_the_workspace_listed_is_not_absence(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.engine.close_leaves_visible = True
        self.release_pass()
        self.assert_retained(workflow_id, "still listed", closes=["ws-started-1"])
        # Once a close really removes it, the next pass closes and reclaims.
        self.engine.close_leaves_visible = False
        self.release_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1", "ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))

    def test_OW8_child_evidence_that_exists_is_never_overridden(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.spawn_record_overrides = {"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": self.record(workflow_id)["workspace_lease"]["path_realpath"],
            "task_id": "task-started-1", "recorded_status": "ACTIVE", "role": None,
            "workspace_id": "ws-another", "agents": {"lead": "lead-9"}}]}
        self.release_pass()
        # Refused AT THE PROOF (before preservation names any workspace), not
        # only by the revalidation at the close: the record names this lease
        # and the bound task but another runtime — no canonical start
        # establishes it.
        self.assert_retained(workflow_id, "%s: child evidence names this workflow's lease"
                             " with 1 record(s) no canonical start of it establishes"
                             " exactly (task, workspace: 'task-started-1' 'ws-another')"
                             % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN)

    def test_OW8b_other_leases_child_records_are_not_evidence_for_this_workflow(self):
        """Positive: child records of OTHER leases (other workflows' spawns)
        are listed; none names this lease, so the child evidence is cleanly
        absent for this workflow and the release proceeds exactly as with an
        empty listing."""
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        self.spawn_record_overrides = {"records": [
            {"parent_task_id": None, "dependency": False,
             "repo": "/managed/another-workflow-lease", "task_id": "task-%d" % index,
             "recorded_status": "ACTIVE", "role": None,
             "workspace_id": "ws-other-%d" % index, "agents": {"lead": "lead-%d" % index}}
            for index in (1, 2)]}
        self.release_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED,
                           True, "closed and absent from a complete fresh listing")])
        self.assertIn("0 unprovable, 0 failed", self.cleanup_receipts(workflow_id)[-1])

    def test_OW9_a_hold_between_admission_and_close_closes_nothing(self):
        from target_runtime import evidence_preservation as preserve_module
        mission_id, revision, workflow_id = self.resumed_and_expired()
        real = preserve_module.preserve

        def preserve_then_hold(*args, **kwargs):
            outcome = real(*args, **kwargs)
            self.service.request_hold(mission_id, "held during the release")
            return outcome
        with mock.patch.object(preserve_module, "preserve", preserve_then_hold):
            self.release_pass()
        self.assert_retained(workflow_id, "the cleanup admission refused at the close")
        # Still held: the release waits; lifted: it closes once and reclaims.
        self.release_pass()
        self.assertEqual(self.engine.close_calls, [])
        self.grok_control(mission_id, revision, "resume")
        self.release_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))

    def test_OW10_a_binding_that_changes_before_the_close_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()

        def another_owner(starts):
            for start in starts:
                start["owner_ref"] = "another-owner"
        with self.binding_reads(another_owner, calls={2}):
            self.release_pass()
        self.assert_retained(workflow_id, "the canonical binding changed since the proof")

    def test_OW11_a_source_that_fails_before_the_close_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        real = broker_module._canonical_binding
        count = []

        def fails_at_the_close(broker, entry):
            count.append(1)
            if len(count) == 2:
                return None, None, None, gate_module.PROBLEM_SOURCE_UNAVAILABLE, "unavailable"
            return real(broker, entry)
        with mock.patch.object(broker_module, "_canonical_binding", fails_at_the_close):
            self.release_pass()
        self.assert_retained(workflow_id, gate_module.PROBLEM_SOURCE_UNAVAILABLE)

    def assert_conflict_retains(self, change, reason):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        with self.binding_reads(change):
            self.release_pass()
        self.assert_retained(workflow_id, reason)

    def test_OW12_conflict_a_second_runtime_start_of_an_ordinal(self):
        self.assert_conflict_retains(
            lambda starts: starts.append(dict(
                [s for s in starts if s["point"] == "runtime"][0], start_id="ms-" + "2" * 32)),
            "ordinal 1 holds 2 runtime")

    def test_OW13_conflict_a_start_of_another_owner(self):
        self.assert_conflict_retains(
            lambda starts: starts[-1].update(owner_ref="another-owner"),
            "belongs to another owner")

    def test_OW14_conflict_a_start_under_another_authorization(self):
        self.assert_conflict_retains(
            lambda starts: starts[-1].update(authorization_id="ma-" + "0" * 32),
            "is under another authorization")

    def test_OW15_conflict_a_start_outside_the_records_ordinals(self):
        self.assert_conflict_retains(
            lambda starts: starts.append(dict(starts[0], start_id="ms-" + "5" * 32,
                                              engagement_sequence=5)),
            "outside the record's 1 dispatches")

    def test_OW16_conflict_a_task_start_naming_another_runtime(self):
        def change(starts):
            (task,) = [s for s in starts if s["point"] == "task"]
            task["settlement"]["identity"]["workspace_id"] = "ws-another"
        self.assert_conflict_retains(change, "names another runtime")

    def test_OW17_conflict_a_task_start_of_another_task(self):
        def change(starts):
            (task,) = [s for s in starts if s["point"] == "task"]
            task["settlement"]["identity"]["task_id"] = "task-of-someone-else"
        self.assert_conflict_retains(change, "not the record's bound")

    def test_OW20_an_expired_idle_runtime_is_released_from_its_canonical_starts(self):
        """The S7c limit (a): the authority lapsed while the resumed runtime sat
        idle, owed no stop; once the release is admitted it is proven, closed,
        observed absent and reclaimed."""
        mission_id, revision, workflow_id = self.resumed_and_expired()
        canonical = self.canonical_snapshot(mission_id)
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertIn("cleanup complete", self.cleanup_receipts(workflow_id)[-1])
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 1))

    def test_OW18_a_stop_owed_and_unconfirmed_keeps_the_release_waiting(self):
        mission_id, revision, workflow_id = self.resumed_and_expired()
        self.engine.close_error = OSError("the close was refused")
        self.service.request_cancel(mission_id, "cancelled after the lapse")
        for _ in range(2):
            self.runtime_pass()
        # The sticky stop is owed and unconfirmed: the release is not admitted,
        # nothing is reclaimed.
        self.assertTrue(all(mission_state.start_stop_required(s)
                            and not mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.cleanup_receipts(workflow_id), [])
        # The owned stop succeeds and is confirmed by absence; only then the
        # release reclaims — the workspace already absent, closed by the stop.
        self.engine.close_error = None
        for _ in range(2):
            self.runtime_pass()
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual(self.engine.live, [])

    def test_OW19_negative_control_truly_absent_child_evidence_retains(self):
        """NEGATIVE CONTROL for truly ABSENT child evidence of a SPAWN — NOT
        the ordinary spawn's behaviour. The fixture's engine double here
        writes no child record at all, which the real writer always does (the
        ordinary spawn, with its real record, is released: OW21). The
        canonical starts prove the runtime, but the record the spawn writes
        is missing: that is never read as clean — nothing is closed or
        reclaimed."""
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.deliver(mission_id, revision, workflow_id)
        self.assertFalse(os.path.exists(self.children_file()))
        self.release_pass()
        self.assert_retained(workflow_id, "the child record a spawn of this workflow"
                             " writes is absent (task(s) 'task-started-1')")
        self.assertEqual(len(self.engine.live), 1)

    # -- cause 3: the ORDINARY spawn, through its real child record ------------
    # (``real_child_records`` lives on ``_ResumptionCase``: a follow-up's
    # retirement of the earlier runtime reads the same records.)

    def spawned_and_delivered(self):
        """An ORDINARY Mission spawn (no hold, no resume), its real child
        record written, carried to a delivered PR."""
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched()
        lease = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        with open(self.children_file()) as handle:
            (child,) = json.load(handle)["children"]
        # The real writer's record, key for key: it names the workspace and
        # its agents ...
        self.assertEqual(sorted(child), [
            "agents", "dependency", "parent_repo", "parent_task_id", "repo",
            "requested_at", "task_id", "task_status", "workspace_id"])
        self.assertEqual((os.path.realpath(child["repo"]), child["task_id"],
                          child["workspace_id"], child["agents"]),
                         (lease, "task-started-1", "ws-started-1", AGENTS))
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        return mission_id, revision, workflow_id

    def children_bytes(self):
        with open(self.children_file(), "rb") as handle:
            return handle.read()

    def test_OW21_cause_3_an_ordinary_spawn_is_released_through_its_child_record(self):
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        release = [o for o in outcomes[workflow_id] if o[0] == self.RELEASE]
        self.assertEqual(release, [(self.RELEASE, True, None, None)])
        # Proven from the canonical starts with the real child record
        # cross-checked EXACTLY (task, workspace, agents — the projection now
        # carries all three), closed exactly once, OBSERVED absent, then the
        # directory removed and the lease released.
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED,
                           True, "closed and absent from a complete fresh listing")])
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        receipts = self.cleanup_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        self.assertIn("cleanup complete", receipts[0])
        self.assertIn("0 unprovable, 0 failed", receipts[0])
        preserved = self.receipts(workflow_id, "target evidence preserved")
        self.assertEqual(len(preserved), 1)
        self.assertIn("complete", preserved[0])
        # The child record is read, never rewritten; no canonical write.
        self.assertEqual(self.children_bytes(), children)
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(len(self.cleanup_receipts(workflow_id)), 1)
        # What the proof read: the production projection carries the
        # recorded workspace and agents exactly.
        (listed,) = self.spawn_records(self.control)["listed"]
        self.assertEqual((listed["task_id"], listed["workspace_id"], listed["agents"]),
                         ("task-started-1", "ws-started-1", AGENTS))

    def test_OW22_cause_3_another_partys_workspace_under_the_recorded_id_retains(self):
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        self.engine.live = [{"workspace_id": "ws-started-1", "agent_names": ["someone-else"]}]
        for _ in range(2):
            self.release_pass()
        self.assert_retained(workflow_id, ws_problem("PROBLEM_AGENTS_DISAGREE"))
        self.assertEqual(self.engine.live[0]["agent_names"], ["someone-else"])

    def test_OW23_cause_3_conflicting_child_records_close_nothing(self):
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        with open(self.children_file()) as handle:
            document = json.load(handle)
        document["children"].append(dict(document["children"][0], workspace_id="ws-another"))
        with open(self.children_file(), "w") as handle:
            json.dump(document, handle)
        self.release_pass()
        self.assert_retained(workflow_id, "no canonical start of it establishes exactly"
                             " (task, workspace: 'task-started-1' 'ws-another')")
        self.assertEqual(len(self.engine.live), 1)

    def test_OW24_cause_3_a_crash_after_the_close_is_reconciled_without_a_second_close(self):
        from target_runtime import workspace_ownership as ws_module
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        real = ws_module.close_proven_workspace

        def closes_then_dies(*args, **kwargs):
            real(*args, **kwargs)
            raise Crash("after the close, before the release is persisted")
        with mock.patch.object(ws_module, "close_proven_workspace", closes_then_dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.cleanup_receipts(workflow_id), [])
        self.restart_dirun()
        self.release_pass()
        # The canonical identity is now absent from a complete listing:
        # nothing to close, reclaimed — no second close.
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED, True,
                           "absent from a complete fresh listing; nothing to close")])
        self.assertEqual(self.cleanup_receipts(workflow_id), [
            "workflow cleanup: cleanup complete: removed 5, skipped 0 not owned,"
            " 0 unprovable, 0 failed"])

    def test_OW25_cause_3_a_close_that_leaves_the_workspace_listed_is_not_absence(self):
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        self.engine.close_leaves_visible = True
        self.release_pass()
        self.assert_retained(workflow_id, "closed, but still listed", closes=["ws-started-1"])
        self.engine.close_leaves_visible = False
        self.release_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1", "ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))

    # -- cause 1: NO task bound (the pre-binding state) --------------------------

    def pre_binding_and_lapsed(self, lift_first=False):
        """The already-supported pre-binding state (``refused_at_the_task_claim``:
        the runtime started and settled ``completed``, the task claim durably
        refused under a hold, NO task bound), then the authority lapses —
        still under the hold, or (``lift_first``) after the hold is lifted but
        before any valid handover. BLOCKED, unbound, the runtime live and
        owed no stop, nothing closed."""
        mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
        if lift_first:
            self.grok_control(mission_id, revision, "resume")
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.runtime_pass()
        entry = self.record(workflow_id)
        self.assertEqual((entry["phase"], entry["target_engine"]),
                         (wa_record.PHASE_BLOCKED, None))
        self.assertIsNone(ownership_module.recorded_task_id(entry))
        self.assertEqual(self.canonical_starts(mission_id), [(1, "runtime", "completed")])
        self.assertFalse(any(mission_state.start_stop_required(s)
                             for s in self.starts(mission_id)))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          self.engine.close_calls, len(self.engine.live)), (1, 0, [], 1))
        self.assertEqual(self.released(workflow_id), (False, True))
        return mission_id, revision, workflow_id

    def test_OW26_cause_1_expired_while_held_waits_then_is_released_after_the_lift(self):
        mission_id, revision, workflow_id = self.pre_binding_and_lapsed()
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        # The retention expired while the hold stands: the release waits —
        # nothing closed, preserved or reclaimed.
        for _ in range(2):
            actions, outcomes = self.release_pass()
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.cleanup_receipts(workflow_id), [])
        self.assertEqual(self.receipts(workflow_id, "target evidence preserved"), [])
        # The hold is lifted before any valid handover (the authority lapsed):
        # the release proves the runtime from the canonical start and the lease,
        # closes it exactly once, OBSERVES it absent, and only then reclaims.
        self.grok_control(mission_id, revision, "resume")
        canonical = self.canonical_snapshot(mission_id)
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        release = [o for o in outcomes[workflow_id] if o[0] == self.RELEASE]
        self.assertEqual(release, [(self.RELEASE, True, None, None)])
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED,
                           True, "closed and absent from a complete fresh listing")])
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        # No task was handed over, nothing started again, no child record
        # fabricated, no canonical write, no fresh authority.
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 0, 1))
        self.assertIsNone(self.record(workflow_id)["target_engine"])
        self.assertFalse(os.path.exists(self.children_file()))
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        receipts = self.cleanup_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        self.assertIn("0 unprovable, 0 failed", receipts[0])
        preserved = self.receipts(workflow_id, "target evidence preserved")
        self.assertEqual(len(preserved), 1)
        self.assertIn("complete", preserved[0])
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(len(self.cleanup_receipts(workflow_id)), 1)

    def test_OW27_cause_1_lifted_then_lapsed_before_a_handover_is_released_on_expiry(self):
        mission_id, revision, workflow_id = self.pre_binding_and_lapsed(lift_first=True)
        self.release_pass()
        self.assertEqual((self.engine.close_calls, self.released(workflow_id)),
                         ([], (False, True)))
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        self.release_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks)), (1, 0))
        self.assertIn("cleanup complete", self.cleanup_receipts(workflow_id)[-1])

    def test_OW28_cause_1_a_cancel_stops_it_once_and_the_release_closes_nothing_more(self):
        """The sticky stop, unweakened: the cancel owes a stop, the owned stop
        closes the runtime from its start identity and confirms it by absence;
        the human confirms the cancel (retention released); the release then
        finds the identity ABSENT and reclaims — one close in total."""
        mission_id, revision, workflow_id = self.pre_binding_and_lapsed()
        self.service.request_cancel(mission_id, "cancelled after the lapse")
        for _ in range(2):
            self.runtime_pass()
        self.assertTrue(all(mission_state.start_stop_confirmed(s)
                            for s in self.starts(mission_id)))
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.service.confirm_cancel(mission_id)
        self.release_pass()
        self.assertEqual(self.record(workflow_id)[wa_record.RETENTION_KEY]["release_reason"],
                         wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED,
                           True, "absent from a complete fresh listing; nothing to close")])
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))

    def test_OW29_cause_1_a_crash_after_the_close_is_reconciled_without_a_second_close(self):
        from target_runtime import workspace_ownership as ws_module
        mission_id, revision, workflow_id = self.pre_binding_and_lapsed(lift_first=True)
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        real = ws_module.close_proven_workspace

        def closes_then_dies(*args, **kwargs):
            real(*args, **kwargs)
            raise Crash("after the close, before the release is persisted")
        with mock.patch.object(ws_module, "close_proven_workspace", closes_then_dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (False, True))
        self.restart_dirun()
        self.release_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(self.released(workflow_id), (True, False))

    def test_OW43_cause_1_nothing_started_is_reclaimed_with_nothing_to_close(self):
        """No task bound and NO canonical start at all: the hold landed before
        the runtime-start claim, which was durably refused, so nothing was
        invoked (a start is recorded before any engine call). After the lapse
        and the expiry the release reclaims on that positive canonical
        evidence — no listing is needed to close nothing, nothing is closed."""
        mission_id, revision = self.authorized()
        workflow_id = self.grok_dispatch(mission_id)[0]["workflow_id"]
        self.target_task_status = "ACTIVE"
        with self.hold_at(mission_id, 1, "runtime_start", "hold before the start"):
            self.runtime_pass()
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-1"),
                         ["claiming", "claim:refused cause=mission_control_hold_active"])
        self.assertEqual(self.canonical_starts(mission_id), [])
        self.assertEqual((len(self.engine.starts), len(self.engine.live)), (0, 0))
        self.grok_control(mission_id, revision, "resume")
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.runtime_pass()
        self.assertEqual((self.record(workflow_id)["phase"],
                          self.record(workflow_id)["target_engine"]),
                         (wa_record.PHASE_BLOCKED, None))
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        self.release_pass()
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", workflow_id, ownership_module.OWNED, True,
                           "no canonical start of this workflow holds a live runtime;"
                           " nothing to close")])
        self.assertEqual((self.engine.close_calls, len(self.engine.starts)), ([], 0))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertIn("0 unprovable, 0 failed", self.cleanup_receipts(workflow_id)[-1])

    def unbound_and_expired(self):
        mission_id, revision, workflow_id = self.pre_binding_and_lapsed(lift_first=True)
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        return mission_id, revision, workflow_id

    def test_OW30_cause_1_another_partys_workspace_is_never_closed(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()
        self.engine.live = [{"workspace_id": "ws-started-1", "agent_names": ["someone-else"]}]
        for _ in range(2):
            self.release_pass()
        self.assert_retained(workflow_id, "workspace ws-started-1 is")
        self.assertEqual(self.engine.live[0]["agent_names"], ["someone-else"])

    def test_OW31_cause_1_child_evidence_naming_the_lease_is_never_overridden(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()
        self.spawn_record_overrides = {"records": [{
            "parent_task_id": None, "dependency": False,
            "repo": self.record(workflow_id)["workspace_lease"]["path_realpath"],
            "task_id": "task-of-another-spawn", "recorded_status": "ACTIVE", "role": None,
            "workspace_id": "ws-another", "agents": {"lead": "lead-9"}}]}
        self.release_pass()
        self.assert_retained(workflow_id, "child evidence names this workflow's lease")
        self.assertEqual(len(self.engine.live), 1)

    def test_OW32_cause_1_a_task_handed_over_but_not_yet_bound_derives_nothing(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()

        def handed_over(starts):
            (runtime,) = starts
            task = json.loads(json.dumps(runtime))
            task.update(start_id="ms-" + "7" * 32, point="task")
            task["settlement"]["identity"]["task_id"] = "task-started-1"
            starts.append(task)
        with self.binding_reads(handed_over):
            self.release_pass()
        self.assert_retained(workflow_id, "which the record has not bound")

    def test_OW33_cause_1_an_unbound_record_of_more_than_one_dispatch_derives_nothing(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()
        real = broker_module._canonical_binding

        def two_dispatches(broker, entry):
            with mock.patch.object(broker_module.dispatch_module, "dispatch_count",
                                   lambda e: 2):
                return real(broker, entry)
        with mock.patch.object(broker_module, "_canonical_binding", two_dispatches):
            self.release_pass()
        self.assert_retained(workflow_id, "binds no task yet holds 2 dispatches")

    def test_OW34_cause_1_a_task_bound_between_proof_and_close_closes_nothing(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()

        def handed_over(starts):
            (runtime,) = starts
            task = json.loads(json.dumps(runtime))
            task.update(start_id="ms-" + "7" * 32, point="task")
            task["settlement"]["identity"]["task_id"] = "task-started-1"
            starts.append(task)
        with self.binding_reads(handed_over, calls={2}):
            self.release_pass()
        self.assert_retained(workflow_id, "the canonical binding changed since the proof")
        self.assertEqual(len(self.engine.live), 1)

    # -- late changes at the EFFECT boundary (both routes) -----------------------

    @contextlib.contextmanager
    def during_the_close_time_listing(self, change):
        """``change`` lands WHILE the release's close-time live listing is read:
        the first listing after the evidence preservation — the potentially
        blocking engine read immediately before the close, after the ONE
        proof. Yields ``fired``, which the test asserts (non-vacuous)."""
        from target_runtime import evidence_preservation as preserve_module
        real_preserve = preserve_module.preserve
        worker = self.broker.worker
        real_listing = worker.live_workspaces
        armed, fired = [], []

        def preserve(*args, **kwargs):
            outcome = real_preserve(*args, **kwargs)
            armed.append(1)
            return outcome

        def listing(*args, **kwargs):
            live = real_listing(*args, **kwargs)
            if armed and not fired:
                fired.append(1)
                change()
            return live
        with mock.patch.object(preserve_module, "preserve", preserve):
            with mock.patch.object(worker, "live_workspaces", listing):
                yield fired

    def assert_retained_after_preservation(self, workflow_id, reason, fired):
        self.assertEqual(fired, [1])
        self.assert_retained(workflow_id, reason)
        self.assertEqual(len(self.engine.live), 1)
        preserved = self.receipts(workflow_id, "target evidence preserved")
        self.assertEqual(len(preserved), 1)
        self.assertIn("complete", preserved[0])

    def own_child_record(self, workflow_id, **overrides):
        record = {"parent_task_id": None, "dependency": False,
                  "repo": self.record(workflow_id)["workspace_lease"]["path_realpath"],
                  "task_id": "task-started-1", "recorded_status": "ACTIVE", "role": None,
                  "workspace_id": "ws-started-1", "agents": dict(AGENTS)}
        record.update(overrides)
        return record

    def test_OW35_canonical_conflicting_child_evidence_appearing_before_the_close(self):
        """A record naming this lease and the bound task but ANOTHER runtime
        appears after the proof, during the close-time read: no canonical
        start establishes it — never overridden, nothing closed, on this pass
        or the next."""
        mission_id, revision, workflow_id = self.resumed_and_delivered()

        def appears():
            self.spawn_record_overrides = {"records": [self.own_child_record(
                workflow_id, workspace_id="ws-another")]}
        with self.during_the_close_time_listing(appears) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "at the close: child evidence names this workflow's lease with 1"
            " record(s) no canonical start of it establishes exactly (task, workspace:"
            " 'task-started-1' 'ws-another')", fired)
        self.release_pass()
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        self.assertEqual(self.released(workflow_id), (False, True))

    def test_OW35b_canonical_an_exact_record_appearing_late_is_corroboration(self):
        """Positive control for OW35: a late record stating EXACTLY the
        identity the canonical starts establish (task, workspace, agents) is
        legitimate history, not a conflict — the close proceeds once."""
        mission_id, revision, workflow_id = self.resumed_and_delivered()

        def appears():
            self.spawn_record_overrides = {"records": [self.own_child_record(workflow_id)]}
        with self.during_the_close_time_listing(appears) as fired:
            self.release_pass()
        self.assertEqual(fired, [1])
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))

    def test_OW36_canonical_child_evidence_truncating_before_the_close_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()

        def truncates():
            self.spawn_record_overrides = {"truncated": True}
        with self.during_the_close_time_listing(truncates) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "at the close: the control-side child evidence is not cleanly"
            " readable", fired)

    def assert_degrading_child_evidence_retains(self, degrade):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        with self.during_the_close_time_listing(degrade) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "at the close: the control-side child evidence is not cleanly"
            " readable", fired)

    def test_OW37_canonical_child_evidence_turning_malformed_before_the_close(self):
        self.assert_degrading_child_evidence_retains(lambda: setattr(
            self, "spawn_record_overrides",
            {"state": "malformed", "detail": "child record 0 is not a JSON object"}))

    def test_OW37b_canonical_child_evidence_turning_unreadable_before_the_close(self):
        self.assert_degrading_child_evidence_retains(lambda: setattr(
            self.broker, "_spawn_records", mock.Mock(side_effect=OSError("gone"))))

    def test_OW38_canonical_child_evidence_changing_to_name_the_workflow_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        elsewhere = self.own_child_record(workflow_id, repo="/elsewhere/another-lease",
                                          task_id="task-of-another-spawn",
                                          workspace_id="ws-another")
        self.spawn_record_overrides = {"records": [elsewhere]}

        def changes():
            self.spawn_record_overrides = {"records": [dict(
                elsewhere, repo=self.record(workflow_id)["workspace_lease"]["path_realpath"],
                task_id="task-started-1")]}
        with self.during_the_close_time_listing(changes) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "at the close: child evidence names this workflow's lease with 1"
            " record(s) no canonical start of it establishes exactly (task, workspace:"
            " 'task-started-1' 'ws-another')", fired)

    def test_OW39_unbound_child_evidence_naming_the_lease_before_the_close_closes_nothing(self):
        mission_id, revision, workflow_id = self.unbound_and_expired()

        def appears():
            self.spawn_record_overrides = {"records": [self.own_child_record(
                workflow_id, task_id="task-of-another-spawn")]}
        with self.during_the_close_time_listing(appears) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "at the close: child evidence names this workflow's lease", fired)

    def test_OW40_canonical_a_hold_landing_during_the_close_time_read_closes_nothing(self):
        mission_id, revision, workflow_id = self.resumed_and_expired()
        with self.during_the_close_time_listing(
                lambda: self.service.request_hold(mission_id, "held at the close")) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "the cleanup admission refused at the close (%s"
            % gate_module.PROBLEM_HOLD_ACTIVE, fired)

    def spawned_and_expired(self):
        """An ORDINARY Mission spawn (its real child record written), then its
        authority lapses — BLOCKED, the runtime idle and owed no stop, the
        Mission not terminal (controls apply) — and the delivery-candidate
        retention's deadline passes."""
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched()
        self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"],
                         "task-started-1")
        self.assertFalse(any(mission_state.start_stop_required(s)
                             for s in self.starts(mission_id)))
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        self.clock.advance(wa_record.DELIVERY_CANDIDATE_RETENTION_SECONDS)
        return mission_id, revision, workflow_id

    def test_OW41_ordinary_spawn_a_hold_landing_during_the_close_time_read(self):
        mission_id, revision, workflow_id = self.spawned_and_expired()
        with self.during_the_close_time_listing(
                lambda: self.service.request_hold(mission_id, "held at the close")) as fired:
            self.release_pass()
        self.assert_retained_after_preservation(
            workflow_id, "the cleanup admission refused at the close (%s"
            % gate_module.PROBLEM_HOLD_ACTIVE, fired)
        # Held: the release waits; lifted: it closes once.
        self.release_pass()
        self.assertEqual(self.engine.close_calls, [])
        self.grok_control(mission_id, revision, "resume")
        self.release_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"][-1],
                         ("workspace_session", "ws-started-1", ownership_module.OWNED,
                          True, "closed and absent from a complete fresh listing"))

    def test_OW42_ordinary_spawn_a_mission_source_failing_during_the_close_time_read(self):
        from mission import store as mission_store
        mission_id, revision, workflow_id = self.spawned_and_delivered()
        failing = mock.patch.object(
            self.broker.mission_gate._service, "mission_controls",
            side_effect=mission_store.MissionStoreError("the Mission store is unreadable"))
        self.addCleanup(mock.patch.stopall)
        with self.during_the_close_time_listing(failing.start) as fired:
            self.release_pass()
        failing.stop()
        self.assert_retained_after_preservation(
            workflow_id, "the cleanup admission refused at the close (%s"
            % gate_module.PROBLEM_SOURCE_UNAVAILABLE, fired)
        # The source answers again: it closes once.
        self.release_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))

    # -- follow-up history: DISTINCT actual-return-shaped identities ------------

    TASK_ID_SHAPE = r"^\d{8}-\d{6}-[0-9a-f]{6}$"

    def distinct_engine(self, replace=True):
        """A CONTROLLED engine identity seam: each task hand-over returns a NEW
        task id in the production shape (``herdr.tasks.dispatch_task``:
        ``time.strftime("%Y%m%d-%H%M%S") + "-" +
        hashlib.sha1(text.encode()).hexdigest()[:6]`` — the timestamp a
        separate prefix, sha1 of the TASK TEXT; the timestamps here are
        deterministic and distinct per call), and a follow-up's runtime start
        leaves one of two POST-START SHAPES:

        - ``replace``: a NEW workspace with new agents (the previous id is
          recorded in ``self.replaced``);
        - otherwise REUSE: the SAME workspace id and agent set across
          ordinals, under the new task id — one identity.

        ``herdr.lifecycle.start_herd`` REFUSES (``RuntimeError``, "Existing
        live herd detected") while the previous supervisor is live and
        ``force`` is unset, and production never supplies ``force``. Since
        the Task 8 startup correction the follow-up's start guard RETIRES the
        earlier runtime first (``TargetBroker._retire_predecessor``: proven,
        admitted, closed once, observed absent), so by the time this start
        runs the previous workspace is already gone and the replace shape is
        what ``start_herd`` produces (a new workspace id). The REUSE shape
        stays a controlled post-condition — ``start_herd`` never re-creates
        an id — for the release to account for. The production start itself
        is exercised by the STARTUP tests (``real_startup``)."""
        engine, harness = self.engine, {}
        self.replaced = []

        def start(plane, repo, **kwargs):
            n = len(engine.starts) + 1
            engine.starts.append(str(repo))
            previous = harness.get(str(repo))
            if previous is not None and not replace:
                # REUSE: the same id and agents again. The follow-up's
                # retirement closed the previous incarnation first, so the
                # start re-creates it (a controlled shape; start_herd itself
                # always creates a new id).
                if not any(w["workspace_id"] == previous["workspace_id"]
                           for w in engine.live):
                    engine.live.append({"workspace_id": previous["workspace_id"],
                                        "agent_names": sorted(previous["agents"].values())})
                return dict(previous)
            if previous is not None:
                engine.live = [w for w in engine.live
                               if w["workspace_id"] != previous["workspace_id"]]
                self.replaced.append(previous["workspace_id"])
            agents = {"supervisor": "sup-%d" % n, "lead": "lead-%d" % n, "pod": "pod-%d" % n}
            harness[str(repo)] = runtime = {"workspace_id": "ws-started-%d" % n,
                                            "agents": agents, "root_pane": "%%%d" % n}
            engine.live.append({"workspace_id": runtime["workspace_id"],
                                "agent_names": sorted(agents.values())})
            return dict(runtime)

        engine.start, engine.dispatch_task = start, self.minted_task_ids()

    def minted_task_ids(self):
        """A task hand-over seam returning the production task-id shape
        (``herdr.tasks.dispatch_task``: ``time.strftime("%Y%m%d-%H%M%S") + "-"
        + sha1(task text)[:6]``, deterministic distinct timestamps here),
        recorded as the observed lease task."""
        engine = self.engine

        def dispatch_task(plane, repo, text, **kwargs):
            engine.tasks.append(text)
            stamp = time.strftime("%Y%m%d-%H%M%S",
                                  time.gmtime(1790000000 + 60 * len(engine.tasks)))
            task_id = stamp + "-" + hashlib.sha1(text.encode()).hexdigest()[:6]
            self.note_hand_over(task_id)
            return {"id": task_id, "status": "ACTIVE"}
        return dispatch_task

    def with_a_follow_up(self, resumed=False, replace=True, prefix=0, after_dispatch=None):
        """An ORDINARY Mission spawn (or, ``resumed``, a resumed initial task
        handover) whose verification requests ONE corrective follow-up, with
        the real child-record writer, the production projection and the
        controlled identity seam (``distinct_engine``: distinct task ids; the
        follow-up's runtime in the modelled REPLACE shape, or ``replace``
        False, the REUSE shape); the corrected mission verified, COMPLETED
        and delivered. Returns the ids and the two settled (task id,
        workspace) pairs. ``prefix``: unrelated history persisted first;
        ``after_dispatch(mission_id, workflow_id)``: run once the initial
        dispatch is bound, before its engineering finishes."""
        self.real_child_records(prefix=prefix)
        self.distinct_engine(replace=replace)
        if resumed:
            mission_id, revision, workflow_id, authority = self.refused_at_the_task_claim()
            self.grok_control(mission_id, revision, "resume")
            self.runtime_pass()
        else:
            mission_id, revision, workflow_id = self.dispatched()
        bound = self.record(workflow_id)["target_engine"]["task_id"]
        if after_dispatch is not None:
            after_dispatch(mission_id, workflow_id)
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        self.runtime_pass()
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="the corrected mission is verified")
        # The follow-up's task (ACTIVE since its hand-over) finishes.
        self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE"), (2, "APPROVE")])
        self.target_task_status = "COMPLETE"
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        tasks = sorted((s["engagement_sequence"], s["settlement"]["identity"]["task_id"],
                        s["settlement"]["identity"]["workspace_id"])
                       for s in self.starts(mission_id) if s["point"] == "task")
        (one, first, ws_one), (two, second, ws_two) = tasks
        # Distinct, production-shaped task ids; the workflow keeps its INITIAL
        # binding; the follow-up's runtime in the modelled shape.
        follow_up_ws = "ws-started-2" if replace else "ws-started-1"
        self.assertEqual((one, two, ws_one, ws_two), (1, 2, "ws-started-1", follow_up_ws))
        self.assertRegex(first, self.TASK_ID_SHAPE)
        self.assertRegex(second, self.TASK_ID_SHAPE)
        self.assertNotEqual(first, second)
        self.assertEqual(self.record(workflow_id)["target_engine"]["task_id"], first)
        self.assertEqual(bound, first)
        self.assertEqual((self.replaced, [w["workspace_id"] for w in self.engine.live]),
                         (["ws-started-1"] if replace else [], [follow_up_ws]))
        # ADDED effect, pinned separately from final cleanup: the follow-up's
        # RETIREMENT closed the earlier runtime exactly once before its start
        # (Task 8 startup correction) — one durable claim and one recorded
        # return, both of dispatch 2 — and NO cleanup has run (the release
        # assertions that follow are the original ones, unchanged).
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])
        self.assertEqual(broker_module._retirement_close_claims(self.record(workflow_id)),
                         ({"ws-started-1": {2}}, {"ws-started-1": {2}}, 0))
        self.assertEqual(self.cleanup_receipts_of(workflow_id), [])
        return mission_id, revision, workflow_id, (first, "ws-started-1"), (second, follow_up_ws)

    def child_rows(self):
        with open(self.children_file()) as handle:
            return [(c["task_id"], c["workspace_id"], sorted(c["agents"].values()))
                    for c in json.load(handle)["children"]]

    def rewrite_children(self, change):
        with open(self.children_file()) as handle:
            document = json.load(handle)
        change(document["children"])
        with open(self.children_file(), "w") as handle:
            json.dump(document, handle)

    def assert_follow_up_released(self, mission_id, workflow_id, canonical, children):
        release = [row for row in self.rows if row[0] == "workspace_session"]
        # EVERY owned identity accounted for (the modelled replace shape):
        # the initial workspace observed absent (nothing to close), the
        # follow-up's live one closed exactly once and observed absent — only
        # then the reclaim.
        self.assertEqual(release, [
            ("workspace_session", "ws-started-1", ownership_module.OWNED, True,
             "absent from a complete fresh listing; nothing to close"),
            ("workspace_session", "ws-started-2", ownership_module.OWNED, True,
             "closed and absent from a complete fresh listing")])
        # One close per identity over the WHOLE loop: the earlier runtime by
        # the follow-up's retirement, the follow-up's by the release.
        self.assertEqual((self.engine.close_calls, self.engine.live),
                         (["ws-started-1", "ws-started-2"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual(len(self.spawn_requests), 2)
        receipts = self.cleanup_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        self.assertIn("0 unprovable, 0 failed", receipts[0])
        self.assertEqual(self.children_bytes(), children)
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual(self.engine.close_calls, ["ws-started-1", "ws-started-2"])
        self.assertEqual(len(self.cleanup_receipts(workflow_id)), 1)

    def test_FU1_an_ordinary_spawns_follow_up_history_is_accounted_and_released(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()
        # The real writer appended one record per SPAWN, each under the task
        # id the engine returned for it.
        self.assertEqual(self.child_rows(), [
            (initial[0], "ws-started-1", ["lead-1", "pod-1", "sup-1"]),
            (follow_up[0], "ws-started-2", ["lead-2", "pod-2", "sup-2"])])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        self.assert_follow_up_released(mission_id, workflow_id, canonical, children)

    def test_FU3_the_reuse_shape_is_one_identity_closed_once(self):
        """The REUSE shape (``_canonical_binding``: one workspace id reused
        across ordinals carries one agent set — one identity): both spawns'
        records name the same workspace and agents under their own task ids;
        the release closes that one workspace exactly once. (The follow-up's
        retirement closed the EARLIER incarnation before its start; the
        controlled start re-created the same id — start_herd itself never
        does — so the id is closed once per incarnation.)"""
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            replace=False)
        self.assertEqual(self.child_rows(), [
            (initial[0], "ws-started-1", ["lead-1", "pod-1", "sup-1"]),
            (follow_up[0], "ws-started-1", ["lead-1", "pod-1", "sup-1"])])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        self.assert_two_incarnations_of_one_id(mission_id, workflow_id)
        self.release_pass()
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"], [
            ("workspace_session", "ws-started-1", ownership_module.OWNED, True,
             "closed and absent from a complete fresh listing")])
        # One close per INCARNATION (see ``assert_two_incarnations_of_one_id``):
        # the retirement's of ordinal 1's, the release's of ordinal 2's.
        self.assertEqual((self.engine.close_calls, self.engine.live),
                         (["ws-started-1", "ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual((self.engine_counts(), len(self.spawn_requests)), ((2, 1, 1), 2))
        self.assertIn("0 unprovable, 0 failed", self.cleanup_receipts(workflow_id)[-1])
        self.assertEqual(self.children_bytes(), children)
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)

    def test_FUN6_the_reuse_shape_with_a_record_of_another_agent_set_conflicts(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            replace=False)
        self.rewrite_children(lambda children: children[1].update(
            agents={"supervisor": "sup-9", "lead": "lead-9", "pod": "pod-9"}))
        self.release_pass()
        self.assert_retained(workflow_id, "no canonical start of it establishes exactly"
                             " (task, workspace: %r 'ws-started-1')" % follow_up[0],
                             closes=["ws-started-1"])
        self.assertEqual([w["workspace_id"] for w in self.engine.live], ["ws-started-1"])
        self.assertEqual(self.engine.close_calls, ["ws-started-1"])

    def test_FU2_a_resumed_handovers_follow_up_history_is_accounted_and_released(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            resumed=True)
        # The resumed initial handover wrote no record; the follow-up SPAWN did.
        self.assertEqual(self.child_rows(), [
            (follow_up[0], "ws-started-2", ["lead-2", "pod-2", "sup-2"])])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        self.release_pass()
        self.assert_follow_up_released(mission_id, workflow_id, canonical, children)

    def assert_follow_up_retained(self, workflow_id, reason):
        # (The one close is the follow-up's retirement of the earlier runtime.)
        self.assert_retained(workflow_id, reason, closes=["ws-started-1"])
        self.assertEqual([w["workspace_id"] for w in self.engine.live], ["ws-started-2"])
        self.assertEqual(self.engine_counts(), (2, 1, 1))

    def test_FUN1_an_unexplained_same_lease_record_is_never_overridden(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()
        self.rewrite_children(lambda children: children.append(dict(
            children[1], task_id="20260926-091500-c0ffee", workspace_id="ws-started-9",
            agents={"supervisor": "sup-9", "lead": "lead-9", "pod": "pod-9"})))
        for _ in range(2):
            self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "with 1 record(s) no canonical start of it establishes exactly"
            " (task, workspace: '20260926-091500-c0ffee' 'ws-started-9')")

    def test_FUN2_a_record_of_a_canonical_task_naming_another_runtime_conflicts(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()
        self.rewrite_children(lambda children: children[1].update(workspace_id="ws-started-9"))
        self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "no canonical start of it establishes exactly (task, workspace:"
            " %r 'ws-started-9')" % follow_up[0])

    def test_FUN3_the_follow_up_spawns_missing_record_is_never_read_as_clean(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()
        self.rewrite_children(lambda children: children.pop(1))
        self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "the child record a spawn of this workflow writes is absent"
            " (task(s) %r)" % follow_up[0])

    def test_FUN4_a_duplicated_record_is_ambiguous(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()
        self.rewrite_children(lambda children: children.append(dict(children[1])))
        self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "with 2 record(s) no canonical start of it establishes exactly")

    def test_FUN5_an_unexplained_record_appearing_at_the_close_closes_nothing(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up()

        def appears():
            self.rewrite_children(lambda children: children.append(dict(
                children[1], task_id="20260926-091500-c0ffee")))
        with self.during_the_close_time_listing(appears) as fired:
            self.release_pass()
        self.assertEqual(fired, [1])
        self.assert_follow_up_retained(
            workflow_id, "at the close: child evidence names this workflow's lease with 1"
            " record(s) no canonical start of it establishes exactly (task, workspace:"
            " '20260926-091500-c0ffee' 'ws-started-2')")
        self.assertEqual(len(self.receipts(workflow_id, "target evidence preserved")), 1)

    # -- Task 8 cap correction, production composition: the REAL writer appends
    # -- this workflow's records AFTER 40 unrelated ones; the Broker reads them
    # -- through the PRODUCTION projection, scoped to the lease.

    PREFIX = 40

    def scoped(self, lease):
        return self.spawn_records(self.control, relevant=broker_module._names_lease(lease))

    def test_CAP0_every_release_read_of_the_spawn_records_is_scoped_to_the_lease(self):
        mission_id, revision, workflow_id = self.resumed_and_delivered()
        lease = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        del self.spawn_record_scopes[:]
        self.release_pass()
        self.assertEqual(self.released(workflow_id), (True, False))
        # The proof, the close-time recheck of its one identity and — Task 8
        # R21-C (C-1) — the destructive boundary's read-only session proof
        # (`_sessions_absent_now`, after the hold reads): all three scoped,
        # each to THIS lease.
        self.assertEqual(len(self.spawn_record_scopes), 3)
        for rule in self.spawn_record_scopes:
            self.assertIsNotNone(rule)
            self.assertIs(rule(lease), True)
            self.assertIs(rule(os.path.join(os.path.dirname(lease), "wf-m-another")), False)

    def test_CAP1_relevant_history_beyond_an_unrelated_prefix_is_accounted_and_released(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            prefix=self.PREFIX)
        lease = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        rows = self.child_rows()
        self.assertEqual(len(rows), self.PREFIX + 2)
        self.assertEqual(rows[self.PREFIX:], [
            (initial[0], "ws-started-1", ["lead-1", "pod-1", "sup-1"]),
            (follow_up[0], "ws-started-2", ["lead-2", "pod-2", "sup-2"])])
        # The DEFAULT projection is truncated in file order and never reaches
        # this workflow's records (the defect's precondition) ...
        unscoped = self.spawn_records(self.control)
        self.assertEqual((unscoped["state"], unscoped["count"], unscoped["truncated"],
                          len(unscoped["listed"])), ("available", self.PREFIX + 2, True, 32))
        self.assertNotIn(lease, [r["repo"] for r in unscoped["listed"]])
        # ... the lease-scoped one holds exactly them, complete.
        scoped = self.scoped(lease)
        self.assertEqual((scoped["state"], scoped["count"], scoped["truncated"],
                          scoped["detail"], scoped.get("scope")),
                         ("available", 2, False, None, {"records": self.PREFIX + 2}))
        self.assertEqual([(r["repo"], r["task_id"], r["workspace_id"], r["agents"])
                          for r in scoped["listed"]], [
            (lease, initial[0], "ws-started-1",
             {"supervisor": "sup-1", "lead": "lead-1", "pod": "pod-1"}),
            (lease, follow_up[0], "ws-started-2",
             {"supervisor": "sup-2", "lead": "lead-2", "pod": "pod-2"})])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        self.assert_follow_up_released(mission_id, workflow_id, canonical, children)

    def test_CAP2_a_differently_tasked_relevant_record_beyond_the_prefix_retains(self):
        """The scope is the lease, not the task: a record of this lease under
        a task no canonical start holds, past the unrelated prefix, is seen
        at the proof and never overridden."""
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            prefix=self.PREFIX)
        self.rewrite_children(lambda children: children.append(dict(
            children[-1], task_id="20260926-091500-c0ffee")))
        self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "%s: child evidence names this workflow's lease with 1 record(s)"
            " no canonical start of it establishes exactly (task, workspace:"
            " '20260926-091500-c0ffee' 'ws-started-2')"
            % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN)
        self.assertEqual(len(self.child_rows()), self.PREFIX + 3)

    def test_CAP3_late_conflicting_relevant_evidence_beyond_the_prefix_retains_at_the_close(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            prefix=self.PREFIX)

        def appears():
            self.rewrite_children(lambda children: children.append(dict(
                children[-1], task_id="20260926-091500-c0ffee")))
        with self.during_the_close_time_listing(appears) as fired:
            self.release_pass()
        self.assertEqual(fired, [1])
        self.assert_follow_up_retained(
            workflow_id, "at the close: child evidence names this workflow's lease with 1"
            " record(s) no canonical start of it establishes exactly (task, workspace:"
            " '20260926-091500-c0ffee' 'ws-started-2')")
        self.assertEqual(len(self.receipts(workflow_id, "target evidence preserved")), 1)
        self.assertEqual(len(self.child_rows()), self.PREFIX + 3)

    def test_CAP4_a_genuinely_over_bound_relevant_set_retains_truthfully(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            prefix=self.PREFIX)
        self.rewrite_children(lambda children: children.extend(dict(
            children[-1], task_id="20260926-0915%02d-c0ffee" % n) for n in range(31)))
        self.release_pass()
        self.assert_follow_up_retained(
            workflow_id, "%s: the control-side child evidence is not cleanly readable, so"
            " its absence cannot be established (state available, truncated True:"
            " relevant spawn records truncated to 32 of 33 (%d records in the file))"
            % (broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN, self.PREFIX + 33))


    # -- Task 8 input-size correction: the REAL projection past its whole-file bound --
    # (the control repository's ``children.json`` holds more than
    # ``_OBSERVE_MAX_FILE_BYTES`` of VALID UNRELATED history; the lease-scoped
    # read is incremental, the unscoped one still refuses)

    PAST_THE_BOUND = 2400       # ≈ 1.10 MB of writer-format unrelated records
    NEAR_THE_BOUND = 2200       # ≈ 1.01 MB, padded to an EXACT size below

    def spawned_and_delivered_past(self, prefix):
        """An ORDINARY Mission spawn whose real child record lands AFTER
        ``prefix`` unrelated records, carried to a delivered PR."""
        self.real_child_records(prefix=prefix)
        mission_id, revision, workflow_id = self.dispatched()
        rows = self.child_rows()
        self.assertEqual(len(rows), prefix + 1)
        self.assertEqual(rows[-1], ("task-started-1", "ws-started-1", sorted(AGENTS.values())))
        self.engineering_finishes(workflow_id)
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        return mission_id, revision, workflow_id

    def children_size(self):
        return os.path.getsize(self.children_file())

    def pad_children_to(self, size):
        """``children.json`` re-serialized EXACTLY as the writer serializes
        it, with an UNRELATED record's extra field padded so the file is
        exactly ``size`` bytes (the relevant record's text is unchanged)."""
        with open(self.children_file()) as handle:
            document = json.load(handle)
        document["children"][0]["pad"] = ""
        # Drop UNRELATED prefix records (never the relevant last one) until
        # the file is below the target, then pad up to it exactly.
        while True:
            length = len((json.dumps(document, indent=2) + "\n").encode("ascii"))
            if length <= size:
                break
            per_record = length // len(document["children"])
            drop = max(1, (length - size) // per_record)
            self.assertGreater(len(document["children"]), drop + 1)
            del document["children"][1:1 + drop]
        missing = size - len((json.dumps(document, indent=2) + "\n").encode("ascii"))
        self.assertGreaterEqual(missing, 0)
        document["children"][0]["pad"] = "x" * missing
        data = (json.dumps(document, indent=2) + "\n").encode("ascii")
        self.assertEqual(len(data), size)
        with open(self.children_file(), "wb") as handle:
            handle.write(data)

    def assert_released_once(self, mission_id, workflow_id):
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        # Proven from the canonical starts with the relevant child record
        # cross-checked EXACTLY (it lies past the bound), closed once at a
        # fresh admitted boundary, OBSERVED absent, then reclaimed.
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"],
                         [("workspace_session", "ws-started-1", ownership_module.OWNED,
                           True, "closed and absent from a complete fresh listing")])
        self.assertEqual((self.engine.close_calls, self.engine.live), (["ws-started-1"], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        self.assertEqual((len(self.engine.starts), len(self.engine.tasks),
                          len(self.spawn_requests)), (1, 1, 1))
        receipts = self.cleanup_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        self.assertIn("0 unprovable, 0 failed", receipts[0])
        self.assertEqual(self.children_bytes(), children)       # read, never rewritten
        self.assertEqual(self.canonical_snapshot(mission_id), canonical)
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, len(self.cleanup_receipts(workflow_id))),
                         (["ws-started-1"], 1))

    def test_IR1_past_the_input_bound_the_follow_up_retires_and_the_release_proceeds(self):
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            prefix=self.PAST_THE_BOUND)
        from herdr.observe import _OBSERVE_MAX_FILE_BYTES
        size = self.children_size()
        self.assertGreater(size, _OBSERVE_MAX_FILE_BYTES)
        lease = self.record(workflow_id)["workspace_lease"]["path_realpath"]
        # The UNSCOPED projection keeps its whole-file refusal ...
        self.assertEqual(self.spawn_records(self.control), {
            "state": "unreadable", "count": None, "truncated": False, "listed": [],
            "detail": "children.json is %d bytes (observation limit %d)"
                      % (size, _OBSERVE_MAX_FILE_BYTES)})
        # ... the lease-scoped one — the follow-up's retirement and the
        # release both read it — holds exactly this workflow's two records.
        scoped = self.scoped(lease)
        self.assertEqual((scoped["state"], scoped["count"], scoped["truncated"],
                          scoped["detail"], scoped.get("scope")),
                         ("available", 2, False, None, {"records": self.PAST_THE_BOUND + 2}))
        self.assertEqual([(r["task_id"], r["workspace_id"]) for r in scoped["listed"]],
                         [(initial[0], "ws-started-1"), (follow_up[0], "ws-started-2")])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        self.assert_follow_up_released(mission_id, workflow_id, canonical, children)

    def released_at_exact_size(self, size):
        mission_id, revision, workflow_id = self.spawned_and_delivered_past(
            self.NEAR_THE_BOUND)
        self.pad_children_to(size)
        unscoped = self.spawn_records(self.control)
        self.assert_released_once(mission_id, workflow_id)
        self.assertEqual(self.children_size(), size)
        return unscoped

    def test_IR2a_one_byte_below_the_bound_the_release_proceeds(self):
        from herdr.observe import _OBSERVE_MAX_FILE_BYTES
        unscoped = self.released_at_exact_size(_OBSERVE_MAX_FILE_BYTES - 1)
        self.assertEqual((unscoped["state"], unscoped["count"], unscoped["truncated"]),
                         ("available", len(self.child_rows()), True))

    def test_IR2b_exactly_at_the_bound_the_release_proceeds(self):
        from herdr.observe import _OBSERVE_MAX_FILE_BYTES
        unscoped = self.released_at_exact_size(_OBSERVE_MAX_FILE_BYTES)
        self.assertEqual((unscoped["state"], unscoped["count"], unscoped["truncated"]),
                         ("available", len(self.child_rows()), True))

    def test_IR2c_one_byte_above_the_bound_the_release_proceeds(self):
        from herdr.observe import _OBSERVE_MAX_FILE_BYTES
        unscoped = self.released_at_exact_size(_OBSERVE_MAX_FILE_BYTES + 1)
        self.assertEqual((unscoped["state"], unscoped["detail"]),
                         ("unreadable", "children.json is %d bytes (observation limit %d)"
                          % (_OBSERVE_MAX_FILE_BYTES + 1, _OBSERVE_MAX_FILE_BYTES)))

    def rewrite_as_writer(self, change):
        """``change`` applied to the records, the file re-serialized EXACTLY as
        the writer serializes it (``json.dumps(…, indent=2) + "\\n"``) — so it
        stays past the bound (a compact rewrite would shrink it below)."""
        with open(self.children_file()) as handle:
            document = json.load(handle)
        change(document["children"])
        with open(self.children_file(), "w") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")

    def retained_past_the_bound(self, change, reason, at_close=False):
        """Past the bound: ``change`` alters the evidence (before the release,
        or — ``at_close`` — at its close-time recheck); the release RETAINS
        with ``reason``, closing nothing, and never rewrites the file (its
        size and modification time unchanged by the release)."""
        from herdr.observe import _OBSERVE_MAX_FILE_BYTES
        mission_id, revision, workflow_id = self.spawned_and_delivered_past(
            self.PAST_THE_BOUND)
        path = self.children_file()
        if at_close:
            with self.during_the_close_time_listing(change) as fired:
                self.release_pass()
            self.assertEqual(fired, [1])
        else:
            change()
            before = os.stat(path)
            self.release_pass()
            after = os.stat(path)
            self.assertEqual((after.st_size, after.st_mtime_ns),
                             (before.st_size, before.st_mtime_ns))
        self.assertGreater(os.stat(path).st_size, _OBSERVE_MAX_FILE_BYTES)
        self.assert_retained(workflow_id, reason)
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), ([], 1))
        return workflow_id

    UNREADABLE = ("%s: the control-side child evidence is not cleanly readable, so its"
                  " absence cannot be established (state %%s, truncated %%s: %%s)"
                  % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN)

    def test_IR3_late_conflicting_relevant_evidence_past_the_bound_retains_at_the_close(self):
        def appears():
            self.rewrite_as_writer(lambda children: children.append(dict(
                children[-1], task_id="20260926-091500-c0ffee")))
        workflow_id = self.retained_past_the_bound(
            appears, "at the close: child evidence names this workflow's lease with 1"
            " record(s) no canonical start of it establishes exactly (task, workspace:"
            " '20260926-091500-c0ffee' 'ws-started-1')", at_close=True)
        self.assertEqual(len(self.receipts(workflow_id, "target evidence preserved")), 1)

    def test_IR4_a_malformed_relevant_record_past_the_bound_retains(self):
        self.retained_past_the_bound(
            lambda: self.rewrite_as_writer(lambda children: children[-1].update(task_id=None)),
            self.UNREADABLE % ("malformed", False, "child record %d has malformed identity"
                               " fields" % self.PAST_THE_BOUND))

    def test_IR5_undecidable_relevance_past_the_bound_retains(self):
        self.retained_past_the_bound(
            lambda: self.rewrite_as_writer(lambda children: children.append(
                dict(children[0], repo=None))),
            self.UNREADABLE % ("malformed", False, "child record %d names no repository, so"
                               " its relevance cannot be decided" % (self.PAST_THE_BOUND + 1)))

    def test_IR6_a_read_failure_past_the_bound_retains(self):
        path = self.children_file()
        self.addCleanup(lambda: os.path.exists(path) and os.chmod(path, 0o644))
        self.retained_past_the_bound(
            lambda: os.chmod(path, 0),
            self.UNREADABLE % ("unreadable", False, "children.json: PermissionError"))

    def test_IR6b_an_undecodable_byte_past_the_bound_retains(self):
        def corrupt():
            with open(self.children_file(), "rb") as handle:
                raw = handle.read()
            cut = raw.index(b"wf-m-old-")
            with open(self.children_file(), "wb") as handle:
                handle.write(raw[:cut] + b"\xff" + raw[cut + 1:])
        self.retained_past_the_bound(
            corrupt, self.UNREADABLE % ("unreadable", False,
                                        "children.json could not be decoded"))

    def test_IR7_a_truncated_file_past_the_bound_retains(self):
        def cut():
            with open(self.children_file(), "rb") as handle:
                raw = handle.read()
            with open(self.children_file(), "wb") as handle:
                handle.write(raw[:int(len(raw) * 0.99)])
        self.retained_past_the_bound(
            cut, self.UNREADABLE % ("malformed", False, "children.json is not valid JSON"))

    def test_IR8_an_over_bound_relevant_set_past_the_bound_retains_truthfully(self):
        self.retained_past_the_bound(
            lambda: self.rewrite_as_writer(lambda children: children.extend(dict(
                children[-1], task_id="20260926-0915%02d-c0ffee" % n) for n in range(32))),
            self.UNREADABLE % ("available", True, "relevant spawn records truncated to 32"
                               " of 33 (%d records in the file)" % (self.PAST_THE_BOUND + 33)))

    # -- Task 8 R19-1: the REAL writer's history, through retirement and cleanup --
    # (another child Herdr spawned by the SAME control repository meets a fault
    # while this workflow's record is already in the history; the follow-up's
    # retirement and the final cleanup both prove this workflow's runtimes from
    # that history)

    def another_spawn(self):
        """ANOTHER child Herdr spawned by this control repository through the
        REAL writer (``HerdrControlPlane.spawn_child``; only ``spawn`` doubled —
        no engine effect). Returns (the exception it raised or None, the
        targets ``spawn`` was invoked for)."""
        spawned = []

        def spawn(plane, repo, *, task, **kwargs):
            spawned.append(str(repo))
            return {"repo": str(repo), "initialization": None,
                    "runtime": {"workspace_id": "ws-another",
                                "agents": {"supervisor": "another-sup"}},
                    "task": {"id": "20260924-120000-a0a0a0", "status": "ACTIVE"},
                    "policy": {}}
        target = os.path.join(os.path.realpath(self.workspaces), "another-child")
        with mock.patch.object(HerdrControlPlane, "spawn", spawn):
            try:
                REAL_SPAWN_CHILD(HerdrControlPlane(), self.control, target,
                                 task="another objective")
            except Exception as exc:                      # noqa: BLE001
                return exc, spawned
        return None, spawned

    @contextlib.contextmanager
    def transient_history_read_failure(self):
        """The FIRST read of ``children.json`` fails (EIO); later reads succeed."""
        real = Path.read_text
        fired = []

        def read_text(path, *args, **kwargs):
            if path.name == "children.json" and not fired:
                fired.append(str(path))
                raise OSError(errno.EIO, "transient history read failure")
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read_text):
            yield fired

    @contextlib.contextmanager
    def interrupted_history_write(self):
        """The write of the new history is interrupted before it completes,
        whichever primitive writes it: a direct rewrite of ``children.json``
        stops half-way (truncated, half written, EIO); a replace onto it stops
        before the rename (EIO)."""
        real_write_text, real_replace = Path.write_text, os.replace
        fired = []

        def write_text(path, data, *args, **kwargs):
            if path.name == "children.json":
                fired.append("rewrite")
                real_write_text(path, data[:len(data) // 2], *args, **kwargs)
                raise OSError(errno.EIO, "interrupted history write")
            return real_write_text(path, data, *args, **kwargs)

        def replace(source, destination, *args, **kwargs):
            if os.path.basename(str(destination)) == "children.json":
                fired.append("replace")
                raise OSError(errno.EIO, "interrupted history write")
            return real_replace(source, destination, *args, **kwargs)
        with mock.patch.object(Path, "write_text", write_text), \
                mock.patch.object(os, "replace", replace):
            yield fired

    def observed_rows(self):
        """The history's (task id, workspace id) rows — or, observed rather
        than raised, what made it unreadable."""
        try:
            return [row[:2] for row in self.child_rows()]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return "unreadable: %s" % exc.__class__.__name__

    def faulted_then_released(self, fault):
        """This workflow dispatched (its record written by the real writer);
        ANOTHER spawn under ``fault``; then the follow-up (its retirement
        proves the initial runtime from the history) and the delivery, then
        ONE release pass. Returns what the other spawn left: (fault fired,
        exception, targets spawned, history bytes unchanged, rows after)."""
        seen = {}

        def after_dispatch(mission_id, workflow_id):
            before = self.children_bytes()
            with fault() as fired:
                raised, spawned = self.another_spawn()
            seen.update(fired=list(fired), raised=raised, spawned=spawned,
                        intact=self.children_bytes() == before, rows=self.observed_rows())
        mission_id, revision, workflow_id, initial, follow_up = self.with_a_follow_up(
            after_dispatch=after_dispatch)
        # The history names both runtimes of this workflow — the one the other
        # spawn's fault met, and the follow-up's appended after it.
        self.assertEqual([row[:2] for row in self.child_rows()],
                         [(initial[0], "ws-started-1"), (follow_up[0], "ws-started-2")])
        canonical, children = self.canonical_snapshot(mission_id), self.children_bytes()
        actions, outcomes = self.release_pass()
        self.assertEqual(actions.count(self.RELEASE), 1)
        self.assert_follow_up_released(mission_id, workflow_id, canonical, children)
        return seen, initial

    def test_WR1_a_transient_history_read_failure_erases_no_record(self):
        """(R19-1) The other spawn's history read fails ONCE: the real writer
        REFUSES before invoking ``spawn`` and leaves the history byte-identical
        — so the follow-up's retirement closes the initial runtime exactly once
        (``with_a_follow_up``) and the release accounts for both runtimes, one
        close each, 0 unprovable."""
        seen, initial = self.faulted_then_released(self.transient_history_read_failure)
        self.assertEqual(len(seen["fired"]), 1)
        self.assertIsInstance(seen["raised"], ChildHistoryError)
        self.assertNotIsInstance(seen["raised"], ChildRecordNotAppended)
        self.assertEqual(str(seen["raised"]),
                         "Child-spawn history %s is unreadable (OSError); it is left as it is"
                         " on disk. Nothing was spawned and nothing appended." % seen["fired"][0])
        self.assertEqual(seen["spawned"], [])
        self.assertTrue(seen["intact"])
        self.assertEqual(seen["rows"], [(initial[0], "ws-started-1")])

    def test_WR2_an_interrupted_history_write_leaves_the_history_whole(self):
        """(R19-1) The other spawn's WRITE is interrupted: the real writer's
        document is written aside and replaced atomically, so the interruption
        leaves the previous history byte-identical — the retirement and the
        release proceed exactly as above. The other child WAS spawned and has
        no record: raised as a PARTIAL effect (``ChildRecordNotAppended``),
        never as a pre-effect refusal, and no record is synthesised for it."""
        seen, initial = self.faulted_then_released(self.interrupted_history_write)
        self.assertEqual(seen["fired"], ["replace"])
        self.assertIsInstance(seen["raised"], ChildRecordNotAppended)
        self.assertNotIsInstance(seen["raised"], ChildHistoryError)
        self.assertEqual(str(seen["raised"]), (
            "PARTIAL EFFECT: child task 20260924-120000-a0a0a0 (workspace ws-another) WAS"
            " spawned at %s; whether it is still running is not known here. Its record was"
            " NOT appended to %s: the failure came before the new history replaced the old"
            " one, which is left as it was on disk ([Errno 5] interrupted history write)."
            " No record was synthesised: its ownership is not provable from the history."
            % (seen["spawned"][0], self.children_file())))
        self.assertEqual(len(seen["spawned"]), 1)
        self.assertTrue(seen["intact"])
        self.assertEqual(seen["rows"], [(initial[0], "ws-started-1")])


def ws_problem(name):
    from target_runtime import workspace_ownership as ws_module
    return getattr(ws_module, name)


# ======================================================================
# Row 8 (with 1, 3, 5) — the working path end to end
# ======================================================================


# ======================================================================
# Task 8 STARTUP correction — a corrective follow-up's runtime start through
# the PRODUCTION startup composition: the real ``HerdrControlPlane.start`` →
# the real ``herdr.lifecycle.start_herd`` (its persisted-state read, the
# live-supervisor refusal, the stale-state ``workspace close``, the unlink
# and the creation) over the controlled herdr host (``_HerdrHost``: the CLI
# seams only), the real child-record writer and production-shaped task ids.
# Every close is visible: the Broker's (``engine.close_calls``) and every one
# the lifecycle issues (``host.native_closes``).
# ======================================================================


class StartupRetirementTests(_ResumptionCase):

    deliver = OwnershipReleaseTests.deliver
    release_pass = OwnershipReleaseTests.release_pass
    released = OwnershipReleaseTests.released
    minted_task_ids = OwnershipReleaseTests.minted_task_ids

    UNPROVEN = broker_module.PROBLEM_PREDECESSOR_UNPROVEN
    PENDING = broker_module.PROBLEM_PREDECESSOR_PENDING
    UNCERTAIN = broker_module.PROBLEM_PREDECESSOR_UNCERTAIN
    FOREIGN_TASK = "20990101-000000-f0f0f0"

    # -- the production composition --------------------------------------------

    def patch_lifecycle(self, id_format="w-host-%d"):
        self.host = _HerdrHost(self.engine, id_format)
        for name in ("run", "jrun", "split", "start_agent", "prompt",
                     "bootstrap_text", "establish_role_bindings"):
            patcher = mock.patch.object(lifecycle_module, name, getattr(self.host, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lifecycle_output = io.StringIO()
        return self.host

    def real_startup(self, id_format="w-host-%d"):
        """EVERY runtime start runs the REAL ``HerdrControlPlane.start``
        (``start_herd``) over the host, after the target's initialization
        (what production ``spawn`` does first); ``before_start`` hooks run
        just before it — another party acting between the retirement and the
        start."""
        host = self.patch_lifecycle(id_format)
        engine = self.engine
        self.before_start = []

        def start(plane, repo, **kwargs):
            engine.starts.append(str(repo))
            for hook in list(self.before_start):
                hook()
            host.initialize(repo)
            with contextlib.redirect_stdout(self.lifecycle_output):
                return REAL_START(plane, repo, **kwargs)
        engine.start = start
        engine.dispatch_task = self.minted_task_ids()
        self.real_child_records()
        return host

    def ws(self, n):
        return self.host.id_format % n

    def lease(self, workflow_id):
        return self.record(workflow_id)["workspace_lease"]["path_realpath"]

    def runtime_json(self, workflow_id):
        return os.path.join(self.lease(workflow_id), ".herd", "state", "runtime.json")

    def state_bytes(self, workflow_id):
        with open(self.runtime_json(workflow_id), "rb") as handle:
            return handle.read()

    def rewrite_state(self, workflow_id, change):
        document = json.loads(self.state_bytes(workflow_id))
        change(document)
        data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        with open(self.runtime_json(workflow_id), "wb") as handle:
            handle.write(data)
        return data

    def archive_path(self, workflow_id, sequence=2):
        return preservation_module.runtime_state_path(self.store_dir, workflow_id, sequence)

    def archive(self, workflow_id, sequence=2):
        return preservation_module.inspect_runtime_state(self.store_dir, workflow_id, sequence)

    def gate_receipts(self, workflow_id):
        return self.receipts(workflow_id, "mission gate")

    def last_gate_receipt(self, workflow_id):
        """The latest hold/block receipt — a placeholder when there is none,
        so its absence fails an assertion instead of erroring."""
        receipts = self.gate_receipts(workflow_id)
        return receipts[-1] if receipts else "(no mission gate receipt)"

    def retirement_trail(self, workflow_id):
        """Every retirement receipt, its marker and parenthetical dropped."""
        marker = broker_module.RETIREMENT_RECEIPT_MARKER + ": "
        return [summary[len(marker):].split(" (", 1)[0]
                for summary in self.retirement_receipts(workflow_id)]

    def up_to_the_follow_up(self):
        """REAL startup: the initial spawn created workspace 1 (no persisted
        state before it: no probe, no close); the engine finishes; the
        verification requests ONE corrective follow-up — the NEXT pass
        performs it."""
        mission_id, revision, workflow_id = self.dispatched()
        self.assertEqual((self.host.created, self.host.native_closes, self.host.probes),
                         ([self.ws(1)], [], []))
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        return mission_id, revision, workflow_id

    def corrected_work_finishes(self, workflow_id, rounds):
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "t-v2", "role": "verification", "process_id": 2},
            detail="the corrected mission is verified")
        self.write_round(workflow_id, len(rounds), "APPROVE")
        self.listing_with_rounds(rounds)
        self.target_task_status = "COMPLETE"

    def at_the_follow_up_claim(self, change, sequence=2):
        """``change`` at follow-up ``sequence``'s runtime-start claim —
        after the verification asked for it, before the retirement's first
        read."""
        real = broker_module._MissionStartGuard.open

        def open_after(guard, at):
            if guard.dispatch_sequence == sequence and at == "runtime_start":
                change()
            return real(guard, at)
        return mock.patch.object(broker_module._MissionStartGuard, "open", open_after)

    def during_preservation(self, change):
        """``change`` right after the retirement preserved the runtime state
        — after its first task observation, before the first close's
        boundary."""
        real = preservation_module.preserve_runtime_state

        def preserve_then(*args, **kwargs):
            outcome = real(*args, **kwargs)
            change()
            return outcome
        return mock.patch.object(preservation_module, "preserve_runtime_state",
                                 preserve_then)

    def around_the_close(self, before=None, after=None):
        """``before`` / ``after`` around the engine call of every proven close
        (``close_proven_workspace``: after the durable claim)."""
        real = workspace_ownership_module.close_proven_workspace

        def close(*args, **kwargs):
            if before is not None:
                before()
            outcome = real(*args, **kwargs)
            if after is not None:
                after()
            return outcome
        return mock.patch.object(workspace_ownership_module, "close_proven_workspace", close)

    def assert_nothing_retired(self, workflow_id, state, problem, phase):
        """ZERO closes (the Broker's AND the lifecycle's), NO discard (the
        persisted state byte-identical), NO start (one workspace ever
        created, no follow-up task), NO durable close claim, and the claim
        refused with ``problem``."""
        self.assertEqual((self.engine.close_calls, self.host.native_closes), ([], []))
        self.assertEqual(self.state_bytes(workflow_id), state)
        self.assertEqual(self.host.created, [self.ws(1)])
        self.assertEqual(self.engine_counts(), (1, 1, 0))
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"),
                         ["claiming", "claim:retiring", "claim:refused cause=" + problem])
        self.assertEqual(broker_module._retirement_close_claims(self.record(workflow_id)),
                         ({}, {}, 0))
        self.assertEqual(self.record(workflow_id)["phase"], phase)

    def assert_retired_and_started(self, workflow_id, original, closes, trail):
        """The positive production effects of ONE follow-up: the Broker's
        proven ``closes``; the lifecycle issued NO close and probed NO
        supervisor (no stale persisted state reached it); exactly one new
        workspace; the ``original`` state preserved byte-exact before its
        discard; the persisted state now names the new runtime."""
        self.assertEqual(self.engine.close_calls, closes)
        self.assertEqual((self.host.native_closes, self.host.probes), ([], []))
        self.assertNotIn("Cleaning previous harness workspace", self.lifecycle_output.getvalue())
        self.assertEqual(self.host.created, [self.ws(1), self.ws(2)])
        status, archived, detail = self.archive(workflow_id)
        self.assertEqual(status, preservation_module.RUNTIME_STATE_INTACT, detail)
        self.assertEqual(archived["text"].encode("utf-8"), original)
        self.assertEqual(json.loads(self.state_bytes(workflow_id))["workspace_id"], self.ws(2))
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual(self.retirement_trail(workflow_id), trail)
        states = self.claim_states(workflow_id, "claim-runtime_start-2")
        self.assertEqual(states[-4:-1], ["claiming", "claim:retiring", "claiming"])
        self.assertTrue(states[-1].startswith("claim:admitted"), states)
        self.assertEqual(self.released(workflow_id), (False, True))

    def retired_trail(self, closed):
        quoted = ['dispatch 2 close-claimed "%s"' % closed, 'dispatch 2 close-returned "%s"' % closed]
        return quoted + ["dispatch 2 discarding the persisted runtime state",
                         "dispatch 2 retired: closed %s; every earlier runtime observed absent;"
                         " runtime state discarded" % closed]

    # -- the harness exhibits what start_herd does ------------------------------

    def test_ST0_the_host_exhibits_start_herds_stale_state_close_and_live_refusal(self):
        """Non-vacuity: with persisted state naming a workspace whose
        supervisor is NOT live, the REAL ``start_herd`` closes that id —
        unproven — before creating its own (the close this correction must
        account for); with the supervisor live it REFUSES and closes
        nothing."""
        host = self.patch_lifecycle()
        repo = os.path.join(self.base, "sensitivity")
        os.makedirs(os.path.join(repo, ".herd", "state"))
        host.initialize(repo)
        with open(os.path.join(repo, ".herd", "state", "runtime.json"), "w") as handle:
            json.dump({"workspace_id": "w-stale", "agents": {"supervisor": "sup-stale"}}, handle)
        with contextlib.redirect_stdout(self.lifecycle_output):
            runtime = REAL_START(HerdrControlPlane(), repo)
        self.assertEqual(host.probes, [("sup-stale", False)])
        self.assertEqual(host.native_closes, ["w-stale"])
        self.assertIn("Cleaning previous harness workspace w-stale",
                      self.lifecycle_output.getvalue())
        self.assertEqual((host.created, runtime["workspace_id"]), (["w-host-1"], "w-host-1"))
        with self.assertRaisesRegex(RuntimeError, "Existing live herd detected"):
            with contextlib.redirect_stdout(self.lifecycle_output):
                REAL_START(HerdrControlPlane(), repo)
        self.assertEqual(host.probes[-1], (runtime["agents"]["supervisor"], True))
        self.assertEqual((host.native_closes, host.created), (["w-stale"], ["w-host-1"]))

    def test_ST2_without_the_retirement_the_real_start_refuses_the_live_predecessor(self):
        """The REAL precondition the correction exists for: with the
        retirement disabled, follow-up 2's REAL start probes the persisted
        supervisor, finds it live, and refuses — nothing created, nothing
        closed."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        supervisor = json.loads(self.state_bytes(workflow_id))["agents"]["supervisor"]
        with mock.patch.object(broker_module.TargetBroker, "_retire_predecessor",
                               lambda *args, **kwargs: None):
            self.runtime_pass()
        self.assertEqual(self.host.probes, [(supervisor, True)])
        self.assertEqual((self.host.created, self.host.native_closes, self.engine.close_calls),
                         ([self.ws(1)], [], []))
        self.assertEqual(self.engine_counts(), (2, 1, 0))

    # -- the positive path -------------------------------------------------------

    def test_ST1_the_follow_up_retires_its_predecessor_and_the_real_start_proceeds(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        original = self.state_bytes(workflow_id)
        agents = sorted(json.loads(original)["agents"].values())
        self.assertEqual(json.loads(original)["workspace_id"], "w-host-1")
        self.assertEqual(self.host.names("w-host-1"), [agents])
        with self.minted() as actions:
            outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][1][:3], ("dispatch_follow_up", True, None))
        # EXACT production startup effects across the WHOLE follow-up: the
        # retirement's one proven close; the REAL start probed nothing, closed
        # nothing (no stale state reached it) and created one workspace — a new
        # id carrying the SAME per-repository agent names.
        self.assert_retired_and_started(workflow_id, original, ["w-host-1"],
                                        self.retired_trail("w-host-1"))
        self.assertEqual([(w["workspace_id"], w["agent_names"]) for w in self.engine.live],
                         [("w-host-2", agents)])
        # No replay: one capability action, one start and one task of the
        # follow-up, the same markers, engagements and spawn requests.
        self.assertEqual(actions.count(broker_module.ACTION_FOLLOW_UP), 1)
        self.assertEqual((len(self.spawn_requests), self.markers(workflow_id),
                          len(self.engagements(mission_id))), (2, 2, 2))
        self.assertEqual(self.canonical_starts(mission_id), [
            (1, "runtime", "completed"), (1, "task", "completed"),
            (2, "runtime", "completed"), (2, "task", "completed")])
        self.assertEqual(self.cleanup_receipts_of(workflow_id), [])
        # The corrected work completes and is delivered; the release (FINAL
        # CLEANUP) closes only the follow-up's runtime — at most one close per
        # proven identity over the whole loop, none by the lifecycle.
        self.corrected_work_finishes(workflow_id, [(1, "APPROVE"), (2, "APPROVE")])
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_COMPLETED)
        self.deliver(mission_id, revision, workflow_id)
        self.release_pass()
        self.assertEqual([row for row in self.rows if row[0] == "workspace_session"], [
            ("workspace_session", "w-host-1", ownership_module.OWNED, True,
             "absent from a complete fresh listing; nothing to close"),
            ("workspace_session", "w-host-2", ownership_module.OWNED, True,
             "closed and absent from a complete fresh listing")])
        self.assertEqual((self.engine.close_calls, self.host.native_closes, self.engine.live),
                         (["w-host-1", "w-host-2"], [], []))
        self.assertEqual(self.released(workflow_id), (True, False))
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.native_closes),
                         (["w-host-1", "w-host-2"], []))
        self.assertEqual(len(self.cleanup_receipts_of(workflow_id)), 1)

    def test_ST3_an_id_reused_after_the_retirement_is_never_closed(self):
        """Another party reuses ``w-host-1`` between the retirement and the
        start: the discarded state leaves the REAL start no stale id, so it
        closes nothing; the release never closes it either."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        original = self.state_bytes(workflow_id)
        foreign = {"workspace_id": "w-host-1", "agent_names": ["another-party"]}
        self.before_start.append(lambda: self.engine.live.append(dict(foreign)))
        self.runtime_pass()
        self.assert_retired_and_started(workflow_id, original, ["w-host-1"],
                                        self.retired_trail("w-host-1"))
        self.assertIn(foreign, self.engine.live)
        self.corrected_work_finishes(workflow_id, [(1, "APPROVE"), (2, "APPROVE")])
        self.runtime_pass()
        self.deliver(mission_id, revision, workflow_id)
        for _ in range(2):
            self.release_pass()
        self.assertEqual((self.engine.close_calls, self.host.native_closes), (["w-host-1"], []))
        self.assertIn(foreign, self.engine.live)
        self.assertEqual(self.released(workflow_id), (False, True))

    def test_ST8_a_legacy_pane_state_resolves_its_owner_and_retires(self):
        """The legacy shape (no ``workspace_id``; the ``<workspace>:``
        supervisor pane) resolves exactly as ``start_herd`` resolves it."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.rewrite_state(workflow_id, lambda document: document.pop("workspace_id"))
        self.assertEqual(json.loads(state)["panes"]["supervisor"], "w-host-1:1")
        self.runtime_pass()
        self.assert_retired_and_started(workflow_id, state, ["w-host-1"],
                                        self.retired_trail("w-host-1"))

    # -- the prior task: fresh identity AND terminal status ------------------------

    def test_ST4_an_active_prior_task_holds_and_waits_without_minting(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        prior = self.handed_over[0]

        def restarted():
            self.target_task_status = "ACTIVE"
        with self.at_the_follow_up_claim(restarted):
            self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.PENDING, wa_record.PHASE_DISPATCHED)
        self.assertEqual(self.archive(workflow_id)[0], preservation_module.RUNTIME_STATE_MISSING)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate hold: %s — the prior hand-over %s is ACTIVE; a runtime"
                         " whose task is still running is never closed" % (self.PENDING, prior))
        # Waiting: re-assessed READ-ONLY each pass — nothing minted, claimed
        # or recorded.
        receipts = list(self.record(workflow_id)["receipts"])
        with self.minted() as actions:
            for _ in range(2):
                self.runtime_pass()
        self.assertEqual(actions, [])
        self.assertEqual([r for r in self.record(workflow_id)["receipts"]
                          if r["bounded_summary"].startswith(("mission", "follow-up"))],
                         [r for r in receipts
                          if r["bounded_summary"].startswith(("mission", "follow-up"))])
        # Observed finished again: the SAME follow-up resumes and retires.
        self.target_task_status = "COMPLETE"
        self.runtime_pass()
        self.assert_retired_and_started(workflow_id, state, ["w-host-1"],
                                        self.retired_trail("w-host-1"))

    def test_ST5_the_same_prior_task_active_again_at_the_close_boundary_closes_nothing(self):
        """Pin 1 (defeats an identity-only check): the task proved terminal
        at the first read is THE prior hand-over, observed ACTIVE again at the
        close's boundary — its id still matches."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        prior = self.handed_over[0]

        def restarted():
            self.target_task_status = "ACTIVE"
        with self.during_preservation(restarted):
            self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.PENDING, wa_record.PHASE_DISPATCHED)
        status, archived, _ = self.archive(workflow_id)
        self.assertEqual((status, archived["text"].encode("utf-8")),
                         (preservation_module.RUNTIME_STATE_INTACT, state))
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate hold: %s — at the effect boundary: the prior hand-over %s"
                         " is ACTIVE; a runtime whose task is still running is never closed"
                         % (self.PENDING, prior))

    def test_ST6_a_foreign_task_at_the_close_boundary_closes_nothing(self):
        """Pin 2 (defeats a status-only check): the lease's task becomes a
        FOREIGN id, still terminal, while the workspace id, its agents and the
        canonical history are all unchanged."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        prior = self.handed_over[0]
        listing = [dict(w) for w in self.engine.live]
        history = self.canonical_starts(mission_id)

        def foreign():
            self.handed_over.append(self.FOREIGN_TASK)
        with self.during_preservation(foreign):
            self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertEqual((self.engine.live, self.canonical_starts(mission_id), self.target_task_status),
                         (listing, history, "COMPLETE"))
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — at the effect boundary: the lease's task is %r,"
                         " not this workflow's prior canonical hand-over %r; a runtime whose task"
                         " is not provably that hand-over is never closed"
                         % (self.UNPROVEN, self.FOREIGN_TASK, prior))

    # -- the persisted state: absent vs existing-but-unprojectable -----------------

    def refused_state(self, change, reason):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.rewrite_state(workflow_id, change)
        self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertEqual(self.archive(workflow_id)[0], preservation_module.RUNTIME_STATE_MISSING)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — the persisted runtime state %s; nothing is"
                         " closed, discarded or started" % (self.UNPROVEN, reason))

    def test_ST7a_state_naming_no_projectable_workspace_refuses(self):
        def unprojectable(document):
            del document["workspace_id"]
            del document["panes"]["supervisor"]
        self.refused_state(unprojectable, "names no projectable workspace id (no"
                           " workspace_id and no '<workspace>:' supervisor pane); its ownership"
                           " cannot be established")

    def test_ST7b_state_naming_a_foreign_supervisor_refuses(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.rewrite_state(workflow_id, lambda document: document["agents"].update(
            supervisor="someone-else"))
        self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — the persisted runtime state names supervisor"
                         " 'someone-else', not an agent of workspace w-host-1" % self.UNPROVEN)

    def test_ST7c_state_with_a_malformed_identity_refuses(self):
        self.refused_state(lambda document: document.update(agents=["sup", "lead"]),
                           "has an agents or panes field that is not an object; its identity"
                           " is not projectable")

    def test_ST7d_state_naming_a_foreign_workspace_refuses(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.rewrite_state(workflow_id, lambda document: document.update(
            workspace_id="w-elsewhere"))
        self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — the persisted runtime state names workspace"
                         " w-elsewhere, which no canonical start of this workflow owns; the"
                         " native start would close it unproven, so nothing is started"
                         % self.UNPROVEN)

    # -- the proof and the child evidence, at the first read and at the close ------

    rewrite_children = OwnershipReleaseTests.rewrite_children

    def unexplained_record(self):
        """A same-lease spawn record the canonical history does not explain."""
        self.rewrite_children(lambda children: children.append(dict(
            children[0], task_id="20260926-091500-c0ffee", workspace_id="w-host-9",
            agents={"supervisor": "sup-9"})))

    UNEXPLAINED = ("with 1 record(s) no canonical start of it establishes exactly (task,"
                   " workspace: '20260926-091500-c0ffee' 'w-host-9')")

    def test_ST19_a_workspace_replaced_between_its_proof_and_its_close_is_never_closed(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)

        def replaced():
            for workspace in self.engine.live:
                if workspace["workspace_id"] == "w-host-1":
                    workspace["agent_names"] = ["another-party"]
        with self.during_preservation(replaced):
            self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — workspace w-host-1 no longer matches its"
                         " proof (replaced or reused by another party); it is never closed"
                         % self.UNPROVEN)

    def test_ST20_an_unexplained_same_lease_record_refuses_the_retirement(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        self.unexplained_record()
        self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        receipt = self.last_gate_receipt(workflow_id)
        self.assertTrue(receipt.startswith("mission gate block: %s — child evidence: "
                                           % self.UNPROVEN), receipt)
        self.assertIn(self.UNEXPLAINED, receipt)

    def test_ST21_late_unexplained_child_evidence_at_the_close_boundary_closes_nothing(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        with self.during_preservation(self.unexplained_record):
            self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        receipt = self.last_gate_receipt(workflow_id)
        self.assertTrue(receipt.startswith(
            "mission gate block: %s — at the effect boundary: child evidence: " % self.UNPROVEN),
            receipt)
        self.assertIn(self.UNEXPLAINED, receipt)

    def test_ST22_a_state_changed_after_its_preservation_is_never_discarded(self):
        """The retirement closed and observed absence, then the persisted state
        it preserved is no longer those bytes: never discarded, nothing
        started."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)

        def changed():
            with open(self.runtime_json(workflow_id), "ab") as handle:
                handle.write(b" ")
        with self.around_the_close(after=changed):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.native_closes), (["w-host-1"], []))
        self.assertEqual(self.state_bytes(workflow_id), state + b" ")
        self.assertEqual((self.host.created, self.engine_counts()), (["w-host-1"], (1, 1, 0)))
        self.assertEqual(self.retirement_trail(workflow_id), [
            'dispatch 2 close-claimed "w-host-1"', 'dispatch 2 close-returned "w-host-1"'])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[-1],
                         "claim:refused cause=" + self.UNPROVEN)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate block: %s — the persisted runtime state changed since it"
                         " was preserved (sha256 %s); it is never discarded unproven"
                         % (self.UNPROVEN, hashlib.sha256(state + b" ").hexdigest()[:12]))

    # -- preservation unproven: nothing closed or discarded ---------------------------

    def planted_archive(self, plant, problem, phase):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        path = self.archive_path(workflow_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        plant(path, workflow_id)

        def fingerprint():
            if os.path.isdir(path):
                return ("directory", sorted(os.listdir(path)))
            with open(path, "rb") as handle:
                return ("file", handle.read())
        before = fingerprint()
        self.runtime_pass()
        self.assert_nothing_retired(workflow_id, state, problem, phase)
        self.assertEqual(fingerprint(), before)         # the prior evidence is kept as it was
        return workflow_id, self.last_gate_receipt(workflow_id)

    def test_ST9a_an_intact_archive_of_other_bytes_is_never_overwritten(self):
        def plant(path, workflow_id):
            text = '{"workspace_id": "w-elsewhere"}\n'
            with open(path, "w") as handle:
                json.dump({"workflow_id": workflow_id, "dispatch_sequence": 2,
                           "sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "size": len(text), "text": text}, handle)
        workflow_id, receipt = self.planted_archive(plant, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertIn("preserve_runtime_state_conflict", receipt)

    def test_ST9b_malformed_evidence_at_the_archive_path_is_never_overwritten(self):
        def plant(path, workflow_id):
            with open(path, "wb") as handle:
                handle.write(b"not an archive")
        workflow_id, receipt = self.planted_archive(plant, self.UNPROVEN, wa_record.PHASE_BLOCKED)
        self.assertIn("preserve_runtime_state_malformed", receipt)

    def test_ST9c_unreadable_evidence_at_the_archive_path_holds(self):
        def plant(path, workflow_id):
            os.makedirs(path)
        workflow_id, receipt = self.planted_archive(plant, self.PENDING, wa_record.PHASE_DISPATCHED)
        self.assertIn("preserve_runtime_state_unreadable", receipt)

    # -- durable at most one CLAIMED close per proven identity ----------------------

    def test_ST10_a_returned_close_whose_absence_is_unobservable_resumes_without_a_second_close(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)

        def listing_fails():
            self.engine.live_error = OSError("listing unavailable")
        with self.around_the_close(after=listing_fails):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.created), (["w-host-1"], ["w-host-1"]))
        self.assertEqual(self.retirement_trail(workflow_id), [
            'dispatch 2 close-claimed "w-host-1"', 'dispatch 2 close-returned "w-host-1"'])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[-1],
                         "claim:refused cause=" + self.UNCERTAIN)
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate hold: %s — a close of workspace w-host-1 was claimed"
                         " durably before its engine call; its engine call is recorded as"
                         " returned; absence is not observable (listing unavailable) — its"
                         " outcome is UNCERTAIN, the close is never re-issued, and the"
                         " retirement waits for its observed absence" % self.UNCERTAIN)
        self.assertEqual(self.state_bytes(workflow_id), state)       # not discarded
        # The listing recovers; a restart; the SAME follow-up is re-assessed:
        # w-host-1 observed absent — it resumes with NO second close.
        self.engine.live_error = None
        self.restart_dirun()
        self.runtime_pass()
        self.assert_retired_and_started(workflow_id, state, ["w-host-1"], [
            'dispatch 2 close-claimed "w-host-1"', 'dispatch 2 close-returned "w-host-1"',
            "dispatch 2 discarding the persisted runtime state",
            "dispatch 2 retired: closed none; every earlier runtime observed absent;"
            " runtime state discarded"])

    def test_ST11_a_returned_close_still_listed_is_never_reissued_across_restarts(self):
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        self.engine.close_leaves_visible = True
        self.runtime_pass()
        uncertain = ("mission gate hold: %s — a close of workspace w-host-1 was claimed durably"
                     " before its engine call; its engine call is recorded as returned; it is"
                     " still listed — its outcome is UNCERTAIN, the close is never re-issued,"
                     " and the retirement waits for its observed absence" % self.UNCERTAIN)
        self.assertEqual((self.engine.close_calls, self.last_gate_receipt(workflow_id)),
                         (["w-host-1"], uncertain))
        claims = self.claim_states(workflow_id, "claim-runtime_start-2")
        with self.minted() as actions:
            self.runtime_pass()
            self.restart_dirun()
            self.runtime_pass()
        self.assertEqual(actions, [])
        self.assertEqual(self.engine.close_calls, ["w-host-1"])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"), claims)
        self.assertEqual((self.host.created, self.engine_counts()), (["w-host-1"], (1, 1, 0)))
        # Observed absent at last: the retirement proceeds — still one close.
        self.engine.close_leaves_visible = False
        self.engine.live = [w for w in self.engine.live if w["workspace_id"] != "w-host-1"]
        self.runtime_pass()
        self.assert_retired_and_started(workflow_id, state, ["w-host-1"], [
            'dispatch 2 close-claimed "w-host-1"', 'dispatch 2 close-returned "w-host-1"',
            "dispatch 2 discarding the persisted runtime state",
            "dispatch 2 retired: closed none; every earlier runtime observed absent;"
            " runtime state discarded"])

    def crashed_after_the_close(self, still_listed=False, id_format="w-host-%d"):
        """The process dies right after the engine call returned — before its
        return is recorded or absence observed; a restart."""
        self.real_startup(id_format)
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        state = self.state_bytes(workflow_id)
        self.engine.close_leaves_visible = still_listed

        def dies():
            raise Crash("after the close, before its return is recorded")
        with self.around_the_close(after=dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(self.engine.close_calls, [self.ws(1)])
        self.assertEqual(self.retirement_trail(workflow_id),
                         ['dispatch 2 close-claimed %s' % json.dumps(self.ws(1))])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2"),
                         ["claiming", "claim:retiring"])
        self.restart_dirun()
        for _ in range(2):
            self.runtime_pass()
        return workflow_id, state

    def test_ST12_a_crash_after_the_close_recovers_without_a_second_close(self):
        workflow_id, state = self.crashed_after_the_close()
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-2")[2],
                         "claim:refused cause=%s (recovery pass: the retirement was interrupted"
                         " before any canonical open)" % broker_module.PROBLEM_PREDECESSOR_INTERRUPTED)
        self.assert_retired_and_started(workflow_id, state, ["w-host-1"], [
            'dispatch 2 close-claimed "w-host-1"',
            "dispatch 2 discarding the persisted runtime state",
            "dispatch 2 retired: closed none; every earlier runtime observed absent;"
            " runtime state discarded"])

    def test_ST13_a_crash_between_the_claim_and_the_engine_call_reports_an_unknown_invocation(self):
        """The claim is durable BEFORE the engine call, so by itself it cannot
        prove the call was made: reported as unknown, never re-issued."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()

        def dies():
            raise Crash("after the durable claim, before the engine call")
        with self.around_the_close(before=dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(self.engine.close_calls, [])
        self.restart_dirun()
        with self.minted() as actions:
            for _ in range(2):
                self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.created), ([], ["w-host-1"]))
        self.assertEqual(actions, [])
        self.assertEqual(self.last_gate_receipt(workflow_id),
                         "mission gate hold: %s — a close of workspace w-host-1 was claimed"
                         " durably before its engine call; no return of its engine call is"
                         " recorded, so whether the call was made is unknown; it is still listed"
                         " (claim of follow-up 2) — its outcome is UNCERTAIN, the close is never"
                         " re-issued, and the retirement waits for its observed absence"
                         % self.UNCERTAIN)

    def test_ST14_an_admitted_identity_with_spaces_round_trips_and_is_never_closed_twice(self):
        """Lossless durable membership: an admitted workspace id containing
        spaces (the canonical validator accepts it) is recorded exactly and
        recognised on the next pass — still listed after a crash, it is
        uncertain, never closed again."""
        workflow_id, state = self.crashed_after_the_close(still_listed=True,
                                                          id_format="w host %d")
        self.assertEqual(self.engine.close_calls, ["w host 1"])
        self.assertEqual(broker_module._retirement_close_claims(self.record(workflow_id)),
                         ({"w host 1": {2}}, {}, 0))
        self.assertIn("a close of workspace w host 1 was claimed durably before its engine"
                      " call; no return of its engine call is recorded",
                      self.last_gate_receipt(workflow_id))
        self.assertEqual(self.host.created, ["w host 1"])

    assert_retained = OwnershipReleaseTests.assert_retained
    cleanup_receipts = OwnershipReleaseTests.cleanup_receipts

    def test_ST15_a_retired_identity_listed_again_at_the_release_is_never_closed_again(self):
        """The RETIRED incarnation of w-host-1 (claimed by follow-up 2; no
        canonical start re-established it) is listed again at the release —
        the ONLY listed workspace (the follow-up's runtime is gone), so nothing
        else can make the release refuse: it is still never closed again."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        self.runtime_pass()
        agents = self.host.names("w-host-1")
        self.assertEqual(agents, [])                    # retired: not listed
        agents = self.host.names("w-host-2")[0]         # the same per-repository names
        self.corrected_work_finishes(workflow_id, [(1, "APPROVE"), (2, "APPROVE")])
        self.runtime_pass()
        self.deliver(mission_id, revision, workflow_id)
        self.engine.live = [{"workspace_id": "w-host-1", "agent_names": list(agents)}]
        entry = self.record(workflow_id)
        self.assertEqual(broker_module._retired_identities(
            self.broker, entry, broker_module._retirement_close_claims(entry)[0]),
            {"w-host-1": 2})
        self.release_pass()
        self.assert_retained(workflow_id, "workspace w-host-1 is still listed after follow-up"
                             " 2's retirement claimed its close; that close is never re-issued",
                             closes=["w-host-1"])
        self.assertEqual((self.host.native_closes, [w["workspace_id"] for w in self.engine.live]),
                         ([], ["w-host-1"]))

    # -- two live identities: EACH close its own boundary ---------------------------

    def two_live_identities(self):
        """Follow-up 2's retirement found w-host-1 ABSENT from the listing (a
        controlled listing shape: the host no longer listed it), so it closed
        nothing, preserved and discarded the state, and the REAL start created
        w-host-2; w-host-1 is then listed again with its agents. Follow-up 2's
        task finishes and the verification asks for follow-up 3, whose
        retirement has TWO live proven identities — two effect boundaries."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        hidden = [w for w in self.engine.live if w["workspace_id"] == "w-host-1"]
        self.engine.live = [w for w in self.engine.live if w["workspace_id"] != "w-host-1"]
        self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.created),
                         ([], ["w-host-1", "w-host-2"]))
        self.engine.live = hidden + self.engine.live
        self.write_round(workflow_id, 2, "APPROVE")
        self.listing_with_rounds([(1, "APPROVE"), (2, "APPROVE")])
        self.target_task_status = "COMPLETE"
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu3", "role": "verification", "process_id": 3},
            detail="acceptance criterion 4 not met")
        return mission_id, revision, workflow_id, self.state_bytes(workflow_id)

    def pass_with_the_retirements_return(self, workflow_id, after_close):
        """ONE Runtime pass with ``after_close`` run after each engine close
        of the retirement, and the effects SNAPSHOT AT THE RETURN of every
        effecting ``_retire_predecessor`` call — before any terminal recovery
        or cleanup of the same pass: the refusal's problem, the Broker's and
        the lifecycle's closes, the listing, the persisted state, the
        creations and the retirement trail at that moment."""
        real = broker_module.TargetBroker._retire_predecessor
        snapshots = []

        def spy(broker, workflows, entry, sequence, claim, point, assess=False):
            outcome = real(broker, workflows, entry, sequence, claim, point, assess=assess)
            if not assess:
                path, state = self.runtime_json(workflow_id), None
                if os.path.exists(path):
                    with open(path, "rb") as handle:
                        state = handle.read()
                snapshots.append({
                    "sequence": sequence,
                    "problem": getattr(outcome, "problem", None),
                    "closes": list(self.engine.close_calls),
                    "native": list(self.host.native_closes),
                    "live": [w["workspace_id"] for w in self.engine.live],
                    "state": state,
                    "created": list(self.host.created),
                    "trail": [t for t in self.retirement_trail(workflow_id)
                              if t.startswith("dispatch %d " % sequence)],
                })
            return outcome
        with mock.patch.object(broker_module.TargetBroker, "_retire_predecessor", spy):
            with self.around_the_close(after=after_close):
                self.runtime_pass()
        return snapshots

    def assert_at_the_retirements_return(self, snapshots, state, problem):
        """At the retirement's OWN return: exactly the first identity closed,
        by it, once; nothing discarded; nothing started; its refusal."""
        self.assertEqual(len(snapshots), 1, snapshots)
        (at,) = snapshots
        self.assertEqual((at["sequence"], at["problem"]), (3, problem))
        self.assertEqual((at["closes"], at["native"]), (["w-host-1"], []))
        self.assertEqual(at["live"], ["w-host-2"])
        self.assertEqual(at["state"], state)
        self.assertEqual(at["created"], ["w-host-1", "w-host-2"])
        self.assertEqual(at["trail"], ['dispatch 3 close-claimed "w-host-1"',
                                       'dispatch 3 close-returned "w-host-1"'])

    def assert_one_of_two_closed(self, workflow_id, state, problem, stopped=False,
                                 reclaimed=False):
        """The RETIREMENT closed exactly the first identity (its trail below
        names only it). ``stopped``: a cancel or EDIT also requires the stop
        of every runtime — the EXISTING owned stop (the recovery pass) then
        closes the still-live w-host-2 under the control's own authority and
        observes w-host-1 already absent (no second close of it); that close
        is attributed to its owner here, never to the retirement."""
        if stopped:
            self.assertEqual((self.engine.close_calls, self.host.native_closes),
                             (["w-host-1", "w-host-2"], []))
            self.assertEqual(self.engine.live, [])
            stops = self.receipts(workflow_id, "mission start")
            self.assertEqual(sorted(s.split(": ", 1)[1] for s in stops
                                    if "point=runtime_start" in s and "stop:confirmed" in s),
                             ["point=runtime_start dispatch=1 state=stop:confirmed cause=recovery"
                              " pass",
                              "point=runtime_start dispatch=2 state=stop:confirmed cause=recovery"
                              " pass"])
        else:
            self.assertEqual((self.engine.close_calls, self.host.native_closes),
                             (["w-host-1"], []))
            self.assertEqual([w["workspace_id"] for w in self.engine.live], ["w-host-2"])
        if reclaimed:
            # The control made the workflow terminal AND released its
            # retention: the RELEASE (final cleanup, its own authority)
            # preserved the evidence and reclaimed the directory — the state
            # went with it; the retirement discarded nothing (its trail below).
            self.assertEqual(self.released(workflow_id), (True, False))
            cleanups = self.cleanup_receipts(workflow_id)
            self.assertEqual(len(cleanups), 1)
            self.assertIn("cleanup complete", cleanups[0])
            self.assertIn("0 unprovable, 0 failed", cleanups[0])
            self.assertEqual(len(self.receipts(workflow_id, "target evidence preserved")), 1)
        else:
            self.assertEqual(self.state_bytes(workflow_id), state)
            self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.host.created, ["w-host-1", "w-host-2"])
        self.assertEqual(self.engine_counts(), (2, 1, 1))
        self.assertEqual([t for t in self.retirement_trail(workflow_id) if t.startswith("dispatch 3")],
                         ['dispatch 3 close-claimed "w-host-1"', 'dispatch 3 close-returned "w-host-1"'])
        self.assertEqual(self.claim_states(workflow_id, "claim-runtime_start-3")[-1],
                         "claim:refused cause=" + problem)

    def test_ST16_the_prior_task_active_again_between_closes_stops_the_second(self):
        def restarted(mission_id, revision):
            self.target_task_status = "ACTIVE"
            return "restarted"
        self.between_closes(restarted, self.PENDING, wa_record.PHASE_DISPATCHED)

    def test_ST17_a_foreign_task_between_closes_stops_the_second(self):
        def foreign(mission_id, revision):
            self.handed_over.append(self.FOREIGN_TASK)
            return "foreign"
        self.between_closes(foreign, self.UNPROVEN, wa_record.PHASE_BLOCKED)

    def test_ST18_a_hold_between_closes_is_seen_by_the_second_boundarys_admission(self):
        self.between_closes(
            lambda mission_id, revision: self.service.request_hold(
                mission_id, "hold between the closes"),
            gate_module.PROBLEM_HOLD_ACTIVE, wa_record.PHASE_DISPATCHED)

    def between_closes(self, control, problem, phase, stopped=False, reclaimed=False):
        """``control`` lands right after the FIRST close returned; the second
        close's own fresh boundary (its reads, then its admission) sees it.
        Asserted AT the retirement's own return first (one close by it,
        nothing discarded or started), then the whole pass with any terminal
        effect attributed to its own owner."""
        mission_id, revision, workflow_id, state = self.two_live_identities()
        fired = []

        def after_the_first():
            if self.engine.close_calls == ["w-host-1"] and not fired:
                fired.append(control(mission_id, revision))
        snapshots = self.pass_with_the_retirements_return(workflow_id, after_the_first)
        self.assertEqual(len(fired), 1)
        self.assert_at_the_retirements_return(snapshots, state, problem)
        self.assert_one_of_two_closed(workflow_id, state, problem, stopped=stopped,
                                      reclaimed=reclaimed)
        self.assertEqual(self.record(workflow_id)["phase"], phase)
        return workflow_id

    def test_ST18b_a_cancel_between_closes_is_seen_by_the_second_boundarys_admission(self):
        self.between_closes(
            lambda mission_id, revision: self.service.request_cancel(
                mission_id, "cancelled between the closes"),
            gate_module.PROBLEM_CANCEL_REQUESTED, wa_record.PHASE_BLOCKED, stopped=True)

    def test_ST18c_an_edit_between_closes_is_seen_by_the_second_boundarys_admission(self):
        # The service-level EDIT (``EngagementCase.edit``), as the in-flight
        # engagement tests inject it.
        self.between_closes(lambda mission_id, revision: self.edit(mission_id),
                            gate_module.PROBLEM_REVISION_SUPERSEDED, wa_record.PHASE_BLOCKED,
                            stopped=True, reclaimed=True)

    def test_ST18d_an_expiry_between_closes_is_seen_by_the_second_boundarys_admission(self):
        def lapse(mission_id, revision):
            self.clock.advance(decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS + 1)
            return "lapsed"
        self.between_closes(lapse, authorization_module.PROBLEM_EXPIRED,
                            wa_record.PHASE_BLOCKED)

    def test_ST18e_a_source_loss_at_the_second_admission_holds(self):
        """The Mission source stops answering the controls read the second
        close's admission makes (the binding read before it still answers)."""
        failing = mock.patch.object(
            self.gate._service, "mission_controls",
            side_effect=mission_store_module.MissionStoreError("the Mission store is unreadable"))
        self.addCleanup(mock.patch.stopall)
        workflow_id = self.between_closes(lambda mission_id, revision: failing.start(),
                                          gate_module.PROBLEM_SOURCE_UNAVAILABLE,
                                          wa_record.PHASE_DISPATCHED)
        failing.stop()
        self.assertIn("mission_control_source_unavailable", self.last_gate_receipt(workflow_id))

    def test_ST18f_an_unreadable_mission_store_at_the_second_boundary_holds_as_source_loss(self):
        """The Mission store becomes unreadable after the first close: the
        second boundary's re-derivation of the binding cannot be made — a
        source that stopped answering, reported AS SUCH (never as a changed
        binding), recoverable."""
        path = os.path.join(self.mission_dir, mission_store_module.MISSIONS_FILE_NAME)
        self.addCleanup(os.chmod, path, 0o600)
        workflow_id = self.between_closes(lambda mission_id, revision: os.chmod(path, 0),
                                          gate_module.PROBLEM_SOURCE_UNAVAILABLE,
                                          wa_record.PHASE_DISPATCHED)
        os.chmod(path, 0o600)
        receipt = self.last_gate_receipt(workflow_id)
        self.assertTrue(receipt.startswith("mission gate hold: %s — at the effect boundary: "
                                           % gate_module.PROBLEM_SOURCE_UNAVAILABLE), receipt)
        self.assertNotIn("changed since the proof", receipt)

    # -- a terminal control after a CLAIMED close: no replay on that incarnation ----

    def start_of(self, mission_id, ordinal, point="runtime"):
        (start,) = [s for s in self.starts(mission_id)
                    if s["engagement_sequence"] == ordinal and s["point"] == point]
        return start

    def stop_view(self, mission_id, ordinal, point="runtime"):
        """(stop required, stop confirmed, latest observation detail) of the
        canonical start — the truthful stop status the owner recorded."""
        start = self.start_of(mission_id, ordinal, point)
        latest = mission_state.latest_stop_observation(start)
        return (mission_state.start_stop_required(start),
                mission_state.start_stop_confirmed(start),
                latest["detail"] if latest else None)

    RETURNED = "its engine call is recorded as returned"
    UNRETURNED = ("no return of its engine call is recorded, so whether the call was made"
                  " is unknown")

    def claimed_still_listed(self, workspace_id, follow_up, invocation=None):
        return ("workspace %s is still listed — follow-up %d's retirement claimed its close"
                " before the engine call; %s; whether the listed workspace is that"
                " incarnation or a later one is not observable; its outcome is UNCERTAIN,"
                " the close is never re-issued on this incarnation, and the stop is PENDING"
                " until its absence is observed"
                % (workspace_id, follow_up, invocation or self.RETURNED))

    def claimed_absent(self, workspace_id, follow_up, invocation=None):
        return ("workspace %s is ABSENT from a fresh listing — follow-up %d's retirement"
                " claimed its close before the engine call; %s; settled by observed absence,"
                " no close issued" % (workspace_id, follow_up, invocation or self.RETURNED))

    def uncertain_then(self, control):
        """Follow-up 2's retirement close RETURNED but the workspace is still
        listed (UNCERTAIN, waiting); then ``control`` (a cancel or an EDIT)
        makes the stop of every runtime a duty; two passes and a restart."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()
        self.engine.close_leaves_visible = True
        self.runtime_pass()
        self.engine.close_leaves_visible = False
        self.assertEqual((self.engine.close_calls, [w["workspace_id"] for w in self.engine.live]),
                         (["w-host-1"], ["w-host-1"]))
        control(mission_id)
        for _ in range(2):
            self.runtime_pass()
        self.restart_dirun()
        self.runtime_pass()
        return mission_id, workflow_id

    def assert_outstanding_then_settled(self, mission_id, workflow_id, closes=("w-host-1",),
                                        invocation=None):
        # While unproven: the stop duty stands, OUTSTANDING and reported
        # UNCERTAIN — NO further close of that incarnation, by anyone.
        self.assertEqual((self.engine.close_calls, self.host.native_closes), (list(closes), []))
        for point in ("runtime", "task"):
            self.assertEqual(self.stop_view(mission_id, 1, point),
                             (True, False, self.claimed_still_listed("w-host-1", 2, invocation)))
        self.assertIn("point=runtime_start dispatch=1 state=stop:pending",
                      " ".join(self.receipts(workflow_id, "mission start")))
        # Observed absence SETTLES it — still no close.
        self.engine.live = [w for w in self.engine.live if w["workspace_id"] != "w-host-1"]
        self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.native_closes), (list(closes), []))
        for point in ("runtime", "task"):
            self.assertEqual(self.stop_view(mission_id, 1, point),
                             (True, True, self.claimed_absent("w-host-1", 2, invocation)))

    def test_ST23_a_cancel_after_an_uncertain_retirement_close_never_replays_it(self):
        mission_id, workflow_id = self.uncertain_then(
            lambda mission_id: self.service.request_cancel(mission_id, "cancelled"))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assert_outstanding_then_settled(mission_id, workflow_id)

    def test_ST23c_a_cancel_after_an_UNRETURNED_claim_never_issues_it(self):
        """The crash lands between the durable claim and the engine call: the
        close was never invoked — but nothing on record can prove that, so a
        later cancel's stop duty does NOT authorize issuing it on that
        incarnation: outstanding and reported unknown/uncertain; observed
        absence settles it; zero closes throughout."""
        self.real_startup()
        mission_id, revision, workflow_id = self.up_to_the_follow_up()

        def dies():
            raise Crash("after the durable claim, before the engine call")
        with self.around_the_close(before=dies):
            with self.assertRaises(Crash):
                self.runtime_pass()
        self.assertEqual(broker_module._retirement_close_claims(self.record(workflow_id)),
                         ({"w-host-1": {2}}, {}, 0))
        self.restart_dirun()
        self.service.request_cancel(mission_id, "cancelled after the crash")
        for _ in range(2):
            self.runtime_pass()
        self.restart_dirun()
        self.runtime_pass()
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.assert_outstanding_then_settled(mission_id, workflow_id, closes=(),
                                             invocation=self.UNRETURNED)

    def test_ST23b_an_edit_after_an_uncertain_retirement_close_never_replays_it(self):
        mission_id, workflow_id = self.uncertain_then(self.edit)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        # No release has run while the stop duty is outstanding: nothing
        # reclaimed, no cleanup receipt, no close.
        self.assertEqual(self.released(workflow_id), (False, True))
        self.assertEqual(self.cleanup_receipts(workflow_id), [])
        self.assert_outstanding_then_settled(mission_id, workflow_id)

    def test_ST24_another_incarnation_keeps_its_own_stop_duty(self):
        """Two live identities: follow-up 3's retirement close of w-host-1
        RETURNED but it is still listed, and its second boundary could not
        read the listing (w-host-2 never claimed); then a cancel. The stop of
        w-host-1's incarnation is outstanding and never re-closes it; the
        stop of w-host-2 — another incarnation — keeps its own duty and
        closes it."""
        mission_id, revision, workflow_id, state = self.two_live_identities()
        self.engine.close_leaves_visible = True

        def listing_fails():
            if self.engine.close_calls == ["w-host-1"]:
                self.engine.live_error = OSError("listing unavailable")
        with self.around_the_close(after=listing_fails):
            self.runtime_pass()
        self.engine.close_leaves_visible, self.engine.live_error = False, None
        self.assertEqual(broker_module._retirement_close_claims(self.record(workflow_id)),
                         ({"w-host-1": {3}}, {"w-host-1": {3}}, 0))
        self.service.request_cancel(mission_id, "cancelled")
        for _ in range(2):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.host.native_closes),
                         (["w-host-1", "w-host-2"], []))
        self.assertEqual([w["workspace_id"] for w in self.engine.live], ["w-host-1"])
        self.assertEqual(self.stop_view(mission_id, 1),
                         (True, False, self.claimed_still_listed("w-host-1", 3)))
        self.assertEqual(self.stop_view(mission_id, 2),
                         (True, True, "workspace w-host-2 closed and ABSENT from a fresh"
                                      " listing"))

    def test_ST25_a_re_established_incarnation_of_the_same_id_keeps_its_own_duty(self):
        """The controlled REUSE shape (the default engine re-lists the same
        id): follow-up 2's retirement closed incarnation 1 of ws-started-1
        (claimed, returned, observed absent); ordinal 2's canonical start
        RE-ESTABLISHED the id — incarnation 2. A cancel: ordinal 2's stop
        keeps its own duty and closes incarnation 2; ordinal 1's stop never
        closes (its incarnation's close was claimed) and settles by observed
        absence — one close per incarnation."""
        self.real_child_records()
        mission_id, revision, workflow_id = self.dispatched()
        self.engineering_finishes(workflow_id)
        self.role_turn.verification_result = FakeRoleTurnResult(
            outcome="request_follow_up",
            turn={"turn_id": "t-fu", "role": "verification", "process_id": 1},
            detail="acceptance criterion 3 not met")
        self.runtime_pass()
        self.assertEqual((self.engine.close_calls, len(self.engine.live)), (["ws-started-1"], 1))
        self.service.request_cancel(mission_id, "cancelled")
        for _ in range(3):
            self.runtime_pass()
        self.assertEqual((self.engine.close_calls, self.engine.live),
                         (["ws-started-1", "ws-started-1"], []))
        self.assertEqual(self.stop_view(mission_id, 2),
                         (True, True, "workspace ws-started-1 closed and ABSENT from a fresh"
                                      " listing"))
        self.assertEqual(self.stop_view(mission_id, 1),
                         (True, True, self.claimed_absent("ws-started-1", 2)))


class RuntimeStateArchiveTests(unittest.TestCase):
    """``evidence_preservation.inspect_runtime_state`` /
    ``preserve_runtime_state``: missing vs unreadable vs malformed vs intact,
    the stored text re-digested, owner/ordinal/size bound, and nothing that
    exists ever overwritten."""

    DATA = b'{"workspace_id": "w-host-1", "agents": {"supervisor": "s"}}\n'

    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="st-archive-")
        self.addCleanup(shutil.rmtree, self.store, True)
        self.path = preservation_module.runtime_state_path(self.store, "wf-1", 2)
        os.makedirs(os.path.dirname(self.path))

    def preserve(self, data=DATA):
        return preservation_module.preserve_runtime_state(self.store, "wf-1", 2, data, 5.0)

    def plant(self, document):
        with open(self.path, "w") as handle:
            json.dump(document, handle)
        with open(self.path, "rb") as handle:
            return handle.read()

    def archive_of(self, data, **changes):
        document = {"workflow_id": "wf-1", "dispatch_sequence": 2,
                    "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                    "text": data.decode("utf-8")}
        document.update(changes)
        return document

    def assert_kept(self, planted, problem):
        ok, got, detail, path = self.preserve()
        self.assertEqual((ok, got), (False, problem), detail)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), planted)
        return detail

    def test_missing_is_written_once_and_read_back_intact(self):
        self.assertEqual(preservation_module.inspect_runtime_state(self.store, "wf-1", 2)[0],
                         preservation_module.RUNTIME_STATE_MISSING)
        self.assertEqual(self.preserve()[:3], (True, None, None))
        status, document, _ = preservation_module.inspect_runtime_state(self.store, "wf-1", 2)
        self.assertEqual((status, document["text"].encode("utf-8")),
                         (preservation_module.RUNTIME_STATE_INTACT, self.DATA))
        self.assertFalse(os.path.exists(self.path + ".partial"))

    def test_an_intact_copy_of_the_same_bytes_is_idempotent_and_untouched(self):
        planted = self.plant(self.archive_of(self.DATA))
        self.assertEqual(self.preserve()[:3], (True, None, None))
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), planted)

    def test_an_intact_copy_of_other_bytes_conflicts(self):
        planted = self.plant(self.archive_of(b"{}\n"))
        self.assert_kept(planted, preservation_module.PROBLEM_RUNTIME_STATE_CONFLICT)

    def test_every_malformed_archive_is_kept_and_never_read_as_missing(self):
        malformed = {
            "recorded digest without the stored text's": self.archive_of(
                self.DATA, text=self.DATA.decode("utf-8") + " "),
            "another workflow": self.archive_of(self.DATA, workflow_id="wf-2"),
            "another ordinal": self.archive_of(self.DATA, dispatch_sequence=3),
            "a boolean ordinal": self.archive_of(self.DATA, dispatch_sequence=True),
            "a wrong size": self.archive_of(self.DATA, size=len(self.DATA) + 1),
            "a non-string text": self.archive_of(self.DATA, text=None),
            "no digest": dict((k, v) for k, v in self.archive_of(self.DATA).items()
                              if k != "sha256"),
            "a non-object": [self.archive_of(self.DATA)],
        }
        for label, document in sorted(malformed.items()):
            with self.subTest(label):
                planted = self.plant(document)
                self.assertEqual(
                    preservation_module.inspect_runtime_state(self.store, "wf-1", 2)[0],
                    preservation_module.RUNTIME_STATE_MALFORMED)
                self.assert_kept(planted, preservation_module.PROBLEM_RUNTIME_STATE_MALFORMED)
        with open(self.path, "wb") as handle:
            handle.write(b"\xff not json")
        self.assert_kept(b"\xff not json", preservation_module.PROBLEM_RUNTIME_STATE_MALFORMED)

    def test_unreadable_evidence_is_kept_and_recoverable(self):
        os.makedirs(self.path)
        ok, problem, detail, path = self.preserve()
        self.assertEqual((ok, problem), (False, preservation_module.PROBLEM_RUNTIME_STATE_UNREADABLE))
        self.assertTrue(os.path.isdir(self.path))

    def test_evidence_appearing_after_the_inspection_is_never_replaced(self):
        real = preservation_module.inspect_runtime_state
        planted = []

        def appears(*args):
            outcome = real(*args)
            if not planted:
                planted.append(self.plant(self.archive_of(b"{}\n")))
            return outcome
        with mock.patch.object(preservation_module, "inspect_runtime_state", appears):
            ok, problem, detail, path = self.preserve()
        self.assertEqual((ok, problem), (False, preservation_module.PROBLEM_RUNTIME_STATE_CONFLICT))
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), planted[0])


class LeaseRuntimeStateProjectionTests(unittest.TestCase):
    """``broker._lease_runtime_state``: genuinely absent vs existing but
    unprojectable, the legacy pane kept, derived exactly as ``start_herd``."""

    def setUp(self):
        self.lease = tempfile.mkdtemp(prefix="st-lease-")
        self.addCleanup(shutil.rmtree, self.lease, True)
        os.makedirs(os.path.join(self.lease, ".herd", "state"))

    def project(self, document=None, raw=None):
        path = os.path.join(self.lease, ".herd", "state", "runtime.json")
        with open(path, "wb") as handle:
            handle.write(raw if raw is not None else json.dumps(document).encode("utf-8"))
        return broker_module._lease_runtime_state(self.lease)

    def test_absent_is_the_only_no_state(self):
        os.rmdir(os.path.join(self.lease, ".herd", "state"))
        self.assertEqual(broker_module._lease_runtime_state(self.lease), (None, None))

    def test_the_workspace_id_and_the_legacy_pane_project_as_start_herd_derives_them(self):
        state, problem = self.project({"workspace_id": "w 1", "repo": self.lease,
                                       "agents": {"supervisor": "s"}})
        self.assertEqual((state["workspace_id"], state["supervisor"], problem), ("w 1", "s", None))
        state, problem = self.project({"workspace_id": "", "panes": {"supervisor": "w-9:3"},
                                       "agents": {"supervisor": "s"}})
        self.assertEqual((state["workspace_id"], problem), ("w-9", None))

    def test_every_existing_but_unprojectable_state_is_a_contradiction(self):
        cases = {
            "no id and no pane": ({"agents": {"supervisor": "s"}}, "names no projectable"),
            "a pane without a workspace prefix": (
                {"panes": {"supervisor": ":3"}, "agents": {"supervisor": "s"}},
                "names no projectable"),
            "a non-string id": ({"workspace_id": 7, "agents": {"supervisor": "s"}},
                                "not a string (7)"),
            "a non-string pane": ({"panes": {"supervisor": 3}, "agents": {"supervisor": "s"}},
                                  "supervisor pane that is not a string"),
            "no supervisor": ({"workspace_id": "w-1", "agents": {}}, "no usable supervisor"),
            "a non-string supervisor": ({"workspace_id": "w-1", "agents": {"supervisor": 1}},
                                        "no usable supervisor"),
            "agents not an object": ({"workspace_id": "w-1", "agents": ["s"]},
                                     "not an object"),
            "panes not an object": ({"workspace_id": "w-1", "panes": [],
                                     "agents": {"supervisor": "s"}}, "not an object"),
            "another repository": ({"workspace_id": "w-1", "repo": "/elsewhere",
                                    "agents": {"supervisor": "s"}}, "not this lease"),
            "a non-object": ([], "is not a JSON object"),
        }
        for label, (document, reason) in sorted(cases.items()):
            with self.subTest(label):
                state, problem = self.project(document)
                self.assertIsNone(state)
                self.assertIsNotNone(problem, "read as no state")
                self.assertTrue(problem[0], problem)
                self.assertIn(reason, problem[1])
        self.assertEqual(self.project(raw=b"{not json")[1], (True, "is not valid JSON"))


class RetirementClaimCodecTests(unittest.TestCase):
    """The durable close claim is lossless for every admitted identity; an
    undecodable one is counted, never read as no claim."""

    def entry(self, *summaries):
        return {"receipts": [{"bounded_summary": s} for s in summaries]}

    def test_any_admitted_identity_round_trips(self):
        marker = broker_module.RETIREMENT_RECEIPT_MARKER
        for workspace_id in ("w-host-1", "w host 1", 'w "quoted" 1', "wé中 1",
                             "w)paren (x", "x" * 128):
            with self.subTest(workspace_id=workspace_id):
                summary = "%s: dispatch 3 close-claimed %s (proven owned: agents a; x)" % (
                    marker, json.dumps(workspace_id))
                self.assertEqual(broker_module._retirement_close_claims(self.entry(summary)),
                                 ({workspace_id: {3}}, {}, 0))

    def test_undecodable_close_records_are_counted(self):
        marker = broker_module.RETIREMENT_RECEIPT_MARKER
        self.assertEqual(broker_module._retirement_close_claims(self.entry(
            marker + ": dispatch 2 close-claimed w-host-1 (bare)",
            marker + ": dispatch x close-returned \"w-host-1\"",
            marker + ": dispatch 2 close-claimed \"\"",
            marker + ": dispatch 2 retired: closed w-host-1")), ({}, {}, 3))


class R8WorkingPathTests(LoopCase):

    def test_R8_proposal_to_pr_url_through_the_production_composition(self):
        mission_id, revision, workflow_id = self.completed()
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][:2], [
            ("mission_candidate", True, None, "candidate_observed"),
            ("mission_delivery", True, None, "delivery_awaiting_decision")])
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        proposal = status["proposal"]
        self.assertEqual((proposal["current"], proposal["source_branch"],
                          proposal["target_base_branch"]),
                         (True, "di-mission/%s-r1" % mission_id, "main"))
        self.assertEqual(proposal["verification"]["exit_status"], 0)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(sorted(self.remote_refs()), ["refs/heads/main"])
        # The human's SEPARATE delivery decision, through the same client.
        request, delivered, is_error = self.grok_delivery_decide(mission_id, revision)
        self.assertFalse(is_error, delivered)
        self.assertEqual((delivered["status"], delivered["decision"],
                          delivered["proposal_digest_sha256"]),
                         ("applied", "accept", proposal["digest_sha256"]))
        self.assertEqual(request["params"]["requestedSchema"]["properties"]["confirm"]
                         ["enum"], [proposal["candidate_identity_digest_sha256"][:12]])
        outcomes = outcome_view(self.runtime_pass())
        self.assertEqual(outcomes[workflow_id][0][:2], ("mission_delivery", True))
        self.assertEqual(outcomes[workflow_id][0][3], "delivery_completed")
        status, _ = self.client.call(protocol.TOOL_DELIVERY_STATUS,
                                     {"mission_id": mission_id})
        delivery = status["delivery"]
        self.assertEqual((delivery["phase"], delivery["authorization_source"],
                          delivery["pr_url"]),
                         ("COMPLETE", "grok_mcp_client_confirmation",
                          "%s/pull/41" % CANONICAL_URL))
        self.assertEqual([(s["step"], s["state"], s["attested"]) for s in delivery["steps"]],
                         [("BASE_REFRESH", "not_needed", False),
                          ("COMMIT", "succeeded", True), ("PUSH", "succeeded", True),
                          ("PR_CREATE", "succeeded", True)])
        # Exact effects: one commit, one push, one PR; one remote branch.
        performed = self.delivery_transport.performed
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))
        self.assertEqual(sorted(self.remote_refs()),
                         ["refs/heads/di-mission/%s-r1" % mission_id, "refs/heads/main"])
        # Mission approval and delivery approval are DISTINCT records.
        document = self.mission_document()
        self.assertEqual([d["decision"] for d in document["missions"][mission_id]
                          ["decisions"]], ["APPROVE"])
        self.assertEqual(len(self.deliveries()), 1)
        record = list(self.deliveries().values())[0]
        self.assertEqual(record["human_authorization"]["client_confirmation"]
                         ["decision_id"], delivered["delivery_decision_id"])
        mission, _ = self.grok_status(mission_id)
        self.assertEqual(mission["canonical"]["progress"], mission_state.PROGRESS_COMPLETED)
        self.assertIn("pull/41", mission["canonical"]["completion"]["closure"]["detail"])
        # The verified result reaches the human as attention.
        pulled, _ = self.client.call(protocol.TOOL_ATTENTION_PULL, {})
        self.assertEqual([(a["condition_kind"], a["presentation"])
                          for a in pulled["surfaced_now"]],
                         [("RESULT_READY", "SURFACED")])
        # A further pass performs nothing more.
        self.runtime_pass()
        self.assertEqual((performed["commit_step"], performed["push"],
                          performed["gh_pr_create"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
