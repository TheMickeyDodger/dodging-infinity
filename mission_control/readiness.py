"""The engineering-runtime readiness observation producer (Task 8, slice
S-VII).

A Mission's proof contract may require the resource
``engineering_runtime``: the local engineering Runtime (``dirun``) running
on this host, able to take the Mission's engineering work. This module is
the ONE production writer of that readiness fact. It probes the Runtime's
single-instance lock NON-DESTRUCTIVELY (``probe_runtime``: observed
contention on the lock — a live process holds it — means running; a lock
file that is missing or unheld means not running; a probe that cannot tell
— the file cannot be opened, or ``flock`` fails with anything but
contention — means UNKNOWN, each with an actionable detail naming the
errno) and records the answer through the canonical
``observe_resource_readiness`` operation — READY, NOT_READY or UNKNOWN
exactly as probed, never invented, never inferred from a stored approval or
a label, and never READY without observed contention.

Callers:

- the dispatch bootstrap (``mission_control.engineering.MissionControl``),
  immediately before its readiness check, when the required resource is
  not fresh — so a human's dispatch request is answered from a fresh probe
  of the Runtime instead of a stale observation;
- the Runtime pass (``target_runtime.runtime.refresh_mission_readiness``),
  for every live Mission-origin workflow whose Mission's current contract
  requires the resource, only when the latest observation is missing, not
  READY, or older than ``1 / REFRESH_DIVISOR`` of its bound — so a long
  engagement keeps fresh readiness at the spawn, completion and delivery
  boundaries without one write per pass.

Stated limits: the probe proves a live process holds the Runtime's lock, not
that every downstream engine is healthy (each effect boundary keeps its own
refusals); each observation spends one state-operation reservation and one
of the Mission's bounded readiness observations, so writes are throttled to
at most ``REFRESH_DIVISOR`` per bound per Mission while the Runtime runs.
Nothing here performs, dispatches, stops or signals anything.
"""

import fcntl
import os

from mission import record as mission_record
from mission import state as mission_state
from mission import state_service as mission_state_service
from mission import store as mission_store

ENGINEERING_RUNTIME_RESOURCE = "engineering_runtime"
# The Runtime's single-instance lock file — the Runtime OWNS the name
# (``telegram_operator.state.RUNTIME_LOCK_FILE_NAME``); this module's import
# roots exclude that package, so the value is duplicated here and pinned
# EQUAL to the owner's by tests/test_static.py and
# tests/test_grok_mission_loop.py (``ReadinessProbeTests``), whose loop
# composition holds the lock through the Runtime's own
# ``target_runtime.cli.acquire_runtime_lock``.
RUNTIME_LOCK_FILE_NAME = "runtime.lock"
# Refresh an observation once it is older than bound / REFRESH_DIVISOR.
REFRESH_DIVISOR = 2
# A canonical write can race another writer: re-read the sequence and
# retry, bounded.
REFRESH_ATTEMPTS = 3


def _errno_detail(exc):
    """``<Class> errno <n> (<strerror>)`` — the OSError named exactly."""
    number = getattr(exc, "errno", None)
    if isinstance(number, int):
        return "%s errno %d (%s)" % (type(exc).__name__, number, os.strerror(number))
    return type(exc).__name__


def probe_runtime(state_directory):
    """``(running, detail)`` for the engineering Runtime whose state lives in
    ``state_directory``: ``running`` is True ONLY when the probe observed
    CONTENTION on the Runtime's lock (another process holds it), False when
    the lock is provably not held (the file is missing, or the probe itself
    took and released it), and None when the probe could not tell (the lock
    file could not be opened, or ``flock`` failed with anything but
    contention — unsupported, an I/O error, interrupted, ...), with the
    errno named. Readiness is never asserted from a probe that did not
    observe contention (Task 8 S-VII, Lead gate F-S7-3).

    Contention is ``flock``'s EWOULDBLOCK (== EAGAIN), which Python raises
    as ``BlockingIOError``; EACCES is the contention errno of ``fcntl``
    RECORD locks, which this probe does not use, so it is not contention
    here. Non-destructive: the lock file is never created; a held lock is
    left alone; an unheld one is released immediately. Never blocks."""
    lock_path = os.path.join(state_directory, RUNTIME_LOCK_FILE_NAME)
    try:
        descriptor = os.open(lock_path, os.O_RDWR)
    except FileNotFoundError:
        return False, ("the engineering Runtime has never run in %s (no %s):"
                       " start the Runtime service before dispatching"
                       % (state_directory, RUNTIME_LOCK_FILE_NAME))
    except OSError as exc:
        return None, ("the engineering Runtime lock %s could not be probed (open"
                      " failed: %s); readiness is unknown" % (lock_path,
                                                               _errno_detail(exc)))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True, "the engineering Runtime is running (its lock is held)"
        except OSError as exc:
            return None, ("the engineering Runtime lock %s could not be probed"
                          " (flock failed: %s, not contention); readiness is"
                          " unknown" % (lock_path, _errno_detail(exc)))
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False, ("the engineering Runtime is not running (its lock %s is"
                       " not held): start the Runtime service" % lock_path)
    finally:
        os.close(descriptor)


