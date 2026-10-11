"""I3 of task 20261010-164104-1761f6: Grok Bot runs nothing, and its view of
the run is a truthful read of the durable record.

What each class proves, by brief section:

- B-2, the bypass is CLOSED: no Grok Bot module reaches
  ``dispatch.production_spawn`` (or the run bridge, the Broker, Herdr's
  orchestrator or ``local_request.cli``), proved STATICALLY over the whole
  transitive import closure (function-level imports included) and
  BEHAVIOURALLY with tripwires on every spawn seam while every tool, every
  run command, ``call run`` and ``serve`` are driven (``StaticBypassClosure``,
  ``BehaviouralBypass``). ``status`` and ``result`` are READ-ONLY lock-free
  projections (``WorkflowStatusProjection``, ``WriterLockHeld``,
  ``LockProbeTotality``, ``WorkflowResult``). The local operator surface keeps
  its run commands (``OperatorSurfaceSeparation``).
- Legacy readability ruling: a legacy (``lr-``) VERIFIED run's REAL result
  and review text come back through Grok Bot by the one recovery
  implementation, and every unrecoverable case fails closed
  (``LegacyResultReadability``).
- B-1/B-1a, the advice SHOWN: ``present``, the CLI help, the arming response
  and every ``run`` command tell the human something that works when
  followed, each pinned separately (``LegacyAdvice``, ``RunAdvice``,
  ``RemedyRoundTrips``, ``RuntimeRemedy``).
- B-3, evidence: the PRODUCTION Runtime wiring (``runtime.process_once`` over
  a real ``TargetBroker`` with ``target_runtime.cli.production_role_turn``)
  with only its EFFECT seams injected advances a Grok-approved workflow to
  COMPLETED with no further Grok call; every Codex turn is a fresh
  restrictive process; the Runtime refuses a turn's proposal it cannot
  verify; the follow-up bound ends in NEEDS_REAUTHORIZATION; the dispatch
  carries the fixed roster (``RuntimeDrive``).

HERMETIC BY CONSTRUCTION, stated exactly:

- No real subprocess: ``setUpModule`` replaces every process entry point
  (``subprocess.*``, ``os.system/popen/fork/forkpty``, every ``os.exec*`` and
  ``os.spawn*``, ``os.posix_spawn*``, ``os.kill/killpg``) with a function
  that RAISES before any product module is imported, and
  ``dispatch.production_spawn`` and ``herdr.orchestrator
  .execute_spawn_request`` are tripwired for the whole module.
- No network: ``socket.socket/create_connection/socketpair/getaddrinfo``
  raise. ``serve`` is driven with its listener replaced by a recorder.
- No writes outside one ``tempfile.mkdtemp()`` root; ``HOME`` points there.
- The machine-global scope store is never reached
  (``_scope_hygiene.isolate_module()``, as in the I2 module).
- No Codex, Herdr, git or real repository: every Codex turn is answered by a
  recorder standing in for ``role_turn._default_runner`` (the production
  argv build and posture verification still run); the git transport is pure
  Python (a "clone" copies a local fixture directory); nothing reaches
  github.com. Targets are synthetic.
- No live Runtime and no real lock holder outside this process: a "held"
  lock is held by this test through the production lock functions.

Fixture shapes are COPIED from tests/test_grok_conforming_i2.py,
tests/test_target_runtime.py, tests/test_mission_bridge.py and
tests/test_local_request.py, never imported, so this module runs alone.
"""

import ast
import errno
import hashlib
import importlib
import io
import itertools
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


# --- Hermetic harness (the I2 construction) -------------------------------

_REFUSED_ENTRY_POINTS = (
    (subprocess, (
        "Popen", "run", "call", "check_call", "check_output",
        "getoutput", "getstatusoutput",
    )),
    (os, (
        "system", "popen", "fork", "forkpty",
        "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "posix_spawn", "posix_spawnp", "kill", "killpg",
    )),
    (socket, (
        "socket", "create_connection", "socketpair", "getaddrinfo",
    )),
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATCHERS = []
_ROOT = []
_ISOLATED = []
# Every reach of a module-wide spawn tripwire, for assertions.
SPAWN_TRIPS = []

store = digest = record = atomic = None
role_turn = conforming = adapter_module = authorize = arming = None
index_module = cli_module = mcp = server_module = None
request_store = surface_module = local_request_cli = None
mission_record = mission_service = mission_store = None
repository_module = operator_session = operator_session_codex = None
scope_hygiene = dispatch_module = bridge_module = broker_module = None
runtime_module = runtime_cli = evidence_module = legacy_result = None
state_artifact = readiness_module = preserve_module = runtime_config = None
workspace_trust = herdr_config = herdr_orchestrator = None
telegram_config = telegram_state = None
CONTROL = None


class HermeticViolation(AssertionError):
    """A test reached a process, network or spawn entry point."""


def _refuse(name):
    def refused(*args, **kwargs):
        raise HermeticViolation(
            "hermetic I3 test reached %s; nothing in this module may spawn,"
            " signal or connect" % name)
    return refused


def _spawn_tripwire(name):
    def tripped(*args, **kwargs):
        SPAWN_TRIPS.append(name)
        raise HermeticViolation("the spawn seam %s was reached" % name)
    return tripped


def setUpModule():
    global store, digest, record, atomic, role_turn, conforming
    global adapter_module, authorize, arming, index_module, cli_module, mcp
    global server_module, request_store, surface_module, local_request_cli
    global mission_record, mission_service, mission_store, repository_module
    global operator_session, operator_session_codex, scope_hygiene
    global dispatch_module, bridge_module, broker_module, runtime_module
    global runtime_cli, evidence_module, legacy_result, state_artifact
    global readiness_module, preserve_module, workspace_trust, herdr_config
    global runtime_config
    global herdr_orchestrator, telegram_config, telegram_state, CONTROL
    for module, names in _REFUSED_ENTRY_POINTS:
        for name in names:
            if not hasattr(module, name):
                continue
            patcher = mock.patch.object(
                module, name, _refuse("%s.%s" % (module.__name__, name)))
            patcher.start()
            _PATCHERS.append(patcher)
    root = tempfile.mkdtemp(prefix="di-i3-grok-runtime-")
    _ROOT.append(root)
    home = mock.patch.dict(os.environ, {"HOME": root})
    home.start()
    _PATCHERS.append(home)
    load = importlib.import_module
    scope_hygiene = load("_scope_hygiene")
    _ISOLATED.append(scope_hygiene.isolate_module())
    store = load("workflow_authority.store")
    digest = load("workflow_authority.digest")
    record = load("workflow_authority.record")
    atomic = load("workflow_authority.atomic")
    role_turn = load("codex_gateway.role_turn")
    conforming = load("grok_bot.conforming")
    adapter_module = load("grok_bot.adapter")
    authorize = load("grok_bot.authorize")
    arming = load("grok_bot.arming")
    index_module = load("grok_bot.index")
    cli_module = load("grok_bot.cli")
    mcp = load("grok_bot.mcp")
    server_module = load("grok_bot.server")
    request_store = load("local_request.store")
    surface_module = load("local_request.surface")
    local_request_cli = load("local_request.cli")
    mission_record = load("mission.record")
    mission_service = load("mission.service")
    mission_store = load("mission.store")
    repository_module = load("codex_gateway.repository")
    operator_session = load("operator_session")
    operator_session_codex = load("operator_session.codex")
    dispatch_module = load("target_runtime.dispatch")
    bridge_module = load("target_runtime.mission_bridge")
    broker_module = load("target_runtime.broker")
    runtime_module = load("target_runtime.runtime")
    runtime_cli = load("target_runtime.cli")
    evidence_module = load("target_runtime.evidence")
    legacy_result = load("target_runtime.legacy_result")
    state_artifact = load("target_runtime.state_artifact")
    runtime_config = load("target_runtime.runtime_config")
    readiness_module = load("target_runtime.readiness")
    preserve_module = load("target_runtime.evidence_preservation")
    workspace_trust = load("target_runtime.workspace_trust")
    herdr_config = load("herdr.config")
    herdr_orchestrator = load("herdr.orchestrator")
    telegram_config = load("telegram_operator.config")
    telegram_state = load("telegram_operator.state")
    # Module-wide spawn tripwires: no test here may reach a real spawn.
    for module, name in ((dispatch_module, "production_spawn"),
                         (herdr_orchestrator, "execute_spawn_request")):
        patcher = mock.patch.object(
            module, name, _spawn_tripwire("%s.%s" % (module.__name__, name)))
        patcher.start()
        _PATCHERS.append(patcher)
    control = os.path.join(root, "control-repository")
    for relative, text in (
        ("AGENTS.md", "synthetic control agents contract\n"),
        ("OPERATOR_PROTOCOL.md", "synthetic control operator protocol\n"),
        ("herdctl.py", "print('control cli stub')\n"),
        (os.path.join("herdr", "core.py"), "VALUE = 1\n"),
        (os.path.join("roles", "executor.md"), "executor role\n"),
    ):
        path = os.path.join(control, relative)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
    CONTROL = os.path.realpath(control)


def tearDownModule():
    while _ISOLATED:
        scope_hygiene.release_module(_ISOLATED.pop())
    while _PATCHERS:
        _PATCHERS.pop().stop()
    while _ROOT:
        shutil.rmtree(_ROOT.pop(), ignore_errors=True)


def fresh_dir(test, name="case"):
    directory = tempfile.mkdtemp(prefix=name + "-", dir=_ROOT[0])
    os.chmod(directory, 0o700)
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def tree(root):
    """Every path under ``root`` with its kind and content digest."""
    found = {}
    for base, dirs, files in os.walk(root):
        for name in dirs:
            found[os.path.relpath(os.path.join(base, name), root)] = "dir"
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                found[os.path.relpath(path, root)] = "link"
                continue
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = hashlib.sha256(
                    handle.read()).hexdigest()
    return found


# --- Grok-side fixture (copied from the I2 module, bridge-less) -----------

NOW = 1760000000
TEXT = ("Please fix the flaky readiness probe in octocat/widget;"
        " keep the public API.")
BASELINE_SHA = "89abcdef0123456789abcdef0123456789abcdef"
CANONICAL_URL = "https://github.com/octocat/widget"
TARGET_TASK_ID = "20261010-000000-target1"
VERIFIED_SUMMARY = "The readiness probe is fixed; the regression test passes."


def authorization_document(**changes):
    """A Mission Authorization as the planning turn returns it, bound to
    the LIVE control repository (its policy digest is what the Runtime
    re-checks before any target work)."""
    document = {
        "objective": "Resolve the flaky readiness probe",
        "constraints": "Keep the public API; no schema change",
        "rules": "Target rules never override control authority",
        "desired_outcome": "The probe is reliable and tested",
        "acceptance": "Regression test added; the suite passes",
        "unresolved_questions": "None recorded",
        "execution_scope": "The target repository only",
        "control": {"repository_realpath": CONTROL,
                    "policy_digest_sha256": digest.control_policy_digest(
                        CONTROL)},
        "target": {"canonical_host": "github.com", "owner": "octocat",
                   "repo": "widget", "canonical_url": CANONICAL_URL},
        "issue_or_pr": {"kind": "issue", "number": 7},
        "baseline": {"ref": "refs/heads/main", "commit_sha": BASELINE_SHA},
        "handoff": {"revision": 1,
                    "text": "Investigate and resolve the flaky readiness"
                            " probe. Add regression coverage."},
        "telegram_approval": None,
        "workflow_id": None,
        "human_intent": None,
        "revision": 1,
        "delivery_authority": "none",
    }
    document.update(changes)
    return document


def envelope(kind, body):
    return "DI-REMOTE-2 RESPONSE " + json.dumps(
        {"remote_protocol_version": 2, "kind": kind, "body": body},
        separators=(",", ":"))


ROUTING_SIGNAL = envelope("mission_authorization", "routing-note-i3")


def codex_stdout(message, session_id="codex-session-synthetic"):
    """Synthetic ``codex exec --json`` output carrying one final message."""
    return "\n".join([
        json.dumps({"session_id": session_id}),
        json.dumps({"type": "agent_message", "message": message}),
    ]).encode("utf-8")


class OperatorCodex(object):
    """SYNTHETIC spawn seam of the initial Operator turn; starts nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, input_bytes, cwd):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 0, stdout=codex_stdout(ROUTING_SIGNAL), stderr=b"")


class PlanningCodex(object):
    """SYNTHETIC spawn seam of the planning turn; starts nothing."""

    def __init__(self):
        self.calls = []
        self.document = authorization_document()

    def __call__(self, argv, prompt_bytes, cwd):
        self.calls.append(list(argv))
        return (0, codex_stdout(envelope("mission_authorization",
                                         self.document)),
                b"", 7000 + len(self.calls))


def restrictive_argv(repository_realpath):
    """The #43 posture, stated independently of the builder."""
    return ["codex", "exec", "--json", "-C", repository_realpath,
            "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules",
            "--strict-config", "-c", "approval_policy=never", "-"]


def pure_repository_check(path):
    """``validate_repository`` without its ``git rev-parse``."""
    return repository_module.resolve_repository_path(path), None


class Clock(object):
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class GrokCase(unittest.TestCase):
    """A Grok Bot adapter built exactly as ``grok_bot.cli`` builds it: the
    BRIDGE-LESS surface (``build_bridgeless_surface``) and the one legacy
    result recovery (``cli.recover_legacy_result``)."""

    def setUp(self):
        self.root = fresh_dir(self)
        self.state = os.path.join(self.root, "state")
        self.store_dir = os.path.join(self.root, "workflows")
        os.mkdir(self.state, 0o700)
        os.mkdir(self.store_dir, 0o700)
        self.clock = Clock()
        check = mock.patch.object(repository_module, "validate_repository",
                                  pure_repository_check)
        check.start()
        self.addCleanup(check.stop)
        self.operator_codex = OperatorCodex()
        self.planning = PlanningCodex()
        self.operator_session = operator_session.RestrictedCodexOperatorSession(
            runner=self.operator_codex)
        self.planning_session = (
            operator_session_codex.RestrictedPlanningOperatorSession(
                clock=self.clock, runner=self.planning))
        self.ids = itertools.count(1)
        self.route = conforming.WorkflowRoute(
            store.WorkflowStore(self.store_dir), CONTROL,
            planning_session=self.planning_session,
            workflow_id_factory=lambda: "wf-i3-%04d" % next(self.ids))
        self.index = index_module.RequestIndex(self.state)
        self.surface = surface_module.build_bridgeless_surface(
            self.state, self.clock)
        self.adapter = self.make_adapter()

    def make_adapter(self, route=None, **kwargs):
        kwargs.setdefault("legacy_result", cli_module.recover_legacy_result)
        return adapter_module.GrokBotAdapter(
            self.surface, self.operator_session, None, self.index, self.clock,
            local_command=["grokbot.py", "--state-dir", self.state,
                           "--workflow-store", self.store_dir],
            route=route if route is not None else self.route, **kwargs)

    # -- assertions -----------------------------------------------------

    def ok(self, result):
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["transport"], "grok_bot")
        return result

    def refused(self, problem, result):
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("problem"), problem, result)
        self.assertEqual(result["delivery_authority"], "none")
        return result

    # -- reads ------------------------------------------------------------

    def workflows(self):
        return store.WorkflowStore(self.store_dir).load()["workflows"]

    def put(self, entry):
        with store.exclusive_store_lock(self.store_dir):
            document = store.WorkflowStore(self.store_dir).load()
            document["workflows"][entry["workflow_id"]] = entry
            store.WorkflowStore(self.store_dir).save(document)

    def edit(self, workflow_id, change):
        """Change one durable record the way a writer does (load-modify-
        save under the writer lock), validating it."""
        with store.exclusive_store_lock(self.store_dir):
            document = store.WorkflowStore(self.store_dir).load()
            change(document["workflows"][workflow_id])
            store.WorkflowStore(self.store_dir).save(document)

    # -- flows ------------------------------------------------------------

    def requested(self, text=TEXT, **kwargs):
        return self.ok(self.adapter.request(text=text, **kwargs))

    def presented(self, ref):
        return self.ok(self.adapter.present(request_ref=ref))

    def arm(self, shown):
        binding = shown["approval_binding"]
        return authorize.arm_workflow(
            self.route, self.index, self.clock, binding["request_ref"],
            binding["workflow_id"], binding["revision"],
            binding["authorization_digest_sha256"], binding["expires_at"],
            shown["display_digest_sha256"])["approval_code"]

    def approved(self, text=TEXT):
        out = self.requested(text)
        shown = self.presented(out["request_ref"])
        args = dict(shown["approval_binding"], relayed_reply="approved",
                    relay_ref="grok-message-1", approval_code=self.arm(shown))
        self.ok(self.adapter.approve(**args))
        held = self.index.workflow_request(out["request_ref"])
        return out["request_ref"], held["workflow_id"]

    def status(self, ref):
        return self.ok(self.adapter.status(request_ref=ref))

    def result(self, ref):
        return self.ok(self.adapter.result(request_ref=ref))


# --- Legacy fixture: a REAL legacy Mission-Core run, recorded the pre-I3
# way (local proposal, operator-attested approval, the real MissionBridge
# with its effect seams injected). Shapes copied from test_mission_bridge.

