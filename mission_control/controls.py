"""The conversational Mission controls: hold, resume, cancel (Task 8,
slice S-VII).

The desk behind the elicited ``di_mission_control`` tool. Every control is
the HUMAN's act: the relay reserves the operation id under the
CLIENT-CONFIRMATION context, asks the human through this client's own
elicitation form — the card below, confirmed by a value bound to the exact
Mission, revision, control and canonical operation — and only an admitted
accept applies the canonical slice S-V operation. A decline, a cancel of
the form or any other answer applies nothing. The model can never supply
the answer, and a connector-credential caller is refused by the Mission
Core itself (``mission_state_control_provenance``).

Controls, each resolved against the Mission's canonical control record at
card time and re-checked by the Mission Core when applied:

- ``hold`` → ``request_hold``: every Dodging Infinity effect refuses at
  every gate until resumed. An engineering session already running is NOT
  paused — the engine has no pause primitive; ``cancel`` is the stop.
- ``resume`` → ``lift_hold``: it permits nothing by itself; every later
  step re-validates the current revision, authority, contract, readiness,
  budgets and blockers at its own gate.
- ``cancel`` → ``request_cancel`` while none is recorded: sticky, never
  undone; the Runtime's owner stops what was started and records the
  observed absence. When a request exists and EVERY engagement start's
  stop is confirmed by observed absence, the SAME control CONFIRMS it
  (``confirm_cancel``; the Mission closes as abandoned by the caller). This
  desk is the production cancel-confirmation caller, and confirmation stays
  a control principal's act — the human's, client-confirmed; the Runtime
  never confirms. A stop that is not yet confirmed refuses
  (``mission_state_cancel_unconfirmed``, naming the starts) before anything
  is reserved or asked.

Stated limits: a control never undoes an effect already completed (a
pushed branch, an opened pull request, a finished engineering task); a
hold does not pause running work.
"""

from mission import record as mission_record
from mission import service as mission_service
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store
from workflow_authority.digest import text_digest

CONTROL_HOLD = "hold"
CONTROL_RESUME = "resume"
CONTROL_CANCEL = "cancel"
CONTROLS = (CONTROL_HOLD, CONTROL_RESUME, CONTROL_CANCEL)

# The canonical operation each control resolves to.
OPERATION_BY_CONTROL = {
    CONTROL_HOLD: mission_state.OPERATION_REQUEST_HOLD,
    CONTROL_RESUME: mission_state.OPERATION_LIFT_HOLD,
}
CANCEL_OPERATIONS = (mission_state.OPERATION_REQUEST_CANCEL,
                     mission_state.OPERATION_CONFIRM_CANCEL)

HOLD_REASON = "hold requested by the human through the client (client-confirmed)"
CANCEL_REASON = "cancel requested by the human through the client (client-confirmed)"
CONFIRM_DETAIL = ("cancel confirmed by the human through the client: every"
                  " engagement start's stop is confirmed by observed absence")

PROBLEM_UNKNOWN_CONTROL = "mission_control_unknown_control"
# A concurrent writer (the Runtime) can move the sequence between the read
# and the operation, exactly as a human would see: re-read and retry,
# bounded; every other refusal is reported as the core states it.
APPLY_ATTEMPTS = 8

EFFECT_TEXT = {
    mission_state.OPERATION_REQUEST_HOLD: (
        "HOLD: every Dodging Infinity effect for this Mission refuses at every"
        " gate until it is resumed. An engineering session already running is"
        " NOT paused (the engine has no pause primitive); cancel is the stop."),
    mission_state.OPERATION_LIFT_HOLD: (
        "RESUME: lift the hold. Nothing starts because of this answer: every"
        " later step re-validates the revision, authority, contract, readiness,"
        " budgets and blockers at its own gate."),
    mission_state.OPERATION_REQUEST_CANCEL: (
        "CANCEL (request): sticky and never undone. The Runtime stops what it"
        " started and records the observed absence; the cancel is CONFIRMED only"
        " afterwards, by asking you again. Nothing already completed (a pushed"
        " branch, an opened pull request) is undone."),
    mission_state.OPERATION_CONFIRM_CANCEL: (
        "CANCEL (confirm): every engagement start's stop is confirmed by observed"
        " absence; confirming closes the Mission as abandoned. Nothing already"
        " completed is undone."),
}


def _refusal(problem, detail):
    return {"ok": False, "problem": problem, "detail": detail, "card": None,
            "confirm_value": None, "binding": None}


def confirm_value_for(mission_id, revision, control, operation):
    """The 12 hex characters the human's form answer must equal: bound to
    the exact Mission, revision, control and canonical operation."""
    return text_digest("|".join((mission_id, str(revision), control,
                                 operation)))[:12]


def resolve_operation(control, controls, state_record):
    """``(operation, None)`` or ``(None, (problem, detail))`` for ``control``
    against the canonical control record."""
    if control == CONTROL_HOLD:
        if controls["cancel_requested"]:
            return None, (mission_state.PROBLEM_CONTROL_STATE,
                          "a cancel is requested; a hold is moot")
        if controls["hold_active"]:
            return None, (mission_state.PROBLEM_CONTROL_STATE,
                          "the Mission is already on hold")
        return mission_state.OPERATION_REQUEST_HOLD, None
    if control == CONTROL_RESUME:
        if controls["cancel_requested"]:
            return None, (mission_state.PROBLEM_CONTROL_STATE,
                          "a cancel is requested; resume cannot undo a cancel")
        if not controls["hold_active"]:
            return None, (mission_state.PROBLEM_CONTROL_STATE,
                          "the Mission is not on hold")
        return mission_state.OPERATION_LIFT_HOLD, None
    if control == CONTROL_CANCEL:
        if controls["cancel_confirmed"]:
            return None, (mission_state.PROBLEM_CONTROL_STATE,
                          "the cancel is already confirmed")
        if not controls["cancel_requested"]:
            return mission_state.OPERATION_REQUEST_CANCEL, None
        unresolved = mission_state.unresolved_starts(state_record or {})
        if unresolved:
            return None, (mission_state.PROBLEM_CANCEL_UNCONFIRMED,
                          "the cancel is requested; %d engagement start(s) have no"
                          " stop confirmed by observed absence yet (%s); ask again"
                          " once the Runtime has observed the absence"
                          % (len(unresolved), ", ".join(
                              "%s:%s" % (s["start_id"], s["point"])
                              for s in unresolved)))
        return mission_state.OPERATION_CONFIRM_CANCEL, None
    return None, (PROBLEM_UNKNOWN_CONTROL, "the control is not hold, resume or cancel")


