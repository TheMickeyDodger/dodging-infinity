"""The READ-ONLY Herdr task source (item 07): every team and every live role
a DI launch route produced, read from the engine's OWN records, at the
catalog producer boundary.

    dicatalog.py register --registry DIR --source-id herd --kind task_native
                          --store-dir <the directory holding repos.json>
                          --producer herdctl

``mission_sources`` stays engine-neutral (a static rule): there the kind is
``contract.KIND_TASK_NATIVE``; everything Herdr-specific lives here. Record
schema ``SCHEMA`` version ``SCHEMA_VERSION``; lifecycle model
``LIFECYCLE_MODEL``; the source block's ``locators`` account is schema
``LOCATORS_SCHEMA`` version ``LOCATORS_SCHEMA_VERSION``. It creates no
Mission Core record and synthesizes no Mission fact.

WHAT IT READS, and nothing else (no recursive scan, no write):

1. ``<store_dir>/repos.json``, the DI repository registry ``herdctl init``
   writes (``{"version": 1, "repos": {alias: {"path", ...}}}``), through
   the existing hardened fixed-artifact read (``state_artifact``: no
   symlink, no escape, no hardlink, its 64 KiB bound). A LOCATOR ONLY: a
   registered root is a place to look, never authority and never proof of
   a live agent. This module never imports the engine's registry module,
   so it never runs its load-time legacy migration (which writes), nor any
   save, prune or registration.
2. For each registered root, the fixed state artifacts ``task.json`` (the
   engine's sole current-task authority, ``herdr.vintage``),
   ``runtime.json`` and ``role-bindings.json``: same hardened read.

BOUNDS (none raised, none new beyond the registry's own): at most
``MAX_REGISTERED_ROOTS`` aliases (more is ``unavailable``, never
truncated); every byte read is charged to the query's SHARED input budget
and every view to its SHARED mission budget, exactly as the store readers
are (an exhausted budget makes this source ``unavailable``, disclosed).

ACCOUNT: each registered alias is accounted for EXACTLY ONCE, in the source
block's ``locators`` account or in exactly one view, never dropped:

- not an absolute, normalised path string (or an alias out of bounds):
  excluded, ``registered_entry_invalid``;
- absent (a removed checkout): excluded, ``registered_root_absent``;
- not its own real path, not a directory, or not examinable (a symlinked
  root): excluded, ``registered_root_refused``;
- a state artifact that cannot be read safely: excluded,
  ``root_state_refused``; a task record that is not well formed: excluded,
  ``root_state_invalid``;
- no herd state at all (registered, never started): counted in
  ``aliases_without_herd_state``;
- otherwise the alias belongs to its PHYSICAL root (device and inode), and
  every alias of one physical root shares ONE view listing them all
  (``producer.recorded.root_aliases``): aliases never make several teams.

VIEWS: one per physical root, source-qualified by task AND root:
``<source_id>:<task id>@<root key>`` (root key: the first 12 hex of the
sha256 of the root's path), or ``<source_id>:root@<root key>`` for a
started team with no task record, whose lifecycle state is the engine's
own no-task literal (``NO_CURRENT_TASK``), standing ``inactive``, no task.
A task view's lifecycle state is the record's own ``status`` literal;
standing: ``ACTIVE`` active; ``ERROR`` inactive; ``ABORTED`` and the
engine's terminal statuses (``herdr.vintage``) terminal; any other literal
``unknown_future`` (never active). ``task.json`` must name this root as its
repository, else the view is inconsistent (never bound). Views are listed
most recently started task first: that order is CAPTURE SCHEDULING only
(``roster.discovery``), never identity or membership evidence. Membership
of a root's roles in its task is DERIVED (``MEMBERSHIP_DERIVED``):
``roster`` binds a task-native TEAM only when a live session corroborates
it, and defers it to any canonical record of the same task or root.

RUNTIME AGENTS (``runtime_agents``): with an ``Observation`` (one per
refresh), each source pass's ROLE records are kept in memory, never in an
answer. ``runtime_agents`` then decides, per role and never one role from
another, whether a live listing row IS that recorded role: the registered
physical root, the runtime role mapping and the persisted binding (the
engine's own ``identity.binding_gap`` and the runtime projection's own
rules, ``runtime_snapshot._bindings``), stable pane/workspace agreement,
ONE unique fresh native row, the engine's own ``identity.classify`` over
that row's full native stable identity (PRESENT only: a replaced session,
a degraded or disagreeing identity is never verified), and identical
records in BOTH source passes. Two-pass agreement is not atomic and does
not detect a change and change-back (ABA); every observed block says so.
A role whose team is bound is represented by that team, never again here;
a role whose team binding is ambiguous is ambiguous here too (a runtime
observation never rescues or masks it).
"""

import hashlib
import json
import os
import stat

from herdr import identity as engine_identity
from herdr import vintage
from mission_sources import contract
from mission_sources import fileread
from mission_sources import query_v2 as query_v2_module
from mission_sources import sources as sources_module
from target_runtime import prepare as prepare_module
from target_runtime import runtime_snapshot as snapshot_module
from target_runtime import state_artifact

KIND = contract.KIND_TASK_NATIVE
SCHEMA = "di.catalog.herdr_task"
SCHEMA_VERSION = 1
LOCATORS_SCHEMA = "di.catalog.herdr_task.locators"
LOCATORS_SCHEMA_VERSION = 1
LIFECYCLE_MODEL = "herdr.task/1"
IDENTITY_SOURCE = "herdr.task_json"
#: The engine's own literal for a root with NO current task
#: (``herdr.tasks.load_task`` with no ``task.json``; parity pinned by test).
NO_CURRENT_TASK = "IDLE"
#: Every view says its membership is derived, not recorded per session.
MEMBERSHIP_DERIVED = "derived_from_current_task_record"

