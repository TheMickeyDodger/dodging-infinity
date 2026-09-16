"""Bounded, read-only Mission Observation (Task 7, Stage 2, roadmap
item 15): pure functions over one stored Mission plus the caller's
pre-materialized, validated observation input, reporting the canonical
facts with provenance and freshness.

Read-only and non-invoking by construction. This module imports only
``copy``, the pure Task 5 modules, the journal and the reconciliation
module. It holds no lock, opens nothing, writes nothing, mints nothing,
waits on nothing, and neither reads nor constructs an authorization, a
decision, a reservation or a human identity. It CONSULTS NOTHING: the
observation input is plain data the caller materialized from its own
controlled sources OUTSIDE this package (``normalize_inputs``), and the
seam refuses anything that is not plain data by TYPE before reading it
(exactly ``dict`` / ``str`` / ``int`` / ``None``; no subclass, no
callable, no other object; bounded depth, item count, string and key
length and integer size, each applied before the value is copied,
formatted, compared or scanned; closed keys; closed source kinds;
closed per-kind vocabularies) with the distinct code
``mission_observation_input``. Type is established with ``type(x) is
<builtin>`` identity only: no attribute is ever read off a caller
object or its type (not even the type's name), an exact dict is
walked with ``dict.items`` (which hashes and compares no key), a key is
proven an exact bounded str before it is looked up or formatted, and
every refusal message is built from this module's own constants and
validated key paths, never from caller data — so no metaclass, no
colliding key's ``__eq__`` / ``__hash__``, no subclass ``__len__`` and
no other dunder dispatch can run inside the read. The service applies
the same exact-type rule to every other caller argument (``mission_id``,
``operation_id``, ``expected_sequence``, ``context``) before any other
use. There is therefore no path by which
a caller's code runs inside a read: no callable is invoked, no
reentrancy is possible, no unbounded wait is reachable, and a hostile
caller cannot mutate anything through observation because observation
never runs its code. Task-6-facing integration stays a controlled
adapter that the CALLER runs and whose materialized result the caller
hands in; the head cursor the reports were collected at travels with
them, and a document that has moved since is reported as such
(``provenance.moved_during_collection``), never folded in silently.

The six-term vocabulary, kept distinct. Every reported fact carries a
STANDING and a FRESHNESS, two closed axes that never substitute for one
another:

- standing ``verified``: the value is what the validated durable record
  holds (re-proved on every load), bound to the head cursor;
- standing ``reported``: the value is a source's assertion that this
  layer cannot verify (a materialized report, an artifact's recorded
  ``available`` flag, evidence submitted but not accepted, a narrative
  claim);
- standing ``unknown``: the source was consulted and holds no fact;
- standing ``unavailable``: the source could not be consulted (no
  report supplied, or the caller reported it unavailable);
- freshness ``fresh`` / ``stale``: for a time-bound fact only, whether
  its age against its bound is within the bound (a reported answer
  against ``REPORTED_FRESHNESS_BOUND_SECONDS``; an accepted evidence
  record against its requirement's approved evidence age; a readiness
  observation against the approved resource age); ``None`` when the
  fact carries no bound or no value to age.

``reported`` is never promoted to ``verified`` by any view here, and
``unavailable`` is never folded into ``unknown``: a fact says exactly
what is known and how.

Attested receipts (Task 7, Stage 2, the receipt criterion). The
``delivery_receipts`` fact reads the ATTESTED form from the record's
marker (``mission.state``), which only the distinct
``attest_delivery_receipt`` operation records and which the persistence
layer re-proves on every load; it is never inferred from a locator kind
or from a delivery source's report. Structural validity and success are
reported apart: ``attested`` lists what the delivery layer's validating
path accepted, ``effects_completed`` lists only the attested references
whose verbatim receipt state AND verbatim step state are the pinned
succeeded states (the seam's own condition), and an
unattested reference stays a raw artifact, readable, never valid. The
delivery source's own report keeps standing ``reported`` and is bound to
a recorded reference as before; ``report_bound_attested`` says whether
the reference it names is attested. What this does not claim: the
attested form is repository source and call-path confinement of the
attesting operation (the static pins), not protection against arbitrary
code in the process, runtime monkey-patching or a rewrite of storage or
source, and not cryptographic authenticity. An attestation changes no
completion term: it approves, closes and waives nothing.

Drift invalidates derived claims now. Candidate and baseline drift are
computed LIVE from the report in hand (recorded artifact digests against
the observed candidate; the observed baseline against the anchor the
Mission's reconciliations first saw) and merged with the latest
reconciliation's drift findings that the live report cannot resolve, so
a mismatching candidate marks the affected accepted evidence ``drifted``
with no reconciliation needed (a terminal Mission can never record one),
and an unavailable source preserves previously detected drift rather
than erasing it. An omission is not a confirmation (round 11): a report
in hand that carries no digest for a recorded artifact marks the
evidence referencing it ``unconfirmed`` (hold ``candidate_unconfirmed``),
and one that names no baseline while an anchor exists raises
``baseline_unconfirmed``; both withhold present verified success without
asserting a contradiction, and an applicable report that names the
subject resolves them. Evidence follows the artifact it REFERENCES
(round 12): a re-recorded key holds several artifacts and the proof
binds the referenced one, so an accepted record is ``drifted`` when the
report's digest for the key is not its own artifact's digest (even if
it is the latest's), and ``unconfirmed`` when the key is unobserved,
whichever artifact under the key it rests on; confirming the latest
never confirms an older one the proof rests on. Relevance (round 13):
every evidence record is reported with its truthful flags, but only
records that BEAR ON THE CLAIM — accepted, not invalidated, and under
the current activation, the same line the proof evaluator draws — can
withhold present success; a never-accepted submission, an invalidated
record or superseded-activation evidence blocks nothing, so the derived
answer is neither more positive nor more negative than the record. A candidate source that is not reported at all is
unavailable or unknown as before and confirms nothing either way. Source
evidence and authority are never rewritten; only the derived claim is
withdrawn.

Historical closure versus present verified success. ``closure_verified``
is the canonical history: the record holds a COMPLETED closure the
persistence layer re-proves on every load. ``verified_success`` is the
PRESENT claim and is True only when that closure stands AND nothing
observed now contradicts or weakens it: proof still satisfied at this
clock, no contradicted or drifted evidence, no HARD blocker,
prerequisites and contract current, and no reported review that is not
APPROVE. A task source reporting COMPLETE is ``reported_complete``;
without the canonical closure it is never verified. The holds are always
listed; the two that state missing information (review unknown or
unavailable) never promote anything and do not by themselves unmake a
canonical closure. A source report that contradicts the canonical record
(a contrary task status, a REJECT against a closure, a drifted or
contradicted proof beside a completion claim) is flagged ``contradicted``
and shown beside it, never reconciled by choosing. Task 5's stated
limits are restated in the report rather than hidden: identity is a
transport credential, separation of duties between the submitter and
the acceptor of evidence is not enforced, and NARRATIVE and PROCESS_EXIT
evidence never verifies anything.
"""

