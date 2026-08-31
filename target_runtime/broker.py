"""The narrow Target Broker: fixed deterministic lifecycle actions.

Every action's signature is exactly ``(workflow_id, action,
revision)`` — plan D-3 made structural: paths, remote URLs, baselines
and handoff bytes are resolved by the Broker FROM the protected
workflow record; a caller has no parameter through which to supply a
sensitive value. Unknown actions fail closed.

Every ``perform`` runs the SAME fail-closed gate before any effect,
in a fixed order (each with its own problem code, the eleven-case
adversarial matrix's Runtime seam):

unknown action -> unknown workflow -> tampered record (closed-schema
re-validation of RECOVERED DURABLE STATE, which is adversarial
input; validate_record's TOTAL render binding refuses any field
altered independently of the digested text — wrong-revision,
altered-baseline, either target form edited into the other, altered
human intent or authority sections) -> control mismatch -> LIVE
policy-digest drift (D-9: the digest is re-computed from the control
repository's actual bytes on every perform) -> stale revision
argument -> approval not consumed / not approved / superseded
(pre-approval) -> consumption outside the approval validity
(expired) -> crash ambiguity -> wrong phase for the action (replay).
A refusal writes NOTHING: no store save, no workspace touch, no
subprocess, no Codex turn.
"""

import os

from codex_gateway.role_turn import ROLE_TURN_COMPLETED
from telegram_operator.protocol import (
    MAX_OUTCOME_DETAIL_CHARS,
    OUTCOME_NEEDS_REAUTHORIZATION,
    OUTCOME_REQUEST_DISPATCH,
    OUTCOME_REQUEST_FOLLOW_UP,
    OUTCOME_VERIFIED_RESULT,
)
from workflow_authority import record as record_module
from workflow_authority import store as store_module
from workflow_authority.digest import (
    DigestError,
    control_policy_digest,
    text_digest,
)

# Target-Herdr *lifecycle* statuses that mean the engineering task has
# STOPPED and there is an outcome to verify — the trigger for the
# verification turn.
#
# Read from herdr.observe's task["status"] projection (the task
# LIFECYCLE), NOT task["state"], which is FILE READABILITY of
# task.json ("available"/"missing"/"malformed") — a distinct closed
# vocabulary that says nothing about whether the target finished.
#
# The value set is herd's OWN stopped set, NOT a hand-picked subset:
# herdr/tasks.py:286 treats {"COMPLETE", "ABORTED", "ERROR"} alike as
# "the prior task has stopped" before starting new work. Each is a
# real write site:
#   COMPLETE  herdctl `complete` (herdctl.py) — finished successfully;
#   ABORTED   herdctl `abort`    (herdctl.py) — a human stopped it;
#   ERROR     herdr/tasks.py     — the task failed.
# All three are stopped and hand the outcome to the verification turn,
# which adjudicates success vs. a corrective follow-up vs. a durable
# re-authorization — none may be left waiting. The running/pre-start
# states IDLE and ACTIVE are the ONLY non-terminal values: the
# workflow WAITS with zero store write. No status is deliberately
# excluded; every value herd can write is either terminal here or a
# legitimate wait. A contract test (tests/test_target_runtime.py)
# DERIVES this set from herd's own source (herdr/tasks.py) and drives
# the real herdr.observe over EVERY status, so an omission — herd
# adding a stopped status, or this pair narrowing — fails the suite
# instead of stranding a workflow in production.
_TARGET_TERMINAL_STATUSES = ("COMPLETE", "ABORTED", "ERROR")

# herdr.observe's `completeness` is visibility-only ("COMPLETE" when
# every consulted source was cleanly observed, "PARTIAL" when any was
# malformed/unreadable/unavailable). Since I3 of task 20260826-113247
# the completion decision is SOURCE-SCOPED per ruling R-6 (see
# `_observation_context`): a demoting diagnostic in a CONSUMED source
# is a WAIT, never a finish, while a production observation that is
# globally PARTIAL only because agents are unprobed still advances.
# The global value itself remains recorded/rendered raw everywhere.


def _bounded_detail(result):
    """The verification turn's detail string, bounded (never a raw
    unbounded value into a stored summary)."""
    detail = getattr(result, "detail", None)
    if isinstance(detail, str):
        return detail[:MAX_OUTCOME_DETAIL_CHARS]
    return None

from workflow_authority import canonical as canonical_module

from target_runtime import capability as capability_module
from target_runtime import dispatch as dispatch_module
from target_runtime import evidence as evidence_module
from target_runtime import prepare as prepare_module
from target_runtime import ownership as ownership_module
from target_runtime import readiness as readiness_module
from target_runtime import workspace as workspace_module
from target_runtime import workspace_trust as workspace_trust_module

ACTION_MATERIALIZE = "materialize_workspace"
ACTION_PREPARE = "prepare"
ACTION_VALIDATE_HANDOFF = "validate_handoff"
ACTION_DISPATCH = "dispatch"
ACTION_VERIFY = "verify"
ACTION_FOLLOW_UP = "dispatch_follow_up"
ACTION_COMPLETE = "complete"
ACTION_RELEASE = "release_workspace"
ACTION_RECONCILE = "reconcile_dispatch"
BROKER_ACTIONS = (
    ACTION_MATERIALIZE,
    ACTION_PREPARE,
    ACTION_VALIDATE_HANDOFF,
    ACTION_DISPATCH,
    ACTION_VERIFY,
    ACTION_FOLLOW_UP,
    ACTION_COMPLETE,
    ACTION_RELEASE,
    ACTION_RECONCILE,
)

# The phase each action requires; anything else is a replay or an
# out-of-order operation and fails closed. Dispatch requires
# VALIDATED (cleared to dispatch); a second `dispatch` on a
# DISPATCHED workflow is a wrong-phase refusal — double dispatch is
# structurally impossible. Verify and follow-up require DISPATCHED;
# complete requires VERIFIED.
_REQUIRED_PHASE = {
    ACTION_MATERIALIZE: record_module.PHASE_AUTHORIZED,
    ACTION_PREPARE: record_module.PHASE_WORKSPACE_READY,
    ACTION_VALIDATE_HANDOFF: record_module.PHASE_PREPARED,
    ACTION_DISPATCH: record_module.PHASE_VALIDATED,
    ACTION_VERIFY: record_module.PHASE_DISPATCHED,
    ACTION_FOLLOW_UP: record_module.PHASE_DISPATCHED,
    ACTION_COMPLETE: record_module.PHASE_VERIFIED,
    ACTION_RELEASE: None,  # release is phase-checked in its handler
    ACTION_RECONCILE: record_module.PHASE_DISPATCHED,
}


def _production_observer(lease_repo):
    """The production read-only Herdr observation (I5 D2).

    Wired lazily so target_runtime keeps its single herdr import at
    the dispatch bridge; `herdr.observe` is the read-only projection.
    """
    from herdr.observe import observe
    return observe(lease_repo, probe_agents=False)


def _production_readiness_probe(lease_repo):
    """The production BOOTSTRAP-READINESS probe (I3).

    Read-only and deliberately SEPARATE from `_production_observer`:
    that observation runs with `probe_agents=False`, so its `agents`
    section carries no live agent facts, and widening it would both
    reproduce the recorded agents-unprobed PARTIAL defect and loosen
    `_probe_one`'s allowlist on a path that feeds a model. This probe
    instead reads the leased workspace's own runtime mapping and the
    live agent registry, and returns `{logical: agent_record}` for the
    logical roles that mapping names.

    Within this function a target it is unable to see yields None,
    which the readiness layer records as UNOBSERVABLE rather than as a
    failure; outside it, what that absence means is the readiness
    layer's decision and not this probe's.
    """
    import json
    import os
    state = os.path.join(lease_repo, ".herd", "state", "runtime.json")
    try:
        with open(state, encoding="utf-8") as handle:
            agents = json.load(handle).get("agents")
    except Exception:                                     # noqa: BLE001
        return None
    if not isinstance(agents, dict):
        return None
    from herdr.tasks import run
    completed = run(["herdr", "agent", "list"])
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        listed = json.loads(completed.stdout)["result"]["agents"]
    except Exception:                                     # noqa: BLE001
        return None
    by_name = {
        record.get("name"): record
        for record in listed
        if isinstance(record, dict)
    }
    probe = {}
    for logical, name in agents.items():
        record = by_name.get(name)
        if record is not None:
            probe[logical] = record
    return probe


def _production_live_workspaces():
    """READ-ONLY projection of live workspaces and the agents in them.

    Built by JOINING two Herdr listings, because neither alone
    carries what the ownership proof needs: `herdr workspace list`
    reports `workspace_id`, `label` and pane counts and has NO agent
    mapping, while `herdr agent list` reports each agent's `name` and
    its `workspace_id`. The join therefore yields, per workspace, the
    SET OF AGENT NAMES currently in it.

    Read-only in the strict sense: it lists and it returns: no
    workspace, pane or session is created, focused, renamed or closed
    here. Returns None when either listing is unreadable, which the
    proof treats as degraded evidence and refuses on.
    """
    import json
    from herdr.tasks import run
    try:
        listed = run(["herdr", "workspace", "list"])
        agents = run(["herdr", "agent", "list"])
    except Exception:                                     # noqa: BLE001
        return None
    if getattr(listed, "returncode", 1) != 0:
        return None
    if getattr(agents, "returncode", 1) != 0:
        return None
    try:
        workspaces = json.loads(
            listed.stdout
        )["result"]["workspaces"]
        agent_rows = json.loads(agents.stdout)["result"]["agents"]
    except Exception:                                     # noqa: BLE001
        return None
    # R-32 X-1: # A ROW THIS IS UNABLE TO READ MAKES THE WHOLE PROJECTION DEGRADED.
    # Skipping it is outside what this function may do.
    #
    # Skipping looks harmless and is not. The ownership proof compares
    # the live agent NAME SET against the recorded one for EQUALITY,
    # so its strength rests entirely on that set being COMPLETE. A
    # silent skip makes it a possibly-strict subset, and while the
    # usual consequence is a false refusal, the dangerous one is a
    # truncated set that happens to EQUAL the recorded one — which
    # reports OWNED and closes a workspace containing live agents
    # nobody recorded. On a machine of fifteen workspaces, one ours,
    # that is unrecoverable.
    #
    # So this returns None — the degraded value the consumer refuses
    # on — rather than a narrower answer presented as a complete one.
    # # `observe_spawn_records` already takes this posture for the same
    # reason: within it a malformed record makes the WHOLE projection
    # malformed.
    by_workspace = {}
    for row in agent_rows:
        if not isinstance(row, dict):
            return None
        workspace_id = row.get("workspace_id")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            return None
        if workspace_id is not None and not isinstance(
            workspace_id, str
        ):
            return None
        if isinstance(workspace_id, str):
            by_workspace.setdefault(workspace_id, set()).add(name)
    projection = []
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            return None
        workspace_id = workspace.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            return None
        projection.append({
            "workspace_id": workspace_id,
            "agent_names": by_workspace.get(workspace_id, set()),
        })
    return projection


def _production_spawn_records_observer(control_repo):
    """The production read-only control-repository spawn projection."""
    from herdr.observe import observe_spawn_records
    return observe_spawn_records(control_repo)

#: I5: a release that completed what it could PROVE it owned while
#: leaving unprovable resources untouched. Distinct from a plain
#: release so /status can tell a complete cleanup from a partial one
#: without parsing prose.
OUTCOME_RELEASED_DEGRADED = "released_degraded"

#: Fixed marker for the cleanup receipt, so a reader of the durable
#: record can find what a release actually removed.
CLEANUP_RECEIPT_MARKER = "workflow cleanup"

