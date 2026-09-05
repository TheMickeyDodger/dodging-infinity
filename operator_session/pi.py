"""The Pi-backed operator session: a second, replaceable implementation.

This module proves the seam is provider-neutral by putting a different
locally installed coding agent (``pi``, package
``@earendil-works/pi-coding-agent``, validated against version 0.85.1)
behind the SAME ``OperatorSession`` prepare/execute boundary the Codex
session uses. It implements only the two hooks; ``prepare`` and
``execute`` (provenance validation, one submit per execute) are the
base class's and are neither overridden nor shadowed here.

Boundary
--------
Imports are the standard library plus ``operator_session.session``. This
module imports NO other package of this repository — in particular not
the Codex gateway, which it substitutes for — so its request, result,
and error types are its own frozen dataclasses below, duck-compatible
with exactly the fields the transport reads. The success status literal
is pinned equal to the Codex vocabulary by a TEST, never by an import.

Constructing ``PiOperatorSession`` performs no I/O of any kind: no
process, no filesystem access, no environment read, no network. Every
external effect happens inside ``_submit``, through ``run_process``.

Invocation (derived from ``pi --help`` and the installed source)
-----------------------------------------------------------------
Always an argv LIST, never a shell; ``cwd`` is the request repository::

    pi --mode json --no-tools --no-extensions --no-skills
       --no-prompt-templates --no-context-files --no-approve
       (--no-session | --session-id <id>) -- <intent text>

- ``--mode json`` is the JSON event stream and is by itself
  non-interactive: ``resolveAppMode`` in ``dist/main.js`` returns the
  json print mode whenever ``parsed.mode === "json"`` regardless of TTY
  state, so no ``--print`` is needed.
- ``--no-tools`` / ``--no-extensions`` / ``--no-skills`` /
  ``--no-prompt-templates`` / ``--no-context-files`` are the documented
  disable switches (``pi --help``; ``dist/cli/args.js``). ``--no-approve``
  ("Ignore project-local files for this run") keeps project-local
  resources out of the run as well.
- ``--`` ends option parsing (``pi --help``: "treat remaining arguments
  as messages/files"), so an intent that begins with ``-`` is a
  message, never an option.
- No ``--provider`` and no ``--model``: the adapter inherits the
  ambient user configuration exactly as the Codex adapter inherits the
  user's Codex configuration. NOTE: ``pi --help`` documents a built-in
  default provider (``google``) that differs from what the local user
  settings select; pinning either would be a policy choice this spike
  deliberately does not make.
- stdin is ``DEVNULL``. ``readPipedStdin`` in ``dist/main.js`` reads a
  non-TTY stdin to EOF and PREPENDS it to the initial message
  (``dist/cli/initial-message.js``), so the child must never inherit
  the caller's stdin.

Session handling (``pi --help``; ``dist/main.js``)
- ``--session-id <id>`` — "Use exact project session ID, creating it if
  missing". ``main.js`` looks the id up by EXACT match among the
  sessions of the current project (cwd-bound) and otherwise creates a
  new session with that id; the id is validated by
  ``assertValidSessionId`` (alphanumeric plus ``.-_``), so it is never
  resolved as a file path. This is the form used when the neutral
  ``session_id`` is present.
- ``--no-session`` — "Don't save session (ephemeral)"; the form used
  when the neutral ``session_id`` is absent.
- ``--session <path|id>`` was NOT chosen: it resolves its argument as a
  file path when it contains a separator or ends in ``.jsonl`` and
  otherwise by PREFIX match, possibly across projects — an
  arbitrary-file and ambiguous-match surface the exact-id flag lacks.
  It is banned by the guard below, as are ``--fork``, ``--continue``,
  and ``--resume``.
- Consequence to state plainly: an ephemeral turn still emits a session
  header carrying an ``id``, and that id is reported truthfully as the
  runtime's identity for the turn, but nothing was saved, so a
  follow-up turn that passes it via ``--session-id`` CREATES a session
  with that id rather than resuming context. Continuity therefore
  begins with the second turn. Providing first-turn persistence would
  be a policy choice outside this spike.
- Cross-provider identifiers: the neutral ``session_id`` is opaque to
  the seam and may have been minted by the OTHER provider on an
  earlier turn (for example when a caller switches the selected
  runtime mid-conversation). pi's ``assertValidSessionId`` accepts any
  alphanumeric/``.-_`` token, so such an id is not rejected: pi finds
  no session of its own with that exact id and CREATES a new, empty
  one under the borrowed identifier — nothing is resumed, and nothing
  of the other provider's state is read. Not unsafe, but a surprise;
  callers that switch providers should expect a fresh context.

Hazard: ``@``-leading messages after ``--``
``pi --help`` documents ``pi [options] [--] [@files...] [messages...]``.
``parseArgs`` in ``dist/cli/args.js`` (the ``arg === "--"`` branch)
pushes EVERY remaining argument that ``startsWith("@")`` into
``fileArgs`` — i.e. after ``--`` an ``@``-leading argument is STILL a
file include, confirmed experimentally by calling the installed
parser on ``["--", "@/etc/hosts", "hello"]`` (result:
``fileArgs: ["/etc/hosts"]``). Operator intent text beginning with
``@`` would therefore read an arbitrary file into the prompt. This
adapter fails closed: such an intent is an invalid-request result and
no process is started. A leading space defeats the check in pi's
parser (``" @x"`` is a message), but the adapter refuses only what pi
would treat as a file.

Process and failure semantics
- A BOUNDED timeout (constructor constant) is applied inside the
  adapter. On expiry the child is sent SIGTERM, given a short grace
  (pi's print mode handles SIGTERM by killing its tracked children and
  exiting 143), then SIGKILL if still alive; the drain of its output
  after SIGKILL is ALSO bounded by the grace, the reap does not read the
  pipes, and the adapter closes its pipe ends, so no step of the
  timeout path waits without a bound (worst case: the timeout plus
  three grace periods). The outcome is a truthful failure naming the
  bound and disclosing whether the child was reaped and whether its
  output was drained; any partial stream is discarded, never parsed
  into an answer. What remains true afterwards: a grandchild that pi
  did not track, or that ignores SIGTERM before the SIGKILL lands, may
  outlive the turn and keep running with the inherited pipe ends; this
  adapter kills only the direct child, and when the pipes did not reach
  EOF within the grace the failure detail says so explicitly.
- ``OSError`` at process start is TWO conditions, told apart by the
  ``filename`` the interpreter attaches: CPython's ``Popen`` reports a
  failed ``chdir(cwd)`` with ``filename == cwd`` (the child never
  reached exec) and a failed exec with ``filename == argv[0]``; chdir
  happens first, so when both are bad the repository is named. A
  repository that cannot be entered is an invalid-request result naming
  the repository; a binary that cannot be executed is the unavailable
  status naming the binary; a ``filename`` matching neither (a future
  interpreter, an unexpected failure) is the unavailable status with a
  detail that names BOTH possible causes and the OS text, asserting
  neither. A test pins the discriminator against the real interpreter
  so it fails loudly rather than reverting to a wrong diagnosis.
- A non-zero exit maps to the failed status with bounded, honestly
  escaped stderr; a stdout that is not UTF-8 maps to the
  malformed-output status and says the stream was not parsed.

Declared compatibility surface (``docs/json.md``; ``dist/modes/print-mode.js``;
``dist/modes/json-event.js``; ``pi-ai`` ``types.d.ts``)
- The first stdout line is the session header
  ``{"type":"session","version":3,"id":"...","timestamp":"...","cwd":"..."}``
  written from ``sessionManager.getHeader()``; the session identity is
  its ``id``.
- The terminal assistant text is the LAST ``message_end`` event whose
  ``message.role`` is ``"assistant"``: its ``message.content`` items of
  ``type: "text"`` joined by newlines. ``message_update`` records are
  delta-only and are never assembled into an answer.
- The assistant ``stopReason`` decides the outcome, against the COMPLETE
  declared vocabulary of ``pi-ai`` (``types.d.ts`` line 287:
  ``"pending" | "stop" | "length" | "toolUse" | "error" | "aborted" |
  "deferred"``): only ``stop`` is a complete answer; ``error`` and
  ``aborted`` are failure events whose ``errorMessage`` is the detail;
  ``length`` (truncated by the output limit), ``pending``, ``toolUse``
  and ``deferred`` are incomplete turns reported as a failure that
  names the stop reason, with their partial text withheld; any value
  outside the vocabulary is malformed output naming the value. This
  matters because in json mode pi exits 0 even on a failure (print-mode
  only sets a non-zero exit code in TEXT mode), so the exit status
  alone cannot be trusted for success.
- Every non-blank line that is not a JSON object whose ``type`` is one
  of ``RECOGNIZED_EVENT_TYPES`` is counted and reported on every path
  that parsed the stream. No recognized terminal message means the
  malformed-output status — never an invented message and never an
  inferred session handle.

Authority: none. This module holds no Mission Authorization, no Git
operation, no delivery, and no lifecycle; the session id it carries is
opaque continuation state for the runtime and nothing else.
"""

