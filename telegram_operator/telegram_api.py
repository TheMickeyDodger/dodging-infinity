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


# --- DI-REMOTE-3 I2: structured call detail and send classification ---
#
# Everything below this comment is ADDITIVE. ``_call`` keeps its exact
# return arity and its exact problem-string texts (existing tests pin
# both); the structured detail is carried by a sibling
# ``_call_structured`` that ``_call`` now wraps, and the existing
# ``retryable`` flag keeps its current meaning for its existing
# callers. The new classification is derived from the STRUCTURED
# outcome and never reuses ``retryable`` (plan §3.2, final paragraph:
# the flag's current values are deliberately WRONG for this path).

# Closed set of structural call outcomes. What a caller may conclude
# about EFFECT depends on which of these occurred, not on HTTP status.
CALL_OK = "ok"
# A genuine Telegram JSON object with ``ok: false`` was PARSED: the
# request reached Telegram, Telegram understood it and refused it.
CALL_TELEGRAM_REFUSED = "telegram_refused"
# A response arrived but no genuine Telegram ``ok: false`` document
# could be parsed from it. R-7: this proves NOTHING about effect — an
# intermediary proxy, gateway or load balancer can emit a bare 4xx,
# and Telegram may still have created the message.
CALL_NO_TELEGRAM_BODY = "no_telegram_body"
CALL_DEADLINE = "deadline"
CALL_OS_ERROR = "os_error"
CALL_UNDECODABLE_BODY = "undecodable_body"
CALL_RESPONSE_OVER_BOUND = "response_over_bound"
# An unexpected non-OSError exception. LABELLED ASSUMPTION (not proven
# by any test here): these come from request CONSTRUCTION — a
# malformed URL raises http.client.InvalidURL, a ValueError, before
# any byte is written — so nothing reached Telegram. This is the only
# non-parsed outcome that may be treated as definite-zero, and it is
# the assumption a reviewer should attack if this classification is
# ever questioned.
CALL_REQUEST_NOT_SENT = "request_not_sent"
CALL_OUTCOMES = (
    CALL_OK,
    CALL_TELEGRAM_REFUSED,
    CALL_NO_TELEGRAM_BODY,
    CALL_DEADLINE,
    CALL_OS_ERROR,
    CALL_UNDECODABLE_BODY,
    CALL_RESPONSE_OVER_BOUND,
    CALL_REQUEST_NOT_SENT,
)

# The three-valued send classification (R-5). Never two-valued.
SEND_APPLIED = "applied"
# The ONLY class that permits a retried sendMessage, so the only class
# that can ever manufacture a duplicate placeholder (R-7).
SEND_DEFINITE_ZERO = "definite_zero"
SEND_INDEFINITE = "indefinite"
SEND_CLASSIFICATIONS = (SEND_APPLIED, SEND_DEFINITE_ZERO, SEND_INDEFINITE)


@dataclass(frozen=True)
class CallDetail:
    """Structured description of ONE transport round trip.

    Additive to ``_call``'s existing 4-tuple. ``description`` is the
    RAW Telegram description (token-redacted and bounded like every
    other text leaving this module) — the folded, human-formatted
    problem string destroys it, which is why R-2/R-3 cannot be
    satisfied without this record.
    """

    outcome: str
    http_status: Optional[int] = None
    # True only when a genuine Telegram JSON object was parsed.
    body_parsed: bool = False
    telegram_ok: Optional[bool] = None
    description: Optional[str] = None
    error_code: Optional[int] = None


def _usable_message_id(result):
    """The int message_id in a sendMessage result, or None.

    ``bool`` is excluded: it is a subclass of ``int``, so ``True``
    could otherwise masquerade as message id 1.
    """
    if not isinstance(result, dict):
        return None
    identifier = result.get("message_id")
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        return None
    return identifier


def classify_send_once(detail, result=None):
    """The FULL plan §3.2 table, as one pure function.

    Three-valued (R-5), and it branches on BODY PARSEABILITY, never on
    HTTP status alone (R-7). ``definite_zero`` requires POSITIVE proof
    of no effect; every ambiguous condition fails closed to
    ``indefinite``, which is safe by design — the converse error is
    the duplicate-placeholder generator this whole design exists to
    remove.
    """
    outcome = getattr(detail, "outcome", None)
    if outcome == CALL_OK:
        # ok=true WITH a usable int message_id is the only applied
        # case; ok=true without one cannot prove which object exists.
        if _usable_message_id(result) is None:
            return SEND_INDEFINITE
        return SEND_APPLIED
    if outcome == CALL_TELEGRAM_REFUSED:
        status = detail.http_status
        if status is not None and (status == 429 or 500 <= status <= 599):
            # Telegram answered, but a rate-limit or a server-side
            # error cannot prove the message was not created first.
            # Plan §3.2 lists 429/5xx as indefinite; a parsed body does
            # not upgrade them.
            return SEND_INDEFINITE
        return SEND_DEFINITE_ZERO
    if outcome == CALL_REQUEST_NOT_SENT:
        return SEND_DEFINITE_ZERO
    # CALL_NO_TELEGRAM_BODY, CALL_DEADLINE, CALL_OS_ERROR,
    # CALL_UNDECODABLE_BODY, CALL_RESPONSE_OVER_BOUND — and any
    # outcome this function does not recognise. Unknown fails closed
    # rather than falling through to a permissive default.
    return SEND_INDEFINITE


