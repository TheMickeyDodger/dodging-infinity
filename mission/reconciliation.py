"""Deterministic, idempotent Mission Reconciliation (Task 7, Stage 2,
roadmap item 16): pure functions over one stored Mission State record
and a set of controlled source reports.

What reconciliation is. A reconciliation pass compares what the durable
record supports with what controlled sources report and records the
DIFFERENCE as findings: revision drift (the active contract binds a
revision the Mission has moved past), missing or contradicted proof,
active HARD blockers, candidate and baseline drift, task and review
changes, a reported completion the record does not verify, a delivery
receipt report bound to a recorded receipt and judged by the injected
validator, and sources that were unknown or unavailable. Findings are
DERIVED: every finding recomputes from the record as of the position it
was taken at, the contract bound there, the previous reconciliation
records, the stored sources and their provenance, and the pass's own
recorded time, and the persistence layer refuses a record whose findings
do not recompute to themselves (``mission_reconciliation_disagrees``),
exactly as it re-proves checkpoints and snapshots.

What it repairs, and only that. A meaningful pass is ONE ordinary state
operation (``mission.state.OPERATION_RECONCILE``) applied through the
same pipeline as every other mutation: it consumes a DI-minted reserved
operation id, takes ``expected_sequence`` and refuses a stale one, is
replay-idempotent on its invocation digest, appends exactly one ledger
entry, exactly one reconciliation record (the effect it binds), and
re-binds the derived snapshot at the new head under the Mission's
CURRENT revision, all in the one atomic save. That is the whole repair
surface: the reconciliation position, the derived findings and the
derived snapshot. It writes no evidence, artifact, blocker, dependency,
claim, checkpoint, continuation or closure; it changes no proof
requirement, budget, revision, Mission identity or human authorization
(nothing here reads or constructs an authorization, a decision, a
reservation or a human identity), and it performs no repository, PR,
delivery, messaging or approval action of any kind. A reported
completion is recorded as a FINDING, never as a closure; a delivery
report is recorded as a finding, never as an effect of this layer, and
a missing, invalid, ambiguous, unbound or stale receipt report causes
no effect and no retry because there is no effect path to reach.

Receipt reports and attested receipts, stated limit. A delivery report
names a recorded receipt-reference artifact by id, reference and the
receipt's content digest, and this package binds the report to that
artifact by those three; what a REPORT CANNOT do is establish that the
existing receipt validator (the delivery layer's, outside this package
and barred from import by the static pins) actually accepted the
receipt: the receipt content is never stored here, the digest is a
public hash any caller can compute, and no signing surface exists. So
``delivery_reported_valid`` / ``delivery_reported_invalid`` say exactly
that — the CALLER reports the judgement — and observation carries the
delivery fact with standing ``reported``, never ``verified``. What
closes the gap is the ATTESTED form (``mission.state``): the delivery
layer's one permitted calling function runs its existing validator and
the parent-authority check first and only then records the receipt
reference through the distinct ``attest_delivery_receipt`` operation,
which the persistence layer re-proves on every load. Only an attested
reference yields the positive record finding
``delivery_receipt_attested``, and only a VALID report bound to an
ATTESTED reference is reflected as ``delivery_reported_valid``; a VALID
report bound to an unattested reference is ``delivery_report_unattested``
— an unverified source claim, never a positive receipt finding. What the
attested form still does NOT claim: it is repository source and call-path
confinement (the static pins), not protection against arbitrary code in
the process, runtime monkey-patching or a rewrite of storage or source,
and not cryptographic authenticity. Structural validity is not success:
an attestation carries the receipt's verbatim state and the step's
verbatim state, and only the pinned succeeded receipt state under the
pinned succeeded step state is read as a completed effect — a pending,
failed or void receipt, or a succeeded receipt under a step that is not
succeeded, is attested as NOT completed.

Bound to the cursor actually observed. A pass collects its sources
against one head cursor (Mission, schema version, revision, position,
event, chain digest) and records exactly that cursor's revision and
position; the operations layer refuses to record sources against a
document that moved between collection and the locked write
(``mission_reconciliation_moved``), so a fact is never stamped with a
revision or position its sources did not see. A record is ``current``
only when it was taken after the head event AND under the Mission's
current revision; an EDIT alone makes it not current.

A finding's identity is semantic: its kind, subject and detail carry
no timestamp (a stale delivery report says "older than the bound at the
time of this pass", never the time itself); the pass's time lives on
the record as ``reconciled_at`` and in ``source_provenance``, both
stored, both excluded from the meaningful-change comparison.

Meaning, timestamps and time. The invocation arguments of a reconcile
operation are the normalized ``sources`` (per kind: standing and
reported value) together with their ``source_provenance`` (per kind:
the observed-at time the source itself reported), so both are bound by
the invocation digest. Whether a pass is MEANINGFUL, however, is
decided by the sources and the standing findings alone: a pass whose
sources and standing findings equal the latest recorded
reconciliation's is a no-op by definition and the service writes
NOTHING for it — no ledger entry, no reconciliation record, no snapshot
re-bind, no ``updated_at`` change — so a refreshed observed-at time
alone never produces an event. Time-bound derivations (proof freshness
against the approved evidence age, a delivery report's age against the
freshness bound) are evaluated at the pass's OWN recorded time,
``reconciled_at``, which is stored, so one pass converges: a later pass
at the same meaning is a no-op, a pass after accepted evidence has
crossed its approved age bound is exactly one event, and the derivation
is stable across reload because the record carries the time it used.

Uncertainty is preserved, never erased. Candidate and baseline drift
findings describe a contradiction between recorded artifacts and the
observed candidate; while no APPLICABLE candidate report is in hand (not
reported, older than the freshness bound at the pass's time, or
collected at a cursor the document has left) the previous pass's drift
findings are carried forward unresolved rather than dropped, and a
report that contradicts always counts even when it cannot resolve. Baseline drift is
measured against the FIRST baseline any reconciliation of the Mission
observed (the anchor), so it stands while the baseline stays moved and
resolves only when a reported baseline equals the anchor again. An
omission is not a confirmation (round 11): drift is judged over the
RECORDED artifacts, not the reported keys — and over EVERY recorded
artifact a key holds that matters (round 12): the latest, and each one
accepted evidence references, since a re-recorded key holds several and
the proof binds the referenced one, not the latest — so a report that carries no
digest for a recorded artifact that has one yields
``candidate_artifact_unobserved`` for that key, and a report that names
no baseline while an anchor exists yields ``baseline_unobserved``; both
are drift-class findings, carried until an applicable report names the
subject, and both withhold present verified success in observation
without asserting a contradiction. Only an APPLICABLE report creates
them: a stale or moved report is not a statement about the candidate
now, so its omissions neither confirm nor create (its contradictions
still count), exactly as no report. An empty ``artifact_digests`` is
accepted at the boundary (a complete report may be impossible under the
key bound, and completeness can only be judged against the loaded
record) and is read as observing nothing. A candidate source that is
not reported at all remains what it was: unavailable or unknown, with
previously detected drift preserved and nothing created.

Conservative on conflict. A stale ``expected_sequence`` refuses
(``mission_state_stale_sequence``) without mutation on the write path
AND on the no-op path; a document that moved during collection refuses
(``mission_reconciliation_moved``); a pass that turns out unchanged
inside the lock refuses (``mission_reconciliation_unchanged``) without
mutation; a terminal record refuses a write (``mission_state_terminal``);
an unavailable source is recorded as unavailable, never guessed from an
earlier value; a contradiction is recorded as a finding, never resolved
by choosing a side.

Effect-free by construction. This module imports only the pure Task 5
modules and the journal; it holds no lock, opens nothing, writes
nothing, mints nothing, and calls no adapter (the observation module
collects source reports; this module only reads their normalized form).
"""