PROBLEM_UNKNOWN_ACTION = "broker_unknown_action"
PROBLEM_UNKNOWN_WORKFLOW = "broker_unknown_workflow"
PROBLEM_STORE_UNREADABLE = "broker_store_unreadable"
PROBLEM_RECORD_INVALID = "broker_record_invalid"
PROBLEM_WRONG_CONTROL = "broker_wrong_control_repository"
PROBLEM_POLICY_DRIFT = "broker_policy_digest_drift"
PROBLEM_STALE_REVISION = "broker_stale_revision"
PROBLEM_NOT_AUTHORIZED = "broker_approval_not_consumed"
PROBLEM_NOT_APPROVED = "broker_decision_not_approve"
PROBLEM_SUPERSEDED = "broker_approval_superseded"
PROBLEM_EXPIRED = "broker_consumption_outside_validity"
PROBLEM_CRASH_AMBIGUOUS = "broker_crash_ambiguous"
PROBLEM_WRONG_PHASE = "broker_wrong_phase_for_action"
PROBLEM_TURN_NOT_COMPLETED = "broker_validation_turn_not_completed"
PROBLEM_SPAWN_FAILED = "broker_spawn_failed"
PROBLEM_FOLLOW_UP_BOUND = "broker_follow_up_bound_reached"
PROBLEM_INSTRUCTIONS_DRIFTED = "broker_instructions_drifted"
PROBLEM_VERIFICATION_UNSUPPORTED = "broker_verification_bad_outcome"
PROBLEM_NO_VERIFIED_RESULT = "broker_no_verified_result"
PROBLEM_SURFACE_UNAVAILABLE = "broker_surface_digest_refused"

# --- I5 revision 1: record-growth containment (round-10 F-1) ---------------
#
# Every action handler can grow the record and save; at a hard
# record bound the validator refuses the grown document and
# store.save raises. `perform` contains that raise and stops the
# ONE affected workflow durably instead of killing the Runtime.
# Two truthful codes (the register of I4's
# runtime_codex_turn_capacity_exhausted — the store is readable;
# the RECORD is the problem):
#   - capacity_exhausted: the validator refused on a hard bound
#     (its own PROBLEM_TOO_LARGE message format, pinned by test);
#   - record_unsavable: the grown record was refused for any other
#     reason (never mislabeled as capacity — the wrong-field
#     class).
PROBLEM_RECORD_CAPACITY_EXHAUSTED = "broker_record_capacity_exhausted"
PROBLEM_RECORD_UNSAVABLE = "broker_record_unsavable"
# The validator's own PROBLEM_TOO_LARGE message fragment (every
# hard-bound refusal in workflow_authority/record.py renders it);
# pinned against the real validator by test.
HARD_BOUND_MESSAGE_MARKER = "; the hard bound is "
OUTCOME_RECORD_GROWTH_BLOCKED = "record_growth_blocked"

# --- I5: reconcile_dispatch (ruling R-3 / D-B3) ----------------------------
#
# Bind EXACTLY ONE provable existing child, or BLOCK durably. The
# action reads NOTHING outside this repository: the child evidence
# is the CONTROL repository's own recorded children (via the
# injected read-only observer), the identity proof is the LEASED
# workspace's own observation, and the global Herdr registry is
# off limits — the deterministic alias is a DERIVED EXPECTATION
# label only, never binding evidence (herd's own child records
# carry no alias at all). More BLOCKED outcomes are the accepted
# cost: a BLOCKED a human resolves is correct behaviour.

# A refusal (nothing written; the workflow stays DISPATCHED):
PROBLEM_RECONCILE_ALREADY_BOUND = "broker_reconcile_already_bound"

# Durable BLOCKED causes (ruling R-3: zero, multi, conflicting,
# truncated, or degraded all stop durably) — the closed set the
# fail-closed matrix derives its rows from.
PROBLEM_RECONCILE_NO_MATCH = "broker_reconcile_no_match"
PROBLEM_RECONCILE_MULTIPLE = "broker_reconcile_multiple_matches"
PROBLEM_RECONCILE_CONFLICT = "broker_reconcile_conflicting_identity"
PROBLEM_RECONCILE_TRUNCATED = "broker_reconcile_children_truncated"
PROBLEM_RECONCILE_DEGRADED = "broker_reconcile_observation_degraded"
RECONCILE_BLOCK_CODES = (
    PROBLEM_RECONCILE_NO_MATCH,
    PROBLEM_RECONCILE_MULTIPLE,
    PROBLEM_RECONCILE_CONFLICT,
    PROBLEM_RECONCILE_TRUNCATED,
    PROBLEM_RECONCILE_DEGRADED,
)

# The internal outcome tokens for the reconcile action (Runtime-
# facing status words, never protocol outcomes).
OUTCOME_RECONCILED = "reconciled"
OUTCOME_RECOVERY_BLOCKED = "recovery_blocked"

# Fixed marker for the durable recovery-block receipt — SCOPED TO
# THIS ACTION (the same per-action pattern as
# VERIFICATION_BLOCK_MARKER; deliberately NOT a universal stop-
# reason mechanism, which is the deferred I3b). The adapter
# duplicates it (it may not import target_runtime); pinned equal by
# a cross-boundary test. BLOCKED is terminal, so at most one such
# receipt can ever exist per workflow.
RECOVERY_BLOCK_MARKER = "recovery blocked"

# --- I3: the fail-closed verified_result gates -----------------------------
#
# `verified_result` from the fresh Codex verification turn is
# NECESSARY, NEVER SUFFICIENT. Before anything is recorded, the
# Broker independently applies the D-A4 conjunctive gates against a
# FRESH evidence collection (a fresh disk read through the same
# injected seams). Each gate carries its OWN problem code; one
# failing conjunct refuses `verified_result` and the workflow stops
# DURABLY (BLOCKED with the reason recorded as a fixed-marker
# receipt) — never an indefinite re-poll, never a silent strand.
# Herd lifecycle COMPLETE alone can never produce VERIFIED by
# construction: it is one conjunct of eight.
#
# Wording rule (I3 binding item 6): the canonical Reviewer APPROVE
# conjunct is TARGET-PRODUCED evidence — the child engine's own
# reviewer wrote that artifact inside the leased workspace. It is
# evidence that the target's review process ran and concluded,
# never independent verification.

PROBLEM_VERIFY_EVIDENCE_INCOMPLETE = (
    "broker_verification_evidence_incomplete"
)
PROBLEM_VERIFY_EVIDENCE_INVALID = (
    "broker_verification_evidence_invalid"
)
PROBLEM_VERIFY_TARGET_NOT_STOPPED = (
    "broker_verification_target_not_stopped"
)
PROBLEM_VERIFY_REVIEW_NOT_APPROVE = (
    "broker_verification_review_not_approve"
)
PROBLEM_VERIFY_ORIGIN_MISMATCH = (
    "broker_verification_origin_mismatch"
)
PROBLEM_VERIFY_BASELINE_MOVED = (
    "broker_verification_baseline_moved"
)
PROBLEM_VERIFY_POLICY_DRIFT = (
    "broker_verification_policy_drift"
)
PROBLEM_VERIFY_SURFACE_BASELINE_MISSING = (
    "broker_verification_surface_baseline_missing"
)
PROBLEM_VERIFY_SURFACE_DRIFT = (
    "broker_verification_surface_drift"
)
PROBLEM_VERIFY_DELIVERY_AUTHORITY = (
    "broker_verification_delivery_authority"
)

# Every problem code the verification gates can emit — the complete
# closed set the table-driven refusal matrix derives its rows from
# (a new gate code without a matrix row fails the suite). The eight
# D-A4 conjuncts map to ten codes: conjunct 1 (evidence complete AND
# schema-valid) and conjunct 7 (surface receipt present AND byte
# equal) each split into two.
VERIFICATION_GATE_CODES = (
    PROBLEM_VERIFY_EVIDENCE_INCOMPLETE,
    PROBLEM_VERIFY_EVIDENCE_INVALID,
    PROBLEM_VERIFY_TARGET_NOT_STOPPED,
    PROBLEM_VERIFY_REVIEW_NOT_APPROVE,
    PROBLEM_VERIFY_ORIGIN_MISMATCH,
    PROBLEM_VERIFY_BASELINE_MOVED,
    PROBLEM_VERIFY_POLICY_DRIFT,
    PROBLEM_VERIFY_SURFACE_BASELINE_MISSING,
    PROBLEM_VERIFY_SURFACE_DRIFT,
    PROBLEM_VERIFY_DELIVERY_AUTHORITY,
)

# The internal broker-outcome token for a durably BLOCKED
# verification (like "target_running", it is a Runtime-facing status
# word, never a protocol outcome).
OUTCOME_VERIFICATION_BLOCKED = "verification_blocked"

# Fixed marker for the durable verification-block receipt; the
# Telegram adapter renders it for a BLOCKED workflow and duplicates
# this string (it may not import target_runtime) — pinned equal by a
# cross-boundary test.
VERIFICATION_BLOCK_MARKER = "verification blocked"


def _binding(projection, name):
    return projection["bindings"][name]


def _gate_evidence_complete(entry, projection):
    if projection.get("completeness") != (
        evidence_module.PROJECTION_COMPLETE
    ):
        failing = sorted({
            diagnostic.get("binding")
            for diagnostic in projection.get("diagnostics", [])
            if isinstance(diagnostic, dict)
        })
        return (
            PROBLEM_VERIFY_EVIDENCE_INCOMPLETE,
            "the evidence projection is PARTIAL (unresolved"
            " bindings: %s); the target is stopped, so there is"
            " nothing left to wait for" % ", ".join(
                str(name) for name in failing
            ),
        )
    return None, None


def _gate_evidence_valid(entry, projection):
    try:
        evidence_module.validate_projection(projection)
    except evidence_module.EvidenceError as exc:
        return (
            PROBLEM_VERIFY_EVIDENCE_INVALID,
            "the evidence projection failed schema validation"
            " (%s: %s)" % (exc.problem, exc),
        )
    return None, None


def _gate_target_stopped(entry, projection):
    binding = _binding(projection, "target_task")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["task_status"] not in _TARGET_TERMINAL_STATUSES
    ):
        return (
            PROBLEM_VERIFY_TARGET_NOT_STOPPED,
            "the target task lifecycle status %r is not a stopped"
            " status" % (binding["task_status"],),
        )
    return None, None


def _gate_review_approved(entry, projection):
    binding = _binding(projection, "review_decision")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["decision"] != "APPROVE"
    ):
        return (
            PROBLEM_VERIFY_REVIEW_NOT_APPROVE,
            "the target-produced canonical review record does not"
            " conclude APPROVE (decision %r) — this record is"
            " evidence that the target's own review process ran and"
            " concluded, never independent verification, and without"
            " it the mission is not verified"
            % (binding["decision"],),
        )
    return None, None


def _gate_origin_identity(entry, projection):
    binding = _binding(projection, "live_origin")
    if binding["status"] != evidence_module.BINDING_EXACT:
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live target origin could not be read exactly",
        )
    try:
        live = canonical_module.canonicalize_repository_url(
            binding["url"]
        )
    except canonical_module.CanonicalizationError as exc:
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live target origin URL does not canonicalize"
            " (%s)" % exc,
        )
    approved = canonical_module.canonicalize_repository_url(
        entry["target"]["canonical_url"]
    )
    if canonical_module.repository_identity_key(live) != (
        canonical_module.repository_identity_key(approved)
    ):
        return (
            PROBLEM_VERIFY_ORIGIN_MISMATCH,
            "the live workspace origin does not name the approved"
            " repository identity (case-folded identity comparison,"
            " never URL string equality)",
        )
    return None, None


def _gate_baseline_unmoved(entry, projection):
    binding = _binding(projection, "baseline_match")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["match"] is not True
    ):
        return (
            PROBLEM_VERIFY_BASELINE_MOVED,
            "the live target HEAD does not equal the approved"
            " baseline commit",
        )
    return None, None


