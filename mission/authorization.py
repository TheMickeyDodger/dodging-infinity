"""Mission Authorization, the Authority Ledger, and the ONE centralized
fail-closed validation path.

Authorization record. Two halves, like the delivery authority. The
AUTHORITY half is immutable after issuance and bound by
``authorization_digest_sha256`` (canonical JSON over every authority
field, re-verified on every load and every validation): authorization
id, Mission id, the EXACT revision, the exact proposal (manifest) digest
of that revision, the truthful human-principal provenance block, the
authorized action scope and delivery targets (sorted, closed
vocabularies), issue time and optional expiry. The STATE half is the
``revocation`` block, mutable and protected by the ledger. Keeping
revocation out of the digest is deliberate: a delivery record that
stored the digest before a revocation must still RESOLVE the record so
the refusal it gets is "revoked", not "unknown".

Single issuance point. ``issue_mission_authorization`` is the only
constructor of an authorization record, and it has exactly one
production call site: ``MissionService.apply_human_decision``. That is
pinned by call-site count across every product file, and — because a
static pin never substitutes for behavioral proof — by tests that drive
each non-human path (model/operator turn, orchestration, worker,
capability, delivery) and assert the store and ledger are unchanged.

Authority Ledger. Append-only entries ``ISSUED``, ``REVOKED``,
``INVALIDATED_BY_EDIT``, ``DENIED``, ``EXPIRED``, each bound to Mission,
revision, authorization (when one exists), decision (when one exists),
and a reason. History is preserved, never overwritten. ``REVOKED`` is
declared for the ledger vocabulary; no path in this bundle writes it
(there is no cancel/revoke decision here; edit invalidation writes
``INVALIDATED_BY_EDIT``).

The one validation path. ``validate_authorization_use`` is the only
production function that decides whether an authorization permits a
use. It takes the loaded store document as plain data and fails closed
with a distinct ``mission_*`` code for: unknown Mission, malformed
Mission state, unknown authorization, tampered authorization digest,
wrong Mission, wrong (stale) revision, wrong manifest digest, expired,
revoked, denied Mission, action outside scope, delivery target outside
scope, and an inconsistent ledger. Store unreadability is reported by
the service wrapper with its own code before this function is reached.
No consumer reimplements a check; the P1-A6 parent seam forwards here.

Reconciliation is a REPLAY of the decision history, not a count and not
a digest check. ``reconcile_mission_history`` replays every recorded
decision of a Mission from revision 1 and requires that the stored
state, current revision, revision provenance, issued authorizations,
their revocation state, the id reservations and the ledger are exactly
what that history produces; each authorization must agree field for
field with its approving decision (scope, targets, expiry, provenance,
issue time, manifest digest), because the approving decision is the
authority of record and an unkeyed content digest recomputed after a
change would only agree with itself. The store runs the same function
on every load and save; the central validator runs it before judging
one authorization. Any disagreement refuses (``mission_history_inconsistent``,
``mission_ledger_inconsistent`` or ``mission_authorization_decision_mismatch``);
nothing is repaired and the more permissive side never wins.
"""

from dataclasses import dataclass
from typing import Optional

from workflow_authority.digest import json_digest

from mission import manifest
from mission import record

AUTHORIZATION_KEYS = (
    "authorization_id", "mission_id", "revision", "proposal_digest_sha256",
    "human_principal", "authorized_action_scope",
    "authorized_delivery_targets", "issued_at", "expires_at", "revocation",
    "authorization_digest_sha256",
)
AUTHORITY_DIGEST_EXCLUDED_KEYS = ("revocation", "authorization_digest_sha256")
REVOCATION_KEYS = ("revoked", "revoked_at", "reason")
REVOCATION_REASON_SUPERSEDED_BY_EDIT = "superseded_by_edit"
REVOCATION_REASONS = (REVOCATION_REASON_SUPERSEDED_BY_EDIT,)

LEDGER_ISSUED = "ISSUED"
LEDGER_REVOKED = "REVOKED"
LEDGER_INVALIDATED_BY_EDIT = "INVALIDATED_BY_EDIT"
LEDGER_DENIED = "DENIED"
LEDGER_EXPIRED = "EXPIRED"
LEDGER_KINDS = (
    LEDGER_ISSUED, LEDGER_REVOKED, LEDGER_INVALIDATED_BY_EDIT, LEDGER_DENIED,
    LEDGER_EXPIRED,
)
LEDGER_ENTRY_KEYS = (
    "entry_id", "kind", "recorded_at", "mission_id", "revision",
    "authorization_id", "decision_id", "reason",
)
MAX_LEDGER_REASON_CHARS = 256