LEGACY_REPO_URL = "https://github.com/Example/Repo"
LEGACY_BASELINE = "a" * 40
LEGACY_SURFACE = "b" * 64
LEGACY_TASK_ID = "task-0001"
LEGACY_CONTEXT = None  # set lazily (mission_record is loaded at module setup)
RESULT_TEXT = "# Task checkpoint\n\nThe readiness probe fix landed with tests.\n"
REVIEW_TEXT = ("Reviewer: `reviewer1` / `session-1`\n\n## Transcript\n\n"
               "Protocol token: APPROVE\n")
TESTS_PASS_DIGEST = hashlib.sha256(b"the focused suite passed").hexdigest()


def legacy_context():
    return mission_record.AuthenticatedContext(
        transport="synthetic_test", principal_kind="local_process_user",
        principal_ref="synthetic-tester")


def legacy_run_request():
    """A legacy proposal whose contract declares the run-result
    requirement (test_local_request.request + test_mission_bridge
    .run_contract, copied)."""
    requirement = {
        "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
        "required_artifact_keys": [], "max_evidence_age_seconds": 3600,
    }
    return {
        "objective": "Investigate and resolve the flaky readiness probe",
        "target_context": "control repository, readiness subsystem",
        "repository_url": LEGACY_REPO_URL,
        "requested_scope": "readiness probe and its tests; no schema change",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": None,
        "proof_contract": {
            "requirements": [
                dict(requirement, key="tests_pass",
                     description="the focused suite passes"),
                dict(requirement, key="run_result",
                     description="the bound target result"),
            ],
            "required_artifacts": [], "required_dependencies": [],
            "required_resource_readiness": [],
            "degradation_policy": {"permitted_blocker_keys": []},
            "continuation_budget": {"max_attempts": 1, "max_checkpoints": 2},
        },
    }


class LegacyTransport(object):
    """The bridge's git transport shape, pure Python (copied from
    test_mission_bridge.FakeTransport): a "worktree" is a real temporary
    directory with the target's state directory and Git's reciprocal
    metadata, under a real ``git_common`` directory."""

    def __init__(self):
        self.worktrees = {}
        self.git_common = None

    def toplevel(self, path):
        return os.path.realpath(path)

    def common_dir(self, path):
        return self.git_common

    def worktree_list(self, repository):
        fields = ["worktree %s" % repository, "HEAD %s" % LEGACY_BASELINE,
                  "branch refs/heads/main", ""]
        for path, (head, reason) in sorted(self.worktrees.items()):
            fields += ["worktree %s" % path, "HEAD %s" % head, "detached",
                       "locked %s" % reason, ""]
        return "\0".join(fields) + "\0"

    def add_worktree(self, repository, path, commit_sha, lock_reason):
        os.makedirs(os.path.join(path, ".herd", "state", "reviews"))
        real = os.path.realpath(path)
        admin = os.path.join(self.git_common, "worktrees",
                             os.path.basename(real))
        os.makedirs(admin)
        with open(os.path.join(admin, "gitdir"), "w") as handle:
            handle.write(os.path.join(real, ".git") + "\n")
        with open(os.path.join(real, ".git"), "w") as handle:
            handle.write("gitdir: %s\n" % admin)
        self.worktrees[real] = (commit_sha, lock_reason)

    def remote_url(self, path):
        return LEGACY_REPO_URL + "\n"

    def head_commit(self, path):
        return LEGACY_BASELINE + "\n"

    def status_porcelain_readonly(self, path):
        return {"status": "captured", "text": "", "total_bytes": 0}


def legacy_raw_observation(status="RUNNING"):
    return {
        "completeness": "PARTIAL",
        "diagnostics": [{"source": "agents", "state": "unavailable",
                         "detail": "agents not probed"}],
        "task": {"state": "available", "id": LEGACY_TASK_ID,
                 "status": status},
        "reviews": {"state": "available", "truncated": False,
                    "listed": [{"round": 1, "decision": "APPROVE"}]},
        "artifacts": {"state": "available", "listed": []},
    }


class LegacyObserver(object):
    def __init__(self):
        self.raw = legacy_raw_observation()
        self.tasks = {}

    def __call__(self, path):
        raw = json.loads(json.dumps(self.raw))
        child = self.tasks.get(os.path.realpath(path), {})
        raw["task"].setdefault("description", child.get("description"))
        raw["task"].setdefault("started_at", child.get("started_at"))
        return raw


class LegacySpawn(object):
    """Records every request; starts nothing (models herd's child record
    and the child's task description)."""

    def __init__(self, observer, clock):
        self.observer, self.clock = observer, clock
        self.calls, self.records = [], []

    def __call__(self, parent_repo, request_):
        self.calls.append((parent_repo, dict(request_)))
        self.observer.tasks[request_["target_repo"]] = {
            "description": request_["task"][:200],
            "started_at": self.clock()}
        self.records.append({"repo": request_["target_repo"],
                             "task_id": LEGACY_TASK_ID})
        return {"task": {"id": LEGACY_TASK_ID},
                "child_record": {"task_id": LEGACY_TASK_ID},
                "repo": request_["target_repo"]}


class LegacyTrust(object):
    def establish_workspace_trust(self, record_):
        return True, None, None

    def workspace_trust_consumable(self, record_):
        return True, None, None


class LegacyOwnership(object):
    def owned_groups(self, directory=None):
        return set()

    def reap_owned(self, pgid, directory=None, **kwargs):
        raise HermeticViolation("the legacy fixture reaped %r" % (pgid,))


class LegacyRun(object):
    """The pre-I3 LOCAL operator route, run by this test as the operator
    would have run it: propose, present, attest "approved", then the real
    ``MissionBridge`` dispatch / observe / verify with every effect seam
    injected (no process, no git, no network). Its own Mission Core and
    request-store instances over the SAME state directory, like a separate
    ``direquest.py`` process."""

    def __init__(self, case):
        self.case = case
        self.clock = case.clock
        self.missions = mission_service.MissionService(
            mission_store.MissionStore(case.state), self.clock)
        self.surface = surface_module.LocalRequestSurface(
            self.missions, request_store.LocalRequestStore(case.state),
            self.clock)
        self.observer = LegacyObserver()
        self.spawn = LegacySpawn(self.observer, self.clock)
        self.transport = LegacyTransport()
        base = fresh_dir(case, "legacy")
        self.repository = os.path.join(base, "repository")
        self.workspaces = os.path.join(base, "workspaces")
        os.mkdir(self.repository)
        os.mkdir(self.workspaces)
        self.transport.git_common = os.path.join(
            os.path.realpath(self.repository), ".git")
        os.mkdir(self.transport.git_common)
        self.bridge = bridge_module.MissionBridge(
            self.missions, "/control-repo", self.clock,
            spawn_fn=self.spawn, observer_fn=self.observer,
            spawn_records_fn=self.records, transport=self.transport,
            surface_digest_fn=lambda repo: {"status": "exact",
                                            "digest": LEGACY_SURFACE},
            ownership=LegacyOwnership(), owner_directory="/owner-scope",
            workspace_repository=self.repository,
            workspaces_root=self.workspaces, worker=LegacyTrust())

    def records(self, repo):
        listed = [dict(r) for r in self.spawn.records]
        return {"state": "available" if listed else "empty",
                "truncated": False, "listed": listed, "count": len(listed)}

    def workspace(self, mission_id):
        return os.path.join(os.path.realpath(self.workspaces), mission_id)

    def approved(self):
        out = self.surface.submit(legacy_run_request())
        presented = self.surface.present(out["request_ref"])
        self.surface.attest_approval(
            request_ref=out["request_ref"],
            mission_id=presented["mission_id"],
            revision=presented["revision"],
            proposal_digest_sha256=presented["proposal_digest_sha256"],
            approved_action_scope=presented["approved_action_scope"],
            approved_delivery_targets=presented["approved_delivery_targets"],
            expires_at=self.clock() + 600, relayed_reply="approved",
            relay_ref="synthetic-relay")
        return out["request_ref"], out["mission_id"]

    def state_op(self, mission_id, method, *args):
        context = legacy_context()
        operation = self.missions.mint_state_operation_id(context)
        sequence = self.missions.get_state(mission_id)["sequence"]
        return method(mission_id, operation, sequence, *(args + (context,)))

    def verified(self):
        """A COMPLETED legacy run with an accepted, recoverable result."""
        ref, mission_id = self.approved()
        self.bridge.dispatch(mission_id)
        self.observer.raw = legacy_raw_observation()
        self.bridge.observe(mission_id)
        state_dir = os.path.join(self.workspace(mission_id), ".herd", "state")
        with open(os.path.join(state_dir, "task-checkpoint.md"), "w") as f:
            f.write(RESULT_TEXT)
        name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (LEGACY_TASK_ID, 1)
        with open(os.path.join(state_dir, "reviews", name), "w") as f:
            f.write(REVIEW_TEXT)
        submitted = self.state_op(
            mission_id, self.missions.submit_evidence, "tests_pass",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
            TESTS_PASS_DIGEST, [])
        self.state_op(mission_id, self.missions.accept_evidence,
                      submitted["evidence_id"], TESTS_PASS_DIGEST)
        self.observer.raw = legacy_raw_observation(status="COMPLETE")
        outcome = self.bridge.verify(mission_id, {
            "task_id": LEGACY_TASK_ID,
            "result_digest_sha256": hashlib.sha256(
                RESULT_TEXT.encode("utf-8")).hexdigest(),
            "review_digest_sha256": hashlib.sha256(
                REVIEW_TEXT.encode("utf-8")).hexdigest()})
        self.case.assertEqual(outcome["state"], "COMPLETED", outcome)
        return ref, mission_id

    def review_path(self, mission_id):
        return os.path.join(
            self.workspace(mission_id), ".herd", "state", "reviews",
            evidence_module.REVIEW_ROUND_FILE_FORMAT % (LEGACY_TASK_ID, 1))

    def checkpoint_path(self, mission_id):
        return os.path.join(self.workspace(mission_id), ".herd", "state",
                            "task-checkpoint.md")


# --- Tripwires around a Grok call -------------------------------------------

class Tripwires(object):
    """Every seam that can start, dispatch or bridge work, tripwired for
    the duration of a Grok call: reaching any one RECORDS it and raises."""

    def __init__(self):
        self.trips = []
        self._patchers = []

    def _wire(self, owner, name):
        trips = self.trips
        label = "%s.%s" % (getattr(owner, "__name__", owner), name)

        def tripped(*args, **kwargs):
            trips.append(label)
            raise HermeticViolation("Grok Bot reached %s" % label)
        patcher = mock.patch.object(owner, name, tripped)
        patcher.start()
        self._patchers.append(patcher)

    def __enter__(self):
        for owner, name in (
            (dispatch_module, "production_spawn"),
            (herdr_orchestrator, "execute_spawn_request"),
            (bridge_module.MissionBridge, "__init__"),
            (broker_module.TargetBroker, "__init__"),
            (local_request_cli, "build_bridge"),
            (local_request_cli, "configured_bridge_factory"),
            (local_request_cli, "build_surface"),
            (surface_module.LocalRequestSurface, "run_command"),
            (runtime_module, "process_once"),
        ):
            self._wire(owner, name)
        return self

    def __exit__(self, *exc):
        while self._patchers:
            self._patchers.pop().stop()
        return False


# ===========================================================================
# B-2 — the bypass is closed, STATICALLY
# ===========================================================================

# Modules on (or leading to) the dispatch path. None may be in Grok Bot's
# transitive import closure; ``target_runtime.evidence`` is here because it
# imports ``target_runtime.dispatch`` at module level.
DISPATCH_PATH_MODULES = frozenset({
    "target_runtime.dispatch", "target_runtime.mission_bridge",
    "target_runtime.broker", "target_runtime.runtime", "target_runtime.cli",
    "target_runtime.evidence", "target_runtime.worker",
    "herdr.orchestrator", "herdr.control_plane", "local_request.cli",
})
# Names that would mean Grok Bot holds or builds an execution path.
EXECUTION_NAMES = frozenset({
    "production_spawn", "execute_spawn_request", "MissionBridge",
    "TargetBroker", "build_bridge", "configured_bridge_factory",
    "bridge_factory", "run_command", "request_cli", "mission_bridge",
    "process_once", "build_surface",
})


def module_file(name):
    """The repository file defining module ``name``, or None (stdlib or
    third-party)."""
    base = os.path.join(REPO, *name.split("."))
    if os.path.isfile(base + ".py"):
        return base + ".py"
    if os.path.isfile(os.path.join(base, "__init__.py")):
        return os.path.join(base, "__init__.py")
    return None


def imported_modules(source, own_name):
    """Every repository module ``source`` imports, at ANY depth of the AST
    (function-level and conditional imports included), with the parent
    packages an import also executes."""
    found = set()
    package = own_name.rsplit(".", 1)[0] if "." in own_name else ""
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = package.split(".")
                stem = parts[:len(parts) - node.level + 1] if node.level > 1 \
                    else parts
                module = ".".join([p for p in stem if p] + (
                    [module] if module else []))
            names = [module] + ["%s.%s" % (module, alias.name)
                                for alias in node.names]
        for name in names:
            parts = name.split(".")
            for length in range(1, len(parts) + 1):
                candidate = ".".join(parts[:length])
                if module_file(candidate) is not None:
                    found.add(candidate)
    return found


def import_closure(roots):
    """The transitive closure of repository modules from ``roots`` (each a
    ``(module_name, source)``), walking files only: nothing is imported."""
    seen = set()
    pending = []
    for name, source in roots:
        pending.extend(imported_modules(source, name))
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        with open(module_file(name), encoding="utf-8") as handle:
            pending.extend(imported_modules(handle.read(), name))
    return seen


def grok_roots():
    roots = []
    for file_name in sorted(os.listdir(os.path.join(REPO, "grok_bot"))):
        if file_name.endswith(".py"):
            name = "grok_bot." + file_name[:-3]
            if file_name == "__init__.py":
                name = "grok_bot"
            with open(os.path.join(REPO, "grok_bot", file_name),
                      encoding="utf-8") as handle:
                roots.append((name, handle.read()))
    with open(os.path.join(REPO, "grokbot.py"), encoding="utf-8") as handle:
        roots.append(("grokbot", handle.read()))
    return roots


