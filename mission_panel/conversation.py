"""The panel's conversation runner: one restricted provider turn at a time per
conversation, intent recorded BEFORE the provider is called, never retried.

The conversation is ``operator_session.codex.RestrictedCodexOperatorSession``:
the role-turn read-only posture, verified on the exact argv before any spawn, a
FRESH turn every time (it continues no provider session, so a reload, a second
window or a restart loses nothing it depended on: each turn is given the
earlier COMPLETED turns of its conversation as quoted, untrusted context). The
conversation carries no authority and is never the source of truth for a
proposal, a decision or a run.

One turn is: validate the words, prepare the request (no provider call), record
the intent durably (``RESERVED``), run the one provider call in a worker, then
settle the record from what the provider returned. Settling is honest about
what it does not know:

- ``COMPLETED``: a usable reply (its words are untrusted display content);
- ``FAILED``: the provider reported a failure (a nonzero exit, a failure event);
- ``REFUSED``: refused before any provider process existed;
- ``UNCERTAIN``: the outcome is unknown (unreadable output, a call that raised,
  the deadline, or a process that died mid-turn). It is NEVER retried: the
  only claim made is that the INTENT was recorded.
"""

import subprocess
import threading

from codex_gateway import contract as gateway_contract

from mission_panel import draft as draft_module
from mission_panel import journal as journal_module

SOURCE = "di-world-panel"
# The provider turns in flight at once, across every conversation.
MAX_CONCURRENT_TURNS = 2
# A read-only drafting turn that has not answered by now is killed.
TURN_DEADLINE_SECONDS = 300

PROBLEM_PROVIDER_BUSY = "mission_panel_provider_busy"
PROBLEM_NOT_PREPARED = "mission_panel_turn_not_prepared"
DEADLINE_ERROR_CODE = "deadline_exceeded"
RAISED_ERROR_CODE = "provider_call_raised"

_STATE_BY_STATUS = {
    gateway_contract.STATUS_COMPLETED: journal_module.TURN_COMPLETED,
    gateway_contract.STATUS_INVALID_REQUEST: journal_module.TURN_REFUSED,
    gateway_contract.STATUS_CODEX_UNAVAILABLE: journal_module.TURN_REFUSED,
    gateway_contract.STATUS_CODEX_FAILED: journal_module.TURN_FAILED,
    gateway_contract.STATUS_MALFORMED_OUTPUT: journal_module.TURN_UNCERTAIN,
}


def bounded_runner(deadline_seconds=None):
    """The one process spawn of a restricted turn (an argv list, no shell,
    bytes in and out) with a DEADLINE the gateway's own spawn lacks: past it
    the child is killed and reaped and the call raises ``TimeoutExpired``,
    which settles the turn as ``UNCERTAIN``. It spawns the argv the role-turn
    posture already verified, unchanged."""
    deadline = TURN_DEADLINE_SECONDS if deadline_seconds is None else deadline_seconds

    def run(argv, input_bytes, cwd):
        return subprocess.run(argv, input=input_bytes, cwd=cwd,
                              capture_output=True, timeout=deadline)
    return run


def _outcome(status, error_code, detail, unrecognized=0):
    return {
        "status": status, "error_code": error_code,
        "detail": None if detail is None else detail[:journal_module.MAX_DETAIL_CHARS],
        "unrecognized_event_lines": unrecognized,
    }


def settle(result, repository_url):
    """``(state, assistant_text, outcome, draft, draft_problem)`` for what the
    provider returned. A result that is not a gateway result is UNCERTAIN."""
    status = getattr(result, "status", None)
    state = _STATE_BY_STATUS.get(status)
    if state is None or not hasattr(result, "error"):
        return (journal_module.TURN_UNCERTAIN, None,
                _outcome("uncertain", "unrecognized_result",
                         "the provider returned a result this panel does not"
                         " recognise"), None, None)
    error = result.error
    unrecognized = getattr(result, "unrecognized_event_lines", 0)
    if state == journal_module.TURN_COMPLETED and isinstance(result.message, str):
        message = result.message
        text = message[:journal_module.MAX_TURN_TEXT_CHARS]
        detail = None
        if len(message) > len(text):
            detail = ("the reply was truncated to %d characters for display"
                      % journal_module.MAX_TURN_TEXT_CHARS)
        draft, problem = draft_module.extract_draft(message, repository_url)
        return (state, text, _outcome(status, None, detail, unrecognized),
                draft, problem)
    if state == journal_module.TURN_COMPLETED:
        state = journal_module.TURN_UNCERTAIN
    return (state, None,
            _outcome(status, None if error is None else error.code,
                     None if error is None else error.detail, unrecognized),
            None, None)


