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

The adapter talks ONLY to the injected Telegram API client and to the
Codex Gateway; it never imports or invokes any orchestration machinery
and never touches orchestration state on disk.
"""

import queue
import sys
import threading
import time

from codex_gateway import build_request as gateway_build_request
from codex_gateway import submit as gateway_submit
from codex_gateway.contract import STATUS_COMPLETED

from telegram_operator import approval as approval_module
from telegram_operator import authz, protocol, state as state_module
from telegram_operator import telegram_api

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
                 error_writer=None):
        self.config = config
        self.store = store
        self.api = api
        self._submit = submit_fn or gateway_submit
        self._build_request = build_request_fn or gateway_build_request
        self._clock = clock or time.time
        self._failure_sleeper = failure_sleeper or time.sleep
        self._error_writer = error_writer or sys.stderr.write
        self._state_lock = threading.Lock()
        self._document = store.load()
        self._work_signals = queue.Queue()
        self._stopping = False
        self._last_poll_problem = None

    # -- persistence helpers --------------------------------------------

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
            self.api.send_message(chat_id, "\n".join(texts))
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
        outcome = self.api.poll_updates(offset)
        if outcome.problem is not None:
            # Kept for the run loop's failure reporting (R2-N1); the
            # problem text is already redacted and bounded upstream.
            self._last_poll_problem = outcome.problem
            return False
        for update in outcome.updates:
            self.process_update(update)
        # Advance past every update whose id is readable, so a batch
        # containing malformed-but-identified updates cannot wedge the
        # poller. Per-update accepted state was already persisted above;
        # this final advance is idempotent for those.
        batch_ids = [
            update["update_id"]
            for update in outcome.updates
            if isinstance(update, dict)
            and isinstance(update.get("update_id"), int)
            and not isinstance(update.get("update_id"), bool)
        ]
        if batch_ids:
            with self._state_lock:
                self._advance_offset(max(batch_ids))
                self._save()
        return True

    def process_update(self, update):
        """Authenticate, then route one update. See the module rules."""
        decision = authz.authenticate_update(
            update, self.config.allowed_user_ids
        )
        if decision.update_id is not None:
            with self._state_lock:
                offset = self._document["update_offset"]
            if offset is not None and decision.update_id < offset:
                # Duplicate delivery of an already-accepted update
                # (Telegram redelivers when an offset is not yet
                # advanced); processing it again could double-queue.
                return
        if not decision.allowed:
            # Authorization precedes ALL content handling: nothing the
            # sender supplied is parsed or persisted, and no reply is
            # sent (an unknown sender learns nothing). The ONLY durable
            # effect is the transport update-offset advance below —
            # intended, so a denied update cannot wedge the poll loop.
            if decision.update_id is not None:
                with self._state_lock:
                    self._advance_offset(decision.update_id)
                    self._save()
            return
        if decision.kind == authz.KIND_MESSAGE:
            self._process_message(update, decision)
        else:
            self._process_callback(update, decision)

    def _advance_offset(self, update_id):
        current = self._document["update_offset"]
        if current is None or update_id + 1 > current:
            self._document["update_offset"] = update_id + 1

    def _process_message(self, update, decision):
        text = authz.message_text(update, decision)
        if text is None:
            with self._state_lock:
                self._advance_offset(decision.update_id)
                self._save()
            self.api.send_message(
                decision.chat_id,
                "Only plain text is supported. Send intent as text.",
            )
            return
        stripped = text.strip()
        if stripped == "/start" or stripped == "/help":
            with self._state_lock:
                self._advance_offset(decision.update_id)
                self._save()
            self.api.send_message(decision.chat_id, _HELP_TEXT)
            return
        if stripped == "/status":
            self._enqueue_or_report(
                decision,
                {
                    "kind": "status",
                    "chat_id": decision.chat_id,
                    "user_id": decision.user_id,
                    "update_id": decision.update_id,
                },
                acknowledgement="Gathering status…",
            )
            return
        if stripped.startswith("/mission"):
            stripped = stripped[len("/mission"):].strip()
        ok, problem = protocol.validate_intent(stripped)
        if not ok:
            with self._state_lock:
                self._advance_offset(decision.update_id)
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
            self.api.send_message(decision.chat_id, reply)
            return
        self._enqueue_or_report(
            decision,
            {
                "kind": "intent",
                "chat_id": decision.chat_id,
                "user_id": decision.user_id,
                "text": stripped,
                "update_id": decision.update_id,
            },
            acknowledgement="Received. Routing to the Codex Operator…",
        )

    def _enqueue_or_report(self, decision, item, acknowledgement):
        with self._state_lock:
            accepted = state_module.enqueue(self._document, item)
            depth = len(self._document["queue"])
            self._advance_offset(decision.update_id)
            self._save()
        if not accepted:
            self.api.send_message(
                decision.chat_id,
                "Work queue is full (%d of %d pending). This message"
                " was NOT queued; try again after pending work"
                " finishes." % (depth, state_module.MAX_QUEUE_DEPTH),
            )
            return
        self._work_signals.put(item)
        self.api.send_message(decision.chat_id, acknowledgement)

    def _process_callback(self, update, decision):
        data = authz.callback_data(update, decision)
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
                self._advance_offset(decision.update_id)
                self._save()
            self.api.answer_callback_query(
                decision.callback_id, "Unrecognized action; ignored."
            )
            return
        now = self._clock()
        with self._state_lock:
            record, problem = approval_module.evaluate_callback(
                self._document,
                approval_id=approval_id,
                user_id=decision.user_id,
                chat_id=decision.chat_id,
                repository=self.config.repository,
                message_id=decision.message_id,
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
                    update_id=decision.update_id,
                    now=now,
                )
                if not consumed:
                    problem = approval_module.PROBLEM_ALREADY_CONSUMED
            item = None
            if problem is None:
                item = {
                    "kind": "decision",
                    "chat_id": decision.chat_id,
                    "user_id": decision.user_id,
                    "approval_id": approval_id,
                    "decision": chosen,
                    "update_id": decision.update_id,
                }
                state_module.enqueue(self._document, item)
            self._advance_offset(decision.update_id)
            # One durable save carries consumption + queue + offset,
            # BEFORE any external acknowledgement or dispatch.
            self._save()
        if problem is not None:
            self.api.answer_callback_query(
                decision.callback_id,
                "Decision refused (%s). Nothing was dispatched." % problem,
            )
            return
        self._work_signals.put(item)
        self.api.answer_callback_query(
            decision.callback_id,
            "Decision recorded (%s). Dispatching…" % chosen,
        )
        self.api.edit_message_reply_markup(
            decision.chat_id, decision.message_id, None
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
        request = self._build_request(
            text,
            self.config.repository,
            session_id=session_id,
            source="telegram",
        )
        with self._state_lock:
            self._document["in_flight"] = {
                "kind": item["kind"],
                "chat_id": item["chat_id"],
                "request_id": request.request_id,
                "approval_id": item.get("approval_id"),
                "update_id": item.get("update_id"),
                "dispatched_at": self._clock(),
            }
            self._document["last_request"] = {
                "kind": item["kind"],
                "request_id": request.request_id,
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
                        "request_id": request.request_id,
                        "updated_at": self._clock(),
                    },
                )
            self._save()
        result = self._submit(request)
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
                    carried_request = request.request_id
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
        self.api.send_message(
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
        parsed = protocol.parse_operator_response(result.message)
        prefix = ""
        if neutralized:
            prefix = (
                "(Protocol-marker lines in your text were quoted before"
                " forwarding; typed text never carries approval"
                " authority.)\n"
            )
        if not parsed.ok:
            self.api.send_message(
                chat_id,
                prefix + "The Operator reply did not pass protocol"
                " validation (%s). No plan is available from this turn"
                " and nothing was approved." % parsed.problem,
            )
            return
        if parsed.kind == protocol.KIND_PLAN:
            self._offer_plan(item, result, parsed, prefix)
        else:
            self.api.send_message(
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
            self.api.send_message(
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
        if telegram_api.would_truncate(plan_text):
            # Refuse-before-send: the FULL text to display (prefix and
            # header included) exceeds what the chunk cap can deliver,
            # so an approval could only bind text the human never saw.
            # No approval record is created at all; the preview below
            # carries no buttons and send_message labels its own cut
            # inline.
            self.api.send_message(
                chat_id,
                prefix + "The Operator returned a plan too long to"
                " display completely (%d characters; at most %d can be"
                " shown). An approval must bind exactly the complete"
                " plan you saw, so NO approval was armed and no"
                " buttons are offered. Re-send your intent asking for"
                " a more concise plan.\n"
                "Undeliverable plan preview (NOT approvable):\n%s"
                % (len(plan_text), telegram_api.MAX_DELIVERABLE_CHARS,
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
            self.api.send_message(
                chat_id,
                "Plan received but the approval store is full (%s);"
                " approval buttons were NOT offered. Resolve pending"
                " approvals first." % problem,
            )
            return
        # The plan text goes out with NO reply_markup: at this instant
        # no actionable control exists anywhere — not on the phone, not
        # in the record (plan_message_id is still None).
        outcome = self.api.send_message(chat_id, plan_text)
        expected_chunks = telegram_api.chunk_count(plan_text)
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
                self.api.send_message(
                    chat_id,
                    "The plan was displayed, but its approval record"
                    " vanished before it could be armed, so no"
                    " approval buttons were offered and no decision"
                    " can be accepted. Re-send your intent for a"
                    " fresh plan.",
                )
                return
            keyboard = {
                "inline_keyboard": [[
                    {
                        "text": "Approve plan",
                        "callback_data": CALLBACK_APPROVE_PREFIX
                        + record["approval_id"],
                    },
                    {
                        "text": "Reject plan",
                        "callback_data": CALLBACK_REJECT_PREFIX
                        + record["approval_id"],
                    },
                ]]
            }
            offered, offer_problem = self.api.edit_message_reply_markup(
                chat_id, plan_message_id, keyboard
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
            self.api.send_message(
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
        self.api.send_message(
            chat_id,
            explanation + " Its approval was voided and cannot be"
            " decided. Re-send your intent for a fresh plan.",
        )

    def _work_decision(self, item):
        chat_id = item["chat_id"]
        approval_id = item["approval_id"]
        with self._state_lock:
            record = self._document["approvals"].get(approval_id)
            session = self._document["sessions"].get(str(chat_id))
        if record is None:
            self._finish_item(item)
            self.api.send_message(
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
            self.api.send_message(
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
        parsed = protocol.parse_operator_response(result.message)
        if not parsed.ok:
            self.api.send_message(
                chat_id,
                "Decision was dispatched, but the Operator reply did"
                " not pass protocol validation (%s). Use /status to"
                " follow up." % parsed.problem,
            )
            return
        self.api.send_message(
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
        self.api.send_message(chat_id, "\n".join(lines))
        if not session or not session.get("session_id"):
            self._finish_item(item)
            self.api.send_message(
                chat_id,
                "No Codex session for this chat yet; no engineering"
                " status to fetch.",
            )
            return
        result = self._dispatch_gateway_turn(
            item, protocol.build_status_text(), session["session_id"]
        )
        if result.status != STATUS_COMPLETED:
            self._report_gateway_failure(chat_id, result)
            return
        parsed = protocol.parse_operator_response(result.message)
        if not parsed.ok:
            self.api.send_message(
                chat_id,
                "Status turn returned no valid protocol envelope (%s)."
                % parsed.problem,
            )
            return
        self.api.send_message(
            chat_id, "[%s]\n%s" % (parsed.kind, parsed.body)
        )

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
