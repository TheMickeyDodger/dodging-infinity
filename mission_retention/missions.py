"""Mission Core, as local retention may touch it: read through its OWN load boundary, and ONE
reduction, of a terminal run's two diagnostic texts. Nothing else of ``missions.json`` is changed.

Why only that. Mission Core's load replays every Mission's recorded decisions and refuses any
disagreement (``mission/authorization.py`` ``reconcile_mission_history``: "digests are not what this
function trusts — the preserved decision record is"). It reads the revisions' proposal BODIES (the
approved scope against ``requested_action_scope`` / ``requested_delivery_target``), re-derives each
activation contract from the stored proposal's ``proof_contract`` (``mission/store.py``
``activation_contract``), validates every proposal whole against its digest (``mission/manifest.py``),
and the Mission State's applied operations are a chain re-derived on every read
(``mission/journal.py``). So the proposals, decisions, authorizations, the Authority Ledger, the
reservations and the Mission State all stay: reducing any of them would weaken or break the checks
that refuse today. The two texts reduced here (``run.workspace_refusal.detail`` and
``run.pending_proof.blockers[].detail``) are read by no approval, replay or reconciliation check;
the run marker that records the reduction is valid only on a run that can never change again
(``manifest.RUN_RECLAIMABLE_STATES``), so nothing about an active record is relaxed.

This module is one of the deliberate, exact Mission Core consumers pinned in tests/test_static.py
and tests/test_mission_core.py: it may use only ``MissionStore`` (its lock, load and save, never a
service or a Mission operation) and ``manifest.reclaimed_run``.
"""

import os

from mission import manifest as mission_manifest
from mission import store as mission_store

OUTCOMES = mission_manifest.RUN_RECLAIM_OUTCOMES


def load_missions(directory):
    """``(missions, mission_state)``: every Mission record and every Mission State record, read
    through Mission Core's own load boundary (which fails closed)."""
    store = mission_store.MissionStore(directory)
    if not os.path.exists(store.path):
        return {}, {}
    document = store.load()
    return document["missions"], document.get("mission_state") or {}


def _has_diagnostics(run):
    return "workspace_refusal" in run or run.get("pending_proof") is not None


def reclaim_run_diagnostics(directory, mission_ids, at, outcome):
    """Reduce the two diagnostic texts of these Missions' TERMINAL runs, through the store's own
    lock, load, full validation (replay included) and atomic save. Idempotent; a Mission with no
    run, a run still able to change, or no diagnostic text is left byte-for-byte as it is. Returns
    the number of Missions changed."""
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of %s" % ", ".join(OUTCOMES))
    store = mission_store.MissionStore(directory)
    if not os.path.exists(store.path):
        return 0
    with store.lock():
        document = store.load()
        changed = 0
        for mission_id in mission_ids:
            mission = document["missions"].get(mission_id)
            run = None if mission is None else mission.get("run")
            if run is None or mission["state"] not in mission_manifest.RUN_RECLAIMABLE_STATES:
                continue
            if not _has_diagnostics(run):
                continue
            marker = run.get("reclaimed")
            if marker is not None and (marker["outcome"] == outcome or outcome == "reclaimed"):
                continue
            mission["run"] = mission_manifest.reclaimed_run(run, at, outcome)
            changed += 1
        if changed:
            store.save(document)
        return changed
