"""Focused tests for Task 8 slice S-IV: the engineering ENGAGEMENT of a
Mission — the dispatch bootstrap (``mission_control.engineering``), the
effect-boundary Mission gate (``mission_control.gate``), the hard
missing-dependency gate (``mission_control.integration``), the canonical
ENGAGEMENT START of the Supervisor's start-claim decision
(``open_engagement_start`` / ``settle_engagement_start`` /
``observe_engagement_stop`` fused to the production bridge's real start
boundaries) and their wiring into the Broker, the Runtime, the status
read and the Grok relay.

EVIDENCE CLASSES, stated once:

- ``DependencyGate*``: the REAL production predicate (no patch): while
  slice S-V's guards are absent every entry refuses
  ``mission_dependency_missing`` with zero effects (R-01 preserved
  exactly as S-III for a direct ``perform`` with an authentic token).
- Every other class patches the two guard-presence predicates and wires
  an in-memory control read on the service (``WiredService``): that is
  S-IV UNIT evidence of the gated path, never integrated acceptance,
  which only the real S-V guards can provide.

Real stores throughout: a real Mission store and service, a real
workflow store, the real Broker with the production bridge
(``dispatch.production_spawn`` → ``herdr.orchestrator.execute_spawn_request``
→ the guarded control plane) over CONTROLLED engine doubles at the
engine's own ``spawn_child`` / ``start`` / ``dispatch_task`` seams, and
the real Runtime pass. Effect counts are read from disk and from the
engine doubles.
"""

import copy
import json
import os
import stat
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from herdr.control_plane import HerdrControlPlane  # noqa: E402
from mission import decision as mission_decision  # noqa: E402
from mission import progress as mission_progress  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import state as mission_state  # noqa: E402
from mission import state_service as mission_state_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_control import engineering as engineering_module  # noqa: E402
from mission_control import gate as gate_module  # noqa: E402
from mission_control import integration  # noqa: E402
from mission_control import status as status_module  # noqa: E402
from target_runtime import broker as broker_module  # noqa: E402
from target_runtime import capability as capability_module  # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from target_runtime import runtime as runtime_module  # noqa: E402
from target_runtime import workspace_ownership as ws_module  # noqa: E402
from workflow_authority import record as wa_record  # noqa: E402
from workflow_authority import store as wa_store  # noqa: E402

from test_target_runtime import (  # noqa: E402
    CANONICAL_URL, NOW, FakeRoleTurnResult, RuntimeCase,
    assert_tree_unchanged, real_shaped_spawn_result, tree_snapshot,
)

MISSION_CORE = wa_record.APPROVAL_KIND_MISSION_CORE
ENGAGEMENT = wa_record.MISSION_ENGAGEMENT_KEY
LINK = wa_record.MISSION_AUTHORITY_KEY
HANDOFF_REVISION = engineering_module.HANDOFF_REVISION
RUNTIME_POINT = dispatch_module.START_POINT_RUNTIME
TASK_POINT = dispatch_module.START_POINT_TASK
WORKSPACE_ID = "ws-started-1"
AGENTS = {"supervisor": "sup-1", "lead": "lead-1", "pod": "pod-1"}
AGENT_NAMES = sorted(AGENTS.values())


def obs(start):
    """The latest canonical stop observation of a start, or None."""
    return mission_state.latest_stop_observation(start)


class Clock(object):
    def __init__(self, start=NOW):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


def contract(**overrides):
    """A proof contract carrying the MANDATORY integration obligations
    (R2-12) plus the delivery obligation, one readiness resource and a
    two-attempt continuation budget."""
    keys = list(engineering_module.MANDATORY_REQUIREMENT_KEYS) + [
        engineering_module.DELIVERY_DECISION_REQUIREMENT_KEY,
        engineering_module.DELIVERY_REQUIREMENT_KEY]
    base = {
        "requirements": [{
            "key": key,
            "description": "obligation %s" % key,
            "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
            "required_artifact_keys": [],
            "max_evidence_age_seconds": 3600,
        } for key in keys],
        "required_artifacts": [],
        "required_dependencies": [],
        "required_resource_readiness": [
            {"resource_key": "build_host", "max_age_seconds": 100000},
        ],
        "degradation_policy": {"permitted_blocker_keys": []},
        "continuation_budget": {"max_attempts": 2, "max_checkpoints": 8},
    }
    base.update(overrides)
    return base


def proposal(baseline_sha, **overrides):
    base = {
        "objective": "Resolve the defect in the target",
        "target_context": "the target repository only",
        "repository_url": CANONICAL_URL,
        "requested_scope": "the readiness probe and its tests",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": mission_record.DELIVERY_TARGET_GITHUB_PR,
        "baseline": {"ref": "refs/heads/main", "commit_sha": baseline_sha},
        "proof_contract": contract(),
    }
    base.update(overrides)
    return base


CONTROL_CONTEXT = mission_record.AuthenticatedContext(
    transport="local",
    principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
    principal_ref="uid:501")


class WiredService(mission_service.MissionService):
    """The production service, unchanged in behaviour. Slice S-V: the
    canonical controls are driven through the REAL operations (a reserved
    control id, the exact current sequence, sufficient provenance) by the
    one-argument conveniences below, and the control READ records which
    Missions the gate and the bootstrap consulted."""

    def __init__(self, store, clock):
        super(WiredService, self).__init__(store, clock)
        self.reads = []

    def mission_controls(self, mission_id):
        self.reads.append(mission_id)
        return super(WiredService, self).mission_controls(mission_id)

    def _at_current_sequence(self, mission_id, call):
        """A concurrent Broker write may advance the sequence between the
        read and the operation (exactly what a human client would see):
        re-read and retry a stale sequence, bounded; every other refusal
        propagates."""
        for _ in range(8):
            sequence = self.get_state(mission_id)["sequence"]
            try:
                return call(sequence)
            except mission_record.MissionError as exc:
                if exc.problem != mission_state_service.PROBLEM_STALE_SEQUENCE:
                    raise
        raise AssertionError("the sequence kept moving under the control")

    def request_hold(self, mission_id, reason="test hold"):
        operation_id = self.mint_control_operation_id(CONTROL_CONTEXT)
        return self._at_current_sequence(mission_id, lambda sequence: super(
            WiredService, self).request_hold(mission_id, operation_id, sequence,
                                             reason, CONTROL_CONTEXT))

    def release_hold(self, mission_id):
        operation_id = self.mint_control_operation_id(CONTROL_CONTEXT)
        return self._at_current_sequence(mission_id, lambda sequence: self.lift_hold(
            mission_id, operation_id, sequence, CONTROL_CONTEXT))

    def request_cancel(self, mission_id, reason="test cancel"):
        operation_id = self.mint_cancel_operation_id(
            mission_id, mission_state.OPERATION_REQUEST_CANCEL, CONTROL_CONTEXT)
        return self._at_current_sequence(mission_id, lambda sequence: super(
            WiredService, self).request_cancel(mission_id, operation_id, sequence,
                                               reason, CONTROL_CONTEXT))

    def confirm_cancel(self, mission_id, detail="every start's stop observed absent"):
        operation_id = self.mint_cancel_operation_id(
            mission_id, mission_state.OPERATION_CONFIRM_CANCEL, CONTROL_CONTEXT)
        return self._at_current_sequence(mission_id, lambda sequence: super(
            WiredService, self).confirm_cancel(mission_id, operation_id, sequence,
                                               detail, CONTROL_CONTEXT))


class Engine(object):
    """The controlled engine behind the REAL bridge: records every start
    and every task hand-over, blocks or injects a Mission write at the
    exact point a test names, and maintains the live workspace listing
    the ownership proof reads."""

    def __init__(self, case):
        self.case = case
        self.reset()

    def reset(self):
        self.starts = []
        self.tasks = []
        self.before_start = None      # callable injected after admission, before the start
        self.before_task = None
        self.start_raises = None
        self.block_start = None       # (started_event, proceed_event)
        self.live = []
        self.live_error = None
        self.close_calls = []
        self.close_error = None
        self.close_leaves_visible = False

    # -- the engine seams (patched onto HerdrControlPlane) ---------------

    def spawn_child(self, plane, parent_repo, target_repo, **kwargs):
        runtime = plane.start(target_repo)
        task = plane.dispatch_task(target_repo, kwargs["task"])
        return real_shaped_spawn_result(str(target_repo), task["id"],
                                        str(parent_repo))

    def start(self, plane, repo, **kwargs):
        if self.before_start is not None:
            self.before_start()
        if self.block_start is not None:
            started, proceed = self.block_start
            started.set()
            self.case.assertTrue(proceed.wait(20), "the blocked start was never released")
        self.starts.append(str(repo))
        if self.start_raises is not None:
            raise self.start_raises
        self.live.append({"workspace_id": WORKSPACE_ID,
                          "agent_names": list(AGENT_NAMES)})
        return {"workspace_id": WORKSPACE_ID, "agents": dict(AGENTS),
                "root_pane": "%1"}

    def dispatch_task(self, plane, repo, text, **kwargs):
        if self.before_task is not None:
            self.before_task()
        self.tasks.append(text)
        return {"id": "task-started-1", "status": "ACTIVE"}

    # -- the ownership seams --------------------------------------------

    def live_listing(self):
        if self.live_error is not None:
            raise self.live_error
        return [dict(w) for w in self.live]

    def close_workspace(self, workspace_id):
        self.close_calls.append(workspace_id)
        if self.close_error is not None:
            raise self.close_error
        if not self.close_leaves_visible:
            self.live = [w for w in self.live if w["workspace_id"] != workspace_id]
        return True


class EngagementCase(RuntimeCase):
    """A real Mission store + WIRED service (unit evidence), the real
    gate, a gated Broker over the real bridge with engine doubles, and
    the real bootstrap."""

    def setUp(self):
        super(EngagementCase, self).setUp()
        self.clock = Clock()
        self.mission_dir = os.path.join(self.base, "mission")
        self.mstore = mission_store.MissionStore(self.mission_dir)
        self.service = WiredService(self.mstore, self.clock)
        self.context = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501")
        self.gate = gate_module.MissionEffectGate(
            self.service, gate_module.local_process_context("dirun-test"))
        self.engine = Engine(self)
        for name in ("spawn_child", "start", "dispatch_task"):
            patcher = mock.patch.object(HerdrControlPlane, name,
                                        self._engine_seam(name))
            patcher.start()
            self.addCleanup(patcher.stop)
        # Slice S-V: the REAL predicate is true for the real wired
        # service; nothing is patched.
        assert integration.required_guards_present(self.service)
        self.broker = self.gated_broker()
        self.control_layer = engineering_module.MissionControl(
            self.service, self.store_dir, self.control)

    def _engine_seam(self, name):
        engine = self.engine

        def seam(plane, *args, **kwargs):
            return getattr(engine, name)(plane, *args, **kwargs)
        return seam

    def gated_broker(self, close=True, gate=None):
        return broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport,
            workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn,
            claude_config_path=self.claude_config,
            spawn_fn=self.mission_spawn,
            clock=lambda: self.clock(),
            observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            workspace_close_fn=self.engine.close_workspace if close else None,
            live_workspaces_fn=self.engine.live_listing if close else None,
            mission_gate=self.gate if gate is None else gate,
        )

    def mission_spawn(self, parent_repo, request, start_guard=None):
        """The production bridge, exactly as the CLI wires it."""
        self.spawn_requests.append((parent_repo, dict(request)))
        return dispatch_module.production_spawn(parent_repo, request,
                                                start_guard=start_guard)

    # -- Mission fixtures -------------------------------------------------

    def propose(self, context=None, **overrides):
        context = context or self.context
        request_id = self.service.mint_request_id(context)
        return self.service.propose(request_id, proposal(self.baseline, **overrides),
                                    context)["mission_id"]

    def decide(self, mission_id, decision, revision=None, context=None,
               expires_at=None):
        context = context or self.context
        record = self.service.get(mission_id)["record"]
        revision = record["current_revision"] if revision is None else revision
        current = record["revisions"][-1]["proposal"]
        decision_id = self.service.mint_decision_id(context)
        envelope = mission_decision.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=decision, received_at=self.clock(),
            approved_action_scope=(current["requested_action_scope"]
                                   if decision == mission_decision.DECISION_APPROVE
                                   else None),
            approved_delivery_targets=([current["requested_delivery_target"]]
                                       if decision == mission_decision.DECISION_APPROVE
                                       else None),
            expires_at=expires_at)
        return self.service.apply_human_decision(envelope)

    def approve(self, mission_id, **kwargs):
        return self.decide(mission_id, mission_decision.DECISION_APPROVE, **kwargs)

    def edit(self, mission_id, **overrides):
        record = self.service.get(mission_id)["record"]
        decision_id = self.service.mint_decision_id(self.context)
        overrides.setdefault("objective", "Resolve the defect, revised")
        return self.service.edit(mission_id, record["current_revision"],
                                 proposal(self.baseline, **overrides), decision_id,
                                 self.context)

    def op(self, name, mission_id, *args):
        operation_id = self.service.mint_state_operation_id(self.context)
        sequence = self.service.get_state(mission_id)["sequence"]
        return getattr(self.service, name)(mission_id, operation_id, sequence,
                                           *args, context=self.context)

    def ready_mission(self, readiness=True, **proposal_overrides):
        mission_id = self.propose(**proposal_overrides)
        self.approve(mission_id)
        self.op("activate_proof_contract", mission_id)
        if readiness:
            self.op("observe_resource_readiness", mission_id, "build_host",
                    mission_state.READINESS_READY, self.clock())
        return mission_id

    def bootstrap(self, mission_id, layer=None):
        layer = layer or self.control_layer
        return layer.dispatch(mission_id, self.context)

    def workflow_row(self, mission_id):
        result = self.bootstrap(mission_id)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["idempotent"])
        return result["workflow_id"]

    # -- Broker driving ---------------------------------------------------

    def act(self, workflow_id, action, broker=None):
        broker = broker or self.broker
        token = capability_module.mint(self.store_dir, workflow_id, action,
                                       HANDOFF_REVISION, self.clock())
        self.presented_capability = token
        return broker.perform(workflow_id, action, HANDOFF_REVISION,
                              capability=token)

    def validated(self, mission_id):
        workflow_id = self.workflow_row(mission_id)
        for action in (broker_module.ACTION_MATERIALIZE,
                       broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF):
            outcome = self.act(workflow_id, action)
            self.assertTrue(outcome.ok, (action, outcome.problem, outcome.detail))
        return workflow_id

    def dispatched(self, mission_id):
        workflow_id = self.validated(mission_id)
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        return workflow_id

    # -- readers ------------------------------------------------------------

    def record(self, workflow_id):
        return wa_store.WorkflowStore(self.store_dir).load()["workflows"][workflow_id]

    def rows(self):
        path = os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME)
        if not os.path.exists(path):
            return {}
        return wa_store.WorkflowStore(self.store_dir).load()["workflows"]

    def starts(self, mission_id):
        state = self.service.get_state(mission_id)["record"]
        return mission_state.engagement_starts_of(state or {})

    def engagements(self, mission_id):
        state = self.service.get_state(mission_id)["record"]
        return mission_state.engagements_of(state or {})

    def ledger_kinds(self, mission_id):
        state = self.service.get_state(mission_id)["record"]
        return [op["kind"] for op in (state or {"applied_operations": []})[
            "applied_operations"]]

    def cursor(self, mission_id):
        return self.service.get_journal(mission_id)["cursor"]

    def receipts(self, workflow_id, marker):
        return [r["bounded_summary"] for r in self.record(workflow_id)["receipts"]
                if r["bounded_summary"].startswith(marker)]

    def consumed(self, token):
        entry = self.capability_entries()[token]
        return entry["consumed_at"] is not None

    def mission_bytes(self):
        with open(self.mstore.path, "rb") as handle:
            return handle.read()