REGISTRY_FILE = "repos.json"
REGISTRY_VERSION = 1
MAX_REGISTERED_ROOTS = 256
MAX_ALIAS_CHARS = 128
MAX_ROOT_PATH_CHARS = 4096
STATE_SUBDIRS = snapshot_module.STATE_SUBDIRS
TASK_FILE = snapshot_module.TASK_FILE
RUNTIME_FILE = snapshot_module.RUNTIME_FILE
BINDINGS_FILE = snapshot_module.BINDINGS_FILE
ROOT_KEY_HEX = 12
#: The display objective: the task description's first non-empty line,
#: bounded (a description is a whole prompt). Display only, never identity.
MAX_OBJECTIVE_CHARS = 160
#: The headings ``herdctl task --mission`` writes into a task description
#: (herdctl.py ``task``): the first section's, and the four optional ones that
#: follow it. Exact strings; see ``_objective``.
_MISSION_OBJECTIVE_HEADING = "OBJECTIVE"
_MISSION_SECTION_HEADINGS = frozenset(
    (_MISSION_OBJECTIVE_HEADING, "CONSTRAINTS", "RULES",
     "ACCEPTANCE CRITERIA", "VERIFICATION"))

# Source-level problems: the registry document itself.
PROBLEM_REGISTRY_ABSENT = "herdr_registry_absent"
PROBLEM_REGISTRY_REFUSED = "herdr_registry_read_refused"
PROBLEM_REGISTRY_SHAPE = "herdr_registry_shape_unexpected"
PROBLEM_REGISTRY_VERSION = "herdr_registry_version_unsupported"
PROBLEM_REGISTRY_OVER_BOUND = "herdr_registry_exceeds_root_bound"

# Per-alias exclusion reasons (the ``locators`` account).
EXCLUDED_ENTRY_INVALID = "registered_entry_invalid"
EXCLUDED_ROOT_ABSENT = "registered_root_absent"
EXCLUDED_ROOT_REFUSED = "registered_root_refused"
EXCLUDED_STATE_REFUSED = "root_state_refused"
EXCLUDED_STATE_INVALID = "root_state_invalid"
EXCLUSION_REASONS = (EXCLUDED_ENTRY_INVALID, EXCLUDED_ROOT_ABSENT,
                     EXCLUDED_ROOT_REFUSED, EXCLUDED_STATE_REFUSED,
                     EXCLUDED_STATE_INVALID)
#: The exclusions that mean a root's role records could NOT be read (an unusable registration, a refused
#: root, a refused or invalid state artifact): such a root is counted in ``roots_role_records_unreadable``
#: and can never be reported as "records no roles". ``EXCLUDED_ROOT_ABSENT`` (a removed checkout: nothing
#: can be hiding there) and a root with no herd state at all (never started) are NOT in this set: they
#: stay disclosed in the ``locators`` account only.
UNREADABLE_EXCLUSIONS = (EXCLUDED_ENTRY_INVALID, EXCLUDED_ROOT_REFUSED,
                         EXCLUDED_STATE_REFUSED, EXCLUDED_STATE_INVALID)

#: A runtime or role-bindings record that is present but not a JSON object.
ROLE_RECORDS_NOT_AN_OBJECT = "role_records_not_a_json_object"

# ``runtime_agents`` block problems.
PROBLEM_SINGLE_PASS = "role_records_read_in_one_source_pass_only"
#: Every task-native source of the answer pass was read fresh and no root of
#: any of them records a role, so the capture had nothing to observe: an
#: EMPTY roster, said with its counts, never a failure to look.
PROBLEM_NO_ROLE_RECORDS = "task_native_sources_record_no_roles"
PROBLEM_SNAPSHOT_NOT_FRESH = "runtime_snapshot_not_fresh"
PROBLEM_NATIVE_NOT_OBSERVED = "native_stable_identity_not_observed"
PROBLEM_SOURCE_NOT_FRESH = "task_native_source_not_fresh"

# Per-role IDENTITY reasons.
ID_PRESENT = "native_identity_present"
ID_BINDING_INCOMPLETE = "role_binding_incomplete"
ID_NOT_IN_FIRST_PASS = "role_not_read_in_first_source_pass"
ID_CHANGED = "role_records_changed_between_source_passes"
ID_STABLE_RECORDS_DISAGREE = "stable_records_disagree"
ID_NAME_DUPLICATED = "agent_name_duplicated_in_listing"
ID_SESSION_UNKNOWN = "live_session_unknown"
ID_SESSION_SHARED = "live_session_shared_by_agents"
ID_NOT_REQUESTED = "stable_identity_not_requested_within_bound"
ID_NOT_OBSERVED = "stable_identity_not_observed"
ID_SESSION_REPLACED = "session_replaced"
ID_DEGRADED = "native_identity_degraded"
ID_TEAM_AMBIGUOUS = "team_binding_ambiguous"
ID_MULTIPLE_ROOTS = "identity_recorded_by_multiple_roots"

# Per-role ASSIGNMENT reasons.
AS_NO_TASK_RECORD = "no_task_record"
AS_NOT_CURRENT = "task_record_not_current"
AS_OVER_CAPACITY = "root_not_captured_over_capacity"
AS_STATUS_UNRECOGNIZED = "task_status_unrecognized"
#: A live role at a root whose team IS bound, that the bound roster does not
#: hold: its own identity is decided; its membership is not verified.
AS_NOT_IN_TEAM_ROSTER = "role_not_in_the_bound_team_roster"


def standing(status):
    """The ``herdr.task/1`` standing of one ``status`` literal."""
    if status == "ACTIVE":
        return contract.STANDING_ACTIVE
    if status in ("ERROR", NO_CURRENT_TASK):
        return contract.STANDING_INACTIVE
    if status == "ABORTED" or vintage.is_terminal_status(status):
        return contract.STANDING_TERMINAL
    return contract.STANDING_UNKNOWN_FUTURE


class _Degraded(Exception):
    def __init__(self, health, problem):
        super(_Degraded, self).__init__(problem)
        self.health = health
        self.problem = problem


class _Excluded(Exception):
    def __init__(self, reason):
        super(_Excluded, self).__init__(reason)
        self.reason = reason


