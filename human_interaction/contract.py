"""Human-interaction seam: a provider-neutral transport boundary.

This module is PROVIDER-FREE. It imports no transport client, no
provider, no orchestration machinery, no authority store, and no
operator session; it is stdlib only. A provider implementation (for
example ``telegram_operator.interaction``) subclasses
``HumanInteractionAdapter`` and is the only place that knows the
provider's wire format.

The seam carries six things between a controller (a transport adapter
such as ``telegram_operator.adapter``) and whatever human-facing
channel it talks to:

- ``receive(cursor)``: ONE inbound wait, returning already-
  authenticated ``InteractionEvent`` objects. Every event is
  authenticated on its envelope alone; ``content`` is populated ONLY
  when ``allowed`` is True, so nothing from an unauthenticated sender
  ever crosses the seam. ``idle`` marks a normal empty wait; the caller
  leaves its own cursor untouched. ``sequence`` is the provider's
  monotonic event id and is the caller's durable cursor key; the seam
  never persists it.
- ``send`` / ``send_once``: one outbound message, with and without the
  provider's own bounded retry. ``SendOnceOutcome.classification`` is
  the three-valued send classification (``SEND_APPLIED``,
  ``SEND_DEFINITE_ZERO``, ``SEND_INDEFINITE``); it is never two-valued.
- ``edit``: one in-place edit. ``already_applied`` is True only under
  structured provider proof that the target already holds exactly this
  text; ``target_missing`` is True only when the target provably cannot
  be edited. Callers never replace a missing target.
- ``acknowledge`` / ``offer_controls``: acknowledge one action and
  attach (or clear) a row of ``Control`` buttons on a message.
- ``chunk_count`` / ``would_truncate`` / ``max_message_chars`` /
  ``max_deliverable_chars``: presentation-limit queries answered by the
  provider at call time, so callers can decide before sending whether a
  text can be displayed completely.

What the seam does NOT own, in plain terms: it holds no authority of
any kind. It does not create, validate, or consume a Mission
Authorization or any approval; it has no delivery or Git authority (no
commit, push, tag, release, or deploy); it does not decide mission
completion and produces no evidence; it does not control Herdr; it
performs no Operator reasoning; it reads and writes no durable state
(no cursor, no session, no workflow record); it has no retry policy of
its own beyond what the provider client already does inside one call;
and it has no relationship with ``OperatorSession`` — the two seams are
independent attributes of their caller and neither imports the other.
"""

import abc
from dataclasses import dataclass
from typing import Optional, Tuple

EVENT_MESSAGE = "message"
# A control press (Telegram callback).
EVENT_ACTION = "action"
EVENT_KINDS = (EVENT_MESSAGE, EVENT_ACTION)

# The three-valued send classification (R-5). Never two-valued.
SEND_APPLIED = "applied"
# The ONLY class that permits a retried send, so the only class that
# can ever manufacture a duplicate placeholder (R-7).
SEND_DEFINITE_ZERO = "definite_zero"
SEND_INDEFINITE = "indefinite"
SEND_CLASSIFICATIONS = (SEND_APPLIED, SEND_DEFINITE_ZERO, SEND_INDEFINITE)


@dataclass(frozen=True)
class InteractionEvent:
    """One authenticated inbound event.

    ``content`` is ALWAYS None when ``allowed`` is False; a denied event
    carries its reason code and identity fields only.
    """

    # Transport-monotonic id; the caller's durable cursor key; None when
    # unreadable.
    sequence: Optional[int]
    # Authenticated + authorized on the envelope alone.
    allowed: bool
    # Provider reason code (opaque to callers).
    reason: str
    # EVENT_MESSAGE | EVENT_ACTION | None.
    kind: Optional[str]
    # Authenticated human identity.
    principal_id: Optional[int]
    # Where replies go.
    conversation_id: Optional[int]
    # The message the event refers to (an action's bound message).
    message_id: Optional[int]
    # Handle for acknowledge(); actions only.
    action_id: Optional[str]
    # Message text / action data; ALWAYS None when not allowed.
    content: Optional[str]


@dataclass(frozen=True)
class ReceiveOutcome:
    """Result of one inbound wait."""

    events: Tuple[InteractionEvent, ...]
    # A normal empty wait; the caller leaves its cursor untouched.
    idle: bool
    # Transport problem; ``events`` is () when set.
    problem: Optional[str]


@dataclass(frozen=True)
class SendOutcome:
    """Result of one send-side call (possibly chunked).

    ``truncated_chars`` is the EXACT number of characters omitted when
    the chunk cap bit (0 when nothing was omitted); the omission is
    also labelled inline in the delivered text.
    """

    ok: bool
    message_ids: Tuple[int, ...]
    chunks_sent: int
    truncated_chars: int
    problem: Optional[str]


@dataclass(frozen=True)
class SendOnceOutcome:
    """Result of ONE placeholder send attempt (never retried here)."""

    classification: str
    message_id: Optional[int] = None
    problem: Optional[str] = None
    detail: Optional[object] = None


@dataclass(frozen=True)
class EditOutcome:
    """Result of one bounded in-place edit.

    ``detail`` is the provider's opaque call detail. The two neutral
    flags are derived by the provider implementation from structured
    proof only; a bare failure sets neither.
    """

    ok: bool
    problem: Optional[str] = None
    detail: Optional[object] = None
    # The target provably already holds exactly this text (R-2 proof).
    already_applied: bool = False
    # The target is gone / cannot be edited (R-3; never replace).
    target_missing: bool = False


@dataclass(frozen=True)
class Control:
    """One offered control (button)."""

    label: str
    # Opaque; returned verbatim as an EVENT_ACTION's content.
    action: str


class HumanInteractionAdapter(abc.ABC):
    """The provider-neutral human-interaction boundary.

    Every method makes at most ONE provider call, returns the provider
    client's outcome (or a neutral projection of it) unchanged, retries
    nothing of its own, and swallows nothing.
    """

    @abc.abstractmethod
    def receive(self, cursor):
        """One inbound wait from ``cursor``; returns a ReceiveOutcome."""

    @abc.abstractmethod
    def send(self, conversation_id, text):
        """Send ``text`` (provider may chunk/retry); returns a SendOutcome."""

    @abc.abstractmethod
    def send_once(self, conversation_id, text):
        """Send ``text`` exactly once; returns a SendOnceOutcome."""

    @abc.abstractmethod
    def edit(self, conversation_id, message_id, text):
        """Edit one message in place; returns an EditOutcome."""

    @abc.abstractmethod
    def acknowledge(self, action_id, text):
        """Acknowledge one action; returns ``(ok, problem)``."""

    @abc.abstractmethod
    def offer_controls(self, conversation_id, message_id, controls):
        """Attach one row of Controls to a message, or clear it (None).

        Returns ``(ok, problem)``.
        """

    @abc.abstractmethod
    def chunk_count(self, text):
        """Chunks ``send`` would need for ``text``, BEFORE any cap."""

    @abc.abstractmethod
    def would_truncate(self, text):
        """True when ``send`` would omit any part of ``text``."""

    @property
    @abc.abstractmethod
    def max_message_chars(self):
        """The provider's per-message character limit."""

    @property
    @abc.abstractmethod
    def max_deliverable_chars(self):
        """The most characters one ``send`` can deliver completely."""
