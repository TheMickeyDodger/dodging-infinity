"""The Mission State operations of ``MissionService`` (Task 5).

``MissionStateOperations`` is mixed into ``MissionService``; it owns no
store, lock, clock or id minter of its own and reaches those through the
service it is mixed into. Every mutating operation here:

- takes ``(mission_id, operation_id, expected_sequence, ..., context)``
  and nothing that could supply, widen, replace or weaken the approved
  contract (R-17): no requirement set, budget, staleness bound,
  degradation permission, severity or required-ness flag is a parameter;
- holds the store's cross-process lock around its whole load-modify-save
  cycle and commits the state record, the consumed reservation and any
  progress change in the ONE atomic write the store already performs;
- consumes a DI-minted, durably reserved ``state_operation`` id (R-6):
  a caller-chosen id refuses ``mission_unknown_state_operation_id``, a
  foreign principal refuses ``mission_state_operation_context_conflict``,
  a consumed id replayed with an identical content digest returns the
  recorded outcome with ``idempotent: true`` and mutates nothing (no
  budget is consumed), a different digest refuses
  ``mission_state_operation_conflict``;
- takes ``expected_sequence`` and refuses ``mission_state_stale_sequence``
  on a mismatch without mutation (R-7), so of two concurrent writers
  exactly one wins;
- refuses on a terminal record (``mission_state_terminal``).

Contract activation and staleness (R-5). ``activate_proof_contract``
derives the contract from the current revision's approved proposal and
from nowhere else; it refuses ``mission_state_no_proof_contract`` when
that proposal carries none, ``mission_state_not_authorized`` unless the
ONE validation path accepts an authorization for the Mission at its
current revision right now, and ``mission_state_contract_already_active``
when the current revision is already activated: there is no update,
patch or override. Every contract-dependent operation re-derives the
active contract on each call and refuses ``mission_state_contract_stale``
when the latest activation is not for the current revision, when the
recomputed digests disagree with the recorded binding, or when the bound
authorization no longer validates. Changing the contract is an EDIT plus
a fresh APPROVE, enforced by Task 4 machinery this module never touches:
nothing here issues, revokes or reads a mutable authorization field, and
nothing here writes ``mission["state"]``.

Closure (R-10, R-21, R-22, R-23). ``complete_successfully`` runs the full
registry-aware ``closure_failures`` and refuses with the FIRST failure's
own code; it never reads remaining budget (the one budget statement is
in ``mission.progress``). ``close_unsuccessful`` verifies an asserted
reason against local state (``mission_state_closure_not_provable``);
``abandon`` asserts nothing. ``get_state`` is the read-time projection:
the record, the re-derived contract status, proof, readiness, dependency
slots, budget, the registry-aware ``closure_eligibility`` block, and the
latest checkpoint with that block attached at read time (never stored).

Reference lists are normalized ONCE at the boundary (R-38): ``artifact_ids``
and ``derived_from`` become sorted, duplicate-free lists BEFORE both the
invocation digest and the stored effect are formed, following the
repository idiom (``validate_proposal`` sorts the action scope before
digesting), so hashing, storage and re-derivation see one canonical list.
Consequence, deliberate: ``[A, A]``, ``[A]`` and any reordering are the
SAME invocation and replay-equivalent — a replay with ``[A]`` after
``[A, A]`` returns the recorded outcome — while a genuinely different set
still conflicts.

Stated limits. This layer does not enforce, and cannot prove, separation
of duties between the submitter and the acceptor of evidence: it verifies
a configured transport credential, never a human identity, so a
different-principal rule would be enforceable only against a caller that
chose to present a different context. Submission and acceptance
provenance are recorded separately and are independently readable, so a
later policy layer could enforce separation without a schema change.
An identical rebind of a dependency slot is an ordinary accepted
operation whose effect is empty: it consumes its operation id, appends
an applied operation and advances ``sequence`` (R-28); it is not a replay
and does not report ``idempotent: true``; its recorded outcome says
``new_binding: false`` (R-31.3).

Outcomes (R-31). Every operation records a closed, typed outcome for its
kind (``state.OUTCOME_KEYS_BY_KIND``) — the operation's own identity and
sequence, the progress the record held at that sequence, and the effect's
identifiers — and the store reconciles that ledger two-way against the
effect records on every load and save (``mission.state_reconcile``). An
exact replay returns the stored outcome unchanged: genuine history,
validated, never recomputed from later or current state (R-31.4).

Nothing here starts, hands off, runs, reads a locator, or performs any
external effect; a replay has no external effect to repeat.
"""

import copy

from mission import authorization as authorization_module
from mission import manifest
from mission import progress as progress_module
from mission import record
from mission import state as state_module
from mission import store as store_module

