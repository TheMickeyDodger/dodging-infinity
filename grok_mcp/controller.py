"""One bounded MCP tool call, end to end, across the neutral seams.

``GrokMcpController`` is the Grok-side controller. Per ``tools/call``
it validates the arguments against the exact tool schema, and for
``di_operator_turn`` it:

1. mints ONE ``InteractionEvent`` (every id DI-owned: ``sequence``,
   ``conversation_id`` and ``message_id`` from a DI counter,
   ``principal_id`` the ordinal of the configured connector credential,
   which denotes "the holder of configured credential N" and never a
   Grok user), seeds a single-shot ``GrokMcpInteractionAdapter`` with
   it, and reads it back through ``adapter.receive`` — the same seam
   the Telegram controller crosses;
2. calls the injected ``OperatorSession``'s ``prepare`` then
   ``execute`` exactly once, with the caller's text, the configured
   repository, and the provider ``session_id`` that a DI-minted
   ``session_ref`` maps to (opaque continuation state, its documented
   use);
3. hands the reply to ``adapter.send`` — which transmits nothing — and
   packages the adapter's outbox into ``structuredContent``.

Identity is DI-minted only. A caller-supplied ``session_ref`` or
``turn_ref`` is accepted ONLY when it validates against this
controller's own bounded in-memory tables; anything else is refused
with an observable reason and never recorded as identity. No input
names or accepts a Grok conversation id, message id, user id, or
thread id.

Idempotency and its stated limit: ``di_status`` and ``di_ping`` are
pure. ``di_operator_turn`` keeps a bounded FIFO replay table keyed by
the DI-minted ``turn_ref``; a repeat carrying one returns the recorded
result and does not invoke the operator again. A transport-level retry
issued before the caller has any ``turn_ref`` re-executes the turn;
that is safe here because the tool has no durable consequential
effect. This controller writes NO durable state: both tables are
in-memory and bounded.

Errors: a bounded-input violation is a tool execution result with
``isError: true`` and a conforming ``structuredContent`` refusal so the
model can self-correct; an unknown tool name or non-object arguments
raise ``UnknownToolError`` / ``InvalidParamsError`` for the server to
map to JSON-RPC protocol errors. An operator failure is reported by
status and exception class name only — never by message text, which
could carry anything.

The operator session is consumed through its neutral ``prepare`` /
``execute`` interface by duck typing; this module imports no provider.
"""

import collections
import itertools
import json
import secrets
import threading

from human_interaction import EVENT_MESSAGE, InteractionEvent

from grok_mcp import adapter as adapter_module
from grok_mcp import protocol

# Bounded in-memory tables (FIFO eviction). Exact-value pinned.
MAX_REPLAY_ENTRIES = 64
MAX_SESSION_ENTRIES = 64

SOURCE = protocol.SOURCE

# The first configured connector credential; the only one this spike
# knows. It is an ordinal, not a Grok identity.
DEFAULT_PRINCIPAL_ID = 1


class UnknownToolError(Exception):
    """The tool name is not in the table (JSON-RPC -32602)."""


class InvalidParamsError(Exception):
    """The arguments are not a JSON object (JSON-RPC -32602)."""


class ToolResult(object):
    """One tool execution result: structured payload plus error flag."""

    def __init__(self, structured, is_error):
        self.structured = structured
        self.is_error = is_error

    def to_jsonrpc_result(self):
        result = {
            "content": [{
                "type": "text",
                "text": json.dumps(self.structured, sort_keys=True),
            }],
            "structuredContent": self.structured,
        }
        if self.is_error:
            result["isError"] = True
        return result


