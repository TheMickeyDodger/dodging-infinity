"""The Runtime → Mission Core reconciliation bridge (Task 8, slice S-V,
ledger R2-11, corrected per R2-11-a and R2-11-b).

Every Runtime pass reports what the WORKFLOW RECORD durably holds about
a Mission-origin workflow — plus the P1-A6 delivery record bound to its
Mission, READ through the delivery package's own validated read — to
the Mission Core's ONE reconciliation operation
(``MissionService.reconcile``): task standing, review standing, the
candidate identity (baseline + every artifact digest held) and the
delivery standing, materialized as plain data, never from a live
listing, a live HEAD or an in-memory claim. The candidate observation
itself is the Runtime's own receipt (``TargetBroker._maintain_candidate``
/ the verification collection), recorded before the report.

INVENTORY — every relevant digest, its canonical owner and field, and its
stable report key (the Mission Core confirms or contradicts an artifact
it recorded under the SAME key; a key not held is omitted and the Core
records that subject UNOBSERVED — unconfirmed, never confirmed; an
omission of a field its owner holds is a defect):

- ``handoff`` — the digest-bound handoff the engine received. Owner:
  workflow record ``handoff.digest_sha256``.
- ``verified_result`` — the verification turn's accepted result. Owner:
  workflow record ``verified_result.digest``.
- ``review_round_<n>`` — every review round the target's review process
  produced, earlier rounds included. Owner: workflow record ``review
  round`` receipts (decision from the observer's canonical round
  listing, CONTENT digest of the round record read through the hardened
  review read; the receipt digest binds round, decision, file and
  content, so a tampered receipt is refused and never reported). The
  review standing is PROVEN only by the collector's own ``review listing``
  receipt — the latest complete listing's highest round, backed by a
  trusted round receipt of exactly that decision and content — and is
  PENDING otherwise (``observation_receipts.review_round_reading``; named
  in the pass result's ``review_status``).
- ``candidate`` — the P1-A6 candidate identity: the STAGED index of the
  observed repository against the observed base, digested by
  ``pr_delivery.candidate.identity_digest`` over ``parse_raw_z`` (the ONE
  identity authority; no competing digest). Owner: workflow record
  ``candidate identity`` receipt (latest). Reported ONLY when that
  observation is ``exact`` and internally consistent; ``not_exact``
  (working-tree changes outside the staged candidate), ``unavailable``
  (the P1-A6 refusal code, e.g. an empty candidate) and a TAMPERED
  receipt are never reported as the candidate — the key is omitted (the
  Core records it unconfirmed) and the pass result names the status and
  problem (``candidate_status``).
- ``observed_head`` — the observed HEAD commit, digested and labelled
  as HEAD (``observation_receipts.head_commit_digest``); never the
  candidate identity. Owner: the same observation receipt.
- ``baseline_receipt`` / ``head_receipt`` — the receipt digests of the
  P1-A6 step receipts that AUTHORIZE the observed base and HEAD. Owner:
  the P1-A6 delivery record (``steps.<STEP>.receipt``), validated through
  the delivery package's own validators (``pr_delivery.mission_parent
  .validated_receipt``: the unchanged ``validate_authorization`` and
  ``validate_receipt`` over a private copy) — read, never written, never
  fabricated. ``baseline_receipt``: a succeeded BASE_REFRESH receipt whose
  bound ``new_base_oid`` IS the observed base. ``head_receipt``: a
  succeeded COMMIT receipt whose recorded ``commit_oid`` IS the observed
  HEAD and whose bound candidate identity IS the observed exact identity;
  before any succeeded commit, a succeeded BASE_REFRESH whose
  ``new_base_oid`` IS the observed HEAD (the refresh moves the source
  ref). The Mission Core accepts a reported receipt digest only when it
  is ATTESTED there (``attest_delivery_receipt``, the Core's own
  non-authorizing operation) for that step, effect completed, under the
  report's revision — the bridge relates, the Core decides.
- ACCEPTED evidence references — owner: Mission Core (read through
  ``get_state``, never copied): the pass result lists every artifact key
  an accepted evidence record references and whether the Runtime holds a
  digest for it.

REVIEW STANDING derives from the review facts: the latest observed
round's decision (APPROVE / REJECT); PENDING while dispatched with no
round observed yet; NONE before dispatch. Never from the verified
result's presence.

BASELINE / CANDIDATE SEMANTICS (the rule; the Core enforces it in
``reconciliation.drift_findings``): the reported baseline is the base the
latest candidate observation was made against — the recorded authorized
baseline before a delivery exists, the P1-A6 delivery's CURRENT base
once one is bound (delivery-phase semantics) — digested as identity with
the authorized baseline's ref. Anchors are PER AUTHORIZED REVISION: a
human EDIT re-bases under a new revision and a fresh anchor (no drift);
a superseded revision's workflow record is NOT reported at all
(``mission_reconcile_revision_superseded``), so its facts are never
relabelled. WITHIN a revision the baseline and the HEAD move only by an
ATTESTED P1-A6 BASE_REFRESH / COMMIT receipt related to the observed
move; the same move without it drifts, and a Mission ``record_artifact``,
an EDIT or any flag written by a caller authorizes nothing. The candidate
identity is compared with the Mission's recorded ``candidate`` artifact:
an authorized commit keeps it (the committed tree against the base IS
the staged candidate), a commit or a staged change that alters it
contradicts it.

DISCIPLINE: reports are collected at the Mission's full current cursor
(one ``get_journal`` read) and presented with the exact sequence that
cursor names; a document that moved refuses and the bridge re-collects
(bounded); the operation id is the Runtime context's existing unconsumed
reservation before any mint (an unchanged pass consumes nothing and
leaks nothing, across restarts too). The bridge SUBMITS observations; it
accepts nothing and claims no verifier independence.

Refusals never raise out of the bridge: the Runtime records the typed
problem per workflow and the next pass tries again.
"""

