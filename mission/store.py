"""One atomic authority document for the Mission registry and ledger.

``missions.json`` is ONE JSON document holding the Mission registry,
the authorization records, the append-only Authority Ledger, and the
DI-issued id reservations:

    {"mission_store_schema_version": 1,
     "missions": {<mission_id>: <mission record>},
     "authorizations": {<authorization_id>: <authorization record>},
     "authority_ledger": [<entry>, ...],
     "reservations": {<request or decision id>: <reservation>}}

Registry and ledger therefore commit in the SAME ``os.replace`` and can
never tear apart. A reservation records the id's kind, the authenticated
context that asked for it, and ``consumed_by``: ``null`` while unused,
the Mission id once a request id created a Mission, the decision id
itself once a decision id was applied (the decision record then lives in
its Mission's history). Reservations are never evicted. The file lives in an injected protected directory
(mode 600 in a mode 700 directory) and has its own lock file
(``missions.lock``), so it never serializes against another store.
Writes go through the shared stdlib-only ``workflow_authority.atomic``
primitives; every writer holds ``exclusive_store_lock`` around its whole
load-modify-save cycle.

Loading fails closed: bad JSON, an unknown schema version, an unknown or
missing top-level key, any invalid record or ledger entry, a cross-
record inconsistency, a decision history that does not reproduce the
stored state/authorizations/ledger (``reconcile_mission_history``, the
same function the central validator runs), or a group/other-accessible
mode on the FILE or on the EXISTING DIRECTORY raises ``MissionStoreError``
and the file is NEVER silently reinitialized. The directory boundary is
enforced before every read, lock, and write. A
missing file yields a fresh default document. Every record and every
ledger entry is validated on every load AND every save; ``save``
validates before anything touches the filesystem, so an invalid
document can never clobber a valid store.

Hard caps are module constants, never derived from input. At a cap the
store REFUSES; authority history and reservations are never pruned or
evicted (a retry might still carry a reservation). Counts are exact.
"""

import json
import os
import stat

from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

from mission import authorization as authorization_module
from mission import manifest
from mission import record
from mission.manifest import (  # noqa: F401 (re-exported bounds)
    MAX_MISSION_DECISIONS,
    MAX_MISSION_REVISIONS,
)

MISSION_STORE_SCHEMA_VERSION = 1
MISSIONS_FILE_NAME = "missions.json"
MISSIONS_LOCK_FILE_NAME = "missions.lock"
TOP_LEVEL_KEYS = (
    "mission_store_schema_version", "missions", "authorizations",
    "authority_ledger", "reservations",
)

# Hard caps, never derived from input. Exact-value pinned.
MAX_MISSION_RECORDS = 1024
MAX_AUTHORIZATION_RECORDS = 4096
MAX_AUTHORITY_LEDGER_ENTRIES = 16384
MAX_RESERVED_REQUEST_IDS = 4096
MAX_RESERVED_DECISION_IDS = 4096

RESERVATION_KIND_REQUEST = "request"
RESERVATION_KIND_DECISION = "decision"
RESERVATION_KINDS = (RESERVATION_KIND_REQUEST, RESERVATION_KIND_DECISION)
RESERVATION_KEYS = ("reserved_at", "kind", "context", "consumed_by")

PROBLEM_STORE_UNREADABLE = authorization_module.PROBLEM_STORE_UNREADABLE
PROBLEM_STORE_FULL = "mission_store_full"

# Any group/other access bit: the store carries authority records.
_FORBIDDEN_STORE_MODE_BITS = 0o077


class MissionStoreError(Exception):
    """The Mission store is unreadable, malformed, or full; ``problem`` is
    ``mission_store_unreadable`` or ``mission_store_full``."""

    def __init__(self, message, problem=PROBLEM_STORE_UNREADABLE):
        super(MissionStoreError, self).__init__(message)
        self.problem = problem


def default_document():
    return {
        "mission_store_schema_version": MISSION_STORE_SCHEMA_VERSION,
        "missions": {},
        "authorizations": {},
        "authority_ledger": [],
        "reservations": {},
    }


def _unreadable(path, message):
    raise MissionStoreError(
        "mission store %s %s; move the file aside (keeping it for"
        " inspection) — it is NOT safe to delete it: it carries authority"
        " records" % (path, message), PROBLEM_STORE_UNREADABLE,
    )


def _full(path, message):
    raise MissionStoreError("mission store %s %s" % (path, message),
                            PROBLEM_STORE_FULL)