def code_names(source):
    """Every identifier and attribute name in ``source``'s CODE (not its
    strings or docstrings)."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.split(".")[-1])
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


class StaticBypassClosureTests(unittest.TestCase):

    def test_B2_grok_import_closure_excludes_the_dispatch_path(self):
        closure = import_closure(grok_roots())
        self.assertEqual(sorted(closure & DISPATCH_PATH_MODULES), [])
        # Anti-vacuity: the closure is real and reaches what Grok uses.
        for expected in ("local_request.surface", "workflow_authority.store",
                         "codex_gateway.role_turn",
                         "target_runtime.legacy_result",
                         "target_runtime.state_artifact"):
            self.assertIn(expected, closure)

    def test_B2_the_closure_detector_fires_on_the_retired_shape(self):
        # Planted: the import Grok Bot's cli carried before I3. Its closure
        # reaches the spawn seam, so this detector would have failed it.
        planted = import_closure([("grok_bot.cli",
                                   "from local_request import cli\n")])
        for module in ("local_request.cli", "target_runtime.mission_bridge",
                       "target_runtime.dispatch", "herdr.orchestrator"):
            self.assertIn(module, planted)
        nested = import_closure([("grok_bot.x", (
            "def f():\n    from target_runtime import broker\n"))])
        self.assertIn("target_runtime.dispatch", nested)

    def test_B2_legacy_result_recovery_closure_excludes_the_dispatch_path(self):
        with open(module_file("target_runtime.legacy_result")) as handle:
            closure = import_closure([("target_runtime.legacy_result",
                                       handle.read())])
        self.assertEqual(sorted(closure & DISPATCH_PATH_MODULES), [])
        self.assertIn("target_runtime.state_artifact", closure)
        self.assertEqual(sorted(n for n in closure if n.startswith("herdr")),
                         [])

    def test_B2_the_hardened_reader_imports_only_the_primitive(self):
        with open(module_file("target_runtime.state_artifact")) as handle:
            tree = ast.parse(handle.read())
        imports = sorted(
            [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names]
            + ["%s.%s" % (n.module, a.name) for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) for a in n.names])
        self.assertEqual(imports, ["os", "target_runtime.prepare"])

    def test_B2_no_grok_source_names_an_execution_path(self):
        for name, source in grok_roots():
            with self.subTest(module=name):
                self.assertEqual(sorted(code_names(source) & EXECUTION_NAMES),
                                 [])

    def test_B2_the_name_detector_fires_on_planted_probes(self):
        planted = ("from local_request import cli as request_cli\n"
                   "def f(bridge_factory=None):\n"
                   "    return surface.run_command(r, 'dispatch')\n")
        self.assertEqual(sorted(code_names(planted) & EXECUTION_NAMES),
                         ["bridge_factory", "request_cli", "run_command"])

    def test_B2_cli_offers_no_bridge_to_inject(self):
        import inspect
        self.assertNotIn("bridge_factory",
                         inspect.signature(cli_module.main).parameters)
        self.assertNotIn("bridge_factory",
                         inspect.signature(cli_module._serve).parameters)
        self.assertFalse(hasattr(cli_module, "_bridge_factory"))
        self.assertFalse(hasattr(cli_module, "request_cli"))

    def test_B2_the_moved_reader_and_recovery_are_one_implementation(self):
        # The hardened reader: evidence re-exports the SAME function.
        self.assertIs(evidence_module.read_state_artifact,
                      state_artifact.read_state_artifact)
        self.assertIs(evidence_module._resolved_chain,
                      state_artifact._resolved_chain)
        # The recovery: MissionBridge.result delegates to it, and keeps no
        # read or re-hash of its own.
        self.assertIs(bridge_module.result_binding,
                      legacy_result.result_binding)
        self.assertEqual(bridge_module.RESULT_ARTIFACT_KEY,
                         legacy_result.RESULT_ARTIFACT_KEY)
        import inspect
        source = inspect.getsource(bridge_module.MissionBridge.result)
        self.assertIn("legacy_result_module.recover_result", source)
        self.assertNotIn("read_state_artifact", source)
        self.assertNotIn("json_digest", source)

    def test_B2_the_projection_never_reads_a_telegram_delivery_marker(self):
        with open(os.path.join(REPO, "grok_bot", "conforming.py")) as handle:
            source = handle.read()
        literals = {node.value for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)}
        for marker in ("result_delivery", "result_placeholder", "telegram"):
            self.assertNotIn(marker, literals)


# ===========================================================================
# B-2 — the bypass is closed, BEHAVIOURALLY
# ===========================================================================

RUN_SHAPES = {
    "dispatch": {}, "observe": {}, "reconcile": {}, "result": {},
    "pause": {}, "resume": {}, "cancel": {},
    "verify": {"reported_result": {"task_id": "t"}},
    "prove": {"operation": "submit_evidence", "arguments": {}},
}


class BehaviouralBypassTests(GrokCase):

    def setUp(self):
        super(BehaviouralBypassTests, self).setUp()
        self.legacy = LegacyRun(self)
        self.legacy_ref, _ = self.legacy.approved()
        self.workflow_ref = self.requested()["request_ref"]

    def test_B2_every_run_command_on_both_lanes_reaches_no_spawn_seam(self):
        self.assertEqual(sorted(RUN_SHAPES),
                         sorted(surface_module.LocalRequestSurface.RUN_COMMANDS))
        with Tripwires() as wires:
            for ref in (self.legacy_ref, self.workflow_ref):
                for command, arguments in sorted(RUN_SHAPES.items()):
                    with self.subTest(ref=ref, command=command):
                        result = self.adapter.run(
                            request_ref=ref, command=command,
                            arguments=arguments)
                        if command == "result":
                            self.ok(result)
                        else:
                            self.assertFalse(result["ok"], result)
        self.assertEqual(wires.trips, [])
        self.assertIsNone(self.surface._bridge)
        self.assertEqual(self.legacy.spawn.calls, [])

    def test_B2_every_tool_reaches_no_spawn_seam(self):
        calls = [
            ("status", {"request_ref": self.legacy_ref}),
            ("status", {"request_ref": self.workflow_ref}),
            ("result", {"request_ref": self.legacy_ref}),
            ("result", {"request_ref": self.workflow_ref}),
            ("present", {"request_ref": self.legacy_ref}),
            ("present", {"request_ref": self.workflow_ref}),
            ("recover", {"request_ref": self.legacy_ref}),
            ("recover", {"request_ref": self.workflow_ref}),
            ("approve", {"request_ref": self.legacy_ref, "revision": 1,
                         "expires_at": NOW, "relayed_reply": "approved",
                         "relay_ref": "r", "approval_code": "0" * 32}),
            ("cancel", {"request_ref": self.workflow_ref,
                        "control_capability": "x"}),
        ]
        with Tripwires() as wires:
            for tool, arguments in calls:
                with self.subTest(tool=tool, ref=arguments["request_ref"]):
                    self.adapter.call(tool, arguments)
        self.assertEqual(wires.trips, [])
        self.assertEqual(self.legacy.spawn.calls, [])

    def test_B2_call_run_through_the_cli_builds_no_bridge(self):
        for flags in ([], ["--control-repo", CONTROL],
                      ["--control-repo", CONTROL, "--workspace-repository",
                       self.legacy.repository, "--workspaces-root",
                       self.legacy.workspaces]):
            for command in ("dispatch", "result"):
                with self.subTest(flags=flags, command=command):
                    out = io.StringIO()
                    with Tripwires() as wires:
                        code = cli_module.main(
                            ["--state-dir", self.state] + flags
                            + ["call", "run"],
                            stdin=io.StringIO(json.dumps({
                                "request_ref": self.legacy_ref,
                                "command": command})),
                            stdout=out, clock=self.clock)
                    self.assertEqual(wires.trips, [])
                    result = json.loads(out.getvalue())
                    if command == "dispatch":
                        self.assertEqual(code, cli_module.EXIT_REFUSED)
                        self.assertEqual(
                            result["problem"],
                            conforming.PROBLEM_LEGACY_REAUTHORIZATION)
                    else:
                        self.assertEqual(code, cli_module.EXIT_OK, result)
        self.assertEqual(self.legacy.spawn.calls, [])

    def test_B2_serve_builds_an_adapter_holding_no_execution_handle(self):
        token = os.path.join(self.root, "token")
        with open(token, "w") as handle:
            handle.write("t" * 43 + "\n")
        os.chmod(token, 0o600)
        built = []

        class Listener(object):
            url = "http://127.0.0.1:1/mcp"
            server_port = 1
            access_control = "bearer"

            def __init__(self, adapter, port, log=None, bearer_token=None):
                built.append(adapter)

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        out = io.StringIO()
        with Tripwires() as wires, mock.patch.object(
            server_module, "LoopbackMcpServer", Listener
        ):
            code = cli_module.main(
                ["--state-dir", self.state, "--control-repo", CONTROL,
                 "--workflow-store", self.store_dir, "--workspace-repository",
                 self.legacy.repository, "--workspaces-root",
                 self.legacy.workspaces, "serve", "--auth-token-file", token],
                stdout=out, clock=self.clock,
                operator_session=self.operator_session,
                planning_session=self.planning_session)
        self.assertEqual(code, cli_module.EXIT_OK, out.getvalue())
        self.assertEqual(wires.trips, [])
        self.assertEqual(len(built), 1)
        adapter = built[0]
        self.assertIsNone(adapter._surface._bridge)
        # The one legacy-result handle is a plain module function of the
        # Grok CLI that closes over nothing.
        self.assertIs(adapter._legacy_result, cli_module.recover_legacy_result)
        self.assertIsNone(adapter._legacy_result.__closure__)
        held = [value for value in vars(adapter).values()]
        for value in held:
            self.assertNotIsInstance(value, bridge_module.MissionBridge)
            self.assertNotIsInstance(value, broker_module.TargetBroker)


class OperatorSurfaceSeparationTests(unittest.TestCase):
    """The LOCAL operator surface (``direquest.py``) keeps its run commands
    under the operator's own authority; Grok Bot's surface has none."""

    def test_B2_direquest_keeps_its_run_route_and_grok_has_none(self):
        self.assertEqual(
            sorted(local_request_cli.RUN_COMMANDS),
            sorted(c if c != "cancel" else "cancel-run"
                   for c in surface_module.LocalRequestSurface.RUN_COMMANDS))
        # direquest builds the real bridge for its run commands, lazily.
        self.assertTrue(callable(local_request_cli.build_bridge))
        with tempfile.TemporaryDirectory(dir=_ROOT[0]) as state:
            grok_surface = surface_module.build_bridgeless_surface(
                state, Clock())
            self.assertIsNone(grok_surface._bridge)
            with self.assertRaises(surface_module.LocalRequestRefusal) as e:
                grok_surface.run_command("lr-x", "dispatch")
            self.assertEqual(e.exception.problem,
                             surface_module.PROBLEM_RUN_UNAVAILABLE)


# ===========================================================================
# B-2 / B-3 item 5 — status: a READ-ONLY, record-attributed snapshot
# ===========================================================================

# Words that would claim a present condition, monitoring freshness or an
# inferred stall/progress. None may appear in a status, anywhere (field
# names, values, statements); the record-attributed phrasing is used.
PRESENT_CLAIMS = ("last checked", "monitoring", "currently", "is running",
                  "runtime: running", "nothing has changed since",
                  "stalled", "stale", "healthy", "is advancing",
                  "workflow is in progress", "work in progress", "as of t=")
_NEGATION = re.compile(r"\b(not|no|never|neither|nor|without)\b")


def flat(value):
    return json.dumps(value, sort_keys=True).lower()


def affirmed_claims(value):
    """Each PRESENT_CLAIMS phrase that occurs WITHOUT a negation in the 40
    characters before it ("not evidence that this workflow is advancing"
    denies the claim; "the workflow is advancing" would make it)."""
    text = flat(value)
    found = []
    for claim in PRESENT_CLAIMS:
        start = text.find(claim)
        while start != -1:
            if not _NEGATION.search(text[max(0, start - 40):start]):
                found.append(claim)
            start = text.find(claim, start + 1)
    return found


def receipt(summary, recorded_at, kind="evidence"):
    return {"kind": kind, "turn_id": "turn-%d" % recorded_at,
            "recorded_at": recorded_at,
            "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "bounded_summary": summary}


class ProjectionCase(GrokCase):

    def setUp(self):
        super(ProjectionCase, self).setUp()
        self.ref, self.workflow_id = self.approved()

    def runtime_lock_path(self):
        return os.path.join(self.store_dir, conforming.RUNTIME_LOCK_FILE_NAME)

    def hold_runtime_lock(self):
        """The Runtime lock GENUINELY held by another holder: the
        production ``acquire_runtime_lock`` on its own descriptor."""
        descriptor = runtime_cli.acquire_runtime_lock(self.store_dir)
        self.assertIsNotNone(descriptor)
        self.addCleanup(os.close, descriptor)
        return descriptor

    def dispatched(self, observed_at, pair=("ACTIVE", "PARTIAL")):
        def change(entry):
            for phase in (record.PHASE_WORKSPACE_READY, record.PHASE_PREPARED,
                          record.PHASE_VALIDATED, record.PHASE_DISPATCHED):
                record.apply_transition(entry, phase)
            entry["receipts"].append(receipt(
                "dispatched handoff revision 1 to octocat/widget (dispatch"
                " 1, exact)", NOW - 5000))
            entry["target_engine"] = {"alias": "di-remote-2-x",
                                      "task_id": TARGET_TASK_ID,
                                      "repo": CANONICAL_URL,
                                      "dispatched_at": NOW - 5000}
            entry["last_observation"] = {"task_status": pair[0],
                                         "completeness": pair[1],
                                         "observed_at": observed_at}
        self.edit(self.workflow_id, change)


class WorkflowStatusProjectionTests(ProjectionCase):

    def test_B2_status_is_read_only_and_creates_nothing(self):
        before = tree(self.root)
        for _ in range(3):
            self.status(self.ref)
            self.result(self.ref)
        self.assertEqual(tree(self.root), before)
        # Notably, no lock file is created by a read: neither the writer's
        # nor the Runtime's.
        self.assertFalse(os.path.exists(self.runtime_lock_path()))

    def test_B2_status_is_labelled_a_snapshot_as_of_the_read(self):
        self.clock.now = NOW + 77
        status = self.status(self.ref)
        self.assertEqual(status["snapshot"]["read_at"], NOW + 77)
        statement = status["snapshot"]["statement"]
        self.assertEqual(statement, conforming.SNAPSHOT_STATEMENT)
        self.assertIn("MAY PRECEDE a commit", statement)
        self.assertIn("not the time of any commit", statement)
        self.assertIn("claims nothing about the present", statement)
        # The withdrawn quantitative bound never comes back.
        self.assertNotIn("microsecond", flat(status))

    def test_B2_status_projects_phase_receipts_turns_and_dispatches(self):
        self.dispatched(NOW - 4000)
        status = self.status(self.ref)
        self.assertEqual(status["phase"], record.PHASE_DISPATCHED)
        projected = status["record"]
        self.assertEqual(projected["dispatches"], {
            "recorded": 1, "follow_ups_recorded": 0,
            "follow_up_bound": dispatch_module.MAX_FOLLOW_UP_DISPATCHES,
            "follow_up_bound_reached": False})
        self.assertEqual(projected["receipts"]["count"],
                         len(self.workflows()[self.workflow_id]["receipts"]))
        self.assertEqual(projected["receipts"]["most_recent"][-1]["summary"],
                         "dispatched handoff revision 1 to octocat/widget"
                         " (dispatch 1, exact)")
        self.assertEqual(projected["target_engine"]["task_id"],
                         TARGET_TASK_ID)
        self.assertTrue(projected["target_engine"]["identity_resolved"])
        self.assertIsNone(projected["verified_result"])
        self.assertIsNone(projected["stop"])

    def test_B3_5_a_stable_target_with_an_old_observation_is_NOT_reported_stalled(self):
        # Ten days since the last DISTINCT observation: for a stable target
        # that is the normal shape (the Runtime records a pair only when it
        # changes), so nothing may call it stalled, stale or unmonitored.
        old = NOW - 10 * 86400
        self.dispatched(old, pair=("ACTIVE", "PARTIAL"))
        self.hold_runtime_lock()
        status = self.status(self.ref)
        observation = status["record"]["last_recorded_observation"]
        self.assertEqual(observation["task_status"], "ACTIVE")
        self.assertEqual(observation["completeness"], "PARTIAL")
        self.assertEqual(observation["recorded_at"], old)
        self.assertEqual(observation["age_at_read_seconds"], 10 * 86400)
        self.assertFalse(observation["unobservable_at_that_observation"])
        self.assertIn("no subsequent distinct observation is recorded",
                      observation["statement"])
        self.assertIn("proves neither present health nor present failure",
                      observation["statement"])
        # Nothing anywhere infers a stall, a stale monitor or health.
        self.assertEqual(affirmed_claims(status), [])
        # The phase is reported exactly as recorded, and the next step
        # carries no runtime-recovery remedy while the lock is held.
        self.assertEqual(status["phase"], record.PHASE_DISPATCHED)
        self.assertNotIn("dirun-agent.sh", status["next"])

    def test_B3_5_an_unobservable_pair_is_surfaced_for_that_observation_only(self):
        self.dispatched(NOW - 600, pair=(None, None))
        observation = self.status(
            self.ref)["record"]["last_recorded_observation"]
        self.assertIsNone(observation["task_status"])
        self.assertIsNone(observation["completeness"])
        self.assertTrue(observation["unobservable_at_that_observation"])
        self.assertIn(conforming.UNOBSERVABLE_STATEMENT,
                      observation["statement"])
        self.assertIn("not necessarily now", observation["statement"])

    def test_B3_5_a_held_runtime_lock_is_not_workflow_progress(self):
        # LOCK HELD != workflow advancing: the same record projects
        # IDENTICALLY whether a process holds the Runtime lock or not; only
        # the separately labelled lock probe differs, and it says so.
        self.dispatched(NOW - 300)
        os.close(os.open(self.runtime_lock_path(),
                         os.O_RDWR | os.O_CREAT, 0o600))
        unheld = self.status(self.ref)
        self.hold_runtime_lock()
        held = self.status(self.ref)
        self.assertEqual(unheld["record"], held["record"])
        self.assertEqual(unheld["phase"], held["phase"])
        self.assertEqual(held["runtime_lock"]["state"], conforming.LOCK_HELD)
        self.assertEqual(held["runtime_lock"]["statement"],
                         conforming.RUNTIME_LOCK_STATEMENT)
        self.assertIn("not evidence that this workflow is advancing",
                      held["runtime_lock"]["statement"])
        self.assertNotIn("remedy", held["runtime_lock"])
        self.assertEqual(unheld["runtime_lock"]["state"],
                         conforming.LOCK_UNHELD)
        self.assertIn("remedy", unheld["runtime_lock"])
        self.assertEqual(affirmed_claims(held), [])

    def test_B3_5_no_progress_is_inferred_from_record_timestamps(self):
        self.dispatched(NOW - 300)
        status = self.status(self.ref)
        projected = status["record"]
        self.assertEqual(projected["progress_statement"],
                         conforming.PROGRESS_STATEMENT)
        self.assertIn("DI records no heartbeat",
                      projected["progress_statement"])
        # The latest time the record carries (its approval, at NOW), not
        # the observation's, and nothing is derived from either.
        self.assertEqual(projected["last_recorded_change_at"], NOW)
        # No field claims progress, liveness or a stall.
        keys = set()

        def walk(value):
            if isinstance(value, dict):
                for key, inner in value.items():
                    keys.add(key.lower())
                    walk(inner)
            elif isinstance(value, list):
                for inner in value:
                    walk(inner)
        walk(status)
        for word in ("progress", "running", "alive", "stall", "stale",
                     "healthy", "advancing"):
            self.assertEqual(
                sorted(k for k in keys if word in k
                       and k != "progress_statement"), [], word)

    def test_B3_5_a_stop_with_a_recorded_reason_names_it(self):
        def change(entry):
            for phase in (record.PHASE_WORKSPACE_READY, record.PHASE_PREPARED,
                          record.PHASE_VALIDATED, record.PHASE_DISPATCHED):
                record.apply_transition(entry, phase)
            entry["receipts"].append(receipt(
                "verification blocked: broker_review_not_approved — the"
                " canonical review decision is REJECT", NOW - 20))
            record.apply_transition(entry, record.PHASE_BLOCKED)
        self.edit(self.workflow_id, change)
        status = self.status(self.ref)
        stop = status["record"]["stop"]
        self.assertTrue(stop["reason_recorded"])
        self.assertEqual(stop["reason"]["marker"], "verification blocked")
        self.assertEqual(stop["reason"]["problem_code"],
                         "broker_review_not_approved")
        self.assertIsNone(stop["statement"])
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, status["next"])

    def test_B3_5_a_stop_without_a_recorded_reason_says_so(self):
        def change(entry):
            for phase in (record.PHASE_WORKSPACE_READY, record.PHASE_PREPARED,
                          record.PHASE_VALIDATED, record.PHASE_DISPATCHED):
                record.apply_transition(entry, phase)
            record.apply_transition(entry,
                                    record.PHASE_NEEDS_REAUTHORIZATION)
        self.edit(self.workflow_id, change)
        status = self.status(self.ref)
        stop = status["record"]["stop"]
        self.assertFalse(stop["reason_recorded"])
        self.assertIsNone(stop["reason"])
        self.assertEqual(stop["statement"],
                         conforming.NO_STOP_REASON_STATEMENT)
        self.assertIn("needs a new human authorization", status["next"])
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, status["next"])

    def test_B2_a_malformed_store_fails_closed(self):
        path = os.path.join(self.store_dir, store.WORKFLOWS_FILE_NAME)
        with open(path, "w") as handle:
            handle.write("{not json")
        for tool in ("status", "result"):
            with self.subTest(tool=tool):
                result = self.adapter.call(tool, {"request_ref": self.ref})
                self.refused(conforming.PROBLEM_WORKFLOW_STORE_UNREADABLE,
                             result)
                self.assertEqual(result["status"], "store_error")

    def test_B3_5_status_mirrors_are_pinned_to_the_runtime(self):
        self.assertEqual(conforming.RUNTIME_LOCK_FILE_NAME,
                         telegram_state.RUNTIME_LOCK_FILE_NAME)
        self.assertEqual(conforming.WRITER_LOCK_FILE_NAME,
                         atomic.WORKFLOWS_LOCK_FILE_NAME)
        self.assertEqual(conforming.RUNTIME_CONFIG_FILE_NAME,
                         runtime_config.RUNTIME_CONFIG_FILE_NAME)
        self.assertEqual(conforming.RUNTIME_CONFIG_SCHEMA,
                         runtime_config.RUNTIME_CONFIG_SCHEMA)
        self.assertEqual(conforming.RUNTIME_CONFIG_KEYS,
                         runtime_config.RUNTIME_CONFIG_KEYS)
        self.assertEqual(conforming.MAX_FOLLOW_UP_DISPATCHES,
                         dispatch_module.MAX_FOLLOW_UP_DISPATCHES)
        self.assertEqual(conforming.DISPATCH_RECEIPT_MARKER,
                         dispatch_module.DISPATCH_RECEIPT_MARKER)
        self.assertEqual(conforming.UNRESOLVED_TASK_ID,
                         dispatch_module.UNRESOLVED_TASK_ID)
        self.assertEqual(conforming.STOP_REASON_MARKERS, (
            broker_module.VERIFICATION_BLOCK_MARKER,
            broker_module.RECOVERY_BLOCK_MARKER,
            workspace_trust.TRUST_BLOCK_RECEIPT_MARKER))
        self.assertEqual(conforming.BOOTSTRAP_RECEIPT_MARKER,
                         readiness_module.BOOTSTRAP_RECEIPT_MARKER)
        self.assertEqual(conforming.BOOTSTRAP_FAILED,
                         readiness_module.BOOTSTRAP_FAILED)
        # The runtime lock Grok probes IS the one dirun takes in this store.
        self.assertEqual(
            os.path.join(self.store_dir, conforming.RUNTIME_LOCK_FILE_NAME),
            self.runtime_lock_path())


class ClaimDetectorSelfCheckTests(unittest.TestCase):

    def test_the_present_claim_detector_fires_and_respects_negation(self):
        self.assertEqual(affirmed_claims({"x": "the workflow is advancing"}),
                         ["is advancing"])
        self.assertEqual(affirmed_claims({"x": "Runtime: running."}),
                         ["runtime: running"])
        self.assertEqual(affirmed_claims({"last_checked": 1}), [])
        self.assertEqual(affirmed_claims({"x": "the workflow is in progress"}),
                         ["workflow is in progress"])
        self.assertEqual(affirmed_claims({"x": "monitoring is stale"}),
                         ["monitoring", "stale"])
        self.assertEqual(affirmed_claims(
            {"x": "it is not evidence that this workflow is advancing"}), [])


class ProbeBlocked(Exception):
    """A bounded call was still BLOCKED when its real-time alarm expired.
    Deliberately not an OSError or ValueError, so no probe can swallow it
    as an ordinary error."""


FIFO_BOUND_SECONDS = 2.0


def bounded(seconds, call):
    """``call()`` under a real-time alarm (SIGALRM, main thread): a call
    still blocked when it expires raises ``ProbeBlocked``. So a regression
    against code that BLOCKS fails here; it never hangs the suite."""
    def expired(signum, frame):
        raise ProbeBlocked("still blocked after %.1f s" % seconds)
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class LockProbeTotalityTests(ProjectionCase):
    """HELD / UNHELD / ABSENT / UNREADABLE-OR-UNKNOWN, each pinned, and the
    probe is TOTAL: every error path is UNKNOWN with its reason, never
    HELD, and never raises out of a read-only status."""

    def probe(self):
        return conforming.probe_lock(self.runtime_lock_path())

    def make_lock_file(self):
        descriptor = os.open(self.runtime_lock_path(),
                             os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)

    def test_B3_5_absent(self):
        self.assertEqual(self.probe()[0], conforming.LOCK_ABSENT)
        self.assertFalse(os.path.exists(self.runtime_lock_path()))

    def test_B3_5_unheld(self):
        self.make_lock_file()
        self.assertEqual(self.probe()[0], conforming.LOCK_UNHELD)

    def test_B3_5_held(self):
        self.hold_runtime_lock()
        self.assertEqual(self.probe()[0], conforming.LOCK_HELD)

    def test_B3_5_the_probe_never_blocks(self):
        self.hold_runtime_lock()
        started = time.monotonic()
        for _ in range(20):
            self.probe()
        self.assertLess(time.monotonic() - started, 2.0)

    def assert_unknown(self, expected_fragment):
        state, detail = self.probe()
        self.assertEqual(state, conforming.LOCK_UNKNOWN, detail)
        self.assertIn(expected_fragment, detail)
        status = self.status(self.ref)  # never raises
        self.assertEqual(status["runtime_lock"]["state"],
                         conforming.LOCK_UNKNOWN)
        self.assertIn("remedy", status["runtime_lock"])
        return detail

    def test_B3_5_unknown_when_the_open_fails(self):
        self.make_lock_file()
        with mock.patch.object(conforming.os, "open", side_effect=OSError(
                errno.EACCES, "synthetic")):
            self.assert_unknown("could not be opened (PermissionError")

    def test_B3_5_unknown_when_the_path_is_unusable(self):
        state, detail = conforming.probe_lock(self.store_dir + "/\x00lock")
        self.assertEqual(state, conforming.LOCK_UNKNOWN)
        self.assertIn("ValueError", detail)

    def test_B3_5_unknown_when_the_attempt_fails_other_than_would_block(self):
        self.make_lock_file()
        real = conforming.fcntl.flock

        def flock(descriptor, operation):
            if operation == conforming.fcntl.LOCK_EX | conforming.fcntl.LOCK_NB:
                raise OSError(errno.EINVAL, "synthetic")
            return real(descriptor, operation)
        with mock.patch.object(conforming.fcntl, "flock", flock):
            self.assert_unknown("the probe failed (OSError: EINVAL)")

    def test_B3_5_unknown_when_releasing_the_probe_hold_fails(self):
        self.make_lock_file()
        real = conforming.fcntl.flock

        def flock(descriptor, operation):
            if operation == conforming.fcntl.LOCK_UN:
                raise OSError(errno.EIO, "synthetic")
            return real(descriptor, operation)
        with mock.patch.object(conforming.fcntl, "flock", flock):
            self.assert_unknown("could not release it (OSError: EIO)")

    def test_B3_5_unknown_when_closing_the_probe_descriptor_fails(self):
        self.make_lock_file()
        real_close = os.close

        def close(descriptor):
            real_close(descriptor)
            raise OSError(errno.EBADF, "synthetic")
        with mock.patch.object(conforming.os, "close", close):
            state, detail = self.probe()
        self.assertEqual(state, conforming.LOCK_UNKNOWN)
        self.assertIn("could not close its descriptor (OSError: EBADF)",
                      detail)

    def test_B3_5_a_close_failure_after_finding_it_held_is_unknown_not_held(self):
        self.hold_runtime_lock()
        real_close = os.close

        def close(descriptor):
            real_close(descriptor)
            raise OSError(errno.EBADF, "synthetic")
        with mock.patch.object(conforming.os, "close", close):
            state, detail = self.probe()
        self.assertEqual(state, conforming.LOCK_UNKNOWN)
        self.assertIn("after finding: another holder had the lock", detail)

    # -- a FIFO at either lock path: prompt, UNKNOWN, untouched -------------

    def fifo_at(self, path):
        if os.path.lexists(path):
            os.remove(path)
        os.mkfifo(path, 0o600)
        observed = os.lstat(path)
        self.assertTrue(stat.S_ISFIFO(observed.st_mode))
        return observed

    def assert_fifo_untouched(self, path, before):
        after = os.lstat(path)
        self.assertTrue(stat.S_ISFIFO(after.st_mode))
        self.assertEqual((after.st_ino, after.st_mode, after.st_nlink,
                          after.st_size, after.st_mtime_ns),
                         (before.st_ino, before.st_mode, before.st_nlink,
                          before.st_size, before.st_mtime_ns))

    def assert_tripwires_armed(self):
        """The bound comes from the alarm in ``bounded``; NO hermetic
        tripwire is switched off for it. Proven, not assumed: every process
        and network entry point still refuses, and the spawn tripwire is
        still installed."""
        for label, entry in (
            ("subprocess.Popen", lambda: subprocess.Popen(["true"])),
            ("os.kill", lambda: os.kill(os.getpid(), 0)),
            ("socket.socket", lambda: socket.socket()),
        ):
            with self.subTest(tripwire=label):
                with self.assertRaises(HermeticViolation):
                    entry()
        self.assertEqual(dispatch_module.production_spawn.__name__, "tripped")

    def read_promptly(self, path, lock_of):
        """The probe, then status and result, each under the bound. A
        blocked call is a FAILURE with its reason, never a hang."""
        self.assert_tripwires_armed()
        started = time.monotonic()
        try:
            probed = bounded(FIFO_BOUND_SECONDS,
                             lambda: conforming.probe_lock(path))
            status = bounded(FIFO_BOUND_SECONDS,
                             lambda: self.adapter.status(request_ref=self.ref))
            result = bounded(FIFO_BOUND_SECONDS,
                             lambda: self.adapter.result(request_ref=self.ref))
        except ProbeBlocked as exc:
            self.fail("a FIFO at %s BLOCKED the read-only path: %s"
                      % (path, exc))
        self.assertLess(time.monotonic() - started, FIFO_BOUND_SECONDS)
        self.assert_tripwires_armed()
        state, detail = probed
        self.assertEqual(state, conforming.LOCK_UNKNOWN, detail)
        self.assertIn("not a regular file", detail)
        status = self.ok(status)
        self.ok(result)
        self.assertEqual(lock_of(status)["state"], conforming.LOCK_UNKNOWN)
        self.assertIn("not a regular file", lock_of(status)["detail"])
        return status

    def test_FIFO_at_the_runtime_lock_path_is_unknown_promptly_and_untouched(self):
        path = self.runtime_lock_path()
        before = self.fifo_at(path)
        status = self.read_promptly(path, lambda s: s["runtime_lock"])
        self.assertIn("remedy", status["runtime_lock"])
        self.assert_fifo_untouched(path, before)

    def test_FIFO_at_the_writer_lock_path_is_unknown_promptly_and_untouched(self):
        path = os.path.join(self.store_dir, conforming.WRITER_LOCK_FILE_NAME)
        before = self.fifo_at(path)
        self.read_promptly(path, lambda s: s["snapshot"]["writer_lock"])
        self.assert_fifo_untouched(path, before)

    def test_B3_5_the_probe_statement_promises_no_restart(self):
        statement = self.status(self.ref)["runtime_lock"]["probe"]
        self.assertEqual(statement, conforming.PROBE_STATEMENT)
        self.assertIn("would find it held and exit", statement)
        self.assertIn("NOT promised here", statement)
        self.assertIn("a foreground run has no such recovery", statement)
        self.assertNotIn("starts it again", statement)
        self.assertNotIn("service manager starts", statement)


PROMPT_SECONDS = 5.0


class WriterLockHeldTests(ProjectionCase):
    """The Supervisor's test: with the store's writer lock GENUINELY HELD
    by another holder and NO live provider, status and result return
    PROMPTLY and TRUTHFULLY."""

    def hold_writer_lock(self):
        holding, release = threading.Event(), threading.Event()

        def holder():
            with store.exclusive_store_lock(self.store_dir):
                holding.set()
                release.wait(60)
        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(release.set)
        self.assertTrue(holding.wait(PROMPT_SECONDS))
        return release

    def read_concurrently(self):
        results = {}

        def reader():
            results["status"] = self.adapter.status(request_ref=self.ref)
            results["result"] = self.adapter.result(request_ref=self.ref)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        return thread, results

    def test_B2_status_and_result_return_promptly_and_truthfully(self):
        self.dispatched(NOW - 120)
        self.hold_writer_lock()
        started = time.monotonic()
        thread, results = self.read_concurrently()
        thread.join(PROMPT_SECONDS)
        self.assertFalse(thread.is_alive(),
                         "status/result waited behind the writer lock")
        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)
        status = self.ok(results["status"])
        self.assertEqual(status["phase"], record.PHASE_DISPATCHED)
        self.assertEqual(status["snapshot"]["writer_lock"]["state"],
                         conforming.LOCK_HELD)
        statement = status["snapshot"]["writer_lock"]["statement"]
        self.assertEqual(statement, conforming.WRITER_LOCK_STATEMENT)
        self.assertIn("SOME PROCESS held the workflow store's writer lock",
                      statement)
        self.assertIn("does not show which process, or what it was doing",
                      statement)
        self.assertIn("This read did not wait for it", statement)
        for overclaim in ("the Runtime or this transport", "load-modify-save"):
            self.assertNotIn(overclaim, statement)
        result = self.ok(results["result"])
        self.assertIsNone(result["verified_result"])
        self.assertIn("No verified result is recorded", result["note"])

    def test_B2_mutant_a_locked_read_DOES_block_so_the_test_is_sensitive(self):
        # Mutation self-check: with the projection reading under the writer
        # lock (the pre-I3 shape), the same read does NOT return while the
        # lock is held. The test above would fail on that code.
        self.dispatched(NOW - 120)
        release = self.hold_writer_lock()
        with mock.patch.object(conforming.WorkflowRoute, "snapshot_entry",
                               conforming.WorkflowRoute.entry):
            thread, results = self.read_concurrently()
            thread.join(0.5)
            self.assertTrue(thread.is_alive())
            release.set()
            thread.join(PROMPT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.ok(results["status"])

    def test_B2_every_write_path_keeps_the_blocking_writer_lock(self):
        # A WRITE through the route still waits for the writer lock.
        release = self.hold_writer_lock()
        done = threading.Event()

        def writer():
            self.route.void_one(self.workflow_id)
            done.set()
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        self.assertFalse(done.wait(0.5))
        release.set()
        self.assertTrue(done.wait(PROMPT_SECONDS))
        thread.join(PROMPT_SECONDS)


class WorkflowResultTests(ProjectionCase):

    def test_B2_no_verified_result_is_stated_not_inferred(self):
        result = self.result(self.ref)
        self.assertEqual(result["status"], "workflow_result")
        self.assertIsNone(result["verified_result"])
        self.assertIn("only the Broker records one", result["note"])
        self.assertEqual(result["verification_statement"],
                         conforming.VERIFICATION_STATEMENT)

    def completed(self, delivered=False):
        summary = VERIFIED_SUMMARY

        def change(entry):
            for phase in (record.PHASE_WORKSPACE_READY, record.PHASE_PREPARED,
                          record.PHASE_VALIDATED, record.PHASE_DISPATCHED,
                          record.PHASE_VERIFIED, record.PHASE_COMPLETED):
                record.apply_transition(entry, phase)
            entry["verified_result"] = {
                "summary": summary, "recorded_at": NOW - 10,
                "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest()}
        self.edit(self.workflow_id, change)

    def test_B2_a_completed_result_is_read_by_pull_from_the_record(self):
        self.completed()
        entry = self.workflows()[self.workflow_id]
        # No Telegram delivery has happened for this relay workflow.
        self.assertIsNone(entry["result_delivery"])
        before = tree(self.root)
        result = self.result(self.ref)
        self.assertEqual(result["verified_result"], {
            "summary": VERIFIED_SUMMARY,
            "digest_sha256": entry["verified_result"]["digest"],
            "recorded_at": NOW - 10})
        self.assertEqual(result["source"], conforming.RESULT_SOURCE_STATEMENT)
        self.assertIn("does not depend on, wait for or use any Telegram",
                      result["source"])
        self.assertIn("never independent verification",
                      result["verification_statement"])
        self.assertIn("Herdr lifecycle COMPLETE alone never produces it",
                      result["verification_statement"])
        # Reading consumed and wrote nothing, a delivery marker included.
        self.assertEqual(tree(self.root), before)
        self.assertIsNone(self.workflows()[self.workflow_id][
            "result_delivery"])
        status = self.status(self.ref)
        self.assertEqual(status["record"]["verified_result"]["recorded_at"],
                         NOW - 10)
        self.assertIn("Read the verified result with result", status["next"])

    def test_B2_run_result_on_a_workflow_answers_as_result(self):
        self.completed()
        via_run = self.ok(self.adapter.run(request_ref=self.ref,
                                           command="result"))
        self.assertEqual(via_run["verified_result"]["summary"],
                         VERIFIED_SUMMARY)
        self.assertNotIn(conforming.FRESH_REQUEST_REMEDY, flat(via_run))


# ===========================================================================
# Lead ruling — a legacy result is READABLE through Grok Bot: real content
# ===========================================================================

def record_verified_legacy_run(case, surface, missions, workspace, repository):
    """A VERIFIED legacy Mission-Core run recorded DIRECTLY through Mission
    Core's own methods (exactly the ones ``MissionBridge`` dispatch, observe
    and verify call), with the two result artifacts written for real in
    ``workspace``. No bridge, no transport, no spawn: the recovery reads
    durable records and real files only. Returns (request_ref, mission_id)."""
    context = legacy_context()
    proposed = surface.submit(legacy_run_request())
    ref, mission_id = proposed["request_ref"], proposed["mission_id"]
    presented = surface.present(ref)
    surface.attest_approval(
        request_ref=ref, mission_id=presented["mission_id"],
        revision=presented["revision"],
        proposal_digest_sha256=presented["proposal_digest_sha256"],
        approved_action_scope=list(presented["approved_action_scope"]),
        approved_delivery_targets=list(presented["approved_delivery_targets"]),
        expires_at=presented["latest_expires_at"], relayed_reply="approved",
        relay_ref="synthetic-relay")
    authorization_id = missions.get(mission_id)["record"]["authorization_ids"][-1]
    workspace = os.path.realpath(workspace)
    binding = missions.record_workspace_binding(
        mission_id, workspace, os.path.realpath(repository), LEGACY_REPO_URL,
        LEGACY_BASELINE, context)
    missions.record_workspace_prepared(
        mission_id, binding["binding_digest_sha256"], context)
    missions.activate_proof_contract(
        mission_id, missions.mint_state_operation_id(context),
        missions.get_state(mission_id)["sequence"], context)
    missions.record_run_intent(
        mission_id, authorization_id, presented["revision"],
        presented["proposal_digest_sha256"], LEGACY_REPO_URL, workspace,
        LEGACY_BASELINE, "c" * 64, "d" * 64, LEGACY_SURFACE,
        list(presented["approved_action_scope"]),
        list(presented["approved_delivery_targets"]), context)
    missions.record_run_receipt(mission_id, LEGACY_TASK_ID, "start_result",
                                None, context)
    missions.record_observed_running(mission_id, LEGACY_TASK_ID, False, context)
    # The two artifacts the recovery re-reads, written for real.
    state_dir = os.path.join(workspace, ".herd", "state")
    os.makedirs(os.path.join(state_dir, "reviews"))
    review_name = evidence_module.REVIEW_ROUND_FILE_FORMAT % (LEGACY_TASK_ID, 1)
    with open(os.path.join(state_dir, evidence_module.CHECKPOINT_FILE_NAME),
              "w") as handle:
        handle.write(RESULT_TEXT)
    with open(os.path.join(state_dir, "reviews", review_name), "w") as handle:
        handle.write(REVIEW_TEXT)
    result_binding = legacy_result.result_binding(
        mission_id, LEGACY_TASK_ID, evidence_module.CHECKPOINT_FILE_NAME,
        hashlib.sha256(RESULT_TEXT.encode("utf-8")).hexdigest(), review_name,
        hashlib.sha256(REVIEW_TEXT.encode("utf-8")).hexdigest())
    result_digest = digest.json_digest(result_binding)

    def apply(method, *args):
        return method(mission_id, missions.mint_state_operation_id(context),
                      missions.get_state(mission_id)["sequence"],
                      *(args + (context,)))
    produced = apply(missions.record_artifact,
                     legacy_result.RESULT_ARTIFACT_KEY,
                     mission_record.ARTIFACT_ROLE_PRODUCED,
                     bridge_module.LOCATOR_KIND_REPOSITORY_PATH,
                     result_binding["result_locator"],
                     result_binding["result_digest_sha256"], True, [])
    reviewed = apply(missions.record_artifact,
                     legacy_result.REVIEW_ARTIFACT_KEY,
                     mission_record.ARTIFACT_ROLE_VERIFICATION,
                     bridge_module.LOCATOR_KIND_REPOSITORY_PATH,
                     result_binding["review_locator"],
                     result_binding["review_digest_sha256"], True,
                     [produced["artifact_id"]])
    submitted = apply(missions.submit_evidence,
                      mission_record.RUN_RESULT_REQUIREMENT_KEY,
                      mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                      result_digest,
                      [produced["artifact_id"], reviewed["artifact_id"]])
    apply(missions.accept_evidence, submitted["evidence_id"], result_digest)
    tests = apply(missions.submit_evidence, "tests_pass",
                  mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                  TESTS_PASS_DIGEST, [])
    apply(missions.accept_evidence, tests["evidence_id"], TESTS_PASS_DIGEST)
    missions.record_verification(
        mission_id,
        dict((name, True) for name, _ in mission_record.VERIFY_CONJUNCTS),
        "PARTIAL", True, digest.json_digest({"task_id": LEGACY_TASK_ID}),
        submitted["evidence_id"], "COMPLETE", context)
    case.assertEqual(missions.get(mission_id)["record"]["state"], "COMPLETED")
    return ref, mission_id


class DirectlyRecordedLegacyResultTests(GrokCase):
    """The builder the D1 sweep in tests/test_grok_bot_mcp.py uses, proven
    HERE first: state recorded directly through Mission Core (no bridge,
    no transport, no spawn, all tripwired), real artifact files, and the
    REAL content read back through Grok Bot."""

    def test_R1_a_directly_recorded_legacy_result_comes_back_through_grok(self):
        base = fresh_dir(self, "direct")
        workspace = os.path.join(base, "workspace")
        repository = os.path.join(base, "repository")
        os.makedirs(workspace)
        os.makedirs(repository)
        missions = mission_service.MissionService(
            mission_store.MissionStore(self.state), self.clock)
        with Tripwires() as wires:
            ref, mission_id = record_verified_legacy_run(
                self, surface_module.build_bridgeless_surface(
                    self.state, self.clock), missions, workspace, repository)
            result = self.ok(self.adapter.result(request_ref=ref))
        self.assertEqual(wires.trips, [])
        self.assertTrue(result["verified_result"])
        self.assertEqual((result["result_text"], result["review_text"]),
                         (RESULT_TEXT, REVIEW_TEXT))
        self.assertEqual(result["mission_id"], mission_id)
        # The same answer as the one recovery called directly.
        direct = legacy_result.recover_result(missions, mission_id)
        for key, value in direct.items():
            self.assertEqual(result[key], value, key)


class LegacyResultReadabilityTests(GrokCase):

    def setUp(self):
        super(LegacyResultReadabilityTests, self).setUp()
        self.legacy = LegacyRun(self)
        self.ref, self.mission_id = self.legacy.verified()

    def snapshot(self):
        return (tree(self.state), tree(self.legacy.workspace(self.mission_id)),
                len(self.legacy.spawn.calls))

    def grok_result(self):
        with Tripwires() as wires:
            result = self.adapter.result(request_ref=self.ref)
        self.assertEqual(wires.trips, [])
        return result

    def assert_unrecoverable(self, fragment):
        before = self.snapshot()
        expected = self.legacy.bridge.result(self.mission_id)
        result = self.ok(self.grok_result())
        self.assertFalse(result["verified_result"])
        self.assertFalse(result["recoverable"])
        self.assertIn(fragment, result["reason"])
        for key in ("result_text", "review_text", "binding"):
            self.assertNotIn(key, result)
        # The direquest path gives exactly the same answer: one
        # implementation.
        for key, value in expected.items():
            self.assertEqual(result[key], value, key)
        self.assertEqual(self.snapshot(), before)
        return result

    def test_R1_a_real_recoverable_legacy_result_comes_back_through_grok(self):
        before = self.snapshot()
        expected = self.legacy.bridge.result(self.mission_id)
        result = self.ok(self.grok_result())
        self.assertTrue(result["verified_result"])
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["result_text"], RESULT_TEXT)
        self.assertEqual(result["review_text"], REVIEW_TEXT)
        self.assertEqual(result["task_id"], LEGACY_TASK_ID)
        self.assertEqual(result["review_evidence"],
                         "target-produced, not independent verification")
        # Exactly what the local operator surface's result returns.
        for key, value in expected.items():
            self.assertEqual(result[key], value, key)
        self.assertEqual(result["lane"], conforming.LANE_LEGACY)
        self.assertEqual(result["request_ref"], self.ref)
        self.assertEqual(result["mission_id"], self.mission_id)
        # The honest half of the note, and no local-command detour.
        self.assertEqual(result["note"], conforming.LEGACY_RESULT_NOTE)
        self.assertIn("Grok Bot names no path", result["note"])
        self.assertIn("hardened state-artifact reader", result["note"])
        self.assertNotIn("direquest", result["note"])
        # R4: it started, advanced, recorded and consumed nothing.
        self.assertEqual(self.snapshot(), before)

    def test_R1_run_result_is_the_same_read_only_result(self):
        via_run = self.ok(self.adapter.run(request_ref=self.ref,
                                           command="result"))
        self.assertEqual(via_run, self.ok(self.grok_result()))
        self.assertEqual(via_run["result_text"], RESULT_TEXT)

    def test_R2_changed_content_fails_closed_as_unrecoverable(self):
        with open(self.legacy.checkpoint_path(self.mission_id), "w") as f:
            f.write(RESULT_TEXT + "tampered\n")
        self.assert_unrecoverable("content is gone or changed")

    def test_R2_gone_content_fails_closed_as_unrecoverable(self):
        os.remove(self.legacy.review_path(self.mission_id))
        self.assert_unrecoverable("content is gone or changed")

    def test_R2_missing_artifacts_fail_closed_as_unrecoverable(self):
        # The recorded workspace is gone entirely: neither recorded locator
        # has anything to read.
        shutil.rmtree(self.legacy.workspace(self.mission_id))
        os.makedirs(self.legacy.workspace(self.mission_id))
        result = self.assert_unrecoverable("content is gone or changed")
        self.assertIn("absent", result["reason"])

    def test_R2_a_tampered_record_is_refused_before_any_read(self):
        # Moving a recorded artifact's locator is caught by Mission Core's
        # own integrity check first: the read is REFUSED, never verified.
        path = os.path.join(self.state, "missions.json")
        with open(path) as handle:
            document = json.load(handle)
        state = document["mission_state"][self.mission_id]
        for artifact in state["artifacts"]:
            if artifact["key"] == legacy_result.REVIEW_ARTIFACT_KEY:
                artifact["locator"] = ".herd/elsewhere/" + (
                    artifact["locator"].split("/")[-1])
        with open(path, "w") as handle:
            json.dump(document, handle)
        result = self.grok_result()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["problem"], "mission_store_unreadable")
        self.assertIn("mission_state_invocation_mismatch", result["reason"])
        self.assertNotIn("result_text", result)

    def test_R2_an_unrecorded_artifact_is_unrecoverable(self):
        # The recovery's own "not recorded" branch, on the record exactly as
        # Mission Core returns it (the one implementation, called directly).
        real_get_state = self.legacy.missions.get_state

        def get_state(mission_id):
            projection = real_get_state(mission_id)
            for artifact in projection["record"]["artifacts"]:
                if artifact["key"] == legacy_result.REVIEW_ARTIFACT_KEY:
                    artifact["locator"] = ".herd/elsewhere/x.md"
            return projection
        with mock.patch.object(self.legacy.missions, "get_state", get_state):
            result = legacy_result.recover_result(self.legacy.missions,
                                                  self.mission_id)
        self.assertFalse(result["verified_result"])
        self.assertEqual(result["reason"],
                         "run_review_artifact is not recorded")

    def test_R2_identical_bytes_through_an_escaping_chain_are_unrecoverable(self):
        # The hardened reader IS the value: the same bytes reached through
        # a symlinked .herd/state/reviews are refused, not verified.
        reviews = os.path.dirname(self.legacy.review_path(self.mission_id))
        elsewhere = os.path.join(fresh_dir(self, "elsewhere"), "reviews")
        shutil.copytree(reviews, elsewhere)
        shutil.rmtree(reviews)
        os.symlink(elsewhere, reviews)
        self.assert_unrecoverable("content is gone or changed")

    def test_R1_an_unverified_legacy_request_is_still_readable(self):
        ref, _ = self.legacy.approved()
        result = self.ok(self.adapter.result(request_ref=ref))
        self.assertFalse(result["verified_result"])
        self.assertEqual(result["reason"], "no VERIFIED run")
        self.assertEqual(result["lane"], conforming.LANE_LEGACY)

    def test_R3_the_adapter_holds_no_handle_that_can_start_work(self):
        self.assertIsNone(self.adapter._surface._bridge)
        self.assertIs(self.adapter._legacy_result,
                      cli_module.recover_legacy_result)
        self.assertIsNone(cli_module.recover_legacy_result.__closure__)
        for value in vars(self.adapter).values():
            self.assertNotIsInstance(value, bridge_module.MissionBridge)
            self.assertNotIsInstance(value, broker_module.TargetBroker)
        for value in vars(self.adapter._surface).values():
            self.assertNotIsInstance(value, bridge_module.MissionBridge)

    def test_R3_without_the_recovery_a_legacy_result_is_refused_not_faked(self):
        adapter = self.make_adapter(legacy_result=None)
        self.refused(adapter_module.PROBLEM_LEGACY_RESULT_UNAVAILABLE,
                     adapter.result(request_ref=self.ref))


# ===========================================================================
# B-1 — the advice SHOWN for a legacy request
# ===========================================================================

def old_arming_argv(state, ref, mission_id):
    """The legacy ``grokbot.py authorize`` command line exactly as a pre-I3
    presentation displayed it (``arming.mission_arming_argv``, removed)."""
    return ["--state-dir", state, "authorize", "--request-ref", ref,
            "--mission-id", mission_id, "--revision", "1",
            "--proposal-digest", "0" * 64,
            "--action-scope", "engineering_change",
            "--action-scope", "repository_read",
            "--expires-at", str(NOW + 600), "--display-digest", "1" * 64]


class LegacyAdviceTests(GrokCase):

    def setUp(self):
        super(LegacyAdviceTests, self).setUp()
        self.legacy = LegacyRun(self)
        pending = self.legacy.surface.submit(legacy_run_request())
        self.ref = pending["request_ref"]
        self.mission_id = pending["mission_id"]

    def test_B1_present_shows_the_record_and_the_remedy_never_an_arming(self):
        shown = self.ok(self.adapter.present(request_ref=self.ref))
        self.assertEqual(shown["lane"], conforming.LANE_LEGACY)
        self.assertFalse(shown["approvable"])
        self.assertNotIn("approval_binding", shown)
        self.assertNotIn("arming", shown)
        self.assertEqual(shown["approval_remedy"],
                         conforming.LEGACY_REAUTHORIZATION_REMEDY)
        text = shown["display_text"]
        # Still readable exactly as recorded.
        self.assertIn("DODGING INFINITY MISSION PROPOSAL", text)
        self.assertIn("Investigate and resolve the flaky readiness probe",
                      text)
        # The advice shown: no approval and no arming applies.
        self.assertIn("THIS PROPOSAL CAN NO LONGER BE APPROVED THROUGH GROK"
                      " BOT", text)
        self.assertIn(conforming.LEGACY_REAUTHORIZATION_REMEDY, text)
        for invitation in ("reply with a separate message", "arming command"
                           " presented", "To approve exactly this proposal",
                           "prints a one-time approval code"):
            self.assertNotIn(invitation, text)
        # Over MCP the human sees the same display first.
        content = mcp.tool_result("present", shown)["content"]
        self.assertEqual(content[0]["text"], text)

    def test_B1_status_shows_the_remedy(self):
        status = self.ok(self.adapter.status(request_ref=self.ref))
        self.assertEqual(status["lane"], conforming.LANE_LEGACY)
        self.assertFalse(status["approvable"])
        self.assertEqual(status["remedy"],
                         conforming.LEGACY_REAUTHORIZATION_REMEDY)

    def test_B1_the_old_arming_command_refuses_clearly_and_mints_nothing(self):
        before = tree(self.state)
        out = io.StringIO()
        code = cli_module.main(old_arming_argv(self.state, self.ref,
                                               self.mission_id),
                               stdout=out, clock=self.clock)
        result = json.loads(out.getvalue())
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertEqual(result["problem"],
                         conforming.PROBLEM_LEGACY_ARMING_RETIRED)
        self.assertEqual(result["reason"], conforming.LEGACY_ARMING_REMEDY)
        self.assertIn("no approval code was minted", result["reason"])
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, result["reason"])
        self.assertNotIn("approval_code", result)
        self.assertEqual(tree(self.state), before)
        self.assertFalse(os.path.exists(arming.commitments_path(self.state)))

    def test_B1_arm_mission_refuses_before_reading_or_writing(self):
        before = tree(self.state)
        with self.assertRaises(surface_module.LocalRequestRefusal) as caught:
            authorize.arm_mission(None, None, None, self.ref, self.mission_id,
                                  1, "0" * 64, [], [], NOW + 600, "1" * 64)
        self.assertEqual(caught.exception.problem,
                         conforming.PROBLEM_LEGACY_ARMING_RETIRED)
        self.assertEqual(tree(self.state), before)

    def help_of(self, name):
        parser = cli_module._parser()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict) and name in choices:
                help_lines = [a.help for a in action._choices_actions
                              if a.dest == name]
                return help_lines[0], choices[name].format_help()
        self.fail("no subcommand %s" % name)

    def test_B1_the_cli_help_states_the_retirement(self):
        summary, full = self.help_of("authorize")
        for text in (summary, full):
            self.assertIn("RETIRED", text)
        flat_summary = " ".join(summary.split())
        self.assertIn("arms nothing", flat_summary)
        self.assertIn("mints no approval_code", flat_summary)
        self.assertNotIn("consent binding", flat_summary)
        description = " ".join(cli_module.DESCRIPTION.split())
        self.assertIn("Grok Bot runs nothing", description)
        self.assertIn("can no longer be approved or run", description)
        doc = " ".join(cli_module.__doc__.split())
        self.assertIn("``authorize`` (the legacy Mission-Core arming) is"
                      " RETIRED", doc)
        self.assertIn("Grok Bot RUNS NOTHING", doc)
        self.assertNotIn("let ``run`` ``dispatch`` prepare", doc)


class RunAdviceTests(GrokCase):
    """The advice each run command shows, pinned SEPARATELY so the read
    path and the reauthorization path cannot drift back into one."""

    def setUp(self):
        super(RunAdviceTests, self).setUp()
        self.legacy = LegacyRun(self)
        self.ref, self.mission_id = self.legacy.approved()

    def run_command(self, command, arguments=None):
        return self.adapter.run(request_ref=self.ref, command=command,
                                arguments=arguments or RUN_SHAPES[command])

    def test_B2_result_is_answered_by_the_read_path_never_reauthorization(self):
        result = self.ok(self.run_command("result"))
        self.assertEqual(result["status"], "result")
        self.assertEqual(result["lane"], conforming.LANE_LEGACY)
        text = flat(result)
        self.assertNotIn(conforming.FRESH_REQUEST_REMEDY.lower(), text)
        self.assertNotIn("reauthoriz", text)
        self.assertNotIn("never converted", text)

    def test_B2_dispatch_gets_the_reauthorization_remedy(self):
        result = self.refused(conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                              self.run_command("dispatch"))
        self.assertEqual(result["reason"], conforming.LEGACY_RUN_REMEDY)
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, result["reason"])
        self.assertIn("Nothing was run", result["reason"])
        self.assertNotIn("direquest", result["reason"])
        self.assertEqual(result["command"], "dispatch")

    def test_B2_result_and_dispatch_advice_differ(self):
        result = self.ok(self.run_command("result"))
        dispatch = self.refused(conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                                self.run_command("dispatch"))
        self.assertNotIn(dispatch["reason"], json.dumps(result))

    def test_B2_advancing_commands_get_the_reauthorization_remedy(self):
        for command in ("reconcile", "verify", "prove"):
            with self.subTest(command=command):
                result = self.refused(
                    conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                    self.run_command(command))
                self.assertEqual(result["reason"],
                                 conforming.LEGACY_RUN_REMEDY)

    def test_B2_observe_points_at_the_recorded_reads(self):
        result = self.refused(conforming.PROBLEM_LEGACY_RUN_UNAVAILABLE,
                              self.run_command("observe"))
        self.assertEqual(result["reason"], conforming.LEGACY_OBSERVE_ADVICE)
        self.assertNotIn(conforming.FRESH_REQUEST_REMEDY, result["reason"])
        # Following it: both named reads answer.
        self.ok(self.adapter.status(request_ref=self.ref))
        self.ok(self.adapter.result(request_ref=self.ref))

    def test_B2_pause_resume_cancel_point_at_the_operator_surface(self):
        for command, spelled in (("pause", "pause"), ("resume", "resume"),
                                 ("cancel", "cancel-run")):
            with self.subTest(command=command):
                result = self.refused(
                    conforming.PROBLEM_LEGACY_RUN_UNAVAILABLE,
                    self.run_command(command))
                reason = result["reason"]
                self.assertIn("a new request would not affect this one",
                              reason)
                self.assertNotIn(conforming.FRESH_REQUEST_REMEDY, reason)
                # Follow the displayed command: it parses with
                # direquest's OWN parser as this request's own command.
                shown = reason.split("keeps this command: ", 1)[1].split(
                    ". The run's recorded state", 1)[0]
                argv = shlex.split(shown)
                self.assertEqual(argv[0], "python3")
                self.assertEqual(argv[1], os.path.join(REPO, "direquest.py"))
                self.assertTrue(os.path.isfile(argv[1]))
                parsed = local_request_cli._parser().parse_args(argv[2:])
                self.assertEqual(parsed.command, spelled)
                self.assertEqual(parsed.request_ref, self.ref)
                self.assertEqual(parsed.state_dir, self.state)
                self.assertEqual(parsed.control_repo, CONTROL)
        self.assertEqual(self.legacy.spawn.calls, [])

    def test_B2_a_workflow_run_is_refused_toward_status_and_result(self):
        ref = self.requested()["request_ref"]
        for command in ("dispatch", "observe", "cancel"):
            with self.subTest(command=command):
                result = self.refused(conforming.PROBLEM_RUN_NOT_APPLICABLE,
                                      self.adapter.run(request_ref=ref,
                                                       command=command))
                self.assertIn("Read it with status and result",
                              result["reason"])

    def test_B2_malformed_and_unknown_run_calls_stay_bad_requests(self):
        bad = adapter_module.PROBLEM_BAD_REQUEST
        self.refused(bad, self.adapter.run(request_ref=self.ref,
                                           command="deploy"))
        self.refused(bad, self.adapter.run(request_ref=self.ref,
                                           command="prove", arguments={}))
        self.refused(bad, self.adapter.run(request_ref=self.ref,
                                           command="dispatch",
                                           arguments={"workspace_path": 7}))
        self.refused(bad, self.adapter.run(request_ref=self.ref,
                                           command=["dispatch"]))

    def test_B2_the_run_tool_description_invites_no_execution(self):
        description, _properties, _required, _extras = (
            mcp.run_tool_definition())
        self.assertIn("Grok Bot RUNS NOTHING", description)
        self.assertIn("command result answers exactly as the result tool",
                      description)
        for invitation in ("DI prepares the Mission's own isolated workspace",
                           "never ask the human for one",
                           "One run command for the request's own"):
            self.assertNotIn(invitation, description)
        tools = {tool["name"]: tool for tool in mcp.tool_definitions()}
        self.assertIn("result", tools)
        self.assertIn("READ-ONLY", tools["result"]["description"])
        self.assertIn("READ-ONLY", tools["status"]["description"])
        self.assertEqual(tools["result"]["inputSchema"]["required"],
                         ["request_ref"])


# ===========================================================================
# B-1a — every displayed remedy, FOLLOWED literally
# ===========================================================================

class RemedyRoundTripTests(GrokCase):

    CONV = "grok-conversation-i3-roundtrip"

    def approve_fresh(self, out, original_ref):
        self.assertEqual(out["status"], "authorization_proposed", out)
        self.assertFalse(out["duplicate"])
        self.assertNotEqual(out["request_ref"], original_ref)
        shown = self.presented(out["request_ref"])
        args = dict(shown["approval_binding"], relayed_reply="approved",
                    relay_ref="grok-message-2",
                    approval_code=self.arm(shown))
        self.assertEqual(self.ok(self.adapter.approve(**args))["phase"],
                         "AUTHORIZED")

    def follow(self, advice, text, original_ref):
        """Perform EXACTLY what FRESH_REQUEST_REMEDY displays."""
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, advice)
        self.approve_fresh(self.ok(self.adapter.request(
            text=text + "\nPlease proceed with this one.",
            conversation_ref=self.CONV)), original_ref)
        self.approve_fresh(self.ok(self.adapter.request(
            text=text, conversation_ref=self.CONV + "-new")), original_ref)
        same = self.ok(self.adapter.request(text=text,
                                            conversation_ref=self.CONV))
        self.assertTrue(same["duplicate"])
        self.assertEqual(same["request_ref"], original_ref)
        return same

    def seed_legacy(self):
        key = digest.json_digest({"text": TEXT, "conversation_ref": self.CONV})
        self.index.begin(key, self.clock())
        legacy = LegacyRun(self).surface.submit(legacy_run_request())
        self.index.record_proposed(key, legacy["request_ref"], self.clock())
        return legacy["request_ref"]

    def test_B1a_the_legacy_present_remedy_reaches_a_fresh_authorization(self):
        ref = self.seed_legacy()
        shown = self.ok(self.adapter.present(request_ref=ref))
        same = self.follow(shown["display_text"], TEXT, ref)
        # The identical request's reply shows the remedy, not an approval.
        self.assertEqual(same["remedy"],
                         conforming.LEGACY_REAUTHORIZATION_REMEDY)

    def test_B1a_the_legacy_run_remedy_reaches_a_fresh_authorization(self):
        ref = self.seed_legacy()
        result = self.refused(conforming.PROBLEM_LEGACY_REAUTHORIZATION,
                              self.adapter.run(request_ref=ref,
                                               command="dispatch"))
        self.follow(result["reason"], TEXT, ref)

    def test_B1a_the_retired_arming_remedy_reaches_a_fresh_authorization(self):
        ref = self.seed_legacy()
        out = io.StringIO()
        cli_module.main(old_arming_argv(self.state, ref, "mn-x"), stdout=out,
                        clock=self.clock)
        self.follow(json.loads(out.getvalue())["reason"], TEXT, ref)

    def test_B1a_D4_the_expiry_void_HOLDS_the_index_entry_and_says_so(self):
        # INSPECTED, not assumed: voiding an expired relay workflow leaves
        # its index entry PROPOSED, so an identical request is answered
        # with the voided record itself. The reply must therefore carry the
        # remedy, and following it must reach a fresh authorization.
        out = self.requested(conversation_ref=self.CONV)
        ref = out["request_ref"]
        held = self.index.workflow_request(ref)
        self.clock.now = self.workflows()[held["workflow_id"]][
            "approval"]["expires_at"]
        expired = self.ok(self.adapter.status(request_ref=ref))
        self.assertIn("can no longer be approved", expired["next"])
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, expired["next"])
        # Any new request saves the store, and that save voids it.
        self.ok(self.adapter.request(text="An unrelated request for"
                                     " octocat/widget.",
                                     conversation_ref="other"))
        voided = self.workflows()[held["workflow_id"]]
        self.assertEqual(voided["phase"], record.PHASE_BLOCKED)
        self.assertTrue(voided["approval"]["superseded"])
        key = digest.json_digest({"text": TEXT, "conversation_ref": self.CONV})
        state, still = self.index.begin(key, self.clock())
        self.assertEqual((state, still), (index_module.STATE_PROPOSED, ref))
        again = self.ok(self.adapter.request(text=TEXT,
                                             conversation_ref=self.CONV))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["request_ref"], ref)
        self.assertEqual(again["phase"], record.PHASE_BLOCKED)
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, again["next"])
        self.follow(again["next"], TEXT, ref)

    def test_B1a_the_superseded_approve_refusal_carries_the_remedy(self):
        out = self.requested(conversation_ref=self.CONV)
        shown = self.presented(out["request_ref"])
        args = dict(shown["approval_binding"], relayed_reply="approved",
                    relay_ref="m", approval_code=self.arm(shown))
        self.route.void_one(shown["approval_binding"]["workflow_id"])
        result = self.refused(conforming.PROBLEM_SUPERSEDED,
                              self.adapter.approve(**args))
        self.follow(result["reason"], TEXT, out["request_ref"])

    def test_B1a_a_not_awaiting_present_carries_the_right_advice(self):
        ref, _ = self.approved()
        approved = self.refused(conforming.PROBLEM_NOT_AWAITING,
                                self.adapter.present(request_ref=ref))
        self.assertIn("read it with status and result", approved["reason"])
        self.assertNotIn(conforming.FRESH_REQUEST_REMEDY, approved["reason"])
        out = self.requested(text=TEXT + " (second)")
        held = self.index.workflow_request(out["request_ref"])
        self.route.void_one(held["workflow_id"])
        voided = self.refused(conforming.PROBLEM_NOT_AWAITING,
                              self.adapter.present(
                                  request_ref=out["request_ref"]))
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, voided["reason"])


# ===========================================================================
# B-3 — the PRODUCTION Runtime wiring, effect seams injected
# ===========================================================================

def real_shaped_observation(status="COMPLETE", task_id=TARGET_TASK_ID,
                            review_decision="APPROVE", completeness="COMPLETE"):
    """A herdr.observe-SHAPED projection (copied from test_target_runtime's
    real_shaped_observation, whose shape is pinned against the real
    projection there)."""
    return {
        "completeness": completeness,
        "diagnostics": [],
        "task": {"state": "available", "status": status, "id": task_id},
        "reviews": {
            "state": "available", "task_id": task_id, "rounds": 1,
            "total_files": 1, "truncated": False,
            "listed": [{"file": "%s-round-01.md" % task_id, "round": 1,
                        "decision": review_decision, "size": 120,
                        "mtime": 1_000_040}],
        },
        "artifacts": {
            "state": "available",
            "listed": [{"name": "task-checkpoint.md", "present": True,
                        "size": 64, "mtime": 1_000_050, "age_seconds": 10,
                        "freshness": "fresh"}],
        },
        "children": {"state": "empty", "parent_task_id": task_id,
                     "count": 0, "truncated": False, "listed": []},
    }


def real_shaped_spawn_result(repo, task_id, parent_repo, recorded=None):
    """The exact outer/inner key shape spawn_child() returns (copied).
    ``recorded`` is the child record's task id (normally the same)."""
    return {
        "repo": repo, "initialization": None,
        "runtime": {"workspace_id": "w", "agents": {}},
        "task": {"id": task_id, "status": "ACTIVE"},
        "policy": {}, "parent_repo": parent_repo,
        "child_record": {
            "requested_at": 1, "parent_repo": parent_repo,
            "parent_task_id": None, "dependency": False, "repo": repo,
            "task_id": task_id if recorded is None else recorded,
            "task_status": "ACTIVE", "workspace_id": "w", "agents": {},
        },
    }


