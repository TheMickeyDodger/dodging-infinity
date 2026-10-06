"""Mission-bound P1-A6 delivery (Task 8, slice S-VI; ledger R2-7, R2-8,
R2-9, the delivery half of R2-12).

The canonical owners do not move: P1-A6 (``pr_delivery``) owns the
candidate identity, the authorization record, receipts, effects,
reconciliation and revocation; the Mission Core owns the Mission parent
authority, evidence acceptance, receipt attestation and closure; the
Runtime owns the leased workspace and every process it starts (the
verification producer, ``target_runtime.verification``); the Grok surface
owns presentation and the client-mediated decision round trip. This
module COMPOSES them for one Mission-origin workflow, in one fixed order,
and adds no second delivery store and no new Git effect implementation.

THE ORDER (``MissionDelivery.advance``, one Runtime pass under the
workflow lock the Broker already holds):

1. APPLICABILITY — the Mission's current revision names ``github_pr``, the
   workflow is COMPLETED with its lease and retention held, the review
   standing is PROVEN APPROVE (``observation_receipts``) and the latest
   candidate observation is exact against the approved baseline.
2. VERIFICATION — the Runtime's producer has run the Mission's APPROVED
   verification argv for exactly this candidate, baseline and revision
   (the Broker runs it before this module is called; this module only
   reads the content-addressed record, re-hashed with its complete log).
   Missing, unconfigured, unsettled, stale or nonzero-exit refuses. A
   workflow's COMPLETED phase, the engineering result or any worker or
   model statement never stands in for this real, separately executed run
   — and the run is machine-observed, never presented as verification by
   an independent party (``VERIFICATION_PROVENANCE``).
3. EVIDENCE — the Runtime's local context submits AND accepts the three
   integration obligations it observed itself (``engineering_verified``:
   the verification record; ``reviewer_approve``: the proven round's
   content digest; ``candidate_identity``: the P1-A6 identity), each
   bound to a recorded Mission artifact under the stable reconciliation
   key. An accepted record with ANOTHER digest refuses (contradiction is
   never overwritten; an EDIT starts a fresh activation).
4. PROPOSAL — ``build_proposal`` is PURE: from the live, read-only P1-A6
   bindings (``pr_delivery.cli.live_bindings``) it computes one proposal
   binding everything the authorization will bind (the authority
   template: candidate entries and identity, source and base refs,
   repository and git-dir realpaths, configured and expanded remote URLs,
   original baseline, the task/review/verification evidence, the
   allowed actions — BASE_REFRESH, COMMIT, PUSH, PR_CREATE and nothing
   else — the committer, the PR content and the reverification argv),
   the Mission parent, the workflow and task identity and an ABSOLUTE
   ``expires_at``. ``prepare_proposal`` is the separate, explicit
   PREPARATION WRITE: the local source branch a detached lease needs
   (``_prepare_source_branch``; see the EFFECT INVENTORY), the canonical
   document (content-addressed), the Mission artifact
   ``delivery_proposal`` and the workflow receipt — idempotent: a current
   proposal is adopted, never re-prepared. Nothing here is a status read.
5. DECISION — the human's, through the Grok desk below (card = the FULL
   proposal, confirm value = the candidate identity prefix): an accept
   is submitted and accepted as ``delivery_decision`` evidence under the
   CLIENT-CONFIRMATION context before anything is minted; a decline is
   the S-V cancel request (sticky: every gate refuses from then on, the
   owner stops what was started, the Runtime releases the candidate's
   retention with S-V's ``declined`` reason — ``declined_delivery`` — and
   confirming the cancel stays a control principal's act, as in S-V).
6. MINTING — the ONE accepted, client-provenanced decision bound to the
   current proposal is BOUND first (a content-addressed binding document
   and its workflow receipt: decision, evidence, proposal and the
   DETERMINISTIC delivery id), and only then minted through
   ``pr_delivery.cli.authorize_client_confirmed`` (idempotent on that id;
   client-confirmed records are never pruned). A crash anywhere repeats
   the same id: at most one record per decision.
7. DRIVE — ``DeliveryMachine.advance_once`` (bounded per pass) with THIS
   module's effect gate, consulted by the machine before EVERY effect and
   after blocking work: the Mission parent (``parent_mission_authority``),
   the S-IV/S-V gate at ``BOUNDARY_DELIVERY_EFFECT`` (revision, live
   authority, provenance, not terminal, no cancel, no hold, readiness),
   the retention, and the bound decision still accepted. A reversible
   refusal holds the step; a terminal one stops it and the record is
   REVOKED (a durable stop, never a silent skip).
8. ATTESTATION — every stored receipt, through
   ``mission_parent.attest_validated_receipt`` (the delivery layer's own
   validator first), once per receipt state; what cannot be attested
   (authority gone, Mission closed) stays unattested and is reported so.
9. COMPLETION — with PR_CREATE succeeded and attested: ``delivery_recorded``
   evidence (the attested receipt), the retention released
   (``pr_created``), then ``complete_successfully`` when the Mission Core
   finds closure eligible. Merge, tag, release, deploy and publish exist
   nowhere here.

EFFECT INVENTORY — every mutation this composition causes outside the
Mission and workflow stores, its owner, its admission and its record:

- Runtime (owned processes, ``target_runtime.verification``): the
  verification run in the leased workspace — admitted at
  ``BOUNDARY_DELIVERY_EFFECT`` before the run, its result accepted only
  through ``admit_and_mark``; recorded as a content-addressed record and a
  workflow receipt. Its provenance is ``VERIFICATION_PROVENANCE``:
  machine-observed, a separately executed owned run; no independent-party
  verification is claimed.
- Runtime lease, WORKSPACE PREPARATION (``_prepare_source_branch``): the
  local source branch — ``update-ref`` create-only against the zero id,
  then ``symbolic-ref HEAD`` — in this workflow's held lease only, before
  any delivery authority exists. Classified as workspace preparation,
  OUTSIDE the P1-A6 delivery effect inventory (no delivery record exists
  to hold a receipt), and exempt from nothing. BOTH mutations, also within
  one call, are separately admitted at their own boundary — a fresh check
  of lease ownership, retention, the S-IV/S-V gate and the Mission
  parent's ``github_pr`` permission, then the gate's critical section —
  and each one's state is recorded durably BEFORE it runs (``intended``
  before the create, ``partial`` before the HEAD move); each runs as an
  OWNED child under the P1-A6 transport's owned-child ledger keyed by the
  preparation attempt (named owner, own session, fsynced intent before the
  spawn, group id after — the mutating ``symbolic-ref HEAD <ref>`` is an
  effect form, the read-only queries stay unowned). Every re-entry first
  proves every earlier child settled (a live group holds and is never
  signalled; an unknown start, or a recorded step without an owned child,
  is unresolved) and only THEN classifies the refs and HEAD for its
  decisions — an earlier read-only observation decides nothing; the same
  proof runs again within the pass before the HEAD move (the create's group
  can outlive its leader through a same-group descendant) and after it,
  before anything is recorded attached or advanced. The refs and HEAD are
  re-classified immediately before the HEAD move and verified right after
  it; the create is never repeated and the HEAD move is attempted at most
  once; anything unprovable is recorded unresolved and never retried. Zero
  remote effects.
- P1-A6 delivery effects (``pr_delivery.machine``, receipts in the
  delivery record, executing before the effect, reconciliation per
  step): fetch, index refresh/read-tree, write-tree, ref CAS (including
  reconciliation's ref moves), commit, push, ``gh pr create``,
  reverification — each admitted by THIS module's effect gate.

Operation ids: every Runtime write presents the context's existing
UNCONSUMED reservation before any mint
(``reserve_reconciliation_operation_id``), so a refused write leaks
nothing; the desk reserves its decision id under the client-confirmation
context before it asks (truthful accounting, like the S-I decision).
"""

import hashlib
import os

from mission import record as mission_record
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store
from mission_control import delivery_artifacts as artifacts
from mission_control import engineering
from mission_control import gate as mission_gate
from mission_control import observation_receipts as receipts
from pr_delivery import authorization as delivery_authorization
from pr_delivery import boundary as delivery_boundary
from pr_delivery import machine as delivery_machine
from pr_delivery import mission_parent as delivery_parent
from pr_delivery import store as delivery_store
from workflow_authority import record as workflow_record
from workflow_authority.atomic import READ_ABSENT, READ_PRESENT, READ_UNAVAILABLE
from workflow_authority.digest import text_digest

# How long a prepared proposal (and so the delivery authority minted from
# it) stays valid, from its preparation: an ABSOLUTE expiry, never
# extended. Exact-value pinned; within P1-A6's own maximum.
DELIVERY_PROPOSAL_VALIDITY_SECONDS = 86400
# The bounded number of P1-A6 steps one Runtime pass drives.
MAX_DELIVERY_STEPS_PER_PASS = 8
# The candidate-identity prefix the human confirms (the same twelve
# characters the terminal ceremony asks a human to type).
DELIVERY_CONFIRM_CHARS = 12
# How many recorded source-branch preparation attempts that provably
# changed nothing are made before the preparation is recorded unresolved.
MAX_PREPARATION_ATTEMPTS = 3

# ``pr_delivery.transport.source_branch_state`` answers.
SOURCE_NOT_STARTED = "not_started"
SOURCE_PARTIAL = "partial"
SOURCE_DONE = "done"
SOURCE_FOREIGN = "foreign"
ZERO_OID = "0" * 40
# The preparation's two mutating children are OWNED under the P1-A6
# transport's owned-child ledger (its existing contract: named owner, own
# session, fsynced intent before the spawn, group id after, settlement
# proof by the group check), keyed by the preparation ATTEMPT — no delivery
# record exists yet — with these effect names. Read-only calls stay
# unowned and unledgered.
PREPARATION_OWNER_PREFIX = "prep-"
PREPARATION_STEP = "source_branch"
PREPARATION_CREATE = "create"
PREPARATION_HEAD_MOVE = "head_move"


def preparation_owner_key(attempt_digest):
    """The owned-child ledger key of one preparation attempt."""
    return PREPARATION_OWNER_PREFIX + attempt_digest

# What the delivery's verification IS, stated wherever it is shown (the
# card, the status read, the PR body): a real, separately executed run,
# owned and observed by the Runtime — and nothing more.
VERIFICATION_PROVENANCE = (
    "machine-observed: a separately executed, Runtime-owned run of the"
    " approved command in the leased workspace, its log bytes, exit status"
    " and timing captured and bound to the candidate and baseline; no"
    " independent-party verification is claimed")

SOURCE_REMOTE_NAME = "origin"
# The cancel reason a DECLINE records: the stored decline document's
# content address follows (``declined_delivery``).
DECLINE_REASON_PREFIX = "delivery declined: decision document "
SOURCE_BRANCH_PREFIX = "di-mission/"
BASE_REF_PREFIX = "refs/heads/"

VERIFICATION_TURN_PREFIX = artifacts.VERIFICATION_TURN_PREFIX
PROPOSAL_TURN_PREFIX = artifacts.PROPOSAL_TURN_PREFIX
BINDING_TURN_PREFIX = artifacts.BINDING_TURN_PREFIX
DELIVERY_ID_PREFIX = "prd-"

REQUIREMENT_ENGINEERING = engineering.MANDATORY_REQUIREMENT_KEYS[0]
REQUIREMENT_REVIEW = engineering.MANDATORY_REQUIREMENT_KEYS[1]
REQUIREMENT_CANDIDATE = engineering.MANDATORY_REQUIREMENT_KEYS[2]
REQUIREMENT_DECISION = engineering.DELIVERY_DECISION_REQUIREMENT_KEY
REQUIREMENT_DELIVERED = engineering.DELIVERY_REQUIREMENT_KEY

ARTIFACT_KEY_VERIFICATION = "verification_record"
ARTIFACT_KEY_CANDIDATE = "candidate"
ARTIFACT_KEY_REVIEW = "review_round_%d"
ARTIFACT_KEY_PROPOSAL = "delivery_proposal"

_KIND_PREFERENCE = {
    REQUIREMENT_ENGINEERING: (mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                              mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST,
                              mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION),
    REQUIREMENT_REVIEW: (mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST,
                         mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
                         mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION),
    REQUIREMENT_CANDIDATE: (mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST,
                            mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION,
                            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD),
    REQUIREMENT_DECISION: (mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION,
                           mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST,
                           mission_record.EVIDENCE_KIND_VERIFICATION_RECORD),
    REQUIREMENT_DELIVERED: (mission_record.EVIDENCE_KIND_EXTERNAL_ATTESTATION,
                            mission_record.EVIDENCE_KIND_ARTIFACT_DIGEST,
                            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD),
}

# Pass results.
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_WAITING = "waiting"
STATUS_HELD = "held"
STATUS_BLOCKED = "blocked"
STATUS_AWAITING_DECISION = "awaiting_decision"
STATUS_DELIVERING = "delivering"
STATUS_DELIVERED = "delivered"
STATUS_COMPLETED = "completed"