def validate_reservation(value, location):
    record.require_dict(value, location)
    record.require_closed_keys(value, RESERVATION_KEYS, location)
    record.require_timestamp(value["reserved_at"], location + ".reserved_at")
    record.require_member(value["kind"], RESERVATION_KINDS, location + ".kind")
    record.validate_context_dict(value["context"], location + ".context")
    consumed = value["consumed_by"]
    if consumed is not None:
        prefix = (record.MISSION_ID_PREFIX
                  if value["kind"] == RESERVATION_KIND_REQUEST
                  else record.DECISION_ID_PREFIX)
        record.require_id(consumed, prefix, location + ".consumed_by")
    return value


def validate_document(document, path="<document>"):
    """Every record, entry and cross-reference, or MissionStoreError."""
    try:
        _validate_document(document, path)
    except record.MissionError as exc:
        _unreadable(path, "is malformed: %s" % exc)
    return document


def _validate_document(document, path):
    if not isinstance(document, dict):
        _unreadable(path, "must contain a JSON object, not %s"
                    % type(document).__name__)
    version = document.get("mission_store_schema_version")
    if isinstance(version, bool) or version != MISSION_STORE_SCHEMA_VERSION:
        _unreadable(path, "has mission_store_schema_version %r; this layer"
                    " understands only %d" % (version, MISSION_STORE_SCHEMA_VERSION))
    unknown = sorted(set(document) - set(TOP_LEVEL_KEYS))
    if unknown:
        _unreadable(path, "has unknown top-level keys: %s; the key set is"
                    " closed" % ", ".join(map(repr, unknown)))
    missing = sorted(set(TOP_LEVEL_KEYS) - set(document))
    if missing:
        _unreadable(path, "is missing required keys: %s"
                    % ", ".join(map(repr, missing)))
    missions = document["missions"]
    authorizations = document["authorizations"]
    ledger = document["authority_ledger"]
    reservations = document["reservations"]
    for name, value, kind in (("missions", missions, dict),
                              ("authorizations", authorizations, dict),
                              ("authority_ledger", ledger, list),
                              ("reservations", reservations, dict)):
        if not isinstance(value, kind):
            _unreadable(path, "key %r must be a JSON %s, not %s"
                        % (name, kind.__name__, type(value).__name__))
    if len(missions) > MAX_MISSION_RECORDS:
        _full(path, "holds %d missions; the hard bound is %d"
              % (len(missions), MAX_MISSION_RECORDS))
    if len(authorizations) > MAX_AUTHORIZATION_RECORDS:
        _full(path, "holds %d authorizations; the hard bound is %d"
              % (len(authorizations), MAX_AUTHORIZATION_RECORDS))
    if len(ledger) > MAX_AUTHORITY_LEDGER_ENTRIES:
        _full(path, "holds %d ledger entries; the hard bound is %d"
              % (len(ledger), MAX_AUTHORITY_LEDGER_ENTRIES))
    request_reservations = 0
    decision_reservations = 0
    for reserved_id, reservation in reservations.items():
        where = "reservation %r" % reserved_id
        validate_reservation(reservation, where)
        prefix = (record.REQUEST_ID_PREFIX
                  if reservation["kind"] == RESERVATION_KIND_REQUEST
                  else record.DECISION_ID_PREFIX)
        record.require_id(reserved_id, prefix, where + " key")
        if reservation["kind"] == RESERVATION_KIND_REQUEST:
            request_reservations += 1
        else:
            decision_reservations += 1
    if request_reservations > MAX_RESERVED_REQUEST_IDS:
        _full(path, "holds %d reserved request ids; the hard bound is %d and"
              " a reservation is never evicted"
              % (request_reservations, MAX_RESERVED_REQUEST_IDS))
    if decision_reservations > MAX_RESERVED_DECISION_IDS:
        _full(path, "holds %d reserved decision ids; the hard bound is %d and"
              " a reservation is never evicted"
              % (decision_reservations, MAX_RESERVED_DECISION_IDS))
    request_ids = {}
    for mission_id, mission in missions.items():
        manifest.validate_mission_record(mission, "mission %r" % mission_id)
        if mission["mission_id"] != mission_id:
            _unreadable(path, "mission keyed %r carries mission_id %r"
                        % (mission_id, mission["mission_id"]))
        if mission["request_id"] in request_ids:
            _unreadable(path, "missions %r and %r share request id %r"
                        % (request_ids[mission["request_id"]], mission_id,
                           mission["request_id"]))
        request_ids[mission["request_id"]] = mission_id
        reservation = reservations.get(mission["request_id"])
        if reservation is None or reservation["kind"] != (
            RESERVATION_KIND_REQUEST
        ) or reservation["consumed_by"] != mission_id:
            _unreadable(path, "mission %r request id %r is not a consumed"
                        " request reservation" % (mission_id, mission["request_id"]))
        for decision in mission["decisions"]:
            reservation = reservations.get(decision["decision_id"])
            if reservation is None or reservation["kind"] != (
                RESERVATION_KIND_DECISION
            ) or reservation["consumed_by"] != decision["decision_id"]:
                _unreadable(path, "mission %r decision %r is not a consumed"
                            " decision reservation"
                            % (mission_id, decision["decision_id"]))
        for authorization_id in mission["authorization_ids"]:
            authorization = authorizations.get(authorization_id)
            if authorization is None or authorization.get("mission_id") != (
                mission_id
            ):
                _unreadable(path, "mission %r references authorization %r,"
                            " which is absent or bound to another mission"
                            % (mission_id, authorization_id))
    for authorization_id, authorization in authorizations.items():
        authorization_module.validate_authorization_record(
            authorization, "authorization %r" % authorization_id
        )
        if authorization["authorization_id"] != authorization_id:
            _unreadable(path, "authorization keyed %r carries id %r"
                        % (authorization_id, authorization["authorization_id"]))
        mission = missions.get(authorization["mission_id"])
        if mission is None or authorization_id not in mission["authorization_ids"]:
            _unreadable(path, "authorization %r is not referenced by its"
                        " mission %r" % (authorization_id,
                                         authorization["mission_id"]))
        if manifest.revision_entry(mission, authorization["revision"]) is None:
            _unreadable(path, "authorization %r binds revision %d, which"
                        " mission %r does not hold"
                        % (authorization_id, authorization["revision"],
                           authorization["mission_id"]))
    seen_entries = set()
    for index, entry in enumerate(ledger):
        authorization_module.validate_ledger_entry(
            entry, "authority_ledger[%d]" % index
        )
        if entry["entry_id"] in seen_entries:
            _unreadable(path, "authority_ledger[%d] repeats entry id %r"
                        % (index, entry["entry_id"]))
        seen_entries.add(entry["entry_id"])
        if entry["mission_id"] not in missions:
            _unreadable(path, "authority_ledger[%d] names unknown mission %r"
                        % (index, entry["mission_id"]))
        if entry["authorization_id"] is not None and (
            entry["authorization_id"] not in authorizations
        ):
            _unreadable(path, "authority_ledger[%d] names unknown"
                        " authorization %r" % (index, entry["authorization_id"]))
    # The decision history is the authority of record: every mission's
    # stored state, revisions, authorizations, reservations and ledger
    # must be exactly what replaying its decisions produces (the SAME
    # function the central validator runs; no second checking path).
    disagreement = authorization_module.reconcile_registry(document)
    if disagreement is not None:
        _unreadable(path, "does not reconcile (%s): %s" % disagreement)
    for mission_id, mission in missions.items():
        disagreement = authorization_module.reconcile_mission_history(
            document, mission
        )
        if disagreement is not None:
            _unreadable(path, "does not reconcile (%s): %s" % disagreement)


