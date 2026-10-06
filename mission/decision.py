"""Transport-neutral human decisions: APPROVE, EDIT, DENY.

A ``HumanDecisionEnvelope`` is the only input from which the service
applies a decision. It carries either an ``AuthenticatedContext`` (see
``mission.record``) that the transport adapter produced from its OWN
authenticated state, or, for an APPROVE only (Task 8), an
``OperatorAttestedContext``: an Outer Operator's attestation, trusted by
declared policy and NOT independently verified. No field of either comes
from a tool payload. The
envelope binds the Mission id, the EXACT revision the human saw, a
DI-minted decision id, the decision, and for APPROVE the approved
action scope and delivery targets (each a subset of what was
requested), for EDIT the replacement proposal.

Idempotency. The decision digest covers the decision CONTENT only:
mission id, revision, decision, approved action scope, approved
delivery targets, the requested authorization expiry (``expires_at`` is
semantic approval scope: a different requested lifetime is a different
decision), and (for EDIT) the replacement proposal's content digest. It
excludes the transport and processing timestamps (``received_at``,
``decided_at``) and every DI-minted id, so a retry carrying a fresh
receive time still matches. The context (of either type) is not part of
the digest; it is checked separately as the principal binding of the
reserved decision id. A reserved decision id replayed with
an identical digest by the same principal returns the recorded outcome
and issues nothing; the same id with a different digest refuses; the
same id from a different principal refuses (see ``mission.service``).

Stated limit, repeated where it matters: for an authenticated kind the
provenance a decision carries records the transport credential that was
verified, never the human behind it; for the operator-attested kind it
records an attestation that was NOT independently verified. Nothing here
proves a human's identity.

Who may decide (Task 8). ``validate`` requires an authenticated principal
kind, or the operator-attested kind for an APPROVE only (with the exact
proposal digest and an expiry). A stored decision's provenance must match.
An ``unauthenticated_local_caller`` context can neither apply nor be
recorded as an APPROVE, EDIT or DENY (``mission_unauthenticated_principal``).
"""

from dataclasses import dataclass
from typing import Optional

from workflow_authority.digest import json_digest

from mission import record

DECISION_APPROVE = "APPROVE"
DECISION_EDIT = "EDIT"
DECISION_DENY = "DENY"
DECISIONS = (DECISION_APPROVE, DECISION_EDIT, DECISION_DENY)

DECISION_KEYS = (
    "decision_id", "mission_id", "revision", "decision",
    "approved_action_scope", "approved_delivery_targets", "expires_at",
    "proposal_digest_sha256", "provenance", "received_at", "decided_at",
    "decision_digest_sha256", "outcome",
)
# The outcome is the HISTORICAL result of applying the decision: the
# state and revision it produced, the manifest digest of the revision it
# bound (APPROVE/DENY) or produced (EDIT), the authorization it issued,
# and the authorizations it invalidated. It is never rewritten.
OUTCOME_KEYS = (
    "resulting_state", "resulting_revision", "proposal_digest_sha256",
    "authorization_id", "invalidated_authorization_ids",
)

PROBLEM_DECISION = "mission_decision"