from mission import journal
from mission import progress as progress_module
from mission import record
from mission import state as state_module

# -- controlled sources: closed kinds and closed report vocabularies -----

SOURCE_CANDIDATE = "candidate"
SOURCE_DELIVERY = "delivery"
SOURCE_REVIEW = "review"
SOURCE_TASK = "task"
SOURCE_KINDS = (SOURCE_CANDIDATE, SOURCE_DELIVERY, SOURCE_REVIEW, SOURCE_TASK)

# A source's standing: what kind of knowledge it yielded. ``verified`` is
# never a source standing; only the durable record is verified.
STANDING_REPORTED = "reported"
STANDING_UNKNOWN = "unknown"
STANDING_UNAVAILABLE = "unavailable"
SOURCE_STANDINGS = (STANDING_REPORTED, STANDING_UNAVAILABLE, STANDING_UNKNOWN)
SOURCE_KEYS = ("standing", "value")
SOURCE_PROVENANCE_KEYS = ("observed_at",)

TASK_REPORT_ACTIVE = "ACTIVE"
TASK_REPORT_BLOCKED = "BLOCKED"
TASK_REPORT_COMPLETE = "COMPLETE"
TASK_REPORT_FAILED = "FAILED"
TASK_REPORT_NOT_STARTED = "NOT_STARTED"
TASK_REPORTS = (TASK_REPORT_ACTIVE, TASK_REPORT_BLOCKED, TASK_REPORT_COMPLETE,
                TASK_REPORT_FAILED, TASK_REPORT_NOT_STARTED)
REVIEW_REPORT_APPROVE = "APPROVE"
REVIEW_REPORT_NONE = "NONE"
REVIEW_REPORT_PENDING = "PENDING"
REVIEW_REPORT_REJECT = "REJECT"
REVIEW_REPORTS = (REVIEW_REPORT_APPROVE, REVIEW_REPORT_NONE, REVIEW_REPORT_PENDING,
                  REVIEW_REPORT_REJECT)
DELIVERY_REPORT_ABSENT = "ABSENT"
DELIVERY_REPORT_AMBIGUOUS = "AMBIGUOUS"
DELIVERY_REPORT_INVALID = "INVALID"
DELIVERY_REPORT_VALID = "VALID"
DELIVERY_REPORTS = (DELIVERY_REPORT_ABSENT, DELIVERY_REPORT_AMBIGUOUS,
                    DELIVERY_REPORT_INVALID, DELIVERY_REPORT_VALID)
# A delivery report names the recorded receipt artifact it judged, the
# receipt reference it validated and the receipt's content digest as the
# existing receipt contract computes it; ABSENT names nothing.
DELIVERY_KEYS = ("status", "receipt_artifact_id", "locator", "receipt_digest_sha256")
CANDIDATE_KEYS = ("baseline_digest_sha256", "artifact_digests")

# -- findings -------------------------------------------------------------

