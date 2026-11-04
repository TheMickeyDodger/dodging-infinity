"""The retention coordinator store: ``mission_retention.json``.

One record per Mission whose local retention DI is tracking, in the SAME directory as the stores
that Mission's payload lives in (the panel state directory for Mission Core Missions, the workflow
store directory for workflow-store Missions). It is the coordinator of reclamation, never a copy of
the payload:

- it dates terminal eligibility (``terminal_at`` from the source record, or, where the source dates
  nothing trustworthy, ``first_eligible_observed_at``: the first local observation of PROVEN
  eligibility, never backdated from a file time and never zero);
- it names the positive basis that made the Mission eligible and the source field that supplies it;
- it walks one Mission through ``observed`` -> ``reclaiming`` -> ``reclaimed`` (automatic, summary
  kept) or ``deleting`` -> ``deleted`` (owner request, summary removed), so a process that dies
  mid-reclamation resumes the same Mission on restart instead of leaving it half-reduced and
  unexplained;
- it keeps the retained SUMMARY (final status, verdict, CI, comments: each recorded or honestly not
  recorded locally) for a reclaimed Mission, and NOTHING but the tombstone for a deleted one.

Its own lock (``mission_retention.lock``) is the COORDINATOR lock: taken first, before the
per-Mission run guard and before each owned store's own lock, never instead of one. No other writer
takes it, so it adds no reverse order to any existing lock.

Like its siblings it is mode 600 in a mode 700 directory, validated against a closed schema on every
load AND every save, written only through ``workflow_authority.atomic``, and it fails closed: an
unreadable or invalid document raises and is never silently reinitialized. It is capped and REFUSES
at the cap; nothing in it is ever evicted (a tombstone that disappeared could let a deleted Mission
read as intact again).
"""

import json
import os
import stat

from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

# Version 2 (round 10): a retained fact says WHICH of five things is true, never folding a third state
# into a confident one. Version 1 (two states) still loads, upgraded honestly (``_upgrade_v1``).
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSIONS = (1,)
RETENTION_FILE_NAME = "mission_retention.json"
RETENTION_LOCK_FILE_NAME = "mission_retention.lock"

# Hard bounds, never derived from input. One record per Mission: the owned stores are themselves
# capped (missions 1024, workflows 64 live at once), so this bound is reached only after thousands of
# reclaimed Missions; at it the store REFUSES and reclamation stops, reported, never evicting.
MAX_RETENTION_RECORDS = 4096
MAX_SOURCE_CHARS = 200
MAX_DETAIL_CHARS = 2000
MAX_ID_CHARS = 128
# At most this many other Missions a reclaimed Mission's shared conversation is still kept for.
MAX_RETAINED_FOR = 64

FAMILY_MISSION_CORE = "mission_core"
FAMILY_WORKFLOW_STORE = "workflow_store"
FAMILIES = (FAMILY_MISSION_CORE, FAMILY_WORKFLOW_STORE)

PHASE_OBSERVED = "observed"
PHASE_RECLAIMING = "reclaiming"
PHASE_RECLAIMED = "reclaimed"
PHASE_DELETING = "deleting"
PHASE_DELETED = "deleted"
PHASES = (PHASE_OBSERVED, PHASE_RECLAIMING, PHASE_RECLAIMED, PHASE_DELETING, PHASE_DELETED)
# The phases whose payload is (being) removed: a Mission in one of these never reads as intact.
REDUCED_PHASES = (PHASE_RECLAIMING, PHASE_RECLAIMED, PHASE_DELETING, PHASE_DELETED)

# A retained fact's state: each a DIFFERENT claim, and only ``recorded`` carries a detail.
#  - recorded: its source was read, and this is what it holds;
#  - not_recorded_locally: CONFIRMED absence (its source was read, and holds none);
#  - not_read: its source was not consulted (not configured for this process): nothing is claimed;
#  - unreadable: its source is there and could not be read: a record may exist;
#  - not_known: kept by an earlier version that did not tell those apart (an upgraded version-1
#    "not recorded"), so absence was never established.
SUMMARY_RECORDED = "recorded"
SUMMARY_NOT_RECORDED = "not_recorded_locally"
SUMMARY_NOT_READ = "not_read"
SUMMARY_UNREADABLE = "unreadable"
SUMMARY_NOT_KNOWN = "not_known"
SUMMARY_STATES = (SUMMARY_RECORDED, SUMMARY_NOT_RECORDED, SUMMARY_NOT_READ, SUMMARY_UNREADABLE,
                  SUMMARY_NOT_KNOWN)
# The exact source texts version 1's CI producer wrote when it did NOT read the delivery store (the
# only version-1 "not recorded" whose real meaning the text itself establishes).
LEGACY_V1_CI_NOT_CONFIGURED = "not read: no PR delivery store is configured for this process"
LEGACY_V1_CI_UNREADABLE_PREFIX = "not read: "
LEGACY_V1_CI_UNREADABLE_MARK = " could not be read as the delivery store"

