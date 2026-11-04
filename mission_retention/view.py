"""The catalog's READ-ONLY view of local retention (Deliverable 2).

Two closed blocks, both computed by the BACKEND from the trusted records, so the office files and
shows a Mission by the same rule that enforces reclamation and deletion (never a second rule in the
webview, which cannot see the run, the contract activations or the dispatch receipts):

- ``block``: per Mission still listed by its source, its retention eligibility (``eligibility``:
  eligible with its basis and source field, or not with its reason) and its retention phase
  (``intact`` until a record exists; then the coordinator's phase), with the retained summary once
  reclaimed. A Herdr task (not a DI-owned store) is ``not_applicable``.
- ``retained``: the Missions whose record is NO LONGER in its store (a terminal workflow reclaimed
  whole, or pruned by the workflow store's own cap): exactly what retention holds for them, its
  phase, time and summary, and nothing invented about the record that is gone.

Every store is read through its own fail-closed load boundary, without a lock and without a write;
each store is read at most once per refresh (``RetentionReader`` caches per refresh).
"""

from mission_retention import eligibility
from mission_retention import missions as missions_module
from mission_retention import store as retention_store
from workflow_authority import store as workflow_store_module

STATE_OBSERVED = "observed"
STATE_NOT_APPLICABLE = "not_applicable"
STATE_UNAVAILABLE = "unavailable"
PHASE_INTACT = "intact"

KIND_MISSION_CORE = "mission_core"
KIND_WORKFLOW_STORE = "workflow_store"

BLOCK_KEYS = ("state", "problem", "eligible", "basis", "basis_source", "reason", "phase",
              "phase_at", "summary", "retained_for")
RETAINED_KEYS = ("mission_ref", "source_id", "native_id", "family", "phase", "phase_at",
                 "summary")
# One source's archive reading (mission_sources/contract_v2.py RETAINED_SOURCE_KEYS; pinned equal)
ARCHIVE_KEYS = ("source_id", "state", "problem", "total", "awaiting", "missions")
MAX_LISTED = 128                              # contract_v2.MAX_RETAINED_MISSIONS, pinned equal

_FAMILY = {KIND_MISSION_CORE: retention_store.FAMILY_MISSION_CORE,
           KIND_WORKFLOW_STORE: retention_store.FAMILY_WORKFLOW_STORE}


def empty_block(state, problem=None):
    return {"state": state, "problem": problem, "eligible": None, "basis": None,
            "basis_source": None, "reason": None, "phase": None, "phase_at": None,
            "summary": None, "retained_for": 0}


def _visible_summary(record):
    """The summary as the office may show it: only once reclaimed; a deleted Mission has none."""
    if record is None or record["phase"] not in (retention_store.PHASE_RECLAIMING,
                                                 retention_store.PHASE_RECLAIMED):
        return None
    return record["summary"]


class RetentionReader(object):
    """One refresh's reader. ``block`` and ``retained`` read each store directory once."""

    def __init__(self):
        self._cache = {}

    def _load(self, kind, store_dir):
        key = (kind, store_dir)
        if key not in self._cache:
            records = retention_store.RetentionStore(store_dir).load()["records"]
            if kind == KIND_MISSION_CORE:
                missions, states = missions_module.load_missions(store_dir)
                self._cache[key] = (missions, states, records)
            else:
                document = workflow_store_module.WorkflowStore(store_dir).load()
                self._cache[key] = (document["workflows"], {}, records)
        return self._cache[key]

    def block(self, kind, store_dir, native_id):
        if kind not in _FAMILY:
            return empty_block(STATE_NOT_APPLICABLE)
        try:
            records_by_id, states, retained = self._load(kind, store_dir)
        except Exception as exc:                            # noqa: BLE001 - said, never guessed
            return empty_block(STATE_UNAVAILABLE, type(exc).__name__)
        source_record = records_by_id.get(native_id)
        if source_record is None:
            return empty_block(STATE_UNAVAILABLE, "record_not_in_store")
        decision = (eligibility.mission_core(source_record, states.get(native_id))
                    if kind == KIND_MISSION_CORE else eligibility.workflow(source_record))
        record = retained.get(retention_store.retention_key(_FAMILY[kind], native_id))
        return {"state": STATE_OBSERVED, "problem": None, "eligible": decision.eligible,
                "basis": decision.basis, "basis_source": decision.basis_source,
                "reason": decision.reason,
                "phase": PHASE_INTACT if record is None else record["phase"],
                "phase_at": None if record is None else record["phase_at"],
                "summary": _visible_summary(record),
                "retained_for": 0 if record is None else len(record["retained_for"])}

    def retained(self, kind, source_id, store_dir):
        """ONE source's archive reading (``ARCHIVE_KEYS``): ``observed``, with the EXACT total of
        Missions no longer in its store, the newest ``MAX_LISTED`` of them, and how many removed
        records await their summary's capture; or ``unavailable``, with its reason and no counts.
        Never an empty list standing for a store that could not be read: when every original record
        is gone there is no per-Mission block left to say it. ``None`` for a source whose store DI
        does not own (a Herdr task)."""
        if kind not in _FAMILY:
            return None
        try:
            records_by_id, _, retained = self._load(kind, store_dir)
            # ``awaiting`` is DISJOINT from ``total`` (never add one into the other twice): the
            # removed records the store's hand-off still holds that are ABSENT from the store (a
            # save that wrote the hand-off and then failed to replace workflows.json left the
            # record present: nothing is owed), have NO retention record yet (one that exists is
            # already in ``total``, listed with no summary until it is captured), and are OWED a
            # summary (an ending with no positive basis is discarded by adoption, as before).
            awaiting = 0
            if kind == KIND_WORKFLOW_STORE:
                with_ending = workflow_store_module.load_removals(store_dir)["removed"]
                awaiting = sum(
                    1 for workflow_id, ending in with_ending.items()
                    if workflow_id not in records_by_id
                    and retention_store.retention_key(_FAMILY[kind], workflow_id) not in retained
                    and eligibility.workflow(ending).eligible)
        except Exception as exc:                             # noqa: BLE001 - said, with why
            return {"source_id": source_id, "state": STATE_UNAVAILABLE,
                    "problem": getattr(exc, "problem", None) or type(exc).__name__,
                    "total": None, "awaiting": None, "missions": []}
        found = []
        for key in sorted(retained):
            record = retained[key]
            if record["family"] != _FAMILY[kind] or record["record_id"] in records_by_id:
                continue
            # (an ``observed`` record here was pruned by the workflow store's own cap before its
            # deadline: it is shown with its captured summary rather than vanishing)
            found.append({
                "mission_ref": "%s:%s" % (source_id, record["record_id"]),
                "source_id": source_id, "native_id": record["record_id"],
                "family": record["family"], "phase": record["phase"],
                "phase_at": record["phase_at"],
                "summary": (record["summary"] if record["phase"] != retention_store.PHASE_DELETED
                            and record["phase"] != retention_store.PHASE_DELETING else None),
            })
        found.sort(key=lambda item: (-item["phase_at"], item["mission_ref"]))
        return {"source_id": source_id, "state": STATE_OBSERVED, "problem": None,
                "total": len(found), "awaiting": awaiting, "missions": found[:MAX_LISTED]}