class PureTransport(object):
    """The git transport's five verbs and two read verbs, in pure Python:
    a "clone" copies a local fixture directory; nothing runs git and
    nothing reaches a remote."""

    def __init__(self, fixtures):
        self.fixtures = dict(fixtures)
        self.remotes = {}
        self.calls = []

    def clone(self, url, path):
        self.calls.append(("clone", url))
        if url not in self.fixtures:
            raise git_transport_error("unknown remote %r" % url)
        shutil.copytree(self.fixtures[url], path, symlinks=True)
        self.remotes[os.path.realpath(path)] = url

    def remote_url(self, path):
        self.calls.append(("remote_url",))
        try:
            return self.remotes[os.path.realpath(path)]
        except KeyError:
            raise git_transport_error("no remote for %r" % path)

    def checkout_detached(self, path, commit_sha):
        self.calls.append(("checkout", commit_sha))

    def head_commit(self, path):
        return BASELINE_SHA

    def status_porcelain(self, path):
        return ""

    def status_porcelain_readonly(self, path):
        return {"status": "captured", "text": "", "total_bytes": 0}

    def diff_head(self, path):
        return {"status": "captured", "retained_bytes": 0,
                "retained_text": "", "retained_text_lossy": False,
                "truncated": False, "total_bytes": 0,
                "digest": hashlib.sha256(b"").hexdigest()}


