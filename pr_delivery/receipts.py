"""One-shot step receipts: derivation, live validation, and the guard path.

A receipt is the ONLY thing a git hook ever accepts from this package —
never the broad parent authorization. It is derived by trusted system
logic (``machine.py``) from a still-valid parent after every
precondition holds, it binds the exact live facts of one step, and it
permits one execution attempt: once HEAD or the remote ref has moved,
the same receipt can never match again, so replay refuses by binding.

``guard_decision`` is what ``herdr.guards`` calls. It is READ-ONLY over
the delivery store and it is written for the hook's situation (Lead M1):
every failure — the store missing, unreadable, group-readable, corrupt,
ambiguous, or simply holding no executing receipt for this repository
and step — becomes a ``(False, reason)`` refusal, never an exception.
Import cost is deliberately small: this module, the schema module, the
store module, and ``workflow_authority.digest``/``store``; no transport,
no subprocess.

Identity lives in the closed ``*_RECEIPT_BINDING_FIELDS`` tuples of
``authorization.py``; ``LIVE_FACT_FIELDS`` names, per step, the subset a
hook can observe and compare (the rest — expected tree, committer,
message, title/body digests — are compared by the machine, which has the
transport). Both subsets are stated rather than implied.
"""

import os
import secrets

from pr_delivery import authorization as auth
from pr_delivery import store as store_module

# The binding fields a git hook observes for itself, per step. Everything
# in the tuple is compared on the guard path; the remaining binding
# fields are compared by the machine.
LIVE_FACT_FIELDS = {
    auth.STEP_COMMIT: (
        "repository_realpath", "git_dir_realpath", "branch", "source_ref",
        "head_before", "staged_sha256",
    ),
    auth.STEP_BASE_REFRESH: (
        "repository_realpath", "source_ref", "old_base_oid", "new_base_oid",
    ),
    auth.STEP_PUSH: (
        "repository_realpath", "remote_name", "remote_url_exact",
        "remote_url_push", "source_ref", "source_commit",
        "destination_ref", "expected_remote_old_oid",
    ),
}

PROBLEM_STEP_NOT_ALLOWED = "pr_delivery_step_not_allowed"
PROBLEM_PHASE_FORBIDS_STEP = "pr_delivery_phase_forbids_step"
PROBLEM_EXPIRED = "pr_delivery_expired"
PROBLEM_REVOKED = "pr_delivery_revoked"
PROBLEM_ATTEMPTS_EXHAUSTED = "pr_delivery_attempts_exhausted"
PROBLEM_RECEIPT_AMBIGUOUS = "pr_delivery_receipt_ambiguous"
PROBLEM_RECEIPT_BINDING = "pr_delivery_receipt_binding_mismatch"
PROBLEM_RECEIPT_STATE = "pr_delivery_receipt_state"
PROBLEM_EVIDENCE_STALE = "pr_delivery_evidence_stale"


class ReceiptError(Exception):
    def __init__(self, message, problem):
        super(ReceiptError, self).__init__(message)
        self.problem = problem


def precondition_problem(record, step, now):
    """The first reason a receipt for ``step`` may not be derived now,
    as ``(problem, detail)``, or ``(None, None)``.

    Every check is against the durable record: allowed action set, phase,
    expiry, revocation, attempt bound, and the three evidence references
    being bound to this record's exact candidate identity and its
    ORIGINAL base (a later base refresh re-verifies and records its own
    result on the BASE_REFRESH receipt; the original evidence stays bound
    to the original base, which is what "recorded" means).
    """
    if step not in record["allowed_actions"]:
        return (
            PROBLEM_STEP_NOT_ALLOWED,
            "%s is not in the authorized action set %s"
            % (step, record["allowed_actions"]),
        )
    if auth.STEP_FOR_PHASE.get(record["phase"]) != step:
        return (
            PROBLEM_PHASE_FORBIDS_STEP,
            "phase %s does not permit %s" % (record["phase"], step),
        )
    if auth.is_revoked(record):
        return PROBLEM_REVOKED, "the authorization was revoked"
    if auth.is_expired(record, now):
        return (
            PROBLEM_EXPIRED,
            "the authorization expired at %r" % (
                record["expiration"]["expires_at"],
            ),
        )
    entry = record["steps"][step]
    attempts = len(entry["voided"]) + (1 if entry["receipt"] else 0)
    if attempts >= auth.MAX_STEP_ATTEMPTS:
        return (
            PROBLEM_ATTEMPTS_EXHAUSTED,
            "%s already has %d attempts; the bound is %d"
            % (step, attempts, auth.MAX_STEP_ATTEMPTS),
        )
    digest = record["candidate"]["identity_digest_sha256"]
    original = record["original_baseline"]["commit_sha"]
    for name, item in record["evidence"].items():
        if item["candidate_identity_digest_sha256"] != digest:
            return (
                PROBLEM_EVIDENCE_STALE,
                "%s is bound to another candidate identity" % name,
            )
        if item["base_oid"] != original:
            return (
                PROBLEM_EVIDENCE_STALE,
                "%s was recorded against base %s, not the approved"
                " baseline %s" % (name, item["base_oid"], original),
            )
    return None, None