PROBLEM_NOT_REQUESTED = "mission_delivery_not_requested"
PROBLEM_NOT_READY = "mission_delivery_not_ready"
PROBLEM_VERIFICATION_UNDECLARED = "mission_delivery_verification_undeclared"
PROBLEM_VERIFICATION_MISSING = "mission_delivery_verification_missing"
PROBLEM_VERIFICATION_FAILED = "mission_delivery_verification_failed"
PROBLEM_VERIFICATION_UNSETTLED = "mission_delivery_verification_unsettled"
PROBLEM_EVIDENCE_CONTRADICTED = "mission_delivery_evidence_contradicted"
PROBLEM_REQUIREMENT_UNDECLARED = "mission_delivery_requirement_undeclared"
PROBLEM_BINDINGS = "mission_delivery_bindings"
PROBLEM_PROPOSAL_ABSENT = "mission_delivery_proposal_absent"
PROBLEM_PROPOSAL_STALE = "mission_delivery_proposal_stale"
PROBLEM_PROPOSAL_EXPIRED = "mission_delivery_proposal_expired"
PROBLEM_DECISION_AMBIGUOUS = "mission_delivery_decision_ambiguous"
PROBLEM_DECISION_PROVENANCE = "mission_delivery_decision_provenance"
PROBLEM_DECISION_MISMATCH = "mission_delivery_decision_mismatch"
PROBLEM_ALREADY_DECIDED = "mission_delivery_already_decided"
PROBLEM_BINDING_CONFLICT = "mission_delivery_binding_conflict"
PROBLEM_MINT_REFUSED = "mission_delivery_mint_refused"
PROBLEM_RETENTION_RELEASED = "mission_delivery_retention_released"
PROBLEM_PREPARATION_OWNERSHIP = "mission_delivery_preparation_ownership"
PROBLEM_PREPARATION_FAILED = "mission_delivery_preparation_failed"
PROBLEM_PREPARATION_UNRESOLVED = "mission_delivery_preparation_unresolved"
# Task 8 S-VII: a retryable P1-A6 failure — a remote query (``ls-remote``,
# the ``gh`` lookup) or an effect verb failed. HELD, named, never absence:
# the machine re-queries on the next pass before any effect.
PROBLEM_TRANSPORT_FAILED = "mission_delivery_transport_failed"
PROBLEM_SOURCE = mission_gate.PROBLEM_SOURCE_UNAVAILABLE
# Task 8 S-VII: the Mission store's own typed refusals — the source could
# not be read, or it is at a hard capacity bound. Both are reversible and
# source-unavailable: never absent, revoked, denied, cancelled or completed.
SOURCE_UNAVAILABLE_PROBLEMS = (mission_store.PROBLEM_STORE_UNREADABLE,
                               mission_store.PROBLEM_STORE_FULL)


def source_unavailable_problem(problem):
    """True for the Mission store's own typed unavailability refusals."""
    return problem in SOURCE_UNAVAILABLE_PROBLEMS


# The preparation state the delivery status reports when the workflow store
# itself could not be read (never "none prepared").
PREPARATION_UNAVAILABLE = "unavailable"


class _WorkflowStoreUnavailable(Exception):
    """The desk's workflow-store read failed (its problem string): a
    source-unavailable condition, never an absent record."""
PROBLEM_MISSION_WRITE = "mission_delivery_mission_write"

_RUNTIME_PRINCIPAL = mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER
_CLIENT_PRINCIPAL = mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION


class _Stop(Exception):
    """One pass stops here with a closed status and problem."""

    def __init__(self, status, problem, detail):
        super(_Stop, self).__init__(detail)
        self.status = status
        self.problem = problem
        self.detail = detail


def _result(status, problem=None, detail=None, **fields):
    result = {"status": status, "problem": problem, "detail": detail,
              "changed": False, "delivery_id": None,
              "proposal_digest_sha256": None, "attested": 0}
    result.update(fields)
    return result


# -- pure readers ---------------------------------------------------------------


def current_revision(stored):
    """The current revision entry of a ``MissionService.get`` projection."""
    return stored["record"]["revisions"][-1]


def delivery_requested(stored):
    return current_revision(stored)["proposal"].get(
        "requested_delivery_target") == mission_record.DELIVERY_TARGET_GITHUB_PR


def declared_verification(stored):
    """The approved verification argv of the current revision, or None."""
    verification = current_revision(stored)["proposal"].get("verification")
    if not isinstance(verification, dict):
        return None
    return list(verification["argv"])


def source_branch(mission_id, revision):
    return "%s%s-r%d" % (SOURCE_BRANCH_PREFIX, mission_id, revision)


def base_branch(entry):
    ref = (entry.get("approved_baseline") or {}).get("ref") or ""
    return ref[len(BASE_REF_PREFIX):] if ref.startswith(BASE_REF_PREFIX) else None


def delivery_id_for(decision_id, proposal_digest):
    """The DETERMINISTIC delivery id of one decision about one proposal."""
    return DELIVERY_ID_PREFIX + hashlib.sha256(
        ("mission-delivery:%s:%s" % (decision_id, proposal_digest)).encode("utf-8")
    ).hexdigest()[:24]


workflow_receipts = artifacts.workflow_receipts
workflow_receipt = artifacts.workflow_receipt


def delivery_fingerprint(record):
    """A delivery record's recorded state as ONE token: its phase and each
    step's state in the closed step order (Task 8 S-VII, L1). Any progress
    of the delivery changes it."""
    return "%s/%s" % (record["phase"], ",".join(
        record["steps"][step]["state"] for step in delivery_authorization.STEPS))


def latest_held(entries, mission_id, revision):
    """The latest recorded held-delivery fact (``parse_held`` fields plus
    ``workflow_id`` and ``recorded_at``) on the Mission's workflow records
    for ``revision``, or None — read from the receipts only."""
    found = None
    for entry in entries:
        linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
        if linkage.get("mission_id") != mission_id or linkage.get("revision") != revision:
            continue
        for receipt in workflow_receipts(entry, artifacts.HELD_TURN_PREFIX):
            fields = artifacts.parse_held(receipt.get("bounded_summary"))
            if fields is not None:
                fields.update(workflow_id=entry["workflow_id"],
                              recorded_at=receipt.get("recorded_at"))
                found = fields
    return found
verification_receipt = artifacts.verification_receipt


def exact_candidate(entry):
    """The latest candidate observation when it is consistent, exact and
    taken against the approved baseline with HEAD at that baseline (the
    pre-delivery state); else None."""
    baseline = (entry.get("approved_baseline") or {}).get("commit_sha")
    observation = receipts.observed_candidate(entry)
    if observation is None or not observation["consistent"]:
        return None
    if observation["status"] != receipts.CANDIDATE_STATUS_EXACT:
        return None
    if observation["base"] != baseline or observation["head"] != baseline:
        return None
    return observation


def review_approval(entry):
    """``{round, file, content}`` of the PROVEN APPROVE standing, or None."""
    proof = receipts.review_round_reading(entry)["proof"]
    if proof is None or proof["decision"] != "APPROVE":
        return None
    for number, decision, name, digest in receipts.observed_review_rounds(entry):
        if number == proof["round"] and digest == proof["content"] and (
            decision == "APPROVE"
        ):
            return {"round": number, "file": name, "content": digest}
    return None


def verification_for(entry, directory, mission_id, revision, identity, base):
    """``(digest, record)`` of the LATEST verification record the workflow
    names for exactly this workflow, Mission revision, candidate identity
    and baseline (document and complete log re-hashed), or ``(None,
    None)``."""
    for receipt in reversed(workflow_receipts(entry, VERIFICATION_TURN_PREFIX)):
        try:
            record = artifacts.load_verification(directory, receipt["digest"])
        except artifacts.ArtifactError:
            continue
        if (record["workflow_id"] == entry["workflow_id"]
                and record["mission_id"] == mission_id
                and record["mission_revision"] == revision
                and record["candidate_identity_digest_sha256"] == identity
                and record["base_oid"] == base):
            return receipt["digest"], record
    return None, None


def pr_content(mission_id, revision, proposal):
    return {
        "title": "Dodging Infinity Mission %s revision %d" % (mission_id, revision),
        "objective": proposal["objective"],
        "architecture_notes": proposal["requested_scope"],
        "nonblocking_risks": "",
    }


def evidence_block(task_id, verified, review, verification, identity, base, now):
    """The P1-A6 evidence triple, every item stamped with the exact
    candidate identity and baseline: the engineering completion is the
    Runtime's ACCEPTED verification-turn result, the review the PROVEN
    APPROVE round, the verification the Runtime producer's record."""
    stamp = {"candidate_identity_digest_sha256": identity, "base_oid": base}
    return {
        "engineering_complete": dict({
            "task_id": task_id,
            "status": delivery_authorization.EVIDENCE_ENGINEERING_STATUS_COMPLETE,
            "task_state_sha256": verified["digest"],
            "recorded_at": verified["recorded_at"],
        }, **stamp),
        "reviewer_approve": dict({
            "task_id": task_id, "round": review["round"],
            "review_file_name": review["file"],
            "review_file_sha256": review["content"],
            "decision": delivery_authorization.EVIDENCE_REVIEW_DECISION_APPROVE,
            "recorded_at": now,
        }, **stamp),
        "independent_verification": dict({
            "command_argv": list(verification["command_argv"]),
            "exit_status": verification["exit_status"],
            "log_sha256": verification["log_sha256"],
            "log_bytes": verification["log_bytes"],
            "ran_at": verification["ran_at"],
            "recorded_at": verification["finished_at"],
        }, **stamp),
    }


def mission_parent_block(stored, authorization_id):
    """The Mission parent a proposal binds: the current revision, its
    proposal digest and the LIVE authorization's id and digest; None when
    the named authorization is not the live one."""
    if stored["live_authorization_id"] != authorization_id:
        return None
    entry = current_revision(stored)
    for authorization in stored["authorizations"]:
        if authorization["authorization_id"] == authorization_id:
            return {
                "mission_id": stored["record"]["mission_id"],
                "revision": entry["revision"],
                "authorization_id": authorization_id,
                "authorization_digest_sha256":
                    authorization["authorization_digest_sha256"],
                "proposal_digest_sha256": entry["proposal_digest_sha256"],
            }
    return None


def build_proposal(entry, stored, bindings, verification_digest, verification,
                   review, now):
    """The PURE proposal construction (see the module docstring, step 4):
    reads its inputs, writes nothing. ``bindings`` are the live read-only
    P1-A6 bindings of the leased workspace."""
    linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
    parent = mission_parent_block(stored, linkage["authorization_id"])
    if parent is None:
        raise _Stop(STATUS_BLOCKED, PROBLEM_NOT_READY,
                    "the workflow's authorization is not the Mission's live"
                    " authorization; no proposal binds a stale parent")
    task_id = entry["target_engine"]["task_id"]
    evidence = evidence_block(
        task_id, entry["verified_result"], review, verification,
        bindings["digest"], bindings["head"], now)
    template = delivery_authorization.authority_template(
        bindings, evidence, verification["command_argv"],
        {"workflow_id": entry["workflow_id"], "engineering_task_id": task_id},
        {"workflow_id": parent["mission_id"],
         "mission_authorization_digest_sha256":
             parent["authorization_digest_sha256"]},
        pr_content(parent["mission_id"], parent["revision"],
                   current_revision(stored)["proposal"]))
    return {
        "schema": artifacts.PROPOSAL_SCHEMA, "mission": parent,
        "workflow_id": entry["workflow_id"], "task_id": task_id,
        "authority_template": template,
        "verification_record_digest_sha256": verification_digest,
        "proposed_at": now,
        # Whole seconds, never later than proposal time plus the bound.
        "expires_at": int(now) + DELIVERY_PROPOSAL_VALIDITY_SECONDS,
    }


def proposal_currency(proposal, stored, entry, now):
    """Why ``proposal`` is not the CURRENT proposal of this workflow and
    Mission right now, or None: it must name this workflow, the Mission's
    current revision, proposal digest and live authorization, and be
    unexpired."""
    if proposal["workflow_id"] != entry["workflow_id"]:
        return PROBLEM_PROPOSAL_STALE, "the proposal names another workflow"
    parent = mission_parent_block(stored, proposal["mission"]["authorization_id"])
    if parent is None or parent != proposal["mission"]:
        return PROBLEM_PROPOSAL_STALE, (
            "the proposal binds a Mission revision or authorization that is"
            " no longer current")
    if proposal["expires_at"] <= now:
        return PROBLEM_PROPOSAL_EXPIRED, "the proposal expired at %s" % (
            proposal["expires_at"],)
    return None


def proposal_staleness(proposal, stored, entry, now, verification_digest, identity):
    """Why ``proposal`` is not the one to present or mint for the current
    verification record and candidate identity, or None."""
    if proposal is None:
        return PROBLEM_PROPOSAL_ABSENT, "no delivery proposal is prepared"
    currency = proposal_currency(proposal, stored, entry, now)
    if currency is not None:
        return currency
    if proposal["verification_record_digest_sha256"] != verification_digest:
        return PROBLEM_PROPOSAL_STALE, (
            "the proposal binds verification record %s, not the current %s"
            % (proposal["verification_record_digest_sha256"], verification_digest))
    bound = proposal["authority_template"]["candidate"]["identity_digest_sha256"]
    if bound != identity:
        return PROBLEM_PROPOSAL_STALE, (
            "the proposal binds candidate %s, not the observed %s" % (bound, identity))
    return None


