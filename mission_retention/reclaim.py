"""Local retention: the 30-day automatic reclamation and the owner's "Delete local data".

TWO MECHANISMS, deliberately distinct (Deliverable 2):

- ARCHIVAL is a VIEW change, made the moment a Mission is finished or cancelled WITH a positive basis
  (``mission_retention.eligibility``): it leaves Mine and every filter except Archived. Nothing is
  deleted by it. It lives in the office's filing, not here.
- RECLAMATION is a DATA change, made ``RETENTION_SECONDS`` (30 days) after the source-dated terminal
  point: the Mission's live reasoning and per-Mission logs are removed from the DI-owned stores and
  only the retained SUMMARY stays. A Mission is archived-but-intact for 30 days, then
  archived-and-reclaimed.

And two OUTCOMES, also distinct: AUTOMATIC reclamation keeps the bounded summary (final status,
verdict, CI results, comments, each recorded or honestly not recorded locally); the owner's DELETE
removes the same payload AND the summary, leaving only a tombstone (the Mission's identity, so it is
recognised as deleted and can never re-materialise as intact).

What is reduced, per store (each through its OWN lock, load, closed-schema validation and atomic
save, one store at a time; the retention store's lock is the coordinator, taken first):

- ``mission_panel_journal.json``: each turn of the Mission's conversations keeps its bookkeeping and
  loses its words, its draft's proposal BODY, its questions and its outcome detail. A conversation
  that also produced ANOTHER Mission is reduced only once EVERY Mission it produced is itself safe to
  reclaim (eligible and due, or already reduced): its words are their reasoning too. Until then the
  Mission records whom the conversation is kept for (``retained_for``) and later passes revisit it;
- ``local_requests.json``: each request of the Mission loses its proposal BODY and any copied
  change-request reason; ``control_capabilities`` is never touched;
- ``missions.json``: ONLY a terminal run's two diagnostic texts (``mission_retention.missions``
  explains why everything else is load-bearing for replay and stays: Mission Core Mission records
  persist beyond reclamation, so "reclaimed" means the PAYLOAD stores were reduced);
- ``workflows.json``: a terminal workflow's WHOLE record is deleted. Nothing can re-create or replay
  it (traced in tests/test_mission_retention.py): every approval callback resolves the record by id
  and refuses when it is absent (``telegram_operator/mission.py`` ``evaluate_mission_callback``,
  ``consume_mission``), consumption also needs PLANNED, the only two writers that add a record mint
  a fresh random id (``telegram_operator/mission.py``, ``grok_bot/conforming.py``), and the
  migration only retires. Any field-level reduction would break the record's total render binding;
- ``preserved-evidence/<workflow_id>.json``: EVERY file's content is removed (names, digests and
  sizes stay); the reviewer's canonical decision and a bounded excerpt of its comments are extracted
  into the summary first. This reconciles with ``target_runtime.evidence_preservation``'s obligation
  ("cleanup reclaims LIVE RESOURCES; it does NOT erase the record of what happened"): after 30 days
  the RETAINED SUMMARY, with the reduced file's digests, IS the preserved record.

Never touched: the Authority Ledger, the reservations, authorizations and decisions in
``missions.json``; ``control_capabilities``; ``pr_delivery.json`` and ``grok_bot_requests.json`` (the
delivery store is READ, bounded and read-only, for the summary's CI facts, never written); vendor-global
transcripts (``~/.claude/projects/**``), the target herd's own ``.herd/`` state, and the Herdr purge
child.

Time. Nothing here reads a wall clock: the caller injects ``clock``. A Mission is due when
``now >= anchor + RETENTION_SECONDS`` (EXACTLY AT the deadline is due), where the anchor is the
source-dated terminal time or, where the source dates nothing trustworthy, the FIRST local
observation of proven eligibility (recorded once and never moved). A clock that reads earlier than
the anchor, or a non-integer reading, is simply not due: an anomaly can delay reclamation, never
hasten it. A clock that jumps forward past the deadline (an office that was off) reclaims at once,
which is the overdue case working as intended; no guarantee is made against an untrusted system
clock correction, which nothing here could detect without an independent time source.

Bounded and resumable. One pass reclaims at most ``MAX_RECLAIMS_PER_PASS`` Missions; the rest are
taken on the next pass. A Mission's retention record is advanced to ``reclaiming``/``deleting``
(summary written while the payload is still intact) BEFORE any store is reduced and to
``reclaimed``/``deleted`` after; a pass that dies in between leaves stores that each load (every
write is atomic), and the next pass resumes the same Mission. Every reduction is idempotent.
"""

import copy
import hashlib
import json
import os
import re
import stat
from collections import namedtuple

from local_request import store as request_store
from mission_panel import journal as journal_module
from mission_retention import eligibility
from mission_retention import missions as missions_module
from mission_retention import store as retention_store
from mission_retention import view as view_module
from target_runtime import evidence_preservation
from workflow_authority import store as workflow_store_module

RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_RECLAIMS_PER_PASS = 8

OUTCOME_RECLAIMED = "reclaimed"
OUTCOME_DELETED = "deleted"

# The PR delivery store, READ only (bounded, never locked for write, never saved), for CI facts.
# Re-declared, pinned equal to ``pr_delivery.store`` by test: retention never imports the delivery
# package (tests/test_static.py pins who may), the same discipline as target_runtime/mission_state.py.
DELIVERY_STORE_FILE = "pr_delivery.json"
DELIVERY_STORE_SCHEMA_VERSION = 1
MAX_DELIVERY_STORE_BYTES = 4194304
MAX_DELIVERY_RECORDS = 64

