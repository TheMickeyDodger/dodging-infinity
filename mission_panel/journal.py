"""The panel journal: durable BOOKKEEPING for the DI World request panel.

What it records, and nothing else:

- that a provider turn was INTENDED, durably, BEFORE the provider is called
  (``RESERVED``), and how it ended: ``COMPLETED``, ``FAILED`` (the provider
  reported a failure), ``REFUSED`` (refused before any provider process
  existed) or ``UNCERTAIN`` (the outcome is unknown);
- the draft proposal a completed turn yielded, exactly as Mission Core's own
  proposal schema normalised it, with its digest;
- that a draft was submitted to the local request surface, which request that
  produced, and which earlier request it replaces;
- the exact decision OFFER a window displayed, FROZEN: the request, Mission, revision, proposal
  digest, repository, action scope, delivery scope and ABSOLUTE expiry, with the digest of all of
  that (the display digest). While an offer is live every window is shown the same one, so a
  person's think time within its expiry is never staleness, and a decision can be compared with
  what was actually displayed (a submitted offer that differs from the retained one is tampered).

What it never holds: a control capability or any secret, a decision, an
approval, an authorization, or any claim that the provider's text is true.
The provider's text is UNTRUSTED display content. Mission Core and the local
request surface are the source of truth for every proposal, decision and run;
a reload re-reads them and uses this journal only for the conversation history
and the replacement lineage.

Uncertainty is recorded, never resolved by guessing. A turn or a submission
found ``RESERVED`` by a process that is not running it is settled as
``UNCERTAIN`` and is NEVER retried automatically: the claim is only that the
INTENT was durably recorded, not that an external effect happened exactly
once.
"""

import copy
import contextlib
import json
import os
import re
import secrets
import stat

from local_request import store as request_store
from mission import record as mission_record
from workflow_authority.atomic import atomic_write_json, exclusive_store_lock
from workflow_authority.digest import json_digest

SCHEMA_VERSION = 1
JOURNAL_FILE_NAME = "mission_panel_journal.json"
JOURNAL_LOCK_FILE_NAME = "mission_panel_journal.lock"

# Hard bounds. Records are never evicted: a full journal refuses.
MAX_TURN_RECORDS = 2048
MAX_TURN_TEXT_CHARS = 8000
MAX_DETAIL_CHARS = 500
# The LIVE (unexpired) offers at once. Expired offers are pruned, never counted.
MAX_OFFER_RECORDS = 256

TURN_ID_PREFIX = "ct"
CONVERSATION_ID_PREFIX = "cv"
_ID_PATTERN = r"%s-[0-9a-f]{32}"

TURN_RESERVED = "RESERVED"
TURN_COMPLETED = "COMPLETED"
TURN_FAILED = "FAILED"
TURN_REFUSED = "REFUSED"
TURN_UNCERTAIN = "UNCERTAIN"
TURN_STATES = (TURN_RESERVED, TURN_COMPLETED, TURN_FAILED, TURN_REFUSED,
               TURN_UNCERTAIN)

SUBMISSION_RESERVED = "RESERVED"
SUBMISSION_SUBMITTED = "SUBMITTED"
SUBMISSION_UNCERTAIN = "UNCERTAIN"
SUBMISSION_REFUSED = "REFUSED"
SUBMISSION_STATES = (SUBMISSION_RESERVED, SUBMISSION_SUBMITTED,
                     SUBMISSION_UNCERTAIN, SUBMISSION_REFUSED)

DOCUMENT_KEYS = ("schema_version", "turns", "offers")
TURN_KEYS = (
    "turn_id", "conversation_id", "sequence", "created_at", "finished_at",
    "state", "user_text", "assistant_text", "gateway_request_id", "outcome",
    "draft", "draft_problem", "submission",
)
# The offer's bound fields: what a person is shown and a decision must match. ``expires_at`` is
# ABSOLUTE and is fixed when the offer is issued; it is never recomputed.
OFFER_BOUND_KEYS = (
    "request_ref", "mission_id", "revision", "proposal_digest_sha256", "repository_url",
    "action_scope", "delivery_targets", "expires_at",
)
OFFER_KEYS = ("offer_digest_sha256",) + OFFER_BOUND_KEYS + ("issued_at",)
OUTCOME_KEYS = ("status", "error_code", "detail", "unrecognized_event_lines")
DRAFT_KEYS = ("proposal", "proposal_digest_sha256")
SUBMISSION_KEYS = ("state", "reserved_at", "finished_at", "request_ref",
                   "supersedes", "problem")