# ====================================================================
# A. The hard missing-dependency gate: the REAL predicate, no patch
# ====================================================================


class DependencyGateTests(RuntimeCase):
    """Slice S-V: the real ``required_guards_present`` is TRUE in this
    integrated candidate — derived from the control record and retention
    implementations actually present and wired on the real service — and
    FALSE again the moment either guard is absent or an instance is
    unwired. With the gate enabled, a Mission-origin record whose Mission
    the store does not know is refused TERMINALLY (unknown Mission) with
    no spawn, no turn and no workspace effect."""

    def setUp(self):
        super(DependencyGateTests, self).setUp()
        self.clock = Clock()
        self.mission_dir = os.path.join(self.base, "mission")
        self.service = mission_service.MissionService(
            mission_store.MissionStore(self.mission_dir), self.clock)
        self.context = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:501")
        self.gate = gate_module.MissionEffectGate(
            self.service, gate_module.local_process_context("dirun-test"))

    def stored_record(self, workflow_id):
        return wa_store.WorkflowStore(self.store_dir).load()["workflows"][workflow_id]

    def test_A1_predicate_is_derived_and_true_only_with_both_guards(self):
        # The REAL predicate, no patch: true for the module definitions
        # and for the real service instance.
        self.assertTrue(integration.required_guards_present())
        self.assertTrue(integration.required_guards_present(self.service))
        self.assertEqual(integration.missing_guards(), ())
        self.assertEqual(integration.missing_guards(self.service), ())
        self.assertTrue(integration.control_record_defined())
        self.assertTrue(integration.retention_defined())
        self.assertTrue(self.gate.enabled())
        # Behavioural pin of the derivation: each guard is read from the
        # names its owning module defines and from the operations the
        # service CLASS defines; removing any one of them makes exactly
        # that guard missing again and disables the gate.
        operations = integration.mission_state_service.MissionStateOperations
        absent_control = (
            (integration.mission_state, "CONTROL_KEY"),
            (integration.mission_state, "CONTROL_RECORD_KEYS"),
            (operations, "confirm_cancel"),
            (operations, "lift_hold"),
        )
        for owner, name in absent_control:
            with mock.patch.object(owner, name, None):
                self.assertFalse(integration.control_record_defined(), name)
                self.assertEqual(integration.missing_guards(self.service),
                                 (integration.GUARD_CONTROL_RECORD,), name)
                self.assertFalse(self.gate.enabled(), name)
        absent_retention = (
            (integration.workflow_record, "RETENTION_KEYS"),
            (integration.workflow_store, "retention_protects"),
        )
        for owner, name in absent_retention:
            with mock.patch.object(owner, name, None):
                self.assertFalse(integration.retention_defined(), name)
                self.assertEqual(integration.missing_guards(self.service),
                                 (integration.GUARD_RETENTION,), name)
                self.assertFalse(self.gate.enabled(), name)
        self.assertTrue(self.gate.enabled())
        # Instance wiring is part of the derivation: an instance that
        # lacks the control READ (or an operation) is unwired, its gate is
        # disabled and admits nothing (a DEPENDENCY refusal, zero effects).
        from test_workflow_authority import mission_core_record

        class Unwired(mission_service.MissionService):
            mission_controls = None

        unwired = Unwired(mission_store.MissionStore(self.mission_dir), self.clock)
        self.assertEqual(integration.missing_guards(unwired),
                         (integration.GUARD_CONTROL_RECORD,))
        self.assertFalse(integration.required_guards_present(unwired))
        gate = gate_module.MissionEffectGate(
            unwired, gate_module.local_process_context("dirun-test"))
        self.assertFalse(gate.enabled())
        admission = gate.admit(mission_core_record(self.authorized_record("wf-mission")),
                               gate_module.BOUNDARY_ACTION_ADMISSION)
        self.assertFalse(admission.ok)
        self.assertEqual(admission.problem, integration.PROBLEM_DEPENDENCY_MISSING)
        self.assertEqual(admission.classification, gate_module.CLASS_DEPENDENCY)
        self.assertFalse(os.path.exists(unwired._store.path))

    def test_A2_bootstrap_refuses_an_unknown_mission_with_zero_effects(self):
        layer = engineering_module.MissionControl(self.service, self.store_dir,
                                                  self.control)
        result = layer.dispatch("mn-" + "0" * 32, self.context)
        self.assertFalse(result["ok"])
        self.assertEqual(result["problem"], engineering_module.PROBLEM_UNKNOWN_MISSION)
        self.assertEqual(engineering_module.effects_summary(result), "proven_zero")
        self.assertEqual(self.store_bytes(), None)
        self.assertEqual(self.capability_entries(), {})
        self.assertEqual(self.spawn_requests, [])

    def test_A3_gated_broker_blocks_an_unknown_mission_after_consuming_the_token_once(self):
        # R-01 exactly as S-III: an authentic token is consumed ONCE; then
        # the enabled gate refuses TERMINALLY (the Mission the record
        # binds does not exist in the Mission store): one block receipt
        # naming the problem, the record BLOCKED, no spawn, no turn, no
        # workspace effect, and the Mission store untouched.
        from test_workflow_authority import mission_core_record
        entry = mission_core_record(self.authorized_record("wf-mission"))
        self.put_record(entry)
        broker = broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport, workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn, claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn, clock=lambda: NOW, observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            mission_gate=self.gate)
        self.mint_live_decoy()
        self.broker = broker
        control_before = tree_snapshot(self.control)
        workspaces_before = tree_snapshot(self.workspaces)
        # The phase-appropriate first step reaches the gate: TERMINAL
        # refusal, one block receipt, the record BLOCKED.
        outcome = self.perform("wf-mission", broker_module.ACTION_MATERIALIZE)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_UNKNOWN_MISSION)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertTrue(self.capability_entries()[
            self.presented_capability]["consumed_at"] is not None)
        record = self.stored_record("wf-mission")
        self.assertEqual(record["phase"], wa_record.PHASE_BLOCKED)
        receipts = [r for r in record["receipts"] if r["bounded_summary"].startswith(
            broker_module.MISSION_BLOCK_RECEIPT_MARKER)]
        self.assertEqual(len(receipts), 1)
        self.assertIn(gate_module.PROBLEM_UNKNOWN_MISSION, receipts[0]["bounded_summary"])
        # BLOCKED is terminal: every action now refuses on the phase, each
        # consuming its token, adding no receipt and changing no phase.
        blocked_bytes = self.store_bytes()
        for action in broker_module.BROKER_ACTIONS:
            outcome = self.perform("wf-mission", action)
            self.assertTrue(self.capability_entries()[
                self.presented_capability]["consumed_at"] is not None)
            if action == broker_module.ACTION_RELEASE:
                # Terminal cleanup runs on a BLOCKED record and reaches
                # the gate again: the same terminal refusal, one more block
                # receipt, the phase kept, nothing released (no lease).
                self.assertEqual(outcome.problem, gate_module.PROBLEM_UNKNOWN_MISSION)
                self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
                record = self.stored_record("wf-mission")
                self.assertEqual(record["phase"], wa_record.PHASE_BLOCKED)
                self.assertEqual(len([
                    r for r in record["receipts"] if r["bounded_summary"].startswith(
                        broker_module.MISSION_BLOCK_RECEIPT_MARKER)]), 2)
                self.assertIsNone(record["workspace_lease"])
                blocked_bytes = self.store_bytes()
                continue
            self.assertFalse(outcome.ok, action)
            self.assertEqual(outcome.problem, broker_module.PROBLEM_WRONG_PHASE, action)
            self.assertEqual(self.store_bytes(), blocked_bytes, action)
        assert_tree_unchanged(self, self.control, control_before, "control repository")
        assert_tree_unchanged(self, self.workspaces, workspaces_before,
                              "managed workspace root")
        self.assertEqual(self.spawn_requests, [])
        self.assertEqual(self.role_turn.calls, [])
        self.assertFalse(os.path.exists(self.service._store.path))

    def test_A4_runtime_pass_claims_the_record_and_blocks_it_terminally(self):
        # With the real predicate true the Mission-origin record IS
        # claimable; the pass's pre-mint admission refuses terminally
        # (no capability is minted for a refused record) and nothing runs.
        from test_workflow_authority import mission_core_record
        self.put_record(mission_core_record(self.authorized_record("wf-mission")))
        broker = broker_module.TargetBroker(
            store_directory=self.store_dir,
            control_repository_realpath=self.control,
            transport=self.transport, workspaces_root=self.workspaces,
            role_turn_fn=self.role_turn, claude_config_path=self.claude_config,
            spawn_fn=self.spawn_fn, clock=lambda: NOW, observer_fn=self.observer,
            spawn_records_fn=self.spawn_records,
            readiness_probe_fn=lambda path: self.readiness_probe(path),
            mission_gate=self.gate)
        self.assertEqual(runtime_module.claimable_workflows(
            self.store_dir, mission_gate=self.gate), [("wf-mission", 2)])
        processed = runtime_module.process_once(broker)
        self.assertEqual(sorted(processed), ["wf-mission"])
        results = processed["wf-mission"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1].problem, gate_module.PROBLEM_UNKNOWN_MISSION)
        self.assertEqual(results[0][1].outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        record = self.stored_record("wf-mission")
        self.assertEqual(record["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(len([r for r in record["receipts"] if r["bounded_summary"].startswith(
            broker_module.MISSION_BLOCK_RECEIPT_MARKER)]), 1)
        self.assertEqual(self.capability_entries(), {})
        self.assertEqual(self.role_turn.calls, [])
        self.assertEqual(self.spawn_requests, [])
        # BLOCKED is terminal for the pass: nothing is claimable any more.
        self.assertEqual(runtime_module.claimable_workflows(
            self.store_dir, mission_gate=self.gate), [])


# ====================================================================
# B. The bootstrap (unit evidence: guards patched, real stores)
# ====================================================================


