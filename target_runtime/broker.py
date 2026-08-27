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
from target_runtime import workspace as workspace_module

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
                 spawn_fn=None, clock=None, observer_fn=None):
        import time
        self.store = store_module.WorkflowStore(store_directory)
        self.control_realpath = control_repository_realpath
        self.transport = transport
        self.workspaces_root = workspaces_root
        # The handoff-validation Codex turn (I2 role_turn), injected
        # so hermetic tests never spawn a process; production wires
        # codex_gateway.role_turn.run_role_turn.
        self._role_turn = role_turn_fn
        # The child-spawn bridge (I5), injected the same way;
        # production wires dispatch.production_spawn (the EXISTING
        # herdr orchestrator bridge — no parallel path).
        self._spawn = spawn_fn or dispatch_module.production_spawn
        self._clock = clock or time.time
        # The EXISTING read-only Herdr observability projection (I5
        # D2), injected so hermetic tests never touch a real target
        # tree; production wires herdr.observe. It is called with the
        # leased workspace realpath only — a FIXED read-only call, no
        # arbitrary-command surface.
        self._observe = observer_fn or _production_observer

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
        try:
            live_digest = control_policy_digest(self.control_realpath)
        except DigestError as exc:
            return None, _refused(PROBLEM_POLICY_DRIFT, str(exc))
        if live_digest != entry["control_identity"][
            "policy_digest_sha256"
        ]:
            return None, _refused(
                PROBLEM_POLICY_DRIFT,
                "the LIVE control policy digest does not match the"
                " one this workflow was authorized under; the policy"
                " surface drifted between authorization and use",
            )
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

        The gate runs first and a refusal writes nothing anywhere.
        Sensitive values (paths, URLs, baselines, handoff bytes) are
        resolved from the record INSIDE the action handlers — the
        caller has no way to supply one. ``capability`` is the
        Runtime-issued one-shot internal token (I3): it must be bound
        to exactly this (workflow, action, revision), unconsumed and
        unexpired; it is validated LAST (so every record/authority
        refusal above it writes nothing, capability store included)
        and CONSUMED DURABLY before any effect — the second
        presentation of the same capability is refused with its own
        code. Codex never sees or supplies one: the only production
        caller is the Runtime, in-process.
        """
        if action not in BROKER_ACTIONS:
            return _refused(
                PROBLEM_UNKNOWN_ACTION,
                "unknown broker action %r; the action set is fixed"
                % (action,),
            )
        with store_module.exclusive_store_lock(self.store.directory):
            try:
                workflows = self.store.load()
            except store_module.StoreError as exc:
                return _refused(PROBLEM_STORE_UNREADABLE, str(exc))
            entry, refusal = self._gate(
                workflows, workflow_id, action, revision
            )
            if refusal is not None:
                return refusal
            consumed, problem, detail = (
                capability_module.validate_and_consume(
                    self.store.directory, capability, workflow_id,
                    action, revision, self._clock(),
                )
            )
            if not consumed:
                return _refused(problem, detail)
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
        record_module.apply_transition(
            entry, record_module.PHASE_WORKSPACE_READY
        )
        self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])

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
        # The spawn request: exactly three fields, all resolved from
        # the protected record. The INITIAL dispatch is the stored
        # handoff text BYTE-EXACT (Supervisor-first); a FOLLOW-UP
        # (D6) is a corrective brief built ONLY from authority fields
        # + recorded failed-acceptance evidence, carrying no technical
        # solution.
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
            return _refused(problem, detail)
        observation = self._observation_context(entry)
        observation_changed = self._note_observation(entry, observation)
        if not observation["target_complete"]:
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
        for precheck in (_gate_evidence_complete, _gate_evidence_valid):
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
            return _refused(
                PROBLEM_TURN_NOT_COMPLETED,
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
        """One guarded read-only observation (either the leased
        workspace or the control repository); None on any failure —
        the caller treats None as a degraded observation."""
        try:
            raw = self._observe(repo_path)
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
        recorded child (its `repo` REALPATH equal to the leased
        workspace realpath EXACTLY), the leased workspace's own
        observation reporting the SAME task id, both observations
        source-scoped supported (R-6 condition 3, the registered
        reconcile consumed set — never global completeness), and
        EXACTLY ONE candidate. The deterministic alias is never
        consulted: herd's child records carry none.
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
        # Control-side observation: the recorded child evidence.
        control_raw = self._observe_raw(self.control_realpath)
        supported, blocking = evidence_module.observation_supports(
            control_raw, evidence_module.RECONCILE_CONSUMED_SOURCES
        )
        if not supported:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the control-side observation is degraded in a"
                " consumed source (%s); a partial view is never a"
                " binding proof" % ", ".join(sorted({
                    str(d.get("source")) for d in blocking
                })),
            )
        children = (
            control_raw.get("children")
            if isinstance(control_raw.get("children"), dict) else {}
        )
        if children.get("truncated") is True:
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_TRUNCATED,
                "the recorded child listing is TRUNCATED; a listing"
                " that may omit a candidate is never a binding proof",
            )
        if children.get("state") not in ("available", "empty"):
            return self._reconcile_block(
                workflows, entry, PROBLEM_RECONCILE_DEGRADED,
                "the recorded child listing is not cleanly readable"
                " (state %r)" % (children.get("state"),),
            )
        listed = (
            children.get("listed")
            if isinstance(children.get("listed"), list) else []
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
                " workspace (exact realpath comparison over %d"
                " recorded child(ren)); nothing provable to bind"
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

    def _release(self, workflows, entry):
        if entry["phase"] not in record_module.TERMINAL_PHASES:
            return _refused(
                PROBLEM_WRONG_PHASE,
                "release requires a terminal phase; the workflow is"
                " %s" % entry["phase"],
            )
        ok, problem, detail = workspace_module.release(
            entry, self.workspaces_root, now=self._clock()
        )
        if not ok:
            return _refused(problem, detail)
        self.store.save(workflows)
        return BrokerOutcome(True, phase=entry["phase"])