#: The most ONE call of the unchanged hardened primitive
#: (``prepare.read_workspace_instruction``) can physically read: its bound
#: plus the overrun-detection byte, from one descriptor.
READ_MAX = prepare_module.MAX_INSTRUCTION_FILE_BYTES + 1
READ_ACCOUNT_SCHEMA = "di.catalog.herdr_task.read_account"
READ_ACCOUNT_VERSION = 1


class _Reservation(object):
    """THE byte reservation of ONE source read, a HARD upper bound in both
    query contract versions:

    - acquisition: ``allowance`` is the shared budget's remaining bytes,
      read ONCE, at most one store read bound
      (``sources.MAX_STORE_READ_BYTES``). Version 2: the ``PassBudget``'s
      ``ReadHold`` ALREADY holds ``allowance + 1`` in the stage and refresh
      ledgers. Version 1: the allowance is held here, by debiting the
      ``QueryBudget`` before any read;
    - I/O bound: ``begin_read`` refuses, BEFORE the primitive is called,
      unless ``spent + READ_MAX <= allowance`` (a possibly small read near
      exhaustion is refused: the accepted cost of a true bound);
    - charge (``end_read`` / ``unknown_read``): MEASURED bytes where the
      count is known, a LABELLED CONSERVATIVE ``READ_MAX`` where it is not;
    - settlement: ``settle`` EXACTLY ONCE (idempotent), by ``_read``'s
      ``finally``. Version 2: ``ReadHold.settle(pulled=spent)``, never
      clipped since ``spent <= allowance < hold``. Version 1: only room
      that was never read returns to the ``QueryBudget``. No counter is
      ever negative; no consumed byte is released."""

    def __init__(self, budget):
        self._budget = budget
        self._v2 = isinstance(budget, query_v2_module.PassBudget)
        start = budget.bytes_remaining
        self.allowance = max(0, min(start,
                                    sources_module.MAX_STORE_READ_BYTES))
        if not self._v2:
            budget.bytes_remaining = start - self.allowance
        self.measured = 0
        self.conservative = 0
        self.reads_started = 0
        self.reads_refused = 0
        self.settled = False

    @property
    def spent(self):
        return self.measured + self.conservative

    def begin_read(self):
        if self.settled or self.spent + READ_MAX > self.allowance:
            self.reads_refused += 1
            raise _Degraded(contract.HEALTH_UNAVAILABLE,
                            sources_module.PROBLEM_QUERY_INPUT_BUDGET)
        self.reads_started += 1

    def end_read(self, status, count):
        """Charge one completed primitive call by its outcome:

        - ``read``, ``refused_non_utf8``: ``count`` (the bytes it read);
        - ``refused_over_bound``: ``READ_MAX`` (it reads exactly bound + 1,
          then refuses; its count is the file SIZE, never charged);
        - ``absent``, ``refused_not_a_regular_file``, ``refused_hardlink``,
          ``refused_unreadable``: 0 (it returns no content byte);
        - ``refused_escapes`` (the wrapper discards the count even after a
          real read) and any other status: a CONSERVATIVE ``READ_MAX``."""
        if status in (prepare_module.INSTRUCTION_READ,
                      prepare_module.INSTRUCTION_REFUSED_NON_UTF8) and (
                type(count) is int and 0 <= count <= READ_MAX):
            self.measured += count
        elif status == prepare_module.INSTRUCTION_REFUSED_OVER_BOUND:
            self.measured += READ_MAX
        elif status in (prepare_module.INSTRUCTION_ABSENT,
                        prepare_module.INSTRUCTION_REFUSED_NOT_REGULAR,
                        prepare_module.INSTRUCTION_REFUSED_HARDLINK,
                        prepare_module.INSTRUCTION_REFUSED_UNREADABLE):
            pass
        else:
            self.conservative += READ_MAX

    def unknown_read(self):
        """The primitive raised: what it read is unknown."""
        self.conservative += READ_MAX

    def settle(self):
        if self.settled:
            return
        self.settled = True
        if not 0 <= self.spent <= self.allowance:
            raise RuntimeError("task-native reservation overspent")
        if self._v2:
            self._budget.bytes_remaining = self.allowance - self.spent
        else:
            self._budget.bytes_remaining += self.allowance - self.spent

    def account(self):
        return {"schema": READ_ACCOUNT_SCHEMA, "version": READ_ACCOUNT_VERSION,
                "allowance": self.allowance, "reserve_per_read": READ_MAX,
                "reads_started": self.reads_started,
                "reads_refused_for_room": self.reads_refused,
                "measured_bytes": self.measured,
                "conservative_bytes": self.conservative,
                "settled_bytes": self.spent}


def _artifact(directory, subdirs, name, reservation, refused):
    """``(digest, text)`` of one fixed artifact, ``(None, None)`` when it is
    absent; raises ``refused`` (an exception instance) when it cannot be
    read safely. No primitive call starts without its full reservation;
    every call is charged on every outcome (``_Reservation``)."""
    reservation.begin_read()
    try:
        status, count, digest, text = state_artifact.read_state_artifact(
            directory, subdirs, name)
    except BaseException:
        reservation.unknown_read()
        raise
    reservation.end_read(status, count)
    if status == prepare_module.INSTRUCTION_ABSENT:
        return None, None
    if status != prepare_module.INSTRUCTION_READ:
        raise refused
    return digest, text


def _json_object(text):
    try:
        value = fileread.load_bounded_json(text.encode("utf-8"))
    except fileread.JsonRefused:
        return None
    return value if type(value) is dict else None


