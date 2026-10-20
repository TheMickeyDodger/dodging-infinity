"""A real Mission request panel adapter for end-to-end tests of the Pixel server
and the webview. TEST SUPPORT, not a product entry point (the leading underscore
keeps the suite loop from collecting it, as with ``_hermetic_git.py``).

What is REAL: the HTTP adapter, the controller, the journal, the conversation
(the restricted-posture check and the Codex event parser), the local request
surface, Mission Core, the operator-attested approval, and the real
``MissionBridge``. What is SCRIPTED, through the control channel on stdin, is
exactly what no test can have for real: the Codex process (its replies), the
process the bridge would spawn, and the team's observed state. This is the same
substitution the bridge and conversation suites already make; nothing here is
evidence of a live provider or Herdr.

    python3 tests/_panel_e2e_server.py --work-dir DIR --discovery-file PATH

``--work-dir`` must be a new, owned directory. The first stdout line is the
startup JSON (never the token); each stdin line is one JSON control command
answered by one stdout JSON line:

    {"op": "provider", "reply": "<codex message>"}      queue the next reply
    {"op": "provider", "fail": "exit"|"garbage"|"raise"} queue a failing turn
    {"op": "provider", "delay": 1.5, "reply": "..."}     reply after a delay
    {"op": "team", "status": "RUNNING"|"COMPLETE"|"ERROR"}
    {"op": "artifacts", "request_ref": "lr-..."}         write the team's files
    {"op": "spawn", "raises": null|"after_child"|"before_child"|"binding_failure"}
                                                         "binding_failure" runs the REAL role-binding
                                                         code and the REAL retention helper into the
                                                         child workspace (one role with no session),
                                                         then raises what real startup raises
    {"op": "clock", "advance": 900}                      move the panel's clock
    {"op": "restart"}                                    a new controller, same state
    {"op": "runtime", "start": true}                     start the REAL progression loop
    {"op": "runtime", "stop": true}                      stop it (its own clean exit)
    {"op": "seed_turns", "turns": 50, "character": "astral"}
                                                         fill a conversation, in the REAL journal,
                                                         with turns whose two texts are the ACCEPTED
                                                         maximum of one kind of character ("ascii",
                                                         "astral", "control"); answers its id. With
                                                         "append_to_latest": true the turns join the
                                                         newest conversation (the one a window has open)
    {"op": "edit_contract", "request_ref": "lr-..."}     an authenticated revision that changes ONLY
                                                         the proof rules (a test of Round 27 F1)
    {"op": "state"}                                      counts, for assertions
    {"op": "stop"}

The progression runtime started by ``runtime`` is the REAL ``dirun`` MissionCore
mode (``target_runtime.cli.main``): its argument handling, single-owner lock,
pass loop, status file and exit are the production code, composed through the
same ``mission_panel.composition`` call the panel uses. Only the bridge's two
EFFECT seams are the scripted ones above, and the loop is paced at
``PASS_INTERVAL_SECONDS`` so a test does not wait the production five.

``state`` reports ``service_calls``: how many calls the HTTP adapter has made
into the controller. The runtime never goes through the controller, so a Mission
that advances while that number does not move was advanced by the runtime and
not by anyone reading it.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _hermetic_git import run_git  # noqa: E402
from herdr import lifecycle as herdr_lifecycle  # noqa: E402
from mission_panel import composition as composition_module  # noqa: E402
from mission_panel import conversation as conversation_module  # noqa: E402
from mission_panel import http_adapter  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from operator_session.codex import RestrictedCodexOperatorSession  # noqa: E402
from target_runtime import cli as runtime_cli  # noqa: E402
from target_runtime import evidence as evidence_module  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402
from target_runtime import mission_progress_status as progress_status  # noqa: E402
from target_runtime.catalog_http import publish_discovery_file, remove_discovery_file  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    FakeOwnership, FakeTransport, FakeTrustWorker, Observer, REVIEW_TEXT, RESULT_TEXT,
    SpawnRecorder, SURFACE, TASK_ID, raw_observation,
)
from types import SimpleNamespace  # noqa: E402

import secrets  # noqa: E402


class Clock(object):
    """Real time plus an offset the control channel can advance."""

    def __init__(self):
        self.offset = 0

    def __call__(self):
        return int(time.time()) + self.offset


class ScriptedCodex(object):
    """The one Codex process spawn: answers from a queue, one entry per turn."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queue = []
        self.calls = 0

    def push(self, entry):
        with self.lock:
            self.queue.append(entry)

    def __call__(self, argv, input_bytes, cwd):
        with self.lock:
            self.calls += 1
            entry = self.queue.pop(0) if self.queue else None
        if entry is None:
            return subprocess.CompletedProcess(argv, 1, b"", b"no scripted reply queued")
        if entry.get("delay"):
            time.sleep(float(entry["delay"]))
        kind = entry.get("fail")
        if kind == "raise":
            raise RuntimeError("scripted provider failure")
        if kind == "exit":
            return subprocess.CompletedProcess(argv, 1, b"", b"scripted nonzero exit")
        if kind == "garbage":
            return subprocess.CompletedProcess(argv, 0, b"not json at all\n", b"")
        line = json.dumps({"session_id": "s-e2e", "last_agent_message": entry["reply"]})
        return subprocess.CompletedProcess(argv, 0, (line + "\n").encode("utf-8"), b"")