# --- R-2/R-3 description matching (rigorous, never a substring scan) --
#
# Telegram emits the not-modified condition in two documented forms:
# the bare phrase and the long explanatory variant. Both are listed
# EXACTLY. Matching is exact membership of the NORMALIZED description
# in a closed set — never ``in``, never a prefix test — so a
# superstring such as "message is not modified by someone else" does
# NOT match.
MESSAGE_NOT_MODIFIED_FORMS = frozenset((
    "message is not modified",
    "message is not modified: specified new message content and reply"
    " markup are exactly the same as a current content and reply markup"
    " of the message",
))
MESSAGE_TO_EDIT_NOT_FOUND_FORMS = frozenset((
    "message to edit not found",
))
# Telegram prefixes refusals with this exact status phrase. Stripping
# it is a defined normalization step, not a fuzzy match.
_DESCRIPTION_PREFIX = "bad request: "


def normalize_description(description):
    """Casefold, collapse whitespace, drop the 'Bad Request: ' prefix.

    Returns None for anything that is not a string.
    """
    if not isinstance(description, str):
        return None
    normalized = " ".join(description.split()).strip().casefold()
    if normalized.startswith(_DESCRIPTION_PREFIX):
        normalized = normalized[len(_DESCRIPTION_PREFIX):]
    return normalized


def _matches_forms(detail, forms):
    """Exact membership, and ONLY under structured proof (R-2 clause 4).

    A description is honoured only when it came from a genuine parsed
    Telegram ``ok: false`` document. An inferred or reconstructed
    description never reaches this.
    """
    if getattr(detail, "outcome", None) != CALL_TELEGRAM_REFUSED:
        return False
    if not detail.body_parsed or detail.telegram_ok is not False:
        return False
    return normalize_description(detail.description) in forms


def is_message_not_modified(detail):
    """R-2: Telegram's message-not-modified condition, under proof.

    STATED BOT API ASSUMPTION, not a property any test here proves:
    only this bot can edit its own messages, so this response on the
    bot's own bound object proves the object already holds exactly the
    intended content. The caller must still check that the edit
    targeted the bound (chat_id, message_id) and that the rendered
    digest matches the current verified result — this function answers
    only clauses 3 and 4 of plan §3.4.
    """
    return _matches_forms(detail, MESSAGE_NOT_MODIFIED_FORMS)


def is_message_to_edit_not_found(detail):
    """R-3: the bound object is gone. The caller must fail CLOSED and
    must never send a replacement message."""
    return _matches_forms(detail, MESSAGE_TO_EDIT_NOT_FOUND_FORMS)


@dataclass(frozen=True)
class SendOnceOutcome:
    """Result of ONE placeholder send attempt (never retried here)."""

    classification: str
    message_id: Optional[int] = None
    problem: Optional[str] = None
    detail: Optional[CallDetail] = None