PROBLEM_UNKNOWN_STATE_OPERATION_ID = "mission_unknown_state_operation_id"
PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT = "mission_state_operation_context_conflict"
PROBLEM_STATE_OPERATION_CONFLICT = "mission_state_operation_conflict"
PROBLEM_STALE_SEQUENCE = "mission_state_stale_sequence"
PROBLEM_NO_PROOF_CONTRACT = "mission_state_no_proof_contract"
PROBLEM_STATE_NOT_AUTHORIZED = "mission_state_not_authorized"
PROBLEM_CONTRACT_ALREADY_ACTIVE = "mission_state_contract_already_active"
PROBLEM_CONTRACT_STALE = "mission_state_contract_stale"
PROBLEM_NO_ACTIVE_CONTRACT = "mission_state_no_active_contract"
PROBLEM_UNKNOWN_REQUIREMENT = "mission_state_unknown_requirement"
PROBLEM_EVIDENCE_ALREADY_ACCEPTED = "mission_state_evidence_already_accepted"
PROBLEM_EVIDENCE_INVALIDATED = state_module.PROBLEM_EVIDENCE_INVALIDATED
PROBLEM_EVIDENCE_KIND_NOT_DECLARED = "mission_state_evidence_kind_not_declared"
PROBLEM_BLOCKER_ALREADY_RESOLVED = "mission_state_blocker_already_resolved"
PROBLEM_UNKNOWN_DEPENDENCY_SLOT = "mission_state_unknown_dependency_slot"
PROBLEM_DEPENDENCY_UNKNOWN_MISSION = "mission_state_dependency_unknown_mission"
PROBLEM_DEPENDENCY_ALREADY_RESOLVED = "mission_state_dependency_already_resolved"
PROBLEM_CHECKPOINT_BUDGET_EXHAUSTED = "mission_state_checkpoint_budget_exhausted"
PROBLEM_ARTIFACT_ROLE_MISMATCH = "mission_state_artifact_role_mismatch"