def required_bound(stored):
    """The ``max_age_seconds`` the current revision's proof contract binds
    for ``engineering_runtime``, or None when it is not required."""
    proposal = stored["record"]["revisions"][-1]["proposal"]
    contract = proposal.get("proof_contract") or {}
    for required in contract.get("required_resource_readiness") or []:
        if required["resource_key"] == ENGINEERING_RUNTIME_RESOURCE:
            return required["max_age_seconds"]
    return None


def latest_observation(state_record):
    """The latest recorded ``engineering_runtime`` observation, or None."""
    latest = None
    for observation in (state_record or {}).get("resource_readiness") or []:
        if observation["resource_key"] == ENGINEERING_RUNTIME_RESOURCE:
            latest = observation
    return latest


def refresh_due(stored, state, now):
    """Whether a fresh observation should be recorded now: the resource is
    required by the current revision's contract, that contract is the
    activated one, the Mission is not terminal, and the latest observation
    is missing, not READY, or older than bound / REFRESH_DIVISOR."""
    bound = required_bound(stored)
    if bound is None:
        return False
    if state["progress"] in mission_state.TERMINAL_PROGRESS_STATES:
        return False
    contract = state["contract"]
    if not contract["active"] or (
        contract["revision"] != stored["record"]["current_revision"]
    ):
        return False
    latest = latest_observation(state["record"])
    if latest is None or latest["status"] != mission_state.READINESS_READY:
        return True
    return not 0 <= now - latest["observed_at"] <= bound // REFRESH_DIVISOR


class RuntimeReadinessProducer(object):
    """Probe the engineering Runtime and record the canonical readiness
    observation for one Mission. ``context`` is the caller's authenticated
    context (the Runtime's local process context, or the dispatching
    caller's); ``probe`` is ``probe_runtime`` in production."""

    def __init__(self, service, state_directory, probe=None):
        self._service = service
        self._state_directory = state_directory
        self._probe = probe or probe_runtime

    def observe(self, mission_id, context):
        """Probe and record ONE observation at the current sequence (bounded
        retry on a concurrent write). Returns ``{"recorded", "status",
        "detail", "problem"}``; raises nothing for a Mission refusal."""
        running, detail = self._probe(self._state_directory)
        # Only an observed contention is READY; a probe that could not tell
        # records UNKNOWN (fail closed: never READY, never a false absence).
        status = (mission_state.READINESS_READY if running is True
                  else mission_state.READINESS_UNKNOWN if running is None
                  else mission_state.READINESS_NOT_READY)
        problem = None
        for _ in range(REFRESH_ATTEMPTS):
            try:
                operation_id = self._service.mint_state_operation_id(context)
                sequence = self._service.get_state(mission_id)["sequence"]
                self._service.observe_resource_readiness(
                    mission_id, operation_id, sequence,
                    ENGINEERING_RUNTIME_RESOURCE, status, self._service.now(),
                    context)
                return {"recorded": True, "status": status, "detail": detail,
                        "problem": None}
            except mission_store.MissionStoreError as exc:
                return {"recorded": False, "status": status, "detail": str(exc),
                        "problem": exc.problem}
            except mission_record.MissionError as exc:
                problem = exc.problem
                if problem != mission_state_service.PROBLEM_STALE_SEQUENCE:
                    return {"recorded": False, "status": status,
                            "detail": str(exc), "problem": problem}
        return {"recorded": False, "status": status,
                "detail": "the Mission sequence kept moving", "problem": problem}

    def refresh(self, mission_id, context):
        """The Runtime's throttled refresh: records an observation only when
        ``refresh_due``. Returns ``None`` when nothing was due, else the
        ``observe`` result; a store refusal is returned, never raised."""
        try:
            stored = self._service.get(mission_id)
            state = self._service.get_state(mission_id)
        except mission_store.MissionStoreError as exc:
            return {"recorded": False, "status": None, "detail": str(exc),
                    "problem": exc.problem}
        except mission_record.MissionError as exc:
            return {"recorded": False, "status": None, "detail": str(exc),
                    "problem": exc.problem}
        if not refresh_due(stored, state, self._service.now()):
            return None
        return self.observe(mission_id, context)