from mission import authorization as mission_authorization
from mission import record as mission_record
from mission import reconciliation
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store
from mission_control import observation_receipts as receipts
from pr_delivery import authorization as delivery_authorization
from pr_delivery import mission_parent as delivery_parent
from pr_delivery import store as delivery_store
from workflow_authority import record as workflow_record
from workflow_authority.atomic import READ_ABSENT, READ_PRESENT
from workflow_authority.digest import json_digest

# The bounded number of collect-and-present attempts per pass: a document
# that keeps moving under the report is reported as such, never chased.
RECOLLECT_ATTEMPTS = 3

# Stable contract keys of the artifact digests the bridge can report.
REPORT_KEY_HANDOFF = "handoff"
REPORT_KEY_VERIFIED_RESULT = "verified_result"
REPORT_KEY_REVIEW_ROUND = "review_round_%d"
REPORT_KEY_CANDIDATE = "candidate"
REPORT_KEY_HEAD = reconciliation.OBSERVED_HEAD_KEY
REPORT_KEY_BASELINE_RECEIPT = reconciliation.BASELINE_RECEIPT_KEY
REPORT_KEY_HEAD_RECEIPT = reconciliation.HEAD_RECEIPT_KEY
REPORT_KEYS = (REPORT_KEY_HANDOFF, REPORT_KEY_VERIFIED_RESULT,
               REPORT_KEY_REVIEW_ROUND, REPORT_KEY_CANDIDATE, REPORT_KEY_HEAD,
               REPORT_KEY_BASELINE_RECEIPT, REPORT_KEY_HEAD_RECEIPT)

# The candidate standing named in every pass result (besides the
# observation receipt's own statuses).
CANDIDATE_UNOBSERVED = "unobserved"
CANDIDATE_TAMPERED = "tampered"
# The review standing named in every pass result (R16-1, R17-1).
REVIEW_PROVEN = "proven"
REVIEW_UNPROVEN = "unproven"
REVIEW_UNOBSERVED = "unobserved"
REVIEW_TAMPERED = "tampered"

