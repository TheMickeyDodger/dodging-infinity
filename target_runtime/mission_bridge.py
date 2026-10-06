"""Authorized Mission -> real Herdr run (Task 8 increment 2).

The thinnest real path from an AUTHORIZED Mission to a target Herdr and
back: dispatch, observe, reconcile, verify, result, pause/resume, cancel
and status. It mirrors ``target_runtime.dispatch`` rather than designing a
second transport: the request is the existing four-field
``build_spawn_request``, the effect is an injected ``spawn_fn`` whose
production default is the real bridge ``dispatch.production_spawn``
(hermetic tests inject a recorder; nothing here is invoked live), the
target identity is ``target_identity_from_spawn``, and every read goes
through the existing read-only seams (the broker's lazy Herdr observers,
``evidence.observation_supports`` and its canonical observation parse,
the hardened ``read_state_artifact`` primitive, the zero-argument
``GitTransport``, ``protected_surface_digest``).

Derivation, not acceptance. Every security-relevant value handed to the
runtime is DERIVED from the approved Mission record and its live
authorization, computed ONCE into an immutable ``DerivedRun``, validated,
and that same value is used: the target repository, the handoff text
(a fixed authority-only template over the approved proposal), the action
scope and delivery targets (exactly the authorization's), the revision
and digest. A caller may present a runtime ``entry``; it is snapshotted
once and every field it carries must EQUAL the derived value or the call
is refused naming the field, before anything is written or started. Two
inputs cannot come from the Mission and are stated as such:

- the local WORKSPACE path: a Mission names a canonical repository URL,
  not a checkout. It is accepted only when its OWN observed origin
  canonicalizes to exactly the approved repository and its worktree is
  clean; it is then bound in the durable intent, so every later read
  derives it from the record;
- the BASELINE commit is OBSERVED BY DI AT DISPATCH (the workspace HEAD
  at intent time), recorded as ``observed_baseline_commit_sha``. The
  human never approved it: the human approved a scope (repository,
  action scope, delivery targets), not a commit. It cannot widen that
  scope (scope, targets and repository are derived from the
  authorization and re-checked by Mission Core); what it fixes is the
  STARTING CONTENT the run is measured against, which the human did not
  approve. The one way starting content could ride along unapproved, a
  dirty worktree whose uncommitted changes would be indistinguishable
  from the run's own work, is gated: a workspace that is not clean is
  refused.

Three distinct facts. The run INTENT is recorded durably (Mission Core)
BEFORE any effect; the RECEIPT is recorded after the effect returns and
binds the target identity it named; RUNNING is set only from a read-only
observation of exactly that target. An intent with no receipt (or a
receipt naming no usable identity) is a HOLD: the outcome is unknown, so
it is never retried, never assumed un-dispatched and never re-authorized;
only ``reconcile`` resolves it, binding EXACTLY ONE provable child or
stopping durably BLOCKED (mirroring the broker's reconcile discipline).
The once-only property covers DI-RECORDED dispatch: Mission Core refuses
a second intent. It is not an exactly-once claim about external effects.

Association, not proximity (increment 2c). A child is THIS run's only
with durable proof: the target's own ``task.json`` description (the
handoff it was started with, as the read-only observer projects it)
begins with this intent's exact prefix naming the Mission id, revision
and proposal digest, and the task started no earlier than the intent.
Reconcile, the RUNNING transition, the cancel classification and the
verification identity all require it; a child that merely shares the
workspace path and task id is never adopted (reconcile stops durably,
``reconcile_unproven_association``). Mission Core adds the lease
discipline: a workspace carries at most one non-terminal run.

VERIFIED is a conjunction DI decides (``record.VERIFY_CONJUNCTS``),
recomputed by Mission Core; the model never decides it. A reported
result is necessary and never sufficient: it is a closed claim naming the
target task and the digests of the target's durable result artifact (its
checkpoint) and its canonical review artifact, and it is BOUND only when
DI's own fresh hardened reads of those files produce exactly those
digests for exactly that observed target. A target that stopped in a
failure state never verifies, whatever artifacts it left. Before a run
is recorded VERIFIED the bound result is recorded through the existing
proof seams (``record_artifact`` for both files, ``submit_evidence`` and
``accept_evidence`` of the binding under the approved ``run_result``
requirement), and Mission Core refuses VERIFIED without that accepted
evidence AND unless the WHOLE approved proof contract is satisfied, by
the existing ``progress.closure_failures`` evaluator inside its lock (an
unsatisfied obligation refuses with its own code and the Mission stays
RUNNING); ``result`` recovers the content from durable records alone by
re-reading and re-hashing the recorded locators, and reports a digest
whose content is gone as unrecoverable, never as a verified result. The
target's own canonical Reviewer APPROVE is TARGET-PRODUCED evidence that
its review ran and concluded, never independent verification.
Observation support is source-scoped (the registered verification set);
the raw global completeness is recorded unaltered, and an
agents-unprobed global PARTIAL is expected and weakens no consumed
evidence. Engineering completion (the target reports COMPLETE),
verified completion (the conjunction held) and delivered (never, here)
are reported separately.

Pause is DI-side only. A durable pause makes DI refuse to initiate any
further progression (dispatch, the RUNNING transition, reconciliation,
verification) until an explicit durable resume. It does NOT suspend
external work: a running target keeps executing and may keep writing to
its own workspace. There is no supported suspend seam in this
repository (no SIGSTOP/SIGCONT contract), and none is invented: stopping
a process group mid-write can hold locks and strand partial writes with
no defined resume.

Cancel records a durable cancel FIRST (the Mission becomes CANCELLED and
the terminal guard refuses every later DI write), then attempts control
only over a process group DI provably owns, through
``process_ownership.reap_owned`` (SIGKILL reaping gated on the owner
ledger), never by a bare identifier and never again after a collection
may have released it. The production spawn bridge returns a Herdr task
identity, not a DI-owned group, so for a production target control is
UNAVAILABLE and cancel reports intent recorded, quiescence unproven,
HOLD. Cancel always reports which of the four achieved states occurred.
DI-record late-write refusal is not stopping a worker or an in-flight
filesystem write; only the first is claimed.

Who writes. Every Mission Core run write carries this bridge's
``AuthenticatedContext`` of the ``local_process_user`` kind, naming the
uid the kernel reports to the DI process: it names the local account
that wrote the record, never a human, and (the standing in-process
limit) any same-user process could construct it.

Delivery is separate: ``DELIVERY_AUTHORITY`` is structurally "none", the
request carries no delivery field, and nothing here imports or calls the
delivery layer. Engineering approval confers no delivery: a RUNNING or
COMPLETED run stays ELIGIBLE as a P1-A6 delivery parent only through
Mission Core's narrowly scoped ``validate_delivery_parent_use`` (its
approved scope must name the target), consumed by the separate, exact
delivery authorization and receipt contract; general engineering
authority stays unavailable once the run consumed it.
"""

