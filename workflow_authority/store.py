"""Atomic, fail-closed storage for workflow authority records.

``workflows.json`` lives beside the existing protected adapter
configuration (outside every repository, in the per-user protected
directory), with mode 600 in a mode 700 directory. Writes are atomic
exactly like the adapter state store: a temp file created in the same
directory, ``fchmod`` 600, ``json.dump``, flush, ``fsync``,
``os.replace``, then an fsync of the directory. A crash therefore
never leaves a torn store, and an interrupted write leaves the
previous file byte-identical.

Loading fails closed: a malformed or unknown-version file raises with
an actionable message and is NEVER silently reinitialized — the store
carries authorization records, and re-creating it from nothing could
replay or erase authority. Every record is validated against the
closed workflow schema on every load AND every save.

On LOAD ONLY, and before that validation, ``_normalize_additive_keys``
materializes the DI-REMOTE-3 additive keys as ``None`` on records
written before those keys existed. It inserts only those keys, never
overwrites, and never derives a value; validation afterwards is
unchanged, so an unknown key or any other missing key still refuses
the load. It is deliberately not applied on save.

A subsequent save PERSISTS the materialized keys, and that is forced,
not a liberty taken: ``result_placeholder`` is in the record's
``_TOP_LEVEL_KEYS`` and ``_require_closed_keys`` requires it, so any
record written at all must carry it. The upgrade is value-neutral
(``None`` is exactly what absence meant) and idempotent (both
insertions are guarded by ``not in``), and it can never write a
half-migrated store: ``load`` mutates only the freshly parsed local
document and raises before returning if validation fails, and ``save``
validates the whole document before anything touches the filesystem.
(Pinned by
tests/test_di_remote_3_schema.py:
``test_M6_store_load_materializes_the_additive_keys``,
``test_M6_store_load_normalizes_EVERY_record_not_just_the_first``,
``test_M7_store_load_still_fails_closed_on_a_corrupt_record`` and
``test_save_is_not_normalized``.)

``MAX_WORKFLOW_RECORDS`` is a hard constant, never derived from
input. At the cap, only records in a terminal phase are pruned
(oldest first); an ACTIVE record is never evicted, and if pruning
cannot make room the new record is REFUSED explicitly. Counts
reported by this module are exact (standing truthfulness rule).
"""

import contextlib
import fcntl
import json
import os
import stat
import tempfile

from telegram_operator.config import CONFIG_DIR_RELATIVE
from workflow_authority.record import (
    RESULT_DELIVERY_ADDITIVE_KEYS,
    TERMINAL_PHASES,
    RecordError,
    validate_record,
)

# Version 2 (DI-REMOTE-2 corrective I1): records gained authority
# fields (human_intent, per-field authority content, optional
# issue_or_pr). A version-1 store fails closed on load and is
# migrated only by the explicit human-invoked
# ``tgop migrate-workflows`` (workflow_authority.migrate), which
# retires v1 records into a preserved byte-exact backup — their
# missing authority fields must never be fabricated.
WORKFLOW_STORE_SCHEMA_VERSION = 2
WORKFLOWS_FILE_NAME = "workflows.json"
# Cross-PROCESS mutual exclusion for load-modify-save cycles on the
# workflow store: the Telegram adapter (this increment) and the
# Runtime (a separate process, later increment) both mutate
# workflows.json. Every writer MUST hold this flock around its whole
# load-modify-save cycle — this is a binding part of the store's
# concurrency contract.
WORKFLOWS_LOCK_FILE_NAME = "workflows.lock"

# Hard cap on stored workflow records, never derived from input.
MAX_WORKFLOW_RECORDS = 64

PROBLEM_STORE_FULL = "workflow_store_full"
PROBLEM_DUPLICATE_WORKFLOW = "workflow_duplicate_id"

_TOP_LEVEL_KEYS = ("workflow_store_schema_version", "workflows")