# Validation-path problem codes: one distinct code per failure.
PROBLEM_UNKNOWN_MISSION = "mission_unknown_mission"
PROBLEM_MALFORMED_STATE = manifest.PROBLEM_MALFORMED_STATE
PROBLEM_UNKNOWN_AUTHORIZATION = "mission_unknown_authorization"
PROBLEM_AUTHORIZATION_TAMPERED = "mission_authorization_digest_mismatch"
PROBLEM_WRONG_MISSION = "mission_authorization_wrong_mission"
PROBLEM_WRONG_REVISION = "mission_authorization_wrong_revision"
PROBLEM_WRONG_MANIFEST_DIGEST = "mission_authorization_wrong_manifest_digest"
PROBLEM_EXPIRED = "mission_authorization_expired"
PROBLEM_REVOKED = "mission_authorization_revoked"
PROBLEM_DENIED = "mission_denied"
PROBLEM_ACTION_OUTSIDE_SCOPE = "mission_action_outside_scope"
PROBLEM_TARGET_OUTSIDE_SCOPE = "mission_delivery_target_outside_scope"
PROBLEM_LEDGER_INCONSISTENT = "mission_ledger_inconsistent"
PROBLEM_STORE_UNREADABLE = "mission_store_unreadable"
PROBLEM_NOT_AUTHORIZED = "mission_not_authorized"
PROBLEM_LEDGER = "mission_ledger_entry"


# -- authorization record ---------------------------------------------


def authorization_digest(value):
    """Digest over the immutable authority half."""
    return json_digest(dict(
        (key, value[key]) for key in AUTHORIZATION_KEYS
        if key not in AUTHORITY_DIGEST_EXCLUDED_KEYS
    ))


def issue_mission_authorization(authorization_id, mission_id, revision,
                                proposal_digest_sha256, human_principal,
                                authorized_action_scope,
                                authorized_delivery_targets, issued_at,
                                expires_at):
    """THE constructor of a Mission Authorization record.

    Its only production caller is ``MissionService.apply_human_decision``
    (pinned). Everything it binds is validated; the returned record is
    complete, digested, and not revoked.
    """
    document = {
        "authorization_id": authorization_id,
        "mission_id": mission_id,
        "revision": revision,
        "proposal_digest_sha256": proposal_digest_sha256,
        "human_principal": human_principal,
        "authorized_action_scope": authorized_action_scope,
        "authorized_delivery_targets": authorized_delivery_targets,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "revocation": {"revoked": False, "revoked_at": None, "reason": None},
        "authorization_digest_sha256": None,
    }
    document["authorization_digest_sha256"] = authorization_digest(document)
    return validate_authorization_record(document)


def validate_authorization_record(value, location="authorization"):
    record.require_dict(value, location)
    record.require_closed_keys(value, AUTHORIZATION_KEYS, location)
    record.require_id(value["authorization_id"], record.AUTHORIZATION_ID_PREFIX,
                      location + ".authorization_id")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_hex(value["proposal_digest_sha256"],
                       location + ".proposal_digest_sha256", 64)
    principal = record.validate_provenance(value["human_principal"],
                                           location + ".human_principal")
    if principal["reference_kind"] != record.REFERENCE_KIND_DECISION or (
        principal["mission_id"] != value["mission_id"]
        or principal["revision"] != value["revision"]
    ):
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s.human_principal must be the provenance of the approving"
                    " decision for this mission and revision" % location)
    actions = value["authorized_action_scope"]
    if record.require_sorted_subset(
        actions, record.ACTION_SCOPES, location + ".authorized_action_scope",
        record.PROBLEM_ACTION_SCOPE, allow_empty=False,
    ) != actions:
        record.fail(record.PROBLEM_ACTION_SCOPE,
                    "%s.authorized_action_scope must be sorted" % location)
    targets = value["authorized_delivery_targets"]
    if record.require_sorted_subset(
        targets, record.DELIVERY_TARGETS,
        location + ".authorized_delivery_targets",
        record.PROBLEM_DELIVERY_TARGET, allow_empty=True,
    ) != targets:
        record.fail(record.PROBLEM_DELIVERY_TARGET,
                    "%s.authorized_delivery_targets must be sorted" % location)
    issued = record.require_timestamp(value["issued_at"], location + ".issued_at")
    expires = record.require_optional_timestamp(value["expires_at"],
                                                location + ".expires_at")
    if expires is not None and expires <= issued:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.expires_at must be after issued_at" % location)
    revocation = value["revocation"]
    record.require_dict(revocation, location + ".revocation")
    record.require_closed_keys(revocation, REVOCATION_KEYS,
                               location + ".revocation")
    record.require_bool(revocation["revoked"], location + ".revocation.revoked")
    if revocation["revoked"]:
        record.require_timestamp(revocation["revoked_at"],
                                 location + ".revocation.revoked_at")
        record.require_member(revocation["reason"], REVOCATION_REASONS,
                              location + ".revocation.reason")
    elif revocation["revoked_at"] is not None or revocation["reason"] is not None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.revocation carries revocation data while not revoked"
                    % location)
    record.require_hex(value["authorization_digest_sha256"],
                       location + ".authorization_digest_sha256", 64)
    if value["authorization_digest_sha256"] != authorization_digest(value):
        record.fail(PROBLEM_AUTHORIZATION_TAMPERED,
                    "%s.authorization_digest_sha256 does not match its"
                    " authority content" % location)
    return value