def _normalized_ids(value):
    """Sorted, duplicate-free reference list when ``value`` is a list of
    strings; anything else is passed through for the validators to
    refuse."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return sorted(set(value))
    return value


class _Operation(object):
    """Everything one accepted mutation works against, inside the lock."""

    def __init__(self, document, mission, state, now, provenance, operation_id,
                 sequence, activation, contract):
        self.document = document
        self.mission = mission
        self.state = state
        self.now = now
        self.provenance = provenance
        self.operation_id = operation_id
        self.sequence = sequence
        self.activation = activation
        self.contract = contract

    @property
    def activation_id(self):
        return self.activation["activation_id"]

    def requirement(self, key):
        for requirement in self.contract["requirements"]:
            if requirement["key"] == key:
                return requirement
        record.fail(PROBLEM_UNKNOWN_REQUIREMENT,
                    "the approved contract declares no requirement %r" % (key,))

    def evidence(self, evidence_id):
        record.require_id(evidence_id, record.EVIDENCE_ID_PREFIX, "evidence_id")
        evidence = state_module.evidence_by_id(self.state, evidence_id)
        if evidence is None:
            record.fail(state_module.PROBLEM_UNKNOWN_EVIDENCE,
                        "mission %s holds no evidence %s"
                        % (self.state["mission_id"], evidence_id))
        return evidence

    def accepted_evidence(self, evidence_id):
        evidence = self.evidence(evidence_id)
        if evidence["invalidation"] is not None:
            record.fail(PROBLEM_EVIDENCE_INVALIDATED,
                        "evidence %s was invalidated and resolves nothing"
                        % evidence_id)
        if evidence["acceptance"] is None:
            record.fail(state_module.PROBLEM_EVIDENCE_NOT_ACCEPTED,
                        "evidence %s is not ACCEPTED; submission alone resolves"
                        " nothing" % evidence_id)
        return evidence

    def artifacts_exist(self, artifact_ids, location):
        if not isinstance(artifact_ids, list):
            record.fail(record.PROBLEM_BAD_TYPE, "%s must be a list" % location)
        for artifact_id in artifact_ids:
            record.require_id(artifact_id, record.ARTIFACT_ID_PREFIX, location)
            if state_module.artifact_by_id(self.state, artifact_id) is None:
                record.fail(state_module.PROBLEM_UNKNOWN_ARTIFACT,
                            "mission %s holds no artifact %s"
                            % (self.state["mission_id"], artifact_id))
        return sorted(set(artifact_ids))

    def transition(self, target):
        current = self.state["progress"]
        if current != target or target == state_module.PROGRESS_IN_PROGRESS:
            state_module.validate_progress_transition(current, target)
        self.state["progress"] = target


class MissionStateOperations(object):
    """Mixed into ``MissionService``; see the module docstring."""

    # -- identity ----------------------------------------------------------

    def mint_state_operation_id(self, context):
        """A DI-owned state operation id, durably reserved for ``context``."""
        return self._reserve(store_module.RESERVATION_KIND_STATE_OPERATION, context)

    # -- the shared pipeline --------------------------------------------------

    @staticmethod
    def _applied_operation(document, operation_id):
        for mission_id, state in document["mission_state"].items():
            for entry in state["applied_operations"]:
                if entry["operation_id"] == operation_id:
                    return mission_id, entry
        return None, None

    def _live_authorization(self, document, mission, now):
        for authorization_id in mission["authorization_ids"]:
            if authorization_module.validate_authorization_use(
                document, authorization_id, mission["mission_id"],
                mission["current_revision"], now,
            ).valid:
                return document["authorizations"][authorization_id]
        return None

    def _bound_contract(self, document, mission, state, now):
        """The active contract, re-derived from the bound revision's
        approved proposal and re-validated for liveness (R-5.4)."""
        activation = state_module.latest_activation(state)
        if activation is None:
            record.fail(PROBLEM_NO_ACTIVE_CONTRACT,
                        "mission %s has no activated proof contract"
                        % mission["mission_id"])
        if activation["revision"] != mission["current_revision"]:
            record.fail(PROBLEM_CONTRACT_STALE,
                        "the active contract binds revision %d but mission %s is"
                        " at revision %d; activate the contract of the current"
                        " revision after it is approved"
                        % (activation["revision"], mission["mission_id"],
                           mission["current_revision"]))
        try:
            contract = store_module.activation_contract(
                document, mission, activation, "activation")
        except record.MissionError as exc:
            record.fail(PROBLEM_CONTRACT_STALE, str(exc))
        check = authorization_module.validate_authorization_use(
            document, activation["authorization_id"], mission["mission_id"],
            activation["revision"], now,
        )
        if not check.valid:
            record.fail(PROBLEM_CONTRACT_STALE,
                        "the authorization the active contract was activated"
                        " under no longer validates (%s: %s)"
                        % (check.problem, check.detail))
        return activation, contract

    def _state_projection(self, document, mission, state):
        return {
            "mission_id": mission["mission_id"],
            "sequence": state["sequence"],
            "progress": state["progress"],
        }

    def _apply(self, kind, mission_id, operation_id, expected_sequence, context,
               arguments, apply, needs_contract=True):
        record.require_context(context)
        record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
        record.require_id(operation_id, record.STATE_OPERATION_ID_PREFIX,
                          "operation_id")
        record.require_int(expected_sequence, "expected_sequence", minimum=0)
        digest = state_module.invocation_digest(kind, mission_id, expected_sequence,
                                                 arguments)
        with self._store.lock():
            document = self._store.load()
            reservation = self._reservation(
                document, operation_id,
                store_module.RESERVATION_KIND_STATE_OPERATION, context,
                PROBLEM_UNKNOWN_STATE_OPERATION_ID,
                PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
            )
            mission = self._mission(document, mission_id)
            if reservation["consumed_by"] is not None:
                applied_mission, applied = self._applied_operation(document,
                                                                   operation_id)
                if applied is None or applied_mission != mission_id or (
                    applied["content_digest_sha256"] != digest
                ):
                    record.fail(PROBLEM_STATE_OPERATION_CONFLICT,
                                "state operation %s was already applied with"
                                " different content; nothing was changed"
                                % operation_id)
                return dict(copy.deepcopy(applied["outcome"]), idempotent=True)
            now = self._now()
            state = document["mission_state"].get(mission_id)
            if state is None:
                state = state_module.new_state_record(mission_id, now)
            if expected_sequence != state["sequence"]:
                record.fail(PROBLEM_STALE_SEQUENCE,
                            "expected sequence %d but mission %s state is at"
                            " sequence %d; re-read the state and retry against"
                            " the current sequence"
                            % (expected_sequence, mission_id, state["sequence"]))
            if state["progress"] in state_module.TERMINAL_PROGRESS_STATES:
                record.fail(state_module.PROBLEM_PROGRESS_TERMINAL,
                            "mission %s progress is %s; terminal is terminal"
                            % (mission_id, state["progress"]))
            activation = contract = None
            if needs_contract:
                activation, contract = self._bound_contract(document, mission,
                                                            state, now)
            provenance = record.provenance_record(
                context, now, record.REFERENCE_KIND_STATE_OPERATION, operation_id,
                mission_id, mission["current_revision"],
            )
            # The ledger entry is appended BEFORE the effect is applied, so
            # every derivation the effect reads (budget from the ledger,
            # checkpoint fields) sees exactly what the store's later
            # recomputation at this sequence will see. Its outcome is
            # filled in once the effect is known; on any refusal the whole
            # in-memory document is discarded unsaved.
            try:
                entry = state_module.append_applied_operation(
                    state, operation_id, kind, digest, now, provenance, {})
            except record.MissionError as exc:
                if exc.problem == state_module.PROBLEM_STATE_FULL:
                    raise store_module.MissionStoreError(
                        str(exc), store_module.PROBLEM_STORE_FULL)
                raise
            operation = _Operation(document, mission, state, now, provenance,
                                   operation_id, entry["sequence"], activation,
                                   contract)
            outcome = apply(operation)
            outcome.update(self._state_projection(document, mission, state))
            outcome["operation_id"] = operation_id
            outcome["sequence"] = operation.sequence
            entry["outcome"] = outcome
            reservation["consumed_by"] = operation_id
            document["mission_state"][mission_id] = state
            self._store.save(document)
        return dict(copy.deepcopy(outcome), idempotent=False)

    # -- read -------------------------------------------------------------------

    def get_state(self, mission_id):
        """The read-time projection of a Mission's Task 5 state."""
        document = self._store.load()
        mission = self._mission(document, mission_id)
        now = self._now()
        state = document["mission_state"].get(mission_id)
        projection = {
            "mission_id": mission_id,
            "record": None if state is None else copy.deepcopy(state),
            "sequence": 0 if state is None else state["sequence"],
            "progress": (state_module.PROGRESS_NOT_STARTED if state is None
                         else state["progress"]),
            "contract": {"active": False, "activation_id": None, "revision": None,
                         "authorization_id": None, "current": False,
                         "authority_live": False, "problem": None, "content": None},
            "proof": None, "readiness": None, "dependencies": None, "budget": None,
            "closure_eligibility": None, "latest_checkpoint": None,
        }
        if state is None:
            return projection
        activation = state_module.latest_activation(state)
        if activation is None:
            return projection
        projection["contract"].update({
            "active": True, "activation_id": activation["activation_id"],
            "revision": activation["revision"],
            "authorization_id": activation["authorization_id"],
        })
        try:
            _, contract = self._bound_contract(document, mission, state, now)
        except record.MissionError as exc:
            projection["contract"]["problem"] = exc.problem
            projection["contract"]["current"] = (
                activation["revision"] == mission["current_revision"])
            return projection
        registry = store_module.registry_view(document)
        activation_id = activation["activation_id"]
        projection["contract"].update({"current": True, "authority_live": True,
                                       "content": copy.deepcopy(contract)})
        projection["proof"] = progress_module.evaluate_proof(
            contract, state, activation_id, now)
        projection["readiness"] = progress_module.readiness(contract, state, now)
        projection["dependencies"] = progress_module.dependency_status(
            contract, state, activation_id)
        projection["budget"] = progress_module.budget(contract, state)
        projection["closure_eligibility"] = progress_module.closure_eligibility(
            contract, state, activation_id, now, registry)
        if state["checkpoints"]:
            latest = copy.deepcopy(state["checkpoints"][-1])
            latest["closure_eligibility"] = projection["closure_eligibility"]
            projection["latest_checkpoint"] = latest
        return projection

    # -- contract -----------------------------------------------------------------

    def activate_proof_contract(self, mission_id, operation_id, expected_sequence,
                                context):
        def apply(op):
            entry = manifest.current_revision_entry(op.mission)
            contract = entry["proposal"].get("proof_contract")
            if contract is None:
                record.fail(PROBLEM_NO_PROOF_CONTRACT,
                            "revision %d of mission %s carries no proof_contract;"
                            " a contract is proposed and approved as part of the"
                            " revision, never supplied here"
                            % (entry["revision"], mission_id))
            authorization = self._live_authorization(op.document, op.mission, op.now)
            if authorization is None:
                record.fail(PROBLEM_STATE_NOT_AUTHORIZED,
                            "no authorization is valid for mission %s at revision"
                            " %d right now" % (mission_id, entry["revision"]))
            latest = state_module.latest_activation(op.state)
            if latest is not None and latest["revision"] == entry["revision"]:
                record.fail(PROBLEM_CONTRACT_ALREADY_ACTIVE,
                            "revision %d of mission %s is already activated as %s;"
                            " there is no replacement, update or override"
                            % (entry["revision"], mission_id, latest["activation_id"]))
            activation_id = self._fresh_id(
                record.PROOF_CONTRACT_ID_PREFIX,
                set(a["activation_id"] for a in op.state["contract_activations"]))
            op.state["contract_activations"].append(state_module.new_activation(
                activation_id, entry["revision"], entry["proposal_digest_sha256"],
                authorization["authorization_id"],
                authorization["authorization_digest_sha256"],
                record.proof_contract_digest(contract), op.now, op.provenance,
                op.operation_id, op.sequence,
            ))
            if op.state["progress"] == state_module.PROGRESS_NOT_STARTED:
                op.transition(state_module.PROGRESS_IN_PROGRESS)
            return {
                "activation_id": activation_id,
                "revision": entry["revision"],
                "proposal_digest_sha256": entry["proposal_digest_sha256"],
                "contract_digest_sha256": record.proof_contract_digest(contract),
                "authorization_id": authorization["authorization_id"],
            }
        return self._apply(state_module.OPERATION_ACTIVATE_CONTRACT, mission_id,
                           operation_id, expected_sequence, context, {}, apply,
                           needs_contract=False)

    # -- claims, artifacts, evidence ------------------------------------------------

    def record_claim(self, mission_id, operation_id, expected_sequence,
                     requirement_key, statement, context):
        def apply(op):
            op.requirement(requirement_key)
            claim_id = self._fresh_id(record.CLAIM_ID_PREFIX,
                                      set(c["claim_id"] for c in op.state["claims"]))
            op.state["claims"].append(state_module.new_claim(
                claim_id, op.activation_id, requirement_key, statement, op.now,
                op.provenance, op.operation_id, op.sequence))
            return {"claim_id": claim_id, "requirement_key": requirement_key}
        return self._apply(state_module.OPERATION_RECORD_CLAIM, mission_id,
                           operation_id, expected_sequence, context,
                           {"requirement_key": requirement_key, "statement": statement},
                           apply)

    def record_artifact(self, mission_id, operation_id, expected_sequence, key,
                        role, locator_kind, locator, content_digest_sha256,
                        available, derived_from, context):
        derived_from = _normalized_ids(derived_from)

        def apply(op):
            for declared in op.contract["required_artifacts"]:
                if declared["key"] == key and declared["role"] != role:
                    record.fail(PROBLEM_ARTIFACT_ROLE_MISMATCH,
                                "the approved contract declares required artifact"
                                " %r with role %s, not %s; a differing digest is"
                                " recordable, a differing role is not"
                                % (key, declared["role"], role))
            links = op.artifacts_exist(derived_from, "derived_from")
            artifact_id = self._fresh_id(
                record.ARTIFACT_ID_PREFIX,
                set(a["artifact_id"] for a in op.state["artifacts"]))
            op.state["artifacts"].append(state_module.new_artifact(
                artifact_id, key, role, locator_kind, locator,
                content_digest_sha256, available, links, op.now, op.provenance,
                op.operation_id, op.sequence))
            return {"artifact_id": artifact_id, "key": key, "role": role}
        return self._apply(state_module.OPERATION_RECORD_ARTIFACT, mission_id,
                           operation_id, expected_sequence, context, {
                               "key": key, "role": role, "locator_kind": locator_kind,
                               "locator": locator,
                               "content_digest_sha256": content_digest_sha256,
                               "available": available,
                               "derived_from": derived_from,
                           }, apply)

    def submit_evidence(self, mission_id, operation_id, expected_sequence,
                        requirement_key, kind, content_digest_sha256, artifact_ids,
                        context):
        artifact_ids = _normalized_ids(artifact_ids)

        def apply(op):
            op.requirement(requirement_key)
            record.require_member(kind, record.EVIDENCE_KINDS, "kind")
            links = op.artifacts_exist(artifact_ids, "artifact_ids")
            evidence_id = self._fresh_id(
                record.EVIDENCE_ID_PREFIX,
                set(e["evidence_id"] for e in op.state["evidence"]))
            op.state["evidence"].append(state_module.new_evidence(
                evidence_id, op.activation_id, requirement_key, kind,
                content_digest_sha256, links, op.now, op.provenance,
                op.operation_id, op.sequence))
            return {"evidence_id": evidence_id, "requirement_key": requirement_key,
                    "kind": kind, "accepted": False}
        return self._apply(state_module.OPERATION_SUBMIT_EVIDENCE, mission_id,
                           operation_id, expected_sequence, context, {
                               "requirement_key": requirement_key, "kind": kind,
                               "content_digest_sha256": content_digest_sha256,
                               "artifact_ids": artifact_ids,
                           }, apply)

    def accept_evidence(self, mission_id, operation_id, expected_sequence,
                        evidence_id, content_digest_sha256, context):
        def apply(op):
            evidence = op.evidence(evidence_id)
            if evidence["activation_id"] != op.activation_id:
                record.fail(PROBLEM_CONTRACT_STALE,
                            "evidence %s was submitted under a superseded contract"
                            " activation and cannot be accepted under the current"
                            " one" % evidence_id)
            if evidence["invalidation"] is not None:
                record.fail(PROBLEM_EVIDENCE_INVALIDATED,
                            "evidence %s was invalidated" % evidence_id)
            if evidence["acceptance"] is not None:
                record.fail(PROBLEM_EVIDENCE_ALREADY_ACCEPTED,
                            "evidence %s is already accepted" % evidence_id)
            if evidence["kind"] not in record.SATISFYING_EVIDENCE_KINDS:
                record.fail(state_module.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING,
                            "%s evidence may be recorded but can never be accepted"
                            " as satisfying proof" % evidence["kind"])
            requirement = op.requirement(evidence["requirement_key"])
            if evidence["kind"] not in requirement["evidence_kinds"]:
                record.fail(PROBLEM_EVIDENCE_KIND_NOT_DECLARED,
                            "requirement %r does not declare %s as acceptable"
                            % (requirement["key"], evidence["kind"]))
            record.require_hex(content_digest_sha256, "content_digest_sha256", 64)
            if content_digest_sha256 != evidence["content_digest_sha256"]:
                record.fail(state_module.PROBLEM_EVIDENCE_DIGEST,
                            "the accepted digest does not equal the submitted digest"
                            " of evidence %s" % evidence_id)
            evidence["acceptance"] = state_module.new_acceptance(
                op.now, content_digest_sha256, op.activation_id, op.provenance,
                op.operation_id, op.sequence)
            return {"evidence_id": evidence_id, "accepted": True}
        return self._apply(state_module.OPERATION_ACCEPT_EVIDENCE, mission_id,
                           operation_id, expected_sequence, context, {
                               "evidence_id": evidence_id,
                               "content_digest_sha256": content_digest_sha256,
                           }, apply)

    def invalidate_evidence(self, mission_id, operation_id, expected_sequence,
                            evidence_id, reason, context):
        def apply(op):
            evidence = op.evidence(evidence_id)
            if evidence["invalidation"] is not None:
                record.fail(PROBLEM_EVIDENCE_INVALIDATED,
                            "evidence %s is already invalidated" % evidence_id)
            evidence["invalidation"] = state_module.new_invalidation(
                op.now, reason, op.provenance, op.operation_id, op.sequence)
            return {"evidence_id": evidence_id, "invalidated": True}
        return self._apply(state_module.OPERATION_INVALIDATE_EVIDENCE, mission_id,
                           operation_id, expected_sequence, context,
                           {"evidence_id": evidence_id, "reason": reason}, apply)

    # -- blockers ---------------------------------------------------------------------

    def open_blocker(self, mission_id, operation_id, expected_sequence, key,
                     description, context):
        def apply(op):
            record.require_contract_key(key, "key")
            permitted = op.contract["degradation_policy"]["permitted_blocker_keys"]
            severity = (state_module.BLOCKER_SEVERITY_DEGRADED if key in permitted
                        else state_module.BLOCKER_SEVERITY_HARD)
            blocker_id = self._fresh_id(
                record.BLOCKER_ID_PREFIX,
                set(b["blocker_id"] for b in op.state["blockers"]))
            op.state["blockers"].append(state_module.new_blocker(
                blocker_id, op.activation_id, key, severity, description, op.now,
                op.provenance, op.operation_id, op.sequence))
            if severity == state_module.BLOCKER_SEVERITY_HARD and (
                op.state["progress"] == state_module.PROGRESS_IN_PROGRESS
            ):
                op.transition(state_module.PROGRESS_BLOCKED)
            return {"blocker_id": blocker_id, "key": key, "severity": severity}
        return self._apply(state_module.OPERATION_OPEN_BLOCKER, mission_id,
                           operation_id, expected_sequence, context,
                           {"key": key, "description": description}, apply)

    def resolve_blocker(self, mission_id, operation_id, expected_sequence,
                        blocker_id, evidence_id, context):
        def apply(op):
            record.require_id(blocker_id, record.BLOCKER_ID_PREFIX, "blocker_id")
            blocker = None
            for candidate in op.state["blockers"]:
                if candidate["blocker_id"] == blocker_id:
                    blocker = candidate
            if blocker is None:
                record.fail(state_module.PROBLEM_UNKNOWN_BLOCKER,
                            "mission %s holds no blocker %s" % (mission_id, blocker_id))
            if blocker["resolution"] is not None:
                record.fail(PROBLEM_BLOCKER_ALREADY_RESOLVED,
                            "blocker %s is already resolved" % blocker_id)
            op.accepted_evidence(evidence_id)
            blocker["resolution"] = state_module.new_resolution(
                op.now, evidence_id, op.provenance, op.operation_id, op.sequence)
            if op.state["progress"] == state_module.PROGRESS_BLOCKED and (
                not state_module.active_hard_blockers(op.state)
            ):
                op.transition(state_module.PROGRESS_IN_PROGRESS)
            return {"blocker_id": blocker_id, "resolved": True,
                    "evidence_id": evidence_id}
        return self._apply(state_module.OPERATION_RESOLVE_BLOCKER, mission_id,
                           operation_id, expected_sequence, context,
                           {"blocker_id": blocker_id, "evidence_id": evidence_id},
                           apply)

    # -- dependencies ---------------------------------------------------------------

    def _check_mission_reference(self, op, reference):
        record.require_id(reference, record.MISSION_ID_PREFIX, "reference")
        if reference == op.state["mission_id"]:
            record.fail(state_module.PROBLEM_DEPENDENCY_SELF,
                        "a mission cannot depend on itself")
        if reference not in op.document["missions"]:
            record.fail(PROBLEM_DEPENDENCY_UNKNOWN_MISSION,
                        "mission %s is not in the registry" % reference)

    def _append_dependency(self, op, key, kind, reference):
        dependency_id = self._fresh_id(
            record.DEPENDENCY_ID_PREFIX,
            set(d["dependency_id"] for d in op.state["dependencies"]))
        op.state["dependencies"].append(state_module.new_dependency(
            dependency_id, op.activation_id, key, kind, reference, op.now,
            op.provenance, op.operation_id, op.sequence))
        if kind == record.DEPENDENCY_KIND_MISSION:
            graph = dict(op.document["mission_state"])
            graph[op.state["mission_id"]] = op.state
            problem = progress_module.dependency_graph_problem(graph)
            if problem is not None:
                record.fail(problem[0], problem[1])
        return dependency_id

    def bind_dependency(self, mission_id, operation_id, expected_sequence,
                        slot_key, reference, context):
        def apply(op):
            slot = None
            for candidate in op.contract["required_dependencies"]:
                if candidate["key"] == slot_key:
                    slot = candidate
            if slot is None:
                record.fail(PROBLEM_UNKNOWN_DEPENDENCY_SLOT,
                            "the approved contract declares no dependency slot %r"
                            % (slot_key,))
            if slot["kind"] == record.DEPENDENCY_KIND_MISSION:
                self._check_mission_reference(op, reference)
            else:
                record.require_str(reference, "reference",
                                   state_module.MAX_RESOURCE_REFERENCE_CHARS)
            existing = progress_module.bound_dependency(op.state, op.activation_id,
                                                        slot_key)
            if existing is not None:
                if existing["reference"] == reference:
                    # R-28: an identical rebind is an accepted operation
                    # with an empty effect; it still consumes its id and
                    # its outcome says so (R-31.3: ``new_binding`` False).
                    return {"dependency_id": existing["dependency_id"],
                            "slot_key": slot_key, "reference": reference,
                            "new_binding": False}
                record.fail(state_module.PROBLEM_DEPENDENCY_REBIND,
                            "slot %r is already bound to %r; a bound slot is never"
                            " rebound, changing a prerequisite is an EDIT plus a"
                            " fresh APPROVE" % (slot_key, existing["reference"]))
            if not progress_module.target_matches(
                slot["target"], reference, store_module.registry_view(op.document)
            ):
                record.fail(progress_module.PROBLEM_DEPENDENCY_TARGET_MISMATCH,
                            "%r does not satisfy the approved target of slot %r"
                            % (reference, slot_key))
            dependency_id = self._append_dependency(op, slot_key, slot["kind"],
                                                    reference)
            return {"dependency_id": dependency_id, "slot_key": slot_key,
                    "reference": reference, "new_binding": True}
        return self._apply(state_module.OPERATION_BIND_DEPENDENCY, mission_id,
                           operation_id, expected_sequence, context,
                           {"slot_key": slot_key, "reference": reference}, apply)

    def declare_dependency(self, mission_id, operation_id, expected_sequence,
                           kind, reference, context):
        """An extra, non-required dependency observation (R-17: adding is
        permitted, it satisfies no slot)."""
        def apply(op):
            record.require_member(kind, record.DEPENDENCY_KINDS, "kind")
            if kind == record.DEPENDENCY_KIND_MISSION:
                self._check_mission_reference(op, reference)
            else:
                record.require_str(reference, "reference",
                                   state_module.MAX_RESOURCE_REFERENCE_CHARS)
            dependency_id = self._append_dependency(op, None, kind, reference)
            return {"dependency_id": dependency_id, "slot_key": None,
                    "reference": reference, "new_binding": True}
        return self._apply(state_module.OPERATION_BIND_DEPENDENCY, mission_id,
                           operation_id, expected_sequence, context,
                           {"kind": kind, "reference": reference}, apply)

    def resolve_dependency(self, mission_id, operation_id, expected_sequence,
                           dependency_id, evidence_id, context):
        def apply(op):
            record.require_id(dependency_id, record.DEPENDENCY_ID_PREFIX,
                              "dependency_id")
            dependency = None
            for candidate in op.state["dependencies"]:
                if candidate["dependency_id"] == dependency_id:
                    dependency = candidate
            if dependency is None:
                record.fail(state_module.PROBLEM_UNKNOWN_DEPENDENCY,
                            "mission %s holds no dependency %s"
                            % (mission_id, dependency_id))
            if dependency["resolution"] is not None:
                record.fail(PROBLEM_DEPENDENCY_ALREADY_RESOLVED,
                            "dependency %s is already resolved" % dependency_id)
            op.accepted_evidence(evidence_id)
            if dependency["key"] is not None and (
                dependency["activation_id"] == op.activation_id
            ):
                narrowed = dict(op.contract, required_dependencies=[
                    s for s in op.contract["required_dependencies"]
                    if s["key"] == dependency["key"]])
                problems = progress_module.prerequisite_problems(
                    narrowed, op.state, op.activation_id,
                    store_module.registry_view(op.document))
                if problems:
                    record.fail(problems[0][0], problems[0][1])
            dependency["resolution"] = state_module.new_resolution(
                op.now, evidence_id, op.provenance, op.operation_id, op.sequence)
            return {"dependency_id": dependency_id, "resolved": True,
                    "evidence_id": evidence_id}
        return self._apply(state_module.OPERATION_RESOLVE_DEPENDENCY, mission_id,
                           operation_id, expected_sequence, context,
                           {"dependency_id": dependency_id, "evidence_id": evidence_id},
                           apply)

    # -- readiness, continuation, checkpoints ---------------------------------------

    def observe_resource_readiness(self, mission_id, operation_id, expected_sequence,
                                   resource_key, status, observed_at, context):
        def apply(op):
            record.require_contract_key(resource_key, "resource_key")
            record.require_member(status, state_module.READINESS_STATUSES, "status")
            record.require_timestamp(observed_at, "observed_at")
            if observed_at > op.now:
                record.fail(state_module.PROBLEM_TIME_INCONSISTENT,
                            "observed_at %d is in the future (now %d); a future-dated"
                            " observation is refused" % (observed_at, op.now))
            op.state["resource_readiness"].append(state_module.new_readiness_observation(
                resource_key, status, observed_at, op.provenance, op.operation_id,
                op.sequence))
            return {"resource_key": resource_key, "status": status}
        return self._apply(state_module.OPERATION_OBSERVE_RESOURCE_READINESS,
                           mission_id, operation_id, expected_sequence, context, {
                               "resource_key": resource_key, "status": status,
                               "observed_at": observed_at,
                           }, apply)

    def record_continuation(self, mission_id, operation_id, expected_sequence,
                            reason, context):
        def apply(op):
            if op.state["progress"] != state_module.PROGRESS_IN_PROGRESS:
                record.fail(state_module.PROBLEM_PROGRESS_TRANSITION,
                            "a continuation is recorded only while IN_PROGRESS;"
                            " mission %s is %s" % (mission_id, op.state["progress"]))
            op.transition(state_module.PROGRESS_IN_PROGRESS)
            # The ledger already carries THIS operation; the attempts
            # consumed before it are the ledger count minus one.
            consumed_before = progress_module.count_operations(
                op.state, state_module.OPERATION_RECORD_CONTINUATION) - 1
            declared = op.contract["continuation_budget"]["max_attempts"]
            if consumed_before >= declared:
                record.fail(state_module.PROBLEM_BUDGET_EXHAUSTED,
                            "%d of %d continuation attempts are consumed; no further"
                            " attempt is permitted and the budget is raised only by"
                            " an EDIT plus a fresh APPROVE" % (consumed_before, declared))
            attempt = consumed_before + 1
            op.state["continuations"].append(state_module.new_continuation(
                attempt, reason, op.now, op.provenance, op.operation_id, op.sequence))
            return {"attempt": attempt, "attempts_remaining": declared - attempt}
        return self._apply(state_module.OPERATION_RECORD_CONTINUATION, mission_id,
                           operation_id, expected_sequence, context,
                           {"reason": reason}, apply)

    def record_checkpoint(self, mission_id, operation_id, expected_sequence,
                          completed_work, outstanding_work, retry_condition,
                          stop_condition, context):
        def apply(op):
            declared = op.contract["continuation_budget"]["max_checkpoints"]
            if len(op.state["checkpoints"]) >= declared:
                record.fail(PROBLEM_CHECKPOINT_BUDGET_EXHAUSTED,
                            "%d of %d checkpoints are recorded; the bound is raised"
                            " only by an EDIT plus a fresh APPROVE"
                            % (len(op.state["checkpoints"]), declared))
            checkpoint_id = self._fresh_id(
                record.CHECKPOINT_ID_PREFIX,
                set(c["checkpoint_id"] for c in op.state["checkpoints"]))
            checkpoint = state_module.new_checkpoint(
                checkpoint_id, op.activation_id, op.now, completed_work,
                outstanding_work, {
                    "revision": op.activation["revision"],
                    "proposal_digest_sha256": op.activation["proposal_digest_sha256"],
                    "contract_digest_sha256": op.activation["contract_digest_sha256"],
                }, [], [], {}, retry_condition, stop_condition, None, None,
                op.provenance, op.operation_id, op.sequence)
            op.state["checkpoints"].append(checkpoint)
            checkpoint.update(progress_module.derive_checkpoint_fields(
                op.contract, op.state, op.activation_id, op.now))
            return {
                "checkpoint_id": checkpoint_id,
                "next_permitted_step": checkpoint["next_permitted_step"],
                "refusal": copy.deepcopy(checkpoint["refusal"]),
                "budget": dict(checkpoint["budget"]),
                "active_blocker_ids": list(checkpoint["active_blocker_ids"]),
                "outstanding_dependency_ids": list(
                    checkpoint["outstanding_dependency_ids"]),
            }
        return self._apply(state_module.OPERATION_RECORD_CHECKPOINT, mission_id,
                           operation_id, expected_sequence, context, {
                               "completed_work": completed_work,
                               "outstanding_work": outstanding_work,
                               "retry_condition": retry_condition,
                               "stop_condition": stop_condition,
                           }, apply)

    # -- closure ------------------------------------------------------------------------

    def _close(self, op, progress, reason, detail):
        op.transition(progress)
        activation = state_module.latest_activation(op.state)
        op.state["closure"] = state_module.new_closure(
            progress, reason, detail, op.now,
            None if activation is None else activation["activation_id"],
            op.provenance, op.operation_id, op.sequence)
        return {"reason": reason, "detail": detail}

    def complete_successfully(self, mission_id, operation_id, expected_sequence,
                              detail, context):
        def apply(op):
            failures = progress_module.closure_failures(
                op.contract, op.state, op.activation_id, op.now,
                store_module.registry_view(op.document))
            if failures:
                record.fail(failures[0][0], failures[0][1])
            return self._close(op, state_module.PROGRESS_COMPLETED,
                               state_module.CLOSURE_REASON_PROOF_COMPLETE, detail)
        return self._apply(state_module.OPERATION_COMPLETE, mission_id,
                           operation_id, expected_sequence, context,
                           {"detail": detail}, apply)

    def close_unsuccessful(self, mission_id, operation_id, expected_sequence,
                           reason, detail, context):
        asserting = reason in (state_module.CLOSURE_REASON_BUDGET_EXHAUSTED,
                               state_module.CLOSURE_REASON_HARD_BLOCKER)

        def apply(op):
            allowed = state_module.CLOSURE_REASONS_BY_PROGRESS[
                state_module.PROGRESS_CLOSED_UNSUCCESSFUL]
            if reason not in allowed:
                record.fail(state_module.PROBLEM_CLOSURE,
                            "%r is not an unsuccessful-closure reason; the reasons"
                            " are %s" % (reason, ", ".join(allowed)))
            if asserting:
                probe = dict(progress=state_module.PROGRESS_CLOSED_UNSUCCESSFUL,
                             reason=reason, sequence=op.state["sequence"],
                             closed_at=op.now, activation_id=op.activation_id)
                problem = progress_module.closure_proof_problem(op.contract,
                                                                op.state, probe)
                if problem is not None:
                    record.fail(progress_module.PROBLEM_CLOSURE_NOT_PROVABLE, problem)
            return self._close(op, state_module.PROGRESS_CLOSED_UNSUCCESSFUL,
                               reason, detail)
        return self._apply(state_module.OPERATION_CLOSE_UNSUCCESSFUL, mission_id,
                           operation_id, expected_sequence, context,
                           {"reason": reason, "detail": detail}, apply,
                           needs_contract=asserting)

    def abandon(self, mission_id, operation_id, expected_sequence, detail, context):
        def apply(op):
            return self._close(op, state_module.PROGRESS_ABANDONED,
                               state_module.CLOSURE_REASON_CALLER_ABANDONED, detail)
        return self._apply(state_module.OPERATION_ABANDON, mission_id, operation_id,
                           expected_sequence, context, {"detail": detail}, apply,
                           needs_contract=False)
