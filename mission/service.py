"""``MissionService``: the production entry points of the Mission Core.

Every mutating operation holds the store's cross-process lock around
its whole load-modify-save cycle and commits registry, ledger and
reservations in ONE atomic write. Every read validates the whole
document; an unreadable or malformed store fails closed everywhere.

Identity issuance and replay (amendment A-2'). ``mint_request_id`` and
``mint_decision_id`` mint a DI-owned id and durably RESERVE it with the
authenticated context that asked for it. ``propose`` and
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
        record.require_context(context)
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
                        "%s id %s is bound to a different authenticated"
                        " context" % (kind, reserved_id))
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

    def propose(self, request_id, proposal, context):
        record.require_context(context)
        record.require_id(request_id, record.REQUEST_ID_PREFIX, "request_id")
        clean = record.validate_proposal(proposal)
        digest = record.proposal_digest(clean)
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
                if bound != digest:
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
                mission_id, request_id, clean, now, context
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
        return authorization_module.validate_authorization_use(
            document, authorization["authorization_id"], mission_id,
            authorization["revision"], self._now(),
            required_delivery_target=delivery_target,
        )