PROBLEM_STORE_UNREADABLE = "mission_panel_journal_unreadable"
PROBLEM_STORE_FULL = "mission_panel_journal_full"
PROBLEM_BAD_TURN = "mission_panel_journal_bad_record"
PROBLEM_BAD_TEXT = "mission_panel_bad_text"
PROBLEM_UNKNOWN_CONVERSATION = "mission_panel_unknown_conversation"
PROBLEM_UNKNOWN_TURN = "mission_panel_unknown_turn"
PROBLEM_CONVERSATION_BUSY = "mission_panel_conversation_busy"
PROBLEM_TURN_NOT_IN_FLIGHT = "mission_panel_turn_not_in_flight"
PROBLEM_NO_DRAFT = "mission_panel_no_draft"
PROBLEM_DRAFT_MISMATCH = "mission_panel_draft_mismatch"
PROBLEM_NO_SUBMISSION_IN_FLIGHT = "mission_panel_no_submission_in_flight"
PROBLEM_OFFERS_FULL = "mission_panel_offers_full"

UNCERTAIN_TURN_OUTCOME = {
    "status": "uncertain", "error_code": "interrupted_before_outcome",
    "detail": "the process that sent this turn to the provider is not running"
              " and recorded no outcome; what the provider did is unknown, so"
              " the turn was not retried",
    "unrecognized_event_lines": 0,
}
_FORBIDDEN_MODE_BITS = 0o077
_HEX = frozenset("0123456789abcdef")


class JournalError(Exception):
    def __init__(self, message, problem=PROBLEM_BAD_TURN):
        super(JournalError, self).__init__(message)
        self.problem = problem


def _bad(where, message):
    raise JournalError("%s: %s" % (where, message), PROBLEM_BAD_TURN)