def revoke(authorization, revoked_at, reason):
    """Mark the STATE half revoked in place; the authority half and its
    digest are untouched, so the record still resolves by digest."""
    record.require_timestamp(revoked_at, "revoked_at")
    record.require_member(reason, REVOCATION_REASONS, "reason")
    authorization["revocation"] = {
        "revoked": True, "revoked_at": revoked_at, "reason": reason,
    }
    return authorization


# -- ledger ----------------------------------------------------------


def new_ledger_entry(entry_id, kind, recorded_at, mission_id, revision,
                     authorization_id, decision_id, reason):
    entry = {
        "entry_id": entry_id,
        "kind": kind,
        "recorded_at": recorded_at,
        "mission_id": mission_id,
        "revision": revision,
        "authorization_id": authorization_id,
        "decision_id": decision_id,
        "reason": reason,
    }
    return validate_ledger_entry(entry)


def validate_ledger_entry(value, location="ledger entry"):
    record.require_dict(value, location)
    record.require_closed_keys(value, LEDGER_ENTRY_KEYS, location)
    record.require_id(value["entry_id"], record.LEDGER_ENTRY_ID_PREFIX,
                      location + ".entry_id")
    record.require_member(value["kind"], LEDGER_KINDS, location + ".kind",
                          PROBLEM_LEDGER)
    record.require_timestamp(value["recorded_at"], location + ".recorded_at")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    if value["kind"] == LEDGER_DENIED:
        if value["authorization_id"] is not None:
            record.fail(PROBLEM_LEDGER,
                        "%s: a DENIED entry names no authorization" % location)
    else:
        record.require_id(value["authorization_id"],
                          record.AUTHORIZATION_ID_PREFIX,
                          location + ".authorization_id")
    if value["decision_id"] is not None:
        record.require_id(value["decision_id"], record.DECISION_ID_PREFIX,
                          location + ".decision_id")
    elif value["kind"] in (LEDGER_ISSUED, LEDGER_DENIED,
                           LEDGER_INVALIDATED_BY_EDIT):
        record.fail(PROBLEM_LEDGER,
                    "%s: a %s entry must name its decision"
                    % (location, value["kind"]))
    record.require_str(value["reason"], location + ".reason",
                       MAX_LEDGER_REASON_CHARS)
    return value


# -- the one validation path -------------------------------------------


@dataclass(frozen=True)
class AuthorityCheck:
    """The outcome of the one validation path: ``valid`` with the exact
    bindings projected for review, or a distinct ``problem`` and detail.
    The projection is present whenever the authorization record was
    resolved, so a refusal can still be reviewed against what it named."""

    valid: bool
    problem: Optional[str]
    detail: Optional[str]
    authorization_id: Optional[str] = None
    mission_id: Optional[str] = None
    revision: Optional[int] = None
    proposal_digest_sha256: Optional[str] = None
    authorized_action_scope: Optional[list] = None
    authorized_delivery_targets: Optional[list] = None

    def as_dict(self):
        return {
            "valid": self.valid,
            "problem": self.problem,
            "detail": self.detail,
            "authorization_id": self.authorization_id,
            "mission_id": self.mission_id,
            "revision": self.revision,
            "proposal_digest_sha256": self.proposal_digest_sha256,
            "authorized_action_scope": (
                None if self.authorized_action_scope is None
                else list(self.authorized_action_scope)
            ),
            "authorized_delivery_targets": (
                None if self.authorized_delivery_targets is None
                else list(self.authorized_delivery_targets)
            ),
        }


