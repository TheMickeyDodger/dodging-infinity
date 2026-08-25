"""Outbound-only Telegram Bot API client with an injected transport.

All traffic is OUTBOUND HTTPS initiated by the adapter: genuine Bot API
long polling via ``getUpdates`` with a positive server-side long-poll
duration, plus ``sendMessage`` / ``answerCallbackQuery`` /
``editMessageReplyMarkup``. There is no webhook, no listener, and no
inbound port, ever.

Deadline discipline (binding operator clarification, 2026-08-24, task
20260824-222824-105052): the client socket deadline is strictly GREATER
than the server-side long-poll duration by a hard constant margin, so a
valid idle long poll is never aborted by the client. Both values are
hard module constants, never input-derived, and the test suite asserts
their relationship. A client deadline firing on an idle long poll is
NORMAL — it is treated as an empty poll and never as a failure or a
reason to disturb the update offset. These deadlines apply ONLY to this
Telegram transport; the Codex Gateway subprocess keeps its existing
no-deadline behavior untouched.

The bot token sits in the URL PATH of every request, so every problem
string, exception text, and diagnostic that could carry a URL is
redacted here before it leaves this module. Tests inject a fake
transport; the default is a thin ``urllib.request`` wrapper and the
suite never touches the network.
"""

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

TELEGRAM_API_BASE = "https://api.telegram.org"

# --- Hard constants, never derived from input ---------------------------
# Server-side long-poll duration passed to getUpdates (seconds).
LONG_POLL_SECONDS = 50
# The client socket deadline exceeds the server-side long-poll duration
# by this margin, so the server always answers first on an idle poll.
SOCKET_DEADLINE_MARGIN_SECONDS = 10
# Client socket deadline for every request (seconds). Must be strictly
# greater than LONG_POLL_SECONDS; the test suite asserts it.
SOCKET_DEADLINE_SECONDS = LONG_POLL_SECONDS + SOCKET_DEADLINE_MARGIN_SECONDS
# Telegram's own per-message limit; longer text is chunked.
MAX_MESSAGE_CHARS = 4096
# At most this many chunks per logical message; text beyond it is cut
# with an explicit, labelled omission notice (never silently).
MAX_MESSAGE_CHUNKS = 5
# Longest text send_message can deliver COMPLETELY (chunk cap times
# chunk size); any longer text is cut with the labelled notice.
MAX_DELIVERABLE_CHARS = MAX_MESSAGE_CHARS * MAX_MESSAGE_CHUNKS
# Bounded retry for send-side calls: total attempts per request.
MAX_SEND_ATTEMPTS = 3
# Exponential backoff between retries, bounded by a hard ceiling.
RETRY_BACKOFF_BASE_SECONDS = 1
RETRY_BACKOFF_CEILING_SECONDS = 30
# Largest API response body this client will read (bytes).
MAX_RESPONSE_BYTES = 1048576
# Characters reserved at the end of the final kept chunk for the
# labelled truncation notice; sized well above the longest notice text.
TRUNCATION_NOTICE_RESERVE = 64
# Problem strings are bounded to this many characters (after redaction).
MAX_PROBLEM_CHARS = 500

REDACTED_TOKEN = "<bot-token-redacted>"


@dataclass(frozen=True)
class PollOutcome:
    """Result of one getUpdates long poll.

    ``deadline_fired`` marks the NORMAL idle case where the client
    socket deadline elapsed; it is an empty poll, not an error.
    ``problem`` is a redacted, bounded description of a genuine
    transport/API failure, or None.
    """

    updates: Tuple[dict, ...]
    deadline_fired: bool
    problem: Optional[str]


@dataclass(frozen=True)
class SendOutcome:
    """Result of one send-side API call (possibly chunked).

    ``truncated_chars`` is the EXACT number of characters omitted when
    the chunk cap bit (0 when nothing was omitted); the omission is
    also labelled inline in the delivered text.
    """

    ok: bool
    message_ids: Tuple[int, ...]
    chunks_sent: int
    truncated_chars: int
    problem: Optional[str]


def redact(text, token):
    """Remove every occurrence of the bot token from ``text``."""
    if not token:
        return text
    return str(text).replace(token, REDACTED_TOKEN)


def _bounded(text):
    text = str(text)
    if len(text) > MAX_PROBLEM_CHARS:
        return text[:MAX_PROBLEM_CHARS] + " [problem text capped]"
    return text