import copy
import hashlib
import os
from dataclasses import dataclass

from local_request import surface as surface_module
from mission import manifest as mission_manifest
from mission import record as mission_record
from target_runtime import broker as broker_module
from target_runtime import dispatch as dispatch_module
from target_runtime import evidence as evidence_module
from target_runtime import process_ownership as ownership_module
from target_runtime.git_transport import (
    CAPTURE_CAPTURED,
    GitTransport,
    GitTransportError,
)
from workflow_authority import canonical
from workflow_authority.digest import json_digest

DELIVERY_AUTHORITY = "none"
BRIDGE_TRANSPORT = "di_mission_bridge"
RESULT_ARTIFACT_KEY = "run_result_artifact"
REVIEW_ARTIFACT_KEY = "run_review_artifact"
LOCATOR_KIND_REPOSITORY_PATH = "REPOSITORY_PATH"
REPORTED_RESULT_KEYS = ("result_digest_sha256", "review_digest_sha256",
                        "task_id")

PROBLEM_NOT_RUNNABLE = "mission_bridge_not_runnable"
PROBLEM_SCOPE = "mission_bridge_scope_insufficient"
PROBLEM_NO_REPOSITORY = "mission_bridge_no_repository"
PROBLEM_NO_RESULT_REQUIREMENT = "mission_bridge_no_result_requirement"
PROBLEM_WORKSPACE_IDENTITY = "mission_bridge_workspace_identity"
PROBLEM_WORKSPACE_NOT_CLEAN = "mission_bridge_workspace_not_clean"
PROBLEM_BASELINE_UNREADABLE = "mission_bridge_baseline_unreadable"
PROBLEM_SURFACE_UNREADABLE = "mission_bridge_surface_unreadable"
PROBLEM_ENTRY_SUBSTITUTION = "mission_bridge_entry_substitution"
PROBLEM_ENTRY_UNKNOWN_FIELD = "mission_bridge_entry_unknown_field"
PROBLEM_AUTHORIZATION_MISMATCH = "mission_bridge_authorization_mismatch"
PROBLEM_PAUSED = "mission_bridge_paused"
PROBLEM_WRONG_STATE = "mission_bridge_wrong_state"
PROBLEM_MISSION_CORE = "mission_bridge_mission_core_refused"

# ONE status projection and ONE wording, shared with the operator surface
# (``local_request.surface``), so the two can never drift apart.
PAUSE_STATEMENT = surface_module.RUN_PAUSE_STATEMENT
HOLD_STATEMENT = surface_module.RUN_HOLD_STATEMENT
BASELINE_STATEMENT = surface_module.RUN_BASELINE_STATEMENT
PROBLEM_PROOF_OPERATION = "mission_bridge_proof_operation"
# The EXISTING Mission State seams the route may call to satisfy approved
# proof obligations, with exactly their own arguments. Each is one
# explicit step: nothing here chains a submission into an acceptance, so
# no claim is ever accepted because it was asserted.
PROOF_OPERATIONS = {
    "submit_evidence": ("requirement_key", "kind", "content_digest_sha256",
                        "artifact_ids"),
    "accept_evidence": ("evidence_id", "content_digest_sha256"),
    "record_claim": ("requirement_key", "statement"),
    "record_artifact": ("key", "role", "locator_kind", "locator",
                        "content_digest_sha256", "available", "derived_from"),
    "bind_dependency": ("slot_key", "reference"),
    "resolve_dependency": ("dependency_id", "evidence_id"),
    "observe_resource_readiness": ("resource_key", "status", "observed_at"),
}

# The runtime-entry fields a caller may present, each compared EXACTLY
# against the derived value. Anything else is refused.
ENTRY_FIELDS = (
    "mission_id", "revision", "proposal_digest_sha256", "authorization_id",
    "target_repository_url", "baseline_commit_sha",
    "observed_baseline_commit_sha", "handoff_text", "action_scope",
    "delivery_targets", "target_repo", "task", "alias", "preset",
)
# The two durable target artifacts a run result is bound to, as
# workspace-relative locators the hardened primitive can re-read.
_STATE_DIRS = (".herd", "state")
_REVIEW_DIRS = (".herd", "state", "reviews")


class MissionBridgeRefusal(Exception):
    def __init__(self, problem, reason, **details):
        super(MissionBridgeRefusal, self).__init__(reason)
        self.problem = problem
        self.reason = reason
        self.details = details


def _refuse(problem, reason, **details):
    raise MissionBridgeRefusal(problem, reason, **details)


def default_context():
    """The bridge's own principal: the local process user, by uid."""
    return mission_record.AuthenticatedContext(
        transport=BRIDGE_TRANSPORT,
        principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
        principal_ref="uid-%d" % os.getuid())