def _refusal(problem, detail, authorization=None):
    if authorization is None:
        return AuthorityCheck(False, problem, detail)
    return AuthorityCheck(
        False, problem, detail,
        authorization_id=authorization["authorization_id"],
        mission_id=authorization["mission_id"],
        revision=authorization["revision"],
        proposal_digest_sha256=authorization["proposal_digest_sha256"],
        authorized_action_scope=list(authorization["authorized_action_scope"]),
        authorized_delivery_targets=list(
            authorization["authorized_delivery_targets"]
        ),
    )


def find_authorization_by_digest(document, digest):
    """The authorization record whose authority digest is ``digest``, or
    None. Exact string match on a 64-hex digest; anything else is None."""
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    for authorization in document.get("authorizations", {}).values():
        if isinstance(authorization, dict) and (
            authorization.get("authorization_digest_sha256") == digest
        ):
            return authorization
    return None


PROBLEM_HISTORY_INCONSISTENT = "mission_history_inconsistent"
PROBLEM_DECISION_MISMATCH = "mission_authorization_decision_mismatch"

_HISTORY_LEDGER_KINDS = (
    LEDGER_ISSUED, LEDGER_DENIED, LEDGER_INVALIDATED_BY_EDIT, LEDGER_EXPIRED,
)


def _history(detail):
    return (PROBLEM_HISTORY_INCONSISTENT, detail)


def _ledger(detail):
    return (PROBLEM_LEDGER_INCONSISTENT, detail)


def _mismatch(detail):
    return (PROBLEM_DECISION_MISMATCH, detail)


def _reservation_agrees(document, reserved_id, kind, consumed_by, provenance):
    reservation = document.get("reservations", {}).get(reserved_id)
    if not isinstance(reservation, dict) or reservation.get("kind") != kind:
        return "%s id %s has no %s reservation" % (kind, reserved_id, kind)
    if reservation.get("consumed_by") != consumed_by:
        return "%s reservation %s is not consumed by %s" % (
            kind, reserved_id, consumed_by)
    context = dict((key, provenance.get(key)) for key in record.CONTEXT_KEYS)
    if reservation.get("context") != context:
        return ("%s reservation %s was reserved by a different authenticated"
                " context than the one recorded in the provenance"
                % (kind, reserved_id))
    return None


def reconcile_registry(document):
    """Registry-wide identity: every decision id and every request id
    names exactly one recorded decision / Mission across ALL Missions, and
    reservation consumption holds in BOTH directions (a consumed
    reservation names a decision or Mission that exists and points back
    at it; every recorded decision and Mission has its consumed
    reservation — the latter half is checked per Mission by
    ``reconcile_mission_history``). Per-Mission replay cannot see a
    decision id reused across two Missions; this can. Returns
    ``(problem, detail)`` or None.
    """
    missions = document.get("missions", {})
    decisions = {}
    requests = {}
    for mission_id, mission in missions.items():
        if mission["request_id"] in requests:
            return _history("request id %s created both mission %s and"
                            " mission %s" % (mission["request_id"],
                                             requests[mission["request_id"]],
                                             mission_id))
        requests[mission["request_id"]] = mission_id
        for decision in mission["decisions"]:
            if decision["decision_id"] in decisions:
                return _history("decision id %s is recorded in both mission %s"
                                " and mission %s"
                                % (decision["decision_id"],
                                   decisions[decision["decision_id"]],
                                   mission_id))
            decisions[decision["decision_id"]] = mission_id
    for reserved_id, reservation in document.get("reservations", {}).items():
        consumed_by = reservation.get("consumed_by")
        if consumed_by is None:
            continue
        if reservation.get("kind") == "request":
            if requests.get(reserved_id) != consumed_by:
                return _history("request reservation %s is marked consumed by"
                                " %s but no mission with that id was created"
                                " from it" % (reserved_id, consumed_by))
        elif consumed_by != reserved_id or reserved_id not in decisions:
            return _history("decision reservation %s is marked consumed but no"
                            " mission records a decision with that id"
                            % reserved_id)
    return None