def latest_mission_artifact(state_record, key):
    latest = None
    for artifact in (state_record or {}).get("artifacts") or []:
        if artifact["key"] == key:
            latest = artifact
    return latest


def mission_proposal(state_record, directory):
    """``(digest, document)`` of the proposal the Mission's latest
    ``delivery_proposal`` artifact names — recorded by the Runtime (local
    process provenance), its document re-hashed from the artifact store —
    or ``(None, None)``."""
    artifact = latest_mission_artifact(state_record, ARTIFACT_KEY_PROPOSAL)
    if artifact is None or artifact["provenance"].get("principal_kind") != (
        _RUNTIME_PRINCIPAL
    ):
        return None, None
    try:
        document = artifacts.load_document(
            directory, artifact["content_digest_sha256"],
            artifacts.PROPOSAL_SCHEMA, artifacts.PROPOSAL_KEYS)
    except artifacts.ArtifactError:
        return None, None
    return artifact["content_digest_sha256"], document


def _evidence_of(state_record, activation_id, key):
    return [evidence for evidence in (state_record or {}).get("evidence") or []
            if evidence["requirement_key"] == key
            and evidence["activation_id"] == activation_id
            and evidence["invalidation"] is None]


def accepted_decision(state_record, activation_id, directory):
    """The ONE accepted ``delivery_decision`` evidence of the activation,
    its decision document and why not: ``(evidence, document, problem,
    detail)``. Submission AND acceptance must carry the client
    confirmation provenance (a caller-supplied dictionary or a matching
    digest alone proves nothing), the document must hash to the accepted
    digest, carry the accept action and name the submission's own
    reserved operation id as its decision id."""
    accepted = [evidence for evidence in _evidence_of(state_record, activation_id,
                                                      REQUIREMENT_DECISION)
                if evidence["acceptance"] is not None]
    if not accepted:
        return None, None, None, None
    if len(accepted) > 1:
        return None, None, PROBLEM_DECISION_AMBIGUOUS, (
            "%d accepted delivery decisions exist for this activation; none is"
            " minted" % len(accepted))
    evidence = accepted[0]
    if evidence["provenance"].get("principal_kind") != _CLIENT_PRINCIPAL or (
        evidence["acceptance"]["provenance"].get("principal_kind") != _CLIENT_PRINCIPAL
    ):
        return None, None, PROBLEM_DECISION_PROVENANCE, (
            "the delivery decision %s was not submitted and accepted under the"
            " client-confirmation context" % evidence["evidence_id"])
    try:
        document = artifacts.load_document(
            directory, evidence["content_digest_sha256"],
            artifacts.DECISION_SCHEMA, artifacts.DECISION_KEYS)
    except artifacts.ArtifactError as exc:
        return None, None, exc.problem, str(exc)
    if document["action"] != artifacts.DECISION_ACCEPT or (
        document["decision_id"] != evidence["operation_id"]
    ):
        return None, None, PROBLEM_DECISION_MISMATCH, (
            "the accepted decision document is not an accept bound to the"
            " evidence's own reserved operation")
    return evidence, document, None, None


def declined_delivery(controls, directory, entry):
    """Whether the Mission's sticky cancel request IS the client-confirmed
    DECLINE of ``entry``'s delivery proposal: requested under the
    client-confirmation context, its reason naming a stored decline
    document for this Mission revision whose proposal names this
    workflow. Pure read."""
    request = controls.get("cancel_request")
    if not isinstance(request, dict) or (request.get("provenance") or {}).get(
        "principal_kind"
    ) != _CLIENT_PRINCIPAL:
        return False
    reason = request.get("reason") or ""
    if not reason.startswith(DECLINE_REASON_PREFIX):
        return False
    linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
    try:
        document = artifacts.load_document(
            directory, reason[len(DECLINE_REASON_PREFIX):],
            artifacts.DECISION_SCHEMA, artifacts.DECISION_KEYS)
        proposal = artifacts.load_document(
            directory, document["proposal_digest_sha256"],
            artifacts.PROPOSAL_SCHEMA, artifacts.PROPOSAL_KEYS)
    except artifacts.ArtifactError:
        return False
    return (document["action"] == artifacts.DECISION_DECLINE
            and document["mission_id"] == linkage.get("mission_id")
            and document["revision"] == linkage.get("revision")
            and proposal["workflow_id"] == entry.get("workflow_id"))


def attested_receipt(state_record, receipt_id):
    """The attested artifact of ``receipt_id`` (latest), or None."""
    latest = None
    for artifact in mission_state.attested_artifacts(state_record or {"artifacts": []}):
        if artifact["locator"] == receipt_id:
            latest = artifact
    return latest


def _attested_as(state_record, receipt, step_state):
    artifact = attested_receipt(state_record, receipt["receipt_id"])
    if artifact is None:
        return None
    marker = mission_state.receipt_attestation_of(artifact)
    if marker["receipt_state"] == receipt["state"] and marker["step_state"] == step_state:
        return artifact
    return None


def delivery_store_refusal(directory):
    """Task 8 S-VII (item E): why the delivery store could not take a NEW
    delivery record now, as ``(problem, detail)``, or None — UNREADABLE
    (the observer read's ``unavailable``) or SATURATED (every record is
    active, so the store's own pruning of inactive records frees no room).
    Read-only; both are reversible: HELD, never blocked, revoked or
    denied."""
    read = delivery_store.DeliveryStore(directory).read()
    if read.availability == READ_UNAVAILABLE:
        return PROBLEM_SOURCE, "the delivery store is unavailable: %s" % read.problem
    if read.availability == READ_PRESENT:
        active = sum(1 for record in read.document["deliveries"].values()
                     if delivery_store.is_active(record))
        if active >= delivery_store.MAX_PR_DELIVERY_RECORDS:
            return PROBLEM_SOURCE, (
                "the delivery store is full (%s: %d active records); nothing is"
                " prepared or minted until one ends"
                % (delivery_store.PROBLEM_STORE_FULL, active))
    return None


def _read_delivery(directory, delivery_id):
    """The delivery record ``delivery_id`` through the store's validated
    observer read: None when absent; an unavailable store holds the pass
    (never read as absence)."""
    read = delivery_store.DeliveryStore(directory).read()
    if read.availability == READ_ABSENT:
        return None
    if read.availability != READ_PRESENT:
        raise _Stop(STATUS_HELD, PROBLEM_SOURCE,
                    "the delivery store is unavailable: %s" % read.problem)
    return read.document["deliveries"].get(delivery_id)


def _preferred_kind(requirement_key, declared):
    for kind in _KIND_PREFERENCE[requirement_key]:
        if kind in declared:
            return kind
    return None


def _requirement(state, key):
    content = state["contract"]["content"] or {}
    for requirement in content.get("requirements") or []:
        if requirement["key"] == key:
            return requirement
    return None


