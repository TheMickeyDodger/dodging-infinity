"""Server-originated elicitation inside one MCP tool call: the pending
table, the wait, the wire shapes, and the response check.

The Model Context Protocol lets a server ask the CLIENT's user a
question (``elicitation/create``) while a tool call is in flight; over
Streamable HTTP the request travels on the event stream of the POST
that carries the tool call, and the client's answer arrives as a
JSON-RPC response in a NEW POST on the same session. This module owns
everything on the server side of that round trip that is not HTTP:

- ``PendingTable``: the synchronized, bounded table of in-flight
  elicitations keyed ``(session_id, request_id)``. Every entry moves
  through ONE state transition under the table lock: ``pending`` to
  ``answered`` (a response admitted) or to ``abandoned`` (expiry,
  stream closure observed, write failure, server close). Admission and
  abandonment are arbitrated inside that one lock: a response is
  admitted only while the entry is pending, unexpired, AND the entry's
  stream probe (the socket check the handler owns, bound at
  registration BEFORE the entry is visible and before the request is
  written) does not observe a closure at that moment; a response
  arriving after a closure is observable is refused even before the
  waiter's next poll would have observed it, and there is no instant at
  which a pending entry exists without its probe. What the table
  records is what the server OBSERVED on its own socket, never a claim
  about the peer. Exactly one delivery reaches a waiter; a second
  response for the same id, a response on another session, an unknown
  id, or a response after abandonment is reported as unsolicited or
  replayed and changes nothing. Capacity is CLAIMED before a caller
  reserves anything durable (``claim``), so a competing call can never
  leave a reservation with no slot; a claim is consumed ONLY by a
  successful registration and otherwise stays with its holder, who
  releases it. Every exit from ``wait`` discards the entry, so the
  table never holds a waiter that is gone.
- ``wait``: a bounded wait on a ``threading.Event`` that wakes on
  delivery, expiry (the injected clock against a named validity bound),
  an observed stream closure (the bound probe), or server close. No
  thread is started and nothing sleeps: the handler thread that owns the
  HTTP connection is the one that waits, polling the event with a
  named, bounded interval so the stream probe runs between polls.
- ``build_request`` / ``evaluate_response``: the exact JSON-RPC request
  shape for form-mode elicitation and the closed evaluation of the
  client's answer. An accept counts ONLY when the single form field
  ``confirm`` equals the binding value the server asked for (the
  proposal-digest prefix the human sees in the card); any other content
  is a binding mismatch and records nothing. The answer is read from a
  JSON-RPC response object, never from a tool argument; nothing here
  reads ``arguments``.

Trust, stated exactly: this module proves that the authenticated
session's client returned an answer to the request the server sent it.
It does not identify the human; the Mission Core records that
provenance as a client confirmation with ``human_identity_proof`` null.

The module imports the standard library only. It writes no durable
state: the durable one-shot is the Mission Core's reserved decision id,
which the caller mints before asking and which the core consumes on
apply.
"""

import collections
import threading

from grok_mcp import protocol

# How long one elicitation stays answerable, from registration. A
# response after this is refused; the reservation the caller minted for
# it stays unconsumed and is reported as such. Exact-value pinned.
ELICITATION_VALIDITY_SECONDS = 900
# The bounded interval between event polls while waiting; the stream
# probe runs between polls. Exact-value pinned.
ELICITATION_POLL_SECONDS = 0.25
# Hard bound on concurrently pending elicitations per server. A further
# decision call refuses before anything is reserved. Exact-value pinned.
MAX_PENDING_ELICITATIONS = 8
# Consumed request ids remembered so a replay is named as such rather
# than as merely unsolicited; bounded, oldest evicted.
MAX_CONSUMED_IDS = 64

# Entry states: one transition out of PENDING, under the table lock.
STATE_PENDING = "pending"
STATE_ANSWERED = "answered"
STATE_ABANDONED = "abandoned"

