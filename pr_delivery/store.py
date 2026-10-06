"""Atomic, fail-closed storage for PR Delivery Authorizations.

``pr_delivery.json`` is a SIBLING of ``workflows.json`` in the same
protected per-user directory (outside every repository, mode 600 in a
mode 700 directory) with its own schema version, hard record cap, and
lock file. It reuses ``workflow_authority.store``'s atomic-replace
primitive and lock rather than copying them. It never opens
``workflows.json``: the Mission Authorization record is a different
authority object and this store must not be able to touch it.

Loading fails closed: a malformed, group-readable, or unknown-version
file raises and is never silently reinitialized. Every record is
validated on every load and every save.

The git hooks (``herdr.guards``) read this store through
``pr_delivery.receipts.guard_decision``; that reader is read-only and
converts every failure here into a refusal reason (Lead M1).
"""

import contextlib
import fcntl
import json
import os
import stat

from workflow_authority.store import (
    READ_ABSENT,
    READ_PRESENT,
    READ_UNAVAILABLE,
    ReadResult,
    atomic_write_json,
    classify_missing,
    default_store_dir,
    exclusive_store_lock,
    read_store_document,
)

from pr_delivery.authorization import (
    TERMINAL_PHASES,
    AuthorizationError,
    is_client_confirmed,
    validate_authorization,
)
from workflow_authority.record import MAX_ID_CHARS, WORKFLOW_ID_ALPHABET

STORE_SCHEMA_VERSION = 1
STORE_FILE_NAME = "pr_delivery.json"
STORE_LOCK_FILE_NAME = "pr_delivery.lock"

# Hard cap on stored delivery records, never derived from input.
MAX_PR_DELIVERY_RECORDS = 64

PROBLEM_STORE_FULL = "pr_delivery_store_full"
PROBLEM_DUPLICATE_DELIVERY = "pr_delivery_duplicate_id"
PROBLEM_DRIVE_BUSY = "pr_delivery_drive_busy"

# Task 8 S-VI: the per-delivery DRIVE lock. Every driver of one delivery
# (the Runtime's Mission bridge, the standalone CLI) holds it across a
# whole ``advance_once`` — every effect and its reconciliation — so a
# delivery has exactly one effect owner at a time. It is taken NON-blocking:
# a second driver gets ``DriveBusy`` and performs nothing. Revocation never
# takes it (the store lock alone), so a stop is always recordable while a
# driver is mid-step, and the driver folds it in (``machine._persist``).
DRIVE_LOCK_FILE_TEMPLATE = "pr_delivery-drive-%s.lock"


class DriveBusy(Exception):
    """Another driver holds this delivery's drive lock right now."""

_TOP_LEVEL_KEYS = ("pr_delivery_store_schema_version", "deliveries")
_FORBIDDEN_STORE_MODE_BITS = 0o077


class StoreError(Exception):
    """The delivery store is unreadable or malformed; message actionable."""


def store_directory(home=None):
    """The protected directory the store lives in (shared with the
    workflow store; the two files never overlap)."""
    return default_store_dir(home)


def default_document():
    return {
        "pr_delivery_store_schema_version": STORE_SCHEMA_VERSION,
        "deliveries": {},
    }


def _refuse_open_store_permissions(path):
    mode = os.stat(path).st_mode
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        raise StoreError(
            "PR delivery store %s is accessible by group/other (mode %o);"
            " it carries authorization records, so refusing to load it."
            " Fix with: chmod 600 %r" % (path, stat.S_IMODE(mode), path)
        )


def _validate_document(document, path):
    if not isinstance(document, dict):
        raise StoreError(
            "PR delivery store %s must contain a JSON object, not %s; move"
            " the file aside (keeping it for inspection)"
            % (path, type(document).__name__)
        )
    version = document.get("pr_delivery_store_schema_version")
    if isinstance(version, bool) or version != STORE_SCHEMA_VERSION:
        raise StoreError(
            "PR delivery store %s has pr_delivery_store_schema_version %r;"
            " this layer understands only %d. Move the file aside (keeping"
            " it for inspection)" % (path, version, STORE_SCHEMA_VERSION)
        )
    unknown = sorted(set(document) - set(_TOP_LEVEL_KEYS))
    if unknown:
        raise StoreError(
            "PR delivery store %s has unknown top-level keys: %s"
            % (path, ", ".join(map(repr, unknown)))
        )
    missing = sorted(set(_TOP_LEVEL_KEYS) - set(document))
    if missing:
        raise StoreError(
            "PR delivery store %s is missing required keys: %s"
            % (path, ", ".join(map(repr, missing)))
        )
    deliveries = document["deliveries"]
    if not isinstance(deliveries, dict):
        raise StoreError(
            "PR delivery store %s key 'deliveries' must be an object"
            % path
        )
    if len(deliveries) > MAX_PR_DELIVERY_RECORDS:
        raise StoreError(
            "PR delivery store %s holds %d records; the hard bound is %d"
            % (path, len(deliveries), MAX_PR_DELIVERY_RECORDS)
        )
    for delivery_id, record in deliveries.items():
        try:
            validate_authorization(
                record,
                location="PR delivery store %s record %r"
                % (path, delivery_id),
            )
        except AuthorizationError as exc:
            raise StoreError("%s (%s)" % (exc, exc.problem))
        if record["delivery_id"] != delivery_id:
            raise StoreError(
                "PR delivery store %s record keyed %r carries delivery_id"
                " %r" % (path, delivery_id, record["delivery_id"])
            )