FINDING_KEYS = ("kind", "subject", "detail")
FINDING_BASELINE_DRIFT = "baseline_drift"
# Round 11: a candidate report that names no baseline while the Mission's
# baseline anchor exists, or no digest for a recorded artifact that has
# one, has NOT confirmed that subject. Absence of a key is missing
# information, never confirmation: it is recorded as UNOBSERVED, carried
# until an applicable report names the subject, and it withholds
# verified success without asserting a contradiction.
FINDING_BASELINE_UNOBSERVED = "baseline_unobserved"
FINDING_CANDIDATE_DRIFT = "candidate_drift"
FINDING_CANDIDATE_UNOBSERVED = "candidate_artifact_unobserved"
FINDING_DELIVERY_ABSENT = "delivery_absent"
FINDING_DELIVERY_AMBIGUOUS = "delivery_ambiguous"
# Task 7, Stage 2 (the receipt criterion): the record holds a receipt
# reference in ATTESTED form (``mission.state``), recorded by the one
# operation kind the delivery layer's validating path may call; its
# detail states the attested step, the verbatim receipt state and
# whether that state is the one completed-effect state.
FINDING_DELIVERY_ATTESTED = "delivery_receipt_attested"
FINDING_DELIVERY_INVALID = "delivery_reported_invalid"
FINDING_DELIVERY_STALE = "delivery_report_stale"
# A VALID report bound to a recorded receipt reference the record never
# attested: an unverified source claim, reflected as exactly that and
# never as a positive receipt finding.
FINDING_DELIVERY_UNATTESTED = "delivery_report_unattested"
FINDING_DELIVERY_UNBOUND = "delivery_report_unbound"
FINDING_DELIVERY_VALID = "delivery_reported_valid"
FINDING_HARD_BLOCKER = "hard_blocker_active"
FINDING_PROOF_CONTRADICTED = "proof_contradicted"
FINDING_PROOF_MISSING = "proof_missing"
FINDING_REPORTED_COMPLETE_UNVERIFIED = "reported_complete_unverified"
FINDING_REVIEW_CHANGED = "review_changed"
FINDING_REVIEW_NOT_APPROVED = "review_not_approved"
FINDING_REVISION_DRIFT = "revision_drift"
FINDING_SOURCE_UNAVAILABLE = "source_unavailable"
FINDING_SOURCE_UNKNOWN = "source_unknown"
FINDING_TASK_CHANGED = "task_changed"
FINDING_KINDS = (
    FINDING_BASELINE_DRIFT, FINDING_BASELINE_UNOBSERVED, FINDING_CANDIDATE_DRIFT,
    FINDING_CANDIDATE_UNOBSERVED, FINDING_DELIVERY_ABSENT,
    FINDING_DELIVERY_AMBIGUOUS, FINDING_DELIVERY_ATTESTED, FINDING_DELIVERY_INVALID,
    FINDING_DELIVERY_STALE, FINDING_DELIVERY_UNATTESTED, FINDING_DELIVERY_UNBOUND,
    FINDING_DELIVERY_VALID, FINDING_HARD_BLOCKER,
    FINDING_PROOF_CONTRADICTED, FINDING_PROOF_MISSING,
    FINDING_REPORTED_COMPLETE_UNVERIFIED, FINDING_REVIEW_CHANGED,
    FINDING_REVIEW_NOT_APPROVED, FINDING_REVISION_DRIFT, FINDING_SOURCE_UNAVAILABLE,
    FINDING_SOURCE_UNKNOWN, FINDING_TASK_CHANGED,
)
# Findings that describe the step FROM the previous reconciliation: they
# exist only when the sources moved, so they never decide by themselves
# whether a pass is meaningful (a repeat with the same sources would
# otherwise drop them and count as a second change).
TRANSITION_FINDINGS = frozenset((FINDING_REVIEW_CHANGED, FINDING_TASK_CHANGED))
# Findings that describe unresolved uncertainty about the candidate: they
# are carried forward while the candidate source cannot resolve them.
DRIFT_FINDINGS = frozenset((FINDING_BASELINE_DRIFT, FINDING_BASELINE_UNOBSERVED,
                            FINDING_CANDIDATE_DRIFT, FINDING_CANDIDATE_UNOBSERVED))
_DELIVERY_FINDINGS = {
    DELIVERY_REPORT_ABSENT: FINDING_DELIVERY_ABSENT,
    DELIVERY_REPORT_AMBIGUOUS: FINDING_DELIVERY_AMBIGUOUS,
    DELIVERY_REPORT_INVALID: FINDING_DELIVERY_INVALID,
    DELIVERY_REPORT_VALID: FINDING_DELIVERY_VALID,
}

# -- the stored record ----------------------------------------------------

RECONCILIATION_KEYS = (
    "operation_id", "sequence", "reconciled_at", "provenance",
    "observed_position", "observed_journal_digest_sha256", "observed_revision",
    "sources", "source_provenance", "findings",
)

# -- hard bounds, never derived from input ------------------------------

MAX_RECONCILIATION_RECORDS = 256
MAX_RECONCILIATION_FINDINGS = 512
MAX_FINDING_DETAIL_CHARS = 500
MAX_OBSERVED_CANDIDATE_KEYS = 64
# A reported answer older than this at the time it is used is stale.
REPORTED_FRESHNESS_BOUND_SECONDS = 600

# -- problem codes: one distinct code per refusal -----------------------

PROBLEM_RECONCILIATION_MALFORMED = "mission_reconciliation_malformed"
PROBLEM_RECONCILIATION_BINDING = "mission_reconciliation_binding"
PROBLEM_RECONCILIATION_DISAGREES = "mission_reconciliation_disagrees"
PROBLEM_RECONCILIATION_UNCHANGED = "mission_reconciliation_unchanged"
PROBLEM_RECONCILIATION_MOVED = "mission_reconciliation_moved"
PROBLEM_RECONCILIATION_FULL = "mission_reconciliation_full"
PROBLEM_SOURCE_MALFORMED = "mission_reconciliation_source_malformed"


def _malformed(location, detail):
    record.fail(PROBLEM_RECONCILIATION_MALFORMED, "%s: %s" % (location, detail))


# -- sources ---------------------------------------------------------------


