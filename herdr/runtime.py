"""Low-level Herdr runtime operations.

These functions talk to the underlying `herdr` binary. They contain no
argparse or herdctl-specific behavior and may be used by the control plane
directly.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def run(cmd, cwd=None, check=False):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=check,
    )


def jrun(cmd):
    p = run(cmd)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return json.loads(p.stdout)


def split(pane, direction):
    d = jrun([
        "herdr",
        "pane",
        "split",
        pane,
        "--direction",
        direction,
        "--no-focus",
    ])
    return d["result"]["pane"]["pane_id"]


# -- failed agent-start diagnostic (observability only) -------------------
#
# When ``start_agent`` is given a ``diagnostic_dir`` and the start fails
# terminally, a bounded, redacted snapshot of the failed pane and its
# processes is written there BEFORE the error is raised, so it survives
# the caller's workspace cleanup. Every diagnostic command has its own
# short deadline and a byte cap at READ time; every stored field and the
# whole file are capped; at most DIAGNOSTIC_MAX_FILES files are kept.
# Redaction is BEST-EFFORT pattern matching, not a guarantee. No
# environment is ever stored. Any capture failure is swallowed: the
# original start failure is raised unchanged in type.

DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS = 5.0
DIAGNOSTIC_READ_MAX_BYTES = 65536
DIAGNOSTIC_READ_CHUNK_BYTES = 4096
DIAGNOSTIC_PANE_LINES = 80
DIAGNOSTIC_FIELD_MAX_CHARS = 8000
DIAGNOSTIC_PROCESS_FIELD_MAX_CHARS = 1000
DIAGNOSTIC_MAX_PROCESSES = 32
DIAGNOSTIC_MAX_ARGV = 64
DIAGNOSTIC_FILE_MAX_CHARS = 131072
DIAGNOSTIC_MAX_FILES = 20
DIAGNOSTIC_FILE_PREFIX = "agent-start-failure-"
DIAGNOSTIC_SCHEMA = "herdr_agent_start_failure_diagnostic_v1"
REDACTED = "<REDACTED>"
TRUNCATED = "[TRUNCATED]"

# Redaction is ONE linear pass over whitespace-separated words, on input
# already capped (DIAGNOSTIC_READ_MAX_BYTES characters): every check is a
# substring test, a prefix test or a scan of simple maximal character runs,
# so no pattern can backtrack. A word is redacted when:
# - the previous word was a secret flag (``--password``), a secret key
#   ending in ``:``/``=`` (``api_key:``, ``"api_key":``) or a scheme word
#   (``Bearer``, ``Basic``);
# - it holds ``key=value`` or ``key:value`` pairs with a secret-looking
#   key (``password=x``, ``"api_key":"x"``, ``--token=x``), anywhere in the
#   word, including after other pairs (``{"name":"w","api_key":"x"}``):
#   each such value is replaced; a secret key whose separator stands alone
#   (``"api_key" : "x"``) hides the value that follows;
# - it carries a token shape (``lc-``, ``sk-``, ``ghp_``/``gho_``/...,
#   ``github_pat_``, ``xox?-``, ``AKIA``) or a long opaque run (32+ hex,
#   or 40+ mixed letters and digits): the whole word is replaced.
_SECRET_KEY_WORDS = ("secret", "token", "password", "passwd", "api_key",
                     "api-key", "apikey", "credential", "auth")
_TOKEN_PREFIXES = ("lc-", "sk-", "github_pat_", "ghp_", "gho_", "ghu_",
                   "ghs_", "ghr_", "xoxa-", "xoxb-", "xoxo-", "xoxp-",
                   "xoxr-", "xoxs-")
_TOKEN_PREFIX_MIN_TAIL = 8
_SCHEME_WORDS = ("bearer", "basic")
_KEY_STRIP = "-\"'`{}[](),;"
_WORD = re.compile(r"\S+")
_PAIR_DELIMITERS = ",;&{}[]()"
_PAIR_SPLIT = re.compile(r"([,;&{}\[\]()])")
_RUN = re.compile(r"[A-Za-z0-9+_-]+")
_HEX = frozenset("0123456789abcdefABCDEF")


def _is_secret_key(key):
    key = key.strip(_KEY_STRIP).lower()
    return bool(key) and any(word in key for word in _SECRET_KEY_WORDS)


def _looks_like_token(word):
    for match in _RUN.finditer(word):
        run = match.group()
        for prefix in _TOKEN_PREFIXES:
            if run.startswith(prefix) and (
                len(run) >= len(prefix) + _TOKEN_PREFIX_MIN_TAIL
            ):
                return True
        if run.startswith("AKIA") and len(run) >= 20:
            return True
        if len(run) >= 32 and all(char in _HEX for char in run):
            return True
        if len(run) >= 40 and any(char.isdigit() for char in run) and any(
            char.isalpha() for char in run
        ):
            return True
    return False


def _first_separator(piece):
    positions = [index for index in (piece.find("="), piece.find(":"))
                 if index >= 0]
    return min(positions) if positions else -1


def _redact_pairs(word, state):
    """Every ``key<sep>value`` pair inside one word, not just the first:
    the word is split (linearly) at JSON/argument delimiters, and each
    piece whose key looks secret has its value replaced. A quoted value
    that runs past a delimiter is dropped up to its closing quote. Sets
    ``hide_next`` for a trailing ``key:`` / ``key=`` with no value (or a
    quoted value still open at the end of the word) and ``key_pending``
    for a trailing bare secret key, whose separator may stand alone as
    the next word (``"api_key" : "x"``)."""
    out = []
    closing = None
    unquoted = False
    last = ""
    for piece in _PAIR_SPLIT.split(word):
        if closing is not None:
            index = piece.find(closing)
            if index >= 0:
                out.append(piece[index:])
                closing = None
            continue
        if not piece:
            continue
        last = piece
        if len(piece) == 1 and piece in _PAIR_DELIMITERS:
            out.append(piece)
            continue
        index = _first_separator(piece)
        if unquoted and index < 0:
            # An unquoted secret value may itself hold delimiters
            # (`--token=a,b`): keep hiding until the next `key<sep>`.
            out.append(REDACTED)
            continue
        unquoted = False
        if index > 0 and _is_secret_key(piece[:index]):
            value = piece[index + 1:]
            if value:
                out.append(piece[:index + 1] + REDACTED)
                if value[0] in "\"'":
                    if value.find(value[0], 1) < 0:
                        closing = value[0]
                else:
                    unquoted = True
                continue
        out.append(piece)
    if closing is not None:
        state["hide_next"] = True
    elif last and last[-1] in ":=" and _is_secret_key(last[:-1]):
        state["hide_next"] = True
    elif last and _first_separator(last) < 0 and _is_secret_key(last):
        state["key_pending"] = True
    return "".join(out)


def _redact_word(word, state):
    hide = state["hide_next"]
    pending = state.get("key_pending", False)
    state["hide_next"] = False
    state["key_pending"] = False
    if pending and word[:1] in (":", "="):
        # The separator of a secret pair stood alone: `key : value`.
        if len(word) == 1:
            state["hide_next"] = True
            return word
        return word[0] + REDACTED
    bare = word.strip(_KEY_STRIP).lower()
    if bare in _SCHEME_WORDS:
        state["hide_next"] = True
    elif word.startswith("-") and "=" not in word and _is_secret_key(word):
        state["hide_next"] = True
    if hide:
        return REDACTED
    redacted = _redact_pairs(word, state)
    if _looks_like_token(redacted):
        return REDACTED
    return redacted


def redact_text(value, state=None):
    """Best-effort pattern matching, linear in the input: replace
    credential- and capability-shaped words. ``state`` carries a pending
    "hide the next word" across separate strings (argv items)."""
    text = value if isinstance(value, str) else str(value)
    state = {"hide_next": False} if state is None else state
    out = []
    last = 0
    for match in _WORD.finditer(text):
        out.append(text[last:match.start()])
        out.append(_redact_word(match.group(), state))
        last = match.end()
    out.append(text[last:])
    return "".join(out)


def _drop_fragment(text):
    """Drop the trailing fragment a cut may have split mid-token, so a
    partial secret cannot slip under the redaction checks."""
    cut = max(text.rfind(" "), text.rfind("\n"), text.rfind("\t"))
    return text[:cut] if cut >= 0 else ""


def _cap(text, limit=DIAGNOSTIC_FIELD_MAX_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit] + " " + TRUNCATED


def _precap(text):
    """Bound the input BEFORE redaction, so redaction time is bounded."""
    if len(text) <= DIAGNOSTIC_READ_MAX_BYTES:
        return text
    return _drop_fragment(text[:DIAGNOSTIC_READ_MAX_BYTES]) + " " + TRUNCATED


def _clean(value, limit=DIAGNOSTIC_FIELD_MAX_CHARS, state=None):
    """Pre-cap, redact, then cap to the field limit, so the final cut
    never splits a secret past the redaction checks."""
    text = value if isinstance(value, str) else str(value)
    return _cap(redact_text(_precap(text), state), limit)


def _redact_argv(argv):
    state = {"hide_next": False}
    return [_clean(item, DIAGNOSTIC_PROCESS_FIELD_MAX_CHARS, state)
            for item in argv[:DIAGNOSTIC_MAX_ARGV]]


def _decode_bounded(data, truncated):
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text = _drop_fragment(text) + " " + TRUNCATED
    return text


def _bounded_command(cmd):
    """Run one diagnostic command with its OWN deadline, reading at most
    DIAGNOSTIC_READ_MAX_BYTES from each of stdout and stderr: a runaway
    command is cut at read time, never buffered whole. The command runs in
    its own process group (it is the leader, so the group id is its pid).

    Cleanup covers EVERYTHING after the process is created. However the
    read ends (EOF, timeout, cap or an error), the owned group is killed
    BEFORE the leader is reaped: until it is reaped the leader, even as a
    zombie, keeps the group id pinned, so the kill cannot reach an
    unrelated group, and it still reaches a descendant that outlived the
    leader while holding the pipe. Nothing polls or waits on the leader
    before that kill; ESRCH means the group is already gone. Returns plain
    data; never raises for the command's sake."""
    started = time.monotonic()
    deadline = started + DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return {"started": False, "error": type(exc).__name__}
    data = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    timed_out = False
    error = None
    selector = None
    try:
        streams = {proc.stdout: "stdout", proc.stderr: "stderr"}
        selector = selectors.DefaultSelector()
        for stream in streams:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            capped = False
            for key, _ in selector.select(remaining):
                label = streams[key.fileobj]
                chunk = os.read(key.fileobj.fileno(),
                                DIAGNOSTIC_READ_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                room = DIAGNOSTIC_READ_MAX_BYTES - len(data[label])
                data[label] += chunk[:room]
                if len(chunk) > room or len(data[label]) >= (
                    DIAGNOSTIC_READ_MAX_BYTES
                ):
                    truncated[label] = True
                    capped = True
            if capped:
                break
    except Exception as exc:
        error = type(exc).__name__
    finally:
        try:
            if selector is not None:
                selector.close()
        finally:
            # The leader is still UNREAPED here (nothing above polls or
            # waits on it), so its pid is still this group's id.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass                            # ESRCH: already gone
            except PermissionError:
                # macOS answers EPERM when the group's only member is the
                # unreaped zombie leader: nothing killable remains. The
                # group id is still ours (the leader is unreaped).
                pass
            try:
                proc.wait(timeout=1.0)          # reap only after the kill
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
    result = {
        "started": True,
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout": _decode_bounded(bytes(data["stdout"]), truncated["stdout"]),
        "stderr": _decode_bounded(bytes(data["stderr"]), truncated["stderr"]),
        "stdout_truncated_at_read": truncated["stdout"],
        "stderr_truncated_at_read": truncated["stderr"],
    }
    if error is not None:
        result["error"] = error
    return result


def _command_record(result, keep_stdout=True):
    record = {key: result[key] for key in (
        "started", "returncode", "timed_out", "elapsed_seconds",
        "stdout_truncated_at_read", "stderr_truncated_at_read", "error",
    ) if key in result}
    if "stderr" in result:
        record["stderr"] = _clean(result["stderr"])
    if keep_stdout and "stdout" in result:
        record["stdout"] = _clean(result["stdout"])
    return record


def _processes(stdout):
    """ONLY pid, name, argv0, cwd and redacted argv/cmdline per process,
    found in the process-info JSON. Nothing else is kept, whatever the
    output carries; unparseable output is withheld, not stored raw."""
    try:
        document = json.loads(stdout)
    except ValueError:
        return None
    found = []

    def walk(node):
        if len(found) >= DIAGNOSTIC_MAX_PROCESSES:
            return
        if isinstance(node, dict):
            if isinstance(node.get("pid"), int):
                item = {"pid": node["pid"]}
                for key in ("name", "argv0", "cwd"):
                    if isinstance(node.get(key), str):
                        item[key] = _clean(
                            node[key], DIAGNOSTIC_PROCESS_FIELD_MAX_CHARS)
                if isinstance(node.get("argv"), list):
                    item["argv"] = _redact_argv(node["argv"])
                if isinstance(node.get("cmdline"), str):
                    item["cmdline"] = _clean(
                        node["cmdline"], DIAGNOSTIC_PROCESS_FIELD_MAX_CHARS)
                found.append(item)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(document)
    return found


def _fit(record):
    """Keep the serialized file under DIAGNOSTIC_FILE_MAX_CHARS: drop
    processes from the end, then halve the longest text field, marking
    every cut."""
    text = json.dumps(record, indent=1, sort_keys=True)
    while len(text) > DIAGNOSTIC_FILE_MAX_CHARS:
        processes = record.get("processes")
        if processes:
            processes.pop()
            record["processes_truncated"] = True
        else:
            fields = [(len(section[key]), section, key)
                      for section in record.values() if isinstance(section, dict)
                      for key in ("stdout", "stderr")
                      if isinstance(section.get(key), str)]
            if not fields:
                break
            length, section, key = max(fields, key=lambda item: item[0])
            section[key] = section[key][:length // 2] + " " + TRUNCATED
        text = json.dumps(record, indent=1, sort_keys=True)
    return text


def _retained(directory):
    found = []
    for path in directory.glob(DIAGNOSTIC_FILE_PREFIX + "*.json"):
        try:
            found.append((path.stat().st_mtime, path.name, path))
        except FileNotFoundError:
            continue
    return [path for _, _, path in sorted(found)]


def _make_room(directory):
    """Prune the oldest diagnostics BEFORE publishing, leaving room for
    exactly one more under DIAGNOSTIC_MAX_FILES. If deletion cannot get
    there, refuse: the caller then publishes nothing (and the original
    start error is still raised), so retention never exceeds its cap."""
    retained = _retained(directory)
    for old in retained[:max(0, len(retained) - (DIAGNOSTIC_MAX_FILES - 1))]:
        try:
            old.unlink()
        except OSError:
            pass
    if len(_retained(directory)) > DIAGNOSTIC_MAX_FILES - 1:
        raise OSError("diagnostic retention cannot make room; not publishing")


def _write_private(directory, name, text):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise OSError("diagnostic directory is not a real directory")
    os.chmod(directory, 0o700)
    _make_room(directory)
    final = directory / name
    temporary = directory / (".tmp-" + name)
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(final))
    except BaseException:
        try:
            os.unlink(str(temporary))
        except OSError:
            pass
        raise
    return final


def capture_start_failure(diagnostic_dir, name, pane, role_cfg, timeout,
                          attempt, result, busy):
    """Write one bounded, redacted diagnostic of a failed agent start and
    return its path."""
    now = datetime.now(timezone.utc)
    info = _bounded_command(["herdr", "pane", "process-info", "--pane", pane])
    reads = {}
    for source in ("detection", "visible"):
        reads[source] = _bounded_command([
            "herdr", "pane", "read", pane, "--source", source,
            "--lines", str(DIAGNOSTIC_PANE_LINES)])
    processes = _processes(info.get("stdout", "")) if info.get(
        "started") else None
    record = {
        "schema": DIAGNOSTIC_SCHEMA,
        "redaction": "best-effort pattern matching; not a guarantee",
        "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "agent": _clean(name, 200),
        "kind": _clean(str(role_cfg.get("kind")), 200),
        "pane": _clean(pane, 200),
        "start_timeout_ms": timeout,
        "attempts": attempt,
        "busy_terminal_failure": bool(busy),
        "agent_start": {
            "returncode": result.returncode,
            "stderr": _clean(result.stderr or ""),
            "stdout": _clean(result.stdout or ""),
        },
        "process_info": _command_record(info, keep_stdout=False),
        "pane_read_detection": _command_record(reads["detection"]),
        "pane_read_visible": _command_record(reads["visible"]),
        "processes": processes or [],
        "processes_parsed": processes is not None,
    }
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))[:64]
    filename = "%s%s-%s-%s.json" % (
        DIAGNOSTIC_FILE_PREFIX, safe_name, now.strftime("%Y%m%dT%H%M%SZ"),
        secrets.token_hex(4))
    return _write_private(diagnostic_dir, filename, _fit(record))


