"""The progression pass: advance the Missions this panel started.

For the requests THIS panel durably submitted (its journal's SUBMITTED
submissions, and nothing else) that are approved and not withdrawn, call
``MissionBridge.advance`` once each and report what came back. It is the only
thing the progression runtime does to a Mission, and it adds nothing to it:

- it STARTS nothing. ``advance`` never dispatches; a Mission begins when the
  person presses Start in the panel (an explicit, authenticated act after an
  exact approval), and a pass over an approved-but-unstarted Mission does nothing;
- it DECIDES nothing. Whether a Mission is observed, reconciled, verified, held
  or left waiting is the bridge's, from the Mission's own record and one fresh
  read; a pass relays that answer and never turns waiting into success;
- it ENUMERATES only what the journal links. A request another surface made, a
  Mission proposed into Mission Core directly, a workflow record: none is read,
  written or reported here;
- it is BOUNDED. A pass examines at most ``MAX_PASS_MISSIONS`` Missions and, when
  more are eligible, rotates so every one is reached in turn and says it was
  bounded (``truncated``); a Mission seen in a terminal state is not read again
  in this process (a restart re-reads it once, which is cheap and correct);
- it CONTAINS failure. One Mission whose read raised, or whose write was
  refused, is reported as that (the class or the problem code, never a message)
  and does not stop the others or the loop.

The status the runtime publishes from these reports is
``target_runtime.mission_progress_status``.
"""

from local_request import store as request_store
from target_runtime import mission_bridge as bridge_module
from target_runtime.mission_progress_status import MAX_REPORT_DETAIL_CHARS

# The most Missions one pass examines. A pass rotates through the eligible ones
# when there are more, so none is starved.
MAX_PASS_MISSIONS = 32

ACTION_REFUSED = "refused"
STATE_UNKNOWN = "unknown"


def _eligible(entry):
    """An approved, open, unwithdrawn request: the only kind that can have a
    run. An unknown attestation (HOLD, RESERVED) is not progressed from here."""
    attested = entry.get("attested_approval")
    return (
        entry["cancellation"] is None
        and entry["surface_state"] == request_store.STATE_OPEN
        and attested is not None
        and attested["state"] == request_store.ATTESTED_APPLIED
        and entry["mission_id"] is not None)


class ProgressionPass(object):
    """``journal`` is the panel's ``PanelJournal``, ``surface`` the
    ``LocalRequestSurface`` over the same state, ``bridge`` the ``MissionBridge``
    composed with the same run guard the panel's bridge uses."""

    def __init__(self, journal, surface, bridge):
        self._journal = journal
        self._surface = surface
        self._bridge = bridge
        self._finished = set()
        self._cursor = 0

    def _eligible_requests(self):
        listing = {e["request_ref"]: e
                   for e in self._surface.list_requests()["requests"]}
        unknown = 0
        eligible = []
        seen = set()
        for ref in self._journal.submitted_requests():
            entry = listing.get(ref)
            if entry is None:
                unknown += 1
                continue
            mission_id = entry["mission_id"]
            if not _eligible(entry) or mission_id in self._finished or (
                mission_id in seen
            ):
                continue
            seen.add(mission_id)
            eligible.append((ref, mission_id))
        return eligible, unknown

    def _advance_one(self, request_ref, mission_id):
        try:
            result = self._bridge.advance(mission_id)
        except bridge_module.MissionBridgeRefusal as exc:
            return {"mission_id": mission_id, "request_ref": request_ref,
                    "state": STATE_UNKNOWN, "action": ACTION_REFUSED,
                    "waiting_for": exc.problem,
                    "detail": "the bridge refused this step (%s)" % exc.problem}
        except Exception as exc:
            return {"mission_id": mission_id, "request_ref": request_ref,
                    "state": STATE_UNKNOWN, "action": bridge_module.ADVANCE_UNREADABLE,
                    "waiting_for": None,
                    "detail": "the step failed (%s); nothing was recorded"
                    % type(exc).__name__}
        step = result["advance"]
        if result["state"] in bridge_module.TERMINAL_MISSION_STATES:
            self._finished.add(mission_id)
        return {"mission_id": mission_id, "request_ref": request_ref,
                "state": result["state"], "action": step["action"],
                "waiting_for": step["waiting_for"],
                "detail": step["detail"][:MAX_REPORT_DETAIL_CHARS]}

    def run_pass(self):
        """One pass. Returns ``{examined, eligible, truncated,
        unknown_requests, missions}``; never raises for a Mission."""
        eligible, unknown = self._eligible_requests()
        total = len(eligible)
        start = self._cursor % total if total else 0
        chosen = (eligible[start:] + eligible[:start])[:MAX_PASS_MISSIONS]
        self._cursor = (start + len(chosen)) % total if total > MAX_PASS_MISSIONS else 0
        missions = [self._advance_one(ref, mission_id) for ref, mission_id in chosen]
        return {"examined": len(chosen), "eligible": total,
                "truncated": len(chosen) < total, "unknown_requests": unknown,
                "missions": missions}
