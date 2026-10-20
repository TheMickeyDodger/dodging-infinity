"""The panel's lifecycle: durable records in, a stage, honest words and the only
actions a window may offer out.

This is the ONE place the words a person reads about authority, withdrawal,
uncertainty and expiry are written, so the honesty can be pinned by tests
rather than hoped for in a renderer. A window shows ``label`` and ``detail``
as given and offers only ``actions``; the service refuses anything else again.

What the wording will not do:

- call a withdrawal a denial. The attested lane is APPROVE-ONLY and ordinary
  ``approve`` is always refused, so no authenticated decision-maker exists to
  record a denial. Withdrawing is the requester taking a pending proposal back;
  it is permanently closed, and a revision is a SEPARATE replacement record;
- present an approval as a verified person. It is an operator attestation: the
  panel relays what was typed, and DI did not verify who typed it;
- claim an exactly-once effect. A recorded start is durable INTENT written
  before the effect was requested; a start with no receipt is a HOLD that is
  reconciled and never repeated;
- offer a decision, a start or a verification the records do not support.
"""

from local_request import store as store_module
from local_request import surface as surface_module
from mission import record as mission_record


STAGES = (
    "of_record", "withdrawn", "revised", "withdrawal_incomplete",
    "approval_uncertain", "approved", "approved_elsewhere", "expired",
    "dispatch_uncertain", "dispatched", "running", "pending_proof", "verified",
    "blocked", "run_cancelled", "closed_elsewhere", "unreadable",
)
ACTIONS = ("decide", "withdraw", "check_approval", "dispatch", "observe",
           "reconcile", "verify", "result")

PROVENANCE = dict(surface_module.PROVENANCE_LABEL)

NOT_EXACTLY_ONCE = (
    "A recorded start is durable intent, written before the team was asked for."
    " It is not a guarantee that the start happened exactly once.")


def approval_valid_seconds():
    """How long an operator-attested approval stays valid: Mission Core's own
    limit, read from it and never restated here."""
    return mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS


def _short(digest):
    return digest[:12]


def _view(stage, label, detail, terminal=False, actions=(), notice=None,
          approval_expires_at=None, attested=False):
    return {
        "stage": stage, "label": label, "detail": detail, "terminal": terminal,
        "actions": list(actions), "notice": notice,
        "approval_expires_at": approval_expires_at,
        "provenance": dict(PROVENANCE) if attested else None,
        "residual_risk": (mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK
                          if attested else None),
    }


def _derive_unstarted(status, attestation, capability_held, run_available,
                      runnable_problem, now):
    """AWAITING_DECISION, or AUTHORIZED with no run intent yet."""
    mission_state = status["run"]["lifecycle_state"]
    digest = _short(status["proposal_digest_sha256"])
    revision = status["mission_observation"]["revision"]
    # An approval of UNKNOWN outcome wins whatever Mission Core now reads: the
    # answer may have been lost after Mission Core applied it, so AUTHORIZED
    # here does not say whose decision it was. The way out is to reconcile
    # against Mission Core's own record, never to apply it again.
    if attestation is not None and attestation["state"] in (
        store_module.ATTESTED_RESERVED, store_module.ATTESTED_HOLD
    ):
        return _view(
            "approval_uncertain", "Approval outcome unknown",
            "Your approval was recorded as intended, but whether Mission Core"
            " applied it is not known. It is not applied again. Check again to"
            " reconcile from Mission Core's own record.",
            actions=["check_approval"], attested=True)
    if mission_state == mission_record.STATE_AWAITING_DECISION:
        notice = None
        if attestation is not None and attestation["state"] == (
            store_module.ATTESTED_REFUSED
        ):
            notice = ("The last approval attempt was refused by Mission Core"
                      " (%s); nothing was approved."
                      % ((attestation.get("outcome") or {}).get("problem")))
        actions = ["decide"]
        if capability_held:
            actions.append("withdraw")
        elif notice is None:
            notice = ("This panel no longer holds the key to withdraw this"
                      " proposal (it is shown once and held only in memory), so"
                      " it can be decided here but not withdrawn here.")
        return _view(
            "of_record", "Waiting for your decision",
            "This proposal is of record: Mission Core holds it as revision %d"
            " with digest %s. Nothing has been approved and nothing has"
            " started. Approving is a separate step you take here by typing the"
            " word approve; the proposal can also be withdrawn."
            % (revision, digest),
            actions=actions, notice=notice)
    # AUTHORIZED, no run intent yet.
    if attestation is None or attestation["state"] != store_module.ATTESTED_APPLIED:
        label = "Approved outside this panel"
        detail = ("Mission Core records this Mission as approved by an"
                  " authenticated route outside this panel. This panel did not"
                  " approve it. Its expiry is Mission Core's to enforce.")
        expires = None
        attested = False
    else:
        expires = attestation["binding"]["expires_at"]
        if now >= expires:
            return _view(
                "expired", "Approval expired",
                "The approval lapsed before the run started. Nothing was"
                " started. Submit a new proposal to ask again.",
                terminal=True, approval_expires_at=expires, attested=True)
        label = "Approved by operator attestation, not independently verified"
        detail = ("The approval was relayed through this panel and recorded in"
                  " Mission Core as an operator attestation. DI did not verify"
                  " who typed it. It is valid until it expires; start the run"
                  " before then.")
        attested = True
    actions, notice = [], None
    if not run_available:
        notice = ("This panel was started without a run, so it cannot start the"
                  " work; the approval stands in Mission Core.")
    elif runnable_problem is not None:
        notice = "This proposal cannot start a run: %s." % runnable_problem
    else:
        actions = ["dispatch"]
    stage = "approved" if attested else "approved_elsewhere"
    return _view(stage, label, detail, actions=actions, notice=notice,
                 approval_expires_at=expires, attested=attested)