_TASK_BY_PHASE = {
    workflow_record.PHASE_PLANNED: reconciliation.TASK_REPORT_NOT_STARTED,
    workflow_record.PHASE_AUTHORIZED: reconciliation.TASK_REPORT_NOT_STARTED,
    workflow_record.PHASE_WORKSPACE_READY: reconciliation.TASK_REPORT_NOT_STARTED,
    workflow_record.PHASE_PREPARED: reconciliation.TASK_REPORT_NOT_STARTED,
    workflow_record.PHASE_VALIDATED: reconciliation.TASK_REPORT_NOT_STARTED,
    workflow_record.PHASE_DISPATCHED: reconciliation.TASK_REPORT_ACTIVE,
    workflow_record.PHASE_VERIFIED: reconciliation.TASK_REPORT_COMPLETE,
    workflow_record.PHASE_COMPLETED: reconciliation.TASK_REPORT_COMPLETE,
    workflow_record.PHASE_BLOCKED: reconciliation.TASK_REPORT_BLOCKED,
    workflow_record.PHASE_NEEDS_REAUTHORIZATION: reconciliation.TASK_REPORT_BLOCKED,
}
_REVIEW_DECISIONS = {"APPROVE": reconciliation.REVIEW_REPORT_APPROVE,
                     "REJECT": reconciliation.REVIEW_REPORT_REJECT}

PROBLEM_NOT_MISSION_ORIGIN = "mission_reconcile_not_mission_origin"
PROBLEM_MOVED = reconciliation.PROBLEM_RECONCILIATION_MOVED
PROBLEM_SOURCE_UNAVAILABLE = "mission_reconcile_source_unavailable"
# The record binds a Mission the store does not know: nothing to report
# to (the gate refuses such a record terminally on its own).
PROBLEM_UNKNOWN_MISSION = "mission_reconcile_unknown_mission"
# The record binds a revision the Mission has left (an EDIT superseded
# it): its facts are that revision's and are NEVER written as an
# observation of the current one — no report, no relabel.
PROBLEM_REVISION_SUPERSEDED = "mission_reconcile_revision_superseded"
# The delivery store could not be read, or two ACTIVE delivery records
# name the same Mission: no delivery-phase fact is related (the
# candidate is observed and reported without any transition receipt).
PROBLEM_DELIVERY_UNAVAILABLE = "mission_reconcile_delivery_unavailable"
PROBLEM_DELIVERY_AMBIGUOUS = "mission_reconcile_delivery_ambiguous"


def mission_delivery(directory, mission_id):
    """The P1-A6 delivery record bound to ``mission_id`` — its ``mission``
    parent block names the Mission (``mission.workflow_id`` IS the Mission
    id: the parent seam requires the authorization's Mission to equal it)
    — read through the delivery store's own observer read
    (``DeliveryStore.read``: every record validated by the delivery
    package itself; no lock, no creation, no write). Returns
    ``{"availability", "problem", "record"}``: ``record`` is the ONE bound
    record — the active (non-terminal) one when several revisions name the
    Mission, else the latest revision — or None; ``problem`` names an
    unavailable store or two active records (ambiguous)."""
    result = {"availability": None, "problem": None, "record": None}
    if directory is None or mission_id is None:
        return result
    read = delivery_store.DeliveryStore(directory).read()
    result["availability"] = read.availability
    if read.availability == READ_ABSENT:
        return result
    if read.availability != READ_PRESENT:
        result["problem"] = "%s: %s" % (PROBLEM_DELIVERY_UNAVAILABLE, read.problem)
        return result
    bound = [record for record in read.document["deliveries"].values()
             if isinstance(record["mission"], dict)
             and record["mission"]["workflow_id"] == mission_id]
    if not bound:
        return result
    active = [record for record in bound if delivery_store.is_active(record)]
    if len(active) > 1:
        result["problem"] = "%s: %d active delivery records name mission %s" % (
            PROBLEM_DELIVERY_AMBIGUOUS, len(active), mission_id)
        return result
    result["record"] = active[0] if active else max(bound, key=lambda r: r["revision"])
    return result


def _validated_step(delivery, step, problems):
    """The P1-A6 validated receipt of ``step`` (None when the step holds
    none or its validation refuses — the refusal code is appended to
    ``problems``)."""
    try:
        return delivery_parent.validated_receipt(delivery, step)
    except delivery_authorization.AuthorizationError as exc:
        problems.append("%s: %s" % (step, exc.problem))
        return None