def git_transport_error(message):
    return importlib.import_module(
        "target_runtime.git_transport").GitTransportError(message)


ROLE_HEADER = re.compile(
    r"^Restricted role turn \(protocol DI-REMOTE-2\)\. Role: ([a-z_]+)\.$")


class CodexTurns(object):
    """Stands in for ``role_turn._default_runner`` ONLY: the production role
    turn still builds and verifies the restrictive argv, renders the prompt
    and parses the answer. Each call records the exact argv, cwd and owner
    scope, reads the role from the prompt it was shown, and answers with a
    scripted role-outcome envelope. It starts nothing."""

    def __init__(self, events):
        self.events = events
        self.calls = []
        self.outcomes = {"prepare": "request_prepare",
                         "handoff_validation": "request_dispatch",
                         "verification": "verified_result",
                         "status_recovery": "request_recovery"}
        self.details = {"verification": VERIFIED_SUMMARY}
        self.queued = {}

    def __call__(self, argv, prompt_bytes, cwd, owner_scope=None):
        prompt = prompt_bytes.decode("utf-8")
        match = ROLE_HEADER.match(prompt.split("\n", 1)[0])
        role = match.group(1) if match else None
        pid = 8100 + len(self.calls)
        self.calls.append({"role": role, "argv": list(argv), "cwd": cwd,
                           "owner_scope": owner_scope, "pid": pid,
                           "prompt": prompt})
        self.events.append("codex:%s" % role)
        queue = self.queued.get(role)
        outcome = queue.pop(0) if queue else self.outcomes[role]
        body = json.dumps({"role": role, "outcome": outcome,
                           "detail": self.details.get(role)})
        message = envelope("role_outcome", body)
        return (0, codex_stdout(message, "codex-%s-%d" % (role, pid)), b"",
                pid)