@dataclass(frozen=True)
class DerivedRun:
    """Everything the run hands to the runtime, derived once from the
    approved Mission record and immutable afterwards."""

    mission_id: str
    revision: int
    proposal_digest_sha256: str
    authorization_id: str
    target_repository_url: str
    workspace_realpath: str
    observed_baseline_commit_sha: str
    surface_baseline_digest_sha256: str
    handoff_text: str
    action_scope: tuple
    delivery_targets: tuple

    def spawn_request(self):
        """A FRESH four-field request each call, built by the existing
        ``build_spawn_request``."""
        return dispatch_module.build_spawn_request({
            "workspace_lease": {"path_realpath": self.workspace_realpath},
            "handoff": {"text": self.handoff_text},
            "workflow_id": self.mission_id,
        })

    def identity_entry(self):
        return {"workflow_id": self.mission_id,
                "target": {"canonical_url": self.target_repository_url}}

    def handoff_digest(self):
        return hashlib.sha256(self.handoff_text.encode("utf-8")).hexdigest()

    def claims(self):
        request = self.spawn_request()
        return {
            "mission_id": self.mission_id, "revision": self.revision,
            "proposal_digest_sha256": self.proposal_digest_sha256,
            "authorization_id": self.authorization_id,
            "target_repository_url": self.target_repository_url,
            "baseline_commit_sha": self.observed_baseline_commit_sha,
            "observed_baseline_commit_sha": self.observed_baseline_commit_sha,
            "handoff_text": self.handoff_text,
            "action_scope": list(self.action_scope),
            "delivery_targets": list(self.delivery_targets),
            "target_repo": request["target_repo"], "task": request["task"],
            "alias": request["alias"], "preset": request["preset"],
        }


def handoff_prefix(mission_id, revision, digest):
    """The handoff's first sentence: it names the exact Mission, revision
    and proposal digest. The child Herdr records the handoff as its own
    ``task.json`` description, and the observer projects that field
    truncated to 200 characters; this prefix is 150, so the target's OWN
    durable task record names the intent that started it."""
    return "AUTHORIZED MISSION %s (revision %d, proposal digest %s)." % (
        mission_id, revision, digest)


def handoff_text(mission_id, revision, digest, proposal, scope, targets):
    """The handoff: the approved Mission content in a FIXED template,
    authority only, no engineering plan. No surrounding whitespace, so the
    bridge's strip is an identity."""
    return handoff_prefix(mission_id, revision, digest) + (
        " This is"
        " the approved Mission content only, NOT an engineering plan; the"
        " Supervisor owns all technical decisions.\n"
        "\nOBJECTIVE\n%s\n"
        "\nTARGET CONTEXT\n%s\n"
        "\nREQUESTED SCOPE\n%s\n"
        "\nAPPROVED ACTION SCOPE\n%s\n"
        "\nAPPROVED DELIVERY TARGETS\n%s\n"
        "\nDELIVERY\nThis run confers no delivery authority"
        " (delivery_authority none)."
    ) % (
        proposal["objective"].strip(),
        proposal["target_context"].strip() or "(none)",
        proposal["requested_scope"].strip(), ", ".join(scope),
        ", ".join(targets) or "(none)",
    )


def result_binding(mission_id, task_id, result_name, result_digest,
                   review_name, review_digest):
    """The exact content of a run's result evidence."""
    return {
        "mission_id": mission_id, "task_id": task_id,
        "result_locator": "/".join(_STATE_DIRS + (result_name,)),
        "result_digest_sha256": result_digest,
        "review_locator": "/".join(_REVIEW_DIRS + (review_name,)),
        "review_digest_sha256": review_digest,
    }


def _snapshot_entry(entry):
    """Read the caller's entry ONCE into plain copies; never again."""
    if entry is None:
        return {}
    if not isinstance(entry, dict):
        _refuse(PROBLEM_ENTRY_UNKNOWN_FIELD, "a runtime entry must be a mapping")
    snapshot = {}
    for key in list(entry.keys()):
        if key not in ENTRY_FIELDS:
            _refuse(PROBLEM_ENTRY_UNKNOWN_FIELD,
                    "runtime entry field %r is not accepted; every runtime"
                    " value is derived from the approved Mission" % (key,),
                    field=key)
        snapshot[key] = copy.deepcopy(entry[key])
    return snapshot


def _check_entry(snapshot, derived):
    claims = derived.claims()
    for key in ENTRY_FIELDS:
        if key in snapshot and snapshot[key] != claims[key]:
            _refuse(PROBLEM_ENTRY_SUBSTITUTION,
                    "runtime entry field %r does not match the approved"
                    " Mission (derived %r); a caller value never widens or"
                    " replaces approved content, and nothing was recorded or"
                    " started" % (key, claims[key]), field=key)


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and not (
        set(value) - set("0123456789abcdef"))


def names_this_run(raw_observation, mission_id, intent):
    """Durable proof that the workspace's CURRENT task is the one THIS
    run's intent started: the target's own ``task.json`` description (as
    observed) begins with this intent's exact handoff prefix (Mission id,
    revision, proposal digest), and the task started no earlier than the
    intent was recorded. An old or foreign child sharing the workspace
    fails both or either, and is never adopted."""
    task = raw_observation.get("task") if isinstance(
        raw_observation, dict) and isinstance(raw_observation.get("task"),
                                              dict) else {}
    description, started = task.get("description"), task.get("started_at")
    return bool(
        isinstance(description, str)
        and description.startswith(handoff_prefix(
            mission_id, intent["revision"], intent["proposal_digest_sha256"]))
        and isinstance(started, int) and not isinstance(started, bool)
        and started >= intent["recorded_at"])