@dataclass(frozen=True)
class EditOutcome:
    """Result of a bounded editMessageText."""

    ok: bool
    problem: Optional[str] = None
    detail: Optional[CallDetail] = None


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

    def _description(self, value):
        """A Telegram description, redacted and bounded like every
        other text leaving this module.

        A description longer than the bound is truthfully labelled by
        ``_bounded`` and therefore cannot equal any known short form —
        it fails CLOSED, which is the safe direction for R-2/R-3.
        """
        if not isinstance(value, str):
            return None
        return _bounded(redact(value, self._token))

    def _detail_from_http_error(self, error):
        """Read and parse an ``HTTPError``'s BODY (I2, mandatory).

        ``default_transport`` uses ``urlopen``, which RAISES
        ``HTTPError`` on every non-2xx, and Telegram returns both
        ``message is not modified`` and ``message to edit not found``
        as HTTP 400 with a JSON ``ok: false`` body. Before this, the
        branch read ``error.code`` only and the body was discarded
        unread, so on the REAL transport those descriptions never
        existed in the process at all — R-2 and R-3 were unsatisfiable
        and R-7's ``definite_zero`` was unreachable. Verified
        first-hand against a local stub server before this was written.

        ``HTTPError`` is itself file-like, so the body is read under
        the SAME ``MAX_RESPONSE_BYTES`` bound and the same decode
        discipline as the success path. Any failure to read degrades
        to "no Telegram body" (hence ``indefinite``); nothing
        propagates. The read is deliberately guarded with a bare
        ``except Exception``: an ``HTTPError`` constructed with
        ``fp=None`` — the shape the existing tests use — raises
        ``KeyError`` from ``tempfile``, not an ``OSError``.
        """
        status = getattr(error, "code", None)
        absent = CallDetail(
            outcome=CALL_NO_TELEGRAM_BODY, http_status=status
        )
        try:
            raw = error.read(MAX_RESPONSE_BYTES + 1)
        except Exception:
            return absent
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            return absent
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return absent
        return self._refusal_detail(document, status) or absent

    def _refusal_detail(self, document, status):
        """A CallDetail for a genuine parsed Telegram refusal, else None.

        "Genuine" is strict: a JSON OBJECT carrying ``ok`` exactly
        ``False``. A non-object body, a missing ``ok``, or ``ok: true``
        on an error response is NOT proof that Telegram refused, so it
        yields None and the caller falls back to "no Telegram body".
        """
        if not isinstance(document, dict):
            return None
        if document.get("ok") is not False:
            return None
        error_code = document.get("error_code")
        if isinstance(error_code, bool) or not isinstance(error_code, int):
            error_code = None
        return CallDetail(
            outcome=CALL_TELEGRAM_REFUSED,
            http_status=status,
            body_parsed=True,
            telegram_ok=False,
            description=self._description(document.get("description")),
            error_code=error_code,
        )

    def _call(self, method, payload, deadline_seconds):
        """One transport round trip. Returns ``(result, problem,
        deadline_fired, retryable)``; exactly one of result/problem/
        deadline_fired is meaningful.

        Unchanged in arity and in every problem-string text. It is now
        a thin wrapper over ``_call_structured``, which additionally
        returns the structured ``CallDetail`` the I2 callers need.
        """
        return self._call_structured(method, payload, deadline_seconds)[:4]

    def _call_structured(self, method, payload, deadline_seconds):
        """``_call`` plus a fifth element: the structured CallDetail."""
        body = json.dumps(payload).encode("utf-8")
        try:
            status, raw = self._transport(
                self._url(method), body, deadline_seconds
            )
        except urllib.error.HTTPError as error:
            if _is_deadline_error(error):
                return None, None, True, False, CallDetail(
                    outcome=CALL_DEADLINE
                )
            retryable = error.code == 429 or 500 <= error.code <= 599
            return None, self._problem(
                "telegram api %s failed: HTTP %s" % (method, error.code)
            ), False, retryable, self._detail_from_http_error(error)
        except OSError as error:
            if _is_deadline_error(error):
                return None, None, True, False, CallDetail(
                    outcome=CALL_DEADLINE
                )
            return None, self._problem(
                "telegram api %s failed: %s" % (method, error)
            ), False, True, CallDetail(outcome=CALL_OS_ERROR)
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
            ), False, False, CallDetail(outcome=CALL_REQUEST_NOT_SENT)
        if len(raw) > MAX_RESPONSE_BYTES:
            return None, self._problem(
                "telegram api %s response exceeded the %d-byte read"
                " bound; refusing to parse a partial body"
                % (method, MAX_RESPONSE_BYTES)
            ), False, False, CallDetail(
                outcome=CALL_RESPONSE_OVER_BOUND, http_status=status
            )
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            return None, self._problem(
                "telegram api %s returned undecodable JSON (%s)"
                % (method, error)
            ), False, True, CallDetail(
                outcome=CALL_UNDECODABLE_BODY, http_status=status
            )
        if not isinstance(document, dict) or document.get("ok") is not True:
            description = None
            if isinstance(document, dict):
                description = document.get("description")
            detail = self._refusal_detail(document, status) or CallDetail(
                outcome=CALL_NO_TELEGRAM_BODY, http_status=status
            )
            return None, self._problem(
                "telegram api %s returned ok=false (HTTP %s): %s"
                % (method, status, description)
            ), False, False, detail
        return document.get("result"), None, False, False, CallDetail(
            outcome=CALL_OK,
            http_status=status,
            body_parsed=True,
            telegram_ok=True,
        )

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
        """Bounded attempts with capped exponential backoff.

        Unchanged for every existing caller; it now discards the
        structured detail its sibling returns.
        """
        return self._send_with_retry_detail(method, payload)[:2]

    def _send_with_retry_detail(self, method, payload):
        """``_send_with_retry`` plus the last attempt's CallDetail.

        ONE retry loop serves both, so the existing bounded-attempt and
        capped-backoff behaviour cannot drift between them.
        """
        problem = None
        detail = None
        for attempt in range(MAX_SEND_ATTEMPTS):
            if attempt:
                self._sleeper(
                    min(
                        RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                        RETRY_BACKOFF_CEILING_SECONDS,
                    )
                )
            (
                result, problem, deadline_fired, retryable, detail
            ) = self._call_structured(
                method, payload, SOCKET_DEADLINE_SECONDS
            )
            if deadline_fired:
                problem = self._problem(
                    "telegram api %s hit the client deadline" % method
                )
                retryable = True
            elif problem is None:
                return result, None, detail
            if not retryable:
                break
        return None, problem, detail

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

    # -- DI-REMOTE-3 I2: placeholder send and bounded edit -------------

    def send_message_once(self, chat_id, text):
        """Send ONE sendMessage. Never retries. Never chunks.

        This is the placeholder-send entry, and it deliberately does
        NOT go through ``_send_with_retry``: that seam converts a fired
        client deadline into ``retryable=True`` and re-sends, which on
        a non-idempotent ``sendMessage`` is precisely a
        duplicate-placeholder generator (R-5). Exactly one transport
        call is made per invocation, on every path that reaches the
        transport at all.

        It never chunks either. ``send_message`` splits a long text
        into up to ``MAX_MESSAGE_CHUNKS`` separate messages; for a
        placeholder that would create several objects where the design
        requires exactly one bindable object. An over-long text is
        REFUSED before the transport is touched.

        Returns a ``SendOnceOutcome`` carrying the three-valued
        classification (R-5), the bound ``message_id`` when applied,
        a redacted problem string, and the structured ``CallDetail``.
        """
        full_text = text if isinstance(text, str) else str(text)
        if not full_text:
            full_text = "(empty message)"
        if len(full_text) > MAX_MESSAGE_CHARS:
            # Refused BEFORE the transport: nothing was sent, so this
            # is definite-zero with absolute proof of no effect (no
            # request left this process). Retrying is safe and will
            # refuse identically — it can never duplicate.
            return SendOnceOutcome(
                classification=SEND_DEFINITE_ZERO,
                problem=self._problem(
                    "telegram api sendMessage refused: placeholder text"
                    " is %d characters and the single-message limit is"
                    " %d; this path never chunks"
                    % (len(full_text), MAX_MESSAGE_CHARS)
                ),
            )
        payload = {"chat_id": chat_id, "text": full_text}
        (
            result, problem, deadline_fired, _, detail
        ) = self._call_structured(
            "sendMessage", payload, SOCKET_DEADLINE_SECONDS
        )
        if deadline_fired:
            # R-5: a fired deadline is INDEFINITE here, never
            # retryable. The request may have reached Telegram.
            problem = self._problem(
                "telegram api sendMessage hit the client deadline; the"
                " outcome is INDEFINITE and is never retried on this"
                " path"
            )
        classification = classify_send_once(detail, result)
        message_id = None
        if classification == SEND_APPLIED:
            message_id = _usable_message_id(result)
        elif problem is None:
            problem = self._problem(
                "telegram api sendMessage returned ok=true with no"
                " usable message_id; the outcome is INDEFINITE"
            )
        return SendOnceOutcome(
            classification=classification,
            message_id=message_id,
            problem=problem,
            detail=detail,
        )

    def edit_message_text(self, chat_id, message_id, text):
        """Edit a pre-bound message, through the bounded retry seam.

        Blanket retry IS safe here, and that is the whole point of the
        placeholder design: ``editMessageText`` against an already-bound
        ``(chat_id, message_id)`` with a byte-identical payload is
        idempotent, so a replayed attempt leaves exactly the same
        visible state (R-5, final sentence). Attempts are bounded by
        ``MAX_SEND_ATTEMPTS`` with the existing capped backoff.

        The payload is exactly ``chat_id``/``message_id``/``text`` —
        no ``parse_mode``, no ``reply_markup``. R-1 requires the edit
        payload to be byte-identical across replays; omitting those
        fields entirely makes that true BY CONSTRUCTION rather than by
        care. Never chunks: an over-long text is refused before the
        transport, so the result path can fail closed instead of
        splitting a result across messages.
        """
        full_text = text if isinstance(text, str) else str(text)
        if not full_text:
            full_text = "(empty message)"
        if len(full_text) > MAX_MESSAGE_CHARS:
            return EditOutcome(
                ok=False,
                problem=self._problem(
                    "telegram api editMessageText refused: rendered text"
                    " is %d characters and the single-message limit is"
                    " %d; this path never chunks and never truncates"
                    % (len(full_text), MAX_MESSAGE_CHARS)
                ),
            )
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": full_text,
        }
        _, problem, detail = self._send_with_retry_detail(
            "editMessageText", payload
        )
        return EditOutcome(
            ok=problem is None, problem=problem, detail=detail
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