class RuntimeDriveCase(GrokCase):

    def setUp(self):
        super(RuntimeDriveCase, self).setUp()
        home = mock.patch.dict(os.environ, {"HOME": self.root})
        home.start()
        self.addCleanup(home.stop)
        self.claude_config = os.path.join(self.root, ".claude.json")
        with open(self.claude_config, "w") as handle:
            json.dump({"hasCompletedOnboarding": True, "projects": {
                "/Users/someone/other-repo": {
                    "hasTrustDialogAccepted": True}}}, handle)
        fixture = os.path.join(self.root, "target-fixture")
        files = {
            "README.md": "target readme\n",
            "AGENTS.md": "target instructions (untrusted)\n",
            os.path.join(".herd", "state", "task-checkpoint.md"): (
                "# Task Checkpoint\n\n## Outcome\nCOMPLETE.\n\n"
                "## Verification\n- suite green\n\n"
                "## Mutation evidence\n- 5/5 mutants KILLED\n"),
            os.path.join(".herd", "state", "reviews",
                         "%s-round-01.md" % TARGET_TASK_ID): (
                "# Reviewer round 1\n\nReviewer: `reviewer1` / `sess-fixture`"
                "\n\nProtocol token: `APPROVE`\n\n## Transcript\n\n"
                "HERD_DECISION: APPROVE\n"),
        }
        for name in preserve_module.REQUIRED_ARTIFACTS:
            files[os.path.join(".herd", "state", name)] = "target %s\n" % name
        for relative, text in files.items():
            path = os.path.join(fixture, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(text)
        self.workspaces = os.path.join(self.root, "workspaces")
        self.events = []
        self.transport = PureTransport({CANONICAL_URL: fixture})
        self.codex = CodexTurns(self.events)
        runner = mock.patch.object(role_turn, "_default_runner", self.codex)
        runner.start()
        self.addCleanup(runner.stop)
        self.spawned = []
        self.spawn_recorded_id = None
        self.observation = real_shaped_observation()
        self.records = []
        self.broker = broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=CONTROL,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=runtime_cli.production_role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.spawn, clock=self.clock,
            observer_fn=lambda repo: json.loads(json.dumps(self.observation)),
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=self.readiness)

    # -- the injected effect seams --------------------------------------

    def spawn(self, parent_repo, request_):
        self.spawned.append((parent_repo, dict(request_)))
        self.events.append("spawn")
        # What herd persists when it starts a child: one child record in
        # the control repository's spawn records, with the true task id.
        self.records.append({
            "requested_at": 1, "parent_repo": parent_repo,
            "parent_task_id": None, "dependency": False,
            "repo": os.path.realpath(request_["target_repo"]),
            "task_id": TARGET_TASK_ID, "task_status": "ACTIVE",
            "workspace_id": "w", "agents": {}})
        return real_shaped_spawn_result(request_["target_repo"],
                                        TARGET_TASK_ID, parent_repo,
                                        recorded=self.spawn_recorded_id)

    def spawn_records(self, repo):
        listed = [dict(r) for r in self.records]
        return {"state": "available" if listed else "empty",
                "count": len(listed), "truncated": False, "listed": listed,
                "detail": None}

    def readiness(self, lease_path):
        return {logical: {"name": "target-" + logical,
                          "interactive_ready": True, "agent_status": "idle",
                          "revision": 1, "state_change_seq": 2}
                for logical in readiness_module.REQUIRED_LOGICAL_ROLES}

    # -- driving ----------------------------------------------------------

    def drive(self, passes=6):
        """Production Runtime passes over the shared store, with EVERY Grok
        Bot entry point tripwired: progression needs no Grok call."""
        trips = []

        def tripwire(name):
            def tripped(*args, **kwargs):
                trips.append(name)
                raise HermeticViolation("a Runtime pass reached Grok %s"
                                        % name)
            return tripped
        patchers = [mock.patch.object(adapter_module.GrokBotAdapter, "call",
                                      tripwire("adapter.call"))]
        for name in ("plan", "record", "status", "result", "consume",
                     "withdraw", "entry", "snapshot_entry",
                     "check_approvable"):
            patchers.append(mock.patch.object(conforming.WorkflowRoute, name,
                                              tripwire(name)))
        for patcher in patchers:
            patcher.start()
        processed = []
        try:
            for _ in range(passes):
                processed.append(runtime_module.process_once(self.broker))
                if self.workflows()[self.workflow_id]["phase"] in (
                    record.PHASE_COMPLETED, record.PHASE_BLOCKED,
                    record.PHASE_NEEDS_REAUTHORIZATION,
                ):
                    break
        finally:
            for patcher in patchers:
                patcher.stop()
        self.assertEqual(trips, [])
        return processed

    def labels(self, processed):
        return [label for one in processed
                for label, _ in one.get(self.workflow_id, [])]

    def entry(self):
        # From a FRESH store instance, read from disk: durable.
        return store.WorkflowStore(self.store_dir).load()["workflows"][
            self.workflow_id]


