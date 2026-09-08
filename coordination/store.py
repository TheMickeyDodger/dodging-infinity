"""One atomic durable document for coordination state, with its own lock.

``coordination.json`` is ONE JSON document holding the conversation
bindings, the route decisions, the attention records, the bot handoffs
and the per-Mission participant rosters:

    {"coordination_store_schema_version": 1,
     "store_sequence": <int>,
     "bindings":        {<cb-id>: <binding record>},
     "route_decisions": {<cr-id>: <route decision record>},
     "attention":       {<ca-id>: <attention record>},
     "handoffs":        {<ch-id>: <handoff record>},
     "participants":    {<mn-id>: <participant roster record>}}

All five families commit in the SAME ``os.replace`` and can never tear
apart. The file lives in an injected protected directory (mode 600 in a
mode 700 directory) and has its own lock file (``coordination.lock``),
so it never serializes against ``missions.lock`` or ``workflows.lock``.
Writes go through the shared stdlib-only ``workflow_authority.atomic``
primitives; every writer holds ``exclusive_store_lock`` around its whole
load-modify-save cycle.

Loading fails closed: bad JSON, an unknown schema version, an unknown or
missing top-level key, a malformed sequence, a family key outside its
id grammar, any invalid record, a cross-record inconsistency, or a
group/other-accessible mode on the FILE or on the EXISTING DIRECTORY
raises ``CoordinationStoreError`` and the file is NEVER silently
reinitialized. The directory boundary is enforced before every read,
lock and write. A missing file yields a fresh default document. Every
record is validated on every load AND every save; ``save`` validates
before anything touches the filesystem, so an invalid document can
never clobber a valid store.

Conflicting writes fail closed (Lead ruling on ASK 2). ``store_sequence``
increments by exactly one on every save. ``save(document,
expected_sequence)`` refuses (``coordination_store_conflict``) unless
BOTH the in-memory document and the on-disk document stand at
``expected_sequence``; a caller holding a document loaded before another
writer's save can therefore never overwrite that save, even if a lock
were misused. The lock remains the primary serialization; the sequence
is the demonstrable guard.

Hard caps are module constants, never derived from input. At a cap the
store REFUSES; nothing is pruned or evicted (evicting the inbound-turn
ledger of route decisions would silently break route idempotency).
"""

import json
import os
import stat

from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

from coordination import attention
from coordination import binding
from coordination import handoff
from coordination import record
from coordination import routing

COORDINATION_STORE_SCHEMA_VERSION = 1
COORDINATION_FILE_NAME = "coordination.json"
COORDINATION_LOCK_FILE_NAME = "coordination.lock"
TOP_LEVEL_KEYS = (
    "coordination_store_schema_version", "store_sequence", "bindings",
    "route_decisions", "attention", "handoffs", "participants",
)
# Each family's map is keyed by an id of this prefix.
FAMILY_PREFIXES = {
    "bindings": record.BINDING_ID_PREFIX,
    "route_decisions": record.ROUTE_ID_PREFIX,
    "attention": record.ATTENTION_ID_PREFIX,
    "handoffs": record.HANDOFF_ID_PREFIX,
    "participants": record.MISSION_ID_PREFIX,
}

# Hard caps, never derived from input. Exact-value pinned.
MAX_BINDING_RECORDS = 4096
MAX_ROUTE_DECISION_RECORDS = 16384
MAX_ATTENTION_RECORDS = 16384
MAX_HANDOFF_RECORDS = 4096
MAX_PARTICIPANT_ROSTERS = 1024
FAMILY_CAPS = {
    "bindings": MAX_BINDING_RECORDS,
    "route_decisions": MAX_ROUTE_DECISION_RECORDS,
    "attention": MAX_ATTENTION_RECORDS,
    "handoffs": MAX_HANDOFF_RECORDS,
    "participants": MAX_PARTICIPANT_ROSTERS,
}

PROBLEM_STORE_UNREADABLE = record.PROBLEM_STORE_UNREADABLE
PROBLEM_STORE_FULL = record.PROBLEM_STORE_FULL
PROBLEM_STORE_CONFLICT = record.PROBLEM_STORE_CONFLICT

# Any group/other access bit: the store binds conversations to Missions.
_FORBIDDEN_STORE_MODE_BITS = 0o077


class CoordinationStoreError(Exception):
    """The coordination store is unreadable, malformed, full, or would be
    overwritten by a conflicting write; ``problem`` is one of
    ``coordination_store_unreadable`` / ``_full`` / ``_conflict``."""

    def __init__(self, message, problem=PROBLEM_STORE_UNREADABLE):
        super(CoordinationStoreError, self).__init__(message)
        self.problem = problem


def default_document():
    return {
        "coordination_store_schema_version": COORDINATION_STORE_SCHEMA_VERSION,
        "store_sequence": 0,
        "bindings": {},
        "route_decisions": {},
        "attention": {},
        "handoffs": {},
        "participants": {},
    }


def _unreadable(path, message):
    raise CoordinationStoreError(
        "coordination store %s %s; move the file aside (keeping it for"
        " inspection) — it is NOT safe to delete it: it holds the durable"
        " route, attention and handoff history" % (path, message),
        PROBLEM_STORE_UNREADABLE,
    )


def _full(path, message):
    raise CoordinationStoreError("coordination store %s %s" % (path, message),
                                 PROBLEM_STORE_FULL)