def registry_entries(store_dir, reservation, seen=None):
    """``[(alias, path_or_None)]`` from the DI repository registry, sorted
    by alias; ``None`` for an entry whose shape is invalid. Read-only: one
    hardened read, no migration, no write. ``seen["aliases"]`` records how
    many aliases the registry REQUESTS as soon as that is known (even when
    the read then degrades, e.g. over its bound)."""
    _digest, text = _artifact(store_dir, (), REGISTRY_FILE, reservation,
                              _Degraded(
        contract.HEALTH_UNAVAILABLE, PROBLEM_REGISTRY_REFUSED))
    if text is None:
        raise _Degraded(contract.HEALTH_UNAVAILABLE, PROBLEM_REGISTRY_ABSENT)
    document = _json_object(text)
    if document is None:
        raise _Degraded(contract.HEALTH_MALFORMED, PROBLEM_REGISTRY_SHAPE)
    if document.get("version") != REGISTRY_VERSION:
        raise _Degraded(contract.HEALTH_INCOMPATIBLE, PROBLEM_REGISTRY_VERSION)
    repos = document.get("repos")
    if type(repos) is not dict:
        raise _Degraded(contract.HEALTH_MALFORMED, PROBLEM_REGISTRY_SHAPE)
    if seen is not None:
        seen["aliases"] = len(repos)
    if len(repos) > MAX_REGISTERED_ROOTS:
        raise _Degraded(contract.HEALTH_UNAVAILABLE,
                        PROBLEM_REGISTRY_OVER_BOUND)
    entries = []
    for alias in sorted(repos):
        entry = repos[alias]
        path = entry.get("path") if type(entry) is dict else None
        valid = (0 < len(alias) <= MAX_ALIAS_CHARS
                 and type(path) is str and 0 < len(path) <= MAX_ROOT_PATH_CHARS
                 and "\x00" not in path and os.path.isabs(path)
                 and os.path.normpath(path) == path)
        entries.append((alias, path if valid else None))
    return entries


def root_key(root):
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:ROOT_KEY_HEX]


def _physical(path):
    """``(device, inode)`` of a registered root, or raises ``_Excluded``."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        raise _Excluded(EXCLUDED_ROOT_ABSENT)
    except OSError:
        raise _Excluded(EXCLUDED_ROOT_REFUSED)
    if not stat.S_ISDIR(status.st_mode) or os.path.realpath(path) != path:
        raise _Excluded(EXCLUDED_ROOT_REFUSED)
    return status.st_dev, status.st_ino


def _root_state(root, reservation):
    """``(task, files)``: ``task`` a dict, ``NO_CURRENT_TASK`` (started, no
    task record) or None (no herd state); ``files`` ``{name: (digest,
    parsed object or None)}`` for the three artifacts. Raises
    ``_Excluded``."""
    refused = _Excluded(EXCLUDED_STATE_REFUSED)
    files = {}
    for name in (TASK_FILE, RUNTIME_FILE, BINDINGS_FILE):
        digest, text = _artifact(root, STATE_SUBDIRS, name, reservation,
                                 refused)
        files[name] = (digest, None if text is None else _json_object(text))
        if text is not None and files[name][1] is None and name == TASK_FILE:
            raise _Excluded(EXCLUDED_STATE_INVALID)
    task = files[TASK_FILE][1]
    if files[TASK_FILE][0] is None:
        return (NO_CURRENT_TASK if files[RUNTIME_FILE][0] is not None
                else None), files
    task_id, status_literal = task.get("id"), task.get("status")
    if (type(task_id) is not str or not task_id
            or len(task_id) > contract.MAX_SNAPSHOT_TEXT_CHARS
            or type(status_literal) is not str or not status_literal
            or len(status_literal) > contract.MAX_SNAPSHOT_TEXT_CHARS):
        raise _Excluded(EXCLUDED_STATE_INVALID)
    return task, files


def _objective(description):
    """The display objective: the description's first non-empty line, bounded.

    ONE exact exception, for the description ``herdctl task --mission`` writes
    (herdctl.py ``task``): sections joined by a blank line, the first being the
    literal line ``OBJECTIVE`` followed by the mission's objective, then
    optionally ``CONSTRAINTS``, ``RULES``, ``ACCEPTANCE CRITERIA`` and
    ``VERIFICATION``, each followed by ``- `` bullet lines. When the first
    non-empty line is exactly ``OBJECTIVE``, the objective is the NEXT non-empty
    line, unless that line is exactly one of the five formatter headings
    (``_MISSION_SECTION_HEADINGS``, ``OBJECTIVE`` itself included) or there is no
    next line: then it is unknown (``None``). An empty objective is written as
    ``OBJECTIVE`` followed directly by the next section's heading, and a repeated
    heading is indistinguishable from a malformed record, so both are unknown (an
    objective that is literally one of those five words is therefore unknown too):
    a heading is never returned as a goal. Any other next line is returned as
    written, including one that begins with ``- ``: the formatter's bullets only
    ever follow a section heading, a heading in this position is refused before
    its bullets are reached, so from the formatter a ``- `` line here is the
    objective's own text. Any other FIRST line is returned exactly as before. The
    only comparison is of a stripped line against those five exact strings;
    nothing else about a line is inspected. Display only, never identity."""
    if type(description) is not str:
        return None
    lines = (line for line in (raw.strip() for raw in description.splitlines())
             if line)
    first = next(lines, None)
    if first == _MISSION_OBJECTIVE_HEADING:
        first = next(lines, None)
        if first in _MISSION_SECTION_HEADINGS:
            first = None
    if first is None:
        return None
    return (first if len(first) <= MAX_OBJECTIVE_CHARS
            else first[:MAX_OBJECTIVE_CHARS - 1] + "…")


def _int_or_none(value):
    return value if type(value) is int else None


def _view(source, aliases, root, task):
    key = root_key(root)
    if task is NO_CURRENT_TASK:
        native_id = "root@" + key
        state = NO_CURRENT_TASK
        task_id = objective = started = version = None
        consistent, started_run = True, False
    else:
        task_id = task["id"]
        native_id = "%s@%s" % (task_id, key)
        state = task["status"]
        objective = _objective(task.get("description"))
        started = _int_or_none(task.get("started_at"))
        version = _int_or_none(task.get("version"))
        repo = task.get("repo")
        consistent = (type(repo) is str and os.path.isabs(repo)
                      and os.path.realpath(repo) == root)
        started_run = True
    return {
        "mission_ref": "%s:%s" % (source["source_id"], native_id),
        "source_id": source["source_id"],
        "native_id": native_id,
        "lifecycle": {"model": LIFECYCLE_MODEL, "state": state,
                      "standing": standing(state)},
        "producer": {
            "declared": source["producer"],
            "recorded": {
                "schema": SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "root_aliases": sorted(aliases),
                "membership": MEMBERSHIP_DERIVED,
            },
        },
        "revision": None,
        "objective": objective,
        "updated_at": None,
        "execution": {
            "task_id": task_id,
            "identity_source": IDENTITY_SOURCE,
            "workspace_realpath": root,
            "run_started": started_run,
            "receipt_recorded": started_run,
            "consistent": consistent,
            "record_ambiguous": False,
        },
        "native": {"task_record_version": version, "started_at": started},
    }


def _role_records(root, files):
    """``(runtime_roles, bound, problem)``: the runtime role mapping
    ``{logical: agent}``, the roles whose persisted binding is complete and
    belongs to that mapping (``runtime_snapshot._bindings``, the capture's
    own rules: ``identity.binding_gap``, the logical/agent match, stable
    record agreement), each with its binding entry; or a problem."""
    for name in (RUNTIME_FILE, BINDINGS_FILE):
        digest, value = files[name]
        if digest is not None and value is None:
            # Present but not a JSON object: unreadable, never "no roles".
            return {}, [], ROLE_RECORDS_NOT_AN_OBJECT
    runtime, recorded = files[RUNTIME_FILE][1], files[BINDINGS_FILE][1]
    if runtime is None:
        return {}, [], None
    agents = runtime.get("agents")
    try:
        bound = snapshot_module._bindings(root, 0, runtime, recorded, {})
    except snapshot_module._Refused as exc:
        return {}, [], exc.problem
    roles = (recorded or {}).get("roles") or {}
    runtime_roles = dict((logical, name) for logical, name in agents.items())
    return runtime_roles, [dict(b, entry=roles[b["logical"]])
                           for b in bound], None


def _recency(view):
    started = view["native"]["started_at"]
    return (view["execution"]["task_id"] is None, started is None,
            -(started or 0), view["execution"]["workspace_realpath"])


def _read(source, now, budget, record):
    """One reading of ``source`` (``_assemble``) under ONE ``_Reservation``.

    Bytes: settled EXACTLY ONCE, in ``finally``, on EVERY exit (accepted,
    degraded, refused, exhausted, or any exception), BEFORE any callback.
    Views: COMMITTED (``missions_remaining -= len(views)``) only after a
    fresh reading AND a normal ``record`` callback. If ``record`` raises,
    the byte debit stands, NO view is committed and the exception
    propagates (``sources.read_source`` then reports the source
    ``malformed / store_reader_failed`` with no missions): no partial fact
    is published. ``record`` (an ``Observation`` pass) receives what was
    requested (None when the registry could not be read), the outcome, the
    labelled ``read_account`` and the accepted roots' role records."""
    reservation = _Reservation(budget)
    seen = {"aliases": None}
    try:
        try:
            reading, views, roots = _assemble(source, now, budget,
                                              reservation, seen)
        finally:
            reservation.settle()
    except Exception:
        if record is not None:
            record({"source_id": source["source_id"], "at": now,
                    "health": contract.HEALTH_MALFORMED,
                    "problem": sources_module.PROBLEM_READER_FAILED,
                    "requested": seen["aliases"],
                    "read_account": reservation.account(), "roots": []})
        raise
    reading["read_account"] = reservation.account()
    if record is not None:
        record({"source_id": source["source_id"], "at": now,
                "health": reading["health"], "problem": reading["problem"],
                "requested": seen["aliases"],
                "read_account": reading["read_account"], "roots": roots,
                "excluded": list((reading.get("locators") or {}).get(
                    "excluded", ()))})
    budget.missions_remaining -= len(views)
    return reading