import json
import subprocess
import uuid
from dataclasses import dataclass
from typing import Optional

from operator_session.session import OperatorSession

PI_BINARY = "pi"
PI_CONTRACT_VERSION = 1

# Bounded process deadline (seconds), applied inside the adapter; the
# grace between SIGTERM and SIGKILL when it expires.
DEFAULT_TIMEOUT_SECONDS = 600.0
TERMINATE_GRACE_SECONDS = 5.0

# Upper bound on error detail text carried in a result. A hard constant,
# never derived from input.
ERROR_DETAIL_MAX_CHARS = 2000

# Status vocabulary. STATUS_COMPLETED is pinned equal to the transport's
# comparison literal by a test, deliberately not by an import.
STATUS_COMPLETED = "completed"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_PI_UNAVAILABLE = "pi_unavailable"
STATUS_PI_FAILED = "pi_failed"
STATUS_MALFORMED_OUTPUT = "malformed_output"

# Error codes carried in PiError.code.
ERROR_EMPTY_INTENT = "empty_intent"
ERROR_INTENT_NOT_UTF8 = "intent_not_utf8"
ERROR_INTENT_FILE_INCLUDE = "intent_file_include"
ERROR_EMPTY_SESSION_ID = "empty_session_id"
ERROR_FLAG_LIKE_SESSION_ID = "flag_like_session_id"
ERROR_BANNED_FLAG = "banned_flag_element"
ERROR_PI_NOT_FOUND = "pi_not_found"
ERROR_REPOSITORY_UNUSABLE = "repository_unusable"
ERROR_PROCESS_START_FAILED = "pi_start_failed"
ERROR_TIMEOUT = "pi_timeout"
ERROR_PI_EXIT_NONZERO = "pi_exit_nonzero"
ERROR_PI_FAILURE_EVENT = "pi_failure_event"
ERROR_INCOMPLETE_ANSWER = "pi_incomplete_answer"
ERROR_UNRECOGNIZED_STOP_REASON = "unrecognized_stop_reason"
ERROR_OUTPUT_NOT_UTF8 = "pi_output_not_utf8"
ERROR_UNRECOGNIZED_OUTPUT = "unrecognized_output"