def _conflict(path, message):
    raise CoordinationStoreError("coordination store %s %s" % (path, message),
                                 PROBLEM_STORE_CONFLICT)


def require_capacity(family, count, path):
    """Refuse when ``family`` would hold more than its cap."""
    cap = FAMILY_CAPS[family]
    if count > cap:
        _full(path, "holds %d %s records; the hard bound is %d and nothing is"
              " ever evicted" % (count, family, cap))


def validate_document(document, path="<document>"):
    """Every record, family key and cross-reference, or
    CoordinationStoreError."""
    try:
        _validate_document(document, path)
    except record.CoordinationError as exc:
        _unreadable(path, "is malformed (%s): %s" % (exc.problem, exc))
    return document


def _validate_document(document, path):
    if not isinstance(document, dict):
        _unreadable(path, "must contain a JSON object, not %s"
                    % type(document).__name__)
    version = document.get("coordination_store_schema_version")
    # Typed before compared: bool and integral float are refused.
    if not isinstance(version, int) or isinstance(version, bool) or (
        version != COORDINATION_STORE_SCHEMA_VERSION
    ):
        _unreadable(path, "has coordination_store_schema_version %r; this"
                    " layer understands only %d"
                    % (version, COORDINATION_STORE_SCHEMA_VERSION))
    unknown = sorted(set(document) - set(TOP_LEVEL_KEYS))
    if unknown:
        _unreadable(path, "has unknown top-level keys: %s; the key set is"
                    " closed" % ", ".join(map(repr, unknown)))
    missing = sorted(set(TOP_LEVEL_KEYS) - set(document))
    if missing:
        _unreadable(path, "is missing required keys: %s"
                    % ", ".join(map(repr, missing)))
    sequence = document["store_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        _unreadable(path, "has store_sequence %r; it must be a non-negative"
                    " integer" % (sequence,))
    for family in FAMILY_PREFIXES:
        value = document[family]
        if not isinstance(value, dict):
            _unreadable(path, "key %r must be a JSON object, not %s"
                        % (family, type(value).__name__))
        require_capacity(family, len(value), path)
        prefix = FAMILY_PREFIXES[family]
        for key in value:
            reason = record.id_problem(key, prefix)
            if reason is not None:
                _unreadable(path, "%s key %r is not a %s- id: %s"
                            % (family, key, prefix, reason))
    _validate_families(document, path)


def _validate_families(document, path):
    """Per-record and cross-record validation of every family, each
    contributed by the family's own module (``handoff`` validates the
    participant rosters too, because eligibility is answered against
    them)."""
    binding.validate_bindings(document, path)
    routing.validate_route_decisions(document, path)
    attention.validate_attention_records(document, path)
    handoff.validate_handoffs(document, path)


def _refuse_open_permissions(path):
    mode = os.stat(path).st_mode
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        _unreadable(path, "is accessible by group/other (mode %o); it binds"
                    " conversations to Missions. Fix with: chmod 600 %r"
                    % (stat.S_IMODE(mode), path))


def _refuse_open_directory(directory):
    """The protected-directory boundary: an EXISTING store directory that
    group/other can reach is refused before any read, lock, or write. A
    missing directory is fine: it is created mode 700 on first write."""
    try:
        mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        _unreadable(directory, "is not a directory")
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        raise CoordinationStoreError(
            "coordination store directory %s is accessible by group/other"
            " (mode %o); it holds the document and its lock, so nothing is"
            " read, locked, or written. Fix with: chmod 700 %r"
            % (directory, stat.S_IMODE(mode), directory),
            PROBLEM_STORE_UNREADABLE,
        )


class CoordinationStore(object):
    """Atomic load/save of the one coordination document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, COORDINATION_FILE_NAME)

    def lock(self):
        """The cross-process lock every load-modify-save cycle holds. The
        directory boundary is checked before the lock file is touched."""
        _refuse_open_directory(self.directory)
        return exclusive_store_lock(self.directory, COORDINATION_LOCK_FILE_NAME)

    def load(self):
        _refuse_open_directory(self.directory)
        if not os.path.exists(self.path):
            return default_document()
        _refuse_open_permissions(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            _unreadable(self.path, "could not be read as JSON (%s)" % exc)
        return validate_document(document, self.path)

    def save(self, document, expected_sequence):
        """Validate, prove both the in-memory and the on-disk document
        stand at ``expected_sequence``, then write at ``expected_sequence
        + 1``. Nothing touches the filesystem before validation."""
        _refuse_open_directory(self.directory)
        validate_document(document, self.path)
        record.require_int(expected_sequence, "expected_sequence", minimum=0)
        if document["store_sequence"] != expected_sequence:
            _conflict(self.path, "in-memory document stands at sequence %d, not"
                      " the expected %d" % (document["store_sequence"],
                                            expected_sequence))
        on_disk = self.load()["store_sequence"]
        if on_disk != expected_sequence:
            _conflict(self.path, "stands at sequence %d on disk, not the expected"
                      " %d; another writer saved first and this document is"
                      " stale" % (on_disk, expected_sequence))
        document["store_sequence"] = expected_sequence + 1
        try:
            atomic_write_json(self.directory, self.path, document,
                              temp_prefix=".coordination-")
        except BaseException:
            document["store_sequence"] = expected_sequence
            raise