def transition_receipts(delivery, observation):
    """``(baseline_receipt, head_receipt, problems)``: the digests of the
    VALIDATED, succeeded P1-A6 step receipts that authorize the OBSERVED
    base and HEAD of ``observation`` (the latest consistent candidate
    observation), related as stated in the module inventory; None when
    no such receipt relates. ``problems`` names every step receipt whose
    validation refused (tampered or inconsistent delivery state)."""
    problems = []
    if delivery is None or observation is None or not observation["consistent"]:
        return None, None, problems
    refresh = _validated_step(delivery, mission_state.TRANSITION_STEP_BASE_REFRESH, problems)
    commit = _validated_step(delivery, mission_state.TRANSITION_STEP_COMMIT, problems)
    baseline_receipt = head_receipt = None
    committed = commit is not None and commit.succeeded
    if refresh is not None and refresh.succeeded:
        binding = refresh.record["steps"][refresh.step]["receipt"]["binding"]
        if binding["new_base_oid"] == observation["base"]:
            baseline_receipt = refresh.receipt_digest_sha256
        if not committed and binding["new_base_oid"] == observation["head"]:
            head_receipt = refresh.receipt_digest_sha256
    if committed:
        receipt = commit.record["steps"][commit.step]["receipt"]
        recorded = receipt["observed"] or {}
        if (observation["status"] == receipts.CANDIDATE_STATUS_EXACT
                and recorded.get("commit_oid") == observation["head"]
                and receipt["binding"]["candidate_identity_digest"] == observation["identity"]):
            head_receipt = commit.receipt_digest_sha256
    return baseline_receipt, head_receipt, problems


def baseline_identity_digest(entry):
    """The baseline the latest consistent candidate observation was made
    against (the authorized baseline before any observation), digested as
    identity with the authorized baseline's ref; None when the record
    carries no baseline."""
    baseline = entry.get("approved_baseline")
    if not isinstance(baseline, dict):
        return None
    observation = candidate_identity(entry)
    commit = (observation["base"] if observation is not None and observation["consistent"]
              else baseline.get("commit_sha"))
    return json_digest({"ref": baseline.get("ref"), "commit_sha": commit})


def review_rounds(entry):
    """``[(round, decision, file, content_digest)]`` the record holds: the
    TRUSTED rounds only (``observation_receipts.review_round_reading``); a
    tampered receipt is never reported and contributes no round."""
    return receipts.observed_review_rounds(entry)


def _review_observed_nothing(reading):
    """No review observation at all: no receipt of either review family,
    or only a trusted, complete listing of no round."""
    listing = reading["listing"]
    return (not reading["tampered"] and not reading["rounds"]
            and (listing is None or (listing["consistent"] and listing["complete"]
                                     and listing["latest"] is None)))


def review_status(entry):
    """What the pass says about the review receipts
    (``observation_receipts.review_round_reading``): ``proven`` (the standing
    is proven; any tampered positions are listed as resolved), ``tampered``
    with ``PROBLEM_REVIEW_RECEIPT_TAMPERED`` and the unresolved positions,
    ``unproven`` with ``PROBLEM_REVIEW_UNPROVEN`` and the gap, or
    ``unobserved``."""
    reading = receipts.review_round_reading(entry)
    status = {"status": None, "problem": None, "gap": reading["gap"],
              "unresolved_positions": reading["unresolved"],
              "tampered_positions": reading["tampered"]}
    if reading["proof"] is not None:
        status.update(status=REVIEW_PROVEN, gap=None)
    elif reading["unresolved"]:
        status.update(status=REVIEW_TAMPERED,
                      problem=receipts.PROBLEM_REVIEW_RECEIPT_TAMPERED)
    elif _review_observed_nothing(reading):
        status.update(status=REVIEW_UNOBSERVED)
    else:
        status.update(status=REVIEW_UNPROVEN, problem=receipts.PROBLEM_REVIEW_UNPROVEN)
    return status


def candidate_identity(entry):
    """The record's latest candidate observation (parsed), or None."""
    return receipts.observed_candidate(entry)