# --- Declared compatibility surface (see module docstring) -----------------

EVENT_TYPE_KEY = "type"
SESSION_HEADER_TYPE = "session"
SESSION_HEADER_ID_KEY = "id"
TERMINAL_MESSAGE_EVENT_TYPE = "message_end"
MESSAGE_KEY = "message"
ROLE_KEY = "role"
ASSISTANT_ROLE = "assistant"
CONTENT_KEY = "content"
TEXT_CONTENT_TYPE = "text"
TEXT_KEY = "text"
STOP_REASON_KEY = "stopReason"
ERROR_MESSAGE_KEY = "errorMessage"

# The COMPLETE StopReason vocabulary of the installed pi-ai package
# (node_modules/@earendil-works/pi-ai/dist/types.d.ts line 287:
# "pending" | "stop" | "length" | "toolUse" | "error" | "aborted" |
# "deferred"), partitioned into what each value means for a turn. Only a
# "stop" is a complete answer. "error"/"aborted" are failures carrying an
# errorMessage. "length" is a TRUNCATED answer and "pending", "toolUse",
# "deferred" are turns that did not reach a terminal answer; none of
# them may be reported as completed, and their partial text is withheld.
# A value outside the vocabulary is an undeclared surface and fails
# closed as malformed output.
STOP_REASONS = (
    "pending", "stop", "length", "toolUse", "error", "aborted", "deferred",
)
COMPLETE_STOP_REASONS = ("stop",)
FAILURE_STOP_REASONS = ("error", "aborted")
INCOMPLETE_STOP_REASONS = ("length", "pending", "toolUse", "deferred")