def validate_candidate_value(value, location):
    """A candidate report: the observed baseline identity (or None) and
    the observed content digest per artifact key, bounded."""
    record.require_dict(value, location)
    record.require_closed_keys(value, CANDIDATE_KEYS, location)
    if value["baseline_digest_sha256"] is not None:
        record.require_hex(value["baseline_digest_sha256"],
                           location + ".baseline_digest_sha256", 64)
    digests = value["artifact_digests"]
    record.require_dict(digests, location + ".artifact_digests")
    if len(digests) > MAX_OBSERVED_CANDIDATE_KEYS:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s.artifact_digests holds %d keys; the hard bound is %d"
                    % (location, len(digests), MAX_OBSERVED_CANDIDATE_KEYS))
    for key, digest in dict.items(digests):
        record.require_contract_key(key, location + ".artifact_digests key")
        record.require_hex(digest, "%s.artifact_digests[%r]" % (location, key), 64)
    return value


def validate_delivery_value(value, location):
    """A delivery report: the validator's judgement of ONE recorded
    receipt, named by the artifact id and the receipt reference it
    validated; ABSENT names nothing."""
    record.require_dict(value, location)
    record.require_closed_keys(value, DELIVERY_KEYS, location)
    record.require_member(value["status"], DELIVERY_REPORTS, location + ".status")
    if value["status"] == DELIVERY_REPORT_ABSENT:
        if value["receipt_artifact_id"] is not None or value["locator"] is not None or (
            value["receipt_digest_sha256"] is not None
        ):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s reports ABSENT and must name no receipt" % location)
        return value
    record.require_id(value["receipt_artifact_id"], record.ARTIFACT_ID_PREFIX,
                      location + ".receipt_artifact_id")
    record.require_str(value["locator"], location + ".locator",
                       state_module.MAX_LOCATOR_CHARS)
    record.require_hex(value["receipt_digest_sha256"],
                       location + ".receipt_digest_sha256", 64)
    return value


def validate_source_value(kind, value, location):
    """The reported value of one source kind, or None."""
    if value is None:
        return None
    if kind == SOURCE_TASK:
        return record.require_member(value, TASK_REPORTS, location)
    if kind == SOURCE_REVIEW:
        return record.require_member(value, REVIEW_REPORTS, location)
    if kind == SOURCE_DELIVERY:
        return validate_delivery_value(value, location)
    return validate_candidate_value(value, location)


def validate_sources(value, location):
    """The closed normalized sources: every kind present, a standing from
    the closed set, a value only when reported, never a time."""
    try:
        record.require_dict(value, location)
        record.require_closed_keys(value, SOURCE_KINDS, location)
        for kind in SOURCE_KINDS:
            sub = "%s.%s" % (location, kind)
            entry = value[kind]
            record.require_dict(entry, sub)
            record.require_closed_keys(entry, SOURCE_KEYS, sub)
            record.require_member(entry["standing"], SOURCE_STANDINGS,
                                  sub + ".standing")
            if entry["standing"] == STANDING_REPORTED:
                if entry["value"] is None:
                    record.fail(record.PROBLEM_BAD_VALUE,
                                "%s is reported but carries no value" % sub)
                validate_source_value(kind, entry["value"], sub + ".value")
            elif entry["value"] is not None:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "%s is %s and must carry no value"
                            % (sub, entry["standing"]))
    except record.MissionError as exc:
        record.fail(PROBLEM_SOURCE_MALFORMED, "%s (%s)" % (exc, exc.problem))
    return value


def validate_source_provenance(value, sources, location):
    """The closed per-source provenance: the observed-at time each
    source itself reported; required for a reported source, permitted
    for an unknown one, absent for an unavailable one."""
    try:
        record.require_dict(value, location)
        record.require_closed_keys(value, SOURCE_KINDS, location)
        for kind in SOURCE_KINDS:
            sub = "%s.%s" % (location, kind)
            entry = value[kind]
            record.require_dict(entry, sub)
            record.require_closed_keys(entry, SOURCE_PROVENANCE_KEYS, sub)
            observed_at = entry["observed_at"]
            standing = sources[kind]["standing"]
            if standing == STANDING_REPORTED:
                record.require_timestamp(observed_at, sub + ".observed_at")
            elif standing == STANDING_UNAVAILABLE:
                if observed_at is not None:
                    record.fail(record.PROBLEM_BAD_VALUE,
                                "%s is unavailable and carries no time" % sub)
            else:
                record.require_optional_timestamp(observed_at, sub + ".observed_at")
    except record.MissionError as exc:
        record.fail(PROBLEM_SOURCE_MALFORMED, "%s (%s)" % (exc, exc.problem))
    return value


def reported_value(sources, kind):
    """The value a source reported, or None unless its standing is
    ``reported``."""
    entry = sources[kind]
    if entry["standing"] != STANDING_REPORTED:
        return None
    return entry["value"]


def is_fresh(evaluated_at, observed_at, bound_seconds):
    """``0 <= evaluated_at - observed_at <= bound``; a future-dated or
    missing observation is never fresh."""
    if observed_at is None:
        return False
    age = evaluated_at - observed_at
    return 0 <= age <= bound_seconds


# -- findings -------------------------------------------------------------


def _finding(kind, subject, detail):
    return {"kind": kind, "subject": subject, "detail": detail}


def _latest_artifacts_by_key(state):
    latest = {}
    for artifact in state["artifacts"]:
        if artifact["key"] is not None:
            latest[artifact["key"]] = artifact
    return latest


def records_as_of(state, position):
    """The reconciliation records at or before ``position`` (a record
    written before the key existed holds none)."""
    return [r for r in dict.get(state, "reconciliations", [])
            if r["sequence"] <= position]


def latest_record(state, position=None):
    if position is None:
        position = state["sequence"]
    records = records_as_of(state, position)
    return records[-1] if records else None


def baseline_anchor(records):
    """The FIRST baseline any reconciliation in ``records`` observed, or
    None: the identity the Mission's candidate was first seen against."""
    for entry in records:
        candidate = reported_value(entry["sources"], SOURCE_CANDIDATE)
        if candidate is not None and candidate["baseline_digest_sha256"] is not None:
            return candidate["baseline_digest_sha256"]
    return None


