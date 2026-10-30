"""THE OFFICE DELIVERY ADAPTER: the ONE office caller pin (6) admits (owner decision (b), 2026-10-28,
owner-decisions.md:141; the approved proposal reports/owner-delivery-bridge-premise-correction-20261029.md).

WHAT IT DOES: it reads ONE delivery's status, as pr_delivery's own projection of the durable record,
and adds the office's block:
- which next step, if any, is ELIGIBLE for a local confirmation, read from the RECORD (its next action
  and the step's recorded state), never inferred from a typical sequence;
- the binding a confirmation must carry: the displayed delivery id, revision, candidate digest and step;
- the plain label: a local check, never authority.
``check_confirmation`` is the pure check that refuses, BEFORE any effect, a confirmation for another
delivery, a changed candidate, a stale revision or a different step.

ONE CONFIRMED STEP (the expected-step supplement, owner-decisions.md:149). ``advance_once`` hands
the confirmation to pr_delivery's single-step method as its ``expected`` binding. THAT METHOD'S GUARD
IS THE PREVENTION: against the record it loads, before any branch, it refuses a step other than the
one confirmed. This adapter's own check before the call, and its re-read after, are ADDITIONAL
checks, never the prevention. The guard is not a lock, transaction isolation or an exactly-once proof.
The confirmation is compared and never written: it is not recorded in the delivery record.

VERSION BOUNDARY: the record office-delivery-b1-candidate-20261029.md describes this adapter before
the guard, when it performed no step. That description is historical.

It never mints, revokes or runs every step, and never constructs a delivery transport, machine, store
or boundary. Its machine comes from the factory it is given, and only ``production_adapter`` passes
pr_delivery's own ``cli.build_machine``, which constructs the real transport; no test calls it. It
performs no base refresh and no pull-request observation: the office's action set is exactly COMMIT,
PUSH and PR_CREATE.
"""

import copy

from pr_delivery import authorization, boundary, cli, machine

OFFICE_ACTION_STEPS = (authorization.STEP_COMMIT, authorization.STEP_PUSH, authorization.STEP_PR_CREATE)

LOCAL_CONFIRMATION_NOTE = (
    "This confirmation is a local check. The delivery was authorized once, by the human ceremony, for"
    " these steps. This confirmation is not recorded in the delivery record."
)
PERFORMS_ONE_CONFIRMED_STEP = (
    "This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh,"
    " never every step). It hands the confirmation to the delivery's own single-step method, whose"
    " expected-step guard refuses, against the record it loads and before any effect, a step other than the"
    " one confirmed. The confirmation is compared, never written: it is not recorded in the delivery record."
)
BASE_REFRESH_UNMET = (
    "The base refresh comes first. This office does not perform it, so it cannot start this delivery:"
    " the delivery's own drive does, and that drive attempts every remaining step."
)
_NOT_ELIGIBLE = {
    boundary.NEXT_COMPLETE: ("complete", "The delivery is complete; there is no next step."),
    boundary.NEXT_REVOKED: ("revoked", "The delivery's authorization was revoked; no step may run."),
    boundary.NEXT_EXPIRED: ("expired", "The delivery's authorization has expired; no step may run."),
    boundary.NEXT_RESOLVE_BLOCKER: ("blocked", "The delivery is blocked; the blocker must be resolved before any step."),
    boundary.NEXT_WAIT_RETRY: ("waiting_to_retry", "The step failed and is waiting to be retried by the delivery's own drive."),
}
_CONFIRMATION_KEYS = ("delivery_id", "revision", "candidate_identity_digest_sha256", "step")


def _not_eligible(reason, step, detail):
    return {"reason": reason, "step": step, "detail": detail}


def office_eligibility(record, projection):
    """The office's block for one record and its projection."""
    next_action = projection["next_action"]
    action, step = next_action["action"], next_action["step"]
    eligible, not_eligible = None, None
    if action in _NOT_ELIGIBLE:
        reason, detail = _NOT_ELIGIBLE[action]
        not_eligible = _not_eligible(reason, step, detail)
    elif step is None:
        not_eligible = _not_eligible(
            "observation_not_an_office_step", None,
            "The last action is the delivery's observation of the pull request, which is not one of this office's steps.",
        )
    elif step == authorization.STEP_BASE_REFRESH:
        not_eligible = _not_eligible("base_refresh_unmet", step, BASE_REFRESH_UNMET)
    elif step not in OFFICE_ACTION_STEPS:
        not_eligible = _not_eligible("not_an_office_step", step, "This step is not one of this office's steps.")
    elif record["steps"][step]["state"] == authorization.STEP_EXECUTING:
        not_eligible = _not_eligible(
            "step_recorded_executing", step,
            "The step is recorded as executing; its outcome must be reconciled by the delivery's own drive first.",
        )
    elif record["steps"][step]["state"] != authorization.STEP_PENDING:
        not_eligible = _not_eligible("step_not_pending", step, "The step is not pending in the record.")
    else:
        eligible = {
            "step": step,
            "binding": {
                "delivery_id": record["delivery_id"],
                "revision": record["revision"],
                "candidate_identity_digest_sha256": record["candidate"]["identity_digest_sha256"],
                "step": step,
            },
        }
    return {
        "eligible": eligible,
        "not_eligible": not_eligible,
        "action_steps": list(OFFICE_ACTION_STEPS),
        "local_confirmation_note": LOCAL_CONFIRMATION_NOTE,
        "performs_steps": True,
        "performs_steps_note": PERFORMS_ONE_CONFIRMED_STEP,
    }


