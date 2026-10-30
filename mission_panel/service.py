"""The panel's controller: the one object every window's request goes through.

It composes the EXISTING lifecycle and adds no authority and no second
execution engine:

- conversation: ``mission_panel.conversation`` over the restricted operator
  session (read-only, fresh turns). It supplies NO durable authority;
- proposal: the local request surface's ``submit``, which makes Mission Core's
  own proposal, revision and digest. A draft in a chat is not a proposal of
  record until a human submits it and the surface binds it;
- decision: the surface's ``attest_approval``. It takes the person's TYPED
  reply (the exact affirmative, checked before anything is read or written)
  bound to the displayed Mission, revision and digest. Ordinary ``approve``
  stays refused. Nothing a provider wrote reaches this call. By owner
  decision (c) the person may instead decline, reject or request changes:
  ``decline``, ``reject`` and ``request_changes`` relay that choice on the
  exact offer shown, through the surface's ``attest_decline``,
  ``attest_rejection`` and ``attest_change_request``; a change request's
  readable reason is checked first;
- run: the surface's ``run_command`` over the existing ``MissionBridge``.

Every window offers only what ``mission_panel.lifecycle`` derives from the
durable records, and this controller refuses anything else again. A stale
display is refused and INVALIDATED, never re-pointed at the current proposal.
A duplicate click or a second window finds the existing record and cannot
apply, submit or start anything twice. An uncertain submission or start is
shown as uncertain and is never retried. The withdrawal key the surface shows
once is held in this process's memory only.

This module imports no Mission Core module and no execution module: it is
handed the surface, the journal, the conversation and (optionally) the run
bridge.
"""

import copy
import re
import threading

from local_request import store as store_module
from local_request import surface as surface_module

from mission_panel import draft as draft_module
from mission_panel import journal as journal_module
from mission_panel import lifecycle as lifecycle_module
from mission_panel import wire as wire_module

RELAY_REF = "di-world-panel"
DECISION_WORD = "approve"
RUN_COMMANDS = ("dispatch", "observe", "reconcile", "verify")

# What reading a COMPLETED reply for ``mission-questions`` can say. ``not_reported``: the turn was
# written by a panel version that did not read for questions (the journal turn has no ``questions``
# key); that is NOT "read; none declared". ``read``: this version read it (items may be empty).
# ``not_applicable``: no completed reply to read.
QUESTIONS_NOT_REPORTED = "not_reported_by_this_panel_version"
QUESTIONS_READ = "read"
QUESTIONS_NOT_APPLICABLE = "not_applicable"

MAX_OVERVIEW_REQUESTS = 20
MAX_OVERVIEW_CONVERSATIONS = 10
MAX_VIEW_TURNS = 50
# The most serialized bytes of turns one conversation view carries. It is a budget in the bytes
# that really cross the relay (``wire.encode``), because characters cannot honour a byte bound:
# the panel accepts 8000-character texts that are up to 6 bytes a character once serialized
# (a control character escapes to six, an astral one is four raw). 3 MiB leaves the rest of the
# 4 MiB response bound (``wire.MAX_RESPONSE_BYTES``) for the envelope; a single turn at the
# accepted maximum is far smaller (a test measures it), so the newest turn always fits.
MAX_VIEW_BYTES = 3 * 1024 * 1024
MAX_RESULT_TEXT_CHARS = 60000
FIRST_WORDS_CHARS = 120

# What a displayed offer can differ from the record in, beyond the Mission, revision and digest
# (the digest covers the proposal's CONTENT only; the expiry is recomputed from the clock on every
# read). These join the surface's own binding vocabulary in a stale-display refusal.
BINDING_SCOPE_MISMATCH = "scope_mismatch"
BINDING_TARGETS_MISMATCH = "delivery_targets_mismatch"
BINDING_REPOSITORY_MISMATCH = "repository_mismatch"
BINDING_EXPIRY_BEYOND_WINDOW = "expiry_beyond_window"
BINDING_WITHDRAWN = "withdrawn"
# A submitted offer that is not the one retained: never issued (or pruned while still unexpired,
# which cannot happen), or issued but submitted with different fields.
BINDING_OFFER_UNKNOWN = "offer_unknown"
BINDING_OFFER_TAMPERED = "offer_tampered"

PROBLEM_BAD_REQUEST = "mission_panel_bad_request"
# Two refusals that must never be conflated. STALE: the OFFER CHANGED (the Mission, revision,
# digest, scope, delivery targets or repository differs from what was shown, or the proposal was
# withdrawn or replaced). EXPIRED: nothing changed and only TIME passed beyond the window that was
# shown. A delayed click on an unchanged offer is neither, and succeeds while the window holds.
PROBLEM_STALE_DISPLAY = "mission_panel_display_stale"
PROBLEM_OFFER_EXPIRED = "mission_panel_offer_expired"
PROBLEM_ACTION_NOT_OFFERED = "mission_panel_action_not_offered"
PROBLEM_CAPABILITY_UNAVAILABLE = "mission_panel_withdrawal_key_unavailable"
PROBLEM_RUN_UNAVAILABLE = "mission_panel_run_unavailable"

_TURN_ID = re.compile(r"ct-[0-9a-f]{32}")
_CONVERSATION_ID = re.compile(r"cv-[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")

_ATTESTATION_KEYS = ("state", "attested_at", "relayed_reply", "binding",
                     "outcome", "provenance_label", "residual_risk",
                     "evidence_status")
# (c): what a window shows of the relayed non-approval outcome.
_OUTCOME_KEYS = ("kind", "state", "reason", "attested_at", "binding", "outcome",
                 "provenance_label", "residual_risk", "evidence_status")
_RUN_KEYS = (
    "phase", "lifecycle_state", "hold", "hold_statement", "paused",
    "pause_statement", "external_work_suspended", "outcome",
    "first_observation", "latest_observation", "target_stopped_observed",
    "unattributed_observation", "pending_proof", "verification",
    "engineering_verified", "cancel", "mission_state_progress",
    "progress_note", "baseline", "delivery", "delivery_authority",
)