class RuntimeDriveTests(RuntimeDriveCase):

    def setUp(self):
        super(RuntimeDriveTests, self).setUp()
        self.ref, self.workflow_id = self.approved()
        self.assertEqual(self.entry()["phase"], record.PHASE_AUTHORIZED)
        self.codex_before = len(self.codex.calls)

    def test_B3_1_unattended_progression_to_completed_with_no_grok_call(self):
        processed = self.drive()
        self.assertEqual(self.labels(processed)[:7], [
            broker_module.ACTION_MATERIALIZE, broker_module.ACTION_PREPARE,
            broker_module.ACTION_VALIDATE_HANDOFF,
            broker_module.ACTION_DISPATCH, broker_module.ACTION_VERIFY,
            broker_module.ACTION_COMPLETE, broker_module.ACTION_RELEASE])
        for one in processed:
            for label, outcome in one.get(self.workflow_id, []):
                self.assertTrue(outcome.ok, (label, outcome.problem,
                                             outcome.detail))
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_COMPLETED)
        self.assertEqual(entry["verified_result"]["summary"], VERIFIED_SUMMARY)
        self.assertEqual(len(self.spawned), 1)
        # Grok Bot then READS it, by pull.
        result = self.result(self.ref)
        self.assertEqual(result["verified_result"]["summary"],
                         VERIFIED_SUMMARY)
        status = self.status(self.ref)
        self.assertEqual(status["phase"], record.PHASE_COMPLETED)
        self.assertEqual(status["record"]["dispatches"]["recorded"], 1)

    def test_B3_2_fresh_restrictive_codex_turns_before_dispatch_and_at_acceptance(self):
        self.drive()
        calls = self.codex.calls[self.codex_before:]
        self.assertEqual([c["role"] for c in calls],
                         ["prepare", "handoff_validation", "verification"])
        # Before dispatch AND at acceptance, in that order.
        self.assertEqual(self.events, ["codex:prepare",
                                       "codex:handoff_validation", "spawn",
                                       "codex:verification"])
        pids = set()
        for call in calls:
            with self.subTest(role=call["role"]):
                self.assertEqual(call["argv"], restrictive_argv(CONTROL))
                self.assertNotIn("resume", call["argv"])
                self.assertEqual(call["cwd"], CONTROL)
                self.assertIsNotNone(call["owner_scope"])
                self.assertIn("carries NO delivery authority", call["prompt"])
                pids.add(call["pid"])
        self.assertEqual(len(pids), 3)  # three distinct processes
        recorded = {(t["role"], t["process_id"])
                    for t in self.entry()["codex_turns"]}
        for call in calls:
            self.assertIn((call["role"], call["pid"]), recorded)

    def test_B3_2_the_verification_prompt_carries_the_canonical_reviewer_artifact(self):
        # A verification call OCCURRING is not evidence the Operator was
        # SHOWN the review. Pinned on the REAL prompt the production role
        # turn rendered: it carries the target's canonical Reviewer
        # artifact's CONTENT, as that file stands in the leased target
        # workspace at verification (rewritten here between passes, so a
        # stale or fixture copy cannot satisfy it).
        rendering = importlib.import_module("workflow_authority.rendering")
        self.observation = real_shaped_observation(status="ACTIVE")
        self.drive(passes=1)
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_DISPATCHED)
        review = os.path.join(
            entry["workspace_lease"]["path_realpath"], ".herd", "state",
            "reviews", "%s-round-01.md" % TARGET_TASK_ID)
        at_verification = (
            "# Reviewer round 1\n\nReviewer: `reviewer1` / `sess`\n\n"
            "Protocol token: `APPROVE`\n\n## Transcript\n\n"
            "Finding F-2 (review content 9b44c1): the readiness retry is"
            " bounded and the regression test fails without the fix.\n"
            "HERD_DECISION: APPROVE\n")
        with open(review, "w") as handle:
            handle.write(at_verification)
        self.observation = real_shaped_observation(status="COMPLETE")
        self.drive()
        self.assertEqual(self.entry()["phase"], record.PHASE_COMPLETED)
        prompts = [c["prompt"] for c in self.codex.calls
                   if c["role"] == "verification"]
        self.assertEqual(len(prompts), 1)
        shown = "\n".join(["review_file.text (quoted):"]
                          + rendering.quoted_intent_lines(at_verification))
        self.assertIn(shown, prompts[0])
        # The content itself, not merely a header, path, digest or word.
        self.assertIn("Finding F-2 (review content 9b44c1): the readiness"
                      " retry is bounded and the regression test fails"
                      " without the fix.", prompts[0])
        # The fixture's original review text is NOT what it was shown.
        self.assertNotIn("Reviewer: `reviewer1` / `sess-fixture`", prompts[0])
        # It is the ACCEPTANCE turn that is shown it.
        for call in self.codex.calls[self.codex_before:]:
            if call["role"] != "verification":
                self.assertNotIn("review content 9b44c1", call["prompt"])

    def test_B3_2_a_turn_proposing_an_outcome_its_role_may_not_emit_advances_nothing(self):
        self.codex.queued["prepare"] = ["verified_result"]
        processed = self.drive(passes=1)
        outcomes = processed[0][self.workflow_id]
        self.assertEqual(len(outcomes), 1)
        label, outcome = outcomes[0]
        self.assertEqual(label, "request:prepare")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem, "runtime_request_turn_incomplete")
        self.assertIn("outcome_not_allowed_for_role", outcome.detail)
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_AUTHORIZED)
        self.assertEqual(self.spawned, [])
        self.assertIsNone(entry["verified_result"])

    def test_B3_2_the_runtime_refuses_a_verified_result_the_evidence_does_not_support(self):
        # The verification turn proposes verified_result; the target's own
        # canonical review says REJECT. The Broker's conjunction decides:
        # nothing is VERIFIED, and the stop's reason is recorded.
        self.observation = real_shaped_observation(review_decision="REJECT")
        processed = self.drive()
        verify = [outcome for label, outcome in processed[0][self.workflow_id]
                  if label == broker_module.ACTION_VERIFY]
        self.assertEqual([(o.outcome, o.problem) for o in verify],
                         [("verification_blocked",
                           "broker_verification_review_not_approve")])
        # The turn DID propose verified_result; the Broker refused it.
        self.assertEqual(self.events[-1], "codex:verification")
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_BLOCKED)
        self.assertIsNone(entry["verified_result"])
        status = self.status(self.ref)
        self.assertIsNone(self.result(self.ref)["verified_result"])
        stop = status["record"]["stop"]
        self.assertTrue(stop["reason_recorded"])
        self.assertEqual(stop["reason"]["marker"], "verification blocked")
        self.assertEqual(stop["reason"]["problem_code"],
                         "broker_verification_review_not_approve")
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, status["next"])

    def test_B3_3_the_follow_up_bound_ends_durably_in_needs_reauthorization(self):
        # Herdr reports COMPLETE every time and the verification turn asks
        # for a correction every time: COMPLETE alone never produces
        # VERIFIED, and the third correction exceeds the bound.
        self.codex.outcomes["verification"] = "request_follow_up"
        self.codex.details["verification"] = "the regression test is missing"
        self.drive(passes=10)
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_NEEDS_REAUTHORIZATION)
        self.assertIsNone(entry["verified_result"])
        self.assertEqual(dispatch_module.dispatch_count(entry),
                         1 + dispatch_module.MAX_FOLLOW_UP_DISPATCHES)
        self.assertEqual(len(self.spawned),
                         1 + dispatch_module.MAX_FOLLOW_UP_DISPATCHES)
        self.assertEqual(conforming.dispatches_recorded(entry),
                         dispatch_module.dispatch_count(entry))
        # Grok shows it truthfully, with the remedy that works.
        status = self.status(self.ref)
        self.assertEqual(status["record"]["dispatches"], {
            "recorded": 3, "follow_ups_recorded": 2, "follow_up_bound": 2,
            "follow_up_bound_reached": True})
        self.assertFalse(status["record"]["stop"]["reason_recorded"])
        self.assertIn(conforming.FRESH_REQUEST_REMEDY, status["next"])
        # And it stays there: a later pass dispatches nothing more.
        self.drive(passes=2)
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual(self.entry()["phase"],
                         record.PHASE_NEEDS_REAUTHORIZATION)

    def test_B3_4_the_dispatch_carries_the_fixed_roster(self):
        self.drive()
        self.assertEqual(len(self.spawned), 1)
        parent_repo, request_ = self.spawned[0]
        self.assertEqual(parent_repo, CONTROL)
        self.assertEqual(request_["preset"],
                         dispatch_module.DI_TARGET_EXECUTION_PRESET)
        self.assertEqual(request_["preset"], "max-quality")
        self.assertEqual(request_["alias"], "di-remote-2-" + self.workflow_id)
        roles = herdr_config.PRESETS[request_["preset"]]["roles"]
        self.assertEqual({role: spec["kind"] for role, spec in roles.items()},
                         {"supervisor": "codex", "lead": "claude",
                          "executor": "claude", "reviewer": "codex"})
        self.assertEqual(
            os.path.realpath(request_["target_repo"]),
            os.path.realpath(self.entry()["workspace_lease"][
                "path_realpath"]))


class RuntimeRecoveryDriveTests(RuntimeDriveCase):

    def setUp(self):
        super(RuntimeRecoveryDriveTests, self).setUp()
        self.ref, self.workflow_id = self.approved()
        self.codex_before = len(self.codex.calls)

    def test_B3_2_a_fresh_status_recovery_turn_then_completion(self):
        # The spawn's two task identities disagree, so the dispatch leaves
        # the target identity unresolved; recovery binds it from evidence.
        self.spawn_recorded_id = "a-different-id"
        unresolved = []
        real_role_turn = runtime_cli.production_role_turn

        def observing_role_turn(role, entry, now, **kwargs):
            # Grok's read of the record at the moment the recovery turn
            # runs: the identity is recorded as UNRESOLVED.
            if role == "status_recovery":
                unresolved.append(self.entry()["target_engine"]["task_id"])
            return real_role_turn(role, entry, now, **kwargs)
        self.broker._role_turn = observing_role_turn
        processed = self.drive(passes=4)
        self.assertEqual(unresolved, [dispatch_module.UNRESOLVED_TASK_ID])
        labels = self.labels(processed)
        self.assertIn("request:status_recovery", labels)
        self.assertIn(broker_module.ACTION_RECONCILE, labels)
        roles = [c["role"] for c in self.codex.calls[self.codex_before:]]
        self.assertIn("status_recovery", roles)
        self.assertLess(self.events.index("spawn"),
                        self.events.index("codex:status_recovery"))
        recovery = [c for c in self.codex.calls
                    if c["role"] == "status_recovery"][0]
        self.assertEqual(recovery["argv"], restrictive_argv(CONTROL))
        self.assertEqual(self.entry()["phase"], record.PHASE_COMPLETED)
        self.assertEqual(len(self.spawned), 1)  # recovery never re-spawns



# ===========================================================================
# The Runtime's own configuration seam (``dirun --runtime-config``): REAL
# config files with real permissions, the REAL loader, the REAL CLI
# composition; effect seams injected only AFTER validation. A private
# fixture HOME throughout: no real trust file and no live HOME is touched.
# ===========================================================================

class ExecutionRecorder(object):
    """Stands in for the Runtime calls ``cli.main`` makes AFTER the
    configuration was loaded and the lock taken (its ``execution`` seam)."""

    def __init__(self):
        self.calls = []

    def recover_inherited_processes(self):
        self.calls.append("recover")
        return [], []

    def readiness_attention(self):
        self.calls.append("readiness")
        return [], 0

    def process_once(self):
        self.calls.append("process_once")
        return {}