def _assemble(source, now, budget, reservation, seen):
    """``(reading, views, roots)``; a degradation is a reading with no view
    and no root (``_read`` settles the reservation and commits views)."""
    excluded, without_state = [], 0
    groups, order = {}, []
    try:
        entries = registry_entries(source["store_dir"], reservation, seen)
        for alias, path in entries:
            try:
                if path is None:
                    raise _Excluded(EXCLUDED_ENTRY_INVALID)
                identity = _physical(path)
                if identity not in groups:
                    task, files = _root_state(path, reservation)
                    groups[identity] = {"root": path, "aliases": [],
                                        "task": task, "files": files}
                    order.append(identity)
                groups[identity]["aliases"].append(alias)
            except _Excluded as exc:
                excluded.append({"alias": alias, "reason": exc.reason})
        views, roots = [], []
        for identity in order:
            group = groups[identity]
            if group["task"] is None:
                without_state += len(group["aliases"])
                continue
            view = _view(source, group["aliases"], group["root"],
                         group["task"])
            views.append(view)
            runtime_roles, bound, problem = _role_records(group["root"],
                                                          group["files"])
            roots.append({
                "view": view, "root": group["root"],
                "aliases": sorted(group["aliases"]),
                "digests": tuple(group["files"][name][0] for name in (
                    TASK_FILE, RUNTIME_FILE, BINDINGS_FILE)),
                "runtime_roles": runtime_roles, "bound": bound,
                "problem": problem})
        if len(views) > budget.missions_remaining:
            raise _Degraded(contract.HEALTH_UNAVAILABLE,
                            sources_module.PROBLEM_QUERY_MISSION_BUDGET)
    except _Degraded as exc:
        return (sources_module._reading(source, now, exc.health, exc.problem),
                [], [])
    views.sort(key=_recency)
    reading = sources_module._reading(source, now, contract.HEALTH_FRESH,
                                      None, views)
    reading["locators"] = {
        "schema": LOCATORS_SCHEMA,
        "version": LOCATORS_SCHEMA_VERSION,
        "registered_aliases": len(entries),
        "physical_roots": len(order),
        "shared_root_groups": sum(1 for identity in order
                                  if len(groups[identity]["aliases"]) > 1),
        "roots_with_current_task": sum(
            1 for view in views if view["execution"]["task_id"] is not None),
        "roots_without_current_task": sum(
            1 for view in views if view["execution"]["task_id"] is None),
        "aliases_without_herd_state": without_state,
        "excluded": excluded,
    }
    return reading, views, roots


