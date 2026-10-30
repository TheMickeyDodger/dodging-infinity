"""Read-only readers for the two registered store kinds.

Each reader takes NO lock, creates no directory and writes nothing. It
reads the store file ONCE through ``mission_sources.fileread`` (no-follow,
regular file only, byte-bounded), parses exactly those bytes, and hands
the document to the store's OWN load-boundary function
(``mission.store.accept_loaded_document`` /
``workflow_authority.store.accept_loaded_document``, the same function the
store's ``load()`` calls), so the native fail-closed validation is applied
unchanged and the file is never re-opened. It returns plain-data mission
views. The two lifecycle models are reported side by side and never
merged:

- Mission Core (``mission.store``): ``lifecycle.model`` is
  ``mission_core.mission_state/1`` and ``lifecycle.state`` is the
  record's own ``state`` literal.
- Legacy Grok WorkflowStore (``workflow_authority.store``):
  ``lifecycle.model`` is ``workflow_authority.workflow_phase/2`` and
  ``lifecycle.state`` is the record's own ``phase`` literal.

``lifecycle.standing`` is derived by each model's OWN table below. A
literal missing from that table (a newer store than this reader) is
``unknown_future`` and is never treated as active, inactive or terminal.

Identity is source-qualified: ``mission_ref`` is ``<source_id>:<native
id>``. Provenance is reported in two separate parts: ``declared`` is the
producer the registration names; ``recorded`` is copied from the record's
own fields (Mission Core: the first revision's transport and principal
kind; WorkflowStore: the origin transport and whether a Telegram identity
is bound). Neither is inferred from the other.

Execution facts are copied, never guessed. Mission Core: the task id is
the run receipt's ``task_id`` (``None`` while unresolved) and the
workspace is the run intent's ``workspace_realpath`` (else the workspace
binding's ``path_realpath``); if both exist and differ the execution is
marked inconsistent. WorkflowStore: the task id is
``target_engine.task_id`` unless it is the runtime's unresolved sentinel,
and the workspace is the UNRELEASED lease's ``path_realpath``.

Health per source: a missing store directory, open permissions or an
unreadable file are ``unavailable``; a file that is not JSON or that the
store's own validation refuses is ``malformed``; a store schema version
other than the one this reader knows is ``incompatible``. A degraded
source contributes NO mission views. This reader does not OWN the stores
it reads, so a registered store whose file does not exist (never
materialised, or deleted) is ``unavailable`` (``store_file_absent``) — never
the owner's empty initial document, and never created. A store file that is
a symlink, a special file, larger than its bound or swapped during the read
is ``unavailable`` too (``mission_sources.fileread``). Only a store file that
was actually read and holds no records is ``fresh`` with zero records.
"""

import os
import stat

from mission import record as mission_record
from mission import store as mission_store
from workflow_authority import record as workflow_record
from workflow_authority import store as workflow_store

from mission_sources import contract
from mission_sources import fileread

# -- per-model standing tables (each model's OWN classification) --------

MISSION_CORE_STANDING = {
    mission_record.STATE_AWAITING_DECISION: contract.STANDING_INACTIVE,
    mission_record.STATE_DENIED: contract.STANDING_INACTIVE,
    mission_record.STATE_AUTHORIZED: contract.STANDING_ACTIVE,
    mission_record.STATE_RUNNING: contract.STANDING_ACTIVE,
    mission_record.STATE_BLOCKED: contract.STANDING_TERMINAL,
    mission_record.STATE_COMPLETED: contract.STANDING_TERMINAL,
    mission_record.STATE_CANCELLED: contract.STANDING_TERMINAL,
    mission_record.STATE_CLOSED: contract.STANDING_TERMINAL,
    # (c): a rejection is terminal; a change request waits, like a denial,
    # for a new revision and is not active.
    mission_record.STATE_REJECTED: contract.STANDING_TERMINAL,
    mission_record.STATE_CHANGES_REQUESTED: contract.STANDING_INACTIVE,
}