def start_agent(
    name,
    pane,
    role_cfg,
    timeout,
    shell_ready_timeout_ms=30000,
    diagnostic_dir=None,
):
    """Start an agent after its pane reaches an available shell.

    With ``diagnostic_dir`` set, a terminal failure first writes a
    bounded, redacted diagnostic there (see ``capture_start_failure``)
    and the error names that file instead of carrying pane text. With
    ``None`` (the default) behaviour is unchanged."""
    cmd = [
        "herdr",
        "agent",
        "start",
        name,
        "--kind",
        role_cfg["kind"],
        "--pane",
        pane,
        "--timeout",
        str(timeout),
    ]

    if role_cfg.get("args"):
        cmd += ["--"] + role_cfg["args"]

    deadline = (
        time.monotonic()
        + max(1.0, shell_ready_timeout_ms / 1000.0)
    )
    attempt = 0

    while True:
        attempt += 1
        p = run(cmd)

        if p.returncode == 0:
            return

        blob = f"{p.stderr}\n{p.stdout}"

        if (
            "agent_pane_busy" in blob
            and time.monotonic() < deadline
        ):
            if attempt == 1:
                print(
                    f"Waiting for pane {pane} to reach an "
                    f"interactive shell before starting {name}..."
                )

            time.sleep(0.5)
            continue

        if diagnostic_dir is not None:
            # Terminal failure, pane still present: capture BEFORE the
            # raise (the caller's cleanup closes the workspace after it).
            # The legacy unbounded busy capture below is NOT used here.
            pointer = ""
            try:
                path = capture_start_failure(
                    diagnostic_dir, name, pane, role_cfg, timeout,
                    attempt, p, "agent_pane_busy" in blob)
                pointer = "\nStart-failure diagnostic: %s" % _cap(
                    str(path), 1024)
            except Exception:
                pointer = ""

            raise RuntimeError(
                f"start {name} failed after {attempt} attempt(s):\n"
                f"{p.stderr}\n"
                f"{p.stdout}"
                f"{pointer}"
            )

        diagnostic = ""

        if "agent_pane_busy" in blob:
            info = run([
                "herdr",
                "pane",
                "process-info",
                pane,
            ])
            recent = run([
                "herdr",
                "pane",
                "read",
                pane,
                "--source",
                "recent-unwrapped",
                "--lines",
                "40",
            ])
            diagnostic = (
                f"\nPane diagnostics ({pane}):\n"
                f"process-info:\n"
                f"{info.stdout}{info.stderr}\n"
                f"recent output:\n"
                f"{recent.stdout}{recent.stderr}"
            )

        raise RuntimeError(
            f"start {name} failed after {attempt} attempt(s):\n"
            f"{p.stderr}\n"
            f"{p.stdout}"
            f"{diagnostic}"
        )