def _gate_control_policy(entry, projection):
    binding = _binding(projection, "control_policy")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["match"] is not True
    ):
        return (
            PROBLEM_VERIFY_POLICY_DRIFT,
            "the LIVE control policy digest does not match the one"
            " this workflow was authorized under",
        )
    return None, None


def _gate_surface_baseline_present(entry, projection):
    if dispatch_module.surface_baseline_digest(entry) is None:
        return (
            PROBLEM_VERIFY_SURFACE_BASELINE_MISSING,
            "no dispatch-time protected-surface baseline receipt"
            " exists (this workflow was dispatched before the"
            " baseline was introduced); verification fails closed"
            " rather than fabricating or retro-fitting one — a fresh"
            " Mission Authorization is required",
        )
    return None, None


def _gate_surface_unchanged(entry, projection):
    baseline = dispatch_module.surface_baseline_digest(entry)
    binding = _binding(projection, "protected_surface")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        baseline is None or binding["digest"] != baseline
    ):
        return (
            PROBLEM_VERIFY_SURFACE_DRIFT,
            "the LIVE protected control-surface digest does not"
            " byte-match the dispatch-time baseline receipt; the"
            " control machinery may have changed during target"
            " execution",
        )
    return None, None


def _gate_delivery_authority(entry, projection):
    binding = _binding(projection, "delivery_authority")
    if binding["status"] != evidence_module.BINDING_EXACT or (
        binding["value"] != "none"
    ):
        return (
            PROBLEM_VERIFY_DELIVERY_AUTHORITY,
            "delivery_authority is %r; it must be exactly 'none'"
            % (binding["value"],),
        )
    return None, None


# The ORDERED gate registry. Evaluation order is fixed (the evidence
# shape gates run first because every later gate reads bindings);
# each gate is INDEPENDENT: it has its own problem code(s) and any
# single failure refuses verified_result. DO NOT REORDER the first
# two entries: the validity gate running first is what makes every
# later gate's `projection["bindings"][name]` subscript safe on the
# fresh collection (round-06 N-3).
_VERIFICATION_GATES = (
    ("evidence_complete", _gate_evidence_complete),
    ("evidence_valid", _gate_evidence_valid),
    ("target_stopped", _gate_target_stopped),
    ("review_approved", _gate_review_approved),
    ("origin_identity", _gate_origin_identity),
    ("baseline_unmoved", _gate_baseline_unmoved),
    ("control_policy", _gate_control_policy),
    ("surface_baseline_present", _gate_surface_baseline_present),
    ("surface_unchanged", _gate_surface_unchanged),
    ("delivery_authority", _gate_delivery_authority),
)


class BrokerOutcome(object):
    """Result of one broker action; refusals never raise."""

    def __init__(self, ok, problem=None, detail=None, phase=None,
                 outcome=None):
        self.ok = ok
        self.problem = problem
        self.detail = detail
        self.phase = phase
        self.outcome = outcome


def _refused(problem, detail=None):
    return BrokerOutcome(False, problem=problem, detail=detail)


