"""``MissionService``: the production entry points of the Mission Core.

Every mutating operation holds the store's cross-process lock around
its whole load-modify-save cycle and commits registry, ledger and
reservations in ONE atomic write. Every read validates the whole
document; an unreadable or malformed store fails closed everywhere.

Identity issuance and replay (amendment A-2'). ``mint_request_id`` and
``mint_decision_id`` mint a DI-owned id and durably RESERVE it with the
context that asked for it (an authenticated one, or for a decision id the
operator-attested one; Task 8). ``propose`` and
``apply_human_decision`` accept only a currently reserved id whose
recorded context matches the calling context: an unknown or
caller-chosen id refuses (``mission_unknown_request_id`` /
``mission_unknown_decision_id``), a reserved id presented from another
principal refuses (``mission_request_context_conflict`` /
``mission_decision_context_conflict``). A consumed request id replayed
with the SAME proposal content (compared against the revision-1 content
it was bound to, never the current revision) returns the same Mission
at its current revision with ``idempotent: true``; different content
refuses ``mission_request_id_conflict`` without mutation. A consumed
decision id replayed with the same decision digest (content including
the requested expiry; excluding the processing timestamps) returns the
recorded outcome and issues nothing; a different digest refuses
``mission_decision_id_conflict``. Stated residual limit: if the first
response is lost before the caller learns its id, a retry mints a new
id and creates a second Mission. The duplicate carries no authority.

Decisions. ``apply_human_decision`` is the ONLY function in the
repository that issues a Mission Authorization (it is the single caller
of ``issue_mission_authorization``). It refuses a stale revision
(``mission_stale_revision``), an unwired transition, and an approved
scope wider than the requested one. APPROVE issues one authorization
bound to the exact revision and manifest digest, appends ``ISSUED``, and
moves the Mission to AUTHORIZED — nothing is dispatched, routed, or run.
DENY appends ``DENIED`` and moves to DENIED; only a new revision and a
new approval can proceed. EDIT appends the next exact revision; when the
superseded revision was AUTHORIZED, the same atomic write revokes every
authorization bound to it (``superseded_by_edit``), appends
``INVALIDATED_BY_EDIT`` per authorization, and returns the Mission to
AWAITING_DECISION. An authorization whose ``expires_at`` has passed is
recorded as ``EXPIRED`` in the ledger on the next mutation of its
Mission; validation refuses it as expired regardless.

Validation. ``validate_authorization`` and ``check_parent_authority``
(the P1-A6 parent seam: resolve by authorization digest, require the
authorization's Mission id to equal the presented id, then ask about the
delivery target) load the store and forward to the ONE validation path
in ``mission.authorization``; they reimplement no check.
"""

import copy

from mission import authorization as authorization_module
from mission import decision as decision_module
from mission import manifest
from mission import progress as progress_module
from mission import record
from mission import state_service
from mission import store as store_module

PROBLEM_UNKNOWN_REQUEST_ID = "mission_unknown_request_id"
PROBLEM_REQUEST_ID_CONFLICT = "mission_request_id_conflict"
PROBLEM_REQUEST_CONTEXT_CONFLICT = "mission_request_context_conflict"
PROBLEM_UNKNOWN_DECISION_ID = "mission_unknown_decision_id"
PROBLEM_DECISION_ID_CONFLICT = "mission_decision_id_conflict"
PROBLEM_DECISION_CONTEXT_CONFLICT = "mission_decision_context_conflict"
PROBLEM_STALE_REVISION = "mission_stale_revision"
# Task 8: the withdrawal marker and its single writer.
PROBLEM_PROPOSAL_WITHDRAWN = "mission_proposal_withdrawn"
PROBLEM_NOT_WITHDRAWABLE = "mission_not_withdrawable"
PROBLEM_WITHDRAWAL_CONTEXT_CONFLICT = "mission_withdrawal_context_conflict"
PROBLEM_WITHDRAWAL_KEY = "mission_withdrawal_key_mismatch"
PROBLEM_WITHDRAWAL_KEY_EXPIRED = "mission_withdrawal_key_expired"
PROBLEM_PROPOSAL_DIGEST_MISMATCH = "mission_proposal_digest_mismatch"

_ISSUANCE_REASON = "approved by human decision"
_DENIAL_REASON = "denied by human decision"
_EXPIRY_REASON = "expires_at passed"