PROMPT_MOVEMENT_TIMEOUT_MS = 30000
PROMPT_POLL_SECONDS = 0.25
PROMPT_SETTLED_STATES = {
    "idle",
    "done",
    "blocked",
}


def _find_int_field(obj, field):
    """Best-effort recursive integer field extraction."""
    if isinstance(obj, dict):
        value = obj.get(field)

        if isinstance(value, int):
            return value

        for child in obj.values():
            found = _find_int_field(
                child,
                field,
            )

            if found is not None:
                return found

    elif isinstance(obj, list):
        for child in obj:
            found = _find_int_field(
                child,
                field,
            )

            if found is not None:
                return found

    return None


def _prompt_snapshot(agent):
    """Read the observable Herdr state used to settle a prompt."""
    result = run([
        "herdr",
        "agent",
        "get",
        agent,
    ])

    if result.returncode:
        return {
            "status": "missing",
            "state_change_seq": None,
            "revision": None,
        }

    try:
        data = json.loads(
            result.stdout
        )
    except Exception:
        return {
            "status": "unknown",
            "state_change_seq": None,
            "revision": None,
        }

    return {
        "status": (
            find_agent_status(data)
            or "unknown"
        ),
        "state_change_seq": _find_int_field(
            data,
            "state_change_seq",
        ),
        "revision": _find_int_field(
            data,
            "revision",
        ),
    }