class TargetBroker(object):
    """One Broker: one store directory, one control repository, one
    injected git transport, one injected role-turn runner."""

    def __init__(self, store_directory, control_repository_realpath,
                 transport, workspaces_root, role_turn_fn,
                 claude_config_path,
                 spawn_fn=None, clock=None, observer_fn=None,
                 spawn_records_fn=None, readiness_probe_fn=None,
                 workspace_close_fn=None, live_workspaces_fn=None):
        import time
        self.store = store_module.WorkflowStore(store_directory)
        self.control_realpath = control_repository_realpath
        self.transport = transport
        self.workspaces_root = workspaces_root
        # The user-global Claude configuration whose ONE trust key a
        # freshly materialized workspace needs before an unattended
        # interactive Herdr can start in it (I1). REQUIRED rather
        # than defaulted, within this constructor: a Broker that
        # silently resolved the real ~/.claude.json would make a
        # hermetic test a writer of the developer's own
        # configuration. Outside that, a caller may still pass the
        # real path deliberately — which is what production does. Production wires it in
        # target_runtime.cli; tests inject a temp path.
        self.claude_config_path = claude_config_path
        # The handoff-validation Codex turn (I2 role_turn), injected
        # so hermetic tests never spawn a process; production wires
        # codex_gateway.role_turn.run_role_turn.
        self._role_turn = role_turn_fn
        # The child-spawn bridge (I5), injected the same way;
        # production wires dispatch.production_spawn (the EXISTING
        # herdr orchestrator bridge — no parallel path).
        self._spawn = spawn_fn or dispatch_module.production_spawn
        self._clock = clock or time.time
        # The canonical read-only Herdr observation (I5 D2), injected
        # so hermetic tests never touch a real target tree; production
        # wires herdr.observe. Reconciliation uses it for the LEASED
        # workspace's independently observed task identity.
        self._observe = observer_fn or _production_observer
        # A distinct narrow, read-only seam for ALL spawn records
        # persisted by the CONTROL repository. It never observes or
        # follows a child repository and does not alter canonical
        # observe()["children"] current-task correlation semantics.
        self._spawn_records = (
            spawn_records_fn or _production_spawn_records_observer
        )
        # The I3 bootstrap-readiness probe: a read-only seam consulted
        # ONLY inside the pre-readiness window of a DISPATCHED
        # workflow. Optional with a production default, following
        # `observer_fn` rather than `claude_config_path`, because this
        # seam is a reader within its own scope: the argument for
        # making the config path required was that a defaulted one
        # would make a test a WRITER of the developer's configuration,
        # and that argument does not carry over to a reader.
        self._readiness_probe = (
            readiness_probe_fn or _production_readiness_probe
        )
        # DOMAIN B (R-29 / R-30 V-5): terminal cleanup of the Herdr
        # WORKSPACE a completed workflow leaves behind, and the
        # long-lived agent sessions inside it.
        #
        # `workspace_close_fn` DEFAULTS TO NONE, and None means this
        # Broker has NO capability to close a workspace: # it proves ownership and reports, and closes no workspace. There is
        # deliberately no default reaching the real
        # `herdr workspace close`. On a machine carrying fifteen
        # workspaces of which one is ours, a mis-scoped close destroys
        # other people's live sessions and is unrecoverable in a way a
        # leaked helper process is not, so the capability is handed in
        # on purpose or it is absent.
        self._workspace_close = workspace_close_fn
        self._live_workspaces = live_workspaces_fn

    # -- the fail-closed gate ------------------------------------------

    def _gate(self, workflows, workflow_id, action, revision):
        entry = workflows["workflows"].get(workflow_id)
        if entry is None:
            return None, _refused(PROBLEM_UNKNOWN_WORKFLOW)
        try:
            record_module.validate_record(entry)
        except record_module.RecordError as exc:
            # validate_record's TOTAL render binding (byte-equality
            # of the stored rendered text with the deterministic
            # rendering of the record's own fields) refuses every
            # field-vs-text tamper here; the former per-line
            # containment check was subsumed by it and removed as
            # dead code.
            return None, _refused(PROBLEM_RECORD_INVALID, str(exc))
        if entry["control_identity"]["repository_realpath"] != (
            self.control_realpath
        ):
            return None, _refused(
                PROBLEM_WRONG_CONTROL,
                "record names control %r; this Runtime is pinned to"
                " %r" % (
                    entry["control_identity"]["repository_realpath"],
                    self.control_realpath,
                ),
            )
        # THE CONTROL-POLICY DIGEST, AND THE TWO CONDITIONS THAT MUST
        # NEVER BE CONFLATED (R-07).
        #
        # "Cannot compute the digest" and "computed it and it does not
        # match" are DIFFERENT FACTS, and only the second one is
        # drift. They are written as structurally separate branches
        # here, each reaching ONE explicit outcome, precisely so that
        # a DigestError can never fall through and read as "the digest
        # matched". An earlier shape shared a `live_digest = None`
        # fall-through between them; that is safe for ACTION_VERIFY
        # only by accident of a downstream re-imposition, and would be
        # a strictly LARGER hole for ACTION_RELEASE, which has none.
        digest_error = None
        live_digest = None
        try:
            live_digest = control_policy_digest(self.control_realpath)
        except DigestError as exc:
            digest_error = exc

        if digest_error is not None:
            # CONDITION 1 — THE POLICY SURFACE CANNOT BE READ AT ALL.
            #
            # This is NOT byte drift, and it is MORE severe than
            # drift, not less: drift means we can see the surface and
            # it changed; this means we cannot see it. The operator's
            # objective authorizes unstranding cleanup blocked solely
            # by policy BYTE DRIFT, and a DigestError is not byte
            # drift. So every action is refused here EXCEPT
            # ACTION_VERIFY.
            #
            # ACTION_VERIFY alone continues, and ONLY because the
            # verification precheck chain re-imposes the policy
            # comparison downstream via `_gate_control_policy`, where
            # it stops the workflow DURABLY after a fresh observation.
            # ACTION_RELEASE has NO such downstream re-imposition — it
            # goes straight to `_release` — which is exactly why it is
            # refused here rather than sharing this branch. Do not add
            # it: the exemption below covers a mismatched digest, not
            # an unreadable one.
            if action != ACTION_VERIFY:
                return None, _refused(
                    PROBLEM_POLICY_DRIFT, str(digest_error)
                )
        elif live_digest != entry["control_identity"][
            "policy_digest_sha256"
        ]:
            # CONDITION 2 — THE DIGEST COMPUTED, AND MISMATCHED.
            #
            # True byte drift of a READABLE policy surface. This, and
            # only this, is what the two exemptions defer.
            #
            # ACTION_VERIFY (preserved) and ACTION_RELEASE (R-03) are
            # named by EXACT EQUALITY, one action each — deliberately
            # not a phase predicate and not a set, so the exemption
            # cannot widen by someone adding a member. It defers
            # EXACTLY ONE CONJUNCT, this digest comparison, and
            # nothing else: workflow identity was already enforced
            # ABOVE this branch and is not exempted; revision,
            # approval (superseded / consumed / decision / validity)
            # and ambiguity are enforced BELOW it and are not
            # exempted; and `_release` still re-checks the terminal
            # phase, the recorded lease realpath, proven ownership
            # (UNPROVABLE refuses and removes nothing), ambiguity and
            # idempotent re-entry for itself.
            #
            # Why RELEASE at all: release closes only PROVEN-OWNED
            # resources, and a terminal workflow stranded by drift can
            # otherwise NEVER be released — its workspace, trust key,
            # sessions and scope records are stranded forever, while
            # `process_once` re-mints a capability for it on every
            # poll. Drift is repairable; permanent stranding is not.
            if action != ACTION_VERIFY and action != ACTION_RELEASE:
                return None, _refused(
                    PROBLEM_POLICY_DRIFT,
                    "the LIVE control policy digest does not match"
                    " the one this workflow was authorized under; the"
                    " policy surface drifted between authorization"
                    " and use",
                )
        # else: the digest computed AND matched — nothing is deferred
        # for any action, exempt or not.
        if revision != entry["handoff"]["revision"]:
            return None, _refused(
                PROBLEM_STALE_REVISION,
                "caller revision %r is not the record's handoff"
                " revision %r" % (
                    revision, entry["handoff"]["revision"],
                ),
            )
        approval = entry["approval"]
        if approval["superseded"]:
            return None, _refused(PROBLEM_SUPERSEDED)
        if approval["consumed_at"] is None:
            return None, _refused(
                PROBLEM_NOT_AUTHORIZED,
                "the one-shot approval was never consumed",
            )
        if approval["decision"] != record_module.DECISION_APPROVE:
            return None, _refused(PROBLEM_NOT_APPROVED)
        if approval["consumed_at"] > approval["expires_at"]:
            return None, _refused(
                PROBLEM_EXPIRED,
                "consumption recorded at %r is outside the approval"
                " validity ending %r" % (
                    approval["consumed_at"], approval["expires_at"],
                ),
            )
        if entry["ambiguity"]["state"] != record_module.AMBIGUITY_NONE:
            return None, _refused(
                PROBLEM_CRASH_AMBIGUOUS,
                "ambiguity state is %r (%s); a crash-ambiguous"
                " workflow is never advanced" % (
                    entry["ambiguity"]["state"],
                    entry["ambiguity"]["detail"],
                ),
            )
        required_phase = _REQUIRED_PHASE[action]
        if required_phase is not None and entry["phase"] != (
            required_phase
        ):
            return None, _refused(
                PROBLEM_WRONG_PHASE,
                "action %r requires phase %s; the workflow is %s"
                " (replayed or out-of-order operation)" % (
                    action, required_phase, entry["phase"],
                ),
            )
        return entry, None

    # -- the single entry point ----------------------------------------

    def perform(self, workflow_id, action, revision, capability=None):
        """Run ONE fixed lifecycle action; fail closed on everything.

        Sensitive values (paths, URLs, baselines, handoff bytes) are
        resolved from the record INSIDE the action handlers — the
        caller has no way to supply one. ``capability`` is the
        Runtime-issued one-shot internal token (I3): it must be bound
        to exactly this (workflow, action, revision), unconsumed and
        unexpired. Codex never sees or supplies one: the only
        production caller is the Runtime, in-process.

        ORDER, AND WHAT EACH STEP WRITES (this is the contract; read
        it as two separate properties, because they are no longer the
        same property):

        1. ``PROBLEM_UNKNOWN_ACTION`` is refused FIRST, outside the
           lock, and consumes NOTHING. An action outside the fixed set
           cannot be the exact binding of any capability, so a token
           presented with one would be refused
           ``capability_binding_mismatch`` regardless; refusing first
           avoids spending authority on a caller-shape error.
        2. The capability is then validated and CONSUMED DURABLY,
           before the workflow store is read and before the gate runs.
           A NON-AUTHENTIC presentation — missing, malformed, unknown
           or forged, already consumed, expired, or bound to a
           different (workflow, action, revision) — is refused writing
           NOTHING, and destroys no other entry in the capability
           store.
        3. Only then are the workflow record loaded and the gate run.

        Consequently:

        * A gate refusal (policy digest drift, wrong phase, stale or
          superseded revision, ambiguity, invalid record, wrong
          control, approval refusals) writes nothing to the WORKFLOW
          RECORD, and neither does a store-unreadable refusal. That
          property is unchanged.
        * An AUTHENTIC presentation is SPENT REGARDLESS OF THE
          OUTCOME — including when the gate refuses afterwards, and
          including when the workflow store cannot be read. It is NOT
          the case that a refusal writes nothing anywhere; it is not
          the case that the capability store is untouched by a
          refusal. Re-presenting that same nonce is refused with its
          own code, durably, across Runtime restarts.

        This is deliberate. Leaving an authentic capability live
        through a gate refusal made every Runtime poll of a
        persistently refusing workflow accrue one more live entry
        against the capability store's hard bound, so one stuck
        workflow degraded the shared authority budget every other
        workflow mints from. Consumption is durable BEFORE any effect:
        a crash between consumption and effect costs one capability —
        the Runtime mints a fresh one after re-validating — never a
        replay.
        """
        if action not in BROKER_ACTIONS:
            return _refused(
                PROBLEM_UNKNOWN_ACTION,
                "unknown broker action %r; the action set is fixed"
                % (action,),
            )
        with store_module.exclusive_store_lock(self.store.directory):
            # R-01 CONSUMPTION ORDER. The capability is validated and
            # consumed FIRST, before the workflow store is even read
            # and before the gate runs. `validate_and_consume` itself
            # refuses every NON-authentic presentation — missing,
            # malformed, unknown/forged, already consumed, expired, or
            # bound to a different (workflow, action, revision) —
            # writing NOTHING and touching no other entry, so nothing
            # a caller can forge destroys authority. What this
            # ordering changes, and the ONLY thing it changes, is that
            # an AUTHENTIC, exactly-bound, unconsumed, unexpired
            # presentation is SPENT even when the gate below refuses.
            # That is the point: a gate refusal used to leave the
            # presented capability live, so a persistently refusing
            # workflow accrued one live entry per Runtime poll against
            # the store's hard bound and starved every other
            # workflow's authority. Running before `self.store.load()`
            # closes the same leak on the store-unreadable path:
            # capability authenticity does not depend on the workflow
            # store, so an unreadable store cannot leak a live
            # capability either.
            consumed, problem, detail = (
                capability_module.validate_and_consume(
                    self.store.directory, capability, workflow_id,
                    action, revision, self._clock(),
                )
            )
            if not consumed:
                return _refused(problem, detail)
            try:
                workflows = self.store.load()
            except store_module.StoreError as exc:
                return _refused(PROBLEM_STORE_UNREADABLE, str(exc))
            entry, refusal = self._gate(
                workflows, workflow_id, action, revision
            )
            if refusal is not None:
                return refusal
            # THE CONTAINMENT BOUNDARY (I5 revision 1, round-10
            # F-1 structural closure): every action handler below
            # can GROW the record (turn identities, receipts, the
            # verified result) and then save; at a hard record
            # bound the validator refuses the save and store.save
            # raises. That is a reason to stop ONE workflow
            # durably — never to kill the Runtime process (an
            # uncaught raise here took down every workflow in the
            # store). One boundary contains every present AND
            # future record-growing save beneath `perform`; a
            # derivation test proves no such save exists outside
            # it. This restores perform's stated contract:
            # refusals (and stops) never raise.
            try:
                if action == ACTION_MATERIALIZE:
                    return self._materialize(workflows, entry)
                if action == ACTION_PREPARE:
                    return self._prepare(workflows, entry)
                if action == ACTION_VALIDATE_HANDOFF:
                    return self._validate_handoff(workflows, entry)
                if action == ACTION_DISPATCH:
                    return self._dispatch(workflows, entry,
                                          follow_up=False)
                if action == ACTION_VERIFY:
                    return self._verify(workflows, entry)
                if action == ACTION_FOLLOW_UP:
                    return self._dispatch(workflows, entry,
                                          follow_up=True)
                if action == ACTION_COMPLETE:
                    return self._complete(workflows, entry)
                if action == ACTION_RECONCILE:
                    return self._reconcile(workflows, entry)
                return self._release(workflows, entry)
            except (store_module.StoreError,
                    record_module.RecordError) as exc:
                return self._contain_unsavable_record(
                    workflow_id, exc
                )

    def _contain_unsavable_record(self, workflow_id, exc):
        """A grown record the store refused: stop ONE workflow
        durably; never raise (the containment boundary's promise).

        The in-memory document is poisoned (it holds the over-bound
        record), so the durable stop starts from a FRESH load — the
        last durably saved state, which validated when it was
        written. The stop is a phase-only transition to BLOCKED: it
        grows nothing, so it cannot re-hit the very failure being
        contained. No reason receipt is written here — a receipt is
        record growth at a growth-failure boundary, and a generic
        cross-action stop-reason mechanism is the deferred I3b
        scope; the truthful code and detail travel in the outcome.
        """
        detail = str(exc)[:MAX_OUTCOME_DETAIL_CHARS]
        if HARD_BOUND_MESSAGE_MARKER in str(exc):
            problem = PROBLEM_RECORD_CAPACITY_EXHAUSTED
        else:
            problem = PROBLEM_RECORD_UNSAVABLE
        try:
            workflows = self.store.load()
            entry = workflows["workflows"].get(workflow_id)
            if entry is None:
                return _refused(PROBLEM_UNKNOWN_WORKFLOW, detail)
            if entry["phase"] not in record_module.TERMINAL_PHASES:
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )
                self.store.save(workflows)
        except (store_module.StoreError,
                record_module.RecordError) as inner:
            # Even the phase-only stop could not be persisted: a
            # store-level failure. Refuse truthfully — the record
            # is unsavable — with both causes in the detail.
            return _refused(
                PROBLEM_RECORD_UNSAVABLE,
                ("durable stop could not be persisted (%s) after"
                 " the record refused to grow (%s)"
                 % (inner, exc))[:MAX_OUTCOME_DETAIL_CHARS],
            )
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_RECORD_GROWTH_BLOCKED,
            problem=problem, detail=detail,
        )

    # -- action handlers (called with the gate already passed) ---------

    def _materialize(self, workflows, entry):
        ok, problem, detail = workspace_module.materialize(
            entry, self.transport, self.workspaces_root,
            now=self._clock(),
        )
        if not ok:
            if problem == workspace_module.PROBLEM_WORKSPACE_EXISTS:
                # Crash uncertainty is durable: the workflow is
                # marked ambiguous and BLOCKED so it can never be
                # silently retried into a directory of unknown
                # provenance.
                entry["ambiguity"] = {
                    "state": record_module.AMBIGUITY_CRASH_UNCERTAIN,
                    "detail": detail,
                }
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )
                self.store.save(workflows)
            return _refused(problem, detail)
        # ORDERING (I1 P8): trust for the workspace DI just
        # materialized is established HERE — after materialization
        # succeeded and BEFORE the workflow can advance one phase.
        # Dispatch requires VALIDATED, reachable only through
        # WORKSPACE_READY, so within the phase machine a refusal that
        # stops the transition and goes to the terminal BLOCKED phase
        # puts a Herdr start out of reach — structural, not merely
        # conventional ordering. Outside the phase machine (a direct
        # call to the spawn bridge) this ordering does not apply.
        ok, problem, detail = workspace_trust_module.establish(
            entry, self.workspaces_root, self.claude_config_path
        )
        if not ok:
            # Durable and actionable: a reason receipt naming the
            # problem code, then a terminal BLOCKED phase. Not a
            # silent retry, not a fallback to an interactive
            # prompt, not a step toward dispatch.
            entry["receipts"] = list(entry["receipts"]) + [
                workspace_trust_module.trust_block_receipt(
                    problem, now=self._clock()
                )
            ]
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
            return _refused(problem, detail)
        record_module.apply_transition(
            entry, record_module.PHASE_WORKSPACE_READY
        )
        self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _trust_still_consumable(self, entry):
        """Is the workspace trusted in the config the CHILD will read?

        The child Herdr is started through the existing spawn bridge
        and inherits this process's environment, so the configuration
        it reads is ``default_config_path()`` resolved from the LIVE
        HOME — not whatever path this Broker was told to write. When
        those differ, the establishment does not be consumed by
        a case and reporting success would be the recorded
        accepted-and-dropped class in a production path.
        """
        lease = entry.get("workspace_lease")
        if not isinstance(lease, dict) or not lease.get(
            "path_realpath"
        ):
            return False, workspace_trust_module.PROBLEM_LEASE_MISSING, (
                "no workspace lease is recorded to verify trust for"
            )
        target = lease["path_realpath"]
        consumed = workspace_trust_module.resolve_config_path(
            workspace_trust_module.default_config_path()
        )
        established = workspace_trust_module.resolve_config_path(
            self.claude_config_path
        )
        if consumed != established:
            return (
                False,
                workspace_trust_module.PROBLEM_CONFIG_NOT_CONSUMED,
                "trust was established in %s but the Herdr this"
                " dispatch would start reads %s; the establishment"
                " could not be consumed" % (established, consumed),
            )
        if not workspace_trust_module.is_trusted(consumed, target):
            return (
                False,
                workspace_trust_module.PROBLEM_TRUST_NOT_PRESENT,
                "%s no longer records trust for %s at the point of"
                " use; the Herdr would stop at the trust dialog"
                % (consumed, target),
            )
        return True, None, None

    def _prepare(self, workflows, entry):
        ok, problem, detail = workspace_module.verify_leased_workspace(
            entry, self.transport, self.workspaces_root
        )
        if not ok:
            return _refused(problem, detail)
        receipts, refused_files = prepare_module.discover_instructions(
            entry, now=self._clock()
        )
        entry["receipts"] = list(entry["receipts"]) + receipts
        record_module.apply_transition(
            entry, record_module.PHASE_PREPARED
        )
        self.store.save(workflows)
        detail = None
        if refused_files:
            detail = "; ".join(refused_files)
        return BrokerOutcome(
            True, phase=entry["phase"], detail=detail
        )

    def _validate_handoff(self, workflows, entry):
        ok, problem, detail = workspace_module.verify_leased_workspace(
            entry, self.transport, self.workspaces_root
        )
        if not ok:
            return _refused(problem, detail)
        # I4: resolve the ACTUAL bounded instruction content from the
        # just-verified leased workspace (the Broker resolves it from
        # the protected record's lease — the caller supplies
        # nothing), and cross-check it against the preparation
        # receipts: a file whose bytes drifted since preparation (or
        # vanished/became unreadable) refuses fail-closed — the turn
        # must judge exactly what preparation accounted for.
        target_context = prepare_module.instruction_context(entry)
        current = {
            item["name"]: item for item in target_context
        }
        receipt_digests = {}
        for receipt in entry["receipts"]:
            name = prepare_module.receipt_instruction_name(receipt)
            if name is None:
                continue
            receipt_digests[name] = receipt["digest"]
            item = current.get(name)
            if (
                item is None
                or item["status"] != prepare_module.INSTRUCTION_READ
                or item["digest"] != receipt["digest"]
            ):
                return _refused(
                    PROBLEM_INSTRUCTIONS_DRIFTED,
                    "instruction file %s no longer matches its"
                    " preparation receipt (changed, vanished, or"
                    " unreadable since preparation); the workflow is"
                    " not advanced" % name,
                )
        # F-3: a file that is READABLE now but had NO receipt was
        # ADDED after preparation — the turn must judge exactly what
        # preparation accounted for, so an unaccounted read is
        # refused (an attacker who can time a write into the leased
        # workspace must not choose what the turn sees).
        for item in target_context:
            if item["status"] == prepare_module.INSTRUCTION_READ and (
                item["name"] not in receipt_digests
            ):
                return _refused(
                    PROBLEM_INSTRUCTIONS_DRIFTED,
                    "instruction file %s is present now but was not"
                    " accounted for at preparation (added since); the"
                    " workflow is not advanced" % item["name"],
                )
        result = self._role_turn(
            "handoff_validation", entry, self._clock(),
            target_context=target_context,
        )
        if result.status != ROLE_TURN_COMPLETED or (
            result.outcome is None
        ):
            return _refused(
                PROBLEM_TURN_NOT_COMPLETED,
                "handoff-validation turn did not complete with an"
                " outcome (status %s, reason %s); the workflow was"
                " not advanced" % (result.status, result.reason),
            )
        if result.turn is not None:
            entry["codex_turns"] = list(entry["codex_turns"]) + [
                result.turn
            ]
        if result.outcome == OUTCOME_REQUEST_DISPATCH:
            record_module.apply_transition(
                entry, record_module.PHASE_VALIDATED
            )
        elif result.outcome == OUTCOME_NEEDS_REAUTHORIZATION:
            record_module.apply_transition(
                entry, record_module.PHASE_NEEDS_REAUTHORIZATION
            )
        else:
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=result.outcome
        )

    def _dispatch(self, workflows, entry, follow_up):
        """Dispatch the EXACT stored handoff to the target Herdr.

        Ordering, load-bearing: lease re-verification (read-only) ->
        follow-up bound check -> the durable dispatch marker (phase
        transition on first dispatch, plus the exact-count evidence
        receipt) is SAVED BEFORE the external spawn, so a crash
        between marker and spawn can never lead to a double dispatch
        — the phase gate refuses a second `dispatch` and the receipt
        count is exact. A failed spawn transitions the workflow to
        BLOCKED durably.
        """
        # The follow-up bound is a pure record read and refuses
        # BEFORE any transport verification (truly zero I/O).
        prior_dispatches = dispatch_module.dispatch_count(entry)
        if follow_up:
            follow_ups_used = prior_dispatches - 1
            if follow_ups_used >= (
                dispatch_module.MAX_FOLLOW_UP_DISPATCHES
            ):
                # R-2: the authorization-scope bound is exceeded. This
                # is NEVER a stranded dead end — the workflow
                # transitions DURABLY to NEEDS_REAUTHORIZATION
                # (visible in /status and the result path), preserving
                # evidence, lease, and the record. A human then issues
                # a new Mission Authorization to continue.
                record_module.apply_transition(
                    entry, record_module.PHASE_NEEDS_REAUTHORIZATION
                )
                self.store.save(workflows)
                return BrokerOutcome(
                    True,
                    phase=entry["phase"],
                    outcome=OUTCOME_NEEDS_REAUTHORIZATION,
                    problem=PROBLEM_FOLLOW_UP_BOUND,
                    detail="%d of %d corrective follow-up dispatches"
                    " used (exact); further correction requires a"
                    " freshly authorized revision — the workflow is"
                    " NEEDS_REAUTHORIZATION" % (
                        follow_ups_used,
                        dispatch_module.MAX_FOLLOW_UP_DISPATCHES,
                    ),
                )
        ok, problem, detail = workspace_module.verify_leased_workspace(
            entry, self.transport, self.workspaces_root
        )
        if not ok:
            return _refused(problem, detail)
        # I1 round-01 C-1 + H4: trust is re-verified AT THE POINT OF
        # USE, against the configuration the Herdr about to be
        # started will ACTUALLY read. Establishment happened phases
        # ago; between then and now a concurrent CLI writer can drop
        # DI's entry (the disclosed lost-update residual), and under
        # an injected `--config` the file DI wrote is not the file
        # the child reads at all. In either case the child would stop
        # at the trust dialog, which an unattended run is not able to
        # answer — a FAIL-OPEN in the guarantee this increment exists
        # to provide, and success reported for an effect that no
        # component consumed. Both are refused here, durably, before
        # this dispatch spawns, within the window this check covers.
        # Outside
        # this check, and disclosed: a clobber occurring between it
        # and the spawn is not covered.
        ok, problem, detail = self._trust_still_consumable(entry)
        if not ok:
            entry["receipts"] = list(entry["receipts"]) + [
                workspace_trust_module.trust_block_receipt(
                    problem, now=self._clock()
                )
            ]
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
            return _refused(problem, detail)
        # Ruling R-2: the INITIAL dispatch stamps the dispatch-time
        # protected-surface baseline receipt. The digest is computed
        # BEFORE any durable write: a REFUSED digest (over-bound,
        # unreadable, missing root) refuses the whole dispatch fail-
        # closed — stamping nothing, transitioning nothing — because
        # a dispatch without a truthful baseline would create a
        # workflow that can never verify, and an absent or fabricated
        # baseline is forbidden outright.
        surface = None
        if not follow_up:
            surface = evidence_module.protected_surface_digest(
                self.control_realpath
            )
            if surface["status"] != evidence_module.BINDING_EXACT:
                return _refused(
                    PROBLEM_SURFACE_UNAVAILABLE,
                    "the protected control-surface digest REFUSED at"
                    " dispatch time (%s: %s); dispatch fails closed"
                    " rather than stamping a fabricated or absent"
                    " baseline" % (
                        surface["status"], surface["detail"],
                    ),
                )
        # The spawn request: exactly four fields. Target/task/alias
        # are resolved from the protected record; preset is the fixed
        # DI-owned Runtime execution posture. The INITIAL dispatch is
        # the stored handoff text BYTE-EXACT (Supervisor-first); a
        # FOLLOW-UP (D6) is a corrective brief built ONLY from
        # authority fields + recorded failed-acceptance evidence,
        # carrying no technical solution.
        if follow_up:
            request = dispatch_module.build_follow_up_spawn_request(
                entry
            )
        else:
            request = dispatch_module.build_spawn_request(entry)
        if not follow_up:
            record_module.apply_transition(
                entry, record_module.PHASE_DISPATCHED
            )
        entry["receipts"] = list(entry["receipts"]) + [
            dispatch_module.dispatch_receipt(
                entry, now=self._clock(),
                sequence_number=prior_dispatches + 1,
            )
        ]
        if surface is not None:
            entry["receipts"] = list(entry["receipts"]) + [
                dispatch_module.surface_receipt(
                    entry, surface["digest"], now=self._clock()
                )
            ]
        # Durable BEFORE the external spawn.
        self.store.save(workflows)
        try:
            spawn_result = self._spawn(self.control_realpath, request)
        except Exception as exc:
            if entry["phase"] not in record_module.TERMINAL_PHASES:
                record_module.apply_transition(
                    entry, record_module.PHASE_BLOCKED
                )
                self.store.save(workflows)
            return _refused(
                PROBLEM_SPAWN_FAILED,
                "the child-spawn bridge failed (%s); the workflow is"
                " BLOCKED and was NOT re-dispatched" % (exc,),
            )
        # D1: capture the durable target-Herdr identity from the
        # spawn result at dispatch time (on the INITIAL dispatch;
        # a follow-up keeps the identity bound at first dispatch).
        if entry.get("target_engine") is None:
            entry["target_engine"] = (
                dispatch_module.target_identity_from_spawn(
                    spawn_result, entry, self._clock()
                )
            )
            self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _observation_context(self, entry):
        """The bounded, capability-free target observation the
        verification/status turn is shown (I5 D2). Read-only: calls
        the injected observer with the leased workspace realpath only,
        and projects a closed key set — never a workspace path, lease
        id, capability, or raw observation blob."""
        lease = entry["workspace_lease"]
        try:
            raw = self._observe(lease["path_realpath"])
        except Exception as exc:
            return {
                "available": False,
                "detail": "observation unavailable (%s)"
                % exc.__class__.__name__,
                "target_complete": False,
                "task_status": None,
                "completeness": None,
            }
        if not isinstance(raw, dict):
            return {
                "available": False, "detail": "no observation",
                "target_complete": False, "task_status": None,
                "completeness": None,
            }
        task = raw.get("task") if isinstance(
            raw.get("task"), dict
        ) else {}
        status = task.get("status")
        completeness = raw.get("completeness")
        # R-6 condition 3 applied to THIS existing gate: "the target
        # has stopped" is decided by the SOURCE-SCOPED support
        # primitive over the registered verification consumed-source
        # set, NEVER by global completeness — a production
        # observation is globally PARTIAL whenever agents are listed
        # unprobed, and a global gate here would stall every
        # dispatched workflow forever. A demoting diagnostic in a
        # CONSUMED source (task/reviews/artifacts/observation) still
        # fails closed to a WAIT exactly as before.
        supported, _blocking = evidence_module.observation_supports(
            raw, evidence_module.VERIFICATION_CONSUMED_SOURCES
        )
        target_complete = (
            supported and status in _TARGET_TERMINAL_STATUSES
        )
        return {
            "available": True,
            "detail": None,
            "task_status": status if isinstance(status, str) else None,
            "target_complete": target_complete,
            "completeness": completeness,
        }

    def _readiness_gate(self, workflows, entry):
        """The I3 bootstrap-readiness gate for a DISPATCHED workflow.

        Returns a BrokerOutcome when the workflow STOPPED durably, and
        None when it did not — including every case where readiness is
        already evidenced, which is the case an engineering mission is
        in for all but the first minutes of its life.

        Writes at most one receipt per STATE CHANGE, following
        `_note_observation`: a target polled repeatedly while its roles
        come up does not churn the store, and a restart re-reads the
        same durable states rather than replaying them, because the
        receipt is written only when the newly derived state differs
        from the last one already on the record.
        """
        previous = readiness_module.last_recorded_state(entry)
        state, detail, _pairs, _probed, stop = readiness_module.evaluate(
            entry,
            lambda: self._readiness_probe(
                entry["workspace_lease"]["path_realpath"]
            ),
            self._clock(),
        )
        if state != previous:
            entry["receipts"] = list(entry["receipts"]) + [
                readiness_module.readiness_receipt(
                    state, detail, now=self._clock()
                )
            ]
            self.store.save(workflows)
        if not stop:
            return None
        problem = readiness_module.problem_for(state)
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            self.store.save(workflows)
        return _refused(problem, detail)

    def _note_observation(self, entry, observation):
        """Record the last DISTINCT target observation (I6 carried
        item). Mutates ``entry["last_observation"]`` and returns True
        ONLY when the observed (task_status, completeness) pair CHANGES
        — so a target polled repeatedly never churns the store, while a
        target that becomes unobservable records the (None, None) pair
        once, making /status able to surface it. Never authority: a
        bounded projection of the read-only observation."""
        status = (
            observation["task_status"]
            if isinstance(observation["task_status"], str) else None
        )
        completeness = (
            observation["completeness"]
            if isinstance(observation["completeness"], str) else None
        )
        prior = entry["last_observation"]
        if prior is not None and (
            prior["task_status"] == status
            and prior["completeness"] == completeness
        ):
            return False
        entry["last_observation"] = {
            "task_status": status,
            "completeness": completeness,
            "observed_at": self._clock(),
        }
        return True

    def _collect_evidence(self, entry):
        """One I1 evidence collection through the SAME injected
        seams the Broker holds — the caller supplies nothing."""
        return evidence_module.collect_verification_evidence(
            entry, self.transport, self._observe,
            self.control_realpath, self._clock(),
        )

    def _verification_block(self, workflows, entry, problem, detail):
        """A DURABLE verification stop (D-A5): with the target
        stopped there is nothing left to wait for, so a failed
        evidence shape or a failed structural gate transitions the
        workflow to BLOCKED — with the reason recorded TRUTHFULLY as
        a fixed-marker E-5 receipt so /status can surface it (ruling
        R-4: no BLOCKED path strands a consumed approval silently).
        Never an indefinite re-poll."""
        import secrets
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "vblock-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": entry["handoff"]["digest_sha256"],
            "bounded_summary": (
                "%s: %s — %s" % (
                    VERIFICATION_BLOCK_MARKER, problem, detail,
                )
            )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }]
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_VERIFICATION_BLOCKED,
            problem=problem, detail=detail,
        )

    def _verify(self, workflows, entry):
        """DISPATCHED: observe the target read-only; when it has
        STOPPED (decided source-scoped per ruling R-6, never on
        global completeness), collect the I1 evidence projection.
        A projection that is incomplete or schema-invalid refuses
        BEFORE ANY MODEL CALL and stops durably (D-A5). A complete
        projection runs the fresh verification turn WITH the
        rendered evidence; a `verified_result` outcome from that
        turn is NECESSARY, NEVER SUFFICIENT — the D-A4 conjunctive
        gates are applied independently against a FRESH collection
        before anything is recorded, and one failing conjunct stops
        the workflow durably with its own problem code. Herd
        lifecycle COMPLETE alone can never produce VERIFIED: it is
        one conjunct of eight. While the target is still running the
        workflow stays DISPATCHED — writing the store ONLY when the
        observed pair changed (I6), never every poll."""
        ok, problem, detail = workspace_module.verify_leased_workspace(
            entry, self.transport, self.workspaces_root
        )
        if not ok:
            return self._verification_block(
                workflows, entry, problem, detail
            )
        observation = self._observation_context(entry)
        observation_changed = self._note_observation(entry, observation)
        if not observation["target_complete"]:
            # I3: THIS is where a bootstrap failure and a legitimately
            # long mission were previously indistinguishable — both
            # arrived here and waited. The readiness gate separates
            # them, and separates them in exactly one direction: it
            # can stop a workflow that, within the bootstrap window,
            # has not yet been ready. Outside
            # that case it returns from durable state, and within that
            # path there is no probe, no clock read and no bound once
            # readiness has been evidenced once. An engineering
            # mission does not acquire a deadline here.
            stopped = self._readiness_gate(workflows, entry)
            if stopped is not None:
                return stopped
            # Legitimate wait: no transition. A store write happens
            # ONLY when the observed pair changed (so an indefinitely
            # unobservable target is recorded once, then quiet).
            if observation_changed:
                self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome="target_running",
                detail="target task status: %s"
                % observation["task_status"],
            )
        # Target stopped: collect the evidence projection. Broken
        # evidence refuses BEFORE any model call — no Codex turn is
        # spent on it — and stops durably (nothing left to wait for).
        projection = self._collect_evidence(entry)
        # Policy drift is already one of the separate verification
        # conjuncts.  It is checked here, after the fresh target
        # observation is captured but BEFORE a verification role turn,
        # so the common Broker gate must not shadow this durable stop.
        # The pre-I9 ordering returned broker_policy_digest_drift before
        # capability consumption on every poll: the Runtime discarded
        # that outcome, last_observation stayed stale, and the existing
        # verification-block receipt was unreachable.
        for precheck in (
                _gate_evidence_complete,
                _gate_evidence_valid,
                _gate_control_policy):
            gate_problem, gate_detail = precheck(entry, projection)
            if gate_problem is not None:
                return self._verification_block(
                    workflows, entry, gate_problem, gate_detail
                )
        result = self._role_turn(
            "verification", entry, self._clock(),
            observation=observation, evidence=projection,
        )
        if result.status != ROLE_TURN_COMPLETED or (
            result.outcome is None
        ):
            return self._verification_block(
                workflows, entry, PROBLEM_TURN_NOT_COMPLETED,
                "verification turn did not complete with an outcome"
                " (status %s, reason %s)"
                % (result.status, result.reason),
            )
        if result.turn is not None:
            entry["codex_turns"] = list(entry["codex_turns"]) + [
                result.turn
            ]
        outcome = result.outcome
        detail_text = _bounded_detail(result)
        if outcome == OUTCOME_VERIFIED_RESULT:
            # NECESSARY, NEVER SUFFICIENT: apply the D-A4 gates
            # independently against a FRESH collection (fresh disk
            # read through the same seams) BEFORE recording
            # anything. One failing conjunct stops durably with its
            # own code; the turn's verified_result cannot override a
            # single gate.
            fresh = self._collect_evidence(entry)
            for _gate_name, check in _VERIFICATION_GATES:
                gate_problem, gate_detail = check(entry, fresh)
                if gate_problem is not None:
                    return self._verification_block(
                        workflows, entry, gate_problem, gate_detail
                    )
            summary = detail_text or "verified"
            entry["verified_result"] = {
                "summary": summary,
                "digest": text_digest(summary),
                "recorded_at": self._clock(),
            }
            record_module.apply_transition(
                entry, record_module.PHASE_VERIFIED
            )
            self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=outcome
            )
        if outcome == OUTCOME_REQUEST_FOLLOW_UP:
            # Record the bounded failed-acceptance evidence a
            # subsequent ACTION_FOLLOW_UP builds the corrective brief
            # from; stay DISPATCHED.
            entry["receipts"] = list(entry["receipts"]) + [{
                "kind": "evidence",
                "turn_id": (
                    result.turn["turn_id"] if result.turn else "verify"
                ),
                "recorded_at": self._clock(),
                "digest": entry["handoff"]["digest_sha256"],
                "bounded_summary": (
                    dispatch_module.CORRECTION_RECEIPT_MARKER
                    + ": " + (detail_text or "correction requested")
                )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
            }]
            self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=outcome
            )
        if outcome == OUTCOME_NEEDS_REAUTHORIZATION:
            record_module.apply_transition(
                entry, record_module.PHASE_NEEDS_REAUTHORIZATION
            )
            self.store.save(workflows)
            return BrokerOutcome(
                True, phase=entry["phase"], outcome=outcome
            )
        # blocked (the only remaining verification outcome).
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=outcome
        )

    def _observe_raw(self, repo_path):
        """One guarded canonical observation; None on any failure."""
        try:
            raw = self._observe(repo_path)
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _spawn_records_raw(self):
        """One guarded control-repository spawn-record projection."""
        try:
            raw = self._spawn_records(self.control_realpath)
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _reconcile_block(self, workflows, entry, problem, detail):
        """A DURABLE recovery stop (D-B3 / ruling R-3): the binding
        could not be PROVEN, so the workflow stops with the reason
        recorded as a fixed-marker E-5 receipt for /status (D-B4) —
        scoped to this action only. Prefer BLOCKED over a probable
        guess; a human resolves it."""
        import secrets
        entry["receipts"] = list(entry["receipts"]) + [{
            "kind": record_module.RECEIPT_KIND_EVIDENCE,
            "turn_id": "rblock-" + secrets.token_hex(8),
            "recorded_at": self._clock(),
            "digest": entry["handoff"]["digest_sha256"],
            "bounded_summary": (
                "%s: %s — %s" % (
                    RECOVERY_BLOCK_MARKER, problem, detail,
                )
            )[:record_module.MAX_BOUNDED_SUMMARY_CHARS],
        }]
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"],
            outcome=OUTCOME_RECOVERY_BLOCKED,
            problem=problem, detail=detail,
        )

    def _reconcile(self, workflows, entry):
        """DISPATCHED + unresolved identity: bind EXACTLY ONE
        provable existing child by writing the EXISTING
        target_engine field, or stop durably. Evidence-only — this
        handler performs no spawn, no dispatch, no replay, and
        accepts no command; its only write is the binding itself or
        the durable block.

        The binding proof (D-B3): the control repository's own
        persisted spawn record (its already-realpath `repo` equal to
        the leased workspace realpath EXACTLY), the leased workspace's
        own canonical observation reporting the SAME task id, clean
        bounded projections on both sides, and EXACTLY ONE candidate.
        The deterministic alias is never consulted: herd's child
        records carry none.
        """
        ok, problem, detail = workspace_module.verify_leased_workspace(
            entry, self.transport, self.workspaces_root
        )
        if not ok:
            return _refused(problem, detail)
        engine = entry.get("target_engine")
        if engine is not None and isinstance(
            engine.get("task_id"), str
        ) and engine["task_id"] and engine["task_id"] != (
            dispatch_module.UNRESOLVED_TASK_ID
        ):
            return _refused(
                PROBLEM_RECONCILE_ALREADY_BOUND,
                "the target-engine identity is already durably bound"
                " (task %s); reconcile binds exactly once"
                % engine["task_id"],
            )
        lease_real = os.path.realpath(
            entry["workspace_lease"]["path_realpath"]
        )
        # Control-side evidence: ALL persisted spawn records, including
        # valid parent_task_id=None / dependency=False outer spawns.
        control_records = self._spawn_records_raw()
        if control_records is None:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn-record projection is"
                " unavailable; a partial view is never a binding"
                " proof",
            )
        if control_records.get("truncated") is True:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_TRUNCATED,
                "the spawn-record listing is TRUNCATED; a listing"
                " that may omit a candidate is never a binding proof",
            )
        if control_records.get("state") not in ("available", "empty"):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn records are not cleanly"
                " readable (state %r)"
                % (control_records.get("state"),),
            )
        listed = (
            control_records.get("listed")
            if isinstance(control_records.get("listed"), list) else None
        )
        count = control_records.get("count")
        if (
            listed is None
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or count != len(listed)
        ):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side spawn-record projection has a"
                " malformed count/listing shape; it is never a"
                " binding proof",
            )
        # Lease-side observation: the workspace's OWN task identity.
        lease_raw = self._observe_raw(lease_real)
        supported, blocking = evidence_module.observation_supports(
            lease_raw, evidence_module.RECONCILE_CONSUMED_SOURCES
        )
        if not supported:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the leased workspace's observation is degraded in a"
                " consumed source (%s); a partial view is never a"
                " binding proof" % ", ".join(sorted({
                    str(d.get("source")) for d in blocking
                })),
            )
        lease_task = (
            lease_raw.get("task")
            if isinstance(lease_raw.get("task"), dict) else {}
        )
        observed_id = lease_task.get("id")
        if lease_task.get("state") != "available" or not isinstance(
            observed_id, str
        ) or not observed_id:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the leased workspace reports no observable task"
                " identity (task state %r); an unobservable identity"
                " is never a binding proof"
                % (lease_task.get("state"),),
            )
        # I4 item 2. BOTH sides are realpath'd here.
        #
        # The audit found the writer already resolves: within
        # `control_plane.spawn_child` the target is rebound to
        # `Path(target_repo).expanduser().resolve()` BEFORE the record
        # is built, so within that writer the `str(target)` fallback
        # is resolved too, and `spawn` has a single return carrying
        # `repo`. So a raw comparison was in fact sound for records
        # that writer produced; records from another writer are
        # outside what was checked.
        #
        # It is not left raw, for two reasons. First, within this
        # module its soundness rested on a property of a DIFFERENT
        # module that no assertion here covered — an undocumented
        # coincidence rather than a guarantee. Second, the failure
        # DIRECTION is bad in a
        # way that "fail-closed" hides: a missed match yields
        # PROBLEM_RECONCILE_NO_MATCH and a durable block, converting a
        # RECOVERABLE dispatch into a permanent stranding, which is
        # the dead-end class this task exists to close. Safe and
        # correct are not the same thing here.
        #
        # Within this comparison, resolving both sides only ADDS a
        # match, and only between two spellings of the SAME FILE, so a
        # workflow is not bound to a different workspace. Outside
        # that: a record naming a path that no longer exists resolves
        # lexically, which is the behaviour the lease side already
        # had.
        matching = [
            candidate for candidate in listed
            if isinstance(candidate, dict)
            and isinstance(candidate.get("repo"), str)
            and os.path.realpath(candidate["repo"]) == lease_real
        ]
        if not matching:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_NO_MATCH,
                "no recorded child names this workflow's leased"
                " workspace (realpath comparison on both sides over"
                " %d recorded child(ren)); nothing provable to bind"
                % len(listed),
            )
        recorded_ids = [
            candidate.get("task_id") for candidate in matching
        ]
        if any(
            not isinstance(recorded_id, str) or not recorded_id
            or recorded_id != observed_id
            for recorded_id in recorded_ids
        ):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_CONFLICT,
                "a workspace-matching child record disagrees with"
                " the workspace's own observed task identity"
                " (recorded %r vs observed %r); a conflicting"
                " identity is never bound"
                % (sorted(set(map(repr, recorded_ids))), observed_id),
            )
        if len(matching) > 1:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_MULTIPLE,
                "%d recorded children name this leased workspace;"
                " EXACTLY ONE provable candidate is required, so an"
                " ambiguous set is never bound" % len(matching),
            )
        entry["target_engine"] = {
            # The deterministic derivation — a LABEL, not evidence
            # (the binding proof above never consulted any alias).
            "alias": dispatch_module.ALIAS_PREFIX + entry[
                "workflow_id"
            ],
            "task_id": observed_id,
            # Identity display (bounded schema field): the canonical
            # target, exactly as the dispatch-time capture stores on
            # its own fallback path. The binding PROOF was the exact
            # lease-realpath equality computed above.
            "repo": entry["target"]["canonical_url"],
            "dispatched_at": self._clock(),
        }
        self.store.save(workflows)
        return BrokerOutcome(
            True, phase=entry["phase"], outcome=OUTCOME_RECONCILED,
            detail="bound the single provable child: target task %s"
            % observed_id,
        )

    def _complete(self, workflows, entry):
        """VERIFIED -> COMPLETED. Mechanical: the verified result was
        recorded at VERIFIED; completion marks the workflow done and
        the result ready for the adapter to deliver."""
        if entry.get("verified_result") is None:
            return _refused(
                PROBLEM_NO_VERIFIED_RESULT,
                "a VERIFIED workflow has no recorded verified result;"
                " refusing to complete",
            )
        record_module.apply_transition(
            entry, record_module.PHASE_COMPLETED
        )
        self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

    def _retire_process_scopes(self, entry, report):
        """Reclaim this workflow's scope records (R-54 AR-4).

        Separated from `_release` so the destructive step has its own
        named function and its own row in the cleanup report, rather
        than being an unnamed tail of a long method.
        """
        from target_runtime import process_ownership as _own
        control = (
            entry.get("control_identity") or {}
        ).get("repository_realpath")
        if not isinstance(control, str) or not control:
            report.record(
                "process_scopes", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                detail="the record names no control repository, so no"
                       " scope can be PROVEN to belong to this"
                       " workflow; nothing is removed",
            )
            return
        retired, refused = _own.retire_workflow_scopes(
            control, entry["workflow_id"]
        )
        for directory, reason in refused:
            report.record(
                "process_scopes", directory,
                ownership_module.UNPROVABLE, detail=reason,
            )
        if retired:
            report.record(
                "process_scopes", entry["workflow_id"],
                ownership_module.OWNED, ok=True,
                detail="retired %d process-scope record(s)"
                       % len(retired),
            )

    def _release(self, workflows, entry):
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            return _refused(
                PROBLEM_WRONG_PHASE,
                "release requires a terminal phase; the workflow is"
                " %s" % entry["phase"],
            )
        report = ownership_module.CleanupReport()
        # I5-1. Trust is revoked BEFORE the directory is removed, and
        # the order is load-bearing in one direction only: while the
        # directory still exists the ownership check is at its
        # strictest. `revoke` itself does NOT require the directory,
        # so a crash between these two steps leaves a second release
        # able to finish the job rather than stranding the entry —
        # which is the live condition this increment was pointed at.
        recorded = ownership_module.recorded_lease_realpath(entry)
        if recorded is None:
            report.record(
                "trust", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                detail="no lease realpath is recorded, so no trust"
                       " key can be PROVEN to belong to this"
                       " workflow; nothing is removed",
            )
        else:
            key = workspace_trust_module.trust_key(recorded)
            verdict = ownership_module.owns_trust_entry(
                entry, key, self.workspaces_root
            )
            if verdict != ownership_module.OWNED:
                report.record("trust", key, verdict)
            else:
                ok, problem, detail = workspace_trust_module.revoke(
                    entry, self.workspaces_root,
                    self.claude_config_path,
                )
                report.record(
                    "trust", key, ownership_module.OWNED,
                    ok=ok, detail=problem,
                )
                if not ok:
                    entry["receipts"] = list(entry["receipts"]) + [
                        workspace_trust_module.revoke_block_receipt(
                            problem, now=self._clock()
                        )
                    ]
        # R-31 W-3: THE DESTRUCTIVE STEP COMES LAST.
        #
        # Sessions are closed BEFORE the managed directory is deleted.
        # The previous order deleted the directory first, so even
        # fully wired it would have killed agents only after their
        # workspace was already gone — the third instance in this
        # increment of an irreversible step running before the step
        # that makes it safe (the first was `Popen` before the record
        # that attributes it; the second was freeze state restored
        # after the fact). `tests/test_ownership.py`'s
        # `DestructiveOrderingClosureTests` is the structural closure
        # for the class rather than a third reorder.
        #
        # Idempotent on re-entry: the session close proves ownership
        # from durable records and finds no live workspace the second
        # time, which is a REFUSAL rather than a second close; and
        # `workspace_module.release` refuses a lease already released.
        # So closed-but-not-deleted is a re-enterable state, and the
        # receipt says which half completed.
        # R-37 AB-1: PRESERVE THE TARGET EVIDENCE FIRST.
        #
        # Before the sessions are closed and before the directory is
        # deleted, because both destroy what it reads. Reclaiming live
        # resources and preserving forensics are different
        # obligations, and cleanup was satisfying the first while
        # silently destroying the second — the run finished and the
        # proof that the chain had worked went with the workspace.
        #
        # It copies bytes and records a digest of each FULL file, so a
        # preserved artifact is bound to what was actually there;
        # reconstructing or summarising would be worse than losing it,
        # because a reader could not tell.
        from target_runtime import evidence_preservation as preserve_module
        # AC-2: the workspace identity comes from the SAME unique
        # binding the close is about to act on, derived ONCE here and
        # handed to both. # Two independent derivations of one identity is how a preserved
        # record could name one workspace while the close named another,
        # with no check between them.
        proof = self._domain_b_proof(entry)
        proof_workspace_id = (
            proof.workspace_id if proof is not None else None
        )
        # THE OWNERSHIP VERDICT IS DERIVED ONCE, HERE, AND USED TWICE.
        #
        # It moved ahead of preservation deliberately. Preservation
        # READS the managed directory and copies its bytes into this
        # workflow's archive, so running it against a directory this
        # workflow has not been PROVEN to own would archive somebody
        # else's evidence under this workflow's id — and, because a
        # missing required artifact HALTS, an unowned or already-gone
        # directory would also mask the refusal that should have been
        # reported (a path mismatch, a lease already released). Only
        # an OWNED directory is read, and only an OWNED directory is
        # the one the destructive steps below will act on, which is
        # the case preservation exists to precede.
        workspace_verdict = ownership_module.owns_workspace(
            entry, recorded if recorded is not None else "",
            self.workspaces_root,
        )
        directory_present = (
            recorded is not None and os.path.isdir(recorded)
        )
        if recorded is not None and (
            workspace_verdict == ownership_module.OWNED
        ) and not directory_present:
            # ALREADY GONE. Recorded rather than skipped silently: a
            # release re-entered after the directory was removed has,
            # within this branch, no evidence left to preserve.
            # Halting here would report degradation in place of the
            # clean refusal `workspace_module.release` already gives
            # for a lease released once.
            report.record(
                "target_evidence", entry["workflow_id"],
                ownership_module.UNPROVABLE,
                ok=True,
                detail="the managed directory is already absent, so"
                       " there is no target evidence to preserve and"
                       " nothing downstream can destroy",
            )
        elif recorded is not None and (
            workspace_verdict == ownership_module.OWNED
        ):
            # AF-3: PRODUCTION NAMES THE REQUIRED ARTIFACTS.
            #
            # The parameter existed and production supplied an empty
            # set, so within production the required-artifact half of
            # the completeness policy could fire only in a test. The
            # constant is passed HERE, at the one production seam, and
            # within this signature `preserve` has no default for it,
            # so a later caller re-opens the hole only by editing the
            # seam.
            ok, problem, detail, summary = preserve_module.preserve(
                entry, recorded, self.store.directory, self._clock(),
                workspace_id=proof_workspace_id,
                required_names=preserve_module.REQUIRED_ARTIFACTS,
            )
            report.record(
                "target_evidence", entry["workflow_id"],
                ownership_module.OWNED if ok
                else ownership_module.UNPROVABLE,
                ok=ok,
                # BOTH halves. `problem or detail` dropped the
                # detail whenever a problem code existed, which within
                # this path is every failure — so the row named the
                # policy and left the missing artifact unnamed.
                detail=("%s: %s" % (problem, detail)) if problem
                else detail,
            )
            if ok:
                entry["receipts"] = list(entry["receipts"]) + [
                    _preserve_receipt(summary, now=self._clock())
                ]
            else:
                # AC-1 / AC-3: THE CHAIN HALTS HERE.
                #
                # Preservation is a PROVEN PRECONDITION of the two
                # destructive steps that follow, not merely the step
                # before them. The previous form recorded the failure
                # and then proceeded — so a preservation failure could
                # destroy the only source of the evidence it had just
                # failed to preserve.
                #
                # Halting retains the sessions AND the directory, and
                # because the lease stays unreleased the workflow
                # remains a cleanup candidate and the next pass
                # retries from re-derived evidence. Retryable-degraded
                # is not completed.
                report.record(
                    "workspace", recorded, ownership_module.UNPROVABLE,
                    detail="the chain HALTED before the session close:"
                           " the target evidence was not preserved, so"
                           " nothing downstream may destroy it",
                )
                self.store.save(workflows)
                return BrokerOutcome(
                    True, phase=entry["phase"],
                    outcome=OUTCOME_RELEASED_DEGRADED,
                    problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                    # The report summary is COUNTS, deliberately
                    # bounded. Counts alone, within this receipt,
                    # leave an operator unable to act: "1 unprovable"
                    # does not say which required artifact is missing,
                    # and that name is the actionable content of the
                    # halt. Appended here, bounded, rather than
                    # widening the receipt line.
                    detail="%s; preservation halted: %s" % (
                        report.summary(),
                        (detail or problem or "no reason recorded")
                        [:400],
                    ),
                )
        sessions = self._release_workspace_sessions(
            entry, report, snapshot=proof
        )
        if workspace_verdict == ownership_module.UNPROVABLE:
            # "We cannot tell" — the record carries no lease to check
            # against. # Within this branch the directory is left as it is, and the
            # release reports itself degraded rather than reporting a
            # removal it did not perform.
            report.record(
                "workspace", "<unrecorded>", ownership_module.UNPROVABLE,
                detail="no lease realpath is recorded",
            )
        else:
            # NOT_OWNED stays a REFUSAL, and deliberately so: a record
            # naming a path this workflow does not own is a corrupt or
            # hostile record, not a partial cleanup, and the existing
            # release hardening already refuses it with its own problem
            # code. Downgrading that to a degraded success would let a
            # caller that checks `ok` read a refusal as a completion —
            # which an existing guarantee test caught when an earlier
            # draft of this method did exactly that. So the call is
            # made in both cases and `workspace_module.release` decides;
            # # this layer adds a pre-check for the UNPROVABLE case it
            # could not express before, and its scope: additive only.
            if sessions != SESSIONS_RECLAIMED:
                # R-36 AA-1: THE DELETE IS CONDITIONAL, not merely
                # subsequent. Ordering two steps does not sequence
                # them if the second runs unconditionally — and this
                # one deletes the directory, so an unproven close
                # followed by a delete turns a transient unreadable
                # projection into permanent abandonment of a LIVE
                # workspace whose sessions are still running.
                #
                # Retaining also preserves CANDIDACY: the lease stays
                # unreleased, so `terminal_cleanup_candidates` returns
                # this workflow again and the next pass re-derives the
                # evidence. # Idempotency comes from proving current state at retry
                # time, rather than from a flag an attempt wrote.
                report.record(
                    "workspace", recorded,
                    ownership_module.UNPROVABLE,
                    detail="the workspace directory is RETAINED"
                           " because the session close was not"
                           " proven; this workflow remains a cleanup"
                           " candidate",
                )
            else:
                ok, problem, detail = workspace_module.release(
                    entry, self.workspaces_root, now=self._clock()
                )
                report.record(
                    "workspace", recorded, workspace_verdict,
                    ok=ok, detail=problem,
                )
                if not ok:
                    return _refused(problem, detail)
                # R-54 AR-4: THE PROCESS-SCOPE RECORDS ARE RECLAIMED
                # HERE, and here is the only place they can be.
                #
                # AL-4..AL-7 decided this lifecycle and, within
                # production, no code executed it, so an assignment
                # was written before every spawn and left forever. The bound is THIS
                # WORKFLOW'S OWN terminal cleanup — not an age, not a
                # size, not a sweep — and the selection is by
                # ASSIGNMENT CREDENTIAL, so a directory whose name
                # merely parses is not reclaimed.
                #
                # ORDERING: it runs AFTER the release proved out,
                # because until then the workflow may still be
                # running and its records are what a recovery would
                # need. Within it a scope holding a corroborated live
                # group is refused and reported, so a premature
                # retire leaves the record rather than the process.
                self._retire_process_scopes(entry, report)
        # R-30 V-2: THE UNSCOPED GLOBAL SWEEP THAT WAS HERE IS
        # REMOVED.
        #
        # It called `recover_orphans` with NO BASE, so it read the
        # GLOBAL record space and would have reaped whatever it found
        # — including another workflow's helper groups — and then
        # reported them as this workflow's. That is a cross-workflow
        # reap and a false attribution at once, which is worse than
        # the unwired state it was meant to fix: # production does not REGISTER through the owned path, so
        # reading that space was unfounded in the first place.
        #
        # The rule it violated, now explicit: # NO PRODUCTION REAPING WITHOUT PRODUCTION REGISTRATION, and a
        # recovery must be scoped to the OWNING workflow rather than to
        # a shared root.
        # Terminal cleanup of what a workflow actually leaves behind
        # is a Domain B operation — the workspace and its sessions —
        # and belongs to `target_runtime.workspace_ownership`, not to
        # a sweep of local helper processes.
        entry["receipts"] = list(entry["receipts"]) + [
            _cleanup_receipt(report, now=self._clock())
        ]
        self.store.save(workflows)
        if report.degraded:
            return BrokerOutcome(
                True, phase=entry["phase"],
                outcome=OUTCOME_RELEASED_DEGRADED,
                problem=ownership_module.PROBLEM_CLEANUP_DEGRADED,
                detail=report.summary(),
            )
        return BrokerOutcome(True, phase=entry["phase"])