# Every event type the json stream may carry: the header, the AgentEvent
# union (docs/json.md), and the AgentSessionEvent extras enumerated from
# dist/core/agent-session.d.ts of the installed 0.85.1 package.
RECOGNIZED_EVENT_TYPES = (
    SESSION_HEADER_TYPE,
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "agent_settled",
    "auto_retry_start",
    "auto_retry_end",
    "bash_execution_update",
    "compaction_start",
    "compaction_end",
    "entry_appended",
    "queue_update",
    "session_info_changed",
    "summarization_retry_attempt_start",
    "summarization_retry_finished",
    "summarization_retry_scheduled",
    "thinking_level_changed",
)

COMPATIBILITY_SURFACE_NOTE = (
    "declared pi json compatibility surface: session header type %s"
    " (id key %s); terminal message event %s with %s.%s == %s and text"
    " content type %s; stop reasons: complete %s, failure %s, incomplete"
    " %s (declared vocabulary %s)"
    % (
        SESSION_HEADER_TYPE,
        SESSION_HEADER_ID_KEY,
        TERMINAL_MESSAGE_EVENT_TYPE,
        MESSAGE_KEY,
        ROLE_KEY,
        ASSISTANT_ROLE,
        TEXT_CONTENT_TYPE,
        "/".join(COMPLETE_STOP_REASONS),
        "/".join(FAILURE_STOP_REASONS),
        "/".join(INCOMPLETE_STOP_REASONS),
        "/".join(STOP_REASONS),
    )
)

# --- Banned flag guard -----------------------------------------------------

# Flags that re-enable tools, re-enable trust, load extensions / skills /
# templates, inject credentials or a system prompt, or select a session
# by file path or prefix. Never in a constructed argv, with or without
# an =value suffix, anywhere in the list (an intent text that IS one of
# these tokens is refused too — fail closed rather than reason about the
# option/message boundary).
BANNED_FLAGS = (
    "--tools", "-t",
    "--extension", "-e",
    "--skill",
    "--prompt-template",
    "--approve", "-a",
    "--api-key",
    "--system-prompt",
    "--append-system-prompt",
    "--session",
    "--fork",
    "--continue", "-c",
    "--resume", "-r",
)


class BannedFlagError(ValueError):
    """A constructed argv contained a banned pi flag."""


def assert_argv_allowed(argv):
    """Reject any argv containing a banned pi flag; return a copy otherwise."""
    for element in argv:
        text = str(element)
        flag = text.split("=", 1)[0]
        if flag in BANNED_FLAGS:
            raise BannedFlagError(
                "banned pi flag in constructed argv: %s" % text
            )
    return list(argv)


# --- Types -----------------------------------------------------------------


@dataclass(frozen=True)
class PiError:
    """A bounded, machine-readable error carried in a result."""

    code: str
    detail: str
    detail_truncated: bool


@dataclass(frozen=True)
class PiRequest:
    """One unit of operator intent bound for the pi runtime."""

    contract_version: int
    request_id: str
    source: str
    repository: str
    text: str
    session_id: Optional[str] = None


@dataclass(frozen=True)
class PiResult:
    """The outcome of one submitted request.

    ``unrecognized_event_lines`` has no default on purpose: every
    construction site states it (0 on paths where no stream was parsed).
    """

    contract_version: int
    request_id: str
    session_id: Optional[str]
    status: str
    message: Optional[str]
    error: Optional[PiError]
    unrecognized_event_lines: int


def default_request_id_factory():
    """Default request-id source; tests substitute a deterministic one."""
    return uuid.uuid4().hex


def make_error(code, detail):
    """Build a PiError with the detail bound applied honestly."""
    text = "" if detail is None else str(detail)
    truncated = len(text) > ERROR_DETAIL_MAX_CHARS
    if truncated:
        text = text[:ERROR_DETAIL_MAX_CHARS]
    return PiError(code=code, detail=text, detail_truncated=truncated)


