"""Grok MCP implementation of the provider-neutral human-interaction seam.

``GrokMcpInteractionAdapter`` is the ONLY module that maps one MCP
``tools/call`` onto ``human_interaction``. It is single-shot: it is
constructed with exactly ONE already-authenticated ``InteractionEvent``
(the bearer credential was verified at the HTTP layer before the event
was built; every id in it is DI-minted) and lives for one call.

The provider on the other side of this seam is a model-mediated tool
plane, not a messaging channel: Grok renders the tool result in its own
conversation. So ``send`` transmits NOTHING. It records the outbound
text in a per-call outbox and returns a real ``SendOutcome`` with
DI-minted message ids, an honest chunk count, and the exact number of
characters the chunk cap omitted. That is where DI declines to send an
addressed message into Grok — by construction, not by omission. The
controller packages the outbox into the structured tool result.

``receive`` yields the one seeded event on its first call and an idle
wait thereafter. ``send_once`` and ``edit`` operate on the same outbox.
Actions and controls do not exist on this transport, so ``acknowledge``
and ``offer_controls`` report ``(False, reason)``.

Construction touches nothing: it stores the event and the id minter.
This module imports no socket, HTTP client, or URL machinery, retries
nothing, sleeps nowhere, swallows nothing, and writes no state.
"""

from human_interaction import (
    SEND_APPLIED,
    EditOutcome,
    HumanInteractionAdapter,
    ReceiveOutcome,
    SendOnceOutcome,
    SendOutcome,
)

# Presentation limits of the structured result. A reply longer than
# MAX_MESSAGE_CHARS is split into chunks; at most MAX_MESSAGE_CHUNKS
# chunks are delivered and the remainder is counted, not hidden.
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGE_CHUNKS = 4
# Characters reserved at the end of the last kept chunk for the
# omission notice, so the exact omitted count is computed FIRST and
# the notice is formatted from that final number (the same fixed-
# reserve shape as the Telegram reference implementation).
TRUNCATION_NOTICE_RESERVE_CHARS = 64

OMISSION_LABEL = "\n[message cut here: %d further characters omitted]"


def _chunks(text):
    return [
        text[index:index + MAX_MESSAGE_CHARS]
        for index in range(0, len(text), MAX_MESSAGE_CHARS)
    ] or [""]


class GrokMcpInteractionAdapter(HumanInteractionAdapter):
    """Single-shot ``HumanInteractionAdapter`` for one MCP tool call."""

    def __init__(self, event, mint_id):
        self._event = event
        self._mint_id = mint_id
        self._received = False
        # (message_id, text) in delivery order.
        self._outbox = []

    @property
    def outbox(self):
        return tuple(self._outbox)

    def delivered_text(self):
        """Every delivered chunk, joined in delivery order."""
        return "".join(text for _, text in self._outbox)

    def receive(self, cursor):
        if self._received:
            return ReceiveOutcome(events=(), idle=True, problem=None)
        self._received = True
        return ReceiveOutcome(events=(self._event,), idle=False, problem=None)

    def send(self, conversation_id, text):
        chunks = _chunks(text)
        delivered = chunks[:MAX_MESSAGE_CHUNKS]
        truncated = 0
        if len(chunks) > MAX_MESSAGE_CHUNKS:
            dropped = sum(len(chunk) for chunk in chunks[MAX_MESSAGE_CHUNKS:])
            visible = delivered[-1][
                : MAX_MESSAGE_CHARS - TRUNCATION_NOTICE_RESERVE_CHARS
            ]
            # The EXACT omitted count: dropped chunks plus the tail of
            # the last kept chunk that the notice displaces.
            truncated = dropped + (len(delivered[-1]) - len(visible))
            delivered[-1] = visible + (OMISSION_LABEL % truncated)
        ids = []
        for chunk in delivered:
            message_id = self._mint_id()
            ids.append(message_id)
            self._outbox.append((message_id, chunk))
        return SendOutcome(
            ok=True, message_ids=tuple(ids), chunks_sent=len(delivered),
            truncated_chars=truncated, problem=None,
        )

    def send_once(self, conversation_id, text):
        message_id = self._mint_id()
        self._outbox.append((message_id, text[:MAX_MESSAGE_CHARS]))
        return SendOnceOutcome(
            classification=SEND_APPLIED, message_id=message_id,
            problem=None, detail=None,
        )

    def edit(self, conversation_id, message_id, text):
        for index, (known_id, known_text) in enumerate(self._outbox):
            if known_id == message_id:
                if known_text == text:
                    return EditOutcome(
                        ok=False, problem="not modified", detail=None,
                        already_applied=True,
                    )
                self._outbox[index] = (message_id, text)
                return EditOutcome(ok=True, problem=None, detail=None)
        return EditOutcome(
            ok=False, problem="target not found", detail=None,
            target_missing=True,
        )

    def acknowledge(self, action_id, text):
        return (False, "actions are not offered on this transport")

    def offer_controls(self, conversation_id, message_id, controls):
        return (False, "controls are not offered on this transport")

    def chunk_count(self, text):
        return len(_chunks(text))

    def would_truncate(self, text):
        return len(text) > self.max_deliverable_chars

    @property
    def max_message_chars(self):
        return MAX_MESSAGE_CHARS

    @property
    def max_deliverable_chars(self):
        return MAX_MESSAGE_CHARS * MAX_MESSAGE_CHUNKS
