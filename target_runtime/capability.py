"""Runtime-issued one-shot internal Broker capabilities (I3).

A capability is an INTERNAL authorization token: the Runtime mints
one — durably, bound to exactly ``(workflow_id, action, revision)``
with a random nonce and a hard expiry — only after it has
independently validated a structured transition request, and the
Broker validates and CONSUMES it exactly once at its gate before any
effect. Codex never issues, sees, or receives one: a capability
value exists ONLY in this store's file (mode 600, atomic writes) and
in the Runtime/Broker call path — never in a Codex prompt, a
Telegram message, a receipt, a log line, a status output, or the
workflow record (whose closed schema cannot carry it). A behavioral
test drives a full lifecycle with a known nonce and asserts exactly
that; the static suite independently proves the control chain cannot
even import this package.

Consumption is durable BEFORE the action's effects: a crash between
consumption and effect costs one capability (the Runtime mints a new
one on a later validated request), never a replay. Every refusal
writes nothing.

Refusal-code ordering note (round-05 ledger): a consumed capability
presented again IMMEDIATELY is refused ``capability_already_consumed``;
presented again after an INTERVENING mint it may surface
``capability_unknown`` instead, because ``mint`` prunes consumed
entries. Both refuse and write nothing — the specific code is a
diagnostic, not a guarantee about which of the two a replay sees.
"""

import json
import os
import secrets
import tempfile

CAPABILITIES_FILE_NAME = "capabilities.json"
CAPABILITY_STORE_SCHEMA_VERSION = 1

# Hard bounds, never derived from input.
MAX_CAPABILITIES = 256
# A minted capability not consumed within this window expires; the
# Runtime simply mints a fresh one after re-validating. Pacing of
# authority, not a mission timeout — nothing is cancelled by it.
CAPABILITY_VALIDITY_SECONDS = 900

PROBLEM_CAPABILITY_MISSING = "capability_missing"
PROBLEM_CAPABILITY_UNKNOWN = "capability_unknown"
PROBLEM_CAPABILITY_CONSUMED = "capability_already_consumed"
PROBLEM_CAPABILITY_EXPIRED = "capability_expired"
PROBLEM_CAPABILITY_MISMATCH = "capability_binding_mismatch"
PROBLEM_CAPABILITY_STORE = "capability_store_unreadable"
PROBLEM_CAPABILITY_STORE_FULL = "capability_store_full"

_ENTRY_KEYS = frozenset(
    ("workflow_id", "action", "revision", "issued_at", "expires_at",
     "consumed_at")
)
_TOP_LEVEL_KEYS = ("capability_store_schema_version", "capabilities")


class CapabilityError(Exception):
    """The capability store is unusable; message is actionable."""


def _default_nonce_factory():
    return secrets.token_hex(32)


def _path(directory):
    return os.path.join(directory, CAPABILITIES_FILE_NAME)


def _default_document():
    return {
        "capability_store_schema_version": (
            CAPABILITY_STORE_SCHEMA_VERSION
        ),
        "capabilities": {},
    }


def _validate_document(document, path):
    if (
        not isinstance(document, dict)
        or set(document.keys()) != set(_TOP_LEVEL_KEYS)
        or document.get("capability_store_schema_version") != (
            CAPABILITY_STORE_SCHEMA_VERSION
        )
        or not isinstance(document.get("capabilities"), dict)
    ):
        raise CapabilityError(
            "capability store %s is malformed or has an unknown"
            " schema version; move it aside (keeping it for"
            " inspection) — a malformed store is never silently"
            " reinitialized" % path
        )
    for nonce, entry in document["capabilities"].items():
        if (
            not isinstance(nonce, str)
            or not isinstance(entry, dict)
            or set(entry.keys()) != _ENTRY_KEYS
        ):
            raise CapabilityError(
                "capability store %s entry %r is malformed; move the"
                " file aside (keeping it for inspection)"
                % (path, nonce)
            )