def _cleanup_receipt(report, now, turn_id_factory=None):
    """The durable, bounded receipt for one release's cleanup (I5).

    It carries `CleanupReport.summary()`, whose degraded state is
    DERIVED from the unprovable and failed lists rather than passed in,
    so within this receipt a complete-cleanup claim over a non-empty
    remainder has no representation.
    """
    import hashlib
    import secrets
    make_turn_id = turn_id_factory or (
        lambda: "cleanup-" + secrets.token_hex(8)
    )
    summary = report.summary()
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "bounded_summary": "%s: %s" % (CLEANUP_RECEIPT_MARKER, summary),
    }


#: Close verdicts that mean THE SESSIONS ARE RECLAIMED — the only
#: states in which the managed directory may be deleted (R-36 AA-1).
#: Everything else, degraded included, retains the directory.
SESSIONS_RECLAIMED = "sessions_reclaimed"
SESSIONS_RETAINED = "sessions_retained"


def _domain_b_proof(broker, entry):
    """The ONE ownership proof for this release (R-40 AD-1).

    Returns a `ProofSnapshot` on an OWNED verdict and None otherwise.
    The previous helper bound the verdict to `_verdict` and returned
    the id regardless, so preservation received an identity derived
    from a proof that had FAILED — and the close then read live state
    a SECOND time and took its own. Both defects are unwritable now:
    there is no id to obtain without a snapshot, and a snapshot exists
    only for OWNED.

    Read-only within this call: it proves and takes no action.
    """
    from target_runtime import workspace_ownership as ws_module
    if broker._live_workspaces is None:
        return None
    try:
        live = broker._live_workspaces()
        children = broker._spawn_records_raw()
    except Exception:                                     # noqa: BLE001
        return None
    verdict, snapshot, _problem, _detail = ws_module.prove_ownership(
        entry, (children or {}).get("listed"), live,
        broker.workspaces_root,
    )
    return snapshot if verdict == ws_module.OWNED else None