# The reviewer's evidence file, and how much of it the summary may keep: the canonical decision line
# the reviewer contract fixes (roles/reviewer.md: exactly "HERD_DECISION: APPROVE" or
# "HERD_DECISION: REJECT"), and at most ``MAX_COMMENT_CHARS`` characters of the comments around the
# LAST such line. Everything else in it, and every other preserved file, is dropped.
REVIEWER_EVIDENCE = "reviewer-evidence.md"
MAX_COMMENT_CHARS = 600
MAX_OUTCOME_CHARS = 1000
_DECISION_LINE = re.compile(r"^HERD_DECISION: (APPROVE|REJECT)\s*$", re.MULTILINE)

# The owner-delete refusals, each its own reason (the fail-closed matrix).
REFUSE_UNRESOLVED = "target_unresolved"
REFUSE_AMBIGUOUS = "target_ambiguous"
REFUSE_NOT_OWNED = "not_owned"
REFUSE_STALE = "status_stale"
REFUSE_UNKNOWN = "status_unknown"
REFUSE_RUNNING = "running"
REFUSE_NOT_TERMINAL = "not_finished"
REFUSE_NO_BASIS = "no_positive_basis"
REFUSE_DISPATCH_POSSIBLE = "dispatch_still_possible"
REFUSE_BUSY = "run_busy"
REFUSE_IN_FLIGHT = "turn_in_flight"
REFUSE_SHARED = "conversation_shared"
REFUSE_ALREADY_DELETED = "already_deleted"
REFUSE_RETENTION_FULL = "retention_full"
# Round 8 finding 1: the conversation changed between the check and the reduction; nothing was
# reduced, and nothing is queued (the deletion's recorded intent is withdrawn before answering).
REFUSE_CHANGED = "conversation_changed"
# Target identity: the request names a Mission of a DIFFERENT Mission Core store than this panel's
# own (or names none). Two registered mission_core sources may hold the same native id
# (mission_sources/registry.py refuses only a duplicate source id or store), so a native id alone
# never decides which store is acted on: the request's store identity must be this store's.
REFUSE_SOURCE_MISMATCH = "source_mismatch"

# A pass's problem for an owner deletion whose conversation another Mission now shares and still
# needs: it stays ``deleting``, nothing reduced, until every Mission sharing it is safe.
PROBLEM_DELETE_WAITS_FOR_SHARED = "mission_retention_delete_waits_for_shared_conversation"

# Round 8 finding 3: a preserved evidence document that EXISTS but cannot be read as one (a read
# error, malformed JSON, a link, a non-regular or oversized file, or a shape that is not a preserved
# document) is never taken as absent; and a reduction that cannot be written or does not read back
# reduced is never recorded as reclaimed. Both are reported, and retried next pass.
PROBLEM_PRESERVED_UNREADABLE = "mission_retention_preserved_evidence_unreadable"
# Round 9 finding 2: a workflow left its store before its summary could be captured. Its ending is
# KEPT in the store's removal hand-off, and capture is retried every pass, reported, until it reads.
PROBLEM_SUMMARY_NOT_CAPTURED = "mission_retention_summary_not_yet_captured"
PROBLEM_PRESERVED_NOT_REDUCED = "mission_retention_preserved_evidence_not_reduced"
# The bound on one preserved document read (its contents are capped at
# evidence_preservation.MAX_TOTAL_BYTES = 1 MiB; JSON escaping can grow that several times).
MAX_PRESERVED_BYTES = 8 * 1024 * 1024

_ELIGIBILITY_REFUSAL = {
    eligibility.REASON_RUNNING: REFUSE_RUNNING,
    eligibility.REASON_NOT_TERMINAL: REFUSE_NOT_TERMINAL,
    eligibility.REASON_UNKNOWN_STATE: REFUSE_UNKNOWN,
    eligibility.REASON_NO_POSITIVE_BASIS: REFUSE_NO_BASIS,
    eligibility.REASON_DISPATCH_POSSIBLE: REFUSE_DISPATCH_POSSIBLE,
}

PassReport = namedtuple("PassReport", ("observed", "reclaimed", "deferred", "problems"))



def store_identity(kind, store_dir):
    """A store's identity as the catalog names it: sha256 of ``<kind>:<realpath>``. Re-declared from
    mission_sources/sources.py ``store_identity`` (retention does not import the read-only catalog
    reader); tests/test_mission_retention_http.py pins the two equal on the same directory."""
    return hashlib.sha256(("%s:%s" % (kind, os.path.realpath(store_dir))).encode("utf-8")).hexdigest()

class DeleteRefused(Exception):
    """The owner's delete was refused, and NOTHING durable was written: no payload reduced, the
    summary (if one was retained) still retained, the retention record exactly what it was, and no
    deletion queued. ``reason`` is one matrix row."""

    def __init__(self, reason, detail):
        super(DeleteRefused, self).__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail



class DeleteIncomplete(Exception):
    """The owner's deletion STARTED and did not finish (a store failed after the deletion was
    recorded): the summary is removed, part of the payload may be, and the record reads ``deleting``
    while each retention pass retries the remaining cleanup (a store that fails again defers it
    again; nothing promises the next attempt succeeds). Never a refusal, and never reported as one:
    the panel answers it as an ACCEPTED, pending deletion (round 9 finding 1)."""

    def __init__(self, detail, removed, previously):
        super(DeleteIncomplete, self).__init__(detail)
        self.detail = detail
        self.removed = removed
        self.previously = previously