def _load(directory):
    path = _path(directory)
    if not os.path.exists(path):
        return _default_document()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CapabilityError(
            "capability store %s could not be read as JSON (%s); move"
            " it aside (keeping it for inspection)" % (path, exc)
        )
    _validate_document(document, path)
    return document


def _save(directory, document):
    _validate_document(document, _path(directory))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".capabilities-", suffix=".tmp", dir=directory
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, _path(directory))
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _prune(document, now):
    """Drop consumed and expired entries; exact count returned."""
    capabilities = document["capabilities"]
    stale = [
        nonce
        for nonce, entry in capabilities.items()
        if entry["consumed_at"] is not None
        or now >= entry["expires_at"]
    ]
    for nonce in stale:
        del capabilities[nonce]
    return len(stale)


def mint(directory, workflow_id, action, revision, now,
         nonce_factory=None):
    """Mint one durable one-shot capability; returns the nonce.

    Bound to exactly ``(workflow_id, action, revision)`` with a hard
    expiry. Raises CapabilityError when the store is unusable or full
    of LIVE capabilities even after pruning (never silently evicts a
    live one). The caller (the Runtime, and ONLY the Runtime) must
    hold the workflow-store lock around mint + perform.
    """
    document = _load(directory)
    _prune(document, now)
    if len(document["capabilities"]) >= MAX_CAPABILITIES:
        raise CapabilityError(
            "capability store holds %d live capabilities; the hard"
            " bound is %d and a live capability is never evicted"
            % (len(document["capabilities"]), MAX_CAPABILITIES)
        )
    make_nonce = nonce_factory or _default_nonce_factory
    nonce = make_nonce()
    document["capabilities"][nonce] = {
        "workflow_id": workflow_id,
        "action": action,
        "revision": revision,
        "issued_at": now,
        "expires_at": now + CAPABILITY_VALIDITY_SECONDS,
        "consumed_at": None,
    }
    _save(directory, document)
    return nonce


def validate_and_consume(directory, nonce, workflow_id, action,
                         revision, now):
    """Validate one presented capability and consume it exactly once.

    Returns ``(ok, problem, detail)``. Every refusal — missing,
    unknown, already consumed, expired, or bound to a different
    (workflow, action, revision) — writes NOTHING. On success the
    consumption is saved durably BEFORE the caller performs any
    effect.
    """
    if not isinstance(nonce, str) or not nonce:
        return False, PROBLEM_CAPABILITY_MISSING, (
            "no capability was presented; every Broker action"
            " requires a Runtime-issued one-shot capability"
        )
    try:
        document = _load(directory)
    except CapabilityError as exc:
        return False, PROBLEM_CAPABILITY_STORE, str(exc)
    entry = document["capabilities"].get(nonce)
    if entry is None:
        return False, PROBLEM_CAPABILITY_UNKNOWN, (
            "the presented capability is not in the store (never"
            " minted, pruned, or forged)"
        )
    if entry["consumed_at"] is not None:
        return False, PROBLEM_CAPABILITY_CONSUMED, (
            "the presented capability was already consumed at %r;"
            " a capability is single-use" % entry["consumed_at"]
        )
    if now >= entry["expires_at"]:
        return False, PROBLEM_CAPABILITY_EXPIRED, (
            "the presented capability expired at %r (now %r)"
            % (entry["expires_at"], now)
        )
    if (
        entry["workflow_id"] != workflow_id
        or entry["action"] != action
        or entry["revision"] != revision
    ):
        return False, PROBLEM_CAPABILITY_MISMATCH, (
            "the presented capability is bound to (%r, %r, %r), not"
            " (%r, %r, %r); cross-workflow or cross-action reuse is"
            " refused" % (
                entry["workflow_id"], entry["action"],
                entry["revision"], workflow_id, action, revision,
            )
        )
    entry["consumed_at"] = now
    try:
        _save(directory, document)
    except (CapabilityError, OSError) as exc:
        return False, PROBLEM_CAPABILITY_STORE, (
            "the capability consumption could not be persisted (%s);"
            " refusing to act on an unconsumed capability" % exc
        )
    return True, None, None