# WorkflowStore eligibility is the store's OWN rule, ``store.is_active``:
# every non-terminal phase is eligible (``active``), the terminal phases are
# ``terminal`` (parity pinned by test). Eligibility alone never creates a
# member: the roster additionally requires the canonical execution chain
# (resolved target task + unreleased workspace lease + matching runtime task),
# so a PLANNED record stays memberless while a NEEDS_REAUTHORIZATION record
# that RETAINS its binding keeps its team.
WORKFLOW_STORE_STANDING = {
    workflow_record.PHASE_PLANNED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_NEEDS_REAUTHORIZATION: contract.STANDING_ACTIVE,
    workflow_record.PHASE_AUTHORIZED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_WORKSPACE_READY: contract.STANDING_ACTIVE,
    workflow_record.PHASE_PREPARED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_VALIDATED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_DISPATCHED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_VERIFIED: contract.STANDING_ACTIVE,
    workflow_record.PHASE_COMPLETED: contract.STANDING_TERMINAL,
    workflow_record.PHASE_BLOCKED: contract.STANDING_TERMINAL,
}

# The runtime records this literal as ``target_engine.task_id`` when the
# task identity could not be resolved. Re-declared (the runtime package is
# not importable here); the focused suite pins parity with the runtime's
# own constant.
WORKFLOW_UNRESOLVED_TASK_ID = "unknown"

PROBLEM_STORE_DIRECTORY_MISSING = "store_directory_missing"
PROBLEM_STORE_PERMISSIONS_OPEN = "store_permissions_open"
PROBLEM_STORE_UNREADABLE = "store_unreadable"
PROBLEM_STORE_TOO_LARGE = "store_exceeds_read_bound"
PROBLEM_STORE_FILE_ABSENT = "store_file_absent"
PROBLEM_QUERY_INPUT_BUDGET = "query_input_budget_exhausted"
PROBLEM_QUERY_MISSION_BUDGET = "query_mission_budget_exhausted"
PROBLEM_STORE_NOT_JSON = "store_not_json"
PROBLEM_STORE_TOO_DEEP = "store_json_nesting_exceeds_bound"
PROBLEM_READER_FAILED = "store_reader_failed"
PROBLEM_STORE_SCHEMA = "store_schema_unsupported"
PROBLEM_STORE_REFUSED = "store_validation_refused"
PROBLEM_RECORD_SHAPE = "record_shape_unexpected"
PROBLEM_UNKNOWN_KIND = "source_kind_unknown"

_OPEN_MODE_BITS = 0o077

# Hard bounds of one query (pinned). A store file is read once within
# MAX_STORE_READ_BYTES; ALL store files of one query together within
# MAX_QUERY_INPUT_BYTES; at most MAX_QUERY_MISSIONS mission views are built
# per query. Each is checked BEFORE the read / the expansion, and a source
# that would exceed one is listed as unavailable with an explicit problem,
# never truncated or skipped silently.
MAX_STORE_READ_BYTES = 16777216
MAX_QUERY_INPUT_BYTES = 67108864
MAX_QUERY_MISSIONS = 2048


def standing_of(table, literal):
    """The standing a model's own table gives ``literal``; unknown_future
    for a literal the table does not know."""
    return table.get(literal, contract.STANDING_UNKNOWN_FUTURE)


def _reading(source, now, health, problem, missions=()):
    return {
        "source_id": source["source_id"],
        "kind": source["kind"],
        "producer": source["producer"],
        "store_dir": source["store_dir"],
        "max_age_seconds": source["max_age_seconds"],
        "health": health,
        "problem": problem,
        "observed_at": now,
        "record_count": len(missions),
        "missions": list(missions),
    }


def _degrade(source, now, health, problem):
    return _reading(source, now, health, problem), None