def _prompt_state_moved(
    baseline,
    current,
):
    """Determine whether the submitted prompt produced observable activity."""
    before_seq = baseline.get(
        "state_change_seq"
    )

    after_seq = current.get(
        "state_change_seq"
    )

    # state_change_seq is Herdr's strongest signal. Prefer it whenever
    # both snapshots expose it.
    if (
        before_seq is not None
        and after_seq is not None
    ):
        return after_seq != before_seq

    before_revision = baseline.get(
        "revision"
    )

    after_revision = current.get(
        "revision"
    )

    # Older/different Herdr schemas may lack state_change_seq.
    if (
        before_revision is not None
        and after_revision is not None
    ):
        return (
            after_revision
            != before_revision
        )

    return (
        current.get("status")
        != baseline.get("status")
    )


def _prompt_failure(
    cmd,
    submitted,
    code,
    message,
):
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=1,
        stdout=submitted.stdout,
        stderr=json.dumps(
            {
                "error": {
                    "code": code,
                    "message": message,
                }
            }
        ),
    )


def prompt(agent, text, timeout, wait=True):
    """Submit a prompt, optionally settling it without Herdr's 5s wait gate."""
    cmd = [
        "herdr",
        "agent",
        "prompt",
        agent,
        text,
    ]

    if not wait:
        return run(cmd)

    baseline = _prompt_snapshot(
        agent
    )

    submitted = run(
        cmd
    )

    if submitted.returncode:
        return submitted

    started = time.monotonic()

    overall_deadline = (
        started
        + max(
            0.001,
            timeout / 1000.0,
        )
    )

    movement_deadline = min(
        overall_deadline,
        started
        + (
            PROMPT_MOVEMENT_TIMEOUT_MS
            / 1000.0
        ),
    )

    moved = False

    while time.monotonic() < overall_deadline:
        current = _prompt_snapshot(
            agent
        )

        if (
            not moved
            and _prompt_state_moved(
                baseline,
                current,
            )
        ):
            moved = True

        if (
            moved
            and current.get("status")
            in PROMPT_SETTLED_STATES
        ):
            return submitted

        now = time.monotonic()

        if (
            not moved
            and now >= movement_deadline
        ):
            return _prompt_failure(
                cmd,
                submitted,
                "agent_prompt_unobserved",
                (
                    "prompt submission succeeded but "
                    f"{agent} showed no observable "
                    "state change within "
                    f"{PROMPT_MOVEMENT_TIMEOUT_MS} ms"
                ),
            )

        time.sleep(
            PROMPT_POLL_SECONDS
        )

    return _prompt_failure(
        cmd,
        submitted,
        "agent_prompt_settle_timeout",
        (
            f"{agent} showed prompt activity but "
            "did not reach idle, done, or blocked "
            f"within {timeout} ms"
        ),
    )


HERDR_STATES = {
    "idle",
    "working",
    "blocked",
    "done",
    "unknown",
}


def find_agent_status(obj):
    """Best-effort extraction across Herdr response schema revisions."""
    if isinstance(obj, dict):
        for key in (
            "agent_status",
            "effective_status",
            "status",
            "state",
        ):
            value = obj.get(key)

            if (
                isinstance(value, str)
                and value.lower() in HERDR_STATES
            ):
                return value.lower()

        for value in obj.values():
            found = find_agent_status(value)

            if found:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = find_agent_status(value)

            if found:
                return found

    return None


def agent_info(agent):
    p = run([
        "herdr",
        "agent",
        "get",
        agent,
    ])

    if p.returncode:
        return {
            "status": "missing",
            "raw": None,
        }

    try:
        data = json.loads(p.stdout)
    except Exception:
        return {
            "status": "unknown",
            "raw": None,
        }

    return {
        "status": find_agent_status(data) or "unknown",
        "raw": data,
    }