class InFlight(object):
    """The turn ids this PROCESS is running (a provider turn, or a submission
    being made). The journal settles every other RESERVED record as UNCERTAIN,
    so the conversation and the service share ONE registry: a record in flight
    here is never settled as dead by another window's reservation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ids = set()

    def add(self, turn_id):
        with self._lock:
            self._ids.add(turn_id)

    def discard(self, turn_id):
        with self._lock:
            self._ids.discard(turn_id)

    def snapshot(self):
        with self._lock:
            return set(self._ids)


class PanelConversation(object):

    def __init__(self, journal, session, control_repository, repository_url,
                 clock, registry=None):
        self._journal = journal
        self._session = session
        self._control = control_repository
        self._repository_url = repository_url
        self._clock = clock
        self._condition = threading.Condition()
        self._in_flight = set()
        self._registry = registry if registry is not None else InFlight()

    # -- state ---------------------------------------------------------------

    def in_flight(self):
        with self._condition:
            return set(self._in_flight)

    def idle(self, timeout):
        """Wait up to ``timeout`` seconds for every provider turn to settle."""
        with self._condition:
            return self._condition.wait_for(lambda: not self._in_flight, timeout)

    def start(self):
        """Settle the turns and submissions a previous process left in flight
        as UNCERTAIN. They are never re-run. Returns the turn ids changed."""
        with self._condition:
            return self._journal.reconcile_unresolved(
                self._registry.snapshot() | self._in_flight, self._clock())

    # -- one turn --------------------------------------------------------------

    def send(self, conversation_id, text):
        """Record the intent of one turn and start its provider call. Returns
        the RESERVED turn at once; the settled turn is read from the journal.
        Refuses before any write when the words are bad, the provider is at
        its concurrency bound, or the conversation has a turn in flight."""
        problem = journal_module.text_problem(text)
        if problem is not None:
            raise journal_module.JournalError(problem, journal_module.PROBLEM_BAD_TEXT)
        with self._condition:
            if len(self._in_flight) >= MAX_CONCURRENT_TURNS:
                raise journal_module.JournalError(
                    "%d provider turns are already in flight; try again when one"
                    " finishes" % len(self._in_flight), PROBLEM_PROVIDER_BUSY)
            prior = ([] if conversation_id is None
                     else self._journal.conversation(conversation_id))
            prompt = draft_module.render_turn_prompt(prior, text, self._repository_url)
            try:
                prepared = self._session.prepare(prompt, self._control, source=SOURCE)
            except Exception as exc:
                raise journal_module.JournalError(
                    "the turn could not be prepared (%s)" % exc, PROBLEM_NOT_PREPARED)
            turn = self._journal.reserve_turn(
                conversation_id, text, prepared.request_id, self._clock(),
                self._registry.snapshot() | self._in_flight)
            self._in_flight.add(turn["turn_id"])
            self._registry.add(turn["turn_id"])
        worker = threading.Thread(target=self._run, args=(turn["turn_id"], prepared),
                                  name="mission-panel-turn", daemon=True)
        try:
            worker.start()
        except BaseException:
            self._release(turn["turn_id"])
            raise
        return turn

    def _release(self, turn_id):
        with self._condition:
            self._in_flight.discard(turn_id)
            self._registry.discard(turn_id)
            self._condition.notify_all()

    def _run(self, turn_id, prepared):
        try:
            try:
                result = self._session.execute(prepared)
            except subprocess.TimeoutExpired:
                fields = (journal_module.TURN_UNCERTAIN, None, _outcome(
                    "uncertain", DEADLINE_ERROR_CODE,
                    "the provider did not answer within %d seconds and was"
                    " killed; what it did is unknown, so the turn was not"
                    " retried" % TURN_DEADLINE_SECONDS), None, None)
            except Exception as exc:
                fields = (journal_module.TURN_UNCERTAIN, None, _outcome(
                    "uncertain", RAISED_ERROR_CODE,
                    "the provider call raised %s: %s; its outcome is unknown, so"
                    " the turn was not retried" % (type(exc).__name__, exc)),
                    None, None)
            else:
                fields = settle(result, self._repository_url)
            state, text, outcome, draft, problem = fields
            self._journal.finish_turn(turn_id, state, text, outcome, draft,
                                      problem, self._clock())
        except Exception:
            # The record stays RESERVED; once this turn leaves the in-flight
            # set a later reconcile settles it as UNCERTAIN. Never a retry.
            pass
        finally:
            self._release(turn_id)
