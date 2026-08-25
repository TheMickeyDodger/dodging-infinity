"""Codex CLI adapter: argv construction, subprocess invocation, JSONL parsing.

The locally installed ``codex`` CLI is the gateway's ONLY downstream
transport, and it is treated as an opaque external process boundary.

Declared compatibility surface
------------------------------
``codex exec --help`` documents ``--json`` as "Print events to stdout as
JSONL" but does not document the concrete event schema, and this module
deliberately does not reverse-engineer it from local session data. The
constants below (``SESSION_ID_KEYS``, ``FINAL_MESSAGE_KEYS``,
``EVENT_WRAPPER_KEYS``, ``EVENT_TYPE_KEY``, ``FAILURE_EVENT_TYPES``) are
the complete set of event shapes the gateway recognizes — a declared
compatibility surface that a human must validate against the installed
Codex CLI version. Parsing fails closed: if no recognized terminal
message is found, the result is ``malformed_output`` with an error naming
this surface; a session handle is never invented or inferred.

Process rules
-------------
Invocation is always an argv list (never a shell), the intent text always
travels on stdin (the ``-`` prompt argument), the working directory for
both new and resumed sessions comes from the subprocess ``cwd``
(``codex exec resume`` exposes no ``-C``/``--cd`` option), and the call
waits for Codex for as long as Codex runs — the gateway imposes no
deadline and propagates failure transparently. The ambient environment
(and therefore the user's own Codex configuration and credentials) is
inherited untouched; the gateway implements no authentication and reads
no credential or configuration file itself.

A guard over every constructed argv rejects sandbox-weakening or
config-bypassing Codex flags outright; see ``assert_argv_allowed``.
"""

import json
import subprocess

from codex_gateway.contract import (
    ERROR_CODEX_EXIT_NONZERO,
    ERROR_CODEX_FAILURE_EVENT,
    ERROR_CODEX_NOT_FOUND,
    ERROR_OUTPUT_NOT_UTF8,
    ERROR_UNRECOGNIZED_OUTPUT,
    STATUS_CODEX_FAILED,
    STATUS_CODEX_UNAVAILABLE,
    STATUS_COMPLETED,
    STATUS_MALFORMED_OUTPUT,
    make_error,
)

CODEX_BINARY = "codex"

# --- Declared compatibility surface (see module docstring) -----------------

# A session handle is read from the first event carrying one of these keys,
# either at the top level or one level down under a wrapper key.
SESSION_ID_KEYS = ("session_id", "thread_id", "conversation_id")

# The final Operator message is read from the LAST event carrying one of
# these keys (key order is the priority within a single event).
FINAL_MESSAGE_KEYS = ("last_agent_message", "agent_message", "message", "text")

# Wrapper keys under which the above keys may sit one level down.
EVENT_WRAPPER_KEYS = ("msg", "item")

# An event whose type (top-level or wrapped) matches one of these values is
# a failure event and maps the turn to codex_failed.
EVENT_TYPE_KEY = "type"
FAILURE_EVENT_TYPES = ("error", "failed", "turn_failed", "task_failed", "stream_error")

COMPATIBILITY_SURFACE_NOTE = (
    "declared Codex JSONL compatibility surface: session keys %s;"
    " final message keys %s; wrapper keys %s; failure event types %s"
    % (
        "/".join(SESSION_ID_KEYS),
        "/".join(FINAL_MESSAGE_KEYS),
        "/".join(EVENT_WRAPPER_KEYS),
        "/".join(FAILURE_EVENT_TYPES),
    )
)

# --- Banned flag guard -----------------------------------------------------

# These flags weaken the Codex sandbox, bypass its git/config/rules checks,
# or change session persistence. They must never appear in a constructed
# argv, with or without an =value suffix.
BANNED_FLAG_PREFIX = "--dangerously-"
BANNED_FLAGS = (
    "--skip-git-repo-check",
    "--ignore-rules",
    "--ignore-user-config",
    "--sandbox",
    "-s",
    "--add-dir",
    "--ephemeral",
)


class BannedFlagError(ValueError):
    """A constructed argv contained a banned Codex flag."""


def assert_argv_allowed(argv):
    """Reject any argv containing a banned Codex flag; return it otherwise."""
    for element in argv:
        text = str(element)
        flag = text.split("=", 1)[0]
        if (
            flag.startswith(BANNED_FLAG_PREFIX)
            or flag in BANNED_FLAGS
            or (text.startswith("-s") and not text.startswith("--"))
        ):
            raise BannedFlagError("banned Codex flag in constructed argv: %s" % text)
    return list(argv)


# --- Argv construction -----------------------------------------------------


def build_new_session_argv(repository):
    """Argv for a new Codex session; intent arrives on stdin via ``-``."""
    return assert_argv_allowed(
        [CODEX_BINARY, "exec", "--json", "-C", str(repository), "-"]
    )


def build_resume_argv(session_id):
    """Argv for resuming a session.

    ``codex exec resume --help`` exposes no ``-C``/``--cd`` option, so the
    working directory comes solely from the subprocess ``cwd``.
    """
    return assert_argv_allowed(
        [CODEX_BINARY, "exec", "resume", "--json", str(session_id), "-"]
    )


# --- Invocation ------------------------------------------------------------