def _domain_b_release(broker, entry, report, snapshot=None):
    """Terminal DOMAIN B cleanup: the workspace and its sessions.

    R-40 AD-3: it CONSUMES the one snapshot the release already
    proved, and re-reads live state only to REVALIDATE it immediately
    before the close (AD-4). It derives no identity of its own, which is what keeps the archive
    and the close from naming different workspaces.
    """
    from target_runtime import workspace_ownership as ws_module
    if broker._live_workspaces is None and broker._workspace_close is None:
        # DOMAIN B IS NOT CONFIGURED FOR THIS BROKER AT ALL.
        #
        # A CONFIGURATION fact, not a degraded reading, and the
        # distinction is the point: a Broker given neither a
        # projection nor a close capability is not the actor for the
        # workspace dimension, so the directory release proceeds as it
        # did before Domain B existed. A Broker that IS configured and
        # is then unable to READ the evidence is the dangerous case,
        # and it retains.
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.NOT_OWNED,
        )
        return SESSIONS_RECLAIMED
    if broker._workspace_close is None:
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.UNPROVABLE,
            ok=False,
            detail="no workspace-close capability is wired, so the"
                   " sessions cannot be reclaimed",
        )
        return SESSIONS_RETAINED
    if snapshot is None:
        # # No proof: within this branch no workspace may be closed. Two shapes are still
        # RECLAIMED because they are positive evidence that no session
        # remains — and both are established from the proof's own
        # refusal rather than guessed at.
        return _domain_b_nothing_to_close(broker, entry, report)
    try:
        live_now = broker._live_workspaces()
    except Exception as exc:                              # noqa: BLE001
        report.record(
            "workspace_session", snapshot.workspace_id,
            ownership_module.UNPROVABLE,
            detail="live state unreadable at close time (%s)"
                   % exc.__class__.__name__,
        )
        return SESSIONS_RETAINED
    try:
        children_now = broker._spawn_records_raw()
    except Exception:                                     # noqa: BLE001
        children_now = None
    closed, workspace_id, problem, detail = (
        ws_module.close_proven_workspace(
            snapshot, live_now, broker._workspace_close,
            child_records=(children_now or {}).get("listed"),
            entry=entry, workspaces_root=broker.workspaces_root,
        )
    )
    if closed:
        report.record("workspace_session", workspace_id,
                      ownership_module.OWNED)
        return SESSIONS_RECLAIMED
    report.record(
        "workspace_session", workspace_id or entry["workflow_id"],
        ownership_module.UNPROVABLE,
        detail="%s: %s" % (problem, detail),
    )
    return SESSIONS_RETAINED