@dataclass(frozen=True)
class HumanDecisionEnvelope:
    """One human decision as the transport adapter received it."""

    # An AuthenticatedContext, or (APPROVE only) an OperatorAttestedContext.
    context: object
    decision_id: str
    mission_id: str
    revision: int
    decision: str
    received_at: int
    approved_action_scope: Optional[list] = None
    approved_delivery_targets: Optional[list] = None
    proposal: Optional[dict] = None
    expires_at: Optional[int] = None
    # When set, the core refuses unless it equals the current revision's
    # proposal digest, checked under the store lock. Not part of the decision
    # digest (the revision already fixes the digest).
    expected_proposal_digest_sha256: Optional[str] = None

    def validate(self):
        # A decision needs an AuthenticatedContext of an authenticated kind,
        # or (APPROVE only) an OperatorAttestedContext; the unauthenticated
        # local caller kind is refused here, in the core.
        record.require_decision_context(
            self.context, self.decision == DECISION_APPROVE, "envelope.context")
        if self.expected_proposal_digest_sha256 is not None:
            record.require_hex(self.expected_proposal_digest_sha256,
                               "envelope.expected_proposal_digest_sha256", 64)
        if self.context.principal_kind in record.APPROVE_ONLY_PRINCIPAL_KINDS and (
            self.expected_proposal_digest_sha256 is None or self.expires_at is None
        ):
            record.fail(record.PROBLEM_ATTESTED_APPROVAL,
                        "an operator-attested APPROVE must bind the exact"
                        " proposal digest and an expiry")
        record.require_id(self.decision_id, record.DECISION_ID_PREFIX,
                          "envelope.decision_id")
        record.require_id(self.mission_id, record.MISSION_ID_PREFIX,
                          "envelope.mission_id")
        record.require_int(self.revision, "envelope.revision", minimum=1)
        record.require_member(self.decision, DECISIONS, "envelope.decision",
                              PROBLEM_DECISION)
        record.require_timestamp(self.received_at, "envelope.received_at")
        if self.decision == DECISION_APPROVE:
            record.require_sorted_subset(
                self.approved_action_scope, record.ACTION_SCOPES,
                "envelope.approved_action_scope", record.PROBLEM_ACTION_SCOPE,
                allow_empty=False,
            )
            record.require_sorted_subset(
                self.approved_delivery_targets, record.DELIVERY_TARGETS,
                "envelope.approved_delivery_targets",
                record.PROBLEM_DELIVERY_TARGET, allow_empty=True,
            )
            if self.proposal is not None:
                record.fail(PROBLEM_DECISION,
                            "envelope.proposal must be null for APPROVE")
            record.require_optional_timestamp(self.expires_at,
                                              "envelope.expires_at")
        else:
            for name in ("approved_action_scope", "approved_delivery_targets",
                         "expires_at"):
                if getattr(self, name) is not None:
                    record.fail(PROBLEM_DECISION,
                                "envelope.%s must be null for %s"
                                % (name, self.decision))
            if self.decision == DECISION_EDIT:
                record.validate_proposal(self.proposal, "envelope.proposal")
            elif self.proposal is not None:
                record.fail(PROBLEM_DECISION,
                            "envelope.proposal must be null for DENY")
        return self

    def normalized_scope(self):
        if self.decision != DECISION_APPROVE:
            return None, None
        return (sorted(self.approved_action_scope),
                sorted(self.approved_delivery_targets))

    def replacement_digest(self):
        if self.decision != DECISION_EDIT:
            return None
        return record.proposal_digest(self.proposal)

    def digest(self):
        actions, targets = self.normalized_scope()
        return decision_digest(
            self.mission_id, self.revision, self.decision, actions, targets,
            self.expires_at, self.replacement_digest(),
        )


def decision_digest(mission_id, revision, decision, approved_action_scope,
                    approved_delivery_targets, expires_at,
                    proposal_digest_sha256):
    """Content-only digest: approval content including the requested
    expiry; no transport/processing timestamp, no DI-minted id."""
    return json_digest({
        "mission_id": mission_id,
        "revision": revision,
        "decision": decision,
        "approved_action_scope": approved_action_scope,
        "approved_delivery_targets": approved_delivery_targets,
        "expires_at": expires_at,
        "proposal_digest_sha256": proposal_digest_sha256,
    })


def new_decision_record(envelope, decided_at, outcome):
    """The durable decision record for a validated, applied envelope."""
    envelope.validate()
    record.require_timestamp(decided_at, "decided_at")
    actions, targets = envelope.normalized_scope()
    provenance = record.provenance_record(
        envelope.context, envelope.received_at,
        record.REFERENCE_KIND_DECISION, envelope.decision_id,
        envelope.mission_id, envelope.revision,
    )
    document = {
        "decision_id": envelope.decision_id,
        "mission_id": envelope.mission_id,
        "revision": envelope.revision,
        "decision": envelope.decision,
        "approved_action_scope": actions,
        "approved_delivery_targets": targets,
        "expires_at": envelope.expires_at,
        "proposal_digest_sha256": envelope.replacement_digest(),
        "provenance": provenance,
        "received_at": envelope.received_at,
        "decided_at": decided_at,
        "decision_digest_sha256": envelope.digest(),
        "outcome": dict(outcome),
    }
    return validate_decision_record(document)