def reconcile_mission_history(document, mission):
    """Replay one Mission's recorded decisions and require that the stored
    state, revisions, authorizations, reservations and ledger are exactly
    what that history produces. The approving decision is the authority
    of record: an authorization must agree with it field for field.

    The decision history is the authoritative account; everything else
    (the state string, the revocation booleans, the ledger entries, the
    authorization content) is derived from it and must agree. Any
    disagreement is returned as ``(problem, detail)`` and the caller
    refuses; nothing is repaired and the more permissive side never wins.
    Recomputing an unkeyed content digest after a change agrees with
    itself, so digests are not what this function trusts — the preserved
    decision record is.

    Timestamps: a decision's ``received_at`` is the transport receipt
    time and its ``decided_at`` the application time; they are never
    required to be equal. The revision an EDIT produces carries the
    receipt time in its provenance and the application time as
    ``created_at``; an authorization carries the receipt time inside its
    provenance and the application time as ``issued_at``; ledger entries
    are recorded at the application time.

    EXPIRED follows the service's recording policy exactly: at each
    successful decision, every unrevoked authorization of the Mission
    whose expiry has passed by that decision's time and is not yet
    recorded is recorded EXPIRED at that time, immediately before the
    decision's own entries. Before the next mutation the event is
    legitimately absent; once a mutation has occurred, a missing,
    misplaced, retimed, duplicated, or unproduced EXPIRED entry refuses.
    Returns None when everything agrees.
    """
    mission_id = mission["mission_id"]
    authorizations = document.get("authorizations", {})
    revisions = mission["revisions"]
    problem = _reservation_agrees(
        document, mission["request_id"], "request", mission_id,
        revisions[0]["provenance"],
    )
    if problem is not None:
        return _history("mission %s: %s" % (mission_id, problem))
    state = record.STATE_AWAITING_DECISION
    revision = 1
    issued_ids = []
    live = {}          # authorization_id -> revision, unrevoked so far
    revoked_by = {}    # authorization_id -> the EDIT decision that revoked it
    expired = set()    # authorization ids already recorded EXPIRED
    expected_ledger = []
    for index, decision in enumerate(mission["decisions"]):
        where = "mission %s decision[%d] %s" % (mission_id, index,
                                              decision["decision_id"])
        problem = _reservation_agrees(
            document, decision["decision_id"], "decision",
            decision["decision_id"], decision["provenance"],
        )
        if problem is not None:
            return _history("%s: %s" % (where, problem))
        if decision["revision"] != revision:
            return _history("%s names revision %d but the mission was at"
                            " revision %d when it was applied"
                            % (where, decision["revision"], revision))
        outcome = decision["outcome"]
        kind = decision["decision"]
        for authorization_id in sorted(live):
            authorization = authorizations[authorization_id]
            expires = authorization["expires_at"]
            if expires is None or decision["decided_at"] < expires or (
                authorization_id in expired
            ):
                continue
            expired.add(authorization_id)
            expected_ledger.append((LEDGER_EXPIRED, authorization_id, None,
                                    authorization["revision"],
                                    decision["decided_at"]))
        if kind == "APPROVE":
            if state != record.STATE_AWAITING_DECISION:
                return _history("%s approves while the mission was %s"
                                % (where, state))
            authorization_id = outcome["authorization_id"]
            authorization = authorizations.get(authorization_id)
            if authorization_id is None or authorization is None:
                return _history("%s issued authorization %r, which the store"
                                " does not hold" % (where, authorization_id))
            if authorization_id in issued_ids:
                return _history("%s re-issues authorization %s"
                                % (where, authorization_id))
            requested = revisions[revision - 1]["proposal"]
            if not set(decision["approved_action_scope"]) <= set(
                requested["requested_action_scope"]
            ) or not set(decision["approved_delivery_targets"]) <= (
                set() if requested["requested_delivery_target"] is None
                else {requested["requested_delivery_target"]}
            ):
                return _history("%s approves scope that revision %d never"
                                " requested" % (where, revision))
            problem = _authorization_agrees(authorization, decision,
                                            revisions[revision - 1], mission_id)
            if problem is not None:
                return _mismatch("authorization %s disagrees with its approving"
                                 " decision %s: %s"
                                 % (authorization_id, decision["decision_id"],
                                    problem))
            expected_ledger.append((LEDGER_ISSUED, authorization_id,
                                    decision["decision_id"], revision,
                                    decision["decided_at"]))
            issued_ids.append(authorization_id)
            live[authorization_id] = revision
            state = record.STATE_AUTHORIZED
            expected_outcome = (state, revision, [])
        elif kind == "DENY":
            if state != record.STATE_AWAITING_DECISION:
                return _history("%s denies while the mission was %s"
                                % (where, state))
            if outcome["authorization_id"] is not None:
                return _history("%s is a denial that names an authorization"
                                % where)
            expected_ledger.append((LEDGER_DENIED, None,
                                    decision["decision_id"], revision,
                                    decision["decided_at"]))
            state = record.STATE_DENIED
            expected_outcome = (state, revision, [])
        elif kind == "EDIT":
            superseded = revision
            invalidated = sorted(
                a for a, r in live.items() if r == superseded
            )
            for authorization_id in invalidated:
                del live[authorization_id]
                revoked_by[authorization_id] = decision
                expected_ledger.append((LEDGER_INVALIDATED_BY_EDIT,
                                        authorization_id,
                                        decision["decision_id"], superseded,
                                        decision["decided_at"]))
            revision += 1
            if revision > len(revisions):
                return _history("%s produced revision %d, which the mission"
                                " does not hold" % (where, revision))
            entry = revisions[revision - 1]
            if entry["provenance"]["reference_id"] != decision["decision_id"]:
                return _history("revision %d of mission %s was not produced by"
                                " decision %s" % (revision, mission_id,
                                                  decision["decision_id"]))
            if entry["provenance"] != record.provenance_record(
                record.provenance_context(decision["provenance"]),
                decision["received_at"], record.REFERENCE_KIND_DECISION,
                decision["decision_id"], mission_id, revision,
            ):
                return _history("revision %d of mission %s carries provenance"
                                " that disagrees with decision %s"
                                % (revision, mission_id, decision["decision_id"]))
            if entry["proposal_digest_sha256"] != decision["proposal_digest_sha256"]:
                return _history("revision %d of mission %s does not carry the"
                                " proposal decision %s recorded"
                                % (revision, mission_id, decision["decision_id"]))
            if entry["created_at"] != decision["decided_at"]:
                return _history("revision %d of mission %s was not created when"
                                " decision %s was applied"
                                % (revision, mission_id, decision["decision_id"]))
            if outcome["authorization_id"] is not None:
                return _history("%s is an edit that names an authorization"
                                % where)
            state = record.STATE_AWAITING_DECISION
            expected_outcome = (state, revision, invalidated)
        else:
            return _history("%s has unknown decision %r" % (where, kind))
        if (outcome["resulting_state"], outcome["resulting_revision"],
                sorted(outcome["invalidated_authorization_ids"])) != (
                    expected_outcome
        ):
            return _history("%s recorded outcome %r; replaying the history"
                            " gives %r" % (where, (
                                outcome["resulting_state"],
                                outcome["resulting_revision"],
                                sorted(outcome["invalidated_authorization_ids"]),
                            ), expected_outcome))
        if outcome["proposal_digest_sha256"] != revisions[
            expected_outcome[1] - 1
        ]["proposal_digest_sha256"]:
            return _history("%s recorded a manifest digest revision %d does not"
                            " carry" % (where, expected_outcome[1]))
    if state != mission["state"]:
        return _history("mission %s is stored as %s but its decision history"
                        " leaves it %s" % (mission_id, mission["state"], state))
    if revision != mission["current_revision"] or revision != len(revisions):
        return _history("mission %s is stored at revision %d with %d revisions"
                        " but its decision history produced revision %d"
                        % (mission_id, mission["current_revision"],
                           len(revisions), revision))
    if list(mission["authorization_ids"]) != issued_ids:
        return _history("mission %s references authorizations %r but its"
                        " history issued %r"
                        % (mission_id, list(mission["authorization_ids"]),
                           issued_ids))
    for authorization_id in issued_ids:
        authorization = authorizations[authorization_id]
        revocation = authorization["revocation"]
        editor = revoked_by.get(authorization_id)
        if editor is None:
            if revocation["revoked"]:
                return _ledger("authorization %s is marked revoked but no"
                               " decision in mission %s history revoked it"
                               % (authorization_id, mission_id))
        elif revocation != {
            "revoked": True, "revoked_at": editor["decided_at"],
            "reason": REVOCATION_REASON_SUPERSEDED_BY_EDIT,
        }:
            return _ledger("authorization %s was superseded by decision %s but"
                           " its revocation state does not record that"
                           % (authorization_id, editor["decision_id"]))
    ledger = document.get("authority_ledger", [])
    actual = []
    for e in ledger:
        if not isinstance(e, dict) or e.get("mission_id") != mission_id:
            continue
        if e.get("kind") not in _HISTORY_LEDGER_KINDS:
            # LEDGER_REVOKED is declared vocabulary; no decision in this
            # bundle produces it, so an occurrence has no history behind
            # it and cannot be reconciled.
            return _ledger("the ledger holds a %s entry for mission %s that no"
                           " recorded decision produced"
                           % (e.get("kind"), mission_id))
        actual.append((e["kind"], e["authorization_id"], e["decision_id"],
                       e["revision"], e["recorded_at"]))
    if actual != expected_ledger:
        return _ledger("mission %s ledger history %r disagrees with its"
                       " decision history %r" % (mission_id, actual,
                                                 expected_ledger))
    return None


