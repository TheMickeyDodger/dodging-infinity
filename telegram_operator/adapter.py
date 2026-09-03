"""Adapter orchestration: authenticated polling, serialized dispatch.

Two threads only: the POLLER long-polls Telegram, authenticates every
update on its identity envelope before touching content, and persists
accepted state; the single WORKER serializes every Codex Gateway turn
for the one configured repository, so no two turns ever resume one
Codex session concurrently and gateway work never blocks polling.

Ordering and durability rules enforced here:

- Authority-bearing state (approval creation, one-shot consumption,
  in-flight dispatch markers) is durably persisted BEFORE any external
  action that depends on it.
- PERSISTED and ACTIONABLE are distinct states for a plan approval:
  the record is persisted before the plan message is sent, but its
  ``plan_message_id`` is None — refused by the explicit
  unbound-plan-message guard in ``approval.evaluate_callback`` (a
  None/None comparison against a callback that omits its message id
  would pass tautologically) — until the send outcome proves the
  COMPLETE plan text was displayed (every expected chunk delivered,
  zero characters truncated). An actionable approval therefore binds exactly the
  complete plan content actually displayed and nothing undisplayed;
  truncated, partial, failed, or unverifiable delivery voids the
  record with a user-facing explanation, and a plan too long to
  display completely is refused before any record exists.
- The Telegram update offset advances ONLY AFTER the state accepted
  from that update is durably persisted. For a DENIED update the only
  accepted — and only persisted — state is the transport update
  offset itself (intended, so a hostile update cannot wedge the poll
  loop); no content, intent, approval, work, or session state from an
  unauthorized sender is ever parsed or stored.
- Worker shutdown uses a queue sentinel with a blocking get — pacing
  comes from Telegram long polling, and poll-failure recovery uses
  capped exponential backoff (hard constants below).
- On restart, dispatched-but-unconfirmed work is reported honestly as
  interrupted/ambiguous and is NEVER automatically replayed.

The adapter talks ONLY to the injected Telegram API client (through
the provider-neutral human-interaction seam) and to the Codex Gateway;
it never imports or invokes any orchestration machinery and never
touches orchestration state on disk.
"""

import queue
import sys
import threading
import time

from codex_gateway import build_request as gateway_build_request
from codex_gateway import submit as gateway_submit
from codex_gateway import role_turn as role_turn_module
from codex_gateway.contract import STATUS_COMPLETED
from human_interaction import (
    EVENT_MESSAGE,
    SEND_APPLIED,
    SEND_DEFINITE_ZERO,
    Control,
)
from operator_session import CodexOperatorSession, FunctionOperatorSession

from telegram_operator import approval as approval_module
from telegram_operator import protocol, state as state_module
from telegram_operator import mission as mission_module
from telegram_operator.interaction import TelegramHumanInteractionAdapter
from workflow_authority import digest as wa_digest
from workflow_authority import record as wa_record
from workflow_authority import store as workflow_store_module

# --- Hard constants, never derived from input ---------------------------
# Capped exponential backoff between FAILED polls (a deadline firing on
# an idle long poll is a normal empty poll, not a failure).
POLL_FAILURE_BACKOFF_BASE_SECONDS = 1
POLL_FAILURE_BACKOFF_CEILING_SECONDS = 60

CALLBACK_APPROVE_PREFIX = "a:"
CALLBACK_REJECT_PREFIX = "r:"

# The exact header prepended to every displayed plan. It consumes
# characters against the Telegram chunk cap alongside the plan body, so
# display-completeness is always computed over the FULL sent text.
PLAN_MESSAGE_HEADER = (
    "PLAN (approve or reject with the buttons; typed"
    " text cannot approve):\n"
)

_WORKER_SENTINEL = object()


RESULT_MESSAGE_HEADER = "Mission COMPLETED and verified."

# The ONLY envelope kind the FRESH planning turn (route (b)) may
# answer with: a v2-only Mission Authorization. Anything else is
# refused `wrong_kind:…` and arms nothing — there is NO fallback to
# the v1 plan path from the fresh planning turn (that path belongs to
# the LEGACY turn). Named so OPERATOR_PROTOCOL.md's stated answer set
# can be pinned set-equal to what the adapter actually accepts.
PLANNING_TURN_ACCEPTED_KINDS = frozenset(
    (protocol.KIND_MISSION_AUTHORIZATION,)
)


def _render_target_line(entry):
    """The exact target identity line for /status and the result
    message (I5 D5/G3): names the canonical URL and the issue/PR, or
    says repository-only. Composed from durable record fields only."""
    target = entry["target"]
    issue = target["issue_or_pr"]
    if issue is None:
        return "target: %s (repository-only)" % target["canonical_url"]
    return "target: %s (%s #%d)" % (
        target["canonical_url"], issue["kind"], issue["number"]
    )


# DI-REMOTE-3 I3: the bot-owned result placeholder.
#
# The text is a PURE function of the workflow id — no clock, no attempt
# counter, no "as of now". Two reasons, both load-bearing:
#   * strategy §1.3 REQUIRES the text to carry the workflow_id, because
#     an `indefinite` send may leave a real message on screen and this
#     id is the only handle a human has for identifying that object;
#   * `text_digest` binds this exact text and is written AHEAD of the
#     send (LEAD RULING L-1), so a text that varied between renders
#     would make the recorded digest describe something that is not on
#     screen.
PLACEHOLDER_MESSAGE_HEADER = "Mission result placeholder"


def render_placeholder_text(workflow_id):
    """The exact placeholder text. Deterministic; id-carrying."""
    return (
        "%s\nworkflow: %s\n\nThe verified mission result will appear"
        " in THIS message when the mission completes. This message"
        " grants no commit, push, PR, tag, release, or deploy"
        " authority." % (PLACEHOLDER_MESSAGE_HEADER, workflow_id)
    )


def render_result_text(entry):
    """The verified-result message text. R-1: a PURE function of
    ``(RESULT_MESSAGE_HEADER, target fields, verified_result.summary)``
    and NOTHING else.

    No clock, no "as of now", no attempt counter, no delivery
    timestamp. If the payload differed between attempts, a replayed
    edit would SUCCEED a second time instead of returning
    message-not-modified — which would destroy the R-2 proof that the
    bound object already holds exactly the intended content.

    This is the single renderer for BOTH delivery lanes: the legacy
    at-most-once ``sendMessage`` path and the edit path produce
    byte-identical text, so the edit path cannot drift from what
    legacy records already received.
    """
    return (
        "%s\n%s\n\nVERIFIED RESULT:\n"
        "%s\n\nDelivery is separately human-gated; this message"
        " grants no commit, push, PR, tag, release, or deploy"
        " authority." % (
            RESULT_MESSAGE_HEADER,
            _render_target_line(entry),
            entry["verified_result"]["summary"],
        )
    )


def _placeholder_is_claimable(placeholder):
    """True when a placeholder may be claimed for a send attempt.

    The CLOSED selection set, and it is the terminality guarantee:
    `indefinite` is TERMINAL and is never claimable by any pass after
    any restart (a retry from it is the duplicate-placeholder
    generator R-5 names); `bound` is terminal for binding; `sending`
    is claimed by nobody (a crash-orphaned one fails closed at
    startup); and a null placeholder is the LEGACY lane, never
    fabricated into a request.
    """
    if placeholder is None:
        return False
    return placeholder.get("state") in (
        wa_record.PLACEHOLDER_REQUIRED,
        wa_record.PLACEHOLDER_FAILED_UNSENT,
    )


def _render_placeholder_status(placeholder):
    """Honest /status phrasing for the placeholder binding.

    Every state gets its OWN branch and its own words. No state renders
    as "pending" or "delivered" unless it is that; the two TERMINAL
    degraded states name the workflow and tell the human what to do,
    because nothing will ever retry them. The unknown/unmapped
    fallback is kept and must remain reachable — a new state silently
    landing in it is a defect.
    """
    if placeholder is None:
        # Plan §1.1: NOT "not needed". This record predates or sits
        # outside the placeholder architecture, and is delivered by the
        # legacy AT-MOST-ONCE path.
        return (
            "no result placeholder (legacy workflow: dispatch is"
            " ungated and the result is delivered AT-MOST-ONCE)"
        )
    state = placeholder.get("state")
    if state == wa_record.PLACEHOLDER_REQUIRED:
        return "result placeholder requested; not yet sent"
    if state == wa_record.PLACEHOLDER_SENDING:
        return (
            "result placeholder send IN FLIGHT (intent written ahead;"
            " outcome not yet recorded)"
        )
    if state == wa_record.PLACEHOLDER_BOUND:
        return "result placeholder bound to message %s" % (
            placeholder.get("message_id"),
        )
    if state == wa_record.PLACEHOLDER_FAILED_UNSENT:
        return (
            "result placeholder send failed with NO message created;"
            " it will be retried"
        )
    if state == wa_record.PLACEHOLDER_INDEFINITE:
        return (
            "result placeholder send outcome INDEFINITE: a placeholder"
            " message naming this workflow MAY exist in this chat and"
            " MAY be unbound. This is TERMINAL and is never retried"
            " automatically (a retry could post a second placeholder);"
            " the mission will not dispatch. Human recovery required."
        )
    if state == wa_record.PLACEHOLDER_UNBINDABLE:
        return (
            "result placeholder is UNBINDABLE: the bound message is"
            " gone or the binding does not match. TERMINAL; no"
            " replacement message is ever sent."
        )
    # Unknown/unmapped placeholder state: fail loud, never silently
    # reported as bound or pending.
    return "result placeholder state unrecognized (%r)" % state


def _render_delivery_status(delivery):
    """Honest /status phrasing for a verified result's delivery.

    DI-REMOTE-3 I5 (plan §6a.6): this used to say it "distinguishes
    the four durable cases". There are now EIGHT representable delivery
    states — the three LEGACY ones plus the five EDIT-path ones — plus
    the None (genuinely pending) case, and every one gets its own
    explicit branch below. No state is reported as "pending" or
    "delivered" unless it IS that, and the unknown/unmapped fallback
    stays reachable and fails loud.

    LEGACY lane (at-most-once `sendMessage`), phrasing UNCHANGED and
    pinned verbatim by T-X2:

      - None      -> genuinely pending: deliver_pending_results (which
                     selects result_delivery is None) will attempt it.
      - delivered -> confirmed delivered.
      - reserved  -> attempted, delivery UNCONFIRMED (crash between
                     reserve and send); NOT retried automatically.
      - partial   -> displayed INCOMPLETELY; NOT retried automatically
                     (a retry would re-display the chunks already seen).

    EDIT lane (exactly-once visible presentation, placeholder-bound
    workflows only):

      - edit_pending          -> the edit intent is durable; the
                                 outcome is not yet recorded.
      - delivered_by_edit     -> the bound object provably holds the
                                 intended text.
      - degraded_unbindable   -> R-3: the object is gone or the
                                 binding moved. TERMINAL, and NO
                                 replacement message is ever sent.
      - degraded_unrenderable -> the rendered result exceeds one
                                 Telegram message. TERMINAL for this
                                 revision; never chunked, never
                                 truncated.
      - edit_indefinite       -> the edit outcome is ambiguous; a
                                 bounded retry is safe because the
                                 edit is idempotent.
    """
    if delivery is None:
        return "recorded (delivery pending)"
    state = delivery.get("state")
    if state == wa_record.DELIVERY_DELIVERED:
        return "delivered"
    since = delivery.get("reserved_at")
    if state == wa_record.DELIVERY_PARTIAL:
        return (
            "recorded; delivered INCOMPLETELY (some chunks shown; not"
            " retried automatically, since t=%s)" % since
        )
    if state == wa_record.DELIVERY_RESERVED:
        return (
            "recorded; delivery attempted but UNCONFIRMED (not retried"
            " automatically, since t=%s)" % since
        )
    # --- the EDIT lane. Every additive key is read with .get: a
    # marker built IN-PROCESS has not passed the load-boundary
    # normalizer and carries only the three legacy keys (T-X6).
    problem = delivery.get("problem")
    if state == wa_record.DELIVERY_DELIVERED_BY_EDIT:
        return (
            "delivered by editing the bound placeholder message %s"
            " (exactly one visible message)"
            % (delivery.get("edited_message_id"),)
        )
    if state == wa_record.DELIVERY_EDIT_PENDING:
        return (
            "recorded; the edit intent is durable and its outcome is"
            " NOT yet recorded (it will be retried; editing a bound"
            " message is idempotent)"
        )
    if state == wa_record.DELIVERY_EDIT_INDEFINITE:
        return (
            "recorded; the edit outcome is AMBIGUOUS and will be"
            " retried (idempotent against the bound message)%s"
            % (": %s" % problem if problem else "")
        )
    if state == wa_record.DELIVERY_DEGRADED_UNBINDABLE:
        return (
            "recorded but NOT delivered: the bound placeholder message"
            " is gone or its binding no longer matches. TERMINAL — no"
            " replacement message is ever sent, so the result must be"
            " recovered by a human%s"
            % (" (%s)" % problem if problem else "")
        )
    if state == wa_record.DELIVERY_DEGRADED_UNRENDERABLE:
        return (
            "recorded but NOT delivered: the rendered result does not"
            " fit ONE Telegram message. TERMINAL for this revision —"
            " never chunked and never truncated%s"
            % (" (%s)" % problem if problem else "")
        )
    # Unknown/unmapped delivery state: fail loud, never silently
    # reported as delivered or pending.
    return "recorded; delivery state unrecognized (%r)" % state