# Outcomes of one elicitation, closed.
OUTCOME_ACCEPT = protocol.ELICITATION_ACTION_ACCEPT
OUTCOME_DECLINE = protocol.ELICITATION_ACTION_DECLINE
OUTCOME_CANCEL = protocol.ELICITATION_ACTION_CANCEL
OUTCOME_EXPIRED = "expired"
# The server observed, on its own socket, that the event stream carrying
# this elicitation was closed (EOF or error) before a response was
# admitted. It says nothing about why, or about what the peer saw.
OUTCOME_STREAM_CLOSED = "stream_closed"
OUTCOME_WRITE_FAILED = "write_failed"
OUTCOME_SERVER_CLOSED = "server_closed"
OUTCOME_TABLE_FULL = "table_full"
OUTCOME_CLIENT_ERROR = "client_error"
OUTCOME_BINDING_MISMATCH = "binding_mismatch"
OUTCOME_MALFORMED = "malformed_response"
# An unexpected exception anywhere on the stream path after the
# reservation: the entry is discarded, nothing is recorded, and the
# refusal still names the reserved decision id.
OUTCOME_STREAM_FAILED = "stream_failed"
OUTCOMES = (
    OUTCOME_ACCEPT, OUTCOME_DECLINE, OUTCOME_CANCEL, OUTCOME_EXPIRED,
    OUTCOME_STREAM_CLOSED, OUTCOME_WRITE_FAILED, OUTCOME_SERVER_CLOSED,
    OUTCOME_TABLE_FULL, OUTCOME_CLIENT_ERROR, OUTCOME_BINDING_MISMATCH,
    OUTCOME_MALFORMED, OUTCOME_STREAM_FAILED,
)

# What ``PendingTable.deliver`` reports for one client response.
DELIVERY_DELIVERED = "delivered"
DELIVERY_UNSOLICITED = "unsolicited"
DELIVERY_REPLAYED = "replayed"

# Refusal reasons a decision tool returns BEFORE anything is reserved.
REFUSAL_NOT_NEGOTIATED = "client_elicitation_not_negotiated"
REFUSAL_SSE_NOT_ACCEPTED = "client_sse_not_accepted"
REFUSAL_TABLE_FULL = "elicitation_table_full"

# Internal wait results.
_WAIT_DELIVERED = "delivered"


class Entry(object):
    """One pending elicitation; owned by the table, waited on by one
    handler thread. ``state`` changes exactly once, under the table
    lock; ``outcome`` names why it was abandoned; ``alive`` is the
    stream probe (True while no closure has been observed), bound at
    construction and consulted at admission and between polls."""

    def __init__(self, session_id, request_id, registered_at, deadline,
                 alive, confirm_value=None, on_admitted=None):
        self.session_id = session_id
        self.request_id = request_id
        self.registered_at = registered_at
        self.deadline = deadline
        self.event = threading.Event()
        self.response = None
        self.state = STATE_PENDING
        self.outcome = None
        self.alive = alive
        # The binding value the card asked for; when given, the answer
        # (outcome + content validity) is EVALUATED AT ADMISSION, under
        # the lock, and carried on ``answer`` and through ``on_admitted``
        # (the channel's note) from that moment on.
        self.confirm_value = confirm_value
        self.on_admitted = on_admitted
        self.answer = None