DOCUMENT_KEYS = ("schema_version", "records")
RECORD_KEYS = (
    "key", "family", "record_id", "first_eligible_observed_at", "terminal_at",
    "terminal_at_source", "basis", "basis_source", "phase", "phase_at", "summary", "retained_for",
)
SUMMARY_KEYS = ("final_state", "final_state_source", "verdict", "ci", "comments")
SUMMARY_FACT_KEYS = ("state", "detail", "source")

PROBLEM_UNREADABLE = "mission_retention_unreadable"
PROBLEM_FULL = "mission_retention_full"

_FORBIDDEN_MODE_BITS = 0o077


class RetentionStoreError(Exception):
    """The retention document is unreadable, invalid, or full. Nothing was written."""

    def __init__(self, message, problem=PROBLEM_UNREADABLE):
        super(RetentionStoreError, self).__init__(message)
        self.problem = problem


def _bad(where, message):
    raise RetentionStoreError("%s: %s" % (where, message))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _short(value, limit, where, allow_none=False):
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value or len(value) > limit:
        _bad(where, "must be a non-empty string of at most %d characters" % limit)


def _closed(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        _bad(where, "must be an object with exactly the keys %s" % ", ".join(keys))


def retention_key(family, record_id):
    """The one key a Mission's retention record is filed under."""
    return "%s:%s" % (family, record_id)


def default_document():
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def _validate_fact(fact, where):
    _closed(fact, SUMMARY_FACT_KEYS, where)
    if fact["state"] not in SUMMARY_STATES:
        _bad(where + ".state", "must be one of %s" % ", ".join(SUMMARY_STATES))
    _short(fact["detail"], MAX_DETAIL_CHARS, where + ".detail", allow_none=True)
    _short(fact["source"], MAX_DETAIL_CHARS, where + ".source")
    if fact["state"] == SUMMARY_RECORDED and fact["detail"] is None:
        _bad(where, "a recorded fact says what was recorded")
    if fact["state"] != SUMMARY_RECORDED and fact["detail"] is not None:
        _bad(where, "only a recorded fact carries a detail; any other says where it was looked for")


def _validate_summary(summary, where):
    _closed(summary, SUMMARY_KEYS, where)
    _short(summary["final_state"], MAX_SOURCE_CHARS, where + ".final_state")
    _short(summary["final_state_source"], MAX_SOURCE_CHARS, where + ".final_state_source")
    for name in ("verdict", "ci", "comments"):
        _validate_fact(summary[name], "%s.%s" % (where, name))


def _validate_record(key, record, where):
    _closed(record, RECORD_KEYS, where)
    if record["family"] not in FAMILIES:
        _bad(where + ".family", "must be one of %s" % ", ".join(FAMILIES))
    _short(record["record_id"], MAX_ID_CHARS, where + ".record_id")
    if record["key"] != key or key != retention_key(record["family"], record["record_id"]):
        _bad(where, "key must be <family>:<record_id> and its own map key")
    if not _is_int(record["first_eligible_observed_at"]):
        _bad(where + ".first_eligible_observed_at", "must be a non-negative integer")
    if record["terminal_at"] is not None and not _is_int(record["terminal_at"]):
        _bad(where + ".terminal_at", "must be null or a non-negative integer")
    if (record["terminal_at"] is None) != (record["terminal_at_source"] is None):
        _bad(where, "terminal_at and terminal_at_source are both set or both null")
    _short(record["terminal_at_source"], MAX_SOURCE_CHARS, where + ".terminal_at_source",
           allow_none=True)
    _short(record["basis"], MAX_SOURCE_CHARS, where + ".basis")
    _short(record["basis_source"], MAX_SOURCE_CHARS, where + ".basis_source")
    if record["phase"] not in PHASES:
        _bad(where + ".phase", "must be one of %s" % ", ".join(PHASES))
    if not _is_int(record["phase_at"]):
        _bad(where + ".phase_at", "must be a non-negative integer")
    retained = record["retained_for"]
    if not isinstance(retained, list) or len(retained) > MAX_RETAINED_FOR or len(set(retained)) != len(
        retained
    ):
        _bad(where + ".retained_for", "must be a list of at most %d distinct ids" % MAX_RETAINED_FOR)
    for other in retained:
        _short(other, MAX_ID_CHARS, where + ".retained_for[]")
    if retained and record["phase"] != PHASE_RECLAIMED:
        # Only a reclaimed Mission can be waiting on another to release a shared conversation.
        _bad(where + ".retained_for", "is non-empty only on a reclaimed Mission")
    summary = record["summary"]
    if record["phase"] in (PHASE_RECLAIMING, PHASE_RECLAIMED):
        if summary is None:
            _bad(where, "a reclaimed Mission keeps its summary")
        _validate_summary(summary, where + ".summary")
    elif record["phase"] == PHASE_OBSERVED:
        # observed: a summary MAY already be captured while the record is intact (a workflow's own
        # store can prune the record at its cap before the deadline, so it is taken at first
        # eligible observation); nothing has been reduced yet.
        if summary is not None:
            _validate_summary(summary, where + ".summary")
    elif summary is not None:
        # deleting/deleted: the owner asked for the summary to go too, so a tombstone carries none
        # (it is not a hiding place for it).
        _bad(where, "a deleted Mission carries no summary")


def validate_document(document, path="<document>"):
    _closed(document, DOCUMENT_KEYS, path)
    if document["schema_version"] != SCHEMA_VERSION:
        _bad(path, "schema_version must be %d" % SCHEMA_VERSION)
    records = document["records"]
    if not isinstance(records, dict):
        _bad(path + ".records", "must be an object")
    if len(records) > MAX_RETENTION_RECORDS:
        raise RetentionStoreError(
            "%s holds %d records, more than %d" % (path, len(records), MAX_RETENTION_RECORDS),
            problem=PROBLEM_FULL)
    for key, record in records.items():
        _validate_record(key, record, "%s.records[%s]" % (path, key))
    return document


def _refuse_open_directory(directory):
    try:
        mode = os.stat(directory).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(mode):
        raise RetentionStoreError("%s is not a directory" % directory)
    if mode & _FORBIDDEN_MODE_BITS:
        raise RetentionStoreError(
            "state directory %s is accessible by group/other (mode %o); nothing is read, locked,"
            " or written. Fix with: chmod 700 %r" % (directory, stat.S_IMODE(mode), directory))


class RetentionStore(object):
    """Atomic load/save of the one retention document in ``directory``."""

    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, RETENTION_FILE_NAME)

    def lock(self):
        """The COORDINATOR lock: taken before any owned store's own lock, never instead of one."""
        _refuse_open_directory(self.directory)
        return exclusive_store_lock(self.directory, RETENTION_LOCK_FILE_NAME)

    def load(self):
        _refuse_open_directory(self.directory)
        if not os.path.exists(self.path):
            return default_document()
        mode = os.stat(self.path).st_mode
        if mode & _FORBIDDEN_MODE_BITS:
            raise RetentionStoreError(
                "%s is accessible by group/other (mode %o). Fix with: chmod 600 %r"
                % (self.path, stat.S_IMODE(mode), self.path))
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RetentionStoreError("%s could not be read as JSON (%s)" % (self.path, exc))
        if isinstance(document, dict) and document.get("schema_version") in LEGACY_SCHEMA_VERSIONS:
            document = _upgrade_v1(document)
        return validate_document(document, self.path)

    def save(self, document):
        _refuse_open_directory(self.directory)
        validate_document(document, self.path)
        atomic_write_json(self.directory, self.path, document, temp_prefix=".mission-retention-")