def _is_int(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and (
        value >= minimum)


def _id_ok(value, prefix):
    return isinstance(value, str) and re.fullmatch(_ID_PATTERN % prefix, value)


def _ref_ok(value):
    return isinstance(value, str) and request_store.request_ref_problem(
        value) is None


def default_document():
    return {"schema_version": SCHEMA_VERSION, "turns": {}, "offers": {}}


_HEX64 = re.compile(r"[0-9a-f]{64}")


def _offer_fields(fields, where):
    """The offer's bound fields, validated and normalised (scope lists sorted and
    duplicate-free), or ``JournalError``."""
    if not isinstance(fields, dict) or sorted(fields) != sorted(OFFER_BOUND_KEYS):
        _bad(where, "an offer carries exactly the keys %s" % ", ".join(OFFER_BOUND_KEYS))
    if not _ref_ok(fields["request_ref"]):
        _bad(where, "request_ref must be an lr- reference")
    if not _id_ok(fields["mission_id"], "mn"):
        _bad(where, "mission_id must be an mn- id")
    if not _is_int(fields["revision"], 1):
        _bad(where, "revision must be a positive integer")
    if not isinstance(fields["proposal_digest_sha256"], str) or not _HEX64.fullmatch(
        fields["proposal_digest_sha256"]
    ):
        _bad(where, "proposal_digest_sha256 must be 64 lowercase hex characters")
    url = fields["repository_url"]
    if url is not None and (not isinstance(url, str) or not url or len(url) > MAX_DETAIL_CHARS):
        _bad(where, "repository_url must be null or a short string")
    for key in ("action_scope", "delivery_targets"):
        value = fields[key]
        if not isinstance(value, list) or not all(
            isinstance(v, str) and 0 < len(v) <= MAX_DETAIL_CHARS for v in value
        ):
            _bad(where, "%s must be a list of names" % key)
        if value != sorted(set(value)):
            _bad(where, "%s must be sorted and duplicate-free" % key)
    if not _is_int(fields["expires_at"], 1):
        _bad(where, "expires_at must be an absolute time")
    return fields


def offer_digest(fields):
    """The display digest of an offer: the canonical digest of its bound fields."""
    return json_digest(_offer_fields(dict((k, fields[k]) for k in OFFER_BOUND_KEYS), "offer"))


def _validate_offer(key, offer, where):
    _closed(offer, OFFER_KEYS, where)
    bound = dict((k, offer[k]) for k in OFFER_BOUND_KEYS)
    _offer_fields(bound, where)
    if not _is_int(offer["issued_at"]) or offer["expires_at"] <= offer["issued_at"]:
        _bad(where, "an offer expires after it is issued")
    if offer["offer_digest_sha256"] != key or json_digest(bound) != key:
        _bad(where, "the display digest does not match the offer it names")


def text_problem(text):
    """Why ``text`` cannot be a turn's words, or None. Checked before any
    lock, read or write."""
    if not isinstance(text, str) or not text.strip():
        return "the words must be a non-empty string"
    if len(text) > MAX_TURN_TEXT_CHARS:
        return "the words are longer than %d characters" % MAX_TURN_TEXT_CHARS
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "the words are not valid UTF-8 text"
    return None


def _closed(value, keys, where):
    if not isinstance(value, dict) or sorted(value) != sorted(keys):
        _bad(where, "must be an object with exactly the keys %s"
             % ", ".join(keys))


def _optional_text(value, limit, where):
    if value is not None and (not isinstance(value, str) or len(value) > limit):
        _bad(where, "must be null or a string of at most %d characters" % limit)


def _validate_outcome(outcome, where):
    _closed(outcome, OUTCOME_KEYS, where)
    if not isinstance(outcome["status"], str) or not outcome["status"] or len(
        outcome["status"]
    ) > MAX_DETAIL_CHARS:
        _bad(where + ".status", "must be a short string")
    _optional_text(outcome["error_code"], MAX_DETAIL_CHARS, where + ".error_code")
    _optional_text(outcome["detail"], MAX_DETAIL_CHARS, where + ".detail")
    if not _is_int(outcome["unrecognized_event_lines"]):
        _bad(where + ".unrecognized_event_lines", "must be a non-negative integer")


def _validate_draft(draft, where):
    _closed(draft, DRAFT_KEYS, where)
    try:
        clean = mission_record.validate_proposal(draft["proposal"])
    except mission_record.MissionError as exc:
        _bad(where, "the proposal is not a valid Mission proposal (%s)" % exc)
    if clean != draft["proposal"]:
        _bad(where, "the proposal is not in normalized form")
    if draft["proposal_digest_sha256"] != mission_record.proposal_digest(clean):
        _bad(where, "proposal_digest_sha256 does not match the proposal")


def _validate_submission(sub, where):
    _closed(sub, SUBMISSION_KEYS, where)
    if sub["state"] not in SUBMISSION_STATES:
        _bad(where, "state must be one of %s" % ", ".join(SUBMISSION_STATES))
    if not _is_int(sub["reserved_at"]):
        _bad(where, "reserved_at must be a non-negative integer")
    if sub["supersedes"] is not None and not _ref_ok(sub["supersedes"]):
        _bad(where, "supersedes must be null or an lr- request reference")
    _optional_text(sub["problem"], MAX_DETAIL_CHARS, where + ".problem")
    if sub["state"] == SUBMISSION_RESERVED:
        if sub["finished_at"] is not None or sub["request_ref"] is not None or (
            sub["problem"] is not None
        ):
            _bad(where, "a RESERVED submission has not finished")
        return
    if not _is_int(sub["finished_at"]) or sub["finished_at"] < sub["reserved_at"]:
        _bad(where, "a settled submission needs a finished_at")
    if sub["state"] == SUBMISSION_SUBMITTED:
        if not _ref_ok(sub["request_ref"]) or sub["problem"] is not None:
            _bad(where, "a SUBMITTED submission names its lr- request only")
    elif sub["request_ref"] is not None:
        _bad(where, "only a SUBMITTED submission names a request")
    if sub["state"] == SUBMISSION_REFUSED and sub["problem"] is None:
        _bad(where, "a REFUSED submission names its problem")


def _validate_turn(key, turn, where):
    _closed(turn, TURN_KEYS, where)
    if not _id_ok(turn["turn_id"], TURN_ID_PREFIX) or turn["turn_id"] != key:
        _bad(where, "turn_id must be its own key and a ct- id")
    if not _id_ok(turn["conversation_id"], CONVERSATION_ID_PREFIX):
        _bad(where, "conversation_id must be a cv- id")
    if not _is_int(turn["sequence"], 1) or not _is_int(turn["created_at"]):
        _bad(where, "sequence and created_at must be integers")
    if turn["state"] not in TURN_STATES:
        _bad(where, "state must be one of %s" % ", ".join(TURN_STATES))
    if text_problem(turn["user_text"]) is not None:
        _bad(where, "user_text is not valid turn text")
    _optional_text(turn["assistant_text"], MAX_TURN_TEXT_CHARS,
                   where + ".assistant_text")
    _optional_text(turn["gateway_request_id"], MAX_DETAIL_CHARS,
                   where + ".gateway_request_id")
    _optional_text(turn["draft_problem"], MAX_DETAIL_CHARS, where + ".draft_problem")
    if turn["state"] == TURN_RESERVED:
        if any(turn[k] is not None for k in (
            "finished_at", "assistant_text", "outcome", "draft", "draft_problem",
        )):
            _bad(where, "a RESERVED turn has no outcome yet")
    else:
        if not _is_int(turn["finished_at"]) or turn["finished_at"] < turn["created_at"]:
            _bad(where, "a settled turn needs a finished_at")
        _validate_outcome(turn["outcome"], where + ".outcome")
    if turn["state"] == TURN_COMPLETED:
        if turn["assistant_text"] is None:
            _bad(where, "a COMPLETED turn carries the provider's words")
        if turn["draft"] is not None and turn["draft_problem"] is not None:
            _bad(where, "a turn yields a draft or a draft problem, not both")
    elif turn["draft"] is not None or turn["draft_problem"] is not None:
        _bad(where, "only a COMPLETED turn can carry a draft or a draft problem")
    if turn["draft"] is not None:
        _validate_draft(turn["draft"], where + ".draft")
    if turn["submission"] is not None:
        if turn["draft"] is None:
            _bad(where, "only a turn with a draft can be submitted")
        _validate_submission(turn["submission"], where + ".submission")


def validate_document(document, path="<document>"):
    _closed(document, DOCUMENT_KEYS, path)
    if document["schema_version"] != SCHEMA_VERSION:
        _bad(path, "schema_version must be %d" % SCHEMA_VERSION)
    turns = document["turns"]
    if not isinstance(turns, dict) or len(turns) > MAX_TURN_RECORDS:
        _bad(path, "turns must be an object of at most %d records" % MAX_TURN_RECORDS)
    positions = set()
    for key, turn in turns.items():
        _validate_turn(key, turn, "%s.turns[%s]" % (path, key))
        position = (turn["conversation_id"], turn["sequence"])
        if position in positions:
            _bad(path, "two turns share the position %s" % (position,))
        positions.add(position)
    offers = document["offers"]
    if not isinstance(offers, dict) or len(offers) > MAX_OFFER_RECORDS:
        _bad(path, "offers must be an object of at most %d records" % MAX_OFFER_RECORDS)
    for key, offer in offers.items():
        _validate_offer(key, offer, "%s.offers[%s]" % (path, key))
    return document


class PanelJournal(object):
    """Atomic load/mutate/save of the journal's one document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, JOURNAL_FILE_NAME)

    # -- storage -----------------------------------------------------------

    def _refuse_open_directory(self):
        try:
            mode = os.stat(self.directory).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(mode):
            raise JournalError("%s is not a directory" % self.directory,
                               PROBLEM_STORE_UNREADABLE)
        if mode & _FORBIDDEN_MODE_BITS:
            raise JournalError(
                "state directory %s is accessible by group/other (mode %o);"
                " nothing is read, locked or written" % (
                    self.directory, stat.S_IMODE(mode)),
                PROBLEM_STORE_UNREADABLE)

    def lock(self):
        self._refuse_open_directory()
        return exclusive_store_lock(self.directory, JOURNAL_LOCK_FILE_NAME)

    def load(self):
        self._refuse_open_directory()
        if not os.path.exists(self.path):
            return default_document()
        mode = os.stat(self.path).st_mode
        if mode & _FORBIDDEN_MODE_BITS:
            raise JournalError(
                "%s is accessible by group/other (mode %o)" % (
                    self.path, stat.S_IMODE(mode)), PROBLEM_STORE_UNREADABLE)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise JournalError("%s could not be read as JSON (%s)" % (
                self.path, exc), PROBLEM_STORE_UNREADABLE)
        return validate_document(document, self.path)

    def save(self, document):
        self._refuse_open_directory()
        validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".mission-panel-")

    @contextlib.contextmanager
    def _writing(self):
        """Lock, load, hand the document over, and save it only if the caller
        marked it changed (``state["changed"] = True``)."""
        with self.lock():
            document = self.load()
            state = {"changed": False}
            yield document, state
            if state["changed"]:
                self.save(document)

    # -- reads ---------------------------------------------------------------

    def turn(self, turn_id):
        found = self.load()["turns"].get(turn_id)
        if found is None:
            raise JournalError("turn %s is not in the journal" % turn_id,
                               PROBLEM_UNKNOWN_TURN)
        return copy.deepcopy(found)

    def conversation(self, conversation_id):
        turns = [t for t in self.load()["turns"].values()
                 if t["conversation_id"] == conversation_id]
        if not turns:
            raise JournalError("conversation %s is not in the journal"
                               % conversation_id, PROBLEM_UNKNOWN_CONVERSATION)
        return copy.deepcopy(sorted(turns, key=lambda t: t["sequence"]))

    def recent_conversations(self, limit):
        """The newest conversations first: ``{conversation_id, turns,
        last_activity}``, from the journal alone."""
        grouped = {}
        for turn in self.load()["turns"].values():
            entry = grouped.setdefault(turn["conversation_id"], {
                "conversation_id": turn["conversation_id"], "turns": 0,
                "last_activity": 0})
            entry["turns"] += 1
            entry["last_activity"] = max(
                entry["last_activity"], turn["finished_at"] or turn["created_at"])
        ordered = sorted(grouped.values(), reverse=True,
                         key=lambda e: (e["last_activity"], e["conversation_id"]))
        return ordered[:limit]

    def lineage(self, request_ref):
        """``{supersedes, superseded_by, turn_id}`` for a request, from the
        SUBMITTED submissions: what this request replaced and what replaced it."""
        found = {"supersedes": None, "superseded_by": None, "turn_id": None}
        replacers = []
        for turn in self.load()["turns"].values():
            sub = turn["submission"]
            if sub is None or sub["state"] != SUBMISSION_SUBMITTED:
                continue
            if sub["request_ref"] == request_ref:
                found["supersedes"], found["turn_id"] = sub["supersedes"], turn["turn_id"]
            if sub["supersedes"] == request_ref:
                replacers.append((sub["finished_at"], sub["request_ref"]))
        if replacers:
            found["superseded_by"] = max(replacers)[1]
        return found

    def superseded_by(self, request_ref):
        return self.lineage(request_ref)["superseded_by"]

    def submitted_requests(self):
        """The ``lr-`` references THIS panel durably submitted, newest first,
        from SUBMITTED submissions only. Read-only; a request this panel did not
        submit (another surface's, a CLI's) is never in it, which is what bounds
        what the progression runtime may enumerate."""
        found = []
        for turn in self.load()["turns"].values():
            sub = turn["submission"]
            if sub is not None and sub["state"] == SUBMISSION_SUBMITTED:
                found.append((sub["finished_at"], sub["request_ref"]))
        return [ref for _, ref in sorted(found, reverse=True)]

    # -- turns ---------------------------------------------------------------

    @staticmethod
    def _settle_unresolved(document, in_flight, now):
        changed = []
        for turn in document["turns"].values():
            if turn["turn_id"] in in_flight:
                continue
            touched = False
            if turn["state"] == TURN_RESERVED:
                turn["state"] = TURN_UNCERTAIN
                turn["finished_at"] = max(now, turn["created_at"])
                turn["outcome"] = dict(UNCERTAIN_TURN_OUTCOME)
                touched = True
            sub = turn["submission"]
            if sub is not None and sub["state"] == SUBMISSION_RESERVED:
                sub["state"] = SUBMISSION_UNCERTAIN
                sub["finished_at"] = max(now, sub["reserved_at"])
                touched = True
            if touched:
                changed.append(turn["turn_id"])
        return changed

    def reconcile_unresolved(self, in_flight_turn_ids, now):
        """Settle every turn or submission left RESERVED by a process that is
        not running it as UNCERTAIN (never retried). Returns the turn ids
        changed; writes nothing when there are none."""
        with self._writing() as (document, state):
            changed = self._settle_unresolved(document, set(in_flight_turn_ids), now)
            state["changed"] = bool(changed)
            return changed

    def reserve_turn(self, conversation_id, user_text, gateway_request_id, now,
                     in_flight_turn_ids):
        problem = text_problem(user_text)
        if problem is not None:
            raise JournalError(problem, PROBLEM_BAD_TEXT)
        in_flight = set(in_flight_turn_ids)
        with self._writing() as (document, state):
            # A turn another process left RESERVED is settled first, so a dead
            # process can never keep a conversation busy.
            self._settle_unresolved(document, in_flight, now)
            state["changed"] = True
            turns = document["turns"]
            if len(turns) >= MAX_TURN_RECORDS:
                raise JournalError(
                    "%d turns are held; the hard bound is %d and records are"
                    " never evicted" % (len(turns), MAX_TURN_RECORDS),
                    PROBLEM_STORE_FULL)
            if conversation_id is None:
                conversation_id = self._fresh_id(
                    CONVERSATION_ID_PREFIX, {t["conversation_id"] for t in turns.values()})
                sequence = 1
            else:
                existing = [t for t in turns.values()
                            if t["conversation_id"] == conversation_id]
                if not existing:
                    state["changed"] = False
                    raise JournalError("conversation %s is not in the journal"
                                       % conversation_id, PROBLEM_UNKNOWN_CONVERSATION)
                if any(t["turn_id"] in in_flight and t["state"] == TURN_RESERVED
                       for t in existing):
                    raise JournalError("a turn of this conversation is still with"
                                       " the provider", PROBLEM_CONVERSATION_BUSY)
                sequence = max(t["sequence"] for t in existing) + 1
            turn_id = self._fresh_id(TURN_ID_PREFIX, turns)
            turns[turn_id] = {
                "turn_id": turn_id, "conversation_id": conversation_id,
                "sequence": sequence, "created_at": now, "finished_at": None,
                "state": TURN_RESERVED, "user_text": user_text,
                "assistant_text": None,
                "gateway_request_id": (None if gateway_request_id is None
                                       else str(gateway_request_id)[:MAX_DETAIL_CHARS]),
                "outcome": None, "draft": None, "draft_problem": None,
                "submission": None,
            }
            return copy.deepcopy(turns[turn_id])

    @staticmethod
    def _fresh_id(prefix, taken):
        for _ in range(8):
            candidate = "%s-%s" % (prefix, secrets.token_hex(16))
            if candidate not in taken:
                return candidate
        raise JournalError("no unused id could be minted", PROBLEM_BAD_TURN)

    def finish_turn(self, turn_id, state, assistant_text, outcome, draft,
                    draft_problem, now):
        with self._writing() as (document, changed):
            turn = document["turns"].get(turn_id)
            if turn is None:
                raise JournalError("turn %s is not in the journal" % turn_id,
                                   PROBLEM_UNKNOWN_TURN)
            if turn["state"] != TURN_RESERVED:
                raise JournalError("turn %s is %s, not in flight" % (
                    turn_id, turn["state"]), PROBLEM_TURN_NOT_IN_FLIGHT)
            turn.update({
                "state": state, "finished_at": max(now, turn["created_at"]),
                "assistant_text": assistant_text, "outcome": outcome,
                "draft": draft, "draft_problem": draft_problem})
            changed["changed"] = True
            validate_document(document, self.path)
            return copy.deepcopy(turn)

    # -- offers ----------------------------------------------------------------

    def offer(self, offer_digest_sha256):
        """The retained offer with this display digest, or None (never issued, or expired
        and pruned)."""
        found = self.load()["offers"].get(offer_digest_sha256)
        return None if found is None else copy.deepcopy(found)

    def freeze_offer(self, fields, now):
        """Freeze the offer a window is about to display. Returns ``(offer, created)``.

        While an unexpired offer exists for exactly this binding, that SAME offer is returned and
        ``created`` is False: every window shows one offer, with one absolute expiry, fixed when
        it was first issued and never recomputed. After its expiry a new offer is issued with a
        new expiry. Expired offers are pruned. Nothing is ever evicted to make room: a full set of
        live offers refuses."""
        if not isinstance(fields, dict) or any(k not in fields for k in OFFER_BOUND_KEYS):
            _bad("offer", "an offer carries exactly the keys %s" % ", ".join(OFFER_BOUND_KEYS))
        bound = _offer_fields(dict((k, fields[k]) for k in OFFER_BOUND_KEYS), "offer")
        if bound["expires_at"] <= now:
            _bad("offer", "an offer must expire after it is issued")
        same = dict((k, bound[k]) for k in OFFER_BOUND_KEYS if k != "expires_at")
        with self._writing() as (document, state):
            offers = document["offers"]
            for key in [k for k, o in offers.items() if o["expires_at"] <= now]:
                del offers[key]
                state["changed"] = True
            for existing in offers.values():
                if all(existing[k] == v for k, v in same.items()):
                    return copy.deepcopy(existing), False
            if len(offers) >= MAX_OFFER_RECORDS:
                raise JournalError(
                    "%d live offers are held; the hard bound is %d and none is evicted"
                    % (len(offers), MAX_OFFER_RECORDS), PROBLEM_OFFERS_FULL)
            digest = json_digest(bound)
            offers[digest] = dict(bound, offer_digest_sha256=digest, issued_at=now)
            state["changed"] = True
            validate_document(document, self.path)
            return copy.deepcopy(offers[digest]), True

    # -- submissions -----------------------------------------------------------

    def reserve_submission(self, turn_id, draft_digest_sha256, supersedes, now):
        """Reserve the submission of a turn's draft BEFORE the surface is
        asked. Returns ``(submission, created)``: a duplicate click, or a
        second window, gets the existing reservation and ``created`` False.
        A REFUSED submission (nothing was recorded) may be tried again; an
        UNCERTAIN one is never retried automatically."""
        with self._writing() as (document, state):
            turn = document["turns"].get(turn_id)
            if turn is None:
                raise JournalError("turn %s is not in the journal" % turn_id,
                                   PROBLEM_UNKNOWN_TURN)
            if turn["draft"] is None:
                raise JournalError("turn %s has no draft to submit" % turn_id,
                                   PROBLEM_NO_DRAFT)
            if turn["draft"]["proposal_digest_sha256"] != draft_digest_sha256:
                raise JournalError("the draft read is not the draft held for"
                                   " this turn", PROBLEM_DRAFT_MISMATCH)
            existing = turn["submission"]
            if existing is not None and existing["state"] != SUBMISSION_REFUSED:
                return copy.deepcopy(existing), False
            turn["submission"] = {
                "state": SUBMISSION_RESERVED, "reserved_at": now,
                "finished_at": None, "request_ref": None,
                "supersedes": supersedes, "problem": None}
            state["changed"] = True
            validate_document(document, self.path)
            return copy.deepcopy(turn["submission"]), True

    def finish_submission(self, turn_id, state, request_ref, problem, now):
        with self._writing() as (document, changed):
            turn = document["turns"].get(turn_id)
            if turn is None:
                raise JournalError("turn %s is not in the journal" % turn_id,
                                   PROBLEM_UNKNOWN_TURN)
            sub = turn["submission"]
            if sub is None or sub["state"] != SUBMISSION_RESERVED:
                raise JournalError("turn %s has no submission in flight" % turn_id,
                                   PROBLEM_NO_SUBMISSION_IN_FLIGHT)
            sub.update({"state": state, "finished_at": max(now, sub["reserved_at"]),
                        "request_ref": request_ref, "problem": problem})
            changed["changed"] = True
            validate_document(document, self.path)
            return copy.deepcopy(sub)