import copy

from mission import journal
from mission import progress as progress_module
from mission import reconciliation
from mission import record
from mission import state as state_module

# -- the vocabulary ---------------------------------------------------------

STANDING_VERIFIED = "verified"
STANDING_REPORTED = reconciliation.STANDING_REPORTED
STANDING_UNKNOWN = reconciliation.STANDING_UNKNOWN
STANDING_UNAVAILABLE = reconciliation.STANDING_UNAVAILABLE
STANDINGS = (STANDING_REPORTED, STANDING_UNAVAILABLE, STANDING_UNKNOWN,
             STANDING_VERIFIED)
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_TERMS = (FRESHNESS_FRESH, FRESHNESS_STALE)
FACT_TERMS = (FRESHNESS_FRESH, FRESHNESS_STALE, STANDING_REPORTED, STANDING_VERIFIED,
              STANDING_UNKNOWN, STANDING_UNAVAILABLE)

FACT_KEYS = ("standing", "freshness", "value", "observed_at", "source", "detail",
             "revision", "position")
SOURCE_RECORD = "record"
SOURCE_REGISTRY = "registry"
SOURCE_CLOCK = "clock"
SOURCE_ADAPTER_PREFIX = "adapter:"

ADAPTER_KINDS = reconciliation.SOURCE_KINDS
INPUT_KEYS = ("cursor", "reports")
REPORT_KEYS = ("value", "observed_at")
UNAVAILABLE_REPORT_KEYS = ("unavailable",)

# -- hard bounds, never derived from input ------------------------------

REPORTED_FRESHNESS_BOUND_SECONDS = reconciliation.REPORTED_FRESHNESS_BOUND_SECONDS
MAX_REPORT_DETAIL_CHARS = 500
MAX_OBSERVATION_INPUT_ITEMS = 512
MAX_OBSERVATION_INPUT_DEPTH = 6
# Bounds applied to a raw value BEFORE it is copied, formatted, compared
# or scanned: a string longer than this, a key longer than this, or an
# integer outside 64 bits is refused by its size alone.
MAX_OBSERVATION_INPUT_STR_CHARS = 2048
MAX_OBSERVATION_INPUT_KEY_CHARS = 128
MAX_OBSERVATION_INPUT_INT_BITS = 63
MAX_MISSION_ID_CHARS = 64
# Every field of a caller-built context is bounded here before the
# baseline validator formats it.
MAX_CONTEXT_FIELD_CHARS = 256

# -- the report shape -------------------------------------------------------

OBSERVATION_KEYS = (
    "mission_id", "observed_at", "cursor", "revision", "sequence", "phase",
    "pending_decisions", "progress", "contract", "task", "review", "candidate",
    "proof", "evidence", "artifacts", "blockers", "dependencies", "readiness",
    "budget", "continuation", "delivery_receipts", "delivery", "completion",
    "reconciliation", "time", "provenance",
)
COMPLETION_KEYS = (
    "closure_verified", "verified_success", "reported_complete", "reported_success",
    "contradicted", "holds", "closure", "limits",
)
HOLD_NO_CANONICAL_CLOSURE = "no_canonical_closure"
HOLD_PROOF_NOT_SATISFIED = "proof_not_satisfied"
HOLD_EVIDENCE_CONTRADICTED = "evidence_contradicted"
HOLD_CANDIDATE_DRIFTED = "candidate_drifted"
HOLD_HARD_BLOCKER_ACTIVE = "hard_blocker_active"
HOLD_PREREQUISITES = "prerequisites_not_satisfied"
HOLD_BASELINE_DRIFTED = "baseline_drifted"
HOLD_REVIEW_NOT_APPROVED = "review_not_approved"
HOLD_REVIEW_UNKNOWN = "review_unknown"
HOLD_REVIEW_UNAVAILABLE = "review_unavailable"
HOLD_CONTRACT_NOT_CURRENT = "contract_not_current"
HOLD_TASK_CONTRADICTS = "task_contradicts"
HOLD_REVIEW_INAPPLICABLE = "review_inapplicable"
# Round 11: a candidate report in hand that does not name a recorded
# artifact (or the anchored baseline) leaves it UNCONFIRMED; a blocking
# hold, not a contradiction and not an information hold.
HOLD_CANDIDATE_UNCONFIRMED = "candidate_unconfirmed"
HOLD_BASELINE_UNCONFIRMED = "baseline_unconfirmed"
HOLDS = (
    HOLD_BASELINE_DRIFTED, HOLD_BASELINE_UNCONFIRMED, HOLD_CANDIDATE_DRIFTED,
    HOLD_CANDIDATE_UNCONFIRMED, HOLD_CONTRACT_NOT_CURRENT,
    HOLD_EVIDENCE_CONTRADICTED, HOLD_HARD_BLOCKER_ACTIVE, HOLD_NO_CANONICAL_CLOSURE,
    HOLD_PREREQUISITES, HOLD_PROOF_NOT_SATISFIED, HOLD_REVIEW_INAPPLICABLE,
    HOLD_REVIEW_NOT_APPROVED, HOLD_REVIEW_UNAVAILABLE, HOLD_REVIEW_UNKNOWN,
    HOLD_TASK_CONTRADICTS,
)
# Holds that state missing INFORMATION rather than a contradiction: a
# review source that could not be consulted or holds no fact. They are
# reported, and they never promote anything, but they do not by
# themselves unmake a canonical closure's present success.
INFORMATION_HOLDS = frozenset((HOLD_REVIEW_UNAVAILABLE, HOLD_REVIEW_UNKNOWN))
LIMITS = {
    "identity": record.PROOF_TRANSPORT_CREDENTIAL_ONLY,
    "separation_of_duties": "not_enforced",
    "non_verifying_evidence_kinds": list(record.NON_SATISFYING_EVIDENCE_KINDS),
}