def _upgrade_v1(document):
    """A version-1 document, read as version 2 WITHOUT inventing a confident absence: version 1 had
    only "recorded" and "not recorded", and its "not recorded" also covered a source it did not read
    or could not read. Each such fact becomes ``not_read`` or ``unreadable`` where version 1's own
    source text says so (its CI producer's exact words), and ``not_known`` otherwise: absence was
    never established. Everything else is kept exactly; the upgraded document is still validated in
    full, and it is written as version 2 by the next save."""
    if document.get("schema_version") != 1 or not isinstance(document.get("records"), dict):
        return document
    for record in document["records"].values():
        summary = record.get("summary") if isinstance(record, dict) else None
        if not isinstance(summary, dict):
            continue
        for name in ("verdict", "ci", "comments"):
            fact = summary.get(name)
            if not isinstance(fact, dict) or fact.get("state") != SUMMARY_NOT_RECORDED:
                continue
            source = fact.get("source") if isinstance(fact.get("source"), str) else ""
            if source.startswith(LEGACY_V1_CI_NOT_CONFIGURED):
                fact["state"] = SUMMARY_NOT_READ
            elif source.startswith(LEGACY_V1_CI_UNREADABLE_PREFIX) and (
                    LEGACY_V1_CI_UNREADABLE_MARK in source):
                fact["state"] = SUMMARY_UNREADABLE
            else:
                fact["state"] = SUMMARY_NOT_KNOWN
    document["schema_version"] = SCHEMA_VERSION
    return document


def put_record(document, record):
    """Add or replace one record, refusing (never evicting) at the cap. The caller holds the lock."""
    records = document["records"]
    if record["key"] not in records and len(records) >= MAX_RETENTION_RECORDS:
        raise RetentionStoreError(
            "the retention store holds %d records, its hard bound; nothing is evicted, so no new"
            " Mission can be tracked until a person intervenes" % MAX_RETENTION_RECORDS,
            problem=PROBLEM_FULL)
    _validate_record(record["key"], record, "record")
    records[record["key"]] = record