def validate_decision_record(value, location="decision"):
    record.require_dict(value, location)
    record.require_closed_keys(value, DECISION_KEYS, location)
    record.require_id(value["decision_id"], record.DECISION_ID_PREFIX,
                      location + ".decision_id")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["revision"], location + ".revision", minimum=1)
    record.require_member(value["decision"], DECISIONS,
                          location + ".decision", PROBLEM_DECISION)
    actions = value["approved_action_scope"]
    targets = value["approved_delivery_targets"]
    replacement = value["proposal_digest_sha256"]
    expires_at = value["expires_at"]
    if value["decision"] == DECISION_APPROVE:
        record.require_optional_timestamp(expires_at, location + ".expires_at")
        if record.require_sorted_subset(
            actions, record.ACTION_SCOPES, location + ".approved_action_scope",
            record.PROBLEM_ACTION_SCOPE, allow_empty=False,
        ) != actions:
            record.fail(record.PROBLEM_ACTION_SCOPE,
                        "%s.approved_action_scope must be sorted" % location)
        if record.require_sorted_subset(
            targets, record.DELIVERY_TARGETS,
            location + ".approved_delivery_targets",
            record.PROBLEM_DELIVERY_TARGET, allow_empty=True,
        ) != targets:
            record.fail(record.PROBLEM_DELIVERY_TARGET,
                        "%s.approved_delivery_targets must be sorted" % location)
        if replacement is not None:
            record.fail(PROBLEM_DECISION,
                        "%s.proposal_digest_sha256 must be null for APPROVE"
                        % location)
    else:
        if actions is not None or targets is not None or expires_at is not None:
            record.fail(PROBLEM_DECISION,
                        "%s carries approved scope for a %s decision"
                        % (location, value["decision"]))
        if value["decision"] == DECISION_EDIT:
            record.require_hex(replacement, location + ".proposal_digest_sha256",
                               64)
        elif replacement is not None:
            record.fail(PROBLEM_DECISION,
                        "%s.proposal_digest_sha256 must be null for DENY"
                        % location)
    provenance = record.require_decision_provenance(
        value["provenance"], value["decision"] == DECISION_APPROVE,
        location + ".provenance")
    if provenance["reference_kind"] != record.REFERENCE_KIND_DECISION:
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s.provenance must reference the decision" % location)
    if provenance["reference_id"] != value["decision_id"] or (
        provenance["mission_id"] != value["mission_id"]
        or provenance["revision"] != value["revision"]
    ):
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s.provenance bindings disagree with the decision"
                    % location)
    record.require_timestamp(value["received_at"], location + ".received_at")
    record.require_timestamp(value["decided_at"], location + ".decided_at")
    # ``received_at`` (transport receipt) is recorded twice, on the
    # decision and inside its provenance; the two copies must agree. It
    # is deliberately NOT required to equal ``decided_at`` (application).
    if provenance["received_at"] != value["received_at"]:
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s.received_at disagrees with %s.provenance.received_at"
                    % (location, location))
    expected = decision_digest(
        value["mission_id"], value["revision"], value["decision"], actions,
        targets, expires_at, replacement,
    )
    record.require_hex(value["decision_digest_sha256"],
                       location + ".decision_digest_sha256", 64)
    if value["decision_digest_sha256"] != expected:
        record.fail(PROBLEM_DECISION,
                    "%s.decision_digest_sha256 does not match its content"
                    % location)
    outcome = value["outcome"]
    record.require_dict(outcome, location + ".outcome")
    record.require_closed_keys(outcome, OUTCOME_KEYS, location + ".outcome")
    record.require_state(outcome["resulting_state"],
                         location + ".outcome.resulting_state")
    record.require_int(outcome["resulting_revision"],
                       location + ".outcome.resulting_revision", minimum=1)
    record.require_hex(outcome["proposal_digest_sha256"],
                       location + ".outcome.proposal_digest_sha256", 64)
    if outcome["authorization_id"] is not None:
        record.require_id(outcome["authorization_id"],
                          record.AUTHORIZATION_ID_PREFIX,
                          location + ".outcome.authorization_id")
    invalidated = outcome["invalidated_authorization_ids"]
    if not isinstance(invalidated, list):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s.outcome.invalidated_authorization_ids must be a list"
                    % location)
    for index, item in enumerate(invalidated):
        record.require_id(item, record.AUTHORIZATION_ID_PREFIX,
                          "%s.outcome.invalidated_authorization_ids[%d]"
                          % (location, index))
    return value