def _read_document(source, now, budget, file_name, version_key,
                   expected_version, check_directory, accept, refused,
                   collection_key):
    """ONE bounded, no-follow read of the store file, then the store's OWN
    load-boundary validation (``accept``) over exactly those bytes.

    Returns ``(degraded_reading, None)`` or ``(None, document)``. The file
    is never opened a second time, so the bytes validated are the bytes
    parsed. This reader does not own the store: a missing store file is
    ``unavailable`` (``store_file_absent``), never the owner's empty
    default. The per-query ``budget`` is charged BEFORE expansion: the read
    is refused when the file exceeds the remaining input bytes, and the
    views are not built when the store's record count exceeds the remaining
    mission budget; both are explicit degraded readings."""
    directory = source["store_dir"]
    try:
        directory_mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_STORE_DIRECTORY_MISSING)
    except OSError:
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_STORE_UNREADABLE)
    if not stat.S_ISDIR(directory_mode):
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_STORE_DIRECTORY_MISSING)
    if check_directory and directory_mode & _OPEN_MODE_BITS:
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_STORE_PERMISSIONS_OPEN)
    path = os.path.join(directory, file_name)
    allowance = min(MAX_STORE_READ_BYTES, budget.bytes_remaining)
    try:
        data = fileread.read_private_file(path, allowance)
    except fileread.FileRefused as exc:
        if exc.problem == fileread.PROBLEM_FILE_ABSENT:
            return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                            PROBLEM_STORE_FILE_ABSENT)
        if exc.problem == fileread.PROBLEM_FILE_PERMISSIONS_OPEN:
            return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                            PROBLEM_STORE_PERMISSIONS_OPEN)
        if exc.problem == fileread.PROBLEM_FILE_TOO_LARGE:
            if allowance < MAX_STORE_READ_BYTES:
                return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                                PROBLEM_QUERY_INPUT_BUDGET)
            return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                            PROBLEM_STORE_TOO_LARGE)
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_STORE_UNREADABLE)
    budget.bytes_remaining -= len(data)
    try:
        document = fileread.load_bounded_json(data)
    except fileread.JsonRefused as exc:
        return _degrade(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_STORE_NOT_JSON
                        if exc.problem != fileread.PROBLEM_JSON_TOO_DEEP
                        else PROBLEM_STORE_TOO_DEEP)
    version = document.get(version_key) if type(document) is dict else None
    if type(version) is not int or version != expected_version:
        return _degrade(source, now, contract.HEALTH_INCOMPATIBLE,
                        PROBLEM_STORE_SCHEMA)
    try:
        document = accept(document, path)
    except refused:
        return _degrade(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_STORE_REFUSED)
    count = len(document[collection_key])
    if count > budget.missions_remaining:
        return _degrade(source, now, contract.HEALTH_UNAVAILABLE,
                        PROBLEM_QUERY_MISSION_BUDGET)
    budget.missions_remaining -= count
    return None, document


# -- Mission Core -------------------------------------------------------

def _mission_core_view(source, mission):
    mission_id = mission["mission_id"]
    state = mission["state"]
    current = mission["revisions"][-1]
    first_provenance = mission["revisions"][0]["provenance"]
    run = mission.get("run") or {}
    intent = run.get("intent")
    receipt = run.get("receipt")
    binding = run.get("workspace")
    pauses = run.get("pauses") or []
    workspaces = set()
    if intent is not None:
        workspaces.add(intent["workspace_realpath"])
    if binding is not None:
        workspaces.add(binding["path_realpath"])
    workspace = None
    if intent is not None:
        workspace = intent["workspace_realpath"]
    elif binding is not None:
        workspace = binding["path_realpath"]
    return {
        "mission_ref": "%s:%s" % (source["source_id"], mission_id),
        "source_id": source["source_id"],
        "native_id": mission_id,
        "lifecycle": {
            "model": contract.LIFECYCLE_MODEL_MISSION_CORE,
            "state": state,
            "standing": standing_of(MISSION_CORE_STANDING, state),
        },
        "producer": {
            "declared": source["producer"],
            "recorded": {
                "transport": first_provenance["transport"],
                "principal_kind": first_provenance["principal_kind"],
            },
        },
        "revision": mission["current_revision"],
        "objective": current["proposal"]["objective"],
        "updated_at": mission["updated_at"],
        "execution": {
            "task_id": receipt["task_id"] if receipt is not None else None,
            "identity_source": (receipt["identity_source"]
                                if receipt is not None else None),
            "workspace_realpath": workspace,
            "run_started": intent is not None,
            "receipt_recorded": receipt is not None,
            "consistent": len(workspaces) <= 1,
            "record_ambiguous": False,
        },
        "native": {
            "paused": bool(pauses) and pauses[-1]["resumed_at"] is None,
            "cancel_requested": run.get("cancel") is not None,
        },
    }


def read_mission_core(source, now, budget):
    degraded, document = _read_document(
        source, now, budget, mission_store.MISSIONS_FILE_NAME,
        "mission_store_schema_version",
        mission_store.MISSION_STORE_SCHEMA_VERSION, True,
        mission_store.accept_loaded_document, mission_store.MissionStoreError,
        "missions")
    if degraded is not None:
        return degraded
    try:
        views = [_mission_core_view(source, mission)
                 for _, mission in sorted(document["missions"].items())]
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        return _reading(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_RECORD_SHAPE)
    return _reading(source, now, contract.HEALTH_FRESH, None, views)