class MissionDelivery(object):
    """The Runtime-side driver of one Mission-origin workflow's delivery
    (see the module docstring). ``gate`` is the Runtime's
    ``MissionEffectGate`` (its service and local-process context);
    ``machine`` a ``DeliveryMachine`` over the delivery store (its effect
    gate is set per drive); ``bindings_fn(repo, remote, base_branch)`` the
    live read-only P1-A6 bindings; ``mint_fn(store_dir, delivery_id,
    authority, now)`` the client-confirmed minting
    (``pr_delivery.cli.authorize_client_confirmed``)."""

    def __init__(self, gate, workflow_store_directory, delivery_store_directory,
                 machine, bindings_fn, mint_fn, clock):
        self.gate = gate
        self.service = gate.service
        self.context = gate.context
        self.artifact_directory = artifacts.artifact_directory(
            workflow_store_directory)
        self.delivery_store_directory = delivery_store_directory
        self.machine = machine
        self._bindings = bindings_fn
        self._mint = mint_fn
        self._clock = clock

    # -- the Broker's reads ------------------------------------------------

    def plan(self, entry):
        """What this pass needs, from pure reads: ``{"applicable",
        "problem", "detail", "argv", "identity", "base", "mission_id",
        "revision", "needs_verification"}``. The Broker runs the verification
        producer exactly when ``needs_verification`` is true."""
        plan = {"applicable": False, "problem": None, "detail": None,
                "argv": None, "identity": None, "base": None, "mission_id": None,
                "revision": None, "needs_verification": False}
        try:
            stored, _state = self._applicable(entry)
        except _Stop as stop:
            plan.update(problem=stop.problem, detail=stop.detail)
            return plan
        except (mission_store.MissionStoreError, mission_record.MissionError) as exc:
            plan.update(problem=PROBLEM_SOURCE, detail=str(exc))
            return plan
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        plan.update(applicable=True, argv=declared_verification(stored),
                    mission_id=linkage["mission_id"], revision=linkage["revision"])
        if self._delivery_bound(entry):
            return plan
        observation = exact_candidate(entry)
        plan.update(identity=observation["identity"], base=observation["base"])
        if plan["argv"] is None:
            plan.update(problem=PROBLEM_VERIFICATION_UNDECLARED,
                        detail="the Mission revision declares no verification argv")
            return plan
        digest, _record = verification_for(
            entry, self.artifact_directory, plan["mission_id"], plan["revision"],
            plan["identity"], plan["base"])
        plan["needs_verification"] = digest is None
        return plan

    def _delivery_bound(self, entry):
        return bool(workflow_receipts(entry, BINDING_TURN_PREFIX))

    def declined(self, entry, controls):
        """``declined_delivery`` over this driver's artifact store (the
        Broker's retention step asks)."""
        return declined_delivery(controls, self.artifact_directory, entry)

    def _applicable(self, entry):
        """``(stored, state)`` when delivery applies to ``entry`` now; raises
        ``_Stop`` naming why not."""
        if not workflow_record.is_mission_core_kind(entry):
            raise _Stop(STATUS_NOT_APPLICABLE, PROBLEM_NOT_REQUESTED,
                        "not a Mission-origin record")
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        stored = self.service.get(linkage["mission_id"])
        if not delivery_requested(stored):
            raise _Stop(STATUS_NOT_APPLICABLE, PROBLEM_NOT_REQUESTED,
                        "the Mission's current revision requests no github_pr"
                        " delivery")
        state = self.service.get_state(linkage["mission_id"])
        bound = self._delivery_bound(entry)
        if entry["phase"] != workflow_record.PHASE_COMPLETED:
            raise _Stop(STATUS_NOT_APPLICABLE, PROBLEM_NOT_READY,
                        "the workflow is %s, not COMPLETED" % entry["phase"])
        if not bound:
            lease = entry.get("workspace_lease")
            if not isinstance(lease, dict) or lease.get("released_at") is not None:
                raise _Stop(STATUS_NOT_APPLICABLE, PROBLEM_NOT_READY,
                            "the workspace lease is not held")
            if not workflow_record.retention_protects(entry, self._clock()):
                raise _Stop(STATUS_NOT_APPLICABLE, PROBLEM_RETENTION_RELEASED,
                            "the delivery candidate's retention is released")
            if review_approval(entry) is None:
                raise _Stop(STATUS_WAITING, PROBLEM_NOT_READY,
                            "the review standing is not a PROVEN APPROVE")
            if exact_candidate(entry) is None:
                raise _Stop(STATUS_WAITING, PROBLEM_NOT_READY,
                            "no exact candidate observation against the approved"
                            " baseline")
        return stored, state

    # -- one pass ----------------------------------------------------------------

    def advance(self, entry, persist):
        """One delivery pass for ``entry`` (the Broker holds the workflow
        lock; ``persist()`` persists ``entry``'s record). Returns a closed
        result; never raises for a refusal."""
        try:
            return self._advance(entry, persist)
        except _Stop as stop:
            return _result(stop.status, stop.problem, stop.detail)
        except mission_store.MissionStoreError as exc:
            return _result(STATUS_HELD, PROBLEM_SOURCE, str(exc))
        except mission_record.MissionError as exc:
            return _result(STATUS_HELD, exc.problem, str(exc))

    def _advance(self, entry, persist):
        stored, state = self._applicable(entry)
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        mission_id = linkage["mission_id"]
        binding = self._binding(entry)
        if binding is not None:
            return self._deliver(entry, persist, binding)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            raise _Stop(STATUS_NOT_APPLICABLE, mission_gate.PROBLEM_MISSION_TERMINAL,
                        "mission %s is %s" % (mission_id, state["progress"]))
        self._admit(entry)
        observation = exact_candidate(entry)
        review = review_approval(entry)
        argv = declared_verification(stored)
        if argv is None:
            raise _Stop(STATUS_BLOCKED, PROBLEM_VERIFICATION_UNDECLARED,
                        "the Mission revision declares no verification argv; the"
                        " delivery cannot be verified")
        verification_digest, verification = verification_for(
            entry, self.artifact_directory, mission_id, linkage["revision"],
            observation["identity"], observation["base"])
        if verification is None:
            raise _Stop(STATUS_WAITING, PROBLEM_VERIFICATION_MISSING,
                        "no verification record binds this exact candidate and"
                        " baseline yet")
        if verification["command_argv"] != argv:
            raise _Stop(STATUS_BLOCKED, PROBLEM_VERIFICATION_MISSING,
                        "the verification record ran another argv than the"
                        " approved one")
        if verification["exit_status"] != 0:
            raise _Stop(STATUS_BLOCKED, PROBLEM_VERIFICATION_FAILED,
                        "the approved verification exited %d; only a green run"
                        " is delivered" % verification["exit_status"])
        if verification["settlement"] != artifacts.SETTLEMENT_SETTLED:
            raise _Stop(STATUS_BLOCKED, PROBLEM_VERIFICATION_UNSETTLED,
                        "the verification process group was not proven settled")
        changed = self._accept_runtime_evidence(
            mission_id, entry, verification_digest, review, observation)
        state = self.service.get_state(mission_id)
        evidence, document, problem, detail = accepted_decision(
            state["record"], state["contract"]["activation_id"],
            self.artifact_directory)
        if problem is not None:
            raise _Stop(STATUS_BLOCKED, problem, detail)
        proposal_digest, proposal = mission_proposal(state["record"],
                                                     self.artifact_directory)
        now = self._clock()
        stale = proposal_staleness(proposal, stored, entry, now,
                                   verification_digest, observation["identity"])
        if evidence is not None:
            # A DECIDED proposal is minted exactly as decided or not at all:
            # it is never re-prepared under the human's answer.
            if stale is not None:
                raise _Stop(STATUS_BLOCKED, stale[0],
                            "the decided proposal is no longer current: %s" % stale[1])
            if document["proposal_digest_sha256"] != proposal_digest or (
                document["mission_id"] != mission_id
                or document["revision"] != linkage["revision"]
                or document["candidate_identity_digest_sha256"]
                != observation["identity"]
            ):
                raise _Stop(STATUS_BLOCKED, PROBLEM_DECISION_MISMATCH,
                            "the accepted delivery decision is not bound to the"
                            " current proposal %s" % proposal_digest)
            binding = self._bind(entry, persist, evidence, document, proposal_digest)
            return self._deliver(entry, persist, binding, changed=True)
        if stale is not None:
            proposal_digest, proposal = self.prepare_proposal(
                entry, persist, stored, verification_digest, verification, review,
                observation, now)
            changed = True
        if self._ensure_proposal_receipt(entry, proposal_digest, proposal, persist):
            changed = True
        return _result(STATUS_AWAITING_DECISION, changed=changed,
                       proposal_digest_sha256=proposal_digest)

    # -- admission --------------------------------------------------------------

    def _admit(self, entry):
        admission = self.gate.admit(entry, mission_gate.BOUNDARY_DELIVERY_EFFECT)
        if not admission.ok:
            raise _Stop(STATUS_BLOCKED if admission.classification
                        == mission_gate.CLASS_TERMINAL else STATUS_HELD,
                        admission.problem, admission.detail)

    # -- Mission writes (Runtime context) --------------------------------------

    def _write(self, mission_id, call):
        """One Mission write with the context's unconsumed reservation and
        the current sequence; a moved document holds the pass."""
        operation_id = self.service.reserve_reconciliation_operation_id(self.context)
        sequence = self.service.get_state(mission_id)["sequence"]
        try:
            return call(operation_id, sequence)
        except mission_record.MissionError as exc:
            if exc.problem == mission_state_service.PROBLEM_STALE_SEQUENCE:
                raise _Stop(STATUS_HELD, exc.problem, str(exc))
            raise _Stop(STATUS_BLOCKED, exc.problem, str(exc))

    def _ensure_artifact(self, mission_id, key, role, locator_kind, locator, digest):
        state = self.service.get_state(mission_id)
        for artifact in (state["record"] or {}).get("artifacts") or []:
            if (artifact["key"] == key and artifact["content_digest_sha256"] == digest
                    and artifact["locator"] == locator and artifact["available"]
                    and artifact["role"] == role):
                return artifact["artifact_id"]
        outcome = self._write(mission_id, lambda op, seq: self.service.record_artifact(
            mission_id, op, seq, key, role, locator_kind, locator, digest, True, [],
            self.context))
        return outcome["artifact_id"]

    def _ensure_evidence(self, mission_id, key, digest, artifact_id):
        """Submit and accept ONE Runtime-observed requirement's evidence,
        once. Returns True when something was written."""
        state = self.service.get_state(mission_id)
        requirement = _requirement(state, key)
        if requirement is None:
            raise _Stop(STATUS_BLOCKED, PROBLEM_REQUIREMENT_UNDECLARED,
                        "the active contract declares no %r requirement" % key)
        kind = _preferred_kind(key, requirement["evidence_kinds"])
        existing = _evidence_of(state["record"], state["contract"]["activation_id"], key)
        accepted = [e for e in existing if e["acceptance"] is not None]
        if any(e["content_digest_sha256"] == digest for e in accepted):
            return False
        if accepted:
            raise _Stop(STATUS_BLOCKED, PROBLEM_EVIDENCE_CONTRADICTED,
                        "requirement %r already holds accepted evidence with"
                        " another digest; nothing is overwritten" % key)
        pending = [e for e in existing if e["acceptance"] is None
                   and e["content_digest_sha256"] == digest
                   and e["provenance"].get("principal_kind") == _RUNTIME_PRINCIPAL]
        if pending:
            evidence_id = pending[-1]["evidence_id"]
        else:
            evidence_id = self._write(mission_id, lambda op, seq: self.service.submit_evidence(
                mission_id, op, seq, key, kind, digest, [artifact_id],
                self.context))["evidence_id"]
        self._write(mission_id, lambda op, seq: self.service.accept_evidence(
            mission_id, op, seq, evidence_id, digest, self.context))
        return True

    def _accept_runtime_evidence(self, mission_id, entry, verification_digest,
                                 review, observation):
        changed = False
        artifact_id = self._ensure_artifact(
            mission_id, ARTIFACT_KEY_VERIFICATION, mission_record.ARTIFACT_ROLE_VERIFICATION,
            mission_state.LOCATOR_KIND_CONTENT_ADDRESS,
            "%s/%s%s" % (artifacts.ARTIFACT_DIR_NAME, verification_digest,
                         artifacts.DOCUMENT_SUFFIX), verification_digest)
        changed |= self._ensure_evidence(mission_id, REQUIREMENT_ENGINEERING,
                                         verification_digest, artifact_id)
        artifact_id = self._ensure_artifact(
            mission_id, ARTIFACT_KEY_REVIEW % review["round"],
            mission_record.ARTIFACT_ROLE_VERIFICATION,
            mission_state.LOCATOR_KIND_OPAQUE_REFERENCE,
            "review round %d %s" % (review["round"], review["file"]), review["content"])
        changed |= self._ensure_evidence(mission_id, REQUIREMENT_REVIEW,
                                         review["content"], artifact_id)
        artifact_id = self._ensure_artifact(
            mission_id, ARTIFACT_KEY_CANDIDATE, mission_record.ARTIFACT_ROLE_PRODUCED,
            mission_state.LOCATOR_KIND_OPAQUE_REFERENCE,
            "p1a6 candidate of workflow %s" % entry["workflow_id"],
            observation["identity"])
        changed |= self._ensure_evidence(mission_id, REQUIREMENT_CANDIDATE,
                                         observation["identity"], artifact_id)
        return changed

    # -- the preparation WRITE ----------------------------------------------------

    def prepare_proposal(self, entry, persist, stored, verification_digest,
                         verification, review, observation, now):
        """The explicit PREPARATION WRITE (module docstring, step 4): the
        local source branch (``_prepare_source_branch``, admitted and
        reconciled at its own boundary), then the live read-only bindings,
        the proposal, its canonical document and the Mission artifact. The
        workflow receipt follows (``_ensure_proposal_receipt``). Returns
        ``(digest, proposal)``."""
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        lease = entry["workspace_lease"]["path_realpath"]
        branch = source_branch(linkage["mission_id"], linkage["revision"])
        base = base_branch(entry)
        if base is None:
            raise _Stop(STATUS_BLOCKED, PROBLEM_BINDINGS,
                        "the approved baseline names no branch ref")
        self._prepare_source_branch(entry, persist, lease, branch, observation["base"])
        try:
            bindings = self._bindings(lease, SOURCE_REMOTE_NAME, base)
        except Exception as exc:                          # noqa: BLE001
            raise _Stop(STATUS_HELD, PROBLEM_BINDINGS,
                        "the live delivery bindings could not be read: %s: %s"
                        % (type(exc).__name__, exc))
        if bindings["repo"] != lease or bindings["git_dir"] != os.path.join(
            lease, ".git"
        ):
            raise _Stop(STATUS_BLOCKED, PROBLEM_BINDINGS,
                        "the bindings name repository %s (git dir %s), not the"
                        " leased workspace %s" % (bindings["repo"],
                                                  bindings["git_dir"], lease))
        if bindings["head"] != observation["base"] or (
            bindings["digest"] != observation["identity"]
            or bindings["source_branch"] != branch
        ):
            raise _Stop(STATUS_HELD, PROBLEM_BINDINGS,
                        "the live bindings disagree with the observed candidate"
                        " (HEAD %s, identity %s, branch %s)" % (
                            bindings["head"], bindings["digest"],
                            bindings["source_branch"]))
        proposal = build_proposal(entry, stored, bindings, verification_digest,
                                  verification, review, now)
        digest = artifacts.store_document(self.artifact_directory, proposal)
        self._ensure_artifact(
            linkage["mission_id"], ARTIFACT_KEY_PROPOSAL,
            mission_record.ARTIFACT_ROLE_PRODUCED,
            mission_state.LOCATOR_KIND_CONTENT_ADDRESS,
            "%s/%s%s" % (artifacts.ARTIFACT_DIR_NAME, digest, artifacts.DOCUMENT_SUFFIX),
            digest)
        return digest, proposal

    # -- the source-branch preparation: admitted, recorded, reconciled -----------

    def _attach_record(self, entry, persist, attempt, state, detail=None):
        digest, ref, head_oid, lease = attempt
        entry["receipts"] = list(entry["receipts"]) + [artifacts.attach_receipt(
            digest, state, ref, head_oid, lease, self._clock(), detail)]
        persist()

    def _preparation_refusal(self, entry, lease):
        """Why a preparation Git mutation may not run NOW, as ``(status,
        problem, detail)``, or None — evaluated FRESH for each mutation
        (the Mission is re-read here, never a pass-start snapshot). Its
        authority is DERIVED from the existing contracts, never asserted by
        a label: the Runtime's lease ownership (this workflow's held lease,
        its own ``.git`` directory, under the workflow lock the Broker
        holds), the candidate's retention, the S-IV/S-V gate at
        ``BOUNDARY_DELIVERY_EFFECT`` (revision, live authority, decision
        provenance, controls, readiness), and the Mission Core's ONE parent
        validator (the live authorization permits ``github_pr`` — the check
        P1-A6's parent seam runs). The gate is then re-applied under the
        Mission lock by ``admit_and_mark`` (``_admit_preparation``)."""
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        held = entry.get("workspace_lease") or {}
        git_dir = os.path.join(lease, ".git")
        if held.get("released_at") is not None or held.get("path_realpath") != lease or (
            os.path.realpath(lease) != lease or os.path.islink(git_dir)
            or not os.path.isdir(git_dir)
        ):
            return (STATUS_BLOCKED, PROBLEM_PREPARATION_OWNERSHIP,
                    "the preparation writes only inside this workflow's held"
                    " lease %s and its own .git directory" % lease)
        if not workflow_record.retention_protects(entry, self._clock()):
            return (STATUS_BLOCKED, PROBLEM_RETENTION_RELEASED,
                    "the delivery candidate's retention is released")
        admission = self.gate.admit(entry, mission_gate.BOUNDARY_DELIVERY_EFFECT)
        if not admission.ok:
            return (STATUS_BLOCKED if admission.classification
                    == mission_gate.CLASS_TERMINAL else STATUS_HELD,
                    admission.problem, admission.detail)
        # Task 8 S-VII (item E): the preparation precedes the first mint,
        # so it runs only while the delivery store could take the record.
        refusal = delivery_store_refusal(self.delivery_store_directory)
        if refusal is not None:
            return (STATUS_HELD,) + refusal
        parent = mission_parent_block(self.service.get(linkage["mission_id"]),
                                      linkage["authorization_id"])
        check = None if parent is None else self.service.check_parent_authority(
            linkage["mission_id"], parent["authorization_digest_sha256"],
            mission_record.DELIVERY_TARGET_GITHUB_PR)
        if check is not None and not check.valid and source_unavailable_problem(
            check.problem
        ):
            # Task 8 S-VII: a store that cannot be read is a reversible HOLD,
            # never a block of the preparation.
            return (STATUS_HELD, PROBLEM_SOURCE,
                    "the Mission store could not be read for the parent check (%s):"
                    " %s" % (check.problem, check.detail))
        if check is None or not check.valid:
            return (STATUS_BLOCKED, PROBLEM_NOT_READY if check is None else check.problem,
                    "the live Mission authorization does not permit github_pr"
                    " delivery%s" % ("" if check is None else ": %s" % check.detail))
        return None

    def _admit_preparation(self, entry, persist, lease, attempt, state):
        """ONE preparation mutation's admission at its OWN boundary: a
        fresh ``_preparation_refusal``, then the gate's short critical
        section whose mark durably records ``state`` for this attempt
        BEFORE the mutation runs. Raises ``_Stop`` with nothing recorded
        when refused."""
        refusal = self._preparation_refusal(entry, lease)
        if refusal is not None:
            raise _Stop(*refusal)

        def mark():
            entry["receipts"] = list(entry["receipts"]) + [artifacts.attach_receipt(
                attempt[0], state, attempt[1], attempt[2], lease, self._clock())]
            persist()
        admission = self.gate.admit_and_mark(entry, mission_gate.BOUNDARY_DELIVERY_EFFECT,
                                             mark)
        if not admission.ok:
            raise _Stop(STATUS_BLOCKED if admission.classification
                        == mission_gate.CLASS_TERMINAL else STATUS_HELD,
                        admission.problem, admission.detail)

    def _prepare_source_branch(self, entry, persist, lease, branch, head_oid):
        """The ONE local Git mutation pair before any delivery authority
        exists: the named source branch the proposal binds, in the leased
        workspace — ``refs/heads/<branch>`` created at exactly ``head_oid``
        (create-only compare-and-swap against the zero id: an existing ref
        is never moved or deleted), then HEAD pointed at it. Each step is
        atomic on its own; the PAIR is not, so:

        - PROVE SETTLEMENT FIRST: every mutation runs as an OWNED child
          (``_owned_mutation``: the transport's owned-child ledger keyed by
          ``preparation_owner_key``); before any decision every earlier
          child's settlement is proven by the group check
          (``_prove_settled``) — a live group holds (never signalled), an
          unknown start or a recorded step without an owned child is
          unresolved — and it is proven AGAIN before the HEAD move and after
          it, within the same pass.
        - CLASSIFY AFTER THE PROOF: a first read-only observation of the
          refs and HEAD only fails fast on an unreadable lease and is
          compared with the post-proof one; it decides NOTHING, because a
          child still settling can change the refs right after it. Every
          adopt / retry / ``no_effect`` / advance decision rests on the
          classification taken AFTER the proof.
        - RECONCILE: the actual refs and HEAD
          (``source_branch_state``) against this attempt's durable states
          (``delivery_artifacts.attach_states``) — done is adopted
          (nothing re-done); a recorded attempt that changed nothing is
          re-attempted (proven absence); ref created with HEAD not moved
          and no HEAD move yet attempted gets its HEAD move (the create is
          never repeated); everything else — a foreign state, a HEAD move
          already attempted, too many attempts, a found state without
          recorded provenance — is recorded ``unresolved``, exposed, never
          retried, and nothing advances.
        - EACH MUTATION ADMITTED AT ITS OWN BOUNDARY
          (``_admit_preparation``): the create under a fresh admission that
          records ``intended`` first; the HEAD move under ANOTHER fresh
          admission that records ``partial`` first (``_move_head``) — so a
          hold, cancel or EDIT landing between the two steps stops the HEAD
          move — and a re-classification immediately before
          ``symbolic-ref``, so a ref that changed after the first
          classification is never attached.
        - RECORD WHAT RESULTED: ``attached`` (verified right after the
          move), ``failed`` with the found state or the refused admission,
          or ``unresolved``. Zero remote effects: neither step reaches a
          remote.

        Returns only when HEAD names the ref at ``head_oid``; raises
        ``_Stop`` otherwise."""
        ref = "refs/heads/" + branch
        attempt = (artifacts.attach_digest(entry["workflow_id"], lease, ref, head_oid),
                   ref, head_oid, lease)
        states = artifacts.attach_states(entry, attempt[0])
        last = states[-1] if states else None

        def unresolved(detail):
            self._unresolved(entry, persist, attempt, detail + changed)
        # A first READ-ONLY observation: an unreadable lease holds here,
        # before the settlement proof writes anything, and it is compared
        # with the post-proof classification below — it DECIDES NOTHING (a
        # child still settling can change the refs right after it is taken).
        before, _before_detail = self._classify(lease, attempt)
        changed = ""
        if last == artifacts.ATTACH_UNRESOLVED:
            unresolved("recorded unresolved earlier")
        # SETTLEMENT BEFORE ANY DECISION (``_prove_settled``).
        self._prove_settled(entry, persist, attempt)
        # EVERY decision below rests on THIS classification, taken after the
        # proof: no owned child of this attempt can change the refs or HEAD
        # any more.
        found, detail = self._classify(lease, attempt)
        if found != before:
            changed = ("; the refs and HEAD read %s before the settlement proof"
                       " and %s after it" % (before, found))
        if found == SOURCE_DONE:
            if last == artifacts.ATTACH_ATTACHED:
                return
            if last in (artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL,
                        artifacts.ATTACH_FAILED):
                self._attach_record(entry, persist, attempt, artifacts.ATTACH_ATTACHED,
                                    "reconciled from the refs and HEAD as found"
                                    + changed)
                return
            unresolved("HEAD already names %s without this Runtime's recorded"
                       " attempt" % ref)
        if found == SOURCE_FOREIGN:
            unresolved(detail)
        if last == artifacts.ATTACH_ATTACHED:
            unresolved("the attached source branch changed afterwards (%s)" % found)
        if found == SOURCE_PARTIAL:
            if artifacts.ATTACH_PARTIAL in states:
                unresolved("ref %s exists at %s with HEAD detached after its one"
                           " HEAD move was attempted" % (ref, head_oid))
            if artifacts.ATTACH_INTENDED not in states or last == artifacts.ATTACH_NO_EFFECT:
                unresolved("ref %s exists at %s with HEAD detached without this"
                           " Runtime's recorded attempt" % (ref, head_oid))
            self._move_head(entry, persist, lease, attempt, after_create=False)
            return
        # found == SOURCE_NOT_STARTED
        if artifacts.ATTACH_PARTIAL in states:
            unresolved("the ref %s this Runtime created is gone" % ref)
        if last in (artifacts.ATTACH_INTENDED, artifacts.ATTACH_FAILED):
            self._attach_record(entry, persist, attempt, artifacts.ATTACH_NO_EFFECT,
                                "reconciled: the ref is absent and HEAD unmoved" + changed)
        if states.count(artifacts.ATTACH_INTENDED) >= MAX_PREPARATION_ATTEMPTS:
            unresolved("%d recorded attempts changed nothing" % MAX_PREPARATION_ATTEMPTS)
        self._create_and_attach(entry, persist, lease, attempt)

    def _classify(self, lease, attempt):
        """The refs and HEAD as found (``source_branch_state``); an
        unreadable lease holds."""
        try:
            return self.machine.transport.source_branch_state(lease, attempt[1], attempt[2])
        except Exception as exc:                          # noqa: BLE001
            raise _Stop(STATUS_HELD, PROBLEM_BINDINGS,
                        "the leased refs could not be read: %s" % type(exc).__name__)

    def _unresolved(self, entry, persist, attempt, detail):
        """Record ``unresolved`` once (never twice in a row) and stop."""
        states = artifacts.attach_states(entry, attempt[0])
        if not states or states[-1] != artifacts.ATTACH_UNRESOLVED:
            self._attach_record(entry, persist, attempt, artifacts.ATTACH_UNRESOLVED,
                                detail)
        raise _Stop(STATUS_BLOCKED, PROBLEM_PREPARATION_UNRESOLVED,
                    "the source-branch preparation is unresolved (%s); nothing"
                    " advances and nothing is retried until an EDIT" % detail)

    def _prove_settled(self, entry, persist, attempt):
        """SETTLEMENT BEFORE ANY DECISION: every mutation this attempt ever
        started is an owned child, and before anything is classified for a
        decision, adopted, retried, moved or advanced its settlement must be
        PROVEN by the transport's own group check — on re-entry, again
        before the HEAD move (the create's group may outlive its leader: a
        same-group descendant keeps it alive, and ``_settle_child`` records
        ``settled: false``), and again after the HEAD move before anything
        advances. A live group holds (never signalled); an intent whose
        group was never recorded, or a recorded step with no owned child at
        all, is an outcome nobody can prove — unresolved. Returns only when
        every owned child of this attempt is proven settled."""
        transport = self.machine.transport
        key = preparation_owner_key(attempt[0])
        settlement = transport.unsettled_children(key)
        unknown = [detail for problem, detail in settlement
                   if problem != transport.child_unsettled_problem]
        if unknown:
            self._unresolved(entry, persist, attempt,
                             "an earlier preparation child's start is unknown: %s"
                             % "; ".join(unknown))
        if settlement:
            raise _Stop(STATUS_HELD, transport.child_unsettled_problem,
                        "an earlier preparation child is still running; nothing is"
                        " adopted, retried, moved or advanced until its settlement is"
                        " proven: %s" % "; ".join(detail for _p, detail in settlement))
        states = artifacts.attach_states(entry, attempt[0])
        intents = transport.child_intents(key)
        if intents is None or states.count(artifacts.ATTACH_INTENDED) > intents.count(
            PREPARATION_CREATE
        ) or states.count(artifacts.ATTACH_PARTIAL) > intents.count(PREPARATION_HEAD_MOVE):
            self._unresolved(entry, persist, attempt,
                             "a recorded preparation step has no owned child record, so"
                             " its outcome cannot be proven")

    def _observe_branch(self, lease, attempt):
        try:
            return self.machine.transport.source_branch_state(lease, attempt[1], attempt[2])
        except Exception as exc:                          # noqa: BLE001
            return "unreadable", type(exc).__name__

    def _failed(self, entry, persist, lease, attempt, exc):
        """Record a refused step with the state as FOUND, then hold."""
        found, _detail = self._observe_branch(lease, attempt)
        self._attach_record(entry, persist, attempt, artifacts.ATTACH_FAILED,
                            "%s; found %s" % (type(exc).__name__, found))
        raise _Stop(STATUS_HELD, PROBLEM_PREPARATION_FAILED,
                    "the source-branch preparation failed (%s); the found state (%s)"
                    " is recorded and reconciled on the next pass"
                    % (type(exc).__name__, found))

    def _owned_mutation(self, attempt, effect, call):
        """Run ONE preparation mutation as an OWNED child of this attempt:
        the transport's existing contract (named owner, own session, fsynced
        ledger intent before the spawn, group id after, settlement row when
        it ends) under the attempt's key. Only the mutating call is owned —
        every read around it stays unowned."""
        transport = self.machine.transport
        previous = transport.effect_owner
        transport.effect_owner = {"delivery_id": preparation_owner_key(attempt[0]),
                                  "step": PREPARATION_STEP, "effect": effect}
        try:
            return call()
        finally:
            transport.effect_owner = previous

    def _create_and_attach(self, entry, persist, lease, attempt):
        """A fresh attempt: the create-only ref under ITS OWN admission
        (``intended`` recorded first), then the HEAD move under ANOTHER
        (``_move_head``). The create is never repeated: a later pass that
        finds the ref created with HEAD unmoved completes only the move."""
        self._admit_preparation(entry, persist, lease, attempt, artifacts.ATTACH_INTENDED)
        try:
            self._owned_mutation(
                attempt, PREPARATION_CREATE,
                lambda: self.machine.transport.update_ref(lease, attempt[1], attempt[2],
                                                          ZERO_OID))
        except Exception as exc:                          # noqa: BLE001
            self._failed(entry, persist, lease, attempt, exc)
        self._move_head(entry, persist, lease, attempt, after_create=True)

    def _move_head(self, entry, persist, lease, attempt, after_create):
        """The HEAD move — the second mutation — only after every earlier
        owned child of this attempt is PROVEN settled (``_prove_settled``:
        the create's group can outlive its leader), under its OWN admission
        (a fresh refusal check and the gate's critical section recording
        ``partial``, this preparation's one HEAD-move attempt), then a
        re-classification IMMEDIATELY before ``symbolic-ref`` (anything but
        ref-created-HEAD-detached-at-the-bound-commit is recorded
        ``unresolved`` and never attached), the move, the move's own
        settlement proven, and a verification right after it. A refused
        admission right after this call's create is recorded ``failed`` with
        the refusal (the ref stands created, HEAD unmoved; the next admitted
        pass moves HEAD, never re-creating the ref); a group still alive
        holds with nothing recorded, and the next pass reconciles."""
        digest, ref, head_oid, _lease = attempt
        # The create's group may still be alive although ``update-ref``
        # returned (a same-group descendant): no HEAD move, no ``partial``,
        # until its settlement is proven.
        self._prove_settled(entry, persist, attempt)
        try:
            self._admit_preparation(entry, persist, lease, attempt, artifacts.ATTACH_PARTIAL)
        except _Stop as stop:
            if after_create:
                self._attach_record(entry, persist, attempt, artifacts.ATTACH_FAILED,
                                    "the ref is created; the HEAD move was not admitted"
                                    " (%s)" % stop.problem)
            raise
        found, detail = self._observe_branch(lease, attempt)
        if found != SOURCE_PARTIAL:
            self._attach_record(entry, persist, attempt, artifacts.ATTACH_UNRESOLVED,
                                "immediately before the HEAD move the refs and HEAD"
                                " were found %s (%s)" % (found, detail))
            raise _Stop(STATUS_BLOCKED, PROBLEM_PREPARATION_UNRESOLVED,
                        "the source-branch preparation is unresolved: before the HEAD"
                        " move the refs and HEAD were found %s (%s); HEAD is not"
                        " attached and nothing is retried until an EDIT" % (found, detail))
        try:
            self._owned_mutation(attempt, PREPARATION_HEAD_MOVE,
                                 lambda: self.machine.transport.attach_head(lease, ref))
        except Exception as exc:                          # noqa: BLE001
            self._failed(entry, persist, lease, attempt, exc)
        # Nothing is verified, recorded ``attached`` or advanced while the
        # HEAD move's own group is still alive.
        self._prove_settled(entry, persist, attempt)
        found, detail = self._observe_branch(lease, attempt)
        if found != SOURCE_DONE:
            self._attach_record(entry, persist, attempt, artifacts.ATTACH_UNRESOLVED,
                                "right after the HEAD move the refs and HEAD were found"
                                " %s (%s)" % (found, detail))
            raise _Stop(STATUS_BLOCKED, PROBLEM_PREPARATION_UNRESOLVED,
                        "the source-branch preparation is unresolved: right after the"
                        " HEAD move the refs and HEAD were found %s (%s)"
                        % (found, detail))
        self._attach_record(entry, persist, attempt, artifacts.ATTACH_ATTACHED)

    def _ensure_proposal_receipt(self, entry, digest, proposal, persist):
        if any(r["digest"] == digest
               for r in workflow_receipts(entry, PROPOSAL_TURN_PREFIX)):
            return False
        template = proposal["authority_template"]
        entry["receipts"] = list(entry["receipts"]) + [workflow_receipt(
            PROPOSAL_TURN_PREFIX, digest,
            "delivery proposal: candidate=%s base=%s source=%s expires_at=%s"
            % (template["candidate"]["identity_digest_sha256"],
               template["original_baseline"]["commit_sha"],
               template["source"]["ref"], proposal["expires_at"]),
            self._clock())]
        persist()
        return True

    # -- binding and minting ----------------------------------------------------

    def _binding(self, entry):
        """The bound decision (the binding document the workflow's binding
        receipt names), or None. Two DIFFERENT bindings refuse."""
        found = None
        for receipt in workflow_receipts(entry, BINDING_TURN_PREFIX):
            try:
                document = artifacts.load_document(
                    self.artifact_directory, receipt["digest"],
                    artifacts.BINDING_SCHEMA, artifacts.BINDING_KEYS)
            except artifacts.ArtifactError as exc:
                raise _Stop(STATUS_BLOCKED, exc.problem, str(exc))
            if found is not None and found != document:
                raise _Stop(STATUS_BLOCKED, PROBLEM_BINDING_CONFLICT,
                            "the workflow binds two different delivery decisions")
            found = document
        return found

    def _bind(self, entry, persist, evidence, document, proposal_digest):
        """Persist the one-decision -> one-delivery binding BEFORE the
        delivery record exists (the deterministic id makes every retry
        the same record)."""
        linkage = entry[workflow_record.MISSION_AUTHORITY_KEY]
        decision_digest = evidence["content_digest_sha256"]
        binding = artifacts.binding_document(
            linkage["mission_id"], linkage["revision"], document["decision_id"],
            decision_digest, evidence["evidence_id"], proposal_digest,
            delivery_id_for(document["decision_id"], proposal_digest))
        digest = artifacts.store_document(self.artifact_directory, binding)
        entry["receipts"] = list(entry["receipts"]) + [workflow_receipt(
            BINDING_TURN_PREFIX, digest,
            "delivery binding: decision=%s evidence=%s proposal=%s delivery=%s"
            % (binding["decision_id"], binding["evidence_id"], proposal_digest,
               binding["delivery_id"]), self._clock())]
        persist()
        return binding

    def _authority(self, binding, proposal, document, evidence):
        confirm = proposal["authority_template"]["candidate"][
            "identity_digest_sha256"][:DELIVERY_CONFIRM_CHARS]
        provenance = evidence["acceptance"]["provenance"]
        authority = dict(proposal["authority_template"])
        authority["human_authorization"] = {
            "identity": "%s client confirmation (principal %s)" % (
                provenance.get("transport"), provenance.get("principal_ref")),
            "source": delivery_authorization.AUTHORIZATION_SOURCE_CLIENT_CONFIRMATION,
            "authorized_at": document["confirmed_at"],
            "confirmation_digest_sha256": text_digest(confirm),
            "client_confirmation": {
                "decision_id": binding["decision_id"],
                "decision_document_digest_sha256":
                    binding["decision_document_digest_sha256"],
                "proposal_digest_sha256": binding["proposal_digest_sha256"],
                "mission_id": binding["mission_id"],
                "mission_revision": binding["revision"],
                "evidence_id": binding["evidence_id"],
            },
        }
        authority["expiration"] = {
            "policy": delivery_authorization.EXPIRATION_POLICY_ABSOLUTE,
            "expires_at": proposal["expires_at"],
        }
        return authority

    def _minted(self, binding):
        """The delivery record of ``binding`` — minting it when absent,
        from the BOUND proposal and decision (never re-derived)."""
        record = _read_delivery(self.delivery_store_directory, binding["delivery_id"])
        if record is not None:
            return record
        try:
            proposal = artifacts.load_document(
                self.artifact_directory, binding["proposal_digest_sha256"],
                artifacts.PROPOSAL_SCHEMA, artifacts.PROPOSAL_KEYS)
            document = artifacts.load_document(
                self.artifact_directory, binding["decision_document_digest_sha256"],
                artifacts.DECISION_SCHEMA, artifacts.DECISION_KEYS)
        except artifacts.ArtifactError as exc:
            raise _Stop(STATUS_BLOCKED, exc.problem, str(exc))
        state = self.service.get_state(binding["mission_id"])
        evidence = None
        for item in _evidence_of(state["record"], state["contract"]["activation_id"],
                                 REQUIREMENT_DECISION):
            if item["evidence_id"] == binding["evidence_id"] and item["acceptance"]:
                evidence = item
        if evidence is None:
            raise _Stop(STATUS_BLOCKED, PROBLEM_DECISION_MISMATCH,
                        "the bound decision evidence %s is no longer accepted"
                        % binding["evidence_id"])
        now = self._clock()
        if proposal["expires_at"] <= now:
            raise _Stop(STATUS_BLOCKED, PROBLEM_PROPOSAL_EXPIRED,
                        "the bound proposal expired before minting; nothing is"
                        " minted")
        refusal = delivery_store_refusal(self.delivery_store_directory)
        if refusal is not None:
            raise _Stop(STATUS_HELD, refusal[0], refusal[1])
        try:
            record, _inserted = self._mint(
                self.delivery_store_directory, binding["delivery_id"],
                self._authority(binding, proposal, document, evidence), now)
        except Exception as exc:                          # noqa: BLE001
            raise _Stop(STATUS_BLOCKED, PROBLEM_MINT_REFUSED,
                        "minting refused: %s: %s" % (type(exc).__name__, exc))
        return record

    # -- the effect gate -------------------------------------------------------

    def effect_gate(self, entry, binding):
        """The gate the machine consults before every effect of the bound
        record: ``gate(record, step, effect) -> (ok, problem, detail,
        terminal)``."""
        def gate(record, step, effect):
            try:
                return self._effect_admission(entry, binding, record)
            except mission_store.MissionStoreError as exc:
                return False, PROBLEM_SOURCE, str(exc), False
            except Exception as exc:                      # noqa: BLE001
                return False, PROBLEM_SOURCE, "%s: %s" % (type(exc).__name__, exc), False
        return gate

    def _effect_admission(self, entry, binding, record):
        confirmation = record["human_authorization"].get("client_confirmation") or {}
        if confirmation.get("decision_id") != binding["decision_id"] or (
            confirmation.get("proposal_digest_sha256")
            != binding["proposal_digest_sha256"]
            or record["delivery_id"] != binding["delivery_id"]
        ):
            return (False, PROBLEM_BINDING_CONFLICT,
                    "the delivery record is not the one bound to the decision", True)
        parent = delivery_parent.parent_mission_authority(record, self.service)
        if not parent["valid"] and source_unavailable_problem(parent["problem"]):
            # Task 8 S-VII: an unreadable or saturated Mission store is a
            # reversible, source-unavailable HOLD — never a terminal refusal,
            # so it never revokes the delivery record.
            return (False, PROBLEM_SOURCE,
                    "the Mission store could not be read for the parent check (%s):"
                    " %s" % (parent["problem"], parent["detail"]), False)
        if not parent["valid"]:
            return False, parent["problem"], parent["detail"], True
        admission = self.gate.admit(entry, mission_gate.BOUNDARY_DELIVERY_EFFECT)
        if not admission.ok:
            return (False, admission.problem, admission.detail,
                    admission.classification == mission_gate.CLASS_TERMINAL)
        if not workflow_record.retention_protects(entry, self._clock()):
            return (False, PROBLEM_RETENTION_RELEASED,
                    "the delivery candidate's retention is released", True)
        state = self.service.get_state(binding["mission_id"])
        live = [e for e in _evidence_of(state["record"],
                                        state["contract"]["activation_id"],
                                        REQUIREMENT_DECISION)
                if e["evidence_id"] == binding["evidence_id"] and e["acceptance"]]
        if not live:
            return (False, PROBLEM_DECISION_MISMATCH,
                    "the bound delivery decision is no longer accepted evidence",
                    True)
        return True, None, None, False

    # -- drive, attest, complete ----------------------------------------------------

    def _deliver(self, entry, persist, binding, changed=False):
        mission_id = binding["mission_id"]
        record = self._minted(binding)
        delivery_id = binding["delivery_id"]
        refusal = None
        retried = None
        if record["phase"] not in delivery_authorization.TERMINAL_PHASES:
            self.machine.effect_gate = self.effect_gate(entry, binding)
            try:
                for _ in range(MAX_DELIVERY_STEPS_PER_PASS):
                    outcome = self.machine.advance_once(delivery_id)
                    if outcome == delivery_machine.OUTCOME_HELD:
                        refusal = self.machine.last_refusal
                        if refusal[2]:
                            self.machine.revoke(delivery_id, "mission-effect-gate",
                                                refusal[0])
                        break
                    if (outcome == delivery_machine.OUTCOME_RETRY
                            and self.machine.last_retry is not None):
                        retried = self.machine.last_retry
                        step, detail = retried
                        refusal = (PROBLEM_TRANSPORT_FAILED, (
                            "step %s failed retryably (%s); nothing is assumed"
                            " absent — the next pass re-queries before any"
                            " effect" % (step, detail)), False)
                        break
                    if outcome != delivery_machine.OUTCOME_ADVANCED:
                        break
                    changed = True
            finally:
                self.machine.effect_gate = None
        record = _read_delivery(self.delivery_store_directory, delivery_id)
        if retried is not None:
            self._record_held(entry, persist, record, retried[0], retried[1])
        attested = self._attest(mission_id, record)
        fields = {"delivery_id": delivery_id, "changed": changed or bool(attested),
                  "attested": attested,
                  "proposal_digest_sha256": binding["proposal_digest_sha256"]}
        if refusal is not None:
            return _result(STATUS_BLOCKED if refusal[2] else STATUS_HELD,
                           refusal[0], refusal[1], **fields)
        if record["phase"] != delivery_authorization.PHASE_COMPLETE:
            if record["phase"] in delivery_authorization.TERMINAL_PHASES:
                blocker = record["blocker"] or {}
                return _result(STATUS_BLOCKED, blocker.get("problem") or record["phase"],
                               blocker.get("detail"), **fields)
            return _result(STATUS_DELIVERING, **fields)
        return self._complete(entry, persist, record, fields)

    def _record_held(self, entry, persist, record, step, detail):
        """Task 8 S-VII (Lead disposition L1): make a retryable transport
        failure that NO P1-A6 receipt carries (it happened before the step's
        receipt) durable on the workflow record the Runtime owns — one
        ``dheld-`` receipt per (delivery, recorded state, problem, step),
        never repeated for the same fact — so the read-only delivery status
        names it across passes, fresh objects and reconnects. A failure the
        step's own receipt carries (``failed_retryable``) is already durable
        in the P1-A6 record and projected from there; nothing is written
        for it here. No delivery-record field is added."""
        receipt = record["steps"][step]["receipt"]
        if receipt is not None and receipt["state"] == (
            delivery_authorization.RECEIPT_FAILED_RETRYABLE
        ):
            return
        state = delivery_fingerprint(record)
        digest = artifacts.held_digest(record["delivery_id"], state,
                                       PROBLEM_TRANSPORT_FAILED, step)
        if any(held["digest"] == digest
               for held in workflow_receipts(entry, artifacts.HELD_TURN_PREFIX)):
            return
        entry["receipts"] = list(entry["receipts"]) + [workflow_receipt(
            artifacts.HELD_TURN_PREFIX, digest,
            artifacts.held_summary(record["delivery_id"], state,
                                   PROBLEM_TRANSPORT_FAILED, step, detail),
            self._clock())]
        persist()

    def _attest(self, mission_id, record):
        """Attest every stored receipt once per receipt/step state; returns
        how many were attested now. A refusal is left unattested and
        reported by the status read, never retried blindly within the
        pass."""
        attested = 0
        for step in delivery_authorization.STEPS:
            receipt = record["steps"][step]["receipt"]
            if receipt is None:
                continue
            state = self.service.get_state(mission_id)
            if _attested_as(state["record"], receipt, record["steps"][step]["state"]):
                continue
            try:
                result = self._write(mission_id, lambda op, seq: (
                    delivery_parent.attest_validated_receipt(
                        record, step, self.service, op, seq, self.context)))
            except _Stop:
                continue
            except delivery_authorization.AuthorizationError:
                continue
            if result["valid"]:
                attested += 1
        return attested

    def _complete(self, entry, persist, record, fields):
        mission_id = record["mission"]["workflow_id"]
        step = delivery_authorization.STEP_PR_CREATE
        receipt = record["steps"][step]["receipt"]
        state = self.service.get_state(mission_id)
        artifact = _attested_as(state["record"], receipt, record["steps"][step]["state"])
        if artifact is None:
            return _result(STATUS_DELIVERED, PROBLEM_NOT_READY,
                           "the PR_CREATE receipt is not attested yet", **fields)
        if state["progress"] not in mission_state.TERMINAL_PROGRESS_STATES:
            self._ensure_evidence(mission_id, REQUIREMENT_DELIVERED,
                                  receipt["receipt_digest_sha256"],
                                  artifact["artifact_id"])
        retention = entry.get(workflow_record.RETENTION_KEY)
        if isinstance(retention, dict) and retention["released_at"] is None:
            retention["released_at"] = self._clock()
            retention["release_reason"] = workflow_record.RETENTION_RELEASE_PR_CREATED
            persist()
            fields["changed"] = True
        state = self.service.get_state(mission_id)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return _result(STATUS_COMPLETED if state["progress"]
                           == mission_state.PROGRESS_COMPLETED else STATUS_DELIVERED,
                           None, "mission %s is %s" % (mission_id, state["progress"]),
                           **fields)
        eligibility = state["closure_eligibility"] or {}
        if not eligibility.get("eligible"):
            failures = eligibility.get("failures") or []
            return _result(STATUS_DELIVERED, failures[0]["problem"] if failures
                           else PROBLEM_NOT_READY,
                           "; ".join(f["detail"] for f in failures) or None, **fields)
        url = (record["pull_request"] or {}).get("url")
        self._write(mission_id, lambda op, seq: self.service.complete_successfully(
            mission_id, op, seq, "delivered through P1-A6: pull request %s" % url,
            self.context))
        fields["changed"] = True
        return _result(STATUS_COMPLETED, **fields)