class DeliveryStore(object):
    """Atomic load/save of the delivery store document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, STORE_FILE_NAME)

    def load(self):
        # Task 8 R23 (amendment): absence is observed STRICTLY. Only a stat
        # raising FileNotFoundError is a missing store; ``os.path.exists`` was
        # False for EVERY OSError (EACCES, EIO), so an unreadable store loaded
        # as an EMPTY document and the ceremony insert saved over it.
        # Task 8 R24-1: stat raises FileNotFoundError for an EXISTING link
        # whose target is unavailable too (at the store or at an ancestor), so
        # the traversal (``classify_missing``) decides: genuine absence keeps
        # the empty default, anything else is UNAVAILABLE.
        problem = None
        try:
            os.stat(self.path)
        except FileNotFoundError:
            missing = classify_missing(self.path)
            if missing.availability == READ_ABSENT:
                return default_document()
            problem = missing.problem
        except OSError as exc:
            problem = "%s: %s" % (exc.__class__.__name__, exc)
        if problem is not None:
            raise StoreError(
                "PR delivery store %s cannot be examined (%s); it is"
                " UNAVAILABLE, not absent — refusing to read it as an empty"
                " store. It is NOT safe to delete or reinitialize it: it"
                " carries delivery authorizations" % (self.path, problem)
            )
        _refuse_open_store_permissions(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StoreError(
                "PR delivery store %s could not be read as JSON (%s); move"
                " the file aside (keeping it for inspection)"
                % (self.path, exc)
            )
        _validate_document(document, self.path)
        return document

    def read(self):
        """The observer read (Task 8, slice S-II): ONE document read through
        this store's own validation, as a ``ReadResult``. Read by
        descriptor (``read_store_document``): the OPENED file must be a
        regular file group/other cannot reach — the file-mode rule
        ``load`` enforces, applied to what was actually opened (this
        store has no directory policy, exactly as ``load``); a symbolic
        link to a readable target stays supported, a dangling,
        inaccessible or exposed target refuses. PRESENT carries the
        validated document; ABSENT only for a directory or file genuinely
        missing; UNAVAILABLE names the refusing rule or the OSError class
        for every access, decode, parse or validation failure (none
        escapes). Read-only: no lock, no creation; ``load`` unchanged."""
        read = read_store_document(self.directory, STORE_FILE_NAME, "StoreError",
                                   refuse_exposed_directory=False)
        if read.availability != READ_PRESENT:
            return read
        document = read.document
        try:
            _validate_document(document, self.path)
        except StoreError as exc:
            return ReadResult(READ_UNAVAILABLE, None, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - validation never escapes
            return ReadResult(READ_UNAVAILABLE, None,
                              "StoreError: %s" % type(exc).__name__)
        return ReadResult(READ_PRESENT, document, None)

    def save(self, document):
        _validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".pr_delivery-")

    def lock(self):
        """Blocking cross-process lock for a load-modify-save cycle."""
        return exclusive_store_lock(self.directory, STORE_LOCK_FILE_NAME)

    @contextlib.contextmanager
    def drive_lock(self, delivery_id):
        """The NON-blocking per-delivery drive lock (see
        ``DRIVE_LOCK_FILE_TEMPLATE``). Raises ``DriveBusy`` at once when
        another driver holds it; nothing is waited for or performed."""
        validate_drive_id(delivery_id)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        path = os.path.join(self.directory,
                            DRIVE_LOCK_FILE_TEMPLATE % delivery_id)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DriveBusy(
                    "PR delivery %s is being driven by another owner right"
                    " now (%s); nothing was performed" % (
                        delivery_id, PROBLEM_DRIVE_BUSY))
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def validate_drive_id(delivery_id):
    """A delivery id is a closed-alphabet identifier (it names a lock
    file): refuse anything that is not."""
    if (not isinstance(delivery_id, str) or not delivery_id
            or len(delivery_id) > MAX_ID_CHARS
            or any(ch not in WORKFLOW_ID_ALPHABET for ch in delivery_id)):
        raise StoreError("delivery id %r is not a closed-alphabet identifier"
                         % (delivery_id,))


def is_active(record):
    return record["phase"] not in TERMINAL_PHASES


def _prune_inactive(document):
    """Drop terminal records, oldest first, to make room. A client-confirmed
    (Mission-bound) record is NEVER pruned (Task 8 S-VI): its Mission
    lifecycle — receipt attestation, the one-decision -> one-delivery binding
    — still reads it, and a pruned record could otherwise be minted again.
    A store full of such records refuses insertion (fail closed)."""
    deliveries = document["deliveries"]
    if len(deliveries) < MAX_PR_DELIVERY_RECORDS:
        return 0
    inactive = sorted(
        (
            delivery_id for delivery_id, record in deliveries.items()
            if not is_active(record) and not is_client_confirmed(record)
        ),
        key=lambda delivery_id: (
            deliveries[delivery_id]["human_authorization"]["authorized_at"],
            delivery_id,
        ),
    )
    pruned = 0
    for delivery_id in inactive:
        if len(deliveries) < MAX_PR_DELIVERY_RECORDS:
            break
        del deliveries[delivery_id]
        pruned += 1
    return pruned


def add_delivery(document, record):
    """Add a validated record or refuse. Returns ``(ok, problem, pruned)``;
    an active record is never evicted to make room."""
    validate_authorization(record)
    deliveries = document["deliveries"]
    if record["delivery_id"] in deliveries:
        return False, PROBLEM_DUPLICATE_DELIVERY, 0
    pruned = _prune_inactive(document)
    if len(deliveries) >= MAX_PR_DELIVERY_RECORDS:
        return False, PROBLEM_STORE_FULL, pruned
    deliveries[record["delivery_id"]] = record
    return True, None, pruned