def read_herdr_task(source, now, budget):
    """The reader for ``KIND`` (``sources.read_source`` contract): a
    reading with one view per eligible physical root, plus the
    ``locators`` account of every registered alias."""
    return _read(source, now, budget, None)


#: The reader table WITHOUT role evidence (one-shot readers, tools).
READERS = dict(sources_module.READERS)
READERS[KIND] = read_herdr_task


# -- one refresh's role evidence ------------------------------------------

class Observation(object):
    """One catalog refresh's role evidence: what each SOURCE PASS of this
    reader read (in memory only), ``pass_readers()`` once per pass."""

    def __init__(self):
        self.passes = []

    def pass_readers(self):
        """A reader table for ONE new source pass that keeps this source's
        role records (``READERS`` otherwise)."""
        current = {"readings": []}
        self.passes.append(current)

        def reader(source, now, budget):
            before = len(current["readings"])
            try:
                return _read(source, now, budget, current["readings"].append)
            except Exception:
                # A failure this source could not record itself: still one
                # reading of the pass, requested UNKNOWN (never zero).
                if len(current["readings"]) == before:
                    current["readings"].append({
                        "source_id": source["source_id"], "at": now,
                        "health": contract.HEALTH_MALFORMED,
                        "problem": sources_module.PROBLEM_READER_FAILED,
                        "requested": None, "read_account": None,
                        "roots": []})
                raise
        table = dict(sources_module.READERS)
        table[KIND] = reader
        return table

    def stable_names(self):
        """``(names, over_bound)``: the distinct agent names of the FIRST
        pass's complete role bindings, sorted, within the capture's own
        bounds (``runtime_snapshot.require_stable_names``); the count of
        names left out. The sorted order is a deterministic choice within
        the bound, never evidence."""
        if not self.passes:
            return None, 0
        names = sorted(set(
            role["agent"] for reading in self.passes[0]["readings"]
            for root in reading["roots"] for role in root["bound"]))
        kept = []
        for name in names[:snapshot_module.MAX_STABLE_NAMES]:
            if len(json.dumps(kept + [name]).encode("utf-8")) > (
                    snapshot_module.MAX_STABLE_NAMES_BYTES):
                break
            kept.append(name)
        return kept, len(names) - len(kept)

    def anchor_root(self):
        """The first task-native root (in scheduling order) of the first
        pass that recorded complete role bindings: the ONE root a capture
        may observe so its listing exists when no team needs observing."""
        if not self.passes:
            return None
        for reading in self.passes[0]["readings"]:
            ordered = sorted(reading["roots"],
                             key=lambda root: _recency(root["view"]))
            for root in ordered:
                if root["bound"]:
                    return root["root"]
        return None


def _team(record, missions_by_ref, missions):
    """``(state, reason)`` of the TEAM a root's roles belong to: ``bound``
    (its team is represented by a roster), ``ambiguous``, or another
    binding state with its reason."""
    mission = missions_by_ref.get(record["view"]["mission_ref"])
    if mission is None:
        return contract.BINDING_UNVERIFIED, PROBLEM_SNAPSHOT_NOT_FRESH
    binding = mission["binding"]
    task_id = record["view"]["execution"]["task_id"]
    if binding["reason"] == "covered_by_canonical_record":
        covering = [m["binding"] for m in missions
                    if m["lifecycle"]["model"]
                    in contract.CANONICAL_LIFECYCLE_MODELS
                    and m["execution"]["task_id"] == task_id
                    and m["execution"]["workspace_realpath"]
                    == record["root"]]
        return _strongest(covering, binding)
    if binding["reason"] == "covered_by_parent_chain":
        chains = [chain["binding"] for m in missions
                  for chain in m.get("task_chains") or ()
                  if chain["chain_task_id"] == task_id
                  and (chain["root"] or {}).get("path") == record["root"]]
        return _strongest(chains, binding)
    return binding["state"], binding["reason"]


def _strongest(bindings, fallback):
    states = [b["state"] for b in bindings]
    if contract.BINDING_BOUND in states:
        return contract.BINDING_BOUND, None
    if contract.BINDING_AMBIGUOUS in states:
        return contract.BINDING_AMBIGUOUS, [
            b["reason"] for b in bindings
            if b["state"] == contract.BINDING_AMBIGUOUS][0]
    if bindings:
        return bindings[0]["state"], bindings[0]["reason"]
    return fallback["state"], fallback["reason"]


def _assignment(record, team, over_capacity):
    view = record["view"]
    task_id = view["execution"]["task_id"]
    status = None if task_id is None else view["lifecycle"]["state"]
    task_ref = None if task_id is None else view["mission_ref"]
    standing_ = view["lifecycle"]["standing"]
    if task_id is None:
        state, reason = contract.ASSIGNMENT_UNASSIGNED, AS_NO_TASK_RECORD
    elif standing_ in (contract.STANDING_INACTIVE, contract.STANDING_TERMINAL):
        state, reason = contract.ASSIGNMENT_UNASSIGNED, AS_NOT_CURRENT
    else:
        state = contract.ASSIGNMENT_UNVERIFIED_REFERENCE
        if standing_ == contract.STANDING_UNKNOWN_FUTURE:
            reason = AS_STATUS_UNRECOGNIZED
        elif team[0] == contract.BINDING_BOUND:
            reason = AS_NOT_IN_TEAM_ROSTER
        elif record["root"] in over_capacity:
            reason = AS_OVER_CAPACITY
        else:
            reason = team[1]
    return {"state": state, "reason": reason, "task_ref": task_ref,
            "task_id": task_id, "task_status": status}