def _evidence_referenced_by_key(as_of):
    """Every keyed, digested artifact that ACCEPTED evidence references,
    grouped by key (round 12): a key may hold several recorded artifacts,
    proof binds the one the evidence references, and that one may not be
    the latest under its key."""
    referenced = {}
    for evidence in as_of["evidence"]:
        if not state_module.is_accepted(evidence):
            continue
        for artifact_id in evidence["artifact_ids"]:
            artifact = state_module.artifact_by_id(as_of, artifact_id)
            if artifact is None or artifact["key"] is None or (
                artifact["content_digest_sha256"] is None
            ):
                continue
            group = referenced.setdefault(artifact["key"], {})
            group[artifact["artifact_id"]] = artifact
    return referenced


def candidate_drift(as_of, candidate, unobserved=True):
    """Over the RECORDED artifacts, never only the reported keys: for
    every key that has a recorded digest — on the latest artifact under
    the key OR on an artifact accepted evidence references (round 12: a
    re-recorded key holds several artifacts, and proof binds the one the
    evidence references, which need not be the latest) — the observed
    digest against each such recorded digest: one drift finding per key
    on any mismatch (a contradiction always counts), and, when
    ``unobserved`` (the report is APPLICABLE: fresh and collected at this
    head, so it is a statement about the candidate now), one UNOBSERVED
    finding per such key the report carries no digest for. A report that
    omits an artifact is never read as confirming it, and a report that
    confirms the latest artifact never confirms an older one the proof
    rests on; a stale or moved report neither confirms nor creates,
    exactly as no report."""
    findings = []
    latest = _latest_artifacts_by_key(as_of)
    referenced = _evidence_referenced_by_key(as_of)
    for key in sorted(set(latest) | set(referenced)):
        must_match = {}
        newest = dict.get(latest, key)
        if newest is not None and newest["content_digest_sha256"] is not None:
            must_match[newest["artifact_id"]] = newest
        must_match.update(dict.get(referenced, key, {}))
        if not must_match:
            continue
        if key not in candidate["artifact_digests"]:
            if not unobserved:
                continue
            findings.append(_finding(
                FINDING_CANDIDATE_UNOBSERVED, key,
                "the candidate report carries no digest for recorded artifact"
                " %r (%d recorded artifact(s) under the key, the latest holding"
                " %s); the artifact is unconfirmed, and an omission is not a"
                " confirmation"
                % (key, len(must_match),
                   newest["content_digest_sha256"] if newest is not None
                   else "no digest")))
            continue
        observed = candidate["artifact_digests"][key]
        disagreeing = [a for a in dict.values(must_match)
                       if a["content_digest_sha256"] != observed]
        if disagreeing:
            findings.append(_finding(
                FINDING_CANDIDATE_DRIFT, key,
                "the observed candidate carries %s for artifact %r, which"
                " disagrees with %d of the %d recorded artifact(s) under the"
                " key it must match (the latest, and every one accepted"
                " evidence references); the latest holds %s"
                % (observed, key, len(disagreeing), len(must_match),
                   newest["content_digest_sha256"] if newest is not None
                   else "no digest")))
    return findings


def baseline_drift(anchor, observed, unobserved=True):
    """A drift finding when the observed baseline is not the anchor; an
    UNOBSERVED finding when an anchor exists, the report is applicable
    (``unobserved``) and it names no baseline at all. Nothing before an
    anchor exists: there is no recorded identity to confirm or
    contradict."""
    if anchor is None:
        return []
    if observed is None:
        if not unobserved:
            return []
        return [_finding(FINDING_BASELINE_UNOBSERVED, None,
                         "the candidate was first observed against baseline %s but"
                         " the candidate report names no baseline; the baseline is"
                         " unconfirmed, and an omission is not a confirmation"
                         % anchor)]
    if observed == anchor:
        return []
    return [_finding(FINDING_BASELINE_DRIFT, None,
                     "the candidate was first observed against baseline %s but"
                     " the observed baseline is now %s" % (anchor, observed))]


def carried_drift(previous, candidate):
    """The previous pass's drift findings that the current candidate
    report cannot RESOLVE: all of them when no applicable report is in
    hand (``candidate`` None: not reported, stale, or collected at a
    cursor the document has left), the keys it does not report and the
    baseline it does not report otherwise. Uncertainty is preserved,
    never erased; only an applicable report that names the subject can
    resolve it."""
    if previous is None:
        return []
    carried = []
    for finding in previous["findings"]:
        if finding["kind"] not in DRIFT_FINDINGS:
            continue
        if candidate is None:
            carried.append(dict(finding))
        elif finding["kind"] in (FINDING_CANDIDATE_DRIFT,
                                 FINDING_CANDIDATE_UNOBSERVED) and (
            finding["subject"] not in candidate["artifact_digests"]
        ):
            carried.append(dict(finding))
        elif finding["kind"] in (FINDING_BASELINE_DRIFT, FINDING_BASELINE_UNOBSERVED) and (
            candidate["baseline_digest_sha256"] is None
        ):
            carried.append(dict(finding))
    return carried


def combine_drift(live, carried):
    """Live drift (what the report in hand contradicts, applicable or
    not: a contradiction always counts) merged with carried drift (what
    it could not resolve), one finding per (kind, subject), the carried
    one winning: a recorded contradiction is never rewritten by a report
    that could not resolve it."""
    combined = {}
    for finding in live:
        combined[(finding["kind"], finding["subject"])] = finding
    for finding in carried:
        combined[(finding["kind"], finding["subject"])] = finding
    return sorted(dict.values(combined),
                  key=lambda f: (f["kind"], f["subject"] or "", f["detail"]))