def _refuse(refusal, detail):
    return {"admitted": False, "refusal": refusal, "detail": detail}


def check_confirmation(record, now, confirmation):
    """Admit ``confirmation`` only when it is EXACTLY the binding of the step this record makes eligible now; refuse anything else, before any effect."""
    # Exactly these keys: a SET question, so no ordering (keys of mixed types cannot be sorted, and must refuse here, never raise).
    if not isinstance(confirmation, dict) or set(confirmation) != set(_CONFIRMATION_KEYS):
        return _refuse("malformed", "A confirmation names exactly a delivery id, a revision, a candidate digest and a step.")
    if not (isinstance(confirmation["delivery_id"], str) and isinstance(confirmation["candidate_identity_digest_sha256"], str)
            and isinstance(confirmation["step"], str) and type(confirmation["revision"]) is int):
        return _refuse("malformed", "A confirmation's fields have the wrong types.")
    eligible = office_eligibility(record, boundary.project_status(record, now))["eligible"]
    if eligible is None:
        return _refuse("not_eligible", "No step of this delivery is eligible for a confirmation now.")
    expected = eligible["binding"]
    if confirmation["delivery_id"] != expected["delivery_id"]:
        return _refuse("other_delivery", "The confirmation names a different delivery.")
    if confirmation["candidate_identity_digest_sha256"] != expected["candidate_identity_digest_sha256"]:
        return _refuse("candidate_changed", "The delivery's candidate is not the one the confirmation was given for.")
    if confirmation["revision"] != expected["revision"]:
        return _refuse("stale_revision", "The delivery has changed since the confirmation was given.")
    if confirmation["step"] != expected["step"]:
        return _refuse("step_mismatch", "The confirmation is for a different step than the one eligible now.")
    return {"admitted": True, "step": expected["step"]}


class OfficeDeliveryAdapter(object):
    """Status for the office. The machine comes from ``build_machine`` and nowhere else."""

    def __init__(self, build_machine):
        if not callable(build_machine):
            raise TypeError("the office delivery adapter needs a machine factory")
        self._build_machine = build_machine

    def status(self, delivery_id):
        delivery_machine = self._build_machine()
        record = delivery_machine.load(delivery_id)
        projection = boundary.project_status(record, delivery_machine.clock())
        return {"delivery": projection, "office": office_eligibility(record, projection)}

    def advance_once(self, delivery_id, confirmation):
        """ONE confirmed step. The PREVENTION is the single-step method's expected-step guard; the check before the call and the re-read after are ADDITIONAL checks."""
        delivery_machine = self._build_machine()
        record = delivery_machine.load(delivery_id)
        admitted = check_confirmation(record, delivery_machine.clock(), confirmation)
        if not admitted["admitted"]:
            return {"advanced": False, "refusal": admitted["refusal"], "detail": admitted["detail"], "step": None, "receipt": None}
        step = admitted["step"]
        try:
            outcome = delivery_machine.advance_once(delivery_id, expected=dict(confirmation))
        except machine.MachineError as exc:
            guarded = exc.problem == machine.PROBLEM_EXPECTED_STEP_REFUSED
            return {
                "advanced": False,
                "refusal": "guard_refused" if guarded else "machine_refused",
                "problem": exc.problem,
                "detail": (
                    "Nothing ran: the delivery's single-step method refused, because the record no longer selects the confirmed step."
                    if guarded else "Nothing ran: the delivery's single-step method refused (%s)." % exc.problem
                ),
                "step": None,
                "receipt": None,
            }
        after = delivery_machine.load(delivery_id)
        entry = after["steps"][step]
        # DEFENCE, not prevention: never report as done a step the record does not show as attempted.
        if entry["state"] == authorization.STEP_PENDING and entry["receipt"] is None:
            return {"advanced": False, "refusal": "not_shown_in_record", "outcome": outcome, "step": step, "receipt": None,
                    "detail": "The record does not show the confirmed step as attempted, so it is not reported as done."}
        return {
            "advanced": outcome == machine.OUTCOME_ADVANCED,
            "outcome": outcome,
            "step": step,
            "receipt": copy.deepcopy(entry["receipt"]),
            "delivery": boundary.project_status(after, delivery_machine.clock()),
        }


def production_adapter():
    """The production composition: pr_delivery's own factory, which constructs the real transport. No test calls this."""
    return OfficeDeliveryAdapter(cli.build_machine)