# -- legacy Grok WorkflowStore -----------------------------------------

def _workflow_view(source, workflow):
    workflow_id = workflow["workflow_id"]
    phase = workflow["phase"]
    engine = workflow["target_engine"]
    lease = workflow["workspace_lease"]
    origin = workflow["origin"]
    authorization = workflow["mission_authorization"]
    ambiguity = workflow["ambiguity"]
    task_id = None
    if engine is not None and engine["task_id"] != WORKFLOW_UNRESOLVED_TASK_ID:
        task_id = engine["task_id"]
    workspace = None
    if lease is not None and lease["released_at"] is None:
        workspace = lease["path_realpath"]
    return {
        "mission_ref": "%s:%s" % (source["source_id"], workflow_id),
        "source_id": source["source_id"],
        "native_id": workflow_id,
        "lifecycle": {
            "model": contract.LIFECYCLE_MODEL_WORKFLOW_STORE,
            "state": phase,
            "standing": standing_of(WORKFLOW_STORE_STANDING, phase),
        },
        "producer": {
            "declared": source["producer"],
            "recorded": {
                "origin_transport": (origin["transport"]
                                     if origin is not None else None),
                "telegram_bound": workflow["telegram"] is not None,
            },
        },
        "revision": authorization["revision"],
        "objective": authorization["objective"],
        "updated_at": None,
        "execution": {
            "task_id": task_id,
            "identity_source": None,
            "workspace_realpath": workspace,
            "run_started": engine is not None,
            "receipt_recorded": engine is not None,
            "consistent": True,
            "record_ambiguous": (type(ambiguity) is dict
                                 and ambiguity.get("state") != "none"),
        },
        "native": {
            "ambiguity": (ambiguity.get("state")
                          if type(ambiguity) is dict else None),
        },
    }


def read_workflow_store(source, now, budget):
    degraded, document = _read_document(
        source, now, budget, workflow_store.WORKFLOWS_FILE_NAME,
        "workflow_store_schema_version",
        workflow_store.WORKFLOW_STORE_SCHEMA_VERSION, False,
        workflow_store.accept_loaded_document, workflow_store.StoreError,
        "workflows")
    if degraded is not None:
        return degraded
    try:
        views = [_workflow_view(source, workflow)
                 for _, workflow in sorted(document["workflows"].items())]
    except (KeyError, TypeError, IndexError, AttributeError, ValueError):
        return _reading(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_RECORD_SHAPE)
    return _reading(source, now, contract.HEALTH_FRESH, None, views)


READERS = {
    contract.KIND_MISSION_CORE: read_mission_core,
    contract.KIND_WORKFLOW_STORE: read_workflow_store,
}


class QueryBudget(object):
    """The aggregate allowance of ONE query, charged before expansion."""

    def __init__(self, bytes_remaining=None, missions_remaining=None):
        self.bytes_remaining = (MAX_QUERY_INPUT_BYTES if bytes_remaining is None
                                else bytes_remaining)
        self.missions_remaining = (MAX_QUERY_MISSIONS
                                   if missions_remaining is None
                                   else missions_remaining)


def read_source(source, now, budget, readers=None):
    """Read one registered source with the reader for its kind.

    Containment for a long-lived caller: a reader that raises for any input
    it did not anticipate yields an explicit ``malformed`` reading
    (``store_reader_failed``) for that source only — never a crash of the
    whole query and never an empty, healthy source. A reader result that is
    not a well-formed reading is treated the same way."""
    table = READERS if readers is None else readers
    reader = table.get(source["kind"])
    if reader is None:
        return _reading(source, now, contract.HEALTH_INCOMPATIBLE,
                        PROBLEM_UNKNOWN_KIND)
    try:
        result = reader(source, now, budget)
    except Exception:
        return _reading(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_READER_FAILED)
    if type(result) is not dict or result.get("health") not in \
            contract.HEALTH_STATES or type(result.get("missions")) is not list:
        return _reading(source, now, contract.HEALTH_MALFORMED,
                        PROBLEM_READER_FAILED)
    return result