def candidate_status(entry):
    """What the pass says about the candidate: the latest observation's
    status and problem (``tampered`` for an inconsistent receipt,
    ``unobserved`` when none exists), with its base and HEAD."""
    observation = candidate_identity(entry)
    if observation is None:
        return {"status": CANDIDATE_UNOBSERVED, "problem": None, "base": None,
                "head": None}
    if not observation["consistent"]:
        return {"status": CANDIDATE_TAMPERED,
                "problem": receipts.PROBLEM_CANDIDATE_RECEIPT_TAMPERED,
                "base": None, "head": None}
    return {"status": observation["status"], "problem": observation["problem"],
            "base": observation["base"], "head": observation["head"]}


def review_report(entry):
    """The review standing: the PROVEN decision
    (``observation_receipts.review_round_reading`` rule 2 — the latest
    complete listing's highest round, backed by a trusted round receipt of
    exactly that decision and content; a decision that is not a closed
    token reports PENDING); PENDING whenever any review observation exists
    without that proof — a tampered receipt, an unread or unbacked highest
    round, an incomplete or tampered listing — whatever any stated number
    looks like (R16-1, R17-1); with no review observation at all, PENDING
    while dispatched and NONE otherwise. No earlier round's decision ever
    stands in for an unproven standing."""
    reading = receipts.review_round_reading(entry)
    if reading["proof"] is not None:
        return _REVIEW_DECISIONS.get(reading["proof"]["decision"],
                                     reconciliation.REVIEW_REPORT_PENDING)
    if not _review_observed_nothing(reading):
        return reconciliation.REVIEW_REPORT_PENDING
    if entry.get("phase") == workflow_record.PHASE_DISPATCHED:
        return reconciliation.REVIEW_REPORT_PENDING
    return reconciliation.REVIEW_REPORT_NONE


def held_digests(entry, delivery=None):
    """Every artifact digest held, by stable key (the inventory above):
    the workflow record's own, and the transition receipts of the bound
    P1-A6 ``delivery`` record related to the latest observation."""
    digests = {}
    handoff = entry.get("handoff")
    if isinstance(handoff, dict) and isinstance(handoff.get("digest_sha256"), str):
        digests[REPORT_KEY_HANDOFF] = handoff["digest_sha256"]
    verified = entry.get("verified_result")
    if isinstance(verified, dict) and isinstance(verified.get("digest"), str):
        digests[REPORT_KEY_VERIFIED_RESULT] = verified["digest"]
    for round_number, _decision, _name, digest in review_rounds(entry):
        if isinstance(digest, str):
            digests[REPORT_KEY_REVIEW_ROUND % round_number] = digest
    observation = candidate_identity(entry)
    if observation is not None and observation["consistent"]:
        if observation["head"] is not None:
            digests[REPORT_KEY_HEAD] = receipts.head_commit_digest(observation["head"])
        if observation["status"] == receipts.CANDIDATE_STATUS_EXACT:
            digests[REPORT_KEY_CANDIDATE] = observation["identity"]
        baseline_receipt, head_receipt, _ = transition_receipts(delivery, observation)
        if baseline_receipt is not None:
            digests[REPORT_KEY_BASELINE_RECEIPT] = baseline_receipt
        if head_receipt is not None:
            digests[REPORT_KEY_HEAD_RECEIPT] = head_receipt
    return digests


def candidate_report(entry, delivery=None):
    """The candidate identity held: the observed baseline digest and every
    artifact digest present (an absent digest is omitted, never
    invented)."""
    return {"baseline_digest_sha256": baseline_identity_digest(entry),
            "artifact_digests": held_digests(entry, delivery)}


def _attested_artifact(state_record, receipt_id, digest=None):
    """The latest ATTESTED receipt-reference artifact of ``receipt_id`` in
    the Mission state (with exactly ``digest`` when given), or None."""
    found = None
    for artifact in mission_state.attested_artifacts(
            state_record or {"artifacts": []}):
        if artifact["locator"] == receipt_id and (
            digest is None or artifact["content_digest_sha256"] == digest
        ):
            found = artifact
    return found