class PanelRefusal(Exception):
    """A refusal with a ``mission_panel_*`` problem code."""

    def __init__(self, problem, reason, **details):
        super(PanelRefusal, self).__init__(reason)
        self.problem = problem
        self.reason = reason
        self.details = details


def _refuse(problem, reason, **details):
    raise PanelRefusal(problem, reason, **details)


def _bad(reason):
    _refuse(PROBLEM_BAD_REQUEST, reason)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class PanelService(object):

    def __init__(self, surface, journal, conversation, registry, clock,
                 bridge=None, repository_url=None, recover_result=None,
                 progression=None):
        self._surface = surface
        self._journal = journal
        self._conversation = conversation
        self._registry = registry
        self._clock = clock
        self._bridge = bridge
        self._repository_url = repository_url
        self._recover_result = recover_result
        # ``progression`` is a zero-argument callable answering whether a
        # progression runtime is passing for this panel (see
        # ``target_runtime.mission_progress_status.probe``), or None when this
        # panel cannot start work and so has nothing to report. It is evidence
        # about a PROCESS; a Mission's own record is the only evidence about a
        # Mission.
        self._progression = progression
        self._guard = threading.Lock()
        self._request_locks = {}
        # The withdrawal keys the surface shows ONCE. In memory only: never
        # written anywhere, lost on restart (and said so on screen).
        self._capabilities = {}

    def start(self):
        """Settle what a previous process left in flight as UNCERTAIN."""
        return self._conversation.start()

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _request_ref(value):
        if not isinstance(value, str) or store_module.request_ref_problem(
            value
        ) is not None:
            _bad("request_ref must be an lr- reference issued by this panel")
        return value

    @staticmethod
    def _turn_id(value):
        if not isinstance(value, str) or not _TURN_ID.fullmatch(value):
            _bad("turn_id must be a ct- turn id")
        return value

    @staticmethod
    def _conversation_id(value, allow_none=False):
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or not _CONVERSATION_ID.fullmatch(value):
            _bad("conversation_id must be a cv- conversation id")
        return value

    @staticmethod
    def _digest(value):
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            _bad("proposal_digest_sha256 must be 64 lowercase hex characters")
        return value

    def _binding_args(self, mission_id, revision, digest):
        if not isinstance(mission_id, str) or not mission_id:
            _bad("mission_id must be the displayed Mission id")
        if not _is_int(revision) or revision < 1:
            _bad("revision must be the displayed positive revision")
        return mission_id, revision, self._digest(digest)

    def _lock_for(self, request_ref):
        with self._guard:
            return self._request_locks.setdefault(request_ref, threading.RLock())

    # -- reading the records ---------------------------------------------------

    def _entries(self):
        return dict((e["request_ref"], e)
                    for e in self._surface.list_requests()["requests"])

    def _readiness(self, status):
        """The bridge's read-only verification readiness, only while a run is
        being watched; None when it cannot be read."""
        run = status.get("run") or {}
        if self._bridge is None or run.get("phase") not in (
            surface_module.RUN_PHASE_RUNNING, surface_module.RUN_PHASE_PENDING_PROOF
        ) or run.get("paused"):
            return None
        try:
            answer = self._bridge.verification_readiness(status["mission_id"])
        except Exception:
            return None
        return {"ready": answer["ready"], "target_stopped": answer["target_stopped"],
                "waiting_for": answer["waiting_for"]}

    def progression(self):
        """The progression runtime's liveness for this panel, or None when
        this panel has no run. Never raises: a probe that fails is reported as
        unreadable, never as running."""
        if self._bridge is None or self._progression is None:
            return None
        try:
            answer = self._progression()
        except Exception as exc:
            return {"state": "unreadable", "passes": None, "interval_seconds": None,
                    "last_pass_age_seconds": None,
                    "detail": "the progression probe failed (%s)" % type(exc).__name__}
        return dict((k, answer.get(k)) for k in (
            "state", "detail", "passes", "interval_seconds", "last_pass_age_seconds"))

    def _runnable_problem(self, proposal):
        return None if proposal is None else draft_module.runnable_problem(proposal)

    def _current(self, entry, status):
        """The Mission, revision, proposal and digest a window should DISPLAY
        and bind a decision to: Mission Core's current one while the proposal
        awaits a decision, otherwise what was recorded."""
        try:
            presented = self._surface.present(entry["request_ref"])
            return {"mission_id": presented["mission_id"],
                    "revision": presented["revision"],
                    "proposal": presented["proposal"],
                    "proposal_digest_sha256": presented["proposal_digest_sha256"]}
        except surface_module.LocalRequestRefusal:
            pass
        observation = status.get("mission_observation") or {}
        attested = status.get("attested_approval") or {}
        # (c): an outcome binds the revision and digest the person decided on, too.
        binding = (status.get("attested_outcome") or {}).get("binding") or attested.get(
            "binding") or {}
        revision = binding.get("revision") or observation.get("revision") or 1
        digest = binding.get("proposal_digest_sha256") or entry[
            "proposal_digest_sha256"]
        proposal = (entry["proposal"] if digest == entry["proposal_digest_sha256"]
                    else None)
        return {"mission_id": entry["mission_id"], "revision": revision,
                "proposal": proposal, "proposal_digest_sha256": digest}

    def _start_cause(self, entry, status):
        """Why an in-doubt Start is in doubt, WHEN this exact attempt left a
        bounded diagnostic: the bridge's read-only projection of what the failed
        start retained in the child workspace (see ``start_cause``). Derived at
        view time and NEVER stored, so it needs no field in Mission Core or in
        this journal and survives a reload and a second window. Asked only
        while the intent is recorded and no receipt exists (the start's outcome
        is unknown, or the run later stopped durably with none), because
        otherwise the start did not fail. If the read itself fails the view
        says the same as when no diagnostic binds: nothing is invented."""
        run = status.get("run")
        if self._bridge is None or not run:
            return None
        raw = run.get("run") or {}
        if raw.get("intent") is None or raw.get("receipt") is not None:
            return None
        if run.get("phase") not in (surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                                    surface_module.RUN_PHASE_BLOCKED):
            return None
        try:
            return copy.deepcopy(self._bridge.startup_cause(entry["mission_id"]))
        except Exception:
            return {"state": "NONE", "rejected": 0}

    def _view(self, entry, status=None, with_start_cause=False):
        status = status or self._surface.status(entry["request_ref"])
        request_ref = entry["request_ref"]
        current = self._current(entry, status)
        lineage = self._journal.lineage(request_ref)
        proposal = current["proposal"] or entry["proposal"]
        # The words name the digest of the revision that is of record. The surface's entry still holds the
        # digest it was submitted with, which is a different revision once Mission Core's proposal was
        # revised by an authenticated route outside this panel.
        derived = lifecycle_module.derive(
            dict(status, proposal_digest_sha256=current["proposal_digest_sha256"]),
            lineage, self._clock(),
            capability_held=request_ref in self._capabilities,
            run_available=self._bridge is not None,
            runnable_problem=self._runnable_problem(proposal),
            verification_ready=self._readiness(status),
            progression=self.progression())
        attested = status.get("attested_approval")
        outcome = status.get("attested_outcome")
        run = status.get("run")
        return {
            "ok": True, "request_ref": request_ref,
            "mission_id": current["mission_id"], "revision": current["revision"],
            "proposal": copy.deepcopy(current["proposal"]),
            "proposal_digest_sha256": current["proposal_digest_sha256"],
            "created_at": entry["created_at"],
            "lifecycle": derived, "lineage": lineage,
            "cancellation": copy.deepcopy(entry["cancellation"]),
            "attestation": None if attested is None else dict(
                (k, copy.deepcopy(attested.get(k))) for k in _ATTESTATION_KEYS
                if k in attested),
            "outcome": None if outcome is None else dict(
                (k, copy.deepcopy(outcome.get(k))) for k in _OUTCOME_KEYS if k in outcome),
            "run": None if run is None else dict(
                (k, copy.deepcopy(run.get(k))) for k in _RUN_KEYS if k in run),
            "start_cause": (self._start_cause(entry, status)
                            if with_start_cause else None),
            "delivery_authority": surface_module.DELIVERY_AUTHORITY,
        }

    def request_view(self, request_ref):
        self._request_ref(request_ref)
        entry = self._entries().get(request_ref)
        if entry is None:
            self._surface.status(request_ref)      # the surface's own refusal
        return self._view(entry, with_start_cause=True)

    def _summary(self, entry):
        status = self._surface.status(entry["request_ref"])
        view = self._view(entry, status)
        # R12: the two action times the per-request view already holds, so a page that reads only the overview can
        # say which requests have a RECORDED approval attestation or withdrawal and when. They say THAT a record was
        # written and WHEN, not who acted: attested_at is stamped when the attestation record is first RESERVED,
        # before Mission Core applies it, so it does not say the approval took effect. None when the view holds none.
        # Nothing else is added.
        attested = view["attestation"]
        cancelled = view["cancellation"]
        return {
            "request_ref": view["request_ref"], "mission_id": view["mission_id"],
            "revision": view["revision"], "created_at": view["created_at"],
            "objective": (view["proposal"] or entry["proposal"])["objective"],
            "proposal_digest_sha256": view["proposal_digest_sha256"],
            "lifecycle": dict((k, view["lifecycle"][k]) for k in (
                "stage", "label", "terminal", "actions", "notice")),
            "attested_at": None if attested is None else attested.get(
                "attested_at"),
            "cancelled_at": None if cancelled is None else cancelled.get(
                "cancelled_at"),
        }

    def capabilities(self):
        label = surface_module.PROVENANCE_LABEL
        return {
            "run_available": self._bridge is not None,
            "progression": self.progression(),
            "repository_url": self._repository_url,
            "conversation": "restricted_read_only_fresh_turns",
            "delivery_authority": surface_module.DELIVERY_AUTHORITY,
            "decision": {
                "transport": label["principal_kind"], "proof": label["proof"],
                "reply": DECISION_WORD,
                "approval_valid_seconds": lifecycle_module.approval_valid_seconds(),
            },
            "scope_meanings": dict(draft_module.SCOPE_MEANINGS),
        }

    @staticmethod
    def _questions_view(turn):
        """What this panel version read from one turn's reply for questions."""
        if turn["state"] != journal_module.TURN_COMPLETED:
            return {"reporting": QUESTIONS_NOT_APPLICABLE, "items": [],
                    "problem": None}
        declared = turn.get("questions")
        if declared is None:
            return {"reporting": QUESTIONS_NOT_REPORTED, "items": [],
                    "problem": None}
        return {"reporting": QUESTIONS_READ, "items": list(declared["items"]),
                "problem": declared["problem"]}

    @classmethod
    def _awaiting(cls, turns):
        """The conversation's answer state, from canonical journal fields only.

        AWAITING YOUR ANSWER exactly when the newest COMPLETED turn declared
        questions, produced no draft, and no later turn exists (a later turn of
        any state, answered or failed or still reserved, ends it: that turn is
        named by ``latest_turn_state``, never guessed to be an answer). A local
        hide in any client is never a resolution; the journal is."""
        completed = [t for t in turns
                     if t["state"] == journal_module.TURN_COMPLETED]
        latest = turns[-1]
        if not completed:
            return {"awaiting_answer": False,
                    "questions_reporting": QUESTIONS_NOT_APPLICABLE,
                    "questions_problem": None, "awaiting_turn_id": None,
                    "awaiting_questions": [],
                    "latest_turn_state": latest["state"]}
        newest = completed[-1]
        view = cls._questions_view(newest)
        awaiting = bool(view["items"]) and newest["draft"] is None and (
            newest["sequence"] == latest["sequence"])
        return {"awaiting_answer": awaiting,
                "questions_reporting": view["reporting"],
                "questions_problem": view["problem"],
                "awaiting_turn_id": newest["turn_id"] if awaiting else None,
                "awaiting_questions": view["items"] if awaiting else [],
                "latest_turn_state": latest["state"]}

    def overview(self):
        listed = self._surface.list_requests(limit=MAX_OVERVIEW_REQUESTS)
        requests = [self._summary(entry) for entry in listed["requests"]]
        conversations = []
        for item in self._journal.recent_conversations(MAX_OVERVIEW_CONVERSATIONS):
            turns = self._journal.conversation(item["conversation_id"])
            item["first_words"] = turns[0]["user_text"][:FIRST_WORDS_CHARS]
            item.update(self._awaiting(turns))
            conversations.append(item)
        return {
            "ok": True, "server_now": self._clock(),
            "capabilities": self.capabilities(),
            "requests": requests, "requests_truncated": listed["truncated"],
            "conversations": conversations,
        }

    # -- the conversation ------------------------------------------------------

    def _turn_view(self, turn, linked, requests):
        draft = turn["draft"]
        submission = turn["submission"]
        if submission is not None:
            submission = dict(submission)
            submission["possible_matches"] = []
            if submission["state"] == journal_module.SUBMISSION_UNCERTAIN:
                # Reconciled from the existing records: matching proposals are
                # NAMED so a person can look; none is silently linked.
                submission["possible_matches"] = sorted(
                    ref for ref, e in requests.items()
                    if ref not in linked
                    and e["proposal_digest_sha256"] == draft["proposal_digest_sha256"]
                    and e["created_at"] >= submission["reserved_at"])
        outcome = turn["outcome"]
        return {
            "turn_id": turn["turn_id"], "conversation_id": turn["conversation_id"],
            "sequence": turn["sequence"], "created_at": turn["created_at"],
            "finished_at": turn["finished_at"], "state": turn["state"],
            "in_flight": turn["turn_id"] in self._registry.snapshot(),
            "user_text": turn["user_text"], "assistant_text": turn["assistant_text"],
            "provider_text": "untrusted",
            "outcome": None if outcome is None else {
                "status": outcome["status"], "error_code": outcome["error_code"],
                "detail": outcome["detail"]},
            "draft": None if draft is None else {
                "proposal": copy.deepcopy(draft["proposal"]),
                "proposal_digest_sha256": draft["proposal_digest_sha256"],
                "runnable_problem": draft_module.runnable_problem(draft["proposal"])},
            "draft_problem": turn["draft_problem"], "submission": submission,
            "questions": self._questions_view(turn),
        }

    def _linked_refs(self):
        return set(t["submission"]["request_ref"]
                   for t in self._journal.load()["turns"].values()
                   if t["submission"] and t["submission"]["request_ref"])

    def conversation_view(self, conversation_id):
        """The most recent turns that fit BOTH bounds: ``MAX_VIEW_TURNS`` turns and
        ``MAX_VIEW_BYTES`` of serialized answer, measured on the bytes that really cross the relay
        (``wire.encode``), never on character counts. Older turns stay in the journal and are never
        dropped silently: the answer carries how many it holds, how many it shows and how many it
        leaves out, and the window says so."""
        self._conversation_id(conversation_id)
        turns = self._journal.conversation(conversation_id)
        linked, requests = self._linked_refs(), self._entries()
        window = turns[-MAX_VIEW_TURNS:]
        kept, used = [], 0
        for turn in reversed(window):
            view = self._turn_view(turn, linked, requests)
            size = len(wire_module.encode(view))
            if kept and used + size > MAX_VIEW_BYTES:
                break                       # this turn and every older one are left out
            kept.append(view)
            used += size
        kept.reverse()
        return {
            "ok": True, "conversation_id": conversation_id,
            "turns_total": len(turns), "turns_shown": len(kept),
            "turns_omitted": len(turns) - len(kept),
            # WHICH limit left them out, so the window can say so: turns past the count bound, and turns
            # that would have taken the serialized text past the byte budget.
            "turns_omitted_by_turn_limit": len(turns) - len(window),
            "turns_omitted_by_byte_limit": len(window) - len(kept),
            "turns_truncated": len(kept) < len(turns),
            "view_limits": {"turns": MAX_VIEW_TURNS, "bytes": MAX_VIEW_BYTES},
            "turns": kept,
        }

    def send_turn(self, conversation_id, text):
        self._conversation_id(conversation_id, allow_none=True)
        turn = self._conversation.send(conversation_id, text)
        return {"ok": True, "turn": self._turn_view(turn, set(), {})}

    # -- proposal: a human binds a draft ------------------------------------------

    def submit_draft(self, turn_id, draft_digest_sha256, supersedes=None):
        self._turn_id(turn_id)
        digest = self._digest(draft_digest_sha256)
        if supersedes is not None:
            self._request_ref(supersedes)
        turn = self._journal.turn(turn_id)
        if turn["draft"] is None or turn["draft"]["proposal_digest_sha256"] != digest:
            # The journal states the precise refusal (no draft / not that draft).
            self._journal.reserve_submission(turn_id, digest, supersedes, self._clock())
        old_token = None
        if supersedes is not None:
            old_token = self._replaceable(supersedes)
        submission, created = self._journal.reserve_submission(
            turn_id, digest, supersedes, self._clock())
        if not created:
            return {"ok": True, "created": False, "submission": submission,
                    "request": self._submitted_view(submission)}
        self._registry.add(turn_id)
        try:
            return self._submit_reserved(turn, submission, old_token)
        finally:
            self._registry.discard(turn_id)

    def _replaceable(self, request_ref):
        """The key to withdraw the proposal a revision replaces, or None when
        it is already withdrawn. Refuses (before anything is reserved) when it
        cannot be replaced."""
        status = self._surface.status(request_ref)
        if status["surface_state"] == store_module.STATE_CANCELLED:
            return None
        entry = self._entries()[request_ref]
        view = self._view(entry, status)
        if "withdraw" not in view["lifecycle"]["actions"]:
            if request_ref not in self._capabilities and "decide" in view[
                "lifecycle"]["actions"]:
                _refuse(PROBLEM_CAPABILITY_UNAVAILABLE,
                        "this panel no longer holds the key to withdraw %s, so"
                        " it cannot be replaced from here" % request_ref)
            _refuse(PROBLEM_ACTION_NOT_OFFERED,
                    "only a pending proposal can be revised; %s is %s"
                    % (request_ref, view["lifecycle"]["stage"]),
                    stage=view["lifecycle"]["stage"])
        return self._capabilities[request_ref]

    def _submit_reserved(self, turn, submission, old_token):
        turn_id = turn["turn_id"]
        proposal = turn["draft"]["proposal"]
        try:
            if submission["supersedes"] is not None and old_token is not None:
                self._surface.cancel(submission["supersedes"], old_token)
                self._capabilities.pop(submission["supersedes"], None)
            outcome = self._surface.submit(proposal)
        except surface_module.LocalRequestRefusal as exc:
            self._journal.finish_submission(
                turn_id, journal_module.SUBMISSION_REFUSED, None,
                exc.problem, self._clock())
            raise
        except Exception:
            settled = self._journal.finish_submission(
                turn_id, journal_module.SUBMISSION_UNCERTAIN, None, None,
                self._clock())
            return {"ok": True, "created": True, "submission": settled,
                    "request": None}
        self._capabilities[outcome["request_ref"]] = outcome["control_capability"]
        settled = self._journal.finish_submission(
            turn_id, journal_module.SUBMISSION_SUBMITTED, outcome["request_ref"],
            None, self._clock())
        return {"ok": True, "created": True, "submission": settled,
                "request": self.request_view(outcome["request_ref"])}

    def _submitted_view(self, submission):
        if submission["state"] != journal_module.SUBMISSION_SUBMITTED:
            return None
        return self.request_view(submission["request_ref"])

    # -- decision -------------------------------------------------------------------

    def decision_offer(self, request_ref):
        """The exact offer a window should display beside the typed word. It is FROZEN and
        RETAINED: while an unexpired offer exists for this exact binding every window is shown
        that same one, with the absolute expiry it was issued with; after the expiry a new offer
        is issued with a new expiry. It records bookkeeping only and decides nothing."""
        self._request_ref(request_ref)
        presented = self._surface.present(request_ref)
        offer, _ = self._journal.freeze_offer({
            "request_ref": request_ref, "mission_id": presented["mission_id"],
            "revision": presented["revision"],
            "proposal_digest_sha256": presented["proposal_digest_sha256"],
            "repository_url": presented["proposal"]["repository_url"],
            "action_scope": sorted(presented["approved_action_scope"]),
            "delivery_targets": sorted(presented["approved_delivery_targets"]),
            "expires_at": presented["latest_expires_at"]}, self._clock())
        return {
            "ok": True, "request_ref": request_ref,
            "mission_id": offer["mission_id"], "revision": offer["revision"],
            "proposal": presented["proposal"],
            "proposal_digest_sha256": offer["proposal_digest_sha256"],
            "offer_digest_sha256": offer["offer_digest_sha256"],
            "approved_action_scope": offer["action_scope"],
            "approved_delivery_targets": offer["delivery_targets"],
            "repository_url": offer["repository_url"],
            "reply": DECISION_WORD,
            # The ABSOLUTE expiry, fixed when this offer was issued. A window shows it and submits
            # the offer WITH the decision; the approval binds exactly this value.
            "expires_at": offer["expires_at"], "issued_at": offer["issued_at"],
            "approval_valid_seconds": lifecycle_module.approval_valid_seconds(),
            "provenance": dict(presented["provenance_label"]),
            "residual_risk": presented["residual_risk"],
            "server_now": self._clock(), "delivery_authority": "none",
        }

    def _decided(self, request_ref, attested, idempotent):
        return {"ok": True,
                "decision": {"authorization_id": (attested["outcome"] or {}).get(
                    "authorization_id"), "idempotent": idempotent},
                "request": self.request_view(request_ref)}

    @staticmethod
    def _offer_args(expires_at, action_scope, delivery_targets, repository_url):
        """The parts of a displayed offer that the digest does not cover, validated."""
        if not _is_int(expires_at) or expires_at < 1:
            _bad("expires_at must be the displayed expiry, a positive integer")
        for name, value in (("action_scope", action_scope),
                            ("delivery_targets", delivery_targets)):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                _bad("%s must be the displayed list of names" % name)
        if repository_url is not None and not isinstance(repository_url, str):
            _bad("repository_url must be the displayed repository, or null")
        return expires_at, sorted(set(action_scope)), sorted(set(delivery_targets)), repository_url

    def decide(self, request_ref, mission_id, revision, proposal_digest_sha256, reply,
               offer_digest_sha256, expires_at, action_scope, delivery_targets, repository_url):
        """Record the person's typed decision on EXACTLY the offer they were shown.

        The window submits the offer it displayed: its display digest and every bound field.
        That offer was FROZEN and RETAINED when it was first shown, and the decision is compared
        with BOTH the retained record and the current canonical state before any authority write:

        - a submitted offer that was never issued, or whose fields differ from the retained
          record, is TAMPERING (``PROBLEM_STALE_DISPLAY``);
        - a different Mission, revision or digest, or a proposal withdrawn or replaced, is a
          STALE DISPLAY: the offer changed, the display is invalidated and never re-pointed;
        - while now < the retained expiry, approving the UNCHANGED offer succeeds however long
          the person took to read it (think time is not staleness), and the approval binds the
          RETAINED expiry and scope, never one re-derived from the clock now, so the window is
          never silently extended;
        - now >= the retained expiry is a genuine EXPIRY (``PROBLEM_OFFER_EXPIRED``): nothing
          changed and time passed.

        Two windows cannot produce two approvals: the surface records one decision per request
        and a second finds it."""
        self._request_ref(request_ref)
        mission_id, revision, digest = self._binding_args(
            mission_id, revision, proposal_digest_sha256)
        expires_at, action_scope, delivery_targets, repository_url = self._offer_args(
            expires_at, action_scope, delivery_targets, repository_url)
        offer_digest = self._digest(offer_digest_sha256)
        # The exact affirmative first, before anything is read or written: a
        # reply that is not exactly the word counts as nothing.
        if not isinstance(reply, str) or len(reply) > store_module.MAX_RELAYED_REPLY_CHARS \
                or not store_module.is_exact_affirmative(reply):
            raise surface_module.LocalRequestRefusal(
                surface_module.PROBLEM_NOT_AFFIRMATIVE,
                "the reply is not the exact word %r; nothing was recorded"
                % DECISION_WORD)
        with self._lock_for(request_ref):
            status = self._surface.status(request_ref)
            attested = status.get("attested_approval")
            if attested is not None and attested["state"] == store_module.ATTESTED_APPLIED:
                return self._decided(request_ref, attested, True)
            if attested is not None and attested["state"] in (
                store_module.ATTESTED_RESERVED, store_module.ATTESTED_HOLD
            ):
                return self.check_approval(request_ref)
            # 1. The submitted offer against the RETAINED one: the exact offer that was displayed.
            submitted = {
                "request_ref": request_ref, "mission_id": mission_id, "revision": revision,
                "proposal_digest_sha256": digest, "repository_url": repository_url,
                "action_scope": action_scope, "delivery_targets": delivery_targets,
                "expires_at": expires_at}
            retained = self._journal.offer(offer_digest)
            if retained is None:
                again = self._surface.status(request_ref).get("attested_approval")
                if again is not None and again["state"] == store_module.ATTESTED_APPLIED:
                    return self._decided(request_ref, again, True)       # another window won
                if expires_at <= self._clock():
                    self._expired(expires_at)           # an offer that has lapsed and been pruned
                self._stale(request_ref, mission_id, revision, digest, BINDING_OFFER_UNKNOWN)
            if any(retained[k] != v for k, v in submitted.items()):
                self._stale(request_ref, mission_id, revision, digest, BINDING_OFFER_TAMPERED)
            # 2. The Mission, revision and digest against the CURRENT record, and that the
            #    RETAINED expiry has not passed.
            try:
                binding = self._surface.binding_status(
                    request_ref, mission_id, revision, digest, retained["expires_at"])["binding"]
            except surface_module.LocalRequestRefusal as exc:
                if exc.problem != surface_module.PROBLEM_CANCELLED:
                    raise
                # Withdrawn (alone, or replaced by a revision): the proposal that was shown is no
                # longer the proposal of record. The display is invalid; the surface's own code is
                # kept so nothing is hidden.
                self._stale(request_ref, mission_id, revision, digest, BINDING_WITHDRAWN,
                            surface_problem=exc.problem)
            if binding != store_module.BINDING_MATCHES:
                again = self._surface.status(request_ref).get("attested_approval")
                if again is not None and again["state"] == store_module.ATTESTED_APPLIED:
                    return self._decided(request_ref, again, True)    # another window won
                if binding == store_module.BINDING_EXPIRED:
                    self._expired(retained["expires_at"])
                self._stale(request_ref, mission_id, revision, digest, binding)
            # 3. What the digest does not cover, against the CURRENT record.
            presented = self._surface.present(request_ref)
            mismatch = None
            if retained["action_scope"] != sorted(presented["approved_action_scope"]):
                mismatch = BINDING_SCOPE_MISMATCH
            elif retained["delivery_targets"] != sorted(presented["approved_delivery_targets"]):
                mismatch = BINDING_TARGETS_MISMATCH
            elif retained["repository_url"] != presented["proposal"]["repository_url"]:
                mismatch = BINDING_REPOSITORY_MISMATCH
            elif retained["expires_at"] > presented["latest_expires_at"]:
                mismatch = BINDING_EXPIRY_BEYOND_WINDOW
            if mismatch is not None:
                self._stale(request_ref, mission_id, revision, digest, mismatch)
            # 4. Relay the RETAINED offer: its expiry and its scope, exactly.
            try:
                self._surface.attest_approval(
                    request_ref, mission_id, revision, digest, retained["action_scope"],
                    retained["delivery_targets"], retained["expires_at"], reply, RELAY_REF)
            except surface_module.LocalRequestRefusal as exc:
                if exc.problem != surface_module.PROBLEM_ATTESTATION_CONFLICT:
                    raise
                # Another window applied the approval with its own expiry.
                again = self._surface.status(request_ref).get("attested_approval")
                if again is None or again["state"] != store_module.ATTESTED_APPLIED:
                    raise
                return self._decided(request_ref, again, True)
            applied = self._surface.status(request_ref)["attested_approval"]
            return self._decided(request_ref, applied, False)

    def _expired(self, expires_at, done="approved"):
        """The window the person was shown is over. Nothing about the offer changed, so the
        display is NOT invalid: reading the offer again gives a fresh window to decide in."""
        _refuse(PROBLEM_OFFER_EXPIRED,
                "the decision window you were shown has passed. Nothing about the proposal"
                " changed and nothing was %s. Read the offer again for a new window" % done,
                expires_at=expires_at, server_now=self._clock(), display_invalid=False)

    def _stale(self, request_ref, mission_id, revision, digest, binding, done="approved",
               **extra):
        current = None
        try:
            presented = self._surface.present(request_ref)
            current = {"mission_id": presented["mission_id"],
                       "revision": presented["revision"],
                       "proposal_digest_sha256": presented["proposal_digest_sha256"]}
        except surface_module.LocalRequestRefusal:
            pass
        _refuse(PROBLEM_STALE_DISPLAY,
                "the proposal shown is not the one Mission Core now holds (%s);"
                " nothing was %s. Read the current proposal and decide on"
                " that" % (binding, done),
                binding=binding, display_invalid=True, current=current,
                displayed={"mission_id": mission_id, "revision": revision,
                           "proposal_digest_sha256": digest}, **extra)

    def check_approval(self, request_ref):
        """Reconcile an approval of unknown outcome against Mission Core's own
        record. It never applies an approval again and never mints one."""
        self._request_ref(request_ref)
        with self._lock_for(request_ref):
            attested = self._surface.status(request_ref).get("attested_approval")
            if attested is None:
                _refuse(PROBLEM_ACTION_NOT_OFFERED,
                        "there is no approval of unknown outcome to check")
            binding = attested["binding"]
            self._surface.attest_approval(
                request_ref, binding["mission_id"], binding["revision"],
                binding["proposal_digest_sha256"], binding["approved_action_scope"],
                binding["approved_delivery_targets"], binding["expires_at"],
                attested["relayed_reply"], RELAY_REF)
            applied = self._surface.status(request_ref)["attested_approval"]
            return self._decided(request_ref, applied, True)

    # -- the non-approval outcomes (owner decision (c)) --------------------------------
    #
    # The person's decline, rejection or change request, relayed on EXACTLY the offer they
    # were shown, compared with the retained offer and the current record as ``decide``
    # compares it. Each makes its one surface call from its own function. Mission Core
    # validates and records the result; this panel holds no context and applies nothing.

    def decline(self, request_ref, mission_id, revision, proposal_digest_sha256,
                offer_digest_sha256, expires_at, action_scope, delivery_targets,
                repository_url):
        """Record the person's DECLINE: Mission Core's existing non-terminal DENY."""
        submitted, offer_digest = self._outcome_args(
            request_ref, mission_id, revision, proposal_digest_sha256, offer_digest_sha256,
            expires_at, action_scope, delivery_targets, repository_url)
        with self._lock_for(request_ref):
            shown = self._shown_offer(submitted, offer_digest, store_module.OUTCOME_DECLINE)
            result = self._surface.attest_decline(
                request_ref, shown["mission_id"], shown["revision"],
                shown["proposal_digest_sha256"], RELAY_REF)
        return self._outcome_decided(request_ref, result)

    def reject(self, request_ref, mission_id, revision, proposal_digest_sha256,
               offer_digest_sha256, expires_at, action_scope, delivery_targets,
               repository_url):
        """Record the person's REJECTION: the terminal state REJECTED."""
        submitted, offer_digest = self._outcome_args(
            request_ref, mission_id, revision, proposal_digest_sha256, offer_digest_sha256,
            expires_at, action_scope, delivery_targets, repository_url)
        with self._lock_for(request_ref):
            shown = self._shown_offer(submitted, offer_digest, store_module.OUTCOME_REJECT)
            result = self._surface.attest_rejection(
                request_ref, shown["mission_id"], shown["revision"],
                shown["proposal_digest_sha256"], RELAY_REF)
        return self._outcome_decided(request_ref, result)

    def request_changes(self, request_ref, mission_id, revision, proposal_digest_sha256,
                        offer_digest_sha256, expires_at, action_scope, delivery_targets,
                        repository_url, reason):
        """Record the person's CHANGE REQUEST and its readable reason (checked first,
        before anything is read or written)."""
        surface_module.require_readable_reason(reason)
        submitted, offer_digest = self._outcome_args(
            request_ref, mission_id, revision, proposal_digest_sha256, offer_digest_sha256,
            expires_at, action_scope, delivery_targets, repository_url)
        with self._lock_for(request_ref):
            shown = self._shown_offer(submitted, offer_digest,
                                      store_module.OUTCOME_REQUEST_CHANGES)
            result = self._surface.attest_change_request(
                request_ref, shown["mission_id"], shown["revision"],
                shown["proposal_digest_sha256"], reason, RELAY_REF)
        return self._outcome_decided(request_ref, result)

    def _outcome_args(self, request_ref, mission_id, revision, proposal_digest_sha256,
                      offer_digest_sha256, expires_at, action_scope, delivery_targets,
                      repository_url):
        self._request_ref(request_ref)
        mission_id, revision, digest = self._binding_args(
            mission_id, revision, proposal_digest_sha256)
        expires_at, action_scope, delivery_targets, repository_url = self._offer_args(
            expires_at, action_scope, delivery_targets, repository_url)
        submitted = {
            "request_ref": request_ref, "mission_id": mission_id, "revision": revision,
            "proposal_digest_sha256": digest, "repository_url": repository_url,
            "action_scope": action_scope, "delivery_targets": delivery_targets,
            "expires_at": expires_at}
        return submitted, self._digest(offer_digest_sha256)

    def _shown_offer(self, submitted, offer_digest, kind):
        """Called under the request lock. The offer the person was shown, checked as
        ``decide`` checks it: the retained offer, then Mission Core's current record. When
        this same outcome is already held for the request, the submitted binding is returned
        unchecked: the surface then only returns or reconciles what it holds, and refuses a
        different content."""
        request_ref = submitted["request_ref"]
        mission_id, revision = submitted["mission_id"], submitted["revision"]
        digest = submitted["proposal_digest_sha256"]
        held = self._surface.status(request_ref).get("attested_outcome")
        if held is not None and held["kind"] == kind and held["state"] != (
            store_module.ATTESTED_REFUSED
        ):
            return submitted
        done = "recorded"
        retained = self._journal.offer(offer_digest)
        if retained is None:
            if submitted["expires_at"] <= self._clock():
                self._expired(submitted["expires_at"], done)
            self._stale(request_ref, mission_id, revision, digest, BINDING_OFFER_UNKNOWN, done)
        if any(retained[k] != v for k, v in submitted.items()):
            self._stale(request_ref, mission_id, revision, digest, BINDING_OFFER_TAMPERED, done)
        try:
            binding = self._surface.binding_status(
                request_ref, mission_id, revision, digest, retained["expires_at"])["binding"]
        except surface_module.LocalRequestRefusal as exc:
            if exc.problem != surface_module.PROBLEM_CANCELLED:
                raise
            self._stale(request_ref, mission_id, revision, digest, BINDING_WITHDRAWN, done,
                        surface_problem=exc.problem)
        if binding != store_module.BINDING_MATCHES:
            if binding == store_module.BINDING_EXPIRED:
                self._expired(retained["expires_at"], done)
            self._stale(request_ref, mission_id, revision, digest, binding, done)
        presented = self._surface.present(request_ref)
        if retained["action_scope"] != sorted(presented["approved_action_scope"]):
            self._stale(request_ref, mission_id, revision, digest, BINDING_SCOPE_MISMATCH, done)
        if retained["delivery_targets"] != sorted(presented["approved_delivery_targets"]):
            self._stale(request_ref, mission_id, revision, digest, BINDING_TARGETS_MISMATCH,
                        done)
        if retained["repository_url"] != presented["proposal"]["repository_url"]:
            self._stale(request_ref, mission_id, revision, digest,
                        BINDING_REPOSITORY_MISMATCH, done)
        return retained

    def _outcome_decided(self, request_ref, result):
        return {"ok": True,
                "outcome": {"kind": result["kind"],
                            "resulting_state": result["resulting_state"],
                            "idempotent": result["idempotent"]},
                "request": self.request_view(request_ref)}

    # -- withdrawal --------------------------------------------------------------------

    def withdraw(self, request_ref, mission_id, revision, proposal_digest_sha256):
        self._request_ref(request_ref)
        mission_id, revision, digest = self._binding_args(
            mission_id, revision, proposal_digest_sha256)
        with self._lock_for(request_ref):
            binding = self._surface.binding_status(
                request_ref, mission_id, revision, digest)["binding"]
            token = self._capabilities.get(request_ref)
            if token is None:
                _refuse(PROBLEM_CAPABILITY_UNAVAILABLE,
                        "the key to withdraw this proposal was shown once and is"
                        " held only in memory by the session that submitted it;"
                        " this panel no longer holds it, so the proposal cannot be"
                        " withdrawn from here")
            if binding != store_module.BINDING_MATCHES:
                self._stale(request_ref, mission_id, revision, digest, binding)
            self._surface.cancel(request_ref, token)
            self._capabilities.pop(request_ref, None)
            return {"ok": True, "request": self.request_view(request_ref)}

    # -- the run -----------------------------------------------------------------------

    def run(self, request_ref, command):
        self._request_ref(request_ref)
        if not isinstance(command, str) or command not in RUN_COMMANDS:
            _bad("command must be one of %s" % ", ".join(RUN_COMMANDS))
        if self._bridge is None:
            _refuse(PROBLEM_RUN_UNAVAILABLE,
                    "this panel was started without a run; it can decide a"
                    " proposal but cannot start the work")
        with self._lock_for(request_ref):
            view = self.request_view(request_ref)
            offered = view["lifecycle"]["actions"]
            if command not in offered:
                _refuse(PROBLEM_ACTION_NOT_OFFERED,
                        "%s is not offered for a proposal that is %s"
                        % (command, view["lifecycle"]["stage"]),
                        stage=view["lifecycle"]["stage"], offered=offered)
            if command == "verify":
                readiness = self._bridge.verification_readiness(view["mission_id"])
                if not (readiness["ready"] or readiness["target_stopped"]):
                    _refuse(PROBLEM_ACTION_NOT_OFFERED,
                            "verification is not offered yet: waiting for %s"
                            % readiness["waiting_for"],
                            stage=view["lifecycle"]["stage"], offered=[])
                self._surface.run_command(
                    request_ref, "verify", reported_result=readiness["reported_result"])
            else:
                self._surface.run_command(request_ref, command)
            return {"ok": True, "request": self.request_view(request_ref)}

    def result(self, request_ref):
        self._request_ref(request_ref)
        view = self.request_view(request_ref)
        if "result" not in view["lifecycle"]["actions"]:
            _refuse(PROBLEM_ACTION_NOT_OFFERED,
                    "a result is retrievable only for a verified run; this one is %s"
                    % view["lifecycle"]["stage"], stage=view["lifecycle"]["stage"])
        recover = self._recover_result
        if recover is None:
            from target_runtime import legacy_result
            recover = legacy_result.recover_result
        recovered = self._surface.read_result(request_ref, recover)
        answer = {
            "ok": True, "request_ref": request_ref,
            "verified_result": recovered["verified_result"],
            "recoverable": recovered["recoverable"],
            "delivery_authority": surface_module.DELIVERY_AUTHORITY,
        }
        if not recovered["recoverable"]:
            answer["reason"] = recovered.get("reason")
            return answer
        for key in ("result", "review"):
            text = recovered[key + "_text"]
            answer[key + "_text"] = text[:MAX_RESULT_TEXT_CHARS]
            answer[key + "_truncated"] = len(text) > MAX_RESULT_TEXT_CHARS
        answer.update({
            "task_id": recovered["task_id"],
            "result_digest_sha256": recovered["binding"]["result_digest_sha256"],
            "review_digest_sha256": recovered["binding"]["review_digest_sha256"],
            "review_evidence": recovered["review_evidence"],
            "delivered": recovered["delivered"],
        })
        return answer