def drift_findings(as_of, previous, candidate, applicable):
    """The candidate and baseline drift a pass records: live findings
    from the report in hand (when reported), merged with the previous
    pass's unresolved findings, which only an APPLICABLE report (fresh,
    collected at this head) resolves."""
    live = []
    if candidate is not None:
        live.extend(candidate_drift(as_of, candidate, unobserved=applicable))
        live.extend(baseline_drift(baseline_anchor(previous),
                                   candidate["baseline_digest_sha256"],
                                   unobserved=applicable))
    last = previous[-1] if previous else None
    return combine_drift(live, carried_drift(last, candidate if applicable else None))


def receipt_binding(as_of, delivery):
    """The recorded receipt artifact a delivery report names, when the
    report is bound to it: the artifact exists as of the position, is a
    delivery-receipt reference, its locator is the reference the
    validator judged, AND its recorded content digest is the receipt
    digest the existing receipt contract computed for the receipt the
    validator judged (a recorded reference with no digest, or another
    digest, is not that receipt). None when unbound or ABSENT."""
    if delivery["status"] == DELIVERY_REPORT_ABSENT:
        return None
    artifact = state_module.artifact_by_id(as_of, delivery["receipt_artifact_id"])
    if artifact is None or artifact["locator_kind"] != (
        state_module.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE
    ) or artifact["locator"] != delivery["locator"] or (
        artifact["content_digest_sha256"] is None
        or artifact["content_digest_sha256"] != delivery["receipt_digest_sha256"]
    ):
        return None
    return artifact


def delivery_findings(as_of, delivery, observed_at, evaluated_at):
    """The one finding a delivery report yields at ``evaluated_at``: a
    report older than the freshness bound is stale, whatever it says; a
    non-ABSENT report that names no recorded receipt with that reference
    is unbound; otherwise the validator's judgement, bound to the
    receipt artifact it judged."""
    if not is_fresh(evaluated_at, observed_at, REPORTED_FRESHNESS_BOUND_SECONDS):
        return [_finding(FINDING_DELIVERY_STALE, delivery["receipt_artifact_id"],
                         "the delivery report is older than the freshness bound"
                         " of %d seconds at the time of this pass; it judges"
                         " nothing now" % REPORTED_FRESHNESS_BOUND_SECONDS)]
    if delivery["status"] == DELIVERY_REPORT_ABSENT:
        return [_finding(FINDING_DELIVERY_ABSENT, None,
                         "the injected receipt validator reports no receipt")]
    artifact = receipt_binding(as_of, delivery)
    if artifact is None:
        # The locator (up to MAX_LOCATOR_CHARS) never enters a finding
        # detail (bounded by MAX_FINDING_DETAIL_CHARS): the artifact id and
        # the digest identify the receipt; the locator is on the artifact.
        return [_finding(FINDING_DELIVERY_UNBOUND, delivery["receipt_artifact_id"],
                         "the delivery report names receipt artifact %s with receipt"
                         " digest %s, but no recorded delivery-receipt reference of"
                         " this mission carries that id, reference and digest; a"
                         " report bound to no recorded receipt reflects nothing"
                         % (delivery["receipt_artifact_id"],
                            delivery["receipt_digest_sha256"]))]
    attested = state_module.receipt_attestation_of(artifact) is not None
    if delivery["status"] == DELIVERY_REPORT_VALID and not attested:
        # Task 7, Stage 2: a VALID report about a reference the record
        # never attested is an unverified source claim. It is reflected
        # as exactly that; the positive finding exists only for a
        # receipt the validating path attested.
        return [_finding(FINDING_DELIVERY_UNATTESTED, artifact["artifact_id"],
                         "the caller reports recorded receipt reference %s"
                         " (digest %s) VALID, but the record holds no attestation"
                         " of that reference by the delivery layer's validating"
                         " path; an unverified source claim reflects no validity"
                         % (artifact["artifact_id"],
                            artifact["content_digest_sha256"]))]
    return [_finding(_DELIVERY_FINDINGS[delivery["status"]], artifact["artifact_id"],
                     "the caller reports the receipt validator judged recorded"
                     " receipt %s (digest %s) %s; this package reflects the report"
                     " and does not itself establish the judgement"
                     % (artifact["artifact_id"], artifact["content_digest_sha256"],
                        delivery["status"]))]


def attested_findings(as_of):
    """One finding per receipt reference the record holds in attested
    form as of the position: a RECORD fact (re-proved on every load),
    never a source report. Structural validity is not success: the
    detail names the verbatim receipt state and says whether it is the
    one completed-effect state; a receipt attested in any other state is
    an attested NON-completion."""
    findings = []
    for artifact in state_module.attested_artifacts(as_of):
        attestation = state_module.receipt_attestation_of(artifact)
        completed = state_module.receipt_effect_completed(attestation)
        findings.append(_finding(
            FINDING_DELIVERY_ATTESTED, artifact["artifact_id"],
            # The digest and the reference live on the artifact the subject
            # names; step and state are bounded by
            # MAX_RECEIPT_ATTESTATION_FIELD_CHARS each, so the detail stays
            # under MAX_FINDING_DETAIL_CHARS for every schema-valid marker.
            "receipt reference %s is attested by the delivery layer's"
            " validating path for step %s in receipt state %s; effect"
            " completed: %s"
            % (artifact["artifact_id"], attestation["step"],
               attestation["receipt_state"], "yes" if completed else "no")))
    return findings