def default_mint_ref():
    return protocol.REF_PREFIX + secrets.token_hex(protocol.REF_HEX_CHARS // 2)


class GrokMcpController(object):
    """Dispatch one validated tool call; own every DI-minted reference."""

    def __init__(self, session, repository, principal_id=DEFAULT_PRINCIPAL_ID,
                 mint_ref=None):
        self._session = session
        self._repository = repository
        self._principal_id = principal_id
        self._mint_ref = mint_ref or default_mint_ref
        self._counter = itertools.count(1)
        self._counter_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._sessions = collections.OrderedDict()
        self._replays = collections.OrderedDict()
        self.calls_received = 0

    # -- DI-minted identity -------------------------------------------

    def _mint_id(self):
        with self._counter_lock:
            return next(self._counter)

    @property
    def replay_count(self):
        return len(self._replays)

    @property
    def session_count(self):
        return len(self._sessions)

    def known_session_refs(self):
        return tuple(self._sessions)

    def _bounds(self):
        return {
            "max_turn_text_chars": protocol.MAX_TURN_TEXT_CHARS,
            "max_echo_chars": protocol.MAX_ECHO_CHARS,
            "max_request_bytes": protocol.MAX_REQUEST_BYTES,
            "max_replay_entries": MAX_REPLAY_ENTRIES,
            "max_session_entries": MAX_SESSION_ENTRIES,
            "max_message_chars": adapter_module.MAX_MESSAGE_CHARS,
            "max_message_chunks": adapter_module.MAX_MESSAGE_CHUNKS,
        }

    # -- dispatch -------------------------------------------------------

    def call_tool(self, name, arguments):
        tool = protocol.tool_by_name(name)
        if tool is None:
            raise UnknownToolError("unknown tool")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise InvalidParamsError("arguments must be an object")
        self.calls_received += 1
        problems = protocol.schema_problems(tool["inputSchema"], arguments)
        reason = "; ".join(problems) if problems else None
        if name == protocol.TOOL_STATUS:
            return self._status(reason)
        if name == protocol.TOOL_PING:
            return self._ping(arguments, reason)
        return self._operator_turn(arguments, reason)

    def _finish(self, tool_name, structured, is_error):
        schema = protocol.tool_by_name(tool_name)["outputSchema"]
        problems = protocol.schema_problems(schema, structured, "result")
        if problems:
            # Fail closed: a non-conforming payload never leaves.
            raise RuntimeError("result does not conform: " + "; ".join(problems))
        return ToolResult(structured, is_error)

    def _status(self, reason):
        structured = {
            "ok": reason is None,
            "reason": reason,
            "ready": True,
            "contract_version": protocol.CONTRACT_VERSION,
            "protocol_versions": list(protocol.SUPPORTED_PROTOCOL_VERSIONS),
            "tools": list(protocol.TOOL_NAMES),
            "bounds": self._bounds(),
            "call_ref": self._mint_ref(),
        }
        return self._finish(protocol.TOOL_STATUS, structured, reason is not None)

    def _ping(self, arguments, reason):
        echo = arguments.get("echo") if reason is None else None
        structured = {
            "ok": reason is None,
            "reason": reason,
            "pong": reason is None,
            "echo": echo if isinstance(echo, str) else None,
            "call_ref": self._mint_ref(),
        }
        return self._finish(protocol.TOOL_PING, structured, reason is not None)

    # -- the one bounded turn -------------------------------------------

    def _refusal(self, turn_ref, reason):
        return self._finish(protocol.TOOL_OPERATOR_TURN, {
            "ok": False,
            "reason": reason,
            "status": protocol.STATUS_REFUSED,
            "request_id": None,
            "session_ref": None,
            "turn_ref": turn_ref,
            "message": None,
            "chunks_sent": 0,
            "truncated_chars": 0,
            "replayed": False,
        }, True)

    def _build_adapter(self, event):
        return adapter_module.GrokMcpInteractionAdapter(event, self._mint_id)

    def _operator_turn(self, arguments, reason):
        with self._turn_lock:
            if reason is not None:
                return self._refusal(self._mint_ref(), reason)
            # Every supplied ref is validated against DI's own tables
            # BEFORE any branch acts on it, so a non-DI-minted ref is
            # refused consistently, never silently ignored.
            session_ref = arguments.get("session_ref")
            provider_session_id = None
            if session_ref is not None:
                if session_ref not in self._sessions:
                    return self._refusal(
                        self._mint_ref(),
                        "arguments.session_ref: unknown session_ref",
                    )
                provider_session_id = self._sessions[session_ref]
                # LRU, not FIFO: a ref in active use is never evicted by
                # newer idle ones.
                self._sessions.move_to_end(session_ref)
            supplied_turn_ref = arguments.get("turn_ref")
            if supplied_turn_ref is not None:
                recorded = self._replays.get(supplied_turn_ref)
                if recorded is None:
                    return self._refusal(
                        self._mint_ref(), "arguments.turn_ref: unknown turn_ref"
                    )
                replay = dict(recorded.structured)
                replay["replayed"] = True
                return self._finish(
                    protocol.TOOL_OPERATOR_TURN, replay, recorded.is_error
                )
            turn_ref = self._mint_ref()
            result = self._execute_turn(
                arguments["text"], provider_session_id, session_ref, turn_ref
            )
            self._replays[turn_ref] = result
            while len(self._replays) > MAX_REPLAY_ENTRIES:
                self._replays.popitem(last=False)
            return result

    def _execute_turn(self, text, provider_session_id, session_ref, turn_ref):
        event = InteractionEvent(
            sequence=self._mint_id(),
            allowed=True,
            reason="bearer credential verified at the HTTP layer",
            kind=EVENT_MESSAGE,
            principal_id=self._principal_id,
            conversation_id=self._mint_id(),
            message_id=self._mint_id(),
            action_id=None,
            content=text,
        )
        adapter = self._build_adapter(event)
        received = adapter.receive(None)
        inbound = received.events[0]
        status = protocol.STATUS_OPERATOR_ERROR
        request_id = None
        reason = None
        reply = None
        result_session_id = None
        try:
            prepared = self._session.prepare(
                inbound.content, self._repository,
                session_id=provider_session_id, source=SOURCE,
            )
            request_id = prepared.request_id
            result = self._session.execute(prepared)
        except Exception as exc:  # reported by class name only
            reason = "operator raised %s" % type(exc).__name__
        else:
            status = getattr(result, "status", None)
            if not isinstance(status, str):
                status = protocol.STATUS_OPERATOR_ERROR
            live_id = getattr(result, "request_id", None)
            if isinstance(live_id, str):
                request_id = live_id
            candidate = getattr(result, "session_id", None)
            if isinstance(candidate, str) and candidate:
                result_session_id = candidate
            message = getattr(result, "message", None)
            if isinstance(message, str):
                reply = message
            if status != protocol.STATUS_COMPLETED:
                error = getattr(result, "error", None)
                code = getattr(error, "code", None)
                reason = "operator turn ended with status %s" % status
                if isinstance(code, str):
                    reason += " (%s)" % code
        if reply is None:
            reply = "Operator turn ended with status %s." % status
            if reason is not None:
                reply += " " + reason
        sent = adapter.send(inbound.conversation_id, reply)
        ok = reason is None
        out_session_ref = None
        if ok:
            held = session_ref is not None and session_ref in self._sessions
            if held and (
                result_session_id is None
                or self._sessions[session_ref] == result_session_id
            ):
                # Continuation: the caller's validated ref still names
                # the provider session (the provider reported the same
                # id, or none at all), so it stays the ref to pass back.
                out_session_ref = session_ref
            elif result_session_id is not None:
                out_session_ref = self._mint_ref()
                self._sessions[out_session_ref] = result_session_id
                while len(self._sessions) > MAX_SESSION_ENTRIES:
                    self._sessions.popitem(last=False)
        return self._finish(protocol.TOOL_OPERATOR_TURN, {
            "ok": ok,
            "reason": reason,
            "status": status,
            "request_id": request_id,
            "session_ref": out_session_ref,
            "turn_ref": turn_ref,
            "message": adapter.delivered_text(),
            "chunks_sent": sent.chunks_sent,
            "truncated_chars": sent.truncated_chars,
            "replayed": False,
        }, not ok)