def delivery_report(entry, delivery=None, state_record=None):
    """The delivery standing (Task 8 S-VI; S-V reported ``unavailable``
    for every bound delivery):

    - ABSENT: no P1-A6 record is bound to the Mission and the workflow
      carries no delivery marker;
    - a bound record: its LATEST stored step receipt (PR_CREATE, PUSH,
      COMMIT, BASE_REFRESH), judged by the delivery layer's OWN validator
      (``mission_parent.validated_receipt`` over a private copy) and
      reported only against the reference the Mission ATTESTED for it:
      VALID when the validator accepts it and the attested reference
      carries its exact digest (structural validity — the attested
      receipt state says whether the effect completed), INVALID when the
      validator refuses a receipt the Mission attested earlier (bound to
      that attested reference);
    - otherwise ``unavailable``: an unattested receipt (the Runtime's
      delivery pass attests it) is never reported as a judgement, so the
      Core records the subject unobserved rather than an unbound claim."""
    if delivery is None:
        if entry.get("result_delivery") is None and entry.get(
            "delivery_authority"
        ) in (None, workflow_record.DELIVERY_AUTHORITY_NONE):
            return {"value": {"status": reconciliation.DELIVERY_REPORT_ABSENT,
                              "receipt_artifact_id": None, "locator": None,
                              "receipt_digest_sha256": None}}
        return {"unavailable": "the workflow carries a delivery marker but no"
                               " P1-A6 record is bound to its Mission"}
    for step in reversed(delivery_authorization.STEPS):
        receipt = delivery["steps"][step]["receipt"]
        if receipt is None:
            continue
        try:
            validated = delivery_parent.validated_receipt(delivery, step)
        except delivery_authorization.AuthorizationError as exc:
            artifact = _attested_artifact(state_record, receipt.get("receipt_id"))
            if artifact is None:
                return {"unavailable": "the latest %s receipt does not validate"
                                       " (%s) and was never attested" % (
                                           step, exc.problem)}
            return {"value": {"status": reconciliation.DELIVERY_REPORT_INVALID,
                              "receipt_artifact_id": artifact["artifact_id"],
                              "locator": artifact["locator"],
                              "receipt_digest_sha256":
                                  artifact["content_digest_sha256"]}}
        artifact = _attested_artifact(state_record, validated.receipt_id,
                                      validated.receipt_digest_sha256)
        if artifact is None:
            return {"unavailable": "the latest %s receipt %s is not attested in"
                                   " the Mission yet" % (step, validated.receipt_id)}
        return {"value": {"status": reconciliation.DELIVERY_REPORT_VALID,
                          "receipt_artifact_id": artifact["artifact_id"],
                          "locator": artifact["locator"],
                          "receipt_digest_sha256": artifact["content_digest_sha256"]}}
    return {"unavailable": "the bound delivery %s holds no step receipt yet"
                           % delivery["delivery_id"]}


def materialize_reports(entry, now, delivery=None, state_record=None):
    """The four reports, as plain data from the workflow record, the
    bound P1-A6 ``delivery`` record (read, never written) and the
    Mission's attested receipt references (``state_record``)."""
    task = _TASK_BY_PHASE.get(entry.get("phase"))
    reports = {}
    if task is not None:
        reports[reconciliation.SOURCE_TASK] = {"value": task, "observed_at": now}
    reports[reconciliation.SOURCE_REVIEW] = {
        "value": review_report(entry), "observed_at": now}
    reports[reconciliation.SOURCE_CANDIDATE] = {
        "value": candidate_report(entry, delivery), "observed_at": now}
    report = delivery_report(entry, delivery, state_record)
    if "unavailable" in report:
        reports[reconciliation.SOURCE_DELIVERY] = report
    else:
        reports[reconciliation.SOURCE_DELIVERY] = {
            "value": report["value"], "observed_at": now}
    return reports


def accepted_evidence_subjects(state_record, held):
    """The artifact keys ACCEPTED evidence references in the Mission Core
    (read, never copied) and whether the Runtime holds a digest for each:
    ``{key: held?}``."""
    subjects = {}
    if state_record is None:
        return subjects
    for evidence in state_record.get("evidence") or []:
        if not mission_state.is_accepted(evidence):
            continue
        for artifact_id in evidence["artifact_ids"]:
            artifact = mission_state.artifact_by_id(state_record, artifact_id)
            if artifact is None or artifact["key"] is None:
                continue
            subjects[artifact["key"]] = artifact["key"] in held
    return subjects