def production_delivery(gate, workflow_store_directory, delivery_store_directory=None,
                        clock=None):
    """The Runtime's delivery driver (the dirun CLI's composition only):
    the real machine through ``pr_delivery.cli.build_machine`` — the ONE
    construction site of the real transport — its live read-only bindings
    and the client-confirmed minting. Imported lazily so the readers of
    this module never load the transport."""
    import time

    from pr_delivery import cli as delivery_cli
    clock = clock or time.time
    directory = delivery_store_directory or delivery_store.store_directory()
    machine = delivery_cli.build_machine(directory, clock=clock)

    def bindings(repo, remote, base):
        return delivery_cli.live_bindings(machine.transport, repo, remote, base)
    return MissionDelivery(gate, workflow_store_directory, directory, machine,
                           bindings, delivery_cli.authorize_client_confirmed, clock)


# -- the Grok desk: presentation, the client-confirmed decision, the status ----


def _line_list(text):
    return ["    " + line for line in (text or "").splitlines()] or ["    "]


def render_delivery_card(stored, proposal, digest, confirm_value):
    """The FULL delivery proposal as the human reads it before deciding:
    every value the authorization will bind, the Mission parent, the
    absolute expiry, and what each answer does. Nothing is truncated."""
    template = proposal["authority_template"]
    parent = proposal["mission"]
    evidence = template["evidence"]
    engineering_item = evidence["engineering_complete"]
    review = evidence["reviewer_approve"]
    verification = evidence["independent_verification"]
    remote = template["remote"]
    lines = [
        "DODGING INFINITY DELIVERY DECISION REQUEST",
        "mission id: %s" % parent["mission_id"],
        "revision: %d (Mission proposal digest sha256 %s)" % (
            parent["revision"], parent["proposal_digest_sha256"]),
        "mission authorization: %s (digest sha256 %s)" % (
            parent["authorization_id"], parent["authorization_digest_sha256"]),
        "delivery proposal digest sha256: %s" % digest,
        "workflow: %s, engineering task %s" % (proposal["workflow_id"],
                                                proposal["task_id"]),
        "repository: %s (git dir %s)" % (template["repository"]["realpath"],
                                        template["repository"]["git_dir_realpath"]),
        "github repository: %s" % template["repository"]["repository_url"],
        "remote %s: configured %s; fetches from %s; pushes to %s" % (
            remote["name"], remote["url_exact"], remote["url_fetch"],
            remote["url_push"]),
        "source branch: %s (%s)" % (template["source"]["branch"],
                                    template["source"]["ref"]),
        "target base: %s (%s)" % (template["target_base"]["branch"],
                                  template["target_base"]["ref"]),
        "original baseline: %s" % template["original_baseline"]["commit_sha"],
        "candidate identity sha256: %s (%d entries)" % (
            template["candidate"]["identity_digest_sha256"],
            template["candidate"]["entry_count"]),
    ]
    for item in template["candidate"]["entries"]:
        lines.append("  %s %s %s %s" % (item["status"], item["mode"], item["blob"],
                                        item["path"]))
    lines.extend([
        "engineering: task %s %s; accepted result sha256 %s recorded at %s" % (
            engineering_item["task_id"], engineering_item["status"],
            engineering_item["task_state_sha256"], engineering_item["recorded_at"]),
        "review: round %d %s, file %s sha256 %s" % (
            review["round"], review["decision"], review["review_file_name"],
            review["review_file_sha256"]),
        "verification: argv %s; exit %d; log sha256 %s (%d bytes); ran at %s,"
        " finished at %s; record sha256 %s" % (
            verification["command_argv"], verification["exit_status"],
            verification["log_sha256"], verification["log_bytes"],
            verification["ran_at"], verification["recorded_at"],
            proposal["verification_record_digest_sha256"]),
        "verification provenance: %s" % VERIFICATION_PROVENANCE,
        "reverification argv: %s" % (template["reverification"]["argv"],),
        "committer: %s <%s> (unsigned)" % (template["committer"]["name"],
                                           template["committer"]["email"]),
        "pull request title: %s" % template["pr_content"]["title"],
        "pull request objective:",
    ])
    lines.extend(_line_list(template["pr_content"]["objective"]))
    lines.append("pull request architecture notes:")
    lines.extend(_line_list(template["pr_content"]["architecture_notes"]))
    lines.extend([
        "allowed actions: %s - and nothing else: no merge, auto-merge, tag,"
        " release, deploy, publish or force push" % ", ".join(
            template["allowed_actions"]),
        "expires at (absolute, unix seconds): %s" % proposal["expires_at"],
        "ACCEPT records this decision as the Mission's delivery_decision"
        " evidence and authorizes exactly this delivery until the expiry;"
        " DECLINE records a sticky cancel request of the Mission; CANCEL"
        " records nothing.",
        "to accept, answer the form field with the first %d characters of the"
        " candidate identity digest: %s" % (DELIVERY_CONFIRM_CHARS, confirm_value),
    ])
    return "\n".join(lines)


