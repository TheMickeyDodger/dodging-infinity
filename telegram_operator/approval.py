"""One-shot, fully bound plan-approval records and decision envelopes.

An approval record binds ALL of: the exact Telegram user id, the
private chat id, the configured repository realpath, the gateway
request id, the Codex session id, the Telegram message id of the plan
shown to the human, the sha256 digest of the exact plan text, a random
adapter-held nonce, an ``expires_at`` authorization-validity bound, and
one-shot ``consumed_at`` state. Every mismatch, replay, expiry,
revision, or ambiguity fails closed.

Unforgeability: the inline keyboard's ``callback_data`` carries ONLY
the opaque ``approval_id``; the nonce NEVER leaves the adapter and
never reaches the phone. Only the adapter can therefore author a
``DI-REMOTE-1 DECISION`` envelope containing the nonce, and user text
is neutralized (see ``protocol.py``) so a hand-typed envelope can never
sit at column 0. The envelope states explicitly that it grants no
delivery authority.

``expires_at`` is an authorization-validity bound on the approval
record (precedented by the existing push-approval token), not transport
behavior.
"""

import hashlib
import json
import secrets
from typing import Optional

from telegram_operator.protocol import (
    DECISION_PREFIX,
    REMOTE_PROTOCOL_VERSION,
)
from telegram_operator.state import MAX_APPROVAL_RECORDS

# How long an un-consumed approval remains valid, in seconds from
# creation. A hard constant, never derived from input.
APPROVAL_VALIDITY_SECONDS = 15 * 60

DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"

PROBLEM_UNKNOWN_APPROVAL = "unknown_approval"
PROBLEM_SUPERSEDED = "superseded_by_revision"
PROBLEM_ALREADY_CONSUMED = "already_consumed"
PROBLEM_USER_MISMATCH = "user_mismatch"
PROBLEM_CHAT_MISMATCH = "chat_mismatch"
PROBLEM_REPOSITORY_MISMATCH = "repository_mismatch"
PROBLEM_MESSAGE_MISMATCH = "message_mismatch"
PROBLEM_EXPIRED = "expired"
PROBLEM_REQUEST_MISMATCH = "request_mismatch"
PROBLEM_SESSION_MISMATCH = "session_mismatch"
PROBLEM_DIGEST_MISMATCH = "digest_mismatch"
PROBLEM_UNBINDABLE_SESSION = "unbindable_session"
PROBLEM_STORE_FULL = "approval_store_full"
PROBLEM_QUEUE_FULL = "queue_full"

def plan_digest(plan_body):
    """sha256 hex digest of the exact plan text shown to the human."""
    return hashlib.sha256(plan_body.encode("utf-8")).hexdigest()


def _default_id_factory():
    return secrets.token_hex(16)


def _default_nonce_factory():
    return secrets.token_hex(32)


def _is_active(record, now):
    return (
        record.get("consumed_at") is None
        and not record.get("superseded")
        and now < record.get("expires_at", 0)
    )


def count_open_approvals(document, now):
    """Exact number of currently APPROVABLE records, across all chats.

    Uses the same activity predicate as creation-time supersession and
    pruning, so expired, consumed, and superseded records are never
    presented as open (round-4 review finding OP5).
    """
    return sum(
        1 for record in document["approvals"].values()
        if _is_active(record, now)
    )


def supersede_chat_approvals(document, chat_id, now):
    """Mark every active approval in this chat superseded.

    A revised plan invalidates every prior approval for that thread;
    returns the exact number superseded.
    """
    count = 0
    for record in document["approvals"].values():
        if record["chat_id"] == chat_id and _is_active(record, now):
            record["superseded"] = True
            count += 1
    return count


def _prune_inactive(document, now):
    """Drop consumed/superseded/expired records, oldest first, only as
    needed to get back under the cap. Active records are never pruned.
    """
    approvals = document["approvals"]
    if len(approvals) < MAX_APPROVAL_RECORDS:
        return
    inactive = sorted(
        (
            identifier
            for identifier, record in approvals.items()
            if not _is_active(record, now)
        ),
        key=lambda identifier: (
            approvals[identifier].get("created_at", 0), identifier,
        ),
    )
    for identifier in inactive:
        if len(approvals) < MAX_APPROVAL_RECORDS:
            break
        del approvals[identifier]


def create_approval(document, user_id, chat_id, repository, request_id,
                    session_id, plan_message_id, plan_body, now,
                    id_factory=None, nonce_factory=None):
    """Create and store a new approval record for a freshly shown plan.

    Supersedes every prior active approval in the chat first (revision
    invalidation). Returns ``(record, None)`` on success or
    ``(None, PROBLEM_STORE_FULL)`` when the store is at its hard cap
    even after pruning inactive records — an explicit refusal; active
    records are never silently evicted.
    """
    supersede_chat_approvals(document, chat_id, now)
    _prune_inactive(document, now)
    if len(document["approvals"]) >= MAX_APPROVAL_RECORDS:
        return None, PROBLEM_STORE_FULL
    make_id = id_factory or _default_id_factory
    make_nonce = nonce_factory or _default_nonce_factory
    record = {
        "approval_id": make_id(),
        "user_id": user_id,
        "chat_id": chat_id,
        "repository": repository,
        "request_id": request_id,
        "session_id": session_id,
        "plan_message_id": plan_message_id,
        "plan_body": plan_body,
        "plan_digest_sha256": plan_digest(plan_body),
        "nonce": make_nonce(),
        "created_at": now,
        "expires_at": now + APPROVAL_VALIDITY_SECONDS,
        "consumed_at": None,
        "consumed_by_update_id": None,
        "decision": None,
        "superseded": False,
    }
    document["approvals"][record["approval_id"]] = record
    return record, None