def _normalize_additive_keys(document):
    """Materialize the DI-REMOTE-3 additive keys on ONE record, at the
    LOAD boundary, before ``validate_record`` sees it.

    ``WORKFLOW_SCHEMA_VERSION`` is deliberately NOT bumped for these
    keys (lead plan §2.1): a bump would route every record already on
    disk into ``tgop migrate-workflows``, whose only v1->v2 behaviour
    is RETIREMENT — that would destroy in-flight COMPLETED records, a
    direct violation of the migration truth in strategy §4. This
    function is the whole of the alternative, and its boundaries are
    the point:

      - it inserts ONLY ``result_placeholder`` at the top level, and
        ONLY the six ``RESULT_DELIVERY_ADDITIVE_KEYS`` inside
        ``result_delivery``, and only when ``result_delivery`` is
        already a dict (a null marker stays null — nothing is created);
      - the inserted value is ALWAYS ``None``. It NEVER derives a
        value from anything else in the record. A placeholder that was
        never requested is null, and null means "this record predates
        or sits outside the placeholder architecture" (the §4 legacy
        lane) — never "placeholder not needed";
      - it NEVER overwrites a key that is present, INCLUDING one
        present with the value ``None``;
      - it fills NO other missing key. An unknown key and any other
        missing key are still REFUSED by the strict, closed-key
        ``validate_record`` that runs unchanged AFTER this.

    The failure mode this function must never become is "fill in
    anything missing", which would silently repair a corrupt
    authorization record instead of failing closed. It is a named
    function with its own anti-abuse tests (T-M6/T-M7) rather than an
    inline ``.get()`` scattered through consumers.

    Total: a non-dict document is returned untouched (the strict
    validation below reports it). Returns the same object, mutated.
    """
    if not isinstance(document, dict):
        return document
    if "result_placeholder" not in document:
        document["result_placeholder"] = None
    delivery = document.get("result_delivery")
    if isinstance(delivery, dict):
        for key in RESULT_DELIVERY_ADDITIVE_KEYS:
            if key not in delivery:
                delivery[key] = None
    return document


class StoreError(Exception):
    """The workflow store is unreadable or malformed; message is
    actionable."""


# Any group/other access bit. The store is authority-bearing:
# group/other WRITE would let another local account forge or erase
# authorization records, and the records carry mission text and chat
# identifiers, so group/other READ is refused too — the same posture
# the adapter config loader takes for the bot token.
_FORBIDDEN_STORE_MODE_BITS = 0o077


def _refuse_open_store_permissions(path):
    """Raise StoreError if group/other can access the store file.

    ``save`` always creates the file mode 600; a wider mode on load
    means somebody changed it after the fact, and the store must not
    be trusted (or leaked) until a human fixes the permissions.
    """
    mode = os.stat(path).st_mode
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        raise StoreError(
            "workflow store %s is accessible by group/other (mode %o);"
            " it carries authorization records, so refusing to load"
            " it. Fix with: chmod 600 %r" % (
                path, stat.S_IMODE(mode), path,
            )
        )


