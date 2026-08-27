"""Durable adapter state, stored atomically OUTSIDE the target repository.

The state file holds the Telegram update offset, the chat-to-Codex
session map, approval records, the bounded work queue, in-flight
dispatch markers, and the last-request lifecycle record that /status
reads first. Writes are atomic (temp file in the same directory, fsync,
``os.replace``) so a crash never leaves a torn file. A malformed or
unrecognized state file fails closed with an actionable message and is
NEVER silently reinitialized: this file carries authority-bearing
records (approval consumption, offsets), and re-creating it from
nothing could replay authority or old updates.

Single-instance locking uses ``fcntl.flock`` on a lock file in the
state directory, so two adapter processes can never interleave writes
or double-consume approvals.
"""

import fcntl
import json
import os
import tempfile

from telegram_operator.config import CONFIG_DIR_RELATIVE

# Schema version 2 marks every pre-1.0 (DI-REMOTE-1-era) approval
# record superseded FOR V2 PURPOSES ONLY via the explicit, human-run
# `tgop migrate-state` command: an old approval must never authorize a
# v2 target. v1 local semantics are unchanged by the migration.
STATE_SCHEMA_VERSION = 2
_PREVIOUS_SCHEMA_VERSION = 1
MIGRATION_COMMAND = "tgop migrate-state"
STATE_FILE_NAME = "state.json"
LOCK_FILE_NAME = "adapter.lock"
# The Runtime process (a separate daemon that claims authorized v2
# workflows) holds THIS lock while running; /status probes it
# non-destructively to tell an operator, actionably, that authorized
# missions will not start because no Runtime is running. This name is
# a binding contract for the Runtime increment.
RUNTIME_LOCK_FILE_NAME = "runtime.lock"

# Hard bounds, never derived from input. A full queue REFUSES new work
# with an explicit user-visible message; nothing is silently dropped.
MAX_QUEUE_DEPTH = 16
# Approval records at the cap: consumed/expired/superseded records are
# pruned deterministically first; if the cap is still reached, creating
# a NEW approval is refused explicitly. Active records are never
# silently evicted.
MAX_APPROVAL_RECORDS = 64
# Session map entries beyond the cap: the oldest entries by updated_at
# are dropped and the drop is recorded in the state itself
# (sessions_dropped_total), so the bound is visible, not silent.
MAX_SESSION_ENTRIES = 64

# The closed top-level shape of the state document. Public: the
# explicit state migration (workflow_authority.migrate) validates v1
# files against this same table, so the two layers can never drift
# silently on what a state document is shaped like.
TOP_LEVEL_SHAPE = {
    "state_schema_version": int,
    "update_offset": (int, type(None)),
    "sessions": dict,
    "approvals": dict,
    "queue": list,
    "in_flight": (dict, type(None)),
    "last_request": (dict, type(None)),
    "sessions_dropped_total": int,
}
# Backward-compatible private alias.
_TOP_LEVEL_SHAPE = TOP_LEVEL_SHAPE


class StateError(Exception):
    """State is unreadable or malformed; message is actionable."""


def default_state_dir(home=None):
    """Directory holding adapter state, outside any repository."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, CONFIG_DIR_RELATIVE)


def default_state():
    """A fresh, empty state document."""
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "update_offset": None,
        "sessions": {},
        "approvals": {},
        "queue": [],
        "in_flight": None,
        "last_request": None,
        "sessions_dropped_total": 0,
    }


def _validate_shape(document, path):
    if not isinstance(document, dict):
        raise StateError(
            "state file %s must contain a JSON object, not %s; move the"
            " file aside (keeping it for inspection) to start fresh"
            % (path, type(document).__name__)
        )
    version = document.get("state_schema_version")
    if isinstance(version, bool) or version != STATE_SCHEMA_VERSION:
        if not isinstance(version, bool) and version == (
            _PREVIOUS_SCHEMA_VERSION
        ):
            # A v1 file is NEVER auto-migrated and NEVER
            # reinitialized: migration marks pre-existing approvals
            # superseded for v2 purposes, which must be an explicit,
            # human-invoked step.
            raise StateError(
                "state file %s has state_schema_version %d; this"
                " adapter now requires version %d. Run '%s' to"
                " migrate it explicitly (a preserved backup is kept)."
                " It is NOT safe to delete the file: it records"
                " approval consumption and the Telegram offset" % (
                    path, _PREVIOUS_SCHEMA_VERSION,
                    STATE_SCHEMA_VERSION, MIGRATION_COMMAND,
                )
            )
        raise StateError(
            "state file %s has state_schema_version %r; this adapter"
            " understands only %d. Move the file aside (keeping it for"
            " inspection) rather than deleting it" % (
                path, version, STATE_SCHEMA_VERSION,
            )
        )
    for key, expected in TOP_LEVEL_SHAPE.items():
        if key not in document:
            raise StateError(
                "state file %s is missing required key %r; move the file"
                " aside (keeping it for inspection) to start fresh"
                % (path, key)
            )
        value = document[key]
        if isinstance(value, bool) and expected is not bool:
            raise StateError(
                "state file %s key %r has invalid boolean value; move"
                " the file aside (keeping it for inspection)" % (path, key)
            )
        if not isinstance(value, expected):
            raise StateError(
                "state file %s key %r has invalid type %s; move the file"
                " aside (keeping it for inspection)" % (
                    path, key, type(value).__name__,
                )
            )


class StateStore(object):
    """Atomic load/save of the adapter state document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, STATE_FILE_NAME)

    def load(self):
        """Read state; a missing file yields a fresh default state."""
        if not os.path.exists(self.path):
            return default_state()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StateError(
                "state file %s could not be read as JSON (%s); move the"
                " file aside (keeping it for inspection) to start fresh."
                " It is NOT safe to delete it: it records approval"
                " consumption and the Telegram offset" % (self.path, exc)
            )
        _validate_shape(document, self.path)
        return document

    def save(self, document):
        """Atomically persist state: temp file, fsync, os.replace."""
        _validate_shape(document, self.path)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".state-", suffix=".tmp", dir=self.directory
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        directory_descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)


def enqueue(document, item):
    """Append work to the bounded queue.

    Returns True when accepted. Returns False when the queue is at
    MAX_QUEUE_DEPTH: the caller must tell the user explicitly that the
    work was refused (bounded and honest, never silently dropped).
    """
    queue = document["queue"]
    if len(queue) >= MAX_QUEUE_DEPTH:
        return False
    queue.append(item)
    return True


def record_session(document, chat_id, entry):
    """Store a chat's session entry, enforcing MAX_SESSION_ENTRIES.

    When the cap forces eviction, the oldest entries by ``updated_at``
    are removed and ``sessions_dropped_total`` is incremented by the
    exact number dropped, so the bound is observable in /status.
    """
    sessions = document["sessions"]
    sessions[str(chat_id)] = entry
    excess = len(sessions) - MAX_SESSION_ENTRIES
    if excess > 0:
        oldest = sorted(
            sessions.items(),
            key=lambda pair: (pair[1].get("updated_at") or 0, pair[0]),
        )[:excess]
        for key, _ in oldest:
            del sessions[key]
        document["sessions_dropped_total"] += excess


def acquire_single_instance_lock(directory):
    """Take the adapter's exclusive instance lock, or return None.

    The returned file descriptor must stay open for the life of the
    process; closing it releases the lock. Returns None when another
    live adapter instance already holds the lock.
    """
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(directory, LOCK_FILE_NAME)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return descriptor