PROBLEM_OBSERVATION_INPUT = "mission_observation_input"


# -- the observation-input seam (plain data in, nothing consulted) ---------
#
# The functions up to ``normalize_inputs`` are the ONLY code in the
# package that touches a raw caller value. They are written in a
# restricted style the static pins check shape by shape: a raw value is
# used for nothing but ``type(value) is <builtin>`` identity tests until
# its exact type is established; the only operations then applied are
# the ones that cannot dispatch to caller code on an EXACT builtin
# (``len`` of an exact str, ``bit_length`` of an exact int,
# ``dict.items`` iteration of an exact dict, which hashes and compares
# nothing); every bound is applied before a value is copied, formatted,
# compared or scanned; no attribute is ever read off a caller object or
# its type; and every refusal message is built from this module's own
# constants and validated key paths, never from caller data.


def _input(detail):
    record.fail(PROBLEM_OBSERVATION_INPUT, "observation input " + detail)


def _at(location, key):
    return location + "[" + key + "]"


def require_plain_data(value, location, budget=None, depth=0):
    """``value`` is plain data and nothing else: exactly ``dict``,
    ``str``, ``int`` (never bool) or ``None``, no subclass, no callable,
    no other object; a str at most ``MAX_OBSERVATION_INPUT_STR_CHARS``,
    a key at most ``MAX_OBSERVATION_INPUT_KEY_CHARS``, an int within
    ``MAX_OBSERVATION_INPUT_INT_BITS``; nested at most
    ``MAX_OBSERVATION_INPUT_DEPTH`` deep and holding at most
    ``MAX_OBSERVATION_INPUT_ITEMS`` items in all, counted BEFORE each
    item is examined. Refuses by type and size before anything else
    reads the value."""
    if budget is None:
        budget = [0]
    budget[0] = budget[0] + 1
    if budget[0] > MAX_OBSERVATION_INPUT_ITEMS:
        _input("holds more than %d items; the hard bound is refused, never"
               " truncated" % MAX_OBSERVATION_INPUT_ITEMS)
    if value is None:
        return value
    kind = type(value)
    if kind is str:
        if len(value) > MAX_OBSERVATION_INPUT_STR_CHARS:
            _input("at %s is a string longer than %d characters"
                   % (location, MAX_OBSERVATION_INPUT_STR_CHARS))
        return value
    if kind is int:
        if value.bit_length() > MAX_OBSERVATION_INPUT_INT_BITS:
            _input("at %s is an integer outside %d bits"
                   % (location, MAX_OBSERVATION_INPUT_INT_BITS))
        return value
    if kind is not dict:
        _input("at %s is not plain data (an object, a string, an integer or"
               " null)" % location)
    if depth >= MAX_OBSERVATION_INPUT_DEPTH:
        _input("at %s nests deeper than %d" % (location, MAX_OBSERVATION_INPUT_DEPTH))
    for key, item in dict.items(value):
        budget[0] = budget[0] + 1
        if budget[0] > MAX_OBSERVATION_INPUT_ITEMS:
            _input("holds more than %d items; the hard bound is refused, never"
                   " truncated" % MAX_OBSERVATION_INPUT_ITEMS)
        if type(key) is not str:
            _input("at %s has a key that is not a string" % location)
        if len(key) > MAX_OBSERVATION_INPUT_KEY_CHARS:
            _input("at %s has a key longer than %d characters"
                   % (location, MAX_OBSERVATION_INPUT_KEY_CHARS))
        require_plain_data(item, _at(location, key), budget, depth + 1)
    return value


def require_exact_str(value, location, max_chars):
    """Exactly ``str`` (no subclass), at most ``max_chars``; refused by
    type and size before anything else reads it."""
    if type(value) is not str:
        _input("at %s is not a string" % location)
    if len(value) > max_chars:
        _input("at %s is longer than %d characters" % (location, max_chars))
    return value


def require_exact_int(value, location):
    """Exactly ``int`` (no bool, no subclass), within
    ``MAX_OBSERVATION_INPUT_INT_BITS``; refused before anything else
    reads it."""
    if type(value) is not int:
        _input("at %s is not an integer" % location)
    if value.bit_length() > MAX_OBSERVATION_INPUT_INT_BITS:
        _input("at %s is an integer outside %d bits"
               % (location, MAX_OBSERVATION_INPUT_INT_BITS))
    return value


CONTEXT_FIELD_COUNT = 4