PASS_INTERVAL_SECONDS = 0.25


class Counting(object):
    """The controller as the HTTP adapter sees it, counting every call it makes
    into it. Only the progression runtime bypasses it."""

    def __init__(self, world, inner):
        self._world, self._inner = world, inner

    def __getattr__(self, name):
        member = getattr(self._inner, name)
        if name.startswith("_") or not callable(member):
            return member

        def counted(*args, **kwargs):
            self._world.service_calls += 1
            self._world.call_log.append((round(time.time(), 2), name))
            del self._world.call_log[:-50]
            return member(*args, **kwargs)
        return counted


class Runtime(object):
    """The real ``dirun`` MissionCore loop in a thread."""

    def __init__(self, world):
        self.world = world
        self.stop = threading.Event()
        self.thread = None
        self.code = None
        self.passes = 0

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        argv = ["--mission-state-dir", self.world.state,
                "--mission-control-repo", self.world.control,
                "--mission-workspace-repository", self.world.repository,
                "--mission-workspaces-root", self.world.root, "run"]

        def sleeper(_seconds):
            self.passes += 1
            if self.stop.wait(PASS_INTERVAL_SECONDS):
                raise KeyboardInterrupt

        def target():
            self.code = runtime_cli.main(
                argv, sleeper=sleeper,
                mission_composition=self.world.mission_composition)
        self.thread = threading.Thread(target=target, name="progression", daemon=True)
        self.thread.start()

    def end(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(15)

    def alive(self):
        return self.thread is not None and self.thread.is_alive()


BINDING_AGENTS = {"supervisor": "h-sup", "lead1": "h-lead1", "executor1": "h-exec1",
                  "reviewer1": "h-rev1"}
BINDING_PANES = {"supervisor": "pane-root", "lead1": "pane-lead", "executor1": "pane-executor",
                 "reviewer1": "pane-reviewer", "controller": "pane-controller"}


def _lead_has_no_session(agent):
    record = {"name": agent, "cwd": "/repo", "workspace_id": "ws1", "pane_id": "pane-" + agent}
    if agent != "h-lead1":
        record["agent_session"] = {"value": "session-" + agent}
    return {"status": "idle", "raw": {"result": {"agent": record}}}


class HarnessSpawn(SpawnRecorder):
    """The bridge test suite's recorder, plus one mode: ``binding_failure`` does what real startup does when the
    roles start but do not bind. The real ``establish_role_bindings`` runs against scripted Herdr answers, the real
    retention helper writes the diagnostic into the child workspace, and the typed failure is raised. No child record
    is created."""

    def __call__(self, parent_repo, request_):
        if self.raises != "binding_failure":
            return super(HarnessSpawn, self).__call__(parent_repo, request_)
        self.calls.append((parent_repo, dict(request_)))
        workspace = request_["target_repo"]
        herd = SimpleNamespace(herd_root=Path(workspace) / ".herd", repo=Path(workspace))
        try:
            herdr_lifecycle.establish_role_bindings(
                herd, BINDING_AGENTS, prober=_lead_has_no_session, settle_seconds=0.0,
                sleeper=lambda _seconds: None)
        except herdr_lifecycle.BindingsNotEstablished as failure:
            herdr_lifecycle._retain_post_bootstrap_failure(
                failure, herd, herdr_lifecycle.STAGE_ESTABLISH_ROLE_BINDINGS, "ws1",
                {"panes": BINDING_PANES, "created_at": self.clock()})
            raise
        raise AssertionError("the scripted roles were meant not to bind")


class World(object):
    """Everything the harness owns under ``work_dir``."""

    def __init__(self, work_dir):
        self.work = work_dir
        self.clock = Clock()
        self.codex = ScriptedCodex()
        self.observer = Observer()
        self.spawn = HarnessSpawn(self.observer, self.clock)
        self.transport = FakeTransport()
        self.trust = FakeTrustWorker()
        self.ownership = FakeOwnership()
        self.control = os.path.join(work_dir, "control")
        self.state = os.path.join(work_dir, "state")
        self.repository = os.path.join(work_dir, "repository")
        self.root = os.path.join(work_dir, "workspaces")
        for path in (self.control, self.repository, self.root):
            os.makedirs(path)
        run_git("init", "-q", self.control)
        for name in ("AGENTS.md", "OPERATOR_PROTOCOL.md"):
            with open(os.path.join(self.control, name), "w") as handle:
                handle.write("# end-to-end fixture\n")
        self.transport.git_common = os.path.join(os.path.realpath(self.repository), ".git")
        os.makedirs(self.transport.git_common)
        self.bridge = None
        self.service_calls = 0
        self.call_log = []
        self.runtime = Runtime(self)
        self.inner = None
        self.service = self.build()

    def records(self, repo):
        listed = [dict(r) for r in self.spawn.records]
        return {"state": "available" if listed else "empty", "truncated": False,
                "listed": listed, "count": len(listed)}

    def bridge_factory(self, missions, control_repo, clock, run_guard):
        """The real bridge over the scripted effect seams: the one the panel's
        composition and the runtime's composition both build."""
        return bridge_module.MissionBridge(
            missions, "/control-repo", clock, spawn_fn=self.spawn,
            observer_fn=self.observer, spawn_records_fn=self.records,
            transport=self.transport,
            surface_digest_fn=lambda repo: {"status": "exact", "digest": SURFACE},
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            worker=self.trust, run_guard=run_guard)

    def mission_composition(self, state, control, repository, root):
        return composition_module.compose(
            state, control, repository, root, clock=self.clock,
            bridge_factory=self.bridge_factory)

    def build(self):
        """A fresh controller over the SAME durable state: a restart."""
        composed = composition_module.compose(
            self.state, self.control, self.repository, self.root, clock=self.clock,
            bridge_factory=self.bridge_factory)
        self.bridge = composed.bridge
        self.composed = composed
        registry = conversation_module.InFlight()
        convo = conversation_module.PanelConversation(
            composed.journal, RestrictedCodexOperatorSession(runner=self.codex),
            self.control, self.transport.origin, self.clock, registry=registry)
        self.inner = service_module.PanelService(
            composed.surface, composed.journal, convo, registry, self.clock,
            bridge=composed.bridge, repository_url=self.transport.origin,
            progression=lambda: progress_status.probe(
                self.state, composed.digest, int(time.time())))
        self.inner.start()
        return Counting(self, self.inner)

    def mission_id_of(self, request_ref):
        # The harness's own bookkeeping: NOT through the counted controller, so
        # it can never be mistaken for a window reading.
        return self.inner.request_view(request_ref)["mission_id"]

    def write_artifacts(self, request_ref):
        mission_id = self.mission_id_of(request_ref)
        state_dir = os.path.join(os.path.realpath(self.root), mission_id, ".herd", "state")
        with open(os.path.join(state_dir, "task-checkpoint.md"), "w") as handle:
            handle.write(RESULT_TEXT)
        name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (TASK_ID, 1)
        with open(os.path.join(state_dir, "reviews", name), "w") as handle:
            handle.write(REVIEW_TEXT)


SEED_CHARACTERS = {"ascii": "a", "astral": "\U0001F600", "control": "\x01"}


def seed_turns(world, turns, character, append_to_latest=False):
    """Write ``turns`` finished turns into the REAL journal, each with both texts at the
    accepted maximum (``MAX_TURN_TEXT_CHARS``) of one kind of character. The journal's own
    validation accepts them; nothing here is a shortcut around it."""
    char = SEED_CHARACTERS[character]
    journal = world.composed.journal
    outcome = {"status": "completed", "error_code": None, "detail": None,
               "unrecognized_event_lines": 0}
    conversation_id = None
    if append_to_latest:
        conversation_id = world.inner.overview()["conversations"][0]["conversation_id"]
    for i in range(turns):
        turn = journal.reserve_turn(
            conversation_id, char * journal_module.MAX_TURN_TEXT_CHARS, "seed-%d" % i,
            world.clock(), set())
        conversation_id = turn["conversation_id"]
        journal.finish_turn(turn["turn_id"], journal_module.TURN_COMPLETED,
                            char * journal_module.MAX_TURN_TEXT_CHARS, outcome, None, None,
                            world.clock())
    return conversation_id


def edit_contract(world, request_ref):
    """An authenticated revision of the Mission behind ``request_ref`` that changes the proof
    rules and NOTHING ELSE. Returns the old and new proposal digests."""
    from test_local_request import SYNTHETIC_AUTHENTICATED
    missions = world.inner._surface._missions
    view = world.inner.request_view(request_ref)
    proposal = json.loads(json.dumps(view["proposal"]))
    proposal["proof_contract"]["continuation_budget"]["max_attempts"] += 5
    proposal["proof_contract"]["requirements"][0]["max_evidence_age_seconds"] = 60
    missions.edit(view["mission_id"], view["revision"], proposal,
                  missions.mint_decision_id(SYNTHETIC_AUTHENTICATED), SYNTHETIC_AUTHENTICATED)
    after = world.inner.request_view(request_ref)
    return {"revision": after["revision"], "old_digest": view["proposal_digest_sha256"],
            "new_digest": after["proposal_digest_sha256"]}


def control(world, server, command):
    op = command.get("op")
    if op == "seed_turns":
        return {"ok": True, "conversation_id": seed_turns(
            world, int(command["turns"]), command["character"],
            bool(command.get("append_to_latest")))}
    if op == "edit_contract":
        return dict(edit_contract(world, command["request_ref"]), ok=True)
    if op == "provider":
        world.codex.push({k: command[k] for k in ("reply", "fail", "delay") if k in command})
        return {"ok": True, "queued": len(world.codex.queue)}
    if op == "team":
        world.observer.raw = raw_observation(status=command["status"])
        return {"ok": True}
    if op == "artifacts":
        world.write_artifacts(command["request_ref"])
        return {"ok": True}
    if op == "spawn":
        world.spawn.raises = command.get("raises")
        return {"ok": True}
    if op == "clock":
        world.clock.offset += int(command["advance"])
        return {"ok": True, "offset": world.clock.offset}
    if op == "restart":
        world.service = world.build()
        server.service = world.service
        return {"ok": True}
    if op == "runtime":
        if command.get("start"):
            world.runtime.start()
        if command.get("stop"):
            world.runtime.end()
        return {"ok": True, "alive": world.runtime.alive(), "code": world.runtime.code}
    if op == "state":
        missions_file = os.path.join(world.state, "missions.json")
        authorizations = 0
        states = {}
        if os.path.exists(missions_file):
            with open(missions_file) as handle:
                document = json.load(handle)
            authorizations = len(document.get("authorizations", {}))
            states = {mid: m["state"] for mid, m in document.get("missions", {}).items()}
        return {"ok": True, "spawn_calls": len(world.spawn.calls),
                "codex_calls": world.codex.calls, "authorizations": authorizations,
                "requests": len(world.inner._surface.list_requests()["requests"]),
                "service_calls": world.service_calls,
                "service_log": world.call_log[-12:],
                "runtime_alive": world.runtime.alive(),
                "runtime_passes": world.runtime.passes,
                "mission_states": states}
    return {"ok": False, "error": "unknown op %r" % (op,)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--discovery-file", required=True)
    args = parser.parse_args(argv)
    world = World(args.work_dir)
    token = secrets.token_urlsafe(32)
    server = http_adapter.PanelServer(world.service, token)
    identity = publish_discovery_file(args.discovery_file, server.server_address[1], token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sys.stdout.write(json.dumps({"endpoint": "mission_panel_e2e", "url": server.url(),
                                 "discovery_file": args.discovery_file}) + "\n")
    sys.stdout.flush()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            command = json.loads(line)
            if command.get("op") == "stop":
                sys.stdout.write(json.dumps({"ok": True}) + "\n")
                sys.stdout.flush()
                break
            try:
                answer = control(world, server, command)
            except Exception as exc:                     # reported to the test, not hidden
                answer = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            sys.stdout.write(json.dumps(answer) + "\n")
            sys.stdout.flush()
    finally:
        world.runtime.end()
        remove_discovery_file(args.discovery_file, identity)
        server.shutdown()
        server.server_close()
        world.inner._conversation.idle(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