def _domain_b_nothing_to_close(broker, entry, report):
    """The two POSITIVE-evidence cases in which no session remains.

    Reached only when the proof produced no snapshot. Everything else
    RETAINS, because a transient unreadable projection must not become
    permanent abandonment of a live workspace.
    """
    from target_runtime import workspace_ownership as ws_module
    try:
        live = broker._live_workspaces()
        children = broker._spawn_records_raw()
    except Exception as exc:                              # noqa: BLE001
        report.record(
            "workspace_session", entry["workflow_id"],
            ownership_module.UNPROVABLE,
            detail="workspace evidence unreadable (%s)"
                   % exc.__class__.__name__,
        )
        return SESSIONS_RETAINED
    _verdict, _snapshot, problem, detail = ws_module.prove_ownership(
        entry, (children or {}).get("listed"), live,
        broker.workspaces_root,
    )
    report.record(
        "workspace_session", entry["workflow_id"],
        ownership_module.UNPROVABLE,
        detail="%s: %s" % (problem, detail),
    )
    if problem == ws_module.PROBLEM_WORKSPACE_NOT_FOUND:
        return SESSIONS_RECLAIMED
    if (
        problem == ws_module.PROBLEM_NO_CHILD_RECORD
        and ownership_module.recorded_task_id(entry) is None
    ):
        return SESSIONS_RECLAIMED
    return SESSIONS_RETAINED


TargetBroker._release_workspace_sessions = _domain_b_release
TargetBroker._domain_b_proof = _domain_b_proof


def _preserve_receipt(summary, now, turn_id_factory=None):
    """The durable receipt for one evidence preservation (AB-1/AB-3).

    Carries the projection's own summary, including its TRUNCATION
    disclosure when the listing was capped, so a reader of the record
    can tell a complete archive from a partial one without opening the
    projection.
    """
    import hashlib
    import secrets
    make_turn_id = turn_id_factory or (
        lambda: "preserve-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "bounded_summary": summary[:400],
    }