def require_exact_context(value):
    """Exactly the transport adapter's ``AuthenticatedContext`` class, no
    subclass; then the INSTANCE DICTIONARY itself is established before
    any field is read: it is reached through the class-level ``__dict__``
    data descriptor (which ``object.__getattribute__`` resolves on the
    type and never by looking a key up in the instance dictionary, so no
    caller-planted key is hashed or compared), walked with ``dict.items``
    (which hashes and compares nothing), every key proven an exact,
    bounded ``str``, the key set proven to be exactly the four fields,
    and each field value taken from that walk by exact-str comparison —
    never by attribute lookup, which would hash and compare a caller's
    colliding key. Every field is then established as an exact, bounded
    ``str`` (``configured_subject`` None or such a str) and a fresh
    context of this package's own construction is returned."""
    if type(value) is not record.AuthenticatedContext:
        _input("at context is not an AuthenticatedContext")
    instance = value.__dict__
    if type(instance) is not dict:
        _input("at context has no plain instance dictionary")
    transport = None
    principal_kind = None
    principal_ref = None
    configured_subject = None
    seen = 0
    for key, item in dict.items(instance):
        if type(key) is not str:
            _input("at context has a field name that is not a string")
        if len(key) > MAX_OBSERVATION_INPUT_KEY_CHARS:
            _input("at context has a field name longer than %d characters"
                   % MAX_OBSERVATION_INPUT_KEY_CHARS)
        if key == "transport":
            transport = item
        elif key == "principal_kind":
            principal_kind = item
        elif key == "principal_ref":
            principal_ref = item
        elif key == "configured_subject":
            configured_subject = item
        else:
            _input("at context carries a field that is not one of the four")
        seen = seen + 1
    if seen != CONTEXT_FIELD_COUNT:
        _input("at context does not carry exactly the four fields")
    transport = require_exact_str(transport, "context.transport",
                                  MAX_CONTEXT_FIELD_CHARS)
    principal_kind = require_exact_str(principal_kind, "context.principal_kind",
                                       MAX_CONTEXT_FIELD_CHARS)
    principal_ref = require_exact_str(principal_ref, "context.principal_ref",
                                      MAX_CONTEXT_FIELD_CHARS)
    configured_subject = (None if configured_subject is None
                          else require_exact_str(configured_subject,
                                                 "context.configured_subject",
                                                 MAX_CONTEXT_FIELD_CHARS))
    return record.AuthenticatedContext(transport=transport,
                                       principal_kind=principal_kind,
                                       principal_ref=principal_ref,
                                       configured_subject=configured_subject)


def _answer(standing, value=None, observed_at=None, detail=None):
    return {"standing": standing, "value": value, "observed_at": observed_at,
            "detail": detail}


def normalize_report(kind, raw):
    """One source's pre-materialized report, ALREADY proven plain data
    by ``require_plain_data``, as a standing, value, observed-at and
    detail. ``None`` is a consulted source with no fact (``unknown``);
    ``{"unavailable": <reason>}`` is a source the caller could not
    consult; ``{"value", "observed_at"}`` is ``reported`` (or
    ``unknown`` when its value is None). Anything else refuses."""
    if raw is None:
        return _answer(STANDING_UNKNOWN)
    location = _at("reports", kind)
    record.require_dict(raw, location)
    if sorted(raw) == sorted(UNAVAILABLE_REPORT_KEYS):
        detail = record.require_str(raw["unavailable"], location + ".unavailable",
                                    MAX_REPORT_DETAIL_CHARS)
        return _answer(STANDING_UNAVAILABLE, detail=detail)
    record.require_closed_keys(raw, REPORT_KEYS, location)
    observed_at = record.require_timestamp(raw["observed_at"],
                                           location + ".observed_at")
    value = reconciliation.validate_source_value(kind, raw["value"],
                                                 location + ".value")
    if value is None:
        return _answer(STANDING_UNKNOWN, observed_at=observed_at)
    return _answer(STANDING_REPORTED, copy.deepcopy(value), observed_at)


def normalize_inputs(inputs):
    """The seam: ``inputs`` is plain data the CALLER materialized from
    its own controlled sources, outside this package — ``{"cursor":
    <the head cursor the reports were collected at, or None>,
    "reports": {<source kind>: <report>}}``. ``require_plain_data``
    runs FIRST and is the only thing that touches the raw value; every
    later step reads exact builtins only. Returns the collected-at
    cursor (validated, or None) and one answer per source kind, a kind
    without a report being ``unavailable``. Every refusal carries
    ``mission_observation_input`` and names a problem code and a
    validated location, never caller data. Nothing is consulted."""
    if inputs is None:
        inputs = {"cursor": None, "reports": {}}
    require_plain_data(inputs, "inputs")
    where = "inputs"
    try:
        record.require_dict(inputs, where)
        record.require_closed_keys(inputs, INPUT_KEYS, where)
        where = "inputs.cursor"
        cursor = inputs["cursor"]
        if cursor is not None:
            cursor = copy.deepcopy(journal.validate_cursor(cursor, where))
        where = "inputs.reports"
        reports = inputs["reports"]
        record.require_dict(reports, where)
        for key in sorted(reports):
            if key not in ADAPTER_KINDS:
                record.fail(record.PROBLEM_UNKNOWN_KEY, "unknown source kind")
        answers = {}
        for kind in ADAPTER_KINDS:
            where = _at("inputs.reports", kind)
            if kind not in reports:
                answers[kind] = _answer(STANDING_UNAVAILABLE,
                                        detail="no report supplied")
                continue
            answers[kind] = normalize_report(kind, reports[kind])
    except record.MissionError as exc:
        if exc.problem == PROBLEM_OBSERVATION_INPUT:
            raise
        _input("is malformed at %s (%s)" % (where, exc.problem))
    return cursor, answers


def sources_of(answers):
    """The timestamp-free normalized sources a reconciliation digests:
    standing and reported value only."""
    sources = {}
    for kind in ADAPTER_KINDS:
        answer = answers[kind]
        sources[kind] = {
            "standing": answer["standing"],
            "value": (copy.deepcopy(answer["value"])
                      if answer["standing"] == STANDING_REPORTED else None),
        }
    return reconciliation.validate_sources(sources, "sources")