def _refuse_open_permissions(path):
    mode = os.stat(path).st_mode
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        _unreadable(path, "is accessible by group/other (mode %o); it carries"
                    " authority records. Fix with: chmod 600 %r"
                    % (stat.S_IMODE(mode), path))


def _refuse_open_directory(directory):
    """The protected-directory boundary: an EXISTING store directory that
    group/other can reach is refused before any read, lock, or write. A
    writable containing directory would let another local account replace
    the authority document or the lock regardless of the file's own mode.
    A missing directory is fine: it is created mode 700 on first write."""
    try:
        mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        _unreadable(directory, "is not a directory")
    if mode & _FORBIDDEN_STORE_MODE_BITS:
        raise MissionStoreError(
            "mission store directory %s is accessible by group/other (mode"
            " %o); it holds the authority document and its lock, so nothing"
            " is read, locked, or written. Fix with: chmod 700 %r"
            % (directory, stat.S_IMODE(mode), directory),
            PROBLEM_STORE_UNREADABLE,
        )


class MissionStore(object):
    """Atomic load/save of the one Mission store document."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, MISSIONS_FILE_NAME)

    def lock(self):
        """The cross-process lock every load-modify-save cycle holds. The
        directory boundary is checked before the lock file is touched."""
        _refuse_open_directory(self.directory)
        return exclusive_store_lock(self.directory, MISSIONS_LOCK_FILE_NAME)

    def load(self):
        _refuse_open_directory(self.directory)
        if not os.path.exists(self.path):
            return default_document()
        _refuse_open_permissions(self.path)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            _unreadable(self.path, "could not be read as JSON (%s)" % exc)
        return validate_document(document, self.path)

    def save(self, document):
        _refuse_open_directory(self.directory)
        validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".missions-")