def derive_findings(state, contract, position, current_revision, sources,
                    source_provenance, previous, evaluated_at):
    """The findings of a reconciliation taken at ``position`` over the
    record as of that position, under the contract bound there, against
    the Mission's revision at the time, the normalized sources, their
    provenance, the previous reconciliation records (``previous`` is the
    list at or before the position; empty for the first) and the pass's
    own time ``evaluated_at``. Pure and deterministic."""
    journal.require_position(position, "position", state["sequence"])
    record.require_timestamp(evaluated_at, "evaluated_at")
    as_of = progress_module.state_as_of(state, position)
    last = previous[-1] if previous else None
    findings = []
    activation = state_module.latest_activation(as_of)
    if activation is not None and activation["revision"] != current_revision:
        findings.append(_finding(
            FINDING_REVISION_DRIFT, None,
            "the active contract binds revision %d but the mission is at"
            " revision %d" % (activation["revision"], current_revision)))
    if activation is not None:
        if contract is None:
            record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                        "position %d is under activation %s but no contract was"
                        " supplied for it" % (position, activation["activation_id"]))
        proof = progress_module.evaluate_proof(contract, as_of,
                                               activation["activation_id"],
                                               evaluated_at)
        for key in sorted(proof["requirements"]):
            status = proof["requirements"][key]
            if status in (progress_module.REQUIREMENT_CONTRADICTED,
                          progress_module.REQUIREMENT_MISMATCHED):
                findings.append(_finding(FINDING_PROOF_CONTRADICTED, key, status))
            elif status != progress_module.REQUIREMENT_SATISFIED:
                findings.append(_finding(FINDING_PROOF_MISSING, key, status))
    for blocker in state_module.active_hard_blockers(as_of):
        findings.append(_finding(FINDING_HARD_BLOCKER, blocker["blocker_id"],
                                 "an active HARD blocker holds the mission"))
    for kind in SOURCE_KINDS:
        standing = sources[kind]["standing"]
        if standing == STANDING_UNAVAILABLE:
            findings.append(_finding(FINDING_SOURCE_UNAVAILABLE, kind,
                                     "the source could not be consulted"))
        elif standing == STANDING_UNKNOWN:
            findings.append(_finding(FINDING_SOURCE_UNKNOWN, kind,
                                     "the source was consulted and holds no fact"))
    task = reported_value(sources, SOURCE_TASK)
    review = reported_value(sources, SOURCE_REVIEW)
    candidate = reported_value(sources, SOURCE_CANDIDATE)
    delivery = reported_value(sources, SOURCE_DELIVERY)
    if last is not None:
        if last["sources"][SOURCE_TASK] != sources[SOURCE_TASK]:
            findings.append(_finding(
                FINDING_TASK_CHANGED, None, "%s -> %s"
                % (_describe(last["sources"][SOURCE_TASK]),
                   _describe(sources[SOURCE_TASK]))))
        if last["sources"][SOURCE_REVIEW] != sources[SOURCE_REVIEW]:
            findings.append(_finding(
                FINDING_REVIEW_CHANGED, None, "%s -> %s"
                % (_describe(last["sources"][SOURCE_REVIEW]),
                   _describe(sources[SOURCE_REVIEW]))))
    if task == TASK_REPORT_COMPLETE:
        closure = as_of["closure"]
        if closure is None or closure["progress"] != state_module.PROGRESS_COMPLETED:
            findings.append(_finding(
                FINDING_REPORTED_COMPLETE_UNVERIFIED, None,
                "the task source reports COMPLETE but the record holds no"
                " COMPLETED closure; a report is not proof"))
        if review != REVIEW_REPORT_APPROVE:
            findings.append(_finding(
                FINDING_REVIEW_NOT_APPROVED, None,
                "the task source reports COMPLETE but the review source is %s"
                % _describe(sources[SOURCE_REVIEW])))
    applicable = candidate is not None and is_fresh(
        evaluated_at, source_provenance[SOURCE_CANDIDATE]["observed_at"],
        REPORTED_FRESHNESS_BOUND_SECONDS)
    findings.extend(drift_findings(as_of, previous, candidate, applicable))
    findings.extend(attested_findings(as_of))
    if delivery is not None:
        findings.extend(delivery_findings(
            as_of, delivery, source_provenance[SOURCE_DELIVERY]["observed_at"],
            evaluated_at))
    findings.sort(key=lambda f: (f["kind"], f["subject"] or "", f["detail"]))
    if len(findings) > MAX_RECONCILIATION_FINDINGS:
        record.fail(PROBLEM_RECONCILIATION_FULL,
                    "a reconciliation derived %d findings; the hard bound is %d"
                    % (len(findings), MAX_RECONCILIATION_FINDINGS))
    return findings


def _describe(source):
    if source["standing"] != STANDING_REPORTED:
        return source["standing"]
    value = source["value"]
    if isinstance(value, dict):
        value = dict.get(value, "status", "candidate")
    return "reported %s" % (value,)


def standing_findings(findings):
    """The findings that describe the state itself, without the
    transition findings that describe the step from the previous pass."""
    return [f for f in findings if f["kind"] not in TRANSITION_FINDINGS]


def plan(mission, state, contract, position, sources, source_provenance,
         evaluated_at):
    """The findings a reconciliation at ``position`` would record at
    ``evaluated_at``, and whether recording them is a meaningful change:
    the first reconciliation always is; a later one is meaningful only
    when its sources or its standing findings differ from the latest
    recorded ones. Provenance never decides."""
    validate_sources(sources, "sources")
    validate_source_provenance(source_provenance, sources, "source_provenance")
    previous = records_as_of(state, position)
    last = previous[-1] if previous else None
    findings = derive_findings(state, contract, position,
                               mission["current_revision"], sources,
                               source_provenance, previous, evaluated_at)
    changed = (last is None or last["sources"] != sources
               or standing_findings(last["findings"]) != standing_findings(findings))
    return {
        "position": position,
        "revision": mission["current_revision"],
        "sources": sources,
        "source_provenance": source_provenance,
        "findings": findings,
        "previous_sequence": None if last is None else last["sequence"],
        "changed": changed,
    }