def _mission_delivery_record(directory, mission_id):
    """The P1-A6 record bound to ``mission_id`` (active preferred), read
    through the delivery store's validated observer read, or None."""
    if directory is None:
        return None
    read = delivery_store.DeliveryStore(directory).read()
    if read.document is None:
        return None
    bound = [record for record in read.document["deliveries"].values()
             if isinstance(record["mission"], dict)
             and record["mission"]["workflow_id"] == mission_id]
    if not bound:
        return None
    active = [record for record in bound if delivery_store.is_active(record)]
    return (active or sorted(bound, key=lambda r: r["revision"]))[-1]


def preparation_state(entries, mission_id, revision):
    """The latest recorded source-branch preparation state of the
    Mission's workflow for ``revision`` (``{"workflow_id", "state",
    "record"}``), or None — read from the workflow records' receipts only
    (never from the lease: the status read runs no Git)."""
    found = None
    for entry in entries:
        linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
        if linkage.get("mission_id") != mission_id or linkage.get("revision") != revision:
            continue
        for receipt in workflow_receipts(entry, artifacts.ATTACH_TURN_PREFIX):
            states = artifacts.attach_states({"receipts": [receipt]}, receipt["digest"])
            found = {"workflow_id": entry["workflow_id"],
                     "state": states[0] if states else artifacts.ATTACH_UNRESOLVED,
                     "record": receipt["bounded_summary"]}
    return found