def write_runtime_config(directory, document, name=None, mode=0o600,
                         raw=None):
    path = os.path.join(directory, name or runtime_config.RUNTIME_CONFIG_FILE_NAME)
    with open(path, "w") as handle:
        handle.write(raw if raw is not None else json.dumps(document))
    os.chmod(path, mode)
    return path


def make_control(parent, name="control", documents=("AGENTS.md",
                                                    "OPERATOR_PROTOCOL.md")):
    control = os.path.join(parent, name)
    os.makedirs(control)
    for document in documents:
        with open(os.path.join(control, document), "w") as handle:
            handle.write("synthetic %s\n" % document)
    return os.path.realpath(control)


class RuntimeConfigSeamTests(unittest.TestCase):

    def setUp(self):
        self.root = os.path.realpath(fresh_dir(self, "seam"))
        home = mock.patch.dict(os.environ, {"HOME": self.root})
        home.start()
        self.addCleanup(home.stop)
        self.store = os.path.join(self.root, "grok-workflows")
        os.mkdir(self.store, 0o700)
        os.chmod(self.store, 0o700)
        self.control = make_control(self.root)

    def document(self, **changes):
        document = {"schema": runtime_config.RUNTIME_CONFIG_SCHEMA,
                    "control_repository": self.control,
                    "workflow_store": self.store}
        document.update(changes)
        return document

    def config(self, **changes):
        return write_runtime_config(self.store, self.document(**changes))

    def main(self, argv):
        execution = ExecutionRecorder()
        stream = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(stream):
            code = runtime_cli.main(argv, execution=execution)
        return code, stream.getvalue(), execution

    def refused(self, argv, problem):
        """Refused by the REAL CLI before the lock or any Runtime call."""
        code, stderr, execution = self.main(argv)
        self.assertEqual(code, runtime_cli.EXIT_CONFIG, stderr)
        self.assertIn("dirun: config: %s: " % problem, stderr)
        self.assertEqual(execution.calls, [])
        self.assertFalse(os.path.exists(os.path.join(
            self.store, conforming.RUNTIME_LOCK_FILE_NAME)))
        namespace = runtime_cli._build_parser().parse_args(argv)
        with self.assertRaises(runtime_config.RuntimeConfigError) as caught:
            runtime_cli._build_broker(namespace)
        self.assertEqual(caught.exception.problem, problem)
        return caught.exception

    # -- accepted, and composed --------------------------------------------

    def test_SEAM_a_grok_only_configuration_is_accepted_by_the_real_cli(self):
        path = self.config()
        code, stderr, execution = self.main(
            ["--runtime-config", path, "once"])
        self.assertEqual(code, runtime_cli.EXIT_OK, stderr)
        self.assertEqual(execution.calls,
                         ["recover", "readiness", "process_once"])
        self.assertIn("processed 0 workflow(s) (exact)", stderr)

    def test_SEAM_the_composition_binds_this_store_and_the_consumed_trust(self):
        namespace = runtime_cli._build_parser().parse_args(
            ["--runtime-config", self.config(), "run"])
        broker, state = runtime_cli._build_broker(namespace)
        self.assertEqual(state, self.store)
        self.assertEqual(broker.store.directory, self.store)
        self.assertEqual(broker.control_realpath, self.control)
        self.assertEqual(broker.workspaces_root,
                         os.path.join(self.store, "workspaces"))
        # Trust is written where the started Herdr reads it: the
        # user-global file under the (private fixture) HOME, never a
        # store-local file.
        self.assertEqual(broker.worker.config_path,
                         workspace_trust.default_config_path())
        self.assertEqual(broker.worker.config_path,
                         os.path.join(self.root, ".claude.json"))
        # The production composition, as composed (nothing injected yet).
        self.assertIs(broker._role_turn, runtime_cli.production_role_turn)
        self.assertIs(broker._spawn, dispatch_module.production_spawn)
        self.assertIsInstance(broker.transport,
                              importlib.import_module(
                                  "target_runtime.git_transport").GitTransport)
        self.assertTrue(broker.worker.observes_live_workspaces)
        self.assertTrue(broker.worker.closes_workspaces)

    # -- refused, each with its own code ------------------------------------

    def test_SEAM_a_missing_configuration_is_refused(self):
        self.refused(["--runtime-config",
                      os.path.join(self.store, "absent.json"), "once"],
                     runtime_config.PROBLEM_MISSING)

    def test_SEAM_open_file_permissions_are_refused(self):
        path = self.config()
        os.chmod(path, 0o644)
        self.refused(["--runtime-config", path, "once"],
                     runtime_config.PROBLEM_PERMISSIONS)

    def test_SEAM_open_directory_permissions_are_refused(self):
        path = self.config()
        os.chmod(self.store, 0o755)
        self.refused(["--runtime-config", path, "once"],
                     runtime_config.PROBLEM_PERMISSIONS)

    def test_SEAM_a_malformed_configuration_is_refused(self):
        cases = {
            "not json": None,
            "a list": [],
            "no schema": dict((k, v) for k, v in self.document().items()
                              if k != "schema"),
            "another schema": self.document(schema="v0"),
            "a missing key": dict((k, v) for k, v in self.document().items()
                                  if k != "workflow_store"),
            "an unrecognized key": self.document(extra=1),
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                path = write_runtime_config(
                    self.store, document,
                    raw="{not json" if label == "not json" else None)
                self.refused(["--runtime-config", path, "once"],
                             runtime_config.PROBLEM_MALFORMED)

    def test_SEAM_a_bad_control_path_is_refused(self):
        alias = os.path.join(self.root, "control-alias")
        os.symlink(self.control, alias)
        no_policy = make_control(self.root, "not-control",
                                 documents=("AGENTS.md",))
        not_a_dir = os.path.join(self.root, "a-file")
        with open(not_a_dir, "w") as handle:
            handle.write("x")
        for label, value in (("relative", "control"),
                             ("not canonical", alias),
                             ("no policy documents", no_policy),
                             ("not a directory", not_a_dir)):
            with self.subTest(case=label):
                path = self.config(control_repository=value)
                self.refused(["--runtime-config", path, "once"],
                             runtime_config.PROBLEM_PATH)

    def test_SEAM_a_mismatched_store_is_refused(self):
        other = os.path.join(self.root, "another-store")
        os.mkdir(other, 0o700)
        path = self.config(workflow_store=os.path.realpath(other))
        self.refused(["--runtime-config", path, "once"],
                     runtime_config.PROBLEM_STORE_MISMATCH)

    def test_SEAM_a_store_inside_the_control_repository_is_refused(self):
        inner = os.path.join(self.control, "state")
        os.mkdir(inner, 0o700)
        path = write_runtime_config(inner, self.document(
            workflow_store=os.path.realpath(inner)))
        self.refused(["--runtime-config", path, "once"],
                     runtime_config.PROBLEM_IN_REPOSITORY)

    def test_SEAM_a_symlinked_config_file_inside_the_control_repository_is_refused(self):
        # REAL filesystem resolution, nothing patched: a protected file
        # INSIDE the control repository, linked in as this store's
        # runtime-config.json. abspath keeps the link's own location, so
        # only the FILE's realpath reveals where it really is.
        inside = os.path.join(self.control, "inside-runtime-config.json")
        with open(inside, "w") as handle:
            json.dump(self.document(), handle)
        os.chmod(inside, 0o600)
        link = os.path.join(self.store, runtime_config.RUNTIME_CONFIG_FILE_NAME)
        os.symlink(inside, link)
        self.assertEqual(os.path.dirname(os.path.abspath(link)), self.store)
        self.assertEqual(os.path.realpath(link), os.path.realpath(inside))
        error = self.refused(["--runtime-config", link, "once"],
                             runtime_config.PROBLEM_IN_REPOSITORY)
        self.assertIn("the configuration file", str(error))
        # The same file, NOT linked from inside, is accepted: the refusal is
        # the containment, nothing else about the document.
        os.remove(link)
        self.config()
        code, stderr, _ = self.main(["--runtime-config", link, "once"])
        self.assertEqual(code, runtime_cli.EXIT_OK, stderr)

    def test_SEAM_the_cli_docstring_matches_the_branches_it_describes(self):
        """The docstring's three configurations, checked against the AST of
        ``_build_broker``: explicit --config writes trust beside its state;
        neither option and --runtime-config use the ONE consumed trust
        location (the single ``default_config_path()`` call)."""
        import inspect
        doc = " ".join(runtime_cli.__doc__.split())
        self.assertIn("``--config PATH``: the Telegram adapter's protected"
                      " configuration, UNCHANGED. Such an explicit run is"
                      " self-contained", doc)
        self.assertIn("neither option: the adapter's default configuration"
                      " file and default state directory, UNCHANGED; Claude"
                      " trust is written to the user-global file the started"
                      " Herdr reads, so a dispatch's trust is consumable", doc)
        self.assertNotIn("(or, with neither option", doc)
        tree = ast.parse(inspect.getsource(runtime_cli._build_broker))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "default_config_path"]
        self.assertEqual(len(calls), 1)
        [assigned] = [node for node in ast.walk(tree)
                      if isinstance(node, ast.Assign) and node.value is calls[0]]
        self.assertEqual(assigned.targets[0].id, "consumed_trust_config")
        [legacy] = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", None)
                    == "claude_config_path"]
        self.assertIsInstance(legacy, ast.IfExp)
        self.assertEqual(ast.unparse(legacy.test), "config_path")
        self.assertEqual(ast.unparse(legacy.body),
                         "os.path.join(state_directory, '.claude.json')")
        self.assertEqual(ast.unparse(legacy.orelse), "consumed_trust_config")
        [runtime_branch] = [node for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                            and getattr(node.func, "id", None)
                            == "_composed_broker"
                            and any(getattr(arg, "id", None) ==
                                    "consumed_trust_config"
                                    for arg in node.args)]
        self.assertEqual(ast.unparse(runtime_branch.args[0]),
                         "selected.workflow_store")

    def test_SEAM_a_file_mixing_the_adapter_schema_is_ambiguous(self):
        # Telegram's non-credential key: a Runtime file carrying ANY
        # adapter key is ambiguous, never repaired or guessed.
        path = self.config(repository=self.control)
        self.refused(["--runtime-config", path, "once"],
                     runtime_config.PROBLEM_AMBIGUOUS)

    def test_SEAM_naming_both_configurations_is_ambiguous_before_any_load(self):
        # The --config path does not even exist: refusing as ambiguous
        # (not "not found") proves neither loader ran first.
        error = self.refused(["--config", os.path.join(self.root, "nope.json"),
                              "--runtime-config", self.config(), "once"],
                             runtime_config.PROBLEM_AMBIGUOUS)
        self.assertIn("name exactly one", str(error))

    # -- the existing Telegram path, untouched ------------------------------

    def test_SEAM_the_legacy_config_route_never_accepts_runtime_input(self):
        # A VALID Runtime configuration handed to --config is read by the
        # adapter's loader ONLY, and fails closed with its existing
        # message: no fallback turns it into Runtime input.
        path = self.config()
        with self.assertRaises(telegram_config.ConfigError) as expected:
            telegram_config.load_config(path)
        code, stderr, execution = self.main(["--config", path, "once"])
        self.assertEqual(code, runtime_cli.EXIT_CONFIG)
        self.assertEqual(stderr.strip(), "dirun: config: %s"
                         % expected.exception)
        self.assertNotIn("runtime_config_", stderr)
        self.assertEqual(execution.calls, [])

    def test_SEAM_a_config_lacking_telegram_credentials_still_fails_for_the_adapter(self):
        with self.assertRaises(telegram_config.ConfigError) as caught:
            telegram_config.load_config(self.config())
        self.assertNotIsInstance(caught.exception,
                                 runtime_config.RuntimeConfigError)
        self.assertIn("bot_token", str(caught.exception))

    def test_SEAM_mirrors_are_pinned_to_their_sources(self):
        self.assertEqual(runtime_config.FORBIDDEN_MODE_BITS,
                         telegram_config.FORBIDDEN_MODE_BITS)
        # The adapter's closed key set, read from its own loader's source.
        with open(telegram_config.__file__) as handle:
            tree = ast.parse(handle.read())
        closed = [tuple(element.value for element in node.comparators[0].elts)
                  for node in ast.walk(tree)
                  if isinstance(node, ast.Compare)
                  and isinstance(node.ops[0], ast.NotIn)
                  and isinstance(node.comparators[0], ast.Tuple)]
        self.assertEqual(closed, [runtime_config.TELEGRAM_ADAPTER_KEYS])
        self.assertTrue(issubclass(runtime_config.RuntimeConfigError,
                                   telegram_config.ConfigError))


class RuntimeConfigDriveTests(RuntimeDriveCase):
    """The REAL production composition from a REAL Runtime configuration
    gets past the REAL dispatch trust check (``workspace_trust_consumable``)
    and completes a Grok-approved workflow; effects injected only after the
    loader validated and the Broker was composed. HOME is this case's
    private fixture directory, so the trust written is a fixture file."""

    def setUp(self):
        super(RuntimeConfigDriveTests, self).setUp()
        self.ref, self.workflow_id = self.approved()

    def composed(self, argv):
        namespace = runtime_cli._build_parser().parse_args(argv)
        broker, state = runtime_cli._build_broker(namespace)
        # Effects injected only now, after validation and composition.
        broker.transport = broker.worker.transport = self.transport
        broker._spawn = self.spawn
        broker._observe = lambda repo: json.loads(json.dumps(self.observation))
        broker._spawn_records = self.spawn_records
        broker._clock = self.clock
        broker.worker._readiness_probe_fn = self.readiness
        broker.worker._live_workspaces_fn = None
        broker.worker._workspace_close_fn = None
        checks = []
        real_check = broker.worker.workspace_trust_consumable

        def consumable(record_):
            outcome = real_check(record_)
            checks.append(outcome)
            return outcome
        broker.worker.workspace_trust_consumable = consumable
        return broker, state, checks

    def drive_composed(self, broker, passes=6):
        self.broker = broker
        return self.drive(passes)

    def remedy(self):
        status = self.status(self.ref)
        self.assertNotEqual(status["runtime_lock"]["state"],
                            conforming.LOCK_HELD)
        remedy = status["runtime_lock"]["remedy"]
        self.assertIn(remedy, status["next"])
        return remedy

    def test_SEAM_the_displayed_remedy_followed_literally_completes_the_workflow(self):
        remedy = self.remedy()
        command = shlex.split(remedy.split(
            "reads it: ", 1)[1].split(" (it runs only", 1)[0])
        config = shlex.split(remedy.split("PREREQUISITE: ", 1)[1].split(
            " must already exist", 1)[0])[0]
        document = json.loads(remedy.split("holding exactly: ", 1)[1].split(
            ". Grok Bot creates none", 1)[0])
        self.assertEqual(command[:3], ["python3", os.path.join(REPO, "dirun.py"),
                                       "--runtime-config"])
        self.assertTrue(os.path.isfile(command[1]))
        self.assertEqual(command[3], config)
        self.assertEqual(document, {
            "schema": runtime_config.RUNTIME_CONFIG_SCHEMA,
            "control_repository": CONTROL,
            "workflow_store": os.path.realpath(self.store_dir)})
        # Performed exactly as displayed: the file, its modes, the command.
        os.chmod(os.path.dirname(config), 0o700)
        write_runtime_config(os.path.dirname(config), document,
                             name=os.path.basename(config))
        broker, state, checks = self.composed(command[2:])
        self.assertEqual(state, os.path.realpath(self.store_dir))
        self.drive_composed(broker)
        entry = self.entry()
        self.assertEqual(entry["phase"], record.PHASE_COMPLETED)
        self.assertEqual(len(self.spawned), 1)
        # Past the REAL dispatch trust check, consumed where the started
        # Herdr reads it (the private fixture HOME's file).
        self.assertEqual(checks, [(True, None, None)])
        lease = entry["workspace_lease"]["path_realpath"]
        self.assertEqual(broker.worker.config_path,
                         os.path.join(self.root, ".claude.json"))
        self.assertFalse(os.path.exists(
            os.path.join(os.path.realpath(self.store_dir), ".claude.json")))
        self.assertEqual(self.result(self.ref)["verified_result"]["summary"],
                         VERIFIED_SUMMARY)
        # The fixture file's other entries survived the trust write.
        with open(os.path.join(self.root, ".claude.json")) as handle:
            self.assertIn("/Users/someone/other-repo",
                          json.load(handle)["projects"])
        self.assertTrue(lease)

    def test_SEAM_the_remedy_says_what_not_to_do_and_what_is_unsupported(self):
        remedy = self.remedy()
        self.assertIn("Do not start a Runtime for this store with --config",
                      remedy)
        self.assertIn("would stop each approved workflow at dispatch", remedy)
        self.assertIn("does not take a Runtime configuration", remedy)
        self.assertIn("Grok Bot creates none and has not checked", remedy)
        self.assertNotIn("dirun-agent.sh install", remedy)
        self.assertNotIn("--config " + os.path.join(self.store_dir, ""),
                         remedy)


if __name__ == "__main__":
    unittest.main()