class BootstrapTests(EngagementCase):

    def test_B1_one_row_with_the_canonical_fence_and_no_capability(self):
        mission_id = self.ready_mission()
        result = self.bootstrap(mission_id)
        self.assertTrue(result["ok"], result)
        self.assertEqual(engineering_module.effects_summary(result), "retained")
        self.assertEqual(result["effects"], {"activation": "none", "fence": "written",
                                             "row": "written"})
        workflow_id = result["workflow_id"]
        self.assertEqual(workflow_id, engineering_module.deterministic_workflow_id(
            mission_id, self.service.get_state(mission_id)["contract"]["activation_id"]))
        entry = self.record(workflow_id)
        self.assertTrue(wa_record.is_mission_core_kind(entry))
        self.assertEqual(entry["phase"], wa_record.PHASE_AUTHORIZED)
        self.assertEqual(entry["approved_baseline"]["commit_sha"], self.baseline)
        self.assertEqual(entry[LINK]["mission_id"], mission_id)
        self.assertEqual(entry[ENGAGEMENT]["engagement_sequence"], 1)
        engagements = self.engagements(mission_id)
        self.assertEqual(len(engagements), 1)
        self.assertEqual(engagements[0]["workflow_id"], workflow_id)
        self.assertEqual(engagements[0]["kind"], mission_state.ENGAGEMENT_KIND_INITIAL)
        self.assertEqual(entry[ENGAGEMENT]["engagement_id"],
                         engagements[0]["engagement_id"])
        self.assertEqual(mission_progress.consumed_attempts(
            self.service.get_state(mission_id)["record"]), 0)
        self.assertEqual(self.capability_entries(), {})
        self.assertEqual(self.spawn_requests, [])
        self.assertEqual(entry["handoff"]["text"],
                         engineering_module.handoff_text(
                             self.service.get(mission_id)["record"]["revisions"][-1][
                                 "proposal"]))

    def test_B2_no_dispatch_before_valid_current_authority(self):
        connector = mission_record.AuthenticatedContext(
            transport="grok_mcp",
            principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
            principal_ref="1")
        cases = {}
        unapproved = self.propose()
        cases["unapproved"] = (unapproved, engineering_module.PROBLEM_NOT_AUTHORIZED)
        denied = self.propose()
        self.decide(denied, mission_decision.DECISION_DENY)
        cases["denied"] = (denied, engineering_module.PROBLEM_NOT_AUTHORIZED)
        edited = self.propose()
        self.approve(edited)
        self.edit(edited)
        cases["edited revision"] = (edited, engineering_module.PROBLEM_NOT_AUTHORIZED)
        provenance = self.propose(context=connector)
        self.approve(provenance, context=connector)
        cases["connector-credential provenance"] = (
            provenance, "mission_control_provenance_insufficient")
        incomplete = self.propose(proof_contract=contract(requirements=[
            r for r in contract()["requirements"] if r["key"] != "reviewer_approve"]))
        self.approve(incomplete)
        cases["missing mandatory obligation"] = (
            incomplete, engineering_module.PROBLEM_CONTRACT_INCOMPLETE)
        stale = self.propose()
        self.approve(stale)
        cases["stale readiness"] = (stale, engineering_module.PROBLEM_READINESS_STALE)
        no_baseline = self.propose(baseline=None)
        self.approve(no_baseline)
        cases["no baseline"] = (no_baseline, engineering_module.PROBLEM_BASELINE_MISSING)
        for label, (mission_id, problem) in cases.items():
            before = self.mission_bytes()
            result = self.bootstrap(mission_id)
            self.assertFalse(result["ok"], label)
            self.assertEqual(result["problem"], problem, label)
            self.assertEqual(result["effects"]["fence"], "none", label)
            self.assertEqual(result["effects"]["row"], "none", label)
            if label == "stale readiness":
                # The only documented Mission store change: the contract
                # activation (a canonical operation), before readiness
                # refuses; the fence is never reserved.
                self.assertEqual(result["effects"]["activation"], "written")
                self.assertEqual(self.ledger_kinds(mission_id),
                                 [mission_state.OPERATION_ACTIVATE_CONTRACT])
            else:
                self.assertEqual(self.mission_bytes(), before, label)
            self.assertEqual(self.engagements(mission_id), [], label)
        # Expired: approved with an expiry the clock then passes.
        expired = self.propose()
        self.approve(expired, expires_at=self.clock() + 50)
        self.clock.advance(60)
        result = self.bootstrap(expired)
        self.assertFalse(result["ok"])
        # The ONE validator no longer accepts the expired authorization,
        # so the Mission has no live authorization at all.
        self.assertEqual(result["problem"], engineering_module.PROBLEM_NO_LIVE_AUTHORIZATION)
        self.assertEqual(engineering_module.effects_summary(result), "proven_zero")
        self.assertEqual(self.rows(), {})
        self.assertEqual(self.capability_entries(), {})

    def test_B3_repeat_reconnect_restart_and_concurrent_yield_one_row(self):
        mission_id = self.ready_mission()
        first = self.bootstrap(mission_id)
        self.assertTrue(first["ok"])
        for _ in range(3):
            again = self.bootstrap(mission_id)
            self.assertTrue(again["ok"])
            self.assertTrue(again["idempotent"])
            self.assertEqual(again["workflow_id"], first["workflow_id"])
            self.assertEqual(again["engagement_id"], first["engagement_id"])
        rebuilt = engineering_module.MissionControl(self.service, self.store_dir,
                                                    self.control)
        restart = self.bootstrap(mission_id, layer=rebuilt)
        self.assertTrue(restart["idempotent"])
        fresh_service = WiredService(mission_store.MissionStore(self.mission_dir),
                                     self.clock)
        restarted = engineering_module.MissionControl(fresh_service, self.store_dir,
                                                      self.control)
        self.assertTrue(restarted.dispatch(mission_id, self.context)["idempotent"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.engagements(mission_id)), 1)
        # Concurrent: a second Mission, four bootstraps at once.
        other = self.ready_mission()
        results = []
        lock = threading.Lock()

        def run():
            # Slice S-V: the real predicate is true for every real wired
            # service instance; nothing is patched (a per-thread patch of
            # a module attribute would race its own restoration).
            layer = engineering_module.MissionControl(
                WiredService(mission_store.MissionStore(self.mission_dir), self.clock),
                self.store_dir, self.control)
            result = layer.dispatch(other, self.context)
            with lock:
                results.append(result)
        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(sum(1 for r in results if not r["idempotent"]), 1, results)
        self.assertEqual(len(set(r["workflow_id"] for r in results)), 1)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.engagements(other)), 1)

    def test_B4_crash_boundaries_retain_uncertainty_and_recreate_nothing(self):
        # Crash after the fence, before the row save.
        mission_id = self.ready_mission()
        real_save = wa_store.WorkflowStore.save

        def crash_once(store, document, _seen=[]):
            if not _seen:
                _seen.append(True)
                raise wa_store.StoreError("crash before the row save")
            return real_save(store, document)
        with mock.patch.object(wa_store.WorkflowStore, "save", crash_once):
            crashed = self.bootstrap(mission_id)
        self.assertFalse(crashed["ok"])
        self.assertEqual(crashed["problem"], engineering_module.PROBLEM_WORKFLOW_STORE)
        self.assertEqual(crashed["effects"], {"activation": "none", "fence": "written",
                                              "row": "unknown"})
        self.assertEqual(engineering_module.effects_summary(crashed), "unknown")
        self.assertEqual(len(self.engagements(mission_id)), 1)
        # Retry, single and concurrent: the fence is found, the row is
        # missing, and it is NEVER recreated.
        retried = self.bootstrap(mission_id)
        self.assertFalse(retried["ok"])
        self.assertEqual(retried["problem"], engineering_module.PROBLEM_ENGAGEMENT_UNCERTAIN)
        self.assertEqual(retried["engagement_id"], crashed["engagement_id"])
        self.assertEqual(self.rows(), {})
        self.assertEqual(len(self.engagements(mission_id)), 1)
        # Crash after the row save (the result was lost): the retry is
        # idempotent and reserves nothing more.
        second = self.ready_mission()
        published = self.bootstrap(second)
        self.assertTrue(published["ok"])
        before = self.mission_bytes()
        again = self.bootstrap(second)
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.mission_bytes(), before)
        # Terminal pruning of the row: the fence retains the uncertainty.
        store = wa_store.WorkflowStore(self.store_dir)
        document = store.load()
        del document["workflows"][published["workflow_id"]]
        store.save(document)
        pruned = self.bootstrap(second)
        self.assertEqual(pruned["problem"], engineering_module.PROBLEM_ENGAGEMENT_UNCERTAIN)
        self.assertNotIn(published["workflow_id"], self.rows())

    def test_B5_admission_before_activation_fence_and_publication(self):
        # Held before any mutation: zero mutations.
        held = self.propose()
        self.approve(held)
        self.service.request_hold(held)
        before = self.mission_bytes()
        result = self.bootstrap(held)
        self.assertEqual(result["problem"], gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(result["admission_point"], engineering_module.ADMISSION_ACTIVATION)
        self.assertEqual(engineering_module.effects_summary(result), "proven_zero")
        self.assertEqual(self.mission_bytes(), before)
        # Cancelled before the fence (the contract was activated by hand).
        cancelled = self.ready_mission()
        self.service.request_cancel(cancelled)
        result = self.bootstrap(cancelled)
        self.assertEqual(result["problem"], gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(result["admission_point"], engineering_module.ADMISSION_FENCE)
        self.assertEqual(self.engagements(cancelled), [])
        self.assertEqual(self.rows(), {})
        # An EDIT landing after the fence and before the publication —
        # the barrier is JUST BEFORE the publication section acquires the
        # Mission lock (a write inside the section is impossible: the
        # lock excludes it; the deadlock evidence of a write injected
        # inside it is kept in S4-B5-deadlock-traceback.log): no stale
        # row, the fence keeps its uncertainty.
        edited = self.ready_mission()
        real_lock = self.service.store_lock
        state = {"armed": True}

        def edit_then_lock():
            if state["armed"]:
                state["armed"] = False
                self.edit(edited)
            return real_lock()
        with mock.patch.object(self.service, "store_lock", edit_then_lock):
            result = self.bootstrap(edited)
        self.assertFalse(result["ok"])
        self.assertEqual(result["admission_point"], engineering_module.ADMISSION_PUBLICATION)
        self.assertEqual(result["problem"], engineering_module.PROBLEM_REVISION_SUPERSEDED)
        self.assertEqual(result["effects"]["fence"], "written")
        self.assertEqual(result["effects"]["row"], "none")
        self.assertEqual(self.rows(), {})
        self.assertEqual(len(self.engagements(edited)), 1)
        # The superseded revision's fence is never published; a fresh
        # approval of the new revision activates a NEW contract with its
        # own fence and its own (different) deterministic workflow id.
        superseded_fence = self.engagements(edited)[0]
        self.approve(edited)
        later = self.bootstrap(edited)
        self.assertTrue(later["ok"], later)
        self.assertNotEqual(later["workflow_id"], superseded_fence["workflow_id"])
        self.assertEqual(sorted(self.rows()), [later["workflow_id"]])
        self.assertEqual(len(self.engagements(edited)), 2)

    def test_B5b_concurrent_writer_waits_only_for_the_short_section(self):
        import contextlib
        import time
        mission_id = self.ready_mission()
        real_lock = self.service.store_lock
        times = {}
        inside = threading.Event()

        @contextlib.contextmanager
        def observed_lock():
            with real_lock():
                times["enter"] = time.monotonic()
                inside.set()
                time.sleep(0.6)   # the publication section, deliberately long
                yield
                times["exit"] = time.monotonic()
        results = []
        with mock.patch.object(self.service, "store_lock", observed_lock):
            worker = threading.Thread(
                target=lambda: results.append(self.bootstrap(mission_id)))
            worker.start()
            self.assertTrue(inside.wait(20), "the publication section was never entered")
            writer_start = time.monotonic()
            self.edit(mission_id)          # blocks only while the section is held
            writer_end = time.monotonic()
            worker.join(30)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"], results[0])
        # The writer started inside the section, finished after it ended,
        # and waited no longer than the section's remainder (plus slack).
        self.assertGreaterEqual(writer_start, times["enter"])
        self.assertGreaterEqual(writer_end, times["exit"])
        self.assertLess(writer_end - times["exit"], 1.0)
        self.assertEqual(self.service.get(mission_id)["record"]["current_revision"], 2)
        self.assertEqual(len(self.rows()), 1)

    def test_B6_source_failures_report_truthful_effects(self):
        mission_id = self.ready_mission()
        # (a) unreadable before anything: proven zero.
        good = self.mission_bytes()
        with open(self.mstore.path, "w") as handle:
            handle.write("{not json")
        result = self.bootstrap(mission_id)
        self.assertEqual(result["problem"], gate_module.PROBLEM_SOURCE_UNAVAILABLE)
        self.assertEqual(engineering_module.effects_summary(result), "proven_zero")
        self.assertEqual(self.rows(), {})
        with open(self.mstore.path, "wb") as handle:
            handle.write(good)
        # (b) failure while the fence is in flight: the fence save outcome
        # is UNKNOWN; the retry recovers from the durable stores.
        repaired = self.ready_mission()
        real_reserve = self.service.reserve_engagement

        def fail_in_flight(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone mid-save")
        with mock.patch.object(self.service, "reserve_engagement", fail_in_flight):
            result = self.bootstrap(repaired)
        self.assertEqual(result["problem"], gate_module.PROBLEM_SOURCE_UNAVAILABLE)
        self.assertEqual(result["effects"], {"activation": "none", "fence": "unknown",
                                             "row": "none"})
        self.assertEqual(engineering_module.effects_summary(result), "unknown")
        self.assertIn("save outcome unknown for: fence", result["detail"])
        self.assertEqual(self.engagements(repaired), [])
        recovered = self.bootstrap(repaired)
        self.assertTrue(recovered["ok"], recovered)
        self.assertFalse(recovered["idempotent"])
        self.assertEqual(len(self.engagements(repaired)), 1)
        # (c) failure after the activation, before the fence: retained.
        third = self.propose()
        self.approve(third)
        real_activate = self.service.activate_proof_contract

        def activate_then_fail(*args, **kwargs):
            outcome = real_activate(*args, **kwargs)
            self.service.get_state = mock.Mock(
                side_effect=mission_store.MissionStoreError("read failed"))
            return outcome
        with mock.patch.object(self.service, "activate_proof_contract",
                               activate_then_fail):
            result = self.bootstrap(third)
        del self.service.get_state
        self.assertEqual(result["problem"], gate_module.PROBLEM_SOURCE_UNAVAILABLE)
        self.assertEqual(result["effects"]["activation"], "written")
        self.assertEqual(result["effects"]["fence"], "none")
        self.assertEqual(engineering_module.effects_summary(result), "retained")
        self.assertIn("durable progress retained: activation", result["detail"])
        self.assertEqual(self.ledger_kinds(third),
                         [mission_state.OPERATION_ACTIVATE_CONTRACT])
        self.op("observe_resource_readiness", third, "build_host",
                mission_state.READINESS_READY, self.clock())
        recovered = self.bootstrap(third)
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(self.ledger_kinds(third).count(
            mission_state.OPERATION_ACTIVATE_CONTRACT), 1)


# ====================================================================
# C. The gated Broker: action admission, blocking steps, R-01
# ====================================================================


class GatedBrokerTests(EngagementCase):

    def test_C1_full_lifecycle_through_the_real_bridge(self):
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(entry["target_engine"]["task_id"], "task-started-1")
        self.assertEqual(self.engine.starts, [entry["workspace_lease"]["path_realpath"]])
        self.assertEqual(self.engine.tasks, [entry["handoff"]["text"]])
        self.assertEqual(len(self.spawn_requests), 1)
        starts = self.starts(mission_id)
        self.assertEqual([s["point"] for s in starts], ["runtime", "task"])
        for start in starts:
            self.assertIsNotNone(start["settlement"])
            self.assertEqual(start["settlement"]["outcome"], "completed")
            self.assertFalse(start["settlement"]["stop_pending"])
            self.assertEqual(start["stop_observations"], [])
        self.assertEqual(starts[0]["settlement"]["identity"],
                         {"workspace_id": WORKSPACE_ID, "agent_names": AGENT_NAMES,
                          "task_id": None})
        self.assertEqual(starts[1]["settlement"]["identity"]["task_id"],
                         "task-started-1")
        self.assertEqual(self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER),
                         [s for s in self.receipts(
                             workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)])
        self.assertEqual(len(self.receipts(
            workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)), 4)
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(self.ledger_kinds(mission_id)[-4:], [
            "open_engagement_start", "settle_engagement_start",
            "open_engagement_start", "settle_engagement_start"])

    def test_C2_hold_and_cancel_at_action_admission_after_one_consumption(self):
        mission_id = self.ready_mission()
        workflow_id = self.workflow_row(mission_id)
        self.service.request_hold(mission_id)
        self.mint_live_decoy()
        outcome = self.act(workflow_id, broker_module.ACTION_MATERIALIZE)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_HELD)
        self.assertTrue(self.consumed(self.presented_capability))
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_AUTHORIZED)
        holds = self.receipts(workflow_id, broker_module.MISSION_HOLD_RECEIPT_MARKER)
        self.assertEqual(len(holds), 1)
        # At most ONE receipt per cause: a second pass adds nothing.
        second = self.act(workflow_id, broker_module.ACTION_MATERIALIZE)
        self.assertEqual(second.problem, gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(len(self.receipts(
            workflow_id, broker_module.MISSION_HOLD_RECEIPT_MARKER)), 1)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.engine.starts, [])
        # Released: admitted normally.
        self.service.release_hold(mission_id)
        self.assertTrue(self.act(workflow_id, broker_module.ACTION_MATERIALIZE).ok)
        # Cancel: terminal, locked BLOCKED, one block receipt.
        self.service.request_cancel(mission_id)
        outcome = self.act(workflow_id, broker_module.ACTION_PREPARE)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(len(self.receipts(
            workflow_id, broker_module.MISSION_BLOCK_RECEIPT_MARKER)), 1)
        self.assertEqual(self.role_turn.calls, [])
        self.assertEqual(self.engine.starts, [])

    def test_C3_mission_change_during_clone_and_each_turn(self):
        # During the clone (a Mission write between the clone and trust
        # establishment): the lease is recorded, trust is NOT established,
        # the record blocks durably; a later pass never re-clones.
        mission_id = self.ready_mission()
        workflow_id = self.workflow_row(mission_id)
        real_clone = self.transport.clone
        calls = {"n": 0}

        def clone_then_edit(*args, **kwargs):
            result = real_clone(*args, **kwargs)
            if calls["n"] == 0:
                calls["n"] += 1
                self.edit(mission_id)
            return result
        with mock.patch.object(self.transport, "clone", clone_then_edit):
            outcome = self.act(workflow_id, broker_module.ACTION_MATERIALIZE)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_REVISION_SUPERSEDED)
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNotNone(entry["workspace_lease"])
        with open(self.claude_config, encoding="utf-8") as handle:
            trust = json.load(handle)["projects"]
        self.assertNotIn(entry["workspace_lease"]["path_realpath"], trust)
        # Each turn: a hold injected INSIDE the handoff-validation turn
        # holds the result (phase preserved, one receipt, no transition);
        # a cancel inside the verification turn blocks every returned
        # result, including the durable verification stop.
        second = self.ready_mission()
        wf2 = self.workflow_row(second)
        self.assertTrue(self.act(wf2, broker_module.ACTION_MATERIALIZE).ok)
        self.assertTrue(self.act(wf2, broker_module.ACTION_PREPARE).ok)
        real_turn = self.role_turn

        class Injecting(object):
            def __init__(self, case, inject_role, inject):
                self.case, self.inject_role, self.inject = case, inject_role, inject

            def __getattr__(self, name):
                return getattr(real_turn, name)

            def __call__(self, role, entry, now, **kwargs):
                result = real_turn(role, entry, now, **kwargs)
                if role == self.inject_role:
                    self.inject()
                return result
        self.broker._role_turn = Injecting(
            self, "handoff_validation", lambda: self.service.request_hold(second))
        handoff_turns_before = len([c for c in self.role_turn.calls
                                    if c[0] == "handoff_validation"])
        outcome = self.act(wf2, broker_module.ACTION_VALIDATE_HANDOFF)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(self.record(wf2)["phase"], wa_record.PHASE_PREPARED)
        # The turn ran exactly once; its result was held, not accepted.
        self.assertEqual(len([c for c in self.role_turn.calls
                              if c[0] == "handoff_validation"]),
                         handoff_turns_before + 1)
        self.service.release_hold(second)
        self.broker._role_turn = real_turn
        self.assertTrue(self.act(wf2, broker_module.ACTION_VALIDATE_HANDOFF).ok)
        self.assertTrue(self.act(wf2, broker_module.ACTION_DISPATCH).ok)
        self.broker._role_turn = Injecting(
            self, "verification", lambda: self.service.request_cancel(second))
        outcome = self.act(wf2, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        entry = self.record(wf2)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(entry["verified_result"])

    def test_C4_every_verification_result_is_gated_including_the_durable_stop(self):
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        # An INCOMPLETE turn during a hold: the durable verification stop
        # is a returned result too — it is HELD, never terminalized.
        self.role_turn.verification_result = FakeRoleTurnResult(
            status="role_turn_failed", outcome=None, reason="crashed",
            turn={"turn_id": "turn-v-fail", "role": "verification",
                  "process_id": 4243})
        real_turn = self.role_turn
        broker = self.broker

        def turn_with_hold(role, entry, now, **kwargs):
            result = real_turn(role, entry, now, **kwargs)
            if role == "verification":
                self.service.request_hold(mission_id)
            return result
        broker._role_turn = turn_with_hold
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_HELD)
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(self.receipts(workflow_id, broker_module.VERIFICATION_BLOCK_MARKER), [])
        # Released: the same failed turn now stops durably.
        self.service.release_hold(mission_id)
        broker._role_turn = real_turn
        outcome = self.act(workflow_id, broker_module.ACTION_VERIFY)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_VERIFICATION_BLOCKED)
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)

    def test_C5_v2_records_are_untouched_by_the_gate(self):
        self.put_record(self.authorized_record("wf-0001"))
        for action in (broker_module.ACTION_MATERIALIZE, broker_module.ACTION_PREPARE,
                       broker_module.ACTION_VALIDATE_HANDOFF,
                       broker_module.ACTION_DISPATCH):
            token = capability_module.mint(self.store_dir, "wf-0001", action, 2,
                                           self.clock())
            outcome = self.broker.perform("wf-0001", action, 2, capability=token)
            self.assertTrue(outcome.ok, (action, outcome.problem, outcome.detail))
        self.assertEqual(len(self.spawn_requests), 1)
        # The v2 path never asks the gate and never opens a start.
        self.assertEqual(self.service.reads, [])
        self.assertEqual(self.engine.starts, [self.record("wf-0001")[
            "workspace_lease"]["path_realpath"]])
        self.assertFalse(os.path.exists(self.mstore.path))