def derive(record, step, binding, now):
    """Mint one receipt in state ``derived``. Raises ReceiptError when a
    precondition fails; never mutates ``record``."""
    problem, detail = precondition_problem(record, step, now)
    if problem is not None:
        raise ReceiptError(detail, problem)
    fields = auth.RECEIPT_BINDING_FIELDS[step]
    if set(binding) != set(fields):
        raise ReceiptError(
            "binding for %s must carry exactly %s" % (step, fields),
            PROBLEM_RECEIPT_BINDING,
        )
    entry = record["steps"][step]
    receipt = {
        "receipt_id": "rcpt-" + secrets.token_hex(12),
        "step": step,
        "delivery_id": record["delivery_id"],
        "parent_authority_digest_sha256": record["authority_digest_sha256"],
        "derived_at": now,
        "attempt": len(entry["voided"]) + 1,
        "state": auth.RECEIPT_DERIVED,
        "binding": dict(binding),
        "observed": None,
        "receipt_digest_sha256": "0" * 64,
    }
    receipt["receipt_digest_sha256"] = auth.receipt_digest(receipt)
    auth.validate_receipt(receipt, step, record["delivery_id"],
                          record["authority_digest_sha256"], "receipt")
    return receipt


def binding_mismatches(receipt, live, fields):
    """Every ``(field, expected, actual)`` where the live value differs.
    A field missing from ``live`` is a mismatch, never a pass."""
    problems = []
    for field in fields:
        expected = receipt["binding"][field]
        if field not in live or live[field] != expected:
            problems.append((field, expected, live.get(field)))
    return problems


def _executing_receipts_for(document, repo_root, step):
    """Records for this repository holding exactly an executing receipt
    for ``step``: ``[(record, receipt)]``."""
    found = []
    for record in document["deliveries"].values():
        if record["repository"]["realpath"] != repo_root:
            continue
        if record["phase"] in auth.TERMINAL_PHASES:
            continue
        entry = record["steps"][step]
        receipt = entry["receipt"]
        if (
            entry["state"] == auth.STEP_EXECUTING
            and receipt is not None
            and receipt["state"] == auth.RECEIPT_EXECUTING
        ):
            found.append((record, receipt))
    return found


def guard_decision(repo_root, step, live, now, store_directory=None):
    """The git-hook decision for one step: ``(ok, reason)``.

    ``live`` carries the hook-observed facts keyed by binding field name
    (see ``LIVE_FACT_FIELDS``). Read-only. Never raises: a store that
    cannot be located, opened, or parsed is a refusal with the reason in
    the message.
    """
    try:
        if step not in LIVE_FACT_FIELDS:
            return False, "delivery receipts do not cover step %r" % (step,)
        realpath = os.path.realpath(str(repo_root))
        directory = store_directory or store_module.store_directory()
        document = store_module.DeliveryStore(directory).load()
        found = _executing_receipts_for(document, realpath, step)
        if not found:
            return (
                False,
                "no executing PR delivery receipt for %s in %s"
                % (step, realpath),
            )
        if len(found) > 1:
            return (
                False,
                "%d executing PR delivery receipts for %s in %s; ambiguous,"
                " refusing (%s)" % (
                    len(found), step, realpath, PROBLEM_RECEIPT_AMBIGUOUS,
                ),
            )
        record, receipt = found[0]
        if auth.is_revoked(record):
            return False, "PR delivery %s is revoked" % record["delivery_id"]
        if auth.is_expired(record, now):
            return False, "PR delivery %s has expired" % record["delivery_id"]
        if step not in record["allowed_actions"]:
            return False, "%s is not an allowed action" % step
        mismatches = binding_mismatches(receipt, live,
                                        LIVE_FACT_FIELDS[step])
        if mismatches:
            field, expected, actual = mismatches[0]
            return (
                False,
                "receipt %s binding `%s` is %r but the live value is %r"
                " (%s)" % (
                    receipt["receipt_id"], field, expected, actual,
                    PROBLEM_RECEIPT_BINDING,
                ),
            )
        return True, "PR delivery receipt %s (%s)" % (
            receipt["receipt_id"], step,
        )
    except Exception as exc:  # the hook must never see a traceback
        return (
            False,
            "PR delivery receipt path unavailable (%s: %s)"
            % (type(exc).__name__, str(exc)[:300]),
        )