def delivery_status(service, mission_id, artifact_directory, delivery_store_directory,
                    now, workflow_entries=()):
    """The PURE delivery status read (``di_delivery_status``): the current
    proposal (with its verification's provenance), the source-branch
    preparation state, the delivery decision, the delivery record's
    authorization source, per-step receipts with their attestation, the PR
    URL, what is uncertain and the next action. Reads the Mission, the
    artifact store, the delivery store and the given workflow records;
    writes nothing, prepares nothing, reconciles nothing, runs no Git and
    waits on no model turn."""
    stored = service.get(mission_id)
    state = service.get_state(mission_id)
    controls = service.mission_controls(mission_id)
    record = state["record"]
    revision = current_revision(stored)["revision"]
    status = {
        "mission_id": mission_id,
        "revision": revision,
        "mission_state": stored["record"]["state"],
        "progress": state["progress"],
        "delivery_requested": delivery_requested(stored),
        "cancel_requested": bool(controls.get("cancel_requested")),
        "proposal": None,
        "preparation": preparation_state(workflow_entries, mission_id, revision),
        "decision": None, "delivery": None,
        "uncertainty": [], "next_action": None,
    }
    digest, proposal = mission_proposal(record, artifact_directory)
    if proposal is not None:
        currency = proposal_currency(
            proposal, stored, {"workflow_id": proposal["workflow_id"]}, now)
        template = proposal["authority_template"]
        verification = template["evidence"]["independent_verification"]
        status["proposal"] = {
            "digest_sha256": digest, "workflow_id": proposal["workflow_id"],
            "candidate_identity_digest_sha256":
                template["candidate"]["identity_digest_sha256"],
            "source_branch": template["source"]["branch"],
            "target_base_branch": template["target_base"]["branch"],
            "expires_at": proposal["expires_at"],
            "current": currency is None,
            "problem": None if currency is None else currency[0],
            "verification": {
                "record_digest_sha256": proposal["verification_record_digest_sha256"],
                "command_argv": list(verification["command_argv"]),
                "exit_status": verification["exit_status"],
                "provenance": VERIFICATION_PROVENANCE,
            },
        }
    activation = state["contract"]["activation_id"]
    decisions = _evidence_of(record, activation, REQUIREMENT_DECISION)
    if decisions:
        latest = decisions[-1]
        status["decision"] = {
            "evidence_id": latest["evidence_id"],
            "decision_id": latest["operation_id"],
            "document_digest_sha256": latest["content_digest_sha256"],
            "accepted": latest["acceptance"] is not None,
            "client_confirmed": latest["provenance"].get("principal_kind")
            == _CLIENT_PRINCIPAL,
        }
    delivery = _mission_delivery_record(delivery_store_directory, mission_id)
    if delivery is not None:
        steps = []
        for step in delivery_authorization.STEPS:
            entry = delivery["steps"][step]
            receipt = entry["receipt"]
            artifact = None if receipt is None else _attested_as(
                record, receipt, entry["state"])
            steps.append({
                "step": step, "state": entry["state"],
                "receipt_id": None if receipt is None else receipt["receipt_id"],
                "receipt_state": None if receipt is None else receipt["state"],
                "attested": artifact is not None,
                "artifact_id": None if artifact is None else artifact["artifact_id"],
            })
            if receipt is not None and artifact is None:
                status["uncertainty"].append(
                    "receipt %s of step %s (%s) is not attested in the Mission"
                    % (receipt["receipt_id"], step, receipt["state"]))
            if receipt is not None and receipt["state"] == (
                delivery_authorization.RECEIPT_FAILED_RETRYABLE
            ):
                status["uncertainty"].append(
                    "%s: step %s failed retryably (%s); nothing is assumed"
                    " absent — the next pass re-queries before any effect"
                    % (PROBLEM_TRANSPORT_FAILED, step,
                       (receipt["observed"] or {}).get("error")))
        projection = delivery_boundary.project_status(delivery, now)
        status["delivery"] = {
            "delivery_id": delivery["delivery_id"], "phase": delivery["phase"],
            "authorization_source": delivery["human_authorization"]["source"],
            "expires_at": delivery["expiration"]["expires_at"],
            "revoked": projection["authorization"]["revoked"],
            "blocker_problem": (delivery["blocker"] or {}).get("problem"),
            "pr_url": (delivery["pull_request"] or {}).get("url"),
            "steps": steps,
        }
        # Task 8 S-VII (L1): a retryable transport failure the Runtime
        # recorded before any receipt, named while the delivery is still at
        # exactly the recorded state (any progress retires it).
        held = latest_held(workflow_entries, mission_id, revision)
        if held is not None and held["delivery"] == delivery["delivery_id"] and (
            held["state"] == delivery_fingerprint(delivery)
        ):
            status["uncertainty"].append(
                "%s: step %s, before any receipt: %s (recorded by the Runtime at"
                " %s on workflow %s)" % (held["problem"], held["step"],
                                          held["detail"], held["recorded_at"],
                                          held["workflow_id"]))
    status["next_action"] = _next_action(status)
    return status