class MissionBridge(object):
    """See the module docstring. ``missions`` is the Mission Core
    service; every other collaborator is injectable and defaults to the
    real read-only seam or the real spawn bridge."""

    def __init__(self, missions, control_repo, clock, spawn_fn=None,
                 observer_fn=None, spawn_records_fn=None, transport=None,
                 surface_digest_fn=None, ownership=None,
                 owner_directory=None, context=None):
        self._missions = missions
        self._control_repo = control_repo
        self._clock = clock
        self._spawn = spawn_fn or dispatch_module.production_spawn
        self._observer = observer_fn or broker_module._production_observer
        self._spawn_records = (
            spawn_records_fn or broker_module._production_spawn_records_observer)
        self._transport = transport or GitTransport()
        self._surface_digest = (
            surface_digest_fn or evidence_module.protected_surface_digest)
        self._ownership = ownership or ownership_module
        self._owner_directory = owner_directory
        self._context = context or default_context()

    # -- reads ------------------------------------------------------------

    def _view(self, mission_id):
        try:
            return self._missions.get(mission_id)
        except mission_record.MissionError as exc:
            _refuse(PROBLEM_MISSION_CORE, str(exc), mission_problem=exc.problem)

    def _record(self, method, *args):
        try:
            return method(*args)
        except mission_record.MissionError as exc:
            _refuse(PROBLEM_MISSION_CORE, str(exc), mission_problem=exc.problem)

    def status(self, mission_id):
        """Answered from durable records alone: no caller context. The
        projection is the operator surface's ``run_status``; ``delivered``
        is DERIVED from attested P1-A6 receipts, never a constant."""
        mission = self._view(mission_id)["record"]
        try:
            state_projection = self._missions.get_state(mission_id)
        except mission_record.MissionError as exc:
            _refuse(PROBLEM_MISSION_CORE, str(exc), mission_problem=exc.problem)
        result = surface_module.run_status(mission, state_projection)
        result.update({
            "mission_id": mission_id, "state": mission["state"],
            "verified_completion": result["engineering_verified"],
            "delivered": result["delivery"]["delivered"],
            "delivery_authority": DELIVERY_AUTHORITY,
        })
        return result

    # -- dispatch ---------------------------------------------------------

    def _derive(self, view, workspace_path):
        mission = view["record"]
        mission_id = mission["mission_id"]
        authorization_id = view["live_authorization_id"]
        if mission["state"] != mission_record.STATE_AUTHORIZED or (
            authorization_id is None
        ):
            _refuse(PROBLEM_NOT_RUNNABLE,
                    "mission %s is %s with no live authorization; only an"
                    " AUTHORIZED Mission with a live authorization runs"
                    % (mission_id, mission["state"]))
        authorization = next(a for a in view["authorizations"]
                             if a["authorization_id"] == authorization_id)
        scope = tuple(authorization["authorized_action_scope"])
        if not set(mission_record.RUN_REQUIRED_ACTION_SCOPE) <= set(scope):
            _refuse(PROBLEM_SCOPE,
                    "the approved action scope %r does not include %s, which"
                    " a run needs: a started target can change its workspace"
                    % (list(scope), mission_record.RUN_REQUIRED_ACTION_SCOPE[0]))
        entry = mission_manifest.current_revision_entry(mission)
        proposal = entry["proposal"]
        url = proposal["repository_url"]
        if url is None:
            _refuse(PROBLEM_NO_REPOSITORY,
                    "the approved Mission names no repository to run against")
        requirements = (proposal.get("proof_contract") or {}).get(
            "requirements", [])
        if not any(r["key"] == mission_record.RUN_RESULT_REQUIREMENT_KEY
                   and mission_record.EVIDENCE_KIND_VERIFICATION_RECORD
                   in r["evidence_kinds"] for r in requirements):
            _refuse(PROBLEM_NO_RESULT_REQUIREMENT,
                    "the approved proof contract declares no %r requirement"
                    " accepting %s, so a run result could not be durably bound"
                    % (mission_record.RUN_RESULT_REQUIREMENT_KEY,
                       mission_record.EVIDENCE_KIND_VERIFICATION_RECORD))
        if not isinstance(workspace_path, str) or not os.path.isabs(
            workspace_path
        ):
            _refuse(PROBLEM_WORKSPACE_IDENTITY,
                    "the workspace must be an absolute path")
        real = os.path.realpath(workspace_path)
        try:
            origin = canonical.canonicalize_repository_url(
                self._transport.remote_url(real).strip()).repository_url
        except (GitTransportError, canonical.CanonicalizationError) as exc:
            _refuse(PROBLEM_WORKSPACE_IDENTITY,
                    "the workspace origin could not be read as a canonical"
                    " repository (%s)" % exc)
        if origin != url:
            _refuse(PROBLEM_WORKSPACE_IDENTITY,
                    "the workspace origin %r is not the approved repository"
                    " %r" % (origin, url))
        try:
            capture = self._transport.status_porcelain_readonly(real)
        except GitTransportError as exc:
            _refuse(PROBLEM_WORKSPACE_NOT_CLEAN,
                    "the workspace status could not be read (%s)" % exc)
        if not isinstance(capture, dict) or capture.get("status") != (
            CAPTURE_CAPTURED
        ) or capture.get("text", "").strip():
            _refuse(PROBLEM_WORKSPACE_NOT_CLEAN,
                    "the workspace is not clean: uncommitted content would be"
                    " indistinguishable from the run's own work")
        try:
            baseline = self._transport.head_commit(real).strip()
        except GitTransportError as exc:
            _refuse(PROBLEM_BASELINE_UNREADABLE,
                    "the workspace HEAD could not be read (%s)" % exc)
        if len(baseline) != 40 or set(baseline) - set("0123456789abcdef"):
            _refuse(PROBLEM_BASELINE_UNREADABLE,
                    "the workspace HEAD is not a full commit id")
        surface = self._surface_digest(self._control_repo)
        if not isinstance(surface, dict) or surface.get("status") != (
            evidence_module.BINDING_EXACT
        ) or not _hex64(surface.get("digest")):
            _refuse(PROBLEM_SURFACE_UNREADABLE,
                    "the protected-surface baseline could not be computed"
                    " exactly; no baseline, no run")
        targets = tuple(authorization["authorized_delivery_targets"])
        return DerivedRun(
            mission_id=mission_id, revision=entry["revision"],
            proposal_digest_sha256=entry["proposal_digest_sha256"],
            authorization_id=authorization_id, target_repository_url=url,
            workspace_realpath=real, observed_baseline_commit_sha=baseline,
            surface_baseline_digest_sha256=surface["digest"],
            handoff_text=handoff_text(
                mission_id, entry["revision"], entry["proposal_digest_sha256"],
                proposal, scope, targets),
            action_scope=scope, delivery_targets=targets,
        )

    def _activate_contract(self, mission_id, revision):
        """Activate the approved proof contract while the authorization is
        live, so the run's result can later be bound under it."""
        state = self._missions.get_state(mission_id)
        if state["contract"]["active"] and state["contract"]["revision"] == revision:
            return
        operation = self._missions.mint_state_operation_id(self._context)
        self._record(self._missions.activate_proof_contract, mission_id,
                     operation, state["sequence"], self._context)

    def dispatch(self, mission_id, workspace_path, entry=None):
        snapshot = _snapshot_entry(entry)
        view = self._view(mission_id)
        run = view["record"].get("run")
        if run is not None and run["intent"] is not None:
            # Once-only for DI-recorded dispatch: an existing intent is
            # never dispatched again, whatever its outcome.
            result = self.status(mission_id)
            result["duplicate"] = True
            return result
        if mission_manifest.run_is_paused(run):
            _refuse(PROBLEM_PAUSED, PAUSE_STATEMENT)
        derived = self._derive(view, workspace_path)
        _check_entry(snapshot, derived)
        self._activate_contract(mission_id, derived.revision)
        self._record(
            self._missions.record_run_intent,
            mission_id, derived.authorization_id, derived.revision,
            derived.proposal_digest_sha256, derived.target_repository_url,
            derived.workspace_realpath, derived.observed_baseline_commit_sha,
            json_digest(derived.spawn_request()), derived.handoff_digest(),
            derived.surface_baseline_digest_sha256,
            list(derived.action_scope), list(derived.delivery_targets),
            self._context)
        # The intent is durable. From here on an unknown outcome is a HOLD.
        try:
            spawned = self._spawn(self._control_repo, derived.spawn_request())
        except Exception:
            return self._hold(mission_id, "the spawn raised; whether a child"
                              " started is unknown")
        identity = dispatch_module.target_identity_from_spawn(
            spawned, derived.identity_entry(), self._clock())
        task_id = (None if identity["task_id"] == dispatch_module.UNRESOLVED_TASK_ID
                   else identity["task_id"])
        try:
            self._missions.record_run_receipt(
                mission_id, task_id, "start_result", self._owned_group(spawned),
                self._context)
        except Exception:
            return self._hold(mission_id, "the spawn returned but its receipt"
                              " could not be recorded")
        return self.status(mission_id)

    def _owned_group(self, spawned):
        """A process group the spawn result names AND the owner ledger
        records; otherwise None. The production bridge names none."""
        group = spawned.get("owned_process_group") if isinstance(
            spawned, dict) else None
        if isinstance(group, int) and not isinstance(group, bool) and (
            group > 1 and group in self._ownership.owned_groups(
                self._owner_directory)
        ):
            return group
        return None

    def _hold(self, mission_id, why):
        result = self.status(mission_id)
        result.update({"hold": True, "hold_reason": why,
                       "statement": HOLD_STATEMENT})
        return result

    # -- observation and reconciliation ------------------------------------

    def _bound(self, mission_id, states):
        mission = self._view(mission_id)["record"]
        if mission["state"] not in states:
            _refuse(PROBLEM_WRONG_STATE, "mission %s is %s"
                    % (mission_id, mission["state"]))
        run = mission.get("run")
        if mission_manifest.run_is_paused(run):
            _refuse(PROBLEM_PAUSED, PAUSE_STATEMENT)
        if run is None or run["intent"] is None:
            _refuse(PROBLEM_WRONG_STATE, "mission %s has no run" % mission_id)
        return mission, run

    def _observe(self, workspace):
        """ONE observer call: the canonical bindings plus the raw
        observation the association proof reads."""
        captured = {}

        def observer(path):
            captured["raw"] = self._observer(path)
            return captured["raw"]

        bindings, task_id, latest_round = evidence_module._observation_bindings(
            observer, workspace)
        return bindings, task_id, latest_round, captured.get("raw")

    def observe(self, mission_id):
        """Read-only observation of the bound target; RUNNING only when it
        shows exactly the target the receipt names AND the workspace's own
        task record names this run's intent."""
        mission, run = self._bound(mission_id, (mission_record.STATE_AUTHORIZED,))
        receipt = run["receipt"]
        if receipt is None or receipt["task_id"] is None:
            return self._hold(mission_id, "no bound target identity to observe")
        bindings, _, _, raw = self._observe(run["intent"]["workspace_realpath"])
        target = bindings["target_task"]
        if target["status"] != evidence_module.BINDING_EXACT or (
            target["task_id"] != receipt["task_id"]
        ) or not names_this_run(raw, mission_id, run["intent"]):
            result = self.status(mission_id)
            result["observed"] = False
            return result
        self._record(self._missions.record_observed_running, mission_id,
                     receipt["task_id"], target["task_status"]
                     in broker_module._TARGET_TERMINAL_STATUSES, self._context)
        result = self.status(mission_id)
        result["observed"] = True
        return result

    def _block(self, mission_id, reason, detail):
        self._record(self._missions.record_run_stop, mission_id, reason,
                     self._context)
        result = self.status(mission_id)
        result["detail"] = detail
        return result

    def reconcile(self, mission_id):
        """HOLD -> exactly one provable child, or a durable BLOCKED."""
        mission, run = self._bound(mission_id, (mission_record.STATE_AUTHORIZED,))
        receipt = run["receipt"]
        if receipt is not None and receipt["task_id"] is not None:
            _refuse(PROBLEM_WRONG_STATE,
                    "mission %s's target identity is already bound" % mission_id)
        workspace = run["intent"]["workspace_realpath"]
        records = self._spawn_records(self._control_repo)
        listed = records.get("listed") if isinstance(records, dict) else None
        if not isinstance(records, dict) or records.get("truncated") is True or (
            records.get("state") not in ("available", "empty")
        ) or not isinstance(listed, list) or records.get("count") != len(listed):
            return self._block(mission_id, "reconcile_degraded",
                               "the control-side spawn records are not a clean,"
                               " complete listing")
        raw = self._observer(workspace)
        supported, _ = evidence_module.observation_supports(
            raw, evidence_module.RECONCILE_CONSUMED_SOURCES)
        task = raw.get("task") if isinstance(raw, dict) and isinstance(
            raw.get("task"), dict) else {}
        observed = task.get("id")
        if not supported or task.get("state") != "available" or not isinstance(
            observed, str
        ) or not observed:
            return self._block(mission_id, "reconcile_degraded",
                               "the workspace reports no observable task identity")
        if not names_this_run(raw, mission_id, run["intent"]):
            # Durable proof is missing: the workspace's own task record does
            # not name THIS intent (an old or foreign child sharing the
            # workspace). Never adopted; stopped for a human to resolve.
            return self._block(mission_id, "reconcile_unproven_association",
                               "the workspace's task record does not name this"
                               " Mission's intent (or started before it); a"
                               " foreign or old child is never adopted")
        matching = [c for c in listed if isinstance(c, dict)
                    and isinstance(c.get("repo"), str)
                    and os.path.realpath(c["repo"]) == workspace]
        if not matching:
            return self._block(mission_id, "reconcile_no_match",
                               "no recorded child names this workspace")
        if any(c.get("task_id") != observed for c in matching):
            return self._block(mission_id, "reconcile_conflicting_identity",
                               "a matching child record disagrees with the"
                               " workspace's own task identity")
        if len(matching) > 1:
            return self._block(mission_id, "reconcile_multiple_matches",
                               "%d recorded children name this workspace"
                               % len(matching))
        self._record(self._missions.record_run_receipt, mission_id, observed,
                     "reconciliation", None, self._context)
        return self.status(mission_id)

    # -- verification -----------------------------------------------------

    def _fresh_reads(self, workspace):
        bindings, task_id, latest_round, raw = self._observe(workspace)
        review_file, _ = evidence_module._review_file_bindings(
            workspace, task_id, latest_round)
        checkpoint = evidence_module._read_binding(
            workspace, _STATE_DIRS, evidence_module.CHECKPOINT_FILE_NAME)
        try:
            head = {"status": evidence_module.BINDING_EXACT,
                    "commit_sha": self._transport.head_commit(workspace).strip()}
        except GitTransportError:
            head = {"status": evidence_module.BINDING_REFUSED_UNREADABLE,
                    "commit_sha": None}
        surface = self._surface_digest(self._control_repo)
        surface = surface if isinstance(surface, dict) else {}
        return raw, {
            "observation": bindings["observation"],
            "target_task": bindings["target_task"],
            "review_decision": bindings["review_decision"],
            "review_file": review_file, "result_file": checkpoint,
            "live_head": head,
            "protected_surface": {"status": surface.get("status"),
                                  "digest": surface.get("digest")},
        }

    def verify(self, mission_id, reported_result):
        """DI decides VERIFIED from a fresh read after the turn; the
        reported result is necessary, never sufficient."""
        mission, run = self._bound(mission_id, (mission_record.STATE_RUNNING,))
        intent, receipt = run["intent"], run["receipt"]
        workspace = intent["workspace_realpath"]
        raw, evidence = self._fresh_reads(workspace)
        exact = evidence_module.BINDING_EXACT
        valid = all(isinstance(b, dict) and isinstance(b.get("status"), str)
                    for b in evidence.values())
        target, review = evidence["target_task"], evidence["review_decision"]
        review_file, result_file = evidence["review_file"], evidence["result_file"]
        head, surface = evidence["live_head"], evidence["protected_surface"]
        reported = isinstance(reported_result, dict) and sorted(
            reported_result) == sorted(REPORTED_RESULT_KEYS) and all(
            isinstance(reported_result[k], str) for k in REPORTED_RESULT_KEYS)
        identity = target.get("status") == exact and (
            target.get("task_id") == receipt["task_id"]) and names_this_run(
            raw, mission_id, intent)
        bound = reported and identity and (
            reported_result["task_id"] == receipt["task_id"]
            and review_file["status"] == exact and result_file["status"] == exact
            and _hex64(review_file["digest"]) and _hex64(result_file["digest"])
            and reported_result["review_digest_sha256"] == review_file["digest"]
            and reported_result["result_digest_sha256"] == result_file["digest"])
        holds = {
            "result_reported": reported,
            "result_bound": bound,
            # The observation, live HEAD and surface bindings; the two
            # artifact files are judged by ``result_bound``.
            "evidence_complete": valid and all(
                evidence[k]["status"] == exact for k in (
                    "observation", "target_task", "review_decision",
                    "live_head", "protected_surface"))
            and evidence["observation"].get("supports_verification") is True,
            "evidence_valid": valid,
            "target_identity": identity,
            "target_stopped": target.get("status") == exact
            and target.get("task_status") in broker_module._TARGET_TERMINAL_STATUSES,
            # Stopped is not succeeded: ERROR and ABORTED never verify.
            "target_succeeded": target.get("status") == exact
            and target.get("task_status") == "COMPLETE",
            # Target-produced evidence that the target's own review ran
            # and concluded APPROVE; never independent verification.
            "review_approve": review.get("status") == exact
            and review.get("decision") == "APPROVE",
            "baseline_unmoved": head["status"] == exact
            and head["commit_sha"] == intent["observed_baseline_commit_sha"],
            "surface_receipt_present": _hex64(
                intent["surface_baseline_digest_sha256"]),
            "surface_unchanged": surface.get("status") == exact
            and surface.get("digest") == intent["surface_baseline_digest_sha256"],
            "delivery_authority_none": DELIVERY_AUTHORITY == "none",
        }
        verified, _ = mission_record.verification_outcome(holds)
        evidence_id = None
        if verified:
            evidence_id = self._record_result(
                mission_id, receipt["task_id"], result_file, review_file)
        observed_status = target.get("task_status")
        outcome = self._record(
            self._missions.record_verification, mission_id, holds,
            evidence["observation"].get("completeness"),
            evidence["observation"].get("supports_verification"),
            json_digest(reported_result) if reported else None, evidence_id,
            observed_status[:mission_record.MAX_OBSERVED_STATUS_CHARS]
            if isinstance(observed_status, str) else "unobserved",
            self._context)
        result = self.status(mission_id)
        # A blocked-pending-proof outcome is DURABLE and non-terminal: the
        # status above already reports it (phase, blocker codes, stopped
        # target); there is no final verification record for it.
        result.update({
            "verification": None if "pending_proof" in outcome else outcome,
            "engineering_completion": target.get("status") == exact
            and target.get("task_status") == "COMPLETE",
            "review_evidence": "target-produced, not independent verification",
        })
        return result

    def prove(self, mission_id, operation, arguments):
        """ONE existing Mission State proof operation, under the bridge's
        context and the record's current sequence, for an approved proof
        obligation. The operation and its arguments are exactly the seam's
        own (``PROOF_OPERATIONS``); the seam does all validation. It never
        submits AND accepts in one call, so nothing is accepted because a
        model asserted it."""
        names = PROOF_OPERATIONS.get(operation)
        if names is None:
            _refuse(PROBLEM_PROOF_OPERATION,
                    "proof operation %r is not one of the existing seams %s"
                    % (operation, sorted(PROOF_OPERATIONS)))
        if not isinstance(arguments, dict) or sorted(arguments) != sorted(names):
            _refuse(PROBLEM_PROOF_OPERATION,
                    "proof operation %s takes exactly %s" % (operation,
                                                            list(names)))
        if mission_manifest.run_is_paused(self._view(mission_id)["record"].get("run")):
            _refuse(PROBLEM_PAUSED, PAUSE_STATEMENT)
        operation_id = self._missions.mint_state_operation_id(self._context)
        sequence = self._missions.get_state(mission_id)["sequence"]
        outcome = self._record(
            getattr(self._missions, operation), mission_id, operation_id,
            sequence, *([arguments[name] for name in names] + [self._context]))
        result = self.status(mission_id)
        result["proof_operation"] = {"operation": operation, "outcome": outcome}
        return result

    def _record_result(self, mission_id, task_id, result_file, review_file):
        """The bound result, through the EXISTING proof seams: both target
        artifacts, then the binding as VERIFICATION_RECORD evidence under
        the approved ``run_result`` requirement, submitted and accepted."""
        binding = result_binding(
            mission_id, task_id, evidence_module.CHECKPOINT_FILE_NAME,
            result_file["digest"], review_file["name"], review_file["digest"])
        digest = json_digest(binding)
        state = self._missions.get_state(mission_id)
        # A retry after a refused VERIFIED (an unsatisfied contract) reuses
        # the already accepted evidence of exactly this binding.
        for existing in (state["record"] or {}).get("evidence", []):
            if existing["requirement_key"] == (
                mission_record.RUN_RESULT_REQUIREMENT_KEY
            ) and existing["content_digest_sha256"] == digest and (
                existing["acceptance"] is not None
            ) and existing["invalidation"] is None:
                return existing["evidence_id"]
        sequence = state["sequence"]

        def apply(method, *args):
            nonlocal sequence
            operation = self._missions.mint_state_operation_id(self._context)
            outcome = self._record(method, mission_id, operation, sequence,
                                   *(args + (self._context,)))
            sequence = outcome["sequence"]
            return outcome

        result_artifact = apply(
            self._missions.record_artifact, RESULT_ARTIFACT_KEY,
            mission_record.ARTIFACT_ROLE_PRODUCED, LOCATOR_KIND_REPOSITORY_PATH,
            binding["result_locator"], binding["result_digest_sha256"], True, [])
        review_artifact = apply(
            self._missions.record_artifact, REVIEW_ARTIFACT_KEY,
            mission_record.ARTIFACT_ROLE_VERIFICATION, LOCATOR_KIND_REPOSITORY_PATH,
            binding["review_locator"], binding["review_digest_sha256"], True,
            [result_artifact["artifact_id"]])
        submitted = apply(
            self._missions.submit_evidence, mission_record.RUN_RESULT_REQUIREMENT_KEY,
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, digest,
            [result_artifact["artifact_id"], review_artifact["artifact_id"]])
        apply(self._missions.accept_evidence, submitted["evidence_id"], digest)
        return submitted["evidence_id"]

    def result(self, mission_id):
        """Recover a VERIFIED run's result from durable records alone (no
        caller context): the accepted evidence, its two artifacts, and the
        content re-read and re-hashed from the recorded locators. A digest
        whose content is gone or changed is UNRECOVERABLE, never a verified
        result."""
        mission = self._view(mission_id)["record"]
        run = mission.get("run")
        verification = run["verification"] if run else None
        if not verification or not verification["verified"]:
            return {"mission_id": mission_id, "verified_result": False,
                    "recoverable": False, "reason": "no VERIFIED run"}
        state = self._missions.get_state(mission_id)["record"]
        evidence = next(e for e in state["evidence"]
                        if e["evidence_id"] == verification["result_evidence_id"])
        artifacts = dict((a["artifact_id"], a) for a in state["artifacts"])
        linked = [artifacts[i] for i in evidence["artifact_ids"]]
        by_key = dict((a["key"], a) for a in linked)
        workspace = run["intent"]["workspace_realpath"]
        contents = {}
        for key in (RESULT_ARTIFACT_KEY, REVIEW_ARTIFACT_KEY):
            artifact = by_key.get(key)
            parts = tuple(artifact["locator"].split("/")) if artifact else ()
            if artifact is None or parts[:-1] not in (_STATE_DIRS, _REVIEW_DIRS):
                return self._unrecoverable(mission_id, "%s is not recorded" % key)
            status, _, digest, text = evidence_module.read_state_artifact(
                workspace, parts[:-1], parts[-1])
            if digest != artifact["content_digest_sha256"] or text is None:
                return self._unrecoverable(
                    mission_id, "%s content is gone or changed (%s); a digest"
                    " alone is never a verified result" % (key, status))
            contents[key] = (artifact, text)
        result_artifact, result_text = contents[RESULT_ARTIFACT_KEY]
        review_artifact, review_text = contents[REVIEW_ARTIFACT_KEY]
        binding = result_binding(
            mission_id, run["receipt"]["task_id"],
            result_artifact["locator"].split("/")[-1],
            result_artifact["content_digest_sha256"],
            review_artifact["locator"].split("/")[-1],
            review_artifact["content_digest_sha256"])
        if json_digest(binding) != evidence["content_digest_sha256"] or (
            evidence["acceptance"] is None
        ):
            return self._unrecoverable(mission_id, "the recorded binding does"
                                       " not match its accepted evidence")
        return {
            "mission_id": mission_id, "verified_result": True,
            "recoverable": True, "task_id": run["receipt"]["task_id"],
            "evidence_id": evidence["evidence_id"], "binding": binding,
            "result_text": result_text, "review_text": review_text,
            "review_evidence": "target-produced, not independent verification",
            "delivered": surface_module.run_status(
                mission, self._missions.get_state(mission_id))["delivery"][
                    "delivered"],
            "delivery_authority": DELIVERY_AUTHORITY,
        }

    @staticmethod
    def _unrecoverable(mission_id, reason):
        return {"mission_id": mission_id, "verified_result": False,
                "recoverable": False, "reason": reason}

    # -- pause / cancel ---------------------------------------------------

    def _bound_authorization(self, mission, claimed):
        run = mission.get("run")
        intent = run["intent"] if run else None
        bound = intent["authorization_id"] if intent else (
            mission["authorization_ids"][-1] if mission["authorization_ids"]
            else None)
        if claimed is not None and claimed != bound:
            _refuse(PROBLEM_AUTHORIZATION_MISMATCH,
                    "authorization %r is not the one mission %s is bound to;"
                    " nothing was changed" % (claimed, mission["mission_id"]))
        return bound

    def pause(self, mission_id, authorization_id=None):
        mission = self._view(mission_id)["record"]
        bound = self._bound_authorization(mission, authorization_id)
        self._record(self._missions.record_pause, mission_id, bound,
                     self._context)
        result = self.status(mission_id)
        result["statement"] = PAUSE_STATEMENT
        result["external_work_suspended"] = False
        return result

    def resume(self, mission_id, authorization_id=None):
        mission = self._view(mission_id)["record"]
        bound = self._bound_authorization(mission, authorization_id)
        self._record(self._missions.record_resume, mission_id, bound,
                     self._context)
        return self.status(mission_id)

    def cancel(self, mission_id, authorization_id=None):
        mission = self._view(mission_id)["record"]
        bound = self._bound_authorization(mission, authorization_id)
        run = mission.get("run")
        cancel = run["cancel"] if run else None
        if cancel is not None and cancel["completed_at"] is None:
            # An interrupted cancel: complete the record, never re-signal.
            return self._complete_cancel(mission_id, run, bound, resumed=True)
        if mission["state"] == mission_record.STATE_AUTHORIZED:
            achieved = (mission_record.CANCEL_BEFORE_INTENT
                        if run is None or run["intent"] is None
                        else mission_record.CANCEL_AFTER_INTENT_TARGET_UNKNOWN)
        elif mission["state"] == mission_record.STATE_RUNNING:
            achieved = (mission_record.CANCEL_AFTER_TARGET_TERMINATED
                        if self._observed_stopped(mission_id, run)
                        else mission_record.CANCEL_AFTER_OBSERVED_RUNNING)
        else:
            _refuse(PROBLEM_WRONG_STATE, "mission %s is %s; there is nothing to"
                    " cancel" % (mission_id, mission["state"]))
        self._record(self._missions.record_cancel, mission_id, achieved, bound,
                     self._context)
        run = self._view(mission_id)["record"]["run"]
        return self._complete_cancel(mission_id, run, bound, resumed=False)

    def _observed_stopped(self, mission_id, run):
        bindings, _, _, raw = self._observe(run["intent"]["workspace_realpath"])
        target = bindings["target_task"]
        return target["status"] == evidence_module.BINDING_EXACT and (
            target["task_id"] == run["receipt"]["task_id"]
        ) and names_this_run(raw, mission_id, run["intent"]) and (
            target["task_status"] in broker_module._TARGET_TERMINAL_STATUSES)

    def _complete_cancel(self, mission_id, run, bound, resumed):
        achieved = run["cancel"]["achieved"]
        unproven = mission_record.CANCEL_QUIESCENCE_UNPROVEN
        if achieved == mission_record.CANCEL_BEFORE_INTENT:
            control, quiescence = (
                "not_applicable", mission_record.CANCEL_QUIESCENCE_NOTHING_STARTED)
        elif resumed:
            control, quiescence = "interrupted_not_resignalled", unproven
        elif achieved == mission_record.CANCEL_AFTER_TARGET_TERMINATED:
            control, quiescence = (
                "not_applicable",
                mission_record.CANCEL_QUIESCENCE_TASK_OBSERVED_STOPPED)
        else:
            control, quiescence = self._control_owned_group(run["receipt"])
        self._record(self._missions.complete_cancel, mission_id, control,
                     quiescence, bound, self._context)
        result = self.status(mission_id)
        result.update({
            "achieved": achieved, "control": control, "quiescence": quiescence,
            "hold": quiescence == unproven,
            "di_records": "CANCELLED: every later DI write is refused",
            "external_quiescence_claimed": quiescence != unproven,
        })
        return result

    def _control_owned_group(self, receipt):
        """SIGKILL reaping ONLY over a group the owner ledger records."""
        unproven = mission_record.CANCEL_QUIESCENCE_UNPROVEN
        group = receipt["owned_process_group"] if receipt else None
        if group is None:
            return "unavailable_no_owned_group", unproven
        if group == os.getpgrp() or group not in self._ownership.owned_groups(
            self._owner_directory
        ):
            return "refused_ownership_unverified", unproven
        verdict, _ = self._ownership.reap_owned(group, self._owner_directory)
        if verdict == ownership_module.REAPED:
            return "owned_group_reaped", (
                mission_record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED)
        if verdict == ownership_module.ALREADY_GONE:
            return "owned_group_already_gone", (
                mission_record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED)
        return "failed_group_still_alive", unproven
