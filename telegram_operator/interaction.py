"""Telegram implementation of the provider-neutral human-interaction seam.

``TelegramHumanInteractionAdapter`` is the ONLY module that maps
Telegram updates and Bot API calls onto ``human_interaction``. Every
method delegates EXACTLY ONCE to the injected ``TelegramApi`` client
and returns the client's outcome unchanged (or, for ``edit``, a neutral
projection of it). It retries nothing, sleeps nowhere, and swallows no
exception: the bounded retry that exists lives inside ``telegram_api``
and is unchanged.

Authentication-before-content is preserved verbatim: every update is
authenticated on its envelope alone by ``authz.authenticate_update``,
and content is read (``authz.message_text`` / ``authz.callback_data``)
ONLY when the decision is allowed. A denied event carries identity
fields and the reason code only, never content and never the raw
update.

Presentation limits are read from ``telegram_api`` module attributes at
call time; this module defines no bound constants of its own.
"""

from human_interaction import (
    EVENT_ACTION,
    EVENT_MESSAGE,
    EditOutcome,
    HumanInteractionAdapter,
    InteractionEvent,
    ReceiveOutcome,
)
from telegram_operator import authz
from telegram_operator import telegram_api

_KIND_MAP = {
    authz.KIND_MESSAGE: EVENT_MESSAGE,
    authz.KIND_CALLBACK: EVENT_ACTION,
}


class TelegramHumanInteractionAdapter(HumanInteractionAdapter):
    """Telegram-backed ``HumanInteractionAdapter``.

    Construction touches ``api`` for NOTHING; it is only stored.
    """

    def __init__(self, api, allowed_user_ids):
        self.api = api
        self.allowed_user_ids = allowed_user_ids

    def event_from_update(self, update):
        """Map one raw Telegram update to an InteractionEvent.

        Telegram-native; deliberately NOT part of the neutral ABC.
        Content is read only for an allowed decision.
        """
        decision = authz.authenticate_update(update, self.allowed_user_ids)
        content = None
        if decision.allowed:
            if decision.kind == authz.KIND_MESSAGE:
                content = authz.message_text(update, decision)
            elif decision.kind == authz.KIND_CALLBACK:
                content = authz.callback_data(update, decision)
        return InteractionEvent(
            sequence=decision.update_id,
            allowed=decision.allowed,
            reason=decision.reason,
            kind=_KIND_MAP.get(decision.kind),
            principal_id=decision.user_id,
            conversation_id=decision.chat_id,
            message_id=decision.message_id,
            action_id=decision.callback_id,
            content=content,
        )

    def receive(self, cursor):
        outcome = self.api.poll_updates(cursor)
        for attr in ("problem", "updates", "deadline_fired"):
            if not hasattr(outcome, attr):
                # Fail closed: an unrecognisable client outcome is a
                # transport problem, never an idle wait.
                return ReceiveOutcome(
                    events=(),
                    idle=False,
                    problem="poll outcome missing %r" % attr,
                )
        if outcome.problem is not None:
            return ReceiveOutcome(events=(), idle=False, problem=outcome.problem)
        if outcome.deadline_fired:
            return ReceiveOutcome(events=(), idle=True, problem=None)
        return ReceiveOutcome(
            events=tuple(self.event_from_update(u) for u in outcome.updates),
            idle=False,
            problem=None,
        )

    def send(self, conversation_id, text):
        return self.api.send_message(conversation_id, text)

    def send_once(self, conversation_id, text):
        return self.api.send_message_once(conversation_id, text)

    def edit(self, conversation_id, message_id, text):
        outcome = self.api.edit_message_text(conversation_id, message_id, text)
        detail = getattr(outcome, "detail", None)
        return EditOutcome(
            ok=getattr(outcome, "ok", False) is True,
            problem=getattr(outcome, "problem", None),
            detail=detail,
            already_applied=telegram_api.is_message_not_modified(detail),
            target_missing=telegram_api.is_message_to_edit_not_found(detail),
        )

    def acknowledge(self, action_id, text):
        return self.api.answer_callback_query(action_id, text)

    def offer_controls(self, conversation_id, message_id, controls):
        if controls is None:
            markup = None
        else:
            markup = {
                "inline_keyboard": [
                    [
                        {"text": control.label, "callback_data": control.action}
                        for control in controls
                    ]
                ]
            }
        return self.api.edit_message_reply_markup(
            conversation_id, message_id, markup
        )

    def chunk_count(self, text):
        return telegram_api.chunk_count(text)

    def would_truncate(self, text):
        return telegram_api.would_truncate(text)

    @property
    def max_message_chars(self):
        return telegram_api.MAX_MESSAGE_CHARS

    @property
    def max_deliverable_chars(self):
        return telegram_api.MAX_DELIVERABLE_CHARS