def _authorization_agrees(authorization, decision, entry, mission_id):
    """Field-for-field agreement of an authorization with the approving
    decision (the authority of record) and the revision it approved."""
    if authorization["mission_id"] != mission_id:
        return "it binds mission %s" % authorization["mission_id"]
    if authorization["revision"] != decision["revision"]:
        return "it binds revision %d, the decision approved %d" % (
            authorization["revision"], decision["revision"])
    if authorization["proposal_digest_sha256"] != entry["proposal_digest_sha256"]:
        return "it binds a manifest digest revision %d does not carry" % (
            decision["revision"])
    if authorization["authorized_action_scope"] != decision["approved_action_scope"]:
        return "authorized_action_scope %r is not the approved %r" % (
            authorization["authorized_action_scope"],
            decision["approved_action_scope"])
    if authorization["authorized_delivery_targets"] != (
        decision["approved_delivery_targets"]
    ):
        return "authorized_delivery_targets %r is not the approved %r" % (
            authorization["authorized_delivery_targets"],
            decision["approved_delivery_targets"])
    if authorization["expires_at"] != decision["expires_at"]:
        return "expires_at %r is not the approved %r" % (
            authorization["expires_at"], decision["expires_at"])
    if authorization["human_principal"] != decision["provenance"]:
        return "human_principal is not the decision's provenance"
    if authorization["issued_at"] != decision["decided_at"]:
        return "issued_at %r is not the decision time %r" % (
            authorization["issued_at"], decision["decided_at"])
    return None


