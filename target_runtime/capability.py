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
one on a later validated request), never a replay.

WHAT A REFUSAL WRITES — the two cases are different, and the
difference is the whole point of this module's ordering contract:

* A refusal BY ``validate_and_consume`` writes NOTHING. Missing,
  malformed (non-str or empty), unknown or forged, already consumed,
  expired, and binding-mismatched presentations are all rejected
  before the function's single write, and no OTHER entry in the store
  is altered, removed, or re-serialized. Nothing a caller can forge
  or replay can destroy live authority belonging to anything else.
* A refusal by the BROKER AFTER a successful consumption does NOT
  restore the capability. ``TargetBroker.perform`` validates and
  consumes BEFORE it reads the workflow store and before its gate
  runs, so an AUTHENTIC, exactly-bound, unconsumed, unexpired
  presentation is SPENT even when the gate then refuses (policy
  digest drift, wrong phase, stale revision, ambiguity, approval
  refusals) and even when the workflow store cannot be read. The
  Runtime mints a fresh capability on its next validated request;
  this is a spend of one token, never a replay.

That ordering is deliberate. Consuming only after the gate left an
authentic capability LIVE through every gate refusal, so a workflow
refusing persistently accrued one live entry per Runtime poll against
``MAX_CAPABILITIES`` — a SHARED, global bound — and degraded the
authority budget every other workflow mints from.

RECOVERY (R-02). ``_prune`` on its own drops only consumed and
expired entries, so authority that became unreachable for any OTHER
reason — its workflow deleted, its revision superseded, its workflow
terminal — stayed until its hard expiry. ``compact`` adds those
grounds, but ONLY on positive proof supplied by the caller's oracle:
this module deliberately cannot import the workflow store or the
Broker, so it cannot and does not decide actionability itself. Every
ambiguity KEEPS the entry. A capability for a live workflow at the
current revision survives compaction even while the gate refuses it
for policy drift, because drift is repairable.