# Fixed marker of the Runtime's durable verification-block receipt.
# DUPLICATED here (this module may not import the Runtime) and pinned
# equal to the Runtime's own constant by a cross-boundary test. The
# adapter stays store-only: it renders the recorded reason, calls
# nothing, and gains no authority.
_VERIFICATION_BLOCK_MARKER = "verification blocked"


def _latest_verification_block(entry):
    """The most recent recorded verification-block reason for a
    BLOCKED workflow, or None. Read from the durable receipts only
    (ruling R-4: a BLOCKED verification must be visible in /status,
    never a silent strand of a consumed approval)."""
    for receipt in reversed(entry["receipts"]):
        summary = receipt.get("bounded_summary", "")
        if receipt.get("kind") == "evidence" and summary.startswith(
            _VERIFICATION_BLOCK_MARKER
        ):
            return summary
    return None


# Fixed marker of the Runtime's durable recovery-block receipt (I5
# D-B4) and the unresolved-task sentinel — both DUPLICATED here
# (this module may not import the Runtime) and pinned equal to the
# Runtime's own constants by cross-boundary tests. Scoped readers
# per action, deliberately NOT a universal stop-reason mechanism
# (that is the deferred I3b).
_RECOVERY_BLOCK_MARKER = "recovery blocked"
_UNRESOLVED_TASK_ID = "unknown"


def _latest_recovery_block(entry):
    """The most recent recorded recovery-block reason for a BLOCKED
    workflow, or None (D-B4: a recovery-BLOCKED workflow says so
    with its reason). Durable receipts only; store-only posture."""
    for receipt in reversed(entry["receipts"]):
        summary = receipt.get("bounded_summary", "")
        if receipt.get("kind") == "evidence" and summary.startswith(
            _RECOVERY_BLOCK_MARKER
        ):
            return summary
    return None


def _bound_engine_task(entry):
    """The durably bound target-engine task identity, or None when
    the identity is absent or unresolved (D-B4: a reconciled
    workflow shows the bound identity; an unresolved one must never
    render a fake binding)."""
    engine = entry.get("target_engine")
    if not isinstance(engine, dict):
        return None
    task_id = engine.get("task_id")
    if not isinstance(task_id, str) or not task_id or task_id == (
        _UNRESOLVED_TASK_ID
    ):
        return None
    return task_id


def _render_observation_line(entry):
    """The actionable target-observation line for /status (I6 carried
    item). Returns None when there is nothing distinct to say; a string
    otherwise. An UNOBSERVABLE target (task_status recorded as null)
    reads distinctly from a healthy running one, so an indefinitely
    stuck target is visible rather than silently identical."""
    observation = entry["last_observation"]
    if observation is None:
        return None
    since = observation["observed_at"]
    if observation["task_status"] is None:
        return (
            "  target task NOT OBSERVABLE since t=%s — the target"
            " herd's task state cannot be read; the mission is not"
            " known to be progressing. Check the target herd." % since
        )
    return (
        "  target task %s (visibility %s) as of t=%s"
        % (observation["task_status"],
           observation["completeness"], since)
    )

_HELP_TEXT = (
    "Telegram Remote Operator.\n"
    "Send natural-language intent (or /mission <intent>) to get a"
    " bounded plan from the Codex Operator.\n"
    "Approve or reject a plan only with its inline buttons; typed text"
    " never approves anything.\n"
    "/status shows the current request state.\n"
    "No remote message can commit, push, tag, release, or deploy;"
    " delivery stays with the human, locally."
)