def render_card(record, revision, control, operation, confirm_value):
    proposal = record["revisions"][-1]["proposal"]
    return "\n".join([
        "Dodging Infinity Mission control — confirm exactly this:",
        "",
        "MISSION %s" % record["mission_id"],
        "REVISION %d (current)" % revision,
        "OBJECTIVE %s" % proposal["objective"],
        "CONTROL %s (canonical operation: %s)" % (control, operation),
        "",
        EFFECT_TEXT[operation],
        "",
        "To confirm, answer with the value %s." % confirm_value,
    ])


class ControlDesk(object):
    """Card, reservation and application of one human control. Built by
    the production composition with the Grok side's Mission service."""

    def __init__(self, service):
        self.service = service

    def card(self, mission_id, revision, control):
        """PURE read: ``{"ok", "problem", "detail", "card", "confirm_value",
        "binding"}`` for ``control`` on the exact ``revision``."""
        if control not in CONTROLS:
            return _refusal(PROBLEM_UNKNOWN_CONTROL,
                            "the control is not hold, resume or cancel")
        stored = self.service.get(mission_id)
        record = stored["record"]
        if revision != record["current_revision"]:
            return _refusal(mission_service.PROBLEM_STALE_REVISION,
                            "revision %d is not current (the Mission is at"
                            " revision %d)" % (revision, record["current_revision"]))
        state = self.service.get_state(mission_id)
        if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
            return _refusal(mission_state.PROBLEM_PROGRESS_TERMINAL,
                            "the Mission is %s" % state["progress"])
        controls = mission_state.control_view(state["record"])
        operation, refused = resolve_operation(control, controls, state["record"])
        if refused is not None:
            return _refusal(*refused)
        confirm_value = confirm_value_for(mission_id, revision, control, operation)
        return {
            "ok": True, "problem": None, "detail": None,
            "card": render_card(record, revision, control, operation, confirm_value),
            "confirm_value": confirm_value,
            "binding": {"mission_id": mission_id, "revision": revision,
                        "control": control, "operation": operation},
        }

    def reserve(self, binding, context):
        """The durable reservation the elicitation request carries: the
        Mission's DERIVED cancel id for a cancel operation, a control
        operation id otherwise (the headroom kept for controls)."""
        if binding["operation"] in CANCEL_OPERATIONS:
            return self.service.mint_cancel_operation_id(
                binding["mission_id"], binding["operation"], context)
        return self.service.mint_control_operation_id(context)

    def apply(self, binding, operation_id, context):
        """Apply the admitted control: ``{"ok", "recorded", "problem",
        "detail", "operation", "controls"}``. ``recorded`` is True only when
        the core recorded it, False when it refused (nothing recorded)."""
        mission_id = binding["mission_id"]
        operation = binding["operation"]
        stored = self.service.get(mission_id)
        if stored["record"]["current_revision"] != binding["revision"]:
            return self._not_recorded(
                binding, mission_service.PROBLEM_STALE_REVISION,
                "the Mission moved to revision %d after the card; nothing was"
                " applied" % stored["record"]["current_revision"])
        problem = detail = None
        for _ in range(APPLY_ATTEMPTS):
            sequence = self.service.get_state(mission_id)["sequence"]
            try:
                self._call(operation, mission_id, operation_id, sequence, context)
            except mission_store.MissionStoreError as exc:
                return self._not_recorded(binding, exc.problem, str(exc))
            except mission_record.MissionError as exc:
                problem, detail = exc.problem, str(exc)
                if problem == mission_state_service.PROBLEM_STALE_SEQUENCE:
                    continue
                return self._not_recorded(binding, problem, detail)
            return {"ok": True, "recorded": True, "problem": None,
                    "detail": EFFECT_TEXT[operation], "operation": operation,
                    "controls": self.service.mission_controls(mission_id)}
        return self._not_recorded(binding, problem, detail)

    def _call(self, operation, mission_id, operation_id, sequence, context):
        service = self.service
        if operation == mission_state.OPERATION_REQUEST_HOLD:
            return service.request_hold(mission_id, operation_id, sequence,
                                        HOLD_REASON, context)
        if operation == mission_state.OPERATION_LIFT_HOLD:
            return service.lift_hold(mission_id, operation_id, sequence, context)
        if operation == mission_state.OPERATION_REQUEST_CANCEL:
            return service.request_cancel(mission_id, operation_id, sequence,
                                          CANCEL_REASON, context)
        return service.confirm_cancel(mission_id, operation_id, sequence,
                                      CONFIRM_DETAIL, context)

    def _not_recorded(self, binding, problem, detail):
        controls = None
        try:
            controls = self.service.mission_controls(binding["mission_id"])
        except (mission_record.MissionError, mission_store.MissionStoreError):
            controls = None
        return {"ok": False, "recorded": False, "problem": problem,
                "detail": detail, "operation": binding["operation"],
                "controls": controls}