def _native_info(agent, cwd):
    """The engine's own probe shape for ``identity.classify``, rebuilt from
    the SAME listing row: its snapshot fields plus its native ``cwd``."""
    record = {"name": agent.agent, "cwd": cwd,
              "workspace_id": agent.workspace_id, "pane_id": agent.pane_id,
              "agent_session": {"value": agent.session}}
    return {"status": agent.status, "raw": {"result": {"agent": record}}}


def _identity(role, prior, rows, native_cwd, requested, live_sessions):
    """``(state, reason)`` of ONE role against ONE listing (never inferred
    from another role). ``prior``: None (the root was not read in the first
    source pass), ``"changed"`` (its records differ) or the role's own key
    in the first pass (None when that pass had no such complete binding)."""
    if role is None:
        return contract.IDENTITY_UNVERIFIED, ID_BINDING_INCOMPLETE
    if prior is None:
        return contract.IDENTITY_UNAVAILABLE, ID_NOT_IN_FIRST_PASS
    if prior == "changed" or prior != _role_key(role):
        return contract.IDENTITY_UNVERIFIED, ID_CHANGED
    if role["pane_id"] is None or role["workspace_id"] is None:
        return contract.IDENTITY_UNVERIFIED, ID_STABLE_RECORDS_DISAGREE
    if len(rows) > 1:
        return contract.IDENTITY_AMBIGUOUS, ID_NAME_DUPLICATED
    agent = rows[0]
    if agent.session is None:
        return contract.IDENTITY_UNVERIFIED, ID_SESSION_UNKNOWN
    if live_sessions.get(agent.session, 0) > 1:
        return contract.IDENTITY_AMBIGUOUS, ID_SESSION_SHARED
    if role["agent"] not in requested:
        return contract.IDENTITY_UNAVAILABLE, ID_NOT_REQUESTED
    cwds = native_cwd.get(role["agent"])
    if cwds is None or len(cwds) != 1:
        return contract.IDENTITY_UNAVAILABLE, ID_NOT_OBSERVED
    verdict = engine_identity.classify(role["logical"], role["entry"],
                                       _native_info(agent, cwds[0]))
    if verdict.verdict == engine_identity.VERDICT_PRESENT:
        return contract.IDENTITY_VERIFIED, ID_PRESENT
    if verdict.verdict == engine_identity.VERDICT_REPLACED:
        return contract.IDENTITY_UNVERIFIED, ID_SESSION_REPLACED
    if verdict.verdict == engine_identity.VERDICT_UNBOUND:
        return contract.IDENTITY_UNVERIFIED, ID_BINDING_INCOMPLETE
    return contract.IDENTITY_UNVERIFIED, "%s:%s" % (ID_DEGRADED,
                                                    verdict.problem)


def _role_key(role):
    return (role["logical"], role["agent"], role["session"], role["pane_id"],
            role["workspace_id"], json.dumps(role["entry"], sort_keys=True))


def _excluded_unreadable(readings):
    """How many registered roots of these readings were EXCLUDED because their
    role records could not be read (``UNREADABLE_EXCLUSIONS``)."""
    return sum(1 for r in readings for entry in r.get("excluded", ())
               if entry["reason"] in UNREADABLE_EXCLUSIONS)


def _roster_empty(readings, fresh):
    """True when EVERY task-native reading of the pass is ``fresh``, no accepted
    root records a role or holds unreadable role records, AND no registered root
    was excluded for being unreadable: nothing readable is never "records
    nothing"."""
    return len(fresh) == len(readings) and not _excluded_unreadable(fresh) \
        and not any(root["runtime_roles"] or root["problem"] is not None
                    for r in fresh for root in r["roots"])