def provenance_of(answers):
    """The per-source provenance a reconciliation stores beside the
    sources: the observed-at time each source itself reported."""
    return dict((kind, {"observed_at": answers[kind]["observed_at"]})
                for kind in ADAPTER_KINDS)


# -- freshness ----------------------------------------------------------------


def freshness(now, observed_at, bound_seconds):
    """``fresh`` when ``0 <= now - observed_at <= bound``, else ``stale``
    (a future-dated observation is stale, never trusted); None without
    an observation to age."""
    if observed_at is None:
        return None
    age = now - observed_at
    if 0 <= age <= bound_seconds:
        return FRESHNESS_FRESH
    return FRESHNESS_STALE


def _aggregate(terms):
    """The conservative freshness of a group: stale if any is stale,
    fresh if any is fresh and none stale, else None."""
    terms = [t for t in terms if t is not None]
    if not terms:
        return None
    if FRESHNESS_STALE in terms:
        return FRESHNESS_STALE
    return FRESHNESS_FRESH


# -- the report ---------------------------------------------------------------


def fact(cursor, standing, value, freshness_=None, observed_at=None,
         source=SOURCE_RECORD, detail=None):
    """One reported fact, bound to the revision and position of the head
    cursor it was observed at."""
    return {
        "standing": standing,
        "freshness": freshness_,
        "value": value,
        "observed_at": observed_at,
        "source": source,
        "detail": detail,
        "revision": cursor["revision"],
        "position": cursor["position"],
    }


def adapter_fact(cursor, kind, answer, now):
    return fact(
        cursor,
        answer["standing"],
        copy.deepcopy(answer["value"]),
        (freshness(now, answer["observed_at"], REPORTED_FRESHNESS_BOUND_SECONDS)
         if answer["standing"] == STANDING_REPORTED else None),
        answer["observed_at"],
        SOURCE_ADAPTER_PREFIX + kind,
        answer["detail"],
    )


def _requirements_by_key(contract):
    if contract is None:
        return {}
    return dict((r["key"], r) for r in contract["requirements"])


COLLECTION_CURRENT = "current"
COLLECTION_MOVED = "moved"
COLLECTION_UNKNOWN = "unknown"
COLLECTION_STATUSES = (COLLECTION_CURRENT, COLLECTION_MOVED, COLLECTION_UNKNOWN)


def applicable(answer, now, collection):
    """A report can RESOLVE a present question only when it is reported,
    fresh at ``now`` and collected at THIS head — provenance known and
    current. A stale report, one collected at a cursor the document has
    left, or one whose collection cursor was not supplied at all may
    still contradict; it may never resolve or affirm. Absent provenance
    is not evidence of currency."""
    return (answer["standing"] == STANDING_REPORTED
            and collection["status"] == COLLECTION_CURRENT
            and reconciliation.is_fresh(now, answer["observed_at"],
                                        REPORTED_FRESHNESS_BOUND_SECONDS))


def drift(state, candidate, now, collection):
    """The candidate and baseline drift in force NOW: the live candidate
    report against the recorded artifacts and the baseline anchor
    (a contradiction always counts), merged with the latest
    reconciliation's drift findings, which only an APPLICABLE report
    resolves. Uncertainty is preserved, never erased."""
    reported = (copy.deepcopy(candidate["value"])
                if candidate["standing"] == STANDING_REPORTED else None)
    previous = reconciliation.records_as_of(state, state["sequence"])
    findings = reconciliation.drift_findings(
        state, previous, reported, applicable(candidate, now, collection))
    keys = set(f["subject"] for f in findings
               if f["kind"] == reconciliation.FINDING_CANDIDATE_DRIFT)
    unobserved = set(f["subject"] for f in findings
                     if f["kind"] == reconciliation.FINDING_CANDIDATE_UNOBSERVED)
    baseline = any(f["kind"] == reconciliation.FINDING_BASELINE_DRIFT
                   for f in findings)
    baseline_unobserved = any(f["kind"] == reconciliation.FINDING_BASELINE_UNOBSERVED
                              for f in findings)
    return {"keys": keys, "unobserved": unobserved, "baseline": baseline,
            "baseline_unobserved": baseline_unobserved, "findings": findings}


def _evidence_items(state, contract, now, drifted_keys, unobserved_keys, reported,
                    resolving, activation_id):
    """Per evidence record, its drift and confirmation status follow the
    artifacts IT references (round 12), never the latest artifact under
    a key: ``drifted`` when the report in hand names the key with a
    digest other than the referenced artifact's own (a contradiction
    counts even from a stale report), or when the key carries recorded
    or carried drift that no APPLICABLE report naming the key with the
    artifact's own digest resolves (a stale or moved matching report
    resolves nothing, as before); ``unconfirmed`` when the key is
    unobserved — an applicable report omitted it, or the omission is
    carried — whichever recorded artifact under that key the evidence
    rests on. ``reported`` is the report's ``artifact_digests`` when a
    candidate report is in hand, else None; ``resolving`` says whether
    that report is applicable. Every item carries its truthful flags;
    ``bears_on_claim`` (round 13) marks the ones the present claim
    actually rests on — ACCEPTED (not invalidated) and under the CURRENT
    activation, exactly the relevance line the proof evaluator draws —
    and only those may withhold verified success: a never-accepted
    submission, an invalidated record or superseded-activation evidence
    is reported as it is but blocks nothing."""
    requirements = _requirements_by_key(contract)
    items = []
    for evidence in state["evidence"]:
        accepted = state_module.is_accepted(evidence)
        verifying = evidence["kind"] in record.SATISFYING_EVIDENCE_KINDS
        requirement = dict.get(requirements, evidence["requirement_key"])
        fresh = None
        if accepted and requirement is not None:
            fresh = freshness(now, evidence["acceptance"]["accepted_at"],
                              requirement["max_evidence_age_seconds"])
        drifted = False
        unconfirmed = False
        bears_on_claim = accepted and evidence["activation_id"] == activation_id
        for artifact_id in evidence["artifact_ids"]:
            artifact = state_module.artifact_by_id(state, artifact_id)
            if artifact is None or artifact["key"] is None or (
                artifact["content_digest_sha256"] is None
            ):
                continue
            key = artifact["key"]
            named = reported is not None and key in reported
            if named and reported[key] != artifact["content_digest_sha256"]:
                drifted = True
            elif key in drifted_keys and not (named and resolving):
                drifted = True
            if key in unobserved_keys:
                unconfirmed = True
        items.append({
            "evidence_id": evidence["evidence_id"],
            "requirement_key": evidence["requirement_key"],
            "kind": evidence["kind"],
            "standing": (STANDING_VERIFIED if accepted and verifying
                         else STANDING_REPORTED),
            "freshness": fresh,
            "accepted": accepted,
            "invalidated": evidence["invalidation"] is not None,
            "verifying_kind": verifying,
            "drifted": drifted,
            "unconfirmed": unconfirmed,
            "bears_on_claim": bears_on_claim,
            "artifact_ids": list(evidence["artifact_ids"]),
        })
    return items