class PendingTable(object):
    """The synchronized pending table. Every method is safe to call
    from any handler thread; ``close_all`` is called by the server on
    close so no waiter outlives it."""

    def __init__(self, clock, validity_seconds=None, poll_seconds=None,
                 max_pending=None):
        self._clock = clock
        self.validity_seconds = (
            ELICITATION_VALIDITY_SECONDS if validity_seconds is None
            else validity_seconds
        )
        self.poll_seconds = (
            ELICITATION_POLL_SECONDS if poll_seconds is None else poll_seconds
        )
        self.max_pending = (
            MAX_PENDING_ELICITATIONS if max_pending is None else max_pending
        )
        self._lock = threading.Lock()
        self._entries = {}
        self._claimed = 0
        self._consumed = collections.deque()
        self._closed = False

    @property
    def count(self):
        with self._lock:
            return len(self._entries)

    @property
    def claimed(self):
        with self._lock:
            return self._claimed

    def _room(self):
        return len(self._entries) + self._claimed < self.max_pending

    def available(self):
        with self._lock:
            return not self._closed and self._room()

    def claim(self):
        """Atomically claim one slot BEFORE the caller reserves anything
        durable; False when the table is full or closed. A claim is
        consumed by ``register(..., claimed=True)`` or given back by
        ``release_claim``."""
        with self._lock:
            if self._closed or not self._room():
                return False
            self._claimed += 1
            return True

    def release_claim(self):
        with self._lock:
            if self._claimed > 0:
                self._claimed -= 1

    def register(self, session_id, request_id, alive, claimed=False,
                 on_registered=None, confirm_value=None, on_admitted=None):
        """Register ``(session_id, request_id)`` as pending with its stream
        probe ``alive`` bound BEFORE the entry becomes visible to
        ``deliver``: no response can be admitted against an entry without
        a probe. None when the table is closed, the key is already
        pending, or (without a prior claim) the table is full. A held
        claim is consumed ONLY by a successful registration; on None, or
        when the clock raises, it stays with the caller, who releases it.
        ``on_registered`` (the caller's "claim consumed" note) runs under
        the same lock right after insertion; if IT raises, the insertion
        and the claim consumption are undone atomically and the claim is
        still the caller's."""
        if not callable(alive):
            raise TypeError("a pending entry is registered with its probe")
        now = self._clock()
        with self._lock:
            key = (session_id, request_id)
            if self._closed or key in self._entries:
                return None
            holding = claimed and self._claimed > 0
            if not holding and not self._room():
                return None
            if holding:
                self._claimed -= 1
            entry = Entry(session_id, request_id, now,
                          now + self.validity_seconds, alive,
                          confirm_value, on_admitted)
            self._entries[key] = entry
            if on_registered is not None:
                try:
                    on_registered()
                except BaseException:
                    del self._entries[key]
                    if holding:
                        self._claimed += 1
                    raise
            return entry

    def discard(self, entry):
        with self._lock:
            self._remove(entry)

    def discard_key(self, session_id, request_id):
        """Remove a pending entry by key, for a caller whose ``register``
        result never reached it; a no-op when nothing is pending there."""
        with self._lock:
            self._entries.pop((session_id, request_id), None)

    def _remove(self, entry):
        key = (entry.session_id, entry.request_id)
        if self._entries.get(key) is entry:
            del self._entries[key]

    def _abandon(self, entry, outcome):
        """Under the lock: the one transition to ``abandoned``."""
        entry.state = STATE_ABANDONED
        entry.outcome = outcome
        self._remove(entry)
        entry.event.set()

    def _answered(self, entry):
        """Under the lock, after the transition to ``answered`` and before
        the waiter is woken. A no-op here; a test may override it to
        force the answered-then-closure-observed ordering."""

    def deliver(self, session_id, message):
        """Hand one client JSON-RPC response to its waiter, exactly once.
        Admission is decided under the table lock against the entry's
        state, its deadline and the stream probe bound at registration;
        the message text is never logged."""
        request_id = message.get("id") if isinstance(message, dict) else None
        if not isinstance(request_id, str):
            return DELIVERY_UNSOLICITED
        key = (session_id, request_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                if key in self._consumed:
                    return DELIVERY_REPLAYED
                return DELIVERY_UNSOLICITED
            if entry.state != STATE_PENDING:
                return DELIVERY_UNSOLICITED
            if self._clock() >= entry.deadline:
                self._abandon(entry, OUTCOME_EXPIRED)
                return DELIVERY_UNSOLICITED
            if not _alive(entry.alive):
                # Stream closure observed before admission: the answer
                # is refused HERE, atomically, not on the waiter's next
                # poll — and before the waiter even started polling.
                self._abandon(entry, OUTCOME_STREAM_CLOSED)
                return DELIVERY_UNSOLICITED
            # ADMISSION. The answer is evaluated first (a failure here
            # leaves the entry pending: nothing was admitted), then ONE
            # transition, then the answer is handed to the waiter's
            # channel. From this line on nothing downstream — cleanup,
            # logging, a call boundary — can change what was admitted.
            answer = None
            if entry.confirm_value is not None:
                answer = evaluate_response(message, entry.confirm_value)
            entry.state = STATE_ANSWERED
            entry.response = message
            entry.answer = answer
            self._remove(entry)
            self._consumed.append(key)
            while len(self._consumed) > MAX_CONSUMED_IDS:
                self._consumed.popleft()
            self._answered(entry)
            if entry.on_admitted is not None and answer is not None:
                entry.on_admitted(answer)
            entry.event.set()
            return DELIVERY_DELIVERED

    def close_all(self):
        """Abandon every pending entry with ``server_closed``; further
        claims and registrations refuse."""
        with self._lock:
            self._closed = True
            for entry in list(self._entries.values()):
                self._abandon(entry, OUTCOME_SERVER_CLOSED)
            self._entries.clear()

    def wait(self, entry):
        """Wait for the client's answer, bounded. Returns ``(outcome,
        response)``: ``("delivered", message)`` when a response was
        admitted; otherwise the abandonment outcome with ``None``. Expiry
        and an observed stream closure (through the probe bound at
        registration) are decided under the same lock admission uses, so
        the two can never both win. The entry is discarded on every
        exit."""
        try:
            while True:
                woke = entry.event.wait(self.poll_seconds)
                with self._lock:
                    if entry.state == STATE_ANSWERED:
                        return _WAIT_DELIVERED, entry.response
                    if entry.state == STATE_ABANDONED:
                        return entry.outcome or OUTCOME_SERVER_CLOSED, None
                    if woke:
                        # Woken without a transition: treat as closed.
                        self._abandon(entry, OUTCOME_SERVER_CLOSED)
                        return OUTCOME_SERVER_CLOSED, None
                    if self._clock() >= entry.deadline:
                        self._abandon(entry, OUTCOME_EXPIRED)
                        return OUTCOME_EXPIRED, None
                    if not _alive(entry.alive):
                        self._abandon(entry, OUTCOME_STREAM_CLOSED)
                        return OUTCOME_STREAM_CLOSED, None
        finally:
            # Cleanup never replaces the answer: every transition above
            # already removed the entry under the lock; this is the
            # safety net for a raise inside the loop, and best-effort.
            best_effort("cleanup discard after wait", self.discard, entry)


def best_effort(label, action, *args, **kwargs):
    """The ONE primitive for every non-authoritative side call on the
    decide path: cleanup (``discard``, ``discard_key``, ``release``,
    ``release_claim``) and failure/cleanup/presentation logging. Runs
    ``action(*args)``; an ``Exception`` from it is swallowed, then noted
    through ``note`` (keyword; a callable taking one string) as
    "<label> raised <Class>; suppressed", and a failure of that note is
    swallowed as well — logging a logging failure never raises. Returns
    True when the action completed. Nothing authoritative (a decision, a
    reservation, the admitted answer) is ever routed through here."""
    note = kwargs.pop("note", None)
    try:
        action(*args, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 - the whole point
        if note is not None:
            try:
                note("%s raised %s; suppressed" % (label, type(exc).__name__))
            except Exception:  # noqa: BLE001 - never raises
                pass
        return False


def _alive(probe):
    """The stream probe, never trusted to be quiet: a raising probe
    reads as a closure observed."""
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - the probe touches a socket
        return False


def build_request(request_id, protocol_version, message, confirm_value):
    """The ``elicitation/create`` JSON-RPC request for FORM mode.

    The 2025-11-25 revision names the mode explicitly; 2025-06-18 knows
    only form mode and defines no ``mode`` member, so it is omitted
    there. The requested schema has exactly one required string field
    whose single-value ``enum`` is the binding value: the human sees the
    full card in ``message`` and confirms by choosing that value.
    """
    params = {
        "message": message,
        "requestedSchema": {
            "type": "object",
            "properties": {
                protocol.ELICITATION_CONFIRM_FIELD: {
                    "type": "string",
                    "title": "Confirm the digest prefix shown in the card",
                    "enum": [confirm_value],
                },
            },
            "required": [protocol.ELICITATION_CONFIRM_FIELD],
        },
    }
    if protocol_version == protocol.SUPPORTED_PROTOCOL_VERSIONS[0]:
        params["mode"] = protocol.ELICITATION_MODE_FORM
    return {
        "jsonrpc": protocol.JSONRPC_VERSION,
        "id": request_id,
        "method": protocol.METHOD_ELICITATION_CREATE,
        "params": params,
    }


def evaluate_response(message, confirm_value):
    """Classify one delivered client response against the binding value.
    Returns ``(outcome, detail)``; only ``accept`` and ``decline`` are
    decisions. Reads a JSON-RPC response object and nothing else."""
    if not isinstance(message, dict):
        return OUTCOME_MALFORMED, "response is not an object"
    if "error" in message:
        return OUTCOME_CLIENT_ERROR, "the client answered with an error"
    result = message.get("result")
    if not isinstance(result, dict):
        return OUTCOME_MALFORMED, "response carries no result object"
    action = result.get("action")
    if action not in protocol.ELICITATION_ACTIONS:
        return OUTCOME_MALFORMED, "response action is not in the closed set"
    if action == protocol.ELICITATION_ACTION_DECLINE:
        return OUTCOME_DECLINE, None
    if action == protocol.ELICITATION_ACTION_CANCEL:
        return OUTCOME_CANCEL, None
    content = result.get("content")
    if not isinstance(content, dict) or sorted(content) != [
        protocol.ELICITATION_CONFIRM_FIELD
    ]:
        return OUTCOME_BINDING_MISMATCH, (
            "accept content must carry exactly the confirm field"
        )
    confirmed = content.get(protocol.ELICITATION_CONFIRM_FIELD)
    if not isinstance(confirmed, str) or confirmed != confirm_value:
        return OUTCOME_BINDING_MISMATCH, (
            "accept content does not confirm the digest prefix shown in the card"
        )
    return OUTCOME_ACCEPT, None


class ElicitationChannel(object):
    """What the server hands the controller for one decision call: a
    refusal reason (no round trip possible on this request) or a live
    ``elicit`` callable bound to the request's session and stream, plus
    the slot claim the caller takes BEFORE reserving anything durable
    and releases on every exit. The claim stays with the channel until
    the callee reports (``consume``) that a registration converted it
    into a pending entry; a callee that fails before that leaves the
    claim here, where ``release`` gives it back. ``last_result`` is the
    relay's own record of the result it computed after reserving an id
    (``(structured, is_error)``): the server presents it whenever the
    ordinary return path failed to hand a tool result back, so no raise
    at a call boundary can lose the reserved id or the admitted outcome."""

    def __init__(self, refusal=None, elicit_fn=None, claim_fn=None,
                 release_fn=None):
        self.refusal = refusal
        self._elicit_fn = elicit_fn
        self._claim_fn = claim_fn
        self._release_fn = release_fn
        self._holding = False
        self.last_result = None
        # The relay's carried post-mint state, attached as soon as it
        # exists, so a raise at any call boundary above the relay can
        # still be answered from it.
        self.state = None
        # The ADMITTED answer ``(outcome, detail)``, handed over by the
        # table at the moment of admission (``admit``): the relay records
        # from it even when the stream path raised after admission.
        self.answer = None

    def admit(self, answer):
        """The table's admission note: the answer, carried from here."""
        self.answer = answer

    @property
    def holding(self):
        """Whether an unconsumed claim is held here right now."""
        return self._holding

    def consume(self):
        """The callee reports the claim converted into a pending entry:
        nothing is owed back from here on."""
        self._holding = False

    def claim(self):
        """Claim one pending slot atomically; False when none is free."""
        if self.refusal is not None or self._claim_fn is None:
            return False
        self._holding = bool(self._claim_fn())
        return self._holding

    def release(self):
        """Give an unconsumed claim back; idempotent."""
        if self._holding:
            self._holding = False
            if self._release_fn is not None:
                self._release_fn()

    def elicit(self, request_id, message, confirm_value):
        """Send the request and wait; returns ``(outcome, detail)`` with
        ``outcome`` in OUTCOMES. The callee receives this channel and
        calls ``consume`` once its registration succeeded; until then the
        claim is still held here."""
        if self.refusal is not None or self._elicit_fn is None:
            return OUTCOME_TABLE_FULL, self.refusal or "no channel"
        return self._elicit_fn(request_id, message, confirm_value, self)


def refused_channel(reason):
    return ElicitationChannel(refusal=reason)