def validate_authorization_use(document, authorization_id, mission_id,
                               revision, now, required_actions=(),
                               required_delivery_target=None,
                               expected_proposal_digest=None):
    """Does authorization ``authorization_id`` permit the named use of
    Mission ``mission_id`` at ``revision`` right ``now``? Fails closed.

    ``document`` is the loaded store document (plain data; the caller
    has already handled unreadability). ``required_actions`` is the
    action scope the use needs (every entry must be authorized);
    ``required_delivery_target`` the delivery target it needs, if any;
    ``expected_proposal_digest`` the manifest digest the caller holds,
    if any (the stored revision's digest is compared regardless).
    """
    if not isinstance(document, dict) or not isinstance(
        document.get("missions"), dict
    ) or not isinstance(document.get("authorizations"), dict) or (
        not isinstance(document.get("authority_ledger"), list)
    ):
        return _refusal(PROBLEM_STORE_UNREADABLE,
                        "store document is not a Mission store document")
    if record.id_problem(mission_id, record.MISSION_ID_PREFIX) is not None:
        return _refusal(PROBLEM_UNKNOWN_MISSION,
                        "mission id is not well formed")
    mission = document["missions"].get(mission_id)
    if mission is None:
        return _refusal(PROBLEM_UNKNOWN_MISSION,
                        "mission %s is not in the registry" % mission_id)
    try:
        manifest.validate_mission_record(mission, "mission %s" % mission_id)
    except record.MissionError as exc:
        return _refusal(PROBLEM_MALFORMED_STATE, str(exc))
    if record.id_problem(authorization_id, record.AUTHORIZATION_ID_PREFIX):
        return _refusal(PROBLEM_UNKNOWN_AUTHORIZATION,
                        "authorization id is not well formed")
    authorization = document["authorizations"].get(authorization_id)
    if authorization is None:
        return _refusal(PROBLEM_UNKNOWN_AUTHORIZATION,
                        "authorization %s is not in the ledger store"
                        % authorization_id)
    try:
        validate_authorization_record(authorization,
                                      "authorization %s" % authorization_id)
    except record.MissionError as exc:
        if exc.problem == PROBLEM_AUTHORIZATION_TAMPERED:
            return _refusal(PROBLEM_AUTHORIZATION_TAMPERED, str(exc))
        return _refusal(PROBLEM_MALFORMED_STATE, str(exc))
    if authorization["mission_id"] != mission_id:
        return _refusal(PROBLEM_WRONG_MISSION,
                        "authorization %s binds mission %s, not %s"
                        % (authorization_id, authorization["mission_id"],
                           mission_id), authorization)
    if authorization_id not in mission["authorization_ids"]:
        return _refusal(PROBLEM_LEDGER_INCONSISTENT,
                        "mission %s does not reference authorization %s"
                        % (mission_id, authorization_id), authorization)
    inconsistency = reconcile_registry(document)
    if inconsistency is None:
        inconsistency = reconcile_mission_history(document, mission)
    if inconsistency is not None:
        return _refusal(inconsistency[0], inconsistency[1], authorization)
    if authorization["revocation"]["revoked"]:
        return _refusal(PROBLEM_REVOKED,
                        "authorization %s was revoked (%s)"
                        % (authorization_id, authorization["revocation"]["reason"]),
                        authorization)
    if not isinstance(revision, int) or isinstance(revision, bool) or (
        authorization["revision"] != revision
    ):
        return _refusal(PROBLEM_WRONG_REVISION,
                        "authorization %s binds revision %d, not %r"
                        % (authorization_id, authorization["revision"], revision),
                        authorization)
    if mission["current_revision"] != revision:
        return _refusal(PROBLEM_WRONG_REVISION,
                        "revision %d is stale: mission %s is at revision %d"
                        % (revision, mission_id, mission["current_revision"]),
                        authorization)
    entry = manifest.revision_entry(mission, revision)
    if entry is None or entry["proposal_digest_sha256"] != (
        authorization["proposal_digest_sha256"]
    ):
        return _refusal(PROBLEM_WRONG_MANIFEST_DIGEST,
                        "authorization %s does not bind the stored manifest"
                        " digest of revision %d" % (authorization_id, revision),
                        authorization)
    if expected_proposal_digest is not None and (
        expected_proposal_digest != authorization["proposal_digest_sha256"]
    ):
        return _refusal(PROBLEM_WRONG_MANIFEST_DIGEST,
                        "the presented manifest digest does not match the"
                        " authorized manifest digest", authorization)
    if mission["state"] == record.STATE_DENIED:
        return _refusal(PROBLEM_DENIED, "mission %s is denied" % mission_id,
                        authorization)
    if mission["state"] != record.STATE_AUTHORIZED:
        return _refusal(PROBLEM_NOT_AUTHORIZED,
                        "mission %s is %s, not AUTHORIZED"
                        % (mission_id, mission["state"]), authorization)
    if isinstance(now, bool) or not isinstance(now, int):
        return _refusal(record.PROBLEM_BAD_TYPE, "now must be an integer",
                        authorization)
    expires = authorization["expires_at"]
    if expires is not None and now >= expires:
        return _refusal(PROBLEM_EXPIRED,
                        "authorization %s expired at %d (now %d)"
                        % (authorization_id, expires, now), authorization)
    if not isinstance(required_actions, (list, tuple)):
        return _refusal(PROBLEM_ACTION_OUTSIDE_SCOPE,
                        "required actions must be a list", authorization)
    for action in required_actions:
        if action not in authorization["authorized_action_scope"]:
            return _refusal(PROBLEM_ACTION_OUTSIDE_SCOPE,
                            "action %r is outside the authorized scope"
                            % (action,), authorization)
    if required_delivery_target is not None and (
        required_delivery_target
        not in authorization["authorized_delivery_targets"]
    ):
        return _refusal(PROBLEM_TARGET_OUTSIDE_SCOPE,
                        "delivery target %r is outside the authorized targets"
                        % (required_delivery_target,), authorization)
    return AuthorityCheck(
        True, None, None,
        authorization_id=authorization_id,
        mission_id=mission_id,
        revision=revision,
        proposal_digest_sha256=authorization["proposal_digest_sha256"],
        authorized_action_scope=list(authorization["authorized_action_scope"]),
        authorized_delivery_targets=list(
            authorization["authorized_delivery_targets"]
        ),
    )