def _join(*parts):
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else None


def derive(status, lineage, now, capability_held, run_available,
           runnable_problem, verification_ready, progression=None):
    """The stage, words and permitted actions for one request, from its
    durable records. ``status`` is the surface's own ``status()``.

    ``progression`` is the probe's answer about the progression runtime
    (``state`` and ``detail``), or None when no probe is wired. While the
    runtime is passing, observing, reconciling and verifying are ITS job and are
    not offered as the ordinary flow; the explicit Start stays, and so does the
    explicit decision on a run that stopped without succeeding (the runtime
    never ends a run on its own). Whenever it is not passing, the manual
    fallback is offered and the screen says nothing is advancing automatically
    and why. The probe is evidence about a process, never about a Mission: what
    happened to a Mission is only ever its own record."""
    automatic = progression is not None and progression.get("state") == "running"
    manual_note = None
    if progression is not None and not automatic:
        manual_note = (
            "Nothing is advancing this Mission automatically right now: %s."
            " You can advance it by hand here." % progression.get("detail"))
    if status["mission_observation"] is None or status.get("run") is None:
        return _view(
            "unreadable", "This proposal's record cannot be read",
            "Mission Core's record could not be read (%s), so nothing is shown"
            " as current and no action is offered."
            % status.get("mission_observation_problem"))
    if status["surface_state"] == store_module.STATE_CANCELLED:
        if lineage.get("superseded_by"):
            return _view(
                "revised", "Revised: replaced by %s" % lineage["superseded_by"],
                "This pending proposal was withdrawn and replaced by a new"
                " proposal, %s. The replacement is a separate record; nothing"
                " was edited in place, and this proposal is permanently closed."
                % lineage["superseded_by"], terminal=True)
        return _view(
            "withdrawn", "Withdrawn",
            "This pending proposal was withdrawn and is permanently closed."
            " Withdrawing records only that the requester took the proposal"
            " back. It is not a decision: no one with authenticated authority"
            " was involved.", terminal=True)
    if status.get("local_cancellation") == "INCOMPLETE":
        return _view(
            "withdrawal_incomplete", "Withdrawal incomplete",
            "Mission Core recorded the withdrawal, but this panel's own record"
            " of it did not finish. No decision can be taken on this proposal."
            " Withdraw again to complete it.",
            actions=["withdraw"] if capability_held else [])
    run = status["run"]
    attestation = status.get("attested_approval")
    mission_state = run["lifecycle_state"]
    phase = run["phase"]
    if mission_state in (mission_record.STATE_AWAITING_DECISION,
                         mission_record.STATE_AUTHORIZED) and phase in (
        surface_module.RUN_PHASE_NOT_STARTED, surface_module.RUN_PHASE_NOT_DISPATCHED
    ):
        return _derive_unstarted(status, attestation, capability_held,
                                 run_available, runnable_problem, now)
    attested = attestation is not None and attestation["state"] == (
        store_module.ATTESTED_APPLIED)
    notice = None
    if run.get("paused"):
        notice = ("Paused: only DI's own progression is paused. The team's work"
                  " is not suspended and may keep running and writing to its"
                  " own workspace.")
    if phase in (surface_module.RUN_PHASE_HOLD_OUTCOME_UNKNOWN,
                 surface_module.RUN_PHASE_HOLD_IDENTITY_UNKNOWN):
        return _view(
            "dispatch_uncertain", "Start outcome unknown",
            "DI recorded its intent to start the run before asking for it, but"
            " has no receipt that the team started. It is not started again,"
            " because starting twice could do the work twice. %s %s"
            % (NOT_EXACTLY_ONCE,
               "The progression runtime is checking Herdr's own records"
               " automatically, a bounded number of times, and never starts the"
               " work again." if automatic else
               "Check again to reconcile from Herdr's own records."),
            actions=[] if automatic else ["reconcile"],
            notice=_join(notice, manual_note), attested=attested)
    if phase == surface_module.RUN_PHASE_DISPATCHED:
        return _view(
            "dispatched", "Start requested",
            "A start was recorded and a team was asked for. DI has not yet"
            " observed it running. %s%s"
            % (NOT_EXACTLY_ONCE,
               " The progression runtime observes it automatically."
               if automatic else ""),
            actions=[] if automatic else ["observe"],
            notice=_join(notice, manual_note), attested=attested)
    if phase in (surface_module.RUN_PHASE_RUNNING, surface_module.RUN_PHASE_PENDING_PROOF):
        # Verification is offered once its OUTCOME is determined: the target
        # has stopped. Earlier it would stop the run on the first unmet
        # condition (the team still working), which is never offered.
        ready = verification_ready is not None and (
            verification_ready.get("ready") is True)
        stopped = verification_ready is not None and (
            verification_ready.get("target_stopped") is True)
        pending = phase == surface_module.RUN_PHASE_PENDING_PROOF
        offered = verification_ready is not None and (ready or stopped)
        extra = None
        if automatic and not pending:
            # The runtime records a verification the moment every condition
            # holds. What stays a person's is ending a run that stopped without
            # succeeding: nothing here ever does that on its own.
            offered = stopped and not ready
            if ready:
                extra = ("Every condition holds. The progression runtime records"
                         " the verification on its next pass.")
            elif offered:
                extra = ("DI's read says this run would not verify (first unmet"
                         " condition: %s). The progression runtime never ends a"
                         " run on its own; recording DI's decision stops it."
                         % verification_ready.get("waiting_for"))
            elif verification_ready is not None:
                extra = ("The progression runtime is advancing this run. Waiting"
                         " for %s." % verification_ready.get("waiting_for"))
        elif verification_ready is not None and not offered:
            extra = ("Verification is not offered yet: waiting for %s."
                     % verification_ready.get("waiting_for"))
        elif offered and not ready:
            extra = ("DI's read says this run would not verify (first unmet"
                     " condition: %s). Recording DI's decision stops the run."
                     % verification_ready.get("waiting_for"))
        if pending and automatic:
            extra = _join(extra, "The progression runtime does not retry a"
                          " verification that is waiting on proof: meeting the"
                          " proof and verifying again is yours.")
        notice = _join(notice, extra, manual_note)
        return _view(
            "pending_proof" if pending else "running",
            "Verification is waiting on proof" if pending else "Team working",
            ("DI verified that the run stopped but the approved completion"
             " contract is not yet satisfied. Nothing is delivered."
             if pending else
             "DI observed the team running. Recorded observations are history,"
             " not a claim about this moment; the world shows the team live."
             " DI decides verification itself from a fresh read, and the team's"
             " own review approval is evidence the team produced, not"
             " independent verification."),
            actions=["verify"] if offered else [], notice=notice, attested=attested)
    if phase == surface_module.RUN_PHASE_COMPLETED:
        return _view(
            "verified", "Verified result",
            "DI verified this run from a fresh read and recorded the result."
            " The result is not delivered: nothing was pushed, merged or"
            " released, and delivery stays a separate, human-controlled step.",
            terminal=True, actions=["result"], notice=notice, attested=attested)
    if phase == surface_module.RUN_PHASE_BLOCKED:
        return _view(
            "blocked", "Run stopped",
            "DI stopped this run and it is permanently closed. Reason: %s."
            % run.get("outcome"), terminal=True, notice=notice, attested=attested)
    if phase == surface_module.RUN_PHASE_CANCELLED:
        return _view(
            "run_cancelled", "Run cancelled",
            "This run was cancelled and is permanently closed.",
            terminal=True, notice=notice, attested=attested)
    return _view(
        "closed_elsewhere", "Closed in Mission Core",
        "Mission Core records this Mission as %s. This panel did not do that."
        % mission_state, terminal=True, notice=notice)