def runtime_agents(observation, missions, reading, now, capture,
                   sources=None):
    """The ``runtime_agents`` block (``contract``) of one refresh.

    ``observation``: this refresh's ``Observation`` (two source passes for
    a verified identity); ``missions``: the classified missions of the
    SECOND pass; ``reading``: its runtime snapshot reading; ``capture``:
    ``{"native_cwd", "requested", "names_over_bound", "over_capacity"}``
    from the runtime capture (``native_cwd`` None when not observed);
    ``sources``: the answer pass's source blocks. A task-native source in
    them with no reading of this pass (the version 2 pass refused its hold
    BEFORE the reader ran) is counted as a reading whose registry is
    UNREAD, never as an absent source."""
    readings = list(observation.passes[-1]["readings"]) if (
        observation.passes) else []
    observed = set(r["source_id"] for r in readings)
    for block in sources or ():
        if block["kind"] == KIND and block["source_id"] not in observed:
            readings.append({"source_id": block["source_id"], "at": now,
                             "health": block["health"],
                             "problem": block["problem"], "requested": None,
                             "read_account": None, "roots": []})
    if not readings:
        # No task-native source is registered at all (the answer pass's
        # source blocks hold none either): said as such, never as "no role
        # records", which a registered source could also mean.
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_NOT_APPLICABLE,
            contract.PROBLEM_TASK_NATIVE_SOURCE_NOT_REGISTERED)
    second = [r for r in readings if r["health"] == contract.HEALTH_FRESH]
    # What the registries REQUESTED, from every reading of this pass whose
    # registry was read (degraded ones included); a reading whose registry
    # could not be read requested an unknown number and adds nothing.
    requested = sum(r["requested"] for r in readings
                    if r["requested"] is not None)
    unread = sum(1 for r in readings if r["requested"] is None)
    if not second:
        # Every task-native source of this pass degraded (its own source
        # block says how): nothing was read into the answer, so nothing is
        # listed.
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_UNAVAILABLE,
            "%s:%s" % (PROBLEM_SOURCE_NOT_FRESH, readings[0]["problem"]),
            counts={"roots_requested": requested,
                    "registries_unread": unread})
    first = [r for r in observation.passes[0]["readings"]
             if r["health"] == contract.HEALTH_FRESH] if len(
        observation.passes) > 1 else None
    over_capacity = set(capture.get("over_capacity") or ())
    counts = {
        "roots_requested": requested,
        "registries_unread": unread,
        "roots_read": sum(len(r["roots"]) for r in second),
        "roles_recorded": sum(len(root["runtime_roles"]) for r in second
                              for root in r["roots"]),
        "roots_role_records_unreadable": sum(
            1 for r in second for root in r["roots"]
            if root["problem"] is not None) + _excluded_unreadable(second),
        "roots_over_capacity": len(over_capacity),
        "names_over_bound": capture.get("names_over_bound") or 0,
    }
    first_at = None if not first else min(r["at"] for r in first)
    second_at = max(r["at"] for r in second)
    if first is None:
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_UNAVAILABLE, PROBLEM_SINGLE_PASS,
            counts=counts, second_pass_at=second_at)
    if _roster_empty(readings, second) and (
            not reading.is_live() or capture.get("native_cwd") is None):
        # Only where nothing could be observed: a capture that DID observe
        # stays ``observed`` with zero agents below.
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_NOT_APPLICABLE, PROBLEM_NO_ROLE_RECORDS,
            counts=counts, first_pass_at=first_at, second_pass_at=second_at)
    if not reading.is_live():
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_UNAVAILABLE, PROBLEM_SNAPSHOT_NOT_FRESH,
            counts=counts, first_pass_at=first_at, second_pass_at=second_at)
    native_cwd = capture.get("native_cwd")
    if native_cwd is None:
        return contract.runtime_agents_block(
            contract.RUNTIME_AGENTS_UNAVAILABLE, PROBLEM_NATIVE_NOT_OBSERVED,
            counts=counts, observed_at=reading.observed_at,
            first_pass_at=first_at, second_pass_at=second_at)
    requested = set(capture.get("requested") or ())
    earlier = {}
    for r in first:
        for root in r["roots"]:
            earlier[(r["source_id"], root["root"])] = root
    rows_by_name, live_sessions = {}, {}
    for agent in reading.agents:
        rows_by_name.setdefault(agent.agent, []).append(agent)
        if agent.session is not None:
            live_sessions[agent.session] = live_sessions.get(
                agent.session, 0) + 1
    members = set()
    for mission in missions:
        for member in mission["members"]:
            members.add((member["agent"], member["session"]["recorded"]))
    missions_by_ref = dict((m["mission_ref"], m) for m in missions)
    entries = []
    for r in second:
        for record in r["roots"]:
            team = _team(record, missions_by_ref, missions)
            prior = earlier.get((r["source_id"], record["root"]))
            prior_roles = {} if prior is None else dict(
                (role["logical"], _role_key(role)) for role in prior["bound"])
            bound = dict((role["logical"], role) for role in record["bound"])
            for logical, name in sorted(record["runtime_roles"].items()):
                rows = rows_by_name.get(name, [])
                if not rows:
                    continue                   # not live: counted, not listed
                counts["roles_live"] = counts.get("roles_live", 0) + 1
                role = bound.get(logical)
                # Skipped ONLY when this role ITSELF is a member (exact agent
                # and recorded session): a bound team never absorbs a role
                # its roster does not hold, which is decided on its own.
                if role is not None and (
                        role["agent"], role["session"]) in members:
                    counts["represented_by_teams"] = counts.get(
                        "represented_by_teams", 0) + 1
                    continue
                if prior is None:
                    previous = None
                elif prior["digests"] != record["digests"]:
                    previous = "changed"
                else:
                    previous = prior_roles.get(logical, "changed")
                state, reason = _identity(role, previous, rows, native_cwd,
                                          requested, live_sessions)
                if team[0] == contract.BINDING_AMBIGUOUS and (
                        state == contract.IDENTITY_VERIFIED):
                    state, reason = (contract.IDENTITY_AMBIGUOUS,
                                     "%s:%s" % (ID_TEAM_AMBIGUOUS, team[1]))
                agent = rows[0] if len(rows) == 1 else None
                entries.append({
                    "agent_ref": "%s:%s#%s" % (r["source_id"],
                                               root_key(record["root"]),
                                               logical),
                    "source_id": r["source_id"],
                    "root_ref": record["view"]["mission_ref"],
                    "root_aliases": list(record["aliases"]),
                    "workspace_realpath": record["root"],
                    "logical": logical,
                    "role": None if role is None else role["role"],
                    "agent": name,
                    "session": None if role is None else role["session"],
                    "pane_id": None if role is None else role["pane_id"],
                    "workspace_id": (None if role is None
                                     else role["workspace_id"]),
                    "provider": None if agent is None else agent.provider,
                    "activity": (None if agent is None
                                 else contract.RUNTIME_STATUS_ACTIVITY.get(
                                     agent.status,
                                     contract.ACTIVITY_UNKNOWN_FUTURE)),
                    "identity": {"state": state, "reason": reason},
                    "assignment": _assignment(record, team, over_capacity),
                })
    verified = {}
    for entry in entries:
        if entry["identity"]["state"] == contract.IDENTITY_VERIFIED:
            key = (entry["agent"], entry["session"])
            verified[key] = verified.get(key, 0) + 1
    for entry in entries:
        if entry["identity"]["state"] == contract.IDENTITY_VERIFIED and (
                verified[(entry["agent"], entry["session"])] > 1):
            entry["identity"] = {"state": contract.IDENTITY_AMBIGUOUS,
                                 "reason": ID_MULTIPLE_ROOTS}
    entries.sort(key=lambda e: e["agent_ref"])
    for entry in entries:
        key = entry["identity"]["state"]
        counts[key] = counts.get(key, 0) + 1
    return contract.runtime_agents_block(
        contract.RUNTIME_AGENTS_OBSERVED, None, reading.observed_at,
        first_at, second_at, counts, entries[:contract.MAX_RUNTIME_AGENTS],
        max(len(entries) - contract.MAX_RUNTIME_AGENTS, 0))