# -- the stored record ----------------------------------------------------


def new_record(operation_id, sequence, reconciled_at, provenance, observed_position,
               observed_journal_digest_sha256, observed_revision, sources,
               source_provenance, findings):
    return {
        "operation_id": operation_id,
        "sequence": sequence,
        "reconciled_at": reconciled_at,
        "provenance": provenance,
        "observed_position": observed_position,
        "observed_journal_digest_sha256": observed_journal_digest_sha256,
        "observed_revision": observed_revision,
        "sources": sources,
        "source_provenance": source_provenance,
        "findings": findings,
    }


def validate_finding(value, location):
    record.require_dict(value, location)
    record.require_closed_keys(value, FINDING_KEYS, location)
    record.require_member(value["kind"], FINDING_KINDS, location + ".kind")
    record.require_optional_str(value["subject"], location + ".subject",
                                record.MAX_CONTRACT_KEY_CHARS)
    record.require_str(value["detail"], location + ".detail", MAX_FINDING_DETAIL_CHARS)
    return value


def validate_record_shape(value, location):
    """The closed, typed, self-contained shape of a stored reconciliation
    record: what the record validator checks (type refusals carry the
    repository's generic codes; the intra-record semantic refusals carry
    ``mission_reconciliation_malformed``). Its operation binding is
    checked there too; its chain binding and its recomputation are
    derived checks the persistence layer runs LAST
    (``require_bindings``, ``disagreement``)."""
    record.require_dict(value, location)
    record.require_closed_keys(value, RECONCILIATION_KEYS, location)
    record.require_id(value["operation_id"], record.STATE_OPERATION_ID_PREFIX,
                      location + ".operation_id")
    record.require_int(value["sequence"], location + ".sequence", minimum=1)
    record.require_timestamp(value["reconciled_at"], location + ".reconciled_at")
    record.require_int(value["observed_position"], location + ".observed_position",
                       minimum=0)
    if value["observed_position"] != value["sequence"] - 1:
        _malformed(location, "observed_position %d must be the position before its"
                   " own event, %d" % (value["observed_position"],
                                       value["sequence"] - 1))
    record.require_hex(value["observed_journal_digest_sha256"],
                       location + ".observed_journal_digest_sha256", 64)
    record.require_int(value["observed_revision"], location + ".observed_revision",
                       minimum=1)
    record.validate_provenance(value["provenance"], location + ".provenance")
    if value["provenance"]["revision"] != value["observed_revision"]:
        _malformed(location, "observed_revision %d is not the provenance revision %d"
                   % (value["observed_revision"], value["provenance"]["revision"]))
    validate_sources(value["sources"], location + ".sources")
    validate_source_provenance(value["source_provenance"], value["sources"],
                               location + ".source_provenance")
    findings = value["findings"]
    if not isinstance(findings, list):
        record.fail(record.PROBLEM_BAD_TYPE, "%s.findings must be a list" % location)
    if len(findings) > MAX_RECONCILIATION_FINDINGS:
        record.fail(record.PROBLEM_TOO_LARGE,
                    "%s.findings holds %d entries; the hard bound is %d"
                    % (location, len(findings), MAX_RECONCILIATION_FINDINGS))
    for index, finding in enumerate(findings):
        validate_finding(finding, "%s.findings[%d]" % (location, index))
    ordered = sorted(findings, key=lambda f: (f["kind"], f["subject"] or "",
                                              f["detail"]))
    if findings != ordered or any(
        findings[i] == findings[i + 1] for i in range(len(findings) - 1)
    ):
        _malformed(location, "findings must be sorted and duplicate-free")
    return value


def require_bindings(value, state, location):
    """The chain binding of a stored (shape-valid) record: the position
    it observed is one the journal holds and the digest there re-derives."""
    if value["observed_position"] >= state["sequence"]:
        record.fail(PROBLEM_RECONCILIATION_BINDING,
                    "%s observed position %d but the journal holds positions"
                    " 0..%d" % (location, value["observed_position"],
                                state["sequence"]))
    expected = journal.journal_digest_at(state, value["observed_position"])
    if value["observed_journal_digest_sha256"] != expected:
        record.fail(PROBLEM_RECONCILIATION_BINDING,
                    "%s.observed_journal_digest_sha256 does not re-derive from"
                    " the journal at position %d; the history it was taken over"
                    " is not this one" % (location, value["observed_position"]))
    return value


def disagreement(value, state, contract):
    """Recompute the record's findings at its own position and time and
    compare; the first disagreement's detail, or None."""
    position = value["observed_position"]
    derived = derive_findings(state, contract, position, value["observed_revision"],
                              value["sources"], value["source_provenance"],
                              records_as_of(state, position), value["reconciled_at"])
    if derived != value["findings"]:
        return ("findings are %r; recomputation at position %d gives %r"
                % (value["findings"], position, derived))
    return None


def position_view(state, current_revision):
    """The latest reconciliation position for a reader: which event it
    was taken after, the revision and chain digest it observed, its
    sources, provenance and findings, and whether it is current: taken
    after the head event AND under the Mission's current revision; None
    without a record."""
    latest = None if state is None else latest_record(state)
    if latest is None:
        return None
    return {
        "operation_id": latest["operation_id"],
        "sequence": latest["sequence"],
        "reconciled_at": latest["reconciled_at"],
        "observed_position": latest["observed_position"],
        "observed_journal_digest_sha256": latest["observed_journal_digest_sha256"],
        "observed_revision": latest["observed_revision"],
        "sources": latest["sources"],
        "source_provenance": latest["source_provenance"],
        "findings": latest["findings"],
        "current": (latest["sequence"] == state["sequence"]
                    and latest["observed_revision"] == current_revision),
    }