def invoke_codex(argv, intent_text, repository):
    """Run codex with the given argv; the intent travels on stdin.

    Always an argv list, never a shell; the exact keyword surface here is
    deliberate and pinned by tests (no shell, no deadline behavior, nothing
    beyond input/cwd/capture_output). The intent is encoded to UTF-8
    strictly here, and the child's streams are captured as BYTES and
    decoded explicitly by the caller — every decode on this boundary is
    this module's to control, never subprocess's, so an undecodable
    stream can be mapped to an honest result instead of a crash.
    """
    assert_argv_allowed(argv)
    return subprocess.run(
        argv,
        input=intent_text.encode("utf-8"),
        cwd=str(repository),
        capture_output=True,
    )


def _decode_diagnostic(data):
    """Decode a diagnostic-only byte stream for quoting in error detail.

    Returns ``(text, escaped)``: strict UTF-8 when possible; otherwise
    the bytes are shown backslash-escaped and ``escaped`` is True so the
    caller can disclose that escaping happened — never a silent lossy
    decode presented as fact.
    """
    raw = data or b""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", "backslashreplace"), True


# --- Event parsing ---------------------------------------------------------


def _views(event):
    """The event dict plus any recognized wrapper sub-dicts."""
    views = [event]
    for wrapper in EVENT_WRAPPER_KEYS:
        inner = event.get(wrapper)
        if isinstance(inner, dict):
            views.append(inner)
    return views


def _first_string(views, keys):
    """First non-empty string under the given keys; key order wins."""
    for key in keys:
        for view in views:
            value = view.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def parse_events(stdout_text):
    """Parse JSONL events against the declared compatibility surface.

    Returns ``(session_id, final_message, failure_detail, unrecognized)``.
    Blank lines only are skipped silently; every non-JSON line and every
    event matching no declared shape is counted so an unrecognized stream
    can never be silently ignored into a false success.
    """
    session_id = None
    final_message = None
    failure_detail = None
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
        views = _views(event)
        recognized = False
        handle = _first_string(views, SESSION_ID_KEYS)
        if handle is not None:
            recognized = True
            if session_id is None:
                session_id = handle.strip()
        is_failure = False
        for view in views:
            event_type = view.get(EVENT_TYPE_KEY)
            if isinstance(event_type, str) and event_type in FAILURE_EVENT_TYPES:
                is_failure = True
        if is_failure:
            recognized = True
            failure_detail = _first_string(views, FINAL_MESSAGE_KEYS)
            if failure_detail is None:
                failure_detail = "failure event without message text: %s" % json.dumps(
                    event, sort_keys=True
                )
        else:
            message = _first_string(views, FINAL_MESSAGE_KEYS)
            if message is not None:
                recognized = True
                final_message = message
        if not recognized:
            unrecognized += 1
    return session_id, final_message, failure_detail, unrecognized


# --- Turn orchestration ----------------------------------------------------


def run_codex_turn(request):
    """Invoke codex for a validated request.

    Returns ``(status, session_id, message, error, unrecognized)``, where
    ``unrecognized`` is the count of non-blank output lines matching no
    declared event shape — reported on EVERY path that parsed a stream so
    a partially-unparsed stream is never presented as fully understood.
    The request's repository must already be validated and resolved.
    """
    if request.session_id:
        argv = build_resume_argv(request.session_id)
    else:
        argv = build_new_session_argv(request.repository)
    try:
        completed = invoke_codex(argv, request.text, request.repository)
    except OSError as exc:
        return (
            STATUS_CODEX_UNAVAILABLE,
            None,
            None,
            make_error(
                ERROR_CODEX_NOT_FOUND,
                "the %s binary could not be executed (%s); install the Codex"
                " CLI and ensure it is on PATH" % (CODEX_BINARY, exc),
            ),
            0,
        )
    try:
        stdout_text = (completed.stdout or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        # The stream was not parsed at all; unrecognized_event_lines is 0
        # because zero lines were decoded — the distinct error code, not
        # the count, carries the total failure.
        return (
            STATUS_MALFORMED_OUTPUT,
            None,
            None,
            make_error(
                ERROR_OUTPUT_NOT_UTF8,
                "codex stdout is not valid UTF-8 (%s); the stream was not"
                " parsed — validate the installed Codex CLI against the"
                " declared compatibility surface" % exc,
            ),
            0,
        )
    session_id, message, failure_detail, unrecognized = parse_events(stdout_text)
    if completed.returncode != 0:
        stderr_text, stderr_escaped = _decode_diagnostic(completed.stderr)
        # The escaping disclosure precedes the quoted stream so the
        # detail bound can never truncate it away.
        detail = "codex exited with status %d; stderr%s: %s" % (
            completed.returncode,
            " (contained non-UTF-8 bytes, shown escaped)" if stderr_escaped else "",
            stderr_text.strip() or "(empty stderr)",
        )
        return (
            STATUS_CODEX_FAILED,
            session_id,
            None,
            make_error(ERROR_CODEX_EXIT_NONZERO, detail),
            unrecognized,
        )
    if failure_detail is not None:
        return (
            STATUS_CODEX_FAILED,
            session_id,
            None,
            make_error(ERROR_CODEX_FAILURE_EVENT, failure_detail),
            unrecognized,
        )
    if message is None:
        return (
            STATUS_MALFORMED_OUTPUT,
            session_id,
            None,
            make_error(
                ERROR_UNRECOGNIZED_OUTPUT,
                "no recognized terminal message in codex output"
                " (%d unrecognized line(s)); %s"
                % (unrecognized, COMPATIBILITY_SURFACE_NOTE),
            ),
            unrecognized,
        )
    return STATUS_COMPLETED, session_id, message, None, unrecognized