class MissionService(state_service.MissionStateOperations):
    """Propose, read, edit, decide, and validate Missions; the Mission
    State operations (Task 5) are mixed in from ``mission.state_service``
    and share this lock, store, clock and id minter."""

    def __init__(self, store, clock, mint_id=None):
        self._store = store
        self._clock = clock
        self._mint_id = mint_id or record.mint_id

    # -- helpers ---------------------------------------------------------

    def _now(self):
        return record.require_timestamp(self._clock(), "clock")

    def now(self):
        """The service clock, for adapters stamping a receive time."""
        return self._now()

    def _fresh_id(self, prefix, taken):
        for _ in range(8):
            candidate = self._mint_id(prefix)
            record.require_id(candidate, prefix, "minted id")
            if candidate not in taken:
                return candidate
        record.fail(record.PROBLEM_ID_GRAMMAR,
                    "the injected id minter keeps returning taken ids")

    def _reserve(self, kind, context):
        if kind == store_module.RESERVATION_KIND_DECISION:
            # A decision id: an AuthenticatedContext of an authenticated kind,
            # or an OperatorAttestedContext; never the unauthenticated kind.
            record.require_decision_reservation_context(context)
        elif kind == store_module.RESERVATION_KIND_REQUEST:
            record.require_context(context)
        else:
            # A state-operation id needs an authenticated principal.
            record.require_authenticated_context(context)
        prefix = store_module.RESERVATION_PREFIXES[kind]
        cap = store_module.RESERVATION_CAPS[kind]
        with self._store.lock():
            document = self._store.load()
            reservations = document["reservations"]
            held = sum(1 for r in reservations.values() if r["kind"] == kind)
            if held >= cap:
                raise store_module.MissionStoreError(
                    "%d %s ids are reserved; the hard bound is %d and a"
                    " reservation is never evicted, so no new id can be"
                    " issued" % (held, kind, cap), store_module.PROBLEM_STORE_FULL,
                )
            reserved_id = self._fresh_id(prefix, reservations)
            reservations[reserved_id] = {
                "reserved_at": self._now(),
                "kind": kind,
                "context": context.as_dict(),
                "consumed_by": None,
            }
            self._store.save(document)
        return reserved_id

    @staticmethod
    def _reservation(document, reserved_id, kind, context, unknown_problem,
                     context_problem):
        reservation = document["reservations"].get(reserved_id)
        if reservation is None or reservation["kind"] != kind:
            record.fail(unknown_problem,
                        "%s id %s was not issued and reserved by this layer;"
                        " a caller-chosen id is never accepted"
                        % (kind, reserved_id))
        if reservation["context"] != context.as_dict():
            record.fail(context_problem,
                        "%s id %s is bound to a different context"
                        % (kind, reserved_id))
        return reservation

    @staticmethod
    def _mission(document, mission_id):
        if record.id_problem(mission_id, record.MISSION_ID_PREFIX) is not None:
            record.fail(authorization_module.PROBLEM_UNKNOWN_MISSION,
                        "mission id is not well formed")
        mission = document["missions"].get(mission_id)
        if mission is None:
            record.fail(authorization_module.PROBLEM_UNKNOWN_MISSION,
                        "mission %s is not in the registry" % mission_id)
        return mission

    def _ledger(self, document, kind, now, mission_id, revision,
                authorization_id, decision_id, reason):
        ledger = document["authority_ledger"]
        if len(ledger) >= store_module.MAX_AUTHORITY_LEDGER_ENTRIES:
            raise store_module.MissionStoreError(
                "the authority ledger holds %d entries; the hard bound is %d"
                " and history is never pruned"
                % (len(ledger), store_module.MAX_AUTHORITY_LEDGER_ENTRIES),
                store_module.PROBLEM_STORE_FULL,
            )
        taken = set(entry["entry_id"] for entry in ledger)
        ledger.append(authorization_module.new_ledger_entry(
            self._fresh_id(record.LEDGER_ENTRY_ID_PREFIX, taken), kind, now,
            mission_id, revision, authorization_id, decision_id, reason,
        ))

    def _note_expirations(self, document, mission, now):
        """Append EXPIRED for every live authorization of ``mission`` whose
        expiry has passed and is not yet in the ledger."""
        recorded = set(
            e["authorization_id"] for e in document["authority_ledger"]
            if e["kind"] == authorization_module.LEDGER_EXPIRED
        )
        for authorization_id in mission["authorization_ids"]:
            authorization = document["authorizations"][authorization_id]
            expires = authorization["expires_at"]
            if expires is None or now < expires or (
                authorization["revocation"]["revoked"]
                or authorization_id in recorded
            ):
                continue
            self._ledger(document, authorization_module.LEDGER_EXPIRED, now,
                         mission["mission_id"], authorization["revision"],
                         authorization_id, None, _EXPIRY_REASON)

    @staticmethod
    def _propose_outcome(mission, request_id, idempotent):
        """The propose response: the stable Mission id plus ONE coherent
        triple — ``revision``, the exact canonical ``proposal`` of that
        revision, and its ``proposal_digest_sha256`` — all read from the
        same current revision entry. On an idempotent replay the triple
        describes the Mission as it is NOW (after any later edits) while
        the Mission id is unchanged; the replay comparison itself is still
        made against the revision-1 content the request id was bound to."""
        entry = manifest.current_revision_entry(mission)
        return {
            "mission_id": mission["mission_id"],
            "revision": entry["revision"],
            "state": mission["state"],
            "request_id": request_id,
            "idempotent": idempotent,
            "proposal": copy.deepcopy(entry["proposal"]),
            "proposal_digest_sha256": entry["proposal_digest_sha256"],
        }

    # -- identity issuance -----------------------------------------------

    def mint_request_id(self, context):
        """A DI-owned request id, durably reserved for ``context``."""
        return self._reserve(store_module.RESERVATION_KIND_REQUEST, context)

    def mint_decision_id(self, context):
        """A DI-owned decision id, durably reserved for ``context``."""
        return self._reserve(store_module.RESERVATION_KIND_DECISION, context)

    # -- propose / get -----------------------------------------------------

    def propose(self, request_id, proposal, context,
                withdrawal_key_digest_sha256=None, withdrawal_key_expires_at=None):
        record.require_context(context)
        record.require_id(request_id, record.REQUEST_ID_PREFIX, "request_id")
        clean = record.validate_proposal(proposal)
        digest = record.proposal_digest(clean)
        if (withdrawal_key_digest_sha256 is None) != (
            withdrawal_key_expires_at is None
        ):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "a withdrawal key digest is bound only with its"
                        " original expiry")
        if withdrawal_key_digest_sha256 is not None:
            record.require_hex(withdrawal_key_digest_sha256,
                               "withdrawal_key_digest_sha256", 64)
            record.require_timestamp(withdrawal_key_expires_at,
                                     "withdrawal_key_expires_at")
        with self._store.lock():
            document = self._store.load()
            reservation = self._reservation(
                document, request_id, store_module.RESERVATION_KIND_REQUEST,
                context, PROBLEM_UNKNOWN_REQUEST_ID,
                PROBLEM_REQUEST_CONTEXT_CONFLICT,
            )
            consumed_by = reservation["consumed_by"]
            if consumed_by is not None:
                mission = document["missions"][consumed_by]
                bound = mission["revisions"][0]["proposal_digest_sha256"]
                if bound != digest or mission.get(
                    "withdrawal_key_digest_sha256"
                ) != withdrawal_key_digest_sha256 or mission.get(
                    "withdrawal_key_expires_at"
                ) != withdrawal_key_expires_at:
                    record.fail(PROBLEM_REQUEST_ID_CONFLICT,
                                "request id %s already created mission %s with"
                                " different proposal content; nothing was"
                                " changed" % (request_id, consumed_by))
                return self._propose_outcome(mission, request_id, True)
            if len(document["missions"]) >= store_module.MAX_MISSION_RECORDS:
                raise store_module.MissionStoreError(
                    "the registry holds %d missions; the hard bound is %d and"
                    " records are never evicted"
                    % (len(document["missions"]),
                       store_module.MAX_MISSION_RECORDS),
                    store_module.PROBLEM_STORE_FULL,
                )
            now = self._now()
            mission_id = self._fresh_id(record.MISSION_ID_PREFIX,
                                        document["missions"])
            mission = manifest.new_mission_record(
                mission_id, request_id, clean, now, context,
                withdrawal_key_digest_sha256, withdrawal_key_expires_at,
            )
            document["missions"][mission_id] = mission
            reservation["consumed_by"] = mission_id
            self._store.save(document)
        return self._propose_outcome(mission, request_id, False)

    def get(self, mission_id):
        """A deep copy of the Mission record and its authorization records,
        plus ``live_authorization_id``: the authorization the ONE central
        validator accepts for the current revision right now, or None."""
        document = self._store.load()
        mission = self._mission(document, mission_id)
        now = self._now()
        live = [
            authorization_id
            for authorization_id in mission["authorization_ids"]
            if authorization_module.validate_authorization_use(
                document, authorization_id, mission_id,
                mission["current_revision"], now,
            ).valid
        ]
        return {
            "record": copy.deepcopy(mission),
            "authorizations": [
                copy.deepcopy(document["authorizations"][authorization_id])
                for authorization_id in mission["authorization_ids"]
            ],
            "live_authorization_id": live[0] if live else None,
        }

    # -- withdrawal marker (Task 8) ------------------------------------------

    def withdraw_proposal(self, mission_id, request_id, withdrawal_key, context):
        """Record the durable withdrawal marker on the proposer's own
        revision-1 proposal, before any decision. Not a decision and not a
        lifecycle transition: the state stays AWAITING_DECISION and
        ``apply_human_decision`` refuses every later decision on it. It is
        accepted only from the exact proposer context of an unauthenticated
        local caller proposal, bound to the creating request id; that
        context is shared by every such caller, so per-proposal ownership
        is the calling surface's one-shot control capability, and this
        method has a single pinned production caller. Idempotent: a second
        call returns the recorded marker."""
        record.require_context(context)
        record.require_id(request_id, record.REQUEST_ID_PREFIX, "request_id")
        with self._store.lock():
            document = self._store.load()
            mission = self._mission(document, mission_id)
            first = mission["revisions"][0]["provenance"]
            if mission["request_id"] != request_id or (
                record.provenance_context(first).as_dict() != context.as_dict()
            ) or context.principal_kind != (
                record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER
            ):
                record.fail(PROBLEM_WITHDRAWAL_CONTEXT_CONFLICT,
                            "only the proposer of an unauthenticated local"
                            " caller proposal, presenting its creating request"
                            " id, may withdraw it")
            # Ownership, bound in the core: the key only the creator got.
            expected = mission.get("withdrawal_key_digest_sha256")
            presented = record.withdrawal_key_digest(withdrawal_key)
            if expected is None or presented is None or presented != expected:
                record.fail(PROBLEM_WITHDRAWAL_KEY,
                            "the withdrawal key does not match the one recorded"
                            " at creation; the shared proposer context and the"
                            " request or Mission id never suffice")
            idempotent = mission.get("withdrawal") is not None
            if not idempotent:
                # Lifetime, bound in the core: the FIRST withdrawal must land
                # before the key's recorded expiry. Completing one already
                # recorded (above) stays idempotent after it.
                now = self._now()
                if now >= mission["withdrawal_key_expires_at"]:
                    record.fail(PROBLEM_WITHDRAWAL_KEY_EXPIRED,
                                "the withdrawal key of mission %s expired at %d"
                                " (now %d); nothing was changed"
                                % (mission_id,
                                   mission["withdrawal_key_expires_at"], now))
                if mission["state"] != record.STATE_AWAITING_DECISION or (
                    mission["current_revision"] != 1 or mission["decisions"]
                    or mission["authorization_ids"]
                ):
                    record.fail(PROBLEM_NOT_WITHDRAWABLE,
                                "mission %s is %s at revision %d with %d"
                                " decision(s): only a revision-1 proposal still"
                                " awaiting its first decision can be withdrawn"
                                % (mission_id, mission["state"],
                                   mission["current_revision"],
                                   len(mission["decisions"])))
                mission["withdrawal"] = manifest.new_withdrawal(mission, context,
                                                                now)
                mission["updated_at"] = now
                self._store.save(document)
        return {
            "mission_id": mission_id,
            "state": mission["state"],
            "withdrawal": copy.deepcopy(mission["withdrawal"]),
            "idempotent": idempotent,
        }

    def _require_exact_content(self, mission, envelope):
        """Called under the store lock, for the current revision only."""
        entry = manifest.current_revision_entry(mission)
        expected = envelope.expected_proposal_digest_sha256
        if expected is not None and expected != entry["proposal_digest_sha256"]:
            record.fail(PROBLEM_PROPOSAL_DIGEST_MISMATCH,
                        "the decision binds proposal digest %s but revision %d"
                        " of mission %s carries %s; nothing was changed"
                        % (expected, entry["revision"], mission["mission_id"],
                           entry["proposal_digest_sha256"]))
        if envelope.context.principal_kind not in record.APPROVE_ONLY_PRINCIPAL_KINDS:
            return
        proposer = mission["revisions"][0]["provenance"]["principal_kind"]
        if proposer != record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER:
            record.fail(record.PROBLEM_ATTESTED_APPROVAL,
                        "an operator-attested APPROVE applies only to a"
                        " proposal the local request surface made")
        requested = entry["proposal"]
        target = requested["requested_delivery_target"]
        if sorted(envelope.approved_action_scope) != sorted(
            requested["requested_action_scope"]
        ) or sorted(envelope.approved_delivery_targets) != (
            [] if target is None else [target]
        ):
            record.fail(record.PROBLEM_ATTESTED_APPROVAL,
                        "an operator-attested APPROVE must approve exactly the"
                        " requested action scope and delivery target of"
                        " revision %d" % entry["revision"])
        if envelope.expires_at > self._now() + (
            record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS
        ):
            record.fail(record.PROBLEM_ATTESTED_APPROVAL,
                        "an operator-attested APPROVE may stay valid at most %d"
                        " seconds" % record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)

    def apply_operator_attested_approval(self, context, decision_id, mission_id,
                                         revision, proposal_digest_sha256,
                                         approved_action_scope,
                                         approved_delivery_targets, expires_at):
        """The one entry point for an operator-attested APPROVE (Task 8,
        user decision): an APPROVE the Outer Operator attests it relayed from
        the human's explicit reply. Not independently verified; the
        provenance proof says so. Every rule (approve-only kind, exact digest,
        exact scope and targets, bounded expiry, local-surface proposal) is
        enforced inside ``apply_human_decision`` itself; this only builds the
        envelope. Replaying the same ``decision_id`` with the same content
        returns the recorded outcome and issues nothing."""
        # An OperatorAttestedContext only: never an AuthenticatedContext.
        record.require_operator_attested_context(context)
        envelope = decision_module.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=decision_module.DECISION_APPROVE,
            received_at=self._now(),
            approved_action_scope=sorted(approved_action_scope),
            approved_delivery_targets=sorted(approved_delivery_targets),
            expires_at=expires_at,
            expected_proposal_digest_sha256=proposal_digest_sha256,
        )
        return self.apply_human_decision(envelope)

    # -- decisions ---------------------------------------------------------

    def edit(self, mission_id, expected_revision, proposal, decision_id,
             context):
        """The EDIT decision: append the next exact revision."""
        envelope = decision_module.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=expected_revision,
            decision=decision_module.DECISION_EDIT, received_at=self._now(),
            proposal=proposal,
        )
        return self.apply_human_decision(envelope)

    def apply_human_decision(self, envelope):
        """Apply one human decision; the ONLY issuer of Mission Authorization."""
        if not isinstance(envelope, decision_module.HumanDecisionEnvelope):
            record.fail(decision_module.PROBLEM_DECISION,
                        "a decision is applied only from a HumanDecisionEnvelope")
        envelope.validate()
        digest = envelope.digest()
        with self._store.lock():
            document = self._store.load()
            reservation = self._reservation(
                document, envelope.decision_id,
                store_module.RESERVATION_KIND_DECISION, envelope.context,
                PROBLEM_UNKNOWN_DECISION_ID, PROBLEM_DECISION_CONTEXT_CONFLICT,
            )
            mission = self._mission(document, envelope.mission_id)
            if reservation["consumed_by"] is not None:
                recorded = self._recorded_decision(document, envelope.decision_id)
                if recorded is None or recorded["mission_id"] != (
                    envelope.mission_id
                ) or recorded["decision_digest_sha256"] != digest:
                    record.fail(PROBLEM_DECISION_ID_CONFLICT,
                                "decision id %s was already applied with"
                                " different content; nothing was changed"
                                % envelope.decision_id)
                return self._decision_outcome(document, recorded, True)
            # Task 8 increment 2: a terminal Mission takes no decision, and
            # neither does one whose run DI has recorded (an intent, a pause
            # or a cancel): an EDIT must not revoke authority from under it.
            self._refuse_terminal(mission)
            if mission.get("run") is not None:
                record.fail(record.PROBLEM_RUN_RECORDED,
                            "mission %s carries a run record; no decision"
                            " applies to it, and nothing was changed"
                            % envelope.mission_id)
            # Task 8 precondition: a withdrawn proposal accepts no decision.
            # An additive guard read; the state machine is unchanged.
            withdrawal = mission.get("withdrawal")
            if withdrawal is not None:
                record.fail(PROBLEM_PROPOSAL_WITHDRAWN,
                            "mission %s carries a withdrawal marker: its"
                            " proposer withdrew revision 1 at %d before any"
                            " decision, so no decision applies to it; its state"
                            " stays %s and nothing was changed"
                            % (envelope.mission_id, withdrawal["withdrawn_at"],
                               mission["state"]))
            if envelope.revision == mission["current_revision"]:
                # Exact content and the operator-attested rules, checked under
                # the store lock, atomically with the application below.
                self._require_exact_content(mission, envelope)
            if envelope.revision != mission["current_revision"]:
                record.fail(PROBLEM_STALE_REVISION,
                            "decision names revision %d but mission %s is at"
                            " revision %d; re-read the mission and decide on"
                            " the current revision"
                            % (envelope.revision, envelope.mission_id,
                               mission["current_revision"]))
            now = self._now()
            self._note_expirations(document, mission, now)
            if envelope.decision == decision_module.DECISION_APPROVE:
                outcome = self._approve(document, mission, envelope, now)
            elif envelope.decision == decision_module.DECISION_DENY:
                outcome = self._deny(document, mission, envelope, now)
            else:
                outcome = self._edit(document, mission, envelope, now)
            decision_record = decision_module.new_decision_record(
                envelope, now, outcome
            )
            manifest.append_decision(mission, decision_record)
            mission["updated_at"] = now
            reservation["consumed_by"] = envelope.decision_id
            self._store.save(document)
        return self._decision_outcome(document, decision_record, False)

    # -- the run record (Task 8 increment 2) --------------------------------
    #
    # Each method below is one locked load-modify-save, refuses without
    # writing anything, and records a FACT another component established:
    # nothing here starts, observes or stops anything. The only write a
    # terminal Mission accepts is ``complete_cancel``, which completes the
    # cancel record whose own write made the Mission CANCELLED.
    #
    # Who may call them, exactly: every run write requires an
    # ``AuthenticatedContext`` of an AUTHENTICATED principal kind
    # (``record.AUTHENTICATED_PRINCIPAL_KINDS``). The unauthenticated local
    # caller kind and the operator-attested relay kind (an
    # ``OperatorAttestedContext``) are refused on every one of them,
    # before the store is read. Pause, resume and cancel must also present
    # the authorization the Mission is bound to (its run intent's, or its
    # latest issued one before any intent), so another Mission's
    # identifiers never control this one.

    @staticmethod
    def _refuse_terminal(mission):
        if mission["state"] in record.TERMINAL_STATES:
            record.fail(record.PROBLEM_MISSION_TERMINAL,
                        "mission %s is %s, which is terminal: no further"
                        " progress, result, proof, run or decision write"
                        " applies, and nothing was changed"
                        % (mission["mission_id"], mission["state"]))

    @staticmethod
    def _run_transition(mission, target, reason, now):
        record.validate_transition(mission["state"], target)
        if (mission["state"], target) not in record.RUN_TRANSITIONS:
            record.fail(record.PROBLEM_INVALID_TRANSITION,
                        "%s -> %s is not a run transition"
                        % (mission["state"], target))
        mission.setdefault("lifecycle", []).append({
            "from_state": mission["state"], "to_state": target,
            "reason": reason, "recorded_at": now,
        })
        mission["state"] = target
        mission["updated_at"] = now

    def _run_write(self, mission_id, context, mutate, guard_terminal=True):
        record.require_authenticated_context(context)
        record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
        with self._store.lock():
            document = self._store.load()
            mission = self._mission(document, mission_id)
            if guard_terminal:
                self._refuse_terminal(mission)
            now = self._now()
            outcome = mutate(document, mission, now)
            self._store.save(document)
        return copy.deepcopy(outcome)

    @staticmethod
    def _run_of(mission, create=False):
        run = mission.get("run")
        if run is None and create:
            run = mission["run"] = manifest.new_run_block()
        return run

    @staticmethod
    def _refuse_paused(mission):
        if manifest.run_is_paused(mission.get("run")):
            record.fail(record.PROBLEM_RUN_PAUSED,
                        "mission %s is paused: DI initiates no further"
                        " progression until an explicit resume; nothing was"
                        " changed" % mission["mission_id"])

    @staticmethod
    def _require_bound_authorization(mission, authorization_id):
        """Control binds to THIS Mission's authorization: the run intent's,
        or the latest issued one before any intent."""
        run = mission.get("run")
        intent = run["intent"] if run else None
        bound = intent["authorization_id"] if intent else (
            mission["authorization_ids"][-1] if mission["authorization_ids"]
            else None)
        if bound is None or authorization_id != bound:
            record.fail(record.PROBLEM_RUN,
                        "authorization %r is not the one mission %s is bound"
                        " to; nothing was changed"
                        % (authorization_id, mission["mission_id"]))

    def record_run_intent(self, mission_id, authorization_id, revision,
                          proposal_digest_sha256, target_repository_url,
                          workspace_realpath, observed_baseline_commit_sha,
                          request_digest_sha256, handoff_digest_sha256,
                          surface_baseline_digest_sha256,
                          approved_action_scope, approved_delivery_targets,
                          context):
        """Record the run INTENT before any effect: one per Mission, only
        on an AUTHORIZED Mission whose live authorization permits the run
        scope at exactly this revision and digest, with exactly the scope
        and delivery targets that authorization carries. Intent recorded
        is not anything started; the state stays AUTHORIZED."""
        def mutate(document, mission, now):
            if mission["state"] != record.STATE_AUTHORIZED:
                record.fail(record.PROBLEM_RUN,
                            "mission %s is %s, not AUTHORIZED"
                            % (mission_id, mission["state"]))
            run = self._run_of(mission)
            if run is not None and run["intent"] is not None:
                record.fail(record.PROBLEM_RUN_ALREADY_RECORDED,
                            "mission %s already carries a run intent; one"
                            " intent per Mission, and nothing was changed"
                            % mission_id)
            self._refuse_paused(mission)
            check = authorization_module.validate_authorization_use(
                document, authorization_id, mission_id, revision, now,
                required_actions=record.RUN_REQUIRED_ACTION_SCOPE,
                expected_proposal_digest=proposal_digest_sha256,
            )
            if not check.valid:
                record.fail(check.problem, check.detail)
            authorization = document["authorizations"][authorization_id]
            if list(approved_action_scope) != authorization[
                "authorized_action_scope"
            ]:
                record.fail(record.PROBLEM_ACTION_SCOPE,
                            "the run must carry exactly the authorized action"
                            " scope %r" % authorization["authorized_action_scope"])
            if list(approved_delivery_targets) != authorization[
                "authorized_delivery_targets"
            ]:
                record.fail(record.PROBLEM_DELIVERY_TARGET,
                            "the run must carry exactly the authorized delivery"
                            " targets %r"
                            % authorization["authorized_delivery_targets"])
            # The lease discipline (increment 2c): a workspace carries at
            # most ONE non-terminal run, so a child found there while this
            # run is live cannot belong to another live Mission.
            for other_id, other in document["missions"].items():
                other_run = other.get("run")
                other_intent = other_run["intent"] if other_run else None
                if other_id != mission_id and other_intent is not None and (
                    other_intent["workspace_realpath"] == workspace_realpath
                ) and other["state"] not in record.TERMINAL_STATES:
                    record.fail(record.PROBLEM_RUN_WORKSPACE_BOUND,
                                "workspace %r is bound to mission %s's live"
                                " run; nothing was changed"
                                % (workspace_realpath, other_id))
            approved_url = manifest.current_revision_entry(mission)["proposal"][
                "repository_url"]
            if approved_url is None or target_repository_url != approved_url:
                record.fail(record.PROBLEM_REPOSITORY_IDENTITY,
                            "the run must name the approved repository %r"
                            % approved_url)
            intent = {
                "recorded_at": now, "authorization_id": authorization_id,
                "revision": revision,
                "proposal_digest_sha256": proposal_digest_sha256,
                "target_repository_url": target_repository_url,
                "workspace_realpath": workspace_realpath,
                "observed_baseline_commit_sha": observed_baseline_commit_sha,
                "request_digest_sha256": request_digest_sha256,
                "handoff_digest_sha256": handoff_digest_sha256,
                "surface_baseline_digest_sha256": surface_baseline_digest_sha256,
                "approved_action_scope": list(approved_action_scope),
                "approved_delivery_targets": list(approved_delivery_targets),
            }
            self._run_of(mission, create=True)["intent"] = intent
            mission["updated_at"] = now
            return intent
        return self._run_write(mission_id, context, mutate)

    def record_run_receipt(self, mission_id, task_id, identity_source,
                           owned_process_group, context):
        """Record that an effect RETURNED: the target identity it named
        (or ``None`` when it named none usably). Only after an intent and
        before RUNNING; a reconciliation may later bind the identity of a
        receipt that has none, exactly once. Late writes after a terminal
        state are refused."""
        def mutate(document, mission, now):
            run = self._run_of(mission)
            if run is None or run["intent"] is None:
                record.fail(record.PROBLEM_RUN,
                            "mission %s has no run intent to receipt"
                            % mission_id)
            if mission["state"] != record.STATE_AUTHORIZED:
                record.fail(record.PROBLEM_RUN,
                            "a receipt is recorded before RUNNING only")
            record.require_member(identity_source, record.RUN_IDENTITY_SOURCES,
                                  "identity_source")
            if identity_source == "reconciliation":
                self._refuse_paused(mission)
                if task_id is None:
                    record.fail(record.PROBLEM_RUN,
                                "a reconciliation binds an identity or nothing")
            receipt = run["receipt"]
            if receipt is None:
                run["receipt"] = {
                    "recorded_at": now, "task_id": task_id,
                    "identity_source": identity_source,
                    "owned_process_group": owned_process_group,
                }
            elif receipt["task_id"] is None and identity_source == (
                "reconciliation"
            ):
                receipt["task_id"] = task_id
                receipt["identity_source"] = identity_source
            else:
                record.fail(record.PROBLEM_RUN_ALREADY_RECORDED,
                            "mission %s already carries a receipt; nothing was"
                            " changed" % mission_id)
            mission["updated_at"] = now
            return run["receipt"]
        return self._run_write(mission_id, context, mutate)

    def record_observed_running(self, mission_id, task_id, already_stopped,
                                context):
        """AUTHORIZED -> RUNNING, only for the target the receipt names,
        from a read-only observation another component made."""
        def mutate(document, mission, now):
            self._refuse_paused(mission)
            run = self._run_of(mission)
            receipt = run["receipt"] if run else None
            if receipt is None or receipt["task_id"] is None or (
                receipt["task_id"] != task_id
            ):
                record.fail(record.PROBLEM_RUN,
                            "the observed target %r is not the one the receipt"
                            " names; RUNNING is never set without it" % task_id)
            record.require_bool(already_stopped, "already_stopped")
            self._run_transition(
                mission, record.STATE_RUNNING,
                record.RUN_REASON_OBSERVED_AFTER_STOP if already_stopped
                else record.RUN_REASON_OBSERVED_RUNNING, now)
            return {"state": mission["state"],
                    "reason": mission["lifecycle"][-1]["reason"]}
        return self._run_write(mission_id, context, mutate)

    def record_run_stop(self, mission_id, reason, context):
        """A durable reconciliation stop: BLOCKED with its own code."""
        def mutate(document, mission, now):
            self._refuse_paused(mission)
            record.require_member(reason, record.RECONCILE_STOP_REASONS, "reason")
            run = self._run_of(mission)
            if run is None or run["intent"] is None:
                record.fail(record.PROBLEM_RUN,
                            "mission %s has no run intent to stop" % mission_id)
            self._run_transition(mission, record.STATE_BLOCKED, reason, now)
            return {"state": mission["state"], "reason": reason}
        return self._run_write(mission_id, context, mutate)

    def record_verification(self, mission_id, conjunct_holds,
                            raw_global_completeness, supports_verification,
                            reported_result_digest_sha256, result_evidence_id,
                            observed_task_status, context):
        """DI's verification decision, recomputed HERE from the conjunct
        values. Three durable outcomes, each written in this one lock:

        - a conjunct fails: the FIRST failing conjunct stops the Mission
          durably with its own code (RUNNING -> BLOCKED, terminal);
        - every conjunct holds and ``result_evidence_id`` names an ACCEPTED
          VERIFICATION_RECORD evidence under the approved ``run_result``
          requirement, but the WHOLE approved contract is not satisfied
          (round-15 state truth): a durable ``pending_proof`` record of the
          stopped target and the evaluator's blocker codes. Non-terminal and
          recoverable: the Mission stays RUNNING, its obligations can be
          met through the existing seams, and this SAME consumed run can be
          verified again (at most ``MAX_VERIFICATION_ATTEMPTS`` blocked
          attempts), with no new intent and no renewed authority;
        - every conjunct holds, the result evidence is accepted and the
          contract is satisfied: VERIFIED (RUNNING -> COMPLETED)."""
        def mutate(document, mission, now):
            self._refuse_paused(mission)
            if mission["state"] != record.STATE_RUNNING:
                record.fail(record.PROBLEM_RUN,
                            "mission %s is %s; only a RUNNING Mission is"
                            " verified" % (mission_id, mission["state"]))
            names = [name for name, _ in record.VERIFY_CONJUNCTS]
            if not isinstance(conjunct_holds, dict) or sorted(
                conjunct_holds
            ) != sorted(names):
                record.fail(record.PROBLEM_RUN,
                            "the verification must decide every conjunct")
            conjuncts = [{"name": name, "holds": conjunct_holds[name]}
                         for name in names]
            verified, failed = record.verification_outcome(conjunct_holds)
            run = self._run_of(mission)
            if verified:
                self._require_accepted_result(document, mission_id,
                                              result_evidence_id)
                failures = self._contract_failures(document, mission, now)
                if failures:
                    return self._record_pending_proof(
                        run, now, failures, raw_global_completeness,
                        supports_verification, result_evidence_id,
                        observed_task_status, mission)
            run["verification"] = {
                "decided_at": now, "verified": verified,
                "failed_conjunct": failed, "conjuncts": conjuncts,
                "raw_global_completeness": raw_global_completeness,
                "supports_verification": supports_verification,
                "reported_result_digest_sha256": reported_result_digest_sha256,
                "result_evidence_id": result_evidence_id if verified else None,
                "observed_task_status": observed_task_status,
            }
            self._run_transition(
                mission,
                record.STATE_COMPLETED if verified else record.STATE_BLOCKED,
                record.RUN_REASON_VERIFIED if verified else failed, now)
            return run["verification"]
        return self._run_write(mission_id, context, mutate)

    def _contract_failures(self, document, mission, now):
        """Task 8 increment 2c: VERIFIED also needs the WHOLE approved proof
        contract satisfied, decided by the EXISTING evaluator
        (``progress.closure_failures``: proof requirements, required
        artifacts, HARD blockers, dependency slots, resource readiness,
        then the current prerequisite checks over the registry), inside
        this same lock, against the contract re-derived for the run.
        Returns the evaluator's ``(code, detail)`` failures, in its order."""
        state = document["mission_state"].get(mission["mission_id"])
        if state is None:
            record.fail(record.PROBLEM_RUN,
                        "mission %s has no Mission State: its approved proof"
                        " contract was never activated" % mission["mission_id"])
        activation, contract = self._bound_contract(document, mission, state, now)
        return progress_module.closure_failures(
            contract, state, activation["activation_id"], now,
            store_module.registry_view(document))

    @staticmethod
    def _record_pending_proof(run, now, failures, raw_global_completeness,
                              supports_verification, result_evidence_id,
                              observed_task_status, mission):
        """The durable, NON-TERMINAL verification-blocked outcome: the
        stopped target's observation, the bound result evidence and every
        blocker code the evaluator listed. The lifecycle does not move."""
        previous = run["pending_proof"]
        attempts = 1 if previous is None else previous["attempts"] + 1
        if attempts > record.MAX_VERIFICATION_ATTEMPTS:
            record.fail(record.PROBLEM_RUN,
                        "mission %s already holds %d blocked verification"
                        " attempts; the hard bound is %d, so nothing more is"
                        " recorded (cancel the run to end it)"
                        % (mission["mission_id"], previous["attempts"],
                           record.MAX_VERIFICATION_ATTEMPTS))
        run["pending_proof"] = {
            "decided_at": now, "attempts": attempts,
            "target_task_id": run["receipt"]["task_id"],
            "observed_task_status": observed_task_status,
            "raw_global_completeness": raw_global_completeness,
            "supports_verification": supports_verification,
            "result_evidence_id": result_evidence_id,
            "blockers": [
                {"code": code, "detail": detail[:record.MAX_BLOCKER_DETAIL_CHARS]}
                for code, detail in failures[:record.MAX_PENDING_PROOF_BLOCKERS]],
        }
        mission["updated_at"] = now
        return {"verified": False, "state": mission["state"],
                "pending_proof": run["pending_proof"]}

    @staticmethod
    def _require_accepted_result(document, mission_id, evidence_id):
        state = document["mission_state"].get(mission_id)
        for evidence in (state["evidence"] if state else []):
            if evidence["evidence_id"] == evidence_id and evidence["kind"] == (
                record.EVIDENCE_KIND_VERIFICATION_RECORD
            ) and evidence["requirement_key"] == (
                record.RUN_RESULT_REQUIREMENT_KEY
            ) and evidence["acceptance"] is not None and (
                evidence["invalidation"] is None
            ):
                return evidence
        record.fail(record.PROBLEM_RUN,
                    "VERIFIED needs an accepted %s evidence under the approved"
                    " %r requirement of mission %s; %r is not one, and nothing"
                    " was changed" % (record.EVIDENCE_KIND_VERIFICATION_RECORD,
                                      record.RUN_RESULT_REQUIREMENT_KEY,
                                      mission_id, evidence_id))

    def record_pause(self, mission_id, authorization_id, context):
        """A durable PAUSE: DI initiates no further progression until an
        explicit resume. It suspends nothing outside DI."""
        def mutate(document, mission, now):
            if mission["state"] not in (record.STATE_AUTHORIZED,
                                        record.STATE_RUNNING):
                record.fail(record.PROBLEM_RUN,
                            "only an AUTHORIZED or RUNNING Mission pauses")
            self._require_bound_authorization(mission, authorization_id)
            self._refuse_paused(mission)
            run = self._run_of(mission, create=True)
            if len(run["pauses"]) >= record.MAX_RUN_PAUSES:
                record.fail(record.PROBLEM_RUN,
                            "mission %s already holds %d pauses; the hard"
                            " bound is %d" % (mission_id, len(run["pauses"]),
                                              record.MAX_RUN_PAUSES))
            run["pauses"].append({"paused_at": now, "resumed_at": None})
            mission["updated_at"] = now
            return run["pauses"][-1]
        return self._run_write(mission_id, context, mutate)

    def record_resume(self, mission_id, authorization_id, context):
        """The explicit durable resume that clears a pause."""
        def mutate(document, mission, now):
            self._require_bound_authorization(mission, authorization_id)
            run = self._run_of(mission)
            if not manifest.run_is_paused(run):
                record.fail(record.PROBLEM_RUN,
                            "mission %s is not paused" % mission_id)
            run["pauses"][-1]["resumed_at"] = now
            mission["updated_at"] = now
            return run["pauses"][-1]
        return self._run_write(mission_id, context, mutate)

    def record_cancel(self, mission_id, achieved, authorization_id, context):
        """The durable CANCEL, recorded first: the Mission becomes
        CANCELLED in this write, so the terminal guard refuses every later
        DI write from here on. ``achieved`` must match the durable facts.
        It stops nothing outside DI; ``complete_cancel`` records what
        control was achieved and what quiescence could be established."""
        def mutate(document, mission, now):
            record.require_member(achieved, record.CANCEL_ACHIEVED_STATES,
                                  "achieved")
            self._require_bound_authorization(mission, authorization_id)
            run = self._run_of(mission)
            intent = run["intent"] if run else None
            expected = {
                record.STATE_AUTHORIZED: (
                    (record.CANCEL_BEFORE_INTENT,) if intent is None
                    else (record.CANCEL_AFTER_INTENT_TARGET_UNKNOWN,)),
                record.STATE_RUNNING: (record.CANCEL_AFTER_OBSERVED_RUNNING,
                                       record.CANCEL_AFTER_TARGET_TERMINATED),
            }.get(mission["state"])
            if expected is None or achieved not in expected:
                record.fail(record.PROBLEM_RUN,
                            "%s does not match mission %s's durable facts"
                            " (state %s)" % (achieved, mission_id,
                                             mission["state"]))
            run = self._run_of(mission, create=True)
            run["cancel"] = {"requested_at": now, "achieved": achieved,
                             "control": None, "quiescence": None,
                             "completed_at": None}
            self._run_transition(mission, record.STATE_CANCELLED, achieved, now)
            return run["cancel"]
        return self._run_write(mission_id, context, mutate)

    def complete_cancel(self, mission_id, control, quiescence, authorization_id,
                        context):
        """Complete the cancel record, once: the one write a terminal
        (CANCELLED) Mission accepts."""
        def mutate(document, mission, now):
            self._require_bound_authorization(mission, authorization_id)
            run = self._run_of(mission)
            cancel = run["cancel"] if run else None
            if mission["state"] != record.STATE_CANCELLED or cancel is None:
                record.fail(record.PROBLEM_RUN,
                            "mission %s has no cancel to complete" % mission_id)
            if cancel["completed_at"] is not None:
                record.fail(record.PROBLEM_RUN_ALREADY_RECORDED,
                            "mission %s's cancel is already complete"
                            % mission_id)
            cancel.update({"control": control, "quiescence": quiescence,
                           "completed_at": now})
            mission["updated_at"] = now
            return cancel
        return self._run_write(mission_id, context, mutate, guard_terminal=False)

    @staticmethod
    def _recorded_decision(document, decision_id):
        for mission in document["missions"].values():
            for entry in mission["decisions"]:
                if entry["decision_id"] == decision_id:
                    return entry
        return None

    def _decision_outcome(self, document, decision_record, idempotent):
        """The projection of one decision. HISTORICAL fields (``revision``,
        ``state``, ``proposal_digest_sha256``, the approved scope, targets
        and expiry, ``invalidated_authorization_ids``) come from the
        RECORDED DECISION — the authority of record — and never change on
        replay. PRESENT fields (``current_revision``, ``current_state``,
        ``authorization_live``, ``authorization_problem``) come from the
        Mission record and from the ONE central validator; no mutable
        authorization field is read here. A replay therefore never reads
        as live authority unless the central check says so right now."""
        outcome = decision_record["outcome"]
        mission = document["missions"][decision_record["mission_id"]]
        authorization_id = outcome["authorization_id"]
        approved = decision_record["decision"] == decision_module.DECISION_APPROVE
        live = None
        problem = None
        digest = None
        if authorization_id is not None:
            check = authorization_module.validate_authorization_use(
                document, authorization_id, decision_record["mission_id"],
                decision_record["revision"], self._now(),
            )
            live = check.valid
            problem = check.problem
            digest = document["authorizations"][authorization_id][
                "authorization_digest_sha256"]
        return {
            "decision": decision_record["decision"],
            "decision_id": decision_record["decision_id"],
            "mission_id": decision_record["mission_id"],
            "revision": outcome["resulting_revision"],
            "state": outcome["resulting_state"],
            "proposal_digest_sha256": outcome["proposal_digest_sha256"],
            "authorization_id": authorization_id,
            "authorization_digest_sha256": digest,
            "authorized_action_scope": (
                list(decision_record["approved_action_scope"]) if approved
                else None
            ),
            "authorized_delivery_targets": (
                list(decision_record["approved_delivery_targets"]) if approved
                else None
            ),
            "expires_at": decision_record["expires_at"] if approved else None,
            "invalidated_authorization_ids": list(
                outcome["invalidated_authorization_ids"]
            ),
            "idempotent": idempotent,
            "current_revision": mission["current_revision"],
            "current_state": mission["state"],
            "authorization_live": live,
            "authorization_problem": problem,
        }

    def _approve(self, document, mission, envelope, now):
        record.validate_transition(mission["state"], record.STATE_AUTHORIZED)
        entry = manifest.current_revision_entry(mission)
        requested = entry["proposal"]
        actions, targets = envelope.normalized_scope()
        for action in actions:
            if action not in requested["requested_action_scope"]:
                record.fail(authorization_module.PROBLEM_ACTION_OUTSIDE_SCOPE,
                            "approved action %r was not requested by revision"
                            " %d" % (action, entry["revision"]))
        for target in targets:
            if target != requested["requested_delivery_target"]:
                record.fail(authorization_module.PROBLEM_TARGET_OUTSIDE_SCOPE,
                            "approved delivery target %r was not requested by"
                            " revision %d" % (target, entry["revision"]))
        if envelope.expires_at is not None and envelope.expires_at <= now:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "expires_at %d is not after the issue time %d"
                        % (envelope.expires_at, now))
        if len(document["authorizations"]) >= (
            store_module.MAX_AUTHORIZATION_RECORDS
        ):
            raise store_module.MissionStoreError(
                "the store holds %d authorizations; the hard bound is %d and"
                " records are never evicted"
                % (len(document["authorizations"]),
                   store_module.MAX_AUTHORIZATION_RECORDS),
                store_module.PROBLEM_STORE_FULL,
            )
        authorization_id = self._fresh_id(record.AUTHORIZATION_ID_PREFIX,
                                          document["authorizations"])
        principal = record.provenance_record(
            envelope.context, envelope.received_at,
            record.REFERENCE_KIND_DECISION, envelope.decision_id,
            mission["mission_id"], entry["revision"],
        )
        authorization = authorization_module.issue_mission_authorization(
            authorization_id, mission["mission_id"], entry["revision"],
            entry["proposal_digest_sha256"], principal, actions, targets, now,
            envelope.expires_at,
        )
        document["authorizations"][authorization_id] = authorization
        mission["authorization_ids"].append(authorization_id)
        mission["state"] = record.STATE_AUTHORIZED
        self._ledger(document, authorization_module.LEDGER_ISSUED, now,
                     mission["mission_id"], entry["revision"], authorization_id,
                     envelope.decision_id, _ISSUANCE_REASON)
        return {
            "resulting_state": record.STATE_AUTHORIZED,
            "resulting_revision": entry["revision"],
            "proposal_digest_sha256": entry["proposal_digest_sha256"],
            "authorization_id": authorization_id,
            "invalidated_authorization_ids": [],
        }

    def _deny(self, document, mission, envelope, now):
        record.validate_transition(mission["state"], record.STATE_DENIED)
        mission["state"] = record.STATE_DENIED
        self._ledger(document, authorization_module.LEDGER_DENIED, now,
                     mission["mission_id"], mission["current_revision"], None,
                     envelope.decision_id, _DENIAL_REASON)
        return {
            "resulting_state": record.STATE_DENIED,
            "resulting_revision": mission["current_revision"],
            "proposal_digest_sha256": manifest.current_revision_entry(
                mission
            )["proposal_digest_sha256"],
            "authorization_id": None,
            "invalidated_authorization_ids": [],
        }

    def _edit(self, document, mission, envelope, now):
        superseded = mission["current_revision"]
        invalidated = []
        if mission["state"] != record.STATE_AWAITING_DECISION:
            record.validate_transition(mission["state"],
                                       record.STATE_AWAITING_DECISION)
        for authorization_id in mission["authorization_ids"]:
            authorization = document["authorizations"][authorization_id]
            if authorization["revision"] != superseded or (
                authorization["revocation"]["revoked"]
            ):
                continue
            authorization_module.revoke(
                authorization, now,
                authorization_module.REVOCATION_REASON_SUPERSEDED_BY_EDIT,
            )
            invalidated.append(authorization_id)
            self._ledger(document, authorization_module.LEDGER_INVALIDATED_BY_EDIT,
                         now, mission["mission_id"], superseded,
                         authorization_id, envelope.decision_id,
                         authorization_module.REVOCATION_REASON_SUPERSEDED_BY_EDIT)
        entry = manifest.append_revision(mission, envelope.proposal, now,
                                         envelope.decision_id, envelope.context,
                                         envelope.received_at)
        mission["state"] = record.STATE_AWAITING_DECISION
        return {
            "resulting_state": record.STATE_AWAITING_DECISION,
            "resulting_revision": entry["revision"],
            "proposal_digest_sha256": entry["proposal_digest_sha256"],
            "authorization_id": None,
            "invalidated_authorization_ids": invalidated,
        }

    # -- validation (forwarding to the ONE path) ----------------------------

    def _load_for_validation(self):
        try:
            return self._store.load(), None
        except store_module.MissionStoreError as exc:
            return None, authorization_module.AuthorityCheck(
                False, authorization_module.PROBLEM_STORE_UNREADABLE, str(exc)
            )

    def validate_authorization(self, authorization_id, mission_id, revision,
                               required_actions=(), required_delivery_target=None,
                               expected_proposal_digest=None):
        document, failure = self._load_for_validation()
        if failure is not None:
            return failure
        return authorization_module.validate_authorization_use(
            document, authorization_id, mission_id, revision, self._now(),
            required_actions=required_actions,
            required_delivery_target=required_delivery_target,
            expected_proposal_digest=expected_proposal_digest,
        )

    def check_parent_authority(self, mission_id, authorization_digest_sha256,
                               delivery_target):
        """P1-A6: does the authorization with this digest permit
        ``delivery_target`` for ``mission_id`` at its bound revision?
        ``delivery_target`` must be a member of the closed delivery-target
        vocabulary; absent or unknown values refuse, they never disable
        the check."""
        if delivery_target not in record.DELIVERY_TARGETS:
            return authorization_module.AuthorityCheck(
                False, authorization_module.PROBLEM_TARGET_OUTSIDE_SCOPE,
                "delivery target %r is not in the closed vocabulary %r; the"
                " parent check requires an exact target"
                % (delivery_target, record.DELIVERY_TARGETS),
            )
        document, failure = self._load_for_validation()
        if failure is not None:
            return failure
        authorization = authorization_module.find_authorization_by_digest(
            document, authorization_digest_sha256
        )
        if authorization is None:
            return authorization_module.AuthorityCheck(
                False, authorization_module.PROBLEM_UNKNOWN_AUTHORIZATION,
                "no Mission Authorization carries the presented digest",
            )
        if authorization["mission_id"] != mission_id:
            return authorization_module.AuthorityCheck(
                False, authorization_module.PROBLEM_WRONG_MISSION,
                "the authorization with the presented digest binds mission %s,"
                " not %r" % (authorization["mission_id"], mission_id),
                authorization_id=authorization["authorization_id"],
                mission_id=authorization["mission_id"],
                revision=authorization["revision"],
                proposal_digest_sha256=authorization["proposal_digest_sha256"],
            )
        # Task 8 increment 2c: the narrowly scoped delivery-parent answer,
        # which also covers a RUNNING or COMPLETED run that consumed this
        # authorization; general use stays refused (see the function).
        return authorization_module.validate_delivery_parent_use(
            document, authorization["authorization_id"], mission_id,
            authorization["revision"], self._now(), delivery_target,
        )