def _next_action(status):
    delivery = status["delivery"]
    if status["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
        return "none: the Mission is %s" % status["progress"]
    if not status["delivery_requested"]:
        return "none: the Mission requests no github_pr delivery"
    if status["cancel_requested"]:
        return ("none: a cancel is requested; the Runtime stops what was started"
                " and a control principal confirms the cancel")
    if delivery is not None:
        if delivery["phase"] == delivery_authorization.PHASE_COMPLETE:
            return "the Runtime attests the receipts and closes the Mission"
        if delivery["phase"] in delivery_authorization.TERMINAL_PHASES:
            return "none: the delivery is %s (%s)" % (delivery["phase"],
                                                      delivery["blocker_problem"])
        return "the Runtime drives the delivery on its next pass"
    preparation = status["preparation"]
    if preparation is not None and preparation["state"] == artifacts.ATTACH_UNRESOLVED:
        return ("none until an EDIT: the source-branch preparation is unresolved;"
                " nothing advances and nothing is retried")
    if status["decision"] is not None and status["decision"]["accepted"]:
        proposal = status["proposal"]
        if proposal is None or not proposal["current"]:
            return ("none until an EDIT: the decided proposal is %s; it is never"
                    " minted and never re-prepared, and an EDIT starts a new"
                    " revision whose new proposal needs a new decision"
                    % ("absent" if proposal is None else proposal["problem"]))
        return "the Runtime binds and mints the decided delivery on its next pass"
    if status["proposal"] is not None and status["proposal"]["current"]:
        return "decide the proposal with di_delivery_decide"
    return "the Runtime verifies the candidate and prepares a proposal"


RESULT_KEYS = ("ok", "problem", "detail", "recorded", "evidence_id",
               "decision_document_digest_sha256", "accepted", "cancel_requested")


def _desk_result(ok, problem=None, detail=None, **fields):
    result = dict((key, None) for key in RESULT_KEYS)
    result.update(ok=ok, problem=problem, detail=detail)
    result.update(fields)
    return result


class DeliveryDesk(object):
    """What the Grok surface is handed (``grokmcp``'s CLI wires it when the
    Mission and workflow stores are configured): the FULL proposal card,
    the client-confirmed decision record and the pure status read. The
    desk decides nothing on the human's behalf: ``accept`` and
    ``decline`` are called only with a client form answer the relay
    admitted, under the CLIENT-CONFIRMATION context the server built after
    its bearer check."""

    def __init__(self, service, workflow_store_directory,
                 delivery_store_directory=None):
        from workflow_authority import store as workflow_store
        self.service = service
        self._workflow_store = workflow_store.WorkflowStore(workflow_store_directory)
        self.artifact_directory = artifacts.artifact_directory(
            workflow_store_directory)
        self.delivery_store_directory = (delivery_store_directory
                                         or delivery_store.store_directory())

    def _workflow(self, workflow_id):
        """The workflow record, read lock-free, or None when the store
        genuinely holds none. Task 8 S-VII: a store that cannot be read
        raises ``_WorkflowStoreUnavailable`` — never an absent record."""
        read = self._workflow_store.read()
        if read.availability == READ_UNAVAILABLE:
            raise _WorkflowStoreUnavailable(read.problem)
        if read.availability != READ_PRESENT:
            return None
        return read.document["workflows"].get(workflow_id)

    def _presentable(self, proposal, digest):
        """Why the proposal may not be presented, or None: its workflow
        receipt must exist (the proposal is persisted in BOTH places before
        presentation) and the workflow's latest exact candidate observation
        must still be the candidate it binds."""
        try:
            entry = self._workflow(proposal["workflow_id"])
        except _WorkflowStoreUnavailable as exc:
            return PROBLEM_SOURCE, (
                "the workflow store could not be read (%s); the proposal is not"
                " presented" % exc)
        if entry is None or not any(
            receipt["digest"] == digest
            for receipt in workflow_receipts(entry, PROPOSAL_TURN_PREFIX)
        ):
            return PROBLEM_PROPOSAL_ABSENT, (
                "no delivery proposal is prepared (the workflow receipt of %s"
                " is absent)" % digest)
        observation = exact_candidate(entry)
        bound = proposal["authority_template"]["candidate"]["identity_digest_sha256"]
        if observation is None or observation["identity"] != bound:
            return PROBLEM_PROPOSAL_STALE, (
                "the workflow's latest candidate observation is not the"
                " candidate %s the proposal binds" % bound)
        return None

    def card(self, mission_id, revision):
        """PURE read: ``{"ok", "problem", "detail", "card", "confirm_value",
        "binding"}`` for the current proposal of ``revision``."""
        from mission import service as mission_service
        stored = self.service.get(mission_id)
        state = self.service.get_state(mission_id)
        entry = current_revision(stored)
        refused = {"ok": False, "card": None, "confirm_value": None, "binding": None}
        if revision != entry["revision"]:
            return dict(refused, problem=mission_service.PROBLEM_STALE_REVISION,
                        detail="decision names revision %d but the mission is at"
                               " revision %d" % (revision, entry["revision"]))
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return dict(refused, problem=mission_gate.PROBLEM_MISSION_TERMINAL,
                        detail="mission %s is %s" % (mission_id, state["progress"]))
        if self.service.mission_controls(mission_id).get("cancel_requested"):
            return dict(refused, problem=mission_gate.PROBLEM_CANCEL_REQUESTED,
                        detail="mission %s has a cancel request" % mission_id)
        digest, proposal = mission_proposal(state["record"], self.artifact_directory)
        if proposal is None:
            return dict(refused, problem=PROBLEM_PROPOSAL_ABSENT,
                        detail="no delivery proposal is prepared for mission %s"
                               % mission_id)
        unpresentable = self._presentable(proposal, digest)
        if unpresentable is not None:
            return dict(refused, problem=unpresentable[0], detail=unpresentable[1])
        currency = proposal_currency(
            proposal, stored, {"workflow_id": proposal["workflow_id"]},
            self.service.now())
        if currency is not None:
            return dict(refused, problem=currency[0], detail=currency[1])
        activation = state["contract"]["activation_id"]
        decided = [e for e in _evidence_of(state["record"], activation,
                                           REQUIREMENT_DECISION)
                   if e["acceptance"] is not None]
        if decided:
            return dict(refused, problem=PROBLEM_ALREADY_DECIDED,
                        detail="delivery decision %s is already accepted"
                               % decided[0]["evidence_id"])
        identity = proposal["authority_template"]["candidate"]["identity_digest_sha256"]
        confirm = identity[:DELIVERY_CONFIRM_CHARS]
        artifact = latest_mission_artifact(state["record"], ARTIFACT_KEY_PROPOSAL)
        return {"ok": True, "problem": None, "detail": None,
                "card": render_delivery_card(stored, proposal, digest, confirm),
                "confirm_value": confirm,
                "binding": {"mission_id": mission_id, "revision": revision,
                            "proposal_digest_sha256": digest,
                            "proposal_artifact_id": artifact["artifact_id"],
                            "candidate_identity_digest_sha256": identity,
                            "activation_id": activation,
                            "expires_at": proposal["expires_at"]}}

    def _recheck(self, binding):
        """Why the card's binding is no longer current (nothing recorded),
        or None."""
        again = self.card(binding["mission_id"], binding["revision"])
        if not again["ok"]:
            return again["problem"], again["detail"]
        now = dict((key, again["binding"][key]) for key in binding)
        if now != binding:
            return PROBLEM_PROPOSAL_STALE, ("the proposal changed between the card"
                                            " and the answer; nothing recorded")
        return None

    def _readback(self, mission_id, decision_id):
        state = self.service.get_state(mission_id)
        for evidence in (state["record"] or {}).get("evidence") or []:
            if evidence["operation_id"] == decision_id:
                return evidence
        return None

    def accept(self, binding, decision_id, context):
        """Record an ACCEPT: store the canonical decision document, SUBMIT
        it as ``delivery_decision`` evidence consuming the reserved
        ``decision_id``, then ACCEPT it — both under ``context`` (client
        confirmation). What is recorded is proven by readback when a call
        raises; nothing is re-applied."""
        stale = self._recheck(binding)
        if stale is not None:
            return _desk_result(False, stale[0], stale[1], recorded=False)
        mission_id = binding["mission_id"]
        document = artifacts.decision_document(
            mission_id, binding["revision"], binding["proposal_digest_sha256"],
            binding["candidate_identity_digest_sha256"], decision_id,
            self.service.now(), artifacts.DECISION_ACCEPT)
        document_digest = artifacts.store_document(self.artifact_directory, document)
        state = self.service.get_state(mission_id)
        requirement = _requirement(state, REQUIREMENT_DECISION)
        if requirement is None:
            return _desk_result(False, PROBLEM_REQUIREMENT_UNDECLARED,
                                "the active contract declares no delivery_decision"
                                " requirement", recorded=False)
        kind = _preferred_kind(REQUIREMENT_DECISION, requirement["evidence_kinds"])
        try:
            submitted = self.service.submit_evidence(
                mission_id, decision_id, state["sequence"], REQUIREMENT_DECISION,
                kind, document_digest, [binding["proposal_artifact_id"]], context)
            evidence_id = submitted["evidence_id"]
        except Exception as exc:                          # noqa: BLE001
            found = self._readback(mission_id, decision_id)
            if found is None:
                return _desk_result(False, getattr(exc, "problem", None),
                                    "submitting the decision raised %s; nothing is"
                                    " recorded" % type(exc).__name__, recorded=False,
                                    decision_document_digest_sha256=document_digest)
            evidence_id = found["evidence_id"]
        try:
            operation_id = self.service.mint_state_operation_id(context)
            self.service.accept_evidence(
                mission_id, operation_id, self.service.get_state(mission_id)["sequence"],
                evidence_id, document_digest, context)
        except Exception as exc:                          # noqa: BLE001
            found = self._readback(mission_id, decision_id)
            accepted = bool(found and found["acceptance"] is not None)
            return _desk_result(
                accepted, None if accepted else getattr(exc, "problem", None),
                "accepting the decision raised %s; readback shows it %s" % (
                    type(exc).__name__, "accepted" if accepted else "NOT accepted"),
                recorded=True, evidence_id=evidence_id, accepted=accepted,
                decision_document_digest_sha256=document_digest)
        return _desk_result(True, recorded=True, evidence_id=evidence_id,
                            accepted=True,
                            decision_document_digest_sha256=document_digest)

    def decline(self, binding, decision_id, context):
        """Record a DECLINE: the canonical S-V cancel request under
        ``context`` (sticky), its reason naming the stored decline document
        so the Runtime releases the candidate's retention (``declined``).
        The reserved ``decision_id`` is not consumed."""
        stale = self._recheck(binding)
        if stale is not None:
            return _desk_result(False, stale[0], stale[1], recorded=False)
        mission_id = binding["mission_id"]
        document = artifacts.decision_document(
            mission_id, binding["revision"], binding["proposal_digest_sha256"],
            binding["candidate_identity_digest_sha256"], decision_id,
            self.service.now(), artifacts.DECISION_DECLINE)
        document_digest = artifacts.store_document(self.artifact_directory, document)
        try:
            operation_id = self.service.mint_cancel_operation_id(
                mission_id, mission_state.OPERATION_REQUEST_CANCEL, context)
            self.service.request_cancel(
                mission_id, operation_id, self.service.get_state(mission_id)["sequence"],
                DECLINE_REASON_PREFIX + document_digest, context)
        except Exception as exc:                          # noqa: BLE001
            requested = bool(self.service.mission_controls(mission_id).get(
                "cancel_requested"))
            return _desk_result(requested, None if requested else getattr(
                exc, "problem", None), "the cancel request raised %s; readback"
                " shows the cancel %s" % (type(exc).__name__, "requested" if requested
                                          else "NOT requested"),
                recorded=requested, cancel_requested=requested,
                decision_document_digest_sha256=document_digest)
        return _desk_result(True, recorded=True, cancel_requested=True,
                            decision_document_digest_sha256=document_digest)

    def status(self, mission_id):
        # Task 8 S-VII: the workflow records through their owner's lock-free
        # ``read``: an unreadable store is reported as such — the preparation
        # state is ``unavailable`` and named in ``uncertainty`` — never as a
        # store with no records.
        read = self._workflow_store.read()
        unavailable = (read.problem or "unreadable"
                       if read.availability == READ_UNAVAILABLE else None)
        entries = (list(read.document["workflows"].values())
                   if read.availability == READ_PRESENT else [])
        status = delivery_status(self.service, mission_id, self.artifact_directory,
                                 self.delivery_store_directory, self.service.now(),
                                 entries)
        if unavailable is not None:
            detail = "the workflow store could not be read (%s)" % unavailable
            status["preparation"] = {"workflow_id": None,
                                     "state": PREPARATION_UNAVAILABLE,
                                     "record": detail}
            status["uncertainty"] = list(status["uncertainty"]) + [
                "%s: the source-branch preparation state is unknown" % detail]
        return status
