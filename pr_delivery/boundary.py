"""The provider-neutral callable boundary and the durable status projection.

A future HumanInteractionAdapter (or any other caller) drives a delivery
through three calls — ``status``, ``advance``, ``revoke`` — with plain
identifiers and strings, and reads back a plain dictionary. The caller
never sees an argv, never invokes a shell, and never learns Git: what it
gets is the answer to "is it authorized, is engineering complete, is it
verified, was the base refreshed, is it committed, is it pushed, what is
the PR URL, what blocks it, and what is the exact next action".

``project_status`` is a PURE function of the durable record and ``now``.
No transcript, no live git read, no model output is consulted on this
path; if the record does not know something, the projection says so.

Authorization itself is NOT on this boundary: the only authorization
source today is a local terminal ceremony (``cli.py``), and a remote
caller minting authority is exactly what the request forbids.
"""

from pr_delivery import authorization as auth
from pr_delivery import receipts

NEXT_ADVANCE = "ADVANCE"
NEXT_WAIT_RETRY = "WAIT_RETRY"
NEXT_RESOLVE_BLOCKER = "RESOLVE_BLOCKER"
NEXT_REVOKED = "REVOKED"
NEXT_EXPIRED = "EXPIRED"
NEXT_COMPLETE = "COMPLETE"

POST_COMMIT_REVERIFICATION_NOTE = (
    "the reverification command is not re-run after the commit; a"
    " post-commit base advance is accepted only with a recorded"
    " fast-forward, disjointness, candidate identity re-match, and base"
    " CI proof"
)


def _step_view(record, step):
    entry = record["steps"][step]
    receipt = entry["receipt"]
    return {
        "state": entry["state"],
        "receipt_id": receipt["receipt_id"] if receipt else None,
        "receipt_state": receipt["state"] if receipt else None,
        "attempts": len(entry["voided"]) + (1 if receipt else 0),
        "observed": dict(receipt["observed"]) if receipt and receipt[
            "observed"
        ] else None,
    }


def _next_action(record, now):
    phase = record["phase"]
    if phase == auth.PHASE_COMPLETE:
        return {"action": NEXT_COMPLETE, "step": None}
    if phase == auth.PHASE_REVOKED or auth.is_revoked(record):
        return {"action": NEXT_REVOKED, "step": None}
    if phase == auth.PHASE_BLOCKED:
        blocker = record["blocker"] or {}
        if blocker.get("problem") == receipts.PROBLEM_EXPIRED:
            return {"action": NEXT_EXPIRED, "step": None}
        return {"action": NEXT_RESOLVE_BLOCKER, "step": None}
    if auth.is_expired(record, now):
        return {"action": NEXT_EXPIRED, "step": None}
    step = auth.STEP_FOR_PHASE[phase]
    if record["steps"][step]["state"] == auth.STEP_FAILED_RETRYABLE:
        return {"action": NEXT_WAIT_RETRY, "step": step}
    return {"action": NEXT_ADVANCE, "step": step}


def project_status(record, now):
    """The durable status answer, from the record alone."""
    evidence = record["evidence"]
    verification = evidence["independent_verification"]
    base_refresh = record["steps"][auth.STEP_BASE_REFRESH]
    refresh_observed = (
        base_refresh["receipt"]["observed"]
        if base_refresh["receipt"] and base_refresh["receipt"]["observed"]
        else None
    )
    problem, detail = None, None
    if auth.is_revoked(record):
        problem, detail = receipts.PROBLEM_REVOKED, "revoked"
    elif auth.is_expired(record, now):
        problem, detail = receipts.PROBLEM_EXPIRED, "expired"
    commit_observed = _step_view(record, auth.STEP_COMMIT)["observed"] or {}
    push_observed = _step_view(record, auth.STEP_PUSH)["observed"] or {}
    pull_request = record["pull_request"]
    return {
        "delivery_id": record["delivery_id"],
        "revision": record["revision"],
        "phase": record["phase"],
        "authorization": {
            "valid": problem is None,
            "problem": problem,
            "detail": detail,
            "human_identity": record["human_authorization"]["identity"],
            "source": record["human_authorization"]["source"],
            "authorized_at": record["human_authorization"]["authorized_at"],
            "expires_at": record["expiration"]["expires_at"],
            "revoked": auth.is_revoked(record),
            "allowed_actions": list(record["allowed_actions"]),
            "repository_url": record["repository"]["repository_url"],
            "source_branch": record["source"]["branch"],
            "target_base_branch": record["target_base"]["branch"],
            "candidate_identity_digest_sha256": record["candidate"][
                "identity_digest_sha256"
            ],
        },
        "engineering": {
            "task_id": evidence["engineering_complete"]["task_id"],
            "status": evidence["engineering_complete"]["status"],
            "reviewer_decision": evidence["reviewer_approve"]["decision"],
            "reviewer_round": evidence["reviewer_approve"]["round"],
        },
        "verification": {
            "recorded": {
                "command_argv": list(verification["command_argv"]),
                "exit_status": verification["exit_status"],
                "log_sha256": verification["log_sha256"],
                "base_oid": verification["base_oid"],
            },
            "after_base_refresh": refresh_observed,
            "post_commit_note": POST_COMMIT_REVERIFICATION_NOTE,
        },
        "base_refresh": dict(
            _step_view(record, auth.STEP_BASE_REFRESH),
            original_base_oid=record["original_baseline"]["commit_sha"],
            current_base_oid=record["base_state"]["current_base_oid"],
            refreshed_at=record["base_state"]["refreshed_at"],
            advance_after_commit=record["base_state"]["advance_after_commit"],
        ),
        "commit": dict(_step_view(record, auth.STEP_COMMIT),
                       commit_oid=commit_observed.get("commit_oid")),
        "push": dict(_step_view(record, auth.STEP_PUSH),
                     remote_oid=push_observed.get("remote_oid")),
        "pull_request": dict(pull_request) if pull_request else None,
        "pr_url": pull_request["url"] if pull_request else None,
        "blocker": dict(record["blocker"]) if record["blocker"] else None,
        "next_action": _next_action(record, now),
    }


class PrDeliveryBoundary(object):
    """Three calls over one machine; returns projections only."""

    def __init__(self, machine):
        self.machine = machine

    def status(self, delivery_id):
        record = self.machine.load(delivery_id)
        return project_status(record, self.machine.clock())

    def advance(self, delivery_id):
        outcome = self.machine.advance(delivery_id)
        status = self.status(delivery_id)
        status["outcome"] = outcome
        return status

    def revoke(self, delivery_id, by, reason):
        self.machine.revoke(delivery_id, by, reason)
        return self.status(delivery_id)