# --- Argv construction -----------------------------------------------------


def build_argv(text, session_id=None, binary=PI_BINARY):
    """The exact argv for one turn; the guard runs over the whole list."""
    argv = [
        binary,
        "--mode", "json",
        "--no-tools",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--no-approve",
    ]
    if session_id is None:
        argv.append("--no-session")
    else:
        argv.extend(["--session-id", session_id])
    argv.extend(["--", text])
    return assert_argv_allowed(argv)


# --- Process ---------------------------------------------------------------


class ProcessTimeout(Exception):
    """The child exceeded the bound and was terminated.

    ``reaped`` says whether the direct child was waited for; ``drained``
    says whether its output pipes reached EOF within the grace. Both are
    disclosed in the message so a caller never mistakes a killed turn
    with a pipe-holding grandchild for a clean termination.
    """

    def __init__(self, timeout_seconds, reaped=True, drained=True):
        self.timeout_seconds = timeout_seconds
        self.reaped = reaped
        self.drained = drained
        Exception.__init__(
            self, timeout_message(timeout_seconds, reaped, drained)
        )


def timeout_message(timeout_seconds, reaped, drained):
    """The single source of the timeout wording (tests build it too)."""
    text = (
        "pi did not finish within the configured bound of %s second(s)"
        " and was terminated" % (timeout_seconds,)
    )
    if not drained:
        text += (
            "; its output could not be drained within the %s-second grace"
            " after SIGKILL, so a grandchild holding the output pipes may"
            " still be running" % (TERMINATE_GRACE_SECONDS,)
        )
    if not reaped:
        text += (
            "; the child could not be reaped within the %s-second grace"
            % (TERMINATE_GRACE_SECONDS,)
        )
    return text


def _end_after_timeout(process):
    """SIGTERM, grace, SIGKILL, grace, then a bounded reap and pipe close.

    Returns ``(reaped, drained)``. Every wait here is bounded by
    ``TERMINATE_GRACE_SECONDS``: after SIGKILL the direct child is dead,
    but a grandchild that inherited the stdout/stderr pipes keeps them
    open and an unbounded drain would wait for ITS EOF, defeating the
    deadline. So the drain is bounded, the reap does not touch the pipes,
    and the adapter's ends are closed so nothing waits on a stranger.
    """
    process.terminate()
    try:
        process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        return True, True
    except subprocess.TimeoutExpired:
        pass
    process.kill()
    try:
        process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        return True, True
    except subprocess.TimeoutExpired:
        pass
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
        reaped = True
    except subprocess.TimeoutExpired:
        reaped = False
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    return reaped, False


def run_process(argv, cwd, timeout_seconds):
    """Run argv (never a shell) with stdin closed and a bounded deadline.

    Returns a ``subprocess.CompletedProcess`` with BYTES streams — every
    decode is the caller's, so an undecodable stream maps to an honest
    result instead of a crash. On timeout: ``_end_after_timeout`` (every
    step bounded), then ``ProcessTimeout`` disclosing whether the child
    was reaped and its output drained; the partial output is discarded.
    No path in this function waits without a bound: the worst case is
    the configured timeout plus three grace periods.
    """
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        reaped, drained = _end_after_timeout(process)
        raise ProcessTimeout(timeout_seconds, reaped=reaped, drained=drained)
    return subprocess.CompletedProcess(
        argv, process.returncode, stdout, stderr
    )


def _decode_diagnostic(data):
    """Strict UTF-8 when possible; otherwise escaped, and say so."""
    raw = data or b""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", "backslashreplace"), True


# --- Event parsing ---------------------------------------------------------


def _assistant_message(event):
    """The assistant message dict of a terminal event, or None."""
    if event.get(EVENT_TYPE_KEY) != TERMINAL_MESSAGE_EVENT_TYPE:
        return None
    message = event.get(MESSAGE_KEY)
    if not isinstance(message, dict) or message.get(ROLE_KEY) != ASSISTANT_ROLE:
        return None
    return message


