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

import json
import os
import stat

from workflow_authority.store import (
    atomic_write_json,
    default_store_dir,
    exclusive_store_lock,
)

from pr_delivery.authorization import (
    TERMINAL_PHASES,
    AuthorizationError,
    validate_authorization,
)

STORE_SCHEMA_VERSION = 1
STORE_FILE_NAME = "pr_delivery.json"
STORE_LOCK_FILE_NAME = "pr_delivery.lock"

# Hard cap on stored delivery records, never derived from input.
MAX_PR_DELIVERY_RECORDS = 64

PROBLEM_STORE_FULL = "pr_delivery_store_full"
PROBLEM_DUPLICATE_DELIVERY = "pr_delivery_duplicate_id"

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
        if not os.path.exists(self.path):
            return default_document()
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

    def save(self, document):
        _validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".pr_delivery-")

    def lock(self):
        """Blocking cross-process lock for a load-modify-save cycle."""
        return exclusive_store_lock(self.directory, STORE_LOCK_FILE_NAME)


def is_active(record):
    return record["phase"] not in TERMINAL_PHASES


def _prune_inactive(document):
    deliveries = document["deliveries"]
    if len(deliveries) < MAX_PR_DELIVERY_RECORDS:
        return 0
    inactive = sorted(
        (
            delivery_id for delivery_id, record in deliveries.items()
            if not is_active(record)
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