class RetentionDeferred(Exception):
    """A pass could not finish one Mission's reduction safely, and reduced nothing more of it; it
    is reported (``problem``) and retried next pass, never recorded as finished."""

    def __init__(self, problem, detail):
        super(RetentionDeferred, self).__init__("%s: %s" % (problem, detail))
        self.problem = problem
        self.detail = detail

def deadline(record):
    anchor = record["terminal_at"]
    if anchor is None:
        anchor = record["first_eligible_observed_at"]
    return anchor + RETENTION_SECONDS


def is_due(record, now):
    """Exactly at the deadline is due. A non-integer or earlier reading is not."""
    if not isinstance(now, int) or isinstance(now, bool) or now < 0:
        return False
    return now >= deadline(record)


def _fact(state, detail, source):
    return {"state": state, "detail": detail, "source": source}


def _bounded(text, limit=retention_store.MAX_DETAIL_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# -- the CI facts, by bounded READ of the PR delivery store (never written) ----------------------


def _delivery_document(directory):
    """The delivery store document, read bounded and no-follow, or None when it cannot be read."""
    path = os.path.join(directory, DELIVERY_STORE_FILE)
    try:
        mode = os.stat(directory).st_mode
        if not stat.S_ISDIR(mode) or mode & 0o077:
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {"pr_delivery_store_schema_version": DELIVERY_STORE_SCHEMA_VERSION,
                "deliveries": {}}
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or (
            info.st_size > MAX_DELIVERY_STORE_BYTES
        ):
            return None
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(MAX_DELIVERY_STORE_BYTES + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_DELIVERY_STORE_BYTES:
        return None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if type(document) is not dict or document.get("pr_delivery_store_schema_version") != (
        DELIVERY_STORE_SCHEMA_VERSION
    ) or type(document.get("deliveries")) is not dict or len(
        document["deliveries"]) > MAX_DELIVERY_RECORDS:
        return None
    return document


def _delivery_ci(delivery_directory, record_id):
    """CI facts DI recorded for this Mission's deliveries, or an honest absence. READ only."""
    # Round 10: not configured, unreadable and confirmed absence are three DIFFERENT claims
    if delivery_directory is None:
        return _fact(retention_store.SUMMARY_NOT_READ, None,
                     "no PR delivery store is configured for this process, so its CI results were"
                     " not consulted")
    document = _delivery_document(delivery_directory)
    if document is None:
        return _fact(retention_store.SUMMARY_UNREADABLE, None,
                     "%s could not be read as the delivery store, so any CI results it holds are"
                     " not known here" % DELIVERY_STORE_FILE)
    facts = []
    for delivery_id, record in sorted(document["deliveries"].items()):
        if not isinstance(record, dict):
            continue
        linked = (record.get("workflow_identity") or {}).get("workflow_id") == record_id or (
            (record.get("mission") or {}).get("workflow_id") == record_id)
        if not linked:
            continue
        for step, entry in sorted((record.get("steps") or {}).items()):
            observed = ((entry or {}).get("receipt") or {}).get("observed") or {}
            parts = ["%s=%s" % (key, observed[key]) for key in (
                "base_ci", "reverification_exit_status") if observed.get(key) is not None]
            if parts:
                facts.append("%s %s: %s" % (delivery_id, step, ", ".join(parts)))
    source = "%s deliveries linked to %s (steps[].receipt.observed)" % (
        DELIVERY_STORE_FILE, record_id)
    if not facts:
        return _fact(retention_store.SUMMARY_NOT_RECORDED, None, source)
    return _fact(retention_store.SUMMARY_RECORDED, _bounded("; ".join(facts)), source)


def reviewer_summary(text):
    """``(decision, comments)`` from a reviewer evidence document: the LAST canonical decision line
    and at most ``MAX_COMMENT_CHARS`` of the text that follows it (or, when nothing follows, that
    precedes it), whitespace-collapsed. ``(None, None)`` when no canonical line exists: the verdict
    is then not recorded, never guessed from other words."""
    if not isinstance(text, str):
        return None, None
    matches = list(_DECISION_LINE.finditer(text))
    if not matches:
        return None, None
    last = matches[-1]
    after = text[last.end():].strip()
    before = text[:last.start()].strip()
    around = after or before
    comments = _bounded(around, MAX_COMMENT_CHARS) if around else None
    return last.group(1), comments


# -- Mission Core family (the panel state directory) ----------------------------------------------


def _requests_for(requests_doc, mission_id):
    return sorted(ref for ref, value in requests_doc["requests"].items()
                  if value["mission_id"] == mission_id)


def _submitted_refs(journal_doc):
    return {t["submission"]["request_ref"] for t in journal_doc["turns"].values()
            if t["submission"] is not None
            and t["submission"]["state"] == journal_module.SUBMISSION_SUBMITTED}


def _conversations_for(journal_doc, refs):
    return sorted({t["conversation_id"] for t in journal_doc["turns"].values()
                   if t["submission"] is not None and t["submission"]["request_ref"] in refs})


def _conversation_missions(journal_doc, requests_doc, conversation_id):
    """Every Mission one conversation produced; ``None`` among them when a submission names a
    request with no confirmed Mission (whose need for the words cannot be judged)."""
    found = set()
    for turn in journal_doc["turns"].values():
        sub = turn["submission"]
        if turn["conversation_id"] != conversation_id or sub is None or sub["request_ref"] is None:
            continue
        request = requests_doc["requests"].get(sub["request_ref"])
        found.add(None if request is None else request["mission_id"])
    return found


def _mission_core_summary(mission, mission_state_record, delivery_directory):
    mission_id = mission["mission_id"]
    state = mission["state"]
    final = state + (" (withdrawn)" if mission.get("withdrawal") is not None else "")
    final_source = "missions.json missions[%s].state" % mission_id
    progress = (mission_state_record or {}).get("progress")
    if eligibility.MISSION_CORE_STANDING.get(state) != eligibility.STANDING_TERMINAL and (
        progress in eligibility.mission_state.TERMINAL_PROGRESS_STATES
    ):
        # Progress-closed while the record's own state is not terminal (the accepted
        # di-office-state-progress-abandoned-v2 shape): the final status is the closure the office
        # files it by (Deliverable 1 recordClosed), with the record's own state beside it.
        final = "%s (Mission State progress; record state %s)" % (progress, state)
        final_source = "missions.json mission_state[%s].progress; missions[%s].state" % (
            mission_id, mission_id)
    verification = (mission.get("run") or {}).get("verification")
    where = "missions.json missions[%s].run.verification" % mission_id
    if verification is None:
        verdict = _fact(retention_store.SUMMARY_NOT_RECORDED, None, where)
    elif verification.get("verified"):
        verdict = _fact(retention_store.SUMMARY_RECORDED,
                        "verified: every verification conjunct held", where)
    else:
        verdict = _fact(retention_store.SUMMARY_RECORDED,
                        "not verified: %s" % verification.get("failed_conjunct"), where)
    comments = _fact(
        retention_store.SUMMARY_NOT_RECORDED, None,
        "looked in missions.json (Mission State artifacts and evidence carry only digests and"
        " locators), mission_panel_journal.json and local_requests.json: a Mission Core Mission's"
        " review findings are read from the target herd's own task.json"
        " (target_runtime/review_record.py), which DI does not own")
    return {"final_state": final, "final_state_source": final_source,
            "verdict": verdict, "ci": _delivery_ci(delivery_directory, mission_id),
            "comments": comments}


def _observe(retention_doc, family, record_id, decision, now):
    """The record for an eligible Mission, created at its first eligible observation."""
    key = retention_store.retention_key(family, record_id)
    existing = retention_doc["records"].get(key)
    if existing is not None:
        return existing, False
    record = {
        "key": key, "family": family, "record_id": record_id,
        "first_eligible_observed_at": now, "terminal_at": decision.terminal_at,
        "terminal_at_source": decision.terminal_at_source, "basis": decision.basis,
        "basis_source": decision.basis_source, "phase": retention_store.PHASE_OBSERVED,
        "phase_at": now, "summary": None, "retained_for": [],
    }
    retention_store.put_record(retention_doc, record)
    return record, True


class MissionCoreRetention(object):
    """Retention over one panel state directory: ``missions.json``, ``local_requests.json``,
    ``mission_panel_journal.json`` and the coordinator ``mission_retention.json``, all in it."""

    def __init__(self, state_directory, clock, run_guard, delivery_directory=None):
        self.directory = state_directory
        self.clock = clock
        self.run_guard = run_guard
        # this panel's own store, as the catalog names it (mission_sources/sources.py
        # ``store_identity``; the derivation is re-declared here and pinned equal by test)
        self.store_identity = store_identity(retention_store.FAMILY_MISSION_CORE, state_directory)
        self.delivery_directory = delivery_directory
        self.retention = retention_store.RetentionStore(state_directory)
        self.journal = journal_module.PanelJournal(state_directory)
        self.requests = request_store.LocalRequestStore(state_directory)

    def _safe(self, retention_doc, missions, states, mission_id, now):
        """Whether one Mission's words may go now: eligible, AND its record due or already
        reduced. The group rule for a shared conversation asks this of EVERY Mission in it."""
        if mission_id is None or mission_id not in missions:
            return False
        if not eligibility.mission_core(missions[mission_id], states.get(mission_id)).eligible:
            return False
        record = retention_doc["records"].get(retention_store.retention_key(
            retention_store.FAMILY_MISSION_CORE, mission_id))
        if record is None:
            return False
        return record["phase"] in retention_store.REDUCED_PHASES or is_due(record, now)

    def sweep(self):
        """One bounded pass: observe newly eligible Missions, resume any interrupted reduction,
        reclaim at most ``MAX_RECLAIMS_PER_PASS`` that are due, and release shared conversations
        whose every Mission has become safe. Never raises for one Mission's problem: it is reported
        and that Mission is left as it was, to be retried next pass."""
        now = self.clock()
        observed, reclaimed, deferred, problems = 0, 0, 0, []
        with self.retention.lock():
            retention_doc = self.retention.load()
            missions, states = missions_module.load_missions(self.directory)
            changed = False
            for mission_id in sorted(missions):
                decision = eligibility.mission_core(missions[mission_id], states.get(mission_id))
                if not decision.eligible:
                    continue
                try:
                    _, created = _observe(retention_doc, retention_store.FAMILY_MISSION_CORE,
                                          mission_id, decision, now)
                except retention_store.RetentionStoreError as exc:
                    problems.append((mission_id, exc.problem))
                    continue
                observed += int(created)
                changed = changed or created
            if changed:
                self.retention.save(retention_doc)
            for key in sorted(retention_doc["records"]):
                record = retention_doc["records"][key]
                if record["family"] != retention_store.FAMILY_MISSION_CORE:
                    continue
                resuming = record["phase"] in (retention_store.PHASE_RECLAIMING,
                                               retention_store.PHASE_DELETING)
                due = record["phase"] == retention_store.PHASE_OBSERVED and is_due(record, now)
                waiting = record["phase"] == retention_store.PHASE_RECLAIMED and bool(
                    record["retained_for"])
                if not (resuming or due or waiting):
                    continue
                if reclaimed >= MAX_RECLAIMS_PER_PASS:
                    deferred += 1
                    continue
                try:
                    if self._reclaim_one(retention_doc, record, missions, states, now):
                        reclaimed += 1
                    else:
                        deferred += 1
                except (journal_module.JournalError, request_store.LocalRequestStoreError,
                        retention_store.RetentionStoreError, RetentionDeferred) as exc:
                    problems.append((record["record_id"], getattr(exc, "problem", str(exc))))
        return PassReport(observed, reclaimed, deferred, problems)

    def _reclaim_one(self, retention_doc, record, missions, states, now):
        mission_id = record["record_id"]
        mission = missions.get(mission_id)
        deleting = record["phase"] == retention_store.PHASE_DELETING
        outcome = OUTCOME_DELETED if deleting else OUTCOME_RECLAIMED
        with self.run_guard.hold(mission_id, blocking=False) as held:
            if not held:
                return False
            if mission is None or not eligibility.mission_core(
                    mission, states.get(mission_id)).eligible:
                # Rechecked at the moment of action: a record that stopped being eligible is left.
                return False
            journal_doc = self.journal.load()
            requests_doc = self.requests.load()
            refs = _requests_for(requests_doc, mission_id)
            if record["phase"] == retention_store.PHASE_OBSERVED:
                record["summary"] = _mission_core_summary(mission, states.get(mission_id),
                                                          self.delivery_directory)
                record["phase"] = retention_store.PHASE_RECLAIMING
                record["phase_at"] = now
                self.retention.save(retention_doc)
            # The group rule: a conversation goes once EVERY Mission it produced is safe. It applies
            # to a resumed owner deletion too (round 8 finding 1): a turn in flight when the owner
            # asked may since have become another Mission's submission, and the owner's request
            # never takes words another Mission still needs.
            release, kept_for, checked = [], set(), {}
            for conversation_id in _conversations_for(journal_doc, set(refs)):
                linked = _conversation_missions(journal_doc, requests_doc, conversation_id)
                unsafe = {other for other in linked - {mission_id}
                          if not self._safe(retention_doc, missions, states, other, now)}
                if unsafe:
                    kept_for.update(str(other) for other in unsafe)
                else:
                    release.append(conversation_id)
                    # what was judged: the journal reduces only this same snapshot, atomically
                    # inside its own write lock, or refuses and nothing is reduced
                    checked[conversation_id] = journal_module.conversation_snapshot(
                        journal_doc, conversation_id)
            if deleting and kept_for:
                raise RetentionDeferred(PROBLEM_DELETE_WAITS_FOR_SHARED, (
                    "the deletion of Mission %s waits: its conversation also produced Mission(s)"
                    " %s, which still need its words" % (mission_id, ", ".join(sorted(kept_for)))))
            self.journal.reclaim_conversations(release, now, outcome, checked)
            request_store.reclaim_requests(self.requests, refs, now, outcome)
            missions_module.reclaim_run_diagnostics(self.directory, [mission_id], now, outcome)
            record["phase"] = (retention_store.PHASE_DELETED if deleting
                               else retention_store.PHASE_RECLAIMED)
            record["phase_at"] = now
            record["retained_for"] = sorted(kept_for)[:retention_store.MAX_RETAINED_FOR]
            if deleting:
                record["summary"] = None
            self.retention.save(retention_doc)
            return True

    def delete(self, mission_id, displayed_state, requested_store):
        """The owner's "Delete local data": rechecked NOW, fail closed with a distinct reason per
        row, then the same payload reduction as reclamation AND the summary removed. Returns the
        tombstone record.

        ``requested_store`` is the store identity of the Mission the person selected, as the
        catalog reported it. It is checked FIRST: a Mission of another store (or of no named store)
        is refused before anything is resolved by its native id."""
        if requested_store != self.store_identity:
            raise DeleteRefused(REFUSE_SOURCE_MISMATCH, (
                "the Mission selected belongs to %s, not to this panel's own Mission Core store; it"
                " is never resolved here by its native id alone" % (
                    "another Mission Core store" if requested_store else "no named store")))
        now = self.clock()
        with self.retention.lock():
            retention_doc = self.retention.load()
            missions, states = missions_module.load_missions(self.directory)
            mission = missions.get(mission_id)
            if mission is None:
                raise DeleteRefused(REFUSE_UNRESOLVED, "no Mission %s is in this office's"
                                    " Mission Core store" % mission_id)
            journal_doc = self.journal.load()
            requests_doc = self.requests.load()
            refs = _requests_for(requests_doc, mission_id)
            if len(refs) > 1:
                raise DeleteRefused(REFUSE_AMBIGUOUS, "Mission %s is named by %d requests"
                                    % (mission_id, len(refs)))
            if not set(refs) & _submitted_refs(journal_doc):
                raise DeleteRefused(REFUSE_NOT_OWNED, "this panel has no record of submitting"
                                    " Mission %s, so its ownership cannot be proven" % mission_id)
            if displayed_state != mission["state"]:
                raise DeleteRefused(REFUSE_STALE, "the display showed %r but the Mission is now"
                                    " %s" % (displayed_state, mission["state"]))
            decision = eligibility.mission_core(mission, states.get(mission_id))
            if not decision.eligible:
                raise DeleteRefused(_ELIGIBILITY_REFUSAL[decision.reason], decision.detail)
            conversations = _conversations_for(journal_doc, set(refs))
            # what is judged below (no other Mission shares them): the journal reduces only this
            # same snapshot, atomically inside its own write lock (round 8 finding 1)
            checked = {c: journal_module.conversation_snapshot(journal_doc, c)
                       for c in conversations}
            others = sorted({str(m) for c in conversations
                             for m in _conversation_missions(journal_doc, requests_doc, c)}
                            - {mission_id})
            if others:
                raise DeleteRefused(REFUSE_SHARED, "its conversation also produced Mission(s) %s,"
                                    " whose words would go too" % ", ".join(others))
            key = retention_store.retention_key(retention_store.FAMILY_MISSION_CORE, mission_id)
            existing = retention_doc["records"].get(key)
            if existing is not None and existing["phase"] == retention_store.PHASE_DELETED:
                raise DeleteRefused(REFUSE_ALREADY_DELETED, "Mission %s was already deleted"
                                    % mission_id)
            with self.run_guard.hold(mission_id, blocking=False) as held:
                if not held:
                    raise DeleteRefused(REFUSE_BUSY, "a progression pass is acting on Mission %s"
                                        % mission_id)
                prior = copy.deepcopy(existing)
                try:
                    record, _ = _observe(retention_doc, retention_store.FAMILY_MISSION_CORE,
                                         mission_id, decision, now)
                except retention_store.RetentionStoreError as exc:
                    raise DeleteRefused(REFUSE_RETENTION_FULL, str(exc))
                previously = record["phase"] if prior is not None else view_module.PHASE_INTACT
                had_summary = record["summary"] is not None
                # The durable intent comes BEFORE the first reduction: the deletion spans three
                # separately locked stores, and a process that dies part-way must resume it, never
                # leave a half-deleted Mission reading as intact. It is withdrawn below if the
                # journal refuses, so a refusal leaves nothing durable behind.
                record["phase"] = retention_store.PHASE_DELETING
                record["phase_at"] = now
                record["summary"] = None
                record["retained_for"] = []
                self.retention.save(retention_doc)
                removed = {"summary": had_summary, "conversation_turns": 0, "requests": 0,
                           "run_diagnostics": 0}
                try:
                    removed["conversation_turns"] = self.journal.reclaim_conversations(
                        conversations, now, OUTCOME_DELETED, checked)
                except journal_module.JournalError as exc:
                    # Nothing was reduced: withdraw the intent (the record, and any retained
                    # summary, exactly as they were) BEFORE answering, so "refused" is the truth.
                    self._withdraw(retention_doc, key, prior, removed, previously)
                    if exc.problem == journal_module.PROBLEM_CONVERSATION_BUSY:
                        raise DeleteRefused(REFUSE_IN_FLIGHT, str(exc))
                    if exc.problem == journal_module.PROBLEM_CONVERSATION_CHANGED:
                        raise DeleteRefused(REFUSE_CHANGED, str(exc))
                    raise
                try:
                    removed["requests"] = request_store.reclaim_requests(
                        self.requests, refs, now, OUTCOME_DELETED)
                    removed["run_diagnostics"] = missions_module.reclaim_run_diagnostics(
                        self.directory, [mission_id], now, OUTCOME_DELETED)
                except Exception as exc:  # noqa: BLE001 - any failure here is a started deletion
                    # Part of the payload is already gone: this is a deletion in progress, said as
                    # one, and the next pass retries the rest (it stays ``deleting``).
                    raise DeleteIncomplete(
                        "the deletion of Mission %s started and did not finish (%s); its summary"
                        " is removed, and the next retention pass retries the remaining cleanup"
                        % (mission_id, exc),
                        removed, previously)
                record["phase"] = retention_store.PHASE_DELETED
                record["phase_at"] = now
                self.retention.save(retention_doc)
                return {"record": dict(record), "previously": previously, "removed": removed}

    def _withdraw(self, retention_doc, key, prior, removed, previously):
        """Restore one Mission's retention record to exactly what it was before a refused deletion
        (or remove it, when this deletion created it). If even that cannot be saved, the deletion is
        NOT withdrawn, and it is reported as started, never as refused."""
        if prior is None:
            retention_doc["records"].pop(key, None)
        else:
            retention_doc["records"][key] = prior
        try:
            self.retention.save(retention_doc)
        except (retention_store.RetentionStoreError, OSError) as exc:
            raise DeleteIncomplete(
                "the deletion could not be withdrawn (%s); it stays recorded as started, its"
                " summary is removed, and the next retention pass retries the remaining cleanup"
                % exc, removed,
                previously)



def read_preserved(directory, workflow_id):
    """One workflow's preserved evidence document, three-valued (round 8 finding 3). ``None`` ONLY
    when no preserved document exists for it. Raises ``RetentionDeferred`` for EVERY other failure
    to read it as one (open or read error, a link, a non-regular or oversized file, undecodable or
    malformed JSON, or a shape that is not a preserved document): such a document is there, so it is
    never taken as absent, and it cannot be certified reduced.

    (``target_runtime.evidence_preservation.load_preserved`` returns ``None`` for all of these alike,
    which is right for its verification readers and wrong here.)"""
    path = evidence_preservation.preserved_path(directory, workflow_id)

    def unreadable(why):
        return RetentionDeferred(PROBLEM_PRESERVED_UNREADABLE, (
            "%s exists but %s: it is not absent, and it cannot be certified reclaimed"
            % (path, why)))
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise unreadable("could not be opened without following a link (%s)" % exc)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise unreadable("is not a regular file")
        if info.st_size > MAX_PRESERVED_BYTES:
            raise unreadable("is larger than %d bytes" % MAX_PRESERVED_BYTES)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(MAX_PRESERVED_BYTES + 1)
    except OSError as exc:
        raise unreadable("could not be read (%s)" % exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_PRESERVED_BYTES:
        raise unreadable("is larger than %d bytes" % MAX_PRESERVED_BYTES)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise unreadable("is not valid JSON (%s)" % exc)
    if type(document) is not dict or type(document.get("files")) is not list or any(
        type(entry) is not dict for entry in document["files"]
    ):
        raise unreadable("is not a preserved evidence document (an object with a files list)")
    return document



def _pruned_source(workflow_id):
    return ("workflows.json workflows[%s].phase, captured while the record was still in its store;"
            " the store's own %d-record cap pruned the record before its retention deadline"
            % (workflow_id, workflow_store_module.MAX_WORKFLOW_RECORDS))

def _preserved_reduced(document):
    return all(entry.get("content") is None for entry in document["files"]) and isinstance(
        document.get("reclaimed"), dict)

# -- workflow-store family (the workflow store directory) -----------------------------------------


class WorkflowRetention(object):
    """Retention over one workflow store directory: ``workflows.json``, ``preserved-evidence/``,
    the read-only ``pr_delivery.json`` beside it, and the coordinator ``mission_retention.json``."""

    def __init__(self, store_directory, clock):
        self.directory = store_directory
        self.clock = clock
        self.retention = retention_store.RetentionStore(store_directory)
        self.workflows = workflow_store_module.WorkflowStore(store_directory)

    def _summary(self, record):
        """The retained summary, or ``RetentionDeferred`` when its preserved evidence exists and
        cannot be read (a verdict is never said "not recorded" over a document that is there)."""
        workflow_id = record["workflow_id"]
        preserved = read_preserved(self.directory, workflow_id)
        reviewer = next((entry for entry in (preserved or {}).get("files") or []
                         if entry.get("name") == REVIEWER_EVIDENCE), None)
        decision, comments = reviewer_summary((reviewer or {}).get("content"))
        result = record.get("verified_result")
        outcome = (result or {}).get("summary") if isinstance(result, dict) else None
        where = ("preserved-evidence/%s.json files[%s] (the canonical HERD_DECISION line);"
                 " workflows.json workflows[%s].verified_result" % (
                     workflow_id, REVIEWER_EVIDENCE, workflow_id))
        parts = []
        if decision is not None:
            parts.append("reviewer HERD_DECISION: %s" % decision)
        if outcome:
            parts.append("verified result: %s" % _bounded(outcome, MAX_OUTCOME_CHARS))
        verdict = (_fact(retention_store.SUMMARY_RECORDED, _bounded("; ".join(parts)), where)
                   if parts else _fact(retention_store.SUMMARY_NOT_RECORDED, None, where))
        where = ("preserved-evidence/%s.json files[%s], at most %d characters around the last"
                 " decision line" % (workflow_id, REVIEWER_EVIDENCE, MAX_COMMENT_CHARS))
        comment = (_fact(retention_store.SUMMARY_RECORDED, comments, where) if comments
                   else _fact(retention_store.SUMMARY_NOT_RECORDED, None, where))
        return {"final_state": record["phase"], "final_state_source":
                "workflows.json workflows[%s].phase" % workflow_id, "verdict": verdict,
                "ci": _delivery_ci(self.directory, workflow_id), "comments": comment}

    def _adopt_removals(self, retention_doc, workflows, now):
        """Adopt the workflow store's removal hand-off (round 8 finding 2): a record its own cap
        pruned, possibly in the very transaction that made it terminal, before any sweep observed
        it. Called under the retention lock and THEN the workflow store's own lock (the sweep's one
        order; the eviction path takes only the workflow lock). The retention record is saved
        BEFORE the adopted endings leave the hand-off, so a crash between the two re-adopts
        idempotently. An ending whose summary cannot be captured yet stays in the hand-off,
        reported. Returns ``(newly observed, problems)``."""
        handoff = workflow_store_module.load_removals(self.directory)
        adopted, problems, observed = [], [], 0
        for workflow_id, ending in sorted(handoff["removed"].items()):
            key = retention_store.retention_key(retention_store.FAMILY_WORKFLOW_STORE, workflow_id)
            record = retention_doc["records"].get(key)
            decision = eligibility.workflow(ending)
            # An ending leaves the hand-off ONLY once nothing more is owed from it (round 9
            # finding 2): the record is still in its store (a save that died after the hand-off;
            # it is observed live), it has no positive basis (retention never reclaims it, as
            # before), or its summary is DURABLY captured. An existing retention record is not
            # proof of capture: an observation whose evidence could not be read leaves one with
            # no summary, and only `summary is not None` is.
            owed = (workflow_id not in workflows and decision.eligible and (
                record is None or (record["summary"] is None and record["phase"] not in (
                    retention_store.PHASE_DELETING, retention_store.PHASE_DELETED))))
            if owed:
                try:
                    summary = self._summary(ending)
                    if record is None:
                        record, created = _observe(
                            retention_doc, retention_store.FAMILY_WORKFLOW_STORE, workflow_id,
                            decision, now)
                        observed += int(created)
                except (RetentionDeferred, retention_store.RetentionStoreError) as exc:
                    # KEPT in the hand-off: retried next pass, and said every pass
                    problems.append((workflow_id, exc.problem))
                    continue
                summary["final_state_source"] = _pruned_source(workflow_id)
                record["summary"] = summary
            adopted.append(workflow_id)
        if adopted:
            self.retention.save(retention_doc)
            for workflow_id in adopted:
                del handoff["removed"][workflow_id]
            workflow_store_module.save_removals(self.directory, handoff)
        return observed, problems

    def _reduce_preserved(self, workflow_id, now, outcome):
        """Reduce one workflow's preserved evidence, PROVEN. ``False`` only when no preserved
        document exists; ``True`` once it re-reads with every content null. Raises
        ``RetentionDeferred`` when it cannot be read, written or proven reduced: the caller then
        records nothing as reclaimed. Idempotent: a document already reduced is not rewritten."""
        document = read_preserved(self.directory, workflow_id)
        if document is None:
            return False
        path = evidence_preservation.preserved_path(self.directory, workflow_id)
        if not _preserved_reduced(document):
            for entry in document["files"]:
                entry["content"] = None
            document["reclaimed"] = {"reclaimed_at": now, "outcome": outcome}
            try:
                workflow_store_module.atomic_write_json(
                    os.path.dirname(path), path, document, temp_prefix=".preserved-")
            except (OSError, TypeError, ValueError) as exc:
                raise RetentionDeferred(PROBLEM_PRESERVED_NOT_REDUCED, (
                    "%s could not be written reduced (%s); it is NOT reclaimed" % (path, exc)))
        again = read_preserved(self.directory, workflow_id)
        if again is None or not _preserved_reduced(again):
            raise RetentionDeferred(PROBLEM_PRESERVED_NOT_REDUCED, (
                "%s did not read back reduced; it is NOT reclaimed" % path))
        return True

    def sweep(self):
        """One bounded pass over the workflow store (see ``MissionCoreRetention.sweep``)."""
        now = self.clock()
        observed, reclaimed, deferred, problems = 0, 0, 0, []
        with self.retention.lock():
            retention_doc = self.retention.load()
            with workflow_store_module.exclusive_store_lock(self.directory):
                workflows = self.workflows.load()["workflows"]
                adopted, adoption_problems = self._adopt_removals(retention_doc, workflows, now)
            observed += adopted
            problems.extend(adoption_problems)
            changed = False
            for workflow_id in sorted(workflows):
                decision = eligibility.workflow(workflows[workflow_id])
                if not decision.eligible:
                    continue
                try:
                    record, created = _observe(retention_doc, retention_store.FAMILY_WORKFLOW_STORE,
                                               workflow_id, decision, now)
                except retention_store.RetentionStoreError as exc:
                    problems.append((workflow_id, exc.problem))
                    continue
                if record["phase"] == retention_store.PHASE_OBSERVED and record["summary"] is None:
                    # captured NOW, while the record is intact: the store's own cap may prune it
                    # before the deadline (workflow_authority/store.py ``_prune_inactive``)
                    try:
                        record["summary"] = self._summary(workflows[workflow_id])
                        changed = True
                    except RetentionDeferred as exc:
                        problems.append((workflow_id, exc.problem))
                observed += int(created)
                changed = changed or created
            if changed:
                self.retention.save(retention_doc)
            for key in sorted(retention_doc["records"]):
                record = retention_doc["records"][key]
                if record["family"] != retention_store.FAMILY_WORKFLOW_STORE:
                    continue
                resuming = record["phase"] == retention_store.PHASE_RECLAIMING
                due = record["phase"] == retention_store.PHASE_OBSERVED and is_due(record, now)
                if not (resuming or due):
                    continue
                if reclaimed >= MAX_RECLAIMS_PER_PASS:
                    deferred += 1
                    continue
                try:
                    if self._reclaim_one(retention_doc, record, now):
                        reclaimed += 1
                    else:
                        deferred += 1
                except (RetentionDeferred, retention_store.RetentionStoreError,
                        workflow_store_module.StoreError) as exc:
                    deferred += 1
                    problems.append((record["record_id"], getattr(exc, "problem", str(exc))))
        return PassReport(observed, reclaimed, deferred, problems)

    def _reclaim_one(self, retention_doc, record, now):
        workflow_id = record["record_id"]
        if record["phase"] == retention_store.PHASE_OBSERVED:
            with workflow_store_module.exclusive_store_lock(self.directory):
                current = self.workflows.load()["workflows"].get(workflow_id)
            if current is not None and not eligibility.workflow(current).eligible:
                return False
            if current is not None:
                record["summary"] = self._summary(current)
            elif record["summary"] is not None:
                # The store's own cap pruned the record before the deadline: the summary captured
                # while it was still there stands, and says so (never "no verdict recorded").
                record["summary"]["final_state_source"] = _pruned_source(workflow_id)
            else:
                # Gone from its store with no summary yet: never reclaimed, and never silently
                # deferred. Its ending stays in the store's removal hand-off, where every pass
                # retries the capture (``_adopt_removals``).
                raise RetentionDeferred(PROBLEM_SUMMARY_NOT_CAPTURED, (
                    "workflow %s left its store before its summary could be captured; it is not"
                    " reclaimed until it is" % workflow_id))
            record["phase"] = retention_store.PHASE_RECLAIMING
            record["phase_at"] = now
            self.retention.save(retention_doc)
        # The workflow store's own lock, held for its whole cycle; the broker writes preserved
        # evidence under this same lock (broker.py: the action lock around _release/preserve).
        with workflow_store_module.exclusive_store_lock(self.directory):
            document = self.workflows.load()
            current = document["workflows"].get(workflow_id)
            if current is not None and not eligibility.workflow(current).eligible:
                return False
            # The payload copy FIRST, proven reduced, and only then the record (round 8 finding
            # 3): a preserved document that cannot be read or proven reduced raises here, leaving
            # the record in its store and this Mission ``reclaiming``, reported and retried.
            self._reduce_preserved(workflow_id, now, OUTCOME_RECLAIMED)
            if current is not None:
                del document["workflows"][workflow_id]
                # retention's OWN removal (not the cap's prune): accounted for here, no hand-off
                self.workflows.save(document)
        record["phase"] = retention_store.PHASE_RECLAIMED
        record["phase_at"] = now
        self.retention.save(retention_doc)
        return True