def _result(ok, problem=None, detail=None, **fields):
    result = {"ok": ok, "problem": problem, "detail": detail, "mission_id": None,
              "workflow_id": None, "operation_id": None, "changed": False,
              "position": None, "revision": None, "finding_count": None,
              "attempts": 0, "reported_keys": [], "accepted_subjects": {},
              "candidate_status": None, "review_status": None, "delivery_id": None,
              "delivery_problems": []}
    result.update(fields)
    return result


def reconcile_workflow(service, context, entry, now, attempts=RECOLLECT_ATTEMPTS,
                       delivery_directory=None):
    """One pass for one Mission-origin record: read the bound P1-A6
    delivery (``delivery_directory``, read-only), collect at the head,
    present with the exact sequence, re-collect on movement (bounded).
    Returns a closed result dict; never raises for a source refusal."""
    if not workflow_record.is_mission_core_kind(entry):
        return _result(False, PROBLEM_NOT_MISSION_ORIGIN,
                       "only a Mission-origin record is reconciled",
                       workflow_id=entry.get("workflow_id"))
    linkage = entry.get(workflow_record.MISSION_AUTHORITY_KEY) or {}
    mission_id = linkage.get("mission_id")
    workflow_id = entry.get("workflow_id")
    delivery = mission_delivery(delivery_directory, mission_id)
    record = delivery["record"]
    observation = candidate_identity(entry)
    _b, _h, step_problems = transition_receipts(record, observation)
    common = {"mission_id": mission_id, "workflow_id": workflow_id,
              "candidate_status": candidate_status(entry),
              "review_status": review_status(entry),
              "delivery_id": None if record is None else record["delivery_id"],
              "delivery_problems": ([delivery["problem"]] if delivery["problem"] else [])
              + step_problems}
    held = held_digests(entry, record)
    problem = detail = None
    made = 0
    for _ in range(attempts):
        made += 1
        try:
            journal_view = service.get_journal(mission_id)
            cursor = journal_view["cursor"]
            if cursor["revision"] != linkage.get("revision"):
                return _result(
                    False, PROBLEM_REVISION_SUPERSEDED,
                    "the workflow binds revision %s of mission %s, which is at"
                    " revision %s; a superseded record's facts are never"
                    " reported as an observation of the current revision"
                    % (linkage.get("revision"), mission_id, cursor["revision"]),
                    revision=cursor["revision"], attempts=made, **common)
            state_record = service.get_state(mission_id)["record"]
            subjects = accepted_evidence_subjects(state_record, held)
            # Task 8 S-VI: the delivery report binds the Mission's ATTESTED
            # receipt references, read in this same attempt.
            reports = materialize_reports(entry, now, record, state_record)
            operation_id = service.reserve_reconciliation_operation_id(context)
            outcome = service.reconcile(
                mission_id, operation_id, cursor["position"],
                {"cursor": cursor, "reports": reports}, context)
        except mission_store.MissionStoreError as exc:
            return _result(False, PROBLEM_SOURCE_UNAVAILABLE, str(exc),
                           attempts=made, **common)
        except mission_record.MissionError as exc:
            problem, detail = exc.problem, str(exc)
            if problem in (PROBLEM_MOVED, mission_state_service.PROBLEM_STALE_SEQUENCE):
                continue  # the document moved: re-collect at the new head
            if problem == mission_authorization.PROBLEM_UNKNOWN_MISSION:
                problem = PROBLEM_UNKNOWN_MISSION
            return _result(False, problem, detail, attempts=made, **common)
        return _result(True, operation_id=operation_id, changed=outcome["changed"],
                       position=cursor["position"], revision=cursor["revision"],
                       finding_count=len(outcome["findings"] or []),
                       attempts=made, reported_keys=sorted(held),
                       accepted_subjects=subjects, **common)
    return _result(False, problem, detail, attempts=made, **common)