# ====================================================================
# D. The engagement START at the real bridge boundaries
# ====================================================================


class StartClaimTests(EngagementCase):

    def prepared(self):
        mission_id = self.ready_mission()
        workflow_id = self.validated(mission_id)
        return mission_id, workflow_id

    def test_D1_writes_before_the_atomic_admission_yield_zero_invocations(self):
        for label, write in (
            ("edit", lambda m: self.edit(m)),
            ("hold", lambda m: self.service.request_hold(m)),
            ("cancel", lambda m: self.service.request_cancel(m)),
        ):
            mission_id, workflow_id = self.prepared()
            starts_before = len(self.engine.starts)
            # The barrier is BEFORE the atomic admission: an EDIT lands
            # right before the canonical ``open_engagement_start``
            # transaction (after the gate's lock-free pre-check), which
            # refuses it itself; a hold or cancel lands right before the
            # admission's control read — in S-IV that read is the gate's
            # pre-check immediately before the transaction (S-V moves it
            # INTO the transaction; see the evidence's residual list).
            if label == "edit":
                real_open = self.service.open_engagement_start

                def write_then_open(*args, **kwargs):
                    write(mission_id)
                    return real_open(*args, **kwargs)
                patched = mock.patch.object(self.service, "open_engagement_start",
                                            write_then_open)
            else:
                real_gate_open = self.gate.open_start

                def write_then_admit(*args, **kwargs):
                    write(mission_id)
                    return real_gate_open(*args, **kwargs)
                patched = mock.patch.object(self.gate, "open_start", write_then_admit)
            with patched:
                outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            # An EDIT right before the atomic transaction is refused BY the
            # transaction itself (the bound contract is stale); hold and
            # cancel by the gate's pre-check (S-V's control read).
            expected = {"edit": (gate_module.PROBLEM_REVISION_SUPERSEDED,
                                 mission_state_service.PROBLEM_CONTRACT_STALE),
                        "hold": (gate_module.PROBLEM_HOLD_ACTIVE,),
                        "cancel": (gate_module.PROBLEM_CANCEL_REQUESTED,)}[label]
            self.assertIn(outcome.problem, expected, label)
            self.assertEqual(len(self.engine.starts), starts_before, label)
            self.assertEqual(self.engine.tasks, [], label)
            self.assertEqual(self.starts(mission_id), [], label)
            entry = self.record(workflow_id)
            # The dispatch marker was written before the bridge (intent
            # and ambiguity, never permission); the start was refused.
            self.assertEqual(dispatch_module.dispatch_count(entry), 1, label)
            self.assertIsNone(entry["target_engine"], label)
            if label == "hold":
                self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_HELD, label)
                self.assertEqual(entry["phase"], wa_record.PHASE_DISPATCHED, label)
            else:
                self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED, label)
                self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED, label)
            self.assertEqual(self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER),
                             [], label)

    def test_D2_write_after_admission_before_the_call_stops_the_runtime(self):
        mission_id, workflow_id = self.prepared()
        self.engine.before_start = lambda: self.edit(mission_id)
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_REVISION_SUPERSEDED)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertIn("admitted before it", outcome.detail)
        self.assertIn("never handed over", outcome.detail)
        self.assertIn("CONFIRMED (absence observed and recorded canonically)",
                      outcome.detail)
        # Exactly one runtime start, no task hand-over, one owned stop.
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(self.engine.live, [])
        starts = self.starts(mission_id)
        self.assertEqual(len(starts), 1)
        start = starts[0]
        self.assertEqual(start["point"], "runtime")
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIn("revision superseded", start["settlement"]["stop_reason"])
        self.assertEqual(start["settlement"]["identity"]["workspace_id"], WORKSPACE_ID)
        self.assertTrue(obs(start)["absent"])
        self.assertTrue(mission_state.start_stop_confirmed(start))
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(entry["target_engine"])
        self.assertEqual(self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1]
                         .split(" state=")[1].split(" ")[0], "stop:confirmed")
        # The old start never revives: a fresh approval of the new revision
        # and a retry refuse (the engagement requires a stop), no second
        # invocation, no second start record.
        self.approve(mission_id)
        again = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertFalse(again.ok and again.outcome is None)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(len(self.starts(mission_id)), 1)
        # Status reports the three facts separately.
        view = status_module.read_status(self.service, mission_id,
                                         workflow_directory=self.store_dir)["mission"]
        reported = view["engagements"]["starts"]
        self.assertEqual(len(reported), 1)
        self.assertEqual((reported[0]["settled"], reported[0]["observed_outcome"],
                          reported[0]["stop_required"], reported[0]["stop_confirmed"]),
                         (True, "completed", True, True))

    def test_D3_write_during_the_blocked_call_is_not_blocked_and_stops_after(self):
        mission_id, workflow_id = self.prepared()
        started, proceed = threading.Event(), threading.Event()
        self.engine.block_start = (started, proceed)
        results = []
        worker = threading.Thread(target=lambda: results.append(
            self.act(workflow_id, broker_module.ACTION_DISPATCH)))
        worker.start()
        self.assertTrue(started.wait(20), "the start was never reached")
        # The claim is OPEN and the engine call is blocked: a concurrent
        # cancel and an EDIT both commit promptly (no Mission lock is held
        # across the call).
        open_starts = [s for s in self.starts(mission_id) if s["settlement"] is None]
        self.assertEqual([s["point"] for s in open_starts], ["runtime"])
        done = threading.Event()

        def writes():
            self.service.request_cancel(mission_id)
            self.edit(mission_id)
            done.set()
        threading.Thread(target=writes).start()
        self.assertTrue(done.wait(10), "a Mission write blocked behind the engine call")
        proceed.set()
        worker.join(30)
        self.assertEqual(len(results), 1)
        outcome = results[0]
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        start = self.starts(mission_id)[0]
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)

    def test_D4_write_after_return_before_settlement_and_at_the_task_point(self):
        # After the runtime start returned, before its settlement.
        mission_id, workflow_id = self.prepared()
        real_settle = self.gate.settle_start
        armed = {"on": True}

        def edit_then_settle(entry, start_id, owner_ref, outcome, identity, reason):
            if armed["on"]:
                armed["on"] = False
                self.edit(mission_id)
            return real_settle(entry, start_id, owner_ref, outcome, identity, reason)
        with mock.patch.object(self.gate, "settle_start", edit_then_settle):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        # The gate's own re-check preceded the write; the CANONICAL
        # settlement found the superseded revision and pends the stop.
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_STOP_REQUIRED)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        start = self.starts(mission_id)[0]
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIn("revision superseded", start["settlement"]["stop_reason"])
        self.assertTrue(obs(start)["absent"])
        # After the runtime start settled cleanly, before the task start:
        # the task start refuses, the runtime is left idle (stated), the
        # runtime start keeps its identity for the S-V cancel to find.
        second, wf2 = self.prepared()
        real_gate_open = self.gate.open_start

        def cancel_before_task_admission(entry, dispatch_sequence, point, owner_ref):
            if point == "task":
                self.service.request_cancel(second)
            return real_gate_open(entry, dispatch_sequence, point, owner_ref)
        with mock.patch.object(self.gate, "open_start",
                               cancel_before_task_admission):
            outcome = self.act(wf2, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertIn("idle and un-tasked", outcome.detail)
        self.assertEqual(len(self.engine.starts), 2)
        self.assertEqual(self.engine.tasks, [])
        starts = self.starts(second)
        self.assertEqual([s["point"] for s in starts], ["runtime"])
        self.assertEqual(starts[0]["settlement"]["outcome"], "completed")
        self.assertFalse(starts[0]["settlement"]["stop_pending"])
        self.assertEqual(starts[0]["settlement"]["identity"]["workspace_id"], WORKSPACE_ID)
        # A cancel AFTER the task hand-over (during the task settlement):
        # handed over, then the owned stop.
        third, wf3 = self.prepared()
        self.engine.live = []
        real_admit = self.gate.admit
        settlements = {"n": 0}

        def cancel_at_task_settlement(entry, boundary, settling=None):
            # The settlement's own current-authority re-check: the second
            # settling admission of this dispatch is the task start's.
            if settling is not None:
                settlements["n"] += 1
                if settlements["n"] == 2:
                    self.service.request_cancel(third)
            return real_admit(entry, boundary, settling=settling)
        with mock.patch.object(self.gate, "admit", cancel_at_task_settlement):
            outcome = self.act(wf3, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertIn("handed over, then a stop was required", outcome.detail)
        self.assertEqual(len(self.engine.tasks), 1)
        task_start = [s for s in self.starts(third) if s["point"] == "task"][0]
        self.assertTrue(task_start["settlement"]["stop_pending"])
        self.assertEqual(task_start["settlement"]["identity"]["task_id"], "task-started-1")
        self.assertTrue(obs(task_start)["absent"])
        self.assertIsNone(self.record(wf3)["target_engine"])

    def test_D5_expiry_is_inspected_at_settlement(self):
        mission_id = self.propose()
        self.approve(mission_id, expires_at=self.clock() + 5000)
        self.op("activate_proof_contract", mission_id)
        self.op("observe_resource_readiness", mission_id, "build_host",
                mission_state.READINESS_READY, self.clock())
        workflow_id = self.validated(mission_id)
        self.engine.before_start = lambda: self.clock.advance(6000)
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertFalse(outcome.ok and outcome.outcome is None)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        start = self.starts(mission_id)[0]
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIn("authorization not live", start["settlement"]["stop_reason"])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])

    def test_D6_ownership_of_the_idle_runtime_confirms_only_observed_absence(self):
        cases = {
            "close leaves the workspace visible": dict(close_leaves_visible=True),
            "close refused": dict(close_error=ws_module.WorkspaceCloseRefused("no")),
            "listing unavailable": dict(live_error=OSError("herdr unreachable")),
            "reused identity": dict(duplicate=True),
            "agents differ": dict(other_agents=True),
        }
        for label, config in cases.items():
            self.engine.reset()
            mission_id, workflow_id = self.prepared()
            engine = self.engine

            def arrange(engine=engine, config=config, mission_id=mission_id):
                self.edit(mission_id)
                engine.close_leaves_visible = config.get("close_leaves_visible", False)
                engine.close_error = config.get("close_error")
                if config.get("duplicate"):
                    engine.live.append({"workspace_id": WORKSPACE_ID,
                                        "agent_names": list(AGENT_NAMES)})
                if config.get("other_agents"):
                    engine.live = [{"workspace_id": WORKSPACE_ID,
                                    "agent_names": ["someone-else"]}]
                engine.live_error = config.get("live_error")
            # The write lands after the runtime start (before settlement),
            # then the ownership conditions are arranged.
            real_settle = self.gate.settle_start
            armed = {"on": True}

            def arrange_then_settle(*args, arrange=arrange):
                if armed["on"]:
                    armed["on"] = False
                    arrange()
                return real_settle(*args)
            with mock.patch.object(self.gate, "settle_start", arrange_then_settle):
                outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED, label)
            self.assertIn("PENDING", outcome.detail, label)
            start = self.starts(mission_id)[0]
            self.assertTrue(start["settlement"]["stop_pending"], label)
            self.assertFalse(obs(start)["absent"], label)
            self.assertFalse(mission_state.start_stop_confirmed(start), label)
            if label in ("listing unavailable", "reused identity", "agents differ"):
                self.assertEqual(engine.close_calls, [], label)
            else:
                self.assertEqual(engine.close_calls, [WORKSPACE_ID], label)
            self.assertEqual(engine.tasks, [], label)
            view = status_module.read_status(self.service, mission_id)["mission"]
            self.assertFalse(view["engagements"]["starts"][0]["stop_confirmed"], label)
            self.assertTrue(view["engagements"]["starts"][0]["stop_required"], label)
        # No close capability wired: the stop is pending, nothing closed.
        self.engine.reset()
        self.broker = self.gated_broker(close=False)
        mission_id, workflow_id = self.prepared()
        self.engine.before_start = lambda: self.edit(mission_id)
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertIn("PENDING", outcome.detail)
        self.assertFalse(obs(self.starts(mission_id)[0])["absent"])

    def test_D12_a_never_returning_start_is_abandoned_within_the_bound(self):
        mission_id, workflow_id = self.prepared()
        started, proceed = threading.Event(), threading.Event()
        self.engine.block_start = (started, proceed)
        self.addCleanup(proceed.set)
        import time
        with mock.patch.object(dispatch_module, "START_WAIT_SECONDS", 0.5):
            before = time.monotonic()
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            elapsed = time.monotonic() - before
        self.assertLess(elapsed, 10.0)
        self.assertTrue(started.is_set())
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_STOP_REQUIRED)
        self.assertIn("no response within 0.5s", outcome.detail)
        self.assertIn("abandoned", outcome.detail)
        self.assertIn("PENDING", outcome.detail)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertIsNone(start["settlement"]["identity"])
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertFalse(obs(start)["absent"])
        self.assertEqual(self.engine.tasks, [])
        entry = self.record(workflow_id)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(entry["target_engine"])
        self.assertIn("settled:uncertain (abandoned after 0.5s)",
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[1])
        # A retry starts nothing (the engagement requires a stop).
        again = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(len(self.engine.starts), 0)
        self.assertFalse(again.ok and again.outcome is None)
        # The late return: the runtime the abandoned call created is
        # owned from the identity it returned, stopped, and the stop
        # observed canonically — without any workflow-store write.
        record_bytes = self.store_bytes()
        observations = []
        real_observe = self.gate.observe_stop

        def recording_observe(*args):
            result = real_observe(*args)
            observations.append((args[3], args[4], result))
            return result
        with mock.patch.object(self.gate, "observe_stop", recording_observe):
            proceed.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                start = self.starts(mission_id)[0]
                if mission_state.start_stop_confirmed(start):
                    break
                time.sleep(0.05)
        self.assertTrue(mission_state.start_stop_confirmed(start),
                        (obs(start), observations,
                         self.engine.close_calls, self.engine.live))
        self.assertIn("late return", obs(start)["detail"])
        # The late identity was persisted canonically BEFORE the owned
        # stop (observation 2, absent False, identity), then the stop was
        # observed (observation 3, absent True, identity); the caller's
        # own pending observation (no identity) is observation 1.
        observations_recorded = start["stop_observations"]
        self.assertEqual([o["absent"] for o in observations_recorded],
                         [False, False, True])
        self.assertIsNone(observations_recorded[0]["identity"])
        self.assertEqual(observations_recorded[1]["identity"]["workspace_id"],
                         WORKSPACE_ID)
        self.assertIn("owned stop not yet attempted", observations_recorded[1]["detail"])
        self.assertEqual(mission_state.start_identity(start)["agent_names"],
                         AGENT_NAMES)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(self.engine.live, [])
        self.assertEqual(self.store_bytes(), record_bytes)
        # The next pass records the canonical confirmation on the record
        # (a release attempt on the blocked record; no invocation).
        self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])

    # -- late-return orderings (checkpoint-1 correction) -----------------

    def abandoned_start(self, before_close=None):
        """A prepared workflow whose runtime start will block until
        ``proceed`` is set, with the wait bound patched to 0.5 s and the
        guard instance captured. ``before_close(guard, point)`` runs
        right before the guard's abandoned close (the settlement)."""
        mission_id, workflow_id = self.prepared()
        started, proceed = threading.Event(), threading.Event()
        self.engine.block_start = (started, proceed)
        self.addCleanup(proceed.set)
        guards = []
        real_guard = broker_module._MissionStartGuard

        class Capturing(real_guard):
            def __init__(self, *args, **kwargs):
                real_guard.__init__(self, *args, **kwargs)
                guards.append(self)

            def close(self, point, failed=False, result=None, abandoned=False):
                if abandoned and before_close is not None:
                    before_close(self, point)
                return real_guard.close(self, point, failed=failed, result=result,
                                        abandoned=abandoned)
        patcher = mock.patch.object(broker_module, "_MissionStartGuard", Capturing)
        patcher.start()
        self.addCleanup(patcher.stop)
        bound = mock.patch.object(dispatch_module, "START_WAIT_SECONDS", 0.5)
        bound.start()
        self.addCleanup(bound.stop)
        return mission_id, workflow_id, started, proceed, guards

    def wait_for(self, predicate, seconds=10):
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_D14_late_return_before_the_callers_settlement_is_consumed(self):
        holder = {}

        def release_then_wait(guard, point):
            # The caller abandoned the call and is about to settle: let
            # the engine return FIRST and wait until the worker has parked
            # its late result, so the settlement consumes it.
            holder["proceed"].set()
            self.assertTrue(self.wait_for(
                lambda: guard.late_outcomes.get(point)
                == "parked for the caller's settlement"))
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start(
            before_close=release_then_wait)
        holder["proceed"] = proceed
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertIn("abandoned", outcome.detail)
        self.assertIn("late result consumed", outcome.detail)
        self.assertIn("CONFIRMED (absence observed and recorded canonically)",
                      outcome.detail)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertEqual(start["settlement"]["identity"]["workspace_id"], WORKSPACE_ID)
        self.assertIn("abandoned: no response within 0.5s; late result consumed",
                      start["settlement"]["owner_reason"])
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertEqual([o["absent"] for o in start["stop_observations"]], [True])
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
        self.assertIn("settled:completed (abandoned after 0.5s)", receipts[1])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED, receipts[2])
        view = status_module.read_status(self.service, mission_id)["mission"]
        reported = view["engagements"]["starts"][0]
        self.assertEqual((reported["observed_outcome"], reported["stop_required"],
                          reported["stop_confirmed"]), ("completed", True, True))
        # A retry starts nothing (the engagement requires a stop).
        again = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertFalse(again.ok and again.outcome is None)
        self.assertEqual(len(self.engine.starts), 1)

    def test_D14b_late_return_during_the_settlement_is_handed_over_in_the_caller(self):
        # The engine returns while the caller's settlement transaction is
        # in flight (after the guard looked for a parked result, before it
        # marked the start settled): the result is handed over by the
        # caller right after its settlement — identity persisted, owned
        # stop, observation — never dropped.
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        real_settle = self.gate.settle_start
        seen = {"n": 0}

        def release_then_settle(*args):
            seen["n"] += 1
            if seen["n"] == 1:
                proceed.set()
                self.assertTrue(self.wait_for(
                    lambda: guards[0].late_outcomes.get(RUNTIME_POINT)
                    == "parked for the caller's settlement"))
            return real_settle(*args)
        with mock.patch.object(self.gate, "settle_start", release_then_settle):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED)
        self.assertIn("late result handed over at settlement", outcome.detail)
        self.assertIn("CONFIRMED (absence observed and recorded canonically)",
                      outcome.detail)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertIsNone(start["settlement"]["identity"])
        self.assertEqual([o["absent"] for o in start["stop_observations"]],
                         [False, True])
        self.assertEqual(start["stop_observations"][0]["identity"]["workspace_id"],
                         WORKSPACE_ID)
        self.assertEqual(mission_state.start_identity(start)["workspace_id"], WORKSPACE_ID)
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1])

    def test_D15_late_return_after_settlement_with_stop_failure_then_restart(self):
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertIn("abandoned", outcome.detail)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertIsNone(mission_state.start_identity(start))
        # The engine returns late while the owned close is broken: the
        # identity is persisted canonically FIRST, the failed stop is
        # observed with it, nothing is confirmed.
        self.engine.close_error = RuntimeError("tmux kill failed")
        proceed.set()
        self.assertTrue(self.wait_for(
            lambda: len(self.starts(mission_id)[0]["stop_observations"]) >= 3))
        self.assertTrue(self.wait_for(
            lambda: str(guards[0].late_outcomes.get(RUNTIME_POINT, "")).startswith(
                "absent=False observed")))
        start = self.starts(mission_id)[0]
        self.assertEqual([o["absent"] for o in start["stop_observations"]],
                         [False, False, False])
        self.assertEqual(mission_state.start_identity(start)["workspace_id"], WORKSPACE_ID)
        self.assertIn("raised RuntimeError", obs(start)["detail"])
        self.assertFalse(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.live), 1)
        # RESTART: a fresh Broker's pass recovers from the persisted
        # identity — closes, observes absence, confirms; no invocation.
        self.engine.close_error = None
        fresh = self.gated_broker()
        released = self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        # C-2 (S-V), revised for R15-3: the release pass's own outcome is
        # pinned exactly — the recovery ran first (close, absence,
        # confirmation), then the release's OWN cleanup admission admitted
        # it (no obligation outstanding) and the release handler's
        # retention check refused (nothing released); engineering stays
        # refused TERMINALLY on the confirmed stop requirement.
        self.assert_release_after_recovery(workflow_id, released,
                                           "confirmed by observed absence")
        start = self.starts(mission_id)[0]
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        self.assertEqual(self.engine.live, [])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1])
        view = status_module.read_status(self.service, mission_id)["mission"]
        self.assertTrue(view["engagements"]["starts"][0]["stop_confirmed"])
        self.assertEqual(view["engagements"]["starts"][0]["identity"]["workspace_id"],
                         WORKSPACE_ID)

    def test_D16_late_return_with_observation_persistence_failures_then_restart(self):
        errors = {
            "source unavailable": lambda: mission_store.MissionStoreError("disk gone"),
            "stale sequence": lambda: mission_record.MissionError(
                mission_state_service.PROBLEM_STALE_SEQUENCE, "moved"),
            "capacity": lambda: mission_store.MissionStoreError(
                "full", mission_store.PROBLEM_STORE_FULL),
        }
        for label, make_error in errors.items():
            # (a) the ABSENCE observation fails after the owned stop:
            # identity persisted, stop executed, absence observed but not
            # recorded; the restart pass confirms from the persisted
            # identity and a fresh listing, without a second close.
            self.engine.reset()
            mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
            self.act(workflow_id, broker_module.ACTION_DISPATCH)
            real_observe = self.service.observe_engagement_stop

            def fail_absent(mission, op_id, seq, start_id, owner_ref, absent, detail,
                            identity, context, make_error=make_error):
                if absent:
                    raise make_error()
                return real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                    detail, identity, context=context)
            with mock.patch.object(self.service, "observe_engagement_stop",
                                   fail_absent):
                proceed.set()
                self.assertTrue(self.wait_for(
                    lambda: str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))
                    .startswith("absent=True observed; observation not persisted")),
                    label)
            start = self.starts(mission_id)[0]
            self.assertEqual([o["absent"] for o in start["stop_observations"]],
                             [False, False], label)
            self.assertEqual(mission_state.start_identity(start)["workspace_id"],
                             WORKSPACE_ID, label)
            self.assertFalse(mission_state.start_stop_confirmed(start), label)
            self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
            self.assertEqual(self.engine.live, [], label)
            fresh = self.gated_broker()
            released = self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
            self.assert_release_after_recovery(
                workflow_id, released, "the stop is confirmed by observed absence", label)
            start = self.starts(mission_id)[0]
            self.assertTrue(mission_state.start_stop_confirmed(start), label)
            self.assertIn("ABSENT from a fresh listing", obs(start)["detail"], label)
            self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
            self.assertEqual(len(self.engine.starts), 1, label)
            self.assertEqual(self.engine.tasks, [], label)
            self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                          self.receipts(workflow_id,
                                        broker_module.MISSION_START_RECEIPT_MARKER)[-1],
                          label)
        # (b) the IDENTITY persistence fails past the bound: the owned
        # stop is WITHHELD (no unrecorded effect), the runtime stays live,
        # the known identity is RETAINED (S4e); once the source answers
        # the owner's pass records it, stops and confirms.
        self.engine.reset()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)

        def fail_identity(mission, op_id, seq, start_id, owner_ref, absent, detail,
                          identity, context):
            if identity is not None:
                raise mission_store.MissionStoreError("disk gone")
            return real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                detail, identity, context=context)
        real_observe = self.service.observe_engagement_stop
        with mock.patch.object(self.service, "observe_engagement_stop", fail_identity), \
                mock.patch.object(broker_module, "MAX_LATE_HANDOVER_ATTEMPTS", 2), \
                mock.patch.object(broker_module, "LATE_HANDOVER_RETRY_SECONDS", 0.01):
            proceed.set()
            self.assertTrue(self.wait_for(
                lambda: str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))
                .startswith("identity not persisted after 2 attempts")))
            # While the source still refuses, the owner's pass changes
            # nothing and closes nothing.
            released = self.act(workflow_id, broker_module.ACTION_RELEASE,
                                broker=self.gated_broker())
            self.assert_release_after_recovery(workflow_id, released,
                                               "the stop is NOT confirmed")
            start = self.starts(mission_id)[0]
            self.assertIsNone(mission_state.start_identity(start))
            self.assertEqual(self.engine.close_calls, [])
            self.assertEqual(len(self.engine.live), 1)
        self.assertIn("retained for the owner's next pass",
                      guards[0].late_outcomes[RUNTIME_POINT])
        # The source answers: the owner's pass consumes the retained
        # identity — recorded first, then the single close, then absence.
        fresh = self.gated_broker()
        released = self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        self.assert_release_after_recovery(workflow_id, released,
                                           "the stop is confirmed by observed absence")
        start = self.starts(mission_id)[0]
        self.assertEqual(mission_state.start_identity(start)["workspace_id"], WORKSPACE_ID)
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(self.engine.live, [])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertEqual(broker_module.RETAINED_HANDOVERS.get(
            (self.gate.owner_ref(self.record(workflow_id), 1), start["start_id"])), None)

    def assert_release_after_recovery(self, workflow_id, released, standing, label=""):
        """The exact release outcome for a record whose engagement start
        requires a stop (R15-3: the release has its OWN cleanup admission,
        separate from engineering). The owner's recovery ran inside the
        release first. With the stop NOT confirmed the cleanup WAITS (a
        held outcome naming the outstanding obligation); once observed
        absence confirmed it, the cleanup is admitted and the release
        handler's own retention check decides (this record is retained:
        nothing is released). Either way the lease is untouched and
        engineering stays refused TERMINALLY at the action admission,
        naming the requirement and the stop's standing as observed
        (confirmed only by absence)."""
        self.assertFalse(released.ok, (label, released.problem, released.detail))
        if "NOT confirmed" in standing:
            self.assertEqual(released.outcome, broker_module.OUTCOME_MISSION_HELD, label)
            self.assertEqual(released.problem, gate_module.PROBLEM_CLEANUP_AWAITS_STOP,
                             label)
        else:
            self.assertEqual(released.problem, broker_module.PROBLEM_RETENTION_PROTECTED,
                             label)
        entry = self.record(workflow_id)
        self.assertIsNone(entry["workspace_lease"]["released_at"], label)
        engineering = self.gate.admit(entry, gate_module.BOUNDARY_ACTION_ADMISSION)
        self.assertEqual((engineering.problem, engineering.classification),
                         (gate_module.PROBLEM_START_STOP_REQUIRED, gate_module.CLASS_TERMINAL),
                         label)
        self.assertIn(standing, engineering.detail, label)

    # -- refused settlements (re-checkpoint 2 correction) ------------------

    def fast_handover(self, attempts=None):
        patchers = [mock.patch.object(broker_module, "LATE_HANDOVER_RETRY_SECONDS", 0.01)]
        if attempts is not None:
            patchers.append(mock.patch.object(broker_module, "MAX_LATE_HANDOVER_ATTEMPTS",
                                              attempts))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_handed_over(self, mission_id, workflow_id, label=""):
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed", label)
        self.assertEqual(start["settlement"]["identity"]["workspace_id"], WORKSPACE_ID, label)
        self.assertIn("late result settled by the owner", start["settlement"]["owner_reason"],
                      label)
        self.assertTrue(start["settlement"]["stop_pending"], label)
        self.assertEqual([o["absent"] for o in start["stop_observations"]], [True], label)
        self.assertTrue(mission_state.start_stop_confirmed(start), label)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
        self.assertEqual(self.engine.live, [], label)
        self.assertEqual(len(self.engine.starts), 1, label)
        self.assertEqual(self.engine.tasks, [], label)
        # The owner's next pass (a fresh Broker) records the canonical
        # confirmation on the record without any invocation.
        fresh = self.gated_broker()
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
        self.assertIn(" state=%s" % broker_module.START_STATE_UNSETTLED, receipts[1], label)
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED, receipts[-1],
                      label)
        self.assertEqual(len(self.engine.starts), 1, label)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
        view = status_module.read_status(self.service, mission_id)["mission"]
        reported = view["engagements"]["starts"][0]
        self.assertEqual((reported["settled"], reported["observed_outcome"],
                          reported["stop_confirmed"]), (True, "completed", True), label)

    def test_D17_late_return_during_a_refused_settlement_is_handed_over(self):
        # Q1 shape: the caller's settlement is refused by the source while
        # the engine returns; once the source answers again the owner
        # settles the start with the late identity, stops, observes.
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        real_settle = self.service.settle_engagement_start
        calls = {"n": 0}

        def refuse_first(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                proceed.set()
                self.assertTrue(self.wait_for(
                    lambda: guards[0].late_outcomes.get(RUNTIME_POINT)
                    == "parked for the caller's settlement"))
                raise mission_store.MissionStoreError("disk gone")
            return real_settle(*args, **kwargs)
        with mock.patch.object(self.service, "settle_engagement_start", refuse_first):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
            self.assertIn("hand-over scheduled", outcome.detail)
            self.assertTrue(self.wait_for(
                lambda: mission_state.start_stop_confirmed(self.starts(mission_id)[0])),
                guards[0].late_outcomes)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(guards[0].late_outcomes[RUNTIME_POINT],
                         "absent=True observed; canonical stop_confirmed=True")
        self.assert_handed_over(mission_id, workflow_id, "Q1")

    def test_D18_late_return_after_a_refused_settlement_is_handed_over(self):
        # Q2 shape: the refused settlement already returned; the engine
        # returns later; the source answers by then.
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()

        def refuse(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start", refuse):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertNotIn("hand-over scheduled", outcome.detail)
        self.assertIsNone(self.starts(mission_id)[0]["settlement"])
        proceed.set()
        self.assertTrue(self.wait_for(
            lambda: mission_state.start_stop_confirmed(self.starts(mission_id)[0])),
            guards[0].late_outcomes)
        self.assert_handed_over(mission_id, workflow_id, "Q2")

    def test_D18b_hand_over_retries_until_the_source_answers(self):
        # The source keeps refusing for a while after the engine returned:
        # bounded retries, exactly one settlement, then the stop.
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        real_settle = self.service.settle_engagement_start
        calls = {"n": 0}
        attempts_seen = []
        real_pause = broker_module._MissionStartGuard._retry_pause

        def recording_pause(guard):
            attempts_seen.append(guard.late_outcomes.get(RUNTIME_POINT))
            return real_pause(guard)

        def refuse_four(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 4:
                raise mission_store.MissionStoreError("disk gone")
            return real_settle(*args, **kwargs)
        at_close = []
        real_close = self.engine.close_workspace

        def snapshot_close(workspace_id):
            # The state the guard reports at the moment of the owned stop.
            at_close.append(guards[0].late_outcomes.get(RUNTIME_POINT))
            return real_close(workspace_id)
        self.broker.worker._workspace_close_fn = snapshot_close
        with mock.patch.object(self.service, "settle_engagement_start", refuse_four), \
                mock.patch.object(broker_module._MissionStartGuard, "_retry_pause",
                                  recording_pause):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
            proceed.set()
            self.assertTrue(self.wait_for(
                lambda: mission_state.start_stop_confirmed(self.starts(mission_id)[0])),
                guards[0].late_outcomes)
        # Exactly five settlement calls: the caller's refused one, three
        # refused owner attempts (each followed by one bounded pause) and
        # the fourth owner attempt that succeeded — no pause after it.
        self.assertEqual(calls["n"], 5)
        self.assertEqual(attempts_seen, [
            "hand-over retrying after a refused settlement (attempt %d refused:"
            " mission_control_source_unavailable)" % n for n in (1, 2, 3)])
        self.assertEqual(at_close, [
            "settled completed by the owner after a refused settlement (attempt 4)"])
        self.assertEqual(guards[0].late_outcomes[RUNTIME_POINT],
                         "absent=True observed; canonical stop_confirmed=True")
        self.assert_handed_over(mission_id, workflow_id, "retries")
        # Exhaustion: with a source that never answers, the hand-over
        # gives up after the bound and says so; nothing was invoked.
        self.engine.reset()
        self.fast_handover(attempts=3)
        second, wf2, started2, proceed2, guards2 = self.abandoned_start()
        with mock.patch.object(self.service, "settle_engagement_start",
                               lambda *a, **k: (_ for _ in ()).throw(
                                   mission_store.MissionStoreError("gone"))):
            self.act(wf2, broker_module.ACTION_DISPATCH)
            proceed2.set()
            self.assertTrue(self.wait_for(
                lambda: str(guards2[0].late_outcomes.get(RUNTIME_POINT, ""))
                .startswith("hand-over exhausted after 3 attempts")))
        self.assertIsNone(self.starts(second)[0]["settlement"])
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(len(self.engine.starts), 1)

    def test_D19_refused_settlement_late_identity_then_stop_failure_then_restart(self):
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()

        def refuse(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start", refuse):
            self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.engine.close_error = RuntimeError("tmux kill failed")
        proceed.set()
        self.assertTrue(self.wait_for(
            lambda: str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))
            .startswith("absent=False observed; canonical")), guards[0].late_outcomes)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertEqual(mission_state.start_identity(start)["workspace_id"], WORKSPACE_ID)
        self.assertFalse(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        # RESTART: a fresh Broker over the same stores recovers from the
        # settlement's identity — closes, confirms — without invocation.
        self.engine.close_error = None
        fresh = self.gated_broker()
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        start = self.starts(mission_id)[0]
        self.assertTrue(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1])

    def test_D20_refused_settlement_without_late_result_then_restart(self):
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()

        def refuse(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start", refuse):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertIsNone(self.starts(mission_id)[0]["settlement"])
        # RESTART with the engine still not returned: the owner's pass
        # settles UNCERTAIN, stop pending, never confirmed, zero
        # invocations, nothing closed (no identity).
        fresh = self.gated_broker()
        released = self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        # The release pass itself: the owner recovery ran inside it, then
        # the release's OWN cleanup admission (R15-3) WAITED because the
        # settled start now owes a stop observed absence has not confirmed;
        # the release handler never ran (the lease is untouched), and
        # engineering stays refused TERMINALLY.
        self.assert_release_after_recovery(workflow_id, released, "the stop is NOT confirmed")
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertIsNone(start["settlement"]["identity"])
        self.assertIn("owner recovery", start["settlement"]["owner_reason"])
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertEqual([o["absent"] for o in start["stop_observations"]], [False])
        self.assertFalse(mission_state.start_stop_confirmed(start))
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(self.engine.starts, [])
        self.assertEqual(self.engine.tasks, [])
        receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
        self.assertIn(" state=%s" % broker_module.START_STATE_SETTLED_UNCERTAIN_RECOVERY,
                      receipts[-2])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_PENDING, receipts[-1])
        view = status_module.read_status(self.service, mission_id)["mission"]
        reported = view["engagements"]["starts"][0]
        self.assertEqual((reported["observed_outcome"], reported["stop_required"],
                          reported["stop_confirmed"], reported["identity"]),
                         ("uncertain", True, False, None))
        # A second pass changes nothing and invokes nothing.
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=self.gated_broker())
        self.assertEqual(len(self.starts(mission_id)[0]["stop_observations"]), 1)
        self.assertEqual(self.engine.starts, [])
        # The engine finally returns to the ORIGINAL process: its late
        # thread finds the start settled and persists the identity through
        # an observation, then stops — still no invocation.
        proceed.set()
        self.assertTrue(self.wait_for(
            lambda: mission_state.start_stop_confirmed(self.starts(mission_id)[0])),
            guards[0].late_outcomes)
        self.assertEqual(mission_state.start_identity(self.starts(mission_id)[0])[
            "workspace_id"], WORKSPACE_ID)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)

    def test_D21_identity_persistence_fails_then_succeeds_stop_after_record(self):
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)   # settled uncertain
        events = []
        real_observe = self.service.observe_engagement_stop
        real_close = self.engine.close_workspace
        failures = {"n": 0}

        def observe(mission, op_id, seq, start_id, owner_ref, absent, detail, identity,
                    context):
            if identity is not None and not absent and failures["n"] < 3:
                failures["n"] += 1
                raise mission_store.MissionStoreError("disk gone")
            result = real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                  detail, identity, context=context)
            events.append(("persisted", absent, identity is not None))
            return result

        def close(workspace_id):
            events.append(("close", workspace_id))
            return real_close(workspace_id)
        self.engine.close_workspace = close
        self.broker.worker._workspace_close_fn = close
        with mock.patch.object(self.service, "observe_engagement_stop", observe):
            proceed.set()
            self.assertTrue(self.wait_for(lambda: len(events) == 3),
                            (events, guards[0].late_outcomes))
        self.assertTrue(mission_state.start_stop_confirmed(self.starts(mission_id)[0]))
        self.assertEqual(failures["n"], 3)
        self.assertEqual(events, [("persisted", False, True), ("close", WORKSPACE_ID),
                                  ("persisted", True, True)])
        start = self.starts(mission_id)[0]
        self.assertEqual([o["absent"] for o in start["stop_observations"]],
                         [False, False, True])
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])

    def test_D22_only_the_owner_can_settle_an_unsettled_start(self):
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()

        def refuse(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start", refuse):
            self.act(workflow_id, broker_module.ACTION_DISPATCH)
        entry = self.record(workflow_id)
        start_id = self.starts(mission_id)[0]["start_id"]
        # Another owner reference, same principal: refused.
        settled, refusal = self.gate.settle_start(
            entry, start_id, "someone-else", "uncertain", None, None)
        self.assertIsNone(settled)
        self.assertEqual(refusal.problem, mission_state.PROBLEM_ENGAGEMENT_START_OWNER)
        # Another principal's gate with the right owner reference: refused.
        other_gate = gate_module.MissionEffectGate(
            self.service, gate_module.local_process_context("someone-else"))
        settled, refusal = other_gate.settle_start(
            entry, start_id, self.gate.owner_ref(entry, 1), "uncertain", None, None)
        self.assertEqual(refusal.problem, mission_state.PROBLEM_ENGAGEMENT_START_OWNER)
        # Another workflow's recovery pass never touches it.
        attempts = []
        real_settle = self.service.settle_engagement_start

        def recording_settle(mission, op_id, seq, sid, owner_ref, *rest, **kwargs):
            attempts.append(sid)
            return real_settle(mission, op_id, seq, sid, owner_ref, *rest, **kwargs)
        other_mission = self.ready_mission()
        other_workflow = self.workflow_row(other_mission)
        with mock.patch.object(self.service, "settle_engagement_start", recording_settle):
            self.assertTrue(self.act(other_workflow, broker_module.ACTION_MATERIALIZE).ok)
        self.assertEqual(attempts, [])
        self.assertIsNone(self.starts(mission_id)[0]["settlement"])
        # The rightful owner's pass settles it uncertain.
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=self.gated_broker())
        self.assertEqual(self.starts(mission_id)[0]["settlement"]["outcome"], "uncertain")
        self.assertEqual(self.engine.starts, [])

    # -- retained hand-overs (S4e) ------------------------------------------

    def retained_key(self, workflow_id, mission_id, sequence=1):
        return (self.gate.owner_ref(self.record(workflow_id), sequence),
                self.starts(mission_id)[0]["start_id"])

    def owner_passes(self, workflow_id):
        outcomes = []
        for action in (broker_module.ACTION_RELEASE, broker_module.ACTION_DISPATCH,
                       broker_module.ACTION_RELEASE):
            out = self.act(workflow_id, action, broker=self.gated_broker())
            outcomes.append((action, out.ok, out.problem))
        return outcomes

    def assert_recovered_once(self, mission_id, workflow_id, invocations, label):
        start = self.starts(mission_id)[0]
        self.assertEqual(mission_state.start_identity(start)["workspace_id"],
                         WORKSPACE_ID, label)
        self.assertTrue(mission_state.start_stop_confirmed(start), label)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
        self.assertEqual(self.engine.live, [], label)
        self.assertEqual(len(self.engine.starts), invocations, label)
        self.assertEqual(self.engine.tasks, [], label)
        self.assertIsNone(broker_module.RETAINED_HANDOVERS.get(
            self.retained_key(workflow_id, mission_id)), label)
        # Later owner passes: no invocation, no second close.
        self.owner_passes(workflow_id)
        self.assertEqual(len(self.engine.starts), invocations, label)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
        self.assertEqual(len(self.starts(mission_id)), 1, label)
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1],
                      label)

    def source_down(self):
        """A settlement outage the test switches off."""
        state = {"down": True, "calls": 0}
        real = self.service.settle_engagement_start

        def settle(*args, **kwargs):
            state["calls"] += 1
            if state["down"]:
                raise mission_store.MissionStoreError("source down")
            return real(*args, **kwargs)
        patcher = mock.patch.object(self.service, "settle_engagement_start", settle)
        patcher.start()
        self.addCleanup(patcher.stop)
        return state

    def test_D23_R1_late_before_close_with_refused_settlement_is_retained(self):
        self.fast_handover()
        holder = {}

        def release_before_close(guard, point):
            holder["proceed"].set()
            self.assertTrue(self.wait_for(
                lambda: guard.late_outcomes.get(point) == "parked for the caller's settlement"))
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start(
            before_close=release_before_close)
        holder["proceed"] = proceed
        state = self.source_down()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertIn("known result retained", outcome.detail)
        retained = broker_module.RETAINED_HANDOVERS.get(self.retained_key(workflow_id, mission_id))
        self.assertEqual((retained["outcome"], retained["identity"]["workspace_id"]),
                         ("completed", WORKSPACE_ID))
        # Still down: the owner's pass records nothing, closes nothing.
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=self.gated_broker())
        self.assertIsNone(self.starts(mission_id)[0]["settlement"])
        self.assertEqual(self.engine.close_calls, [])
        state["down"] = False
        self.owner_passes(workflow_id)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertIn("retained completed result is settled now",
                      start["settlement"]["owner_reason"])
        self.assertEqual([o["absent"] for o in start["stop_observations"]], [True])
        self.assert_recovered_once(mission_id, workflow_id, 1, "R1")

    def test_D24_R2_synchronous_result_with_refused_settlement_is_retained(self):
        self.fast_handover()
        mission_id, workflow_id = self.prepared()
        state = self.source_down()
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertIn("known result retained", outcome.detail)
        self.assertEqual(len(self.engine.starts), 1)
        self.assertEqual(self.engine.tasks, [])   # the task point never opened
        retained = broker_module.RETAINED_HANDOVERS.get(self.retained_key(workflow_id, mission_id))
        self.assertEqual(retained["identity"]["agent_names"], AGENT_NAMES)
        state["down"] = False
        self.owner_passes(workflow_id)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertIn("settlement refused (mission_control_source_unavailable) with a"
                      " known completed result", start["settlement"]["owner_reason"])
        self.assert_recovered_once(mission_id, workflow_id, 1, "R2")

    def test_D25_R3_outage_beyond_all_attempts_then_restored(self):
        self.fast_handover(attempts=2)
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        state = self.source_down()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)
        proceed.set()
        self.assertTrue(self.wait_for(
            lambda: str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))
            .startswith("hand-over exhausted after 2 attempts")), guards[0].late_outcomes)
        self.assertIn("retained for the owner's next pass", guards[0].late_outcomes[RUNTIME_POINT])
        self.assertIsNotNone(broker_module.RETAINED_HANDOVERS.get(
            self.retained_key(workflow_id, mission_id)))
        self.assertEqual(self.engine.close_calls, [])
        state["down"] = False
        self.owner_passes(workflow_id)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "completed")
        self.assertIn("hand-over exhausted after 2 attempts", start["settlement"]["owner_reason"])
        self.assert_recovered_once(mission_id, workflow_id, 1, "R3")

    def test_D26_observation_exhaustion_then_restoration(self):
        # (a) IDENTITY-observation exhaustion (settled uncertain by the
        # caller; the late identity cannot be persisted within the bound).
        self.fast_handover(attempts=2)
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)
        real_observe = self.service.observe_engagement_stop
        state = {"down": True}

        def observe(mission, op_id, seq, start_id, owner_ref, absent, detail, identity,
                    context):
            if state["down"] and identity is not None:
                raise mission_store.MissionStoreError("source down")
            return real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                detail, identity, context=context)
        with mock.patch.object(self.service, "observe_engagement_stop", observe):
            proceed.set()
            self.assertTrue(self.wait_for(
                lambda: "retained for the owner's next pass"
                in str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))))
            self.assertEqual(self.engine.close_calls, [])
            self.act(workflow_id, broker_module.ACTION_RELEASE, broker=self.gated_broker())
            self.assertIsNone(mission_state.start_identity(self.starts(mission_id)[0]))
            self.assertEqual(self.engine.close_calls, [])
            state["down"] = False
            self.owner_passes(workflow_id)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "uncertain")
        self.assertIn("retained execution identity recorded",
                      [o["detail"] for o in start["stop_observations"]
                       if o["identity"] is not None][0])
        self.assert_recovered_once(mission_id, workflow_id, 1, "identity exhaustion")
        # (b) STOP-observation exhaustion: identity persisted, the close
        # issued, absence observed but not recorded within the bound; the
        # owner's pass records absence from a fresh listing — no second
        # close.
        self.engine.reset()
        self.fast_handover(attempts=2)
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)
        state = {"down": True}

        def observe_absent_fails(mission, op_id, seq, start_id, owner_ref, absent, detail,
                                 identity, context):
            if state["down"] and absent:
                raise mission_store.MissionStoreError("source down")
            return real_observe(mission, op_id, seq, start_id, owner_ref, absent,
                                detail, identity, context=context)
        with mock.patch.object(self.service, "observe_engagement_stop",
                               observe_absent_fails):
            proceed.set()
            self.assertTrue(self.wait_for(
                lambda: "observation not persisted after 2 attempts"
                in str(guards[0].late_outcomes.get(RUNTIME_POINT, ""))))
            retained = broker_module.RETAINED_HANDOVERS.get(
                self.retained_key(workflow_id, mission_id))
            self.assertTrue(retained["stop_issued"])
            self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
            state["down"] = False
            self.owner_passes(workflow_id)
        start = self.starts(mission_id)[0]
        self.assertIn("ABSENT from a fresh listing", obs(start)["detail"])
        self.assert_recovered_once(mission_id, workflow_id, 1, "stop-observation exhaustion")

    def test_D27_late_thread_and_owner_pass_race_close_once(self):
        # The late thread holds the per-start lock through its owned stop
        # (the engine close blocks); an owner pass meanwhile skips the
        # start instead of closing it a second time.
        self.fast_handover()
        mission_id, workflow_id, started, proceed, guards = self.abandoned_start()
        state = self.source_down()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)
        closing, release_close = threading.Event(), threading.Event()
        self.addCleanup(release_close.set)
        real_close = self.engine.close_workspace

        def slow_close(workspace_id):
            closing.set()
            self.assertTrue(release_close.wait(20))
            return real_close(workspace_id)
        self.broker.worker._workspace_close_fn = slow_close
        state["down"] = False
        proceed.set()
        self.assertTrue(closing.wait(20))
        # The late thread is inside its close: the owner's pass (a fresh
        # Broker sharing the engine) must not close again.
        fresh = self.gated_broker()
        fresh.worker._workspace_close_fn = slow_close
        self.act(workflow_id, broker_module.ACTION_RELEASE, broker=fresh)
        self.assertEqual(self.engine.close_calls, [])   # the slow close has not returned
        release_close.set()
        self.assertTrue(self.wait_for(
            lambda: mission_state.start_stop_confirmed(self.starts(mission_id)[0])))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.owner_passes(workflow_id)
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)

    def test_D28_wrong_owner_cannot_consume_the_retained_entry(self):
        self.fast_handover()
        mission_id, workflow_id = self.prepared()
        state = self.source_down()
        self.act(workflow_id, broker_module.ACTION_DISPATCH)
        key = self.retained_key(workflow_id, mission_id)
        self.assertIsNotNone(broker_module.RETAINED_HANDOVERS.get(key))
        state["down"] = False
        # Another principal's gate (another owner_ref) runs its passes:
        # nothing consumed, nothing settled, nothing closed.
        other_gate = gate_module.MissionEffectGate(
            self.service, gate_module.local_process_context("someone-else"))
        other = self.gated_broker(gate=other_gate)
        for action in (broker_module.ACTION_RELEASE, broker_module.ACTION_DISPATCH):
            self.act(workflow_id, action, broker=other)
        self.assertIsNotNone(broker_module.RETAINED_HANDOVERS.get(key))
        self.assertIsNone(self.starts(mission_id)[0]["settlement"])
        self.assertEqual(self.engine.close_calls, [])
        self.assertEqual(len(self.engine.starts), 1)
        # The rightful owner consumes it.
        self.owner_passes(workflow_id)
        self.assert_recovered_once(mission_id, workflow_id, 1, "wrong owner")

    def test_D13_observation_persistence_failures_never_claim_confirmation(self):
        cases = {
            "source unavailable": mission_store.MissionStoreError("disk gone"),
            "stale sequence": mission_record.MissionError(
                mission_state_service.PROBLEM_STALE_SEQUENCE, "moved"),
            "capacity": mission_store.MissionStoreError(
                "full", mission_store.PROBLEM_STORE_FULL),
        }
        for label, error in cases.items():
            self.engine.reset()
            mission_id, workflow_id = self.prepared()
            self.engine.before_start = lambda m=mission_id: self.edit(m)
            real_observe = self.service.observe_engagement_stop
            armed = {"on": True}

            def observe_fails(*args, error=error, **kwargs):
                if armed["on"]:
                    armed["on"] = False
                    raise error
                return real_observe(*args, **kwargs)
            with mock.patch.object(self.service, "observe_engagement_stop",
                                   observe_fails):
                outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
            self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_BLOCKED, label)
            self.assertIn("absence OBSERVED; canonical confirmation PENDING",
                          outcome.detail, label)
            self.assertNotIn("CONFIRMED", outcome.detail, label)
            receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
            self.assertIn(" state=%s" % broker_module.START_STATE_STOP_OBSERVED_UNCONFIRMED,
                          receipts[-1], label)
            self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
            start = self.starts(mission_id)[0]
            self.assertTrue(start["settlement"]["stop_pending"], label)
            self.assertEqual(start["stop_observations"], [], label)
            view = status_module.read_status(self.service, mission_id)["mission"]
            self.assertFalse(view["engagements"]["starts"][0]["stop_confirmed"], label)
            self.assertTrue(view["engagements"]["starts"][0]["stop_required"], label)
            # The retry (a release attempt on the blocked record) records
            # the confirmation from a fresh absence — no invocation.
            starts_before = len(self.engine.starts)
            self.act(workflow_id, broker_module.ACTION_RELEASE)
            start = self.starts(mission_id)[0]
            self.assertTrue(mission_state.start_stop_confirmed(start), label)
            receipts = self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)
            self.assertIn(" state=%s" % broker_module.START_STATE_STOP_CONFIRMED,
                          receipts[-1], label)
            self.assertEqual(len(self.engine.starts), starts_before, label)
            self.assertEqual(self.engine.close_calls, [WORKSPACE_ID], label)
            view = status_module.read_status(self.service, mission_id)["mission"]
            self.assertTrue(view["engagements"]["starts"][0]["stop_confirmed"], label)
        # A raising wired close: pending (no confirmation), recoverable
        # once the close works.
        self.engine.reset()
        mission_id, workflow_id = self.prepared()
        self.engine.before_start = lambda: self.edit(mission_id)
        self.engine.close_error = RuntimeError("tmux kill failed")
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertIn("PENDING", outcome.detail)
        self.assertNotIn("OBSERVED", outcome.detail)
        start = self.starts(mission_id)[0]
        self.assertFalse(obs(start)["absent"])
        self.assertIn(" state=%s" % broker_module.START_STATE_STOP_PENDING,
                      self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER)[-1])
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID])
        self.assertEqual(len(self.engine.live), 1)
        self.engine.close_error = None
        released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        start = self.starts(mission_id)[0]
        self.assertTrue(mission_state.start_stop_confirmed(start),
                        (obs(start), released.problem, released.detail,
                         self.receipts(workflow_id, broker_module.MISSION_START_RECEIPT_MARKER),
                         self.engine.close_calls, self.engine.live))
        self.assertEqual(self.engine.close_calls, [WORKSPACE_ID, WORKSPACE_ID])
        self.assertEqual(len(self.engine.starts), 1)

    def test_D7_failed_and_unsettled_starts_are_never_retried(self):
        # A returned start error: settled FAILED (not absence proof), a
        # stop pends, the record blocks; a retry refuses.
        mission_id, workflow_id = self.prepared()
        self.engine.start_raises = RuntimeError("tmux refused")
        outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_SPAWN_FAILED)
        start = self.starts(mission_id)[0]
        self.assertEqual(start["settlement"]["outcome"], "failed")
        self.assertTrue(start["settlement"]["stop_pending"])
        self.assertIn("not absence proof", start["settlement"]["stop_reason"])
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_BLOCKED)
        self.engine.start_raises = None
        # The settlement itself fails (source gone after the start): the
        # start stays UNSETTLED; the retry, a restart and a concurrent
        # retry all refuse with ambiguity and invoke nothing.
        second, wf2 = self.prepared()
        real_settle = self.service.settle_engagement_start

        def settle_unavailable(*args, **kwargs):
            raise mission_store.MissionStoreError("disk gone")
        with mock.patch.object(self.service, "settle_engagement_start",
                               settle_unavailable):
            outcome = self.act(wf2, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertEqual(len(self.engine.starts), 2)
        self.assertEqual(self.engine.tasks, [])
        open_start = self.starts(second)[0]
        self.assertIsNone(open_start["settlement"])
        entry = self.record(wf2)
        self.assertEqual(entry["phase"], wa_record.PHASE_BLOCKED)
        # The uncertainty is the CANONICAL unsettled start (above); the
        # record is blocked with the gate's receipt but not marked
        # crash-ambiguous, so the owner's later pass can still settle it
        # (S4d; earlier this asserted the crash marker).
        self.assertEqual(entry["ambiguity"]["state"], wa_record.AMBIGUITY_NONE)
        self.assertIn(gate_module.PROBLEM_START_UNSETTLED,
                      self.receipts(wf2, broker_module.MISSION_BLOCK_RECEIPT_MARKER)[-1])
        cursor_before = self.cursor(second)
        starts_before = len(self.engine.starts)
        for broker in (self.broker, self.gated_broker()):
            again = self.act(wf2, broker_module.ACTION_DISPATCH, broker=broker)
            self.assertFalse(again.ok and again.outcome is None)
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            self.act(wf2, broker_module.ACTION_DISPATCH, broker=self.gated_broker())))
            for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(results), 3)
        self.assertEqual(len(self.engine.starts), starts_before)
        self.assertEqual(self.cursor(second), cursor_before)
        self.assertEqual(len(self.starts(second)), 1)
        self.assertIsNone(self.starts(second)[0]["settlement"])
        # The Runtime never advances it either (no mint, no turn).
        self.assertEqual(runtime_module.claimable_workflows(
            self.store_dir, mission_gate=self.gate), [])
        # A crash with an OPEN start (modelled canonically: the start is
        # admitted and its owner never returns) on a fresh Mission: the
        # next dispatch pass blocks with ambiguity and starts nothing.
        third, wf3 = self.prepared()
        reference = self.record(wf3)[ENGAGEMENT]
        self.op("open_engagement_start", third, reference["engagement_id"], "runtime",
                "dead-owner")
        turns_before = len(self.role_turn.calls)
        outcome = self.act(wf3, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_START_UNSETTLED)
        self.assertEqual(len(self.engine.starts), starts_before)
        self.assertEqual(self.record(wf3)["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(self.record(wf3)["ambiguity"]["state"], wa_record.AMBIGUITY_NONE)
        self.assertEqual(len(self.role_turn.calls), turns_before)
        # Another owner's unsettled start is never settled by this owner's
        # recovery pass: it stays unsettled, nothing is invoked.
        self.act(wf3, broker_module.ACTION_RELEASE, broker=self.gated_broker())
        self.assertIsNone(self.starts(third)[0]["settlement"])
        self.assertEqual(len(self.engine.starts), starts_before)

    def test_D8_the_core_refuses_replay_and_foreign_owners(self):
        mission_id, workflow_id = self.prepared()
        reference = self.record(workflow_id)[ENGAGEMENT]
        eid = reference["engagement_id"]
        ms = mission_state
        # A task start before the runtime start: order refused.
        with self.assertRaises(mission_record.MissionError) as caught:
            self.op("open_engagement_start", mission_id, eid, "task", "o")
        self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_ORDER)
        opened = self.op("open_engagement_start", mission_id, eid, "runtime", "owner-a")
        # The same (engagement, point) never re-opens, for anyone.
        for owner in ("owner-a", "owner-b"):
            with self.assertRaises(mission_record.MissionError) as caught:
                self.op("open_engagement_start", mission_id, eid, "runtime", owner)
            self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_EXISTS)
        # Another owner cannot settle or observe it.
        with self.assertRaises(mission_record.MissionError) as caught:
            self.op("settle_engagement_start", mission_id, opened["start_id"],
                    "owner-b", "completed", None, None)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_OWNER)
        other = mission_record.AuthenticatedContext(
            transport="local",
            principal_kind=mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
            principal_ref="uid:999")
        with self.assertRaises(mission_record.MissionError) as caught:
            self.service.settle_engagement_start(
                mission_id, self.service.mint_state_operation_id(other),
                self.service.get_state(mission_id)["sequence"], opened["start_id"],
                "owner-a", "completed", None, None, other)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_OWNER)
        # Observation before a stop is required refuses; settlement with a
        # stop reason then permits exactly one observation at a time.
        settled = self.op("settle_engagement_start", mission_id, opened["start_id"],
                          "owner-a", "completed",
                          {"workspace_id": "w", "agent_names": ["a"], "task_id": None},
                          None)
        self.assertFalse(settled["stop_pending"])
        with self.assertRaises(mission_record.MissionError) as caught:
            self.op("observe_engagement_stop", mission_id, opened["start_id"],
                    "owner-a", True, "nothing", None)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_STATE)
        with self.assertRaises(mission_record.MissionError) as caught:
            self.op("settle_engagement_start", mission_id, opened["start_id"],
                    "owner-a", "completed", None, None)
        self.assertEqual(caught.exception.problem, ms.PROBLEM_ENGAGEMENT_START_STATE)
        # The task start now opens; settling it uncertain pends a stop
        # even though nothing else changed; the journal replays exactly.
        task = self.op("open_engagement_start", mission_id, eid, "task", "owner-a")
        settled = self.op("settle_engagement_start", mission_id, task["start_id"],
                          "owner-a", "uncertain", None, None)
        self.assertTrue(settled["stop_pending"])
        observed = self.op("observe_engagement_stop", mission_id, task["start_id"],
                           "owner-a", False, "still listed",
                           {"workspace_id": "w", "agent_names": ["a"],
                            "task_id": "t-1"})
        self.assertEqual(mission_state.start_identity(
            mission_state.engagement_start_by_id(
                self.service.get_state(mission_id)["record"], task["start_id"])),
            {"workspace_id": "w", "agent_names": ["a"], "task_id": "t-1"})
        self.assertFalse(observed["stop_confirmed"])
        document = self.mstore.load()
        self.assertEqual(document, json.loads(self.mission_bytes().decode("utf-8")))
        state = self.service.get_state(mission_id)["record"]
        self.assertEqual(len(state["engagement_starts"]), 2)
        as_of = mission_progress.state_as_of(state, opened["sequence"])
        self.assertEqual(len(as_of["engagement_starts"]), 1)
        self.assertIsNone(as_of["engagement_starts"][0]["settlement"])
        # The gate refuses every boundary while the stop is unconfirmed.
        entry = self.record(workflow_id)
        for boundary in gate_module.BOUNDARIES:
            admission = self.gate.admit(entry, boundary)
            self.assertEqual(admission.problem, gate_module.PROBLEM_START_STOP_REQUIRED,
                             boundary)
            self.assertEqual(admission.classification, gate_module.CLASS_TERMINAL)

    def test_D9_source_unavailable_is_reversible_at_every_entry(self):
        mission_id = self.ready_mission()
        workflow_id = self.workflow_row(mission_id)
        good = self.mission_bytes()
        def corrupt():
            with open(self.mstore.path, "w", encoding="utf-8") as handle:
                handle.write("{oops")
        cases = {"corrupt": corrupt}
        if os.geteuid() != 0:
            cases["unreadable"] = lambda: os.chmod(self.mstore.path, 0)
        for label, damage in cases.items():
            damage()
            outcome = self.act(workflow_id, broker_module.ACTION_MATERIALIZE)
            self.assertEqual(outcome.problem, gate_module.PROBLEM_SOURCE_UNAVAILABLE, label)
            self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_HELD, label)
            self.assertTrue(self.consumed(self.presented_capability), label)
            entry = self.record(workflow_id)
            self.assertEqual(entry["phase"], wa_record.PHASE_AUTHORIZED, label)
            self.assertEqual(len(self.receipts(
                workflow_id, broker_module.MISSION_HOLD_RECEIPT_MARKER)), 1, label)
            self.assertEqual(self.transport.calls, [], label)
            # The Runtime pass raises nothing, runs no turn and accepts
            # nothing: every recorded outcome is a refusal or a hold.
            results = runtime_module.process_once(self.broker)
            for _label, recorded in results.get(workflow_id, []):
                self.assertTrue((not recorded.ok) or recorded.outcome
                                == broker_module.OUTCOME_MISSION_HELD, label)
            self.assertEqual(self.role_turn.calls, [], label)
            self.assertEqual(self.record(workflow_id)["phase"],
                             wa_record.PHASE_AUTHORIZED, label)
            bootstrap = self.bootstrap(mission_id)
            self.assertEqual(bootstrap["problem"], gate_module.PROBLEM_SOURCE_UNAVAILABLE, label)
            view = status_module.read_status(self.service, mission_id)["mission"]
            self.assertEqual(view["availability"], status_module.AVAILABILITY_UNAVAILABLE, label)
            os.chmod(self.mstore.path, stat.S_IRUSR | stat.S_IWUSR)
            with open(self.mstore.path, "wb") as handle:
                handle.write(good)
        # Repaired: admitted normally, the hold receipt stays history.
        outcome = self.act(workflow_id, broker_module.ACTION_MATERIALIZE)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        # Saturation of the start records: reversible, zero invocations.
        self.assertTrue(self.act(workflow_id, broker_module.ACTION_PREPARE).ok)
        self.assertTrue(self.act(workflow_id, broker_module.ACTION_VALIDATE_HANDOFF).ok)
        with mock.patch.object(mission_state, "MAX_ENGAGEMENT_START_RECORDS", 0):
            outcome = self.act(workflow_id, broker_module.ACTION_DISPATCH)
        self.assertEqual(outcome.problem, mission_state.PROBLEM_STATE_FULL)
        self.assertEqual(outcome.outcome, broker_module.OUTCOME_MISSION_HELD)
        self.assertEqual(self.engine.starts, [])
        self.assertEqual(self.record(workflow_id)["phase"], wa_record.PHASE_DISPATCHED)
        self.assertEqual(self.starts(mission_id), [])

    def test_D10_follow_up_reserves_once_and_recovers_its_reservation(self):
        mission_id = self.ready_mission()
        workflow_id = self.dispatched(mission_id)
        entry = self.record(workflow_id)
        state = self.service.get_state(mission_id)["record"]
        self.assertEqual(mission_progress.consumed_attempts(state), 0)
        first, refusal = self.gate.reserve_follow_up(entry, 2)
        self.assertIsNone(refusal)
        again, refusal = self.gate.reserve_follow_up(entry, 2)
        self.assertIsNone(refusal)
        self.assertEqual(again, first)
        state = self.service.get_state(mission_id)["record"]
        self.assertEqual(mission_progress.consumed_attempts(state), 1)
        self.assertEqual(len(mission_state.engagements_of(state)), 2)
        third, refusal = self.gate.reserve_follow_up(entry, 3)
        self.assertIsNone(refusal)
        self.assertEqual(mission_progress.consumed_attempts(
            self.service.get_state(mission_id)["record"]), 2)
        exhausted, refusal = self.gate.reserve_follow_up(entry, 4)
        self.assertIsNone(exhausted)
        self.assertEqual(refusal.problem, mission_state.PROBLEM_BUDGET_EXHAUSTED)
        self.assertEqual(refusal.classification, gate_module.CLASS_HOLD)
        # The shared account: a continuation recorded by the Mission side
        # is the same budget the workflow side spends from.
        with self.assertRaises(mission_record.MissionError) as caught:
            self.op("record_continuation", mission_id, "one more")
        self.assertEqual(caught.exception.problem, mission_state.PROBLEM_BUDGET_EXHAUSTED)

    def test_D11_cross_mission_isolation(self):
        a = self.ready_mission()
        b = self.ready_mission()
        wf_a = self.workflow_row(a)
        wf_b = self.workflow_row(b)
        # The other Mission's cancel is a REAL canonical operation (S-V):
        # recorded before the observed window, so the reads below are the
        # Broker action's alone.
        self.service.request_cancel(b)
        reads = []
        self.service.reads = []
        real_get, real_state = self.service.get, self.service.get_state
        with mock.patch.object(self.service, "get",
                               lambda m: reads.append(m) or real_get(m)), \
                mock.patch.object(self.service, "get_state",
                                  lambda m: reads.append(m) or real_state(m)):
            outcome = self.act(wf_a, broker_module.ACTION_MATERIALIZE)
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(set(reads), {a})
        self.assertEqual(set(self.service.reads), {a})
        self.assertEqual(self.record(wf_b)["phase"], wa_record.PHASE_AUTHORIZED)
        self.assertEqual(self.starts(b), [])