Refusal-code ordering note (round-05 ledger, re-derived after the
consume-before-gate reordering): a consumed capability presented
again IMMEDIATELY is refused ``capability_already_consumed``;
presented again after an INTERVENING mint it may surface
``capability_unknown`` instead, because ``mint`` prunes consumed
entries. Both refuse and write nothing — the specific code is a
diagnostic, not a guarantee about which of the two a replay sees.
Because an authentic presentation is now spent even when the Broker
subsequently refuses, re-presenting the capability from a
gate-refused attempt takes exactly this same path: it is one of those
two codes, never a second execution.
"""

import json
import os
import secrets
import tempfile

from capability import contract as capability_contract

CAPABILITIES_FILE_NAME = "capabilities.json"
CAPABILITY_STORE_SCHEMA_VERSION = 1

# Hard bounds, never derived from input.
MAX_CAPABILITIES = 256
# A minted capability not consumed within this window expires; the
# Runtime simply mints a fresh one after re-validating. Pacing of
# authority, not a mission timeout — nothing is cancelled by it.
CAPABILITY_VALIDITY_SECONDS = 900

# The refusal vocabulary is OWNED by the neutral ``capability.contract``
# seam, because a caller reads ``problem`` through the seam whichever
# implementation produced it. The names here are rebound to the SAME
# string objects, so every existing comparison and every persisted
# value is unchanged; nothing observable moves.
PROBLEM_CAPABILITY_MISSING = capability_contract.PROBLEM_CAPABILITY_MISSING
PROBLEM_CAPABILITY_UNKNOWN = capability_contract.PROBLEM_CAPABILITY_UNKNOWN
PROBLEM_CAPABILITY_CONSUMED = (
    capability_contract.PROBLEM_CAPABILITY_CONSUMED
)
PROBLEM_CAPABILITY_EXPIRED = capability_contract.PROBLEM_CAPABILITY_EXPIRED
PROBLEM_CAPABILITY_MISMATCH = (
    capability_contract.PROBLEM_CAPABILITY_MISMATCH
)
PROBLEM_CAPABILITY_STORE = capability_contract.PROBLEM_CAPABILITY_STORE
PROBLEM_CAPABILITY_STORE_FULL = (
    capability_contract.PROBLEM_CAPABILITY_STORE_FULL
)

_ENTRY_KEYS = frozenset(
    ("workflow_id", "action", "revision", "issued_at", "expires_at",
     "consumed_at")
)
_TOP_LEVEL_KEYS = ("capability_store_schema_version", "capabilities")


class CapabilityError(capability_contract.CapabilityError):
    """The capability store is unusable; message is actionable.

    A subclass of the neutral seam's ``CapabilityError`` so the
    Runtime, which mints and compacts through the seam, catches the
    neutral type and still catches every error this module raises;
    ``str(exc)`` and the type this module raises are unchanged.
    """


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


def _prune(document, now, non_actionable=None, oracle_errors=None):
    """Drop entries that are PROVABLY non-actionable; return the
    removed nonces in sorted order.

    Two independent grounds, and the difference matters:

    * SELF-EVIDENT (always applied, no oracle needed): the entry is
      already consumed, or `now` has reached its hard expiry. These
      need no knowledge outside this module.
    * PROVEN BY THE CALLER'S ORACLE (applied only when
      ``non_actionable`` is supplied): the workflow this authority
      names is gone, its revision has moved on, or its phase is
      terminal and this action can never run there. This module
      cannot know any of that — it deliberately cannot import the
      workflow store or the Broker — so the caller that already holds
      the workflow document passes the answer in.

    FAIL-CLOSED IS THE WHOLE CONTRACT HERE. Removing authority is
    destructive and irreversible, so an entry is removed ONLY on a
    positive proof of non-actionability. The oracle must return
    EXACTLY ``True`` to delete: any exception, and any other value
    whatsoever — ``None``, a truthy string, ``1``, a truthy object —
    KEEPS the entry. Ambiguity is never resolved in favour of
    deletion. In particular a capability that is unconsumed,
    unexpired, for a live workflow at the current revision is
    actionable EVEN WHILE the Broker gate refuses it for
    ``broker_policy_digest_drift``: drift is REPAIRABLE, so deleting
    such an entry would convert a recoverable condition into
    permanent authority loss.

    Deterministic and bounded: iteration is over ``sorted`` nonces, so
    the removal set and its order depend only on the store's content,
    never on dict insertion order; the store is already hard-bounded
    at ``MAX_CAPABILITIES``. ``now`` is the caller's single injected
    clock value — this function never reads a clock of its own.
    """
    capabilities = document["capabilities"]
    stale = []
    for nonce in sorted(capabilities):
        entry = capabilities[nonce]
        if (
            entry["consumed_at"] is not None
            or now >= entry["expires_at"]
        ):
            stale.append(nonce)
            continue
        if non_actionable is None:
            continue
        try:
            verdict = non_actionable(
                entry["workflow_id"], entry["action"],
                entry["revision"],
            )
        except Exception as exc:
            # A BROKEN ORACLE PROVES NOTHING. Keep the authority — the
            # fail-closed semantic is UNCHANGED and deliberately so.
            #
            # But it must not be INVISIBLE (J2 N-1). Silently keeping
            # everything is indistinguishable from having nothing to
            # remove, so a defective oracle would regress compaction to
            # its pre-R-02 behaviour with no counter, no log and no
            # refusal — recovery would quietly stop while every test
            # and every operator surface still looked healthy. When the
            # caller passes ``oracle_errors`` the failure is recorded
            # for it to surface; the entry is kept either way.
            if oracle_errors is not None:
                oracle_errors.append((nonce, exc))
            continue
        if verdict is True:
            stale.append(nonce)
    for nonce in stale:
        del capabilities[nonce]
    return stale


def compact(directory, now, non_actionable=None, oracle_errors=None):
    """Compact the capability store; return the removed nonces sorted.

    The recovery half of R-02. ``_prune`` alone runs only as a side
    effect of ``mint``, and drops only consumed-or-expired entries, so
    authority that became non-actionable for any other reason — its
    workflow deleted, its revision superseded, its workflow terminal —
    was previously unreachable and occupied the shared bound until its
    hard expiry.

    ``non_actionable`` is the caller's oracle; see ``_prune`` for the
    fail-closed contract it must satisfy. Pass a list as
    ``oracle_errors`` to be told which entries the oracle FAILED on:
    those entries are kept, as always, but a caller that never asks
    cannot distinguish "nothing was removable" from "the oracle is
    broken and removal has silently stopped". The store is written ONLY
    when something was actually removed, so a no-op compaction leaves
    the file byte-identical.

    A malformed store raises ``CapabilityError`` out of ``_load`` and
    is NEVER silently reinitialized: an unreadable store is a
    condition to surface, not to erase.
    """
    document = _load(directory)
    removed = _prune(document, now, non_actionable, oracle_errors)
    if removed:
        _save(directory, document)
    return removed


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

    Returns ``(ok, problem, detail)``. Every refusal here — missing,
    malformed, unknown, already consumed, expired, or bound to a
    different (workflow, action, revision) — writes NOTHING, and
    leaves every other entry in the store byte-for-byte untouched. On
    success the consumption is saved durably BEFORE the caller
    performs any effect, and the caller does NOT restore it if the
    caller itself later refuses.
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