def _artifact_items(state, candidate):
    observed = {}
    if candidate["standing"] == STANDING_REPORTED:
        observed = candidate["value"]["artifact_digests"]
    receipts = []
    attested = []
    items = []
    for artifact in state["artifacts"]:
        seen = (dict.get(observed, artifact["key"]) if artifact["key"] is not None
                else None)
        matches = None
        if seen is not None and artifact["content_digest_sha256"] is not None:
            matches = seen == artifact["content_digest_sha256"]
        # Task 7, Stage 2: the attested form is read from the MARKER the
        # persistence layer re-proves, never inferred from the locator
        # kind; structural validity (attested) and success (the one
        # pinned receipt state) are reported apart.
        attestation = state_module.receipt_attestation_of(artifact)
        attested_view = None
        if attestation is not None:
            attested_view = {
                "delivery_id": attestation["delivery_id"],
                "step": attestation["step"],
                "receipt_state": attestation["receipt_state"],
                "step_state": attestation["step_state"],
                "authorization_id": attestation["authorization_id"],
                "effect_completed": state_module.receipt_effect_completed(attestation),
            }
        items.append({
            "artifact_id": artifact["artifact_id"],
            "key": artifact["key"],
            "role": artifact["role"],
            "locator_kind": artifact["locator_kind"],
            "content_digest_sha256": artifact["content_digest_sha256"],
            "available_reported": artifact["available"],
            "observed_digest_sha256": seen,
            "matches_observed": matches,
            "recorded_at": artifact["recorded_at"],
            "attested": attestation is not None,
            "receipt_attestation": attested_view,
        })
        if artifact["locator_kind"] == state_module.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE:
            receipts.append(artifact["artifact_id"])
        if attestation is not None:
            attested.append(dict(attested_view, artifact_id=artifact["artifact_id"]))
    return items, receipts, attested


def _completion(state, contract_status, proof, dependencies, evidence_items,
                task, review, active_hard, drifted, now, collection):
    """HISTORICAL closure versus PRESENT verified success. The closure
    is canonical history the store re-proves; present verified success
    additionally requires that nothing observed NOW contradicts or
    weakens it: proof still satisfied at this clock, no contradicted,
    drifted or UNCONFIRMED evidence among the records the claim rests on
    (accepted, current activation — round 13; a candidate report in hand
    that does not name the artifact, or the anchored baseline, confirms
    nothing — round 11), no HARD blocker, prerequisites and contract
    current, and no reported review that is not APPROVE. Holds that only
    state missing information (review unknown / unavailable) are listed
    but do not by themselves unmake a canonical closure; an unconfirmed
    hold blocks but is not a contradiction."""
    closure = state["closure"]
    verified = (closure is not None
                and closure["progress"] == state_module.PROGRESS_COMPLETED)
    reported_complete = (task["standing"] == STANDING_REPORTED
                         and task["value"] == reconciliation.TASK_REPORT_COMPLETE)
    task_contradicts = (task["standing"] == STANDING_REPORTED and task["value"] in (
        reconciliation.TASK_REPORT_FAILED, reconciliation.TASK_REPORT_BLOCKED,
        reconciliation.TASK_REPORT_NOT_STARTED))
    # Only an applicable APPROVE affirms; a stale one, or one collected
    # at a cursor the document has left, affirms nothing.
    review_applicable = applicable(review, now, collection)
    review_approved = (review_applicable
                       and review["value"] == reconciliation.REVIEW_REPORT_APPROVE)
    holds = []
    if not verified:
        holds.append(HOLD_NO_CANONICAL_CLOSURE)
    if proof is None or not proof["satisfied"]:
        holds.append(HOLD_PROOF_NOT_SATISFIED)
    if proof is not None and any(
        s in (progress_module.REQUIREMENT_CONTRADICTED,
              progress_module.REQUIREMENT_MISMATCHED)
        for s in dict.values(proof["requirements"])
    ):
        holds.append(HOLD_EVIDENCE_CONTRADICTED)
    # Round 13: only evidence that bears on the claim (accepted, current
    # activation — the proof evaluator's own line) can withhold present
    # success; a never-accepted, invalidated or superseded record is
    # reported as drifted or unconfirmed but blocks nothing.
    relevant = [e for e in evidence_items if e["bears_on_claim"]]
    if any(e["drifted"] for e in relevant):
        holds.append(HOLD_CANDIDATE_DRIFTED)
    if any(e["unconfirmed"] for e in relevant):
        holds.append(HOLD_CANDIDATE_UNCONFIRMED)
    if drifted["baseline"]:
        holds.append(HOLD_BASELINE_DRIFTED)
    if drifted["baseline_unobserved"]:
        holds.append(HOLD_BASELINE_UNCONFIRMED)
    if active_hard:
        holds.append(HOLD_HARD_BLOCKER_ACTIVE)
    if dependencies is not None and dependencies["prerequisite_problems"]:
        holds.append(HOLD_PREREQUISITES)
    if not contract_status["current"] and contract_status["active"]:
        holds.append(HOLD_CONTRACT_NOT_CURRENT)
    if review["standing"] == STANDING_UNAVAILABLE:
        holds.append(HOLD_REVIEW_UNAVAILABLE)
    elif review["standing"] == STANDING_UNKNOWN:
        holds.append(HOLD_REVIEW_UNKNOWN)
    elif review["value"] != reconciliation.REVIEW_REPORT_APPROVE:
        holds.append(HOLD_REVIEW_NOT_APPROVED)
    elif not review_applicable:
        holds.append(HOLD_REVIEW_INAPPLICABLE)
    if verified and task_contradicts:
        holds.append(HOLD_TASK_CONTRADICTS)
    blocking = [h for h in holds if h not in INFORMATION_HOLDS]
    contradicted = False
    if verified and task_contradicts:
        contradicted = True
    if reported_complete and not verified:
        contradicted = True
    if verified and review["standing"] == STANDING_REPORTED and (
        review["value"] == reconciliation.REVIEW_REPORT_REJECT
    ):
        contradicted = True
    if HOLD_EVIDENCE_CONTRADICTED in holds and (verified or reported_complete):
        contradicted = True
    if (HOLD_CANDIDATE_DRIFTED in holds or HOLD_BASELINE_DRIFTED in holds) and (
        verified or reported_complete
    ):
        contradicted = True
    return {
        "closure_verified": verified,
        "verified_success": verified and not blocking,
        "reported_complete": reported_complete,
        "reported_success": (reported_complete and review_approved
                             and applicable(task, now, collection)),
        "contradicted": contradicted,
        "holds": sorted(holds),
        "closure": copy.deepcopy(closure),
        "limits": copy.deepcopy(LIMITS),
    }