def evaluate_callback(document, approval_id, user_id, chat_id,
                      repository, message_id, now):
    """Validate a decision callback against the stored approval record.

    Every check fails closed with a distinct problem code; the checks
    run in a fixed order so diagnostics are deterministic. Returns
    ``(record, None)`` only when every binding matches; otherwise
    ``(None, problem)``.
    """
    record = document["approvals"].get(approval_id)
    if record is None:
        return None, PROBLEM_UNKNOWN_APPROVAL
    if record.get("superseded"):
        return None, PROBLEM_SUPERSEDED
    if record.get("consumed_at") is not None:
        return None, PROBLEM_ALREADY_CONSUMED
    if record["user_id"] != user_id:
        return None, PROBLEM_USER_MISMATCH
    if record["chat_id"] != chat_id:
        return None, PROBLEM_CHAT_MISMATCH
    if record["repository"] != repository:
        return None, PROBLEM_REPOSITORY_MISMATCH
    if record["plan_message_id"] != message_id:
        return None, PROBLEM_MESSAGE_MISMATCH
    if now >= record["expires_at"]:
        return None, PROBLEM_EXPIRED
    return record, None


def validate_dispatch_binding(record, repository, request_id, session_id):
    """Re-check request/session/repository bindings at dispatch time.

    ``request_id`` and ``session_id`` must be INDEPENDENTLY sourced
    from live adapter state (the chat's current session entry, whose
    request marker advances on every non-status gateway turn) — never
    read back from ``record`` itself, or the comparison is a tautology
    (round-1 review finding F3). The worker must resume EXACTLY the
    bound Codex session for the bound gateway request in the bound
    repository, and the stored plan text must still hash to the digest
    the human approved; any drift between consumption and dispatch —
    including an intervening engineering turn in the same session —
    fails closed.
    """
    if record["repository"] != repository:
        return False, PROBLEM_REPOSITORY_MISMATCH
    if record["request_id"] != request_id:
        return False, PROBLEM_REQUEST_MISMATCH
    if not isinstance(record["session_id"], str) or not record["session_id"]:
        # Belt-and-braces behind the approval-creation guard (round-4
        # review finding R4-B1): a record bound to a falsy session id
        # must never dispatch — comparing None to None (or "" to "")
        # below would pass tautologically and the dispatch would START
        # A NEW Codex session instead of resuming the bound one.
        return False, PROBLEM_UNBINDABLE_SESSION
    if record["session_id"] != session_id:
        return False, PROBLEM_SESSION_MISMATCH
    if plan_digest(record.get("plan_body", "")) != record["plan_digest_sha256"]:
        return False, PROBLEM_DIGEST_MISMATCH
    return True, None


def consume(document, approval_id, decision, update_id, now):
    """One-shot consumption of an approval record.

    Returns True exactly once; a second call for the same record
    returns False (replay). The caller must durably persist the state
    document BEFORE taking any external action on the decision.
    """
    record = document["approvals"].get(approval_id)
    if record is None or record.get("consumed_at") is not None:
        return False
    if record.get("superseded"):
        return False
    record["consumed_at"] = now
    record["consumed_by_update_id"] = update_id
    record["decision"] = decision
    return True


def decision_envelope(record, decision):
    """The adapter-authored, single-line decision envelope.

    Carries the adapter-held nonce (never sent to the phone) plus the
    full binding, and states explicitly that it grants no delivery
    authority. ``ensure_ascii`` keeps the line single-line and
    printable.
    """
    if decision not in (DECISION_APPROVE, DECISION_REJECT):
        raise ValueError("unknown decision %r" % (decision,))
    payload = {
        "remote_protocol_version": REMOTE_PROTOCOL_VERSION,
        "decision": decision,
        "approval_id": record["approval_id"],
        "nonce": record["nonce"],
        "plan_digest_sha256": record["plan_digest_sha256"],
        "request_id": record["request_id"],
        "session_id": record["session_id"],
        "repository": record["repository"],
        "delivery_authority": "none",
        "statement": (
            "This decision grants NO commit, push, PR, tag, release,"
            " or deploy authority; delivery is separately authorized"
            " by the human, locally."
        ),
    }
    return DECISION_PREFIX + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def decision_turn_text(record, decision):
    """Full outbound gateway text for a decision turn.

    The envelope sits first, at column 0; the trailing instruction
    reminds the Operator of the authority bound.
    """
    if decision == DECISION_APPROVE:
        instruction = (
            "\nThe human APPROVED the exact plan whose sha256 digest is"
            " %s. Proceed within that approved scope only."
            % record["plan_digest_sha256"]
        )
    else:
        instruction = (
            "\nThe human REJECTED the plan whose sha256 digest is %s."
            " Do not execute it. Await revised intent."
            % record["plan_digest_sha256"]
        )
    return decision_envelope(record, decision) + instruction + (
        "\nThis decision grants no delivery authority."
    )