class Adapter(object):
    """One adapter instance: one bot, one allowlist, one repository."""

    def __init__(self, config, store, api, submit_fn=None,
                 build_request_fn=None, clock=None, failure_sleeper=None,
                 error_writer=None, workflow_store=None,
                 workflow_id_factory=None, mission_nonce_factory=None,
                 planning_turn_fn=None, operator_session=None,
                 interaction=None):
        self.config = config
        self.store = store
        self._api = api
        # The human-interaction seam. An explicit interaction is
        # authoritative; otherwise the Telegram implementation over the
        # injected client, which the ``api`` setter keeps in step.
        self._interaction_defaulted = interaction is None
        self._interaction = (
            interaction if interaction is not None
            else TelegramHumanInteractionAdapter(api, config.allowed_user_ids)
        )
        # The operator session seam. An explicit session wins; an
        # injected callable pair (either half may be absent — each
        # half defaults to the gateway independently) becomes a
        # function-backed session; otherwise the Codex-backed one.
        if operator_session is not None:
            self._session = operator_session
        elif submit_fn is not None or build_request_fn is not None:
            self._session = FunctionOperatorSession(
                build_request_fn or gateway_build_request,
                submit_fn or gateway_submit,
            )
        else:
            self._session = CodexOperatorSession()
        self._clock = clock or time.time
        self._failure_sleeper = failure_sleeper or time.sleep
        self._error_writer = error_writer or sys.stderr.write
        self._state_lock = threading.Lock()
        self._document = store.load()
        self._work_signals = queue.Queue()
        self._stopping = False
        self._last_poll_problem = None
        # The DI-REMOTE-2 workflow authority store lives beside the
        # adapter state; every load-modify-save cycle on it holds the
        # cross-process store flock (the Runtime writes it too).
        self.workflow_store = workflow_store or (
            workflow_store_module.WorkflowStore(store.directory)
        )
        self._workflow_id_factory = (
            workflow_id_factory or mission_module._default_workflow_id_factory
        )
        self._mission_nonce_factory = mission_nonce_factory
        # The PRE-RECORD fresh restrictive planning turn (I2): the
        # ONLY path that can produce an armable Mission Authorization.
        # Production wires codex_gateway.role_turn.run_planning_turn —
        # a distinct fresh read-only process with no session parameter
        # anywhere; tests inject a hermetic fake here.
        self._planning_turn = (
            planning_turn_fn or role_turn_module.run_planning_turn
        )

    # -- persistence helpers --------------------------------------------

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        # A post-construction rebind reaches the DEFAULT interaction
        # too; an explicitly injected interaction is left untouched.
        self._api = value
        if self._interaction_defaulted:
            self._interaction.api = value

    def _save(self):
        """Persist the current document (caller holds the lock)."""
        self.store.save(self._document)

    # -- startup recovery ------------------------------------------------

    def startup_recovery(self):
        """Report and clear interrupted work; never replay authority.

        Queued-but-undispatched items are dropped with an honest notice
        (plain intent is safe to resend by hand). A dispatched-but-
        unconfirmed in-flight marker is reported as AMBIGUOUS — the
        turn may or may not have reached Codex — and is never
        redispatched.
        """
        # I3 / strategy §1.3: a placeholder found mid-`sending` at
        # process start is a crash between the write-ahead and the
        # outcome. It fails CLOSED to `indefinite` here — never
        # re-sent.
        self._reconcile_sending_placeholders()
        with self._state_lock:
            document = self._document
            pending = list(document["queue"])
            in_flight = document["in_flight"]
            document["queue"] = []
            document["in_flight"] = None
            self._save()
        notices = {}
        for item in pending:
            chat_id = item.get("chat_id")
            if chat_id is None:
                continue
            if (
                isinstance(in_flight, dict)
                and in_flight.get("update_id") is not None
                and item.get("update_id") == in_flight.get("update_id")
                and chat_id == in_flight.get("chat_id")
            ):
                # This queued item IS the dispatched turn — the
                # pre-submit save proves it left the adapter, so the
                # "BEFORE dispatch ... re-send it" advice would be
                # false and could cause a user-initiated double
                # execution (round-3 finding R3-N1). It is reported
                # only through the AMBIGUOUS notice below.
                continue
            if item.get("kind") == "decision":
                # The one-shot approval behind a queued decision is
                # already consumed; telling the user to re-send it
                # would be unfollowable advice (round-1 finding F6).
                notices.setdefault(chat_id, []).append(
                    "A recorded plan decision was interrupted by an"
                    " adapter restart BEFORE dispatch and was dropped."
                    " Its one-shot approval is already consumed and"
                    " cannot be re-sent: ask for a fresh plan and"
                    " approve that."
                )
            else:
                notices.setdefault(chat_id, []).append(
                    "A queued %s was interrupted by an adapter restart"
                    " BEFORE dispatch and was dropped. Re-send it if"
                    " still wanted." % item.get("kind", "item")
                )
        if isinstance(in_flight, dict):
            chat_id = in_flight.get("chat_id")
            if chat_id is not None:
                notices.setdefault(chat_id, []).append(
                    "A dispatched %s turn (gateway request %s) was"
                    " interrupted by an adapter restart. Its outcome is"
                    " AMBIGUOUS: it may or may not have reached the"
                    " Operator. It was NOT replayed. Use /status to"
                    " inspect the current state." % (
                        in_flight.get("kind", "work"),
                        in_flight.get("request_id"),
                    )
                )
        for chat_id, texts in notices.items():
            self._interaction.send(chat_id, "\n".join(texts))
        return notices

    # -- poller side -----------------------------------------------------

    def poll_once(self):
        """One long poll plus full processing of every update in it.

        Returns True when the poll itself succeeded (idle deadline
        included) and False on a poll problem, so the caller can apply
        capped backoff.
        """
        with self._state_lock:
            offset = self._document["update_offset"]
        outcome = self._interaction.receive(offset)
        if outcome.problem is not None:
            # Kept for the run loop's failure reporting (R2-N1); the
            # problem text is already redacted and bounded upstream.
            self._last_poll_problem = outcome.problem
            return False
        for event in outcome.events:
            self.process_event(event)
        # Advance past every event whose sequence is readable, so a
        # batch containing malformed-but-identified events cannot wedge
        # the poller. Per-event accepted state was already persisted
        # above; this final advance is idempotent for those.
        batch_ids = [
            event.sequence
            for event in outcome.events
            if event.sequence is not None
        ]
        if batch_ids:
            with self._state_lock:
                self._advance_offset(max(batch_ids))
                self._save()
        return True

    def process_update(self, update):
        """Telegram-native compatibility entry: authenticate, then route
        one raw update through ``process_event``. See the module rules."""
        event_from_update = getattr(
            self._interaction, "event_from_update", None
        )
        if event_from_update is None:
            raise TypeError(
                "process_update needs a Telegram interaction with"
                " event_from_update; use process_event with an"
                " InteractionEvent instead"
            )
        self.process_event(event_from_update(update))

    def process_event(self, event):
        """Route one already-authenticated event. See the module rules."""
        if event.sequence is not None:
            with self._state_lock:
                offset = self._document["update_offset"]
            if offset is not None and event.sequence < offset:
                # Duplicate delivery of an already-accepted update
                # (Telegram redelivers when an offset is not yet
                # advanced); processing it again could double-queue.
                return
        if not event.allowed:
            # Authorization precedes ALL content handling: nothing the
            # sender supplied is parsed or persisted, and no reply is
            # sent (an unknown sender learns nothing). The ONLY durable
            # effect is the transport update-offset advance below —
            # intended, so a denied update cannot wedge the poll loop.
            if event.sequence is not None:
                with self._state_lock:
                    self._advance_offset(event.sequence)
                    self._save()
            return
        if event.kind == EVENT_MESSAGE:
            self._process_message(event)
        else:
            self._process_callback(event)

    def _advance_offset(self, update_id):
        current = self._document["update_offset"]
        if current is None or update_id + 1 > current:
            self._document["update_offset"] = update_id + 1

    def _process_message(self, event):
        text = event.content
        if text is None:
            with self._state_lock:
                self._advance_offset(event.sequence)
                self._save()
            self._interaction.send(
                event.conversation_id,
                "Only plain text is supported. Send intent as text.",
            )
            return
        stripped = text.strip()
        if stripped == "/start" or stripped == "/help":
            with self._state_lock:
                self._advance_offset(event.sequence)
                self._save()
            self._interaction.send(event.conversation_id, _HELP_TEXT)
            return
        if stripped == "/status":
            self._enqueue_or_report(
                event,
                {
                    "kind": "status",
                    "chat_id": event.conversation_id,
                    "user_id": event.principal_id,
                    "update_id": event.sequence,
                },
                acknowledgement="Gathering status…",
            )
            return
        if stripped.startswith("/mission"):
            stripped = stripped[len("/mission"):].strip()
        ok, problem = protocol.validate_intent(stripped)
        if not ok:
            with self._state_lock:
                self._advance_offset(event.sequence)
                self._save()
            if problem == protocol.INTENT_PROBLEM_TOO_LONG:
                reply = (
                    "Intent is %d characters; the limit is %d. It was"
                    " NOT queued (a silently shortened intent would be"
                    " a different intent). Send a shorter message."
                    % (len(stripped), protocol.MAX_INTENT_CHARS)
                )
            else:
                reply = "Empty intent. Send the request as text."
            self._interaction.send(event.conversation_id, reply)
            return
        self._enqueue_or_report(
            event,
            {
                "kind": "intent",
                "chat_id": event.conversation_id,
                "user_id": event.principal_id,
                "text": stripped,
                "update_id": event.sequence,
            },
            acknowledgement="Received. Routing to the Codex Operator…",
        )

    def _enqueue_or_report(self, event, item, acknowledgement):
        with self._state_lock:
            accepted = state_module.enqueue(self._document, item)
            depth = len(self._document["queue"])
            self._advance_offset(event.sequence)
            self._save()
        if not accepted:
            self._interaction.send(
                event.conversation_id,
                "Work queue is full (%d of %d pending). This message"
                " was NOT queued; try again after pending work"
                " finishes." % (depth, state_module.MAX_QUEUE_DEPTH),
            )
            return
        self._work_signals.put(item)
        self._interaction.send(event.conversation_id, acknowledgement)

    def _process_callback(self, event):
        data = event.content
        if isinstance(data, str) and (
            data.startswith(mission_module.CALLBACK_MISSION_APPROVE_PREFIX)
            or data.startswith(
                mission_module.CALLBACK_MISSION_REJECT_PREFIX
            )
        ):
            self._process_mission_callback(event, data)
            return
        chosen = None
        approval_id = None
        if isinstance(data, str):
            if data.startswith(CALLBACK_APPROVE_PREFIX):
                chosen = approval_module.DECISION_APPROVE
                approval_id = data[len(CALLBACK_APPROVE_PREFIX):]
            elif data.startswith(CALLBACK_REJECT_PREFIX):
                chosen = approval_module.DECISION_REJECT
                approval_id = data[len(CALLBACK_REJECT_PREFIX):]
        if chosen is None or not approval_id:
            with self._state_lock:
                self._advance_offset(event.sequence)
                self._save()
            self._interaction.acknowledge(
                event.action_id, "Unrecognized action; ignored."
            )
            return
        now = self._clock()
        with self._state_lock:
            record, problem = approval_module.evaluate_callback(
                self._document,
                approval_id=approval_id,
                user_id=event.principal_id,
                chat_id=event.conversation_id,
                repository=self.config.repository,
                message_id=event.message_id,
                now=now,
            )
            if problem is None and len(
                self._document["queue"]
            ) >= state_module.MAX_QUEUE_DEPTH:
                # Refuse BEFORE consuming: a consumed-but-undispatched
                # approval would waste its one shot.
                problem = approval_module.PROBLEM_QUEUE_FULL
            if problem is None:
                consumed = approval_module.consume(
                    self._document,
                    approval_id,
                    chosen,
                    update_id=event.sequence,
                    now=now,
                )
                if not consumed:
                    problem = approval_module.PROBLEM_ALREADY_CONSUMED
            item = None
            if problem is None:
                item = {
                    "kind": "decision",
                    "chat_id": event.conversation_id,
                    "user_id": event.principal_id,
                    "approval_id": approval_id,
                    "decision": chosen,
                    "update_id": event.sequence,
                }
                state_module.enqueue(self._document, item)
            self._advance_offset(event.sequence)
            # One durable save carries consumption + queue + offset,
            # BEFORE any external acknowledgement or dispatch.
            self._save()
        if problem is not None:
            self._interaction.acknowledge(
                event.action_id,
                "Decision refused (%s). Nothing was dispatched." % problem,
            )
            return
        self._work_signals.put(item)
        self._interaction.acknowledge(
            event.action_id,
            "Decision recorded (%s). Dispatching…" % chosen,
        )
        self._interaction.offer_controls(
            event.conversation_id, event.message_id, None
        )

    def _process_mission_callback(self, event, data):
        """One-shot v2 mission decision, completed HERE, in full.

        A v2 approval dispatches NO gateway turn (plan determination
        C-3): evaluation, one-shot consumption, and the PLANNED ->
        AUTHORIZED (or BLOCKED) transition are durably persisted into
        the workflow store BEFORE any acknowledgement; the Runtime — a
        separate process — claims the authorization from the store.
        The v1 approval path is untouched.
        """
        if data.startswith(mission_module.CALLBACK_MISSION_APPROVE_PREFIX):
            chosen = wa_record.DECISION_APPROVE
        else:
            chosen = wa_record.DECISION_REJECT
        workflow_id = data[len(
            mission_module.CALLBACK_MISSION_APPROVE_PREFIX
        ):]
        if not workflow_id:
            with self._state_lock:
                self._advance_offset(event.sequence)
                self._save()
            self._interaction.acknowledge(
                event.action_id, "Unrecognized action; ignored."
            )
            return
        now = self._clock()
        consumed = False
        with self._state_lock:
            # Layer 2 of ruling E-4, checked FIRST so it holds even
            # without the structural layer: a v1-era approval id can
            # never authorize a v2 mission, whatever its
            # superseded_for_v2 marking (missing/false refuse too).
            problem = mission_module.refuse_v1_approval_for_v2(
                self._document, workflow_id
            )
            if problem is None:
                try:
                    with workflow_store_module.exclusive_store_lock(
                        self.workflow_store.directory
                    ):
                        workflows = self.workflow_store.load()
                        entry, problem = (
                            mission_module.evaluate_mission_callback(
                                workflows,
                                workflow_id,
                                user_id=event.principal_id,
                                chat_id=event.conversation_id,
                                repository=self.config.repository,
                                message_id=event.message_id,
                                now=now,
                            )
                        )
                        if problem is None:
                            ok = mission_module.consume_mission(
                                workflows, workflow_id, chosen,
                                update_id=event.sequence, now=now,
                            )
                            if not ok:
                                problem = (
                                    mission_module.PROBLEM_ALREADY_CONSUMED
                                )
                            else:
                                # LAYER 1 (plan §1.1), the load-bearing
                                # atomicity property. The placeholder
                                # REQUEST is written into the SAME
                                # locked load-modify-save transaction
                                # that arms the mission, so the two
                                # cannot come apart: one save() persists
                                # both or neither. If this write does
                                # not land, nothing is armed.
                                #
                                # That is what makes
                                # `result_placeholder is None` provably
                                # mean "legacy record" and never
                                # "go-forward workflow that lost its
                                # request" — the distinction the whole
                                # legacy lane rests on.
                                if chosen == wa_record.DECISION_APPROVE:
                                    self._request_result_placeholder(
                                        workflows, workflow_id, now
                                    )
                                # Authority durably persisted BEFORE
                                # any external acknowledgement.
                                self.workflow_store.save(workflows)
                                consumed = True
                except workflow_store_module.StoreError:
                    # A tampered/corrupt workflow store fails CLOSED:
                    # nothing is readable, so nothing is decidable —
                    # a clean refusal, never a crashed poller.
                    problem = mission_module.PROBLEM_STORE_UNREADABLE
            self._advance_offset(event.sequence)
            self._save()
        if not consumed:
            self._interaction.acknowledge(
                event.action_id,
                "Mission decision refused (%s). Nothing was authorized"
                " or dispatched." % problem,
            )
            return
        if chosen == wa_record.DECISION_APPROVE:
            self._interaction.acknowledge(
                event.action_id,
                "Mission AUTHORIZED (one-shot consumed).",
            )
            self._interaction.send(
                event.conversation_id,
                "Mission %s AUTHORIZED and durably recorded. NO"
                " gateway turn was dispatched: the Runtime service"
                " picks the authorization up from the workflow store."
                " Delivery authority remains none — no commit, push,"
                " PR, tag, release, or deploy can result from this"
                " approval." % workflow_id,
            )
        else:
            self._interaction.acknowledge(
                event.action_id, "Mission rejected."
            )
            self._interaction.send(
                event.conversation_id,
                "Mission %s REJECTED and closed. Nothing was"
                " authorized and nothing will run." % workflow_id,
            )
        self._interaction.offer_controls(
            event.conversation_id, event.message_id, None
        )

    # -- worker side -----------------------------------------------------

    def process_work_item(self, item):
        """Run one queued item to completion (worker thread only)."""
        kind = item.get("kind")
        if kind == "intent":
            self._work_intent(item)
        elif kind == "decision":
            self._work_decision(item)
        elif kind == "status":
            self._work_status(item)
        else:
            self._finish_item(item)

    def _finish_item(self, item):
        with self._state_lock:
            if item in self._document["queue"]:
                self._document["queue"].remove(item)
            self._save()

    def _dispatch_gateway_turn(self, item, text, session_id):
        """One serialized gateway turn with an in-flight marker.

        The marker is persisted BEFORE the external call and cleared
        after it returns, so a crash in between leaves honest evidence
        of an ambiguous dispatch.
        """
        prepared = self._session.prepare(
            text,
            self.config.repository,
            session_id=session_id,
            source="telegram",
        )
        with self._state_lock:
            self._document["in_flight"] = {
                "kind": item["kind"],
                "chat_id": item["chat_id"],
                "request_id": prepared.request_id,
                "approval_id": item.get("approval_id"),
                "update_id": item.get("update_id"),
                "dispatched_at": self._clock(),
            }
            self._document["last_request"] = {
                "kind": item["kind"],
                "request_id": prepared.request_id,
                "status": "dispatched",
                "session_id": session_id,
                "updated_at": self._clock(),
            }
            # The per-chat CURRENT-REQUEST marker advances HERE, in the
            # PRE-submit save (round-3 review finding R3-B1): "a turn
            # was dispatched" is a fact the adapter knows before the
            # call, and the Codex subprocess has no deadline, so a
            # process death during submit() must still leave the
            # marker advanced on disk — otherwise a stale approval
            # would dispatch after restart. Only the request half
            # moves now; the result's real session id is recorded in
            # the post-submit block. A READ-ONLY status turn never
            # advances the marker.
            if item["kind"] != "status":
                previous = self._document["sessions"].get(
                    str(item["chat_id"])
                )
                state_module.record_session(
                    self._document,
                    item["chat_id"],
                    {
                        "session_id": (
                            previous.get("session_id")
                            if previous else None
                        ),
                        "request_id": prepared.request_id,
                        "updated_at": self._clock(),
                    },
                )
            self._save()
        result = self._session.execute(prepared)
        with self._state_lock:
            self._document["in_flight"] = None
            self._document["last_request"] = {
                "kind": item["kind"],
                "request_id": result.request_id,
                "status": result.status,
                "session_id": result.session_id,
                "updated_at": self._clock(),
            }
            # Post-submit, the ONLY session-map duty left is recording
            # the result's REAL session id — the request marker
            # already advanced durably in the pre-submit save (R3-B1)
            # and must not be recomputed here, where a mid-submit
            # crash would skip it. A falsy result session id writes
            # nothing: the pre-submit entry (previous session id, new
            # request marker) already says everything a
            # codex_failed/session-less result can add (R2-B1).
            if result.session_id:
                previous = self._document["sessions"].get(
                    str(item["chat_id"])
                )
                if previous:
                    carried_request = previous.get("request_id")
                elif item["kind"] != "status":
                    carried_request = prepared.request_id
                else:
                    carried_request = None
                state_module.record_session(
                    self._document,
                    item["chat_id"],
                    {
                        "session_id": result.session_id,
                        "request_id": carried_request,
                        "updated_at": self._clock(),
                    },
                )
            if item in self._document["queue"]:
                self._document["queue"].remove(item)
            self._save()
        return result

    def _report_gateway_failure(self, chat_id, result):
        detail = ""
        if result.error is not None:
            detail = " %s: %s" % (result.error.code, result.error.detail)
            if result.error.detail_truncated:
                detail += " [detail truncated]"
        self._interaction.send(
            chat_id,
            "Gateway turn failed (%s).%s Nothing further was done."
            % (result.status, detail),
        )

    def _work_intent(self, item):
        chat_id = item["chat_id"]
        with self._state_lock:
            session = self._document["sessions"].get(str(chat_id))
        session_id = session.get("session_id") if session else None
        text, neutralized = protocol.build_intent_text(item["text"])
        result = self._dispatch_gateway_turn(item, text, session_id)
        if result.status != STATUS_COMPLETED:
            self._report_gateway_failure(chat_id, result)
            return
        # Routing is purely on marker/version (supervisor ruling E-3);
        # the adapter never classifies intent. DELIBERATE v1-path
        # behavior change, wired here as an inherited obligation: the
        # router refuses a column-0 DI-REMOTE-1 DECISION line and a
        # both-marker message that the old v1-only parser accepted —
        # both fail closed as protocol-validation failures now.
        parsed = protocol.parse_routed_operator_response(result.message)
        prefix = ""
        if neutralized:
            prefix = (
                "(Protocol-marker lines in your text were quoted before"
                " forwarding; typed text never carries approval"
                " authority.)\n"
            )
        if not parsed.ok:
            self._interaction.send(
                chat_id,
                prefix + "The Operator reply did not pass protocol"
                " validation (%s). No plan is available from this turn"
                " and nothing was approved." % parsed.problem,
            )
            return
        if parsed.protocol_version == 2:
            if parsed.kind == protocol.KIND_MISSION_AUTHORIZATION:
                # Route (b), supervisor ruling R-1: the v2 marker in
                # the LEGACY (possibly resumed, ambient) reply carries
                # NO authority whatsoever — its body is discarded
                # unread. It only triggers the FRESH restrictive
                # planning turn below; only THAT turn's envelope may
                # arm a mission. The adapter still never classifies
                # intent (ruling E-3): routing stays purely on the
                # envelope marker.
                self._run_planning_and_offer(item, prefix)
            else:
                self._interaction.send(
                    chat_id,
                    prefix + "The Operator returned an unexpected v2"
                    " envelope kind (%s) on an intent turn; it was"
                    " ignored fail-closed and nothing was armed."
                    % parsed.kind,
                )
            return
        if parsed.kind == protocol.KIND_PLAN:
            self._offer_plan(item, result, parsed, prefix)
        else:
            self._interaction.send(
                chat_id, prefix + "[%s]\n%s" % (parsed.kind, parsed.body)
            )

    def _offer_plan(self, item, result, parsed, prefix):
        """Show a plan and arm its one-shot, fully bound approval.

        Invariant: an ACTIONABLE approval binds exactly the complete
        plan content actually displayed, and nothing undisplayed — and
        no approval CONTROL exists anywhere before that is proven. The
        ordering is load-bearing: the record is persisted before the
        send but stays non-actionable (``plan_message_id`` is None,
        refused by the explicit unbound-plan-message guard in
        ``approval.evaluate_callback``); the plan text is sent with NO
        inline keyboard; only after the send outcome proves complete
        delivery AND the exact message binding has been durably saved
        is the keyboard offered, via ``edit_message_reply_markup`` on
        the very message the binding names. A plan whose full message
        text cannot be displayed within the chunk cap is refused
        BEFORE any record exists; truncated, partial, failed, or
        unverifiable delivery voids the record and tells the user why
        (no control was ever offered on those paths); a failed or
        unverifiable keyboard offer voids the record too. The stored
        digest covers ``parsed.body``, which is acceptable exactly
        because the body is proven to have been displayed complete and
        verbatim inside the sent text.
        """
        chat_id = item["chat_id"]
        if not isinstance(result.session_id, str) or not result.session_id:
            # Binding operator instruction (round-4 finding R4-B1): a
            # COMPLETED gateway result may legally carry a null (or
            # empty) session handle. An approval bound to that could
            # only ever dispatch as a BRAND-NEW Codex session that has
            # never seen the plan — so no approval record is created
            # and no buttons are offered. Fail closed, tell the user
            # plainly.
            self._interaction.send(
                chat_id,
                prefix + "The Operator returned a plan, but the turn"
                " completed WITHOUT a resumable Codex session handle,"
                " so this plan cannot be approved (an approval could"
                " only start a new session that has never seen the"
                " plan). No approval was armed and no buttons are"
                " offered. Re-send your intent to get a bindable"
                " plan.",
            )
            return
        plan_text = prefix + PLAN_MESSAGE_HEADER + parsed.body
        if self._interaction.would_truncate(plan_text):
            # Refuse-before-send: the FULL text to display (prefix and
            # header included) exceeds what the chunk cap can deliver,
            # so an approval could only bind text the human never saw.
            # No approval record is created at all; the preview below
            # carries no buttons and send_message labels its own cut
            # inline.
            self._interaction.send(
                chat_id,
                prefix + "The Operator returned a plan too long to"
                " display completely (%d characters; at most %d can be"
                " shown). An approval must bind exactly the complete"
                " plan you saw, so NO approval was armed and no"
                " buttons are offered. Re-send your intent asking for"
                " a more concise plan.\n"
                "Undeliverable plan preview (NOT approvable):\n%s"
                % (len(plan_text), self._interaction.max_deliverable_chars,
                   parsed.body),
            )
            return
        now = self._clock()
        with self._state_lock:
            record, problem = approval_module.create_approval(
                self._document,
                user_id=item["user_id"],
                chat_id=chat_id,
                repository=self.config.repository,
                request_id=result.request_id,
                session_id=result.session_id,
                plan_message_id=None,
                plan_body=parsed.body,
                now=now,
            )
            # Authority-bearing record exists durably BEFORE the plan
            # message (the external action) is sent.
            self._save()
        if problem is not None:
            self._interaction.send(
                chat_id,
                "Plan received but the approval store is full (%s);"
                " approval buttons were NOT offered. Resolve pending"
                " approvals first." % problem,
            )
            return
        # The plan text goes out with NO reply_markup: at this instant
        # no actionable control exists anywhere — not on the phone, not
        # in the record (plan_message_id is still None).
        outcome = self._interaction.send(chat_id, plan_text)
        expected_chunks = self._interaction.chunk_count(plan_text)
        complete = (
            outcome.ok
            and outcome.truncated_chars == 0
            and len(outcome.message_ids) == expected_chunks
        )
        if complete:
            plan_message_id = outcome.message_ids[-1]
            armed = False
            with self._state_lock:
                stored = self._document["approvals"].get(
                    record["approval_id"]
                )
                if stored is not None:
                    # Complete display proven: every expected chunk
                    # delivered, nothing truncated. Durably persist the
                    # exact message binding — the LAST chunk, the very
                    # message the keyboard will be offered on — BEFORE
                    # any control exists.
                    stored["plan_message_id"] = plan_message_id
                    self._save()
                    armed = True
            if not armed:
                # Belt: the record vanished between creation and
                # arming. No binding was persisted, so no control may
                # be offered.
                self._interaction.send(
                    chat_id,
                    "The plan was displayed, but its approval record"
                    " vanished before it could be armed, so no"
                    " approval buttons were offered and no decision"
                    " can be accepted. Re-send your intent for a"
                    " fresh plan.",
                )
                return
            controls = (
                Control(
                    "Approve plan",
                    CALLBACK_APPROVE_PREFIX + record["approval_id"],
                ),
                Control(
                    "Reject plan",
                    CALLBACK_REJECT_PREFIX + record["approval_id"],
                ),
            )
            offered, offer_problem = self._interaction.offer_controls(
                chat_id, plan_message_id, controls
            )
            if offered is True:
                return
            # The keyboard offer failed or came back unverifiable: the
            # armed record must not remain actionable, because the
            # human may or may not be looking at usable buttons.
            with self._state_lock:
                stored = self._document["approvals"].get(
                    record["approval_id"]
                )
                if stored is not None:
                    stored["superseded"] = True
                    self._save()
            if not isinstance(offer_problem, str) or not offer_problem:
                offer_problem = (
                    "the keyboard offer outcome could not be verified"
                )
            self._interaction.send(
                chat_id,
                "The plan was displayed completely, but its approval"
                " buttons could not be attached (%s). Its approval was"
                " voided and cannot be decided; any buttons that may"
                " be visible under the plan are disarmed. Re-send your"
                " intent for a fresh plan." % offer_problem,
            )
            return
        with self._state_lock:
            stored = self._document["approvals"].get(record["approval_id"])
            if stored is not None:
                # Complete display was NOT proven; the record must not
                # remain approvable. No control was ever offered.
                stored["superseded"] = True
                self._save()
        # chunks_sent — not len(message_ids) — is the DISPLAY truth:
        # send_message reports ok=False with chunks_sent=index+1 when a
        # chunk WAS delivered but its message id was unusable, so the
        # id count understates what the human is looking at (round-1
        # review finding F-2; mistakes.md "silent truncation presented
        # as fact" — the rendered text must not misreport the case).
        delivered = outcome.chunks_sent
        if not outcome.ok and delivered >= expected_chunks:
            # Every chunk was displayed; only the delivery could not
            # be verified/bound (e.g. an unusable message id). This is
            # the one missing-binding shape the real client can emit.
            explanation = (
                "The plan text was displayed, but its delivery could"
                " not be verified (%s), so no decision can be accepted"
                " on it; no approval buttons were offered."
                % outcome.problem
            )
        elif not outcome.ok and delivered > 0:
            explanation = (
                "Plan delivery failed partway (%s): only %d of %d"
                " message chunks were delivered, so the plan you can"
                " see is INCOMPLETE." % (
                    outcome.problem, delivered, expected_chunks,
                )
            )
        elif not outcome.ok:
            explanation = (
                "Plan delivery failed (%s); the plan was not"
                " displayed." % outcome.problem
            )
        elif outcome.truncated_chars:
            explanation = (
                "The plan was delivered TRUNCATED (%d characters"
                " omitted), so what you see is not the complete plan."
                % outcome.truncated_chars
            )
        else:
            # Belt against a lying transport: ok=True with a chunk/id
            # count mismatch cannot be produced by the real client
            # (its ok=True path appends exactly one id per chunk).
            # Kept so even an impossible outcome shape fails closed.
            explanation = (
                "Plan delivery could not be verified as complete (no"
                " usable message binding was returned)."
            )
        self._interaction.send(
            chat_id,
            explanation + " Its approval was voided and cannot be"
            " decided. Re-send your intent for a fresh plan.",
        )

    def _request_result_placeholder(self, workflows, workflow_id, now):
        """Stamp the `required` placeholder request onto ONE record.

        Caller MUST already hold the store lock and MUST save the same
        document afterwards; this never saves and never sends. It is a
        pure in-document mutation precisely so it cannot be split from
        the arming save (plan §1.1).

        `chat_id` is copied from the record's own `telegram.chat_id`
        and is thereafter immutable — the record schema refuses any
        other value, so a placeholder bound in another chat is
        unrepresentable. An existing placeholder is never overwritten.
        """
        entry = workflows["workflows"].get(workflow_id)
        if entry is None or entry.get("result_placeholder") is not None:
            return False
        entry["result_placeholder"] = {
            "state": wa_record.PLACEHOLDER_REQUIRED,
            "chat_id": entry["telegram"]["chat_id"],
            "message_id": None,
            "requested_at": now,
            "sent_at": None,
            "bound_at": None,
            "text_digest": None,
        }
        return True

    def _reconcile_sending_placeholders(self):
        """Fail a crash-interrupted `sending` CLOSED, at startup only.

        Strategy §1.3, the one forced residual: a record found in
        `sending` when this process starts means a previous process
        wrote the send intent and died before recording the outcome.
        There is no Bot API call that can reconcile that — `getUpdates`
        returns incoming updates only, and a bot cannot read back its
        own outgoing messages — so DI genuinely cannot distinguish
        "the request never left" from "Telegram created the message".

        It therefore fails closed to `indefinite`: TERMINAL, never
        auto-retried, surfaced truthfully in /status naming the
        workflow. Guessing, or sending a second plausible placeholder,
        is exactly the duplicate-placeholder generator R-5 forbids.
        """
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                changed = False
                for entry in workflows["workflows"].values():
                    placeholder = entry.get("result_placeholder")
                    if placeholder is None:
                        continue
                    if placeholder.get("state") != (
                        wa_record.PLACEHOLDER_SENDING
                    ):
                        continue
                    placeholder["state"] = (
                        wa_record.PLACEHOLDER_INDEFINITE
                    )
                    changed = True
                if changed:
                    self.workflow_store.save(workflows)
        except workflow_store_module.StoreError:
            return

    def _eligible_placeholder_workflow_ids(self):
        """Snapshot the workflow ids whose placeholder may be sent.

        Read-only and mutation-free: it takes the lock, lists, and
        releases. The listing is only a candidate set — each id is
        RE-CHECKED under the lock at claim time, because the store is
        shared and a record may have moved on in between.
        """
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                return [
                    workflow_id
                    for workflow_id, entry in sorted(
                        workflows["workflows"].items()
                    )
                    if _placeholder_is_claimable(
                        entry.get("result_placeholder")
                    )
                ]
        except workflow_store_module.StoreError:
            return []

    def _claim_one_placeholder(self, workflow_id):
        """Write ONE record's send intent ahead, durably. Sends nothing.

        Returns ``(chat_id, text, digest)`` when this pass now owns the
        claim, or None when the record is no longer claimable or the
        write did not land.

        RULING R-10: exactly ONE record is claimed per lock cycle, and
        the caller sends and resolves it before claiming the next, so
        **never more than one placeholder is in `sending` at a time**.
        The previous batch claim marked every eligible record `sending`
        in one save; a crash after the FIRST object's send then made
        the startup sweep mark the whole remaining batch terminal
        `indefinite` even though their transport was never invoked —
        fabricating ambiguity for objects that were never touched, and
        stranding N-1 unrelated missions per crash. With a per-object
        claim a crash can orphan AT MOST ONE record: the irreducible
        one-object ambiguity strategy §1.2/§1.3 accepts, and no more.
        Every never-attempted workflow stays `required` and is claimed
        by the next pass.

        LEAD RULING L-1 is unchanged and still strict: `sent_at` AND
        `text_digest` of the exact text about to be sent are persisted
        BEFORE the transport is touched.
        """
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                entry = workflows["workflows"].get(workflow_id)
                if entry is None:
                    return None
                placeholder = entry.get("result_placeholder")
                # Re-checked under the lock: the snapshot that named
                # this id is not authority.
                if not _placeholder_is_claimable(placeholder):
                    return None
                text = render_placeholder_text(workflow_id)
                digest = wa_digest.text_digest(text)
                placeholder["state"] = wa_record.PLACEHOLDER_SENDING
                placeholder["sent_at"] = self._clock()
                placeholder["text_digest"] = digest
                placeholder["message_id"] = None
                placeholder["bound_at"] = None
                self.workflow_store.save(workflows)
                return placeholder["chat_id"], text, digest
        except workflow_store_module.StoreError:
            # The claim did not land, so nothing may be sent for it.
            return None

    def ensure_result_placeholders(self):
        """Drive `required`/`failed_unsent` -> `sending` -> outcome.

        ONE object at a time, and the ordering per object is the
        guarantee:

        1. UNDER THE LOCK, claim THIS record alone: state becomes
           `sending` with `sent_at` AND `text_digest` of the exact text
           about to be sent (LEAD RULING L-1), then save. Without the
           digest written ahead, an `indefinite` outcome would leave a
           message on screen the human cannot identify, and §1.3's
           recovery path would be truthful but useless.
        2. OUTSIDE the lock, send ONE message through the I2
           single-attempt path (`send_message_once`). That path never
           retries and never chunks: a retried `sendMessage` after a
           fired deadline is precisely a duplicate-placeholder
           generator (R-5).
        3. UNDER THE LOCK, record the three-valued outcome.

        R-10: the claim is PER OBJECT, never per batch. A crash at any
        point can therefore orphan at most one `sending` record. If an
        outcome fails to persist, the pass STOPS rather than claiming
        another object, so the at-most-one-orphan invariant holds even
        when the store becomes unwritable mid-pass.

        SELECTION IS THE TERMINALITY GUARANTEE: only `required` and
        `failed_unsent` are ever claimed. `indefinite` is never
        selected, by any pass, after any restart — a retry from it is
        the duplicate generator R-5 names. `bound` is terminal for
        binding, and `sending` is claimed by nobody (a crash-orphaned
        one is failed closed at startup, not re-sent).
        """
        for workflow_id in self._eligible_placeholder_workflow_ids():
            claim = self._claim_one_placeholder(workflow_id)
            if claim is None:
                continue
            chat_id, text, digest = claim
            outcome = self._interaction.send_once(chat_id, text)
            if not self._record_placeholder_outcome(
                workflow_id, digest, outcome
            ):
                # The outcome could not be persisted, so this record is
                # still `sending`. Claiming another object now would
                # put a SECOND record in `sending` and reopen exactly
                # the batch-contamination window R-10 closes.
                return

    def _record_placeholder_outcome(self, workflow_id, digest, outcome):
        """Record ONE placeholder send outcome durably (R-5).

        Returns True when this pass's claim is RESOLVED — persisted, or
        already resolved by somebody else — and False when the record
        is still `sending` because the write did not land. The caller
        uses that to keep R-10's at-most-one-`sending` invariant.
        """
        classification = getattr(outcome, "classification", None)
        message_id = getattr(outcome, "message_id", None)
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                entry = workflows["workflows"].get(workflow_id)
                if entry is None:
                    return True
                placeholder = entry.get("result_placeholder")
                # Only resolve the intent THIS pass wrote: the state
                # must still be `sending` and the digest must still be
                # the one that was sent. Anything else was changed
                # underneath us and is left alone rather than
                # overwritten.
                if placeholder is None:
                    return True
                if placeholder.get("state") != (
                    wa_record.PLACEHOLDER_SENDING
                ):
                    return True
                if placeholder.get("text_digest") != digest:
                    return True
                now = self._clock()
                if (
                    classification == SEND_APPLIED
                    and isinstance(message_id, int)
                    and not isinstance(message_id, bool)
                    and message_id >= 1
                ):
                    placeholder["state"] = wa_record.PLACEHOLDER_BOUND
                    placeholder["message_id"] = message_id
                    placeholder["bound_at"] = now
                elif classification == SEND_DEFINITE_ZERO:
                    # Proved no message exists. Retry is SAFE and the
                    # next pass will re-claim this record.
                    placeholder["state"] = (
                        wa_record.PLACEHOLDER_FAILED_UNSENT
                    )
                else:
                    # INDEFINITE, and anything unrecognized: fail
                    # closed to the TERMINAL state. Never retried.
                    placeholder["state"] = (
                        wa_record.PLACEHOLDER_INDEFINITE
                    )
                self.workflow_store.save(workflows)
                return True
        except workflow_store_module.StoreError:
            return False

    # -- DI-REMOTE-3 I5: edit-based final delivery ---------------------

    def _edit_delivery_claimable(self, entry):
        """True when the EDIT engine may attempt this record.

        The new engine drives ONLY workflows carrying a placeholder
        BINDING (plan §4): `result_placeholder.state == bound`. A
        record with no placeholder is the LEGACY lane and is delivered
        at-most-once by `_deliver_one_result`; this predicate never
        selects it.

        Every additive `result_delivery` key is read with ``.get``,
        never ``[...]``: a marker built IN-PROCESS carries only the
        three legacy keys and has NOT passed the load-boundary
        normalizer (plan §2.3, T-X6).
        """
        if entry["phase"] != wa_record.PHASE_COMPLETED:
            return False
        verified = entry["verified_result"]
        if verified is None:
            return False
        placeholder = entry.get("result_placeholder")
        if placeholder is None:
            return False
        if placeholder.get("state") != wa_record.PLACEHOLDER_BOUND:
            return False
        delivery = entry.get("result_delivery")
        if delivery is None:
            return True
        state = delivery.get("state")
        # READ-AS-PROOF AUDIT (round-04), state by state. Every terminal
        # or non-claimable outcome below either has its premise
        # RE-VERIFIED locally here, or a stated reason why it cannot be.
        #
        # degraded_unbindable — TERMINAL, premise NOT locally verifiable
        # and deliberately so. Its premise is "the bound Telegram object
        # is gone / its binding no longer matches", established at
        # delivery time from a real editMessageText outcome. Re-checking
        # it here would require ANOTHER Telegram call, and R-3 forbids
        # ever sending a replacement for this workflow, so there is no
        # safe local action even if the premise had lapsed. It is left
        # terminal, by design, not by omission.
        if state == wa_record.DELIVERY_DEGRADED_UNBINDABLE:
            return False
        # A LEGACY marker on a placeholder-bound record is terminal and
        # truthful; the state IS the premise (a legacy at-most-once
        # marker), so there is nothing external to re-verify, and the
        # edit engine never rewrites one.
        if state in wa_record.DELIVERY_LEGACY_STATES:
            return False
        # R-4: delivery is satisfied ONLY for the CURRENT verified
        # result. A revised result re-edits the SAME bound object, so a
        # stale digest never counts as delivered.
        if delivery.get("verified_result_digest") != verified["digest"]:
            return True
        # G1 (round-02), the READ-AS-PROOF half of the relational
        # invariant. A `delivered_by_edit` receipt is trusted as "the
        # bound object holds the verified result" ONLY if its
        # `rendered_digest` is the digest of the text THIS record
        # renders now. The record validator enforces the
        # `edited_message_id == bound message_id` half, but it cannot
        # check this half: the result renderer lives HERE, above the
        # store-only authority boundary that record.py must not cross.
        # A receipt whose rendered_digest does not match is not proof —
        # re-deliver (editing a pre-bound object with the correct
        # byte-identical payload is idempotent, R-5, and overwrites the
        # untrue receipt with a true one).
        if state == wa_record.DELIVERY_DELIVERED_BY_EDIT:
            return delivery.get("rendered_digest") != wa_digest.text_digest(
                render_result_text(entry)
            )
        # degraded_unrenderable — TERMINAL, but its premise IS locally
        # verifiable, so it must be verified (round-04). The premise is
        # "the result this record renders does not fit ONE Telegram
        # message". Trust it as terminal ONLY when the receipt's
        # rendered_digest matches THIS record's current rendering AND
        # that rendering genuinely exceeds the seam's max_message_chars.
        # A receipt marking a result unrenderable when the current render
        # is not actually oversized (a false terminal, or stale on the
        # render even at the same verified_result revision) is NOT proof:
        # reclaim and heal it (the edit is idempotent, R-5). This is the
        # same class as the delivered_by_edit check above — a terminal
        # receipt must not be trusted without its premise. T-G2 is
        # preserved: a genuinely oversized result stays terminal and is
        # never chunked or truncated.
        if state == wa_record.DELIVERY_DEGRADED_UNRENDERABLE:
            text = render_result_text(entry)
            premise_holds = (
                delivery.get("rendered_digest")
                == wa_digest.text_digest(text)
                and len(text) > self._interaction.max_message_chars
            )
            return not premise_holds
        # `edit_pending` and `edit_indefinite` are RETRYABLE, because an
        # edit against a pre-bound object with a byte-identical payload
        # is idempotent (R-5); they are claimable regardless, so there is
        # no false-terminal risk and the re-claim renders afresh. A
        # shorter revision re-entered above via the verified_result_digest
        # branch.
        return state in (
            wa_record.DELIVERY_EDIT_PENDING,
            wa_record.DELIVERY_EDIT_INDEFINITE,
        )

    def deliver_result_edits(self):
        """Deliver each bound workflow's verified result BY EDITING its
        placeholder — exactly-once VISIBLE presentation.

        One object at a time (the R-10 discipline): claim THIS record's
        edit intent durably, edit, record the outcome, then move on.

        The write-ahead records BOTH digests (R-4) before the transport
        is touched, so a crash mid-edit is resumable and can never
        report a delivery it cannot prove.
        """
        for workflow_id in self._edit_delivery_workflow_ids():
            claim = self._claim_one_edit(workflow_id)
            if claim is None:
                continue
            self._perform_one_edit(workflow_id, claim)

    def _edit_delivery_workflow_ids(self):
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                return [
                    workflow_id
                    for workflow_id, entry in sorted(
                        workflows["workflows"].items()
                    )
                    if self._edit_delivery_claimable(entry)
                ]
        except workflow_store_module.StoreError:
            return []

    def _claim_one_edit(self, workflow_id):
        """Write ONE record's edit intent ahead, durably.

        Returns the claim dict, or None when the record is no longer
        claimable, the claim did not land, or the render guard refused
        it (in which case the refusal itself was persisted).
        """
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                entry = workflows["workflows"].get(workflow_id)
                if entry is None:
                    return None
                if not self._edit_delivery_claimable(entry):
                    return None
                placeholder = entry["result_placeholder"]
                text = render_result_text(entry)
                now = self._clock()
                # THE RENDER-TIME GUARD.
                #
                # LABELLING, mandatory (Supervisor ACCEPTED CORRECTION
                # to strategy §3): THIS GUARD IS THE LOAD-BEARING
                # SINGLE-MESSAGE GUARANTEE. The constant-derived
                # arithmetic test is ONLY a constant-drift alarm in
                # front of it, never the guarantee itself.
                #
                # Why the arithmetic can never be the guarantee:
                # RULING R-18 now bounds `target.issue_or_pr.number` at
                # the record boundary (its full canonical issue/PR URL
                # must fit `canonical.MAX_TARGET_URL_CHARS`), so the
                # target line is NO LONGER unbounded. But this guard
                # stays LOAD-BEARING for a DIFFERENT input the record
                # boundary does not bound to one message: the
                # verified-result SUMMARY. `MAX_VERIFIED_SUMMARY_CHARS`
                # is deliberately wider than the enforced-presentable
                # `protocol.MAX_OUTCOME_DETAIL_CHARS`, so a summary at
                # the schema cap can still overflow one message. A
                # proof over the (now-bounded) target line alone
                # therefore still cannot establish that the result fits
                # one message — this guard, and only this guard, does.
                #
                # Fail CLOSED: never chunk, never truncate. The result
                # path does not call the chunking `send_message` at
                # all.
                if len(text) > self._interaction.max_message_chars:
                    entry["result_delivery"] = self._delivery_marker(
                        entry,
                        state=(
                            wa_record.DELIVERY_DEGRADED_UNRENDERABLE
                        ),
                        verified_result_digest=(
                            entry["verified_result"]["digest"]
                        ),
                        rendered_digest=wa_digest.text_digest(text),
                        attempted_at=now,
                        settled_at=now,
                        problem=(
                            "the rendered result is %d characters and"
                            " the single-message limit is %d; delivery"
                            " fails closed — it is never chunked and"
                            " never truncated"
                            % (len(text), self._interaction.max_message_chars)
                        ),
                    )
                    self.workflow_store.save(workflows)
                    return None
                rendered_digest = wa_digest.text_digest(text)
                entry["result_delivery"] = self._delivery_marker(
                    entry,
                    state=wa_record.DELIVERY_EDIT_PENDING,
                    verified_result_digest=(
                        entry["verified_result"]["digest"]
                    ),
                    rendered_digest=rendered_digest,
                    attempted_at=now,
                )
                self.workflow_store.save(workflows)
                return {
                    "chat_id": placeholder["chat_id"],
                    "message_id": placeholder["message_id"],
                    "text": text,
                    "rendered_digest": rendered_digest,
                    "verified_result_digest": (
                        entry["verified_result"]["digest"]
                    ),
                }
        except workflow_store_module.StoreError:
            return None

    def _delivery_marker(self, entry, state, verified_result_digest,
                         rendered_digest, attempted_at,
                         settled_at=None, problem=None,
                         edited_message_id=None):
        """A complete nine-key result_delivery marker.

        ``reserved_at`` keeps its EXACT legacy meaning — when delivery
        was first reserved for this record — and is preserved across
        edit attempts rather than restamped. Read with ``.get`` (T-X6).
        """
        existing = entry.get("result_delivery") or {}
        reserved_at = existing.get("reserved_at")
        if reserved_at is None:
            reserved_at = attempted_at
        return {
            "state": state,
            "reserved_at": reserved_at,
            # The LEGACY key keeps its exact meaning: it names a
            # message created by the legacy sendMessage path. The edit
            # path records `edited_message_id` instead and never
            # writes this one.
            "telegram_message_id": None,
            "verified_result_digest": verified_result_digest,
            "rendered_digest": rendered_digest,
            "edited_message_id": edited_message_id,
            "attempted_at": attempted_at,
            "settled_at": settled_at,
            "problem": problem,
        }

    def _perform_one_edit(self, workflow_id, claim):
        """Edit the bound object, then record the outcome durably.

        The edit goes through the I2 bounded `editMessageText` seam.
        Blanket retry is SAFE there, and that is the design's whole
        point: editing a pre-bound ``(chat_id, message_id)`` with a
        byte-identical payload is idempotent, so a replay leaves
        exactly the same visible state (R-5). The payload carries NO
        ``parse_mode`` and NO ``reply_markup`` — the transport omits
        them entirely, so byte-identity across replays holds BY
        CONSTRUCTION rather than by care (R-1).
        """
        outcome = self._interaction.edit(
            claim["chat_id"], claim["message_id"], claim["text"]
        )
        detail = getattr(outcome, "detail", None)
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                entry = workflows["workflows"].get(workflow_id)
                if entry is None:
                    return
                delivery = entry.get("result_delivery") or {}
                # Only resolve the intent THIS pass wrote.
                if delivery.get("state") != (
                    wa_record.DELIVERY_EDIT_PENDING
                ):
                    return
                if delivery.get("rendered_digest") != (
                    claim["rendered_digest"]
                ):
                    return
                now = self._clock()
                state, problem, edited_message_id = (
                    self._classify_edit_outcome(
                        entry, claim, outcome, detail
                    )
                )
                entry["result_delivery"] = self._delivery_marker(
                    entry,
                    state=state,
                    verified_result_digest=(
                        claim["verified_result_digest"]
                    ),
                    rendered_digest=claim["rendered_digest"],
                    attempted_at=delivery.get("attempted_at") or now,
                    settled_at=now,
                    problem=problem,
                    edited_message_id=edited_message_id,
                )
                self.workflow_store.save(workflows)
        except workflow_store_module.StoreError:
            return

    def _classify_edit_outcome(self, entry, claim, outcome, detail):
        """Map one edit attempt to a durable delivery state.

        Returns ``(state, problem, edited_message_id)``.
        """
        placeholder = entry.get("result_placeholder") or {}
        binding_matches = (
            placeholder.get("state") == wa_record.PLACEHOLDER_BOUND
            and placeholder.get("chat_id") == claim["chat_id"]
            and placeholder.get("message_id") == claim["message_id"]
        )
        if not binding_matches:
            # R-2 clause 1, applied to EVERY success path and not only
            # to message-not-modified: the record must still name the
            # object this pass edited. If the binding moved underneath
            # us, we cannot claim the CURRENT bound object holds the
            # text, so we fail closed rather than record a delivery we
            # cannot prove. Belt coverage — I3 never rewrites a bound
            # placeholder — but a concurrent writer must not be able to
            # turn an edit into a false delivery claim.
            return (
                wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                "the placeholder binding changed while the edit was in"
                " flight, so this delivery cannot be proven against the"
                " CURRENT bound object; NO replacement message is sent",
                None,
            )
        if getattr(outcome, "ok", False):
            return (
                wa_record.DELIVERY_DELIVERED_BY_EDIT, None,
                claim["message_id"],
            )
        # --- R-2: message-not-modified is success ONLY under proof ---
        #
        # STATED BOT API ASSUMPTION, not a fact any test here proves:
        # ONLY THIS BOT CAN EDIT ITS OWN MESSAGES. Under that
        # assumption, this response on the bot's own bound object
        # proves the object already holds exactly the intended
        # content, because no third party could have produced
        # coincidentally identical text. If that assumption were
        # false, clause (3) below would no longer be sufficient.
        #
        # All FOUR clauses must hold (plan §3.4):
        #   1. the edit targeted the EXACT bound chat_id AND
        #      message_id, both re-read from the durable record in
        #      THIS load — checked here, not assumed from the claim;
        #   2. the rendered digest equals the digest recorded for the
        #      CURRENT verified_result (R-4);
        #   3. the description matches the message-not-modified
        #      condition rigorously — exact membership of a named
        #      closed set on the NORMALIZED description, never a loose
        #      substring scan;
        #   4. it is a genuine structured ok=false body, not an
        #      inferred one.
        # Clauses 3 and 4 are the provider-proof half: they are decided
        # by the provider implementation behind the seam from the
        # structured detail and arrive here as ``already_applied``
        # (``target_missing`` likewise carries the R-3 proof below).
        # ``detail`` is retained for callers and no longer interpreted
        # here.
        verified = entry.get("verified_result") or {}
        digest_matches = (
            verified.get("digest") == claim["verified_result_digest"]
            and claim["rendered_digest"] == wa_digest.text_digest(
                claim["text"]
            )
        )
        if getattr(outcome, "already_applied", False) is True:
            if binding_matches and digest_matches:
                return (
                    wa_record.DELIVERY_DELIVERED_BY_EDIT, None,
                    claim["message_id"],
                )
            # The phrase arrived, but the proof did not. Never a
            # success: fail closed to the ambiguous state.
            return (
                wa_record.DELIVERY_EDIT_INDEFINITE,
                "Telegram reported message-not-modified, but the"
                " four-part proof did not hold (binding_matches=%s,"
                " digest_matches=%s); it is NOT recorded as delivered"
                % (binding_matches, digest_matches),
                None,
            )
        # --- R-3: the bound object is gone, or the binding moved ---
        # Fail CLOSED. NO replacement message is EVER sent: silently
        # sending a fresh result is the exact duplicate-presentation
        # bug this whole task exists to remove.
        if getattr(outcome, "target_missing", False) is True or (
            not binding_matches
        ):
            return (
                wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                "the bound placeholder message is gone or its binding"
                " no longer matches; the verified result was NOT"
                " delivered and NO replacement message is ever sent."
                " Human recovery required.",
                None,
            )
        return (
            wa_record.DELIVERY_EDIT_INDEFINITE,
            getattr(outcome, "problem", None)
            or "the edit outcome is ambiguous",
            None,
        )

    def deliver_pending_results(self):
        """The LEGACY AT-MOST-ONCE lane: deliver a verified result by
        sending a fresh message.

        SCOPE, enforced by the selector below and not merely asserted
        here (RULING R-15): this lane serves ONLY records with
        ``result_placeholder is None`` — those that predate or sit
        outside the placeholder architecture. A placeholder-bearing
        workflow is delivered by EDITING its bound object
        (``deliver_result_edits``) and must never receive a fresh
        message, because a fresh message is a second visible result
        object.

        **AT-MOST-ONCE, not exactly-once.** This docstring used to
        claim "exactly once" and "EXACTLY-ONCE in effect"; that was
        false for the lane it serves, and a docstring asserting a
        guarantee the code does not provide is exactly the class this
        task keeps rejecting. What this lane actually guarantees:

          * the durable ``result_delivery`` marker is written BEFORE
            the send is attempted, under the store lock, so a crash
            after the marker never re-sends;
          * a crash BEFORE the marker re-attempts on the next loop,
            because nothing was sent;
          * but the send itself is a non-idempotent ``sendMessage``
            with no way to read back the bot's own outgoing message,
            so the RESERVED and PARTIAL outcomes are TERMINAL and are
            never re-sent. In those cases the result is never shown
            again. That is at-most-once, and it is the acceptance gap
            the placeholder architecture exists to close for
            go-forward workflows.

        The adapter reads and writes ONLY the shared workflow store —
        it never touches Runtime, Broker, or Herdr.
        """
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                pending = []
                for workflow_id, entry in sorted(
                    workflows["workflows"].items()
                ):
                    if (
                        entry["phase"] == wa_record.PHASE_COMPLETED
                        and entry["verified_result"] is not None
                        and entry["result_delivery"] is None
                        # RULING R-15 — the LEGACY-LANE GATE, and it is
                        # a GUARD, not a comment.
                        #
                        # Before this, the selector never read
                        # `result_placeholder`, so a placeholder-BOUND
                        # record and a legacy record were
                        # INDISTINGUISHABLE here. `_deliver_one_result`
                        # asserted in prose that it was reached only by
                        # legacy records; nothing enforced it.
                        #
                        # The window was real and reachable, not
                        # theoretical: `_edit_delivery_workflow_ids`
                        # returns [] on a TRANSIENT StoreError and
                        # writes NO marker, and `run()` calls
                        # `deliver_pending_results` immediately after
                        # `deliver_result_edits` in the SAME pass. Its
                        # own load then succeeds, sees a bound record
                        # with `result_delivery is None`, and sends a
                        # FRESH result message — a second visible
                        # object for a workflow whose result must be
                        # delivered by EDITING its bound placeholder.
                        # That breaks exactly-once visible
                        # presentation and R-3's "never create a
                        # replacement result object".
                        #
                        # Run-loop ordering is NOT an invariant:
                        # transient store recovery breaks it, and that
                        # is precisely the window this task exists to
                        # close. So the legacy at-most-once lane is
                        # reachable ONLY by records that never entered
                        # the placeholder architecture.
                        and entry.get("result_placeholder") is None
                    ):
                        pending.append((workflow_id, entry))
                if not pending:
                    return
                # Reserve delivery durably BEFORE sending. A RESERVED
                # marker is never re-sent (deliver_pending_results
                # only picks result_delivery is None), so a crash
                # after reserve can never double-send; a crash BEFORE
                # reserve re-attempts (nothing was reserved or sent).
                now = self._clock()
                claimed = []
                for workflow_id, entry in pending:
                    entry["result_delivery"] = {
                        "state": wa_record.DELIVERY_RESERVED,
                        "reserved_at": now,
                        "telegram_message_id": None,
                    }
                    claimed.append((workflow_id, entry))
                self.workflow_store.save(workflows)
        except workflow_store_module.StoreError:
            return
        # Send outside the lock; each send's outcome updates the
        # durable marker to the real message id (or clears it so a
        # failed send is retried, never silently dropped).
        for workflow_id, entry in claimed:
            self._deliver_one_result(workflow_id, entry)

    def _deliver_one_result(self, workflow_id, entry):
        # LEGACY AT-MOST-ONCE LANE, unchanged. Reached only by records
        # with NO placeholder (plan §1.1/§4). The text now comes from
        # the shared renderer, which produces the SAME bytes this path
        # has always sent.
        chat_id = entry["telegram"]["chat_id"]
        text = render_result_text(entry)
        outcome = self._interaction.send(chat_id, text)
        # Resolve the send against BOTH axes of exactly-once:
        #  - complete send -> DELIVERED with the real message id;
        #  - PARTIAL send (some chunks displayed, ok=False) -> a
        #    terminal PARTIAL marker: never re-sent, because re-sending
        #    the whole result would re-display the chunks the human
        #    already saw (round-10 F-3). chunks_sent, not the message-id
        #    count, is the DISPLAY truth here as it is on the plan and
        #    mission paths;
        #  - nothing displayed -> clear the reservation so the next loop
        #    retries the whole result (safe: no chunk was shown).
        message_id = None
        if getattr(outcome, "ok", False) and getattr(
            outcome, "message_ids", None
        ):
            message_id = outcome.message_ids[-1]
        elif isinstance(outcome, bool) and outcome:
            message_id = None
        chunks_sent = getattr(outcome, "chunks_sent", None)
        partial = (
            message_id is None
            and not (isinstance(outcome, bool) and outcome)
            and isinstance(chunks_sent, int)
            and chunks_sent > 0
        )
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
                stored = workflows["workflows"].get(workflow_id)
                if stored is None:
                    return
                reserved_at = (
                    stored["result_delivery"]["reserved_at"]
                    if stored["result_delivery"] else self._clock()
                )
                if message_id is not None:
                    stored["result_delivery"] = {
                        "state": wa_record.DELIVERY_DELIVERED,
                        "reserved_at": reserved_at,
                        "telegram_message_id": int(message_id),
                    }
                elif partial:
                    # Part of the result was displayed but the send did
                    # not complete. Do NOT retry: a fresh send re-sends
                    # the WHOLE result and the human sees the first
                    # chunks twice. Mark it PARTIAL — terminal for
                    # auto-delivery, surfaced honestly in /status.
                    stored["result_delivery"] = {
                        "state": wa_record.DELIVERY_PARTIAL,
                        "reserved_at": reserved_at,
                        "telegram_message_id": None,
                    }
                else:
                    # Nothing was displayed: clear the reservation so
                    # the next loop retries — never a silent drop, never
                    # a double-send (the reserved marker was the only
                    # thing marking it delivered).
                    stored["result_delivery"] = None
                self.workflow_store.save(workflows)
        except workflow_store_module.StoreError:
            return

    def _void_mission_durably(self, workflow_id):
        """Durably void a mission record; fail closed on store errors.

        When the store is unreadable the void cannot be written — but
        nothing remains actionable either, because every decision
        path's own load fails closed on the same StoreError.
        """
        try:
            with self._state_lock:
                with workflow_store_module.exclusive_store_lock(
                    self.workflow_store.directory
                ):
                    workflows = self.workflow_store.load()
                    if mission_module.void_mission(
                        workflows, workflow_id
                    ):
                        self.workflow_store.save(workflows)
            return True
        except workflow_store_module.StoreError:
            return False

    def _run_planning_and_offer(self, item, prefix):
        """The I2 planning boundary: fresh restrictive process only.

        Runs the pre-record planning turn (a distinct fresh Codex
        process under the verified read-only posture, rooted at the
        control repository, with NO session id anywhere in argv or
        stdin) and offers a mission ONLY from that turn's envelope.
        Every refusal or failure arms nothing, persists nothing, and
        reports actionably.
        """
        chat_id = item["chat_id"]
        result = self._planning_turn(
            item["text"], self.config.repository, self._clock()
        )
        if result.status != role_turn_module.ROLE_TURN_COMPLETED:
            detail = ""
            if result.error is not None:
                detail = " %s" % result.error.detail
                if getattr(result.error, "detail_truncated", False):
                    detail += " [detail truncated]"
            self._interaction.send(
                chat_id,
                prefix + "The fresh planning turn did not complete"
                " (%s: %s).%s NO mission was recorded and nothing was"
                " armed." % (result.status, result.reason, detail),
            )
            return
        routed = protocol.parse_routed_operator_response(result.message)
        # The kind check alone suffices: mission_authorization is a
        # v2-ONLY kind (it is not in the closed v1 RESPONSE_KINDS, so
        # a version-1 parse can never carry it) — that router
        # invariant is pinned by a test, and the former
        # protocol_version != 2 condition was removed as unreachable
        # (I2 review carry-over, resolved per the Lead's direction).
        if (
            not routed.ok
            or routed.kind not in PLANNING_TURN_ACCEPTED_KINDS
        ):
            problem = (
                routed.problem
                if not routed.ok
                else "wrong_kind:%s" % routed.kind
            )
            self._interaction.send(
                chat_id,
                prefix + "The fresh planning turn did not return a"
                " valid Mission Authorization envelope (%s). NO"
                " mission was recorded and nothing was armed."
                % problem,
            )
            return
        self._offer_mission(
            item, routed, prefix, planning_turn=result.turn
        )

    def _offer_mission(self, item, parsed, prefix, planning_turn):
        """Display a Mission Authorization and arm its one-shot v2
        approval — the PROVEN displayed-content ordering contract,
        reused exactly in shape.

        Order, load-bearing and fail-closed: validation refusals (the
        v2 null-binding analog: control mismatch, minted transport
        bindings, bad shapes) -> record bounds refusal (nothing built)
        -> pre-send truncation refusal over the FULL sent text (belt;
        currently unreachable by the envelope-cap invariant, kept and
        unit-tested) -> persist the record durably, NOT actionable
        (telegram.plan_message_id is None) -> send with NO keyboard ->
        prove complete delivery (ok AND truncated_chars == 0 AND one
        message id per expected chunk) -> durably bind the exact
        message id -> ONLY THEN edit_message_reply_markup on that
        exact chat+message (success is exactly ``offered is True``) ->
        a failed or unverifiable offer durably VOIDS the record; every
        incomplete-delivery shape durably voids with text derived from
        ``chunks_sent``.
        """
        chat_id = item["chat_id"]
        try:
            document = mission_module.validate_mission_document(
                parsed.body, self.config.repository
            )
        except (
            mission_module.MissionError,
            mission_module.AuthorizationError,
        ) as exc:
            self._interaction.send(
                chat_id,
                prefix + "The Operator returned a Mission Authorization"
                " that failed validation (%s): %s. NO mission was"
                " recorded and no approval exists."
                % (getattr(exc, "problem", "invalid"), exc),
            )
            return
        workflow_id = self._workflow_id_factory()
        now = self._clock()
        try:
            # human_intent is ADAPTER-stamped: the exact text the
            # transport accepted for this queue item, byte-exact —
            # never anything the Operator document supplied (a
            # document carrying human_intent was already refused
            # above). The rendered text is computed inside the record
            # constructor by the single renderer, so the render
            # binding holds by construction.
            entry = mission_module.build_workflow_record(
                document, item["text"],
                user_id=item["user_id"], chat_id=chat_id, now=now,
                workflow_id=workflow_id,
                nonce_factory=self._mission_nonce_factory,
            )
            # D2/criterion C ("all turn ids"): the fresh planning
            # turn's identity is recorded in the record it produced.
            if planning_turn is not None:
                entry["codex_turns"] = [dict(planning_turn)]
                wa_record.validate_record(entry)
        except wa_record.RecordError as exc:
            self._interaction.send(
                chat_id,
                prefix + "The Mission Authorization exceeds a hard"
                " bound (%s): %s. It was refused, not truncated;"
                " nothing was recorded." % (exc.problem, exc),
            )
            return
        rendered = entry["mission_authorization"]["rendered_text"]
        mission_text = (
            prefix + mission_module.MISSION_MESSAGE_HEADER + rendered
        )
        if self._interaction.would_truncate(mission_text):
            self._interaction.send(
                chat_id,
                prefix + "The Mission Authorization is too long to"
                " display completely (%d characters; at most %d can be"
                " shown). An approval must bind exactly the complete"
                " text you saw, so NOTHING was recorded and no buttons"
                " are offered." % (
                    len(mission_text),
                    self._interaction.max_deliverable_chars,
                ),
            )
            return
        try:
            with self._state_lock:
                with workflow_store_module.exclusive_store_lock(
                    self.workflow_store.directory
                ):
                    workflows = self.workflow_store.load()
                    superseded = mission_module.supersede_chat_missions(
                        workflows, chat_id
                    )
                    ok, add_problem, _ = (
                        workflow_store_module.add_workflow(
                            workflows, entry
                        )
                    )
                    # Durable BEFORE the plan message is sent; the
                    # record is persisted but NOT actionable
                    # (plan_message_id is None). Supersession of older
                    # unapproved missions is persisted in the same
                    # durable save.
                    self.workflow_store.save(workflows)
        except workflow_store_module.StoreError as exc:
            self._interaction.send(
                chat_id,
                prefix + "The workflow store could not be read or"
                " written (%s); NOTHING was recorded and no approval"
                " exists. Repair the store before sending missions."
                % exc,
            )
            return
        if superseded:
            self._interaction.send(
                chat_id,
                "Note: %d earlier unapproved mission(s) in this chat"
                " were voided by this new mission (exact count)."
                % superseded,
            )
        if not ok:
            self._interaction.send(
                chat_id,
                prefix + "Mission received but the workflow store"
                " refused it (%s); no approval buttons were offered."
                % add_problem,
            )
            return
        outcome = self._interaction.send(chat_id, mission_text)
        expected_chunks = self._interaction.chunk_count(mission_text)
        complete = (
            outcome.ok
            and outcome.truncated_chars == 0
            and len(outcome.message_ids) == expected_chunks
        )
        if complete:
            mission_message_id = outcome.message_ids[-1]
            armed = False
            try:
                with self._state_lock:
                    with workflow_store_module.exclusive_store_lock(
                        self.workflow_store.directory
                    ):
                        workflows = self.workflow_store.load()
                        stored = workflows["workflows"].get(workflow_id)
                        if stored is not None:
                            stored["telegram"]["message_ids"] = [
                                int(value)
                                for value in outcome.message_ids
                            ]
                            stored["telegram"]["plan_message_id"] = (
                                mission_message_id
                            )
                            self.workflow_store.save(workflows)
                            armed = True
            except workflow_store_module.StoreError:
                # Unreadable store: the binding could not be durably
                # persisted, so no control may be offered (and any
                # later decision on this store fails closed too).
                armed = False
            if not armed:
                self._interaction.send(
                    chat_id,
                    "The mission was displayed, but its workflow record"
                    " vanished before it could be armed; no approval"
                    " buttons were offered and no decision can be"
                    " accepted. Re-send your intent.",
                )
                return
            controls = (
                Control(
                    "Approve mission",
                    mission_module.CALLBACK_MISSION_APPROVE_PREFIX
                    + workflow_id,
                ),
                Control(
                    "Reject mission",
                    mission_module.CALLBACK_MISSION_REJECT_PREFIX
                    + workflow_id,
                ),
            )
            offered, offer_problem = self._interaction.offer_controls(
                chat_id, mission_message_id, controls
            )
            if offered is True:
                return
            self._void_mission_durably(workflow_id)
            if not isinstance(offer_problem, str) or not offer_problem:
                offer_problem = (
                    "the keyboard offer outcome could not be verified"
                )
            self._interaction.send(
                chat_id,
                "The mission was displayed completely, but its approval"
                " buttons could not be attached (%s). The mission was"
                " voided and cannot be decided; any buttons that may be"
                " visible are disarmed. Re-send your intent."
                % offer_problem,
            )
            return
        self._void_mission_durably(workflow_id)
        # chunks_sent — not len(message_ids) — is the DISPLAY truth
        # (the recorded void-text-from-the-wrong-field lesson).
        delivered = outcome.chunks_sent
        if not outcome.ok and delivered >= expected_chunks:
            explanation = (
                "The mission text was displayed, but its delivery"
                " could not be verified (%s), so no decision can be"
                " accepted on it; no approval buttons were offered."
                % outcome.problem
            )
        elif not outcome.ok and delivered > 0:
            explanation = (
                "Mission delivery failed partway (%s): only %d of %d"
                " message chunks were delivered, so the mission you"
                " can see is INCOMPLETE." % (
                    outcome.problem, delivered, expected_chunks,
                )
            )
        elif not outcome.ok:
            explanation = (
                "Mission delivery failed (%s); the mission was not"
                " displayed." % outcome.problem
            )
        elif outcome.truncated_chars:
            explanation = (
                "The mission was delivered TRUNCATED (%d characters"
                " omitted), so what you see is not the complete"
                " mission." % outcome.truncated_chars
            )
        else:
            explanation = (
                "Mission delivery could not be verified as complete"
                " (no usable message binding was returned)."
            )
        self._interaction.send(
            chat_id,
            explanation + " The mission was voided and cannot be"
            " decided. Re-send your intent.",
        )

    def _work_decision(self, item):
        chat_id = item["chat_id"]
        approval_id = item["approval_id"]
        with self._state_lock:
            record = self._document["approvals"].get(approval_id)
            session = self._document["sessions"].get(str(chat_id))
        if record is None:
            self._finish_item(item)
            self._interaction.send(
                chat_id,
                "Recorded decision could not be dispatched: approval"
                " record vanished. Nothing was sent.",
            )
            return
        current_session = session.get("session_id") if session else None
        # Independently sourced current request id (round-1 review
        # finding F3): the chat's last NON-STATUS gateway turn. If the
        # conversation moved past the turn that produced this plan, the
        # approval no longer describes the current state and must not
        # dispatch.
        current_request = session.get("request_id") if session else None
        ok, problem = approval_module.validate_dispatch_binding(
            record,
            self.config.repository,
            current_request,
            current_session,
        )
        if not ok:
            self._finish_item(item)
            explanations = {
                approval_module.PROBLEM_SESSION_MISMATCH: (
                    "the Codex session changed after the plan was shown"
                ),
                approval_module.PROBLEM_REQUEST_MISMATCH: (
                    "the conversation moved past this plan (a later"
                    " turn was dispatched in this chat)"
                ),
                approval_module.PROBLEM_REPOSITORY_MISMATCH: (
                    "the configured repository is not the one this"
                    " plan was bound to"
                ),
                approval_module.PROBLEM_DIGEST_MISMATCH: (
                    "the stored plan text no longer matches the digest"
                    " this approval was bound to — possible tampering;"
                    " inspect the adapter state file"
                ),
                approval_module.PROBLEM_UNBINDABLE_SESSION: (
                    "this plan's Codex session handle was never"
                    " recorded, so the approval could never resume the"
                    " session that produced it; the stored approval is"
                    " unusable"
                ),
            }
            self._interaction.send(
                chat_id,
                "Decision NOT dispatched (%s): %s. Ask for a fresh"
                " plan." % (
                    problem,
                    explanations.get(problem, "binding validation failed"),
                ),
            )
            return
        text = approval_module.decision_turn_text(record, item["decision"])
        result = self._dispatch_gateway_turn(
            item, text, record["session_id"]
        )
        if result.status != STATUS_COMPLETED:
            self._report_gateway_failure(chat_id, result)
            return
        parsed = protocol.parse_routed_operator_response(result.message)
        if not parsed.ok:
            self._interaction.send(
                chat_id,
                "Decision was dispatched, but the Operator reply did"
                " not pass protocol validation (%s). Use /status to"
                " follow up." % parsed.problem,
            )
            return
        if parsed.protocol_version == 2:
            self._interaction.send(
                chat_id,
                "Decision was dispatched, but the Operator returned an"
                " unexpected v2 envelope (%s) on a v1 decision turn;"
                " it was ignored fail-closed. Use /status to follow"
                " up." % parsed.kind,
            )
            return
        self._interaction.send(
            chat_id, "[%s]\n%s" % (parsed.kind, parsed.body)
        )

    def _work_status(self, item):
        chat_id = item["chat_id"]
        with self._state_lock:
            document = self._document
            last = document["last_request"]
            in_flight = document["in_flight"]
            # This status request itself is still in the durable queue
            # while it is being served; counting it would misreport an
            # idle adapter as having pending work (round-1 finding F5).
            # Identity, not value equality: two byte-identical queued
            # dicts must both still count (round-2 finding R2-N3) —
            # only THIS served item is excluded.
            other_items = [
                queued for queued in document["queue"]
                if queued is not item
            ]
            depth = len(other_items)
            dropped_sessions = document["sessions_dropped_total"]
            session = document["sessions"].get(str(chat_id))
            # Same activity predicate as supersession/pruning, so an
            # EXPIRED approval is never rendered as open (round-4
            # finding OP5). The count is instance-global (all chats)
            # and the label says so.
            pending = approval_module.count_open_approvals(
                document, self._clock()
            )
        lines = ["Adapter state (durable, read first):"]
        if last:
            lines.append(
                "last gateway turn: kind=%s status=%s request=%s"
                % (last.get("kind"), last.get("status"),
                   last.get("request_id"))
            )
        else:
            lines.append("no gateway turn has been dispatched yet")
        lines.append(
            "queued items besides this status request (exact): %d"
            % depth
        )
        lines.append(
            "in-flight dispatch: %s"
            % ("yes (request %s)" % in_flight.get("request_id")
               if isinstance(in_flight, dict) else "none")
        )
        lines.append(
            "approvable plans awaiting decision, all chats (exact): %d"
            % pending
        )
        lines.append(
            "session map evictions since first run (exact): %d"
            % dropped_sessions
        )
        # DI-REMOTE-2 workflow state, read fresh from the shared store
        # (the Runtime writes it too), plus the Runtime liveness probe:
        # a not-running / not-installed Runtime is an ACTIONABLE ERROR
        # here, never a silent stall (binding consequence of ruling
        # E-1 — an authorized mission with no Runtime would otherwise
        # just sit).
        # I5 D5/G3: mission engineering status is read from the
        # DURABLE workflow store (the coupling medium the Runtime
        # keeps current) and named EXACTLY per workflow — the target
        # canonical URL and issue/PR (or repository-only), the phase,
        # and whether a verified result is available. It does NOT
        # resume the legacy chat session for engineering status, and
        # the adapter never touches Runtime, Broker, or Herdr.
        try:
            with workflow_store_module.exclusive_store_lock(
                self.workflow_store.directory
            ):
                workflows = self.workflow_store.load()
        except workflow_store_module.StoreError as exc:
            workflows = None
            lines.append(
                "ERROR: the v2 workflow store could not be read (%s);"
                " mission state is unknown until it is repaired." % exc
            )
        if workflows is not None:
            entries = workflows["workflows"]
            if not entries:
                lines.append("v2 mission workflows: none")
            else:
                lines.append(
                    "v2 mission workflows (exact, %d):" % len(entries)
                )
                for workflow_id in sorted(entries):
                    entry = entries[workflow_id]
                    line = "  %s: phase=%s; %s" % (
                        workflow_id, entry["phase"],
                        _render_target_line(entry),
                    )
                    if entry["phase"] == wa_record.PHASE_NEEDS_REAUTHORIZATION:
                        line += (
                            "; needs re-authorization (send a new"
                            " mission to continue)"
                        )
                    if entry["phase"] == wa_record.PHASE_BLOCKED:
                        block_reason = (
                            _latest_verification_block(entry)
                            or _latest_recovery_block(entry)
                        )
                        if block_reason is not None:
                            line += "; %s" % block_reason
                    # D-B4 (I5): a durably bound target-engine
                    # identity is shown; unresolved or absent
                    # renders nothing (never a fake binding).
                    bound_task = _bound_engine_task(entry)
                    if bound_task is not None:
                        line += "; target engine task: %s" % (
                            bound_task
                        )
                    line += "; %s" % _render_placeholder_status(
                        entry.get("result_placeholder")
                    )
                    if entry["verified_result"] is not None:
                        line += "; verified result %s" % (
                            _render_delivery_status(
                                entry["result_delivery"]
                            )
                        )
                    lines.append(line)
                    observation_line = _render_observation_line(entry)
                    if observation_line is not None:
                        lines.append(observation_line)
        runtime_running, runtime_detail = mission_module.runtime_status(
            self.store.directory
        )
        if runtime_running:
            lines.append("Runtime: running.")
        else:
            lines.append("ERROR: %s" % runtime_detail)
        lines.append(
            "Mission progress is queued/polled: the Runtime advances"
            " authorized workflows on its own schedule and there is no"
            " unsolicited streaming; re-send /status to refresh."
        )
        self._finish_item(item)
        self._interaction.send(chat_id, "\n".join(lines))

    # -- threads ---------------------------------------------------------

    def _worker_loop(self):
        while True:
            item = self._work_signals.get()
            if item is _WORKER_SENTINEL:
                return
            self.process_work_item(item)

    def run(self):
        """Blocking run: worker thread + poll loop with capped backoff."""
        self.startup_recovery()
        worker = threading.Thread(
            target=self._worker_loop, name="tgop-worker", daemon=False
        )
        worker.start()
        consecutive_failures = 0
        ceiling_reported = False
        try:
            while not self._stopping:
                # I5 D4: deliver any verified result the Runtime has
                # recorded but not yet delivered. Runs each loop
                # iteration so a COMPLETED workflow's result reaches
                # Telegram without a user prompt; delivery is recorded
                # durably so it can neither double-send nor drop.
                # I3: drive requested placeholders to bound BEFORE
                # delivering results. Ordering matters only for
                # promptness, not for correctness: each step is
                # independently fail-closed and store-only.
                self.ensure_result_placeholders()
                self.deliver_result_edits()
                self.deliver_pending_results()
                if self.poll_once():
                    consecutive_failures = 0
                    ceiling_reported = False
                    continue
                consecutive_failures += 1
                pause = min(
                    POLL_FAILURE_BACKOFF_BASE_SECONDS
                    * (2 ** (consecutive_failures - 1)),
                    POLL_FAILURE_BACKOFF_CEILING_SECONDS,
                )
                # Poll failures must leave a trace (round-2 finding
                # R2-N1): a revoked token or a transport bug must be
                # distinguishable from a healthy idle adapter. Emitted
                # on the FIRST failure of an outage and once more when
                # backoff reaches its ceiling; the problem text is
                # redacted and bounded before it ever reaches here.
                at_ceiling = (
                    pause >= POLL_FAILURE_BACKOFF_CEILING_SECONDS
                )
                if consecutive_failures == 1 or (
                    at_ceiling and not ceiling_reported
                ):
                    if at_ceiling:
                        ceiling_reported = True
                    self._error_writer(
                        "tgop: poll failure #%d (next retry in %ds):"
                        " %s\n" % (
                            consecutive_failures, pause,
                            self._last_poll_problem,
                        )
                    )
                self._failure_sleeper(pause)
        finally:
            self._work_signals.put(_WORKER_SENTINEL)
            worker.join()

    def stop(self):
        self._stopping = True