def collection_provenance(collected_at, head):
    """Where the reports were collected relative to this head: current
    (the supplied cursor is the head), moved (it is not), or unknown
    (no cursor was supplied). Only ``current`` lets a report resolve."""
    if collected_at is None:
        return {"status": COLLECTION_UNKNOWN, "collected_at": None, "head": head}
    if collected_at == head:
        return {"status": COLLECTION_CURRENT, "collected_at": collected_at, "head": head}
    return {"status": COLLECTION_MOVED, "collected_at": collected_at, "head": head}


def report(mission, state, contract, contract_status, registry, now, answers,
           collection=None):
    """The observation of one Mission at its head cursor. ``state`` may be
    None (no event yet); ``contract`` is the latest activation's contract
    (None without one) and ``contract_status`` the service's liveness
    view of it; ``registry`` is the store's plain registry view;
    ``answers`` are the collected source answers; ``collection`` is the
    collection provenance (``collection_provenance``), unknown when
    None."""
    record.require_timestamp(now, "now")
    mission_id = mission["mission_id"]
    if state is None:
        state = state_module.new_state_record(mission_id, 0)
    cursor = journal.head_cursor(mission, state)
    if collection is None:
        collection = collection_provenance(None, cursor)
    record.require_member(collection["status"], COLLECTION_STATUSES, "collection.status")
    moved = (None if collection["status"] != COLLECTION_MOVED
             else {"collected_at": collection["collected_at"], "head": collection["head"]})
    activation = state_module.latest_activation(state)
    activation_id = None if activation is None else activation["activation_id"]
    drifted = drift(state, answers[reconciliation.SOURCE_CANDIDATE], now, collection)
    drifted_keys = drifted["keys"]
    proof = readiness = dependencies = budget = None
    if contract is not None:
        proof = progress_module.evaluate_proof(contract, state, activation_id, now)
        readiness = progress_module.readiness(contract, state, now)
        dependencies = dict(
            progress_module.dependency_status(contract, state, activation_id),
            prerequisite_problems=[
                {"problem": p, "detail": d}
                for p, d in progress_module.prerequisite_problems(
                    contract, state, activation_id, registry)])
        budget = progress_module.budget(contract, state)
    candidate_answer = answers[reconciliation.SOURCE_CANDIDATE]
    reported_digests = (candidate_answer["value"]["artifact_digests"]
                        if candidate_answer["standing"] == STANDING_REPORTED else None)
    evidence_items = _evidence_items(state, contract, now, drifted_keys,
                                     drifted["unobserved"], reported_digests,
                                     applicable(candidate_answer, now, collection),
                                     activation_id)
    artifact_items, receipts, attested = _artifact_items(
        state, answers[reconciliation.SOURCE_CANDIDATE])
    active = state_module.active_blockers(state)
    active_hard = bool(state_module.active_hard_blockers(state))
    task = adapter_fact(cursor, reconciliation.SOURCE_TASK,
                        answers[reconciliation.SOURCE_TASK], now)
    review = adapter_fact(cursor, reconciliation.SOURCE_REVIEW,
                          answers[reconciliation.SOURCE_REVIEW], now)
    candidate = adapter_fact(cursor, reconciliation.SOURCE_CANDIDATE,
                             answers[reconciliation.SOURCE_CANDIDATE], now)
    delivery = adapter_fact(cursor, reconciliation.SOURCE_DELIVERY,
                            answers[reconciliation.SOURCE_DELIVERY], now)
    readiness_terms = []
    if contract is not None:
        latest_observation = {}
        for observation in state["resource_readiness"]:
            latest_observation[observation["resource_key"]] = observation
        for required in contract["required_resource_readiness"]:
            seen = latest_observation.get(required["resource_key"])
            readiness_terms.append(
                None if seen is None
                else freshness(now, seen["observed_at"], required["max_age_seconds"]))
    proof_freshness = _aggregate(e["freshness"] for e in evidence_items)
    if proof is not None and progress_module.REQUIREMENT_STALE in dict.values(proof["requirements"]):
        proof_freshness = FRESHNESS_STALE
    latest_checkpoint = None
    if state["checkpoints"]:
        checkpoint = state["checkpoints"][-1]
        latest_checkpoint = {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "recorded_at": checkpoint["recorded_at"],
            "next_permitted_step": checkpoint["next_permitted_step"],
            "refusal": copy.deepcopy(checkpoint["refusal"]),
            "sequence": checkpoint["sequence"],
        }
    reconciliation_view = reconciliation.position_view(state,
                                                       mission["current_revision"])
    delivery_answer = answers[reconciliation.SOURCE_DELIVERY]
    bound_receipt = None
    bound_attested = None
    report_fresh = None
    if delivery_answer["standing"] == STANDING_REPORTED:
        bound = reconciliation.receipt_binding(state, delivery_answer["value"])
        bound_receipt = None if bound is None else bound["artifact_id"]
        report_fresh = reconciliation.is_fresh(now, delivery_answer["observed_at"],
                                               REPORTED_FRESHNESS_BOUND_SECONDS)
        if collection["status"] != COLLECTION_CURRENT:
            # Collected at a cursor the document has left, or at an
            # unknown one: it binds and affirms nothing now.
            bound_receipt = None
        if bound_receipt is not None:
            bound_attested = state_module.receipt_attestation_of(bound) is not None
    attested_ids = [a["artifact_id"] for a in attested]
    return {
        "mission_id": mission_id,
        "observed_at": now,
        "cursor": cursor,
        "revision": mission["current_revision"],
        "sequence": state["sequence"],
        "phase": fact(cursor, STANDING_VERIFIED, {
            "state": mission["state"],
            "current_revision": mission["current_revision"],
            "revisions": len(mission["revisions"]),
        }),
        "pending_decisions": fact(cursor, STANDING_VERIFIED, {
            "awaiting_decision": mission["state"] == record.STATE_AWAITING_DECISION,
            "revision": mission["current_revision"],
            "live_authorization": contract_status["live_authorization"],
            "next_permitted_step": (None if latest_checkpoint is None
                                    else latest_checkpoint["next_permitted_step"]),
        }),
        "progress": fact(cursor, STANDING_VERIFIED, {
            "progress": state["progress"],
            "closure_reason": (None if state["closure"] is None
                               else state["closure"]["reason"]),
        }),
        "contract": fact(cursor, STANDING_VERIFIED, {
            "active": activation is not None,
            "activation_id": activation_id,
            "revision": None if activation is None else activation["revision"],
            "current": contract_status["current"],
            "authority_live": contract_status["authority_live"],
            "problem": contract_status["problem"],
        }),
        "task": task,
        "review": review,
        "candidate": candidate,
        "proof": fact(cursor, STANDING_VERIFIED, copy.deepcopy(proof), proof_freshness),
        "evidence": fact(cursor, STANDING_VERIFIED, evidence_items,
                         _aggregate(e["freshness"] for e in evidence_items)),
        "artifacts": fact(cursor, STANDING_VERIFIED, artifact_items,
                          detail="available flags and locators are recorded"
                                 " assertions; nothing is dereferenced"),
        "blockers": fact(cursor, STANDING_VERIFIED, {
            "active": [{"blocker_id": b["blocker_id"], "key": b["key"],
                        "severity": b["severity"], "opened_at": b["opened_at"]}
                       for b in active],
            "hard_active": active_hard,
        }),
        "dependencies": fact(cursor, STANDING_VERIFIED, dependencies, source=SOURCE_REGISTRY),
        "readiness": fact(cursor, STANDING_VERIFIED, copy.deepcopy(readiness),
                          _aggregate(readiness_terms)),
        "budget": fact(cursor, STANDING_VERIFIED, copy.deepcopy(budget)),
        "continuation": fact(cursor, STANDING_VERIFIED, {
            "attempts": len(state["continuations"]),
            "checkpoints": len(state["checkpoints"]),
            "latest_checkpoint": latest_checkpoint,
        }),
        "delivery_receipts": fact(cursor, STANDING_VERIFIED, {
            "recorded": receipts,
            "attested": attested,
            "unattested": [a for a in receipts if a not in attested_ids],
            "effects_completed": [a["artifact_id"] for a in attested
                                  if a["effect_completed"]],
            "report_bound_to": bound_receipt,
            "report_bound_attested": bound_attested,
            "report_fresh": report_fresh,
        }, detail="recorded receipt references; only an ATTESTED reference"
                  " (recorded through the delivery layer's validating path and"
                  " re-proved on load) establishes receipt validity, and only"
                  " the one succeeded receipt state is a completed effect; an"
                  " unattested reference and a delivery source's report are"
                  " unverified claims, the report reflected only when bound to"
                  " a recorded receipt and fresh"),
        "delivery": delivery,
        "completion": _completion(state, contract_status, proof, dependencies,
                                  evidence_items, task, review, active_hard, drifted,
                                  now, collection),
        "reconciliation": reconciliation_view,
        "time": {
            "now": now,
            "record_updated_at": None if state["sequence"] == 0 else state["updated_at"],
            "source": SOURCE_CLOCK,
        },
        "provenance": {
            "cursor": cursor,
            "revision": cursor["revision"],
            "position": cursor["position"],
            "journal_digest_sha256": cursor["journal_digest_sha256"],
            "record_source": SOURCE_RECORD,
            "freshness_bound_seconds": REPORTED_FRESHNESS_BOUND_SECONDS,
            "moved_during_collection": copy.deepcopy(moved),
            "collection": copy.deepcopy(collection),
            "drift": drifted["findings"],
            "sources": dict(
                (kind, {"source": SOURCE_ADAPTER_PREFIX + kind,
                        "standing": answers[kind]["standing"],
                        "observed_at": answers[kind]["observed_at"],
                        "detail": answers[kind]["detail"]})
                for kind in ADAPTER_KINDS),
        },
    }