# ====================================================================
# E. The Grok relay
# ====================================================================


from test_grok_mcp import MissionFixture, make_controller  # noqa: E402


class GrokRelayTests(MissionFixture):

    def test_E1_dispatch_tool_refuses_while_unwired(self):
        client, controller, operator = self.wired()
        structured = self.structured(client, "di_mission_dispatch",
                                     {"mission_id": "mn-" + "0" * 32})
        self.assertFalse(structured["ok"])
        self.assertEqual(structured["status"], "refused")
        self.assertIn("not wired", structured["reason"])
        self.assertIsNone(self.store_bytes())

    def test_E2_dispatch_tool_refuses_an_unknown_mission_with_zero_effects(self):
        # Wired to the real bootstrap with the REAL predicate true (slice
        # S-V): an unknown Mission refuses with zero effects — no Mission
        # store write, no workflow store.
        with tempfile_directory() as workflow_dir:
            layer = engineering_module.MissionControl(
                self.service, workflow_dir, os.path.realpath(self.tmp.name))
            controller, operator = make_controller(
                None, mission_service=self.service, engagement_bootstrap=layer.dispatch)
            client = self.serve(controller)
            self.assertEqual(client.initialize()[0], 200)
            structured = self.structured(client, "di_mission_dispatch",
                                         {"mission_id": "mn-" + "0" * 32})
            self.assertFalse(structured["ok"])
            self.assertEqual(structured["problem"],
                             engineering_module.PROBLEM_UNKNOWN_MISSION)
            self.assertEqual(structured["missing_guards"], [])
            self.assertIsNone(self.store_bytes())
            self.assertEqual(os.listdir(workflow_dir), [])


import contextlib  # noqa: E402
import tempfile  # noqa: E402


@contextlib.contextmanager
def tempfile_directory():
    with tempfile.TemporaryDirectory() as directory:
        yield directory


if __name__ == "__main__":
    unittest.main()