def chunk_count(text):
    """Chunks ``send_message`` would need for ``text``, BEFORE the cap.

    Mirrors ``send_message``'s chunking exactly (including its
    empty-text placeholder), so callers can decide before sending
    whether a text can be displayed completely, and can verify after
    sending that every expected chunk was delivered.
    """
    full_text = text if isinstance(text, str) else str(text)
    if not full_text:
        full_text = "(empty message)"
    return -(-len(full_text) // MAX_MESSAGE_CHARS)


def would_truncate(text):
    """True when ``send_message`` would omit any part of ``text``.

    Truncation happens exactly when the chunk count exceeds
    ``MAX_MESSAGE_CHUNKS`` — equivalently, when the text is longer
    than ``MAX_DELIVERABLE_CHARS``.
    """
    return chunk_count(text) > MAX_MESSAGE_CHUNKS


def default_transport(url, payload_bytes, deadline_seconds):
    """POST JSON to ``url`` and return ``(status_code, body_bytes)``.

    The only network touchpoint in the adapter. Raises OSError-family
    exceptions (including ``socket.timeout``) upward; the caller
    classifies and redacts them.
    """
    request = urllib.request.Request(
        url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=deadline_seconds) as reply:
        return reply.getcode(), reply.read(MAX_RESPONSE_BYTES + 1)


def _is_deadline_error(error):
    """True when an exception is the client socket deadline elapsing."""
    if isinstance(error, socket.timeout):
        return True
    if isinstance(error, urllib.error.URLError):
        return isinstance(getattr(error, "reason", None), socket.timeout)
    return False


class TelegramApi(object):
    """Bot API methods over an injected transport, with redaction."""

    def __init__(self, token, transport=None, sleeper=None):
        self._token = token
        self._transport = transport or default_transport
        self._sleeper = sleeper or time.sleep

    def _url(self, method):
        return "%s/bot%s/%s" % (TELEGRAM_API_BASE, self._token, method)

    def _problem(self, text):
        return _bounded(redact(text, self._token))

    def _call(self, method, payload, deadline_seconds):
        """One transport round trip. Returns ``(result, problem,
        deadline_fired, retryable)``; exactly one of result/problem/
        deadline_fired is meaningful."""
        body = json.dumps(payload).encode("utf-8")
        try:
            status, raw = self._transport(
                self._url(method), body, deadline_seconds
            )
        except urllib.error.HTTPError as error:
            if _is_deadline_error(error):
                return None, None, True, False
            retryable = error.code == 429 or 500 <= error.code <= 599
            return None, self._problem(
                "telegram api %s failed: HTTP %s" % (method, error.code)
            ), False, retryable
        except OSError as error:
            if _is_deadline_error(error):
                return None, None, True, False
            return None, self._problem(
                "telegram api %s failed: %s" % (method, error)
            ), False, True
        except Exception as error:
            # Terminal handler (round-1 review finding F2): the
            # transport can raise outside the OSError family — e.g.
            # http.client.InvalidURL is a ValueError, raised with the
            # full request URL (token included) in its message. Nothing
            # may escape this module unredacted, and one bad request
            # must degrade to a problem outcome, never kill the
            # poll/send loop. Unexpected exception types are classified
            # non-retryable: they signal a malformed request or a bug,
            # not a transient network condition.
            return None, self._problem(
                "telegram api %s failed unexpectedly (%s: %s)"
                % (method, type(error).__name__, error)
            ), False, False
        if len(raw) > MAX_RESPONSE_BYTES:
            return None, self._problem(
                "telegram api %s response exceeded the %d-byte read"
                " bound; refusing to parse a partial body"
                % (method, MAX_RESPONSE_BYTES)
            ), False, False
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            return None, self._problem(
                "telegram api %s returned undecodable JSON (%s)"
                % (method, error)
            ), False, True
        if not isinstance(document, dict) or document.get("ok") is not True:
            description = None
            if isinstance(document, dict):
                description = document.get("description")
            return None, self._problem(
                "telegram api %s returned ok=false (HTTP %s): %s"
                % (method, status, description)
            ), False, False
        return document.get("result"), None, False, False

    # -- polling ---------------------------------------------------------

    def poll_updates(self, offset):
        """The SINGLE poll transport site: one genuine long poll.

        Sends ``getUpdates`` with the positive server-side long-poll
        duration ``LONG_POLL_SECONDS`` and the strictly larger client
        socket deadline ``SOCKET_DEADLINE_SECONDS``. Any change to how
        this adapter polls Telegram is a change to this one function.
        Never retries internally and never sleeps: pacing comes from
        the long poll itself, and failure recovery (capped backoff)
        belongs to the caller's loop.
        """
        payload = {
            "timeout": LONG_POLL_SECONDS,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result, problem, deadline_fired, _ = self._call(
            "getUpdates", payload, SOCKET_DEADLINE_SECONDS
        )
        if deadline_fired:
            # NORMAL idle long poll: empty, not an error, offset
            # untouched by the caller.
            return PollOutcome(
                updates=(), deadline_fired=True, problem=None
            )
        if problem is not None:
            return PollOutcome(
                updates=(), deadline_fired=False, problem=problem
            )
        if not isinstance(result, list) or not all(
            isinstance(update, dict) for update in result
        ):
            return PollOutcome(
                updates=(),
                deadline_fired=False,
                problem=self._problem(
                    "telegram api getUpdates returned a malformed"
                    " result shape"
                ),
            )
        return PollOutcome(
            updates=tuple(result), deadline_fired=False, problem=None
        )

    # -- send side -------------------------------------------------------

    def _send_with_retry(self, method, payload):
        """Bounded attempts with capped exponential backoff."""
        problem = None
        for attempt in range(MAX_SEND_ATTEMPTS):
            if attempt:
                self._sleeper(
                    min(
                        RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                        RETRY_BACKOFF_CEILING_SECONDS,
                    )
                )
            result, problem, deadline_fired, retryable = self._call(
                method, payload, SOCKET_DEADLINE_SECONDS
            )
            if deadline_fired:
                problem = self._problem(
                    "telegram api %s hit the client deadline" % method
                )
                retryable = True
            elif problem is None:
                return result, None
            if not retryable:
                break
        return None, problem

    def send_message(self, chat_id, text, reply_markup=None):
        """Send ``text``, chunked to Telegram's message limit.

        At most ``MAX_MESSAGE_CHUNKS`` chunks are sent; omitted text is
        cut with an inline labelled notice and the exact omitted count
        is reported in ``truncated_chars`` — never silently. The
        ``reply_markup`` (inline keyboard) rides on the LAST chunk so
        buttons sit under the complete visible text.
        """
        full_text = text if isinstance(text, str) else str(text)
        if not full_text:
            full_text = "(empty message)"
        chunks = [
            full_text[start:start + MAX_MESSAGE_CHARS]
            for start in range(0, len(full_text), MAX_MESSAGE_CHARS)
        ]
        truncated_chars = 0
        if len(chunks) > MAX_MESSAGE_CHUNKS:
            kept = chunks[:MAX_MESSAGE_CHUNKS]
            dropped = sum(len(chunk) for chunk in chunks[MAX_MESSAGE_CHUNKS:])
            visible = kept[-1][: MAX_MESSAGE_CHARS - TRUNCATION_NOTICE_RESERVE]
            truncated_chars = dropped + (len(kept[-1]) - len(visible))
            notice = (
                "\n[message cut here: %d further characters omitted]"
                % truncated_chars
            )
            kept[-1] = visible + notice
            chunks = kept
        message_ids = []
        for index, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup is not None and index == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            result, problem = self._send_with_retry("sendMessage", payload)
            if problem is not None:
                return SendOutcome(
                    ok=False,
                    message_ids=tuple(message_ids),
                    chunks_sent=index,
                    truncated_chars=truncated_chars,
                    problem=problem,
                )
            identifier = None
            if isinstance(result, dict):
                identifier = result.get("message_id")
            if not isinstance(identifier, int) or isinstance(identifier, bool):
                return SendOutcome(
                    ok=False,
                    message_ids=tuple(message_ids),
                    chunks_sent=index + 1,
                    truncated_chars=truncated_chars,
                    problem=self._problem(
                        "telegram api sendMessage returned no usable"
                        " message_id"
                    ),
                )
            message_ids.append(identifier)
        return SendOutcome(
            ok=True,
            message_ids=tuple(message_ids),
            chunks_sent=len(chunks),
            truncated_chars=truncated_chars,
            problem=None,
        )

    def answer_callback_query(self, callback_id, text):
        """Acknowledge an inline-button press (bounded retry)."""
        _, problem = self._send_with_retry(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text},
        )
        return problem is None, problem

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        """Replace (or clear, with None) a message's inline keyboard."""
        payload = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        else:
            payload["reply_markup"] = {"inline_keyboard": []}
        _, problem = self._send_with_retry(
            "editMessageReplyMarkup", payload
        )
        return problem is None, problem
