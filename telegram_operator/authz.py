"""Authentication of Telegram updates BEFORE any content is touched.

Every inbound update is authenticated on its identity envelope alone —
update id, sender id, chat id, chat type — before one byte of message
content is parsed, stored, or logged. ``authenticate_update`` therefore
reads ONLY envelope keys and never ``text`` / ``data`` / ``caption`` /
entities; the test suite proves that behaviorally with recording
mappings, and the content accessors below refuse to run for a denied
decision. A denied decision carries a reason code and identity fields
only — never content — so nothing from an unauthenticated sender is
persisted anywhere.

Fail-closed rules: unknown users, non-private chats, chat/user identity
mismatches, and malformed envelopes are all denied, with no gateway
execution and no content persisted.
"""

from dataclasses import dataclass
from typing import Optional

KIND_MESSAGE = "message"
KIND_CALLBACK = "callback"

REASON_ALLOWED = "allowed"
REASON_MALFORMED_ENVELOPE = "malformed_envelope"
REASON_UNSUPPORTED_KIND = "unsupported_update_kind"
REASON_UNKNOWN_USER = "unknown_user"
REASON_NON_PRIVATE_CHAT = "non_private_chat"
REASON_CHAT_USER_MISMATCH = "chat_user_mismatch"


class ContentAccessDenied(Exception):
    """Content was requested for an update that was not authenticated."""


@dataclass(frozen=True)
class AuthDecision:
    """Outcome of authenticating one update's identity envelope.

    Denied decisions never carry content — only the reason code and
    whatever identity fields were validated before the failure.
    """

    allowed: bool
    reason: str
    kind: Optional[str]
    update_id: Optional[int]
    user_id: Optional[int]
    chat_id: Optional[int]
    message_id: Optional[int]
    callback_id: Optional[str]


def _denied(reason, kind=None, update_id=None, user_id=None, chat_id=None):
    return AuthDecision(
        allowed=False,
        reason=reason,
        kind=kind,
        update_id=update_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=None,
        callback_id=None,
    )


def _exact_int(value):
    """True only for genuine ints (bool is excluded on purpose)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _envelope_identity(container):
    """Extract (user_id, chat_id, chat_type, message_id) envelope fields.

    Returns None when the envelope is malformed. Touches ONLY identity
    keys; content keys are never read here.
    """
    if not isinstance(container, dict):
        return None
    sender = container.get("from")
    chat = container.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    user_id = sender.get("id")
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    message_id = container.get("message_id")
    if not _exact_int(user_id) or not _exact_int(chat_id):
        return None
    if not isinstance(chat_type, str):
        return None
    if message_id is not None and not _exact_int(message_id):
        return None
    return user_id, chat_id, chat_type, message_id


def authenticate_update(update, allowed_user_ids):
    """Authenticate one raw Telegram update by envelope identity only.

    ``allowed_user_ids`` is the exact numeric allowlist from the
    validated config. Returns an AuthDecision; when ``allowed`` is
    False, the caller must not parse, persist, or forward anything from
    this update.
    """
    if not isinstance(update, dict):
        return _denied(REASON_MALFORMED_ENVELOPE)
    update_id = update.get("update_id")
    if not _exact_int(update_id):
        return _denied(REASON_MALFORMED_ENVELOPE)
    has_message = "message" in update
    has_callback = "callback_query" in update
    if has_message == has_callback:
        # Neither, or both: not a supported single-payload update.
        return _denied(REASON_UNSUPPORTED_KIND, update_id=update_id)
    if has_message:
        identity = _envelope_identity(update.get("message"))
        if identity is None:
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_MESSAGE,
                update_id=update_id,
            )
        user_id, chat_id, chat_type, message_id = identity
        kind = KIND_MESSAGE
        callback_id = None
    else:
        callback = update.get("callback_query")
        if not isinstance(callback, dict):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
            )
        callback_id = callback.get("id")
        sender = callback.get("from")
        if not isinstance(callback_id, str) or not isinstance(sender, dict):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
            )
        user_id = sender.get("id")
        if not _exact_int(user_id):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
            )
        attached = callback.get("message")
        if not isinstance(attached, dict):
            # A callback with no reachable message cannot be bound to a
            # plan message; deny rather than guess.
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
                user_id=user_id,
            )
        chat = attached.get("chat")
        if not isinstance(chat, dict):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
                user_id=user_id,
            )
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        message_id = attached.get("message_id")
        if not _exact_int(chat_id) or not isinstance(chat_type, str):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
                user_id=user_id,
            )
        if message_id is not None and not _exact_int(message_id):
            return _denied(
                REASON_MALFORMED_ENVELOPE,
                kind=KIND_CALLBACK,
                update_id=update_id,
                user_id=user_id,
            )
        kind = KIND_CALLBACK
    is_allowed_user = any(
        user_id == allowed for allowed in allowed_user_ids
    )
    if not is_allowed_user:
        return _denied(
            REASON_UNKNOWN_USER,
            kind=kind,
            update_id=update_id,
            user_id=user_id,
            chat_id=chat_id,
        )
    if chat_type != "private":
        return _denied(
            REASON_NON_PRIVATE_CHAT,
            kind=kind,
            update_id=update_id,
            user_id=user_id,
            chat_id=chat_id,
        )
    if chat_id != user_id:
        return _denied(
            REASON_CHAT_USER_MISMATCH,
            kind=kind,
            update_id=update_id,
            user_id=user_id,
            chat_id=chat_id,
        )
    return AuthDecision(
        allowed=True,
        reason=REASON_ALLOWED,
        kind=kind,
        update_id=update_id,
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        callback_id=callback_id,
    )


def message_text(update, decision):
    """Return the message text for an ALLOWED message update.

    Refuses (ContentAccessDenied) for denied decisions or kind
    mismatches, so content can never be read past a failed
    authentication. Returns None when the allowed message simply has no
    text (for example a sticker).
    """
    if not decision.allowed or decision.kind != KIND_MESSAGE:
        raise ContentAccessDenied(
            "message content access refused: decision is %s/%s"
            % (decision.allowed, decision.kind)
        )
    text = update["message"].get("text")
    if not isinstance(text, str):
        return None
    return text


def callback_data(update, decision):
    """Return callback data for an ALLOWED callback update.

    Same refusal contract as ``message_text``. Returns None when the
    callback carries no usable string data.
    """
    if not decision.allowed or decision.kind != KIND_CALLBACK:
        raise ContentAccessDenied(
            "callback content access refused: decision is %s/%s"
            % (decision.allowed, decision.kind)
        )
    data = update["callback_query"].get("data")
    if not isinstance(data, str):
        return None
    return data