def default_store_dir(home=None):
    """Directory holding the workflow store, outside any repository."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, CONFIG_DIR_RELATIVE)


def default_document():
    """A fresh, empty workflow store document."""
    return {
        "workflow_store_schema_version": WORKFLOW_STORE_SCHEMA_VERSION,
        "workflows": {},
    }


def _validate_document(document, path):
    if not isinstance(document, dict):
        raise StoreError(
            "workflow store %s must contain a JSON object, not %s;"
            " move the file aside (keeping it for inspection) — it is"
            " NOT safe to delete it: it carries authorization records"
            % (path, type(document).__name__)
        )
    version = document.get("workflow_store_schema_version")
    if isinstance(version, bool) or version != (
        WORKFLOW_STORE_SCHEMA_VERSION
    ):
        raise StoreError(
            "workflow store %s has workflow_store_schema_version %r;"
            " this layer understands only %d. A version-1 store is"
            " migrated ONLY by the explicit 'tgop migrate-workflows'"
            " (v1 records are retired into a preserved backup — their"
            " missing authority fields are never fabricated)."
            " Otherwise move the file aside (keeping it for"
            " inspection) rather than deleting it"
            % (path, version, WORKFLOW_STORE_SCHEMA_VERSION)
        )
    unknown = sorted(set(document) - set(_TOP_LEVEL_KEYS))
    if unknown:
        raise StoreError(
            "workflow store %s has unknown top-level keys: %s; the key"
            " set is closed. Move the file aside (keeping it for"
            " inspection)" % (path, ", ".join(map(repr, unknown)))
        )
    missing = sorted(set(_TOP_LEVEL_KEYS) - set(document))
    if missing:
        raise StoreError(
            "workflow store %s is missing required keys: %s; move the"
            " file aside (keeping it for inspection)"
            % (path, ", ".join(map(repr, missing)))
        )
    workflows = document["workflows"]
    if not isinstance(workflows, dict):
        raise StoreError(
            "workflow store %s key 'workflows' must be an object, not"
            " %s; move the file aside (keeping it for inspection)"
            % (path, type(workflows).__name__)
        )
    if len(workflows) > MAX_WORKFLOW_RECORDS:
        raise StoreError(
            "workflow store %s holds %d records; the hard bound is %d."
            " This store was not written by this layer — move it aside"
            " (keeping it for inspection)"
            % (path, len(workflows), MAX_WORKFLOW_RECORDS)
        )
    for workflow_id, record in workflows.items():
        try:
            validate_record(
                record,
                location="workflow store %s record %r"
                % (path, workflow_id),
            )
        except RecordError as exc:
            raise StoreError(str(exc))
        if record["workflow_id"] != workflow_id:
            raise StoreError(
                "workflow store %s record keyed %r carries"
                " workflow_id %r; the key and the record must agree."
                " Move the file aside (keeping it for inspection)"
                % (path, workflow_id, record["workflow_id"])
            )


class WorkflowStore(object):
    """Atomic load/save of the workflow store document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, WORKFLOWS_FILE_NAME)

    def load(self):
        """Read the store; a missing file yields a fresh default.

        Every other failure — unreadable file, invalid JSON, unknown
        version, closed-schema violation in any record — raises
        StoreError and leaves the file untouched. Nothing is ever
        silently reinitialized.
        """
        if not os.path.exists(self.path):
            return default_document()
        _refuse_open_store_permissions(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StoreError(
                "workflow store %s could not be read as JSON (%s);"
                " move the file aside (keeping it for inspection) —"
                " it is NOT safe to delete it: it carries"
                " authorization records" % (self.path, exc)
            )
        # LOAD boundary: materialize the additive keys on every
        # record BEFORE the strict closed-schema validation below.
        # Deliberately not applied on save — a writer must produce a
        # complete record, and normalizing on the way out would hide
        # exactly that defect.
        workflows = document.get("workflows") if isinstance(
            document, dict
        ) else None
        if isinstance(workflows, dict):
            for record in workflows.values():
                _normalize_additive_keys(record)
        _validate_document(document, self.path)
        return document

    def save(self, document):
        """Atomically persist the store: temp file, fsync, replace.

        The document is validated (closed schema, every record)
        BEFORE anything touches the filesystem, so an invalid
        document can never clobber a valid store.
        """
        _validate_document(document, self.path)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".workflows-", suffix=".tmp", dir=self.directory
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


@contextlib.contextmanager
def exclusive_store_lock(directory):
    """Blocking cross-process lock over the workflow store.

    Hold this around every load-modify-save cycle. The lock file is
    separate from the store file so ``os.replace`` never invalidates
    the held descriptor.
    """
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(directory, WORKFLOWS_LOCK_FILE_NAME)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def is_active(record):
    """True when the record is in a non-terminal lifecycle phase."""
    return record["phase"] not in TERMINAL_PHASES


def store_counts(document):
    """Exact record counts: total, active, inactive."""
    workflows = document["workflows"]
    active = sum(1 for record in workflows.values() if is_active(record))
    total = len(workflows)
    return {"total": total, "active": active, "inactive": total - active}


def _prune_inactive(document):
    """Drop terminal-phase records, oldest first, only as needed to
    get back under the cap. Active records are never pruned. Returns
    the EXACT number of records pruned, so the bound is observable at
    the call site rather than silent."""
    workflows = document["workflows"]
    if len(workflows) < MAX_WORKFLOW_RECORDS:
        return 0
    inactive = sorted(
        (
            workflow_id
            for workflow_id, record in workflows.items()
            if not is_active(record)
        ),
        key=lambda workflow_id: (
            workflows[workflow_id]["approval"]["created_at"],
            workflow_id,
        ),
    )
    pruned = 0
    for workflow_id in inactive:
        if len(workflows) < MAX_WORKFLOW_RECORDS:
            break
        del workflows[workflow_id]
        pruned += 1
    return pruned


def add_workflow(document, record):
    """Add a validated record to the store document, or refuse.

    Returns ``(ok, problem, pruned)``. ``ok`` is True on success with
    ``problem`` None; ``(False, problem, pruned)`` when the workflow
    id already exists (``pruned`` is 0 — nothing was touched) or when
    the store is at ``MAX_WORKFLOW_RECORDS`` even after pruning
    terminal-phase records — an explicit refusal; an active record is
    never evicted to make room. ``pruned`` is the EXACT number of
    terminal-phase records dropped to make room (standing
    truthfulness rule: pruning of authorization records is reported,
    never silent). The caller reports exact totals via
    ``store_counts``.
    """
    validate_record(record)
    workflows = document["workflows"]
    if record["workflow_id"] in workflows:
        return False, PROBLEM_DUPLICATE_WORKFLOW, 0
    pruned = _prune_inactive(document)
    if len(workflows) >= MAX_WORKFLOW_RECORDS:
        return False, PROBLEM_STORE_FULL, pruned
    workflows[record["workflow_id"]] = record
    return True, None, pruned