def _text_of(message):
    """The joined text content of an assistant message, or None."""
    parts = []
    content = message.get(CONTENT_KEY)
    if isinstance(content, list):
        for item in content:
            if (
                isinstance(item, dict)
                and item.get(EVENT_TYPE_KEY) == TEXT_CONTENT_TYPE
                and isinstance(item.get(TEXT_KEY), str)
            ):
                parts.append(item[TEXT_KEY])
    text = "\n".join(parts)
    return text if text.strip() else None


def parse_events(stdout_text):
    """Parse the json event stream against the declared surface.

    Returns ``(session_id, final_message, failure, unrecognized)`` where
    ``failure`` is None or a ``(status, error_code, detail)`` triple for
    the LAST assistant message when its stop reason was a failure, an
    incomplete answer, or a value outside the declared vocabulary. Blank
    lines are skipped silently; every other line that is not a JSON
    object of a recognized event type is counted.
    """
    session_id = None
    final_message = None
    failure = None
    unrecognized = 0
    for line in (stdout_text or "").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            unrecognized += 1
            continue
        if not isinstance(event, dict):
            unrecognized += 1
            continue
        event_type = event.get(EVENT_TYPE_KEY)
        if event_type not in RECOGNIZED_EVENT_TYPES:
            unrecognized += 1
            continue
        if event_type == SESSION_HEADER_TYPE and session_id is None:
            handle = event.get(SESSION_HEADER_ID_KEY)
            if isinstance(handle, str) and handle.strip():
                session_id = handle.strip()
        message = _assistant_message(event)
        if message is None:
            continue
        stop_reason = message.get(STOP_REASON_KEY)
        final_message = None
        if stop_reason in COMPLETE_STOP_REASONS:
            failure = None
            final_message = _text_of(message)
        elif stop_reason in FAILURE_STOP_REASONS:
            detail = message.get(ERROR_MESSAGE_KEY)
            if not isinstance(detail, str) or not detail.strip():
                detail = "assistant message ended with stopReason %r and no" \
                    " errorMessage" % (stop_reason,)
            failure = (STATUS_PI_FAILED, ERROR_PI_FAILURE_EVENT, detail)
        elif stop_reason in INCOMPLETE_STOP_REASONS:
            failure = (
                STATUS_PI_FAILED, ERROR_INCOMPLETE_ANSWER,
                "assistant message ended with stopReason %r: the answer is"
                " not a complete terminal answer (%s) and its partial text"
                " is withheld rather than reported as complete"
                % (
                    stop_reason,
                    "truncated by the output limit" if stop_reason == "length"
                    else "the turn did not reach a terminal answer",
                ),
            )
        else:
            failure = (
                STATUS_MALFORMED_OUTPUT, ERROR_UNRECOGNIZED_STOP_REASON,
                "assistant message ended with stopReason %r, which is outside"
                " the declared vocabulary %s; the output is not understood"
                " and its text is withheld" % (stop_reason, "/".join(STOP_REASONS)),
            )
    return session_id, final_message, failure, unrecognized


# --- The session -----------------------------------------------------------


class PiOperatorSession(OperatorSession):
    """Prepare/execute through the locally installed pi runtime."""

    def __init__(self, binary=PI_BINARY, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
        # Constants only; nothing here touches the world.
        self._binary = binary
        self._timeout_seconds = timeout_seconds

    def _build_request(self, text, repository, session_id=None,
                       source="terminal"):
        return PiRequest(
            contract_version=PI_CONTRACT_VERSION,
            request_id=default_request_id_factory(),
            source=source,
            repository=str(repository),
            text=text,
            session_id=session_id,
        )

    def _result(self, request, status, session_id, message, error,
                unrecognized):
        return PiResult(
            contract_version=PI_CONTRACT_VERSION,
            request_id=request.request_id,
            session_id=session_id,
            status=status,
            message=message,
            error=error,
            unrecognized_event_lines=unrecognized,
        )

    def _invalid(self, request, code, detail):
        return self._result(
            request, STATUS_INVALID_REQUEST, None, None,
            make_error(code, detail), 0,
        )

    def _start_failure(self, request, argv, exc):
        """Map an OSError from process start to an honest result.

        See the module docstring: the interpreter names the cwd when
        chdir failed and the executable when exec failed. Anything else
        is reported without asserting a single cause.
        """
        filename = getattr(exc, "filename", None)
        repository = str(request.repository)
        if filename == repository:
            return self._invalid(
                request, ERROR_REPOSITORY_UNUSABLE,
                "the repository %s could not be entered as the working"
                " directory (%s); the process was not started — check the"
                " configured repository path" % (repository, exc),
            )
        if filename == argv[0]:
            return self._result(
                request, STATUS_PI_UNAVAILABLE, None, None,
                make_error(
                    ERROR_PI_NOT_FOUND,
                    "the %s binary could not be executed (%s); install pi"
                    " and ensure it is on PATH" % (self._binary, exc),
                ),
                0,
            )
        return self._result(
            request, STATUS_PI_UNAVAILABLE, None, None,
            make_error(
                ERROR_PROCESS_START_FAILED,
                "the process could not be started and the cause could not"
                " be attributed: either the %s binary could not be executed"
                " or the repository %s could not be entered; the OS said:"
                " %s" % (self._binary, repository, exc),
            ),
            0,
        )

    def _submit(self, request):
        text = request.text
        if not isinstance(text, str) or not text.strip():
            return self._invalid(
                request, ERROR_EMPTY_INTENT, "intent text is empty"
            )
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            return self._invalid(
                request, ERROR_INTENT_NOT_UTF8,
                "intent text is not UTF-8 encodable (%s)" % exc,
            )
        if text.startswith("@"):
            return self._invalid(
                request, ERROR_INTENT_FILE_INCLUDE,
                "intent text begins with '@', which pi treats as a file"
                " include even after '--'; refused so operator text can"
                " never read a file into the prompt",
            )
        session_id = request.session_id
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id.strip():
                return self._invalid(
                    request, ERROR_EMPTY_SESSION_ID,
                    "session id must be a non-blank string when given",
                )
            if session_id.startswith("-"):
                return self._invalid(
                    request, ERROR_FLAG_LIKE_SESSION_ID,
                    "session id must not begin with '-'",
                )
        try:
            argv = build_argv(text, session_id=session_id, binary=self._binary)
        except BannedFlagError as exc:
            return self._invalid(request, ERROR_BANNED_FLAG, str(exc))
        try:
            completed = run_process(argv, request.repository, self._timeout_seconds)
        except ProcessTimeout as exc:
            return self._result(
                request, STATUS_PI_FAILED, None, None,
                make_error(ERROR_TIMEOUT, str(exc)), 0,
            )
        except OSError as exc:
            return self._start_failure(request, argv, exc)
        try:
            stdout_text = (completed.stdout or b"").decode("utf-8")
        except UnicodeDecodeError as exc:
            return self._result(
                request, STATUS_MALFORMED_OUTPUT, None, None,
                make_error(
                    ERROR_OUTPUT_NOT_UTF8,
                    "pi stdout is not valid UTF-8 (%s); the stream was not"
                    " parsed — validate the installed pi against the"
                    " declared compatibility surface" % exc,
                ),
                0,
            )
        session_id, message, failure, unrecognized = parse_events(
            stdout_text
        )
        if completed.returncode != 0:
            stderr_text, escaped = _decode_diagnostic(completed.stderr)
            detail = "pi exited with status %d; stderr%s: %s" % (
                completed.returncode,
                " (contained non-UTF-8 bytes, shown escaped)" if escaped else "",
                stderr_text.strip() or "(empty stderr)",
            )
            return self._result(
                request, STATUS_PI_FAILED, session_id, None,
                make_error(ERROR_PI_EXIT_NONZERO, detail), unrecognized,
            )
        if failure is not None:
            status, code, detail = failure
            return self._result(
                request, status, session_id, None,
                make_error(code, detail), unrecognized,
            )
        if message is None:
            return self._result(
                request, STATUS_MALFORMED_OUTPUT, session_id, None,
                make_error(
                    ERROR_UNRECOGNIZED_OUTPUT,
                    "no recognized terminal assistant message in pi output"
                    " (%d unrecognized line(s)); %s"
                    % (unrecognized, COMPATIBILITY_SURFACE_NOTE),
                ),
                unrecognized,
            )
        return self._result(
            request, STATUS_COMPLETED, session_id, message, None, unrecognized
        )
